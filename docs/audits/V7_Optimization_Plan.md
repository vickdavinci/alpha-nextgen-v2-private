# V7 Optimization Plan

## Goal
Improve V7 performance with minimal code changes by reducing low-quality clustered VASS entries, improving spread slot quality, and preserving bear-market protections.

## Observed Problem (From Stage7 Jul-Sep 2017)
- VASS entries are clustered too tightly in time.
- Same spread structures are entered repeatedly in short windows.
- Bullish spread slots get filled early, reducing later high-quality opportunities.
- Overlay did not fire in low-VIX bull periods, so losses are primarily pacing/quality related, not overlay-block related.

## Evidence Snapshot
- `VASS_ENTRY` total: 37
- Consecutive VASS entries within 15 minutes: 15
- Same-symbol consecutive VASS entries within 15 minutes: 12
- Multi-entry clusters observed on the same morning (10:00 / 10:15 / 10:30)

## Optimization Principles
1. Keep architecture unchanged.
2. Avoid adding broad new features.
3. Improve entry quality before increasing capacity.
4. Keep existing risk protections (stress gates, close escalation) intact.

## Plan Changes

### O1. VASS Directional Cooldown Tightening
- Current behavior allows fast re-entries.
- Change:
  - Keep short anti-burst cooldown for retry noise only (45-60 minutes).
  - Do not rely on intraday cooldown for primary pacing control.
- Expected impact:
  - Reduces duplicate retry bursts without suppressing valid regime trades.

### O2. Progressive Conviction for Same-Day Adds
- Current behavior allows repeated same-direction additions at similar conviction.
- Change:
  - 1st same-direction spread of day: base conviction threshold.
  - 2nd same-direction spread: require stronger conviction (e.g., +30%).
  - 3rd same-direction spread: require strongest conviction or block.
- Expected impact:
  - Preserves high-confidence pyramiding while blocking low-edge adds.

### O3. Duplicate Structure Guard
- Current behavior can re-enter nearly identical structures rapidly.
- Change:
  - Block new VASS entry when long/short strikes match an open or recently opened spread within cooldown window.
- Expected impact:
  - Prevents over-concentration in identical spread risk.

### O4. Slot Policy + Rolling Window Lock (Regime-Aware)
- Current issue: early same-type entries consume slots and block later opportunities.
- Change:
  - `OPTIONS_MAX_SWING_POSITIONS = 4`
  - `OPTIONS_MAX_SWING_PER_DIRECTION = 2`
  - Add rolling lock per `strategy+direction` key:
    - 2 trading days in `RISK_ON` and `RISK_OFF`
    - 3 trading days in `NEUTRAL`, choppy, and stress states
  - Lock keys are independent for each strategy+direction:
    - `BULL_CALL_DEBIT`, `BEAR_CALL_CREDIT`, `BEAR_PUT_DEBIT`, `BULL_PUT_CREDIT`
  - No lock sharing across opposite directions.
- Expected impact:
  - Enables staggered participation in trends.
  - Reduces same-strategy burst stacking.
  - Improves behavior in choppy windows via longer lock.

### O5. Position-Aware Re-Entry Gate
- Pure time cooldown can loop daily on same strategy.
- Change:
  - If same `strategy+direction` spread is open, block new entry by default.
  - Optional override only when existing position is protected (profit-lock state) and new conviction is stronger.
- Expected impact:
  - Prevents repetitive stacking while preserving selective adds.

### O6. Keep Existing Exit Plumbing Protections
- Preserve and monitor existing mechanisms:
  - spread close escalation
  - retry/cancel handling
  - forced close protections
- Expected impact:
  - Avoid reintroducing tail-loss plumbing regressions.

## What Not To Change In This Round
- Do not add new regime engines.
- Do not remove overlay system.
- Do not loosen bear-market assignment guard without separate validation.

## Validation Backtest Sequence
1. 2017 Jul-Sep (funnel and bull quality baseline)
2. 2022 Dec-Feb (bear stress sanity)
3. 2018 choppy window (whipsaw behavior check)

## Success Criteria
- Fewer clustered same-day VASS entries.
- Lower repeated same-symbol/same-structure entries.
- Improved net P&L in 2017 baseline window.
- No regression in 2022 tail-risk protections.
- No increase in close-order plumbing failures.

## Metrics To Track
- VASS entries/day and inter-entry minutes.
- Duplicate-structure blocks count.
- Conviction tier distribution for 1st/2nd/3rd same-direction entries.
- Slot saturation reasons (`R_SLOT_DIRECTION_MAX` trend).
- Rolling lock blocks by regime (`2d` vs `3d`).
- Net P&L and fee-adjusted P&L by month.

## Rollout Strategy
- Implement O3 + O4 first (highest leverage for clustering/slot pressure).
- Add O1 and O5 next (execution pacing hardening).
- Add O2 last (conviction ladder fine-tuning).
- Re-evaluate slot policy only after multi-regime validation.
