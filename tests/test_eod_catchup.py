"""Tests for daily EOD bhavcopy/yfinance catch-up."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from engine import storage
from engine.eod_catchup import (
    BhavcopyResult,
    CatchupSummary,
    catch_up_daily_eod,
    missing_trading_days,
    normalise_nse_bhavcopy,
)


class EodCatchupTest(unittest.TestCase):
    def test_missing_trading_days_are_detected_from_latest_local_date(self):
        days = missing_trading_days(
            {"AAA": "2026-05-26", "BBB": "2026-05-27"},
            "2026-05-29",
        )

        self.assertEqual(days, ["2026-05-27", "2026-05-28", "2026-05-29"])

    def test_bhavcopy_success_writes_normalized_ohlcv_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = storage.connect(Path(tmp) / "test.db")
            storage.ensure_schema(conn)
            storage.upsert_daily_rows(conn, "AAA", "1", _daily_frame("2026-05-27", 100.0))
            try:
                summary = catch_up_daily_eod(
                    conn,
                    _profile("AAA"),
                    to_date="2026-05-28",
                    bhavcopy_fetcher=lambda day: BhavcopyResult(
                        day,
                        "bhavcopy",
                        _bhavcopy_frame("AAA", day, 110.0),
                    ),
                    yfinance_fetcher=lambda *_args: {},
                )
                written = storage.query_frame(
                    conn,
                    "SELECT date, open, high, low, close, volume FROM ohlcv_daily WHERE symbol = 'AAA' ORDER BY date",
                )
            finally:
                conn.close()

        self.assertEqual(summary.rows_written, 1)
        self.assertEqual(summary.source_counts["bhavcopy"], 1)
        self.assertEqual(written["date"].tolist(), ["2026-05-27", "2026-05-28"])
        self.assertEqual(float(written.iloc[-1]["close"]), 110.0)
        self.assertEqual(int(written.iloc[-1]["volume"]), 1000)

    def test_yfinance_fallback_writes_rows_when_bhavcopy_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = storage.connect(Path(tmp) / "test.db")
            storage.ensure_schema(conn)
            storage.upsert_daily_rows(conn, "AAA", "1", _daily_frame("2026-05-27", 100.0))
            events: list[str] = []
            try:
                summary = catch_up_daily_eod(
                    conn,
                    _profile("AAA"),
                    to_date="2026-05-28",
                    bhavcopy_fetcher=lambda day: BhavcopyResult(day, "bhavcopy", pd.DataFrame(), status="unavailable"),
                    yfinance_fetcher=lambda symbols, _from_date, _to_date: {
                        symbols[0]: _daily_frame("2026-05-28", 120.0)
                    },
                    logger=events.append,
                )
                row = conn.execute(
                    "SELECT close, volume FROM ohlcv_daily WHERE symbol = 'AAA' AND date = '2026-05-28'"
                ).fetchone()
            finally:
                conn.close()

        self.assertEqual(summary.rows_written, 1)
        self.assertEqual(summary.source_counts["yfinance"], 1)
        self.assertEqual(row, (120.0, 1000))
        self.assertTrue(any("EOD target date: 2026-05-28" in event for event in events))
        self.assertTrue(any("bhavcopy status=unavailable" in event for event in events))
        self.assertTrue(any("yfinance rows_written=1" in event for event in events))

    def test_empty_external_sources_keep_local_stale_and_record_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = storage.connect(Path(tmp) / "test.db")
            storage.ensure_schema(conn)
            storage.upsert_daily_rows(conn, "AAA", "1", _daily_frame("2026-05-27", 100.0))
            try:
                summary = catch_up_daily_eod(
                    conn,
                    _profile("AAA"),
                    to_date="2026-05-28",
                    bhavcopy_fetcher=lambda day: BhavcopyResult(day, "bhavcopy", _empty_bhavcopy()),
                    yfinance_fetcher=lambda *_args: {"AAA": pd.DataFrame()},
                )
            finally:
                conn.close()

        self.assertEqual(summary.rows_written, 0)
        self.assertTrue(summary.stale)
        self.assertEqual(summary.data_as_of, "2026-05-27")
        self.assertEqual(summary.source_counts["local_stale"], 1)
        self.assertTrue(any("Current-month data is incomplete" in warning for warning in summary.warnings))

    def test_summary_dict_keeps_full_stale_symbol_list(self):
        summary = CatchupSummary(
            target_date="2026-05-28",
            stale_symbols=[f"SYM{idx:02d}" for idx in range(25)],
        )

        payload = summary.to_dict()

        self.assertEqual(len(payload["stale_symbols"]), 25)
        self.assertEqual(len(payload["stale_symbols_preview"]), 20)

    def test_nse_bhavcopy_normalizer_accepts_official_columns(self):
        raw = pd.DataFrame(
            {
                "SYMBOL": ["AAA"],
                "SERIES": ["EQ"],
                "DATE1": ["28-May-2026"],
                "OPEN_PRICE": [10],
                "HIGH_PRICE": [12],
                "LOW_PRICE": [9],
                "CLOSE_PRICE": [11],
                "TTL_TRD_QNTY": [1234],
            }
        )

        frame = normalise_nse_bhavcopy(raw, "2026-05-28")

        self.assertEqual(frame.to_dict("records")[0]["symbol"], "AAA")
        self.assertEqual(frame.to_dict("records")[0]["date"], "2026-05-28")
        self.assertEqual(frame.to_dict("records")[0]["volume"], 1234)


def _profile(symbol: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": symbol,
                "security_id": "1",
                "exchange_segment": "NSE_EQ",
                "instrument": "EQUITY",
            }
        ]
    )


def _daily_frame(day: str, close: float) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": [day],
            "open": [close - 1],
            "high": [close + 1],
            "low": [close - 2],
            "close": [close],
            "volume": [1000],
        }
    )


def _bhavcopy_frame(symbol: str, day: str, close: float) -> pd.DataFrame:
    frame = _daily_frame(day, close)
    frame.insert(0, "symbol", symbol)
    return frame


def _empty_bhavcopy() -> pd.DataFrame:
    return pd.DataFrame(columns=["symbol", "date", "open", "high", "low", "close", "volume"])


if __name__ == "__main__":
    unittest.main()
