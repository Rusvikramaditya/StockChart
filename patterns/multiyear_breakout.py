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
from patterns.utils import clip_confidence, has_ohlcv, local_highs, series


def detect(daily: dict, weekly: dict | None = None) -> list[PatternResult]:
    cfg = settings.MULTIYEAR_BREAKOUT
    min_bars = int(float(cfg["min_years"]) * 52)
    if weekly is None or not has_ohlcv(weekly, min_bars + 4):
        return []

    high = series(weekly, "high")
    low = series(weekly, "low")
    close = series(weekly, "close")
    volume = series(weekly, "volume")

    max_years = max(float(cfg.get("max_years", 3.0)), float(cfg["min_years"]))
    lookback = min(len(close), max(min_bars, int(max_years * 52)))
    high_w = high[-lookback:]
    low_w = low[-lookback:]
    close_w = close[-lookback:]
    volume_w = volume[-lookback:]

    # Resistance pulled from prior bars only so a breakout candle does
    # not become the new resistance. Cross-pattern bug class.
    resistance_window = high_w[:-1]
    if len(resistance_window) < min_bars:
        return []

    candidates = []
    for setup in _resistance_setups(high_w, low_w, close_w, cfg):
        candidate = _build_result(
            setup=setup,
            cfg=cfg,
            low_w=low_w,
            close_w=close_w,
            volume_w=volume_w,
            lookback=lookback,
        )
        if candidate is not None:
            candidates.append(candidate)

    if not candidates:
        return []

    model_rank = {"strict_line": 2, "resistance_zone": 1, "long_high_breakout": 0}
    candidates.sort(
        key=lambda result: (
            model_rank.get(str((result.extra or {}).get("resistance_model", "")), 0),
            float((result.extra or {}).get("pattern_quality_score", 0.0)),
            float(result.confidence),
        ),
        reverse=True,
    )
    return [candidates[0]]


def _resistance_setups(high_w: np.ndarray, low_w: np.ndarray, close_w: np.ndarray, cfg: dict) -> list[dict]:
    resistance_window = high_w[:-1]
    setups = []
    strict = _strict_line_setup(resistance_window, cfg)
    if strict is not None:
        setups.append(strict)
    zone = _resistance_zone_setup(resistance_window, cfg)
    if zone is not None:
        setups.append(zone)
    old_high = _long_high_breakout_setup(high_w, low_w, close_w, cfg)
    if old_high is not None:
        setups.append(old_high)
    return setups


def _strict_line_setup(resistance_window: np.ndarray, cfg: dict) -> dict | None:
    resistance = float(np.max(resistance_window))
    if resistance <= 0:
        return None

    tolerance_pct = float(cfg["resistance_tolerance_pct"])
    touch_offsets = np.where(
        np.abs(resistance_window - resistance) / resistance * 100.0 <= tolerance_pct
    )[0]
    if len(touch_offsets) < int(cfg["min_touches"]):
        return None

    # Touch dispersion guard: max-min span of the touch highs must stay
    # tighter than max_touch_dispersion_pct of resistance.
    touch_highs = resistance_window[touch_offsets]
    touch_dispersion_pct = (
        float((touch_highs.max() - touch_highs.min()) / resistance * 100.0)
        if touch_highs.size
        else 0.0
    )
    if touch_dispersion_pct > float(cfg["max_touch_dispersion_pct"]):
        return None

    touch_spread_frac = _touch_spread_fraction(touch_offsets, resistance_window)
    if touch_spread_frac < float(cfg["min_touch_spread_fraction"]):
        return None

    return {
        "model": "strict_line",
        "resistance": resistance,
        "zone_low": resistance,
        "zone_high": resistance,
        "touch_offsets": touch_offsets,
        "touch_dispersion_pct": touch_dispersion_pct,
        "touch_spread_frac": touch_spread_frac,
    }


