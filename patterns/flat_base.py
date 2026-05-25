"""Flat Base / Darvas Box detector.

Strict continuation setup: a tight horizontal base near 52-week highs, with
multiple resistance touches, controlled depth, volume contraction, and a
scan-date entry that is not already extended.
"""

from __future__ import annotations

import numpy as np

from config import settings
from patterns.base import PatternResult
from patterns.utils import clip_confidence, has_ohlcv, is_stage2, series


def detect(daily: dict, weekly: dict | None = None) -> list[PatternResult]:
    cfg = settings.FLAT_BASE
    max_bars = int(cfg["max_base_bars"])
    min_bars = int(cfg["min_base_bars"])
    if not has_ohlcv(daily, max(252, max_bars + 1)):
        return []

    high_full = series(daily, "high")
    low_full = series(daily, "low")
    close_full = series(daily, "close")
    volume_full = series(daily, "volume")
    if not is_stage2(close_full, settings.STAGE2):
        return []

    latest_close = float(close_full[-1])
    high_52w = float(np.max(high_full[-252:]))
    best: dict | None = None

    for base_len in range(min_bars, max_bars + 1):
        high = high_full[-base_len:]
        low = low_full[-base_len:]
        volume = volume_full[-base_len:]
        if len(high) < min_bars:
            continue

        prior_high = high[:-1]
        if prior_high.size == 0:
            continue
        pivot = float(np.max(prior_high))
        if pivot <= 0:
            continue

        base_low = float(np.min(low))
        depth_pct = (pivot - base_low) / pivot * 100.0
        if not (float(cfg["min_depth_pct"]) <= depth_pct <= float(cfg["max_depth_pct"])):
            continue

        from_high_pct = (high_52w - pivot) / high_52w * 100.0 if high_52w > 0 else 999.0
        if from_high_pct > float(cfg["max_from_52w_high_pct"]):
            continue

        tolerance = float(cfg["resistance_tolerance_pct"])
        touch_idx = np.flatnonzero(np.abs(prior_high - pivot) / pivot * 100.0 <= tolerance)
        if len(touch_idx) < int(cfg["min_resistance_touches"]):
            continue
        touch_highs = prior_high[touch_idx]
        touch_range_pct = float((touch_highs.max() - touch_highs.min()) / pivot * 100.0)
        if touch_range_pct > float(cfg["max_touch_range_pct"]):
            continue

        vol_ratio = _volume_contraction_ratio(volume)
        if vol_ratio > float(cfg["max_volume_contraction_ratio"]):
            continue

        breakout = latest_close > pivot
        if breakout:
            extension_pct = (latest_close - pivot) / pivot * 100.0
            if extension_pct > float(cfg["max_breakout_extension_pct"]):
                continue
            distance_to_pivot_pct = 0.0
        else:
            distance_to_pivot_pct = (pivot - latest_close) / pivot * 100.0
            if distance_to_pivot_pct > float(cfg["within_breakout_pct"]):
                continue

        recent_stop_window = min(15, base_len)
        stop_loss = float(np.min(low[-recent_stop_window:]))
        stop_distance_pct = (pivot - stop_loss) / pivot * 100.0 if pivot > 0 else 999.0
        if stop_loss <= 0 or stop_distance_pct <= 0 or stop_distance_pct > float(cfg["max_stop_distance_pct"]):
            continue

        risk = pivot - stop_loss
        target = pivot + risk * 2.0
        quality = _pattern_quality(
            touch_count=len(touch_idx),
            touch_range_pct=touch_range_pct,
            depth_pct=depth_pct,
            from_high_pct=from_high_pct,
            volume_ratio=vol_ratio,
            breakout=breakout,
            distance_to_pivot_pct=distance_to_pivot_pct,
            stop_distance_pct=stop_distance_pct,
            base_len=base_len,
        )
        candidate = {
            "base_len": base_len,
            "pivot": pivot,
            "target": target,
            "stop_loss": stop_loss,
            "base_low": base_low,
            "depth_pct": depth_pct,
            "from_high_pct": from_high_pct,
            "touch_idx": touch_idx,
            "touch_range_pct": touch_range_pct,
            "volume_ratio": vol_ratio,
            "breakout": breakout,
            "distance_to_pivot_pct": distance_to_pivot_pct,
            "stop_distance_pct": stop_distance_pct,
            "quality": quality,
        }
        if best is None or candidate["quality"]["total"] > best["quality"]["total"]:
            best = candidate

    if best is None:
        return []

    quality_score = best["quality"]["total"]
    confidence = 58.0 + min(14.0, len(best["touch_idx"]) * 3.0)
    confidence += max(0.0, 12.0 - best["depth_pct"] * 0.5)
    confidence += max(0.0, (1.0 - best["volume_ratio"]) * 15.0)
    if best["breakout"]:
        confidence += 8.0
    confidence = clip_confidence(confidence)

    return [
        PatternResult(
            pattern="Flat Base",
            status="BREAKING OUT" if best["breakout"] else "PIVOT READY",
            pivot=round(best["pivot"], 2),
            target=round(best["target"], 2),
            stop_loss=round(best["stop_loss"], 2),
            confidence=confidence,
            explanation=(
                f"Tight {best['base_len']}-bar base near highs, depth {best['depth_pct']:.1f}%, "
                f"{len(best['touch_idx'])} resistance touches, volume ratio {best['volume_ratio']:.2f}; "
                f"pattern grade {quality_score:.1f}/10."
            ),
            timeframe="daily",
            bars_in_pattern=int(best["base_len"]),
            quality_score=confidence,
            extra={
                "touch_indices": best["touch_idx"].tolist(),
                "base_low": round(best["base_low"], 2),
                "depth_pct": round(best["depth_pct"], 2),
                "from_52w_high_pct": round(best["from_high_pct"], 2),
                "touch_range_pct": round(best["touch_range_pct"], 3),
                "volume_contraction_ratio": round(best["volume_ratio"], 2),
                "stop_distance_pct": round(best["stop_distance_pct"], 2),
                "pattern_quality_score": quality_score,
                "pattern_quality_breakdown": best["quality"]["components"],
            },
        )
    ]


