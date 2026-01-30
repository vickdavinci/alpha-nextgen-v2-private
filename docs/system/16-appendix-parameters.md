# Section 16: Appendix - Parameters

## 16.1 Purpose

This appendix consolidates **all tunable parameters** from across the Alpha NextGen system into a single reference. All values should be defined in `config.py` for easy modification.

---

## 16.2 Capital Engine Parameters

### Phase Definitions

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `PHASE_SEED_MIN` | $50,000 | Minimum equity for SEED phase |
| `PHASE_SEED_MAX` | $99,999 | Maximum equity for SEED phase |
| `PHASE_GROWTH_MIN` | $100,000 | Minimum equity for GROWTH phase |
| `PHASE_GROWTH_MAX` | $499,999 | Maximum equity for GROWTH phase |

### Phase Transition Rules

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `UPWARD_TRANSITION_DAYS` | 5 | Consecutive days above threshold for upgrade |
| `DOWNWARD_TRANSITION_DAYS` | 1 | Days below threshold for downgrade (immediate) |

### Position Limits by Phase

| Parameter | SEED | GROWTH | Description |
|-----------|:----:|:------:|-------------|
| `MAX_SINGLE_POSITION_PCT` | 50% | 40% | Maximum single position size |
| `TARGET_VOLATILITY` | 20% | 20% | Target annualized volatility |
| `KILL_SWITCH_PCT` | 3% | 3% | Daily loss threshold |

### Lockbox Parameters

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `LOCKBOX_MILESTONE_1` | $100,000 | First lockbox trigger |
| `LOCKBOX_MILESTONE_2` | $200,000 | Second lockbox trigger |
| `LOCKBOX_LOCK_PCT` | 10% | Percentage of equity to lock |

---

## 16.3 Regime Engine Parameters

### Factor Weights

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `WEIGHT_TREND` | 0.45 | Trend factor weight (45%) - increased to follow price trend during narrow rallies |
| `WEIGHT_VOLATILITY` | 0.25 | Volatility factor weight (25%) |
| `WEIGHT_BREADTH` | 0.15 | Breadth factor weight (15%) - reduced to prevent blocking during mega-cap rallies |
| `WEIGHT_CREDIT` | 0.15 | Credit factor weight (15%) |

### Smoothing

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `REGIME_SMOOTHING_ALPHA` | 0.30 | Exponential smoothing factor |

### Regime Score Thresholds

| Parameter | Value | State |
|-----------|:-----:|-------|
| `REGIME_RISK_ON` | 70 | RISK_ON (70-100) |
| `REGIME_NEUTRAL` | 50 | NEUTRAL (50-69) |
| `REGIME_CAUTIOUS` | 40 | CAUTIOUS (40-49) |
| `REGIME_DEFENSIVE` | 30 | DEFENSIVE (30-39) |
| `REGIME_RISK_OFF` | 0 | RISK_OFF (0-29) |

### Trend Factor Parameters

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `SMA_FAST` | 20 | Fast moving average period |
| `SMA_MED` | 50 | Medium moving average period |
| `SMA_SLOW` | 200 | Slow moving average period |
| `EXTENDED_THRESHOLD` | 1.05 | 5% above SMA200 = extended |
| `OVERSOLD_THRESHOLD` | 0.95 | 5% below SMA200 = oversold |

### Volatility Factor Parameters

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `VOL_LOOKBACK` | 20 | Realized volatility lookback (days) |
| `VOL_PERCENTILE_LOOKBACK` | 252 | Percentile ranking lookback (days) |

### Breadth Factor Parameters

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `BREADTH_LOOKBACK` | 20 | RSP vs SPY comparison period |

### Credit Factor Parameters

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `CREDIT_LOOKBACK` | 20 | HYG vs IEF comparison period |

---

## 16.4 Cold Start Engine Parameters

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `COLD_START_DAYS` | 5 | Number of days in cold start mode |
| `WARM_ENTRY_SIZE_MULT` | 0.50 | Position size multiplier (50%) |
| `WARM_ENTRY_TIME` | 10:00 ET | Earliest warm entry time |
| `WARM_REGIME_MIN` | 50 | Minimum regime score (exclusive) |
| `WARM_QLD_THRESHOLD` | 60 | Score above which QLD selected |
| `WARM_MIN_SIZE` | $2,000 | Minimum warm entry position |

---

## 16.5 Trend Engine Parameters (V2)

### MA200 + ADX Entry Parameters

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `MA200_PERIOD` | 200 | Moving average period for trend direction |
| `ADX_PERIOD` | 14 | ADX period for momentum strength |
| `ADX_ENTRY_THRESHOLD` | 25 | Minimum ADX for entry (score >= 0.50) |
| `TREND_ADX_EXIT_THRESHOLD` | 20 | ADX below this triggers exit consideration |

