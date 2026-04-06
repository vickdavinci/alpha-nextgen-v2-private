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
./scripts/qc_backtest.sh "V12.23.1-FullYear2024" --open
./scripts/qc_backtest.sh "V12.23.1-Smoke-JulSep2017" --open
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
   ```

   Then create these 3 files in `STAGE_FOLDER`:

   **a) `objectstore_loader_{VER_SLUG}_{BACKTEST_YEAR}.py`**
   - Read `scripts/qc_research_objectstore_loader.py`, replace docstring with run metadata, set `RUN_NAME` and `BACKTEST_YEAR`.

   **b) `{RUN_NAME}_OBJECTSTORE_CROSSCHECK.md`**
   - Create with backtest ID, expected ObjectStore keys (using `SANITIZED_NAME`), validation checklist, paste marker.

   **c) `{RUN_NAME}_OBJECTSTORE_RESEARCH_RUNBOOK.md`**
   - Create with run metadata, expected keys, QC Research setup steps, standard post-load RCA script.

   See the agent definition (`.claude/agents/backtest-workflow.md`) for full templates, variable resolution details, and reference examples.

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

See the agent definition (`.claude/agents/backtest-workflow.md`) for full scaffolding templates, naming conventions, and the standard RCA script.

| File | Purpose | How to Create |
|------|---------|---------------|
| `objectstore_loader_<ver>_<year>.py` | Pre-configured QC Research notebook script | Read `scripts/qc_research_objectstore_loader.py`, set `RUN_NAME` and `BACKTEST_YEAR`, customize docstring with run metadata |
| `<RUN_NAME>_OBJECTSTORE_CROSSCHECK.md` | Empty file where user pastes notebook output | Create with header block containing backtest ID, run name, expected ObjectStore keys, version-specific validation checklist, and a `PASTE NOTEBOOK OUTPUT BELOW THIS LINE:` marker |
| `<RUN_NAME>_OBJECTSTORE_RESEARCH_RUNBOOK.md` | Step-by-step instructions for this specific run | Create with confirmed run metadata, expected keys, QC Research setup steps, post-load RCA script (Cell 2), **delta analysis script (Cell 3)** comparing against baseline, cross-reference section, artifact tables, and version-specific validation checks |

**Runbook completeness requirement:** Every runbook must include a **baseline cross-reference** (prior run for the same year), a **code change delta table**, and a **Cell 3 delta analysis script** that probes version-specific features. The agent discovers the baseline by searching prior stage folders for the same backtest year. See the agent definition (`.claude/agents/backtest-workflow.md`) for the full runbook template with all 14 sections.

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
2. Canonical loader template: `scripts/qc_research_objectstore_loader.py`
3. Reference stage: `docs/audits/logs/stage12.30/` (loader, crosscheck, runbook)

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
