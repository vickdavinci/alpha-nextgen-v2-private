# Bull Market MICRO Fix Plan (V10)

## Objective
Deliver a coding-ready, phased plan to fix MICRO profitability by deprecating `DEBIT_MOMENTUM`, making `ITM_MOMENTUM` the primary confirmation strategy, preserving divergence edge via `DEBIT_FADE` only where valid, and keeping order/state/risk plumbing stable.

This document is decision-complete and intended to be implemented directly.

---

## Non-Negotiable Constraints
- No regressions in state consistency (position lifecycle, counters, persistence).
- No regressions in order safety (OCO linkage, orphan cancellation, forced close behavior).
- No regressions in risk guardrails (kill switch, governor, direction/day caps, exposure caps).
- All deprecations must be explicit and stale code removed in the final phase.

---

## Current Problems (RCA Summary)
1. `DEBIT_MOMENTUM` is structurally weak for confirmation:
- capped upside,
- fee-heavy (higher contract count),
- low edge after slippage/fees in low-vol intraday conditions.

2. `ITM_MOMENTUM` is under-routed in bull/normal regimes:
- confirmation paths frequently choose `DEBIT_MOMENTUM` when enabled,
- ITM path mostly limited to narrow scenarios.

3. Strategy/regime mismatch:
- high-VIX environments can produce large directional moves where capped spreads under-capture upside.

4. Dead code exists:
- Grind-Up override branch references `CAUTIOUS` inside a block that only handles `CHOPPY_LOW` (unreachable path).

---

## Target Strategy Framework

## Signal-to-Strategy Mapping
- Divergence (`QQQ` and `VIX` disagree):
  - LOW/MED VIX: `DEBIT_FADE`
  - HIGH VIX: `ITM_MOMENTUM` (no spread cap)

- Confirmation (`QQQ` and `VIX` align):
  - ALL VIX levels: `ITM_MOMENTUM`

- Crisis regimes:
  - `PROTECTIVE_PUTS` / `NO_TRADE` as currently designed (with existing QQQ-direction gates retained)

- `DEBIT_MOMENTUM`:
  - Deprecated and removed from routing.

## VIX-Tier Gates
- LOW VIX (< 18): stricter min move gates to filter noise.
- MED VIX (18-25): standard move gates.
- HIGH VIX (> 25): keep move gate, but avoid spread capping for divergence/confirmation by using ITM.

## ITM DTE Rules
- ITM min DTE:
  - LOW/MED VIX: `>= 3`
  - HIGH VIX: `>= 2`
- ITM max DTE: `<= 5` (unchanged intraday envelope).

---

## Required Config Changes

Add/ensure these config keys (names must be exact):

1. Strategy enable/deprecation
- `INTRADAY_DEBIT_MOMENTUM_ENABLED = False` (final state)
- `INTRADAY_ITM_MOMENTUM_ENABLED = True` (if this flag does not exist, add it and default true)

2. VIX-tier move gates
- `MICRO_MIN_MOVE_LOW_VIX = 0.50`  # percent abs move
- `MICRO_MIN_MOVE_MED_VIX = 0.40`
- `MICRO_MIN_MOVE_HIGH_VIX = 0.40`

3. ITM DTE routing
- `MICRO_ITM_DTE_MIN_LOW_VIX = 3`
- `MICRO_ITM_DTE_MIN_MED_VIX = 3`
- `MICRO_ITM_DTE_MIN_HIGH_VIX = 2`
- `MICRO_ITM_DTE_MAX = 5`

4. ITM contract quality
- `INTRADAY_ITM_DELTA_MIN = 0.65`
- `INTRADAY_ITM_DELTA_MAX = 0.80`

5. ITM exits (intraday)
- `INTRADAY_ITM_TARGET = 0.45`  # acceptable range 0.40-0.50; choose 0.45 initial
- `INTRADAY_ITM_STOP = 0.25`
- `INTRADAY_ITM_TRAIL_TRIGGER = 0.20`
- `INTRADAY_ITM_TRAIL_PCT = 0.50`

6. Keep existing safety caps
- keep per-day and per-direction intraday caps unchanged unless separately approved.

---

## Code Changes by Area

## A) Strategy Routing (Options Engine)
Primary file: `engines/satellite/options_engine.py`

1. Replace confirmation routing behavior
- Wherever confirmation currently calls `momentum_or_disabled(...)`, route to `ITM_MOMENTUM` directly.
- Do NOT preserve fallback to `DEBIT_MOMENTUM`.

2. Divergence routing by VIX tier
- LOW/MED VIX divergence: keep `DEBIT_FADE`.
- HIGH VIX divergence: route to `ITM_MOMENTUM` (direction by QQQ sign + existing safety gates).

