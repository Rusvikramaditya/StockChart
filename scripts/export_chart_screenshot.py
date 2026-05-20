"""Export a rendered thesis chart HTML file to PNG with nonblank validation.

The reusable implementation lives in ``engine.chart_screenshot`` and waits for
the ``data-chart-ready`` marker before asserting ``colored_pixels``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.chart_screenshot import (  # noqa: E402
    DEFAULT_HEIGHT,
    DEFAULT_WIDTH,
    export_chart_screenshot,
    find_chrome_executable,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("html_path", type=Path)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT)
    parser.add_argument("--chrome-path", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    html_path = args.html_path
    output_path = args.out or html_path.with_suffix(".png")
    stats = export_chart_screenshot(
        html_path,
        output_path,
        width=args.width,
        height=args.height,
        chrome_path=args.chrome_path,
    )
    print(f"Screenshot: {output_path}")
    print(
        "Canvas: "
        f"{stats['canvas_count']} canvas element(s), "
        f"{stats['colored_pixels']}/{stats['sampled_pixels']} colored sampled pixels"
    )
    print(f"Bytes: {stats['screenshot_bytes']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
