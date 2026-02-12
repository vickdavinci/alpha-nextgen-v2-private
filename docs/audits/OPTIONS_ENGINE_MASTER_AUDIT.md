# Options Engine Master Audit (Single Source of Truth)

**Purpose:** Central, continuously updated bug registry and audit history for the Options Engine (Micro + VASS).  
**Rule:** All fixes and validations must be recorded here before/after backtests.

---

## Backtest Success Criteria (Use Before Reading Bug Summary)

**Principle:** Net P&L alone is not an acceptance test. Evaluate in order:  
**Technical Safety -> Execution Integrity -> Strategy Behavior -> Regime Fit -> P&L**

| ID | Gate | Success Criteria | Threshold / Rule | Status |
|:--:|------|------------------|------------------|:------:|
| SC-01 | Technical Safety | Runtime stability | No crash that halts trading loop | ⬜ |
| SC-02 | Technical Safety | No position amplification | Forced close never increases net position | ⬜ |
| SC-03 | Technical Safety | Orphan containment | No persistent orphan positions after reconcile window | ⬜ |
| SC-04 | Technical Safety | Exit margin reliability | Exit margin rejection rate = **0%** | ⬜ |
| SC-05 | Technical Safety | Expiry discipline | Intraday DTE<=2 overnight escapes = **0** | ⬜ |
| SC-06 | Execution Integrity | Intraday conversion | Approved->Executed >= **60%** | ⬜ |
| SC-07 | Execution Integrity | Drop reason quality | `INTRADAY_SIGNAL_DROPPED` coded >= **95%** actionable `E_*` / `R_*` / `DROP_*` (no generic fallback dominance) | ⬜ |
| SC-08 | Execution Integrity | OCO coverage | OCO created for open intraday positions >= **95%** | ⬜ |
| SC-09 | Execution Integrity | OCO race prevention | No OCO recovery during force-close cutoff window | ⬜ |
| SC-10 | Execution Integrity | Spread close integrity | Combo close metadata correct; no stuck `is_closing` lock | ⬜ |
| SC-11 | Strategy Behavior | Tail-loss control | No catastrophic avoidable single-trade loss from control failure | ⬜ |
| SC-12 | Strategy Behavior | Stress protection firing | VIX ladder and deterioration/spike exits log when thresholds are crossed | ⬜ |
| SC-13 | Strategy Behavior | VASS participation | Non-zero entries where matrix allows; rejects dominated by market constraints | ⬜ |
| SC-14 | Regime Fit | Bear handling | CALL gating active in stress; PUT participation not starved | ⬜ |
| SC-15 | Regime Fit | Bull handling | CALL participation remains healthy (no over-blocking) | ⬜ |
| SC-16 | Regime Fit | Choppy handling | Reduced churn/re-entry behavior vs prior version | ⬜ |
| SC-17 | Performance | Risk-adjusted improvement | Drawdown/tail-loss/technical rejects improve vs prior baseline | ⬜ |
| SC-18 | Performance | P&L interpretation | P&L is valid only if SC-01..SC-16 pass | ⬜ |
| SC-19 | Diagnostics Integrity | Backtest rejection visibility | Engine/router rejection reasons visible in backtest logs for RCA | ⬜ |
| SC-20 | Strategy Behavior | VASS unblock effectiveness | `WIN_RATE_GATE_BLOCK` is not the dominant VASS rejection reason | ⬜ |
| SC-21 | Technical Safety | Spread DTE de-risk reliability | Stress/chop spreads do not bypass DTE-driven de-risk/exit checks due to stale state | ⬜ |

| Run Grade | Rule |
|:--:|------|
| Green (Promote) | SC-01..SC-16 pass and SC-17 not degraded materially |
| Yellow (Iterate) | SC-01..SC-10 pass, but SC-11..SC-17 partially fail |
| Red (Block) | Any failure in SC-01..SC-05, or repeated SC-06..SC-10 failure |

### Win Rate Targets (Optimization Phase)

| Regime Window | Current Baseline | Target Win Rate | Notes |
|---|---:|---:|---|
| Bull trend (2017 Jul-Oct) | ~44.7% | `>=46%` while preserving positive net | Keep participation without opening floodgates |
| Bear/stress (2022 Dec-Feb + Jul-Oct) | ~33% | `>=38%` | Improve spread mix and reduce hard-block starvation |
| Shock/chop (2015 Jul-Oct) | ~30.7% | `>=35%` | Reduce churn and improve defensive trade quality |
| Cross-run aggregate | low-30s to mid-40s | `>=40%` | Must pass safety gates first |

### Win Rate Guardrails

- Win-rate improvements are valid only if technical safety gates remain green (`SC-01..SC-05`).
- Do not promote a run if win rate rises but tail-loss concentration or technical reject rates regress (`SC-17`).
- Evaluate win rate together with funnel quality (`SC-06`) and VASS participation (`SC-13`), not in isolation.

### Latest Run Quick Scorecard (V6.18: Jul-Oct 2017 + Jul-Oct 2015)

| Gate | Snapshot | Status |
|------|----------|:------:|
| SC-06 Intraday conversion | 2017: Approved=162, Selected=83 (**51.2%**); 2015: Approved=301, Selected=132 (**43.9%**) | ❌ |
| SC-07 Drop reason quality | 2017 drops coded (`E_CALL_GATE_MA20`/`E_INTRADAY_TIME_WINDOW`) 69/69; 2015 coded 89/89 | ✅ |
| SC-12 Stress protection firing visibility | 2015 shows stress gates firing (`E_CALL_GATE_STRESS=41`, `E_CALL_GATE_VIX5D=5`) | ✅ |
| SC-19 Backtest rejection visibility | Engine rejection reasons now visible in backtest logs (`E_*`, `ValidationFail=*`) | ✅ |

### V6.18 Dual-Run Snapshot (2017 + 2015)

| Run | Trades | Win Rate | Gross P&L | Fees | Net P&L | Key Technical Readout |
|---|---:|---:|---:|---:|---:|---|
| 2017 Jul-Oct | 38 | 44.7% | +5,160 | 2,870.4 | +2,289.6 | Drops now coded; router rejects not dominant |
| 2015 Jul-Oct | 127 | 30.7% | -19,121 | 4,651.75 | -23,772.75 | Stress gates firing; VASS churn and assignment-gated bearish participation persist |

| Bug ID | V6.18 Status Update |
|---|---|
| T-28 | Improved materially (81.8% severe drop state cleared), still below conversion acceptance threshold |
| T-33 | Validated fixed for rejection reason opacity (`E_*` now visible) |
| T-36 | Partially fixed: rejection visibility restored, result/exit telemetry coverage still incomplete |
| O-13 | Still open; bear-put assignment gate remains a meaningful suppressor in 2015 run |
| O-19 | Confirmed open; `NEUTRALITY_EXIT` is dominant spread exit reason in 2015 run |

### Latest Run Quick Scorecard (V6.18: Jul-Oct 2022)

| Gate | Snapshot | Status |
|------|----------|:------:|
| SC-04 Exit margin reliability | `Insufficient buying power=0`, `ROUTER_MARGIN_WIDTH_INVALID=0`, `MARGIN_CB_SKIP=0` | ✅ |
| SC-06 Intraday conversion | `APPROVED=551`, `RESULT=95` (**17.2%**) | ❌ |
| SC-07 Drop reason quality | `INTRADAY_SIGNAL_DROPPED=446`, coded `E_*` = 446, `R_*` = 0, `DROP_*` = 0 | ✅ |
| SC-12 Stress protection firing | `PREMARKET_VIX_LADDER=103`, `PREMARKET_LADDER_CALL_BLOCK=6`, `E_CALL_GATE_STRESS=219` | ✅ |
| SC-13 VASS participation | `VASS_ENTRY=4`, `VASS_REJECTION=674`, `ValidationFail=WIN_RATE_GATE_BLOCK:610` | ❌ |
| SC-14 Bear handling (call control) | `INTRADAY approved CALL=288`, dropped CALL=272 (stress/MA/loss gates active) | 🟡 |
| SC-19 Diagnostics integrity | Engine rejects visible with actionable codes; no generic `DROP_ENGINE_NO_SIGNAL` fallback | ✅ |

### Latest Run Quick Scorecard (V6.18: Dec 2021-Feb 2022)

| Gate | Snapshot | Status |
|------|----------|:------:|
| SC-04 Exit margin reliability | `Order Error=4` (`Insufficient buying power`) + `ROUTER_MARGIN_WIDTH_INVALID=1` + `MARGIN_CB_SKIP=2` | ❌ |
| SC-06 Intraday conversion | `APPROVED=352`, `RESULT=47` (**13.4%**) | ❌ |
| SC-07 Drop reason quality | `INTRADAY_SIGNAL_DROPPED=288`, all coded actionable `E_*` (`E_CALL_GATE_STRESS`, `E_CALL_GATE_MA20`, `E_INTRADAY_TRADE_LIMIT`, `E_INTRADAY_CAP_TOO_SMALL`, `E_INTRADAY_TIME_WINDOW`) | ✅ |
| SC-12 Stress protection firing | `PREMARKET_VIX_LADDER=50` (`L2_STRESS=22`), stress/MA20 CALL blocks fire; `E_CALL_GATE_VIX5D=0`, `E_CALL_GATE_LOSS_STREAK=0` | 🟡 |
| SC-13 VASS participation | `VASS_ENTRY=31`, `VASS_REJECTION=450`; rejects dominated by `WIN_RATE_GATE_BLOCK=402` and `HAS_SPREAD_POSITION=110` | ❌ |
| SC-14 Bear handling (call control) | Direction generation is PUT-leaning (`PUT=468`, `CALL=282`) but executed P&L remains CALL-heavy negative (`CALL=-17,680`, `PUT=+1,630`) | ❌ |
| SC-19 Diagnostics integrity | Engine-side rejection causes visible; no generic no-signal fallback dominance | ✅ |

### V6.18 Dec 2021-Feb 2022 Snapshot

