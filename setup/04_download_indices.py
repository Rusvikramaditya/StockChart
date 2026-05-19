"""Download Nifty 50 and sector indices through Dhan IDX_I."""

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
from engine import dhan_client, storage


def _fetch_yfinance(index_name: str, from_date: str, to_date: str) -> pd.DataFrame:
    yf_symbol = settings.YFINANCE_INDEX_SYMBOLS.get(index_name)
    if not yf_symbol:
        return pd.DataFrame()
    import yfinance as yf

    df = yf.download(yf_symbol, start=from_date, end=to_date, progress=False, auto_adjust=False)
    if df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.reset_index()
    return pd.DataFrame(
        {
            "date": pd.to_datetime(df["Date"]),
            "open": df["Open"],
            "high": df["High"],
            "low": df["Low"],
            "close": df["Close"],
            "volume": df["Volume"] if "Volume" in df else 0,
        }
    )


def _fetch_index(index_name: str, from_date: str, to_date: str, index_master: dict) -> tuple[pd.DataFrame, str]:
    security_id = dhan_client.resolve_index_security_id(index_name, index_master)
    if not security_id:
        fallback = _fetch_yfinance(index_name, from_date, to_date)
        return fallback, "yfinance_missing_in_dhan"

    frame = dhan_client.fetch_historical_sync(
        security_id=security_id,
        exchange_segment="IDX_I",
        instrument="INDEX",
        from_date=from_date,
        to_date=to_date,
    )
    return frame, "dhan_idx_i"


def run(from_date: str | None = None, to_date: str | None = None) -> dict:
    storage.ensure_directories()
    conn = storage.connect()
    storage.ensure_schema(conn)
    to_date = to_date or date.today().isoformat()
    from_date = from_date or (date.today() - timedelta(days=365 * settings.INDEX_HISTORY_YEARS)).isoformat()
    index_names = ["NIFTY 50", *settings.SECTOR_INDICES]
    index_master = dhan_client.index_master()

    successes = 0
    failures: list[str] = []
    source_counts: dict[str, int] = {}
    for index_name in index_names:
        try:
            frame, source = _fetch_index(index_name, from_date, to_date, index_master)
            if frame.empty:
                failures.append(f"{index_name}: empty response")
                continue
            rows = storage.upsert_index_rows(conn, index_name, frame)
            source_counts[source] = source_counts.get(source, 0) + 1
            successes += 1
            print(f"{index_name}: {rows} rows ({source})")
        except Exception as exc:
            failures.append(f"{index_name}: {exc}")

    print(f"Index downloads: {successes}/{len(index_names)}")
    print(f"Sources: {source_counts}")
    if failures:
        print("Failures:")
        for item in failures:
            print(f"  - {item}")
    if successes < 10:
        raise SystemExit("FAIL: fewer than 10 index series downloaded")
    print("PASS: index_daily populated")
    return {"successes": successes, "requested": len(index_names), "sources": source_counts}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-date", default=None)
    parser.add_argument("--to-date", default=None)
    args = parser.parse_args()
    run(args.from_date, args.to_date)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
