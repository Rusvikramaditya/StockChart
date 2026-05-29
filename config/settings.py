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
DHAN_RATE_LIMIT_CACHE_PATH = DATA_DIR / "dhan_rate_limit.json"

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
DHAN_RATE_LIMIT_COOLDOWN_SECONDS = int(os.getenv("DHAN_RATE_LIMIT_COOLDOWN_SECONDS", "900"))
DHAN_MARKETFEED_BATCH_SLEEP_SECONDS = float(os.getenv("DHAN_MARKETFEED_BATCH_SLEEP_SECONDS", "1.0"))

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
    # Textbook cup & handle requires a real handle: small controlled pullback
    # from right rim, NOT a deep retrace into the cup. 33% is the classic
    # O'Neil ceiling. Anything deeper is a "double bottom" or "failed cup".
    "handle_max_retrace_pct": 33.0,
    # Handle must show meaningful pullback (>=2% below pivot) — flat sideways
    # drift after rim isn't a handle, and stop becomes meaningless.
    "handle_min_pullback_pct": 2.0,
    # Handle high must test pivot (within 5%). If max in handle window is far
    # below pivot, there's no real resistance test — it's just a base.
    "handle_high_near_pivot_pct": 5.0,
    # Cap entry-to-stop distance. Wider stops mean unacceptable risk on a
    # swing trade. EMCURE example: stop 1381 from entry 1585 = 12.9%, too
    # wide. Real-money breakout stops should be <=10% from pivot.
    "max_stop_distance_pct": 10.0,
    "max_breakout_extension_pct": 8.0,
}

ASCENDING_TRIANGLE = {
    "lookback_bars": 60,
    # Textbook ascending triangle requires >=3 touches at near-identical
    # resistance and >=3 ascending higher lows. Real money depends on this
    # being a real pattern, not a generic "consolidation near highs".
    "min_resistance_touches": 3,
    "resistance_tolerance_pct": 1.5,
    # Touch range: (max - min) / resistance among detected touches. Rejects
    # messy "resistance zones" (e.g. APARINDS 12,676-13,024 = 2.7% range)
    # that look like clusters under 1.5% tolerance but aren't truly flat.
    # Textbook ascending triangles have all touches within ~0.5-1% range.
    "max_touch_range_pct": 1.0,
    "within_breakout_pct": 4.0,
    "min_rising_lows": 3,
    # Triangle base lows must sit at least this % below resistance, else
    # they're flat consolidation near highs masquerading as a "low".
    "min_low_gap_below_resistance_pct": 1.5,
    "argrelextrema_order": 4,
    "max_breakout_extension_pct": 8.0,
}

FLAT_BASE = {
    # O'Neil/Darvas-style continuation base: tight, horizontal, near highs.
    "min_base_bars": 25,
    "max_base_bars": 75,
    "min_depth_pct": 3.0,
    "max_depth_pct": 15.0,
    "min_resistance_touches": 3,
    "resistance_tolerance_pct": 1.0,
    "max_touch_range_pct": 0.8,
    "max_from_52w_high_pct": 12.0,
    "max_volume_contraction_ratio": 0.9,
    "within_breakout_pct": 2.0,
    "max_breakout_extension_pct": 3.0,
    "max_stop_distance_pct": 10.0,
}

BULL_FLAG = {
    # Pole: explosive advance. Textbook 1-3 weeks, >=15%. Was 12%/3-15 bars,
    # which let 3-day spikes qualify as "poles" (ACUTAAS 2026-05-21 case).
    "min_pole_pct": 15.0,
    "pole_min_bars": 4,
    "pole_max_bars": 15,
    # Pole cleanliness. A choppy advance is not a pole. Max intra-pole
    # drawdown (peak->trough during pole bars) must stay under this.
    "max_pole_drawdown_pct": 6.0,
    # Flag: controlled 1-3 week pullback. Was 5-20 bars / 2-12%; 20 bars and
    # 12% pullback are too loose (deep correction, not a flag).
    "flag_min_bars": 5,
    "flag_max_bars": 15,
    "min_flag_pullback_pct": 3.0,
    "max_flag_pullback_pct": 8.0,
    # Volume dry-up during flag. Textbook flag vol << pole vol; 0.9 was
    # essentially "no contraction required". 0.7 enforces real drying.
    "max_flag_vol_ratio": 0.7,
    # Flag direction. Bull flag pulls DOWN or sideways; upward sloping flag
    # is a rising wedge (bearish), not a flag.
    "max_flag_upslope_pct": 1.0,
    # Prior uptrend gate. Bull flag is a CONTINUATION pattern; the pole
    # must sit inside an existing uptrend. Require positive gain over
    # the lookback period BEFORE pole_start.
    "prior_uptrend_lookback_bars": 30,
    "min_prior_gain_pct": 5.0,
    # Stop distance cap. Stop = flag_low; if it sits more than this % below
    # pivot, swing-trade risk is unacceptable.
    "max_stop_distance_pct": 10.0,
    # Stale guards.
    "within_breakout_pct": 3.0,
    "max_breakout_extension_pct": 5.0,
}

