"""Bull Flag detector audit tests.

Verifies strict textbook detection (pole strength + flag tightness + volume
contraction + prior uptrend + non-upsloping flag) and the new 0-10
pattern_quality_score breakdown. Also pins the BREAKING OUT regression
(pivot_high must exclude the current bar so close > pivot is reachable).
"""

from __future__ import annotations

import unittest

import numpy as np

from patterns import bull_flag


def _build_bull_flag(
    *,
    prior_bars: int = 50,
    pole_bars: int = 8,
    flag_bars: int = 8,
    prior_gain_pct: float = 12.0,
    pole_gain_pct: float = 20.0,
    pole_drawdown_pct: float = 1.5,
    flag_pullback_pct: float = 5.0,
    flag_vol_ratio: float = 0.5,
    flag_upslope_pct: float = -2.0,  # downsloping
    final_close_offset_pct: float = -0.5,  # below pivot (PIVOT READY)
) -> dict:
    """Synthetic bull flag aligned to the detector's right-anchored window.

    Detector reads the flag from ``high[n - flag_len : -1]`` and the pole
    from the bars directly before that. So the synth must position the
    flag in the LAST ``flag_bars`` bars before the final bar.

    Index layout (right-aligned):
        [0                                .. n-1-flag_bars-pole_bars)  prior uptrend
        [n-1-flag_bars-pole_bars          .. n-1-flag_bars)            pole
        [n-1-flag_bars                    .. n-1)                      flag
        [n-1]                                                          final bar
    """
    pole_vol = 1_800_000.0
    prior_vol = 800_000.0
    flag_vol = pole_vol * flag_vol_ratio

    start_price = 100.0
    prior_end_price = start_price * (1.0 + prior_gain_pct / 100.0)
    pole_end_price = prior_end_price * (1.0 + pole_gain_pct / 100.0)
    flag_low_price = pole_end_price * (1.0 - flag_pullback_pct / 100.0)
    flag_first_close = pole_end_price * 0.995
    flag_last_close = flag_first_close * (1.0 + flag_upslope_pct / 100.0)
    flag_high_price = max(flag_first_close, flag_last_close, pole_end_price * 0.999)
    final_close = flag_high_price * (1.0 + final_close_offset_pct / 100.0)

    n = prior_bars + pole_bars + flag_bars + 1
    flag_start = n - 1 - flag_bars
    flag_end_excl = n - 1  # exclusive (matches detector's [n - flag_len : -1])
    pole_start = flag_start - pole_bars
    pole_end_incl = flag_start - 1
    prior_start = 0
    prior_end_excl = pole_start

    high = np.zeros(n, dtype=float)
    low = np.zeros(n, dtype=float)
    close = np.zeros(n, dtype=float)
    volume = np.zeros(n, dtype=float)

    # 1. Prior uptrend - flat tail + linear ramp INSIDE the detector's
    #    prior_window so the detector measures gain across exactly the
    #    intended pct.
    prior_window = 30  # mirror config.BULL_FLAG["prior_uptrend_lookback_bars"]
    flat_end = max(0, prior_end_excl - prior_window)
    if flat_end > 0:
        high[:flat_end] = start_price + 0.4
        low[:flat_end] = start_price - 0.4
        close[:flat_end] = start_price
        volume[:flat_end] = prior_vol
    ramp_start = flat_end
    ramp_len = prior_end_excl - ramp_start
    if ramp_len > 0:
        ramp = np.linspace(start_price, prior_end_price, ramp_len)
        high[ramp_start:prior_end_excl] = ramp + 0.4
        low[ramp_start:prior_end_excl] = ramp - 0.4
        close[ramp_start:prior_end_excl] = ramp
        volume[ramp_start:prior_end_excl] = prior_vol

    # 2. Pole. Closes ramp linearly start->end. Drawdown is set relative
    #    to the bar's OWN high so the detector's running_max-based
    #    drawdown measurement equals pole_drawdown_pct exactly.
    pole_closes = np.linspace(prior_end_price, pole_end_price, pole_bars)
    high[pole_start:pole_end_incl + 1] = pole_closes + 0.5
    low[pole_start:pole_end_incl + 1] = pole_closes - 0.5
    close[pole_start:pole_end_incl + 1] = pole_closes
    volume[pole_start:pole_end_incl + 1] = pole_vol
    if pole_drawdown_pct > 0 and pole_bars >= 3:
        mid = pole_bars // 2
        bar_high = float(pole_closes[mid]) + 0.5
        low[pole_start + mid] = bar_high * (1.0 - pole_drawdown_pct / 100.0)

    # 3. Flag. Linear close ramp from first to last. Inject the flag_low at
    # the mid bar via the LOW. Set first flag bar's HIGH to flag_high_price
    # so the detector sees it as the pivot.
    flag_close_ramp = np.linspace(flag_first_close, flag_last_close, flag_bars)
    high[flag_start:flag_end_excl] = flag_close_ramp + 0.3
    low[flag_start:flag_end_excl] = flag_close_ramp - 0.3
    close[flag_start:flag_end_excl] = flag_close_ramp
    volume[flag_start:flag_end_excl] = flag_vol
    high[flag_start] = flag_high_price  # pivot anchor on the first flag bar
    low[flag_start + flag_bars // 2] = flag_low_price  # stop anchor

    # 4. Final bar (excluded from flag pivot computation but determines
    # BREAKING OUT vs PIVOT READY).
    high[-1] = final_close + 0.3
    low[-1] = final_close - 0.3
    close[-1] = final_close
    volume[-1] = flag_vol

    open_ = close - 0.1
    return {"open": open_, "high": high, "low": low, "close": close, "volume": volume}


class BullFlagDetectionTest(unittest.TestCase):

    def test_textbook_pattern_detected(self):
        daily = _build_bull_flag()
        results = bull_flag.detect(daily)
        self.assertEqual(len(results), 1, "Textbook bull flag must detect")
        r = results[0]
        self.assertEqual(r.pattern, "Bull Flag")
        self.assertGreater(r.extra["pattern_quality_score"], 6.0)

    def test_weak_pole_rejected(self):
        """Pole <15% (was 12%) must reject."""
        daily = _build_bull_flag(pole_gain_pct=10.0)
        self.assertEqual(bull_flag.detect(daily), [])

    def test_choppy_pole_rejected(self):
        """Pole drawdown >6% means not a clean pole."""
        daily = _build_bull_flag(pole_drawdown_pct=8.0)
        self.assertEqual(bull_flag.detect(daily), [])

    def test_deep_flag_pullback_rejected(self):
        """Pullback >8% is a correction, not a flag."""
        daily = _build_bull_flag(flag_pullback_pct=10.0)
        self.assertEqual(bull_flag.detect(daily), [])

    def test_loose_flag_volume_rejected(self):
        """Flag vol ratio >0.7 means no drying up."""
        daily = _build_bull_flag(flag_vol_ratio=0.85)
        self.assertEqual(bull_flag.detect(daily), [])

    def test_upsloping_flag_rejected(self):
        """An upsloping flag is a rising wedge, not a bull flag."""
        daily = _build_bull_flag(flag_upslope_pct=4.0)
        self.assertEqual(bull_flag.detect(daily), [])

    def test_no_prior_uptrend_rejected(self):
        """Without prior uptrend the pole isn't continuation."""
        daily = _build_bull_flag(prior_gain_pct=1.0)
        self.assertEqual(bull_flag.detect(daily), [])

    def test_stale_breakout_rejected(self):
        """Price already extended >5% past pivot is stale."""
        daily = _build_bull_flag(final_close_offset_pct=+8.0)
        self.assertEqual(bull_flag.detect(daily), [])


class BullFlagBreakoutReachabilityTest(unittest.TestCase):
    """Pivot-bug regression: BREAKING OUT must be reachable. ``flag_high``
    is computed from ``high[:-1]`` so latest close can exceed it."""

    def test_breakout_status_when_close_above_flag_high(self):
        daily = _build_bull_flag(final_close_offset_pct=+1.5)
        results = bull_flag.detect(daily)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "BREAKING OUT")


