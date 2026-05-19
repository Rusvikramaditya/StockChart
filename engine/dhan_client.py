"""Dhan API helpers used by the Phase 1 setup scripts."""

from __future__ import annotations

import json
import os
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from config import settings

try:
    import pyotp
except ImportError:  # pragma: no cover - optional until TOTP env vars are set
    pyotp = None


_token_lock = threading.Lock()
_current_access_token = settings.DHAN_ACCESS_TOKEN
_last_refresh_time: datetime | None = None
_refresh_blocked_until: datetime | None = None
_master_df: pd.DataFrame | None = None


class DhanError(RuntimeError):
    """Raised when a required Dhan request cannot be completed."""


def _parse_cached_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _read_session_token_cache() -> tuple[str | None, datetime | None]:
    if not settings.DHAN_SESSION_TOKEN_CACHE_ENABLED:
        return None, None
    try:
        path = settings.DHAN_SESSION_TOKEN_CACHE_PATH
        if not path.exists():
            return None, None
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("client_id") and str(data.get("client_id")) != settings.DHAN_CLIENT_ID:
            return None, None
        token = str(data.get("access_token") or "").strip()
        refreshed_at = _parse_cached_datetime(data.get("refreshed_at"))
        if not token or not refreshed_at:
            return None, None
        max_age = timedelta(hours=settings.DHAN_SESSION_TOKEN_MAX_AGE_HOURS)
        if datetime.now() - refreshed_at > max_age:
            return None, None
        return token, refreshed_at
    except Exception as exc:
        print(f"[Dhan] Could not read token cache: {exc}")
        return None, None


def _write_session_token_cache(token: str) -> None:
    if not settings.DHAN_SESSION_TOKEN_CACHE_ENABLED or not token:
        return
    try:
        settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "client_id": settings.DHAN_CLIENT_ID,
            "access_token": token,
            "refreshed_at": datetime.now().isoformat(timespec="seconds"),
        }
        tmp_path = Path(f"{settings.DHAN_SESSION_TOKEN_CACHE_PATH}.tmp")
        tmp_path.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp_path, settings.DHAN_SESSION_TOKEN_CACHE_PATH)
    except Exception as exc:
        print(f"[Dhan] Could not write token cache: {exc}")


def _apply_cached_session_token() -> str | None:
    global _current_access_token, _last_refresh_time
    token, refreshed_at = _read_session_token_cache()
    if not token:
        return None
    with _token_lock:
        _current_access_token = token
        _last_refresh_time = refreshed_at
    return token


def dhan_headers() -> dict[str, str]:
    cached = _apply_cached_session_token()
    with _token_lock:
        token = _current_access_token
    token = cached or token or ""
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "access-token": token,
        "client-id": settings.DHAN_CLIENT_ID,
    }


def auto_refresh_dhan_token(allow_cached: bool = True) -> str | None:
    """Refresh the Dhan token using TOTP when DHAN_PIN/TOTP_SECRET are present."""
    global _current_access_token, _last_refresh_time, _refresh_blocked_until

    if allow_cached:
        cached = _apply_cached_session_token()
        if cached:
            return cached

    if not settings.DHAN_CLIENT_ID or not settings.DHAN_PIN or not settings.DHAN_TOTP_SECRET:
        return None
    if pyotp is None:
        raise DhanError("pyotp is required for Dhan TOTP token refresh")

    now = datetime.now()
    with _token_lock:
        blocked_until = _refresh_blocked_until
    if blocked_until and now < blocked_until:
        return None

    otp = pyotp.TOTP(settings.DHAN_TOTP_SECRET).now()
    url = (
        "https://auth.dhan.co/app/generateAccessToken"
        f"?dhanClientId={settings.DHAN_CLIENT_ID}&pin={settings.DHAN_PIN}&totp={otp}"
    )
    response = requests.post(url, timeout=30)
    if response.status_code != 200:
        return None

    data = response.json()
    token = str(data.get("accessToken") or "").strip()
    if token:
        with _token_lock:
            _current_access_token = token
            _last_refresh_time = datetime.now()
            _refresh_blocked_until = None
        _write_session_token_cache(token)
        return token

    message = str(data.get("message") or data).lower()
    if "once every 2 minutes" in message:
        cooldown = settings.DHAN_REFRESH_COOLDOWN_SECONDS
    elif "invalid totp" in message:
        cooldown = settings.DHAN_INVALID_TOTP_COOLDOWN_SECONDS
    else:
        cooldown = 0
    if cooldown:
        with _token_lock:
            _refresh_blocked_until = datetime.now() + timedelta(seconds=cooldown)
    return None


def dhan_request(method: str, url: str, **kwargs: Any) -> requests.Response:
    headers = dict(kwargs.pop("headers", {}) or {})
    headers.update(dhan_headers())
    response = requests.request(method, url, headers=headers, **kwargs)
    if response.status_code != 401:
        return response

    refreshed = auto_refresh_dhan_token(allow_cached=False)
    if not refreshed:
        return response

    headers.update(dhan_headers())
    return requests.request(method, url, headers=headers, **kwargs)


