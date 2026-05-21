"""Conviction scoring for detected patterns.

Real-money rule set. A scored pattern is shown to the user (dashboard,
Telegram) only when ALL of the following hold:

  * Pattern grade >= ``MIN_TRADABLE_QUALITY_SCORE`` (0-100 scale; for
    audited detectors this is the 0-10 ``extra.pattern_quality_score``
    multiplied by 10; legacy detectors fall back to the dataclass
    ``quality_score`` then ``confidence``).
  * Reward/risk >= ``MIN_REWARD_RISK_HARD``. Below 1:1 is a bad bet by
    definition; we do not surface it.

If both gates pass, the raw 0-100 conviction score determines the tier
via ``CONVICTION_TIERS``. Two soft caps then refine the tier:

  * If the pattern grade (0-10) is below
    ``PATTERN_GRADE_HIGHEST_FLOOR``, the tier cannot exceed HIGH.
  * If reward/risk is below ``MIN_REWARD_RISK_SOFT``, the tier is
    demoted by one step.

This keeps directionally correct but technically mediocre setups
(EMCURE IHS 5.5/10, ACUTAAS bull flag RSI 87 + R:R 1.4 cases on
2026-05-21) from being flagged as HIGHEST conviction.
"""

from __future__ import annotations

from typing import Any

from config import settings
from filters import rsi, sector_rs, stage2, volume
from patterns.base import PatternResult
from patterns.utils import moving_average, series


TIER_ORDER = ("HIGHEST", "HIGH", "MEDIUM", "SKIP")


def score_pattern(
    symbol: str,
    pattern: PatternResult,
    daily: dict,
    weekly: dict,
    market_regime_result: dict,
    sector_rs_cache: dict,
) -> dict:
    stage2_result = stage2.evaluate(daily)
    volume_result = volume.evaluate(daily)
    sector_result = sector_rs.evaluate(symbol, daily, sector_rs_cache)
    rsi_result = rsi.evaluate(daily)
    multi_tf_result = _multi_timeframe_alignment(pattern, weekly)

    pattern_score_100 = _pattern_quality_score(pattern)
    pattern_grade_10 = round(pattern_score_100 / 10.0, 2)
    pattern_points = _pattern_quality_points(pattern_score_100)
    stage2_points = settings.CONVICTION_WEIGHTS["stage2"] if stage2_result["passed"] else 0
    volume_points = _volume_points(volume_result)
    sector_points = _sector_points(sector_result)
    regime_points = _regime_points(market_regime_result)
    multi_tf_points = settings.CONVICTION_WEIGHTS["multi_tf"] if multi_tf_result["passed"] else 0
    rsi_adjustment = int(rsi_result["penalty"])

    raw_score = (
        pattern_points
        + stage2_points
        + volume_points
        + sector_points
        + regime_points
        + multi_tf_points
        + rsi_adjustment
    )

    reward_risk = _reward_risk_ratio(pattern)

    skip_reason = _skip_reason(pattern_score_100, reward_risk)
    if skip_reason is not None:
        final_score = 0
        tier = "SKIP"
    else:
        final_score = int(round(max(0.0, min(100.0, raw_score))))
        tier = conviction_tier(final_score)
        tier = _apply_tier_caps(
            tier=tier,
            pattern_grade=pattern_grade_10,
            reward_risk=reward_risk,
        )

    return {
        "symbol": symbol.upper(),
        "pattern": pattern.pattern,
        "status": pattern.status,
        "pivot": pattern.pivot,
        "target": pattern.target,
        "stop_loss": pattern.stop_loss,
        "timeframe": pattern.timeframe,
        "pattern_result": pattern,
        "score": final_score,
        "tier": tier,
        "tradable": tier != "SKIP" and skip_reason is None,
        "skip_reason": skip_reason,
        "reward_risk": None if reward_risk is None else round(reward_risk, 2),
        "pattern_grade": pattern_grade_10,
        "breakdown": {
            "pattern": pattern_points,
            "pattern_quality_score": pattern_score_100,
            "pattern_grade": pattern_grade_10,
            "pattern_confidence": pattern.confidence,
            "stage2": stage2_points,
            "volume": volume_points,
            "sector_rs": sector_points,
            "market_regime": regime_points,
            "multi_tf": multi_tf_points,
            "rsi_adjustment": rsi_adjustment,
            "reward_risk": None if reward_risk is None else round(reward_risk, 2),
        },
        "filters": {
            "stage2": stage2_result,
            "volume": volume_result,
            "sector_rs": sector_result,
            "market_regime": market_regime_result,
            "rsi": rsi_result,
            "multi_tf": multi_tf_result,
        },
    }


def conviction_tier(score: int | float) -> str:
    score = int(score)
    if score >= settings.CONVICTION_TIERS["HIGHEST"]:
        return "HIGHEST"
    if score >= settings.CONVICTION_TIERS["HIGH"]:
        return "HIGH"
    if score >= settings.CONVICTION_TIERS["MEDIUM"]:
        return "MEDIUM"
    return "SKIP"


# ---------------------------------------------------------------------------
# Quality + R:R helpers
# ---------------------------------------------------------------------------

