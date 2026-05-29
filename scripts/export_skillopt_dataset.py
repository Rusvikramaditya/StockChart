#!/usr/bin/env python3
"""Build a SkillOpt-compatible dataset from Pattern Finder backtests."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest.engine import run_backtest
from backtest.metrics import BacktestResult
from backtest.skillopt_export import (
    build_skillopt_item,
    label_rule_text,
    write_skillopt_dataset,
)
from config import settings


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export Pattern Finder walk-forward trades into SkillOpt SearchQA split format."
    )
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
    parser.add_argument("--output-dir", type=Path, default=settings.OUTPUT_DIR / "skillopt" / "latest")
    parser.add_argument("--min-promote-return-pct", type=float, default=0.0)
    parser.add_argument("--max-promote-drawdown-pct", type=float, default=None)
    parser.add_argument("--min-items", type=int, default=3)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    universes = args.universe or ["nifty500"]
    items: list[dict] = []
    result_summaries: list[dict] = []

    for universe in universes:
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
        result_summaries.append({
            "universe": universe,
            "summary": result.summary,
            "config": result.config,
        })
        items.extend(_items_from_result(result, start_index=len(items) + 1, args=args))

    if len(items) < int(args.min_items):
        print(f"Not enough backtest trades for SkillOpt export: {len(items)} item(s).")
        print("Try a broader universe, more days, more symbols, or a lower min-conviction.")
        return 1

    summary = write_skillopt_dataset(
        items,
        args.output_dir,
        manifest={
            "source": "Pattern Finder walk-forward backtest",
            "universes": universes,
            "backtest_results": result_summaries,
            "label_rule": label_rule_text(args.min_promote_return_pct, args.max_promote_drawdown_pct),
        },
    )

    print(f"SkillOpt dataset written: {summary.output_dir}")
    print(f"Items: {summary.total_items}")
    print(f"Splits: {summary.split_counts}")
    print(f"Labels: {summary.label_counts}")
    print(f"Initial skill: {summary.initial_skill_path}")
    print(f"Run command: {summary.command_path}")
    return 0


def _items_from_result(result: BacktestResult, *, start_index: int, args: argparse.Namespace) -> list[dict]:
    return [
        build_skillopt_item(
            trade,
            universe=result.universe,
            index=start_index + idx,
            min_promote_return_pct=args.min_promote_return_pct,
            max_promote_drawdown_pct=args.max_promote_drawdown_pct,
        )
        for idx, trade in enumerate(result.trades)
    ]


if __name__ == "__main__":
    raise SystemExit(main())
