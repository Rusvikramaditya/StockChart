"""Cup & Handle detector.

Detection is strict: textbook cup & handle requires a real handle (controlled
pullback from right rim, <=33% retrace, handle high near pivot), tight rims,
and a tradable stop distance (<=10% from pivot). Each detected pattern also
carries a 0-10 pattern_quality_score so the UI can grade textbook-ness.
"""

from __future__ import annotations

import numpy as np

from config import settings
from patterns.base import PatternResult
from patterns.utils import clip_confidence, has_ohlcv, pct_distance, series


def detect(daily: dict, weekly: dict | None = None) -> list[PatternResult]:
    results = []
    daily_hit = _detect_one(daily, "daily")
    if daily_hit:
        results.append(daily_hit)
    if weekly:
        weekly_hit = _detect_one(weekly, "weekly")
        if weekly_hit:
            results.append(weekly_hit)
    return results


def _detect_one(data: dict, timeframe: str) -> PatternResult | None:
    cfg = settings.CUP_HANDLE
    min_bars = int(cfg["min_bars"])
    if not has_ohlcv(data, min_bars):
        return None

    high = series(data, "high")
    low = series(data, "low")
    close = series(data, "close")
    volume = series(data, "volume")
    n = len(close)
    window_len = min(int(cfg["max_bars"]), n)
    if window_len < min_bars:
        return None

    handle_len = max(8, min(40, window_len // 8))
    if window_len - handle_len < min_bars:
        return None

    high_w = high[-window_len:]
    low_w = low[-window_len:]
    close_w = close[-window_len:]
    vol_w = volume[-window_len:]
    cup_end = window_len - handle_len
    cup_high = high_w[:cup_end]
    cup_low = low_w[:cup_end]

    left_slice_end = max(5, cup_end // 3)
    right_slice_start = max(left_slice_end, cup_end - max(10, cup_end // 3))
    left_idx = int(np.argmax(cup_high[:left_slice_end]))
    right_idx = int(right_slice_start + np.argmax(cup_high[right_slice_start:cup_end]))
    if right_idx <= left_idx + 10:
        return None

    trough_rel = int(np.argmin(cup_low[left_idx:right_idx + 1]))
    trough_idx = left_idx + trough_rel
    left_rim = float(cup_high[left_idx])
    right_rim = float(cup_high[right_idx])
    trough = float(cup_low[trough_idx])
    pivot = max(left_rim, right_rim)
    rim_avg = (left_rim + right_rim) / 2.0
    if rim_avg <= 0 or trough <= 0:
        return None

    depth_pct = (rim_avg - trough) / rim_avg * 100.0
    if not cfg["min_depth_pct"] <= depth_pct <= cfg["max_depth_pct"]:
        return None
    rim_distance = pct_distance(left_rim, right_rim)
    if rim_distance > cfg["rim_tolerance_pct"]:
        return None

    handle_slice_high = high_w[cup_end:]
    handle_slice_low = low_w[cup_end:]
    handle_high = float(np.max(handle_slice_high))
    handle_low = float(np.min(handle_slice_low))
    handle_depth = pivot - handle_low
    cup_depth = pivot - trough
    if cup_depth <= 0:
        return None

    handle_retrace_pct = handle_depth / cup_depth * 100.0
    handle_floor = trough + cup_depth * (2.0 / 3.0)
    if handle_retrace_pct > cfg["handle_max_retrace_pct"] or handle_low < handle_floor:
        return None

    # Handle must show a real pullback (not flat drift) and test the pivot
    # area (not just sit far below).
    handle_pullback_pct = (pivot - handle_low) / pivot * 100.0
    min_pullback = float(cfg.get("handle_min_pullback_pct", 2.0))
    if handle_pullback_pct < min_pullback:
        return None
    handle_high_gap_pct = (pivot - handle_high) / pivot * 100.0
    max_gap = float(cfg.get("handle_high_near_pivot_pct", 5.0))
    if handle_high_gap_pct > max_gap:
        return None

    latest_close = float(close_w[-1])
    breakout = latest_close > pivot
    if not breakout and (pivot - latest_close) / pivot * 100.0 > 5.0:
        return None
    max_ext = float(cfg.get("max_breakout_extension_pct", 8.0))
    if breakout and (latest_close - pivot) / pivot * 100.0 > max_ext:
        return None  # pattern already played out

    stop_loss = handle_low
    # Reject if stop is too wide for a tradable swing setup.
    max_stop = float(cfg.get("max_stop_distance_pct", 10.0))
    stop_distance_pct = (pivot - stop_loss) / pivot * 100.0
    if stop_distance_pct > max_stop:
        return None

    avg_vol = float(np.mean(vol_w[-50:])) if len(vol_w) >= 50 else float(np.mean(vol_w))
    volume_ratio = float(vol_w[-1] / avg_vol) if avg_vol > 0 else 0.0
    target = pivot + cup_depth

    # Conviction confidence (legacy: used by scorer alongside filters).
    confidence = 58.0
    confidence += max(0.0, 12.0 - rim_distance)
    confidence += max(0.0, 10.0 - abs(handle_retrace_pct - 33.0) / 3.0)
    if breakout:
        confidence += 10.0
    if volume_ratio >= 1.2:
        confidence += 8.0
    confidence = clip_confidence(confidence)

    # Pattern quality (0-10): independent grade of textbook-ness.
    quality_breakdown = _pattern_quality(
        rim_distance_pct=rim_distance,
        depth_pct=depth_pct,
        cup_high=cup_high,
        cup_low=cup_low,
        trough_idx_in_cup=trough_idx,
        cup_end=cup_end,
        handle_pullback_pct=handle_pullback_pct,
        handle_high_gap_pct=handle_high_gap_pct,
        handle_retrace_pct=handle_retrace_pct,
        handle_vol=vol_w[cup_end:],
        cup_vol=vol_w[:cup_end],
        breakout=breakout,
        distance_to_pivot_pct=(pivot - latest_close) / pivot * 100.0,
        stop_distance_pct=stop_distance_pct,
    )
    pattern_quality_score = quality_breakdown["total"]

    return PatternResult(
        pattern="Cup & Handle",
        status="BREAKING OUT" if breakout else "PIVOT READY",
        pivot=round(pivot, 2),
        target=round(target, 2),
        stop_loss=round(stop_loss, 2),
        confidence=confidence,
        explanation=(
            f"{timeframe} cup depth {depth_pct:.1f}% with rim distance {rim_distance:.1f}%; "
            f"handle retrace {handle_retrace_pct:.1f}%, handle pullback {handle_pullback_pct:.1f}%; "
            f"pattern grade {pattern_quality_score:.1f}/10."
        ),
        timeframe=timeframe,
        bars_in_pattern=window_len,
        quality_score=confidence,
        extra={
            "left_rim_idx": left_idx,
            "right_rim_idx": right_idx,
            "trough_idx": trough_idx,
            "handle_start_idx": cup_end,
            "depth_pct": round(depth_pct, 2),
            "handle_retrace_pct": round(handle_retrace_pct, 2),
            "handle_pullback_pct": round(handle_pullback_pct, 2),
            "handle_high_gap_pct": round(handle_high_gap_pct, 2),
            "stop_distance_pct": round(stop_distance_pct, 2),
            "volume_ratio": round(volume_ratio, 2),
            "pattern_quality_score": pattern_quality_score,
            "pattern_quality_breakdown": quality_breakdown["components"],
        },
    )


def _pattern_quality(
    *,
    rim_distance_pct: float,
    depth_pct: float,
    cup_high: np.ndarray,
    cup_low: np.ndarray,
    trough_idx_in_cup: int,
    cup_end: int,
    handle_pullback_pct: float,
    handle_high_gap_pct: float,
    handle_retrace_pct: float,
    handle_vol: np.ndarray,
    cup_vol: np.ndarray,
    breakout: bool,
    distance_to_pivot_pct: float,
    stop_distance_pct: float,
) -> dict:
    """Return 0-10 cup & handle grade with per-component breakdown.

    Components (max points):
        rim_symmetry        2.0   left vs right rim distance (smaller = better)
        depth_healthy       1.5   12-25% sweet spot, 25-35% acceptable, more = penalty
        roundedness         1.5   trough centered in cup time-wise (U not V)
        handle_quality      2.0   real pullback (2-8%) + high tests pivot (gap <2%)
        volume_contraction  1.0   handle vol < cup vol
        breakout_proximity  1.0   breakout = full, near pivot = partial
        stop_tightness      1.0   stop close to pivot = lower risk = higher grade
    """
    # 1. Rim symmetry (max 2.0)
    if rim_distance_pct <= 2.0:
        rim_pts = 2.0
    elif rim_distance_pct <= 4.0:
        rim_pts = 1.5
    elif rim_distance_pct <= 6.0:
        rim_pts = 1.0
    else:
        rim_pts = 0.5

    # 2. Depth healthy (max 1.5) — sweet spot 15-25%
    if 15.0 <= depth_pct <= 25.0:
        depth_pts = 1.5
    elif 12.0 <= depth_pct < 15.0 or 25.0 < depth_pct <= 35.0:
        depth_pts = 1.0
    elif 35.0 < depth_pct <= 45.0:
        depth_pts = 0.5
    else:
        depth_pts = 0.2

    # 3. Roundedness (max 1.5) — trough should be in middle 60% of cup bars,
    # ideally exact center. Sharp V = trough near left or right rim = low score.
    cup_bars = max(1, cup_end)
    trough_position = trough_idx_in_cup / cup_bars  # 0 = left rim, 1 = right rim
    center_dist = abs(trough_position - 0.5)  # 0 = perfect U, 0.5 = V at edge
    if center_dist <= 0.10:
        roundedness_pts = 1.5
    elif center_dist <= 0.20:
        roundedness_pts = 1.0
    elif center_dist <= 0.30:
        roundedness_pts = 0.5
    else:
        roundedness_pts = 0.2

    # 4. Handle quality (max 2.0) — real handle with controlled pullback +
    # high tests pivot. Combines two sub-checks.
    # 4a. Pullback in healthy range 3-8% = full, 2-3% or 8-12% = half
    if 3.0 <= handle_pullback_pct <= 8.0:
        pullback_pts = 1.0
    elif 2.0 <= handle_pullback_pct < 3.0 or 8.0 < handle_pullback_pct <= 12.0:
        pullback_pts = 0.5
    else:
        pullback_pts = 0.2
    # 4b. Handle high tests pivot — gap < 2% = full, 2-5% = half
    if handle_high_gap_pct <= 2.0:
        high_test_pts = 1.0
    elif handle_high_gap_pct <= 4.0:
        high_test_pts = 0.5
    else:
        high_test_pts = 0.2
    handle_pts = pullback_pts + high_test_pts

    # 5. Volume contraction in handle vs cup (max 1.0)
    vol_pts = 0.0
    if len(handle_vol) >= 3 and len(cup_vol) >= 10:
        handle_avg = float(np.mean(handle_vol))
        cup_avg = float(np.mean(cup_vol))
        if cup_avg > 0:
            ratio = handle_avg / cup_avg
            if ratio <= 0.7:
                vol_pts = 1.0
            elif ratio <= 0.9:
                vol_pts = 0.5
            else:
                vol_pts = 0.0

    # 6. Breakout / proximity (max 1.0)
    if breakout:
        proximity_pts = 1.0
    elif distance_to_pivot_pct <= 1.5:
        proximity_pts = 0.7
    elif distance_to_pivot_pct <= 3.0:
        proximity_pts = 0.4
    else:
        proximity_pts = 0.1

    # 7. Stop tightness (max 1.0) — tighter stop = lower risk = better grade
    if stop_distance_pct <= 4.0:
        stop_pts = 1.0
    elif stop_distance_pct <= 6.0:
        stop_pts = 0.7
    elif stop_distance_pct <= 8.0:
        stop_pts = 0.4
    else:
        stop_pts = 0.2

    total = (
        rim_pts
        + depth_pts
        + roundedness_pts
        + handle_pts
        + vol_pts
        + proximity_pts
        + stop_pts
    )
    total = round(max(0.0, min(10.0, total)), 1)

    return {
        "total": total,
        "components": {
            "rim_symmetry": round(rim_pts, 2),
            "depth_healthy": round(depth_pts, 2),
            "roundedness": round(roundedness_pts, 2),
            "handle_quality": round(handle_pts, 2),
            "volume_contraction": round(vol_pts, 2),
            "breakout_proximity": round(proximity_pts, 2),
            "stop_tightness": round(stop_pts, 2),
        },
    }
