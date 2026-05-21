"""Tests for VCP detection thresholds and quality grading.

Detection is strict (textbook Minervini-style): >=3 successively tighter
contractions detected via real swing extrema, highs clustered near pivot,
volume declining, minimum pattern duration. Each detected pattern carries
a 0-10 pattern_quality_score in extra.
"""

from __future__ import annotations

import unittest

import numpy as np

from patterns import vcp


def _build_vcp(
    *,
    contractions: int = 4,
    first_depth: float = 20.0,
    tightening: float = 0.5,
    final_depth: float | None = None,
    pivot: float = 100.0,
    declining_volume: bool = True,
    breakout: bool = False,
    total_bars: int = 260,
    vcp_bars: int = 90,
    leg_len: int = 11,
    fill_recovery_high: float | None = None,
    high_overrides: dict[int, float] | None = None,
) -> dict:
    """Construct a synthetic VCP-shaped daily OHLCV dict.

    Pre-VCP region: rising trend from 0.4*pivot to ~pivot (ensures stage 2).
    VCP region: alternating down-leg + up-leg contractions of `leg_len` bars
    each, with each pullback `tightening` of the prior. Last close is near
    pivot (PIVOT READY) or above pivot (BREAKING OUT).

    `high_overrides` maps a swing-high contraction index (0-based) to a
    custom high value (lets tests sculpt high dispersion).
    """
    n = total_bars
    pre_bars = n - vcp_bars
    close = np.zeros(n, dtype=float)
    close[:pre_bars] = np.linspace(pivot * 0.4, pivot * 0.98, pre_bars)

    depths = [first_depth]
    for _ in range(1, contractions):
        depths.append(depths[-1] * tightening)
    if final_depth is not None:
        depths[-1] = final_depth

    overrides = high_overrides or {}
    pos = pre_bars
    current_high = float(overrides.get(0, pivot))
    close[pos] = current_high

    for i, depth in enumerate(depths):
        low_val = current_high * (1.0 - depth / 100.0)
        end = min(pos + leg_len + 1, n)
        if end - pos >= 2:
            close[pos:end] = np.linspace(current_high, low_val, end - pos)
        pos = end - 1

        if i < contractions - 1:
            next_high = float(overrides.get(i + 1, pivot))
        else:
            next_high = (
                fill_recovery_high
                if fill_recovery_high is not None
                else pivot * 0.99
            )
        end = min(pos + leg_len + 1, n)
        if end - pos >= 2:
            close[pos:end] = np.linspace(low_val, next_high, end - pos)
        pos = end - 1
        current_high = next_high

    if pos + 1 < n:
        close[pos + 1 : n] = current_high

    if breakout:
        close[-1] = pivot * 1.02

    high = close + 0.05
    low = close - 0.05

    volume = np.full(n, 1_000_000.0, dtype=float)
    if declining_volume:
        # Per-leg declining volume: each contraction block has lower volume.
        leg_vols = np.linspace(1_500_000.0, 400_000.0, contractions)
        block = 2 * leg_len
        for i in range(contractions):
            v_start = pre_bars + i * block
            v_end = min(v_start + block + 1, n)
            volume[v_start:v_end] = leg_vols[i]

    open_ = close - 0.02
    return {
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


class VCPDetectionTest(unittest.TestCase):
    """Strict detection: weak patterns must be rejected."""

    def test_textbook_pattern_detected(self):
        daily = _build_vcp()
        results = vcp.detect(daily)
        self.assertEqual(len(results), 1, "Textbook VCP must detect")
        r = results[0]
        self.assertEqual(r.pattern, "VCP")
        self.assertEqual(r.status, "PIVOT READY")
        self.assertGreater(r.extra["pattern_quality_score"], 6.0)
        self.assertEqual(len(r.extra["contractions_pct"]), 4)

    def test_two_contractions_rejected(self):
        """Only 2 contractions must reject (textbook needs >=3)."""
        daily = _build_vcp(contractions=2, leg_len=18)
        results = vcp.detect(daily)
        self.assertEqual(results, [], "2 contractions must reject")

    def test_loose_tightening_rejected(self):
        """Ratios above tightening_ratio_max must reject (no real funnel)."""
        # tightening=0.85 -> ratios ~0.85 each, > 0.80 threshold
        daily = _build_vcp(contractions=4, first_depth=12.0, tightening=0.85)
        results = vcp.detect(daily)
        self.assertEqual(results, [], "Loose tightening must reject")

    def test_wide_final_contraction_rejected(self):
        """Final contraction > max_final_tightness_pct must reject."""
        daily = _build_vcp(
            contractions=4,
            first_depth=20.0,
            tightening=0.7,
            final_depth=8.0,  # > 6.0 max
        )
        results = vcp.detect(daily)
        self.assertEqual(results, [], "Final >6% must reject")

    def test_deep_first_contraction_rejected(self):
        """First contraction > max_first_contraction_pct must reject."""
        daily = _build_vcp(contractions=4, first_depth=40.0, tightening=0.5)
        results = vcp.detect(daily)
        self.assertEqual(results, [], "First contraction >35% must reject")

    def test_short_duration_rejected(self):
        """Duration below min_pattern_bars must reject."""
        # 3 contractions x leg_len=4 -> 5*4 = 20 bars < 25 min
        daily = _build_vcp(contractions=3, leg_len=4)
        results = vcp.detect(daily)
        self.assertEqual(results, [], "Short duration must reject")

    def test_high_dispersion_rejected(self):
        """Swing highs spread > max_high_dispersion_pct must reject."""
        # Drop the 3rd swing high (index 2) to 88 -> dispersion = 12%
        daily = _build_vcp(
            contractions=4,
            high_overrides={2: 88.0},
        )
        results = vcp.detect(daily)
        self.assertEqual(results, [], "High dispersion must reject")

    def test_no_volume_decline_rejected(self):
        """Flat / rising volume must reject when volume_declining gate on."""
        daily = _build_vcp(declining_volume=False)
        results = vcp.detect(daily)
        self.assertEqual(results, [], "Flat volume must reject")

    def test_breaking_out_status_fires(self):
        """BREAKING OUT must fire when last close exceeds pivot.

        Regression for the pivot-includes-today bug pattern.
        """
        daily = _build_vcp(breakout=True)
        results = vcp.detect(daily)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "BREAKING OUT")

    def test_breakout_extension_too_far_rejected(self):
        """Price already > max_breakout_extension_pct past pivot must reject (stale)."""
        daily = _build_vcp(breakout=True)
        # Push last close 12% above pivot (> 8% extension limit)
        daily["close"][-1] = 100.0 * 1.12
        daily["high"][-1] = daily["close"][-1] + 0.05
        results = vcp.detect(daily)
        self.assertEqual(results, [], "Stale breakout must reject")


