"""Fetch historical OHLCV only for symbols missing from the local SQLite DB.

Default behavior is a dry run. Pass --execute to call Dhan and write rows.
This is the CLI entrypoint; the actual fetch logic lives in
``engine.fetch_missing`` so the scanner can call the same code path as a
pre-scan stage.
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import settings
from engine import storage, universe
from engine.fetch_missing import (
    FetchResult,
    coverage_for_symbols,
    fetch_missing_for_profile,
)


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


def _progress(done: int, total: int, result: FetchResult) -> None:
    print(
        f"[{done}/{total}] {result.symbol}: "
        f"{result.status} rows_written={result.rows_written} reason={result.reason}"
    )


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

    print(f"Universe: {args.universe}")
    print(f"Selected symbols: {len(profile)}")
    print(f"Window: {from_date} to {to_date}")

    summary = fetch_missing_for_profile(
        conn,
        profile,
        to_date=to_date,
        from_date=from_date,
        min_rows=args.min_rows,
        require_latest_date=args.require_latest_date,
        max_concurrent=args.max_concurrent,
        limit=args.limit,
        execute=args.execute,
        progress_cb=_progress if args.execute else None,
    )

    print(f"Already skipped: {summary.skipped}")
    print(f"Planned fetches: {summary.planned}")
    if not args.execute:
        preview_planned = [r for r in summary.results if r.status == "planned"]
        if preview_planned:
            preview = ", ".join(r.symbol for r in preview_planned[: min(25, len(preview_planned))])
            print(f"Fetch preview: {preview}")

    report_path = args.report_path or default_report_path(args.universe)
    write_report(report_path, summary.results)
    if not args.execute:
        print(f"DRY RUN: no Dhan calls made. Report: {report_path}")
        return {"planned": summary.planned, "skipped": summary.skipped, "report": str(report_path)}

    print(f"Fetch complete: success={summary.success}, failed={summary.failed}, skipped={summary.skipped}")
    print(f"Report: {report_path}")
    if summary.failed and summary.success == 0 and summary.planned > 0:
        raise SystemExit("FAIL: all planned fetches failed")
    return {
        "planned": summary.planned,
        "skipped": summary.skipped,
        "success": summary.success,
        "failed": summary.failed,
        "rows_written": summary.rows_written,
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
