"""Conviction scoring for detected patterns."""

from __future__ import annotations

from config import settings
from filters import rsi, sector_rs, stage2, volume
from patterns.base import PatternResult
from patterns.utils import moving_average, series


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

    pattern_quality = _pattern_quality_score(pattern)
    pattern_points = _pattern_quality_points(pattern_quality)
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
    skip_reason = None
    if _bear_regime(market_regime_result):
        final_score = 0
        tier = "SKIP"
        skip_reason = "BEAR_REGIME"
    elif pattern_quality < float(settings.MIN_TRADABLE_QUALITY_SCORE):
        final_score = 0
        tier = "SKIP"
        skip_reason = "LOW_PATTERN_QUALITY"
    else:
        final_score = int(round(max(0.0, min(100.0, raw_score))))
        tier = conviction_tier(final_score)
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
        "breakdown": {
            "pattern": pattern_points,
            "pattern_quality_score": pattern_quality,
            "pattern_confidence": pattern.confidence,
            "stage2": stage2_points,
            "volume": volume_points,
            "sector_rs": sector_points,
            "market_regime": regime_points,
            "multi_tf": multi_tf_points,
            "rsi_adjustment": rsi_adjustment,
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


def _pattern_quality_score(pattern: PatternResult) -> float:
    quality = pattern.quality_score if pattern.quality_score is not None else pattern.confidence
    return round(max(0.0, min(100.0, float(quality))), 2)


def _pattern_quality_points(quality_score: float) -> int:
    for threshold, points in settings.QUALITY_SCORE_POINTS:
        if quality_score >= float(threshold):
            return int(points)
    return 0


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
