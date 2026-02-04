🧪 APVP V2.30: All-Weather Validation Protocol
Purpose: Detect logical incoherence in regime-aware scaling, ghost state clearing, and granular startup gates before running historical simulations.
Scope: StartupGate API • Regime-Aware Scaling • Ghost Spread Flushing • Dynamic Governor Recovery • Portfolio Router

Phase 0: Pre-Flight Sanity Gate (Hard Stop)
If any item fails → DO NOT RUN BACKTEST
	•	Branch Integrity: Confirm backtest branch is V2.30. Verify no uncommitted local "hotfixes."
	•	Persistence Check: Ensure state_manager.py logic for saving gate_status.json and GovernorTier is active.
	•	Static Config Check: Verify DRAWDOWN_GOVERNOR_RECOVERY_BASE = 0.08 and StartupGate Phase timings (5/5/5) are correct.
	•	Indicator Warmup: Ensure SetWarmUp is configured for 200 bars to prevent "Day 1" data gaps.

Phase 1: Architecture Coherency (The V2.30 Flow Audit)
Goal: Verify the "Regime decides WHAT, Gate decides HOW MUCH" separation.
Stage
Engine
Precheck Question
Status
1
Regime Engine
Does it output scores for Bear (<40), Neutral (40-60), and Bull (>60)?
[ ]
2
StartupGate API
Are allows_hedges(), allows_bearish_options(), and allows_longs() independent?
[ ]
3
Governor
Is current_scale applied as a global multiplier to all engine sizing?
[ ]
4
State Manager
Does OnOrderEvent and FridayFirewall both have clear_spread_state() hooks?
[ ]
5
Execution
Is Portfolio[].Invested the primary source of truth for state reconciliation?
[ ]
🔍 Critical Fail Conditions:
	•	StartupGate blocking allows_hedges() based on Regime Score (Hedges must be gated by time only).
	•	Options Engine using hardcoded dollar amounts instead of current_scale percentages.

Phase 2: Logic Consistency & Incoherency Detection
A. The "Ghost Spread" Eradication (P0-P1)
	•	Immediate Flush: Confirm OnOrderEvent clears spread_state when FillStatus == Filled.
	•	Rejection Flush: Confirm OnOrderEvent clears spread_state on Rejection or Cancel.
	•	Friday Plunger: Verify _reconcile_spread_state() runs at 15:45 ET and cross-references Portfolio.Invested.
	•	Governor Recovery: Verify the formula: threshold = 0.08 * current_scale.
B. Regime-Aware Startup Logic (V2.30 Granularity)
	•	Phase 1 (Observation - Days 1-5):
	◦	allows_hedges() == True | allows_longs() == False | allows_options() == False.
	•	Phase 2 (Reduced - Days 6-10):
	◦	Hedges enabled | Longs @ 10% cap | Options @ 10% cap.
	•	Phase 3 (Scaling - Days 11-15):
	◦	Hedges enabled | Longs @ 50% cap | Options @ 50% cap.
	•	Phase 4 (Armed - Day 16+):
	◦	All limits removed. Governor Tier 1 (100% scale) becomes active.
C. Bear Market Wiring Audit
	•	Hedge Trigger: Confirm Regime < 40 triggers PSQ/TMF regardless of StartupGate status.
	•	Option Pivot: Confirm Put Spreads are enabled when Regime < 45 (No "40-44" dead zone).
	•	Margin Circuit Breaker: Confirm MarginUsed > 60% triggers _liquidate_all_spread_aware().

Phase 3: Mandatory Assertions (Code-Level Scan)
	•	Assert: current_scale <= 1.0 (Governor never over-leverages).
	•	Assert: if(StartupGate.Phase == 1) then Position(TQQQ) == 0.
	•	Assert: if(RegimeScore < 40) then allows_directional_longs() == False.
	•	Assert: if(Portfolio.Invested == False) then spread_state == NULL.

Phase 4: Pre-Backtest Failure Flags
Severity
Indicator
Meaning
Action
🔴 CRITICAL
GHOST_STATE_AFTER_REJECTION
Logic blind to broker reality
Fix OnOrderEvent
🔴 CRITICAL
HEDGE_LOCKED_BY_STARTUP_GATE
System not All-Weather
Fix allows_hedges()
🔴 CRITICAL
RECOVERY_THRESHOLD_STATIC
Governor recovery impossible
Fix dynamic formula
🟡 WARN
METADATA_STALE_AFTER_KILL
Log pollution risk
Purge pending fields
🟡 WARN
FRIDAY_FLUSH_DURING_MARKET
Race condition risk
Confirm single-thread

Phase 5: Final Readiness Gate
You may proceed to backtesting only if:
	1	You can explain why a rejected order at 10:00 AM won't block a new trade at 1:00 PM.
	2	You can prove the bot will buy PSQ/TMF on Day 1 if the market is crashing.
	3	The Dynamic Recovery math (4% threshold at 50% scale) is unit-tested.

Required Deliverable
Before hitting "Run Backtest," you must produce the V2.30 Logic Ownership Map:
	•	Regime Engine decides the DIRECTION (What to buy).
	•	StartupGate decides the AVAILABILITY (Are we too new to trade?).
	•	Governor decides the QUANTITY (How much risk is safe?).
	•	State Manager decides the REALITY (Are we actually in a trade?).
