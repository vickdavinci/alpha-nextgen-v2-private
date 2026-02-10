# V6.15 Remediation Proposal

## Objective
Fix participation leaks and improve bear-market profitability with minimal logic changes and clear validation gates.

## Baseline Findings Used for This Plan
- 2022 Dec-Feb: CALL losses are still concentrated and dominate drawdown.
- 2015 Jul-Sep NoSync: `Dir=NONE` remains high (72.6%), PUT spreads are frequently blocked by `BEAR_PUT_ASSIGNMENT_GATE`, and approved->dropped path remains opaque.
- 2015 Jul-Sep NoSync: PRE_MARKET_SETUP callback type crash and intraday force-exit fallback close-side regression are still present in baseline logs.
- 2017 Jul-Sep NoSync: August loss cluster was amplified by force-close non-idempotent behavior (`INTRADAY_FORCE_EXIT_FALLBACK` + inflated OCO recovery quantity + next-day orphan liquidation).

---

## Pass 1: Execution Safety Contract (P0)
### Goal
Stabilize execution plumbing first: no callback crashes, no wrong-side closes, no opaque router rejects.

### Changes
- Normalize and guard symbol type in PRE_MARKET_SETUP (`T-31`) so callback never throws on option symbol objects.
- Enforce reduce-only close semantics in intraday force-exit fallback (`T-32`):
  - derive close side from actual holding sign only
  - reject close intent if quantity cannot be proven from holdings
- Replace generic `DROP_ROUTER_REJECT` with canonical router reject codes (`T-33`) and mirror checks before approval:
  - slot/counter limit
  - cooldown
  - min trade value
  - margin/cap headroom
  - close quantity safety
- Add strict force-close idempotency guard (`T-34`/`T-35`):
  - mark symbol as `close_in_progress` after first force-close intent
  - block new OCO creation/recovery for that symbol until position is flat
  - disallow additional entry/close submits for same symbol in force-close window
  - enforce quantity invariant: `requested_close_qty <= abs(current_holdings_qty)`
  - if invariant fails, emit hard error code and skip submit (no synthetic recovery orders)

### Expected Outcome
Execution path is deterministic and diagnosable; approved signals are either submitted or dropped with explicit local reason.
No position amplification from force-close workflow.

---

## Pass 2: PUT Path Reliability and Profitability (P0/P1)
### Goal
Improve bear capture while reducing PUT-side tail losses.

### Changes
- Keep `ITM_MOMENTUM` as primary bearish alpha path (no strategy expansion).
- Recalibrate `BEAR_PUT_ASSIGNMENT_GATE` (`O-13`) from hard-block behavior to risk-aware filter:
  - keep hard block only when combined ITM + DTE + margin-risk conditions are truly high risk
  - allow moderate-risk bear put debit candidates with reduced size instead of full rejection
- Add low-IV branch handling to avoid effectively disabling PUT spreads in bullish/chop months (`O-15`):
  - if assignment risk is low/moderate, prefer reduced-size over hard reject
  - preserve hard reject only in high-assignment-risk states
- Reduce protective-put drag:
  - reduce protective position size
  - tighten protective stop
  - keep strict no-overnight policy for pure intraday protective contracts

### Expected Outcome
Higher PUT participation in bear windows with controlled assignment risk; less protective theta bleed.

---

## Pass 3: CALL Bleed Containment (Minimal, Not Feature Heavy)
### Goal
Prevent bear-regime CALL losses while preserving bull participation.

### Changes
- Keep existing CALL gates but make sure they are uniformly applied to:
  - Micro CALL entries
  - VASS bullish spread entries
- Preserve bull participation by making gates conditional, not permanent:
  - trend recovers -> gates auto-release
  - no blanket CALL shutdown in neutral/bull

### Expected Outcome
Reduced Jan-2022 style CALL spread drawdowns.

---

## Pass 4: Participation Tuning (Small-Step Only)
### Goal
Increase participation without opening noise floodgates.

### Changes
- Keep matrix logic intact; only adjust thresholds incrementally:
  - `QQQ_NOISE_THRESHOLD` (small reduction)
  - `INTRADAY_QQQ_FALLBACK_MIN_MOVE` (small reduction)
  - optional single-step conviction threshold adjustment if `Dir=NONE` remains above 60%
- Do not alter core resolver hierarchy in this pass.

### Expected Outcome
Controlled increase in conversion from signal to trades.

---

## Validation Matrix (Must Pass)
### Runs
- 2022 Jan-Feb: bear validation
- 2017 sample: bull-regression check
- 2015 Aug-Sep: choppy/crash robustness

### KPIs
- approved → executed conversion rate
- PUT expectancy by strategy
- CALL tail-loss concentration (top 5 losses)
- dropped-reason distribution with explicit codes (low unknown)
- PRE_MARKET_SETUP callback errors (must be zero)
- force-exit fallback wrong-side submissions (must be zero)
- force-close quantity amplification events (must be zero)
- VASS PUT rejection share from `BEAR_PUT_ASSIGNMENT_GATE` (must materially decline)

---

## Implementation Order
1. Pass 1 (Execution safety contract)
2. Pass 2 (PUT path reliability/profitability)
3. Quick 2022 Jan-Feb run
4. Pass 3 (CALL gate parity check)
5. Pass 4 (participation threshold tuning)
6. Full validation matrix