class BullFlagQualityScoreTest(unittest.TestCase):

    def test_textbook_grades_higher_than_borderline(self):
        textbook = _build_bull_flag(
            pole_gain_pct=28.0,
            pole_drawdown_pct=1.0,
            flag_pullback_pct=4.0,
            flag_vol_ratio=0.35,
            flag_upslope_pct=-2.0,
            prior_gain_pct=18.0,
        )
        borderline = _build_bull_flag(
            pole_gain_pct=16.0,
            pole_drawdown_pct=5.0,
            flag_pullback_pct=7.5,
            flag_vol_ratio=0.65,
            flag_upslope_pct=0.5,
            prior_gain_pct=6.0,
        )
        t = bull_flag.detect(textbook)
        b = bull_flag.detect(borderline)
        self.assertEqual(len(t), 1)
        self.assertEqual(len(b), 1)
        self.assertGreater(
            t[0].extra["pattern_quality_score"],
            b[0].extra["pattern_quality_score"],
        )

    def test_textbook_grades_at_least_seven(self):
        daily = _build_bull_flag(
            pole_gain_pct=28.0,
            pole_drawdown_pct=1.0,
            flag_pullback_pct=4.0,
            flag_vol_ratio=0.35,
            flag_upslope_pct=-2.0,
            prior_gain_pct=18.0,
            final_close_offset_pct=-0.5,
        )
        results = bull_flag.detect(daily)
        self.assertEqual(len(results), 1)
        self.assertGreaterEqual(results[0].extra["pattern_quality_score"], 7.0)

    def test_quality_breakdown_components_present(self):
        daily = _build_bull_flag()
        results = bull_flag.detect(daily)
        self.assertEqual(len(results), 1)
        components = results[0].extra["pattern_quality_breakdown"]
        expected = {
            "pole_strength", "pole_cleanliness", "flag_pullback",
            "volume_contraction", "flag_direction", "prior_uptrend",
            "breakout_proximity", "stop_tightness",
        }
        self.assertEqual(set(components.keys()), expected)
        self.assertAlmostEqual(
            sum(components.values()),
            results[0].extra["pattern_quality_score"],
            places=1,
        )

    def test_quality_score_capped_at_ten(self):
        daily = _build_bull_flag(
            pole_gain_pct=35.0,
            pole_drawdown_pct=0.5,
            flag_pullback_pct=4.5,
            flag_vol_ratio=0.30,
            flag_upslope_pct=-3.0,
            prior_gain_pct=25.0,
            final_close_offset_pct=+0.5,  # broken out
        )
        results = bull_flag.detect(daily)
        self.assertEqual(len(results), 1)
        self.assertLessEqual(results[0].extra["pattern_quality_score"], 10.0)


if __name__ == "__main__":
    unittest.main()
