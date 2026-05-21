# Pattern Finder System Audit & Fix Plan

**Initiated**: 2026-05-21
**Owner**: Real-money commercial platform — vigilance gate
**Status legend**: 🔴 critical / 🟠 high / 🟡 medium / 🟢 cosmetic. ✅ = shipped.

## Pattern Detector Status

| Detector | Audit | Pivot bug | Quality grade | Notes |
|---|---|---|---|---|
| Ascending Triangle | ✅ | ✅ | ✅ 0-10 | 6-component breakdown |
| Cup & Handle | ✅ | n/a (rim-based) | ✅ 0-10 | 7-component breakdown |
| VCP | ✅ | ✅ | ✅ 0-10 | Real swing detection + Stage 2 gate |
| Inverse Head & Shoulders | ✅ | ✅ | ✅ 0-10 | Sloped neckline + prior downtrend |
| Bull Flag | ✅ | ✅ | ✅ 0-10 | 8-component breakdown |
| Multi-Year Breakout | ✅ | ✅ already safe | ✅ 0-10 | 7-component breakdown; PIVOT READY now reachable |
| Supertrend | ✅ | n/a (ATR-based) | ✅ 0-10 | Staleness gate at 1 bar; 5-component breakdown |

## Findings — Action Items

### 🔴 P0 — Real-money loss risk

- [x] ✅ **MYBO-001**: Multi-Year Breakout PIVOT READY unreachable. Fixed by gating volume surge only when `breakout=True`; pivot-ready surfaces without surge, surge now a quality component.
- [x] ✅ **STR-001**: Supertrend no staleness gate. Fixed with `max_flip_age_bars=1` (today or yesterday only). Flips >=2 bars old rejected.

### 🟠 P1 — False signals to user

- [x] ✅ **MYBO-002**: Multi-Year Breakout 0-10 quality grade. Components: touch_count, touch_flatness, touch_spread, duration, volume_surge, breakout_proximity, stop_tightness.
- [x] ✅ **STR-002**: Supertrend 0-10 quality grade. Components: flip_freshness, atr_regime, stop_tightness, entry_extension, volume_confirmation.
- [x] ✅ **STAGE2-001**: Stage 2 filter now reads intraday `high`/`low` for 52w levels (was using `close`).

### 🟡 P2 — Detection looseness

- [x] ✅ **MYBO-003**: Multi-Year Breakout `resistance_tolerance_pct` 3.0 -> 1.5.
- [x] ✅ **MYBO-004**: New `max_touch_dispersion_pct=1.0` guard. Touches must cluster.
- [x] ✅ **MYBO-005**: New `min_touch_spread_fraction=0.5` guard. Touches must span at least half the window.
- [x] ✅ **MYBO-006**: New `max_stop_distance_pct=12.0` cap.
- [x] ✅ **STR-003**: Supertrend pivot now = close at flip bar. Extension surfaced separately.
- [x] ✅ **STR-004**: Supertrend status now reflects flip age: today = BREAKING OUT, 1-bar-old = PIVOT READY.
- [x] ✅ **DB-001**: Filesystem lock added around fetch_missing + fetch stages in scanner.py. PID-based stale-lock reclamation.

### 🟢 P3 — Cosmetic / nice-to-have

- [x] ✅ **UI-001**: TARGET annotation card now reserves top 110px for pattern title and falls back to the right-of-arrow column when the default position would clip.
- [x] ✅ **UX-001**: `skip_reason` breakdown surfaced in dashboard via `<details class="skip-breakdown">` panel with bucket counts and sample symbols.
- [x] ✅ **PERF-001**: `DataLoader.get_recent_close_stats()` does latest/prior/MA50/MA200 in a single SQLite window-function query. Scanner pre-computes once and shares the dict with `compute_market_regime` (advance/decline) + `compute_leaderboard` (breadth), eliminating ~500 per-symbol SELECTs per scan.

## Pattern Audit Playbook

Same template across all detectors:
1. Tighten config thresholds in `config/settings.py`.
2. Rewrite detector: explicit rejection gates + 0-10 `_pattern_quality()` with pattern-specific components.
3. Add tests at `tests/test_<pattern>.py`: textbook detected, weak rejected, edge cases, grade ordering, breakdown keys, 10-cap, BREAKING OUT regression.
4. Run full suite — zero regressions required.
5. Update this doc.

## Final Suite Status

**255 tests pass, 0 failures, 0 regressions.**

All P0 + P1 + P2 + P3 items shipped. Audit closed.
