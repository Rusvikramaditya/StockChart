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
    cup_handle,
    inv_head_shoulders,
    multiyear_breakout,
    vcp,
)

ALL_DETECTORS = [
    cup_handle.detect,
    vcp.detect,
    inv_head_shoulders.detect,
    multiyear_breakout.detect,
]

PROFILE_DETECTORS = {
    "nifty500": [
        vcp.detect,
        inv_head_shoulders.detect,
    ],
    "small_mid_liquid": [
        cup_handle.detect,
        vcp.detect,
        multiyear_breakout.detect,
    ],
    "watchlist": ALL_DETECTORS,
}


PROFILE_SYMBOL_PATHS = {
    "nifty500": settings.NIFTY500_DHAN_CSV,
    "small_mid_liquid": settings.SMALL_MID_LIQUID_CSV,
    "watchlist": settings.WATCHLIST_CSV,
}


def get_detectors_for_universe(universe_name: str | None, *, symbol: str | None = None) -> list:
    """Return live detectors allowed for a universe profile."""

    key = _normalise_profile_name(universe_name)
    if key == "all_nse_equity" and symbol:
        key = _profile_for_symbol(symbol) or key
    return list(PROFILE_DETECTORS.get(key, ALL_DETECTORS))


def _normalise_profile_name(value: str | None) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


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


__all__ = ["ALL_DETECTORS", "PROFILE_DETECTORS", "get_detectors_for_universe"]
