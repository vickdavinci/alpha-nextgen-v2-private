# Engine Logic Reference - Alpha NextGen V2

> **Purpose:** Complete reference for all engine logic, conditions, and config values.
> This document enables developers to understand the entire trading system flow.
>
> **Last Updated:** 04 February 2026 (V2.30)

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Regime Engine](#regime-engine)
3. [Capital Engine](#capital-engine)
4. [Risk Engine](#risk-engine)
5. [Cold Start Engine](#cold-start-engine)
6. [Startup Gate (V2.30)](#startup-gate-v230)
7. [Trend Engine](#trend-engine)
8. [Mean Reversion Engine](#mean-reversion-engine)
9. [Options Engine](#options-engine)
10. [Hedge Engine](#hedge-engine)
11. [Yield Sleeve](#yield-sleeve)
12. [Portfolio Router](#portfolio-router)
13. [Key Thresholds Quick Reference](#key-thresholds-quick-reference)

---

## System Overview

### Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              DATA LAYER                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│  Proxy (Daily): SPY, RSP, HYG, IEF     │  Traded (Minute): QLD, SSO, etc.  │
│  Options: QQQ chains                    │  VIX: Minute resolution           │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            CORE ENGINES                                     │
├──────────────────┬────────────────────┬─────────────────────────────────────┤
│  REGIME ENGINE   │   CAPITAL ENGINE   │   RISK ENGINE     │  STARTUP GATE   │
│  Score 0-100     │   Phase: SEED/     │   Kill Switch     │  V2.30          │
│  5-Factor V2.3   │   GROWTH/MATURE    │   Governor V2.26  │  All-Weather    │
│  Smoothing 0.3   │   Lockbox          │   Ghost Flush P0  │  4 phases 15d   │
└──────────────────┴────────────────────┴─────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          STRATEGY ENGINES                                   │
├───────────┬────────────┬──────────┬───────────┬───────────┬─────────────────┤
│   TREND   │  OPTIONS   │   MR     │   HEDGE   │   YIELD   │   COLD START    │
│   40%     │   25%      │   10%    │   0-30%   │ Remainder │   Days 1-5      │
│ QLD,SSO   │ QQQ Opts   │ TQQQ,    │ TMF,PSQ   │    SHV    │   50% sizing    │
│ TNA,FAS   │ Swing+Intr │ SOXL     │           │           │   (options)     │
└───────────┴────────────┴──────────┴───────────┴───────────┴─────────────────┘
                                    │
                        TargetWeight Objects
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         PORTFOLIO ROUTER                                    │
│  1. Collect → 2. Aggregate → 3. Validate → 4. Net → 5. Prioritize → 6. Exec│
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        EXECUTION ENGINE                                     │
│        Market Orders (IMMEDIATE)    │    MOO Orders (EOD, submit 15:45)     │
│        OCO Manager (Options)        │    Fill Handler (Position tracking)  │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Signal Flow Sequence

```mermaid
sequenceDiagram
    participant D as Data
    participant R as Regime
    participant K as Risk
    participant S as Strategy
    participant P as Router
    participant E as Execution

    D->>R: Market data
    R->>R: Calculate 5-factor score
    R-->>S: RegimeState (score, classification)

    D->>K: Price data
    K->>K: Check circuit breakers
    K-->>P: GO/NO-GO status

    S->>S: Generate signals
    S-->>P: TargetWeight objects

    P->>P: Aggregate + Validate
    P->>E: OrderIntent objects
    E->>E: Submit to broker
```

---

## Regime Engine

**File:** `engines/core/regime_engine.py`
**Purpose:** Detect overall market state using 5 weighted factors.

### Flowchart

```mermaid
flowchart TD
    START[Start] --> CHECK{Indicators Ready?}
    CHECK -->|No| WAIT[Return cached score]
    CHECK -->|Yes| CALC[Calculate 5 factors]

    CALC --> F1[Trend Factor 30%]
    CALC --> F2[VIX Factor 20%]
    CALC --> F3[Realized Vol 15%]
    CALC --> F4[Breadth Factor 20%]
    CALC --> F5[Credit Factor 15%]

    F1 --> AGG[Weighted Average]
    F2 --> AGG
    F3 --> AGG
    F4 --> AGG
    F5 --> AGG

    AGG --> SMOOTH[Apply EMA Smoothing<br/>α = 0.3]
    SMOOTH --> CLASS{Classify Score}

    CLASS -->|>= 70| RISKON[RISK_ON]
    CLASS -->|>= 50| NEUTRAL[NEUTRAL]
    CLASS -->|>= 45| CAUTIOUS[CAUTIOUS]
    CLASS -->|>= 35| DEFENSIVE[DEFENSIVE]
    CLASS -->|< 35| RISKOFF[RISK_OFF]
```

### Config Values

| Parameter | Value | Description |
|-----------|------:|-------------|
| `TREND_WEIGHT` | 0.30 | Price vs MA200 contribution |
| `VIX_WEIGHT` | 0.20 | VIX level contribution |
| `REALIZED_VOL_WEIGHT` | 0.15 | Historical volatility |
| `BREADTH_WEIGHT` | 0.20 | Market breadth (SPY vs RSP) |
| `CREDIT_WEIGHT` | 0.15 | Credit spread (HYG vs IEF) |
| `REGIME_SMOOTHING_ALPHA` | 0.30 | EMA smoothing factor |

### State Classification

| Score Range | State | Trading Allowed |
|:-----------:|:-----:|:---------------:|
| >= 70 | RISK_ON | Full allocation |
| >= 50 | NEUTRAL | Standard allocation |
| >= 45 | CAUTIOUS | Reduced allocation (V6.15: was >= 40) |
| >= 35 | DEFENSIVE | Hedges only (V6.15: was >= 30) |
| < 35 | RISK_OFF | No new longs |

---

## Capital Engine

**File:** `engines/core/capital_engine.py`
**Purpose:** Manage account phases, lockbox, and position limits.

### Flowchart

```mermaid
flowchart TD
    START[Calculate Capital State] --> PHASE{Determine Phase}

    PHASE -->|< $100K| SEED[SEED Phase]
    PHASE -->|$100K-$250K| GROWTH[GROWTH Phase]
    PHASE -->|> $250K| MATURE[MATURE Phase]

    SEED --> LOCKBOX[Calculate Lockbox]
    GROWTH --> LOCKBOX
    MATURE --> LOCKBOX

    LOCKBOX --> LB1{Equity > $100K?}
    LB1 -->|Yes| ADD1[Add $10K to lockbox]
    LB1 -->|No| TE
    ADD1 --> LB2{Equity > $200K?}
    LB2 -->|Yes| ADD2[Add another $20K]
    LB2 -->|No| TE

    TE[Tradeable Equity<br/>= Total - Lockbox] --> LIMITS[Apply Position Limits]

    LIMITS --> RETURN[Return CapitalState]
```

### Config Values

| Parameter | Value | Description |
|-----------|------:|-------------|
| `PHASE_SEED_MAX` | $100,000 | Max equity for SEED phase |
| `PHASE_GROWTH_MAX` | $250,000 | Max equity for GROWTH phase |
| `LOCKBOX_MILESTONE_1` | $100,000 | First lockbox trigger |
| `LOCKBOX_MILESTONE_1_AMT` | $10,000 | Amount locked at $100K |
| `LOCKBOX_MILESTONE_2` | $200,000 | Second lockbox trigger |
| `LOCKBOX_MILESTONE_2_AMT` | $20,000 | Amount locked at $200K |
| `SEED_MAX_POSITION_PCT` | 0.30 | Max single position in SEED |
| `GROWTH_MAX_POSITION_PCT` | 0.25 | Max single position in GROWTH |

---

## Risk Engine

**File:** `engines/core/risk_engine.py`
**Purpose:** Protect capital through circuit breakers and safeguards.

### Flowchart

```mermaid
flowchart TD
    START[Risk Check] --> KS{Kill Switch<br/>Loss >= 5%?}

    KS -->|Yes| KS_ACT[KILL SWITCH ACTIVE<br/>Liquidate ALL<br/>Reset Cold Start]
    KS_ACT --> RETURN_NOGO[Return: NO-GO]

    KS -->|No| PM{Panic Mode<br/>SPY -4% intraday?}
    PM -->|Yes| PM_ACT[PANIC MODE<br/>Liquidate Longs<br/>Keep Hedges]
    PM_ACT --> RETURN_PARTIAL[Return: Partial GO]

    PM -->|No| WB{Weekly Breaker<br/>WTD Loss >= 5%?}
    WB -->|Yes| WB_ACT[WEEKLY BREAKER<br/>50% Position Sizing]

    WB -->|No| GAP{Gap Filter<br/>SPY gap >= -1.5%?}
    GAP -->|Yes| GAP_ACT[GAP FILTER<br/>Block Intraday Only]

    GAP -->|No| VOL{Vol Shock<br/>Bar > 3× ATR?}
    VOL -->|Yes| VOL_ACT[VOL SHOCK<br/>15-min Pause]

    VOL -->|No| TG{Time Guard<br/>13:55-14:10 ET?}
    TG -->|Yes| TG_ACT[TIME GUARD<br/>Block New Entries]

    TG -->|No| RETURN_GO[Return: GO]

    WB_ACT --> GAP
    GAP_ACT --> VOL
    VOL_ACT --> TG
    TG_ACT --> RETURN_GO
```

### Config Values

| Parameter | Value | Description |
|-----------|------:|-------------|
| `KILL_SWITCH_PCT` | 0.05 | 5% daily loss triggers kill switch (V2.3.17) |
| `KILL_SWITCH_PREEMPTIVE_PCT` | 0.045 | 4.5% preemptive kill switch (V2.4.4) |
| `PANIC_MODE_SPY_DROP` | 0.04 | SPY -4% triggers panic mode |
| `WEEKLY_BREAKER_PCT` | 0.05 | 5% WTD loss triggers sizing reduction |
| `GAP_FILTER_PCT` | 0.015 | SPY -1.5% gap blocks MR entries |
| `VOL_SHOCK_ATR_MULT` | 3.0 | Bar > 3× ATR triggers 15-min pause |
| `TIME_GUARD_START` | "13:55" | Entry blocking window start |
| `TIME_GUARD_END` | "14:10" | Entry blocking window end |
| `MARGIN_CALL_MAX_CONSECUTIVE` | 5 | Max consecutive margin calls before circuit breaker (V2.4.4) |

### Margin Call Circuit Breaker (V2.4.4)

When the system detects `MARGIN_CALL_MAX_CONSECUTIVE` (5) consecutive margin calls, a 4-hour cooldown is triggered. During the cooldown:
- No new entries are submitted
- Existing positions are not affected
- Cooldown resets after 4 hours or after a successful non-margin-call fill

### Circuit Breaker Priority

1. **Kill Switch** (highest) - Full liquidation
2. **Preemptive Kill Switch** - Warning at 4.5%, blocks new entries (V2.4.4)
3. **Panic Mode** - Liquidate longs, keep hedges
4. **Margin Call Breaker** - 4-hour cooldown after 5 consecutive margin calls (V2.4.4)
5. **Weekly Breaker** - 50% sizing reduction
6. **Gap Filter** - Block intraday only
7. **Vol Shock** - 15-minute pause
8. **Time Guard** - Block entries 13:55-14:10

### Drawdown Governor (V2.26) + Dynamic Recovery (V2.29)

| Drawdown from HWM | Scale | Recovery Threshold |
|:------------------:|:-----:|:------------------:|
| < 3% | 100% | 8% |
| 3-6% | 75% | 6% |
| 6-10% | 50% | 4% |
| 10-15% | 25% | 2% |
| > 15% | 0% | — |

**V2.29 Dynamic Recovery:** `effective_recovery = DRAWDOWN_GOVERNOR_RECOVERY_BASE × governor_scale`
Config: `DRAWDOWN_GOVERNOR_RECOVERY_BASE = 0.08`

### Ghost Spread State Flush (V2.29 P0)

Three-layer reconciliation to prevent ghost spread state:
1. **OnOrderEvent** — Clear spread after both legs fill
2. **Portfolio check** — Clear if neither leg held after any option fill
3. **Friday safety net** — Weekly reconciliation clears any ghost state

---

## Cold Start Engine

**File:** `engines/core/cold_start_engine.py`
**Purpose:** Gradual entry during first 5 days after start or kill switch.

### Flowchart

```mermaid
flowchart TD
    START[Cold Start Check] --> DAY{Days Running?}

    DAY -->|Day 1| D1[No entries<br/>Build indicators]
    DAY -->|Days 2-5| D2[Warm Entry Logic]
    DAY -->|> Day 5| D5[Full Strategies Active]

    D2 --> TIME{Time >= 10:00?}
    TIME -->|No| WAIT[Wait for warm entry window]
    TIME -->|Yes| REGIME{Regime >= 50?}

    REGIME -->|No| SKIP[Skip - Regime too low]
    REGIME -->|Yes| ENTRY[Allow Entry<br/>50% sizing multiplier]

    D5 --> FULL[Full allocation]
```

### Config Values

| Parameter | Value | Description |
|-----------|------:|-------------|
| `COLD_START_DAYS` | 5 | Number of days in cold start mode |
| `WARM_ENTRY_SIZE_MULT` | 0.50 | 50% sizing during warm entry |
| `WARM_ENTRY_TIME` | "10:00" | Earliest warm entry time |
| `WARM_REGIME_MIN` | 50 | Minimum regime score for warm entry |
| `OPTIONS_COLD_START_MULTIPLIER` | 0.50 | V2.3.20: 50% options sizing during cold start |

---

## Startup Gate (V2.30)

**File:** `engines/core/startup_gate.py`
**Purpose:** All-weather time-based arming sequence. Separate from Cold Start (which resets on kill switch). Once fully armed, stays armed permanently. No regime dependency.

**V2.30 Design Principle:** "The gate controls HOW MUCH capital to deploy. The regime controls WHAT to deploy it in."

### Flowchart

```mermaid
flowchart TD
    START[Algorithm Launch] --> P0[Phase 0: INDICATOR_WARMUP<br/>5 days, hedges + yield only]

    P0 -->|5 days complete| P1[Phase 1: OBSERVATION<br/>5 days, + bearish options at 50%]

    P1 -->|5 days complete| P2[Phase 2: REDUCED<br/>5 days, all engines at 50%]
    P2 -->|5 days complete| P3[Phase 3: FULLY_ARMED<br/>Permanent, no restrictions]

    P3 --> DONE[StartupGate complete<br/>Never checked again]
```

### Phase Summary

| Phase | Duration | Hedges/Yield | Bearish Options | Directional Longs | Size Multiplier |
|-------|:--------:|:------------:|:---------------:|:-----------------:|:---------------:|
| INDICATOR_WARMUP | 5 days | Allowed | Blocked | Blocked | 0% |
| OBSERVATION | 5 days | Allowed | Allowed (50%) | Blocked | 50% |
| REDUCED | 5 days | Allowed | Allowed (50%) | Allowed (50%) | 50% |
| FULLY_ARMED | Permanent | Allowed | Allowed | Allowed | 100% |

### Granular API

| Method | Returns | Purpose |
|--------|:-------:|---------|
| `allows_hedges()` | Always True | Hedges are never gated |
| `allows_yield()` | Always True | Yield sleeve is never gated |
| `allows_bearish_options()` | OBSERVATION+ | PUT spreads in bear markets |
| `allows_directional_longs()` | REDUCED+ | Trend entries, MR, bullish options |
| `get_size_multiplier()` | 0.0 / 0.50 / 1.0 | Capital scaling factor |

### Config Values

| Parameter | Value | Description |
|-----------|------:|-------------|
| `STARTUP_GATE_ENABLED` | True | Master toggle |
| `STARTUP_GATE_WARMUP_DAYS` | 5 | Indicator warmup phase duration |
| `STARTUP_GATE_OBSERVATION_DAYS` | 5 | Observation phase duration |
| `STARTUP_GATE_REDUCED_DAYS` | 5 | Reduced phase duration |
| `STARTUP_GATE_REDUCED_SIZE_MULT` | 0.50 | Size multiplier during OBSERVATION/REDUCED |

### Relationship with Cold Start

- **StartupGate** is one-time and permanent. Never resets on kill switch.
- **ColdStartEngine** resets on kill switch (5-day warmup).
- StartupGate runs FIRST. While it gates directional longs, ColdStart still counts days silently.
- Once FULLY_ARMED, StartupGate is permanently out of the picture.
- No cross-dependencies between the two.

### Persistence

Persisted via `StateManager` with key `ALPHA_NEXTGEN_STARTUP_GATE`:
- `phase`: Current phase name
- `days_in_phase`: Days spent in current phase

Backward compatible: `restore_state()` maps `REGIME_GATE` to `INDICATOR_WARMUP`, reads `arming_days` as fallback for `days_in_phase`.

---

## Trend Engine

**File:** `engines/core/trend_engine.py`
**Purpose:** MA200 + ADX trend-following for core allocation (40%).

### Flowchart

```mermaid
flowchart TD
    START[Trend Signal Check] --> IND{Indicators Ready?}

    IND -->|No| NONE[No Signal]
    IND -->|Yes| ENTRY{Entry Check}

    ENTRY --> MA{Price > MA200?}
    MA -->|No| NO_ENTRY[No Entry Signal]
    MA -->|Yes| ADX{ADX >= 15?}

    ADX -->|No| NO_ENTRY
    ADX -->|Yes| REGIME{Regime >= 40?}

    REGIME -->|No| NO_ENTRY
    REGIME -->|Yes| ALLOC[Generate Entry Signal<br/>QLD: 15%, SSO: 12%<br/>TNA: 8%, FAS: 5%]

    subgraph EXIT[Exit Conditions]
        EX1[Price < MA200]
        EX2[ADX < 10]
        EX3[Stop Loss Hit]
        EX4[SMA50 Structural Exit V2.4<br/>Price < SMA50 × 0.98<br/>for 2 consecutive days]
        EX5[Hard Stop<br/>QLD/SSO: -15%, TNA/FAS: -12%]
    end

    EXIT --> EXIT_SIG[Generate Exit Signal<br/>target_weight = 0]
```

### Config Values

| Parameter | Value | Description |
|-----------|------:|-------------|
| `TREND_ADX_ENTRY_THRESHOLD` | 15 | ADX >= 15 for entry (V2.3.12) |
| `ADX_MODERATE_THRESHOLD` | 22 | ADX moderate threshold (V2.5) |
| `TREND_ADX_EXIT_THRESHOLD` | 10 | ADX < 10 for exit (V2.3.12) |
| `TREND_SYMBOL_ALLOCATIONS` | QLD: 0.15, SSO: 0.12, TNA: 0.08, FAS: 0.05 | Symbol weights (40% total) |
| `TREND_USE_SMA50_EXIT` | True | Enable SMA50 structural exit (V2.4) |
| `TREND_SMA50_PERIOD` | 50 | SMA50 lookback period |
| `TREND_SMA50_BUFFER` | 0.02 | 2% buffer below SMA50 for exit trigger |
| `TREND_SMA_CONFIRM_DAYS` | 2 | Consecutive days below SMA50 before exit (V2.5) |
| `TREND_HARD_STOP_2X` | 0.15 | 15% hard stop for QLD, SSO (V2.4) |
| `TREND_HARD_STOP_3X` | 0.12 | 12% hard stop for TNA, FAS (V2.4) |
| `CHANDELIER_ATR_PERIOD` | 14 | ATR period for stops |
| `CHANDELIER_BASE_MULT` | 3.5 | ATR multiplier for 2× ETFs |
| `CHANDELIER_3X_BASE_MULT` | 2.5 | ATR multiplier for 3× ETFs (TNA, FAS) |

### Stop Loss Progression

| Profit Range | 2× ETFs (QLD, SSO) | 3× ETFs (TNA, FAS) |
|:------------:|:------------------:|:------------------:|
| < 15% | ATR × 3.5 | ATR × 2.5 |
| 15-25% | ATR × 3.0 | ATR × 2.0 |
| > 25% | ATR × 2.5 | ATR × 1.5 |

---

## Mean Reversion Engine

**File:** `engines/satellite/mean_reversion_engine.py`
**Purpose:** RSI oversold bounce strategy for TQQQ/SOXL (10%).

### Flowchart

```mermaid
flowchart TD
    START[MR Signal Check] --> TIME{10:00-15:00 ET?}

    TIME -->|No| NONE[No Signal]
    TIME -->|Yes| RISK{Safeguards Clear?}

    RISK -->|No| BLOCKED[Entry Blocked<br/>Gap/Vol/Panic]
    RISK -->|Yes| RSI{RSI(5) < 25?}

    RSI -->|No| NO_ENTRY[No Entry Signal]
    RSI -->|Yes| DROP{Intraday Drop > 2.5%?}

    DROP -->|No| NO_ENTRY
    DROP -->|Yes| VIX{VIX < 35?}

    VIX -->|No| NO_ENTRY
    VIX -->|Yes| ENTRY[Generate Entry Signal<br/>TQQQ: 5%, SOXL: 5%<br/>Urgency: IMMEDIATE]

    subgraph EXIT[Exit Conditions - By 15:45]
        EX1[Profit Target +3%]
        EX2[Stop Loss -2%]
        EX3[Time Exit 15:45]
    end
```

### Config Values

| Parameter | Value | Description |
|-----------|------:|-------------|
| `MR_RSI_OVERSOLD` | 25 | RSI < 25 for entry |
| `MR_DROP_THRESHOLD` | 0.025 | Intraday drop > 2.5% |
| `MR_VIX_MAX` | 35 | VIX < 35 for entry |
| `MR_PROFIT_TARGET` | 0.03 | +3% profit target |
| `MR_STOP_LOSS` | 0.02 | -2% stop loss |
| `MR_WINDOW_START` | "10:00" | Entry window start |
| `MR_WINDOW_END` | "15:00" | Entry window end |
| `MR_FORCE_CLOSE_TIME` | "15:45" | Mandatory exit time |

---

## Options Engine

**File:** `engines/satellite/options_engine.py`
**Purpose:** QQQ options with Dual-Mode architecture (Swing 18.75% + Intraday 6.25%, total 25%).

### Dual-Mode Architecture

```mermaid
flowchart TD
    START[Options Signal Check] --> MODE{DTE Range?}

    MODE -->|14-45 DTE| SWING[SWING MODE<br/>18.75% Allocation]
    MODE -->|0-1 DTE| INTRADAY[INTRADAY MODE<br/>6.25% Allocation]

    SWING --> VASS{VASS: IV Environment?}
    VASS -->|VIX < 15| DEBIT_MONTHLY[Debit Spreads<br/>30-45 DTE Monthly]
    VASS -->|VIX 15-25| DEBIT_WEEKLY[Debit Spreads<br/>7-21 DTE Weekly]
    VASS -->|VIX > 25| CREDIT_WEEKLY[Credit Spreads<br/>7-14 DTE Weekly]

    DEBIT_MONTHLY --> SPREAD_ORDER[Spread Order]
    DEBIT_WEEKLY --> SPREAD_ORDER
    CREDIT_WEEKLY --> SPREAD_ORDER

    INTRADAY --> MICRO[Micro Regime Engine<br/>VIX Level × VIX Direction]
    MICRO --> STRAT{Strategy Selection}
    STRAT --> FADE[DEBIT_FADE<br/>Mean Reversion]
    STRAT --> MOMENTUM[ITM_MOMENTUM<br/>Trend Following]
    STRAT --> CREDIT[CREDIT_SPREAD<br/>Theta Decay]
```

### VASS (VIX-Adaptive Strategy Selection) — V2.8

VASS routes swing mode trades to the appropriate strategy based on the current IV environment.

| IV Environment | VIX Range | Strategy | DTE Range |
|----------------|-----------|----------|-----------|
| Low IV | < 15 | Debit Spreads | 30-45 (Monthly) |
| Medium IV | 15-25 | Debit Spreads | 7-21 (Weekly) |
| High IV | > 25 | Credit Spreads | 7-14 (Weekly) |

### Credit Spread Config (V2.8)

| Parameter | Value | Description |
|-----------|------:|-------------|
| `CREDIT_SPREAD_MIN_CREDIT` | 0.30 | Minimum credit received per spread |
| `CREDIT_SPREAD_WIDTH_TARGET` | 5.0 | Target spread width in dollars |
| `CREDIT_SPREAD_SHORT_LEG_DELTA_MIN` | 0.25 | Min delta for short leg |
| `CREDIT_SPREAD_SHORT_LEG_DELTA_MAX` | 0.40 | Max delta for short leg |
| `CREDIT_SPREAD_PROFIT_TARGET` | 0.50 | 50% profit target (buy back at 50% of credit) |
| `CREDIT_SPREAD_STOP_MULTIPLIER` | 2.0 | Stop at 2× credit received |

### Elastic Delta Bands (V2.24.1)

Dynamic delta adjustment based on profit/loss levels.

| Parameter | Value | Description |
|-----------|------:|-------------|
| `ELASTIC_DELTA_STEPS` | [0.0, 0.03, 0.07, 0.12] | P&L breakpoints for delta adjustment |
| `ELASTIC_DELTA_FLOOR` | 0.10 | Minimum delta floor |
| `ELASTIC_DELTA_CEILING` | 0.95 | Maximum delta ceiling |

### Sizing Caps (V2.18)

| Parameter | Value | Description |
|-----------|------:|-------------|
| `SWING_SPREAD_MAX_DOLLARS` | 7,500 | Max dollar allocation per swing spread |
| `INTRADAY_SPREAD_MAX_DOLLARS` | 4,000 | Max dollar allocation per intraday spread |

### Safety Rules (V2.4.1)

| Parameter | Value | Description |
|-----------|------:|-------------|
| `SWING_FALLBACK_ENABLED` | False | Single-leg fallback disabled (V2.4.1) |
| Friday Firewall | — | No new swing entries on Fridays |

### Micro Regime Engine (Intraday)

```mermaid
flowchart TD
    START[Micro Regime] --> VIX_LEVEL{VIX Level?}

    VIX_LEVEL -->|< 11.5| VERY_CALM[VERY_CALM]
    VIX_LEVEL -->|< 15| CALM[CALM]
    VIX_LEVEL -->|< 18| NORMAL[NORMAL]
    VIX_LEVEL -->|< 22| ELEVATED[ELEVATED]
    VIX_LEVEL -->|< 28| HIGH[HIGH]
    VIX_LEVEL -->|< 40| VERY_HIGH[VERY_HIGH]
    VIX_LEVEL -->|>= 40| EXTREME[EXTREME]

    VIX_DIR[VIX Direction] --> DIR{Change from Open?}
    DIR -->|< -3%| FALLING_FAST
    DIR -->|< -1.5%| FALLING
    DIR -->|< -0.5%| DRIFTING_DOWN
    DIR -->|< 0.5%| FLAT
    DIR -->|< 1.5%| DRIFTING_UP
    DIR -->|< 3%| RISING
    DIR -->|>= 3%| RISING_FAST
```

### Config Values - Swing Mode

| Parameter | Value | Description |
|-----------|------:|-------------|
| `OPTIONS_SWING_ALLOCATION` | 0.1875 | 18.75% portfolio allocation |
| `OPTIONS_SWING_DTE_MIN` | 14 | Minimum DTE for swing (V2.4, was 6) |
| `OPTIONS_SWING_DTE_MAX` | 45 | Maximum DTE for swing |
| `OPTIONS_DTE_MAX` | 60 | Universe filter max DTE (V2.23, was 30) |
| `SPREAD_DTE_MIN` | 14 | Spread minimum DTE (V2.4, was 6) |
| `OPTIONS_SWING_DELTA_MIN` | 0.55 | Min delta for swing |
| `OPTIONS_SWING_DELTA_MAX` | 0.85 | Max delta for swing |
| `OPTIONS_SINGLE_LEG_DTE_EXIT` | 4 | Exit single-leg at 4 DTE (V2.3.18) |
| `OPTIONS_SPREAD_DTE_EXIT` | 5 | Exit spreads at 5 DTE |
| SetFilter strikes | -25 to +25 | Strike range (V2.23, was -8 to +5) |

### Config Values - Intraday Mode

| Parameter | Value | Description |
|-----------|------:|-------------|
| `OPTIONS_INTRADAY_ALLOCATION` | 0.0625 | 6.25% portfolio allocation |
| `OPTIONS_INTRADAY_DTE_MAX` | 1 | Max DTE for intraday (0-1 DTE) |
| `OPTIONS_INTRADAY_DELTA_MIN` | 0.40 | Min delta for intraday |
| `OPTIONS_INTRADAY_DELTA_MAX` | 0.60 | Max delta for intraday |
| `INTRADAY_MAX_TRADES_PER_DAY` | 2 | Sniper mode: 2 trades max |
| `QQQ_NOISE_THRESHOLD` | 0.0035 | 0.35% minimum move for signal |
| `INTRADAY_FADE_MIN_MOVE` | 0.0050 | 0.50% min for FADE |
| `INTRADAY_FADE_MAX_MOVE` | 0.0120 | 1.20% max for FADE |
| `INTRADAY_MOMENTUM_MIN_MOVE` | 0.0080 | 0.80% min for MOMENTUM |
| `INTRADAY_DEBIT_FADE_VIX_MIN` | 13.5 | Min VIX for intraday debit fade (V2.19) |

### Config Values - Cold Start (V2.3.20)

| Parameter | Value | Description |
|-----------|------:|-------------|
| `OPTIONS_COLD_START_MULTIPLIER` | 0.50 | 50% sizing during cold start |

### Neutrality Exit (V2.22)

| Parameter | Value | Description |
|-----------|------:|-------------|
| `SPREAD_NEUTRALITY_EXIT_ENABLED` | True | Enable hysteresis shield neutrality exit (V2.22) |

When a spread's net value approaches zero (neutrality), the system triggers an exit to avoid holding worthless positions. The hysteresis shield prevents rapid re-entry after a neutrality exit.

---

## Hedge Engine

**File:** `engines/satellite/hedge_engine.py`
**Purpose:** Regime-based tail protection with TMF and PSQ.

### Flowchart

```mermaid
flowchart TD
    START[Hedge Allocation] --> REGIME{Regime Score?}

    REGIME -->|>= 70| NONE[0% Hedge<br/>Full Risk-On]
    REGIME -->|60-69| L1[Level 1: 5% TMF]
    REGIME -->|50-59| L2[Level 2: 10% TMF + 5% PSQ]
    REGIME -->|40-49| L3[Level 3: 15% TMF + 10% PSQ]
    REGIME -->|< 40| L4[Level 4: 20% TMF + 10% PSQ]

    L1 --> EMIT[Emit TargetWeight<br/>Urgency: EOD]
    L2 --> EMIT
    L3 --> EMIT
    L4 --> EMIT
```

### Config Values

| Parameter | Value | Description |
|-----------|------:|-------------|
| `HEDGE_REGIME_THRESHOLD` | 70 | No hedge above this score |
| `HEDGE_L1_THRESHOLD` | 60 | Level 1 hedge threshold |
| `HEDGE_L2_THRESHOLD` | 50 | Level 2 hedge threshold |
| `HEDGE_L3_THRESHOLD` | 40 | Level 3 hedge threshold |
| `HEDGE_TMF_MAX` | 0.20 | Max TMF allocation |
| `HEDGE_PSQ_MAX` | 0.10 | Max PSQ allocation |

---

## Yield Sleeve

**File:** `engines/satellite/yield_sleeve.py`
**Purpose:** Cash management via SHV (Short-Term Treasury ETF).

### Flowchart

```mermaid
flowchart TD
    START[Yield Calculation] --> CASH[Calculate Unallocated Cash<br/>= Equity - All Positions - Cash Buffer]

    CASH --> BUFFER[Subtract 10% Cash Buffer<br/>V2.3.17]

    BUFFER --> MIN{Unallocated > $10K?}
    MIN -->|No| NONE[No SHV Trade]
    MIN -->|Yes| MARGIN{Check MarginRemaining}

    MARGIN --> CAP[Cap at 95% of Margin]
    CAP --> BUY[Buy SHV<br/>Urgency: EOD]
```

### Config Values

| Parameter | Value | Description |
|-----------|------:|-------------|
| `SHV_MIN_TRADE` | 10,000 | Minimum trade size (V2.3.6) |
| `CASH_BUFFER_PCT` | 0.10 | 10% cash buffer (V2.3.17) |
| `YIELD_ALLOCATION_MAX` | 0.99 | Max SHV allocation (V2.3.17) |

---

## Portfolio Router

**File:** `portfolio/portfolio_router.py`
**Purpose:** Central hub that aggregates, validates, and routes signals.

### Processing Flow

```mermaid
flowchart TD
    START[Receive Signals] --> COLLECT[1. COLLECT<br/>All TargetWeight objects]

    COLLECT --> AGG[2. AGGREGATE<br/>By symbol, net opposing]

    AGG --> VALIDATE[3. VALIDATE<br/>Check exposure limits]

    VALIDATE --> SHV{Need Cash for Buys?}
    SHV -->|Yes| AUTO[Auto-Liquidate SHV<br/>V2.3.20]
    SHV -->|No| NET

    AUTO --> NET[4. NET<br/>vs Current positions]

    NET --> PRIO[5. PRIORITIZE<br/>IMMEDIATE > EOD]

    PRIO --> EXEC[6. EXECUTE<br/>Generate OrderIntents]

    EXEC --> IMMED{Urgency?}
    IMMED -->|IMMEDIATE| MARKET[Market Orders NOW]
    IMMED -->|EOD| MOO[Queue for 15:45 MOO]
```

### Capital Firewall (V2.18)

Capital is partitioned between trend and options to prevent one strategy from consuming the other's allocation.

| Parameter | Value | Description |
|-----------|------:|-------------|
| `CAPITAL_PARTITION_TREND` | 0.50 | 50% of tradeable equity reserved for trend |
| `CAPITAL_PARTITION_OPTIONS` | 0.50 | 50% of tradeable equity reserved for options |
| `MAX_MARGIN_WEIGHTED_ALLOCATION` | 0.90 | 90% leverage cap across all positions |

### Price Discovery Chain (V2.19 / V2.24)

3-layer fallback for option price resolution:

1. **Mid-price** (ask + bid) / 2 — preferred
2. **Last trade price** — if mid is unavailable or zero
3. **Theoretical price** (Black-Scholes) — last resort

This prevents ghost fills and ensures accurate position valuation.

### Limit Orders (V2.19)

| Parameter | Value | Description |
|-----------|------:|-------------|
| `OPTIONS_USE_LIMIT_ORDERS` | True | Use limit orders for options (V2.19) |

Options orders default to limit orders at mid-price instead of market orders to avoid slippage on wide bid-ask spreads.

### SHV Auto-Liquidation (V2.3.20)

```python
# Calculate shortfall
buy_value = sum(order.quantity * price for order in buys)
sell_proceeds = sum(order.quantity * price for order in sells)
projected_cash = available_cash + sell_proceeds
shortfall = buy_value - projected_cash

if shortfall > 0:
    shv_sell_amount = min(shortfall * 1.05, available_shv)
    # Generate SHV SELL order, insert at beginning
```

### Exposure Group Limits

| Group | Max Net Long | Max Gross | Symbols |
|-------|:------------:|:---------:|---------|
| NASDAQ_BETA | 50% | 75% | QLD, TQQQ, PSQ |
| SPY_BETA | 40% | 40% | SSO |
| SMALL_CAP_BETA | 25% | 25% | TNA |
| FINANCIALS_BETA | 15% | 15% | FAS |
| RATES | 99% | 99% | TMF, SHV (V2.3.17) |

### Source Allocation Limits

| Source | Max % | Description |
|--------|------:|-------------|
| TREND | 40% | Core trend following |
| OPT | 30% | Swing options |
| OPT_INTRADAY | 5% | Intraday options |
| MR | 10% | Mean reversion |
| HEDGE | 30% | Hedging |
| YIELD | 99% | Cash management (V2.3.17) |
| COLD_START | 35% | Cold start entries |
| RISK | 100% | Risk-driven exits |
| ROUTER | 100% | Router-initiated trades |

---

## Key Thresholds Quick Reference

### Risk Controls

| Safeguard | Threshold | Action |
|-----------|:---------:|--------|
| Kill Switch | 5% daily loss | Liquidate ALL (V2.3.17) |
| Panic Mode | SPY -4% | Liquidate longs |
| Weekly Breaker | 5% WTD loss | 50% sizing |
| Gap Filter | SPY -1.5% gap | Block MR |
| Vol Shock | 3× ATR | 15-min pause |
| Time Guard | 13:55-14:10 | Block entries |

### Entry Conditions

| Engine | Key Threshold | Value |
|--------|--------------|:-----:|
| Trend | ADX Entry | >= 15 |
| Trend | MA200 | Price > MA200 |
| MR | RSI Oversold | < 25 |
| MR | Drop | > 2.5% |
| MR | VIX | < 35 |
| Options | Regime | >= 40 |
| Options (Swing) | DTE | 14-45 |
| Options (Intraday) | DTE | 0-1 |

### Key Times (Eastern)

| Time | Event |
|:----:|-------|
| 09:25 | Set equity_prior_close |
| 09:30 | Market open, MOO executes |
| 09:33 | Set equity_sod, check gap |
| 10:00 | Warm entry window opens |
| 13:55 | Time guard starts |
| 14:10 | Time guard ends |
| 15:00 | MR window closes |
| 15:30 | Intraday options force close |
| 15:45 | EOD processing, MOO submit, TQQQ/SOXL close |
| 16:00 | Market close, state persist |

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| V2.3.17 | 2026-02-01 | Kill switch 3%→5%, 10% cash buffer, RATES/YIELD 99% |
| V2.3.18 | 2026-02-01 | Single-leg exit 2→4 DTE, Swing entry 5→6 DTE |
| V2.3.19 | 2026-02-01 | ITM_MOMENTUM time window to config |
| V2.3.20 | 2026-02-01 | Cold start options 50%, SHV auto-liquidation |
| V2.4 | 2026-02-01 | SMA50 structural exit, hard stops, width-based short leg |
| V2.5 | 2026-02-01 | ADX moderate threshold 22, SMA confirm 2 days |
| V2.8 | 2026-02-01 | VASS, credit spreads, IV environment routing |
| V2.18 | 2026-02-02 | Capital firewall 50/50, leverage cap 90%, sizing caps |
| V2.19 | 2026-02-02 | Limit orders, VIX filter, ghost margin fix |
| V2.22 | 2026-02-02 | Neutrality exit (hysteresis shield) |
| V2.23 | 2026-02-02 | Universe filter +/-25 strikes, 0-60 DTE |
| V2.24 | 2026-02-03 | Router price failsafe, spread filter diagnostics |
| V2.24.2 | 2026-02-03 | Runtime error fix, DTE double-filter fix, push 413 fix |
| V2.26 | 2026-02-03 | Drawdown governor, chop detector |
| V2.28.1 | 2026-02-04 | Graduated KS tiers (2/4/6%), tightened governor |
| V2.29 | 2026-02-04 | Ghost spread flush, dynamic governor recovery, StartupGate |
| V2.30 | 2026-02-04 | All-Weather StartupGate redesign, bearish options path fix |

---

*Document: docs/system/ENGINE_LOGIC_REFERENCE.md*
*Created: 2026-02-01*
*Author: Engineering Team*
