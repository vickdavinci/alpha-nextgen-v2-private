# Quick Start Guide

**Goal:** Clone → Setup → Run tests in under 5 minutes.

**Current Version:** V6.x (Options Engine + Micro Regime)

---

## Prerequisites

- Python 3.11 (required - system default may be 3.14, but project requires 3.11)
- Git
- GitHub CLI (`gh`) - optional but recommended
- Lean CLI - for QuantConnect backtests

**Check Python version:**
```bash
python3.11 --version
# Should output: Python 3.11.x
```

If not installed: [python.org/downloads](https://www.python.org/downloads/)

---

## Setup (Copy-Paste Ready)

```bash
# 1. Clone and enter directory
git clone https://github.com/vickdavinci/alpha-nextgen-v2-private.git
cd alpha-nextgen-v2-private

# 2. Create virtual environment with Python 3.11
python3.11 -m venv venv

# 3. Activate virtual environment
source venv/bin/activate  # macOS/Linux
# venv\Scripts\activate   # Windows

# 4. Install dependencies
pip install -r requirements.lock

# 5. Install pre-commit hooks
pip install pre-commit
pre-commit install

# 6. Verify setup
make test
```

---

## Verify Success

After running `make test`, you should see:

```
========================= test session starts ==========================
tests/test_architecture_boundaries.py::test_... PASSED
tests/test_target_weight_contract.py::test_... PASSED
...
========================= X passed in Y.YYs ============================
```

**All tests passed?** You're ready to code.

---

## Quick Commands

| Command | Purpose |
|---------|---------|
| `make test` | Run all tests |
| `make lint` | Check code formatting |
| `make format` | Auto-format code |
| `make branch name=feature/va/my-feature` | Create feature branch |
| `./scripts/qc_backtest.sh "V6.12-Test" --open` | Run QC backtest and wait for results |

---

## Running a Backtest

The primary workflow for testing changes:

```bash
# 1. Make your code changes

# 2. Run unit tests locally
make test

# 3. Run QC backtest (syncs, minifies, pushes, and runs)
./scripts/qc_backtest.sh "V6.12-MyFeature" --open

# 4. Review results in terminal or at the URL provided
```

**What the script does:**
- Syncs all Python files to the Lean workspace
- Minifies files to reduce size (QC has 256KB limit per file)
- Pushes to QuantConnect cloud
- Starts backtest and waits for completion (with `--open`)

---

## First Task Workflow

```bash
# 1. Activate venv (every session!)
source venv/bin/activate

# 2. Check current branch and status
git status && git branch

# 3. Create your feature branch
make branch name=feature/<initials>/<description>

# 4. Make changes to code

# 5. Run tests
make test

# 6. Commit (pre-commit hooks run automatically)
git add <files>
git commit -m "feat: your description"

# 7. Push and create PR
git push origin <branch-name>
gh pr create --base develop
```

---

## Project Architecture (V6.x)

```
Core-Satellite Architecture:
├── Core (40%): Trend Engine (QLD, SSO, TNA, FAS)
├── Satellite (10%): Mean Reversion (TQQQ, SOXL - intraday only)
├── Satellite (25%): Options Engine (QQQ spreads)
│   ├── Swing Mode (18.75%): VASS spreads, 14-45 DTE
│   └── Intraday Mode (6.25%): Micro Regime, 1-5 DTE
└── Hedges: TMF, PSQ (regime-triggered)
```

**Key Engines:**
- **Regime Engine (V5.3)**: 4-factor model (Momentum, VIX Combined, Trend, Drawdown)
- **Options Engine (V6.x)**: Dual-mode with Micro Regime direction detection
- **Risk Engine**: Kill switch, panic mode, drawdown governor

---

## Troubleshooting

### "Python 3.11 not found"

```bash
# macOS with Homebrew
brew install python@3.11

# Ubuntu/Debian
sudo apt install python3.11 python3.11-venv

# Windows
# Download from python.org/downloads
```

### "Module not found" errors

```bash
# Make sure venv is activated
source venv/bin/activate

# Reinstall dependencies
pip install -r requirements.lock
```

### "Pre-commit hook failed"

```bash
# Check what failed
pre-commit run --all-files

# Common fixes:
make format           # Fix formatting
# Remove print() statements from source files
# Remove datetime.now() - use self.algorithm.Time
```

### Backtest errors

```bash
# Check if lean CLI is installed
lean --version

# Re-authenticate if needed
lean login

# Check backtest logs in QC web interface
```

---

## Key Files to Read

| Priority | File | Purpose |
|:--------:|------|---------|
| 1 | `CLAUDE.md` | Architecture, rules, thresholds |
| 2 | `WORKBOARD.md` | Current tasks and progress |
| 3 | `config.py` | All tunable parameters |
| 4 | `docs/system/04-regime-engine.md` | V5.3 regime model |
| 5 | `docs/system/18-options-engine.md` | Options dual-mode design |

---

## Need Help?

| Topic | Document |
|-------|----------|
| Git workflow | [CONTRIBUTING.md](CONTRIBUTING.md) |
| Architecture rules | [CLAUDE.md](CLAUDE.md) |
| QuantConnect patterns | [QC_RULES.md](QC_RULES.md) |
| Common errors | [ERRORS.md](ERRORS.md) |
| Full project structure | [PROJECT-STRUCTURE.md](PROJECT-STRUCTURE.md) |

---

*Time to first test: ~5 minutes*
*Last updated: February 2026 (V6.x)*
