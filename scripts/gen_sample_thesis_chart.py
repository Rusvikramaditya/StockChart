"""Generate a real-stock thesis chart sample from local SQLite DB.

Usage:
    python scripts/gen_sample_thesis_chart.py [SYMBOL]

Outputs:
    output/charts/<SYMBOL>_thesis_chart_<date>.html   — self-contained chart
    output/charts/<SYMBOL>_thesis_payload_<date>.json — raw payload (debug)

Trade levels are manually supplied for renderer QA and clearly marked in the
payload. Scanner-integrated chart export is verified separately by the pipeline.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

# Allow running from project root or from scripts/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from config import settings
from engine.chart_payload import build_chart_payload, payload_to_json, validate_payload


SYMBOL_DEFAULT = "ADANIENT"
PATTERN_DEFAULT = "ascending_triangle"

COMPANY_NAMES: dict[str, str] = {
    "ADANIENT": "Adani Enterprises Ltd",
    "INFY": "Infosys Ltd",
    "RELIANCE": "Reliance Industries Ltd",
    "TCS": "Tata Consultancy Services Ltd",
    "HDFCBANK": "HDFC Bank Ltd",
    "ICICIBANK": "ICICI Bank Ltd",
    "TATAMOTORS": "Tata Motors Ltd",
    "WIPRO": "Wipro Ltd",
    "SBIN": "State Bank of India",
    "BAJFINANCE": "Bajaj Finance Ltd",
}

SAMPLE_PACK: tuple[tuple[str, str], ...] = (
    ("ADANIENT", "ascending_triangle"),
    ("INFY", "cup_handle"),
    ("RELIANCE", "bull_flag"),
    ("TCS", "vcp"),
    ("SBIN", "inverse_head_shoulders"),
    ("WIPRO", "supertrend"),
    ("HDFCBANK", "multi_year_breakout"),
)

OUTPUT_DIR = settings.OUTPUT_DIR / "charts"
DASHBOARD_DIR = settings.BASE_DIR / "dashboard"
VENDOR_DIR = DASHBOARD_DIR / "vendor"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a real-stock thesis chart renderer QA sample.")
    parser.add_argument("symbol", nargs="?", default=SYMBOL_DEFAULT, help="NSE symbol present in the local DB.")
    parser.add_argument(
        "--pattern",
        choices=sorted(PATTERN_BUILDERS),
        default=PATTERN_DEFAULT,
        help="Pattern overlay type to render.",
    )
    parser.add_argument(
        "--sample-pack",
        action="store_true",
        help="Generate one real-stock QA sample for each supported pattern overlay.",
    )
    return parser.parse_args(argv)


def load_ohlcv(symbol: str) -> pd.DataFrame:
    conn = sqlite3.connect(settings.DB_PATH)
    df = pd.read_sql_query(
        "SELECT date, open, high, low, close, volume FROM ohlcv_daily "
        "WHERE symbol = ? ORDER BY date",
        conn,
        params=(symbol,),
    )
    conn.close()
    if df.empty:
        raise ValueError(f"No data found for symbol '{symbol}' in the local DB")
    return df


class _ManualResult:
    """Manually supplied trade levels — renderer QA only."""

    def __init__(self, entry: float, target: float, stop: float, pattern: str, bars: int, extra: dict):
        self.pattern = pattern
        self.status = "BREAKING OUT"
        self.pivot = entry
        self.target = target
        self.stop_loss = stop
        self.confidence = 0.0
        self.bars_in_pattern = bars
        self.extra = extra


def main(symbol: str = SYMBOL_DEFAULT, pattern_key: str = PATTERN_DEFAULT) -> None:
    symbol = symbol.upper()
    if pattern_key not in PATTERN_BUILDERS:
        supported = ", ".join(sorted(PATTERN_BUILDERS))
        raise ValueError(f"Unsupported pattern '{pattern_key}'. Supported: {supported}")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Loading OHLCV for {symbol}...")
    df = load_ohlcv(symbol)
    print(f"  {len(df)} rows, last date: {df['date'].iloc[-1]}")

    last_close = float(df["close"].iloc[-1])
    entry = round(last_close * 1.005, 2)
    target = round(last_close * 1.18, 2)
    stop = round(last_close * 0.94, 2)
    print(f"  Manual levels — entry: {entry}  target: {target}  stop: {stop}")

    company = COMPANY_NAMES.get(symbol, symbol)
    pattern_name, bars, extra = PATTERN_BUILDERS[pattern_key](len(df), stop)
    result = _ManualResult(entry=entry, target=target, stop=stop, pattern=pattern_name, bars=bars, extra=extra)

    payload = build_chart_payload(
        df,
        symbol,
        result,
        company_name=company,
        timeframe="Daily",
        lookback_bars=120,
    )

    # Flag that trade levels are manually supplied for renderer QA.
    payload["_proof_note"] = (
        "Trade levels manually supplied for renderer QA. "
        "Scanner-integrated chart export is verified separately by the pipeline."
    )

    validate_payload(payload)

    today = date.today().strftime("%Y%m%d")
    pattern_suffix = "" if pattern_key == PATTERN_DEFAULT else f"_{pattern_key}"

    # Save JSON payload
    json_path = OUTPUT_DIR / f"{symbol}_thesis_payload{pattern_suffix}_{today}.json"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"  Payload -> {json_path}")

    # Build standalone HTML
    html_path = OUTPUT_DIR / f"{symbol}_thesis_chart{pattern_suffix}_{today}.html"
    html_path.write_text(_build_standalone_html(payload), encoding="utf-8")
    print(f"  Chart  -> {html_path}")
    print()
    print("Open the chart HTML in a browser to visually verify the output.")
    print(
        "PNG export: python scripts/export_chart_screenshot.py "
        f"{html_path.relative_to(settings.BASE_DIR)}"
    )
    print("Scanner-integrated chart export is verified separately by the pipeline.")


def main_sample_pack() -> None:
    for symbol, pattern_key in SAMPLE_PACK:
        main(symbol, pattern_key)
        print()


def _ascending_triangle(_source_rows: int, _stop: float) -> tuple[str, int, dict]:
    return "Ascending Triangle", 60, {
        "touch_indices": [12, 34, 55],
        "low_indices": [5, 28, 50],
    }


def _cup_handle(_source_rows: int, _stop: float) -> tuple[str, int, dict]:
    return "Cup & Handle", 80, {
        "left_rim_idx": 8,
        "trough_idx": 36,
        "right_rim_idx": 62,
        "handle_start_idx": 66,
    }


def _bull_flag(source_rows: int, _stop: float) -> tuple[str, int, dict]:
    return "Bull Flag", 70, {
        "pole_start_idx": max(0, source_rows - 86),
        "pole_end_idx": max(0, source_rows - 66),
    }


def _vcp(_source_rows: int, _stop: float) -> tuple[str, int, dict]:
    return "VCP", 70, {"contractions": [14, 9, 5]}


def _inverse_head_shoulders(_source_rows: int, _stop: float) -> tuple[str, int, dict]:
    # SBIN last-100-bar minima: ls=59(1036.10), head=68(975.80), rs=74(1038.30)
    # head is the deepest — W-valley curves downward correctly
    return "Inverse Head & Shoulders", 100, {
        "left_shoulder_idx": 59,
        "head_idx": 68,
        "right_shoulder_idx": 74,
    }


def _supertrend(_source_rows: int, stop: float) -> tuple[str, int, dict]:
    return "Supertrend Flip", 35, {"supertrend": round(stop, 2)}


def _multi_year_breakout(_source_rows: int, _stop: float) -> tuple[str, int, dict]:
    return "Multi-Year Breakout", 100, {"resistance_touch_indices": [8, 48, 88]}


PATTERN_BUILDERS = {
    "ascending_triangle": _ascending_triangle,
    "cup_handle": _cup_handle,
    "bull_flag": _bull_flag,
    "vcp": _vcp,
    "inverse_head_shoulders": _inverse_head_shoulders,
    "supertrend": _supertrend,
    "multi_year_breakout": _multi_year_breakout,
}


def _build_standalone_html(payload: dict) -> str:
    """Render a fully self-contained thesis chart HTML from a payload dict."""
    lw_js = (VENDOR_DIR / "lightweight-charts.standalone.production.js").read_text(encoding="utf-8")
    renderer_js = (DASHBOARD_DIR / "chart_renderer.js").read_text(encoding="utf-8")
    annotations_js = (DASHBOARD_DIR / "chart_annotations.js").read_text(encoding="utf-8")
    payload_json = payload_to_json(payload)
    title = f"{payload['symbol']} — Thesis Chart"

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
    .tc-proof-note {{
      position: absolute; top: 6px; right: 80px;
      font-size: 10px; color: rgba(220,100,0,0.7); z-index: 10;
      pointer-events: none; font-family: Inter, Arial, sans-serif;
      max-width: 300px; text-align: right;
    }}
  </style>
</head>
<body>
  <div id="tc-root">
    <a class="tc-attribution"
       href="https://www.tradingview.com"
       target="_blank"
       rel="noopener noreferrer">Powered by TradingView</a>
    <div class="tc-proof-note">
      Manual trade levels — renderer QA only.<br>Scanner chart export verified separately.
    </div>
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


if __name__ == "__main__":
    args = parse_args()
    if args.sample_pack:
        main_sample_pack()
    else:
        main(args.symbol, args.pattern)
