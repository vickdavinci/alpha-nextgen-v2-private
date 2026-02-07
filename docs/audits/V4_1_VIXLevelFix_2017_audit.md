# V4.1 VIX Level Fix - 2017 Full Year Backtest Audit

**Backtest Name:** V4.1-VIXLevelFix-2017
**Period:** January 1, 2017 - December 31, 2017
**Market Context:** Strong Bull Market (QQQ +32%, SPY +19%)
**Audit Date:** 2026-02-06
**Log File:** `docs/audits/logs/stage4/V4_1_VIXLevelFix_2017_logs.txt`

---

## Executive Summary

The V4.1 VIX Level fix **successfully achieved its primary goal**: the regime engine correctly identified 2017 as a RISK_ON environment starting January 5th. However, the backtest resulted in a **-26.4% loss** due to a catastrophic failure in the Drawdown Governor system that triggered a "death spiral" starting in June 2017.

| Outcome | Status |
|---------|--------|
| V4.1 Regime Detection | **PASSED** - RISK_ON achieved (Score 71-75) |
| Overall Performance | **FAILED** - Lost $13,203 (-26.4%) |
| Root Cause | Drawdown Governor death spiral |

---

## 1. Performance Summary

| Metric | Value |
|--------|-------|
| Starting Equity | $50,000 |
| Peak Equity (HWM) | $67,718 (+35.4%) |
| Final Equity | $36,797 |
| Net Return | **-26.41%** |
| Max Drawdown | **45.7%** |
| Total Orders | 210 |
| Win Rate | 38% |
| Loss Rate | 62% |
| Average Win | +2.72% |
| Average Loss | -2.04% |
| Profit-Loss Ratio | 1.34 |
| Sharpe Ratio | -0.737 |
| Sortino Ratio | -0.546 |
| Total Fees | $1,536.81 |

### Equity Curve Timeline

```
Jan 1:   $50,000 (Start)
Jan-May: $50,000 → $67,718 (+35.4% peak)
Jun 9:   $68,162 → $56,153 (-17.6% single day crash)
Jun 10:  Governor hits Scale=0%
Jun-Dec: Death spiral, continuous losses
Dec 29:  $36,797 (End)
```

---

## 2. V4.1 Regime Engine Validation

### 2A. RISK_ON Achievement (PRIMARY GOAL - PASSED)

The V4.1 VIX Level fix successfully achieved RISK_ON status:

**First RISK_ON:** January 5, 2017
```
2017-01-05 15:45:00 REGIME: RegimeState(RISK_ON | Score=71.5 | MOM=50(-0.0%) VIX=100(lvl=11.8) BR=70 DD=90 T=85)
```

**VIX Level Scoring Working Correctly:**
- VIX = 11.8 (below 15 threshold) → VIX Level Score = 100
- This contributed +25 points (weight 0.25 × score 100)
- Combined with other factors → Total Score = 71.5 (above RISK_ON threshold of 70)

**Year-End Regime:**
```
2017-12-29 15:45:00 REGIME: RegimeState(RISK_ON | Score=72.8 | MOM=50(+0.1%) VIX=100(lvl=10.2) BR=50 DD=90 T=85)
```

### 2B. Regime Distribution

| Regime State | Days | Percentage |
|--------------|------|------------|
| RISK_ON (≥70) | ~245 | ~98% |
| NEUTRAL (50-69) | ~5 | ~2% |
| CAUTIOUS (40-49) | 0 | 0% |
| DEFENSIVE (30-39) | 0 | 0% |
| RISK_OFF (<30) | 0 | 0% |

**Conclusion:** V4.1 correctly identified 2017 as a persistent bull market.

---

## 3. Engine-by-Engine Analysis

### 3A. Trend Engine (QLD/SSO/TNA/FAS)

| Metric | Value |
|--------|-------|
| Positions Entered | QLD, SSO (2 of 4 slots filled) |
| TNA Entries | 0 (blocked by ADX) |
| FAS Entries | 1 (later exited) |
| Entry Blocks | Multiple - ADX too weak |

**Key Finding:** ADX remained low (10-11) throughout 2017, blocking TNA and FAS entries:
```
TREND: TNA entry blocked - ADX 10.7 too weak (score=0.25 < 0.75, regime=74)
TREND: FAS entry blocked - ADX 10.6 too weak (score=0.25 < 0.75, regime=74)
```

**Assessment:** 3/5 - Working but overly restrictive ADX filter in low-volatility bull market.

### 3B. Options Engine (QQQ Spreads)

| Metric | Value |
|--------|-------|
| Spreads Entered | ~25 |
| KS_TIER3_OPTIONS Closures | 1 (June 12) |
| GOVERNOR_SHUTDOWN Closures | 15+ |
| VASS Rejections | Hundreds |

