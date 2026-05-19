"""Download 5-year daily OHLCV for active Nifty 500 stocks from Dhan."""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import aiohttp
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import settings
from engine import dhan_client, storage, symbols


async def fetch_one(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    row: dict[str, Any],
    from_date: str,
    to_date: str,
) -> tuple[str, str, pd.DataFrame | None, str | None]:
    symbol = str(row["symbol"]).upper()
    security_id = str(row["security_id"]).strip()
    if not security_id:
        return symbol, security_id, None, "missing security_id"

    payload = dhan_client.historical_payload(
        security_id=security_id,
        exchange_segment="NSE_EQ",
        instrument="EQUITY",
        from_date=from_date,
        to_date=to_date,
    )
    url = f"{settings.DHAN_BASE_URL}/v2/charts/historical"
    async with sem:
        for attempt in range(settings.FETCH_RETRY_COUNT):
            try:
                async with session.post(url, json=payload) as response:
                    text = await response.text()
                    if response.status == 200:
                        data = await response.json(content_type=None)
                        frame = dhan_client.parse_historical_response(data)
                        if frame.empty:
                            return symbol, security_id, None, "empty Dhan response"
                        return symbol, security_id, frame, None
                    if response.status == 429:
                        await asyncio.sleep(2**attempt)
                        continue
                    return symbol, security_id, None, f"HTTP {response.status}: {text[:160]}"
            except Exception as exc:
                if attempt == settings.FETCH_RETRY_COUNT - 1:
                    return symbol, security_id, None, str(exc)
                await asyncio.sleep(2**attempt)
    return symbol, security_id, None, "retries exhausted"


async def fetch_all(active: pd.DataFrame, from_date: str, to_date: str) -> list:
    sem = asyncio.Semaphore(settings.MAX_CONCURRENT_FETCHES)
    timeout = aiohttp.ClientTimeout(total=90)
    headers = dhan_client.dhan_headers()
    async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
        tasks = [
            fetch_one(session, sem, row, from_date, to_date)
            for row in active.to_dict("records")
        ]
        return await asyncio.gather(*tasks)


def run(limit: int | None = None, from_date: str | None = None, to_date: str | None = None) -> dict:
    if not settings.NIFTY500_DHAN_CSV.exists():
        raise SystemExit("FAIL: run setup/01_download_symbols.py first")

    storage.ensure_directories()
    conn = storage.connect()
    storage.ensure_schema(conn)

    active = symbols.load_active_symbols()
    active = active[active["security_id"].astype(str).str.strip().ne("")]
    if limit:
        active = active.head(limit)
    if active.empty:
        raise SystemExit("FAIL: no active symbols with Dhan security_id")

    to_date = to_date or date.today().isoformat()
    from_date = from_date or (date.today() - timedelta(days=365 * settings.HISTORY_YEARS)).isoformat()
    print(f"Downloading {len(active)} stocks from Dhan NSE_EQ: {from_date} to {to_date}")

    results = asyncio.run(fetch_all(active, from_date, to_date))
    success = 0
    rows_written = 0
    failures: list[str] = []
    for symbol, security_id, frame, error in results:
        if error or frame is None or frame.empty:
            failures.append(f"{symbol}: {error}")
            continue
        rows_written += storage.upsert_daily_rows(conn, symbol, security_id, frame)
        success += 1

    print(f"Dhan OHLCV success: {success}/{len(active)}")
    print(f"Rows written: {rows_written}")
    if failures:
        print("Failures:")
        for item in failures[:30]:
            print(f"  - {item}")
        if len(failures) > 30:
            print(f"  ... {len(failures) - 30} more")

    if success < max(1, int(len(active) * 0.50)):
        raise SystemExit("FAIL: fewer than 50% of requested stock downloads succeeded")
    print("PASS: ohlcv_daily populated")
    return {"success": success, "requested": len(active), "rows_written": rows_written}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--from-date", default=None)
    parser.add_argument("--to-date", default=None)
    args = parser.parse_args()
    run(args.limit, args.from_date, args.to_date)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

