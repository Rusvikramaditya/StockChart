"""Momentum radar tests for watch-only price action names."""

from __future__ import annotations

import unittest

import numpy as np

from engine.momentum_radar import evaluate


class MomentumRadarTest(unittest.TestCase):
    def test_strong_breakout_price_action_returns_watch_only_row(self):
        daily = _daily_arrays(
            np.linspace(300.0, 685.05, 260),
            latest_high=698.0,
            latest_volume=2_400_000,
            base_volume=900_000,
        )
        weekly = _weekly_arrays(
            latest_close=685.05,
            prior_resistance=654.45,
            latest_high=698.0,
            stop_low=423.2,
            latest_volume=7_100_000,
            base_volume=1_500_000,
        )

        row = evaluate("HSCL", daily, weekly, company_name="Himadri Speciality Chemical")

        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["symbol"], "HSCL")
        self.assertEqual(row["action"], "WATCH ONLY")
        self.assertGreaterEqual(row["score"], 85)
        self.assertIn("daily volume surge", row["reasons"])
        self.assertTrue(any("invalidation is wide" in note for note in row["watch_notes"]))

    def test_near_resistance_with_weekly_volume_returns_radar_row(self):
        close = np.linspace(1_550.0, 2_273.0, 260)
        close[-2] = 2_219.5
        daily = _daily_arrays(
            close,
            latest_high=2_396.0,
            latest_volume=494_000,
            base_volume=397_000,
        )
        weekly = _weekly_arrays(
            latest_close=2_273.0,
            prior_resistance=2_396.0,
            latest_high=2_356.5,
            stop_low=1_652.0,
            latest_volume=2_020_000,
            base_volume=1_185_000,
        )

        row = evaluate("GLAND", daily, weekly, company_name="Gland Pharma")

        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["symbol"], "GLAND")
        self.assertGreaterEqual(row["score"], 65)
        self.assertTrue(any("Wait for weekly close above" in note for note in row["watch_notes"]))
        self.assertTrue(any("daily volume" in note for note in row["watch_notes"]))

    def test_weak_name_is_not_added_to_radar(self):
        daily = _daily_arrays(
            np.linspace(100.0, 70.0, 260),
            latest_high=130.0,
            latest_volume=90_000,
            base_volume=120_000,
        )

        self.assertIsNone(evaluate("WEAK", daily, None))


def _daily_arrays(
    close: np.ndarray,
    *,
    latest_high: float,
    latest_volume: float,
    base_volume: float,
) -> dict:
    close = np.asarray(close, dtype=float)
    high = close * 1.01
    high[-1] = latest_high
    low = close * 0.97
    volume = np.full(len(close), float(base_volume))
    volume[-1] = float(latest_volume)
    return {
        "date": _dates("2025-01-01", len(close), "D"),
        "open": close * 0.99,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def _weekly_arrays(
    *,
    latest_close: float,
    prior_resistance: float,
    latest_high: float,
    stop_low: float,
    latest_volume: float,
    base_volume: float,
) -> dict:
    close = np.linspace(latest_close * 0.72, latest_close, 80)
    high = close * 1.02
    high[-2] = prior_resistance
    high[-1] = latest_high
    low = close * 0.95
    low[-10:] = np.minimum(low[-10:], stop_low)
    volume = np.full(len(close), float(base_volume))
    volume[-1] = float(latest_volume)
    return {
        "date": _dates("2024-01-05", len(close), "W"),
        "open": close * 0.99,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def _dates(start: str, count: int, unit: str) -> np.ndarray:
    step = np.timedelta64(7 if unit == "W" else 1, "D")
    base = np.datetime64(start, "D")
    return np.array([base + step * idx for idx in range(count)], dtype="datetime64[D]")


if __name__ == "__main__":
    unittest.main()
