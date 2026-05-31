"""Self-contained Carbon Ember dashboard rendering."""

from __future__ import annotations

import base64
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

from jinja2 import Environment, FileSystemLoader, select_autoescape

from config import settings
from engine.explainer import PATTERN_101


TEMPLATE_PATH = settings.BASE_DIR / "dashboard" / "template.html"
_DASHBOARD_DIR = settings.BASE_DIR / "dashboard"
_VENDOR_DIR = _DASHBOARD_DIR / "vendor"
TIER_ORDER = ("HIGHEST", "HIGH", "MEDIUM", "SKIP")
TIER_LABELS = {
    "HIGHEST": "Highest conviction",
    "HIGH": "High conviction",
    "MEDIUM": "Watchlist quality",
    "SKIP": "Rejected / avoid",
}
REGIME_CHECK_LABELS = {
    "nifty_above_50ma": "Nifty above 50 MA",
    "nifty_above_200ma": "Nifty above 200 MA",
    "ma50_above_ma200": "50 MA above 200 MA",
    "advance_decline_confirmed": "Breadth confirmed",
}
BREAKDOWN_LABELS = {
    "pattern": "Pattern quality",
    "stage2": "Stage 2 uptrend",
    "volume": "Volume confirmation",
    "pocket_pivot": "Pocket pivot",
    "sector_rs": "Sector relative strength",
    "market_regime": "Market regime",
    "multi_tf": "Multi-timeframe",
    "rsi_adjustment": "RSI adjustment",
}
FILTER_LABELS = {
    "stage2": "Stage 2",
    "volume": "Volume",
    "daily_volume": "Daily Volume",
    "pocket_pivot": "Pocket Pivot",
    "sector_rs": "Sector RS",
    "market_regime": "Regime",
    "rsi": "RSI",
    "multi_tf": "Multi-TF",
}
PATTERN_CHART_GUIDE = {
    "Ascending Triangle": "Flat resistance should be marked above price while rising lows form the support line. A valid setup needs price to hold the rising support and break the resistance area.",
    "Cup & Handle": "The cup base should show a rounded recovery back near the old high. The handle is the final smaller pullback before the breakout rim.",
    "Bull Flag": "The pole should be the sharp advance. The flag is the controlled pullback or pause after that advance; heavy selling inside the flag weakens the setup.",
    "Flat Base": "The base should be tight and horizontal near 52-week highs. A valid setup waits for price to clear the box instead of chasing an old move.",
    "VCP": "The chart should show volatility boxes shrinking from left to right. The final box should be tight near the pivot, with entry only after price clears that area.",
    "Double Bottom": "The second low should undercut the first low, shake out weak holders, and reclaim the middle pivot with improving volume.",
    "High Tight Flag": "The chart should show a very sharp advance followed by a short controlled flag near the highs. It is rare and should not be forced.",
    "Inverse Head & Shoulders": "The left shoulder, deeper head, and right shoulder should be visible below the neckline. The setup only confirms when price clears the neckline.",
    "Supertrend Bullish Flip": "The chart should show price reclaiming the supertrend support line. The support line is the invalidation reference if the flip fails.",
    "Multi-Year Breakout": "The chart should show a long resistance line or zone that has been tested before. A real breakout needs price and volume to clear that ceiling.",
    "Weekly Breakout": "The chart should show price clearing a weekly resistance line or descending trendline. Daily candles are confirmation, not the source of the setup.",
}


def render_dashboard(context: dict[str, Any], *, template_path: str | Path | None = None) -> str:
    """Render a self-contained dashboard HTML string from scanner context."""
    path = Path(template_path) if template_path else TEMPLATE_PATH
    env = Environment(
        loader=FileSystemLoader(str(path.parent)),
        autoescape=select_autoescape(("html", "xml")),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template(path.name)
    return template.render(
        **build_dashboard_context(context),
        lw_charts_js=_read_js(_VENDOR_DIR / "lightweight-charts.standalone.production.js"),
        tc_renderer_js=_read_js(_DASHBOARD_DIR / "chart_renderer.js"),
        tc_annotations_js=_read_js(_DASHBOARD_DIR / "chart_annotations.js"),
    )


def write_dashboard(
    context: dict[str, Any],
    output_path: str | Path | None = None,
    *,
    template_path: str | Path | None = None,
) -> Path:
    """Render the dashboard and write it to disk."""
    if output_path is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        output_path = settings.OUTPUT_DIR / f"dashboard_{timestamp}.html"
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_dashboard(context, template_path=template_path), encoding="utf-8")
    return path


