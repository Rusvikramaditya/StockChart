"""Bull Flag detector.

Strict textbook geometry: explosive pole (>=15%, 4-15 bars, clean run with
limited intra-pole drawdown) followed by a controlled, volume-contracting
flag (5-15 bars, 3-8% pullback, non-upsloping, vol <= 70% of pole) that
sits inside an existing uptrend.

Each detected pattern carries a 0-10 ``pattern_quality_score`` so the
dashboard can grade textbook-ness independently of the conviction blend.

Pivot bug regression: ``flag_high`` is computed from ``high[:-1]`` so that
``BREAKING OUT`` (close > flag_high) is reachable. Including the current
bar would make breakouts unreachable by candle definition.
"""

from __future__ import annotations

import numpy as np

from config import settings
from patterns.base import PatternResult
from patterns.utils import clip_confidence, has_ohlcv, pct_change, series


def detect(daily: dict, weekly: dict | None = None) -> list[PatternResult]:
    cfg = settings.BULL_FLAG
    pole_max = int(cfg["pole_max_bars"])
    flag_max = int(cfg["flag_max_bars"])
    prior_window = int(cfg["prior_uptrend_lookback_bars"])
    min_bars = pole_max + flag_max + prior_window + 5
    if not has_ohlcv(daily, min_bars):
        return []

    high = series(daily, "high")
    low = series(daily, "low")
    close = series(daily, "close")
    volume = series(daily, "volume")
    n = len(close)

    pole_min = int(cfg["pole_min_bars"])
    flag_min = int(cfg["flag_min_bars"])
    min_pole = float(cfg["min_pole_pct"])
    max_pole_dd = float(cfg["max_pole_drawdown_pct"])
    min_pullback = float(cfg["min_flag_pullback_pct"])
    max_pullback = float(cfg["max_flag_pullback_pct"])
    max_vol_ratio = float(cfg["max_flag_vol_ratio"])
    max_upslope = float(cfg["max_flag_upslope_pct"])
    min_prior_gain = float(cfg["min_prior_gain_pct"])
    max_stop_dist = float(cfg["max_stop_distance_pct"])
    within_breakout = float(cfg["within_breakout_pct"])
    max_ext = float(cfg["max_breakout_extension_pct"])

    latest_close = float(close[-1])

    best: dict | None = None
    for flag_len in range(flag_min, flag_max + 1):
        pole_end = n - flag_len - 1  # last bar of pole = bar before flag starts
        if pole_end <= pole_min:
            continue
        # Flag slice excludes the latest bar from pivot computation so the
        # BREAKING OUT branch is reachable (close <= high otherwise).
        flag_slice_high = high[n - flag_len : -1]
        flag_slice_low = low[n - flag_len : -1]
        flag_slice_close = close[n - flag_len : -1]
        flag_slice_vol = volume[n - flag_len : -1]
        if flag_slice_high.size == 0:
            continue
        flag_high = float(np.max(flag_slice_high))
        flag_low = float(np.min(flag_slice_low))
        if flag_high <= 0:
            continue

        # Flag direction. Bull flag pulls down or sideways. An upsloping
        # flag is a rising wedge (bearish), not continuation.
        first_third = flag_slice_close[: max(1, flag_len // 3)]
        last_third = flag_slice_close[-max(1, flag_len // 3):]
        if first_third.size and last_third.size:
            slope_pct = (float(last_third.mean()) - float(first_third.mean())) / float(first_third.mean()) * 100.0
            if slope_pct > max_upslope:
                continue
        else:
            slope_pct = 0.0

        for pole_len in range(pole_min, pole_max + 1):
            pole_start = pole_end - pole_len
            if pole_start < prior_window:
                continue  # need bars BEFORE pole_start for the prior-uptrend gate
            pole_start_close = float(close[pole_start])
            pole_end_close = float(close[pole_end])
            if pole_start_close <= 0:
                continue
            pole_pct = pct_change(pole_start_close, pole_end_close)
            if pole_pct < min_pole:
                continue

            # Pole cleanliness: max drawdown peak->trough WITHIN the pole bars.
            pole_high_run = high[pole_start : pole_end + 1]
            pole_low_run = low[pole_start : pole_end + 1]
            if pole_high_run.size and pole_low_run.size:
                # Drawdown from running max down to subsequent lows.
                running_max = np.maximum.accumulate(pole_high_run)
                drawdowns = (running_max - pole_low_run) / np.where(running_max > 0, running_max, 1.0) * 100.0
                pole_drawdown_pct = float(np.max(drawdowns))
            else:
                pole_drawdown_pct = 0.0
            if pole_drawdown_pct > max_pole_dd:
                continue

            # Prior uptrend (continuation context).
            prior_start = pole_start - prior_window
            prior_close = float(close[prior_start])
            if prior_close <= 0:
                continue
            prior_gain_pct = (pole_start_close - prior_close) / prior_close * 100.0
            if prior_gain_pct < min_prior_gain:
                continue

            pullback_pct = (pole_end_close - flag_low) / pole_end_close * 100.0
            if not (min_pullback <= pullback_pct <= max_pullback):
                continue

            pole_vol = float(np.mean(volume[pole_start : pole_end + 1]))
            flag_vol = float(np.mean(flag_slice_vol)) if flag_slice_vol.size else 0.0
            vol_ratio = flag_vol / pole_vol if pole_vol > 0 else 1.0
            if vol_ratio > max_vol_ratio:
                continue

            pivot = flag_high
            stop_loss = flag_low
            stop_distance_pct = (pivot - stop_loss) / pivot * 100.0 if pivot > 0 else 999.0
            if stop_distance_pct > max_stop_dist or stop_distance_pct <= 0:
                continue

            breakout = latest_close > pivot
            if breakout:
                extension_pct = (latest_close - pivot) / pivot * 100.0
                if extension_pct > max_ext:
                    continue
                distance_to_pivot_pct = 0.0
            else:
                distance_to_pivot_pct = (pivot - latest_close) / pivot * 100.0
                if distance_to_pivot_pct > within_breakout:
                    continue
                if latest_close < flag_low:
                    continue  # broken below flag = pattern failed

            pole_height = pole_end_close - pole_start_close
            target = pivot + max(pole_height, 0.0)

            quality = _pattern_quality(
                pole_pct=pole_pct,
                pole_drawdown_pct=pole_drawdown_pct,
                pullback_pct=pullback_pct,
                vol_ratio=vol_ratio,
                slope_pct=slope_pct,
                breakout=breakout,
                distance_to_pivot_pct=distance_to_pivot_pct,
                stop_distance_pct=stop_distance_pct,
                prior_gain_pct=prior_gain_pct,
            )

            candidate = {
                "pole_start": pole_start,
                "pole_end": pole_end,
                "pole_len": pole_len,
                "flag_len": flag_len,
                "pole_pct": pole_pct,
                "pole_drawdown_pct": pole_drawdown_pct,
                "flag_high": flag_high,
                "flag_low": flag_low,
                "pullback_pct": pullback_pct,
                "vol_ratio": vol_ratio,
                "slope_pct": slope_pct,
                "prior_gain_pct": prior_gain_pct,
                "stop_distance_pct": stop_distance_pct,
                "distance_to_pivot_pct": distance_to_pivot_pct,
                "breakout": breakout,
                "pivot": pivot,
                "stop_loss": stop_loss,
                "target": target,
                "quality": quality,
            }
            if best is None or candidate["quality"]["total"] > best["quality"]["total"]:
                best = candidate

    if best is None:
        return []

    quality_score = best["quality"]["total"]
    # Legacy conviction confidence (0-100) used by the scorer's filter blend.
    confidence = 55.0 + min(20.0, best["pole_pct"]) + max(0.0, (max_vol_ratio - best["vol_ratio"]) * 25.0)
    if best["breakout"]:
        confidence += 8.0
    confidence = clip_confidence(confidence)

    return [
        PatternResult(
            pattern="Bull Flag",
            status="BREAKING OUT" if best["breakout"] else "PIVOT READY",
            pivot=round(best["pivot"], 2),
            target=round(best["target"], 2),
            stop_loss=round(best["stop_loss"], 2),
            confidence=confidence,
            explanation=(
                f"Pole gained {best['pole_pct']:.1f}% in {best['pole_len']} bars with "
                f"{best['pole_drawdown_pct']:.1f}% max drawdown; flag pulled back "
                f"{best['pullback_pct']:.1f}% over {best['flag_len']} bars, vol ratio "
                f"{best['vol_ratio']:.2f}; pattern grade {quality_score:.1f}/10."
            ),
            timeframe="daily",
            bars_in_pattern=int(best["pole_len"] + best["flag_len"]),
            quality_score=confidence,
            extra={
                "pole_start_idx": best["pole_start"],
                "pole_end_idx": best["pole_end"],
                "flag_len": best["flag_len"],
                "pole_pct": round(best["pole_pct"], 2),
                "pole_drawdown_pct": round(best["pole_drawdown_pct"], 2),
                "pullback_pct": round(best["pullback_pct"], 2),
                "flag_volume_ratio": round(best["vol_ratio"], 2),
                "flag_slope_pct": round(best["slope_pct"], 2),
                "prior_gain_pct": round(best["prior_gain_pct"], 2),
                "stop_distance_pct": round(best["stop_distance_pct"], 2),
                "pattern_quality_score": quality_score,
                "pattern_quality_breakdown": best["quality"]["components"],
            },
        )
    ]


def _pattern_quality(
    *,
    pole_pct: float,
    pole_drawdown_pct: float,
    pullback_pct: float,
    vol_ratio: float,
    slope_pct: float,
    breakout: bool,
    distance_to_pivot_pct: float,
    stop_distance_pct: float,
    prior_gain_pct: float,
) -> dict:
    """Return a 0-10 bull-flag grade with per-component breakdown.

    Components (max points):
        pole_strength       2.0   how big a move powered the pole
        pole_cleanliness    1.5   max intra-pole drawdown (tighter = better)
        flag_pullback       1.5   pullback in the 3-6% sweet spot
        volume_contraction  1.5   flag vol vs pole vol (lower = better)
        flag_direction      1.0   downsloping > sideways > flat-up
        prior_uptrend       0.5   continuation context (prior_gain present)
        breakout_proximity  1.0   broken out / near pivot
        stop_tightness      1.0   stop close to pivot = lower risk
    """
    # 1. Pole strength (max 2.0)
    if pole_pct >= 25.0:
        pole_pts = 2.0
    elif pole_pct >= 20.0:
        pole_pts = 1.5
    elif pole_pct >= 15.0:
        pole_pts = 1.0
    else:
        pole_pts = 0.5

    # 2. Pole cleanliness (max 1.5)
    if pole_drawdown_pct <= 2.0:
        clean_pts = 1.5
    elif pole_drawdown_pct <= 4.0:
        clean_pts = 1.0
    elif pole_drawdown_pct <= 6.0:
        clean_pts = 0.5
    else:
        clean_pts = 0.2

    # 3. Flag pullback (max 1.5)
    if 3.0 <= pullback_pct <= 6.0:
        pullback_pts = 1.5
    elif 6.0 < pullback_pct <= 8.0:
        pullback_pts = 1.0
    else:
        pullback_pts = 0.4

    # 4. Volume contraction (max 1.5)
    if vol_ratio <= 0.4:
        vol_pts = 1.5
    elif vol_ratio <= 0.55:
        vol_pts = 1.0
    elif vol_ratio <= 0.7:
        vol_pts = 0.5
    else:
        vol_pts = 0.0

    # 5. Flag direction (max 1.0). Downsloping > flat > slight upslope.
    if slope_pct <= -1.0:
        dir_pts = 1.0
    elif slope_pct <= 0.0:
        dir_pts = 0.7
    elif slope_pct <= 1.0:
        dir_pts = 0.3
    else:
        dir_pts = 0.0

    # 6. Prior uptrend (max 0.5). Continuation context.
    if prior_gain_pct >= 15.0:
        prior_pts = 0.5
    elif prior_gain_pct >= 5.0:
        prior_pts = 0.3
    else:
        prior_pts = 0.0

    # 7. Breakout proximity (max 1.0)
    if breakout:
        prox_pts = 1.0
    elif distance_to_pivot_pct <= 1.0:
        prox_pts = 0.7
    elif distance_to_pivot_pct <= 2.0:
        prox_pts = 0.4
    else:
        prox_pts = 0.1

    # 8. Stop tightness (max 1.0)
    if stop_distance_pct <= 4.0:
        stop_pts = 1.0
    elif stop_distance_pct <= 6.0:
        stop_pts = 0.7
    elif stop_distance_pct <= 8.0:
        stop_pts = 0.4
    else:
        stop_pts = 0.2

    total = pole_pts + clean_pts + pullback_pts + vol_pts + dir_pts + prior_pts + prox_pts + stop_pts
    total = round(max(0.0, min(10.0, total)), 1)
    return {
        "total": total,
        "components": {
            "pole_strength": round(pole_pts, 2),
            "pole_cleanliness": round(clean_pts, 2),
            "flag_pullback": round(pullback_pts, 2),
            "volume_contraction": round(vol_pts, 2),
            "flag_direction": round(dir_pts, 2),
            "prior_uptrend": round(prior_pts, 2),
            "breakout_proximity": round(prox_pts, 2),
            "stop_tightness": round(stop_pts, 2),
        },
    }
