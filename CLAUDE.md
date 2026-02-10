# CLAUDE.md - Claude Code Instructions

## 🚨 WAKE-UP PROTOCOL (READ FIRST AFTER COMPACTION)

**Context Amnesia Warning:** If this session just started or was compacted, you have lost:
- Shell state (venv not active)
- Memory of what task you were working on
- Any files you previously read

**Before doing anything else, run these commands:**

```bash
# 1. Activate environment and verify Python version
source venv/bin/activate && python --version
# Expected: Python 3.11.x (NOT 3.14)

# 2. Check current task state
head -60 WORKBOARD.md

# 3. Check git status for uncommitted work
git status && git branch
```

**Why this matters:**
- System default is Python 3.14, but project requires 3.11
- WORKBOARD.md tracks what task is in progress
- You may have uncommitted changes from before compaction

---

## Build & Test Commands

```bash
# Setup (first time)
make setup                    # Create venv, install deps, pre-commit hooks

# Run all tests
make test                     # or: pytest

# Run single test file
pytest tests/test_regime_engine.py -v

# Run single test function
pytest tests/test_regime_engine.py::test_regime_score_boundaries -v

# Run scenario tests only
pytest tests/scenarios/ -v

# Run tests matching pattern
pytest -k "kill_switch" -v

# Lint and format
make lint                     # Black + isort

# Validate config against spec
make validate-config

# Create feature branch
make branch name=feature/va/my-feature
```

---

## QuantConnect Infrastructure

> **Plan:** Trading Firm ($48/mo) + 2× B4-12 nodes ($48/mo) = **$96/mo**

| Resource | Limit | Notes |
|----------|------:|-------|
| File Size | 256 KB | No minification needed |
| Backtest Log | 5 MB | Use `trades_only=True` for essential logs |
| Daily Log | 50 MB | Multiple debug runs allowed |
| Plot Points | 32,000 | ~14K for 5-year backtest |
| Backtest Nodes | 2× B4-12 | 4 cores, 12GB RAM - required for options data |

### Logging Pattern (CRITICAL)

```python
# ALWAYS log (trades_only=True): fills, entries, exits, errors, kill switch
self.log("FILL: BUY 100 QLD", trades_only=True)

# LIVE ONLY (trades_only=False): signals, diagnostics, regime updates
self.log("INTRADAY_SIGNAL: ...", trades_only=False)  # Silent in backtests
```

**Without this pattern:** 400+ logs/day kills backtests. See `docs/guides/backtest-workflow.md`.

### QuantConnect Backtest Workflow

> **Use the automated script!** Do NOT manually sync files.

**Cloud Project:** `AlphaNextGen` (cloud-id: 27678023)
**Lean Workspace:** `~/Documents/Trading Github/lean-workspace/AlphaNextGen`

```bash
# RECOMMENDED: Wait for completion and see results (use this!)
./scripts/qc_backtest.sh "V2.11-MyFeature" --open

# Fire-and-forget (async, just starts the backtest):
./scripts/qc_backtest.sh "V2.4.4-MyFeature"

# Auto-generated name from git branch:
./scripts/qc_backtest.sh --open
```

**IMPORTANT FOR CLAUDE:** Always use `--open` flag to wait for backtest completion and access results directly. Without `--open`, the backtest runs async and you cannot see the results.

**What the script does:**
1. Syncs ALL project files (main.py, config.py, engines/, portfolio/, etc.) to lean-workspace
2. Pushes to QuantConnect cloud via `lean cloud push`
3. Starts the backtest with specified name
4. With `--open`: Waits for completion and streams results to terminal
5. Prints the backtest URL for viewing results

**Example output:**
```
╔═══════════════════════════════════════════════════════════════╗
║           QC BACKTEST - AlphaNextGen V2                       ║
╚═══════════════════════════════════════════════════════════════╝

Backtest Name: V2.4.4-P0-Fixes
[1/3] Syncing files to lean workspace...
   ✓ Synced 47 Python files
[2/3] Pushing to QuantConnect cloud...
   ✓ Push complete
[3/3] Starting backtest...
   ✓ Backtest started

URL:  https://www.quantconnect.com/project/27678023/...
```

