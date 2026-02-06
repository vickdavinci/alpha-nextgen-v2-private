# V3.5 Regime Audit Report: 2022 H1 Backtest

**Date:** 2026-02-05
**Backtest Period:** 2022-01-01 to 2022-06-30
**Market Context:** SEVERE BEAR (S&P -20%, Nasdaq -30%)
**Result:** -22.71% return with only 2 BULL_CALL entries (zero PUTs executed)

---

## Executive Summary

The 2022 H1 backtest failed to navigate the bear market properly due to **three compounding issues**:

1. **Dead Zone Bug (V3.4)**: Direction selection blocked ALL options in regime 50-70, wasting 69 trading days
2. **Margin Blocking**: When PUT signals finally fired (regime 49-50), they were blocked by over-conservative margin checks
3. **Governor at 0%**: Extended period at Governor 0% with $43K equity couldn't satisfy $48-60K margin requirements

---

## Regime Navigation Analysis

### Regime Distribution (165 days)

| Regime | Days | % | Expected Action | Actual Action |
|--------|------|---|-----------------|---------------|
| RISK_ON (>70) | 16 | 10% | CALL spreads | ✅ 2 BULL_CALL entered |
| NEUTRAL (50-70) | 69 | 42% | PUT @ 25-50% | ❌ Dead zone blocked all |
| CAUTIOUS (40-50) | 41 | 25% | PUT @ 100% | ❌ Margin blocked |
| DEFENSIVE (<40) | 39 | 24% | PUT @ 100% | ❌ Governor 0% + margin |

**Key Finding**: The algo correctly IDENTIFIED the regime (80 days in CAUTIOUS/DEFENSIVE = 49%), but failed to NAVIGATE it due to code bugs and margin constraints.

### Regime Detection Quality

| Metric | Status | Notes |
|--------|--------|-------|
| VIX Shock Cap | ✅ Working | Triggered Jan 6, 14, 18, Feb 4, 14, etc. |
| Recovery Hysteresis | ✅ Working | Blocked premature upgrades (Jan 3, 18) |
| 3-Factor Scoring | ✅ Reasonable | Trend, VIX, Drawdown balanced |
| Transition Timing | ⚠️ Moderate | Jan 6 shock cap activated same day as VIX spike |

---

## Issue #1: Dead Zone Bug (FIXED in V3.5)

### Problem

The direction selection in main.py created a 20-point dead zone:

```python
# V3.4 (BROKEN)
if regime > 70: CALL
elif regime < 50: PUT
else: return  # ← 69 days wasted here
```

### Evidence

```
2022-01-24: REGIME Score=55.1 | NEUTRAL
2022-01-25: REGIME Score=54.9 | NEUTRAL
2022-01-26: REGIME Score=52.4 | NEUTRAL
2022-01-27: REGIME Score=50.6 | NEUTRAL
```

No options entries for 69 NEUTRAL days.

### Fix Applied (V3.5)

```python
# V3.5 (FIXED)
if regime > 70: directions = [CALL]
elif regime >= 60: directions = [CALL, PUT]  # Upper NEUTRAL - both
else: directions = [PUT]  # Lower NEUTRAL/CAUTIOUS/BEAR
```

---

## Issue #2: Margin Blocking PUT Entries

### Problem

When regime finally dropped to CAUTIOUS (49-50) and PUT signals fired, they were blocked:

```
2022-01-28 15:45:00 SPREAD: ENTRY_SIGNAL | BEAR_PUT: Regime=49 | x20 contracts
2022-01-28 15:45:00 SPREAD: BLOCKED - Insufficient margin |
    Required=$48,000 | Effective Free=$35,072 |
    Width=$4.0 x20 contracts

2022-02-23 15:45:00 SPREAD: ENTRY_SIGNAL | BEAR_PUT: Regime=50 | x20 contracts
2022-02-23 15:45:00 SPREAD: BLOCKED - Insufficient margin |
    Required=$60,000 | Effective Free=$34,860 |
    Width=$5.0 x20 contracts
```

### Root Cause

The margin calculation is over-conservative:

```python
margin_per_contract = spread_width * 100 * 6  # 6× safety factor!
min_free_margin = total_equity * 0.20  # 20% cushion!
effective_free_margin = max(0, free_margin - min_free_margin)
```

