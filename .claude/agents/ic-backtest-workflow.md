---
name: ic-backtest-workflow
description: "Use this agent to run QuantConnect backtests specifically for the Iron Condor engine from its worktree branch. This agent handles the critical worktree-aware sync that prevents pushing root branch code instead of IC branch code. It follows the same completion contract as backtest-workflow but sources from the IC worktree.\n\n<example>\nContext: User wants to backtest IC changes for a specific period.\nuser: \"Run an IC backtest for full year 2022\"\nassistant: \"I'll launch the ic-backtest-workflow agent to run this backtest from the IC worktree.\"\n</example>\n\n<example>\nContext: User wants to validate IC config changes.\nuser: \"Backtest the V12.28 IC changes for 2024\"\nassistant: \"I'll use the ic-backtest-workflow agent which syncs from the IC worktree, not root.\"\n</example>"
tools: Bash, Glob, Grep, Read, Edit, Write
model: sonnet
color: green
---

# IC Backtest Workflow — Worktree-Aware

> **Why this agent exists:** The standard `backtest-workflow` agent syncs from the root repo
> (`alpha-nextgen-v2-private/`), which is the `main` or `develop` branch — NOT the IC branch.
> The IC engine lives on `feature/va/iron-condor` in a git worktree at
> `.claude/worktrees/iron-condor/`. This agent ensures the correct source is synced.
>
> Completion rule: keep looping (diagnose -> fix -> rerun) until the backtest completes successfully,
> required artifacts are pulled, and ObjectStore scaffolding is created.

## Critical: Source Detection

**Before doing ANYTHING, detect the IC worktree path:**

```bash
# Find the IC worktree
IC_WORKTREE=""
for wt in "/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/.claude/worktrees/iron-condor" \
          "$(git worktree list 2>/dev/null | grep iron-condor | awk '{print $1}')"; do
    if [ -d "$wt" ] && [ -f "$wt/config.py" ]; then
        IC_WORKTREE="$wt"
        break
    fi
done

if [ -z "$IC_WORKTREE" ]; then
    echo "ERROR: IC worktree not found. Cannot proceed."
    exit 1
fi

echo "IC worktree: $IC_WORKTREE"
echo "Branch: $(cd "$IC_WORKTREE" && git branch --show-current)"
```

**The IC worktree path is the SOURCE for all sync operations.**

## Project Configuration

```
ROOT_REPO:     /Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private
IC_WORKTREE:   /Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/.claude/worktrees/iron-condor
LEAN_WORKSPACE: /Users/vigneshwaranarumugam/Documents/Trading Github/lean-workspace/AlphaNextGen
QC_PROJECT_ID: 27678023
VENV:          /Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/venv
LOGS_DIR:      /Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/docs/audits/logs
STAGE_FOLDER:  stage12.33-IC  (or current version from IC branch config.py)
```

## Why Standard Workflow Fails for IC

`scripts/qc_backtest.sh` has `SRC` hardcoded to the root repo (line 25):
```bash
SRC="/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private"
```

Even though `sync_to_lean.sh` has worktree detection, `qc_backtest.sh`:
1. Sets `SRC` to root repo (line 25)
2. Does `cd "$SRC"` (line 100)
3. Calls `sync_to_lean.sh` from root context
4. Runs minify/validate scripts from root

**Result:** Root branch code gets pushed, NOT IC branch code. IC config changes are lost.

## Mandatory Completion Contract (Self-Healing Loop)

A backtest task is complete only when all of the following are true:

### Phase 1: Backtest Execution + Core Artifacts (autonomous — no user input)

1. IC worktree detected, branch verified as `feature/va/iron-condor`.
2. Pre-flight checks pass (compile, tests, sync verification).
3. Code synced from IC worktree to lean-workspace (manual `cp`, NOT `qc_backtest.sh`).
4. Minify + validate + size guard pass.
5. Push to QC cloud succeeds.
6. `lean cloud backtest "AlphaNextGen" --name "<BACKTEST_NAME>" --parameter run_label "<BACKTEST_NAME>" --open` exits successfully.
7. QC backtest status is terminal-success (completed, not failed/cancelled/runtime-error).
8. Core artifacts are pulled with:
   `python "$IC_WORKTREE/scripts/qc_pull_backtest.py" "<BACKTEST_NAME>" --all --skip-observability --project 27678023 --output "$IC_WORKTREE/docs/audits/logs/<stage_folder>"`
9. Required core files exist in the stage folder:
   - `*_logs.txt`
   - `*_trades.csv`
   - `*_orders.csv`
   - `*_overview.txt`

### Phase 2: ObjectStore Scaffolding (autonomous — agent creates these)

10. ObjectStore scaffolding files created in the stage folder:
    - `objectstore_loader_<ver>_<year>.py` (copy of canonical loader with RUN_NAME and BACKTEST_YEAR set)
    - `<RUN_NAME>_OBJECTSTORE_CROSSCHECK.md` (empty crosscheck with backtest ID, expected keys, paste marker)
    - `<RUN_NAME>_OBJECTSTORE_RESEARCH_RUNBOOK.md` (run-specific instructions with backtest ID, expected keys, RCA scripts)

### Phase 3: User Checkpoint + Analysis (requires user input)

11. **Ask the user**: "ObjectStore scaffolding is ready in `<stage_folder>`. Do you want to load the ObjectStore data via QC Research notebook now? I can proceed with log/trade analysis using the core artifacts, or wait for you to paste ObjectStore data first."
12. Launch `log-analyzer` and/or `trade-analyzer` on the core artifacts (proceed regardless of ObjectStore readiness).

If any Phase 1 step fails, the agent must:

1. Capture the exact failure message.
2. Fix root cause in code — **always edit in the IC worktree** (never root repo or lean-workspace).
3. Re-run from Step 4 (sync from worktree).
4. Repeat until all Phase 1 conditions pass.

Do not stop at first failure. Do not ask the user to run intermediate recovery commands.

## Environment Setup (All Agents)

Before any backtest workflow, ensure the environment is correct:

```bash
source "/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/venv/bin/activate"
python --version   # Must be 3.11.x (NOT 3.14)
```

