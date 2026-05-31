"""Past MEDIUM/HIGH/HIGHEST suggestion performance dashboard."""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from config import settings
from engine import storage


DEFAULT_DAYS = 60
MAX_SCAN_DAYS = 365
REPORT_TIERS = {"MEDIUM", "HIGH", "HIGHEST"}
SKIP_REPORT_PREFIXES = ("verify_", "past_high_highest_", "past_suggestions_", "past_recommendations_")
SKIP_REPORT_NAME_PARTS = ("sample", "fixture")


@dataclass
class PastPick:
    symbol: str
    company_name: str
    sector: str
    tier: str
    pattern: str
    timeframe: str
    recommended_at: datetime
    report_name: str
    report_href: str
    price_then: float | None
    cmp_today: float | None
    cmp_date: str
    entry: float | None
    target: float | None
    stop_loss: float | None

    def to_dict(self, *, as_of: date) -> dict[str, Any]:
        change_pct = _pct_change(self.price_then, self.cmp_today)
        return {
            "symbol": self.symbol,
            "companyName": self.company_name,
            "sector": self.sector,
            "tier": self.tier,
            "pattern": self.pattern,
            "timeframe": self.timeframe,
            "recommendedAt": self.recommended_at.isoformat(),
            "recommendedDate": self.recommended_at.strftime("%d %b %Y"),
            "daysAgo": max(0, (as_of - self.recommended_at.date()).days),
            "reportName": self.report_name,
            "reportHref": self.report_href,
            "priceThen": self.price_then,
            "priceThenText": _money(self.price_then),
            "cmpToday": self.cmp_today,
            "cmpTodayText": _money(self.cmp_today),
            "cmpDate": self.cmp_date,
            "changePct": change_pct,
            "changePctText": "N/A" if change_pct is None else f"{change_pct:+.2f}%",
            "entry": self.entry,
            "entryText": _money(self.entry),
            "target": self.target,
            "targetText": _money(self.target),
            "stopLoss": self.stop_loss,
            "stopText": _money(self.stop_loss),
        }


def write_dashboard(
    *,
    output_path: str | Path | None = None,
    output_dir: str | Path = settings.OUTPUT_DIR,
    db_path: str | Path = settings.DB_PATH,
    default_days: int = DEFAULT_DAYS,
    max_days: int = MAX_SCAN_DAYS,
    now: datetime | None = None,
) -> Path:
    """Build and write the past suggestions dashboard."""
    now = now or datetime.now()
    picks = collect_picks(output_dir=output_dir, db_path=db_path, max_days=max_days, now=now)
    html_text = render_dashboard(picks, default_days=default_days, max_days=max_days, now=now)
    path = Path(output_path) if output_path else Path(output_dir) / "past_suggestions_dashboard.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html_text, encoding="utf-8")
    return path


def collect_picks(
    *,
    output_dir: str | Path = settings.OUTPUT_DIR,
    db_path: str | Path = settings.DB_PATH,
    max_days: int = MAX_SCAN_DAYS,
    now: datetime | None = None,
) -> list[PastPick]:
    """Read saved scanner HTML reports and return MEDIUM/HIGH/HIGHEST cards."""
    output_root = Path(output_dir)
    now = now or datetime.now()
    cutoff = now.date().toordinal() - int(max_days)
    rows: list[dict[str, Any]] = []
    for path in sorted(output_root.glob("*.html"), key=lambda item: item.stat().st_mtime):
        if not _is_report_candidate(path):
            continue
        report_dt = _report_datetime(path)
        if report_dt.date().toordinal() < cutoff:
            continue
        rows.extend(_parse_report(path, report_dt, output_root=output_root))

    symbols = sorted({str(row["symbol"]).upper() for row in rows if row.get("symbol")})
    latest = _latest_closes(symbols, db_path=Path(db_path))
    picks: list[PastPick] = []
    for row in rows:
        symbol = str(row["symbol"]).upper()
        close = latest.get(symbol, {})
        picks.append(
            PastPick(
                symbol=symbol,
                company_name=str(row.get("company_name") or symbol),
                sector=str(row.get("sector") or "UNKNOWN").strip() or "UNKNOWN",
                tier=str(row.get("tier") or "").upper(),
                pattern=str(row.get("pattern") or "Pattern"),
                timeframe=str(row.get("timeframe") or "daily").lower(),
                recommended_at=row["recommended_at"],
                report_name=str(row.get("report_name") or ""),
                report_href=str(row.get("report_href") or ""),
                price_then=_number(row.get("price_then")),
                cmp_today=_number(close.get("close")),
                cmp_date=str(close.get("date") or ""),
                entry=_number(row.get("entry")),
                target=_number(row.get("target")),
                stop_loss=_number(row.get("stop_loss")),
            )
        )
    return sorted(picks, key=lambda item: (item.recommended_at, item.symbol, item.pattern), reverse=True)