### ADX Scoring Thresholds

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `ADX_WEAK_THRESHOLD` | 20 | Below = 0.25 score |
| `ADX_MODERATE_THRESHOLD` | 25 | 20-25 = 0.50 score |
| `ADX_STRONG_THRESHOLD` | 35 | 25-35 = 0.75, above = 1.0 score |

### Chandelier Stop Parameters (V2.1)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `ATR_PERIOD` | 14 | ATR calculation period |
| `CHANDELIER_BASE_MULT` | 3.0 | Initial multiplier (profit < 10%) |
| `CHANDELIER_TIGHT_MULT` | 2.5 | Medium multiplier (profit 10-20%) |
| `CHANDELIER_TIGHTER_MULT` | 2.0 | Tight multiplier (profit > 20%) |
| `PROFIT_TIGHT_PCT` | 0.10 | First tightening threshold (10%) |
| `PROFIT_TIGHTER_PCT` | 0.20 | Second tightening threshold (20%) |

### Entry/Exit Thresholds

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `TREND_ENTRY_REGIME_MIN` | 40 | Minimum regime for entry |
| `TREND_EXIT_REGIME` | 30 | Regime score forcing exit |

### V2 Entry Conditions Summary

| Condition | Requirement |
|-----------|-------------|
| Price vs MA200 | Close > MA200 |
| ADX Score | ADX >= 25 (score >= 0.50) |
| Regime | Score >= 40 |

### V2 Exit Conditions Summary

| Condition | Trigger |
|-----------|---------|
| MA200 Cross | Close < MA200 |
| ADX Weakness | ADX < 20 |
| Chandelier Stop | Price < Highest High − (ATR × multiplier) |
| Regime Exit | Score < 30 |

---

## 16.6 Mean Reversion Engine Parameters (V2.1)

### RSI Parameters

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `RSI_PERIOD` | 5 | Fast RSI period |
| `RSI_THRESHOLD` | VIX-adjusted | Oversold threshold (20-30) |

### Entry Conditions

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `MR_DROP_THRESHOLD` | 0.025 | 2.5% drop from open |
| `MR_VOLUME_MULT` | 1.2 | Volume > 1.2× average |
| `MR_WINDOW_START` | 10:00 ET | Earliest entry time |
| `MR_WINDOW_END` | 15:00 ET | Latest entry time |
| `MR_REGIME_MIN` | 40 | Minimum regime score |

### Exit Conditions

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `MR_TARGET_PCT` | 0.02 | +2% profit target |
| `MR_STOP_PCT` | VIX-adjusted | 4-8% stop loss |
| `MR_FORCE_EXIT_TIME` | 15:45 ET | Mandatory close time |

### VIX Regime Filter Parameters (V2.1)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `VIX_NORMAL_THRESHOLD` | 20 | Below = NORMAL regime |
| `VIX_CAUTION_THRESHOLD` | 30 | 20-30 = CAUTION regime |
| `VIX_HIGH_RISK_THRESHOLD` | 40 | 30-40 = HIGH_RISK regime |

### VIX-Adjusted Parameters Table

| VIX Level | Regime | Allocation | RSI Threshold | Stop Loss |
|:---------:|--------|:----------:|:-------------:|:---------:|
| < 20 | NORMAL | 10% | RSI < 30 | 8% |
| 20-30 | CAUTION | 5% | RSI < 25 | 6% |
| 30-40 | HIGH_RISK | 2% | RSI < 20 | 4% |
| > 40 | CRASH | **0%** (disabled) | — | — |

---

## 16.7 Hedge Engine Parameters

### Regime Thresholds

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `HEDGE_LEVEL_1` | 40 | Score below which hedging begins |
| `HEDGE_LEVEL_2` | 30 | Score below which medium hedge |
| `HEDGE_LEVEL_3` | 20 | Score below which full hedge |

### TMF Allocation

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `TMF_LIGHT` | 0.10 | TMF at DEFENSIVE (10%) |
| `TMF_MEDIUM` | 0.15 | TMF at RISK_OFF moderate (15%) |
| `TMF_FULL` | 0.20 | TMF at RISK_OFF severe (20%) |

### PSQ Allocation

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `PSQ_MEDIUM` | 0.05 | PSQ at RISK_OFF moderate (5%) |
| `PSQ_FULL` | 0.10 | PSQ at RISK_OFF severe (10%) |

### Rebalancing

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `HEDGE_REBAL_THRESHOLD` | 0.02 | 2% difference to rebalance |

---

## 16.8 Yield Sleeve Parameters

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `SHV_MIN_TRADE` | $2,000 | Minimum cash for SHV purchase |
| `SHV_MAX_ALLOCATION` | None | No maximum (fills available cash) |

---

## 16.8.1 Options Engine Parameters (V2.1)

### Allocation

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `OPTIONS_ALLOCATION_MIN` | 0.20 | Minimum allocation to options (20%) |
| `OPTIONS_ALLOCATION_MAX` | 0.30 | Maximum allocation to options (30%) |

