"""Pattern detector registry.

Phase 8 backtests keep detector implementations available for research and
chart rendering, but remove large-sample losers from live scanning until their
thresholds are rebuilt with quality scores.
"""

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


def get_detectors_for_universe(universe_name: str | None) -> list:
    """Return live detectors allowed for a universe profile."""

    key = _normalise_profile_name(universe_name)
    return list(PROFILE_DETECTORS.get(key, ALL_DETECTORS))


def _normalise_profile_name(value: str | None) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


__all__ = ["ALL_DETECTORS", "PROFILE_DETECTORS", "get_detectors_for_universe"]
