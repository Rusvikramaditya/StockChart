"""SQLite helpers for Phase 1 data infrastructure."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable

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

        CREATE TABLE IF NOT EXISTS sent_alerts (
            symbol TEXT,
            pattern TEXT,
            signal_date TEXT,
            sent_at TEXT,
            PRIMARY KEY (symbol, pattern, signal_date)
        );

        CREATE TABLE IF NOT EXISTS signal_history (
            symbol TEXT NOT NULL,
            pattern TEXT NOT NULL,
            signal_date TEXT NOT NULL,
            timeframe TEXT,
            tier TEXT,
            score INTEGER,
            status TEXT,
            company_name TEXT,
            sector TEXT,
            cmp REAL,
            entry_price REAL,
            target REAL,
            stop_loss REAL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            PRIMARY KEY (symbol, pattern, signal_date)
        );

        CREATE INDEX IF NOT EXISTS idx_signal_history_date
        ON signal_history(signal_date);

        CREATE INDEX IF NOT EXISTS idx_signal_history_symbol
        ON signal_history(symbol);
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


def alert_was_sent(conn: sqlite3.Connection, symbol: str, pattern: str, signal_date: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM sent_alerts
        WHERE symbol = ? AND pattern = ? AND signal_date = ?
        LIMIT 1
        """,
        (symbol.upper(), str(pattern), str(signal_date)),
    ).fetchone()
    return row is not None


def record_alert_sent(conn: sqlite3.Connection, symbol: str, pattern: str, signal_date: str, sent_at: str) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO sent_alerts
        (symbol, pattern, signal_date, sent_at)
        VALUES (?, ?, ?, ?)
        """,
        (symbol.upper(), str(pattern), str(signal_date), str(sent_at)),
    )
    conn.commit()


def record_signal_history(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> int:
    """Upsert visible report signals into the durable signal ledger."""
    prepared = []
    for row in rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        pattern = str(row.get("pattern") or "").strip()
        signal_date = str(row.get("signal_date") or "").strip()
        seen_at = str(row.get("seen_at") or "").strip()
        if not symbol or not pattern or not signal_date or not seen_at:
            continue
        prepared.append(
            (
                symbol,
                pattern,
                signal_date,
                _optional_text(row.get("timeframe")),
                _optional_text(row.get("tier")),
                _optional_int(row.get("score")),
                _optional_text(row.get("status")),
                _optional_text(row.get("company_name")),
                _optional_text(row.get("sector")),
                _optional_float(row.get("cmp")),
                _optional_float(row.get("entry_price")),
                _optional_float(row.get("target")),
                _optional_float(row.get("stop_loss")),
                seen_at,
                seen_at,
            )
        )
    if not prepared:
        return 0
    conn.executemany(
        """
        INSERT INTO signal_history
        (symbol, pattern, signal_date, timeframe, tier, score, status, company_name,
         sector, cmp, entry_price, target, stop_loss, first_seen_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, pattern, signal_date) DO UPDATE SET
            timeframe = excluded.timeframe,
            tier = excluded.tier,
            score = excluded.score,
            status = excluded.status,
            company_name = excluded.company_name,
            sector = excluded.sector,
            cmp = excluded.cmp,
            entry_price = excluded.entry_price,
            target = excluded.target,
            stop_loss = excluded.stop_loss,
            last_seen_at = excluded.last_seen_at
        """,
        prepared,
    )
    conn.commit()
    return len(prepared)


def fetch_recent_signal_history(
    conn: sqlite3.Connection,
    *,
    since_date: str,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    sql = """
        SELECT symbol, pattern, signal_date, timeframe, tier, score, status,
               company_name, sector, cmp, entry_price, target, stop_loss,
               first_seen_at, last_seen_at
        FROM signal_history
        WHERE signal_date >= ?
        ORDER BY signal_date DESC, symbol ASC, pattern ASC
        """
    params: list[Any] = [str(since_date)]
    if limit is not None:
        sql += "\n        LIMIT ?"
        params.append(int(limit))
    frame = query_frame(
        conn,
        sql,
        params,
    )
    return frame.to_dict("records")


def fetch_daily_rows_since(
    conn: sqlite3.Connection,
    symbols: Iterable[str],
    *,
    since_date: str,
) -> list[dict[str, Any]]:
    upper_symbols = sorted({str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()})
    if not upper_symbols:
        return []
    frames = []
    for start in range(0, len(upper_symbols), 800):
        batch = upper_symbols[start : start + 800]
        placeholders = ",".join("?" for _ in batch)
        frames.append(
            query_frame(
                conn,
                f"""
                SELECT symbol, date, open, high, low, close, volume
                FROM ohlcv_daily
                WHERE symbol IN ({placeholders}) AND date >= ?
                ORDER BY symbol, date
                """,
                [*batch, str(since_date)],
            )
        )
    frame = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return frame.to_dict("records")


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _optional_int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None
