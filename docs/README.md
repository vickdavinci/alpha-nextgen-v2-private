# Alpha NextGen V2.1.1 Documentation

> **Navigation Hub** - Start here to find what you need.

---

## Quick Navigation

| I want to... | Go to |
|--------------|-------|
| Understand the system architecture | [system/02-system-architecture.md](system/02-system-architecture.md) |
| Learn how a specific engine works | [system/](#system-documentation) |
| Find a configuration parameter | [system/16-appendix-parameters.md](system/16-appendix-parameters.md) |
| Run a backtest | [guides/backtest-workflow.md](guides/backtest-workflow.md) |
| Understand main.py implementation | [guides/main-py-implementation.md](guides/main-py-implementation.md) |
| Check deployment readiness | [audits/v2-1-1-readiness-audit.md](audits/v2-1-1-readiness-audit.md) |
| Read the V2.1.1 design spec | [specs/v2-1-options-engine-design.txt](specs/v2-1-options-engine-design.txt) |
| Find what docs to update after code changes | [internal/documentation-map.md](internal/documentation-map.md) |

---

## Folder Structure

```
docs/
├── README.md                 # You are here - navigation hub
│
├── system/                   # Core system documentation (20 sections)
│   ├── 00-table-of-contents.md
│   ├── 01-executive-summary.md
│   ├── 02-system-architecture.md
│   ├── 03-data-infrastructure.md
│   ├── 04-regime-engine.md        # Core: Market state detection
│   ├── 05-capital-engine.md       # Core: Position sizing
│   ├── 06-cold-start-engine.md    # Core: Days 1-5 handling
│   ├── 07-trend-engine.md         # Strategy: MA200+ADX (Core 70%)
│   ├── 08-mean-reversion-engine.md # Strategy: RSI oversold (Satellite 0-10%)
│   ├── 09-hedge-engine.md         # Strategy: TMF/PSQ overlay
│   ├── 10-yield-sleeve.md         # Strategy: SHV cash management
│   ├── 11-portfolio-router.md     # Infra: Order coordination
│   ├── 12-risk-engine.md          # Infra: Circuit breakers
│   ├── 13-execution-engine.md     # Infra: Order submission
│   ├── 14-daily-operations.md     # Ops: Timeline & schedule
│   ├── 15-state-persistence.md    # Infra: ObjectStore
│   ├── 16-appendix-parameters.md  # Reference: All config values
│   ├── 17-appendix-glossary.md    # Reference: Term definitions
│   ├── 18-options-engine.md       # Strategy: QQQ options (Satellite 20-30%)
│   └── 19-oco-manager.md          # Infra: OCO order pairs
│
├── guides/                   # How-to guides
│   ├── backtest-workflow.md       # Running backtests
│   ├── main-py-implementation.md  # Understanding main.py
│   └── github-branch-protection.md # Branch protection rules
│
├── audits/                   # Quality audits & test plans
│   ├── v2-1-readiness-audit.md    # V2.1 audit (PASSED)
│   ├── v2-1-1-readiness-audit.md  # V2.1.1 audit (NO-GO - blockers)
│   └── v2-test-plan.md            # Test strategy
│
├── specs/                    # Design specifications
│   ├── v2-1-complete-architecture.txt
│   ├── v2-1-critical-modifications.txt
│   ├── v2-1-options-engine-design.txt  # Full V2.1.1 spec
│   ├── v2-1-critical-fixes-guide.md
│   ├── v2-1-final-synthesis.md
│   ├── v2-1-delivery-summary.txt
│   ├── v2-1-quick-reference.txt
│   └── v2-1-summary.txt
│
└── internal/                 # Internal reference (for Claude/devs)
    └── documentation-map.md       # Code-to-doc mapping
```

---

## System Documentation

### Reading Order (Recommended)

**For New Readers:**
1. [01-executive-summary.md](system/01-executive-summary.md) - Goals and philosophy
2. [02-system-architecture.md](system/02-system-architecture.md) - Big picture
3. [14-daily-operations.md](system/14-daily-operations.md) - Daily timeline

**For Implementers:**
1. [03-data-infrastructure.md](system/03-data-infrastructure.md) - Data requirements
2. [04-regime-engine.md](system/04-regime-engine.md) → [12-risk-engine.md](system/12-risk-engine.md) - Core engines
3. [07-trend-engine.md](system/07-trend-engine.md) → [18-options-engine.md](system/18-options-engine.md) - Strategies

### By Category

#### Core Engines (Always Active)

| Doc | Engine | Purpose |
|-----|--------|---------|
| [04](system/04-regime-engine.md) | **Regime Engine** | 4-factor market state scoring (0-100) |
| [05](system/05-capital-engine.md) | **Capital Engine** | Phase management, lockbox, tradeable equity |
| [06](system/06-cold-start-engine.md) | **Cold Start Engine** | Days 1-5 warm entry logic |
| [12](system/12-risk-engine.md) | **Risk Engine** | Circuit breakers and safeguards |

#### Strategy Engines (Core-Satellite Architecture)

| Doc | Engine | Allocation | Instruments |
|-----|--------|:----------:|-------------|
| [07](system/07-trend-engine.md) | **Trend Engine** | 70% (Core) | QLD, SSO |
| [18](system/18-options-engine.md) | **Options Engine** | 20-30% (Satellite) | QQQ Options |
| [08](system/08-mean-reversion-engine.md) | **Mean Reversion** | 0-10% (Satellite) | TQQQ, SOXL |
| [09](system/09-hedge-engine.md) | **Hedge Engine** | Overlay | TMF, PSQ |
| [10](system/10-yield-sleeve.md) | **Yield Sleeve** | Overlay | SHV |

#### Infrastructure

| Doc | Component | Purpose |
|-----|-----------|---------|
| [11](system/11-portfolio-router.md) | **Portfolio Router** | Central coordination, order authorization |
| [13](system/13-execution-engine.md) | **Execution Engine** | Order submission to broker |
| [19](system/19-oco-manager.md) | **OCO Manager** | One-Cancels-Other for options |
| [15](system/15-state-persistence.md) | **State Persistence** | ObjectStore save/load |

#### Reference

| Doc | Content |
|-----|---------|
| [16](system/16-appendix-parameters.md) | All configuration parameters |
| [17](system/17-appendix-glossary.md) | Term definitions |
| [00](system/00-table-of-contents.md) | Full table of contents |

---

## Current Status

| Component | Version | Status |
|-----------|---------|--------|
| **Swing Mode (Options)** | V2.1 | ✅ Ready |
| **Intraday Mode (Options)** | V2.1.1 | ❌ Blockers (see audit) |
| **Trend Engine** | V2 | ✅ Ready |
| **Mean Reversion** | V2.1 | ✅ Ready |

**Latest Audit**: [v2-1-1-readiness-audit.md](audits/v2-1-1-readiness-audit.md)

---

## Key References

### Configuration
- All parameters: [system/16-appendix-parameters.md](system/16-appendix-parameters.md)
- Source of truth: `config.py` in repository root

### Risk Controls

| Control | Trigger | Action |
|---------|---------|--------|
| Kill Switch | -3% daily | Liquidate ALL |
| Panic Mode | SPY -4% | Liquidate longs only |
| Weekly Breaker | -5% WTD | Reduce sizing 50% |

Full details: [system/12-risk-engine.md](system/12-risk-engine.md)

### Traded Instruments

| Symbol | Type | Strategy | Overnight? |
|--------|------|----------|:----------:|
| QLD | 2× Nasdaq | Trend | ✅ |
| SSO | 2× S&P 500 | Trend | ✅ |
| TQQQ | 3× Nasdaq | Mean Reversion | ❌ |
| SOXL | 3× Semi | Mean Reversion | ❌ |
| TMF | 3× Treasury | Hedge | ✅ |
| PSQ | 1× Inverse | Hedge | ✅ |
| SHV | Short Treasury | Yield | ✅ |

---

## For Claude (AI Developer)

When working on this codebase:

1. **Before code changes**: Read the relevant spec in `specs/`
2. **After code changes**: Check `internal/documentation-map.md` for docs to update
3. **For architecture questions**: Start with `system/02-system-architecture.md`
4. **For parameters**: Check `system/16-appendix-parameters.md` and `config.py`

---

## Related Root-Level Files

| File | Purpose |
|------|---------|
| `CLAUDE.md` | AI assistant instructions |
| `CONTRIBUTING.md` | Git workflow and PR guidelines |
| `config.py` | All tunable parameters |
| `main.py` | QCAlgorithm entry point |

---

*Last Updated: 2026-01-29 (V2.1.1 Documentation Reorganization)*
