# V4.0 Regime Model - 2017 Bull Market Audit Report

**Backtest Period:** January 1, 2017 - December 29, 2017
**Starting Capital:** $50,000
**Final Equity:** $9,859
**Net Return:** -80.3%
**Market Context:** Strong Bull Market (SPY +19% in 2017)
**Audit Date:** 2026-02-06

---

## Executive Summary

**CRITICAL FAILURE:** The V4.0 algorithm lost 80.3% in a year where the market gained 19%. This is a catastrophic failure that requires immediate investigation and fixes before any live deployment.

### Key Findings

1. **Drawdown Governor Death Spiral**: The algorithm peaked at $75,645 on March 14, 2017 (+51% gain), then collapsed to under $10K. The Drawdown Governor triggered at 5.2% drawdown and never recovered, keeping Scale=0% for 280+ days.

2. **SPIKE_CAP False Triggers**: The V4.0 SPIKE_CAP triggered 42 times in a low-VIX bull market (VIX averaged 11-14 throughout 2017), capping regime scores unnecessarily.

3. **Margin Liquidation Cascade**: On March 14, 2017, a margin call triggered forced liquidation of 16 positions (7 short options + 7 long options + 2 equities), crystallizing losses.

4. **Governor Blocking All Recovery**: After March 22, 2017, the Governor Scale remained at 0%, preventing ALL trend and options entries for the rest of the year.

---

## Section 1: Performance Summary

| Metric | Value |
|--------|-------|
| Starting Capital | $50,000 |
| High Water Mark | $75,645 (Mar 14, 2017) |
| Final Equity | $9,859 |
| Net Return | -80.3% |
| Max Drawdown | 86.2% |
| Total Trades | ~420 |
| GOVERNOR_SHUTDOWN Events | 319 |
| MARGIN_CB_LIQUIDATE Events | 38 |
| Kill Switch Triggers (TREND_EXIT) | 3 |
| SPIKE_CAP Activations | 42 |

### Monthly Equity Progression

| Month | Start Equity | End Equity | Return |
|-------|-------------|------------|--------|
| Jan 2017 | $50,000 | ~$57,000 | +14.0% |
| Feb 2017 | ~$57,000 | ~$71,000 | +24.6% |
| **Mar 2017** | ~$71,000 | ~$65,000 | **-8.5%** |
| Apr 2017 | ~$65,000 | ~$51,000 | -21.5% |
| May 2017 | ~$51,000 | ~$43,000 | -15.7% |
| Jun 2017 | ~$43,000 | ~$36,000 | -16.3% |
| Jul-Dec | Continued decline | $9,859 | N/A |

---

## Section 2: Engine-by-Engine Breakdown

### 2A. Trend Engine (QLD/SSO/TNA/FAS)

**Performance: 1/5 - CRITICAL FAILURE**

| Metric | Value |
|--------|-------|
| Total Entries | ~15 |
| Successful MOO Fills | ~6 |
| Positions Liquidated by Margin CB | 2 (QLD, SSO on Mar 14) |
| Stale MOO Cleared | 280+ (blocked by Governor) |

**Key Issue:** After March 22, 2017, every single trend entry signal was blocked:
```
TREND: ENTRY_SIGNAL SSO | MA200+ADX Entry: Close=13.16 > MA200=11.18, ADX=30.3
TREND: ENTRY_APPROVED SSO | ADX=30.3 | Slot 1/2
ROUTER: RECEIVED | SSO | Weight=12.0% | Source=TREND | Urgency=MOC
...
EOD_GOVERNOR_0: Processing defensive signals only (hedges + PUTs)
GOVERNOR: SHUTDOWN | All non-hedge allocations zeroed
```

The Trend Engine generated valid signals with ADX scores of 25-30 (STRONG momentum), but EVERY SINGLE ONE was blocked by the Drawdown Governor from March 22 to December 31 - a period of 280 trading days.

### 2B. Options Engine (QQQ Spreads)

**Performance: 1/5 - CRITICAL FAILURE**

| Metric | Value |
|--------|-------|
| Total Spread Entries | ~400+ |
| Margin Liquidations | 38 events |
| VASS Rejections | Thousands |
| Net P&L | Severe Loss |

**Key Issues:**

1. **Margin Overflow**: The algorithm accumulated too many spread positions, leading to margin calls:
   ```
   2017-03-14 14:00:00 Order Error: Insufficient buying power
   2017-03-14 14:00:00 MARGIN_CB_LIQUIDATE: 5 consecutive margin calls | Force closing all options positions
   ```

