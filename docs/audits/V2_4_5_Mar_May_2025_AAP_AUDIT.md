# V2.4.5 Algorithmic Audit Protocol (AAP) Report

**Backtest:** V2.4.5-Mar-May-2025
**Period:** Mar 1 - May 30, 2025 (3 months)
**Starting Capital:** $50,000
**Final Capital:** $47,033
**Return:** **-5.9%**
**URL:** [Pending QC link]

---

## Executive Summary

| Metric | Value | Status |
|--------|-------|:------:|
| Total Return | -5.9% | ⚠️ Loss |
| Kill Switch Triggers | 1 | ✅ Working |
| CB Level 1 Triggers | 1 | ✅ Working |
| Weekly Breaker Triggers | 1 | ✅ Working |
| Vol Shock Triggers | 80+ | ✅ Working |
| Margin Call Orders | 0 | ✅ CLEAN |
| Invalid Orders | 0 | ✅ CLEAN |
| Critical Errors | 0 | ✅ CLEAN |

**VERDICT:** System safeguards working correctly. Loss of -5.9% was primarily from a bull call spread that moved against the position, triggering kill switch. Mar 2025 was a volatile period (multiple vol shocks).

---

## Phase 1: Three-Way Match (Funnel Analysis)

| Funnel Stage | Metric | Count | Status |
|--------------|--------|:-----:|:------:|
| 1. Signal Generation | ENTRY_SIGNAL lines | 4 | ✅ |
| 2. Router Processing | Orders Submitted | 30 | ✅ |
| 3. Execution | Trades Filled | 14 | ✅ |

**Diagnosis:** ✅ **PASS** - All signals were processed and executed. No router blockages or broker rejections.

---

## Phase 2: Engine-by-Engine Analysis

### 1. TREND ENGINE

| Metric | Value |
|--------|-------|
| Trend P&L | **-$679.76** |
| Trades | 5 |
| Entry Signals | 2 (FAS only) |
| Entries Blocked (ADX) | ~60+ |
| Wins | 2 |
| Losses | 3 |

#### Trade Summary

| Symbol | Entry Date | Exit Date | P&L | Exit Reason |
|--------|------------|-----------|-----|-------------|
| SSO | Mar 3 | Mar 4 | -$704.75 | SMA50_BREAK |
| FAS | Mar 17 | Mar 18 | +$188.98 | SMA50_BREAK |
| FAS | Mar 19 | Mar 20 | +$32.38 | SMA50_BREAK |
| QLD | Mar 27 | Mar 28 | -$196.37 | SMA50_BREAK |

#### Analysis

**ADX Filter Very Strict:**
- QLD: Blocked most days (ADX 17-20, always < 25)
- SSO: Blocked most days (ADX 16-20, always < 25)
- FAS: Only symbol passing ADX threshold (ADX 25.3-25.4)

**SMA50 Exit Pattern:**
- All 5 trades exited via SMA50_BREAK
- Choppy market causing quick exits (1-day holds)
- Mar 2025 characterized by price oscillation around SMA50

**Cold Start Entry:**
- Mar 2: SSO entered via warm entry (COLD_START) at 25% weight
- Mar 27: QLD entered via warm entry after kill switch reset

---

### 2. OPTIONS ENGINE - SWING MODE (Spreads)

| Metric | Value |
|--------|-------|
| Spread P&L | **-$2,938** |
| Trades | 4 (2 spreads × 2 entries) |
| Entry Signals | 2 |
| Kill Switch Triggers | 1 |

#### Spread Trades

| Date | Type | Long/Short | Contracts | P&L |
|------|------|------------|:---------:|-----|
| Mar 25 14:10 | BULL_CALL | $488/$493 | 30 | -$1,530 |
| Mar 26 10:00 | BULL_CALL | $488/$493 | 32 | -$1,408 |

#### Analysis