def _resistance_zone_setup(resistance_window: np.ndarray, cfg: dict) -> dict | None:
    zone_high = float(np.max(resistance_window))
    if zone_high <= 0:
        return None

    zone_width_pct = float(cfg.get("resistance_zone_width_pct", 4.0))
    zone_low = zone_high * (1.0 - zone_width_pct / 100.0)
    recent_exclusion = max(0, int(cfg.get("recent_touch_exclusion_weeks", 12)))
    historical_limit = max(0, len(resistance_window) - recent_exclusion)
    swing_high_offsets = local_highs(resistance_window, order=2)
    swing_high_offsets = swing_high_offsets[swing_high_offsets < historical_limit]
    touch_offsets = swing_high_offsets[
        (resistance_window[swing_high_offsets] >= zone_low)
        & (resistance_window[swing_high_offsets] <= zone_high)
    ]
    if len(touch_offsets) < int(cfg.get("min_zone_touches", cfg["min_touches"])):
        return None

    touch_highs = resistance_window[touch_offsets]
    touch_range_pct = (
        float((touch_highs.max() - touch_highs.min()) / zone_high * 100.0)
        if touch_highs.size
        else 0.0
    )
    if touch_range_pct > float(cfg.get("max_zone_touch_range_pct", zone_width_pct)):
        return None

    touch_spread_frac = _touch_spread_fraction(touch_offsets, resistance_window)
    if touch_spread_frac < float(cfg["min_touch_spread_fraction"]):
        return None

    return {
        "model": "resistance_zone",
        "resistance": zone_high,
        "zone_low": zone_low,
        "zone_high": zone_high,
        "touch_offsets": touch_offsets,
        "touch_dispersion_pct": touch_range_pct,
        "touch_spread_frac": touch_spread_frac,
    }


def _long_high_breakout_setup(
    high_w: np.ndarray,
    low_w: np.ndarray,
    close_w: np.ndarray,
    cfg: dict,
) -> dict | None:
    resistance_window = high_w[:-1]
    resistance = float(np.max(resistance_window))
    latest_close = float(close_w[-1])
    if resistance <= 0 or latest_close <= resistance:
        return None

    base_weeks = int(cfg.get("long_high_base_weeks", 26))
    if len(close_w) < base_weeks + 2:
        return None

    base_slice = slice(-base_weeks - 1, -1)
    base_low = float(np.min(low_w[base_slice]))
    base_depth_pct = (resistance - base_low) / resistance * 100.0
    if base_depth_pct > float(cfg.get("long_high_max_base_depth_pct", 18.0)):
        return None

    near_high_pct = float(cfg.get("long_high_near_high_pct", 10.0))
    near_high_level = resistance * (1.0 - near_high_pct / 100.0)
    near_high_weeks = int(np.sum(close_w[base_slice] >= near_high_level))
    if near_high_weeks < int(cfg.get("long_high_min_near_high_weeks", 8)):
        return None

    swing_high_offsets = local_highs(resistance_window, order=2)
    touch_offsets = swing_high_offsets[
        np.abs(resistance_window[swing_high_offsets] - resistance) / resistance * 100.0
        <= float(cfg.get("resistance_zone_width_pct", 4.0))
    ]
    if len(touch_offsets) == 0:
        touch_offsets = np.array([int(np.argmax(resistance_window))], dtype=int)

    touch_highs = resistance_window[touch_offsets]
    touch_range_pct = (
        float((touch_highs.max() - touch_highs.min()) / resistance * 100.0)
        if touch_highs.size
        else 0.0
    )
    base_pressure_frac = min(1.0, near_high_weeks / max(1, base_weeks))

    return {
        "model": "long_high_breakout",
        "resistance": resistance,
        "zone_low": near_high_level,
        "zone_high": resistance,
        "touch_offsets": touch_offsets,
        "touch_dispersion_pct": touch_range_pct,
        "touch_spread_frac": base_pressure_frac,
        "base_depth_pct": base_depth_pct,
        "near_high_weeks": near_high_weeks,
    }


def _touch_spread_fraction(touch_offsets: np.ndarray, resistance_window: np.ndarray) -> float:
    window_span = max(1, len(resistance_window) - 1)
    if len(touch_offsets) < 2:
        return 0.0
    return (float(touch_offsets[-1]) - float(touch_offsets[0])) / window_span


