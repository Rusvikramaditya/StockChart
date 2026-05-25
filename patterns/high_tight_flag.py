"""High Tight Flag detector.

Rare power-pattern detector: a very large advance in a short window followed
by a controlled, volume-contracting flag near the highs.
"""

from __future__ import annotations

import numpy as np

from config import settings
from patterns.base import PatternResult
from patterns.utils import clip_confidence, has_ohlcv, series


def detect(daily: dict, weekly: dict | None = None) -> list[PatternResult]:
    cfg = settings.HIGH_TIGHT_FLAG
    max_bars = int(cfg["advance_max_bars"]) + int(cfg["flag_max_bars"]) + 5
    if not has_ohlcv(daily, max_bars):
        return []

    high = series(daily, "high")
    low = series(daily, "low")
    close = series(daily, "close")
    volume = series(daily, "volume")
    n = len(close)
    latest_close = float(close[-1])
    best: dict | None = None

    for flag_len in range(int(cfg["flag_min_bars"]), int(cfg["flag_max_bars"]) + 1):
        flag_start = n - flag_len - 1
        flag_end = n - 1
        if flag_start <= 0:
            continue
        flag_highs = high[flag_start:flag_end]
        flag_lows = low[flag_start:flag_end]
        flag_closes = close[flag_start:flag_end]
        flag_volume = volume[flag_start:flag_end]
        if flag_highs.size == 0:
            continue

        pivot = float(np.max(flag_highs))
        flag_low = float(np.min(flag_lows))
        if pivot <= 0 or flag_low <= 0:
            continue

        first_third = flag_closes[: max(1, flag_len // 3)]
        last_third = flag_closes[-max(1, flag_len // 3):]
        slope_pct = (
            (float(last_third.mean()) - float(first_third.mean())) / float(first_third.mean()) * 100.0
            if first_third.size and float(first_third.mean()) > 0
            else 0.0
        )
        if slope_pct > float(cfg["max_flag_upslope_pct"]):
            continue

        for advance_len in range(int(cfg["advance_min_bars"]), int(cfg["advance_max_bars"]) + 1):
            advance_start = flag_start - advance_len
            advance_end = flag_start - 1
            if advance_start < 0:
                continue

            start_close = float(close[advance_start])
            end_close = float(close[advance_end])
            if start_close <= 0:
                continue
            advance_pct = (end_close / start_close - 1.0) * 100.0
            if advance_pct < float(cfg["min_advance_pct"]):
                continue

            pullback_pct = (end_close - flag_low) / end_close * 100.0 if end_close > 0 else 999.0
            if not (float(cfg["min_flag_pullback_pct"]) <= pullback_pct <= float(cfg["max_flag_pullback_pct"])):
                continue

            advance_volume = volume[advance_start : advance_end + 1]
            advance_avg_vol = float(np.mean(advance_volume)) if advance_volume.size else 0.0
            flag_avg_vol = float(np.mean(flag_volume)) if flag_volume.size else 0.0
            vol_ratio = flag_avg_vol / advance_avg_vol if advance_avg_vol > 0 else 1.0
            if vol_ratio > float(cfg["max_flag_volume_ratio"]):
                continue

            breakout = latest_close > pivot
            if breakout:
                extension_pct = (latest_close - pivot) / pivot * 100.0
                if extension_pct > float(cfg["max_breakout_extension_pct"]):
                    continue
                distance_to_pivot_pct = 0.0
            else:
                distance_to_pivot_pct = (pivot - latest_close) / pivot * 100.0
                if distance_to_pivot_pct > float(cfg["within_breakout_pct"]):
                    continue

            stop_distance_pct = (pivot - flag_low) / pivot * 100.0
            if stop_distance_pct <= 0 or stop_distance_pct > float(cfg["max_stop_distance_pct"]):
                continue

            risk = pivot - flag_low
            target = pivot + risk * 2.0
            quality = _pattern_quality(
                advance_pct=advance_pct,
                pullback_pct=pullback_pct,
                vol_ratio=vol_ratio,
                slope_pct=slope_pct,
                breakout=breakout,
                distance_to_pivot_pct=distance_to_pivot_pct,
                stop_distance_pct=stop_distance_pct,
            )
            candidate = {
                "advance_start": advance_start,
                "advance_end": advance_end,
                "advance_len": advance_len,
                "flag_len": flag_len,
                "advance_pct": advance_pct,
                "pullback_pct": pullback_pct,
                "vol_ratio": vol_ratio,
                "slope_pct": slope_pct,
                "pivot": pivot,
                "target": target,
                "stop_loss": flag_low,
                "breakout": breakout,
                "distance_to_pivot_pct": distance_to_pivot_pct,
                "stop_distance_pct": stop_distance_pct,
                "quality": quality,
            }
            if best is None or candidate["quality"]["total"] > best["quality"]["total"]:
                best = candidate

    if best is None:
        return []

    quality_score = best["quality"]["total"]
    confidence = 60.0 + min(20.0, (best["advance_pct"] - 60.0) / 2.0)
    confidence += max(0.0, (1.0 - best["vol_ratio"]) * 18.0)
    if best["breakout"]:
        confidence += 8.0
    confidence = clip_confidence(confidence)

    return [
        PatternResult(
            pattern="High Tight Flag",
            status="BREAKING OUT" if best["breakout"] else "PIVOT READY",
            pivot=round(best["pivot"], 2),
            target=round(best["target"], 2),
            stop_loss=round(best["stop_loss"], 2),
            confidence=confidence,
            explanation=(
                f"Advance gained {best['advance_pct']:.1f}% in {best['advance_len']} bars; "
                f"flag pulled back {best['pullback_pct']:.1f}% over {best['flag_len']} bars, "
                f"volume ratio {best['vol_ratio']:.2f}; pattern grade {quality_score:.1f}/10."
            ),
            timeframe="daily",
            bars_in_pattern=int(best["advance_len"] + best["flag_len"]),
            quality_score=confidence,
            extra={
                "advance_start_idx": best["advance_start"],
                "advance_end_idx": best["advance_end"],
                "advance_pct": round(best["advance_pct"], 2),
                "flag_len": best["flag_len"],
                "pullback_pct": round(best["pullback_pct"], 2),
                "flag_volume_ratio": round(best["vol_ratio"], 2),
                "flag_slope_pct": round(best["slope_pct"], 2),
                "stop_distance_pct": round(best["stop_distance_pct"], 2),
                "pattern_quality_score": quality_score,
                "pattern_quality_breakdown": best["quality"]["components"],
            },
        )
    ]


def _pattern_quality(
    *,
    advance_pct: float,
    pullback_pct: float,
    vol_ratio: float,
    slope_pct: float,
    breakout: bool,
    distance_to_pivot_pct: float,
    stop_distance_pct: float,
) -> dict:
    if advance_pct >= 110.0:
        advance_pts = 2.5
    elif advance_pct >= 95.0:
        advance_pts = 2.0
    else:
        advance_pts = 1.4

    if 10.0 <= pullback_pct <= 18.0:
        pullback_pts = 1.5
    elif pullback_pct <= 22.0:
        pullback_pts = 1.0
    else:
        pullback_pts = 0.5

    if vol_ratio <= 0.45:
        vol_pts = 1.5
    elif vol_ratio <= 0.6:
        vol_pts = 1.0
    else:
        vol_pts = 0.5

    if slope_pct <= -2.0:
        slope_pts = 1.0
    elif slope_pct <= 0.0:
        slope_pts = 0.7
    else:
        slope_pts = 0.3

    if breakout:
        prox_pts = 1.0
    elif distance_to_pivot_pct <= 1.0:
        prox_pts = 0.8
    else:
        prox_pts = 0.4

    if stop_distance_pct <= 8.0:
        stop_pts = 1.5
    elif stop_distance_pct <= 12.0:
        stop_pts = 1.0
    else:
        stop_pts = 0.5

    rarity_pts = 1.0
    total = advance_pts + pullback_pts + vol_pts + slope_pts + prox_pts + stop_pts + rarity_pts
    total = round(max(0.0, min(10.0, total)), 1)
    return {
        "total": total,
        "components": {
            "advance_power": round(advance_pts, 2),
            "flag_pullback": round(pullback_pts, 2),
            "volume_contraction": round(vol_pts, 2),
            "flag_direction": round(slope_pts, 2),
            "pivot_proximity": round(prox_pts, 2),
            "stop_tightness": round(stop_pts, 2),
            "rarity": round(rarity_pts, 2),
        },
    }
