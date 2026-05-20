"""Static HTML report for backtest results."""

from __future__ import annotations

from datetime import datetime
from html import escape
from pathlib import Path

from config import settings
from backtest.metrics import BacktestResult


def write_report(result: BacktestResult, output_path: str | Path | None = None) -> Path:
    if output_path is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = settings.OUTPUT_DIR / f"backtest_{stamp}.html"
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(result), encoding="utf-8")
    return path


def render_report(result: BacktestResult) -> str:
    summary = result.summary
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Pattern Finder Backtest</title>
  <style>
    :root {{ --bg:#0a0a0a; --panel:#141414; --line:#2a2a2a; --text:#eee; --muted:#a3a3a3; --accent:#ff4800; --green:#22c55e; --red:#ef4444; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font-family:Segoe UI, Inter, Arial, sans-serif; }}
    main {{ width:min(1180px,100%); margin:0 auto; padding:24px; }}
    h1 {{ margin:0 0 8px; font-size:30px; }}
    h2 {{ margin:28px 0 12px; font-size:18px; }}
    .muted {{ color:var(--muted); }}
    .grid {{ display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:10px; margin:18px 0; }}
    .stat, section {{ border:1px solid var(--line); background:var(--panel); border-radius:8px; }}
    .stat {{ padding:14px; }}
    .stat b {{ display:block; color:var(--accent); font-size:22px; }}
    section {{ padding:14px; margin-bottom:14px; overflow:auto; }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; }}
    th,td {{ padding:9px 8px; border-bottom:1px solid var(--line); text-align:left; }}
    th {{ color:var(--muted); font-weight:600; }}
    .num {{ text-align:right; font-family:Consolas, monospace; }}
    .pos {{ color:var(--green); }}
    .neg {{ color:var(--red); }}
    svg {{ width:100%; height:220px; display:block; background:#101010; border-radius:8px; }}
  </style>
</head>
<body>
<main>
  <h1>Pattern Finder Backtest</h1>
  <p class="muted">Universe: {escape(result.universe)} | Generated {datetime.now().strftime('%d %b %Y %H:%M')}</p>
  <div class="grid">
    {_stat("Trades", summary["trades"])}
    {_stat("Win Rate", f'{summary["win_rate"]}%')}
    {_stat("Profit Factor", summary["profit_factor"])}
    {_stat("Expectancy", f'{summary["expectancy"]}%')}
    {_stat("Sharpe", summary["sharpe"])}
  </div>
  {_section("Summary By Pattern", _metrics_table(result.by_pattern, "Pattern"))}
  {_section("Conviction Tier Validation", _bucket_table(result.conviction_validation))}
  {_section("Quality Score Validation", _bucket_table(result.quality_validation))}
  {_section("Filter Impact", _filter_table(result.filter_impact))}
  {_section("Stack Validation", _bucket_table(result.stack_validation))}
  {_section("Equity Curve", _equity_svg(result.equity_curve))}
  {_section("Monthly Returns", _monthly_table(result.monthly_returns))}
</main>
</body>
</html>"""


def _stat(label: str, value) -> str:
    return f'<div class="stat"><span class="muted">{escape(label)}</span><b>{escape(str(value))}</b></div>'


def _section(title: str, body: str) -> str:
    return f"<h2>{escape(title)}</h2><section>{body}</section>"


def _metrics_table(rows: list[dict], group_label: str) -> str:
    headers = [group_label, "Trades", "Win%", "Avg Win", "Avg Loss", "PF", "Expectancy", "Sharpe", "Max DD"]
    body = []
    for row in rows:
        body.append(
            "<tr>"
            f"<td>{escape(str(row.get('group', 'ALL')))}</td>"
            f"<td class='num'>{row['trades']}</td>"
            f"<td class='num'>{row['win_rate']}</td>"
            f"<td class='num pos'>{row['avg_win_pct']}</td>"
            f"<td class='num neg'>{row['avg_loss_pct']}</td>"
            f"<td class='num'>{row['profit_factor']}</td>"
            f"<td class='num'>{row['expectancy']}</td>"
            f"<td class='num'>{row['sharpe']}</td>"
            f"<td class='num neg'>{row['max_drawdown']}</td>"
            "</tr>"
        )
    return _table(headers, body)


def _bucket_table(rows: list[dict]) -> str:
    body = []
    for row in rows:
        body.append(
            "<tr>"
            f"<td>{escape(str(row.get('bucket', '')))}</td>"
            f"<td class='num'>{row['trades']}</td>"
            f"<td class='num'>{row['win_rate']}</td>"
            f"<td class='num'>{row['profit_factor']}</td>"
            f"<td class='num'>{row['expectancy']}</td>"
            "</tr>"
        )
    return _table(["Bucket", "Trades", "Win%", "PF", "Expectancy"], body)


def _filter_table(rows: list[dict]) -> str:
    body = []
    for row in rows:
        klass = "pos" if row["improvement"] >= 0 else "neg"
        body.append(
            "<tr>"
            f"<td>{escape(str(row['filter']))}</td>"
            f"<td class='num'>{row['with_trades']} / {row['with_win_rate']}%</td>"
            f"<td class='num'>{row['without_trades']} / {row['without_win_rate']}%</td>"
            f"<td class='num {klass}'>{row['improvement']}%</td>"
            "</tr>"
        )
    return _table(["Filter", "With Filter", "Without", "Improvement"], body)


def _monthly_table(rows: list[dict]) -> str:
    body = []
    for row in rows:
        klass = "pos" if row["return_pct"] >= 0 else "neg"
        body.append(
            "<tr>"
            f"<td>{escape(row['month'])}</td>"
            f"<td class='num {klass}'>{row['return_pct']}%</td>"
            f"<td class='num'>{row['trades']}</td>"
            "</tr>"
        )
    return _table(["Month", "Return", "Trades"], body)


def _table(headers: list[str], rows: list[str]) -> str:
    head = "".join(f"<th>{escape(header)}</th>" for header in headers)
    body = "".join(rows) or f"<tr><td colspan='{len(headers)}' class='muted'>No trades in this slice.</td></tr>"
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _equity_svg(points: list[dict]) -> str:
    if not points:
        return "<p class='muted'>No equity curve because no trades were generated.</p>"
    values = [float(point["equity"]) for point in points]
    low = min(values)
    high = max(values)
    span = max(high - low, 1.0)
    coords = []
    for idx, value in enumerate(values):
        x = idx / max(1, len(values) - 1) * 1000
        y = 180 - ((value - low) / span * 160)
        coords.append(f"{x:.1f},{y:.1f}")
    return (
        "<svg viewBox='0 0 1000 220' role='img' aria-label='Equity curve'>"
        "<polyline fill='none' stroke='#ff4800' stroke-width='3' points='"
        + " ".join(coords)
        + "' />"
        "</svg>"
    )
