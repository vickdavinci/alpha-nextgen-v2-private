# Options Backtest Audit Summary (0-2 DTE Focus)

This document summarizes the earlier code audit findings for alpha-nextgen-v2, focusing on
options backtesting (especially 0-2 DTE intraday mode) and mismatches vs documented design.
It also includes a concrete task list to remediate the issues and improve validation of the
options chain in the next backtest.

## Executive Summary

The codebase contains multiple divergences from the documented V2.3 options design that can
cause intraday options to never trigger, force exits to be skipped, and swing options to trade
outside intended regime or DTE boundaries. The most impactful issues are: missing intraday
position tracking (which breaks 15:30 force close), regime-based gating not applied in swing
entries, debit-spread swing logic not implemented, and backtest data constraints for 0-2 DTE
contracts (which QC often lacks). Fixing these issues is essential to validate the options
chain in the next backtest.

## Findings (Design vs Code Gaps and Bugs)

1. **Intraday positions are not tracked, so 15:30 force exit never fires.**
   - The intraday force-exit handler checks `_intraday_position`, but entries register only
     `_position`. This means intraday positions are never marked as intraday and are not
     force-closed at 15:30.
   - Risk: 0-2 DTE positions can be carried into the close or overnight in backtests.

2. **Swing direction selection and regime gating diverge from design.**
   - Design: swing direction should be chosen by regime score thresholds (e.g., >60 bull,
     <45 bear, 45-60 no trade).
   - Code: swing direction uses MA200/RSI and `check_entry_signal` defaults to a regime score
     of 50, effectively bypassing regime gating.
   - Risk: swing entries occur in regimes that should be blocked or hedged.

3. **Swing mode uses single-leg options, not debit spreads.**
   - Design: swing mode (V2.3) is debit spreads only.
   - Code: selects a single option contract and submits single-leg entries with OCO exits.
   - Risk: backtest performance and risk are not aligned with the spec.

4. **Swing DTE boundaries and validation do not match the spec.**
   - Design: swing DTE is 10-21.
   - Code: swing selection uses 5-45, and entry validation uses global 0-45 DTE checks.
   - Risk: options are selected outside the intended swing horizon.

5. **Allocation and entry-score thresholds diverge from documented values.**
   - Design: 20% total options allocation (15% swing / 5% intraday), minimum entry score 3.0.
   - Code: 25% allocation and entry score minimum 2.0 (lowered for testing).
   - Risk: sizing and signal thresholds are materially different from the documented system.

6. **Intraday entry testing is blocked by QC historical data constraints.**
   - QC backtests often lack 0-2 DTE contracts or provide missing Greeks/bid-ask, so contracts
     are filtered out before entry signals are evaluated.
   - Risk: intraday options never trigger, preventing chain validation in backtests.

## Recommended Tasks to Fix and Validate Options Backtesting

### A) Intraday Position Tracking + Force Exit

- Add a mode flag to options positions (or track `_intraday_position` explicitly).
- When an intraday entry signal fires, store the position as intraday.
- Ensure `check_intraday_force_exit` and the 15:30 scheduled handler reference the correct
  intraday position state.

### B) Align Swing Direction and Regime Gating

- Replace MA200/RSI direction selection for swing with regime-score thresholds per V2.3.
- Pass the actual regime score into `check_entry_signal` so gating applies.

### C) Implement Debit Spreads for Swing Mode

- Add a spread selector (long/short legs) aligned with the documented debit-spread logic.
- Update order placement to submit combo orders and adjust OCO to manage spread exits.
- Update position tracking to handle multi-leg risk/exit accounting.

### D) Enforce V2.3 DTE Boundaries in Selection and Validation

- Set swing DTE to 10-21 in config.
- Validate DTE by mode (intraday vs swing), not by the global min/max.

### E) Normalize Allocation + Entry Score Thresholds

- Align options allocation to 20% total (15% swing / 5% intraday).
- Set entry score minimum back to 3.0 to match the spec.

### F) Make QC Backtests Viable for 0-2 DTE

- Add diagnostic logging to the intraday contract selector:
  - Count how many 0-2 DTE contracts exist in the chain.
  - Log why candidates fail filters (OI, spread, missing Greeks).
- If QC data lacks 0-2 DTE, temporarily expand intraday DTE to 0-5 for backtests.
- Add fallback delta logic when Greeks are missing in historical data.

## Suggested Validation Plan for the Next Backtest

1. **1-day backtest**: confirm intraday contracts are detected and entries can fire.
2. **7-day backtest**: verify intraday force exit at 15:30 and OCO behavior.
3. **30-day backtest**: validate options chain selection, win rate, and position limits.

## Outcome Goal

After completing the tasks above, the next backtest should reliably exercise intraday 0-2 DTE
entries, validate the options chain selection logic, and enforce documented risk/exit behavior.

# Red Team Code Audit - System Integrity Review

Audience: Solo trader operating Alpha NextGen V2.

Scope: Data In -> Engine -> Signal -> Router -> Execution.

Focus: System integrity, execution safety, and Python best practices.

## Findings

### Restart Vulnerability
- State restoration in `main.py` uses `StateManager.load_all()` without passing engine instances, so capital/risk/positions are not restored on restart (state is effectively dropped).
- Options and OCO state are saved manually to ObjectStore as `str(...)` but never reloaded.
- `StateManager.load_all()` ignores execution and router state (no execution/router restore in `load_all`).

### Stale Data Traps
- Micro regime/VIX spike checks can operate on stale `_current_vix` if no fresh VIX bar arrived before scheduled checks.
- Options intraday force-exit uses entry price as a fallback “current price,” which can suppress a forced exit or trigger at the wrong price when data is stale.

### Floating Point Precision Risks
- Portfolio router uses float math for target values and truncates via `int()`, which can zero out a small-but-legitimate delta (or flip a near-zero negative to 0) causing missing or malformed orders.

### Execution Edge Cases
- OCO manager does not handle partial fills; it assumes a fill closes the whole pair and immediately cancels the other leg.
- Execution engine does not reconcile partial fill + cancel (no residual quantity tracking), potentially leading to double-sell or fallback mis-sizing.

### Complexity Debt
- MicroRegimeEngine is large and tightly coupled to options intraday logic; consider consolidating or simplifying to reduce operational risk for a solo developer.

## Recommendations (High Level)
- Wire `StateManager.load_all()` with actual engine instances and add explicit restore for Options/OCO state with JSON parsing.
- Add timestamp freshness checks around micro regime updates and VIX spike inputs.
- Replace `int()` truncation with explicit rounding and min-quantity checks; guard against float epsilon errors.
- Add partial-fill tracking and remaining-quantity logic in OCO and Execution engines.
- Simplify micro regime logic to a smaller set of regimes or replace with a single intraday gating function.
