"""Generate weekly candles from ohlcv_daily."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import storage


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


def generate_weekly_incremental(conn: sqlite3.Connection, full: bool = False) -> dict[str, int]:
    storage.ensure_schema(conn)
    if full:
        conn.execute("DELETE FROM ohlcv_weekly")
        conn.commit()

    symbols_df = storage.query_frame(
        conn,
        "SELECT DISTINCT symbol FROM ohlcv_daily ORDER BY symbol",
    )
    total_rows = 0
    processed = 0
    for symbol in symbols_df["symbol"].tolist():
        last_week = None
        if not full:
            row = conn.execute(
                "SELECT MAX(week) FROM ohlcv_weekly WHERE symbol = ?",
                (symbol,),
            ).fetchone()
            last_week = row[0] if row else None

        if last_week:
            daily = storage.query_frame(
                conn,
                """
                SELECT date, open, high, low, close, volume
                FROM ohlcv_daily
                WHERE symbol = ? AND date > ?
                ORDER BY date
                """,
                (symbol, last_week),
            )
        else:
            daily = storage.query_frame(
                conn,
                """
                SELECT date, open, high, low, close, volume
                FROM ohlcv_daily
                WHERE symbol = ?
                ORDER BY date
                """,
                (symbol,),
            )

        weekly = _weekly_from_daily(daily)
        if weekly.empty:
            continue
        total_rows += storage.upsert_weekly_rows(conn, symbol, weekly)
        processed += 1

    return {"symbols_processed": processed, "weekly_rows_written": total_rows}


def run(full: bool = False) -> dict[str, int]:
    conn = storage.connect()
    stats = generate_weekly_incremental(conn, full=full)
    print(f"Symbols processed: {stats['symbols_processed']}")
    print(f"Weekly rows written: {stats['weekly_rows_written']}")
    if stats["symbols_processed"] == 0:
        existing = conn.execute("SELECT COUNT(*) FROM ohlcv_weekly").fetchone()[0]
        if existing == 0:
            raise SystemExit("FAIL: no daily data available to aggregate")
        print(f"No new daily rows to aggregate; existing weekly rows: {existing}")
    print("PASS: ohlcv_weekly ready")
    return stats


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()
    run(full=args.full)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
