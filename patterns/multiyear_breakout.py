"""Multi-Year Breakout detector (weekly timeframe).

Strict textbook geometry: a horizontal resistance line tested >=3 times
over the multi-year window, with touches spread across at least half the
window (not clustered in the last quarter), tight touch dispersion, and
either the latest close just breaking through with volume surge
(BREAKING OUT) or sitting just below (PIVOT READY).

The legacy implementation gated PIVOT READY on volume surge as well,
which by construction can never fire pre-breakout. That gate is now
applied only to BREAKING OUT; pre-breakout candidates surface and the
absence of surge is reflected in the quality grade instead.

Each detected pattern carries a 0-10 ``pattern_quality_score`` so the
dashboard can grade textbook-ness independently of the conviction blend.
"""

from __future__ import annotations

import numpy as np

from config import settings
from patterns.base import PatternResult
from patterns.utils import clip_confidence, has_ohlcv, series


def detect(daily: dict, weekly: dict | None = None) -> list[PatternResult]:
    cfg = settings.MULTIYEAR_BREAKOUT
    min_bars = int(float(cfg["min_years"]) * 52)
    if weekly is None or not has_ohlcv(weekly, min_bars + 4):
        return []

    high = series(weekly, "high")
    low = series(weekly, "low")
    close = series(weekly, "close")
    volume = series(weekly, "volume")

    lookback = min(len(close), max(min_bars, 156))
    high_w = high[-lookback:]
    low_w = low[-lookback:]
    close_w = close[-lookback:]
    volume_w = volume[-lookback:]

    # Resistance pulled from prior bars only so a breakout candle does
    # not become the new resistance. Cross-pattern bug class.
    resistance_window = high_w[:-1]
    if len(resistance_window) < min_bars:
        return []
    resistance = float(np.max(resistance_window))
    if resistance <= 0:
        return []

    tolerance_pct = float(cfg["resistance_tolerance_pct"])
    touch_offsets = np.where(
        np.abs(resistance_window - resistance) / resistance * 100.0 <= tolerance_pct
    )[0]
    if len(touch_offsets) < int(cfg["min_touches"]):
        return []

    # Touch dispersion guard: max-min span of the touch highs must stay
    # tighter than max_touch_dispersion_pct of resistance.
    touch_highs = resistance_window[touch_offsets]
    touch_dispersion_pct = (
        float((touch_highs.max() - touch_highs.min()) / resistance * 100.0)
        if touch_highs.size
        else 0.0
    )
    if touch_dispersion_pct > float(cfg["max_touch_dispersion_pct"]):
        return []

    # Touch spread guard: touches must span at least
    # min_touch_spread_fraction of the lookback window.
    window_span = max(1, len(resistance_window) - 1)
    touch_spread_frac = (
        (float(touch_offsets[-1]) - float(touch_offsets[0])) / window_span
        if len(touch_offsets) >= 2
        else 0.0
    )
    if touch_spread_frac < float(cfg["min_touch_spread_fraction"]):
        return []

    latest_close = float(close_w[-1])
    breakout = latest_close > resistance
    if breakout:
        extension_pct = (latest_close - resistance) / resistance * 100.0
        if extension_pct > float(cfg["max_breakout_extension_pct"]):
            return []  # stale, already played out
        distance_to_pivot_pct = 0.0
    else:
        distance_to_pivot_pct = (resistance - latest_close) / resistance * 100.0
        if distance_to_pivot_pct > float(cfg["within_breakout_pct"]):
            return []

    # Volume surge measured against prior 50 weeks (excludes current bar).
    if len(volume_w) >= 51:
        avg_volume = float(np.mean(volume_w[-51:-1]))
    elif len(volume_w) >= 2:
        avg_volume = float(np.mean(volume_w[:-1]))
    else:
        avg_volume = 0.0
    volume_ratio = float(volume_w[-1] / avg_volume) if avg_volume > 0 else 0.0

    # ONLY require volume surge for BREAKING OUT. Pre-breakout candidates
    # have not yet generated surge by definition; surge becomes a grade
    # component (not a gate). This fixes the bug where PIVOT READY status
    # was unreachable for multi-year breakouts.
    if breakout and volume_ratio < float(cfg["volume_surge_ratio"]):
        return []

    base_low = float(np.min(low_w))
    # Half-base measured target: conservative, matches O'Neil convention
    # for multi-year bases (which have huge depth).
    target = resistance + (resistance - base_low) * 0.5

    # Stop = lowest low over last 12 weeks (3 months). Provides a real
    # invalidation level and limits risk.
    stop_loss = float(np.min(low_w[-12:]))
    if stop_loss <= 0 or resistance <= 0:
        return []
    stop_distance_pct = (resistance - stop_loss) / resistance * 100.0
    if stop_distance_pct > float(cfg["max_stop_distance_pct"]) or stop_distance_pct <= 0:
        return []

    years_at_resistance = round(lookback / 52.0, 2)

    quality = _pattern_quality(
        touch_count=len(touch_offsets),
        touch_dispersion_pct=touch_dispersion_pct,
        touch_spread_frac=touch_spread_frac,
        years_at_resistance=years_at_resistance,
        volume_ratio=volume_ratio,
        volume_surge_required=float(cfg["volume_surge_ratio"]),
        breakout=breakout,
        distance_to_pivot_pct=distance_to_pivot_pct,
        stop_distance_pct=stop_distance_pct,
    )
    pattern_quality_score = quality["total"]

    # Legacy conviction confidence (0-100) used by the scorer's filter blend.
    confidence = 60.0 + min(18.0, len(touch_offsets) * 4.0) + min(15.0, volume_ratio * 4.0)
    if breakout:
        confidence += 7.0
    confidence = clip_confidence(confidence)

    return [
        PatternResult(
            pattern="Multi-Year Breakout",
            status="BREAKING OUT" if breakout else "PIVOT READY",
            pivot=round(resistance, 2),
            target=round(target, 2),
            stop_loss=round(stop_loss, 2),
            confidence=confidence,
            explanation=(
                f"{len(touch_offsets)} weekly touches over {years_at_resistance} years near "
                f"{resistance:.2f} (dispersion {touch_dispersion_pct:.2f}%); "
                f"volume {volume_ratio:.2f}x avg; pattern grade {pattern_quality_score:.1f}/10."
            ),
            timeframe="weekly",
            bars_in_pattern=lookback,
            quality_score=confidence,
            extra={
                "resistance_touch_offsets": touch_offsets.tolist(),
                "touch_dispersion_pct": round(touch_dispersion_pct, 3),
                "touch_spread_fraction": round(touch_spread_frac, 3),
                "volume_ratio": round(volume_ratio, 2),
                "years": years_at_resistance,
                "stop_distance_pct": round(stop_distance_pct, 2),
                "pattern_quality_score": pattern_quality_score,
                "pattern_quality_breakdown": quality["components"],
            },
        )
    ]


