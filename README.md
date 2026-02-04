# Alpha NextGen V2

**Multi-Strategy Algorithmic Trading System for Leveraged ETFs**

[![CI](https://github.com/vickdavinci/alpha-nextgen-v2-private/actions/workflows/test.yml/badge.svg?branch=develop)](https://github.com/vickdavinci/alpha-nextgen-v2-private/actions/workflows/test.yml)
[![Platform](https://img.shields.io/badge/Platform-QuantConnect-blue)]()
[![Broker](https://img.shields.io/badge/Broker-Interactive%20Brokers-red)]()
[![Status](https://img.shields.io/badge/Status-V2.30-brightgreen)]()

---

## Overview

Alpha NextGen V2 is a systematic trading system that combines **regime detection**, **multiple trading strategies**, and **comprehensive risk management** to trade leveraged ETFs and QQQ options on the QuantConnect (LEAN) platform with Interactive Brokers.

**V2.30 Core-Satellite Architecture:**
- **Core (40%)**: Trend Engine - MA200 + ADX confirmation across QLD, SSO, TNA, FAS
- **Satellite (50%)**: Options Engine - Dual-Mode (Swing + Intraday) with VASS credit/debit spreads
- **Satellite (10%)**: Mean Reversion Engine - RSI + VIX filter for TQQQ/SOXL
- **Overlay**: Hedge Engine (TMF/PSQ) - regime-driven defensive allocation

**V2.30 "All-Weather" Features:**
- Startup Gate: 15-day phased capital deployment allowing hedges + bearish options from day 1
- Direction-aware options gating: bearish PUT spreads active in bear markets
- Drawdown Governor: cumulative drawdown scaling with dynamic recovery
- Graduated Kill Switch: 3-tier response (reduce / trend-exit / full-exit)
- Chop Detection: ADX-based regime sub-factor prevents trading in range-bound markets

> Forked from V1 v1.0.0 on 2026-01-26.

The system adapts its behavior based on market conditions:
- **Bull markets (regime 70+)**: Full leverage via trend-following + bullish options spreads
- **Neutral markets (50-69)**: Selective entries, no options (dead zone)
- **Cautious/Defensive (30-49)**: Hedges active, bearish PUT spreads, trend entries gated
- **Bear markets (0-29)**: Maximum hedges (TMF 20% + PSQ 10%), longs blocked, PUT spreads active

---

## Investment Thesis

| Belief | Implementation |
|--------|----------------|
| Markets have regimes | 6-factor Regime Engine scores market state 0-100 (trend, VIX, vol, breadth, credit, chop) |
| Leveraged ETFs reward precision | Strategy-specific leverage: 2x for swing (QLD/SSO), 3x for intraday (TQQQ/SOXL) |
| Mean reversion works intraday | RSI-based oversold detection for TQQQ/SOXL with VIX regime filter |
| Trends persist when confirmed | MA200 + ADX confirmation with SMA50 structural exit |
| Options capture regime extremes | Bullish CALL spreads in risk-on, bearish PUT spreads in risk-off |
| Capital preservation first | Drawdown Governor + Graduated Kill Switch + Startup Gate layered defense |

---

## Architecture

```
                              DATA LAYER
            ┌─────────────────────────────────────────────────────┐
            │  Proxy (Daily)            Traded (Minute)           │
            │  SPY, RSP, HYG, IEF      QLD, SSO, TNA, FAS,      │
            │  VIX                      TQQQ, SOXL, TMF, PSQ,    │
            │                           QQQ Options               │
            └─────────────────────────────────────────────────────┘
                                    │
                                    ▼
                              CORE ENGINES
            ┌─────────────────────────────────────────────────────┐
            │ Regime Engine  │ Capital Engine │ Risk Engine       │
            │ (6-factor,     │ (Phase/Lockbox │ (Governor, KS,    │
            │  0-100 score)  │  Partitions)   │  Circuit Breakers)│
            ├────────────────┴────────────────┴───────────────────┤
            │ Startup Gate (V2.30)       │ Cold Start Engine      │
            │ (15-day phased deployment) │ (5-day KS recovery)    │
            └─────────────────────────────────────────────────────┘
                                    │
                                    ▼
                           STRATEGY ENGINES
            ┌─────────────────────────────────────────────────────┐
            │ Trend     │ Options     │ Mean Rev  │ Hedge         │
            │ (QLD/SSO  │ (QQQ Swing  │ (TQQQ/    │ (TMF/PSQ     │
            │  TNA/FAS) │  + Intraday)│  SOXL)    │  Defensive)   │
            │ 40% Core  │ 50% Satell. │ 10% Sat.  │ Overlay       │
            └─────────────────────────────────────────────────────┘
                                    │
                            TargetWeight Objects
                                    ▼
            ┌─────────────────────────────────────────────────────┐
            │              PORTFOLIO ROUTER                       │
            │  Collect → Aggregate → Validate → Net → Execute     │
            │  (Exposure limits, margin checks, capital firewall) │
            └─────────────────────────────────────────────────────┘
                                    │
                                    ▼
            ┌─────────────────────────────────────────────────────┐
            │  EXECUTION ENGINE    │    OCO MANAGER               │
            │  (Market/MOO/Limit)  │    (Options profit/stop)     │
            └─────────────────────────────────────────────────────┘
                                    │
                                    ▼
                              ┌───────────┐
                              │   IBKR    │
                              │  Broker   │
                              └───────────┘
```

**Key Design Principles:**
- Strategy engines emit `TargetWeight` intentions. Only the Portfolio Router places orders.
- The Startup Gate controls **how much** capital to deploy. The Regime Engine controls **what** to deploy it in.
- Capital is partitioned: 50% Trend / 50% Options to prevent engine starvation.

---

## Strategies

| Strategy | Instruments | Allocation | Style | Entry Signal |
|----------|-------------|:----------:|-------|--------------|
| **Trend** | QLD, SSO, TNA, FAS | 40% | Swing (days-weeks) | MA200 + ADX >= 15, SMA50 structural exit |
| **Options (Swing)** | QQQ Options | 37.5% | Swing (5-45 DTE) | VASS regime scoring, debit/credit spreads |
| **Options (Intraday)** | QQQ Options | 12.5% | Intraday (0-2 DTE) | Micro Regime (VIX Level x VIX Direction) |
| **Mean Reversion** | TQQQ, SOXL | 10% | Intraday (minutes-hours) | RSI(5) < 25, VIX filter, 2.5% drop |
| **Hedge** | TMF, PSQ | Overlay | Defensive | Regime < 40 (3 tiers) |
| **Cold Start** | QLD, SSO | N/A | Safe deploy (5 days) | Kill switch recovery, 50% size |

---

## Startup Gate (V2.30)

The Startup Gate is a one-time 15-day arming sequence that ramps capital deployment while allowing defensive engines from day 1. Once fully armed, it stays armed permanently (kill switch does NOT reset it).

| Phase | Days | What's Allowed | Size Multiplier |
|-------|:----:|----------------|:---------------:|
| **INDICATOR_WARMUP** | 1-5 | Hedges (TMF/PSQ) only | 0% |
| **OBSERVATION** | 6-10 | + Bearish options (PUT spreads) | 50% |
| **REDUCED** | 11-15 | + All engines (trend, MR, bullish options) | 50% |
| **FULLY_ARMED** | 16+ | No restrictions (permanent) | 100% |

---

## Risk Management

### Layered Defense System

```
Layer 1: Startup Gate ──────── Capital deployment ramp (15 days)
Layer 2: Drawdown Governor ─── Cumulative drawdown scaling (3%/6%/10%/15%)
Layer 3: Graduated Kill Switch  Per-day loss tiers (2%/4%/6%)
Layer 4: Circuit Breakers ───── 5-level graduated response
Layer 5: Panic Mode ─────────── SPY crash detection (-4%)
Layer 6: Time/Vol Guards ────── Intraday safety nets
```

### Graduated Kill Switch (V2.27)

| Tier | Trigger | Action |
|:----:|---------|--------|
| 1 | -2% daily loss | Halve trend allocation, block new options |
| 2 | -4% daily loss | Liquidate trend positions, keep spreads |
| 3 | -6% daily loss | Liquidate everything, reset cold start |

### Drawdown Governor (V2.26)

| Drawdown from Peak | Allocation Scale |
|:-------------------:|:----------------:|
| < 3% | 100% |
| 3% | 75% |
| 6% | 50% |
| 10% | 25% |
| 15% | 0% (cash + hedges only) |

Recovery is dynamic: `base (8%) x current_scale` -- lower allocations have lower recovery bars.

### Circuit Breakers

| Control | Trigger | Action |
|---------|---------|--------|
| **Weekly Breaker** | -5% WTD loss | 50% sizing reduction |
| **Gap Filter** | SPY -1.5% gap | Block MR/warm entries |
| **Vol Shock** | SPY bar > 3x ATR | 15-min entry pause |
| **Time Guard** | 13:55-14:10 ET | Block all entries |
| **Panic Mode** | SPY -4% intraday | Liquidate leveraged longs, keep hedges |

### Overnight Safety

| Symbol | Leverage | Engine | Overnight? |
|--------|:--------:|--------|:----------:|
| QLD | 2x | Trend | Yes |
| SSO | 2x | Trend | Yes |
| TNA | 3x | Trend | Yes |
| FAS | 3x | Trend | Yes |
| TMF | 3x | Hedge | Yes |
| PSQ | 1x | Hedge | Yes |
| TQQQ | 3x | Mean Rev | **NO** (close by 15:45) |
| SOXL | 3x | Mean Rev | **NO** (close by 15:45) |

---

## Project Structure

```
alpha-nextgen-v2-private/
├── main.py                          # QCAlgorithm entry point (5,422 lines)
├── config.py                        # ALL tunable parameters (1,172 lines)
├── requirements.txt                 # Python dependencies
├── requirements.lock                # Locked versions for reproducibility
├── pyproject.toml                   # Unified tool config (pytest, black, mypy)
├── Makefile                         # Workflow automation
├── .python-version                  # Python 3.11
│
├── engines/
│   ├── core/
│   │   ├── regime_engine.py         # 6-factor market state scoring (0-100)
│   │   ├── capital_engine.py        # Phase management, lockbox, partitions
│   │   ├── risk_engine.py           # Governor, graduated KS, circuit breakers
│   │   ├── cold_start_engine.py     # 5-day warm entry after kill switch
│   │   ├── startup_gate.py          # V2.30: 15-day phased capital deployment
│   │   └── trend_engine.py          # MA200 + ADX trend signals (40%)
│   └── satellite/
│       ├── options_engine.py        # QQQ options dual-mode (50%)
│       ├── mean_reversion_engine.py # Intraday RSI bounce (10%)
│       └── hedge_engine.py          # TMF/PSQ defensive overlay
│
├── portfolio/
│   ├── portfolio_router.py          # Central coordination, order authorization
│   ├── exposure_groups.py           # Exposure limit enforcement
│   └── position_manager.py          # Entry prices, stops, highest highs
│
├── execution/
│   ├── execution_engine.py          # Order submission (market, MOO, limit)
│   └── oco_manager.py               # One-Cancels-Other for options
│
├── persistence/
│   └── state_manager.py             # ObjectStore save/load
│
├── data/                            # Symbols, indicators, validation
├── models/                          # Data classes and enums
├── scheduling/                      # Timed events
├── utils/                           # Helper functions
├── scripts/                         # Backtest runner, config validator
│
├── tests/                           # 1,345+ tests
│   ├── test_*.py                    # Unit tests per engine
│   ├── scenarios/                   # End-to-end workflow tests
│   └── integration/                 # Cross-engine integration tests
│
├── docs/
│   ├── system/                      # Core specification (20 sections)
│   ├── audits/                      # Backtest results and code audits
│   └── internal/                    # Documentation map
│
├── CLAUDE.md                        # AI assistant instructions
├── WORKBOARD.md                     # Task tracking & ownership
├── QUICKSTART.md                    # Fast onboarding guide
├── CONTRIBUTING.md                  # Git workflow, branch naming
├── QC_RULES.md                      # QuantConnect coding patterns
└── ERRORS.md                        # Common errors and solutions
```

See [PROJECT-STRUCTURE.md](PROJECT-STRUCTURE.md) for detailed file listing with Mermaid diagrams.

---

## Documentation

### Core Reference

| Document | Purpose |
|----------|---------|
| [CLAUDE.md](CLAUDE.md) | Component map, critical rules, coding conventions |
| [QUICKSTART.md](QUICKSTART.md) | Clone to running tests setup guide |
| [QC_RULES.md](QC_RULES.md) | QuantConnect/LEAN-specific patterns |
| [ERRORS.md](ERRORS.md) | Common errors and solutions |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Git workflow, branch naming, commit format |

### System Specification (docs/system/)

| Section | Content |
|---------|---------|
| [00 - Table of Contents](docs/system/00-table-of-contents.md) | Navigation index |
| [01 - Executive Summary](docs/system/01-executive-summary.md) | Project overview and goals |
| [02 - System Architecture](docs/system/02-system-architecture.md) | Component layers and data flow |
| [03 - Data Infrastructure](docs/system/03-data-infrastructure.md) | Symbols, indicators, warmup |
| [04 - Regime Engine](docs/system/04-regime-engine.md) | 6-factor scoring system |
| [05 - Capital Engine](docs/system/05-capital-engine.md) | Phases, lockbox, partitions |
| [06 - Cold Start Engine](docs/system/06-cold-start-engine.md) | Days 1-5 warm entry + Startup Gate |
| [07 - Trend Engine](docs/system/07-trend-engine.md) | MA200 + ADX + SMA50 exit |
| [08 - Mean Reversion Engine](docs/system/08-mean-reversion-engine.md) | Intraday RSI bounce logic |
| [09 - Hedge Engine](docs/system/09-hedge-engine.md) | TMF/PSQ regime-driven allocation |
| [10 - Yield Sleeve](docs/system/10-yield-sleeve.md) | SHV cash management (spec only) |
| [11 - Portfolio Router](docs/system/11-portfolio-router.md) | Central coordination + capital firewall |
| [12 - Risk Engine](docs/system/12-risk-engine.md) | Governor, graduated KS, circuit breakers |
| [13 - Execution Engine](docs/system/13-execution-engine.md) | Order submission |
| [14 - Daily Operations](docs/system/14-daily-operations.md) | Timeline and events |
| [15 - State Persistence](docs/system/15-state-persistence.md) | ObjectStore patterns |
| [16 - Parameters](docs/system/16-appendix-parameters.md) | All tunable values |
| [17 - Glossary](docs/system/17-appendix-glossary.md) | Terms and formulas |
| [18 - Options Engine](docs/system/18-options-engine.md) | Dual-mode + VASS + Micro Regime |
| [19 - OCO Manager](docs/system/19-oco-manager.md) | One-Cancels-Other order pairs |
| [ENGINE_LOGIC_REFERENCE](docs/system/ENGINE_LOGIC_REFERENCE.md) | Engine gating and flow quick reference |

---

## Quick Reference

### Regime States

| Score | State | Trend Longs | Options | Hedges |
|:-----:|-------|:-----------:|:-------:|--------|
| 70-100 | RISK_ON | Full | CALL spreads | None |
| 50-69 | NEUTRAL | Full | No trade (dead zone) | None |
| 40-49 | CAUTIOUS | Full | PUT spreads | 10% TMF |
| 30-39 | DEFENSIVE | Blocked | PUT spreads | 15% TMF + 5% PSQ |
| 0-29 | RISK_OFF | Blocked | PUT spreads | 20% TMF + 10% PSQ |

### Daily Timeline (Eastern Time)

| Time | Event |
|------|-------|
| 09:25 | Set equity_prior_close baseline |
| 09:30 | Market open, MOO orders fill |
| 09:31 | MOO fallback check |
| 09:33 | Set equity_sod, check gap filter |
| 10:00 | Warm entry check, MR window opens |
| 13:55 | Time guard starts |
| 14:10 | Time guard ends |
| 15:00 | MR entry window closes |
| 15:45 | **TQQQ/SOXL force close**, EOD processing, MOO submission |
| 16:00 | Market close, save state |

### Exposure Limits

| Group | Max Net Long | Max Gross |
|-------|:------------:|:---------:|
| NASDAQ_BETA | 50% | 75% |
| SPY_BETA | 40% | 40% |
| SMALL_CAP_BETA | 25% | 25% |
| FINANCIALS_BETA | 15% | 15% |
| RATES | 40% | 40% |

---

## Getting Started

> **New Developer?** Start with [QUICKSTART.md](QUICKSTART.md) for setup instructions.

### Prerequisites

- Python 3.11 (required -- system default may differ)
- QuantConnect Trading Firm plan (for options data)
- Interactive Brokers account (for live trading)
- Lean CLI (for cloud backtesting)

### Quick Setup

```bash
git clone https://github.com/vickdavinci/alpha-nextgen-v2-private.git
cd alpha-nextgen-v2-private
make setup  # Creates venv, installs deps, configures pre-commit hooks
```

### Running Tests

```bash
make test                                    # All tests
pytest tests/test_regime_engine.py -v        # Single file
pytest -k "kill_switch" -v                   # Pattern match
pytest tests/scenarios/ -v                   # Scenario tests
```

### Running Backtests

```bash
# Recommended: wait for completion and view results
./scripts/qc_backtest.sh "V2.30-MyFeature" --open

# Auto-name from git branch
./scripts/qc_backtest.sh --open
```

The script syncs all project files to the lean workspace, pushes to QuantConnect cloud, and runs the backtest.

---

## Key Configuration

All parameters are centralized in `config.py` (1,172 lines). Never hardcode values.

```python
# Capital Partitions (V2.18)
CAPITAL_PARTITION_TREND = 0.50      # 50% reserved for Trend Engine
CAPITAL_PARTITION_OPTIONS = 0.50    # 50% reserved for Options Engine

# Graduated Kill Switch (V2.27)
KS_TIER_1_PCT = 0.02               # -2% daily → reduce trend
KS_TIER_2_PCT = 0.04               # -4% daily → exit trend
KS_TIER_3_PCT = 0.06               # -6% daily → exit everything

# Drawdown Governor (V2.26)
DRAWDOWN_GOVERNOR_RECOVERY_BASE = 0.08  # Dynamic recovery threshold

# Startup Gate (V2.30)
STARTUP_GATE_WARMUP_DAYS = 5       # Indicators warmup (hedges only)
STARTUP_GATE_OBSERVATION_DAYS = 5  # Add bearish options
STARTUP_GATE_REDUCED_DAYS = 5      # All engines at 50%
```

---

## Critical Rules

1. **Strategy engines are ANALYZERS ONLY** -- They emit `TargetWeight`, never call order methods
2. **Only Portfolio Router places orders** -- `MarketOrder()`, `MarketOnOpenOrder()`, `Liquidate()`
3. **Risk checks run BEFORE strategy logic** -- Every minute, every time
4. **TQQQ and SOXL must close by 15:45** -- They are intraday only, never overnight
5. **Never hardcode values** -- All parameters come from `config.py`
6. **Startup Gate controls HOW MUCH, Regime controls WHAT** -- Independent concerns

---

## Version History

| Version | Date | Description |
|---------|------|-------------|
| V2.30 | 2026-02-04 | All-Weather StartupGate, direction-aware options gating, bearish path fix |
| V2.29 | 2026-02-04 | Ghost Spread Flush, Dynamic Governor Recovery |
| V2.28 | 2026-02-04 | 5 bug fixes from Q1 2022 backtest, tightened governor |
| V2.27 | 2026-02-03 | Graduated Kill Switch (3-tier), Win Rate Gate |
| V2.26 | 2026-02-03 | Drawdown Governor, Chop Detection regime factor |
| V2.25 | 2026-02-03 | 5 critical bug fixes from 2015 full-year audit |
| V2.24 | 2026-02-03 | Zero-trade diagnostic fixes, DTE double-filter fix |
| V2.23 | 2026-02-03 | VASS credit spread integration, strike/DTE fix |
| V2.22 | 2026-02-03 | Neutrality exit -- close flat spreads in regime dead zone |
| V2.21 | 2026-02-03 | Rejection-aware spread sizing with margin estimation |
| V2.20 | 2026-02-03 | Event-driven state recovery for broker rejections |
| V2.19 | 2026-02-02 | Emergency execution patch -- limit orders + VIX filter |
| V2.18 | 2026-02-02 | Capital firewall, leverage cap, ghost margin fix |
| V2.1.1 | 2026-01-28 | Options Engine redesign (Dual-Mode + Micro Regime) |
| V2.0 | 2026-01-27 | Core-Satellite architecture, all engines integrated |
| V1.0 | 2026-01-25 | Original fork from V1 |

---

## Target Performance

| Metric | Target |
|--------|:------:|
| Annual Return | 25-40% |
| Maximum Drawdown | < 20% |
| Sharpe Ratio | > 1.0 |
| Win Rate | > 55% |

---

## License

Private - Not for distribution

---

## Contact

For questions about this system, refer to the documentation in `docs/system/` or `CLAUDE.md`.