**Critical Issues Found:**

1. **June 12 Kill Switch Liquidation:**
   ```
   KS_TIER3_OPTIONS: Atomic close complete | Shorts=3 Longs=3
   ```
   - Closed 3 bull call spreads at 40-50% losses
   - Liquidated QLD and SSO positions simultaneously

2. **Persistent VASS Rejections:**
   ```
   VASS_REJECTION: Direction=CALL | IV_Env=LOW | VIX=9.8 | Contracts_checked=92 |
   Reason=No contracts met spread criteria (DTE/delta/credit)
   ```
   - Low VIX environment made finding suitable spreads difficult
   - Many days with zero valid spreads found

3. **MARGIN_UTIL_GATE Blocking:**
   ```
   MARGIN_UTIL_GATE: BLOCKED | Utilization=78.2% >= Max=70%
   ```
   - New entries blocked while existing positions bled

**Assessment:** 2/5 - Options engine contributed most to losses through forced liquidations.

### 3C. Mean Reversion Engine (TQQQ/SOXL)

| Metric | Value |
|--------|-------|
| Entries | 0 |
| Overnight Holds | 0 |

**Assessment:** N/A - Not active in 2017 backtest.

### 3D. Hedge Engine (TMF/PSQ)

| Metric | Value |
|--------|-------|
| TMF Allocation | 0% |
| PSQ Allocation | 0% |

Correctly stayed flat during RISK_ON regime.

**Assessment:** 5/5 - Working as designed.

### 3E. Yield Sleeve (SHV)

Not actively logged in this backtest.

---

## 4. Risk & Safeguard Analysis

### 4A. Kill Switch Events

| Event | Count | Details |
|-------|-------|---------|
| KS_TIER1 (REDUCE) | 1 | June 9 @ 12:01 (2.22% loss) |
| KS_TIER3_OPTIONS | 1 | June 12 - Atomic close |
| CB_LEVEL_1 | 1 | June 9 - Sizing reduced to 50% |

**June 9 Cascade:**
```
12:01 KS_GRADUATED: NONE → REDUCE | Loss=2.22% from sod
12:01 CB_LEVEL_1: TRIGGERED | SOD loss=2.22% >= 2.00%
12:38 WEEKLY_BREAKER: TRIGGERED | WTD loss=5.33%
```

### 4B. Drawdown Governor (CRITICAL FAILURE)

**Timeline of Governor Behavior:**

| Date | Event | DD% | Scale |
|------|-------|-----|-------|
| Jan 1 | Initialized | 0% | 100% |
| Mar 22 | STEP_DOWN | 5.9% | 100%→50% |
| Mar 22 | REGIME_OVERRIDE | - | 50%→100% |
| Apr 12 | STEP_DOWN | 6.3% | 100%→50% |
| Apr 12 | REGIME_OVERRIDE | - | 50%→100% |
| Apr 22 | STEP_DOWN | 6.6% | 100%→50% |
| Apr 26 | REGIME_OVERRIDE | - | 50%→100% |
| May 18 | STEP_DOWN | 6.0% | 100%→50% |
| **Jun 10** | **STEP_DOWN** | **14.7%** | **100%→0%** |
| Jun 10 | REGIME_OVERRIDE | - | 0%→50% |
| Jun 20 | STEP_DOWN | 21.5% | 50%→0% |
| Jul 4 | STEP_DOWN | 29.9% | 50%→0% |
| Jul 18 | STEP_DOWN | 32.5% | 50%→0% |
| ... | (Pattern continues) | ... | ... |
| Dec 27 | STEP_DOWN | 44.1% | 50%→0% |

**CRITICAL BUG IDENTIFIED:**

On June 10, the Governor jumped directly from 100% to 0%:
```
2017-06-10 09:25:00 DRAWDOWN_GOVERNOR: STEP_DOWN | DD=14.7% | Scale 100% → 0%
```

This should have been a gradual step-down (100%→75%→50%→25%→0%), not a direct jump.

**Death Spiral Pattern:**
1. Governor hits 0% due to drawdown
2. REGIME_OVERRIDE detects RISK_ON (score 73-75) for 5+ days
3. REGIME_OVERRIDE boosts Scale to 50%
4. New positions entered
5. Market pullback → positions lose value
6. GOVERNOR_SHUTDOWN liquidates at loss
7. Equity drops further
8. Governor steps back down to 0%
9. **Repeat from step 2**

