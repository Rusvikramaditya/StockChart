"""Tests for Inverse Head & Shoulders detection thresholds and quality grading.

Detection is strict (textbook only): shoulders symmetric (<=7%), head
meaningfully below shoulder avg (>=3%), time-symmetric (<=2.5x), prior
downtrend (>=8% decline into LS), capped neckline downslope (<=5%), and
tradable stop (<=10%). Each detected pattern carries a 0-10
pattern_quality_score independent of the conviction score.
"""

from __future__ import annotations

import unittest

import numpy as np

from patterns import inv_head_shoulders


def _build_ihs(
    *,
    lookback: int = 120,
    ls_idx: int = 45,
    head_idx: int = 70,
    rs_idx: int = 95,
    ls_low: float = 91.0,
    head_low: float = 84.0,
    rs_low: float = 91.0,
    neckline: float = 100.0,
    neckline_downslope_pct: float = 0.0,
    prior_decline_pct: float = 12.0,
    prior_peak_offset: int = 25,
    declining_volume: bool = True,
    final_close_offset_pct: float = -2.0,
    invalidation_dip_pct: float = 0.0,
) -> dict:
    """Build a synthetic IHS daily OHLCV dict.

    Strategy: lay down a smooth piecewise-linear baseline through control
    points (prior_peak -> LS area -> left_neck_peak -> head area ->
    right_neck_peak -> RS area -> final close), then depress three exact
    bars (LS, head, RS) just below the baseline so argrelextrema(order=5)
    flags only those bars as local lows.

    Control points (idx, value):
        0                          -> prior_peak * 0.98   (gentle pre-decline tail)
        ls_idx - prior_peak_offset -> prior_peak          (THIS is what the
                                       detector's prior-decline scan finds)
        ls_idx                     -> ls_low + 1.5        (baseline just above LS)
        left_neck_idx              -> neckline            (left rally peak)
        head_idx                   -> head_low + 1.5
        right_neck_idx             -> neckline * (1 - downslope_pct/100)
        rs_idx                     -> rs_low + 1.5
        n - 1                      -> final_close         (placed relative to
                                       sloped neckline at the current bar)
    """
    n = lookback
    left_neck_idx = (ls_idx + head_idx) // 2
    right_neck_idx = (head_idx + rs_idx) // 2
    left_neck_price = neckline
    right_neck_price = neckline * (1.0 - neckline_downslope_pct / 100.0)

    prior_peak = ls_low / max(1e-6, 1.0 - prior_decline_pct / 100.0)
    prior_peak_idx = max(5, ls_idx - prior_peak_offset)

    # Final close offset = pct of sloped-neckline value at current bar.
    if right_neck_idx > left_neck_idx:
        slope = (right_neck_price - left_neck_price) / (right_neck_idx - left_neck_idx)
    else:
        slope = 0.0
    neckline_at_end = left_neck_price + slope * ((n - 1) - left_neck_idx)
    final_close = neckline_at_end * (1.0 + final_close_offset_pct / 100.0)

    ctrl_x = [
        0,
        prior_peak_idx,
        ls_idx,
        left_neck_idx,
        head_idx,
        right_neck_idx,
        rs_idx,
        n - 1,
    ]
    ctrl_y = [
        prior_peak * 0.98,
        prior_peak,
        ls_low + 1.5,
        left_neck_price,
        head_low + 1.5,
        right_neck_price,
        rs_low + 1.5,
        final_close,
    ]
    # Ensure strictly increasing x for np.interp
    assert all(ctrl_x[i] < ctrl_x[i + 1] for i in range(len(ctrl_x) - 1)), ctrl_x

    baseline = np.interp(np.arange(n), ctrl_x, ctrl_y)
    high = baseline + 0.4
    low = baseline - 0.4
    close = baseline.copy()

    # Depress trough bars so each is a clear local min vs ±5 neighbors.
    _stamp_trough(high, low, close, ls_idx, ls_low)
    _stamp_trough(high, low, close, head_idx, head_low)
    _stamp_trough(high, low, close, rs_idx, rs_low)

    # Optional invalidation dip after RS
    if invalidation_dip_pct > 0 and rs_idx + 5 < n:
        dip_idx = rs_idx + 5
        dip_low = rs_low * (1.0 - invalidation_dip_pct / 100.0)
        low[dip_idx] = dip_low
        close[dip_idx] = dip_low + 0.2
        high[dip_idx] = dip_low + 0.6

    # Volume: declining through pattern is textbook
    volume = np.full(n, 1_000_000.0, dtype=float)
    if declining_volume:
        volume[: ls_idx + 3] = 1_500_000.0  # high vol approaching LS
        volume[ls_idx + 3 : head_idx + 3] = 1_000_000.0
        volume[head_idx + 3 : rs_idx + 3] = 700_000.0  # drying up at RS
        volume[rs_idx + 3 :] = 1_300_000.0  # breakout-area pickup

    open_ = close - 0.1
    return {
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def _stamp_trough(high, low, close, idx, depth, half=2):
    """Force a local min: pivot bar deepest, ±half bars slightly higher."""
    n = len(low)
    for off in range(-half, half + 1):
        i = idx + off
        if 0 <= i < n:
            level = depth + abs(off) * 0.3
            low[i] = level
            high[i] = level + 0.5
            close[i] = level + 0.2


class IhsDetectionTest(unittest.TestCase):
    """Strict detection: bad patterns must reject."""

    def test_textbook_pattern_detected(self):
        daily = _build_ihs()
        results = inv_head_shoulders.detect(daily)
        self.assertEqual(len(results), 1, "Textbook IHS must detect")
        r = results[0]
        self.assertEqual(r.pattern, "Inverse Head & Shoulders")
        self.assertIn(r.status, ("PIVOT READY", "BREAKING OUT"))
        self.assertGreater(r.extra["pattern_quality_score"], 6.0)

    def test_breakout_detected(self):
        """Final close above neckline -> BREAKING OUT."""
        daily = _build_ihs(final_close_offset_pct=+1.5)
        results = inv_head_shoulders.detect(daily)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "BREAKING OUT")

    def test_asymmetric_shoulders_rejected(self):
        """Shoulders >7% apart must reject (was 10% before)."""
        daily = _build_ihs(ls_low=82.0, rs_low=92.0)  # ~11.5% diff
        results = inv_head_shoulders.detect(daily)
        self.assertEqual(results, [], "Lopsided shoulders must reject")

    def test_flat_head_rejected(self):
        """Head <3% below shoulder avg = triple bottom, not IHS."""
        daily = _build_ihs(ls_low=82.0, head_low=81.0, rs_low=82.0)
        results = inv_head_shoulders.detect(daily)
        self.assertEqual(results, [], "Flat head must reject")

    def test_no_prior_downtrend_rejected(self):
        """Without prior decline into LS, not a reversal pattern."""
        daily = _build_ihs(prior_decline_pct=3.0)
        results = inv_head_shoulders.detect(daily)
        self.assertEqual(results, [], "Pattern without prior downtrend must reject")

    def test_steep_neckline_downslope_rejected(self):
        """Neckline downslope >5% means failed rally between shoulders."""
        daily = _build_ihs(neckline_downslope_pct=8.0)
        results = inv_head_shoulders.detect(daily)
        self.assertEqual(results, [], "Steep neckline downslope must reject")

    def test_invalidated_after_right_shoulder_rejected(self):
        """Lower low after RS invalidates the pattern.

        Dip 10% below RS breaks the original triplet (post-RS lower low) AND
        cannot form a new valid triplet either (dip falls below head, so
        ``head < right_shoulder`` constraint fails).
        """
        daily = _build_ihs(invalidation_dip_pct=10.0)
        results = inv_head_shoulders.detect(daily)
        self.assertEqual(results, [], "Lower low after RS must invalidate")

    def test_stale_breakout_rejected(self):
        """Price >8% past neckline = stale."""
        daily = _build_ihs(final_close_offset_pct=+12.0)
        results = inv_head_shoulders.detect(daily)
        self.assertEqual(results, [], "Stale breakout must reject")

    def test_too_short_duration_rejected(self):
        """Pattern <25 bars LS->RS is noise on daily."""
        daily = _build_ihs(ls_idx=50, head_idx=60, rs_idx=70)  # 20 bars span
        results = inv_head_shoulders.detect(daily)
        self.assertEqual(results, [], "Too-short pattern must reject")