**Mar 25-26 Bull Call Spread:**
```
ENTRY: Mar 25 @ 14:10
  Long $488 Call @ $12.08
  Short $493 Call @ $8.93
  Net Debit: $3.15 × 30 = $9,450

ENTRY #2: Mar 26 @ 10:00
  Long $488 Call @ $10.48
  Short $493 Call @ $7.40
  Net Debit: $3.08 × 32 = $9,856

KILL SWITCH: Mar 26 @ 10:21
  Portfolio loss: -5.43%
  Closed both spreads
  Long $488 @ $9.59
  Short $493 @ $6.95
  Net Exit: $2.64
  Loss: ~$0.50/spread × 62 contracts × 100 = -$3,100
```

**Observation:** Market moved against the bull call spread rapidly. Kill switch correctly triggered at -5.43% and liquidated all positions.

---

### 3. OPTIONS ENGINE - INTRADAY MODE (Micro Regime)

| Metric | Value |
|--------|-------|
| Intraday P&L | **+$867** |
| Trades | 5 |
| Wins | 2 |
| Losses | 3 |
| Strategies Used | ITM_MOMENTUM, DEBIT_FADE |

#### Intraday Trades

| Date | Time | Strategy | Strike | Contracts | P&L |
|------|------|----------|--------|:---------:|----:|
| Mar 12 | 10:45 | ITM_MOM | $469P | 6 | -$234 |
| Mar 12 | 11:00 | ITM_MOM | $468P | 6 | -$210 |
| Mar 19 | 11:45 | DEB_FADE | $475P | 7 | -$217 |
| Mar 20 | 11:30 | DEB_FADE | $479P | 9 | **+$765** |
| Mar 20 | 12:26 | DEB_FADE | $478P | 7 | **+$763** |

#### Analysis

**Mar 12 - ITM_MOMENTUM (VIX 27.9, RISING):**
- Regime: WORSENING_HIGH
- Strategy correctly identified PUT direction
- Trades hit stop loss as volatility was too extreme
- Vol Shock triggered multiple times

**Mar 20 - DEBIT_FADE (VIX 19.2, FALLING):**
- Regime: IMPROVING
- Correctly faded the rally with PUTs
- Both trades hit profit target
- Net +$1,528 on Mar 20

**Observation:** Intraday engine profitable (+$867). DEBIT_FADE strategy outperformed ITM_MOMENTUM in this period.

---

### 4. RISK ENGINE ✅

| Metric | Value |
|--------|-------|
| Kill Switch Triggers | 1 |
| CB Level 1 Triggers | 1 |
| Weekly Breaker Triggers | 1 |
| Vol Shock Triggers | 80+ |

#### Kill Switch Event (Mar 26 @ 10:21)

```
KILL_SWITCH: TRIGGERED | Loss=5.43% from prior_close
Baseline=$50,457.43 | Current=$47,715.83
Options P&L=$-3,019 | Trend P&L=$0
KILL_SWITCH: CATASTROPHIC LOSS - Full liquidation
```

**✅ Correct Behavior:**
- Triggered at -5.43% (threshold: 5%)
- Successfully closed all options positions
- No margin call spam (0 invalid orders)
- Cold start reset to day 0

#### Circuit Breaker Events

| Event | Date | Trigger |
|-------|------|---------|
| CB_LEVEL_1 | Mar 26 09:58 | Daily loss 2.47% ≥ 2% |
| WEEKLY_BREAKER | Mar 27 09:31 | WTD loss 6.16% |

**✅ Both triggered correctly and reduced sizing.**

#### Vol Shock Protection

- **80+ vol shock events** across Mar-May
- Most active days: Apr 7-9 (market turmoil), Apr 23
- Apr 9 had extreme moves (bar range $16.17 vs threshold $7.11)
- All correctly paused trading for 15 min

---

### 5. REGIME ENGINE

| Metric | Value |
|--------|-------|
| Regime Range | 51.2 - 67.6 |
| Average Regime | ~58 (NEUTRAL) |
| Hedge Allocations | TMF: 0%, PSQ: 0% |

**Pattern:** Regime stayed in NEUTRAL band throughout (50-70). No defensive hedges activated.

---

### 6. HEDGE ENGINE - INACTIVE (CORRECT)

