# Capital Engine Comprehensive Audit

> **Version:** V3.0 Hardening
> **Date:** 2026-02-04
> **Purpose:** Verify capital allocation works correctly across all regimes and portfolio sizes

---

## 1. Executive Summary

This audit examines the capital allocation system to ensure:
1. Multiple engines can run simultaneously without over-allocating
2. Margin consumption is properly tracked with leveraged ETFs
3. Thesis-aligned regime gating prevents allocation conflicts
4. The system scales correctly for any portfolio size ≥ $50,000

---

## 2. Current Allocation Budgets

| Engine | Budget | Symbols | Leverage | Margin Impact |
|--------|:------:|---------|:--------:|:-------------:|
| **Trend** | 40% | QLD (15%), SSO (12%), TNA (8%), FAS (5%) | 2-3× | 40% × 2.4× = **96%** |
| **Options** | 25% | QQQ spreads | 1× (defined risk) | ~25% |
| **Mean Reversion** | 10% | TQQQ (5%), SOXL (5%) | 3× | 10% × 3× = **30%** |
| **Hedges** | 0-30% | TMF (0-20%), PSQ (0-10%) | 1-3× | Up to **50%** |
| **Yield** | Variable | SHV | 1× | Variable |

### Theoretical Maximum (All Engines Active)

```
Trend (40%) + Options (25%) + MR (10%) + Hedges (30%) = 105%
```

**Mitigation:** Regime gating ensures not all engines run simultaneously.

---

## 3. Thesis-Aligned Capital Flow

### 3.1 Regime-to-Allocation Matrix

| Regime | Score | Trend | MR | Options | Hedges | Max Total |
|--------|:-----:|:-----:|:--:|:-------:|:------:|:---------:|
| **Bull** | 70+ | 40% | 10% | 25% | 0% | **75%** |
| **Neutral** | 50-69 | 40% | 10% | 0% | 0% | **50%** |
| **Cautious** | 40-49 | 0% | 0% | 25% | 10% | **35%** |
| **Defensive** | 30-39 | 0% | 0% | 25% | 20% | **45%** |
| **Bear** | 0-29 | 0% | 0% | 25% | 30% | **55%** |

### 3.2 Margin-Weighted Analysis

| Regime | Active Engines | Allocation | Margin Weighted |
|--------|----------------|:----------:|:---------------:|
| **Bull** | Trend + Options + MR | 75% | ~120% ⚠️ |
| **Neutral** | Trend + MR | 50% | ~80% |
| **Cautious** | Options + Hedges (light) | 35% | ~45% |
| **Defensive** | Options + Hedges (medium) | 45% | ~60% |
| **Bear** | Options + Hedges (full) | 55% | ~75% |

---

## 4. Issues Identified

### 4.1 🔴 Bull Regime Margin Overflow

In Bull (70+), all growth engines active:

```
Trend:   40% allocation × 2.4× avg leverage = 96% margin
Options: 25% allocation × 1× (spreads)      = 25% margin
MR:      10% allocation × 3× leverage       = 30% margin
────────────────────────────────────────────────────────
TOTAL MARGIN CONSUMPTION:                    = 151% ⚠️
```

**Current Mitigation:** `MAX_MARGIN_WEIGHTED_ALLOCATION = 0.90` caps at 90%

**Problem:** The cap scales down positions, but there's no **prioritization** of which engine gets reduced first.

### 4.2 🔴 Exposure Group Conflicts

| Group | Symbols | Max Net Long | Max Gross | Potential Conflict |
|-------|---------|:------------:|:---------:|:------------------:|
| NASDAQ_BETA | QLD, TQQQ, SOXL, PSQ | 50% | 75% | Trend + MR compete |
| SPY_BETA | SSO | 40% | 40% | None (single symbol) |
| SMALL_CAP_BETA | TNA | 25% | 25% | None (single symbol) |
| FINANCIALS_BETA | FAS | 15% | 15% | None (single symbol) |
| RATES | TMF | 40% | 40% | None (single symbol) |

**NASDAQ_BETA Conflict:**
- Trend QLD: 15%
- MR TQQQ: 5%
- MR SOXL: 5%
- **Total: 25%** (within 50% limit ✅)

QQQ options add delta exposure ≈ 10-15%, total NASDAQ: 40% (still OK)

### 4.3 🔴 Capital Partition Not Enforced at Runtime

Config defines:
```python
CAPITAL_PARTITION_TREND = 0.50    # 50% reserved for Trend
CAPITAL_PARTITION_OPTIONS = 0.50  # 50% reserved for Options
```

**Problem:** These are **documentation only** — not enforced in code!

### 4.4 🟡 Hedges Not Regime-Gated

