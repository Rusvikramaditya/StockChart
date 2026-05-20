"""CLI for Pattern Finder backtests."""

from __future__ import annotations

import argparse
from pathlib import Path

from backtest.engine import run_backtest
from backtest.report import write_report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Pattern Finder walkforward backtest.")
    parser.add_argument("--universe", action="append", default=None, help="Universe profile. Can be repeated.")
    parser.add_argument("--years", type=int, default=3)
    parser.add_argument("--min-conviction", type=int, default=50)
    parser.add_argument("--entry", choices=["next_open", "pivot"], default="next_open")
    parser.add_argument("--max-hold-days", type=int, default=30)
    parser.add_argument("--min-history", type=int, default=200)
    parser.add_argument("--limit-symbols", type=int, default=None)
    parser.add_argument("--max-days", type=int, default=None)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    universes = args.universe or ["nifty500"]
    failures: list[tuple[str, str]] = []
    for universe in universes:
        try:
            result = run_backtest(
                universe=universe,
                lookback_years=args.years,
                entry_mode=args.entry,
                max_hold_days=args.max_hold_days,
                min_conviction=args.min_conviction,
                min_history_rows=args.min_history,
                limit_symbols=args.limit_symbols,
                max_days=args.max_days,
                start_date=args.start_date,
                end_date=args.end_date,
            )
            output = args.output
            if output is not None and len(universes) > 1:
                output = output.with_name(f"{output.stem}_{universe}{output.suffix}")
            report_path = write_report(result, output)
            print(
                f"{universe}: trades={result.summary['trades']} "
                f"win_rate={result.summary['win_rate']}% report={report_path}"
            )
        except Exception as exc:
            failures.append((universe, str(exc)))
            print(f"{universe}: FAILED {exc}")
    if failures:
        print("Backtest failures:")
        for universe, message in failures:
            print(f"  {universe}: {message}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
