"""Convert OHLCV + scored pattern result into a thesis chart payload for Lightweight Charts."""

from __future__ import annotations

import json
import math
from typing import Any

import pandas as pd


LOOKBACK_BARS = 120


def build_chart_payload(
    df: pd.DataFrame,
    symbol: str,
    pattern_result: Any,
    *,
    company_name: str | None = None,
    timeframe: str = "Daily",
    lookback_bars: int = LOOKBACK_BARS,
) -> dict[str, Any]:
    """Return a JSON-serializable chart payload.

    Raises ValueError if candles or required trade levels are absent.
    """
    frame = _prepare_frame(df, lookback_bars)
    trade_plan = _trade_plan(pattern_result)
    geometry = _geometry(pattern_result)

    bars_in_pattern = _int(_field(pattern_result, "bars_in_pattern"), default=lookback_bars)
    actual_lookback = max(lookback_bars, bars_in_pattern)
    visible = frame.tail(max(20, actual_lookback))
    visible_start = len(frame) - len(visible)

    return {
        "symbol": symbol.upper(),
        "company_name": company_name or symbol.upper(),
        "exchange": "NSE",
        "timeframe": timeframe,
        "source_rows": len(frame),
        "visible_start_index": visible_start,
        "candles": _candle_list(visible),
        "pattern": {
            "type": str(_field(pattern_result, "pattern", "Pattern")),
            "status": str(_field(pattern_result, "status", "")),
            "bars_in_pattern": _int(_field(pattern_result, "bars_in_pattern"), default=len(frame)),
            "geometry": geometry,
        },
        "trade_plan": trade_plan,
        "annotations": _annotations(trade_plan),
    }


def validate_payload(payload: dict[str, Any]) -> None:
    """Raise ValueError if required fields are missing."""
    if not payload.get("candles"):
        raise ValueError("chart_payload: candles list is empty or missing")
    tp = payload.get("trade_plan") or {}
    for field in ("entry", "target", "stop"):
        if tp.get(field) is None:
            raise ValueError(f"chart_payload: trade_plan.{field} is required")


def payload_to_json(payload: dict[str, Any]) -> str:
    """Serialize payload to JSON, safe for embedding in <script> tags."""
    raw = json.dumps(payload, allow_nan=False)
    # Prevent </script> injection in script blocks
    return raw.replace("</script>", "<\\/script>")


# ---------------------------------------------------------------------------
# Internal builders
# ---------------------------------------------------------------------------

def _prepare_frame(df: pd.DataFrame, lookback_bars: int) -> pd.DataFrame:
    if df is None or (hasattr(df, "empty") and df.empty):
        raise ValueError("chart_payload: OHLCV DataFrame is empty or None")

    frame = df.copy()
    col_map = {str(c).lower(): c for c in frame.columns}

    date_col = col_map.get("date") or col_map.get("week")
    if date_col is not None:
        frame.index = pd.to_datetime(frame[date_col], errors="coerce")
    else:
        frame.index = pd.to_datetime(frame.index, errors="coerce")

    rename = {}
    for src, dst in [("open", "Open"), ("high", "High"), ("low", "Low"), ("close", "Close"), ("volume", "Volume")]:
        if src not in col_map:
            raise ValueError(f"chart_payload: missing OHLCV column '{src}'")
        rename[col_map[src]] = dst

    frame = frame.rename(columns=rename)[["Open", "High", "Low", "Close", "Volume"]]
    frame = frame.apply(pd.to_numeric, errors="coerce")
    frame = frame.dropna(subset=["Open", "High", "Low", "Close"])
    frame["Volume"] = frame["Volume"].fillna(0.0)
    frame = frame[~frame.index.isna()].sort_index()
    frame = frame[~frame.index.duplicated(keep="last")]

    if frame.empty:
        raise ValueError("chart_payload: no valid OHLCV rows after cleaning")

    return frame


def _candle_list(frame: pd.DataFrame) -> list[dict[str, Any]]:
    out = []
    for ts, row in frame.iterrows():
        unix = int(pd.Timestamp(ts).timestamp())
        out.append({
            "time": unix,
            "open": _r(row["Open"]),
            "high": _r(row["High"]),
            "low": _r(row["Low"]),
            "close": _r(row["Close"]),
            "volume": round(float(row["Volume"]), 0),
        })
    return out


def _trade_plan(pattern_result: Any) -> dict[str, Any]:
    entry = _num(_field(pattern_result, "pivot"))
    target = _num(_field(pattern_result, "target"))
    stop = _num(_field(pattern_result, "stop_loss"))

    upside_pct: float | None = None
    downside_pct: float | None = None
    reward_risk: float | None = None

    if entry is not None and target is not None and entry > 0:
        upside_pct = round((target - entry) / entry * 100, 2)
    if entry is not None and stop is not None and entry > 0:
        downside_pct = round((entry - stop) / entry * 100, 2)
    if upside_pct is not None and downside_pct is not None and downside_pct > 0:
        reward_risk = round(upside_pct / downside_pct, 2)

    return {
        "entry": entry,
        "target": target,
        "stop": stop,
        "upside_pct": upside_pct,
        "downside_pct": downside_pct,
        "reward_risk": reward_risk,
    }


def _geometry(pattern_result: Any) -> dict[str, Any] | None:
    extra = dict(_field(pattern_result, "extra", {}) or {})
    if not extra:
        return None
    clean: dict[str, Any] = {}
    for k, v in extra.items():
        try:
            json.dumps(v)
            clean[k] = v
        except (TypeError, ValueError):
            pass
    return clean or None


def _annotations(trade_plan: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    role_map = [
        ("entry", "#ff6b00"),
        ("target", "#26a69a"),
        ("stop", "#ef5350"),
    ]
    for role, color in role_map:
        price = trade_plan.get(role)
        if price is not None:
            items.append({"type": "hline", "price": price, "role": role, "color": color})
    return items


def _field(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _num(value: Any) -> float | None:
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def _r(value: Any, decimals: int = 4) -> float:
    return round(float(value), decimals)


def _int(value: Any, *, default: int) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default