| Metric | Value |
|---|---:|
| Trades | 126 |
| Win Rate | 40.5% (51W / 75L) |
| Net P&L | -16,050 |
| Calls / Puts (executed trades) | 66 / 60 |
| Call P&L / Put P&L | -17,680 / +1,630 |
| MICRO_UPDATE | 1,800 |
| Dir=NONE / CALL / PUT | 1,050 / 282 / 468 |
| INTRADAY_APPROVED / DROPPED / RESULT | 352 / 288 / 47 |
| Top intraday drop codes | `E_CALL_GATE_STRESS=129`, `E_INTRADAY_TRADE_LIMIT=63`, `E_CALL_GATE_MA20=51`, `E_INTRADAY_CAP_TOO_SMALL=33` |
| Top NO_TRADE blocks | `CONFIRMATION_FAIL`, `REGIME_NOT_TRADEABLE`, panic-regime neutral blocks, `VIX_STABLE_LOW_CONVICTION` |
| VASS validation fails (dominant) | `WIN_RATE_GATE_BLOCK=402`, `HAS_SPREAD_POSITION=110`, `TRADE_LIMIT_BLOCK=42` |

| Bug ID | V6.18 Dec-Feb Status Update |
|---|---|
| T-18 | Regression observed: one `ROUTER_MARGIN_WIDTH_INVALID` plus margin-related skips/errors in this run |
| T-27 | Regression observed: margin rejects still occur at runtime (`Insufficient buying power=4`) |
| T-28 | Still open and severe (`APPROVED->RESULT=13.4%`) |
| T-33 | Still validated (drop reasons remain explicit/actionable) |
| T-36 | Still open: rejection reason visibility is good, but lifecycle/result telemetry coverage remains incomplete |
| O-20 | Still open and confirmed: VASS heavily throttled by win-rate shutoff |

### Latest Run Quick Scorecard (V6.19: Dec 2021-Feb 2022)

| Gate | Snapshot | Status |
|------|----------|:------:|
| SC-04 Exit margin reliability | `Order Error=4` (`Insufficient buying power`) + `ROUTER_MARGIN_WIDTH_INVALID=1` + `MARGIN_CB_SKIP=2` | ❌ |
| SC-06 Intraday conversion | `CANDIDATE=352`, `APPROVED=64`, `RESULT=47` (`APPROVED->RESULT=73.4%`) | 🟡 |
| SC-07 Drop reason quality | `INTRADAY_SIGNAL_DROPPED=288`, all drops coded actionable `E_*` (no `DROP_ENGINE_NO_SIGNAL`) | ✅ |
| SC-12 Stress protection firing | `PREMARKET_VIX_LADDER=50`, `PREMARKET_LADDER_CALL_BLOCK=6`, stress/MA20 call gates firing (`E_CALL_GATE_STRESS=129`, `E_CALL_GATE_MA20=51`) | ✅ |
| SC-13 VASS participation | `VASS_ENTRY=31`, `VASS_REJECTION=450`, `VASS_SKIPPED=124`; rejects dominated by `ValidationFail=WIN_RATE_GATE_BLOCK=402` | ❌ |
| SC-14 Bear handling (call control) | Direction generation PUT-leaning (`Dir=PUT=554`, `Dir=CALL=484`) but realized P&L remains CALL-heavy negative (`CALL=-17,680`, `PUT=+1,630`) | ❌ |
| SC-19 Diagnostics integrity | Engine rejection reasons visible; daily `OPTIONS_DIAG_SUMMARY` absent in this run | 🟡 |

### V6.19 Dec 2021-Feb 2022 Snapshot

| Metric | Value |
|---|---:|
| Trades | 126 |
| Win Rate | 40.5% (51W / 75L) |
| Net P&L | -16,050 |
| Calls / Puts (executed trades) | 66 / 60 |
| Call P&L / Put P&L | -17,680 / +1,630 |
| MICRO_UPDATE | 1,800 |
| Dir=NONE / CALL / PUT | 1,050 / 484 / 554 |
| INTRADAY_CANDIDATE / APPROVED / DROPPED / RESULT | 352 / 64 / 288 / 47 |
| Top intraday drop codes | `E_CALL_GATE_STRESS=129`, `E_INTRADAY_TRADE_LIMIT=63`, `E_CALL_GATE_MA20=51`, `E_INTRADAY_CAP_TOO_SMALL=33` |
| Top NO_TRADE blocks | `CONFIRMATION_FAIL`, `REGIME_NOT_TRADEABLE`, panic-regime neutral blocks, `VIX_STABLE_LOW_CONVICTION` |
| VASS validation fails (dominant) | `WIN_RATE_GATE_BLOCK=402`, `HAS_SPREAD_POSITION=110`, `TRADE_LIMIT_BLOCK=42` |

| Bug ID | V6.19 Dec-Feb Status Update |
|---|---|
| T-18 | Regression persists (`ROUTER_MARGIN_WIDTH_INVALID=1`), though one occurrence is bypassed in close flow (`ROUTER_EXIT_BYPASS_MARGIN_ESTIMATE`) |
| T-27 | Regression persists (`Insufficient buying power=4` with related forced-close/orphan cleanup activity) |
| T-28 | Improved materially in this run (`APPROVED->RESULT=73.4%`), but candidate->approved funnel remains narrow |
| T-33 | Validated (all 288 intraday drops carry explicit actionable `E_*` codes) |
| T-36 | Partial: spread exit reconciliation appears (`SPREAD: EXIT | Reason=FILL_CLOSE_RECONCILED=1`), but lifecycle coverage still incomplete (`SPREAD: EXIT_SIGNAL=3` vs `SPREAD entries=31`) |
| O-20 | Still open and severe: VASS throttled mainly by `WIN_RATE_GATE_BLOCK=402` |
| O-21 | Still open: spread mix remains bull-call heavy into stress windows; call-side losses dominate realized P&L |

### Latest Run Quick Scorecard (V6.18: Sep-Dec 2018)

| Gate | Snapshot | Status |
|------|----------|:------:|
| SC-04 Exit margin reliability | `Order Error=8` (insufficient buying power cluster), `MARGIN_CB_SKIP=4` | ❌ |
| SC-06 Intraday conversion | `APPROVED=370`, `RESULT=28` (**7.6%**) | ❌ |
| SC-07 Drop reason quality | `INTRADAY_SIGNAL_DROPPED=121`, all actionable `E_*` | ✅ |
| SC-12 Stress protection firing | `PREMARKET_VIX_LADDER=32` (`L1=21`, `L2=8`, `L3=3`), stress/MA20 gates active | ✅ |
| SC-13 VASS participation | `VASS_ENTRY=33` but `VASS_SKIPPED+REJECTION=851`; dominated by occupancy/gates | ❌ |
| SC-14 Bear/chop handling | PUT-leaning direction generation, but realized losses remain call-concentrated | ❌ |
| SC-19 Diagnostics integrity | Drop/rejection reasons remain explicit and useful | ✅ |

### V6.18 Sep-Dec 2018 Snapshot

| Metric | Value |
|---|---:|
| Trades | 116 |
| Win Rate | 30.2% (35W / 81L) |
| Net P&L | -23,293 |
| Call / Put P&L (by symbol right) | `CALL=-22,491`, `PUT=-802` |
| MICRO_UPDATE | 2,440 |
| Dir=NONE / CALL / PUT | 1,614 / 262 / 564 |
| INTRADAY_APPROVED / DROPPED / RESULT | 370 / 121 / 28 |
| Top intraday drop codes | `E_CALL_GATE_MA20=53`, `E_INTRADAY_TRADE_LIMIT=33`, `E_CALL_GATE_STRESS=21` |
| Top NO_TRADE blocks | `REGIME_NOT_TRADEABLE`, `CONFIRMATION_FAIL`, `VIX_STABLE_LOW_CONVICTION` |
| VASS validation fails (dominant) | `HAS_SPREAD_POSITION=603`, `BEAR_PUT_ASSIGNMENT_GATE=103`, `DIRECTION_MISSING=84` |

| Bug ID | V6.18 Sep-Dec 2018 Status Update |
|---|---|
| T-27 | Regression persists: margin rejects still appear in this run |
| T-28 | Still open and worse in chop (`APPROVED->RESULT=7.6%`) |
| T-33 | Validated (diagnostics explicit and stable) |
| O-13 | Confirmed open (`BEAR_PUT_ASSIGNMENT_GATE=103`) |
| O-19 | Confirmed open (`NEUTRALITY_EXIT` remains dominant spread exit mode) |
| O-21 | Confirmed open (call-side spread loss concentration remains severe) |

### V6.18 2022 Snapshot (Jul-Oct)

| Metric | Value |
|---|---:|
| Trades | 112 |
| Win Rate | 33.0% (37W / 75L) |
| Net P&L | -6,510 (fees included in trade log) |
| Calls / Puts (executed trades) | 22 / 90 |
| Call P&L / Put P&L | -2,416 / -4,094 |
| MICRO_UPDATE | 2,460 |
| Dir=NONE / CALL / PUT | 1,346 / 464 / 650 |
| INTRADAY_APPROVED / DROPPED / RESULT | 551 / 446 / 95 |
| Top intraday drop codes | `E_CALL_GATE_STRESS=219`, `E_INTRADAY_TRADE_LIMIT=184`, `E_INTRADAY_TIME_WINDOW=20` |
| Top NO_TRADE blocks | `CONFIRMATION_FAIL=249`, `REGIME_NOT_TRADEABLE=99`, `VIX_STABLE_LOW_CONVICTION=90`, `QQQ_FLAT=90` |
| VASS | `ENTRY=4`, `REJECTION=674` (`WIN_RATE_GATE_BLOCK=610`, `DIRECTION_MISSING=64`) |

| Bug ID | V6.18 2022 Status Update |
|---|---|
| T-18 | Improved and effectively clear in this run (`ROUTER_MARGIN_WIDTH_INVALID=0`) |
| T-27 | Improved and effectively clear in this run (no force-close margin reject cluster) |
| T-28 | Still open (`APPROVED->RESULT=17.2%`) despite actionable drop codes |
| T-33 | Remains validated (`E_*` drop reasons explicit in all 446 drops) |
| T-36 | Still open (result/exit lifecycle telemetry remains incomplete; spread exits absent) |
| O-13 | Still open (bear-put spread participation remains heavily suppressed by VASS gating/fails) |
| O-19 | Not triggered in this run (`NEUTRALITY_EXIT=0`), keep open pending longer-horizon validation |

### V6.19 Technical Fix Wave (Applied in Code, Pending Backtest Validation)

