# Alpha NextGen V2 Implementation Roadmap

## Overview

This document outlines the implementation plan for V2 based on V2.1 specifications.
Forked from V1 v1.0.0 with Core-Satellite architecture in place.

**Target Performance**: 18-25% annual return (vs V1's 10-12%)
**Timeline**: 8-12 weeks
**Source Specs**: `docs/v2-specs/`
**Current Version**: V2.1.1 (Options Engine Redesign - Jan 28, 2026)

---

## Architecture Summary

```
V2.1.1 Engine Allocation:
├── Core (70%)
│   └── Trend Engine - MA200 + ADX confirmation
├── Satellite (20%)
│   └── Options Engine - Dual-Mode Architecture (V2.1.1)
│       ├── Swing Mode (15%) - Debit spreads, credit spreads, ITM long
│       └── Intraday Mode (5%) - Micro Regime Engine (21 regimes)
└── Satellite (0-10%)
    └── Mean Reversion Engine - RSI + VIX filter
```

---

## Epic 0: Options Engine V2.1.1 Redesign (NEW)

**Priority**: HIGH
**Allocation**: 20% of portfolio (Swing 15% + Intraday 5%)
**Status**: ✅ COMPLETE (v2.1.1 - Jan 28, 2026)

### Overview

Complete redesign of Options Engine with dual-mode architecture and Micro Regime Engine for intraday trading.

### Key Deliverables

| Component | Description | Status |
|-----------|-------------|--------|
| Dual-Mode Architecture | Swing (5-45 DTE) + Intraday (0-2 DTE) | ✅ Designed |
| Micro Regime Engine | VIX Level × VIX Direction = 21 trading regimes | ✅ Designed |
| VIX Direction Classification | 7 categories (FALLING_FAST to WHIPSAW) | ✅ Designed |
| Tiered VIX Monitoring | 5min/15min/30min/60min layers | ✅ Designed |
| VIX1D Evaluation | Rejected (0.95 correlation during trading hours) | ✅ Complete |
| Documentation | V2_1_OPTIONS_ENGINE_DESIGN.txt (2,135 lines) | ✅ Complete |

### Micro Regime Matrix (21 Regimes)

```
VIX Level × VIX Direction:
- VIX Levels: LOW (<20), MEDIUM (20-30), HIGH (>30)
- VIX Directions: FALLING_FAST, FALLING, STABLE, RISING, RISING_FAST, SPIKING, WHIPSAW
- Result: 21 distinct trading regimes with specific strategies
```

### Swing Mode Strategies (5-45 DTE)
- Debit Spreads (10-14 DTE) - Defined risk
- Credit Spreads (18-21 DTE) - Premium collection
- ITM Long Options (14-21 DTE) - Directional
- Protective Puts (35-45 DTE) - Insurance

### Intraday Mode Strategies (0-2 DTE)
- Long Calls (VIX falling, momentum up)
- Long Puts (VIX rising, momentum down)
- Iron Condors (VIX stable or whipsaw)

**Spec Reference**: `docs/v2-specs/V2_1_OPTIONS_ENGINE_DESIGN.txt`

---

## Epic 1: Options Engine (Original - Superseded by Epic 0)

**Priority**: HIGH
**Allocation**: 20-30% of portfolio
**Status**: ✅ COMPLETE (v2.1.2) - Superseded by V2.1.1 Redesign

### Tickets

#### OPT-1: Options Engine Core
**Description**: Implement 4-factor entry scoring system for QQQ options
**Acceptance Criteria**:
- [x] Entry Score calculation: ADX + Momentum + IV + Liquidity
- [x] Score threshold >= 3.0 for entry
- [x] Position sizing: 1% risk per trade (inverse stop/size relationship)
- [x] +50% profit target (mathematically optimal)
- [x] 2:30 PM late-day constraint (only 20% stops after 2:30)

**Dependencies**: None
**Spec Reference**: V2_1_COMPLETE_ARCHITECTURE.txt (Part 2, Engine 3)

#### OPT-2: Entry Score Model
**Description**: Implement 4-factor scoring
**Acceptance Criteria**:
- [x] ADX factor (25 threshold, higher = stronger trend)
- [x] Momentum factor (above 200 SMA)
- [x] IV Rank factor (IV percentile 20-80 optimal)
- [x] Liquidity factor (bid-ask spread check)
- [x] Total score range: 0-4, threshold >= 3.0

**Dependencies**: OPT-1

#### OPT-3: OCO Order Management
**Description**: Implement One-Cancels-Other order pairs
**Acceptance Criteria**:
- [x] Atomic execution (both legs submitted together)
- [x] Stop and profit target linked
- [x] No "ghost orders" after one fills
- [x] Error handling for partial fills

**Dependencies**: OPT-1
**Spec Reference**: V2-1-FINAL-SYNTHESIS.md (Modification #3)

#### OPT-4: Options Engine Tests
**Description**: TDD test suite for options engine
**Acceptance Criteria**:
- [x] Entry score calculation tests
- [x] Position sizing inverse relationship tests
- [x] OCO order atomicity tests
- [x] 2:30 PM constraint tests
- [x] Integration tests with Portfolio Router

**Dependencies**: OPT-1, OPT-2, OPT-3

---

## Epic 2: Enhanced Trend Engine

**Priority**: HIGH
**Allocation**: 70% of portfolio
**Status**: ✅ COMPLETE (v2.1.0)

### Tickets

#### TRE-1: MA200 + ADX Signal
**Description**: Replace BB compression with MA200 + ADX confirmation
**Acceptance Criteria**:
- [x] Entry: Price > MA200 AND ADX > 25
- [x] Exit: Price < MA200 OR ADX < 20
- [x] Kelly position sizing (based on win rate)
- [x] Volatility targeting (15% annual vol)

**Dependencies**: None
**Spec Reference**: V2_1_COMPLETE_ARCHITECTURE.txt (Part 2, Engine 1)

#### TRE-2: Trailing Stop Enhancement
**Description**: Chandelier stop with ATR multiplier tiers
**Acceptance Criteria**:
- [x] 3.0× ATR for profit 0-10%
- [x] 2.5× ATR for profit 10-20%
- [x] 2.0× ATR for profit 20%+
- [x] Stop never moves down rule

**Dependencies**: TRE-1

#### TRE-3: Trend Engine V2 Tests
**Description**: TDD test suite for enhanced trend engine
**Acceptance Criteria**:
- [x] MA200 + ADX entry tests
- [x] ADX strength threshold tests
- [x] Kelly sizing tests
- [x] Trailing stop tier tests

**Dependencies**: TRE-1, TRE-2

---

## Epic 3: Mean Reversion VIX Filter

**Priority**: HIGH
**Allocation**: 0-10% of portfolio
**Status**: ✅ COMPLETE (v2.1.0)

### Tickets

#### MRE-1: VIX Regime Classification
**Description**: Implement VIX-based regime filter to prevent catching falling knives
**Acceptance Criteria**:
- [x] VIX < 20: NORMAL → 10% allocation
- [x] VIX 20-30: CAUTION → 5% allocation
- [x] VIX 30-40: HIGH_RISK → 2% allocation
- [x] VIX > 40: CRASH → 0% allocation (DISABLED)

**Dependencies**: None
**Spec Reference**: V2-1-Critical-Fixes-Guide.md (Fix #2)

#### MRE-2: Regime-Adjusted Parameters
**Description**: Adjust entry thresholds by VIX regime
**Acceptance Criteria**:
- [x] RSI threshold: 30 (normal), 25 (caution), 20 (high risk)
- [x] Bollinger width: 2.0σ (normal), 2.2σ (caution), 2.5σ (high risk)
- [x] Stop loss: 8% (normal), 6% (caution), 4% (high risk)

**Dependencies**: MRE-1

#### MRE-3: VIX Data Feed
**Description**: Add ^VIX data feed integration
**Acceptance Criteria**:
- [x] Daily VIX data from CBOE
- [x] Validate range (normal 10-20, elevated 20-30, crisis 30+)
- [x] Store in state for persistence

**Dependencies**: None

#### MRE-4: Mean Reversion V2 Tests
**Description**: TDD test suite for VIX filter
**Acceptance Criteria**:
- [x] VIX regime classification tests
- [x] March 2020 crash simulation (VIX 82)
- [x] Feb 2018 spike test (VIX 50)
- [x] Allocation scaling tests

**Dependencies**: MRE-1, MRE-2, MRE-3

---

## Epic 4: Yield Sleeve Enhancement

**Priority**: MEDIUM
**Allocation**: Idle cash management
**Status**: V1 Complete, V2 Enhancement Needed

### Tickets

#### YLD-1: SHV Ladder Strategy
**Description**: Implement aggressive yield on idle cash
**Acceptance Criteria**:
- [ ] $15,000 in SHV (1-month bills) @ 4.8%
- [ ] $7,500 in SHV (3-month bills) @ 4.9%
- [ ] $4,500 in VGIT (Treasury ETF) @ 4.2%
- [ ] $3,000 cash buffer @ 0.1%

**Dependencies**: None
**Spec Reference**: V2-1-Critical-Fixes-Guide.md (Fix #1)

#### YLD-2: Daily Cash Sweep
**Description**: Automate daily cash rebalancing
**Acceptance Criteria**:
- [ ] 4:00 PM daily sweep trigger
- [ ] If cash > $40k: buy SHV
- [ ] If cash < $30k: sell SHV
- [ ] Target range: $30-40k

**Dependencies**: YLD-1

#### YLD-3: Monthly Interest Harvest
**Description**: Monthly SHV position rebalancing
**Acceptance Criteria**:
- [ ] First business day of month
- [ ] Reinvest matured positions
- [ ] Log accrued interest
- [ ] Adjust if daily drain changed

**Dependencies**: YLD-1, YLD-2

#### YLD-4: Yield Sleeve V2 Tests
**Description**: TDD test suite for SHV ladder
**Acceptance Criteria**:
- [ ] Ladder allocation tests
- [ ] Daily sweep logic tests
- [ ] Interest calculation tests

**Dependencies**: YLD-1, YLD-2, YLD-3

---

## Epic 5: Risk Engine V2

**Priority**: HIGH
**Allocation**: N/A (controls all engines)
**Status**: V1 Complete, V2 Enhancement Needed

### Tickets

#### RSK-1: 5-Level Circuit Breaker
**Description**: Implement V2.1 circuit breaker system
**Acceptance Criteria**:
- [ ] Level 1: Daily loss -2% → reduce sizing 50%
- [ ] Level 2: Weekly loss -5% → reduce sizing 50%
- [ ] Level 3: Portfolio vol > 1.5% → block new entries
- [ ] Level 4: Correlation > 0.60 → reduce exposure
- [ ] Level 5: Greeks breach → close options positions

**Dependencies**: None
**Spec Reference**: V2_1_COMPLETE_ARCHITECTURE.txt (Part 5)

#### RSK-2: Greeks Monitoring (Options)
**Description**: Monitor options Greeks for risk
**Acceptance Criteria**:
- [ ] Delta exposure tracking
- [ ] Gamma risk on expiration approach
- [ ] Vega exposure to IV changes
- [ ] Theta decay monitoring

**Dependencies**: OPT-1, RSK-1

#### RSK-3: Risk Engine V2 Tests
**Description**: TDD test suite for 5-level breakers
**Acceptance Criteria**:
- [ ] Each circuit breaker trigger test
- [ ] Cascade behavior tests
- [ ] Reset condition tests
- [ ] Greeks breach tests

**Dependencies**: RSK-1, RSK-2

---

## Epic 6: Portfolio Orchestrator

**Priority**: HIGH
**Allocation**: N/A (coordinates engines)
**Status**: Not Started

### Tickets

#### ORC-1: Signal Aggregation
**Description**: Blend signals from all three engines
**Acceptance Criteria**:
- [ ] Weight signals by engine allocation (70/20-30/0-10)
- [ ] Detect correlation conflicts
- [ ] Apply volatility targeting
- [ ] Generate final target weights

**Dependencies**: OPT-1, TRE-1, MRE-1

#### ORC-2: Rebalancing Logic
**Description**: Trigger rebalancing based on drift
**Acceptance Criteria**:
- [ ] Rebalance if drift > 5%
- [ ] Respect circuit breaker state
- [ ] EOD rebalancing preferred
- [ ] Emergency intraday if needed

**Dependencies**: ORC-1, RSK-1

#### ORC-3: Crash Detection
**Description**: Detect market crashes for defensive action
**Acceptance Criteria**:
- [ ] SPY -4% intraday → liquidate longs
- [ ] VIX > 40 → disable MR engine
- [ ] Correlation spike → reduce exposure

**Dependencies**: ORC-1, MRE-1

#### ORC-4: Orchestrator Tests
**Description**: TDD test suite for orchestrator
**Acceptance Criteria**:
- [ ] Signal aggregation tests
- [ ] Allocation enforcement tests
- [ ] Crash detection tests
- [ ] Rebalancing trigger tests

**Dependencies**: ORC-1, ORC-2, ORC-3

---

## Implementation Schedule

### Phase 1: Foundation (Weeks 1-2)
- [ ] TRE-1: MA200 + ADX Signal
- [ ] TRE-2: Trailing Stop Enhancement
- [ ] TRE-3: Trend Engine V2 Tests
- [ ] RSK-1: 5-Level Circuit Breaker
- [ ] RSK-3: Risk Engine V2 Tests

**Milestone**: Trend-only trading capability (Week 2)

### Phase 2: Mean Reversion Enhancement (Weeks 3-4)
- [ ] MRE-3: VIX Data Feed
- [ ] MRE-1: VIX Regime Classification
- [ ] MRE-2: Regime-Adjusted Parameters
- [ ] MRE-4: Mean Reversion V2 Tests

**Milestone**: Two-engine system (Week 4)

### Phase 3: Options Engine (Weeks 5-7)
- [ ] OPT-1: Options Engine Core
- [ ] OPT-2: Entry Score Model
- [ ] OPT-3: OCO Order Management
- [ ] OPT-4: Options Engine Tests
- [ ] RSK-2: Greeks Monitoring

**Milestone**: Full three-engine system (Week 7)

### Phase 4: Orchestration & Yield (Week 8)
- [ ] ORC-1: Signal Aggregation
- [ ] ORC-2: Rebalancing Logic
- [ ] ORC-3: Crash Detection
- [ ] ORC-4: Orchestrator Tests
- [ ] YLD-1: SHV Ladder Strategy
- [ ] YLD-2: Daily Cash Sweep
- [ ] YLD-3: Monthly Interest Harvest
- [ ] YLD-4: Yield Sleeve V2 Tests

**Milestone**: Production-ready V2 (Week 8)

### Phase 5: Backtesting & Validation (Weeks 9-10)
- [ ] 10-year backtest (2015-2024)
- [ ] Crisis period validation (2020, 2018, 2022)
- [ ] Paper trading (2 weeks)

### Phase 6: Production Deployment (Weeks 11-12)
- [ ] Small size deployment ($10-20k)
- [ ] Full deployment ($50k+)
- [ ] Monitoring dashboard setup

---

## Definition of Done

For each ticket:
1. [ ] Implementation complete
2. [ ] Unit tests written (TDD mandate)
3. [ ] Tests passing
4. [ ] Code reviewed
5. [ ] Documentation updated
6. [ ] Backtest validated (if applicable)

---

## Risk Register

| Risk | Mitigation |
|------|------------|
| Options complexity | Start with simple 0DTE/1DTE calls only |
| VIX data latency | Use daily close, not intraday |
| OCO broker support | Verify IB supports OCO before implementing |
| Backtest overfitting | Use walk-forward validation |

---

## References

- `docs/v2-specs/V2_1_COMPLETE_ARCHITECTURE.txt` - Master specification
- `docs/v2-specs/V2_1_OPTIONS_ENGINE_DESIGN.txt` - Options Engine V2.1.1 (Dual-Mode + Micro Regime)
- `docs/v2-specs/V2-1-Critical-Fixes-Guide.md` - Critical fixes (yield, VIX)
- `docs/v2-specs/V2-1-FINAL-SYNTHESIS.md` - Mathematical proofs
- `docs/v2-specs/V2_1_QUICK_REFERENCE.txt` - Quick reference guide
- `docs/v2-specs/V2_1_CRITICAL_MODIFICATIONS.txt` - 4 critical modifications

---

*Generated: 2026-01-26*
*Updated: 2026-01-28 (V2.1.1 Options Engine Redesign)*
*Version: V2.1.1*
