# CLAUDE.md - Claude Code Instructions

## Analysis Rigor Rules (MANDATORY)

- NEVER state a config value from memory — always grep/read the actual line first
- NEVER claim a root cause without tracing the exact code path with line numbers
- NEVER propose a fix without first verifying the current behavior with data
- When analyzing trades, cross-reference orders.csv + logs — never rely on one source alone
- If an earlier statement contradicts new evidence, flag it immediately as a correction
- Distinguish clearly between: CONFIRMED (verified in code/data) vs HYPOTHESIS (needs verification)

## Shared Process (Mandatory)

- Follow `PROCESS.md` for workflow, gates, commit contract, and backtest artifact process.
- If any instruction here conflicts with `PROCESS.md` process enforcement, run `PROCESS.md` checks first.

## Recent Work Log (V12.30 / V12.31 / V12.32)

### V12.30: Bear/Credit Routing & Regime Alignment (16 commits)

Fixed bear-side and credit-side VASS routing so options engine activates bearish/credit lanes when regime warrants it.

**Key changes:**
- **Assignment gate polarity flip** (`23b99a0`): Was blocking bear entries in bear regimes; flipped to enforce in bull, relax in bear
- **VASS neutral fallback direction** (`56036af`): In macro NEUTRAL, infer direction from transition delta instead of blocking all entries
- **BEAR_PUT preferred in high-IV bearish** (`9462ec5`): Routes to BEAR_PUT_DEBIT over BEAR_CALL_CREDIT when VIX high + bearish regime
- **STRESS assignment gate relaxed** (`812ec87`): In true bear (score ≤ 45, VIX ≤ 30), skip OTM assignment gate
- **BEAR_PUT high-IV pivot with fallback** (`4204b54`): If pivot to PUT finds no candidates, reverts to credit and retries
- **Deep bear guard on neutral fallback** (`1bfdfe5`): When score ≤ 45, block BULLISH inference to preserve BEAR_PUT pivot
- **Overlay state persistence** (`72d3180`): `_regime_overlay_state`, `enter_seq`, `sample_seq` survive restart
- **Credit recovery derisk disabled** (`f03b247`): Transition derisk window turned off
- **25 regression tests** (`e95c7ff`): Covering all V12.30 routing and gating changes

### V12.31: Scan Funnel Unclog + SAFE Hardening (14 commits)

**Strategy/Throughput:**
- **Credit tail-cap gated to emergency windows** (`cacc53d`): In THETA_FIRST, only fire tail-cap in emergency VIX/regime windows
- **Scan funnel unclogged** (`5028e7b`): Attempt-aware interval throttle reduces wasted `R_VASS_SCAN_INTERVAL_GUARD` burns by consuming scan budget only when candidate-scan work actually starts
- **Resolver unclogged** (`658d232`): Soft neutral fallback + short direction memory reduce `R_VASS_RESOLVER_NO_TRADE`; this is the commit that materially improved bearish spread availability in neutral-macro tape
- **QQQ invalidation threshold tuned** (`04b73a9`): 4.0% → 3.9% for earlier tail-loss detection
- **BR-08 open-delay** (`68a84ac`): Profit-target exits deferred first 5 min after open (09:30-09:34)
- **Adaptive insufficient-BP retry** (`2d634e5`): Broker-maintenance-based retry contract cap + cooldown

**SAFE Track (Pre-Live Hardening):**
- SAFE-01 through SAFE-06: Router rejection artifact schema, tag/trace backfill, deterministic incident IDs, lifecycle diag counters, exit code normalization, ObjectStore loader hardening
- Follow-ups: Tighter invalid counter classification, `tag_origin` attribution

### V12.31: Bear-Path Exit/Structure Fixes (7 commits)