**GOVERNOR_SHUTDOWN Liquidations:**
```
Jun 20: GOVERNOR_SHUTDOWN - QLD, options liquidated
Jul 4-6: GOVERNOR_SHUTDOWN - More liquidations
Jul 18: GOVERNOR_SHUTDOWN - QLD, FAS, options liquidated
Aug 1: GOVERNOR_SHUTDOWN - QLD, FAS, options liquidated
Aug 15: GOVERNOR_SHUTDOWN - QLD liquidated
... (15+ total shutdown events)
```

**Assessment:** 1/5 - Governor death spiral caused most of the losses.

### 4C. Other Safeguards

| Safeguard | Triggers | Status |
|-----------|----------|--------|
| WEEKLY_BREAKER | 1 (June 9) | Working |
| VOL_SHOCK | 1 (June 5) | Working |
| GAP_FILTER | 0 | N/A |
| PANIC_MODE | 0 | N/A |
| TIME_GUARD | Active | Working |
| FRIDAY_FIREWALL | Weekly | Working |

---

## 5. The June 9 Crash - Detailed Analysis

**The Single Worst Day:**

| Time | Event | Equity |
|------|-------|--------|
| 09:25 | Day start | $67,718 (HWM) |
| 09:33 | SOD baseline | $68,162 |
| 12:01 | KS Tier 1 triggered | $66,649 (-2.22%) |
| 12:38 | Weekly Breaker triggered | $63,639 (-5.33% WTD) |
| 15:45 | EOD | $56,153 |
| **Net** | **Single day loss** | **-$12,009 (-17.6%)** |

**What Positions Were Hit:**

Bull call spreads entered May 22-25:
- QQQ 170623C00137000/142000 × 28 contracts
- QQQ 170630C00138000/143000 × 15 contracts
- QQQ 170630C00139000/144000 × 20 contracts

These spreads were in-the-money but got crushed during a sharp tech selloff.

---

## 6. Funnel Analysis (Signal Loss)

```
┌─────────────────────────────────────────────────────────────┐
│ Stage 1: Regime Signals Generated                           │
│ └─→ 245+ days of RISK_ON (Score 70-75)                     │
├─────────────────────────────────────────────────────────────┤
│ Stage 2: Entry Signals Generated                            │
│ └─→ ~100 option spread signals                              │
│ └─→ ~50 trend entry signals                                 │
├─────────────────────────────────────────────────────────────┤
│ Stage 3: Signals BLOCKED                                    │
│ ├─→ ADX too weak: ~40 trend blocks                         │
│ ├─→ VASS rejection: ~200 option blocks                     │
│ ├─→ MARGIN_UTIL_GATE: ~30 blocks                           │
│ ├─→ Governor SHUTDOWN: 15+ forced closes                   │ ← LEAKAGE
│ └─→ Position limits: ~20 blocks                            │
├─────────────────────────────────────────────────────────────┤
│ Stage 4: Orders Submitted                                   │
│ └─→ 210 total orders                                        │
├─────────────────────────────────────────────────────────────┤
│ Stage 5: Orders Filled                                      │
│ └─→ ~200 fills (95%+ fill rate)                            │
└─────────────────────────────────────────────────────────────┘

BIGGEST LEAKAGE: Governor SHUTDOWN forced liquidations at losses
```

---

## 7. Scorecard

| System | Score | Status | Key Finding |
|--------|:-----:|--------|-------------|
| Trend Engine | 3/5 | Needs Tuning | ADX filter too restrictive in low-vol bull |
| Options Engine | 2/5 | Issues | VASS rejections + forced liquidations |
| MR Engine | N/A | Not Active | - |
| Hedge Engine | 5/5 | Working | Correctly stayed flat in RISK_ON |
| Kill Switch | 4/5 | Working | Triggered appropriately |
| **Drawdown Governor** | **1/5** | **BROKEN** | **Death spiral, direct 100%→0% jump** |
| Regime Detection | 5/5 | Working | V4.1 fix successful - RISK_ON achieved |
| Overnight Safety | 5/5 | Working | No violations |
| State Persistence | 5/5 | Working | All states saved correctly |
| **Overall** | **2/5** | **Failed** | **Governor killed a winning strategy** |

---

## 8. Root Cause Analysis

### Primary Issue: Drawdown Governor Death Spiral

The Drawdown Governor has a critical design flaw that creates a "death spiral" in volatile bull markets:

1. **Direct Jump Bug**: Scale jumped 100%→0% directly on June 10, bypassing intermediate steps
2. **REGIME_OVERRIDE Loop**: In RISK_ON markets, the override continuously re-enables trading
3. **No Cooldown After Losses**: Positions liquidated at losses don't trigger any cooldown
4. **HWM Ratchet Trap**: HWM of $67,718 never resets, making recovery mathematically difficult

