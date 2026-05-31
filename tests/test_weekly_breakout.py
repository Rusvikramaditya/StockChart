"""Weekly price-action breakout detector tests."""

from __future__ import annotations

import unittest

import numpy as np

from patterns import weekly_breakout


def _weekly_trendline_breakout(*, latest_close: float = 155.0, latest_volume_ratio: float = 1.8) -> dict:
    n = 70
    close = np.linspace(205.0, 128.0, n)
    close[-12:] = np.linspace(130.0, latest_close, 12)
    high = close + 4.0
    low = close - 5.0
    open_ = close - 2.0

    for idx, value in [(5, 210.0), (25, 190.0), (45, 170.0)]:
        high[idx] = value
        close[idx] = value - 8.0
        open_[idx] = close[idx] - 2.0
        low[idx] = close[idx] - 6.0
        high[idx - 1] = min(high[idx - 1], value - 14.0)
        high[idx + 1] = min(high[idx + 1], value - 14.0)

    volume = np.full(n, 1_000_000.0)
    volume[-1] = 1_000_000.0 * latest_volume_ratio
    return {"open": open_, "high": high, "low": low, "close": close, "volume": volume}


class WeeklyBreakoutTest(unittest.TestCase):
    def test_descending_trendline_breakout_detects_on_weekly_bars(self):
        weekly = _weekly_trendline_breakout()
        results = weekly_breakout.detect({}, weekly)

        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(result.pattern, "Weekly Breakout")
        self.assertEqual(result.status, "BREAKING OUT")
        self.assertEqual(result.timeframe, "weekly")
        self.assertEqual(result.extra["weekly_breakout_model"], "descending_trendline")
        self.assertGreaterEqual(result.extra["pattern_quality_score"], 7.0)
        self.assertGreater(result.extra["volume_ratio"], 1.4)

    def test_rejects_when_price_is_not_near_weekly_breakout_line(self):
        weekly = _weekly_trendline_breakout(latest_close=132.0)
        self.assertEqual(weekly_breakout.detect({}, weekly), [])


if __name__ == "__main__":
    unittest.main()
