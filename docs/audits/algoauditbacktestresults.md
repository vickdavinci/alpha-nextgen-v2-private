Standard Algorithmic Audit Protocol (AAP)
Target Dataset: Last Backtest
Objective: Forensic analysis of Execution Hygiene, Logic Latency, and Risk Management efficiency.
Goal: Identify specific "Profit Leaks" and "Logic Lags" in the current build.
Phase 1: Execution Hygiene (The "Plumbing" Check)
Verify that the Order Management System (OMS) is executing cleanly without data errors or timing mismatches.
Metric
Data Source
Audit Question
Developer Finding
Atomic Synchronization
orders.csv
Timestamp Match: Do the Long and Short legs of every multi-leg spread have the exact same Time stamp (tolerance < 1 sec)?
[ ] Pass / Fail
Ghost Fills
orders.csv
Data Integrity: Are there any fills with Price: 0 or Status: Filled but Value: 0?
[Count]
Asset Validation
trades.csv
Ticker Check: Are there any "Unknown" symbols or unintended asset classes (e.g., unintended leveraged ETFs)?
[ ] Clean / Anomalies
Slippage Audit
orders.csv
Execution Cost: Compare Limit Order Price vs. Actual Fill Price. Did we suffer > 2% slippage on entry?
[ ] % Avg Slippage

Phase 2: Regime & Logic Latency (The "Reaction" Analysis)
Analyze how fast the bot adapts to changes in Market Direction (Trend Inversions).
A. The "Falling Knife" Test (Bull-to-Bear Transitions)
	•	Identify the steepest market drop in this backtest period.
	•	Investigation:
	◦	Did the bot attempt to buy "Long Calls" while the price was below the 50-SMA?
	◦	How many days did it take for the Regime Engine to switch from "Bull" to "Bear/Cash"?
	◦	Optimization: Was the exit too slow?
B. The "Missed Rally" Test (Bear-to-Bull Transitions)
	•	Identify the sharpest recovery rally in this backtest period.
	•	Investigation:
	◦	Did the bot re-enter the market within the first 5% of the move?
	◦	Did it use the correct instrument (Calls vs. Spreads) for the recovery?

Phase 3: Risk Management Stress Test
Analyze the "Tail Risk" events to ensure capital preservation rules are working.
A. The "Hall of Shame" (Biggest Losers)
Identify the Top 3 Absolute Worst Trades (Largest $ Loss) in this run.
	•	Trade ID: [ ] | Symbol: [ ] | Loss %: [ ]
	•	Root Cause Analysis (Must Answer):
	◦	Did the Portfolio Stop Loss trigger late?
	◦	Did the Option Premium decay > 50%?
	◦	Hypothesis: If we had a "Hard Option Stop" at -30%, would this loss have been prevented?
B. Position Sizing Safety
	•	Check: Did any single trade entry allocate > 15% of the Total Equity?
	•	Metric: Max Position Size observed.
	•	Optimization: Does the sizing logic need a hard cap (e.g., 0.10)?

Phase 4: Profit Attribution (The "Winner" Anatomy)
Analyze the source of Alpha to understand what to scale.
A. The "Hall of Fame" (Biggest Winners)
Identify the Top 3 Best Trades (Largest $ Profit) in this run.
	•	Trade ID: [ ] | Symbol: [ ] | Profit %: [ ]
	•	Profit Driver Analysis:
	◦	Directional (Delta): Did we win because the market moved in our favor?
	◦	Time Decay (Theta): Did we win because the short leg expired worthless?
	◦	Scalability: Is this strategy scalable, or was it a lucky fill?

Phase 5: Required Optimizations (The Action Plan)
Based on the findings above, propose specific code patches:
	1	Risk Patch: define the logic required to fix the "Hall of Shame" losses (e.g., Stop Loss tightness).
	2	Filter Patch: Define any Rules of Engagement needed to prevent "Falling Knife" entries (e.g., SMA Filters).
	3	Execution Patch: Define fixes for any slippage or atomic sync issues found in Phase 1.
