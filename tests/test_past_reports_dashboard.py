"""Past MEDIUM/HIGH/HIGHEST suggestions dashboard tests."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from engine import past_reports_dashboard, storage


NOW = datetime(2026, 5, 30, 12, 0, 0)


def test_collect_picks_reads_medium_high_and_highest_report_cards(tmp_path):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    report = output_dir / "control_20260501_101500_watchlist.html"
    report.write_text(
        _report_html(
            symbol="ABC",
            company="ABC Industries",
            tier="HIGHEST",
            sector="NIFTY IT",
            pattern="Weekly Breakout",
            timeframe="Weekly",
            cmp_text="Rs.100",
            entry="102",
            target="140",
            stop="90",
        ),
        encoding="utf-8",
    )
    medium_report = output_dir / "scan_20260503_101500_watchlist.html"
    medium_report.write_text(
        _report_html(
            symbol="MID",
            company="Medium Ltd",
            tier="MEDIUM",
            sector="NIFTY AUTO",
            pattern="Flat Base",
            timeframe="Daily",
            cmp_text="Rs.80",
            entry="82",
            target="96",
            stop="74",
        ),
        encoding="utf-8",
    )
    ignored = output_dir / "verify_20260502_101500_watchlist.html"
    ignored.write_text(
        _report_html(
            symbol="VERIFY",
            company="Verify Ltd",
            tier="HIGH",
            sector="NIFTY FMCG",
            pattern="Double Bottom",
            timeframe="Daily",
            cmp_text="Rs.50",
            entry="51",
            target="60",
            stop="45",
        ),
        encoding="utf-8",
    )
    sample = output_dir / "phase5b_sample.html"
    sample.write_text(
        _report_html(
            symbol="TESTSTOCK",
            company="Test Stock",
            tier="HIGH",
            sector="NIFTY TEST",
            pattern="Sample Pattern",
            timeframe="Daily",
            cmp_text="Rs.70",
            entry="72",
            target="80",
            stop="64",
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "test.db"
    conn = storage.connect(db_path)
    storage.ensure_schema(conn)
    try:
        storage.upsert_daily_rows(
            conn,
            "ABC",
            "1",
            pd.DataFrame(
                [
                    {"date": "2026-05-29", "open": 120, "high": 126, "low": 119, "close": 125, "volume": 1000},
                ]
            ),
        )
        storage.upsert_daily_rows(
            conn,
            "MID",
            "2",
            pd.DataFrame(
                [
                    {"date": "2026-05-29", "open": 90, "high": 94, "low": 89, "close": 92, "volume": 1000},
                ]
            ),
        )
    finally:
        conn.close()

    picks = past_reports_dashboard.collect_picks(output_dir=output_dir, db_path=db_path, max_days=60, now=NOW)

    assert len(picks) == 2
    by_symbol = {pick.symbol: pick for pick in picks}
    pick = by_symbol["ABC"]
    assert pick.symbol == "ABC"
    assert pick.company_name == "ABC Industries"
    assert pick.sector == "NIFTY IT"
    assert pick.tier == "HIGHEST"
    assert pick.pattern == "Weekly Breakout"
    assert pick.timeframe == "weekly"
    assert pick.price_then == 100
    assert pick.cmp_today == 125
    assert pick.cmp_date == "2026-05-29"
    medium = by_symbol["MID"]
    assert medium.tier == "MEDIUM"
    assert medium.pattern == "Flat Base"
    assert medium.price_then == 80
    assert medium.cmp_today == 92


def test_render_dashboard_has_day_tier_search_controls():
    pick = past_reports_dashboard.PastPick(
        symbol="ABC",
        company_name="ABC Industries",
        sector="NIFTY IT",
        tier="HIGH",
        pattern="Double Bottom",
        timeframe="daily",
        recommended_at=datetime(2026, 5, 1, 10, 15),
        report_name="scan_20260501_101500.html",
        report_href="/output/scan_20260501_101500.html",
        price_then=100,
        cmp_today=112,
        cmp_date="2026-05-29",
        entry=101,
        target=130,
        stop_loss=92,
    )

    html = past_reports_dashboard.render_dashboard([pick], default_days=60, now=NOW)

    assert "Past Suggestions Performance" in html
    assert '<select id="days">' in html
    assert '<select id="tier">' in html
    assert '<select id="sector">' in html
    assert '<input id="search"' in html
    assert '"symbol": "ABC"' in html
    assert '"sector": "NIFTY IT"' in html
    assert '"priceThenText": "Rs.100"' in html
    assert '"cmpTodayText": "Rs.112"' in html
    assert '"changePctText": "+12.00%"' in html
    assert 'class="sort-header"' in html
    assert 'data-sort-default="return_desc"' in html
    assert "% Change" in html
    assert "Avg % change" in html
    assert "Best % change" in html
    assert "Overall success" in html
    assert "HIGHEST success" in html
    assert "HIGH success" in html
    assert "MEDIUM success" in html
    assert '<option value="MEDIUM">Medium only</option>' in html
    assert "populateSectorOptions" in html
    assert "updateSortHeaders" in html


def _report_html(
    *,
    symbol: str,
    company: str,
    tier: str,
    sector: str,
    pattern: str,
    timeframe: str,
    cmp_text: str,
    entry: str,
    target: str,
    stop: str,
) -> str:
    payload = {
        "symbol": symbol,
        "company_name": company,
        "timeframe": timeframe,
        "candles": [{"close": float(cmp_text.replace("Rs.", ""))}],
        "pattern": {"type": pattern},
        "trade_plan": {"entry": float(entry), "target": float(target), "stop": float(stop)},
    }
    return f"""
    <article class="result-card tier-high" data-tier-card="{tier}" data-sector="{sector}" data-pattern="{pattern}">
      <span class="symbol">{symbol}</span>
      <div class="metric"><span class="label">CMP</span><span class="value">{cmp_text}</span></div>
      <div class="metric"><span class="label">Entry</span><span class="value">Rs.{entry}</span></div>
      <div class="metric"><span class="label">Target</span><span class="value">Rs.{target}</span></div>
      <div class="metric"><span class="label">Stop / R:R</span><span class="value">Rs.{stop} / 2.0:1</span></div>
      <script type="application/json" class="tc-payload">{json.dumps(payload)}</script>
    </article>
    """