System Python is 3.14, but the project requires 3.11 via the venv. Lean CLI is installed globally on system Python (`pip3 install lean==1.0.221`). Do **not** upgrade lean — newer versions break on pydantic v2.

## IC Backtest Workflow (Step by Step)

### Phase 1: Execute + Pull Core Artifacts

#### Step 1: Environment Setup

```bash
IC_WORKTREE="/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/.claude/worktrees/iron-condor"
LEAN_DST="/Users/vigneshwaranarumugam/Documents/Trading Github/lean-workspace/AlphaNextGen"
VENV_PYTHON="/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/venv/bin/python"

# Activate venv and verify
source "/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/venv/bin/activate"
python --version  # Must be 3.11.x

# Verify we're on the IC branch
cd "$IC_WORKTREE"
git branch --show-current  # Must be feature/va/iron-condor

# Verify IC engine is enabled
grep "IRON_CONDOR_ENGINE_ENABLED" "$IC_WORKTREE/config.py"
grep "ISOLATION_TEST_MODE" "$IC_WORKTREE/config.py"
```

#### Step 2: Pre-Flight Checks

```bash
cd "$IC_WORKTREE"

# Compile check — include IC engine
python -m py_compile main.py
python -m py_compile config.py
python -m py_compile engines/satellite/iron_condor_engine.py
python -m py_compile engines/satellite/options_engine.py
python -m py_compile portfolio/portfolio_router.py

# Run IC tests
pytest tests/test_iron_condor_engine.py -q

# Check for uncommitted changes
git status --short
```

If tests fail or there are compile errors, fix them before proceeding.

#### Step 3: Update Dates in main.py (if needed)

```bash
cd "$IC_WORKTREE"
# Read current dates
grep "SetStartDate\|SetEndDate" main.py
```

Edit `main.py` in the **IC worktree** (not root!) to set the backtest period.

#### Step 4: Sync FROM WORKTREE to Lean Workspace

**This is the critical step.** Do NOT use `qc_backtest.sh` — it syncs from root.

```bash
IC_WORKTREE="/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/.claude/worktrees/iron-condor"
LEAN_DST="/Users/vigneshwaranarumugam/Documents/Trading Github/lean-workspace/AlphaNextGen"

echo "=== IC Sync: Worktree → Lean Workspace ==="
echo "Source: $IC_WORKTREE"
echo "Dest:   $LEAN_DST"

# Core files
cp "$IC_WORKTREE/main.py" "$LEAN_DST/"
cp "$IC_WORKTREE/config.py" "$LEAN_DST/"

# Mixins
rm -f "$LEAN_DST"/main*_mixin.py
for f in "$IC_WORKTREE"/main*_mixin.py; do
    [ -f "$f" ] && cp "$f" "$LEAN_DST/"
done

# Engine and infrastructure directories
for dir in engines portfolio execution models persistence scheduling data utils; do
    if [ -d "$IC_WORKTREE/$dir" ]; then
        rm -rf "$LEAN_DST/$dir"
        cp -r "$IC_WORKTREE/$dir" "$LEAN_DST/"
    fi
done

# Cleanup
find "$LEAN_DST" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "$LEAN_DST" -name ".DS_Store" -delete 2>/dev/null || true
find "$LEAN_DST" -name "*.pyc" -delete 2>/dev/null || true

echo "Sync complete from IC worktree."
```

#### Step 5: Verify Sync Integrity

```bash
# Confirm IC-specific values are in lean workspace (not root values)
echo "=== Sync Verification ==="

# Check IC engine enabled
grep "IRON_CONDOR_ENGINE_ENABLED" "$LEAN_DST/config.py"

# Check IC delta config (should be IC branch values, not root defaults)
grep "IC_SHORT_DELTA_MIN" "$LEAN_DST/config.py"
grep "IC_SHORT_DELTA_MAX" "$LEAN_DST/config.py"

# Check VASS is disabled (IC isolation)
grep "VASS_ENABLED" "$LEAN_DST/config.py"

# Check isolation mode
grep "ISOLATION_TEST_MODE" "$LEAN_DST/config.py"

# Verify dates
grep "SetStartDate\|SetEndDate" "$LEAN_DST/main.py"
```

**If any value doesn't match the IC worktree, STOP. The sync failed.**

#### Step 6: Minify

```bash
VENV_PYTHON="/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/venv/bin/python"
IC_WORKTREE="/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/.claude/worktrees/iron-condor"
LEAN_DST="/Users/vigneshwaranarumugam/Documents/Trading Github/lean-workspace/AlphaNextGen"

"$VENV_PYTHON" "$IC_WORKTREE/scripts/minify_workspace.py"
"$VENV_PYTHON" "$IC_WORKTREE/scripts/ultra_minify.py" --workspace "$LEAN_DST" --target-indent 1
```

#### Step 7: Validate

```bash
"$VENV_PYTHON" "$IC_WORKTREE/scripts/validate_lean_minified.py" --root "$LEAN_DST" --strict
```

If validation fails, fix the issue and re-run from Step 4.

#### Step 8: Size Guard

```bash
echo "=== Per-file size check (max 256KB) ==="
oversized=0
for f in $(find "$LEAN_DST" -name "*.py" -type f); do
    size=$(wc -c < "$f")
    if [ "$size" -gt 256000 ]; then
        echo "OVERSIZED: $f ($(( size / 1024 ))KB)"
        oversized=1
    fi
done
if [ "$oversized" -eq 1 ]; then
    echo "ERROR: Files exceed 256KB limit. Fix before pushing."
fi
```

#### Step 9: Push to QC Cloud

```bash
cd "/Users/vigneshwaranarumugam/Documents/Trading Github/lean-workspace"
lean cloud push --project AlphaNextGen
```

If push fails with 413, use the individual file push script:
```bash
"$VENV_PYTHON" "$IC_WORKTREE/scripts/qc_push_individual.py" \
    --workspace "$LEAN_DST" \
    --project-id 27678023
```

#### Step 10: Start Backtest