Current flow in `_on_eod_processing`:
```python
# 5. Generate Hedge signals (ALWAYS — never gated, defensive by nature)
self._generate_hedge_signals(regime_state)  # ← NO REGIME CHECK!
```

**Current behavior:** Hedges run at ALL regime levels
**Thesis says:** Hedges should be 0% at regime 50+ (Bull/Neutral)

### 4.5 🟡 No Priority System for Allocation Conflicts

When multiple engines compete for limited capital, there's no explicit priority.

---

## 5. What's Working Correctly

| Component | Status | Notes |
|-----------|:------:|-------|
| Exposure Groups | ✅ | Properly defined and enforced |
| Leverage-Adjusted Margin | ✅ | `SYMBOL_LEVERAGE` accounts for 2×/3× ETFs |
| Margin Cap | ✅ | `MAX_MARGIN_WEIGHTED_ALLOCATION = 90%` prevents overflow |
| Options Reservation | ✅ | `RESERVED_OPTIONS_PCT = 25%` protects options capital |
| Governor Scaling | ✅ | Drawdown governor scales all engines proportionally |
| Source Allocation Limits | ✅ | Portfolio router enforces per-source caps |

---

## 6. Recommended Fixes

### Fix #1: Enforce Hedge Regime Gating

```python
# In main.py _on_eod_processing
# 5. Generate Hedge signals (regime-gated per thesis)
regime_score = regime_state.score
if regime_score < 50:  # Only hedge in Cautious/Defensive/Bear
    self._generate_hedge_signals(regime_state)
else:
    # In Bull/Neutral, ensure hedges are unwound
    self._generate_hedge_exit_signals(regime_state)
```

### Fix #2: Add Total Allocation Cap

```python
# In config.py
MAX_TOTAL_ALLOCATION = 0.95  # Never allocate more than 95%

# In portfolio_router.py - after source limits
total_weight = sum(w.target_weight for w in aggregated.values())
if total_weight > config.MAX_TOTAL_ALLOCATION:
    scale = config.MAX_TOTAL_ALLOCATION / total_weight
    for w in aggregated.values():
        w.target_weight *= scale
```

### Fix #3: Add Engine Priority System

```python
# In config.py
ENGINE_PRIORITY = {
    "RISK": 0,      # Highest - emergency liquidations
    "HEDGE": 1,     # Second - defensive
    "TREND": 2,     # Core positions
    "OPT": 3,       # Satellite
    "MR": 4,        # Lowest - opportunistic
}
```

---

## 7. Portfolio Size Scalability Audit

### 7.1 Minimum Portfolio Requirements

| Component | Minimum | Rationale |
|-----------|--------:|-----------|
| **Seed Capital** | $50,000 | `PHASE_SEED_MIN` in config |
| **Options Spread** | $2,000 | `MIN_SPREAD_CONTRACTS = 2` × $5 width × 100 × 2 legs |
| **Trend Position** | $1,000 | Minimum meaningful position |
| **MR Position** | $500 | Minimum meaningful position |

### 7.2 Hardcoded Values to Audit

The following sections document any hardcoded dollar values that could prevent scaling:

- See Section 8 for detailed findings

---

## 8. Hardcoded Value Audit

### 8.1 🔴 Critical: Hardcoded Dollar Caps (DO NOT SCALE)

These values are **fixed dollar amounts** that will limit larger portfolios:

| Parameter | File | Value | Impact | Recommendation |
|-----------|------|------:|--------|----------------|
| `OPTIONS_MAX_MARGIN_CAP` | config.py | $10,000 | Caps ALL options margin | Change to percentage |
| `SWING_SPREAD_MAX_DOLLARS` | config.py | $7,500 | Caps swing spread size | Change to percentage |
| `INTRADAY_SPREAD_MAX_DOLLARS` | config.py | $4,000 | Caps intraday spread size | Change to percentage |
| `margin_remaining < 1000` | main.py:4105 | $1,000 | Minimum margin to trade options | Change to percentage |

**Example Problem:**
- $50K portfolio: $7,500 swing cap = 15% of portfolio ✅
- $200K portfolio: $7,500 swing cap = 3.75% of portfolio ❌ (under-allocated!)

### 8.2 🟡 Medium: Phase Thresholds (Scaling Boundaries)

These define phase transitions — they ARE intended to be fixed:

| Parameter | File | Value | Purpose |
|-----------|------|------:|---------|
| `PHASE_SEED_MIN` | config.py | $50,000 | Minimum starting capital |
| `PHASE_SEED_MAX` | config.py | $99,999 | Upper bound of SEED phase |
| `PHASE_GROWTH_MIN` | config.py | $100,000 | Entry to GROWTH phase |
| `PHASE_GROWTH_MAX` | config.py | $499,999 | Upper bound of GROWTH phase |
| `LOCKBOX_MILESTONES` | config.py | [$100K, $200K] | Profit lock triggers |

