# V2.5 Algorithmic Audit Protocol (AAP) Report

**Backtest:** V2.5-Jan-May-2025
**Period:** Jan 1 - May 31, 2025 (5 months)
**Starting Capital:** $50,000
**Final Capital:** $46,656
**Return:** **-6.7%**

---

## Executive Summary

| Metric | Value | Status |
|--------|-------|:------:|
| Total Return | -6.7% | ⚠️ Loss |
| Kill Switch Triggers | 1 | ✅ Working |
| Weekly Breaker Triggers | 1 | ✅ Working |
| Total Trades | 13 | ⚠️ Low |
| Last Trade Date | **Mar 24, 2025** | 🔴 CRITICAL |
| Apr-May Trades | **0** | 🔴 CRITICAL |

**VERDICT:** System stopped trading after March 24. Two months of zero activity due to overly restrictive filters.

---

## Phase 1: Three-Way Match (Funnel Analysis)

| Funnel Stage | Jan-Mar | Apr-May | Status |
|--------------|:-------:|:-------:|:------:|
| Entry Signals | 11 | 0 | 🔴 |
| Orders Submitted | 25 | 0 | 🔴 |
| Trades Filled | 13 | 0 | 🔴 |

**Diagnosis:** 🔴 **FAIL** - No entry signals generated in April or May.

---

## Phase 2: Engine-by-Engine Analysis

### 1. TREND ENGINE

| Metric | Jan-Mar | Apr-May |
|--------|:-------:|:-------:|
| Entry Signals | 11 | 0 |
| Trades | 12 | 0 |
| P&L | -$318 | $0 |

#### Why No Trades in Apr-May?

**ADX Filter Blocking ALL Entries:**

| Date | Symbol | ADX | Threshold | Result |
|------|--------|:---:|:---------:|--------|
| Apr 1 | FAS | 17.3 | 22 | BLOCKED |
| Apr 2 | FAS | 16.8 | 22 | BLOCKED |
| May 2 | FAS | 20.9 | 22 | BLOCKED |
| May 5 | FAS | 19.5 | 22 | BLOCKED |
| May 8 | FAS | 16.2 | 22 | BLOCKED |

**Root Cause:** ADX values in April-May ranged from **15-21**, all below the V2.5 threshold of 22. Even after lowering from 25 to 22, the market conditions in Apr-May had weaker trend strength.

**Evidence from logs:**
```
2025-04-01 15:45:00 TREND: FAS entry blocked - ADX 17.3 too weak (score=0.50 < 0.75)
2025-05-02 15:45:00 TREND: FAS entry blocked - ADX 20.9 too weak (score=0.50 < 0.75)
```

---

### 2. OPTIONS ENGINE - SWING MODE (Spreads)

| Metric | Jan-Mar | Apr-May |
|--------|:-------:|:-------:|
| Entry Signals | 1 | 0 |
| Trades | 2 | 0 |
| P&L | -$3,432 | $0 |

#### Why No Trades in Apr-May?

**Regime Neutral Zone (45-60) Blocking Spreads:**

| Date | Regime Score | Spread Type | Result |
|------|:------------:|-------------|--------|
| Apr 1-4 | 50-54 | NEUTRAL | NO ENTRY |
| Apr 5-30 | 41-47 | CAUTIOUS | Bear Put only if <45 |
| May 1-31 | 41-53 | CAUTIOUS/NEUTRAL | NO ENTRY |

**Spread Entry Rules:**
- Bull Call: Regime > 60 (never reached in Apr-May)
- Bear Put: Regime < 45 (barely reached some days)
- No Entry: Regime 45-60 (most of Apr-May)

**Evidence from logs:**
```
2025-04-01 15:45:00 REGIME: RegimeState(NEUTRAL | Score=53.5 | ...)
2025-04-07 15:45:00 REGIME: RegimeState(CAUTIOUS | Score=43.9 | ...)
2025-05-15 15:45:00 REGIME: RegimeState(NEUTRAL | Score=52.1 | ...)
```

---

### 3. OPTIONS ENGINE - INTRADAY MODE (Micro Regime)

| Metric | Jan-Mar | Apr-May |
|--------|:-------:|:-------:|
| Micro Signals Generated | ~200 | ~400 |
| Actual Trades | 0 | 0 |

#### CRITICAL FINDING: Micro Regime Signals Ignored

**592 micro regime signals generated** (DEBIT_FADE, ITM_MOMENTUM) but **ZERO intraday trades executed**.

**Sample signals from April 1:**
```
2025-04-01 11:00:00 MICRO: Strategy=DEBIT_FADE | Direction=PUT
2025-04-01 11:15:00 MICRO: Strategy=DEBIT_FADE | Direction=PUT
2025-04-01 12:45:00 MICRO: Strategy=ITM_MOMENTUM | Direction=CALL
2025-04-01 13:30:00 MICRO: Strategy=ITM_MOMENTUM | Direction=CALL
```

**Why Signals Not Executed:**

The micro regime engine recommends strategies, but the actual trade execution requires:
1. Valid 0-1 DTE contract selection (DTE constraint)
2. Contract with 0.30 delta ± tolerance
3. Valid bid/ask prices
4. Acceptable spread width

**Hypothesis:** In backtesting, QC may not have full options data for 0-1 DTE contracts in Apr-May 2025 (future date), causing contract selection to fail silently.

---

### 4. RISK ENGINE ✅

