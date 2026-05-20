# Chart Approval Handoff

Date: 2026-05-20

Status: Reopened on 2026-05-20 after visual review feedback. The prior gallery used forced renderer-QA overlays, including an INFY "Cup and Handle" that was not a real detector hit. That approval path has been removed. The current gallery contains only actual detector hits from the local OHLCV database; plan item `5r-18` stays open until those real-detector samples are explicitly approved.

## Approval Pack

Generated from actual detector hits, not forced labels:

```powershell
python scripts\gen_detected_chart_gallery.py --max-per-pattern 1
```

The browser screenshot exporter validated nonblank canvases for each desktop and mobile PNG.
The approval screenshots are copied into `docs/chart_approval_samples/` so the pushed repo contains the review evidence.
Open `docs/CHART_APPROVAL_GALLERY.html` for a single-page visual review of every detector-hit desktop/mobile sample.

Latest refresh: fake one-per-pattern renderer samples were deleted from the approval path. Current local detector search found real examples for Cup & Handle, Inverse H&S, and Multi-Year Breakout. VCP was searched but no current local-DB detector hit was found, so it is intentionally absent. Ascending Triangle, Bull Flag, and Supertrend were not included because the active detector registry does not currently emit them for this approval search.

| Detector pattern | Symbol | Desktop file | Mobile file |
|---|---|---|---|
| Cup & Handle | AETHER | `docs/chart_approval_samples/REAL_AETHER_cup_handle_20260520_desktop.png` | `docs/chart_approval_samples/REAL_AETHER_cup_handle_20260520_mobile.png` |
| Inverse H&S | ADFFOODS | `docs/chart_approval_samples/REAL_ADFFOODS_inverse_head_shoulders_20260520_desktop.png` | `docs/chart_approval_samples/REAL_ADFFOODS_inverse_head_shoulders_20260520_mobile.png` |
| Multi-Year Breakout | ARVIND | `docs/chart_approval_samples/REAL_ARVIND_multi_year_breakout_20260520_desktop.png` | `docs/chart_approval_samples/REAL_ARVIND_multi_year_breakout_20260520_mobile.png` |

## Approval Criteria

- White TradingView-style canvas with readable candles.
- Large stock title and timeframe are readable and do not fight the axis labels.
- Entry, target, stop, upside, risk, and R:R are visible.
- Mobile screenshots do not show label collisions.
- Pattern drawings support the thesis without turning into debug clutter.

## Important Caveat

These samples are actual detector outputs, not manually forced renderer QA labels. They are still not trading recommendations. The correct review question is whether the detector label is visually credible enough to trust; if not, tighten the detector or downgrade the label.
