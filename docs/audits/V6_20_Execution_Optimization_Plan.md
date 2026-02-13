# V6.20 Execution + Optimization Plan

Date: 2026-02-11
Scope: Options engine (Micro + VASS), execution funnel, spread lifecycle, bear/chop optimization.

## 1) Objective

- Fix execution bottlenecks without breaking resolver design.
- Improve bear and choppy performance while preserving bull participation.
- Increase useful spread participation with controlled margin risk.

## 2) Core Design Rules

- Resolver remains direction-only.
- Execution policy handles slots, assignment risk, strategy mix, and capacity.
- Capacity increase is staged, not one-shot.

## 3) Root Cause Summary (From Recent Runs)

- Correct bearish/PUT opportunities are generated but blocked downstream.
- Dominant downstream blockers:
- `HAS_SPREAD_POSITION`
- `BEAR_PUT_ASSIGNMENT_GATE`
- Residual technical regressions still observed:
- margin-related runtime rejects in stress windows
- orphan/reconcile close-path invalid attempts
- incomplete spread exit telemetry in some paths
- Call-side spread concentration still too high in bear/chop windows.

### Critical Execution Bug: Slot/Counter Model Mismatch

- Config allows multiple swing positions (`OPTIONS_MAX_SWING_POSITIONS`), but spread engine still uses a single slot (`_spread_position`).
- Hard gate `HAS_SPREAD_POSITION` blocks all additional spreads before configured limits are evaluated.
- Result: VASS participation is structurally capped at one spread even when risk budget allows more.
- Additional friction: legacy `_entry_attempted_today` and shared drop buckets hide true rejection causes.

## 4) Implementation Plan

## Phase 1: Technical Stability First

1. Validate spread lifecycle parity:
- Ensure `SPREAD: ENTRY_SIGNAL -> SPREAD: EXIT_SIGNAL -> SPREAD: EXIT` is consistent.
2. Eliminate repeated orphan/invalid close loops:
- Remove recurring `RECON_ORPHAN_OPTION` invalid close attempts.
3. Close-path margin reliability:
- Confirm no regressions in:
- `ROUTER_MARGIN_WIDTH_INVALID`
- `Insufficient buying power`
- `MARGIN_CB_SKIP`
4. Revalidate DTE de-risk path:
- Keep fresh-DTE recomputation active each exit check.

## Phase 1.5: Slot/Counter Execution Refactor (Required Before Further Tuning)

1. Replace single spread state with collection state:
- Introduce `self._spread_positions: list[SpreadPosition]`.
- Keep `self._spread_position` as temporary compatibility alias during migration only.
2. Remove hard single-slot blocker:
- Remove/retire `HAS_SPREAD_POSITION` as a universal rejection.
- Gate by real limits only:
- `OPTIONS_MAX_SWING_POSITIONS`
- `OPTIONS_MAX_TOTAL_POSITIONS`
- per-direction cap (new)
3. Add per-direction cap enforcement:
- New config: `OPTIONS_MAX_SWING_PER_DIRECTION` (stage default `2`).
- Direction keys: `BULLISH` and `BEARISH` based on spread type.
4. Migrate spread lifecycle APIs to list-safe behavior:
- `has_spread_position()` => any active spread.
- `get_spread_position()` => keep for legacy consumers (oldest active).
- Add `get_spread_positions()`, `get_open_spread_count()`, `get_open_spread_count_by_direction()`.
- Update close, reconcile, EOD, expiry-hammer, and orphan cleanup loops to iterate all active spreads.
5. Remove stale daily lock coupling:
- Retire swing-path dependency on legacy `_entry_attempted_today`.
- Keep directional cooldowns explicit and scoped to strategy + direction.
6. Add deterministic rejection codes at the point of failure:
- `R_SLOT_SWING_MAX`
- `R_SLOT_TOTAL_MAX`
- `R_SLOT_DIRECTION_MAX`
- `R_COOLDOWN_DIRECTIONAL`
- `R_MARGIN_PRECHECK`
- `R_CONTRACT_QUALITY`
7. Ensure OCO children do not consume strategy slot counters:
- Count only filled parent entries as active position occupancy.
- OCO stop/limit siblings remain order management artifacts, not capacity blockers.

