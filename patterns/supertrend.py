"""Supertrend Bullish Flip detector.

Detects a fresh transition from bearish to bullish on the Supertrend
indicator (ATR-based trailing-stop trend follower). Real-money rule:
a "fresh flip" means the flip happened today or yesterday at the
absolute latest; older flips are dropped because price has typically
already moved 1-2 ATRs above the line, killing R:R.

Pivot is the close at the flip bar (not the latest close), so the
reported entry reflects where a disciplined entry would have triggered.
The card surfaces both the entry and the current extension % so the
trader can see if the signal is still actionable.

Each detected pattern carries a 0-10 ``pattern_quality_score`` so the
scorer can demote stale or low-edge flips below HIGHEST tier.
"""

from __future__ import annotations

import numpy as np

from config import settings
from patterns.base import PatternResult
from patterns.utils import clip_confidence, has_ohlcv, series


def detect(daily: dict, weekly: dict | None = None) -> list[PatternResult]:
    cfg = settings.SUPERTREND
    period = int(cfg["atr_period"])
    min_bars = period + 30
    if not has_ohlcv(daily, min_bars):
        return []

    high = series(daily, "high")
    low = series(daily, "low")
    close = series(daily, "close")
    volume = series(daily, "volume")
    atr = _atr(high, low, close, period)
    if len(atr) == 0:
        return []

    line, direction = _supertrend(high, low, close, atr, float(cfg["multiplier"]))
    flip_lookback = int(cfg["flip_lookback_bars"])
    max_age = int(cfg["max_flip_age_bars"])

    if len(direction) < flip_lookback + 2:
        return []

    # Find the most recent bullish flip within the scan window.
    flip_idx: int | None = None
    for idx in range(len(direction) - 1, max(0, len(direction) - flip_lookback - 1), -1):
        if direction[idx] == 1 and direction[idx - 1] == -1:
            flip_idx = idx
            break
    if flip_idx is None:
        return []

    last_idx = len(close) - 1
    flip_age = last_idx - flip_idx
    if flip_age > max_age:
        return []  # stale flip, real-money rule

    latest_close = float(close[-1])
    latest_atr = float(atr[-1])
    flip_close = float(close[flip_idx])
    stop_loss = float(line[-1])

    if stop_loss <= 0 or latest_close <= stop_loss:
        return []
    if flip_close <= 0:
        return []

    # Entry = close at flip bar. Card surfaces extension separately so a
    # late-arriving trader sees how much the move has run.
    pivot = flip_close
    extension_pct = (latest_close - pivot) / pivot * 100.0 if pivot > 0 else 0.0

    stop_distance_pct = (pivot - stop_loss) / pivot * 100.0 if pivot > 0 else 999.0
    if stop_distance_pct > float(cfg["max_stop_distance_pct"]) or stop_distance_pct <= 0:
        return []

    target = pivot + float(cfg["atr_target_multiplier"]) * latest_atr
    if target <= pivot:
        return []

    # Volume confirmation on the flip bar. Required by textbook (real
    # flips usually show a momentum candle with volume expansion).
    flip_window_start = max(0, flip_idx - 20)
    flip_window_end = flip_idx  # exclusive of flip bar
    if flip_window_end - flip_window_start >= 5:
        avg_vol = float(np.mean(volume[flip_window_start:flip_window_end]))
    else:
        avg_vol = 0.0
    flip_vol = float(volume[flip_idx]) if flip_idx < len(volume) else 0.0
    volume_ratio = flip_vol / avg_vol if avg_vol > 0 else 0.0

    quality = _pattern_quality(
        flip_age=flip_age,
        max_age=max_age,
        atr=latest_atr,
        close=latest_close,
        stop_distance_pct=stop_distance_pct,
        extension_pct=extension_pct,
        volume_ratio=volume_ratio,
    )
    pattern_quality_score = quality["total"]

    confidence = 60.0 + min(20.0, stop_distance_pct * 2.0)
    if flip_age == 0:
        confidence += 8.0
    confidence = clip_confidence(confidence)

    return [
        PatternResult(
            pattern="Supertrend Bullish Flip",
            status="BREAKING OUT" if flip_age == 0 else "PIVOT READY",
            pivot=round(pivot, 2),
            target=round(target, 2),
            stop_loss=round(stop_loss, 2),
            confidence=confidence,
            explanation=(
                f"Supertrend flipped bullish {flip_age} bar(s) ago; ATR({period}) "
                f"{latest_atr:.2f}; extension {extension_pct:.1f}% from flip; "
                f"pattern grade {pattern_quality_score:.1f}/10."
            ),
            timeframe="daily",
            bars_in_pattern=period + flip_age,
            quality_score=confidence,
            extra={
                "flip_idx": int(flip_idx),
                "flip_age_bars": int(flip_age),
                "atr": round(latest_atr, 2),
                "supertrend_line": round(stop_loss, 2),
                "multiplier": float(cfg["multiplier"]),
                "extension_pct": round(extension_pct, 2),
                "stop_distance_pct": round(stop_distance_pct, 2),
                "flip_volume_ratio": round(volume_ratio, 2),
                "pattern_quality_score": pattern_quality_score,
                "pattern_quality_breakdown": quality["components"],
            },
        )
    ]


