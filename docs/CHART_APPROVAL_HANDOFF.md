# Chart Approval Handoff

Date: 2026-05-20

Status: Phase 5R implementation and QA evidence are ready for user approval. The remaining plan item is `5r-18`, which must stay open until the real-stock samples are approved against the TradingView-style reference.

## Approval Pack

Generated with real local OHLCV rows:

```powershell
python scripts\gen_sample_thesis_chart.py --sample-pack
```

The browser screenshot exporter validated nonblank canvases for each desktop and mobile PNG.

| Overlay family | Symbol | Desktop QA file | Mobile QA file |
|---|---|---|---|
| Ascending triangle | ADANIENT | `output/charts/ADANIENT_thesis_chart_20260520_desktop_qa.png` | `output/charts/ADANIENT_thesis_chart_20260520_mobile_qa.png` |
| Cup and handle | INFY | `output/charts/INFY_thesis_chart_cup_handle_20260520_desktop_qa.png` | `output/charts/INFY_thesis_chart_cup_handle_20260520_mobile_qa.png` |
| Bull flag | RELIANCE | `output/charts/RELIANCE_thesis_chart_bull_flag_20260520_desktop_qa.png` | `output/charts/RELIANCE_thesis_chart_bull_flag_20260520_mobile_qa.png` |
| VCP | TCS | `output/charts/TCS_thesis_chart_vcp_20260520_desktop_qa.png` | `output/charts/TCS_thesis_chart_vcp_20260520_mobile_qa.png` |
| Inverse H&S | SBIN | `output/charts/SBIN_thesis_chart_inverse_head_shoulders_20260520_desktop_qa.png` | `output/charts/SBIN_thesis_chart_inverse_head_shoulders_20260520_mobile_qa.png` |
| Supertrend flip | WIPRO | `output/charts/WIPRO_thesis_chart_supertrend_20260520_desktop_qa.png` | `output/charts/WIPRO_thesis_chart_supertrend_20260520_mobile_qa.png` |
| Multi-year breakout | HDFCBANK | `output/charts/HDFCBANK_thesis_chart_multi_year_breakout_20260520_desktop_qa.png` | `output/charts/HDFCBANK_thesis_chart_multi_year_breakout_20260520_mobile_qa.png` |

## Approval Criteria

- White TradingView-style canvas with readable candles.
- Large stock title and timeframe are readable and do not fight the axis labels.
- Entry, target, stop, upside, risk, and R:R are visible.
- Mobile screenshots do not show label collisions.
- Pattern drawings support the thesis without turning into debug clutter.

## Important Caveat

These samples use manual trade levels for renderer QA only. Scanner-integrated chart export is verified separately through pipeline tests and dry-run proof outputs. Do not treat these renderer QA samples as trading recommendations.

