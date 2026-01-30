# 18. Options Engine

[Previous: 17 - Appendix: Glossary](17-appendix-glossary.md) | [Table of Contents](00-table-of-contents.md) | [Next: 19 - OCO Manager](19-oco-manager.md)

---

## Overview

The **Options Engine** implements a dual-mode architecture for QQQ options trading. This is a **satellite engine** (20% allocation) with two distinct operating modes based on DTE (days to expiration).

> **V2.1.1 Redesign**: Complete architectural redesign with Micro Regime Engine for intraday trading.
> Full specification: `docs/v2-specs/V2_1_OPTIONS_ENGINE_DESIGN.txt`

**Key Characteristics:**
- **Underlying**: QQQ (Nasdaq 100 ETF)
- **Total Allocation**: 20% of portfolio
- **Dual-Mode Architecture**:
  - **Swing Mode (15%)**: 5-45 DTE, spread strategies
  - **Intraday Mode (5%)**: 0-2 DTE, Micro Regime Engine

---

## Dual-Mode Architecture (V2.1.1)

```
┌─────────────────────────────────────────────────────────────────────┐
│                    OPTIONS ENGINE DUAL-MODE                          │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌─────────────────────────┐     ┌─────────────────────────────┐  │
│  │     SWING MODE          │     │     INTRADAY MODE           │  │
│  │     (5-45 DTE)          │     │       (0-2 DTE)             │  │
│  ├─────────────────────────┤     ├─────────────────────────────┤  │
│  │ Allocation: 15%         │     │ Allocation: 5%              │  │
│  │                         │     │                             │  │
│  │ Strategies:             │     │ Decision Engine:            │  │
│  │ • Debit Spreads (10-14) │     │ MICRO REGIME ENGINE         │  │
│  │ • Credit Spreads (18-21)│     │ (VIX Level × VIX Direction) │  │
│  │ • ITM Long (14-21)      │     │                             │  │
│  │ • Protective Puts (35-45)│    │ 21 Trading Regimes          │  │
│  │                         │     │                             │  │
│  │ Entry: 4-Factor Score   │     │ Strategies:                 │  │
│  │ Exit: OCO pairs         │     │ • Long Calls (momentum up)  │  │
│  └─────────────────────────┘     │ • Long Puts (momentum down) │
│                                  │ • Iron Condors (range/whip) │
│                                  └─────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Swing Mode (5-45 DTE)

### Allocation: 15% of Portfolio

Swing Mode uses the original 4-factor entry scoring system with spread-based strategies for defined risk.

---

## 4-Factor Entry Scoring System

Each factor contributes 0-1 points. Total score ranges 0-4, minimum threshold: **3.0**

### Factor 1: ADX (Trend Strength)

| ADX Value | Score | Interpretation |
|:---------:|:-----:|----------------|
| < 20 | 0.00 | No trend, avoid |
| 20-25 | 0.50 | Weak trend |
| 25-30 | 0.75 | Moderate trend |
| > 30 | 1.00 | Strong trend, ideal |

**Config Parameter:** `OPTIONS_ADX_PERIOD` (default: 14)

### Factor 2: Momentum (MA200 Position)

| Condition | Score | Interpretation |
|-----------|:-----:|----------------|
| Price > MA200 | 1.00 | Bullish momentum (favor calls) |
| Price < MA200 | 1.00 | Bearish momentum (favor puts) |
| Price = MA200 (within 0.1%) | 0.50 | Neutral, reduced conviction |

**Config Parameter:** `OPTIONS_MA_PERIOD` (default: 200)

### Factor 3: IV Rank

Implied Volatility percentile over the past year.

| IV Rank | Score | Interpretation |
|:-------:|:-----:|----------------|
| 0-20% | 0.25 | Very low IV, cheap options |
| 20-40% | 0.50 | Low IV |
| 40-60% | 0.75 | Normal IV |
| 60-80% | 1.00 | Elevated IV, good premium |
| 80-100% | 0.75 | Very high IV, potential mean reversion |

**Config Parameters:** `OPTIONS_IV_LOOKBACK` (default: 252)

### Factor 4: Liquidity (Bid-Ask Spread)

| Spread (% of mid) | Score | Interpretation |
|:-----------------:|:-----:|----------------|
| < 2% | 1.00 | Excellent liquidity |
| 2-5% | 0.75 | Good liquidity |
| 5-10% | 0.50 | Acceptable |
| > 10% | 0.00 | Too wide, avoid |

**Config Parameter:** `OPTIONS_MAX_SPREAD_PCT` (default: 0.10)

---

## Confidence-Weighted Tiered Stops

Higher entry scores indicate higher conviction, allowing wider stops with fewer contracts (risk-adjusted sizing).

| Entry Score | Stop Loss | Contract Size | Risk Profile |
|:-----------:|:---------:|:-------------:|--------------|
| 3.00-3.25 | 20% | 34 contracts | Tight stops, higher volume |
| 3.25-3.50 | 22% | 31 contracts | |
| 3.50-3.75 | 25% | 27 contracts | |
| 3.75-4.00 | 30% | 23 contracts | Wide stops, lower volume |

**Formula:**
```
contracts = floor(allocation / (entry_price * 100 * stop_pct))
```

**Config Parameters:**
- `OPTIONS_STOP_TIER_1` (default: 0.20)
- `OPTIONS_STOP_TIER_2` (default: 0.22)
- `OPTIONS_STOP_TIER_3` (default: 0.25)
- `OPTIONS_STOP_TIER_4` (default: 0.30)

---

## Entry Rules

### Prerequisites

1. **Regime Score >= 40** (not RISK_OFF)
2. **No existing options position**
3. **Time within trading window** (10:00 AM - 2:30 PM ET)
4. **Risk Engine green light** (no active safeguards)
5. **Entry Score >= 3.0**

### Direction Selection

| Condition | Direction | Rationale |
|-----------|-----------|-----------|
| Price > MA200, RSI < 70 | CALL | Bullish momentum, not overbought |
| Price < MA200, RSI > 30 | PUT | Bearish momentum, not oversold |
| Otherwise | NONE | Wait for clearer signal |

### Contract Selection

1. **Expiry**: 1-4 DTE (per V2.1 spec)
2. **Strike**: ATM or first OTM strike
3. **Delta Range**: 0.40-0.60 (near ATM, absolute value for puts)
4. **Minimum Premium**: $0.50 per contract

**Config Parameters:**
- `OPTIONS_DTE_MIN` (default: 1)
- `OPTIONS_DTE_MAX` (default: 4)
- `OPTIONS_DELTA_MIN` (default: 0.40)
- `OPTIONS_DELTA_MAX` (default: 0.60)
- `OPTIONS_MIN_PREMIUM` (default: 0.50)

---

## Exit Rules

### Profit Target

**+50% gain** from entry price.

```python
if current_price >= entry_price * 1.50:
    emit_exit_signal("PROFIT_TARGET")
