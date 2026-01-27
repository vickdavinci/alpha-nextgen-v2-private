# Section 2: System Architecture

[← Executive Summary](01-executive-summary.md) | [Table of Contents](00-table-of-contents.md) | [Data Infrastructure →](03-data-infrastructure.md)

---

## Overview

Alpha NextGen is a modular algorithmic trading system built on a hub-and-spoke architecture. The Portfolio Router serves as the central hub, coordinating signals from multiple strategy engines while core engines (Regime, Capital, Risk) provide the foundational context that governs all trading decisions.

**Design Philosophy:**
- Separation of concerns (each engine has one job)
- Strategies emit signals, Router decides execution
- Risk controls have absolute authority
- State persists across restarts

---

## Master Architecture Diagram
```mermaid
flowchart TB
    subgraph DATA["DATA LAYER"]
        direction LR
        subgraph PROXY["Proxy Data (Daily)"]
            SPY_D["SPY"]
            RSP_D["RSP"]
            HYG_D["HYG"]
            IEF_D["IEF"]
        end
        subgraph TRADED["Traded Data (Minute)"]
            TQQQ["TQQQ"]
            SOXL["SOXL"]
            QLD["QLD"]
            SSO["SSO"]
            TMF["TMF"]
            PSQ["PSQ"]
            SHV["SHV"]
            SPY_M["SPY"]
        end
    end

    subgraph CORE["CORE ENGINES"]
        direction LR
        REGIME["REGIME ENGINE<br/>─────────────<br/>4 Proxy Factors<br/>Score 0-100<br/>Smoothing α=0.3<br/>State Classification"]
        CAPITAL["CAPITAL ENGINE<br/>─────────────<br/>SEED/GROWTH Phase<br/>Virtual Lockbox<br/>Tradeable Equity<br/>Position Limits"]
        RISK["RISK ENGINE<br/>─────────────<br/>Kill Switch -3%<br/>Panic Mode -4%<br/>Weekly Breaker<br/>Gap/Vol/Time Guard"]
    end

    subgraph STRATEGIES["STRATEGY ENGINES"]
        direction LR
        TREND["TREND<br/>─────────<br/>QLD/SSO<br/>MA200+ADX<br/>Chandelier Stop<br/>Urgency: EOD"]
        MR["MEAN REV<br/>─────────<br/>TQQQ/SOXL<br/>RSI < 25<br/>Drop > 2.5%<br/>Urgency: IMMED"]
        HEDGE["HEDGE<br/>─────────<br/>TMF/PSQ<br/>Regime < 40<br/>Scaled Alloc<br/>Urgency: EOD"]
        YIELD["YIELD<br/>─────────<br/>SHV<br/>Cash > $2k<br/>LIFO Liquidate<br/>Urgency: EOD"]
        COLD["COLD START<br/>─────────<br/>Days 1-5<br/>Regime > 50<br/>50% Size<br/>Urgency: IMMED"]
    end

    subgraph ROUTER["PORTFOLIO ROUTER (HUB)"]
        direction TB
        R1["1. COLLECT TargetWeights"]
        R2["2. AGGREGATE by Symbol"]
        R3["3. VALIDATE vs Limits"]
        R4["4. NET vs Current"]
        R5["5. PRIORITIZE by Urgency"]
        R6["6. EXECUTE"]
        R1 --> R2 --> R3 --> R4 --> R5 --> R6
    end

    subgraph EXECUTION["EXECUTION ENGINE"]
        direction LR
        MARKET["MARKET ORDERS<br/>─────────────<br/>MR Entry/Exit<br/>Stop Losses<br/>Kill Switch<br/>Panic Mode"]
        MOO["MOO ORDERS<br/>─────────────<br/>Trend Entry/Exit<br/>Hedge Rebalance<br/>Yield Sleeve<br/>Submit @ 15:45"]
    end

    DATA --> CORE
    CORE --> STRATEGIES
    STRATEGIES -->|"TargetWeight Objects"| ROUTER
    RISK -.->|"GO/NO-GO"| ROUTER
    ROUTER --> EXECUTION
    EXECUTION --> BROKER["BROKER<br/>(IBKR)"]
```

---

## Component Layers

### Layer 1: Data Layer

Provides market data at appropriate resolutions.

| Data Type | Resolution | Purpose |
|-----------|:----------:|---------|
| Proxy Symbols | Daily | Regime calculation only (never traded) |
| Traded Symbols | Minute | Strategy signals and execution |
| SPY | Both | Regime (daily) + Safeguards (minute) |

### Layer 2: Core Engines

Provide context that governs all trading decisions.

| Engine | Responsibility | Output |
|--------|----------------|--------|
| **Regime Engine** | Detect market state | Score 0-100, State classification |
| **Capital Engine** | Manage account phases | Tradeable equity, Position limits |
| **Risk Engine** | Protect capital | GO/NO-GO signals, Circuit breakers |

