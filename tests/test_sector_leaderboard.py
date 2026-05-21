"""Tests for sector leaderboard compute + ranking."""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from engine import sector_leaderboard


def _trending_frame(start: float, end: float, bars: int = 260) -> pd.DataFrame:
    """Build a DataFrame with `bars` rows of `close` interpolating start->end."""
    dates = pd.date_range("2024-01-01", periods=bars, freq="B")
    close = np.linspace(start, end, bars)
    return pd.DataFrame(
        {
            "date": dates,
            "open": close,
            "high": close * 1.005,
            "low": close * 0.995,
            "close": close,
            "volume": np.full(bars, 1_000_000.0),
        }
    )


class _FakeLoader:
    """Minimal loader: returns canned frames for indices and stocks."""

    def __init__(self, indices: dict[str, pd.DataFrame], stocks: dict[str, pd.DataFrame]):
        self._indices = indices
        self._stocks = stocks

    def get_index(self, name: str) -> pd.DataFrame:
        return self._indices.get(name.upper(), pd.DataFrame())

    def get_stock_daily(self, symbol: str) -> pd.DataFrame:
        return self._stocks.get(symbol.upper(), pd.DataFrame())

    def get_recent_close_stats(
        self,
        symbols,
        *,
        ma_periods=(50, 200),
    ) -> dict[str, dict[str, float | int | None]]:
        ma_periods = tuple(sorted({int(p) for p in ma_periods if p > 0}))
        out: dict[str, dict[str, float | int | None]] = {}
        for symbol in symbols:
            frame = self._stocks.get(str(symbol).upper())
            if frame is None or len(frame) == 0:
                continue
            close = pd.to_numeric(frame["close"], errors="coerce").dropna()
            if len(close) == 0:
                continue
            record: dict[str, float | int | None] = {
                "latest": float(close.iloc[-1]),
                "prior": float(close.iloc[-2]) if len(close) >= 2 else None,
                "bars": int(len(close)),
            }
            for p in ma_periods:
                tail = close.iloc[-p:] if len(close) >= p else close
                record[f"ma{p}"] = float(tail.mean()) if len(tail) > 0 else None
            out[str(symbol).upper()] = record
        return out


def _build_loader(sector_returns: dict[str, float], nifty_return: float = 0.0) -> _FakeLoader:
    """Build a loader where each sector index gains `sector_returns[name]%` over the
    last 260 bars, Nifty gains `nifty_return%`, and each sector has 5 mock
    constituents whose 200d trend mirrors the sector's direction.
    """
    nifty = _trending_frame(100.0, 100.0 * (1.0 + nifty_return / 100.0))
    indices = {"NIFTY 50": nifty}
    stocks = {}
    for sector, ret in sector_returns.items():
        end_price = 100.0 * (1.0 + ret / 100.0)
        indices[sector] = _trending_frame(100.0, end_price)
        # Stocks track the same direction so breadth reflects sector return.
        for i in range(5):
            stocks[f"{_short(sector)}{i}"] = _trending_frame(100.0, end_price)
    return _FakeLoader(indices, stocks)


def _short(sector: str) -> str:
    return sector.replace("NIFTY ", "").replace(" ", "")[:6]


def _sector_map(sector_returns: dict[str, float]) -> dict:
    out: dict[str, dict] = {}
    for sector in sector_returns:
        for i in range(5):
            out[f"{_short(sector)}{i}"] = {"sector_index": sector, "industry": "Test"}
    return out


