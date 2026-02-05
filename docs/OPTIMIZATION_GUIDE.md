# Alpha NextGen V2 — Optimization Guide

> **Purpose:** Parameter reference for backtesting and performance tuning.
> **Version:** V2.33 "Thesis-Aligned"
> **Last Updated:** 2026-02-04

---

## Quick Reference: High-Impact Parameters

| Parameter | File | Current | Range | Impact |
|-----------|------|:-------:|-------|--------|
| `GOVERNOR_INTRADAY_OPTIONS_MIN_SCALE` | config.py | 0.50 | 0.25-1.0 | Options activity during drawdowns |
| `GOVERNOR_INTRADAY_OPTIONS_MIN_SCALE_BEARISH` | config.py | 0.25 | 0.0-0.75 | Bear options during drawdowns |
| `SPREAD_REGIME_BEARISH` | config.py | 45 | 40-55 | When bear spreads activate |
| `SPREAD_REGIME_BULLISH` | config.py | 60 | 55-70 | When bull spreads activate |
| `DRAWDOWN_GOVERNOR_STEP_THRESHOLD` | config.py | 0.03 | 0.02-0.05 | Governor step-down sensitivity |
| `KS_SKIP_DAYS` | config.py | 1 | 0-3 | Days blocked after kill switch |

---

## 1. Governor System (Drawdown Protection)

Controls position sizing during equity drawdowns from peak.

### Core Governor Parameters

```python
# config.py

# Step-down thresholds (cumulative from equity peak)
DRAWDOWN_GOVERNOR_STEP_THRESHOLD = 0.03   # 3% loss = step down to 75%
# At 6% loss = 50%, at 9% loss = 25%, at 12% loss = 0% (shutdown)

# Recovery requirement (scales with current level)
DRAWDOWN_GOVERNOR_RECOVERY_BASE = 0.08    # 8% at 100% scale
# Effective: 100%→8%, 75%→6%, 50%→4%, 25%→2%
```

### Options-Specific Governor Parameters (V2.32)

```python
# config.py

# ENTRY GATES — minimum governor scale to allow new options entries
GOVERNOR_INTRADAY_OPTIONS_MIN_SCALE = 0.50        # Bull/neutral options need 50%+
GOVERNOR_INTRADAY_OPTIONS_MIN_SCALE_BEARISH = 0.25 # Bear options allowed at 25%+

# SIZING CONTROLS — how governor affects options position sizes at EOD
GOVERNOR_OPTIONS_SIZING_FLOOR = 0.50    # Options never scaled below 50%
GOVERNOR_EXEMPT_BEARISH_OPTIONS = True  # Bear spreads keep full size (risk-reducing)
```

### Options Governor Matrix

| Governor Scale | Bull Options Entry | Bear Options Entry | Bull Sizing | Bear Sizing |
|:--------------:|:------------------:|:------------------:|:-----------:|:-----------:|
| 100% | ✅ Full | ✅ Full | 100% | 100% |
| 75% | ✅ Full | ✅ Full | 75% | 100% (exempt) |
| 50% | ✅ Full | ✅ Full | 50% (floor) | 100% (exempt) |
| 25% | ❌ Blocked | ✅ Full | 50% (floor) | 100% (exempt) |
| 0% | ❌ Blocked | ❌ Blocked | 0% | 0% |

### Optimization Notes

| Scenario | Recommendation |
|----------|----------------|
| More aggressive (bull markets) | Lower `STEP_THRESHOLD` to 0.02, raise `MIN_SCALE` to 0.75 |
| More defensive (bear markets) | Raise `STEP_THRESHOLD` to 0.04, lower `MIN_SCALE_BEARISH` to 0.0 |
| Faster recovery | Lower `RECOVERY_BASE` to 0.06 |

---

## 2. Kill Switch System (Emergency Exits)

Graduated response to daily losses.

### Kill Switch Parameters