**Verdict:** These are **intentionally fixed** for phase management. ✅ OK

### 8.3 🟡 Medium: Minimum Trade Values

| Parameter | File | Value | Purpose | Scales? |
|-----------|------|------:|---------|:-------:|
| `MIN_TRADE_VALUE` | config.py | $2,000 | Minimum trend/MR trade | ❌ Fixed |
| `WARM_MIN_SIZE` | config.py | $2,000 | Cold start minimum | ❌ Fixed |
| `MIN_INTRADAY_OPTIONS_TRADE_VALUE` | config.py | $500 | Minimum options trade | ❌ Fixed |

**Verdict:** These minimums are OK — they prevent dust trades. But consider percentage-based minimums for very large portfolios.

### 8.4 ✅ Good: Percentage-Based Parameters

These scale correctly with portfolio size:

| Parameter | Value | Scales? |
|-----------|------:|:-------:|
| `TREND_TOTAL_ALLOCATION` | 40% | ✅ |
| `OPTIONS_TOTAL_ALLOCATION` | 25% | ✅ |
| `MR_TOTAL_ALLOCATION` | 10% | ✅ |
| `TMF_FULL` | 20% | ✅ |
| `PSQ_FULL` | 10% | ✅ |
| `MAX_MARGIN_WEIGHTED_ALLOCATION` | 90% | ✅ |
| `RESERVED_OPTIONS_PCT` | 25% | ✅ |
| All `EXPOSURE_LIMITS` | Percentages | ✅ |
| `MAX_SINGLE_POSITION_PCT` | 50%/40% | ✅ |

### 8.5 Recommended Fixes for Scalability

#### Fix 1: Convert Options Caps to Percentages

```python
# BEFORE (hardcoded)
OPTIONS_MAX_MARGIN_CAP = 10000  # $10K
SWING_SPREAD_MAX_DOLLARS = 7500  # $7,500
INTRADAY_SPREAD_MAX_DOLLARS = 4000  # $4,000

# AFTER (percentage-based)
OPTIONS_MAX_MARGIN_PCT = 0.20  # 20% of portfolio
SWING_SPREAD_MAX_PCT = 0.15   # 15% of portfolio
INTRADAY_SPREAD_MAX_PCT = 0.08  # 8% of portfolio
```

#### Fix 2: Convert Margin Guard to Percentage

```python
# BEFORE (main.py:4105)
if margin_remaining < 1000:

# AFTER
min_margin_pct = 0.02  # 2% of portfolio
if margin_remaining < self.Portfolio.TotalPortfolioValue * min_margin_pct:
```

#### Fix 3: Add Portfolio Size Validation

```python
# In Initialize()
if self.Portfolio.Cash < config.PHASE_SEED_MIN:
    raise ValueError(f"Portfolio ${self.Portfolio.Cash:,.0f} below minimum ${config.PHASE_SEED_MIN:,.0f}")
```

---

## 9. Summary

| Issue | Severity | Status |
|-------|:--------:|:------:|
| Bull margin overflow (151%) | 🔴 High | Mitigated by 90% cap |
| Total allocation > 100% | 🔴 High | Needs total cap enforcement |
| Hedges not regime-gated | 🟡 Medium | Needs regime check |
| No engine priority | 🟡 Medium | Enhancement recommended |
| Capital partition not enforced | 🟡 Medium | Remove or enforce |
| **OPTIONS_MAX_MARGIN_CAP hardcoded** | 🔴 High | ✅ Fixed → `OPTIONS_MAX_MARGIN_PCT = 0.20` |
| **SWING_SPREAD_MAX_DOLLARS hardcoded** | 🔴 High | ✅ Fixed → `SWING_SPREAD_MAX_PCT = 0.15` |
| **INTRADAY_SPREAD_MAX_DOLLARS hardcoded** | 🔴 High | ✅ Fixed → `INTRADAY_SPREAD_MAX_PCT = 0.08` |
| **margin_remaining < 1000 hardcoded** | 🟡 Medium | ✅ Fixed → `OPTIONS_MIN_MARGIN_PCT = 0.02` |
| Phase thresholds hardcoded | ✅ OK | Intentional design |
| Min trade values hardcoded | ✅ OK | Prevents dust trades |

---

## Appendix A: Config Parameters Reference

