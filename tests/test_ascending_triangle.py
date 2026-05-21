"""Tests for ascending triangle detection thresholds and quality grading.

Detection is strict (textbook only): >=3 flat-resistance touches, >=3 rising
lows, low touch dispersion. Each detected pattern carries a 0-10
pattern_quality_score so the UI can grade pattern cleanliness independently
from the conviction score.
"""

from __future__ import annotations

import unittest

import numpy as np

from patterns import ascending_triangle


def _build_triangle(
    *,
    touches: int = 4,
    rising_lows: int = 4,
    resistance: float = 100.0,
    touch_jitter_pct: float = 0.1,
    low_start: float = 82.0,
    low_step: float = 2.0,
    lookback: int = 60,
    declining_volume: bool = True,
) -> dict:
    """Construct a synthetic ascending triangle daily OHLCV dict.

    Baseline candles sit at `baseline` (well below resistance and the lows).
    Explicit touch candles and rising-low candles are stamped at chosen
    indices. argrelextrema(order=4) requires >=4 bars on each side, so
    pivots are spaced >= 5 bars apart.
    """
    rng = np.random.default_rng(seed=42)
    n = lookback
    # Monotonically rising baseline so argrelextrema only catches explicit
    # pivot dips. A flat or noisy baseline would register intermediate noise
    # as spurious local lows under less_equal semantics.
    baseline = np.linspace(resistance * 0.93, resistance * 0.97, n)
    high = baseline + 0.4
    low = baseline - 0.4
    close = baseline.copy()
    volume = np.full(n, 1_000_000.0, dtype=float)

    # Spread rising-low pivots across early portion (idx ~5 .. n/2)
    low_indices = np.linspace(5, n // 2, rising_lows).astype(int)
    for i, idx in enumerate(low_indices):
        depth = low_start + i * low_step
        low[idx] = depth
        high[idx] = depth + 0.5
        close[idx] = depth + 0.3

    # Spread touch pivots across later portion (idx ~ n/2+5 .. n-3).
    # Touch peaks spread evenly across [resistance - touch_jitter_pct%,
    # resistance], so range = touch_jitter_pct exactly. Deterministic.
    touch_indices = np.linspace(n // 2 + 5, n - 3, touches).astype(int)
    lower_peak = resistance * (1 - touch_jitter_pct / 100.0)
    peaks = np.linspace(lower_peak, resistance, touches)
    for i, idx in enumerate(touch_indices):
        peak = float(peaks[i])
        high[idx] = peak
        low[idx] = peak - 0.6
        close[idx] = peak - 0.2

    # Last bar — pulled clearly below resistance tolerance so it does NOT
    # leak into touch-count or local-low detection, but close stays within
    # the detector's within_breakout_pct (4%) for PIVOT READY status.
    close[-1] = resistance * 0.975
    high[-1] = resistance * 0.978
    low[-1] = resistance * 0.972

    # Volume contraction during pattern
    if declining_volume:
        volume[: n // 2] = 1_500_000.0
        volume[n // 2 :] = 700_000.0

    open_ = close.copy() - 0.1
    return {
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


class AscendingTriangleDetectionTest(unittest.TestCase):
    """Strict detection: bad patterns must be rejected."""

    def test_textbook_pattern_detected(self):
        daily = _build_triangle(touches=4, rising_lows=4)
        results = ascending_triangle.detect(daily)
        self.assertEqual(len(results), 1, "Textbook pattern must detect")
        r = results[0]
        self.assertEqual(r.pattern, "Ascending Triangle")
        self.assertEqual(r.status, "PIVOT READY")
        self.assertGreater(r.extra["pattern_quality_score"], 6.0)

    def test_two_touches_rejected(self):
        """Only 2 resistance touches must be rejected (textbook needs 3+)."""
        daily = _build_triangle(touches=2, rising_lows=4)
        results = ascending_triangle.detect(daily)
        self.assertEqual(results, [], "2 touches must reject under new threshold")

    def test_two_rising_lows_rejected(self):
        """Only 2 rising lows must be rejected (textbook needs 3+)."""
        daily = _build_triangle(touches=4, rising_lows=2)
        results = ascending_triangle.detect(daily)
        self.assertEqual(results, [], "2 rising lows must reject under new threshold")

    def test_messy_resistance_zone_rejected(self):
        """Touches spread across a wide zone (>1% range) must reject.

        APARINDS-style case ChatGPT flagged: touches between e.g. 12,676 and
        13,024 are technically clustered under 1.5% tolerance but span a
        2%+ range. Not a flat resistance line.
        """
        daily = _build_triangle(touches=4, rising_lows=4, touch_jitter_pct=1.3)
        results = ascending_triangle.detect(daily)
        self.assertEqual(results, [], "Wide-range touches must reject")


class AscendingTriangleQualityScoreTest(unittest.TestCase):
    """Quality score: 0-10 grade, in extra['pattern_quality_score']."""

    def test_textbook_scores_higher_than_borderline(self):
        """Clean pattern (many touches, tight, declining volume) must grade
        strictly higher than borderline (min touches, looser, flat volume)."""
        textbook = _build_triangle(
            touches=5,
            rising_lows=4,
            touch_jitter_pct=0.05,
            declining_volume=True,
        )
        borderline = _build_triangle(
            touches=3,
            rising_lows=3,
            touch_jitter_pct=0.6,
            declining_volume=False,
        )
        textbook_res = ascending_triangle.detect(textbook)
        borderline_res = ascending_triangle.detect(borderline)
        self.assertEqual(len(textbook_res), 1)
        self.assertEqual(len(borderline_res), 1)
        textbook_score = textbook_res[0].extra["pattern_quality_score"]
        borderline_score = borderline_res[0].extra["pattern_quality_score"]
        self.assertGreater(textbook_score, borderline_score)

    def test_textbook_grades_at_least_seven(self):
        daily = _build_triangle(
            touches=5,
            rising_lows=4,
            touch_jitter_pct=0.05,
            declining_volume=True,
        )
        results = ascending_triangle.detect(daily)
        self.assertEqual(len(results), 1)
        score = results[0].extra["pattern_quality_score"]
        self.assertGreaterEqual(score, 7.0, f"Textbook should grade >=7.0, got {score}")
        self.assertLessEqual(score, 10.0)

    def test_quality_breakdown_components_present(self):
        daily = _build_triangle()
        results = ascending_triangle.detect(daily)
        self.assertEqual(len(results), 1)
        breakdown = results[0].extra["pattern_quality_breakdown"]
        expected_keys = {
            "touches",
            "touch_flatness",
            "rising_lows",
            "slope_steadiness",
            "volume_contraction",
            "breakout_proximity",
        }
        self.assertEqual(set(breakdown.keys()), expected_keys)
        self.assertAlmostEqual(
            sum(breakdown.values()),
            results[0].extra["pattern_quality_score"],
            places=1,
        )

    def test_quality_score_capped_at_ten(self):
        """Even with all components maxed, total must not exceed 10.0."""
        daily = _build_triangle(
            touches=6,
            rising_lows=5,
            touch_jitter_pct=0.02,
            declining_volume=True,
        )
        results = ascending_triangle.detect(daily)
        self.assertEqual(len(results), 1)
        self.assertLessEqual(results[0].extra["pattern_quality_score"], 10.0)


if __name__ == "__main__":
    unittest.main()
