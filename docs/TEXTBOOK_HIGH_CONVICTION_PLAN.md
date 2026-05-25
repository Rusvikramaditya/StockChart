# Textbook High-Conviction Pattern Plan

## Findings

- The issue was not a lack of detectors. The scanner already covered several
  major patterns, but scoring allowed decent/late setups to remain visible.
- Backtests showed high win rates with weak profit factor, which means losses
  were too large relative to winners.
- Volume, Stage 2 trend, RSI divergence, sector strength, and scan-date
  reward/risk need to be gates for loud alerts, not just dashboard notes.
- Deduplication was able to recompute a capped setup back into a higher tier;
  scorer caps must survive consolidation.

## Implementation Plan

1. Add only a few stricter patterns:
   - Flat Base / Darvas Box
   - Double Bottom / Undercut-and-Reclaim
   - High Tight Flag
2. Add Pocket Pivot as a confirmation filter, not a standalone pattern.
3. Tighten scoring:
   - minimum live pattern grade: 7.0/10
   - HIGHEST floor: 8.0/10
   - HIGHEST needs 2:1 reward/risk, <=10% stop distance, Stage 2, volume or
     pocket-pivot confirmation, leading sector RS, and weekly alignment
4. Use profile-specific detector policy:
   - Nifty 500 avoids weak broad-profile cup/multi-year candidates
   - small/mid avoids noisy IHS flooding, keeps multi-year breakouts
   - watchlist keeps all detectors for manual research
5. Cap Telegram to high-quality tiers and a small number of alerts.

## Verification

- Unit/regression suite must pass.
- Dry-run scans must finish with zero errors and produce dashboards.