```bash
cd "/Users/vigneshwaranarumugam/Documents/Trading Github/lean-workspace"
lean cloud backtest "AlphaNextGen" --name "<BACKTEST_NAME>" --parameter run_label "<BACKTEST_NAME>" --open
```

**IMPORTANT:** Use `--open` to wait for completion and stream results.
**CRITICAL:** The `--parameter run_label` flag pins ObjectStore keys to this run name. Without it, keys fall back to generic `year_YYYY` pattern and get overwritten by subsequent backtests — making ObjectStore data unreliable for RCA.

#### Step 11: Handle Failures (Self-Healing Loop)

If backtest fails with runtime error:
1. Read the error from the output
2. Fix the code in the **IC worktree** (not root!)
3. Re-run from Step 4 (sync from worktree)
4. Repeat until success

**Never fix code in the root repo or lean-workspace directly.**

#### Step 12: Pull Core Artifacts

Always use `--skip-observability` (QC blocks CLI ObjectStore export on this account tier).

```bash
IC_WORKTREE="/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/.claude/worktrees/iron-condor"
VENV_PYTHON="/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/venv/bin/python"

"$VENV_PYTHON" "$IC_WORKTREE/scripts/qc_pull_backtest.py" "<BACKTEST_NAME>" --all --skip-observability \
    --project 27678023 \
    --output "$IC_WORKTREE/docs/audits/logs/<stage_folder>"
```

Stage folder naming: `stage12.33-IC` (or current version from IC branch config.py).

If name lookup is ambiguous, pull by explicit backtest id:
```bash
"$VENV_PYTHON" "$IC_WORKTREE/scripts/qc_pull_backtest.py" --id "<BACKTEST_ID>" --all --skip-observability \
    --project 27678023 \
    --output "$IC_WORKTREE/docs/audits/logs/<stage_folder>"
```

#### Step 13: Verify Core Artifacts

Required files in stage folder:
- `*_logs.txt`
- `*_trades.csv`
- `*_orders.csv`
- `*_overview.txt`

If any are missing, retry pull (by name then explicit id).

### Phase 2: Create ObjectStore Scaffolding

#### Step 14: Resolve Variables and Create Scaffolding

Resolve these variables from the completed backtest (all 3 files depend on them):

```
RUN_NAME        = backtest name from step 10          (e.g. "V12.33-IC-FullYear2024")
BACKTEST_ID     = backtest id captured in step 10     (e.g. "a7947d16dfb3b9d186026372f5731d7a")
BACKTEST_YEAR   = year extracted from date range       (e.g. 2024)
START_DATE      = start date from step 3              (e.g. "2024-01-01")
END_DATE        = end date from step 3                (e.g. "2024-12-31")
SANITIZED_NAME  = RUN_NAME with dots replaced by _    (e.g. "V12_33-IC-FullYear2024")
VER_SLUG        = version slug, lowercase dots→_      (e.g. "v12_33_ic" from "V12.33-IC-FullYear2024")
STAGE_FOLDER    = output folder from step 12          (e.g. "$IC_WORKTREE/docs/audits/logs/stage12.33-IC")
```

**14a. Discover baseline run for delta comparison:**
- Search `$IC_WORKTREE/docs/audits/logs/` for the most recent prior IC stage folder with an overview file for the same `BACKTEST_YEAR` (excluding the current run).
- Example: for `V12.33-IC-FullYear2024`, search for `stage*-IC/V12.*IC*2024*_overview.txt` but NOT `V12.33-IC-FullYear2024_overview.txt`.
- If found, read the baseline overview and extract:
  ```
  BASELINE_RUN_NAME     = e.g. "V12.32-IC-FullYear2024-v3"
  BASELINE_BACKTEST_ID  = from overview "Backtest ID:" line
  BASELINE_STAGE_FOLDER = e.g. "$IC_WORKTREE/docs/audits/logs/stage12.32-IC"
  BASELINE_NET_PROFIT   = from overview "Net Profit" line
  BASELINE_SHARPE       = from overview "Sharpe Ratio" line
  BASELINE_DRAWDOWN     = from overview "Drawdown" line
  BASELINE_WIN_RATE     = from overview "Win Rate" line
  BASELINE_ORDERS       = from overview "Total Orders" line
  BASELINE_FEES         = from overview "Total Fees" line
  ```
- Also run `git log --oneline` in the IC worktree to find commits between the baseline version and current version. Extract a summary of code changes for the delta table.
- If no baseline found, set `BASELINE_RUN_NAME = None` and omit delta sections from crosscheck/runbook.

Then create these 3 files in `STAGE_FOLDER`:

**a) `objectstore_loader_{VER_SLUG}_{BACKTEST_YEAR}.py`**
- Read the canonical template: `$IC_WORKTREE/scripts/qc_research_objectstore_loader.py`
  (if not present in IC worktree, fall back to root: `scripts/qc_research_objectstore_loader.py`)
- Replace the entire docstring (lines 1-10) with run-specific metadata:
  ```python
  """QuantConnect Research Object Store loader for Alpha NextGen {RUN_NAME}.

  Usage in QC Research notebook:
  1) Upload this file to the project (or paste into a notebook cell).
  2) Run the script cell.

  Run: {RUN_NAME}
  Backtest ID: {BACKTEST_ID}
  Project ID: 27678023
  Period: {START_DATE} to {END_DATE}
  """
  ```
- Replace `RUN_NAME = "V12.4-JulSep2024-R2"` → `RUN_NAME = "{RUN_NAME}"`
- Replace `BACKTEST_YEAR = 2024` → `BACKTEST_YEAR = {BACKTEST_YEAR}`
- Write to `{STAGE_FOLDER}/objectstore_loader_{VER_SLUG}_{BACKTEST_YEAR}.py`
- **DO NOT** name the file `objectstore_loader.py` — it MUST have the versioned suffix (e.g., `objectstore_loader_v12_33_ic_2024.py`)

**b) `{RUN_NAME}_OBJECTSTORE_CROSSCHECK.md`**
- Use the Crosscheck Template from the "ObjectStore Scaffolding" section below, substituting all `{...}` variables.
- Write to `{STAGE_FOLDER}/{RUN_NAME}_OBJECTSTORE_CROSSCHECK.md`

