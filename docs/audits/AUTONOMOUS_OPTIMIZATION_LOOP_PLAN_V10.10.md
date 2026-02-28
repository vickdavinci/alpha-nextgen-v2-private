# Autonomous Optimization Loop Plan (V10.10)

## Objective
Build a repeatable autonomous workflow that:
1. Runs backtests.
2. Collects and organizes logs/reports in the same style as `stage10.9`.
3. Detects plumbing, state, order, and telemetry issues first.
4. Fixes issues in code.
5. Re-runs validation backtests.
6. Tunes only existing strategies and existing engines.
7. Stops only when plumbing is stable and profitability improves across regimes.

This plan is documentation-only and does not wire automation yet.

## Scope
In scope:
1. Plumbing fixes.
2. State and order lifecycle hardening.
3. Telemetry quality for RCA.
4. Parameter tuning for existing strategies.
5. Regime-specific calibration of existing logic.

Out of scope:
1. New strategies.
2. New engines.
3. New product expansion.
4. Live-trading deployment changes.

## Mandatory Constraint
Only existing strategies may be modified:
1. VASS existing spread strategies.
2. MICRO existing strategies (including OTM/DEBIT_FADE paths already present).
3. ITM existing horizon strategy logic already present.

No new strategy identifiers, no new strategy routing trees.

## Canonical Backtest Execution
All backtests must follow `docs/guides/backtest-workflow.md`.

Canonical command:
```bash
./scripts/qc_backtest.sh "<BACKTEST_NAME>" --open
```

Hard rules:
1. No manual sync flow.
2. No direct push as primary path.
3. No skipping minify/validate steps.
4. Fix root cause, then rerun full script.

## Current Script Capability Assessment
This section captures the current state of existing automation scripts.

### What already exists
1. `scripts/qc_backtest.sh`
Role: canonical run path (sync, minify, validate, push, launch backtest).
2. `scripts/qc_pull_backtest.py`
Role: pulls QC artifacts (overview, logs, orders, trades) for a named or ID-based backtest.
3. Existing analysis/report stack via agents:
`backtest-runner`, `log-analyzer`, `trade-analyzer`.

### Current gap to be aware of
1. `qc_backtest.sh` does not automatically pull artifacts after a run.
2. `qc_pull_backtest.py` default output is legacy `docs/audits/logs/stage4` unless `--output` is provided.
3. Continuous monitoring loop is not yet wired by script; it is currently an operational process.
4. Object Store payload export can be blocked by QC licensing on non-institutional tiers.

### Operational implication
After each run, the loop must explicitly call `qc_pull_backtest.py` with an explicit output path under stage10.10.
If Object Store pull reports permission denied, the loop must switch to QC Research notebook access for Object Store validation.

## Folder and Artifact Convention (Stage10.9 Style)
Use:
`docs/audits/logs/stage10.10/`

Per run, maintain consistent file families:
1. `<RUN_NAME>_logs.txt`
2. `<RUN_NAME>_orders.csv`
3. `<RUN_NAME>_trades.csv`
4. `<RUN_NAME>_REPORT.md` from log-analyzer
5. `<RUN_NAME>_SIGNAL_FLOW_REPORT.md` from log-analyzer
6. `<RUN_NAME>_TRADE_DETAIL_REPORT.md` from trade-analyzer
7. `<RUN_NAME>_RCA.md` optional synthesized conclusion
8. `<RUN_NAME>_FIXES_APPLIED.md` required when code changes were applied in that iteration

Optional iteration subfolders for long loops:
1. `docs/audits/logs/stage10.10/iter_01/`
2. `docs/audits/logs/stage10.10/iter_02/`

## Continuous Monitoring Workflow (Backtest/Paper-Test Style)
This section defines continuous monitoring behavior without wiring new code yet.

### Monitoring objective
1. Detect run completion quickly.
2. Pull artifacts reliably.
3. Trigger analyzers.
4. Produce diagnosis docs and next-action decisions.

### Polling cadence
1. During active run: poll every 5-10 minutes.
2. After completion detected: immediate artifact pull.
3. During analysis: poll every 2-5 minutes for completion of generated reports.

### Manual command pattern using existing scripts
List recent runs:
```bash
python scripts/qc_pull_backtest.py --list --project 27678023
```