def build_dashboard_context(context: dict[str, Any]) -> dict[str, Any]:
    """Normalize scanner output into a stable template contract."""
    market_regime = _normalize_market_regime(context.get("market_regime") or context.get("regime") or {})
    sector_by_symbol = _build_symbol_to_sector(context)
    tier_by_sector = _build_sector_to_tier(context.get("sector_leaderboard") or {})
    results = []
    skipped_count = 0
    skip_reasons: dict[str, int] = {}
    skipped_samples: dict[str, list[str]] = {}
    for item in _list(context, "results", "scored_results"):
        normalized = _normalize_result(item)
        # Real-money rule: SKIP tier setups never appear on the dashboard
        # or go to Telegram. They are tracked separately so the user can
        # audit WHY each setup was rejected.
        if normalized["tier"] == "SKIP":
            skipped_count += 1
            raw_reason = str(item.get("skip_reason") or normalized.get("skip_reason") or "UNKNOWN")
            bucket = _skip_reason_bucket(raw_reason)
            skip_reasons[bucket] = skip_reasons.get(bucket, 0) + 1
            samples = skipped_samples.setdefault(bucket, [])
            if len(samples) < 8:
                samples.append(normalized["symbol"])
            continue
        symbol = normalized["symbol"]
        sector = sector_by_symbol.get(symbol) or "NIFTY 50"
        sector_tier = tier_by_sector.get(sector, "UNKNOWN")
        normalized["sector"] = sector
        normalized["sector_tier"] = sector_tier
        normalized["sector_tier_class"] = sector_tier.lower()
        results.append(normalized)
    skip_breakdown = _build_skip_breakdown(skip_reasons, skipped_samples)
    results.sort(key=lambda item: (TIER_ORDER.index(item["tier"]) if item["tier"] in TIER_ORDER else 99, -item["score"]))
    sectors = _normalize_sectors(context.get("sector_rs") or context.get("sector_cache") or context.get("sectors") or {})
    sector_leaderboard = _normalize_leaderboard(context.get("sector_leaderboard") or {})
    errors = [_normalize_error(item) for item in _list(context, "errors", "failed_stages")]

    generated_at = _format_datetime(_coalesce(context.get("generated_at"), context.get("scan_time")))
    duration = _fmt_number(_coalesce(context.get("duration_seconds"), context.get("duration")), suffix="s")
    stocks_scanned = _coalesce(context.get("stocks_scanned"), context.get("scan_count"), context.get("symbols_scanned"), "N/A")
    alerts_sent = context.get("alerts_sent")
    if alerts_sent is None:
        alerts_sent = min(
            _telegram_alert_limit(),
            sum(1 for item in results if _would_send_telegram(item)),
        )

    # SKIP tier is excluded entirely from dashboard tier groups so the
    # "SKIP (252)" button never appears next to HIGHEST/HIGH/MEDIUM.
    # Skipped count is tracked separately in the summary panel.
    tier_groups = []
    for tier in TIER_ORDER:
        if tier == "SKIP":
            continue
        items = [item for item in results if item["tier"] == tier]
        tier_groups.append(
            {
                "name": tier,
                "label": TIER_LABELS[tier],
                "results": items,
                "count": len(items),
                "avg_score": _average_score(items),
            }
        )

    return {
        "generated_at": generated_at,
        "scan_timeframe": str(context.get("scan_timeframe") or "daily").title(),
        "market_regime": market_regime,
        "nifty_cmp": _fmt_money(_coalesce(context.get("nifty_cmp"), market_regime["details"].get("nifty_close"))),
        "stocks_scanned": stocks_scanned,
        "duration": duration,
        "results": results,
        "tier_groups": tier_groups,
        "skipped_count": skipped_count,
        "skip_breakdown": skip_breakdown,
        "sectors": sectors,
        "sector_leaderboard": sector_leaderboard,
        "errors": errors,
        "pattern_guide": _supported_pattern_guide(),
        "summary": {
            "hit_count": len(results),
            "alert_count": alerts_sent,
            "error_count": len(errors),
            "highest_count": len([item for item in results if item["tier"] == "HIGHEST"]),
        },
    }


def _normalize_market_regime(regime: dict[str, Any]) -> dict[str, Any]:
    checks = regime.get("checks") or {}
    details = regime.get("details") or {}
    return {
        "score": _int(regime.get("score"), default=0),
        "verdict": str(regime.get("verdict") or "UNKNOWN"),
        "class_name": _status_class(regime.get("verdict")),
        "checks": [
            {
                "key": key,
                "label": REGIME_CHECK_LABELS.get(key, key.replace("_", " ").title()),
                "passed": bool(value),
            }
            for key, value in checks.items()
        ],
        "details": details,
        "detail_rows": [
            ("Nifty close", _fmt_money(details.get("nifty_close"))),
            ("50 MA", _fmt_money(details.get("nifty_ma50"))),
            ("200 MA", _fmt_money(details.get("nifty_ma200"))),
            ("A/D ratio", _fmt_number(details.get("advance_decline_ratio"))),
        ],
    }


