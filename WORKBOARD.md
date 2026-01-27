# WORKBOARD.md - Alpha NextGen V2 Task & Ownership Board

> **Single source of truth for:** Who owns what, what's in progress, what's next.
>
> **Rules:**
> - Pull before editing (avoid conflicts)
> - Move tasks by cut/paste
> - Update when starting/finishing work

---

## Team

| Member | Initials | Focus Area |
|--------|----------|------------|
| Vigneshwaran | VA | Strategy Engines, Models |
| [Collaborator] | -- | Core Engines, Execution |

---

## V2 Fork Status

> **V2 Forked from V1 v1.0.0** (2026-01-26)
>
> **Completed:**
> - PHASE 0: V1 Structural Audit ✅
> - PHASE 1: Repository Hard Fork ✅
> - PHASE 2: Core-Satellite Refactoring ✅
> - PHASE 3: Master Plan (`V2_IMPLEMENTATION_ROADMAP.md`) ✅
>
> **Architecture:**
> - `engines/core/` - Foundational engines (70%)
> - `engines/satellite/` - Conditional engines (0-30%)
> - `docs/v2-specs/` - V2.1 specifications
>
> **Tests:** 694 passed, 27 skipped

---

## Current Sprint: V2 Phase 1 - Foundation

> **Goal:** Enhanced Trend Engine + 5-Level Circuit Breakers
>
> See `V2_IMPLEMENTATION_ROADMAP.md` for full roadmap.

### Ready to Start

| Ticket | Component | Size | Spec |
|--------|-----------|:----:|------|
| MRE-1 | VIX Regime Classification | M | V2-1-Critical-Fixes-Guide.md |
| MRE-2 | Regime-Adjusted Parameters | M | V2-1-Critical-Fixes-Guide.md |
| MRE-4 | Mean Reversion V2 Tests | M | V2_1_COMPLETE_ARCHITECTURE.txt |

### In Progress

| Component | Owner | Branch | Started | Spec |
|-----------|-------|--------|---------|------|
| MRE-3 | VA | feature/MRE-3-vix-data-feed | 2026-01-26 | V2-1-Critical-Fixes-Guide.md |

### Done (V2 Phase 1)

| Ticket | Component | Owner | Commit | Merged |
|--------|-----------|-------|--------|--------|
| TRE-1 | MA200 + ADX Signal | VA | develop | 2026-01-26 |
| TRE-2 | Trailing Stop Enhancement | VA | develop | 2026-01-26 |
| RSK-1 | 5-Level Circuit Breaker | VA | develop | 2026-01-26 |

### In Review

| Component | Owner | PR | Reviewer |
|-----------|-------|---:|----------|
| _None_ | | | |

### Ready to Start

| Component | Assigned | Size | Spec |
|-----------|----------|:----:|------|
| _Phase 6 Complete - See Next Steps above_ | | | |

### Done (Phase 6)

| Component | Owner | Commit | Merged |
|-----------|-------|--------|--------|
| main.py | VA | 7c0baf0 | 2026-01-25 |
| docs/MAIN_PY_IMPLEMENTATION.md | VA | 7c0baf0 | 2026-01-25 |

### Done (Phase 5)

| Component | Owner | PR | Merged |
|-----------|-------|---:|--------|
| execution/execution_engine.py | VA | #40 | 2026-01-26 |
| persistence/state_manager.py | VA | #40 | 2026-01-26 |
| scheduling/daily_scheduler.py | VA | #40 | 2026-01-26 |

### Done (Phase 4)

| Component | Owner | PR | Merged |
|-----------|-------|---:|--------|
| portfolio/exposure_groups.py | VA | #36 | 2026-01-25 |
| portfolio/portfolio_router.py | VA | #37 | 2026-01-25 |
| engines/risk_engine.py | VA | #38 | 2026-01-25 |

### Done (Phase 3)

