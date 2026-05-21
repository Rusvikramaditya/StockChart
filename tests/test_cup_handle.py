"""Tests for cup & handle detection thresholds and quality grading.

Detection is strict: textbook cup & handle requires a real handle (controlled
pullback from right rim, <=33% retrace, handle high near pivot, stop distance
<=10% from pivot). Each detected pattern carries a 0-10 pattern_quality_score.
"""

from __future__ import annotations

import unittest

import numpy as np

from patterns import ascending_triangle, cup_handle, vcp


def _build_cup(
    *,
    n: int = 120,
    pivot: float = 100.0,
    depth_pct: float = 20.0,
    rim_distance_pct: float = 1.0,
    handle_pullback_pct: float = 5.0,
    handle_high_gap_pct: float = 1.0,
    breakout: bool = False,
    declining_handle_volume: bool = True,
) -> dict:
    """Construct a synthetic cup & handle daily OHLCV dict.

    Cup spans bars 0..n-handle_len, handle is the trailing handle_len bars.
    Trough sits roughly center of cup. Left rim is at `pivot`, right rim is
    `pivot * (1 - rim_distance_pct/100)`. Handle high tests pivot within
    `handle_high_gap_pct`, handle low is `handle_pullback_pct` below pivot.
    """
    handle_len = max(8, n // 8)
    cup_end = n - handle_len
    high = np.zeros(n, dtype=float)
    low = np.zeros(n, dtype=float)
    close = np.zeros(n, dtype=float)
    volume = np.full(n, 1_000_000.0, dtype=float)

    left_rim = pivot
    right_rim = pivot * (1.0 - rim_distance_pct / 100.0)
    trough = pivot * (1.0 - depth_pct / 100.0)
    trough_idx = cup_end // 2

    # Cup: parabolic from left rim down to trough, back up to right rim
    x_left = np.arange(0, trough_idx + 1)
    if len(x_left) >= 2:
        t = (x_left - x_left[0]) / (x_left[-1] - x_left[0])
        cup_left = left_rim + (trough - left_rim) * (1 - (1 - t) ** 2)
        close[:trough_idx + 1] = cup_left
    x_right = np.arange(trough_idx, cup_end)
    if len(x_right) >= 2:
        t = (x_right - x_right[0]) / (x_right[-1] - x_right[0])
        cup_right = trough + (right_rim - trough) * (t ** 2)
        close[trough_idx:cup_end] = cup_right

    # Stamp explicit rim and trough values
    close[0] = left_rim
    close[trough_idx] = trough
    close[cup_end - 1] = right_rim
    high[:cup_end] = close[:cup_end] + 0.2
    low[:cup_end] = close[:cup_end] - 0.2
    # Forced rim highs/trough low
    high[0] = left_rim
    high[cup_end - 1] = right_rim
    low[trough_idx] = trough

    # Handle: pullback from right rim to handle_low, then drift back up
    handle_low = pivot * (1.0 - handle_pullback_pct / 100.0)
    handle_high = pivot * (1.0 - handle_high_gap_pct / 100.0)
    handle_low_idx = cup_end + handle_len // 2
    # Down-slope
    for i in range(cup_end, handle_low_idx + 1):
        t = (i - cup_end) / max(1, handle_low_idx - cup_end)
        close[i] = right_rim + (handle_low - right_rim) * t
    # Up-slope (back toward handle_high)
    for i in range(handle_low_idx, n):
        t = (i - handle_low_idx) / max(1, n - 1 - handle_low_idx)
        close[i] = handle_low + (handle_high - handle_low) * t
    close[handle_low_idx] = handle_low
    high[cup_end:] = close[cup_end:] + 0.2
    low[cup_end:] = close[cup_end:] - 0.2
    low[handle_low_idx] = handle_low
    # Make sure handle's max high equals handle_high
    high_at_top_of_handle = max(handle_low_idx + 1, n - 2)
    high[high_at_top_of_handle] = handle_high

    # Last bar: breakout or near-pivot
    if breakout:
        close[-1] = pivot * 1.02
        high[-1] = pivot * 1.025
        low[-1] = pivot * 1.005
    else:
        close[-1] = pivot * 0.99
        high[-1] = handle_high  # don't exceed pivot
        low[-1] = pivot * 0.985

    # Volume: cup high, handle low (contraction)
    if declining_handle_volume:
        volume[:cup_end] = 1_500_000.0
        volume[cup_end:] = 600_000.0

    open_ = close - 0.05
    return {
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


class CupHandleDetectionTest(unittest.TestCase):
    def test_textbook_pattern_detected(self):
        daily = _build_cup()
        results = cup_handle.detect(daily)
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r.pattern, "Cup & Handle")
        self.assertIn(r.status, {"PIVOT READY", "BREAKING OUT"})
        self.assertGreater(r.extra["pattern_quality_score"], 6.0)

    def test_no_handle_pullback_rejected(self):
        """Handle that's just sideways drift (<2% pullback) must reject."""
        daily = _build_cup(handle_pullback_pct=1.0)
        results = cup_handle.detect(daily)
        self.assertEqual(results, [], "Sideways handle must reject")

    def test_handle_far_below_pivot_rejected(self):
        """Handle whose high is >5% below pivot isn't testing resistance."""
        daily = _build_cup(handle_high_gap_pct=7.0, handle_pullback_pct=8.0)
        results = cup_handle.detect(daily)
        self.assertEqual(results, [], "Handle not testing pivot must reject")

    def test_emcure_style_deep_stop_rejected(self):
        """Stop distance > 10% from pivot rejected as untradable.

        EMCURE example: cup depth ~50%, handle low ≈ cup low, stop at 1381
        from entry 1585 = 12.9% risk. Real-money trades need tighter stops.
        """
        daily = _build_cup(depth_pct=45.0, handle_pullback_pct=12.0)
        results = cup_handle.detect(daily)
        self.assertEqual(results, [], "Wide-stop setup must reject")

    def test_breakout_status_reachable(self):
        """BREAKING OUT status must fire when last close > pivot.

        Regression test for the bug where pivot = max(high) included the
        breakout candle itself, making close > pivot unreachable.
        """
        daily = _build_cup(breakout=True)
        results = cup_handle.detect(daily)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "BREAKING OUT")