**Notes:**
- lean CLI is already installed globally
- Trading Firm plan allows 256KB files (no minification needed)
- Use B4-12 nodes for options backtests (requires more memory)
- Results viewable at: https://www.quantconnect.com/terminal

### Engine Isolation Mode (V6.4)

For targeted backtesting of individual engines, use **Isolation Mode** to disable all other engines and safeguards.

**To test Options Engine only:**
```python
# In config.py, set:
ISOLATION_TEST_MODE = True
```

**What gets disabled:**
- Kill Switch, Startup Gate, Cold Start
- Trend, Mean Reversion, Hedge, Yield engines
- Drawdown Governor, Panic Mode, Weekly Breaker
- Gap Filter, Vol Shock

**What stays enabled:**
- Regime Engine (required for options direction)
- Options Engine (VASS Swing + Micro Intraday)
- Time Guards (13:55-14:10 block)
- Position Limits

**To return to normal:**
```python
ISOLATION_TEST_MODE = False
```

See `docs/guides/ENGINE_ISOLATION_MODE.md` for full configuration options.

---

## Project Overview

**Alpha NextGen V2** is a multi-strategy algorithmic trading system built on QuantConnect (LEAN engine) for deployment on Interactive Brokers. The system implements a **Core-Satellite** architecture:

- **Core (40%)**: Trend Engine - MA200 + ADX confirmation across equities + commodities (V6.11)
  - QLD (15%) - 2× Nasdaq (primary equity)
  - SSO (7%) - 2× S&P 500 (broad equity)
  - UGL (10%) - 2× Gold (commodity hedge, uncorrelated)
  - UCO (8%) - 2× Crude Oil (energy/inflation hedge)
- **Satellite (10%)**: Mean Reversion Engine - RSI oversold bounce with VIX filter
  - TQQQ (4%) - 3× Nasdaq
  - SPXL (3%) - 3× S&P 500
  - SOXL (3%) - 3× Semiconductor
- **Satellite (25%)**: Options Engine - VASS + Dual-Mode Architecture (V6.10)
  - **Swing Mode (18.75%)**: VASS debit/credit spreads (14-45 DTE, VASS routes by IV)
  - **Intraday Mode (6.25%)**: Micro Regime Engine - VIX Level × VIX Direction (1-5 DTE)

Forked from V1 v1.0.0 on 2026-01-26. See `docs/specs/v2.1/` for V2.1 specifications (archived).
See `docs/specs/v2.1/v2-1-options-engine-design.txt` for Options Engine V2.1.1 design reference.

## Repository Structure

```
alpha-nextgen/
├── main.py                     # QCAlgorithm entry point (~3,800 lines - V2.24.2)
├── config.py                   # ALL tunable parameters
├── requirements.txt            # Python dependencies
├── requirements.lock           # Locked versions for reproducibility
├── pyproject.toml              # Unified tool config (pytest, black, mypy)
├── Makefile                    # Workflow automation (make setup, make test)
├── .python-version             # Python 3.11
├── .pre-commit-config.yaml     # Pre-commit hooks configuration
│
├── WORKBOARD.md                # Task tracking & ownership (who's working on what)
├── QUICKSTART.md               # Fast onboarding guide (5 min setup)
├── CONTRIBUTING.md             # Git workflow, branch naming, commit format
├── developer-guide-claude.md   # Development guide - build workflow
├── PROJECT-STRUCTURE.md        # Visual structure with Mermaid diagrams
├── CLAUDE.md                   # AI assistant instructions (this file)
├── QC_RULES.md                 # QuantConnect coding patterns
├── ERRORS.md                   # Common errors and solutions
│
├── .github/
│   ├── workflows/
│   │   └── test.yml            # CI/CD pipeline (pytest, linting, validation)
│   └── PULL_REQUEST_TEMPLATE.md # PR checklist for developers
│
├── .claude/
│   └── config.json             # Claude Code project settings
│
├── scripts/
│   ├── validate_config.py      # Spec compliance checker
│   ├── check_spec_parity.py    # Code-to-spec update warning
│   ├── qc_backtest.sh          # Automated sync → push → backtest pipeline
│   └── minify_workspace.py     # Strip comments/docstrings for QC push size limit
│
├── historical/
│   └── V2_IMPLEMENTATION_ROADMAP.md  # Historical roadmap (archived)
│
├── engines/                    # V2 Core-Satellite architecture
│   ├── core/                   # Foundational engines (always active)
│   │   ├── regime_engine.py    # Market state detection
│   │   ├── capital_engine.py   # Position sizing
│   │   ├── risk_engine.py      # Circuit breakers
│   │   ├── cold_start_engine.py # Startup handling (resets on kill switch)
│   │   ├── startup_gate.py     # V2.30: All-weather time-based arming (permanent)
│   │   └── trend_engine.py     # MA200+ADX (40%)
│   └── satellite/              # Conditional engines
│       ├── mean_reversion_engine.py # Intraday bounce (0-10%)
│       ├── hedge_engine.py     # TMF/PSQ overlay
│       ├── yield_sleeve.py     # SHV cash management
│       └── options_engine.py   # QQQ options (25%) - VASS + Dual-Mode + Micro Regime
├── portfolio/                  # Router, exposure groups, positions
├── execution/                  # Order management
├── data/                       # Symbols, indicators, validation
├── models/                     # Data classes and enums
├── persistence/                # State save/load (ObjectStore)
├── scheduling/                 # Timed events
├── utils/                      # Helper functions
├── tests/                      # Unit and scenario tests
└── docs/
    ├── system/                 # Core system docs (00-19)
    ├── specs/v2.1/             # V2.1 design specs (archived)
    ├── audits/                 # Backtest results, code audits
    │   └── v2.1/               # V2.1 audits (archived)
    └── internal/               # Documentation map
```

See [PROJECT-STRUCTURE.md](PROJECT-STRUCTURE.md) for detailed file listing with Mermaid diagrams.

---

## Component Map

**This is the single index for the entire system.** When debugging or modifying any component, read the corresponding spec file first.

### Core Engines (engines/core/)

| Component | File | Spec Document | Description |
|-----------|------|---------------|-------------|
| **Regime Engine** | `engines/core/regime_engine.py` | `docs/system/04-regime-engine.md` | 4-factor market state scoring (0-100) |
| **Capital Engine** | `engines/core/capital_engine.py` | `docs/system/05-capital-engine.md` | Phase management, lockbox, tradeable equity |
| **Risk Engine** | `engines/core/risk_engine.py` | `docs/system/12-risk-engine.md` | All circuit breakers and safeguards (tiered KS, drawdown governor) |
| **Cold Start Engine** | `engines/core/cold_start_engine.py` | `docs/system/06-cold-start-engine.md` | Days 1-5 warm entry logic |
| **Startup Gate** | `engines/core/startup_gate.py` | `docs/system/ENGINE_LOGIC_REFERENCE.md` | V2.30: All-weather time-based arming (15 days: 5+5+5). Never resets on kill switch. |
| **Trend Engine** | `engines/core/trend_engine.py` | `docs/system/07-trend-engine.md` | MA200 + ADX trend signals for QLD/SSO/UGL/UCO (40%) - V6.11: equities + commodities |

### Satellite Engines (engines/satellite/)

| Component | File | Spec Document | Description |
|-----------|------|---------------|-------------|
| **Mean Reversion Engine** | `engines/satellite/mean_reversion_engine.py` | `docs/system/08-mean-reversion-engine.md` | Intraday oversold bounce signals for TQQQ/SPXL/SOXL (10%) |
| **Hedge Engine** | `engines/satellite/hedge_engine.py` | `docs/system/09-hedge-engine.md` | Regime-based TMF/PSQ allocation signals |
| **Yield Sleeve** | `engines/satellite/yield_sleeve.py` | `docs/system/10-yield-sleeve.md` | SHV cash management signals |
| **Options Engine** | `engines/satellite/options_engine.py` | `docs/system/18-options-engine.md` | QQQ options Dual-Mode (25%): Swing 18.75% + Intraday 6.25% (Micro Regime) |

### Infrastructure

| Component | File | Spec Document | Description |
|-----------|------|---------------|-------------|
| **Portfolio Router** | `portfolio/portfolio_router.py` | `docs/system/11-portfolio-router.md` | Central coordination, order authorization |
| **Execution Engine** | `execution/execution_engine.py` | `docs/system/13-execution-engine.md` | Order submission to broker |
| **OCO Manager** | `execution/oco_manager.py` | `docs/system/19-oco-manager.md` | One-Cancels-Other order pairs for options |
| **Position Manager** | `portfolio/position_manager.py` | `docs/system/15-state-persistence.md` | Entry prices, stops, highest highs |
| **State Manager** | `persistence/state_manager.py` | `docs/system/15-state-persistence.md` | ObjectStore save/load |

### Supporting Documents

| Document | Description |
|----------|-------------|
| `docs/system/14-daily-operations.md` | Complete daily timeline and event schedule |
| `docs/system/16-appendix-parameters.md` | All tunable parameters in one place |
| `docs/system/17-appendix-glossary.md` | Terms, abbreviations, formulas |
| `docs/specs/v2.1/` | V2.1 specifications and architecture guides |
| `docs/specs/v2.1/v2-1-options-engine-design.txt` | Options Engine V2.1.1 (Dual-Mode + Micro Regime) |

---

## Feature Branches (Not on develop)

Some features are developed on separate branches to keep core trading logic clean:

| Branch | Contents | Status |
|--------|----------|--------|
| `feat/backtest-reporting` | Metrics engine (Sharpe, Sortino, drawdown), CSV export, QC charts | Complete, not integrated |

```bash
# Access feature branch
git checkout feat/backtest-reporting

# Return to main development
git checkout develop

# Merge when ready for production
git checkout develop && git merge feat/backtest-reporting
```

See `WORKBOARD.md` → "Feature Branches" section for full details.

---

## Critical Rules

### 1. Strategy Engines Are Analyzers Only

**Strategy Engines (`Trend`, `MeanRev`, `Hedge`, `Yield`, `ColdStart`) emit `TargetWeight` objects. They are NOT authorized to place orders.**

Only the **Portfolio Router** is authorized to call:
- `self.MarketOrder()`
- `self.MarketOnOpenOrder()`
- `self.Liquidate()`

```python
# CORRECT - Strategy engine emits intention
class TrendEngine:
    def generate_signals(self) -> List[TargetWeight]:
        if self.has_entry_signal():  # MA200 + ADX confirmation
            return [TargetWeight("QLD", 0.30, "TREND", Urgency.EOD, "MA200_ADX Entry")]
        return []

# WRONG - Strategy engine placing orders directly
class TrendEngine:
    def generate_signals(self):
        if self.is_breakout():
            self.algorithm.MarketOnOpenOrder("QLD", 100)  # ❌ NEVER DO THIS
```

### 2. Risk Engine Always First

Every minute-level check must run Risk Engine BEFORE any strategy logic:

```python
def OnData(self, data):
    # ALWAYS FIRST - Check splits on proxy symbols (freezes everything)
    if data.Splits.ContainsKey(self.spy):
        self.Log("SPLIT_GUARD: SPY split detected, freezing all processing")
        return
    
    # ALWAYS SECOND - Risk checks
    if self.risk_engine.check_kill_switch():
        return  # All other processing skipped
    
    if self.risk_engine.check_panic_mode():
        return  # Longs liquidated, skip entry logic
    
    # Now safe to run strategies
    self.mr_engine.scan_for_signals(data)
```

### 3. Overnight Safety

**MUST liquidate by 15:45 ET (Mean Reversion intraday only):**
- `TQQQ` — 3× Nasdaq (Mean Reversion)
- `SPXL` — 3× S&P 500 (Mean Reversion)
- `SOXL` — 3× Semiconductor (Mean Reversion)

**Allowed overnight holds (Trend Engine swing trades + Hedges):**
- `QLD` — 2× Nasdaq (Trend/Swing)
- `SSO` — 2× S&P 500 (Trend/Swing)
- `UGL` — 2× Gold (Trend/Swing)
- `UCO` — 2× Crude Oil (Trend/Swing)
- `SH` — 1× Inverse S&P (Strategic Hedge)

> **V6.11 Universe Redesign:** All Trend symbols are 2× leverage. TNA/FAS replaced with UGL/UCO for commodity diversification. TMF/PSQ/SHV removed; SH is the only hedge symbol.

```python
# In Mean Reversion Engine - enforced at 15:45
INTRADAY_ONLY_SYMBOLS = ["TQQQ", "SPXL", "SOXL"]

def force_close_intraday_positions(self):
    """Force close all MR positions at 15:45."""
    for symbol in INTRADAY_ONLY_SYMBOLS:
        if self.Portfolio[symbol].Invested:
            # Emit exit signal - Router will execute
            self.emit_target_weight(symbol, 0.0, Urgency.IMMEDIATE, "TIME_EXIT_15:45")
```

### 4. Lockbox Is Sacred

Never allow lockbox capital to be traded:

```python
def get_available_shv(self) -> float:
    """Get SHV available for liquidation (excluding lockbox)."""
    total_shv = self.Portfolio["SHV"].HoldingsValue
    return max(0, total_shv - self.lockbox_amount)
```

### 5. State Must Persist

Any state that affects trading decisions must be persisted:

```python
# At end of day
self.save_state()

# On restart
def Initialize(self):
    self.load_state()
    self.reconcile_positions()
```

---

## Coding Conventions

### 1. QuantConnect Patterns

Always use QuantConnect's API patterns. See `QC_RULES.md` for details.

```python
# CORRECT - QuantConnect style
from AlgorithmImports import *

self.AddEquity("SPY", Resolution.Minute)
self.Schedule.On(self.DateRules.EveryDay(), self.TimeRules.At(9, 30), self.OnMarketOpen)

# WRONG - Generic Python imports
import datetime  # ❌ Use from AlgorithmImports import *
import pandas    # ❌ Use from AlgorithmImports import *
```

### 2. Parameter Access

**Never hardcode values.** Always reference `config.py`:

```python
# CORRECT
from config import KILL_SWITCH_PCT, ADX_PERIOD

if loss_pct >= KILL_SWITCH_PCT:
    self.trigger_kill_switch()

# WRONG
if loss_pct >= 0.03:  # Magic number
    self.trigger_kill_switch()
```

### 3. Type Hints

Use type hints for all function signatures:

```python
from typing import Optional, List, Dict
from models.target_weight import TargetWeight
from models.enums import Urgency

def generate_signal(self, symbol: str, regime_score: float) -> Optional[TargetWeight]:
    ...
```

### 4. Docstrings

Use Google-style docstrings:

```python
def calculate_stop(self, entry_price: float, atr: float, profit_pct: float) -> float:
    """Calculate Chandelier trailing stop level.
    
    Args:
        entry_price: Original entry price for the position.
        atr: Current 14-period Average True Range.
        profit_pct: Current profit as decimal (0.15 = 15%).
    
    Returns:
        Stop price level. Never moves down from previous stop.
    
    Raises:
        ValueError: If entry_price or atr is non-positive.
    """
```

