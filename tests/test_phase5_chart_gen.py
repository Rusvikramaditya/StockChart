"""Phase 5A contract tests for annotated chart generation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import matplotlib.image as mpimg
import numpy as np
import pandas as pd

from engine.chart_gen import generate_pattern_chart
from patterns.base import PatternResult


class ChartGenerationPhase5ATest(unittest.TestCase):
    def test_generate_chart_for_each_pattern_type(self):
        cases = [
            self._case(
                "Cup & Handle",
                bars_in_pattern=100,
                extra={
                    "left_rim_idx": 10,
                    "trough_idx": 45,
                    "right_rim_idx": 78,
                    "handle_start_idx": 84,
                },
            ),
            self._case(
                "Ascending Triangle",
                bars_in_pattern=60,
                extra={
                    "touch_indices": [8, 31, 56],
                    "low_indices": [5, 26, 50],
                },
            ),
            self._case(
                "Bull Flag",
                bars_in_pattern=28,
                extra={
                    "pole_start_idx": 228,
                    "pole_end_idx": 241,
                    "flag_len": 15,
                    "pole_pct": 14.2,
                    "pullback_pct": 5.8,
                },
            ),
            self._case(
                "VCP",
                bars_in_pattern=90,
                extra={"contractions_pct": [24.8, 14.9, 7.2]},
            ),
            self._case(
                "Inverse Head & Shoulders",
                bars_in_pattern=120,
                extra={
                    "left_shoulder_idx": 24,
                    "head_idx": 58,
                    "right_shoulder_idx": 88,
                    "neckline": 181.0,
                },
            ),
            self._case(
                "Supertrend Bullish Flip",
                bars_in_pattern=13,
                extra={
                    "flip_idx": 257,
                    "atr": 4.2,
                    "supertrend": 168.0,
                },
            ),
            self._case(
                "Multi-Year Breakout",
                bars_in_pattern=156,
                extra={
                    "resistance_touch_indices": [42, 96, 144],
                    "years": 3.0,
                },
                frame=self._ohlcv_frame(rows=180, freq="W-FRI"),
            ),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            for result, frame in cases:
                with self.subTest(pattern=result.pattern):
                    path = generate_pattern_chart(
                        frame,
                        "TESTSTOCK",
                        result,
                        pivot=result.pivot,
                        target=result.target,
                        stop_loss=result.stop_loss,
                        conviction=82,
                        output_dir=output_dir,
                    )
                    self.assertTrue(path.exists())
                    self.assertEqual(path.suffix, ".png")
                    self.assertGreater(path.stat().st_size, 30_000)

                    image = mpimg.imread(path)
                    self.assertEqual(image.shape[0], 800)
                    self.assertEqual(image.shape[1], 1200)
                    rgb = image[..., :3]
                    self.assertLess(float(rgb[:8, :8].mean()), 0.15)
                    self.assertGreater(float(rgb.std()), 0.025)

    def test_rejects_empty_input(self):
        result, _ = self._case("Ascending Triangle", bars_in_pattern=60, extra={})
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                generate_pattern_chart(pd.DataFrame(), "EMPTY", result, output_dir=tmp)

    def _case(
        self,
        pattern: str,
        *,
        bars_in_pattern: int,
        extra: dict,
        frame: pd.DataFrame | None = None,
    ) -> tuple[PatternResult, pd.DataFrame]:
        frame = frame if frame is not None else self._ohlcv_frame()
        result = PatternResult(
            pattern=pattern,
            status="BREAKING OUT",
            pivot=181.0,
            target=218.0,
            stop_loss=165.0,
            confidence=82.0,
            explanation=f"Synthetic {pattern} setup.",
            timeframe="weekly" if "Multi-Year" in pattern else "daily",
            bars_in_pattern=bars_in_pattern,
            extra=extra,
        )
        return result, frame

    def _ohlcv_frame(self, rows: int = 260, freq: str = "B") -> pd.DataFrame:
        dates = pd.date_range("2024-01-01", periods=rows, freq=freq)
        trend = np.linspace(100.0, 182.0, rows)
        wave = np.sin(np.linspace(0.0, 9.0 * np.pi, rows)) * 4.0
        close = trend + wave
        open_ = close + np.cos(np.linspace(0.0, 6.0 * np.pi, rows)) * 1.2
        high = np.maximum(open_, close) + 2.5
        low = np.minimum(open_, close) - 2.5
        volume = np.linspace(90_000.0, 160_000.0, rows) + np.sin(np.linspace(0.0, 4.0 * np.pi, rows)) * 8_000.0
        return pd.DataFrame(
            {
                "date": dates,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            }
        )


if __name__ == "__main__":
    unittest.main()