def _volume_contraction_ratio(volume: np.ndarray) -> float:
    if len(volume) < 10:
        return 1.0
    base_volume = volume[:-1] if len(volume) > 1 else volume
    half = len(base_volume) // 2
    if half <= 0:
        return 1.0
    first = float(np.mean(base_volume[:half]))
    second = float(np.mean(base_volume[half:]))
    return second / first if first > 0 else 1.0


def _pattern_quality(
    *,
    touch_count: int,
    touch_range_pct: float,
    depth_pct: float,
    from_high_pct: float,
    volume_ratio: float,
    breakout: bool,
    distance_to_pivot_pct: float,
    stop_distance_pct: float,
    base_len: int,
) -> dict:
    if touch_count >= 5:
        touch_pts = 2.0
    elif touch_count == 4:
        touch_pts = 1.5
    else:
        touch_pts = 1.0

    if touch_range_pct <= 0.25:
        flat_pts = 1.5
    elif touch_range_pct <= 0.5:
        flat_pts = 1.0
    else:
        flat_pts = 0.5

    if 5.0 <= depth_pct <= 10.0:
        depth_pts = 1.5
    elif depth_pct <= 12.0:
        depth_pts = 1.0
    else:
        depth_pts = 0.5

    if from_high_pct <= 3.0:
        high_pts = 1.0
    elif from_high_pct <= 7.0:
        high_pts = 0.7
    else:
        high_pts = 0.3

    if volume_ratio <= 0.65:
        vol_pts = 1.5
    elif volume_ratio <= 0.8:
        vol_pts = 1.0
    else:
        vol_pts = 0.5

    if breakout:
        prox_pts = 1.0
    elif distance_to_pivot_pct <= 1.0:
        prox_pts = 0.8
    else:
        prox_pts = 0.4

    if stop_distance_pct <= 5.0:
        stop_pts = 1.0
    elif stop_distance_pct <= 8.0:
        stop_pts = 0.7
    else:
        stop_pts = 0.3

    duration_pts = 0.5 if 25 <= base_len <= 65 else 0.2
    total = touch_pts + flat_pts + depth_pts + high_pts + vol_pts + prox_pts + stop_pts + duration_pts
    total = round(max(0.0, min(10.0, total)), 1)
    return {
        "total": total,
        "components": {
            "touch_count": round(touch_pts, 2),
            "touch_flatness": round(flat_pts, 2),
            "base_depth": round(depth_pts, 2),
            "near_high": round(high_pts, 2),
            "volume_contraction": round(vol_pts, 2),
            "pivot_proximity": round(prox_pts, 2),
            "stop_tightness": round(stop_pts, 2),
            "duration": round(duration_pts, 2),
        },
    }
