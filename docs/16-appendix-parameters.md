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

## 16.5 Trend Engine Parameters

### Bollinger Band Parameters

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `BB_PERIOD` | 20 | Moving average period |
| `BB_STD_DEV` | 2.0 | Standard deviation multiplier |
| `COMPRESSION_THRESHOLD` | 0.10 | Bandwidth threshold (10%) |

### Chandelier Stop Parameters

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `ATR_PERIOD` | 14 | ATR calculation period |
| `CHANDELIER_BASE_MULT` | 3.0 | Initial multiplier (profit < 15%) |
| `CHANDELIER_TIGHT_MULT` | 2.0 | Medium multiplier (profit 15-25%) |
| `CHANDELIER_TIGHTER_MULT` | 1.5 | Tight multiplier (profit > 25%) |
| `PROFIT_TIGHT_PCT` | 0.15 | First tightening threshold (15%) |
| `PROFIT_TIGHTER_PCT` | 0.25 | Second tightening threshold (25%) |

### Entry/Exit Thresholds

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `TREND_ENTRY_REGIME_MIN` | 40 | Minimum regime for entry |
| `TREND_EXIT_REGIME` | 30 | Regime score forcing exit |

---

## 16.6 Mean Reversion Engine Parameters

### RSI Parameters

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `RSI_PERIOD` | 5 | Fast RSI period |
| `RSI_THRESHOLD` | 25 | Oversold threshold |

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
| `MR_STOP_PCT` | 0.02 | −2% stop loss |
| `MR_FORCE_EXIT_TIME` | 15:45 ET | Mandatory close time |

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

## 16.9 Portfolio Router Parameters

### Exposure Group Limits

| Group | Max Net Long | Max Net Short | Max Gross |
|-------|:------------:|:-------------:|:---------:|
| `NASDAQ_BETA` | 50% | 30% | 75% |
| `SPY_BETA` | 40% | 0% | 40% |
| `RATES` | 40% | 0% | 40% |

### Group Membership

| Symbol | Group | Type |
|--------|-------|------|
| TQQQ | NASDAQ_BETA | 3× Long Nasdaq |
| QLD | NASDAQ_BETA | 2× Long Nasdaq |
| SOXL | NASDAQ_BETA | 3× Long Semi |
| PSQ | NASDAQ_BETA | 1× Inverse Nasdaq |
| SSO | SPY_BETA | 2× Long S&P |
| TMF | RATES | 3× Long Treasury |
| SHV | RATES | Short Treasury |

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
| SMA(200) | 200 | Slow moving average |
| BB(20,2) | 20 | Bollinger Bands |
| RSI(5) | 10 | Fast RSI (extra warmup) |
| ATR(14) | 14 | Average True Range |
| Realized Vol | 252 | For percentile ranking |

**Minimum warmup period: 252 trading days** to ensure all indicators are fully populated.

---

## 16.14 Symbol Configuration

### Traded Symbols

| Symbol | Description | Strategy |
|--------|-------------|----------|
| TQQQ | 3× Nasdaq | Mean Reversion |
| SOXL | 3× Semiconductor | Mean Reversion |
| QLD | 2× Nasdaq | Trend, Warm Entry |
| SSO | 2× S&P 500 | Trend, Warm Entry |
| TMF | 3× Treasury | Hedge |
| PSQ | 1× Inverse Nasdaq | Hedge |
| SHV | Short Treasury | Yield |

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
# TREND ENGINE
# =============================================================================

# Bollinger Bands
BB_PERIOD = 20
BB_STD_DEV = 2.0
COMPRESSION_THRESHOLD = 0.10

# Chandelier Stop
ATR_PERIOD = 14
CHANDELIER_BASE_MULT = 3.0
CHANDELIER_TIGHT_MULT = 2.0
CHANDELIER_TIGHTER_MULT = 1.5
PROFIT_TIGHT_PCT = 0.15
PROFIT_TIGHTER_PCT = 0.25

# Entry/Exit
TREND_ENTRY_REGIME_MIN = 40
TREND_EXIT_REGIME = 30

# =============================================================================
# MEAN REVERSION ENGINE
# =============================================================================

# RSI
RSI_PERIOD = 5
RSI_THRESHOLD = 25

# Entry Conditions
MR_DROP_THRESHOLD = 0.025
MR_VOLUME_MULT = 1.2
MR_WINDOW_START = "10:00"
MR_WINDOW_END = "15:00"
MR_REGIME_MIN = 40

# Exit Conditions
MR_TARGET_PCT = 0.02
MR_STOP_PCT = 0.02
MR_FORCE_EXIT_TIME = "15:45"

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
# PORTFOLIO ROUTER
# =============================================================================

# Exposure Limits
EXPOSURE_LIMITS = {
    "NASDAQ_BETA": {"max_net_long": 0.50, "max_net_short": 0.30, "max_gross": 0.75},
    "SPY_BETA": {"max_net_long": 0.40, "max_net_short": 0.00, "max_gross": 0.40},
    "RATES": {"max_net_long": 0.40, "max_net_short": 0.00, "max_gross": 0.40}
}

# Group Membership
SYMBOL_GROUPS = {
    "TQQQ": "NASDAQ_BETA",
    "QLD": "NASDAQ_BETA",
    "SOXL": "NASDAQ_BETA",
    "PSQ": "NASDAQ_BETA",  # Inverse
    "SSO": "SPY_BETA",
    "TMF": "RATES",
    "SHV": "RATES"
}

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

TRADED_SYMBOLS = ["TQQQ", "SOXL", "QLD", "SSO", "TMF", "PSQ", "SHV"]
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
