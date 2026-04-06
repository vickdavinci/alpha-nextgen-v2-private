---
name: backtest-workflow
description: "Use this agent to run QuantConnect backtests with the full autonomous workflow: pre-flight checks, sync, push, run, self-healing retry loop for runtime errors, artifact pull, ObjectStore scaffolding creation, and analyzer report generation. This agent follows docs/guides/backtest-workflow.md exactly.\n\n<example>\nContext: User wants to run a backtest for a specific period.\nuser: \"Run a backtest for full year 2024\"\nassistant: \"I'll launch the backtest-workflow agent to run this backtest.\"\n<commentary>\nUse the backtest-workflow agent with dates 2024-01-01 to 2024-12-31.\n</commentary>\n</example>\n\n<example>\nContext: User wants to run a smoke test backtest.\nuser: \"Run a quick smoke test for Jul-Sep 2017\"\nassistant: \"I'll launch the backtest-workflow agent for the smoke test period.\"\n<commentary>\nUse the backtest-workflow agent with dates 2017-07-01 to 2017-09-30.\n</commentary>\n</example>\n\n<example>\nContext: User wants to validate recent code changes with a backtest.\nuser: \"Backtest my V12.20 changes for 2024\"\nassistant: \"I'll use the backtest-workflow agent to run the full workflow including retry/fix loop.\"\n<commentary>\nUse the backtest-workflow agent which handles the complete lifecycle including runtime error fixes.\n</commentary>\n</example>"
tools: Bash, Glob, Grep, Read, Edit, Write
model: sonnet
color: blue
---

> This file intentionally mirrors the workflow guide verbatim. Follow it exactly.

# Backtest Workflow Guide

> Mandatory for all coding agents (Claude Code, Codex, or other): use this exact flow.
> Completion rule: keep looping (diagnose -> fix -> rerun) until the backtest completes successfully and required artifacts are pulled.

## Canonical Command

```bash
./scripts/qc_backtest.sh "<BACKTEST_NAME>" --open
```

- Always use `--open` to wait for completion and stream results.
- Without `--open`, the backtest runs async and you cannot see results.

Examples:

```bash
./scripts/qc_backtest.sh "V12.16-FullYear2024" --open
./scripts/qc_backtest.sh "V12.16-Smoke-JulSep2017" --open
./scripts/qc_backtest.sh --open   # auto-generates name from git branch
```

## Mandatory Completion Contract (Self-Healing Loop)

A backtest task is complete only when all of the following are true:

### Phase 1: Backtest Execution + Core Artifacts (autonomous — no user input)

1. `./scripts/qc_backtest.sh "<BACKTEST_NAME>" --open` exits successfully.
2. QC backtest status is terminal-success (completed, not failed/cancelled/runtime-error).
3. Core artifacts are pulled with:
   `python scripts/qc_pull_backtest.py "<BACKTEST_NAME>" --all --skip-observability --project 27678023 --output "docs/audits/logs/<stage_folder>"`
4. Required core files exist in the stage folder:
   - `*_logs.txt`
   - `*_trades.csv`
   - `*_orders.csv`
   - `*_overview.txt`

### Phase 2: ObjectStore Scaffolding (autonomous — agent creates these)

5. ObjectStore scaffolding files created in the stage folder:
   - `objectstore_loader_<version>_<year>.py` (copy of canonical loader with RUN_NAME and BACKTEST_YEAR set)
   - `<RUN_NAME>_OBJECTSTORE_CROSSCHECK.md` (empty crosscheck with backtest ID, expected keys, paste marker)
   - `<RUN_NAME>_OBJECTSTORE_RESEARCH_RUNBOOK.md` (run-specific instructions with backtest ID, expected keys, RCA scripts)

### Phase 3: User Checkpoint + Analysis (requires user input)

6. **Ask the user**: "ObjectStore scaffolding is ready in `<stage_folder>`. Do you want to load the ObjectStore data via QC Research notebook now? I can proceed with log/trade analysis using the core artifacts, or wait for you to paste ObjectStore data first."
7. Launch `log-analyzer` and/or `trade-analyzer` on the core artifacts (proceed regardless of ObjectStore readiness).

If any Phase 1 step fails, the agent must:

1. Capture the exact failure message.
2. Fix root cause in code/runtime workflow.
3. Re-run the canonical workflow end-to-end.
4. Repeat until all Phase 1 conditions pass.

Do not stop at first failure. Do not ask the user to run intermediate recovery commands.

## What The Script Now Does

`./scripts/qc_backtest.sh` is the only supported path. It performs:

1. Sync from repo to `lean-workspace/AlphaNextGen` via `scripts/sync_to_lean.sh`
2. Standard minify: `scripts/minify_workspace.py`
3. Ultra minify: `scripts/ultra_minify.py --target-indent 1`
4. Post-minify validation: `scripts/validate_lean_minified.py --strict`
5. Per-file QC size guard (`*.py <= 256KB`)
6. Push to QC cloud project `AlphaNextGen`
7. Start backtest (`--open` waits for completion)

## Hard Rules

- Do **not** run manual `cp` sync steps.
- Do **not** run direct `lean cloud push` as primary workflow.
- Do **not** skip validator/minify checks.
- If script fails, fix root cause, then re-run script.

## Pre-Backtest Local Checks

```bash
source venv/bin/activate
python --version
python -m py_compile main.py engines/satellite/options_engine.py portfolio/portfolio_router.py
```

Optional but recommended:

```bash
pytest -q
```

## Failure Handling

### 1) Validation failure

- Read missing marker/compile error from validator output.
- Fix code and re-run `./scripts/qc_backtest.sh ...`.

### 2) Per-file size failure (>256KB)

- Reduce file size in offending file(s).
- Keep required telemetry markers intact.
- Re-run script.

### 3) QC push/backtest runtime error

- Fix code.
- Re-run script end-to-end (no manual partial workflow).

### 4) Runtime loop policy (mandatory)

Use this loop for all failures (validation, size guard, push, compile, runtime exceptions):

1. Run canonical command with `--open`.
2. If failure:
   - classify (`validation`, `size`, `push`, `compile`, `runtime`, `artifact-pull`)
   - apply minimal targeted fix
   - rerun canonical command
3. On backtest success, pull artifacts and verify required files.
4. If artifact pull fails, fix pull path/name/id and retry pull until successful.

This is an autonomous workflow. The agent owns recovery until completion.

## Agent Playbook

When a task says “run backtest”:

### Phase 1: Execute + Pull Core Artifacts

1. Activate venv: `source venv/bin/activate && python --version` (must be 3.11).
2. Run `./scripts/qc_backtest.sh “<name>” --open --start-date YYYY-MM-DD --end-date YYYY-MM-DD` (or `--backtest-year YYYY` when applicable).
3. If run fails, fix root cause and rerun step 2 until success.
4. Capture backtest id + status from script output.
5. Pull core artifacts (skip ObjectStore — QC blocks CLI export on this tier):
   `python scripts/qc_pull_backtest.py “<name>” --all --skip-observability --project 27678023 --output “docs/audits/logs/<stage_folder>”`
6. Verify 4 core artifacts exist in stage folder (`*_logs.txt`, `*_trades.csv`, `*_orders.csv`, `*_overview.txt`); if missing, retry pull (name then explicit id).

### Phase 2: Create ObjectStore Scaffolding

> **Gold-standard templates live in `.claude/skills/objectstore-scaffold/templates/`.**
> Always read these templates — never improvise the loader, RCA script, or delta script from scratch.

