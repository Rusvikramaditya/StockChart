"""Inverse Head & Shoulders detector."""

from __future__ import annotations

import numpy as np

from config import settings
from patterns.base import PatternResult
from patterns.utils import clip_confidence, has_ohlcv, local_lows, series


def detect(daily: dict, weekly: dict | None = None) -> list[PatternResult]:
    cfg = settings.INV_HEAD_SHOULDERS
    lookback = int(cfg["lookback_bars"])
    if not has_ohlcv(daily, lookback):
        return []

    high = series(daily, "high")[-lookback:]
    low = series(daily, "low")[-lookback:]
    close = series(daily, "close")[-lookback:]
    troughs = local_lows(low, int(cfg["argrelextrema_order"]))
    if len(troughs) < 3:
        return []

    best = None
    for idx in range(len(troughs) - 2):
        left_idx, head_idx, right_idx = map(int, troughs[idx : idx + 3])
        left = float(low[left_idx])
        head = float(low[head_idx])
        right = float(low[right_idx])
        if not (head < left and head < right):
            continue
        shoulder_avg = (left + right) / 2.0
        if shoulder_avg <= 0:
            continue
        symmetry_pct = abs(left - right) / shoulder_avg * 100.0
        if symmetry_pct > float(cfg["shoulder_symmetry_pct"]):
            continue
        if right_idx <= head_idx or head_idx <= left_idx:
            continue
        left_neck = float(np.max(high[left_idx:head_idx + 1]))
        right_neck = float(np.max(high[head_idx:right_idx + 1]))
        neckline = max(left_neck, right_neck)
        latest_close = float(close[-1])
        breakout = latest_close > neckline
        if not breakout and (neckline - latest_close) / neckline * 100.0 > 5.0:
            continue
        score = 100.0 - symmetry_pct + (right_idx - left_idx) / lookback * 10.0
        candidate = {
            "score": score,
            "left_idx": left_idx,
            "head_idx": head_idx,
            "right_idx": right_idx,
            "left": left,
            "head": head,
            "right": right,
            "symmetry_pct": symmetry_pct,
            "neckline": neckline,
            "breakout": breakout,
        }
        if not best or candidate["score"] > best["score"]:
            best = candidate

    if not best:
        return []

    neckline = best["neckline"]
    depth = neckline - best["head"]
    pivot = neckline
    target = neckline + max(depth, 0.0)
    stop_loss = min(best["left"], best["right"])
    confidence = 58.0 + max(0.0, 20.0 - best["symmetry_pct"])
    if best["breakout"]:
        confidence += 10.0
    quality_score = clip_confidence(confidence)

    return [
        PatternResult(
            pattern="Inverse Head & Shoulders",
            status="BREAKING OUT" if best["breakout"] else "PIVOT READY",
            pivot=round(pivot, 2),
            target=round(target, 2),
            stop_loss=round(stop_loss, 2),
            confidence=quality_score,
            explanation=(
                f"Three troughs detected with head deepest and shoulder symmetry "
                f"{best['symmetry_pct']:.1f}%."
            ),
            timeframe="daily",
            bars_in_pattern=lookback,
            quality_score=quality_score,
            extra={
                "left_shoulder_idx": best["left_idx"],
                "head_idx": best["head_idx"],
                "right_shoulder_idx": best["right_idx"],
                "symmetry_pct": round(best["symmetry_pct"], 2),
                "neckline": round(neckline, 2),
            },
        )
    ]
