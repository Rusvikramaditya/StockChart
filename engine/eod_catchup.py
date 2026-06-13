"""Daily EOD catch-up from official bhavcopy data with yfinance fallback."""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Callable, Iterable
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from engine import storage


REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "text/csv,application/zip,application/octet-stream,*/*",
}

NSE_BHAVCOPY_URLS = (
    "https://archives.nseindia.com/products/content/sec_bhavdata_full_{ddmmyyyy}.csv",
    "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{ddmmyyyy}.csv",
)
BSE_BHAVCOPY_URLS = (
    "https://www.bseindia.com/download/BhavCopy/Equity/EQ{ddmmyy}_CSV.ZIP",
    "https://www.bseindia.com/download/BhavCopy/Equity/EQ{ddmmyy}_csv.zip",
)


@dataclass(frozen=True)
class BhavcopyResult:
    date: str
    source: str
    frame: pd.DataFrame
    status: str = "success"  # success | unavailable | error
    message: str = ""


@dataclass
class CatchupSummary:
    target_date: str
    data_as_of: str = ""
    min_data_as_of: str = ""
    missing_days: list[str] = field(default_factory=list)
    caught_up_days: list[str] = field(default_factory=list)
    rows_written: int = 0
    source_counts: dict[str, int] = field(default_factory=dict)
    symbols_current: int = 0
    symbols_stale: int = 0
    stale_symbols: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stale: bool = False
    current_month_incomplete: bool = False

    def to_dict(self) -> dict:
        return {
            "target_date": self.target_date,
            "data_as_of": self.data_as_of,
            "min_data_as_of": self.min_data_as_of,
            "missing_days": list(self.missing_days),
            "missing_days_count": len(self.missing_days),
            "caught_up_days": list(self.caught_up_days),
            "caught_up_days_count": len(self.caught_up_days),
            "rows_written": self.rows_written,
            "source_counts": dict(self.source_counts),
            "symbols_current": self.symbols_current,
            "symbols_stale": self.symbols_stale,
            "stale_symbols": list(self.stale_symbols),
            "stale_symbols_preview": list(self.stale_symbols[:20]),
            "warnings": list(self.warnings),
            "stale": self.stale,
            "current_month_incomplete": self.current_month_incomplete,
        }


BhavcopyFetcher = Callable[[str], BhavcopyResult]
YfinanceFetcher = Callable[[list[str], str, str], dict[str, pd.DataFrame]]


def latest_completed_eod_date(now: datetime | None = None) -> str:
    """Return the latest EOD date that should reasonably be complete."""
    if now is None:
        now = datetime.now(ZoneInfo("Asia/Kolkata"))
    today = now.date()
    if today.weekday() >= 5:
        return _previous_weekday(today).isoformat()
    if now.time() >= time(18, 30):
        return today.isoformat()
    return _previous_weekday(today - timedelta(days=1)).isoformat()


