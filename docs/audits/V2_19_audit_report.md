# V2.19 Audit Report - Zero Options Trades Investigation

**Date:** 2026-02-02
**Backtest:** V2.19-ExecutionPatch
**Period:** Jan 1-31, 2025
**Log File:** `docs/audits/logs/stage2/V2_19_ExecutionPatch_logs.txt`

---

## Executive Summary

**Result:** ZERO options trades executed in the entire backtest period.

**Root Causes Identified:**
1. **DTE Configuration Conflict** - VASS selects 30-45 DTE for low IV, but SPREAD_DTE_MAX=21 blocks them
2. **"No price available"** - Router skipped the ONE valid spread signal due to missing pricing
3. **Sizing Bugs** - Incorrect margin calculations in intraday options path

---

## AAP (Algorithmic Audit Protocol) Checklist

### Phase 1: V2.11 Funnel Analysis

| Funnel Stage | Data Source | Check | Status |
|:------------:|:-----------:|-------|:------:|
| 1. Market Regime | logs.txt | VIX Level (CBOE) detected correctly? | ✅ PASS |
| 2. VASS Selection | logs.txt | Correct Strategy Matrix selected? | ✅ PASS |
| 3. Sniper Signal | logs.txt | Signals generated? | ✅ PASS (1) |
| 4. Margin Filter | logs.txt | MARGIN_RESERVED logged? | ❌ N/A |
| 5. Execution | trades.csv | ComboMarketOrders filled? | ❌ FAIL (0) |

### Phase 2: Logic Integrity

| Check | Result | Evidence |
|-------|:------:|----------|
| VIX Level Classification | ✅ | VIX=13.2 → IV_Env=LOW (correctly < 15) |
| VIX Direction | ✅ | UVXY proxy tracking, Dir=NONE (stable) |
| Regime Score | ✅ | Score=64 (bullish, >60 threshold) |
| Signal Generation | ✅ | 1 BULL_CALL signal at 10:00 |
| Router Execution | ❌ | "No price available" - skipped |

### Phase 3: Critical Failure Flags

| Severity | Keyword | Found? | Count | Notes |
|:--------:|---------|:------:|:-----:|-------|
| 🔴 CRITICAL | VASS_REJECTION_GHOST | No | 0 | - |
| 🔴 CRITICAL | VASS_REJECTION | Yes | 500+ | "No contracts met spread criteria" |
| 🔴 CRITICAL | ROUTER: SKIP | Yes | 1 | "No price available" |
| 🟡 WARN | SLIPPAGE_EXCEEDED | No | 0 | - |
| 🟢 INFO | GAMMA_PIN_EXIT | No | 0 | No positions to exit |
| 🟢 INFO | SWING: Spread construction failed | Yes | 500+ | Every minute from 10:00-15:00 |

---

## Detailed Log Analysis

### The ONE Valid Signal (Line 194)

```
2024-01-03 10:00:00 SPREAD: ENTRY_SIGNAL | BULL_CALL: Regime=64 | VIX=13.2 |
  Long=399.78 Short=404.78 | Debit=$2.60 MaxProfit=$2.40 | x20 | DTE=15 Score=3.25
```

**Analysis:**
- Regime 64 > 60 → CALL direction (correct)
- VIX 13.2 < 15 → LOW IV environment (correct)
- DTE=15 (within 14-21 range)
- Score=3.25 > 2.0 threshold
- Debit=$2.60, MaxProfit=$2.40 (valid spread)

**This signal should have executed but was SKIPPED.**

### The Failure (Line 196)

```
2024-01-03 10:00:00 ROUTER: SKIP | QQQ   240119C00399780 | No price available
```

**Problem:** Router couldn't get pricing for the option symbol at execution time.

### Continuous Rejections (Lines 200-500+)

```
VASS_REJECTION: Direction=CALL | IV_Env=LOW | VIX=13.2 | Regime=64 |
  Contracts_checked=11 | Reason=No contracts met spread criteria (DTE/delta/credit)
```

**Pattern:** Every minute from 10:00 to 15:00 (300+ rejections per day)

**Why only 11 contracts checked?**
- SPREAD_DTE_MIN=14, SPREAD_DTE_MAX=21 (7-day window)
- Only Jan 19 expiry (15 DTE) qualifies
- ~11 strikes passed basic filters

---

## Root Cause Analysis

### Bug #1: DTE Configuration Conflict (CRITICAL)

**Config values in conflict:**

