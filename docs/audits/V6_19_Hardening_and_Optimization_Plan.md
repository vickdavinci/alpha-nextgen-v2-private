# V6.19 Hardening and Optimization Plan

Date: 2026-02-11  
Baseline version: V6.18  
Objective: Move from diagnostic-heavy V6.18 to production-hardened, regime-compatible V6.19 with measurable execution reliability and strategy robustness.

---

## 1) Why V6.19 Is Needed

V6.18 proved the system is debuggable, but not yet reliable or regime-robust:

1. Execution funnel is still broken in stress/chop windows.
2. Margin path regressions still appear in some runs.
3. VASS is frequently blocked by meta-gates/occupancy rather than market-quality checks.
4. Call-side loss concentration remains too high outside clean bull regimes.

This plan is a controlled change list from V6.18 to V6.19, not a feature expansion.

---

## 2) V6.18 Evidence Snapshot (Inputs for V6.19)

From audited runs:

1. 2017 Jul-Oct (bull): positive net, but participation suppressed by MA20 and upstream gating.
2. 2015 Jul-Oct (shock/chop): large losses, spread churn (`NEUTRALITY_EXIT`) and bearish spread suppression.
3. 2018 Sep-Dec (choppy stress): `APPROVED->RESULT` collapsed to 7.6%; call losses dominated.
4. 2021 Dec-2022 Feb (bear transition): PUT side positive, CALL side heavily negative; VASS blocked by `WIN_RATE_GATE_BLOCK`; margin errors reappeared.

Primary open bug clusters:

1. Technical: `T-18`, `T-27`, `T-28`, `T-36`.
2. Optimization: `O-13`, `O-19`, `O-20`, `O-21`, `O-22`, `O-23`.

---

## 3) V6.19 Scope and Non-Scope

## In Scope

1. Technical reliability hardening for execution, margin, and lifecycle telemetry.
2. Minimal optimization changes targeting chokepoints already evidenced in logs.
3. Regime-fit improvements for bull, bear, and choppy windows without architecture rewrite.

## Out of Scope

1. Large architecture refactor (multi-file decision-contract rewrite).
2. New data sources or event-driven macro feature expansion.
3. Broad parameter overfitting sweep in a single cycle.

---

## 4) V6.19 Change List: Technical Hardening

Each item below is a planned V6.19 change from V6.18.

## H1. Close-path margin determinism (`T-18`, `T-27`)

1. Standardize close-order margin handling for all reduce-risk paths.
2. Ensure OCO sibling cancellation and close replacement sequence cannot create temporary margin spikes.
3. Eliminate remaining `MARGIN_CB_SKIP` ambiguity by explicit close-intent tagging and path-specific guard.

Acceptance:

1. `ROUTER_MARGIN_WIDTH_INVALID=0`.
2. `Order Error: Insufficient buying power=0` for close/replacement flow.

## H2. Approved->execution conversion reliability (`T-28`)

1. Remove duplicate post-approval blocks that are effectively re-validating the same signal.
2. Keep a single authoritative rejection point per check type.
3. Preserve explicit `E_*` codes for every drop.

Acceptance:

1. `APPROVED->RESULT >= 25%` in first validation pass.
2. No increase in invalid/canceled safety failures.

## H3. Lifecycle telemetry completion (`T-36`)

1. Guarantee `INTRADAY_RESULT` emission for every closed intraday lifecycle.
2. Guarantee `SPREAD: EXIT` and reason for every spread close path.
3. Add reconciliation counters comparing entries, closes, and terminal states.

Acceptance:

1. `INTRADAY_RESULT` count reconciles with closed intraday positions.
2. `SPREAD: EXIT` reason coverage aligns with spread closes in orders/trades.

## H4. OCO/force-close race hardening (`T-27`, `T-34`, `T-35`)

1. Keep idempotent close lock as single source of truth.
2. Prevent OCO recovery/restore during force-close windows.
3. Ensure close-side quantity derives only from live holdings snapshot.

Acceptance:

1. No quantity amplification events.
2. No stale OCO-triggered replacement during force-close window.

## H5. Regression guard instrumentation

1. Add run-level summary counters for key failure families:
2. `margin_reject_count`, `approved_count`, `result_count`, `vass_block_count`, `lifecycle_gap_count`.

Acceptance:

1. End-of-run summary available for direct audit ingestion.

---

## 5) V6.19 Change List: Optimization

Optimization changes are constrained to existing logic families.

## O1. VASS win-rate coupling fix (`O-20`)

1. Decouple VASS from hard global `WIN_RATE_GATE_BLOCK`.
2. Convert hard shutoff to risk scaling for VASS:
3. score penalty, reduced size, or higher-quality threshold instead of full block.

Acceptance:

1. `WIN_RATE_GATE_BLOCK` is not dominant VASS rejection reason.
2. VASS entries appear in eligible bearish/high-IV windows.

## O2. Spread occupancy choke reduction (`O-22`)

