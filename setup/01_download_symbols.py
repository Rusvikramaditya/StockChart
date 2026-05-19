"""Download Nifty 500 symbols and merge them with Dhan security IDs."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import storage, symbols


def run(force_download: bool = False, force_master_refresh: bool = False) -> tuple[int, int]:
    storage.ensure_directories()
    merged, missing = symbols.create_nifty500_dhan_file(
        force_download=force_download,
        force_master_refresh=force_master_refresh,
    )
    total = len(merged)
    matched = total - len(missing)
    print(f"Nifty 500 rows: {total}")
    print(f"Dhan security IDs matched: {matched}/{total}")
    if missing:
        preview = ", ".join(missing[:20])
        print(f"Missing Dhan IDs ({len(missing)}): {preview}")
    if total < 450:
        raise SystemExit("FAIL: Nifty 500 CSV has fewer than 450 rows")
    if matched < int(total * 0.90):
        raise SystemExit("FAIL: Dhan merge matched fewer than 90% of symbols")
    print("PASS: config/nifty500.csv and config/nifty500_dhan.csv ready")
    return matched, total


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force-download", action="store_true")
    parser.add_argument("--force-master-refresh", action="store_true")
    args = parser.parse_args()
    run(args.force_download, args.force_master_refresh)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

