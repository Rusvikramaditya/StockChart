"""Weekly price-action breakout detector.

This detector is intentionally separate from daily pattern detectors. It
searches weekly candles for swing-trade structures: a horizontal resistance
breakout/base breakout or a descending trendline reversal breakout. Daily
volume and candle quality remain confirmation filters in the scorer/report.
"""

from __future__ import annotations

import numpy as np

from config import settings
from patterns.base import PatternResult
from patterns.utils import clip_confidence, has_ohlcv, local_highs, series


def detect(daily: dict, weekly: dict | None = None) -> list[PatternResult]:
    cfg = settings.WEEKLY_BREAKOUT
    if weekly is None or not has_ohlcv(weekly, int(cfg["min_bars"])):
        return []

    high_full = series(weekly, "high")
    low_full = series(weekly, "low")
    close_full = series(weekly, "close")
    volume_full = series(weekly, "volume")

    lookback = min(len(close_full), int(cfg["lookback_weeks"]))
    high = high_full[-lookback:]
    low = low_full[-lookback:]
    close = close_full[-lookback:]
    volume = volume_full[-lookback:]

    candidates = []
    horizontal = _horizontal_setup(high, low, close, volume, cfg)
    if horizontal is not None:
        candidates.append(horizontal)
    trendline = _trendline_setup(high, low, close, volume, cfg)
    if trendline is not None:
        candidates.append(trendline)
    if not candidates:
        return []

    candidates.sort(
        key=lambda item: (
            float(item.extra.get("pattern_quality_score", 0.0)),
            float(item.confidence),
        ),
        reverse=True,
    )
    return [candidates[0]]


def _horizontal_setup(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    cfg: dict,
) -> PatternResult | None:
    prior_high = high[:-1]
    if len(prior_high) < int(cfg["min_bars"]) - 1:
        return None

    resistance = float(np.max(prior_high))
    if resistance <= 0:
        return None

    swing_highs = local_highs(prior_high, order=2)
    if len(swing_highs) == 0:
        swing_highs = np.array([int(np.argmax(prior_high))], dtype=int)

    tolerance = float(cfg["resistance_tolerance_pct"])
    touch_offsets = swing_highs[
        np.abs(prior_high[swing_highs] - resistance) / resistance * 100.0 <= tolerance
    ]
    if len(touch_offsets) < int(cfg["min_resistance_touches"]):
        return None

    touch_highs = prior_high[touch_offsets]
    touch_range_pct = float((touch_highs.max() - touch_highs.min()) / resistance * 100.0)
    if touch_range_pct > float(cfg["max_touch_range_pct"]):
        return None

    spread = _spread_fraction(touch_offsets, len(prior_high))
    if spread < float(cfg["min_touch_spread_fraction"]):
        return None

    return _build_result(
        model="horizontal_resistance",
        pivot=resistance,
        high=high,
        low=low,
        close=close,
        volume=volume,
        cfg=cfg,
        touches=touch_offsets.tolist(),
        touch_range_pct=touch_range_pct,
        line_slope=0.0,
        prior_decline_pct=_prior_decline_pct(high, low),
    )


