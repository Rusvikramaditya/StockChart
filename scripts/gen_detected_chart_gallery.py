"""Generate an approval gallery from actual detector hits only.

This deliberately does not force one sample per pattern. If the detector cannot
find a current local-DB hit for a pattern, that pattern is omitted.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings  # noqa: E402
from engine.chart_payload import build_chart_payload  # noqa: E402
from engine.chart_screenshot import export_chart_screenshot  # noqa: E402
from engine.data_loader import DataLoader  # noqa: E402
from engine.thesis_chart import write_thesis_chart_html  # noqa: E402
from patterns import ALL_DETECTORS  # noqa: E402
from patterns.base import PatternResult  # noqa: E402


DEFAULT_PATTERNS = ("Cup & Handle", "Inverse Head & Shoulders", "Multi-Year Breakout", "VCP")


@dataclass(frozen=True)
class Candidate:
    symbol: str
    company_name: str
    pattern: PatternResult


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate real detector-hit chart approval samples.")
    parser.add_argument("--universe", default="all_nse_equity", help="Universe profile to scan.")
    parser.add_argument("--max-per-pattern", type=int, default=1, help="Samples to keep per detected pattern.")
    parser.add_argument("--width", type=int, default=1600, help="Desktop screenshot width.")
    parser.add_argument("--height", type=int, default=1000, help="Desktop screenshot height.")
    parser.add_argument("--mobile-width", type=int, default=389, help="Mobile screenshot width.")
    parser.add_argument("--mobile-height", type=int, default=844, help="Mobile screenshot height.")
    parser.add_argument("--out-dir", type=Path, default=settings.BASE_DIR / "docs" / "chart_approval_samples")
    parser.add_argument("--html-dir", type=Path, default=settings.CHARTS_DIR / "detected_samples")
    parser.add_argument("--gallery", type=Path, default=settings.BASE_DIR / "docs" / "CHART_APPROVAL_GALLERY.html")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    args.html_dir.mkdir(parents=True, exist_ok=True)
    candidates = find_candidates(args.universe, args.max_per_pattern)
    if not candidates:
        raise SystemExit("No detector hits found; refusing to generate fake approval samples.")

    rows = []
    for candidate in candidates:
        rows.append(render_candidate(candidate, args))

    args.gallery.write_text(build_gallery_html(rows), encoding="utf-8")
    print(f"Gallery: {args.gallery}")
    for row in rows:
        print(f"{row['symbol']} {row['pattern']} -> {row['desktop']}, {row['mobile']}")
    return 0


def find_candidates(universe: str, max_per_pattern: int) -> list[Candidate]:
    loader = DataLoader()
    found: dict[str, list[Candidate]] = {}
    try:
        profile = loader.get_universe_profile(universe)
        name_map = {
            str(row.symbol).upper(): str(getattr(row, "company_name", "") or row.symbol)
            for row in profile.itertuples(index=False)
        }
        for symbol in profile["symbol"].astype(str).str.upper().tolist():
            daily = loader.get_stock_daily_arrays(symbol)
            weekly = loader.get_stock_weekly_arrays(symbol)
            if len(daily.get("close", [])) == 0:
                continue
            for detector in ALL_DETECTORS:
                for hit in detector(daily, weekly):
                    if hit.pattern not in DEFAULT_PATTERNS:
                        continue
                    found.setdefault(hit.pattern, []).append(
                        Candidate(symbol=symbol, company_name=name_map.get(symbol, symbol), pattern=hit)
                    )
    finally:
        loader.close()

    selected: list[Candidate] = []
    for pattern in DEFAULT_PATTERNS:
        rows = sorted(found.get(pattern, []), key=lambda item: float(item.pattern.confidence), reverse=True)
        selected.extend(rows[: max(1, max_per_pattern)])
    return selected


def render_candidate(candidate: Candidate, args: argparse.Namespace) -> dict[str, str]:
    loader = DataLoader()
    try:
        frame = (
            loader.get_stock_weekly(candidate.symbol)
            if candidate.pattern.timeframe.lower() == "weekly"
            else loader.get_stock_daily(candidate.symbol)
        )
    finally:
        loader.close()

    payload = build_chart_payload(
        frame,
        candidate.symbol,
        candidate.pattern,
        company_name=candidate.company_name,
        timeframe=candidate.pattern.timeframe.title(),
        lookback_bars=max(120, int(candidate.pattern.bars_in_pattern) + 30),
    )
    payload["_proof_note"] = "Actual detector hit from local OHLCV DB. Levels are detector output, not manual QA."

    stamp = date.today().strftime("%Y%m%d")
    base = f"REAL_{candidate.symbol}_{slug(candidate.pattern.pattern)}_{stamp}"
    html_path = args.html_dir / f"{base}.html"
    desktop_path = args.out_dir / f"{base}_desktop.png"
    mobile_path = args.out_dir / f"{base}_mobile.png"
    write_thesis_chart_html(payload, html_path)
    export_chart_screenshot(html_path, desktop_path, width=args.width, height=args.height)
    export_chart_screenshot(html_path, mobile_path, width=args.mobile_width, height=args.mobile_height)
    return {
        "symbol": candidate.symbol,
        "company": candidate.company_name,
        "pattern": candidate.pattern.pattern,
        "status": candidate.pattern.status,
        "timeframe": candidate.pattern.timeframe,
        "confidence": f"{float(candidate.pattern.confidence):.1f}",
        "pivot": f"{float(candidate.pattern.pivot):.2f}",
        "target": f"{float(candidate.pattern.target):.2f}",
        "stop": f"{float(candidate.pattern.stop_loss):.2f}",
        "explanation": candidate.pattern.explanation,
        "desktop": desktop_path.name,
        "mobile": mobile_path.name,
    }


def build_gallery_html(rows: list[dict[str, str]]) -> str:
    cards = "\n".join(sample_card(row) for row in rows)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Real Detector Chart Gallery</title>
<style>
:root{{color-scheme:light;--bg:#f5f5f3;--panel:#fff;--text:#171717;--muted:#5f6368;--border:#d9d9d6;--accent:#0f766e;}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--text);font-family:Arial,Helvetica,sans-serif;line-height:1.45;}}
header,main{{max-width:1320px;margin:0 auto;padding:24px 20px;}}
h1{{margin:0 0 8px;font-size:30px;line-height:1.15;letter-spacing:0;}}
p{{margin:0 0 10px;color:var(--muted)}}
code{{padding:2px 5px;border:1px solid var(--border);border-radius:4px;background:#fafafa;color:#111;}}
.sample{{margin-top:18px;border:1px solid var(--border);border-radius:8px;background:var(--panel);overflow:hidden;}}
.sample-head{{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;padding:14px 16px;border-bottom:1px solid var(--border);}}
.sample h2{{margin:0;font-size:18px;line-height:1.25}}
.meta{{color:var(--muted);font-size:13px;margin-top:6px}}
.symbol{{color:var(--accent);font-weight:800;white-space:nowrap}}
.levels{{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}}
.levels span{{border:1px solid var(--border);border-radius:6px;padding:5px 8px;font-size:12px;color:#222;background:#fafafa}}
.grid{{display:grid;grid-template-columns:minmax(0,1fr) minmax(280px,420px);gap:14px;padding:14px;align-items:start}}
.frame-title{{margin:0 0 8px;color:var(--muted);font-size:13px;font-weight:700;text-transform:uppercase}}
img{{display:block;width:100%;height:auto;border:1px solid var(--border);background:#fff}}
.note{{margin-top:18px;padding:14px 16px;border:1px solid var(--border);border-radius:8px;background:#fff;color:var(--muted)}}
@media (max-width:860px){{h1{{font-size:24px}}.sample-head{{flex-direction:column}}.grid{{grid-template-columns:1fr}}}}
</style>
</head>
<body>
<header>
  <h1>Real Detector Chart Gallery</h1>
  <p>This page contains only actual detector hits from the local OHLCV database. No pattern label is forced onto a stock just to fill a visual sample slot.</p>
  <p>If a pattern type is missing here, the active detector did not find a current local-DB example for it. Missing is better than fake.</p>
</header>
<main>
{cards}
  <div class="note">These are detector outputs for review, not trade recommendations. Confirm the structure manually before trusting any pattern label.</div>
</main>
</body>
</html>
"""


def sample_card(row: dict[str, str]) -> str:
    safe = {key: html.escape(value) for key, value in row.items()}
    return f"""  <section class="sample">
    <div class="sample-head">
      <div>
        <h2>{safe['pattern']}</h2>
        <div class="meta">{safe['company']} | {safe['timeframe']} | {safe['status']} | confidence {safe['confidence']}</div>
        <p>{safe['explanation']}</p>
        <div class="levels">
          <span>Entry {safe['pivot']}</span>
          <span>Target {safe['target']}</span>
          <span>Stop {safe['stop']}</span>
        </div>
      </div>
      <div class="symbol">{safe['symbol']}</div>
    </div>
    <div class="grid">
      <div><p class="frame-title">Desktop</p><img src="chart_approval_samples/{safe['desktop']}" alt="{safe['symbol']} {safe['pattern']} desktop chart"></div>
      <div><p class="frame-title">Mobile</p><img src="chart_approval_samples/{safe['mobile']}" alt="{safe['symbol']} {safe['pattern']} mobile chart"></div>
    </div>
  </section>"""


def slug(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", str(value)).strip("_").lower()


if __name__ == "__main__":
    raise SystemExit(main())
