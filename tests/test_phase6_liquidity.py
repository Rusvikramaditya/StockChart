"""Phase 6-0d tests for OHLCV-derived liquidity profiles."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from engine import storage, universe
from filters import liquidity


class LiquidityPhase6Test(unittest.TestCase):
    def _write_broad_universe(self, path: Path) -> None:
        rows = []
        for symbol, security_id in [
            ("NIFTYLIQ", "100"),
            ("GOODLIQ", "101"),
            ("LOWVALUE", "102"),
            ("STALE", "103"),
            ("LOWPRICE", "104"),
            ("NOHIST", "105"),
        ]:
            rows.append(
                {
                    "symbol": symbol,
                    "company_name": symbol.title(),
                    "security_id": security_id,
                    "exchange_segment": "NSE_EQ",
                    "instrument": "EQUITY",
                    "instrument_type": "ES",
                    "series": "EQ",
                    "lot_size": "1",
                    "listing_date": "",
                    "status": "ACTIVE",
                }
            )
        pd.DataFrame(rows, columns=universe.OUTPUT_COLUMNS).to_csv(path, index=False)

    def _write_nifty500(self, path: Path) -> None:
        pd.DataFrame(
            [
                {
                    "symbol": "NIFTYLIQ",
                    "company_name": "Nifty Liquid",
                    "security_id": "100",
                    "exchange_segment": "NSE_EQ",
                    "instrument": "EQUITY",
                    "instrument_type": "ES",
                    "series": "EQ",
                    "lot_size": "1",
                    "listing_date": "",
                    "status": "ACTIVE",
                }
            ],
            columns=universe.OUTPUT_COLUMNS,
        ).to_csv(path, index=False)

    def _daily_rows(
        self,
        *,
        start: str,
        periods: int,
        close: float,
        volume: int,
    ) -> pd.DataFrame:
        dates = pd.date_range(start, periods=periods, freq="D")
        return pd.DataFrame(
            {
                "date": dates,
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": volume,
            }
        )

    def test_small_mid_liquid_profile_uses_ohlcv_and_excludes_nifty500(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            broad_path = tmp_path / "all_nse_equity.csv"
            nifty_path = tmp_path / "nifty500_dhan.csv"
            output_path = tmp_path / "small_mid_liquid.csv"
            self._write_broad_universe(broad_path)
            self._write_nifty500(nifty_path)
            conn = storage.connect(tmp_path / "test.db")
            storage.ensure_schema(conn)
            try:
                storage.upsert_daily_rows(
                    conn,
                    "NIFTYLIQ",
                    "100",
                    self._daily_rows(start="2026-01-01", periods=130, close=100.0, volume=200_000),
                )
                storage.upsert_daily_rows(
                    conn,
                    "GOODLIQ",
                    "101",
                    self._daily_rows(start="2026-01-01", periods=130, close=100.0, volume=200_000),
                )
                storage.upsert_daily_rows(
                    conn,
                    "LOWVALUE",
                    "102",
                    self._daily_rows(start="2026-01-01", periods=130, close=100.0, volume=1_000),
                )

                result = liquidity.build_small_mid_liquid_profile(
                    conn,
                    output_path=output_path,
                    broad_path=broad_path,
                    nifty500_path=nifty_path,
                )
            finally:
                conn.close()

            self.assertEqual(result.rows, 1)
            written = pd.read_csv(output_path, dtype=str).fillna("")
            self.assertEqual(written["symbol"].tolist(), ["GOODLIQ"])
            self.assertEqual(written["security_id"].tolist(), ["101"])
            self.assertIn("avg_traded_value_50d", written.columns)
            self.assertEqual(written["liquidity_pass"].tolist(), ["True"])
            self.assertEqual(written["risk_tier"].tolist(), ["HIGH"])

    def test_liquidity_profile_reports_failure_reasons_without_faking_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            broad_path = tmp_path / "all_nse_equity.csv"
            self._write_broad_universe(broad_path)
            broad = universe.load_all_nse_equity(path=broad_path)
            conn = storage.connect(tmp_path / "test.db")
            storage.ensure_schema(conn)
            try:
                storage.upsert_daily_rows(
                    conn,
                    "GOODLIQ",
                    "101",
                    self._daily_rows(start="2026-01-01", periods=130, close=100.0, volume=200_000),
                )
                storage.upsert_daily_rows(
                    conn,
                    "STALE",
                    "103",
                    self._daily_rows(start="2025-01-01", periods=130, close=100.0, volume=200_000),
                )
                storage.upsert_daily_rows(
                    conn,
                    "LOWPRICE",
                    "104",
                    self._daily_rows(start="2026-01-01", periods=130, close=5.0, volume=300_000),
                )

                frame = liquidity.build_liquidity_profile_frame(conn, broad)
            finally:
                conn.close()

            indexed = frame.set_index("symbol")
            self.assertTrue(bool(indexed.at["GOODLIQ", "liquidity_pass"]))
            self.assertIn("stale_data", indexed.at["STALE", "liquidity_reason"])
            self.assertIn("price_below_min", indexed.at["LOWPRICE", "liquidity_reason"])
            self.assertIn("no_daily_data", indexed.at["NOHIST", "liquidity_reason"])
            self.assertIn("insufficient_history", indexed.at["NOHIST", "liquidity_reason"])


if __name__ == "__main__":
    unittest.main()
