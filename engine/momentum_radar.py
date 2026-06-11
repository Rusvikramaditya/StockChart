"""Watch-only momentum radar for strong names outside strict setup alerts."""

from __future__ import annotations

from typing import Any

import numpy as np

from config import settings
from patterns.utils import moving_average, series


def evaluate(
    symbol: str,
    daily: dict,
    weekly: dict | None = None,
    *,
    company_name: str | None = None,
    skip_reason: str | None = None,
) -> dict[str, Any] | None:
    """Return a watch-only radar row when price action is strong enough.

    This intentionally does not create a PatternResult. Radar names are not
    trade alerts; they are a separate list of strong/near-breakout stocks that
    failed to produce a clean actionable setup.
    """

    close = series(daily, "close")
    high = series(daily, "high")
    low = series(daily, "low")
    volume = series(daily, "volume")
    dates = daily.get("date", [])
    if min(len(close), len(high), len(low), len(volume)) < 60:
        return None

    latest_close = _last_float(close)
    prior_close = _last_float(close[:-1])
    if latest_close is None or latest_close <= 0:
        return None

    high_52w = float(np.max(high[-252:])) if len(high) >= 2 else float(np.max(high))
    low_52w = float(np.min(low[-252:])) if len(low) >= 2 else float(np.min(low))
    from_52w_high_pct = (high_52w - latest_close) / high_52w * 100.0 if high_52w > 0 else None
    above_52w_low_pct = (latest_close - low_52w) / low_52w * 100.0 if low_52w > 0 else None
    one_day_change_pct = (
        (latest_close / prior_close - 1.0) * 100.0
        if prior_close is not None and prior_close > 0
        else None
    )
    return_20d_pct = _lookback_return(close, 20)
    return_63d_pct = _lookback_return(close, 63)
    daily_volume_ratio = _volume_ratio(volume, 50)

    weekly_info = _weekly_context(weekly, latest_close)
    stage2_info = _stage2_context(close, high, low)

    score = 0.0
    reasons: list[str] = []

    if from_52w_high_pct is not None:
        if from_52w_high_pct <= 3.0:
            score += 20
            reasons.append("near 52W high")
        elif from_52w_high_pct <= 8.0:
            score += 16
            reasons.append("close to 52W high")
        elif from_52w_high_pct <= 15.0:
            score += 8

    if daily_volume_ratio is not None:
        if daily_volume_ratio >= 2.0:
            score += 22
            reasons.append("daily volume surge")
        elif daily_volume_ratio >= 1.4:
            score += 16
            reasons.append("daily volume expansion")
        elif daily_volume_ratio >= 1.1:
            score += 8

    weekly_volume_ratio = weekly_info.get("weekly_volume_ratio")
    if weekly_volume_ratio is not None:
        if weekly_volume_ratio >= 2.0:
            score += 16
            reasons.append("weekly volume surge")
        elif weekly_volume_ratio >= 1.4:
            score += 12
            reasons.append("weekly volume expansion")
        elif weekly_volume_ratio >= 1.1:
            score += 6

    if stage2_info["passed"]:
        score += 18
        reasons.append("Stage 2 uptrend")
    elif stage2_info["forming"]:
        score += 10
        reasons.append("Stage 2 forming")
    elif stage2_info["above_ma50"]:
        score += 5

    if one_day_change_pct is not None:
        if one_day_change_pct >= 5.0:
            score += 12
            reasons.append("strong latest candle")
        elif one_day_change_pct >= 2.0:
            score += 7

    if return_20d_pct is not None:
        if return_20d_pct >= 15.0:
            score += 12
            reasons.append("strong 20D momentum")
        elif return_20d_pct >= 7.0:
            score += 7

    if return_63d_pct is not None:
        if return_63d_pct >= 25.0:
            score += 10
            reasons.append("strong 3M momentum")
        elif return_63d_pct >= 12.0:
            score += 6

    resistance_distance = weekly_info.get("weekly_resistance_distance_pct")
    resistance_extension = weekly_info.get("weekly_resistance_extension_pct")
    if resistance_extension is not None:
        if resistance_extension <= 8.0:
            score += 14
            reasons.append("above weekly resistance")
        elif resistance_extension <= 12.0:
            score += 8
    elif resistance_distance is not None:
        if resistance_distance <= 3.0:
            score += 12
            reasons.append("near weekly pivot")
        elif resistance_distance <= 6.0:
            score += 8

    score = round(min(100.0, score), 1)
    if score < float(settings.MOMENTUM_RADAR["min_score"]):
        return None
    if not _has_core_strength(
        from_52w_high_pct=from_52w_high_pct,
        daily_volume_ratio=daily_volume_ratio,
        weekly_volume_ratio=weekly_volume_ratio,
        weekly_info=weekly_info,
    ):
        return None

    watch_notes = _watch_notes(
        daily_volume_ratio=daily_volume_ratio,
        stage2_info=stage2_info,
        weekly_info=weekly_info,
        skip_reason=skip_reason,
    )

    return {
        "symbol": str(symbol or "").upper(),
        "company_name": company_name or "",
        "score": score,
        "status": _status(score),
        "action": "WATCH ONLY",
        "cmp": round(latest_close, 2),
        "latest_date": _date_text(dates[-1]) if len(dates) else "",
        "one_day_change_pct": _round(one_day_change_pct),
        "return_20d_pct": _round(return_20d_pct),
        "return_63d_pct": _round(return_63d_pct),
        "from_52w_high_pct": _round(from_52w_high_pct),
        "above_52w_low_pct": _round(above_52w_low_pct),
        "daily_volume_ratio": _round(daily_volume_ratio),
        "stage2_status": "PASS" if stage2_info["passed"] else ("FORMING" if stage2_info["forming"] else "FAIL"),
        "reasons": reasons[:5],
        "watch_notes": watch_notes,
        **weekly_info,
    }


