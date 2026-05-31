"""Volume confirmation and base dry-up checks."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import numpy as np

from config import settings
from patterns.utils import series


def evaluate(data: dict, *, timeframe: str = "daily", avg_period: int | None = None) -> dict:
    """Return volume confirmation for daily or weekly OHLCV data.

    Defaults preserve the original daily 50-bar behavior. Weekly callers pass
    ``timeframe="weekly"`` and normally keep the same 50-bar lookback, which
    becomes a 50-week average.
    """

    timeframe_key = _normalise_timeframe(timeframe)
    volume = series(data, "volume")
    close = series(data, "close")
    period = int(avg_period or settings.VOLUME["avg_vol_period"])
    if len(volume) < period + 10 or len(close) < period + 10:
        details = _recent_volume_details(data, volume, None, timeframe_key, period)
        details.update(
            {
                "reason": f"need {period + 10} bars",
                "bars": len(volume),
                "timeframe": timeframe_key,
                "avg_period": period,
            }
        )
        return _result(
            False,
            details,
        )

    avg_volume = float(np.mean(volume[-period - 1 : -1]))
    latest_volume = float(volume[-1])
    breakout_ratio = latest_volume / avg_volume if avg_volume > 0 else 0.0
    recent_avg = float(np.mean(volume[-10:-1]))
    base_dry_up_ratio = recent_avg / avg_volume if avg_volume > 0 else 0.0
    price_change_pct = (
        (float(close[-1]) / float(close[-2]) - 1.0) * 100.0 if float(close[-2]) > 0 else 0.0
    )
    passed = breakout_ratio >= float(settings.VOLUME["breakout_vol_ratio"])
    dry_up = base_dry_up_ratio <= 0.8
    details = {
        "timeframe": timeframe_key,
        "avg_period": period,
        "latest_volume": int(latest_volume),
        "avg_volume": int(avg_volume),
        "avg_50d_volume": int(avg_volume),
        "breakout_volume_ratio": round(breakout_ratio, 2),
        "base_dry_up_ratio": round(base_dry_up_ratio, 2),
        "base_dry_up": dry_up,
        "price_change_pct": round(price_change_pct, 2),
    }
    details.update(_recent_volume_details(data, volume, avg_volume, timeframe_key, period))
    if timeframe_key == "weekly":
        details["avg_50w_volume"] = int(avg_volume)
    return _result(passed, details)


def _result(passed: bool, details: dict) -> dict:
    return {
        "name": "volume",
        "passed": bool(passed),
        "status": "PASS" if passed else ("DRY_UP" if details.get("base_dry_up") else "FAIL"),
        "details": details,
    }


def _normalise_timeframe(value: str) -> str:
    text = str(value or "daily").strip().lower()
    if text.startswith("week"):
        return "weekly"
    return "daily"


def _recent_volume_details(
    data: dict,
    volume: np.ndarray,
    avg_volume: float | None,
    timeframe: str,
    period: int,
) -> dict[str, Any]:
    latest_volume = float(volume[-1]) if len(volume) else 0.0
    latest_date = _date_at(data, len(volume) - 1) if len(volume) else None
    last_5 = [int(float(value)) for value in volume[-5:]]
    last_5_dates = [
        value
        for value in (_date_at(data, idx) for idx in range(max(0, len(volume) - 5), len(volume)))
        if value
    ]
    last_5_avg = float(np.mean(last_5)) if last_5 else 0.0
    recent_ratio = last_5_avg / avg_volume if avg_volume and avg_volume > 0 else 0.0
    return {
        "latest_volume": int(latest_volume),
        "latest_volume_date": latest_date,
        "latest_volume_is_today": latest_date == date.today().isoformat(),
        "last_5_volumes": last_5,
        "last_5_volume_dates": last_5_dates,
        "last_5_avg_volume": int(last_5_avg),
        "last_5_vs_avg_ratio": round(recent_ratio, 2),
        "recent_volume_direction": _recent_volume_direction(recent_ratio),
        "timeframe": timeframe,
        "avg_period": period,
    }


def _recent_volume_direction(ratio: float) -> str:
    if ratio >= 1.05:
        return "higher"
    if ratio <= 0.95 and ratio > 0:
        return "lower"
    if ratio > 0:
        return "near_average"
    return "unknown"


def _date_at(data: dict, idx: int) -> str | None:
    values = data.get("date")
    if values is None:
        values = data.get("week")
    if values is None or idx < 0 or len(values) <= idx:
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