| Component | Owner | PR | Merged |
|-----------|-------|---:|--------|
| engines/cold_start_engine.py | VA | #26 | 2026-01-25 |
| engines/trend_engine.py | VA | #27 | 2026-01-25 |
| engines/mean_reversion_engine.py | VA | #28 | 2026-01-25 |
| TYPE_CHECKING guards (all engines) | VA | #29 | 2026-01-25 |
| docs: requirements-dev.txt | VA | #30 | 2026-01-25 |
| engines/hedge_engine.py | VA | #32 | 2026-01-25 |
| engines/yield_sleeve.py | VA | #33 | 2026-01-25 |

### Done (Phase 1)

| Component | Owner | PR | Merged |
|-----------|-------|---:|--------|
| config.py | VA | #18 | 2026-01-25 |
| models/enums.py | VA | — | 2026-01-25 |
| models/target_weight.py | VA | — | 2026-01-25 |
| utils/calculations.py | VA | #22 | 2026-01-25 |

---

## Phase 2 - Core Engines (Complete ✓)

### Done (Phase 2)

| Component | Owner | PR | Merged |
|-----------|-------|---:|--------|
| engines/regime_engine.py | VA | #24 | 2026-01-25 |
| engines/capital_engine.py | VA | #25 | 2026-01-25 |

### Deferred

| Component | Assigned | Size | Spec | Notes |
|-----------|----------|:----:|------|-------|
| CI: Enforce coverage threshold (70%) | -- | S | — | Deferred to Phase 4 |

---

## Phase 3 - Strategy Engines (Complete ✓)

> See "Done (Phase 3)" in Current Sprint section.

---

## Phase 4 - Coordination (Complete ✓)

> See "Done (Phase 4)" in Current Sprint section.

---

## Phase 5 - Execution & State (Complete ✓)

> See "Done (Phase 5)" in Current Sprint section.

---

## Phase 6 - Integration (Complete ✓)

> See "Done (Phase 6)" in Current Sprint section.
>
> **Implementation Summary:**
> - `main.py` - QCAlgorithm entry point (1,332 lines)
> - Hub-and-Spoke architecture with PortfolioRouter as central hub
> - All engines, infrastructure, and scheduled events wired together
> - Full documentation in `docs/MAIN_PY_IMPLEMENTATION.md`

---

## Ideas Backlog

> **Purpose:** Capture ideas, enhancements, and "nice-to-haves" that are out of scope for current phases but worth remembering.
>
> **Rules:**
> - Add ideas anytime - don't let them get lost
> - Categorize by type (Enhancement, Research, Tooling, etc.)
> - Move to a Phase when prioritized for implementation
> - Delete if no longer relevant

### Future Enhancements

| Idea | Category | Notes | Added |
|------|----------|-------|-------|
| Intraday options trading (QQQ calls/puts) | Options | Core logic: QQQ averages 1% daily move. Enter at top/bottom. Requires Phase 5+ complete. | 2026-01-25 |
| Report generation and monitoring | Operations | Daily/weekly performance reports, alerts, dashboards. Essential for live trading oversight. | 2026-01-25 |
| Web UI for system management | Operations | Dashboard to view positions, regime state, trigger manual overrides. Consider after v1.0 stable. | 2026-01-25 |

### Research / Exploration

| Idea | Category | Notes | Added |
|------|----------|-------|-------|
| Evaluate adding short positions | Strategy | Currently using PSQ (inverse ETF) as hedge. Research if direct shorting improves returns in sustained downtrends. | 2026-01-25 |
| Comprehensive options strategy matrix | Options | Multiple strategies (spreads, straddles, etc.) based on conditions. Needs separate architecture. Treat as separate project sharing regime engine. | 2026-01-25 |
| Crypto trading with same logic | New Market | Regime/MR/trend logic portable. Challenges: 24/7 market, different infra, higher volatility. Fork-and-modify approach. | 2026-01-25 |

### Technical Debt / Improvements

| Idea | Category | Notes | Added |
|------|----------|-------|-------|
| _None yet_ | | | |

---

## Archive

<details>
<summary>Phase 0 - Pre-Development & Foundation (Complete)</summary>