7. Resolve these variables from the completed backtest (all 3 files depend on them):

   ```
   RUN_NAME        = backtest name from step 2           (e.g. “V12.31-FullYear2024”)
   BACKTEST_ID     = backtest id captured in step 4      (e.g. “a7947d16dfb3b9d186026372f5731d7a”)
   BACKTEST_YEAR   = year extracted from date range       (e.g. 2024)
   START_DATE      = start date from step 2              (e.g. “2024-01-01”)
   END_DATE        = end date from step 2                (e.g. “2024-12-31”)
   SANITIZED_NAME  = RUN_NAME with dots replaced by _    (e.g. “V12_31-FullYear2024”)
   VER_SLUG        = version slug, lowercase dots→_      (e.g. “v12_31” from “V12.31-FullYear2024”)
   STAGE_FOLDER    = output folder from step 5           (e.g. “docs/audits/logs/stage12.31”)
   VERSION         = version tag from backtest name      (e.g. “V12.31”)
   PREV_VERSION    = decrement minor by 1                (e.g. “V12.30”)
   ```

   7a. **Discover version commits (drives all version-specific content):**
   - Run `git log --oneline --all --grep=”{VERSION}:”` to find all commits for this version.
   - Parse each commit message to extract: change description, category (ENTRY/EXIT/CONFIG/TELEMETRY/INFRA).
   - For key commits, run `git show --stat {hash}` to see which files were touched.
   - For commits touching `config.py`, grep the diff for parameter names and old/new values.
   - Map each ENTRY/EXIT/CONFIG commit to its expected observability delta:
     - New exit code → “New `{CODE}` in order_lifecycle”
     - Removed exit code → “Zero `{CODE}` exits expected”
     - Threshold change → “More/fewer `{GATE}` drops in signal_lifecycle”
     - Entry gate change → “Changed approval rate”
   - Store as `VERSION_CHANGES` list for use in runbook/crosscheck generation.

   7b. **Discover baseline run for delta comparison:**
   - Search `docs/audits/logs/` for the most recent prior stage folder with an overview file for the same `BACKTEST_YEAR` (excluding the current run).
   - Example: for `V12.32-FullYear2024`, search for `stage12.*/V12.*2024*_overview.txt` but NOT `V12.32-FullYear2024_overview.txt`.
   - If found, read the baseline overview and extract:
     ```
     BASELINE_RUN_NAME     = e.g. “V12.30-2024-Audit”
     BASELINE_BACKTEST_ID  = from overview “Backtest ID:” line
     BASELINE_STAGE_FOLDER = e.g. “docs/audits/logs/stage12.30”
     BASELINE_NET_PROFIT   = from overview “Net Profit” line
     BASELINE_SHARPE       = from overview “Sharpe Ratio” line
     BASELINE_DRAWDOWN     = from overview “Drawdown” line
     BASELINE_WIN_RATE     = from overview “Win Rate” line
     BASELINE_ORDERS       = from overview “Total Orders” line
     BASELINE_FEES         = from overview “Total Fees” line
     ```
   - If no baseline found, set `BASELINE_RUN_NAME = None` and omit delta sections from the runbook.

   Then create these 3 files in `STAGE_FOLDER`:

   **a) `objectstore_loader_{VER_SLUG}_{BACKTEST_YEAR}.py`**
   - Read the gold-standard template: `.claude/skills/objectstore-scaffold/templates/loader.py.template`
   - Replace ONLY these two placeholders:
     - `{{RUN_NAME}}` → `”{RUN_NAME}”` (the computed run name)
     - `{{BACKTEST_YEAR}}` → `{BACKTEST_YEAR}` (the integer year)
   - **Do NOT modify any other part of the loader.** The logic is identical across all versions.
   - Write to `{STAGE_FOLDER}/objectstore_loader_{VER_SLUG}_{BACKTEST_YEAR}.py`
   - **DO NOT** name the file `objectstore_loader.py` — it MUST have the versioned suffix (e.g., `objectstore_loader_v12_31_2022.py`)

   **b) `{RUN_NAME}_OBJECTSTORE_CROSSCHECK.md`**
   - Use the Crosscheck Template from the “ObjectStore Scaffolding” section below, substituting all `{...}` variables.
   - **Populate version-specific checks (7-15)** from `VERSION_CHANGES` discovered in step 7a — one check per significant commit.
   - **Populate the Code Change Summary table** from `VERSION_CHANGES` — each row is a commit with its expected observability delta.
   - **Populate the Baseline Comparison table** from step 7b baseline stats.
   - Write to `{STAGE_FOLDER}/{RUN_NAME}_OBJECTSTORE_CROSSCHECK.md`

   **c) `{RUN_NAME}_OBJECTSTORE_RESEARCH_RUNBOOK.md`**
   - Use the Runbook Template structure from the “ObjectStore Scaffolding” section below.
   - Must include ALL 14 sections: objective, run metadata, cross-reference baseline (if found), code change delta table, expected keys, why required, setup steps, Cell 2 RCA script, Cell 3 delta analysis script, validation checks (standard + version-specific), market context, artifact file table, cross-reference artifact table, local artifacts protocol.
   - **CRITICAL**: Use `.get(“key”, pd.DataFrame())` in all scripts — NEVER bracket access. Wrap all analysis blocks with `if not df.empty` guards.
   - **For the RCA script (Cell 2):** Read `.claude/skills/objectstore-scaffold/templates/rca_script.py.template` as the base. Replace the TODO stub in check #4 with actual version-specific exit code checks derived from `VERSION_CHANGES`.
   - **For the Delta script (Cell 3):** Read `.claude/skills/objectstore-scaffold/templates/delta_script.py.template` as the base. Replace all `{{PLACEHOLDERS}}` with actual values. In D2, add version-specific exit codes from `VERSION_CHANGES`. In D8, fill trade volume from overview data.
   - **Populate the delta table** from `VERSION_CHANGES` — each commit maps to a row with expected observability delta.
   - **Populate validation checks 7-15** from `VERSION_CHANGES` — one check per significant ENTRY/EXIT/CONFIG commit.
   - Write to `{STAGE_FOLDER}/{RUN_NAME}_OBJECTSTORE_RESEARCH_RUNBOOK.md`

   **Concrete example** — if backtest name is `V12.31-FullYear2024`, backtest ID is `abc123`, dates are 2024-01-01 to 2024-12-31:
   ```
   objectstore_loader_v12_31_2024.py         ← RUN_NAME=”V12.31-FullYear2024”, BACKTEST_YEAR=2024
   V12.31-FullYear2024_OBJECTSTORE_CROSSCHECK.md   ← keys use “V12_31-FullYear2024_2024”
   V12.31-FullYear2024_OBJECTSTORE_RESEARCH_RUNBOOK.md  ← backtest ID “abc123”, same keys
   ```

