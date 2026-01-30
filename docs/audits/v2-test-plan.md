# V2 Comprehensive Test Plan

> **Version:** 2.1.3
> **Last Updated:** 27 January 2026
> **Status:** ✅ Complete - 990 tests passing

---

## Executive Summary

This document defines the comprehensive test strategy for Alpha NextGen V2, with particular emphasis on:
- **Options Engine integration** (new in V2.1)
- **Multi-engine conflict detection** (critical for live trading)
- **End-to-end workflow validation** (scenario tests)
- **Crisis period simulation** (risk validation)

---

## Test Pyramid

```
                    ┌─────────────────┐
                    │   E2E/Backtest  │  ← 10-year backtest, crisis periods
                    │    (Manual)     │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │   Integration   │  ← 17 tests: Multi-engine, Options+OCO
                    │     Tests       │
                    └────────┬────────┘
                             │
              ┌──────────────▼──────────────┐
              │      Scenario Tests         │  ← 25 tests: Full trading days
              │   (Kill switch, Panic, etc) │
              └──────────────┬──────────────┘
                             │
        ┌────────────────────▼────────────────────┐
        │           Unit Tests (946 tests)        │  ← Individual engine logic
        └────────────────────┬────────────────────┘
                             │
    ┌────────────────────────▼────────────────────────┐
    │        Architecture & Contract Tests            │  ← Static analysis, schemas
    └─────────────────────────────────────────────────┘

TOTAL: 990 tests passing (v2.1.3)
```

---

## Test Categories

### 1. Architecture Tests (Existing - PHASE 3 CI)

**Purpose:** Enforce structural rules via static analysis

| Test | File | Description |
|------|------|-------------|
| Hub-and-Spoke | `test_architecture_boundaries.py` | Engines cannot import other strategy engines |
| Order Authority | `test_architecture_boundaries.py` | Only PortfolioRouter places orders |
| QC Compliance | `test_architecture_boundaries.py` | No print(), sleep(), datetime.now() |

**Status:** ✅ Complete (454 lines)

---

### 2. Contract Tests (Existing - PHASE 3 CI)

**Purpose:** Schema stability and serialization correctness

| Test | File | Description |
|------|------|-------------|
| TargetWeight Schema | `test_target_weight_contract.py` | Golden payload matching |
| Round-trip Serialization | `test_target_weight_contract.py` | to_dict/from_dict |

**Status:** ✅ Complete (258 lines)

---

### 3. Unit Tests (Existing - PHASE 4 CI)

**Purpose:** Individual engine logic verification

| Engine | File | Tests | Lines |
|--------|------|:-----:|:-----:|
| Trend Engine | `test_trend_engine.py` | 66 | 1,117 |
| Risk Engine | `test_risk_engine.py` | ~60 | 1,117 |
| Options Engine | `test_options_engine.py` | 66 | 916 |
| Mean Reversion | `test_mean_reversion_engine.py` | ~50 | 909 |
| Portfolio Router | `test_portfolio_router.py` | ~45 | 753 |
| Execution Engine | `test_execution_engine.py` | ~40 | 692 |
| Cold Start | `test_cold_start_engine.py` | ~35 | 666 |
| State Persistence | `test_state_persistence.py` | ~35 | 656 |
| Yield Sleeve | `test_yield_sleeve.py` | ~30 | 608 |
| Hedge Engine | `test_hedge_engine.py` | ~30 | 569 |
| Exposure Groups | `test_exposure_groups.py` | ~30 | 531 |
| Daily Scheduler | `test_daily_scheduler.py` | ~25 | 528 |
| Regime Engine | `test_regime_engine.py` | ~25 | 478 |
| OCO Manager | `test_oco_manager.py` | ~25 | 476 |
| Capital Engine | `test_capital_engine.py` | ~25 | 472 |
| VIX Regime | `test_vix_regime.py` | ~25 | 453 |

**Status:** ✅ Complete (907 tests, ~13,116 lines)

---

### 4. Integration Tests (NEW - Priority)

**Purpose:** Verify multiple components work together without conflicts

#### 4.1 Options Integration Tests

