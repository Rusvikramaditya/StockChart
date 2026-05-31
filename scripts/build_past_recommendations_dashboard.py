"""Build the past MEDIUM/HIGH/HIGHEST suggestions dashboard."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import past_reports_dashboard  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build past MEDIUM/HIGH/HIGHEST suggestion performance dashboard.")
    parser.add_argument("--days", type=int, default=past_reports_dashboard.DEFAULT_DAYS, help="Default selected day window in the HTML UI.")
    parser.add_argument("--max-days", type=int, default=past_reports_dashboard.MAX_SCAN_DAYS, help="Maximum lookback loaded into the HTML.")
    parser.add_argument("--output", type=Path, default=None, help="Output HTML path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    path = past_reports_dashboard.write_dashboard(
        output_path=args.output,
        default_days=args.days,
        max_days=args.max_days,
    )
    print(f"Dashboard: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
