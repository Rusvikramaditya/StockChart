"""Multi-Year Breakout detector audit tests.

Verifies the PIVOT READY-unreachable bug is fixed (volume surge required
only on the breakout candle), strict touch dispersion + spread guards,
and the new 0-10 pattern_quality_score breakdown.
"""

from __future__ import annotations

import unittest

import numpy as np

from patterns import multiyear_breakout


def _build_multiyear(
    *,
    weeks: int = 156,
    resistance: float = 1000.0,
    touch_count: int = 4,
    touch_dispersion_pct: float = 0.3,
    touch_spread_frac: float = 0.8,
    base_low: float = 600.0,
    latest_close_pct_of_resistance: float = 0.985,  # PIVOT READY
    breakout_volume_ratio: float = 1.6,
    stop_low: float | None = None,
) -> dict:
    """Synthetic 3-year weekly bull-base hitting resistance.

    Generates ``weeks`` of weekly OHLCV. Touches are sprinkled across the
    spread fraction of the window, evenly. Base low is the global trough.
    Latest week is the breakout/PIVOT-READY candle.
    """
    open_ = np.full(weeks, resistance * 0.7, dtype=float)
    high = np.full(weeks, resistance * 0.7, dtype=float)
    low = np.full(weeks, resistance * 0.7, dtype=float)
    close = np.full(weeks, resistance * 0.7, dtype=float)
    volume = np.full(weeks, 1_000_000.0, dtype=float)

    # Baseline rises from base_low area up toward resistance over the
    # window. Final 12 weeks sit close to resistance so the stop (last
    # 12-week low) stays inside max_stop_distance_pct of the pivot.
    baseline = np.linspace(base_low + 50.0, resistance * 0.97, weeks)
    open_[:] = baseline
    high[:] = baseline + 5.0
    low[:] = baseline - 5.0
    close[:] = baseline
    # One bar in the early window touches base_low to set the multi-year low.
    low[3] = base_low

    # Place touches across the spread fraction.
    first_touch_idx = 5
    last_touch_idx = min(weeks - 6, int(first_touch_idx + (weeks - 12) * touch_spread_frac))
    touch_positions = np.linspace(first_touch_idx, last_touch_idx, touch_count).astype(int)
    # Dispersion: spread touches in a tight band [resistance * (1 - disp/100), resistance].
    disp_low = resistance * (1.0 - touch_dispersion_pct / 100.0)
    touch_levels = np.linspace(disp_low, resistance, touch_count)
    for idx, lvl in zip(touch_positions, touch_levels):
        high[idx] = float(lvl)
        close[idx] = float(lvl) - 1.0
        open_[idx] = float(lvl) - 2.0
        low[idx] = float(lvl) - 4.0

    # Final bar: breakout or pivot-ready
    final_close = resistance * latest_close_pct_of_resistance
    close[-1] = final_close
    open_[-1] = final_close - 1.0
    high[-1] = final_close + 1.0 if final_close < resistance else final_close + 1.0
    low[-1] = final_close - 5.0

    # Volume: average baseline then breakout-candle surge.
    avg_baseline_vol = 1_000_000.0
    volume[:] = avg_baseline_vol
    volume[-1] = avg_baseline_vol * breakout_volume_ratio

    if stop_low is not None:
        low[-5] = stop_low  # within last 12 weeks for stop computation

    return {"open": open_, "high": high, "low": low, "close": close, "volume": volume}


