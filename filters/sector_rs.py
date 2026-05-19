"""Sector relative strength filter."""

from __future__ import annotations

import json

import pandas as pd

from config import settings


def compute_sector_rs_cache(loader, symbols: list[str] | None = None) -> dict:
    mapping = load_sector_map()
    selected_symbols = symbols or sorted(mapping.keys())
    sector_names = sorted({mapping.get(symbol, {}).get("sector_index", "NIFTY 50") for symbol in selected_symbols})
    nifty = loader.get_index("NIFTY 50")
    nifty_return = _lookback_return(nifty, settings.SECTOR_RS["lookback_days"])
    sectors = {}
    for sector_name in sector_names:
        sector_df = loader.get_index(sector_name)
        sector_return = _lookback_return(sector_df, settings.SECTOR_RS["lookback_days"])
        sectors[sector_name] = {
            "return_pct": sector_return,
            "vs_nifty_pct": None if sector_return is None or nifty_return is None else round(sector_return - nifty_return, 2),
        }
    return {
        "lookback_days": settings.SECTOR_RS["lookback_days"],
        "nifty_return_pct": nifty_return,
        "sectors": sectors,
        "symbol_to_sector": mapping,
    }


def evaluate(symbol: str, daily: dict, cache: dict) -> dict:
    symbol = symbol.upper()
    mapping = cache.get("symbol_to_sector", {})
    sector_name = mapping.get(symbol, {}).get("sector_index", "NIFTY 50")
    sector_info = cache.get("sectors", {}).get(sector_name, {})
    stock_return = _array_return(daily, int(cache.get("lookback_days", settings.SECTOR_RS["lookback_days"])))
    nifty_return = cache.get("nifty_return_pct")
    stock_vs_nifty = None if stock_return is None or nifty_return is None else round(stock_return - nifty_return, 2)
    sector_vs_nifty = sector_info.get("vs_nifty_pct")
    threshold = float(settings.SECTOR_RS["leading_threshold"])

    stock_leading = stock_vs_nifty is not None and stock_vs_nifty >= threshold
    sector_leading = sector_vs_nifty is not None and sector_vs_nifty >= threshold
    if stock_leading and sector_leading:
        status = "LEADING"
        passed = True
    elif stock_leading or sector_leading:
        status = "NEUTRAL"
        passed = False
    else:
        status = "LAGGING"
        passed = False

    return {
        "name": "sector_rs",
        "passed": passed,
        "status": status,
        "details": {
            "sector_index": sector_name,
            "stock_return_pct": stock_return,
            "nifty_return_pct": nifty_return,
            "sector_return_pct": sector_info.get("return_pct"),
            "stock_vs_nifty_pct": stock_vs_nifty,
            "sector_vs_nifty_pct": sector_vs_nifty,
            "threshold_pct": threshold,
        },
    }


def load_sector_map() -> dict[str, dict]:
    if not settings.SECTOR_MAP_JSON.exists():
        return {}
    payload = json.loads(settings.SECTOR_MAP_JSON.read_text(encoding="utf-8"))
    return {str(key).upper(): value for key, value in payload.get("symbols", {}).items()}


def _lookback_return(frame: pd.DataFrame, lookback_days: int) -> float | None:
    if frame is None or frame.empty or len(frame) <= lookback_days:
        return None
    close = pd.to_numeric(frame["close"], errors="coerce").dropna()
    if len(close) <= lookback_days or float(close.iloc[-1 - lookback_days]) <= 0:
        return None
    return round((float(close.iloc[-1]) / float(close.iloc[-1 - lookback_days]) - 1.0) * 100.0, 2)


def _array_return(daily: dict, lookback_days: int) -> float | None:
    close = daily.get("close")
    if close is None or len(close) <= lookback_days or float(close[-1 - lookback_days]) <= 0:
        return None
    return round((float(close[-1]) / float(close[-1 - lookback_days]) - 1.0) * 100.0, 2)