**Scoped BEAR_PUT / bear-regime fixes:**
- **Bear-regime fresh OGP carveout** (`b84e56e`): Skip fresh-trade OGP for `BEAR_CALL_CREDIT` / `BEAR_PUT_DEBIT` when regime is already bearish; keep global `close_all` intact
- **Put phantom-profit fix** (`7ef1515`): `BEAR_PUT` / `BEAR_PUT_DEBIT` profit-target eligibility now uses tradeable spread value instead of intrinsic-overridden valuation
- **BEAR_PUT width widening** (`a28c46d`): Bias `BEAR_PUT` width target/min one strike wider and modestly relax bearish put D/W caps inside the existing selector/validator helpers
- **BEAR_PUT close-all OGP override** (`db35600`): Raise bear-regime `close_all` OGP threshold to `40.0` only for `BEAR_PUT` / `BEAR_PUT_DEBIT`, leaving all other spread exits unchanged
- **BEAR_PUT MFE/trail tradeable-value scoping** (`e8f0ca0`): `BEAR_PUT` / `BEAR_PUT_DEBIT` MFE-lock and trail evaluation now use tradeable spread value, preventing intrinsic-inflated early non-profit exits
- **Assignment margin exit scoping** (`71c64e6`): Remove `MARGIN_BUFFER_INSUFFICIENT` forced exits for debit spreads and narrow the margin-buffer guard to near-expiry ITM credit spreads only
- **Close-cancel ladder phase scoping** (`f2c5a01`): Reset the cancel budget only on `LIMIT -> COMBO_MARKET` promotion so combo-market gets its own rung without skipping directly to sequential fallback

### V12.32: Transition Gate Narrowing (1 commit)

- **VASS bear recovery hard-block scoped by flip bars** (`d7fa2c1`): Keep `VASS_TRANSITION_BLOCK_BEAR_ON_RECOVERY` only for the first two `RECOVERY` bars after an overlay flip, then fall through to the existing `TRANSITION_HANDOFF_PUT_THROTTLE`

### Open P0 items (from V12.30 Plumbing Register)

- BR-08 open-delay: implemented, needs A/B validation
- QQQ invalidation tail-loss: threshold tuned, needs validation
- Entry invalid insufficient-BP: adaptive retry implemented, needs zero-invalid validation
- OGP too blunt for elevated VIX: first two steps implemented via fresh-trade carveout plus `BEAR_PUT` bear-regime `close_all` override, still needs validation
- Put phantom-profit / early intrinsic-driven exits on `BEAR_PUT`: profit-target and MFE/trail first steps implemented, still need validation
- BEAR_PUT width under skew: first-step implemented, still needs validation
- Margin-buffer forced exits: first-step implemented via debit-path removal plus credit danger-zone scoping, still needs validation
- Combo close cancel ladder: first-step phase scoping implemented, still needs validation
- VASS bear recovery transition block: first-step implemented via early-bar-only hard block plus later handoff throttle, still needs validation

See `docs/audits/logs/stage12.30/V12.30_PLUMBING_STRATEGY_STATUS.md` for full status register.

### Validation run

```bash
source venv/bin/activate && \
pytest -q tests/test_options_engine.py \
          tests/test_portfolio_router.py \
          tests/test_plumbing_regressions.py \
          tests/integration/test_ondata_flow.py
```

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

**What the script does (current):**
1. Syncs project files to lean-workspace via `scripts/sync_to_lean.sh`
2. Runs standard + ultra minification
3. Runs strict telemetry/syntax validation (`scripts/validate_lean_minified.py --strict`)
4. Enforces QC per-file size guard (`*.py <= 256KB`)
5. Pushes to QuantConnect cloud via `lean cloud push`
6. Starts the backtest with specified name
7. With `--open`: waits for completion and streams results

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
- **Satellite (50%)**: Options Engine - VASS + Dual-Mode Architecture (V12.5: decomposed into sub-engines)
  - **Swing Mode (37.5%)**: VASS debit/credit spreads (14-45 DTE, routes by IV, VIX-tiered exits)
  - **Intraday Mode (12.5%)**: Micro Regime Engine - VIX Level × VIX Direction (1-5 DTE)
  - **ITM Momentum**: Single-leg ITM options (delta 0.70-0.80, 14-21 DTE)

Forked from V1 v1.0.0 on 2026-01-26. See `docs/specs/v2.1/` for V2.1 specifications (archived).
See `docs/specs/v2.1/v2-1-options-engine-design.txt` for Options Engine V2.1.1 design reference.

## Repository Structure