class IhsQualityScoreTest(unittest.TestCase):
    """0-10 grade in extra['pattern_quality_score']."""

    def test_textbook_grades_higher_than_borderline(self):
        textbook = _build_ihs(
            ls_low=87.0, head_low=72.0, rs_low=87.0,
            neckline_downslope_pct=0.0,
            prior_decline_pct=18.0,
            declining_volume=True,
        )
        borderline = _build_ihs(
            ls_low=87.0, head_low=80.0, rs_low=90.0,  # ~3.4% symmetry, ~9.6% head depth
            neckline_downslope_pct=3.0,
            prior_decline_pct=9.0,
            declining_volume=False,
        )
        t_res = inv_head_shoulders.detect(textbook)
        b_res = inv_head_shoulders.detect(borderline)
        self.assertEqual(len(t_res), 1)
        self.assertEqual(len(b_res), 1)
        self.assertGreater(
            t_res[0].extra["pattern_quality_score"],
            b_res[0].extra["pattern_quality_score"],
        )

    def test_textbook_grades_at_least_seven(self):
        daily = _build_ihs(
            ls_low=87.0, head_low=72.0, rs_low=87.0,
            neckline_downslope_pct=0.0,
            prior_decline_pct=18.0,
            declining_volume=True,
        )
        results = inv_head_shoulders.detect(daily)
        self.assertEqual(len(results), 1)
        score = results[0].extra["pattern_quality_score"]
        self.assertGreaterEqual(score, 7.0, f"Textbook must grade >=7.0, got {score}")
        self.assertLessEqual(score, 10.0)

    def test_quality_breakdown_components_present(self):
        daily = _build_ihs()
        results = inv_head_shoulders.detect(daily)
        self.assertEqual(len(results), 1)
        breakdown = results[0].extra["pattern_quality_breakdown"]
        expected_keys = {
            "shoulder_symmetry",
            "head_depth",
            "time_symmetry",
            "neckline_quality",
            "volume_pattern",
            "prior_downtrend",
            "breakout_proximity",
            "stop_tightness",
        }
        self.assertEqual(set(breakdown.keys()), expected_keys)
        self.assertAlmostEqual(
            sum(breakdown.values()),
            results[0].extra["pattern_quality_score"],
            places=1,
        )

    def test_quality_score_capped_at_ten(self):
        daily = _build_ihs(
            ls_low=87.0, head_low=70.0, rs_low=87.0,
            neckline_downslope_pct=0.0,  # flat neckline (graded full)
            prior_decline_pct=22.0,
            declining_volume=True,
            final_close_offset_pct=+0.5,  # broken out
        )
        results = inv_head_shoulders.detect(daily)
        self.assertEqual(len(results), 1)
        self.assertLessEqual(results[0].extra["pattern_quality_score"], 10.0)