Pull all artifacts by run name into stage10.10 iteration folder:
```bash
python scripts/qc_pull_backtest.py "<BACKTEST_NAME>" --all \
  --project 27678023 \
  --output "docs/audits/logs/stage10.10/iter_XX"
```

Pull by explicit backtest ID:
```bash
python scripts/qc_pull_backtest.py --id "<BACKTEST_ID>" --all \
  --project 27678023 \
  --output "docs/audits/logs/stage10.10/iter_XX"
```

### Monitoring outputs per iteration
Required:
1. `*_logs.txt`
2. `*_orders.csv`
3. `*_trades.csv`
4. `*_REPORT.md`
5. `*_SIGNAL_FLOW_REPORT.md`
6. `*_TRADE_DETAIL_REPORT.md`
7. `*_FIXES_APPLIED.md` (if code changed)
8. `*_OBJECTSTORE_CROSSCHECK.md` when Object Store observability validation is needed.

Optional:
1. `*_overview.txt`
2. `*_RCA.md`

### Monitoring alerts / stop conditions
Alert immediately if:
1. Runtime/build error.
2. Missing any of logs/orders/trades files after pull.
3. Report generation incomplete.
4. New P0 plumbing issue introduced.

Stop iteration if:
1. Gate A fails after a supposed plumbing fix.
2. Two consecutive iterations show no improvement and no new actionable finding.
3. Budget constraints are reached.

## Versioning and Promotion Policy
Opinion: your proposed versioning control is correct and should be mandatory.

### Version bump rule
Move to a new major working version (example `V10.10` -> `V10.11`) only when:
1. A major fix set is merged, and
2. At least 3 backtests are complete across different regimes, and
3. Plumbing Gate A is passing on those runs.

Recommended stronger promotion threshold:
1. 3 regime-diverse smoke runs, and
2. 1 full-year validation run before locking the version.

### Patch version rule
Use patch increments (`V10.10.1`, `V10.10.2`) for:
1. Focused plumbing fixes.
2. Telemetry/reporting fixes.
3. Minor threshold tuning without architecture change.

### Version freeze rule
Once a version is declared for baseline comparison:
1. No mixed strategy + plumbing changes in one commit.
2. Only critical bug fixes allowed until comparison cycle ends.

## Documentation Deliverables (Mandatory)

### A) Iteration-level fix capture
Every iteration folder must include:
1. `<RUN_NAME>_FIXES_APPLIED.md`

Minimum schema:
1. Issue ID
2. Root cause
3. Files changed
4. Commit hash
5. Expected impact
6. Validation evidence (report lines/metrics)
7. Status (`PASS`/`PARTIAL`/`FAIL`)

### B) Version-level change capture
Every version folder must include:
1. `<VERSION>_CHANGELOG.md`

Minimum sections:
1. Plumbing changes
2. State/order lifecycle changes
3. Telemetry changes
4. Strategy tuning changes
5. Known risks carried forward
6. Rollback notes

### C) Acceptance gates record
Per version add:
1. `<VERSION>_ACCEPTANCE_GATES.md`

Must explicitly record `PASS`/`FAIL` for:
1. Gate A (plumbing)
2. Gate B (strategy quality)
3. Gate C (cross-regime robustness)

### D) Run manifest
Per version add:
1. `<VERSION>_RUN_MANIFEST.md`

Must include:
1. Backtest names
2. Date windows
3. Commit SHA used
4. Config deltas
5. Artifact file list

### E) Open issues register
Per version add:
1. `<VERSION>_OPEN_ISSUES.md`

Must include:
1. Severity (`P0/P1/P2`)
2. Owner
3. Repro evidence
4. Planned fix iteration
5. Closure criteria

## Agents and Responsibilities
1. `backtest-runner` (`.claude/agents/backtest-runner.md`)
Role: run backtest workflow end-to-end and store outputs.
2. `log-analyzer` (`.claude/agents/log-analyzer.md`)
Role: produce system-level report plus signal-flow report.
3. `trade-analyzer` (`.claude/agents/trade-analyzer.md`)
Role: trade-level RCA with regime/context/exit-path detail.
4. Code-fix agent (Codex/Claude)
Role: apply bounded fixes based on report evidence.

