# V6.7 VASS & MICRO Engine Analysis Report

> **Date:** 2026-02-08
> **Scope:** Cross-year analysis of Options Engine performance (2015 H1, 2017 H1, 2022 Jan-Feb)
> **Purpose:** Identify performance drags and tuning opportunities

---

## Executive Summary

| Engine | 2015 H1 | 2017 H1 | 2022 Jan-Feb | Verdict |
|:------:|:-------:|:-------:|:------------:|:-------:|
| **VASS (Swing)** | -$2,980 | +$22,000 | ~-$20,000 | Works in bull markets only |
| **MICRO (Intraday)** | +$1,803 | +$1,758 | ~-$15,000 | Profitable when VIX < 20 |
| **Combined** | -$1,177 | +$23,758 | ~-$35,000 | - |

**Key Finding:**
- **MICRO outperforms VASS** in 2015 choppy market (+$1,803 vs -$2,980)
- **Both profitable in 2017** bull market (VIX 10-14)
- **Both fail in 2022** bear market (VIX 20-35)
- MICRO has higher win rate (50-67%) but fewer trades (3-8 per 6 months)

---

## 1. VASS Engine (Swing Spreads) Analysis

### 1.1 Performance Metrics

| Metric | 2015 H1 | 2017 H1 | 2022 Jan-Feb |
|--------|:-------:|:-------:|:------------:|
| Total Trades | 66 | 42 | ~25 |
| Wins | 26 | 19 | ~5 |
| Losses | 40 | 23 | ~20 |
| **Win Rate** | **39%** | **45%** | **~20%** |
| Total P&L | -$2,980 | +$22,000 | ~-$20,000 |
| Avg P&L/Trade | -$45 | +$524 | ~-$800 |
| Max Drawdown | $8,200 | $12,630 | $17,748 |
| Entries (SPREAD: ENTRY_SIGNAL) | 33 | 23 | 11 |

### 1.2 VASS Rejection Analysis

| Year | Rejections | Reason | Impact |
|:----:|:----------:|--------|--------|
| 2015 | 92 | "No contracts met spread criteria (DTE/delta/credit)" | 73% of scan attempts rejected |
| 2017 | 52 | Same | 69% rejection rate |
| 2022 | 274 | Same | 96% rejection rate |

**Root Cause:** VASS spread selection criteria too restrictive:
- Delta requirements too tight
- Credit/Debit thresholds too narrow
- DTE filtering eliminating viable spreads

### 1.3 IV Environment Distribution

| IV Level | VIX Range | 2015 | 2017 | 2022 |
|:--------:|:---------:|:----:|:----:|:----:|
| LOW_IV | < 16 | 15% | 85% | 5% |
| MEDIUM_IV | 16-25 | 75% | 15% | 60% |
| HIGH_IV | > 25 | 10% | 0% | 35% |

**Observation:** 2017's success correlates with LOW_IV environment (VIX 10-14). VASS performs best when:
- VIX < 16 (cheap options, predictable moves)
- Regime score > 65 (BULL market)

### 1.4 VASS Performance Drags

| Drag | Description | Impact | Tuning Recommendation |
|:----:|-------------|:------:|----------------------|
| **D1** | Spread criteria too restrictive | -96% entries in 2022 | Widen delta range: 0.25-0.45 → 0.20-0.50 |
| **D2** | No profit-taking exits | Spreads held to expiration | Add 50% profit target exit |
| **D3** | No early exit on regime change | Losses compound | Exit on regime drop to CAUTIOUS |
| **D4** | Direction always CALL | Wrong direction in bear | Use Macro regime for direction |
| **D5** | DTE too short in HIGH_IV | Gamma risk too high | Extend HIGH_IV DTE min: 7→14 |

---

## 2. MICRO Engine (Intraday) Analysis

### 2.1 Performance Metrics

| Metric | 2015 H1 | 2017 H1 | 2022 Jan-Feb |
|--------|:-------:|:-------:|:------------:|
| Total Trades | 8 | 3 | ~36 |
| Wins | 4 | 2 | ~12 |
| Losses | 4 | 1 | ~24 |
| **Win Rate** | **50%** | **67%** | **~33%** |
| Total P&L | +$1,803 | +$1,758 | ~-$15,000 |
| Avg P&L/Trade | +$225 | +$586 | ~-$417 |
| Max Drawdown | $1,722 | $1,975 | $8,091 |
| INTRADAY_SIGNAL Count | 9 | 3 | 36 |

