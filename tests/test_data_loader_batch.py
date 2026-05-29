"""Tests for DataLoader.get_recent_close_stats batch reader."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from engine import dhan_client, storage
from engine.data_loader import DataLoader, _as_float


def _close_frame(prices: list[float]) -> pd.DataFrame:
    n = len(prices)
    dates = pd.date_range("2024-01-01", periods=n, freq="B").strftime("%Y-%m-%d")
    return pd.DataFrame(
        {
            "date": list(dates),
            "open": prices,
            "high": [p * 1.01 for p in prices],
            "low": [p * 0.99 for p in prices],
            "close": prices,
            "volume": [1_000_000.0] * n,
        }
    )


class AsFloatTest(unittest.TestCase):
    def test_none_returns_none(self):
        self.assertIsNone(_as_float(None))

    def test_numeric_string_returns_float(self):
        self.assertEqual(_as_float("3.14"), 3.14)

    def test_non_numeric_returns_none(self):
        self.assertIsNone(_as_float("not-a-number"))

    def test_nan_returns_none(self):
        self.assertIsNone(_as_float(float("nan")))

    def test_int_returns_float(self):
        self.assertEqual(_as_float(5), 5.0)


class GetRecentCloseStatsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        conn = storage.connect(self.db_path)
        storage.ensure_schema(conn)
        storage.upsert_daily_rows(conn, "ALPHA", "1", _close_frame(list(range(100, 300))))
        storage.upsert_daily_rows(conn, "BETA", "2", _close_frame([100.0] * 200))
        storage.upsert_daily_rows(conn, "TINY", "3", _close_frame([50.0, 51.0]))
        conn.close()
        self.loader = DataLoader(self.db_path)

    def tearDown(self) -> None:
        self.loader.close()
        self.tmp.cleanup()

    def test_returns_latest_prior_and_mas(self):
        stats = self.loader.get_recent_close_stats(["ALPHA"])
        rec = stats["ALPHA"]
        self.assertEqual(rec["latest"], 299.0)
        self.assertEqual(rec["prior"], 298.0)
        self.assertEqual(rec["bars"], 200)
        self.assertIsNotNone(rec["ma50"])
        self.assertIsNotNone(rec["ma200"])
        # ma200 over last 200 bars (100..299 in this synth) averages 199.5.
        self.assertAlmostEqual(rec["ma200"], 199.5, places=3)

    def test_flat_series_ma_equals_price(self):
        stats = self.loader.get_recent_close_stats(["BETA"])
        rec = stats["BETA"]
        self.assertEqual(rec["latest"], 100.0)
        self.assertEqual(rec["prior"], 100.0)
        self.assertAlmostEqual(rec["ma50"], 100.0)
        self.assertAlmostEqual(rec["ma200"], 100.0)

    def test_short_history_returns_partial_record(self):
        stats = self.loader.get_recent_close_stats(["TINY"])
        rec = stats["TINY"]
        self.assertEqual(rec["latest"], 51.0)
        self.assertEqual(rec["prior"], 50.0)
        self.assertEqual(rec["bars"], 2)

    def test_unknown_symbol_omitted(self):
        stats = self.loader.get_recent_close_stats(["ALPHA", "DOES_NOT_EXIST"])
        self.assertIn("ALPHA", stats)
        self.assertNotIn("DOES_NOT_EXIST", stats)

    def test_empty_symbols_returns_empty(self):
        self.assertEqual(self.loader.get_recent_close_stats([]), {})

    def test_custom_ma_periods(self):
        stats = self.loader.get_recent_close_stats(["ALPHA"], ma_periods=(20,))
        rec = stats["ALPHA"]
        self.assertIn("ma20", rec)
        self.assertNotIn("ma50", rec)
        self.assertNotIn("ma200", rec)

    def test_empty_ma_periods_only_returns_latest_prior(self):
        stats = self.loader.get_recent_close_stats(["ALPHA"], ma_periods=())
        rec = stats["ALPHA"]
        self.assertIn("latest", rec)
        self.assertIn("prior", rec)
        self.assertNotIn("ma50", rec)
        self.assertNotIn("ma200", rec)

    def test_case_insensitive_symbols(self):
        stats = self.loader.get_recent_close_stats(["alpha"])
        self.assertIn("ALPHA", stats)


class FetchTodaysCandlesTest(unittest.TestCase):
    def test_raises_specific_rate_limit_error_on_dhan_429(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            rate_path = Path(tmp) / "dhan_rate_limit.json"
            loader = DataLoader(db_path)
            profile = pd.DataFrame([{"symbol": "AAA", "security_id": "1"}])

            class Response:
                status_code = 429
                text = '{"status":"failed","data":{"1":"Too many requests"}}'

            try:
                with (
                    patch.object(dhan_client.settings, "DATA_DIR", Path(tmp)),
                    patch.object(dhan_client.settings, "DHAN_RATE_LIMIT_CACHE_PATH", rate_path),
                    patch("engine.data_loader.dhan_client.dhan_request", return_value=Response()),
                ):
                    with self.assertRaises(dhan_client.DhanRateLimitError):
                        loader.fetch_todays_candles(profile)
                    self.assertTrue(rate_path.exists())
            finally:
                loader.close()

    def test_active_rate_limit_blocks_before_dhan_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            rate_path = Path(tmp) / "dhan_rate_limit.json"
            loader = DataLoader(db_path)
            profile = pd.DataFrame([{"symbol": "AAA", "security_id": "1"}])

            try:
                with (
                    patch.object(dhan_client.settings, "DATA_DIR", Path(tmp)),
                    patch.object(dhan_client.settings, "DHAN_RATE_LIMIT_CACHE_PATH", rate_path),
                    patch.object(dhan_client.settings, "DHAN_RATE_LIMIT_COOLDOWN_SECONDS", 900),
                ):
                    dhan_client.record_rate_limit("Too many requests")
                    with patch("engine.data_loader.dhan_client.dhan_request") as request:
                        with self.assertRaises(dhan_client.DhanRateLimitError):
                            loader.fetch_todays_candles(profile)
                    request.assert_not_called()
            finally:
                loader.close()


if __name__ == "__main__":
    unittest.main()