```python
# config.py

# Tier thresholds (daily loss from prior close)
KS_TIER_1_PCT = 0.02       # 2% → REDUCE (50% sizing, block new options)
KS_TIER_2_PCT = 0.025      # 2.5% → TREND_EXIT (liquidate trend, keep spreads)
KS_TIER_3_PCT = 0.03       # 3% → FULL_EXIT (liquidate everything)

# Post-KS behavior
KS_SKIP_DAYS = 1           # Business days blocked after Tier 2/3
KS_TIER_1_BLOCK_NEW_OPTIONS = True  # Block new option entries at Tier 1
```

### Optimization Notes

| Scenario | Recommendation |
|----------|----------------|
| Tighter risk control | Lower all thresholds by 0.5% |
| More tolerance for volatility | Raise thresholds, increase `KS_SKIP_DAYS` to 2 |
| Options-focused | Set `KS_TIER_1_BLOCK_NEW_OPTIONS = False` |

---

## 3. Regime Engine (Market State Detection)

4-factor scoring determines market conditions (0-100 scale).

### Regime Thresholds

```python
# config.py

# Classification boundaries
REGIME_RISK_ON = 70        # Score >= 70 = aggressive
REGIME_NEUTRAL = 50        # Score 50-69 = balanced
REGIME_CAUTIOUS = 40       # Score 40-49 = defensive lean
REGIME_DEFENSIVE = 30      # Score 30-39 = defensive
# Score < 30 = RISK_OFF (no new longs)

# Factor weights (must sum to 1.0)
WEIGHT_TREND = 0.25        # MA200 position
WEIGHT_VIX = 0.20          # Implied volatility
WEIGHT_VOLATILITY = 0.15   # Realized volatility
WEIGHT_BREADTH = 0.20      # Market breadth
WEIGHT_CREDIT = 0.15       # Credit spreads
WEIGHT_CHOP = 0.05         # ADX (trend strength)

# Smoothing
REGIME_SMOOTHING_ALPHA = 0.30  # EMA alpha (higher = more reactive)
```

### Optimization Notes

| Scenario | Recommendation |
|----------|----------------|
| More reactive to changes | Raise `SMOOTHING_ALPHA` to 0.40 |
| Smoother transitions | Lower `SMOOTHING_ALPHA` to 0.20 |
| VIX-focused strategy | Raise `WEIGHT_VIX` to 0.30, reduce others |

---

## 4. Options Engine (Spread Trading)

### Direction Thresholds

```python
# config.py

# Spread direction based on regime
SPREAD_REGIME_BULLISH = 60      # Score > 60 → BULL_CALL spreads
SPREAD_REGIME_BEARISH = 45      # Score < 45 → BEAR_PUT spreads
SPREAD_REGIME_CRISIS = 30       # Score < 30 → No spreads (protective only)
# Score 45-60 = NEUTRAL (no trade)

# Exit thresholds
SPREAD_REGIME_EXIT_BULL = 45    # Exit bull spread if regime drops below
SPREAD_REGIME_EXIT_BEAR = 60    # Exit bear spread if regime rises above
```

### VASS (Volatility-Adaptive Strategy Selection)

```python
# config.py

# IV environment classification
VASS_IV_LOW_THRESHOLD = 15      # VIX < 15 = LOW IV
VASS_IV_HIGH_THRESHOLD = 25     # VIX > 25 = HIGH IV
# VIX 15-25 = MEDIUM IV

# Strategy routing by IV
# LOW IV (VIX < 15):  Debit spreads, monthly DTE (30-45)
# MEDIUM IV (15-25):  Debit spreads, weekly DTE (14-21)
# HIGH IV (VIX > 25): Credit spreads, weekly DTE (7-14)

# DTE ranges
SPREAD_DTE_MIN = 14             # Minimum days to expiration
SPREAD_DTE_MAX = 45             # Maximum days to expiration
VASS_DEBIT_DTE_LOW_IV = (30, 45)
VASS_DEBIT_DTE_MED_IV = (14, 21)
VASS_CREDIT_DTE_HIGH_IV = (7, 14)
```

