"""Inverse Head & Shoulders detector.

Strict textbook geometry: shoulders match within tight tolerance, head sits
meaningfully below shoulder average, time symmetry between left/right halves,
sloped neckline with capped downslope, prior downtrend into the left shoulder
(reversal context), and a tradable stop distance.

Each detected pattern carries a 0-10 ``pattern_quality_score`` independent of
the conviction confidence, so the dashboard can grade textbook-ness.

Plotting geometry surfaced via ``extra``:
    left_shoulder_idx, head_idx, right_shoulder_idx   (relative to window)
    left_neck_idx, left_neck_price                    (left-side neckline anchor)
    right_neck_idx, right_neck_price                  (right-side neckline anchor)
    neckline                                          (sloped value at current bar)
"""

from __future__ import annotations

from itertools import combinations

import numpy as np

from config import settings
from patterns.base import PatternResult
from patterns.utils import clip_confidence, has_ohlcv, local_lows, series


def detect(daily: dict, weekly: dict | None = None) -> list[PatternResult]:
    cfg = settings.INV_HEAD_SHOULDERS
    lookback = int(cfg["lookback_bars"])
    if not has_ohlcv(daily, lookback):
        return []

    high_full = series(daily, "high")
    low_full = series(daily, "low")
    close_full = series(daily, "close")
    volume_full = series(daily, "volume")
    n = len(close_full)
    if n < lookback:
        return []

    high = high_full[-lookback:]
    low = low_full[-lookback:]
    close = close_full[-lookback:]
    volume = volume_full[-lookback:]

    order = int(cfg["argrelextrema_order"])
    troughs = local_lows(low, order)
    if len(troughs) < 3:
        return []

    sym_max = float(cfg["shoulder_symmetry_pct"])
    head_depth_min = float(cfg["min_head_depth_vs_shoulder_pct"])
    time_ratio_max = float(cfg["max_time_asymmetry_ratio"])
    min_pat_bars = int(cfg["min_pattern_bars"])
    downslope_max = float(cfg["max_neckline_downslope_pct"])
    prior_window = int(cfg["prior_downtrend_lookback_bars"])
    decline_min = float(cfg["min_prior_decline_pct"])
    rs_max_age = int(cfg["right_shoulder_max_age_bars"])
    invalid_tol = float(cfg["invalidation_tolerance_pct"])
    max_ext = float(cfg["max_breakout_extension_pct"])
    max_stop_dist = float(cfg["max_stop_distance_pct"])
    within_breakout = float(cfg["within_breakout_pct"])

    latest_close = float(close[-1])

    best: dict | None = None
    for triple in combinations(troughs.tolist(), 3):
        ls_idx, head_idx, rs_idx = (int(x) for x in triple)
        if not (ls_idx < head_idx < rs_idx):
            continue

        ls_low = float(low[ls_idx])
        head_low = float(low[head_idx])
        rs_low = float(low[rs_idx])
        if not (head_low < ls_low and head_low < rs_low):
            continue

        # Shoulder symmetry
        shoulder_avg = (ls_low + rs_low) / 2.0
        if shoulder_avg <= 0:
            continue
        symmetry_pct = abs(ls_low - rs_low) / shoulder_avg * 100.0
        if symmetry_pct > sym_max:
            continue

        # Head depth below shoulder average
        head_depth_pct = (shoulder_avg - head_low) / shoulder_avg * 100.0
        if head_depth_pct < head_depth_min:
            continue

        # Time symmetry
        left_span = head_idx - ls_idx
        right_span = rs_idx - head_idx
        if left_span <= 0 or right_span <= 0:
            continue
        time_ratio = max(left_span, right_span) / max(1, min(left_span, right_span))
        if time_ratio > time_ratio_max:
            continue

        # Pattern duration
        if (rs_idx - ls_idx) < min_pat_bars:
            continue

        # Right shoulder recency
        if (lookback - 1 - rs_idx) > rs_max_age:
            continue

        # Invalidation: no lower-low after right shoulder
        if rs_idx + 1 < lookback:
            after = low[rs_idx + 1:]
            if float(np.min(after)) < rs_low * (1.0 - invalid_tol / 100.0):
                continue

        # Neckline anchors: highest high inside each arch.
        left_neck_idx = ls_idx + int(np.argmax(high[ls_idx:head_idx + 1]))
        right_neck_idx = head_idx + int(np.argmax(high[head_idx:rs_idx + 1]))
        left_neck_price = float(high[left_neck_idx])
        right_neck_price = float(high[right_neck_idx])
        if left_neck_price <= 0 or right_neck_price <= 0:
            continue
        if right_neck_idx <= left_neck_idx:
            continue

        downslope_pct = (left_neck_price - right_neck_price) / left_neck_price * 100.0
        if downslope_pct > downslope_max:
            continue

        slope = (right_neck_price - left_neck_price) / (right_neck_idx - left_neck_idx)

        def neckline_at(idx: int) -> float:
            return left_neck_price + slope * (idx - left_neck_idx)

        current_idx = lookback - 1
        neckline_now = neckline_at(current_idx)
        if neckline_now <= 0:
            continue

        # Prior downtrend (within window, before LS)
        prior_start = max(0, ls_idx - prior_window)
        if ls_idx - prior_start < 5:
            continue  # not enough history within window to verify reversal context
        prior_peak = float(np.max(high[prior_start:ls_idx]))
        if prior_peak <= 0:
            continue
        prior_decline_pct = (prior_peak - ls_low) / prior_peak * 100.0
        if prior_decline_pct < decline_min:
            continue

        # Stop = right shoulder low (last swing low before breakout)
        stop_loss = rs_low
        stop_distance_pct = (neckline_now - stop_loss) / neckline_now * 100.0
        if stop_distance_pct > max_stop_dist or stop_distance_pct <= 0:
            continue

        # Breakout / proximity gates
        breakout = latest_close > neckline_now
        if breakout:
            extension_pct = (latest_close - neckline_now) / neckline_now * 100.0
            if extension_pct > max_ext:
                continue
            distance_to_pivot_pct = 0.0
        else:
            distance_to_pivot_pct = (neckline_now - latest_close) / neckline_now * 100.0
            if distance_to_pivot_pct > within_breakout:
                continue

        # Measured-move target: depth below sloped neckline at head's x.
        depth = neckline_at(head_idx) - head_low
        if depth <= 0:
            continue
        target = neckline_now + depth

        # Volume slices (centered windows around each pivot, clipped to bounds).
        ls_vol = _vol_window(volume, ls_idx, half=2)
        head_vol = _vol_window(volume, head_idx, half=2)
        rs_vol = _vol_window(volume, rs_idx, half=2)

        quality = _pattern_quality(
            symmetry_pct=symmetry_pct,
            head_depth_pct=head_depth_pct,
            time_ratio=time_ratio,
            downslope_pct=downslope_pct,
            ls_vol=ls_vol,
            head_vol=head_vol,
            rs_vol=rs_vol,
            prior_decline_pct=prior_decline_pct,
            breakout=breakout,
            distance_to_pivot_pct=distance_to_pivot_pct,
            stop_distance_pct=stop_distance_pct,
        )

        candidate = {
            "ls_idx": ls_idx,
            "head_idx": head_idx,
            "rs_idx": rs_idx,
            "ls_low": ls_low,
            "head_low": head_low,
            "rs_low": rs_low,
            "symmetry_pct": symmetry_pct,
            "head_depth_pct": head_depth_pct,
            "time_ratio": time_ratio,
            "downslope_pct": downslope_pct,
            "left_neck_idx": left_neck_idx,
            "right_neck_idx": right_neck_idx,
            "left_neck_price": left_neck_price,
            "right_neck_price": right_neck_price,
            "neckline_now": neckline_now,
            "depth": depth,
            "target": target,
            "stop_loss": stop_loss,
            "stop_distance_pct": stop_distance_pct,
            "breakout": breakout,
            "prior_decline_pct": prior_decline_pct,
            "distance_to_pivot_pct": distance_to_pivot_pct,
            "quality": quality,
        }
        if best is None or candidate["quality"]["total"] > best["quality"]["total"]:
            best = candidate

    if best is None:
        return []

    quality_score = best["quality"]["total"]

    # Conviction confidence: legacy 0-100 used by the scorer alongside filters.
    confidence = 58.0 + max(0.0, 20.0 - best["symmetry_pct"])
    if best["breakout"]:
        confidence += 10.0
    confidence = clip_confidence(confidence)

    pivot = best["neckline_now"]

    return [
        PatternResult(
            pattern="Inverse Head & Shoulders",
            status="BREAKING OUT" if best["breakout"] else "PIVOT READY",
            pivot=round(pivot, 2),
            target=round(best["target"], 2),
            stop_loss=round(best["stop_loss"], 2),
            confidence=confidence,
            explanation=(
                f"Shoulders symmetric within {best['symmetry_pct']:.1f}%, head "
                f"{best['head_depth_pct']:.1f}% below shoulder avg, prior decline "
                f"{best['prior_decline_pct']:.1f}%; pattern grade "
                f"{quality_score:.1f}/10."
            ),
            timeframe="daily",
            bars_in_pattern=lookback,
            quality_score=confidence,
            extra={
                "left_shoulder_idx": best["ls_idx"],
                "head_idx": best["head_idx"],
                "right_shoulder_idx": best["rs_idx"],
                "left_neck_idx": best["left_neck_idx"],
                "right_neck_idx": best["right_neck_idx"],
                "left_neck_price": round(best["left_neck_price"], 2),
                "right_neck_price": round(best["right_neck_price"], 2),
                "neckline": round(pivot, 2),
                "symmetry_pct": round(best["symmetry_pct"], 2),
                "head_depth_pct": round(best["head_depth_pct"], 2),
                "time_ratio": round(best["time_ratio"], 2),
                "neckline_downslope_pct": round(best["downslope_pct"], 2),
                "prior_decline_pct": round(best["prior_decline_pct"], 2),
                "stop_distance_pct": round(best["stop_distance_pct"], 2),
                "pattern_quality_score": quality_score,
                "pattern_quality_breakdown": best["quality"]["components"],
            },
        )
    ]