def _pattern_quality(
    *,
    touch_count: int,
    touch_dispersion_pct: float,
    touch_spread_frac: float,
    years_at_resistance: float,
    volume_ratio: float,
    volume_surge_required: float,
    breakout: bool,
    distance_to_pivot_pct: float,
    stop_distance_pct: float,
) -> dict:
    """Return 0-10 grade with per-component breakdown.

    Components (max points):
        touch_count         2.0   3 = 1.0, 4 = 1.5, 5+ = 2.0
        touch_flatness      1.5   tighter dispersion = higher
        touch_spread        1.5   touches across the whole window = full
        duration            1.0   more years at resistance = stronger
        volume_surge        2.0   x avg vol on current bar
        breakout_proximity  1.0   broken out / near pivot
        stop_tightness      1.0   stop close to pivot = lower risk
    """
    # 1. Touch count
    if touch_count >= 5:
        touch_pts = 2.0
    elif touch_count == 4:
        touch_pts = 1.5
    else:  # >= 3 enforced by detector
        touch_pts = 1.0

    # 2. Touch flatness
    if touch_dispersion_pct <= 0.4:
        flat_pts = 1.5
    elif touch_dispersion_pct <= 0.7:
        flat_pts = 1.0
    elif touch_dispersion_pct <= 1.0:
        flat_pts = 0.5
    else:
        flat_pts = 0.2

    # 3. Touch spread across the window
    if touch_spread_frac >= 0.8:
        spread_pts = 1.5
    elif touch_spread_frac >= 0.65:
        spread_pts = 1.0
    elif touch_spread_frac >= 0.5:
        spread_pts = 0.5
    else:
        spread_pts = 0.0

    # 4. Duration at resistance
    if years_at_resistance >= 4.0:
        duration_pts = 1.0
    elif years_at_resistance >= 3.0:
        duration_pts = 0.7
    elif years_at_resistance >= 2.0:
        duration_pts = 0.5
    else:
        duration_pts = 0.2

    # 5. Volume surge
    if volume_ratio >= 2.0:
        vol_pts = 2.0
    elif volume_ratio >= 1.6:
        vol_pts = 1.5
    elif volume_ratio >= volume_surge_required:
        vol_pts = 1.0
    elif volume_ratio >= 1.0:
        vol_pts = 0.5
    else:
        vol_pts = 0.0

    # 6. Breakout proximity
    if breakout:
        prox_pts = 1.0
    elif distance_to_pivot_pct <= 1.0:
        prox_pts = 0.7
    elif distance_to_pivot_pct <= 2.0:
        prox_pts = 0.4
    else:
        prox_pts = 0.2

    # 7. Stop tightness
    if stop_distance_pct <= 5.0:
        stop_pts = 1.0
    elif stop_distance_pct <= 8.0:
        stop_pts = 0.7
    elif stop_distance_pct <= 12.0:
        stop_pts = 0.4
    else:
        stop_pts = 0.2

    total = touch_pts + flat_pts + spread_pts + duration_pts + vol_pts + prox_pts + stop_pts
    total = round(max(0.0, min(10.0, total)), 1)
    return {
        "total": total,
        "components": {
            "touch_count": round(touch_pts, 2),
            "touch_flatness": round(flat_pts, 2),
            "touch_spread": round(spread_pts, 2),
            "duration": round(duration_pts, 2),
            "volume_surge": round(vol_pts, 2),
            "breakout_proximity": round(prox_pts, 2),
            "stop_tightness": round(stop_pts, 2),
        },
    }
