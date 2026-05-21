"""Volatility Contraction Pattern detector (Minervini-style).

Detection is strict: real swing-extrema based contractions, monotonically
tightening (each leg <= tightening_ratio_max of prior), highs clustering near
pivot, minimum duration, and final tightness <= max_final_tightness_pct.

Each detected pattern carries a 0-10 pattern_quality_score in `extra` so the
UI grades pattern cleanliness independently of the conviction score.
"""

from __future__ import annotations

import numpy as np

from config import settings
from patterns.base import PatternResult
from patterns.utils import (
    clip_confidence,
    has_ohlcv,
    is_stage2,
    local_highs,
    local_lows,
    series,
)


def _alternating_swings(
    high: np.ndarray, low: np.ndarray, order: int
) -> list[tuple[str, int, float]]:
    """Return alternating swing extrema as (kind, idx, value) tuples.

    Consecutive same-type extrema (two highs in a row, two lows in a row) are
    deduped: the more extreme value wins (max for H, min for L). Output is
    strictly alternating H/L (or L/H) ordered by index.
    """
    highs_idx = local_highs(high, order)
    lows_idx = local_lows(low, order)
    events: list[tuple[str, int, float]] = []
    for idx in highs_idx:
        events.append(("H", int(idx), float(high[idx])))
    for idx in lows_idx:
        events.append(("L", int(idx), float(low[idx])))
    events.sort(key=lambda e: e[1])

    swings: list[tuple[str, int, float]] = []
    for ev in events:
        if not swings:
            swings.append(ev)
            continue
        prev = swings[-1]
        if ev[0] == prev[0]:
            # Same kind in a row -> keep the more extreme
            if ev[0] == "H" and ev[2] > prev[2]:
                swings[-1] = ev
            elif ev[0] == "L" and ev[2] < prev[2]:
                swings[-1] = ev
        else:
            swings.append(ev)
    return swings


# Sub-1% pullbacks are within typical candle noise: at real-money scale they
# are not contractions, just micro-fluctuations. Filtering them removes
# spurious swings from flat consolidation tails without losing real legs
# (the strictest textbook final pullback is still ~2-3%).
_MIN_CONTRACTION_DEPTH_PCT = 1.0


def _build_contractions(
    swings: list[tuple[str, int, float]]
) -> list[dict]:
    """From an alternating swing sequence, build H->L contraction records.

    Each contraction = (high preceded a low). Recovery legs (L->H) are
    implicit between contractions. Micro-swings below the noise floor are
    skipped.
    """
    contractions: list[dict] = []
    for i in range(len(swings) - 1):
        a, b = swings[i], swings[i + 1]
        if a[0] == "H" and b[0] == "L":
            high_val = a[2]
            low_val = b[2]
            if high_val <= 0:
                continue
            depth_pct = (high_val - low_val) / high_val * 100.0
            if depth_pct < _MIN_CONTRACTION_DEPTH_PCT:
                continue
            contractions.append(
                {
                    "high_idx": a[1],
                    "high": high_val,
                    "low_idx": b[1],
                    "low": low_val,
                    "depth_pct": depth_pct,
                }
            )
    return contractions


