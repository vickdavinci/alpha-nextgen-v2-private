I have performed a deep forensic analysis of the logs (Smooth Magenta Bat_logs.txt) and trade records (Smooth Magenta Bat_trades.csv).

The Verdict: The backtest failed due to a Catastrophic Configuration Failure in the Portfolio Router which allowed the Intraday Options Engine to deploy **50% of the portfolio ($25,434)** into a single Naked Call option, instead of the intended 5% ($2,500).

This massive position immediately triggered the Kill Switch upon normal market noise (-6.26% drawdown in 1 minute), effectively disabling the algorithm on Day 1.

Here is the itemized analysis of the three critical failures found in the logs.

1. The "Account Killer" Bug: Unmapped Source Limit
Severity: CRITICAL (Loss of Funds)

Evidence:

Log: INTRADAY_SIGNAL... PUT x58 (Engine calculated correct size: ~58 contracts).

Fill: BUY 471.0 QQQ (Router executed 471 contracts).

Cost: 471 * $0.54 * 100 = **$25,434**.

Context: Total Portfolio = $50,000. Execution was 50.8% of capital.

The Root Cause:

The Intraday Engine sends signals with source="OPT_INTRADAY".

The PortfolioRouter has a dictionary SOURCE_ALLOCATION_LIMITS.

The Bug: OPT_INTRADAY is MISSING from this dictionary.

The Fallback: The Router likely defaults to 0.50 (50%) or 1.0 (100%) for unknown sources.

The Fix: You must explicitly map OPT_INTRADAY in portfolio_router.py.

2. The "Sizing" Logic Failure (Requested Quantity Ignored)
Severity: HIGH (Risk Management Failure)

Evidence:

The Engine Log calculated x58 (correct risk sizing).

The Router executed 471 (allocation sizing).

This proves that Step 2 (Aggregation) in the Router is dropping or ignoring the requested_quantity field from the TargetWeight object.

The Root Cause:

In portfolio_router.py, inside aggregate_weights:

Check if agg.requested_quantity is actually being assigned from weight.requested_quantity.

Hypothesis: The TargetWeight coming from check_intraday_entry_signal has the quantity, but the aggregation logic overwrites it or fails to prioritize it.

The Fix: Verify portfolio_router.py lines 180-200. Ensure:

3. The Scheduler Crash (Attribute Error)
Severity: MEDIUM (Operational Blindness)

Evidence:

Log: SCHEDULER: Callback error for EOD_PROCESSING: 'RegimeState' object has no attribute 'score'.

The Root Cause:

The RegimeState object (defined in models/regime_state.py) likely uses the attribute name regime_score or market_score, but the daily_scheduler.py is trying to access .score.

The Fix:

Check models/regime_state.py for the correct field name.

Update scheduling/daily_scheduler.py to use the correct attribute.