## Optimization State Machine
Each iteration must move through these states:
1. `PRECHECK`
2. `RUN_BACKTEST`
3. `COLLECT_ARTIFACTS`
4. `ANALYZE_LOGS`
5. `ANALYZE_TRADES`
6. `SCORE`
7. `FIX_PLUMBING` or `TUNE_STRATEGY`
8. `VERIFY_SMOKE`
9. `PROMOTE_OR_REJECT`

Transition rule:
1. If plumbing fails, no strategy tuning allowed in that iteration.
2. Only after plumbing passes can strategy tuning run.

## Detailed Iteration Workflow

### 1) Precheck
1. Confirm branch and commit base.
2. Confirm no unintended file edits.
3. Confirm backtest period and run name.
4. Confirm no stale state assumptions.

### 2) Backtest Run
1. Execute `qc_backtest.sh` using canonical workflow.
2. Ensure run completes and outputs are available.
3. Pull artifacts with `qc_pull_backtest.py` into stage10.10 folder using `--output`.
4. Store artifacts under stage10.10 naming.
5. If Object Store export is blocked, run QC Research Object Store cross-check and save `*_OBJECTSTORE_CROSSCHECK.md`.

### 2a) Object Store Cross-Check Path (when CLI export is blocked)
1. Confirm key presence with `lean cloud object-store list ""`.
2. If `lean cloud object-store get` is permission denied, do not treat this as run failure.
3. Open QC web IDE Research for project `27678023`.
4. Load keys via `qb.object_store.contains_key/read` in notebook.
5. Save findings under run stage folder as `*_OBJECTSTORE_CROSSCHECK.md`.

Reference example artifacts:
1. `docs/audits/logs/stage12.4/V12.4-JulSep2024-R2_OBJECTSTORE_RESEARCH_RUNBOOK.md`
2. `docs/audits/logs/stage12.4/V12.4-JulSep2024-R2_RESEARCH_OBJECTSTORE_LOADER.py`
3. `docs/audits/logs/stage12.4/V12.4-JulSep2024-R2_OBJECTSTORE_CROSSCHECK.md`

### 3) Dual Analysis
1. Run `log-analyzer` on run artifacts.
2. Enforce creation of both required reports:
`_REPORT.md` and `_SIGNAL_FLOW_REPORT.md`.
3. Run `trade-analyzer` and produce `_TRADE_DETAIL_REPORT.md`.
4. If any report missing, iteration is invalid.

### 4) Scoring and Classification
Classify findings into:
1. `P0`: Direct P&L skew or broken lifecycle.
2. `P1`: High risk, intermittent skew.
3. `P2`: Medium risk, attribution gaps.
4. `Strategy`: Valid logic but weak expectancy.

### 5) Plumbing Fix Pass
Allowed actions:
1. Order lifecycle bug fixes.
2. State persistence/restore fixes.
3. OCO/partial-fill/close idempotency fixes.
4. Reconcile and ghost handling corrections.
5. Telemetry de-spam and attribution improvements.

Not allowed in plumbing pass:
1. Strategy direction logic changes.
2. Profit/stop threshold tuning unless required for broken semantics.

### 6) Verification Pass
1. Run smoke backtest.
2. Re-run both analyzers.
3. Confirm no regression on fixed issues.
4. If regression exists, stay in plumbing loop.

### 7) Strategy Tuning Pass (Only After Plumbing Clean)
Allowed tuning surface:
1. Existing stop/target/trail thresholds.
2. Existing DTE/delta windows.
3. Existing gate thresholds and cooldown lengths.
4. Existing sizing multipliers and caps.

Not allowed:
1. New strategy types.
2. New engine mode expansions.
3. New routing hierarchies.

### 8) Cross-Regime Validation
Promote only if improvement survives required windows.

## Regime Test Matrix
Minimum cycle:
1. Smoke: Jul-Sep 2023.
2. Smoke: Jul-Sep 2017.
3. Smoke: Jan-Mar 2021.
4. Smoke: Dec 2021-Mar 2022.

Promotion cycle:
1. Full-year 2023.
2. Full-year 2024.
3. Optional full-year 2017 if runtime budget permits.

## Plumbing Invariants (Must Pass)

### Order Lifecycle
1. Every entry maps to one terminal exit.
2. Every filled entry has OCO protection or explicit justified exception.
3. Partial fills get immediate protection.
4. Close logic is idempotent with retry-if-live, skip-if-flat.
5. No zero/negative quantity into OCO create path.
6. No wrong spread removal fallback behavior.

