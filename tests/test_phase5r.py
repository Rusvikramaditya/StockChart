"""Phase 5R contract tests — TradingView thesis chart system."""

from __future__ import annotations

import json
import re
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from engine.chart_payload import (
    build_chart_payload,
    payload_to_json,
    validate_payload,
)
from engine.thesis_chart import export_thesis_chart_png
from patterns.base import PatternResult
from scripts.export_chart_screenshot import find_chrome_executable
from scripts.gen_detected_chart_gallery import parse_args as parse_detected_gallery_args
from scripts.gen_sample_thesis_chart import PATTERN_BUILDERS, SYMBOL_DEFAULT, parse_args as parse_sample_args


BASE_DIR = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = BASE_DIR / "dashboard"


class _FakeResult:
    def __init__(self, pivot=200.0, target=240.0, stop_loss=185.0, pattern="Ascending Triangle", extra=None):
        self.pattern = pattern
        self.status = "BREAKING OUT"
        self.pivot = pivot
        self.target = target
        self.stop_loss = stop_loss
        self.confidence = 82.0
        self.bars_in_pattern = 60
        self.extra = extra or {}


def _ohlcv(rows=130, start="2024-01-01") -> pd.DataFrame:
    dates = pd.date_range(start, periods=rows, freq="B")
    trend = np.linspace(180.0, 210.0, rows)
    wave = np.sin(np.linspace(0, 6 * np.pi, rows)) * 3.0
    close = trend + wave
    open_ = close + np.random.default_rng(42).uniform(-1.5, 1.5, rows)
    high = np.maximum(open_, close) + 1.5
    low = np.minimum(open_, close) - 1.5
    vol = np.linspace(50_000, 80_000, rows)
    return pd.DataFrame({"date": dates, "open": open_, "high": high, "low": low, "close": close, "volume": vol})


class ChartPayloadTest(unittest.TestCase):

    def test_basic_payload_structure(self):
        df = _ohlcv()
        result = _FakeResult()
        payload = build_chart_payload(df, "TESTCO", result, company_name="Test Company Ltd")

        self.assertEqual(payload["symbol"], "TESTCO")
        self.assertEqual(payload["company_name"], "Test Company Ltd")
        self.assertEqual(payload["exchange"], "NSE")
        self.assertIn("candles", payload)
        self.assertIn("trade_plan", payload)
        self.assertIn("pattern", payload)
        self.assertIn("annotations", payload)
        self.assertIn("source_rows", payload)
        self.assertIn("visible_start_index", payload)

    def test_candle_count_respects_lookback(self):
        df = _ohlcv(rows=200)
        payload = build_chart_payload(df, "X", _FakeResult(), lookback_bars=120)
        self.assertEqual(len(payload["candles"]), 120)

    def test_candle_fields_present(self):
        df = _ohlcv(rows=50)
        payload = build_chart_payload(df, "X", _FakeResult())
        candle = payload["candles"][0]
        for key in ("time", "open", "high", "low", "close", "volume"):
            self.assertIn(key, candle)
        self.assertIsInstance(candle["time"], int)

    def test_trade_plan_calculations(self):
        result = _FakeResult(pivot=200.0, target=240.0, stop_loss=180.0)
        df = _ohlcv()
        payload = build_chart_payload(df, "X", result)
        tp = payload["trade_plan"]

        self.assertAlmostEqual(tp["upside_pct"], 20.0, places=1)
        self.assertAlmostEqual(tp["downside_pct"], 10.0, places=1)
        self.assertAlmostEqual(tp["reward_risk"], 2.0, places=1)

    def test_trade_plan_entry_target_stop_present(self):
        result = _FakeResult(pivot=200.0, target=230.0, stop_loss=185.0)
        df = _ohlcv()
        payload = build_chart_payload(df, "X", result)
        tp = payload["trade_plan"]

        self.assertEqual(tp["entry"], 200.0)
        self.assertEqual(tp["target"], 230.0)
        self.assertEqual(tp["stop"], 185.0)

    def test_annotations_contain_three_hlines(self):
        df = _ohlcv()
        payload = build_chart_payload(df, "X", _FakeResult())
        roles = {a["role"] for a in payload["annotations"]}
        self.assertIn("entry", roles)
        self.assertIn("target", roles)
        self.assertIn("stop", roles)

    def test_empty_dataframe_raises(self):
        with self.assertRaises(ValueError):
            build_chart_payload(pd.DataFrame(), "X", _FakeResult())

    def test_missing_ohlcv_column_raises(self):
        df = _ohlcv().drop(columns=["close"])
        with self.assertRaises(ValueError):
            build_chart_payload(df, "X", _FakeResult())

    def test_json_serializable(self):
        df = _ohlcv()
        payload = build_chart_payload(df, "JSONTEST", _FakeResult())
        s = payload_to_json(payload)
        roundtrip = json.loads(s)
        self.assertEqual(roundtrip["symbol"], "JSONTEST")

    def test_no_nan_in_json(self):
        df = _ohlcv()
        payload = build_chart_payload(df, "X", _FakeResult())
        s = payload_to_json(payload)
        # json.dumps with allow_nan=False guarantees this; double-check no NaN literal
        self.assertNotIn("NaN", s)
        self.assertNotIn("Infinity", s)

    def test_script_injection_escaped(self):
        """Payload JSON must not allow </script> to break out of a script tag."""
        df = _ohlcv()
        result = _FakeResult(pattern="</script><script>alert(1)</script>")
        payload = build_chart_payload(df, "X", result)
        s = payload_to_json(payload)
        self.assertNotIn("</script>", s)

    def test_validate_payload_passes_complete(self):
        df = _ohlcv()
        payload = build_chart_payload(df, "X", _FakeResult())
        validate_payload(payload)  # must not raise

    def test_validate_payload_fails_no_candles(self):
        payload = {"candles": [], "trade_plan": {"entry": 1, "target": 2, "stop": 0.9}}
        with self.assertRaises(ValueError):
            validate_payload(payload)

    def test_validate_payload_fails_missing_trade_level(self):
        df = _ohlcv()
        result = _FakeResult(pivot=200.0, target=None, stop_loss=180.0)
        result.target = None
        payload = build_chart_payload(df, "X", result)
        with self.assertRaises(ValueError):
            validate_payload(payload)

    def test_pattern_result_dataclass_supported(self):
        """PatternResult dataclass (from patterns.base) works as pattern_result input."""
        df = _ohlcv()
        pr = PatternResult(
            pattern="Cup & Handle",
            status="BREAKING OUT",
            pivot=200.0,
            target=240.0,
            stop_loss=185.0,
            confidence=80.0,
            explanation="Test.",
            timeframe="daily",
            bars_in_pattern=100,
            extra={"left_rim_idx": 10, "trough_idx": 45},
        )
        payload = build_chart_payload(df, "CUPTEST", pr)
        self.assertEqual(payload["pattern"]["type"], "Cup & Handle")
        self.assertIsNotNone(payload["pattern"]["geometry"])