class CupHandleQualityScoreTest(unittest.TestCase):
    def test_textbook_grades_at_least_seven(self):
        daily = _build_cup(
            depth_pct=20.0,
            rim_distance_pct=1.0,
            handle_pullback_pct=5.0,
            handle_high_gap_pct=1.0,
            declining_handle_volume=True,
            breakout=True,
        )
        results = cup_handle.detect(daily)
        self.assertEqual(len(results), 1)
        score = results[0].extra["pattern_quality_score"]
        self.assertGreaterEqual(score, 7.0, f"Textbook should grade >=7.0, got {score}")
        self.assertLessEqual(score, 10.0)

    def test_borderline_grades_lower_than_textbook(self):
        textbook = _build_cup(
            depth_pct=20.0,
            rim_distance_pct=0.5,
            handle_pullback_pct=5.0,
            handle_high_gap_pct=0.5,
            declining_handle_volume=True,
            breakout=True,
        )
        borderline = _build_cup(
            depth_pct=32.0,
            rim_distance_pct=5.0,
            handle_pullback_pct=9.0,
            handle_high_gap_pct=4.0,
            declining_handle_volume=False,
            breakout=False,
        )
        t_res = cup_handle.detect(textbook)
        b_res = cup_handle.detect(borderline)
        self.assertEqual(len(t_res), 1)
        self.assertEqual(len(b_res), 1)
        self.assertGreater(
            t_res[0].extra["pattern_quality_score"],
            b_res[0].extra["pattern_quality_score"],
        )

    def test_quality_breakdown_components_present(self):
        daily = _build_cup()
        results = cup_handle.detect(daily)
        self.assertEqual(len(results), 1)
        breakdown = results[0].extra["pattern_quality_breakdown"]
        expected = {
            "rim_symmetry",
            "depth_healthy",
            "roundedness",
            "handle_quality",
            "volume_contraction",
            "breakout_proximity",
            "stop_tightness",
        }
        self.assertEqual(set(breakdown.keys()), expected)
        self.assertAlmostEqual(
            sum(breakdown.values()),
            results[0].extra["pattern_quality_score"],
            places=1,
        )

    def test_quality_score_capped_at_ten(self):
        daily = _build_cup(
            depth_pct=20.0,
            rim_distance_pct=0.2,
            handle_pullback_pct=4.0,
            handle_high_gap_pct=0.5,
            declining_handle_volume=True,
            breakout=True,
        )
        results = cup_handle.detect(daily)
        self.assertEqual(len(results), 1)
        self.assertLessEqual(results[0].extra["pattern_quality_score"], 10.0)


class BreakingOutBugRegressionTest(unittest.TestCase):
    """The detectors that compute pivot from a window that includes today's
    bar previously had `breakout = close > pivot` unreachable (close <= high).
    These tests verify the fix: BREAKING OUT must fire on a real breakout.
    """

    def test_ascending_triangle_breaking_out_fires(self):
        """Build an ascending triangle where today's close exceeds the prior
        resistance line and verify status = BREAKING OUT."""
        n = 60
        resistance = 100.0
        high = np.full(n, resistance * 0.94)
        low = np.full(n, resistance * 0.93)
        close = np.full(n, resistance * 0.935)
        volume = np.full(n, 1_000_000.0)

        # Place 3 touches at resistance (idx 35, 45, 55) and 3 rising lows
        # well below (idx 5, 15, 25)
        for idx, val in [(5, 82.0), (15, 85.0), (25, 88.0)]:
            low[idx] = val
            high[idx] = val + 0.5
            close[idx] = val + 0.3
        for idx in [35, 45, 55]:
            high[idx] = resistance
            close[idx] = resistance - 0.3
            low[idx] = resistance - 0.5

        # Pull baseline up between touches to avoid spurious local lows.
        # The baseline is gently rising.
        baseline = np.linspace(resistance * 0.94, resistance * 0.97, n)
        for i in range(n):
            if i not in {5, 15, 25, 35, 45, 55}:
                low[i] = baseline[i] - 0.3
                high[i] = baseline[i] + 0.3
                close[i] = baseline[i]

        # Breakout candle: close > prior resistance, high may exceed prior
        # resistance (which is the pre-existing 100.0).
        close[-1] = resistance * 1.02
        high[-1] = resistance * 1.025
        low[-1] = resistance * 1.005

        daily = {"open": close - 0.1, "high": high, "low": low, "close": close, "volume": volume}
        results = ascending_triangle.detect(daily)
        self.assertEqual(len(results), 1, "Ascending triangle breakout should detect")
        self.assertEqual(
            results[0].status,
            "BREAKING OUT",
            "Status must be BREAKING OUT when close > prior resistance",
        )


if __name__ == "__main__":
    unittest.main()