### Spread Sizing

```python
# config.py

OPTIONS_ALLOCATION_PCT = 0.20         # 20% of portfolio for options
OPTIONS_SWING_ALLOCATION_PCT = 0.15   # 15% for swing spreads
SPREAD_WIDTH_TARGET = 5.0             # $5 strike width
SPREAD_MAX_CONTRACTS = 20             # Hard cap per spread
MIN_SPREAD_CONTRACTS = 2              # Minimum to enter
```

### Entry Quality

```python
# config.py

OPTIONS_ENTRY_SCORE_MIN = 2.0   # Minimum 4-factor score (0-4 scale)
# Factors: ADX strength, Momentum, IV Rank, Liquidity

# Credit spread minimums
CREDIT_SPREAD_MIN_CREDIT = 0.30           # Base: $0.30 per contract
CREDIT_SPREAD_MIN_CREDIT_HIGH_IV = 0.20   # When VIX > 30: $0.20
```

### Optimization Notes

| Scenario | Recommendation |
|----------|----------------|
| Earlier bear entries | Lower `SPREAD_REGIME_BEARISH` to 50-55 |
| Wider neutral zone | Raise `BEARISH` to 40, lower `BULLISH` to 65 |
| More credit spreads | Lower `VASS_IV_HIGH_THRESHOLD` to 20 |
| Higher quality entries | Raise `ENTRY_SCORE_MIN` to 2.5 |
| Larger positions | Raise `OPTIONS_ALLOCATION_PCT` to 0.25 |

---

## 5. Trend Engine (Core Positions)

MA200 + ADX confirmation for leveraged ETF positions.

### Entry/Exit Criteria

```python
# config.py

# Entry signals
ADX_ENTRY_THRESHOLD = 25       # ADX >= 25 for trend confirmation
ADX_PERIOD = 14                # ADX lookback period

# Trailing stop (Chandelier)
CHANDELIER_ATR_MULT = 3.0      # Stop = High - 3×ATR
CHANDELIER_ATR_PERIOD = 14     # ATR lookback

# Position sizing
TREND_QLD_WEIGHT = 0.20        # 20% QLD (2× Nasdaq)
TREND_SSO_WEIGHT = 0.15        # 15% SSO (2× S&P)
TREND_TNA_WEIGHT = 0.12        # 12% TNA (3× Russell)
TREND_FAS_WEIGHT = 0.08        # 8% FAS (3× Financials)
```

### Optimization Notes

| Scenario | Recommendation |
|----------|----------------|
| Fewer false entries | Raise `ADX_ENTRY_THRESHOLD` to 30 |
| Tighter stops | Lower `CHANDELIER_ATR_MULT` to 2.5 |
| More concentrated | Raise `QLD_WEIGHT` to 0.25, reduce others |

---

## 6. Hedge Engine (Defensive Overlay)

Regime-based TMF/PSQ allocation.

### Hedge Tiers

```python
# config.py

# Activation thresholds
HEDGE_LEVEL_1 = 40    # Regime < 40 → Light hedge
HEDGE_LEVEL_2 = 30    # Regime < 30 → Medium hedge
HEDGE_LEVEL_3 = 20    # Regime < 20 → Full hedge

# Allocations by tier
HEDGE_TMF_LIGHT = 0.10    # 10% TMF at LIGHT
HEDGE_TMF_MEDIUM = 0.15   # 15% TMF at MEDIUM
HEDGE_TMF_FULL = 0.20     # 20% TMF at FULL

HEDGE_PSQ_LIGHT = 0.00    # 0% PSQ at LIGHT
HEDGE_PSQ_MEDIUM = 0.05   # 5% PSQ at MEDIUM
HEDGE_PSQ_FULL = 0.10     # 10% PSQ at FULL

# Rebalance threshold
HEDGE_REBAL_THRESHOLD = 0.02  # Only rebalance if diff > 2%
```

