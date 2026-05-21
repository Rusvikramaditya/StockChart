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
        self.assertIn("Open thesis", html)
        self.assertIn("data-search-text=", html)
        self.assertIn("No setups match the current filters.", html)
        self.assertIn("2 patterns", html)
        self.assertIn("Also detected: Ascending Triangle, Bull Flag", html)
        self.assertIn("Errors Panel", html)
        self.assertIn("data:image/png;base64,", html)
        self.assertNotIn("{{", html)
        self.assertNotIn("{%", html)
        # No CDN or external resource references in src/href attributes.
        # (URL strings in inlined JS code/comments are allowed.)
        ext_srcs = re.findall(r'(?:src|href)\s*=\s*["\']https?://', html, re.IGNORECASE)
        self.assertEqual([], ext_srcs, "No external CDN or API resource attributes allowed")

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

    def test_zero_score_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            chart_path = Path(tmp) / "chart.png"
            chart_path.write_bytes(PNG_1X1)
            context = self._context(chart_path)
            context["results"][0]["score"] = 0
            context["results"][0]["tier"] = "SKIP"
            context["results"][0]["cmp"] = 0

            normalized = build_dashboard_context(context)

            result = normalized["results"][0]
            self.assertEqual(result["score"], 0)
            self.assertEqual(result["tier"], "SKIP")
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
        self.assertIn("DRY_UP", html)
        self.assertIn("Constructive base", html)

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