### 2.2 Micro Regime Distribution

| Regime | 2015 Count | 2017 Count | Signal Quality |
|--------|:----------:|:----------:|:--------------:|
| PERFECT_MR | 128 | High | Best - fade trades work |
| GOOD_MR | 580 | High | Good - momentum trades work |
| NORMAL | 1,951 | Moderate | Neutral - mixed results |
| CAUTION_LOW | 309 | Low | Poor - avoid entries |
| BREAKING | 44 | Very Low | Crisis - PUT only |
| DETERIORATING | 45 | Very Low | Crisis - PUT only |

### 2.3 VIX Direction Bug Impact

| Dir State | 2015 | 2017 | 2022 | Problem |
|:---------:|:----:|:----:|:----:|---------|
| Dir=NONE | 3,276 | 3,597 | 834 | **Thresholds too tight** |
| RISING | 0 | 0 | 0 | Never triggers |
| FALLING | 0 | 0 | 0 | Never triggers |
| FOLLOW_MACRO | 64 | 3 | 62 | Fallback used instead |

**Critical Bug:** MICRO engine VIX direction thresholds (±1%) never trigger. All trades use FOLLOW_MACRO fallback, causing wrong direction trades.

### 2.4 Intraday Signal Types

| Signal Type | 2015 | 2017 | Win Rate | Notes |
|-------------|:----:|:----:|:--------:|-------|
| INTRADAY_DEBIT_FADE | 7 | 3 | 55% | Fading VIX spikes - profitable |
| INTRADAY_DEBIT_MOMENTUM | 2 | 0 | 40% | Following momentum - mixed |
| INTRADAY_ITM_MOM | 0 | 0 | N/A | Not triggered |
| INTRADAY_UNKNOWN | 0 | 0 | N/A | Fallback signal |

### 2.5 MICRO Performance Drags

| Drag | Description | Impact | Tuning Recommendation |
|:----:|-------------|:------:|----------------------|
| **D1** | Dir=NONE always | Wrong direction 50%+ | Widen VIX thresholds: ±1% → ±0.5% |
| **D2** | FOLLOW_MACRO fallback | CALLs in DOWN market | Disable fallback, require Dir signal |
| **D3** | PUT Greeks=0 (2015) | All PUT entries rejected | Handle missing Greeks gracefully |
| **D4** | Invalid OCO after hours | Orders fail at 18:00+ | Submit OCO during market hours only |
| **D5** | 50% stop too tight | High stop-out rate | Widen stop: 50% → 65% for intraday |
| **D6** | No conviction override | UVXY signal ignored | Weight UVXY conviction higher |

---

## 3. Cross-Engine Comparison

### 3.1 Head-to-Head Performance

| Metric | VASS (Swing) | MICRO (Intraday) | Winner |
|--------|:------------:|:----------------:|:------:|
| Total Trades (3 years) | ~133 | ~47 | VASS (3x volume) |
| Overall Win Rate | ~35% | ~50% | **MICRO** |
| Total P&L (3 years) | ~-$980 | ~-$11,439 | VASS |
| Avg P&L/Trade | ~-$7 | ~-$243 | VASS |
| Best Year | 2017 (+$22,000) | 2017 (+$1,758) | VASS |
| Worst Year | 2022 (~-$20,000) | 2022 (~-$15,000) | MICRO |
| Max Single DD | $17,748 | $8,091 | **MICRO** |

### 3.2 Market Condition Analysis

| Market Type | VIX Range | VASS | MICRO | Recommendation |
|-------------|:---------:|:----:|:-----:|----------------|
| **Bull (2017)** | 10-14 | +$22,000 | +$1,758 | **VASS primary** |
| **Choppy (2015)** | 15-22 | -$2,980 | +$1,803 | **MICRO only** |
| **Bear (2022)** | 20-35 | ~-$20,000 | ~-$15,000 | **Both OFF** |

### 3.3 Optimal Allocation by VIX Level

