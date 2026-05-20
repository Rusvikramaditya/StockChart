"""Phase 8 backtest engine tests."""

from __future__ import annotations

import ast
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from backtest import __main__ as backtest_cli
from backtest.analyze import analyze_reports, parse_report, render_markdown
from backtest.engine import run_backtest, track_trade_forward
from backtest.metrics import BacktestResult
from backtest.report import render_report, write_report
from engine.explainer import attach_explanation
from engine import storage
from engine import data_loader as data_loader_module
from engine.data_loader import DataLoader
from engine.scorer import score_pattern
from patterns import ALL_DETECTORS
from patterns.base import PatternResult


class DataLoaderBacktestSliceTest(unittest.TestCase):
    def test_daily_and_weekly_up_to_do_not_return_future_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = storage.connect(Path(tmp) / "test.db")
            storage.ensure_schema(conn)
            try:
                storage.upsert_daily_rows(conn, "TEST", "100", _daily_rows())
                storage.upsert_weekly_rows(conn, "TEST", _weekly_rows())
            finally:
                conn.close()

            loader = DataLoader(Path(tmp) / "test.db")
            try:
                daily = loader.get_daily_up_to("TEST", "2026-01-03")
                weekly = loader.get_weekly_up_to("TEST", "2026-01-09")
                future = loader.get_stock_daily_after("TEST", "2026-01-03", limit=2)
            finally:
                loader.close()

        self.assertEqual(daily["date"].tolist(), ["2026-01-01", "2026-01-02", "2026-01-03"])
        self.assertEqual(weekly["week"].tolist(), ["2026-01-02", "2026-01-09"])
        self.assertEqual(future["date"].tolist(), ["2026-01-04", "2026-01-05"])

    def test_get_trading_days_chunks_large_symbol_sets(self):
        symbols = [f"SYM{idx:04d}" for idx in range(1205)]
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            conn = storage.connect(db_path)
            storage.ensure_schema(conn)
            try:
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO ohlcv_daily
                    (symbol, security_id, date, open, high, low, close, volume)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (symbol, str(100 + idx), "2026-01-01", 100.0, 101.0, 99.0, 100.0, 1000)
                        for idx, symbol in enumerate(symbols)
                    ],
                )
                conn.commit()
            finally:
                conn.close()

            loader = DataLoader(db_path)
            chunk_sizes: list[int] = []
            original_chunks = data_loader_module._chunks

            def tracked_chunks(items, size):
                for chunk in original_chunks(items, size):
                    chunk_sizes.append(len(chunk))
                    yield chunk

            try:
                with patch("engine.data_loader._chunks", side_effect=tracked_chunks):
                    days = loader.get_trading_days(symbols)
            finally:
                loader.close()

        self.assertEqual(days, ["2026-01-01"])
        self.assertEqual(chunk_sizes, [800, 405])