| Fix Area | What Changed | Validation Status |
|---|---|:---:|
| Intraday funnel semantics (T-28) | `INTRADAY_SIGNAL_APPROVED` now logs only after engine produces a real order intent; pre-check logging moved to candidate stage | 🟡 |
| Intraday counters correctness (T-28) | Intraday trade counters moved to fill-time (`register_entry`) from pre-submit time; pre-submit rollback path removed | 🟡 |
| OCO inactive sibling cleanup (T-27) | Added OCO inactive handler to cancel sibling and cleanup pair on `Invalid`/`Canceled` lifecycle states | 🟡 |
| Spread exit lifecycle telemetry (T-36) | Added explicit spread exit reconciliation log (`SPREAD: EXIT | Reason=FILL_CLOSE_RECONCILED`) | 🟡 |
| Run-level diagnostics (T-36/T-28 support) | Added `OPTIONS_DIAG_SUMMARY` counters for `Approved/Dropped/Results/VASS_Blocks/MarginRejects` | 🟡 |
| VASS win-rate decouple controls (O-20) | Added `VASS_WIN_RATE_HARD_BLOCK=False` + `VASS_WIN_RATE_SHUTOFF_SCALE` fallback so VASS can degrade size instead of hard stop | 🟡 |
| Bear/chop call concentration controls (O-21) | Added BULL_CALL stress block/early-stress size controls (`BULL_CALL_STRESS_*`, `BULL_CALL_EARLY_STRESS_*`) and debit-momentum regime blocks | 🟡 |
| Spread DTE refresh hardening (T-37) | `_check_spread_exit` now recomputes DTE from live expiry each check to avoid stale DTE state bypassing de-risk exits | 🟡 |
| Credit direction propagation fix (T-07/T-21 support) | Router->engine credit spread call path now passes resolved `direction` explicitly | 🟡 |
| Choppy single-leg participation control (O-23 support) | Applied `CHOPPY_SIZE_REDUCTION` on intraday single-leg entries for churn control parity with spread path | 🟡 |

### V6.20 Execution + Cleanup Wave (Applied in Code, Pending Backtest Validation)

| Fix Area | What Changed | Validation Status |
|---|---|:---:|
| Multi-spread execution state (T-41) | Replaced single spread-slot behavior with list-backed spread tracking (`_spread_positions`) while keeping compatibility accessor (`get_spread_position`) | 🟡 |
| Slot/counter gating taxonomy (T-42) | Spread/intraday entry gating now emits explicit `R_SLOT_*` reasons (`R_SLOT_SWING_MAX`, `R_SLOT_TOTAL_MAX`, `R_SLOT_DIRECTION_MAX`, `R_SLOT_INTRADAY_MAX`) | 🟡 |
| Spread lifecycle iteration (T-43) | Premarket ITM checks, Friday firewall, spread exits, and reconcile paths iterate all active spreads (no primary-slot assumption) | 🟡 |
| Spread rejection normalization (T-44) | Added/used `R_COOLDOWN_DIRECTIONAL`, `R_MARGIN_PRECHECK`, and `R_CONTRACT_QUALITY:*` in spread entry paths and VASS logging | 🟡 |
| Stage-1 capacity activation (T-45) | `OPTIONS_MAX_SWING_POSITIONS` raised to `3` with direction cap guard (`OPTIONS_MAX_SWING_PER_DIRECTION=2`) | 🟡 |
| Trend reserve-symbol alignment (T-46) | Router trend margin reserve list updated from deprecated `TNA/FAS` to `UGL/UCO` | 🟡 |
| ITM log throttling wiring (T-47) | Implemented stateful use of `SHORT_LEG_ITM_EXIT_LOG_INTERVAL` to throttle repeated ITM trigger logs | 🟡 |
| Intraday cutoff log consistency (T-48) | Removed hardcoded `15:30` force-exit wording in options engine runtime logs; now uses `INTRADAY_FORCE_EXIT_TIME` | 🟡 |

**Commit references (V6.20 wave):**
- `dd93f44` - multi-spread slots/lifecycle and rejection hardening
- `c6b62f8` - Stage-1 swing cap + `R_*` spread rejection taxonomy + cooldown decoupling
- `caa4ffc` - trend symbol alignment + ITM throttling + configurable intraday cutoff logs

### V6.21 Plumbing Hotfixes (Applied, Pending Validation)

| Fix Area | What Changed | Validation Status |
|---|---|:---:|
| Spread close retry durability | Added persistent spread-close retry queue on close-leg `Canceled` events; retries every 5 minutes until flat | 🟡 |
| Spread close accounting isolation | Replaced shared/global close counters with per-spread close trackers keyed by `long|short` | 🟡 |
| Spread retry key safety | Retry queue now keyed by spread pair (not long symbol only), preventing cross-spread contamination | 🟡 |
| Stale close-state cleanup | Auto-purge retry/close tracker keys when spread is no longer active | 🟡 |
| Execution telemetry noise reduction | Added execution event forwarding guard so non-ExecutionEngine orders log once as `EXEC_EXTERNAL` (reduces `EXEC: UNKNOWN_ORDER` spam) | 🟡 |
| Router combo width fallback hardening | Router now derives spread width from OCC leg symbols when `spread_width` metadata is missing/invalid before margin estimation | 🟡 |
| Combo short-qty consistency | Router combo scaling now keeps short-leg qty exactly aligned with scaled spread count/sign | 🟡 |
| Reconcile orphan close guard | Reconcile now skips orphan liquidate when broker holding is already flat at submit time | 🟡 |
| Assignment equity containment tighten | Reconcile now liquidates any residual `QQQ` equity detected in options isolation flow | 🟡 |
| ITM% calculation correction | Short-leg ITM exit % now uses underlying price denominator (not strike) | 🟡 |

### Open Technical Items Snapshot (Post V6.21 Patch)

| Priority | Bug ID | Remaining Gap | Next Validation Gate |
|---|---|---|---|
| P0 | T-18 | Router margin-width invalid appears intermittently; fallback derivation added and awaiting rerun validation | SC-04, SC-10 |
| P0 | T-27 | Force-close/OCO margin reject cluster still reappears | SC-04, SC-09 |
| P1 | T-28 | Candidate->Approved funnel remains narrow even though Approved->Result improved | SC-06 |
| P1 | T-36 | Spread lifecycle telemetry still incomplete across all entry/exit paths | SC-10, SC-19 |
| P1 | T-19 | Assignment containment fix still needs rerun validation in stress windows | SC-03 |

---

## Bug Registry (Master)