**c) `{RUN_NAME}_OBJECTSTORE_RESEARCH_RUNBOOK.md`**
- Use the Runbook Template structure from the "ObjectStore Scaffolding" section below.
- Must include ALL 14 sections: objective, run metadata, cross-reference baseline (if found), code change delta table, expected keys, why required, setup steps, Cell 2 RCA script, Cell 3 delta analysis script, validation checks (standard + IC-specific + version-specific), market context, artifact file table, cross-reference artifact table, local artifacts protocol.
- **CRITICAL**: Use `.get("key", pd.DataFrame())` in all scripts — NEVER bracket access. Wrap all analysis blocks with `if not df.empty` guards.
- Write to `{STAGE_FOLDER}/{RUN_NAME}_OBJECTSTORE_RESEARCH_RUNBOOK.md`

**Concrete example** — if backtest name is `V12.33-IC-FullYear2024`, backtest ID is `abc123`, dates are 2024-01-01 to 2024-12-31:
```
objectstore_loader_v12_33_ic_2024.py                       ← RUN_NAME="V12.33-IC-FullYear2024", BACKTEST_YEAR=2024
V12.33-IC-FullYear2024_OBJECTSTORE_CROSSCHECK.md           ← keys use "V12_33-IC-FullYear2024_2024"
V12.33-IC-FullYear2024_OBJECTSTORE_RESEARCH_RUNBOOK.md     ← backtest ID "abc123", same keys
```

### Phase 3: User Checkpoint + Analysis

#### Step 15: User Checkpoint + IC Config Summary

**USER CHECKPOINT** — Report to the user:
- Confirm backtest completed successfully with backtest ID.
- List all artifacts created (4 core + 3 scaffolding) with paths.
- Include IC config snapshot (see template below).
- Ask: "ObjectStore scaffolding is ready. When you're ready, upload `objectstore_loader_<ver>_<year>.py` to QC Research, run it, and paste the output into `<RUN_NAME>_OBJECTSTORE_CROSSCHECK.md`. I'll now proceed with core artifact analysis."

```
## IC Backtest Complete

**Branch:** feature/va/iron-condor
**Source:** IC worktree (NOT root)
**Name:** [backtest name]
**Backtest ID:** [backtest id]
**Period:** [start] to [end]

### Config Snapshot
- IC_SHORT_DELTA: [min]-[max]
- IC_WING_WIDTH: $[low]/$[mid]/$[high]
- IC_TARGET/STOP: [target]% / [stop]×
- IC_DTE_RANGES: [ranges]
- ISOLATION_TEST_MODE: True
- VASS_ENABLED: False

### Results
| Metric | Value |
|--------|-------|
| Net Return | X.XX% |
| Total Trades | XXX |
| IC Trades | XXX |

### Core Artifacts
`docs/audits/logs/<stage>/<files>`

### ObjectStore Scaffolding
- `objectstore_loader_<ver>_<year>.py`
- `<RUN_NAME>_OBJECTSTORE_CROSSCHECK.md`
- `<RUN_NAME>_OBJECTSTORE_RESEARCH_RUNBOOK.md`
```

#### Step 16: Launch Analysis

Launch `log-analyzer` and/or `trade-analyzer` agents on the core artifacts (do not wait for ObjectStore data — proceed immediately).

Report findings with file/line evidence.

## Failure Handling

### 1) Validation failure

- Read missing marker/compile error from validator output.
- Fix code in **IC worktree** and re-run from Step 4.

### 2) Per-file size failure (>256KB)

- Reduce file size in offending file(s) in **IC worktree**.
- Keep required telemetry markers intact.
- Re-run from Step 4.

### 3) QC push/backtest runtime error

- Fix code in **IC worktree**.
- Re-run from Step 4 end-to-end (no manual partial workflow).

### 4) Runtime loop policy (mandatory)

Use this loop for all failures (validation, size guard, push, compile, runtime exceptions):

1. Run Steps 4-10 (sync → push → backtest with `--open`).
2. If failure:
   - classify (`validation`, `size`, `push`, `compile`, `runtime`, `artifact-pull`)
   - apply minimal targeted fix **in IC worktree**
   - rerun from Step 4
3. On backtest success, pull artifacts and verify required files.
4. If artifact pull fails, fix pull path/name/id and retry pull until successful.

This is an autonomous workflow. The agent owns recovery until completion.

## Hard Rules

1. **NEVER sync from root repo.** Always sync from IC worktree.
2. **NEVER use `qc_backtest.sh` directly** — it hardcodes root as source.
3. **NEVER edit code in root repo or lean-workspace** — always edit in IC worktree.
4. **ALWAYS verify sync** — check IC-specific config values in lean workspace before pushing.
5. **ALWAYS run from worktree context** — `cd "$IC_WORKTREE"` before any git or pytest commands.
6. **Venv path:** `source "/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/venv/bin/activate"` (shared venv, not worktree-local).
7. **NEVER run `lean cloud push` directly** — always go through the full sync → minify → validate → push pipeline.
8. **ALWAYS use `--skip-observability`** on artifact pull — QC blocks CLI ObjectStore export on this tier.

## ObjectStore Scaffolding Reference

### Naming Convention

- **Loader filename**: `objectstore_loader_<ver>_<year>.py` where `<ver>` is the version extracted from backtest name (e.g., `v12_33_ic` from `V12.33-IC-FullYear2024`), `<year>` is the backtest year.
  - Example: `objectstore_loader_v12_33_ic_2024.py`
- **Crosscheck/Runbook**: Use the full `<RUN_NAME>` prefix.
  - Example: `V12.33-IC-FullYear2024_OBJECTSTORE_CROSSCHECK.md`

### Sanitized Run Name

ObjectStore keys use a sanitized version of the run name: replace non-alphanumeric chars (except `-_`) with `_`.
- `V12.33-IC-FullYear2024` → `V12_33-IC-FullYear2024`

### Expected ObjectStore Key Pattern