def _trendline_setup(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    cfg: dict,
) -> PatternResult | None:
    prior_high = high[:-1]
    if len(prior_high) < int(cfg["min_bars"]) - 1:
        return None

    prior_decline_pct = _prior_decline_pct(high, low)
    if prior_decline_pct < float(cfg["min_prior_decline_pct"]):
        return None

    swing_highs = local_highs(prior_high, order=2)
    anchor_limit = len(prior_high) - int(cfg.get("trendline_anchor_exclusion_weeks", 4))
    swing_highs = swing_highs[(swing_highs > 0) & (swing_highs < anchor_limit)]
    if len(swing_highs) < 2:
        return None

    latest_idx = len(high) - 1
    best: dict | None = None
    min_sep = int(cfg["min_trendline_separation_weeks"])
    tolerance = float(cfg["trendline_tolerance_pct"])

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
            if not _breakout_window_ok(float(close[-1]), pivot, cfg):
                continue

            touch_offsets = []
            for idx in swing_highs:
                projected = _line_at(left_high, slope, int(left_idx), int(idx))
                if projected <= 0:
                    continue
                distance = abs(float(high[int(idx)]) - projected) / projected * 100.0
                if distance <= tolerance:
                    touch_offsets.append(int(idx))
            if len(touch_offsets) < int(cfg["min_trendline_touches"]):
                continue

            touch_error = _mean_line_error_pct(high, touch_offsets, left_high, slope, int(left_idx))
            candidate = {
                "pivot": pivot,
                "touches": touch_offsets,
                "touch_range_pct": touch_error,
                "line_slope": slope,
                "prior_decline_pct": prior_decline_pct,
            }
            if best is None or (
                len(candidate["touches"]),
                -candidate["touch_range_pct"],
                candidate["pivot"],
            ) > (
                len(best["touches"]),
                -best["touch_range_pct"],
                best["pivot"],
            ):
                best = candidate

    if best is None:
        return None

    return _build_result(
        model="descending_trendline",
        pivot=float(best["pivot"]),
        high=high,
        low=low,
        close=close,
        volume=volume,
        cfg=cfg,
        touches=list(best["touches"]),
        touch_range_pct=float(best["touch_range_pct"]),
        line_slope=float(best["line_slope"]),
        prior_decline_pct=float(best["prior_decline_pct"]),
    )


def _build_result(
    *,
    model: str,
    pivot: float,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    cfg: dict,
    touches: list[int],
    touch_range_pct: float,
    line_slope: float,
    prior_decline_pct: float,
) -> PatternResult | None:
    latest_close = float(close[-1])
    if not _breakout_window_ok(latest_close, pivot, cfg):
        return None

    breakout = latest_close > pivot
    stop_window = max(2, int(cfg["stop_lookback_weeks"]))
    stop_loss = float(np.min(low[-stop_window:]))
    if stop_loss <= 0 or stop_loss >= pivot:
        return None
    stop_distance_pct = (pivot - stop_loss) / pivot * 100.0
    if stop_distance_pct > float(cfg["max_stop_distance_pct"]):
        return None

    avg_volume = float(np.mean(volume[-51:-1])) if len(volume) >= 51 else float(np.mean(volume[:-1]))
    volume_ratio = float(volume[-1] / avg_volume) if avg_volume > 0 else 0.0
    distance_to_pivot_pct = max(0.0, (pivot - latest_close) / pivot * 100.0)
    target = pivot + (pivot - stop_loss) * 2.0

    quality = _pattern_quality(
        model=model,
        touch_count=len(touches),
        touch_error_pct=touch_range_pct,
        prior_decline_pct=prior_decline_pct,
        volume_ratio=volume_ratio,
        volume_required=float(cfg["volume_surge_ratio"]),
        breakout=breakout,
        distance_to_pivot_pct=distance_to_pivot_pct,
        stop_distance_pct=stop_distance_pct,
    )
    grade = quality["total"]
    confidence = clip_confidence(
        55.0
        + min(15.0, len(touches) * 4.0)
        + min(12.0, prior_decline_pct * 0.25)
        + min(12.0, volume_ratio * 4.0)
        + (8.0 if breakout else 0.0)
    )
    label = "descending weekly trendline" if model == "descending_trendline" else "weekly resistance"

    return PatternResult(
        pattern="Weekly Breakout",
        status="BREAKING OUT" if breakout else "PIVOT READY",
        pivot=round(pivot, 2),
        target=round(target, 2),
        stop_loss=round(stop_loss, 2),
        confidence=confidence,
        explanation=(
            f"{len(touches)} touches against {label}; prior decline {prior_decline_pct:.1f}%; "
            f"weekly volume {volume_ratio:.2f}x avg; pattern grade {grade:.1f}/10."
        ),
        timeframe="weekly",
        bars_in_pattern=len(close),
        quality_score=confidence,
        extra={
            "weekly_breakout_model": model,
            "touch_indices": touches,
            "touch_error_pct": round(touch_range_pct, 3),
            "line_slope": round(line_slope, 4),
            "prior_decline_pct": round(prior_decline_pct, 2),
            "volume_ratio": round(volume_ratio, 2),
            "stop_distance_pct": round(stop_distance_pct, 2),
            "pattern_quality_score": grade,
            "pattern_quality_breakdown": quality["components"],
        },
    )


