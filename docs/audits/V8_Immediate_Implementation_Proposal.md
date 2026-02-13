# V8 Immediate Implementation Proposal (Minimal-Change Track)

**Date:** 2026-02-12  
**Objective:** Stabilize execution plumbing and regime responsiveness with minimal code change before larger optimization work.

## Why this track
This proposal includes only items with the highest impact-to-change ratio and lowest refactor risk.

- Keep architecture intact.
- Avoid broad strategy rewrites.
- Fix known failure modes from recent runs: spread churn exits, stale macro-regime usage, ambiguous rejection funnel.

## In Scope (Immediate V8)

1. Intraday regime refresh (macro lag reduction)
2. Tune existing overlay thresholds (no new overlay feature)
3. Neutrality staged de-risk (reduce churn)
4. VIX direction scoring bug fix (1-line correctness)
5. Engine vs router reject telemetry hardening (`E_*` vs `R_*`)
6. Anti-churn guard for spread exits (no non-emergency same-bar exits)
7. VASS anti-cluster entry control (same strategy+direction+DTE burst prevention)

## Detailed Implementation Plan

## 1) Intraday Regime Refresh (Macro Lag Reduction)

### Current behavior
- Options frequently consume `regime_engine.get_previous_score()` (prior EOD value).
- Intraday risk transitions can be missed for hours.

### Logic change
- Add intraday refresh checkpoints at `12:00` and `14:00`.
- Store intraday snapshot in `main.py` state:
  - `self._intraday_regime_score: Optional[float]`
  - `self._intraday_regime_updated_at: Optional[datetime]`
- Add one accessor in `main.py`:
  - `_get_effective_regime_score_for_options()`

### Effective-score rule
- Use defensive/worst-of score for options risk gating:
  - `effective_score = min(previous_eod_score, intraday_score_if_available)`
- Rationale:
  - Prevent stale bullish score from allowing bullish entries during stress.
  - Do not use intraday higher score to become more aggressive intraday.

### Touch points
- `main.py`
  - scheduler registration for `12:00`, `14:00`
  - refresh callback
  - replace options call sites currently using direct `get_previous_score()` where appropriate
- `engines/core/regime_engine.py`
  - if needed, small extraction method for intraday recalculation without full EOD pipeline side effects

### Logging
- `REGIME_REFRESH_INTRADAY: score=<x> at <time>`
- `REGIME_EFFECTIVE_OPTIONS: eod=<x> intraday=<y|None> effective=<z>`

### Acceptance checks
- Intraday logs show score changes on volatile days.
- Same-day direction flips happen without waiting for 15:45 EOD.

---

## 2) Overlay Threshold Tuning (Existing Path Only)

### Current behavior
- Overlay path exists and is active, but thresholds appear too stiff for transitions.

### Logic change
- No logic-branch additions.
- Tune config only (initial candidate set):
  - `REGIME_OVERLAY_STRESS_VIX: 21.0 -> 19.0`
  - `REGIME_OVERLAY_STRESS_VIX_5D: 0.18 -> 0.12`
  - `REGIME_OVERLAY_EARLY_VIX_LOW: 16.0 -> 15.0`
  - `REGIME_OVERLAY_EARLY_VIX_HIGH: 18.0 -> 17.0`

### Touch points
- `config.py` only

### Logging
- Keep existing overlay logs and ensure counts are included in summary diagnostics.

### Acceptance checks
- More timely STRESS/EARLY_STRESS transitions in 2022 stress windows.
- Bull runs do not collapse from over-triggering.

---

## 3) Neutrality Staged De-Risk (Reduce Churn)

### Current behavior
- Neutrality can trigger immediate full exit, causing fast re-entry churn and fee drag.

### Logic change
- Introduce 2-stage neutrality state per spread key (`long|short`):
  - Stage 1 (`NEUTRALITY_WARN`):
    - mark first-neutral timestamp
    - tighten stop behavior only
    - do not exit yet
  - Stage 2 (`NEUTRALITY_CONFIRMED_EXIT`):
    - exit only if neutrality persists for confirmation window
    - or if P&L deteriorates beyond a stage-1 damage threshold
- Clear warning state when regime leaves neutrality zone.

### Proposed knobs
- `SPREAD_NEUTRALITY_CONFIRM_HOURS = 2`
- `SPREAD_NEUTRALITY_STAGE1_DAMAGE_PCT = 0.15`

### Decision flow
1. If neutrality condition false: clear warn state; continue normal logic.
2. If neutrality true and no warn state: set warn state, tighten stop, return no-exit.
3. If neutrality true and warn active:
   - if elapsed >= confirm_hours: exit
   - elif pnl_pct <= -damage_pct: exit
   - else: hold

### Touch points
- `engines/satellite/options_engine.py`
- `config.py`

