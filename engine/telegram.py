"""Telegram Bot API alert helpers."""

from __future__ import annotations

import html
import time
from collections import Counter
from pathlib import Path
from typing import Any

import requests

from config import settings


BOT_API_BASE = "https://api.telegram.org"
CAPTION_LIMIT = 1024


def send_alert(
    message: str,
    *,
    token: str | None = None,
    chat_id: str | None = None,
    timeout: int = 10,
    retry_count: int = 2,
    sleep_seconds: int = 5,
    session=requests,
) -> bool:
    """Send an HTML Telegram message. Returns False on missing config or API failure."""
    token = _resolve_token(token)
    chat_id = _resolve_chat_id(chat_id)
    if not token or not chat_id:
        return False

    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    return _post_with_retries(
        _api_url(token, "sendMessage"),
        timeout=timeout,
        retry_count=retry_count,
        sleep_seconds=sleep_seconds,
        session=session,
        data=payload,
    )


def send_chart_alert(
    scored: dict[str, Any],
    chart_path: str | Path,
    *,
    token: str | None = None,
    chat_id: str | None = None,
    caption: str | None = None,
    timeout: int = 10,
    retry_count: int = 2,
    sleep_seconds: int = 5,
    session=requests,
) -> bool:
    """Send a chart image with a compact alert caption."""
    token = _resolve_token(token)
    chat_id = _resolve_chat_id(chat_id)
    chart_path = Path(chart_path)
    if not token or not chat_id or not chart_path.exists():
        return False

    payload = {
        "chat_id": chat_id,
        "caption": _trim_caption(caption or format_chart_caption(scored)),
        "parse_mode": "HTML",
    }
    with chart_path.open("rb") as photo:
        return _post_with_retries(
            _api_url(token, "sendPhoto"),
            timeout=timeout,
            retry_count=retry_count,
            sleep_seconds=sleep_seconds,
            session=session,
            data=payload,
            files={"photo": photo},
        )


def should_send_alert(scored: dict[str, Any], min_conviction: int | None = None) -> bool:
    min_conviction = int(min_conviction or settings.TELEGRAM_MIN_CONVICTION)
    return bool(scored.get("tradable", True)) and int(scored.get("score", 0)) >= min_conviction


def format_alert(scored: dict[str, Any]) -> str:
    symbol = _esc(scored.get("symbol", "UNKNOWN"))
    pattern = _esc(scored.get("pattern", "Pattern"))
    status = _esc(scored.get("status", ""))
    tier = _esc(scored.get("tier", ""))
    cmp_value = _current_price(scored)
    rr = _risk_reward(scored)
    filters = scored.get("filters", {})
    stack_count = int(scored.get("stacked_count") or scored.get("stack_count") or 1)

    lines = [
        f"\U0001F6A8 <b>{symbol}</b> | CMP Rs.{_fmt(cmp_value)}",
        f"\U0001F4D0 <b>{pattern}</b> {status}",
        (
            f"Entry Rs.{_fmt(scored.get('pivot'))} | Target Rs.{_fmt(scored.get('target'))} | "
            f"Stop Rs.{_fmt(scored.get('stop_loss'))} | R:R {rr}:1"
        ),
        f"Conviction: <b>{int(scored.get('score', 0))}/100</b> {tier}",
        (
            "Filters: "
            f"Stage2 {_check(filters.get('stage2', {}).get('passed'))} | "
            f"Volume {_check(filters.get('volume', {}).get('passed'))} | "
            f"Sector {_status(filters.get('sector_rs', {}).get('status'))} | "
            f"Regime {_status(filters.get('market_regime', {}).get('verdict'))} | "
            f"RSI {_esc(filters.get('rsi', {}).get('status', 'UNKNOWN'))}"
        ),
    ]
    if stack_count > 1:
        lines.append("Stacked patterns: " + _stacked_pattern_text(scored, stack_count))
    if scored.get("skip_reason"):
        lines.append(f"Skip reason: {_esc(scored['skip_reason'])}")
    return "\n".join(lines)


def format_chart_caption(scored: dict[str, Any]) -> str:
    pattern = _esc(scored.get("pattern", "Pattern"))
    symbol = _esc(scored.get("symbol", "UNKNOWN"))
    rr = _risk_reward(scored)
    why = _short_reason(scored)
    caption = "\n".join(
        [
            f"\U0001F4D0 <b>{pattern}</b> | {symbol}",
            why,
            (
                f"Entry Rs.{_fmt(scored.get('pivot'))} | Stop Rs.{_fmt(scored.get('stop_loss'))} | "
                f"Target Rs.{_fmt(scored.get('target'))} | R:R {rr}:1"
            ),
            f"Conviction: <b>{int(scored.get('score', 0))}/100</b> {_esc(scored.get('tier', ''))}",
        ]
    )
    stack_count = int(scored.get("stacked_count") or scored.get("stack_count") or 1)
    if stack_count > 1:
        caption += "\nStacked: " + _stacked_pattern_text(scored, stack_count)
    return _trim_caption(caption)