class VCPQualityScoreTest(unittest.TestCase):
    """Quality score: 0-10 grade in extra['pattern_quality_score']."""

    def test_textbook_grades_at_least_seven(self):
        daily = _build_vcp(
            contractions=5,
            first_depth=20.0,
            tightening=0.5,
            declining_volume=True,
            breakout=True,
            leg_len=8,
        )
        results = vcp.detect(daily)
        self.assertEqual(len(results), 1)
        score = results[0].extra["pattern_quality_score"]
        self.assertGreaterEqual(score, 7.0, f"Textbook should grade >=7.0, got {score}")
        self.assertLessEqual(score, 10.0)

    def test_textbook_grades_higher_than_borderline(self):
        textbook = _build_vcp(
            contractions=5,
            first_depth=18.0,
            tightening=0.45,
            declining_volume=True,
            breakout=True,
            leg_len=8,
        )
        borderline = _build_vcp(
            contractions=3,
            first_depth=30.0,
            tightening=0.78,
            final_depth=5.5,
            declining_volume=False,
            breakout=False,
            leg_len=12,
        )
        t_res = vcp.detect(textbook)
        b_res = vcp.detect(borderline)
        # borderline may or may not detect; if it does, score must be lower.
        self.assertEqual(len(t_res), 1)
        if b_res:
            self.assertGreater(
                t_res[0].extra["pattern_quality_score"],
                b_res[0].extra["pattern_quality_score"],
            )

    def test_quality_breakdown_components_present(self):
        daily = _build_vcp()
        results = vcp.detect(daily)
        self.assertEqual(len(results), 1)
        breakdown = results[0].extra["pattern_quality_breakdown"]
        expected = {
            "contraction_count",
            "tightening_progression",
            "final_tightness",
            "volume_dryup",
            "pivot_proximity",
            "base_depth",
        }
        self.assertEqual(set(breakdown.keys()), expected)
        self.assertAlmostEqual(
            sum(breakdown.values()),
            results[0].extra["pattern_quality_score"],
            places=1,
        )

    def test_quality_score_capped_at_ten(self):
        """All components maxed must still cap at 10.0."""
        daily = _build_vcp(
            contractions=5,
            first_depth=12.0,
            tightening=0.4,
            declining_volume=True,
            breakout=True,
            leg_len=8,
        )
        results = vcp.detect(daily)
        self.assertEqual(len(results), 1)
        self.assertLessEqual(results[0].extra["pattern_quality_score"], 10.0)


class VCPBreakoutBugRegressionTest(unittest.TestCase):
    """Regression: pivot is computed from prior swing highs, not today's bar.

    Previously, detectors that built the pivot from a window that included
    today's bar made `close > pivot` unreachable (close <= high). VCP must
    fire BREAKING OUT when today's close clears the consolidation pivot.
    """

    def test_breaking_out_reachable(self):
        daily = _build_vcp(breakout=True)
        results = vcp.detect(daily)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "BREAKING OUT")
        # Pivot is the textbook 100.0 from synthetic build, NOT the breakout
        # candle's close.
        self.assertAlmostEqual(results[0].pivot, 100.0, places=1)
        self.assertGreater(daily["close"][-1], results[0].pivot)


if __name__ == "__main__":
    unittest.main()
