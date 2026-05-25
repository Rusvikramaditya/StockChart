"""Consolidate multiple detected patterns into one result per symbol."""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from config import settings
from engine.scorer import conviction_tier

TIER_ORDER = ("HIGHEST", "HIGH", "MEDIUM", "SKIP")


def deduplicate_results(scored_results: Iterable[dict]) -> list[dict]:
    """Return one consolidated scored result per symbol.

    The highest-conviction pattern remains the primary setup. Additional unique
    patterns stay visible in the dashboard and Telegram alert without changing
    conviction unless settings explicitly enable a stack bonus.
    """

    groups: dict[str, list[dict]] = defaultdict(list)
    for item in scored_results:
        symbol = str(item.get("symbol", "")).strip().upper()
        if symbol:
            groups[symbol].append(item)

    consolidated = [_merge_symbol(symbol, items) for symbol, items in groups.items()]
    return sorted(consolidated, key=lambda item: (-int(item.get("score", 0)), item["symbol"]))


def dedup_results(scored_results: Iterable[dict]) -> list[dict]:
    """Backward-compatible alias for the plan's original function name."""

    return deduplicate_results(scored_results)


def _merge_symbol(symbol: str, items: list[dict]) -> dict:
    primary = max(items, key=lambda item: int(item.get("score", 0)))
    merged = dict(primary)
    merged["symbol"] = symbol

    all_patterns = _unique_patterns(primary, items)
    stack_bonus = min(
        max(0, len(all_patterns) - 1) * int(settings.STACK_BONUS_PER_PATTERN),
        int(settings.STACK_BONUS_CAP),
    )
    individual_score = int(primary.get("score", 0))
    final_score = min(100, max(0, individual_score + stack_bonus))
    primary_tier = str(primary.get("tier") or conviction_tier(individual_score)).upper()
    tier = _lower_tier(conviction_tier(final_score), primary_tier)

    merged["individual_score"] = individual_score
    merged["stack_bonus"] = stack_bonus
    merged["score"] = final_score
    merged["tier"] = tier
    merged["stacked_count"] = len(all_patterns)
    merged["all_patterns"] = all_patterns
    merged["also_detected"] = [name for name in all_patterns if name != merged.get("pattern")]
    merged["pattern_results"] = [item.get("pattern_result") for item in items if item.get("pattern_result")]

    primary_tradable = bool(primary.get("tradable", tier != "SKIP"))
    merged["tradable"] = primary_tradable and tier != "SKIP" and not primary.get("skip_reason")
    if tier == "SKIP" and not merged.get("skip_reason"):
        merged["skip_reason"] = "LOW_CONVICTION_AFTER_DEDUP"

    if len(all_patterns) > 1:
        note = "Stacked patterns detected: " + ", ".join(all_patterns) + "."
        explanation = str(merged.get("explanation") or "").strip()
        merged["explanation"] = f"{explanation}\n\n{note}" if explanation else note

    return merged


def _lower_tier(left: str, right: str) -> str:
    """Return the lower-conviction tier so scorer caps survive dedup."""
    left = str(left or "SKIP").upper()
    right = str(right or "SKIP").upper()
    if left not in TIER_ORDER:
        left = "SKIP"
    if right not in TIER_ORDER:
        right = "SKIP"
    return left if TIER_ORDER.index(left) >= TIER_ORDER.index(right) else right


def _unique_patterns(primary: dict, items: list[dict]) -> list[str]:
    names: list[str] = []

    def add(name: object) -> None:
        text = str(name or "").strip()
        if text and text not in names:
            names.append(text)

    add(primary.get("pattern"))
    for item in sorted(items, key=lambda row: int(row.get("score", 0)), reverse=True):
        add(item.get("pattern"))
    return names or ["Pattern"]
