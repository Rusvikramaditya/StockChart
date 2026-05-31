"""Real-money conviction-scoring rule tests.

Covers the four fixes wired in scorer.py + config/settings.py to address
the EMCURE / ACUTAAS 100/100 HIGHEST over-rating bug (2026-05-21):

1. Pattern grade flows through: extra.pattern_quality_score (0-10) is
   the dominant input; legacy confidence is only a fallback.
2. RSI extreme penalties subtract from raw conviction.
3. Reward / risk floors gate the tier from the scan-date entry:
   <1.0 = SKIP, actionable <1.5 = SKIP, historical <1.5 = demote.
4. Pattern grade ceiling: grades < PATTERN_GRADE_HIGHEST_FLOOR cannot
   tier HIGHEST regardless of filter passes.

These tests are unit tests against scorer internals + a focused
end-to-end test that exercises score_pattern() with synthetic OHLCV
to make sure the rules survive the full scoring pipeline.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np

from config import settings
from engine import dedup, scorer, telegram
from patterns.base import PatternResult


def _pattern(
    *,
    name: str = "Inverse Head & Shoulders",
    pivot: float = 1750.83,
    target: float = 1975.01,
    stop_loss: float = 1497.10,
    confidence: float = 88.0,
    grade: float | None = 5.5,
    extra: dict | None = None,
    timeframe: str = "daily",
) -> PatternResult:
    payload = dict(extra or {})
    if grade is not None and "pattern_quality_score" not in payload:
        payload["pattern_quality_score"] = grade
    return PatternResult(
        pattern=name,
        status="BREAKING OUT",
        pivot=pivot,
        target=target,
        stop_loss=stop_loss,
        confidence=confidence,
        explanation="synthetic",
        timeframe=timeframe,
        bars_in_pattern=120,
        quality_score=confidence,
        extra=payload,
    )


class PatternQualitySourceTest(unittest.TestCase):

    def test_extra_grade_dominates_legacy_confidence(self):
        """A detector publishing extra.pattern_quality_score=5.5 must
        produce a 55-point quality score even when legacy confidence=95."""
        p = _pattern(grade=5.5, confidence=95.0)
        self.assertAlmostEqual(scorer._pattern_quality_score(p), 55.0, places=1)

    def test_fallback_to_confidence_when_no_grade(self):
        p = _pattern(grade=None, confidence=88.0)
        self.assertAlmostEqual(scorer._pattern_quality_score(p), 88.0, places=1)

    def test_invalid_grade_falls_back(self):
        p = _pattern(grade=None, confidence=70.0, extra={"pattern_quality_score": "nope"})
        self.assertAlmostEqual(scorer._pattern_quality_score(p), 70.0, places=1)


class RewardRiskRatioTest(unittest.TestCase):

    def test_emcure_real_world_case(self):
        """EMCURE: entry 1750.83, target 1975.01, stop 1497.10 -> ~0.89."""
        p = _pattern(pivot=1750.83, target=1975.01, stop_loss=1497.10)
        rr = scorer._reward_risk_ratio(p)
        self.assertIsNotNone(rr)
        self.assertAlmostEqual(rr, 12.8 / 14.5, places=2)

    def test_missing_leg_returns_none(self):
        p = _pattern(target=None)  # type: ignore[arg-type]
        self.assertIsNone(scorer._reward_risk_ratio(p))

    def test_zero_or_negative_downside_returns_none(self):
        p = _pattern(pivot=100.0, target=120.0, stop_loss=105.0)  # stop above pivot
        self.assertIsNone(scorer._reward_risk_ratio(p))

    def test_zero_or_negative_upside_returns_none(self):
        p = _pattern(pivot=100.0, target=95.0, stop_loss=90.0)  # target below pivot
        self.assertIsNone(scorer._reward_risk_ratio(p))


class TierHelperTest(unittest.TestCase):

    def test_cap_returns_lower_tier(self):
        self.assertEqual(scorer._cap_tier("HIGHEST", "HIGH"), "HIGH")
        self.assertEqual(scorer._cap_tier("MEDIUM", "HIGH"), "MEDIUM")
        self.assertEqual(scorer._cap_tier("SKIP", "HIGH"), "SKIP")

    def test_demote_steps_down_one(self):
        self.assertEqual(scorer._demote_tier("HIGHEST"), "HIGH")
        self.assertEqual(scorer._demote_tier("HIGH"), "MEDIUM")
        self.assertEqual(scorer._demote_tier("MEDIUM"), "SKIP")
        self.assertEqual(scorer._demote_tier("SKIP"), "SKIP")


class SkipReasonTest(unittest.TestCase):

    def test_low_pattern_quality_skips(self):
        # grade 5.5 -> 55 score, below the live tradable floor
        self.assertEqual(
            scorer._skip_reason(pattern_score_100=55.0, reward_risk=2.0),
            "LOW_PATTERN_QUALITY",
        )

    def test_bad_rr_skips_even_with_great_pattern(self):
        reason = scorer._skip_reason(pattern_score_100=95.0, reward_risk=0.9)
        self.assertIsNotNone(reason)
        self.assertTrue(reason.startswith("REWARD_RISK_BELOW_FLOOR"))

    def test_no_skip_when_both_gates_pass(self):
        self.assertIsNone(scorer._skip_reason(pattern_score_100=80.0, reward_risk=1.6))

    def test_missing_rr_does_not_skip(self):
        """Some patterns (Supertrend) have no R:R; absence is not a fail."""
        self.assertIsNone(scorer._skip_reason(pattern_score_100=80.0, reward_risk=None))


class TierCapTest(unittest.TestCase):

    def test_grade_below_floor_caps_at_high(self):
        tier = scorer._apply_tier_caps(
            tier="HIGHEST", pattern_grade=6.0, reward_risk=2.0,
        )
        self.assertEqual(tier, "HIGH")

    def test_grade_at_or_above_floor_keeps_highest(self):
        floor = float(settings.PATTERN_GRADE_HIGHEST_FLOOR)
        tier = scorer._apply_tier_caps(
            tier="HIGHEST", pattern_grade=floor, reward_risk=2.0,
        )
        self.assertEqual(tier, "HIGHEST")

    def test_weak_rr_demotes_one_step(self):
        tier = scorer._apply_tier_caps(
            tier="HIGHEST", pattern_grade=9.0, reward_risk=1.3,
        )
        self.assertEqual(tier, "HIGH")

    def test_grade_cap_and_rr_demote_compose(self):
        """Below-floor grade caps at HIGH, then weak R:R demotes to MEDIUM."""
        tier = scorer._apply_tier_caps(
            tier="HIGHEST", pattern_grade=6.0, reward_risk=1.2,
        )
        self.assertEqual(tier, "MEDIUM")


def _trending_daily(n: int = 260, start: float = 100.0, end: float = 100.0) -> dict:
    close = np.linspace(start, end, n)
    return {
        "open": close.copy(),
        "high": close + 0.5,
        "low": close - 0.5,
        "close": close,
        "volume": np.full(n, 1_000_000.0),
    }


class ScorePatternEndToEndTest(unittest.TestCase):
    """Exercise the full score_pattern() with synthetic OHLCV that
    isolates the new rules. We patch the leaf filters to focus on the
    new gating logic without re-testing them here.
    """

    def _run(self, pattern, rsi_value=65.0, rsi_status="HEALTHY"):
        daily = _trending_daily(n=260, start=100.0, end=110.0)
        weekly = _trending_daily(n=80, start=100.0, end=110.0)
        regime = {"score": 4, "verdict": "CONFIRMED UPTREND"}
        sector_cache = {"sectors": {}, "symbol_to_sector": {}, "nifty_return_pct": 0.0}
        with patch("engine.scorer.stage2.evaluate", return_value={"passed": True, "status": "PASS", "details": {"close": 110.0}}), \
             patch("engine.scorer.volume.evaluate", return_value={"passed": True, "status": "PASS", "details": {}}), \
             patch("engine.scorer.sector_rs.evaluate", return_value={"passed": True, "status": "LEADING", "details": {}}), \
             patch("engine.scorer.rsi.evaluate", return_value={"name": "rsi", "value": rsi_value, "penalty": 0, "status": rsi_status, "bearish_divergence": False, "details": {}}):
            return scorer.score_pattern("TEST", pattern, daily, weekly, regime, sector_cache)

    def test_emcure_like_pattern_is_skipped(self):
        """Grade 5.5 + RR 0.9 -> hard SKIP, never reaches dashboard."""
        p = _pattern(grade=5.5, pivot=1750.83, target=1975.01, stop_loss=1497.10)
        result = self._run(p)
        self.assertEqual(result["tier"], "SKIP")
        self.assertFalse(result["tradable"])
        self.assertIsNotNone(result["skip_reason"])

    def test_acutaas_like_pattern_skipped_when_future_rr_is_weak(self):
        """Strong filters + decent grade 7.1 + R:R 1.4 is not actionable."""
        p = _pattern(name="Bull Flag", grade=7.1, pivot=100.0, target=139.0, stop_loss=90.0)
        result = self._run(p)
        self.assertEqual(result["tier"], "SKIP")
        self.assertFalse(result["tradable"])
        self.assertTrue(result["skip_reason"].startswith("ACTIONABLE_REWARD_RISK_BELOW_FLOOR"))

    def test_textbook_pattern_stays_highest(self):
        """Grade 8 + RR 2.0 + clean filters -> HIGHEST."""
        p = _pattern(grade=8.5, pivot=120.0, target=180.0, stop_loss=108.0)
        result = self._run(p)
        self.assertEqual(result["tier"], "HIGHEST")
        self.assertTrue(result["tradable"])

    def test_scan_date_entry_rejects_late_breakout(self):
        """Once price is far above pivot, R:R must be judged from scan close."""
        p = _pattern(grade=8.5, pivot=100.0, target=130.0, stop_loss=90.0)
        result = self._run(p)
        self.assertEqual(result["entry_price"], 110.0)
        self.assertEqual(result["entry_basis"], "scan_close")
        self.assertEqual(result["tier"], "SKIP")
        self.assertTrue(result["skip_reason"].startswith("ACTIONABLE_REWARD_RISK_BELOW_FLOOR"))

    def test_target_hit_after_breakout_is_rejected(self):
        """Do not surface a pattern whose measured move has already happened."""
        p = _pattern(grade=8.5, pivot=100.0, target=130.0, stop_loss=90.0)
        daily = _trending_daily(n=260, start=90.0, end=110.0)
        daily["close"][-5:] = np.array([98.0, 101.0, 118.0, 110.0, 109.0])
        daily["high"][-5:] = np.array([99.0, 102.0, 131.0, 112.0, 110.0])
        daily["open"] = daily["close"].copy()
        daily["low"] = daily["close"] - 1.0
        weekly = _trending_daily(n=80, start=100.0, end=110.0)
        with patch("engine.scorer.stage2.evaluate", return_value={"passed": True, "status": "PASS", "details": {"close": 109.0}}), \
             patch("engine.scorer.volume.evaluate", return_value={"passed": True, "status": "PASS", "details": {}}), \
             patch("engine.scorer.sector_rs.evaluate", return_value={"passed": True, "status": "LEADING", "details": {}}), \
             patch("engine.scorer.rsi.evaluate", return_value={"name": "rsi", "value": 65.0, "penalty": 0, "status": "HEALTHY", "bearish_divergence": False, "details": {}}):
            result = scorer.score_pattern("TEST", p, daily, weekly, {"score": 4}, {"sectors": {}})

        self.assertEqual(result["tier"], "SKIP")
        self.assertEqual(result["skip_reason"], "MOVE_ALREADY_HAPPENED_TARGET_HIT")
        self.assertTrue(result["target_hit_since_breakout"])

    def test_rsi_overbought_penalty_active(self):
        """An RSI 87 reading with config penalty -15 must subtract from score."""
        p = _pattern(grade=8.0, pivot=100.0, target=130.0, stop_loss=85.0)
        rsi_payload = {
            "name": "rsi", "value": 87.0, "penalty": int(settings.RSI["penalty_overbought"]["penalty"]),
            "status": "OVERBOUGHT", "bearish_divergence": False, "details": {},
        }
        with patch("engine.scorer.stage2.evaluate", return_value={"passed": True, "status": "PASS", "details": {"close": 100.0}}), \
             patch("engine.scorer.volume.evaluate", return_value={"passed": True, "status": "PASS", "details": {}}), \
             patch("engine.scorer.sector_rs.evaluate", return_value={"passed": True, "status": "LEADING", "details": {}}), \
             patch("engine.scorer.rsi.evaluate", return_value=rsi_payload):
            result = scorer.score_pattern(
                "TEST", p,
                _trending_daily(), _trending_daily(80),
                {"score": 4}, {"sectors": {}},
            )
        self.assertEqual(result["breakdown"]["rsi_adjustment"], settings.RSI["penalty_overbought"]["penalty"])
        # Penalty must actually subtract -- score should be less than the
        # equivalent score with neutral RSI.
        rsi_neutral = {"name": "rsi", "value": 65.0, "penalty": 0, "status": "HEALTHY", "bearish_divergence": False, "details": {}}
        with patch("engine.scorer.stage2.evaluate", return_value={"passed": True, "status": "PASS", "details": {"close": 100.0}}), \
             patch("engine.scorer.volume.evaluate", return_value={"passed": True, "status": "PASS", "details": {}}), \
             patch("engine.scorer.sector_rs.evaluate", return_value={"passed": True, "status": "LEADING", "details": {}}), \
             patch("engine.scorer.rsi.evaluate", return_value=rsi_neutral):
            healthy = scorer.score_pattern(
                "TEST", p,
                _trending_daily(), _trending_daily(80),
                {"score": 4}, {"sectors": {}},
            )
        self.assertLess(result["score"], healthy["score"])

    def test_weekly_pattern_uses_weekly_volume_as_primary_and_keeps_daily_confirmation(self):
        p = _pattern(name="Weekly Breakout", grade=8.5, pivot=108.0, target=140.0, stop_loss=92.0, timeframe="weekly")
        daily = _trending_daily(n=260, start=100.0, end=110.0)
        weekly = _trending_daily(n=80, start=100.0, end=110.0)
        daily_volume = {"passed": False, "status": "FAIL", "details": {"timeframe": "daily"}}
        weekly_volume = {"passed": True, "status": "PASS", "details": {"timeframe": "weekly"}}

        def volume_side_effect(data, *, timeframe="daily", avg_period=None):
            return weekly_volume if timeframe == "weekly" else daily_volume

        with patch("engine.scorer.stage2.evaluate", return_value={"passed": True, "status": "PASS", "details": {"close": 110.0}}), \
             patch("engine.scorer.volume.evaluate", side_effect=volume_side_effect), \
             patch("engine.scorer.pocket_pivot.evaluate", return_value={"passed": False, "status": "FAIL", "details": {}}), \
             patch("engine.scorer.sector_rs.evaluate", return_value={"passed": True, "status": "LEADING", "details": {}}), \
             patch("engine.scorer.rsi.evaluate", return_value={"name": "rsi", "value": 65.0, "penalty": 0, "status": "HEALTHY", "bearish_divergence": False, "details": {}}):
            result = scorer.score_pattern("TEST", p, daily, weekly, {"score": 4}, {"sectors": {}})

        self.assertIs(result["filters"]["volume"], weekly_volume)
        self.assertIs(result["filters"]["daily_volume"], daily_volume)
        self.assertEqual(result["breakdown"]["volume"], settings.CONVICTION_WEIGHTS["volume"])
        self.assertEqual(result["tier"], "HIGH")


class DedupAndTelegramGateTest(unittest.TestCase):

    def test_dedup_preserves_scorer_tier_cap(self):
        merged = dedup.deduplicate_results([
            {
                "symbol": "AAA", "pattern": "Flat Base", "score": 95, "tier": "HIGH",
                "tradable": True, "pattern_result": _pattern(name="Flat Base", grade=7.5),
            }
        ])[0]

        self.assertEqual(merged["tier"], "HIGH")
        self.assertTrue(merged["tradable"])

    def test_telegram_does_not_send_medium_even_with_high_score(self):
        self.assertFalse(telegram.should_send_alert({"tradable": True, "score": 95, "tier": "MEDIUM"}))
        self.assertTrue(telegram.should_send_alert({"tradable": True, "score": 95, "tier": "HIGH"}))


class DashboardSkipFilterTest(unittest.TestCase):

    def test_skip_tier_not_in_dashboard_results(self):
        from engine.dashboard import build_dashboard_context
        ctx = {
            "generated_at": "2026-05-21",
            "market_regime": {"score": 4, "verdict": "CONFIRMED UPTREND", "checks": {}, "details": {}},
            "results": [
                {"symbol": "AAA", "pattern": "Bull Flag", "tier": "HIGHEST", "score": 92,
                 "pivot": 100, "target": 130, "stop_loss": 85, "tradable": True,
                 "filters": {}, "breakdown": {}, "explanation": "ok"},
                {"symbol": "BBB", "pattern": "Bull Flag", "tier": "SKIP", "score": 0,
                 "pivot": 100, "target": 110, "stop_loss": 80, "tradable": False,
                 "skip_reason": "LOW_PATTERN_QUALITY",
                 "filters": {}, "breakdown": {}, "explanation": "ok"},
            ],
            "errors": [],
        }
        normalized = build_dashboard_context(ctx)
        symbols = [r["symbol"] for r in normalized["results"]]
        self.assertIn("AAA", symbols)
        self.assertNotIn("BBB", symbols)
        self.assertEqual(normalized["skipped_count"], 1)
        # No SKIP tier group should be exposed to the template
        tier_names = [g["name"] for g in normalized["tier_groups"]]
        self.assertNotIn("SKIP", tier_names)

    def test_dashboard_uses_scan_date_entry_for_visible_levels(self):
        from engine.dashboard import build_dashboard_context
        ctx = {
            "generated_at": "2026-05-21",
            "market_regime": {"score": 4, "verdict": "CONFIRMED UPTREND", "checks": {}, "details": {}},
            "results": [
                {"symbol": "AAA", "pattern": "Bull Flag", "tier": "HIGH", "score": 82,
                 "pivot": 100, "entry_price": 112, "target": 142, "stop_loss": 96,
                 "reward_risk": 1.88, "tradable": True,
                 "filters": {}, "breakdown": {}, "explanation": "ok"},
            ],
            "errors": [],
        }
        result = build_dashboard_context(ctx)["results"][0]

        self.assertEqual(result["pivot_text"], "Rs.100")
        self.assertEqual(result["entry_text"], "Rs.112")
        self.assertEqual(result["risk_reward"], "1.88:1")

    def test_actionable_rr_skip_reason_is_bucketed(self):
        from engine.dashboard import build_dashboard_context
        ctx = {
            "generated_at": "2026-05-21",
            "market_regime": {"score": 4, "verdict": "CONFIRMED UPTREND", "checks": {}, "details": {}},
            "results": [
                {"symbol": "LATE", "pattern": "Bull Flag", "tier": "SKIP", "score": 0,
                 "pivot": 100, "entry_price": 118, "target": 130, "stop_loss": 90,
                 "tradable": False, "skip_reason": "ACTIONABLE_REWARD_RISK_BELOW_FLOOR_0.43",
                 "filters": {}, "breakdown": {}, "explanation": "ok"},
            ],
            "errors": [],
        }
        breakdown = build_dashboard_context(ctx)["skip_breakdown"]

        self.assertEqual(breakdown[0]["key"], "ACTIONABLE_REWARD_RISK_BELOW_FLOOR")
        self.assertEqual(breakdown[0]["label"], "Future reward / risk below 1.5:1")

    def test_dashboard_volume_chip_shows_timeframe_ratio_and_numbers(self):
        from engine.dashboard import build_dashboard_context
        ctx = {
            "generated_at": "2026-05-21",
            "scan_timeframe": "weekly",
            "market_regime": {"score": 4, "verdict": "CONFIRMED UPTREND", "checks": {}, "details": {}},
            "results": [
                {
                    "symbol": "VOL",
                    "pattern": "Weekly Breakout",
                    "status": "BREAKING OUT",
                    "timeframe": "weekly",
                    "tier": "HIGH",
                    "score": 82,
                    "pivot": 100,
                    "entry_price": 102,
                    "target": 130,
                    "stop_loss": 90,
                    "tradable": True,
                    "filters": {
                        "volume": {
                            "passed": True,
                            "status": "PASS",
                            "details": {
                                "timeframe": "weekly",
                                "latest_volume": 250_000,
                                "avg_volume": 100_000,
                                "breakout_volume_ratio": 2.5,
                            },
                        },
                        "daily_volume": {
                            "passed": False,
                            "status": "FAIL",
                            "details": {
                                "timeframe": "daily",
                                "latest_volume": 50_000,
                                "avg_volume": 125_000,
                                "breakout_volume_ratio": 0.4,
                            },
                        },
                    },
                    "breakdown": {},
                    "explanation": "ok",
                },
            ],
            "errors": [],
        }

        result = build_dashboard_context(ctx)["results"][0]
        volume_rows = {row["key"]: row for row in result["filters"]}

        self.assertEqual(volume_rows["volume"]["label"], "Weekly Volume")
        self.assertIn("2.50x", volume_rows["volume"]["display"])
        self.assertIn("2.50L", volume_rows["volume"]["display"])
        self.assertEqual(volume_rows["daily_volume"]["label"], "Daily Volume")
        self.assertIn("0.40x", volume_rows["daily_volume"]["display"])


if __name__ == "__main__":
    unittest.main()
