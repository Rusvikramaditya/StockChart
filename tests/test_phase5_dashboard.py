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
        self.assertIn("Result Cards by Conviction Tier", html)
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
