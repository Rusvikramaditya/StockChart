"""Analyze generated backtest HTML reports for Phase 8b tuning."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest.analyze import write_analysis  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze Pattern Finder backtest reports.")
    parser.add_argument("reports", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True, help="Markdown output path.")
    parser.add_argument("--json-out", type=Path, default=None, help="Optional JSON output path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    analysis = write_analysis(args.reports, args.out, json_output_path=args.json_out)
    print(f"Analysis: {args.out}")
    if args.json_out:
        print(f"JSON: {args.json_out}")
    print(f"Conviction: {analysis['conviction']['recommendation']}")
    print(f"Quality score: {analysis['quality_score']['recommendation']}")
    print(f"Stack bonus: {analysis['stack_bonus']['recommendation']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
