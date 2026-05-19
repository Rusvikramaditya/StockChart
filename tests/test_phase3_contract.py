"""Phase 3 contract tests for filters, scoring, and explanations."""

from __future__ import annotations

import unittest

import numpy as np

from config import settings
from engine.data_loader import DataLoader
from engine.explainer import attach_explanation
from engine.scorer import score_pattern
from filters import market_regime, rsi, sector_rs, stage2, volume
from patterns import ALL_DETECTORS
from patterns.base import PatternResult


class Phase3ContractTest(unittest.TestCase):
    def test_synthetic_filters_are_testable(self):
        close = np.linspace(100.0, 210.0, 260)
        daily = {
            "open": close - 1.0,
            "high": close + 2.0,
            "low": close - 2.0,
            "close": close,
            "volume": np.array([100000.0] * 259 + [180000.0]),
        }
        self.assertTrue(stage2.evaluate(daily)["passed"])
        self.assertTrue(volume.evaluate(daily)["passed"])
        self.assertIsNotNone(rsi.evaluate(daily)["value"])

        pattern = PatternResult(
            pattern="Ascending Triangle",
            status="BREAKING OUT",
            pivot=200.0,
            target=230.0,
            stop_loss=190.0,
            confidence=100.0,
            explanation="Synthetic breakout.",
            timeframe="daily",
            bars_in_pattern=60,
        )
        weekly_close = np.linspace(100.0, 150.0, 40)
        weekly = {
            "open": weekly_close - 1.0,
            "high": weekly_close + 2.0,
            "low": weekly_close - 2.0,
            "close": weekly_close,
            "volume": np.array([100000.0] * 40),
        }
        sector_cache = {
            "lookback_days": 63,
            "nifty_return_pct": 1.0,
            "sectors": {"NIFTY 50": {"return_pct": 1.0, "vs_nifty_pct": 0.0}},
            "symbol_to_sector": {},
        }
        scored = score_pattern("TEST", pattern, daily, weekly, {"score": 0, "verdict": "BEAR"}, sector_cache)
        self.assertEqual(scored["score"], 0)
        self.assertEqual(scored["tier"], "SKIP")
        self.assertFalse(scored["tradable"])
        self.assertEqual(scored["skip_reason"], "BEAR_REGIME")

    def test_real_db_detect_filter_score_explain_10_stocks(self):
        if not settings.DB_PATH.exists():
            self.skipTest("local Phase 1 DB is not present")

        loader = DataLoader()
        try:
            symbols = []
            for symbol in loader.get_all_active_symbols():
                if len(loader.get_stock_daily(symbol)) >= 252 and len(loader.get_stock_weekly(symbol)) >= 104:
                    symbols.append(symbol)
                if len(symbols) == 10:
                    break
            if len(symbols) < 10:
                self.skipTest("local DB does not contain 10 fully populated symbols")

            regime = market_regime.compute_market_regime(loader, symbols)
            sector_cache = sector_rs.compute_sector_rs_cache(loader, symbols)
            scored = []
            for symbol in symbols:
                daily = loader.get_stock_daily_arrays(symbol)
                weekly = loader.get_stock_weekly_arrays(symbol)
                for detector in ALL_DETECTORS:
                    results = detector(daily, weekly)
                    self.assertIsInstance(results, list)
                    for pattern in results:
                        self.assertIsInstance(pattern, PatternResult)
                        item = score_pattern(symbol, pattern, daily, weekly, regime, sector_cache)
                        enriched = attach_explanation(item)
                        for section in range(6):
                            self.assertIn(f"SECTION {section}", enriched["explanation"])
                        scored.append(enriched)

            self.assertGreater(len(scored), 0)
        finally:
            loader.close()


if __name__ == "__main__":
    unittest.main()
