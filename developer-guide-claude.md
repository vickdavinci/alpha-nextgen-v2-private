# Developer Guide: Alpha NextGen V2

**Version:** V6.x (February 2026)
**Purpose:** Development workflows, testing, and deployment guide for the Alpha NextGen algorithmic trading system.

---

## Table of Contents

1. [Quick Reference](#1-quick-reference)
2. [Session Initialization](#2-session-initialization)
3. [Environment Setup](#3-environment-setup)
4. [Development Workflow](#4-development-workflow)
5. [QuantConnect Backtest Workflow](#5-quantconnect-backtest-workflow)
6. [Testing Strategy](#6-testing-strategy)
7. [Code Architecture](#7-code-architecture)
8. [Common Tasks](#8-common-tasks)
9. [Troubleshooting](#9-troubleshooting)

---

## 1. Quick Reference

### Essential Commands

```bash
# Daily startup
source venv/bin/activate && python --version  # Must be 3.11.x

# Development
make test                                      # Run all tests
make lint                                      # Check formatting
make format                                    # Auto-format code

# Backtesting
./scripts/qc_backtest.sh "V6.12-Test" --open   # Run and wait for results

# Git
make branch name=feature/va/my-feature         # Create feature branch
git status && git branch                        # Check status
```


### Backtest Pipeline (Mandatory)

Always use `./scripts/qc_backtest.sh`. It now performs sync, minify, strict validation, per-file size guard, push, and backtest in one flow. Do not manually sync/push as primary path.

### Key Files

| File | Purpose |
|------|---------|
| `CLAUDE.md` | Architecture rules, component map, thresholds |
| `WORKBOARD.md` | Current tasks and progress |
| `config.py` | All tunable parameters |
| `main.py` | QCAlgorithm entry point (~3,800 lines) |

### Critical Rules

1. **Only Portfolio Router places orders** - Strategy engines emit `TargetWeight` objects only
2. **Risk Engine runs first** - Every `OnData` must check kill switch before strategies
3. **TQQQ/SOXL close by 15:45** - No overnight holds for 3× leveraged ETFs (MR only)
4. **Python 3.11 required** - System default may be 3.14, but project requires 3.11

---

## 2. Session Initialization

### Starting a New Session

```bash
# 1. Activate environment (CRITICAL - do this first!)
source venv/bin/activate && python --version
# Expected: Python 3.11.x

# 2. Check current state
git status && git branch
head -60 WORKBOARD.md

# 3. Check for uncommitted work
git diff --stat
```

### After Context Reset / Compaction

If Claude Code session was compacted or reset:

```bash
# The venv is NOT active - always run this first
source venv/bin/activate && python --version

# Check what you were working on
head -60 WORKBOARD.md
git log --oneline -5
```

---

## 3. Environment Setup

### Prerequisites

| Tool | Version | Purpose |
|------|---------|---------|
| Python | 3.11.x | Local development (NOT 3.14) |
| Git | 2.40+ | Version control |
| Lean CLI | Latest | QuantConnect backtests |
| GitHub CLI | Optional | PR creation |

### Fresh Setup

```bash
# Clone
git clone https://github.com/vickdavinci/alpha-nextgen-v2-private.git
cd alpha-nextgen-v2-private

# Create venv with correct Python
python3.11 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.lock
pip install pre-commit && pre-commit install

# Verify
make test
```

### Lean CLI Setup

```bash
# Install
pip install lean

# Authenticate
lean login

# Verify
lean whoami
```

---

## 4. Development Workflow

### Feature Development Flow

```
1. Create Branch
   └── make branch name=feature/va/description

2. Make Changes
   ├── Edit code
   ├── Run tests: make test
   └── Run backtest: ./scripts/qc_backtest.sh "Name" --open

3. Commit
   ├── git add <files>  (specific files, not -A)
   └── git commit -m "feat: description"

4. Push & PR
   ├── git push origin <branch>
   └── gh pr create --base develop
```

### Commit Message Format

```
<type>(<scope>): <description>

Types: feat, fix, docs, refactor, test, chore
Scope: regime, options, trend, risk, etc.

Examples:
- feat(options): V6.12 add PUT delta filter relaxation
- fix(regime): V5.3 spike cap threshold adjustment
- docs(audit): V6.11 2022 backtest analysis
```

### Branch Naming

```
feature/va/<description>   # New features
fix/va/<description>       # Bug fixes
docs/va/<description>      # Documentation only
```

---

## 5. QuantConnect Backtest Workflow

### The Automated Script (Recommended)

```bash
# Run backtest and wait for results
./scripts/qc_backtest.sh "V6.12-MyFeature" --open

# Fire-and-forget (async)
./scripts/qc_backtest.sh "V6.12-MyFeature"

# Auto-name from git branch
./scripts/qc_backtest.sh --open
```

**What the script does:**
1. Syncs all Python files to lean-workspace
2. Minifies files (strips comments/docstrings for 256KB limit)
3. Pushes to QuantConnect cloud
4. Starts backtest
5. With `--open`: Waits and streams results

### Changing Backtest Dates

Edit `main.py` lines ~196-197:

```python
self.SetStartDate(2022, 1, 1)
self.SetEndDate(2022, 2, 28)  # 2 months
```

Then run the backtest script.

### QC Resource Limits

| Resource | Limit | Notes |
|----------|------:|-------|
| File Size | 256 KB | Minification handles this |
| Backtest Log | 5 MB | Use `trades_only=True` |
| Daily Log | 50 MB | Multiple debug runs OK |
| Nodes | B4-12 | Required for options data |

### Logging Pattern

```python
# ALWAYS log (visible in backtest): fills, entries, exits, errors
self.Log("FILL: BUY 100 QLD", trades_only=True)

# LIVE ONLY (silent in backtest): signals, diagnostics
self.Log("INTRADAY_SIGNAL: ...", trades_only=False)
```

---

## 6. Testing Strategy

### Test Levels

| Level | Command | Purpose |
|-------|---------|---------|
| Unit | `make test` | Engine logic validation |
| Scenario | `pytest tests/scenarios/` | Workflow testing |
| Backtest | `./scripts/qc_backtest.sh` | Full system validation |
| Audit | Manual | Log analysis post-backtest |

### Running Tests

```bash
# All tests
make test

# Single file
pytest tests/test_regime_engine.py -v

# Single function
pytest tests/test_regime_engine.py::test_regime_score_boundaries -v

# Pattern match
pytest -k "kill_switch" -v
```

### Post-Backtest Audit

After a backtest, analyze logs:

1. Download logs from QC or copy to `docs/audits/logs/`
2. Key patterns to grep:
   - `KILL_SWITCH` - Circuit breaker triggers
   - `Dir=NONE` - Signal starvation
   - `VASS_REJECTION` - Options contract filter issues
   - `ERROR` / `EXCEPTION` - Failures

---

## 7. Code Architecture

### Current Architecture (V6.x)

```
Core-Satellite Architecture:
├── Core (40%): Trend Engine
│   └── QLD (15%), SSO (12%), TNA (8%), FAS (5%)
├── Satellite (10%): Mean Reversion Engine
│   └── TQQQ (5%), SOXL (5%) - INTRADAY ONLY
├── Satellite (25%): Options Engine (Dual-Mode)
│   ├── Swing Mode (18.75%): VASS spreads, 14-45 DTE
│   └── Intraday Mode (6.25%): Micro Regime, 1-5 DTE
└── Hedges: TMF, PSQ (regime-triggered)
```

### Key Engines

| Engine | File | Purpose |
|--------|------|---------|
| Regime (V5.3) | `engines/core/regime_engine.py` | 4-factor market state scoring |
| Options (V6.x) | `engines/satellite/options_engine.py` | Dual-mode QQQ spreads |
| Trend | `engines/core/trend_engine.py` | MA200+ADX swing trades |
| Risk | `engines/core/risk_engine.py` | Circuit breakers, kill switch |
| Mean Reversion | `engines/satellite/mean_reversion_engine.py` | Intraday oversold bounce |

### Regime Model (V5.3)

```
4-Factor Model:
├── Momentum (30%): 20-day ROC
├── VIX Combined (30%): 60% level + 40% direction
├── Trend (25%): SPY vs MA200
└── Drawdown (15%): Distance from 52-week high

Guards:
├── Spike Cap: Score capped at 45 when VIX 5d >= +28%
└── Breadth Decay Penalty: -5/-8 points for RSP/SPY decay
```

### Options Engine Modes

| Mode | Allocation | DTE | Direction Source |
|------|:----------:|:---:|------------------|
| Swing | 18.75% | 14-45 | Macro Regime Score |
| Intraday | 6.25% | 1-5 | Micro Regime (VIX × UVXY) |

---

## 8. Common Tasks

### Add a New Parameter

1. Add to `config.py` with default value
2. Update `docs/system/16-appendix-parameters.md`
3. Update `CLAUDE.md` Key Thresholds if critical
4. Update relevant engine spec doc

### Modify a Threshold

1. Change value in `config.py`
2. Run backtest to validate
3. Update documentation

### Add a New Engine

1. Create file in `engines/core/` or `engines/satellite/`
2. Add to `CLAUDE.md` Component Map
3. Add to `PROJECT-STRUCTURE.md`
4. Create spec doc in `docs/system/`
5. Update `main.py` to instantiate

### Run a Multi-Year Backtest

```python
# In main.py
self.SetStartDate(2015, 1, 1)
self.SetEndDate(2022, 12, 31)
```

Then:
```bash
./scripts/qc_backtest.sh "V6.12-FullHistory" --open
```

---

## 9. Troubleshooting

### Python Version Issues

```bash
# Check version
python --version

# If not 3.11, activate venv
source venv/bin/activate
python --version  # Should now be 3.11.x
```

### Backtest Won't Start

```bash
# Check lean authentication
lean whoami

# Re-login if needed
lean login

# Check if files synced
ls ~/Documents/Trading\ Github/lean-workspace/AlphaNextGen/
```

### Tests Failing

```bash
# Run with verbose output
pytest -v --tb=short

# Run specific failing test
pytest tests/test_file.py::test_name -v
```

### Pre-commit Hooks Fail

```bash
# See what's failing
pre-commit run --all-files

# Common fixes
make format  # Fix black/isort issues
```

### Log Analysis

```bash
# Count key patterns in backtest log
grep -c "KILL_SWITCH" logfile.txt
grep -c "Dir=NONE" logfile.txt
grep -c "VASS_REJECTION" logfile.txt
grep -c "ERROR" logfile.txt
```

---

## Quick Links

| Document | Purpose |
|----------|---------|
| [CLAUDE.md](CLAUDE.md) | Architecture and rules |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Git workflow |
| [QC_RULES.md](QC_RULES.md) | QuantConnect patterns |
| [ERRORS.md](ERRORS.md) | Common errors |
| [docs/system/](docs/system/) | Engine specifications |

---

*Last updated: February 2026 (V6.x)*
