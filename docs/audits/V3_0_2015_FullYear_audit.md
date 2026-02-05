# V3.0 2015 Full Year Audit Report

**Backtest Period:** 2015-01-01 to 2015-12-31
**Starting Capital:** $50,000
**Market Context:** CHOPPY/FLAT (S&P ~flat for year, Aug flash crash)
**Audit Date:** 2026-02-05
**Log File:** `docs/audits/logs/stage3/V3_0_2015_FullYear_Final_logs.txt`
**Trades File:** `docs/audits/logs/stage3/V3_0_2015_FullYear_Final_trades.csv`

---

## Executive Summary

| Metric | Old 2015 Result | V3.0 2015 Result | Change |
|--------|-----------------|------------------|--------|
| **Final Equity** | $37,918 | $42,390 | +$4,472 (+11.8%) |
| **Return** | -24.2% | -15.2% | +9.0 pp improvement |
| **Max Drawdown** | ~24%+ | -15.3% | Significantly improved |
| **Days at Governor 0%** | 180+ days | 300 days | **WORSE** - still stuck |
| **HWM Resets Completed** | 0 | 0 | No improvement |
| **REGIME_OVERRIDE Events** | 10+ attempts | 0 | **Feature not triggering** |

### Verdict: PARTIAL IMPROVEMENT - Death Spiral PERSISTS

V3.0 reduced losses from -24% to -15%, but **the fundamental death spiral problem was NOT solved**. The bot spent 300 days (82% of the year) locked at Governor 0%, unable to recover. The HWM reset mechanism attempted 33 times but never completed 10 consecutive positive days. The REGIME_OVERRIDE feature never triggered despite bullish regimes.

---

## 1. Performance Summary

### 1.1 Equity Curve Milestones
| Date | Equity | Event |
|------|--------|-------|
| 2015-01-01 | $50,000 | Start |
| 2015-01-07 | $50,029 | **HWM Set** (never exceeded) |
| 2015-01-22 | $46,930 | Governor STEP_DOWN to 50% (DD=6.2%) |
| 2015-03-11 | $43,919 | Governor STEP_DOWN to 0% (DD=12.2%) |
| 2015-08-24 | $43,701 | Flash Crash - TMF hedge entered |
| 2015-12-31 | $42,390 | **Final Equity** (DD=15.3%) |

### 1.2 Trading Activity
- **Total Fills:** 60 orders
- **Total Trades (from CSV):** 32 closed trades
- **Active Trading Days:** ~70 days (Jan-Mar at 50%+, plus sparse hedges)
- **Inactive Days at Governor 0%:** ~300 days

### 1.3 Win/Loss by Strategy (from trades CSV)
| Strategy | Wins | Losses | Net P&L |
|----------|------|--------|---------|
| Options | 5 | 23 | -$6,077 |
| Trend (QLD/SSO/FAS) | 3 | 4 | -$403 |
| Hedges (TMF) | 0 | 3 | -$805 |
| **Total** | **8** | **30** | **-$7,285** |

---

## 2. V3.0 Fix Verification

### 2.1 V3-4/V3-5: 3-Tier Governor (100%/50%/0%)

**Status: IMPLEMENTED CORRECTLY**

The logs confirm ONLY 3 tiers were used:
- Scale=100%: 2 occurrences (first 21 days)
- Scale=50%: 64 occurrences (~48 days)
- Scale=0%: 300 occurrences (~296 days)
- Scale=75%: 0 occurrences (old tier eliminated)
- Scale=25%: 0 occurrences (old tier eliminated)

**Governor State Transitions:**
1. Jan 22: 100% -> 50% (DD=6.2%)
2. Mar 11: 50% -> 0% (DD=12.2%)
3. No STEP_UP events occurred

### 2.2 V3-6: HWM Reset Mechanism

**Status: FAILED TO TRIGGER**

HWM Reset Counter Activity:
- Total reset attempts logged: 33
- Completed resets (10/10 days): **0**
- Best streak achieved: 2 consecutive days

**Why It Failed:**
The HWM reset requires 10 consecutive days of positive P&L while at Governor 50%+. However:
1. At Governor 50%, the bot was limited to half-sized positions
2. Small positions meant small daily P&L swings
3. Any negative day (even -$3) reset the counter to 0
4. Market chop prevented 10 consecutive green days