def catch_up_daily_eod(
    conn,
    profile: pd.DataFrame,
    *,
    to_date: str | None = None,
    now: datetime | None = None,
    bhavcopy_fetcher: BhavcopyFetcher | None = None,
    yfinance_fetcher: YfinanceFetcher | None = None,
    logger: Callable[[str], None] | None = None,
) -> CatchupSummary:
    """Catch up selected symbols through the latest completed EOD session."""
    storage.ensure_schema(conn)
    selected = _normalise_profile(profile)
    resolved_now = now or datetime.now(ZoneInfo("Asia/Kolkata"))
    target_date = to_date or latest_completed_eod_date(resolved_now)
    summary = CatchupSummary(target_date=target_date)
    _log(logger, f"EOD target date: {target_date} ({_target_date_reason(resolved_now, forced=to_date is not None)})")
    if selected.empty:
        summary.warnings.append("No symbols selected for EOD catch-up.")
        summary.stale = True
        _log(logger, "EOD catch-up skipped: no symbols selected.")
        return summary

    symbols = selected["symbol"].tolist()
    latest_by_symbol = latest_dates_for_symbols(conn, symbols)
    latest_for_plan = {symbol: latest_by_symbol.get(symbol, "") for symbol in symbols}
    existing_dates = [value for value in latest_for_plan.values() if value]
    data_as_of = max(existing_dates) if existing_dates else "none"
    min_data_as_of = min(existing_dates) if existing_dates else "none"
    _log(
        logger,
        f"Local DB before catch-up: symbols={len(symbols)}, data_as_of={data_as_of}, min_data_as_of={min_data_as_of}",
    )
    summary.missing_days = missing_trading_days(latest_for_plan, target_date)
    if not summary.missing_days:
        _finish_summary(conn, symbols, summary)
        _log(logger, f"EOD catch-up not needed: local data already current through {summary.data_as_of or 'none'}.")
        return summary

    _log(logger, f"EOD catch-up needed: {len(summary.missing_days)} day(s): {_preview(summary.missing_days)}")
    bhavcopy_fetcher = bhavcopy_fetcher or fetch_bhavcopy_for_day
    yfinance_fetcher = yfinance_fetcher or fetch_yfinance_daily
    security_ids = _security_ids(selected)

    for day in summary.missing_days:
        needed = [symbol for symbol in symbols if _is_before(latest_by_symbol.get(symbol), day)]
        if not needed:
            continue
        day_wrote = 0
        _log(logger, f"{day}: trying bhavcopy for {len(needed)} symbol(s).")
        result = bhavcopy_fetcher(day)
        bhavcopy_written = 0
        if result.status == "success" and not result.frame.empty:
            rows_by_symbol = _rows_for_symbols(result.frame, needed)
            for symbol, frame in rows_by_symbol.items():
                written = storage.upsert_daily_rows(conn, symbol, security_ids.get(symbol, ""), frame)
                if written:
                    latest_by_symbol[symbol] = day
                    day_wrote += written
                    bhavcopy_written += written
                    summary.rows_written += written
                    _add_source_count(summary.source_counts, "bhavcopy", written)
            needed = [symbol for symbol in needed if _is_before(latest_by_symbol.get(symbol), day)]
            _log(
                logger,
                f"{day}: bhavcopy status=success, rows_written={bhavcopy_written}, remaining={len(needed)}.",
            )
        elif result.status == "success":
            _log(logger, f"{day}: bhavcopy status=success but returned 0 usable rows.")
        elif result.status == "error":
            summary.warnings.append(f"Bhavcopy fetch failed for {day}: {result.message}")
            _log(logger, f"{day}: bhavcopy status=error: {result.message or 'no detail'}")
        else:
            _log(logger, f"{day}: bhavcopy status={result.status}; using yfinance fallback if needed.")

        if needed:
            _log(logger, f"{day}: trying yfinance fallback for {len(needed)} symbol(s).")
            yframes = yfinance_fetcher(needed, day, day)
            yfinance_written = 0
            for symbol in needed:
                frame = _frame_for_day(yframes.get(symbol), day)
                if frame.empty:
                    continue
                written = storage.upsert_daily_rows(conn, symbol, security_ids.get(symbol, ""), frame)
                if written:
                    latest_by_symbol[symbol] = day
                    day_wrote += written
                    yfinance_written += written
                    summary.rows_written += written
                    _add_source_count(summary.source_counts, "yfinance", written)
            _log(logger, f"{day}: yfinance rows_written={yfinance_written}.")

        unresolved = [symbol for symbol in needed if _is_before(latest_by_symbol.get(symbol), day)]
        if unresolved:
            preview = ", ".join(unresolved[:8])
            suffix = "..." if len(unresolved) > 8 else ""
            summary.warnings.append(
                f"No EOD rows recovered for {day}: {len(unresolved)} symbol(s) still stale ({preview}{suffix})."
            )
            _log(logger, f"{day}: still stale after fallbacks: {len(unresolved)} symbol(s) ({preview}{suffix}).")
        if day_wrote:
            summary.caught_up_days.append(day)

    _finish_summary(conn, symbols, summary)
    _log(
        logger,
        "EOD catch-up finished: "
        f"data_as_of={summary.data_as_of or 'none'}, rows_written={summary.rows_written}, "
        f"sources={_format_source_counts(summary.source_counts)}, stale={summary.symbols_stale}.",
    )
    return summary