def _pattern_quality(
    *,
    flip_age: int,
    max_age: int,
    atr: float,
    close: float,
    stop_distance_pct: float,
    extension_pct: float,
    volume_ratio: float,
) -> dict:
    """Return 0-10 grade with per-component breakdown.

    Components (max points):
        flip_freshness      3.0   today's flip = 3.0, 1-bar old = 2.0
        atr_regime          1.5   ATR % of price (volatility expansion)
        stop_tightness      2.0   stop close to pivot = lower risk
        entry_extension     1.5   small extension from flip = better entry
        volume_confirmation 2.0   flip bar volume vs prior 20-day avg
    """
    # 1. Freshness (max 3.0)
    if flip_age == 0:
        fresh_pts = 3.0
    elif flip_age == 1:
        fresh_pts = 2.0
    elif flip_age <= max_age:
        fresh_pts = 1.0
    else:
        fresh_pts = 0.0

    # 2. ATR regime (max 1.5). Higher ATR/close = stronger momentum
    # regime, helps the 2.5x ATR target.
    atr_pct_of_close = atr / close * 100.0 if close > 0 else 0.0
    if atr_pct_of_close >= 4.0:
        atr_pts = 1.5
    elif atr_pct_of_close >= 2.5:
        atr_pts = 1.0
    elif atr_pct_of_close >= 1.5:
        atr_pts = 0.5
    else:
        atr_pts = 0.2

    # 3. Stop tightness (max 2.0)
    if stop_distance_pct <= 3.0:
        stop_pts = 2.0
    elif stop_distance_pct <= 5.0:
        stop_pts = 1.5
    elif stop_distance_pct <= 7.0:
        stop_pts = 1.0
    else:
        stop_pts = 0.5

    # 4. Entry extension (max 1.5). Small extension means the trader can
    # still enter near the flip; large extension = move already ran.
    if extension_pct <= 1.0:
        ext_pts = 1.5
    elif extension_pct <= 2.5:
        ext_pts = 1.0
    elif extension_pct <= 5.0:
        ext_pts = 0.5
    else:
        ext_pts = 0.2

    # 5. Volume confirmation on the flip bar (max 2.0)
    if volume_ratio >= 2.0:
        vol_pts = 2.0
    elif volume_ratio >= 1.5:
        vol_pts = 1.5
    elif volume_ratio >= 1.2:
        vol_pts = 1.0
    elif volume_ratio >= 1.0:
        vol_pts = 0.5
    else:
        vol_pts = 0.0

    total = fresh_pts + atr_pts + stop_pts + ext_pts + vol_pts
    total = round(max(0.0, min(10.0, total)), 1)
    return {
        "total": total,
        "components": {
            "flip_freshness": round(fresh_pts, 2),
            "atr_regime": round(atr_pts, 2),
            "stop_tightness": round(stop_pts, 2),
            "entry_extension": round(ext_pts, 2),
            "volume_confirmation": round(vol_pts, 2),
        },
    }


def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    true_range = np.maximum.reduce(
        [
            high - low,
            np.abs(high - prev_close),
            np.abs(low - prev_close),
        ]
    )
    atr = np.zeros_like(close, dtype=float)
    if len(close) < period:
        return np.array([], dtype=float)
    atr[:period] = np.mean(true_range[:period])
    for idx in range(period, len(close)):
        atr[idx] = (atr[idx - 1] * (period - 1) + true_range[idx]) / period
    return atr


def _supertrend(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    atr: np.ndarray,
    multiplier: float,
) -> tuple[np.ndarray, np.ndarray]:
    hl2 = (high + low) / 2.0
    upper = hl2 + multiplier * atr
    lower = hl2 - multiplier * atr
    final_upper = upper.copy()
    final_lower = lower.copy()
    direction = np.ones(len(close), dtype=int)
    line = np.zeros(len(close), dtype=float)

    for idx in range(1, len(close)):
        if upper[idx] < final_upper[idx - 1] or close[idx - 1] > final_upper[idx - 1]:
            final_upper[idx] = upper[idx]
        else:
            final_upper[idx] = final_upper[idx - 1]

        if lower[idx] > final_lower[idx - 1] or close[idx - 1] < final_lower[idx - 1]:
            final_lower[idx] = lower[idx]
        else:
            final_lower[idx] = final_lower[idx - 1]

        if close[idx] > final_upper[idx - 1]:
            direction[idx] = 1
        elif close[idx] < final_lower[idx - 1]:
            direction[idx] = -1
        else:
            direction[idx] = direction[idx - 1]

        line[idx] = final_lower[idx] if direction[idx] == 1 else final_upper[idx]

    line[0] = final_lower[0]
    return line, direction