```
regime_observability__<sanitized_run_name>_<year>.csv
regime_timeline_observability__<sanitized_run_name>_<year>.csv
signal_lifecycle_observability__<sanitized_run_name>_<year>.csv
router_rejection_observability__<sanitized_run_name>_<year>.csv
order_lifecycle_observability__<sanitized_run_name>_<year>.csv
```

Sharded variants (if payload exceeded single-key limit):
- Manifest: `<prefix>__<sanitized_run_name>_<year>__manifest.json`
- Parts: `<prefix>__<sanitized_run_name>_<year>__part001.csv`, `__part002.csv`, ...

### Crosscheck Template

The crosscheck must include ALL of these sections. **Do NOT produce a minimal crosscheck.** Use the reference example for completeness.

```markdown
# <RUN_NAME> Object Store Cross-Check

## Run Metadata

- Backtest name: `<RUN_NAME>`
- Backtest ID: `<BACKTEST_ID>`
- Project ID: 27678023
- Period: <START_DATE> to <END_DATE>
- Expected run suffix (algorithm sanitizer): `<SANITIZED_RUN_NAME>`
- Net Profit: <from overview> | Sharpe: <from overview> | Drawdown: <from overview> | Win Rate: <from overview> | Trades: <from overview>

## Expected Object Store Keys

- `regime_observability__<SANITIZED_RUN_NAME>_<YEAR>.csv`
- `regime_timeline_observability__<SANITIZED_RUN_NAME>_<YEAR>.csv`
- `signal_lifecycle_observability__<SANITIZED_RUN_NAME>_<YEAR>.csv`
- `router_rejection_observability__<SANITIZED_RUN_NAME>_<YEAR>.csv`
- `order_lifecycle_observability__<SANITIZED_RUN_NAME>_<YEAR>.csv`

Sharded variants (if payload exceeded single-key limit):
- Manifest: `<prefix>__<SANITIZED_RUN_NAME>_<YEAR>__manifest.json`
- Parts: `<prefix>__<SANITIZED_RUN_NAME>_<YEAR>__part001.csv`, `__part002.csv`, ...

## Validation Checklist

### Standard Every-Run Checks
1. `transition_overlay` flip count and distribution — <market context>.
2. IC signal lifecycle events during stable/deterioration overlays.
3. Regime decision tuples alignment with IC handoff gates.
4. Engine funnel approval rates by engine.
5. Catastrophic exit count and same-session re-entry health.
6. Trace integrity: APPROVED signal trace + order trace coverage (100% expected).

### IC-Specific Checks
7. IC entry gate pass rate (EM buffer, C/W floor, delta range).
8. IC exit reason distribution (stop, MFE, profit target, DTE, hold guard).
9. IC condor_id lifecycle completeness (entry → exit pairing).

### Version-Specific Checks (Delta vs <BASELINE_RUN_NAME>)
<!-- Agent: populate from code change delta table in step 14a.
     Each check should reference the baseline value for comparison.
     Example: "10. EM gate pass rate (baseline: 45%) — expect improvement after threshold change." -->
10. <check with baseline reference value and expected direction>
11. <check with baseline reference value and expected direction>
...

## Code Change Summary (vs <BASELINE_RUN_NAME>)

| # | Change | Version | Expected Delta vs Baseline |
|---|--------|---------|---------------------------|
| 1 | <change from git log> | <ver> | <expected observable delta> |
| ... | ... | ... | ... |

## Baseline Comparison (<BASELINE_RUN_NAME> vs <RUN_NAME>)

<!-- Pre-fill baseline column from the baseline overview + crosscheck output.
     Current run column stays TBD until user pastes ObjectStore output. -->

| Metric | <BASELINE_RUN_NAME> | <RUN_NAME> | Delta |
|--------|---------------------|------------|-------|
| Net Profit | <baseline> | <current> | TBD |
| Sharpe Ratio | <baseline> | <current> | TBD |
| Drawdown | <baseline> | <current> | TBD |
| Win Rate | <baseline> | <current> | TBD |
| Total Orders | <baseline> | TBD | TBD |
| IC Entries | <from baseline crosscheck or TBD> | TBD | TBD |
| IC Exits | <from baseline crosscheck or TBD> | TBD | TBD |
| IC Win Rate | <from baseline crosscheck or TBD> | TBD | TBD |
| IC Avg P&L | <from baseline crosscheck or TBD> | TBD | TBD |
| STABLE overlay % | <from baseline crosscheck or TBD> | TBD | Should be similar |
| <version-specific metrics...> | ... | ... | ... |

## Instructions

1. Open QC project `27678023` in the QuantConnect web IDE.
2. Open Research and create a new Python notebook.
3. Paste and run `<STAGE_FOLDER>/objectstore_loader_<VER_SLUG>_<YEAR>.py`.
4. Copy the full notebook output (Cell 1 loader + Cell 2 RCA + Cell 3 delta) and paste below.
5. Fill in the "<RUN_NAME>" column in the Baseline Comparison table above.

---

PASTE NOTEBOOK OUTPUT BELOW THIS LINE:

```

**Pre-fill the baseline column:** If a baseline crosscheck exists with pasted output (e.g., `<BASELINE_STAGE_FOLDER>/<BASELINE_RUN_NAME>_OBJECTSTORE_CROSSCHECK.md`), read it and extract funnel counts, drop code counts, overlay percentages, and exit pattern counts to pre-fill the baseline column. This gives the user immediate comparison context when they paste new output.

**Reference example for crosscheck completeness**: `docs/audits/logs/stage12.32/V12.32-FullYear2024_OBJECTSTORE_CROSSCHECK.md`

### Runbook Template

The runbook must contain ALL of these sections in order. **Do NOT omit any section.** Use the reference examples to verify completeness.