### Optimization Notes

| Scenario | Recommendation |
|----------|----------------|
| Earlier hedging | Raise `HEDGE_LEVEL_1` to 50 |
| More aggressive hedging | Raise all `TMF_*` and `PSQ_*` values by 5% |
| Less whipsaw | Raise `REBAL_THRESHOLD` to 0.03 |

---

## 7. Startup Gate (Cold Start Protection)

Graduated entry during algorithm startup.

### Phase Durations

```python
# config.py

STARTUP_INDICATOR_WARMUP_DAYS = 5   # Days 1-5: Hedges only
STARTUP_OBSERVATION_DAYS = 5         # Days 6-10: Add bearish options
STARTUP_REDUCED_DAYS = 5             # Days 11-15: All at 50% size
# Day 16+: FULLY_ARMED (no restrictions)

STARTUP_REDUCED_MULTIPLIER = 0.50    # Size multiplier during REDUCED
```

### Optimization Notes

| Scenario | Recommendation |
|----------|----------------|
| Faster ramp-up | Reduce all `*_DAYS` to 3 |
| More conservative start | Raise `REDUCED_MULTIPLIER` to 0.25 |

---

## 8. Mean Reversion Engine (Intraday)

RSI oversold bounce strategy for TQQQ/SOXL.

### Entry Criteria

```python
# config.py

MR_RSI_PERIOD = 5              # RSI lookback
MR_RSI_OVERSOLD = 25           # Entry when RSI < 25
MR_RSI_EXIT = 50               # Exit when RSI > 50

MR_VIX_MAX = 30                # Block entries if VIX > 30
MR_MIN_DECLINE_PCT = 0.02      # Require 2% intraday decline

# Position sizing
MR_TQQQ_WEIGHT = 0.05          # 5% TQQQ
MR_SOXL_WEIGHT = 0.05          # 5% SOXL
```

### Optimization Notes

| Scenario | Recommendation |
|----------|----------------|
| More selective entries | Lower `RSI_OVERSOLD` to 20 |
| Earlier exits | Lower `RSI_EXIT` to 40 |
| Higher VIX tolerance | Raise `VIX_MAX` to 35 |

---

## 9. Backtest Date Ranges

### Key Test Periods

| Period | Dates | Character | Tests |
|--------|-------|-----------|-------|
| 2015 Full | 2015-01-01 to 2015-12-31 | Choppy, -12% correction | Kill switch, governor |
| 2017 Full | 2017-01-01 to 2017-12-31 | Strong bull, low VIX | Options activity |
| 2018 Q4 | 2018-10-01 to 2018-12-31 | -20% selloff | Hedges, bear spreads |
| 2020 Q1 | 2020-01-01 to 2020-03-31 | COVID crash | Panic mode, kill switch |
| 2022 Q1 | 2022-01-01 to 2022-03-31 | Bear market start | Direction switching |
| 2023 Full | 2023-01-01 to 2023-12-31 | Recovery rally | Bull performance |

### Setting Dates

```python
# main.py lines 194-195
self.SetStartDate(2017, 1, 1)
self.SetEndDate(2017, 12, 31)
```

---

## 10. Optimization Workflow

### Step 1: Baseline
Run current parameters on target period, record metrics.

### Step 2: Single Parameter Sweep
Change ONE parameter at a time, observe impact:
```
SPREAD_REGIME_BEARISH: [40, 45, 50, 55]
GOVERNOR_INTRADAY_OPTIONS_MIN_SCALE: [0.25, 0.50, 0.75, 1.0]
KS_TIER_3_PCT: [0.025, 0.03, 0.035, 0.04]
```

### Step 3: Key Metrics to Track

| Metric | Target | Notes |
|--------|--------|-------|
| Total Return | Maximize | Primary objective |
| Max Drawdown | < 15% | Risk constraint |
| Sharpe Ratio | > 1.5 | Risk-adjusted return |
| Trade Count | > 20/year | Activity level |
| Win Rate | > 50% | Quality of entries |

