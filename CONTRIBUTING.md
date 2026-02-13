# Contributing to Alpha NextGen V2

This document outlines the coding rules and development workflow for Alpha NextGen V2.

> **V2 Fork Note**: This repository was forked from V1 v1.0.0 on 2026-01-26.
> Uses Core-Satellite architecture. See `V2_IMPLEMENTATION_ROADMAP.md` for roadmap.

---

## Table of Contents

1. [Local Development Setup](#local-development-setup)
2. [Branching Strategy](#branching-strategy)
3. [Release Process](#release-process)
4. [Commit Message Standards](#commit-message-standards)
5. [Development Workflow](#development-workflow)
6. [Pull Request Guidelines](#pull-request-guidelines)
7. [Architecture Rules](#architecture-rules-enforced-by-ci)
8. [QuantConnect Compliance](#quantconnect-compliance-enforced-by-ci)
9. [Coding Conventions](#coding-conventions)
10. [Testing](#testing)
11. [Pre-commit Hooks](#pre-commit-hooks)
12. [CI Pipeline](#ci-pipeline)
13. [Common Mistakes](#common-mistakes)

---

## Local Development Setup

### Prerequisites

- Python 3.11 (required)
- Git
- A code editor (VS Code recommended)

### Step-by-Step Setup

```bash
# 1. Clone the repository
git clone <repo-url>
cd alpha-nextgen-v2-private

# 2. Create virtual environment (REQUIRED)
python3.11 -m venv venv

# 3. Activate virtual environment
# On macOS/Linux:
source venv/bin/activate
# On Windows:
venv\Scripts\activate

# 4. Install dependencies (use lockfile for reproducibility)
pip install -r requirements.lock

# 5. Install development dependencies (IDE type checking)
pip install -r requirements-dev.txt

# 6. Install pre-commit hooks (recommended)
pip install pre-commit
pre-commit install

# 7. Verify setup by running tests
pytest tests/test_smoke_integration.py -v

# 8. Run full test suite
pytest tests/ -v
```

### Verify Your Setup

After setup, run the verification command:

```bash
# Quick verification (recommended)
make verify

# Or manually check each:
python --version                                    # Should show Python 3.11.x
pytest tests/test_architecture_boundaries.py -v    # Should pass
pytest tests/test_target_weight_contract.py -v     # Should pass
pytest tests/test_smoke_integration.py -v          # Should pass
```

**Expected Output:**
- All 3 test files should pass
- No import errors
- No "module not found" errors

If any test fails, check:
1. Virtual environment is activated (`source venv/bin/activate`)
2. Dependencies installed (`pip install -r requirements.lock`)
3. You're in the project root directory

### IDE Configuration (VS Code)

For consistent formatting, add to `.vscode/settings.json`:

```json
{
    "python.defaultInterpreterPath": "${workspaceFolder}/venv/bin/python",
    "python.formatting.provider": "black",
    "python.formatting.blackArgs": ["--config=pyproject.toml"],
    "editor.formatOnSave": true,
    "python.linting.enabled": true,
    "python.linting.mypyEnabled": true
}
```

### QuantConnect API Validation (IDE)

The project uses `quantconnect-stubs` for IDE-based QuantConnect API hints. This enables:
- Autocomplete for QC methods (`SetHoldings`, `History`, `Log`, etc.)
- Type hints for `Slice`, `TradeBar`, and other QC types

**How it works:**

Engine files use `TYPE_CHECKING` guards to import QC types without runtime dependency:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from AlgorithmImports import QCAlgorithm

class MyEngine:
    def __init__(self, algorithm: Optional["QCAlgorithm"] = None):
        self.algorithm = algorithm
```

**Benefits:**
- ✅ IDE sees `QCAlgorithm` type for autocomplete
- ✅ AI code generators see the expected interface ("Ghost Mode")
- ✅ Zero runtime impact (`TYPE_CHECKING` is False at runtime)
- ✅ Code remains "Pure Python" for unit testing

**Setup:** Install dev dependencies: `pip install -r requirements-dev.txt`

**CI Note:** The `engines/` directory is excluded from mypy in CI. This is intentional:
- The `quantconnect-stubs` have incomplete method coverage for strict type checking
- Excluding engines prevents false positive CI failures
- Real type errors in `models/` and `utils/` are still caught by mypy
- Unit tests validate engine logic independently

---

## Branching Strategy

We use a GitFlow-inspired branching model with two protected branches.

```
main     ────●────────────────●────────────────●──── (production releases)
              \              /                /
develop  ──────●────●────●──●────●────●────●─────── (integration branch)
                \       /         \       /
                 feature/A         feature/B
```

### Branch Types

| Branch | Purpose | Protected? |
|--------|---------|:----------:|
| `main` | Production-ready code, releases only | ✅ Yes |
| `develop` | Integration branch, all features merge here | ✅ Yes |
| `feature/*` | New features and changes | No |
| `bugfix/*` | Bug fixes | No |
| `hotfix/*` | Urgent production fixes (rare) | No |

### Rules

1. **NEVER push directly to `main` or `develop`** — All changes via PR
2. **Feature branches are created FROM `develop`**
3. **PRs target `develop`** (not `main`)
4. **`main` is updated only via release merges from `develop`**

### Workflow

```bash
# 1. Start from develop (always pull latest)
git checkout develop
git pull origin develop

# 2. Create feature branch FROM develop
git checkout -b feature/my-feature

# 3. Make changes, commit
git add .
git commit -m "Add my feature"

# 4. Push feature branch
git push origin feature/my-feature

# 5. Create PR targeting develop (NOT main)
#    Include: assignee, reviewer, and phase label
gh pr create --base develop \
  --title "feat: My feature" \
  --body "Description" \
  --assignee @me \
  --reviewer vickdavinci \
  --label "phase-1"

# 6. After CI passes and approval, merge to develop

# 7. Delete feature branch (REQUIRED cleanup)
git checkout develop
git pull origin develop
git branch -d feature/my-feature        # Delete local branch
git push origin --delete feature/my-feature  # Delete remote branch
```

---

## Release Process

Releases merge `develop` into `main` and create a version tag.

### When to Release

- After completing a phase (e.g., Phase 3 complete)
- Before starting risky/experimental work (creates rollback point)
- When `develop` is stable and tested

### Release Workflow

```bash
# 1. Ensure develop is up to date and CI passes
git checkout develop
git pull origin develop
pytest tests/ -v  # All tests should pass

# 2. Create PR from develop → main
gh pr create --base main --head develop \
  --title "vX.Y.Z: Release description" \
  --body "## Summary
- Feature 1
- Feature 2

## Test Results
All 445 tests passing"

# 3. Wait for CI to pass, get approval, then merge
#    IMPORTANT: Use regular merge, NOT squash merge
gh pr merge <PR#> --merge  # NOT --squash

# 4. Create and push version tag
git checkout main
git pull origin main
git tag -a vX.Y.Z -m "Release description"
git push origin vX.Y.Z

# 5. Sync develop with main (REQUIRED)
#    The merge commit on main must be brought back to develop
git checkout develop
git merge main
git push origin develop

# 6. Continue development on develop
git checkout develop
```

### Merge Strategy for Releases

| Merge Type | Use Case | Post-Merge Sync Needed? |
|------------|----------|:-----------------------:|
| **Regular merge** (`--merge`) | Releases (develop → main) | ✅ Yes (step 5) |
| **Squash merge** (`--squash`) | Feature branches → develop | ❌ No |

**Why regular merge for releases:**
- Preserves commit history on `main`
- Merge commit on `main` must be synced back to `develop`

**Why sync is required after release:**
- The merge PR creates a merge commit on `main` that `develop` doesn't have
- Without sync, `develop` falls behind `main` by one commit
- Future release PRs will show divergence

**Why squash merge for features:**
- Keeps `develop` history clean (one commit per feature)
- Feature branch commits are combined into one

### Version Numbering

Follow semantic versioning: `vMAJOR.MINOR.PATCH`

| Version | When to Increment |
|---------|-------------------|
| `MAJOR` | Breaking changes to interfaces |
| `MINOR` | New features, phase completions |
| `PATCH` | Bug fixes, minor improvements |

Examples:
- `v0.3.0` - Phase 3 complete
- `v0.3.1` - Bug fix in Phase 3 code
- `v1.0.0` - First production-ready release

### Rollback

If a release has issues, you can rollback:

```bash
# Checkout a specific tag
git checkout v0.3.0

# Or reset develop to a tag (creates a branch)
git checkout -b hotfix/rollback-v0.3.0 v0.3.0
```

---

## Commit Message Standards

### Format

All commit messages follow this format:

```
<type>: <subject>

[optional body]

[optional footer]
```

### Types (Required)

| Type | Usage |
|------|-------|
| `feat` | New feature or capability |
| `fix` | Bug fix |
| `refactor` | Code change that neither fixes a bug nor adds a feature |
| `docs` | Documentation only changes |
| `test` | Adding or updating tests |
| `chore` | Build process, CI/CD, dependency updates |
| `style` | Formatting, whitespace (no code change) |
| `perf` | Performance improvement |

### Subject Line Rules

1. **Lowercase** - Start with lowercase (e.g., `feat: add regime scoring`)
2. **Imperative mood** - "add" not "adds" or "added"
3. **No period** - Don't end with a period
4. **50 chars max** - Keep subject concise
5. **Be specific** - "fix: handle null portfolio value" not "fix: bug"

### Examples

```bash
# Good commits
feat: add regime engine scoring logic
fix: handle division by zero in ATR calculation
docs: update WORKBOARD with Phase 1 tasks
test: add unit tests for trend engine breakout
refactor: extract stop-loss calculation to utility
chore: update requirements.lock with new dependencies

# Bad commits
Fixed stuff                    # Vague, wrong case
feat: Add new feature.         # Capitalized, has period
update                         # No type, too vague
WIP                            # Not a meaningful commit
```

### Body (Optional but Recommended for Complex Changes)

Use the body to explain **what** and **why** (not how - the code shows how):

```bash
git commit -m "fix: prevent overnight TQQQ holdings

TQQQ is a 3x leveraged ETF that must close by 15:45 ET.
Previous logic allowed positions to slip through when
the time guard was disabled during backtesting.

Closes #42"
```

### Footer (Optional)

- Reference issues: `Closes #123`, `Fixes #456`
- Breaking changes: `BREAKING CHANGE: TargetWeight now requires urgency`

### Multi-line Commits

For complex commits, use heredoc or editor:

```bash
# Using heredoc
git commit -m "$(cat <<'EOF'
feat: implement cold start engine warm entry

- 20% allocation on Day 1-2
- 40% allocation on Day 3-4
- Full allocation from Day 5+
- Resets on kill switch trigger

Implements spec from docs/06-cold-start-engine.md
EOF
)"

# Using editor
git commit  # Opens default editor
```

---

## Development Workflow

### Golden Rule (Non-Negotiable)

> **Before ANY commit, you MUST be on a feature branch.**

```bash
# Check your branch
git branch --show-current

# If it returns "main" or "develop", STOP and create a feature branch:
git checkout -b feature/<initials>/<description>
```

This rule is **enforced by pre-commit hooks**. If you try to commit to `main` or `develop`, the commit will be blocked.

### Before Writing Code

1. **Check WORKBOARD.md** for task assignments
2. **Read the spec document** for the component you're modifying (see `CLAUDE.md` → Component Map)
3. **Understand the data flow** - Strategy Engines → TargetWeight → Portfolio Router → Orders
4. **Check `config.py`** for related parameters

### While Writing Code

```bash
# Format code as you go
black engines/ portfolio/ models/ utils/

# Run relevant tests frequently
pytest tests/test_<your_component>.py -v
```

### After Writing Code

```bash
# 1. Run linting
black engines/ portfolio/ models/ utils/

# 2. Run import sorting
isort engines/ portfolio/ models/ utils/

# 3. Run type checking
mypy engines/ portfolio/ models/ --ignore-missing-imports

# 4. Run all tests
pytest tests/ -v

# 5. Run architecture boundary tests specifically
pytest tests/test_architecture_boundaries.py -v
```

### Submitting Changes

1. **Start from `develop`**: `git checkout develop && git pull origin develop`
2. **Create feature branch**: `git checkout -b feature/my-feature`
3. Make your changes
4. Run all tests locally
5. Push: `git push origin feature/my-feature`
6. **Create PR targeting `develop`**: `gh pr create --base develop`
7. Wait for CI checks to pass
8. Get approval and merge to `develop`

> **Important:** Always create feature branches FROM `develop` and target PRs TO `develop`.

---

## Pull Request Guidelines

### PR Title Format

PR titles should follow commit message format:

```
<type>: <description>
```

Examples:
- `feat: implement regime engine scoring`
- `fix: handle edge case in stop-loss calculation`
- `docs: update Phase 1 documentation`

### PR Metadata (Required)

Every PR must include:

| Field | Required | Value |
|-------|:--------:|-------|
| **Assignee** | ✅ | Yourself (`--assignee @me`) |
| **Reviewer** | ✅ | `vickdavinci` (auto-assigned via CODEOWNERS for `main`) |
| **Label** | ✅ | Phase label (see below) |

**Available Labels:**

| Label | Description |
|-------|-------------|
| `phase-1` | Foundation (config, models, utils) |
| `phase-2` | Core Engines (regime, capital) |
| `phase-3` | Strategy Engines |
| `phase-4` | Coordination (router, risk) |
| `phase-5` | Execution & State |
| `phase-6` | Integration (main.py) |
| `infrastructure` | CI/CD, tooling, configuration |
| `breaking-change` | Introduces breaking changes |

**Example PR creation:**
```bash
gh pr create --base develop \
  --title "feat: implement regime engine" \
  --body "Description here" \
  --assignee @me \
  --reviewer vickdavinci \
  --label "phase-2"
```

### PR Description Requirements

Every PR must include (see `.github/PULL_REQUEST_TEMPLATE.md`):

1. **Summary** - What does this PR do?
2. **Changes** - List of specific changes made
3. **Testing** - How was this tested?
4. **Checklist** - Verify all requirements met

### Before Requesting Review

Ensure your PR passes the **Definition of Done** (see WORKBOARD.md):

- [ ] Code implements the spec document requirements
- [ ] All tests pass (`pytest tests/ -v`)
- [ ] No linting errors (`make lint`)
- [ ] Documentation updated (consult `docs/DOCUMENTATION-MAP.md`)
- [ ] Self-reviewed the diff for obvious issues

### Review Checklist (For Reviewers)

When reviewing a PR, check:

| Category | Questions to Ask |
|----------|------------------|
| **Correctness** | Does the code do what the spec says? |
| **Architecture** | Does it follow hub-and-spoke? Engines emit TargetWeight only? |
| **Safety** | Are there edge cases? Division by zero? Null checks? |
| **QC Compliance** | No print()? No datetime.now()? No time.sleep()? |
| **Parameters** | All values from config.py? No magic numbers? |
| **Tests** | Are there tests? Do they test the right things? |
| **Documentation** | Updated if behavior changed? |

### Approval Requirements

| Target Branch | Required Approvals | CI Must Pass |
|---------------|:------------------:|:------------:|
| `develop` | 0 (self-merge OK) | ✅ Yes |
| `main` | 1 | ✅ Yes |

### Merging Strategy

- **Squash and merge** - For feature branches with many small commits
- **Merge commit** - For branches with well-structured, meaningful commits
- **Rebase and merge** - Avoid unless you understand the implications

### After Merge

1. Delete the feature branch (GitHub offers this automatically)
2. Update `WORKBOARD.md` - Move task from "In Review" to "Done"
3. Pull latest `develop` locally: `git checkout develop && git pull`

---

## Architecture Rules (Enforced by CI)

These rules are automatically enforced by `tests/test_architecture_boundaries.py`.

### Rule 1: Hub-and-Spoke Architecture

**Strategy engines must NOT import other strategy engines.**

All engines communicate via `TargetWeight` through the Portfolio Router.

```python
# ❌ WRONG - Engine importing another engine
# In mean_reversion_engine.py
from engines.trend_engine import TrendEngine  # VIOLATION!

# ✅ CORRECT - Engines are isolated
# Each engine only imports models and utilities
from models.target_weight import TargetWeight
from models.enums import Urgency
```

### Rule 2: Order Authority

**ONLY `portfolio_router.py` may call order methods.**

Engines emit `TargetWeight` objects; they NEVER place orders directly.

```python
# ❌ WRONG - Engine placing orders
class TrendEngine:
    def generate_signals(self):
        if self.is_breakout():
            self.algorithm.MarketOrder("QLD", 100)  # VIOLATION!

# ✅ CORRECT - Engine emits TargetWeight
class TrendEngine:
    def generate_signals(self) -> List[TargetWeight]:
        if self.is_breakout():
            return [TargetWeight(
                symbol="QLD",
                target_weight=0.30,
                source="TREND",
                urgency=Urgency.EOD,
                reason="BB Breakout detected"
            )]
        return []
```

**Forbidden methods in engine files:**
- `MarketOrder()`
- `MarketOnOpenOrder()`
- `LimitOrder()`
- `StopMarketOrder()`
- `Liquidate()`

---

## QuantConnect Compliance (Enforced by CI)

These rules are automatically enforced by `tests/test_architecture_boundaries.py`.

### No `print()` statements

```python
# ❌ WRONG
print(f"Price: {price}")

# ✅ CORRECT
self.algorithm.Log(f"TREND_ENGINE: Price={price:.2f}")
```

**Why:** `print()` output is lost in QuantConnect. Use `self.Log()` for visible output.

### No `time.sleep()`

```python
# ❌ WRONG
import time
time.sleep(5)

# ✅ CORRECT
# Use scheduling for delayed execution
self.Schedule.On(
    self.DateRules.EveryDay(),
    self.TimeRules.At(10, 0),
    self.MyDelayedFunction
)
```

**Why:** `sleep()` breaks the simulation - time doesn't pass, the algorithm just hangs.

### No `datetime.now()` / `datetime.today()`

```python
# ❌ WRONG
from datetime import datetime
current_time = datetime.now()

# ✅ CORRECT
current_time = self.algorithm.Time
```

**Why:** `datetime.now()` returns YOUR computer's time, not the simulated market time. This causes bugs in backtesting.

---

## Coding Conventions

### Parameter Access

**Never hardcode values.** Always reference `config.py`:

```python
# ❌ WRONG
if loss_pct >= 0.03:  # Magic number
    self.trigger_kill_switch()

# ✅ CORRECT
from config import KILL_SWITCH_PCT

if loss_pct >= KILL_SWITCH_PCT:
    self.trigger_kill_switch()
```

### Logging

Use structured, informative log messages:

```python
# ❌ WRONG
self.Log("Entered position")

# ✅ CORRECT
self.Log(f"TREND_ENTRY: {symbol} | Price={price:.2f} | Regime={regime_score} | Size=${size:,.0f}")
```

### Type Hints

Use type hints for all function signatures:

```python
from typing import Optional, List
from models.target_weight import TargetWeight

def generate_signals(self, regime_score: float) -> List[TargetWeight]:
    ...
```

### Docstrings

Use Google-style docstrings:

```python
def calculate_stop(self, entry_price: float, atr: float) -> float:
    """Calculate trailing stop level.

    Args:
        entry_price: Original entry price for the position.
        atr: Current 14-period Average True Range.

    Returns:
        Stop price level.

    Raises:
        ValueError: If entry_price or atr is non-positive.
    """
```

---

## Testing

### Test Categories

| Category | Location | Purpose | Phase |
|----------|----------|---------|:-----:|
| **Architecture Tests** | `tests/test_architecture_boundaries.py` | Enforce architecture rules | ✅ Now |
| **Contract Tests** | `tests/test_target_weight_contract.py` | Ensure TargetWeight schema stability | ✅ Now |
| **Smoke Tests** | `tests/test_smoke_integration.py` | Verify components wire together | ✅ Now |
| **Unit Tests** | `tests/test_*.py` | Test individual functions/classes | ✅ Now |
| **Integration Tests** | `tests/integration/` | Multi-component interactions | ✅ V2.1 |
| **Scenario Tests** | `tests/scenarios/` | Test complete workflows | Phase 5+ |

### Integration Tests (V2.1)

Integration tests verify that multiple components work together without conflicts:

| Test File | Purpose |
|-----------|---------|
| `test_options_integration.py` | Options + OCO lifecycle, Greeks monitoring |
| `test_multi_engine_conflict.py` | Engine conflict detection, allocation limits |
| `test_state_recovery.py` | Restart recovery, state persistence |

**Why Integration Tests Matter:**
- Options Engine is new and interacts with OCO Manager, Risk Engine, and Router
- Multiple engines compete for allocation - conflicts must be detected
- System must survive restarts with state intact

See `docs/V2_TEST_PLAN.md` for comprehensive test strategy.

### Test Status

- **Critical tests** (architecture, contract, smoke) must always pass
- **Integration tests** must pass - they verify component interactions
- **Unit tests** may have `@pytest.mark.skip` for unimplemented components
- **Scenario tests** are skipped until Phase 5+ integration

### Running Tests

```bash
# Run all tests (recommended before commit)
pytest tests/ -v

# Run critical tests only (quick check)
make test-critical

# Run specific test file
pytest tests/test_trend_engine.py -v

# Run integration tests only (V2.1)
pytest tests/integration/ -v

# Run options-related tests
pytest tests/ -v -m options
pytest tests/test_options_engine.py tests/test_oco_manager.py tests/integration/test_options_integration.py -v

# Run with coverage
pytest tests/ --cov=engines --cov=portfolio --cov=models --cov=execution --cov-report=term-missing

# Run only non-skipped tests
pytest tests/ -v -m "not skip"

# Run by marker
pytest tests/ -v -m architecture
pytest tests/ -v -m contract
pytest tests/ -v -m smoke
pytest tests/ -v -m integration
```

### Understanding Skipped Tests

Many tests use `@pytest.mark.skip` - **this is intentional**, not broken:

```python
@pytest.mark.skip(reason="Phase 2 - RegimeEngine not implemented yet")
def test_regime_score_calculation():
    """This test is waiting for Phase 2 implementation."""
    pass
```

**What this means:**
- Tests are scaffolded for future components
- Skip message indicates which phase implements it
- Remove `@pytest.mark.skip` when you implement the component
- CI allows skipped tests but fails on actual test failures

**When to add skip markers:**
- Scaffolding tests for unimplemented features
- Tests that depend on components not yet built

**When to remove skip markers:**
- Component is fully implemented
- All dependencies are available

### Test Fixtures (Shared Utilities)

The `tests/conftest.py` provides reusable fixtures - **use these instead of creating your own mocks**:

```python
# Available fixtures:

def test_my_engine(mock_algorithm):
    """mock_algorithm - Basic QCAlgorithm mock with Portfolio, Time, Log, etc."""
    engine = MyEngine(mock_algorithm)
    assert engine.algorithm.Portfolio.TotalPortfolioValue == 50000.0

def test_with_positions(mock_portfolio_with_positions):
    """mock_portfolio_with_positions - Algorithm with pre-configured QLD, TQQQ, SH positions."""
    algo = mock_portfolio_with_positions
    assert algo.Portfolio["QLD"].Invested == True

def test_with_prices(deterministic_prices):
    """deterministic_prices - Reproducible price data for SPY, QLD, TQQQ, SSO, UGL, UCO, SH."""
    prices = deterministic_prices
    assert len(prices["SPY"]) == 100  # 100 data points

def test_regime_logic(sample_regime_scores):
    """sample_regime_scores - Fixed scores for RISK_ON, NEUTRAL, CAUTIOUS, DEFENSIVE, RISK_OFF."""
    scores = sample_regime_scores
    assert scores["RISK_ON"] == 75
```

**Benefits:**
- Deterministic: Same test run twice = identical results (seed=42)
- Shared: Don't reinvent mocks in each test file
- Documented: Each fixture has docstrings explaining usage

### Test Seeds

All tests use deterministic random seeds (configured in `tests/conftest.py`). This means:
- Same test run twice = identical results
- Failures are reproducible across machines

---

## Pre-commit Hooks

Pre-commit hooks run automatically before each commit to catch issues early.

### Setup (One-time)

```bash
pip install pre-commit
pre-commit install
```

### What Hooks Check

| Hook | What It Does |
|------|--------------|
| `trailing-whitespace` | Removes trailing whitespace |
| `end-of-file-fixer` | Ensures files end with newline |
| `check-yaml` | Validates YAML syntax |
| `check-json` | Validates JSON syntax |
| `check-added-large-files` | Blocks files > 500KB |
| `check-merge-conflict` | Detects merge conflict markers |
| `detect-private-key` | Prevents accidental credential commits |
| `black` | Formats Python code |
| `isort` | Sorts imports |
| `protect-branches` | **Blocks direct commits to `main`/`develop`** |
| `no-print-statements` | Blocks `print()` in source files |
| `no-datetime-now` | Blocks `datetime.now()` usage |
| `no-time-sleep` | Blocks `time.sleep()` usage |

### Manual Run

```bash
# Run on all files
pre-commit run --all-files

# Run specific hook
pre-commit run black --all-files
```

### Skip Hooks (Emergency Only)

```bash
# Only use in emergencies - explain in commit message
git commit --no-verify -m "Emergency fix: [explanation]"
```

### Troubleshooting Hook Failures

**Hook: "Block commits to protected branches"**
```
ERROR: Direct commits to develop blocked
```
**Fix:** Create a feature branch first:
```bash
git checkout -b feature/<initials>/<description>
```

---

**Hook: "Check for print() statements"**
```
Found print() in engines/my_engine.py:45
```
**Fix:** Replace `print()` with QuantConnect logging:
```python
# Wrong
print(f"Price: {price}")

# Correct
self.algorithm.Log(f"MY_ENGINE: Price={price:.2f}")
```

---

**Hook: "Check for datetime.now()"**
```
Found datetime.now() in engines/my_engine.py:30
```
**Fix:** Use algorithm time instead:
```python
# Wrong
current_time = datetime.now()

# Correct
current_time = self.algorithm.Time
```

---

**Hook: "Check for time.sleep()"**
```
Found time.sleep() in engines/my_engine.py:50
```
**Fix:** Use QuantConnect scheduling:
```python
# Wrong
time.sleep(5)

# Correct - Use scheduled events
self.Schedule.On(
    self.DateRules.EveryDay(),
    self.TimeRules.AfterMarketOpen("SPY", 30),
    self.MyDelayedFunction
)
```

---

**Hook: Black formatting failed**
```
would reformat engines/my_engine.py
```
**Fix:** Auto-format your code:
```bash
make format
# or
black engines/ portfolio/ models/ utils/
```

---

**General: Hooks not running**
```bash
# Reinstall hooks
pre-commit install

# Run manually on all files
pre-commit run --all-files
```

---

## CI Pipeline

The CI pipeline runs automatically on every PR to `main` or `develop`.

### Status Check: `test`

This is the check name required for branch protection. It includes:

| Phase | Check | Behavior |
|:-----:|-------|----------|
| 1 | Config Validation | Verify `config.py` matches specs |
| 2 | Linting (Black) | Only runs on files with content |
| 2 | Type Checking (mypy) | Only runs on files with content |
| 3 | Architecture Tests | **MUST PASS** - enforces hub-spoke |
| 3 | Contract Tests | **MUST PASS** - TargetWeight schema |
| 3 | Smoke Tests | **MUST PASS** - component wiring |
| 4 | Unit Tests | Skipped tests OK, failures fail build |
| 4 | Scenario Tests | Skipped tests OK, failures fail build |
| 5 | Coverage | Informational only |
| 6 | Doc Parity | Warns if code changed without spec update |

### Branch Protection

Both `main` and `develop` are protected branches:

| Branch | CI Required | Approvals | Direct Push |
|--------|:-----------:|:---------:|:-----------:|
| `main` | ✅ Yes | 1 | ❌ Blocked |
| `develop` | ✅ Yes | 0 | ❌ Blocked |

- All changes must go through Pull Requests
- CI must pass before merge is allowed
- `main` requires approval; `develop` allows self-merge after CI passes

See `docs/GITHUB-BRANCH-PROTECTION.md` for setup instructions.

---

## Project Configuration

All tool configurations are centralized in `pyproject.toml`:

- **pytest** - Test paths, markers, output settings
- **black** - Line length (100), Python version
- **isort** - Import sorting profile
- **mypy** - Type checking settings
- **coverage** - Coverage reporting

---

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Engine calls `MarketOrder()` | Emit `TargetWeight` instead |
| Using `print()` | Use `self.Log()` |
| Using `datetime.now()` | Use `self.algorithm.Time` |
| Hardcoded numbers | Reference `config.py` |
| Engine imports another engine | Keep engines isolated |
| Tests fail randomly | Check you're not using unseeded random |
| Feature branch from `main` | Always branch from `develop` |
| PR targets `main` | PRs should target `develop` |
| Direct push to `develop` | All changes via PR |
| Skipping pre-commit | Run `pre-commit install` |

---

## Questions?

- Read `CLAUDE.md` for detailed architecture documentation
- Read `docs/` folder for component specifications
- Check `ERRORS.md` for common error solutions
- Check `WORKBOARD.md` for task assignments