def _normalize_result(item: dict[str, Any]) -> dict[str, Any]:
    pattern_result = item.get("pattern_result")
    symbol = str(item.get("symbol") or "UNKNOWN").upper()
    pattern = str(_coalesce(item.get("pattern"), _field(pattern_result, "pattern"), "Pattern"))
    pivot = _number(_coalesce(item.get("pivot"), _field(pattern_result, "pivot")))
    entry = _number(_coalesce(item.get("entry_price"), pivot))
    target = _number(_coalesce(item.get("target"), _field(pattern_result, "target")))
    stop_loss = _number(_coalesce(item.get("stop_loss"), _field(pattern_result, "stop_loss")))
    score = _int(_coalesce(item.get("score"), item.get("final_score"), _field(pattern_result, "confidence")), default=0)
    tier = str(_coalesce(item.get("tier"), _tier(score))).upper()
    explanation = str(_coalesce(item.get("explanation"), _field(pattern_result, "explanation"), ""))
    sections = _build_sections(pattern, explanation, item, entry, target, stop_loss)
    filters = _normalize_filters(item.get("filters") or {})
    volume_card = _build_volume_card(item.get("filters") or {})
    watch_checklist = _build_watch_checklist(item, entry, target, stop_loss)
    breakdown = _normalize_breakdown(item.get("breakdown") or {})
    all_patterns = _pattern_names(item.get("all_patterns") or item.get("patterns") or [])
    chart_src = _chart_data_uri(item.get("chart_data_uri") or item.get("chart_path"))
    chart_payload_json = _get_chart_payload_json(item)
    pattern_grade = _resolve_pattern_grade(item, pattern_result)
    reward_risk = _number(_coalesce(item.get("reward_risk"), (item.get("breakdown") or {}).get("reward_risk")))

    return {
        "symbol": symbol,
        "screener_url": _screener_url(symbol),
        "cmp": _fmt_money(_coalesce(item.get("cmp"), item.get("current_price"), _filter_detail(item, "stage2", "close"))),
        "pattern": pattern,
        "status": str(_coalesce(item.get("status"), _field(pattern_result, "status"), "")),
        "timeframe": str(_coalesce(item.get("timeframe"), _field(pattern_result, "timeframe"), "daily")),
        "pivot": pivot,
        "technical_pivot": _number(_coalesce(item.get("technical_pivot"), pivot)),
        "entry_price": entry,
        "entry_basis": str(item.get("entry_basis") or "pivot"),
        "scan_close": _number(item.get("scan_close")),
        "target": target,
        "stop_loss": stop_loss,
        "pivot_text": _fmt_money(pivot),
        "technical_pivot_text": _fmt_money(_coalesce(item.get("technical_pivot"), pivot)),
        "entry_text": _fmt_money(entry),
        "scan_close_text": _fmt_money(item.get("scan_close")),
        "target_text": _fmt_money(target),
        "stop_text": _fmt_money(stop_loss),
        "risk_reward": _risk_reward(entry, target, stop_loss),
        "score": score,
        "tier": tier if tier in TIER_ORDER else "SKIP",
        "tier_label": TIER_LABELS.get(tier, tier.title()),
        "tier_class": tier.lower(),
        "tradable": bool(item.get("tradable", tier != "SKIP")),
        "skip_reason": item.get("skip_reason"),
        "stacked_count": _int(item.get("stacked_count") or item.get("stack_count") or len(all_patterns) or 1, default=1),
        "all_patterns": all_patterns,
        "filters": filters,
        "volume_card": volume_card,
        "watch_checklist": watch_checklist,
        "breakdown": breakdown,
        "sections": sections,
        "chart_guide": PATTERN_CHART_GUIDE.get(pattern, "Read the chart from pattern structure first, then entry, target, stop, and invalidation."),
        "chart_src": chart_src,
        "chart_available": bool(chart_src),
        "chart_payload_json": chart_payload_json,
        "has_thesis_chart": bool(chart_payload_json),
        "pattern_grade": pattern_grade,
        "pattern_grade_display": "n/a" if pattern_grade is None else f"{pattern_grade:.1f}",
        "pattern_grade_class": _grade_class(pattern_grade),
        "pattern_grade_label": _grade_label(pattern_grade),
        "reward_risk": reward_risk,
        "reward_risk_display": "n/a" if reward_risk is None else f"{reward_risk:.2f}:1",
        "reward_risk_class": _rr_class(reward_risk),
    }


def _resolve_pattern_grade(item: dict[str, Any], pattern_result: Any) -> float | None:
    """Return the 0-10 pattern grade if the detector or scorer published one."""
    candidates = [
        item.get("pattern_grade"),
        (item.get("breakdown") or {}).get("pattern_grade"),
    ]
    extra = _field(pattern_result, "extra", {}) or {}
    if isinstance(extra, dict):
        candidates.append(extra.get("pattern_quality_score"))
    for value in candidates:
        number = _number(value)
        if number is not None:
            return round(number, 2)
    return None


def _grade_class(grade: float | None) -> str:
    if grade is None:
        return "unknown"
    if grade >= 8.0:
        return "textbook"
    if grade >= 7.0:
        return "decent"
    return "weak"


def _grade_label(grade: float | None) -> str:
    if grade is None:
        return "PATTERN GRADE n/a"
    if grade >= 8.0:
        tier = "TEXTBOOK"
    elif grade >= 7.0:
        tier = "DECENT"
    else:
        tier = "WEAK"
    return f"GRADE {grade:.1f}/10 • {tier}"


def _rr_class(rr: float | None) -> str:
    if rr is None:
        return "unknown"
    if rr >= 2.0:
        return "strong"
    if rr >= 1.5:
        return "ok"
    if rr >= 1.0:
        return "weak"
    return "bad"


def _normalize_filters(filters: dict[str, Any]) -> list[dict[str, Any]]:
    normalized = []
    for key, label in FILTER_LABELS.items():
        if key == "daily_volume" and key not in filters:
            continue
        value = filters.get(key) or {}
        label = _filter_label(key, label, value)
        if key == "market_regime":
            status = str(value.get("verdict") or value.get("status") or "UNKNOWN")
            passed = _int(value.get("score"), default=0) >= 3
        elif key == "rsi":
            status = str(value.get("status") or "UNKNOWN")
            passed = "DIVERGENCE" not in status.upper() and _number(value.get("value")) is not None
        else:
            status = str(value.get("status") or "UNKNOWN")
            passed = bool(value.get("passed"))
        summary = _filter_summary(key, value)
        display_status = _filter_plain_status(key, status, value, passed)
        class_name = _filter_class_name(key, status, value, passed)
        display = f"{label}: {display_status}" + (f" - {summary}" if summary else "")
        normalized.append(
            {
                "key": key,
                "label": label,
                "status": status,
                "display_status": display_status,
                "summary": summary,
                "display": display,
                "title": _filter_title(label, status, value),
                "passed": passed,
                "class_name": class_name,
            }
        )
    return normalized


def _filter_label(key: str, default: str, value: dict[str, Any]) -> str:
    if key == "volume":
        timeframe = str((value.get("details") or {}).get("timeframe") or "").lower()
        if timeframe == "weekly":
            return "Weekly Volume"
        if timeframe == "daily":
            return "Daily Volume"
    return default