```

**Config Parameter:** `OPTIONS_PROFIT_TARGET_PCT` (default: 0.50)

### Stop Loss

Tiered based on entry score (see table above).

```python
stop_pct = get_stop_for_score(entry_score)
if current_price <= entry_price * (1 - stop_pct):
    emit_exit_signal("STOP_LOSS")
```

### Late Day Constraint

After **2:30 PM ET**, only 20% stops are allowed (tightest tier). This prevents holding options with significant theta decay into close.

```python
if current_time >= "14:30" and stop_pct > 0.20:
    stop_pct = 0.20  # Force tight stop
```

**Config Parameter:** `OPTIONS_LATE_DAY_TIME` (default: "14:30")

### Time Exit

Force close by **3:45 PM ET** if still holding. This aligns with the Mean Reversion Engine's forced close time to ensure no intraday positions are held overnight.

**Config Parameters:**
- `OPTIONS_FORCE_EXIT_HOUR` (default: 15)
- `OPTIONS_FORCE_EXIT_MINUTE` (default: 45)

---

## Greeks Monitoring (V2.1)

The Options Engine tracks real-time Greeks for risk management.

### Monitored Greeks

| Greek | Purpose | Alert Threshold |
|-------|---------|-----------------|
| **Delta** | Directional exposure | Delta > 0.70 or < 0.30 |
| **Gamma** | Delta sensitivity | Gamma > 0.10 |
| **Theta** | Time decay | Theta < -$0.15/contract |
| **Vega** | Volatility sensitivity | Vega > 0.20 |

### Greeks Snapshot

```python
@dataclass
class GreeksSnapshot:
    delta: float
    gamma: float
    theta: float
    vega: float
    iv: float
    underlying_price: float
    timestamp: str
