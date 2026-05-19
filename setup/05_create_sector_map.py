"""Create config/sector_map.json from Nifty 500 industry labels."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import sector_map, symbols


def run() -> dict:
    active = symbols.load_active_symbols()
    payload = sector_map.write_sector_map(active)
    count = len(payload["symbols"])
    print(f"Sector mappings written: {count}")
    if count < 450:
        raise SystemExit("FAIL: fewer than 450 active symbols mapped")
    print("PASS: config/sector_map.json ready")
    return payload


def main() -> int:
    argparse.ArgumentParser().parse_args()
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

