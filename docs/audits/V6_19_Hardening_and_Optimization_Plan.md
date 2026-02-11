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

## O8. Protective-put transition ladder (shock lifecycle control)

1. Implement one shock-state ladder across options modes, using existing VIX ladder state:
2. `L1` (elevated shock start): allow one `PROTECTIVE_PUTS` cycle.
3. `L2` (stress with volatility rollover): stop repeated long-put rebuys and prefer defined-risk put-writing (`BULL_PUT_CREDIT`) at reduced size.
4. `L3` (panic): block immediate put-writing until rollover confirmation, then allow only reduced-size defined-risk credits.
5. Add `shock_id` memory so the system does not keep re-buying protective puts within the same shock episode unless volatility re-accelerates.
6. If volatility re-accelerates during L2/L3, invalidate credit posture and revert to defense.

Acceptance:

1. Protective puts are not repeatedly re-entered during volatility decline in the same shock episode.
2. Post-spike transitions show measurable shift from long-put repetition toward controlled credit participation.
3. Tail-risk controls remain active (no increase in assignment/margin incidents).

## O9. VASS regime-mix bias correction (bull-spread lean in chop/bear)

1. Add explicit regime-aware spread mix constraints:
2. In chop/bear windows, cap repeated `BULL_CALL_DEBIT` recycling.
3. Require minimum bearish/neutral strategy attempt ratio before allowing additional bullish debit attempts.
4. Keep this logic inside options engine routing; do not add decision duplication in `main.py`.

Acceptance:

1. In chop/bear runs, VASS no longer over-concentrates in bullish debit structures.
2. Strategy mix becomes regime-consistent without reducing safety gates.

## O10. Credit spread liquidity unblock validation/tuning

1. Add explicit validation of credit spread quality filters in current data regime.
2. Recalibrate credit-path thresholds only as needed to avoid accidental full-path suppression:
3. minimum OI, max spread percent, and long-leg quality bounds.
4. Keep changes bounded and risk-aware; avoid opening illiquid tails.

Acceptance:

1. Credit-path `CREDIT_ENTRY_VALIDATION_FAILED` is no longer dominated by liquidity filter hard-fail.
2. Credit entries appear in eligible high-IV windows with acceptable fill quality.
3. No degradation in slippage/invalid-order behavior.

## O11. Regime-aware DTE de-risk ladder for spreads

1. Keep terminal force-close protection at DTE=1.
2. Add earlier staged de-risking in volatile/choppy regimes:
3. partial de-risk at earlier DTE threshold (for example DTE 7), full de-risk at tighter threshold (for example DTE 5) when stress persists.
4. Preserve longer hold in stable risk-on conditions where trend remains favorable.

Acceptance:

1. Fewer long-duration adverse spread holds through corrections.
2. Reduced tail-loss concentration from near-expiry or prolonged decaying holds.
3. No material loss of profitable trend captures in bull windows.

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
8. O9 VASS regime-mix bias correction.
9. O10 credit spread liquidity unblock validation/tuning.
10. O11 regime-aware DTE de-risk ladder.

## Phase C: Isolated High-Risk Rollout

1. O8 protective-put transition ladder is implemented and validated in isolation.
2. Gate with feature flag (`ENABLE_O8_SHOCK_LADDER` style) and dedicated logs:
3. shock level transitions, `shock_id`, rollover detection, posture switches, and invalidation events.
4. Promote O8 only after isolated pass across stress/chop windows.

Gate to proceed:

1. No regression in technical safety metrics.
2. Conversion and strategy-mix metrics improve together.

---

## 7) Backtest Matrix for V6.19 Validation

Run order:

1. 2017 Jul-Oct quick sanity run (early bull regression guard).
2. 2018 Sep-Dec (choppy stress fast fail).
3. 2015 Jul-Oct (shock/crash robustness).
4. 2021 Dec-2022 Feb (bear transition).
5. 2017 Jul-Oct full rerun (final bull confirmation).

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
6. Protective-put lifecycle transitions from `L1` defense to `L2/L3` controlled premium-capture behavior when volatility rolls over.

Cross-regime:

1. Bull window retains positive participation quality.
2. Bear/chop windows show improved risk-adjusted outcomes versus V6.18 baselines.

Performance:

1. V6.19 should improve risk-adjusted P&L versus V6.18 baselines in at least 3 of 4 core windows.
2. Stress/chop drawdown should reduce by at least 20% versus corresponding V6.18 baseline windows.
3. No promotion if process metrics improve while tail-loss concentration worsens.

---

## 9) Deliverables for V6.19

1. Code changes implementing H1-H5 and O1-O11.
2. Updated `OPTIONS_ENGINE_MASTER_AUDIT.md` with per-run scorecards.
3. Updated optimization playbook with validated parameter deltas.
4. One consolidated V6.19 audit report with pass/fail against success criteria.
5. Size-impact note confirming decision logic stayed engine-local and avoided material `main.py` growth.
6. Isolated O8 rollout report (feature-flag run logs + pass/fail) before merged activation.

---

## 10) Final Operating Principle for V6.19

V6.19 will optimize only after reliability is proven.  
No optimization change is accepted unless execution safety and lifecycle observability remain green.

---

