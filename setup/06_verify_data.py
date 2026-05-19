"""Verify local market data infrastructure and broad universe coverage."""

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
from engine import storage, universe
from filters import liquidity

BROAD_MIN_COVERAGE = 0.95
BROAD_SAMPLE_SYMBOLS = ["MRPL", "MAZDOCK", "GRSE", "ADANIPOWER", "AEROFLEX", "STLTECH", "VEDL"]


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


def _active_symbols_from_csv(path: Path) -> list[str]:
    if not path.exists():
        return []
    frame = pd.read_csv(path, dtype=str).fillna("")
    if "status" in frame.columns:
        frame = frame[frame["status"].astype(str).str.upper().eq("ACTIVE")]
    return sorted(set(frame["symbol"].astype(str).str.upper().str.strip()) - {""})


def _table_symbols(conn: sqlite3.Connection, table: str, symbol_col: str = "symbol") -> set[str]:
    if not _table_exists(conn, table):
        return set()
    frame = storage.query_frame(
        conn,
        f"""
        SELECT DISTINCT {symbol_col} AS symbol
        FROM {table}
        WHERE close > 0
        """,
    )
    if frame.empty:
        return set()
    return set(frame["symbol"].astype(str).str.upper())


def _coverage_detail(active: list[str], covered: set[str]) -> tuple[int, int, float, list[str]]:
    active_set = set(active)
    covered_count = len(active_set & covered)
    total = len(active_set)
    coverage = (covered_count / total) if total else 0.0
    missing = sorted(active_set - covered)
    return covered_count, total, coverage, missing


def _check_broad_universe_csv() -> tuple[bool, str]:
    if not settings.ALL_NSE_EQUITY_CSV.exists():
        return False, "config/all_nse_equity.csv missing"
    frame = pd.read_csv(settings.ALL_NSE_EQUITY_CSV, dtype=str).fillna("")
    if "status" in frame.columns:
        active = frame[frame["status"].astype(str).str.upper().eq("ACTIVE")].copy()
    else:
        active = frame.copy()
    security_ids = active["security_id"].astype(str).str.strip()
    missing_ids = int(security_ids.eq("").sum())
    sample_missing = sorted(set(BROAD_SAMPLE_SYMBOLS) - set(active["symbol"].astype(str).str.upper()))
    ok = len(active) >= 1500 and missing_ids == 0 and not sample_missing
    detail = (
        f"{len(active)} active broad NSE symbols, missing_security_id={missing_ids}, "
        f"sample_missing={sample_missing[:10]}"
    )
    return ok, detail


def _check_broad_daily(conn: sqlite3.Connection) -> tuple[bool, str]:
    active = _active_symbols_from_csv(settings.ALL_NSE_EQUITY_CSV)
    daily_symbols = _table_symbols(conn, "ohlcv_daily")
    covered, total, coverage, missing = _coverage_detail(active, daily_symbols)
    latest = conn.execute("SELECT MAX(date) FROM ohlcv_daily WHERE close > 0").fetchone()[0] or "none"
    ok = total > 0 and coverage >= BROAD_MIN_COVERAGE
    detail = (
        f"{covered}/{total} broad symbols have daily rows ({coverage:.1%}), "
        f"latest {latest}, missing_count={len(missing)}, missing_sample={missing[:20]}"
    )
    return ok, detail


def _check_broad_weekly(conn: sqlite3.Connection) -> tuple[bool, str]:
    active = _active_symbols_from_csv(settings.ALL_NSE_EQUITY_CSV)
    daily_symbols = _table_symbols(conn, "ohlcv_daily")
    weekly_symbols = _table_symbols(conn, "ohlcv_weekly")
    downloaded = sorted(set(active) & daily_symbols)
    covered, total, coverage, missing = _coverage_detail(downloaded, weekly_symbols)
    latest = conn.execute("SELECT MAX(week) FROM ohlcv_weekly WHERE close > 0").fetchone()[0] or "none"
    ok = total > 0 and covered == total
    detail = (
        f"{covered}/{total} downloaded broad symbols have weekly rows ({coverage:.1%}), "
        f"latest {latest}, missing_count={len(missing)}, missing_sample={missing[:20]}"
    )
    return ok, detail


