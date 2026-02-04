🧪 Algorithmic Pre-Backtest Validation Protocol (APVP)  
Purpose: Detect logical incoherence, code defects, engine conflicts, parameter drift, and process violations before running historical simulations.
Scope:VASS Logic • Regime Detection • Gamma Pin Protection • Portfolio Router • Settlement & Margin Synchronization

Phase 0: Pre-Flight Sanity Gate (Hard Stop)
If any item fails → DO NOT RUN BACKTEST
Environment & Code Integrity
	•	Branch Integrity: Confirm backtest branch matches the intended version (V2.11).→ No local uncommitted changes.
	•	Single Source of Truth: Strategy parameters (credit floor, VIX thresholds, hard caps) exist in one config file only.
	•	Dead Code Scan: No unused engines, flags, or deprecated logic paths referenced.
	•	Determinism Check: No random seeds, time-dependent calls, or live-data hooks active.

Phase 1: Architecture Coherency Check (The V2.11 Flow Audit)
Goal: Ensure the multi-engine system is logically coherent before data touches it.
Engine Interaction Map
Stage
Engine
Precheck Question
Status
1
Regime Engine
Does it expose a single, normalized market_regime output?
[ ]
2
VASS Selector
Is strategy selection only driven by Regime output (no overrides)?
[ ]
3
Signal Engines
Are Sniper & Trend isolated until Portfolio_Router arbitration?
[ ]
4
Portfolio Router
Is capital reservation enforced before execution logic?
[ ]
5
Execution Engine
Does it consume validated orders only (no internal sizing)?
[ ]
🔍 Fail Conditions
	•	Multiple engines modifying capital directly
	•	Strategy selection bypassing Regime Engine
	•	Execution recalculating quantities independently

Phase 2: Logic Consistency & Incoherency Detection
“Does the logic still mean what you think it means?”
A. Regime → Strategy Coherence (VASS Discipline)
	•	VIX Threshold Alignment
	◦	VIX > 22 → Credit strategies enabled
	◦	VIX < 18 → Debit strategies enabled
	◦	No overlap or ambiguous zone
	•	Direction Filter Integrity
	◦	UVXY_PROXY rising → entries blocked
	◦	Confirm no code path ignores this flag
	•	Credit Floor Enforcement
	◦	$0.35 applied before contract selection
	◦	No rounding or post-selection rejection
❌ Red Flag: Strategy rejection logic living in multiple places.

B. Time, Expiry & Gamma Logic Integrity
	•	Expiry Awareness
	◦	Does the system explicitly know what day of week it is?
	•	Gamma Pin Distance Logic
	◦	0.5% proximity check uses underlying price, not option price
	•	Exit Priority Order
	◦	GAMMA_PIN_EXIT → higher priority than FRIDAY_FIREWALL
	•	Spread Construction Logic
	◦	Long/Short leg signs enforced at creation (not execution)
❌ Red Flag: Fixes applied “downstream” instead of at order creation.

C. Capital, Margin & Settlement Synchronization
	•	Hard Cap Enforcement
	◦	$5,000 cap applied before quantity calculation
	•	Engine Budget Isolation
	◦	Trend Engine max = 70% capital
	◦	Options Engine guaranteed ≥ 30%
	•	Settlement Gate Awareness
	◦	Monday/Tuesday logic blocks trading until 10:30 AM
	•	No Retroactive Capital Claims
	◦	Engines cannot reclaim “unused” margin later in the day
❌ Red Flag: Capital shared implicitly instead of routed explicitly.

Phase 3: Code-Level Conflict & Bug Scan (Static Reasoning)
Catch bugs that backtests will not reveal.
Mandatory Assertions (Must Exist in Code)
	•	Assert: abs(long_qty) == abs(short_qty)
	•	Assert: order_cost <= HARD_CAP
	•	Assert: margin_reserved + order_cost <= available_cash
	•	Assert: strategy_selected ∈ VASS_MATRIX
Known Failure Patterns to Scan For
Pattern
Why It’s Dangerous
Silent try/except
Masks execution bugs
Duplicate config values
Parameter drift
Engine-specific sizing
Capital desync
Post-execution fixes
Hides root cause

Phase 4: Pre-Backtest Failure Flags (“Pre-Smoke Signals”)
Severity
Indicator
Meaning
Action
🔴 CRITICAL
STRATEGY_SELECTED_BUT_NO_PATH
Logic incoherent
Fix selection logic
🔴 CRITICAL
CAPITAL_MUTATION_MULTI_ENGINE
Race condition
Refactor router
🔴 CRITICAL
LEG_SIGN_ASSUMED
Spread not enforced
Block execution
🟡 WARN
PARAM_DEFINED_TWICE
Drift risk
Consolidate config
🟡 WARN
TIME_CHECK_IMPLICIT
Date logic fragile
Make explicit
🟢 INFO
ASSERTIONS_PRESENT
Defensive coding
Proceed

Phase 5: Backtest Readiness Gate (Final Go / No-Go)
You may proceed to backtesting only if:
	•	All CRITICAL flags resolved
	•	No unresolved logic ambiguities
	•	Capital, regime, and execution ownership are unambiguous
	•	You can explain why the system should not trade on a random Tuesday

📦 Required Pre-Backtest Deliverable
Before running any historical simulation:
	1	Completed APVP Checklist
	2	Logic Ownership Map
	◦	Which engine decides what
	◦	Which engine decides how much
	3	Invariant List
	◦	Rules that must never be violated (even if P&L improves)