### 4-Factor Entry Scoring

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `OPTIONS_ADX_PERIOD` | 14 | ADX lookback period |
| `OPTIONS_MA_PERIOD` | 200 | Moving average for momentum |
| `OPTIONS_IV_LOOKBACK` | 252 | IV rank lookback (1 year) |
| `OPTIONS_MAX_SPREAD_PCT` | 0.10 | Max bid-ask spread (10%) |
| `OPTIONS_ENTRY_SCORE_MIN` | 3.0 | Minimum score for entry (out of 4.0) |

### ADX Scoring (Factor 1)

| ADX Value | Score |
|:---------:|:-----:|
| < 20 | 0.00 |
| 20-25 | 0.50 |
| 25-30 | 0.75 |
| > 30 | 1.00 |

### IV Rank Scoring (Factor 3)

| IV Rank | Score |
|:-------:|:-----:|
| 0-20% | 0.25 |
| 20-40% | 0.50 |
| 40-60% | 0.75 |
| 60-80% | 1.00 |
| 80-100% | 0.75 |

### Tiered Stop Losses

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `OPTIONS_STOP_TIER_1` | 0.20 | Score 3.0-3.25: 20% stop |
| `OPTIONS_STOP_TIER_2` | 0.22 | Score 3.25-3.5: 22% stop |
| `OPTIONS_STOP_TIER_3` | 0.25 | Score 3.5-3.75: 25% stop |
| `OPTIONS_STOP_TIER_4` | 0.30 | Score 3.75-4.0: 30% stop |
| `OPTIONS_PROFIT_TARGET_PCT` | 0.50 | +50% profit target |

### Time Constraints

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `OPTIONS_ENTRY_START` | 10:00 ET | Earliest entry time |
| `OPTIONS_ENTRY_END` | 14:30 ET | Latest entry time |
| `OPTIONS_LATE_DAY_TIME` | 14:30 ET | Force tight stops after this |
| `OPTIONS_FORCE_EXIT_HOUR` | 15 | Force close hour (3 PM ET) |
| `OPTIONS_FORCE_EXIT_MINUTE` | 45 | Force close minute (:45) |

### Greeks Monitoring (Circuit Breaker Level 5)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `CB_DELTA_MAX` | 0.80 | Max delta exposure per position |
| `CB_GAMMA_WARNING` | 0.05 | Gamma warning threshold near expiry |
| `CB_VEGA_MAX` | 0.50 | Max vega exposure |
| `CB_THETA_WARNING` | -0.02 | Daily theta decay warning (-2%) |

### Contract Selection

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `OPTIONS_DTE_MIN` | 1 | Minimum days to expiry |
| `OPTIONS_DTE_MAX` | 4 | Maximum days to expiry |
| `OPTIONS_DELTA_MIN` | 0.40 | Minimum delta (ATM range) |
| `OPTIONS_DELTA_MAX` | 0.60 | Maximum delta (ATM range) |
| `OPTIONS_MIN_PREMIUM` | 0.50 | Minimum premium per contract ($0.50) |

### V2.1.1 Dual-Mode Architecture

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `OPTIONS_TOTAL_ALLOCATION` | 0.20 | Total options budget (20%) |
| `OPTIONS_SWING_ALLOCATION` | 0.15 | Swing Mode allocation (15%) |
| `OPTIONS_INTRADAY_ALLOCATION` | 0.05 | Intraday Mode allocation (5%) |

### V2.1.1 DTE Boundaries

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `OPTIONS_SWING_DTE_MIN` | 5 | Minimum DTE for Swing Mode |
| `OPTIONS_SWING_DTE_MAX` | 45 | Maximum DTE for Swing Mode |
| `OPTIONS_INTRADAY_DTE_MIN` | 0 | Minimum DTE for Intraday Mode |
| `OPTIONS_INTRADAY_DTE_MAX` | 2 | Maximum DTE for Intraday Mode |

### V2.1.1 VIX Direction Thresholds (Micro Regime Engine)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `VIX_DIRECTION_FALLING_FAST` | -5.0% | Strong recovery threshold |
| `VIX_DIRECTION_FALLING` | -2.0% | Recovery threshold |
| `VIX_DIRECTION_STABLE_LOW` | -2.0% | Stable range lower bound |
| `VIX_DIRECTION_STABLE_HIGH` | 2.0% | Stable range upper bound |
| `VIX_DIRECTION_RISING` | 5.0% | Fear building threshold |
| `VIX_DIRECTION_RISING_FAST` | 10.0% | Panic emerging threshold |
| `VIX_DIRECTION_SPIKING` | 10.0% | Crash mode threshold |

### V2.1.1 VIX Level Thresholds

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `VIX_LEVEL_LOW_MAX` | 20 | VIX < 20: Normal, mean reversion works |
| `VIX_LEVEL_MEDIUM_MAX` | 25 | VIX 20-25: Caution zone |
| (VIX > 25) | — | Elevated, momentum dominates |