### Step 4: Multi-Period Validation
Test winning parameters on multiple periods (2015, 2017, 2020, 2022) to avoid overfitting.

---

## 11. Common Optimization Scenarios

### Scenario A: "More Options Trades"
```python
GOVERNOR_INTRADAY_OPTIONS_MIN_SCALE = 0.25
SPREAD_REGIME_BEARISH = 50  # Smaller neutral zone
OPTIONS_ENTRY_SCORE_MIN = 1.5  # Lower quality bar
```

### Scenario B: "Aggressive Bear Market"
```python
SPREAD_REGIME_BEARISH = 55  # Earlier bear entries
HEDGE_LEVEL_1 = 50  # Earlier hedging
KS_TIER_1_BLOCK_NEW_OPTIONS = False  # Keep options active
```

### Scenario C: "Conservative Bull Market"
```python
SPREAD_REGIME_BULLISH = 65  # Higher bar for bull entries
ADX_ENTRY_THRESHOLD = 30  # Stronger trends only
CHANDELIER_ATR_MULT = 2.5  # Tighter stops
```

### Scenario D: "Capital Preservation"
```python
DRAWDOWN_GOVERNOR_STEP_THRESHOLD = 0.02  # Earlier step-down
KS_TIER_3_PCT = 0.025  # Earlier full exit
OPTIONS_ALLOCATION_PCT = 0.10  # Smaller options exposure
```

---

## Appendix A: Parameter Location Index

| Category | File | Line Range |
|----------|------|------------|
| Governor | config.py | 420-430 |
| Kill Switch | config.py | 440-470 |
| Regime Thresholds | config.py | 65-85 |
| Options Spreads | config.py | 500-700 |
| VASS Strategy | config.py | 580-620 |
| Trend Engine | config.py | 200-250 |
| Hedge Engine | config.py | 255-290 |
| Mean Reversion | config.py | 300-340 |
| Startup Gate | config.py | 350-380 |

---

## Appendix B: Thesis-Aligned Configuration Matrix

> **Investment Thesis:** The system adapts behavior based on market conditions measured by regime score (0-100).

### Regime Definitions

| Regime | Score Range | Strategy |
|--------|:-----------:|----------|
| **Bull** | 70-100 | Full leverage via trend-following + bullish CALL spreads |
| **Neutral** | 50-69 | Selective entries, NO options (dead zone) |
| **Cautious/Defensive** | 30-49 | Hedges active, bearish PUT spreads, trend entries gated |
| **Bear** | 0-29 | Maximum hedges (TMF 20% + PSQ 10%), longs blocked, PUT spreads active |

### Thesis-Aligned Parameters

```python
# ============================================================
# THESIS-ALIGNED CONFIG — All Regimes Covered
# ============================================================

# OPTIONS DIRECTION THRESHOLDS
# Bull (70+): CALL spreads active
# Neutral (50-69): No options (dead zone)
# Cautious/Bear (<50): PUT spreads active
SPREAD_REGIME_BULLISH = 70   # CALL spreads ONLY in Bull (regime > 70)
SPREAD_REGIME_BEARISH = 50   # PUT spreads in Cautious + Bear (regime < 50)
SPREAD_REGIME_CRISIS = 0     # DISABLED — PUT spreads work in all bear regimes

# HEDGE ACTIVATION (graduated response)
# Cautious (40-49): Light hedge
# Defensive (30-39): Medium hedge
# Bear (0-29): Full hedge
HEDGE_LEVEL_1 = 50           # Light hedge starts at regime < 50
HEDGE_LEVEL_2 = 40           # Medium hedge at regime < 40
HEDGE_LEVEL_3 = 30           # FULL hedge at regime < 30

# LONG ENTRY GATING
# Only allow trend/MR entries in Neutral or better (50+)
TREND_ENTRY_REGIME_MIN = 50  # Trend blocked below 50
MR_REGIME_MIN = 50           # Mean Reversion blocked below 50
```