Deliverable:
Completed Audit Report filling in the brackets above for the provided dataset.
Phase 6: The "Funnel Analysis"
Correlate the new multi-engine flow to identify where "The Sniper" or "The Trend" is failing.
Funnel Stage
Data Source
V2.11 Logic Check
Status (Pass/Fail)
1. Market Regime
logs.txt
Is Regime_Engine detecting the correct VIX Level (CBOE)?
[ ]
2. VASS Selection
logs.txt
Did it pick the correct Strategy Matrix (Credit vs. Debit)?
[ ]
3. Sniper Signal
logs.txt
Count of VASS_SIGNAL_GENERATED lines.
[ ]
4. Margin Filter
logs.txt
Count of ROUTER: MARGIN_RESERVED ($5000) lines.
[ ]
5. Execution
trades.csv
Count of actual ComboMarketOrders FILLED.
[ ]
V2.11 Specific Diagnosis:
	•	If Signals > Margin Reserved: Margin Collision. (Trend Engine "hogged" the cash; Check Portfolio_Router).
	•	If VASS_REJECTION exists: Strategy too tight. (Check: Was $0.35 credit floor hit? Was IV too low for spreads?)
	•	If Post-Holiday Tuesday (Jan 21): Check if SETTLEMENT_GATE: ACTIVE held the bot until 10:30 AM.

Phase 7: Logic Integrity Checks (The Audit)
A. The VASS Strategy Matrix (Selection Discipline)
	•	[ ] Volatility Level Check: Compare logs.txt with CBOE VIX. If VIX > 22, were trades Credit Spreads? If VIX < 18, were they Debit Spreads?
	•	[ ] Volatility Direction Check: Compare UVXY_PROXY logs. Did the bot avoid entries when UVXY was spiking (VIX Direction = RISING)?
	•	[ ] The $0.35 Floor: Pick 3 rejections. Does the log confirm the credit offered was < $0.35?
B. Gamma Pin & Expiry Protection
	•	[ ] Proximity Check: Pick a trade that closed on a Friday. Was the QQQ price within 0.5% of the short strike?
	•	[ ] Early Exit Logic: Did the log trigger GAMMA_PIN_EXIT or wait for the FRIDAY_FIREWALL (15:45)?
	•	[ ] Leg Sign Check: Verify trades.csv for a spread. Does it show 1 Buy and 1 Sell? (Ensures short_ratio = -long_ratio fix is working).
C. Capital & Settlement Security
	•	[ ] Monday/Tuesday Gate: On Jan 21, 2025, did the bot log WAITING_FOR_SETTLEMENT?
	•	[ ] Position Sizing: Did the Options trade exceed the $5,000 Hard Cap? (Check Quantity * Price).
	•	[ ] Trend vs. Options: When the Trend Engine fired at 15:45, did it respect the 30% cash reserve for the Options Engine?
Phase 8: Critical Failure Flags ( "Smoke Signals")
Severity
Search Keyword
V2.11 Meaning
Action if Found
🔴 CRITICAL
VASS_REJECTION_GHOST
Strategy exists but no contracts found.
FAIL: Check Option Universe/DTE.
🔴 CRITICAL
MARGIN_ERROR_TREND
Trend engine ignored the $5k reserve.
FAIL: Fix Portfolio_Router.
🔴 CRITICAL
SIGN_MISMATCH
Spread has two Buys or two Sells.
FAIL: Fix execution_engine.py.
🟡 WARN
SLIPPAGE_EXCEEDED
Trade cost > 2% buffer.
Investigate: Liquidity issue.
🟢 INFO
GAMMA_PIN_EXIT
Safety exit triggered near strike.
PASS: Protection worked.
🟢 INFO
SETTLEMENT_GATE_OPEN
Tuesday 10:30 AM passed; trading resumed.
PASS: Settlement aware.


