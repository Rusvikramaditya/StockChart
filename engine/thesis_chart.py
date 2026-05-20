"""Standalone thesis chart HTML and screenshot generation."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

from config import settings
from engine.chart_payload import payload_to_json
from engine.chart_screenshot import export_chart_screenshot


DASHBOARD_DIR = settings.BASE_DIR / "dashboard"
VENDOR_DIR = DASHBOARD_DIR / "vendor"


def write_thesis_chart_html(payload: dict[str, Any], output_path: str | Path) -> Path:
    """Write one fully self-contained Lightweight Charts thesis chart HTML."""

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(build_standalone_html(payload), encoding="utf-8")
    return output


def export_thesis_chart_png(
    scored: dict[str, Any],
    *,
    output_dir: str | Path | None = None,
    width: int = 1600,
    height: int = 1000,
) -> dict[str, Any]:
    """Render a scored result's chart payload to HTML and PNG.

    Returns paths and screenshot stats. Raises clearly if no payload is present
    or if the browser screenshot validation fails.
    """

    payload = scored.get("chart_payload")
    if not isinstance(payload, dict):
        raise ValueError("chart_payload is required for thesis chart screenshot export")

    output_root = Path(output_dir) if output_dir is not None else settings.CHARTS_DIR
    output_root.mkdir(parents=True, exist_ok=True)
    basename = _chart_basename(payload, scored)
    html_path = output_root / f"{basename}.html"
    png_path = output_root / f"{basename}.png"
    write_thesis_chart_html(payload, html_path)
    stats = export_chart_screenshot(html_path, png_path, width=width, height=height)
    return {"html_path": html_path, "png_path": png_path, "stats": stats}


def build_standalone_html(payload: dict[str, Any]) -> str:
    """Return a complete offline HTML document for one thesis chart payload."""

    lw_js = (VENDOR_DIR / "lightweight-charts.standalone.production.js").read_text(encoding="utf-8")
    renderer_js = (DASHBOARD_DIR / "chart_renderer.js").read_text(encoding="utf-8")
    annotations_js = (DASHBOARD_DIR / "chart_annotations.js").read_text(encoding="utf-8")
    payload_json = payload_to_json(payload)
    title = escape(f"{payload.get('symbol', 'Chart')} - Thesis Chart")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    html, body {{ width: 100%; height: 100%; background: #ffffff; overflow: hidden; }}
    #tc-root {{ position: relative; width: 100%; height: 100vh; }}
    .tc-attribution {{
      position: absolute; bottom: 8px; left: 12px;
      font-size: 11px; color: #b0b0b0; z-index: 10; pointer-events: auto;
      font-family: Inter, Arial, sans-serif; text-decoration: none;
    }}
    .tc-attribution:hover {{ color: #888; }}
  </style>
</head>
<body>
  <div id="tc-root">
    <a class="tc-attribution"
       href="https://www.tradingview.com"
       target="_blank"
       rel="noopener noreferrer">Powered by TradingView</a>
  </div>
  <script>{lw_js}</script>
  <script>{renderer_js}</script>
  <script>{annotations_js}</script>
  <script>
    var payload = {payload_json};
    ThesisChart.init(document.getElementById('tc-root'), payload);
  </script>
</body>
</html>"""


def _chart_basename(payload: dict[str, Any], scored: dict[str, Any]) -> str:
    symbol = payload.get("symbol") or scored.get("symbol") or "chart"
    pattern = (payload.get("pattern") or {}).get("type") or scored.get("pattern") or "pattern"
    stamp = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:10]}"
    return "_".join([_slug(symbol), "thesis", _slug(pattern), stamp])


def _slug(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "_", str(value).strip()).strip("_").lower()
    return text or "chart"
