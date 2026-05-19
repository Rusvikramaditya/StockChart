"""Supertrend bullish flip detector."""

from __future__ import annotations

import numpy as np

from config import settings
from patterns.base import PatternResult
from patterns.utils import clip_confidence, has_ohlcv, series


def detect(daily: dict, weekly: dict | None = None) -> list[PatternResult]:
    cfg = settings.SUPERTREND
    period = int(cfg["atr_period"])
    min_bars = period + 20
    if not has_ohlcv(daily, min_bars):
        return []

    high = series(daily, "high")
    low = series(daily, "low")
    close = series(daily, "close")
    atr = _atr(high, low, close, period)
    if len(atr) == 0:
        return []

    line, direction = _supertrend(high, low, close, atr, float(cfg["multiplier"]))
    lookback = int(cfg["flip_lookback_bars"])
    if len(direction) < lookback + 1:
        return []

    flip_idx = None
    for idx in range(len(direction) - lookback, len(direction)):
        if idx <= 0:
            continue
        if direction[idx] == 1 and direction[idx - 1] == -1:
            flip_idx = idx
            break
    if flip_idx is None:
        return []

    latest_close = float(close[-1])
    latest_atr = float(atr[-1])
    stop_loss = float(line[-1])
    if stop_loss <= 0 or latest_close <= stop_loss:
        return []
    target = latest_close + 2.0 * latest_atr
    confidence = 62.0 + min(18.0, (latest_close - stop_loss) / latest_close * 100.0 * 2.0)

    return [
        PatternResult(
            pattern="Supertrend Bullish Flip",
            status="BREAKING OUT",
            pivot=round(latest_close, 2),
            target=round(target, 2),
            stop_loss=round(stop_loss, 2),
            confidence=clip_confidence(confidence),
            explanation=(
                f"Supertrend flipped bullish {len(close) - 1 - flip_idx} bars ago; "
                f"ATR({period}) is {latest_atr:.2f}."
            ),
            timeframe="daily",
            bars_in_pattern=period + lookback,
            extra={
                "flip_idx": int(flip_idx),
                "atr": round(latest_atr, 2),
                "supertrend": round(stop_loss, 2),
                "multiplier": float(cfg["multiplier"]),
            },
        )
    ]


def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    true_range = np.maximum.reduce(
        [
            high - low,
            np.abs(high - prev_close),
            np.abs(low - prev_close),
        ]
    )
    atr = np.zeros_like(close, dtype=float)
    if len(close) < period:
        return np.array([], dtype=float)
    atr[:period] = np.mean(true_range[:period])
    for idx in range(period, len(close)):
        atr[idx] = (atr[idx - 1] * (period - 1) + true_range[idx]) / period
    return atr


def _supertrend(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    atr: np.ndarray,
    multiplier: float,
) -> tuple[np.ndarray, np.ndarray]:
    hl2 = (high + low) / 2.0
    upper = hl2 + multiplier * atr
    lower = hl2 - multiplier * atr
    final_upper = upper.copy()
    final_lower = lower.copy()
    direction = np.ones(len(close), dtype=int)
    line = np.zeros(len(close), dtype=float)

    for idx in range(1, len(close)):
        if upper[idx] < final_upper[idx - 1] or close[idx - 1] > final_upper[idx - 1]:
            final_upper[idx] = upper[idx]
        else:
            final_upper[idx] = final_upper[idx - 1]

        if lower[idx] > final_lower[idx - 1] or close[idx - 1] < final_lower[idx - 1]:
            final_lower[idx] = lower[idx]
        else:
            final_lower[idx] = final_lower[idx - 1]

        if close[idx] > final_upper[idx - 1]:
            direction[idx] = 1
        elif close[idx] < final_lower[idx - 1]:
            direction[idx] = -1
        else:
            direction[idx] = direction[idx - 1]

        line[idx] = final_lower[idx] if direction[idx] == 1 else final_upper[idx]

    line[0] = final_lower[0]
    return line, direction

