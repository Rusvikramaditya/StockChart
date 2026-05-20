"""Analyze generated backtest HTML reports for tuning decisions."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable


CORE_UNIVERSES = {"nifty500", "small_mid_liquid"}


@dataclass(frozen=True)
class AnalysisRules:
    min_bucket_trades: int = 30
    min_pattern_trades: int = 30
    meaningful_win_margin: float = 5.0


def parse_report(path: str | Path) -> dict:
    path = Path(path)
    html = path.read_text(encoding="utf-8")
    universe = _extract_universe(html)
    summary = _extract_summary_stats(html)
    sections = {}
    for title, body in _section_html(html):
        sections[_normalise_title(title)] = _parse_table(body)
    return {
        "path": str(path),
        "universe": universe,
        "summary": summary,
        "by_pattern": _coerce_metric_rows(sections.get("summary_by_pattern", []), "pattern"),
        "conviction_validation": _coerce_metric_rows(sections.get("conviction_tier_validation", []), "bucket"),
        "quality_validation": _coerce_metric_rows(sections.get("quality_score_validation", []), "bucket"),
        "filter_impact": _coerce_filter_rows(sections.get("filter_impact", [])),
        "stack_validation": _coerce_metric_rows(sections.get("stack_validation", []), "bucket"),
        "trait_diagnostics": _coerce_trait_rows(sections.get("pattern_trait_diagnostics", [])),
    }


def analyze_reports(paths: Iterable[str | Path], rules: AnalysisRules | None = None) -> dict:
    rules = rules or AnalysisRules()
    reports = [parse_report(path) for path in paths]
    return {
        "reports": reports,
        "conviction": _analyze_conviction(reports, rules),
        "quality_score": _analyze_quality_score(reports, rules),
        "stack_bonus": _analyze_stack_bonus(reports, rules),
        "pattern_candidates": _analyze_pattern_candidates(reports, rules),
        "trait_diagnostics": _summarize_trait_diagnostics(reports, rules),
        "filter_impact": _summarize_filter_impact(reports),
        "rules": {
            "min_bucket_trades": rules.min_bucket_trades,
            "min_pattern_trades": rules.min_pattern_trades,
            "meaningful_win_margin": rules.meaningful_win_margin,
            "core_universes": sorted(CORE_UNIVERSES),
        },
    }


def write_analysis(
    paths: Iterable[str | Path],
    output_path: str | Path,
    *,
    json_output_path: str | Path | None = None,
    rules: AnalysisRules | None = None,
) -> dict:
    analysis = analyze_reports(paths, rules)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_markdown(analysis), encoding="utf-8")
    if json_output_path is not None:
        json_output = Path(json_output_path)
        json_output.parent.mkdir(parents=True, exist_ok=True)
        json_output.write_text(json.dumps(analysis, indent=2), encoding="utf-8")
    return analysis


def render_markdown(analysis: dict) -> str:
    lines = [
        "# Phase 8 Backtest Tuning Analysis",
        "",
        "## Reports",
        "",
    ]
    for report in analysis["reports"]:
        summary = report["summary"]
        lines.append(
            f"- {report['universe']}: trades={summary.get('trades', 0)}, "
            f"win_rate={summary.get('win_rate', 0)}%, "
            f"profit_factor={summary.get('profit_factor', 0)}, "
            f"report={report['path']}"
        )

    lines.extend(["", "## Conviction Validation", ""])
    for row in analysis["conviction"]["by_universe"]:
        low_bucket = row.get("low_bucket") or "50-69"
        lines.append(
            f"- {row['universe']}: {row['status']} "
            f"(90+ win={row.get('high_win_rate')}%, {low_bucket} win={row.get('low_win_rate')}%, "
            f"margin={row.get('margin')})"
        )
    lines.append(f"- Recommendation: {analysis['conviction']['recommendation']}")

    lines.extend(["", "## Quality Score Validation", ""])
    for row in analysis["quality_score"]["by_universe"]:
        lines.append(
            f"- {row['universe']}: {row['status']} "
            f"(80+ win={row.get('high_win_rate')}%, 50-64 win={row.get('low_win_rate')}%, "
            f"margin={row.get('margin')})"
        )
    lines.append(f"- Recommendation: {analysis['quality_score']['recommendation']}")

    lines.extend(["", "## Stack Bonus Validation", ""])
    for row in analysis["stack_bonus"]["by_universe"]:
        lines.append(
            f"- {row['universe']}: {row['status']} "
            f"(1-pattern win={row.get('one_pattern_win_rate')}%, "
            f"3-stacked win={row.get('three_stacked_win_rate')}%, "
            f"margin={row.get('margin')})"
        )
    lines.append(f"- Recommendation: {analysis['stack_bonus']['recommendation']}")

    lines.extend(["", "## Pattern Removal Candidates", ""])
    candidates = analysis["pattern_candidates"]["remove"]
    if candidates:
        for pattern, evidence in candidates.items():
            details = "; ".join(
                f"{row['universe']} trades={row['trades']} win={row['win_rate']}% pf={row['profit_factor']}"
                for row in evidence
            )
            lines.append(f"- {pattern}: {details}")
    else:
        lines.append("- None from current rules.")

    lines.extend(["", "## Pattern Trait Diagnostics", ""])
    traits = analysis["trait_diagnostics"]["candidates"]
    if traits:
        for row in traits:
            lines.append(
                f"- {row['universe']} {row['pattern']} {row['trait']}: "
                f"trades={row['trades']} wins/losses={row['wins']}/{row['losses']} "
                f"win_avg={row['win_avg']} loss_avg={row['loss_avg']} spread={row['spread']}"
            )
    else:
        lines.append("- Insufficient winner/loss trait samples from current rules.")

    lines.extend(["", "## Filter Impact", ""])
    for filter_name, rows in analysis["filter_impact"].items():
        details = "; ".join(
            f"{row['universe']} improvement={row['improvement']}pp"
            for row in rows
        )
        lines.append(f"- {filter_name}: {details}")

    lines.append("")
    return "\n".join(lines)


def _extract_universe(html: str) -> str:
    match = re.search(r"Universe:\s*([^|<]+)", html)
    return unescape(match.group(1)).strip() if match else "unknown"


def _extract_summary_stats(html: str) -> dict:
    stats = {}
    for label, value in re.findall(r'<span class="muted">([^<]+)</span><b>([^<]+)</b>', html):
        key = _normalise_title(label)
        stats[key] = _number(value)
    return stats


def _section_html(html: str) -> list[tuple[str, str]]:
    return [
        (unescape(title).strip(), body)
        for title, body in re.findall(r"<h2>(.*?)</h2><section>(.*?)</section>", html, flags=re.S)
    ]


class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows: list[list[str]] = []
        self.current_row: list[str] | None = None
        self.current_cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.current_row = []
        elif tag in {"th", "td"} and self.current_row is not None:
            self.current_cell = []

    def handle_data(self, data):
        if self.current_cell is not None:
            self.current_cell.append(data)

    def handle_endtag(self, tag):
        if tag in {"th", "td"} and self.current_cell is not None and self.current_row is not None:
            self.current_row.append(" ".join("".join(self.current_cell).split()))
            self.current_cell = None
        elif tag == "tr" and self.current_row is not None:
            if self.current_row:
                self.rows.append(self.current_row)
            self.current_row = None


def _parse_table(html: str) -> list[dict[str, str]]:
    parser = _TableParser()
    parser.feed(html)
    if len(parser.rows) < 2:
        return []
    headers = [_normalise_title(cell) for cell in parser.rows[0]]
    return [
        {headers[idx]: cell for idx, cell in enumerate(row[: len(headers)])}
        for row in parser.rows[1:]
    ]


def _coerce_metric_rows(rows: list[dict[str, str]], group_key: str) -> list[dict]:
    parsed = []
    for row in rows:
        item = {group_key: row.get(group_key, "")}
        for key, value in row.items():
            if key != group_key:
                item[_metric_key(key)] = _number(value)
        parsed.append(item)
    return parsed


def _coerce_filter_rows(rows: list[dict[str, str]]) -> list[dict]:
    parsed = []
    for row in rows:
        with_trades, with_win_rate = _split_count_rate(row.get("with_filter", "0 / 0"))
        without_trades, without_win_rate = _split_count_rate(row.get("without", "0 / 0"))
        parsed.append(
            {
                "filter": row.get("filter", ""),
                "with_trades": with_trades,
                "with_win_rate": with_win_rate,
                "without_trades": without_trades,
                "without_win_rate": without_win_rate,
                "improvement": _number(row.get("improvement", 0)),
            }
        )
    return parsed


def _coerce_trait_rows(rows: list[dict[str, str]]) -> list[dict]:
    parsed = []
    for row in rows:
        wins, losses = _split_count_rate(row.get("wins_losses", "0 / 0"))
        trait = row.get("trait", "")
        if not _is_retune_trait_name(trait):
            continue
        parsed.append(
            {
                "pattern": row.get("pattern", ""),
                "trait": trait,
                "trades": int(_number(row.get("trades", 0))),
                "wins": wins,
                "losses": int(losses),
                "win_avg": _number(row.get("win_avg", 0)),
                "loss_avg": _number(row.get("loss_avg", 0)),
                "spread": _number(row.get("spread", 0)),
            }
        )
    return parsed


def _is_retune_trait_name(trait: str) -> bool:
    normalized = str(trait).lower()
    if normalized.endswith("_idx") or normalized.endswith("_index") or normalized in {"neckline", "pivot"}:
        return False
    return (
        normalized == "bars_in_pattern"
        or normalized.endswith("_pct")
        or "_pct_" in normalized
        or normalized.endswith("_ratio")
        or "_ratio_" in normalized
        or normalized.endswith("_count")
        or normalized.endswith("_change")
        or normalized in {"years", "volume_declining", "stage2"}
        or "touch" in normalized
    )


def _analyze_conviction(reports: list[dict], rules: AnalysisRules) -> dict:
    rows = []
    failing_core = []
    core_reports_present = False
    for report in reports:
        is_core = report["universe"] in CORE_UNIVERSES
        core_reports_present = core_reports_present or is_core
        high = _find_bucket(report["conviction_validation"], "90+")
        low = _first_sampled_bucket(report["conviction_validation"], rules, ["50-69", "70-89"])
        status = "INSUFFICIENT"
        margin = None
        if high and low and high["trades"] >= rules.min_bucket_trades and low["trades"] >= rules.min_bucket_trades:
            margin = round(high["win_rate"] - low["win_rate"], 2)
            status = "PASS" if margin >= rules.meaningful_win_margin else "FAIL"
        row = {
            "universe": report["universe"],
            "status": status,
            "high_trades": high.get("trades") if high else 0,
            "high_win_rate": high.get("win_rate") if high else None,
            "low_bucket": low.get("bucket") if low else None,
            "low_trades": low.get("trades") if low else 0,
            "low_win_rate": low.get("win_rate") if low else None,
            "margin": margin,
        }
        rows.append(row)
        if is_core and status != "PASS":
            failing_core.append(row)
    if not core_reports_present:
        recommendation = "insufficient core evidence"
    elif failing_core:
        recommendation = "retune conviction weights"
    else:
        recommendation = "keep conviction weights"
    return {"by_universe": rows, "recommendation": recommendation}


def _analyze_quality_score(reports: list[dict], rules: AnalysisRules) -> dict:
    rows = []
    failing_core = []
    gate_candidates = []
    core_reports_present = False
    for report in reports:
        is_core = report["universe"] in CORE_UNIVERSES
        core_reports_present = core_reports_present or is_core
        high = _find_bucket(report.get("quality_validation", []), "80+")
        low = _first_sampled_bucket(report.get("quality_validation", []), rules, ["65-79", "50-64", "<50"])
        status = "INSUFFICIENT"
        margin = None
        if high and low and high["trades"] >= rules.min_bucket_trades and low["trades"] >= rules.min_bucket_trades:
            margin = round(high["win_rate"] - low["win_rate"], 2)
            status = "PASS" if margin >= rules.meaningful_win_margin else "FAIL"
        elif high and high["trades"] >= rules.min_bucket_trades and not low:
            status = "GATED"
        row = {
            "universe": report["universe"],
            "status": status,
            "high_trades": high.get("trades") if high else 0,
            "high_win_rate": high.get("win_rate") if high else None,
            "low_bucket": low.get("bucket") if low else None,
            "low_trades": low.get("trades") if low else 0,
            "low_win_rate": low.get("win_rate") if low else None,
            "margin": margin,
        }
        rows.append(row)
        if is_core and status not in {"PASS", "GATED"}:
            failing_core.append(row)
        if is_core and status == "PASS" and low and low.get("profit_factor", 0) < 1.0:
            gate_candidates.append(row)
    if not core_reports_present:
        recommendation = "insufficient core evidence"
    elif failing_core:
        recommendation = "retune quality score traits"
    elif gate_candidates:
        recommendation = "raise minimum tradable quality score to 80"
    elif any(row["status"] == "GATED" for row in rows):
        recommendation = "keep minimum tradable quality score"
    else:
        recommendation = "keep quality score buckets"
    return {"by_universe": rows, "recommendation": recommendation}


def _analyze_stack_bonus(reports: list[dict], rules: AnalysisRules) -> dict:
    rows = []
    failing_core = []
    core_reports_present = False
    for report in reports:
        is_core = report["universe"] in CORE_UNIVERSES
        core_reports_present = core_reports_present or is_core
        one = _find_bucket(report["stack_validation"], "1 pattern")
        three = _find_bucket(report["stack_validation"], "3 stacked")
        status = "INSUFFICIENT"
        margin = None
        if one and three and one["trades"] >= rules.min_bucket_trades and three["trades"] >= rules.min_bucket_trades:
            margin = round(three["win_rate"] - one["win_rate"], 2)
            status = "PASS" if margin >= rules.meaningful_win_margin else "FAIL"
        row = {
            "universe": report["universe"],
            "status": status,
            "one_pattern_trades": one.get("trades") if one else 0,
            "one_pattern_win_rate": one.get("win_rate") if one else None,
            "three_stacked_trades": three.get("trades") if three else 0,
            "three_stacked_win_rate": three.get("win_rate") if three else None,
            "margin": margin,
        }
        rows.append(row)
        if is_core and status != "PASS":
            failing_core.append(row)
    if not core_reports_present:
        recommendation = "insufficient core evidence"
    elif failing_core:
        recommendation = "remove stack score bonus but keep stacked pattern visibility"
    else:
        recommendation = "keep stack score bonus"
    return {"by_universe": rows, "recommendation": recommendation}


def _analyze_pattern_candidates(reports: list[dict], rules: AnalysisRules) -> dict:
    remove: dict[str, list[dict]] = {}
    for report in reports:
        if report["universe"] not in CORE_UNIVERSES:
            continue
        for row in report["by_pattern"]:
            if row["trades"] < rules.min_pattern_trades:
                continue
            if row["win_rate"] < 50.0 or row["profit_factor"] < 1.0:
                remove.setdefault(row["pattern"], []).append(
                    {
                        "universe": report["universe"],
                        "trades": row["trades"],
                        "win_rate": row["win_rate"],
                        "profit_factor": row["profit_factor"],
                    }
                )
    return {"remove": remove}


def _summarize_filter_impact(reports: list[dict]) -> dict[str, list[dict]]:
    summary: dict[str, list[dict]] = {}
    for report in reports:
        for row in report["filter_impact"]:
            summary.setdefault(row["filter"], []).append(
                {
                    "universe": report["universe"],
                    "with_trades": row["with_trades"],
                    "without_trades": row["without_trades"],
                    "improvement": row["improvement"],
                }
            )
    return summary


def _summarize_trait_diagnostics(reports: list[dict], rules: AnalysisRules) -> dict:
    candidates = []
    min_side_samples = max(5, rules.min_bucket_trades // 3)
    for report in reports:
        for row in report.get("trait_diagnostics", []):
            if int(row.get("trades", 0)) < rules.min_pattern_trades:
                continue
            if int(row.get("wins", 0)) < min_side_samples or int(row.get("losses", 0)) < min_side_samples:
                continue
            candidates.append(
                {
                    "universe": report["universe"],
                    "pattern": row["pattern"],
                    "trait": row["trait"],
                    "trades": row["trades"],
                    "wins": row["wins"],
                    "losses": row["losses"],
                    "win_avg": row["win_avg"],
                    "loss_avg": row["loss_avg"],
                    "spread": row["spread"],
                }
            )
    candidates.sort(key=lambda row: abs(float(row["spread"])), reverse=True)
    return {"candidates": candidates[:12]}


def _find_bucket(rows: list[dict], bucket: str) -> dict | None:
    for row in rows:
        if row.get("bucket") == bucket:
            return row
    return None


def _first_sampled_bucket(rows: list[dict], rules: AnalysisRules, buckets: list[str]) -> dict | None:
    for bucket in buckets:
        row = _find_bucket(rows, bucket)
        if row and row.get("trades", 0) >= rules.min_bucket_trades:
            return row
    return None


def _split_count_rate(text: str) -> tuple[int, float]:
    match = re.search(r"([\d,]+)\s*/\s*([-+]?\d+(?:\.\d+)?)%?", str(text))
    if not match:
        return 0, 0.0
    return int(match.group(1).replace(",", "")), float(match.group(2))


def _number(value) -> int | float:
    text = str(value).replace("%", "").replace(",", "").strip()
    if not text:
        return 0
    try:
        number = float(text)
    except ValueError:
        return 0
    return int(number) if number.is_integer() else number


def _metric_key(key: str) -> str:
    return {
        "win": "win_rate",
        "pf": "profit_factor",
        "avg_win": "avg_win_pct",
        "avg_loss": "avg_loss_pct",
        "max_dd": "max_drawdown",
    }.get(key, key)


def _normalise_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
