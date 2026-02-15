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
3. Pull logs/orders/trades for audit via existing pull tooling.
4. Report findings with file/line evidence.

## Related Scripts

- `scripts/qc_backtest.sh` (primary)
- `scripts/sync_to_lean.sh` (sync helper; supports `--minify --validate --push`)
- `scripts/validate_lean_minified.py` (telemetry/syntax guard)
- `scripts/ultra_minify.py` (size reduction)