With:
- Spread width = $4-5
- 6× safety = $2,400-$3,000 per contract
- 20 contracts × $2,400 = $48,000 required
- But only $35K available

### Recommended Fix

**P1: Reduce margin safety factor from 6× to 4×**

```python
# Current (too conservative)
margin_per_contract = spread_width * 100 * 6  # $3,000/contract for $5 spread

# Recommended (still conservative but practical)
margin_per_contract = spread_width * 100 * 4  # $2,000/contract for $5 spread
```

**P2: Add margin-aware sizing**

```python
# Before: Request 20 contracts, get blocked
# After: Calculate max contracts that fit in margin, request that

max_contracts_by_margin = effective_free_margin // margin_per_contract
actual_contracts = min(requested_contracts, max_contracts_by_margin)
```

---

## Issue #3: Governor at 0% Extended Period

### Timeline

```
Jan 19: 100% → 50% (5.9% DD)
Jan 22: 50% → 0%  (13.8% DD)
Jan 22 - Feb+: Stuck at 0%
```

### Impact

At Governor 0%, total equity was ~$43,000:
- 20% cushion = $8,600
- Effective free = ~$35,000
- But 20 contracts @ $2,400 = $48,000 needed

**The V3.0 fix to allow PUTs at Governor 0% was working, but the margin check blocked them anyway.**

---

## Spread Entry Summary

| Date | Direction | Regime | Result |
|------|-----------|--------|--------|
| Jan 10 | BULL_CALL | 73 | ✅ Filled 7 contracts |
| Jan 11 | BULL_CALL | 73 | ✅ Filled 16 contracts |
| Jan 28 | BEAR_PUT | 49 | ❌ Margin blocked (need $48K, have $35K) |
| Feb 23 | BEAR_PUT | 50 | ❌ Margin blocked (need $60K, have $35K) |
| Apr 4 | BULL_CALL | 74 | ✅ Filled 16 contracts |

**Total: 3 BULL_CALL entries, 0 BEAR_PUT entries**

---

## Recommendations

### P0 (Critical - Done)

| Fix | Status |
|-----|--------|
| V3.5 Remove dead zone | ✅ Done |
| V3.5 Dual-direction scanning in upper NEUTRAL | ✅ Done |
| V3.5 Fix governor gate for NEUTRAL zone | ✅ Done |

### P1 (High - TODO)

| ID | Fix | Impact |
|:--:|-----|--------|
| P1-1 | Reduce margin safety factor 6× → 4× | Allow PUT entries |
| P1-2 | Add margin-aware sizing | Size to fit available margin |
| P1-3 | Reduce 20% equity cushion to 10% | More margin available |

### P2 (Medium - TODO)

| ID | Fix | Impact |
|:--:|-----|--------|
| P2-1 | Log when margin blocks entry with "would have" size | Better diagnostics |
| P2-2 | Add OPTIONS_MAX_MARGIN_PCT config | Control max margin usage |

---

## Regime Detection Scorecard

| Dimension | Score | Notes |
|-----------|:-----:|-------|
| Identification | 4/5 | Correctly detected 80 CAUTIOUS/DEFENSIVE days |
| Transition Timing | 4/5 | VIX shock cap activated same day as spikes |
| Stability | 3/5 | Some oscillation in NEUTRAL zone |
| Navigation | 1/5 | Failed to execute protective strategies |
| **Overall** | **3/5** | Good detection, poor execution |

---

## Conclusion

The V3.3 regime engine correctly identified the 2022 bear market (80 days in CAUTIOUS/DEFENSIVE). The failure was in **execution**:

1. **Dead zone bug** blocked 69 days of potential PUT entries (V3.5 fixes this)
2. **Over-conservative margin** blocked the 2 PUT signals that did fire (needs P1 fix)
3. **Sizing too aggressive** for available margin (needs margin-aware sizing)

**Next Steps:**
1. ✅ Deploy V3.5 fixes (dual-direction scanning)
2. Apply P1 margin fixes (reduce 6× to 4×, add margin-aware sizing)
3. Re-run 2022 H1 backtest to verify PUT entries execute

---

*Report generated by Claude Opus 4.5 automated audit system*