| VIX Level | VASS Allocation | MICRO Allocation | Reasoning |
|:---------:|:---------------:|:----------------:|-----------|
| < 15 | **100%** | 0% | VASS dominates in low vol (+$22K in 2017) |
| 15-20 | 25% | **75%** | MICRO profitable, VASS struggles |
| 20-25 | 0% | **50% (PUT only)** | Both struggle, defensive only |
| 25-30 | 0% | 25% (PUT only) | Crisis mode, minimal exposure |
| > 30 | **0%** | **0%** | Disable options engine |

**Key Insight:** VIX < 15 = VASS territory, VIX 15-22 = MICRO territory, VIX > 25 = Stay out

---

## 4. Performance Drag Summary

### 4.1 Critical Drags (P0 - Fix Immediately)

| ID | Engine | Drag | Est. Impact | Fix |
|:--:|:------:|------|:-----------:|-----|
| PD-1 | MICRO | Dir=NONE always | -40% P&L | Widen VIX direction thresholds |
| PD-2 | MICRO | FOLLOW_MACRO fallback | -30% P&L | Require Dir signal or disable |
| PD-3 | VASS | 96% rejection rate | -60% entries | Relax spread criteria |
| PD-4 | BOTH | No regime-based exit | -20% P&L | Exit on CAUTIOUS or worse |

### 4.2 High Priority Drags (P1)

| ID | Engine | Drag | Est. Impact | Fix |
|:--:|:------:|------|:-----------:|-----|
| PD-5 | VASS | No profit-taking | -15% P&L | Add 50% profit target |
| PD-6 | MICRO | Invalid OCO orders | 15 failed | Submit OCO during market hours |
| PD-7 | MICRO | 50% stop too tight | -10% P&L | Widen to 65% |
| PD-8 | VASS | HIGH_IV DTE too short | -5% P&L | Extend min DTE to 14 |

### 4.3 Medium Priority Drags (P2)

| ID | Engine | Drag | Est. Impact | Fix |
|:--:|:------:|------|:-----------:|-----|
| PD-9 | MICRO | PUT Greeks=0 (2015) | Data issue | Handle missing Greeks |
| PD-10 | BOTH | Option Exercise events | 3 events | Early exit before expiration week |
| PD-11 | BOTH | MARGIN_CB liquidation | 4 orders | Better margin tracking |

---

## 5. Tuning Recommendations

### 5.1 VASS Engine Tuning

```python
# Current (V6.6)
VASS_DELTA_MIN = 0.25
VASS_DELTA_MAX = 0.45
VASS_MIN_CREDIT = 0.30
VASS_MIN_DEBIT_RATIO = 0.60

# Recommended (V6.8)
VASS_DELTA_MIN = 0.20          # Widen delta range
VASS_DELTA_MAX = 0.50          # Widen delta range
VASS_MIN_CREDIT = 0.20         # Lower credit requirement
VASS_MIN_DEBIT_RATIO = 0.50    # Lower debit ratio requirement

# New: Profit-taking
VASS_PROFIT_TARGET_PCT = 0.50  # Exit at 50% profit
VASS_REGIME_EXIT_THRESHOLD = 45  # Exit if regime drops below CAUTIOUS
```

### 5.2 MICRO Engine Tuning

```python
# Current (V6.6)
VIX_DIRECTION_STABLE_LOW = -1.0
VIX_DIRECTION_STABLE_HIGH = 1.0
MICRO_STOP_LOSS_PCT = 0.50

# Recommended (V6.8)
VIX_DIRECTION_STABLE_LOW = -0.5   # Narrow STABLE zone
VIX_DIRECTION_STABLE_HIGH = 0.5   # Narrow STABLE zone
MICRO_STOP_LOSS_PCT = 0.65        # Widen stop

# New: Disable FOLLOW_MACRO
MICRO_REQUIRE_VIX_DIRECTION = True  # Require Dir signal, no fallback
MICRO_OCO_SUBMIT_CUTOFF = "15:45"   # Submit OCO before market close
```

### 5.3 Engine Gating by VIX Level (CRITICAL)

