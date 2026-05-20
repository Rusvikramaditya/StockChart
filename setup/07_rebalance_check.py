"""Refresh broad NSE universe and detect Nifty 500 profile changes."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import settings
from engine import dhan_client, sector_map, storage, symbols, universe
from filters import liquidity


NIFTY_PROFILE_COLUMNS = [
    "symbol",
    "company_name",
    "industry",
    "security_id",
    "exchange_segment",
    "instrument",
    "instrument_type",
    "series",
    "lot_size",
    "listing_date",
    "status",
]


@dataclass(frozen=True)
class RebalanceResult:
    broad_rows: int
    nifty_rows: int
    added: list[str]
    removed: list[str]
    history_rows: int
    small_mid_liquid_rows: int | None
    watchlist_rows: int | None
    output_paths: dict[str, str]


def check_rebalance(
    *,
    apply_history: bool = True,
    force_master_refresh: bool = True,
    rebuild_liquidity: bool = True,
    refresh_watchlist: bool = True,
    fresh_nifty: pd.DataFrame | None = None,
    master_path: Path | None = None,
    db_path: Path | None = None,
    all_nse_path: Path | None = None,
    nifty_csv_path: Path | None = None,
    nifty_dhan_path: Path | None = None,
    small_mid_path: Path | None = None,
    watchlist_path: Path | None = None,
    sector_map_path: Path | None = None,
) -> dict[str, Any]:
    """Refresh broad universe, Nifty 500 profile, sector map, and derived profiles."""

    storage.ensure_directories()
    all_nse_path = Path(all_nse_path) if all_nse_path is not None else settings.ALL_NSE_EQUITY_CSV
    nifty_csv_path = Path(nifty_csv_path) if nifty_csv_path is not None else settings.NIFTY500_CSV
    nifty_dhan_path = Path(nifty_dhan_path) if nifty_dhan_path is not None else settings.NIFTY500_DHAN_CSV
    small_mid_path = Path(small_mid_path) if small_mid_path is not None else settings.SMALL_MID_LIQUID_CSV
    watchlist_path = Path(watchlist_path) if watchlist_path is not None else settings.WATCHLIST_CSV
    sector_map_path = Path(sector_map_path) if sector_map_path is not None else settings.SECTOR_MAP_JSON
    db_path = Path(db_path) if db_path is not None else settings.DB_PATH

    broad_result = universe.build_all_nse_equity_universe(
        master_path=master_path,
        output_path=all_nse_path,
        force_master_refresh=force_master_refresh,
    )
    broad = universe.load_all_nse_equity(path=all_nse_path)
    old = _load_old_nifty_profile(nifty_dhan_path)
    old_symbols = set(old.loc[old["status"].eq("ACTIVE"), "symbol"])

    fresh_nifty = _normalize_nifty_csv(
        symbols.download_nifty500_csv() if fresh_nifty is None else fresh_nifty
    )
    fresh_profile = _build_nifty_profile(fresh_nifty, broad)
    new_symbols = set(fresh_profile["symbol"])
    added = sorted(new_symbols - old_symbols)
    removed = sorted(old_symbols - new_symbols)

    combined = _combine_active_and_removed(fresh_profile, old, removed)
    nifty_csv_path.parent.mkdir(parents=True, exist_ok=True)
    nifty_dhan_path.parent.mkdir(parents=True, exist_ok=True)
    fresh_nifty.to_csv(nifty_csv_path, index=False)
    combined.to_csv(nifty_dhan_path, index=False)
    _write_sector_map(combined, sector_map_path)

    history_rows = 0
    if apply_history and added:
        conn = storage.connect(db_path)
        storage.ensure_schema(conn)
        try:
            for row in combined[combined["symbol"].isin(added)].itertuples(index=False):
                try:
                    written = _fetch_added_symbol(conn, row)
                    history_rows += written
                    print(f"Added {row.symbol}: {written} daily rows")
                except Exception as exc:
                    print(f"Added {row.symbol}: history fetch failed: {exc}")
        finally:
            conn.close()

    small_mid_rows: int | None = None
    if rebuild_liquidity:
        conn = storage.connect(db_path)
        storage.ensure_schema(conn)
        try:
            small_mid = liquidity.build_small_mid_liquid_profile(
                conn,
                output_path=small_mid_path,
                broad_path=all_nse_path,
                nifty500_path=nifty_dhan_path,
            )
            small_mid_rows = small_mid.rows
        finally:
            conn.close()

    watchlist_rows: int | None = None
    if refresh_watchlist:
        watchlist = universe.build_watchlist_profile(
            output_path=watchlist_path,
            broad_path=all_nse_path,
        )
        watchlist_rows = watchlist.rows

    result = RebalanceResult(
        broad_rows=broad_result.rows,
        nifty_rows=int(combined["status"].eq("ACTIVE").sum()),
        added=added,
        removed=removed,
        history_rows=history_rows,
        small_mid_liquid_rows=small_mid_rows,
        watchlist_rows=watchlist_rows,
        output_paths={
            "all_nse_equity": str(all_nse_path),
            "nifty500": str(nifty_csv_path),
            "nifty500_dhan": str(nifty_dhan_path),
            "sector_map": str(sector_map_path),
            "small_mid_liquid": str(small_mid_path),
            "watchlist": str(watchlist_path),
        },
    )

    print(f"Broad NSE equity rows: {result.broad_rows}")
    print(f"Nifty 500 active rows: {result.nifty_rows}")
    print(f"{len(added)} added, {len(removed)} removed.")
    if removed:
        print("Removed marked INACTIVE: " + ", ".join(removed[:30]))
    if small_mid_rows is not None:
        print(f"Small/mid liquid rows: {small_mid_rows}")
    if watchlist_rows is not None:
        print(f"Watchlist rows: {watchlist_rows}")
    print("PASS: rebalance check complete")
    return asdict(result)


def _fetch_added_symbol(conn, row: object) -> int:
    symbol = str(row.symbol).upper()
    security_id = str(row.security_id).strip()
    if not security_id:
        return 0
    from_date = (date.today() - timedelta(days=365 * settings.HISTORY_YEARS)).isoformat()
    to_date = date.today().isoformat()
    daily = dhan_client.fetch_historical_sync(
        security_id,
        str(getattr(row, "exchange_segment", "NSE_EQ") or "NSE_EQ"),
        str(getattr(row, "instrument", "EQUITY") or "EQUITY"),
        from_date,
        to_date,
    )
    daily_rows = storage.upsert_daily_rows(conn, symbol, security_id, daily)
    weekly = _weekly_from_daily(daily)
    storage.upsert_weekly_rows(conn, symbol, weekly)
    return daily_rows


def _weekly_from_daily(daily: pd.DataFrame) -> pd.DataFrame:
    if daily.empty:
        return pd.DataFrame(columns=["week", "open", "high", "low", "close", "volume"])
    frame = daily.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame.dropna(subset=["date"]).sort_values("date")
    weekly = (
        frame.set_index("date")
        .resample("W-FRI")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna(subset=["open", "high", "low", "close"])
        .reset_index()
    )
    weekly["week"] = weekly["date"].dt.strftime("%Y-%m-%d")
    return weekly[["week", "open", "high", "low", "close", "volume"]]


def _load_old_nifty_profile(path: Path) -> pd.DataFrame:
    if path.exists():
        raw = pd.read_csv(path, dtype=str, keep_default_na=False)
    else:
        raw = pd.DataFrame(columns=NIFTY_PROFILE_COLUMNS)
    frame = raw.copy()
    frame.columns = [str(column).strip().lower() for column in frame.columns]
    for column in NIFTY_PROFILE_COLUMNS:
        if column not in frame.columns:
            frame[column] = ""
    frame["symbol"] = frame["symbol"].astype(str).str.strip().str.upper()
    frame["status"] = frame["status"].astype(str).str.strip().str.upper().replace("", "ACTIVE")
    return frame[NIFTY_PROFILE_COLUMNS]


def _normalize_nifty_csv(frame: pd.DataFrame) -> pd.DataFrame:
    columns = {str(column).strip().upper(): column for column in frame.columns}
    symbol_col = columns.get("SYMBOL")
    if symbol_col is None:
        raise RuntimeError(f"Nifty 500 CSV missing Symbol column: {list(frame.columns)}")
    company_col = columns.get("COMPANY NAME") or columns.get("COMPANY")
    industry_col = columns.get("INDUSTRY") or columns.get("SECTOR")

    out = pd.DataFrame()
    out["Symbol"] = frame[symbol_col].astype(str).str.strip().str.upper()
    out["Company Name"] = frame[company_col].astype(str).str.strip() if company_col else out["Symbol"]
    out["Industry"] = frame[industry_col].astype(str).str.strip() if industry_col else ""
    out = out[out["Symbol"].ne("") & out["Symbol"].ne("NAN")]
    return out.drop_duplicates(subset=["Symbol"]).sort_values("Symbol").reset_index(drop=True)


def _build_nifty_profile(nifty_df: pd.DataFrame, broad: pd.DataFrame) -> pd.DataFrame:
    resolved = universe.resolve_symbols_from_broad(
        nifty_df["Symbol"].tolist(),
        broad_df=broad,
        source="nifty500_rebalance",
    )
    meta = nifty_df.set_index("Symbol")
    profile = resolved.copy()
    profile["company_name"] = [
        str(meta.at[symbol, "Company Name"] or company)
        for symbol, company in profile[["symbol", "company_name"]].itertuples(index=False)
    ]
    profile.insert(2, "industry", [str(meta.at[symbol, "Industry"]) for symbol in profile["symbol"]])
    return profile[NIFTY_PROFILE_COLUMNS].sort_values("symbol").reset_index(drop=True)


def _combine_active_and_removed(active: pd.DataFrame, old: pd.DataFrame, removed: list[str]) -> pd.DataFrame:
    frames = [active.copy()]
    if removed:
        removed_rows = old[old["symbol"].isin(removed)].copy()
        removed_rows["status"] = "INACTIVE"
        frames.append(removed_rows[NIFTY_PROFILE_COLUMNS])
    combined = pd.concat(frames, ignore_index=True)
    combined = combined.drop_duplicates(subset=["symbol"], keep="first")
    return combined.sort_values(["status", "symbol"]).reset_index(drop=True)[NIFTY_PROFILE_COLUMNS]


def _write_sector_map(symbols_df: pd.DataFrame, path: Path) -> dict:
    payload = sector_map.build_sector_map(symbols_df)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-history", action="store_true")
    parser.add_argument("--skip-liquidity", action="store_true")
    parser.add_argument("--skip-watchlist", action="store_true")
    parser.add_argument("--use-cached-master", action="store_true")
    args = parser.parse_args()
    check_rebalance(
        apply_history=not args.skip_history,
        force_master_refresh=not args.use_cached_master,
        rebuild_liquidity=not args.skip_liquidity,
        refresh_watchlist=not args.skip_watchlist,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
