# Chart Approval Handoff

Date: 2026-05-20

Status: Reopened on 2026-05-20 after visual review feedback. The prior samples were too renderer-QA oriented and did not explain the pattern thesis clearly enough. The chart title, pattern overlays, dashboard pattern guide, and approval screenshots have been refreshed; plan item `5r-18` stays open until the new samples are explicitly approved.

## Approval Pack

Generated with real local OHLCV rows:

```powershell
python scripts\gen_sample_thesis_chart.py --sample-pack
```

The browser screenshot exporter validated nonblank canvases for each desktop and mobile PNG.
The approval screenshots are copied into `docs/chart_approval_samples/` so the pushed repo contains the review evidence; the same files also remain in ignored local `output/charts/` after regeneration.
Open `docs/CHART_APPROVAL_GALLERY.html` for a single-page visual review of every desktop/mobile sample.

Latest refresh: chart titles are smaller, desktop/mobile overlays draw visible pattern structure, VCP draws contraction boxes plus a pivot line, and the dashboard now includes a top-level pattern guide plus per-stock chart-reading notes. Compact mobile charts still shorten price-line labels to `Target`, `Entry`, and `Stop`, hide the default last-price marker, and clamp the risk/reward box before the right price scale to reduce label collisions.

| Overlay family | Symbol | Desktop QA file | Mobile QA file |
|---|---|---|---|
| Ascending triangle | ADANIENT | `docs/chart_approval_samples/ADANIENT_thesis_chart_20260520_desktop_qa.png` | `docs/chart_approval_samples/ADANIENT_thesis_chart_20260520_mobile_qa.png` |
| Cup and handle | INFY | `docs/chart_approval_samples/INFY_thesis_chart_cup_handle_20260520_desktop_qa.png` | `docs/chart_approval_samples/INFY_thesis_chart_cup_handle_20260520_mobile_qa.png` |
| Bull flag | RELIANCE | `docs/chart_approval_samples/RELIANCE_thesis_chart_bull_flag_20260520_desktop_qa.png` | `docs/chart_approval_samples/RELIANCE_thesis_chart_bull_flag_20260520_mobile_qa.png` |
| VCP | TCS | `docs/chart_approval_samples/TCS_thesis_chart_vcp_20260520_desktop_qa.png` | `docs/chart_approval_samples/TCS_thesis_chart_vcp_20260520_mobile_qa.png` |
| Inverse H&S | SBIN | `docs/chart_approval_samples/SBIN_thesis_chart_inverse_head_shoulders_20260520_desktop_qa.png` | `docs/chart_approval_samples/SBIN_thesis_chart_inverse_head_shoulders_20260520_mobile_qa.png` |
| Supertrend flip | WIPRO | `docs/chart_approval_samples/WIPRO_thesis_chart_supertrend_20260520_desktop_qa.png` | `docs/chart_approval_samples/WIPRO_thesis_chart_supertrend_20260520_mobile_qa.png` |
| Multi-year breakout | HDFCBANK | `docs/chart_approval_samples/HDFCBANK_thesis_chart_multi_year_breakout_20260520_desktop_qa.png` | `docs/chart_approval_samples/HDFCBANK_thesis_chart_multi_year_breakout_20260520_mobile_qa.png` |

## Approval Criteria

- White TradingView-style canvas with readable candles.
- Large stock title and timeframe are readable and do not fight the axis labels.
- Entry, target, stop, upside, risk, and R:R are visible.
- Mobile screenshots do not show label collisions.
- Pattern drawings support the thesis without turning into debug clutter.

## Important Caveat

These samples use manual trade levels for renderer QA only. Scanner-integrated chart export is verified separately through pipeline tests and dry-run proof outputs. Do not treat these renderer QA samples as trading recommendations.
