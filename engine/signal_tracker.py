"""Lifecycle tracking for recent visible scanner signals."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Iterable

import pandas as pd

from engine import storage


LOOKBACK_DAYS = 30
REPORT_TIERS = {"MEDIUM", "HIGH", "HIGHEST"}


def record_current_signals(
    conn,
    results: Iterable[dict[str, Any]],
    *,
    generated_at: datetime,
    data_as_of: str | None = None,
) -> int:
    """Persist current visible report cards for future lifecycle checks."""
    seen_at = generated_at.isoformat(timespec="seconds")
    rows = []
    for item in results:
        if not _is_visible_signal(item):
            continue
        signal_date = _date_text(item.get("signal_date") or data_as_of or generated_at.date())
        if not signal_date:
            continue
        rows.append(
            {
                "symbol": item.get("symbol"),
                "pattern": item.get("pattern") or _field(item.get("pattern_result"), "pattern"),
                "signal_date": signal_date,
                "timeframe": item.get("timeframe") or _field(item.get("pattern_result"), "timeframe"),
                "tier": item.get("tier"),
                "score": item.get("score"),
                "status": item.get("status") or _field(item.get("pattern_result"), "status"),
                "company_name": item.get("company_name"),
                "sector": item.get("sector"),
                "cmp": item.get("cmp"),
                "entry_price": item.get("entry_price") or item.get("pivot") or _field(item.get("pattern_result"), "pivot"),
                "target": item.get("target") or _field(item.get("pattern_result"), "target"),
                "stop_loss": item.get("stop_loss") or _field(item.get("pattern_result"), "stop_loss"),
                "seen_at": seen_at,
            }
        )
    return storage.record_signal_history(conn, rows)


def build_tracker(
    conn,
    current_results: Iterable[dict[str, Any]],
    *,
    generated_at: datetime,
    data_as_of: str | None = None,
    lookback_days: int = LOOKBACK_DAYS,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Return recent signal rows with validity status from post-signal candles."""
    as_of = _date_value(data_as_of) or generated_at.date()
    since = as_of - timedelta(days=max(1, int(lookback_days)) - 1)
    signals = storage.fetch_recent_signal_history(conn, since_date=since.isoformat(), limit=limit)
    symbols = [str(row.get("symbol") or "").upper() for row in signals]
    prices = _group_daily_rows(storage.fetch_daily_rows_since(conn, symbols, since_date=since.isoformat()))
    current_by_symbol = _current_results_by_symbol(current_results)

    tracked = []
    for row in signals:
        symbol = str(row.get("symbol") or "").upper()
        signal_date = _date_value(row.get("signal_date"))
        symbol_prices = prices.get(symbol, [])
        latest = symbol_prices[-1] if symbol_prices else {}
        evaluation = _evaluate_signal(row, symbol_prices, signal_date=signal_date)
        current_item = current_by_symbol.get(symbol)
        fresh_today = current_item is not None
        days_since_signal = max(0, (as_of - signal_date).days) if signal_date else None
        trade_view = _trade_view(
            row,
            latest_close=_number(latest.get("close")),
            evaluation=evaluation,
            fresh_today=fresh_today,
            current_item=current_item,
        )
        tracked.append(
            {
                **row,
                "symbol": symbol,
                "days_since_signal": days_since_signal,
                "same_day": days_since_signal == 0,
                "latest_date": str(latest.get("date") or ""),
                "latest_close": _number(latest.get("close")),
                "change_pct": _pct_change(row.get("cmp"), latest.get("close")),
                "status": evaluation["status"],
                "status_class": evaluation["status_class"],
                "reason": _fresh_reason(evaluation["reason"], fresh_today),
                "fresh_state": "Fresh today" if fresh_today else "Not fresh today",
                "fresh_class": "fresh" if fresh_today else "stale",
                "target_hit_date": evaluation.get("target_hit_date"),
                "stop_hit_date": evaluation.get("stop_hit_date"),
                **trade_view,
            }
        )
    return tracked


