"""Quarterly Nifty 500 rebalance check."""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import settings
from engine import dhan_client, sector_map, storage, symbols


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


def _fetch_added_symbol(conn, row: pd.Series) -> int:
    symbol = str(row["symbol"]).upper()
    security_id = str(row["security_id"]).strip()
    if not security_id:
        return 0
    from_date = (date.today() - timedelta(days=365 * settings.HISTORY_YEARS)).isoformat()
    to_date = date.today().isoformat()
    daily = dhan_client.fetch_historical_sync(
        security_id,
        "NSE_EQ",
        "EQUITY",
        from_date,
        to_date,
    )
    daily_rows = storage.upsert_daily_rows(conn, symbol, security_id, daily)
    weekly = _weekly_from_daily(daily)
    storage.upsert_weekly_rows(conn, symbol, weekly)
    return daily_rows


def check_rebalance(apply_history: bool = True) -> dict:
    storage.ensure_directories()
    old = (
        pd.read_csv(settings.NIFTY500_DHAN_CSV, dtype={"security_id": str})
        if settings.NIFTY500_DHAN_CSV.exists()
        else pd.DataFrame(columns=["symbol", "security_id", "status"])
    )
    if "status" not in old.columns:
        old["status"] = "ACTIVE"
    old_active = old[old["status"].astype(str).str.upper().eq("ACTIVE")].copy()
    old_symbols = set(old_active["symbol"].astype(str).str.upper())

    fresh_nifty = symbols.download_nifty500_csv()
    fresh_active = symbols.build_dhan_symbol_master(fresh_nifty, force_master_refresh=True)
    new_symbols = set(fresh_active["symbol"].astype(str).str.upper())

    added = sorted(new_symbols - old_symbols)
    removed = sorted(old_symbols - new_symbols)
    if removed:
        removed_rows = old[old["symbol"].astype(str).str.upper().isin(removed)].copy()
        removed_rows["status"] = "INACTIVE"
        combined = pd.concat([fresh_active, removed_rows], ignore_index=True)
    else:
        combined = fresh_active
    combined = combined.drop_duplicates(subset=["symbol"], keep="first").sort_values("symbol")
    combined.to_csv(settings.NIFTY500_DHAN_CSV, index=False)
    fresh_nifty.to_csv(settings.NIFTY500_CSV, index=False)
    sector_map.write_sector_map(combined)

    history_rows = 0
    if apply_history and added:
        conn = storage.connect()
        storage.ensure_schema(conn)
        added_rows = combined[combined["symbol"].astype(str).str.upper().isin(added)]
        for _, row in added_rows.iterrows():
            try:
                written = _fetch_added_symbol(conn, row)
                history_rows += written
                print(f"Added {row['symbol']}: {written} daily rows")
            except Exception as exc:
                print(f"Added {row['symbol']}: history fetch failed: {exc}")

    print(f"{len(added)} added, {len(removed)} removed.")
    if removed:
        print("Removed marked INACTIVE: " + ", ".join(removed[:30]))
    print("PASS: rebalance check complete")
    return {"added": added, "removed": removed, "history_rows": history_rows}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-history", action="store_true")
    args = parser.parse_args()
    check_rebalance(apply_history=not args.skip_history)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