def _filter_summary(key: str, value: dict[str, Any]) -> str:
    if key not in {"volume", "daily_volume"}:
        return ""
    details = value.get("details") or {}
    ratio = _number(details.get("breakout_volume_ratio"))
    latest = _number(details.get("latest_volume"))
    avg = _number(details.get("avg_volume") or details.get("avg_50d_volume") or details.get("avg_50w_volume"))
    if ratio is None or latest is None or avg is None:
        return ""
    return f"{ratio:.2f}x ({_fmt_volume(latest)} / {_fmt_volume(avg)} avg)"


def _filter_title(label: str, status: str, value: dict[str, Any]) -> str:
    details = value.get("details") or {}
    parts = [f"{label}: {status}"]
    summary = _filter_summary("volume", value)
    if summary:
        parts.append(summary)
    if "base_dry_up_ratio" in details:
        parts.append(f"Base dry-up ratio {_fmt_number(details.get('base_dry_up_ratio'))}x")
    reason = details.get("reason")
    if reason:
        parts.append(str(reason))
    return " | ".join(parts)


def _filter_plain_status(key: str, status: str, value: dict[str, Any], passed: bool) -> str:
    text = str(status or "UNKNOWN").upper()
    details = value.get("details") or {}
    if key in {"volume", "daily_volume"}:
        latest = _number(details.get("latest_volume"))
        if latest == 0:
            return "No live volume yet"
        if passed:
            return "Breakout volume confirmed"
        if text == "DRY_UP":
            return "Quiet base; wait for trigger volume"
        return "No volume confirmation yet"
    if key == "pocket_pivot":
        if passed:
            return "Strong buying candle"
        if text == "NO_UP_CLOSE":
            return "No strong buying candle today"
        if text == "INSUFFICIENT_DATA":
            return "Not enough data"
        return "Buying candle not strong enough"
    if key == "sector_rs":
        if text == "LEADING":
            return "Sector leading"
        if text == "NEUTRAL":
            return "Sector neutral"
        if text == "LAGGING":
            return "Sector lagging"
    if key == "market_regime":
        if "UPTREND" in text or "BULL" in text:
            return "Market supportive"
        if "BEAR" in text:
            return "Market weak; be selective"
        if "NEUTRAL" in text:
            return "Market mixed; be selective"
    if key == "rsi":
        if text == "HEALTHY":
            return "Momentum healthy"
        if "OVERBOUGHT" in text:
            return "Extended; wait for cooling"
        if "DIVERGENCE" in text:
            return "Momentum divergence"
    if key == "multi_tf":
        if text == "ALIGNED":
            return "Daily and weekly aligned"
        if "DIVERG" in text:
            return "Daily and weekly disagree"
    if key == "stage2":
        if passed:
            return "Uptrend intact"
        return "Not in confirmed uptrend"
    return text.replace("_", " ").title()


def _filter_class_name(key: str, status: str, value: dict[str, Any], passed: bool) -> str:
    text = str(status or "").upper()
    details = value.get("details") or {}
    if passed:
        return "pass"
    if key in {"volume", "daily_volume"} and (text == "DRY_UP" or _number(details.get("latest_volume")) == 0):
        return "watch"
    if key == "pocket_pivot" and text in {"NO_UP_CLOSE", "INSUFFICIENT_DATA"}:
        return "watch"
    if key == "sector_rs" and text in {"NEUTRAL", "UNKNOWN"}:
        return "watch"
    if key == "market_regime" and "NEUTRAL" in text:
        return "watch"
    if key == "rsi" and "OVERBOUGHT" in text:
        return "watch"
    return "fail"


def _build_volume_card(filters: dict[str, Any]) -> dict[str, Any]:
    primary = filters.get("volume") or _daily_volume_filter(filters) or {}
    daily = _daily_volume_filter(filters)
    primary_section = _volume_section(primary)
    daily_section = None
    if primary_section.get("timeframe_key") == "weekly" and daily:
        daily_section = _volume_section(daily, title="Daily Volume Snapshot")

    return {
        "available": bool(primary_section.get("available") or (daily_section or {}).get("available")),
        "primary": primary_section,
        "daily": daily_section,
        "king_text": _king_candle_text(filters.get("king_candle") or {}),
        "king_levels": _king_candle_levels(filters.get("king_candle") or {}),
        "king_class": _king_candle_class(filters.get("king_candle") or {}),
    }


def _volume_section(result: dict[str, Any], *, title: str | None = None) -> dict[str, Any]:
    details = result.get("details") or {}
    timeframe = str(details.get("timeframe") or "daily").lower()
    avg_period = _int(details.get("avg_period"), default=50)
    latest = _number(details.get("latest_volume"))
    avg = _volume_average(details, timeframe)
    last_5_avg = _number(details.get("last_5_avg_volume"))
    last_5_ratio = _number(details.get("last_5_vs_avg_ratio"))
    if (last_5_ratio is None or last_5_ratio <= 0) and avg and last_5_avg is not None:
        last_5_ratio = last_5_avg / avg
    direction = str(details.get("recent_volume_direction") or "unknown")
    latest_date = str(details.get("latest_volume_date") or "latest candle")
    latest_label = "Today volume" if details.get("latest_volume_is_today") else "Latest candle volume"
    avg_suffix = "W" if timeframe == "weekly" else "D"
    period_name = "weekly" if avg_suffix == "W" else "daily"
    average_label = f"{avg_period}{avg_suffix} avg"
    last_5_values = [_fmt_volume(value) for value in details.get("last_5_volumes") or []]

    return {
        "available": bool(result),
        "title": title or ("Weekly Volume" if timeframe == "weekly" else "Daily Volume"),
        "timeframe_key": timeframe,
        "timeframe": timeframe.title(),
        "latest_label": latest_label,
        "latest_date": latest_date,
        "latest_value": _fmt_volume(latest),
        "average_label": average_label,
        "average_value": _fmt_volume(avg),
        "last_5_label": f"5-{'week' if avg_suffix == 'W' else 'day'} avg",
        "last_5_sequence_label": f"Last 5 {period_name} volumes",
        "last_5_values": " / ".join(last_5_values) if last_5_values else "N/A",
        "last_5_average": _fmt_volume(last_5_avg),
        "recent_text": _recent_volume_text(direction, last_5_ratio, average_label, period_name),
        "recent_class": _recent_volume_class(direction),
    }