### V2.1.1 Micro Regime Score Components

| Component | Score Range | Description |
|-----------|:-----------:|-------------|
| VIX Level | 0-25 | Lower VIX = higher score |
| VIX Direction | -10 to +20 | Falling = higher, Spiking/Whipsaw = penalty |
| QQQ Move | 0-20 | Sweet spot at 0.8-1.25% |
| Move Velocity | 0-15 | Gradual moves score higher |

### V2.1.1 Tiered VIX Monitoring

| Layer | Interval | Purpose |
|-------|:--------:|---------|
| Layer 1 | 5 min | Spike detection (VIX > 5% change) |
| Layer 2 | 15 min | Direction assessment |
| Layer 3 | 60 min | Whipsaw detection (reversal count) |
| Layer 4 | 30 min | Full regime classification |

### V2.1.1 Intraday Strategy Parameters

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `INTRADAY_DEBIT_FADE_MIN_SCORE` | 50 | Minimum micro score for debit fade |
| `INTRADAY_DEBIT_FADE_MIN_MOVE` | 1.0% | Minimum QQQ move |
| `INTRADAY_DEBIT_FADE_VIX_MAX` | 25 | Maximum VIX for debit fade |
| `INTRADAY_CREDIT_MIN_VIX` | 18 | Minimum VIX for credit spreads |
| `INTRADAY_ITM_MIN_VIX` | 25 | Minimum VIX for ITM momentum |
| `INTRADAY_ITM_MIN_MOVE` | 0.8% | Minimum QQQ move for ITM |
| `INTRADAY_FORCE_EXIT_TIME` | 15:30 ET | Intraday positions must close |

### V2.1.1 Swing Mode Simple Filters

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `SWING_TIME_WINDOW_START` | 10:00 ET | Swing entry window start |
| `SWING_TIME_WINDOW_END` | 14:30 ET | Swing entry window end |
| `SWING_GAP_THRESHOLD` | 1.0% | Skip if SPY gaps > 1.0% |
| `SWING_EXTREME_SPY_DROP` | -2.0% | Pause if SPY drops > 2% |
| `SWING_EXTREME_VIX_SPIKE` | 15.0% | Pause if VIX spikes > 15% |

---

## 16.8.2 OCO Manager Parameters (V2.1)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `OCO_STATE_KEY` | "oco_state" | ObjectStore key for persistence |
| `OCO_RECONCILE_ON_START` | True | Verify orders on restart |
| `OCO_CANCEL_TIMEOUT_SEC` | 30 | Timeout for cancel confirmation |

---

## 16.9 Portfolio Router Parameters

### Exposure Group Limits

| Group | Max Net Long | Max Net Short | Max Gross |
|-------|:------------:|:-------------:|:---------:|
| `NASDAQ_BETA` | 50% | 30% | 75% |
| `SPY_BETA` | 40% | 0% | 40% |
| `SMALL_CAP_BETA` | 25% | 0% | 25% |
| `FINANCIALS_BETA` | 15% | 0% | 15% |
| `RATES` | 40% | 0% | 40% |

### Group Membership

| Symbol | Group | Type | Allocation |
|--------|-------|------|:----------:|
| TQQQ | NASDAQ_BETA | 3× Long Nasdaq | 5% (MR) |
| QLD | NASDAQ_BETA | 2× Long Nasdaq | 20% (Trend) |
| SOXL | NASDAQ_BETA | 3× Long Semi | 5% (MR) |
| PSQ | NASDAQ_BETA | 1× Inverse Nasdaq | (Hedge) |
| SSO | SPY_BETA | 2× Long S&P | 15% (Trend) |
| TNA | SMALL_CAP_BETA | 3× Long Russell 2000 | 12% (Trend) |
| FAS | FINANCIALS_BETA | 3× Long Financials | 8% (Trend) |
| TMF | RATES | 3× Long Treasury | (Hedge) |
| SHV | RATES | Short Treasury | (Yield) |

### Trade Thresholds

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `MIN_TRADE_VALUE` | $2,000 | Minimum position value |
| `MIN_SHARE_DELTA` | 1 | Minimum shares to trade |

---

## 16.10 Risk Engine Parameters

### Kill Switch

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `KILL_SWITCH_PCT` | 0.03 | 3% daily loss threshold |

### Panic Mode

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `PANIC_MODE_PCT` | 0.04 | 4% SPY intraday drop |

### Weekly Breaker

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `WEEKLY_BREAKER_PCT` | 0.05 | 5% week-to-date loss |
| `WEEKLY_SIZE_REDUCTION` | 0.50 | 50% sizing reduction |

### Gap Filter

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `GAP_FILTER_PCT` | 0.015 | 1.5% gap down threshold |

### Vol Shock

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `VOL_SHOCK_ATR_MULT` | 3.0 | ATR multiplier for trigger |
| `VOL_SHOCK_PAUSE_MIN` | 15 | Minutes to pause |