2. **Spread Accumulation Without Exit**: The algorithm entered new spreads daily without waiting for existing positions to close, leading to position pileup.

3. **Governor-Forced Daily Liquidation**: After March 22, the pattern became:
   - 15:45: Enter spread
   - 09:25 next day: Governor liquidates spread
   - 15:45: Enter new spread
   - Repeat (each cycle losing to bid-ask spread)

**Sample Trades CSV showing systematic losses:**
```
2017-04-03: BULL_CALL x15 | Entry: $3.84 Long / $0.96 Short | Exit: $3.12 / $1.08 | P&L: -$1,260
2017-04-04: BULL_CALL x14 | Entry: $3.90 Long / $0.99 Short | Exit: $3.94 / $1.07 | P&L: -$56
2017-04-05: BULL_CALL x13 | Entry: $4.46 Long / $1.28 Short | Exit: $4.33 / $1.38 | P&L: -$299
```

### 2C. Mean Reversion Engine (TQQQ/SOXL)

**Performance: 3/5 - INACTIVE**

| Metric | Value |
|--------|-------|
| MR Entries | 0 |
| Overnight Holds | 0 |

**Finding:** The MR Engine was correctly inactive throughout 2017 because:
1. RSI rarely dropped below 25 in the strong bull market
2. The regime was stuck in NEUTRAL (50-65), not triggering MR conditions

This is actually correct behavior - MR is designed for oversold conditions.

### 2D. Hedge Engine (TMF/PSQ)

**Performance: 4/5 - CORRECTLY DORMANT**

| Metric | Value |
|--------|-------|
| TMF Entries | 2 |
| PSQ Entries | 0 |

**Finding:** The Hedge Engine correctly stayed dormant because:
- Regime Score ranged 50-65 (NEUTRAL) throughout 2017
- Hedge threshold is regime < 50
- TMF only entered briefly during SPIKE_CAP periods (Apr 17, May 22)

This is correct behavior for a bull market.

---

## Section 3: Risk & Safeguard Verification

### 3A. Kill Switch

**Performance: 3/5 - TRIGGERED APPROPRIATELY**

| Tier | Triggers | Description |
|------|----------|-------------|
| REDUCE (Tier 1) | Many | 2% daily loss - sizing to 50% |
| TREND_EXIT (Tier 2) | 3 | Jun 19, Nov 27, Dec 27 |
| FULL_LIQUIDATE (Tier 3) | 0 | Never reached 5% daily loss |

The Kill Switch triggered appropriately but couldn't prevent losses because the positions were already liquidated by the Drawdown Governor.

### 3B. Drawdown Governor - THE PRIMARY FAILURE

**Performance: 0/5 - CATASTROPHIC DEATH SPIRAL**

**Timeline of Governor State:**

| Date | DD% | Scale | Equity | Status |
|------|-----|-------|--------|--------|
| Mar 14 | 0% | 100% | $75,645 | HWM SET |
| Mar 17 | 5.2% | 50% | $71,714 | First trigger |
| Mar 22 | 14.5% | 0% | $64,666 | **SHUTDOWN** |
| Mar 23 | 10.1% | 0% | $68,008 | Stuck |
| Apr 1+ | 10-20% | 0% | Declining | Stuck |
| Dec 22 | 85.8% | 0% | $10,746 | Still stuck |
| Dec 29 | 86.2% | 0% | $9,859 | **FINAL** |

**THE BUG:** The Drawdown Governor's recovery mechanism requires:
```python
EQUITY_RECOVERY: Recovery=0.0% < 3% needed
```

Once DD exceeds ~15%, the algorithm needs a 3% RECOVERY to exit 0% scale. But at 0% scale, it can only trade hedges, which don't generate returns in a bull market. This creates an **unrecoverable death spiral**.

**Evidence from logs:**
```
2017-12-22 09:25:00 DRAWDOWN_GOVERNOR: DD=85.8% | Scale=0% | HWM=$75,645 | Current=$10,746
2017-12-22 09:25:00 EQUITY_RECOVERY: Day 276 at 0% | Recovery=0.0% < 3% needed | Trough=$10,746
2017-12-22 09:25:00 GOVERNOR: SHUTDOWN - Liquidating non-hedge positions
```

For 276+ days, the algorithm:
1. Tried to enter positions each day at 15:45
2. Liquidated them each morning at 09:25
3. Lost money on bid-ask spread
4. Never recovered because it couldn't hold positions

---

## Section 4: V4.0 Regime Analysis (CRITICAL)

### Regime Score Distribution (2017)