3. Apply VIX-tier move gates before strategy selection
- Evaluate `abs(qqq_move_pct)` against tier-specific threshold.
- Reject with explicit code when below threshold.

4. Apply ITM DTE floors in contract selection for ITM path
- Enforce min DTE by VIX tier and max DTE common cap.
- Reject with explicit reason code if violated.

5. Preserve crisis routing unchanged
- `PROTECTIVE_PUTS` and existing panic logic remain intact.

6. Remove dead Grind-Up code
- Delete unreachable branch (CAUTIOUS check under `CHOPPY_LOW`-only block) rather than leaving stale code/comments.
- If any Grind-Up behavior is still desired, reintroduce in a reachable branch only with explicit tests.

## B) Exit/Order Behavior
Files: `engines/satellite/options_engine.py`, `main.py`, `execution/oco_manager.py`

1. Keep current intraday semantics
- No overnight conversion in this phase.
- Keep force-close schedule and OCO lifecycle as current.

2. Ensure ITM exits use configured profile
- ITM positions must use ITM target/stop/trailing settings via existing per-strategy profile hooks.
- Confirm software trailing + OCO coexistence remains stable.

3. OCO tag consistency
- OCO tags must retain strategy context for RCA (`MICRO:<strategy>` context in stop/profit tags).
- Keep symbol-aware close/removal logic to avoid wrong-position clears.

## C) State Management Guarantees
Files: `main.py`, `engines/satellite/options_engine.py`, persistence manager files if touched

1. Position state transitions must remain strict
- entry registered -> active -> closing -> removed.
- no cross-symbol removal.

2. Daily counters
- Ensure deprecating `DEBIT_MOMENTUM` does not break counters by strategy/direction/day.
- Ensure intraday limits still count ITM/FADE correctly.

3. Persistence
- Backward compatibility for any touched serialized fields.
- No new required fields without defaults.

4. Ghost/orphan behavior
- Preserve current ghost clear protections and avoid reintroducing churn loops.

## D) Telemetry Requirements (Mandatory)

Add/ensure these logs/counters for every run:

1. Routing/funnel
- candidate -> approved -> contract selected -> executed -> exit.
- reject codes must be explicit and machine-joinable.

2. Strategy attribution
- every MICRO entry and exit must carry strategy label (`ITM_MOMENTUM` / `DEBIT_FADE` / `PROTECTIVE_PUTS`).
- no `UNKNOWN` for valid MICRO entries/exits.

3. DTE diagnostics
- candidate/approved/executed by DTE bucket for ITM and FADE.
- specific reject counters for ITM DTE floor violations.

4. VIX-tier diagnostics
- route counts by tier LOW/MED/HIGH.
- move-gate reject counts by tier.

5. Log budget
- throttle repetitive guards to prevent truncation.
- ensure full-period logs remain under 5MB.

---

## Phased Implementation Plan

## Phase 0 - Branch Safety and Baseline Lock
1. Create branch and freeze baseline metrics for two windows:
- Bull: Jul-Sep 2017.
- Bear: Dec-Feb 2021/2022.
2. Store baseline reports for strategy counts, funnel, P&L, fees, and log volume.

Exit criteria:
- Baseline artifacts committed under `docs/audits/...`.

## Phase 1 - Routing Refactor (No Plumbing Mutation)
1. Deprecate `DEBIT_MOMENTUM` in routing only.
2. Confirmation -> ITM; divergence -> FADE (LOW/MED) or ITM (HIGH).
3. Add VIX-tier move gates and ITM DTE floors.
4. Keep force-close/state logic unchanged.

Exit criteria:
- Compiles clean.
- No failing unit tests introduced.
- Smoke backtest executes without runtime errors.

## Phase 2 - Telemetry Hardening
1. Add missing reject codes and counters for new gates.
2. Enforce non-unknown strategy attribution in MICRO exit paths.
3. Verify OCO tag context propagation.
4. Verify log spam throttling (especially repetitive guard lines).

Exit criteria:
- Full funnel reconstructable from logs + orders/trades.
- No log truncation on short smoke windows.

## Phase 3 - Plumbing Validation Lane
1. State checks:
- no stale intraday/swing/spread states,
- no wrong-symbol removal,
- no ghost churn increase.
2. Order checks:
- no orphan OCO after exits,
- no duplicate close submits,
- forced-close path remains idempotent.
3. Risk checks:
- per-day/per-direction caps still enforced,
- kill switch/governor unaffected.

Exit criteria:
- Zero new plumbing anomalies versus baseline.

## Phase 4 - Backtest Sequence
1. Short bull smoke run.
2. Short bear smoke run.
3. Full bull year run.
4. Full bear year run.

For each run, publish:
- strategy mix,
- win/loss and expectancy by strategy,
- fee drag,
- top choke reasons,
- state/order anomaly summary,
- log size and truncation status.

