"""SQLite-backed data access for scanner and detector phases."""

from __future__ import annotations

import time
from datetime import date
from typing import Iterable

import numpy as np
import pandas as pd

from config import settings
from engine import dhan_client, storage, universe


class DataLoader:
    def __init__(self, db_path=settings.DB_PATH):
        self.db_path = db_path
        self.conn = storage.connect(db_path)
        storage.ensure_schema(self.conn)

    def close(self) -> None:
        self.conn.close()

    def get_stock_daily(self, symbol: str) -> pd.DataFrame:
        return storage.query_frame(
            self.conn,
            """
            SELECT date, open, high, low, close, volume
            FROM ohlcv_daily
            WHERE symbol = ?
            ORDER BY date
            """,
            (symbol.upper(),),
        )

    def get_stock_weekly(self, symbol: str) -> pd.DataFrame:
        return storage.query_frame(
            self.conn,
            """
            SELECT week, open, high, low, close, volume
            FROM ohlcv_weekly
            WHERE symbol = ?
            ORDER BY week
            """,
            (symbol.upper(),),
        )

    def get_daily_up_to(self, symbol: str, as_of_date: str | date) -> pd.DataFrame:
        return storage.query_frame(
            self.conn,
            """
            SELECT date, open, high, low, close, volume
            FROM ohlcv_daily
            WHERE symbol = ? AND date <= ?
            ORDER BY date
            """,
            (symbol.upper(), _date_text(as_of_date)),
        )

    def get_weekly_up_to(self, symbol: str, as_of_date: str | date) -> pd.DataFrame:
        return storage.query_frame(
            self.conn,
            """
            SELECT week, open, high, low, close, volume
            FROM ohlcv_weekly
            WHERE symbol = ? AND week <= ?
            ORDER BY week
            """,
            (symbol.upper(), _date_text(as_of_date)),
        )

    def get_index_up_to(self, index_name: str, as_of_date: str | date) -> pd.DataFrame:
        return storage.query_frame(
            self.conn,
            """
            SELECT date, open, high, low, close, volume
            FROM index_daily
            WHERE index_name = ? AND date <= ?
            ORDER BY date
            """,
            (index_name.upper(), _date_text(as_of_date)),
        )

    def get_stock_daily_after(
        self,
        symbol: str,
        after_date: str | date,
        *,
        limit: int,
    ) -> pd.DataFrame:
        return storage.query_frame(
            self.conn,
            """
            SELECT date, open, high, low, close, volume
            FROM ohlcv_daily
            WHERE symbol = ? AND date > ?
            ORDER BY date
            LIMIT ?
            """,
            (symbol.upper(), _date_text(after_date), int(limit)),
        )

    def get_trading_days(
        self,
        symbols: list[str],
        *,
        start_date: str | date | None = None,
        end_date: str | date | None = None,
    ) -> list[str]:
        if not symbols:
            return []
        days: set[str] = set()
        for chunk in _chunks([{"symbol": symbol.upper()} for symbol in symbols], 800):
            chunk_symbols = [row["symbol"] for row in chunk]
            params: list[object] = list(chunk_symbols)
            placeholders = ",".join("?" for _ in chunk_symbols)
            where = [f"symbol IN ({placeholders})"]
            if start_date is not None:
                where.append("date >= ?")
                params.append(_date_text(start_date))
            if end_date is not None:
                where.append("date <= ?")
                params.append(_date_text(end_date))
            frame = storage.query_frame(
                self.conn,
                f"""
                SELECT DISTINCT date
                FROM ohlcv_daily
                WHERE {' AND '.join(where)}
                ORDER BY date
                """,
                params,
            )
            days.update(frame["date"].astype(str).tolist())
        return sorted(days)

    def get_index(self, index_name: str) -> pd.DataFrame:
        return storage.query_frame(
            self.conn,
            """
            SELECT date, open, high, low, close, volume
            FROM index_daily
            WHERE index_name = ?
            ORDER BY date
            """,
            (index_name.upper(),),
        )

    def get_recent_close_stats(
        self,
        symbols: list[str],
        *,
        ma_periods: tuple[int, ...] = (50, 200),
    ) -> dict[str, dict[str, float | int | None]]:
        """Batch-load per-symbol close stats in a single SQLite query.

        Returns ``{symbol: {"latest": float, "prior": float, "ma50": float,
        "ma200": float, "bars": int}}`` for every symbol that has at least
        2 close rows in the DB. Used by market-regime breadth (advance /
        decline) and the sector-leaderboard breadth calculation -- both
        previously made one SELECT per symbol (~500 round trips per scan).

        ``ma_periods`` controls which moving-average columns are produced
        (default 50d + 200d). The largest period determines the window
        size requested from SQLite.
        """
        if not symbols:
            return {}
        ma_periods = tuple(sorted({int(p) for p in ma_periods if p > 0}))
        window = max(max(ma_periods) if ma_periods else 0, 2)

        upper_symbols = sorted({str(s).upper() for s in symbols if s})
        out: dict[str, dict[str, float | int | None]] = {}

        # SQLite has a default placeholder cap. Chunk the IN(...) list so
        # we never exceed it.
        chunk_size = 800
        for start in range(0, len(upper_symbols), chunk_size):
            batch = upper_symbols[start : start + chunk_size]
            placeholders = ",".join("?" for _ in batch)
            ma_select = "".join(
                f",\n                   AVG(CASE WHEN rn <= {p} THEN close END) AS ma{p}"
                for p in ma_periods
            )
            sql = f"""
            WITH ranked AS (
                SELECT symbol, close,
                       ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY date DESC) AS rn
                FROM ohlcv_daily
                WHERE symbol IN ({placeholders})
            )
            SELECT symbol,
                   MAX(CASE WHEN rn = 1 THEN close END) AS latest,
                   MAX(CASE WHEN rn = 2 THEN close END) AS prior{ma_select},
                   SUM(CASE WHEN rn <= {window} THEN 1 ELSE 0 END) AS bars
            FROM ranked
            WHERE rn <= {window}
            GROUP BY symbol
            """
            frame = storage.query_frame(self.conn, sql, batch)
            for row in frame.itertuples(index=False):
                sym = str(row.symbol).upper()
                record: dict[str, float | int | None] = {
                    "latest": _as_float(row.latest),
                    "prior": _as_float(row.prior),
                    "bars": int(row.bars or 0),
                }
                for p in ma_periods:
                    record[f"ma{p}"] = _as_float(getattr(row, f"ma{p}", None))
                out[sym] = record
        return out

    def get_stock_daily_arrays(self, symbol: str) -> dict[str, np.ndarray]:
        frame = self.get_stock_daily(symbol)
        return dataframe_to_arrays(frame)

    def get_stock_weekly_arrays(self, symbol: str) -> dict[str, np.ndarray]:
        frame = self.get_stock_weekly(symbol).rename(columns={"week": "date"})
        return dataframe_to_arrays(frame)

    def get_all_active_symbols(self) -> list[str]:
        return self.get_symbols_for_universe("nifty500")

    def get_symbols_for_universe(self, universe_name: str = "nifty500") -> list[str]:
        active = universe.load_universe_profile(universe_name)
        return active["symbol"].tolist()

    def get_universe_profile(self, universe_name: str = "nifty500") -> pd.DataFrame:
        return universe.load_universe_profile(universe_name)

    def fetch_todays_candles(
        self,
        symbols_df: pd.DataFrame | None = None,
        universe_name: str = "nifty500",
    ) -> int:
        """Fetch Dhan marketfeed OHLC in batches and append today's rows."""
        dhan_client.raise_if_rate_limited()
        if symbols_df is None:
            symbols_df = universe.load_universe_profile(universe_name)
        today = date.today().isoformat()
        total = 0
        sleep_seconds = max(0.0, float(settings.DHAN_MARKETFEED_BATCH_SLEEP_SECONDS))
        for batch_index, chunk in enumerate(_chunks(symbols_df.to_dict("records"), 800)):
            payload_ids = [
                _security_id_payload(row["security_id"])
                for row in chunk
                if str(row.get("security_id", "")).strip()
            ]
            if not payload_ids:
                continue
            if batch_index > 0 and sleep_seconds:
                time.sleep(sleep_seconds)
            response = dhan_client.dhan_request(
                "POST",
                f"{settings.DHAN_BASE_URL}/v2/marketfeed/ohlc",
                json={"NSE_EQ": payload_ids},
                timeout=30,
            )
            if response.status_code == 429:
                dhan_client.record_rate_limit(response.text)
                raise dhan_client.DhanRateLimitError(
                    f"Dhan batch OHLC HTTP 429: {response.text[:200]}"
                )
            if response.status_code != 200:
                raise dhan_client.DhanError(
                    f"Dhan batch OHLC HTTP {response.status_code}: {response.text[:200]}"
                )
            data = response.json().get("data", {}).get("NSE_EQ", {})
            rows = []
            for row in chunk:
                symbol = str(row["symbol"]).upper()
                sid = str(row["security_id"]).strip()
                quote = data.get(sid) or data.get(str(_security_id_payload(sid)))
                if not isinstance(quote, dict):
                    continue
                ohlc = quote.get("ohlc") if isinstance(quote.get("ohlc"), dict) else {}
                close = _float(quote.get("last_price") or quote.get("ltp") or ohlc.get("close"))
                open_ = _float(ohlc.get("open") or close)
                high = _float(ohlc.get("high") or close)
                low = _float(ohlc.get("low") or close)
                if close <= 0:
                    continue
                rows.append(
                    {
                        "date": today,
                        "open": open_,
                        "high": high,
                        "low": low,
                        "close": close,
                        "volume": 0,
                    }
                )
                total += storage.upsert_daily_rows(
                    self.conn,
                    symbol,
                    sid,
                    pd.DataFrame(rows[-1:]),
                )
        return total


