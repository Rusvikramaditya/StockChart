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
PROFILE_REQUIRED_COLUMNS = ("symbol", "security_id", "exchange_segment", "instrument", "status")
SYMBOL_ONLY_COLUMNS = ("symbol",)
DEFAULT_WATCHLIST_SYMBOLS = (
    "MRPL",
    "MAZDOCK",
    "GRSE",
    "ADANIPOWER",
    "AEROFLEX",
    "STLTECH",
    "VEDL",
)
PROFILE_PATHS = {
    "all_nse_equity": settings.ALL_NSE_EQUITY_CSV,
    "nifty500": settings.NIFTY500_DHAN_CSV,
    "small_mid_liquid": settings.SMALL_MID_LIQUID_CSV,
    "recent_listings": settings.RECENT_LISTINGS_CSV,
    "watchlist": settings.WATCHLIST_CSV,
}
DATA_DERIVED_PROFILES = frozenset({"small_mid_liquid", "recent_listings"})


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


@dataclass(frozen=True)
class WatchlistBuildResult:
    output_path: Path
    rows: int
    symbols: tuple[str, ...]


class UniverseBuildError(RuntimeError):
    """Raised when the broad universe cannot be built safely."""


class UniverseProfileError(RuntimeError):
    """Raised when a requested universe profile is unavailable or invalid."""


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


def load_universe_profile(
    profile_name: str,
    *,
    profile_path: Path | None = None,
    broad_path: Path | None = None,
    allow_empty: bool = False,
) -> pd.DataFrame:
    """Load and validate a selectable universe profile.

    All returned profiles use ``OUTPUT_COLUMNS`` and active rows only. Profiles
    that only contain symbols are resolved through ``all_nse_equity.csv`` so
    security ids are never guessed.
    """

    name = normalise_profile_name(profile_name)
    if name not in PROFILE_PATHS:
        supported = ", ".join(sorted(PROFILE_PATHS))
        raise UniverseProfileError(f"Unsupported universe profile '{profile_name}'. Supported: {supported}")

    broad = load_all_nse_equity(path=broad_path)
    if name == "all_nse_equity":
        return _active_only(broad, "all_nse_equity", allow_empty=allow_empty)

    path = Path(profile_path) if profile_path is not None else PROFILE_PATHS.get(name)
    if not path.exists():
        if name in DATA_DERIVED_PROFILES:
            raise UniverseProfileError(_derived_profile_message(name, path))
        raise UniverseProfileError(f"Universe profile '{name}' missing: {path}")

    raw = _read_csv(path)
    if raw.empty:
        if name in DATA_DERIVED_PROFILES:
            raise UniverseProfileError(_derived_profile_message(name, path))
        if not allow_empty:
            raise UniverseProfileError(f"Universe profile '{name}' has no rows: {path}")

    if _has_required_columns(raw, PROFILE_REQUIRED_COLUMNS):
        frame = normalise_profile_frame(
            raw,
            source=str(path),
            required_columns=PROFILE_REQUIRED_COLUMNS,
            require_security_id=False,
        )
        return _validate_against_broad(
            frame,
            broad,
            source=str(path),
            allow_empty=allow_empty,
            allow_unresolved_blank_ids=name == "nifty500",
            allow_security_id_repair=name == "nifty500",
        )

    symbol_frame = normalise_profile_frame(raw, source=str(path), required_columns=SYMBOL_ONLY_COLUMNS)
    return resolve_symbols_from_broad(
        symbol_frame["symbol"].tolist(),
        broad_df=broad,
        source=str(path),
        allow_empty=allow_empty,
    )


def load_all_nse_equity(*, path: Path | None = None) -> pd.DataFrame:
    path = Path(path) if path is not None else settings.ALL_NSE_EQUITY_CSV
    if not path.exists():
        raise UniverseProfileError(f"Broad NSE equity universe missing: {path}")
    frame = normalise_profile_frame(
        _read_csv(path),
        source=str(path),
        required_columns=OUTPUT_COLUMNS,
    )
    return _active_only(frame, str(path))