### Phase 3: User Checkpoint + Analysis

8. **USER CHECKPOINT** — Report to the user:
   - Confirm backtest completed successfully with backtest ID.
   - List all artifacts created (4 core + 3 scaffolding) with paths.
   - Ask: “ObjectStore scaffolding is ready. When you're ready, upload `objectstore_loader_<ver>_<year>.py` to QC Research, run it, and paste the output into `<RUN_NAME>_OBJECTSTORE_CROSSCHECK.md`. I'll now proceed with core artifact analysis.”
9. Launch `log-analyzer` and/or `trade-analyzer` agents on the core artifacts (do not wait for ObjectStore data — proceed immediately).
10. Report findings with file/line evidence.

### Required execution pattern (no early exit)

- Keep the same run context and continue recovery attempts until completion contract passes.
- For runtime exceptions during cloud execution, patch code and rerun `qc_backtest.sh --open` immediately.
- For pull ambiguity, retry by explicit backtest id using `qc_pull_backtest.py --id`.

### Claude Code

Claude Code has a dedicated **`backtest-workflow`** agent (defined in `.claude/agents/backtest-workflow.md`).
Use it via the Task tool:

```
Use the backtest-workflow agent to run a backtest for July to September 2024
```

The agent handles the full loop automatically:
1. Sync, push, run, runtime-fix retries until backtest succeeds.
2. Pull core artifacts (logs, trades, orders, overview) — skips ObjectStore.
3. Create ObjectStore scaffolding files (loader, crosscheck, runbook).
4. Report to user with artifact paths and ObjectStore instructions.
5. Launch `log-analyzer` and `trade-analyzer` on core artifacts.

`backtest-runner` is legacy and should not be used for new runs.

Claude Code also has direct access to the terminal. If you prefer manual control, run the canonical command directly via the Bash tool. Always use `--open` so the backtest blocks until complete and streams results back.

### Codex

Codex should run the same workflow using Claude agents via MCP.

One-time MCP setup (global):

```bash
codex mcp add claude-agents --env CLAUDE_CODE_ENTRYPOINT=1 -- claude mcp serve
```

Then for backtests, Codex should:

1. Run steps 1-5 directly via terminal workflow in this guide (`qc_backtest.sh --open` + `qc_pull_backtest.py --all`).
2. Use Claude `log-analyzer` and `trade-analyzer` agents via MCP for report generation only.
3. Keep outputs in the stage folder (`*_REPORT.md`, `*_SIGNAL_FLOW_REPORT.md`, `*_TRADE_DETAIL_REPORT.md`).

Do **not** use `backtest-runner` in the Codex path.

If MCP is unavailable, fallback to direct terminal workflow in this guide (`qc_backtest.sh --open` + `qc_pull_backtest.py --all`) and clearly note the fallback in the run summary.

### General (Any Agent)

- Never run `lean cloud push` directly — always go through `qc_backtest.sh`.
- Never manually `cp` files to lean-workspace — the sync script handles this.
- If the script fails, fix the root cause and re-run the full script (no partial workflows).
- Always pull artifacts to an explicit stage folder after completion.
- Log file naming: `V12_16_FullYear2024_logs.txt`, `V12_16_JulSep2017_logs.txt`.

## Related Scripts

- `scripts/qc_backtest.sh` (primary)
- `scripts/sync_to_lean.sh` (sync helper; supports `--minify --validate --push`)
- `scripts/validate_lean_minified.py` (telemetry/syntax guard)
- `scripts/ultra_minify.py` (size reduction)
- `scripts/qc_pull_backtest.py` (post-run core artifact pull; use `--skip-observability`)
- `scripts/qc_research_objectstore_loader.py` (canonical ObjectStore loader template for QC Research notebooks)

## Post-Run Artifact Pull (Required)

Always pull to an explicit stage folder. Always use `--skip-observability` (QC blocks CLI ObjectStore export on this account tier).

```bash
python scripts/qc_pull_backtest.py "<BACKTEST_NAME>" --all --skip-observability \
  --project 27678023 \
  --output "docs/audits/logs/<stage_folder>"
```

If name lookup is ambiguous, pull by explicit backtest id:

```bash
python scripts/qc_pull_backtest.py --id "<BACKTEST_ID>" --all --skip-observability \
  --project 27678023 \
  --output "docs/audits/logs/<stage_folder>"
```

