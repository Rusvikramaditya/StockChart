"""Verify Phase 1 data infrastructure."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd
from tabulate import tabulate

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import settings
from engine import storage


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _check_symbols() -> tuple[bool, str]:
    if not settings.NIFTY500_CSV.exists():
        return False, "config/nifty500.csv missing"
    df = pd.read_csv(settings.NIFTY500_CSV)
    return len(df) >= 450, f"{len(df)} Nifty 500 rows"


def _check_dhan_symbols() -> tuple[bool, str]:
    if not settings.NIFTY500_DHAN_CSV.exists():
        return False, "config/nifty500_dhan.csv missing"
    df = pd.read_csv(settings.NIFTY500_DHAN_CSV, dtype={"security_id": str})
    if "status" not in df.columns:
        df["status"] = "ACTIVE"
    active = df[df["status"].astype(str).str.upper().eq("ACTIVE")]
    security_ids = active["security_id"].fillna("").astype(str).str.strip()
    security_ids = security_ids.mask(security_ids.str.lower().isin({"nan", "none"}), "")
    matched = security_ids.ne("").sum()
    return matched >= 450, f"{matched}/{len(active)} active symbols have security_id"


def _check_schema(conn: sqlite3.Connection) -> tuple[bool, str]:
    expected = ["ohlcv_daily", "ohlcv_weekly", "index_daily"]
    missing = [table for table in expected if not _table_exists(conn, table)]
    return not missing, "schema ok" if not missing else f"missing tables: {missing}"


def _check_daily(conn: sqlite3.Connection) -> tuple[bool, str]:
    df = storage.query_frame(
        conn,
        """
        SELECT symbol, COUNT(*) AS rows, MAX(date) AS latest
        FROM ohlcv_daily
        WHERE close > 0
        GROUP BY symbol
        """,
    )
    good = df[df["rows"] >= 200]
    latest = str(df["latest"].max()) if not df.empty else "none"
    return len(good) >= 450, f"{len(good)} symbols >=200 daily rows, latest {latest}"


def _check_weekly(conn: sqlite3.Connection) -> tuple[bool, str]:
    df = storage.query_frame(
        conn,
        """
        SELECT symbol, COUNT(*) AS rows
        FROM ohlcv_weekly
        WHERE close > 0
        GROUP BY symbol
        """,
    )
    good = df[df["rows"] >= 50]
    return len(good) >= 450, f"{len(good)} symbols >=50 weekly rows"


def _check_indices(conn: sqlite3.Connection) -> tuple[bool, str]:
    required = ["NIFTY 50", *settings.SECTOR_INDICES]
    placeholders = ",".join("?" for _ in required)
    df = storage.query_frame(
        conn,
        f"""
        SELECT index_name, COUNT(*) AS rows
        FROM index_daily
        WHERE close > 0 AND index_name IN ({placeholders})
        GROUP BY index_name
        """,
        [name.upper() for name in required],
    )
    good = df[df["rows"] >= 50]
    has_nifty = "NIFTY 50" in set(df["index_name"].astype(str).str.upper())
    passed = has_nifty and len(good) >= 10
    return passed, f"{len(good)}/{len(required)} required indices >=50 rows; NIFTY 50={has_nifty}"


def _check_sector_map() -> tuple[bool, str]:
    if not settings.SECTOR_MAP_JSON.exists():
        return False, "config/sector_map.json missing"
    data = json.loads(settings.SECTOR_MAP_JSON.read_text(encoding="utf-8"))
    mapped = data.get("symbols", {})
    return len(mapped) >= 450, f"{len(mapped)} symbols mapped"


def verify_data() -> tuple[int, int, list[dict[str, str]]]:
    details: list[dict[str, str]] = []
    conn = storage.connect()
    checks = [
        ("nifty500_csv", _check_symbols),
        ("nifty500_dhan_csv", _check_dhan_symbols),
        ("sqlite_schema", lambda: _check_schema(conn)),
        ("ohlcv_daily", lambda: _check_daily(conn)),
        ("ohlcv_weekly", lambda: _check_weekly(conn)),
        ("index_daily", lambda: _check_indices(conn)),
        ("sector_map", _check_sector_map),
    ]
    passed = 0
    for name, check in checks:
        try:
            ok, detail = check()
        except Exception as exc:
            ok, detail = False, str(exc)
        passed += int(ok)
        details.append({"check": name, "status": "PASS" if ok else "FAIL", "detail": detail})
    return passed, len(checks), details


def main() -> int:
    argparse.ArgumentParser().parse_args()
    passed, total, details = verify_data()
    print(tabulate(details, headers="keys", tablefmt="github"))
    print(f"Phase 1 data checks: {passed}/{total}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
