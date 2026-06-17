"""Phase 5B contract tests for the Carbon Ember dashboard."""

from __future__ import annotations

import base64
import re
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

from engine.dashboard import build_dashboard_context, render_dashboard, write_dashboard
from engine.explainer import attach_explanation
from patterns.base import PatternResult


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8Xw8AAoMBgUeJ"
    "8wAAAABJRU5ErkJggg=="
)


class ImageCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.images = []
        self.links = []
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "img":
            self.images.append(attrs)
        elif tag == "link":
            self.links.append(attrs)
        elif tag == "script":
            self.scripts.append(attrs)


class DashboardPhase5BTest(unittest.TestCase):
    def test_render_dashboard_is_self_contained_and_has_required_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            chart_path = Path(tmp) / "chart.png"
            chart_path.write_bytes(PNG_1X1)
            html = render_dashboard(self._context(chart_path))

        self.assertIn("#0a0a0a", html)
        self.assertIn("#141414", html)
        self.assertIn("#ff4800", html)
        self.assertIn("Syne", html)
        self.assertIn("JetBrains Mono", html)
        self.assertIn('name="viewport"', html)
        self.assertIn("@media (max-width: 760px)", html)
        self.assertIn("Market Regime Panel", html)
        self.assertIn("Sector Heatmap", html)
        self.assertIn("Pattern Guide", html)
        self.assertIn("VCP", html)
        self.assertIn("How to read this chart", html)
        self.assertIn("Result Cards by Conviction Tier", html)
        self.assertIn('id="resultSearch"', html)
        self.assertIn("Search setups", html)
        self.assertIn('id="panelGlobalSearch"', html)
        self.assertIn("Global panel search", html)
        self.assertIn("initPanelSearch", html)
        self.assertIn("data-panel-search-scope", html)
        self.assertIn("Open thesis", html)
        self.assertIn("Watch checklist", html)
        self.assertIn("data-search-text=", html)
        self.assertIn("No setups match the current filters.", html)
        self.assertIn("2 patterns", html)
        self.assertIn("Also detected: Ascending Triangle, Bull Flag", html)
        self.assertIn("Errors Panel", html)
        self.assertIn("data:image/png;base64,", html)
        self.assertNotIn("{{", html)
        self.assertNotIn("{%", html)
        # No external resource loads are allowed. Screener company links are
        # allowed because they are user-clicked anchors, not dashboard assets.
        ext_refs = re.findall(r'(src|href)\s*=\s*["\'](https?://[^"\']+)', html, re.IGNORECASE)
        ext_srcs = [url for attr, url in ext_refs if attr.lower() == "src"]
        ext_hrefs = [url for attr, url in ext_refs if attr.lower() == "href"]
        self.assertEqual([], ext_srcs, "No external CDN or API resource attributes allowed")
        self.assertTrue(all(url.startswith("https://www.screener.in/company/") for url in ext_hrefs))
        self.assertIn('href="https://www.screener.in/company/TESTSTOCK/"', html)

        order = [
            "Pattern 101",
            "This Stock Specifically",
            "Action Plan",
            "Risk Note",
            "Conviction Breakdown",
        ]
        positions = [html.index(label) for label in order]
        self.assertEqual(positions, sorted(positions))

        parser = ImageCollector()
        parser.feed(html)
        self.assertEqual(len(parser.images), 1)
        self.assertTrue(parser.images[0]["src"].startswith("data:image/png;base64,"))
        self.assertEqual(parser.links, [])
        external_scripts = [script for script in parser.scripts if script.get("src")]
        self.assertEqual(external_scripts, [])

    def test_write_dashboard_creates_html_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            chart_path = Path(tmp) / "chart.png"
            chart_path.write_bytes(PNG_1X1)
            output_path = Path(tmp) / "dashboard.html"

            written = write_dashboard(self._context(chart_path), output_path)

            self.assertEqual(written, output_path)
            html = output_path.read_text(encoding="utf-8")
            self.assertIn("TESTSTOCK", html)
            self.assertIn("NIFTY IT", html)
            self.assertNotIn("chart.png", html)

    def test_default_dashboard_paths_do_not_collide(self):
        with tempfile.TemporaryDirectory() as tmp:
            chart_path = Path(tmp) / "chart.png"
            chart_path.write_bytes(PNG_1X1)

            first = write_dashboard(self._context(chart_path))
            second = write_dashboard(self._context(chart_path))

        try:
            self.assertNotEqual(first, second)
            self.assertTrue(first.name.startswith("dashboard_"))
            self.assertTrue(second.name.startswith("dashboard_"))
        finally:
            first.unlink(missing_ok=True)
            second.unlink(missing_ok=True)

    def test_zero_score_medium_tier_is_preserved(self):
        """A zero-score MEDIUM result must still flow through normalization.
        SKIP-tier results are filtered out entirely by build_dashboard_context
        (real-money rule: SKIP setups never reach the dashboard) - this test
        targets the more permissive MEDIUM case so the normalization logic
        is still exercised for low-confidence-but-tradable scenarios.
        """
        with tempfile.TemporaryDirectory() as tmp:
            chart_path = Path(tmp) / "chart.png"
            chart_path.write_bytes(PNG_1X1)
            context = self._context(chart_path)
            context["results"][0]["score"] = 0
            context["results"][0]["tier"] = "MEDIUM"
            context["results"][0]["cmp"] = 0

            normalized = build_dashboard_context(context)

            result = normalized["results"][0]
            self.assertEqual(result["score"], 0)
            self.assertEqual(result["tier"], "MEDIUM")
            self.assertEqual(result["cmp"], "Rs.0")

    def test_sector_filter_dropdown_and_card_data_attrs_present(self):
        """Result cards must carry data-sector/data-sector-tier and the controls
        bar must include a sector filter dropdown with leading-N presets."""
        with tempfile.TemporaryDirectory() as tmp:
            chart_path = Path(tmp) / "chart.png"
            chart_path.write_bytes(PNG_1X1)
            ctx = self._context(chart_path)
            ctx["sector_rs"] = {
                **(ctx.get("sector_rs") or {}),
                "symbol_to_sector": {
                    "TESTSTOCK": {"sector_index": "NIFTY IT", "industry": "IT"}
                },
            }
            ctx["sector_leaderboard"] = {
                "rows": [
                    {
                        "sector": "NIFTY IT", "rank": 1, "tier": "LEADING",
                        "composite_score": 78.4, "ret_1m_pct": 4.8,
                        "ret_3m_pct": 11.2, "ret_6m_pct": 18.5,
                        "rs_1m_pct": 2.1, "rs_3m_pct": 5.4, "rs_6m_pct": 6.8,
                        "stage2": True, "breadth_50dma_pct": 72.0,
                        "breadth_200dma_pct": 64.0, "constituents": 11,
                    },
                ],
            }
            html = render_dashboard(ctx)

        self.assertIn('id="sectorFilter"', html, "Sector filter dropdown missing")
        self.assertIn('value="__LEADING__"', html, "Leading-only preset missing")
        self.assertIn('value="__TOP3__"', html)
        self.assertIn('value="__TOP5__"', html)
        self.assertIn('data-sector="NIFTY IT"', html, "Result card missing data-sector")
        self.assertIn('data-sector-tier="LEADING"', html, "Result card missing data-sector-tier")
        self.assertIn("NIFTY IT | LEADING", html, "Sector chip not rendered on card")
        self.assertIn("Hot Sector", html, "Hot sector badge missing for leading sector")
        # Leaderboard panel itself
        self.assertIn("Sector Leaderboard", html)
        self.assertIn("lb-leading", html)

    def test_clear_filters_button_and_glossary_present(self):
        """Controls must include a Clear filters button + filter glossary."""
        with tempfile.TemporaryDirectory() as tmp:
            chart_path = Path(tmp) / "chart.png"
            chart_path.write_bytes(PNG_1X1)
            html = render_dashboard(self._context(chart_path))
        self.assertIn('id="clearFilters"', html)
        self.assertIn("filter-glossary", html)
        # Tier-count spans must be present so JS can update them live
        self.assertIn('data-tier-count="ALL"', html)
        self.assertIn('data-tier-count="HIGHEST"', html)
        # Glossary explains the chip statuses
        self.assertIn("Quiet base", html)
        self.assertIn("No live volume yet", html)
        self.assertIn("Constructive base", html)

    def test_filter_chips_use_plain_english_and_watch_state(self):
        context = self._context(Path("missing.png"))
        scored = context["results"][0]
        scored["filters"]["volume"] = {
            "passed": False,
            "status": "DRY_UP",
            "details": {
                "timeframe": "daily",
                "latest_volume": 0,
                "avg_volume": 80190,
                "breakout_volume_ratio": 0.0,
                "base_dry_up": True,
                "base_dry_up_ratio": 0.79,
            },
        }
        scored["filters"]["pocket_pivot"] = {
            "passed": False,
            "status": "NO_UP_CLOSE",
            "details": {"latest_volume": 0},
        }
        scored["filters"]["sector_rs"] = {"passed": False, "status": "NEUTRAL"}
        scored["filters"]["market_regime"] = {"score": 1, "verdict": "BEAR"}

        result = build_dashboard_context(context)["results"][0]
        chips = {row["key"]: row for row in result["filters"]}

        self.assertEqual(chips["volume"]["display"], "Daily Volume: No live volume yet - 0.00x (0 / 80.19K avg)")
        self.assertEqual(chips["volume"]["class_name"], "watch")
        self.assertEqual(chips["pocket_pivot"]["display"], "Pocket Pivot: No strong buying candle today")
        self.assertEqual(chips["pocket_pivot"]["class_name"], "watch")
        self.assertEqual(chips["sector_rs"]["display"], "Sector RS: Sector neutral")
        self.assertEqual(chips["sector_rs"]["class_name"], "watch")
        self.assertEqual(chips["market_regime"]["display"], "Regime: Market weak; be selective")
        self.assertEqual(chips["market_regime"]["class_name"], "fail")
        watch_text = " ".join(row["text"] for row in result["watch_checklist"])
        self.assertIn("Verify live volume before acting", watch_text)
        self.assertIn("market is weak", watch_text)

    def test_volume_panel_shows_recent_volume_and_king_candle_context(self):
        context = self._context(Path("missing.png"))
        scored = context["results"][0]
        scored["filters"]["volume"] = {
            "passed": True,
            "status": "PASS",
            "details": {
                "timeframe": "daily",
                "avg_period": 50,
                "latest_volume": 250_000,
                "latest_volume_date": "2026-05-29",
                "latest_volume_is_today": False,
                "avg_volume": 100_000,
                "avg_50d_volume": 100_000,
                "last_5_volumes": [90_000, 110_000, 125_000, 160_000, 250_000],
                "last_5_avg_volume": 147_000,
                "last_5_vs_avg_ratio": 1.47,
                "recent_volume_direction": "higher",
                "breakout_volume_ratio": 2.5,
            },
        }
        scored["filters"]["king_candle"] = {
            "passed": True,
            "status": "CONFIRMED",
            "details": {
                "observed": True,
                "candle_date": "2026-05-28",
                "king_high": 106.0,
                "king_midpoint": 103.25,
                "king_low": 100.5,
            },
        }

        html = render_dashboard(context)

        self.assertIn("Volume Context", html)
        self.assertIn("Latest candle volume", html)
        self.assertNotIn("Today volume", html)
        self.assertIn("50D avg", html)
        self.assertIn("5-day avg", html)
        self.assertIn("Last 5 daily volumes", html)
        self.assertIn("2.50L", html)
        self.assertIn("Recent daily volume is higher than average: 1.47x above 50D avg.", html)
        self.assertIn("King Candle observed as additional confirmation", html)
        self.assertIn("High Rs.106", html)

        scored["filters"]["volume"]["details"]["latest_volume_is_today"] = True
        self.assertIn("Today volume", render_dashboard(context))

    def test_weekly_volume_panel_keeps_daily_snapshot_separate(self):
        context = self._context(Path("missing.png"))
        scored = context["results"][0]
        scored["timeframe"] = "weekly"
        scored["filters"]["volume"] = {
            "passed": True,
            "status": "PASS",
            "details": {
                "timeframe": "weekly",
                "avg_period": 50,
                "latest_volume": 1_200_000,
                "latest_volume_date": "2026-05-29",
                "avg_volume": 800_000,
                "avg_50w_volume": 800_000,
                "last_5_volumes": [700_000, 760_000, 820_000, 900_000, 1_200_000],
                "last_5_avg_volume": 876_000,
                "last_5_vs_avg_ratio": 1.1,
                "recent_volume_direction": "higher",
            },
        }
        scored["filters"]["daily_volume"] = {
            "passed": False,
            "status": "FAIL",
            "details": {
                "timeframe": "daily",
                "avg_period": 50,
                "latest_volume": 120_000,
                "latest_volume_date": "2026-05-29",
                "latest_volume_is_today": False,
                "avg_volume": 200_000,
                "avg_50d_volume": 200_000,
                "last_5_volumes": [180_000, 150_000, 140_000, 130_000, 120_000],
                "last_5_avg_volume": 144_000,
                "last_5_vs_avg_ratio": 0.72,
                "recent_volume_direction": "lower",
            },
        }

        html = render_dashboard(context)

        self.assertLess(html.index("Weekly Volume"), html.index("Daily Volume Snapshot"))
        self.assertIn("50W avg", html)
        self.assertIn("Last 5 weekly volumes", html)
        self.assertIn("Latest candle volume", html)
        self.assertIn("50D avg", html)
        self.assertIn("Last 5 daily volumes", html)
        self.assertIn("Recent daily volume is lower than average: 0.72x of 50D avg.", html)

    def test_normalized_result_carries_sector_metadata(self):
        ctx = self._context(Path("missing.png"))
        ctx["sector_rs"] = {
            **(ctx.get("sector_rs") or {}),
            "symbol_to_sector": {
                "TESTSTOCK": {"sector_index": "NIFTY METAL", "industry": "Metal"}
            },
        }
        ctx["sector_leaderboard"] = {
            "rows": [{"sector": "NIFTY METAL", "rank": 9, "tier": "LAGGING", "composite_score": 28.5}],
        }
        normalized = build_dashboard_context(ctx)
        first = normalized["results"][0]
        self.assertEqual(first["sector"], "NIFTY METAL")
        self.assertEqual(first["sector_tier"], "LAGGING")
        self.assertEqual(first["sector_tier_class"], "lagging")

    def test_no_results_state_explains_empty_scan(self):
        context = self._context(Path("missing.png"))
        context["results"] = []
        html = render_dashboard(context)

        self.assertIn("No setups passed this scan.", html)
        self.assertIn("scanner did not produce tradable pattern cards", html)
        self.assertIn('id="resultSearch"', html)

    def test_momentum_radar_renders_watch_only_names_separately(self):
        context = self._context(Path("missing.png"))
        context["momentum_radar"] = [
            {
                "symbol": "HSCL",
                "company_name": "Himadri Speciality Chemical",
                "score": 96.0,
                "status": "Momentum surge",
                "action": "WATCH ONLY",
                "cmp": 685.05,
                "latest_date": "2026-06-05",
                "from_52w_high_pct": 1.86,
                "one_day_change_pct": 6.71,
                "return_20d_pct": 19.4,
                "daily_volume_ratio": 2.72,
                "weekly_volume_ratio": 4.76,
                "weekly_resistance": 654.45,
                "weekly_resistance_extension_pct": 4.68,
                "stage2_status": "PASS",
                "reasons": ["daily volume surge", "weekly volume surge"],
                "watch_notes": ["Weekly invalidation is wide; wait for a tighter base"],
            }
        ]

        normalized = build_dashboard_context(context)
        html = render_dashboard(context)

        self.assertEqual(normalized["summary"]["hit_count"], 1)
        self.assertEqual(normalized["summary"]["radar_count"], 1)
        self.assertIn("Research Only", html)
        self.assertIn('id="researchOnlySearch"', html)
        self.assertIn("No entry price is generated from this table.", html)
        self.assertIn("WATCH ONLY", html)
        self.assertIn("HSCL", html)
        self.assertIn("Weekly invalidation is wide", html)
        self.assertIn('href="https://www.screener.in/company/HSCL/"', html)

    def test_high_quality_untriggered_setup_renders_early_watchlist(self):
        context = self._context(Path("missing.png"))
        watch = dict(context["results"][0])
        watch.update(
            {
                "symbol": "EARLY",
                "status": "PIVOT READY",
                "score": 92,
                "tier": "MEDIUM",
                "entry_price": 120.0,
                "entry_state": "WAIT_FOR_TRIGGER",
                "entry_triggered": False,
                "trigger_price": 120.0,
                "scan_close": 114.0,
                "target": 168.0,
                "stop_loss": 104.0,
                "reward_risk": 3.0,
                "pattern_grade": 8.6,
            }
        )
        context["results"] = [watch]

        normalized = build_dashboard_context(context)
        html = render_dashboard(context)

        self.assertEqual(normalized["summary"]["early_watch_count"], 1)
        self.assertEqual(normalized["summary"]["entry_decision_count"], 1)
        self.assertEqual(normalized["early_watchlist"][0]["symbol"], "EARLY")
        self.assertEqual(normalized["early_watchlist"][0]["action"], "WAIT FOR TRIGGER")
        self.assertEqual(normalized["entry_decisions"][0]["decision"], "ENTER ABOVE")
        self.assertEqual(normalized["entry_decisions"][0]["enter_at"], "Rs.120")
        self.assertIn("Top Entry Actions", html)
        self.assertIn("This table is capped at 60 rows", html)
        self.assertIn("Click headers to sort", html)
        self.assertIn("initSortableTables", html)
        self.assertIn("table-sort-button", html)
        self.assertIn('id="entryDecisionSearch"', html)
        self.assertIn('id="earlyWatchSearch"', html)
        self.assertIn("ENTER ABOVE", html)
        self.assertIn("Waiting For Entry Trigger", html)
        self.assertIn("Conviction", html)
        self.assertIn("EARLY", html)
        self.assertIn('aria-label="EARLY entry decision"', html)
        self.assertIn("WAIT FOR TRIGGER", html)
        self.assertIn("Do not enter yet", html)
        self.assertIn('href="https://www.screener.in/company/EARLY/"', html)

    def test_triggered_result_card_renders_enter_now_decision(self):
        context = self._context(Path("missing.png"))
        triggered = dict(context["results"][0])
        triggered.update(
            {
                "entry_price": 181.0,
                "entry_triggered": True,
                "scan_close": 185.0,
                "trigger_context": {"status": "CLEAN_TRIGGER", "label": "Clean trigger"},
            }
        )
        context["results"] = [triggered]

        normalized = build_dashboard_context(context)
        html = render_dashboard(context)

        self.assertEqual(normalized["results"][0]["entry_card"]["decision"], "ENTER NOW")
        self.assertEqual(normalized["results"][0]["entry_card"]["enter_at"], "Rs.185")
        self.assertIn('aria-label="TESTSTOCK entry decision"', html)
        self.assertIn("ENTER NOW", html)
        self.assertIn("Clean trigger with acceptable candle/volume context.", html)

    def test_skipped_daily_weekly_candidate_renders_setup_watchlist(self):
        context = self._context(Path("missing.png"))
        weekly = PatternResult(
            pattern="Weekly Breakout",
            status="BREAKING OUT",
            pivot=329.91,
            target=388.52,
            stop_loss=300.6,
            confidence=91.0,
            explanation="Weekly trendline breakout is visible, but the move is extended.",
            timeframe="weekly",
            bars_in_pattern=40,
            extra={"pattern_quality_score": 7.7},
        )
        daily = PatternResult(
            pattern="Double Bottom",
            status="PIVOT READY",
            pivot=102.78,
            target=122.81,
            stop_loss=92.76,
            confidence=68.0,
            explanation="Daily double bottom is forming below the trigger.",
            timeframe="daily",
            bars_in_pattern=60,
            extra={"pattern_quality_score": 6.4},
        )
        skipped = {
            "symbol": "KRBL",
            "pattern": weekly.pattern,
            "status": weekly.status,
            "pivot": weekly.pivot,
            "technical_pivot": weekly.pivot,
            "entry_price": 366.8,
            "entry_state": "TRIGGERED",
            "entry_triggered": True,
            "trigger_price": weekly.pivot,
            "scan_close": 366.8,
            "target": weekly.target,
            "stop_loss": weekly.stop_loss,
            "timeframe": "weekly",
            "pattern_result": weekly,
            "pattern_results": [weekly, daily],
            "all_patterns": [weekly.pattern, daily.pattern],
            "score": 0,
            "tier": "SKIP",
            "tradable": False,
            "skip_reason": "MOVE_ALREADY_HAPPENED_TARGET_HIT",
            "reward_risk": 0.33,
            "pattern_grade": 7.7,
            "breakdown": {
                "pattern": 20,
                "stage2": 0,
                "volume": 5,
                "sector_rs": 0,
                "market_regime": 0,
                "multi_tf": 40,
                "rsi_adjustment": 0,
            },
            "filters": {
                "stage2": {"passed": False, "status": "FAIL"},
                "volume": {"passed": False, "status": "DRY_UP", "details": {"timeframe": "weekly"}},
                "sector_rs": {"passed": False, "status": "NEUTRAL"},
                "market_regime": {"score": 1, "verdict": "BEAR"},
                "rsi": {"value": 59.0, "status": "HEALTHY"},
                "multi_tf": {"passed": True, "status": "WEEKLY_PATTERN"},
            },
        }
        context["results"] = [skipped]

        normalized = build_dashboard_context(context)
        html = render_dashboard(context)

        self.assertEqual(normalized["summary"]["hit_count"], 0)
        self.assertEqual(normalized["summary"]["setup_watch_count"], 1)
        self.assertEqual(normalized["summary"]["entry_decision_count"], 1)
        self.assertEqual(normalized["setup_watchlist"][0]["symbol"], "KRBL")
        self.assertEqual(normalized["setup_watchlist"][0]["timeframe_tag"], "Daily + Weekly")
        self.assertEqual(normalized["setup_watchlist"][0]["conviction_label"], "HIGH WATCH")
        self.assertEqual(normalized["entry_decisions"][0]["decision"], "DO NOT ENTER")
        self.assertEqual(normalized["entry_decisions"][0]["enter_at"], "No entry")
        self.assertIn("Top Entry Actions", html)
        self.assertIn("No-Entry Setup Details", html)
        self.assertIn('id="entryDecisionSearch"', html)
        self.assertIn('id="setupWatchSearch"', html)
        self.assertIn("HIGH WATCH", html)
        self.assertIn("Daily + Weekly", html)
        self.assertIn("DO NOT ENTER", html)
        self.assertIn("DO NOT CHASE", html)
        self.assertIn("Target already hit after breakout", html)
        self.assertIn('href="https://www.screener.in/company/KRBL/"', html)

    def test_signal_tracker_renders_lifecycle_table_separately(self):
        context = self._context(Path("missing.png"))
        context["signal_tracker"] = [
            {
                "symbol": "OLD",
                "pattern": "Flat Base",
                "signal_date": "2026-06-09",
                "timeframe": "daily",
                "tier": "HIGH",
                "score": 84,
                "cmp": 100,
                "latest_close": 104,
                "latest_date": "2026-06-10",
                "change_pct": 4.0,
                "entry_price": 100,
                "target": 120,
                "stop_loss": 94,
                "days_since_signal": 1,
                "status": "Still Valid",
                "status_class": "valid",
                "fresh_state": "Not fresh today",
                "fresh_class": "stale",
                "original_conviction": "HIGH 84",
                "current_conviction": "Still valid",
                "trigger_state": "Triggered",
                "trigger_price": 100,
                "trigger_note": "Price is above the entry trigger.",
                "entry_decision": "Hold if already in",
                "decision_reason": "The old trade remains valid, but it is not a new signal today.",
                "current_reward_risk": 1.6,
                "reason": "No fresh card today; latest close remains above the original entry and stop.",
            }
        ]

        normalized = build_dashboard_context(context)
        html = render_dashboard(context)

        self.assertEqual(normalized["summary"]["tracker_count"], 1)
        self.assertEqual(normalized["summary"]["tracker_prior_count"], 1)
        self.assertEqual(normalized["summary"]["tracker_today_count"], 0)
        self.assertEqual(normalized["summary"]["pattern_feedback_count"], 1)
        self.assertIn("30-Day Trade Decision Tracker", html)
        self.assertIn("Pattern Feedback", html)
        self.assertIn('id="signalTrackerSearch"', html)
        self.assertIn('id="patternFeedbackSearch"', html)
        self.assertIn("LOW SAMPLE", html)
        self.assertIn("Trigger means price crosses or closes above Entry", html)
        self.assertIn("Original Conviction", html)
        self.assertIn("Current Conviction", html)
        self.assertIn("Entry Decision", html)
        self.assertIn("HIGH 84", html)
        self.assertIn("Still valid", html)
        self.assertIn("Hold if already in", html)
        self.assertIn("Trigger Rs.100", html)
        self.assertIn("Not fresh today", html)
        self.assertIn("No fresh card today", html)
        self.assertIn('href="https://www.screener.in/company/OLD/"', html)

    def _context(self, chart_path: Path) -> dict:
        pattern = PatternResult(
            pattern="Ascending Triangle",
            status="BREAKING OUT",
            pivot=181.0,
            target=218.0,
            stop_loss=165.0,
            confidence=92.0,
            explanation="3 resistance touches near 181; rising support is intact.",
            timeframe="daily",
            bars_in_pattern=60,
        )
        scored = {
            "symbol": "TESTSTOCK",
            "pattern": pattern.pattern,
            "status": pattern.status,
            "pivot": pattern.pivot,
            "target": pattern.target,
            "stop_loss": pattern.stop_loss,
            "timeframe": pattern.timeframe,
            "pattern_result": pattern,
            "score": 92,
            "tier": "HIGHEST",
            "tradable": True,
            "chart_path": str(chart_path),
            "all_patterns": ["Ascending Triangle", "Bull Flag"],
            "stacked_count": 2,
            "breakdown": {
                "pattern": 23,
                "stage2": 20,
                "volume": 20,
                "sector_rs": 15,
                "market_regime": 10,
                "multi_tf": 10,
                "rsi_adjustment": -6,
            },
            "filters": {
                "stage2": {"passed": True, "status": "PASS", "details": {"close": 183.4}},
                "volume": {"passed": True, "status": "CONFIRMED", "details": {"breakout_volume_ratio": 1.9}},
                "sector_rs": {"passed": True, "status": "LEADING"},
                "market_regime": {"score": 4, "verdict": "CONFIRMED UPTREND"},
                "rsi": {"value": 64.0, "status": "HEALTHY", "bearish_divergence": False},
                "multi_tf": {"passed": True, "status": "ALIGNED"},
            },
        }
        scored = attach_explanation(scored)
        return {
            "generated_at": "19 May 2026, 15:45",
            "stocks_scanned": 500,
            "duration_seconds": 18.4,
            "market_regime": {
                "score": 4,
                "verdict": "CONFIRMED UPTREND",
                "checks": {
                    "nifty_above_50ma": True,
                    "nifty_above_200ma": True,
                    "ma50_above_ma200": True,
                    "advance_decline_confirmed": True,
                },
                "details": {
                    "nifty_close": 24710.2,
                    "nifty_ma50": 24200.1,
                    "nifty_ma200": 22980.0,
                    "advance_decline_ratio": 1.8,
                },
            },
            "sector_rs": {
                "sectors": {
                    "NIFTY IT": {"return_pct": 7.2, "vs_nifty_pct": 2.8},
                    "NIFTY FMCG": {"return_pct": -1.0, "vs_nifty_pct": -2.4},
                    "NIFTY AUTO": {"return_pct": 3.1, "vs_nifty_pct": 0.3},
                }
            },
            "results": [scored],
            "errors": [{"stage": "detect", "symbol": "MISS", "message": "insufficient history", "critical": False}],
        }


if __name__ == "__main__":
    unittest.main()