def build_watchlist_profile(
    symbols: Iterable[str] = DEFAULT_WATCHLIST_SYMBOLS,
    *,
    output_path: Path | None = None,
    broad_path: Path | None = None,
) -> WatchlistBuildResult:
    """Resolve watchlist symbols from the broad universe and write a profile CSV."""

    output_path = Path(output_path) if output_path is not None else settings.WATCHLIST_CSV
    resolved = resolve_symbols_from_broad(symbols, broad_path=broad_path, source="watchlist")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    resolved.to_csv(output_path, index=False)
    return WatchlistBuildResult(
        output_path=output_path,
        rows=len(resolved),
        symbols=tuple(resolved["symbol"].tolist()),
    )


def resolve_symbols_from_broad(
    symbols: Iterable[str],
    *,
    broad_df: pd.DataFrame | None = None,
    broad_path: Path | None = None,
    source: str = "symbols",
    allow_empty: bool = False,
) -> pd.DataFrame:
    """Resolve symbols to full profile rows using ``all_nse_equity.csv``."""

    requested = _dedupe_symbols(symbols)
    if not requested and not allow_empty:
        raise UniverseProfileError(f"{source} did not provide any symbols")
    broad = broad_df.copy() if broad_df is not None else load_all_nse_equity(path=broad_path)
    broad = normalise_profile_frame(broad, source="all_nse_equity", required_columns=OUTPUT_COLUMNS)
    indexed = broad.drop_duplicates(subset=["symbol"], keep="first").set_index("symbol", drop=False)
    missing = [symbol for symbol in requested if symbol not in indexed.index]
    if missing:
        raise UniverseProfileError(
            f"{source} contains symbol(s) not present in all_nse_equity: {', '.join(missing)}"
        )
    if not requested:
        return broad.head(0)[OUTPUT_COLUMNS].copy()
    return indexed.loc[requested, OUTPUT_COLUMNS].reset_index(drop=True)


def normalise_profile_frame(
    frame: pd.DataFrame,
    *,
    source: str,
    required_columns: Iterable[str],
    require_security_id: bool = True,
) -> pd.DataFrame:
    """Return a validated profile frame with normalized lowercase columns."""

    if frame is None:
        raise UniverseProfileError(f"{source} could not be read")
    normalized = frame.copy()
    normalized.columns = [str(column).strip().lower() for column in normalized.columns]
    required = tuple(required_columns)
    missing_columns = [column for column in required if column not in normalized.columns]
    if missing_columns:
        raise UniverseProfileError(f"{source} missing required column(s): {', '.join(missing_columns)}")
    for column in required:
        normalized[column] = _valid_text(normalized[column])
    if "symbol" in normalized.columns:
        normalized["symbol"] = normalized["symbol"].str.upper()
        blank_symbols = int(normalized["symbol"].eq("").sum())
        if blank_symbols:
            raise UniverseProfileError(f"{source} has {blank_symbols} blank symbol row(s)")
        duplicate_symbols = normalized.loc[normalized["symbol"].duplicated(), "symbol"].tolist()
        if duplicate_symbols:
            preview = ", ".join(duplicate_symbols[:10])
            raise UniverseProfileError(f"{source} has duplicate symbol row(s): {preview}")
    if require_security_id and "security_id" in required:
        blank_security_ids = int(normalized["security_id"].eq("").sum())
        if blank_security_ids:
            raise UniverseProfileError(f"{source} has {blank_security_ids} blank security_id row(s)")
    if "status" in normalized.columns:
        normalized["status"] = normalized["status"].str.upper().mask(
            normalized["status"].eq(""),
            "ACTIVE",
        )
    return normalized


def normalise_profile_name(profile_name: str) -> str:
    name = str(profile_name).strip().lower().replace("-", "_")
    if not name:
        raise UniverseProfileError("Universe profile name is required")
    return name


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def _has_required_columns(frame: pd.DataFrame, required_columns: Iterable[str]) -> bool:
    available = {str(column).strip().lower() for column in frame.columns}
    return all(column in available for column in required_columns)