def render_dashboard(
    picks: list[PastPick],
    *,
    default_days: int = DEFAULT_DAYS,
    max_days: int = MAX_SCAN_DAYS,
    now: datetime | None = None,
) -> str:
    now = now or datetime.now()
    payload = [pick.to_dict(as_of=now.date()) for pick in picks]
    json_payload = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    generated = now.strftime("%d %b %Y, %H:%M")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Past Suggestions Performance</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #07090f;
      --panel: #10141d;
      --panel-2: #151b26;
      --line: #273142;
      --line-soft: #1d2533;
      --text: #f7f9ff;
      --muted: #9aa8bd;
      --faint: #6f7d91;
      --orange: #ff6b35;
      --cyan: #00d5ff;
      --green: #6ef195;
      --red: #ff5c7a;
      --yellow: #ffd166;
      --shadow: rgba(0, 0, 0, 0.42);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, "Segoe UI", Arial, sans-serif;
      letter-spacing: 0;
    }}
    button, input, select {{ font: inherit; letter-spacing: 0; }}
    .shell {{ width: min(1360px, 100%); margin: 0 auto; padding: 26px 18px 34px; }}
    .top {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 18px;
      align-items: end;
      padding: 18px 0 22px;
      border-bottom: 1px solid var(--line);
    }}
    .eyebrow {{ margin: 0 0 8px; color: var(--cyan); font-size: 12px; font-weight: 900; text-transform: uppercase; }}
    h1 {{ margin: 0; font-size: 34px; line-height: 1.05; }}
    .subline {{ margin: 9px 0 0; color: var(--muted); max-width: 760px; font-size: 14px; }}
    .stamp {{
      min-width: 220px;
      border: 1px solid var(--line);
      border-left: 4px solid var(--orange);
      border-radius: 8px;
      padding: 12px 14px;
      background: var(--panel);
      box-shadow: 0 18px 46px var(--shadow);
    }}
    .stamp span {{ display: block; color: var(--muted); font-size: 12px; }}
    .stamp strong {{ display: block; margin-top: 4px; font-size: 16px; }}
    .controls {{
      display: grid;
      grid-template-columns: minmax(240px, 1fr) 150px 170px 150px 170px;
      gap: 12px;
      margin: 18px 0;
      padding: 14px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      box-shadow: 0 18px 46px var(--shadow);
    }}
    label {{ display: grid; gap: 7px; color: var(--muted); font-size: 11px; font-weight: 900; text-transform: uppercase; }}
    input, select {{
      width: 100%;
      min-height: 40px;
      color: var(--text);
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #0b1018;
      padding: 0 11px;
    }}
    input:focus, select:focus {{ outline: 2px solid rgba(0, 213, 255, 0.25); border-color: var(--cyan); }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(7, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 14px;
    }}
    .stat {{
      min-width: 0;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      padding: 14px;
    }}
    .stat span {{ display: block; color: var(--muted); font-size: 11px; font-weight: 900; text-transform: uppercase; }}
    .stat strong {{ display: block; margin-top: 7px; font-size: 22px; }}
    .stat small {{ display: block; margin-top: 3px; color: var(--faint); font-size: 11px; }}
    .stat:nth-child(1) strong {{ color: var(--cyan); }}
    .stat:nth-child(2) strong {{ color: var(--green); }}
    .stat:nth-child(3) strong {{ color: var(--green); }}
    .stat:nth-child(4) strong {{ color: var(--cyan); }}
    .stat:nth-child(5) strong {{ color: var(--yellow); }}
    .stat:nth-child(6) strong {{ color: var(--yellow); }}
    .stat:nth-child(7) strong {{ color: var(--orange); }}
    .panel {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      box-shadow: 0 18px 46px var(--shadow);
      overflow: hidden;
    }}
    .panel-head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 14px 16px;
      border-bottom: 1px solid var(--line-soft);
    }}
    .panel-head h2 {{ margin: 0; font-size: 17px; }}
    .hint {{ color: var(--faint); font-size: 12px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th {{
      position: sticky;
      top: 0;
      z-index: 1;
      background: #111722;
      color: var(--muted);
      text-align: left;
      font-size: 11px;
      text-transform: uppercase;
      padding: 11px 12px;
      border-bottom: 1px solid var(--line);
      white-space: nowrap;
    }}
    .sort-header {{
      appearance: none;
      border: 0;
      background: transparent;
      color: inherit;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 0;
      font: inherit;
      font-weight: 900;
      text-align: left;
      text-transform: uppercase;
    }}
    .sort-header:hover, .sort-header.active {{ color: var(--text); }}
    .sort-mark {{
      min-width: 28px;
      color: var(--cyan);
      font-size: 10px;
      font-weight: 900;
    }}
    td {{ padding: 12px; border-bottom: 1px solid var(--line-soft); vertical-align: middle; }}
    tbody tr:hover {{ background: rgba(0, 213, 255, 0.045); }}
    .sym strong {{ display: block; font-size: 14px; }}
    .sym span {{ display: block; margin-top: 3px; color: var(--faint); font-size: 12px; }}
    .pill {{
      display: inline-flex;
      align-items: center;
      height: 24px;
      padding: 0 8px;
      border-radius: 6px;
      border: 1px solid var(--line);
      font-size: 11px;
      font-weight: 900;
    }}
    .tier-highest {{ color: var(--green); border-color: rgba(110, 241, 149, 0.38); }}
    .tier-high {{ color: var(--cyan); border-color: rgba(0, 213, 255, 0.38); }}
    .tier-medium {{ color: var(--yellow); border-color: rgba(255, 209, 102, 0.42); }}
    .ret-pos {{ color: var(--green); font-weight: 900; }}
    .ret-neg {{ color: var(--red); font-weight: 900; }}
    .ret-flat {{ color: var(--yellow); font-weight: 900; }}
    .money {{ font-variant-numeric: tabular-nums; white-space: nowrap; }}
    .report-link {{ color: var(--cyan); text-decoration: none; font-weight: 800; }}
    .empty {{ padding: 24px; color: var(--muted); }}
    @media (max-width: 1180px) {{
      .stats {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
    }}
    @media (max-width: 980px) {{
      .top, .controls, .stats {{ grid-template-columns: 1fr; }}
      h1 {{ font-size: 27px; }}
      .table-wrap {{ overflow-x: auto; }}
      table {{ min-width: 1080px; }}
    }}
  </style>
</head>
<body>
  <main class="shell">
    <header class="top">
      <div>
        <p class="eyebrow">Past report performance</p>
        <h1>Past Suggestions Performance</h1>
        <p class="subline">Tracks saved MEDIUM, HIGH, and HIGHEST scanner cards, compares suggestion-time price with latest local database CMP, and groups repeated appearances by stock and pattern inside the selected window.</p>
      </div>
      <div class="stamp">
        <span>Generated</span>
        <strong>{html.escape(generated)}</strong>
        <span>CMP uses latest local DB close.</span>
      </div>
    </header>

    <section class="controls" aria-label="Dashboard controls">
      <label>Search <input id="search" type="search" placeholder="Symbol, company, pattern"></label>
      <label>Days
        <select id="days">
          <option value="30">30 days</option>
          <option value="60">60 days</option>
          <option value="90">90 days</option>
          <option value="180">180 days</option>
          <option value="365">365 days</option>
          <option value="all">All loaded</option>
        </select>
      </label>
      <label>Sector
        <select id="sector">
          <option value="all">All sectors</option>
        </select>
      </label>
      <label>Tier
        <select id="tier">
          <option value="all">Medium + High + Highest</option>
          <option value="HIGHEST">Highest only</option>
          <option value="HIGH">High only</option>
          <option value="MEDIUM">Medium only</option>
        </select>
      </label>
      <label>Sort
        <select id="sort">
          <option value="date_desc">Newest first</option>
          <option value="date_asc">Oldest first</option>
          <option value="return_desc">Best % change</option>
          <option value="return_asc">Worst % change</option>
          <option value="symbol_asc">Stock A-Z</option>
          <option value="symbol_desc">Stock Z-A</option>
          <option value="sector_asc">Sector A-Z</option>
          <option value="sector_desc">Sector Z-A</option>
          <option value="tier_desc">Highest tier first</option>
          <option value="tier_asc">Medium tier first</option>
          <option value="pattern_asc">Pattern A-Z</option>
          <option value="pattern_desc">Pattern Z-A</option>
          <option value="price_desc">Price then high-low</option>
          <option value="price_asc">Price then low-high</option>
          <option value="cmp_desc">CMP high-low</option>
          <option value="cmp_asc">CMP low-high</option>
          <option value="mentions_desc">Most mentions</option>
          <option value="mentions_asc">Fewest mentions</option>
        </select>
      </label>
    </section>

    <section class="stats" aria-label="Summary">
      <div class="stat"><span>Ideas shown</span><strong id="statIdeas">0</strong></div>
      <div class="stat"><span>Overall success</span><strong id="statOverallSuccess">N/A</strong><small id="statOverallDetail">0/0 positive</small></div>
      <div class="stat"><span>HIGHEST success</span><strong id="statHighestSuccess">N/A</strong><small id="statHighestDetail">0/0 positive</small></div>
      <div class="stat"><span>HIGH success</span><strong id="statHighSuccess">N/A</strong><small id="statHighDetail">0/0 positive</small></div>
      <div class="stat"><span>MEDIUM success</span><strong id="statMediumSuccess">N/A</strong><small id="statMediumDetail">0/0 positive</small></div>
      <div class="stat"><span>Avg % change</span><strong id="statAverage">N/A</strong></div>
      <div class="stat"><span>Best % change</span><strong id="statBest">N/A</strong></div>
    </section>

    <section class="panel">
      <div class="panel-head">
        <h2>Recommendation Table</h2>
        <span class="hint" id="tableHint">Default view: {int(default_days)} days</span>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th><button type="button" class="sort-header" data-sort-default="symbol_asc" data-sort-asc="symbol_asc" data-sort-desc="symbol_desc">Stock <span class="sort-mark" data-sort-mark></span></button></th>
              <th><button type="button" class="sort-header" data-sort-default="sector_asc" data-sort-asc="sector_asc" data-sort-desc="sector_desc">Sector <span class="sort-mark" data-sort-mark></span></button></th>
              <th><button type="button" class="sort-header" data-sort-default="tier_desc" data-sort-asc="tier_asc" data-sort-desc="tier_desc">Tier <span class="sort-mark" data-sort-mark></span></button></th>
              <th><button type="button" class="sort-header" data-sort-default="pattern_asc" data-sort-asc="pattern_asc" data-sort-desc="pattern_desc">Pattern <span class="sort-mark" data-sort-mark></span></button></th>
              <th><button type="button" class="sort-header" data-sort-default="date_desc" data-sort-asc="date_asc" data-sort-desc="date_desc">Recommended <span class="sort-mark" data-sort-mark></span></button></th>
              <th><button type="button" class="sort-header" data-sort-default="price_desc" data-sort-asc="price_asc" data-sort-desc="price_desc">Price then <span class="sort-mark" data-sort-mark></span></button></th>
              <th><button type="button" class="sort-header" data-sort-default="cmp_desc" data-sort-asc="cmp_asc" data-sort-desc="cmp_desc">CMP today <span class="sort-mark" data-sort-mark></span></button></th>
              <th><button type="button" class="sort-header" data-sort-default="return_desc" data-sort-asc="return_asc" data-sort-desc="return_desc">% Change <span class="sort-mark" data-sort-mark></span></button></th>
              <th><button type="button" class="sort-header" data-sort-default="mentions_desc" data-sort-asc="mentions_asc" data-sort-desc="mentions_desc">Mentions <span class="sort-mark" data-sort-mark></span></button></th>
              <th>Levels</th>
              <th>Report</th>
            </tr>
          </thead>
          <tbody id="rows"></tbody>
        </table>
        <div class="empty" id="empty" hidden>No MEDIUM / HIGH / HIGHEST suggestions found for this selection.</div>
      </div>
    </section>
  </main>

  <script id="pickData" type="application/json">{json_payload}</script>
  <script>
    const rawRows = JSON.parse(document.getElementById("pickData").textContent || "[]");
    const controls = {{
      search: document.getElementById("search"),
      days: document.getElementById("days"),
      sector: document.getElementById("sector"),
      tier: document.getElementById("tier"),
      sort: document.getElementById("sort"),
    }};
    controls.days.value = "{int(default_days)}";
    const sortButtons = Array.from(document.querySelectorAll(".sort-header"));

    const el = {{
      rows: document.getElementById("rows"),
      empty: document.getElementById("empty"),
      hint: document.getElementById("tableHint"),
      ideas: document.getElementById("statIdeas"),
      overallSuccess: document.getElementById("statOverallSuccess"),
      overallDetail: document.getElementById("statOverallDetail"),
      highestSuccess: document.getElementById("statHighestSuccess"),
      highestDetail: document.getElementById("statHighestDetail"),
      highSuccess: document.getElementById("statHighSuccess"),
      highDetail: document.getElementById("statHighDetail"),
      mediumSuccess: document.getElementById("statMediumSuccess"),
      mediumDetail: document.getElementById("statMediumDetail"),
      average: document.getElementById("statAverage"),
      best: document.getElementById("statBest"),
    }};

    function esc(value) {{
      return String(value ?? "").replace(/[&<>"']/g, (ch) => ({{
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
      }}[ch]));
    }}

    function pct(value) {{
      return typeof value === "number" && Number.isFinite(value) ? value : null;
    }}

    function populateSectorOptions() {{
      const sectors = Array.from(new Set(rawRows.map((row) => row.sector || "UNKNOWN"))).sort((a, b) => a.localeCompare(b));
      for (const sector of sectors) {{
        if (!sector || sector === "UNKNOWN") continue;
        const option = document.createElement("option");
        option.value = sector;
        option.textContent = sector;
        controls.sector.appendChild(option);
      }}
    }}

    function textCompare(a, b, key, direction) {{
      const result = String(a[key] ?? "").localeCompare(String(b[key] ?? ""));
      return direction === "asc" ? result : -result;
    }}

    function numberCompare(a, b, key, direction) {{
      const av = pct(a[key]);
      const bv = pct(b[key]);
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      return direction === "asc" ? av - bv : bv - av;
    }}

    function dateCompare(a, b, direction) {{
      const av = new Date(a.recommendedAt).getTime();
      const bv = new Date(b.recommendedAt).getTime();
      return direction === "asc" ? av - bv : bv - av;
    }}

    function tierValue(row) {{
      return row.tier === "HIGHEST" ? 3 : row.tier === "HIGH" ? 2 : row.tier === "MEDIUM" ? 1 : 0;
    }}

    function sortRows(rows) {{
      const sort = controls.sort.value;
      rows.sort((a, b) => {{
        if (sort === "return_desc") return numberCompare(a, b, "changePct", "desc");
        if (sort === "return_asc") return numberCompare(a, b, "changePct", "asc");
        if (sort === "symbol" || sort === "symbol_asc") return textCompare(a, b, "symbol", "asc");
        if (sort === "symbol_desc") return textCompare(a, b, "symbol", "desc");
        if (sort === "sector_asc") return textCompare(a, b, "sector", "asc");
        if (sort === "sector_desc") return textCompare(a, b, "sector", "desc");
        if (sort === "tier_desc") return tierValue(b) - tierValue(a) || textCompare(a, b, "symbol", "asc");
        if (sort === "tier_asc") return tierValue(a) - tierValue(b) || textCompare(a, b, "symbol", "asc");
        if (sort === "pattern_asc") return textCompare(a, b, "pattern", "asc");
        if (sort === "pattern_desc") return textCompare(a, b, "pattern", "desc");
        if (sort === "price_desc") return numberCompare(a, b, "priceThen", "desc");
        if (sort === "price_asc") return numberCompare(a, b, "priceThen", "asc");
        if (sort === "cmp_desc") return numberCompare(a, b, "cmpToday", "desc");
        if (sort === "cmp_asc") return numberCompare(a, b, "cmpToday", "asc");
        if (sort === "mentions_desc") return numberCompare(a, b, "mentions", "desc");
        if (sort === "mentions_asc") return numberCompare(a, b, "mentions", "asc");
        if (sort === "date_asc") return dateCompare(a, b, "asc");
        return dateCompare(a, b, "desc");
      }});
      return rows;
    }}

    function updateSortHeaders() {{
      const current = controls.sort.value;
      for (const button of sortButtons) {{
        const ascending = current === button.dataset.sortAsc;
        const descending = current === button.dataset.sortDesc;
        const active = ascending || descending;
        button.classList.toggle("active", active);
        button.setAttribute("aria-pressed", active ? "true" : "false");
        const mark = button.querySelector("[data-sort-mark]");
        if (mark) mark.textContent = active ? (ascending ? "ASC" : "DESC") : "";
      }}
    }}

    function groupedRows() {{
      const query = controls.search.value.trim().toLowerCase();
      const dayLimit = controls.days.value;
      const sector = controls.sector.value;
      const tier = controls.tier.value;
      const filtered = rawRows.filter((row) => {{
        if (dayLimit !== "all" && Number(row.daysAgo) > Number(dayLimit)) return false;
        if (sector !== "all" && row.sector !== sector) return false;
        if (tier !== "all" && row.tier !== tier) return false;
        if (!query) return true;
        return [row.symbol, row.companyName, row.sector, row.pattern, row.timeframe].join(" ").toLowerCase().includes(query);
      }}).sort((a, b) => new Date(a.recommendedAt) - new Date(b.recommendedAt));

      const groups = new Map();
      for (const row of filtered) {{
        const key = `${{row.symbol}}|${{row.pattern}}`;
        if (!groups.has(key)) {{
          groups.set(key, {{ ...row, mentions: 1, latestDate: row.recommendedDate }});
        }} else {{
          const existing = groups.get(key);
          existing.mentions += 1;
          existing.latestDate = row.recommendedDate;
          if (row.tier === "HIGHEST") existing.tier = "HIGHEST";
          else if (row.tier === "HIGH" && existing.tier !== "HIGHEST") existing.tier = "HIGH";
          if ((!existing.sector || existing.sector === "UNKNOWN") && row.sector) existing.sector = row.sector;
        }}
      }}
      return sortRows(Array.from(groups.values()));
    }}

    function successStats(rows, tier) {{
      const scoped = tier ? rows.filter((row) => row.tier === tier) : rows;
      const measured = scoped.map((row) => pct(row.changePct)).filter((value) => value != null);
      return {{
        winners: measured.filter((value) => value > 0).length,
        total: measured.length,
      }};
    }}

    function setSuccessStat(valueEl, detailEl, stats) {{
      valueEl.textContent = stats.total ? `${{((stats.winners / stats.total) * 100).toFixed(1)}}%` : "N/A";
      detailEl.textContent = stats.total ? `${{stats.winners}}/${{stats.total}} positive` : "no CMP data";
    }}

    function render() {{
      const rows = groupedRows();
      el.empty.hidden = rows.length !== 0;
      el.rows.innerHTML = rows.map((row) => {{
        const ret = pct(row.changePct);
        const retClass = ret == null ? "ret-flat" : ret > 0 ? "ret-pos" : ret < 0 ? "ret-neg" : "ret-flat";
        const tierClass = row.tier === "HIGHEST" ? "tier-highest" : row.tier === "HIGH" ? "tier-high" : "tier-medium";
        const levels = `Entry ${{row.entryText}}<br>Target ${{row.targetText}}<br>Stop ${{row.stopText}}`;
        return `<tr>
          <td class="sym"><strong>${{esc(row.symbol)}}</strong><span>${{esc(row.companyName)}}</span></td>
          <td>${{esc(row.sector || "UNKNOWN")}}</td>
          <td><span class="pill ${{tierClass}}">${{esc(row.tier)}}</span></td>
          <td>${{esc(row.pattern)}}<br><span class="hint">${{esc(row.timeframe)}}</span></td>
          <td>${{esc(row.recommendedDate)}}<br><span class="hint">${{row.daysAgo}} days ago</span></td>
          <td class="money">${{esc(row.priceThenText)}}</td>
          <td class="money">${{esc(row.cmpTodayText)}}<br><span class="hint">${{esc(row.cmpDate || "latest DB")}}</span></td>
          <td class="${{retClass}}">${{esc(row.changePctText)}}</td>
          <td>${{row.mentions}}<br><span class="hint">latest ${{esc(row.latestDate)}}</span></td>
          <td class="money">${{levels}}</td>
          <td><a class="report-link" href="${{esc(row.reportHref)}}" target="_blank">Open</a></td>
        </tr>`;
      }}).join("");

      const returns = rows.map((row) => pct(row.changePct)).filter((value) => value != null);
      const avg = returns.length ? returns.reduce((acc, value) => acc + value, 0) / returns.length : null;
      const best = returns.length ? Math.max(...returns) : null;
      el.ideas.textContent = String(rows.length);
      setSuccessStat(el.overallSuccess, el.overallDetail, successStats(rows));
      setSuccessStat(el.highestSuccess, el.highestDetail, successStats(rows, "HIGHEST"));
      setSuccessStat(el.highSuccess, el.highDetail, successStats(rows, "HIGH"));
      setSuccessStat(el.mediumSuccess, el.mediumDetail, successStats(rows, "MEDIUM"));
      el.average.textContent = avg == null ? "N/A" : `${{avg >= 0 ? "+" : ""}}${{avg.toFixed(2)}}%`;
      el.best.textContent = best == null ? "N/A" : `${{best >= 0 ? "+" : ""}}${{best.toFixed(2)}}%`;
      updateSortHeaders();
      el.hint.textContent = controls.days.value === "all" ? "Showing all loaded report cards" : `Showing first mention per stock + pattern in last ${{controls.days.value}} days`;
    }}

    populateSectorOptions();
    for (const control of Object.values(controls)) control.addEventListener("input", render);
    for (const button of sortButtons) {{
      button.addEventListener("click", () => {{
        const primary = button.dataset.sortDefault || button.dataset.sortDesc || button.dataset.sortAsc;
        const alternate = primary === button.dataset.sortAsc ? button.dataset.sortDesc : button.dataset.sortAsc;
        controls.sort.value = controls.sort.value === primary && alternate ? alternate : primary;
        render();
      }});
    }}
    render();
  </script>
</body>
</html>
"""


def _parse_report(path: Path, report_dt: datetime, *, output_root: Path) -> list[dict[str, Any]]:
    parser = _ReportParser(report_dt=report_dt, report_path=path, output_root=output_root)
    parser.feed(path.read_text(encoding="utf-8", errors="ignore"))
    return parser.rows


class _ReportParser(HTMLParser):
    def __init__(self, *, report_dt: datetime, report_path: Path, output_root: Path) -> None:
        super().__init__(convert_charrefs=False)
        self.report_dt = report_dt
        self.report_path = report_path
        self.output_root = output_root
        self.rows: list[dict[str, Any]] = []
        self.current: dict[str, Any] | None = None
        self.capture: str | None = None
        self.capture_parts: list[str] = []
        self.metric_label: str | None = None
        self.metric_value: str | None = None
        self.in_metric = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = {key: value or "" for key, value in attrs}
        classes = set(attrs_map.get("class", "").split())
        if tag == "article" and "result-card" in classes:
            self.current = {
                "tier": attrs_map.get("data-tier-card", "").upper(),
                "pattern": html.unescape(attrs_map.get("data-pattern", "")),
                "sector": html.unescape(attrs_map.get("data-sector", "")) or "UNKNOWN",
                "metrics": {},
                "recommended_at": self.report_dt,
                "report_name": self.report_path.name,
                "report_href": self._href(),
            }
            return
        if self.current is None:
            return
        if tag == "span" and "symbol" in classes:
            self._begin_capture("symbol")
        elif tag == "div" and "metric" in classes:
            self.in_metric = True
            self.metric_label = None
            self.metric_value = None
        elif self.in_metric and tag == "span" and "label" in classes:
            self._begin_capture("metric_label")
        elif self.in_metric and tag == "span" and "value" in classes:
            self._begin_capture("metric_value")
        elif tag == "script" and "tc-payload" in classes:
            self._begin_capture("payload")

    def handle_data(self, data: str) -> None:
        if self.capture:
            self.capture_parts.append(data)

    def handle_entityref(self, name: str) -> None:
        if self.capture:
            self.capture_parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self.capture:
            self.capture_parts.append(f"&#{name};")

    def handle_endtag(self, tag: str) -> None:
        if self.current is None:
            return
        if self.capture and tag in {"span", "script"}:
            value = html.unescape("".join(self.capture_parts)).strip()
            if self.capture == "metric_label":
                self.metric_label = value
            elif self.capture == "metric_value":
                self.metric_value = value
            elif self.capture == "payload":
                self._apply_payload(value)
            else:
                self.current[self.capture] = value
            self.capture = None
            self.capture_parts = []
        if tag == "div" and self.in_metric:
            if self.metric_label and self.metric_value:
                self.current["metrics"][self.metric_label] = self.metric_value
            self.in_metric = False
        if tag == "article":
            self._finish_current()

    def _begin_capture(self, name: str) -> None:
        self.capture = name
        self.capture_parts = []

    def _apply_payload(self, raw: str) -> None:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return
        self.current["symbol"] = self.current.get("symbol") or payload.get("symbol")
        self.current["company_name"] = payload.get("company_name") or payload.get("symbol")
        self.current["timeframe"] = str(payload.get("timeframe") or "").lower()
        pattern = payload.get("pattern") or {}
        if isinstance(pattern, dict):
            self.current["pattern"] = self.current.get("pattern") or pattern.get("type")
        trade = payload.get("trade_plan") or {}
        if isinstance(trade, dict):
            self.current["entry"] = trade.get("entry")
            self.current["target"] = trade.get("target")
            self.current["stop_loss"] = trade.get("stop")
        candles = payload.get("candles") or []
        if candles and self.current.get("price_then") is None:
            self.current["price_then"] = candles[-1].get("close")

    def _finish_current(self) -> None:
        row = self.current or {}
        self.current = None
        if row.get("tier") not in REPORT_TIERS:
            return
        metrics = row.get("metrics") or {}
        row["symbol"] = str(row.get("symbol") or "").upper()
        if not row["symbol"]:
            return
        row["company_name"] = row.get("company_name") or row["symbol"]
        row["sector"] = str(row.get("sector") or "UNKNOWN").strip() or "UNKNOWN"
        row["timeframe"] = row.get("timeframe") or "daily"
        row["price_then"] = _parse_money(metrics.get("CMP")) or _number(row.get("price_then"))
        row["entry"] = _number(row.get("entry")) or _parse_money(metrics.get("Entry"))
        row["target"] = _number(row.get("target")) or _parse_money(metrics.get("Target"))
        stop_text = metrics.get("Stop / R:R") or metrics.get("Stop")
        row["stop_loss"] = _number(row.get("stop_loss")) or _parse_money(stop_text)
        self.rows.append(row)

    def _href(self) -> str:
        try:
            rel = self.report_path.relative_to(self.output_root).as_posix()
        except ValueError:
            rel = self.report_path.name
        return f"/output/{rel}"


def _latest_closes(symbols: list[str], *, db_path: Path) -> dict[str, dict[str, Any]]:
    if not symbols:
        return {}
    conn = storage.connect(db_path)
    try:
        out: dict[str, dict[str, Any]] = {}
        for start in range(0, len(symbols), 800):
            batch = [symbol.upper() for symbol in symbols[start : start + 800]]
            placeholders = ",".join("?" for _ in batch)
            frame = storage.query_frame(
                conn,
                f"""
                WITH ranked AS (
                    SELECT symbol, date, close,
                           ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY date DESC) AS rn
                    FROM ohlcv_daily
                    WHERE symbol IN ({placeholders})
                )
                SELECT symbol, date, close
                FROM ranked
                WHERE rn = 1
                """,
                batch,
            )
            for row in frame.itertuples(index=False):
                out[str(row.symbol).upper()] = {"date": str(row.date), "close": _number(row.close)}
        return out
    finally:
        conn.close()


def _is_report_candidate(path: Path) -> bool:
    name = path.name.lower()
    if path.suffix.lower() != ".html":
        return False
    if name.startswith(SKIP_REPORT_PREFIXES):
        return False
    return not any(part in name for part in SKIP_REPORT_NAME_PARTS)


def _report_datetime(path: Path) -> datetime:
    match = re.search(r"(20\d{6})_(\d{6})", path.name)
    if match:
        return datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S")
    match = re.search(r"(20\d{6})", path.name)
    if match:
        return datetime.strptime(match.group(1), "%Y%m%d")
    return datetime.fromtimestamp(path.stat().st_mtime)


def _parse_money(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value)
    match = re.search(r"-?\d[\d,]*(?:\.\d+)?", text)
    if not match:
        return None
    return _number(match.group(0).replace(",", ""))


def _pct_change(start: float | None, end: float | None) -> float | None:
    if start is None or end is None or start <= 0:
        return None
    return round((end / start - 1.0) * 100.0, 2)


def _money(value: float | None) -> str:
    number = _number(value)
    if number is None:
        return "N/A"
    return "Rs." + f"{number:,.2f}".rstrip("0").rstrip(".")


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None