def download_instrument_master(force_refresh: bool = False) -> Path:
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = settings.INSTRUMENT_MASTER_PATH
    if not force_refresh and path.exists():
        age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
        if age < timedelta(days=7):
            return path

    response = requests.get(settings.DHAN_MASTER_URL, timeout=60)
    if response.status_code != 200 or not response.text.strip():
        if path.exists():
            return path
        raise DhanError(f"Instrument master download failed: HTTP {response.status_code}")
    path.write_text(response.text, encoding="utf-8", newline="")
    return path


def _normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    frame = df.copy()
    frame.columns = [str(col).strip().upper() for col in frame.columns]
    return frame


def load_master_df(force_refresh: bool = False) -> pd.DataFrame:
    global _master_df
    if _master_df is not None and not force_refresh:
        return _master_df.copy()
    path = download_instrument_master(force_refresh=force_refresh)
    _master_df = _normalise_columns(pd.read_csv(path, low_memory=False))
    return _master_df.copy()


def _first_existing_column(df: pd.DataFrame, names: list[str]) -> str | None:
    for name in names:
        if name in df.columns:
            return name
    return None


def _normalise_key(value: str) -> str:
    return (
        str(value)
        .upper()
        .replace("&", "AND")
        .replace("-", "")
        .replace("_", "")
        .replace(" ", "")
        .strip()
    )


def equity_master(force_refresh: bool = False) -> dict[str, dict[str, str]]:
    df = load_master_df(force_refresh=force_refresh)
    symbol_col = _first_existing_column(
        df,
        ["SEM_TRADING_SYMBOL", "TRADING_SYMBOL", "SYMBOL", "SM_SYMBOL_NAME"],
    )
    security_col = _first_existing_column(
        df,
        ["SEM_SMST_SECURITY_ID", "SECURITY_ID", "SECURITYID", "SECURITY ID"],
    )
    exchange_col = _first_existing_column(df, ["SEM_EXM_EXCH_ID", "EXCHANGE", "EXCHANGE_SEGMENT"])
    segment_col = _first_existing_column(df, ["SEM_SEGMENT", "SEGMENT"])
    instrument_col = _first_existing_column(
        df,
        ["SEM_INSTRUMENT_NAME", "INSTRUMENT", "INSTRUMENT_TYPE"],
    )
    custom_symbol_col = _first_existing_column(df, ["SEM_CUSTOM_SYMBOL", "CUSTOM_SYMBOL"])
    if not symbol_col or not security_col:
        raise DhanError("Instrument master missing symbol/security columns")

    frame = df.copy()
    if exchange_col:
        frame = frame[frame[exchange_col].astype(str).str.upper().str.contains("NSE", na=False)]
    if segment_col:
        segment_values = frame[segment_col].astype(str).str.upper()
        frame = frame[segment_values.str.contains("NSE_EQ|EQUITY|^E$", regex=True, na=False)]
    if instrument_col:
        instrument_values = frame[instrument_col].astype(str).str.upper()
        frame = frame[instrument_values.str.contains("EQUITY|EQ", regex=True, na=False)]

    master: dict[str, dict[str, str]] = {}
    for _, row in frame.iterrows():
        symbol = str(row.get(symbol_col, "")).strip().upper()
        security_id = str(row.get(security_col, "")).strip()
        if not symbol or symbol == "NAN" or not security_id or security_id.lower() == "nan":
            continue
        info = {
            "symbol": symbol,
            "security_id": security_id,
            "exchange_segment": "NSE_EQ",
            "instrument": "EQUITY",
        }
        master.setdefault(symbol, info)
        master.setdefault(_normalise_key(symbol), info)
        if custom_symbol_col:
            custom = str(row.get(custom_symbol_col, "")).strip().upper()
            if custom and custom != "NAN":
                master.setdefault(custom, info)
                master.setdefault(_normalise_key(custom), info)
    return master


def index_master(force_refresh: bool = False) -> dict[str, dict[str, str]]:
    df = load_master_df(force_refresh=force_refresh)
    symbol_col = _first_existing_column(
        df,
        ["SEM_TRADING_SYMBOL", "TRADING_SYMBOL", "SYMBOL", "SM_SYMBOL_NAME"],
    )
    security_col = _first_existing_column(
        df,
        ["SEM_SMST_SECURITY_ID", "SECURITY_ID", "SECURITYID", "SECURITY ID"],
    )
    exchange_col = _first_existing_column(df, ["SEM_EXM_EXCH_ID", "EXCHANGE", "EXCHANGE_SEGMENT"])
    segment_col = _first_existing_column(df, ["SEM_SEGMENT", "SEGMENT"])
    custom_symbol_col = _first_existing_column(df, ["SEM_CUSTOM_SYMBOL", "CUSTOM_SYMBOL"])
    if not symbol_col or not security_col:
        raise DhanError("Instrument master missing index symbol/security columns")

    frame = df.copy()
    if exchange_col:
        frame = frame[frame[exchange_col].astype(str).str.upper().str.contains("NSE", na=False)]
    if segment_col:
        segment_values = frame[segment_col].astype(str).str.upper()
        frame = frame[segment_values.str.contains("IDX_I|INDEX|IDX", regex=True, na=False)]

    master: dict[str, dict[str, str]] = {}
    for _, row in frame.iterrows():
        names = [str(row.get(symbol_col, "")).strip().upper()]
        if custom_symbol_col:
            names.append(str(row.get(custom_symbol_col, "")).strip().upper())
        security_id = str(row.get(security_col, "")).strip()
        if not security_id or security_id.lower() == "nan":
            continue
        info = {
            "security_id": security_id,
            "exchange_segment": "IDX_I",
            "instrument": "INDEX",
        }
        for name in names:
            if not name or name == "NAN":
                continue
            master.setdefault(name, info)
            master.setdefault(_normalise_key(name), info)
    return master