class ThesisChartHtmlTest(unittest.TestCase):
    """Static checks on thesis_chart.html (standalone renderer page)."""

    def _html(self) -> str:
        path = DASHBOARD_DIR / "thesis_chart.html"
        self.assertTrue(path.exists(), "dashboard/thesis_chart.html must exist")
        return path.read_text(encoding="utf-8")

    def test_file_exists(self):
        self.assertTrue((DASHBOARD_DIR / "thesis_chart.html").exists())

    def test_references_local_vendor_only(self):
        html = self._html()
        # No CDN script or link elements
        ext = re.findall(r'<script[^>]+src=["\']https?://', html, re.IGNORECASE)
        self.assertEqual([], ext, "thesis_chart.html must not load scripts from CDN")

    def test_references_local_vendor_js(self):
        html = self._html()
        self.assertIn("lightweight-charts.standalone.production.js", html)

    def test_references_renderer_and_annotations(self):
        html = self._html()
        self.assertIn("chart_renderer.js", html)
        self.assertIn("chart_annotations.js", html)

    def test_attribution_present(self):
        html = self._html()
        self.assertIn("TradingView", html)

    def test_payload_loading_hooks_present(self):
        html = self._html()
        self.assertIn("CHART_PAYLOAD", html)
        self.assertIn("ThesisChart.init", html)

    def test_annotation_renderer_has_pattern_overlay_hooks(self):
        js = (DASHBOARD_DIR / "chart_annotations.js").read_text(encoding="utf-8")
        for hook in [
            "drawCupHandle",
            "drawAscendingTriangle",
            "drawBullFlag",
            "drawVcp",
            "drawInverseHeadShoulders",
            "drawMultiYearBreakout",
        ]:
            self.assertIn(hook, js)
        # Path B: downside fill box removed; upside shown in arrow box label
        self.assertIn("upside_pct", js)
        # Stop rendered as dashed line (no fill box)
        self.assertIn("stopY", js)

    def test_mobile_chart_labels_avoid_right_axis_collision(self):
        renderer_js = (DASHBOARD_DIR / "chart_renderer.js").read_text(encoding="utf-8")
        annotations_js = (DASHBOARD_DIR / "chart_annotations.js").read_text(encoding="utf-8")

        self.assertIn("var compact = container.clientWidth < 520", renderer_js)
        self.assertIn("compact ? 'Target' : 'Target ' + _fmt(tp.target)", renderer_js)
        self.assertIn("compact ? 'Entry' : 'Entry ' + _fmt(tp.entry)", renderer_js)
        self.assertIn("compact ? 'Stop' : 'Stop ' + _fmt(tp.stop)", renderer_js)
        self.assertIn("priceLineVisible: !compact", renderer_js)
        self.assertIn("lastValueVisible: !compact", renderer_js)

        self.assertIn("var compact = W < 520", annotations_js)
        self.assertIn("boxEnd = W - psW - (compact ? 8 : 10)", annotations_js)
        self.assertIn("var boxW = Math.max(48, boxEnd - boxStart)", annotations_js)
        self.assertIn("boxW - 10", annotations_js)