### Time Guard

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `TIME_GUARD_START` | 13:55 ET | Start of blocked window |
| `TIME_GUARD_END` | 14:10 ET | End of blocked window |

---

## 16.11 Execution Engine Parameters

### Timing

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `MOO_SUBMISSION_TIME` | 15:45 ET | When to submit MOO orders |
| `MOO_FALLBACK_CHECK` | 09:31 ET | When to verify MOO fills |
| `MARKET_ORDER_TIMEOUT` | 60 sec | Warning threshold |
| `CONNECTION_TIMEOUT` | 5 min | Halt new orders threshold |

---

## 16.12 Scheduling Parameters

### Scheduled Events

| Event | Time (ET) | Description |
|-------|-----------|-------------|
| `PRE_MARKET_SETUP` | 09:25 | Set baselines, load state |
| `SOD_BASELINE` | 09:33 | Set equity_sod, check gap |
| `WARM_ENTRY_CHECK` | 10:00 | Cold start warm entry |
| `TIME_GUARD_START` | 13:55 | Block entries |
| `TIME_GUARD_END` | 14:10 | Resume entries |
| `MR_FORCE_CLOSE` | 15:45 | Close MR positions |
| `EOD_PROCESSING` | 15:45 | Run all EOD logic |
| `WEEKLY_RESET` | Mon 09:30 | Reset weekly breaker |

---

## 16.13 Indicator Warmup Parameters

| Indicator | Warmup Days | Description |
|-----------|:-----------:|-------------|
| SMA(20) | 20 | Fast moving average |
| SMA(50) | 50 | Medium moving average |
| SMA(200) | 200 | Slow moving average (MA200 for Trend Engine) |
| ADX(14) | 14 | Average Directional Index (Trend Engine entry) |
| RSI(5) | 10 | Fast RSI (Mean Reversion) |
| ATR(14) | 14 | Average True Range (Chandelier stops) |
| Realized Vol | 252 | For percentile ranking (Regime Engine) |

**Minimum warmup period: 252 trading days** to ensure all indicators are fully populated.

---

## 16.14 Symbol Configuration

### Traded Symbols

| Symbol | Description | Strategy | Allocation |
|--------|-------------|----------|:----------:|
| QLD | 2× Nasdaq | Trend, Warm Entry | 20% |
| SSO | 2× S&P 500 | Trend, Warm Entry | 15% |
| TNA | 3× Russell 2000 | Trend | 12% |
| FAS | 3× Financials | Trend | 8% |
| TQQQ | 3× Nasdaq | Mean Reversion | 5% |
| SOXL | 3× Semiconductor | Mean Reversion | 5% |
| TMF | 3× Treasury | Hedge | 0-20% |
| PSQ | 1× Inverse Nasdaq | Hedge | 0-10% |
| SHV | Short Treasury | Yield | Remainder |

### Symbol Liquidity Requirements

| Symbol | AUM | Daily Volume | Max Bid-Ask Spread |
|--------|:---:|:------------:|:------------------:|
| QLD | $4.2B | High | 0.03% |
| SSO | $3.8B | High | 0.03% |
| TNA | $2.16B | $472M | 0.05% |
| FAS | $2.55B | ~374K shares | 0.05% |
| TQQQ | $19B | Very High | 0.02% |
| SOXL | $8B | Very High | 0.03% |

### Proxy Symbols (Data Only)

| Symbol | Description | Used For |
|--------|-------------|----------|
| SPY | S&P 500 ETF | Regime trend, panic mode, gap filter, vol shock |
| RSP | Equal-Weight S&P | Regime breadth |
| HYG | High-Yield Corporate | Regime credit |
| IEF | 7-10 Year Treasury | Regime credit |

---

## 16.15 config.py Template