```
alpha-nextgen/
├── main.py                     # QCAlgorithm entry point (~2,900 lines - V12.31)
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
│       ├── hedge_engine.py     # SH inverse overlay (V6.11: TMF/PSQ retired)
│       ├── yield_sleeve.py     # SHV cash management (spec only)
│       ├── options_engine.py   # QQQ options (50%) - VASS exits, spread lifecycle, Micro Regime
│       ├── vass_entry_engine.py # V12.5: VASS swing entry routing, direction, candidate selection
│       ├── micro_entry_engine.py # V12.5: Micro intraday entry gates, lane caps, friction filters
│       └── itm_horizon_engine.py # V12.5: ITM momentum single-leg entry/direction proposals
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
| **Startup Gate** | `engines/core/startup_gate.py` | `docs/system/ENGINE_LOGIC_REFERENCE.md` | V6.0: All-weather time-based arming (6 days: 3+3). Never resets on kill switch. |
| **Trend Engine** | `engines/core/trend_engine.py` | `docs/system/07-trend-engine.md` | MA200 + ADX trend signals for QLD/SSO/UGL/UCO (40%) - V6.11: equities + commodities |

### Satellite Engines (engines/satellite/)

| Component | File | Spec Document | Description |
|-----------|------|---------------|-------------|
| **Mean Reversion Engine** | `engines/satellite/mean_reversion_engine.py` | `docs/system/08-mean-reversion-engine.md` | Intraday oversold bounce signals for TQQQ/SPXL/SOXL (10%) |
| **Hedge Engine** | `engines/satellite/hedge_engine.py` | `docs/system/09-hedge-engine.md` | Regime-based SH allocation signals (V6.11: TMF/PSQ retired) |
| **Yield Sleeve** | `engines/satellite/yield_sleeve.py` | `docs/system/10-yield-sleeve.md` | SHV cash management signals (spec only) |
| **Options Engine** | `engines/satellite/options_engine.py` | `docs/system/18-options-engine.md` | QQQ options: spread exit cascade, lifecycle mgmt, Micro Regime (V12.4) |
| **VASS Entry Engine** | `engines/satellite/vass_entry_engine.py` | `docs/system/18-options-engine.md` | V12.5: VASS swing entry routing matrix, direction context, candidate selection, leg building |
| **Micro Entry Engine** | `engines/satellite/micro_entry_engine.py` | `docs/system/18-options-engine.md` | V12.5: Micro intraday entry gates, lane caps, friction filters, contract validation |
| **ITM Horizon Engine** | `engines/satellite/itm_horizon_engine.py` | `docs/system/18-options-engine.md` | V12.5: ITM momentum single-leg direction proposals and entry orchestration |

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

## Options Architecture Decision (V12.5)

V12.5 is an ongoing refactor decomposing the monolithic options engine into specialized sub-engines. The current boundary for ownership:

- `VASSEntryEngine` (`engines/satellite/vass_entry_engine.py`) owns:
  - VASS strategy routing matrix (direction × IV regime)
  - VASS-specific anti-cluster/day-gap/swing filter guards
  - VASS lifecycle throttles that are not contract-economics checks
  - V12.5: VASS directional scan, candidate contract building, spread signal construction, leg selection
  - V12.30: Neutral fallback direction inference, BEAR_PUT high-IV pivot, assignment gate polarity
  - V12.31: Attempt-aware scan interval throttle, soft neutral fallback with direction memory
- `MicroEntryEngine` (`engines/satellite/micro_entry_engine.py`) owns:
  - Micro intraday lane caps, friction gates, contract-selection validation
  - V12.5: Intraday entry execution and gating
- `ITMHorizonEngine` (`engines/satellite/itm_horizon_engine.py`) owns:
  - ITM momentum direction proposals and entry orchestration
- `OptionsEngine` (`engines/satellite/options_engine.py`) owns:
  - **All spread exit logic**: the full exit cascade (VIX spike, overlay stress, tail risk cap, adaptive stop, trail, MFE lock, etc.)
  - Spread lifecycle management (hold guard, assignment risk, DTE close)
  - Shared spread economics and contract-quality validation (D/W caps, debit caps, sizing, margin checks)
  - `check_spread_entry_signal()` / `check_credit_spread_entry_signal()` final validation
  - V12.27: Credit THETA_FIRST mode (hold guard, 2x-credit stop, guarded premarket ITM close)
  - V12.28: Spread close intent persistence, retry ladder, preclear close-intent awareness
  - V12.31: BR-08 profit-target open-delay, adaptive insufficient-BP retry sizing
- `main.py` orchestrates flow: calls sub-engines for entry, `OptionsEngine` for exit/validation.

### Practical Rule For New Changes

- If the logic is VASS entry policy/routing intent, put it in `VASSEntryEngine`.
- If the logic is micro intraday entry gating, put it in `MicroEntryEngine`.
- If the logic is ITM momentum entry, put it in `ITMHorizonEngine`.
- If the logic is spread exit/lifecycle or shared validation, keep it in `OptionsEngine`.

### VASS Spread Exit Cascade (V12.31)

The debit spread exit cascade runs in this priority order. **Higher priority exits prevent lower ones from firing.**

```
OPEN DELAY (V12.31):
  - Profit-target exits deferred during 09:30-09:34 (BR-08 open-delay)

