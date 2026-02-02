🛡️ Algorithmic Audit Protocol (AAP) - 



Phase 1: The "Three-Way Match" (Funnel Analysis)
Correlate Logs vs. Orders vs. Trades to find "Silent Failures".
Funnel Stage	Data Source	Metric to Check	Status (Pass/Fail)
1. Signal Generation	logs.txt	Count of ENTRY_SIGNAL lines.	[ ]
2. Router Processing	logs.txt	Count of ROUTER: SUBMITTING lines.	[ ]
3. Liquidity Event	orders.csv	Count of SHV (or Cash Asset) SELL orders.	[ ]
4. Execution	trades.csv	Count of actual FILLED entry trades.	[ ]
** Diagnosis:**
* If (1) > (2): Router Blockage. (Check: Margin logic, Cash guards).
* If (2) > (4): Broker Rejection. (Check: Limit prices, Market hours, Bad symbols).
* If (2) exists but (3) is zero: Cash Trap. (Router failed to free up capital).

Phase 2: Logic Integrity Checks
Verify the algorithm did what it said it would do.
A. Trend Engine (Structure & Discipline)
* Entry Logic: Pick 3 random Trend trades from trades.csv.
    * [ ] Did the Log confirm Price > SMA and ADX > Threshold at that timestamp?
* Exit Logic: Pick 3 random Trend exits.
    * [ ] Does the Log Reason=... match the active config.py strategy (e.g., Chandelier vs. SMA)?
    * [ ] Premature Ejaculation Check: Did it exit before the condition was met? (Sign of bad data or rogue logic).
B. Options Engine (Selection & Execution)
* Contract Selection: Search Logs for Selected legs.
    * [ ] Are Deltas within the allowed range (e.g., 0.50 - 0.85)?
    * [ ] Is DTE (Days to Expiration) correct?
* Throttle Check:
    * [ ] Calculate time difference between the first 5 "Scanning" logs.
    * [ ] Is it respecting the SCAN_INTERVAL (e.g., 15 mins)? Or spamming every minute?
C. Risk Engine (The Safety Net)
* Hard Stop Validation:
    * [ ] Identify the largest Loss in trades.csv.
    * [ ] Did it exceed the MAX_LOSS_PCT defined in config?
    * [ ] If YES -> CRITICAL FAIL.
* Kill Switch:
    * [ ] Search Logs for KILL_SWITCH.
    * [ ] If triggered, did it result in Liquidating all positions in orders.csv immediately?

Phase 3: Critical Failure Flags (The "Grep" List)
Search the logs for these specific "Smoke Signals" of broken code.
Severity	Search Keyword	Meaning	Action if Found
🔴 CRITICAL	INSUFFICIENT_MARGIN	Broker blocked trade due to funds.	FAIL: Fix Router/Sizing.
🔴 CRITICAL	ZeroDivisionError	Math crash (usually ATR/ADX calculation).	FAIL: Fix Indicators.
🟡 WARN	Order rejected	Broker rejected specific parameters.	Investigate: Price/Qty issue.
🟡 WARN	No data for	Missing market data for symbol.	Check: Subscription/Ticker.
🟢 INFO	SHV_AUTO_LIQUIDATE	System is freeing up cash.	PASS: (Required for buys).
Phase 4: Performance Reality Check
Does the P&L make sense given the market context?
* Market Context: What did SPY/QQQ do during this period? (e.g., "Choppy", "Bull Run", "Crash").
* Bot Behavior:
    * In Chop: Did Trend Engine stay quiet (or get whipsawed)?
    * In Crash: Did Risk Engine save the account?
    * In Bull: Did Options Engine capture upside?
* Sanity Check: If SPY is +5% and Bot is -10%, correlate with Phase 2 (Logic Integrity).

ACTION :

1. Open and review the logs thoroughly at /docs/audits/logs/stage2/
2. Perform the "Three-Way Match":
    * Find a specific ENTRY_SIGNAL in the log (Timestamp X).
    * Find the corresponding SUBMITTING order in the log.
    * Find the SHV SELL (if needed) and OPTION BUY in orders.csv.
    * Find the FILL in trades.csv.
3. Report the Drop-off Rate:
    * Signals Generated: [Count]
    * Orders Submitted: [Count]
    * Trades Filled: [Count]
    * Note: If Signals > Orders, explain WHY (e.g., "Filtered by Regime" is OK; "Insufficient Margin" is NOT).
4. Scan for Errors: Grep for Error, Exception, Insufficient, Rejected.
Deliverable: The completed AAP Checklist above.