def local_data_status(conn, symbols: Iterable[str], *, to_date: str | None = None, now: datetime | None = None) -> CatchupSummary:
    target_date = to_date or latest_completed_eod_date(now)
    cleaned = [str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()]
    summary = CatchupSummary(target_date=target_date)
    _finish_summary(conn, cleaned, summary)
    if summary.stale:
        summary.source_counts = {"local_stale": summary.symbols_stale or len(cleaned)}
    else:
        summary.source_counts = {"local": len(cleaned)}
    return summary


def latest_dates_for_symbols(conn, symbols: Iterable[str]) -> dict[str, str]:
    cleaned = [str(symbol).upper() for symbol in symbols if str(symbol).strip()]
    if not cleaned:
        return {}
    output: dict[str, str] = {}
    for start in range(0, len(cleaned), 800):
        chunk = cleaned[start : start + 800]
        placeholders = ",".join("?" for _ in chunk)
        frame = storage.query_frame(
            conn,
            f"""
            SELECT symbol, MAX(date) AS latest
            FROM ohlcv_daily
            WHERE close > 0 AND symbol IN ({placeholders})
            GROUP BY symbol
            """,
            chunk,
        )
        for row in frame.itertuples(index=False):
            output[str(row.symbol).upper()] = str(row.latest or "")
    return output


def missing_trading_days(latest_by_symbol: dict[str, str], target_date: str) -> list[str]:
    target = _parse_date(target_date)
    latest_dates = [_parse_date(value) for value in latest_by_symbol.values() if value]
    if not latest_dates:
        start_after = _previous_weekday(target)
    else:
        start_after = min(latest_dates)
    days = []
    current = start_after + timedelta(days=1)
    while current <= target:
        if current.weekday() < 5:
            days.append(current.isoformat())
        current += timedelta(days=1)
    if any(not value for value in latest_by_symbol.values()) and target.isoformat() not in days:
        days.append(target.isoformat())
    return sorted(set(days))


def fetch_bhavcopy_for_day(day: str, *, session=requests) -> BhavcopyResult:
    parsed = _parse_date(day)
    errors = []
    for template in NSE_BHAVCOPY_URLS:
        url = template.format(ddmmyyyy=parsed.strftime("%d%m%Y"))
        try:
            response = session.get(url, headers=REQUEST_HEADERS, timeout=30)
        except requests.RequestException as exc:
            errors.append(f"{url}: {exc}")
            continue
        if response.status_code == 404:
            continue
        if response.status_code != 200:
            errors.append(f"{url}: HTTP {response.status_code}")
            continue
        try:
            raw = pd.read_csv(io.StringIO(response.text))
            frame = normalise_nse_bhavcopy(raw, day)
        except Exception as exc:
            errors.append(f"{url}: {exc}")
            continue
        return BhavcopyResult(date=day, source="bhavcopy", frame=frame)

    for template in BSE_BHAVCOPY_URLS:
        url = template.format(ddmmyy=parsed.strftime("%d%m%y"))
        try:
            response = session.get(url, headers=REQUEST_HEADERS, timeout=30)
        except requests.RequestException as exc:
            errors.append(f"{url}: {exc}")
            continue
        if response.status_code == 404:
            continue
        if response.status_code != 200:
            errors.append(f"{url}: HTTP {response.status_code}")
            continue
        try:
            raw = _read_bse_zip(response.content)
            frame = normalise_bse_bhavcopy(raw, day)
        except Exception as exc:
            errors.append(f"{url}: {exc}")
            continue
        return BhavcopyResult(date=day, source="bhavcopy", frame=frame)

    status = "error" if errors else "unavailable"
    return BhavcopyResult(date=day, source="bhavcopy", frame=pd.DataFrame(), status=status, message=" | ".join(errors))


