"""Rounded reversal base with descending supply-line reclaim detector."""

from __future__ import annotations

import numpy as np

from config import settings
from patterns.base import PatternResult
from patterns.utils import clip_confidence, has_ohlcv, local_highs, series


def detect(daily: dict, weekly: dict | None = None) -> list[PatternResult]:
    cfg = settings.ROUNDED_REVERSAL_BASE
    lookback = int(cfg["lookback_bars"])
    min_bars = int(cfg["min_bars"])
    if not has_ohlcv(daily, min_bars):
        return []

    high = series(daily, "high")[-lookback:]
    low = series(daily, "low")[-lookback:]
    close = series(daily, "close")[-lookback:]
    volume = series(daily, "volume")[-lookback:]
    if len(close) < min_bars:
        return []

    base = _rounded_base(high, low, close, cfg)
    if base is None:
        return []
    trendline = _descending_trendline(high, close, cfg)
    if trendline is None:
        return []

    latest_close = float(close[-1])
    pivot = float(trendline["pivot"])
    breakout = latest_close > pivot
    if breakout:
        extension_pct = (latest_close - pivot) / pivot * 100.0
        if extension_pct > float(cfg["max_breakout_extension_pct"]):
            return []
        distance_to_pivot_pct = 0.0
    else:
        distance_to_pivot_pct = (pivot - latest_close) / pivot * 100.0
        if distance_to_pivot_pct > float(cfg["within_breakout_pct"]):
            return []

    stop_loss = _stop_loss(low, int(base["right_low_idx"]), cfg)
    if stop_loss is None or stop_loss >= pivot:
        return []
    stop_distance_pct = (pivot - stop_loss) / pivot * 100.0
    if stop_distance_pct <= 0 or stop_distance_pct > float(cfg["max_stop_distance_pct"]):
        return []

    volume_ratio = _volume_ratio(volume)
    weekly_context = _weekly_context(weekly)
    quality = _pattern_quality(
        base_depth_pct=float(base["base_depth_pct"]),
        prior_decline_pct=float(base["prior_decline_pct"]),
        right_side_recovery_pct=float(base["right_side_recovery_pct"]),
        roundedness_pct=float(base["roundedness_pct"]),
        trendline_error_pct=float(trendline["touch_error_pct"]),
        touch_count=len(trendline["touches"]),
        distance_to_pivot_pct=distance_to_pivot_pct,
        stop_distance_pct=stop_distance_pct,
        volume_ratio=volume_ratio,
        weekly_aligned=weekly_context["aligned"],
        breakout=breakout,
    )
    grade = quality["total"]
    target = pivot + (pivot - stop_loss) * 2.0
    confidence = clip_confidence(
        54.0
        + min(16.0, float(base["prior_decline_pct"]) * 0.25)
        + min(12.0, float(base["right_side_recovery_pct"]) * 0.35)
        + min(10.0, len(trendline["touches"]) * 3.0)
        + min(8.0, volume_ratio * 3.0)
        + (6.0 if breakout else 0.0)
        + (5.0 if weekly_context["aligned"] else 0.0)
    )

    return [
        PatternResult(
            pattern="Rounded Reversal Base",
            status="BREAKING OUT" if breakout else "PIVOT READY",
            pivot=round(pivot, 2),
            target=round(target, 2),
            stop_loss=round(stop_loss, 2),
            confidence=confidence,
            explanation=(
                f"Rounded reversal base after {base['prior_decline_pct']:.1f}% decline; "
                f"right side recovered {base['right_side_recovery_pct']:.1f}%; "
                f"{len(trendline['touches'])} descending supply-line touches; "
                f"daily volume {volume_ratio:.2f}x avg; pattern grade {grade:.1f}/10."
            ),
            timeframe="daily",
            bars_in_pattern=int(len(close) - int(base["base_start_idx"])),
            quality_score=confidence,
            extra={
                "reversal_model": "rounded_base_trendline_reclaim",
                "base_start_idx": int(base["base_start_idx"]),
                "base_low_idx": int(base["base_low_idx"]),
                "right_low_idx": int(base["right_low_idx"]),
                "base_depth_pct": round(float(base["base_depth_pct"]), 2),
                "prior_decline_pct": round(float(base["prior_decline_pct"]), 2),
                "right_side_recovery_pct": round(float(base["right_side_recovery_pct"]), 2),
                "roundedness_pct": round(float(base["roundedness_pct"]), 2),
                "trendline_touch_indices": list(trendline["touches"]),
                "trendline_error_pct": round(float(trendline["touch_error_pct"]), 3),
                "line_slope": round(float(trendline["line_slope"]), 4),
                "volume_ratio": round(volume_ratio, 2),
                "stop_distance_pct": round(stop_distance_pct, 2),
                "weekly_thesis_aligned": weekly_context["aligned"],
                "weekly_context": weekly_context["status"],
                "pattern_quality_score": grade,
                "pattern_quality_breakdown": quality["components"],
            },
        )
    ]