HOLD GUARD (if position < 1440 min old):
  1. HARD_STOP_DURING_HOLD     — pnl <= -(VIX-tiered hard stop)  → immediate exit
  2. EOD_HOLD_RISK_GATE        — pnl <= -(VIX-tiered EOD gate) at 15:45+, held >= 240m → exit
  3. Bypass checks: Profitable | Transition | LossBypass (75% of stop) | SevereLoss (110%)
  4. If no bypass → BLOCK all exits below

MAIN CASCADE (after hold expired or bypassed):
  P0. VIX Spike Exit           — VIX >= 25 or 5D change >= 20%
  P1. Overlay Stress Exit      — regime stress state
  P2. Overnight De-risk        — pre-close transition deterioration (V12.4; credit recovery derisk DISABLED V12.30)
  P3. MFE Lock Floor           — gave back gains past lock threshold
  P4. TAIL_RISK_CAP            — dollar loss >= 1.0% of portfolio equity (~$1,000)
                                  (V12.31: gated to emergency windows in THETA_FIRST credit mode)
  P5. Day-4 EOD Decision       — close after 4 days if losing
  P6. Profit Target            — regime-adaptive, commission-aware (V12.31: open-delay aware)
  P7. Trail Stop               — trail from high-water mark
  P8. Width Hard Stop           — % of spread width
  P9. PCT Hard Stop            — VIX-tiered hard stop %
  P10. Adaptive Stop (STOP_LOSS) — VIX-tiered + regime-multiplied + ATR-scaled
  P11. Time Stop               — max hold days (low VIX: shorter)
  P12. Regime Deterioration    — regime dropped 10+ pts while losing
  P13. QQQ Invalidation (V12.25) — bull debit thesis-break when QQQ moves 3.9% against (V12.31: tuned from 4.0%)
  P14. DTE Exit                — close by 5 DTE
  P15. Neutrality Exit         — regime in dead zone (45-60) with flat P&L (V12.25: DISABLED)