```

### Risk Engine Integration

The Risk Engine monitors portfolio-level Greeks exposure:

```python
if abs(portfolio_delta) > MAX_PORTFOLIO_DELTA:
    trigger_delta_hedge_alert()
```

**Config Parameters:**
- `OPTIONS_MAX_DELTA` (default: 0.70)
- `OPTIONS_MAX_GAMMA` (default: 0.10)
- `OPTIONS_MIN_THETA` (default: -0.15)

---

## OCO Order Integration

All options positions use **One-Cancels-Other (OCO)** order pairs for exit management:

1. **Entry**: Place market order for contracts
2. **Immediately after fill**: Submit OCO pair
   - Stop order at stop price
   - Limit order at profit target
3. **On either fill**: Cancel the other leg

See [19 - OCO Manager](19-oco-manager.md) for implementation details.

---

## Data Flow

```
┌─────────────────┐
│  Market Data    │ ─── QQQ price, IV, option chain
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 4-Factor Scoring│ ─── ADX + Momentum + IV + Liquidity
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Entry Decision  │ ─── Score >= 3.0? Direction?
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Contract Select │ ─── ATM, 0-1 DTE, liquidity check
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  TargetWeight   │ ─── Emit to Portfolio Router
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  OCO Manager    │ ─── Stop + Profit target pair
└─────────────────┘
```

---

## Configuration Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `OPTIONS_ALLOCATION_PCT` | 0.20 | Max allocation to options (20%) |
| `OPTIONS_ENTRY_SCORE_MIN` | 3.0 | Minimum score for entry |
| `OPTIONS_ADX_PERIOD` | 14 | ADX lookback period |
| `OPTIONS_MA_PERIOD` | 200 | Moving average period |
| `OPTIONS_IV_LOOKBACK` | 252 | IV rank lookback (1 year) |
| `OPTIONS_MAX_SPREAD_PCT` | 0.10 | Max bid-ask spread (10%) |
| `OPTIONS_PROFIT_TARGET_PCT` | 0.50 | Profit target (+50%) |
| `OPTIONS_STOP_TIER_1` | 0.20 | Tightest stop (score 3.0-3.25) |
| `OPTIONS_STOP_TIER_2` | 0.22 | Stop for score 3.25-3.5 |
| `OPTIONS_STOP_TIER_3` | 0.25 | Stop for score 3.5-3.75 |
| `OPTIONS_STOP_TIER_4` | 0.30 | Widest stop (score 3.75-4.0) |
| `OPTIONS_LATE_DAY_TIME` | "14:30" | Force tight stops after this |
| `OPTIONS_FORCE_EXIT_HOUR` | 15 | Force close hour (3 PM) |
| `OPTIONS_FORCE_EXIT_MINUTE` | 45 | Force close minute (3:45 PM) |
| `OPTIONS_MAX_DELTA` | 0.70 | Delta alert threshold |
| `OPTIONS_MAX_GAMMA` | 0.10 | Gamma alert threshold |
| `OPTIONS_MIN_THETA` | -0.15 | Theta alert threshold |

---

## Implementation Notes

### File Location

`engines/satellite/options_engine.py`

### Key Classes

| Class | Purpose |
|-------|---------|
| `OptionsEngine` | Main engine class |
| `EntryScore` | 4-factor score breakdown |
| `OptionContract` | Contract details with Greeks |
| `OptionDirection` | CALL or PUT enum |

### Dependencies

- `engines/core/risk_engine.py` (Greeks monitoring)
- `execution/oco_manager.py` (exit orders)
- `config.py` (all parameters)

---

---

## Intraday Mode (0-2 DTE) - Micro Regime Engine

### Allocation: 5% of Portfolio

Intraday Mode uses the **Micro Regime Engine** to determine optimal strategy based on VIX conditions.

### Why VIX Direction Matters

**Key Insight**: VIX level alone is insufficient. VIX direction determines whether mean reversion or momentum works.

```
VIX at 25 and FALLING = Recovery starting, FADE the move (buy calls)
VIX at 25 and RISING = Fear building, RIDE the move (buy puts)