| Metric | Value |
|--------|-------|
| Kill Switch Triggers | 1 |
| Weekly Breaker Triggers | 1 |
| Vol Shock Pauses | Multiple |

#### Events

**Jan 6 Kill Switch:**
```
KILL_SWITCH: TRIGGERED | Loss=5.28% from sod
Baseline=$50,535.06 | Current=$47,867.87
Options P&L=$-3,466 | Trend P&L=$312
```

**Jan 7 Weekly Breaker:**
```
WEEKLY_BREAKER: TRIGGERED | WTD loss=6.22%
```

**✅ Both working correctly.**

---

### 5. COLD START ENGINE ✅

| Event | Date | Status |
|-------|------|--------|
| Day 1 | Jan 1 | Warm entries executed |
| Day 5 | Jan 5 | Transitioned to NORMAL |
| Reset | Jan 6 | Kill switch reset |

**✅ Working correctly.**

---

## Phase 3: Critical Failure Flags

| Severity | Issue | Status |
|----------|-------|:------:|
| 🔴 CRITICAL | No trades Apr-May | FAIL |
| 🔴 CRITICAL | ADX blocking ALL | FAIL |
| 🔴 CRITICAL | Micro signals ignored | FAIL |
| 🟡 WARN | Spread neutral zone | FAIL |
| ✅ OK | Kill switch | PASS |
| ✅ OK | No margin calls | PASS |

---

## Phase 4: Root Cause Analysis

### Why No Trades After March 24, 2025?

| Engine | Blocking Condition | Apr-May Values | Threshold |
|--------|-------------------|:--------------:|:---------:|
| **TREND** | ADX too weak | 15-21 | ≥22 |
| **SWING** | Regime neutral | 41-53 | >60 or <45 |
| **INTRADAY** | Contract selection | Failed | Valid 0-1 DTE |

### The Triple Block

1. **Trend Engine**: Even with ADX lowered to 22, April-May had ADX 15-21 (very weak trends)

2. **Swing Spreads**: Regime scores 41-53 fall in "neutral zone" (45-60) where no spreads are entered

3. **Intraday Options**: Despite 592 micro regime signals recommending trades, no contracts were selected (likely QC data limitation for future dates)

---

## Phase 5: Recommendations

### P0 - CRITICAL (Blocking All Trades)

| Priority | Recommendation | Rationale |
|:--------:|----------------|-----------|
| P0 | Lower ADX to 18-20 | Apr-May ADX was 15-21, even 22 is too high |
| P0 | Narrow spread neutral zone | Current 45-60 is too wide, try 50-55 |
| P0 | Add intraday logging | Silent failures hide why 592 signals produced 0 trades |

### P1 - HIGH

| Priority | Recommendation | Rationale |
|:--------:|----------------|-----------|
| P1 | Consider ADX ≥ 18 for weak trend markets | Jan-May 2025 had weak ADX overall |
| P1 | Add Bull Call at regime > 55 | Current >60 may be too restrictive |
| P1 | Add Bear Put at regime < 48 | Current <45 may be too restrictive |

### P2 - MEDIUM

| Priority | Recommendation | Rationale |
|:--------:|----------------|-----------|
| P2 | Log contract selection failures | "No valid contract" message needed |
| P2 | Log when micro signals are ignored | Track funnel leakage |

---

## Trade Summary

### All Trades (Jan-Mar 2025)

| Entry Date | Symbol | Exit Date | P&L | Exit Reason |
|------------|--------|-----------|----:|-------------|
| Jan 2 | TNA | Jan 6 | +$240 | - |
| Jan 2 | FAS | Jan 6 | +$47 | - |
| Jan 2 | SSO | Jan 13 | -$634 | - |
| Jan 6 | Bull Call Spread | Jan 6 | **-$3,432** | Kill Switch |
| Jan 7 | FAS | Jan 10 | -$139 | - |
| Jan 13 | FAS | Jan 15 | +$511 | - |
| Jan 14 | SSO | Feb 27 | +$236 | - |
| Jan 16 | FAS | Mar 10 | -$352 | SMA50_BREAK |
| Mar 13 | FAS | Mar 17 | +$122 | SMA50_BREAK |
| Mar 18 | FAS | Mar 20 | +$26 | SMA50_BREAK |
| Mar 21 | FAS | Mar 24 | +$119 | SMA50_BREAK |

**Net P&L:** -$3,256 (Options: -$3,432, Trend: +$176)

---

## Comparison: V2.4.5 vs V2.5

| Metric | V2.4.5 Mar-May | V2.5 Jan-May |
|--------|:--------------:|:------------:|
| Return | -5.9% | -6.7% |
| Total Trades | 14 | 13 |
| Apr-May Trades | 0* | 0 |
| ADX Threshold | 25→22 | 22 |
| Intraday Trades | 5 | 0 |

*V2.4.5 only ran Mar-May, so no Jan-Feb data.

**Key Insight:** V2.5 improvements (ADX 22, wider stops) didn't prevent Apr-May trading drought because:
1. ADX 22 still too high for Apr-May (15-21)
2. Intraday signals completely disconnected from execution

---

## Verdict

**V2.5 has systematic issues preventing trades in low-ADX, neutral-regime environments.**

The system is effectively "turned off" during:
- Low ADX periods (< 22)
- Neutral regime periods (45-60)
- Future dates where QC lacks options data

**Recommended Action:** Implement P0 fixes before next backtest.

---

**Report Generated:** 2025-02-02
**Backtest Hash:** V2_5_Jan_May_2025
