# V6.16 Decision Funnel RCA

## Plan Status (Multi-Phase)

This RCA is a multi-phase remediation plan.

- `Phase 1 - Hardening`: `Complete`
- `Phase 2 - Decision Contract Model (E_*/R_*)`: `Not Started`
- `Phase 3 - File Extraction / Thin Facade`: `Not Started`

### Phase 1 Scope (Completed)

- Router rejection tracing with structured `R_*` reason codes.
- Trace metadata propagation (`trace_id`, source tagging) from options signals.
- Explicit `INTRADAY_ROUTER_REJECTED` / `VASS_ROUTER_REJECTED` telemetry in orchestration flow.
- CALL risk gates and execution hardening already integrated in existing options flow.

### Phase 2 Scope (Pending)

- Formal engine decision contract object with terminal states: `READY`, `BLOCKED`, `ERROR`.
- Canonical `E_*` engine rejection codes (disjoint from router `R_*`).
- Remove ambiguous catch-all drop semantics (`DROP_ENGINE_NO_SIGNAL` path).

### Phase 3 Scope (Pending)

- Extract decider/model modules:
  - `options_decision_models.py`
  - `options_intraday_decider.py`
  - `options_swing_decider.py`
- Reduce `main.py` and `options_engine.py` size/complexity; keep thin orchestration facade.

## Purpose

Document the core architectural issue in options signal processing (Micro + VASS), why it causes misleading diagnostics and weak conversion, and the implementation direction to fix it without weakening risk controls.

## Problem Statement

The current options path mixes strategy decisioning and execution gating across multiple layers:

1. `main.py` resolver approves direction.
2. `options_engine.py` performs additional entry gating and may still return `None`.
3. `portfolio_router.py` applies execution/risk checks and may skip submit.

This creates a split funnel where a signal can be "approved" but still die later for non-router reasons, while logs often report generic router rejection.

## Observed Symptoms

- High `INTRADAY_SIGNAL_APPROVED` to executed drop-off.
- Large count of `INTRADAY_SIGNAL_DROPPED` with historically opaque reason codes.
- Frequent confusion on whether drops are caused by resolver, engine gating, or router execution checks.
- Difficulty tuning strategy due to ambiguous attribution.

## Root Cause

### 1) Duplicate and staggered decision points

Direction approval and executable-entry approval are not the same event but are logged as if they are.

### 2) Mixed concerns across layers

- Strategy gates exist in both pre-router and post-resolver flow.
- Router has true execution constraints; engine has strategy constraints.
- Main thread logs collapse different failure sources into similar "drop" semantics.

### 3) Insufficient terminal-state tracing

No single trace record consistently indicates:

- engine-ready vs engine-blocked
- router-accepted vs router-skipped
- broker-submitted vs broker-failed

## Why This Matters

This is not only a logging issue. It directly impacts:

- participation rate
- confidence in backtest interpretation
- correctness of optimization decisions
- speed of diagnosing regressions

## Current Approach Risks

1. "Approved" can be a false-positive stage label.
2. Good signals are filtered twice by overlapping logic.
3. Router gets blamed for engine-side gating drops.
4. Optimization can target wrong bottleneck.

## Target Architecture (Decision Contract Model)

Unify strategy decisioning into a single engine decision contract:

- `READY`: executable order intent exists
- `BLOCKED`: engine rejected with explicit `E_*` reason
- `ERROR`: unexpected failure

Then keep router as execution-only:

- `R_*` reasons for price/margin/idempotency/risk-governor constraints
- no strategy-level reinterpretation

## Separation of Concerns

### Engine (Micro/VASS)

- regime + conviction + strategy + contract + sizing + entry-time eligibility
- returns one deterministic decision object with reason code

### Router

- order construction validation
- margin/risk/idempotency
- broker submit
- returns execution result code

### Main

- orchestration only
- no second strategy interpretation after engine decision

## Logging Contract (Mandatory)

Each decision must produce exactly one terminal status for a `trace_id`:

- `ENTRY_REJECTED_ENGINE (E_*)`
- `ENTRY_REJECTED_ROUTER (R_*)`
- `ORDER_SUBMITTED`
- `ORDER_FAILED_BROKER`

No generic catch-all drop code should remain.

## Interaction With Existing Fixes

The redesign is compatible with existing hardening:

- close idempotency guards
- OCO force-close cutoff
- symbol normalization
- close-side live-holdings enforcement
- call gates (MA20 / VIX 5d / consecutive losses)
- margin protection and combo checks

These controls remain; only the decision flow ownership is simplified.

## Implementation Constraints

- Must reduce, not increase, file-size pressure.
- Prefer extraction:
  - `options_decision_models.py`
  - `options_intraday_decider.py`
  - `options_swing_decider.py`
- Keep `options_engine.py` as thin facade.
- Keep `main.py` orchestration minimal.

## Acceptance Criteria

1. No "approved but no order" ambiguity.
2. Engine and router rejection reasons are disjoint (`E_*` vs `R_*`).
3. Approved-to-executed conversion diagnosable with exact stage attribution.
4. No regression in existing risk controls.
5. Phase 2 and Phase 3 complete with no regression against Phase 1 hardening.

## Conclusion

The core issue is architectural funnel ambiguity, not a single threshold bug.  
Fixing this requires consolidating strategy decision ownership in engine deciders and leaving router as execution/risk-only infrastructure.