1. **Objective** — What this runbook validates and the market context (bull/bear year)
2. **Confirmed Run Metadata** — Backtest name, ID, project ID, period, sanitized name, headline stats from overview
3. **Cross-Reference: Baseline Run** (if `BASELINE_RUN_NAME` found) — Baseline metadata, stats, stage folder, loader path
4. **Code Change Delta Table** (if baseline found) — Table of changes between versions with expected observable deltas
5. **Expected Object Store Keys** — 5 keys using `SANITIZED_NAME` + sharded variants
6. **Why this path is required** — QC blocks CLI export
7. **Setup and Execution steps** — Open QC project → Research → paste loader → run
8. **Post-Load RCA Script (Cell 2)** — Standard IC script using `.get()` with empty-check guards
9. **Delta Analysis Script (Cell 3)** (if baseline found) — Version-specific probes comparing against baseline
10. **Validation Checks to Record** — Split into "Standard Every-Run Checks" + "IC-Specific Checks" + "Version-Specific Checks (Delta vs Baseline)"
11. **Context for Market Analysis** — Key delta questions for this version vs baseline
12. **Artifact Files in Stage Folder** — Table of all files (core + scaffolding + reports)
13. **Cross-Reference: Baseline Artifacts** (if baseline found) — Table of baseline files with paths
14. **Local Artifacts Update Protocol** — Where to save crosscheck, what to include

**Section 3: Cross-Reference Template** (only if `BASELINE_RUN_NAME` is set):

```markdown
## Cross-Reference: <BASELINE_RUN_NAME> (Baseline)

- Backtest name: `<BASELINE_RUN_NAME>`
- Backtest ID: `<BASELINE_BACKTEST_ID>`
- Stage folder: `<BASELINE_STAGE_FOLDER>/`
- Loader: `objectstore_loader_<baseline_ver>_<year>.py`
- Net Profit: <BASELINE_NET_PROFIT> | Sharpe: <BASELINE_SHARPE> | Drawdown: <BASELINE_DRAWDOWN> | Win Rate: <BASELINE_WIN_RATE> | Total Orders: <BASELINE_ORDERS> | Fees: <BASELINE_FEES>

### Code Changes (<BASELINE_VERSION> → <CURRENT_VERSION>)

| Change | Version | Expected Delta vs Baseline |
|--------|---------|---------------------------|
| <change description from git log> | <version> | <expected observable delta> |
| ... | ... | ... |
```

To populate the delta table: run `git log --oneline` in the IC worktree between the baseline version tag and current HEAD, group commits by feature/fix, and describe the expected observable impact of each on the ObjectStore data (e.g., "fewer X rejections", "more Y approvals", "zero Z exits in time window").

**Section 8: Standard Post-Load RCA Script (Cell 2):**

CRITICAL: Always use `.get("key", pd.DataFrame())` — NEVER use bracket access `["key"]`. Always wrap analysis blocks with `if not df.empty` guards.

```python
import pandas as pd
from datetime import timedelta

order_df = loaded_artifacts.get("order_lifecycle", pd.DataFrame()).copy()
router_df = loaded_artifacts.get("router_rejections", pd.DataFrame()).copy()
sig_df = loaded_artifacts.get("signal_lifecycle", pd.DataFrame()).copy()
tl_df = loaded_artifacts.get("regime_timeline", pd.DataFrame()).copy()

print(f"Loaded: order={len(order_df)} router={len(router_df)} signal={len(sig_df)} timeline={len(tl_df)}")

for df in (order_df, router_df, sig_df, tl_df):
    if not df.empty and "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"], errors="coerce")
        df.dropna(subset=["time"], inplace=True)

def pick(df, names):
    for n in names:
        if n in df.columns:
            return n
    return None

def row_text(df):
    if df.empty:
        return pd.Series(dtype=str)
    cols = [c for c in df.columns if df[c].dtype == "object"]
    if not cols:
        cols = list(df.columns)
    return df[cols].astype(str).agg(" | ".join, axis=1).str.upper()

# 1) IC events by transition overlay
if not sig_df.empty and not tl_df.empty:
    eng = pick(sig_df, ["engine"])
    evt = pick(sig_df, ["event"])
    if eng and evt:
        ic = sig_df[sig_df[eng].astype(str).str.upper().str.contains("IC|CONDOR", na=False)].copy()
        ic["event_u"] = ic[evt].astype(str).str.upper()
        joined = pd.merge_asof(
            ic.sort_values("time"),
            tl_df[["time", "transition_overlay"]].sort_values("time"),
            on="time", direction="backward", tolerance=timedelta(hours=6),
        )
        print("IC events by overlay:")
        print(pd.crosstab(joined["transition_overlay"].fillna("NO_MATCH"), joined["event_u"]))
    else:
        print("SKIP: signal_lifecycle missing engine/event columns")
else:
    print("SKIP: signal_lifecycle or regime_timeline empty")

# 2) Catastrophic exits vs same-session re-entry
if not order_df.empty and not sig_df.empty:
    otext = row_text(order_df)
    cat_mask = otext.str.contains("IC_STOP_LOSS|IC_HARD_STOP|CONDOR_STOP", na=False)
    exit_rows = order_df.loc[cat_mask, ["time"]].sort_values("time")
    eng = pick(sig_df, ["engine"])
    evt = pick(sig_df, ["event"])
    if eng and evt:
        ic = sig_df[sig_df[eng].astype(str).str.upper().str.contains("IC|CONDOR", na=False)].copy()
        ic["event_u"] = ic[evt].astype(str).str.upper()
        approved = ic[ic["event_u"] == "APPROVED"][["time"]].sort_values("time")
        reentries = 0
        for t in exit_rows["time"]:
            same_day = approved[(approved["time"].dt.date == t.date()) & (approved["time"] > t)]
            if not same_day.empty:
                reentries += 1
        print(f"Catastrophic exit rows={len(exit_rows)} | same-session re-entry rows={reentries}")
    else:
        print(f"Catastrophic exit rows={len(exit_rows)} | SKIP re-entry check (missing signal columns)")
else:
    print("SKIP: order_lifecycle or signal_lifecycle empty")

# 3) Quote-invalid proximity
if not router_df.empty and not order_df.empty:
    rtext = row_text(router_df)
    qinv = router_df[rtext.str.contains("R_CONTRACT_QUOTE_INVALID|EXIT_NET_VALUE_NEGATIVE", na=False)].copy()
    otext = row_text(order_df)
    cat_mask = otext.str.contains("IC_STOP_LOSS|IC_HARD_STOP|CONDOR_STOP", na=False)
    exit_rows = order_df.loc[cat_mask, ["time"]].sort_values("time")
    near = 0
    for t in exit_rows["time"]:
        hit = ((qinv["time"] >= t - timedelta(minutes=10)) & (qinv["time"] <= t + timedelta(minutes=30))).any()
        near += int(hit)
    print(f"Quote-invalid rows={len(qinv)} | catastrophic exits with nearby quote-invalid={near}")
    if not qinv.empty:
        print(qinv.sort_values("time").head(20))
else:
    print("SKIP: router_rejections or order_lifecycle empty")
```