### Layer 3: Strategy Engines

Generate trading signals based on their specific logic.

| Strategy | Style | Holding Period | Urgency | Allocation |
|----------|-------|:---------------|:-------:|:----------:|
| **Trend** | MA200+ADX swing | Days to weeks | EOD | 70% (Core) |
| **Mean Reversion** | RSI oversold + VIX | Minutes to hours | IMMEDIATE | 0-10% |
| **Options** | 4-factor QQQ | Intraday | IMMEDIATE | 20-30% |
| **Hedge** | Tail protection | As needed | EOD | Per regime |
| **Yield** | Cash management | Ongoing | EOD | Remainder |
| **Cold Start** | Safe deployment | Days 1-5 | IMMEDIATE | 50% sizing |

### Layer 4: Portfolio Router

Central coordinator that aggregates, validates, and routes signals.

**Key Functions:**
1. Collect TargetWeight objects from all strategies
2. Aggregate weights by symbol (net opposing signals)
3. Validate against exposure group limits
4. Calculate share deltas vs current positions
5. Prioritize by urgency (IMMEDIATE vs EOD)
6. Route to Execution Engine

### Layer 5: Execution Engine

Converts validated signals into broker orders.

| Order Type | When Used | Timing |
|------------|-----------|--------|
| Market | IMMEDIATE urgency | Execute now |
| MOO | EOD urgency | Submit at 15:45, execute at next open |

---

## Data Flow Diagram
```mermaid
flowchart TB
    subgraph MARKET["MARKET DATA"]
        MD1["Daily Bars<br/>SPY, RSP, HYG, IEF"]
        MD2["Minute Bars<br/>All Traded Symbols"]
    end

    subgraph INDICATORS["INDICATORS"]
        IND1["SMA 20/50/200"]
        IND2["ADX 14 (Trend Strength)"]
        IND3["ATR 14"]
        IND4["RSI 5"]
        IND5["Realized Vol"]
        IND6["VIX (MR Filter)"]
    end

    subgraph REGIME["REGIME ENGINE"]
        RE1["Trend Factor 35%"]
        RE2["Vol Factor 25%"]
        RE3["Breadth Factor 25%"]
        RE4["Credit Factor 15%"]
        RE5["Score 0-100"]
        RE6["State Classification"]
    end

    subgraph CAPITAL["CAPITAL ENGINE"]
        CE1["Total Equity"]
        CE2["Phase Check"]
        CE3["Lockbox Check"]
        CE4["Tradeable Equity"]
    end

    subgraph STRATEGIES["STRATEGY SIGNALS"]
        ST1["Trend Signals"]
        ST2["MR Signals"]
        ST3["Hedge Targets"]
        ST4["Yield Target"]
    end

    subgraph ROUTER["PORTFOLIO ROUTER"]
        RO1["Aggregate"]
        RO2["Validate"]
        RO3["Execute"]
    end

    MD1 --> IND1 & IND5
    MD2 --> IND2 & IND3 & IND4

    IND1 --> RE1
    IND5 --> RE2
    MD1 --> RE3 & RE4

    RE1 & RE2 & RE3 & RE4 --> RE5 --> RE6

    CE1 --> CE2 --> CE3 --> CE4

    RE6 --> ST1 & ST2 & ST3
    CE4 --> ST1 & ST2 & ST4
    IND2 --> ST1
    IND4 --> ST2

    ST1 & ST2 & ST3 & ST4 --> RO1 --> RO2 --> RO3
```

---

## Authority Hierarchy

When conflicts arise, higher levels ALWAYS override lower levels.
```mermaid
flowchart TB
    subgraph L1["LEVEL 1: OPERATIONAL SAFETY (Highest)"]
        L1A["Broker Connection"]
        L1B["Data Freshness"]
        L1C["Symbol Halts"]
        L1D["Split Detection"]
    end

    subgraph L2["LEVEL 2: CIRCUIT BREAKERS"]
        L2A["Kill Switch -3%"]
        L2B["Panic Mode -4%"]
        L2C["Weekly Breaker -5%"]
        L2D["Vol Shock 3×ATR"]
        L2E["Gap Filter -1.5%"]
        L2F["Time Guard"]
    end

    subgraph L3["LEVEL 3: REGIME CONSTRAINTS"]
        L3A["Score < 30: No New Longs"]
        L3B["Score < 40: Hedges Required"]
        L3C["Score ≤ 50: No Cold Start"]
    end

    subgraph L4["LEVEL 4: CAPITAL CONSTRAINTS"]
        L4A["Phase Position Limits"]
        L4B["Group Exposure Caps"]
        L4C["Lockbox Reservations"]
        L4D["Min Trade Size $2k"]
    end

    subgraph L5["LEVEL 5: STRATEGY SIGNALS"]
        L5A["Trend Signals"]
        L5B["MR Signals"]
        L5C["Hedge Signals"]
        L5D["Yield Signals"]
        L5E["Cold Start Signals"]
    end

    subgraph L6["LEVEL 6: EXECUTION (Lowest)"]
        L6A["Order Type Selection"]
        L6B["SHV Liquidation Priority"]
        L6C["Order Tagging"]
    end

    L1 --> L2 --> L3 --> L4 --> L5 --> L6
```

