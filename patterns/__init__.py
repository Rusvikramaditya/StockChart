"""Pattern detector registry."""

from patterns import (
    ascending_triangle,
    bull_flag,
    cup_handle,
    inv_head_shoulders,
    multiyear_breakout,
    supertrend,
    vcp,
)

ALL_DETECTORS = [
    cup_handle.detect,
    ascending_triangle.detect,
    bull_flag.detect,
    vcp.detect,
    inv_head_shoulders.detect,
    supertrend.detect,
    multiyear_breakout.detect,
]

__all__ = ["ALL_DETECTORS"]