### State Management
1. Persist and restore all new fields.
2. Restart does not leave long unprotected windows.
3. Intraday ghost clear remains guarded and non-destructive by default.
4. Removal counters are single-source and mutually exclusive.

### Telemetry
1. Signal and order attribution present (`signal_id`/`trace_id` path).
2. No blank exit tags on fills.
3. Daily funnel summary present and consistent.
4. Top reject reasons captured.
5. Exit-path P&L breakdown present.
6. Loop spam throttled and still diagnosable.

## Profitability Assessment Framework
For each engine and direction:
1. Win rate.
2. Avg win.
3. Avg loss.
4. EV per trade.
5. Hold-time distribution.
6. Regime conversion quality.

Fail conditions:
1. Positive direction alignment but negative EV due to execution leakage.
2. Dead-zone gating where both sides are blocked.
3. Large loss clusters without breaker response.

## Acceptance Gates

### Gate A: Plumbing Stability
1. No open P0 plumbing defects.
2. No repeated close retries once flat.
3. Ghost reconcile churn near zero on normal days.
4. Router rejects attributable by code/stage/trace.

### Gate B: Strategy Quality
1. EV/trade improves versus baseline in target windows.
2. Drawdown is controlled relative to baseline.
3. No single-regime overfit signature.

### Gate C: Cross-Regime Robustness
1. Improvement not isolated to one year.
2. At least neutral-to-positive trend across required windows.

Gate reporting rule:
1. A version cannot be promoted without a completed gate file:
`<VERSION>_ACCEPTANCE_GATES.md`.

## Budget and Throughput Controls
To avoid over-consuming usage and compute:
1. Cap daily full-year runs.
2. Use smoke runs for fast verification.
3. Require measurable delta before another full-year rerun.
4. Stop iteration after N no-improvement cycles.
5. Prioritize plumbing-only reruns when unresolved lifecycle bugs remain.

Budget-specific monitoring policy:
1. Prefer targeted artifact pulls (`--logs/--orders/--trades`) only when full pull is unnecessary.
2. Use `--all` only for finalized iteration snapshots.
3. Maintain one completed iteration package before starting the next run.

## Commit and Traceability Policy
1. One commit per coherent fix set.
2. Message includes version tag and category:
`V10.10: <plumbing|state|telemetry|tuning> <summary>`
3. Every commit ties to one run artifact set and report triplet.
4. Every promoted version must update:
`<VERSION>_CHANGELOG.md`, `<VERSION>_RUN_MANIFEST.md`, and `<VERSION>_OPEN_ISSUES.md`.

## Review and PR Policy
Manual review is required before any PR.

Rules:
1. No automatic PR creation by agents.
2. No automatic review-request creation by agents.
3. No auto push specifically for PR handoff.
4. Agent output at end of loop is local commits + docs + run summaries only.
5. Human performs next-morning branch review and manual PR creation.

## Minimal Promotion Checklist
Before declaring a new version:
1. 3+ regime-diverse backtests complete.
2. Required analyzers produced all reports.
3. `FIXES_APPLIED` docs exist for changed iterations.
4. Version changelog updated.
5. Acceptance gates file marked and signed off.
6. Run manifest and open issues register updated.

## Suggested Run Naming Convention
1. `V10_10_Iter01_Smoke_2023JulSep`
2. `V10_10_Iter01_Smoke_2017JulSep`
3. `V10_10_Iter01_FullYear2024`
4. `V10_10_Iter02_...`

This keeps report families and iteration history easy to compare.

## Human Review Checkpoints
Required manual review points:
1. After first plumbing-clean iteration.
2. Before enabling strategy tuning phase.
3. Before promotion to full-year campaigns.
4. Before accepting cross-regime conclusion.

## Failure and Rollback Policy
1. If iteration introduces a new P0, rollback that change set immediately.
2. Keep a stable baseline branch/tag.
3. Only cherry-pick proven fixes into progression branch.

## Expected Outcome
If followed strictly, this loop will:
1. Remove execution/plumbing skew from results.
2. Improve RCA confidence from reports.
3. Prevent strategy tuning on corrupted telemetry.
4. Move the system toward regime-robust profitability without over-engineering.
