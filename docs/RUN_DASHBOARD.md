# Pattern Finder Dashboard Runbook

Run every command from:

```powershell
cd "A:\VibeCoding\ProductIdeas\Pattern Finder"
```

## 1. Verify local data

```powershell
python setup\06_verify_data.py
```

## 2. Generate a quick dashboard

Use this when you want a fast local report from existing data:

```powershell
python scanner.py --universe nifty500 --limit 25 --skip-fetch --dry-run --no-telegram --workers 8 --output output\readiness_nifty500_limit25.html
```

Open it:

```powershell
start output\readiness_nifty500_limit25.html
```

## 3. Run from the local control dashboard

Use this when you want dropdowns, filters, and a button instead of typing scanner arguments:

```powershell
python scripts\scanner_control_server.py
```

Then open:

```text
http://127.0.0.1:8765
```

Recommended first run in the UI:

- Universe: `Nifty 500`
- Run mode: `Safe dry run`
- Limit: `25`
- Workers: `8`
- Click `Run scanner`

For live Dhan fetch:

1. Click `Import StockScanner Dhan`.
2. Click `Verify Dhan auth`.
3. Continue only if the UI says Dhan auth is verified and token cached.
4. Select `Live fetch, no Telegram` for live data without alerts.

For Telegram alerts:

1. Save `TELEGRAM_BOT_TOKEN` in `.env`.
2. Open the bot in Telegram and press `Start` or send any message.
3. Click `Resolve Telegram Chat ID`.
4. Click `Verify Telegram`.
5. Use `Live fetch + Telegram` only after verification passes.

The UI writes a dashboard under `output\control_*.html` and gives you an `Open generated dashboard` link when the run finishes.

Charts are visible in two places:

- Inside each generated dashboard result card under `Open thesis`, when a scan finds scored pattern hits.
- As PNG files under `output\charts`, also listed in the control dashboard's `Recent Chart PNGs` panel.

## 4. Generate the broader local dashboard

Use this when you want the scanner to check the full locally available equity universe:

```powershell
python scanner.py --universe all_nse_equity --skip-fetch --dry-run --no-telegram --workers 8 --output output\readiness_all_nse_equity_full.html
```

Open it:

```powershell
start output\readiness_all_nse_equity_full.html
```

## 5. Generate the real detector chart gallery

This is separate from the scanner dashboard. It renders actual detector hits only, for chart approval:

```powershell
python scripts\gen_detected_chart_gallery.py --max-per-pattern 1
start docs\CHART_APPROVAL_GALLERY.html
```

## Important

If the dashboard says `No setups passed this scan`, it means the dashboard rendered but the scanner found zero tradable pattern cards for that universe and filter set. It is not a fake pattern placeholder.
