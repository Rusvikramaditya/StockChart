"""OHLCV-derived liquidity profile builder."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd

from config import settings
from engine import storage, universe


@dataclass(frozen=True)
class LiquidityRules:
    lookback_days: int = 50
    min_history_rows: int = 120
    stale_days: int = 10
    min_price: float = 10.0
    min_avg_volume_50d: float = 50_000.0
    min_avg_traded_value_50d: float = 10_000_000.0


@dataclass(frozen=True)
class LiquidityProfileResult:
    output_path: Path
    rows: int
    evaluated_rows: int
    excluded_symbols: int
    latest_date: str
    rules: LiquidityRules


DEFAULT_RULES = LiquidityRules()
LIQUIDITY_COLUMNS = [
    "latest_date",
    "latest_close",
    "history_rows",
    "avg_volume_50d",
    "avg_traded_value_50d",
    "liquidity_pass",
    "risk_tier",
    "liquidity_reason",
]
PROFILE_COLUMNS = [*universe.OUTPUT_COLUMNS, *LIQUIDITY_COLUMNS]


def compute_liquidity_metrics(
    conn: sqlite3.Connection,
    *,
    rules: LiquidityRules = DEFAULT_RULES,
) -> pd.DataFrame:
    """Return one OHLCV-derived liquidity row for every symbol in daily data."""

    if rules.lookback_days <= 0:
        raise ValueError("lookback_days must be positive")

    return storage.query_frame(
        conn,
        """
        WITH ranked AS (
            SELECT
                symbol,
                date,
                close,
                volume,
                ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY date DESC) AS rn,
                COUNT(*) OVER (PARTITION BY symbol) AS history_rows
            FROM ohlcv_daily
            WHERE close > 0
        )
        SELECT
            symbol,
            MAX(CASE WHEN rn = 1 THEN date END) AS latest_date,
            MAX(CASE WHEN rn = 1 THEN close END) AS latest_close,
            MAX(history_rows) AS history_rows,
            AVG(CASE WHEN rn <= ? THEN volume END) AS avg_volume_50d,
            AVG(CASE WHEN rn <= ? THEN close * volume END) AS avg_traded_value_50d
        FROM ranked
        GROUP BY symbol
        """,
        [rules.lookback_days, rules.lookback_days],
    )


def build_liquidity_profile_frame(
    conn: sqlite3.Connection,
    base_profile: pd.DataFrame,
    *,
    exclude_symbols: Iterable[str] = (),
    rules: LiquidityRules = DEFAULT_RULES,
) -> pd.DataFrame:
    """Attach liquidity metrics and pass/fail reasons to a broad profile."""

    base = universe.normalise_profile_frame(
        base_profile,
        source="base_profile",
        required_columns=universe.OUTPUT_COLUMNS,
    )
    base = base[base["status"].astype(str).str.upper().eq("ACTIVE")].copy()
    metrics = compute_liquidity_metrics(conn, rules=rules)
    if metrics.empty:
        metrics = pd.DataFrame(columns=["symbol", *LIQUIDITY_COLUMNS[:5]])

    metrics["symbol"] = metrics["symbol"].astype(str).str.upper()
    frame = base.merge(metrics, how="left", on="symbol")
    excluded = {str(symbol).strip().upper() for symbol in exclude_symbols if str(symbol).strip()}
    market_latest = _market_latest(frame)

    for column in ["latest_close", "history_rows", "avg_volume_50d", "avg_traded_value_50d"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0)
    frame["history_rows"] = frame["history_rows"].astype(int)
    frame["latest_date"] = frame["latest_date"].fillna("").astype(str)

    reasons = []
    passes = []
    tiers = []
    for row in frame.itertuples(index=False):
        row_reasons = _failure_reasons(row, market_latest=market_latest, excluded=excluded, rules=rules)
        passes.append(not row_reasons)
        reasons.append("PASS" if not row_reasons else "|".join(row_reasons))
        tiers.append(_risk_tier(row, passed=not row_reasons, rules=rules))

    frame["latest_close"] = frame["latest_close"].round(2)
    frame["avg_volume_50d"] = frame["avg_volume_50d"].round(0).astype(int)
    frame["avg_traded_value_50d"] = frame["avg_traded_value_50d"].round(0).astype(int)
    frame["liquidity_pass"] = passes
    frame["risk_tier"] = tiers
    frame["liquidity_reason"] = reasons
    return frame[PROFILE_COLUMNS].sort_values("symbol").reset_index(drop=True)


def build_small_mid_liquid_profile(
    conn: sqlite3.Connection,
    *,
    output_path: Path | None = None,
    broad_path: Path | None = None,
    nifty500_path: Path | None = None,
    rules: LiquidityRules = DEFAULT_RULES,
) -> LiquidityProfileResult:
    """Generate ``config/small_mid_liquid.csv`` from broad NSE daily OHLCV."""

    output_path = Path(output_path) if output_path is not None else settings.SMALL_MID_LIQUID_CSV
    broad = universe.load_all_nse_equity(path=broad_path)
    nifty500 = universe.load_universe_profile(
        "nifty500",
        profile_path=nifty500_path,
        broad_path=broad_path,
        allow_empty=True,
    )
    excluded = set(nifty500["symbol"].astype(str).str.upper())
    evaluated = build_liquidity_profile_frame(conn, broad, exclude_symbols=excluded, rules=rules)
    selected = evaluated[evaluated["liquidity_pass"]].copy()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    selected.to_csv(output_path, index=False)
    latest_date = str(evaluated["latest_date"].max()) if not evaluated.empty else ""
    return LiquidityProfileResult(
        output_path=output_path,
        rows=len(selected),
        evaluated_rows=len(evaluated),
        excluded_symbols=len(excluded),
        latest_date=latest_date,
        rules=rules,
    )


def _market_latest(frame: pd.DataFrame) -> pd.Timestamp | None:
    parsed = pd.to_datetime(frame["latest_date"], errors="coerce")
    if parsed.dropna().empty:
        return None
    return parsed.max()


def _failure_reasons(
    row: object,
    *,
    market_latest: pd.Timestamp | None,
    excluded: set[str],
    rules: LiquidityRules,
) -> list[str]:
    reasons: list[str] = []
    if row.symbol in excluded:
        reasons.append("excluded_nifty500")
    if not row.latest_date:
        reasons.append("no_daily_data")
    if int(row.history_rows) < rules.min_history_rows:
        reasons.append("insufficient_history")
    if market_latest is not None:
        latest = pd.to_datetime(row.latest_date, errors="coerce")
        if pd.isna(latest) or latest < market_latest - pd.Timedelta(days=rules.stale_days):
            reasons.append("stale_data")
    if float(row.latest_close) < rules.min_price:
        reasons.append("price_below_min")
    if float(row.avg_volume_50d) < rules.min_avg_volume_50d:
        reasons.append("avg_volume_below_min")
    if float(row.avg_traded_value_50d) < rules.min_avg_traded_value_50d:
        reasons.append("avg_traded_value_below_min")
    return reasons


def _risk_tier(row: object, *, passed: bool, rules: LiquidityRules) -> str:
    if not passed:
        return "AVOID"
    traded_value = float(row.avg_traded_value_50d)
    volume = float(row.avg_volume_50d)
    if traded_value >= 500_000_000 and volume >= 200_000:
        return "LOW"
    if traded_value >= 100_000_000 and volume >= 100_000:
        return "MEDIUM"
    if traded_value >= rules.min_avg_traded_value_50d and volume >= rules.min_avg_volume_50d:
        return "HIGH"
    return "AVOID"