```

**Known issue (V12.4):** TAIL_RISK_CAP (P4) fires at ~19% of debit ($1,000 / $5,250 typical position), which is BEFORE the adaptive stop (P10) at 25%. The adaptive stop is effectively unreachable for current position sizing (15 contracts × ~$3.50 debit).

### VASS Direction Resolution (V12.30)

The VASS direction resolver was significantly reworked in V12.30:
- **Macro-only mode** (V12.26): Direction comes from regime, not micro signals
- **Neutral fallback** (V12.30): When macro returns NEUTRAL, infers BULLISH/BEARISH from transition-score delta (min 1.0pt, soft 0.5pt)
- **Deep bear guard** (V12.30): When regime ≤ 45, BULLISH inference blocked to preserve BEAR_PUT pivot
- **High-IV BEAR_PUT pivot** (V12.30): In high-IV bearish, prefers BEAR_PUT_DEBIT over BEAR_CALL_CREDIT; falls back to credit if no PUT candidates
- **Assignment gate polarity** (V12.30): Enforces OTM gate in bull regimes, relaxes in bear (STRESS relax: score ≤ 45, VIX ≤ 30)
- **Short direction memory** (V12.31): Soft neutral fallback with attempt-aware throttle to reduce resolver starvation

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
│  (Score 0-100)  │  (Phase/Lockbox)│ (Tiered KS/DG)  │ (6 days)    │
└────────┬────────┴────────┬────────┴────────┬────────┴──────┬──────┘
         │                 │                 │               │
         ▼                 ▼                 ▼               ▼
┌───────────────────────────────────────────────────────────────────┐
│                       STRATEGY ENGINES                            │
├─────────────────┬─────────────────┬─────────────────┬─────────────┤
│  Trend Engine   │ Options Engine  │    MR Engine    │Hedge/Yield  │
│   (Core 40%)    │ (Satellite 50%) │ (Satellite 10%) │  (Overlay)  │
│QLD,SSO,UGL,UCO  │ VASS Dual-Mode  │ TQQQ,SPXL,SOXL  │    SH       │
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
    """Verify kill switch Tier 3 liquidates all positions."""
    # Setup: Portfolio with multiple positions
    # Action: Trigger 6% loss (Tier 3)
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
7. **3x held overnight** - TQQQ/SPXL/SOXL must close by 15:45

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
| Kill switch (Tier 3) | 6% daily loss | Full liquidation (V2.27: graduated system) |
| Kill switch (Tier 2) | 4% daily loss | Trend exit, keep spreads |
| Kill switch (Tier 1) | 2% daily loss | Reduce trend 50%, block new options |
| Preemptive KS | 4.5% daily loss | Warning threshold |
| Panic mode | SPY -4% intraday | Liquidate longs only |
| Weekly breaker | 5% WTD loss | 50% sizing reduction |
| Gap filter | SPY -1.5% gap | Block MR entries |
| Vol shock | 3x ATR bar | 15-min pause |
| Leverage cap | 90% margin utilization | Block new entries (V6.20: raised from 60%) |
| Regime RISK_ON | Score >= 70 | Full leverage, no hedges |
| Regime NEUTRAL | Score 50-69 | Full leverage, no hedges |
| Regime CAUTIOUS | Score 45-49 | V6.15: raised from 40 (light hedge: 5% SH) |
| Regime DEFENSIVE | Score 35-44 | V6.15: raised from 30 (medium hedge: 8% SH) |
| Regime RISK_OFF | Score 0-34 | No new longs, full hedge: 10% SH |
| Trend entry (V2) | Price > MA200 + ADX >= 15 | Trend entry eligible (V2.3.12: was 25) |
| Oversold | RSI(5) < 25 | MR entry eligible |
| VIX Low | VIX < 20 | Normal market, MR works |
| VIX Medium | VIX 20-25 | Caution zone |
| VIX High | VIX > 25 | Elevated, momentum dominates |
| VIX Extreme | VIX > 40 | Crisis mode |
| VASS Low IV (V6.6) | VIX < 16 | Debit spreads, 30-45 DTE (monthly) |
| VASS Medium IV | VIX 16-25 | Debit spreads, 7-30 DTE (V6.12: widened from 7-21) |
| VASS High IV (V6.13.1) | VIX > 25 | Credit spreads, 5-40 DTE (expanded from 5-28) |
| UVXY Bearish | UVXY > +2.0% | PUT conviction signal |
| UVXY Bullish | UVXY < -4.0% | CALL conviction signal |
| UVXY Conviction Extreme | 3.0% | NEUTRAL VETO threshold |
| BEAR_PUT OTM Gate (V6.4) | Short PUT >= 2% OTM | Block assignment-risk entries (V6.10: was 3%) |
| Options Profit Target | 60% | Profit target |
| Options ATR Stop Max | 28% | Max loss cap |
| Options ATR Stop Min | 12% | Min stop floor |
| Intraday Force Exit (V6.15) | 15:25 | Close intraday options (was 15:30) |
| Intraday FADE VIX Min | VIX >= 9.0 | Enable DEBIT_FADE (V8.2: lowered from 9.5) |
| Intraday ITM VIX Min | VIX >= 9.0 | Enable ITM momentum |
| Spread Force Close DTE (V6.10) | DTE = 1 | Mandatory spread close (assignment prevention) |
| Short Leg ITM Exit | 3.5% ITM | Exit spread when short leg ITM (raised to reduce noise) |
| Spread Width Min (V6.13) | $4 | Minimum spread width (optimized for fills) |
| Spread Width Effective Max (V9.1) | $7 | Preferred width ceiling for R:R sort |
| Spread Max Debit/Width (V9.1) | 55% | Block spreads where debit > 55% of width (R:R gate) |
| VASS Max Contracts (V12.4) | 15 | Hard cap on spread contracts per VASS entry |
| VASS Tail Risk Cap (V12.4) | 1.0% equity | Per-spread $ loss cap (~$1,000 on $100K). Fires BEFORE adaptive stop. |
| VASS Stop Low VIX (V12.2) | 25% | Adaptive stop when entry VIX < 18 |
| VASS Stop Med VIX (V12.2) | 35% | Adaptive stop when entry VIX 18-25 |
| VASS Stop High VIX (V12.2) | 40% | Adaptive stop when entry VIX > 25 |
| VASS Hard Stop Low VIX (V12.3) | 35% | Absolute hard cap during hold, VIX < 18 |
| VASS Hard Stop Med VIX (V12.3) | 40% | Absolute hard cap during hold, VIX 18-25 |
| VASS Hard Stop High VIX (V12.3) | 45% | Absolute hard cap during hold, VIX > 25 |
| VASS EOD Gate Low VIX (V12.3) | -20% | Close at EOD during hold if loss > 20%, VIX < 18 |
| VASS EOD Gate Med VIX (V12.3) | -25% | Close at EOD during hold if loss > 25%, VIX 18-25 |
| VASS EOD Gate High VIX (V12.3) | -35% | Close at EOD during hold if loss > 35%, VIX > 25 |
| VASS Hold Guard (V12.3) | 1440 min (1 day) | Soft hold window; bypasses: LossBypass 75%, SevereLoss 110%, Transition |
| VASS EOD Gate Min Hold (V12.3) | 240 min (4h) | EOD gate can't fire until spread held 4 hours |
| VASS ATR Exit Scaling (V12.3) | 0.85x-1.25x | Multiply stops/targets by ATR ratio vs 1.5% ref |
| VASS Loss Breaker | 3 consecutive | Pause VASS entries for 1 day after 3 consecutive losses |
| VASS Overnight De-risk (V12.4) | 15:40 | Close bullish debit spreads pre-close on regime DETERIORATION/AMBIGUOUS |
| VASS Profit Target Open Delay (V12.31) | 5 min | Defer profit-target exits during 09:30-09:34 to avoid open slippage |
| VASS QQQ Invalidation Intraday (V12.31) | 3.9% | Bull debit thesis-break exit when QQQ moves against (was 4.0%) |
| VASS Neutral Fallback Deep Bear Max (V12.30) | 45 | Block BULLISH inference from neutral fallback when regime ≤ this |
| Choppy Market Filter (V6.10) | 3 reversals/2hr | 65% size reduction in choppy markets (V6.19) |
| VIX Stable Band Low (V6.22) | +/-0.3% | STABLE band when VIX < 15 |
| VIX Stable Band High (V8.2) | +/-0.8% | STABLE band when VIX > 25 |
| Margin Utilization Max (V6.20) | 90% | Cap total margin usage (emergency brake) |
| Options Max Margin PCT (V6.20) | 40% | Max margin for options |
| Options Budget Cap (V8.2) | 50% | Options budget gate = CAPITAL_PARTITION_OPTIONS (uses TotalPortfolioValue, NOT tradeable equity) |
| Micro Score Bullish (V6.22) | 42 | Bullish confirmation threshold (lowered from 48) |
| Micro Score Bearish (D7) | 50 | Bearish confirmation threshold |

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

### Exposure Limits (V6.11)

| Group | Symbols | Max Net Long | Max Gross |
|-------|---------|:------------:|:---------:|
| NASDAQ_BETA | QLD, TQQQ, SOXL | 50% | 75% |
| SPY_BETA | SSO, SPXL, SH (inverse) | 40% | 50% |
| COMMODITIES | UGL, UCO | 25% | 25% |

> **V6.11 Note:** RATES group removed (TMF/SHV retired). COMMODITIES added with UGL (2x Gold) and UCO (2x Crude Oil).

---

## Custom Agents

These agents are defined in `.claude/agents/` and available via the Task tool:

| Agent | Purpose | Usage |
|-------|---------|-------|
| **log-analyzer** | Analyze backtest logs and generate comprehensive trading performance reports with hedge fund style statistics | `Use the log-analyzer agent to analyze the V7 2017 logs in stage7/` |
| **backtest-runner** | Run backtests on QuantConnect with automated sync, push, and log organization | `Use the backtest-runner agent to run Dec 2021 - Feb 2022` |
| **v3-pre-live-auditor** | Comprehensive pre-live audit checks (state persistence, IBKR rules, assignment handling) | `Use the v3-pre-live-auditor to validate before going live` |
| **docs-sync** | Update documentation after code changes to keep docs in sync | `Use the docs-sync agent to update docs for my changes` |
| **trade-analyzer** | Trade-by-trade P&L analysis with log cross-referencing for regime, VIX, entry/exit triggers per trade | `Use the trade-analyzer agent to analyze the V10.1 2023 trades in stage10.1/` |

To invoke an agent with bypass permissions:
```
Use the log-analyzer agent in bypassPermissions mode to analyze stage7 logs
```