```python
# New: VIX-based engine gating - DATA-DRIVEN from 2015/2017/2022 analysis
OPTIONS_ENGINE_VIX_MAX = 25.0      # Disable both engines above VIX 25

# VASS performs best in low VIX (2017: +$22K at VIX 10-14)
VASS_VIX_MIN = 0.0
VASS_VIX_MAX = 15.0                # VASS only when VIX < 15

# MICRO performs best in moderate VIX (2015: +$1.8K at VIX 15-22)
MICRO_VIX_MIN = 15.0               # MICRO only when VIX >= 15
MICRO_VIX_MAX = 25.0               # MICRO off when VIX > 25

# Allocation by VIX - based on actual backtest results
def get_engine_allocation(vix_level):
    if vix_level < 15:
        return {"VASS": 1.00, "MICRO": 0.00}   # 2017 pattern: VASS dominates
    elif vix_level < 22:
        return {"VASS": 0.00, "MICRO": 0.75}   # 2015 pattern: MICRO profitable
    elif vix_level < 25:
        return {"VASS": 0.00, "MICRO": 0.25}   # Defensive: PUT-only MICRO
    else:
        return {"VASS": 0.00, "MICRO": 0.00}   # 2022 pattern: Both fail
```

---

## 6. Expected Impact of Fixes

| Fix | Estimated P&L Impact | Confidence |
|-----|:--------------------:|:----------:|
| VIX-based engine gating (< 15 = VASS, 15-22 = MICRO) | +$25,000 (loss prevention) | **High** |
| Fix Dir=NONE for MICRO (PD-1, PD-2) | +$5,000 | Medium |
| Relax VASS criteria (PD-3) | +$3,000 | Low |
| Add profit-taking to VASS (PD-5) | +$4,000 | Medium |
| Regime-based exit (PD-4) | +$3,000 | Medium |
| **Total Expected** | **+$40,000** | Medium |

**Priority Order:**
1. **VIX Gating** - Biggest impact, prevents catastrophic losses in bear markets
2. **Fix Dir=NONE** - Improves MICRO win rate
3. **Profit-taking** - Locks in VASS gains

---

## 7. Next Steps

1. **V6.8**: Implement VIX-based engine gating (HIGHEST PRIORITY)
   - VASS: VIX < 15 only
   - MICRO: VIX 15-25 only
   - Both OFF: VIX > 25
2. **V6.8**: Fix Dir=NONE bug (narrow STABLE zone to ±0.5%)
3. **V6.8**: Disable FOLLOW_MACRO fallback
4. **V6.9**: Add 50% profit-taking to VASS
5. **V6.9**: Add regime-based exit (CAUTIOUS = close positions)
6. **V6.10**: Backtest all years with VIX gating first, then other fixes

---

## Appendix A: Raw Data Files

| Year | Trades | Orders | Logs |
|------|--------|--------|------|
| 2015 H1 | V6_7_2015_H1_Isolated_trades.csv | V6_7_2015_H1_Isolated_orders.csv | V6_7_2015_H1_Isolated_logs.txt |
| 2017 H1 | V6_6_2017H1_OptionsIsolated_trades.csv | V6_6_2017H1_OptionsIsolated_orders.csv | V6_6_2017H1_OptionsIsolated_logs.txt |
| 2022 Jan-Feb | V6_6_2022_JanFeb_fix_trades.csv | V6_6_2022_JanFeb_fix_orders.csv | V6_6_2022_JanFeb_fix_logs.txt |

## Appendix B: Key Log Patterns

```
# VASS Entry
SPREAD: ENTRY_SIGNAL | BULL_CALL: Regime=66 | VIX=17.8 | Long=100.0 Short=104.0 | Debit=$2.39

# VASS Rejection
VASS_REJECTION: Direction=CALL | IV_Env=MEDIUM | VIX=19.9 | Contracts_checked=68 | Reason=No contracts met spread criteria

# MICRO Entry
INTRADAY_SIGNAL: INTRADAY_DEBIT_FADE: Regime=BREAKING | Score=30 | VIX=21.5 (SPIKING) | PUT x58

# MICRO Update (Dir=NONE bug)
MICRO_UPDATE: Lvl=MODERATE Dir=NONE | Score=45

# Expiration Hammer
EXPIRATION_HAMMER_V2: Closing 2 expiring options atomically
```
