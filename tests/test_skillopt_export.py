"""SkillOpt dataset export tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backtest.metrics import BacktestResult
from backtest.skillopt_export import (
    PROMOTE,
    REJECT,
    build_skillopt_item,
    export_backtest_result,
    split_items,
)


class SkillOptExportTest(unittest.TestCase):
    def test_item_uses_signal_context_and_keeps_future_outcome_in_metadata(self):
        item = build_skillopt_item(_trade(result="LOSS", return_pct=-4.5), universe="watchlist", index=1)

        self.assertEqual(item["answers"], [REJECT])
        self.assertIn("Primary pattern: Ascending Triangle", item["context"])
        self.assertIn("stage2", item["context"])
        self.assertNotIn("future_result", item["context"])
        self.assertNotIn("future_return_pct", item["context"])
        self.assertEqual(item["metadata"]["future_result"], "LOSS")
        self.assertEqual(item["metadata"]["future_return_pct"], -4.5)

    def test_winning_trade_is_promoted_when_it_meets_label_rule(self):
        item = build_skillopt_item(
            _trade(result="WIN", return_pct=8.0, max_drawdown_pct=-2.0),
            universe="small_mid_liquid",
            index=1,
            min_promote_return_pct=5.0,
            max_promote_drawdown_pct=3.0,
        )

        self.assertEqual(item["answers"], [PROMOTE])

    def test_split_is_chronological(self):
        items = [
            {"id": "late", "metadata": {"signal_date": "2026-03-01"}, "answers": [PROMOTE]},
            {"id": "early", "metadata": {"signal_date": "2026-01-01"}, "answers": [REJECT]},
            {"id": "middle", "metadata": {"signal_date": "2026-02-01"}, "answers": [PROMOTE]},
        ]

        splits = split_items(items)

        self.assertEqual(splits["train"][0]["id"], "early")
        self.assertEqual(splits["val"][0]["id"], "middle")
        self.assertEqual(splits["test"][0]["id"], "late")

    def test_export_writes_skillopt_split_files(self):
        result = BacktestResult(
            [
                _trade(symbol="AAA", signal_date="2026-01-01", result="WIN", return_pct=4.0),
                _trade(symbol="BBB", signal_date="2026-02-01", result="LOSS", return_pct=-3.0),
                _trade(symbol="CCC", signal_date="2026-03-01", result="TIMEOUT", return_pct=1.0),
            ],
            universe="watchlist",
            config={"entry_mode": "next_open"},
        )
        with tempfile.TemporaryDirectory() as tmp:
            summary = export_backtest_result(result, Path(tmp))

            self.assertEqual(summary.total_items, 3)
            self.assertEqual(summary.split_counts, {"train": 1, "val": 1, "test": 1})
            self.assertTrue((Path(tmp) / "initial_skill.md").exists())
            self.assertTrue((Path(tmp) / "skillopt_command.ps1").exists())
            manifest = json.loads((Path(tmp) / "manifest.json").read_text(encoding="utf-8"))
            train_items = json.loads((Path(tmp) / "train" / "items.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["format"], "skillopt_searchqa_split")
        self.assertEqual(train_items[0]["metadata"]["symbol"], "AAA")


def _trade(
    *,
    symbol: str = "TEST",
    signal_date: str = "2026-01-01",
    result: str = "WIN",
    return_pct: float = 6.0,
    max_drawdown_pct: float = -1.5,
) -> dict:
    return {
        "symbol": symbol,
        "signal_date": signal_date,
        "pattern": "Ascending Triangle",
        "score": 82,
        "tier": "HIGH",
        "pattern_quality_score": 86,
        "pattern_confidence": 78,
        "pattern_timeframe": "daily",
        "stacked_count": 1,
        "all_patterns": ["Ascending Triangle"],
        "bars_in_pattern": 55,
        "entry_price": 100.0,
        "target": 112.0,
        "stop_loss": 94.0,
        "reward_risk": 2.0,
        "filters": {
            "stage2": {"status": "PASS", "passed": True, "details": {"above_50ma": True}},
            "volume": {"status": "PASS", "passed": True, "details": {"breakout_volume_ratio": 1.8}},
        },
        "pattern_extra": {"touch_count": 3, "base_depth_pct": 9.5},
        "result": result,
        "return_pct": return_pct,
        "exit_date": "2026-01-12",
        "max_drawdown_pct": max_drawdown_pct,
    }


if __name__ == "__main__":
    unittest.main()
