# Section 1: Executive Summary

[Table of Contents](00-table-of-contents.md) | [System Architecture →](02-system-architecture.md)

---

## Project Overview

**Alpha NextGen** is an algorithmic trading system designed to generate consistent returns through systematic trading of leveraged ETFs. The system combines multiple strategies, adaptive regime detection, and comprehensive risk management to navigate varying market conditions.

| Attribute | Value |
|-----------|-------|
| **Platform** | QuantConnect (LEAN Engine) |
| **Broker** | Interactive Brokers |
| **Asset Class** | US Equity ETFs (Leveraged and Inverse) |
| **Initial Capital** | $50,000 |
| **Target Capital** | $50,000 - $500,000 |
| **Trading Hours** | US Market Hours (09:30 - 16:00 ET) |

---

## Investment Thesis

### Core Beliefs

1. **Markets have regimes** - Bull, bear, and transitional periods require different approaches
2. **Leveraged ETFs reward precision** - Daily rebalancing creates opportunities and risks
3. **Mean reversion works intraday** - Oversold bounces in volatile instruments are tradeable
4. **Trends persist** - Compression breakouts lead to extended moves
5. **Risk management is paramount** - Survival enables compounding

### Strategy Summary

| Strategy | Thesis | Holding Period |
|----------|--------|:---------------|
| **Trend** | Volatility compression precedes breakouts | Days to weeks |
| **Mean Reversion** | Extreme oversold conditions revert quickly | Minutes to hours |
| **Hedge** | Tail risk protection preserves capital | As needed |
| **Yield** | Idle cash should earn return | Ongoing |

---

## Design Philosophy

### Principle 1: Separation of Concerns

Each component has ONE job:
- Regime Engine → Detect market state
- Capital Engine → Manage account phases
- Risk Engine → Protect capital
- Strategies → Generate signals
- Router → Coordinate execution

### Principle 2: Defense in Depth

Multiple layers of protection:
- Strategy-level stops
- Portfolio-level circuit breakers
- Regime-based exposure reduction
- Position and group limits

### Principle 3: Clarity Over Cleverness

- Simple, testable rules
- No black-box machine learning
- Explicit parameters with clear rationale
- Comprehensive logging

### Principle 4: Graceful Degradation

When conditions deteriorate:
- Reduce exposure progressively
- Add hedges incrementally
- Preserve capital for recovery

---

## Key Constraints

### Hard Constraints (Non-Negotiable)

| Constraint | Rationale |
|------------|-----------|
| No 3x overnight holds | Decay risk too high; volatility drag destroys returns |
| Maximum 3% daily loss | Capital preservation; live to trade another day |
| No trading during Fed window | 13:55-14:10 volatility is unpredictable |
| No entries on gap days | -1.5% SPY gap indicates elevated risk |
| Minimum 5 days before full strategies | Cold start allows system calibration |

### Soft Constraints (Configurable)

| Constraint | Default | Range |
|------------|:-------:|:-----:|
| Target volatility | 20% | 15-25% |
| Maximum single position | 40-50% | 30-60% |
| Regime smoothing alpha | 0.3 | 0.2-0.5 |
| MR RSI threshold | 25 | 20-30 |
| Trend BB compression | 0.10 | 0.08-0.12 |

---

## Target Performance

### Goals

| Metric | Target | Rationale |
|--------|:------:|-----------|
| **Annual Return** | 25-40% | Leveraged ETFs with risk management |
| **Maximum Drawdown** | < 20% | Preserve capital for compounding |
| **Sharpe Ratio** | > 1.0 | Risk-adjusted returns matter |
| **Win Rate** | > 55% | Modest edge with positive expectancy |
| **Recovery Time** | < 60 days | Bounce back from drawdowns |

### Non-Goals

| What We Avoid | Why |
|---------------|-----|
| Maximizing absolute returns | Risk of ruin too high |
| High-frequency trading | Infrastructure complexity |
| Options strategies | Platform limitations, complexity |
| Overnight 3x positions | Decay and gap risk |

---

## Risk Management Summary

### Circuit Breakers

| Control | Trigger | Severity |
|---------|---------|:--------:|
| **Kill Switch** | -3% daily loss | 🔴 Critical |
| **Panic Mode** | SPY -4% intraday | 🔴 Critical |
| **Weekly Breaker** | -5% week-to-date | 🟠 High |
| **Gap Filter** | SPY gaps -1.5% | 🟡 Medium |
| **Vol Shock** | SPY bar > 3×ATR | 🟡 Medium |
| **Time Guard** | 13:55-14:10 daily | 🟡 Medium |

### Exposure Limits

| Group | Max Net | Max Gross |
|-------|:-------:|:---------:|
| NASDAQ_BETA (TQQQ, QLD, SOXL, PSQ) | 50% | 75% |
| SPY_BETA (SSO) | 40% | 40% |
| SMALL_CAP_BETA (TNA) | 25% | 25% |
| FINANCIALS_BETA (FAS) | 15% | 15% |
| RATES (TMF, SHV) | 40% | 40% |

---

## Account Phases

The system adapts its risk parameters based on account size:

| Phase | Equity Range | Max Position | Kill Switch |
|-------|:------------:|:------------:|:-----------:|
| **SEED** | $50k - $100k | 50% | 3% |
| **GROWTH** | $100k - $500k | 40% | 3% |

### Virtual Lockbox

Profit protection mechanism:
- At $100k: Lock 10% of equity (never risk again)
- At $200k: Lock additional 10% of equity
- Locked capital invested in SHV (earning yield)
- Excluded from tradeable equity calculations

---

## System Components

### Core Engines (Always Active)