class MultiYearDetectionTest(unittest.TestCase):

    def test_pivot_ready_detected_without_volume_surge(self):
        """Bug regression: PIVOT READY must surface even though the current
        bar has no breakout-volume surge."""
        daily = _build_multiyear(
            latest_close_pct_of_resistance=0.985,  # below resistance
            breakout_volume_ratio=0.9,  # no surge
        )
        results = multiyear_breakout.detect(daily={"open": [], "high": [], "low": [], "close": [], "volume": []}, weekly=daily)
        self.assertEqual(len(results), 1, "PIVOT READY must detect without volume surge")
        self.assertEqual(results[0].status, "PIVOT READY")

    def test_breakout_detected_with_volume_surge(self):
        daily = _build_multiyear(
            latest_close_pct_of_resistance=1.02,  # above resistance
            breakout_volume_ratio=1.8,
        )
        results = multiyear_breakout.detect(daily={"open": [], "high": [], "low": [], "close": [], "volume": []}, weekly=daily)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "BREAKING OUT")

    def test_breakout_without_volume_surge_rejected(self):
        """Breakout candle without volume surge is a low-conviction
        candidate; detector rejects it to keep the BREAKING OUT label
        clean."""
        daily = _build_multiyear(
            latest_close_pct_of_resistance=1.02,
            breakout_volume_ratio=1.0,  # no surge
        )
        results = multiyear_breakout.detect(daily={"open": [], "high": [], "low": [], "close": [], "volume": []}, weekly=daily)
        self.assertEqual(results, [])

    def test_clustered_touches_rejected(self):
        """Touches clustered in last quarter of the window must reject."""
        daily = _build_multiyear(touch_spread_frac=0.2)
        results = multiyear_breakout.detect(daily={"open": [], "high": [], "low": [], "close": [], "volume": []}, weekly=daily)
        self.assertEqual(results, [])

    def test_wide_dispersion_rejected(self):
        """Touches spanning >1% range must reject (was 3% before)."""
        daily = _build_multiyear(touch_dispersion_pct=1.5)
        results = multiyear_breakout.detect(daily={"open": [], "high": [], "low": [], "close": [], "volume": []}, weekly=daily)
        self.assertEqual(results, [])

    def test_too_few_touches_rejected(self):
        daily = _build_multiyear(touch_count=2)
        results = multiyear_breakout.detect(daily={"open": [], "high": [], "low": [], "close": [], "volume": []}, weekly=daily)
        self.assertEqual(results, [])


class MultiYearQualityScoreTest(unittest.TestCase):

    def test_textbook_grades_higher_than_borderline(self):
        textbook = _build_multiyear(
            touch_count=5, touch_dispersion_pct=0.2, touch_spread_frac=0.9,
            latest_close_pct_of_resistance=1.01, breakout_volume_ratio=2.2,
        )
        borderline = _build_multiyear(
            touch_count=3, touch_dispersion_pct=0.9, touch_spread_frac=0.55,
            latest_close_pct_of_resistance=0.99, breakout_volume_ratio=1.0,
        )
        t = multiyear_breakout.detect({}, weekly=textbook)
        b = multiyear_breakout.detect({}, weekly=borderline)
        self.assertEqual(len(t), 1)
        self.assertEqual(len(b), 1)
        self.assertGreater(
            t[0].extra["pattern_quality_score"],
            b[0].extra["pattern_quality_score"],
        )

    def test_quality_breakdown_components_present(self):
        daily = _build_multiyear()
        results = multiyear_breakout.detect({}, weekly=daily)
        self.assertEqual(len(results), 1)
        components = results[0].extra["pattern_quality_breakdown"]
        expected = {
            "touch_count", "touch_flatness", "touch_spread", "duration",
            "volume_surge", "breakout_proximity", "stop_tightness",
        }
        self.assertEqual(set(components.keys()), expected)
        self.assertAlmostEqual(
            sum(components.values()),
            results[0].extra["pattern_quality_score"],
            places=1,
        )

    def test_quality_score_capped_at_ten(self):
        daily = _build_multiyear(
            touch_count=6, touch_dispersion_pct=0.1, touch_spread_frac=0.95,
            latest_close_pct_of_resistance=1.01, breakout_volume_ratio=2.5,
        )
        results = multiyear_breakout.detect({}, weekly=daily)
        self.assertEqual(len(results), 1)
        self.assertLessEqual(results[0].extra["pattern_quality_score"], 10.0)


if __name__ == "__main__":
    unittest.main()
