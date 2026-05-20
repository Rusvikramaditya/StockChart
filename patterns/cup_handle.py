"""Cup & Handle detector."""

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

    handle_low = float(np.min(low_w[cup_end:]))
    handle_depth = pivot - handle_low
    cup_depth = pivot - trough
    if cup_depth <= 0:
        return None
    handle_retrace_pct = handle_depth / cup_depth * 100.0
    handle_floor = trough + cup_depth * (2.0 / 3.0)
    if handle_retrace_pct > cfg["handle_max_retrace_pct"] or handle_low < handle_floor:
        return None

    latest_close = float(close_w[-1])
    breakout = latest_close > pivot
    if not breakout and (pivot - latest_close) / pivot * 100.0 > 5.0:
        return None

    avg_vol = float(np.mean(vol_w[-50:])) if len(vol_w) >= 50 else float(np.mean(vol_w))
    volume_ratio = float(vol_w[-1] / avg_vol) if avg_vol > 0 else 0.0
    target = pivot + cup_depth
    stop_loss = handle_low
    confidence = 58.0
    confidence += max(0.0, 12.0 - rim_distance)
    confidence += max(0.0, 10.0 - abs(handle_retrace_pct - 33.0) / 3.0)
    if breakout:
        confidence += 10.0
    if volume_ratio >= 1.2:
        confidence += 8.0
    quality_score = clip_confidence(confidence)

    return PatternResult(
        pattern="Cup & Handle",
        status="BREAKING OUT" if breakout else "PIVOT READY",
        pivot=round(pivot, 2),
        target=round(target, 2),
        stop_loss=round(stop_loss, 2),
        confidence=quality_score,
        explanation=(
            f"{timeframe} cup depth {depth_pct:.1f}% with rim distance {rim_distance:.1f}% "
            f"and handle retrace {handle_retrace_pct:.1f}%."
        ),
        timeframe=timeframe,
        bars_in_pattern=window_len,
        quality_score=quality_score,
        extra={
            "left_rim_idx": left_idx,
            "right_rim_idx": right_idx,
            "trough_idx": trough_idx,
            "handle_start_idx": cup_end,
            "depth_pct": round(depth_pct, 2),
            "handle_retrace_pct": round(handle_retrace_pct, 2),
            "volume_ratio": round(volume_ratio, 2),
        },
    )