### Documentation & Setup (2026-01-24)

| Task | Owner | Completed |
|------|-------|-----------|
| Session management setup | VA | 2026-01-24 |
| developer-guide-claude.md rewrite | VA | 2026-01-24 |
| Git workflow established | VA | 2026-01-24 |
| WORKBOARD.md created | VA | 2026-01-24 |

### CI/CD & Infrastructure (2026-01-25)

| Task | Owner | Completed |
|------|-------|-----------|
| GitHub Actions CI workflow (`.github/workflows/test.yml`) | VA | 2026-01-25 |
| Architecture boundary tests (`tests/test_architecture_boundaries.py`) | VA | 2026-01-25 |
| QC compliance tests (print, sleep, datetime.now) | VA | 2026-01-25 |
| Branch protection for `main` and `develop` | VA | 2026-01-25 |
| CONTRIBUTING.md created | VA | 2026-01-25 |
| PR template (`.github/PULL_REQUEST_TEMPLATE.md`) | VA | 2026-01-25 |
| Branch protection docs (`docs/GITHUB-BRANCH-PROTECTION.md`) | VA | 2026-01-25 |
| Lean CLI workspace integration | VA | 2026-01-25 |
| QC cloud backtest verification | VA | 2026-01-25 |
| CI violation detection verified | VA | 2026-01-25 |

### Infrastructure Hardening (2026-01-24)

| Task | Owner | Completed |
|------|-------|-----------|
| Fixed CI pipeline to fail correctly (no silent skips) | VA | 2026-01-24 |
| Created `tests/scenarios/__init__.py` | VA | 2026-01-24 |
| Added explicit `@pytest.mark.skip` to all placeholder tests | VA | 2026-01-24 |
| Created `pyproject.toml` (unified tool configuration) | VA | 2026-01-24 |
| Created `.pre-commit-config.yaml` (pre-commit hooks) | VA | 2026-01-24 |
| Created `Makefile` (workflow automation: make setup, make test, make branch) | VA | 2026-01-24 |
| Updated CONTRIBUTING.md with local dev setup | VA | 2026-01-24 |
| Added "Golden Rule" section to CONTRIBUTING.md (branch protection) | VA | 2026-01-24 |
| Added pre-commit hook to block commits to main/develop | VA | 2026-01-24 |
| Added test scaffolds for all Phase 2-5 components | VA | 2026-01-24 |
| Fixed Black formatting in models/enums.py and models/target_weight.py | VA | 2026-01-24 |

### Documentation Automation (2026-01-24)

| Task | Owner | Completed |
|------|-------|-----------|
| Created `docs/DOCUMENTATION-MAP.md` (code-to-doc mapping) | VA | 2026-01-24 |
| Added "Documentation Update Requirements" section to CLAUDE.md | VA | 2026-01-24 |
| Updated CLAUDE.md repository structure with new files | VA | 2026-01-24 |
| Updated PROJECT-STRUCTURE.md with new files and counts | VA | 2026-01-24 |
| Updated docs/00-table-of-contents.md with DOCUMENTATION-MAP.md | VA | 2026-01-24 |

### Developer Experience Improvements (2026-01-24)

| Task | Owner | Completed |
|------|-------|-----------|
| Created `.vscode/settings.json` (IDE configuration) | VA | 2026-01-24 |
| Created `.editorconfig` (cross-editor consistency) | VA | 2026-01-24 |
| Added `make verify` command (setup verification) | VA | 2026-01-24 |
| Added `make validate-config` command | VA | 2026-01-24 |
| Added `make phase1-check` command | VA | 2026-01-24 |
| Added test fixture documentation to CONTRIBUTING.md | VA | 2026-01-24 |
| Added skip marker explanation to CONTRIBUTING.md | VA | 2026-01-24 |
| Added pre-commit hook troubleshooting to CONTRIBUTING.md | VA | 2026-01-24 |

### Process Standards (2026-01-24)