def normalise_nse_bhavcopy(raw: pd.DataFrame, day: str) -> pd.DataFrame:
    columns = _column_map(raw)
    symbol_col = _required_column(columns, "SYMBOL")
    open_col = _required_column(columns, "OPEN_PRICE", "OPEN")
    high_col = _required_column(columns, "HIGH_PRICE", "HIGH")
    low_col = _required_column(columns, "LOW_PRICE", "LOW")
    close_col = _required_column(columns, "CLOSE_PRICE", "CLOSE")
    volume_col = _required_column(columns, "TTL_TRD_QNTY", "TOTTRDQTY", "VOLUME")
    series_col = columns.get("SERIES")
    date_col = columns.get("DATE1") or columns.get("DATE")

    frame = pd.DataFrame(
        {
            "symbol": raw[symbol_col].astype(str).str.strip().str.upper(),
            "date": raw[date_col] if date_col else day,
            "open": raw[open_col],
            "high": raw[high_col],
            "low": raw[low_col],
            "close": raw[close_col],
            "volume": raw[volume_col],
            "series": raw[series_col].astype(str).str.strip().str.upper() if series_col else "EQ",
        }
    )
    frame["_series_rank"] = frame["series"].map({"EQ": 0, "BE": 1, "SM": 2, "ST": 3}).fillna(9)
    frame = frame.sort_values(["symbol", "_series_rank"]).drop_duplicates("symbol", keep="first")
    return _normalise_bhavcopy_ohlcv(frame, fallback_date=day)


def normalise_bse_bhavcopy(raw: pd.DataFrame, day: str) -> pd.DataFrame:
    columns = _column_map(raw)
    symbol_col = columns.get("SC_CODE") or columns.get("SCRIP_CD") or columns.get("SYMBOL")
    if not symbol_col:
        raise ValueError("BSE bhavcopy missing SC_CODE/SYMBOL")
    frame = pd.DataFrame(
        {
            "symbol": raw[symbol_col].astype(str).str.strip().str.upper(),
            "date": day,
            "open": raw[_required_column(columns, "OPEN")],
            "high": raw[_required_column(columns, "HIGH")],
            "low": raw[_required_column(columns, "LOW")],
            "close": raw[_required_column(columns, "CLOSE")],
            "volume": raw[_required_column(columns, "NO_OF_SHRS", "VOLUME")],
        }
    )
    return _normalise_bhavcopy_ohlcv(frame, fallback_date=day)


def fetch_yfinance_daily(symbols: list[str], from_date: str, to_date: str) -> dict[str, pd.DataFrame]:
    import yfinance as yf

    output: dict[str, pd.DataFrame] = {}
    end = (_parse_date(to_date) + timedelta(days=1)).isoformat()
    for symbol in symbols:
        ticker = f"{symbol}.NS"
        try:
            data = yf.download(ticker, start=from_date, end=end, progress=False, auto_adjust=False)
        except Exception:
            output[symbol] = pd.DataFrame()
            continue
        output[symbol] = _normalise_yfinance_frame(data)
    return output


def _normalise_yfinance_frame(data: pd.DataFrame) -> pd.DataFrame:
    if data is None or data.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    frame = data.copy()
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = frame.columns.get_level_values(0)
    frame = frame.reset_index()
    date_col = "Date" if "Date" in frame.columns else frame.columns[0]
    return storage.normalise_ohlcv_frame(
        pd.DataFrame(
            {
                "date": frame[date_col],
                "open": frame["Open"],
                "high": frame["High"],
                "low": frame["Low"],
                "close": frame["Close"],
                "volume": frame["Volume"] if "Volume" in frame else 0,
            }
        )
    )


def _finish_summary(conn, symbols: list[str], summary: CatchupSummary) -> None:
    latest = latest_dates_for_symbols(conn, symbols)
    dates = [value for value in latest.values() if value]
    summary.data_as_of = max(dates) if dates else ""
    summary.min_data_as_of = min(dates) if dates else ""
    summary.stale_symbols = [
        symbol for symbol in symbols if _is_before(latest.get(symbol), summary.target_date)
    ]
    summary.symbols_stale = len(summary.stale_symbols)
    summary.symbols_current = max(0, len(symbols) - summary.symbols_stale)
    summary.stale = bool(summary.stale_symbols) or _is_before(summary.data_as_of, summary.target_date)
    if summary.stale:
        _add_source_count(summary.source_counts, "local_stale", summary.symbols_stale or len(symbols))
        summary.warnings.append(
            f"Data as of {summary.data_as_of or 'none'}; target completed EOD is {summary.target_date}."
        )
    target = _parse_date(summary.target_date)
    as_of = _parse_date(summary.data_as_of) if summary.data_as_of else None
    summary.current_month_incomplete = bool(as_of and as_of < target and as_of.year == target.year and as_of.month == target.month)
    if summary.current_month_incomplete:
        summary.warnings.append("Current-month data is incomplete; monthly charts/reports may be stale.")


