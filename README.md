# Pattern Finder

Local NSE pattern scanner for daily chart-pattern discovery, broad-universe data refresh, HTML dashboard output, and optional Telegram alerts.

## Current Status

- Phases 1-8 in `docs/NSE_PATTERN_ENGINE_PLAN_Final.html` are implemented and pass the quality gate.
- The only open plan task is `5r-18`: user approval of the real-stock thesis chart samples.
- Review chart samples in `docs/CHART_APPROVAL_GALLERY.html`.
- Approval details are tracked in `docs/CHART_APPROVAL_HANDOFF.md`.

## Setup

Use Python 3.11+ from the project root.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m playwright install chromium
Copy-Item .env.example .env
```

Fill `.env` with Dhan credentials before live data fetches. Fill Telegram values only when live alerts are desired.

Required for Dhan fetches:

```text
DHAN_CLIENT_ID
DHAN_ACCESS_TOKEN
DHAN_PIN
DHAN_TOTP_SECRET
```

Required for Telegram sends:

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
```

## Verify The Local Data

```powershell
python setup\06_verify_data.py
```

Expected current proof baseline from the plan: broad NSE symbols are present, most active symbols have daily history, downloaded broad symbols have weekly candles, and the watchlist plus `small_mid_liquid` profile are covered.

## Safe Dry Runs

These commands do not send Telegram alerts and do not fetch today's candles.

```powershell
python scanner.py --universe nifty500 --skip-fetch --dry-run --no-telegram --output output\nifty500_dry_run.html
python scanner.py --universe small_mid_liquid --skip-fetch --dry-run --no-telegram --output output\small_mid_liquid_dry_run.html
python scanner.py --universe watchlist --skip-fetch --dry-run --no-telegram --output output\watchlist_dry_run.html
```

## Daily Scan

Default stable run:

```powershell
python scanner.py --universe nifty500
```

Run without Telegram:

```powershell
python scanner.py --universe nifty500 --no-telegram
```

Require liquidity pass for tradable alerts:

```powershell
python scanner.py --universe small_mid_liquid --min-liquidity
```

## Broad Universe Refresh

Refresh broad universe/profile files:

```powershell
python scanner.py --refresh-universe
```

Plan missing historical downloads without calling Dhan:

```powershell
python setup\10_fetch_missing_historical.py --universe all_nse_equity
```

Execute the missing-history fetch:

```powershell
python setup\10_fetch_missing_historical.py --universe all_nse_equity --execute
```

Generate weekly candles after new daily rows are fetched:

```powershell
python setup\03_generate_weekly.py
```

## Chart Approval

Open this file in a browser:

```text
docs/CHART_APPROVAL_GALLERY.html
```

Approve only if the samples match the intended TradingView-style direction: readable candles, large but non-overlapping stock title, clear pattern drawing, visible entry/target/stop, readable risk boxes, and no mobile label collisions.

## Quality Gate

Run before marking work complete or committing changes:

```powershell
$env:PYTHONUTF8='1'; python scripts\quality_gate.py --max-completed-phase 8
```

The gate compiles source files, runs unit tests, checks Git whitespace, scans for sensitive staged paths, runs a secret scan, and verifies the plan default-completion ceiling.