**Section 9: Delta Analysis Script (Cell 3):**

Only include if `BASELINE_RUN_NAME` is set. This script compares the current run against the baseline. Generate it with version-specific probes based on the code changes discovered in step 14a.

The script MUST include these standard probes (D1-D8) plus any version-specific probes (D9+):

```python
# === <RUN_NAME> vs <BASELINE_RUN_NAME> Delta Analysis ===
# Baseline: <BASELINE_NET_PROFIT> | <BASELINE_ORDERS> orders | <BASELINE_DRAWDOWN> DD

print("\n=== <RUN_NAME> vs <BASELINE_RUN_NAME> Delta Analysis ===\n")

order_text = row_text(order_df) if not order_df.empty else pd.Series(dtype=str)

eng_col = pick(sig_df, ["engine"])
evt_col = pick(sig_df, ["event"])
strat_col = pick(sig_df, ["strategy"])
code_col = pick(sig_df, ["code"])
dir_col = pick(sig_df, ["direction"])

# --- D1: IC funnel (candidate/approved/dropped + approval rate) ---
if eng_col and evt_col and not sig_df.empty:
    ic_df = sig_df[sig_df[eng_col].astype(str).str.upper().str.contains("IC|CONDOR", na=False)]
    ic_approved = ic_df[ic_df[evt_col].astype(str).str.upper() == "APPROVED"]
    ic_dropped = ic_df[ic_df[evt_col].astype(str).str.upper() == "DROPPED"]
    ic_candidate = ic_df[ic_df[evt_col].astype(str).str.upper() == "CANDIDATE"]
    print(f"D1) IC funnel: candidate={len(ic_candidate)} approved={len(ic_approved)} dropped={len(ic_dropped)}")
    print(f"    Approval rate: {100.0*len(ic_approved)/max(1,len(ic_candidate)):.1f}%")
    if strat_col:
        print(f"    Approved by strategy:")
        print(ic_approved[strat_col].astype(str).str.upper().value_counts())
else:
    print("D1) SKIP: signal_lifecycle missing columns")

# --- D2: Version-specific telemetry codes ---
# Agent: populate this list from git log code changes discovered in step 14a.
# Include codes introduced/modified between baseline and current version.
print(f"\nD2) Version-specific telemetry codes:")
version_codes = [
    # ("CODE_NAME", "description of what it means and expected count"),
    # Add entries based on git log analysis
]
if code_col and not sig_df.empty:
    for code, desc in version_codes:
        count = sig_df[code_col].astype(str).str.upper().str.contains(code, na=False).sum()
        print(f"    {code}: {count}  ({desc})")

# --- D3: IC exit pattern distribution ---
print(f"\nD3) IC exit pattern distribution (compare vs baseline crosscheck):")
ic_exit_patterns = [
    "IC_PROFIT_TARGET", "IC_STOP_LOSS", "IC_MFE_LOCK", "IC_TRAIL_STOP",
    "IC_DTE_EXIT", "IC_HOLD_GUARD", "IC_HARD_STOP", "IC_UNDERLYING_INVALID",
    "IC_WING_BREACH", "IC_THETA_TARGET", "IC_EM_EXIT",
    "CONDOR_STOP", "CONDOR_PROFIT", "CONDOR_DTE",
]
if not order_df.empty:
    for pat in ic_exit_patterns:
        count = order_text.str.contains(pat, na=False).sum()
        if count > 0:
            print(f"    {pat}: {count}")

# --- D4: Regime overlay distribution ---
print(f"\nD4) Regime overlay distribution:")
if not tl_df.empty and "transition_overlay" in tl_df.columns:
    counts = tl_df["transition_overlay"].value_counts()
    total = len(tl_df)
    for k, v in counts.items():
        print(f"    {k}: {v} ({100.0*v/total:.1f}%)")

# --- D5: Monthly IC approved entries ---
print(f"\nD5) Monthly IC approved entries:")
if eng_col and evt_col and not sig_df.empty:
    ic_df = sig_df[sig_df[eng_col].astype(str).str.upper().str.contains("IC|CONDOR", na=False)]
    ic_approved = ic_df[ic_df[evt_col].astype(str).str.upper() == "APPROVED"]
    if not ic_approved.empty and "time" in ic_approved.columns:
        ic_approved = ic_approved.copy()
        ic_approved["month"] = ic_approved["time"].dt.to_period("M")
        print(ic_approved.groupby("month").size())

# --- D6: IC dropped by rejection code ---
print(f"\nD6) IC dropped by rejection code:")
if code_col and eng_col and evt_col and not sig_df.empty:
    ic_df = sig_df[sig_df[eng_col].astype(str).str.upper().str.contains("IC|CONDOR", na=False)]
    ic_dropped = ic_df[ic_df[evt_col].astype(str).str.upper() == "DROPPED"]
    if not ic_dropped.empty:
        print(ic_dropped[code_col].astype(str).str.upper().value_counts().head(20))
    else:
        print("    No dropped entries")

# --- D7: IC entry gate breakdown ---
print(f"\nD7) IC entry gate breakdown:")
if not router_df.empty and "code" in router_df.columns:
    ic_gates = router_df[router_df["code"].astype(str).str.upper().str.contains("IC_|CONDOR_|R_IC_", na=False)]
    if not ic_gates.empty:
        print(ic_gates["code"].astype(str).str.upper().value_counts().head(20))
    else:
        print("    No IC-specific router rejections")

# --- D8: Insufficient-BP rejections ---
print(f"\nD8) Insufficient-BP rejections:")
if not router_df.empty and "code" in router_df.columns:
    bp_mask = router_df["code"].astype(str).str.upper().str.contains("INSUFFICIENT_BP|BUYING_POWER", na=False)
    print(f"    Total: {bp_mask.sum()}")
else:
    print("    SKIP: router_rejections empty or missing 'code'")

# Agent: Add D9+ probes for version-specific features discovered in git log.
# Examples of IC version-specific probes:
# - EM gate pass rate (threshold change)
# - MFE lock floor persistence (new feature)
# - Rejection cooldown fires (new feature)
# - Theta-derived exit fires (new feature)
# - DTE range distribution

print(f"\n=== Summary: Compare above counts against <BASELINE_RUN_NAME> crosscheck ===")
print(f"Baseline: <BASELINE_NET_PROFIT> | <BASELINE_ORDERS> orders | <BASELINE_DRAWDOWN> DD | <BASELINE_FEES> fees")
```