def _build_result(
    *,
    setup: dict,
    cfg: dict,
    low_w: np.ndarray,
    close_w: np.ndarray,
    volume_w: np.ndarray,
    lookback: int,
) -> PatternResult | None:
    resistance = float(setup["resistance"])
    latest_close = float(close_w[-1])
    breakout = latest_close > resistance
    if breakout:
        extension_pct = (latest_close - resistance) / resistance * 100.0
        if extension_pct > float(cfg["max_breakout_extension_pct"]):
            return None  # stale, already played out
        distance_to_pivot_pct = 0.0
    else:
        distance_to_pivot_pct = (resistance - latest_close) / resistance * 100.0
        if distance_to_pivot_pct > float(cfg["within_breakout_pct"]):
            return None

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
        return None

    base_low = float(np.min(low_w))
    # Half-base measured target: conservative, matches O'Neil convention
    # for multi-year bases (which have huge depth).
    target = resistance + (resistance - base_low) * 0.5

    # Stop = lowest low over last 12 weeks (3 months). Provides a real
    # invalidation level and limits risk.
    stop_loss = float(np.min(low_w[-12:]))
    if stop_loss <= 0 or resistance <= 0:
        return None
    stop_distance_pct = (resistance - stop_loss) / resistance * 100.0
    if stop_distance_pct > float(cfg["max_stop_distance_pct"]) or stop_distance_pct <= 0:
        return None

    years_at_resistance = round(lookback / 52.0, 2)

    quality = _pattern_quality(
        resistance_model=str(setup["model"]),
        touch_count=len(setup["touch_offsets"]),
        touch_dispersion_pct=float(setup["touch_dispersion_pct"]),
        touch_spread_frac=float(setup["touch_spread_frac"]),
        years_at_resistance=years_at_resistance,
        volume_ratio=volume_ratio,
        volume_surge_required=float(cfg["volume_surge_ratio"]),
        breakout=breakout,
        distance_to_pivot_pct=distance_to_pivot_pct,
        stop_distance_pct=stop_distance_pct,
    )
    pattern_quality_score = quality["total"]

    # Legacy conviction confidence (0-100) used by the scorer's filter blend.
    confidence = 60.0 + min(18.0, len(setup["touch_offsets"]) * 4.0) + min(15.0, volume_ratio * 4.0)
    if breakout:
        confidence += 7.0
    if setup["model"] == "resistance_zone":
        confidence -= 3.0
    elif setup["model"] == "long_high_breakout":
        confidence -= 2.0
    confidence = clip_confidence(confidence)

    model_label = {
        "strict_line": "strict resistance line",
        "resistance_zone": "resistance zone",
        "long_high_breakout": "long high breakout",
    }.get(str(setup["model"]), "resistance setup")
    return PatternResult(
        pattern="Multi-Year Breakout",
        status="BREAKING OUT" if breakout else "PIVOT READY",
        pivot=round(resistance, 2),
        target=round(target, 2),
        stop_loss=round(stop_loss, 2),
        confidence=confidence,
        explanation=(
            f"{len(setup['touch_offsets'])} weekly touches over {years_at_resistance} years near "
            f"{resistance:.2f} using a {model_label} "
            f"(range {float(setup['touch_dispersion_pct']):.2f}%); "
            f"volume {volume_ratio:.2f}x avg; pattern grade {pattern_quality_score:.1f}/10."
        ),
        timeframe="weekly",
        bars_in_pattern=lookback,
        quality_score=confidence,
        extra={
            "resistance_model": setup["model"],
            "resistance_touch_offsets": setup["touch_offsets"].tolist(),
            "resistance_touch_indices": setup["touch_offsets"].tolist(),
            "resistance_zone_low": round(float(setup["zone_low"]), 2),
            "resistance_zone_high": round(float(setup["zone_high"]), 2),
            "base_depth_pct": None if "base_depth_pct" not in setup else round(float(setup["base_depth_pct"]), 2),
            "near_high_weeks": setup.get("near_high_weeks"),
            "touch_dispersion_pct": round(float(setup["touch_dispersion_pct"]), 3),
            "touch_spread_fraction": round(float(setup["touch_spread_frac"]), 3),
            "volume_ratio": round(volume_ratio, 2),
            "years": years_at_resistance,
            "stop_distance_pct": round(stop_distance_pct, 2),
            "pattern_quality_score": pattern_quality_score,
            "pattern_quality_breakdown": quality["components"],
        },
    )


def _pattern_quality(
    *,
    resistance_model: str,
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
    else:
        touch_pts = 1.0

    # 2. Touch flatness. A practical resistance zone gets a wider allowance
    # than an exact textbook line, but still loses points as the band widens.
    if resistance_model == "resistance_zone":
        if touch_dispersion_pct <= 2.0:
            flat_pts = 1.2
        elif touch_dispersion_pct <= 3.0:
            flat_pts = 0.9
        elif touch_dispersion_pct <= 4.0:
            flat_pts = 0.6
        else:
            flat_pts = 0.2
    elif touch_dispersion_pct <= 0.4:
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
