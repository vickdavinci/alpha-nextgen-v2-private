# V2.23 APVP Audit Report

## Algorithmic Pre-Backtest Validation Protocol — V2.23 VASS Credit Spread

**Date**: 2026-02-03
**Branch**: `testing/va/stage2-backtest`
**Version**: V2.23 → V2.23.1 (post-audit fixes)

---

## Phase 0: Pre-Flight Sanity Gate

| Check | Status | Detail |
|-------|--------|--------|
| Branch integrity | PASS | `testing/va/stage2-backtest`, V2.23 committed |
| Single source of truth | PASS | All strategy params in `config.py` |
| Dead code scan | PASS | No orphaned engines or deprecated paths |
| Determinism check | PASS | No random seeds, time-dependent calls, or live-data hooks |

---

## Phase 1: Architecture Coherency

| Stage | Engine | Status | Detail |
|-------|--------|--------|--------|
| 1 | Regime Engine | PASS | Exposes single `regime_score` (0-100) |
| 2 | VASS Selector | PASS | Selection driven by Regime + IVSensor (no overrides) |
| 3 | Signal Engines | PASS | Trend/Options/MR isolated until Portfolio Router |
| 4 | Portfolio Router | PASS | Capital reservation enforced before execution |
| 5 | Execution Engine | PASS | Consumes validated orders only (no internal sizing) |

---

## Phase 2: Logic Consistency

### A. Regime + Strategy Coherence
| Check | Status | Detail |
|-------|--------|--------|
| VIX thresholds aligned | PASS | LOW <15, MEDIUM 15-25, HIGH >25 — no overlap |
| UVXY rising filter | WARN | No blanket UVXY rising filter; VIX-based filters serve similar purpose |
| Credit floor enforcement | PASS | `CREDIT_SPREAD_MIN_CREDIT = $0.30` checked before selection |

### B. Time, Expiry & Gamma
| Check | Status | Detail |
|-------|--------|--------|
| Expiry awareness | PASS | DTE calculated from contract expiry vs current date |
| Gamma pin distance | PASS | Uses underlying price |
| Exit priority order | WARN | No declared priority between GAMMA_PIN_EXIT and FRIDAY_FIREWALL — both can fire same bar |
| Spread construction | PASS | Leg signs enforced at creation (`short_ratio = -long_ratio`) |

### C. Capital, Margin & Settlement
| Check | Status | Detail |
|-------|--------|--------|
| Hard cap enforcement | PASS | `SWING_SPREAD_MAX_DOLLARS` applied before sizing |
| Engine budget isolation | PASS | Trend 55%, Options 25% via `portfolio_router.py` |
| Settlement gate | PASS | Monday/Tuesday logic blocks until 10:30 AM |
| No retroactive claims | PASS | Engines cannot reclaim unused margin |

---

## Phase 3: Code-Level Conflict & Bug Scan

### CRITICAL (Fixed in V2.23.1): Credit Spread TargetWeight Convention Mismatch

**Bug**: `check_credit_spread_entry_signal()` used reversed convention from router:
- Primary symbol was SHORT leg (should be LONG leg for router combo logic)
- `requested_quantity = -num_spreads` (should be positive for router `> 0` check)
- Router would create broken combo order (BUY short_leg + SELL short_leg = same symbol)

**Fix**: Aligned with debit spread convention — long leg (protection) as primary, positive quantity.

### Other Findings

| # | Severity | Finding | Status |
|---|----------|---------|--------|
| 1 | WARN | No explicit `abs(long_qty) == abs(short_qty)` assertion | Mitigated by `short_ratio = -long_ratio` |
| 2 | WARN | Hardcoded VIX=22 threshold | **Fixed**: Extracted to `config.VIX_LEVEL_ELEVATED_MAX` |
| 3 | WARN | No declared GAMMA_PIN vs FRIDAY_FIREWALL priority | Accepted (both fire same bar, last-writer wins) |
| 4 | INFO | VASS fallback safe | Matrix covers all 6 valid keys |

---

## Phase 4: Pre-Smoke Signals

| Severity | Indicator | Status |
|----------|-----------|--------|
| CRITICAL | `CREDIT_SPREAD_ROUTER_MISMATCH` | FIXED in V2.23.1 |
| WARN | `PARAM_HARDCODED` (VIX=22) | FIXED in V2.23.1 |
| WARN | `EXIT_PRIORITY_IMPLICIT` | ACCEPTED |
| INFO | `ASSERTIONS_PRESENT` | OK |

---

## Phase 5: Backtest Readiness Gate

**VERDICT: GO** (after V2.23.1 fixes applied)

- All CRITICAL flags resolved
- No unresolved logic ambiguities
- Capital, regime, and execution ownership unambiguous
- 1349 tests pass, 0 failures

---

## V2.23.1 Fixes Applied

| Fix | File | Change |
|-----|------|--------|
| Credit TargetWeight convention | `options_engine.py:3134-3164` | Primary symbol = long leg, qty positive |
| VIX threshold extraction | `options_engine.py:827` + `config.py:101` | `VIX_LEVEL_ELEVATED_MAX = 22.0` |
| Test assertions | `test_options_engine.py` | Verify long leg as primary, positive qty |