| ID | Category | Bug | Evidence | Status | Notes |
|:--:|:--------:|-----|----------|:------:|------|
| T-01 | Technical | Intraday positions escaping EOD force‑close | Q1 2022: DTE≤2 held >1 day | ✅ Validated | V6.13: No DTE≤2 overnight escapes in 112 days |
| T-02 | Technical | Scheduler failure / EOD routines | PRE_MARKET_SETUP callback error | ✅ Validated | V6.13: 0 scheduler errors in 112 days |
| T-03 | Technical | Short‑leg close logging sparse | Short leg closes << spreads removed | ✅ Validated | V6.13: 32 POSITION_REMOVED match leg closures |
| T-04 | Technical | Zero‑price inject (sizing bug) | `V2.19_INJECT price=0` | 🟡 Applied (Pending Validation) | Verify after V2.19 inject fallback |
| T-05 | Technical | Signal → Execution instrumentation | Approved signals >> results | ✅ Validated | V6.13_a: approval/selection/result funnel measurable (396→140 trades) |
| T-06 | Technical | Assignment risk exits | `ASSIGNMENT_RISK_EXIT` in 2022 | 🟡 Monitor | Confirm if behavior desired |
| T-07 | Technical | VASS credit option‑type mismatch | Credit spreads never firing | 🟡 Applied (Pending Validation) | Strategy‑aware option_right fix |
| T-08 | Technical | VASS_ENTRY logging missing | VASS_ENTRY=0 while SPREAD: ENTRY_SIGNAL fires | ✅ Validated | V6.13_a: `VASS_ENTRY=40` with matching spread entries |
| T-09 | Technical | Pre‑entry margin buffer for spreads | 2015 margin call after full‑size entry | 🟡 Applied (Pending Validation) | SPREAD_MARGIN_SAFETY_FACTOR + margin checks |
| T-10 | Technical | Overnight gap protection (swing) | 2015 overnight liquidation event | ✅ Validated | V6.13: 121 EOD protection events logged |
| T-11 | Technical | VIX spike auto‑exit (spreads) | 2015: VIX 12→28+ held | 🟡 Applied (Pending Validation) | Exit on VIX level/5D spike |
| T-12 | Technical | Regime deterioration exit (spreads) | 2015: regime fell from 75→cautious, held | 🟡 Applied (Pending Validation) | Exit on regime drop vs entry |
| T-13 | Technical | Invalid Stop Orders (Price=0) | V6.13: 10 StopMarket orders with Price=0, Status=Invalid | 🟡 Applied (Pending Validation) | V6.14: OCO Manager validates stop_price > 0 before creation |
| T-14 | Technical | Expiration Hammer Too Late | V6.13: 14 options sold @ $0.01 on expiry | 🟡 Applied (Pending Validation) | V6.14: Moved from 14:00 to 12:00 (noon) |
| T-15 | Technical | Assignment Safety Net MOO Canceled | V6.13: Dec 27 MOO canceled, -$37K loss | 🟡 Applied (Pending Validation) | V6.14: Immediate market fallback for critical cancelled MOO |
| T-16 | Technical | High Order Failure Rate | V6.13: 27.6% orders failed (Invalid+Canceled) | 🟡 Applied (Pending Validation) | V6.14: Pre-submit validation (symbol, price, expiry) |
| T-17 | Technical | Filter parsing failure (DTE=287) | V6.13 2015: 6 approved signals with nonsense filter counts | 🟡 Applied (Pending Validation) | V6.14: Enhanced filter funnel diagnostics with blocker ID |
| T-18 | Technical | Router margin width invalid regression | V6.13_a: `ROUTER_MARGIN_WIDTH_INVALID` appears during spread submits | 🟡 Applied (Pending Validation) | Router now falls back to width derivation from OCC symbols when metadata width is missing/invalid; rerun validation pending |
| T-19 | Technical | Assignment containment leak | V6.13_a: 2022-03-29 assignment path still produced QQQ stock liquidation | 🟡 Applied (Pending Validation) | `_reconcile_positions()` now always liquidates residual assignment QQQ equity and keeps zombie/orphan cleanup active |
| T-20 | Technical | Spread candidate pre-filter drops zero-bid long legs | `main.py:3872` rejects when `bid<=0 or ask<=0` before leg-role assignment | 🟡 Applied (Pending Validation) | Prefilter now rejects only invalid ask; zero-bid candidates are retained with safe mid-price fallback |
| T-21 | Technical | Credit selector missing liquidity quality filters | `options_engine.py:2974+` credit path lacks explicit OI/spread quality gates | 🟡 Applied (Pending Validation) | Added OI/spread quality filters and diagnostics parity for credit short/long leg selection |
| T-22 | Technical | Credit cooldown not direction-scoped | `options_engine.py:3015` uses shared `"CREDIT"` cooldown bucket | 🟡 Applied (Pending Validation) | Cooldown key is now strategy-scoped (`BULL_PUT_CREDIT` / `BEAR_CALL_CREDIT`) with legacy key cleanup |
| T-23 | Technical | Risk-reduction exits blocked by strict combo margin gate | Close combo path still enforces margin estimator before submit | 🟡 Applied (Pending Validation) | Router now bypasses margin-estimator block for `spread_close_short` exits |
| T-24 | Technical | Spread closing lock can remain stuck after failed close submit | `spread.is_closing=True` set before router close failures | 🟡 Applied (Pending Validation) | Router now resets lock on close-order failures |
| T-25 | Technical | VIX spike/regime deterioration exits preempted by assignment path | `ASSIGNMENT_RISK_EXIT` fires first; no `SPREAD: EXIT_SIGNAL` observed | 🟡 Applied (Pending Validation) | Added assignment grace window before non-mandatory ITM/margin exits |
| T-26 | Technical | Micro can starve VASS via shared daily options counter | Shared `MAX_OPTIONS_TRADES_PER_DAY` exhausted by intraday flow | 🟡 Applied (Pending Validation) | Added reserved swing slots guard for intraday limits |
| T-27 | Technical | OCO sibling orders still trigger margin rejects at force-close | Stage6.19 Dec-Feb: `Insufficient buying power=4` with force-close/orphan recovery around rejected exits | 🟡 Applied (Pending Validation) | Added inactive-order sibling cancel+cleanup in OCO manager; regression persists under stress and needs another validation run after latest patch |
| T-28 | Technical | Intraday approved->execution drop remains severe | Stage6.19 Dec-Feb: `CANDIDATE=352`, `APPROVED=64`, `RESULT=47` (`APPROVED->RESULT=73.4%`) | 🟡 Applied (Pending Validation) | Major improvement vs prior severe leak; remaining bottleneck moved upstream (candidate->approved narrowing) |
| T-29 | Technical | VASS spreads are instantly closed by assignment guard | Stage6.14: `SPREAD: POSITION_REGISTERED=23`, `ASSIGNMENT_RISK_EXIT=23`, `POSITION_REMOVED=23` (<=1 min) | 🟡 Applied (Pending Validation) | Assignment grace (`SPREAD_ASSIGNMENT_GRACE_MINUTES`) added before non-mandatory assignment exits |
| T-30 | Technical | NEUTRAL conviction veto can overtrade noisy flips | Panic rebound windows showed repeated NEUTRAL veto direction swings | 🟡 Applied (Pending Validation) | NEUTRAL veto now requires MICRO tradeable regime + direction alignment with MICRO recommendation |
| T-31 | Technical | PRE_MARKET_SETUP callback still receives option symbol objects | V6.15 2015 run: `symbol must be a non-empty string` on 2015-08-26 / 2015-08-27 / 2015-09-03 | 🟡 Applied (Pending Validation) | V6.16: premarket and ITM-short paths normalize symbols before router submission |
| T-32 | Technical | Intraday force-exit fallback close-side regression | V6.15 2015 run: `INTRADAY_FORCE_EXIT_FALLBACK` followed by `ROUTER: MARKET_ORDER | BUY ... Tag=MICRO` during close intent | 🟡 Applied (Pending Validation) | V6.16: router close intents now derive side/qty from live holdings only (reduce-only behavior) |
| T-33 | Technical | Approved->dropped reason opacity in intraday path | V6.18: drops now carry explicit codes (`E_CALL_GATE_MA20`, `E_CALL_GATE_STRESS`, `E_INTRADAY_TIME_WINDOW`, `E_INTRADAY_TRADE_LIMIT`) | ✅ Validated | Engine-side drop reason propagation is now visible in backtest logs |
| T-34 | Technical | Force-close path not idempotent (position amplification) | V6.15 2017 run: 2017-08-22 close cycle grew to `Qty=653` via repeated fallback/OCO recovery before next-day reconcile | 🟡 Applied (Pending Validation) | V6.16: close-in-progress lock + one-submit-per-symbol/day + live-qty override |
| T-35 | Technical | OCO recovery recreates oversized orders during force-close window | V6.15 2017 run: `OCO_RECOVER` created `Qty=431` while active intraday leg was `Qty=222` near 15:25 | 🟡 Applied (Pending Validation) | V6.16: OCO recovery disabled near force-close window and skipped during close-in-progress |
| T-36 | Technical | Backtest telemetry suppresses intraday rejection reasons | Stage6.19 Dec-Feb: explicit rejection codes present; lifecycle still partial (`SPREAD: EXIT_SIGNAL=3` vs `ENTRY_SIGNAL=31`) | 🟡 Applied (Pending Validation) | Rejection visibility fixed; lifecycle/result completeness remains open until next run confirms `OPTIONS_DIAG_SUMMARY` + full exit coverage |
| T-37 | Technical | Spread DTE state can become stale and delay de-risk exits | Dec-Feb/2015 reviews showed spread hold-to-expiry behavior inconsistent with intended DTE de-risk | 🟡 Applied (Pending Validation) | `_check_spread_exit` now recomputes DTE from leg expiry each cycle before applying de-risk/expiry exits |
| T-41 | Technical | Single-slot spread state starved VASS participation | Prior runs dominated by `HAS_SPREAD_POSITION` / single active spread assumptions | 🟡 Applied (Pending Validation) | V6.20 introduced list-backed spread tracking and multi-spread lifecycle iteration |
| T-42 | Technical | Slot rejection ambiguity masked true blocker source | Generic/legacy skip reasons reduced RCA quality | 🟡 Applied (Pending Validation) | V6.20 uses explicit `R_SLOT_*` reasons in engine/router-visible diagnostics |
| T-43 | Technical | Spread lifecycle logic assumed one active spread | Premarket/firewall/exit/reconcile paths historically read single spread object | 🟡 Applied (Pending Validation) | V6.20 updated main orchestration to iterate all active spreads |
| T-44 | Technical | Spread rejection codes inconsistent across cooldown/margin/quality | Mixed legacy reasons (`ENTRY_ATTEMPT_LIMIT`, `MARGIN_PRECHECK_BLOCK`, etc.) | 🟡 Applied (Pending Validation) | V6.20 normalized to `R_COOLDOWN_DIRECTIONAL`, `R_MARGIN_PRECHECK`, `R_CONTRACT_QUALITY:*` |
| T-45 | Technical | Stage-1 capacity not enabled in config | Plan required 3 swing slots; config remained at 2 | ✅ Validated | V6.20 set `OPTIONS_MAX_SWING_POSITIONS=3` |
| T-46 | Technical | Trend reserve-symbol list stale in router | Router still used `TNA/FAS` after `UGL/UCO` trend redesign | ✅ Validated | V6.20 changed trend reserve list to `QLD/SSO/UGL/UCO` |
| T-47 | Technical | ITM log interval configured but not wired | `SHORT_LEG_ITM_EXIT_LOG_INTERVAL` existed without runtime state usage | ✅ Validated | V6.20 added throttled ITM trigger logging state by symbol/time |
| T-48 | Technical | Intraday force-close log text hardcoded 15:30 | Runtime/log wording diverged from configured cutoff (`INTRADAY_FORCE_EXIT_TIME`) | ✅ Validated | V6.20 options engine logs now derive cutoff from config |
| T-49 | Technical | Spread close quantity tracker cross-contamination | Stage6.21 logs show impossible aggregates (for example `Total closed=120/20`, `320/20`) | 🟡 Applied (Pending Validation) | V6.21: replaced shared close counters with per-spread close trackers keyed by `long|short` |
| T-50 | Technical | `EXEC: UNKNOWN_ORDER` flood hides actionable execution telemetry | Stage6.21 logs contain repeated unknown broker IDs for OCO/atomic/manual orders | 🟡 Applied (Pending Validation) | V6.21: main now forwards order events to ExecutionEngine only for mapped broker IDs; external events logged once as `EXEC_EXTERNAL` |
| T-51 | Technical | Canceled spread close can fall through to expiry | Example: `211220C00391000` close canceled on Dec 11, later liquidated by expiration hammer on Dec 20 | 🟡 Applied (Pending Validation) | V6.21: canceled close legs now enqueue persistent spread-close retry loop until position is flat |
| O-01 | Optimization | Low win rate / negative P&L | 2022Q1: -21,641 | 🔴 Open | Strategy tuning |
| O-02 | Optimization | High Dir=NONE | 2022Q1: 59% | 🔴 Open | Micro gating still strict |
| O-03 | Optimization | Micro gating too restrictive | CAUTIOUS/NORMAL/WORSENING blocks | 🔴 Open | Expand tradeable regimes or thresholds |
| O-04 | Optimization | VASS rejection storm | 2022Q1: 534 rejections | 🟡 Applied (Pending Validation) | DTE fallback + credit fix |
| O-05 | Optimization | CALL/PUT profitability imbalance | 2022Q1: PUTs worse | 🔴 Open | Strategy calibration |
| O-06 | Optimization | Multi-day hold efficiency | V6.13_a: 51/140 trades held >1 day | 🟡 Monitor | Most are swing by design; optimize carry/expiry behavior |
| O-07 | Optimization | PUT strategy underperforms in crash | 2015/2022 PUT P&L negative | 🔴 Open | Re‑evaluate PUT sizing/strategy |
| O-08 | Optimization | VASS as intraday governor | VASS inactive → micro dominates | 🔴 Open | Consider gating micro by VASS |
| O-09 | Optimization | Spread exit reason logging | Limited PROFIT/STOP logs vs exits | 🟡 Monitor | Verify exit reasons cover all paths |
| O-10 | Optimization | Monthly P&L tracking | Aug 2015 drawdown visibility missing | ✅ Implemented | V6.12: MonthlyPnLTracker class + main.py integration |
| O-11 | Optimization | Position concentration limit | Multiple spreads same expiry | 🔴 Open | Add per‑expiry cap |
| O-12 | Optimization | CALL loss concentration remains dominant | Stage6.14: CALL P&L -20,695 vs PUT P&L +351 | 🟡 Applied (Pending Validation) | Added stress CALL block (`INTRADAY_CALL_BLOCK_VIX_MIN`, `INTRADAY_CALL_BLOCK_REGIME_MAX`) |
| O-13 | Optimization | PUT spread participation blocked by assignment guard strictness | V6.15 2015 run: `ValidationFail=BEAR_PUT_ASSIGNMENT_GATE` 124 rejections; put-direction rejections dominate (299/355) | 🔴 Open | Recalibrate assignment gate thresholds for bear-put debit construction |
| O-14 | Optimization | Micro participation still bottlenecked by gating | V6.15 2015 run: `Dir=NONE 72.6%`, `NO_TRADE` top blocks `REGIME_NOT_TRADEABLE/CONFIRMATION_FAIL/QQQ_FLAT` | 🔴 Open | Small-threshold tuning only; avoid broad logic expansion |
| O-15 | Optimization | PUT participation remains too low in bull/chop windows | V6.15 2017 run: PUT only 3/38 trades; VASS PUT attempts mostly blocked by assignment gate in low IV | 🔴 Open | Keep bear-put path available with risk-scaled sizing instead of hard gate in low-risk contexts |
| O-16 | Optimization | VASS conviction override too sensitive in RISK_ON | V6.18 2015: VIX-spike veto windows still produce weak bearish spread outcomes in bullish macro context | 🔴 Open | Add regime-aware hysteresis for VIX-veto so transient spikes do not force bearish spread direction in sustained risk-on |
| O-17 | Optimization | Counter-trend PUT entries during sustained RISK_ON degrade Micro expectancy | V6.17 2017 NoSync: PUTs in regime 73-75 had weak outcomes vs CALLs | 🔴 Open | Add trend-persistence gate for counter-trend intraday PUTs when macro remains strongly risk-on |
| O-18 | Optimization | Late-day intraday entries show weak/unstable edge | V6.17 2017 NoSync: multiple entries at/after 15:00 with mixed quality and elevated noise | 🔴 Open | Tighten late-session entry quality rules (higher conviction or reduced size near close) |
| O-19 | Optimization | Neutrality churn exits reduce spread efficiency | V6.18 2015: `SPREAD: EXIT_SIGNAL` shows `NEUTRALITY_EXIT=17` (dominant spread exit mode) | 🔴 Open | Convert hard neutrality exits to staged de-risking (size-down/guarded hold) when risk is contained |
| O-20 | Optimization | VASS frozen by global win-rate shutoff in bear run | V6.19 Dec-Feb: `VASS_REJECTION=450`, dominant `ValidationFail=WIN_RATE_GATE_BLOCK=402` while `VASS_ENTRY=31` | 🟡 Applied (Pending Validation) | Implemented soft-shutoff controls (`VASS_WIN_RATE_HARD_BLOCK=False`, scaled participation). Need next run to confirm `WIN_RATE_GATE_BLOCK` no longer dominant |
| O-21 | Optimization | VASS over-concentrates in bullish debit spreads during deteriorating bear windows | V6.19 Dec-Feb: realized call-side losses dominate (`CALL P&L=-17,680`, `PUT P&L=+1,630`) | 🟡 Applied (Pending Validation) | Added VIX stress/acceleration BULL_CALL blocks + early-stress size down + debit-momentum regime blocks. Validate spread-mix shift in next runs |
| O-22 | Optimization | Choppy regime slot starvation from persistent spread occupancy | V6.18 Sep-Dec 2018: `HAS_SPREAD_POSITION=603` dominates VASS skips/rejections | 🔴 Open | Reduce hold/churn lockup via staged exits and position-age aware re-entry controls |
| O-23 | Optimization | Combined-size floor blocks tradable intraday setups in chop | V6.18 Sep-Dec 2018: `E_INTRADAY_COMBINED_SIZE_MIN=7` drops | 🟡 Monitor | Recalibrate combined-size minimum near boundary conditions |

