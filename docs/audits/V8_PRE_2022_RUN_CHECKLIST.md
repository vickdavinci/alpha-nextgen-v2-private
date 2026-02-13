# V8 Pre-2022 Run Checklist

Use this checklist immediately before launching the Dec-Feb 2022 backtest.

## 1) Baseline and Reproducibility

- [ ] Freeze baseline commit and tag it (example: `v8-pre-2022`)
- [ ] Confirm exact branch and HEAD commit hash in notes
- [ ] Store run config snapshot with date/time

## 2) Workspace and Code Sync

- [ ] Verify Lean workspace sync for:
  - [ ] `main.py`
  - [ ] `engines/satellite/options_engine.py`
  - [ ] `portfolio/portfolio_router.py`
  - [ ] `engines/core/regime_engine.py`
  - [ ] `config.py`
- [ ] Compile sanity check:
  - [ ] `python -m py_compile main.py`
  - [ ] `python -m py_compile engines/satellite/options_engine.py`
  - [ ] `python -m py_compile portfolio/portfolio_router.py`
  - [ ] `python -m py_compile engines/core/regime_engine.py`

## 3) Spread Exit Plumbing (Must Pass)

- [ ] OCO cancel runs before forced spread close
- [ ] Forced close retry path is active
- [ ] Emergency close fallback is active after retry max
- [ ] Terminal safe-lock/alert triggers if close still fails
- [ ] No repeated close attempts without state transition

## 4) Scheduler and Reconciliation Integrity

- [ ] Duplicate schedule guard prevents double-fire events
- [ ] Reconciliation runs intraday (not only once/day)
- [ ] Zombie/orphan cleanup confirms actual holdings before action

## 5) Regime Data Integrity

- [ ] Intraday regime refresh is read-only (no mutation of daily state)
- [ ] No extra mutating regime recalculation from daily summary path
- [ ] VIX/breadth history windows remain correct length/lookback

## 6) VASS Execution Controls

- [ ] Anti-churn guard active for same direction + same DTE within 15 min
- [ ] 3-day cooldown for same spread signature active
- [ ] Separate bullish and bearish slot caps configured
- [ ] Stress gate blocks bullish spread entries when active

## 7) Risk Gates and Limits

- [ ] WIN_RATE_GATE mode is intentional (hard block vs reduced size)
- [ ] DTE risk-off exits are active before expiry (not only DTE=1)
- [ ] Daily options trade limits match intended test profile
- [ ] Position caps match intended test profile

## 8) Telemetry and Diagnostics

- [ ] Engine rejection telemetry emits specific `E_*` codes
- [ ] Router rejection telemetry emits specific `R_*` codes
- [ ] No unknown/generic drop code for known paths
- [ ] Entry/exit reason logging present for both MICRO and VASS

## 9) Capital and Allocation Sanity

- [ ] Starting cash is `100000`
- [ ] Options allocation is `50%`
- [ ] Engine allocations sum to `100%`
- [ ] Margin assumptions documented in run notes

## 10) Bull Market Tuning Guardrails (Regression Protection)

- [ ] Bull-only tuning is regime-scoped (never applied globally)
- [ ] Bull profile activates only in explicit bull regime conditions
- [ ] MICRO PUT suppression in bull profile is enabled (for low-stress bull states)
- [ ] DEBIT_FADE preference is enabled in bull/chop-friendly states
- [ ] DEBIT_MOMENTUM entry is tightened vs FADE in bull profile
- [ ] VASS same-direction/same-DTE re-entry guard remains active (anti-cluster)
- [ ] Bullish spread cooldown window is active to avoid same-day overstacking

## 11) Pre-Run Smoke Test

- [ ] Short-date smoke run completes without runtime exceptions
- [ ] No same-timestamp entry/exit anomaly on new spreads
- [ ] Spread close path shows actual fill/cancel-retry behavior in logs

## 12) Acceptance Targets for This Run

- [ ] Zero runtime/plumbing errors
- [ ] Spread close success rate meets target
- [ ] Rejection mix dominated by intentional caps/cooldowns (not unknown)
- [ ] Tail-loss per spread within defined threshold

## Run Metadata (Fill Before Launch)

- Date:
- Branch:
- Commit:
- Config version:
- Backtest window:
- Operator:
