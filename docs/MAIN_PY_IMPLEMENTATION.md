# main.py Implementation Summary

**Date**: January 2025
**Phase**: 6 - QCAlgorithm Entry Point
**Status**: Complete

---

## Overview

This document summarizes the implementation of `main.py`, the central entry point for the Alpha NextGen trading system on QuantConnect. The implementation wires together all engines, infrastructure components, and scheduled events into a cohesive trading algorithm.

### Key Statistics

| Metric | Value |
|--------|-------|
| Total Lines | 1,332 |
| Methods | 35 |
| Tests Passed | 710 |
| Tests Skipped | 25 (scenario tests) |

---

## Architecture

The `AlphaNextGen` class inherits from `QCAlgorithm` and implements the Hub-and-Spoke architecture:

```
                    ┌─────────────────────┐
                    │   AlphaNextGen      │
                    │   (main.py)         │
                    └──────────┬──────────┘
                               │
        ┌──────────────────────┼──────────────────────┐
        │                      │                      │
        ▼                      ▼                      ▼
┌───────────────┐    ┌─────────────────┐    ┌────────────────┐
│ Core Engines  │    │ Strategy Engines│    │ Infrastructure │
├───────────────┤    ├─────────────────┤    ├────────────────┤
│ RegimeEngine  │    │ TrendEngine     │    │ PortfolioRouter│
│ CapitalEngine │    │ MREngine        │    │ ExecutionEngine│
│ RiskEngine    │    │ HedgeEngine     │    │ StateManager   │
│ ColdStartEngine│   │ YieldSleeve     │    │ DailyScheduler │
└───────────────┘    └─────────────────┘    └────────────────┘
```

---

## Implementation Checklist

The implementation was completed in 19 sequential tasks:

| # | Task | Description |
|---|------|-------------|
| 1 | Imports | All engines, infrastructure, models, config |
| 2 | Class Attributes | Type hints for all instance variables |
| 3 | _add_securities() | Add 11 symbols (7 traded + 4 proxy) |
| 4 | _setup_indicators() | SMAs, ATRs, BBs, RSIs, Rolling Windows |
| 5 | _initialize_engines() | 8 engines with algorithm reference |
| 6 | _initialize_infrastructure() | Router, Execution, State, Scheduler |
| 7 | _setup_schedules() | Register 10 scheduled events |
| 8 | Initialize() | Wire all setup steps together |
| 9 | _check_splits() | Split detection for proxy/traded symbols |
| 10 | _update_rolling_windows() | Historical price tracking |
| 11 | OnData() | Minute-by-minute processing flow |
| 12 | Morning Handlers | Pre-market, MOO fallback, SOD baseline |
| 13 | Midday Handlers | Warm entry, time guard start/end |
| 14 | EOD Handlers | MR force close, EOD processing, market close |
| 15 | OnOrderEvent() | Fill, reject, cancel handling |
| 16 | State Management | Load, save, reconcile positions |
| 17 | Signal Processing | Immediate and EOD signal execution |
| 18 | Utility Helpers | Risk checks, regime calc, logging |
| 19 | Testing | Syntax validation and test suite |

---

## Key Methods

### Lifecycle Methods

| Method | Trigger | Purpose |
|--------|---------|---------|
| `Initialize()` | Algorithm start | Setup securities, indicators, engines, schedules |
| `OnData()` | Every minute | Process data, run risk checks, scan signals |
| `OnOrderEvent()` | Order status change | Track fills, handle rejections |

### Scheduled Event Handlers

| Method | Time (ET) | Purpose |
|--------|-----------|---------|
| `_on_pre_market_setup()` | 09:25 | Set equity_prior_close baseline |
| `_on_moo_fallback()` | 09:31 | Convert failed MOO to market orders |
| `_on_sod_baseline()` | 09:33 | Set equity_sod, check gap filter |
| `_on_warm_entry_check()` | 10:00 | Cold start warm entry |
| `_on_time_guard_start()` | 13:55 | Block entries (Fed window) |
| `_on_time_guard_end()` | 14:10 | Resume entries |
| `_on_mr_force_close()` | 15:45 | Force close TQQQ/SOXL |
| `_on_eod_processing()` | 15:45 | Regime, signals, MOO submission |
| `_on_market_close()` | 16:00 | Persist state, daily summary |
| `_on_weekly_reset()` | Mon 09:30 | Reset weekly breaker |

### OnData() Flow

The `OnData()` method follows a strict execution order:

```
1. Split Check (MUST BE FIRST)
   └─ Proxy split → freeze ALL
   └─ Traded split → freeze that symbol

2. Warmup Check
   └─ Skip if warming up

3. Update Rolling Windows
   └─ SPY, RSP, HYG, IEF closes

4. Risk Engine Checks
   └─ Kill switch, panic mode, weekly breaker, etc.

5. Handle Kill Switch
   └─ Liquidate ALL, reset cold start, return

6. Handle Panic Mode
   └─ Liquidate longs only, continue

7. MR Entry Scanning
   └─ Only if window open and entries allowed

8. MR Exit Checking
   └─ Target profit, stop loss

9. Trend Stop Monitoring
   └─ Chandelier trailing stops

10. Process Immediate Signals
    └─ Execute via MarketOrder
```

---

## Issues Encountered and Solutions

### Issue 1: Missing PositionManager Class

**Problem**: The `PositionManager` class was imported but `portfolio/position_manager.py` was a stub file with only a docstring.

```python
# Error
from portfolio.position_manager import PositionManager
# ImportError: cannot import name 'PositionManager'
```

**Solution**: Removed the import and class attribute since position tracking is handled within `PortfolioRouter`.

```python
# Before
from portfolio.position_manager import PositionManager
position_manager: PositionManager

# After
# (removed - PortfolioRouter handles position tracking)
```

**Files Changed**: `main.py` (imports and class attributes)

---

### Issue 2: RegimeState and CapitalState in Wrong Module

**Problem**: Imported `RegimeState` and `CapitalState` from `models/` but these were stub files. The actual classes are defined in the engine files.

```python
# Error
from models.regime_state import RegimeState
from models.capital_state import CapitalState
# ImportError: cannot import name 'RegimeState'
```

**Solution**: Import from the engine modules where the classes are actually defined.

```python
# Before
from models.regime_state import RegimeState
from models.capital_state import CapitalState

# After
from engines.regime_engine import RegimeEngine, RegimeState
from engines.capital_engine import CapitalEngine, CapitalState
```

**Root Cause**: The `models/` directory contains stubs that were planned for Phase 6 but the classes were already implemented inside the engines during earlier phases.

**Files Changed**: `main.py` (imports section)

---

### Issue 3: QCAlgorithm Not Defined Locally

**Problem**: When testing imports locally, `QCAlgorithm` is not defined because it only exists in the QuantConnect environment.

```python
# Error when running: python -c "import main"
NameError: name 'QCAlgorithm' is not defined
```

**Solution**: This is expected behavior. The `from AlgorithmImports import *` only works in QuantConnect's cloud environment. Local testing uses:
1. Syntax checking: `python -m py_compile main.py`
2. Unit tests that mock the QC environment

**Verification**: Confirmed syntax is valid and all 710 unit tests pass.

---

## Design Decisions

### 1. Split Check First

The split check is the **first** operation in `OnData()` because:
- Proxy symbol splits (SPY, RSP, HYG, IEF) corrupt regime calculations
- Traded symbol splits corrupt position sizing
- Must freeze before any calculations occur

### 2. Risk Engine Before Strategy

Risk checks run before any strategy logic:
- Kill switch takes precedence over all trading
- Panic mode liquidates longs immediately
- Prevents strategies from overriding safety controls

### 3. Scheduled Events via Callbacks

Used callback registration pattern instead of inline definitions:
```python
self.scheduler.on_pre_market_setup(self._on_pre_market_setup)
```
Benefits:
- Clean separation of scheduling and logic
- Easier testing (can fire events manually)
- Methods are self-documenting

### 4. Signal Processing Separation

Separated immediate vs EOD signal processing:
- `_process_immediate_signals()` → `MarketOrder()`
- `_process_eod_signals()` → `MarketOnOpenOrder()`

This matches the `Urgency` enum in `TargetWeight` and ensures correct order types.

---

## Testing Results

### Unit Tests
```
======================= 710 passed, 25 skipped in 2.04s ========================
```

### Skipped Tests
The 25 skipped tests are scenario/integration tests that require:
- Full end-to-end simulation
- Mock QuantConnect environment
- These are marked for Phase 6+ completion

### Architecture Tests Passed
- Engines don't import other strategy engines
- Only PortfolioRouter places orders
- No `print()`, `sleep()`, or `datetime.now()` calls

---

## File Structure

```
main.py
├── Imports (lines 1-32)
│   └── Engines, Infrastructure, Models, Config
│
├── Class AlphaNextGen (lines 34-1332)
│   ├── Docstring (lines 34-94)
│   ├── Type Hints (lines 96-143)
│   ├── Initialize() (lines 145-229)
│   ├── OnData() (lines 231-304)
│   │
│   ├── SETUP HELPERS (lines 306-428)
│   │   ├── _add_securities()
│   │   ├── _setup_indicators()
│   │   ├── _initialize_engines()
│   │   ├── _initialize_infrastructure()
│   │   └── _setup_schedules()
│   │
│   ├── ONDATA HELPERS (lines 430-556)
│   │   ├── _check_splits()
│   │   └── _update_rolling_windows()
│   │
│   ├── SCHEDULED EVENT HANDLERS (lines 558-790)
│   │   ├── _on_pre_market_setup()
│   │   ├── _on_moo_fallback()
│   │   ├── _on_sod_baseline()
│   │   ├── _on_warm_entry_check()
│   │   ├── _on_time_guard_start()
│   │   ├── _on_time_guard_end()
│   │   ├── _on_mr_force_close()
│   │   ├── _on_eod_processing()
│   │   ├── _on_market_close()
│   │   └── _on_weekly_reset()
│   │
│   ├── ORDER EVENT HANDLER (lines 792-854)
│   │   └── OnOrderEvent()
│   │
│   ├── STATE MANAGEMENT HELPERS (lines 856-920)
│   │   ├── _load_state()
│   │   ├── _save_state()
│   │   └── _reconcile_positions()
│   │
│   ├── SIGNAL PROCESSING HELPERS (lines 922-1034)
│   │   ├── _process_immediate_signals()
│   │   ├── _process_eod_signals()
│   │   ├── _generate_trend_signals_eod()
│   │   ├── _generate_hedge_signals()
│   │   └── _generate_yield_signals()
│   │
│   └── UTILITY HELPERS (lines 1036-1332)
│       ├── _run_risk_checks()
│       ├── _handle_kill_switch()
│       ├── _handle_panic_mode()
│       ├── _scan_mr_signals()
│       ├── _check_mr_exits()
│       ├── _monitor_trend_stops()
│       ├── _has_leveraged_position()
│       ├── _calculate_regime()
│       ├── _log_daily_summary()
│       ├── _on_fill()
│       └── _get_volume_ratio()
```

---

## Next Steps

1. **Scenario Tests**: Enable and implement the 25 skipped scenario tests
2. **Backtesting**: Run full backtest on QuantConnect (2020-2024)
3. **Paper Trading**: Deploy to paper trading environment
4. **Documentation**: Update WORKBOARD.md with Phase 6 completion

---

## References

- [CLAUDE.md](../CLAUDE.md) - Component map and authority rules
- [docs/14-daily-operations.md](14-daily-operations.md) - Daily timeline specification
- [docs/12-risk-engine.md](12-risk-engine.md) - Risk safeguards specification
- [docs/11-portfolio-router.md](11-portfolio-router.md) - Order authority rules