| Range | Classification | Days | % |
|-------|---------------|------|---|
| 70+ | RISK_ON | 0 | 0% |
| 50-69 | NEUTRAL | ~250 | ~100% |
| 40-49 | CAUTIOUS (SPIKE_CAP) | ~12 | ~5% |
| 30-39 | DEFENSIVE | 0 | 0% |
| <30 | RISK_OFF | 0 | 0% |

**Key Finding:** The V4.0 regime model NEVER detected RISK_ON in 2017, a year when SPY gained 19%. The highest daily score was ~64. This is a significant calibration failure.

### SPIKE_CAP Analysis

**42 SPIKE_CAP activations** found, including:

| Date | VIX Level | SPIKE_CAP Score | V4.0 Raw Score |
|------|-----------|-----------------|----------------|
| Mar 24 | ~12 | 56.8 | Would be 62+ |
| Mar 27 | ~12 | 50.8 | Would be 62+ |
| Apr 12 | ~12 | 56.3 | Would be 64+ |
| Apr 13 | ~12 | 52.9 | Would be 65+ |
| Aug 11 | ~15 | 56.6 | Would be 65+ |
| Nov 13 | ~10 | 54.4 | Would be 63+ |

**THE BUG:** SPIKE_CAP was triggering with VIX at 10-12 (historically LOW). The V4_SPIKE_CAP_VIX_MIN_LEVEL = 15.0 setting was supposed to prevent this, but VIX direction spikes (even small ones like +15-20%) were still capping scores.

In 2017's low-VIX environment, even small percentage moves in VIX (e.g., from 10 to 12) registered as "spikes" because:
- VIX_DIRECTION_SPIKE_THRESHOLD = 0.25 (25% change in 5 days)
- Going from VIX 10 to 12.5 is a 25% increase, triggering SPIKE_CAP
- But VIX 12.5 is historically VERY LOW and not a crisis signal

---

## Section 5: Root Cause Analysis

### Primary Failure Chain

```
1. V4.0 Regime stuck in NEUTRAL (50-65) instead of RISK_ON (70+)
   |
   v
2. Options Engine accumulated too many positions (margin overflow)
   |
   v
3. March 14: Margin call liquidated all positions at unfavorable prices
   |
   v
4. Drawdown Governor triggered at 5.2% DD, then 14.5% DD = Scale 0%
   |
   v
5. Governor blocked ALL entries for 280 days
   |
   v
6. Daily pattern: Enter at 15:45, Liquidate at 09:25 (lose bid-ask each day)
   |
   v
7. Equity eroded from $65K to $9.8K over 9 months
```

### Secondary Contributing Factors

1. **No Governor Recovery Path**: At 0% scale, only hedges allowed. Hedges don't profit in bull markets. No way to recover.

2. **Spread Position Limits Missing**: The algorithm entered new spreads without checking total open positions, leading to margin overflow.

3. **SPIKE_CAP Calibration**: The VIX spike detection was too sensitive for a low-VIX environment.

---

## Section 6: Optimization Recommendations

### P0 - CRITICAL (Must Fix Before Any Live Trading)

#### P0.1: Drawdown Governor Recovery Mechanism

**Problem:** At Scale=0%, algorithm cannot recover because it can only hold hedges.

**Solution:** Implement "Recovery Mode" where Scale=0% allows:
- Minimum 1 trend position at 25% normal size
- Minimum 1 spread position at 25% normal size
- OR: Time-based recovery (after N days at 0%, auto-reset to 25%)

```python
# Proposed fix in config.py
GOVERNOR_RECOVERY_ENABLED = True
GOVERNOR_RECOVERY_DAYS = 30  # After 30 days at 0%, reset to 25%
GOVERNOR_RECOVERY_MIN_SCALE = 0.25  # Allow 25% sizing in recovery mode
```

#### P0.2: Options Position Limits

**Problem:** Accumulated too many spreads, causing margin overflow.

**Solution:** Implement strict position limits:
```python
MAX_CONCURRENT_SPREADS = 3  # Never more than 3 open spreads
SPREAD_MARGIN_RESERVE_PCT = 0.30  # Reserve 30% margin for existing positions
```

### P1 - HIGH (Should Fix Before Production)

#### P1.1: SPIKE_CAP VIX Minimum Enhancement

**Problem:** SPIKE_CAP triggering at VIX 10-12 (low volatility).

**Solution:** Raise the VIX floor for spike detection:
```python
V4_SPIKE_CAP_VIX_MIN_LEVEL = 18.0  # Raised from 15.0 - only trigger in elevated VIX
V4_SPIKE_CAP_THRESHOLD = 0.30  # Raised from 0.25 - require 30% VIX increase
```