| Test ID | Test Name | Components | Description |
|---------|-----------|------------|-------------|
| OPT-INT-1 | Options + OCO Lifecycle | OptionsEngine, OCOManager, ExecutionEngine | Entry creates OCO pair, fill cancels other leg |
| OPT-INT-2 | Options + Risk Greeks | OptionsEngine, RiskEngine | Greeks exceed limits → position reduction |
| OPT-INT-3 | Options + Portfolio Router | OptionsEngine, PortfolioRouter | Options TargetWeight routed correctly |
| OPT-INT-4 | Options + State Persistence | OptionsEngine, OCOManager, StateManager | Restart recovery with open options |
| OPT-INT-5 | Options Time Constraints | OptionsEngine, DailyScheduler | Late day stop tightening, force close |

#### 4.2 Multi-Engine Conflict Tests

| Test ID | Test Name | Components | Description |
|---------|-----------|------------|-------------|
| CONF-1 | Trend + MR Same Direction | TrendEngine, MREngine, Router | Both want NASDAQ exposure → aggregation |
| CONF-2 | Trend + Options Allocation | TrendEngine, OptionsEngine, Router | 70% + 25% → reduced to fit limits |
| CONF-3 | MR + Options Intraday | MREngine, OptionsEngine | Both IMMEDIATE urgency → priority |
| CONF-4 | Hedge vs Long Conflict | HedgeEngine, TrendEngine, Router | Hedge PSQ vs Long QLD netting |
| CONF-5 | Kill Switch All Engines | RiskEngine, All Engines | Kill switch overrides all signals |
| CONF-6 | VIX Crash + Options | MREngine, OptionsEngine | VIX > 40 disables both |

#### 4.3 State Recovery Tests

| Test ID | Test Name | Components | Description |
|---------|-----------|------------|-------------|
| STATE-1 | Mid-Day Restart | StateManager, All Engines | Restart at 11:30, recover positions |
| STATE-2 | OCO Reconnection | OCOManager, StateManager | Reconnect to live OCO orders |
| STATE-3 | Regime State Recovery | RegimeEngine, StateManager | Load yesterday's regime score |
| STATE-4 | Position Reconciliation | PositionManager, StateManager | Broker positions vs local state |

---

### 5. Scenario Tests (Implement - PHASE 5 CI)

**Purpose:** End-to-end workflow validation

#### 5.1 Risk Scenarios

| Scenario | File | Description | Status |
|----------|------|-------------|--------|
| Kill Switch | `test_kill_switch_scenario.py` | -3% loss → full liquidation | ⏳ Implement |
| Panic Mode | `test_panic_mode_scenario.py` | SPY -4% → liquidate longs only | ⏳ Implement |
| Weekly Breaker | `test_weekly_breaker_scenario.py` | -5% WTD → 50% sizing | ⏳ Implement |
| VIX Crash | `test_vix_crash_scenario.py` | VIX > 40 → disable MR + Options | ⏳ Implement |

#### 5.2 Trading Day Scenarios

| Scenario | File | Description | Status |
|----------|------|-------------|--------|
| Full Day | `test_full_cycle_scenario.py` | Pre-market → Close complete flow | ⏳ Implement |
| Cold Start | `test_cold_start_scenario.py` | Days 1-5 warm entry sequence | ⏳ Implement |
| Gap Down Day | `test_gap_down_scenario.py` | SPY -2% gap → entries blocked | ⏳ New |
| Options Day | `test_options_day_scenario.py` | Options entry → OCO → exit | ⏳ New |

#### 5.3 Multi-Day Scenarios

| Scenario | File | Description | Status |
|----------|------|-------------|--------|
| Phase Transition | `test_phase_transition_scenario.py` | SEED → GROWTH after 5 days | ⏳ New |
| Lockbox Milestone | `test_lockbox_scenario.py` | Hit $100K → 10% locked | ⏳ New |

---

### 6. Crisis Period Tests (Manual/Backtest)

**Purpose:** Validate system behavior in historical crises

| Crisis | Period | VIX Peak | Expected Behavior |
|--------|--------|:--------:|-------------------|
| COVID Crash | Mar 2020 | 82.69 | MR disabled, Options disabled, Hedges active |
| VIX Spike | Feb 2018 | 50.30 | MR reduced, Options tight stops |
| 2022 Bear | Jan-Oct 2022 | 36.45 | Regime DEFENSIVE/RISK_OFF, Hedges 20% |
| Flash Crash | Aug 2015 | 40.74 | Kill switch protection |

---

## Integration Test Implementation

### Test File Structure