### 5. Logging

Use QuantConnect's logging with structured messages:

```python
# CORRECT - Structured, informative
self.Log(f"TREND_ENTRY: {symbol} | Price={price:.2f} | Regime={regime_score} | Size=${size:,.0f}")
self.Log(f"KILL_SWITCH: Loss={loss_pct:.2%} | Baseline={baseline:,.2f} | Current={current:,.2f}")

# WRONG - Vague
self.Log("Entered position")
self.Log("Kill switch triggered")
```

### 6. Error Handling

Always handle errors gracefully with logging:

```python
try:
    data = json.loads(self.ObjectStore.Read(key))
except json.JSONDecodeError as e:
    self.Log(f"STATE_ERROR: Failed to parse {key}: {e}")
    data = self.get_default_state()
except Exception as e:
    self.Log(f"STATE_ERROR: Unexpected error loading {key}: {e}")
    data = self.get_default_state()
```

---

## Data Flow Architecture

```
┌───────────────────────────────────────────────────────────────────┐
│                         CORE ENGINES                              │
├─────────────────┬─────────────────┬─────────────────┬─────────────┤
│  Regime Engine  │  Capital Engine │   Risk Engine   │ Startup Gate│
│  (Score 0-100)  │  (Phase/Lockbox)│ (Tiered KS/DG)  │ (15 days)   │
└────────┬────────┴────────┬────────┴────────┬────────┴──────┬──────┘
         │                 │                 │               │
         ▼                 ▼                 ▼               ▼
┌───────────────────────────────────────────────────────────────────┐
│                       STRATEGY ENGINES                            │
├─────────────────┬─────────────────┬─────────────────┬─────────────┤
│  Trend Engine   │ Options Engine  │    MR Engine    │Hedge/Yield  │
│   (Core 40%)    │ (Satellite 25%) │ (Satellite 10%) │  (Overlay)  │
│QLD,SSO,UGL,UCO  │ VASS Dual-Mode  │ TQQQ,SPXL,SOXL  │ TMF,PSQ,SHV │
│  Urgency: MOC   │ Swing + Intraday│ Urgency: IMMED  │Urgency: EOD │
└────────┬────────┴────────┬────────┴────────┬────────┴──────┬──────┘
         │                 │                 │               │
         └─────────────────┴─────────────────┴───────────────┘
                                   │
                          TargetWeight Objects
                                   │
                                   ▼
┌───────────────────────────────────────────────────────────────────┐
│                      PORTFOLIO ROUTER                             │
│  1. Collect → 2. Aggregate → 3. Validate → 4. Net → 5. Execute   │
│        (ONLY component authorized to place orders)                │
└──────────────────────────────┬────────────────────────────────────┘
                               │
                               ▼
┌───────────────────────────────────────────────────────────────────┐
│                      EXECUTION ENGINE                             │
├───────────────────────────────┬───────────────────────────────────┤
│         Market Orders         │           MOC Orders              │
│    (MR, Options, Stops)       │    (Trend, Hedge, Yield)          │
├───────────────────────────────┼───────────────────────────────────┤
│         OCO Manager           │        Fill Handler               │
│   (Options profit/stop pairs) │    (Position tracking)            │
└───────────────────────────────┴───────────────────────────────────┘
                               │
                               ▼
                         ┌───────────┐
                         │   IBKR    │
                         │  Broker   │
                         └───────────┘
```

---

## Testing Guidelines

### Unit Tests

Each engine should have comprehensive unit tests:

```python
# tests/test_regime_engine.py
def test_regime_score_boundaries():
    """Test regime correctly classifies edge cases."""
    assert classify_regime(70) == "RISK_ON"
    assert classify_regime(69) == "NEUTRAL"
    assert classify_regime(40) == "CAUTIOUS"
    assert classify_regime(39) == "DEFENSIVE"
    assert classify_regime(30) == "DEFENSIVE"
    assert classify_regime(29) == "RISK_OFF"
```