| Metric | Value |
|--------|-------|
| Hedge Trades | 0 |
| TMF Allocation | 0% |
| PSQ Allocation | 0% |

**Correct Behavior:** Regime score never dropped below defensive threshold (<40). No hedges needed.

---

### 7. COLD START ENGINE

| Event | Date | Action |
|-------|------|--------|
| Day 1 | Mar 1 | Warm entry blocked (regime 50.0 ≤ 50) |
| Day 2 | Mar 2 | SSO warm entry @ 25% |
| Day 5 | Mar 5 | Transitioned to NORMAL mode |
| Reset | Mar 26 | Kill switch triggered reset |
| Day 1 | Mar 27 | QLD warm entry @ 25% |

**✅ Cold Start working correctly.**

---

## Phase 3: Critical Failure Flags

| Severity | Search Term | Found | Status |
|----------|-------------|:-----:|:------:|
| 🔴 CRITICAL | INSUFFICIENT_MARGIN | 0 | ✅ PASS |
| 🔴 CRITICAL | ZeroDivisionError | 0 | ✅ PASS |
| 🔴 CRITICAL | Margin call spam | 0 | ✅ PASS |
| 🟡 WARN | Order rejected | 0 | ✅ PASS |
| 🟡 WARN | No data for | 0 | ✅ PASS |
| 🟢 INFO | Error/Exception | 0 | ✅ PASS |

**✅ NO CRITICAL FAILURES**

---

## Phase 4: Performance Reality Check

### Market Context (Mar-May 2025)

- **Mar 2025:** Volatile (VIX 19-28), multiple vol shocks
- **Apr 2025:** Extreme volatility (Apr 7-9), VIX spikes
- **May 2025:** Calmer, VIX settling 18-20

### Bot vs Market

| Metric | Bot | Assessment |
|--------|-----|------------|
| Total Return | -5.9% | ⚠️ |
| Drawdown | ~6% | Acceptable |
| Win Rate | 4/14 (29%) | Low |

### Attribution

| Component | P&L | % of Loss |
|-----------|----:|:---------:|
| Trend Engine | -$680 | 23% |
| Options Spreads | -$2,938 | **99%** |
| Intraday Options | +$867 | (profit) |
| Fees | ~$200 | 7% |

**Root Cause:** The bull call spread on Mar 25-26 accounted for nearly the entire loss. The spread was entered in a favorable regime (61-64) but the market reversed sharply, triggering the kill switch.

---

## Summary

### What Worked ✅

1. **Kill Switch** - Triggered once, executed cleanly, no margin spam
2. **CB Level 1** - Triggered correctly at 2.47% loss
3. **Weekly Breaker** - Triggered correctly at 6.16% WTD loss
4. **Vol Shock** - 80+ pauses, protected from extreme moves
5. **Intraday Options** - Net profitable (+$867)
6. **Cold Start** - Reset correctly after kill switch

### What Needs Attention ⚠️

1. **ADX Filter Too Strict** - Only FAS passed (ADX 25+), QLD/SSO blocked
2. **Spread Sizing** - Two consecutive entries ($9,450 + $9,856 = $19,306 at risk)
3. **SMA50 Exit** - Choppy markets causing 1-day exits

### Recommendations for V2.5

| Priority | Recommendation |
|:--------:|----------------|
| P1 | Consider ADX ≥ 22 instead of 25 |
| P1 | Add spread position limit (max 1 active spread) |
| P2 | Add 2-day SMA50 confirmation before exit |
| P3 | Review Grind-Up Override proposal |

---

## Comparison: V2.4.5 Jan-Feb vs Mar-May

| Metric | Jan-Feb 2025 | Mar-May 2025 |
|--------|:------------:|:------------:|
| Return | -7.8% | -5.9% |
| Kill Switch | 1 | 1 |
| Margin Calls | 0 | 0 |
| Trend Trades | 10 | 5 |
| Options Trades | 1 | 9 |
| Intraday P&L | N/A | +$867 |

**Improvement:** Intraday options engine now active and profitable. System stability maintained.

---

**Report Generated:** 2026-02-02
**Backtest Hash:** V2_4_5_Mar_May_2025
