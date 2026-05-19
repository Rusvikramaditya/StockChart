"""Build selectable universe profile CSVs."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import storage, universe


def run_watchlist(symbols: list[str] | None = None) -> universe.WatchlistBuildResult:
    storage.ensure_directories()
    result = universe.build_watchlist_profile(
        universe.DEFAULT_WATCHLIST_SYMBOLS if symbols is None else symbols
    )
    print(f"Watchlist rows: {result.rows}")
    print(f"Symbols: {', '.join(result.symbols)}")
    print(f"Output: {result.output_path}")
    print("PASS: config/watchlist.csv ready")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--profile",
        choices=["watchlist"],
        default="watchlist",
        help="Profile to build. Data-derived profiles are intentionally not generated here.",
    )
    parser.add_argument(
        "--symbols",
        default="",
        help="Optional comma-separated watchlist override. Defaults to the Phase 6-0b sample symbols.",
    )
    args = parser.parse_args()

    symbols = [item.strip() for item in args.symbols.split(",") if item.strip()] or None
    if args.profile == "watchlist":
        run_watchlist(symbols)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
