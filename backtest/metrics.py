"""Backtest result metrics."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from math import sqrt
from typing import Iterable


@dataclass
class BacktestResult:
    trades: list[dict]
    universe: str = "nifty500"
    config: dict = field(default_factory=dict)

    @property
    def summary(self) -> dict:
        return _metrics_for(self.trades)

    @property
    def by_pattern(self) -> list[dict]:
        return _grouped_metrics(self.trades, "pattern")

    @property
    def by_tier(self) -> list[dict]:
        return _grouped_metrics(self.trades, "tier")

    @property
    def by_stack_count(self) -> list[dict]:
        rows = _grouped_metrics(self.trades, "stacked_count")
        return sorted(rows, key=lambda row: int(row["group"]))

    @property
    def filter_impact(self) -> list[dict]:
        rows = []
        for key in ("stage2", "volume", "sector_rs", "market_regime", "rsi", "multi_tf"):
            with_filter = [trade for trade in self.trades if _filter_passed(trade, key)]
            without_filter = [trade for trade in self.trades if not _filter_passed(trade, key)]
            with_metrics = _metrics_for(with_filter)
            without_metrics = _metrics_for(without_filter)
            rows.append(
                {
                    "filter": key,
                    "with_trades": with_metrics["trades"],
                    "with_win_rate": with_metrics["win_rate"],
                    "without_trades": without_metrics["trades"],
                    "without_win_rate": without_metrics["win_rate"],
                    "improvement": _round(with_metrics["win_rate"] - without_metrics["win_rate"]),
                }
            )
        return rows

    @property
    def monthly_returns(self) -> list[dict]:
        grouped: dict[str, list[float]] = defaultdict(list)
        for trade in self.trades:
            exit_date = str(trade.get("exit_date") or trade.get("entry_date") or "")
            month = exit_date[:7] if len(exit_date) >= 7 else "UNKNOWN"
            grouped[month].append(float(trade.get("return_pct", 0.0)))
        return [
            {"month": month, "return_pct": _round(sum(values)), "trades": len(values)}
            for month, values in sorted(grouped.items())
        ]

    @property
    def equity_curve(self) -> list[dict]:
        cumulative = 0.0
        points = []
        for trade in sorted(self.trades, key=lambda item: str(item.get("exit_date") or item.get("entry_date"))):
            cumulative += float(trade.get("return_pct", 0.0))
            points.append(
                {
                    "date": str(trade.get("exit_date") or trade.get("entry_date")),
                    "equity": _round(cumulative),
                }
            )
        return points

    @property
    def conviction_validation(self) -> list[dict]:
        buckets = [
            ("90+", lambda score: score >= 90),
            ("70-89", lambda score: 70 <= score < 90),
            ("50-69", lambda score: 50 <= score < 70),
        ]
        rows = []
        for label, predicate in buckets:
            rows.append(_bucket_row(label, [t for t in self.trades if predicate(float(t.get("score", 0)))]))
        return rows

    @property
    def quality_validation(self) -> list[dict]:
        buckets = [
            ("80+", lambda quality: quality >= 80),
            ("65-79", lambda quality: 65 <= quality < 80),
            ("50-64", lambda quality: 50 <= quality < 65),
            ("<50", lambda quality: quality < 50),
        ]
        rows = []
        for label, predicate in buckets:
            rows.append(_bucket_row(label, [t for t in self.trades if predicate(_quality_score(t))]))
        return rows

    @property
    def stack_validation(self) -> list[dict]:
        rows = []
        for count in sorted({int(trade.get("stacked_count", 1)) for trade in self.trades} or {1}):
            rows.append(
                _bucket_row(
                    f"{count} pattern" if count == 1 else f"{count} stacked",
                    [trade for trade in self.trades if int(trade.get("stacked_count", 1)) == count],
                )
            )
        return rows


def _metrics_for(trades: Iterable[dict]) -> dict:
    rows = list(trades)
    wins = [row for row in rows if row.get("result") == "WIN"]
    losses = [row for row in rows if row.get("result") == "LOSS"]
    closed = wins + losses
    returns = [float(row.get("return_pct", 0.0)) for row in rows]
    total_win = sum(max(value, 0.0) for value in returns)
    total_loss = abs(sum(min(value, 0.0) for value in returns))
    win_rate = len(wins) / len(closed) * 100.0 if closed else 0.0
    avg_win = sum(float(row.get("return_pct", 0.0)) for row in wins) / len(wins) if wins else 0.0
    avg_loss = sum(float(row.get("return_pct", 0.0)) for row in losses) / len(losses) if losses else 0.0
    loss_rate = 100.0 - win_rate if closed else 0.0
    expectancy = (win_rate / 100.0 * avg_win) + (loss_rate / 100.0 * avg_loss)
    return {
        "trades": len(rows),
        "wins": len(wins),
        "losses": len(losses),
        "timeouts": sum(1 for row in rows if row.get("result") == "TIMEOUT"),
        "win_rate": _round(win_rate),
        "avg_win_pct": _round(avg_win),
        "avg_loss_pct": _round(avg_loss),
        "profit_factor": _round(total_win / total_loss) if total_loss > 0 else (999.0 if total_win > 0 else 0.0),
        "expectancy": _round(expectancy),
        "avg_holding_days": _round(sum(float(row.get("hold_days", 0)) for row in rows) / len(rows)) if rows else 0.0,
        "max_drawdown": _round(min((float(row.get("max_drawdown_pct", 0.0)) for row in rows), default=0.0)),
        "sharpe": _round(_sharpe(returns)),
        "max_consecutive_losses": _max_consecutive_losses(rows),
    }


def _grouped_metrics(trades: list[dict], key: str) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for trade in trades:
        grouped[str(trade.get(key, "UNKNOWN"))].append(trade)
    return [
        {"group": group, **_metrics_for(rows)}
        for group, rows in sorted(grouped.items())
    ]


def _filter_passed(trade: dict, key: str) -> bool:
    filters = trade.get("filters") or {}
    value = filters.get(key) or {}
    if key == "market_regime":
        return int(value.get("score", 0)) >= 3
    if key == "rsi":
        return "DIVERGENCE" not in str(value.get("status", "")).upper()
    return bool(value.get("passed"))


def _quality_score(trade: dict) -> float:
    value = trade.get("pattern_quality_score")
    if value is None:
        value = trade.get("pattern_confidence", 0)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _bucket_row(label: str, trades: list[dict]) -> dict:
    return {"bucket": label, **_metrics_for(trades)}


def _sharpe(returns: list[float]) -> float:
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
    std = variance ** 0.5
    return 0.0 if std == 0 else mean / std * sqrt(252)


def _max_consecutive_losses(trades: list[dict]) -> int:
    worst = 0
    current = 0
    for trade in sorted(trades, key=lambda item: str(item.get("exit_date") or item.get("entry_date"))):
        if trade.get("result") == "LOSS":
            current += 1
            worst = max(worst, current)
        else:
            current = 0
    return worst


def _round(value: float, digits: int = 2) -> float:
    return round(float(value), digits)