---

## V6.15 Jul-Sep 2015 NoSync (Pre-Change Baseline Audit)

**Run:** `V6_15_Jul_Sep_2015_NoSync`  
**Files reviewed:**  
`docs/audits/logs/stage6.15/V6_15_Jul_Sep_2015_NoSync_logs.txt`  
`docs/audits/logs/stage6.15/V6_15_Jul_Sep_2015_NoSync_orders.csv`  
`docs/audits/logs/stage6.15/V6_15_Jul_Sep_2015_NoSync_trades.csv`

**Date:** 2026-02-10

### Headline Metrics

- Trades: **98**
- Net P&L: **-$14,190**
- Calls: **67 trades**, **P&L -$5,622**, win rate **38.8%**
- Puts: **31 trades**, **P&L -$8,568**, win rate **35.5%**
- No margin order failure lines detected in this run

### Micro Signal Funnel

- `MICRO_UPDATE`: **1,840**
- `Dir=NONE`: **1,335** (**72.6%**)
- `INTRADAY_SIGNAL_APPROVED`: **173**
- `INTRADAY_SIGNAL_DROPPED`: **35**
- `INTRADAY_RESULT`: **20**
- Top NO_TRADE reasons:
  - `REGIME_NOT_TRADEABLE`: **222**
  - `CONFIRMATION_FAIL`: **214**
  - `QQQ_FLAT`: **165**
  - `VIX_STABLE_LOW_CONVICTION`: **43**

### VASS Behavior

- `SPREAD: ENTRY_SIGNAL`: **27**
- `VASS_REJECTION`: **355**
- Rejection direction split: **PUT 299**, **CALL 56**
- Validation fails:
  - `DIRECTION_MISSING`: **205**
  - `BEAR_PUT_ASSIGNMENT_GATE`: **124**
  - `TRADE_LIMIT_BLOCK`: **22**

### Technical Findings from This Run

1. **T-31 baseline confirmed:** PRE_MARKET_SETUP callback crashed on symbol typing/normalization in this run. **V6.16 code fix applied; pending re-validation.**
2. **T-32 baseline confirmed:** intraday force-exit fallback exhibited close-side regression (`BUY` on close intent) in this run. **V6.16 router close-path fix applied; pending re-validation.**
3. **T-33 baseline confirmed:** approved->dropped instrumentation was opaque (`DROP_ROUTER_REJECT` without canonical cause). **V6.16 partial fix applied; pending re-validation.**
4. **T-11/T-12 behavior visible:** VIX spike and regime deterioration spread exits are firing, but not enough to protect losses when entries are poor.

### Optimization Findings from This Run

1. **O-13 confirmed:** PUT spread path is materially constrained by assignment gate strictness in stressed periods.
2. **O-14 confirmed:** participation remains primarily constrained by Micro gating, not slots.

---

## V6.15 Jul-Sep 2017 NoSync (Pre-Change Baseline Audit)

**Run:** `V6_15_Jul_Sep_2017_NoSync`  
**Files reviewed:**  
`docs/audits/logs/stage6.15/V6_15_Jul_Sep_2017_NoSync_logs.txt`  
`docs/audits/logs/stage6.15/V6_15_Jul_Sep_2017_NoSync_orders.csv`  
`docs/audits/logs/stage6.15/V6_15_Jul_Sep_2017_NoSync_trades.csv`

**Date:** 2026-02-10

### Headline Metrics

- Trades: **38**
- Net P&L: **-$1,247**
- Calls: **35 trades**, **P&L +$1,657**
- Puts: **3 trades**, **P&L -$2,904**
- Micro funnel:
  - `MICRO_UPDATE=1,820`
  - `Dir=NONE=1,575` (**86.5%**)
  - `NO_TRADE=941`
  - `INTRADAY_SIGNAL_APPROVED=79`
  - `INTRADAY_RESULT=4`

### August Big-Loss RCA

- Largest August loss cluster is not market thesis failure alone; it is execution-path corruption:
  - 2017-08-22 15:30: `INTRADAY_FORCE_EXIT_FALLBACK` triggered for `170825C00144000`.
  - Close path submitted additional `BUY` and OCO recovery rebuilt with inflated quantity (`Qty=431`).
  - 2017-08-23 09:33: `RECON_ORPHAN_CLOSE_SUBMITTED` liquidated `Qty=653` at much lower price.
  - Result: stacked losses on same contract (`-4,180`, `-3,996`, `-4,218`) and large fallback loss.

### Additional Findings

1. **T-34 baseline confirmed:** close flow was not idempotent in force-close window and could amplify holdings. **V6.16 lock/idempotency fix applied; pending re-validation.**
2. **T-35 baseline confirmed:** OCO recovery ran during force-close and recovered quantity diverged from holdings. **V6.16 cutoff/guard fix applied; pending re-validation.**
3. VASS PUT path remains underrepresented:
   - `VASS_REJECTION=5` in this sample, all `ValidationFail=BEAR_PUT_ASSIGNMENT_GATE`
   - spread entries are almost entirely bullish debit (`BULL_CALL`).
4. Spread exits are firing (`REGIME_DETERIORATION`, `NEUTRALITY_EXIT`, `PROFIT_TARGET`, `STOP_LOSS`), but entry mix remains one-sided.

---

## V6.13 Dec 2021 - Mar 2022 (Technical Fix Validation)

**Run:** `V6_13_Dec2021_Mar2022`
**Files reviewed:**
`docs/audits/logs/stage6.13/V6_13_Dec2021_Mar2022_logs.txt`
`docs/audits/logs/stage6.13/V6_13_Dec2021_Mar2022_orders.csv`
`docs/audits/logs/stage6.13/V6_13_Dec2021_Mar2022_trades.csv`

**Date:** 2026-02-09

### Headline Metrics

- Trades: **185**
- Win%: **25.4%** (47W / 138L)
- Net P&L: **-$28,482**
- Calls: **124 trades**, **P&L +$20,680** (21.8% win)
- Puts: **60 trades**, **P&L -$9,131** (33.3% win)
- Max Single Trade Loss: **-$42,180** (assignment event)

### Fix Verification Results