class TradeTrackingPhase8Test(unittest.TestCase):
    def test_track_trade_forward_uses_first_target_or_stop_conservatively(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            conn = storage.connect(db_path)
            storage.ensure_schema(conn)
            try:
                storage.upsert_daily_rows(conn, "WIN", "100", _daily_rows_for_outcome("target"))
                storage.upsert_daily_rows(conn, "LOSS", "101", _daily_rows_for_outcome("stop"))
            finally:
                conn.close()

            loader = DataLoader(db_path)
            try:
                win = track_trade_forward(loader, "WIN", "2026-01-01", _scored(), entry_mode="next_open", max_hold_days=5)
                loss = track_trade_forward(loader, "LOSS", "2026-01-01", _scored(), entry_mode="next_open", max_hold_days=5)
            finally:
                loader.close()

        self.assertEqual(win["result"], "WIN")
        self.assertEqual(win["exit_price"], 110.0)
        self.assertEqual(loss["result"], "LOSS")
        self.assertEqual(loss["exit_price"], 95.0)

    def test_track_trade_forward_skips_next_open_when_trade_plan_is_already_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            conn = storage.connect(db_path)
            storage.ensure_schema(conn)
            try:
                storage.upsert_daily_rows(conn, "GAP", "102", _daily_rows_with_gap_above_target())
            finally:
                conn.close()

            loader = DataLoader(db_path)
            try:
                trade = track_trade_forward(
                    loader,
                    "GAP",
                    "2026-01-01",
                    _scored(),
                    entry_mode="next_open",
                    max_hold_days=5,
                )
            finally:
                loader.close()

        self.assertIsNone(trade)


class BacktestMetricsReportPhase8Test(unittest.TestCase):
    def test_metrics_cover_pattern_tier_filters_stack_and_report_sections(self):
        result = BacktestResult(
            [
                {
                    "pattern": "Ascending Triangle",
                    "tier": "HIGH",
                    "stacked_count": 2,
                    "result": "WIN",
                    "return_pct": 10.0,
                    "hold_days": 5,
                    "max_drawdown_pct": -2.0,
                    "entry_date": "2026-01-01",
                    "exit_date": "2026-01-06",
                    "score": 75,
                    "pattern_quality_score": 82,
                    "filters": {"stage2": {"passed": True}},
                },
                {
                    "pattern": "Bull Flag",
                    "tier": "MEDIUM",
                    "stacked_count": 1,
                    "result": "LOSS",
                    "return_pct": -5.0,
                    "hold_days": 3,
                    "max_drawdown_pct": -5.0,
                    "entry_date": "2026-02-01",
                    "exit_date": "2026-02-04",
                    "score": 55,
                    "pattern_quality_score": 58,
                    "filters": {"stage2": {"passed": False}},
                },
            ],
            universe="test",
        )

        self.assertEqual(result.summary["trades"], 2)
        self.assertEqual(result.summary["win_rate"], 50.0)
        self.assertEqual(result.summary["profit_factor"], 2.0)
        self.assertEqual({row["group"] for row in result.by_pattern}, {"Ascending Triangle", "Bull Flag"})
        self.assertEqual({row["bucket"] for row in result.conviction_validation}, {"90+", "70-89", "50-69"})
        self.assertEqual({row["bucket"] for row in result.quality_validation}, {"80+", "65-79", "50-64", "<50"})
        self.assertEqual({row["bucket"] for row in result.stack_validation}, {"1 pattern", "2 stacked"})

        html = render_report(result)
        self.assertIn("Summary By Pattern", html)
        self.assertIn("Conviction Tier Validation", html)
        self.assertIn("Quality Score Validation", html)
        self.assertIn("Filter Impact", html)
        self.assertIn("Stack Validation", html)
        self.assertIn("Equity Curve", html)
        self.assertIn("Monthly Returns", html)

    def test_write_report_creates_backtest_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "backtest.html"
            written = write_report(BacktestResult([], universe="test"), path)
            html = written.read_text(encoding="utf-8")

        self.assertEqual(written, path)
        self.assertIn("Pattern Finder Backtest", html)


class BacktestAnalysisPhase8Test(unittest.TestCase):
    def test_report_analysis_flags_failed_conviction_and_stack_bonus(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nifty.html"
            result = BacktestResult(_analysis_fixture_trades(), universe="nifty500")
            write_report(result, path)

            parsed = parse_report(path)
            analysis = analyze_reports([path])
            markdown = render_markdown(analysis)

        self.assertEqual(parsed["universe"], "nifty500")
        self.assertEqual(analysis["conviction"]["recommendation"], "retune conviction weights")
        self.assertEqual(
            analysis["stack_bonus"]["recommendation"],
            "remove stack score bonus but keep stacked pattern visibility",
        )
        self.assertEqual(analysis["quality_score"]["recommendation"], "retune quality score traits")
        self.assertIn("Weak Pattern", analysis["pattern_candidates"]["remove"])
        self.assertIn("Phase 8 Backtest Tuning Analysis", markdown)
        self.assertIn("Quality Score Validation", markdown)

    def test_watchlist_only_analysis_does_not_claim_core_rules_are_validated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlist.html"
            result = BacktestResult(_analysis_fixture_trades(), universe="watchlist")
            write_report(result, path)

            analysis = analyze_reports([path])

        self.assertEqual(analysis["conviction"]["recommendation"], "insufficient core evidence")
        self.assertEqual(analysis["quality_score"]["recommendation"], "insufficient core evidence")
        self.assertEqual(analysis["stack_bonus"]["recommendation"], "insufficient core evidence")

    def test_quality_analysis_uses_nearest_sampled_lower_bucket(self):
        trades = []
        for idx in range(40):
            trades.append(_analysis_trade(idx, "High Quality", 90, 1, "WIN" if idx < 34 else "LOSS"))
        for idx in range(40, 80):
            trades.append(_analysis_trade(idx, "Medium Quality", 70, 1, "WIN" if idx < 50 else "LOSS"))

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nifty.html"
            write_report(BacktestResult(trades, universe="nifty500"), path)

            analysis = analyze_reports([path])

        row = analysis["quality_score"]["by_universe"][0]
        self.assertEqual(row["low_bucket"], "65-79")
        self.assertEqual(analysis["quality_score"]["recommendation"], "raise minimum tradable quality score to 80")

    def test_quality_analysis_accepts_active_quality_gate(self):
        trades = []
        for idx in range(40):
            trades.append(_analysis_trade(idx, "High Quality", 90, 1, "WIN" if idx < 32 else "LOSS"))

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nifty.html"
            write_report(BacktestResult(trades, universe="nifty500"), path)

            analysis = analyze_reports([path])

        row = analysis["quality_score"]["by_universe"][0]
        self.assertEqual(row["status"], "GATED")
        self.assertEqual(analysis["quality_score"]["recommendation"], "keep minimum tradable quality score")

    def test_large_sample_loser_detectors_are_removed_from_live_registry(self):
        detector_modules = {detector.__module__ for detector in ALL_DETECTORS}

        self.assertNotIn("patterns.ascending_triangle", detector_modules)
        self.assertNotIn("patterns.bull_flag", detector_modules)
        self.assertNotIn("patterns.supertrend", detector_modules)
        self.assertIn("patterns.cup_handle", detector_modules)
        self.assertIn("patterns.inv_head_shoulders", detector_modules)
        self.assertIn("patterns.multiyear_breakout", detector_modules)


class QualityScorePhase8bTest(unittest.TestCase):
    def test_pattern_result_serializes_quality_score(self):
        pattern = PatternResult(
            pattern="VCP",
            status="PIVOT READY",
            pivot=100.0,
            target=120.0,
            stop_loss=94.0,
            confidence=92.0,
            explanation="Quality score fixture.",
            timeframe="daily",
            bars_in_pattern=90,
            quality_score=66.5,
        )

        self.assertEqual(pattern.as_dict()["quality_score"], 66.5)

    def test_scorer_uses_quality_score_buckets_instead_of_linear_confidence(self):
        pattern = PatternResult(
            pattern="VCP",
            status="PIVOT READY",
            pivot=100.0,
            target=120.0,
            stop_loss=94.0,
            confidence=100.0,
            explanation="Quality score fixture.",
            timeframe="daily",
            bars_in_pattern=90,
            quality_score=64.0,
        )
        daily = {
            "open": [100.0],
            "high": [101.0],
            "low": [99.0],
            "close": [100.0],
            "volume": [1000.0],
        }
        weekly = dict(daily)

        with (
            patch("engine.scorer.stage2.evaluate", return_value={"passed": False, "status": "NO_STAGE2"}),
            patch("engine.scorer.volume.evaluate", return_value={"passed": False, "status": "NO_VOLUME", "details": {}}),
            patch("engine.scorer.sector_rs.evaluate", return_value={"status": "LAGGING"}),
            patch(
                "engine.scorer.rsi.evaluate",
                return_value={"penalty": 0, "value": 55, "status": "HEALTHY", "bearish_divergence": False},
            ),
        ):
            scored = score_pattern(
                "TEST",
                pattern,
                daily,
                weekly,
                {"score": 4, "verdict": "CONFIRMED UPTREND"},
                {},
            )

        self.assertEqual(scored["breakdown"]["pattern"], 15)
        self.assertEqual(scored["breakdown"]["pattern_quality_score"], 64.0)
        self.assertEqual(scored["breakdown"]["pattern_confidence"], 100.0)
        self.assertEqual(scored["score"], 0)
        self.assertEqual(scored["tier"], "SKIP")
        self.assertEqual(scored["skip_reason"], "LOW_PATTERN_QUALITY")

    def test_scorer_allows_backtest_proven_high_quality_bucket(self):
        pattern = PatternResult(
            pattern="VCP",
            status="PIVOT READY",
            pivot=100.0,
            target=120.0,
            stop_loss=94.0,
            confidence=70.0,
            explanation="Quality score fixture.",
            timeframe="daily",
            bars_in_pattern=90,
            quality_score=80.0,
        )
        daily = {
            "open": [100.0],
            "high": [101.0],
            "low": [99.0],
            "close": [100.0],
            "volume": [1000.0],
        }
        weekly = dict(daily)

        with (
            patch("engine.scorer.stage2.evaluate", return_value={"passed": False, "status": "NO_STAGE2"}),
            patch("engine.scorer.volume.evaluate", return_value={"passed": False, "status": "NO_VOLUME", "details": {}}),
            patch("engine.scorer.sector_rs.evaluate", return_value={"status": "LAGGING"}),
            patch(
                "engine.scorer.rsi.evaluate",
                return_value={"penalty": 0, "value": 55, "status": "HEALTHY", "bearish_divergence": False},
            ),
        ):
            scored = score_pattern(
                "TEST",
                pattern,
                daily,
                weekly,
                {"score": 4, "verdict": "CONFIRMED UPTREND"},
                {},
            )

        self.assertEqual(scored["breakdown"]["pattern"], 25)
        self.assertEqual(scored["score"], 25)
        self.assertEqual(scored["skip_reason"], None)

    def test_explanation_marks_zero_weight_factors_disabled(self):
        pattern = PatternResult(
            pattern="VCP",
            status="PIVOT READY",
            pivot=100.0,
            target=120.0,
            stop_loss=94.0,
            confidence=70.0,
            explanation="Quality score fixture.",
            timeframe="daily",
            bars_in_pattern=90,
            quality_score=80.0,
        )
        daily = {
            "open": [100.0],
            "high": [101.0],
            "low": [99.0],
            "close": [100.0],
            "volume": [1000.0],
        }
        weekly = dict(daily)

        with (
            patch("engine.scorer.stage2.evaluate", return_value={"passed": False, "status": "NO_STAGE2"}),
            patch("engine.scorer.volume.evaluate", return_value={"passed": False, "status": "NO_VOLUME", "details": {}}),
            patch("engine.scorer.sector_rs.evaluate", return_value={"status": "LAGGING"}),
            patch(
                "engine.scorer.rsi.evaluate",
                return_value={"penalty": 0, "value": 55, "status": "HEALTHY", "bearish_divergence": False},
            ),
        ):
            scored = score_pattern(
                "TEST",
                pattern,
                daily,
                weekly,
                {"score": 4, "verdict": "CONFIRMED UPTREND"},
                {},
            )

        explanation = attach_explanation(scored)["explanation"]
        self.assertIn("Volume: disabled (NO_VOLUME)", explanation)
        self.assertIn("Market regime: disabled (CONFIRMED UPTREND)", explanation)
        self.assertNotIn("/0", explanation)

    def test_detector_results_include_quality_score_field(self):
        detector_paths = [
            "patterns/ascending_triangle.py",
            "patterns/bull_flag.py",
            "patterns/cup_handle.py",
            "patterns/inv_head_shoulders.py",
            "patterns/multiyear_breakout.py",
            "patterns/supertrend.py",
            "patterns/vcp.py",
        ]

        for path in detector_paths:
            text = (Path(__file__).resolve().parent.parent / path).read_text(encoding="utf-8")
            tree = ast.parse(text, filename=path)
            result_calls = [
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "PatternResult"
            ]

            self.assertGreater(len(result_calls), 0, path)
            for call in result_calls:
                keyword_names = {keyword.arg for keyword in call.keywords}
                self.assertIn("quality_score", keyword_names, path)


class BacktestCliPhase8Test(unittest.TestCase):
    def test_multi_universe_cli_continues_after_unavailable_profile(self):
        def fake_run_backtest(*, universe, **_kwargs):
            if universe == "recent_listings":
                raise ValueError("recent listings unavailable")
            return BacktestResult([], universe=universe)

        with (
            patch("backtest.__main__.run_backtest", side_effect=fake_run_backtest),
            patch("backtest.__main__.write_report", return_value=Path("watchlist.html")) as write_report_mock,
        ):
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = backtest_cli.main([
                    "--universe",
                    "recent_listings",
                    "--universe",
                    "watchlist",
                ])

        self.assertEqual(code, 1)
        self.assertIn("recent_listings: FAILED", stdout.getvalue())
        write_report_mock.assert_called_once()


class WalkforwardLoopPhase8Test(unittest.TestCase):
    def test_run_backtest_uses_sliced_detector_data_and_future_only_for_outcome(self):
        fake_loader = _FakeBacktestLoader()
        scored_daily_max_dates: list[str] = []

        def tracking_score_hits(symbol, daily, weekly, regime, sector):
            scored_daily_max_dates.append(str(pd.Timestamp(daily["date"][-1]).date()))
            return _fake_score_hits(symbol, daily, weekly, regime, sector)

        with (
            patch("backtest.engine.DataLoader", return_value=fake_loader),
            patch("backtest.engine.compute_market_regime", return_value={"score": 4, "verdict": "CONFIRMED UPTREND"}),
            patch("backtest.engine.compute_sector_rs_cache", return_value={}),
            patch("backtest.engine._score_hits", side_effect=tracking_score_hits),
        ):
            result = run_backtest(
                universe="test",
                min_history_rows=3,
                min_conviction=50,
                max_hold_days=3,
                entry_mode="next_open",
                end_date="2026-01-04",
            )

        self.assertGreaterEqual(result.summary["trades"], 1)
        self.assertIn("2026-01-03", scored_daily_max_dates)
        self.assertIn("2026-01-04", scored_daily_max_dates)
        self.assertNotIn("2026-01-05", scored_daily_max_dates)
        self.assertEqual(fake_loader.stock_daily_calls, ["TEST"])


def _daily_rows() -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=5, freq="D")
    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "open": [100, 101, 102, 103, 104],
            "high": [101, 102, 103, 104, 105],
            "low": [99, 100, 101, 102, 103],
            "close": [100, 101, 102, 103, 104],
            "volume": 1000,
        }
    )


