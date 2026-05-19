"""Ascending Triangle detector."""

from __future__ import annotations

import numpy as np

from config import settings
from patterns.base import PatternResult
from patterns.utils import clip_confidence, has_ohlcv, local_lows, series


def detect(daily: dict, weekly: dict | None = None) -> list[PatternResult]:
    cfg = settings.ASCENDING_TRIANGLE
    lookback = int(cfg["lookback_bars"])
    if not has_ohlcv(daily, lookback):
        return []

    high = series(daily, "high")[-lookback:]
    low = series(daily, "low")[-lookback:]
    close = series(daily, "close")[-lookback:]
    volume = series(daily, "volume")[-lookback:]
    resistance = float(np.max(high))
    if resistance <= 0:
        return []

    tolerance_pct = float(cfg["resistance_tolerance_pct"])
    touch_mask = np.abs(high - resistance) / resistance * 100.0 <= tolerance_pct
    touch_idx = np.flatnonzero(touch_mask)
    if len(touch_idx) < int(cfg["min_resistance_touches"]):
        return []

    lows_idx = local_lows(low, int(cfg["argrelextrema_order"]))
    if len(lows_idx) < int(cfg["min_rising_lows"]):
        return []
    recent_lows = lows_idx[-4:]
    low_values = low[recent_lows]
    rising_pairs = sum(float(low_values[i]) > float(low_values[i - 1]) for i in range(1, len(low_values)))
    if rising_pairs < int(cfg["min_rising_lows"]) - 1:
        return []

    latest_close = float(close[-1])
    distance_to_pivot = (resistance - latest_close) / resistance * 100.0
    breakout = latest_close > resistance
    if not breakout and distance_to_pivot > float(cfg["within_breakout_pct"]):
        return []

    base_low = float(np.min(low_values))
    target = resistance + max(resistance - base_low, 0.0)
    stop_loss = float(low_values[-1])
    avg_vol = float(np.mean(volume[-50:])) if len(volume) >= 50 else float(np.mean(volume))
    volume_ratio = float(volume[-1] / avg_vol) if avg_vol > 0 else 0.0
    confidence = 55.0 + min(15.0, len(touch_idx) * 3.0) + min(15.0, rising_pairs * 7.5)
    if breakout:
        confidence += 10.0
    if volume_ratio >= 1.2:
        confidence += 5.0

    return [
        PatternResult(
            pattern="Ascending Triangle",
            status="BREAKING OUT" if breakout else "PIVOT READY",
            pivot=round(resistance, 2),
            target=round(target, 2),
            stop_loss=round(stop_loss, 2),
            confidence=clip_confidence(confidence),
            explanation=(
                f"{len(touch_idx)} resistance touches near {resistance:.2f}; "
                f"{rising_pairs + 1} rising-low points found."
            ),
            timeframe="daily",
            bars_in_pattern=lookback,
            extra={
                "touch_indices": touch_idx.tolist(),
                "low_indices": recent_lows.tolist(),
                "resistance_tolerance_pct": tolerance_pct,
                "volume_ratio": round(volume_ratio, 2),
            },
        )
    ]

