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

__all__ = ["ALL_DETECTORS"]