### Scenario Tests

Test complete workflows in `/tests/scenarios`:

```python
# tests/scenarios/test_kill_switch_scenario.py
def test_kill_switch_liquidates_all():
    """Verify kill switch liquidates all positions."""
    # Setup: Portfolio with multiple positions
    # Action: Trigger 3% loss
    # Assert: All positions = 0, cold start reset
```

---

## Common Pitfalls

See `ERRORS.md` for detailed error solutions. Key issues:

1. **Indicator not ready** - Check `indicator.IsReady` before using
2. **Symbol not found** - Ensure `AddEquity()` called in `Initialize()`
3. **Division by zero** - Always check denominators
4. **Time zone confusion** - All times are Eastern; use `self.Time`
5. **State not loaded** - Always call `load_state()` in `Initialize()`
6. **Strategy placing orders** - Only Portfolio Router places orders
7. **3× held overnight** - TQQQ/SOXL must close by 15:45

---

## Making Changes

### Before Modifying Code

1. Read the relevant spec document from the Component Map
2. Understand the data flow and dependencies
3. Check `config.py` for related parameters
4. Review existing tests

### After Modifying Code

1. Run all unit tests
2. Run scenario tests for affected workflows
3. **Update documentation** (see Documentation Requirements below)
4. Update `config.py` if new parameters added

---

## Documentation Update Requirements (MANDATORY)

> **This is NOT optional.** Documentation updates are part of the definition of "done" for any code change.

### After ANY Code Change, You MUST:

1. **Consult `docs/DOCUMENTATION-MAP.md`** to identify which documentation is affected
2. **Update ALL affected documentation** listed in the map
3. **Include doc updates in the same commit/PR** as the code change

### Quick Reference: Common Updates

| If You Changed... | Update These Docs |
|-------------------|-------------------|
| Any engine file | Corresponding `docs/XX-engine.md` + Component Map |
| `config.py` | `docs/16-appendix-parameters.md` + Key Thresholds table |
| New file in root | This file (Repository Structure) + `PROJECT-STRUCTURE.md` |
| CI/workflow files | `CONTRIBUTING.md` |
| Test structure | `CONTRIBUTING.md` → Testing section |
| Models/enums | `PROJECT-STRUCTURE.md` + Component Map |

### Why This Matters

- Documentation is read by both humans and AI assistants
- Outdated docs cause confusion and bugs
- The cost of updating docs NOW is much lower than fixing confusion LATER

### The Golden Rule

> **If you changed code, you changed documentation.**
>
> Even if the change seems "internal" or "minor", check the documentation map.
> Future developers (and future Claude sessions) will thank you.

---

## Quick Reference

### Key Times (Eastern)

| Time | Event |
|------|-------|
| 09:25 | Pre-market setup, set `equity_prior_close` |
| 09:30 | Market open, MOO orders execute |
| 09:31 | MOO fallback check |
| 09:33 | Set `equity_sod`, check gap filter |
| 10:00 | Warm entry check, MR window opens |
| 13:55 | Time guard starts (entries blocked) |
| 14:10 | Time guard ends |
| 15:00 | MR entry window closes |
| 15:45 | **TQQQ/SOXL force close**, EOD processing, MOO submission |
| 16:00 | Market close, state persistence |

### Key Thresholds

