# Alpha NextGen - Project Documentation

## Overview

Alpha NextGen is a multi-strategy algorithmic trading system designed for the QuantConnect platform. It combines regime-based market state detection, multiple trading strategies, and comprehensive risk management to trade leveraged ETFs.

**Target Platform:** QuantConnect (LEAN Engine)
**Target Broker:** Interactive Brokers
**Initial Capital:** $50,000 (SEED Phase)
**Asset Class:** US Equity ETFs (Leveraged and Inverse)

---

## Documentation Structure

This documentation is organized into 17 sections, each self-contained with relevant diagrams embedded inline.
```
docs/
├── 00-table-of-contents.md      # Master index and navigation
├── 01-executive-summary.md      # Goals, constraints, key decisions
├── 02-system-architecture.md    # Overall design and component relationships
├── 03-data-infrastructure.md    # Symbols, indicators, data quality rules
├── 04-regime-engine.md          # Market state detection (0-100 score)
├── 05-capital-engine.md         # Phase management and lockbox
├── 06-cold-start-engine.md      # First 5 days deployment handling
├── 07-trend-engine.md           # Swing breakout strategy (QLD, SSO)
├── 08-mean-reversion-engine.md  # Intraday fade strategy (TQQQ, SOXL)
├── 09-hedge-engine.md           # Tail risk protection (TMF, PSQ)
├── 10-yield-sleeve.md           # Cash management (SHV)
├── 11-portfolio-router.md       # Signal aggregation and coordination
├── 12-risk-engine.md            # Circuit breakers and safeguards
├── 13-execution-engine.md       # Order types and fill handling
├── 14-daily-operations.md       # Timeline and scheduled events
├── 15-state-persistence.md      # ObjectStore and survival across restarts
├── 16-appendix-parameters.md    # All tunable parameters in one place
├── 17-appendix-glossary.md      # Trading and system term definitions
└── reference/
    ├── QC_RULES.md              # QuantConnect coding patterns
    └── ERRORS.md                # Common errors and fixes
```

---

## Section Overview

### Foundation

| Section | Title | Description |
|:-------:|-------|-------------|
| [01](01-executive-summary.md) | **Executive Summary** | Project goals, design philosophy, key constraints, and critical decisions that shaped the system |
| [02](02-system-architecture.md) | **System Architecture** | High-level component diagram, data flow, engine relationships, and authority hierarchy |

### Data Layer

| Section | Title | Description |
|:-------:|-------|-------------|
| [03](03-data-infrastructure.md) | **Data Infrastructure** | Proxy vs traded symbols, resolution requirements, indicator definitions, exposure groups, and data quality rules |

### Core Engines

| Section | Title | Description |
|:-------:|-------|-------------|
| [04](04-regime-engine.md) | **Regime Engine** | Four-factor market state scoring (trend, volatility, breadth, credit), smoothing, and state classification |
| [05](05-capital-engine.md) | **Capital Engine** | SEED/GROWTH phase management, position limits, virtual lockbox profit protection |
| [12](12-risk-engine.md) | **Risk Engine** | Kill switch (-3%), panic mode (-4% SPY), weekly breaker, gap filter, vol shock, time guard |

### Strategy Engines

| Section | Title | Description |
|:-------:|-------|-------------|
| [06](06-cold-start-engine.md) | **Cold Start Engine** | Days 1-5 handling, warm entry logic, reduced position sizing |
| [07](07-trend-engine.md) | **Trend Engine** | Bollinger Band compression breakouts, Chandelier trailing stops, EOD signals |
| [08](08-mean-reversion-engine.md) | **Mean Reversion Engine** | RSI oversold detection, intraday-only positions, +2%/-2% exits |
| [09](09-hedge-engine.md) | **Hedge Engine** | Regime-based TMF/PSQ allocation, tail risk protection |
| [10](10-yield-sleeve.md) | **Yield Sleeve** | SHV for idle cash, LIFO liquidation, lockbox investment |

### Execution Layer