def detect(daily: dict, weekly: dict | None = None) -> list[PatternResult]:
    cfg = settings.VCP
    lookback = int(cfg["lookback_bars"])
    if not has_ohlcv(daily, max(220, lookback)):
        return []

    high_full = series(daily, "high")
    low_full = series(daily, "low")
    close_full = series(daily, "close")
    volume_full = series(daily, "volume")
    if not is_stage2(close_full, settings.STAGE2):
        return []

    high = high_full[-lookback:]
    low = low_full[-lookback:]
    close = close_full[-lookback:]
    volume = volume_full[-lookback:]

    # 1. Extract alternating swings within the lookback window.
    swings = _alternating_swings(high, low, int(cfg["swing_order"]))
    contractions = _build_contractions(swings)

    # 2. Count gate.
    min_c = int(cfg["min_contractions"])
    max_c = int(cfg["max_contractions"])
    if len(contractions) < min_c:
        return []
    # If more contractions than max, keep the most recent max_c (the latest
    # contractions define the active pattern; older ones are pre-pattern noise).
    if len(contractions) > max_c:
        contractions = contractions[-max_c:]

    depths = [c["depth_pct"] for c in contractions]

    # 3. First leg sanity: too deep -> not a contraction pattern.
    if depths[0] > float(cfg["max_first_contraction_pct"]):
        return []

    # 4. Final and prior tightness ceilings.
    if depths[-1] > float(cfg["max_final_tightness_pct"]):
        return []
    if len(depths) >= 2 and depths[-2] > float(cfg["max_prior_tightness_pct"]):
        return []

    # 5. Tightening: each contraction <= ratio_max * prior.
    ratio_max = float(cfg["tightening_ratio_max"])
    tightening_ratios: list[float] = []
    for i in range(1, len(depths)):
        prior = depths[i - 1]
        if prior <= 0:
            return []
        ratio = depths[i] / prior
        tightening_ratios.append(ratio)
        if ratio > ratio_max:
            return []

    # 6. Highs must cluster near the pivot (not a downtrend).
    highs_arr = np.array([c["high"] for c in contractions], dtype=float)
    pivot_high = float(highs_arr.max())
    high_disp_pct = float((highs_arr.max() - highs_arr.min()) / pivot_high * 100.0)
    if high_disp_pct > float(cfg["max_high_dispersion_pct"]):
        return []

    # 7. Duration: from first swing high to last swing low.
    duration_bars = contractions[-1]["low_idx"] - contractions[0]["high_idx"]
    if duration_bars < int(cfg["min_pattern_bars"]):
        return []

    # 8. Overall volume declining (gate).
    vol_first = float(np.mean(volume[:20])) if len(volume) >= 20 else float(np.mean(volume))
    vol_last = float(np.mean(volume[-20:])) if len(volume) >= 20 else float(np.mean(volume))
    volume_declining = vol_last < vol_first
    if bool(cfg["volume_declining"]) and not volume_declining:
        return []

    # 9. Per-leg volume dryup count (graded, not gated).
    leg_vols: list[float] = []
    for c in contractions:
        lo, hi = c["high_idx"], c["low_idx"] + 1
        if hi > lo:
            leg_vols.append(float(np.mean(volume[lo:hi])))
    per_leg_declining = sum(
        1 for i in range(1, len(leg_vols)) if leg_vols[i] < leg_vols[i - 1]
    )

    # 10. Pivot = highest swing high among contractions. Breakout reachable
    # because pivot is computed from prior swings only (not today's bar).
    pivot = pivot_high
    latest_close = float(close[-1])
    distance_to_pivot_pct = (pivot - latest_close) / pivot * 100.0
    breakout = latest_close > pivot
    if breakout and (latest_close - pivot) / pivot * 100.0 > float(
        cfg["max_breakout_extension_pct"]
    ):
        return []  # stale, already played out
    if not breakout and distance_to_pivot_pct > float(cfg["within_breakout_pct"]):
        return []

    # 11. Trade levels.
    final_low = float(contractions[-1]["low"])
    stop_loss = final_low
    risk = pivot - stop_loss
    target = pivot + risk * 2.0 if risk > 0 else pivot * 1.10

    # 12. Conviction (legacy filter score) and quality grade.
    confidence = 55.0 + min(15.0, len(contractions) * 3.0)
    confidence += max(0.0, 10.0 - depths[-1] * 1.5)  # reward tight final
    if volume_declining:
        confidence += 5.0
    if breakout:
        confidence += 8.0
    confidence = clip_confidence(confidence)

    quality_breakdown = _pattern_quality(
        depths=depths,
        tightening_ratios=tightening_ratios,
        per_leg_declining=per_leg_declining,
        leg_count=len(contractions),
        breakout=breakout,
        distance_to_pivot_pct=distance_to_pivot_pct,
    )
    pattern_quality_score = quality_breakdown["total"]

    explanation = (
        f"{len(contractions)} contractions tightening "
        + " -> ".join(f"{d:.1f}%" for d in depths)
        + f"; high dispersion {high_disp_pct:.1f}%; "
        f"pattern grade {pattern_quality_score:.1f}/10."
    )

    return [
        PatternResult(
            pattern="VCP",
            status="BREAKING OUT" if breakout else "PIVOT READY",
            pivot=round(pivot, 2),
            target=round(target, 2),
            stop_loss=round(stop_loss, 2),
            confidence=confidence,
            explanation=explanation,
            timeframe="daily",
            bars_in_pattern=int(duration_bars),
            quality_score=confidence,
            extra={
                "contractions_pct": [round(d, 2) for d in depths],
                "tightening_ratios": [round(r, 3) for r in tightening_ratios],
                "high_dispersion_pct": round(high_disp_pct, 3),
                "duration_bars": int(duration_bars),
                "volume_declining": volume_declining,
                "per_leg_volume_declining": per_leg_declining,
                "stage2": True,
                "pattern_quality_score": pattern_quality_score,
                "pattern_quality_breakdown": quality_breakdown["components"],
            },
        )
    ]


