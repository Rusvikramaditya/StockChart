"""Export Pattern Finder backtest trades as SkillOpt SearchQA items."""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from backtest.metrics import BacktestResult


PROMOTE = "PROMOTE"
REJECT = "REJECT"


@dataclass(frozen=True)
class SkillOptExportSummary:
    output_dir: Path
    total_items: int
    split_counts: dict[str, int]
    label_counts: dict[str, int]
    initial_skill_path: Path
    command_path: Path


def export_backtest_result(
    result: BacktestResult,
    output_dir: str | Path,
    *,
    min_promote_return_pct: float = 0.0,
    max_promote_drawdown_pct: float | None = None,
) -> SkillOptExportSummary:
    items = [
        build_skillopt_item(
            trade,
            universe=result.universe,
            index=idx,
            min_promote_return_pct=min_promote_return_pct,
            max_promote_drawdown_pct=max_promote_drawdown_pct,
        )
        for idx, trade in enumerate(result.trades, start=1)
    ]
    return write_skillopt_dataset(
        items,
        output_dir,
        manifest={
            "source": "Pattern Finder BacktestResult",
            "universe": result.universe,
            "backtest_config": result.config,
            "label_rule": label_rule_text(min_promote_return_pct, max_promote_drawdown_pct),
        },
    )


def build_skillopt_item(
    trade: dict,
    *,
    universe: str,
    index: int,
    min_promote_return_pct: float = 0.0,
    max_promote_drawdown_pct: float | None = None,
) -> dict:
    label = label_trade(
        trade,
        min_promote_return_pct=min_promote_return_pct,
        max_promote_drawdown_pct=max_promote_drawdown_pct,
    )
    item_id = _item_id(trade, universe, index)
    return {
        "id": item_id,
        "question": (
            "Using only the signal-time Pattern Finder snapshot, should this setup "
            "be promoted as a loud high-conviction alert? Answer exactly PROMOTE or REJECT."
        ),
        "context": build_signal_context(trade, universe=universe),
        "answers": [label],
        "metadata": {
            "universe": universe,
            "symbol": str(trade.get("symbol", "")).upper(),
            "signal_date": str(trade.get("signal_date", "")),
            "future_result": trade.get("result"),
            "future_return_pct": trade.get("return_pct"),
            "future_exit_date": trade.get("exit_date"),
            "future_max_drawdown_pct": trade.get("max_drawdown_pct"),
            "label_source": "backtest_future_outcome",
            "label_rule": label_rule_text(min_promote_return_pct, max_promote_drawdown_pct),
        },
    }


def label_trade(
    trade: dict,
    *,
    min_promote_return_pct: float = 0.0,
    max_promote_drawdown_pct: float | None = None,
) -> str:
    if str(trade.get("result", "")).upper() != "WIN":
        return REJECT
    if _float(trade.get("return_pct")) < float(min_promote_return_pct):
        return REJECT
    if max_promote_drawdown_pct is not None:
        drawdown = abs(_float(trade.get("max_drawdown_pct")))
        if drawdown > abs(float(max_promote_drawdown_pct)):
            return REJECT
    return PROMOTE


def label_rule_text(min_promote_return_pct: float, max_promote_drawdown_pct: float | None) -> str:
    rule = f"{PROMOTE}=future WIN with return_pct >= {float(min_promote_return_pct):g}; otherwise {REJECT}"
    if max_promote_drawdown_pct is not None:
        rule += f"; reject if absolute max_drawdown_pct > {abs(float(max_promote_drawdown_pct)):g}"
    return rule


def build_signal_context(trade: dict, *, universe: str) -> str:
    filters = trade.get("filters") or {}
    breakdown = trade.get("breakdown") or {}
    traits = trade.get("pattern_extra") or {}
    all_patterns = trade.get("all_patterns") or [trade.get("pattern")]

    sections = [
        "[DOC] Pattern Finder signal snapshot",
        f"Universe: {universe}",
        f"Symbol: {str(trade.get('symbol', '')).upper()}",
        f"Signal date: {trade.get('signal_date')}",
        f"Primary pattern: {trade.get('pattern')}",
        f"All detected patterns: {_compact_json(all_patterns)}",
        f"Current scanner tier: {trade.get('tier')}",
        f"Current scanner conviction score: {trade.get('score')}",
        f"Pattern quality score: {trade.get('pattern_quality_score')}",
        f"Pattern confidence: {trade.get('pattern_confidence')}",
        f"Pattern timeframe: {trade.get('pattern_timeframe')}",
        f"Stacked pattern count: {trade.get('stacked_count', 1)}",
        f"Bars in pattern: {trade.get('bars_in_pattern')}",
        "[DOC] Trade plan available on the signal date",
        f"Entry price: {trade.get('entry_price') or trade.get('entry')}",
        f"Target: {trade.get('target')}",
        f"Stop loss: {trade.get('stop_loss') or trade.get('stop')}",
        f"Reward risk: {trade.get('reward_risk') or breakdown.get('reward_risk')}",
        "[DOC] Filter states available on the signal date",
        _format_filters(filters),
        "[DOC] Pattern traits available on the signal date",
        _compact_json(traits),
    ]
    return "\n".join(str(part) for part in sections if part is not None)


def split_items(
    items: Iterable[dict],
    *,
    train_ratio: float = 0.6,
    val_ratio: float = 0.2,
    test_ratio: float = 0.2,
) -> dict[str, list[dict]]:
    ordered = sorted(list(items), key=_split_sort_key)
    total = len(ordered)
    if total == 0:
        return {"train": [], "val": [], "test": []}
    train_count, val_count, test_count = _split_counts(total, train_ratio, val_ratio, test_ratio)
    return {
        "train": ordered[:train_count],
        "val": ordered[train_count : train_count + val_count],
        "test": ordered[train_count + val_count : train_count + val_count + test_count],
    }