class VendorBundleTest(unittest.TestCase):

    def test_vendor_bundle_exists(self):
        bundle = DASHBOARD_DIR / "vendor" / "lightweight-charts.standalone.production.js"
        self.assertTrue(bundle.exists(), "Vendored LW Charts bundle must exist")
        self.assertGreater(bundle.stat().st_size, 50_000, "Bundle seems too small — check download")

    def test_vendor_bundle_exposes_global(self):
        bundle = DASHBOARD_DIR / "vendor" / "lightweight-charts.standalone.production.js"
        content = bundle.read_text(encoding="utf-8")
        self.assertIn("window.LightweightCharts", content)

    def test_vendor_notes_exist(self):
        self.assertTrue((DASHBOARD_DIR / "vendor" / "VENDOR_NOTES.md").exists())


class ScreenshotExportTest(unittest.TestCase):

    def test_screenshot_export_script_is_available(self):
        script = BASE_DIR / "scripts" / "export_chart_screenshot.py"
        self.assertTrue(script.exists())
        content = script.read_text(encoding="utf-8")
        self.assertIn("data-chart-ready", content)
        self.assertIn("colored_pixels", content)

    def test_sample_chart_generator_has_safe_help_and_default_symbol(self):
        self.assertEqual(parse_sample_args([]).symbol, SYMBOL_DEFAULT)
        self.assertEqual(parse_sample_args(["infy"]).symbol, "infy")
        self.assertEqual(parse_sample_args(["infy", "--pattern", "cup_handle"]).pattern, "cup_handle")
        self.assertTrue(parse_sample_args(["--sample-pack"]).sample_pack)
        with redirect_stdout(StringIO()):
            with self.assertRaises(SystemExit) as cm:
                parse_sample_args(["--help"])
        self.assertEqual(cm.exception.code, 0)

    def test_sample_chart_generator_covers_required_pattern_overlays(self):
        self.assertEqual(
            {
                "ascending_triangle",
                "bull_flag",
                "cup_handle",
                "inverse_head_shoulders",
                "multi_year_breakout",
                "supertrend",
                "vcp",
            },
            set(PATTERN_BUILDERS),
        )

    def test_detected_gallery_generator_is_detector_hit_only_path(self):
        args = parse_detected_gallery_args([])
        self.assertEqual(args.universe, "all_nse_equity")
        self.assertEqual(args.max_per_pattern, 1)

        gallery = (BASE_DIR / "docs" / "CHART_APPROVAL_GALLERY.html").read_text(encoding="utf-8")
        self.assertIn("Real Detector Chart Gallery", gallery)
        self.assertIn("No pattern label is forced", gallery)
        self.assertNotIn("INFY_thesis_chart_cup_handle", gallery)
        self.assertNotIn("Manual trade levels", gallery)

    def test_local_browser_is_available_for_chart_screenshots(self):
        self.assertIsNotNone(find_chrome_executable())

    def test_thesis_chart_export_paths_do_not_collide(self):
        with self.subTest("two rapid exports for same symbol"):
            import tempfile

            payload = {
                "symbol": "TEST",
                "company_name": "Test Ltd",
                "pattern": {"type": "Ascending Triangle"},
                "candles": [{"time": 1, "open": 1, "high": 2, "low": 1, "close": 2, "volume": 1}],
                "trade_plan": {"entry": 2, "target": 3, "stop": 1.5},
            }
            scored = {"symbol": "TEST", "pattern": "Ascending Triangle", "chart_payload": payload}
            with tempfile.TemporaryDirectory() as tmp:
                with patch("engine.thesis_chart.export_chart_screenshot") as screenshot_mock:
                    screenshot_mock.return_value = {"canvas_count": 1, "colored_pixels": 200, "sampled_pixels": 200}
                    first = export_thesis_chart_png(scored, output_dir=tmp)
                    second = export_thesis_chart_png(scored, output_dir=tmp)

                self.assertNotEqual(first["png_path"], second["png_path"])
                self.assertTrue(first["html_path"].exists())
                self.assertIn("Powered by TradingView", first["html_path"].read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