def resolve_index_security_id(index_name: str, master: dict[str, dict[str, str]] | None = None) -> str | None:
    master = master or index_master()
    candidates = [index_name, *settings.INDEX_NAME_ALIASES.get(index_name, [])]
    for candidate in candidates:
        info = master.get(candidate.upper()) or master.get(_normalise_key(candidate))
        if info and info.get("security_id"):
            return str(info["security_id"])
    for candidate in candidates:
        security_id = settings.INDEX_SECURITY_IDS.get(candidate.upper())
        if security_id:
            return str(security_id)
    return None


def _parse_dhan_daily_dates(values: Any) -> pd.Series:
    series = pd.Series(values)
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.notna().sum() == len(series) and len(series) > 0:
        max_abs = numeric.abs().max()
        unit = "ms" if pd.notna(max_abs) and max_abs > 10**12 else "s"
        parsed = pd.to_datetime(numeric, unit=unit, utc=True, errors="coerce")
        return parsed.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    return pd.to_datetime(series, errors="coerce")


def parse_historical_response(data: Any) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    candle_data = data.get("data", data) if isinstance(data, dict) else data

    if isinstance(candle_data, list):
        for item in candle_data:
            if not isinstance(item, dict):
                continue
            records.append(
                {
                    "date": item.get("date") or item.get("timestamp") or item.get("datetime") or "",
                    "open": item.get("open", 0),
                    "high": item.get("high", 0),
                    "low": item.get("low", 0),
                    "close": item.get("close", 0),
                    "volume": item.get("volume", 0),
                }
            )
    elif isinstance(candle_data, dict):
        opens = candle_data.get("open") or candle_data.get("Open") or []
        highs = candle_data.get("high") or candle_data.get("High") or []
        lows = candle_data.get("low") or candle_data.get("Low") or []
        closes = candle_data.get("close") or candle_data.get("Close") or []
        volumes = candle_data.get("volume") or candle_data.get("Volume") or []
        dates = (
            candle_data.get("date")
            or candle_data.get("datetime")
            or candle_data.get("timestamp")
            or candle_data.get("start_Time")
            or []
        )
        row_count = min(len(opens), len(highs), len(lows), len(closes), len(volumes))
        for idx in range(row_count):
            records.append(
                {
                    "date": dates[idx] if idx < len(dates) else "",
                    "open": opens[idx],
                    "high": highs[idx],
                    "low": lows[idx],
                    "close": closes[idx],
                    "volume": volumes[idx],
                }
            )

    if not records:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

    frame = pd.DataFrame(records)
    frame["date"] = _parse_dhan_daily_dates(frame["date"])
    frame = frame.dropna(subset=["date"]).sort_values("date")
    for col in ["open", "high", "low", "close"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce").fillna(0).astype(int)
    frame = frame.dropna(subset=["open", "high", "low", "close"])
    return frame[["date", "open", "high", "low", "close", "volume"]]


def historical_payload(
    security_id: str,
    exchange_segment: str,
    instrument: str,
    from_date: str,
    to_date: str,
) -> dict[str, Any]:
    return {
        "securityId": str(security_id),
        "exchangeSegment": exchange_segment,
        "instrument": instrument,
        "expiryCode": 0,
        "fromDate": from_date,
        "toDate": to_date,
        "HistoricalDataType": "D",
    }


def fetch_historical_sync(
    security_id: str,
    exchange_segment: str,
    instrument: str,
    from_date: str,
    to_date: str,
    timeout: int = 30,
) -> pd.DataFrame:
    url = f"{settings.DHAN_BASE_URL}/v2/charts/historical"
    response = dhan_request(
        "POST",
        url,
        json=historical_payload(security_id, exchange_segment, instrument, from_date, to_date),
        timeout=timeout,
    )
    if response.status_code != 200:
        raise DhanError(f"Dhan historical HTTP {response.status_code}: {response.text[:200]}")
    return parse_historical_response(response.json())


def date_years_ago(years: int) -> str:
    return (date.today() - timedelta(days=365 * years)).isoformat()
