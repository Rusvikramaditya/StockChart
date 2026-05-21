"""Volatility Contraction Pattern detector."""

from __future__ import annotations

import numpy as np

from config import settings
from patterns.base import PatternResult
from patterns.utils import clip_confidence, has_ohlcv, is_stage2, series


def detect(daily: dict, weekly: dict | None = None) -> list[PatternResult]:
    cfg = settings.VCP
    lookback = 90
    if not has_ohlcv(daily, max(220, lookback)):
        return []

    high = series(daily, "high")
    low = series(daily, "low")
    close = series(daily, "close")
    volume = series(daily, "volume")
    if not is_stage2(close, settings.STAGE2):
        return []

    high_w = high[-lookback:]
    low_w = low[-lookback:]
    close_w = close[-lookback:]
    volume_w = volume[-lookback:]
    parts = np.array_split(np.arange(lookback), 3)
    contractions = []
    for part in parts:
        part_high = float(np.max(high_w[part]))
        part_low = float(np.min(low_w[part]))
        range_pct = (part_high - part_low) / part_high * 100.0 if part_high > 0 else 0.0
        contractions.append(range_pct)

    decreasing = all(contractions[i] < contractions[i - 1] for i in range(1, len(contractions)))
    if not decreasing or len(contractions) - 1 < int(cfg["min_contractions"]):
        return []
    if contractions[-1] > float(cfg["max_final_tightness_pct"]):
        return []
    if contractions[-2] > float(cfg["max_prior_tightness_pct"]):
        return []

    volume_declining = float(np.mean(volume_w[-20:])) < float(np.mean(volume_w[:20]))
    if bool(cfg["volume_declining"]) and not volume_declining:
        return []

    # Pivot is the pre-existing high over the last 30 bars excluding today,
    # so a breakout candle (today's close > prior high) is reachable.
    prior_high_window = high_w[-30:-1] if len(high_w) > 30 else high_w[:-1]
    pivot = float(np.max(prior_high_window)) if len(prior_high_window) else float(np.max(high_w))
    latest_close = float(close_w[-1])
    breakout = latest_close > pivot
    if not breakout and (pivot - latest_close) / pivot * 100.0 > 4.0:
        return []

    stop_loss = float(np.min(low_w[-20:]))
    target = pivot + (pivot - stop_loss) * 2.0
    confidence = 60.0 + max(0.0, 20.0 - contractions[-1] * 2.0)
    if volume_declining:
        confidence += 10.0
    if breakout:
        confidence += 8.0
    quality_score = clip_confidence(confidence)

    return [
        PatternResult(
            pattern="VCP",
            status="BREAKING OUT" if breakout else "PIVOT READY",
            pivot=round(pivot, 2),
            target=round(target, 2),
            stop_loss=round(stop_loss, 2),
            confidence=quality_score,
            explanation=(
                "Contractions tightened from "
                + " -> ".join(f"{item:.1f}%" for item in contractions)
                + "; volume declined."
            ),
            timeframe="daily",
            bars_in_pattern=lookback,
            quality_score=quality_score,
            extra={
                "contractions_pct": [round(item, 2) for item in contractions],
                "volume_declining": volume_declining,
                "stage2": True,
            },
        )
    ]