### Expected Core Artifacts

After a successful pull, the stage folder should contain:

| File | Contents |
|------|----------|
| `*_logs.txt` | Full backtest log (up to 5 MB) |
| `*_trades.csv` | All fills with timestamps, P&L, fees |
| `*_orders.csv` | Order events (submitted, filled, canceled) |
| `*_overview.txt` | QC summary (return, Sharpe, drawdown, trade count) |

### ObjectStore Scaffolding (Mandatory After Artifact Pull)

After pulling the 4 core artifacts, the agent **must** create 3 ObjectStore scaffolding files in the stage folder. These enable the user to load observability data from QC Research notebooks and paste it back for enriched analysis.

**Do NOT attempt to pull ObjectStore data via CLI** — QC blocks `lean cloud object-store get` on this account tier. The scaffolding files guide the user through the manual QC Research notebook workflow.

| File | Purpose | How to Create |
|------|---------|---------------|
| `objectstore_loader_<ver>_<year>.py` | Pre-configured QC Research notebook script | Read `scripts/qc_research_objectstore_loader.py`, set `RUN_NAME` and `BACKTEST_YEAR`, customize docstring with run metadata |
| `<RUN_NAME>_OBJECTSTORE_CROSSCHECK.md` | Empty file where user pastes notebook output | Create with header block containing backtest ID, run name, expected ObjectStore keys, and a `PASTE NOTEBOOK OUTPUT BELOW THIS LINE:` marker |
| `<RUN_NAME>_OBJECTSTORE_RESEARCH_RUNBOOK.md` | Step-by-step instructions for this specific run | Create with confirmed run metadata (backtest ID, project ID, expected key names), QC Research setup steps, and the post-load RCA script |

#### Naming Convention

- **Loader filename**: `objectstore_loader_<ver>_<year>.py` where `<ver>` is the version extracted from backtest name (e.g., `v12_31` from `V12.31-FullYear2024`), `<year>` is the backtest year.
  - Example: `objectstore_loader_v12_31_2024.py`
- **Crosscheck/Runbook**: Use the full `<RUN_NAME>` prefix.
  - Example: `V12.31-FullYear2024_OBJECTSTORE_CROSSCHECK.md`

#### Sanitized Run Name

ObjectStore keys use a sanitized version of the run name: replace non-alphanumeric chars (except `-_`) with `_`.
- `V12.31-FullYear2024` → `V12_31-FullYear2024`

#### ObjectStore Loader Creation Steps

1. Read the canonical loader: `scripts/qc_research_objectstore_loader.py`
2. Replace the docstring with run-specific metadata:
   ```python
   """QuantConnect Research Object Store loader for Alpha NextGen <RUN_NAME>.

   Usage in QC Research notebook:
   1) Upload this file to the project (or paste into a notebook cell).
   2) Run the script cell.

   Run: <RUN_NAME>
   Backtest ID: <BACKTEST_ID>
   Project ID: 27678023
   Period: <START_DATE> to <END_DATE>
   """
   ```
3. Set `RUN_NAME = "<backtest_name>"` and `BACKTEST_YEAR = <year>`
4. Write to stage folder with versioned filename.

#### Expected ObjectStore Key Pattern

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

#### Crosscheck Template

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
2. VASS signal lifecycle events during stable/deterioration overlays.
3. Regime decision tuples alignment with options handoff gates.
4. Engine funnel approval rates by engine.
5. Catastrophic exit count and same-session re-entry health.
6. Trace integrity: APPROVED signal trace + order trace coverage (100% expected).

### Version-Specific Checks (Delta vs <BASELINE_RUN_NAME>)
<!-- Agent: populate from code change delta table in step 7a.
     Each check should reference the baseline value for comparison.
     Example: "8. Scan funnel unclog: R_VASS_SCAN_INTERVAL_GUARD (baseline: 2431) — expect reduction." -->
7. <check with baseline reference value and expected direction>
8. <check with baseline reference value and expected direction>
...

## Code Change Summary (vs <BASELINE_RUN_NAME>)

| # | Change | Version | Expected Delta vs Baseline |
|---|--------|---------|---------------------------|
| 1 | <change from git log> | <ver> | <expected observable delta> |
| ... | ... | ... | ... |

## Baseline Comparison (<BASELINE_RUN_NAME> vs <RUN_NAME>)

<!-- Pre-fill baseline column from the baseline overview + crosscheck output.
     V12.32 column stays TBD until user pastes ObjectStore output. -->

