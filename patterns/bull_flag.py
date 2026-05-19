"""Bull Flag detector."""

from __future__ import annotations

import numpy as np

from config import settings
from patterns.base import PatternResult
from patterns.utils import clip_confidence, has_ohlcv, pct_change, series


def detect(daily: dict, weekly: dict | None = None) -> list[PatternResult]:
    cfg = settings.BULL_FLAG
    min_bars = int(cfg["pole_max_bars"]) + 25
    if not has_ohlcv(daily, min_bars):
        return []

    high = series(daily, "high")
    low = series(daily, "low")
    close = series(daily, "close")
    volume = series(daily, "volume")

    best: dict | None = None
    for flag_len in range(5, 21):
        pole_end = len(close) - flag_len - 1
        if pole_end <= 0:
            continue
        for pole_len in range(int(cfg["pole_min_bars"]), int(cfg["pole_max_bars"]) + 1):
            pole_start = pole_end - pole_len
            if pole_start < 0:
                continue
            pole_pct = pct_change(float(close[pole_start]), float(close[pole_end]))
            if pole_pct < float(cfg["min_pole_pct"]):
                continue
            flag_high = float(np.max(high[-flag_len:]))
            flag_low = float(np.min(low[-flag_len:]))
            pole_close = float(close[pole_end])
            if pole_close <= 0:
                continue
            pullback_pct = (pole_close - flag_low) / pole_close * 100.0
            if not cfg["min_flag_pullback_pct"] <= pullback_pct <= cfg["max_flag_pullback_pct"]:
                continue
            pole_vol = float(np.mean(volume[pole_start : pole_end + 1]))
            flag_vol = float(np.mean(volume[-flag_len:]))
            vol_ratio = flag_vol / pole_vol if pole_vol > 0 else 0.0
            if vol_ratio > float(cfg["max_flag_vol_ratio"]):
                continue
            latest_close = float(close[-1])
            if latest_close < flag_low or latest_close > flag_high * 1.05:
                continue
            score = pole_pct - pullback_pct + (1.0 - vol_ratio) * 20.0
            candidate = {
                "score": score,
                "pole_start": pole_start,
                "pole_end": pole_end,
                "pole_len": pole_len,
                "flag_len": flag_len,
                "pole_pct": pole_pct,
                "flag_high": flag_high,
                "flag_low": flag_low,
                "pullback_pct": pullback_pct,
                "vol_ratio": vol_ratio,
            }
            if not best or candidate["score"] > best["score"]:
                best = candidate

    if not best:
        return []

    latest_close = float(close[-1])
    breakout = latest_close > best["flag_high"]
    pole_height = float(close[best["pole_end"]] - close[best["pole_start"]])
    pivot = best["flag_high"]
    target = pivot + max(pole_height, 0.0)
    stop_loss = best["flag_low"]
    confidence = 55.0 + min(20.0, best["pole_pct"]) + max(0.0, (0.9 - best["vol_ratio"]) * 25.0)
    if breakout:
        confidence += 8.0

    return [
        PatternResult(
            pattern="Bull Flag",
            status="BREAKING OUT" if breakout else "PIVOT READY",
            pivot=round(pivot, 2),
            target=round(target, 2),
            stop_loss=round(stop_loss, 2),
            confidence=clip_confidence(confidence),
            explanation=(
                f"Pole gained {best['pole_pct']:.1f}% in {best['pole_len']} bars; "
                f"flag pulled back {best['pullback_pct']:.1f}% with volume ratio {best['vol_ratio']:.2f}."
            ),
            timeframe="daily",
            bars_in_pattern=int(best["pole_len"] + best["flag_len"]),
            extra={
                "pole_start_idx": best["pole_start"],
                "pole_end_idx": best["pole_end"],
                "flag_len": best["flag_len"],
                "pole_pct": round(best["pole_pct"], 2),
                "pullback_pct": round(best["pullback_pct"], 2),
                "flag_volume_ratio": round(best["vol_ratio"], 2),
            },
        )
    ]

