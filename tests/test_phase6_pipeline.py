"""Phase 6 scanner pipeline and pattern dedup tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from engine import dhan_client, storage, universe
from engine.dedup import deduplicate_results
from patterns.base import PatternResult
from scanner import (
    Pipeline,
    PipelineContext,
    PipelineError,
    _generate_weekly_incremental,
    _symbol_chunks as _original_symbol_chunks,
    _validate_live_fetch_scope,
    parse_args,
    stage,
)


class DedupPhase6Test(unittest.TestCase):
    def test_dedup_picks_highest_score_and_preserves_stacked_patterns_without_bonus(self):
        primary = _scored("TEST", "Ascending Triangle", 72, target=120.0)
        secondary = _scored("TEST", "Bull Flag", 66, target=112.0)

        merged = deduplicate_results([secondary, primary])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["symbol"], "TEST")
        self.assertEqual(merged[0]["pattern"], "Ascending Triangle")
        self.assertEqual(merged[0]["target"], 120.0)
        self.assertEqual(merged[0]["individual_score"], 72)
        self.assertEqual(merged[0]["stack_bonus"], 0)
        self.assertEqual(merged[0]["score"], 72)
        self.assertEqual(merged[0]["tier"], "HIGH")
        self.assertEqual(merged[0]["stacked_count"], 2)
        self.assertEqual(merged[0]["all_patterns"], ["Ascending Triangle", "Bull Flag"])
        self.assertIn("Stacked patterns detected", merged[0]["explanation"])

    def test_stack_bonus_can_be_disabled_from_backtest_evidence(self):
        rows = [_scored("TEST", f"Pattern {idx}", 88) for idx in range(6)]

        merged = deduplicate_results(rows)

        self.assertEqual(merged[0]["stack_bonus"], 0)
        self.assertEqual(merged[0]["score"], 88)
        self.assertEqual(merged[0]["tier"], "HIGH")
        self.assertEqual(merged[0]["stacked_count"], 6)

    def test_three_pattern_stack_keeps_visibility_without_changing_score(self):
        stacked = deduplicate_results([
            _scored("STACK", "Ascending Triangle", 70),
            _scored("STACK", "Bull Flag", 68),
            _scored("STACK", "VCP", 66),
            _scored("SINGLE", "Cup & Handle", 70),
        ])
        by_symbol = {item["symbol"]: item for item in stacked}

        self.assertEqual(by_symbol["STACK"]["score"], 70)
        self.assertEqual(by_symbol["STACK"]["stack_bonus"], 0)
        self.assertEqual(by_symbol["SINGLE"]["score"], 70)
        self.assertEqual(by_symbol["SINGLE"]["stack_bonus"], 0)

    def test_dedup_keeps_separate_symbols(self):
        merged = deduplicate_results([
            _scored("AAA", "Ascending Triangle", 80),
            _scored("BBB", "Cup & Handle", 75),
        ])

        self.assertEqual([item["symbol"] for item in merged], ["AAA", "BBB"])


class StageDecoratorPhase6Test(unittest.TestCase):
    def test_noncritical_stage_records_error_without_raising(self):
        class Demo(Pipeline):
            @stage("optional", critical=False)
            def optional(self):
                raise ValueError("optional failed")

        ctx = PipelineContext()
        result = Demo(ctx).optional()

        self.assertIsNone(result)
        self.assertEqual(ctx.errors[0]["stage"], "optional")
        self.assertFalse(ctx.errors[0]["critical"])
        self.assertIn("optional", ctx.stage_timings)

    def test_critical_stage_records_and_raises(self):
        class Demo(Pipeline):
            @stage("critical", critical=True)
            def critical(self):
                raise PipelineError("critical failed")

        ctx = PipelineContext()
        with self.assertRaises(PipelineError):
            Demo(ctx).critical()
        self.assertTrue(ctx.errors[0]["critical"])


class PipelineVerifyPhase6Test(unittest.TestCase):
    def test_verify_loads_profile_and_reports_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = storage.connect(Path(tmp) / "test.db")
            storage.ensure_schema(conn)
            try:
                storage.upsert_daily_rows(conn, "TEST", "100", _daily_rows())
                storage.upsert_weekly_rows(conn, "TEST", _weekly_rows())
                storage.upsert_index_rows(conn, "NIFTY 50", _daily_rows())
                ctx = PipelineContext(loader=_FakeLoader(conn), universe_name="watchlist")

                Pipeline(ctx).verify()
            finally:
                conn.close()

        self.assertEqual(ctx.symbols, ["TEST"])
        self.assertEqual(ctx.stats["daily_coverage"], "1/1")
        self.assertEqual(ctx.stats["weekly_coverage"], "1/1")
        self.assertEqual(ctx.errors, [])

    def test_verify_chunks_large_universe_coverage_queries(self):
        symbols = [f"SYM{idx:04d}" for idx in range(1205)]
        with tempfile.TemporaryDirectory() as tmp:
            conn = storage.connect(Path(tmp) / "test.db")
            storage.ensure_schema(conn)
            try:
                _insert_one_row_per_symbol(conn, symbols)
                storage.upsert_index_rows(conn, "NIFTY 50", _daily_rows(rows=1))
                ctx = PipelineContext(loader=_FakeLoader(conn, symbols=symbols), universe_name="all_nse_equity")
                chunk_sizes: list[int] = []

                def tracked_chunks(items, size):
                    for chunk in _original_symbol_chunks(items, size):
                        chunk_sizes.append(len(chunk))
                        yield chunk

                with patch("scanner._symbol_chunks", side_effect=tracked_chunks):
                    Pipeline(ctx).verify()
            finally:
                conn.close()

        self.assertEqual(ctx.stats["daily_coverage"], "1205/1205")
        self.assertEqual(ctx.stats["weekly_coverage"], "1205/1205")
        self.assertGreater(len(chunk_sizes), 2)
        self.assertLessEqual(max(chunk_sizes), 800)

    def test_argparse_supports_required_phase6_flags(self):
        args = parse_args([
            "--universe",
            "watchlist",
            "--skip-fetch",
            "--stage",
            "detect",
            "--dry-run",
            "--workers",
            "1",
            "--scan-timeframe",
            "weekly",
            "--fetch-all-data",
            "--check-rebalance",
            "--refresh-universe",
            "--min-liquidity",
            "--limit",
            "5",
        ])

        self.assertEqual(args.universe, "watchlist")
        self.assertTrue(args.skip_fetch)
        self.assertEqual(args.stage, "detect")
        self.assertTrue(args.dry_run)
        self.assertEqual(args.workers, 1)
        self.assertEqual(args.scan_timeframe, "weekly")
        self.assertTrue(args.fetch_all_data)
        self.assertTrue(args.check_rebalance)
        self.assertTrue(args.refresh_universe)
        self.assertTrue(args.min_liquidity)
        self.assertEqual(args.limit, 5)

    def test_friday_fetch_triggers_weekly_incremental(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = storage.connect(Path(tmp) / "test.db")
            storage.ensure_schema(conn)
            try:
                loader = _FakeLoader(conn)
                ctx = PipelineContext(
                    loader=loader,
                    universe_name="watchlist",
                    scan_date=pd.Timestamp("2026-05-22").date(),
                )
                ctx.selected_profile = loader.get_universe_profile("watchlist")
                pipeline = Pipeline(ctx)

                with patch("scanner._generate_weekly_incremental", return_value={"symbols_processed": 1}) as weekly:
                    pipeline.fetch()
            finally:
                conn.close()

        weekly.assert_called_once()
        self.assertEqual(ctx.stats["rows_fetched"], 1)
        self.assertEqual(ctx.stats["weekly_incremental"], {"symbols_processed": 1})

    def test_weekly_scan_fetch_refreshes_weekly_incremental_before_friday(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = storage.connect(Path(tmp) / "test.db")
            storage.ensure_schema(conn)
            try:
                loader = _FakeLoader(conn)
                ctx = PipelineContext(
                    loader=loader,
                    universe_name="watchlist",
                    scan_timeframe="weekly",
                    scan_date=pd.Timestamp("2026-05-20").date(),
                )
                ctx.selected_profile = loader.get_universe_profile("watchlist")
                pipeline = Pipeline(ctx)

                with patch("scanner._generate_weekly_incremental", return_value={"symbols_processed": 1}) as weekly:
                    pipeline.fetch()
            finally:
                conn.close()

        weekly.assert_called_once()
        self.assertEqual(ctx.stats["weekly_incremental"], {"symbols_processed": 1})

    def test_full_all_nse_live_fetch_is_allowed_when_not_cooling_down(self):
        with patch("scanner.dhan_client.raise_if_rate_limited"):
            _validate_live_fetch_scope("all_nse_equity", skip_fetch=False, limit=None)

    def test_live_fetch_scope_surfaces_active_dhan_cooldown(self):
        with patch(
            "scanner.dhan_client.raise_if_rate_limited",
            side_effect=dhan_client.DhanRateLimitError("cooling down"),
        ):
            with self.assertRaises(PipelineError):
                _validate_live_fetch_scope("small_mid_liquid", skip_fetch=False, limit=None)

    def test_weekly_incremental_generates_without_rebuilding_existing_weeks(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = storage.connect(Path(tmp) / "test.db")
            storage.ensure_schema(conn)
            try:
                storage.upsert_daily_rows(conn, "TEST", "100", _daily_rows(rows=20))

                first = _generate_weekly_incremental(conn)
                second = _generate_weekly_incremental(conn)
                weekly_count = conn.execute(
                    "SELECT COUNT(*) FROM ohlcv_weekly WHERE symbol = 'TEST'"
                ).fetchone()[0]
            finally:
                conn.close()

        self.assertGreater(first["weekly_rows_written"], 0)
        self.assertEqual(second["weekly_rows_written"], 0)
        self.assertGreater(weekly_count, 0)


class PipelineTelegramChartPhase6Test(unittest.TestCase):
    def test_alert_prefers_thesis_screenshot_png(self):
        with tempfile.TemporaryDirectory() as tmp:
            png_path = Path(tmp) / "thesis.png"
            png_path.write_bytes(b"png")
            ctx = PipelineContext(loader=_FakeLoader(None))
            scored = _scored("TEST", "Ascending Triangle", 75)
            scored["chart_payload"] = {"symbol": "TEST"}
            ctx.scored_results = [scored]
            pipeline = Pipeline(ctx)

            with (
                patch("scanner.export_thesis_chart_png") as export_mock,
                patch("scanner.generate_pattern_chart") as fallback_mock,
                patch("scanner.telegram.send_chart_alert", return_value=True) as send_mock,
            ):
                export_mock.return_value = {
                    "html_path": Path(tmp) / "thesis.html",
                    "png_path": png_path,
                    "stats": {"colored_pixels": 200},
                }

                sent = pipeline._send_alerts()

        self.assertEqual(sent, 1)
        send_mock.assert_called_once()
        self.assertEqual(Path(send_mock.call_args.args[1]), png_path)
        fallback_mock.assert_not_called()
        self.assertEqual(scored["chart_screenshot_path"], str(png_path))

    def test_alert_falls_back_to_mplfinance_png_when_thesis_screenshot_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            fallback_path = Path(tmp) / "fallback.png"
            fallback_path.write_bytes(b"png")
            ctx = PipelineContext(loader=_FakeLoader(None))
            scored = _scored("TEST", "Ascending Triangle", 75)
            scored["chart_payload"] = {"symbol": "TEST"}
            ctx.scored_results = [scored]
            pipeline = Pipeline(ctx)

            with (
                patch("scanner.export_thesis_chart_png", side_effect=RuntimeError("browser failed")),
                patch("scanner.generate_pattern_chart", return_value=fallback_path) as fallback_mock,
                patch("scanner.telegram.send_chart_alert", return_value=True) as send_mock,
            ):
                sent = pipeline._send_alerts()

        self.assertEqual(sent, 1)
        fallback_mock.assert_called_once()
        self.assertEqual(Path(send_mock.call_args.args[1]), fallback_path)
        self.assertEqual(ctx.errors[0]["stage"], "chart_screenshot")
        self.assertEqual(scored["chart_path"], str(fallback_path))

    def test_alert_cap_matches_dashboard_suggestion_count(self):
        ctx = PipelineContext(loader=_FakeLoader(None))
        ctx.scored_results = [
            _scored(f"TEST{idx}", "Ascending Triangle", 75)
            for idx in range(10)
        ]
        pipeline = Pipeline(ctx)

        with (
            patch.object(pipeline, "_alert_chart_path", return_value=None),
            patch("scanner.telegram.send_alert", return_value=True) as send_mock,
        ):
            sent = pipeline._send_alerts()

        self.assertEqual(sent, 9)
        self.assertEqual(send_mock.call_count, 9)

    def test_output_records_failed_telegram_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = PipelineContext(
                loader=_FakeLoader(None),
                output_path=Path(tmp) / "dashboard.html",
                send_telegram=True,
            )
            ctx.symbols = ["TEST"]
            ctx.market_regime = {"score": 4, "verdict": "CONFIRMED UPTREND"}
            pipeline = Pipeline(ctx)

            with patch("scanner.telegram.send_daily_summary", return_value=False):
                pipeline.output()

        self.assertFalse(ctx.stats["telegram_summary_sent"])
        self.assertEqual(ctx.errors[0]["stage"], "telegram")
        self.assertIn("TELEGRAM_BOT_TOKEN", ctx.errors[0]["message"])


class _FakeLoader:
    def __init__(self, conn, symbols: list[str] | None = None):
        self.conn = conn
        self.symbols = symbols or ["TEST"]

    def get_universe_profile(self, _name):
        return pd.DataFrame(
            [
                {
                    "symbol": symbol,
                    "company_name": f"{symbol} Ltd",
                    "security_id": str(100 + idx),
                    "exchange_segment": "NSE_EQ",
                    "instrument": "EQUITY",
                    "instrument_type": "ES",
                    "series": "EQ",
                    "lot_size": "1",
                    "listing_date": "",
                    "status": "ACTIVE",
                }
                for idx, symbol in enumerate(self.symbols)
            ],
            columns=universe.OUTPUT_COLUMNS,
        )

    def close(self):
        pass

    def get_stock_daily(self, _symbol):
        return _daily_rows()

    def fetch_todays_candles(self, _profile, universe_name="nifty500"):
        return 1


def _scored(symbol: str, pattern: str, score: int, *, target: float = 115.0) -> dict:
    pattern_result = PatternResult(
        pattern=pattern,
        status="PIVOT READY",
        pivot=100.0,
        target=target,
        stop_loss=95.0,
        confidence=80.0,
        explanation=f"{pattern} explanation.",
        timeframe="daily",
        bars_in_pattern=60,
    )
    return {
        "symbol": symbol,
        "pattern": pattern,
        "status": pattern_result.status,
        "pivot": pattern_result.pivot,
        "target": pattern_result.target,
        "stop_loss": pattern_result.stop_loss,
        "timeframe": pattern_result.timeframe,
        "pattern_result": pattern_result,
        "score": score,
        "tier": "HIGH",
        "tradable": True,
        "explanation": "Base explanation.",
    }


def _daily_rows(rows: int = 220) -> pd.DataFrame:
    dates = pd.date_range("2025-01-01", periods=rows, freq="D")
    close = pd.Series(range(rows), dtype=float) + 100.0
    return pd.DataFrame(
        {
            "date": dates,
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": 100_000,
        }
    )


def _weekly_rows(rows: int = 60) -> pd.DataFrame:
    weeks = pd.date_range("2025-01-03", periods=rows, freq="W-FRI")
    close = pd.Series(range(rows), dtype=float) + 100.0
    return pd.DataFrame(
        {
            "week": weeks.strftime("%Y-%m-%d"),
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": 500_000,
        }
    )


def _insert_one_row_per_symbol(conn, symbols: list[str]) -> None:
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
    conn.executemany(
        """
        INSERT OR REPLACE INTO ohlcv_weekly
        (symbol, week, open, high, low, close, volume)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [(symbol, "2026-01-02", 100.0, 101.0, 99.0, 100.0, 5000) for symbol in symbols],
    )
    conn.commit()


if __name__ == "__main__":
    unittest.main()