class IhsGeometryTest(unittest.TestCase):
    """Plotting geometry: detector must emit sloped-neckline anchors."""

    def test_neckline_anchors_present(self):
        daily = _build_ihs()
        results = inv_head_shoulders.detect(daily)
        self.assertEqual(len(results), 1)
        extra = results[0].extra
        for key in ("left_neck_idx", "right_neck_idx", "left_neck_price", "right_neck_price"):
            self.assertIn(key, extra, f"missing geometry field {key}")
        self.assertLess(extra["left_neck_idx"], extra["right_neck_idx"])
        self.assertLess(extra["left_shoulder_idx"], extra["head_idx"])
        self.assertLess(extra["head_idx"], extra["right_shoulder_idx"])

    def test_target_uses_measured_move(self):
        daily = _build_ihs()
        results = inv_head_shoulders.detect(daily)
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertGreater(r.target, r.pivot, "Target above neckline")
        self.assertLess(r.stop_loss, r.pivot, "Stop below neckline")

    def test_breakout_reachable(self):
        """Cross-pattern bug regression: 'BREAKING OUT' must be reachable.
        Neckline must come from past bars only, not include today's bar."""
        daily = _build_ihs(final_close_offset_pct=+2.0)
        results = inv_head_shoulders.detect(daily)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "BREAKING OUT")


if __name__ == "__main__":
    unittest.main()
