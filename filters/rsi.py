"""RSI overlay for conviction scoring."""

from __future__ import annotations

import numpy as np

from config import settings
from patterns.utils import local_highs, series


def compute_rsi(close: np.ndarray, period: int | None = None) -> np.ndarray:
    period = int(period or settings.RSI["period"])
    close = np.asarray(close, dtype=float)
    if len(close) < period + 1:
        return np.array([], dtype=float)
    delta = np.diff(close)
    gains = np.where(delta > 0, delta, 0.0)
    losses = np.where(delta < 0, -delta, 0.0)
    rsi = np.full(len(close), np.nan, dtype=float)
    avg_gain = float(np.mean(gains[:period]))
    avg_loss = float(np.mean(losses[:period]))
    rsi[period] = _rsi_value(avg_gain, avg_loss)
    for idx in range(period + 1, len(close)):
        avg_gain = (avg_gain * (period - 1) + gains[idx - 1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[idx - 1]) / period
        rsi[idx] = _rsi_value(avg_gain, avg_loss)
    return rsi


def evaluate(daily: dict) -> dict:
    close = series(daily, "close")
    period = int(settings.RSI["period"])
    rsi_values = compute_rsi(close, period)
    if len(rsi_values) == 0 or np.isnan(rsi_values[-1]):
        return {
            "name": "rsi",
            "value": None,
            "penalty": 0,
            "status": "UNKNOWN",
            "bearish_divergence": False,
            "details": {"reason": f"need {period + 1} bars"},
        }

    latest = float(rsi_values[-1])
    penalty = 0
    status = "HEALTHY"
    if latest < float(settings.RSI["penalty_weak"]["threshold"]):
        penalty += int(settings.RSI["penalty_weak"]["penalty"])
        status = "WEAK"
    elif latest > float(settings.RSI["penalty_overbought"]["threshold"]):
        penalty += int(settings.RSI["penalty_overbought"]["penalty"])
        status = "OVERBOUGHT"
    elif not (settings.RSI["healthy_low"] <= latest <= settings.RSI["healthy_high"]):
        status = "NEUTRAL"

    divergence = bearish_divergence(close, rsi_values, period)
    if divergence:
        penalty += int(settings.RSI["penalty_divergence"])
        status = "DIVERGENCE"

    return {
        "name": "rsi",
        "value": round(latest, 2),
        "penalty": penalty,
        "status": status,
        "bearish_divergence": divergence,
        "details": {
            "period": period,
            "healthy_range": [settings.RSI["healthy_low"], settings.RSI["healthy_high"]],
        },
    }


def bearish_divergence(close: np.ndarray, rsi_values: np.ndarray, period: int) -> bool:
    lookback = max(period * 2, 30)
    if len(close) < lookback or len(rsi_values) < lookback:
        return False
    close_w = close[-lookback:]
    rsi_w = rsi_values[-lookback:]
    highs = local_highs(close_w, 3)
    highs = [idx for idx in highs if not np.isnan(rsi_w[idx])]
    if len(highs) < 2:
        return False
    first, second = highs[-2], highs[-1]
    return bool(close_w[second] > close_w[first] and rsi_w[second] < rsi_w[first])


def _rsi_value(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))
