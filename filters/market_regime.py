"""Market regime filter computed once per scan."""

from __future__ import annotations

import pandas as pd

from config import settings
from patterns.utils import moving_average


def compute_market_regime(loader, symbols: list[str] | None = None) -> dict:
    nifty = loader.get_index("NIFTY 50")
    close = pd.to_numeric(nifty["close"], errors="coerce").dropna().to_numpy(dtype=float)
    if len(close) < settings.MARKET_REGIME["nifty_ma_long"]:
        return {
            "score": 0,
            "verdict": "UNKNOWN",
            "checks": {},
            "details": {"reason": "insufficient NIFTY 50 history"},
        }

    ma50 = moving_average(close, int(settings.MARKET_REGIME["nifty_ma_short"]))
    ma200 = moving_average(close, int(settings.MARKET_REGIME["nifty_ma_long"]))
    latest_close = float(close[-1])
    latest_ma50 = float(ma50[-1])
    latest_ma200 = float(ma200[-1])
    breadth = _advance_decline_ratio(loader, symbols or loader.get_all_active_symbols())
    checks = {
        "nifty_above_50ma": latest_close > latest_ma50,
        "nifty_above_200ma": latest_close > latest_ma200,
        "ma50_above_ma200": latest_ma50 > latest_ma200,
        "advance_decline_confirmed": (
            breadth is not None and breadth >= float(settings.MARKET_REGIME["advance_decline_threshold"])
        ),
    }
    score = sum(1 for value in checks.values() if value)
    if score <= int(settings.MARKET_REGIME["bear_score_threshold"]):
        verdict = "BEAR"
    elif score <= 2:
        verdict = "NEUTRAL"
    else:
        verdict = "CONFIRMED UPTREND"
    return {
        "score": score,
        "verdict": verdict,
        "checks": checks,
        "details": {
            "nifty_close": round(latest_close, 2),
            "nifty_ma50": round(latest_ma50, 2),
            "nifty_ma200": round(latest_ma200, 2),
            "advance_decline_ratio": None if breadth is None else round(breadth, 2),
        },
    }


def _advance_decline_ratio(loader, symbols: list[str]) -> float | None:
    advances = 0
    declines = 0
    for symbol in symbols:
        frame = loader.get_stock_daily(symbol)
        if len(frame) < 2:
            continue
        closes = pd.to_numeric(frame["close"], errors="coerce").dropna()
        if len(closes) < 2:
            continue
        change = float(closes.iloc[-1]) - float(closes.iloc[-2])
        if change > 0:
            advances += 1
        elif change < 0:
            declines += 1
    if advances == 0 and declines == 0:
        return None
    return advances / max(declines, 1)
