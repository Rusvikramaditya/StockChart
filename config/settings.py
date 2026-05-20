"""Central settings for the NSE Pattern Intelligence Engine."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# Paths
DATA_DIR = BASE_DIR / "data"
CONFIG_DIR = BASE_DIR / "config"
OUTPUT_DIR = BASE_DIR / "output"
CHARTS_DIR = OUTPUT_DIR / "charts"

DB_PATH = DATA_DIR / "nse_ohlcv.db"
NIFTY500_CSV = CONFIG_DIR / "nifty500.csv"
NIFTY500_DHAN_CSV = CONFIG_DIR / "nifty500_dhan.csv"
ALL_NSE_EQUITY_CSV = CONFIG_DIR / "all_nse_equity.csv"
WATCHLIST_CSV = CONFIG_DIR / "watchlist.csv"
SMALL_MID_LIQUID_CSV = CONFIG_DIR / "small_mid_liquid.csv"
RECENT_LISTINGS_CSV = CONFIG_DIR / "recent_listings.csv"
SECTOR_MAP_JSON = CONFIG_DIR / "sector_map.json"
INSTRUMENT_MASTER_PATH = DATA_DIR / "dhan_instrument_master.csv"
DHAN_SESSION_TOKEN_CACHE_PATH = DATA_DIR / "dhan_session.json"

# Dhan API
DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID", "").strip()
DHAN_ACCESS_TOKEN = os.getenv("DHAN_ACCESS_TOKEN", "").strip()
DHAN_PIN = os.getenv("DHAN_PIN", "").strip()
DHAN_TOTP_SECRET = os.getenv("DHAN_TOTP_SECRET", "").strip()
DHAN_BASE_URL = "https://api.dhan.co"
DHAN_MASTER_URL = os.getenv(
    "DHAN_MASTER_URL",
    "https://images.dhan.co/api-data/api-scrip-master.csv",
)
DHAN_SESSION_TOKEN_CACHE_ENABLED = (
    os.getenv("DHAN_SESSION_TOKEN_CACHE_ENABLED", "true").strip().lower()
    not in {"0", "false", "no"}
)
DHAN_SESSION_TOKEN_MAX_AGE_HOURS = float(
    os.getenv("DHAN_SESSION_TOKEN_MAX_AGE_HOURS", "23")
)
DHAN_REFRESH_COOLDOWN_SECONDS = int(os.getenv("DHAN_REFRESH_COOLDOWN_SECONDS", "120"))
DHAN_INVALID_TOTP_COOLDOWN_SECONDS = int(
    os.getenv("DHAN_INVALID_TOTP_COOLDOWN_SECONDS", "30")
)

# NSE Nifty 500 universe. The archives endpoint is a fallback for the same file.
NIFTY500_URLS = [
    "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv",
    "https://archives.nseindia.com/content/indices/ind_nifty500list.csv",
]

# Historical fetch windows
HISTORY_YEARS = 5
INDEX_HISTORY_YEARS = 2
MAX_CONCURRENT_FETCHES = 5
FETCH_RETRY_COUNT = 3

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "").strip()

# Dhan index IDs. Setup scripts resolve from the instrument master first and use
# these values only when the master cannot provide an exact match.
INDEX_SECURITY_IDS = {
    "NIFTY 50": "13",
    "NIFTY50": "13",
    "NIFTY": "13",
    "NIFTY BANK": "25",
    "BANKNIFTY": "25",
    "NIFTY FIN SERVICE": "27",
    "FINNIFTY": "27",
    "NIFTY AUTO": "14",
    "NIFTY IT": "29",
    "NIFTY FMCG": "28",
    "NIFTY MEDIA": "30",
    "NIFTY METAL": "31",
    "NIFTY PHARMA": "32",
    "NIFTY REALTY": "34",
    "NIFTY ENERGY": "42",
    "NIFTY INFRA": "43",
    "NIFTY PSU BANK": "22",
    "NIFTY CONSUMPTION": "18",
    "NIFTY MIDCAP 100": "41",
}

INDEX_NAME_ALIASES = {
    "NIFTY 50": ["NIFTY50", "NIFTY"],
    "NIFTY BANK": ["BANKNIFTY"],
    "NIFTY FIN SERVICE": ["FINNIFTY", "NIFTY FINANCIAL SERVICES"],
    "NIFTY IT": ["NIFTYIT", "NIFTY INFORMATION TECHNOLOGY"],
    "NIFTY FMCG": ["NIFTYFMCG"],
    "NIFTY PHARMA": ["NIFTYPHARMA"],
    "NIFTY AUTO": ["NIFTYAUTO"],
    "NIFTY REALTY": ["NIFTYREALTY"],
    "NIFTY METAL": ["NIFTYMETAL"],
    "NIFTY ENERGY": ["NIFTYENERGY"],
    "NIFTY INFRA": ["NIFTYINFRA", "NIFTY INFRASTRUCTURE"],
    "NIFTY MEDIA": ["NIFTYMEDIA"],
    "NIFTY PSU BANK": ["NIFTYPSUBANK"],
    "NIFTY PRIVATE BANK": ["NIFTYPVTBANK", "NIFTY PRIVATE BANK"],
    "NIFTY CONSUMPTION": ["NIFTYCONSUMPTION", "NIFTY INDIA CONSUMPTION"],
    "NIFTY MIDCAP 100": ["NIFTYMIDCAP100"],
}

SECTOR_INDICES = [
    "NIFTY IT",
    "NIFTY PHARMA",
    "NIFTY AUTO",
    "NIFTY FMCG",
    "NIFTY METAL",
    "NIFTY REALTY",
    "NIFTY ENERGY",
    "NIFTY INFRA",
    "NIFTY MEDIA",
    "NIFTY PSU BANK",
    "NIFTY PRIVATE BANK",
    "NIFTY CONSUMPTION",
    "NIFTY BANK",
    "NIFTY FIN SERVICE",
    "NIFTY MIDCAP 100",
]

YFINANCE_INDEX_SYMBOLS = {
    "NIFTY 50": "^NSEI",
    "NIFTY BANK": "^NSEBANK",
    "NIFTY IT": "^CNXIT",
    "NIFTY PHARMA": "^CNXPHARMA",
    "NIFTY AUTO": "^CNXAUTO",
    "NIFTY FMCG": "^CNXFMCG",
    "NIFTY METAL": "^CNXMETAL",
    "NIFTY REALTY": "^CNXREALTY",
    "NIFTY ENERGY": "^CNXENERGY",
    "NIFTY INFRA": "^CNXINFRA",
    "NIFTY MEDIA": "^CNXMEDIA",
    "NIFTY PSU BANK": "^CNXPSUBANK",
    "NIFTY CONSUMPTION": "^CNXCONSUM",
    "NIFTY MIDCAP 100": "NIFTY_MIDCAP_100.NS",
}

# Pattern thresholds
CUP_HANDLE = {
    "min_bars": 50,
    "max_bars": 455,
    "min_depth_pct": 12.0,
    "max_depth_pct": 55.0,
    "rim_tolerance_pct": 8.0,
    "handle_max_retrace_pct": 50.0,
    "max_breakout_extension_pct": 8.0,
}

ASCENDING_TRIANGLE = {
    "lookback_bars": 60,
    "min_resistance_touches": 2,
    "resistance_tolerance_pct": 1.5,
    "within_breakout_pct": 4.0,
    "min_rising_lows": 2,
    "argrelextrema_order": 4,
    "max_breakout_extension_pct": 8.0,
}

BULL_FLAG = {
    "min_pole_pct": 12.0,
    "pole_min_bars": 3,
    "pole_max_bars": 15,
    "min_flag_pullback_pct": 2.0,
    "max_flag_pullback_pct": 12.0,
    "max_flag_vol_ratio": 0.9,
}

VCP = {
    "min_contractions": 2,
    "max_final_tightness_pct": 6.0,
    "max_prior_tightness_pct": 8.0,
    "volume_declining": True,
}

INV_HEAD_SHOULDERS = {
    "lookback_bars": 120,
    "shoulder_symmetry_pct": 10.0,
    "argrelextrema_order": 5,
    "max_breakout_extension_pct": 8.0,  # skip if price already >8% past neckline (stale)
}

SUPERTREND = {
    "atr_period": 10,
    "multiplier": 3.0,
    "flip_lookback_bars": 3,
}

MULTIYEAR_BREAKOUT = {
    "min_years": 2,
    "min_touches": 2,
    "resistance_tolerance_pct": 3.0,
    "volume_surge_ratio": 1.4,
    "timeframe": "weekly",
    "max_breakout_extension_pct": 10.0,
}

# Filter thresholds
STAGE2 = {
    "ma_short": 150,
    "ma_long": 200,
    "slope_lookback": 20,
    "max_from_52w_high_pct": 25.0,
    "min_from_52w_low_pct": 30.0,
}

VOLUME = {
    "breakout_vol_ratio": 1.4,
    "avg_vol_period": 50,
}

SECTOR_RS = {
    "lookback_days": 63,
    "leading_threshold": 1.0,
    "lagging_threshold": 1.0,
}

MARKET_REGIME = {
    "nifty_ma_short": 50,
    "nifty_ma_long": 200,
    "advance_decline_threshold": 1.5,
    "bear_score_threshold": 1,
}

RSI = {
    "period": 14,
    "healthy_low": 55,
    "healthy_high": 78,
    "penalty_weak": {"threshold": 45, "penalty": 0},
    "penalty_overbought": {"threshold": 80, "penalty": 0},
    "penalty_divergence": 0,
}

CONVICTION_WEIGHTS = {
    "pattern": 25,
    "stage2": 15,
    "volume": 0,
    "sector_rs": 20,
    "market_regime": 0,
    "multi_tf": 40,
}

QUALITY_SCORE_POINTS = (
    (80.0, 25),
    (65.0, 20),
    (50.0, 15),
    (0.0, 5),
)
MIN_TRADABLE_QUALITY_SCORE = 80.0

CONVICTION_TIERS = {
    "HIGHEST": 90,
    "HIGH": 70,
    "MEDIUM": 50,
}

STACK_BONUS_PER_PATTERN = 0
STACK_BONUS_CAP = 0

PROCESS_WORKERS = 8
STOCK_TIMEOUT_SECONDS = 30
TELEGRAM_MIN_CONVICTION = 70
