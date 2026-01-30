# Documentation Map

> **Purpose:** This file maps code files to their documentation. Claude MUST consult this after ANY code change to update the relevant documentation.

---

## Documentation Structure (Updated 2026-01-29)

```
docs/
├── README.md                 # Navigation hub
├── system/                   # Core system documentation (00-19)
├── guides/                   # How-to guides
├── audits/                   # Quality audits & test plans
├── specs/                    # Design specifications
└── internal/                 # Internal reference (this file)
```

---

## How to Use This Map

1. After making code changes, find the changed file/directory in the table below
2. Update ALL documentation listed in the "Documentation to Update" column
3. Include documentation updates in the same commit/PR as the code change

---

## Infrastructure & Configuration

| Changed File/Directory | Documentation to Update |
|------------------------|-------------------------|
| `.github/workflows/*.yml` | `CONTRIBUTING.md` → CI Pipeline section |
| `.pre-commit-config.yaml` | `CONTRIBUTING.md` → Pre-commit Hooks section |
| `pyproject.toml` | `CONTRIBUTING.md` → Project Configuration section |
| `Makefile` | `CONTRIBUTING.md` → Development Workflow section |
| `requirements.txt` / `requirements.lock` | `CONTRIBUTING.md` → Local Development Setup |
| `.claude/config.json` | `CLAUDE.md` → Repository Structure |
| `.vscode/settings.json` | `CONTRIBUTING.md` → IDE Configuration section |
| `.editorconfig` | `PROJECT-STRUCTURE.md` |
| `tests/conftest.py` | `CONTRIBUTING.md` → Test Fixtures section |

---

## Root-Level Files

| Changed File/Directory | Documentation to Update |
|------------------------|-------------------------|
| New file in root | `CLAUDE.md` → Repository Structure, `PROJECT-STRUCTURE.md` |
| `config.py` | `docs/system/16-appendix-parameters.md`, `CLAUDE.md` → Key Thresholds |
| `main.py` | `docs/system/02-system-architecture.md`, `docs/guides/main-py-implementation.md` |

---

## Source Code Directories

### Core Engines (engines/core/)

| Changed File/Directory | Documentation to Update |
|------------------------|-------------------------|
| `engines/core/*.py` (new file) | `CLAUDE.md` → Component Map, `PROJECT-STRUCTURE.md` |
| `engines/core/regime_engine.py` | `docs/system/04-regime-engine.md` |
| `engines/core/capital_engine.py` | `docs/system/05-capital-engine.md` |
| `engines/core/cold_start_engine.py` | `docs/system/06-cold-start-engine.md` |
| `engines/core/trend_engine.py` | `docs/system/07-trend-engine.md` |
| `engines/core/risk_engine.py` | `docs/system/12-risk-engine.md` |

### Satellite Engines (engines/satellite/)

| Changed File/Directory | Documentation to Update |
|------------------------|-------------------------|
| `engines/satellite/*.py` (new file) | `CLAUDE.md` → Component Map, `PROJECT-STRUCTURE.md` |
| `engines/satellite/mean_reversion_engine.py` | `docs/system/08-mean-reversion-engine.md` |
| `engines/satellite/hedge_engine.py` | `docs/system/09-hedge-engine.md` |
| `engines/satellite/yield_sleeve.py` | `docs/system/10-yield-sleeve.md` |
| `engines/satellite/options_engine.py` | `docs/system/18-options-engine.md`, `docs/specs/v2-1-options-engine-design.txt` |

### Portfolio & Execution

| Changed File/Directory | Documentation to Update |
|------------------------|-------------------------|
| `portfolio/portfolio_router.py` | `docs/system/11-portfolio-router.md` |
| `execution/execution_engine.py` | `docs/system/13-execution-engine.md` |
| `execution/oco_manager.py` | `docs/system/19-oco-manager.md` |
| `persistence/*.py` | `docs/system/15-state-persistence.md` |
| `scheduling/daily_scheduler.py` | `docs/system/14-daily-operations.md` |

### Models

| Changed File/Directory | Documentation to Update |
|------------------------|-------------------------|
| `models/*.py` (new file) | `CLAUDE.md` → Component Map, `PROJECT-STRUCTURE.md` |
| `models/target_weight.py` | `CLAUDE.md` → Critical Rules |
| `models/enums.py` | `docs/system/17-appendix-glossary.md` |

---

## Test Files

| Changed File/Directory | Documentation to Update |
|------------------------|-------------------------|
| `tests/*.py` (new file) | `PROJECT-STRUCTURE.md` |
| `tests/scenarios/*.py` (new file) | `PROJECT-STRUCTURE.md`, `CONTRIBUTING.md` → Testing section |
| Test architecture changes | `CONTRIBUTING.md` → Testing section |

---

## Documentation Files

| Changed File/Directory | Documentation to Update |
|------------------------|-------------------------|
| New doc in `docs/system/` | `docs/system/00-table-of-contents.md`, `CLAUDE.md` → Component Map |
| New guide in `docs/guides/` | `docs/README.md` → Quick Navigation |
| New audit in `docs/audits/` | `docs/README.md` → Current Status |
| New spec in `docs/specs/` | `docs/README.md` → Folder Structure |
| Workflow changes | `CONTRIBUTING.md`, `developer-guide-claude.md` |
| New errors discovered | `ERRORS.md` |
| New QC patterns | `QC_RULES.md` |

---

## Cross-Documentation Consistency (Bidirectional)

When modifying documentation files directly, check these related documents for duplicate content that needs synchronization:

| If You Modify... | Check These for Consistency |
|------------------|----------------------------|
| `CONTRIBUTING.md` (commit/PR/workflow) | `developer-guide-claude.md` → commit message section |
| `CONTRIBUTING.md` (testing) | `developer-guide-claude.md` → testing sections |
| `CONTRIBUTING.md` (branching) | `developer-guide-claude.md` → git workflow section |
| `developer-guide-claude.md` (any workflow) | `CONTRIBUTING.md` → corresponding section |
| `CLAUDE.md` (architecture) | `docs/system/02-system-architecture.md` |
| `CLAUDE.md` (thresholds) | `docs/system/16-appendix-parameters.md`, `config.py` |
| `WORKBOARD.md` (process/DoD) | `CONTRIBUTING.md` → PR Guidelines |
| `PROJECT-STRUCTURE.md` | `CLAUDE.md` → Repository Structure |

**Rule:** If content exists in multiple places, designate ONE as authoritative and have others reference it.

---

## Special Rules

### When Adding New Components

If you add a **new engine, model, or major component**:
1. Update `CLAUDE.md` → Component Map table
2. Update `PROJECT-STRUCTURE.md` → Flat File Listing
3. Create corresponding spec doc in `docs/system/` if needed
4. Update `docs/system/00-table-of-contents.md`
5. Update `docs/README.md` if it's a major component

### When Changing Architecture

If you modify **how components interact**:
1. Update `docs/system/02-system-architecture.md`
2. Update `CLAUDE.md` → Data Flow Architecture diagram
3. Update `CLAUDE.md` → Critical Rules if authority changes

### When Changing Thresholds/Parameters

If you modify **config.py values**:
1. Update `docs/system/16-appendix-parameters.md`
2. Update `CLAUDE.md` → Key Thresholds table
3. Update relevant engine spec doc

---

## Maintenance

This map should be updated when:
- New source directories are added
- New documentation files are created
- New patterns of code-to-doc relationships emerge

**Last Updated:** 29 January 2026 (V2.1.1 - Documentation Reorganization)