def _pattern_quality(
    *,
    depths: list[float],
    tightening_ratios: list[float],
    per_leg_declining: int,
    leg_count: int,
    breakout: bool,
    distance_to_pivot_pct: float,
) -> dict:
    """Return 0-10 VCP pattern grade with per-component breakdown.

    Components (max points):
        contraction_count       2.0  more legs = more textbook (3=1, 4=1.5, 5+=2)
        tightening_progression  2.5  mean tightening ratio (smaller = better funnel)
        final_tightness         2.0  final pullback % (tighter = better pivot test)
        volume_dryup            1.5  count of per-leg volume declines
        pivot_proximity         1.5  breakout or distance to pivot
        base_depth              0.5  first leg shallow = healthier base
    """
    # 1. Contraction count
    if leg_count >= 5:
        count_pts = 2.0
    elif leg_count == 4:
        count_pts = 1.5
    else:  # 3 (detector enforces >=3)
        count_pts = 1.0

    # 2. Tightening progression - mean of contraction[i]/contraction[i-1]
    tighten_pts = 0.0
    if tightening_ratios:
        mean_ratio = float(np.mean(tightening_ratios))
        if mean_ratio <= 0.5:
            tighten_pts = 2.5
        elif mean_ratio <= 0.65:
            tighten_pts = 1.8
        elif mean_ratio <= 0.80:
            tighten_pts = 1.0
        else:
            tighten_pts = 0.3
    else:
        # Single contraction shouldn't reach here (detector requires >=3),
        # but guard anyway.
        tighten_pts = 0.0

    # 3. Final tightness - final pullback %
    final = depths[-1]
    if final <= 3.0:
        final_pts = 2.0
    elif final <= 5.0:
        final_pts = 1.5
    elif final <= 6.0:
        final_pts = 1.0
    else:
        final_pts = 0.5  # detector caps at max_final, but keep graded fallback

    # 4. Per-leg volume dryup
    max_drops = max(1, leg_count - 1)
    dryup_ratio = per_leg_declining / max_drops
    if dryup_ratio >= 0.99:
        dryup_pts = 1.5
    elif dryup_ratio >= 0.67:
        dryup_pts = 1.0
    elif dryup_ratio >= 0.34:
        dryup_pts = 0.5
    else:
        dryup_pts = 0.0

    # 5. Pivot proximity
    if breakout:
        proximity_pts = 1.5
    elif distance_to_pivot_pct <= 1.0:
        proximity_pts = 1.2
    elif distance_to_pivot_pct <= 2.5:
        proximity_pts = 0.8
    else:
        proximity_pts = 0.4

    # 6. Base depth (first contraction)
    first = depths[0]
    if first <= 15.0:
        base_pts = 0.5
    elif first <= 25.0:
        base_pts = 0.4
    elif first <= 35.0:
        base_pts = 0.2
    else:
        base_pts = 0.0  # detector rejects > 35, but keep graded fallback

    total = count_pts + tighten_pts + final_pts + dryup_pts + proximity_pts + base_pts
    total = round(max(0.0, min(10.0, total)), 1)

    return {
        "total": total,
        "components": {
            "contraction_count": round(count_pts, 2),
            "tightening_progression": round(tighten_pts, 2),
            "final_tightness": round(final_pts, 2),
            "volume_dryup": round(dryup_pts, 2),
            "pivot_proximity": round(proximity_pts, 2),
            "base_depth": round(base_pts, 2),
        },
    }
