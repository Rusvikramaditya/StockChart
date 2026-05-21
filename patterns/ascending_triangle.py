"""Ascending Triangle detector.

Detection is strict (textbook only): >=3 touches at flat resistance, >=3 rising
lows, low touch dispersion. Each detected pattern also carries a 0-10
pattern_quality_score in `extra` so the UI can grade pattern cleanliness
independently from the conviction score that mixes filters + multi-tf.
"""

from __future__ import annotations

import numpy as np

from config import settings
from patterns.base import PatternResult
from patterns.utils import clip_confidence, has_ohlcv, local_lows, series


def detect(daily: dict, weekly: dict | None = None) -> list[PatternResult]:
    cfg = settings.ASCENDING_TRIANGLE
    lookback = int(cfg["lookback_bars"])
    if not has_ohlcv(daily, lookback):
        return []

    high = series(daily, "high")[-lookback:]
    low = series(daily, "low")[-lookback:]
    close = series(daily, "close")[-lookback:]
    volume = series(daily, "volume")[-lookback:]
    # Resistance must be the pre-existing pattern high, not include the
    # current bar. Otherwise a breakout candle becomes the new resistance
    # and `close > resistance` is unreachable (close <= high by definition).
    resistance = float(np.max(high[:-1])) if len(high) > 1 else 0.0
    if resistance <= 0:
        return []

    tolerance_pct = float(cfg["resistance_tolerance_pct"])
    touch_mask = np.abs(high[:-1] - resistance) / resistance * 100.0 <= tolerance_pct
    touch_idx = np.flatnonzero(touch_mask)
    if len(touch_idx) < int(cfg["min_resistance_touches"]):
        return []

    # Touch range guard: max-min span of touches must be tight, not a wide
    # zone. Catches "resistance zones" that look like clusters under tolerance
    # but actually span 2%+ in price.
    touch_highs = high[touch_idx]
    touch_range_pct = float((touch_highs.max() - touch_highs.min()) / resistance * 100.0)
    max_range = float(cfg.get("max_touch_range_pct", 1.0))
    if touch_range_pct > max_range:
        return []
    # Stddev still useful as a quality input (cleaner pattern = tighter std).
    dispersion_pct = float(np.std(touch_highs) / resistance * 100.0)

    lows_idx = local_lows(low, int(cfg["argrelextrema_order"]))
    # Filter out spurious "lows" sitting near resistance — flat consolidation
    # regions can register as local_lows under np.less_equal. True triangle
    # base lows are distinctly below resistance.
    min_base_gap_pct = float(cfg.get("min_low_gap_below_resistance_pct", 1.5))
    if len(lows_idx):
        below_resistance = (resistance - low[lows_idx]) / resistance * 100.0 >= min_base_gap_pct
        lows_idx = lows_idx[below_resistance]
    min_rising = int(cfg["min_rising_lows"])
    if len(lows_idx) < min_rising:
        return []
    recent_lows = lows_idx[-max(4, min_rising):]
    low_values = low[recent_lows]
    rising_pairs = sum(
        float(low_values[i]) > float(low_values[i - 1])
        for i in range(1, len(low_values))
    )
    # min_rising lows means rising_pairs >= min_rising - 1 monotonic ups
    if rising_pairs < min_rising - 1:
        return []

    latest_close = float(close[-1])
    distance_to_pivot = (resistance - latest_close) / resistance * 100.0
    breakout = latest_close > resistance
    if not breakout and distance_to_pivot > float(cfg["within_breakout_pct"]):
        return []
    max_ext = float(cfg.get("max_breakout_extension_pct", 8.0))
    if breakout and (latest_close - resistance) / resistance * 100.0 > max_ext:
        return []  # pattern already played out

    base_low = float(np.min(low_values))
    target = resistance + max(resistance - base_low, 0.0)
    stop_loss = float(low_values[-1])
    avg_vol = float(np.mean(volume[-50:])) if len(volume) >= 50 else float(np.mean(volume))
    volume_ratio = float(volume[-1] / avg_vol) if avg_vol > 0 else 0.0

    # Conviction confidence (legacy: used by scorer alongside filters).
    confidence = 55.0 + min(15.0, len(touch_idx) * 3.0) + min(15.0, rising_pairs * 7.5)
    if breakout:
        confidence += 10.0
    if volume_ratio >= 1.2:
        confidence += 5.0
    confidence = clip_confidence(confidence)

    # Pattern quality (0-10): independent grade of textbook-ness.
    quality_breakdown = _pattern_quality(
        touch_count=len(touch_idx),
        dispersion_pct=dispersion_pct,
        resistance=resistance,
        rising_lows_count=len(recent_lows),
        rising_pairs=rising_pairs,
        low_values=low_values,
        volume=volume,
        breakout=breakout,
        distance_to_pivot_pct=distance_to_pivot,
    )
    pattern_quality_score = quality_breakdown["total"]

    return [
        PatternResult(
            pattern="Ascending Triangle",
            status="BREAKING OUT" if breakout else "PIVOT READY",
            pivot=round(resistance, 2),
            target=round(target, 2),
            stop_loss=round(stop_loss, 2),
            confidence=confidence,
            explanation=(
                f"{len(touch_idx)} resistance touches near {resistance:.2f} "
                f"(dispersion {dispersion_pct:.2f}%); "
                f"{rising_pairs + 1} rising-low points; "
                f"pattern grade {pattern_quality_score:.1f}/10."
            ),
            timeframe="daily",
            bars_in_pattern=lookback,
            quality_score=confidence,
            extra={
                "touch_indices": touch_idx.tolist(),
                "low_indices": recent_lows.tolist(),
                "resistance_tolerance_pct": tolerance_pct,
                "touch_dispersion_pct": round(dispersion_pct, 3),
                "touch_range_pct": round(touch_range_pct, 3),
                "volume_ratio": round(volume_ratio, 2),
                "pattern_quality_score": pattern_quality_score,
                "pattern_quality_breakdown": quality_breakdown["components"],
            },
        )
    ]


