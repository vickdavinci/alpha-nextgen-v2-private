# Algorithm Audit Protocol - Pre-Backtest (V12.21)

- Version: `V12.21`
- Last updated: `2026-02-28`
- Applies to: options-first stack (`MICRO`, `ITM`, `VASS`) and shared router/execution/plumbing paths

## Purpose
This protocol is the mandatory pre-backtest audit gate for the current codebase.
It is designed to catch execution-plumbing defects that can create false alpha, false loss, or corrupted RCA before starting a full-year backtest.

This is not a strategy-ideation checklist. It is a runtime-truth and accounting-integrity checklist.

## Scope
Primary code surfaces:

- `main.py`
- `main_options_mixin.py`
- `main_orders_mixin.py`
- `main_intraday_close_mixin.py`
- `main_market_close_mixin.py`
- `main_observability_mixin.py`
- `engines/satellite/options_engine.py`
- `engines/satellite/options_pending_guard.py`
- `engines/satellite/options_position_manager.py`
- `engines/satellite/options_exit_evaluator.py`
- `engines/satellite/micro_entry_engine.py`
- `engines/satellite/itm_horizon_engine.py`
- `engines/satellite/vass_entry_engine.py`
- `portfolio/portfolio_router.py`
- `execution/oco_manager.py`
- `utils/daily_summary_logger.py`
- `scripts/generate_run_reports.py`
- `scripts/gen_trade_detail_report.py`
- `config.py`

## Required Output Artifacts
For each audited run, generate and keep in stage folder:

- `{RUN_NAME}_REPORT.md`
- `{RUN_NAME}_SIGNAL_FLOW_REPORT.md`
- `{RUN_NAME}_TRADE_DETAIL_REPORT.md`
- `{RUN_NAME}_OBJECTSTORE_CROSSCHECK.md` (or equivalent ObjectStore validation output)

## Non-Negotiable Invariants

### 1) Order Lifecycle Integrity
- Every entry must end in exactly one terminal close path.
- Filled entries must get OCO protection or explicit tracked exception.
- Partial fills must not leave position unprotected.
- Exit intent must be idempotent:
  - retry when live quantity remains
  - skip when already flat
- No close order can invert side/quantity from live holdings.
- Spread-close attribution must resolve by spread identity first, symbol fallback last.

### 2) Lane Separation Integrity (`MICRO`, `ITM`, `VASS`)
- Lane ownership must stay independent in runtime state and telemetry.
- Unknown lane must not silently collapse into `MICRO` for retry/cooldown state.
- `RISK` option exits must remain lane-aware or lane-neutral, not hard-forced to wrong engine.
- Shared caps must not create hidden cross-engine mis-tagging.

### 3) State Persistence / Recovery
- Pending entry/exit locks clear only under valid orphan criteria.
- Pending locks must not be cleared while matching entry order is still open.
- Restore path must preserve critical options state (pending, cooldowns, loss breakers, hold policy).
- Ghost cleanup must be conservative and non-destructive by default.

### 4) Router / Execution Coherency
- Router is single order authority.
- Close/protective intents are not blocked by min trade value checks.
- Preclear/close-intent races are bounded and observable.
- Margin reservation/release cannot leak across spread lifecycle.

### 5) Telemetry and RCA Completeness
- `signal_id`/`trace_id` propagation must survive submit/fill/reject paths.
- Rejections must include normalized source tags and stage.
- Exit PnL attribution must include deterministic reason path.
- Daily summaries must reset daily counters correctly (no cumulative drift unless intentionally cumulative).
- Log-budget suppression must preserve RCA checkpoints/summaries.

### 6) Report Tooling Correctness
- ITM and MICRO must remain separate in generated reports.
- Unknown tags must not be silently coerced into the wrong engine bucket.
- Trade-detail and signal-flow outputs must align with runtime tags.

### 7) ObjectStore Integrity
- Required observability channels must be present and parseable:
  - `regime_decisions`
  - `regime_timeline`
  - `signal_lifecycle`
  - `router_rejections`
  - `order_lifecycle`
- Backtest init cleanup must remove base keys plus shard manifest/parts.

## V12.21 Critical Regression Gates
These checks specifically guard known V12.15-V12.21 drift classes.

### Gate A: Trade-detail ITM vs MICRO split
- Verify `scripts/gen_trade_detail_report.py` does not aggregate ITM into MICRO summary buckets.
- Expected: separate ITM section and totals in generated report.

### Gate B: Unknown-lane option risk exits
- Verify unknown-lane option `RISK` exits are lane-neutral (`OPT_UNKNOWN` / `OPT:RISK_EXIT`) unless metadata explicitly identifies `VASS`.
- Reject silent default to `MICRO` or `VASS` on unknown lane.

### Gate C: OCO failure budget behavior
- Verify repeated OCO submit failures trigger cooldown.
- Verify cooldown expiry re-opens attempts (no all-day lockout).

### Gate D: Unknown lane cooldown/retry isolation
- Verify unknown lane does not mutate `MICRO` retry/cooldown buckets.