| Section | Title | Description |
|:-------:|-------|-------------|
| [11](11-portfolio-router.md) | **Portfolio Router** | TargetWeight aggregation, exposure limit validation, urgency-based routing |
| [13](13-execution-engine.md) | **Execution Engine** | Market orders, MOO orders, fallback handling, fill processing |

### Operations

| Section | Title | Description |
|:-------:|-------|-------------|
| [14](14-daily-operations.md) | **Daily Operations** | Complete timeline from 09:00 to 16:00, scheduled events, engine activation matrix |
| [15](15-state-persistence.md) | **State Persistence** | What survives restarts, ObjectStore usage, save/load triggers |

### Appendices

| Section | Title | Description |
|:-------:|-------|-------------|
| [16](16-appendix-parameters.md) | **Parameters Reference** | All tunable values with defaults, ranges, and descriptions |
| [17](17-appendix-glossary.md) | **Glossary** | Definitions for trading terms and system-specific concepts |

---

## Traded Instruments

| Symbol | Type | Leverage | Strategy Use | Overnight Hold |
|--------|------|:--------:|--------------|:--------------:|
| **TQQQ** | Nasdaq 100 | 3x | Mean Reversion | ❌ Never |
| **SOXL** | Semiconductor | 3x | Mean Reversion | ❌ Never |
| **QLD** | Nasdaq 100 | 2x | Trend, Cold Start | ✅ Yes |
| **SSO** | S&P 500 | 2x | Trend, Cold Start | ✅ Yes |
| **TMF** | 20+ Year Treasury | 3x | Hedge | ✅ Yes |
| **PSQ** | Nasdaq 100 Inverse | 1x | Hedge | ✅ Yes |
| **SHV** | Short Treasury | 1x | Yield | ✅ Yes |

**Proxy Symbols (Regime Calculation Only):**
- SPY - S&P 500 (trend, volatility)
- RSP - Equal Weight S&P 500 (breadth)
- HYG - High Yield Corporate Bond (credit)
- IEF - 7-10 Year Treasury (credit)

---

## System States

| Regime Score | State | New Longs | Hedges | Cold Start |
|:------------:|-------|:---------:|:------:|:----------:|
| 70-100 | **RISK_ON** | ✅ Full | ❌ None | ✅ Allowed |
| 50-69 | **NEUTRAL** | ✅ Full | ❌ None | ✅ If >50 |
| 40-49 | **CAUTIOUS** | ✅ Full | 10% TMF | ❌ Blocked |
| 30-39 | **DEFENSIVE** | ⚠️ Reduced | 15% TMF, 5% PSQ | ❌ Blocked |
| 0-29 | **RISK_OFF** | ❌ None | 20% TMF, 10% PSQ | ❌ Blocked |

---

## Risk Controls Summary

| Control | Trigger | Action |
|---------|---------|--------|
| **Kill Switch** | -3% daily (from either baseline) | Liquidate ALL, disable trading, reset cold start |
| **Panic Mode** | SPY -4% intraday | Liquidate leveraged longs, keep hedges |
| **Weekly Breaker** | -5% week-to-date | Reduce all positions 50% |
| **Gap Filter** | SPY gaps down ≥1.5% | Block intraday entries |
| **Vol Shock** | SPY 1-min range > 3×ATR | Pause entries 15 minutes |
| **Time Guard** | 13:55-14:10 daily | Block all entries |
| **Split Guard** | Corporate action detected | Freeze affected symbol |

---

## Reading Order

**For New Readers:**
1. Start with [Executive Summary](01-executive-summary.md)
2. Review [System Architecture](02-system-architecture.md) for the big picture
3. Read [Daily Operations](14-daily-operations.md) to understand the timeline
4. Then dive into specific engines as needed

**For Implementers:**
1. [QC Rules](../QC_RULES.md) - Critical QuantConnect patterns
2. [Data Infrastructure](03-data-infrastructure.md) - What data is needed
3. Follow sections 04-15 in order for implementation sequence

**For Reviewers:**
1. [Executive Summary](01-executive-summary.md) - Goals and constraints
2. [Risk Engine](12-risk-engine.md) - Safety mechanisms
3. [Parameters Reference](16-appendix-parameters.md) - Tunable values

---

## Diagrams