**Sample HWM_RESET Logs:**
```
2015-01-27 HWM_RESET: Day 1/10 | P&L=+$1 | Scale=50%
2015-01-28 HWM_RESET: Day 2/10 | P&L=+$399 | Scale=50%
2015-01-29 HWM_RESET: Counter reset | P&L=$-35 (negative)
```

**Critical Failure:** Once Governor hit 0% on March 11, HWM_RESET was disabled entirely:
```
2015-03-11 HWM_RESET: Counter reset | Scale=0% < 50%
```

### 2.3 V3-3: VIX Direction in Daily Regime

**Status: IMPLEMENTED CORRECTLY**

VIX Direction (VD) is actively used in regime calculation:
```
2015-01-02 REGIME: Score=53.8 | T=85 VIX=70 VD=50 RV=85 B=55 C=55 ADX=100
2015-08-24 REGIME: Score=49.3 | T=50 VIX=30 VD=25 RV=35 B=55 C=45 ADX=30
```

VD values ranged from 25 (VIX spiking) to 75 (VIX falling fast), showing directional awareness.

### 2.4 V3-1/V3-2: EOD_LOCK Exemptions (Hedges/PUTs at Governor 0%)

**Status: PARTIALLY WORKING**

**Evidence of Hedge Exemption:**
```
2015-08-24 06:30:00 OPTIONS_EOD: Bearish PUT spread allowed at Governor 0%
2015-08-24 06:30:00 HEDGE: TMF_SIGNAL | Regime=49.3, TMF target=10%
2015-08-24 06:30:00 EOD_GOVERNOR_0: Processing defensive signals only (hedges + PUTs)
2015-08-24 09:31:00 FILL: BUY 24.0 TMF @ $190.70
```

**The Problem:** Hedges were entered but then immediately liquidated the next morning:
```
2015-08-25 09:25:00 GOVERNOR: SHUTDOWN — Liquidating all positions
2015-08-25 09:25:00 GOVERNOR_SHUTDOWN: Closed equity TMF x24.0
2015-08-25 09:31:00 FILL: SELL 24.0 TMF @ $173.37
```

This "whipsaw" pattern occurred 3 times:
1. Aug 24-25: TMF bought at $190.70, sold next day at $173.37 (-$415 loss)
2. Sep 8-9: TMF bought at $161.18, sold next day at $154.32 (-$171 loss)
3. Dec 14-15: TMF bought at $168.39, sold next day at $159.67 (-$218 loss)

**Root Cause:** The Governor SHUTDOWN logic liquidates ALL positions at 09:25 each morning when DD > 10%. This overrides the EOD hedge exemption, creating a destructive 1-day holding pattern.

### 2.5 Governor REGIME_OVERRIDE

**Status: NEVER TRIGGERED**

Despite config enabling REGIME_OVERRIDE:
```python
GOVERNOR_REGIME_OVERRIDE_ENABLED = True
GOVERNOR_REGIME_OVERRIDE_THRESHOLD = 70  # Regime >= 70 for 5 days
GOVERNOR_REGIME_OVERRIDE_DAYS = 5
```

**Zero REGIME_OVERRIDE events occurred in 2015.**

**Why It Failed:**
1. The regime score rarely exceeded 70 during the choppy year
2. When regime was briefly >70, it didn't sustain for 5 consecutive days
3. Most regime scores were in the 55-68 range (NEUTRAL), not RISK_ON (>70)

**Sample Regime Scores:**
| Period | Regime Range | Classification |
|--------|--------------|----------------|
| Jan-Feb | 53-67 | NEUTRAL |
| Mar-Jul | 48-63 | CAUTIOUS/NEUTRAL |
| Aug (crash) | 44-49 | CAUTIOUS |
| Sep-Dec | 48-65 | CAUTIOUS/NEUTRAL |

---

## 3. Death Spiral Analysis

### 3.1 Timeline of the Death Spiral

| Date | Event | Equity | Governor |
|------|-------|--------|----------|
| Jan 7 | HWM set | $50,029 | 100% |
| Jan 21 | First big option loss (-$1,880 on P00101000) | ~$47,000 | 100% |
| Jan 22 | Governor STEP_DOWN | $46,930 | 50% |
| Feb-Mar | Struggling at 50%, small options losses | $45,000-46,000 | 50% |
| Mar 11 | Governor STEP_DOWN | $43,919 | **0%** |
| Mar-Jul | **ZERO trading activity** | $43,701 | 0% |
| Aug 24 | Flash crash, TMF hedge entered | $43,715 | 0% |
| Aug 25 | TMF liquidated by morning SHUTDOWN | $43,283 | 0% |
| Sep-Nov | **ZERO trading activity** | $43,110 | 0% |
| Dec 14-15 | TMF hedge, immediately liquidated | $42,390 | 0% |
| Dec 31 | End of year | **$42,390** | 0% |