### Verification Matrix

| Regime | Score | Trend | MR | CALL Spreads | PUT Spreads | Hedge Level |
|--------|:-----:|:-----:|:--:|:------------:|:-----------:|:-----------:|
| **Bull** | 70+ | ✅ | ✅ | ✅ | ❌ | NONE |
| **Neutral** | 50-69 | ✅ | ✅ | ❌ | ❌ | NONE |
| **Cautious** | 40-49 | ❌ | ❌ | ❌ | ✅ | LIGHT (10% TMF) |
| **Defensive** | 30-39 | ❌ | ❌ | ❌ | ✅ | MEDIUM (15% TMF, 5% PSQ) |
| **Bear** | 0-29 | ❌ | ❌ | ❌ | ✅ | FULL (20% TMF, 10% PSQ) |

### Changes from Default Configuration

| Parameter | Default | Thesis-Aligned | Delta | Rationale |
|-----------|:-------:|:--------------:|:-----:|-----------|
| `SPREAD_REGIME_BULLISH` | 60 | **70** | +10 | CALL spreads only in true Bull market |
| `SPREAD_REGIME_BEARISH` | 45 | **50** | +5 | PUT spreads cover full Cautious zone |
| `SPREAD_REGIME_CRISIS` | 30 | **0** | -30 | Remove crisis block — PUTs should work in Bear |
| `HEDGE_LEVEL_1` | 40 | **50** | +10 | Start hedging at Cautious threshold |
| `HEDGE_LEVEL_2` | 30 | **40** | +10 | Medium hedge in Defensive zone |
| `HEDGE_LEVEL_3` | 20 | **30** | +10 | Full hedge covers entire Bear range |
| `TREND_ENTRY_REGIME_MIN` | 40 | **50** | +10 | Gate trend entries in Cautious zone |
| `MR_REGIME_MIN` | 40 | **50** | +10 | Gate MR entries in Cautious zone |

### Visual: Regime-to-Engine Mapping

```
REGIME SCORE:  0 -------- 30 -------- 40 -------- 50 -------- 70 -------- 100
               |          |           |           |           |           |
TREND/MR:      |<-------- BLOCKED ----------------->|<------ ALLOWED ----->|
               |                                    |                      |
CALL SPREADS:  |<-------------- BLOCKED --------------------------->|<-YES->|
               |                                                    |      |
PUT SPREADS:   |<---------------- ALLOWED ----------------->|<-- BLOCKED -->|
               |                                            |              |
HEDGES:        |<- FULL ->|<- MEDIUM ->|<- LIGHT ->|<----- NONE --------->|
               |          |            |           |                       |
               0         30           40          50                      70
```

### Backtest Validation Sequence

After applying thesis-aligned parameters, validate across multiple market regimes:

| Test | Period | Primary Regime | Validates |
|------|--------|----------------|-----------|
| 1 | 2017 Full | Bull (70+) | Trend + CALL spread performance |
| 2 | 2015 Aug-Oct | Cautious/Defensive | PUT spreads + hedge activation |
| 3 | 2018 Q4 | Bear (<30) | Full hedge + PUT spreads in crash |
| 4 | 2020 Mar | Bear (<30) | Panic mode + kill switch behavior |
| 5 | 2022 Q1 | Defensive/Bear | Direction switching, hedge timing |

### Key Metrics to Compare

| Metric | Default Config | Thesis-Aligned | Target |
|--------|:--------------:|:--------------:|:------:|
| 2017 Return | TBD% | TBD% | > 50% |
| 2015 Max DD | TBD% | TBD% | < 15% |
| 2018 Q4 Return | TBD% | TBD% | > -10% |
| PUT Spread Win Rate | TBD% | TBD% | > 50% |
| Hedge Activation Days | TBD | TBD | Timely |
