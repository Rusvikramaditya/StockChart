"""Leak-safe walkforward backtest engine."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Iterable

import pandas as pd

from config import settings
from engine.data_loader import DataLoader
from engine.dedup import deduplicate_results
from engine.explainer import attach_explanation
from engine.scorer import score_pattern
from filters.market_regime import compute_market_regime
from filters.sector_rs import compute_sector_rs_cache
from patterns import get_detectors_for_universe
from patterns.base import PatternResult

from backtest.metrics import BacktestResult


@dataclass(frozen=True)
class BacktestConfig:
    universe: str = "nifty500"
    lookback_years: int = 3
    entry_mode: str = "next_open"
    max_hold_days: int = 30
    min_conviction: int = 50
    min_history_rows: int = 200
    limit_symbols: int | None = None
    max_days: int | None = None
    start_date: str | None = None
    end_date: str | None = None


def run_backtest(
    db_path=settings.DB_PATH,
    *,
    universe: str = "nifty500",
    lookback_years: int = 3,
    entry_mode: str = "next_open",
    max_hold_days: int = 30,
    min_conviction: int = 50,
    min_history_rows: int = 200,
    limit_symbols: int | None = None,
    max_days: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> BacktestResult:
    """Run a day-by-day walkforward backtest with no detector future leakage."""

    if entry_mode not in {"next_open", "pivot"}:
        raise ValueError("entry_mode must be 'next_open' or 'pivot'")
    config = BacktestConfig(
        universe=universe,
        lookback_years=lookback_years,
        entry_mode=entry_mode,
        max_hold_days=max_hold_days,
        min_conviction=min_conviction,
        min_history_rows=min_history_rows,
        limit_symbols=limit_symbols,
        max_days=max_days,
        start_date=start_date,
        end_date=end_date,
    )
    source_loader = DataLoader(db_path)
    try:
        symbols = source_loader.get_symbols_for_universe(universe)
        if limit_symbols is not None:
            symbols = symbols[: max(0, int(limit_symbols))]
        if not symbols:
            return BacktestResult([], universe=universe, config=asdict(config))

        loader = _CachedBacktestLoader(source_loader, symbols)
        end = end_date or _latest_date(loader, symbols)
        start = start_date or _start_from_years(end, lookback_years)
        trading_days = loader.get_trading_days(symbols, start_date=start, end_date=end)
        if max_days is not None:
            trading_days = trading_days[-max(0, int(max_days)) :]

        trades: list[dict] = []
        open_until: dict[tuple[str, str], str] = {}
        regime_cache: dict[str, dict] = {}
        sector_cache: dict[str, dict] = {}

        for current_date in trading_days:
            slice_loader = _SliceLoader(loader, current_date, symbols)
            market_regime = regime_cache.setdefault(
                current_date,
                compute_market_regime(slice_loader, symbols),
            )
            sector_rs_cache = sector_cache.setdefault(
                current_date,
                compute_sector_rs_cache(slice_loader, symbols),
            )

            for symbol in symbols:
                daily_df = slice_loader.get_stock_daily(symbol)
                if len(daily_df) < min_history_rows:
                    continue
                weekly_df = slice_loader.get_stock_weekly(symbol)
                daily_arrays = _frame_to_arrays(daily_df, date_column="date")
                weekly_arrays = _frame_to_arrays(weekly_df, date_column="week")
                scored_hits = _score_hits(
                    symbol,
                    daily_arrays,
                    weekly_arrays,
                    market_regime,
                    sector_rs_cache,
                    universe,
                )
                for scored in deduplicate_results(scored_hits):
                    if int(scored.get("score", 0)) < min_conviction:
                        continue
                    if str(scored.get("status", "")).upper() not in {"BREAKING OUT", "PIVOT READY"}:
                        continue
                    key = (symbol, str(scored.get("pattern", "")))
                    if open_until.get(key, "") >= current_date:
                        continue
                    outcome = track_trade_forward(
                        loader,
                        symbol,
                        current_date,
                        scored,
                        entry_mode=entry_mode,
                        max_hold_days=max_hold_days,
                    )
                    if outcome is None:
                        continue
                    breakdown = scored.get("breakdown") or {}
                    pattern_result: PatternResult | None = scored.get("pattern_result")
                    trade = {
                        "symbol": symbol,
                        "signal_date": current_date,
                        "pattern": scored["pattern"],
                        "score": scored["score"],
                        "tier": scored["tier"],
                        "pattern_quality_score": breakdown.get("pattern_quality_score"),
                        "pattern_confidence": breakdown.get("pattern_confidence"),
                        "pattern_timeframe": scored.get("timeframe") or getattr(pattern_result, "timeframe", None),
                        "bars_in_pattern": getattr(pattern_result, "bars_in_pattern", None),
                        "pattern_extra": dict(getattr(pattern_result, "extra", {}) or {}),
                        "stacked_count": int(scored.get("stacked_count", 1)),
                        "all_patterns": scored.get("all_patterns", [scored["pattern"]]),
                        "filters": scored.get("filters", {}),
                        **outcome,
                    }
                    trades.append(trade)
                    open_until[key] = str(trade["exit_date"])

        return BacktestResult(trades, universe=universe, config=asdict(config))
    finally:
        source_loader.close()


def track_trade_forward(
    loader: DataLoader,
    symbol: str,
    signal_date: str,
    scored: dict,
    *,
    entry_mode: str = "next_open",
    max_hold_days: int = 30,
) -> dict | None:
    """Track a detected trade forward until target, stop, or timeout."""

    future = loader.get_stock_daily_after(symbol, signal_date, limit=max_hold_days + 1)
    if future.empty:
        return None
    pattern: PatternResult | None = scored.get("pattern_result")
    entry = float(scored.get("pivot") or getattr(pattern, "pivot", 0.0))
    target = float(scored.get("target") or getattr(pattern, "target", 0.0))
    stop = float(scored.get("stop_loss") or getattr(pattern, "stop_loss", 0.0))
    if entry <= 0 or target <= 0 or stop <= 0:
        return None

    rows = future.reset_index(drop=True)
    if entry_mode == "next_open":
        entry = float(rows.at[0, "open"])
        entry_date = str(rows.at[0, "date"])
        outcome_rows = rows.iloc[:max_hold_days]
    else:
        entry_date = signal_date
        outcome_rows = rows.iloc[:max_hold_days]
    if target <= entry or stop >= entry:
        return None

    worst_drawdown = 0.0
    last_row = None
    for offset, row in enumerate(outcome_rows.itertuples(index=False), start=1):
        last_row = row
        low = float(row.low)
        high = float(row.high)
        worst_drawdown = min(worst_drawdown, (low - entry) / entry * 100.0)
        if low <= stop:
            return _outcome("LOSS", entry_date, row.date, entry, stop, offset, worst_drawdown)
        if high >= target:
            return _outcome("WIN", entry_date, row.date, entry, target, offset, worst_drawdown)

    if last_row is None:
        return None
    return _outcome(
        "TIMEOUT",
        entry_date,
        last_row.date,
        entry,
        float(last_row.close),
        int(len(outcome_rows)),
        worst_drawdown,
    )


def _score_hits(
    symbol: str,
    daily: dict,
    weekly: dict,
    market_regime: dict,
    sector_rs_cache: dict,
    universe: str = "nifty500",
) -> list[dict]:
    scored = []
    for detector in get_detectors_for_universe(universe):
        for hit in detector(daily, weekly):
            try:
                scored.append(
                    attach_explanation(
                        score_pattern(symbol, hit, daily, weekly, market_regime, sector_rs_cache)
                    )
                )
            except Exception:
                continue
    return scored


class _SliceLoader:
    def __init__(self, loader: DataLoader, as_of_date: str, symbols: list[str]):
        self.loader = loader
        self.as_of_date = as_of_date
        self.symbols = symbols
        self.daily_cache: dict[str, pd.DataFrame] = {}
        self.weekly_cache: dict[str, pd.DataFrame] = {}
        self.index_cache: dict[str, pd.DataFrame] = {}

    def get_stock_daily(self, symbol: str) -> pd.DataFrame:
        key = str(symbol).upper()
        if key not in self.daily_cache:
            self.daily_cache[key] = self.loader.get_daily_up_to(key, self.as_of_date)
        return self.daily_cache[key]

    def get_stock_weekly(self, symbol: str) -> pd.DataFrame:
        key = str(symbol).upper()
        if key not in self.weekly_cache:
            self.weekly_cache[key] = self.loader.get_weekly_up_to(key, self.as_of_date)
        return self.weekly_cache[key]

    def get_index(self, index_name: str) -> pd.DataFrame:
        key = str(index_name).upper()
        if key not in self.index_cache:
            self.index_cache[key] = self.loader.get_index_up_to(key, self.as_of_date)
        return self.index_cache[key]

    def get_all_active_symbols(self) -> list[str]:
        return list(self.symbols)


class _CachedBacktestLoader:
    """In-memory OHLCV cache for walkforward backtests.

    The backtest repeatedly slices the same symbols by date. Loading each
    symbol once keeps the no-future contract while avoiding hundreds of
    thousands of SQLite round-trips on broad universe runs.
    """

    def __init__(self, loader: DataLoader, symbols: list[str]):
        self.loader = loader
        self.symbols = [str(symbol).upper() for symbol in symbols]
        self.daily = {symbol: _normalise_cached_dates(loader.get_stock_daily(symbol), "date") for symbol in self.symbols}
        self.weekly = {symbol: _normalise_cached_dates(loader.get_stock_weekly(symbol), "week") for symbol in self.symbols}
        self.index_daily: dict[str, pd.DataFrame] = {}

    def get_trading_days(
        self,
        symbols: list[str],
        *,
        start_date: str | date | None = None,
        end_date: str | date | None = None,
    ) -> list[str]:
        start = str(start_date) if start_date is not None else None
        end = str(end_date) if end_date is not None else None
        days: set[str] = set()
        for symbol in symbols:
            frame = self.daily.get(str(symbol).upper())
            if frame is None or frame.empty:
                continue
            series = frame["date"]
            if start is not None:
                series = series[series >= start]
            if end is not None:
                series = series[series <= end]
            days.update(series.tolist())
        return sorted(days)

    def get_daily_up_to(self, symbol: str, as_of_date: str | date) -> pd.DataFrame:
        frame = self.daily.get(str(symbol).upper())
        if frame is None or frame.empty:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
        return frame[frame["date"] <= str(as_of_date)].copy()

    def get_weekly_up_to(self, symbol: str, as_of_date: str | date) -> pd.DataFrame:
        frame = self.weekly.get(str(symbol).upper())
        if frame is None or frame.empty:
            return pd.DataFrame(columns=["week", "open", "high", "low", "close", "volume"])
        return frame[frame["week"] <= str(as_of_date)].copy()

    def get_stock_daily_after(self, symbol: str, after_date: str | date, *, limit: int) -> pd.DataFrame:
        frame = self.daily.get(str(symbol).upper())
        if frame is None or frame.empty:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
        return frame[frame["date"] > str(after_date)].head(int(limit)).copy()

    def get_index_up_to(self, index_name: str, as_of_date: str | date) -> pd.DataFrame:
        key = str(index_name).upper()
        if key not in self.index_daily:
            self.index_daily[key] = _normalise_cached_dates(self.loader.get_index(key), "date")
        frame = self.index_daily[key]
        if frame.empty:
            return frame.copy()
        return frame[frame["date"] <= str(as_of_date)].copy()


def _normalise_cached_dates(frame: pd.DataFrame, column: str) -> pd.DataFrame:
    if frame is None or frame.empty:
        return frame
    cached = frame.copy()
    cached[column] = cached[column].astype(str)
    return cached


def _frame_to_arrays(frame: pd.DataFrame, *, date_column: str) -> dict:
    if frame is None or frame.empty:
        return {
            "date": [],
            "open": [],
            "high": [],
            "low": [],
            "close": [],
            "volume": [],
        }
    return {
        "date": frame[date_column].to_numpy(),
        "open": frame["open"].astype(float).to_numpy(),
        "high": frame["high"].astype(float).to_numpy(),
        "low": frame["low"].astype(float).to_numpy(),
        "close": frame["close"].astype(float).to_numpy(),
        "volume": frame["volume"].astype(float).to_numpy(),
    }


def _outcome(
    result: str,
    entry_date: str,
    exit_date: str,
    entry: float,
    exit_price: float,
    hold_days: int,
    max_drawdown_pct: float,
) -> dict:
    return {
        "entry_date": str(entry_date),
        "exit_date": str(exit_date),
        "entry_price": round(float(entry), 4),
        "exit_price": round(float(exit_price), 4),
        "result": result,
        "hold_days": int(hold_days),
        "return_pct": round((float(exit_price) - float(entry)) / float(entry) * 100.0, 4),
        "max_drawdown_pct": round(float(max_drawdown_pct), 4),
    }


def _latest_date(loader: DataLoader, symbols: Iterable[str]) -> str:
    days = loader.get_trading_days(list(symbols))
    if not days:
        raise ValueError("No OHLCV daily data available for selected universe")
    return days[-1]


def _start_from_years(end_date: str, lookback_years: int) -> str:
    end = pd.to_datetime(end_date).date()
    return (end - timedelta(days=365 * int(lookback_years))).isoformat()
