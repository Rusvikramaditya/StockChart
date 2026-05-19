"""SQLite helpers for Phase 1 data infrastructure."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

import pandas as pd

from config import settings


def ensure_directories() -> None:
    for path in [
        settings.DATA_DIR,
        settings.CONFIG_DIR,
        settings.OUTPUT_DIR,
        settings.CHARTS_DIR,
    ]:
        path.mkdir(parents=True, exist_ok=True)


def connect(db_path: Path | str = settings.DB_PATH) -> sqlite3.Connection:
    ensure_directories()
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS ohlcv_daily (
            symbol TEXT,
            security_id TEXT,
            date TEXT,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume INTEGER,
            PRIMARY KEY (symbol, date)
        );

        CREATE INDEX IF NOT EXISTS idx_sym ON ohlcv_daily(symbol);

        CREATE TABLE IF NOT EXISTS ohlcv_weekly (
            symbol TEXT,
            week TEXT,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume INTEGER,
            PRIMARY KEY (symbol, week)
        );

        CREATE TABLE IF NOT EXISTS index_daily (
            index_name TEXT,
            date TEXT,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume INTEGER,
            PRIMARY KEY (index_name, date)
        );
        """
    )
    conn.commit()


def normalise_ohlcv_frame(df: pd.DataFrame, date_column: str = "date") -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    frame = df.copy()
    frame[date_column] = pd.to_datetime(frame[date_column], errors="coerce")
    frame = frame.dropna(subset=[date_column])
    frame = frame.sort_values(date_column)
    frame[date_column] = frame[date_column].dt.strftime("%Y-%m-%d")
    for col in ["open", "high", "low", "close"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce").fillna(0).astype(int)
    frame = frame.dropna(subset=["open", "high", "low", "close"])
    return frame[["date", "open", "high", "low", "close", "volume"]]


def upsert_daily_rows(
    conn: sqlite3.Connection,
    symbol: str,
    security_id: str,
    df: pd.DataFrame,
) -> int:
    frame = normalise_ohlcv_frame(df)
    rows = [
        (
            symbol.upper(),
            str(security_id),
            row.date,
            float(row.open),
            float(row.high),
            float(row.low),
            float(row.close),
            int(row.volume),
        )
        for row in frame.itertuples(index=False)
    ]
    conn.executemany(
        """
        INSERT OR REPLACE INTO ohlcv_daily
        (symbol, security_id, date, open, high, low, close, volume)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def upsert_index_rows(conn: sqlite3.Connection, index_name: str, df: pd.DataFrame) -> int:
    frame = normalise_ohlcv_frame(df)
    rows = [
        (
            index_name.upper(),
            row.date,
            float(row.open),
            float(row.high),
            float(row.low),
            float(row.close),
            int(row.volume),
        )
        for row in frame.itertuples(index=False)
    ]
    conn.executemany(
        """
        INSERT OR REPLACE INTO index_daily
        (index_name, date, open, high, low, close, volume)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def upsert_weekly_rows(conn: sqlite3.Connection, symbol: str, df: pd.DataFrame) -> int:
    if df is None or df.empty:
        return 0
    rows = [
        (
            symbol.upper(),
            str(row.week),
            float(row.open),
            float(row.high),
            float(row.low),
            float(row.close),
            int(row.volume),
        )
        for row in df.itertuples(index=False)
    ]
    conn.executemany(
        """
        INSERT OR REPLACE INTO ohlcv_weekly
        (symbol, week, open, high, low, close, volume)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def query_frame(conn: sqlite3.Connection, sql: str, params: Iterable = ()) -> pd.DataFrame:
    return pd.read_sql_query(sql, conn, params=tuple(params))