def dataframe_to_arrays(frame: pd.DataFrame) -> dict[str, np.ndarray]:
    if frame is None or frame.empty:
        return {
            "date": np.array([], dtype="datetime64[D]"),
            "open": np.array([], dtype=float),
            "high": np.array([], dtype=float),
            "low": np.array([], dtype=float),
            "close": np.array([], dtype=float),
            "volume": np.array([], dtype=float),
        }
    dates = pd.to_datetime(frame["date"], errors="coerce").dt.date
    return {
        "date": np.array(dates, dtype="datetime64[D]"),
        "open": frame["open"].astype(float).to_numpy(),
        "high": frame["high"].astype(float).to_numpy(),
        "low": frame["low"].astype(float).to_numpy(),
        "close": frame["close"].astype(float).to_numpy(),
        "volume": frame["volume"].astype(float).to_numpy(),
    }


def _chunks(items: list[dict], size: int) -> Iterable[list[dict]]:
    for idx in range(0, len(items), size):
        yield items[idx : idx + size]


def _security_id_payload(security_id: str):
    value = str(security_id).strip()
    try:
        return int(value)
    except ValueError:
        return value


def _float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _date_text(value: str | date) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _as_float(value) -> float | None:
    """Coerce a SQLite cell to ``float``; return None for NULL / NaN / non-numeric."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None