**Section 10: Validation Checks — always split into three sub-sections:**

```markdown
### Standard Every-Run Checks
1. `transition_overlay` flip count and distribution.
2. IC signal lifecycle events during stable/deterioration overlays.
3. Regime decision tuples alignment.
4. Engine funnel approval rates.
5. Catastrophic exit count and same-session re-entry.
6. Trace integrity (100% expected).

### IC-Specific Checks
7. IC entry gate pass rate (EM buffer, C/W floor, delta range).
8. IC exit reason distribution (stop, MFE, profit target, DTE, hold guard).
9. IC condor_id lifecycle completeness (entry → exit pairing).

### Version-Specific Checks (Delta vs <BASELINE_RUN_NAME>)
10. <check derived from code change 1>
11. <check derived from code change 2>
...
```

Populate version-specific checks from the code change delta table. Each check should state the expected value/direction and what to compare against in the baseline crosscheck.

**Section 12: Artifact Files in Stage Folder:**

List ALL files the agent created or expects in the stage folder (4 core + 3 scaffolding + any reports). Use a markdown table with File and Description columns.

**Section 13: Cross-Reference Baseline Artifacts:**

List key baseline files with their relative paths from the IC worktree root.

**Reference examples:**
- Loader: `docs/audits/logs/stage12.30/objectstore_loader_v12_30_2024.py`
- Crosscheck: `docs/audits/logs/stage12.30/V12.30-2024-Audit_OBJECTSTORE_CROSSCHECK.md`
- Runbook: `docs/audits/logs/stage12.30/V12.30-2024-Audit_OBJECTSTORE_RESEARCH_RUNBOOK.md`
- **Best reference for completeness**: `docs/audits/logs/stage12.32/V12.32-FullYear2024_OBJECTSTORE_RESEARCH_RUNBOOK.md`

## ObjectStore Data Access (Manual via QC Research)

**CLI export is blocked** on this account tier (`lean cloud object-store get` fails with institutional-only restriction). The agent does NOT attempt ObjectStore pull.

Instead, the agent creates scaffolding files that guide the user through the manual QC Research notebook workflow:

1. Agent creates `objectstore_loader_<ver>_<year>.py` (pre-configured loader script).
2. User uploads the loader to QC web IDE → Research notebook → runs it.
3. User pastes output into `<RUN_NAME>_OBJECTSTORE_CROSSCHECK.md`.
4. If deeper analysis is needed, user follows `<RUN_NAME>_OBJECTSTORE_RESEARCH_RUNBOOK.md`.

Concrete reference implementation:
1. Canonical guide: `docs/guides/objectstore-research-workflow.md`
2. Canonical loader template: `scripts/qc_research_objectstore_loader.py`
3. Reference stage: `docs/audits/logs/stage12.30/` (loader, crosscheck, runbook)

## Post-Pull Analysis Pipeline

After pulling core artifacts and creating ObjectStore scaffolding, generate reports using the specialized agents. **Do not wait for ObjectStore data** — launch analyzers on core artifacts immediately.

1. **Performance Report** (`log-analyzer`): Hedge-fund-style stats, regime analysis, signal flow.
2. **Trade Detail Report** (`trade-analyzer`): Per-trade P&L with regime, VIX, entry/exit context.

Claude Code example:
```
Use the log-analyzer agent to analyze docs/audits/logs/stage12.33-IC/
Use the trade-analyzer agent to analyze the V12.33-IC 2024 trades in stage12.33-IC/
```

Both agents read logs + trades.csv and produce markdown reports saved to the same stage folder.

## Related Scripts

- `scripts/sync_to_lean.sh` (sync helper — DO NOT use directly for IC, use manual cp from worktree)
- `scripts/minify_workspace.py` (standard minify)
- `scripts/ultra_minify.py` (size reduction)
- `scripts/validate_lean_minified.py` (telemetry/syntax guard)
- `scripts/qc_pull_backtest.py` (post-run core artifact pull; use `--skip-observability`)
- `scripts/qc_push_individual.py` (fallback for 413 push failures)
- `scripts/qc_research_objectstore_loader.py` (canonical ObjectStore loader template for QC Research notebooks)

## Backtest Naming Convention

```
V12.33-IC-FullYear2022
V12.33-IC-FullYear2024
V12.33-IC-Smoke-JulSep2023
```

Always include `IC` in the name to distinguish from main branch backtests.

## Quick Reference

| Item | Value |
|------|-------|
| IC Worktree | `.claude/worktrees/iron-condor/` |
| IC Branch | `feature/va/iron-condor` |
| QC Project | `AlphaNextGen` (ID: 27678023) |
| Lean Workspace | `~/Documents/Trading Github/lean-workspace/AlphaNextGen` |
| Venv | `~/Documents/Trading Github/alpha-nextgen-v2-private/venv/` |
| Python | 3.11 (via venv) |
| Lean CLI | 1.0.221 (system Python 3.14) |
| Max File Size | 256 KB per .py |
| Logs Directory | `docs/audits/logs/` (inside IC worktree) |
| Max Backtest Log | 5 MB |
| Backtest Nodes | 2x B4-12 (4 cores, 12 GB RAM) |