1. Reduce persistent `HAS_SPREAD_POSITION` starvation by staged de-risking and re-entry policy.
2. Add position-age aware unlock behavior in choppy regimes.

Acceptance:

1. `HAS_SPREAD_POSITION` share of VASS reject reasons drops materially.
2. No increase in overlap risk beyond configured caps.

## O3. Bear-put assignment gate recalibration (`O-13`)

1. Recalibrate `BEAR_PUT_ASSIGNMENT_GATE` to preserve safety but allow valid bearish spreads.
2. Keep hard block only for truly high assignment-risk structures.

Acceptance:

1. `BEAR_PUT_ASSIGNMENT_GATE` rejection count decreases.
2. Bear-put participation improves without assignment incidents.

## O4. Neutrality churn reduction (`O-19`)

1. Replace immediate hard `NEUTRALITY_EXIT` with staged de-risking where risk is controlled.
2. Add short anti-ping-pong cooldown after neutrality-triggered exits.

Acceptance:

1. `NEUTRALITY_EXIT` frequency declines.
2. Fee drag and rapid re-entry loops decline.

## O5. Call concentration control in non-bull windows (`O-21`)

1. Keep existing stress CALL gates.
2. Add regime-aware spread mix rebalance to prevent repeated `BULL_CALL_DEBIT` concentration when stress persists.

Acceptance:

1. Call-side tail-loss concentration reduces.
2. PUT/credit participation improves during bear/chop stress.

## O6. Combined-size floor recalibration (`O-23`)

1. Re-tune `E_INTRADAY_COMBINED_SIZE_MIN` boundary behavior in chop.
2. Prevent good setups from being dropped near threshold while keeping tiny/noise trades blocked.

Acceptance:

1. `E_INTRADAY_COMBINED_SIZE_MIN` drops decrease.
2. Trade quality metrics do not degrade.

## O7. Intraday trade-limit starvation control

1. Rebalance intraday trade-budget usage to avoid consuming all slots early.
2. Reserve controlled capacity for later high-quality signals in volatile sessions.

Acceptance:

1. `E_INTRADAY_TRADE_LIMIT` drops decline.
2. Late-session quality signals are not systematically starved.

---

## 6) Implementation Sequence (Strict Order)

## Phase A: Technical First

1. H1 close-path margin determinism.
2. H4 OCO/force-close race hardening.
3. H2 conversion reliability cleanup.
4. H3 lifecycle telemetry completion.
5. H5 regression summary instrumentation.

Gate to proceed:

1. No margin regressions in validation runs.
2. Rejection and lifecycle metrics are fully auditable.

## Phase B: Optimization Second

1. O1 VASS win-rate coupling fix.
2. O2 spread occupancy choke reduction.
3. O3 assignment-gate recalibration.
4. O4 neutrality churn reduction.
5. O5 call concentration control.
6. O6 combined-size boundary tuning.
7. O7 trade-limit starvation control.

Gate to proceed:

1. No regression in technical safety metrics.
2. Conversion and strategy-mix metrics improve together.

---

## 7) Backtest Matrix for V6.19 Validation

Run order:

1. 2018 Sep-Dec (choppy stress fast fail).
2. 2015 Jul-Oct (shock/crash robustness).
3. 2021 Dec-2022 Feb (bear transition).
4. 2017 Jul-Oct (bull regression guard).

Promotion rule:

1. Do not promote if any critical technical safety gate fails.
2. Do not promote if performance gain comes only from increased risk without conversion/lifecycle improvement.

---

## 8) V6.19 Success Criteria

Technical:

1. `ROUTER_MARGIN_WIDTH_INVALID=0`.
2. `Insufficient buying power` close/replacement rejects = `0`.
3. Full lifecycle telemetry coverage for intraday and spreads.

Execution:

1. `APPROVED->RESULT >= 25%` initial target, then `>=30%`.
2. No generic drop-code fallback dominance.

Optimization:

1. `WIN_RATE_GATE_BLOCK` no longer dominant VASS blocker.
2. `HAS_SPREAD_POSITION` dominance reduced.
3. `BEAR_PUT_ASSIGNMENT_GATE` rejections reduced with no assignment incidents.
4. `NEUTRALITY_EXIT` churn reduced.
5. CALL loss concentration reduced in non-bull windows.

Cross-regime:

1. Bull window retains positive participation quality.
2. Bear/chop windows show improved risk-adjusted outcomes versus V6.18 baselines.

---

## 9) Deliverables for V6.19

1. Code changes implementing H1-H5 and O1-O7.
2. Updated `OPTIONS_ENGINE_MASTER_AUDIT.md` with per-run scorecards.
3. Updated optimization playbook with validated parameter deltas.
4. One consolidated V6.19 audit report with pass/fail against success criteria.

---

## 10) Final Operating Principle for V6.19

V6.19 will optimize only after reliability is proven.  
No optimization change is accepted unless execution safety and lifecycle observability remain green.

