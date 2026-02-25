# Backtest Workflow Guide

> Mandatory for all coding agents (Codex/Claude/other): use this exact flow.

## Canonical Command

```bash
./scripts/qc_backtest.sh "<BACKTEST_NAME>" --open
```

Examples:

```bash
./scripts/qc_backtest.sh "V10.2-FullYear2023" --open
./scripts/qc_backtest.sh "V10.2-Smoke-JulSep2017"
./scripts/qc_backtest.sh --open
```

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

## Agent Playbook

When a task says “run backtest”:

1. Use `./scripts/qc_backtest.sh "<name>" --open`.
2. Capture backtest id + status.
3. Pull artifacts via `python scripts/qc_pull_backtest.py "<name>" --all --project 27678023 --output "<stage_folder>"`.
4. Report findings with file/line evidence.

## Related Scripts

- `scripts/qc_backtest.sh` (primary)
- `scripts/sync_to_lean.sh` (sync helper; supports `--minify --validate --push`)
- `scripts/validate_lean_minified.py` (telemetry/syntax guard)
- `scripts/ultra_minify.py` (size reduction)
- `scripts/qc_pull_backtest.py` (post-run artifacts and Object Store pull attempt)

## Post-Run Artifact Pull (Required)

Always pull to an explicit stage folder. Do not rely on default output.

```bash
python scripts/qc_pull_backtest.py "<BACKTEST_NAME>" --all \
  --project 27678023 \
  --output "docs/audits/logs/<stage_folder>"
```

If name lookup is ambiguous, pull by explicit backtest id:

```bash
python scripts/qc_pull_backtest.py --id "<BACKTEST_ID>" --all \
  --project 27678023 \
  --output "docs/audits/logs/<stage_folder>"
```

## Object Store Retrieval Reality (QC Account-Tier Aware)

`qc_pull_backtest.py` attempts to pull structured observability CSVs from Object Store.
On this account tier, payload export via CLI can be blocked by QC licensing.

Expected CLI behaviors:
1. `lean cloud object-store list ""` can confirm keys exist.
2. `lean cloud object-store get <key>` can fail with institutional-only export restriction.

When CLI export is blocked, use QC Cloud Research notebook access:
1. Open project in QC web IDE and launch Research.
2. Read Object Store keys with `qb.object_store.contains_key(...)` / `qb.object_store.read(...)`.
3. Store cross-check output in the same stage folder as backtest reports.

Concrete reference implementation:
1. Canonical guide: `docs/guides/objectstore-research-workflow.md`
2. Canonical loader: `scripts/qc_research_objectstore_loader.py`
3. Historical stage example: `docs/audits/logs/stage12.4/V12.4-JulSep2024-R2_OBJECTSTORE_CROSSCHECK.md`

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