def _weekly_rows() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "week": ["2026-01-02", "2026-01-09", "2026-01-16"],
            "open": [100, 105, 110],
            "high": [105, 110, 115],
            "low": [98, 102, 108],
            "close": [104, 109, 114],
            "volume": [1000, 1000, 1000],
        }
    )


def _daily_rows_for_outcome(kind: str) -> pd.DataFrame:
    if kind == "target":
        highs = [101, 111, 112]
        lows = [99, 100, 101]
    else:
        highs = [101, 102, 103]
        lows = [99, 94, 93]
    return pd.DataFrame(
        {
            "date": ["2026-01-01", "2026-01-02", "2026-01-03"],
            "open": [100, 100, 100],
            "high": highs,
            "low": lows,
            "close": [100, 100, 100],
            "volume": 1000,
        }
    )


def _daily_rows_with_gap_above_target() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": ["2026-01-01", "2026-01-02", "2026-01-03"],
            "open": [100, 120, 121],
            "high": [101, 122, 123],
            "low": [99, 118, 119],
            "close": [100, 121, 122],
            "volume": 1000,
        }
    )


def _scored() -> dict:
    return {
        "pattern": "Test Pattern",
        "pivot": 100.0,
        "target": 110.0,
        "stop_loss": 95.0,
        "score": 70,
        "tier": "HIGH",
        "pattern_result": None,
    }