```
tests/
├── integration/                         # NEW: Integration tests
│   ├── __init__.py
│   ├── test_options_integration.py      # Options + OCO + Execution
│   ├── test_multi_engine_conflict.py    # Engine conflict detection
│   ├── test_state_recovery.py           # Restart recovery
│   └── test_signal_aggregation.py       # Router aggregation
├── scenarios/                           # Existing: Workflow tests
│   ├── test_kill_switch_scenario.py     # IMPLEMENT
│   ├── test_panic_mode_scenario.py      # IMPLEMENT
│   ├── test_cold_start_scenario.py      # IMPLEMENT
│   ├── test_full_cycle_scenario.py      # IMPLEMENT
│   ├── test_options_day_scenario.py     # NEW
│   ├── test_vix_crash_scenario.py       # NEW
│   └── test_gap_down_scenario.py        # NEW
└── ... (existing unit tests)
```

---

## CI Pipeline Updates

### Current Pipeline Phases

| Phase | Name | Tests |
|:-----:|------|-------|
| 1 | Config Validation | `scripts/validate_config.py` |
| 2 | Code Quality | Black, mypy |
| 3 | Architecture | `test_architecture_boundaries.py`, `test_target_weight_contract.py`, `test_smoke_integration.py` |
| 4 | Unit Tests | All except scenarios |
| 5 | Scenario Tests | `tests/scenarios/` |
| 6 | Coverage Report | Informational |
| 7 | Doc Parity | `scripts/check_spec_parity.py` |

### Proposed Additions

| Phase | Name | Tests | Priority |
|:-----:|------|-------|----------|
| 4.5 | **Integration Tests** | `tests/integration/` | **HIGH** |
| 5.5 | **Options Scenario** | Options-specific workflows | **HIGH** |

---

## Test Data Requirements

### Mock Data Fixtures

```python
# conftest.py additions

@pytest.fixture
def market_data_normal():
    """Normal market conditions: VIX=15, SPY trending up."""
    return {
        "vix": 15.0,
        "spy_change": 0.005,  # +0.5%
        "regime_score": 65,
        "adx": 28,
    }

@pytest.fixture
def market_data_crisis():
    """Crisis conditions: VIX=45, SPY crashing."""
    return {
        "vix": 45.0,
        "spy_change": -0.04,  # -4%
        "regime_score": 22,
        "adx": 35,
    }

@pytest.fixture
def portfolio_with_options(mock_algorithm):
    """Portfolio with active options position and OCO pair."""
    # Setup QQQ call position
    # Setup OCO pair (stop + profit)
    return mock_algorithm
```

---

## Test Execution Order

```
1. Architecture Tests (fail-fast, <5 sec)
   ↓
2. Contract Tests (fail-fast, <5 sec)
   ↓
3. Smoke Integration (fail-fast, <1 sec)
   ↓
4. Unit Tests (parallel, ~30 sec)
   ↓
5. Integration Tests (sequential, ~60 sec)  ← NEW
   ↓
6. Scenario Tests (sequential, ~120 sec)
   ↓
7. Coverage Report (informational)
```

---

## Success Criteria

### For V2.1 Release

| Criterion | Target | Current |
|-----------|:------:|:-------:|
| Unit test pass rate | 100% | 100% |
| Integration test pass rate | 100% | N/A (new) |
| Scenario test pass rate | 100% | N/A (skipped) |
| Code coverage (engines/) | >70% | ~65% |
| Architecture violations | 0 | 0 |
| Contract test failures | 0 | 0 |

### For Production

| Criterion | Target |
|-----------|:------:|
| 10-year backtest Sharpe | >1.0 |
| Max drawdown | <25% |
| Crisis period survival | No kill switch in 2020 |
| Paper trading (2 weeks) | No critical bugs |

---

## Implementation Priority

### Phase 1: Integration Tests (Immediate)

1. `test_options_integration.py` - Options lifecycle with OCO
2. `test_multi_engine_conflict.py` - Engine conflict detection
3. `test_state_recovery.py` - Restart recovery

### Phase 2: Scenario Tests (This Week)

1. Implement skipped scenarios (kill switch, panic, cold start)
2. Add options day scenario
3. Add VIX crash scenario

### Phase 3: Crisis Validation (Next Week)

1. Run 10-year backtest
2. Validate 2020 COVID crash
3. Validate 2018 VIX spike
4. Validate 2022 bear market

---

## Appendix: Test Naming Convention

```
test_<component>_<action>_<condition>_<expected>

Examples:
- test_options_entry_score_above_threshold_creates_signal
- test_router_aggregate_conflicting_weights_nets_correctly
- test_oco_stop_fills_cancels_profit_leg
- test_kill_switch_triggered_liquidates_all_positions
```

---

*Next: Implementation of integration tests*
