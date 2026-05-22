"""Structured explanation generator for scored pattern hits."""

from __future__ import annotations

from config import settings
from patterns.base import PatternResult


PATTERN_101 = {
    "Ascending Triangle": (
        "An Ascending Triangle forms when price repeatedly tests a flat resistance "
        "level while making higher lows. It shows buyers stepping in earlier on "
        "each pullback while sellers defend the same ceiling."
    ),
    "Cup & Handle": (
        "A Cup & Handle forms when price rounds out a larger base, returns near the "
        "old high, then pulls back in a smaller handle before attempting breakout."
    ),
    "Bull Flag": (
        "A Bull Flag forms after a sharp advance followed by a tight consolidation. "
        "The flag is useful only when volume cools off instead of showing heavy selling."
    ),
    "VCP": (
        "A Volatility Contraction Pattern shows each price swing becoming smaller. "
        "That tightening suggests weak holders are being shaken out before a breakout."
    ),
    "Inverse Head & Shoulders": (
        "An Inverse Head & Shoulders is a reversal pattern with three troughs. The "
        "middle trough is deepest, and a break above the neckline confirms buyers are taking control."
    ),
    "Supertrend Bullish Flip": (
        "Supertrend is an ATR-based trend filter. A bullish flip means price has "
        "moved above its volatility-adjusted trailing resistance."
    ),
    "Multi-Year Breakout": (
        "A Multi-Year Breakout happens when price clears a resistance level that "
        "has capped the stock for years. Long bases can reduce overhead supply."
    ),
}


def generate_explanation(scored: dict) -> str:
    symbol = scored["symbol"]
    pattern: PatternResult = scored["pattern_result"]
    filters = scored["filters"]
    breakdown = scored["breakdown"]
    rr = _risk_reward(scored)
    return "\n\n".join(
        [
            f"SECTION 0: {pattern.pattern.upper()}",
            "SECTION 1: PATTERN 101\n" + PATTERN_101.get(pattern.pattern, pattern.explanation),
            "SECTION 2: THIS STOCK SPECIFICALLY\n" + _stock_specific(symbol, scored, filters),
            "SECTION 3: ACTION PLAN\n" + _action_plan(scored, rr),
            "SECTION 4: RISK\n" + _risk_note(pattern, filters),
            "SECTION 5: CONVICTION BREAKDOWN\n" + _breakdown(scored, breakdown, filters),
        ]
    )


def attach_explanation(scored: dict) -> dict:
    enriched = dict(scored)
    enriched["explanation"] = generate_explanation(scored)
    return enriched


def _stock_specific(symbol: str, scored: dict, filters: dict) -> str:
    pattern: PatternResult = scored["pattern_result"]
    stage = filters["stage2"]
    volume = filters["volume"]
    sector = filters["sector_rs"]
    rsi = filters["rsi"]
    entry = _entry_price(scored)
    return (
        f"{symbol} triggered {pattern.pattern} on the {pattern.timeframe} chart. "
        f"Detector detail: {pattern.explanation} Pivot is Rs.{pattern.pivot}, "
        f"scan-date entry is Rs.{entry}, "
        f"target is Rs.{pattern.target}, and stop is Rs.{pattern.stop_loss}. "
        f"Stage 2 is {stage['status']}; volume is {volume['status']} with "
        f"{volume.get('details', {}).get('breakout_volume_ratio', 0)}x breakout volume; "
        f"sector RS is {sector['status']} versus Nifty; RSI is {rsi['value']} ({rsi['status']})."
    )


def _action_plan(scored: dict, rr: dict) -> str:
    pattern: PatternResult = scored["pattern_result"]
    entry = _entry_price(scored)
    return (
        f"Entry: Buy only above Rs.{entry} with volume confirmation. "
        f"Stop loss: Rs.{pattern.stop_loss}. Target: Rs.{pattern.target}. "
        f"Risk per share: Rs.{rr['risk']}. Reward per share: Rs.{rr['reward']}. "
        f"Reward:risk: {rr['ratio']}:1."
    )


def _risk_note(pattern: PatternResult, filters: dict) -> str:
    warnings = [f"The setup is invalid if price closes below Rs.{pattern.stop_loss}."]
    if not filters["volume"]["passed"]:
        warnings.append("Breakout volume is not confirmed yet; a low-volume breakout can fail.")
    if filters["rsi"]["bearish_divergence"]:
        warnings.append("RSI bearish divergence is present and reduces conviction.")
    if filters["market_regime"].get("score", 0) <= 1:
        warnings.append("Market regime is bearish; new breakouts should be avoided.")
    return " ".join(warnings)


def _breakdown(scored: dict, breakdown: dict, filters: dict) -> str:
    def line(label: str, key: str, status: str) -> str:
        weight = settings.CONVICTION_WEIGHTS[key]
        if weight <= 0:
            return f"{label}: disabled ({status})"
        return f"{label}: {breakdown[key]}/{weight} ({status})"

    return (
        f"Conviction: {scored['score']}/100 {scored['tier']}\n"
        f"{line('Pattern quality', 'pattern', 'quality gate')}\n"
        f"{line('Stage 2 uptrend', 'stage2', filters['stage2']['status'])}\n"
        f"{line('Volume', 'volume', filters['volume']['status'])}\n"
        f"{line('Sector RS', 'sector_rs', filters['sector_rs']['status'])}\n"
        f"{line('Market regime', 'market_regime', filters['market_regime']['verdict'])}\n"
        f"{line('Multi-timeframe', 'multi_tf', filters['multi_tf']['status'])}\n"
        f"RSI adjustment: {breakdown['rsi_adjustment']}"
    )


def _risk_reward(scored: dict) -> dict:
    pattern: PatternResult = scored["pattern_result"]
    entry = _entry_price(scored)
    risk = max(round(entry - pattern.stop_loss, 2), 0.0)
    reward = max(round(pattern.target - entry, 2), 0.0)
    ratio = round(reward / risk, 2) if risk > 0 else 0.0
    return {"risk": risk, "reward": reward, "ratio": ratio}


def _entry_price(scored: dict) -> float:
    entry = scored.get("entry_price")
    if entry is None:
        entry = scored["pattern_result"].pivot
    return float(entry)