| Metric | <BASELINE_RUN_NAME> | <RUN_NAME> | Delta |
|--------|---------------------|------------|-------|
| Net Profit | <baseline> | <current> | TBD |
| Sharpe Ratio | <baseline> | <current> | TBD |
| Drawdown | <baseline> | <current> | TBD |
| Win Rate | <baseline> | <current> | TBD |
| Total Orders | <baseline> | TBD | TBD |
| VASS Candidates | <from baseline crosscheck or TBD> | TBD | TBD |
| VASS Approved | <from baseline crosscheck or TBD> | TBD | TBD |
| VASS Approval Rate | <from baseline crosscheck or TBD> | TBD | TBD |
| `R_VASS_SCAN_INTERVAL_GUARD` | <from baseline crosscheck or TBD> | TBD | Expect reduction |
| `R_VASS_RESOLVER_NO_TRADE` | <from baseline crosscheck or TBD> | TBD | Expect reduction |
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

**Gold-standard templates**: `.claude/skills/objectstore-scaffold/templates/` (loader, RCA script, delta script)

#### Runbook Template

The runbook must contain ALL of these sections in order. **Do NOT omit any section.** Use the reference examples to verify completeness.

1. **Objective** — What this runbook validates and the market context (bull/bear year)
2. **Confirmed Run Metadata** — Backtest name, ID, project ID, period, sanitized name, headline stats from overview
3. **Cross-Reference: Baseline Run** (if `BASELINE_RUN_NAME` found) — Baseline metadata, stats, stage folder, loader path
4. **Code Change Delta Table** (if baseline found) — Table of changes between versions with expected observable deltas
5. **Expected Object Store Keys** — 5 keys using `SANITIZED_NAME` + sharded variants
6. **Why this path is required** — QC blocks CLI export
7. **Setup and Execution steps** — Open QC project → Research → paste loader → run
8. **Post-Load RCA Script (Cell 2)** — Standard script using `.get()` with empty-check guards
9. **Delta Analysis Script (Cell 3)** (if baseline found) — Version-specific probes comparing against baseline
10. **Validation Checks to Record** — Split into "Standard Every-Run Checks" + "Version-Specific Checks (Delta vs Baseline)"
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

To populate the delta table: run `git log --oneline` between the baseline version tag and current HEAD, group commits by feature/fix, and describe the expected observable impact of each on the ObjectStore data (e.g., "fewer X rejections", "more Y approvals", "zero Z exits in time window").

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

# 1) VASS events by transition overlay
if not sig_df.empty and not tl_df.empty:
    eng = pick(sig_df, ["engine"])
    evt = pick(sig_df, ["event"])
    if eng and evt:
        vass = sig_df[sig_df[eng].astype(str).str.upper().str.contains("VASS", na=False)].copy()
        vass["event_u"] = vass[evt].astype(str).str.upper()
        joined = pd.merge_asof(
            vass.sort_values("time"),
            tl_df[["time", "transition_overlay"]].sort_values("time"),
            on="time", direction="backward", tolerance=timedelta(hours=6),
        )
        print("VASS events by overlay:")
        print(pd.crosstab(joined["transition_overlay"].fillna("NO_MATCH"), joined["event_u"]))
    else:
        print("SKIP: signal_lifecycle missing engine/event columns")
else:
    print("SKIP: signal_lifecycle or regime_timeline empty")

# 2) Catastrophic exits vs same-session re-entry
if not order_df.empty and not sig_df.empty:
    otext = row_text(order_df)
    cat_mask = otext.str.contains("VASS_TAIL_RISK_CAP|SPREAD_HARD_STOP_DURING_HOLD", na=False)
    exit_rows = order_df.loc[cat_mask, ["time"]].sort_values("time")
    eng = pick(sig_df, ["engine"])
    evt = pick(sig_df, ["event"])
    if eng and evt:
        vass = sig_df[sig_df[eng].astype(str).str.upper().str.contains("VASS", na=False)].copy()
        vass["event_u"] = vass[evt].astype(str).str.upper()
        approved = vass[vass["event_u"] == "APPROVED"][["time"]].sort_values("time")
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
    cat_mask = otext.str.contains("VASS_TAIL_RISK_CAP|SPREAD_HARD_STOP_DURING_HOLD", na=False)
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

Only include if `BASELINE_RUN_NAME` is set. This script compares the current run against the baseline. Generate it with version-specific probes based on the code changes discovered in step 7a.

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

# --- D1: VASS funnel (candidate/approved/dropped + approval rate) ---
if eng_col and evt_col and strat_col and not sig_df.empty:
    vass_df = sig_df[sig_df[eng_col].astype(str).str.upper().str.contains("VASS", na=False)]
    vass_approved = vass_df[vass_df[evt_col].astype(str).str.upper() == "APPROVED"]
    vass_dropped = vass_df[vass_df[evt_col].astype(str).str.upper() == "DROPPED"]
    vass_candidate = vass_df[vass_df[evt_col].astype(str).str.upper() == "CANDIDATE"]
    print(f"D1) VASS funnel: candidate={len(vass_candidate)} approved={len(vass_approved)} dropped={len(vass_dropped)}")
    print(f"    Approval rate: {100.0*len(vass_approved)/max(1,len(vass_candidate)):.1f}%")
    print(f"    Approved by strategy:")
    print(vass_approved[strat_col].astype(str).str.upper().value_counts())
