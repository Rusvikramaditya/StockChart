"""Sector momentum leaderboard.

Top-down sector-first selection layer. Computes per-sector multi-timeframe
returns (1M / 3M / 6M), relative strength vs Nifty 50, trend regime
(Stage 2 yes/no on the sector index itself), breadth (% of sector
constituents above 50 and 200 DMA), and a composite 0-100 momentum score.

The leaderboard surfaces on the dashboard as the FIRST visible panel so
that a swing trader can pick 2-3 leading sectors before looking at pattern
hits.

Composite score formula (weighted, sums to 1.0):
    25%  RS vs Nifty over 21 bars (catches early rotation)
    35%  RS vs Nifty over 63 bars (dominant swing horizon)
    20%  RS vs Nifty over 126 bars (durability)
    10%  % constituents above 50 DMA (short-term breadth)
     5%  % constituents above 200 DMA (long-term breadth)
     5%  Sector index in Stage 2 uptrend

RS normalization: clip((rs_pct + 10) / 20, 0, 1) maps the +/- 10% range
vs Nifty to 0..1 before weighting. Tiers: >=60 LEADING, <=40 LAGGING,
otherwise NEUTRAL.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

import pandas as pd

from config import settings


LOOKBACKS = {"1m": 21, "3m": 63, "6m": 126}

WEIGHTS = {
    "rs_1m": 0.25,
    "rs_3m": 0.35,
    "rs_6m": 0.20,
    "breadth_50dma": 0.10,
    "breadth_200dma": 0.05,
    "stage2": 0.05,
}

TIER_LEADING = 60.0
TIER_LAGGING = 40.0


def compute_leaderboard(
    loader,
    sector_map: dict | None = None,
    *,
    breadth_stats: dict[str, dict[str, float | int | None]] | None = None,
) -> dict:
    """Compute sector leaderboard rows, ranked by composite score descending.

    Args:
        loader: object exposing get_index(name) -> DataFrame,
            get_stock_daily(symbol) -> DataFrame, and
            get_recent_close_stats(symbols, ma_periods=...) -> dict.
        sector_map: optional pre-loaded {symbol: {sector_index: ...}} dict.
            If None, loaded from settings.SECTOR_MAP_JSON.
        breadth_stats: optional pre-loaded {symbol: {latest, ma50, ma200, bars}}.
            If None, computed once for the full constituent universe.

    Returns:
        {"weights": {...}, "tier_thresholds": {...}, "rows": [row, ...]}
    """
    if sector_map is None:
        sector_map = _load_sector_map()

    nifty = loader.get_index("NIFTY 50")
    nifty_returns = {key: _frame_return(nifty, bars) for key, bars in LOOKBACKS.items()}

    sector_to_symbols = _invert_sector_map(sector_map)
    if breadth_stats is None:
        all_symbols = sorted({s for syms in sector_to_symbols.values() for s in syms})
        breadth_stats = loader.get_recent_close_stats(all_symbols, ma_periods=(50, 200))
    rows = [
        _compute_row(loader, name, symbols, nifty_returns, breadth_stats)
        for name, symbols in sorted(sector_to_symbols.items())
    ]
    rows.sort(
        key=lambda r: r["composite_score"] if r["composite_score"] is not None else -1.0,
        reverse=True,
    )
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank

    return {
        "weights": dict(WEIGHTS),
        "tier_thresholds": {"leading": TIER_LEADING, "lagging": TIER_LAGGING},
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# Per-sector compute
# ---------------------------------------------------------------------------

def _compute_row(
    loader,
    sector_name: str,
    symbols: list[str],
    nifty_returns: dict,
    breadth_stats: dict[str, dict[str, float | int | None]],
) -> dict:
    sector_df = loader.get_index(sector_name)
    rets = {key: _frame_return(sector_df, bars) for key, bars in LOOKBACKS.items()}
    rs = {
        key: (None if rets[key] is None or nifty_returns[key] is None
              else round(rets[key] - nifty_returns[key], 2))
        for key in LOOKBACKS
    }
    stage2 = _sector_stage2(sector_df)
    breadth_50, breadth_200 = _breadth(symbols, breadth_stats)
    score = _composite_score(rs, breadth_50, breadth_200, stage2)
    return {
        "sector": sector_name,
        "ret_1m_pct": rets["1m"],
        "ret_3m_pct": rets["3m"],
        "ret_6m_pct": rets["6m"],
        "rs_1m_pct": rs["1m"],
        "rs_3m_pct": rs["3m"],
        "rs_6m_pct": rs["6m"],
        "stage2": stage2,
        "breadth_50dma_pct": breadth_50,
        "breadth_200dma_pct": breadth_200,
        "constituents": len(symbols),
        "composite_score": score,
        "tier": _tier(score),
        "rank": None,
    }


def _composite_score(
    rs: dict[str, float | None],
    breadth_50: float | None,
    breadth_200: float | None,
    stage2: bool,
) -> float | None:
    """Weighted blend of RS + breadth + Stage 2; renormalized over present components.

    Returns None when no RS component is available (sector data missing).
    """
    score = 0.0
    weight_sum = 0.0
    for key, lb in (("rs_1m", "1m"), ("rs_3m", "3m"), ("rs_6m", "6m")):
        if rs[lb] is not None:
            score += WEIGHTS[key] * _normalize_rs(rs[lb])
            weight_sum += WEIGHTS[key]
    if weight_sum <= 0:
        return None
    if breadth_50 is not None:
        score += WEIGHTS["breadth_50dma"] * (breadth_50 / 100.0)
        weight_sum += WEIGHTS["breadth_50dma"]
    if breadth_200 is not None:
        score += WEIGHTS["breadth_200dma"] * (breadth_200 / 100.0)
        weight_sum += WEIGHTS["breadth_200dma"]
    score += WEIGHTS["stage2"] * (1.0 if stage2 else 0.0)
    weight_sum += WEIGHTS["stage2"]
    return round(score / weight_sum * 100.0, 1)


def _normalize_rs(rs_pct: float) -> float:
    return max(0.0, min(1.0, (rs_pct + 10.0) / 20.0))


def _tier(score: float | None) -> str:
    if score is None:
        return "UNKNOWN"
    if score >= TIER_LEADING:
        return "LEADING"
    if score <= TIER_LAGGING:
        return "LAGGING"
    return "NEUTRAL"


# ---------------------------------------------------------------------------
# Return + trend + breadth helpers
# ---------------------------------------------------------------------------

def _frame_return(frame: pd.DataFrame | None, lookback_bars: int) -> float | None:
    if frame is None or len(frame) == 0:
        return None
    close = pd.to_numeric(frame["close"], errors="coerce").dropna()
    if len(close) <= lookback_bars:
        return None
    start = float(close.iloc[-1 - lookback_bars])
    if start <= 0:
        return None
    return round((float(close.iloc[-1]) / start - 1.0) * 100.0, 2)


def _sector_stage2(frame: pd.DataFrame | None) -> bool:
    """Sector index in Stage 2: above 50MA above 200MA with positive 50MA slope."""
    if frame is None or len(frame) == 0:
        return False
    close = pd.to_numeric(frame["close"], errors="coerce").dropna()
    if len(close) < 221:  # 200 for MA200 + 21 for slope
        return False
    ma50 = close.rolling(50).mean()
    ma200 = close.rolling(200).mean()
    latest = float(close.iloc[-1])
    latest_ma50 = float(ma50.iloc[-1])
    latest_ma200 = float(ma200.iloc[-1])
    slope = latest_ma50 - float(ma50.iloc[-21])
    return latest > latest_ma50 > latest_ma200 and slope > 0


def _breadth(
    symbols: Iterable[str],
    stats: dict[str, dict[str, float | int | None]],
) -> tuple[float | None, float | None]:
    above_50, above_200, total = 0, 0, 0
    for symbol in symbols:
        record = stats.get(str(symbol).upper())
        if not record:
            continue
        if (record.get("bars") or 0) < 200:
            continue
        latest = record.get("latest")
        ma50 = record.get("ma50")
        ma200 = record.get("ma200")
        if latest is None or ma50 is None or ma200 is None:
            continue
        total += 1
        if latest > ma50:
            above_50 += 1
        if latest > ma200:
            above_200 += 1
    if total == 0:
        return None, None
    return (
        round(above_50 / total * 100.0, 1),
        round(above_200 / total * 100.0, 1),
    )


def _load_sector_map() -> dict:
    if not settings.SECTOR_MAP_JSON.exists():
        return {}
    raw = json.loads(settings.SECTOR_MAP_JSON.read_text(encoding="utf-8"))
    return {str(k).upper(): v for k, v in raw.get("symbols", {}).items()}


def _invert_sector_map(symbol_map: dict) -> dict[str, list[str]]:
    """Group symbols by their sector_index, dropping the catch-all 'NIFTY 50' bucket."""
    out: dict[str, list[str]] = {}
    for symbol, info in symbol_map.items():
        sector = info.get("sector_index") if isinstance(info, dict) else None
        if not sector or sector == "NIFTY 50":
            continue
        out.setdefault(sector, []).append(symbol.upper())
    return out
