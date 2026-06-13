"""Rounded reversal base detector tests."""

from __future__ import annotations

import unittest

import numpy as np

from patterns import rounded_reversal_base


def _rounded_reversal(*, latest_close: float = 108.0, volume_ratio: float = 1.5) -> tuple[dict, dict]:
    n = 150
    close = np.empty(n)
    close[:70] = np.linspace(150.0, 78.0, 70)
    close[70:120] = 78.0 + 0.01 * (np.arange(50) ** 2)
    close[120:] = np.linspace(close[119], latest_close, 30)
    high = close + 2.5
    low = close - 2.5
    open_ = close - 1.0

    for idx, value in [(18, 155.0), (50, 140.0), (88, 126.0), (118, 116.0)]:
        high[idx] = value
        close[idx] = value - 5.0
        open_[idx] = close[idx] - 1.0
        low[idx] = close[idx] - 4.0
        high[idx - 1] = min(high[idx - 1], value - 8.0)
        high[idx + 1] = min(high[idx + 1], value - 8.0)

    close[-1] = latest_close
    open_[-1] = latest_close - 2.0
    high[-1] = latest_close + 1.0
    low[-1] = latest_close - 3.0
    volume = np.full(n, 1_000_000.0)
    volume[-1] = 1_000_000.0 * volume_ratio

    weekly_close = np.linspace(80.0, latest_close, 40)
    weekly = {
        "open": weekly_close - 1.0,
        "high": weekly_close + 2.0,
        "low": weekly_close - 2.0,
        "close": weekly_close,
        "volume": np.full(40, 1_000_000.0),
    }
    daily = {"open": open_, "high": high, "low": low, "close": close, "volume": volume}
    return daily, weekly


class RoundedReversalBaseTest(unittest.TestCase):
    def test_detects_rounded_base_supply_line_reclaim(self):
        daily, weekly = _rounded_reversal()
        results = rounded_reversal_base.detect(daily, weekly)

        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(result.pattern, "Rounded Reversal Base")
        self.assertEqual(result.status, "BREAKING OUT")
        self.assertEqual(result.timeframe, "daily")
        self.assertEqual(result.extra["reversal_model"], "rounded_base_trendline_reclaim")
        self.assertTrue(result.extra["weekly_thesis_aligned"])
        self.assertGreaterEqual(result.extra["pattern_quality_score"], 7.0)
        self.assertGreater(result.pivot, result.stop_loss)

    def test_rejects_when_reclaim_is_too_extended(self):
        daily, weekly = _rounded_reversal(latest_close=118.0)

        self.assertEqual(rounded_reversal_base.detect(daily, weekly), [])


if __name__ == "__main__":
    unittest.main()
