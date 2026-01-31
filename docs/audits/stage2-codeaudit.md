I have performed a deep-dive architectural audit of the alpha-nextgen-v2 codebase against the V2.1/V2.3 design specifications.

Bottom Line Up Front: The backtest is failing and results are erratic because the implementation is fundamentally trading "Naked Options" using "Allocation Sizing," while the documentation explicitly mandates "Debit Spreads" using "Risk Sizing."

You are currently exposing the portfolio to undefined risk (naked long calls/puts) and overriding the sophisticated risk-management sizing logic with a brute-force allocation percentage.

Here is the itemized report of Logic Pitfalls, Code Errors, and the Fix Plan.

1. Critical Logic Pitfalls (Design vs. Implementation)
A. The "Spread" Gap (Major Architecture Failure)
Design Doc (18-options-engine.md): Explicitly states "Strategy: DEBIT SPREADS (Simplified V2.3)". It details buying a Long Leg and selling a Short Leg to cap max loss and survive whipsaws.

Code Base (options_engine.py): The code selects a single best_contract.

_select_swing_option_contract: Filters for ONE contract (the long leg).

check_entry_signal: Registers ONE contract.

TargetWeight: Requests allocation for ONE symbol.

The Consequence: The algorithm is buying Naked Calls/Puts.

Impact: A -0.36% move in QQQ triggers stops (as calculated in your own design doc), whereas a spread would survive a -1.0% move. You are getting stopped out by noise because the "hedge" (short leg) is missing.

B. The "Sizing" Disconnect (Risk Logic Ignored)
Design Doc: "Confidence-Weighted Tiered Stops... contracts = floor(allocation / (entry_price * 100 * stop_pct))". The engine calculates a specific number of contracts (e.g., 4) based on risk tolerance.

Code Base:

options_engine.py calculates num_contracts correctly in calculate_position_size, logs it, stores it in _pending_num_contracts... and then throws it away.

It returns TargetWeight(target_weight=1.0).

portfolio_router.py receives 1.0. It applies the source limit (e.g., 25% of portfolio).

Router Logic: Contracts = (Total Equity * 25%) / Option Price.

The Consequence:

Scenario: Risk Engine calculates safe size is 4 contracts ($2,000 exposure).

Reality: Router calculates 25% of $50k = $12,500. It buys 25 contracts.

Result: You are taking 6x the intended risk. One stop-out wipes out 6x the acceptable loss limit.

C. Intraday Mode Mismatch
Design Doc: Intraday strategies include "Credit Spreads" and "Debit Fade".

Code Base: The check_intraday_entry_signal logic attempts to implement these directions but, like the Swing mode, maps them to single option contracts (Naked Longs). "Credit Spread" logic in the code appears to just be "Sell a Naked Option" (or buy a Put if the logic flips), which is extremely dangerous if not intended.

2. Specific Code Errors & Bugs
1. portfolio_router.py: Greeks Monitoring Failure
Location: _monitor_risk_greeks

Bug: Checks self.options_engine.has_position(). Since the system is trading naked options but the logic might expect a spread (conceptually), the Greeks calculated (delta, theta) are for a single leg.

Impact: A naked long call has significantly higher Theta decay and Delta exposure than a Vertical Spread. Your "Level 5 Circuit Breaker" will trigger constantly because it sees the massive unhedged Greeks of the naked position.

2. main.py: Option Chain Validation Race Condition
Location: _generate_options_signals

Bug: You added a check _validate_options_symbol, but CurrentSlice.OptionChains lookup in _select_swing_option_contract can still fail or return an empty chain during warm-up or data gaps, causing the engine to silently skip entries.

Fix: Ensure SetWarmUp includes Options data resolution or handle empty chains explicitly with a retry counter, not just a return.

3. options_engine.py: VIX Direction Logic
Location: MicroRegimeEngine.classify_vix_direction

Bug: It compares vix_current to vix_open.

Issue: If VIX gaps up significantly at open but trades flat afterwards, vix_change_pct remains high all day. The design implies "Intraday Trend" (Direction), not just "Gap from yesterday".

Fix: VIX Direction should likely weigh current - 30min_ago more heavily than current - open for intraday decisions.

3. Immediate Remediation Plan (For Next Backtest)
To validate the options chain in the next backtest, you must fix the Spread and Sizing issues.

Step 1: Implement "Virtual Spreads" (Quickest Fix)
Since PortfolioRouter logic is hard to rewrite to support multi-leg orders (OptionStrategy) immediately:

Modify OptionsEngine:

Instead of TargetWeight for best_contract, emit TargetWeight for TWO symbols:

TargetWeight(LongLegSymbol, weight=X)

TargetWeight(ShortLegSymbol, weight=-Y)

Modify PortfolioRouter:

Ensure it can process negative weights for Options as "Sell to Open" (currently it might clamp negatives to 0 or treat as "Close").

CRITICAL: You must allow negative target weights for Options in validate_weights.

Step 2: Fix the Sizing Bug
Modify TargetWeight Class: Add a field requested_quantity (Optional[int]).

Update OptionsEngine: Pass the calculated num_contracts into this field.

Update PortfolioRouter:

Check if requested_quantity is present.

If yes, use that as the quantity.

If no, fall back to the percentage-based calculation.

Step 3: Sanity Check Configuration
Config: Set OPTIONS_TOTAL_ALLOCATION to a hard dollar limit or ensure the num_contracts calculation logic is the primary driver in the Router.

Summary: The code is currently a "Naked Option Buying" engine with "Portfolio % Sizing". The design is a "Vertical Spread" engine with "Risk % Sizing". These are two different products. Aligning them is the only way to get a valid backtest.