### Authority Examples

| Scenario | Resolution |
|----------|------------|
| Trend wants to buy QLD, but Kill Switch triggered | **Kill Switch wins** - No trade |
| MR wants to enter TQQQ, but Gap Filter active | **Gap Filter wins** - Entry blocked |
| Hedge wants 20% TMF, but Regime is 60 | **Regime wins** - No hedge needed |
| Trend wants 50% QLD, but NASDAQ_BETA already at 45% | **Exposure Limit wins** - Reduced to 5% |
| MR and Trend both want QLD | **Router aggregates** - Net weight applied |

---

## Engine Interaction Map

| Engine | Receives From | Sends To |
|--------|---------------|----------|
| **Regime** | Data Layer (proxy symbols) | All Strategy Engines, Risk Engine |
| **Capital** | Portfolio state | All Strategy Engines, Router |
| **Risk** | Portfolio state, SPY minute data, Greeks | Router (GO/NO-GO) |
| **Cold Start** | Regime, Capital, Risk | Router |
| **Trend** | Data Layer (MA200, ADX), Regime, Capital | Router |
| **Mean Reversion** | Data Layer, Regime, Risk, VIX | Router |
| **Options** | Data Layer (QQQ), IV, Greeks, Regime | Router, OCO Manager |
| **Hedge** | Regime | Router |
| **Yield** | Portfolio cash | Router |
| **Router** | All Strategies, Risk | Execution Engine |
| **OCO Manager** | Options Engine | Execution Engine |
| **Execution** | Router, OCO Manager | Broker |

---

## Signal Flow: TargetWeight Object

All strategies communicate with the Router using a standardized TargetWeight structure:

| Field | Type | Description |
|-------|------|-------------|
| `symbol` | Symbol | Which instrument |
| `weight` | float | Target portfolio percentage (0.0 to 1.0) |
| `strategy` | string | Which strategy generated this |
| `urgency` | enum | IMMEDIATE or EOD |
| `reason` | string | Why this signal (for logging) |

**Example TargetWeight:**
```
Symbol: QLD
Weight: 0.40 (40% of portfolio)
Strategy: TREND
Urgency: EOD
Reason: MA200_ADX_ENTRY
```

---

## Urgency Classification

| Urgency | Order Type | When Executed | Used By |
|---------|------------|---------------|---------|
| **IMMEDIATE** | Market Order | Now | MR entry/exit, Stops, Kill Switch, Panic Mode, Warm Entry |
| **EOD** | MOO Order | Next market open | Trend entry/exit, Hedge rebalance, Yield |

---

## System Boundaries

### What the System Does

- Trades US equity ETFs (leveraged and inverse)
- Uses regime detection to adapt to market conditions
- Combines multiple strategies in a single portfolio
- Manages risk with multiple circuit breakers
- Persists state across restarts

### What the System Does NOT Do

- Trade futures or forex
- Use margin beyond ETF leverage
- Hold 3x leveraged products overnight (except TMF hedge)
- Trade during pre-market or after-hours
- Hold options overnight (closed by 15:45)

### V2.1 Additions

- **Options Engine**: Trades QQQ options (20-30% allocation) using 4-factor entry scoring
- **VIX Regime Filter**: Adjusts MR parameters based on VIX level
- **5-Level Circuit Breaker**: Graduated risk response system
- **Greeks Monitoring**: Portfolio delta/gamma/vega limits

---

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| **Proxy symbols for Regime** | Cleaner signals, no interference from traded symbols |
| **Separate MR and Trend instruments** | 3x for intraday (no overnight decay), 2x for swing (acceptable decay) |
| **Router as central hub** | Single point of coordination, prevents conflicts |
| **Two urgency levels** | Time-sensitive MR vs. patient Trend |
| **Static exposure groups** | Simpler than rolling correlation, easier to validate |
| **Smoothed regime score** | Prevents whipsaw from daily noise |

---

## Dependencies

**Depends On:**
- Section 3: Data Infrastructure (symbol definitions)

**Used By:**
- All subsequent sections (reference architecture)

---

[← Executive Summary](01-executive-summary.md) | [Table of Contents](00-table-of-contents.md) | [Data Infrastructure →](03-data-infrastructure.md)