### 3.2 Why the Bot Stayed Stuck

1. **HWM Never Reset:** $50,029 from Jan 7 persisted all year. Current equity of $43,701 was always 12-15% below this stale peak.

2. **Governor 0% = No Recovery Path:** At 0%, the bot could only enter hedges, which got liquidated the next morning. There was no mechanism to grow equity back.

3. **Regime Never Reached 70+:** The REGIME_OVERRIDE threshold of 70 was never sustained for 5 days in choppy 2015.

4. **Small Wins Couldn't Sustain 10 Days:** The HWM reset required 10 consecutive positive days at 50%+ scale, but the bot was at 0%.

### 3.3 The Hedge Whipsaw Bug

The most damaging pattern was the hedge entry/liquidation cycle:

```
Day 1 EOD:  HEDGE: TMF_SIGNAL | target=10%
Day 1 EOD:  EOD_GOVERNOR_0: Processing defensive signals only
Day 1 MOO:  FILL: BUY 24.0 TMF @ $190.70
Day 2 9:25: GOVERNOR: SHUTDOWN — Liquidating all positions
Day 2 9:31: FILL: SELL 24.0 TMF @ $173.37  <- LOSS
```

Each hedge cycle lost money because:
1. Bought at MOO price
2. Held overnight (normal market movement)
3. Sold next morning at SHUTDOWN (usually at a loss)

---

## 4. V3.0 Fix Effectiveness Scorecard

| Fix | Target Problem | Implemented? | Working? | Evidence |
|-----|----------------|--------------|----------|----------|
| V3-1/V3-2: EOD_LOCK Exemption | Hedges blocked at Gov 0% | YES | PARTIAL | Hedges entered but liquidated next AM |
| V3-3: VIX Direction | Crash detection lag | YES | YES | VD values visible in regime logs |
| V3-4/V3-5: 3-Tier Governor | 75%/25% limbo states | YES | YES | Only 100/50/0 scales used |
| V3-6: HWM Reset | Stale HWM locks bot | YES | NO | 0 successful resets (needed 10 days) |
| REGIME_OVERRIDE | Trust bullish regime | YES | NO | Never triggered (regime < 70) |

---

## 5. Comparison: Old 2015 vs V3.0 2015

| Metric | Old 2015 | V3.0 2015 | Verdict |
|--------|----------|-----------|---------|
| Final Equity | $37,918 | $42,390 | **+$4,472 better** |
| Return | -24.2% | -15.2% | **+9 pp better** |
| Max DD | ~24% | ~15.3% | **Better** |
| Days at Gov 0% | 180+ | 300 | **Worse** |
| HWM Resets | 0 | 0 | Same |
| Death Spiral | YES | YES | **NOT FIXED** |

### Why V3.0 Lost Less Despite Same Death Spiral

1. **Earlier Shutdown:** V3.0's 0% trigger at -10% (vs old -15%) stopped losses sooner
2. **Hedge Exemption:** Even though hedges were liquidated next day, the system tried to hedge
3. **Cleaner Exit:** 3-tier system avoided the churning of 75%/25% states

---

## 6. Critical Bugs Identified

### Bug 1: Governor Morning SHUTDOWN Liquidates Hedges

**Severity:** HIGH
**Location:** `main.py` or `risk_engine.py` - Morning SHUTDOWN routine

The 09:25 SHUTDOWN logic liquidates ALL positions including hedges that were explicitly allowed at Governor 0%. This creates a destructive 1-day holding pattern that turns hedges into guaranteed losses.

**Evidence:**
```
2015-08-24 06:30:00 EOD_GOVERNOR_0: Processing defensive signals only (hedges + PUTs)
2015-08-25 09:25:00 GOVERNOR_SHUTDOWN: Closed equity TMF x24.0
```

**Fix Required:** Exempt TMF/PSQ from GOVERNOR_SHUTDOWN when Governor is at 0%.

### Bug 2: HWM Reset Requires Scale >= 50% (Impossible at 0%)

**Severity:** HIGH
**Location:** `config.py` - GOVERNOR_HWM_RESET_MIN_SCALE = 0.50