Each section contains embedded Mermaid diagrams. GitHub renders these automatically.

**Key Diagrams by Section:**

| Section | Diagrams Included |
|---------|-------------------|
| 02 - System Architecture | Master Architecture, Authority Hierarchy |
| 03 - Data Infrastructure | Data Flow |
| 04 - Regime Engine | Regime Calculation Detail |
| 05 - Capital Engine | Phase Transitions, Lockbox Logic |
| 06 - Cold Start Engine | Warm Entry Flow |
| 07 - Trend Engine | Entry/Exit Logic, Chandelier Stops |
| 08 - Mean Reversion Engine | Scan and Exit Flow |
| 09 - Hedge Engine | Regime-Based Allocation |
| 11 - Portfolio Router | Six-Step Workflow, Exposure Groups |
| 12 - Risk Engine | All Safeguards |
| 13 - Execution Engine | Order State Machine |
| 14 - Daily Operations | Daily Timeline, System State Machine, Master Timeline Flow |
| 15 - State Persistence | Persisted Variables |

---

## Quick Reference

### Key Thresholds

| Parameter | Value | Used By |
|-----------|:-----:|---------|
| Kill Switch Loss | 3% | Risk Engine |
| Panic Mode (SPY Drop) | 4% | Risk Engine |
| Weekly Breaker | 5% | Risk Engine |
| Gap Filter | 1.5% | Risk Engine |
| Vol Shock (ATR Multiple) | 3x | Risk Engine |
| Cold Start Days | 5 | Cold Start Engine |
| Regime Smoothing Alpha | 0.3 | Regime Engine |
| BB Compression Threshold | 0.10 | Trend Engine |
| RSI Oversold | 25 | Mean Reversion Engine |
| MR Drop from Open | 2.5% | Mean Reversion Engine |
| MR Target | +2% | Mean Reversion Engine |
| MR Stop | -2% | Mean Reversion Engine |

### Exposure Group Limits

| Group | Symbols | Max Net | Max Gross |
|-------|---------|:-------:|:---------:|
| NASDAQ_BETA | TQQQ, QLD, SOXL, PSQ | 50% | 75% |
| SPY_BETA | SSO | 40% | 40% |
| RATES | TMF, SHV | 40% | 40% |

### Position Limits by Phase

| Phase | Equity Range | Max Single Position |
|-------|:------------:|:-------------------:|
| SEED | $50k - $100k | 50% |
| GROWTH | $100k - $500k | 40% |

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-01-25 | Phase 6 complete: main.py implemented (1,332 lines) - Ready for backtesting |
| 0.6.0 | 2026-01-25 | Phase 6 complete: main.py entry point wires all components |
| 0.5.0 | 2026-01-25 | Phase 5 complete: ExecutionEngine, StateManager, DailyScheduler |
| 0.4.0 | 2026-01-25 | Phase 4 complete: ExposureGroups, PortfolioRouter, RiskEngine |
| 0.3.0 | 2026-01-25 | Phase 3 complete: All strategy engines |
| 0.2.0 | 2026-01-25 | Phase 2 complete: Core engines |
| 0.1.0 | 2026-01-25 | Phase 1 complete: Foundation |

---

## Contributing

When updating documentation:

1. Maintain the section structure
2. Keep diagrams inline with their explanations
3. Update the Table of Contents if adding sections
4. Update Parameters Reference if changing values
5. Add entries to Glossary for new terms

---

## Related Files

| File | Location | Purpose |
|------|----------|---------|
| Main Algorithm | `../main.py` | QCAlgorithm entry point (1,332 lines - Complete) |
| Configuration | `../config.py` | All tunable parameters |
| Developer Guide | `../developer-guide-claude.md` | Build workflow and session init |
| Test Cases | `../tests/scenarios/` | Scenario test files |

---

## Navigation

➡️ **Start Reading:** [Table of Contents](00-table-of-contents.md)

➡️ **Jump to Architecture:** [System Architecture](02-system-architecture.md)

➡️ **Implementation Guide:** [QC Rules](../QC_RULES.md)

➡️ **Error Reference:** [Common Errors](../ERRORS.md)