HIGH_TIGHT_FLAG = {
    # Rare power continuation pattern. Kept intentionally severe so it
    # produces only a handful of true momentum candidates.
    "min_advance_pct": 80.0,
    "advance_min_bars": 10,
    "advance_max_bars": 40,
    "flag_min_bars": 8,
    "flag_max_bars": 25,
    "min_flag_pullback_pct": 8.0,
    "max_flag_pullback_pct": 25.0,
    "max_flag_volume_ratio": 0.75,
    "max_flag_upslope_pct": 1.0,
    "within_breakout_pct": 2.0,
    "max_breakout_extension_pct": 3.0,
    "max_stop_distance_pct": 15.0,
}

VCP = {
    "lookback_bars": 90,
    # Textbook Minervini VCP: 3-6 successively tighter contractions.
    "min_contractions": 3,
    "max_contractions": 6,
    # Final pullback is the pivot test (typically 3-8%); prior leg can be wider.
    "max_final_tightness_pct": 6.0,
    "max_prior_tightness_pct": 10.0,
    # First leg is the deepest correction. Hard reject if base too deep
    # (no longer a contraction pattern, more like a new downtrend).
    "max_first_contraction_pct": 35.0,
    # Each contraction must be <= this fraction of the prior contraction.
    # 0.80 is permissive enough to admit real cases; grading rewards <=0.65.
    "tightening_ratio_max": 0.80,
    # Swing highs must cluster near the pivot (consolidation, not downtrend).
    "max_high_dispersion_pct": 8.0,
    # Minimum bars from first swing high to final swing low (5 trading weeks).
    "min_pattern_bars": 25,
    # argrelextrema order for swing detection.
    "swing_order": 3,
    "volume_declining": True,
    # PIVOT READY zone: within N% below pivot.
    "within_breakout_pct": 4.0,
    # Stale guard: skip if price already extended >N% past pivot.
    "max_breakout_extension_pct": 8.0,
}

INV_HEAD_SHOULDERS = {
    "lookback_bars": 120,
    # Shoulder symmetry: textbook IHS shoulders match within ~5-7%.
    # Was 10 (admitted lopsided "shoulders"). Tightened to 7.
    "shoulder_symmetry_pct": 7.0,
    # Head must sit meaningfully below shoulder average. <3% = flat triple-
    # bottom, not IHS. Hard reject.
    "min_head_depth_vs_shoulder_pct": 3.0,
    # Time symmetry: max(left_span, right_span) / min(...). 1.0 = perfectly
    # symmetric; 2.5x is the loosest still-credible IHS. Beyond that, the
    # right shoulder is forming on a different leg than the left.
    "max_time_asymmetry_ratio": 2.5,
    # Min duration left-shoulder -> right-shoulder. Real reversals take time;
    # 25 bars = ~5 trading weeks. Anything faster is intraday noise on daily.
    "min_pattern_bars": 25,
    # Neckline downslope cap. Sloped neckline is fine, but a strongly
    # downsloping neckline (right peak << left peak) means the rally between
    # shoulders is failing — invalid reversal context.
    "max_neckline_downslope_pct": 5.0,
    # Prior downtrend gate: IHS is a REVERSAL pattern. Need a real decline
    # into the left shoulder, else it's a "W" mid-uptrend.
    "prior_downtrend_lookback_bars": 30,
    "min_prior_decline_pct": 8.0,
    # Stale guards (existing).
    "argrelextrema_order": 5,
    "right_shoulder_max_age_bars": 25,
    "invalidation_tolerance_pct": 1.0,
    "max_breakout_extension_pct": 8.0,
    # Cap entry-to-stop distance. IHS shoulders sit deeper below neckline
    # than cup-handle handles do, so 15% (vs cup-handle's 10%) is realistic.
    "max_stop_distance_pct": 15.0,
    # PIVOT READY zone: how far below the (sloped) neckline at current bar
    # we'll still accept as "approaching breakout".
    "within_breakout_pct": 5.0,
}

DOUBLE_BOTTOM = {
    # Undercut-and-reclaim reversal base. This is the strict replacement
    # candidate for loose IHS-like reversals.
    "lookback_bars": 150,
    "min_low_separation_bars": 15,
    "max_low_separation_bars": 90,
    "min_base_depth_pct": 8.0,
    "max_base_depth_pct": 35.0,
    "min_undercut_pct": 0.3,
    "max_undercut_pct": 8.0,
    "max_second_low_volume_ratio": 0.9,
    "within_breakout_pct": 3.0,
    "max_breakout_extension_pct": 5.0,
    "max_stop_distance_pct": 15.0,
    "swing_order": 4,
}

SUPERTREND = {
    "atr_period": 10,
    "multiplier": 3.0,
    # Max bars since the bullish flip for the signal to count as fresh.
    # Was 3 (which lets the detector report 3-day-old flips as BREAKING
    # OUT after price has already moved 2 ATRs). 1 = today or yesterday.
    "max_flip_age_bars": 1,
    # Window we scan for any flip, regardless of staleness, so we can
    # still detect old flips and grade them lower if needed.
    "flip_lookback_bars": 5,
    # Reward / risk target multiplier on ATR.
    "atr_target_multiplier": 2.5,
    # Stop-distance cap. Stop = supertrend line. If line sits >X% below
    # close, position is untradable on a swing basis.
    "max_stop_distance_pct": 10.0,
}