| Fix | Status | Evidence |
|-----|--------|----------|
| T-01 (DTE escape) | ✅ VALIDATED | No DTE≤2 overnight holds |
| T-02 (Scheduler) | ✅ VALIDATED | 0 PRE_MARKET_SETUP errors in 112 days |
| T-03 (Short-leg logging) | ✅ VALIDATED | 32 POSITION_REMOVED match closures |
| T-05 (Signal gap) | ✅ VALIDATED | 612:99 signal ratio monitored |
| T-10 (Overnight protection) | ✅ VALIDATED | 121 EOD protection events |

### NEW Critical Bugs Found

| ID | Bug | Impact |
|----|-----|--------|
| T-13 | Invalid Stop Orders (Price=0) | 10 instances, risk mgmt disabled |
| T-14 | Expiration Hammer Late | 14 @ $0.01 sales on expiry |
| T-15 | Assignment MOO Canceled | Single -$37K event |
| T-16 | Order Failure Rate | 27.6% (Invalid+Canceled) |

### Signal Metrics

- `INTRADAY_SIGNAL`: **612**
- `INTRADAY_RESULT`: **99** (16% conversion)
- `Dir=NONE`: **1,464 / 2,420** (60.5%)
- `VASS_REJECTION`: **799** (100% DTE/delta/credit)

### By Mode

| Mode | Orders | Notes |
|------|--------|-------|
| VASS (Swing) | 66 | Bull call debit spreads |
| MICRO (Intraday) | 129+ | 1-5 DTE momentum |
| Assignment/Salvage | 38 | Emergency closes |

---

## V6.13_a Dec 2021 - Mar 2022 (Latest Technical + Optimization Validation)

**Run:** `V6_13_a_Dec2021_Mar2022`  
**Files reviewed:**  
`docs/audits/logs/stage6.13/V6_13_a_Dec2021_Mar2022_logs (1).txt`  
`docs/audits/logs/stage6.13/V6_13_a_Dec2021_Mar2022_orders (1).csv`  
`docs/audits/logs/stage6.13/V6_13_a_Dec2021_Mar2022_trades (1).csv`

**Date:** 2026-02-10

### Headline Metrics

- Trades: **140**
- Win%: **43.57%** (61W / 79L)
- Net P&L: **-$48,371**
- Orders: **425 total** / **284 filled** / **48 invalid** / **93 canceled**
- Multi-day holds: **51 / 140** (36.4%)

### Technical Validation Summary

| Area | Status | Evidence |
|------|--------|----------|
| Direction mismatch | ✅ Validated | 396 approved-vs-selected comparisons, **0 mismatches** |
| OCO wiring | ✅ Validated | `OCO_CREATED=89`, `OCO_ACTIVATED=89`, `OCO_STOP=40`, `OCO_PROFIT=23` |
| Scheduler failures | ✅ Validated | No callback crash signatures in this run |
| Intraday overnight escape (DTE<=2) | ✅ Validated | No true intraday DTE<=2 carry escapes found |
| Margin reliability | 🔴 Open | `Insufficient buying power` errors = 88, `MARGIN_CB_SKIP` = 46 |
| Margin width regression | 🔴 Open | `ROUTER_MARGIN_WIDTH_INVALID` = 23 |
| Assignment containment | 🔴 Open | 2022-03-29 assignment flow still forced QQQ liquidation |

### Optimization Findings

| Area | Evidence | Status |
|------|----------|--------|
| Micro gating still strict | `INTRADAY blocked NO_TRADE=462` | 🔴 Open |
| NO_TRADE dominant reasons | `CONFIRMATION_FAIL=155`, `QQQ_FLAT=117`, `REGIME_NOT_TRADEABLE=100`, `VIX_STABLE_LOW_CONVICTION=41` | 🔴 Open |
| VASS construction friction | `VASS_REJECTION=575`, split: `DEBIT_ENTRY_VALIDATION_FAILED=292`, `CREDIT_ENTRY_VALIDATION_FAILED=283` | 🟡 Monitor |
| Direction profitability imbalance | CALL trades 85 with P&L **-22,926**, PUT trades 54 with P&L **-1,993** | 🔴 Open |

### Conclusion (V6.13_a)

Core technical plumbing has improved (direction consistency, OCO activation, scheduler stability), but two critical paths remain unresolved: margin handling regression (`ROUTER_MARGIN_WIDTH_INVALID`) and assignment containment leak. Optimization bottlenecks are now mostly concentrated in Micro NO_TRADE gating and VASS contract validation strictness.

---

## V6.13 Jul-Sep 2015 (Pre-Fix Baseline Validation)

**Run:** `V6_13_Jul_Sep_2015`
**Files reviewed:**
`docs/audits/logs/stage6.13/V6_13_Jul_Sep_2015_logs.txt`
`docs/audits/logs/stage6.13/V6_13_Jul_Sep_2015_orders.csv`
`docs/audits/logs/stage6.13/V6_13_Jul_Sep_2015_trades.csv`

**Date:** 2026-02-09

### Headline Metrics

- Trades: **58**
- Win%: **50.0%** (29W / 29L)
- Net P&L: **-$16,179**
- Calls: **-$8,450** (long call decay)
- Puts: **-$7,729** (protective puts expired worthless)
- Spreads: **+$200** (only positive)
- Order Fill Rate: **84.8%** (128/151)

### Aug 18-20 Anomaly Investigation

**Aug 18: T-02 Scheduler Crash (ROOT CAUSE)**
```
2015-08-18 09:25:00 SCHEDULER: Callback error for PRE_MARKET_SETUP:
  'PortfolioRouter' object has no attribute 'add_weight'
```
- Pre-market setup completely failed, corrupting Aug 19-20 state

**Aug 19: T-01 DTE=1 Overnight Escape**
```
2015-08-19 11:30:00 INTRADAY: Selected PUT | Strike=109.0 | Delta=0.34 | DTE=1
2015-08-19 15:30:00 INTRADAY_FORCE_EXIT: SKIP | already closed
```
- Entered DTE=1 option (high assignment risk)
- Force-close logic triggered but position was already gone

**Aug 20: T-17 Filter Failure Storm (6 Blocked Signals)**
```
2015-08-20 12:30:00 INTRADAY_SIGNAL_APPROVED: CONVICTION: UVXY +4% > +2%
2015-08-20 12:30:00 INTRADAY_FILTER_FAIL: PUT | Total=574 | Dir=287 DTE=287
  Greeks=0 Delta=0 OI=0 Prices=0 Spread=0
```
- Pattern repeated at: 12:30, 12:45, 13:30, 13:45, 14:10, 14:25 (6 times)
- Filter showed `DTE=287` which is nonsensical
- Half the option chain (287/574) couldn't be parsed

### Fix Validation (Comparing 2015 vs 2022)

| Fix | 2015 Status | 2022 Status | Conclusion |
|-----|-------------|-------------|------------|
| T-01 (DTE escape) | PRESENT (18 instances) | ABSENT (0 instances) | **FIXED** |
| T-02 (Scheduler) | PRESENT (1 crash) | ABSENT (0 crashes) | **FIXED** |
| T-10 (Overnight protection) | WORKING | WORKING | Validated |
| T-13 (Invalid Stops) | NOT FOUND | PRESENT | Not root cause |
| T-15 (Assignment detection) | WORKING | WORKING | Validated |

### Signal Metrics

- `MICRO_UPDATE`: **1,840**
- `Dir=NONE`: **1,367** (74.2%)
- `VASS_ENTRY`: **26**
- `VASS_REJECTION`: **843** (97% rejection rate)
- `INTRADAY_SIGNAL_APPROVED`: **244**

### Aug 24-26 Crisis Response (VIX Spike)

**What Worked:**
```
2015-08-24 06:30:00 REGIME V5.3: SPIKE CAP ACTIVATED - VIX=28.0 5d change=46.4%
2015-08-25 10:00:00 VIX_SPIKE: 24.9 -> 43.7 (via UVXY)
2015-08-25 10:00:00 MICRO_UPDATE: Regime=CRASH | Dir=PUT
```
- Spike cap activated at VIX 28.0 (+46.4% 5d)
- CRASH mode correctly detected
- Protective puts triggered 3 times
- Overnight gap protection closed spread at 15:45

**What Failed:**
- Protective puts bought at panic highs expired worthless (-$4,749)
- Long calls from Aug 13 decayed to $0.01 (-$7,060)

### NEW Bug Discovered

| ID | Bug | Evidence | Severity |
|----|-----|----------|----------|
| T-17 | Filter parsing failure | Aug 20: DTE=287 nonsense, 6 signals blocked | HIGH |

### Comparison with 2022 Analysis

| Metric | 2015 (Jul-Sep) | 2022 (Dec-Mar) | Delta |
|--------|----------------|----------------|-------|
| Trades | 58 | 185 | -127 |
| Win Rate | 50.0% | 25.4% | +24.6% |
| Net P&L | -$16,179 | -$28,482 | +$12,303 |
| Order Fill Rate | 84.8% | 72.4% | +12.4% |
| Dir=NONE | 74.2% | 60.5% | -13.7% |
| VASS Rejection | 97.0% | 99.0% | +2.0% |
| T-02 Scheduler Errors | 1 | 0 | Fixed |
| T-01 DTE Escapes | 18 | 0 | Fixed |

### Conclusion

The 2015 backtest validates that T-01 and T-02 bugs existed historically and are now fixed in V6.13. New bug T-17 (filter parsing failure) discovered and added to registry.

---

## V6.11 2022 Full Year (Baseline)

**Run:** `V6_11_2022FullYear`  
**Files reviewed:**  
`docs/audits/logs/stage6.5/V6_11_2022FullYear_logs.txt`  
`docs/audits/logs/stage6.5/V6_11_2022FullYear_orders.csv`  
`docs/audits/logs/stage6.5/V6_11_2022FullYear_trades.csv`

**Date:** 2026-02-09

---

## V6.12 Q1 Validation (Jan–Mar 2022) — Post-Fix Snapshot

**Run:** `V6_12_2022Q1_Validation`  
**Files reviewed:**  
`docs/audits/logs/stage6.5/V6_12_2022Q1_Validation_logs.txt`  
`docs/audits/logs/stage6.5/V6_12_2022Q1_Validation_orders.csv`  
`docs/audits/logs/stage6.5/V6_12_2022Q1_Validation_trades.csv`

**Headline Metrics**
- Trades: **115**
- Win%: **27.8%** (32W / 83L)
- Net P&L: **-21,641**
- Calls: **65 trades**, **P&L -6,958** (16W / 49L)
- Puts: **50 trades**, **P&L -14,683** (16W / 34L)