| Engine | Purpose | Update Frequency |
|--------|---------|:----------------:|
| **Regime Engine** | Market state detection | Daily (EOD) |
| **Capital Engine** | Phase and limit management | Daily (EOD) |
| **Risk Engine** | Circuit breakers and safeguards | Continuous |

### Strategy Engines (Conditionally Active)

| Strategy | Instruments | Allocation | When Active |
|----------|-------------|:----------:|-------------|
| **Cold Start** | QLD, SSO | 25% sizing | Days 1-5 only |
| **Trend** | QLD (20%), SSO (15%), TNA (12%), FAS (8%) | 55% | After cold start, Regime ≥ 40 |
| **Options** | QQQ options | 25% | After cold start, 4-factor score ≥ 3.0 |
| **Mean Reversion** | TQQQ (5%), SOXL (5%) | 10% | After cold start, Regime ≥ 40, Intraday |
| **Hedge** | TMF, PSQ | 0-30% | Regime < 40 |
| **Yield** | SHV | Remainder | Always (for idle cash) |

### Coordination Layer

| Component | Purpose |
|-----------|---------|
| **Portfolio Router** | Aggregate signals, validate limits, route orders |
| **Execution Engine** | Convert signals to broker orders |
| **State Persistence** | Survive restarts |

---

## Daily Operations Overview

| Time (ET) | Phase | Key Activities |
|-----------|-------|----------------|
| 09:00 | Pre-Market | Load state, verify warmup |
| 09:25 | Pre-Market | Set equity baseline, regime preview |
| 09:30 | Market Open | Process MOO fills |
| 09:33 | Post-Open | Set SOD baseline, check gap filter |
| 10:00 | Cold Start | Warm entry check (if Days < 5) |
| 10:00-15:00 | Trading | Risk checks, MR scanning, position monitoring |
| 13:55-14:10 | Time Guard | Block all entries |
| 15:45 | EOD | Regime calc, signals, submit MOOs |
| 16:00 | Close | Save state, increment days |

---

## Critical Success Factors

### Must Have

| Factor | Why Critical |
|--------|--------------|
| **Kill switch works perfectly** | Prevents catastrophic loss |
| **No 3x overnight** | Eliminates decay risk |
| **State persists** | Survives restarts without confusion |
| **Regime adapts** | Different behavior for different markets |
| **Logging comprehensive** | Debug issues, verify behavior |

### Should Have

| Factor | Why Important |
|--------|---------------|
| Cold start smooth deployment | Safe first week |
| Chandelier stops trail properly | Capture trend profits |
| Hedge scaling gradual | No whipsaw on hedges |
| SHV for idle cash | Every dollar earns |

### Nice to Have

| Factor | Why Desired |
|--------|-------------|
| Lockbox milestones | Psychological profit protection |
| Weekly breaker | Extra safety layer |
| Split detection | Avoid corporate action confusion |

---

## Key Decisions Made

| Decision | Alternatives Considered | Why This Choice |
|----------|------------------------|-----------------|
| **No options trading** | Vol Harvest strategy | Platform complexity, keeps system simpler |
| **Static exposure groups** | Rolling correlation | Easier to validate, more predictable |
| **Proxy symbols for regime** | Traded symbols | Cleaner signals, no interference |
| **2x for overnight, 3x intraday** | All 3x or all 2x | Balances opportunity vs decay |
| **Single regime score** | Separate scores per strategy | Simpler coordination, consistent behavior |
| **Router hub architecture** | Direct strategy-to-broker | Central control, easier debugging |

---

## What This System Is NOT

| Not This | Why Not |
|----------|---------|
| **High-frequency trading** | No sub-second execution needed |
| **Market making** | Not providing liquidity |
| **Arbitrage** | Not exploiting price discrepancies |
| **Machine learning** | Explicit rules, no black boxes |
| **Options trading** | Platform limitations |
| **24/7 crypto trading** | Different asset class |

---

## Success Metrics

### Quantitative (Monthly Review)

- [ ] Return vs S&P 500 benchmark
- [ ] Maximum drawdown within limits
- [ ] Sharpe ratio above target
- [ ] Kill switch trigger frequency
- [ ] Strategy win rates

### Qualitative (Weekly Review)

- [ ] System behaving as expected?
- [ ] Regime transitions making sense?
- [ ] Any unexpected errors?
- [ ] State persistence working?
- [ ] Logging sufficient for debugging?

---

## Project Timeline

| Phase | Duration | Milestone |
|-------|:--------:|-----------|
| **Foundation** | Week 1 | Environment setup, minimal shell |
| **Core Engines** | Week 2 | Regime, Capital, Risk working |
| **Strategies** | Week 3 | All strategies implemented |
| **Integration** | Week 4 | Router, persistence, testing |
| **Backtesting** | Week 5 | Historical validation |
| **Paper Trading** | Weeks 6-9 | 30 days live simulation |
| **Live Trading** | Week 10+ | Real capital deployment |

---

## Document Organization

This specification is organized into 17 sections:

| Sections | Coverage |
|----------|----------|
| 01-02 | Foundation (this summary, architecture) |
| 03 | Data layer |
| 04-05, 12 | Core engines |
| 06-10 | Strategy engines |
| 11, 13 | Execution layer |
| 14-15 | Operations |
| 16-17 | Reference appendices |

Each section is self-contained with embedded diagrams and clear dependencies.

---

## Next Steps

1. ➡️ Review [System Architecture](02-system-architecture.md) for the big picture
2. ➡️ Understand [Data Infrastructure](03-data-infrastructure.md) requirements
3. ➡️ Begin implementation following the [QC Rules](../QC_RULES.md)

---

[Table of Contents](00-table-of-contents.md) | [System Architecture →](02-system-architecture.md)