```python
"""
Alpha NextGen Configuration
All tunable parameters in one place.
"""

# =============================================================================
# CAPITAL ENGINE
# =============================================================================

# Phase Definitions
PHASE_SEED_MIN = 50_000
PHASE_SEED_MAX = 99_999
PHASE_GROWTH_MIN = 100_000
PHASE_GROWTH_MAX = 499_999

# Phase Transitions
UPWARD_TRANSITION_DAYS = 5
DOWNWARD_TRANSITION_DAYS = 1  # Immediate

# Position Limits
MAX_SINGLE_POSITION_PCT = {
    "SEED": 0.50,
    "GROWTH": 0.40
}

TARGET_VOLATILITY = 0.20
KILL_SWITCH_PCT_BY_PHASE = {
    "SEED": 0.03,
    "GROWTH": 0.03
}

# Lockbox
LOCKBOX_MILESTONES = [100_000, 200_000]
LOCKBOX_LOCK_PCT = 0.10

# =============================================================================
# REGIME ENGINE
# =============================================================================

# Factor Weights (adjusted to reduce breadth sensitivity during narrow rallies)
WEIGHT_TREND = 0.45      # Increased from 0.35
WEIGHT_VOLATILITY = 0.25
WEIGHT_BREADTH = 0.15    # Reduced from 0.25
WEIGHT_CREDIT = 0.15

# Smoothing
REGIME_SMOOTHING_ALPHA = 0.30

# Thresholds
REGIME_RISK_ON = 70
REGIME_NEUTRAL = 50
REGIME_CAUTIOUS = 40
REGIME_DEFENSIVE = 30

# Trend Factor
SMA_FAST = 20
SMA_MED = 50
SMA_SLOW = 200
EXTENDED_THRESHOLD = 1.05
OVERSOLD_THRESHOLD = 0.95

# Volatility Factor
VOL_LOOKBACK = 20
VOL_PERCENTILE_LOOKBACK = 252

# Breadth & Credit
BREADTH_LOOKBACK = 20
CREDIT_LOOKBACK = 20

# =============================================================================
# COLD START ENGINE
# =============================================================================

COLD_START_DAYS = 5
WARM_ENTRY_SIZE_MULT = 0.50
WARM_ENTRY_TIME = "10:00"
WARM_REGIME_MIN = 50
WARM_QLD_THRESHOLD = 60
WARM_MIN_SIZE = 2_000

# =============================================================================
# TREND ENGINE (V2 - MA200 + ADX)
# =============================================================================

# V2 Entry: MA200 + ADX Confirmation
MA200_PERIOD = 200  # Long-term trend baseline
ADX_PERIOD = 14  # Average Directional Index for momentum confirmation
ADX_ENTRY_THRESHOLD = 25  # Minimum ADX for entry (score_adx >= 0.50)
ADX_STRONG_THRESHOLD = 35  # ADX for highest confidence

# ADX Scoring Thresholds (V2.1 spec)
# ADX < 20: 0.25 (weak)
# ADX 20-25: 0.50 (moderate)
# ADX 25-35: 0.75 (strong)
# ADX >= 35: 1.00 (very strong)
ADX_WEAK_THRESHOLD = 20
ADX_MODERATE_THRESHOLD = 25

# Chandelier Stop
ATR_PERIOD = 14
CHANDELIER_BASE_MULT = 3.0
CHANDELIER_TIGHT_MULT = 2.5  # Updated per V2.1: 2.5x for profit 10-20%
CHANDELIER_TIGHTER_MULT = 2.0  # Updated per V2.1: 2.0x for profit 20%+
PROFIT_TIGHT_PCT = 0.10  # Updated per V2.1: tighten at 10%
PROFIT_TIGHTER_PCT = 0.20  # Updated per V2.1: tighten more at 20%

# Entry/Exit
TREND_ENTRY_REGIME_MIN = 40
TREND_EXIT_REGIME = 30
TREND_ADX_EXIT_THRESHOLD = 20  # Exit if ADX drops below this

# =============================================================================
# MEAN REVERSION ENGINE (V2.1 - VIX Filter)
# =============================================================================

# RSI
RSI_PERIOD = 5
# RSI_THRESHOLD is VIX-adjusted (see VIX_MR_PARAMS below)

# Entry Conditions
MR_DROP_THRESHOLD = 0.025
MR_VOLUME_MULT = 1.2
MR_WINDOW_START = "10:00"
MR_WINDOW_END = "15:00"
MR_REGIME_MIN = 40

# Exit Conditions
MR_TARGET_PCT = 0.02
# MR_STOP_PCT is VIX-adjusted (see VIX_MR_PARAMS below)
MR_FORCE_EXIT_TIME = "15:45"

# VIX Regime Filter (V2.1)
VIX_NORMAL_THRESHOLD = 20
VIX_CAUTION_THRESHOLD = 30
VIX_HIGH_RISK_THRESHOLD = 40

# VIX-Adjusted MR Parameters
VIX_MR_PARAMS = {
    "NORMAL":    {"allocation": 0.10, "rsi_threshold": 30, "stop_pct": 0.08},  # VIX < 20
    "CAUTION":   {"allocation": 0.05, "rsi_threshold": 25, "stop_pct": 0.06},  # VIX 20-30
    "HIGH_RISK": {"allocation": 0.02, "rsi_threshold": 20, "stop_pct": 0.04},  # VIX 30-40
    "CRASH":     {"allocation": 0.00, "rsi_threshold": 0,  "stop_pct": 0.00},  # VIX > 40 (disabled)
}

# =============================================================================
# HEDGE ENGINE
# =============================================================================

# Thresholds
HEDGE_LEVEL_1 = 40
HEDGE_LEVEL_2 = 30
HEDGE_LEVEL_3 = 20

# TMF Allocation
TMF_LIGHT = 0.10
TMF_MEDIUM = 0.15
TMF_FULL = 0.20

# PSQ Allocation
PSQ_MEDIUM = 0.05
PSQ_FULL = 0.10

# Rebalancing
HEDGE_REBAL_THRESHOLD = 0.02

# =============================================================================
# YIELD SLEEVE
# =============================================================================

SHV_MIN_TRADE = 2_000

# =============================================================================
# OPTIONS ENGINE (V2.1)
# =============================================================================

# Allocation
OPTIONS_ALLOCATION_MIN = 0.20  # 20% minimum
OPTIONS_ALLOCATION_MAX = 0.30  # 30% maximum

# 4-Factor Entry Scoring
OPTIONS_ADX_PERIOD = 14
OPTIONS_MA_PERIOD = 200
OPTIONS_IV_LOOKBACK = 252
OPTIONS_MAX_SPREAD_PCT = 0.10
OPTIONS_ENTRY_SCORE_MIN = 3.0

# Tiered Stop Losses
OPTIONS_STOP_TIER_1 = 0.20  # Score 3.0-3.25
OPTIONS_STOP_TIER_2 = 0.22  # Score 3.25-3.5
OPTIONS_STOP_TIER_3 = 0.25  # Score 3.5-3.75
OPTIONS_STOP_TIER_4 = 0.30  # Score 3.75-4.0
OPTIONS_PROFIT_TARGET_PCT = 0.50

# Time Constraints
OPTIONS_ENTRY_START = "10:00"
OPTIONS_ENTRY_END = "14:30"
OPTIONS_LATE_DAY_TIME = "14:30"
OPTIONS_FORCE_EXIT_HOUR = 15    # 3 PM ET
OPTIONS_FORCE_EXIT_MINUTE = 45  # 3:45 PM ET

# Greeks Monitoring (Circuit Breaker Level 5)
# Note: These use CB_ prefix as they're part of the circuit breaker system
# See Risk Engine section (12-risk-engine.md) for integration
CB_DELTA_MAX = 0.80  # Max delta exposure per position
CB_GAMMA_WARNING = 0.05  # Gamma warning threshold near expiry
CB_VEGA_MAX = 0.50  # Max vega exposure
CB_THETA_WARNING = -0.02  # Daily theta decay warning (-2%)

# Contract Selection
OPTIONS_DTE_MIN = 1
OPTIONS_DTE_MAX = 4
OPTIONS_DELTA_MIN = 0.40
OPTIONS_DELTA_MAX = 0.60
OPTIONS_MIN_PREMIUM = 0.50

# =============================================================================
# OCO MANAGER (V2.1)
# =============================================================================

OCO_STATE_KEY = "oco_state"
OCO_RECONCILE_ON_START = True
OCO_CANCEL_TIMEOUT_SEC = 30

# =============================================================================
# PORTFOLIO ROUTER
# =============================================================================

# Exposure Limits
EXPOSURE_LIMITS = {
    "NASDAQ_BETA": {"max_net_long": 0.50, "max_net_short": 0.30, "max_gross": 0.75},
    "SPY_BETA": {"max_net_long": 0.40, "max_net_short": 0.00, "max_gross": 0.40},
    "SMALL_CAP_BETA": {"max_net_long": 0.25, "max_net_short": 0.00, "max_gross": 0.25},
    "FINANCIALS_BETA": {"max_net_long": 0.15, "max_net_short": 0.00, "max_gross": 0.15},
    "RATES": {"max_net_long": 0.40, "max_net_short": 0.00, "max_gross": 0.40}
}

# Group Membership
SYMBOL_GROUPS = {
    "TQQQ": "NASDAQ_BETA",
    "QLD": "NASDAQ_BETA",
    "SOXL": "NASDAQ_BETA",
    "PSQ": "NASDAQ_BETA",  # Inverse
    "SSO": "SPY_BETA",
    "TNA": "SMALL_CAP_BETA",
    "FAS": "FINANCIALS_BETA",
    "TMF": "RATES",
    "SHV": "RATES"
}

# Trend Engine Allocations (V2.2 Balanced Model)
TREND_SYMBOL_ALLOCATIONS = {
    "QLD": 0.20,  # 20% - 2× Nasdaq
    "SSO": 0.15,  # 15% - 2× S&P 500
    "TNA": 0.12,  # 12% - 3× Russell 2000
    "FAS": 0.08,  # 8% - 3× Financials
}
TREND_TOTAL_ALLOCATION = 0.55  # 55% total to Trend Engine

# Mean Reversion Allocations
MR_SYMBOL_ALLOCATIONS = {
    "TQQQ": 0.05,  # 5% - 3× Nasdaq
    "SOXL": 0.05,  # 5% - 3× Semiconductor
}
MR_TOTAL_ALLOCATION = 0.10  # 10% total to MR Engine

# Trade Thresholds
MIN_TRADE_VALUE = 2_000
MIN_SHARE_DELTA = 1

# =============================================================================
# RISK ENGINE
# =============================================================================

# Kill Switch
KILL_SWITCH_PCT = 0.03

# Panic Mode
PANIC_MODE_PCT = 0.04

# Weekly Breaker
WEEKLY_BREAKER_PCT = 0.05
WEEKLY_SIZE_REDUCTION = 0.50

# Gap Filter
GAP_FILTER_PCT = 0.015

# Vol Shock
VOL_SHOCK_ATR_MULT = 3.0
VOL_SHOCK_PAUSE_MIN = 15

# Time Guard
TIME_GUARD_START = "13:55"
TIME_GUARD_END = "14:10"

# =============================================================================
# V2.1 CIRCUIT BREAKER SYSTEM (5 Levels)
# =============================================================================
# These are graduated responses BEFORE the nuclear kill switch

# Level 1: Daily Loss Circuit Breaker
# At -2% daily loss, reduce sizing but don't liquidate
CB_DAILY_LOSS_THRESHOLD = 0.02  # -2% daily loss
CB_DAILY_SIZE_REDUCTION = 0.50  # Reduce to 50% sizing

# Level 2: Weekly Loss Circuit Breaker (same as WEEKLY_BREAKER_PCT above)

# Level 3: Portfolio Volatility Circuit Breaker
# If portfolio volatility exceeds threshold, block new entries
CB_PORTFOLIO_VOL_THRESHOLD = 0.015  # 1.5% daily portfolio volatility
CB_PORTFOLIO_VOL_LOOKBACK = 20  # Days for volatility calculation

# Level 4: Correlation Circuit Breaker
# If correlation between positions exceeds threshold, reduce exposure
CB_CORRELATION_THRESHOLD = 0.60  # Correlation > 60%
CB_CORRELATION_REDUCTION = 0.50  # Reduce exposure by 50%

# Level 5: Greeks Breach Circuit Breaker (for Options Engine)
# Thresholds for options risk monitoring
CB_DELTA_MAX = 0.80  # Max delta exposure per position
CB_GAMMA_WARNING = 0.05  # Gamma warning threshold near expiry
CB_VEGA_MAX = 0.50  # Max vega exposure
CB_THETA_WARNING = -0.02  # Daily theta decay warning (-2%)

# =============================================================================
# EXECUTION ENGINE
# =============================================================================

MOO_SUBMISSION_TIME = "15:45"
MOO_FALLBACK_CHECK = "09:31"
MARKET_ORDER_TIMEOUT_SEC = 60
CONNECTION_TIMEOUT_MIN = 5

# =============================================================================
# SCHEDULING
# =============================================================================

SCHEDULED_EVENTS = {
    "PRE_MARKET_SETUP": "09:25",
    "SOD_BASELINE": "09:33",
    "WARM_ENTRY_CHECK": "10:00",
    "TIME_GUARD_START": "13:55",
    "TIME_GUARD_END": "14:10",
    "MR_FORCE_CLOSE": "15:45",
    "EOD_PROCESSING": "15:45"
}

# =============================================================================
# INDICATORS
# =============================================================================

INDICATOR_WARMUP_DAYS = 252  # Max of all indicator requirements

# =============================================================================
# SYMBOLS
# =============================================================================

TRADED_SYMBOLS = ["TQQQ", "SOXL", "QLD", "SSO", "TNA", "FAS", "TMF", "PSQ", "SHV"]
PROXY_SYMBOLS = ["SPY", "RSP", "HYG", "IEF"]
ALL_SYMBOLS = TRADED_SYMBOLS + PROXY_SYMBOLS
```

