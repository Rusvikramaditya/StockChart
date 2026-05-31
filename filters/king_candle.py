"""King Candle confirmation context for bullish setups."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import numpy as np

from config import settings
from patterns.utils import series


def evaluate(daily: dict) -> dict:
    """Return whether a recent bullish King Candle was observed.

    This is informational confirmation only. It does not create a standalone
    pattern and does not change conviction scoring.
    """

    cfg = settings.KING_CANDLE
    lookback = int(cfg["lookback_bars"])
    confirmation_bars = int(cfg["confirmation_bars"])
    open_ = series(daily, "open")
    high = series(daily, "high")
    low = series(daily, "low")
    close = series(daily, "close")
    volume = series(daily, "volume")
    count = min(len(open_), len(high), len(low), len(close), len(volume))
    if count < lookback + 1:
        return _result(False, "INSUFFICIENT_DATA", {"reason": f"need {lookback + 1} bars", "bars": count})

    start = max(lookback, count - confirmation_bars - 1)
    best: dict[str, Any] | None = None
    for idx in range(start, count):
        candidate = _candidate_details(daily, open_, high, low, close, volume, idx, lookback, cfg)
        if candidate["observed"]:
            best = candidate

    if best is None:
        return _result(False, "NOT_OBSERVED", {"lookback_bars": lookback})

    status = _follow_through_status(close, best, confirmation_bars)
    best["follow_through_status"] = status
    best["confirmation_bars"] = min(confirmation_bars, count - int(best["candle_index"]) - 1)
    passed = status in {"CONFIRMED", "HOLDING_MIDPOINT", "PENDING_FOLLOW_THROUGH"}
    return _result(passed, status, best)


def _candidate_details(
    daily: dict,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    idx: int,
    lookback: int,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    prior = slice(idx - lookback, idx)
    candle_open = float(open_[idx])
    candle_high = float(high[idx])
    candle_low = float(low[idx])
    candle_close = float(close[idx])
    candle_volume = float(volume[idx])
    candle_range = max(candle_high - candle_low, 1e-9)
    candle_body = abs(candle_close - candle_open)
    prior_ranges = np.maximum(high[prior] - low[prior], 1e-9)
    prior_bodies = np.maximum(np.abs(close[prior] - open_[prior]), 1e-9)
    avg_prior_range = float(np.mean(prior_ranges))
    avg_prior_body = float(np.mean(prior_bodies))
    prior_high = float(np.max(high[prior]))
    avg_volume = _prior_average(volume, idx, int(settings.VOLUME["avg_vol_period"]))
    volume_ratio = candle_volume / avg_volume if avg_volume > 0 else 0.0

    body_pct = candle_body / candle_range * 100.0
    close_position_pct = (candle_close - candle_low) / candle_range * 100.0
    range_ratio = candle_range / avg_prior_range if avg_prior_range > 0 else 0.0
    body_ratio = candle_body / avg_prior_body if avg_prior_body > 0 else 0.0
    range_pct_price = candle_range / candle_close * 100.0 if candle_close > 0 else 0.0

    bullish = candle_close > candle_open
    shape_ok = (
        bullish
        and body_pct >= float(cfg["min_body_pct"])
        and close_position_pct >= float(cfg["min_close_position_pct"])
        and range_ratio >= float(cfg["min_range_vs_prior"])
        and body_ratio >= float(cfg["min_body_vs_prior"])
    )
    breakout_ok = candle_close > prior_high
    volume_ok = volume_ratio >= float(cfg["min_volume_ratio"])
    not_extended = range_pct_price <= float(cfg["max_range_pct_of_price"])
    observed = shape_ok and breakout_ok and volume_ok and not_extended

    return {
        "observed": bool(observed),
        "direction": "bullish" if bullish else "bearish",
        "candle_index": int(idx),
        "candle_offset": int(idx - len(close) + 1),
        "candle_date": _date_at(daily, idx),
        "king_high": round(candle_high, 2),
        "king_low": round(candle_low, 2),
        "king_midpoint": round((candle_high + candle_low) / 2.0, 2),
        "body_pct": round(body_pct, 2),
        "close_position_pct": round(close_position_pct, 2),
        "range_ratio_vs_prior": round(range_ratio, 2),
        "body_ratio_vs_prior": round(body_ratio, 2),
        "volume_ratio": round(volume_ratio, 2),
        "range_pct_of_price": round(range_pct_price, 2),
        "breakout_close": round(candle_close, 2),
        "prior_high": round(prior_high, 2),
    }


def _follow_through_status(close: np.ndarray, details: dict[str, Any], confirmation_bars: int) -> str:
    idx = int(details["candle_index"])
    if idx >= len(close) - 1:
        return "PENDING_FOLLOW_THROUGH"
    end = min(len(close), idx + confirmation_bars + 1)
    follow_closes = close[idx + 1 : end]
    midpoint = float(details["king_midpoint"])
    high = float(details["king_high"])
    if np.any(follow_closes < midpoint):
        return "FAILED_MIDPOINT"
    if np.any(follow_closes > high):
        return "CONFIRMED"
    return "HOLDING_MIDPOINT"


def _prior_average(values: np.ndarray, idx: int, period: int) -> float:
    start = max(0, idx - period)
    window = values[start:idx]
    return float(np.mean(window)) if len(window) else 0.0


def _date_at(data: dict, idx: int) -> str | None:
    values = data.get("date")
    if values is None:
        values = data.get("week")
    if values is None or len(values) <= idx:
        return None
    value = values[idx]
    if isinstance(value, np.datetime64):
        return str(np.datetime_as_string(value, unit="D"))
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    return text[:10] or None


def _result(passed: bool, status: str, details: dict[str, Any]) -> dict:
    return {
        "name": "king_candle",
        "passed": bool(passed),
        "status": status,
        "details": details,
    }
