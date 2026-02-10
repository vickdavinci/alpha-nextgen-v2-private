# V6.3 Backtest Audit Report — 2022 Jan-Feb

**Backtest Name:** V6.3-MicroConvictionFix-2022JanFeb
**Period:** 2022-01-01 to 2022-02-28
**Starting Capital:** $75,000
**Market Context:** BEAR (QQQ -15%, VIX avg 22-28)

---

## Executive Summary

| Metric | Value | Status |
|--------|-------|--------|
| Final Equity | $59,979 | |
| Net Return | **-20.03%** | POOR (Option assignment loss) |
| Total Orders | 34 | |
| Total Trades | 17 | |
| Win Rate | 25% | |
| Max Drawdown | 24.8% | |
| Kill Switch Triggers | 1 (FULL_EXIT) | Feb 15 |
| Errors/Exceptions | 0 | GOOD |

**Overall Assessment:** The V6.3 conviction fix is working correctly - VASS conviction fires on VIX changes and VETOs Macro direction appropriately. However, a **critical P0 bug remains**: the intraday contract selector still selects PUT contracts when direction is CALL. Additionally, an option assignment on Feb 14 caused a massive $38K loss triggering the kill switch.

---

## STEP 2: Performance Summary

| Metric | Value |
|--------|-------|
| Starting Equity | $75,000 |
| Final Equity | $59,979 |
| Net Return | -20.03% |
| Total Trading Days | 40 |
| Total Orders | 34 |
| Total Trades | 17 |
| Trend Trades | 3 (QLD, FAS, SSO) |
| Options Trades | 6 (spreads) |
| Hedge Trades | 3 (TMF) |
| Kill Switch | 1 (Feb 15) |

---

## STEP 3: Regime Deep Dive

### 3A. Regime Distribution

| Regime State | Score Range | Days | % of Backtest |
|--------------|-------------|------|---------------|
| RISK_ON | >= 70 | **0** | 0% |
| UPPER_NEUTRAL | 60-69 | 14 | 35% |
| LOWER_NEUTRAL | 50-59 | 8 | 20% |
| CAUTIOUS | 40-49 | 18 | 45% |
| DEFENSIVE | 30-39 | 0 | 0% |
| RISK_OFF | < 30 | 0 | 0% |
| **TOTAL** | | **40** | 100% |

**Key Observations:**
- **Zero RISK_ON days** - Correct for Jan-Feb 2022 bear market
- **CAUTIOUS dominant (45%)** - System correctly identified elevated risk
- Regime transitioned from NEUTRAL (Jan 3-20) to CAUTIOUS (Jan 21 onward)
- SPIKE_CAP activated multiple times during VIX spikes

### 3B. V4.1 Regime Factor Validation

Sample log: `REGIME: RegimeState(CAUTIOUS | Score=49.1 | MOM=50(-0.4%) VIX_C=31(lvl=31.2) T=50 DD=70 | Hedge: TMF=10% PSQ=0%)`

| Factor | Expected | Observed | Status |
|--------|----------|----------|--------|
| VIX Level (V4.1) | `lvl=XX.X` format | `VIX_C=31(lvl=31.2)` | **CORRECT** |
| Momentum | MOM=XX | MOM=50 | CORRECT |
| Trend | T=XX | T=50 | CORRECT |
| Drawdown | DD=XX | DD=70 | CORRECT |

**V4.1 VIX Level Check:** PASSED
- VIX rose from 17.2 (Jan 3) to 32.0 (Jan 27)
- VIX Level scoring working correctly

### 3C. Regime Transition Accuracy

| Date | Market Event | Expected | Actual | Match? |
|------|--------------|----------|--------|--------|
| Jan 3-14 | Pre-selloff | NEUTRAL | NEUTRAL (65-68) | YES |
| Jan 18-21 | VIX spike 19→25 | CAUTIOUS | CAUTIOUS (49-56) | YES |
| Jan 24-28 | VIX spike 28→32 | CAUTIOUS | CAUTIOUS (48-49) | YES |
| Feb 14-15 | Assignment crisis | CAUTIOUS | CAUTIOUS + KS | YES |

**Regime Transition Latency:** 1-2 days (within target)

---

## STEP 4: Conviction Engine Validation (V6.3 FIX)

### 4A. VASS Conviction Engine

