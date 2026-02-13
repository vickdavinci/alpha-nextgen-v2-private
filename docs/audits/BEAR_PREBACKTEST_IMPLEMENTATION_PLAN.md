# Bear Pre-Backtest Implementation Plan
## Scope: Fix Execution/Direction Failures Before Next 2022 Run

**Goal:** Remove primary bear-market loss drivers with minimal code changes and no new feature sprawl.

## A. Mandatory Fixes (Implement First)

### A1) VASS Direction Guard in Bear Conditions
- Block `BULL_CALL_*` entries when:
  - `QQQ < MA50` **and**
  - `regime_score < 60`
- Keep existing bear-capable spread paths active (`BEAR_CALL_CREDIT`, `BEAR_PUT_*` as configured).

### A2) Conviction Clamp in Elevated VIX
- When `VIX > 18` and macro is `NEUTRAL`:
  - disallow bullish VASS conviction override.
- Bearish override remains allowed.

### A3) Debit Spread Tail-Loss Cap
- Enforce hard stop at `-35% of spread width` for debit spreads.
- Route hard-stop exits through forced close retry flow (no bypass).

## B. Stop-Miss Plumbing Fixes (Must Pass)

### B1) Quote Fallback for Exit Evaluation
- Current behavior skips spread exit cycle when one leg quote is missing.
- Implement fallback mark logic:
  - bid/ask mid if available,
  - else last trade,
  - else previous valid mark cache for that leg.
- Never silently skip without telemetry.

### B2) Debit Time Stop
- Add debit-only max hold window:
  - `VASS_DEBIT_MAX_HOLD_DAYS = 7`.
- Applies to `BULL_CALL_DEBIT` and `BEAR_PUT_DEBIT` only.
- Credit spreads unchanged.

### B3) Exit Reason Telemetry
- Add/confirm explicit reasons:
  - `SPREAD_EXIT_SKIPPED_NO_QUOTE`
  - `SPREAD_HARD_STOP_TRIGGERED`
  - `SPREAD_TIME_STOP_7D`
  - `SPREAD_CLOSE_RETRY`
  - `SPREAD_CLOSE_ESCALATED`
  - `SAFE_LOCK_ALERT`

## C. Guardrail Validation (Pre-Run)

### C1) Compile + Sync
- Compile check for touched files.
- Verify Lean workspace and repo are synchronized.

### C2) Short Smoke Backtest
- Run short window (5-10 trading days) to validate:
  - No runtime errors.
  - No same-timestamp entry/exit anomaly.
  - Hard-stop and time-stop generate exits with valid reasons.
  - Forced-close retry path functions when simulated cancellation occurs.

### C3) Metrics Must Pass
- `Unknown/Generic drop` reasons near zero.
- Spread exits contain reason codes and fill/retry trace.
- No spread remains open past debit max-hold unless forced by market closure edge-case.

## D. Backtest Acceptance Criteria (Dec-Feb 2022)

### D1) Technical
- Zero runtime/plumbing exceptions.
- Zero silent spread-exit skips (all skips have reason code).

### D2) Risk
- No catastrophic debit spread tail losses beyond hard-stop design intent.
- Reduction in largest single-loss magnitude vs prior baseline.

### D3) Behavior
- Lower VASS bullish spread count during bear periods.
- No bullish conviction override in `NEUTRAL + VIX > 18` state.

## E. Change Control Rules

1. Implement sections A+B only before next 2022 run.
2. Do not add unrelated tuning knobs in same patch.
3. Evaluate on single A/B run, then decide next tuning step.

---

## F. Must-Implementation Matrix (Aligned to V8 Pre-2022 Checklist)

This section maps the mandatory pre-run checklist into explicit implementation requirements for this patch cycle.

### F1) Baseline and Reproducibility
- Freeze baseline commit tag before code changes.
- Record branch, HEAD hash, config snapshot in run notes.

### F2) Workspace and Compile Integrity
- Ensure repo and Lean workspace files are synchronized for:
  - `main.py`
  - `engines/satellite/options_engine.py`
  - `portfolio/portfolio_router.py`
  - `engines/core/regime_engine.py`
  - `config.py`
- Run compile checks for all touched files.

### F3) Spread Exit Plumbing (Must Pass)
- Keep OCO cancel before forced spread close.
- Keep retry + escalation + emergency close path.
- Keep terminal safe-lock alert/retry behavior.
- Prevent repeated close submissions without state transition.

### F4) Scheduler and Reconciliation Integrity
- Keep duplicate schedule guards active.
- Keep intraday reconciliation checkpoints active.
- Ensure zombie/orphan cleanup validates live holdings before liquidation/removal.

### F5) Regime Data Integrity
- Keep intraday regime refresh read-only (non-mutating path).
- Prevent duplicate mutating regime recalculation from summary path.
- Preserve correct lookback semantics for VIX and breadth histories.

### F6) VASS Execution Controls
- Keep anti-churn same-direction + same-DTE 15-minute guard.
- Keep 3-day cooldown for same spread signature.
- Keep separate bullish/bearish slot caps.
- Keep stress gate that blocks bullish spread entries.

### F7) Risk Gates and Limits
- Confirm WIN_RATE_GATE mode is intentional and logged.
- Confirm DTE risk-off exits are active pre-expiry (not DTE=1 only).
- Confirm daily options trade limits and position caps match intended run profile.

### F8) Telemetry and Diagnostics
- Emit specific engine rejection codes (`E_*`).
- Emit specific router rejection codes (`R_*`).
- Avoid generic/unknown drops for known paths.
- Preserve explicit entry/exit reason logs for both MICRO and VASS.

### F9) Capital and Allocation Sanity
- Starting cash `100000`.
- Options allocation `50%`.
- Engine allocations sum to `100%`.
- Margin assumptions documented before run.

### F10) Bull-Regression Guardrails (Do Not Break While Fixing Bear)
- Bear-only changes must remain regime-scoped.
- Bull profile behavior must not be globally altered.
- Keep DEBIT_FADE preference and anti-cluster controls intact.

### F11) Smoke and Acceptance Gates
- Short smoke run must pass with zero runtime exceptions.
- No same-timestamp spread entry/exit anomaly.
- Spread close logs must show fill/cancel-retry traceability.
- Tail-loss and rejection-mix acceptance thresholds must be met before full run.

---

**Execution Order:** `F1/F2 → A1/A2/A3 → B1/B2/B3 → F3/F4/F5/F6/F7/F8/F9/F10 → C1/C2/C3 → D`