def write_skillopt_dataset(
    items: Iterable[dict],
    output_dir: str | Path,
    *,
    manifest: dict | None = None,
) -> SkillOptExportSummary:
    output = Path(output_dir)
    splits = split_items(items)
    output.mkdir(parents=True, exist_ok=True)

    split_counts: dict[str, int] = {}
    label_counts: Counter[str] = Counter()
    for split, split_items_list in splits.items():
        split_path = output / split
        split_path.mkdir(parents=True, exist_ok=True)
        (split_path / "items.json").write_text(
            json.dumps(split_items_list, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        split_counts[split] = len(split_items_list)
        for item in split_items_list:
            label_counts.update(item.get("answers") or [])

    initial_skill_path = output / "initial_skill.md"
    initial_skill_path.write_text(initial_skill_text(), encoding="utf-8")

    command_path = output / "skillopt_command.ps1"
    command_path.write_text(skillopt_command_text(output, initial_skill_path), encoding="utf-8")

    manifest_payload = {
        "format": "skillopt_searchqa_split",
        "splits": split_counts,
        "labels": dict(sorted(label_counts.items())),
        **(manifest or {}),
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest_payload, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )

    return SkillOptExportSummary(
        output_dir=output,
        total_items=sum(split_counts.values()),
        split_counts=split_counts,
        label_counts=dict(sorted(label_counts.items())),
        initial_skill_path=initial_skill_path,
        command_path=command_path,
    )


def initial_skill_text() -> str:
    return """# Pattern Finder Alert Selection Skill

Classify each historical scanner signal as PROMOTE or REJECT.

PROMOTE means the setup deserved a loud high-conviction alert. REJECT means it should have been suppressed, downgraded, or kept for manual watchlist review only.

Use only signal-time evidence from the context. Do not invent future price action.

Starting rules:
- Prefer clean textbook patterns with strong pattern quality, not just a high blended conviction score.
- Demand a realistic trade plan: reward/risk near 2:1 or better, a stop that is not too wide, and no obvious late breakout extension.
- Promote only when trend, volume or pocket-pivot confirmation, sector relative strength, market regime, and weekly alignment support the setup.
- Reject setups with weak volume, lagging sector strength, bearish or unknown market regime, poor multi-timeframe alignment, extreme RSI risk, or messy pattern traits.
- If the evidence is mixed, choose REJECT.

Answer with exactly one token inside the answer tags: <answer>PROMOTE</answer> or <answer>REJECT</answer>.
"""


def skillopt_command_text(split_dir: Path, initial_skill_path: Path) -> str:
    split_dir_text = str(split_dir.resolve())
    skill_path_text = str(initial_skill_path.resolve())
    out_root_text = str((split_dir / "skillopt_run").resolve())
    return f"""param(
    [Parameter(Mandatory = $true)]
    [string]$SkillOptPath,
    [string]$OptimizerModel = "gpt-5.5",
    [string]$TargetModel = "gpt-5.5"
)

Set-Location $SkillOptPath
python scripts/train.py `
    --config configs/searchqa/default.yaml `
    --split_dir "{split_dir_text}" `
    --skill_init "{skill_path_text}" `
    --out_root "{out_root_text}" `
    --optimizer_model $OptimizerModel `
    --target_model $TargetModel `
    --num_epochs 2 `
    --batch_size 16 `
    --minibatch_size 4 `
    --merge_batch_size 4 `
    --workers 4 `
    --analyst_workers 4
"""


def _format_filters(filters: dict) -> str:
    if not filters:
        return "No filter details were recorded."
    lines = []
    for name in sorted(filters):
        info = filters.get(name) or {}
        status = info.get("status") or info.get("verdict") or ("PASS" if info.get("passed") else "FAIL")
        details = info.get("details") or {}
        lines.append(f"- {name}: {status}; details={_compact_json(details)}")
    return "\n".join(lines)


def _split_counts(total: int, train_ratio: float, val_ratio: float, test_ratio: float) -> tuple[int, int, int]:
    ratios = [float(train_ratio), float(val_ratio), float(test_ratio)]
    if any(ratio < 0 for ratio in ratios) or sum(ratios) <= 0:
        raise ValueError("Split ratios must be non-negative and sum to more than zero")
    raw = [total * ratio / sum(ratios) for ratio in ratios]
    counts = [int(value) for value in raw]
    remaining = total - sum(counts)
    order = sorted(range(3), key=lambda idx: raw[idx] - counts[idx], reverse=True)
    for idx in order[:remaining]:
        counts[idx] += 1

    if total >= 3:
        for idx in range(3):
            if counts[idx] == 0:
                donor = max(range(3), key=lambda pos: counts[pos])
                counts[donor] -= 1
                counts[idx] += 1
    return counts[0], counts[1], counts[2]


def _split_sort_key(item: dict) -> tuple[str, str]:
    metadata = item.get("metadata") or {}
    return (str(metadata.get("signal_date", "")), str(item.get("id", "")))


def _item_id(trade: dict, universe: str, index: int) -> str:
    parts = [
        universe,
        trade.get("signal_date"),
        trade.get("symbol"),
        trade.get("pattern"),
        index,
    ]
    slug = "_".join(_slug(part) for part in parts if part is not None)
    return slug[:160] or f"trade_{index}"


def _slug(value) -> str:
    text = str(value).strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def _compact_json(value) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)


def _float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
