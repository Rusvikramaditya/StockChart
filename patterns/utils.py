"""Small numerical helpers shared by pattern detectors."""

from __future__ import annotations

import numpy as np
from scipy.signal import argrelextrema


def series(data: dict, key: str) -> np.ndarray:
    values = np.asarray(data.get(key, []), dtype=float)
    if values.ndim != 1:
        values = values.reshape(-1)
    return values


def has_ohlcv(data: dict, min_bars: int) -> bool:
    return all(len(series(data, key)) >= min_bars for key in ["open", "high", "low", "close", "volume"])


def last_finite(values: np.ndarray) -> float | None:
    clean = values[np.isfinite(values)]
    if clean.size == 0:
        return None
    return float(clean[-1])


def pct_change(start: float, end: float) -> float:
    if start <= 0:
        return 0.0
    return (end / start - 1.0) * 100.0


def pct_distance(a: float, b: float) -> float:
    base = max(abs(a), abs(b), 1e-9)
    return abs(a - b) / base * 100.0


def moving_average(values: np.ndarray, period: int) -> np.ndarray:
    if len(values) < period:
        return np.array([], dtype=float)
    weights = np.ones(period) / period
    return np.convolve(values, weights, mode="valid")


def local_lows(values: np.ndarray, order: int) -> np.ndarray:
    if len(values) < order * 2 + 1:
        return np.array([], dtype=int)
    return argrelextrema(values, np.less_equal, order=order)[0]


def local_highs(values: np.ndarray, order: int) -> np.ndarray:
    if len(values) < order * 2 + 1:
        return np.array([], dtype=int)
    return argrelextrema(values, np.greater_equal, order=order)[0]


def is_stage2(close: np.ndarray, settings: dict) -> bool:
    ma_short_period = int(settings["ma_short"])
    ma_long_period = int(settings["ma_long"])
    slope_lookback = int(settings["slope_lookback"])
    if len(close) < ma_long_period + slope_lookback:
        return False
    ma_short = moving_average(close, ma_short_period)
    ma_long = moving_average(close, ma_long_period)
    if len(ma_short) <= slope_lookback or len(ma_long) == 0:
        return False
    latest_close = float(close[-1])
    latest_short = float(ma_short[-1])
    latest_long = float(ma_long[-1])
    short_slope = latest_short - float(ma_short[-1 - slope_lookback])
    high_52w = float(np.max(close[-252:]))
    low_52w = float(np.min(close[-252:]))
    within_high = (high_52w - latest_close) / high_52w * 100.0 <= settings["max_from_52w_high_pct"]
    above_low = (latest_close - low_52w) / low_52w * 100.0 >= settings["min_from_52w_low_pct"] if low_52w > 0 else False
    return latest_close > latest_short > latest_long and short_slope > 0 and within_high and above_low


def clip_confidence(value: float) -> float:
    return round(float(max(0.0, min(100.0, value))), 2)

