"""Multi-Year Breakout detector."""

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
    resistance_window = high_w[:-1]
    if len(resistance_window) < min_bars:
        return []

    resistance = float(np.max(resistance_window))
    if resistance <= 0:
        return []
    tolerance = float(cfg["resistance_tolerance_pct"])
    touch_idx = np.flatnonzero(np.abs(resistance_window - resistance) / resistance * 100.0 <= tolerance)
    if len(touch_idx) < int(cfg["min_touches"]):
        return []

    latest_close = float(close_w[-1])
    breakout = latest_close > resistance
    if not breakout and (resistance - latest_close) / resistance * 100.0 > tolerance:
        return []

    avg_volume = float(np.mean(volume_w[-51:-1])) if len(volume_w) >= 51 else float(np.mean(volume_w[:-1]))
    volume_ratio = float(volume_w[-1] / avg_volume) if avg_volume > 0 else 0.0
    if volume_ratio < float(cfg["volume_surge_ratio"]):
        return []

    base_low = float(np.min(low_w))
    target = resistance + (resistance - base_low) * 0.5
    stop_loss = float(np.min(low_w[-12:]))
    confidence = 60.0 + min(18.0, len(touch_idx) * 4.0) + min(15.0, volume_ratio * 4.0)
    if breakout:
        confidence += 7.0
    quality_score = clip_confidence(confidence)

    return [
        PatternResult(
            pattern="Multi-Year Breakout",
            status="BREAKING OUT" if breakout else "PIVOT READY",
            pivot=round(resistance, 2),
            target=round(target, 2),
            stop_loss=round(stop_loss, 2),
            confidence=quality_score,
            explanation=(
                f"{len(touch_idx)} weekly resistance touches near {resistance:.2f}; "
                f"volume surge {volume_ratio:.2f}x."
            ),
            timeframe="weekly",
            bars_in_pattern=lookback,
            quality_score=quality_score,
            extra={
                "resistance_touch_indices": touch_idx.tolist(),
                "volume_ratio": round(volume_ratio, 2),
                "years": round(lookback / 52.0, 2),
            },
        )
    ]