## Phase 5 - Stale Code Removal (Finalization)
After validation passes:
1. Remove deprecated helpers/branches tied only to `DEBIT_MOMENTUM` fallback behavior.
2. Remove dead Grind-Up code and stale comments.
3. Update docs/matrix references to final behavior.
4. Keep migration notes for one version cycle, then delete obsolete flags/docs.

Exit criteria:
- No unreachable strategy branches.
- No deprecated path references in runtime code.

---

## Test Plan (Must Pass)

## Unit/Component
1. Routing tests for all 21 regimes with VIX-tier gates.
2. Confirmation paths return ITM (except crisis/no-trade branches).
3. High-VIX divergence routes to ITM, not FADE.
4. ITM DTE floor rejects correctly by VIX tier.
5. Strategy attribution never unknown for valid MICRO path.

## Integration
1. Entry -> OCO -> exit lifecycle works for ITM and FADE.
2. Force-close and OCO cancel interplay remains clean.
3. State restore/reset does not create stale or phantom positions.

## Regression
1. No increase in ghost reconciliation churn.
2. No increase in orphan order incidents.
3. No increase in duplicate close events.

---

## Acceptance Criteria (Go/No-Go)

Go only if all are true:
1. `DEBIT_MOMENTUM` entries = 0 by design.
2. `ITM_MOMENTUM` materially present in confirmation paths (bull and bear windows).
3. MICRO net expectancy improves versus baseline in bull window.
4. Crisis safety behavior preserved in bear window.
5. No state/order regression events.
6. Logs support complete RCA without truncation.

---

## Rollback Plan
If any critical regression appears:
1. Revert Phase 1 routing commit set.
2. Keep telemetry additions if safe (observability-only).
3. Re-run baseline windows to confirm parity restored.

---

## Deliverables
1. Updated code implementing Phases 1-3.
2. Backtest reports for Phase 4 windows.
3. Final stale-code cleanup commit (Phase 5).
4. Updated strategy matrix documentation.


---

## V10 Blocker Fixes (Applied)

### 1) DTE Routing Choke Prevention (Mandatory)
When `MICRO_DTE_ROUTING_ENABLED = True`, set explicit tier ranges to avoid implicit narrowing:

- `MICRO_DTE_LOW_VIX_MIN = 1`
- `MICRO_DTE_LOW_VIX_MAX = 5`
- `MICRO_DTE_MEDIUM_VIX_MIN = 1`
- `MICRO_DTE_MEDIUM_VIX_MAX = 5`
- `MICRO_DTE_HIGH_VIX_MIN = 1`
- `MICRO_DTE_HIGH_VIX_MAX = 5`

Then apply ITM-only floor as an overlay:
- LOW/MED ITM floor `>= 3`
- HIGH ITM floor `>= 2`

This guarantees no accidental 2-3 DTE global choke while still enforcing ITM quality.

### 2) Telemetry Log Budget Safety (Mandatory)
Any new DTE-routing diagnostics in `main.py` must be throttled.

Implementation rule:
- Emit at most once per `(strategy, vix_tier)` per 30 minutes.
- Add config: `MICRO_DTE_DIAG_LOG_INTERVAL_MIN = 30`
- Do not emit per-bar DTE-range diagnostics.
- Preserve existing concise end-of-day summaries as primary RCA source.

### 3) Phase Isolation Restored (Mandatory)
Do not implement Phases 1-3 in one code pass.

Execution sequence:
1. **Pass A**: routing-only changes (no telemetry additions).
2. **Pass B**: telemetry-only additions (no routing changes).
3. **Pass C**: plumbing validation + bugfix-only if needed.

This keeps attribution clean and prevents mixed-cause regressions.

### 4) Entry Tag Propagation Scope (Verify-Only)
`MICRO:<strategy>` entry tags already appear in orders for existing runs.

Rule:
- Treat strategy tag propagation as **verify-only**.
- No code mutation unless a concrete missing-tag path is observed in new run evidence.

### 5) High-VIX Divergence Boundary (Mandatory)
Apply HIGH-VIX divergence -> `ITM_MOMENTUM` only for non-crisis high-VIX regimes:
- Allowed: `PANIC_EASING`, `CALMING`, `ELEVATED`, `WORSENING_HIGH` (with existing direction gates)
- Excluded (must remain crisis safety): `FULL_PANIC`, `CRASH`, `VOLATILE`

Crisis regimes remain `PROTECTIVE_PUTS` / `NO_TRADE` exactly.

---

## V10 Commit Scope Rule
For the V10 implementation commit sequence:
- V10-A commit: routing + config only.
- V10-B commit: telemetry only.
- V10-C commit: plumbing fixes only (if validation finds issues).

Never mix A/B/C in one commit.