## 11) Patch Wave Applied (2026-02-11, Pre-Next Backtest)

These items are now implemented in code and moved from proposal to validation queue.

### Technical Hardening Applied

1. Spread DTE reliability hardening:
2. Exit loop now recomputes spread DTE from live expiry before de-risk logic (`T-37`), to prevent stale-state hold-through behavior.
3. Credit direction propagation fix:
4. Credit spread entry path now receives explicit resolver direction in router->engine call.

### Optimization Controls Applied

1. VASS win-rate decouple (`O-20`):
2. Added soft-shutoff controls so VASS can scale down instead of hard-blocking when win-rate gate degrades (`VASS_WIN_RATE_HARD_BLOCK=False`, `VASS_WIN_RATE_SHUTOFF_SCALE`).
3. Bear/chop call concentration controls (`O-21`):
4. Added VIX stress and acceleration-driven BULL_CALL block/size controls (`BULL_CALL_STRESS_*`, `BULL_CALL_EARLY_STRESS_*`).
5. Added intraday `DEBIT_MOMENTUM` regime blocks in caution/elevated/worsening regimes.
6. Choppy participation consistency:
7. Applied choppy size reduction to single-leg intraday flow, not only spreads.

### What Remains Pending Validation

1. Confirm `WIN_RATE_GATE_BLOCK` is no longer dominant in VASS rejections.
2. Confirm bear/chop runs show reduced call-loss concentration and healthier spread mix.
3. Confirm DTE-based spread de-risk exits trigger on time in stress windows.
4. Confirm margin-related runtime rejects do not regress while participation improves.

---

## 12) V6.19 Validation Results (2026-02-11)

### 12.1 Dec 2021 - Feb 2022 Backtest (stage6.19)

Run inputs:
- `docs/audits/logs/stage6.19/V6_19_Dec2021_Feb2022_logs.txt`
- `docs/audits/logs/stage6.19/V6_19_Dec2021_Feb2022_orders.csv`
- `docs/audits/logs/stage6.19/V6_19_Dec2021_Feb2022_trades.csv`

**Result: NO IMPROVEMENT over V6.18 baseline.**

| Metric | V6.18 Baseline | V6.19 Actual | Status |
|--------|----------------|--------------|--------|
| Trades | 126 | 127 | Same |
| Net P&L | -$16,050 | -$16,050 | Same |
| Win Rate | 40.5% | 40.5% | Same |
| INTRADAY_RESULT | 47 | 47 | Same |
| VASS_ENTRY | 31 | 31 | Same |
| VASS_REJECTION | 450 | 450 | Same |
| WIN_RATE_GATE_BLOCK | 402 | **402** | ❌ NOT FIXED |
| HAS_SPREAD_POSITION | 110 | 110 | Same |
| Margin Errors | 4-5 | 5 | Same |
| Conversion Rate | 13.4% | 13.4% | ❌ Target was ≥25% |

### 12.2 Monthly P&L Breakdown

| Month | V6.19 P&L |
|-------|-----------|
| Dec 2021 | -$9,374 |
| Jan 2022 | -$7,009 |
| Feb 2022 | +$333 |
| **Total** | **-$16,050** |

### 12.3 E_* Rejection Code Breakdown

| Code | Count | % |
|------|-------|---|
| E_CALL_GATE_STRESS | 129 | 45% |
| E_INTRADAY_TRADE_LIMIT | 63 | 22% |
| E_CALL_GATE_MA20 | 51 | 18% |
| Other | 45 | 15% |

### 12.4 Assessment

**O1 (WIN_RATE_GATE decouple) did NOT take effect:**
- `WIN_RATE_GATE_BLOCK=402` still dominates VASS rejections
- Config shows `VASS_WIN_RATE_HARD_BLOCK` may still be True or logic path not reached

**Possible causes:**
1. Config flag not changed in backtest run
2. Code path not reached due to upstream rejection
3. Soft-shutoff scale still blocking (scale=0.0 during shutoff)

### 12.5 Next Steps

1. Verify `VASS_WIN_RATE_HARD_BLOCK=False` is set in config for next run
2. Add explicit log when soft-shutoff scale is applied vs hard block
3. Re-run Dec 2021-Feb 2022 with verified config
4. If still failing, trace code path to find why O1 fix is not reached

---

## 13) Cross-Period P&L Summary (All V6.18 Runs)

| Period | Net P&L | Conversion Rate | Dominant Blocker | Status |
|--------|---------|-----------------|------------------|--------|
| 2017 Jul-Oct | +$5,160 | ~51% | E_CALL_GATE_MA20 | ✅ Profitable |
| 2015 Jul-Oct | -$19,121 | ~44% | VASS churn | ❌ Loss |
| 2018 Sep-Dec | -$23,293 | 7.6% | HAS_SPREAD_POSITION (603) | ❌ Loss |
| 2021 Dec-2022 Feb | -$16,050 | 13.4% | WIN_RATE_GATE_BLOCK (402) | ❌ Loss |

**Pattern:** Algorithm only profitable in low-VIX bull markets (2017). All stress/chop/bear periods show losses.

**Root cause:** VASS stuck in BULL_CALL_DEBIT concentration; credit spreads blocked by liquidity filters and WIN_RATE_GATE.