def _rounded_base(high: np.ndarray, low: np.ndarray, close: np.ndarray, cfg: dict) -> dict | None:
    latest_idx = len(close) - 1
    base_low_idx = int(np.argmin(low))
    min_duration = int(cfg["min_base_duration_bars"])
    if base_low_idx < 20 or latest_idx - base_low_idx < 15:
        return None

    left_window_start = max(0, base_low_idx - 55)
    left_high = float(np.max(high[left_window_start:base_low_idx]))
    base_low = float(low[base_low_idx])
    latest_close = float(close[-1])
    if left_high <= 0 or base_low <= 0:
        return None

    prior_decline_pct = (left_high - base_low) / left_high * 100.0
    if prior_decline_pct < float(cfg["min_prior_decline_pct"]):
        return None

    right_side_recovery_pct = (latest_close - base_low) / base_low * 100.0
    if right_side_recovery_pct < float(cfg["min_right_side_recovery_pct"]):
        return None

    base_depth_pct = (left_high - base_low) / left_high * 100.0
    if base_depth_pct > float(cfg["max_base_depth_pct"]):
        return None

    base_start_idx = _base_start_idx(high, base_low_idx, left_high)
    if latest_idx - base_start_idx < min_duration:
        return None

    right_low_lookback = int(cfg["right_low_lookback_bars"])
    right_start = max(base_low_idx + 1, len(low) - right_low_lookback)
    right_slice = low[right_start:]
    if right_slice.size == 0:
        return None
    right_low_idx = right_start + int(np.argmin(right_slice))
    right_low = float(low[right_low_idx])
    if right_low < base_low * 0.98:
        return None

    left_avg = float(np.mean(close[max(0, base_low_idx - 20) : max(1, base_low_idx - 5)]))
    right_avg = float(np.mean(close[min(len(close) - 1, base_low_idx + 5) :]))
    roundedness_pct = (right_avg - left_avg) / max(left_avg, 1e-9) * 100.0
    if roundedness_pct < -8.0:
        return None

    return {
        "base_start_idx": base_start_idx,
        "base_low_idx": base_low_idx,
        "right_low_idx": right_low_idx,
        "base_depth_pct": base_depth_pct,
        "prior_decline_pct": prior_decline_pct,
        "right_side_recovery_pct": right_side_recovery_pct,
        "roundedness_pct": roundedness_pct,
    }


def _descending_trendline(high: np.ndarray, close: np.ndarray, cfg: dict) -> dict | None:
    swing_highs = local_highs(high[:-1], int(cfg["swing_order"]))
    anchor_limit = len(high) - int(cfg["trendline_anchor_exclusion_bars"])
    swing_highs = swing_highs[(swing_highs > 0) & (swing_highs < anchor_limit)]
    if len(swing_highs) < 2:
        return None

    latest_idx = len(high) - 1
    min_sep = int(cfg["min_trendline_separation_bars"])
    tolerance = float(cfg["trendline_tolerance_pct"])
    best: dict | None = None
    for left_pos, left_idx in enumerate(swing_highs[:-1]):
        for right_idx in swing_highs[left_pos + 1 :]:
            if int(right_idx) - int(left_idx) < min_sep:
                continue
            left_high = float(high[int(left_idx)])
            right_high = float(high[int(right_idx)])
            if right_high >= left_high:
                continue
            slope = (right_high - left_high) / (int(right_idx) - int(left_idx))
            pivot = _line_at(left_high, slope, int(left_idx), latest_idx)
            if pivot <= 0:
                continue

            touches = []
            for idx in swing_highs:
                if int(idx) < int(left_idx):
                    continue
                projected = _line_at(left_high, slope, int(left_idx), int(idx))
                if projected <= 0:
                    continue
                error_pct = abs(float(high[int(idx)]) - projected) / projected * 100.0
                if error_pct <= tolerance:
                    touches.append(int(idx))
            if len(touches) < int(cfg["min_trendline_touches"]):
                continue

            touch_error = _mean_line_error_pct(high, touches, left_high, slope, int(left_idx))
            latest_close = float(close[-1])
            if latest_close < pivot:
                distance = (pivot - latest_close) / pivot * 100.0
                if distance > float(cfg["within_breakout_pct"]):
                    continue
            candidate = {
                "pivot": pivot,
                "touches": touches,
                "touch_error_pct": touch_error,
                "line_slope": slope,
            }
            if best is None or (
                len(candidate["touches"]),
                -candidate["touch_error_pct"],
                candidate["pivot"],
            ) > (
                len(best["touches"]),
                -best["touch_error_pct"],
                best["pivot"],
            ):
                best = candidate
    return best