def _stage2_context(close: np.ndarray, high: np.ndarray, low: np.ndarray) -> dict[str, bool]:
    out = {"passed": False, "forming": False, "above_ma50": False}
    if len(close) < 200:
        return out

    ma50 = moving_average(close, 50)
    ma150 = moving_average(close, 150)
    ma200 = moving_average(close, 200)
    if len(ma50):
        out["above_ma50"] = float(close[-1]) > float(ma50[-1])
    if len(ma150) == 0 or len(ma200) == 0:
        return out

    slope_lookback = min(20, len(ma150) - 1)
    high_52w = float(np.max(high[-252:])) if len(high) >= 252 else float(np.max(high))
    low_52w = float(np.min(low[-252:])) if len(low) >= 252 else float(np.min(low))
    latest_close = float(close[-1])
    checks = {
        "close_gt_150ma": latest_close > float(ma150[-1]),
        "close_gt_200ma": latest_close > float(ma200[-1]),
        "150ma_gt_200ma": float(ma150[-1]) > float(ma200[-1]),
        "150ma_slope_positive": float(ma150[-1]) > float(ma150[-1 - slope_lookback]),
        "within_25pct_52w_high": (high_52w - latest_close) / high_52w * 100.0 <= 25.0 if high_52w > 0 else False,
        "at_least_30pct_above_52w_low": (latest_close - low_52w) / low_52w * 100.0 >= 30.0 if low_52w > 0 else False,
    }
    out["passed"] = all(checks.values())
    out["forming"] = (
        checks["close_gt_150ma"]
        and checks["close_gt_200ma"]
        and checks["150ma_slope_positive"]
        and checks["within_25pct_52w_high"]
    )
    return out