**Signal → Execution**
- `INTRADAY_SIGNAL`: **296**
- `INTRADAY_SIGNAL_APPROVED`: **208**
- `INTRADAY_RESULT`: **75** (≈ **25%** conversion)

**VASS Activity**
- `VASS_ENTRY`: **0**
- `VASS_REJECTION`: **534**
- `SWING: Spread construction failed`: **534**
- `FailStats` present on **178** rejection logs

**Notable Observations**
- **VASS still not executing** (0 entries).
- **Assignment risk exits remain**: `ASSIGNMENT_RISK_EXIT` **13**.
- **Short-leg close logging** appears but still sparse: `SPREAD: Short leg closed` **2** vs `SPREAD: POSITION_REMOVED` **14**.
- **Dir=NONE still high**: `Dir=NONE` **1,067 / 1,800** (~59.3%).
- No `MICRO_NO_TRADE` logs in this run (fix added after these logs).

---

## V6.13 Dec 2021 - Feb 2022 (Stage6.14 Validation)

**Run:** `V6_13_Dec2021_Feb2022`  
**Files reviewed:**  
`docs/audits/logs/stage6.14/V6_13_Dec2021_Feb2022_logs.txt`  
`docs/audits/logs/stage6.14/V6_13_Dec2021_Feb2022_orders.csv`  
`docs/audits/logs/stage6.14/V6_13_Dec2021_Feb2022_trades.csv`

**Date:** 2026-02-10

### Headline Metrics

- Trades: **169**
- Win%: **27.22%**
- Gross P&L: **-$20,344**
- Fees: **-$2,102.2**
- Net P&L after fees: **-$22,446.2**
- CALL P&L: **-$20,695**
- PUT P&L: **+$351**

### Technical Validation (Stage6.14)

| Area | Result | Evidence |
|------|--------|----------|
| Pre-market ladder active | ✅ Working | `PREMARKET_VIX_LADDER=50`, `PREMARKET_LADDER_CALL_BLOCK=15` |
| Cross-blocking fix | ✅ Working | Intraday + spread entries coexist on same timestamps/days |
| OCO margin reject path | 🟡 Fix applied (pending validation) | Static + fallback force-close timing moved to config-driven 15:25 path |
| Intraday signal execution path | 🟡 Fix applied (pending validation) | Approved `micro_state` is now reused in entry signal path |
| VASS execution continuity | 🟡 Fix applied (pending validation) | Assignment grace window added before non-mandatory assignment exits |

### New / Reconfirmed Bugs from Stage6.14

| ID | Bug | Severity | Evidence |
|----|-----|:--------:|----------|
| T-27 | OCO sibling margin reject race at force-close | P0 | Repeating `Order Error`/`INVALID` pairs at 15:30 for stale OCO ids; timing mitigation applied |
| T-28 | Intraday approved signals dropped before order placement | P0 | `INTRADAY_SIGNAL_DROPPED=281` (72.99% of approved signals); single-update mitigation applied |
| T-29 | Assignment guard instantly liquidates every VASS spread | P0 | `SPREAD: POSITION_REGISTERED=23`, `ASSIGNMENT_RISK_EXIT=23`, `POSITION_REMOVED=23`; assignment grace applied |
| O-12 | CALL-side loss concentration persists | P1 | CALL bucket contributes nearly all net loss; stress CALL block applied |

---

## Executive Summary

Latest validated run (`V6_13_a`, Dec 2021-Mar 2022) confirms technical progress in direction consistency and OCO activation, but strategy performance remains poor with **-$48,371** net P&L and **43.57%** win rate.  
Primary unresolved technical risks are **margin submit regression** and **assignment containment leak**. Primary optimization blockers remain **high Micro NO_TRADE gating**, **high VASS contract rejection**, and **CALL-side loss concentration**.

---

## Bug List (Current Run) — Technical

| Priority | Bug | Evidence | Impact | Status |
|:--------:|-----|----------|--------|:------:|
| **P0** | Margin submit regression (`ROUTER_MARGIN_WIDTH_INVALID`) | **23** occurrences in `V6_13_a` log during spread routing | Valid setups rejected; missed entries and unstable margin path | 🟡 Applied (Pending Validation) |
| **P0** | Assignment containment leak | 2022-03-29 assignment path still produced QQQ stock liquidation | One uncontained assignment path can still create large equity loss | 🔴 Open |
| **P1** | Buying power rejects still elevated | `Insufficient buying power` **88**, `MARGIN_CB_SKIP` **46** | Frequent rejected orders and execution instability | 🔴 Open |
| **P1** | Exit combo metadata incomplete (historical root cause) | Close signals lacked `spread_width`/credit metadata; router returned `ROUTER_MARGIN_WIDTH_INVALID` | Assignment-protective exits failed to execute reliably | 🟡 Applied (Pending Validation) |
| **P1** | Close path margin gate too strict for risk-reduction exits | Router blocked close combos on estimator failures | Risk-reduction exits could not fire when most needed | 🟡 Applied (Pending Validation) |
| **P1** | Spread closing lock persistence after close-submit failures | `is_closing=True` could stay latched after close submit rejection | Suppressed later exit retries | 🟡 Applied (Pending Validation) |
| **P1** | Pre-entry margin check strictness mismatch | Rejections occur despite pre-check passes in some windows | Indicates mismatch between local estimate and broker model timing | 🟡 Monitor |
| **P1** | Pre-filter drops zero-bid long-leg candidates | `main.py:3872-3873` skips contracts with `bid<=0/ask<=0` in candidate builder | Missed valid long-leg contracts; lower VASS constructability | 🟡 Applied (Pending Validation) |
| **P1** | Credit selector lacks OI/spread quality filters | `options_engine.py:2974+` credit short/long selection has no explicit OI/spread gates | Increased rejects, unstable fills, poorer spread quality | 🟡 Applied (Pending Validation) |
| **P1** | Credit cooldown cross-blocking | `options_engine.py:3015` shared `CREDIT` cooldown key | Bear-call and bull-put credit attempts can block each other | 🟡 Applied (Pending Validation) |
| **P1** | Micro intraday can consume swing daily capacity | Shared global options daily counter used by both modes | VASS entries suppressed even when swing slot/risk is available | 🟡 Applied (Pending Validation) |
| **P0** | OCO sibling margin reject race at force-close | Stage6.14: `Insufficient buying power=24`, all clustered at 15:30 | Stale exit orders still create invalid/margin noise and execution instability | 🟡 Applied (Pending Validation) |
| **P0** | Intraday approved signals dropped before order placement | Stage6.14: `APPROVED=385`, `DROPPED=281`, `EXECUTED=104` | Most approved signals never become orders | 🟡 Applied (Pending Validation) |
| **P0** | VASS spreads instantly closed by assignment guard | Stage6.14: `POSITION_REGISTERED=23`, `ASSIGNMENT_RISK_EXIT=23` | Swing engine rendered ineffective; churn losses | 🟡 Applied (Pending Validation) |
| **P2** | Legacy filter parsing anomaly tracking | 2015 had `DTE=287` parse anomalies; not reproduced in `V6_13_a` | Keep diagnostic hooks until two clean multi-year runs | 🟡 Monitor |

## Bug List (Current Run) — Optimization

| Priority | Bug | Evidence | Impact | Status |
|:--------:|-----|----------|--------|:------:|
| **P1** | Net strategy still negative | 140 trades, Win **43.57%**, Net **-$48,371** | Core strategy stack still unprofitable | 🔴 Open |
| **P1** | Micro NO_TRADE gating too high | `INTRADAY blocked NO_TRADE=462` | Participation throttled in tradeable windows | 🔴 Open |
| **P1** | VASS validation friction high | `VASS_REJECTION=575` vs `VASS_ENTRY=40` | Swing opportunities still over-filtered | 🔴 Open |
| **P1** | CALL loss concentration | CALL trades **85**, P&L **-$22,926** | Direction/strategy mix still biased to losing call setups | 🔴 Open |
| **P1** | CALL concentration still dominates latest run | Stage6.14: CALL P&L **-$20,695** vs PUT **+$351** | Optimization still not regime-compatible in stress periods | 🔴 Open |
| **P2** | Multi-day carry efficiency | 51/140 trades held >1 day | Carry/expiry handling likely reducing edge | 🟡 Monitor |
| **P2** | Signal quality in neutral micro states | NO_TRADE reasons cluster: CONFIRMATION_FAIL/QQQ_FLAT/REGIME_NOT_TRADEABLE | Conviction thresholds likely too strict/lagging | 🟡 Monitor |
| **P1** | VASS conviction override too sensitive in risk-on windows | Stage6.17 2015 NoSync: VIX-veto bearish overrides in bullish macro periods were net negative | Reversal overrides degrade spread direction quality in trend markets | 🔴 Open |
| **P1** | Counter-trend PUTs in sustained RISK_ON | Stage6.17 2017 NoSync: PUT bucket materially weaker than CALL bucket during strong risk-on regime | Avoidable drag from low-probability counter-trend entries | 🔴 Open |
| **P2** | Late-day intraday entry quality | Stage6.17 2017 NoSync: after-15:00 entries showed unstable edge distribution | End-of-day noise increases slippage/churn risk | 🔴 Open |
| **P2** | Neutrality churn in spread exits | Stage6.17 analyses: dead-zone neutrality exits create repeated small-loss/fee churn | Reduces spread expectancy without materially lowering tail risk | 🔴 Open |

## Performance Snapshot

**All trades**
- Total trades: **140**
- Win%: **43.57%** (61 wins / 79 losses)
- Net P&L: **-48,371**

**By direction**
- Calls: **85 trades**, **P&L -22,926**
- Puts: **54 trades**, **P&L -1,993**

**Underlying assignments**
- `QQQ` equity liquidation event: **1** (2022-03-29 assignment containment leak)

---

## Order Mix

- Total orders: **425**
- Filled orders: **284**
- Invalid orders: **48**
- Canceled orders: **93**

**Observation:** Failure rate remains high (`(48 + 93) / 425 = 33.2%`), indicating reliability issues despite improved instrumentation.

---

## Signal → Execution Metrics

- `INTRADAY_SIGNAL_APPROVED`: **396**
- `INTRADAY blocked NO_TRADE`: **462**
- Top NO_TRADE reasons: `CONFIRMATION_FAIL=155`, `QQQ_FLAT=117`, `REGIME_NOT_TRADEABLE=100`, `VIX_STABLE_LOW_CONVICTION=41`

