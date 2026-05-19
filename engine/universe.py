"""Broad NSE equity universe builder.

The Dhan instrument master contains equities, ETFs, mutual funds, debt,
government securities, and exchange-specific variants in the same CSV. This
module keeps the broad stock universe deliberately strict on instrument class:
NSE equity segment, equity-share instrument type, and a valid security id.
Series is retained for downstream liquidity/risk filters instead of being used
as a default hard exclusion.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd

from config import settings
from engine import dhan_client


OUTPUT_COLUMNS = [
    "symbol",
    "company_name",
    "security_id",
    "exchange_segment",
    "instrument",
    "instrument_type",
    "series",
    "lot_size",
    "listing_date",
    "status",
]

PREFERRED_EQUITY_SERIES = ("EQ", "BE", "SM", "ST", "BZ", "E1", "IT")
DEFAULT_CHUNK_SIZE = 50_000
PREFERRED_SERIES_RANK = {series: idx for idx, series in enumerate(PREFERRED_EQUITY_SERIES)}


@dataclass(frozen=True)
class MasterColumns:
    exchange: str
    segment: str
    security_id: str
    instrument: str
    symbol: str
    lot_size: str | None
    company_name: str | None
    symbol_name: str | None
    instrument_type: str
    series: str | None
    listing_date: str | None

    def selected(self) -> list[str]:
        values = [
            self.exchange,
            self.segment,
            self.security_id,
            self.instrument,
            self.symbol,
            self.lot_size,
            self.company_name,
            self.symbol_name,
            self.instrument_type,
            self.series,
            self.listing_date,
        ]
        return sorted({value for value in values if value})


@dataclass(frozen=True)
class UniverseBuildResult:
    output_path: Path
    rows: int
    duplicates_removed: int
    series_counts: dict[str, int]


class UniverseBuildError(RuntimeError):
    """Raised when the broad universe cannot be built safely."""


def build_all_nse_equity_universe(
    *,
    master_path: Path | None = None,
    output_path: Path | None = None,
    force_master_refresh: bool = False,
    allowed_series: Iterable[str] | None = None,
    chunksize: int = DEFAULT_CHUNK_SIZE,
) -> UniverseBuildResult:
    """Build ``config/all_nse_equity.csv`` from the Dhan instrument master."""

    if master_path is None:
        master_path = dhan_client.download_instrument_master(force_refresh=force_master_refresh)
    if output_path is None:
        output_path = settings.ALL_NSE_EQUITY_CSV

    master_path = Path(master_path)
    output_path = Path(output_path)
    columns = resolve_master_columns(master_path)
    series_filter = (
        {str(item).strip().upper() for item in allowed_series if str(item).strip()}
        if allowed_series is not None
        else None
    )
    if allowed_series is not None and not series_filter:
        raise UniverseBuildError("allowed_series cannot be empty when provided")

    frames: list[pd.DataFrame] = []
    for chunk in pd.read_csv(
        master_path,
        usecols=columns.selected(),
        dtype=str,
        chunksize=chunksize,
        low_memory=False,
    ):
        filtered = _filter_equity_chunk(chunk, columns, series_filter)
        if not filtered.empty:
            frames.append(filtered)

    if frames:
        universe = pd.concat(frames, ignore_index=True)
    else:
        universe = pd.DataFrame(columns=OUTPUT_COLUMNS)

    before_dedupe = len(universe)
    universe = _dedupe_universe(universe)
    duplicates_removed = before_dedupe - len(universe)
    if universe.empty:
        raise UniverseBuildError("No eligible NSE equity rows found in Dhan instrument master")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    universe.to_csv(output_path, index=False)
    return UniverseBuildResult(
        output_path=output_path,
        rows=len(universe),
        duplicates_removed=duplicates_removed,
        series_counts=universe["series"].value_counts().sort_index().to_dict(),
    )


def resolve_master_columns(master_path: Path) -> MasterColumns:
    header = pd.read_csv(master_path, nrows=0)
    column_map = {str(column).strip().upper(): column for column in header.columns}

    return MasterColumns(
        exchange=_required(column_map, "exchange", ["SEM_EXM_EXCH_ID", "EXCHANGE"]),
        segment=_required(column_map, "segment", ["SEM_SEGMENT", "SEGMENT"]),
        security_id=_required(
            column_map,
            "security_id",
            ["SEM_SMST_SECURITY_ID", "SECURITY_ID", "SECURITYID", "SECURITY ID"],
        ),
        instrument=_required(
            column_map,
            "instrument",
            ["SEM_INSTRUMENT_NAME", "INSTRUMENT", "INSTRUMENT_TYPE"],
        ),
        symbol=_required(
            column_map,
            "symbol",
            ["SEM_TRADING_SYMBOL", "TRADING_SYMBOL", "SYMBOL", "SM_SYMBOL_NAME"],
        ),
        lot_size=_optional(column_map, ["SEM_LOT_UNITS", "LOT_SIZE", "LOT_UNITS"]),
        company_name=_optional(column_map, ["SEM_CUSTOM_SYMBOL", "CUSTOM_SYMBOL", "COMPANY_NAME"]),
        symbol_name=_optional(column_map, ["SM_SYMBOL_NAME", "SYMBOL_NAME", "NAME"]),
        instrument_type=_required(
            column_map,
            "instrument_type",
            ["SEM_EXCH_INSTRUMENT_TYPE", "EXCH_INSTRUMENT_TYPE", "EXCHANGE_INSTRUMENT_TYPE"],
        ),
        series=_optional(column_map, ["SEM_SERIES", "SERIES"]),
        listing_date=_optional(column_map, ["SEM_LISTING_DATE", "LISTING_DATE", "LISTING DATE"]),
    )


def _filter_equity_chunk(
    chunk: pd.DataFrame,
    columns: MasterColumns,
    allowed_series: set[str] | None,
) -> pd.DataFrame:
    symbol = _valid_text(chunk[columns.symbol]).str.upper()
    security_id = _valid_text(chunk[columns.security_id])
    exchange = _valid_text(chunk[columns.exchange]).str.upper()
    segment = _valid_text(chunk[columns.segment]).str.upper()
    instrument = _valid_text(chunk[columns.instrument]).str.upper()
    series = (
        _valid_text(chunk[columns.series]).str.upper()
        if columns.series
        else pd.Series("", index=chunk.index, dtype="object")
    )

    mask = (
        exchange.eq("NSE")
        & segment.isin({"E", "NSE_EQ"})
        & instrument.eq("EQUITY")
        & symbol.ne("")
        & security_id.ne("")
        & security_id.ne("0")
    )
    if allowed_series is not None:
        mask &= series.isin(allowed_series)
    instrument_type = _valid_text(chunk[columns.instrument_type]).str.upper()
    mask &= instrument_type.eq("ES")

    if not mask.any():
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    company_name = _first_non_blank(
        chunk,
        [columns.company_name, columns.symbol_name],
        fallback=symbol,
    )
    lot_size = (
        _valid_text(chunk[columns.lot_size]).map(_normalise_lot_size)
        if columns.lot_size
        else pd.Series("1", index=chunk.index, dtype="object")
    )
    listing_date = (
        _normalise_listing_date(_valid_text(chunk[columns.listing_date]))
        if columns.listing_date
        else pd.Series("", index=chunk.index, dtype="object")
    )

    out = pd.DataFrame(
        {
            "symbol": symbol,
            "company_name": company_name,
            "security_id": security_id,
            "exchange_segment": "NSE_EQ",
            "instrument": "EQUITY",
            "instrument_type": instrument_type,
            "series": series,
            "lot_size": lot_size,
            "listing_date": listing_date,
            "status": "ACTIVE",
        }
    )
    return out.loc[mask, OUTPUT_COLUMNS]


def _dedupe_universe(universe: pd.DataFrame) -> pd.DataFrame:
    frame = universe.copy()
    frame["_series_rank"] = frame["series"].map(PREFERRED_SERIES_RANK).fillna(999).astype(int)
    frame["_security_sort"] = pd.to_numeric(frame["security_id"], errors="coerce").fillna(10**18)
    frame = frame.sort_values(["symbol", "_series_rank", "_security_sort", "security_id"])
    frame = frame.drop_duplicates(subset=["symbol"], keep="first")
    frame = frame.sort_values("symbol").reset_index(drop=True)
    return frame[OUTPUT_COLUMNS]


def _first_non_blank(
    chunk: pd.DataFrame,
    column_names: list[str | None],
    *,
    fallback: pd.Series,
) -> pd.Series:
    result = pd.Series("", index=chunk.index, dtype="object")
    for column_name in column_names:
        if not column_name:
            continue
        candidate = _valid_text(chunk[column_name])
        result = result.mask(result.eq(""), candidate)
    return result.mask(result.eq(""), fallback)


def _valid_text(series: pd.Series) -> pd.Series:
    text = series.fillna("").astype(str).str.strip()
    invalid = text.str.lower().isin({"nan", "none", "nat", "null"})
    return text.mask(invalid, "")


def _normalise_lot_size(value: str) -> str:
    value = str(value).strip()
    if not value:
        return "1"
    try:
        number = float(value)
    except ValueError:
        return value
    if number.is_integer():
        return str(int(number))
    return str(number)


def _normalise_listing_date(series: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(series, errors="coerce")
    formatted = parsed.dt.strftime("%Y-%m-%d")
    return formatted.fillna("")


def _required(column_map: dict[str, str], label: str, aliases: list[str]) -> str:
    found = _optional(column_map, aliases)
    if found:
        return found
    raise UniverseBuildError(f"Dhan instrument master missing required {label} column")


def _optional(column_map: dict[str, str], aliases: list[str]) -> str | None:
    for alias in aliases:
        found = column_map.get(alias.upper())
        if found is not None:
            return found
    return None