def _pattern_quality(
    *,
    touch_count: int,
    dispersion_pct: float,
    resistance: float,
    rising_lows_count: int,
    rising_pairs: int,
    low_values: np.ndarray,
    volume: np.ndarray,
    breakout: bool,
    distance_to_pivot_pct: float,
) -> dict:
    """Return 0-10 pattern grade with per-component breakdown.

    Components (max points):
        touches            2.0   how many resistance touches (3=1, 4=1.5, 5+=2)
        touch_flatness     2.0   stddev of touch highs / resistance
        rising_lows        2.0   count of higher lows (3=1, 4+=2)
        slope_steadiness   1.0   R^2 of linear fit on rising lows
        volume_contraction 1.0   second-half avg vol vs first-half (lower = better)
        breakout_proximity 2.0   already broken out, or how close to pivot
    """
    # 1. Touches
    if touch_count >= 5:
        touches_pts = 2.0
    elif touch_count == 4:
        touches_pts = 1.5
    else:  # 3 (detector already enforces >=3)
        touches_pts = 1.0

    # 2. Touch flatness — dispersion is std/resistance %
    if dispersion_pct <= 0.3:
        flatness_pts = 2.0
    elif dispersion_pct <= 0.5:
        flatness_pts = 1.5
    elif dispersion_pct <= 0.7:
        flatness_pts = 1.0
    else:
        flatness_pts = 0.5

    # 3. Rising lows count
    if rising_lows_count >= 4:
        lows_pts = 2.0
    elif rising_lows_count == 3:
        lows_pts = 1.0
    else:
        lows_pts = 0.0

    # 4. Slope steadiness — R^2 of linear fit on low_values
    slope_pts = 0.0
    if len(low_values) >= 3:
        x = np.arange(len(low_values), dtype=float)
        y = np.asarray(low_values, dtype=float)
        # Linear regression
        slope, intercept = np.polyfit(x, y, 1)
        y_pred = slope * x + intercept
        ss_res = float(np.sum((y - y_pred) ** 2))
        ss_tot = float(np.sum((y - np.mean(y)) ** 2))
        r_squared = 1.0 - ss_res / ss_tot if ss_tot > 1e-9 else 0.0
        # Only reward if slope is positive (rising)
        if slope > 0:
            if r_squared >= 0.85:
                slope_pts = 1.0
            elif r_squared >= 0.65:
                slope_pts = 0.6
            else:
                slope_pts = 0.3

    # 5. Volume contraction during pattern
    vol_pts = 0.0
    if len(volume) >= 10:
        half = len(volume) // 2
        first_half_avg = float(np.mean(volume[:half]))
        second_half_avg = float(np.mean(volume[half:]))
        if first_half_avg > 0:
            ratio = second_half_avg / first_half_avg
            if ratio <= 0.8:
                vol_pts = 1.0
            elif ratio <= 1.0:
                vol_pts = 0.5
            else:
                vol_pts = 0.0

    # 6. Breakout / proximity to pivot
    if breakout:
        proximity_pts = 2.0
    elif distance_to_pivot_pct <= 1.0:
        proximity_pts = 1.5
    elif distance_to_pivot_pct <= 2.5:
        proximity_pts = 1.0
    else:
        proximity_pts = 0.5

    total = touches_pts + flatness_pts + lows_pts + slope_pts + vol_pts + proximity_pts
    total = round(max(0.0, min(10.0, total)), 1)

    return {
        "total": total,
        "components": {
            "touches": round(touches_pts, 2),
            "touch_flatness": round(flatness_pts, 2),
            "rising_lows": round(lows_pts, 2),
            "slope_steadiness": round(slope_pts, 2),
            "volume_contraction": round(vol_pts, 2),
            "breakout_proximity": round(proximity_pts, 2),
        },
    }
