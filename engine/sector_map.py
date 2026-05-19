"""Industry-to-sector-index mapping for Nifty 500 symbols."""

from __future__ import annotations

import json
from datetime import datetime

import pandas as pd

from config import settings


KEYWORD_TO_INDEX = [
    (["bank"], "NIFTY BANK"),
    (["financial", "finance", "housing finance", "insurance", "capital market"], "NIFTY FIN SERVICE"),
    (["software", "information technology", "it services", "computer"], "NIFTY IT"),
    (["pharma", "healthcare", "hospital", "diagnostic", "biotechnology"], "NIFTY PHARMA"),
    (["automobile", "auto ", "auto ancillary", "tyre"], "NIFTY AUTO"),
    (["fmcg", "food", "beverages", "personal care", "household"], "NIFTY FMCG"),
    (["consumer", "retail", "textiles", "apparel", "durables"], "NIFTY CONSUMPTION"),
    (["metal", "mining", "steel", "aluminium", "copper"], "NIFTY METAL"),
    (["oil", "gas", "power", "energy", "petroleum", "refineries"], "NIFTY ENERGY"),
    (["construction", "cement", "infrastructure", "capital goods", "industrial"], "NIFTY INFRA"),
    (["realty", "real estate"], "NIFTY REALTY"),
    (["media", "entertainment", "broadcast"], "NIFTY MEDIA"),
]


def map_industry_to_index(industry: str) -> str:
    text = f" {str(industry).lower()} "
    for keywords, index_name in KEYWORD_TO_INDEX:
        if any(keyword in text for keyword in keywords):
            return index_name
    return "NIFTY 50"


def build_sector_map(symbols_df: pd.DataFrame) -> dict:
    rows: dict[str, dict[str, str]] = {}
    for _, row in symbols_df.iterrows():
        status = str(row.get("status", "ACTIVE")).upper()
        if status != "ACTIVE":
            continue
        symbol = str(row.get("symbol") or row.get("Symbol") or "").strip().upper()
        if not symbol:
            continue
        industry = str(row.get("industry") or row.get("Industry") or "").strip()
        rows[symbol] = {
            "industry": industry,
            "sector_index": map_industry_to_index(industry),
        }
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "default_index": "NIFTY 50",
        "symbols": rows,
    }


def write_sector_map(symbols_df: pd.DataFrame) -> dict:
    payload = build_sector_map(symbols_df)
    settings.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    settings.SECTOR_MAP_JSON.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return payload