Same VIX level → OPPOSITE strategies!
```

### VIX Direction Classification

| Direction | VIX Change (15min) | Score | Implication |
|-----------|:------------------:|:-----:|-------------|
| FALLING_FAST | < -2.0% | +2 | Strong recovery |
| FALLING | -0.5% to -2.0% | +1 | Recovery starting |
| STABLE | -0.5% to +0.5% | 0 | Range-bound |
| RISING | +0.5% to +2.0% | -1 | Fear building |
| RISING_FAST | +2.0% to +5.0% | -2 | Panic emerging |
| SPIKING | > +5.0% | -3 | Crash mode |
| WHIPSAW | 5+ reversals/hour | 0 | No direction |

### 21 Micro-Regime Matrix (Complete)

VIX Level × VIX Direction = **21 distinct trading regimes**. Each regime maps to a specific strategy and allocation.

#### VIX LOW Regimes (VIX < 20) - Normal Market Conditions

| VIX Direction | Micro Regime | Strategy | Allocation | Rationale |
|---------------|--------------|----------|:----------:|-----------|
| FALLING_FAST | COMPLACENT_BULL | Long Calls | 5% | Strong recovery, ride momentum |
| FALLING | CALM_BULL | Debit Spreads | 4% | Recovery starting, defined risk |
| STABLE | GOLDILOCKS | Iron Condors | 3% | Range-bound, collect premium |
| RISING | WAKING_UP | Protective Puts | 2% | Fear emerging, hedge longs |
| RISING_FAST | SURPRISE_FEAR | Long Puts | 3% | Unexpected selloff, ride down |
| SPIKING | FLASH_CRASH | Emergency Puts | 2% | Sudden crash, protective only |
| WHIPSAW | CONFUSED_LOW | Iron Condors | 2% | No direction, small range bets |

#### VIX MEDIUM Regimes (VIX 20-30) - Elevated Fear

| VIX Direction | Micro Regime | Strategy | Allocation | Rationale |
|---------------|--------------|----------|:----------:|-----------|
| FALLING_FAST | RELIEF_RALLY | Long Calls | 4% | Fear unwinding fast |
| FALLING | FEAR_FADING | Call Spreads | 3% | Recovery starting |
| STABLE | ELEVATED_RANGE | Iron Condors | 2% | High premium, tight range |
| RISING | FEAR_BUILDING | Put Spreads | 3% | Momentum down continuing |
| RISING_FAST | PANIC_STARTING | Long Puts | 4% | Accelerating selloff |
| SPIKING | CRISIS_FORMING | Defensive Only | 2% | Potential crash forming |
| WHIPSAW | CONFUSED_MED | Small Condors | 1% | Chaos, minimal exposure |

#### VIX HIGH Regimes (VIX > 30) - Crisis Conditions

| VIX Direction | Micro Regime | Strategy | Allocation | Rationale |
|---------------|--------------|----------|:----------:|-----------|
| FALLING_FAST | CRISIS_ENDING | Long Calls | 3% | Crisis resolution, bounce |
| FALLING | RECOVERY_START | Call Spreads | 2% | Early recovery signs |
| STABLE | CRISIS_PLATEAU | **NO TRADE** | 0% | Uncertainty too high |
| RISING | CRISIS_WORSENING | Protective Only | 1% | Crisis deepening |
| RISING_FAST | FULL_PANIC | **NO TRADE** | 0% | Max fear, stay out |
| SPIKING | CAPITULATION | **NO TRADE** | 0% | Capitulation phase |
| WHIPSAW | CHAOS | **NO TRADE** | 0% | Complete chaos, no edge |

**Key Insight**: 4 of 21 regimes result in **NO TRADE** (all HIGH VIX with non-recovery direction). This is by design—some market conditions have no edge.

### Micro Score Calculation

The Micro Regime Engine calculates a **composite score** (range: -15 to 80) to determine trade eligibility and sizing.

#### Score Components

| Component | Score Range | Calculation |
|-----------|:-----------:|-------------|
| **VIX Level** | 0-25 pts | Lower VIX = higher score |
| **VIX Direction** | -10 to +20 pts | Falling = positive, Spiking = negative |
| **QQQ Move** | 0-20 pts | Sweet spot at 0.8-1.25% move |
| **Move Velocity** | 0-15 pts | Gradual moves score higher than spikes |

#### VIX Level Scoring

```
VIX < 15:     25 points (ideal conditions)
VIX 15-20:    20 points
VIX 20-25:    15 points
VIX 25-30:    10 points
VIX 30-40:     5 points
VIX > 40:      0 points
```

#### VIX Direction Scoring

```
FALLING_FAST:  +20 points (strong recovery)
FALLING:       +10 points (recovery)
STABLE:         0 points (neutral)
RISING:        -5 points (fear building)
RISING_FAST:  -10 points (panic)
SPIKING:      -10 points + DANGER FLAG
WHIPSAW:       -5 points + WHIPSAW FLAG
```

#### Score Interpretation

| Score Range | Action | Allocation Multiplier |
|:-----------:|--------|:---------------------:|
| 60+ | Strong entry | 100% of regime allocation |
| 40-59 | Normal entry | 75% of regime allocation |
| 20-39 | Cautious entry | 50% of regime allocation |
| 0-19 | Skip or minimal | 25% or skip |
| < 0 | **NO TRADE** | 0% |

---

### VIX Monitoring System

**IMPORTANT**: We use VIX only, NOT VIX1D.

VIX1D was evaluated and rejected because:
1. VIX and VIX1D move together during trading hours (0.95 correlation)
2. VIX1D only diverges at market open (9:30-10:00 AM)
3. Our trading window starts at 10:00 AM - divergence already resolved
4. Adding VIX1D increases complexity without actionable benefit

### Tiered VIX Monitoring

| Layer | Check Frequency | Purpose |
|-------|-----------------|---------|
| Layer 1 | 5 minutes | Spike detection (VIX change > 3%) |
| Layer 2 | 15 minutes | Direction confirmation |
| Layer 3 | 60 minutes | Whipsaw detection (5+ reversals) |
| Layer 4 | 30 minutes | Full regime recalculation |

---

### Intraday Strategy Deployment

Based on micro regime, deploy one of four intraday strategies:

#### Strategy 1: Debit Fade (Mean Reversion)

**When**: VIX FALLING + QQQ oversold
**Setup**: Buy OTM call/put debit spread
**Target**: Fade the extreme move, exit at VWAP
**Max Allocation**: 3%

```
Trigger: micro_score >= 50 AND qqqMove >= 1.0% AND vix < 25
Strategy: ATM debit spread, 0-1 DTE
Exit: +30% profit OR return to VWAP OR 15:30 time stop
```

#### Strategy 2: Credit Spreads (Range-Bound)

**When**: VIX STABLE or WHIPSAW regimes
**Setup**: Sell OTM credit spread (iron condor)
**Target**: Collect theta decay in range
**Max Allocation**: 2%

```
Trigger: vixDirection in (STABLE, WHIPSAW) AND vix >= 18
Strategy: 10-delta wings, 0-1 DTE
Exit: 50% of max profit OR breach of short strike
```

#### Strategy 3: ITM Momentum

**When**: VIX RISING + clear directional move
**Setup**: Buy ITM option (0.70 delta)
**Target**: Ride momentum continuation
**Max Allocation**: 3%

```
Trigger: vixDirection in (RISING, RISING_FAST) AND qqqMove >= 0.8% AND vix >= 25
Strategy: ITM put (delta 0.70), 0-1 DTE
Exit: +25% profit OR trailing stop after +15%
```

#### Strategy 4: Protective Puts

**When**: VIX SPIKING or CRISIS conditions
**Setup**: Buy OTM puts for portfolio protection
**Target**: Hedge existing long exposure
**Max Allocation**: 2%

```
Trigger: vixDirection in (SPIKING, RISING_FAST) AND vix >= 30
Strategy: 5% OTM puts, 1-2 DTE
Exit: Crisis resolution OR expiry
```

### Intraday Force Exit (15:30 ET)

**All intraday options positions MUST close by 15:30 ET** (not 15:45 like Mean Reversion). This gives:
- 30 minutes before market close
- Time to handle any execution issues
- Avoids overnight gamma risk on 0-DTE

---

## Related Sections

- [12 - Risk Engine](12-risk-engine.md) - Greeks monitoring integration
- [19 - OCO Manager](19-oco-manager.md) - Exit order management
- [11 - Portfolio Router](11-portfolio-router.md) - Order authorization
- [16 - Appendix: Parameters](16-appendix-parameters.md) - All config values
- [v2-specs/V2_1_OPTIONS_ENGINE_DESIGN.txt](v2-specs/V2_1_OPTIONS_ENGINE_DESIGN.txt) - Full V2.1.1 specification

---

[Previous: 17 - Appendix: Glossary](17-appendix-glossary.md) | [Table of Contents](00-table-of-contents.md) | [Next: 19 - OCO Manager](19-oco-manager.md)