| Task | Owner | Completed |
|------|-------|-----------|
| Added commit message standards to CONTRIBUTING.md | VA | 2026-01-24 |
| Added PR review guidelines to CONTRIBUTING.md | VA | 2026-01-24 |
| Added Definition of Done to WORKBOARD.md | VA | 2026-01-24 |
| Principal Architect review completed | VA | 2026-01-24 |

**Documentation Automation Process:**
- Claude consults `docs/DOCUMENTATION-MAP.md` after any code change
- Maps code files → documentation that needs updating
- Documentation updates included in same commit/PR as code
- No developer action required - Claude handles automatically

**CI Capabilities:**
- ✅ Catches engines placing orders (architecture violation)
- ✅ Catches `print()` statements (QC compliance)
- ✅ Catches `time.sleep()` calls (QC compliance)
- ✅ Catches `datetime.now()` usage (QC compliance)
- ✅ Blocks PR merge if tests fail
- ✅ Skipped tests allowed, but failures fail build
- ✅ Linting only runs on files with actual content

**Pre-commit Hooks:**
- ✅ Black (code formatting)
- ✅ isort (import sorting)
- ✅ No print() in source files
- ✅ No datetime.now() usage
- ✅ No time.sleep() usage
- ✅ Blocks commits to main/develop branches

**Workflow Automation (Makefile):**
- `make setup` - Create venv, install deps, install pre-commit
- `make test` - Run all tests
- `make lint` - Run black and isort
- `make branch name=feature/va/my-feature` - Create feature branch from develop

**Lean CLI Integration:**
- Workspace: `../lean-workspace/AlphaNextGen/`
- Verified: `lean cloud push` + `lean cloud backtest` working
- Test backtest: $50,000 → $50,147 (+0.29%)

</details>

---

## Definition of Done

A task is **not complete** until ALL of the following are true:

### Code Quality
- [ ] Code implements requirements from the spec document
- [ ] All unit tests pass (`pytest tests/test_<component>.py -v`)
- [ ] All architecture tests pass (`pytest tests/test_architecture_boundaries.py -v`)
- [ ] No linting errors (`make lint` or `black --check`)
- [ ] Type hints added for all function signatures
- [ ] Docstrings added for public functions

### Safety & Compliance
- [ ] No `print()` statements (use `self.algorithm.Log()`)
- [ ] No `datetime.now()` (use `self.algorithm.Time`)
- [ ] No `time.sleep()` (use scheduling)
- [ ] No hardcoded values (use `config.py`)
- [ ] Engines do NOT place orders (emit TargetWeight only)

### Documentation
- [ ] `docs/DOCUMENTATION-MAP.md` consulted
- [ ] All affected documentation updated
- [ ] WORKBOARD.md task moved to "Done" section

### Review
- [ ] PR created targeting `develop`
- [ ] CI passes (all green checks)
- [ ] Self-reviewed the diff
- [ ] Approval received (if targeting `main`)

---

## Interface Change Protocol

If you need to change a shared interface (TargetWeight, RegimeState, etc.):

1. Notify collaborator: "I need to change [interface]. Pause related work."
2. Make the change in a normal feature branch
3. Update all affected components in SAME branch
4. PR, review, merge
5. Notify: "[Interface] change merged. Resume work."

Expected frequency: 1-2 times total project lifetime.

---

## Quick Reference

**Sizes:** S = <100 lines | M = 100-300 lines | L = 300+ lines

**Workflow:**
```
1. Pick from "Ready to Start" -> Move to "In Progress"
2. Create branch: feature/<initials>/<component>
3. Code -> PR -> Move to "In Review"
4. After merge -> Move to "Done"
5. Delete feature branch (local + remote)
```

**Branch format:** `feature/va/config-py` or `feature/vd/regime-engine`

**New Developer Setup:**
```bash
git clone <repo> && cd alpha-nextgen
python3.11 -m venv venv && source venv/bin/activate
pip install -r requirements.lock
pre-commit install
pytest tests/test_smoke_integration.py -v
```

---

*Last Updated: 25 January 2026 (Phase 6 Complete! main.py implemented - Ready for backtesting)*
