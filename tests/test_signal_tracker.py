"""Signal lifecycle tracker tests."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from engine import signal_tracker, storage


NOW = datetime(2026, 6, 10, 18, 45)


def test_tracker_keeps_missing_current_card_as_still_valid(tmp_path: Path):
    conn = storage.connect(tmp_path / "tracker.db")
    try:
        storage.ensure_schema(conn)
        storage.record_signal_history(
            conn,
            [
                {
                    "symbol": "AAA",
                    "pattern": "Flat Base",
                    "signal_date": "2026-06-09",
                    "timeframe": "daily",
                    "tier": "HIGH",
                    "score": 82,
                    "cmp": 100,
                    "entry_price": 100,
                    "target": 120,
                    "stop_loss": 94,
                    "seen_at": "2026-06-09T18:45:00",
                }
            ],
        )
        storage.upsert_daily_rows(
            conn,
            "AAA",
            "1",
            pd.DataFrame(
                [
                    {"date": "2026-06-09", "open": 98, "high": 101, "low": 97, "close": 100, "volume": 1000},
                    {"date": "2026-06-10", "open": 101, "high": 105, "low": 99, "close": 104, "volume": 1200},
                ]
            ),
        )

        rows = signal_tracker.build_tracker(conn, [], generated_at=NOW, data_as_of="2026-06-10")
    finally:
        conn.close()

    assert len(rows) == 1
    row = rows[0]
    assert row["symbol"] == "AAA"
    assert row["status"] == "Still Valid"
    assert row["fresh_state"] == "Not fresh today"
    assert row["latest_close"] == 104
    assert row["change_pct"] == 4.0
    assert row["current_conviction"] == "Still valid"
    assert row["trigger_state"] == "Extended"
    assert row["entry_decision"] == "Hold if already in"
    assert row["decision_reason"] == "The old trade remains valid, but it is not a new signal today."
    assert "No fresh card today" in row["reason"]


def test_tracker_marks_stop_before_target_as_invalidated(tmp_path: Path):
    conn = storage.connect(tmp_path / "tracker.db")
    try:
        storage.ensure_schema(conn)
        storage.record_signal_history(
            conn,
            [
                {
                    "symbol": "STOP",
                    "pattern": "Bull Flag",
                    "signal_date": "2026-06-09",
                    "timeframe": "daily",
                    "tier": "HIGH",
                    "score": 80,
                    "cmp": 100,
                    "entry_price": 100,
                    "target": 115,
                    "stop_loss": 95,
                    "seen_at": "2026-06-09T18:45:00",
                }
            ],
        )
        storage.upsert_daily_rows(
            conn,
            "STOP",
            "2",
            pd.DataFrame(
                [
                    {"date": "2026-06-09", "open": 99, "high": 102, "low": 98, "close": 100, "volume": 1000},
                    {"date": "2026-06-10", "open": 98, "high": 101, "low": 94, "close": 96, "volume": 1400},
                    {"date": "2026-06-11", "open": 100, "high": 116, "low": 99, "close": 114, "volume": 1600},
                ]
            ),
        )

        rows = signal_tracker.build_tracker(conn, [], generated_at=NOW, data_as_of="2026-06-11")
    finally:
        conn.close()

    assert rows[0]["status"] == "Invalidated"
    assert rows[0]["status_class"] == "invalid"
    assert rows[0]["stop_hit_date"] == "2026-06-10"
    assert rows[0]["entry_decision"] == "Exit / Avoid"
    assert "stop was touched" in rows[0]["reason"]


def test_fresh_watch_signal_explains_trigger_before_entry(tmp_path: Path):
    conn = storage.connect(tmp_path / "tracker.db")
    try:
        storage.ensure_schema(conn)
        storage.record_signal_history(
            conn,
            [
                {
                    "symbol": "WAIT",
                    "pattern": "Ascending Triangle",
                    "signal_date": "2026-06-10",
                    "timeframe": "daily",
                    "tier": "HIGH",
                    "score": 86,
                    "cmp": 114.0,
                    "entry_price": 115.0,
                    "target": 170.0,
                    "stop_loss": 100.0,
                    "seen_at": "2026-06-10T18:45:00",
                }
            ],
        )
        storage.upsert_daily_rows(
            conn,
            "WAIT",
            "3",
            pd.DataFrame(
                [
                    {"date": "2026-06-10", "open": 113, "high": 114.8, "low": 112, "close": 114.0, "volume": 1000},
                ]
            ),
        )

        rows = signal_tracker.build_tracker(
            conn,
            [{"symbol": "WAIT", "tier": "HIGH", "score": 86, "tradable": True}],
            generated_at=NOW,
            data_as_of="2026-06-10",
        )
    finally:
        conn.close()

    row = rows[0]
    assert row["same_day"] is True
    assert row["fresh_state"] == "Fresh today"
    assert row["current_conviction"] == "Fresh HIGH 86"
    assert row["trigger_state"] == "Waiting for trigger"
    assert row["trigger_price"] == 115.0
    assert "crosses or closes above entry" in row["trigger_note"]
    assert row["entry_decision"] == "Wait for trigger"
    assert "has not crossed the entry trigger" in row["decision_reason"]


def test_tracker_includes_full_lookback_beyond_200_rows(tmp_path: Path):
    conn = storage.connect(tmp_path / "tracker.db")
    try:
        storage.ensure_schema(conn)
        rows = [
            {
                "symbol": f"SYM{index:03d}",
                "pattern": "Flat Base",
                "signal_date": "2026-06-10",
                "timeframe": "daily",
                "tier": "MEDIUM",
                "score": 70,
                "cmp": 100,
                "entry_price": 100,
                "target": 120,
                "stop_loss": 94,
                "seen_at": "2026-06-10T18:45:00",
            }
            for index in range(205)
        ]
        rows.append(
            {
                "symbol": "PRIVISCL",
                "pattern": "Double Bottom",
                "signal_date": "2026-06-09",
                "timeframe": "daily",
                "tier": "HIGH",
                "score": 100,
                "cmp": 3444.3,
                "entry_price": 3444.3,
                "target": 4343.29,
                "stop_loss": 2989.11,
                "seen_at": "2026-06-09T18:45:00",
            }
        )
        storage.record_signal_history(conn, rows)

        tracked = signal_tracker.build_tracker(conn, [], generated_at=NOW, data_as_of="2026-06-10")
    finally:
        conn.close()

    assert len(tracked) == 206
    assert "PRIVISCL" in {row["symbol"] for row in tracked}


def test_record_current_signals_skips_non_visible_tiers(tmp_path: Path):
    conn = storage.connect(tmp_path / "tracker.db")
    try:
        storage.ensure_schema(conn)
        recorded = signal_tracker.record_current_signals(
            conn,
            [
                {
                    "symbol": "AAA",
                    "pattern": "Flat Base",
                    "signal_date": "2026-06-10",
                    "tier": "HIGH",
                    "tradable": True,
                    "cmp": 101,
                    "entry_price": 100,
                    "target": 120,
                    "stop_loss": 94,
                },
                {
                    "symbol": "SKIPME",
                    "pattern": "VCP",
                    "signal_date": "2026-06-10",
                    "tier": "SKIP",
                    "tradable": False,
                },
            ],
            generated_at=NOW,
            data_as_of="2026-06-10",
        )
        rows = storage.fetch_recent_signal_history(conn, since_date="2026-06-01")
    finally:
        conn.close()

    assert recorded == 1
    assert [row["symbol"] for row in rows] == ["AAA"]