def _breakout_window_ok(latest_close: float, pivot: float, cfg: dict) -> bool:
    if pivot <= 0:
        return False
    if latest_close > pivot:
        extension_pct = (latest_close - pivot) / pivot * 100.0
        return extension_pct <= float(cfg["max_breakout_extension_pct"])
    distance_pct = (pivot - latest_close) / pivot * 100.0
    return distance_pct <= float(cfg["within_breakout_pct"])


def _line_at(left_high: float, slope: float, left_idx: int, idx: int) -> float:
    return left_high + slope * (idx - left_idx)


def _spread_fraction(offsets: np.ndarray, window_len: int) -> float:
    if len(offsets) < 2:
        return 0.0
    return (float(offsets[-1]) - float(offsets[0])) / max(1, window_len - 1)


def _mean_line_error_pct(high: np.ndarray, offsets: list[int], left_high: float, slope: float, left_idx: int) -> float:
    errors = []
    for idx in offsets:
        projected = _line_at(left_high, slope, left_idx, idx)
        if projected > 0:
            errors.append(abs(float(high[idx]) - projected) / projected * 100.0)
    return float(np.mean(errors)) if errors else 100.0


def _prior_decline_pct(high: np.ndarray, low: np.ndarray) -> float:
    if len(high) < 4:
        return 0.0
    midpoint = max(1, len(high) // 2)
    early_high = float(np.max(high[:midpoint]))
    later_low = float(np.min(low[midpoint:]))
    return (early_high - later_low) / early_high * 100.0 if early_high > 0 else 0.0


def _pattern_quality(
    *,
    model: str,
    touch_count: int,
    touch_error_pct: float,
    prior_decline_pct: float,
    volume_ratio: float,
    volume_required: float,
    breakout: bool,
    distance_to_pivot_pct: float,
    stop_distance_pct: float,
) -> dict:
    if touch_count >= 4:
        touch_pts = 2.0
    elif touch_count == 3:
        touch_pts = 1.5
    else:
        touch_pts = 1.0

    if touch_error_pct <= 1.5:
        clean_pts = 2.0
    elif touch_error_pct <= 3.0:
        clean_pts = 1.4
    elif touch_error_pct <= 5.0:
        clean_pts = 0.8
    else:
        clean_pts = 0.3

    if prior_decline_pct >= 35.0:
        context_pts = 1.5
    elif prior_decline_pct >= 25.0:
        context_pts = 1.1
    elif prior_decline_pct >= 18.0:
        context_pts = 0.7
    else:
        context_pts = 0.3

    if volume_ratio >= 2.0:
        vol_pts = 2.0
    elif volume_ratio >= 1.6:
        vol_pts = 1.5
    elif volume_ratio >= volume_required:
        vol_pts = 1.0
    elif volume_ratio >= 1.0:
        vol_pts = 0.5
    else:
        vol_pts = 0.0

    if breakout:
        prox_pts = 1.5
    elif distance_to_pivot_pct <= 1.0:
        prox_pts = 1.1
    elif distance_to_pivot_pct <= 2.0:
        prox_pts = 0.7
    else:
        prox_pts = 0.4

    if stop_distance_pct <= 8.0:
        stop_pts = 1.0
    elif stop_distance_pct <= 12.0:
        stop_pts = 0.7
    elif stop_distance_pct <= 18.0:
        stop_pts = 0.4
    else:
        stop_pts = 0.0

    model_pts = 0.0 if model == "horizontal_resistance" else 0.0
    total = touch_pts + clean_pts + context_pts + vol_pts + prox_pts + stop_pts + model_pts
    total = round(max(0.0, min(10.0, total)), 1)
    return {
        "total": total,
        "components": {
            "touch_count": round(touch_pts, 2),
            "line_cleanliness": round(clean_pts, 2),
            "reversal_context": round(context_pts, 2),
            "volume_surge": round(vol_pts, 2),
            "breakout_proximity": round(prox_pts, 2),
            "stop_tightness": round(stop_pts, 2),
        },
    }
