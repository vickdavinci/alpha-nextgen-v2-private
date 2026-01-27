# Alpha NextGen V2

**Multi-Strategy Algorithmic Trading System for Leveraged ETFs**

[![CI](https://github.com/vickdavinci/alpha-nextgen/actions/workflows/test.yml/badge.svg?branch=develop)](https://github.com/vickdavinci/alpha-nextgen/actions/workflows/test.yml)
[![Platform](https://img.shields.io/badge/Platform-QuantConnect-blue)]()
[![Broker](https://img.shields.io/badge/Broker-Interactive%20Brokers-red)]()
[![Status](https://img.shields.io/badge/Status-v2.0.0--dev-brightblue-brightgreen)](https://github.com/vickdavinci/alpha-nextgen/releases/tag/v1.0.0)

---

## Overview

Alpha NextGen V2 is a systematic trading system that combines **regime detection**, **multiple trading strategies**, and **comprehensive risk management** to trade leveraged ETFs on the QuantConnect (LEAN) platform with Interactive Brokers.

The system adapts its behavior based on market conditions:
- **Favorable markets**: Deploy leverage through trend-following and mean reversion
- **Deteriorating markets**: Reduce exposure and activate hedges
- **Dangerous markets**: Maximum defense with full hedge allocation

---

## Investment Thesis

| Belief | Implementation |
|--------|----------------|
| Markets have regimes | 4-factor Regime Engine scores market state 0-100 |
| Leveraged ETFs reward precision | Strategy-specific leverage: 2x for swing, 3x for intraday |
| Mean reversion works intraday | RSI-based oversold detection for TQQQ/SOXL |
| Trends persist | Bollinger Band compression breakouts for QLD/SSO |
| Risk management is paramount | 6-layer authority hierarchy with circuit breakers |

---

## Architecture

```
                              DATA LAYER
            ┌─────────────────────────────────────────────┐
            │  Proxy (Daily)          Traded (Minute)     │
            │  SPY, RSP, HYG, IEF     TQQQ, SOXL, QLD,   │
            │                          SSO, TMF, PSQ, SHV │
            └─────────────────────────────────────────────┘
                                  │
                                  ▼
                            CORE ENGINES
            ┌─────────────────────────────────────────────┐
            │  Regime Engine    Capital Engine    Risk    │
            │  (4-factor score) (Phase/Lockbox)   Engine  │
            └─────────────────────────────────────────────┘
                                  │
                                  ▼
                          STRATEGY ENGINES
            ┌─────────────────────────────────────────────┐
            │ Trend │ Mean Rev │ Hedge │ Yield │ Cold    │
            │ (QLD/ │ (TQQQ/   │ (TMF/ │ (SHV) │ Start   │
            │  SSO) │  SOXL)   │  PSQ) │       │ (Day1-5)│
            └─────────────────────────────────────────────┘
                                  │
                          TargetWeight Objects
                                  ▼
            ┌─────────────────────────────────────────────┐
            │            PORTFOLIO ROUTER                 │
            │  (Aggregate → Validate → Net → Execute)     │
            └─────────────────────────────────────────────┘
                                  │
                                  ▼
            ┌─────────────────────────────────────────────┐
            │           EXECUTION ENGINE → IBKR           │
            └─────────────────────────────────────────────┘
```

**Key Design Principle**: Strategy engines emit `TargetWeight` intentions. Only the Portfolio Router is authorized to place orders.

---

## Strategies

| Strategy | Instruments | Style | Holding Period | Entry Signal |
|----------|-------------|-------|----------------|--------------|
| **Trend** | QLD, SSO | Swing | Days to weeks | BB compression breakout |
| **Mean Reversion** | TQQQ, SOXL | Intraday | Minutes to hours | RSI < 25, -2.5% drop |
| **Hedge** | TMF, PSQ | Defensive | As needed | Regime < 40 |
| **Yield** | SHV | Cash mgmt | Ongoing | Idle cash > $2k |
| **Cold Start** | QLD, SSO | Safe deploy | Days 1-5 | Regime > 50, 50% size |

---

## Risk Management

### Circuit Breakers

| Control | Trigger | Action |
|---------|---------|--------|
| **Kill Switch** | -3% daily loss | Liquidate ALL, reset to cold start |
| **Panic Mode** | SPY -4% intraday | Liquidate leveraged longs, keep hedges |
| **Weekly Breaker** | -5% WTD loss | 50% sizing reduction |
| **Gap Filter** | SPY -1.5% gap | Block MR/warm entries |
| **Vol Shock** | SPY bar > 3x ATR | 15-min entry pause |
| **Time Guard** | 13:55-14:10 ET | Block all entries |

### Overnight Safety

| Symbol | Leverage | Overnight? | Reason |
|--------|:--------:|:----------:|--------|
| TQQQ | 3x | **NO** | Close by 15:45 |
| SOXL | 3x | **NO** | Close by 15:45 |
| QLD | 2x | Yes | Acceptable decay |
| SSO | 2x | Yes | Acceptable decay |
| TMF | 3x | Yes | Strategic hedge |
| PSQ | 1x | Yes | No leverage decay |
| SHV | 1x | Yes | Cash equivalent |

---

## Project Structure

```
alpha-nextgen/
├── main.py                     # QCAlgorithm entry point (1,332 lines - Complete)
├── config.py                   # ALL tunable parameters (single source of truth)
├── requirements.txt            # Python dependencies
├── .python-version             # Python 3.11
│
├── developer-guide-claude.md   # START HERE - Session init, build workflow
├── PROJECT-STRUCTURE.md        # Visual structure with Mermaid diagrams
├── CLAUDE.md                   # AI assistant instructions
├── QC_RULES.md                 # QuantConnect coding patterns
├── ERRORS.md                   # Common errors and solutions
│
├── .github/                    # GitHub configuration
│   ├── workflows/
│   │   └── test.yml            # CI/CD pipeline (pytest, linting, validation)
│   └── PULL_REQUEST_TEMPLATE.md # PR checklist for developers
├── .claude/                    # Claude Code config
│   └── config.json             # Project settings
├── scripts/                    # Utilities
│   ├── validate_config.py      # Spec compliance checker
│   └── check_spec_parity.py    # Code-to-spec update warning
├── archive/                    # Archived files
│   └── main_old.py.bak         # Original implementation backup
│
├── engines/                    # Strategy and core engines
├── portfolio/                  # Router, positions, exposure
├── execution/                  # Order management
├── data/                       # Symbols, indicators
├── models/                     # Data classes and enums
├── persistence/                # State save/load
├── scheduling/                 # Timed events
├── utils/                      # Helper functions
├── tests/                      # Unit and scenario tests
└── docs/                       # Full specification (17 sections)
```

See [PROJECT-STRUCTURE.md](PROJECT-STRUCTURE.md) for detailed file listing with Mermaid diagrams.

---

## Documentation

### Core Reference

| Document | Purpose |
|----------|---------|
| [developer-guide-claude.md](developer-guide-claude.md) | **START HERE** - Session init, build phases, workflows |
| [CLAUDE.md](CLAUDE.md) | Component map, critical rules, coding conventions |
| [QC_RULES.md](QC_RULES.md) | QuantConnect/LEAN-specific patterns |
| [ERRORS.md](ERRORS.md) | Common errors and solutions |

### Specification (docs/)

| Section | Content |
|---------|---------|
| [00 - Table of Contents](docs/00-table-of-contents.md) | Navigation index |
| [01 - Executive Summary](docs/01-executive-summary.md) | Project overview and goals |
| [02 - System Architecture](docs/02-system-architecture.md) | Component layers and data flow |
| [03 - Data Infrastructure](docs/03-data-infrastructure.md) | Symbols, indicators, warmup |
| [04 - Regime Engine](docs/04-regime-engine.md) | 4-factor scoring system |
| [05 - Capital Engine](docs/05-capital-engine.md) | Phases, lockbox, tradeable equity |
| [06 - Cold Start Engine](docs/06-cold-start-engine.md) | Days 1-5 warm entry |
| [07 - Trend Engine](docs/07-trend-engine.md) | BB breakout logic |
| [08 - Mean Reversion Engine](docs/08-mean-reversion-engine.md) | Intraday bounce logic |
| [09 - Hedge Engine](docs/09-hedge-engine.md) | TMF/PSQ allocation |
| [10 - Yield Sleeve](docs/10-yield-sleeve.md) | SHV cash management |
| [11 - Portfolio Router](docs/11-portfolio-router.md) | Central coordination |
| [12 - Risk Engine](docs/12-risk-engine.md) | Circuit breakers |
| [13 - Execution Engine](docs/13-execution-engine.md) | Order submission |
| [14 - Daily Operations](docs/14-daily-operations.md) | Timeline and events |
| [15 - State Persistence](docs/15-state-persistence.md) | ObjectStore patterns |
| [16 - Parameters](docs/16-appendix-parameters.md) | All tunable values |
| [17 - Glossary](docs/17-appendix-glossary.md) | Terms and formulas |

---

## Quick Reference

### Regime States

| Score | State | New Longs | Hedges |
|:-----:|-------|:---------:|--------|
| 70-100 | RISK_ON | Full | None |
| 50-69 | NEUTRAL | Full | None |
| 40-49 | CAUTIOUS | Full | 10% TMF |
| 30-39 | DEFENSIVE | Reduced | 15% TMF + 5% PSQ |
| 0-29 | RISK_OFF | Blocked | 20% TMF + 10% PSQ |

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
| 15:45 | **TQQQ/SOXL force close**, EOD processing |
| 16:00 | Market close, save state |

### Exposure Limits

| Group | Max Net Long | Max Gross |
|-------|:------------:|:---------:|
| NASDAQ_BETA | 50% | 75% |
| SPY_BETA | 40% | 40% |
| RATES | 40% | 40% |

---

## Getting Started

> **New Developer?** Start with [QUICKSTART.md](QUICKSTART.md) - clone to running tests in 5 minutes.

### Prerequisites

- Python 3.11+ (required)
- QuantConnect account (free tier works for development)
- Interactive Brokers account (for live trading)
- Lean CLI (optional, for local backtesting)

### Quick Setup

```bash
git clone https://github.com/vickdavinci/alpha-nextgen.git
cd alpha-nextgen
python3.11 -m venv venv && source venv/bin/activate
pip install -r requirements.lock
pip install -r requirements-dev.txt  # Optional: IDE autocomplete for QC API
make verify  # Confirms setup is working
```

**Full setup guide with troubleshooting:** [QUICKSTART.md](QUICKSTART.md)

### Development Workflow

1. **Read the docs**: Start with [CLAUDE.md](CLAUDE.md), then [QC_RULES.md](QC_RULES.md)
2. **Understand the spec**: Read docs/04 through docs/12 for engine logic
3. **Follow build order**:
   ```
   Phase 1: config.py → models/ → utils/
   Phase 2: regime_engine → capital_engine
   Phase 3: cold_start → trend → mean_reversion → hedge → yield
   Phase 4: portfolio_router → risk_engine
   Phase 5: execution_engine → state_manager → scheduler
   Phase 6: main.py (wire everything)
   ```
4. **Test incrementally**: Each component has unit tests in `tests/unit/`

### Running Backtests

**Option 1: QuantConnect Algorithm Lab**

```python
# In QuantConnect Algorithm Lab
from AlgorithmImports import *

class AlphaNextGen(QCAlgorithm):
    def Initialize(self):
        self.SetStartDate(2020, 1, 1)
        self.SetEndDate(2024, 1, 1)
        self.SetCash(50000)
        # ... initialization code
```

**Option 2: Lean CLI (Cloud)**

```bash
# Authenticate (one-time)
lean login

# Push code to QuantConnect cloud
cd ../lean-workspace
lean cloud push --project AlphaNextGen

# Run backtest
lean cloud backtest AlphaNextGen --name "My-Backtest"
```

### Lean CLI Workspace

A linked Lean workspace exists at `../lean-workspace/AlphaNextGen/` for cloud backtesting:

```
lean-workspace/
└── AlphaNextGen/
    └── main.py → symlink to alpha-nextgen/main.py
```

---

## Key Configuration

All parameters are centralized in `config.py`. Never hardcode values.

```python
# Example from config.py
KILL_SWITCH_PCT = 0.03          # 3% daily loss triggers full liquidation
PANIC_MODE_PCT = 0.04           # SPY -4% intraday triggers panic mode
BB_COMPRESSION_THRESHOLD = 0.10 # Bandwidth < 10% = compression
RSI_OVERSOLD = 25               # RSI(5) < 25 = oversold
SMOOTHING_ALPHA = 0.30          # Regime score EMA smoothing
```

---

## Critical Rules

1. **Strategy engines are ANALYZERS ONLY** - They emit `TargetWeight`, never call order methods
2. **Only Portfolio Router places orders** - `MarketOrder()`, `MarketOnOpenOrder()`, `Liquidate()`
3. **Check splits FIRST in OnData** - Proxy splits freeze everything, traded splits freeze that symbol
4. **TQQQ and SOXL must close by 15:45** - They are intraday only, never overnight
5. **Never hardcode values** - All parameters come from `config.py`
6. **Risk checks run BEFORE strategy logic** - Every minute, every time

---

## Target Performance

| Metric | Target |
|--------|:------:|
| Annual Return | 25-40% |
| Maximum Drawdown | < 20% |
| Sharpe Ratio | > 1.0 |
| Win Rate | > 55% |

---

## Status

**v0.6.0 Released** - Phase 6 Complete (All Phases Done - Ready for Backtesting)

| Phase | Status | Description |
|:-----:|:------:|-------------|
| 0 | ✅ Done | CI/CD, branch protection, Lean CLI integration |
| 1 | ✅ Done | config.py, models, utils/calculations.py |
| 2 | ✅ Done | Core engines (regime_engine, capital_engine) |
| 3 | ✅ Done | Strategy engines (cold_start, trend, mr, hedge, yield) |
| 4 | ✅ Done | Coordination (exposure_groups, portfolio_router, risk_engine) |
| 5 | ✅ Done | Execution & state (execution_engine, state_manager, daily_scheduler) |
| 6 | ✅ Done | Integration (main.py - 1,332 lines, 35 methods) |

**Next Steps:** Run full backtests (2020-2024), paper trading, then live deployment.

---

## Project Journal

Technical blog posts documenting this project's journey:

| Date | Post |
|------|------|
| 2026-01-26 | [The Humbling Truth: When Complexity Loses to Simplicity](https://github.com/vickdavinci/tech-journal/blob/main/alpha-nextgen/2026-01-26-root-cause-analysis.md) |
| 2026-01-26 | [The Backtest Reality Check: When Your Code Meets the Cloud](https://github.com/vickdavinci/tech-journal/blob/main/alpha-nextgen/2026-01-26-backtest-reality-check.md) |
| 2026-01-25 | [Phase 1-6 Complete: Building an Algorithmic Trading System in 48 Hours](https://github.com/vickdavinci/tech-journal/blob/main/alpha-nextgen/2026-01-25-phase1-6-complete.md) |
| 2026-01-25 | [From Mainframe to Modern DevOps: Building a Trading System Foundation in 3 Days](https://github.com/vickdavinci/tech-journal/blob/main/alpha-nextgen/2026-01-25-phase0-release.md) |

Full journal: [tech-journal/alpha-nextgen](https://github.com/vickdavinci/tech-journal/tree/main/alpha-nextgen)

---

## License

Private - Not for distribution

---

## Contact

For questions about this system, refer to the documentation in `docs/` or `CLAUDE.md`.
