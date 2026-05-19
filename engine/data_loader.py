"""SQLite-backed data access for scanner and detector phases."""

from __future__ import annotations

from datetime import date
from typing import Iterable

import numpy as np
import pandas as pd

from config import settings
from engine import dhan_client, storage, symbols


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

    def get_stock_daily_arrays(self, symbol: str) -> dict[str, np.ndarray]:
        frame = self.get_stock_daily(symbol)
        return dataframe_to_arrays(frame)

    def get_stock_weekly_arrays(self, symbol: str) -> dict[str, np.ndarray]:
        frame = self.get_stock_weekly(symbol).rename(columns={"week": "date"})
        return dataframe_to_arrays(frame)

    def get_all_active_symbols(self) -> list[str]:
        if not settings.NIFTY500_DHAN_CSV.exists():
            return []
        active = symbols.load_active_symbols()
        return active["symbol"].tolist()

    def fetch_todays_candles(self, symbols_df: pd.DataFrame | None = None) -> int:
        """Fetch Dhan marketfeed OHLC in batches and append today's rows."""
        if symbols_df is None:
            symbols_df = symbols.load_active_symbols()
        today = date.today().isoformat()
        total = 0
        for chunk in _chunks(symbols_df.to_dict("records"), 800):
            payload_ids = [
                _security_id_payload(row["security_id"])
                for row in chunk
                if str(row.get("security_id", "")).strip()
            ]
            if not payload_ids:
                continue
            response = dhan_client.dhan_request(
                "POST",
                f"{settings.DHAN_BASE_URL}/v2/marketfeed/ohlc",
                json={"NSE_EQ": payload_ids},
                timeout=30,
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
