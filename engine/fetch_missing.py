"""Backfill missing historical OHLCV from Dhan into the local SQLite cache.

Reusable engine for two callers:
  * setup/10_fetch_missing_historical.py CLI (planned dry-runs + execute)
  * scanner.py pre-scan stage (auto-backfill before each live scan)

Senior-architect contract:
  * No argparse here. Callers pass plain kwargs to ``fetch_missing_for_profile``.
  * Returns structured dataclasses so the scanner can apply per-symbol fallback
    (drop failed symbols from the scan) without parsing print output.
  * Async I/O is encapsulated behind a sync ``run`` helper.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable

import aiohttp
import pandas as pd

from config import settings
from engine import dhan_client, storage


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
    status: str  # "success" | "failed" | "skipped" | "planned"
    rows_written: int
    existing_rows: int
    existing_latest: str
    reason: str
    error: str = ""


# ---------------------------------------------------------------------------
# Coverage + planning
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Async fetch
# ---------------------------------------------------------------------------

async def _fetch_one(
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
                    await asyncio.sleep(2 ** attempt)
                    continue
                return item, None, f"HTTP {response.status}: {text[:200]}"
        except Exception as exc:  # network / parse / auth
            if attempt == settings.FETCH_RETRY_COUNT - 1:
                return item, None, str(exc)
            await asyncio.sleep(2 ** attempt)
    return item, None, "retries exhausted"


async def _worker(
    queue: "asyncio.Queue[PlanRow | None]",
    results: "asyncio.Queue[tuple[PlanRow, pd.DataFrame | None, str]]",
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
                await _fetch_one(session, item, from_date=from_date, to_date=to_date)
            )
        finally:
            queue.task_done()


async def _fetch_planned_async(
    conn,
    planned: list[PlanRow],
    *,
    from_date: str,
    to_date: str,
    max_concurrent: int,
    progress_cb=None,
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
                _worker(queue, results, session, from_date=from_date, to_date=to_date)
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
            if progress_cb is not None:
                progress_cb(completed, len(planned), result)
        await queue.join()
        await asyncio.gather(*workers)
    return output


def fetch_planned(
    conn,
    planned: list[PlanRow],
    *,
    from_date: str,
    to_date: str,
    max_concurrent: int,
    progress_cb=None,
) -> list[FetchResult]:
    """Sync wrapper around ``_fetch_planned_async`` for non-async callers."""
    return asyncio.run(
        _fetch_planned_async(
            conn,
            planned,
            from_date=from_date,
            to_date=to_date,
            max_concurrent=max_concurrent,
            progress_cb=progress_cb,
        )
    )


# ---------------------------------------------------------------------------
# High-level orchestration
# ---------------------------------------------------------------------------

@dataclass
class FetchSummary:
    planned: int
    skipped: int
    success: int
    failed: int
    failed_symbols: list[str]
    rows_written: int
    results: list[FetchResult]


def fetch_missing_for_profile(
    conn,
    profile: pd.DataFrame,
    *,
    symbols: Iterable[str] | None = None,
    to_date: str | None = None,
    from_date: str | None = None,
    min_rows: int = 1,
    require_latest_date: bool = True,
    max_concurrent: int | None = None,
    limit: int | None = None,
    execute: bool = True,
    progress_cb=None,
) -> FetchSummary:
    """Plan + execute (or dry-run) historical backfill for a universe profile.

    Args:
        conn: SQLite connection already initialised via ``storage.ensure_schema``.
        profile: universe profile dataframe (must have ``symbol`` and
            ``security_id`` columns).
        symbols: optional symbol whitelist to filter the profile.
        to_date / from_date: ISO date strings. Defaults: today / (today - 5y).
        min_rows: symbols with fewer existing rows trigger a refetch.
        require_latest_date: also refetch when latest existing date < to_date.
        max_concurrent: parallel Dhan calls. Defaults to settings.MAX_CONCURRENT_FETCHES.
        limit: cap planned fetches after skipping fully-covered symbols.
        execute: when False, return summary with 0 fetches (dry run).
        progress_cb: optional callable ``(done, total, result)`` invoked per
            completed symbol. Used for live progress in the web server.

    Returns:
        FetchSummary with per-symbol results and the list of symbols that failed
        (caller can drop these from the scan to enforce per-symbol fallback).
    """
    selected = profile
    if symbols is not None:
        wanted = {str(s).upper().strip() for s in symbols if str(s).strip()}
        if wanted:
            selected = profile[profile["symbol"].astype(str).str.upper().isin(wanted)].copy()

    if selected.empty:
        return FetchSummary(0, 0, 0, 0, [], 0, [])

    to_date = to_date or date.today().isoformat()
    from_date = from_date or (date.today() - timedelta(days=365 * settings.HISTORY_YEARS)).isoformat()
    coverage = coverage_for_symbols(conn, selected["symbol"].astype(str).str.upper().tolist())
    planned, skipped = build_fetch_plan(
        selected,
        coverage,
        min_rows=min_rows,
        to_date=to_date,
        require_latest_date=require_latest_date,
    )
    if limit is not None:
        planned = planned[:limit]

    if not execute or not planned:
        return FetchSummary(
            planned=len(planned),
            skipped=len(skipped),
            success=0,
            failed=0,
            failed_symbols=[],
            rows_written=0,
            results=list(skipped)
            + [
                FetchResult(
                    symbol=item.symbol,
                    security_id=item.security_id,
                    status="planned" if execute else "planned",
                    rows_written=0,
                    existing_rows=item.existing_rows,
                    existing_latest=item.existing_latest,
                    reason=item.reason,
                )
                for item in planned
            ],
        )

    concurrent = max_concurrent or settings.MAX_CONCURRENT_FETCHES
    fetched = fetch_planned(
        conn,
        planned,
        from_date=from_date,
        to_date=to_date,
        max_concurrent=concurrent,
        progress_cb=progress_cb,
    )
    success = sum(1 for r in fetched if r.status == "success")
    failed = [r for r in fetched if r.status == "failed"]
    return FetchSummary(
        planned=len(planned),
        skipped=len(skipped),
        success=success,
        failed=len(failed),
        failed_symbols=[r.symbol for r in failed],
        rows_written=sum(r.rows_written for r in fetched),
        results=list(skipped) + list(fetched),
    )