#### P1.2: Regime Score Calibration for Bull Markets

**Problem:** Regime never exceeded 64 in a +19% bull year.

**Solution:** Recalibrate V4.0 factors:
- Reduce WEIGHT_VIX_DIRECTION_V4 from 0.25 to 0.15 in low-VIX environments
- Increase WEIGHT_MOMENTUM_V4 from 0.30 to 0.35

### P2 - MEDIUM (Quality Improvements)

#### P2.1: Add Governor Scale Logging

Add daily log of Governor state for easier debugging:
```
GOVERNOR_STATUS: Day=276 | Scale=0% | Recovery=0.0%/3.0% needed | Entries blocked
```

#### P2.2: Add Spread Position Count to EOD Log

```
SPREAD_STATUS: Open=5 | Max=3 | Margin used=85%
```

---

## Section 7: Scorecard

| System | Score | Status | Key Finding |
|--------|:-----:|--------|-------------|
| Trend Engine | 2/5 | FAILED | Valid signals blocked by Governor for 280 days |
| Options Engine | 1/5 | CRITICAL | Position pileup caused margin cascade |
| MR Engine | 4/5 | OK | Correctly inactive in bull market |
| Hedge Engine | 4/5 | OK | Correctly dormant when regime > 50 |
| Kill Switch | 3/5 | PARTIAL | Triggered but couldn't prevent losses |
| Drawdown Governor | 0/5 | CRITICAL | Death spiral - no recovery mechanism |
| V4.0 Regime Detection | 2/5 | FAILED | Never detected RISK_ON in +19% year |
| **Overall** | **1/5** | **CRITICAL** | -80% loss in +19% bull market |

---

## Section 8: Conclusions

### What Went Wrong

1. **The Drawdown Governor has a fatal flaw**: Once DD exceeds ~15%, Scale=0% traps the algorithm in a death spiral with no escape mechanism.

2. **V4.0 Regime Model is too conservative**: It classified a +19% bull year as perpetually "NEUTRAL" (50-65), never reaching "RISK_ON" (70+).

3. **SPIKE_CAP is miscalibrated**: It fires on low-VIX environments where VIX going from 10 to 12 is normal noise, not a crisis signal.

4. **Options position management is inadequate**: No hard limits on concurrent spreads allowed margin overflow.

### What Worked

1. Hedge Engine correctly stayed dormant
2. MR Engine correctly stayed inactive
3. Kill Switch triggered at appropriate levels
4. The algorithm DID make 51% gains in 2.5 months before the crash

### Path Forward

Before any live deployment:
1. **P0.1** - Fix Governor death spiral (MANDATORY)
2. **P0.2** - Add spread position limits (MANDATORY)
3. **P1.1** - Recalibrate SPIKE_CAP for low-VIX environments
4. **P1.2** - Recalibrate regime to detect RISK_ON in bull markets
5. Re-run 2017 backtest to verify fixes

---

## Appendix: Key Log Excerpts

### A1: March 14, 2017 - The Turning Point

```
2017-03-14 09:25:00 HWM_RESET: Day 1/10 | P&L=+$1,333 | Scale=100%
2017-03-14 09:31:00 KS_GRADUATED: NONE -> REDUCE | Loss=2.83%
2017-03-14 10:46:00 WEEKLY_BREAKER: TRIGGERED | WTD loss=5.01%
2017-03-14 14:00:00 MARGIN_CB_LIQUIDATE: 5 consecutive margin calls
2017-03-14 14:00:00 MARGIN_CB_LIQUIDATE: Liquidation complete | 16 positions
```

### A2: March 22, 2017 - Governor Death Spiral Begins

```
2017-03-22 09:25:00 DRAWDOWN_GOVERNOR: DD=14.5% | Scale=0% | HWM=$75,645
2017-03-22 09:25:00 HWM_RESET: Counter reset | Scale=0% < 50%
2017-03-22 09:25:00 GOVERNOR: SHUTDOWN - Liquidating non-hedge positions
```

### A3: December 22, 2017 - Still Trapped (Day 276)

```
2017-12-22 09:25:00 DRAWDOWN_GOVERNOR: DD=85.8% | Scale=0% | HWM=$75,645
2017-12-22 09:25:00 EQUITY_RECOVERY: Day 276 at 0% | Recovery=0.0% < 3% needed
2017-12-22 09:25:00 GOVERNOR: SHUTDOWN - Liquidating non-hedge positions
```

---

*Report generated by Claude Code audit system*
