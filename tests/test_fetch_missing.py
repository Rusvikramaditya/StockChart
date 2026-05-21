"""Tests for engine.fetch_missing planning + per-symbol fallback wiring."""

from __future__ import annotations

import argparse
import unittest
from unittest.mock import patch

import pandas as pd

from engine import fetch_missing
from engine.fetch_missing import (
    Coverage,
    FetchResult,
    PlanRow,
    build_fetch_plan,
    fetch_missing_for_profile,
)


def _profile(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


class BuildFetchPlanTest(unittest.TestCase):

    def test_already_downloaded_symbols_skipped(self):
        profile = _profile(
            [
                {"symbol": "AAA", "security_id": "1", "exchange_segment": "NSE_EQ", "instrument": "EQUITY"},
                {"symbol": "BBB", "security_id": "2", "exchange_segment": "NSE_EQ", "instrument": "EQUITY"},
            ]
        )
        coverage = {
            "AAA": Coverage(rows=1500, earliest="2020-01-01", latest="2026-05-21"),
            "BBB": Coverage(rows=1500, earliest="2020-01-01", latest="2026-05-21"),
        }
        planned, skipped = build_fetch_plan(
            profile, coverage, min_rows=200, to_date="2026-05-21", require_latest_date=True
        )
        self.assertEqual(planned, [])
        self.assertEqual([r.symbol for r in skipped], ["AAA", "BBB"])
        self.assertTrue(all(r.reason == "already_downloaded" for r in skipped))

    def test_stale_latest_requires_refetch(self):
        profile = _profile([{"symbol": "AAA", "security_id": "1", "exchange_segment": "NSE_EQ", "instrument": "EQUITY"}])
        coverage = {"AAA": Coverage(rows=2000, earliest="2020-01-01", latest="2026-04-01")}
        planned, skipped = build_fetch_plan(
            profile, coverage, min_rows=200, to_date="2026-05-21", require_latest_date=True
        )
        self.assertEqual(len(planned), 1)
        self.assertEqual(planned[0].symbol, "AAA")
        self.assertTrue(planned[0].reason.startswith("stale_latest"))

    def test_missing_security_id_skipped(self):
        profile = _profile(
            [{"symbol": "AAA", "security_id": "", "exchange_segment": "NSE_EQ", "instrument": "EQUITY"}]
        )
        planned, skipped = build_fetch_plan(
            profile, {}, min_rows=200, to_date="2026-05-21", require_latest_date=True
        )
        self.assertEqual(planned, [])
        self.assertEqual(skipped[0].reason, "missing_security_id")

    def test_missing_symbol_requires_fetch(self):
        profile = _profile([{"symbol": "AAA", "security_id": "1", "exchange_segment": "NSE_EQ", "instrument": "EQUITY"}])
        planned, _ = build_fetch_plan(
            profile, {}, min_rows=200, to_date="2026-05-21", require_latest_date=True
        )
        self.assertEqual(len(planned), 1)
        self.assertEqual(planned[0].reason, "missing")


class FetchMissingDryRunTest(unittest.TestCase):

    def test_execute_false_returns_planned_without_dhan_calls(self):
        profile = _profile(
            [{"symbol": "AAA", "security_id": "1", "exchange_segment": "NSE_EQ", "instrument": "EQUITY"}]
        )
        with patch.object(fetch_missing, "coverage_for_symbols", return_value={}):
            summary = fetch_missing_for_profile(
                conn=None,
                profile=profile,
                to_date="2026-05-21",
                from_date="2021-05-21",
                execute=False,
            )
        self.assertEqual(summary.planned, 1)
        self.assertEqual(summary.success, 0)
        self.assertEqual(summary.failed_symbols, [])

    def test_empty_profile_returns_zero_planned(self):
        summary = fetch_missing_for_profile(
            conn=None,
            profile=pd.DataFrame(columns=["symbol", "security_id", "exchange_segment", "instrument"]),
            execute=True,
        )
        self.assertEqual(summary.planned, 0)
        self.assertEqual(summary.failed_symbols, [])

    def test_per_symbol_failures_collected(self):
        profile = _profile(
            [
                {"symbol": "AAA", "security_id": "1", "exchange_segment": "NSE_EQ", "instrument": "EQUITY"},
                {"symbol": "BBB", "security_id": "2", "exchange_segment": "NSE_EQ", "instrument": "EQUITY"},
            ]
        )
        # Simulate Dhan returning success for AAA, failure for BBB
        fake_results = [
            FetchResult(
                symbol="AAA", security_id="1", status="success", rows_written=10,
                existing_rows=0, existing_latest="", reason="missing",
            ),
            FetchResult(
                symbol="BBB", security_id="2", status="failed", rows_written=0,
                existing_rows=0, existing_latest="", reason="missing", error="HTTP 500",
            ),
        ]
        with patch.object(fetch_missing, "coverage_for_symbols", return_value={}), \
             patch.object(fetch_missing, "fetch_planned", return_value=fake_results):
            summary = fetch_missing_for_profile(
                conn=None, profile=profile, execute=True,
            )
        self.assertEqual(summary.success, 1)
        self.assertEqual(summary.failed, 1)
        self.assertEqual(summary.failed_symbols, ["BBB"])
        self.assertEqual(summary.rows_written, 10)


class ScannerStageWiringTest(unittest.TestCase):
    """Verify the scanner's fetch_missing stage drops failed symbols (per-symbol
    fallback) and records errors without crashing the pipeline."""

    def test_stage_drops_failed_symbols(self):
        from scanner import Pipeline, PipelineContext

        ctx = PipelineContext(universe_name="watchlist")
        ctx.selected_profile = pd.DataFrame(
            [
                {"symbol": "AAA", "security_id": "1", "exchange_segment": "NSE_EQ", "instrument": "EQUITY"},
                {"symbol": "BBB", "security_id": "2", "exchange_segment": "NSE_EQ", "instrument": "EQUITY"},
            ]
        )
        ctx.symbols = ["AAA", "BBB"]

        class _FakeLoader:
            def __init__(self):
                self.conn = object()

        ctx.loader = _FakeLoader()
        fake_summary = fetch_missing.FetchSummary(
            planned=1, skipped=0, success=0, failed=1,
            failed_symbols=["BBB"], rows_written=0, results=[],
        )
        with patch("scanner.fetch_missing_for_profile", return_value=fake_summary):
            Pipeline(ctx).fetch_missing()
        self.assertEqual(ctx.symbols, ["AAA"])
        self.assertEqual(ctx.selected_profile["symbol"].tolist(), ["AAA"])
        # One non-critical error recorded for the dropped symbol
        self.assertTrue(any(e["symbol"] == "BBB" and e["stage"] == "fetch_missing" for e in ctx.errors))

    def test_stage_skipped_on_dry_run(self):
        from scanner import Pipeline, PipelineContext

        ctx = PipelineContext(universe_name="watchlist", dry_run=True)
        ctx.selected_profile = pd.DataFrame([{"symbol": "AAA", "security_id": "1"}])
        ctx.symbols = ["AAA"]
        Pipeline(ctx).fetch_missing()
        self.assertEqual(ctx.stats.get("fetch_missing"), "skipped")
        self.assertEqual(ctx.symbols, ["AAA"])  # untouched

    def test_stage_skipped_when_flag_off(self):
        from scanner import Pipeline, PipelineContext

        ctx = PipelineContext(universe_name="watchlist", fetch_missing=False)
        ctx.selected_profile = pd.DataFrame([{"symbol": "AAA", "security_id": "1"}])
        ctx.symbols = ["AAA"]
        Pipeline(ctx).fetch_missing()
        self.assertEqual(ctx.stats.get("fetch_missing"), "skipped")


class ScannerControlServerCommandTest(unittest.TestCase):

    def test_default_command_does_not_skip_backfill(self):
        from scripts.scanner_control_server import build_scan_command

        form = {"universe": ["nifty500"], "mode": ["live_with_telegram"], "workers": ["8"]}
        command, _ = build_scan_command(form)
        self.assertNotIn("--no-fetch-missing", command)

    def test_skip_backfill_checkbox_adds_flag(self):
        from scripts.scanner_control_server import build_scan_command

        form = {
            "universe": ["nifty500"],
            "mode": ["live_with_telegram"],
            "workers": ["8"],
            "skip_backfill": ["on"],
        }
        command, _ = build_scan_command(form)
        self.assertIn("--no-fetch-missing", command)


if __name__ == "__main__":
    unittest.main()