def _active_only(frame: pd.DataFrame, source: str, *, allow_empty: bool = False) -> pd.DataFrame:
    if "status" not in frame.columns:
        active = frame.copy()
        active["status"] = "ACTIVE"
    else:
        active = frame[frame["status"].astype(str).str.upper().eq("ACTIVE")].copy()
    if active.empty and not allow_empty:
        raise UniverseProfileError(f"{source} has no ACTIVE symbols")
    return active.reset_index(drop=True)


def _validate_against_broad(
    frame: pd.DataFrame,
    broad: pd.DataFrame,
    *,
    source: str,
    allow_empty: bool = False,
    allow_unresolved_blank_ids: bool = False,
    allow_security_id_repair: bool = False,
) -> pd.DataFrame:
    active = _active_only(frame, source, allow_empty=True)
    if active.empty:
        if allow_empty:
            return broad.head(0)[OUTPUT_COLUMNS].copy()
        raise UniverseProfileError(f"{source} has no ACTIVE symbols")

    broad_index = broad.drop_duplicates(subset=["symbol"], keep="first").set_index("symbol")
    missing = [symbol for symbol in active["symbol"].tolist() if symbol not in broad_index.index]
    if missing:
        missing_rows = active[active["symbol"].isin(missing)]
        unresolved_blank_ids = missing_rows["security_id"].astype(str).str.strip().eq("").all()
        if allow_unresolved_blank_ids and unresolved_blank_ids:
            active = active[~active["symbol"].isin(missing)].copy()
            if active.empty:
                raise UniverseProfileError(f"{source} has no resolvable ACTIVE symbols")
        else:
            raise UniverseProfileError(
                f"{source} contains symbol(s) not present in all_nse_equity: {', '.join(missing)}"
            )

    if active.empty:
        if allow_empty:
            return broad.head(0)[OUTPUT_COLUMNS].copy()
        raise UniverseProfileError(
            f"{source} has no resolvable ACTIVE symbols"
        )

    mismatched = []
    for row in active[["symbol", "security_id"]].itertuples(index=False):
        if not str(row.security_id).strip():
            continue
        expected = str(broad_index.at[row.symbol, "security_id"]).strip()
        if str(row.security_id).strip() != expected:
            mismatched.append(f"{row.symbol} expected {expected} got {row.security_id}")
    if mismatched:
        if not allow_security_id_repair:
            preview = "; ".join(mismatched[:10])
            raise UniverseProfileError(f"{source} has security_id mismatch against all_nse_equity: {preview}")

    resolved = resolve_symbols_from_broad(
        active["symbol"].tolist(),
        broad_df=broad,
        source=source,
        allow_empty=allow_empty,
    )
    if missing:
        resolved.attrs["unresolved_symbols"] = tuple(missing)
    if mismatched:
        resolved.attrs["security_id_mismatches"] = tuple(mismatched)
    return resolved


def _dedupe_symbols(symbols: Iterable[str]) -> list[str]:
    requested = []
    seen = set()
    duplicates = []
    for symbol in symbols:
        clean = str(symbol).strip().upper()
        if not clean:
            continue
        if clean in seen:
            duplicates.append(clean)
            continue
        seen.add(clean)
        requested.append(clean)
    if duplicates:
        raise UniverseProfileError("Duplicate symbol(s): " + ", ".join(sorted(set(duplicates))))
    return requested


def _derived_profile_message(profile_name: str, path: Path) -> str:
    if profile_name == "small_mid_liquid":
        return (
            f"Universe profile '{profile_name}' is not generated yet: {path}. "
            "It requires OHLCV-derived liquidity metrics from Phase 6-0c/6-0d; refusing to fake it."
        )
    if profile_name == "recent_listings":
        return (
            f"Universe profile '{profile_name}' is not generated yet: {path}. "
            "The current broad universe has no reliable listing dates; refusing to fake it."
        )
    return f"Universe profile '{profile_name}' is not generated yet: {path}"


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
