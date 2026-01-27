# Developer Guide: Building Alpha NextGen with Claude Code

---

## Start Here: Session Initialization

Every Claude Code session needs context about the Alpha NextGen project.

```
┌─────────────────────────────────────────────────────────────────────┐
│                    SESSION START                                     │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────┐         ┌─────────────────────────┐
│   FIRST-TIME SETUP      │         │   RETURNING SESSION     │
│                         │         │                         │
│ • New to project        │         │ • Worked on this before │
│ • Fresh start needed    │         │ • Continuing work       │
│                         │         │                         │
│ Read: Full docs         │         │ Read: WORKBOARD.md      │
│       (~10-15 min)      │         │       + CLAUDE.md       │
└─────────────────────────┘         └─────────────────────────┘
```

---

### Scenario 1: First-Time Onboarding

**Use this when:** Starting fresh, new to the project, or after a major context reset.

```
# ALPHA NEXTGEN FIRST-TIME ONBOARDING

**PRIME DIRECTIVE:** Only the Portfolio Router may place orders. Strategy engines
are ANALYZERS ONLY — they emit `TargetWeight` objects but NEVER call
`MarketOrder()`, `MarketOnOpenOrder()`, or `Liquidate()`.

Read the following files to understand the project:

1. **Constitution Files** (read these first):
   - Read `CLAUDE.md` - Component map, authority rules, coding conventions
   - Read `QC_RULES.md` - QuantConnect/LEAN specific patterns
   - Read `ERRORS.md` - Common errors and solutions

2. **Architecture Overview**:
   - Read `docs/01-executive-summary.md` - Project goals and constraints
   - Read `docs/02-system-architecture.md` - Hub-spoke design, authority hierarchy
   - Read `PROJECT-STRUCTURE.md` - Current file layout

3. **Configuration** (if exists):
   - Read `config.py` - All tunable parameters

4. **Current State**:
   - Read `WORKBOARD.md` - Current phase, tasks, and ownership

5. **After reading, confirm your understanding**:
   - Summarize the project in 2-3 sentences
   - State the current phase and what's in progress
   - Identify your assigned task from WORKBOARD.md

Do NOT start coding until you have confirmed your understanding.
```

---

### Scenario 2: Returning Session (Quick Resume)

**Use this when:** You've worked on the project before and just need to resume.

```
# ALPHA NEXTGEN SESSION RESUME

Read `WORKBOARD.md` and `CLAUDE.md`.

Then confirm:
1. Current phase and what's in progress
2. Your assigned task
3. The spec document for your task

Ready to continue.
```

---

### Scenario 3: After a Git Pull

**Use this when:** You've pulled changes from the repository.

```
# ALPHA NEXTGEN POST-PULL CHECK

Run: git log --oneline -5

Read any files that changed in recent commits.
Read `WORKBOARD.md` for current task status.
Read `CLAUDE.md` to refresh on conventions.

Summarize what changed and confirm you're ready to continue.
```

---

### State Tracking

| What | Where |
|------|-------|
| Current phase | `WORKBOARD.md` |
| Task ownership | `WORKBOARD.md` |
| What's in progress | `WORKBOARD.md` |
| Build order | This guide, Section 5.3 |
| Coding conventions | `CLAUDE.md` |
| Git workflow | `CONTRIBUTING.md` |

---

## Table of Contents