**Observation:** Approval and block reasons are now visible, but gating remains strict and materially limits participation.

---

## VASS Activity

- `VASS_REJECTION`: **575**
- `VASS_ENTRY`: **40**
- Rejection split: `DEBIT_ENTRY_VALIDATION_FAILED=292`, `CREDIT_ENTRY_VALIDATION_FAILED=283`

**Observation:** VASS now enters more often than prior runs, but validation rejects are still dominant.

---

## Assignment Prevention

- `ASSIGNMENT_RISK_EXIT`: present
- `PREMARKET_ITM` checks: present
- `QQQ` equity liquidation event: **1** (2022-03-29)

**Conclusion:** Assignment safeguards improved but are not yet airtight.

---

## Major Issues (Ranked)

**P1 — Low Win Rate + Negative P&L**
   - Calls remain the largest loss bucket (**-22.9k**).
   - Net result stays deeply negative despite technical fixes.

**P1 — Micro gating remains strict**
   - `NO_TRADE` and confirmation checks are still blocking a large share of opportunities.

**P1 — VASS validation friction**
   - Debit and credit validations both reject at high volume (292/283).

**P0 — Margin/assignment reliability**
   - `ROUTER_MARGIN_WIDTH_INVALID` and one assignment containment leak remain unresolved technical risks.

---

## What Improved vs V6.9

✅ **Direction mismatch fixed** (approved vs selected direction mismatch = 0)  
✅ **OCO wiring active** (`OCO_CREATED=89`, `OCO_ACTIVATED=89`)  
✅ **Scheduler callback stability improved** (no legacy PRE_MARKET_SETUP crash in this run)  

---

## Fixes Applied (Pending Validation)

- **V6.13 optimization tuning (config-only)**:
  - Micro participation: lowered noise/fallback thresholds (`QQQ_NOISE_THRESHOLD`, `INTRADAY_QQQ_FALLBACK_MIN_MOVE`, `MICRO_SCORE_*_CONFIRM`, `INTRADAY_DEBIT_FADE_VIX_MIN`, `INTRADAY_FADE_MIN_MOVE`, `INTRADAY_ITM_MIN_*`).
  - Loss control: tightened ITM stop/target and ATR base/floor (`INTRADAY_ITM_STOP`, `INTRADAY_ITM_TARGET`, `OPTIONS_ATR_STOP_MULTIPLIER`, `OPTIONS_ATR_STOP_MIN_PCT`).
  - VASS constructability: widened credit short-leg delta max and reduced spread width target/min (`CREDIT_SPREAD_SHORT_LEG_DELTA_MAX`, `SPREAD_WIDTH_MIN`, `SPREAD_WIDTH_TARGET`).
  - Choppy recycling: re-enabled tight neutrality exit (`SPREAD_NEUTRALITY_EXIT_*`).
- **VASS spread failure cooldown trap**: Added DTE fallback ranges and only apply cooldown after all ranges fail.
- **VASS rejection visibility**: Added compact `FailStats` summary to throttled `VASS_REJECTION` logs.
- **Swing/Intraday cross‑blocking**: Replaced `has_position()` gate with mode‑specific `can_enter_swing()` and `has_intraday_position()` checks.
- **Micro Dir=None reduction**: Lowered `QQQ_NOISE_THRESHOLD` (0.35→0.25), `INTRADAY_QQQ_FALLBACK_MIN_MOVE` (0.50→0.35), `MICRO_SCORE_*_CONFIRM` (52/48→50/50), and `INTRADAY_ITM_MIN_MOVE` (0.80→0.45).
- **CALL bias control**: Asymmetric UVXY conviction thresholds (CALL -5%, PUT +2.5%) and NEUTRAL aligned trades allowed at reduced size.
- **Micro NO_TRADE visibility**: Throttled `MICRO_NO_TRADE` logs added with VIX/QQQ move + score.
- **Order tagging**: Options orders tagged as `MICRO` (intraday) or `VASS` (swing) in router for web/log clarity.
- **VASS credit selection fix**: Candidate contracts now filtered by strategy-required option right (CALL/PUT), not macro direction.
- **VASS_ENTRY logging**: Added explicit `VASS_ENTRY` log on spread signal creation.
- **Signal→execution visibility**: Added `INTRADAY_SIGNAL_DROPPED` log for approved signals that fail to produce orders.
- **Overnight gap protection (swing)**: Added EOD VIX-based close for spreads (all days).
- **VIX spike auto-exit (spreads)**: Exit bullish spreads on VIX level/5D spike.
- **Regime deterioration exit (spreads)**: Exit on regime drop/rise vs entry score.
- **Monthly P&L tracking (O-10)**: New `MonthlyPnLTracker` class in `utils/monthly_pnl_tracker.py`. Tracks P&L by engine (TREND, MR, OPT, HEDGE, YIELD) and month. Integrated into `main.py` for all fill handlers (TREND exits, MR exits, intraday options, spread closes). Persisted via ObjectStore. Logs EOD summary with win rate and net P&L.

---

## What Still Needs Fixing

1. **Close approved->dropped leak first (P0)**  
   - Eliminate generic `DROP_ENGINE_NO_SIGNAL` dominance with explicit engine/router reason propagation.
2. **Make intraday rejection telemetry backtest-visible (P0)**  
   - Ensure gate/validation failures are visible without requiring `LiveMode`.
3. **Reduce Dir=NONE without breaking bull participation (P1)**  
   - Tune conviction/noise thresholds with regime-conditional sizing, not blanket loosening.
4. **Improve VASS constructability and entry mix (P1)**  
   - Continue targeted filter tuning by dominant rejection class (DTE/delta/liquidity), avoid global over-relaxation.
5. **Control CALL losses in bear stress while preserving bull capture (P1)**  
   - Keep conditional CALL gates active and verify trigger rates in logs.

---

## Recommended Next Actions

1. **Lower VASS rejection rate**
   - Re‑examine delta bounds vs market data in 2022.
2. **Tighten intraday execution pipeline**
   - Add explicit logging for approval→submit→fill to trace drop‑offs.
3. **Audit CALL strategy**
   - Losses are concentrated in CALLs; check for wrong regime usage or stop/target scaling.

---

## Optimization Fix List (Summary)

**Execution & Pipeline**
- Add explicit log markers at: signal approved → order submit → fill → register.
- Track “approved but not submitted” reasons (margin pre‑check, cooldown, time window).

**Micro Signal Quality**
- Further reduce `Dir=NONE` by narrowing VIX‑stable band or adding a secondary QQQ fallback for low‑VIX days.
- Add a minimum conviction threshold that scales with VIX (lower in low‑VIX regimes).

**VASS Construction**
- Instrument rejection reasons by category (delta, DTE, credit, liquidity) and log counts.
- Relax delta/DTE only for the **rejection-dominant category**, not globally.

**Strategy Profitability**
- Review CALL losses by regime; disable CALLs in CAUTIOUS/DEFENSIVE unless macro+micro align.
- Re‑test stop/target symmetry for CALLs only; keep PUT logic unchanged if it is less negative.

**Assignment & Risk**
- Keep DTE=1 force‑close and pre‑market ITM check (working).
- Add a pre‑entry assignment buffer check for **all** spreads (not just on exit).

---

# V6.11 Options Engine Audit — 2017 Full Year

**Run:** `V6_11_2017FullYear`  
**Files reviewed:**  
`docs/audits/logs/stage6.5/V6_11_2017FullYear_logs.txt`  
`docs/audits/logs/stage6.5/V6_11_2017FullYear_orders.csv`  
`docs/audits/logs/stage6.5/V6_11_2017FullYear_trades.csv`

**Date:** 2026-02-09

---

## Performance Snapshot (2017)

- Total trades: **35**
- Win%: **57.1%**
- Net P&L: **+25,967**
- Fees: **1,600.30**

**By direction**
- Calls: **35 trades**, **+25,967 P&L**
- Puts: **0 trades**

---

## Signal / Execution Metrics (2017)

- `MICRO_UPDATE`: **7,260**
- `Dir=NONE`: **6,928** (~95.4%)
- `Dir=CALL`: **269**
- `Dir=PUT`: **73**
- `INTRADAY_SIGNAL`: **20**
- `INTRADAY_SIGNAL_APPROVED`: **18**
- `INTRADAY: Selected`: **8**
- `INTRADAY_RESULT`: **1**
- `VASS_ENTRY`: **34**
- `VASS_REJECTION`: **64**
- `PREMARKET_ITM`: **3**

---

## 2017 Observations

1. **Micro still largely inactive** (Dir=NONE ~95%).  
2. **Intraday execution nearly zero** (1 result for the entire year).  
3. **No PUT trades**, even in minor pullbacks.  
4. **Despite low activity, P&L was positive** due to CALL success in bull market.

---

# V6.11 Options Engine Audit — 2015 Full Year

**Run:** `V6_11_2015FullYear`  
**Files reviewed:**  
`docs/audits/logs/stage6.5/V6_11_2015FullYear_logs.txt`  
`docs/audits/logs/stage6.5/V6_11_2015FullYear_orders.csv`  
`docs/audits/logs/stage6.5/V6_11_2015FullYear_trades.csv`

**Date:** 2026-02-09

---

## Performance Snapshot (2015)

- Total trades: **109**
- Win%: **44.0%**
- Net P&L: **+16,003**
- Fees: **4,354.60**

**By direction**
- Calls: **84 trades**, **+24,075 P&L**
- Puts: **25 trades**, **-8,072 P&L**

---

## Signal / Execution Metrics (2015)

- `MICRO_UPDATE`: **7,300**
- `Dir=NONE`: **6,309** (~86.4%)
- `Dir=CALL`: **608**
- `Dir=PUT`: **403**
- `INTRADAY_SIGNAL`: **101**
- `INTRADAY_SIGNAL_APPROVED`: **76**
- `INTRADAY: Selected`: **56**
- `INTRADAY_RESULT`: **18**
- `VASS_ENTRY`: **84**
- `VASS_REJECTION`: **461**
- `ASSIGNMENT_RISK_EXIT`: **2**

---

## 2015 Observations

1. **Overall P&L positive, but PUT trades negative.**  
2. **Dir=NONE still high**, though lower than 2017.  
3. **Intraday conversion weak** (101 signals → 18 results).  
4. **VASS still rejected frequently** (461 rejections).

---