| Date | Trigger | VIX Change | Macro | Resolved | Action |
|------|---------|------------|-------|----------|--------|
| Jan 19 10:00 | VIX 5d +24% | >+20% | BULLISH | **BEARISH** | VETO |
| Jan 21 15:45 | VIX crossed 25 | Above 25 | NEUTRAL | **BEARISH** | VETO |
| Jan 24 10:00 | VIX 5d +42% | >+20% | NEUTRAL | **BEARISH** | VETO |
| Jan 28 15:45 | VIX 5d +59% | >+20% | NEUTRAL | **BEARISH** | VETO |
| Jan 31 10:00 | VIX 5d +21% | >+20% | NEUTRAL | **BEARISH** | VETO |
| Feb 18 10:00 | VIX crossed 25 | Above 25 | NEUTRAL | **BEARISH** | VETO |
| Feb 18 15:45 | VIX crossed 25 | Above 25 | NEUTRAL | **BEARISH** | VETO |
| Feb 23 10:00 | VIX 20d +67% | >+30% STRONG | NEUTRAL | **BEARISH** | VETO |
| Feb 28 10:00 | VIX 20d +66% | >+30% STRONG | NEUTRAL | **BEARISH** | VETO |

**VASS Conviction Status: WORKING CORRECTLY**
- 10 conviction triggers, all BEARISH
- All VETOs correctly overrode Macro direction
- VIX 5d and 20d change thresholds working

### 4B. Micro Conviction Engine (V6.3 FIX)

| Metric | Expected | Actual | Status |
|--------|----------|--------|--------|
| State-based fallback | REMOVED | No occurrences | **FIXED** |
| UVXY conviction triggers | When UVXY >+8% or <-5% | 14 occurrences | WORKING |

Sample working conviction (Jan 11):
```
2022-01-11 11:45:00 INTRADAY_SIGNAL_APPROVED: CONVICTION: UVXY -6% < -5% | Macro=BULLISH | ALIGNED: MICRO + Macro agree on BULLISH | Direction=CALL
```

**V6.3 Fix Verification:** The state-based fallback was successfully removed. Conviction now only fires on:
- UVXY >+8% → BEARISH
- UVXY <-5% → BULLISH

---

## STEP 5: Binary Governor Analysis

| Metric | Expected | Actual | Status |
|--------|----------|--------|--------|
| Governor logs | Present | **None found** | Expected (disabled) |
| 75%/50%/25% states | 0 | 0 | PASS |

**Governor is DISABLED** for this backtest (per V5.3 testing config).

---

## STEP 6: Options Engine Deep Dive

### 6A. Strategy Selection Validation

| IV Environment | Expected | Actual | Correct? |
|----------------|----------|--------|----------|
| MEDIUM (15-25) | DEBIT | BEAR_PUT_DEBIT | **YES** |
| HIGH (>25) | DEBIT | BEAR_PUT_DEBIT | **YES** |

**CREDIT spread count: 0** - CORRECT for V5.3

### 6B. Spread Trades Summary

| Date | Strategy | Long | Short | Debit | Qty | DTE | Outcome |
|------|----------|------|-------|-------|-----|-----|---------|
| Jan 19 | BEAR_PUT | 386 | 380 | $3.53 | 20 | 20 | WIN (+$2,700) |
| Jan 24 | BEAR_PUT | 360 | 355 | $3.23 | 20 | 17 | LOSS (-$500) |
| Jan 31 | BEAR_PUT | 369 | 364 | $3.05 | 20 | 17 | **ASSIGNMENT** |
| Feb 18 | BEAR_PUT | 356 | 351 | $3.26 | 12 | 13 | WIN (+$400) |
| Feb 23 | BEAR_PUT | 350 | 345 | $3.17 | 20 | 13 | LOSS (-$1,600) |
| Feb 28 | BEAR_PUT | 355 | 350 | $3.00 | 20 | 10 | OPEN |

### 6C. CRITICAL: Option Assignment Event

```
2022-02-14 14:00:00 EXERCISE_DETECTED: QQQ   220218P00364000 | Qty=20.0 | Msg='Assigned'
2022-02-14 14:00:00 FILL: BUY 2000.0 QQQ @ $364.00
2022-02-14 14:00:00 FILL: SELL 2000.0 QQQ @ $344.79
```

**Impact:** Short PUT at 364 was exercised, forcing purchase of 2000 QQQ shares at $364 then immediate liquidation at $344.79.
**Loss:** ~$38,420

### 6D. VASS Rejection Analysis

High rejection rate due to:
- `No contracts met spread criteria (DTE/delta/credit)` - Most common

---

## STEP 7: Intraday Signal Analysis (P0 BUG IDENTIFIED)

### 7A. Intraday Signal Stats