1. [Introduction](#1-introduction)
2. [Environment Setup](#2-environment-setup)
3. [Claude Code Configuration](#3-claude-code-configuration)
4. [QuantConnect Integration](#4-quantconnect-integration)
5. [Incremental Development Workflow](#5-incremental-development-workflow)
6. [Testing & Validation](#6-testing--validation)
7. [Handling Documentation-Code Mismatches](#7-handling-documentation-code-mismatches)
8. [Best Practices for AI-Assisted Development](#8-best-practices-for-ai-assisted-development)
9. [Debugging Strategies](#9-debugging-strategies)
10. [Appendix: Command Reference](#10-appendix-command-reference)

---

## 1. Introduction

### 1.1 Purpose

This guide helps developers use **Claude Code** (Anthropic's AI coding assistant) to accelerate development of the Alpha NextGen trading system while maintaining code quality and minimizing debugging time.

### 1.2 Philosophy: AI as a Pair Programmer

Claude Code is not a "code generator" — it's a **pair programmer**. The most effective workflow:

| Human Role | Claude Code Role |
|------------|------------------|
| Define requirements | Implement to spec |
| Review & approve | Explain decisions |
| Test & validate | Debug & fix |
| Maintain architecture | Follow conventions |

### 1.3 Expected Outcomes

By following this guide, you will:
- Reduce development time by 60-80%
- Catch logic errors before they reach production
- Maintain consistency between documentation and code
- Build a test suite alongside implementation

---

## 2. Environment Setup

### 2.1 Prerequisites

| Tool | Version | Purpose |
|------|---------|---------|
| Python | 3.11+ | Local development |
| VS Code | Latest | Primary IDE |
| Claude Code | Latest | AI assistant |
| Git | 2.40+ | Version control |
| Docker | Optional | Local LEAN testing |

### 2.2 Install Claude Code

```bash
# Install Claude Code CLI
npm install -g @anthropic-ai/claude-code

# Verify installation
claude --version

# Authenticate (opens browser)
claude auth login
```

### 2.3 VS Code Extensions

Install these extensions for optimal development:

```
# Required
ms-python.python              # Python language support
ms-python.vscode-pylance      # Type checking
github.copilot               # Optional: Disable during Claude Code sessions

# Recommended
ms-azuretools.vscode-docker   # Docker support (for local LEAN)
mechatroner.rainbow-csv       # CSV viewing for test data
yzhang.markdown-all-in-one    # Documentation editing
```

### 2.4 For New Developers (Start Here)

**Use this section if you are joining an existing Alpha NextGen project.**

```bash
# 1. Clone the repository
git clone git@github.com:vickdavinci/alpha-nextgen.git
cd alpha-nextgen

# 2. Switch to develop branch (main is protected)
git checkout develop
git pull origin develop

# 3. Create virtual environment (in project root)
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or: venv\Scripts\activate  # Windows

# 4. Install dependencies from existing requirements.txt
pip install -r requirements.txt

# 5. Verify setup
python scripts/validate_config.py
pytest tests/ -v  # Shows "no tests yet" - this is expected

# 6. Create your feature branch
git checkout -b feature/your-feature-name
```

**Notes:**
- The `venv/` directory is already in `.gitignore` - do not commit it
- Always branch from `develop`, not `main`
- See Section 4 for Lean CLI / QuantConnect setup

### 2.5 Python Environment Details

| Item | Location | Notes |
|------|----------|-------|
| Virtual environment | `./venv/` (project root) | Already gitignored |
| Dependencies | `./requirements.txt` | Pre-configured, just run `pip install -r` |
| Python version | `.python-version` | Specifies 3.11 (for pyenv users) |

**Activating the environment (each terminal session):**

```bash
# Linux/Mac
source venv/bin/activate

# Windows (Command Prompt)
venv\Scripts\activate

# Windows (PowerShell)
venv\Scripts\Activate.ps1
```

**Deactivating when done:**

```bash
deactivate
```

---

### 2.6 For Project Maintainers (Historical Reference)

> **Note:** This section documents how the project was initially created. New developers should use Section 2.4 instead.

<details>
<summary>Click to expand original project setup steps</summary>

```bash
# Create project directory
mkdir alpha-nextgen
cd alpha-nextgen

# Initialize Git
git init

# Create directory structure
mkdir -p engines portfolio execution data models persistence scheduling utils tests/scenarios docs

# Create initial files
touch main.py config.py requirements.txt
touch CLAUDE.md ERRORS.md QC_RULES.md

# Create requirements.txt
cat > requirements.txt << 'EOF'
# QuantConnect uses its own environment
# These are for LOCAL development/testing only
pytest>=7.0.0
pytest-cov>=4.0.0
black>=23.0.0
isort>=5.12.0
mypy>=1.0.0
EOF

# Copy documentation files to docs/
# (Move all spec files: 00-table-of-contents.md through 17-appendix-glossary.md)
```

</details>

---

## 3. Claude Code Configuration

### 3.1 Initialize Claude Code in Project

```bash
# Navigate to project root
cd alpha_nextgen

# Initialize Claude Code
claude init

# This creates .claude/ directory with configuration
```

### 3.2 Configure CLAUDE.md

The `CLAUDE.md` file is **critical** — Claude Code reads it automatically. Ensure it contains:

1. **Component Map** — Links engines to spec files
2. **Critical Rules** — Authority hierarchy, overnight safety
3. **Coding Conventions** — Patterns to follow
4. **Quick Reference** — Times, thresholds, limits

See the generated `CLAUDE.md` for the complete template.

### 3.3 Create .claude/config.json

```json
{
  "project_name": "alpha_nextgen",
  "language": "python",
  "framework": "quantconnect",
  "documentation_path": "./docs",
  "test_command": "pytest tests/ -v",
  "lint_command": "black --check . && isort --check .",
  "type_check_command": "mypy engines/ portfolio/ --ignore-missing-imports",
  "conventions": {
    "docstring_style": "google",
    "max_line_length": 100,
    "use_type_hints": true
  },
  "critical_files": [
    "CLAUDE.md",
    "ERRORS.md", 
    "QC_RULES.md",
    "config.py"
  ]
}
```

### 3.4 Configure .claudeignore

```
# Ignore these paths
venv/
__pycache__/
*.pyc
.git/
.pytest_cache/
*.egg-info/
dist/
build/

# Don't ignore docs - Claude needs them!
# !docs/
```

---

## 4. QuantConnect Integration

> **First-Time Setup Checklist:**
> - [ ] Create QuantConnect account
> - [ ] Get API credentials (User ID + Token)
> - [ ] Install Lean CLI
> - [ ] Authenticate with `lean login`
> - [ ] Create/link QC project
> - [ ] Configure `.leanignore`
> - [ ] Run first cloud backtest

---

### 4.1 QuantConnect Account Setup

**Step 1: Create Account**

1. Go to [https://www.quantconnect.com/](https://www.quantconnect.com/)
2. Click "Sign Up" → Create account (email or GitHub)
3. Verify your email

**Step 2: Understand Subscription Tiers**

| Tier | Backtests | Live Algorithms | Data | Cost |
|------|:---------:|:---------------:|------|:----:|
| **Free** | 10/month | 1 | US Equities (minute) | $0 |
| **Quant Researcher** | Unlimited | 1 | + Options, Forex | $8/mo |
| **Team** | Unlimited | 10 | + Crypto, Futures | $24/mo |

> **Note:** Free tier is sufficient for development. Upgrade when ready for live trading.

**Step 3: Get API Credentials**

1. Log in to QuantConnect
2. Click your profile icon (top right) → **Account**
3. Navigate to **API Access** tab
4. Click **Generate API Token**
5. **Save securely:**
   - User ID: `XXXXXX` (6-digit number)
   - API Token: `XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX` (32-character string)

> **Security:** Never commit credentials to Git. Store in environment variables or `~/.lean/credentials`.

---

### 4.2 Lean CLI Installation & Authentication

**Step 1: Install Lean CLI**

```bash
# Install (use your project's venv)
pip install lean

# Verify installation
lean --version
# Expected output: lean, version X.X.X
```

**Step 2: Authenticate**

```bash
# Login to QuantConnect (interactive)
lean login

# Or use command-line arguments
lean login --user-id YOUR_USER_ID --api-token YOUR_API_TOKEN
```

**Step 3: Verify Authentication**

```bash
# Check who you're logged in as
lean whoami
# Expected: You are logged in as: your-email@example.com (User ID: XXXXXX)
```

**Credential Storage:**

| Location | Purpose |
|----------|---------|
| `~/.lean/credentials` | Stores your QC credentials (auto-created by `lean login`) |
| `./lean.json` | Project-specific Lean configuration (auto-created by `lean init`) |

**Troubleshooting Authentication:**

| Error | Solution |
|-------|----------|
| "Invalid credentials" | Regenerate API token on QC website |
| "Network error" | Check internet connection, try `lean logout` then `lean login` |
| "Token expired" | Tokens don't expire, but regenerate if issues persist |

---

### 4.3 Creating a QuantConnect Project

You can create a QC project in two ways:

**Option A: Create via Lean CLI (Recommended)**

```bash
# Navigate to your local repo
cd /path/to/alpha-nextgen

# Initialize Lean in this directory
lean init

# This creates:
# - lean.json (project configuration)
# - .lean/ (local Lean data cache - gitignored)
```

**Option B: Create via QC Web Interface**

1. Log in to QuantConnect
2. Click **Algorithm Lab** → **Create New Algorithm**
3. Name it: `alpha-nextgen`
4. Choose **Python** as language
5. Link locally:
   ```bash
   lean cloud pull --project "alpha-nextgen"
   ```

**Project Naming Convention:**

| Local Directory | QC Project Name | Notes |
|-----------------|-----------------|-------|
| `alpha-nextgen/` | `alpha-nextgen` | Keep names identical to avoid confusion |

---

### 4.4 File Synchronization Strategy

**What Gets Synced to QuantConnect Cloud:**

| Path | Syncs? | Reason |
|------|:------:|--------|
| `main.py` | ✅ Yes | Algorithm entry point |
| `config.py` | ✅ Yes | Parameters needed at runtime |
| `engines/*.py` | ✅ Yes | Core trading logic |
| `portfolio/*.py` | ✅ Yes | Portfolio management |
| `execution/*.py` | ✅ Yes | Order handling |
| `models/*.py` | ✅ Yes | Data structures |
| `persistence/*.py` | ✅ Yes | State management |
| `scheduling/*.py` | ✅ Yes | Event scheduling |
| `utils/*.py` | ✅ Yes | Helper functions |
| `data/*.py` | ✅ Yes | Data management |
| `tests/` | ❌ No | Local testing only |
| `docs/` | ❌ No | Documentation only |
| `scripts/` | ❌ No | Local utilities only |
| `venv/` | ❌ No | Local environment |
| `.github/` | ❌ No | CI/CD configuration |
| `.claude/` | ❌ No | Claude Code config |
| `archive/` | ❌ No | Archived files |
| `*.md` (root) | ❌ No | Documentation |

**Configure `.leanignore`:**

Create this file in your project root to prevent unwanted files from syncing:

```bash
# .leanignore - Files to exclude from QuantConnect sync

# Testing
tests/
scripts/

# Documentation
docs/
*.md

# Local development
venv/
.venv/
__pycache__/
*.pyc
.pytest_cache/

# IDE and tools
.claude/
.github/
.git/
.vscode/
.idea/

# Archives and backups
archive/
*.bak

# Lean local files
.lean/
backtests/
live/
storage/

# OS files
.DS_Store
Thumbs.db
```

**Sync Commands:**

```bash
# Push local changes to QC cloud
lean cloud push

# Pull changes from QC cloud (if edited on web)
lean cloud pull

# Check sync status
lean cloud status
```

---

### 4.5 Project Structure Mapping

**How Our Structure Maps to QuantConnect:**

```
LOCAL (alpha-nextgen/)              QC CLOUD (alpha-nextgen)
========================            ========================
main.py ─────────────────────────── main.py (entry point)
config.py ───────────────────────── config.py
engines/ ────────────────────────── engines/
  ├── __init__.py                     ├── __init__.py
  ├── regime_engine.py                ├── regime_engine.py
  └── ...                             └── ...
portfolio/ ──────────────────────── portfolio/
models/ ─────────────────────────── models/
execution/ ──────────────────────── execution/
persistence/ ────────────────────── persistence/
scheduling/ ─────────────────────── scheduling/
utils/ ──────────────────────────── utils/
data/ ───────────────────────────── data/

tests/           ─────── NOT SYNCED (local only)
docs/            ─────── NOT SYNCED (local only)
scripts/         ─────── NOT SYNCED (local only)
.github/         ─────── NOT SYNCED (local only)
```

**Important: `__init__.py` Files**

Every directory synced to QC **must** have an `__init__.py` file for imports to work:

```bash
# Verify all required __init__.py files exist
ls engines/__init__.py
ls portfolio/__init__.py
ls models/__init__.py
ls execution/__init__.py
ls persistence/__init__.py
ls scheduling/__init__.py
ls utils/__init__.py
ls data/__init__.py
```

**QC Python Environment:**

QuantConnect uses a specific Python environment. Available libraries include:

| Library | Available | Notes |
|---------|:---------:|-------|
| `numpy` | ✅ | Full support |
| `pandas` | ✅ | Full support |
| `scipy` | ✅ | Full support |
| `sklearn` | ✅ | Machine learning |
| `ta-lib` | ✅ | Technical analysis |
| `requests` | ❌ | No external HTTP calls |
| `custom packages` | ❌ | Cannot pip install on QC |

> **Rule:** Only use standard libraries and QC-provided packages. Check [QC Documentation](https://www.quantconnect.com/docs) for full list.

**Time Zone Handling:**

| Context | Time Zone | Example |
|---------|-----------|---------|
| QC Internal | UTC | `self.Time` returns UTC |
| US Markets | Eastern | Convert for schedule events |
| Our Code | Eastern | All times in docs are ET |

```python
# Converting to Eastern Time in QC
eastern = self.Time.ConvertFromUtc(TimeZones.NewYork)
```

---

### 4.6 Running Backtests

**Cloud Backtest (Recommended for Development):**

```bash
# Run backtest with default settings
lean cloud backtest "alpha-nextgen"

# Run with custom name (for tracking)
lean cloud backtest "alpha-nextgen" --name "Phase1-RegimeTest"

# Run and open results in browser
lean cloud backtest "alpha-nextgen" --open
```

**Backtest Configuration:**

Configure in `main.py` `Initialize()` method:

```python
def Initialize(self):
    # Backtest date range
    self.SetStartDate(2020, 1, 1)
    self.SetEndDate(2024, 1, 1)

    # Starting capital
    self.SetCash(50000)

    # Benchmark for comparison
    self.SetBenchmark("SPY")

    # Brokerage model (affects fills, fees)
    self.SetBrokerageModel(BrokerageName.InteractiveBrokersBrokerage)
```

**Reading Backtest Results:**

After a backtest completes, review:

| Metric | What It Tells You |
|--------|-------------------|
| **Total Return** | Overall profit/loss percentage |
| **Sharpe Ratio** | Risk-adjusted return (target: > 1.0) |
| **Max Drawdown** | Largest peak-to-trough decline (target: < 20%) |
| **Win Rate** | Percentage of profitable trades |
| **Trades** | Total number of trades executed |

**Viewing Logs:**

```bash
# Pull backtest logs
lean cloud pull

# Logs are in: ./backtests/[backtest-id]/
```

In your code, use `self.Log()` for debugging:

```python
self.Log(f"REGIME_SCORE: {score} | STATE: {state}")
```

---

### 4.7 Local Backtesting with Docker

**Prerequisites:**

1. Install Docker Desktop: [https://www.docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop)
2. Ensure Docker is running

**Setup Local Data:**

```bash
# Download required data (one-time setup)
lean data download --dataset "US Equities" --resolution "Minute"

# This downloads to ~/.lean/data/ (can be several GB)
```

**Run Local Backtest:**

```bash
# Run backtest locally
lean backtest "alpha-nextgen"

# Run with specific config
lean backtest "alpha-nextgen" --output ./backtests/local/

# Generate HTML report
lean report
```

**Local vs Cloud Backtesting:**

| Aspect | Local (Docker) | Cloud |
|--------|:--------------:|:-----:|
| Speed | Slower (depends on your hardware) | Faster (QC servers) |
| Data | Must download (~GB) | Available instantly |
| Cost | Free (unlimited) | Limited on free tier |
| Debugging | Better (full logs) | Limited logs |
| Recommended | Complex debugging | Regular development |

---

### 4.8 Testing Workflow

```
┌─────────────────────┐     ┌─────────────────────┐     ┌─────────────────────┐
│   LOCAL TESTING     │     │   CLOUD BACKTEST    │     │   PAPER TRADING     │
│                     │     │                     │     │                     │
│  1. Unit Tests      │     │  1. Push code       │     │  1. Connect broker  │
│     pytest tests/   │────▶│     lean cloud push │────▶│  2. Deploy algo     │
│                     │     │                     │     │                     │
│  2. Validate Config │     │  2. Run backtest    │     │  3. Monitor trades  │
│     validate_config │     │     lean cloud      │     │     (real-time)     │
│                     │     │     backtest        │     │                     │
│  3. Type Check      │     │                     │     │  4. Review results  │
│     mypy engines/   │     │  3. Review results  │     │                     │
└─────────────────────┘     └─────────────────────┘     └─────────────────────┘
         │                           │                           │
         ▼                           ▼                           ▼
    Logic Correct?           Behavior Correct?          Ready for Live?
    (Syntax, Types)          (Trading Logic)            (Real Money)
```

**Workflow Commands:**

```bash
# Step 1: Local validation
pytest tests/ -v
python scripts/validate_config.py
mypy engines/ portfolio/ models/ --ignore-missing-imports

# Step 2: Cloud backtest
lean cloud push
lean cloud backtest "alpha-nextgen" --name "Test-$(date +%Y%m%d)"

# Step 3: Review and iterate
lean cloud pull  # Get results
# Review logs, fix issues, repeat
```

---

### 4.9 Paper Trading Setup

> **Prerequisite:** Backtest shows acceptable performance (Sharpe > 1.0, Drawdown < 20%)

**Step 1: Connect Interactive Brokers**

1. Have an IBKR account (paper trading enabled)
2. In QC: **Algorithm Lab** → **Live Trading** → **Add Brokerage**
3. Select **Interactive Brokers**
4. Follow OAuth flow to connect

**Step 2: Deploy to Paper**

```bash
# Deploy algorithm to paper trading
lean live "alpha-nextgen" --brokerage "Interactive Brokers" --environment paper
```

Or via QC Web Interface:
1. Open your algorithm in Algorithm Lab
2. Click **Go Live** → Select **Paper Trading**
3. Configure settings and deploy

**Step 3: Monitor Paper Trades**

- QC Dashboard shows real-time positions
- Orders appear as they execute
- Use `self.Log()` statements for debugging

**Paper Trading Checklist:**

- [ ] Backtest Sharpe > 1.0
- [ ] Max Drawdown < 20%
- [ ] No runtime errors in backtest
- [ ] Kill switch tested (triggers correctly)
- [ ] Overnight positions correct (no TQQQ/SOXL)
- [ ] IBKR paper account has sufficient funds

---

### 4.10 Troubleshooting

**Common Lean CLI Errors:**

| Error | Cause | Solution |
|-------|-------|----------|
| `No such project` | Project doesn't exist on QC | Run `lean cloud push` first to create it |
| `Authentication failed` | Invalid/expired token | Run `lean logout` then `lean login` |
| `Import error: No module named 'engines'` | Missing `__init__.py` | Ensure all directories have `__init__.py` |
| `Rate limit exceeded` | Too many API calls | Wait a few minutes, try again |
| `Docker not running` | Docker Desktop not started | Start Docker Desktop |

**Common Runtime Errors:**

| Error | Cause | Solution |
|-------|-------|----------|
| `Indicator not ready` | Accessing indicator before warmup | Check `indicator.IsReady` before use |
| `Symbol not found` | Missing `AddEquity()` call | Add symbol in `Initialize()` |
| `Division by zero` | Unchecked denominator | Add zero checks before division |
| `No data for symbol` | Wrong date range or delisted symbol | Check data availability on QC |

> **Full Error Reference:** See [ERRORS.md](ERRORS.md) for comprehensive troubleshooting guide.

**Getting Help:**

1. **QC Documentation:** [https://www.quantconnect.com/docs](https://www.quantconnect.com/docs)
2. **QC Forum:** [https://www.quantconnect.com/forum](https://www.quantconnect.com/forum)
3. **Lean CLI Docs:** [https://www.quantconnect.com/docs/v2/lean-cli](https://www.quantconnect.com/docs/v2/lean-cli)

---

## 5. Incremental Development Workflow

> **Core Principle:** Build one component at a time, test it, commit it, then move to the next. Never ask for "the entire system" in one prompt.

---

### 5.1 Development Philosophy

**The Golden Rule:**

```
✅ GOOD: "Implement RegimeEngine based on docs/04-regime-engine.md"
✅ GOOD: "Review engines/trend_engine.py and fix any deviations from spec"
✅ GOOD: "Add unit tests for CapitalEngine covering phase transitions"

❌ BAD: "Build the entire trading system"
❌ BAD: "Implement all strategy engines"
❌ BAD: "Make everything work"
```

**Key Principles:**

| Principle | Description |
|-----------|-------------|
| **Spec-First** | Always read the spec document before implementing or modifying |
| **Authority Hierarchy** | Only Portfolio Router places orders—strategies emit TargetWeight |
| **No Magic Numbers** | All values come from `config.py`, never hardcoded |
| **Test Before Commit** | Run relevant tests before committing any changes |
| **Atomic Commits** | One logical change per commit (one component, one fix) |

---

### 5.2 Understanding Codebase State

Before implementing anything, understand what already exists.

**Quick Status Check:**

```bash
# Check if files exist and their sizes
find . -name "*.py" -path "./engines/*" -exec wc -l {} \;
find . -name "*.py" -path "./portfolio/*" -exec wc -l {} \;
find . -name "*.py" -path "./models/*" -exec wc -l {} \;

# Check for TODO/FIXME markers
grep -r "TODO\|FIXME\|STUB\|NotImplemented" engines/ portfolio/ models/
```

**File State Classification:**

| State | Indicators | Action |
|-------|------------|--------|
| **Empty** | Only imports or docstring, < 20 lines | Implement from scratch per spec |
| **Stub** | Class defined, methods raise `NotImplementedError` | Implement method bodies per spec |
| **Partial** | Some methods work, others incomplete | Complete remaining methods per spec |
| **Complete** | Fully implemented | Review for spec compliance, add tests |
| **Outdated** | Works but doesn't match current spec | Update to match spec |

**Review Checklist for Existing Code:**

```markdown
□ Does it follow CLAUDE.md conventions?
□ Does it use config.py parameters (no hardcoded values)?
□ Does it have type hints on all methods?
□ Does it have docstrings?
□ Does it match the spec document?
□ Are there any TODO/FIXME comments?
□ Does it have corresponding unit tests?
```

---

### 5.3 Component Development Order

Follow this dependency chain. **Components within the same phase can be developed in parallel** if they don't depend on each other.

```
Phase 1: Foundation (No dependencies - can be parallel)
┌─────────────────────────────────────────────────────────────┐
│  1. config.py            All tunable parameters             │
│  2. models/enums.py      RegimeState, CapitalPhase, etc.    │
│  3. models/target_weight.py   Core signal data structure    │
│  4. utils/calculations.py     Helper functions              │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
Phase 2: Core Engines (Depend on Phase 1 - can be parallel)
┌─────────────────────────────────────────────────────────────┐
│  5. engines/regime_engine.py     Market state scoring       │
│  6. engines/capital_engine.py    Phase & lockbox management │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
Phase 3: Strategy Engines (Depend on Phase 2 - can be parallel)
┌─────────────────────────────────────────────────────────────┐
│  7. engines/cold_start_engine.py   Days 1-5 warm entry      │
│  8. engines/trend_engine.py        BB breakout signals      │
│  9. engines/mean_reversion_engine.py  RSI oversold signals  │
│  10. engines/hedge_engine.py       TMF/PSQ allocation       │
│  11. engines/yield_sleeve.py       SHV cash management      │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
Phase 4: Coordination (Depends on Phase 3 - SEQUENTIAL)
┌─────────────────────────────────────────────────────────────┐
│  12. portfolio/exposure_groups.py   Exposure limits         │
│  13. portfolio/portfolio_router.py  Central coordinator     │  ← ORDER MATTERS
│  14. engines/risk_engine.py         Circuit breakers        │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
Phase 5: Execution & Persistence (Depends on Phase 4)
┌─────────────────────────────────────────────────────────────┐
│  15. execution/execution_engine.py   Order submission       │
│  16. persistence/state_manager.py    ObjectStore save/load  │
│  17. scheduling/daily_scheduler.py   Time-based events      │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
Phase 6: Integration
┌─────────────────────────────────────────────────────────────┐
│  18. main.py                Wire everything together        │
└─────────────────────────────────────────────────────────────┘
```

**Definition of Done (Per Phase):**

| Phase | Done When... |
|-------|--------------|
| Phase 1 | All parameters in config.py, enums defined, TargetWeight works |
| Phase 2 | Regime returns scores 0-100, Capital tracks phases correctly |
| Phase 3 | Each strategy emits valid TargetWeight, tests pass |
| Phase 4 | Router aggregates signals, Risk blocks bad trades |
| Phase 5 | Orders submit correctly, state persists across restarts |
| Phase 6 | Full backtest runs without errors |

---

### 5.4 Spec-First Development Cycle

For **each component**, follow this cycle:

```
┌───────────────────────────────────────────────────────────────────────┐
│                    SPEC-FIRST DEVELOPMENT CYCLE                        │
└───────────────────────────────────────────────────────────────────────┘

  ┌──────────┐      ┌──────────┐      ┌──────────┐      ┌──────────┐
  │  1.PREP  │─────▶│ 2.PROMPT │─────▶│3.IMPLEMENT│─────▶│ 4.REVIEW │
  └──────────┘      └──────────┘      └──────────┘      └──────────┘
       │                                                      │
       │                                                      ▼
       │                                               ┌──────────┐
       │                                               │ 5. TEST  │
       │                                               └──────────┘
       │                                                      │
       │           ┌──────────┐      ┌──────────┐            │
       └───────────│7.ITERATE │◀─────│6.VALIDATE│◀───────────┘
                   └──────────┘      └──────────┘
                        │
                        ▼ (if passed)
                   ┌──────────┐
                   │ 8.COMMIT │
                   └──────────┘
```

**Step Details:**

**Step 1: PREP (You)**
```markdown
□ Read the spec file completely (e.g., docs/04-regime-engine.md)
□ Identify inputs (what data/dependencies it needs)
□ Identify outputs (what it returns/emits)
□ Note any ambiguities to clarify with Claude
□ Check if file exists and its current state
```

**Step 2: PROMPT (You → Claude)**
```
"Read docs/04-regime-engine.md and implement engines/regime_engine.py.

Current state: [EMPTY | STUB | PARTIAL | describe what exists]

Requirements:
- Follow CLAUDE.md conventions
- Use parameters from config.py (no hardcoded values)
- Include type hints and docstrings
- Do NOT implement dependencies - import and use them

If any spec details are unclear, ask before implementing."
```

**Step 3: IMPLEMENT (Claude)**
- Claude reads the spec
- Claude generates/modifies code
- Claude explains key design decisions
- Claude flags any spec ambiguities

**Step 4: REVIEW (You)**
```markdown
□ Logic matches spec exactly
□ Parameter names match config.py
□ No hardcoded values (magic numbers)
□ Type hints present on all methods
□ Docstrings explain purpose
□ No unauthorized order placement (if strategy engine)
```

**Step 5: TEST (You + Claude)**
```
"Write unit tests for RegimeEngine covering:
- Score boundaries (RISK_ON at 70, etc.)
- Smoothing calculation
- Edge cases (0, 100, negative inputs)
- Invalid inputs should raise/handle gracefully"
```

**Step 6: VALIDATE (You)**
```bash
# Run specific tests
pytest tests/test_regime_engine.py -v

# Type check
mypy engines/regime_engine.py --ignore-missing-imports
```

**Step 7: ITERATE (If Needed)**
- If tests fail → fix and retest
- If review finds issues → return to Step 2 with specific feedback

**Step 8: COMMIT (You)**
```bash
git add engines/regime_engine.py tests/test_regime_engine.py
git commit -m "Implement RegimeEngine with 4-factor scoring

- Trend, volatility, breadth, credit factors
- Exponential smoothing (alpha=0.3)
- Score boundaries: RISK_ON≥70, RISK_OFF<30
- Unit tests for all boundaries and smoothing"
```

---

### 5.5 Git Workflow Integration

**Branch Strategy:**

```
main                    Production-ready code
  │
  └── develop           Active development
        │
        ├── feature/regime-engine     One branch per component
        ├── feature/capital-engine
        └── fix/trend-engine-stop-calc
```

**Recommended Workflow:**

```bash
# Start new component
git checkout develop
git pull origin develop
git checkout -b feature/regime-engine

# Work on component (multiple commits OK)
git add engines/regime_engine.py
git commit -m "Add RegimeEngine skeleton with factor methods"

git add engines/regime_engine.py
git commit -m "Implement 4-factor scoring calculation"

git add tests/test_regime_engine.py
git commit -m "Add unit tests for RegimeEngine"

# When complete, merge to develop
git checkout develop
git merge feature/regime-engine
git push origin develop

# Clean up
git branch -d feature/regime-engine
```

**Commit Message Convention:**

> **Authoritative Source:** See `CONTRIBUTING.md` → "Commit Message Standards" for the complete specification.

**Quick Reference:**

```
<type>: <short description>

Types: feat, fix, refactor, test, docs, chore, style, perf
```

**Examples:**

```bash
# Good commit messages
git commit -m "feat: implement RegimeEngine 4-factor scoring"
git commit -m "fix: correct Chandelier stop calculation in TrendEngine"

# Bad commit messages
git commit -m "updates"
git commit -m "WIP"
```

**Full Details:** Subject line rules, body format, multi-line commits → see `CONTRIBUTING.md`

---

### 5.6 Sample Prompts by Phase

**Phase 1: Foundation**

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Prompt: config.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"Read docs/16-appendix-parameters.md completely.

Create/update config.py with ALL parameters organized by section:
- Regime Engine parameters
- Capital Engine parameters
- Risk Engine parameters
- Strategy Engine parameters (Trend, MR, Hedge, Yield)
- Execution parameters
- Scheduling parameters

Requirements:
- Use exact variable names from the spec
- Group related parameters with comment headers
- Include inline comments explaining each parameter
- Use appropriate types (int, float, Decimal for money)"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Prompt: models/enums.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"Create models/enums.py with these enumerations:

1. RegimeState - RISK_ON, NEUTRAL, CAUTIOUS, DEFENSIVE, RISK_OFF
2. CapitalPhase - SEED, GROWTH
3. Urgency - IMMEDIATE, EOD
4. Strategy - TREND, MEAN_REV, HEDGE, YIELD, COLD_START
5. OrderType - MARKET, MOO (Market-on-Open)
6. ExposureGroup - NASDAQ_BETA, SPY_BETA, RATES

Use Python's Enum class. Include docstrings explaining:
- What each enum represents
- When each value is used"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Prompt: models/target_weight.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"Read docs/11-portfolio-router.md Section 11.2.

Create models/target_weight.py with a TargetWeight dataclass:
- symbol: str
- weight: float (0.0 to 1.0, where 0.0 = exit)
- strategy: Strategy enum
- urgency: Urgency enum
- reason: str (human-readable explanation)
- timestamp: datetime (when signal was generated)

Add:
- __repr__ for debugging output
- is_exit property (True if weight == 0.0)
- validation in __post_init__ (weight must be 0.0-1.0)"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Phase 2: Core Engines**

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Prompt: engines/regime_engine.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"Read docs/04-regime-engine.md completely.

Implement engines/regime_engine.py with a RegimeEngine class:

Methods required:
- __init__(self, algorithm) - Store QC algorithm reference
- calculate_trend_factor(self) -> float - SPY vs 50/200 SMA
- calculate_volatility_factor(self) -> float - VIX level assessment
- calculate_breadth_factor(self) -> float - Advance/decline ratio
- calculate_credit_factor(self) -> float - HYG/LQD spread
- calculate_regime_score(self) -> float - Weighted combination (0-100)
- apply_smoothing(self, raw: float) -> float - EMA smoothing
- get_regime_state(self) -> RegimeState - Classify score to state
- update(self) -> RegimeState - Called each bar, returns current state

Requirements:
- Use parameters from config.py (REGIME_* variables)
- Store previous_smoothed_score for smoothing
- Return RegimeState enum (not raw score) from get_regime_state()
- Log score changes: self.algorithm.Log(f'REGIME: score={score} state={state}')"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Prompt: engines/capital_engine.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"Read docs/05-capital-engine.md completely.

Implement engines/capital_engine.py with a CapitalEngine class:

Methods required:
- __init__(self, algorithm, initial_capital: float)
- get_phase(self) -> CapitalPhase - SEED if equity < threshold, else GROWTH
- get_lockbox_amount(self) -> float - Protected capital amount
- get_tradeable_equity(self) -> float - Total equity minus lockbox
- set_baseline(self, amount: float) - Set daily baseline for drawdown calc
- get_daily_drawdown(self) -> float - Current drawdown from baseline
- update_high_water_mark(self) - Track peak equity
- on_day_start(self) - Called at market open
- on_day_end(self) - Called at market close

Requirements:
- Use CAPITAL_* parameters from config.py
- Lockbox is NEVER available for trading
- Phase transitions logged: self.algorithm.Log(f'CAPITAL_PHASE: {old} -> {new}')"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Phase 3: Strategy Engines**

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Prompt: engines/trend_engine.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"Read docs/07-trend-engine.md completely.

Implement engines/trend_engine.py with a TrendEngine class:

CRITICAL RULE: This engine ONLY emits TargetWeight objects.
It NEVER calls MarketOrder, MarketOnOpenOrder, or Liquidate.

Methods required:
- __init__(self, algorithm, regime_engine, capital_engine)
- check_compression(self, symbol: str) -> bool - BB bandwidth < threshold
- check_breakout(self, symbol: str) -> bool - Price breaks upper BB
- calculate_position_size(self, symbol: str) -> float - Weight based on regime
- update_chandelier_stop(self, symbol: str) - Trail stop, never lower
- generate_entry_signals(self) -> List[TargetWeight] - Check QLD/SSO for entries
- generate_exit_signals(self) -> List[TargetWeight] - Check stops, targets
- generate_signals(self) -> List[TargetWeight] - Main method, combines both

Requirements:
- Symbols: QLD (2× Nasdaq), SSO (2× S&P)
- Use TREND_* parameters from config.py
- Position size scales with regime score
- Exit if stop hit OR target reached OR regime deteriorates"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Prompt: engines/mean_reversion_engine.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"Read docs/08-mean-reversion-engine.md completely.

Implement engines/mean_reversion_engine.py with a MeanReversionEngine class:

CRITICAL RULES:
1. This engine ONLY emits TargetWeight objects (no order placement)
2. TQQQ/SOXL are INTRADAY ONLY - must exit by 15:45 ET

Methods required:
- __init__(self, algorithm, regime_engine, capital_engine)
- check_oversold(self, symbol: str) -> bool - RSI(5) < 25
- check_entry_window(self) -> bool - 10:00-15:00 ET only
- check_gap_filter(self) -> bool - SPY gap not worse than -1.5%
- generate_entry_signals(self) -> List[TargetWeight]
- generate_exit_signals(self) -> List[TargetWeight] - Include TIME_EXIT at 15:45
- generate_signals(self) -> List[TargetWeight]

Requirements:
- Symbols: TQQQ (3× Nasdaq), SOXL (3× Semi)
- Use MR_* parameters from config.py
- Force exit at 15:45 ET regardless of profit/loss
- Respect gap filter (block entries if SPY gaps down >1.5%)"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Phase 4: Coordination**

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Prompt: portfolio/portfolio_router.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"Read docs/11-portfolio-router.md completely.

Implement portfolio/portfolio_router.py with a PortfolioRouter class:

THIS IS THE ONLY CLASS AUTHORIZED TO PLACE ORDERS.

Methods required:
- __init__(self, algorithm, risk_engine, capital_engine, exposure_groups)
- collect_signals(self, *engines) -> List[TargetWeight] - Gather from all engines
- validate_signal(self, signal: TargetWeight) -> bool - Check with risk engine
- check_exposure_limits(self, signals: List[TargetWeight]) -> List[TargetWeight]
- net_signals(self, signals: List[TargetWeight]) -> List[TargetWeight]
- execute_signals(self, signals: List[TargetWeight]) - Place actual orders
- process_cycle(self) -> None - Main entry point, called each minute

Order placement methods (ONLY place these calls HERE):
- _place_market_order(self, symbol, quantity)
- _place_moo_order(self, symbol, quantity)
- _liquidate(self, symbol)

Requirements:
- Always check risk_engine.check_kill_switch() FIRST
- Log all order placements with full context
- Respect exposure group limits
- Handle both IMMEDIATE and EOD urgency signals"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

### 5.7 Checkpoint Validation

**Run these checks after completing each phase:**

```bash
# ═══════════════════════════════════════════════════════════════════
# Phase 1: Foundation
# ═══════════════════════════════════════════════════════════════════
python -c "from config import *; print('config.py OK')"
python -c "from models.enums import *; print('enums.py OK')"
python -c "from models.target_weight import TargetWeight; print('target_weight.py OK')"
pytest tests/test_config.py tests/test_models.py -v

# ═══════════════════════════════════════════════════════════════════
# Phase 2: Core Engines
# ═══════════════════════════════════════════════════════════════════
python -c "from engines.regime_engine import RegimeEngine; print('regime_engine.py OK')"
python -c "from engines.capital_engine import CapitalEngine; print('capital_engine.py OK')"
pytest tests/test_regime_engine.py tests/test_capital_engine.py -v

# ═══════════════════════════════════════════════════════════════════
# Phase 3: Strategy Engines
# ═══════════════════════════════════════════════════════════════════
pytest tests/test_trend_engine.py -v
pytest tests/test_mr_engine.py -v
pytest tests/test_hedge_engine.py -v
pytest tests/test_yield_sleeve.py -v

# ═══════════════════════════════════════════════════════════════════
# Phase 4: Coordination
# ═══════════════════════════════════════════════════════════════════
pytest tests/test_exposure_groups.py -v
pytest tests/test_portfolio_router.py -v
pytest tests/test_risk_engine.py -v

# ═══════════════════════════════════════════════════════════════════
# Phase 5: Execution & Persistence
# ═══════════════════════════════════════════════════════════════════
pytest tests/test_execution_engine.py -v
pytest tests/test_state_manager.py -v

# ═══════════════════════════════════════════════════════════════════
# Full Suite (Run before Phase 6)
# ═══════════════════════════════════════════════════════════════════
pytest tests/ -v --tb=short
mypy engines/ portfolio/ models/ --ignore-missing-imports
```

**Phase Completion Checklist:**

```markdown
## Phase X Completion Checklist

### Code Quality
□ All methods have type hints
□ All classes/methods have docstrings
□ No hardcoded values (all from config.py)
□ Logging statements for key events
□ Error handling for edge cases

### Testing
□ Unit tests exist for all public methods
□ Tests cover happy path
□ Tests cover edge cases
□ Tests cover error conditions
□ All tests pass

### Documentation
□ Code matches spec document
□ Any spec deviations documented with reason
□ README updated if needed

### Git
□ Changes committed with descriptive message
□ No untracked files left behind
□ Branch merged to develop (if using branches)
```

---

### 5.8 Resuming Development

**Project State is Tracked in WORKBOARD.md**

All task and ownership state is tracked in `WORKBOARD.md`. This is the single source of truth for who's working on what.

**Resuming a Session:**

```
# ALPHA NEXTGEN SESSION RESUME

Read `WORKBOARD.md` and `CLAUDE.md`.

Then confirm:
1. Current phase and what's in progress
2. Your assigned task
3. The spec document for your task

Ready to continue.
```

**What WORKBOARD.md Contains:**

| Section | Purpose |
|---------|---------|
| Current Sprint | Active phase with task columns |
| In Progress | Tasks being worked on (with owner, branch) |
| Ready to Start | Available tasks to claim |
| Done | Completed tasks |
| Future Phases | Upcoming work with dependencies |

**Workflow:**

```
1. Check WORKBOARD.md for your task
2. Create branch: feature/<initials>/<component>
3. Implement against spec
4. PR to develop
5. After merge: move task to "Done" in WORKBOARD.md
```

**After a Long Break:**

If returning after several days or significant changes:

```
# ALPHA NEXTGEN POST-BREAK RESUME

Run: git log --oneline -10

Read `WORKBOARD.md` for current task status.
Read any files that changed since last session.
Read `CLAUDE.md` to refresh on conventions.

Summarize the current state and what to work on next.
```

---

### 5.9 Common Development Patterns

**Pattern 1: Stubbing Dependencies**

When implementing a component that depends on others not yet built:

```python
# In trend_engine.py, if regime_engine not ready:

class TrendEngine:
    def __init__(self, algorithm, regime_engine=None, capital_engine=None):
        self.algorithm = algorithm
        # Accept None for testing without dependencies
        self.regime_engine = regime_engine
        self.capital_engine = capital_engine

    def _get_regime_score(self) -> float:
        """Get regime score, defaulting to neutral if engine not available."""
        if self.regime_engine is None:
            return 50.0  # Neutral default for testing
        return self.regime_engine.get_current_score()
```

**Pattern 2: Testing Without QC Infrastructure**

Create mock objects for unit testing:

```python
# tests/conftest.py
import pytest
from unittest.mock import MagicMock
from datetime import datetime

@pytest.fixture
def mock_algorithm():
    """Create a mock QC algorithm for testing."""
    algo = MagicMock()
    algo.Time = datetime(2024, 1, 15, 10, 30)  # 10:30 AM
    algo.Portfolio = MagicMock()
    algo.Securities = {}
    algo.Log = MagicMock()  # Capture log calls
    return algo

@pytest.fixture
def mock_regime_engine():
    """Create a mock regime engine."""
    engine = MagicMock()
    engine.get_current_score.return_value = 65.0
    engine.get_regime_state.return_value = RegimeState.NEUTRAL
    return engine

# In tests:
def test_trend_engine_entry(mock_algorithm, mock_regime_engine):
    engine = TrendEngine(mock_algorithm, mock_regime_engine)
    signals = engine.generate_signals()
    # Assert on signals...
```

**Pattern 3: Mocking Market Data**

```python
# tests/helpers.py
def create_mock_security(symbol: str, price: float, invested: bool = False):
    """Create a mock security for testing."""
    security = MagicMock()
    security.Symbol = symbol
    security.Price = price
    security.Close = price
    return security

def create_mock_portfolio_holding(symbol: str, quantity: int, avg_price: float):
    """Create a mock portfolio holding."""
    holding = MagicMock()
    holding.Symbol = symbol
    holding.Quantity = quantity
    holding.AveragePrice = avg_price
    holding.Invested = quantity != 0
    holding.HoldingsValue = quantity * avg_price
    return holding
```

**Pattern 4: Time-Based Testing**

```python
# Testing time-dependent logic (MR window, force close, etc.)
from datetime import datetime
from unittest.mock import patch

def test_mr_entry_window():
    """Test that MR only enters during 10:00-15:00."""
    engine = MeanReversionEngine(mock_algorithm)

    # Before window
    mock_algorithm.Time = datetime(2024, 1, 15, 9, 45)  # 9:45 AM
    assert engine.check_entry_window() == False

    # During window
    mock_algorithm.Time = datetime(2024, 1, 15, 11, 0)  # 11:00 AM
    assert engine.check_entry_window() == True

    # After window
    mock_algorithm.Time = datetime(2024, 1, 15, 15, 30)  # 3:30 PM
    assert engine.check_entry_window() == False
```

---

## 6. Testing & Validation

> **Testing Philosophy:** Every component must be tested in isolation before integration. Tests are documentation—they show how components should behave.

---

### 6.1 Testing Pyramid

```
                         ┌─────────────────┐
                         │   QC Cloud      │  ← Full system backtest
                         │   Backtest      │     (Real market data)
                        ─┴─────────────────┴─
                       ┌─────────────────────┐
                       │   Scenario Tests    │  ← Multi-component flows
                       │   (End-to-End)      │     (Kill switch, overnight, etc.)
                      ─┴─────────────────────┴─
                     ┌───────────────────────────┐
                     │    Integration Tests      │  ← Component interactions
                     │  (Engine + Router, etc.)  │     (Signal flow, coordination)
                    ─┴───────────────────────────┴─
                   ┌─────────────────────────────────┐
                   │         Unit Tests              │  ← Single method/class
                   │  (RegimeEngine.classify_score)  │     (Isolated logic)
                  ─┴─────────────────────────────────┴─
                 ┌───────────────────────────────────────┐
                 │         Static Analysis               │  ← Type checking, linting
                 │   (mypy, black, isort, flake8)        │     (Code quality)
                ─┴───────────────────────────────────────┴─
```

**Test Distribution Target:**

| Level | Percentage | Run When |
|-------|:----------:|----------|
| Static Analysis | - | Every save (IDE) |
| Unit Tests | 70% | Every commit |
| Integration Tests | 20% | Every PR |
| Scenario Tests | 8% | Before merge |
| QC Backtest | 2% | After major changes |

---

### 6.2 Test Organization

**Directory Structure:**

```
tests/
├── __init__.py
├── conftest.py              # Shared fixtures
├── pytest.ini               # Pytest configuration
│
├── unit/                    # Unit tests (isolated)
│   ├── __init__.py
│   ├── test_config.py
│   ├── test_enums.py
│   ├── test_target_weight.py
│   ├── test_regime_engine.py
│   ├── test_capital_engine.py
│   ├── test_trend_engine.py
│   ├── test_mr_engine.py
│   ├── test_hedge_engine.py
│   ├── test_yield_sleeve.py
│   ├── test_risk_engine.py
│   ├── test_portfolio_router.py
│   └── test_execution_engine.py
│
├── integration/             # Integration tests (multi-component)
│   ├── __init__.py
│   ├── test_signal_flow.py
│   ├── test_order_lifecycle.py
│   └── test_state_persistence.py
│
├── scenarios/               # Scenario tests (end-to-end)
│   ├── __init__.py
│   ├── test_kill_switch_scenario.py
│   ├── test_panic_mode_scenario.py
│   ├── test_overnight_scenario.py
│   ├── test_cold_start_scenario.py
│   └── test_gap_filter_scenario.py
│
├── fixtures/                # Test data and mock objects
│   ├── __init__.py
│   ├── mock_algorithm.py
│   ├── mock_portfolio.py
│   ├── mock_indicators.py
│   └── sample_data.py
│
└── helpers/                 # Test utilities
    ├── __init__.py
    ├── assertions.py
    └── time_helpers.py
```

**Naming Conventions:**

| Convention | Example | Purpose |
|------------|---------|---------|
| File prefix | `test_` | Pytest discovery |
| Class prefix | `Test` | Pytest discovery |
| Method prefix | `test_` | Pytest discovery |
| Descriptive names | `test_kill_switch_triggers_at_3_percent_loss` | Self-documenting |

**Test Markers:**

```python
# Use markers to categorize tests
@pytest.mark.unit          # Fast, isolated tests
@pytest.mark.integration   # Multi-component tests
@pytest.mark.scenario      # End-to-end flows
@pytest.mark.slow          # Tests that take >1 second
@pytest.mark.qc            # Tests requiring QC imports
```

---

### 6.3 Pytest Configuration

**pytest.ini:**

```ini
# tests/pytest.ini
[pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*

# Markers
markers =
    unit: Unit tests (fast, isolated)
    integration: Integration tests (multi-component)
    scenario: Scenario tests (end-to-end)
    slow: Slow tests (>1 second)
    qc: Tests requiring QuantConnect imports

# Default options
addopts =
    -v
    --tb=short
    --strict-markers
    -ra

# Coverage settings
[coverage:run]
source = engines,portfolio,models,execution,persistence
omit = tests/*,*/__init__.py

[coverage:report]
exclude_lines =
    pragma: no cover
    raise NotImplementedError
    if TYPE_CHECKING:
```

**conftest.py (Shared Fixtures):**

```python
# tests/conftest.py
"""Shared pytest fixtures for all tests."""
import pytest
from datetime import datetime, time
from unittest.mock import MagicMock, PropertyMock
from decimal import Decimal

# ═══════════════════════════════════════════════════════════════════
# ALGORITHM FIXTURES
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def mock_algorithm():
    """Create a mock QuantConnect algorithm."""
    algo = MagicMock()

    # Time - default to 10:30 AM (during trading hours)
    algo.Time = datetime(2024, 1, 15, 10, 30, 0)

    # Logging
    algo.Log = MagicMock()
    algo.Debug = MagicMock()
    algo.Error = MagicMock()

    # Portfolio (empty by default)
    algo.Portfolio = MagicMock()
    algo.Portfolio.TotalPortfolioValue = 50000.0
    algo.Portfolio.Cash = 10000.0
    algo.Portfolio.TotalHoldingsValue = 40000.0

    # Securities container
    algo.Securities = {}

    # Order methods (should NOT be called by strategy engines)
    algo.MarketOrder = MagicMock()
    algo.MarketOnOpenOrder = MagicMock()
    algo.Liquidate = MagicMock()

    return algo


@pytest.fixture
def mock_algorithm_with_positions(mock_algorithm):
    """Algorithm with sample positions."""
    # Add positions
    mock_algorithm.Portfolio.__getitem__ = MagicMock(side_effect=lambda s: {
        "QLD": create_holding("QLD", 100, 75.00, 7500.0),
        "TMF": create_holding("TMF", 50, 25.00, 1250.0),
        "SHV": create_holding("SHV", 100, 110.00, 11000.0),
        "TQQQ": create_holding("TQQQ", 0, 0.0, 0.0),
        "SOXL": create_holding("SOXL", 0, 0.0, 0.0),
    }.get(s, create_holding(s, 0, 0.0, 0.0)))

    return mock_algorithm


# ═══════════════════════════════════════════════════════════════════
# ENGINE FIXTURES
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def mock_regime_engine():
    """Create a mock regime engine."""
    engine = MagicMock()
    engine.get_current_score.return_value = 65.0
    engine.get_regime_state.return_value = "NEUTRAL"
    engine.previous_smoothed_score = 65.0
    return engine


@pytest.fixture
def mock_capital_engine():
    """Create a mock capital engine."""
    engine = MagicMock()
    engine.get_phase.return_value = "GROWTH"
    engine.get_tradeable_equity.return_value = 45000.0
    engine.get_lockbox_amount.return_value = 5000.0
    engine.get_daily_drawdown.return_value = 0.005  # 0.5%
    return engine


@pytest.fixture
def mock_risk_engine():
    """Create a mock risk engine."""
    engine = MagicMock()
    engine.check_kill_switch.return_value = False
    engine.check_panic_mode.return_value = False
    engine.check_weekly_breaker.return_value = False
    engine.check_vol_shock.return_value = False
    engine.is_time_guard_active.return_value = False
    return engine


# ═══════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════

def create_holding(symbol: str, quantity: int, avg_price: float, value: float):
    """Create a mock portfolio holding."""
    holding = MagicMock()
    holding.Symbol = symbol
    holding.Quantity = quantity
    holding.AveragePrice = avg_price
    holding.HoldingsValue = value
    holding.Invested = quantity != 0
    holding.UnrealizedProfit = value - (quantity * avg_price) if quantity else 0
    return holding


def create_security(symbol: str, price: float):
    """Create a mock security."""
    security = MagicMock()
    security.Symbol = symbol
    security.Price = price
    security.Close = price
    security.Open = price * 0.99
    security.High = price * 1.01
    security.Low = price * 0.98
    security.Volume = 1000000
    return security


# ═══════════════════════════════════════════════════════════════════
# TIME FIXTURES
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def market_open_time():
    """Return 9:30 AM market open."""
    return datetime(2024, 1, 15, 9, 30, 0)


@pytest.fixture
def market_close_time():
    """Return 4:00 PM market close."""
    return datetime(2024, 1, 15, 16, 0, 0)


@pytest.fixture
def mr_entry_window_time():
    """Return 11:00 AM (during MR entry window)."""
    return datetime(2024, 1, 15, 11, 0, 0)


@pytest.fixture
def force_close_time():
    """Return 3:45 PM (TQQQ/SOXL force close)."""
    return datetime(2024, 1, 15, 15, 45, 0)
```

---

### 6.4 Unit Test Patterns

**Standard Unit Test Template:**

```python
# tests/unit/test_regime_engine.py
"""Unit tests for Regime Engine."""
import pytest
from unittest.mock import MagicMock
from engines.regime_engine import RegimeEngine
from models.enums import RegimeState


class TestRegimeScoreClassification:
    """Test regime state classification from scores."""

    def test_risk_on_at_boundary(self):
        """Score of exactly 70 should be RISK_ON."""
        engine = RegimeEngine(MagicMock())
        assert engine.classify_score(70) == RegimeState.RISK_ON

    def test_risk_on_above_boundary(self):
        """Score above 70 should be RISK_ON."""
        engine = RegimeEngine(MagicMock())
        assert engine.classify_score(85) == RegimeState.RISK_ON

    def test_neutral_just_below_risk_on(self):
        """Score of 69 should be NEUTRAL, not RISK_ON."""
        engine = RegimeEngine(MagicMock())
        assert engine.classify_score(69) == RegimeState.NEUTRAL

    def test_neutral_at_lower_boundary(self):
        """Score of 40 should be NEUTRAL."""
        engine = RegimeEngine(MagicMock())
        assert engine.classify_score(40) == RegimeState.NEUTRAL

    def test_cautious_at_boundary(self):
        """Score of 39 should be CAUTIOUS."""
        engine = RegimeEngine(MagicMock())
        assert engine.classify_score(39) == RegimeState.CAUTIOUS

    def test_defensive_at_boundary(self):
        """Score of 30 should be DEFENSIVE."""
        engine = RegimeEngine(MagicMock())
        assert engine.classify_score(30) == RegimeState.DEFENSIVE

    def test_risk_off_below_30(self):
        """Score below 30 should be RISK_OFF."""
        engine = RegimeEngine(MagicMock())
        assert engine.classify_score(29) == RegimeState.RISK_OFF
        assert engine.classify_score(0) == RegimeState.RISK_OFF

    @pytest.mark.parametrize("score,expected", [
        (100, RegimeState.RISK_ON),
        (70, RegimeState.RISK_ON),
        (69, RegimeState.NEUTRAL),
        (50, RegimeState.NEUTRAL),
        (40, RegimeState.NEUTRAL),
        (39, RegimeState.CAUTIOUS),
        (30, RegimeState.DEFENSIVE),
        (29, RegimeState.RISK_OFF),
        (0, RegimeState.RISK_OFF),
    ])
    def test_all_boundaries(self, score, expected):
        """Parametrized test for all score boundaries."""
        engine = RegimeEngine(MagicMock())
        assert engine.classify_score(score) == expected


class TestRegimeSmoothing:
    """Test exponential smoothing calculation."""

    def test_smoothing_formula_correct(self):
        """Smoothed = alpha * raw + (1-alpha) * previous."""
        engine = RegimeEngine(MagicMock())
        raw, previous = 60, 50
        alpha = 0.30  # From config
        expected = alpha * raw + (1 - alpha) * previous  # 53
        assert engine.apply_smoothing(raw, previous) == pytest.approx(expected)

    def test_smoothing_dampens_spike_up(self):
        """Spike up should be dampened."""
        engine = RegimeEngine(MagicMock())
        smoothed = engine.apply_smoothing(raw=80, previous=50)
        assert 50 < smoothed < 80  # Between previous and raw

    def test_smoothing_dampens_spike_down(self):
        """Spike down should be dampened."""
        engine = RegimeEngine(MagicMock())
        smoothed = engine.apply_smoothing(raw=20, previous=50)
        assert 20 < smoothed < 50  # Between raw and previous

    def test_smoothing_stable_when_no_change(self):
        """No change when raw equals previous."""
        engine = RegimeEngine(MagicMock())
        smoothed = engine.apply_smoothing(raw=50, previous=50)
        assert smoothed == pytest.approx(50)


class TestRegimeEdgeCases:
    """Test edge cases and error handling."""

    def test_score_at_zero(self):
        """Score of 0 should not raise error."""
        engine = RegimeEngine(MagicMock())
        assert engine.classify_score(0) == RegimeState.RISK_OFF

    def test_score_at_100(self):
        """Score of 100 should not raise error."""
        engine = RegimeEngine(MagicMock())
        assert engine.classify_score(100) == RegimeState.RISK_ON

    def test_negative_score_handled(self):
        """Negative score should be treated as RISK_OFF."""
        engine = RegimeEngine(MagicMock())
        # Depending on implementation: clamp or treat as RISK_OFF
        result = engine.classify_score(-10)
        assert result == RegimeState.RISK_OFF

    def test_score_over_100_handled(self):
        """Score over 100 should be treated as RISK_ON."""
        engine = RegimeEngine(MagicMock())
        result = engine.classify_score(110)
        assert result == RegimeState.RISK_ON
```

**Strategy Engine Test Template:**

```python
# tests/unit/test_trend_engine.py
"""Unit tests for Trend Engine."""
import pytest
from unittest.mock import MagicMock, patch
from engines.trend_engine import TrendEngine
from models.target_weight import TargetWeight
from models.enums import Strategy, Urgency


class TestTrendEngineEntryConditions:
    """Test entry signal generation conditions."""

    @pytest.fixture
    def trend_engine(self, mock_algorithm, mock_regime_engine, mock_capital_engine):
        """Create TrendEngine with mocked dependencies."""
        return TrendEngine(mock_algorithm, mock_regime_engine, mock_capital_engine)

    def test_no_entry_without_compression(self, trend_engine):
        """No entry signal if BB not compressed."""
        trend_engine.check_compression = MagicMock(return_value=False)
        signals = trend_engine.generate_entry_signals()
        assert len(signals) == 0

    def test_no_entry_without_breakout(self, trend_engine):
        """No entry signal if no breakout after compression."""
        trend_engine.check_compression = MagicMock(return_value=True)
        trend_engine.check_breakout = MagicMock(return_value=False)
        signals = trend_engine.generate_entry_signals()
        assert len(signals) == 0

    def test_entry_on_compression_and_breakout(self, trend_engine):
        """Entry signal when compression AND breakout."""
        trend_engine.check_compression = MagicMock(return_value=True)
        trend_engine.check_breakout = MagicMock(return_value=True)
        trend_engine._is_already_in_position = MagicMock(return_value=False)

        signals = trend_engine.generate_entry_signals()

        assert len(signals) >= 1
        assert all(isinstance(s, TargetWeight) for s in signals)
        assert all(s.strategy == Strategy.TREND for s in signals)

    def test_no_entry_if_already_in_position(self, trend_engine):
        """No entry signal if already holding position."""
        trend_engine.check_compression = MagicMock(return_value=True)
        trend_engine.check_breakout = MagicMock(return_value=True)
        trend_engine._is_already_in_position = MagicMock(return_value=True)

        signals = trend_engine.generate_entry_signals()
        assert len(signals) == 0


class TestTrendEngineNoOrderPlacement:
    """CRITICAL: Verify TrendEngine never places orders directly."""

    def test_no_market_order_calls(self, mock_algorithm):
        """TrendEngine must NEVER call MarketOrder."""
        engine = TrendEngine(mock_algorithm, MagicMock(), MagicMock())

        # Run all signal generation
        engine.generate_signals()

        # Verify no order calls
        mock_algorithm.MarketOrder.assert_not_called()
        mock_algorithm.MarketOnOpenOrder.assert_not_called()
        mock_algorithm.Liquidate.assert_not_called()

    def test_only_emits_target_weights(self, mock_algorithm):
        """TrendEngine should only return TargetWeight objects."""
        engine = TrendEngine(mock_algorithm, MagicMock(), MagicMock())
        signals = engine.generate_signals()

        assert all(isinstance(s, TargetWeight) for s in signals)
```

---

### 6.5 Mocking QuantConnect Objects

**Mock Indicators:**

```python
# tests/fixtures/mock_indicators.py
"""Mock QuantConnect indicators for testing."""
from unittest.mock import MagicMock, PropertyMock


def create_mock_bollinger_band(upper: float, middle: float, lower: float,
                                bandwidth: float, is_ready: bool = True):
    """Create a mock Bollinger Band indicator."""
    bb = MagicMock()
    bb.IsReady = is_ready
    bb.UpperBand.Current.Value = upper
    bb.MiddleBand.Current.Value = middle
    bb.LowerBand.Current.Value = lower
    bb.BandWidth.Current.Value = bandwidth
    return bb


def create_mock_rsi(value: float, is_ready: bool = True):
    """Create a mock RSI indicator."""
    rsi = MagicMock()
    rsi.IsReady = is_ready
    rsi.Current.Value = value
    return rsi


def create_mock_sma(value: float, is_ready: bool = True):
    """Create a mock SMA indicator."""
    sma = MagicMock()
    sma.IsReady = is_ready
    sma.Current.Value = value
    return sma


def create_mock_atr(value: float, is_ready: bool = True):
    """Create a mock ATR indicator."""
    atr = MagicMock()
    atr.IsReady = is_ready
    atr.Current.Value = value
    return atr


# Example usage in tests:
def test_trend_entry_on_bb_breakout():
    """Test entry signal on BB breakout."""
    # Set up compressed BB that's breaking out
    bb = create_mock_bollinger_band(
        upper=100.0,
        middle=95.0,
        lower=90.0,
        bandwidth=0.08  # < 10% = compressed
    )

    engine = TrendEngine(mock_algo)
    engine._get_bb_indicator = MagicMock(return_value=bb)
    engine._get_current_price = MagicMock(return_value=101.0)  # Above upper

    assert engine.check_breakout("QLD") == True
```

**Mock Time Helpers:**

```python
# tests/helpers/time_helpers.py
"""Time manipulation helpers for testing."""
from datetime import datetime, timedelta
from contextlib import contextmanager
from unittest.mock import patch


@contextmanager
def mock_time(algo_mock, hour: int, minute: int = 0,
              year: int = 2024, month: int = 1, day: int = 15):
    """Context manager to set algorithm time."""
    original_time = algo_mock.Time
    algo_mock.Time = datetime(year, month, day, hour, minute, 0)
    try:
        yield
    finally:
        algo_mock.Time = original_time


def set_time(algo_mock, hour: int, minute: int = 0):
    """Set algorithm time directly."""
    algo_mock.Time = datetime(2024, 1, 15, hour, minute, 0)


# Example usage:
def test_mr_entry_window(mock_algorithm):
    """Test MR entry window is 10:00-15:00."""
    engine = MeanReversionEngine(mock_algorithm)

    # Before window
    set_time(mock_algorithm, 9, 45)
    assert engine.check_entry_window() == False

    # During window
    set_time(mock_algorithm, 11, 0)
    assert engine.check_entry_window() == True

    # After window
    set_time(mock_algorithm, 15, 30)
    assert engine.check_entry_window() == False
```

---

### 6.6 Scenario Tests

**Scenario Test Template:**

```python
# tests/scenarios/test_kill_switch_scenario.py
"""Scenario: Kill switch activates and liquidates all positions."""
import pytest
from unittest.mock import MagicMock, call
from engines.risk_engine import RiskEngine
from portfolio.portfolio_router import PortfolioRouter
from models.enums import Urgency


class TestKillSwitchScenario:
    """
    Scenario: Portfolio loses 3%+ intraday, triggering kill switch.

    Expected behavior:
    1. Risk engine detects 3% loss from baseline
    2. Kill switch activates
    3. All positions liquidated immediately
    4. Cold start days reset to 0
    5. No new entries allowed until next day
    """

    @pytest.fixture
    def setup_scenario(self, mock_algorithm_with_positions):
        """Set up scenario with positions and engines."""
        algo = mock_algorithm_with_positions
        algo.Portfolio.TotalPortfolioValue = 50000.0

        risk_engine = RiskEngine(algo)
        risk_engine.set_baseline(50000.0)  # Set at market open

        return algo, risk_engine

    def test_kill_switch_triggers_at_3_percent(self, setup_scenario):
        """Kill switch triggers at exactly 3% loss."""
        algo, risk_engine = setup_scenario

        # Simulate 3% loss
        algo.Portfolio.TotalPortfolioValue = 48500.0  # 3% loss

        assert risk_engine.check_kill_switch() == True

    def test_kill_switch_does_not_trigger_below_threshold(self, setup_scenario):
        """Kill switch does NOT trigger below 3%."""
        algo, risk_engine = setup_scenario

        # Simulate 2.9% loss
        algo.Portfolio.TotalPortfolioValue = 48550.0  # 2.9% loss

        assert risk_engine.check_kill_switch() == False

    def test_kill_switch_emits_liquidation_for_all(self, setup_scenario):
        """Kill switch emits IMMEDIATE liquidation for all positions."""
        algo, risk_engine = setup_scenario
        algo.Portfolio.TotalPortfolioValue = 48000.0  # 4% loss

        signals = risk_engine.trigger_kill_switch()

        # Check all positions have liquidation signals
        symbols_to_liquidate = ["QLD", "TMF", "SHV"]
        signal_symbols = [s.symbol for s in signals]

        for symbol in symbols_to_liquidate:
            assert symbol in signal_symbols

        # All signals should be weight=0 and IMMEDIATE
        for signal in signals:
            assert signal.weight == 0.0
            assert signal.urgency == Urgency.IMMEDIATE
            assert "KILL_SWITCH" in signal.reason

    def test_kill_switch_resets_cold_start(self, setup_scenario):
        """Kill switch resets days_running to 0."""
        algo, risk_engine = setup_scenario
        risk_engine.days_running = 15  # Had been running 15 days

        risk_engine.trigger_kill_switch()

        assert risk_engine.days_running == 0

    def test_kill_switch_blocks_new_entries(self, setup_scenario):
        """After kill switch, no new entries allowed today."""
        algo, risk_engine = setup_scenario

        risk_engine.trigger_kill_switch()

        assert risk_engine.entries_blocked == True

    def test_kill_switch_logged_correctly(self, setup_scenario):
        """Kill switch event is logged with details."""
        algo, risk_engine = setup_scenario
        algo.Portfolio.TotalPortfolioValue = 48000.0

        risk_engine.trigger_kill_switch()

        # Check log was called with KILL_SWITCH
        log_calls = [str(c) for c in algo.Log.call_args_list]
        assert any("KILL_SWITCH" in call for call in log_calls)
```

**Critical Scenarios to Test:**

| Scenario | File | Tests |
|----------|------|-------|
| Kill Switch | `test_kill_switch_scenario.py` | 3% loss triggers, liquidates all, resets cold start |
| Panic Mode | `test_panic_mode_scenario.py` | SPY -4% triggers, liquidates longs only, keeps hedges |
| Overnight Exit | `test_overnight_scenario.py` | TQQQ/SOXL close by 15:45, QLD/SSO can hold |
| Cold Start | `test_cold_start_scenario.py` | Days 1-5 scaling, gradual position building |
| Gap Filter | `test_gap_filter_scenario.py` | SPY -1.5% gap blocks MR entries |
| Weekly Breaker | `test_weekly_breaker_scenario.py` | 5% WTD loss reduces sizing 50% |
| Vol Shock | `test_vol_shock_scenario.py` | 3×ATR bar pauses entries 15 min |

---

### 6.7 Test Coverage

**Coverage Targets:**

| Component | Minimum | Ideal |
|-----------|:-------:|:-----:|
| `engines/` | 80% | 90% |
| `portfolio/` | 80% | 90% |
| `models/` | 90% | 95% |
| `execution/` | 75% | 85% |
| `persistence/` | 75% | 85% |
| **Overall** | **80%** | **90%** |

**Running Coverage:**

```bash
# Run tests with coverage
pytest tests/ --cov=engines --cov=portfolio --cov=models \
    --cov-report=term-missing --cov-report=html

# View HTML report
open htmlcov/index.html

# Check specific file coverage
pytest tests/unit/test_regime_engine.py --cov=engines/regime_engine \
    --cov-report=term-missing
```

**Coverage Report Interpretation:**

```
Name                              Stmts   Miss  Cover   Missing
───────────────────────────────────────────────────────────────
engines/regime_engine.py             85      8    91%   45-48, 112-115
engines/capital_engine.py            62      3    95%   78-80
engines/trend_engine.py             124     18    85%   89-95, 145-156
───────────────────────────────────────────────────────────────
TOTAL                               271     29    89%
```

| Column | Meaning |
|--------|---------|
| Stmts | Total statements in file |
| Miss | Statements not executed by tests |
| Cover | Percentage covered |
| Missing | Line numbers not covered |

**Improving Coverage:**

```python
# Focus on uncovered lines (e.g., lines 45-48)
# Usually error handlers or edge cases

# Add specific test for missing lines:
def test_regime_engine_handles_missing_data(self):
    """Test lines 45-48: handling missing indicator data."""
    engine = RegimeEngine(mock_algorithm)
    engine._get_spy_price = MagicMock(return_value=None)

    # Should handle gracefully, not crash
    score = engine.calculate_regime_score()
    assert score is not None  # Returns default or previous
```

---

### 6.8 Asking Claude to Write Tests

**Prompt Templates:**

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
UNIT TEST PROMPT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"Write unit tests for engines/regime_engine.py.

Reference: docs/04-regime-engine.md

Test categories needed:
1. Score classification boundaries (RISK_ON at 70, etc.)
2. Smoothing calculation (alpha=0.30)
3. Factor calculations (trend, volatility, breadth, credit)
4. Edge cases (0, 100, negative, over 100)
5. Indicator not ready handling

Use:
- pytest with fixtures from conftest.py
- Descriptive test names
- Docstrings explaining each test
- @pytest.mark.unit marker
- Parametrized tests for boundaries"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SCENARIO TEST PROMPT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"Write a scenario test for the overnight position scenario.

Scenario: System ensures TQQQ and SOXL are closed by 15:45 ET

Test flow:
1. Setup: MR Engine has open TQQQ position
2. Time advances to 15:45 ET
3. MR Engine generates force-close signal
4. Signal has urgency=IMMEDIATE and reason contains 'TIME_EXIT'
5. After close, no TQQQ/SOXL positions remain

Reference: docs/08-mean-reversion-engine.md Section 8.4

Use fixtures from conftest.py. Add @pytest.mark.scenario marker."
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
COVERAGE GAP PROMPT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"The following lines in engines/trend_engine.py are not covered by tests:

Lines 89-95: Chandelier stop calculation when profit > 10%
Lines 145-156: Exit signal when regime deteriorates to DEFENSIVE

Write specific tests to cover these lines. Each test should:
1. Set up the exact conditions to trigger these code paths
2. Verify the expected behavior
3. Have descriptive names indicating what's being tested"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

### 6.9 QuantConnect Backtest Validation

**Running Backtests:**

```bash
# Push code to QC
lean cloud push

# Run backtest with descriptive name
lean cloud backtest "alpha-nextgen" --name "Phase3-StrategyEngines-$(date +%Y%m%d)"

# Run and open results in browser
lean cloud backtest "alpha-nextgen" --open

# List recent backtests
lean cloud backtest list "alpha-nextgen"
```

**Backtest Validation Checklist:**

```markdown
## Backtest Validation Checklist

### Completion
□ Backtest completes without runtime errors
□ No "Runtime Error" in log output
□ No unexpected exceptions

### Order Execution
□ Orders appear in Order Log
□ Fill prices are reasonable (within spread)
□ No rejected orders (unless expected)

### Regime Behavior
□ Regime changes logged
□ Regime transitions match market conditions
□ Regime affects position sizing correctly

### Risk Management
□ Kill switch triggers if 3% loss (check in volatile backtest periods)
□ Panic mode triggers on SPY -4% days
□ Weekly breaker reduces sizing after 5% WTD loss

### Overnight Positions
□ TQQQ never appears in end-of-day holdings
□ SOXL never appears in end-of-day holdings
□ QLD/SSO/TMF/PSQ/SHV may appear overnight

### Position Sizing
□ No single position exceeds exposure limits
□ NASDAQ_BETA net ≤ 50%
□ SPY_BETA ≤ 40%
□ RATES ≤ 40%

### Performance Metrics
□ Sharpe Ratio > 0 (positive risk-adjusted return)
□ Max Drawdown < 25% (acceptable for leveraged strategy)
□ Win Rate reasonable for strategy type
```

**Common Backtest Issues:**

| Issue | Symptom | Solution |
|-------|---------|----------|
| Import error | "No module named 'engines'" | Verify `__init__.py` in all directories |
| Indicator not ready | Orders in first few days fail | Check `IsReady` before using indicators |
| Division by zero | Runtime error in calculations | Add zero checks before division |
| No trades | Order log is empty | Check regime conditions, entry signals |
| Overnight TQQQ | TQQQ in EOD holdings | Verify 15:45 force close logic |

---

### 6.10 CI/CD Integration

**GitHub Actions Workflow:**

```yaml
# .github/workflows/test.yml
name: Tests

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main, develop]

jobs:
  test:
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt
          pip install -r requirements-dev.txt

      - name: Run static analysis
        run: |
          black --check engines/ portfolio/ models/
          isort --check-only engines/ portfolio/ models/
          flake8 engines/ portfolio/ models/
          mypy engines/ portfolio/ models/ --ignore-missing-imports

      - name: Run unit tests
        run: |
          pytest tests/unit/ -v --tb=short -m "not slow"

      - name: Run integration tests
        run: |
          pytest tests/integration/ -v --tb=short

      - name: Run coverage
        run: |
          pytest tests/ --cov=engines --cov=portfolio --cov=models \
            --cov-report=xml --cov-fail-under=80

      - name: Upload coverage
        uses: codecov/codecov-action@v3
        with:
          file: ./coverage.xml

  scenario-tests:
    runs-on: ubuntu-latest
    needs: test  # Only run if unit/integration pass

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          pip install -r requirements-dev.txt

      - name: Run scenario tests
        run: |
          pytest tests/scenarios/ -v --tb=long
```

**When Tests Run:**

| Event | Tests Run | Required to Pass |
|-------|-----------|:----------------:|
| Push to feature branch | Unit only | No |
| PR to develop | Unit + Integration | Yes |
| PR to main | All (Unit + Integration + Scenario) | Yes |
| Merge to main | All + Backtest trigger | Yes |

**Handling CI Failures:**

```bash
# View failed test output locally
pytest tests/unit/test_regime_engine.py -v --tb=long

# Run specific failing test
pytest tests/unit/test_regime_engine.py::TestRegimeScoreClassification::test_risk_on_at_70 -v

# Debug with print statements
pytest tests/ -v -s  # -s shows print output
```

**Spec Parity Check:**

A CI/CD step automatically warns if you modify a code file without updating its corresponding spec document. This helps keep documentation and code in sync.

```yaml
# Add to GitHub Actions workflow (.github/workflows/test.yml)
- name: Check spec parity
  run: |
    # Get modified Python files
    CHANGED_CODE=$(git diff --name-only HEAD~1 | grep -E "engines/.*\.py$" || true)

    for file in $CHANGED_CODE; do
      # Map code file to spec file
      case $file in
        engines/regime_engine.py) spec="docs/04-regime-engine.md" ;;
        engines/capital_engine.py) spec="docs/05-capital-engine.md" ;;
        engines/cold_start_engine.py) spec="docs/06-cold-start-engine.md" ;;
        engines/trend_engine.py) spec="docs/07-trend-engine.md" ;;
        engines/mean_reversion_engine.py) spec="docs/08-mean-reversion-engine.md" ;;
        engines/hedge_engine.py) spec="docs/09-hedge-engine.md" ;;
        engines/yield_sleeve.py) spec="docs/10-yield-sleeve.md" ;;
        engines/risk_engine.py) spec="docs/12-risk-engine.md" ;;
        *) spec="" ;;
      esac

      if [ -n "$spec" ]; then
        if ! git diff --name-only HEAD~1 | grep -q "$spec"; then
          echo "⚠️ SPEC MISMATCH: $file modified but $spec not updated"
        fi
      fi
    done
```

> **Note:** This is a soft warning and does not block the build. It reminds developers to update documentation when behavior changes. Check build logs for `⚠️ SPEC MISMATCH` warnings.

---

## 7. Handling Documentation-Code Mismatches

> **Core Principle:** The spec documents are the source of truth. Code must match specs. When they diverge, fix one or the other—never leave mismatches unresolved.

---

### 7.1 Types of Mismatches

| Type | Example | Severity | Impact |
|------|---------|:--------:|--------|
| **Parameter Value** | Doc: "3%" / Code: `0.05` | 🔴 High | Wrong trigger thresholds |
| **Logic Operator** | Doc: ">" / Code: `>=` | 🔴 High | Off-by-one errors, boundary bugs |
| **Formula Error** | Doc: `0.3*raw + 0.7*prev` / Code: `0.7*raw + 0.3*prev` | 🔴 High | Wrong calculations |
| **Missing Feature** | Doc describes it, code doesn't have it | 🟡 Medium | Incomplete system |
| **Extra Feature** | Code has it, doc doesn't mention | 🟡 Medium | Untested/undocumented behavior |
| **Naming Mismatch** | Doc: `kill_switch_pct` / Code: `KILL_PCT` | 🟢 Low | Confusion, maintenance burden |
| **Outdated Comment** | Comment says one thing, code does another | 🟢 Low | Misleading documentation |

**Severity Guide:**
- 🔴 **High**: Affects trading behavior—fix immediately before any testing
- 🟡 **Medium**: System incomplete or has undocumented behavior—fix before release
- 🟢 **Low**: Cosmetic or naming issues—fix in next cleanup batch

---

### 7.2 Prevention Strategy

**1. Parameter Validation Script**

```python
# scripts/validate_config.py
"""Validate config.py parameters against spec documentation."""
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple

# Map spec files to their expected parameters
SPEC_PARAMS = {
    "docs/04-regime-engine.md": [
        ("REGIME_SMOOTHING_ALPHA", 0.30),
        ("REGIME_RISK_ON", 70),
        ("REGIME_NEUTRAL_LOW", 40),
        ("REGIME_DEFENSIVE", 30),
        ("WEIGHT_TREND", 0.30),
        ("WEIGHT_VOLATILITY", 0.25),
        ("WEIGHT_BREADTH", 0.25),
        ("WEIGHT_CREDIT", 0.20),
    ],
    "docs/05-capital-engine.md": [
        ("SEED_PHASE_THRESHOLD", 75000),
        ("LOCKBOX_AMOUNT", 5000),
    ],
    "docs/12-risk-engine.md": [
        ("KILL_SWITCH_PCT", 0.03),
        ("PANIC_MODE_PCT", 0.04),
        ("WEEKLY_BREAKER_PCT", 0.05),
        ("GAP_FILTER_PCT", 0.015),
        ("VOL_SHOCK_ATR_MULT", 3.0),
    ],
    "docs/07-trend-engine.md": [
        ("BB_PERIOD", 20),
        ("BB_STD_DEV", 2.0),
        ("BB_COMPRESSION_THRESHOLD", 0.10),
        ("CHANDELIER_ATR_MULT", 3.0),
    ],
    "docs/08-mean-reversion-engine.md": [
        ("MR_RSI_PERIOD", 5),
        ("MR_RSI_OVERSOLD", 25),
        ("MR_ENTRY_START", "10:00"),
        ("MR_ENTRY_END", "15:00"),
        ("MR_FORCE_CLOSE", "15:45"),
    ],
}


def validate_config() -> List[Dict]:
    """Compare config.py against spec parameters."""
    try:
        import config
    except ImportError:
        print("ERROR: Cannot import config.py")
        sys.exit(1)

    mismatches = []

    for spec_file, params in SPEC_PARAMS.items():
        for param_name, expected in params:
            if hasattr(config, param_name):
                actual = getattr(config, param_name)
                if actual != expected:
                    mismatches.append({
                        "spec": spec_file,
                        "param": param_name,
                        "expected": expected,
                        "actual": actual,
                        "severity": "HIGH" if isinstance(expected, (int, float)) else "MEDIUM"
                    })
            else:
                mismatches.append({
                    "spec": spec_file,
                    "param": param_name,
                    "expected": expected,
                    "actual": "MISSING",
                    "severity": "HIGH"
                })

    return mismatches


def main():
    """Run validation and report results."""
    mismatches = validate_config()

    if not mismatches:
        print("✅ All parameters match spec documentation!")
        sys.exit(0)

    print("❌ MISMATCHES FOUND:\n")
    for m in mismatches:
        print(f"  [{m['severity']}] {m['param']}")
        print(f"         Spec ({m['spec']}): {m['expected']}")
        print(f"         Code (config.py): {m['actual']}")
        print()

    sys.exit(1)


if __name__ == "__main__":
    main()
```

**2. Pre-Commit Hook**

```bash
#!/bin/bash
# .git/hooks/pre-commit (make executable: chmod +x)

echo "Running spec validation..."

# Check if config.py is being committed
if git diff --cached --name-only | grep -q "config.py"; then
    python scripts/validate_config.py
    if [ $? -ne 0 ]; then
        echo ""
        echo "❌ Commit blocked: config.py doesn't match spec documentation"
        echo "   Fix the mismatch or update the spec, then try again."
        exit 1
    fi
fi

# Check for spec parity (code changed but spec not updated)
CHANGED_ENGINES=$(git diff --cached --name-only | grep -E "engines/.*\.py$" || true)

for file in $CHANGED_ENGINES; do
    case $file in
        engines/regime_engine.py) spec="docs/04-regime-engine.md" ;;
        engines/capital_engine.py) spec="docs/05-capital-engine.md" ;;
        engines/trend_engine.py) spec="docs/07-trend-engine.md" ;;
        engines/mean_reversion_engine.py) spec="docs/08-mean-reversion-engine.md" ;;
        engines/risk_engine.py) spec="docs/12-risk-engine.md" ;;
        *) spec="" ;;
    esac

    if [ -n "$spec" ]; then
        if ! git diff --cached --name-only | grep -q "$spec"; then
            echo "⚠️  WARNING: $file modified but $spec not updated"
            echo "   Consider updating the spec if behavior changed."
        fi
    fi
done

echo "✅ Pre-commit checks passed"
exit 0
```

**3. Documentation Cross-Reference in Code**

Every engine file should have a header linking to its spec:

```python
# engines/regime_engine.py
"""
Regime Engine - Market State Classification

Specification: docs/04-regime-engine.md
Spec Version: 1.2
Last Validated: 2024-01-20

This engine implements the 4-factor regime scoring system:
- Trend factor (SPY vs 50/200 SMA)
- Volatility factor (VIX level)
- Breadth factor (advance/decline)
- Credit factor (HYG/LQD spread)

Key Spec References:
├── Section 4.3: Factor weights (WEIGHT_TREND=0.30, etc.)
├── Section 4.4: Score thresholds (RISK_ON≥70, RISK_OFF<30)
├── Section 4.5: Factor calculations
└── Section 4.8: Smoothing formula (alpha=0.30)

IMPORTANT: Any changes to this file should be reflected in the spec.
"""
```

**4. Spec Version Tracking**

```python
class RegimeEngine:
    """Regime scoring engine."""

    # Spec tracking - update when spec changes
    SPEC_FILE = "docs/04-regime-engine.md"
    SPEC_VERSION = "1.2"
    LAST_VALIDATED = "2024-01-20"

    def __init__(self, algorithm):
        self.algorithm = algorithm
        self._log_spec_info()

    def _log_spec_info(self):
        """Log spec version on initialization (debug mode)."""
        self.algorithm.Debug(
            f"RegimeEngine initialized | Spec: {self.SPEC_FILE} v{self.SPEC_VERSION}"
        )
```

---

### 7.3 Detection Strategy

**1. Manual Validation (Run Regularly)**

```bash
# Run after any config or spec changes
python scripts/validate_config.py

# Full validation before PR
python scripts/validate_config.py && pytest tests/test_spec_compliance.py -v
```

**2. Ask Claude to Cross-Check**

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SPEC CROSS-CHECK PROMPT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"Compare engines/regime_engine.py against docs/04-regime-engine.md.

Check for mismatches in:
1. Parameter values (thresholds, weights, multipliers)
2. Logic operators (>, >=, <, <=, ==)
3. Formula implementations (especially smoothing)
4. Boundary conditions (what happens at exactly 70, 30, etc.)
5. Missing features mentioned in spec
6. Extra features not mentioned in spec

Format findings as:

| Location | Spec Says | Code Does | Severity | Fix |
|----------|-----------|-----------|----------|-----|
"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**3. Automated Spec Compliance Tests**

```python
# tests/test_spec_compliance.py
"""
Spec Compliance Tests

These tests verify that code matches specification documents.
Each test references the specific spec section it validates.
"""
import pytest


class TestRegimeEngineCompliance:
    """Verify Regime Engine matches docs/04-regime-engine.md."""

    def test_factor_weights_sum_to_one(self):
        """Spec 4.3: Factor weights must sum to 1.0."""
        from config import (
            WEIGHT_TREND, WEIGHT_VOLATILITY,
            WEIGHT_BREADTH, WEIGHT_CREDIT
        )
        total = WEIGHT_TREND + WEIGHT_VOLATILITY + WEIGHT_BREADTH + WEIGHT_CREDIT
        assert abs(total - 1.0) < 0.001, f"Weights sum to {total}, spec requires 1.0"

    def test_smoothing_alpha(self):
        """Spec 4.8: Smoothing alpha must be 0.30."""
        from config import REGIME_SMOOTHING_ALPHA
        assert REGIME_SMOOTHING_ALPHA == 0.30, "Smoothing alpha must be 0.30 per spec"

    def test_risk_on_threshold(self):
        """Spec 4.6: RISK_ON at score >= 70."""
        from config import REGIME_RISK_ON
        assert REGIME_RISK_ON == 70

    def test_risk_off_threshold(self):
        """Spec 4.6: RISK_OFF at score < 30."""
        from config import REGIME_RISK_OFF
        assert REGIME_RISK_OFF == 30


class TestRiskEngineCompliance:
    """Verify Risk Engine matches docs/12-risk-engine.md."""

    def test_kill_switch_threshold(self):
        """Spec 12.2: Kill switch at 3% daily loss."""
        from config import KILL_SWITCH_PCT
        assert KILL_SWITCH_PCT == 0.03

    def test_panic_mode_threshold(self):
        """Spec 12.3: Panic mode at SPY -4% intraday."""
        from config import PANIC_MODE_PCT
        assert PANIC_MODE_PCT == 0.04

    def test_weekly_breaker_threshold(self):
        """Spec 12.4: Weekly breaker at 5% WTD loss."""
        from config import WEEKLY_BREAKER_PCT
        assert WEEKLY_BREAKER_PCT == 0.05


class TestMeanReversionCompliance:
    """Verify MR Engine matches docs/08-mean-reversion-engine.md."""

    def test_intraday_symbols(self):
        """Spec 8.1: TQQQ and SOXL are intraday only."""
        from config import MR_SYMBOLS, INTRADAY_ONLY_SYMBOLS
        assert "TQQQ" in MR_SYMBOLS
        assert "SOXL" in MR_SYMBOLS
        assert "TQQQ" in INTRADAY_ONLY_SYMBOLS
        assert "SOXL" in INTRADAY_ONLY_SYMBOLS

    def test_force_close_time(self):
        """Spec 8.4: Force close at 15:45 ET."""
        from config import MR_FORCE_CLOSE
        assert MR_FORCE_CLOSE == "15:45"

    def test_rsi_oversold_threshold(self):
        """Spec 8.2: RSI oversold at 25."""
        from config import MR_RSI_OVERSOLD
        assert MR_RSI_OVERSOLD == 25
```

**4. Scheduled CI Check**

```yaml
# .github/workflows/spec-compliance.yml
name: Spec Compliance Check

on:
  push:
    paths:
      - 'config.py'
      - 'engines/**'
      - 'docs/**'
  schedule:
    - cron: '0 9 * * 1'  # Every Monday at 9 AM

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run spec validation
        run: python scripts/validate_config.py

      - name: Run compliance tests
        run: pytest tests/test_spec_compliance.py -v
```

---

### 7.4 Resolution Strategy

**Decision Tree:**

```
┌─────────────────────────────────────────────────────────────────────┐
│                    MISMATCH RESOLUTION FLOW                          │
└─────────────────────────────────────────────────────────────────────┘

Found a mismatch between SPEC and CODE
                    │
                    ▼
        ┌───────────────────────┐
        │  Which is correct?    │
        └───────────────────────┘
                    │
       ┌────────────┴────────────┐
       ▼                         ▼
┌─────────────┐          ┌─────────────┐
│ SPEC is     │          │ CODE is     │
│ correct     │          │ correct     │
└─────────────┘          └─────────────┘
       │                         │
       ▼                         ▼
┌─────────────┐          ┌─────────────┐
│ Fix CODE    │          │ Fix SPEC    │
│ to match    │          │ to match    │
│ spec        │          │ code        │
└─────────────┘          └─────────────┘
       │                         │
       └────────────┬────────────┘
                    ▼
        ┌───────────────────────┐
        │  Add compliance test  │
        │  to prevent regression│
        └───────────────────────┘
                    │
                    ▼
        ┌───────────────────────┐
        │  Commit with message: │
        │  "fix: Align X with   │
        │   spec section Y.Z"   │
        └───────────────────────┘
```

**Resolution Checklist:**

```markdown
## Mismatch Resolution Checklist

### 1. Identify
□ What is the mismatch? (parameter, logic, formula, etc.)
□ Where is it? (file:line for both spec and code)
□ What is the severity? (High/Medium/Low)

### 2. Determine Source of Truth
□ Is this a new feature? → Spec is truth, code needs update
□ Is this a bug fix? → Code is truth, spec needs update
□ Is this unclear? → Discuss with team, document decision

### 3. Fix
□ Update the incorrect artifact (code OR spec)
□ If fixing code: ensure tests still pass
□ If fixing spec: ensure code matches new spec

### 4. Prevent Regression
□ Add/update compliance test in test_spec_compliance.py
□ Add parameter to validate_config.py if applicable

### 5. Document
□ Commit message references the fix
□ Update LAST_VALIDATED date in code header
□ Update spec version if spec changed
```

---

### 7.5 Handling Intentional Divergences

Sometimes code intentionally differs from spec (experiments, temporary workarounds). **Always document these.**

**1. Temporary Divergence (Planned Fix)**

```python
# In code, mark with TODO and ticket reference:
# TODO(ALPHA-123): Spec says 3.0 * ATR but using 2.5 pending backtest results
# Spec: docs/07-trend-engine.md Section 7.4
# Revert by: 2024-02-01
CHANDELIER_MULT = 2.5  # Spec: 3.0
```

**2. Experimental Divergence**

```python
# In config.py, use feature flags:
EXPERIMENTAL_CHANDELIER_MULT = 2.5  # Testing different value
USE_EXPERIMENTAL_CHANDELIER = False  # Toggle for A/B testing

# In code:
mult = EXPERIMENTAL_CHANDELIER_MULT if USE_EXPERIMENTAL_CHANDELIER else CHANDELIER_ATR_MULT
```

**3. Track Divergences**

Create `DIVERGENCES.md` in repo root:

```markdown
# Intentional Spec Divergences

## Active Divergences

| Code Location | Spec Location | Divergence | Reason | Ticket | Revert By |
|---------------|---------------|------------|--------|--------|-----------|
| config.py:45 | docs/07:7.4 | ATR mult 2.5 vs 3.0 | Testing | ALPHA-123 | 2024-02-01 |

## Resolved Divergences

| Date | Divergence | Resolution |
|------|------------|------------|
| 2024-01-10 | RSI period 3 vs 5 | Reverted to spec (5) after backtest |
```

---

### 7.6 Spec Update Workflow

When code needs to evolve beyond current spec:

```
┌─────────────────────────────────────────────────────────────────────┐
│                      SPEC UPDATE WORKFLOW                            │
└─────────────────────────────────────────────────────────────────────┘

1. PROPOSE
   └── Create issue/ticket describing proposed change
       └── Include: rationale, impact, backtest results (if applicable)

2. DRAFT SPEC UPDATE
   └── Update spec document with proposed changes
       └── Mark changed sections with [PROPOSED] tag

3. IMPLEMENT
   └── Update code to match proposed spec
       └── Feature flag if risky: USE_NEW_BEHAVIOR = False

4. VALIDATE
   └── Run backtests comparing old vs new behavior
       └── Document results in ticket

5. REVIEW
   └── Get approval for spec change
       └── Remove [PROPOSED] tags

6. RELEASE
   └── Enable feature flag (if used)
       └── Update spec version number
       └── Update LAST_VALIDATED in code
```

**Spec Update Prompt:**

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SPEC UPDATE PROMPT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"I need to update the spec for the Chandelier stop calculation.

Current spec (docs/07-trend-engine.md Section 7.4):
- ATR multiplier: 3.0 for profit < 15%, 2.5 for profit >= 15%

Proposed change:
- ATR multiplier: 2.5 for profit < 10%, 2.0 for profit >= 10%
- Reason: Tighter stops improved backtest Sharpe by 0.3

Please:
1. Update docs/07-trend-engine.md Section 7.4 with new values
2. Update config.py parameters to match
3. Update test_spec_compliance.py with new expected values
4. Update engines/trend_engine.py header with new spec version"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

### 7.7 Asking Claude to Help with Mismatches

**Cross-Check Prompt:**

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"Read both files and identify ALL mismatches:
- engines/trend_engine.py
- docs/07-trend-engine.md

For each mismatch, tell me:
1. What the spec says (with section reference)
2. What the code does (with line number)
3. Severity (High/Medium/Low)
4. Recommended fix (fix code or fix spec)"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Fix Mismatch Prompt:**

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"I found a mismatch:

SPEC (docs/07-trend-engine.md, Section 7.4):
  'Chandelier stop uses 3.0 × ATR when profit < 15%'

CODE (engines/trend_engine.py, line 142):
  'multiplier = 2.5 * atr if profit_pct < 0.15 else ...'

The SPEC is the source of truth.

Please:
1. Fix engines/trend_engine.py to use 3.0 instead of 2.5
2. Add a test to tests/test_spec_compliance.py verifying CHANDELIER_ATR_MULT == 3.0
3. Update the LAST_VALIDATED date in the file header"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Bulk Validation Prompt:**

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"Run a full spec compliance check across all engines:

For each engine file, compare against its spec:
- engines/regime_engine.py ↔ docs/04-regime-engine.md
- engines/capital_engine.py ↔ docs/05-capital-engine.md
- engines/trend_engine.py ↔ docs/07-trend-engine.md
- engines/mean_reversion_engine.py ↔ docs/08-mean-reversion-engine.md
- engines/hedge_engine.py ↔ docs/09-hedge-engine.md
- engines/risk_engine.py ↔ docs/12-risk-engine.md

Output a summary table of all findings."
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## 8. Best Practices for AI-Assisted Development

> **Philosophy:** Claude Code is a powerful collaborator, but you remain the architect. Use it for implementation speed, not decision-making. Always understand the code it generates.

---

### 8.1 Prompt Engineering Fundamentals

**Anatomy of an Effective Prompt:**

```
┌─────────────────────────────────────────────────────────────────────┐
│                     EFFECTIVE PROMPT STRUCTURE                       │
└─────────────────────────────────────────────────────────────────────┘

1. CONTEXT       → What does Claude need to know?
2. TASK          → What should Claude do?
3. CONSTRAINTS   → What rules must be followed?
4. OUTPUT FORMAT → How should the result look?
5. EXAMPLES      → What does good output look like? (optional)
```

**Example - Good Prompt:**

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CONTEXT:
I'm building the Regime Engine for Alpha NextGen trading system.
Spec: docs/04-regime-engine.md

TASK:
Implement the calculate_regime_score() method.

CONSTRAINTS:
- Use parameters from config.py (WEIGHT_TREND, WEIGHT_VOLATILITY, etc.)
- Return value must be clamped to [0, 100]
- Include type hints and docstring
- Do NOT call any external APIs or order methods

OUTPUT FORMAT:
- Python method with Google-style docstring
- Explain any edge case handling in comments
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**DO vs DON'T:**

| DO ✅ | DON'T ❌ |
|-------|---------|
| "Implement calculate_regime_score() that takes trend, vol, breadth, credit as floats (0-100) and returns weighted sum" | "Write the regime calculation" |
| "Based on Section 4.5 of docs/04-regime-engine.md, implement the volatility factor" | "Implement volatility factor" |
| "This engine only EMITS TargetWeight. It does NOT call MarketOrder." | "Build the trend engine" |
| "Explain your implementation choices for edge cases" | (Accept code without understanding) |
| "Line 45 uses > but spec Section 4.6 says >=" | "The regime check is wrong" |
| "Focus on calculate_trend_factor() only for now" | "Implement the entire engine" |

**Prompt Templates by Task Type:**

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
IMPLEMENT NEW COMPONENT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"Read [SPEC_FILE] completely.

Implement [FILE_PATH] with:
- [List of required methods/classes]
- [Key behaviors]

Constraints:
- Follow CLAUDE.md conventions
- Use parameters from config.py
- Include type hints and docstrings
- [Any domain-specific constraints]

Current dependencies available:
- [List what's already implemented]"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FIX A BUG
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"Bug in [FILE_PATH]:

Symptom: [What's happening]
Expected: [What should happen]
Test case: [Failing test or reproduction steps]

Spec reference: [Section that defines correct behavior]

Please:
1. Identify the root cause
2. Fix the code
3. Add/update test to prevent regression"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ADD TESTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"Write unit tests for [FILE_PATH].

Coverage needed:
- Happy path: [Normal operation]
- Edge cases: [Boundary values, empty inputs, etc.]
- Error cases: [Invalid inputs, exceptions]

Reference: [Spec file for expected behavior]

Use:
- pytest with fixtures from conftest.py
- Descriptive test names
- @pytest.mark.[unit|integration|scenario]"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
REFACTOR CODE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"Refactor [FILE_PATH] to:
- [Specific improvement goal]

Constraints:
- Do NOT change external behavior (tests must still pass)
- Maintain same public API
- [Any specific patterns to follow]

Explain what you changed and why."
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

### 8.2 When to Use Claude vs Manual Coding

**Best Use Cases for Claude:**

| Task | Why Claude Excels |
|------|-------------------|
| Boilerplate code | Fast generation of repetitive patterns |
| Test writing | Generates comprehensive test cases |
| Documentation | Produces well-structured docstrings |
| Code from spec | Translates requirements to implementation |
| Refactoring | Consistent application of patterns |
| Bug investigation | Analyzes code flow systematically |
| API integration | Knows common library patterns |

**When to Code Manually:**

| Task | Why Manual is Better |
|------|---------------------|
| Architecture decisions | You understand the system context |
| Security-critical code | Requires deep security expertise |
| Performance-critical code | Needs profiling and optimization |
| Complex business logic | Requires domain expertise |
| Debugging subtle issues | You can use debugger, print statements |
| Quick one-line fixes | Faster to just type it |

**Hybrid Approach (Recommended):**

```
┌─────────────────────────────────────────────────────────────────────┐
│                    HYBRID DEVELOPMENT WORKFLOW                       │
└─────────────────────────────────────────────────────────────────────┘

1. YOU: Design the architecture and interfaces
2. CLAUDE: Implement the skeleton/boilerplate
3. YOU: Review and adjust the structure
4. CLAUDE: Fill in method implementations
5. YOU: Review each method against spec
6. CLAUDE: Write tests
7. YOU: Run tests, identify gaps
8. CLAUDE: Fix issues, add edge case tests
9. YOU: Final review and commit
```

---

### 8.3 Code Review with Claude

**Self-Review Prompt (After Generation):**

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"Review the code you just generated. Check for:

1. LOGIC: Does it match the spec exactly?
2. EDGE CASES: What happens with 0, negative, max values?
3. ERROR HANDLING: Are exceptions caught appropriately?
4. TYPE SAFETY: Are all inputs validated?
5. SECURITY: Any injection risks, unsafe operations?
6. PERFORMANCE: Any obvious inefficiencies?
7. TESTING GAPS: What tests are still needed?

List any issues found with severity (High/Medium/Low) and fixes."
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Cross-Review Prompt (Comparing to Spec):**

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"Compare [FILE_PATH] against [SPEC_FILE].

For each method, verify:
- Parameters match spec exactly
- Logic operators (>, >=, <, <=) are correct
- Formulas are implemented correctly
- Return values match spec

Output a compliance table:
| Method | Spec Section | Compliant? | Issue (if any) |
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**What to Always Verify Manually:**

```markdown
## Manual Review Checklist

### Critical (Always Check)
□ No hardcoded credentials or API keys
□ No unauthorized order placement (strategy engines)
□ Kill switch and risk checks in correct order
□ TQQQ/SOXL never held overnight
□ Lockbox amount never touched

### Important (Spot Check)
□ Parameter values match config.py
□ Logic operators match spec
□ Error messages are informative
□ Logging statements present for key events

### Nice to Have
□ Code style consistent
□ Comments helpful (not obvious)
□ Variable names clear
```

---

### 8.4 Iterative Development

**Don't Try for Perfect in One Shot:**

```
┌─────────────────────────────────────────────────────────────────────┐
│                    ITERATIVE REFINEMENT PATTERN                      │
└─────────────────────────────────────────────────────────────────────┘

Round 1: "Implement basic structure with method stubs"
         → Review skeleton, adjust if needed

Round 2: "Implement core logic for [specific method]"
         → Review logic against spec

Round 3: "Add error handling and edge cases"
         → Review error paths

Round 4: "Add logging for debugging and monitoring"
         → Review log messages

Round 5: "Write unit tests for all public methods"
         → Run tests, fix failures

Round 6: "Add docstrings and type hints"
         → Final polish
```

**Multi-Turn Conversation Patterns:**

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BUILDING ON PREVIOUS WORK
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Turn 1: "Implement RegimeEngine skeleton with method signatures"
Turn 2: "Good. Now implement calculate_trend_factor()"
Turn 3: "Good. Now implement calculate_volatility_factor()"
Turn 4: "Good. Now implement calculate_regime_score() that uses all factors"
Turn 5: "Good. Now add the smoothing logic from Section 4.8"
Turn 6: "Good. Now write tests for all methods"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
REFINEMENT AFTER FEEDBACK
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Turn 1: [Initial implementation]
Turn 2: "This test fails: test_risk_on_at_70. The boundary should be >= not >"
Turn 3: "Good. But you also changed NEUTRAL which should still be > 40"
Turn 4: "Perfect. Now the same fix is needed in get_position_multiplier()"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

### 8.5 Context Management

**What Context to Provide:**

| Always Provide | Sometimes Provide | Never Provide |
|----------------|-------------------|---------------|
| Spec document reference | Related file contents | API keys/secrets |
| File being modified | Previous conversation summary | Credentials |
| Constraints and rules | Error messages | Production data |
| Expected output format | Test results | Customer info |

**How to Chunk Large Work:**

```
┌─────────────────────────────────────────────────────────────────────┐
│                       CHUNKING STRATEGY                              │
└─────────────────────────────────────────────────────────────────────┘

WRONG: "Implement the entire Portfolio Router"

RIGHT:
  Chunk 1: "Implement collect_signals() that gathers from all engines"
  Chunk 2: "Implement validate_signal() that checks with risk engine"
  Chunk 3: "Implement net_signals() that combines same-symbol signals"
  Chunk 4: "Implement execute_signals() that places orders"
  Chunk 5: "Implement process_cycle() that orchestrates everything"
  Chunk 6: "Write tests for each method"
```

**Session Continuity:**

All task state is tracked in `WORKBOARD.md`. When starting a new session:

```
# ALPHA NEXTGEN SESSION RESUME

Read `WORKBOARD.md` and `CLAUDE.md`.

Then confirm:
1. Current phase and what's in progress
2. Your assigned task
3. The spec document for your task

Ready to continue.
```

After completing work, update `WORKBOARD.md`:
- Move your task from "In Progress" to "Done"
- Add PR number if applicable

See **Section 5.8** and `CONTRIBUTING.md` for detailed workflow.

---

### 8.6 Error Recovery

**When Claude Makes a Mistake:**

| Approach | When to Use | Example |
|----------|-------------|---------|
| **Specific correction** | Clear, isolated error | "Line 45: change > to >=" |
| **Test-driven fix** | Logic error | "This test fails: [test output]" |
| **Spec reference** | Misunderstanding | "Per Section 4.6, 40 is included in CAUTIOUS" |
| **Start over** | Fundamentally wrong | "Let's restart. Read the spec first." |

**Effective Error Reporting:**

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
GOOD ERROR REPORT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"Bug in engines/regime_engine.py line 87:

Current code:
  if score > 70:
      return RegimeState.RISK_ON

Problem:
  test_risk_on_at_70() fails
  Expected: RISK_ON
  Actual: NEUTRAL

Spec (docs/04-regime-engine.md Section 4.6):
  'RISK_ON: score >= 70'

The comparison should be >= not >"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

BAD ERROR REPORT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"The regime engine is broken"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**When to Start Over:**

- Claude is going in circles making the same mistake
- The approach is fundamentally wrong
- Too many patches have made code unmaintainable
- You realize the spec was misunderstood from the start

```
"Let's start fresh. Please re-read docs/04-regime-engine.md completely,
then implement engines/regime_engine.py from scratch. I'll review each
method before you proceed to the next."
```

---

### 8.7 Security Considerations

**Never Share with Claude:**

| Category | Examples | Risk |
|----------|----------|------|
| **Credentials** | API keys, passwords, tokens | Exposure in logs/history |
| **Production data** | Real customer data, PII | Privacy violation |
| **Internal URLs** | Production endpoints, admin panels | Security exposure |
| **Secret keys** | Encryption keys, signing keys | Compromise |

**Safe Patterns:**

```python
# WRONG - Hardcoded credential
API_KEY = "sk-abc123secretkey456"

# RIGHT - Environment variable reference
API_KEY = os.environ.get("BROKER_API_KEY")

# When asking Claude about config:
"The API_KEY is loaded from environment variable BROKER_API_KEY.
Implement the authentication using this pattern."
```

**Sanitize Before Sharing:**

```
# WRONG - Real error with credentials
"I'm getting this error:
  AuthError: Invalid key 'sk-abc123secret' for account 12345"

# RIGHT - Sanitized error
"I'm getting this error:
  AuthError: Invalid key '[REDACTED]' for account [REDACTED]"
```

---

### 8.8 Quality Assurance

**Common Claude Mistakes to Watch For:**

| Mistake | How to Spot | Prevention |
|---------|-------------|------------|
| **Off-by-one** | Boundary tests fail | Always test exact boundaries |
| **Wrong operator** | > vs >= confusion | Reference spec in prompt |
| **Missing edge cases** | Null/empty inputs crash | Ask for edge case handling |
| **Inconsistent naming** | Mix of styles | Specify naming convention |
| **Forgotten imports** | Runtime errors | Run code before committing |
| **Hardcoded values** | Values differ from config | Search for magic numbers |
| **Order placement** | Strategy engines calling MarketOrder | Review for order methods |

**Quality Checklist Before Accepting Code:**

```markdown
## Code Acceptance Checklist

### Correctness
□ All tests pass
□ Logic matches spec
□ Edge cases handled

### Style
□ Type hints on all methods
□ Docstrings present
□ Consistent naming
□ No magic numbers (use config.py)

### Safety
□ No hardcoded credentials
□ No unauthorized order calls
□ Risk checks present where needed
□ Errors logged appropriately

### Completeness
□ All spec requirements implemented
□ Logging statements for key events
□ Tests cover happy path + edge cases
```

---

### 8.9 Version Control Best Practices

**Commit After Each Component:**

```bash
# Good commit after implementing a component
git add engines/regime_engine.py tests/test_regime_engine.py
git commit -m "feat(regime): implement RegimeEngine per spec 04

- 4-factor scoring (trend, vol, breadth, credit)
- Exponential smoothing (alpha=0.30)
- State classification (RISK_ON through RISK_OFF)
- Unit tests for boundaries and smoothing

Spec: docs/04-regime-engine.md"
```

**Tag Milestones:**

```bash
# After completing each phase
git tag -a v0.1.0-phase1 -m "Phase 1 complete: Foundation (config, models, utils)"
git tag -a v0.2.0-phase2 -m "Phase 2 complete: Core engines (regime, capital)"
git tag -a v0.3.0-phase3 -m "Phase 3 complete: Strategy engines"
git tag -a v0.4.0-phase4 -m "Phase 4 complete: Coordination (router, risk)"
git tag -a v0.5.0-phase5 -m "Phase 5 complete: Execution & persistence"
git tag -a v1.0.0 -m "Phase 6 complete: Full integration"
```

**Branch Strategy for Claude Work:**

```bash
# Create feature branch for Claude-assisted work
git checkout -b feature/regime-engine

# Multiple small commits as you iterate
git commit -m "feat(regime): add skeleton with method stubs"
git commit -m "feat(regime): implement factor calculations"
git commit -m "feat(regime): add smoothing logic"
git commit -m "test(regime): add unit tests"

# Squash or keep history based on preference
git checkout develop
git merge feature/regime-engine
```

---

### 8.10 Documentation Hygiene

**Update Docs When Code Changes:**

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DOC UPDATE PROMPT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"I changed the kill switch threshold from 3% to 2.5% in config.py.

Update the following files to reflect this change:
1. docs/12-risk-engine.md (Section 12.2)
2. docs/16-appendix-parameters.md (Risk Engine section)
3. CLAUDE.md (Quick Reference - Key Thresholds table)
4. tests/test_spec_compliance.py (update expected value)

Show me the diff for each file."
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Keep WORKBOARD.md Updated:**

Track task ownership and progress in `WORKBOARD.md`:

```markdown
# Workflow

1. Pick from "Ready to Start" -> Move to "In Progress"
2. Add your initials and branch name
3. Create branch: feature/<initials>/<component>
4. Code -> PR -> Move to "In Review"
5. After merge -> Move to "Done"
```

See `CONTRIBUTING.md` for branch naming and commit message conventions.

---

### 8.11 Troubleshooting Claude Issues

**Common Issues and Solutions:**

| Issue | Symptom | Solution |
|-------|---------|----------|
| **Context lost** | Claude forgets previous work | Provide session resume template |
| **Going in circles** | Same mistake repeated | Be more explicit, or start fresh |
| **Overcomplicating** | Too much abstraction | "Keep it simple. Just implement X." |
| **Wrong assumptions** | Code doesn't match intent | Provide more constraints upfront |
| **Incomplete output** | Code cuts off mid-function | "Continue from where you left off" |
| **Hallucinating APIs** | Non-existent methods used | "Use only QC APIs from documentation" |

**When Claude Gets Stuck:**

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RESET PROMPT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"Let's step back. I think there's confusion about [X].

Here's what I need:
1. [Clear requirement 1]
2. [Clear requirement 2]
3. [Clear requirement 3]

Constraints:
- [Constraint 1]
- [Constraint 2]

Please start fresh with just [specific method/class]."
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Context Exhaustion Signs:**

- Claude starts repeating itself
- Responses become shorter or less detailed
- Claude "forgets" earlier constraints
- Code quality decreases

**Solution:** Start a new session with a fresh context summary:

```
"Starting fresh session for Alpha NextGen.

Context files to read:
- CLAUDE.md (conventions)
- developer-guide-claude.md (Section 5 for workflow)

Current state: [Brief summary]
Next task: [Specific next step]"
```

---

## 9. Debugging Strategies

> **Debugging Philosophy:** Systematic debugging beats random changes. Gather information, form a hypothesis, test it, repeat until solved.

---

### 9.1 Debugging Methodology

**Systematic Approach:**

```
┌─────────────────────────────────────────────────────────────────────┐
│                    DEBUGGING WORKFLOW                                │
└─────────────────────────────────────────────────────────────────────┘

1. REPRODUCE
   ├── Can you reliably reproduce the issue?
   ├── What are the exact steps/conditions?
   └── Is it date-specific, time-specific, or data-specific?

2. GATHER INFORMATION
   ├── What do the logs say?
   ├── What are the relevant variable values?
   └── What should be happening vs what is happening?

3. ISOLATE
   ├── Which component is responsible?
   ├── Remove complexity (test single component)
   └── Add targeted logging

4. HYPOTHESIZE
   ├── What could cause this behavior?
   ├── List possible causes by likelihood
   └── What would confirm/deny each hypothesis?

5. TEST
   ├── Add logging to confirm hypothesis
   ├── Write a unit test that reproduces the bug
   └── Try the fix

6. FIX & VERIFY
   ├── Implement the fix
   ├── Verify the unit test passes
   └── Run broader tests to check for regressions
```

**Quick Diagnostic Questions:**

| Question | What It Reveals |
|----------|-----------------|
| "Does it happen in backtest AND live?" | Environment issue vs logic issue |
| "Does it happen every time or randomly?" | Deterministic bug vs race condition |
| "Did it ever work correctly?" | Regression vs original bug |
| "What changed recently?" | Likely cause of regression |
| "Does it happen for all symbols or just one?" | Symbol-specific data issue |
| "Does it happen at specific times?" | Schedule/timing issue |

---

### 9.2 Common Issues and Solutions

**Indicator Issues:**

| Issue | Symptom | Likely Cause | Solution |
|-------|---------|--------------|----------|
| Indicator not ready | `None` or exception on access | Insufficient warmup | Increase `SetWarmUp()` or check `IsReady` |
| Wrong indicator values | Values don't match expected | Wrong period or resolution | Verify indicator parameters |
| Stale indicator | Same value repeatedly | Not being updated | Check indicator is registered properly |
| NaN/Inf values | Calculations explode | Division by zero or bad data | Add validation, handle edge cases |

**Order Issues:**

| Issue | Symptom | Likely Cause | Solution |
|-------|---------|--------------|----------|
| Orders not filling | No trades in backtest | Wrong order type/timing | Use MOO correctly, check market hours |
| Partial fills | Less quantity than expected | Insufficient liquidity | Use FillModel, check volume |
| Wrong position size | Position larger/smaller than expected | Wrong calculation | Verify tradeable equity calculation |
| Orders rejected | Error in logs | Insufficient funds, invalid params | Check portfolio value, order validation |
| Duplicate orders | Same order placed multiple times | Missing state check | Add `is_already_invested` check |

**Timing Issues:**

| Issue | Symptom | Likely Cause | Solution |
|-------|---------|--------------|----------|
| Schedule not firing | Event never runs | Wrong time/date rule | Log schedule registration, verify times |
| Wrong timezone | Events at wrong time | UTC vs Eastern confusion | Use `TimeZones.NewYork` explicitly |
| Pre-market issues | Data missing or weird | Accessing extended hours | Check `ExchangeHours.IsOpen` |
| TQQQ held overnight | Position not closed | Force close not triggering | Check 15:45 schedule, add redundant check |

**Risk/Circuit Breaker Issues:**

| Issue | Symptom | Likely Cause | Solution |
|-------|---------|--------------|----------|
| Kill switch not triggering | No liquidation at 3% loss | Wrong baseline or calculation | Log both values, verify formula |
| Kill switch triggering too early | Liquidation before 3% | Wrong equity comparison | Using SOD vs prior close incorrectly |
| Panic mode false positive | Liquidation without SPY drop | Wrong SPY calculation | Check SPY gap calculation |
| Weekly breaker not resetting | Stuck in reduced sizing | Reset logic error | Verify Monday reset |

**State/Persistence Issues:**

| Issue | Symptom | Likely Cause | Solution |
|-------|---------|--------------|----------|
| State not loading | Variables reset on restart | Load not called or failing | Check `Initialize()` load, add logging |
| State corrupted | Unexpected values | Serialization issue | Validate on load, use schema |
| Days running wrong | Cold start behavior incorrect | Counter not persisting | Add days_running to state |
| Positions out of sync | Algo thinks it owns what it doesn't | No reconciliation | Call reconcile on startup |

---

### 9.3 Logging Best Practices

**Log Levels and Usage:**

| Level | Method | When to Use | Example |
|-------|--------|-------------|---------|
| **Debug** | `self.Debug()` | Detailed debugging (disable in production) | Variable values, calculation steps |
| **Log** | `self.Log()` | Important events | Order placement, state changes |
| **Error** | `self.Error()` | Problems that need attention | Failed operations, data issues |

**Structured Logging Format:**

```python
# Pattern: COMPONENT_EVENT: key=value | key=value

# Good examples
self.Log(f"REGIME_UPDATE: score={score:.1f} | state={state} | smoothed={smoothed:.1f}")
self.Log(f"TREND_ENTRY: symbol=QLD | price={price:.2f} | weight=0.30 | reason=BB_BREAKOUT")
self.Log(f"KILL_SWITCH: triggered=True | loss_pct={loss_pct:.4f} | baseline={baseline:.2f}")
self.Log(f"ORDER_PLACED: symbol=QLD | qty=100 | type=MOO | urgency=EOD")

# Bad examples (avoid)
self.Log("Entered position")  # No context
self.Log(f"score is {score}")  # No component prefix
```

**What to Log When:**

```python
class LoggingMixin:
    """Standard logging patterns for all engines."""

    def log_state_change(self, component: str, old_value, new_value, reason: str = ""):
        """Log when important state changes."""
        self.algorithm.Log(
            f"{component}_STATE_CHANGE: old={old_value} | new={new_value} | reason={reason}"
        )

    def log_signal(self, component: str, symbol: str, action: str, details: dict):
        """Log when a signal is generated."""
        detail_str = " | ".join(f"{k}={v}" for k, v in details.items())
        self.algorithm.Log(f"{component}_SIGNAL: symbol={symbol} | action={action} | {detail_str}")

    def log_decision(self, component: str, decision: str, factors: dict):
        """Log why a decision was made."""
        factor_str = " | ".join(f"{k}={v}" for k, v in factors.items())
        self.algorithm.Log(f"{component}_DECISION: result={decision} | {factor_str}")


# Usage in engines
class RegimeEngine(LoggingMixin):
    def update(self):
        old_state = self.current_state
        new_state = self.calculate_state()

        if old_state != new_state:
            self.log_state_change("REGIME", old_state, new_state, f"score={self.score:.1f}")

        return new_state
```

**Debug Logging Template:**

```python
# Add DEBUG_ prefix for logs that should be removed in production
def OnData(self, data):
    # Periodic state dump (every 30 minutes during market hours)
    if self.Time.minute == 0 and self.Time.hour % 1 == 0:
        self.Debug(f"DEBUG_STATE: Time={self.Time}")
        self.Debug(f"DEBUG_STATE: Equity={self.Portfolio.TotalPortfolioValue:,.2f}")
        self.Debug(f"DEBUG_STATE: Cash={self.Portfolio.Cash:,.2f}")
        self.Debug(f"DEBUG_STATE: Regime={self.regime_engine.get_regime_state()}")
        self.Debug(f"DEBUG_STATE: Baseline={self.equity_sod:,.2f}")

        # Log all positions
        for symbol in self.traded_symbols:
            h = self.Portfolio[symbol]
            if h.Invested:
                self.Debug(f"DEBUG_POSITION: {symbol} | qty={h.Quantity} | "
                          f"avg={h.AveragePrice:.2f} | pnl={h.UnrealizedProfit:,.2f}")
```

---

### 9.4 QuantConnect-Specific Debugging

**Indicator Debugging:**

```python
def debug_indicators(self):
    """Log all indicator states for debugging."""
    self.Debug("="*60)
    self.Debug(f"DEBUG_INDICATORS: Time={self.Time}")

    # Check if indicators are ready
    for name, indicator in self.indicators.items():
        self.Debug(f"  {name}: IsReady={indicator.IsReady} | Value={indicator.Current.Value:.4f}")

    # Specific indicator details
    if hasattr(self, 'bb_qld'):
        bb = self.bb_qld
        self.Debug(f"  BB_QLD: Upper={bb.UpperBand.Current.Value:.2f} | "
                  f"Mid={bb.MiddleBand.Current.Value:.2f} | "
                  f"Lower={bb.LowerBand.Current.Value:.2f} | "
                  f"BW={bb.BandWidth.Current.Value:.4f}")

    if hasattr(self, 'rsi_tqqq'):
        self.Debug(f"  RSI_TQQQ: {self.rsi_tqqq.Current.Value:.2f}")

    self.Debug("="*60)
```

**Order Debugging:**

```python
def OnOrderEvent(self, orderEvent):
    """Log all order events for debugging."""
    order = self.Transactions.GetOrderById(orderEvent.OrderId)

    self.Log(f"ORDER_EVENT: {orderEvent.Symbol} | "
            f"Status={orderEvent.Status} | "
            f"Fill={orderEvent.FillQuantity}@{orderEvent.FillPrice:.2f} | "
            f"Type={order.Type} | "
            f"Direction={order.Direction}")

    if orderEvent.Status == OrderStatus.Invalid:
        self.Error(f"ORDER_INVALID: {orderEvent.Symbol} | Message={orderEvent.Message}")

    if orderEvent.Status == OrderStatus.Filled:
        self.Log(f"ORDER_FILLED: {orderEvent.Symbol} | "
                f"Qty={orderEvent.FillQuantity} | "
                f"Price={orderEvent.FillPrice:.2f} | "
                f"Value=${orderEvent.FillQuantity * orderEvent.FillPrice:,.2f}")
```

**Schedule Debugging:**

```python
def Initialize(self):
    # Log all scheduled events
    self.Debug("SCHEDULE_SETUP: Registering scheduled events...")

    # Market open setup (9:25)
    self.Schedule.On(
        self.DateRules.EveryDay("SPY"),
        self.TimeRules.At(9, 25),
        self.on_pre_market
    )
    self.Debug("SCHEDULE_SETUP: Registered on_pre_market at 09:25")

    # Force close (15:45)
    self.Schedule.On(
        self.DateRules.EveryDay("SPY"),
        self.TimeRules.At(15, 45),
        self.on_force_close
    )
    self.Debug("SCHEDULE_SETUP: Registered on_force_close at 15:45")

def on_pre_market(self):
    self.Debug(f"SCHEDULE_FIRED: on_pre_market at {self.Time}")
    # ... actual logic

def on_force_close(self):
    self.Debug(f"SCHEDULE_FIRED: on_force_close at {self.Time}")
    # ... actual logic
```

**Data Debugging:**

```python
def OnData(self, data):
    # Log when data is missing
    for symbol in self.symbols:
        if symbol not in data.Bars:
            self.Debug(f"DEBUG_DATA: No bar for {symbol} at {self.Time}")

    # Log split events
    if data.Splits.Count > 0:
        for split in data.Splits.Values:
            self.Log(f"SPLIT_DETECTED: {split.Symbol} | "
                    f"Factor={split.SplitFactor} | "
                    f"Type={split.Type}")

    # Log dividend events
    if data.Dividends.Count > 0:
        for div in data.Dividends.Values:
            self.Log(f"DIVIDEND_DETECTED: {div.Symbol} | Amount={div.Distribution:.4f}")
```

---

### 9.5 Backtest Debugging

**Binary Search for Date-Specific Issues:**

```
┌─────────────────────────────────────────────────────────────────────┐
│                    BINARY SEARCH DEBUGGING                           │
└─────────────────────────────────────────────────────────────────────┘

Full backtest fails (2020-01-01 to 2024-01-01)

Step 1: Test first half (2020-01-01 to 2022-01-01)
        ├── Pass? → Problem is in 2022-2024
        └── Fail? → Problem is in 2020-2022

Step 2: Test first quarter of problem range
        Continue halving until you isolate the week...

Step 3: Found problem week, test single days
        └── Found: 2021-03-15 causes the crash

Step 4: Investigate that specific date
        └── What happened? Split? Dividend? Market closure?
```

**Running Specific Date Range:**

```python
def Initialize(self):
    # Narrow date range for debugging
    self.SetStartDate(2021, 3, 14)  # Day before problem
    self.SetEndDate(2021, 3, 16)    # Day after problem

    # Add extra logging for this debug session
    self.debug_mode = True
```

**Comparing Backtest Runs:**

```bash
# Run multiple backtests with variations
lean cloud backtest "alpha-nextgen" --name "Debug-Baseline"
lean cloud backtest "alpha-nextgen" --name "Debug-NoRiskChecks"
lean cloud backtest "alpha-nextgen" --name "Debug-LoggingEnabled"

# Compare results
# - Same trades? → Risk check issue
# - Different entries? → Signal generation issue
# - Different exits? → Exit logic issue
```

**Backtest Debugging Checklist:**

```markdown
## Backtest Debug Checklist

### Before Running
□ Date range set correctly
□ Starting cash set correctly
□ Benchmark set (SPY)
□ Debug logging enabled

### Check Runtime
□ Warmup period sufficient (indicators ready)
□ No runtime errors in logs
□ Scheduled events firing

### Check Logic
□ Regime scores reasonable (0-100)
□ Positions within exposure limits
□ Risk checks executing
□ Overnight positions correct

### Check Results
□ Number of trades reasonable
□ Sharpe ratio reasonable
□ Max drawdown acceptable
□ No unexplained large gains/losses
```

---

### 9.6 State and Persistence Debugging

**Debugging ObjectStore:**

```python
def debug_state_persistence(self):
    """Debug state save/load issues."""

    # Check what's in ObjectStore
    self.Debug("DEBUG_STATE: Checking ObjectStore contents...")

    key = "alpha_nextgen_state"
    if self.ObjectStore.ContainsKey(key):
        raw_data = self.ObjectStore.Read(key)
        self.Debug(f"DEBUG_STATE: Found key '{key}'")
        self.Debug(f"DEBUG_STATE: Raw length = {len(raw_data)}")

        try:
            import json
            state = json.loads(raw_data)
            self.Debug(f"DEBUG_STATE: Parsed successfully")
            for k, v in state.items():
                self.Debug(f"DEBUG_STATE:   {k} = {v}")
        except Exception as e:
            self.Error(f"DEBUG_STATE: Failed to parse: {e}")
    else:
        self.Debug(f"DEBUG_STATE: Key '{key}' not found")


def save_state_with_debug(self):
    """Save state with debugging info."""
    state = {
        "days_running": self.days_running,
        "equity_high_water_mark": self.equity_hwm,
        "last_regime_score": self.regime_engine.score,
        "saved_at": str(self.Time),
    }

    self.Debug(f"DEBUG_SAVE: Saving state: {state}")

    try:
        import json
        self.ObjectStore.Save("alpha_nextgen_state", json.dumps(state))
        self.Debug("DEBUG_SAVE: State saved successfully")
    except Exception as e:
        self.Error(f"DEBUG_SAVE: Failed to save: {e}")
```

**Position Reconciliation Debugging:**

```python
def reconcile_positions_with_debug(self):
    """Reconcile algorithm state with actual positions."""
    self.Debug("DEBUG_RECONCILE: Starting position reconciliation...")

    # What we think we have
    expected_positions = self.position_manager.get_all_positions()

    # What we actually have
    actual_positions = {}
    for symbol in self.traded_symbols:
        holding = self.Portfolio[symbol]
        if holding.Invested:
            actual_positions[symbol] = {
                "quantity": holding.Quantity,
                "avg_price": holding.AveragePrice,
            }

    # Compare
    self.Debug(f"DEBUG_RECONCILE: Expected: {expected_positions}")
    self.Debug(f"DEBUG_RECONCILE: Actual: {actual_positions}")

    # Find discrepancies
    for symbol in set(expected_positions.keys()) | set(actual_positions.keys()):
        expected = expected_positions.get(symbol, {})
        actual = actual_positions.get(symbol, {})

        if expected != actual:
            self.Log(f"RECONCILE_MISMATCH: {symbol} | "
                    f"expected={expected} | actual={actual}")
            # Fix: update our state to match reality
            self.position_manager.sync_from_portfolio(symbol, actual)
```

---

### 9.7 Using Claude to Debug

**Diagnostic Prompt:**

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DIAGNOSE ISSUE PROMPT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"I have a bug in the trading system.

SYMPTOM:
[Describe what's happening]

EXPECTED:
[Describe what should happen]

RELEVANT CODE:
[Paste the relevant method/class]

DEBUG LOGS:
[Paste relevant log output]

CONTEXT:
- This is the [component name] in Alpha NextGen
- Spec: [reference to spec section]
- Recent changes: [what changed recently, if known]

Please:
1. Analyze the logs and code
2. Identify likely root cause(s)
3. Suggest specific fix(es)
4. Recommend a test case to prevent regression"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Add Debug Logging Prompt:**

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"Add comprehensive debug logging to [FILE_PATH] to help diagnose
[describe the issue].

Requirements:
- Use DEBUG_ prefix for temporary debug logs
- Use structured format: 'DEBUG_COMPONENT: key=value | key=value'
- Log at key decision points
- Log input values and calculated results
- Log state before and after changes

Focus on the [specific method/area] where the issue likely occurs."
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Root Cause Analysis Prompt:**

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"Help me do root cause analysis for this bug.

BUG:
[Description]

OBSERVATIONS:
- [Observation 1]
- [Observation 2]
- [Observation 3]

HYPOTHESES I'VE CONSIDERED:
1. [Hypothesis 1] - [why I think it might/might not be this]
2. [Hypothesis 2] - [why I think it might/might not be this]

What other hypotheses should I consider?
How can I test each hypothesis?
What additional information would help narrow down the cause?"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

### 9.8 Debugging Tools

**QuantConnect Tools:**

| Tool | Purpose | How to Access |
|------|---------|---------------|
| **Backtest Logs** | View all `Log()` output | Algorithm Lab → Backtest → Logs tab |
| **Order Events** | See all order activity | Algorithm Lab → Backtest → Orders tab |
| **Insights** | View algorithm insights | Algorithm Lab → Backtest → Insights tab |
| **Charts** | Visual price/indicator data | Algorithm Lab → Backtest → Charts tab |

**Local Debugging Tools:**

```bash
# Run with verbose pytest output
pytest tests/test_regime_engine.py -v -s --tb=long

# Debug specific test with pdb
pytest tests/test_regime_engine.py::test_specific -v --pdb

# Run with logging visible
pytest tests/ -v -s --log-cli-level=DEBUG
```

**VS Code Debugging Setup:**

```json
// .vscode/launch.json
{
    "version": "0.2.0",
    "configurations": [
        {
            "name": "Debug Unit Test",
            "type": "python",
            "request": "launch",
            "module": "pytest",
            "args": [
                "tests/test_regime_engine.py",
                "-v",
                "-s"
            ],
            "cwd": "${workspaceFolder}",
            "env": {
                "PYTHONPATH": "${workspaceFolder}"
            }
        },
        {
            "name": "Debug Specific Test",
            "type": "python",
            "request": "launch",
            "module": "pytest",
            "args": [
                "tests/test_regime_engine.py::TestRegimeScoreClassification::test_risk_on_at_70",
                "-v",
                "-s"
            ],
            "cwd": "${workspaceFolder}"
        }
    ]
}
```

**Debug Mode Toggle:**

```python
# In config.py
DEBUG_MODE = True  # Set to False for production

# In engines
class RegimeEngine:
    def __init__(self, algorithm):
        self.algorithm = algorithm
        self.debug = getattr(algorithm, 'debug_mode', False) or DEBUG_MODE

    def update(self):
        if self.debug:
            self.algorithm.Debug(f"DEBUG_REGIME: Starting update at {self.algorithm.Time}")

        # ... logic ...

        if self.debug:
            self.algorithm.Debug(f"DEBUG_REGIME: Score={self.score:.1f} State={self.state}")
```

---

## 10. Appendix: Command Reference

> **Tip:** Bookmark this section for quick access during development.

---

### 10.1 Claude Code Commands

**Basic Commands:**

| Command | Description |
|---------|-------------|
| `claude` | Start interactive Claude Code session |
| `claude "prompt"` | Run single prompt without entering interactive mode |
| `claude --help` | Show all available commands and options |
| `claude --version` | Show Claude Code version |

**Session Management:**

```bash
# Start new session in current directory
claude

# Start with specific context file
claude --context CLAUDE.md

# Resume previous session
claude --resume

# Clear context and start fresh
/clear  # (inside claude session)
```

**In-Session Commands:**

| Command | Description |
|---------|-------------|
| `/help` | Show available commands |
| `/clear` | Clear conversation context |
| `/compact` | Summarize and compact context |
| `/cost` | Show token usage and cost |
| `/quit` or `/exit` | Exit Claude Code |

**Useful Patterns:**

```bash
# Quick file read and question
claude "Read engines/regime_engine.py and explain the calculate_regime_score method"

# Quick code generation
claude "Write a pytest test for the CapitalEngine.get_tradeable_equity method"

# Multi-file context
claude "Compare engines/trend_engine.py against docs/07-trend-engine.md for mismatches"
```

---

### 10.2 Lean CLI Commands

**Authentication:**

```bash
# Interactive login
lean login

# Login with credentials
lean login --user-id YOUR_USER_ID --api-token YOUR_API_TOKEN

# Check current login
lean whoami

# Logout
lean logout
```

**Project Setup:**

```bash
# Initialize Lean in current directory
lean init

# Create new project
lean create-project "alpha-nextgen" --language python
```

**Cloud Operations:**

```bash
# Push local code to QuantConnect cloud
lean cloud push

# Push specific project
lean cloud push --project "alpha-nextgen"

# Pull from cloud (sync changes made in web IDE)
lean cloud pull

# Check sync status
lean cloud status
```

**Backtesting:**

```bash
# Run cloud backtest (default settings)
lean cloud backtest "alpha-nextgen"

# Run with custom name (for tracking)
lean cloud backtest "alpha-nextgen" --name "Phase3-Test-$(date +%Y%m%d)"

# Run and open results in browser
lean cloud backtest "alpha-nextgen" --open

# List recent backtests
lean cloud backtest list "alpha-nextgen"

# Delete a backtest
lean cloud backtest delete "alpha-nextgen" --backtest "BACKTEST_ID"
```

**Local Backtesting (Docker):**

```bash
# Run local backtest
lean backtest "alpha-nextgen"

# Run with specific output directory
lean backtest "alpha-nextgen" --output ./backtests/local/

# Generate HTML report from last backtest
lean report

# Download market data for local backtests
lean data download --dataset "US Equities" --resolution "Minute"
```

**Live Trading:**

```bash
# Deploy to live trading
lean live "alpha-nextgen" --brokerage "Interactive Brokers"

# Deploy to paper trading
lean live "alpha-nextgen" --brokerage "Interactive Brokers" --environment paper

# Stop live algorithm
lean live stop "alpha-nextgen"

# List live algorithms
lean live list
```

---

### 10.3 Python & Pytest Commands

**Virtual Environment:**

```bash
# Create virtual environment
python -m venv venv

# Activate (macOS/Linux)
source venv/bin/activate

# Activate (Windows)
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Deactivate
deactivate
```

**Running Tests:**

```bash
# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_regime_engine.py -v

# Run specific test class
pytest tests/test_regime_engine.py::TestRegimeScoreClassification -v

# Run specific test method
pytest tests/test_regime_engine.py::TestRegimeScoreClassification::test_risk_on_at_70 -v

# Run tests matching pattern
pytest tests/ -k "regime" -v
pytest tests/ -k "test_kill" -v
```

**Test Options:**

| Option | Description |
|--------|-------------|
| `-v` | Verbose output (show test names) |
| `-vv` | More verbose (show assertion details) |
| `-s` | Show print statements and logs |
| `-x` | Stop on first failure |
| `--lf` | Run only tests that failed last time |
| `--ff` | Run failed tests first, then rest |
| `-n auto` | Run tests in parallel (requires pytest-xdist) |
| `--pdb` | Drop into debugger on failure |
| `--tb=short` | Shorter traceback format |
| `--tb=long` | Full traceback |

**Test Coverage:**

```bash
# Run with coverage report
pytest tests/ --cov=engines --cov=portfolio --cov=models

# Generate HTML coverage report
pytest tests/ --cov=engines --cov-report=html
open htmlcov/index.html  # View in browser

# Fail if coverage below threshold
pytest tests/ --cov=engines --cov-fail-under=80

# Show lines missing coverage
pytest tests/ --cov=engines --cov-report=term-missing
```

**Test Markers:**

```bash
# Run only unit tests
pytest tests/ -m unit

# Run only integration tests
pytest tests/ -m integration

# Run only scenario tests
pytest tests/ -m scenario

# Skip slow tests
pytest tests/ -m "not slow"

# Run multiple markers
pytest tests/ -m "unit or integration"
```

---

### 10.4 Code Quality Commands

**Formatting with Black:**

```bash
# Format all Python files
black .

# Format specific directory
black engines/ portfolio/

# Check formatting (don't modify)
black --check .

# Show diff of what would change
black --diff .

# Format with specific line length
black --line-length 100 .
```

**Import Sorting with isort:**

```bash
# Sort imports in all files
isort .

# Sort specific directory
isort engines/

# Check only (don't modify)
isort --check-only .

# Show diff
isort --diff .

# Compatible with Black
isort --profile black .
```

**Linting with Flake8:**

```bash
# Run flake8 on all files
flake8 .

# Run on specific directory
flake8 engines/ portfolio/

# Ignore specific errors
flake8 --ignore=E501,W503 .

# Show statistics
flake8 --statistics .
```

**Type Checking with mypy:**

```bash
# Check specific directories
mypy engines/ portfolio/ models/

# Ignore missing imports (for QC imports)
mypy engines/ --ignore-missing-imports

# Strict mode
mypy engines/ --strict

# Generate HTML report
mypy engines/ --html-report mypy_report/
```

**All Quality Checks (One Command):**

```bash
# Run all checks
black --check . && isort --check-only . && flake8 . && mypy engines/ portfolio/ --ignore-missing-imports

# Or create a script (scripts/quality_check.sh)
#!/bin/bash
echo "Running Black..."
black --check . || exit 1
echo "Running isort..."
isort --check-only . || exit 1
echo "Running flake8..."
flake8 . || exit 1
echo "Running mypy..."
mypy engines/ portfolio/ models/ --ignore-missing-imports || exit 1
echo "All checks passed!"
```

---

### 10.5 Git Commands

**Daily Workflow:**

```bash
# Check status
git status

# Pull latest changes
git pull origin develop

# Create feature branch
git checkout -b feature/regime-engine

# Stage specific files
git add engines/regime_engine.py tests/test_regime_engine.py

# Stage all changes
git add -A

# Commit with message
git commit -m "feat(regime): implement RegimeEngine scoring"

# Push to remote
git push origin feature/regime-engine
```

**Viewing Changes:**

```bash
# See unstaged changes
git diff

# See staged changes
git diff --cached

# See changes in specific file
git diff engines/regime_engine.py

# See commit history
git log --oneline -10

# See changes in last commit
git show HEAD
```

**Branch Management:**

```bash
# List all branches
git branch -a

# Switch to existing branch
git checkout develop

# Create and switch to new branch
git checkout -b feature/new-feature

# Delete local branch
git branch -d feature/old-feature

# Delete remote branch
git push origin --delete feature/old-feature

# Merge branch into current
git merge feature/regime-engine
```

**Tags and Releases:**

```bash
# Create annotated tag
git tag -a v0.1.0 -m "Phase 1: Foundation complete"

# List tags
git tag -l

# Push tags to remote
git push origin v0.1.0
git push origin --tags  # Push all tags

# Checkout specific tag
git checkout v0.1.0
```

**Undoing Changes:**

```bash
# Unstage a file
git reset HEAD engines/regime_engine.py

# Discard changes in working directory (CAREFUL!)
git checkout -- engines/regime_engine.py

# Amend last commit (before push)
git commit --amend -m "New commit message"

# Revert a commit (creates new commit)
git revert HEAD
```

---

### 10.6 Project Scripts

**Validation Scripts:**

```bash
# Validate config against specs
python scripts/validate_config.py

# Check spec parity
python scripts/check_spec_parity.py

# Validate all __init__.py files exist
python scripts/check_init_files.py
```

**Development Scripts:**

```bash
# Run all pre-commit checks
./scripts/pre_commit.sh

# Generate test coverage report
./scripts/coverage_report.sh

# Clean up generated files
./scripts/clean.sh
```

**Example: scripts/pre_commit.sh**

```bash
#!/bin/bash
# scripts/pre_commit.sh - Run before committing

set -e  # Exit on error

echo "=== Running Pre-Commit Checks ==="

echo "1. Formatting check..."
black --check engines/ portfolio/ models/ tests/

echo "2. Import sorting check..."
isort --check-only engines/ portfolio/ models/ tests/

echo "3. Type checking..."
mypy engines/ portfolio/ models/ --ignore-missing-imports

echo "4. Config validation..."
python scripts/validate_config.py

echo "5. Running unit tests..."
pytest tests/unit/ -v --tb=short

echo "=== All Pre-Commit Checks Passed ==="
```

---

### 10.7 Environment Setup Commands

**Initial Setup (One-Time):**

```bash
# Clone repository
git clone https://github.com/YOUR_ORG/alpha-nextgen.git
cd alpha-nextgen

# Create virtual environment
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows

# Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Initialize Lean CLI
lean init
lean login

# Verify setup
pytest tests/unit/ -v --tb=short
python scripts/validate_config.py
```

**VS Code Setup:**

```bash
# Install recommended extensions (from .vscode/extensions.json)
code --install-extension ms-python.python
code --install-extension ms-python.black-formatter
code --install-extension ms-python.isort
code --install-extension ms-python.mypy-type-checker

# Open project
code .
```

**Docker Setup (for Local Backtesting):**

```bash
# Install Docker Desktop (if not installed)
# https://www.docker.com/products/docker-desktop

# Verify Docker is running
docker --version

# Pull Lean Docker image (happens automatically, but can pre-pull)
docker pull quantconnect/lean

# Run first local backtest to verify
lean backtest "alpha-nextgen"
```

---

### 10.8 Quick Reference Tables

**Key Files:**

| File | Purpose |
|------|---------|
| `WORKBOARD.md` | **Task tracking & ownership** - who's working on what |
| `CONTRIBUTING.md` | **Git workflow** - branch naming, commit format, PR process |
| `CLAUDE.md` | Project rules and conventions for Claude |
| `developer-guide-claude.md` | This development guide |
| `config.py` | All tunable parameters |
| `main.py` | Algorithm entry point |
| `QC_RULES.md` | QuantConnect-specific patterns |
| `ERRORS.md` | Common errors and solutions |

**Key Directories:**

| Directory | Contents |
|-----------|----------|
| `engines/` | All strategy and core engines |
| `portfolio/` | Router, exposure groups, positions |
| `execution/` | Order management |
| `models/` | Data classes and enums |
| `persistence/` | State save/load |
| `tests/` | All tests (unit, integration, scenario) |
| `docs/` | Specification documents |
| `scripts/` | Utility scripts |

**Key Times (Eastern):**

| Time | Event |
|------|-------|
| 09:25 | Pre-market setup |
| 09:30 | Market open, MOO orders execute |
| 09:31 | MOO fallback check |
| 09:33 | Set equity_sod, gap filter check |
| 10:00 | MR entry window opens |
| 13:55 | Time guard starts (entries blocked) |
| 14:10 | Time guard ends |
| 15:00 | MR entry window closes |
| 15:45 | **TQQQ/SOXL force close** |
| 16:00 | Market close, state persistence |

**Key Thresholds:**

| Threshold | Value | Effect |
|-----------|-------|--------|
| Kill Switch | 3% daily loss | Liquidate ALL positions |
| Panic Mode | SPY -4% intraday | Liquidate longs only |
| Weekly Breaker | 5% WTD loss | 50% sizing reduction |
| Gap Filter | SPY -1.5% gap | Block MR entries |
| Vol Shock | 3× ATR bar | 15-min entry pause |
| BB Compression | < 10% bandwidth | Trend entry eligible |
| RSI Oversold | < 25 | MR entry eligible |

**Exposure Limits:**

| Group | Symbols | Max Net Long | Max Gross |
|-------|---------|:------------:|:---------:|
| NASDAQ_BETA | TQQQ, QLD, SOXL, PSQ | 50% | 75% |
| SPY_BETA | SSO | 40% | 40% |
| RATES | TMF, SHV | 40% | 40% |

**Overnight Rules:**

| Symbol | Strategy | Overnight? |
|--------|----------|:----------:|
| QLD | Trend (2×) | ✅ Yes |
| SSO | Trend (2×) | ✅ Yes |
| TMF | Hedge (3×) | ✅ Yes |
| PSQ | Hedge (1×) | ✅ Yes |
| SHV | Yield | ✅ Yes |
| TQQQ | MR (3×) | ❌ **Close 15:45** |
| SOXL | MR (3×) | ❌ **Close 15:45** |

---

## Quick Start Checklist

```markdown
## Initial Setup (One-Time)

### Environment
□ Python 3.11+ installed
□ Git installed
□ Docker Desktop installed (for local backtests)
□ VS Code installed with Python extensions

### Repository
□ Clone alpha-nextgen repository
□ Create and activate virtual environment
□ Install requirements.txt
□ Install requirements-dev.txt

### QuantConnect
□ Create QuantConnect account
□ Get API credentials (User ID + Token)
□ Install Lean CLI (pip install lean)
□ Run `lean login` with credentials
□ Run `lean init` in project directory

### Verification
□ Run `pytest tests/unit/ -v` - all pass
□ Run `python scripts/validate_config.py` - no mismatches
□ Run `lean cloud push` - uploads successfully
□ Run `lean cloud backtest` - completes without errors

### Ready to Develop
□ Read CLAUDE.md completely
□ Read this developer guide (Sections 1-5 minimum)
□ Understand the 6-phase build order
□ Begin Phase 1: Foundation
```

---

## Document History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | Jan 2025 | Initial comprehensive guide |
| 1.1 | Jan 2025 | Sections 4-10 expanded |

---

*This guide is a living document. Update it as you discover new patterns and practices.*

*Last Updated: January 2025*