Once Governor hits 0%, HWM reset is disabled:
```
2015-03-11 HWM_RESET: Counter reset | Scale=0% < 50%
```

This means there's NO path to reset the HWM when deeply underwater.

**Fix Required:** Add alternative recovery mechanism at Governor 0%, such as:
- Time-based HWM decay (reduce HWM by 1% per month)
- Recovery threshold at 0% (if equity gains 5% from recent low, step up to 50%)

### Bug 3: REGIME_OVERRIDE Threshold Too High for Choppy Markets

**Severity:** MEDIUM
**Location:** `config.py` - GOVERNOR_REGIME_OVERRIDE_THRESHOLD = 70

In choppy years like 2015, the regime rarely exceeds 70. The override never triggered.

**Fix Required:** Consider:
- Lower threshold to 65 for override
- Or use 5-day SMA of regime instead of consecutive days
- Or add a time-based override (after 60 days at 0%, force step up to 50%)

---

## 7. Recommendations

### Immediate Fixes (P0)

1. **Exempt Hedges from Morning SHUTDOWN**
   - TMF/PSQ should not be liquidated at 09:25 when Governor is at 0%
   - Hedges are explicitly allowed; don't contradict this

2. **Add 0% Recovery Path**
   - Option A: Time-based HWM decay (HWM -= 1% per 30 days at Gov 0%)
   - Option B: Equity recovery threshold (if equity gains 5% from floor, step up)
   - Option C: Force 10% size at Gov 0% for hedges + bearish options

### Medium-Term Fixes (P1)

3. **Lower REGIME_OVERRIDE Threshold**
   - Reduce from 70 to 65, or
   - Use 5-day rolling average instead of consecutive

4. **Add Emergency Time-Based Override**
   - After 60 days at Governor 0%, force step up to 50%
   - Rationale: Any regime is better than being frozen indefinitely

### Testing Recommendations

5. **Re-test 2015 After Fixes**
   - The hedge liquidation bug alone could add back $800+
   - A working 0% recovery path could save $3,000+

---

## 8. Conclusion

V3.0 improved 2015 results from -24% to -15%, primarily by:
- Triggering Governor 0% earlier (at -10% vs -15%)
- Eliminating the 75%/25% churning states
- Attempting to hedge at Gov 0%

However, **the death spiral problem persists**. The bot spent 300 days (82% of the year) frozen at Governor 0% with no path to recovery. The V3.0 fixes were implemented but have critical gaps:

1. Hedges are allowed at EOD but liquidated at morning SHUTDOWN
2. HWM reset requires Scale >= 50%, impossible at Governor 0%
3. REGIME_OVERRIDE never triggered due to threshold too high for choppy markets

**Final Verdict:** V3.0 is a meaningful improvement but does not fully solve the 2015 death spiral. Additional fixes are required to create a viable recovery path from Governor 0%.

---

## Appendix A: Key Log Excerpts

### A.1 Governor STEP_DOWN Events
```
2015-01-22 09:25:00 DRAWDOWN_GOVERNOR: STEP_DOWN | DD=6.2% | Scale 100% → 50% | HWM=$50,029 | Current=$46,930
2015-03-11 09:25:00 DRAWDOWN_GOVERNOR: STEP_DOWN | DD=12.2% | Scale 50% → 0% | HWM=$50,029 | Current=$43,919
```

### A.2 Hedge Whipsaw Pattern
```
2015-08-24 06:30:00 HEDGE: TMF_SIGNAL | Regime=49.3, TMF target=10%, current=0%, tier=LIGHT
2015-08-24 06:30:00 EOD_GOVERNOR_0: Processing defensive signals only (hedges + PUTs)
2015-08-24 09:31:00 FILL: BUY 24.0 TMF @ $190.70
2015-08-25 09:25:00 GOVERNOR: SHUTDOWN — Liquidating all positions
2015-08-25 09:25:00 GOVERNOR_SHUTDOWN: Closed equity TMF x24.0
2015-08-25 09:31:00 FILL: SELL 24.0 TMF @ $173.37
```

### A.3 Final Day
```
2015-12-31 09:25:00 DRAWDOWN_GOVERNOR: DD=15.3% | Scale=0% | HWM=$50,029 | Current=$42,390
2015-12-31 16:00:00 EOD_GOVERNOR_0: Processing defensive signals only (hedges + PUTs)
2015-12-31 15:45:00 CAPITAL: EOD CapitalState(Total=$42,390 | Locked=$0 | Tradeable=$42,390)
```