def send_daily_summary(
    market_regime: dict[str, Any],
    scored_results: list[dict[str, Any]],
    *,
    stocks_scanned: int | None = None,
    total_alerts: int | None = None,
    token: str | None = None,
    chat_id: str | None = None,
    timeout: int = 10,
    retry_count: int = 2,
    sleep_seconds: int = 5,
    session=requests,
) -> bool:
    message = format_daily_summary(
        market_regime,
        scored_results,
        stocks_scanned=stocks_scanned,
        total_alerts=total_alerts,
    )
    return send_alert(
        message,
        token=token,
        chat_id=chat_id,
        timeout=timeout,
        retry_count=retry_count,
        sleep_seconds=sleep_seconds,
        session=session,
    )


def format_daily_summary(
    market_regime: dict[str, Any],
    scored_results: list[dict[str, Any]],
    *,
    stocks_scanned: int | None = None,
    total_alerts: int | None = None,
) -> str:
    tiers = Counter(str(item.get("tier", "SKIP")) for item in scored_results)
    if total_alerts is None:
        total_alerts = sum(1 for item in scored_results if should_send_alert(item))
    scanned = stocks_scanned if stocks_scanned is not None else "N/A"
    verdict = _esc(market_regime.get("verdict", "UNKNOWN"))
    score = market_regime.get("score", "N/A")
    return "\n".join(
        [
            "\U0001F4CA <b>NSE Pattern Scan Summary</b>",
            f"Market regime: <b>{verdict}</b> ({score}/4)",
            f"Stocks scanned: {scanned}",
            f"Pattern hits: {len(scored_results)}",
            (
                "Tiers: "
                f"HIGHEST {tiers.get('HIGHEST', 0)} | "
                f"HIGH {tiers.get('HIGH', 0)} | "
                f"MEDIUM {tiers.get('MEDIUM', 0)} | "
                f"SKIP {tiers.get('SKIP', 0)}"
            ),
            f"Telegram alerts: {total_alerts}",
        ]
    )


def _post_with_retries(
    url: str,
    *,
    timeout: int,
    retry_count: int,
    sleep_seconds: int,
    session,
    data: dict[str, Any],
    files: dict[str, Any] | None = None,
) -> bool:
    attempts = max(1, retry_count + 1)
    for attempt in range(attempts):
        try:
            _rewind_files(files)
            response = session.post(url, data=data, files=files, timeout=timeout)
            if response.ok and _telegram_ok(response):
                return True
        except requests.RequestException:
            pass
        if attempt < attempts - 1:
            time.sleep(sleep_seconds)
    return False


def _rewind_files(files: dict[str, Any] | None) -> None:
    if not files:
        return
    for file_obj in files.values():
        if hasattr(file_obj, "seek"):
            file_obj.seek(0)


def _telegram_ok(response) -> bool:
    try:
        payload = response.json()
    except ValueError:
        return False
    return bool(payload.get("ok"))


def _api_url(token: str, method: str) -> str:
    return f"{BOT_API_BASE}/bot{token}/{method}"


def _resolve_token(token: str | None) -> str:
    return (token or settings.TELEGRAM_BOT_TOKEN).strip()


def _resolve_chat_id(chat_id: str | None) -> str:
    return str(chat_id or settings.TELEGRAM_CHAT_ID).strip()


def _current_price(scored: dict[str, Any]) -> float | None:
    if scored.get("cmp") is not None:
        return scored["cmp"]
    return scored.get("filters", {}).get("stage2", {}).get("details", {}).get("close")


def _risk_reward(scored: dict[str, Any]) -> str:
    pivot = _num(scored.get("pivot"))
    target = _num(scored.get("target"))
    stop = _num(scored.get("stop_loss"))
    if pivot is None or target is None or stop is None:
        return "0"
    risk = max(pivot - stop, 0.0)
    reward = max(target - pivot, 0.0)
    return _fmt(reward / risk if risk > 0 else 0.0)


def _short_reason(scored: dict[str, Any]) -> str:
    pattern_result = scored.get("pattern_result")
    explanation = getattr(pattern_result, "explanation", "") if pattern_result else ""
    explanation = explanation or scored.get("explanation", "") or "Setup detected by the pattern engine."
    first_sentence = str(explanation).split(".")[0].strip()
    return _esc(first_sentence[:180] or "Setup detected by the pattern engine.")


def _check(value: Any) -> str:
    return "\u2705" if bool(value) else "\u274c"


def _status(value: Any) -> str:
    return _esc(value or "UNKNOWN")


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=False)


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt(value: Any) -> str:
    number = _num(value)
    if number is None:
        return "N/A"
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _trim_caption(caption: str) -> str:
    if len(caption) <= CAPTION_LIMIT:
        return caption
    return caption[: CAPTION_LIMIT - 3].rstrip() + "..."


def _stacked_pattern_text(scored: dict[str, Any], stack_count: int) -> str:
    names = []
    for value in scored.get("all_patterns") or []:
        text = str(value or "").strip()
        if text and text not in names:
            names.append(text)
    if not names:
        names.append(str(scored.get("pattern") or "Pattern"))
    return f"{stack_count} (" + ", ".join(_esc(name) for name in names) + ")"
