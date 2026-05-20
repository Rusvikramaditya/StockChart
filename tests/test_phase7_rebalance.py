"""Phase 7-3 rebalance refresh tests."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from engine import storage


BASE_DIR = Path(__file__).resolve().parent.parent


def _load_rebalance_module():
    path = BASE_DIR / "setup" / "07_rebalance_check.py"
    spec = importlib.util.spec_from_file_location("phase7_rebalance_check", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RebalancePhase7Test(unittest.TestCase):
    def test_rebalance_refreshes_broad_nifty_sector_and_liquidity_profiles(self):
        module = _load_rebalance_module()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            master_path = tmp_path / "master.csv"
            all_nse_path = tmp_path / "all_nse_equity.csv"
            nifty_csv_path = tmp_path / "nifty500.csv"
            nifty_dhan_path = tmp_path / "nifty500_dhan.csv"
            small_mid_path = tmp_path / "small_mid_liquid.csv"
            sector_map_path = tmp_path / "sector_map.json"
            db_path = tmp_path / "test.db"

            _write_master(master_path)
            _write_old_nifty(nifty_dhan_path)
            conn = storage.connect(db_path)
            storage.ensure_schema(conn)
            try:
                storage.upsert_daily_rows(conn, "SMALL", "300", _daily_rows())
            finally:
                conn.close()

            result = module.check_rebalance(
                apply_history=False,
                force_master_refresh=False,
                rebuild_liquidity=True,
                refresh_watchlist=False,
                fresh_nifty=pd.DataFrame(
                    [
                        {"Symbol": "KEEP", "Company Name": "Keep Ltd", "Industry": "Financial Services"},
                        {"Symbol": "NEW", "Company Name": "New Ltd", "Industry": "Information Technology"},
                    ]
                ),
                master_path=master_path,
                db_path=db_path,
                all_nse_path=all_nse_path,
                nifty_csv_path=nifty_csv_path,
                nifty_dhan_path=nifty_dhan_path,
                small_mid_path=small_mid_path,
                sector_map_path=sector_map_path,
            )

            broad = pd.read_csv(all_nse_path, dtype=str).fillna("")
            nifty = pd.read_csv(nifty_dhan_path, dtype=str).fillna("")
            small_mid = pd.read_csv(small_mid_path, dtype=str).fillna("")
            sector_payload = json.loads(sector_map_path.read_text(encoding="utf-8"))

        self.assertEqual(result["added"], ["NEW"])
        self.assertEqual(result["removed"], ["OLD"])
        self.assertEqual(result["broad_rows"], 3)
        self.assertEqual(result["small_mid_liquid_rows"], 1)
        self.assertEqual(set(broad["symbol"]), {"KEEP", "NEW", "SMALL"})
        self.assertEqual(
            nifty[["symbol", "security_id", "status"]].to_dict("records"),
            [
                {"symbol": "KEEP", "security_id": "100", "status": "ACTIVE"},
                {"symbol": "NEW", "security_id": "200", "status": "ACTIVE"},
                {"symbol": "OLD", "security_id": "999", "status": "INACTIVE"},
            ],
        )
        self.assertEqual(small_mid["symbol"].tolist(), ["SMALL"])
        self.assertIn("KEEP", sector_payload["symbols"])
        self.assertIn("NEW", sector_payload["symbols"])
        self.assertNotIn("OLD", sector_payload["symbols"])


def _write_master(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_SMST_SECURITY_ID,SEM_INSTRUMENT_NAME,SEM_TRADING_SYMBOL,SEM_LOT_UNITS,SEM_CUSTOM_SYMBOL,SEM_EXCH_INSTRUMENT_TYPE,SEM_SERIES,SM_SYMBOL_NAME",
                "NSE,E,100,EQUITY,KEEP,1,Keep Broad,ES,EQ,KEEP BROAD",
                "NSE,E,200,EQUITY,NEW,1,New Broad,ES,EQ,NEW BROAD",
                "NSE,E,300,EQUITY,SMALL,1,Small Broad,ES,EQ,SMALL BROAD",
            ]
        ),
        encoding="utf-8",
    )


def _write_old_nifty(path: Path) -> None:
    pd.DataFrame(
        [
            {
                "symbol": "KEEP",
                "company_name": "Keep Ltd",
                "industry": "Financial Services",
                "security_id": "100",
                "exchange_segment": "NSE_EQ",
                "instrument": "EQUITY",
                "instrument_type": "ES",
                "series": "EQ",
                "lot_size": "1",
                "listing_date": "",
                "status": "ACTIVE",
            },
            {
                "symbol": "OLD",
                "company_name": "Old Ltd",
                "industry": "Energy",
                "security_id": "999",
                "exchange_segment": "NSE_EQ",
                "instrument": "EQUITY",
                "instrument_type": "ES",
                "series": "EQ",
                "lot_size": "1",
                "listing_date": "",
                "status": "ACTIVE",
            },
        ]
    ).to_csv(path, index=False)


def _daily_rows(rows: int = 130) -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=rows, freq="D")
    return pd.DataFrame(
        {
            "date": dates,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 200_000,
        }
    )


if __name__ == "__main__":
    unittest.main()
