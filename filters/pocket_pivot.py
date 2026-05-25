"""Pocket-pivot style institutional accumulation confirmation."""

from __future__ import annotations

import numpy as np

from config import settings
from patterns.utils import moving_average, series


def evaluate(daily: dict) -> dict:
    """Return whether the latest bar shows early accumulation.

    This is intentionally a confirmation filter, not a standalone pattern.
    A pass means the latest up-close volume exceeded the heaviest down-volume
    in the prior lookback window while price closed in the upper part of the
    candle and stayed above key moving averages.
    """

    cfg = settings.POCKET_PIVOT
    min_bars = int(cfg["min_history_bars"])
    lookback = int(cfg["max_down_day_lookback"])
    close = series(daily, "close")
    open_ = series(daily, "open")
    high = series(daily, "high")
    low = series(daily, "low")
    volume = series(daily, "volume")
    if min(len(close), len(open_), len(high), len(low), len(volume)) < max(min_bars, lookback + 2):
        return _result(False, "INSUFFICIENT_DATA", {"reason": f"need {max(min_bars, lookback + 2)} bars"})

    latest_close = float(close[-1])
    latest_open = float(open_[-1])
    latest_high = float(high[-1])
    latest_low = float(low[-1])
    latest_volume = float(volume[-1])
    if latest_close <= latest_open or latest_volume <= 0:
        return _result(False, "NO_UP_CLOSE", {"latest_volume": int(latest_volume)})

    prior_slice = slice(-lookback - 1, -1)
    prior_down_volume = [
        float(volume[idx])
        for idx in range(len(close) + prior_slice.start, len(close) - 1)
        if float(close[idx]) < float(close[idx - 1])
    ]
    max_down_volume = max(prior_down_volume) if prior_down_volume else 0.0
    volume_pass = latest_volume > max_down_volume if max_down_volume > 0 else False

    candle_range = max(latest_high - latest_low, 1e-9)
    close_range_pct = (latest_close - latest_low) / candle_range * 100.0
    range_pass = close_range_pct >= float(cfg["close_range_min_pct"])
    ma_pass = _above_moving_averages(close, tuple(int(p) for p in cfg["ma_periods"]))

    details = {
        "latest_volume": int(latest_volume),
        "max_down_volume_lookback": int(max_down_volume),
        "volume_ratio_vs_down_day": round(latest_volume / max_down_volume, 2) if max_down_volume > 0 else 0.0,
        "close_range_pct": round(close_range_pct, 2),
        "above_key_mas": ma_pass,
    }
    passed = volume_pass and range_pass and ma_pass
    return _result(passed, "PASS" if passed else "FAIL", details)


def _above_moving_averages(close: np.ndarray, periods: tuple[int, ...]) -> bool:
    latest_close = float(close[-1])
    for period in periods:
        ma = moving_average(close, period)
        if len(ma) == 0 or latest_close <= float(ma[-1]):
            return False
    return True


def _result(passed: bool, status: str, details: dict) -> dict:
    return {
        "name": "pocket_pivot",
        "passed": bool(passed),
        "status": status,
        "details": details,
    }