---

## 16.16 Parameter Validation

### On Startup

Validate all parameters are within acceptable ranges:

```python
def validate_config():
    """Validate configuration parameters."""
    
    errors = []
    
    # Factor weights must sum to 1.0
    weight_sum = WEIGHT_TREND + WEIGHT_VOLATILITY + WEIGHT_BREADTH + WEIGHT_CREDIT
    if abs(weight_sum - 1.0) > 0.001:
        errors.append(f"Factor weights sum to {weight_sum}, must equal 1.0")
    
    # Regime thresholds must be in order
    if not (REGIME_RISK_ON > REGIME_NEUTRAL > REGIME_CAUTIOUS > REGIME_DEFENSIVE):
        errors.append("Regime thresholds must be in descending order")
    
    # Kill switch must be positive
    if KILL_SWITCH_PCT <= 0:
        errors.append("Kill switch percentage must be positive")
    
    # ... additional validations
    
    if errors:
        raise ValueError(f"Configuration errors: {errors}")
```

---

## 16.17 Key Design Decisions Summary

| Decision | Rationale |
|----------|-----------|
| **Single config.py file** | All parameters in one place for easy modification |
| **Named constants** | Self-documenting code |
| **Phase-dependent limits** | Different risk profiles at different account sizes |
| **Conservative defaults** | Err on side of safety |
| **Validation on startup** | Catch configuration errors early |

---

*Next Section: [17 - Appendix: Glossary](17-appendix-glossary.md)*

*Previous Section: [15 - State Persistence](15-state-persistence.md)*