def _weekly_context(weekly: dict | None, latest_close: float) -> dict[str, Any]:
    out: dict[str, Any] = {
        "weekly_resistance": None,
        "weekly_resistance_distance_pct": None,
        "weekly_resistance_extension_pct": None,
        "weekly_stop_distance_pct": None,
        "weekly_volume_ratio": None,
    }
    if not weekly:
        return out
    high = series(weekly, "high")
    low = series(weekly, "low")
    volume = series(weekly, "volume")
    if min(len(high), len(low), len(volume)) < 12:
        return out

    lookback = min(len(high), int(settings.WEEKLY_BREAKOUT["lookback_weeks"]))
    prior_high = high[-lookback:-1]
    if len(prior_high) == 0:
        return out
    resistance = float(np.max(prior_high))
    if resistance <= 0:
        return out
    out["weekly_resistance"] = round(resistance, 2)
    if latest_close > resistance:
        out["weekly_resistance_extension_pct"] = round((latest_close - resistance) / resistance * 100.0, 2)
    else:
        out["weekly_resistance_distance_pct"] = round((resistance - latest_close) / resistance * 100.0, 2)

    stop_window = min(len(low), int(settings.WEEKLY_BREAKOUT["stop_lookback_weeks"]))
    stop = float(np.min(low[-stop_window:]))
    if stop > 0 and stop < resistance:
        out["weekly_stop_distance_pct"] = round((resistance - stop) / resistance * 100.0, 2)
    out["weekly_volume_ratio"] = _round(_volume_ratio(volume, 50))
    return out


def _watch_notes(
    *,
    daily_volume_ratio: float | None,
    stage2_info: dict[str, bool],
    weekly_info: dict[str, Any],
    skip_reason: str | None,
) -> list[str]:
    notes: list[str] = []
    if skip_reason:
        notes.append("Strict setup gate: " + str(skip_reason).replace("_", " ").lower())
    if not stage2_info["passed"]:
        notes.append("Stage 2 trend is still forming")
    if daily_volume_ratio is not None and daily_volume_ratio < float(settings.VOLUME["breakout_vol_ratio"]):
        notes.append("Needs daily volume above 1.4x for a clean trigger")
    resistance = weekly_info.get("weekly_resistance")
    distance = weekly_info.get("weekly_resistance_distance_pct")
    if resistance is not None and distance is not None and distance > float(settings.WEEKLY_BREAKOUT["within_breakout_pct"]):
        notes.append(f"Wait for weekly close above {resistance:.2f}")
    stop_distance = weekly_info.get("weekly_stop_distance_pct")
    if stop_distance is not None and stop_distance > float(settings.WEEKLY_BREAKOUT["max_stop_distance_pct"]):
        notes.append("Weekly invalidation is wide; wait for a tighter base")
    if not notes:
        notes.append("Momentum is strong, but no strict pattern card fired")
    return notes[:4]


def _has_core_strength(
    *,
    from_52w_high_pct: float | None,
    daily_volume_ratio: float | None,
    weekly_volume_ratio: float | None,
    weekly_info: dict[str, Any],
) -> bool:
    near_high = from_52w_high_pct is not None and from_52w_high_pct <= 10.0
    volume_ok = (
        (daily_volume_ratio is not None and daily_volume_ratio >= 1.1)
        or (weekly_volume_ratio is not None and weekly_volume_ratio >= 1.4)
    )
    near_weekly = (
        weekly_info.get("weekly_resistance_extension_pct") is not None
        or (
            weekly_info.get("weekly_resistance_distance_pct") is not None
            and float(weekly_info["weekly_resistance_distance_pct"]) <= 6.0
        )
    )
    return bool((near_high and volume_ok) or (near_weekly and volume_ok))


def _status(score: float) -> str:
    if score >= 85:
        return "Momentum surge"
    if score >= 70:
        return "Near breakout watch"
    return "Early radar"


def _volume_ratio(volume: np.ndarray, period: int) -> float | None:
    if len(volume) < period + 1:
        return None
    avg = float(np.mean(volume[-period - 1 : -1]))
    latest = float(volume[-1])
    if avg <= 0:
        return None
    return latest / avg


def _lookback_return(close: np.ndarray, bars: int) -> float | None:
    if len(close) <= bars or float(close[-1 - bars]) <= 0:
        return None
    return (float(close[-1]) / float(close[-1 - bars]) - 1.0) * 100.0


def _last_float(values: np.ndarray) -> float | None:
    if len(values) == 0:
        return None
    value = float(values[-1])
    return value if np.isfinite(value) else None


def _round(value: float | None) -> float | None:
    return None if value is None else round(float(value), 2)


def _date_text(value: Any) -> str:
    text = str(value or "")
    return text[:10]