### Gate E: Strategy bucket mapping
- Verify `DEBIT_FADE` and `INTRADAY_DEBIT_FADE` map to `MICRO` bucket, not `OTHER`.

## Pre-Backtest Execution Steps

### Step 0: Environment and baseline
```bash
source venv/bin/activate
python --version
git rev-parse --short HEAD
git branch --show-current
```

### Step 1: Compile guards
```bash
python -m py_compile main.py \
  main_options_mixin.py \
  main_orders_mixin.py \
  portfolio/portfolio_router.py \
  execution/oco_manager.py \
  engines/satellite/options_engine.py
```

### Step 2: Architecture compliance guards
```bash
pytest tests/test_architecture_boundaries.py -q
```

### Step 3: Plumbing regression pack
```bash
pytest tests/test_portfolio_router.py tests/test_oco_manager.py tests/test_plumbing_regressions.py -q
pytest tests/test_options_engine.py -k "IntradayRetryIsolation or IntradayEngineBucketMapping" -q
```

### Step 4: Report tooling compile checks
```bash
python -m py_compile scripts/generate_run_reports.py scripts/gen_trade_detail_report.py
```

### Step 5: Static grep checks for known anti-patterns
```bash
# ITM should not be merged into MICRO in trade-detail report
rg -n "_tag" scripts/gen_trade_detail_report.py

# Unknown-lane option RISK path must be lane-neutral/explicit
rg -n "OPT_UNKNOWN|RISK_EXIT|OPT_MICRO|OPT_VASS" portfolio/portfolio_router.py

# OCO fallback should not use epoch literal
rg -n "1970, 1, 1" execution/oco_manager.py

# DEBIT_FADE should map into MICRO bucket
rg -n "DEBIT_FADE|_engine_bucket_from_strategy" main_options_mixin.py
```

### Step 6: Optional smoke run before full-year
Use a short-window backtest if runtime has large plumbing changes.

### Step 7: Full backtest workflow (after pre-checks pass)
Run canonical workflow from `docs/guides/backtest-workflow.md` and complete artifact pull.

## Mandatory Runtime Validation (Post Backtest)
A pre-backtest audit is not complete until runtime artifacts validate the same invariants.

### Required runtime checks in stage folder
- `*_logs.txt`, `*_trades.csv`, `*_orders.csv`, `*_overview.txt` present.
- ObjectStore crosscheck confirms all five observability channels loaded.
- Engine-tag distribution in logs/orders matches report-tool distribution.
- No high-severity recurring plumbing codes (examples):
  - `R_CLOSE_NO_LIVE_HOLDING` (race/stale intent)
  - `R_EXIT_PRECLEAR_PENDING` (exit contention)
  - repeated OCO submit failure storms
  - spread quantity mismatch warnings

## Audit Severity Model
- `P0`: breaks PnL truth/execution integrity directly (NO-GO).
- `P1`: high-probability intermittent skew/live risk (NO-GO unless waived with explicit reason).
- `P2`: observability/maintainability risk (GO possible with tracked debt).

## GO / NO-GO Rules

### NO-GO if any of the below is true
- Any open `P0` finding.
- Any unresolved lane-separation misattribution in runtime or reporting.
- OCO protection can be skipped or stuck without deterministic recovery.
- ObjectStore crosscheck missing required channels.
- Runtime artifacts missing for the current version run.

### GO only if all below are true
- Core compile + compliance + plumbing tests pass.
- V12.21 regression gates A-E pass.
- Report outputs preserve ITM/MICRO/VASS separation.
- Runtime artifact set is complete and consistent with source-of-truth (`trades.csv` + ObjectStore).

## Single Prompt for Audit Agents (Copy/Paste)
Use this prompt when spawning `log-analyzer` or `trade-analyzer` style audit agents.

```
Audit this run for pre-backtest execution integrity (not strategy ideation).
Focus on order lifecycle, lane separation, state recovery, telemetry completeness,
report correctness, and ObjectStore consistency.

Hard requirements:
1) Detect any path where ITM, MICRO, and VASS attribution is mixed.
2) Detect stale close-intent and preclear contention behaviors.
3) Detect spread close attribution errors (identity vs symbol fallback).
4) Verify OCO failure budget/cooldown behavior and recovery.
5) Verify daily diagnostics are daily-reset where expected.
6) Verify report outputs match runtime tags and do not merge ITM into MICRO.
7) Cross-check logs with ObjectStore observability channels.

Output format:
A) GO/NO-GO verdict (3 lines)
B) Findings first (P0/P1/P2) with path:line evidence
C) Invariant scorecard (Pass/Partial/Fail)
D) Backtest skew risk summary (false-profit / hidden-loss / missing-attribution)
E) Ordered minimal patch queue
```

## Change Log (This protocol)
- Rewritten for `V12.21` from archived `V10.10` protocol.
- Added explicit lane-separation and reporting correctness gates.
- Added OCO cooldown reset and unknown-lane neutrality checks.
- Added post-backtest runtime validation contract tied to stage artifacts.