### Logging
- `NEUTRALITY_WARN: spread=<key> pnl=<x> score=<y>`
- `NEUTRALITY_CONFIRMED_EXIT: spread=<key> elapsed=<h> pnl=<x>`
- `NEUTRALITY_WARN_CLEARED: spread=<key> score=<y>`

### Acceptance checks
- Lower count of neutrality exits followed by same-direction re-entry within 24h.

---

## 4) VIX Direction Scoring Bug Fix (1 line)

### Current behavior
- In `utils/calculations.py`, fast-falling VIX branch returns `score_falling` instead of `score_falling_fast`.

### Logic change
- In `vix_direction_factor_score_v4`, change:
  - `return score_falling` -> `return score_falling_fast`
  for the `falling_fast_threshold` branch.

### Touch points
- `utils/calculations.py`

### Acceptance checks
- Unit/spot checks show fast VIX drops map to stronger bullish direction factor as designed.

---

## 5) Reject Telemetry Hardening (`E_*` vs `R_*`)

### Current behavior
- Some drops still collapse into ambiguous/no-signal buckets.

### Logic change
- Enforce strict namespace ownership:
  - Engine blocks must emit `E_*`
  - Router rejects must emit `R_*`
- In `main.py`, preserve and report source code verbatim from engine/router path.
- Keep a tiny fallback only for true unknown exceptions.

### Touch points
- `engines/satellite/options_engine.py`
- `portfolio/portfolio_router.py`
- `main.py`

### Logging/metrics
- Daily counters:
  - `E_*` counts by code
  - `R_*` counts by code
  - `UNKNOWN_DROP` count

### Acceptance checks
- Unknown drop bucket becomes near-zero.
- Top reject reasons are explicit and actionable.

---

## 6) Anti-Churn Guard (Detailed)

### Problem to fix
- Observed spread open and close at same timestamp (or too soon), causing guaranteed fee drag and false tail-loss dynamics.

### Design goal
- Block non-emergency exits during a short post-entry hold window.
- Preserve emergency risk exits.

### Guard scope
- Applies to spread exits from:
  - overlay stress exit
  - neutrality exit
  - standard profit/stop exits
  - non-emergency DTE-driven exits above hard floor
- Does **not** block:
  - assignment risk exits
  - deep ITM/overnight ITM forced exits
  - mandatory DTE force-close (`SPREAD_FORCE_CLOSE_DTE`)

### Proposed config
- `SPREAD_MIN_HOLD_MINUTES = 20`

### State source
- Use existing `SpreadPosition.entry_time` (already present).
- Compute `minutes_live = now - entry_time` once per spread per cycle.

### Execution-order change in `main.py`
- In `_check_spread_exit` per spread:
  1. Always run emergency checks first (`check_assignment_risk_exit`, 0DTE firewall).
  2. Evaluate hold guard:
     - if `minutes_live < SPREAD_MIN_HOLD_MINUTES`, skip non-emergency exit paths.
  3. If outside hold window, run overlay/non-emergency exits and normal spread exits.

### Engine guard mirror
- Add same minimum-hold check in `check_spread_exit_signals()` for defense-in-depth.
- If guard active and no emergency flag, return no exit with log:
  - `SPREAD_EXIT_GUARD_HOLD: spread=<key> live=<m> < min=<m>`

### Why both main + engine
- Main order ensures correct priority and avoids sending avoidable exit signals.
- Engine mirror prevents accidental bypass if future call paths invoke exit logic directly.

### Acceptance checks
- Zero non-emergency same-bar spread open+close events.
- Emergency exits still fire within hold window when required.

---

## 7) VASS Anti-Cluster Entry Control (Same Strategy/Direction/DTE)

### Problem to fix
- VASS can enter repeated near-identical spreads in short bursts, saturating slots and amplifying wrong-way exposure.
- Requirement: avoid same VASS spread profile (same direction, same DTE/expiry) being re-entered rapidly.

### Design goal
- Block repeated VASS entries for the same profile in two windows:
  - short burst guard: 15 minutes
  - rolling cooldown: 3 days
- Still allow concurrent VASS positions when expiry/DTE differs.

### Signature definition (VASS only)
- `signature = (strategy_type, direction, expiry_bucket)`
- `expiry_bucket` default: exact expiry date of selected long leg (preferred).
- Fallback if expiry unavailable: selected `days_to_expiry`.

### Proposed config
- `VASS_SIMILAR_ENTRY_MIN_GAP_MINUTES = 15`
- `VASS_SIMILAR_ENTRY_COOLDOWN_DAYS = 3`
- `VASS_SIMILAR_ENTRY_USE_EXPIRY_BUCKET = True`