else:
    print("D1) SKIP: signal_lifecycle missing columns")

# --- D2: Version-specific telemetry codes ---
# Agent: populate this list from git log code changes discovered in step 7a.
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

# --- D3: Exit pattern distribution ---
print(f"\nD3) Exit pattern distribution (compare vs baseline crosscheck):")
exit_patterns = [
    "PROFIT_TARGET", "STOP_LOSS", "TRAIL_STOP", "MFE_LOCK", "TAIL_RISK_CAP",
    "DTE_EXIT", "REGIME_DETERIORATION", "OVERLAY_STRESS", "HARD_STOP",
    "NEUTRALITY_EXIT", "CREDIT_STOP_2X", "CREDIT_DELTA_BREACH",
    "QQQ_INVALID", "THESIS_BREAK", "MARGIN_BUFFER",
    "BULL_CALL", "BEAR_PUT", "BEAR_CALL",
]
if not order_df.empty:
    for pat in exit_patterns:
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

# --- D5: Monthly VASS approved entries ---
print(f"\nD5) Monthly VASS approved entries:")
if eng_col and evt_col and not sig_df.empty:
    vass_df = sig_df[sig_df[eng_col].astype(str).str.upper().str.contains("VASS", na=False)]
    vass_approved = vass_df[vass_df[evt_col].astype(str).str.upper() == "APPROVED"]
    if not vass_approved.empty and "time" in vass_approved.columns:
        vass_approved = vass_approved.copy()
        vass_approved["month"] = vass_approved["time"].dt.to_period("M")
        print(vass_approved.groupby("month").size())

# --- D6: VASS dropped by direction+strategy ---
print(f"\nD6) VASS dropped by direction+strategy:")
if code_col and dir_col and strat_col and eng_col and evt_col and not sig_df.empty:
    vass_df = sig_df[sig_df[eng_col].astype(str).str.upper().str.contains("VASS", na=False)]
    vass_dropped = vass_df[vass_df[evt_col].astype(str).str.upper() == "DROPPED"]
    if not vass_dropped.empty:
        print(vass_dropped.assign(
            direction_u=vass_dropped[dir_col].str.upper(),
            strategy_u=vass_dropped[strat_col].str.upper()
        ).groupby(["direction_u", "strategy_u"]).size().sort_values(ascending=False).head(20))
    else:
        print("    No dropped entries")

# --- D7: Recovery derisk exits (should be 0 — disabled V12.30) ---
print(f"\nD7) Recovery derisk exits (expect 0 — disabled V12.30):")
if not order_df.empty:
    recovery_exits = order_text.str.contains("RECOVERY_DERISK|CREDIT_RECOVERY_DERISK", na=False).sum()
    print(f"    RECOVERY_DERISK exits: {recovery_exits}")

# --- D8: Insufficient-BP rejections ---
print(f"\nD8) Insufficient-BP rejections:")
if not router_df.empty and "code" in router_df.columns:
    bp_mask = router_df["code"].astype(str).str.upper().str.contains("INSUFFICIENT_BP|BUYING_POWER", na=False)
    print(f"    Total: {bp_mask.sum()}")
else:
    print("    SKIP: router_rejections empty or missing 'code'")

# Agent: Add D9+ probes for version-specific features discovered in git log.
# Examples of version-specific probes:
# - BR-08 open-delay: profit-target exits in 09:30-09:34 (expect 0 after V12.31)
# - QQQ invalidation exits (threshold change)
# - TAIL_RISK_CAP in THETA_FIRST (gating change)
# - Neutral fallback / SOFT_NEUTRAL fires
# - Any new telemetry code counts

print(f"\n=== Summary: Compare above counts against <BASELINE_RUN_NAME> crosscheck ===")
print(f"Baseline: <BASELINE_NET_PROFIT> | <BASELINE_ORDERS> orders | <BASELINE_DRAWDOWN> DD | <BASELINE_FEES> fees")
```

**Section 10: Validation Checks — always split into two sub-sections:**

```markdown
### Standard Every-Run Checks
1. `transition_overlay` flip count and distribution.
2. VASS signal lifecycle events during stable/deterioration overlays.
3. Regime decision tuples alignment.
4. Engine funnel approval rates.
5. Catastrophic exit count and same-session re-entry.
6. Trace integrity (100% expected).