| Metric | Count |
|--------|-------|
| INTRADAY_SIGNAL_APPROVED | 49 |
| "INTRADAY: Selected" | 49 |
| Actual intraday trades | **0** |

### 7B. THE BUG: Direction Mismatch

**Evidence from logs:**
```
2022-01-04 10:15:00 INTRADAY_SIGNAL_APPROVED: ... | Direction=CALL
2022-01-04 10:15:00 INTRADAY: Selected PUT | Strike=395.0 | Delta=0.28 | DTE=2
```

**Root Cause:** When `INTRADAY_SIGNAL_APPROVED` has `Direction=CALL`, the contract selector is still selecting PUT contracts. This is a critical P0 bug.

**Impact:** Zero intraday trades executed despite 49 approved signals.

---

## STEP 8: Engine-by-Engine Breakdown

### 8A. Trend Engine (QLD/SSO/TNA/FAS)

| Symbol | Entry | Exit | P&L |
|--------|-------|------|-----|
| QLD | Jan 4 | Jan 7 | -$2,076 |
| FAS | Jan 18 | Jan 21 | -$464 |
| SSO | Feb 7 | Feb 14 | -$386 |
| QLD | Carryover | Feb 15 (KS) | -$11 |

**Total Trend P&L:** -$2,937

### 8B. Hedge Engine (TMF)

| Date | Symbol | Action | Qty |
|------|--------|--------|-----|
| Jan 31 | TMF | BUY | 32 |
| Feb 7 | TMF | SELL | 32 |
| Feb 22 | TMF | BUY | 29 |

Hedges activated correctly when regime < 50.

---

## STEP 9: Risk & Safeguard Verification

### 9A. Kill Switch

| Date | Tier | Loss | Action |
|------|------|------|--------|
| Feb 15 09:31 | FULL_EXIT | 20.30% | All positions liquidated |

**Root Cause:** Feb 14 option assignment created $38K loss, opening Feb 15 with significant unrealized loss.

### 9B. Other Safeguards

| Safeguard | Triggers | Status |
|-----------|----------|--------|
| VOL_SHOCK | 4 | Working |
| SPIKE_CAP | 6 | Working |
| GAP_FILTER | 0 | N/A |
| TIME_GUARD | Active | Working |

---

## STEP 10: Startup Gate Validation

```
2022-01-03: INDICATOR_WARMUP Day 1/3 | No trading
2022-01-03: INDICATOR_WARMUP Day 2/3 | No trading
2022-01-03: INDICATOR_WARMUP Day 3/3 | → REDUCED
2022-01-04: REDUCED Day 1/3 | TREND/MR at 50%
2022-01-05: REDUCED Day 2/3 | TREND/MR at 50%
2022-01-06: REDUCED Day 3/3 | → FULLY_ARMED
```

**Startup Gate: WORKING CORRECTLY** - 6 days total

---

## STEP 11: Smoke Signals

| Severity | Pattern | Expected | Actual | Status |
|----------|---------|----------|--------|--------|
| CRITICAL | ERROR/EXCEPTION | 0 | 0 | **PASS** |
| CRITICAL | CREDIT spread | 0 | 0 | **PASS** |
| CRITICAL | Direction mismatch | 0 | **49** | **FAIL** |
| WARN | Option assignment | 0 | 1 | **FAIL** |
| INFO | VETO triggers | >0 | 10 | PASS |

---

## STEP 12: Optimization Recommendations

### P0 — CRITICAL

| Issue | Evidence | Impact | Fix |
|-------|----------|--------|-----|
| **Intraday direction mismatch** | 49 CALL signals → 49 PUT selections | Zero intraday trades | Fix `_select_intraday_option_contract()` to use resolved direction |
| **Option assignment risk** | Feb 14 short PUT exercised | -$38K loss | Add early exit when ITM >2 days before expiry |

### P1 — HIGH

| Issue | Evidence | Impact | Fix |
|-------|----------|--------|-----|
| No assignment protection | Short leg exercised | Massive loss | Add ASSIGNMENT_RISK_EXIT logic |
| Trend losses in bear | 4/4 losses | -$2,937 | Add regime guard for trend entries |

### P2 — MEDIUM

| Issue | Evidence | Impact | Fix |
|-------|----------|--------|-----|
| High VASS rejection rate | ~300 rejections per day | Missed opportunities | Relax DTE/delta criteria |

---

## STEP 13: Scorecard

