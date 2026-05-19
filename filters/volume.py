"""Volume confirmation and base dry-up checks."""

from __future__ import annotations

import numpy as np

from config import settings
from patterns.utils import series


def evaluate(daily: dict) -> dict:
    volume = series(daily, "volume")
    close = series(daily, "close")
    period = int(settings.VOLUME["avg_vol_period"])
    if len(volume) < period + 10 or len(close) < period + 10:
        return _result(False, {"reason": f"need {period + 10} bars", "bars": len(volume)})

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
        "latest_volume": int(latest_volume),
        "avg_50d_volume": int(avg_volume),
        "breakout_volume_ratio": round(breakout_ratio, 2),
        "base_dry_up_ratio": round(base_dry_up_ratio, 2),
        "base_dry_up": dry_up,
        "price_change_pct": round(price_change_pct, 2),
    }
    return _result(passed, details)


def _result(passed: bool, details: dict) -> dict:
    return {
        "name": "volume",
        "passed": bool(passed),
        "status": "PASS" if passed else ("DRY_UP" if details.get("base_dry_up") else "FAIL"),
        "details": details,
    }