def _pattern_quality_score(pattern: PatternResult) -> float:
    """Return a 0-100 quality score.

    Prefers ``extra['pattern_quality_score']`` (0-10 grade from audited
    detectors) scaled to 0-100. Falls back to ``PatternResult.quality_score``
    (legacy 0-100) then ``PatternResult.confidence`` for detectors that have
    not been audited yet.
    """
    extra = pattern.extra or {}
    grade = extra.get("pattern_quality_score") if isinstance(extra, dict) else None
    if grade is not None:
        try:
            return round(max(0.0, min(100.0, float(grade) * 10.0)), 2)
        except (TypeError, ValueError):
            pass
    legacy = pattern.quality_score if pattern.quality_score is not None else pattern.confidence
    try:
        return round(max(0.0, min(100.0, float(legacy))), 2)
    except (TypeError, ValueError):
        return 0.0


def _pattern_quality_points(quality_score: float) -> int:
    for threshold, points in settings.QUALITY_SCORE_POINTS:
        if quality_score >= float(threshold):
            return int(points)
    return 0


def _reward_risk_ratio(pattern: PatternResult) -> float | None:
    """Return upside/downside ratio computed from pivot/target/stop_loss.

    Returns None when any leg is missing or pivot is non-positive. Returns
    None (not 0) when downside is <= 0 so callers can distinguish "no R:R
    available" from "R:R = 0".
    """
    pivot, target, stop = pattern.pivot, pattern.target, pattern.stop_loss
    if pivot is None or target is None or stop is None:
        return None
    try:
        pivot_f = float(pivot)
        target_f = float(target)
        stop_f = float(stop)
    except (TypeError, ValueError):
        return None
    if pivot_f <= 0:
        return None
    upside = (target_f - pivot_f) / pivot_f
    downside = (pivot_f - stop_f) / pivot_f
    if downside <= 0 or upside <= 0:
        return None
    return upside / downside


def _skip_reason(pattern_score_100: float, reward_risk: float | None) -> str | None:
    """Hard gates before tiering. Either failure means SKIP regardless of score."""
    if pattern_score_100 < float(settings.MIN_TRADABLE_QUALITY_SCORE):
        return "LOW_PATTERN_QUALITY"
    if reward_risk is not None and reward_risk < float(settings.MIN_REWARD_RISK_HARD):
        return f"REWARD_RISK_BELOW_FLOOR_{reward_risk:.2f}"
    return None


def _apply_tier_caps(*, tier: str, pattern_grade: float, reward_risk: float | None) -> str:
    """Soft caps. Setup passed gates but should not be the loudest signal."""
    if pattern_grade < float(settings.PATTERN_GRADE_HIGHEST_FLOOR):
        tier = _cap_tier(tier, "HIGH")
    if reward_risk is not None and reward_risk < float(settings.MIN_REWARD_RISK_SOFT):
        tier = _demote_tier(tier)
    return tier


def _cap_tier(tier: str, max_tier: str) -> str:
    """Return whichever of (tier, max_tier) is lower in the tier order."""
    if tier not in TIER_ORDER or max_tier not in TIER_ORDER:
        return tier
    return tier if TIER_ORDER.index(tier) >= TIER_ORDER.index(max_tier) else max_tier


def _demote_tier(tier: str) -> str:
    """Move one step down the tier ladder: HIGHEST -> HIGH -> MEDIUM -> SKIP."""
    if tier not in TIER_ORDER:
        return tier
    idx = TIER_ORDER.index(tier)
    return TIER_ORDER[min(idx + 1, len(TIER_ORDER) - 1)]


# ---------------------------------------------------------------------------
# Filter-point helpers (unchanged behavior)
# ---------------------------------------------------------------------------

def _volume_points(result: dict) -> int:
    if result["passed"]:
        return settings.CONVICTION_WEIGHTS["volume"]
    if result.get("details", {}).get("base_dry_up"):
        return settings.CONVICTION_WEIGHTS["volume"] // 2
    return 0


def _sector_points(result: dict) -> int:
    if result["status"] == "LEADING":
        return settings.CONVICTION_WEIGHTS["sector_rs"]
    if result["status"] == "NEUTRAL":
        return settings.CONVICTION_WEIGHTS["sector_rs"] // 2
    return 0


def _regime_points(result: dict) -> int:
    score = int(result.get("score", 0))
    if score >= 3:
        return settings.CONVICTION_WEIGHTS["market_regime"]
    if score == 2:
        return settings.CONVICTION_WEIGHTS["market_regime"] // 2
    return 0


def _bear_regime(result: dict) -> bool:
    if str(result.get("verdict", "")).upper() == "BEAR":
        return True
    score = int(result.get("score", 0))
    return score <= int(settings.MARKET_REGIME["bear_score_threshold"])


def _multi_timeframe_alignment(pattern: PatternResult, weekly: dict) -> dict:
    close = series(weekly, "close")
    if pattern.timeframe.lower() == "weekly":
        return {"name": "multi_tf", "passed": True, "status": "WEEKLY_PATTERN", "details": {}}
    if len(close) < 35:
        return {"name": "multi_tf", "passed": False, "status": "INSUFFICIENT_WEEKLY_DATA", "details": {}}
    ma30 = moving_average(close, 30)
    latest_close = float(close[-1])
    latest_ma = float(ma30[-1])
    prior_ma = float(ma30[-5]) if len(ma30) >= 5 else float(ma30[0])
    passed = latest_close > latest_ma and latest_ma > prior_ma
    return {
        "name": "multi_tf",
        "passed": bool(passed),
        "status": "ALIGNED" if passed else "NOT_ALIGNED",
        "details": {
            "weekly_close": round(latest_close, 2),
            "weekly_ma30": round(latest_ma, 2),
            "weekly_ma30_slope": round(latest_ma - prior_ma, 4),
        },
    }
