# Quick Start Guide

**Goal:** Clone → Setup → Run tests in under 5 minutes.

---

## Prerequisites

- Python 3.11 (required)
- Git
- GitHub CLI (`gh`) - optional but recommended

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
git clone https://github.com/vickdavinci/alpha-nextgen.git
cd alpha-nextgen

# 2. Create virtual environment
python3.11 -m venv venv

# 3. Activate virtual environment
source venv/bin/activate  # macOS/Linux
# venv\Scripts\activate   # Windows

# 4. Install dependencies
pip install -r requirements.lock
pip install -r requirements-dev.txt  # Optional: IDE autocomplete for QC API

# 5. Install pre-commit hooks
pip install pre-commit
pre-commit install

# 6. Verify setup
make verify
```

---

## Verify Success

After running `make verify`, you should see:

```
1. Python version:
Python 3.11.x

2. Running critical tests...
tests/test_architecture_boundaries.py::test_... PASSED
tests/test_target_weight_contract.py::test_... PASSED
tests/test_smoke_integration.py::test_... PASSED

==============================================
Setup verified! You're ready to develop.
==============================================
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
| `make verify` | Verify setup is working |

---

## First Task Workflow

```bash
# 1. Create your feature branch
make branch name=feature/<initials>/<description>

# 2. Make changes to code

# 3. Run tests
make test

# 4. Commit (pre-commit hooks run automatically)
git add <files>
git commit -m "feat: your description"

# 5. Push and create PR
git push origin <branch-name>
gh pr create --base develop
```

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

### Tests fail with import errors

```bash
# Verify you're in project root
pwd
# Should end with: alpha-nextgen

# Verify venv is activated
which python
# Should show: .../alpha-nextgen/venv/bin/python
```

---

## What's Next?

1. **Read the architecture:** [CLAUDE.md](CLAUDE.md) - Component map and critical rules
2. **Check task board:** [WORKBOARD.md](WORKBOARD.md) - Pick a task from "Ready to Start"
3. **Understand the spec:** `docs/` folder has detailed specifications

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