def _analysis_fixture_trades() -> list[dict]:
    trades: list[dict] = []
    for idx in range(40):
        trades.append(_analysis_trade(idx, "Weak Pattern", 95, 3, "WIN" if idx < 10 else "LOSS"))
    for idx in range(40, 80):
        trades.append(_analysis_trade(idx, "Strong Pattern", 55, 1, "WIN" if idx < 70 else "LOSS"))
    return trades


def _analysis_trade(idx: int, pattern: str, score: int, stack_count: int, result: str) -> dict:
    return {
        "symbol": f"T{idx}",
        "pattern": pattern,
        "tier": "HIGHEST" if score >= 90 else "MEDIUM",
        "stacked_count": stack_count,
        "result": result,
        "return_pct": 10.0 if result == "WIN" else -5.0,
        "hold_days": 5,
        "max_drawdown_pct": -3.0,
        "entry_date": "2026-01-01",
        "exit_date": "2026-01-06",
        "score": score,
        "pattern_quality_score": score,
        "filters": {},
    }


class _FakeBacktestLoader:
    def __init__(self):
        self.frame = _daily_rows()
        self.weekly = _weekly_rows()
        self.index = _daily_rows()
        self.stock_daily_calls = []

    def get_symbols_for_universe(self, _universe):
        return ["TEST"]

    def get_trading_days(self, _symbols, start_date=None, end_date=None):
        return ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"]

    def get_stock_daily(self, symbol):
        self.stock_daily_calls.append(symbol)
        return self.frame.copy()

    def get_stock_weekly(self, _symbol):
        return self.weekly.copy()

    def get_index(self, _index_name):
        return self.index.copy()

    def get_daily_up_to(self, symbol, as_of):
        return self.frame[self.frame["date"] <= as_of].copy()

    def get_weekly_up_to(self, _symbol, _as_of):
        return pd.DataFrame(columns=["week", "open", "high", "low", "close", "volume"])

    def get_stock_daily_after(self, _symbol, after_date, *, limit):
        return self.frame[self.frame["date"] > after_date].head(limit).copy()

    def close(self):
        pass


def _fake_score_hits(symbol, daily, _weekly, _regime, _sector):
    if len(daily["close"]) < 3:
        return []
    return [
        {
            "symbol": symbol,
            "pattern": "Test Pattern",
            "status": "PIVOT READY",
            "pivot": 100.0,
            "target": 104.0,
            "stop_loss": 98.0,
            "score": 70,
            "tier": "HIGH",
            "stacked_count": 1,
            "all_patterns": ["Test Pattern"],
            "filters": {},
            "pattern_result": None,
        }
    ]


if __name__ == "__main__":
    unittest.main()