def _check_watchlist_coverage(conn: sqlite3.Connection) -> tuple[bool, str]:
    active = _active_symbols_from_csv(settings.WATCHLIST_CSV)
    daily_symbols = _table_symbols(conn, "ohlcv_daily")
    weekly_symbols = _table_symbols(conn, "ohlcv_weekly")
    daily_covered, total, daily_coverage, daily_missing = _coverage_detail(active, daily_symbols)
    weekly_covered, _, weekly_coverage, weekly_missing = _coverage_detail(active, weekly_symbols)
    ok = total > 0 and daily_covered == total and weekly_covered == total
    detail = (
        f"daily {daily_covered}/{total} ({daily_coverage:.1%}), "
        f"weekly {weekly_covered}/{total} ({weekly_coverage:.1%}), "
        f"daily_missing={daily_missing}, weekly_missing={weekly_missing}"
    )
    return ok, detail


def _check_small_mid_liquid_profile(conn: sqlite3.Connection) -> tuple[bool, str]:
    if not settings.SMALL_MID_LIQUID_CSV.exists():
        return False, "config/small_mid_liquid.csv missing"
    frame = pd.read_csv(settings.SMALL_MID_LIQUID_CSV, dtype=str).fillna("")
    required = set(liquidity.PROFILE_COLUMNS)
    missing_columns = sorted(required - set(frame.columns))
    if missing_columns:
        return False, f"missing column(s): {missing_columns}"
    resolved = universe.load_universe_profile("small_mid_liquid")
    active_symbols = resolved["symbol"].astype(str).str.upper().tolist()
    daily_symbols = _table_symbols(conn, "ohlcv_daily")
    covered, total, coverage, missing_daily = _coverage_detail(active_symbols, daily_symbols)
    nifty_overlap = sorted(set(active_symbols) & set(_active_symbols_from_csv(settings.NIFTY500_DHAN_CSV)))
    active = frame[frame["status"].astype(str).str.upper().eq("ACTIVE")].copy()
    pass_flags = active["liquidity_pass"].astype(str).str.lower().isin({"true", "1", "yes"})
    traded_value = pd.to_numeric(active["avg_traded_value_50d"], errors="coerce").fillna(0)
    avg_volume = pd.to_numeric(active["avg_volume_50d"], errors="coerce").fillna(0)
    latest_close = pd.to_numeric(active["latest_close"], errors="coerce").fillna(0)
    rules = liquidity.DEFAULT_RULES
    ok = (
        total >= 100
        and covered == total
        and pass_flags.all()
        and traded_value.min() >= rules.min_avg_traded_value_50d
        and avg_volume.min() >= rules.min_avg_volume_50d
        and latest_close.min() >= rules.min_price
        and not nifty_overlap
    )
    detail = (
        f"{total} symbols, daily {covered}/{total} ({coverage:.1%}), "
        f"min_avg_value={traded_value.min():.0f}, min_avg_volume={avg_volume.min():.0f}, "
        f"min_close={latest_close.min():.2f}, nifty_overlap={nifty_overlap[:10]}, "
        f"missing_daily={missing_daily[:10]}"
    )
    return ok, detail


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
        ("broad_universe_csv", _check_broad_universe_csv),
        ("broad_daily_coverage", lambda: _check_broad_daily(conn)),
        ("broad_weekly_coverage", lambda: _check_broad_weekly(conn)),
        ("watchlist_coverage", lambda: _check_watchlist_coverage(conn)),
        ("small_mid_liquid_profile", lambda: _check_small_mid_liquid_profile(conn)),
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
    print(f"Data coverage checks: {passed}/{total}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