### Secondary Issues

1. **VASS Criteria Too Strict**: Hundreds of "No contracts met spread criteria" in low-VIX environment
2. **Margin Utilization Cap**: 70% cap blocked new entries while bleeding positions continued losing
3. **ADX Filter**: Blocked 2 of 4 trend slots throughout the year

---

## 9. Recommendations

### P0 - CRITICAL (Must Fix Before Next Backtest)

#### P0-1: Fix Governor Step-Down Logic
**Issue:** Governor jumped directly from 100% to 0%
**Evidence:** `Scale 100% → 0%` on June 10
**Impact:** Immediate shutdown instead of gradual de-risking
**Fix:** Enforce step-down sequence: 100%→75%→50%→25%→0% with minimum 2-day hold at each level

```python
# In risk_engine.py - enforce gradual step-down
GOVERNOR_SCALE_STEPS = [100, 75, 50, 25, 0]
MIN_DAYS_PER_STEP = 2

def step_down_governor(self, current_dd):
    current_idx = GOVERNOR_SCALE_STEPS.index(self.governor_scale)
    # Only step down ONE level at a time
    if current_idx < len(GOVERNOR_SCALE_STEPS) - 1:
        self.governor_scale = GOVERNOR_SCALE_STEPS[current_idx + 1]
```

#### P0-2: Add Loss-Based Cooldown for REGIME_OVERRIDE
**Issue:** REGIME_OVERRIDE re-enabled trading immediately after losses
**Evidence:** 15+ GOVERNOR_SHUTDOWN events followed by immediate re-entry
**Impact:** Each override led to more losses
**Fix:** After GOVERNOR_SHUTDOWN liquidation, disable REGIME_OVERRIDE for 10 trading days

```python
# Add cooldown after forced liquidation
if liquidation_reason == "GOVERNOR_SHUTDOWN":
    self.regime_override_cooldown_until = self.algorithm.Time + timedelta(days=14)
```

### P1 - HIGH (Significant Performance Impact)

#### P1-1: Add HWM Reset After Extended Drawdown
**Issue:** HWM=$67,718 never reset, making recovery impossible
**Evidence:** DD stayed 30-45% for 6 months with no path to recovery
**Impact:** System trapped in perpetual drawdown state
**Fix:** Reset HWM to current equity after 60+ consecutive days at Scale≤25%

#### P1-2: Review VASS Spread Criteria for Low-VIX
**Issue:** Hundreds of VASS rejections when VIX<12
**Evidence:** "No contracts met spread criteria" repeated daily
**Impact:** Missed opportunities in favorable conditions
**Fix:** Relax delta/DTE requirements when VIX<12 (complacent market)

#### P1-3: Reduce Margin Utilization Cap
**Issue:** 70% cap blocked entries but allowed bleeding positions
**Evidence:** `MARGIN_UTIL_GATE: BLOCKED | Utilization=78.2%`
**Impact:** Could enter when margin was fine, blocked when it mattered
**Fix:** Consider dynamic cap based on position P&L, or lower to 60%

### P2 - MEDIUM (Optimization)

#### P2-1: ADX Threshold Review
**Issue:** ADX 10-11 blocked TNA/FAS entries all year
**Evidence:** `TNA entry blocked - ADX 10.7 too weak`
**Impact:** Only 2 of 4 trend slots used
**Fix:** Consider regime-aware ADX threshold (lower in sustained RISK_ON)

#### P2-2: Add Spread P&L Monitoring
**Issue:** Spreads held through large drawdowns before forced exit
**Evidence:** Spreads lost 40-50% before KS liquidation
**Impact:** Locked in maximum loss instead of partial
**Fix:** Add individual spread stop-loss at -30% of max loss

---

## 10. Conclusion

### What Worked
- **V4.1 VIX Level Fix: SUCCESS** - Regime correctly identified 2017 as RISK_ON
- Early performance was excellent: +35.4% peak return by June
- Hedge engine correctly stayed flat
- State persistence and basic safeguards worked

### What Failed
- **Drawdown Governor: CATASTROPHIC FAILURE** - Death spiral destroyed profits
- Options engine suffered from VASS rejections and forced liquidations
- ADX filter too restrictive in low-volatility environment

### Next Steps
1. Implement P0 fixes (Governor step-down, REGIME_OVERRIDE cooldown)
2. Re-run 2017 backtest to validate fixes
3. Run 2022 H1 backtest to test bear market behavior
4. Only proceed to live trading after both bull and bear scenarios pass

---

**Report Generated:** 2026-02-06
**Analyst:** Claude Code Audit Agent
**Version:** V4.1-VIXLevelFix-2017-Audit-v1.0
