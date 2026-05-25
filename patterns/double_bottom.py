"""Double Bottom / Undercut-and-Reclaim detector."""

from __future__ import annotations

import numpy as np

from config import settings
from patterns.base import PatternResult
from patterns.utils import clip_confidence, has_ohlcv, local_lows, series


def detect(daily: dict, weekly: dict | None = None) -> list[PatternResult]:
    cfg = settings.DOUBLE_BOTTOM
    lookback = int(cfg["lookback_bars"])
    if not has_ohlcv(daily, lookback):
        return []

    high = series(daily, "high")[-lookback:]
    low = series(daily, "low")[-lookback:]
    close = series(daily, "close")[-lookback:]
    volume = series(daily, "volume")[-lookback:]
    latest_close = float(close[-1])

    lows_idx = [int(idx) for idx in local_lows(low, int(cfg["swing_order"])) if idx < len(low) - 3]
    if len(lows_idx) < 2:
        return []

    best: dict | None = None
    for left in lows_idx:
        for right in lows_idx:
            if right <= left:
                continue
            separation = right - left
            if separation < int(cfg["min_low_separation_bars"]) or separation > int(cfg["max_low_separation_bars"]):
                continue

            first_low = float(low[left])
            second_low = float(low[right])
            if first_low <= 0 or second_low <= 0:
                continue

            undercut_pct = (first_low - second_low) / first_low * 100.0
            if not (float(cfg["min_undercut_pct"]) <= undercut_pct <= float(cfg["max_undercut_pct"])):
                continue

            middle_high_slice = high[left + 1 : right]
            if middle_high_slice.size == 0:
                continue
            pivot = float(np.max(middle_high_slice))
            if pivot <= 0:
                continue

            base_low = min(first_low, second_low)
            depth_pct = (pivot - base_low) / pivot * 100.0
            if not (float(cfg["min_base_depth_pct"]) <= depth_pct <= float(cfg["max_base_depth_pct"])):
                continue

            low_volume_ratio = _low_volume_ratio(volume, left, right)
            if low_volume_ratio > float(cfg["max_second_low_volume_ratio"]):
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

            stop_loss = second_low * 0.99
            stop_distance_pct = (pivot - stop_loss) / pivot * 100.0
            if stop_distance_pct <= 0 or stop_distance_pct > float(cfg["max_stop_distance_pct"]):
                continue

            risk = pivot - stop_loss
            target = pivot + risk * 2.0
            quality = _pattern_quality(
                undercut_pct=undercut_pct,
                depth_pct=depth_pct,
                low_volume_ratio=low_volume_ratio,
                breakout=breakout,
                distance_to_pivot_pct=distance_to_pivot_pct,
                stop_distance_pct=stop_distance_pct,
                separation=separation,
            )
            candidate = {
                "left": left,
                "right": right,
                "separation": separation,
                "first_low": first_low,
                "second_low": second_low,
                "undercut_pct": undercut_pct,
                "pivot": pivot,
                "target": target,
                "stop_loss": stop_loss,
                "depth_pct": depth_pct,
                "low_volume_ratio": low_volume_ratio,
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
    confidence = 55.0 + max(0.0, 12.0 - best["depth_pct"] * 0.25)
    confidence += max(0.0, (1.0 - best["low_volume_ratio"]) * 20.0)
    confidence += 8.0 if best["breakout"] else 0.0
    confidence = clip_confidence(confidence)

    return [
        PatternResult(
            pattern="Double Bottom",
            status="BREAKING OUT" if best["breakout"] else "PIVOT READY",
            pivot=round(best["pivot"], 2),
            target=round(best["target"], 2),
            stop_loss=round(best["stop_loss"], 2),
            confidence=confidence,
            explanation=(
                f"Undercut-and-reclaim base: second low undercut by {best['undercut_pct']:.1f}%, "
                f"base depth {best['depth_pct']:.1f}%, second-low volume ratio "
                f"{best['low_volume_ratio']:.2f}; pattern grade {quality_score:.1f}/10."
            ),
            timeframe="daily",
            bars_in_pattern=int(lookback - best["left"]),
            quality_score=confidence,
            extra={
                "left_low_idx": best["left"],
                "right_low_idx": best["right"],
                "first_low": round(best["first_low"], 2),
                "second_low": round(best["second_low"], 2),
                "undercut_pct": round(best["undercut_pct"], 2),
                "base_depth_pct": round(best["depth_pct"], 2),
                "second_low_volume_ratio": round(best["low_volume_ratio"], 2),
                "stop_distance_pct": round(best["stop_distance_pct"], 2),
                "pattern_quality_score": quality_score,
                "pattern_quality_breakdown": best["quality"]["components"],
            },
        )
    ]


def _low_volume_ratio(volume: np.ndarray, left: int, right: int) -> float:
    radius = 2
    first = volume[max(0, left - radius) : min(len(volume), left + radius + 1)]
    second = volume[max(0, right - radius) : min(len(volume), right + radius + 1)]
    first_avg = float(np.mean(first)) if first.size else 0.0
    second_avg = float(np.mean(second)) if second.size else 0.0
    return second_avg / first_avg if first_avg > 0 else 1.0


def _pattern_quality(
    *,
    undercut_pct: float,
    depth_pct: float,
    low_volume_ratio: float,
    breakout: bool,
    distance_to_pivot_pct: float,
    stop_distance_pct: float,
    separation: int,
) -> dict:
    if 1.0 <= undercut_pct <= 4.0:
        undercut_pts = 2.0
    elif undercut_pct <= 6.0:
        undercut_pts = 1.4
    else:
        undercut_pts = 0.8

    if 12.0 <= depth_pct <= 25.0:
        depth_pts = 1.5
    elif depth_pct <= 30.0:
        depth_pts = 1.0
    else:
        depth_pts = 0.5

    if low_volume_ratio <= 0.55:
        vol_pts = 1.5
    elif low_volume_ratio <= 0.75:
        vol_pts = 1.0
    else:
        vol_pts = 0.5

    if breakout:
        prox_pts = 1.5
    elif distance_to_pivot_pct <= 1.0:
        prox_pts = 1.0
    elif distance_to_pivot_pct <= 2.0:
        prox_pts = 0.7
    else:
        prox_pts = 0.3

    if stop_distance_pct <= 8.0:
        stop_pts = 1.5
    elif stop_distance_pct <= 12.0:
        stop_pts = 1.0
    else:
        stop_pts = 0.5

    separation_pts = 1.0 if 20 <= separation <= 65 else 0.5
    reclaim_pts = 1.0 if breakout or distance_to_pivot_pct <= 1.5 else 0.5

    total = undercut_pts + depth_pts + vol_pts + prox_pts + stop_pts + separation_pts + reclaim_pts
    total = round(max(0.0, min(10.0, total)), 1)
    return {
        "total": total,
        "components": {
            "undercut": round(undercut_pts, 2),
            "base_depth": round(depth_pts, 2),
            "volume_dryup": round(vol_pts, 2),
            "pivot_proximity": round(prox_pts, 2),
            "stop_tightness": round(stop_pts, 2),
            "low_separation": round(separation_pts, 2),
            "right_side_reclaim": round(reclaim_pts, 2),
        },
    }