def _volume_average(details: dict[str, Any], timeframe: str) -> float | None:
    if timeframe == "weekly":
        return _number(_coalesce(details.get("avg_50w_volume"), details.get("avg_volume")))
    return _number(_coalesce(details.get("avg_50d_volume"), details.get("avg_volume")))


def _daily_volume_filter(filters: dict[str, Any]) -> dict[str, Any] | None:
    daily = filters.get("daily_volume")
    if daily:
        return daily
    primary = filters.get("volume")
    if str(((primary or {}).get("details") or {}).get("timeframe") or "").lower() == "daily":
        return primary
    return None


def _recent_volume_text(direction: str, ratio: float | None, avg_label: str, period_name: str) -> str:
    if ratio is None or ratio <= 0:
        return "Recent volume comparison is unavailable."
    ratio_text = f"{ratio:.2f}x"
    if direction == "higher":
        return f"Recent {period_name} volume is higher than average: {ratio_text} above {avg_label}."
    if direction == "lower":
        return f"Recent {period_name} volume is lower than average: {ratio_text} of {avg_label}."
    if direction == "near_average":
        return f"Recent {period_name} volume is near average: {ratio_text} of {avg_label}."
    return f"Recent {period_name} volume comparison is unavailable."


def _recent_volume_class(direction: str) -> str:
    if direction == "higher":
        return "higher"
    if direction == "lower":
        return "lower"
    return "neutral"


def _king_candle_text(result: dict[str, Any]) -> str:
    details = result.get("details") or {}
    status = str(result.get("status") or "").upper()
    if details.get("observed"):
        if status == "CONFIRMED":
            return "King Candle observed as additional confirmation with follow-through."
        if status == "PENDING_FOLLOW_THROUGH":
            return "King Candle observed as additional confirmation; follow-through is pending."
        if status == "HOLDING_MIDPOINT":
            return "King Candle observed as additional confirmation; price is holding its midpoint."
        return "King Candle observed as additional confirmation."
    return ""


def _king_candle_levels(result: dict[str, Any]) -> str:
    details = result.get("details") or {}
    if not details.get("observed"):
        return ""
    high = _fmt_money(details.get("king_high"))
    midpoint = _fmt_money(details.get("king_midpoint"))
    low = _fmt_money(details.get("king_low"))
    date_text = details.get("candle_date")
    prefix = f"{date_text}: " if date_text else ""
    return f"{prefix}High {high}, Mid {midpoint}, Low {low}"


def _king_candle_class(result: dict[str, Any]) -> str:
    status = str(result.get("status") or "").upper()
    if status in {"CONFIRMED", "HOLDING_MIDPOINT"}:
        return "confirm"
    if status == "PENDING_FOLLOW_THROUGH":
        return "watch"
    return "neutral"


def _build_watch_checklist(
    item: dict[str, Any],
    entry: float | None,
    target: float | None,
    stop_loss: float | None,
) -> list[dict[str, str]]:
    filters = item.get("filters") or {}
    timeframe = str(item.get("timeframe") or _field(item.get("pattern_result"), "timeframe", "daily")).lower()
    timeframe_label = "weekly" if timeframe.startswith("week") else "daily"
    rows = [
        {
            "label": "Trigger",
            "text": (
                f"Watch for price to close above {_fmt_money(entry)} on the {timeframe_label} chart. "
                "Avoid chasing if the move is already far above entry."
            ),
            "class_name": "trigger",
        },
        _watch_volume_row(filters, timeframe_label),
        {
            "label": "Invalidation",
            "text": f"The setup is wrong if price closes below {_fmt_money(stop_loss)}. Target area is {_fmt_money(target)}.",
            "class_name": "risk",
        },
    ]
    context = _watch_context_row(filters)
    if context:
        rows.append(context)
    return rows


def _watch_volume_row(filters: dict[str, Any], timeframe_label: str) -> dict[str, str]:
    primary = filters.get("volume") or filters.get("daily_volume") or {}
    details = primary.get("details") or {}
    status = str(primary.get("status") or "").upper()
    ratio = _number(details.get("breakout_volume_ratio"))
    latest = _number(details.get("latest_volume"))
    avg = _number(details.get("avg_volume") or details.get("avg_50d_volume") or details.get("avg_50w_volume"))
    ratio_text = "N/A" if ratio is None else f"{ratio:.2f}x"
    latest_text = _fmt_volume(latest)
    avg_text = _fmt_volume(avg)
    if latest == 0:
        text = f"Latest volume is 0 or missing. Verify live volume before acting; normal average is {avg_text}."
        class_name = "watch"
    elif bool(primary.get("passed")):
        text = f"Volume is confirmed at {ratio_text} of average ({latest_text} vs {avg_text})."
        class_name = "confirm"
    elif status == "DRY_UP":
        text = (
            f"Volume is quiet on the {timeframe_label} setup. That can be healthy inside a base, "
            f"but entry needs fresh volume above average ({latest_text} vs {avg_text})."
        )
        class_name = "watch"
    else:
        text = f"No volume support yet. Wait for expansion above average ({latest_text} vs {avg_text})."
        class_name = "risk"
    return {"label": "Volume", "text": text, "class_name": class_name}