## Phase 2: PUT Unblock (Primary)

1. Make assignment gate regime-aware:
- Relax `BEAR_PUT_ASSIGNMENT_GATE` in bear/choppy regimes.
- Stronger relax for defined-risk `BEAR_PUT_DEBIT` in elevated VIX.
2. Keep stricter protection where needed:
- Preserve tighter assignment checks for short-put dominant credit structures.
3. Keep panic safety:
- In true panic conditions, retain hard protection only where assignment risk is real.

## Phase 3: Capacity + Strategy Mix Control

1. Stage 1 capacity increase:
- `OPTIONS_MAX_SWING_POSITIONS`: `2 -> 3`
- per-direction cap: max `2` in same direction
2. Add posture-aware slot policy:
- Allow opposite-risk spread entry when one side is already occupied.
3. Enforce bear/chop strategy preference:
- For bearish resolved direction in weak/chop regimes:
- attempt `BEAR_PUT_DEBIT` first
- use call-side credit only as fallback when bearish put path fails quality checks
4. Keep high-IV credit path available:
- Allow credits in high IV, but do not let them dominate by default.

## Phase 3.5: Credit Path Unblock (T-21 Closure)

1. Relax credit liquidity filters to executable values:
- Reduce credit OI floor.
- Widen acceptable bid/ask spread % on credit legs.
2. Keep debit-quality filters intact and symmetric where applicable.
3. Add explicit telemetry on failed credit candidate reason histogram:
- `OI_FAIL`, `SPREAD_PCT_FAIL`, `DELTA_FAIL`, `DTE_FAIL`, `WIDTH_FAIL`.

## Phase 4: Guardrails

1. Keep existing call stress gates active.
2. Keep VASS win-rate gate as size-scaling, not hard block.
3. Protect VASS from intraday trade-budget starvation:
- Prevent intraday from consuming all useful swing capacity early in volatile sessions.
4. Add spread age stop:
- Force de-risk by DTE/age in volatile regimes before expiry week.
- Goal: avoid long-held spreads drifting to worthless expiry.

## Phase 5: Validation Sequence

1. `2018 Sep-Dec` first (choppy stress validation).
2. `2021 Dec-2022 Feb` second (bear transition validation).
3. `2017 Jul-Sep` third (bull regression guard).

## 5) Success Criteria

## Technical

1. Margin/runtime close rejects near zero.
2. No repeated orphan invalid-close loops.
3. Spread exit telemetry complete and auditable.

## Execution

1. `HAS_SPREAD_POSITION` eliminated from dominant rejection set.
2. `BEAR_PUT_ASSIGNMENT_GATE` materially reduced in bear/chop windows.
3. New router rejection taxonomy shows >90% classified (no generic catch-all dominance).
4. VASS receives usable swing capacity when directional signals exist.

## Optimization

1. PUT spread participation increases in bear/chop windows.
2. Call-loss concentration reduces in non-bull windows.
3. Drawdown improves vs previous baselines for 2018 and Dec-Feb 2022.
4. Bull regime (2017) does not materially regress.

## 6) Capacity Decision Rule

1. Run Stage 1 (`3 total`, `2 same-direction cap`) first.
2. Move to Stage 2 (`4 total`, `3 same-direction cap`) only if:
- margin stability remains acceptable
- PUT participation improves
- call concentration risk decreases
3. If stability degrades:
- stay at Stage 1 and tune mix/gates before further capacity increase.

## 7) Non-Goals (This Wave)

- No resolver rewrite.
- No major architecture refactor.
- No broad overfitting sweep in one pass.

## 8) Deliverables

1. Updated code with phased changes.
2. Updated `OPTIONS_ENGINE_MASTER_AUDIT.md` status rows per run.
3. Run-by-run comparison sheet with:
- blocker deltas
- strategy mix deltas
- margin/reconcile regression status
4. Slot/counter migration note documenting:
- removed gates
- new caps
- compatibility shims and cleanup deadline
