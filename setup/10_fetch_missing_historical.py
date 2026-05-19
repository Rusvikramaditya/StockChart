"""Fetch historical OHLCV only for symbols missing from the local SQLite DB.

Default behavior is a dry run. Pass --execute to call Dhan and write rows.
This script is intentionally separate from the original Phase 1 downloader so
the broad NSE expansion can be planned and resumed without re-fetching symbols
that already have data.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import aiohttp
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import settings
from engine import dhan_client, storage, universe


@dataclass(frozen=True)
class Coverage:
    rows: int = 0
    earliest: str = ""
    latest: str = ""


@dataclass(frozen=True)
class PlanRow:
    symbol: str
    security_id: str
    exchange_segment: str
    instrument: str
    existing_rows: int
    existing_earliest: str
    existing_latest: str
    reason: str


@dataclass(frozen=True)
class FetchResult:
    symbol: str
    security_id: str
    status: str
    rows_written: int
    existing_rows: int
    existing_latest: str
    reason: str
    error: str = ""


def coverage_for_symbols(conn, symbols: list[str]) -> dict[str, Coverage]:
    if not symbols:
        return {}
    placeholders = ",".join("?" for _ in symbols)
    frame = storage.query_frame(
        conn,
        f"""
        SELECT symbol,
               COUNT(*) AS rows,
               MIN(date) AS earliest,
               MAX(date) AS latest
        FROM ohlcv_daily
        WHERE close > 0 AND symbol IN ({placeholders})
        GROUP BY symbol
        """,
        symbols,
    )
    return {
        str(row.symbol).upper(): Coverage(
            rows=int(row.rows or 0),
            earliest=str(row.earliest or ""),
            latest=str(row.latest or ""),
        )
        for row in frame.itertuples(index=False)
    }


def build_fetch_plan(
    profile: pd.DataFrame,
    coverage: dict[str, Coverage],
    *,
    min_rows: int,
    to_date: str,
    require_latest_date: bool,
) -> tuple[list[PlanRow], list[FetchResult]]:
    plan: list[PlanRow] = []
    skipped: list[FetchResult] = []
    for row in profile.itertuples(index=False):
        symbol = str(row.symbol).upper()
        security_id = str(row.security_id).strip()
        existing = coverage.get(symbol, Coverage())
        if not security_id:
            skipped.append(
                FetchResult(
                    symbol=symbol,
                    security_id="",
                    status="skipped",
                    rows_written=0,
                    existing_rows=existing.rows,
                    existing_latest=existing.latest,
                    reason="missing_security_id",
                )
            )
            continue

        has_enough_rows = existing.rows >= min_rows
        latest_ok = not require_latest_date or (existing.latest and existing.latest >= to_date)
        if has_enough_rows and latest_ok:
            skipped.append(
                FetchResult(
                    symbol=symbol,
                    security_id=security_id,
                    status="skipped",
                    rows_written=0,
                    existing_rows=existing.rows,
                    existing_latest=existing.latest,
                    reason="already_downloaded",
                )
            )
            continue

        if existing.rows <= 0:
            reason = "missing"
        elif existing.rows < min_rows:
            reason = f"under_min_rows_{existing.rows}_lt_{min_rows}"
        else:
            reason = f"stale_latest_{existing.latest}_lt_{to_date}"
        plan.append(
            PlanRow(
                symbol=symbol,
                security_id=security_id,
                exchange_segment=str(getattr(row, "exchange_segment", "NSE_EQ") or "NSE_EQ"),
                instrument=str(getattr(row, "instrument", "EQUITY") or "EQUITY"),
                existing_rows=existing.rows,
                existing_earliest=existing.earliest,
                existing_latest=existing.latest,
                reason=reason,
            )
        )
    return plan, skipped


async def fetch_one(
    session: aiohttp.ClientSession,
    item: PlanRow,
    *,
    from_date: str,
    to_date: str,
) -> tuple[PlanRow, pd.DataFrame | None, str]:
    payload = dhan_client.historical_payload(
        security_id=item.security_id,
        exchange_segment=item.exchange_segment,
        instrument=item.instrument,
        from_date=from_date,
        to_date=to_date,
    )
    url = f"{settings.DHAN_BASE_URL}/v2/charts/historical"
    for attempt in range(settings.FETCH_RETRY_COUNT):
        try:
            async with session.post(url, json=payload) as response:
                text = await response.text()
                if response.status == 200:
                    data = await response.json(content_type=None)
                    frame = dhan_client.parse_historical_response(data)
                    if frame.empty:
                        return item, None, "empty Dhan response"
                    return item, frame, ""
                if response.status == 429:
                    await asyncio.sleep(2**attempt)
                    continue
                return item, None, f"HTTP {response.status}: {text[:200]}"
        except Exception as exc:
            if attempt == settings.FETCH_RETRY_COUNT - 1:
                return item, None, str(exc)
            await asyncio.sleep(2**attempt)
    return item, None, "retries exhausted"


async def worker(
    queue: asyncio.Queue[PlanRow | None],
    results: asyncio.Queue[tuple[PlanRow, pd.DataFrame | None, str]],
    session: aiohttp.ClientSession,
    *,
    from_date: str,
    to_date: str,
) -> None:
    while True:
        item = await queue.get()
        try:
            if item is None:
                return
            await results.put(
                await fetch_one(session, item, from_date=from_date, to_date=to_date)
            )
        finally:
            queue.task_done()


async def fetch_planned(
    conn,
    planned: list[PlanRow],
    *,
    from_date: str,
    to_date: str,
    max_concurrent: int,
) -> list[FetchResult]:
    queue: asyncio.Queue[PlanRow | None] = asyncio.Queue()
    results: asyncio.Queue[tuple[PlanRow, pd.DataFrame | None, str]] = asyncio.Queue()
    timeout = aiohttp.ClientTimeout(total=90)
    headers = dhan_client.dhan_headers()
    for item in planned:
        await queue.put(item)
    worker_count = max(1, min(max_concurrent, len(planned)))
    for _ in range(worker_count):
        await queue.put(None)

    output: list[FetchResult] = []
    async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
        workers = [
            asyncio.create_task(
                worker(queue, results, session, from_date=from_date, to_date=to_date)
            )
            for _ in range(worker_count)
        ]
        for completed in range(1, len(planned) + 1):
            item, frame, error = await results.get()
            if error or frame is None or frame.empty:
                result = FetchResult(
                    symbol=item.symbol,
                    security_id=item.security_id,
                    status="failed",
                    rows_written=0,
                    existing_rows=item.existing_rows,
                    existing_latest=item.existing_latest,
                    reason=item.reason,
                    error=error or "empty response",
                )
            else:
                written = storage.upsert_daily_rows(conn, item.symbol, item.security_id, frame)
                result = FetchResult(
                    symbol=item.symbol,
                    security_id=item.security_id,
                    status="success",
                    rows_written=written,
                    existing_rows=item.existing_rows,
                    existing_latest=item.existing_latest,
                    reason=item.reason,
                )
            output.append(result)
            print(
                f"[{completed}/{len(planned)}] {result.symbol}: "
                f"{result.status} rows_written={result.rows_written} reason={result.reason}"
            )
        await queue.join()
        await asyncio.gather(*workers)
    return output


def default_report_path(universe_name: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = universe.normalise_profile_name(universe_name)
    return settings.OUTPUT_DIR / f"historical_fetch_{safe_name}_{stamp}.csv"


def write_report(path: Path, rows: list[FetchResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(asdict(rows[0]).keys()) if rows else list(FetchResult.__dataclass_fields__)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def symbols_from_retry_report(path: Path) -> set[str]:
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    if "symbol" not in frame.columns or "status" not in frame.columns:
        raise SystemExit(f"FAIL: retry report must have symbol and status columns: {path}")
    failed = frame[~frame["status"].astype(str).str.lower().isin({"success", "skipped"})]
    return set(failed["symbol"].astype(str).str.upper().str.strip())


def run(args: argparse.Namespace) -> dict[str, Any]:
    storage.ensure_directories()
    conn = storage.connect()
    storage.ensure_schema(conn)

    profile = universe.load_universe_profile(args.universe)
    if args.symbols:
        requested = {symbol.strip().upper() for symbol in args.symbols.split(",") if symbol.strip()}
        profile = profile[profile["symbol"].isin(requested)].copy()
    if args.retry_failed_report:
        retry_symbols = symbols_from_retry_report(args.retry_failed_report)
        profile = profile[profile["symbol"].isin(retry_symbols)].copy()
    if profile.empty:
        raise SystemExit("FAIL: no symbols selected after profile/symbol filters")

    to_date = args.to_date or date.today().isoformat()
    from_date = args.from_date or (date.today() - timedelta(days=365 * settings.HISTORY_YEARS)).isoformat()
    coverage = coverage_for_symbols(conn, profile["symbol"].tolist())
    planned, skipped = build_fetch_plan(
        profile,
        coverage,
        min_rows=args.min_rows,
        to_date=to_date,
        require_latest_date=args.require_latest_date,
    )
    if args.limit is not None:
        planned = planned[: args.limit]

    print(f"Universe: {args.universe}")
    print(f"Selected symbols: {len(profile)}")
    print(f"Already skipped: {len(skipped)}")
    print(f"Planned fetches: {len(planned)}")
    print(f"Window: {from_date} to {to_date}")
    if planned:
        preview = ", ".join(item.symbol for item in planned[: min(25, len(planned))])
        print(f"Fetch preview: {preview}")

    report_rows = list(skipped)
    if not args.execute:
        report_rows.extend(
            FetchResult(
                symbol=item.symbol,
                security_id=item.security_id,
                status="planned",
                rows_written=0,
                existing_rows=item.existing_rows,
                existing_latest=item.existing_latest,
                reason=item.reason,
            )
            for item in planned
        )
        report_path = args.report_path or default_report_path(args.universe)
        write_report(report_path, report_rows)
        print(f"DRY RUN: no Dhan calls made. Report: {report_path}")
        return {"planned": len(planned), "skipped": len(skipped), "report": str(report_path)}

    if not planned:
        report_path = args.report_path or default_report_path(args.universe)
        write_report(report_path, report_rows)
        print(f"Nothing to fetch. Report: {report_path}")
        return {"planned": 0, "skipped": len(skipped), "report": str(report_path)}

    fetched = asyncio.run(
        fetch_planned(
            conn,
            planned,
            from_date=from_date,
            to_date=to_date,
            max_concurrent=args.max_concurrent,
        )
    )
    report_rows.extend(fetched)
    report_path = args.report_path or default_report_path(args.universe)
    write_report(report_path, report_rows)
    success = sum(1 for row in fetched if row.status == "success")
    failed = sum(1 for row in fetched if row.status == "failed")
    print(f"Fetch complete: success={success}, failed={failed}, skipped={len(skipped)}")
    print(f"Report: {report_path}")
    if failed and success == 0:
        raise SystemExit("FAIL: all planned fetches failed")
    return {
        "planned": len(planned),
        "skipped": len(skipped),
        "success": success,
        "failed": failed,
        "report": str(report_path),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", default="watchlist")
    parser.add_argument("--limit", type=int, default=None, help="Limit planned fetches after skipping existing data.")
    parser.add_argument("--symbols", default="", help="Comma-separated symbols to restrict the selected universe.")
    parser.add_argument("--from-date", default=None)
    parser.add_argument("--to-date", default=None)
    parser.add_argument("--min-rows", type=int, default=1, help="Skip symbols with at least this many existing rows.")
    parser.add_argument(
        "--require-latest-date",
        action="store_true",
        help="Fetch symbols whose latest local date is older than --to-date even if they have min rows.",
    )
    parser.add_argument("--max-concurrent", type=int, default=settings.MAX_CONCURRENT_FETCHES)
    parser.add_argument("--retry-failed-report", type=Path, default=None)
    parser.add_argument("--report-path", type=Path, default=None)
    parser.add_argument("--execute", action="store_true", help="Call Dhan and write SQLite rows. Default is dry-run.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
