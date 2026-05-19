"""Build selectable universe profile CSVs."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import storage, universe
from filters import liquidity


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


def run_small_mid_liquid() -> liquidity.LiquidityProfileResult:
    storage.ensure_directories()
    conn = storage.connect()
    try:
        result = liquidity.build_small_mid_liquid_profile(conn)
    finally:
        conn.close()
    rules = result.rules
    print(f"Small/mid liquid rows: {result.rows}")
    print(f"Evaluated rows: {result.evaluated_rows}")
    print(f"Excluded Nifty 500 symbols: {result.excluded_symbols}")
    print(f"Latest date: {result.latest_date}")
    print(
        "Rules: "
        f"{rules.min_history_rows} rows, "
        f"{rules.lookback_days}d avg volume >= {rules.min_avg_volume_50d:.0f}, "
        f"{rules.lookback_days}d avg traded value >= {rules.min_avg_traded_value_50d:.0f}, "
        f"price >= {rules.min_price:.2f}, stale <= {rules.stale_days} days"
    )
    print(f"Output: {result.output_path}")
    print("PASS: config/small_mid_liquid.csv ready")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--profile",
        choices=["watchlist", "small_mid_liquid"],
        default="watchlist",
        help="Profile to build.",
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
    elif args.profile == "small_mid_liquid":
        run_small_mid_liquid()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
