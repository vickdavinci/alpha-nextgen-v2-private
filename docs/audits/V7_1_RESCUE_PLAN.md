# V7.1 Rescue Plan: Minimal-Change Hardening + Regime-Navigation Optimization

## Summary
This plan is a minimal-code, high-impact stabilization pass focused on three outcomes:

1. Fix execution plumbing first (entry/exit churn, canceled closes, ambiguous drop reasons).
2. Reduce regime bias (VASS over-bullish in bear/choppy, Micro over-blocking/Dir=None).
3. Improve profit capture with controlled risk (fewer tail losses, faster adaptation on shocks).

This is intentionally not a large refactor. It prioritizes surgical edits in `main.py`, `engines/satellite/options_engine.py`, `portfolio/portfolio_router.py`, and `config.py`.

## Scope
In scope:
- Technical bug fixes that directly affect trade lifecycle and rejection correctness.
- Regime-bias tuning through existing decision paths and config thresholds.
- Signal funnel cleanup and telemetry normalization for RCA-quality logs.

Out of scope:
- Major architecture rewrite into new modules/files.
- New strategy families beyond current Micro/VASS framework.
- Full regime engine redesign.

## Phase 1: Execution Plumbing (P0, must-pass before optimization)

### 1) Eliminate same-bar spread open/close churn
- Add a minimum hold guard for newly opened spreads before any non-emergency exit checks.
- Emergency exits still allowed: assignment, catastrophic risk, broker rejection fallback.
- Apply in `main.py` spread exit orchestrator path (single source of truth).

### 2) Close-order reliability contract
- Enforce a single close state machine per spread:
  - `queued -> submitted -> filled|retry|escalated`.
- On cancel/reject:
  - retry with bounded attempts,
  - then escalation path (market/legged fallback with explicit reason code).
- Prevent silent "queued close but no fill" outcomes.

### 3) Exit arbitration precedence
- Define strict precedence so only one exit reason fires per cycle:
  - `assignment/emergency > hard_stop > DTE_exit > neutrality > timed_force_close`.
- Record winning reason code in log and diagnostics.

### 4) Router rejection reason hardening
- Ensure every router reject has a stable `R_*` code.
- Remove generic fallthrough where possible (`DROP_ENGINE_NO_SIGNAL` only for true unknowns).
- Keep engine and router reason namespaces disjoint (`E_*` vs `R_*`).

## Phase 2: Regime Bias Correction (P1, minimal logic change)

### 5) VASS bullish bias correction via existing matrix
- Tighten bullish spread eligibility in stress/downtrend overlays.
- Increase bearish spread eligibility when overlay stress is active.
- Keep strategy set unchanged; rebalance selector thresholds and gating only.

### 6) Capacity fairness (Micro vs VASS)
- Enforce directional/strategy slot fairness so one side cannot monopolize all swing slots.
- Keep current total caps but reserve at least one swing opportunity for opposite direction under stress.
- Preserve hard risk limits.

### 7) WIN_RATE gate decoupling
- Prevent Micro losses from fully disabling VASS if VASS local performance is acceptable.
- Gate VASS using VASS-local rolling metrics first, portfolio-wide second.
- Keep hard-block capability as config-controlled fallback.

### 8) Assignment gate calibration (not removal)
- Keep assignment safety guard, but avoid blanket blocking of all bear-put opportunities.
- Use narrower risk criteria (moneyness, DTE, liquidity) to permit valid bearish structures.

## Phase 3: Signal Funnel Efficiency (P1/P2)

### 9) Reduce non-informative `Dir=None` blocks
- Use existing conviction logic but lower over-strict neutral blockers.
- Convert selected hard blocks into reduced-size entries where risk is bounded.
- Keep call stress gates active in bear/choppy conditions.

### 10) Remove duplicate filtering
- Ensure "approved" means executable unless router risk checks fail.
- Align engine prechecks with router acceptance criteria to avoid approval/drop mismatch.

### 11) Telemetry for optimization loop
- Emit counters for:
  - generated, approved, router-rejected, executed,
  - per-reason rejects (`E_*` and `R_*`),
  - spread open/close latency and retry counts,
  - strategy-direction mix by regime bucket.

## Config/Tuning Defaults (initial V7.1 baseline)
- Keep overlay feature but lower trigger stiffness modestly (avoid overreaction and avoid no-op behavior).
- Keep spread DTE exit at current configured value, but ensure implementation enforces it.
- Keep call stress gates on; apply same regime stress awareness to VASS spread selector.
- Keep total options allocation unchanged for first validation pass (avoid confounding variables in plumbing verification).

## Important API/Interface/Type Changes
- No external API changes.
- Internal additions:
  - stable exit reason code enum usage in logs (`EXIT_*`),
  - stable engine reject codes (`E_*`) and router reject codes (`R_*`) coverage completion,
  - optional spread lifecycle state fields in memory diagnostics (no external contract impact).

## Test Plan and Scenarios

### A) Deterministic technical tests
1. Spread entered and immediately evaluated for exit in same timestamp.
- Expected: no non-emergency close within min-hold window.

2. Close order canceled twice.
- Expected: retries occur, escalation path executes, final terminal state logged.

3. DTE threshold reached.
- Expected: close attempted before expiration-hammer path.

4. Engine approved signal with valid contract.
- Expected: either order submit or explicit `R_*` rejection, never ambiguous drop.

### B) Backtest validation matrix
1. `2017 Jul-Sep` (bull sanity)
- Goal: no performance collapse, churn count near zero, healthy executed/approved ratio.

2. `2018 Q4` (choppy stress)
- Goal: reduced tail losses, lower same-direction slot monopolization.

3. `2022 Dec-Feb` (bear stress)
- Goal: lower bullish spread share, improved bearish participation, fewer catastrophic hold-to-expiry losses.

4. `2015 Jul-Sep` (crash behavior)
- Goal: protective behavior + successful transition to appropriate bearish structures without close-plumbing failures.

## Acceptance Criteria (must all pass)
- Spread same-bar churn events = 0 (except explicit emergency exits).
- Canceled-close unresolved cases = 0.
- Approved->Executed conversion improves materially vs current baseline.
- `DROP_ENGINE_NO_SIGNAL` share reduced to low single digits of total drops.
- VASS bullish share reduced in bear/choppy periods; bearish strategy participation increases.
- Tail-loss concentration reduced (top 5 losses smaller as % of total loss).
- No regression in safety gates: assignment/margin protection remains active.

## Rollout Order
1. Phase 1 only, run 2017 quick verification.
2. Add Phase 2, run 2022 + 2018.
3. Add Phase 3, run full matrix.
4. Freeze winning config set as `V7.1-baseline`.

## Assumptions and Defaults
- Existing dirty-worktree changes are preserved and not reverted.
- Current architecture remains single-file-heavy; this plan avoids major refactor.
- Profitability target is approached through reliability + regime fit, not leverage increase.
- If a tradeoff appears between higher participation and safety, safety wins in Phase 1; participation is tuned in Phase 2/3.