```python
# Allocation Budgets
TREND_TOTAL_ALLOCATION = 0.40
OPTIONS_TOTAL_ALLOCATION = 0.25
MR_TOTAL_ALLOCATION = 0.10
TMF_FULL = 0.20
PSQ_FULL = 0.10

# Margin Controls
MAX_MARGIN_WEIGHTED_ALLOCATION = 0.90
RESERVED_OPTIONS_PCT = 0.25

# Leverage Multipliers
SYMBOL_LEVERAGE = {
    "QLD": 2.0, "SSO": 2.0,
    "TNA": 3.0, "FAS": 3.0,
    "TQQQ": 3.0, "SOXL": 3.0,
    "TMF": 3.0, "PSQ": 1.0,
}

# Exposure Limits
EXPOSURE_LIMITS = {
    "NASDAQ_BETA": {"max_net_long": 0.50, "max_net_short": 0.30, "max_gross": 0.75},
    "SPY_BETA": {"max_net_long": 0.40, "max_net_short": 0.00, "max_gross": 0.40},
    "SMALL_CAP_BETA": {"max_net_long": 0.25, "max_net_short": 0.00, "max_gross": 0.25},
    "FINANCIALS_BETA": {"max_net_long": 0.15, "max_net_short": 0.00, "max_gross": 0.15},
    "RATES": {"max_net_long": 0.40, "max_net_short": 0.00, "max_gross": 0.40},
}
```

---

## Appendix B: Portfolio Scaling Impact Analysis

### Current Hardcoded Values vs Portfolio Size

| Portfolio | Swing Cap ($7,500) | Intraday Cap ($4,000) | Options Cap ($10,000) | Margin Guard ($1,000) |
|----------:|:------------------:|:---------------------:|:---------------------:|:---------------------:|
| $50,000 | 15.0% ✅ | 8.0% ✅ | 20.0% ✅ | 2.0% ✅ |
| $100,000 | 7.5% ⚠️ | 4.0% ⚠️ | 10.0% ⚠️ | 1.0% ✅ |
| $200,000 | 3.75% ❌ | 2.0% ❌ | 5.0% ❌ | 0.5% ✅ |
| $500,000 | 1.5% ❌ | 0.8% ❌ | 2.0% ❌ | 0.2% ✅ |

**Legend:**
- ✅ Appropriate allocation
- ⚠️ Under-allocated (suboptimal)
- ❌ Severely under-allocated (broken)

### Expected vs Actual Allocation at $200K Portfolio

| Component | Expected (%) | Expected ($) | Actual (Hardcoded) | Utilization |
|-----------|:------------:|:------------:|:------------------:|:-----------:|
| Swing Spread | 15% | $30,000 | $7,500 | 25% ❌ |
| Intraday Spread | 8% | $16,000 | $4,000 | 25% ❌ |
| Total Options | 25% | $50,000 | $10,000 | 20% ❌ |

**Conclusion:** At $200K, the options engine would only utilize 20-25% of its intended allocation due to hardcoded caps.

### Recommended Percentage-Based Values

| Parameter | Current (Fixed) | Recommended (%) | $50K Result | $200K Result |
|-----------|----------------:|:---------------:|:-----------:|:------------:|
| `SWING_SPREAD_MAX` | $7,500 | 15% | $7,500 | $30,000 |
| `INTRADAY_SPREAD_MAX` | $4,000 | 8% | $4,000 | $16,000 |
| `OPTIONS_MAX_MARGIN` | $10,000 | 20% | $10,000 | $40,000 |
| Margin Guard | $1,000 | 2% | $1,000 | $4,000 |

---

## Appendix C: Files Changed for Scalability (V3.0)

| File | Change | Status |
|------|--------|:------:|
| config.py | Added `OPTIONS_MAX_MARGIN_PCT = 0.20` | ✅ |
| config.py | Added `SWING_SPREAD_MAX_PCT = 0.15` | ✅ |
| config.py | Added `INTRADAY_SPREAD_MAX_PCT = 0.08` | ✅ |
| config.py | Added `OPTIONS_MIN_MARGIN_PCT = 0.02` | ✅ |
| main.py | Updated margin guard to use `OPTIONS_MIN_MARGIN_PCT` | ✅ |
| main.py | Updated margin cap to use `OPTIONS_MAX_MARGIN_PCT` | ✅ |
| options_engine.py | Updated swing debit sizing to use `SWING_SPREAD_MAX_PCT` | ✅ |
| options_engine.py | Updated credit spread sizing to use `SWING_SPREAD_MAX_PCT` | ✅ |
| options_engine.py | Updated intraday sizing to use `INTRADAY_SPREAD_MAX_PCT` | ✅ |

**Note:** Legacy hardcoded values kept for backwards compatibility but no longer used.