def _watch_context_row(filters: dict[str, Any]) -> dict[str, str] | None:
    notes = []
    regime = filters.get("market_regime") or {}
    regime_text = str(regime.get("verdict") or regime.get("status") or "").upper()
    if "BEAR" in regime_text:
        notes.append("market is weak, so treat as watchlist only or use smaller size")
    elif "NEUTRAL" in regime_text:
        notes.append("market is mixed, so wait for clean price confirmation")
    sector = filters.get("sector_rs") or {}
    sector_text = str(sector.get("status") or "").upper()
    if sector_text == "LAGGING":
        notes.append("sector is lagging Nifty")
    elif sector_text == "NEUTRAL":
        notes.append("sector tailwind is only neutral")
    if not notes:
        return None
    return {
        "label": "Context",
        "text": "Be selective: " + "; ".join(notes) + ".",
        "class_name": "watch",
    }


def _supported_pattern_guide() -> list[dict[str, str]]:
    return [
        {
            "name": name,
            "meaning": PATTERN_101.get(name, "Pattern education is not available yet."),
            "chart_marks": PATTERN_CHART_GUIDE.get(name, ""),
        }
        for name in PATTERN_CHART_GUIDE
    ]


def _normalize_breakdown(breakdown: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for key, label in BREAKDOWN_LABELS.items():
        if key not in breakdown:
            continue
        rows.append({"label": label, "value": _fmt_number(breakdown.get(key))})
    return rows


def _would_send_telegram(item: dict[str, Any]) -> bool:
    allowed = {str(tier).upper() for tier in getattr(settings, "TELEGRAM_ALLOWED_TIERS", {"HIGHEST", "HIGH"})}
    return (
        bool(item.get("tradable"))
        and str(item.get("tier", "")).upper() in allowed
        and int(item.get("score", 0)) >= int(settings.TELEGRAM_MIN_CONVICTION)
    )


def _telegram_alert_limit() -> int:
    limit = int(getattr(settings, "TELEGRAM_MAX_ALERTS", 0) or 0)
    return limit if limit > 0 else 10**9


def _normalize_sectors(raw: Any) -> list[dict[str, Any]]:
    sectors = raw.get("sectors", raw) if isinstance(raw, dict) else raw
    if not sectors:
        return []
    if isinstance(sectors, dict):
        iterable = [{"name": name, **(value or {})} for name, value in sectors.items()]
    else:
        iterable = list(sectors)

    normalized = []
    for item in iterable:
        name = str(item.get("name") or item.get("sector") or item.get("sector_index") or "UNKNOWN")
        vs_nifty = _number(item.get("vs_nifty_pct"))
        return_pct = _number(item.get("return_pct"))
        status = _sector_status(vs_nifty)
        normalized.append(
            {
                "name": name,
                "return_pct": _fmt_percent(return_pct),
                "vs_nifty_pct": _fmt_percent(vs_nifty),
                "score": 0 if vs_nifty is None else max(-12.0, min(12.0, vs_nifty)),
                "class_name": status.lower(),
                "status": status,
            }
        )
    return sorted(normalized, key=lambda item: item["score"], reverse=True)


_SKIP_REASON_LABELS = {
    "LOW_PATTERN_QUALITY": "Pattern grade below threshold",
    "REWARD_RISK_BELOW_FLOOR": "Reward / risk below 1.0:1",
    "ACTIONABLE_REWARD_RISK_BELOW_FLOOR": "Future reward / risk below 1.5:1",
    "MOVE_ALREADY_HAPPENED_TARGET_HIT": "Target already hit after breakout",
    "TARGET_ALREADY_REACHED": "Already at or above target",
    "STOP_ALREADY_BROKEN": "Already below stop",
    "STAGE2_FAIL": "Not in Stage 2 uptrend",
    "VOLUME_FAIL": "No breakout volume",
    "SECTOR_LAGGING": "Sector lagging Nifty",
    "REGIME_BEAR": "Market regime bear",
    "RSI_OVERBOUGHT": "RSI overbought",
    "MULTI_TF_DIVERGENT": "Daily / weekly divergent",
    "UNKNOWN": "Unspecified",
}


def _skip_reason_bucket(raw: str) -> str:
    """Collapse the per-symbol skip reason string into a short bucket key."""
    if not raw:
        return "UNKNOWN"
    upper = raw.upper()
    if upper.startswith("REWARD_RISK_BELOW_FLOOR"):
        return "REWARD_RISK_BELOW_FLOOR"
    if upper.startswith("ACTIONABLE_REWARD_RISK_BELOW_FLOOR"):
        return "ACTIONABLE_REWARD_RISK_BELOW_FLOOR"
    if upper in _SKIP_REASON_LABELS:
        return upper
    return "UNKNOWN"


def _build_skip_breakdown(
    counts: dict[str, int], samples: dict[str, list[str]]
) -> list[dict[str, Any]]:
    """Sort skip-reason buckets by count desc, attach labels + samples."""
    rows = []
    for bucket, count in counts.items():
        rows.append(
            {
                "key": bucket,
                "label": _SKIP_REASON_LABELS.get(bucket, bucket.replace("_", " ").title()),
                "count": int(count),
                "samples": list(samples.get(bucket, [])),
            }
        )
    rows.sort(key=lambda r: r["count"], reverse=True)
    return rows


def _build_symbol_to_sector(context: dict[str, Any]) -> dict[str, str]:
    """Map symbol -> sector_index using whichever upstream source is present."""
    raw = context.get("sector_rs") or context.get("sector_cache") or {}
    mapping = raw.get("symbol_to_sector") if isinstance(raw, dict) else None
    if not isinstance(mapping, dict):
        return {}
    out: dict[str, str] = {}
    for symbol, info in mapping.items():
        if isinstance(info, dict):
            sector = info.get("sector_index")
            if sector:
                out[str(symbol).upper()] = str(sector)
    return out


def _build_sector_to_tier(leaderboard: dict[str, Any]) -> dict[str, str]:
    if not isinstance(leaderboard, dict):
        return {}
    out: dict[str, str] = {}
    for row in leaderboard.get("rows") or []:
        if isinstance(row, dict):
            sector = row.get("sector")
            tier = row.get("tier")
            if sector and tier:
                out[str(sector)] = str(tier)
    return out


def _normalize_leaderboard(raw: Any) -> dict[str, Any]:
    """Shape compute_leaderboard output for the template.

    Each row gets per-column display strings (signed %s) and CSS class names
    derived from the tier. Sort is preserved from the compute side (already
    ranked by composite_score desc).
    """
    if not isinstance(raw, dict):
        return {"rows": [], "leading_count": 0, "lagging_count": 0, "neutral_count": 0}
    rows = raw.get("rows") or []
    out_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        tier = str(row.get("tier") or "UNKNOWN")
        composite = _number(row.get("composite_score"))
        out_rows.append(
            {
                "sector": str(row.get("sector") or "UNKNOWN"),
                "rank": _int(row.get("rank"), default=0),
                "tier": tier,
                "tier_class": tier.lower(),
                "composite_score": composite,
                "composite_score_display": "n/a" if composite is None else f"{composite:.1f}",
                "ret_1m_display": _fmt_percent(row.get("ret_1m_pct")),
                "ret_3m_display": _fmt_percent(row.get("ret_3m_pct")),
                "ret_6m_display": _fmt_percent(row.get("ret_6m_pct")),
                "rs_1m": _number(row.get("rs_1m_pct")),
                "rs_3m": _number(row.get("rs_3m_pct")),
                "rs_6m": _number(row.get("rs_6m_pct")),
                "rs_1m_display": _fmt_percent(row.get("rs_1m_pct")),
                "rs_3m_display": _fmt_percent(row.get("rs_3m_pct")),
                "rs_6m_display": _fmt_percent(row.get("rs_6m_pct")),
                "rs_1m_class": _rs_class(row.get("rs_1m_pct")),
                "rs_3m_class": _rs_class(row.get("rs_3m_pct")),
                "rs_6m_class": _rs_class(row.get("rs_6m_pct")),
                "stage2": bool(row.get("stage2")),
                "breadth_50dma_display": _fmt_percent(row.get("breadth_50dma_pct"), signed=False),
                "breadth_200dma_display": _fmt_percent(row.get("breadth_200dma_pct"), signed=False),
                "constituents": _int(row.get("constituents"), default=0),
            }
        )
    return {
        "rows": out_rows,
        "leading_count": sum(1 for r in out_rows if r["tier"] == "LEADING"),
        "neutral_count": sum(1 for r in out_rows if r["tier"] == "NEUTRAL"),
        "lagging_count": sum(1 for r in out_rows if r["tier"] == "LAGGING"),
        "weights": raw.get("weights") or {},
    }


def _rs_class(value: Any) -> str:
    n = _number(value)
    if n is None:
        return "unknown"
    if n >= 1.0:
        return "leading"
    if n <= -1.0:
        return "lagging"
    return "neutral"


def _normalize_error(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return {
            "stage": str(item.get("stage") or item.get("where") or "pipeline"),
            "symbol": str(item.get("symbol") or "-"),
            "message": str(item.get("message") or item.get("error") or "Unknown error"),
            "critical": bool(item.get("critical", False)),
        }
    return {"stage": "pipeline", "symbol": "-", "message": str(item), "critical": False}


def _build_sections(
    pattern: str,
    explanation: str,
    item: dict[str, Any],
    entry: float | None,
    target: float | None,
    stop_loss: float | None,
) -> dict[str, str]:
    parsed = _parse_explanation_sections(explanation)
    breakdown_lines = []
    for row in _normalize_breakdown(item.get("breakdown") or {}):
        breakdown_lines.append(f"{row['label']}: {row['value']}")

    return {
        "pattern_header": parsed.get(0) or pattern.upper(),
        "pattern_101": parsed.get(1) or PATTERN_101.get(pattern, "Pattern education is not available yet."),
        "stock_specific": parsed.get(2) or _field(item.get("pattern_result"), "explanation", "Detector details are not available."),
        "action_plan": parsed.get(3) or _default_action_plan(entry, target, stop_loss),
        "risk": parsed.get(4) or _default_risk_note(stop_loss),
        "conviction": parsed.get(5) or "\n".join(breakdown_lines) or "Conviction breakdown is not available.",
    }


def _parse_explanation_sections(explanation: str) -> dict[int, str]:
    if not explanation:
        return {}
    matches = list(re.finditer(r"SECTION\s+(\d+):\s*([^\n]*)(?:\n|$)", explanation))
    if not matches:
        return {}
    sections: dict[int, str] = {}
    for idx, match in enumerate(matches):
        number = int(match.group(1))
        title = match.group(2).strip()
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(explanation)
        body = explanation[start:end].strip()
        sections[number] = body or title
    return sections


def _get_chart_payload_json(item: dict[str, Any]) -> str | None:
    """Build or retrieve a thesis chart payload JSON string from a scored result."""
    # Pre-built payload dict (scanner supplied it directly)
    pre = item.get("chart_payload")
    if isinstance(pre, dict):
        try:
            from engine.chart_payload import payload_to_json
            return payload_to_json(pre)
        except Exception:
            pass

    # OHLCV DataFrame provided alongside the result
    df = item.get("df") or item.get("ohlcv") or item.get("ohlcv_df")
    if df is not None:
        try:
            from engine.chart_payload import build_chart_payload, payload_to_json
            symbol = str(item.get("symbol") or "UNKNOWN")
            pattern_result = item.get("pattern_result")
            company_name = item.get("company_name") or item.get("name")
            tf = str(item.get("timeframe") or "Daily").capitalize()
            payload = build_chart_payload(df, symbol, pattern_result,
                                          company_name=company_name,
                                          entry_price=item.get("entry_price"),
                                          timeframe=tf)
            return payload_to_json(payload)
        except Exception:
            pass

    return None


def _read_js(path: Path) -> str:
    """Read a local JS file for inline embedding; returns empty string on error."""
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _chart_data_uri(value: Any) -> str | None:
    if not value:
        return None
    text = str(value)
    if text.startswith("data:image/"):
        return text
    path = Path(text)
    if not path.is_absolute():
        path = settings.BASE_DIR / path
    if not path.exists() or not path.is_file():
        return None
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{payload}"


def _list(context: dict[str, Any], *keys: str) -> list[Any]:
    for key in keys:
        value = context.get(key)
        if value is not None:
            return list(value)
    return []


def _field(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _screener_url(symbol: str) -> str:
    return f"https://www.screener.in/company/{quote(symbol, safe='')}/"


def _coalesce(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _filter_detail(item: dict[str, Any], filter_name: str, detail_name: str) -> Any:
    return ((item.get("filters") or {}).get(filter_name) or {}).get("details", {}).get(detail_name)


def _pattern_names(values: Iterable[Any]) -> list[str]:
    names = []
    for value in values:
        name = value if isinstance(value, str) else _field(value, "pattern")
        if name:
            names.append(str(name))
    return names


def _risk_reward(entry: float | None, target: float | None, stop_loss: float | None) -> str:
    if entry is None or target is None or stop_loss is None:
        return "N/A"
    risk = max(entry - stop_loss, 0.0)
    reward = max(target - entry, 0.0)
    return _fmt_number(reward / risk if risk > 0 else 0.0) + ":1"


def _default_action_plan(entry: float | None, target: float | None, stop_loss: float | None) -> str:
    return (
        f"Entry above {_fmt_money(entry)}. Target {_fmt_money(target)}. "
        f"Stop {_fmt_money(stop_loss)}. Respect the stop if the setup fails."
    )


def _default_risk_note(stop_loss: float | None) -> str:
    return f"The setup is invalid if price closes below {_fmt_money(stop_loss)}."


def _average_score(items: list[dict[str, Any]]) -> str:
    if not items:
        return "N/A"
    return _fmt_number(sum(item["score"] for item in items) / len(items))


def _sector_status(vs_nifty: float | None) -> str:
    if vs_nifty is None:
        return "UNKNOWN"
    if vs_nifty >= settings.SECTOR_RS["leading_threshold"]:
        return "LEADING"
    if vs_nifty <= -settings.SECTOR_RS["lagging_threshold"]:
        return "LAGGING"
    return "NEUTRAL"


def _status_class(value: Any) -> str:
    text = str(value or "unknown").lower()
    if "bear" in text:
        return "bear"
    if "uptrend" in text or "bull" in text:
        return "bull"
    if "neutral" in text:
        return "neutral"
    return "unknown"


def _tier(score: int) -> str:
    if score >= settings.CONVICTION_TIERS["HIGHEST"]:
        return "HIGHEST"
    if score >= settings.CONVICTION_TIERS["HIGH"]:
        return "HIGH"
    if score >= settings.CONVICTION_TIERS["MEDIUM"]:
        return "MEDIUM"
    return "SKIP"


def _format_datetime(value: Any) -> str:
    if value is None:
        return datetime.now().strftime("%d %b %Y, %H:%M")
    if isinstance(value, datetime):
        return value.strftime("%d %b %Y, %H:%M")
    return str(value)


def _fmt_money(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "N/A"
    return "Rs." + f"{number:,.2f}".rstrip("0").rstrip(".")


def _fmt_percent(value: Any, *, signed: bool = True) -> str:
    number = _number(value)
    if number is None:
        return "N/A"
    if signed:
        return f"{number:+.2f}%".replace("+0.00", "0.00")
    return f"{number:.1f}%"


def _fmt_volume(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "N/A"
    absolute = abs(number)
    if absolute >= 10_000_000:
        return f"{number / 10_000_000:.2f}Cr".rstrip("0").rstrip(".")
    if absolute >= 100_000:
        return f"{number / 100_000:.2f}L".rstrip("0").rstrip(".")
    if absolute >= 1_000:
        return f"{number / 1_000:.2f}K".rstrip("0").rstrip(".")
    return f"{number:.0f}"


def _fmt_number(value: Any, *, suffix: str = "") -> str:
    number = _number(value)
    if number is None:
        return "N/A"
    return f"{number:,.2f}".rstrip("0").rstrip(".") + suffix


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _int(value: Any, *, default: int = 0) -> int:
    number = _number(value)
    return default if number is None else int(round(number))