### Version-Specific Checks (Delta vs <BASELINE_RUN_NAME>)
7. <check derived from code change 1>
8. <check derived from code change 2>
...
```

Populate version-specific checks from the code change delta table. Each check should state the expected value/direction and what to compare against in the baseline crosscheck.

**Section 12: Artifact Files in Stage Folder:**

List ALL files the agent created or expects in the stage folder (4 core + 3 scaffolding + any reports). Use a markdown table with File and Description columns.

**Section 13: Cross-Reference Baseline Artifacts:**

List key baseline files with their relative paths from the repo root.

**Reference examples:**
- Loader: `docs/audits/logs/stage12.30/objectstore_loader_v12_30_2024.py`
- Crosscheck: `docs/audits/logs/stage12.30/V12.30-2024-Audit_OBJECTSTORE_CROSSCHECK.md`
- Runbook: `docs/audits/logs/stage12.30/V12.30-2024-Audit_OBJECTSTORE_RESEARCH_RUNBOOK.md`
- **Best reference for completeness**: `docs/audits/logs/stage12.32/V12.32-FullYear2024_OBJECTSTORE_RESEARCH_RUNBOOK.md`

### Post-Pull Analysis Pipeline

After pulling core artifacts and creating ObjectStore scaffolding, generate reports using the specialized agents. **Do not wait for ObjectStore data** — launch analyzers on core artifacts immediately.

1. **Performance Report** (`log-analyzer`): Hedge-fund-style stats, regime analysis, signal flow.
2. **Trade Detail Report** (`trade-analyzer`): Per-trade P&L with regime, VIX, entry/exit context.

Claude Code example:
```
Use the log-analyzer agent to analyze docs/audits/logs/stage12.16/
Use the trade-analyzer agent to analyze the V12.16 2024 trades in stage12.16/
```

Both agents read logs + trades.csv and produce markdown reports saved to the same stage folder.

## ObjectStore Data Access (Manual via QC Research)

**CLI export is blocked** on this account tier (`lean cloud object-store get` fails with institutional-only restriction). The agent does NOT attempt ObjectStore pull.

Instead, the agent creates scaffolding files that guide the user through the manual QC Research notebook workflow:

1. Agent creates `objectstore_loader_<ver>_<year>.py` (pre-configured loader script).
2. User uploads the loader to QC web IDE → Research notebook → runs it.
3. User pastes output into `<RUN_NAME>_OBJECTSTORE_CROSSCHECK.md`.
4. If deeper analysis is needed, user follows `<RUN_NAME>_OBJECTSTORE_RESEARCH_RUNBOOK.md`.

Concrete reference implementation:
1. Canonical guide: `docs/guides/objectstore-research-workflow.md`
2. Gold-standard loader template: `.claude/skills/objectstore-scaffold/templates/loader.py.template`
3. Gold-standard RCA script template: `.claude/skills/objectstore-scaffold/templates/rca_script.py.template`
4. Gold-standard delta script template: `.claude/skills/objectstore-scaffold/templates/delta_script.py.template`
5. Reference stage (gold standard): `docs/audits/logs/stage12.32/` (loader, crosscheck, runbook)
6. Skill instructions: `.claude/skills/objectstore-scaffold/SKILL.md` (full workflow description)

## Environment Setup (All Agents)

Before any backtest workflow, ensure the environment is correct:

```bash
source venv/bin/activate
python --version   # Must be 3.11.x (NOT 3.14)
```

System Python is 3.14, but the project requires 3.11 via the venv. Lean CLI is installed globally on system Python (`pip3 install lean==1.0.221`). Do **not** upgrade lean — newer versions break on pydantic v2.

## Lean Workspace Sync Sanity Check

If validator passes in repo root but fails in `lean-workspace`, deployment files are stale.
Before running cloud backtests, confirm lean-workspace mirror is current:

```bash
python scripts/validate_lean_minified.py --root ../lean-workspace --strict
```

If it fails on missing mixin/module files, run canonical sync flow again:

```bash
./scripts/qc_backtest.sh "<BACKTEST_NAME>" --open
```

## Quick Reference

| Item | Value |
|------|-------|
| QC Cloud Project | `AlphaNextGen` (ID: 27678023) |
| Lean Workspace | `~/Documents/Trading Github/lean-workspace/AlphaNextGen` |
| Logs Directory | `docs/audits/logs/` |
| Max File Size | 256 KB per `.py` file |
| Max Backtest Log | 5 MB |
| Backtest Nodes | 2x B4-12 (4 cores, 12 GB RAM) |
| Python Version | 3.11 (venv) |
| Lean CLI Version | 1.0.221 (pinned) |