def _base_start_idx(high: np.ndarray, base_low_idx: int, left_high: float) -> int:
    threshold = left_high * 0.92
    for idx in range(max(0, base_low_idx - 70), base_low_idx):
        if float(high[idx]) >= threshold:
            return idx
    return max(0, base_low_idx - 55)


def _stop_loss(low: np.ndarray, right_low_idx: int, cfg: dict) -> float | None:
    lookback = int(cfg["right_low_lookback_bars"])
    start = max(0, min(right_low_idx, len(low) - lookback))
    recent_low = float(np.min(low[start:]))
    if recent_low <= 0:
        return None
    return recent_low * (1.0 - float(cfg["stop_buffer_pct"]) / 100.0)


def _weekly_context(weekly: dict | None) -> dict[str, object]:
    if not weekly or not has_ohlcv(weekly, 35):
        return {"aligned": False, "status": "weekly unavailable"}
    close = series(weekly, "close")
    if len(close) < 35:
        return {"aligned": False, "status": "weekly insufficient"}
    ma10 = np.mean(close[-10:])
    ma30 = np.mean(close[-30:])
    aligned = bool(float(close[-1]) > float(ma10) >= float(ma30))
    status = "weekly thesis aligned" if aligned else "weekly thesis not confirmed"
    return {"aligned": aligned, "status": status}


def _volume_ratio(volume: np.ndarray) -> float:
    if len(volume) < 51:
        avg = float(np.mean(volume[:-1])) if len(volume) > 1 else 0.0
    else:
        avg = float(np.mean(volume[-51:-1]))
    return float(volume[-1] / avg) if avg > 0 else 0.0


def _line_at(left_high: float, slope: float, left_idx: int, idx: int) -> float:
    return left_high + slope * (idx - left_idx)


def _mean_line_error_pct(high: np.ndarray, offsets: list[int], left_high: float, slope: float, left_idx: int) -> float:
    errors = []
    for idx in offsets:
        projected = _line_at(left_high, slope, left_idx, idx)
        if projected > 0:
            errors.append(abs(float(high[idx]) - projected) / projected * 100.0)
    return float(np.mean(errors)) if errors else 100.0


def _pattern_quality(
    *,
    base_depth_pct: float,
    prior_decline_pct: float,
    right_side_recovery_pct: float,
    roundedness_pct: float,
    trendline_error_pct: float,
    touch_count: int,
    distance_to_pivot_pct: float,
    stop_distance_pct: float,
    volume_ratio: float,
    weekly_aligned: bool,
    breakout: bool,
) -> dict:
    decline_pts = 1.3 if prior_decline_pct >= 25.0 else 0.8
    depth_pts = 1.2 if 20.0 <= base_depth_pct <= 45.0 else 0.7
    recovery_pts = 1.4 if right_side_recovery_pct >= 18.0 else 0.8
    rounded_pts = 1.1 if roundedness_pct >= -1.0 else 0.6
    touch_pts = 1.2 if touch_count >= 3 else 0.8
    line_pts = 1.1 if trendline_error_pct <= 2.0 else 0.7
    proximity_pts = 1.1 if breakout else (0.8 if distance_to_pivot_pct <= 2.0 else 0.5)
    stop_pts = 1.0 if stop_distance_pct <= 10.0 else 0.6
    volume_pts = 0.9 if volume_ratio >= 1.3 else (0.6 if volume_ratio >= 0.9 else 0.3)
    weekly_pts = 0.7 if weekly_aligned else 0.0
    total = round(
        max(
            0.0,
            min(
                10.0,
                decline_pts
                + depth_pts
                + recovery_pts
                + rounded_pts
                + touch_pts
                + line_pts
                + proximity_pts
                + stop_pts
                + volume_pts
                + weekly_pts,
            ),
        ),
        1,
    )
    return {
        "total": total,
        "components": {
            "prior_decline": round(decline_pts, 2),
            "base_depth": round(depth_pts, 2),
            "right_side_recovery": round(recovery_pts, 2),
            "roundedness": round(rounded_pts, 2),
            "trendline_touches": round(touch_pts, 2),
            "trendline_fit": round(line_pts, 2),
            "pivot_proximity": round(proximity_pts, 2),
            "stop_tightness": round(stop_pts, 2),
            "volume": round(volume_pts, 2),
            "weekly_thesis": round(weekly_pts, 2),
        },
    }
