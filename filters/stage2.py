"""Stage 2 uptrend filter."""

from __future__ import annotations

import numpy as np

from config import settings
from patterns.utils import moving_average, series


def evaluate(daily: dict) -> dict:
    close = series(daily, "close")
    high = series(daily, "high")
    low = series(daily, "low")
    cfg = settings.STAGE2
    ma_short_period = int(cfg["ma_short"])
    ma_long_period = int(cfg["ma_long"])
    slope_lookback = int(cfg["slope_lookback"])
    minimum = max(ma_long_period + slope_lookback, 252)
    if len(close) < minimum:
        return _result(False, {"reason": f"need {minimum} daily bars", "bars": len(close)})

    ma_short = moving_average(close, ma_short_period)
    ma_long = moving_average(close, ma_long_period)
    latest_close = float(close[-1])
    ma_short_latest = float(ma_short[-1])
    ma_long_latest = float(ma_long[-1])
    ma_short_prior = float(ma_short[-1 - slope_lookback])
    # Use intraday high / low for 52-week levels (O'Neil, Bulkowski
    # convention). The previous close-based computation rejected valid
    # candidates within 25% of the true 52w high.
    high_52w = float(np.max(high[-252:])) if len(high) >= 252 else float(np.max(high))
    low_52w = float(np.min(low[-252:])) if len(low) >= 252 else float(np.min(low))

    checks = {
        "close_gt_150ma": latest_close > ma_short_latest,
        "close_gt_200ma": latest_close > ma_long_latest,
        "150ma_gt_200ma": ma_short_latest > ma_long_latest,
        "150ma_slope_positive": ma_short_latest > ma_short_prior,
        "within_25pct_52w_high": (
            (high_52w - latest_close) / high_52w * 100.0 <= cfg["max_from_52w_high_pct"]
            if high_52w > 0
            else False
        ),
        "at_least_30pct_above_52w_low": (
            (latest_close - low_52w) / low_52w * 100.0 >= cfg["min_from_52w_low_pct"]
            if low_52w > 0
            else False
        ),
    }
    passed = all(checks.values())
    details = {
        "checks": checks,
        "close": round(latest_close, 2),
        "ma150": round(ma_short_latest, 2),
        "ma200": round(ma_long_latest, 2),
        "ma150_slope": round(ma_short_latest - ma_short_prior, 4),
        "from_52w_high_pct": round((high_52w - latest_close) / high_52w * 100.0, 2) if high_52w > 0 else None,
        "above_52w_low_pct": round((latest_close - low_52w) / low_52w * 100.0, 2) if low_52w > 0 else None,
    }
    return _result(passed, details)


def _result(passed: bool, details: dict) -> dict:
    return {
        "name": "stage2",
        "passed": bool(passed),
        "status": "PASS" if passed else "FAIL",
        "details": details,
    }