### Decision flow
1. In VASS entry path, after strategy and contract selection (before submit), build signature.
2. Check burst guard:
  - if `now - last_entry_at[signature] < 15 minutes`, reject with `E_VASS_SIMILAR_15M_BLOCK`.
3. Check cooldown guard:
  - if `now < cooldown_until[signature]`, reject with `E_VASS_SIMILAR_3D_COOLDOWN`.
4. If entry is accepted/filled:
  - `last_entry_at[signature] = now`
  - `cooldown_until[signature] = now + 3 days`

### State tracking
- Add in `options_engine.py`:
  - `self._vass_last_entry_at_by_signature: dict[str, datetime]`
  - `self._vass_cooldown_until_by_signature: dict[str, datetime]`
- Optional stale cleanup for signatures older than 10 days.

### Reason-code and telemetry requirements
- Add explicit engine codes:
  - `E_VASS_SIMILAR_15M_BLOCK`
  - `E_VASS_SIMILAR_3D_COOLDOWN`
- Include both in drop summaries and diagnostics table.

### Why this meets your slot objective
- Same strategy + same direction + same DTE/expiry is throttled.
- Different DTE/expiry remains allowed, so multiple VASS positions can coexist as a ladder instead of clones.

### Acceptance checks
- No duplicate VASS signatures inside 15 minutes.
- No duplicate VASS signatures inside 3-day cooldown.
- Concurrent VASS positions remain possible across different expiries.

---

## Out of Scope (Deferred)

- Micro regime taxonomy collapse.
- Removing strategy families in this pass.
- Major capital partition redesign while running in options isolation mode.
- Large dead-code cleanup sweep.

## Phase D: Critical Missing Items (Add to Immediate V8 Scope)

## D1) Force-Close / OCO Margin-Reject Race Hardening (`T-27`)

### Problem
- At force-close windows, stale OCO siblings and close retries can still produce margin rejects or invalid-close churn.

### Logic change
- Before issuing any forced close on an option/spread leg:
  - cancel all linked OCO siblings and mark close-intent lock.
- During close-intent window:
  - suppress creation of new protective siblings for that symbol/spread.
- If close reject occurs:
  - immediate cleanup of stale sibling IDs, then retry on clean state.

### Required outcomes
- No repeated force-close `Insufficient buying power` cluster tied to stale OCO siblings.
- One active close-intent per symbol/spread at a time.

---

## D2) Assignment Containment Final Guard (`T-19`)

### Problem
- Assignment path can still leak into residual QQQ equity liquidation scenarios.

### Logic change
- Add end-of-cycle containment sweep:
  - detect residual assigned equity created by option assignment paths,
  - force reconcile to policy target for isolation mode (flat unless explicitly intended).
- Add explicit guardrail log code for each containment action.

### Required outcomes
- No untracked residual QQQ equity after assignment events.
- Assignment cleanup always terminates to expected portfolio state.

---

## D3) Spread Lifecycle Telemetry Completion (`T-36`)

### Problem
- `E_*`/`R_*` clarity improved, but spread lifecycle observability is still incomplete.

### Logic change
- Add canonical lifecycle counters keyed by spread key:
  - `ENTRY_SIGNAL`, `ENTRY_SUBMIT`, `ENTRY_FILLED`,
  - `EXIT_SIGNAL`, `EXIT_SUBMIT`, `EXIT_FILLED`, `EXIT_CANCELED`,
  - `POSITION_REMOVED`.
- Emit end-of-day reconciliation:
  - mismatch report for any spread key that has entry without terminal exit/removal state.

### Required outcomes
- Full auditable path per spread from open to close/removal.
- No silent lifecycle holes.

---

## D4) Zero/Invalid Price Submit Guard

### Problem
- Spread entry attempts can still hit invalid submits with zero/invalid quote legs.

### Logic change
- Final pre-submit quote validation in router path for spread entries:
  - require positive executable quote for both legs (`ask>0` buy leg, `bid>0` sell leg),
  - reject with explicit `R_CONTRACT_QUOTE_INVALID` code.
- Do not let invalid quote setups reach broker submit stage.

### Required outcomes
- Zero spread entry submits with invalid/zero executable quotes.
- Invalid submissions shift to explicit pre-submit rejects.

---

## D5) Close-Retry Escalation Contract

### Problem
- Repeated canceled close attempts can loop without deterministic terminal handling.

### Logic change
- Add bounded retry budget per spread close intent:
  - retries at configured interval,
  - after budget exhaustion -> escalation path (`market/legged emergency close`),
  - if still not flat -> terminal alert + locked-safe mode for that spread key.

### Required outcomes
- Every close intent reaches one terminal state: `FILLED`, `ESCALATED_FILLED`, or `SAFE_LOCK_ALERT`.
- No infinite retry loops.

---

## D6) BEAR_PUT Assignment Gate Recalibration (`O-13`)