| Threshold | Value | Triggers |
|-----------|-------|----------|
| Kill switch | 5% daily loss | Full liquidation (V2.3.17: raised from 3%) |
| Preemptive KS | 4.5% daily loss | Warning threshold |
| Panic mode | SPY -4% intraday | Liquidate longs only |
| Weekly breaker | 5% WTD loss | 50% sizing reduction |
| Gap filter | SPY -1.5% gap | Block MR entries |
| Vol shock | 3× ATR bar | 15-min pause |
| Leverage cap | 90% margin | Block new entries (V2.18) |
| Trend entry (V2) | Price > MA200 + ADX >= 15 | Trend entry eligible (V2.3.12: was 25) |
| Oversold | RSI(5) < 25 | MR entry eligible |
| VIX Low | VIX < 15 | Complacent market, cheap options |
| VIX Normal | VIX 15-22 | Normal volatility |
| VIX High | VIX 22-30 | Elevated fear |
| VIX Extreme | VIX > 40 | Crisis mode |
| V5.3 VIX Clamp (V6.6) | VIX >= 25 | VIX Combined score capped at 47 |
| V5.3 Spike Cap (V6.6) | VIX 5d >= +28% | Regime score capped at 38 (DEFENSIVE) |
| V5.3 Breadth Decay (V6.9) | RSP/SPY 5d < -1% | 8-point regime penalty |
| VASS Low IV (V2.8) | VIX < 15 | Debit spreads, 30-45 DTE (monthly) |
| VASS Medium IV (V2.8) | VIX 15-25 | Debit spreads, 7-21 DTE (weekly) |
| VASS High IV (V6.8) | VIX > 25 | Credit spreads, 5-28 DTE (was 7-21) |
| UVXY Bearish (V6.10) | UVXY > +2.5% | PUT conviction signal (was +4%) |
| UVXY Bullish (V6.10) | UVXY < -3% | CALL conviction signal (was -5%) |
| UVXY Conviction Extreme (V6.10) | 5% | NEUTRAL VETO threshold (was 7%) |
| BEAR_PUT OTM Gate (V6.4) | Short PUT >= 3% OTM | Block assignment-risk entries |
| Options ATR Stop Max (V6.8) | 30% | Max loss cap (was 50%) |
| Intraday FADE VIX Min (V6.10) | VIX >= 10.5 | Enable DEBIT_FADE (was 13.5) |
| Intraday ITM VIX Min (V6.8) | VIX >= 10.0 | Enable ITM momentum (was 11.5) |
| Spread Force Close DTE (V6.10) | DTE = 1 | Mandatory spread close (assignment prevention) |
| Short Leg ITM Exit (V6.10) | 0.5% ITM | Exit spread when short leg ITM (was 1%) |
| Spread Width Min (V6.10) | $5 | Minimum spread width (was $3, assignment protection) |
| Spread Stop/Target (V6.10) | 40%/40% | Symmetric R:R (was 35%/50%) |
| Choppy Market Filter (V6.10) | 3 reversals/2hr | 50% size reduction in choppy markets |
| VIX Stable Band Low (V6.13.1) | +/-0.2% | STABLE band when VIX < 15 (was +/-0.3%) |
| VIX Stable Band High (V6.13.1) | +/-0.7% | STABLE band when VIX > 25 (was +/-1.0%) |
| Margin Pre-Check (V6.10) | 15% buffer | Check margin before signal approval |

### Overnight Holdings (V6.11 Universe)

| Symbol | Type | Overnight? |
|--------|------|:----------:|
| QLD | 2× Nasdaq (Trend) | Yes |
| SSO | 2× S&P 500 (Trend) | Yes |
| UGL | 2× Gold (Trend) | Yes |
| UCO | 2× Crude Oil (Trend) | Yes |
| SH | 1× Inverse S&P (Hedge) | Yes |
| TQQQ | 3× Nasdaq (MR) | **Close by 15:45** |
| SPXL | 3× S&P 500 (MR) | **Close by 15:45** |
| SOXL | 3× Semiconductor (MR) | **Close by 15:45** |

> **V6.11 Note:** TMF/PSQ/SHV removed from default universe. SH is the only hedge symbol. All Trend symbols are 2× leverage.

### Exposure Limits

| Group | Max Net Long | Max Gross |
|-------|:------------:|:---------:|
| NASDAQ_BETA | 50% | 75% |
| SPY_BETA | 40% | 40% |
| COMMODITY | 20% | 20% |
| RATES | 99% | 99% (V2.3.17: raised from 40% for SHV) |