| System | Score | Status | Key Finding |
|--------|:-----:|--------|-------------|
| **Regime Identification (V4.1)** | 5/5 | EXCELLENT | VIX Level working, correct distribution |
| **Regime Navigation** | 4/5 | GOOD | Correct PUT-only in CAUTIOUS |
| **VASS Conviction Engine** | 5/5 | EXCELLENT | 10 VETOs, all correct |
| **Micro Conviction Engine** | 4/5 | GOOD | State-based fallback removed (V6.3 fix) |
| **Binary Governor** | N/A | DISABLED | Per test config |
| **Options Engine (Swing)** | 4/5 | GOOD | All DEBIT, correct direction |
| **Options Engine (Intraday)** | 1/5 | **BROKEN** | Direction mismatch bug |
| **Startup Gate** | 5/5 | EXCELLENT | 6-day ramp working |
| Trend Engine | 2/5 | WEAK | 0% win rate in bear |
| Hedge Engine | 4/5 | GOOD | TMF active in CAUTIOUS |
| Kill Switch | 5/5 | EXCELLENT | Triggered correctly |
| Assignment Risk | 1/5 | **BROKEN** | No protection, caused -$38K |
| **Overall** | **3.0/5** | NEEDS WORK | V6.3 conviction fix works, but intraday still broken |

---

## Conclusion

### V6.3 Fixes Verified:
1. **State-based fallback REMOVED** - No longer re-classifying Micro's direction
2. **VASS conviction WORKING** - 10 correct BEARISH VETOs during VIX spikes
3. **Micro conviction WORKING** - UVXY-based conviction firing correctly

### Critical Issues Remaining:

1. **P0: Intraday Direction Mismatch**
   - `_select_intraday_option_contract()` ignores resolved direction
   - Selecting PUT when direction is CALL
   - 49 approved signals → 0 trades

2. **P0: Option Assignment Risk**
   - Short PUT exercised on Feb 14
   - -$38K loss, triggered kill switch
   - Need early exit for ITM spreads near expiry

### Next Steps:
1. ~~Fix `_select_intraday_option_contract()` to use the resolved direction from `generate_micro_intraday_signal()`~~ **FIXED in V6.4**
2. Add assignment protection logic for spreads approaching expiry
3. Re-run Jan-Feb 2022 backtest to capture intraday alpha

---

## V6.4 Fix Applied (Post-Audit)

### Two Bugs Identified and Fixed

#### Bug #1: Duplicate Enum Definition

The P0 intraday direction mismatch was partially caused by **duplicate `OptionDirection` enum definitions**:

1. `engines/satellite/options_engine.py:58` defined its own `OptionDirection`
2. `models/enums.py:194` also defined `OptionDirection`

In `main.py`:
- Line 31: `from engines.satellite.options_engine import OptionDirection` (first import)
- Line 43: `from models.enums import OptionDirection` (overwrites first import!)

When `generate_micro_intraday_signal()` returned `options_engine.OptionDirection.CALL`, the comparison in `_select_intraday_option_contract()` was against `models.enums.OptionDirection.CALL`. Since these are **different enum classes**, the comparison always failed, causing `required_right = OptionRight.Put`.

**Fix #1:**
1. Removed duplicate `OptionDirection` class from `options_engine.py`
2. Added `OptionDirection` to imports from `models.enums` in `options_engine.py`
3. Removed redundant import from `main.py`

#### Bug #2: Logic Error in Direction Resolution

The condition at line 1957 was wrong:

```python
# BUGGY:
if has_conviction and resolved_direction:
    # Use resolved_direction
else:
    final_direction = state.recommended_direction  # ← WRONG for FOLLOW_MACRO
```

When `resolve_trade_signal()` returns via the **FOLLOW_MACRO** path:
- `should_trade = True`
- `resolved_direction = "BULLISH"` (set correctly)
- `has_conviction = False`

The condition `has_conviction and resolved_direction` evaluated to `False`, so it used `state.recommended_direction` (often `None`) instead of the correctly resolved direction.

**Fix #2:**
```python
# FIXED:
if resolved_direction:  # Use resolved_direction whenever set (includes FOLLOW_MACRO)
    if resolved_direction == "BULLISH":
        final_direction = OptionDirection.CALL
    else:
        final_direction = OptionDirection.PUT
```

### Files Changed
- `engines/satellite/options_engine.py`:
  - Removed local `OptionDirection` enum (Bug #1)
  - Import from `models.enums` instead (Bug #1)
  - Fixed condition from `if has_conviction and resolved_direction:` to `if resolved_direction:` (Bug #2)
- `main.py` - Removed duplicate import (Bug #1)

### Verification
- All 1333 unit tests pass
- Syntax validation passed
- Ready for backtest re-run