```python
# VASS expects these DTE ranges:
VASS_LOW_IV_DTE_MIN = 30   # For VIX < 15: 30-45 DTE (monthly)
VASS_LOW_IV_DTE_MAX = 45

# But main.py filters with:
SPREAD_DTE_MIN = 14        # Min 14 DTE
SPREAD_DTE_MAX = 21        # Max 21 DTE (blocks > 21!)
```

**Impact:** In low IV (VIX < 15), VASS wants monthly options (30-45 DTE) but `SPREAD_DTE_MAX=21` filters them out. Result: Contract universe reduced to ~11 strikes in weekly expiry only.

**Fix Options:**

Option A - Align SPREAD with VASS:
```python
SPREAD_DTE_MAX = 45  # Allow monthly expiries
```

Option B - Align VASS with SPREAD:
```python
VASS_LOW_IV_DTE_MIN = 14  # Use same range
VASS_LOW_IV_DTE_MAX = 21
```

### Bug #2: "No price available" in Router (CRITICAL)

**Location:** `portfolio/portfolio_router.py`

**Symptom:** Valid spread signal skipped because pricing data unavailable.

**Possible causes:**
1. Option symbol not in Securities collection at that moment
2. Bid/ask prices = 0 (data gap)
3. Symbol resolution timing issue

**Fix:** Add retry logic or fallback pricing.

### Bug #3: Sizing Calculation (HIGH - Intraday path)

**Location:** `main.py` lines 3246-3260

```python
# Line 3246: Pre-check blocks ALL options when margin < cap
if margin_remaining < config.OPTIONS_MAX_MARGIN_CAP:
    return  # Blocks everything!

# Line 3260: Subtracts cap instead of using as limit
margin_available_for_options = margin_remaining - config.OPTIONS_MAX_MARGIN_CAP
# Should be: min(margin_remaining, config.OPTIONS_MAX_MARGIN_CAP)
```

**Impact:** Affects `_scan_intraday_options()` path, not swing spreads. Still needs fixing.

---

## Performance Summary

### Trend Engine (Working)

| Metric | Value |
|--------|-------|
| Signals Generated | 4 per day |
| Entries Approved | 2 (position limit) |
| Trades Filled | 3 (TNA, FAS, SSO) |
| Status | ✅ WORKING |

### Options Engine (BLOCKED)

| Metric | Value |
|--------|-------|
| Swing Signals Generated | 1 |
| Signals Skipped (No Price) | 1 |
| VASS Rejections | 500+ |
| Trades Filled | 0 |
| Status | ❌ BLOCKED |

### Micro Regime Engine (Active)

| Metric | Value |
|--------|-------|
| Updates Logged | Every 15 min |
| VIX Level | 12.4-13.2 (NORMAL/GOOD_MR) |
| VIX Direction | NONE (stable) |
| Status | ✅ WORKING (but no intraday signals due to other blocks) |

---

## Recommendations

### Priority 1: Fix DTE Conflict

**File:** `config.py`

Either widen SPREAD_DTE_MAX to 45 (to match VASS_LOW_IV range) or lower VASS_LOW_IV_DTE_MIN to 14.

### Priority 2: Fix Router Price Lookup

**File:** `portfolio/portfolio_router.py`

Add retry logic when "No price available" is encountered. Or use fallback to market order.

### Priority 3: Fix Sizing Bugs

**File:** `main.py`

Lines 3246 and 3260 - correct the margin calculations for intraday options.

### Priority 4: Add Throttling for Rejections

The log shows 500+ rejection messages per day. Add smarter throttling (e.g., only log every 15 min instead of every minute).

---

## WORKBOARD Bug Registry

| ID | Category | Bug | Severity | Status |
|:--:|:--------:|-----|:--------:|:------:|
| V2.19-1 | Config | VASS_LOW_IV_DTE (30-45) conflicts with SPREAD_DTE_MAX (21) | 🔴 CRITICAL | To Fix |
| V2.19-2 | Router | "No price available" skips valid spread signals | 🔴 CRITICAL | To Fix |
| V2.19-3 | Sizing | Margin guard pre-check blocks options when margin < cap | 🟡 HIGH | To Fix |
| V2.19-4 | Sizing | margin_available = margin - cap (should be min) | 🟡 HIGH | To Fix |

---

## Next Steps

1. Fix DTE conflict (Priority 1)
2. Investigate router price lookup failure (Priority 2)
3. Fix sizing bugs (Priority 3)
4. Re-run backtest as V2.20

---

*Report generated by Claude Code - AAP V2.11 Protocol*