MULTIYEAR_BREAKOUT = {
    "min_years": 2,
    # Practical long-base scan window. The old detector effectively looked
    # back about 3 years; 5 years catches real multi-year overhead supply.
    "max_years": 5,
    # Min touches at the multi-year resistance level. 2 was the floor;
    # textbook breakout chartists want 3+ tests over the period.
    "min_touches": 3,
    # Resistance tolerance tightened 3.0 -> 1.5. A 3% range over multi-year
    # data is a "zone" not a "line".
    "resistance_tolerance_pct": 1.5,
    # Touch dispersion guard: max(touch_high) - min(touch_high) as % of
    # resistance. Tightens what counts as "the same level".
    "max_touch_dispersion_pct": 1.0,
    # Practical resistance-zone mode. Real Indian charts often respect a zone
    # around the old high, not one exact tick-perfect line.
    "resistance_zone_width_pct": 4.0,
    "max_zone_touch_range_pct": 4.0,
    "recent_touch_exclusion_weeks": 12,
    # Related old-high breakout mode: catches a clean multi-year/52-week high
    # breakout even when the old high was not touched 3 perfect times.
    "long_high_base_weeks": 26,
    "long_high_max_base_depth_pct": 18.0,
    "long_high_near_high_pct": 10.0,
    "long_high_min_near_high_weeks": 8,
    # Touch spread guard: touches must span at least this fraction of the
    # lookback window. Prevents 3 touches clustered in the last quarter
    # masquerading as multi-year resistance.
    "min_touch_spread_fraction": 0.5,
    # Volume surge required ONLY on the breakout candle. Pre-breakout
    # bars (PIVOT READY) no longer need surge; surge becomes a grade
    # component instead of a hard gate. Fixes the bug where pivot-ready
    # multi-year breakouts were never detected.
    "volume_surge_ratio": 1.4,
    # Stale guard.
    "max_breakout_extension_pct": 10.0,
    # PIVOT READY zone.
    "within_breakout_pct": 3.0,
    # Stop-distance cap. Real-money swing stops must be tradable even on
    # weekly setups.
    "max_stop_distance_pct": 12.0,
    "timeframe": "weekly",
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

POCKET_PIVOT = {
    "lookback_bars": 10,
    "min_history_bars": 60,
    "ma_periods": (10, 20, 50),
    "close_range_min_pct": 60.0,
    "max_down_day_lookback": 10,
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
    # Penalties subtract from raw conviction score. Real-money rule: an
    # overheated RSI (>= 80) or hidden bearish divergence must show up in
    # the score, not just as a chip on the card. Previous values were all
    # zero, which let RSI-87 setups still tier HIGHEST (ACUTAAS bull flag
    # case, 2026-05-21).
    "penalty_weak":        {"threshold": 45, "penalty": -10},
    "penalty_overbought":  {"threshold": 80, "penalty": -15},
    "penalty_divergence":  -20,
}

CONVICTION_WEIGHTS = {
    "pattern": 25,
    "stage2": 15,
    "volume": 10,
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
# Pattern grade gate (0-100 scale; for audited detectors this is the 0-10
# grade x10). Below this the scan rejects the setup outright as "low
# pattern quality" - it does not appear on the dashboard and does not go
# to Telegram. We trade real money; we do not show mediocre patterns.
# Grade < 7.0 is not clean enough for the live dashboard/Telegram feed.
MIN_TRADABLE_QUALITY_SCORE = 70.0

# Reward/risk floors. A textbook pattern with bad R:R is still a bad
# trade. The scorer uses scan-date actionable entry, not the old pivot,
# once price is already above the breakout level. This prevents cards
# where the move already happened from looking attractive.
MIN_REWARD_RISK_HARD = 1.0
MIN_REWARD_RISK_SOFT = 1.5
# Dashboard/Telegram floor from the scan-date actionable entry. This is
# intentionally stricter than the historical hard floor because users
# should see future opportunity, not a target box that only made sense at
# yesterday's or last week's pivot.
MIN_ACTIONABLE_REWARD_RISK = 1.5

# Pattern grade ceiling for the HIGHEST tier. Setups in the
# [MIN_TRADABLE_QUALITY_SCORE/10, PATTERN_GRADE_HIGHEST_FLOOR) band can
# still tier HIGH or MEDIUM but never HIGHEST. Only TEXTBOOK-grade
# patterns (>= 7.5/10) earn the loudest signal on the card.
PATTERN_GRADE_HIGHEST_FLOOR = 8.0

TEXTBOOK_HIGHEST_GATES = {
    "min_reward_risk": 2.0,
    "max_stop_distance_pct": 10.0,
}

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
TELEGRAM_ALLOWED_TIERS = {"HIGHEST", "HIGH"}
TELEGRAM_MAX_ALERTS = 5