### Problem
- Bear-put participation remains blocked too often by assignment gate in stress contexts.

### Logic change
- Keep assignment gate, but make it regime/volatility-aware:
  - stress regime/high VIX uses relaxed OTM threshold,
  - calm/low-VIX keeps stricter threshold.
- Add rejection code detail to separate gate strictness from contract scarcity.

### Required outcomes
- Lower `BEAR_PUT_ASSIGNMENT_GATE` share in bear runs.
- Increased bearish spread participation without ITM assignment blowups.

---

## D7) Micro Candidate->Approved Funnel Tuning (`O-14`)

### Problem
- Candidate->approved bottleneck remains severe due to over-strict blockers.

### Logic change
- Small-threshold tuning only (no new strategy logic):
  - reduce `CONFIRMATION_FAIL` strictness modestly,
  - relax `QQQ_FLAT` floor slightly,
  - reduce `VIX_STABLE_LOW_CONVICTION` hard blocks where safe.
- Keep stress CALL gates intact.

### Required outcomes
- Candidate->approved conversion improves materially.
- No bull-run collapse from over-loosening.

---

## D8) VASS Strategy-Mix Rebalance (`O-21`)

### Problem
- VASS remains over-bullish in stress/choppy windows.

### Logic change
- Reweight selector preference by overlay/regime state:
  - stress: suppress bullish debit priority, increase bearish strategy priority,
  - recovery: re-enable bullish ladder progressively.
- Keep anti-cluster and slot fairness active.

### Required outcomes
- Lower bullish spread share in bear/choppy windows.
- Improved regime-consistent direction mix.

---

## D9) Trade-Limit Policy Tuning

### Problem
- Hard daily limits (`TRADE_LIMIT_BLOCK`, `E_INTRADAY_TRADE_LIMIT`) block valid entries.

### Logic change
- Keep absolute hard cap but add regime-aware soft allocation:
  - reserve budget slices for swing and intraday paths,
  - allow unused reserved budget rollover later in day.

### Required outcomes
- Fewer valid-signal blocks from static daily caps.
- No runaway overtrading.

---

## D10) Per-Expiry Concentration Cap

### Problem
- Concentration in same expiry increases tail risk and correlated losses.

### Logic change
- Add max concurrent spreads per expiry bucket.
- Apply after anti-cluster checks and before submit.
- Reject with explicit code (e.g., `R_EXPIRY_CONCENTRATION_CAP`).

### Required outcomes
- Lower same-expiry clustering.
- Better spread ladder diversification.

## Implementation Sequence

### Phase A: Correctness + Plumbing (must-pass)
1. VIX direction scoring one-line fix.
2. Anti-churn guard (main + engine mirror).
3. VASS anti-cluster entry control (15m + 3d signature gating).
4. `E_*` / `R_*` telemetry hardening.
5. D1 force-close/OCO race hardening.
6. D2 assignment containment final guard.
7. D3 spread lifecycle telemetry completion.
8. D4 zero/invalid quote submit guard.
9. D5 close-retry escalation contract.

### Phase B: Regime Responsiveness
10. Intraday regime refresh.
11. Overlay threshold tuning (config only).

### Phase C: Churn Reduction
12. Neutrality staged de-risk.

### Phase D: Optimization Tighteners (minimal-change)
13. D6 BEAR_PUT assignment gate recalibration.
14. D7 Micro candidate->approved funnel tuning.
15. D8 VASS strategy-mix rebalance.
16. D9 trade-limit policy tuning.
17. D10 per-expiry concentration cap.

## Acceptance Criteria (Go/No-Go)

1. No non-emergency same-timestamp spread open+close churn.
2. No repeated VASS signature entries within 15 minutes.
3. No repeated VASS signature entries within 3-day cooldown.
4. Unknown/no-signal drop bucket materially reduced.
5. Force-close margin/OCO reject cluster materially reduced.
6. No residual assignment-equity leaks post reconciliation.
7. Spread lifecycle counters reconcile (entry->exit->removed) with no silent holes.
8. Same-day regime stress changes reflected in options gating.
9. Neutrality re-entry churn reduced.
10. `BEAR_PUT_ASSIGNMENT_GATE` share reduced in bear runs.
11. Candidate->approved funnel improves without bull regression.
12. No regression in assignment and hard-risk safety exits.

## Backtest Validation Order

1. 2017 (quick sanity): verify no bull-market collapse.
2. 2022 Dec-Feb: verify stress adaptation + reduced wrong-way churn.
3. 2018 Q4: verify choppy churn control.

## Deliverables After Approval

1. Code changes for all in-scope items.
2. `docs/audits/OPTIONS_ENGINE_MASTER_AUDIT.md` updates with status and evidence.
3. Post-run validation note with funnel and churn before/after deltas.
