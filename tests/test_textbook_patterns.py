"""Tests for strict high-conviction pattern additions."""

from __future__ import annotations

import unittest

import numpy as np

from filters import pocket_pivot
from patterns import double_bottom, flat_base, high_tight_flag


def _base_ohlcv(close: np.ndarray, volume: np.ndarray | None = None) -> dict:
    volume_arr = volume if volume is not None else np.full(len(close), 1_000_000.0)
    return {
        "open": close - 0.2,
        "high": close + 0.6,
        "low": close - 0.6,
        "close": close.copy(),
        "volume": volume_arr.astype(float),
    }


def _flat_base_fixture(*, stale_breakout: bool = False) -> dict:
    trend = np.linspace(45.0, 92.0, 220)
    base = np.linspace(94.0, 98.0, 40)
    close = np.concatenate([trend, base])
    data = _base_ohlcv(close)
    start = len(close) - 40
    volume = np.full(len(close), 900_000.0)
    volume[start : start + 20] = 1_500_000.0
    volume[start + 20 :] = 700_000.0
    data["volume"] = volume
    for offset in (5, 15, 25, 35):
        idx = start + offset
        data["high"][idx] = 100.0
        data["close"][idx] = 98.8
        data["low"][idx] = 96.5
    data["low"][start + 8 : start + 14] = 92.0
    data["close"][-1] = 104.0 if stale_breakout else 99.2
    data["high"][-1] = data["close"][-1] + 0.3
    data["low"][-1] = data["close"][-1] - 0.5
    return data


def _double_bottom_fixture(*, no_undercut: bool = False) -> dict:
    close = np.full(150, 96.0)
    data = _base_ohlcv(close)
    volume = np.full(150, 1_000_000.0)
    left, right, mid = 45, 85, 62
    data["low"][left] = 90.0
    data["close"][left] = 91.0
    data["high"][left] = 92.0
    data["low"][right] = 96.0 if no_undercut else 87.5
    data["close"][right] = 96.5 if no_undercut else 89.0
    data["high"][right] = 97.0 if no_undercut else 90.0
    data["high"][mid] = 100.0
    data["close"][mid] = 99.0
    data["low"][mid] = 97.0
    volume[left - 2 : left + 3] = 1_500_000.0
    volume[right - 2 : right + 3] = 700_000.0
    data["volume"] = volume
    data["close"][-1] = 99.0
    data["high"][-1] = 99.4
    data["low"][-1] = 98.0
    return data


def _high_tight_flag_fixture(*, weak_advance: bool = False) -> dict:
    prior = np.linspace(80.0, 100.0, 30)
    advance_end = 150.0 if weak_advance else 185.0
    advance = np.linspace(100.0, advance_end, 28)
    flag = np.linspace(181.0 if not weak_advance else 148.0, 171.0 if not weak_advance else 142.0, 12)
    latest = np.array([179.0 if not weak_advance else 146.0])
    close = np.concatenate([prior, advance, flag, latest])
    data = _base_ohlcv(close)
    flag_start = len(close) - 13
    data["high"][flag_start] = 182.0 if not weak_advance else 149.0
    data["low"][flag_start + 5] = 162.0 if not weak_advance else 136.0
    volume = np.full(len(close), 700_000.0)
    volume[30:58] = 2_000_000.0
    volume[flag_start:] = 900_000.0
    data["volume"] = volume
    return data


class FlatBaseTest(unittest.TestCase):

    def test_textbook_flat_base_detects(self):
        results = flat_base.detect(_flat_base_fixture())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].pattern, "Flat Base")
        self.assertGreaterEqual(results[0].extra["pattern_quality_score"], 7.0)

    def test_stale_flat_base_breakout_rejected(self):
        self.assertEqual(flat_base.detect(_flat_base_fixture(stale_breakout=True)), [])


class DoubleBottomTest(unittest.TestCase):

    def test_textbook_undercut_reclaim_detects(self):
        results = double_bottom.detect(_double_bottom_fixture())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].pattern, "Double Bottom")
        self.assertGreaterEqual(results[0].extra["pattern_quality_score"], 7.0)

    def test_missing_undercut_rejected(self):
        self.assertEqual(double_bottom.detect(_double_bottom_fixture(no_undercut=True)), [])


class HighTightFlagTest(unittest.TestCase):

    def test_textbook_high_tight_flag_detects(self):
        results = high_tight_flag.detect(_high_tight_flag_fixture())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].pattern, "High Tight Flag")
        self.assertGreaterEqual(results[0].extra["pattern_quality_score"], 7.0)

    def test_weak_advance_rejected(self):
        self.assertEqual(high_tight_flag.detect(_high_tight_flag_fixture(weak_advance=True)), [])


class PocketPivotTest(unittest.TestCase):

    def test_pocket_pivot_confirms_up_volume(self):
        close = np.linspace(90.0, 105.0, 70)
        data = _base_ohlcv(close, np.full(70, 1_000_000.0))
        for idx in range(58, 68, 2):
            data["close"][idx] = data["close"][idx - 1] - 1.0
            data["volume"][idx] = 1_200_000.0
        data["open"][-1] = data["close"][-1] - 1.0
        data["low"][-1] = data["close"][-1] - 1.5
        data["high"][-1] = data["close"][-1] + 0.3
        data["volume"][-1] = 1_600_000.0

        result = pocket_pivot.evaluate(data)

        self.assertTrue(result["passed"])
        self.assertEqual(result["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
