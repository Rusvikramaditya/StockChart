"""King Candle confirmation tests."""

from __future__ import annotations

import unittest

import numpy as np

from filters import king_candle


def _daily_with_king(*, follow_close: float = 106.2) -> dict:
    n = 70
    close = np.full(n, 100.0)
    open_ = close - 0.1
    high = close + 0.5
    low = close - 0.5
    volume = np.full(n, 100_000.0)

    king_idx = n - 2
    open_[king_idx] = 101.0
    low[king_idx] = 100.5
    high[king_idx] = 106.0
    close[king_idx] = 105.5
    volume[king_idx] = 220_000.0

    open_[-1] = 105.6
    low[-1] = min(105.0, follow_close - 0.5)
    high[-1] = max(106.5, follow_close + 0.2)
    close[-1] = follow_close
    volume[-1] = 180_000.0

    return {
        "date": np.arange("2026-02-01", "2026-04-12", dtype="datetime64[D]"),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


class KingCandleTest(unittest.TestCase):
    def test_bullish_king_candle_with_follow_through_is_confirmed(self):
        result = king_candle.evaluate(_daily_with_king())

        self.assertTrue(result["passed"])
        self.assertEqual(result["status"], "CONFIRMED")
        self.assertTrue(result["details"]["observed"])
        self.assertEqual(result["details"]["king_high"], 106.0)
        self.assertEqual(result["details"]["king_midpoint"], 103.25)
        self.assertGreater(result["details"]["volume_ratio"], 2.0)

    def test_failed_midpoint_is_not_confirmation(self):
        result = king_candle.evaluate(_daily_with_king(follow_close=102.5))

        self.assertFalse(result["passed"])
        self.assertEqual(result["status"], "FAILED_MIDPOINT")
        self.assertTrue(result["details"]["observed"])


if __name__ == "__main__":
    unittest.main()
