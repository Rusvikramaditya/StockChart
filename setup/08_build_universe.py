"""Build the broad NSE equity universe from the Dhan instrument master."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import storage, universe


def run(
    *,
    force_master_refresh: bool = False,
    output_path: Path | None = None,
    min_rows: int = 1_500,
) -> universe.UniverseBuildResult:
    storage.ensure_directories()
    result = universe.build_all_nse_equity_universe(
        output_path=output_path,
        force_master_refresh=force_master_refresh,
    )
    print(f"Broad NSE equity rows: {result.rows}")
    print(f"Duplicate symbols removed: {result.duplicates_removed}")
    print(f"Output: {result.output_path}")
    if result.series_counts:
        counts = ", ".join(f"{key}={value}" for key, value in result.series_counts.items())
        print(f"Series counts: {counts}")
    if result.rows < min_rows:
        raise SystemExit(f"FAIL: broad NSE equity universe has fewer than {min_rows} rows")
    print("PASS: config/all_nse_equity.csv ready")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force-master-refresh", action="store_true")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--min-rows", type=int, default=1_500)
    args = parser.parse_args()
    run(
        force_master_refresh=args.force_master_refresh,
        output_path=args.output,
        min_rows=args.min_rows,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
