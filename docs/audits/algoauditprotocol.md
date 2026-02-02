🛡️ Algorithmic Audit Protocol (AAP) - V2.11
Focus: VASS Logic, Gamma Pin Protection, and Portfolio Margin Synchronization.

Phase 1: The "V2.11 Funnel Analysis"
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

Phase 2: Logic Integrity Checks (The V2.11 Audit)
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

Phase 3: Critical Failure Flags (V2.11 "Smoke Signals")
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

Phase 4: Performance Reality Check (Jan–Mar 2025)
Market Context: QQQ Jan–Mar 2025 (Volatile/Late Cycle).
	•	The "Wait" Success: Did the bot stay cash during the New Year/MLK settlement gaps?
	•	The "Spread" Success: Did Credit Spreads protect the downside better than the V2.1 single-leg versions?
	•	Win Rate Audit: * Trend Engine: No. of Trades [ ] | Win % [ ]
	◦	Options Sniper: No. of Trades [ ] | Win % [ ]

📥 Required Deliverable for Review:
	1	Completed AAP Checklist (as formatted above).
	2	The "Drop-off" Report:
	◦	Signals Generated: [Count]
	◦	Rejections (Floor < $0.35): [Count]
	◦	Rejections (Margin): [Count]
	◦	Actual Trades Filled: [Count]
	3	Engine Performance: Individual P&L contribution of the Trend Engine vs. the VASS Options Engine.
