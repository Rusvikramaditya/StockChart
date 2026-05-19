"""Nifty 500 universe download and Dhan security-id merge helpers."""

from __future__ import annotations

from io import StringIO

import pandas as pd
import requests

from config import settings
from engine import dhan_client


REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "text/csv,application/csv,*/*",
    "Referer": "https://www.niftyindices.com/",
}


def _first_existing_column(df: pd.DataFrame, names: list[str]) -> str | None:
    upper_map = {str(col).strip().upper(): col for col in df.columns}
    for name in names:
        found = upper_map.get(name.upper())
        if found is not None:
            return found
    return None


def _download_csv_text(url: str) -> str:
    response = requests.get(url, headers=REQUEST_HEADERS, timeout=45)
    if response.status_code != 200 or not response.text.strip():
        raise RuntimeError(f"HTTP {response.status_code}")
    return response.text


def download_nifty500_csv() -> pd.DataFrame:
    errors: list[str] = []
    text = ""
    for url in settings.NIFTY500_URLS:
        try:
            text = _download_csv_text(url)
            break
        except Exception as exc:
            errors.append(f"{url}: {exc}")
    if not text:
        raise RuntimeError("Could not download Nifty 500 CSV: " + " | ".join(errors))

    df = pd.read_csv(StringIO(text))
    symbol_col = _first_existing_column(df, ["Symbol", "SYMBOL"])
    company_col = _first_existing_column(df, ["Company Name", "Company", "NAME OF COMPANY"])
    industry_col = _first_existing_column(df, ["Industry", "Sector", "ISIN"])
    if not symbol_col:
        raise RuntimeError(f"Nifty 500 CSV missing Symbol column: {list(df.columns)}")

    out = pd.DataFrame()
    out["Symbol"] = df[symbol_col].astype(str).str.strip().str.upper()
    out["Company Name"] = (
        df[company_col].astype(str).str.strip() if company_col else out["Symbol"]
    )
    out["Industry"] = df[industry_col].astype(str).str.strip() if industry_col else ""
    out = out[out["Symbol"].ne("") & out["Symbol"].ne("NAN")]
    out = out.drop_duplicates(subset=["Symbol"]).sort_values("Symbol").reset_index(drop=True)
    return out


def load_or_download_nifty500(force_download: bool = False) -> pd.DataFrame:
    if settings.NIFTY500_CSV.exists() and not force_download:
        return pd.read_csv(settings.NIFTY500_CSV)
    df = download_nifty500_csv()
    settings.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(settings.NIFTY500_CSV, index=False)
    return df


def build_dhan_symbol_master(nifty_df: pd.DataFrame, force_master_refresh: bool = False) -> pd.DataFrame:
    master = dhan_client.equity_master(force_refresh=force_master_refresh)
    rows: list[dict[str, str]] = []
    for _, item in nifty_df.iterrows():
        symbol = str(item.get("Symbol", "")).strip().upper()
        info = master.get(symbol) or master.get(
            symbol.replace("&", "AND").replace("-", "").replace(" ", "")
        )
        rows.append(
            {
                "symbol": symbol,
                "company_name": str(item.get("Company Name", symbol)).strip(),
                "industry": str(item.get("Industry", "")).strip(),
                "security_id": str(info.get("security_id", "") if info else "").strip(),
                "exchange_segment": "NSE_EQ",
                "instrument": "EQUITY",
                "status": "ACTIVE",
            }
        )
    return pd.DataFrame(rows).sort_values("symbol").reset_index(drop=True)


def create_nifty500_dhan_file(
    force_download: bool = False,
    force_master_refresh: bool = False,
) -> tuple[pd.DataFrame, list[str]]:
    nifty_df = load_or_download_nifty500(force_download=force_download)
    merged = build_dhan_symbol_master(nifty_df, force_master_refresh=force_master_refresh)
    settings.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    merged.to_csv(settings.NIFTY500_DHAN_CSV, index=False)
    missing = merged.loc[merged["security_id"].astype(str).str.strip().eq(""), "symbol"].tolist()
    return merged, missing


def load_active_symbols() -> pd.DataFrame:
    df = pd.read_csv(settings.NIFTY500_DHAN_CSV, dtype={"security_id": str})
    if "status" not in df.columns:
        df["status"] = "ACTIVE"
    active = df[df["status"].astype(str).str.upper().eq("ACTIVE")].copy()
    active["symbol"] = active["symbol"].astype(str).str.upper().str.strip()
    active["security_id"] = active["security_id"].fillna("").astype(str).str.strip()
    active.loc[active["security_id"].str.lower().isin({"nan", "none"}), "security_id"] = ""
    return active