def _current_results_by_symbol(current_results: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    current: dict[str, dict[str, Any]] = {}
    for item in current_results:
        if not _is_visible_signal(item):
            continue
        symbol = str(item.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        existing = current.get(symbol)
        if existing is None or (_number(item.get("score")) or 0.0) > (_number(existing.get("score")) or 0.0):
            current[symbol] = item
    return current


def _evaluate_signal(
    signal: dict[str, Any],
    prices: list[dict[str, Any]],
    *,
    signal_date: date | None,
) -> dict[str, Any]:
    latest = prices[-1] if prices else {}
    latest_close = _number(latest.get("close"))
    entry = _number(signal.get("entry_price"))
    target = _number(signal.get("target"))
    stop = _number(signal.get("stop_loss"))
    post_signal = [
        row for row in prices
        if signal_date is not None and (_date_value(row.get("date")) or date.min) > signal_date
    ]
    target_hit_date = _first_hit_date(post_signal, "high", target, mode="above")
    stop_hit_date = _first_hit_date(post_signal, "low", stop, mode="below")

    if latest_close is None:
        return {
            "status": "Data Missing",
            "status_class": "missing",
            "reason": "No latest candle is available in the local database.",
        }
    if target_hit_date and stop_hit_date and target_hit_date == stop_hit_date:
        return {
            "status": "Review",
            "status_class": "review",
            "reason": f"Target and stop both touched on {target_hit_date}; inspect intraday sequence.",
            "target_hit_date": target_hit_date,
            "stop_hit_date": stop_hit_date,
        }
    if target_hit_date and (not stop_hit_date or target_hit_date < stop_hit_date):
        return {
            "status": "Target Hit",
            "status_class": "target",
            "reason": f"Target was touched on {target_hit_date} before invalidation.",
            "target_hit_date": target_hit_date,
            "stop_hit_date": stop_hit_date,
        }
    if stop_hit_date and (not target_hit_date or stop_hit_date < target_hit_date):
        return {
            "status": "Invalidated",
            "status_class": "invalid",
            "reason": f"Stop was touched on {stop_hit_date} before target.",
            "target_hit_date": target_hit_date,
            "stop_hit_date": stop_hit_date,
        }
    if stop is not None and latest_close <= stop:
        return {
            "status": "Invalidated",
            "status_class": "invalid",
            "reason": "Latest close is at or below the original stop.",
        }
    if entry is not None and latest_close < entry:
        return {
            "status": "Watch",
            "status_class": "watch",
            "reason": "Latest close is below the original entry but the stop has not broken.",
        }
    return {
        "status": "Still Valid",
        "status_class": "valid",
        "reason": "Latest close remains above the original entry and stop.",
    }


def _trade_view(
    signal: dict[str, Any],
    *,
    latest_close: float | None,
    evaluation: dict[str, Any],
    fresh_today: bool,
    current_item: dict[str, Any] | None,
) -> dict[str, Any]:
    entry = _number(signal.get("entry_price"))
    target = _number(signal.get("target"))
    stop = _number(signal.get("stop_loss"))
    current_rr = _reward_risk_from(latest_close, target, stop)
    trigger = _trigger_view(latest_close=latest_close, entry=entry, target=target, status_class=evaluation["status_class"])
    decision = _entry_decision(
        latest_close=latest_close,
        entry=entry,
        target=target,
        stop=stop,
        current_rr=current_rr,
        status_class=evaluation["status_class"],
        fresh_today=fresh_today,
    )
    current_conviction = _current_conviction(
        latest_close=latest_close,
        entry=entry,
        target=target,
        current_rr=current_rr,
        status=evaluation["status"],
        status_class=evaluation["status_class"],
        current_item=current_item,
    )
    return {
        "original_conviction": _original_conviction(signal),
        "original_conviction_class": _tier_class(signal.get("tier")),
        "current_conviction": current_conviction["label"],
        "current_conviction_class": current_conviction["class"],
        "trigger_price": entry,
        "trigger_state": trigger["state"],
        "trigger_class": trigger["class"],
        "trigger_note": trigger["note"],
        "entry_decision": decision["decision"],
        "decision_class": decision["class"],
        "decision_reason": decision["reason"],
        "current_reward_risk": current_rr,
    }


def _trigger_view(
    *,
    latest_close: float | None,
    entry: float | None,
    target: float | None,
    status_class: str,
) -> dict[str, str]:
    if latest_close is None:
        return {"state": "No price data", "class": "missing", "note": "Latest candle is missing."}
    if status_class == "invalid":
        return {"state": "Stop broken", "class": "invalid", "note": "The original setup is no longer active."}
    if status_class == "target":
        return {"state": "Target hit", "class": "target", "note": "The move already reached the original target."}
    if status_class == "review":
        return {"state": "Review", "class": "review", "note": "Target and stop touched on the same candle."}
    if entry is None:
        return {"state": "No trigger level", "class": "review", "note": "The original entry level is missing."}
    if latest_close < entry:
        distance = (entry / latest_close - 1.0) * 100.0 if latest_close > 0 else None
        gap = "" if distance is None else f" ({distance:.2f}% away)"
        return {
            "state": "Waiting for trigger",
            "class": "watch",
            "note": f"Trigger means price crosses or closes above entry{gap}.",
        }
    if target is not None and latest_close >= target:
        return {"state": "Past target", "class": "target", "note": "Fresh entry is too late after target."}
    extension = (latest_close / entry - 1.0) * 100.0 if entry > 0 else 0.0
    if extension <= 1.0:
        return {"state": "At trigger", "class": "valid", "note": "Price is at or just above the entry trigger."}
    if extension <= 3.0:
        return {"state": "Triggered", "class": "valid", "note": f"Price is {extension:.2f}% above entry."}
    return {"state": "Extended", "class": "watch", "note": f"Price is {extension:.2f}% above entry; avoid chasing."}


def _entry_decision(
    *,
    latest_close: float | None,
    entry: float | None,
    target: float | None,
    stop: float | None,
    current_rr: float | None,
    status_class: str,
    fresh_today: bool,
) -> dict[str, str]:
    if latest_close is None:
        return {"decision": "No decision", "class": "missing", "reason": "Latest price data is unavailable."}
    if status_class == "invalid":
        return {"decision": "Exit / Avoid", "class": "avoid", "reason": "The original stop has broken."}
    if status_class == "target":
        return {"decision": "Book / Trail", "class": "book", "reason": "The original target has already been touched."}
    if status_class == "review":
        return {"decision": "Review manually", "class": "review", "reason": "Daily candles cannot prove target/stop order."}
    if entry is None:
        return {"decision": "Review manually", "class": "review", "reason": "Entry trigger is missing."}
    if latest_close < entry:
        if fresh_today:
            return {
                "decision": "Wait for trigger",
                "class": "wait",
                "reason": "Setup is visible, but price has not crossed the entry trigger.",
            }
        return {
            "decision": "No fresh entry",
            "class": "no-entry",
            "reason": "Old signal is below entry; wait for a fresh trigger.",
        }
    if target is not None and latest_close >= target:
        return {"decision": "Do not enter", "class": "no-entry", "reason": "Price is already at or above target."}
    if current_rr is not None and current_rr < 1.0:
        return {
            "decision": "No fresh entry",
            "class": "no-entry",
            "reason": "Current reward/risk is too weak from the latest price.",
        }
    extension = (latest_close / entry - 1.0) * 100.0 if entry > 0 else 0.0
    if not fresh_today:
        return {
            "decision": "Hold if already in",
            "class": "hold",
            "reason": "The old trade remains valid, but it is not a new signal today.",
        }
    if extension <= 2.0 and (current_rr is None or current_rr >= 1.5):
        return {
            "decision": "Can enter if volume confirms",
            "class": "enter",
            "reason": "Trigger is active and reward/risk remains acceptable.",
        }
    return {
        "decision": "Do not chase",
        "class": "wait",
        "reason": "Trigger happened, but the latest price is extended from entry.",
    }


def _current_conviction(
    *,
    latest_close: float | None,
    entry: float | None,
    target: float | None,
    current_rr: float | None,
    status: str,
    status_class: str,
    current_item: dict[str, Any] | None,
) -> dict[str, str]:
    if status_class in {"invalid", "target", "review", "missing"}:
        return {"label": status, "class": status_class}
    if current_item is not None:
        return {"label": _fresh_conviction(current_item), "class": _tier_class(current_item.get("tier"))}
    if latest_close is not None and entry is not None and latest_close < entry:
        return {"label": "Weakening", "class": "watch"}
    if latest_close is not None and target is not None and latest_close >= target:
        return {"label": "Extended", "class": "target"}
    if current_rr is not None and current_rr < 1.0:
        return {"label": "Extended", "class": "watch"}
    return {"label": "Still valid", "class": "valid"}


def _original_conviction(signal: dict[str, Any]) -> str:
    tier = str(signal.get("tier") or "UNKNOWN").upper()
    score = _number(signal.get("score"))
    if score is None:
        return tier
    return f"{tier} {int(score)}"


def _fresh_conviction(item: dict[str, Any]) -> str:
    tier = str(item.get("tier") or "UNKNOWN").upper()
    score = _number(item.get("score"))
    if score is None:
        return f"Fresh {tier}"
    return f"Fresh {tier} {int(score)}"


def _tier_class(tier: Any) -> str:
    text = str(tier or "").upper()
    if text in {"HIGHEST", "HIGH", "MEDIUM"}:
        return text.lower()
    return "unknown"


def _reward_risk_from(latest_close: float | None, target: float | None, stop: float | None) -> float | None:
    if latest_close is None or target is None or stop is None:
        return None
    risk = latest_close - stop
    reward = target - latest_close
    if risk <= 0:
        return None
    return round(max(0.0, reward) / risk, 2)


def _first_hit_date(
    rows: list[dict[str, Any]],
    field: str,
    level: float | None,
    *,
    mode: str,
) -> str | None:
    if level is None:
        return None
    for row in rows:
        value = _number(row.get(field))
        if value is None:
            continue
        if mode == "above" and value >= level:
            return str(row.get("date") or "")
        if mode == "below" and value <= level:
            return str(row.get("date") or "")
    return None


def _group_daily_rows(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        symbol = str(row.get("symbol") or "").upper()
        if symbol:
            grouped[symbol].append(row)
    for symbol in grouped:
        grouped[symbol].sort(key=lambda row: str(row.get("date") or ""))
    return grouped


def _fresh_reason(reason: str, fresh_today: bool) -> str:
    prefix = "Still appears in today's report" if fresh_today else "No fresh card today"
    return f"{prefix}; {reason[0].lower() + reason[1:] if reason else 'status checked.'}"


def _is_visible_signal(item: dict[str, Any]) -> bool:
    tier = str(item.get("tier") or "").upper()
    return tier in REPORT_TIERS and bool(item.get("tradable", True))


def _pct_change(start: Any, end: Any) -> float | None:
    start_number = _number(start)
    end_number = _number(end)
    if start_number is None or end_number is None or start_number <= 0:
        return None
    return round((end_number / start_number - 1.0) * 100.0, 2)


def _date_value(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def _date_text(value: Any) -> str:
    parsed = _date_value(value)
    return parsed.isoformat() if parsed else ""


def _field(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None