def _log(logger: Callable[[str], None] | None, message: str) -> None:
    if logger is not None:
        logger(message)


def _preview(values: list[str], limit: int = 5) -> str:
    if not values:
        return "none"
    suffix = ", ..." if len(values) > limit else ""
    return ", ".join(values[:limit]) + suffix


def _format_source_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "none"
    return ", ".join(f"{source}={count}" for source, count in sorted(counts.items()))


def _target_date_reason(now: datetime, *, forced: bool) -> str:
    if forced:
        return "explicit override"
    current = now.date()
    if current.weekday() >= 5:
        return "weekend; using previous weekday"
    if now.time() < time(18, 30):
        return "before 18:30 IST EOD cutoff; using previous weekday"
    return "after 18:30 IST EOD cutoff; using today"


def _rows_for_symbols(frame: pd.DataFrame, symbols: list[str]) -> dict[str, pd.DataFrame]:
    if frame is None or frame.empty:
        return {}
    wanted = {symbol.upper() for symbol in symbols}
    clean = frame.copy()
    clean["symbol"] = clean["symbol"].astype(str).str.upper()
    clean = clean[clean["symbol"].isin(wanted)]
    return {
        symbol: group[["date", "open", "high", "low", "close", "volume"]]
        for symbol, group in clean.groupby("symbol")
    }


def _frame_for_day(frame: pd.DataFrame | None, day: str) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    clean = storage.normalise_ohlcv_frame(frame)
    return clean[clean["date"].eq(day)].copy()


def _normalise_profile(profile: pd.DataFrame) -> pd.DataFrame:
    if profile is None or profile.empty:
        return pd.DataFrame(columns=["symbol", "security_id"])
    frame = profile.copy()
    frame["symbol"] = frame["symbol"].astype(str).str.strip().str.upper()
    frame = frame[frame["symbol"].ne("")]
    if "security_id" not in frame:
        frame["security_id"] = ""
    frame["security_id"] = frame["security_id"].fillna("").astype(str).str.strip()
    return frame.drop_duplicates("symbol", keep="first").reset_index(drop=True)


def _security_ids(profile: pd.DataFrame) -> dict[str, str]:
    return {
        str(row.symbol).upper(): str(row.security_id).strip()
        for row in profile[["symbol", "security_id"]].itertuples(index=False)
    }


def _normalise_bhavcopy_ohlcv(frame: pd.DataFrame, *, fallback_date: str) -> pd.DataFrame:
    clean = frame.copy()
    if "date" not in clean:
        clean["date"] = fallback_date
    normalized = storage.normalise_ohlcv_frame(clean)
    normalized.insert(0, "symbol", clean.loc[normalized.index, "symbol"].astype(str).str.upper().values)
    return normalized[["symbol", "date", "open", "high", "low", "close", "volume"]]


def _read_bse_zip(content: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if not names:
            raise ValueError("BSE bhavcopy zip has no CSV")
        with archive.open(names[0]) as handle:
            return pd.read_csv(handle)


def _column_map(frame: pd.DataFrame) -> dict[str, str]:
    return {str(column).strip().upper(): column for column in frame.columns}


def _required_column(columns: dict[str, str], *names: str) -> str:
    for name in names:
        if name in columns:
            return columns[name]
    raise ValueError("missing required bhavcopy column: " + "/".join(names))


def _previous_weekday(day: date) -> date:
    current = day
    while current.weekday() >= 5:
        current -= timedelta(days=1)
    return current


def _parse_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return pd.to_datetime(str(value), errors="raise").date()


def _is_before(left: str | None, right: str) -> bool:
    if not left:
        return True
    return str(left) < str(right)


def _add_source_count(counts: dict[str, int], source: str, rows: int) -> None:
    counts[source] = counts.get(source, 0) + int(rows)