def _vol_window(volume: np.ndarray, idx: int, *, half: int) -> np.ndarray:
    start = max(0, idx - half)
    end = min(len(volume), idx + half + 1)
    return volume[start:end]


def _pattern_quality(
    *,
    symmetry_pct: float,
    head_depth_pct: float,
    time_ratio: float,
    downslope_pct: float,
    ls_vol: np.ndarray,
    head_vol: np.ndarray,
    rs_vol: np.ndarray,
    prior_decline_pct: float,
    breakout: bool,
    distance_to_pivot_pct: float,
    stop_distance_pct: float,
) -> dict:
    """Return 0-10 IHS grade with per-component breakdown.

    Components (max points):
        shoulder_symmetry   2.0   |LS-RS|/avg shoulder low
        head_depth          1.5   head depth vs shoulder avg (deeper = stronger)
        time_symmetry       1.5   left vs right time spans
        neckline_quality    1.0   flat / upsloping = full; downslope penalized
        volume_pattern      1.5   RS_vol < LS_vol (drying-up) + head < LS
        prior_downtrend     1.0   real decline into LS = reversal context
        breakout_proximity  1.0   breakout = full; near neckline = partial
        stop_tightness      0.5   stop close to neckline = lower risk
    """
    # 1. Shoulder symmetry (max 2.0)
    if symmetry_pct <= 2.0:
        sym_pts = 2.0
    elif symmetry_pct <= 4.0:
        sym_pts = 1.5
    elif symmetry_pct <= 7.0:
        sym_pts = 1.0
    else:
        sym_pts = 0.3

    # 2. Head depth (max 1.5)
    if head_depth_pct >= 15.0:
        depth_pts = 1.5
    elif head_depth_pct >= 10.0:
        depth_pts = 1.0
    elif head_depth_pct >= 5.0:
        depth_pts = 0.5
    elif head_depth_pct >= 3.0:
        depth_pts = 0.2
    else:
        depth_pts = 0.0

    # 3. Time symmetry (max 1.5)
    if time_ratio <= 1.3:
        time_pts = 1.5
    elif time_ratio <= 1.8:
        time_pts = 1.0
    elif time_ratio <= 2.5:
        time_pts = 0.5
    else:
        time_pts = 0.2

    # 4. Neckline quality (max 1.0). Upslope/flat full; downslope penalized.
    if downslope_pct <= 0.0:
        neck_pts = 1.0
    elif downslope_pct <= 2.0:
        neck_pts = 0.6
    elif downslope_pct <= 5.0:
        neck_pts = 0.3
    else:
        neck_pts = 0.0

    # 5. Volume pattern (max 1.5).
    vol_pts = 0.0
    ls_avg = float(np.mean(ls_vol)) if ls_vol.size else 0.0
    rs_avg = float(np.mean(rs_vol)) if rs_vol.size else 0.0
    head_avg = float(np.mean(head_vol)) if head_vol.size else 0.0
    if ls_avg > 0:
        rs_ratio = rs_avg / ls_avg
        if rs_ratio <= 0.7:
            vol_pts += 1.0
        elif rs_ratio < 1.0:
            vol_pts += 0.5
        if head_avg < ls_avg * 0.9:
            vol_pts += 0.5

    # 6. Prior downtrend (max 1.0)
    if prior_decline_pct >= 15.0:
        trend_pts = 1.0
    elif prior_decline_pct >= 10.0:
        trend_pts = 0.6
    elif prior_decline_pct >= 8.0:
        trend_pts = 0.3
    else:
        trend_pts = 0.0

    # 7. Breakout proximity (max 1.0)
    if breakout:
        prox_pts = 1.0
    elif distance_to_pivot_pct <= 1.5:
        prox_pts = 0.7
    elif distance_to_pivot_pct <= 3.0:
        prox_pts = 0.4
    else:
        prox_pts = 0.1

    # 8. Stop tightness (max 0.5)
    if stop_distance_pct <= 4.0:
        stop_pts = 0.5
    elif stop_distance_pct <= 6.0:
        stop_pts = 0.3
    else:
        stop_pts = 0.2

    total = sym_pts + depth_pts + time_pts + neck_pts + vol_pts + trend_pts + prox_pts + stop_pts
    total = round(max(0.0, min(10.0, total)), 1)

    return {
        "total": total,
        "components": {
            "shoulder_symmetry": round(sym_pts, 2),
            "head_depth": round(depth_pts, 2),
            "time_symmetry": round(time_pts, 2),
            "neckline_quality": round(neck_pts, 2),
            "volume_pattern": round(vol_pts, 2),
            "prior_downtrend": round(trend_pts, 2),
            "breakout_proximity": round(prox_pts, 2),
            "stop_tightness": round(stop_pts, 2),
        },
    }
