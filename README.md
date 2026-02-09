# Alpha NextGen V2

**Multi-Strategy Algorithmic Trading System for Leveraged ETFs**

[![CI](https://github.com/vickdavinci/alpha-nextgen-v2-private/actions/workflows/test.yml/badge.svg?branch=develop)](https://github.com/vickdavinci/alpha-nextgen-v2-private/actions/workflows/test.yml)
[![Platform](https://img.shields.io/badge/Platform-QuantConnect-blue)]()
[![Broker](https://img.shields.io/badge/Broker-Interactive%20Brokers-red)]()
[![Status](https://img.shields.io/badge/Status-V6.12-brightgreen)]()

---

## Overview

Alpha NextGen V2 is a systematic trading system that combines **regime detection**, **multiple trading strategies**, and **comprehensive risk management** to trade leveraged ETFs and QQQ options on the QuantConnect (LEAN) platform with Interactive Brokers.

**V6.12 Core-Satellite Architecture:**
- **Core (40%)**: Trend Engine - MA200 + ADX confirmation across QLD, SSO, UGL, UCO (all 2× leverage)
- **Satellite (25%)**: Options Engine - Dual-Mode (Swing 18.75% + Intraday 6.25%) with VASS credit/debit spreads
- **Satellite (10%)**: Mean Reversion Engine - RSI + VIX filter for TQQQ/SPXL/SOXL
- **Overlay**: Hedge Engine (SH) - regime-driven defensive allocation

**V6.x "All-Weather" Features:**
- Startup Gate: 15-day phased capital deployment allowing hedges + bearish options from day 1
- V5.3 Regime Model: 4-factor scoring (Momentum, VIX Combined, Trend, Drawdown) with guards
- VIX Guards: High-VIX Clamp (47 cap @ VIX≥25), Spike Cap (38 cap @ +28% 5-day), Breadth Decay
- Drawdown Governor: cumulative drawdown scaling with dynamic recovery
- Graduated Kill Switch: 3-tier response (reduce / trend-exit / full-exit)

> Forked from V1 v1.0.0 on 2026-01-26.

The system adapts its behavior based on market conditions:
- **Bull markets (regime 70+)**: Full leverage via trend-following + bullish options spreads
- **Neutral markets (50-69)**: Selective entries, no options (dead zone)
- **Cautious/Defensive (30-49)**: Hedges active, bearish PUT spreads, trend entries gated
- **Bear markets (0-29)**: Maximum hedges (SH 30%), longs blocked, PUT spreads active

---

## Investment Thesis

| Belief | Implementation |
|--------|----------------|
| Markets have regimes | 4-factor Regime Engine (V5.3): Momentum 30%, VIX Combined 30%, Trend 25%, Drawdown 15% |
| Leveraged ETFs reward precision | Strategy-specific leverage: 2x for swing (QLD/SSO/UGL/UCO), 3x for intraday (TQQQ/SPXL/SOXL) |
| Mean reversion works intraday | RSI-based oversold detection for TQQQ/SPXL/SOXL with VIX regime filter |
| Trends persist when confirmed | MA200 + ADX confirmation with SMA50 structural exit |
| Options capture regime extremes | Bullish CALL spreads in risk-on, bearish PUT spreads in risk-off |
| Capital preservation first | Drawdown Governor + Graduated Kill Switch + Startup Gate layered defense |

---

## Architecture

```
                              DATA LAYER
            ┌─────────────────────────────────────────────────────┐
            │  Proxy (Daily)            Traded (Minute)           │
            │  SPY, RSP, VIX           QLD, SSO, UGL, UCO,       │
            │                           TQQQ, SPXL, SOXL, SH,    │
            │                           QQQ Options               │
            └─────────────────────────────────────────────────────┘
                                    │
                                    ▼
                              CORE ENGINES
            ┌─────────────────────────────────────────────────────┐
            │ Regime Engine  │ Capital Engine │ Risk Engine       │
            │ (4-factor V5.3 │ (Phase/Lockbox │ (Governor, KS,    │
            │  0-100 score)  │  Partitions)   │  Circuit Breakers)│
            ├────────────────┴────────────────┴───────────────────┤
            │ Startup Gate (V6.0)        │ Cold Start Engine      │
            │ (15-day phased deployment) │ (5-day KS recovery)    │
            └─────────────────────────────────────────────────────┘
                                    │
                                    ▼
                           STRATEGY ENGINES
            ┌─────────────────────────────────────────────────────┐
            │ Trend     │ Options     │ Mean Rev  │ Hedge         │
            │ (QLD/SSO  │ (QQQ Swing  │ (TQQQ/    │ (SH          │
            │  UGL/UCO) │  + Intraday)│ SPXL/SOXL)│  Defensive)   │
            │ 40% Core  │ 25% Satell. │ 10% Sat.  │ Overlay       │
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
| **Trend** | QLD, SSO, UGL, UCO | 40% | Swing (days-weeks) | MA200 + ADX >= 15, SMA50 structural exit |
| **Options (Swing)** | QQQ Options | 18.75% | Swing (14-45 DTE) | VASS regime scoring, debit/credit spreads |
| **Options (Intraday)** | QQQ Options | 6.25% | Intraday (1-5 DTE) | Micro Regime (VIX Level × VIX Direction) |
| **Mean Reversion** | TQQQ, SPXL, SOXL | 10% | Intraday (minutes-hours) | RSI(5) < 25, VIX filter, 2.5% drop |
| **Hedge** | SH | Overlay | Defensive | Regime < 40 (3 tiers) |
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
| UGL | 2x | Trend | Yes |
| UCO | 2x | Trend | Yes |
| SH | 1x | Hedge | Yes |
| TQQQ | 3x | Mean Rev | **NO** (close by 15:45) |
| SPXL | 3x | Mean Rev | **NO** (close by 15:45) |
| SOXL | 3x | Mean Rev | **NO** (close by 15:45) |

---

## Project Structure

```
alpha-nextgen-v2-private/
├── main.py                          # QCAlgorithm entry point (~3,800 lines)
├── config.py                        # ALL tunable parameters
├── requirements.txt                 # Python dependencies
├── requirements.lock                # Locked versions for reproducibility
├── pyproject.toml                   # Unified tool config (pytest, black, mypy)
├── Makefile                         # Workflow automation
├── .python-version                  # Python 3.11
│
├── engines/
│   ├── core/
│   │   ├── regime_engine.py         # 4-factor market state scoring V5.3 (0-100)
│   │   ├── capital_engine.py        # Phase management, lockbox, partitions
│   │   ├── risk_engine.py           # Governor, graduated KS, circuit breakers
│   │   ├── cold_start_engine.py     # 5-day warm entry after kill switch
│   │   ├── startup_gate.py          # V6.0: 15-day phased capital deployment
│   │   └── trend_engine.py          # MA200 + ADX trend signals (40%)
│   └── satellite/
│       ├── options_engine.py        # QQQ options dual-mode (25%)
│       ├── mean_reversion_engine.py # Intraday RSI bounce (10%)
│       ├── hedge_engine.py          # SH defensive overlay
│       └── yield_sleeve.py          # SHV cash management (spec only)
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
| [04 - Regime Engine](docs/system/04-regime-engine.md) | 4-factor scoring system (V5.3) |
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
| 40-49 | CAUTIOUS | Full | PUT spreads | 10% SH |
| 30-39 | DEFENSIVE | Blocked | PUT spreads | 20% SH |
| 0-29 | RISK_OFF | Blocked | PUT spreads | 30% SH |

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
| COMMODITY | 20% | 20% |
| RATES | 99% | 99% |

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

All parameters are centralized in `config.py`. Never hardcode values.

```python
# Kill Switch (V6.x)
KILL_SWITCH_PCT = 0.05             # -5% daily → full liquidation

# Drawdown Governor
DRAWDOWN_GOVERNOR_RECOVERY_BASE = 0.08  # Dynamic recovery threshold

# Startup Gate (V6.0)
STARTUP_GATE_WARMUP_DAYS = 5       # Indicators warmup (hedges only)
STARTUP_GATE_OBSERVATION_DAYS = 5  # Add bearish options
STARTUP_GATE_REDUCED_DAYS = 5      # All engines at 50%

# V5.3 Regime Guards
VIX_HIGH_CLAMP_SCORE = 47          # Cap score when VIX >= 25
SPIKE_CAP_SCORE = 38               # Cap when VIX 5-day change >= +28%
```

---

## Critical Rules

1. **Strategy engines are ANALYZERS ONLY** -- They emit `TargetWeight`, never call order methods
2. **Only Portfolio Router places orders** -- `MarketOrder()`, `MarketOnOpenOrder()`, `Liquidate()`
3. **Risk checks run BEFORE strategy logic** -- Every minute, every time
4. **TQQQ, SPXL, and SOXL must close by 15:45** -- They are intraday only, never overnight
5. **Never hardcode values** -- All parameters come from `config.py`
6. **Startup Gate controls HOW MUCH, Regime controls WHAT** -- Independent concerns

---

## Version History

| Version | Date | Description |
|---------|------|-------------|
| V6.12 | 2026-02-09 | Dir=NONE reduction + VASS DTE fallback + cross-blocking fix |
| V6.11 | 2026-02-08 | Universe redesign: QLD/SSO/UGL/UCO (2×), SH hedge, removed TNA/FAS/TMF/PSQ |
| V6.10 | 2026-02-07 | Options Engine V6.10: Spread width $5, force close DTE=1, 40%/40% R:R |
| V6.8 | 2026-02-06 | Credit spreads DTE 5-28, ATR stop max 30%, intraday VIX adjustments |
| V6.4 | 2026-02-05 | Engine Isolation Mode for targeted backtesting |
| V6.0 | 2026-02-05 | Simplified Startup Gate, V5.3 Regime Model integration |
| V5.3 | 2026-02-05 | 4-factor regime model: Momentum, VIX Combined, Trend, Drawdown with guards |
| V2.30 | 2026-02-04 | All-Weather StartupGate, direction-aware options gating, bearish path fix |
| V2.29 | 2026-02-04 | Ghost Spread Flush, Dynamic Governor Recovery |
| V2.28 | 2026-02-04 | 5 bug fixes from Q1 2022 backtest, tightened governor |
| V2.27 | 2026-02-03 | Graduated Kill Switch (3-tier), Win Rate Gate |
| V2.26 | 2026-02-03 | Drawdown Governor, Chop Detection regime factor |
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
