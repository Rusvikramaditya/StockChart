"""Supertrend Bullish Flip detector audit tests.

Verifies staleness gate (>= max_flip_age_bars rejects), volume confirmation
component, stop-distance cap, and 0-10 pattern_quality_score breakdown.
"""

from __future__ import annotations

import unittest

import numpy as np

from patterns import supertrend


def _build_flip(
    *,
    bars: int = 90,
    pre_decline_pct: float = 8.0,
    flip_bars_ago: int = 0,
    post_flip_run_pct: float = 1.0,
    flip_bar_volume_ratio: float = 1.8,
    atr_pct_of_price: float = 2.0,
) -> dict:
    """Synthetic price series with a Supertrend bullish flip at a known bar.

    Structure:
      * Bars 0 .. n - flip_bars_ago - 2:   decline (bear regime, supertrend
        line above price).
      * Flip bar (n - 1 - flip_bars_ago):  strong up candle that closes
        above the prior supertrend upper line.
      * Subsequent bars: rally by post_flip_run_pct.

    The synth uses moderate ATR so the Supertrend math behaves predictably.
    """
    start_price = 100.0
    flip_idx_target = bars - 1 - flip_bars_ago
    if flip_idx_target < 30:
        bars = flip_idx_target + 30  # need enough prior data

    flip_idx = bars - 1 - flip_bars_ago

    # Pre-flip: linear decline so the supertrend line tracks above price.
    decline_end = start_price * (1.0 - pre_decline_pct / 100.0)
    pre_segment = np.linspace(start_price, decline_end, flip_idx)
    # Flip bar: up candle that just clears the prior supertrend upper
    # line. Jump magnitude is calibrated against ATR so the supertrend
    # lower line lands close enough to the flip close to keep
    # stop_distance under the 10% cap.
    flip_close = decline_end * (1.0 + 4.0 * atr_pct_of_price / 100.0)
    # Post-flip: linear rise from flip_close by post_flip_run_pct.
    post_count = bars - flip_idx - 1
    if post_count > 0:
        end_close = flip_close * (1.0 + post_flip_run_pct / 100.0)
        post_segment = np.linspace(flip_close, end_close, post_count)
    else:
        post_segment = np.array([], dtype=float)

    close = np.concatenate([pre_segment, [flip_close], post_segment])
    bars = len(close)  # may have changed via padding above
    # Build OHLC with ATR roughly atr_pct_of_price of price.
    bar_range = close * (atr_pct_of_price / 100.0)
    high = close + bar_range * 0.5
    low = close - bar_range * 0.5
    # On the flip bar specifically, make the day-range tall and the close
    # well above its low so the new-trend logic sees a decisive candle.
    high[flip_idx] = close[flip_idx] + bar_range[flip_idx] * 0.8
    low[flip_idx] = max(close[flip_idx] - bar_range[flip_idx] * 0.2, decline_end * 0.99)
    open_ = close.copy() - 0.5

    # Volume: baseline + multiplier on flip bar.
    volume = np.full(bars, 1_000_000.0, dtype=float)
    avg_vol = 1_000_000.0
    volume[flip_idx] = avg_vol * flip_bar_volume_ratio

    return {"open": open_, "high": high, "low": low, "close": close, "volume": volume}


class SupertrendStalenessTest(unittest.TestCase):

    def test_today_flip_detected_as_breaking_out(self):
        daily = _build_flip(flip_bars_ago=0)
        results = supertrend.detect(daily)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "BREAKING OUT")
        self.assertEqual(results[0].extra["flip_age_bars"], 0)

    def test_yesterday_flip_detected_as_pivot_ready(self):
        daily = _build_flip(flip_bars_ago=1)
        results = supertrend.detect(daily)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "PIVOT READY")
        self.assertEqual(results[0].extra["flip_age_bars"], 1)

    def test_stale_flip_rejected(self):
        """3-bar-old flip is stale by the new gate."""
        daily = _build_flip(flip_bars_ago=3, post_flip_run_pct=4.0)
        results = supertrend.detect(daily)
        self.assertEqual(results, [], "Stale flip must reject")


class SupertrendQualityScoreTest(unittest.TestCase):

    def test_quality_breakdown_components_present(self):
        daily = _build_flip()
        results = supertrend.detect(daily)
        self.assertEqual(len(results), 1)
        components = results[0].extra["pattern_quality_breakdown"]
        expected = {
            "flip_freshness", "atr_regime", "stop_tightness",
            "entry_extension", "volume_confirmation",
        }
        self.assertEqual(set(components.keys()), expected)
        self.assertAlmostEqual(
            sum(components.values()),
            results[0].extra["pattern_quality_score"],
            places=1,
        )

    def test_textbook_grades_higher_than_borderline(self):
        textbook = _build_flip(
            flip_bars_ago=0,
            post_flip_run_pct=0.3,  # small extension = good entry
            flip_bar_volume_ratio=2.5,
            atr_pct_of_price=2.5,
        )
        borderline = _build_flip(
            flip_bars_ago=1,
            post_flip_run_pct=2.5,  # already extended
            flip_bar_volume_ratio=1.1,
            atr_pct_of_price=2.0,
        )
        t = supertrend.detect(textbook)
        b = supertrend.detect(borderline)
        self.assertEqual(len(t), 1)
        self.assertEqual(len(b), 1)
        self.assertGreater(
            t[0].extra["pattern_quality_score"],
            b[0].extra["pattern_quality_score"],
        )

    def test_quality_score_capped_at_ten(self):
        daily = _build_flip(
            flip_bars_ago=0, post_flip_run_pct=0.2,
            flip_bar_volume_ratio=3.0, atr_pct_of_price=2.5,
        )
        results = supertrend.detect(daily)
        self.assertEqual(len(results), 1)
        self.assertLessEqual(results[0].extra["pattern_quality_score"], 10.0)


if __name__ == "__main__":
    unittest.main()
