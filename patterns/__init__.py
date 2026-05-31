"""Pattern detector registry.

Phase 8 backtests keep detector implementations available for research and
chart rendering, but remove large-sample losers from live scanning until their
thresholds are rebuilt with quality scores.
"""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path

from config import settings
from patterns import (
    ascending_triangle,
    bull_flag,
    cup_handle,
    double_bottom,
    flat_base,
    high_tight_flag,
    inv_head_shoulders,
    multiyear_breakout,
    supertrend,
    vcp,
    weekly_breakout,
)

ALL_DETECTORS = [
    flat_base.detect,
    cup_handle.detect,
    vcp.detect,
    double_bottom.detect,
    high_tight_flag.detect,
    inv_head_shoulders.detect,
    multiyear_breakout.detect,
    ascending_triangle.detect,
    bull_flag.detect,
    supertrend.detect,
]

WEEKLY_DETECTORS = [
    weekly_breakout.detect,
    multiyear_breakout.detect,
]

PROFILE_DETECTORS = {
    # Nifty large-cap scans should prefer tight continuation bases and avoid
    # historically weak broad-profile detectors unless they are on a watchlist.
    "nifty500": [
        flat_base.detect,
        vcp.detect,
        high_tight_flag.detect,
        ascending_triangle.detect,
        bull_flag.detect,
        inv_head_shoulders.detect,
        supertrend.detect,
    ],
    # Small/mid scans keep multi-year breakouts and cup bases, where the
    # latest backtests were better, but avoid noisy IHS flooding.
    "small_mid_liquid": [
        flat_base.detect,
        vcp.detect,
        double_bottom.detect,
        high_tight_flag.detect,
        multiyear_breakout.detect,
        cup_handle.detect,
        ascending_triangle.detect,
        bull_flag.detect,
        supertrend.detect,
    ],
    "watchlist": ALL_DETECTORS,
}


PROFILE_SYMBOL_PATHS = {
    "nifty500": settings.NIFTY500_DHAN_CSV,
    "small_mid_liquid": settings.SMALL_MID_LIQUID_CSV,
    "watchlist": settings.WATCHLIST_CSV,
}


def get_detectors_for_universe(
    universe_name: str | None,
    *,
    symbol: str | None = None,
    scan_timeframe: str = "daily",
) -> list:
    """Return live detectors allowed for a universe profile."""

    timeframe = _normalise_scan_timeframe(scan_timeframe)
    key = _normalise_profile_name(universe_name)
    if key == "all_nse_equity" and symbol:
        key = _profile_for_symbol(symbol) or key
    daily = list(PROFILE_DETECTORS.get(key, ALL_DETECTORS))
    if timeframe == "weekly":
        return list(WEEKLY_DETECTORS)
    if timeframe == "all":
        return _dedupe_detectors([*daily, *WEEKLY_DETECTORS])
    return daily


def _normalise_profile_name(value: str | None) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _normalise_scan_timeframe(value: str | None) -> str:
    text = str(value or "daily").strip().lower().replace("-", "_").replace(" ", "_")
    if text in {"weekly", "week"}:
        return "weekly"
    if text in {"all", "both", "daily_weekly", "weekly_daily"}:
        return "all"
    return "daily"


def _dedupe_detectors(detectors: list) -> list:
    seen: set[tuple[str, str]] = set()
    out = []
    for detector in detectors:
        key = (getattr(detector, "__module__", ""), getattr(detector, "__name__", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(detector)
    return out


def _profile_for_symbol(symbol: str) -> str | None:
    normalized = str(symbol or "").strip().upper()
    if not normalized:
        return None
    for profile in ("watchlist", "nifty500", "small_mid_liquid"):
        if normalized in _profile_symbols(profile):
            return profile
    return None


@lru_cache(maxsize=None)
def _profile_symbols(profile: str) -> frozenset[str]:
    path = PROFILE_SYMBOL_PATHS.get(profile)
    if path is None or not Path(path).exists():
        return frozenset()
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            return frozenset()
        symbol_field = next((field for field in reader.fieldnames if field.strip().lower() == "symbol"), None)
        if symbol_field is None:
            return frozenset()
        return frozenset(
            str(row.get(symbol_field, "")).strip().upper()
            for row in reader
            if str(row.get(symbol_field, "")).strip()
        )


__all__ = ["ALL_DETECTORS", "PROFILE_DETECTORS", "WEEKLY_DETECTORS", "get_detectors_for_universe"]