class SectorLeaderboardComputeTest(unittest.TestCase):

    def test_leading_sector_outranks_lagging(self):
        loader = _build_loader({"NIFTY IT": 25.0, "NIFTY METAL": -10.0}, nifty_return=5.0)
        result = sector_leaderboard.compute_leaderboard(
            loader, sector_map=_sector_map({"NIFTY IT": 25.0, "NIFTY METAL": -10.0})
        )
        rows = result["rows"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["sector"], "NIFTY IT")
        self.assertEqual(rows[0]["rank"], 1)
        self.assertEqual(rows[0]["tier"], "LEADING")
        self.assertEqual(rows[1]["sector"], "NIFTY METAL")
        self.assertEqual(rows[1]["tier"], "LAGGING")
        self.assertGreater(rows[0]["composite_score"], rows[1]["composite_score"])

    def test_rs_pct_signs_correct(self):
        loader = _build_loader({"NIFTY IT": 20.0}, nifty_return=5.0)
        result = sector_leaderboard.compute_leaderboard(
            loader, sector_map=_sector_map({"NIFTY IT": 20.0})
        )
        row = result["rows"][0]
        self.assertIsNotNone(row["rs_6m_pct"])
        self.assertGreater(row["rs_6m_pct"], 0.0, "Outperforming sector must have RS > 0")

    def test_composite_score_in_zero_to_hundred(self):
        loader = _build_loader(
            {"NIFTY IT": 30.0, "NIFTY METAL": -20.0, "NIFTY AUTO": 0.0},
            nifty_return=5.0,
        )
        result = sector_leaderboard.compute_leaderboard(
            loader,
            sector_map=_sector_map({"NIFTY IT": 30.0, "NIFTY METAL": -20.0, "NIFTY AUTO": 0.0}),
        )
        for row in result["rows"]:
            self.assertIsNotNone(row["composite_score"])
            self.assertGreaterEqual(row["composite_score"], 0.0)
            self.assertLessEqual(row["composite_score"], 100.0)

    def test_tier_assignment(self):
        loader = _build_loader(
            {"NIFTY IT": 35.0, "NIFTY METAL": -15.0, "NIFTY AUTO": 6.0},
            nifty_return=5.0,
        )
        result = sector_leaderboard.compute_leaderboard(
            loader,
            sector_map=_sector_map({"NIFTY IT": 35.0, "NIFTY METAL": -15.0, "NIFTY AUTO": 6.0}),
        )
        tiers = {row["sector"]: row["tier"] for row in result["rows"]}
        self.assertEqual(tiers["NIFTY IT"], "LEADING")
        self.assertEqual(tiers["NIFTY METAL"], "LAGGING")
        # AUTO ~1% above Nifty, modest breadth — should be NEUTRAL.
        self.assertIn(tiers["NIFTY AUTO"], ("NEUTRAL", "LEADING"))

    def test_breadth_reflects_constituents(self):
        loader = _build_loader({"NIFTY IT": 25.0}, nifty_return=5.0)
        result = sector_leaderboard.compute_leaderboard(
            loader, sector_map=_sector_map({"NIFTY IT": 25.0})
        )
        row = result["rows"][0]
        # All 5 constituents trend up linearly so latest > MA50 and > MA200.
        self.assertEqual(row["breadth_50dma_pct"], 100.0)
        self.assertEqual(row["breadth_200dma_pct"], 100.0)

    def test_stage2_detection(self):
        loader = _build_loader({"NIFTY IT": 25.0}, nifty_return=5.0)
        result = sector_leaderboard.compute_leaderboard(
            loader, sector_map=_sector_map({"NIFTY IT": 25.0})
        )
        self.assertTrue(result["rows"][0]["stage2"])

    def test_ranks_are_sequential(self):
        loader = _build_loader(
            {"NIFTY IT": 30.0, "NIFTY METAL": -10.0, "NIFTY AUTO": 8.0},
            nifty_return=5.0,
        )
        result = sector_leaderboard.compute_leaderboard(
            loader,
            sector_map=_sector_map({"NIFTY IT": 30.0, "NIFTY METAL": -10.0, "NIFTY AUTO": 8.0}),
        )
        ranks = [row["rank"] for row in result["rows"]]
        self.assertEqual(ranks, [1, 2, 3])

    def test_empty_sector_map_returns_no_rows(self):
        loader = _FakeLoader({"NIFTY 50": _trending_frame(100.0, 105.0)}, {})
        result = sector_leaderboard.compute_leaderboard(loader, sector_map={})
        self.assertEqual(result["rows"], [])

    def test_missing_index_data_handled(self):
        """Sectors with no index data must still appear but with None scores."""
        loader = _FakeLoader(
            {"NIFTY 50": _trending_frame(100.0, 105.0)},
            {"ITX0": _trending_frame(100.0, 120.0)},
        )
        result = sector_leaderboard.compute_leaderboard(
            loader,
            sector_map={"ITX0": {"sector_index": "NIFTY IT", "industry": "Test"}},
        )
        self.assertEqual(len(result["rows"]), 1)
        row = result["rows"][0]
        self.assertIsNone(row["composite_score"])
        self.assertEqual(row["tier"], "UNKNOWN")


class CompositeScoreFormulaTest(unittest.TestCase):

    def test_rs_normalization_caps(self):
        # rs >= +10 -> 1.0; rs <= -10 -> 0.0
        self.assertAlmostEqual(sector_leaderboard._normalize_rs(15.0), 1.0)
        self.assertAlmostEqual(sector_leaderboard._normalize_rs(-15.0), 0.0)
        self.assertAlmostEqual(sector_leaderboard._normalize_rs(0.0), 0.5)

    def test_score_only_rs_components_when_breadth_missing(self):
        # If breadth is None, score uses only RS + stage2 components and
        # renormalizes weights. Must still return a finite 0-100 value.
        score = sector_leaderboard._composite_score(
            rs={"1m": 5.0, "3m": 5.0, "6m": 5.0},
            breadth_50=None,
            breadth_200=None,
            stage2=True,
        )
        self.assertIsNotNone(score)
        self.assertGreater(score, 50.0)
        self.assertLessEqual(score, 100.0)

    def test_all_rs_missing_returns_none(self):
        score = sector_leaderboard._composite_score(
            rs={"1m": None, "3m": None, "6m": None},
            breadth_50=80.0,
            breadth_200=70.0,
            stage2=True,
        )
        self.assertIsNone(score)


if __name__ == "__main__":
    unittest.main()
