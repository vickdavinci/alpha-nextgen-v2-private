# Options Engine Master Audit (Single Source of Truth)

**Purpose:** Central, continuously updated bug registry and audit history for the Options Engine (Micro + VASS).  
**Rule:** All fixes and validations must be recorded here before/after backtests.

---

## Bug Registry (Master)

| ID | Category | Bug | Evidence | Status | Notes |
|:--:|:--------:|-----|----------|:------:|------|
| T-01 | Technical | Intraday positions escaping EOD force‑close | Q1 2022: DTE≤2 held >1 day | ✅ Validated | V6.13: No DTE≤2 overnight escapes in 112 days |
| T-02 | Technical | Scheduler failure / EOD routines | PRE_MARKET_SETUP callback error | ✅ Validated | V6.13: 0 scheduler errors in 112 days |
| T-03 | Technical | Short‑leg close logging sparse | Short leg closes << spreads removed | ✅ Validated | V6.13: 32 POSITION_REMOVED match leg closures |
| T-04 | Technical | Zero‑price inject (sizing bug) | `V2.19_INJECT price=0` | 🟡 Applied (Pending Validation) | Verify after V2.19 inject fallback |
| T-05 | Technical | Signal → Execution instrumentation | Approved signals >> results | ✅ Validated | V6.13_a: approval/selection/result funnel measurable (396→140 trades) |
| T-06 | Technical | Assignment risk exits | `ASSIGNMENT_RISK_EXIT` in 2022 | 🟡 Monitor | Confirm if behavior desired |
| T-07 | Technical | VASS credit option‑type mismatch | Credit spreads never firing | 🟡 Applied (Pending Validation) | Strategy‑aware option_right fix |
| T-08 | Technical | VASS_ENTRY logging missing | VASS_ENTRY=0 while SPREAD: ENTRY_SIGNAL fires | ✅ Validated | V6.13_a: `VASS_ENTRY=40` with matching spread entries |
| T-09 | Technical | Pre‑entry margin buffer for spreads | 2015 margin call after full‑size entry | 🟡 Applied (Pending Validation) | SPREAD_MARGIN_SAFETY_FACTOR + margin checks |
| T-10 | Technical | Overnight gap protection (swing) | 2015 overnight liquidation event | ✅ Validated | V6.13: 121 EOD protection events logged |
| T-11 | Technical | VIX spike auto‑exit (spreads) | 2015: VIX 12→28+ held | 🟡 Applied (Pending Validation) | Exit on VIX level/5D spike |
| T-12 | Technical | Regime deterioration exit (spreads) | 2015: regime fell from 75→cautious, held | 🟡 Applied (Pending Validation) | Exit on regime drop vs entry |
| T-13 | Technical | Invalid Stop Orders (Price=0) | V6.13: 10 StopMarket orders with Price=0, Status=Invalid | 🟡 Applied (Pending Validation) | V6.14: OCO Manager validates stop_price > 0 before creation |
| T-14 | Technical | Expiration Hammer Too Late | V6.13: 14 options sold @ $0.01 on expiry | 🟡 Applied (Pending Validation) | V6.14: Moved from 14:00 to 12:00 (noon) |
| T-15 | Technical | Assignment Safety Net MOO Canceled | V6.13: Dec 27 MOO canceled, -$37K loss | 🟡 Applied (Pending Validation) | V6.14: Immediate market fallback for critical cancelled MOO |
| T-16 | Technical | High Order Failure Rate | V6.13: 27.6% orders failed (Invalid+Canceled) | 🟡 Applied (Pending Validation) | V6.14: Pre-submit validation (symbol, price, expiry) |
| T-17 | Technical | Filter parsing failure (DTE=287) | V6.13 2015: 6 approved signals with nonsense filter counts | 🟡 Applied (Pending Validation) | V6.14: Enhanced filter funnel diagnostics with blocker ID |
| T-18 | Technical | Router margin width invalid regression | V6.13_a: `ROUTER_MARGIN_WIDTH_INVALID` appears during spread submits | 🟡 Applied (Pending Validation) | Exit metadata now includes spread width/type/credit to prevent close-path blocks |
| T-19 | Technical | Assignment containment leak | V6.13_a: 2022-03-29 assignment path still produced QQQ stock liquidation | 🟡 Applied (Pending Validation) | `_reconcile_positions()` now actively clears zombie state, closes orphan option holdings, and liquidates stale assignment QQQ equity |
| T-20 | Technical | Spread candidate pre-filter drops zero-bid long legs | `main.py:3872` rejects when `bid<=0 or ask<=0` before leg-role assignment | 🟡 Applied (Pending Validation) | Prefilter now rejects only invalid ask; zero-bid candidates are retained with safe mid-price fallback |
| T-21 | Technical | Credit selector missing liquidity quality filters | `options_engine.py:2974+` credit path lacks explicit OI/spread quality gates | 🟡 Applied (Pending Validation) | Added OI/spread quality filters and diagnostics parity for credit short/long leg selection |
| T-22 | Technical | Credit cooldown not direction-scoped | `options_engine.py:3015` uses shared `"CREDIT"` cooldown bucket | 🟡 Applied (Pending Validation) | Cooldown key is now strategy-scoped (`BULL_PUT_CREDIT` / `BEAR_CALL_CREDIT`) with legacy key cleanup |
| T-23 | Technical | Risk-reduction exits blocked by strict combo margin gate | Close combo path still enforces margin estimator before submit | 🟡 Applied (Pending Validation) | Router now bypasses margin-estimator block for `spread_close_short` exits |
| T-24 | Technical | Spread closing lock can remain stuck after failed close submit | `spread.is_closing=True` set before router close failures | 🟡 Applied (Pending Validation) | Router now resets lock on close-order failures |
| T-25 | Technical | VIX spike/regime deterioration exits preempted by assignment path | `ASSIGNMENT_RISK_EXIT` fires first; no `SPREAD: EXIT_SIGNAL` observed | 🟡 Applied (Pending Validation) | Added assignment grace window before non-mandatory ITM/margin exits |
| T-26 | Technical | Micro can starve VASS via shared daily options counter | Shared `MAX_OPTIONS_TRADES_PER_DAY` exhausted by intraday flow | 🟡 Applied (Pending Validation) | Added reserved swing slots guard for intraday limits |
| T-27 | Technical | OCO sibling orders still trigger margin rejects at force-close | Stage6.14: 6 dates x 4 logs (`Order Error` + `INVALID`) at 15:30 for stale OCO ids | 🟡 Applied (Pending Validation) | Force-close fallback/static timing now driven by `INTRADAY_FORCE_EXIT_TIME=15:25` to reduce race window |
| T-28 | Technical | Intraday approved->execution drop remains severe | Stage6.14: `INTRADAY_SIGNAL_APPROVED=385`, `INTRADAY_SIGNAL=104`, `DROPPED=281` | 🟡 Applied (Pending Validation) | Added canonical `DROP_*` reason codes + one-shot retry for temporary drop causes |
| T-29 | Technical | VASS spreads are instantly closed by assignment guard | Stage6.14: `SPREAD: POSITION_REGISTERED=23`, `ASSIGNMENT_RISK_EXIT=23`, `POSITION_REMOVED=23` (<=1 min) | 🟡 Applied (Pending Validation) | Assignment grace (`SPREAD_ASSIGNMENT_GRACE_MINUTES`) added before non-mandatory assignment exits |
| T-30 | Technical | NEUTRAL conviction veto can overtrade noisy flips | Panic rebound windows showed repeated NEUTRAL veto direction swings | 🟡 Applied (Pending Validation) | NEUTRAL veto now requires MICRO tradeable regime + direction alignment with MICRO recommendation |
| O-01 | Optimization | Low win rate / negative P&L | 2022Q1: -21,641 | 🔴 Open | Strategy tuning |
| O-02 | Optimization | High Dir=NONE | 2022Q1: 59% | 🔴 Open | Micro gating still strict |
| O-03 | Optimization | Micro gating too restrictive | CAUTIOUS/NORMAL/WORSENING blocks | 🔴 Open | Expand tradeable regimes or thresholds |
| O-04 | Optimization | VASS rejection storm | 2022Q1: 534 rejections | 🟡 Applied (Pending Validation) | DTE fallback + credit fix |
| O-05 | Optimization | CALL/PUT profitability imbalance | 2022Q1: PUTs worse | 🔴 Open | Strategy calibration |
| O-06 | Optimization | Multi-day hold efficiency | V6.13_a: 51/140 trades held >1 day | 🟡 Monitor | Most are swing by design; optimize carry/expiry behavior |
| O-07 | Optimization | PUT strategy underperforms in crash | 2015/2022 PUT P&L negative | 🔴 Open | Re‑evaluate PUT sizing/strategy |
| O-08 | Optimization | VASS as intraday governor | VASS inactive → micro dominates | 🔴 Open | Consider gating micro by VASS |
| O-09 | Optimization | Spread exit reason logging | Limited PROFIT/STOP logs vs exits | 🟡 Monitor | Verify exit reasons cover all paths |
| O-10 | Optimization | Monthly P&L tracking | Aug 2015 drawdown visibility missing | ✅ Implemented | V6.12: MonthlyPnLTracker class + main.py integration |
| O-11 | Optimization | Position concentration limit | Multiple spreads same expiry | 🔴 Open | Add per‑expiry cap |
| O-12 | Optimization | CALL loss concentration remains dominant | Stage6.14: CALL P&L -20,695 vs PUT P&L +351 | 🟡 Applied (Pending Validation) | Added stress CALL block (`INTRADAY_CALL_BLOCK_VIX_MIN`, `INTRADAY_CALL_BLOCK_REGIME_MAX`) |

---

## V6.13 Dec 2021 - Mar 2022 (Technical Fix Validation)

**Run:** `V6_13_Dec2021_Mar2022`
**Files reviewed:**
`docs/audits/logs/stage6.13/V6_13_Dec2021_Mar2022_logs.txt`
`docs/audits/logs/stage6.13/V6_13_Dec2021_Mar2022_orders.csv`
`docs/audits/logs/stage6.13/V6_13_Dec2021_Mar2022_trades.csv`

**Date:** 2026-02-09

### Headline Metrics

- Trades: **185**
- Win%: **25.4%** (47W / 138L)
- Net P&L: **-$28,482**
- Calls: **124 trades**, **P&L +$20,680** (21.8% win)
- Puts: **60 trades**, **P&L -$9,131** (33.3% win)
- Max Single Trade Loss: **-$42,180** (assignment event)

### Fix Verification Results

| Fix | Status | Evidence |
|-----|--------|----------|
| T-01 (DTE escape) | ✅ VALIDATED | No DTE≤2 overnight holds |
| T-02 (Scheduler) | ✅ VALIDATED | 0 PRE_MARKET_SETUP errors in 112 days |
| T-03 (Short-leg logging) | ✅ VALIDATED | 32 POSITION_REMOVED match closures |
| T-05 (Signal gap) | ✅ VALIDATED | 612:99 signal ratio monitored |
| T-10 (Overnight protection) | ✅ VALIDATED | 121 EOD protection events |

### NEW Critical Bugs Found

| ID | Bug | Impact |
|----|-----|--------|
| T-13 | Invalid Stop Orders (Price=0) | 10 instances, risk mgmt disabled |
| T-14 | Expiration Hammer Late | 14 @ $0.01 sales on expiry |
| T-15 | Assignment MOO Canceled | Single -$37K event |
| T-16 | Order Failure Rate | 27.6% (Invalid+Canceled) |

### Signal Metrics

- `INTRADAY_SIGNAL`: **612**
- `INTRADAY_RESULT`: **99** (16% conversion)
- `Dir=NONE`: **1,464 / 2,420** (60.5%)
- `VASS_REJECTION`: **799** (100% DTE/delta/credit)

### By Mode

| Mode | Orders | Notes |
|------|--------|-------|
| VASS (Swing) | 66 | Bull call debit spreads |
| MICRO (Intraday) | 129+ | 1-5 DTE momentum |
| Assignment/Salvage | 38 | Emergency closes |

---

## V6.13_a Dec 2021 - Mar 2022 (Latest Technical + Optimization Validation)

**Run:** `V6_13_a_Dec2021_Mar2022`  
**Files reviewed:**  
`docs/audits/logs/stage6.13/V6_13_a_Dec2021_Mar2022_logs (1).txt`  
`docs/audits/logs/stage6.13/V6_13_a_Dec2021_Mar2022_orders (1).csv`  
`docs/audits/logs/stage6.13/V6_13_a_Dec2021_Mar2022_trades (1).csv`

**Date:** 2026-02-10

### Headline Metrics

- Trades: **140**
- Win%: **43.57%** (61W / 79L)
- Net P&L: **-$48,371**
- Orders: **425 total** / **284 filled** / **48 invalid** / **93 canceled**
- Multi-day holds: **51 / 140** (36.4%)

### Technical Validation Summary

| Area | Status | Evidence |
|------|--------|----------|
| Direction mismatch | ✅ Validated | 396 approved-vs-selected comparisons, **0 mismatches** |
| OCO wiring | ✅ Validated | `OCO_CREATED=89`, `OCO_ACTIVATED=89`, `OCO_STOP=40`, `OCO_PROFIT=23` |
| Scheduler failures | ✅ Validated | No callback crash signatures in this run |
| Intraday overnight escape (DTE<=2) | ✅ Validated | No true intraday DTE<=2 carry escapes found |
| Margin reliability | 🔴 Open | `Insufficient buying power` errors = 88, `MARGIN_CB_SKIP` = 46 |
| Margin width regression | 🔴 Open | `ROUTER_MARGIN_WIDTH_INVALID` = 23 |
| Assignment containment | 🔴 Open | 2022-03-29 assignment flow still forced QQQ liquidation |

### Optimization Findings

| Area | Evidence | Status |
|------|----------|--------|
| Micro gating still strict | `INTRADAY blocked NO_TRADE=462` | 🔴 Open |
| NO_TRADE dominant reasons | `CONFIRMATION_FAIL=155`, `QQQ_FLAT=117`, `REGIME_NOT_TRADEABLE=100`, `VIX_STABLE_LOW_CONVICTION=41` | 🔴 Open |
| VASS construction friction | `VASS_REJECTION=575`, split: `DEBIT_ENTRY_VALIDATION_FAILED=292`, `CREDIT_ENTRY_VALIDATION_FAILED=283` | 🟡 Monitor |
| Direction profitability imbalance | CALL trades 85 with P&L **-22,926**, PUT trades 54 with P&L **-1,993** | 🔴 Open |

### Conclusion (V6.13_a)

Core technical plumbing has improved (direction consistency, OCO activation, scheduler stability), but two critical paths remain unresolved: margin handling regression (`ROUTER_MARGIN_WIDTH_INVALID`) and assignment containment leak. Optimization bottlenecks are now mostly concentrated in Micro NO_TRADE gating and VASS contract validation strictness.

---

## V6.13 Jul-Sep 2015 (Pre-Fix Baseline Validation)

**Run:** `V6_13_Jul_Sep_2015`
**Files reviewed:**
`docs/audits/logs/stage6.13/V6_13_Jul_Sep_2015_logs.txt`
`docs/audits/logs/stage6.13/V6_13_Jul_Sep_2015_orders.csv`
`docs/audits/logs/stage6.13/V6_13_Jul_Sep_2015_trades.csv`

**Date:** 2026-02-09

### Headline Metrics

- Trades: **58**
- Win%: **50.0%** (29W / 29L)
- Net P&L: **-$16,179**
- Calls: **-$8,450** (long call decay)
- Puts: **-$7,729** (protective puts expired worthless)
- Spreads: **+$200** (only positive)
- Order Fill Rate: **84.8%** (128/151)

### Aug 18-20 Anomaly Investigation

**Aug 18: T-02 Scheduler Crash (ROOT CAUSE)**
```
2015-08-18 09:25:00 SCHEDULER: Callback error for PRE_MARKET_SETUP:
  'PortfolioRouter' object has no attribute 'add_weight'
```
- Pre-market setup completely failed, corrupting Aug 19-20 state

**Aug 19: T-01 DTE=1 Overnight Escape**
```
2015-08-19 11:30:00 INTRADAY: Selected PUT | Strike=109.0 | Delta=0.34 | DTE=1
2015-08-19 15:30:00 INTRADAY_FORCE_EXIT: SKIP | already closed
```
- Entered DTE=1 option (high assignment risk)
- Force-close logic triggered but position was already gone

**Aug 20: T-17 Filter Failure Storm (6 Blocked Signals)**
```
2015-08-20 12:30:00 INTRADAY_SIGNAL_APPROVED: CONVICTION: UVXY +4% > +2%
2015-08-20 12:30:00 INTRADAY_FILTER_FAIL: PUT | Total=574 | Dir=287 DTE=287
  Greeks=0 Delta=0 OI=0 Prices=0 Spread=0
```
- Pattern repeated at: 12:30, 12:45, 13:30, 13:45, 14:10, 14:25 (6 times)
- Filter showed `DTE=287` which is nonsensical
- Half the option chain (287/574) couldn't be parsed

### Fix Validation (Comparing 2015 vs 2022)

| Fix | 2015 Status | 2022 Status | Conclusion |
|-----|-------------|-------------|------------|
| T-01 (DTE escape) | PRESENT (18 instances) | ABSENT (0 instances) | **FIXED** |
| T-02 (Scheduler) | PRESENT (1 crash) | ABSENT (0 crashes) | **FIXED** |
| T-10 (Overnight protection) | WORKING | WORKING | Validated |
| T-13 (Invalid Stops) | NOT FOUND | PRESENT | Not root cause |
| T-15 (Assignment detection) | WORKING | WORKING | Validated |

### Signal Metrics

- `MICRO_UPDATE`: **1,840**
- `Dir=NONE`: **1,367** (74.2%)
- `VASS_ENTRY`: **26**
- `VASS_REJECTION`: **843** (97% rejection rate)
- `INTRADAY_SIGNAL_APPROVED`: **244**

### Aug 24-26 Crisis Response (VIX Spike)

**What Worked:**
```
2015-08-24 06:30:00 REGIME V5.3: SPIKE CAP ACTIVATED - VIX=28.0 5d change=46.4%
2015-08-25 10:00:00 VIX_SPIKE: 24.9 -> 43.7 (via UVXY)
2015-08-25 10:00:00 MICRO_UPDATE: Regime=CRASH | Dir=PUT
```
- Spike cap activated at VIX 28.0 (+46.4% 5d)
- CRASH mode correctly detected
- Protective puts triggered 3 times
- Overnight gap protection closed spread at 15:45

**What Failed:**
- Protective puts bought at panic highs expired worthless (-$4,749)
- Long calls from Aug 13 decayed to $0.01 (-$7,060)

### NEW Bug Discovered

| ID | Bug | Evidence | Severity |
|----|-----|----------|----------|
| T-17 | Filter parsing failure | Aug 20: DTE=287 nonsense, 6 signals blocked | HIGH |

### Comparison with 2022 Analysis

| Metric | 2015 (Jul-Sep) | 2022 (Dec-Mar) | Delta |
|--------|----------------|----------------|-------|
| Trades | 58 | 185 | -127 |
| Win Rate | 50.0% | 25.4% | +24.6% |
| Net P&L | -$16,179 | -$28,482 | +$12,303 |
| Order Fill Rate | 84.8% | 72.4% | +12.4% |
| Dir=NONE | 74.2% | 60.5% | -13.7% |
| VASS Rejection | 97.0% | 99.0% | +2.0% |
| T-02 Scheduler Errors | 1 | 0 | Fixed |
| T-01 DTE Escapes | 18 | 0 | Fixed |

### Conclusion

The 2015 backtest validates that T-01 and T-02 bugs existed historically and are now fixed in V6.13. New bug T-17 (filter parsing failure) discovered and added to registry.

---

## V6.11 2022 Full Year (Baseline)

**Run:** `V6_11_2022FullYear`  
**Files reviewed:**  
`docs/audits/logs/stage6.5/V6_11_2022FullYear_logs.txt`  
`docs/audits/logs/stage6.5/V6_11_2022FullYear_orders.csv`  
`docs/audits/logs/stage6.5/V6_11_2022FullYear_trades.csv`

**Date:** 2026-02-09

---

## V6.12 Q1 Validation (Jan–Mar 2022) — Post-Fix Snapshot

**Run:** `V6_12_2022Q1_Validation`  
**Files reviewed:**  
`docs/audits/logs/stage6.5/V6_12_2022Q1_Validation_logs.txt`  
`docs/audits/logs/stage6.5/V6_12_2022Q1_Validation_orders.csv`  
`docs/audits/logs/stage6.5/V6_12_2022Q1_Validation_trades.csv`

**Headline Metrics**
- Trades: **115**
- Win%: **27.8%** (32W / 83L)
- Net P&L: **-21,641**
- Calls: **65 trades**, **P&L -6,958** (16W / 49L)
- Puts: **50 trades**, **P&L -14,683** (16W / 34L)

**Signal → Execution**
- `INTRADAY_SIGNAL`: **296**
- `INTRADAY_SIGNAL_APPROVED`: **208**
- `INTRADAY_RESULT`: **75** (≈ **25%** conversion)

**VASS Activity**
- `VASS_ENTRY`: **0**
- `VASS_REJECTION`: **534**
- `SWING: Spread construction failed`: **534**
- `FailStats` present on **178** rejection logs

**Notable Observations**
- **VASS still not executing** (0 entries).
- **Assignment risk exits remain**: `ASSIGNMENT_RISK_EXIT` **13**.
- **Short-leg close logging** appears but still sparse: `SPREAD: Short leg closed` **2** vs `SPREAD: POSITION_REMOVED` **14**.
- **Dir=NONE still high**: `Dir=NONE` **1,067 / 1,800** (~59.3%).
- No `MICRO_NO_TRADE` logs in this run (fix added after these logs).

---

## V6.13 Dec 2021 - Feb 2022 (Stage6.14 Validation)

**Run:** `V6_13_Dec2021_Feb2022`  
**Files reviewed:**  
`docs/audits/logs/stage6.14/V6_13_Dec2021_Feb2022_logs.txt`  
`docs/audits/logs/stage6.14/V6_13_Dec2021_Feb2022_orders.csv`  
`docs/audits/logs/stage6.14/V6_13_Dec2021_Feb2022_trades.csv`

**Date:** 2026-02-10

### Headline Metrics

- Trades: **169**
- Win%: **27.22%**
- Gross P&L: **-$20,344**
- Fees: **-$2,102.2**
- Net P&L after fees: **-$22,446.2**
- CALL P&L: **-$20,695**
- PUT P&L: **+$351**

### Technical Validation (Stage6.14)

| Area | Result | Evidence |
|------|--------|----------|
| Pre-market ladder active | ✅ Working | `PREMARKET_VIX_LADDER=50`, `PREMARKET_LADDER_CALL_BLOCK=15` |
| Cross-blocking fix | ✅ Working | Intraday + spread entries coexist on same timestamps/days |
| OCO margin reject path | 🟡 Fix applied (pending validation) | Static + fallback force-close timing moved to config-driven 15:25 path |
| Intraday signal execution path | 🟡 Fix applied (pending validation) | Approved `micro_state` is now reused in entry signal path |
| VASS execution continuity | 🟡 Fix applied (pending validation) | Assignment grace window added before non-mandatory assignment exits |

### New / Reconfirmed Bugs from Stage6.14

| ID | Bug | Severity | Evidence |
|----|-----|:--------:|----------|
| T-27 | OCO sibling margin reject race at force-close | P0 | Repeating `Order Error`/`INVALID` pairs at 15:30 for stale OCO ids; timing mitigation applied |
| T-28 | Intraday approved signals dropped before order placement | P0 | `INTRADAY_SIGNAL_DROPPED=281` (72.99% of approved signals); single-update mitigation applied |
| T-29 | Assignment guard instantly liquidates every VASS spread | P0 | `SPREAD: POSITION_REGISTERED=23`, `ASSIGNMENT_RISK_EXIT=23`, `POSITION_REMOVED=23`; assignment grace applied |
| O-12 | CALL-side loss concentration persists | P1 | CALL bucket contributes nearly all net loss; stress CALL block applied |

---

## Executive Summary

Latest validated run (`V6_13_a`, Dec 2021-Mar 2022) confirms technical progress in direction consistency and OCO activation, but strategy performance remains poor with **-$48,371** net P&L and **43.57%** win rate.  
Primary unresolved technical risks are **margin submit regression** and **assignment containment leak**. Primary optimization blockers remain **high Micro NO_TRADE gating**, **high VASS contract rejection**, and **CALL-side loss concentration**.

---

## Bug List (Current Run) — Technical

| Priority | Bug | Evidence | Impact | Status |
|:--------:|-----|----------|--------|:------:|
| **P0** | Margin submit regression (`ROUTER_MARGIN_WIDTH_INVALID`) | **23** occurrences in `V6_13_a` log during spread routing | Valid setups rejected; missed entries and unstable margin path | 🟡 Applied (Pending Validation) |
| **P0** | Assignment containment leak | 2022-03-29 assignment path still produced QQQ stock liquidation | One uncontained assignment path can still create large equity loss | 🔴 Open |
| **P1** | Buying power rejects still elevated | `Insufficient buying power` **88**, `MARGIN_CB_SKIP` **46** | Frequent rejected orders and execution instability | 🔴 Open |
| **P1** | Exit combo metadata incomplete (historical root cause) | Close signals lacked `spread_width`/credit metadata; router returned `ROUTER_MARGIN_WIDTH_INVALID` | Assignment-protective exits failed to execute reliably | 🟡 Applied (Pending Validation) |
| **P1** | Close path margin gate too strict for risk-reduction exits | Router blocked close combos on estimator failures | Risk-reduction exits could not fire when most needed | 🟡 Applied (Pending Validation) |
| **P1** | Spread closing lock persistence after close-submit failures | `is_closing=True` could stay latched after close submit rejection | Suppressed later exit retries | 🟡 Applied (Pending Validation) |
| **P1** | Pre-entry margin check strictness mismatch | Rejections occur despite pre-check passes in some windows | Indicates mismatch between local estimate and broker model timing | 🟡 Monitor |
| **P1** | Pre-filter drops zero-bid long-leg candidates | `main.py:3872-3873` skips contracts with `bid<=0/ask<=0` in candidate builder | Missed valid long-leg contracts; lower VASS constructability | 🟡 Applied (Pending Validation) |
| **P1** | Credit selector lacks OI/spread quality filters | `options_engine.py:2974+` credit short/long selection has no explicit OI/spread gates | Increased rejects, unstable fills, poorer spread quality | 🟡 Applied (Pending Validation) |
| **P1** | Credit cooldown cross-blocking | `options_engine.py:3015` shared `CREDIT` cooldown key | Bear-call and bull-put credit attempts can block each other | 🟡 Applied (Pending Validation) |
| **P1** | Micro intraday can consume swing daily capacity | Shared global options daily counter used by both modes | VASS entries suppressed even when swing slot/risk is available | 🟡 Applied (Pending Validation) |
| **P0** | OCO sibling margin reject race at force-close | Stage6.14: `Insufficient buying power=24`, all clustered at 15:30 | Stale exit orders still create invalid/margin noise and execution instability | 🟡 Applied (Pending Validation) |
| **P0** | Intraday approved signals dropped before order placement | Stage6.14: `APPROVED=385`, `DROPPED=281`, `EXECUTED=104` | Most approved signals never become orders | 🟡 Applied (Pending Validation) |
| **P0** | VASS spreads instantly closed by assignment guard | Stage6.14: `POSITION_REGISTERED=23`, `ASSIGNMENT_RISK_EXIT=23` | Swing engine rendered ineffective; churn losses | 🟡 Applied (Pending Validation) |
| **P2** | Legacy filter parsing anomaly tracking | 2015 had `DTE=287` parse anomalies; not reproduced in `V6_13_a` | Keep diagnostic hooks until two clean multi-year runs | 🟡 Monitor |

## Bug List (Current Run) — Optimization

| Priority | Bug | Evidence | Impact | Status |
|:--------:|-----|----------|--------|:------:|
| **P1** | Net strategy still negative | 140 trades, Win **43.57%**, Net **-$48,371** | Core strategy stack still unprofitable | 🔴 Open |
| **P1** | Micro NO_TRADE gating too high | `INTRADAY blocked NO_TRADE=462` | Participation throttled in tradeable windows | 🔴 Open |
| **P1** | VASS validation friction high | `VASS_REJECTION=575` vs `VASS_ENTRY=40` | Swing opportunities still over-filtered | 🔴 Open |
| **P1** | CALL loss concentration | CALL trades **85**, P&L **-$22,926** | Direction/strategy mix still biased to losing call setups | 🔴 Open |
| **P1** | CALL concentration still dominates latest run | Stage6.14: CALL P&L **-$20,695** vs PUT **+$351** | Optimization still not regime-compatible in stress periods | 🔴 Open |
| **P2** | Multi-day carry efficiency | 51/140 trades held >1 day | Carry/expiry handling likely reducing edge | 🟡 Monitor |
| **P2** | Signal quality in neutral micro states | NO_TRADE reasons cluster: CONFIRMATION_FAIL/QQQ_FLAT/REGIME_NOT_TRADEABLE | Conviction thresholds likely too strict/lagging | 🟡 Monitor |

## Performance Snapshot

**All trades**
- Total trades: **140**
- Win%: **43.57%** (61 wins / 79 losses)
- Net P&L: **-48,371**

**By direction**
- Calls: **85 trades**, **P&L -22,926**
- Puts: **54 trades**, **P&L -1,993**

**Underlying assignments**
- `QQQ` equity liquidation event: **1** (2022-03-29 assignment containment leak)

---

## Order Mix

- Total orders: **425**
- Filled orders: **284**
- Invalid orders: **48**
- Canceled orders: **93**

**Observation:** Failure rate remains high (`(48 + 93) / 425 = 33.2%`), indicating reliability issues despite improved instrumentation.

---

## Signal → Execution Metrics

- `INTRADAY_SIGNAL_APPROVED`: **396**
- `INTRADAY blocked NO_TRADE`: **462**
- Top NO_TRADE reasons: `CONFIRMATION_FAIL=155`, `QQQ_FLAT=117`, `REGIME_NOT_TRADEABLE=100`, `VIX_STABLE_LOW_CONVICTION=41`

**Observation:** Approval and block reasons are now visible, but gating remains strict and materially limits participation.

---

## VASS Activity

- `VASS_REJECTION`: **575**
- `VASS_ENTRY`: **40**
- Rejection split: `DEBIT_ENTRY_VALIDATION_FAILED=292`, `CREDIT_ENTRY_VALIDATION_FAILED=283`

**Observation:** VASS now enters more often than prior runs, but validation rejects are still dominant.

---

## Assignment Prevention

- `ASSIGNMENT_RISK_EXIT`: present
- `PREMARKET_ITM` checks: present
- `QQQ` equity liquidation event: **1** (2022-03-29)

**Conclusion:** Assignment safeguards improved but are not yet airtight.

---

## Major Issues (Ranked)

**P1 — Low Win Rate + Negative P&L**
   - Calls remain the largest loss bucket (**-22.9k**).
   - Net result stays deeply negative despite technical fixes.

**P1 — Micro gating remains strict**
   - `NO_TRADE` and confirmation checks are still blocking a large share of opportunities.

**P1 — VASS validation friction**
   - Debit and credit validations both reject at high volume (292/283).

**P0 — Margin/assignment reliability**
   - `ROUTER_MARGIN_WIDTH_INVALID` and one assignment containment leak remain unresolved technical risks.

---

## What Improved vs V6.9

✅ **Direction mismatch fixed** (approved vs selected direction mismatch = 0)  
✅ **OCO wiring active** (`OCO_CREATED=89`, `OCO_ACTIVATED=89`)  
✅ **Scheduler callback stability improved** (no legacy PRE_MARKET_SETUP crash in this run)  

---

## Fixes Applied (Pending Validation)

- **V6.13 optimization tuning (config-only)**:
  - Micro participation: lowered noise/fallback thresholds (`QQQ_NOISE_THRESHOLD`, `INTRADAY_QQQ_FALLBACK_MIN_MOVE`, `MICRO_SCORE_*_CONFIRM`, `INTRADAY_DEBIT_FADE_VIX_MIN`, `INTRADAY_FADE_MIN_MOVE`, `INTRADAY_ITM_MIN_*`).
  - Loss control: tightened ITM stop/target and ATR base/floor (`INTRADAY_ITM_STOP`, `INTRADAY_ITM_TARGET`, `OPTIONS_ATR_STOP_MULTIPLIER`, `OPTIONS_ATR_STOP_MIN_PCT`).
  - VASS constructability: widened credit short-leg delta max and reduced spread width target/min (`CREDIT_SPREAD_SHORT_LEG_DELTA_MAX`, `SPREAD_WIDTH_MIN`, `SPREAD_WIDTH_TARGET`).
  - Choppy recycling: re-enabled tight neutrality exit (`SPREAD_NEUTRALITY_EXIT_*`).
- **VASS spread failure cooldown trap**: Added DTE fallback ranges and only apply cooldown after all ranges fail.
- **VASS rejection visibility**: Added compact `FailStats` summary to throttled `VASS_REJECTION` logs.
- **Swing/Intraday cross‑blocking**: Replaced `has_position()` gate with mode‑specific `can_enter_swing()` and `has_intraday_position()` checks.
- **Micro Dir=None reduction**: Lowered `QQQ_NOISE_THRESHOLD` (0.35→0.25), `INTRADAY_QQQ_FALLBACK_MIN_MOVE` (0.50→0.35), `MICRO_SCORE_*_CONFIRM` (52/48→50/50), and `INTRADAY_ITM_MIN_MOVE` (0.80→0.45).
- **CALL bias control**: Asymmetric UVXY conviction thresholds (CALL -5%, PUT +2.5%) and NEUTRAL aligned trades allowed at reduced size.
- **Micro NO_TRADE visibility**: Throttled `MICRO_NO_TRADE` logs added with VIX/QQQ move + score.
- **Order tagging**: Options orders tagged as `MICRO` (intraday) or `VASS` (swing) in router for web/log clarity.
- **VASS credit selection fix**: Candidate contracts now filtered by strategy-required option right (CALL/PUT), not macro direction.
- **VASS_ENTRY logging**: Added explicit `VASS_ENTRY` log on spread signal creation.
- **Signal→execution visibility**: Added `INTRADAY_SIGNAL_DROPPED` log for approved signals that fail to produce orders.
- **Overnight gap protection (swing)**: Added EOD VIX-based close for spreads (all days).
- **VIX spike auto-exit (spreads)**: Exit bullish spreads on VIX level/5D spike.
- **Regime deterioration exit (spreads)**: Exit on regime drop/rise vs entry score.
- **Monthly P&L tracking (O-10)**: New `MonthlyPnLTracker` class in `utils/monthly_pnl_tracker.py`. Tracks P&L by engine (TREND, MR, OPT, HEDGE, YIELD) and month. Integrated into `main.py` for all fill handlers (TREND exits, MR exits, intraday options, spread closes). Persisted via ObjectStore. Logs EOD summary with win rate and net P&L.

---

## What Still Needs Fixing

1. **Reduce Dir=NONE further** (micro thresholds still too conservative)  
2. **Improve VASS contract selection rate** (rejections still very high)  
3. **Close the signal→execution gap**  
4. **Improve call profitability** (strategy logic or stop/target tuning)

---

## Recommended Next Actions

1. **Lower VASS rejection rate**
   - Re‑examine delta bounds vs market data in 2022.
2. **Tighten intraday execution pipeline**
   - Add explicit logging for approval→submit→fill to trace drop‑offs.
3. **Audit CALL strategy**
   - Losses are concentrated in CALLs; check for wrong regime usage or stop/target scaling.

---

## Optimization Fix List (Summary)

**Execution & Pipeline**
- Add explicit log markers at: signal approved → order submit → fill → register.
- Track “approved but not submitted” reasons (margin pre‑check, cooldown, time window).

**Micro Signal Quality**
- Further reduce `Dir=NONE` by narrowing VIX‑stable band or adding a secondary QQQ fallback for low‑VIX days.
- Add a minimum conviction threshold that scales with VIX (lower in low‑VIX regimes).

**VASS Construction**
- Instrument rejection reasons by category (delta, DTE, credit, liquidity) and log counts.
- Relax delta/DTE only for the **rejection-dominant category**, not globally.

**Strategy Profitability**
- Review CALL losses by regime; disable CALLs in CAUTIOUS/DEFENSIVE unless macro+micro align.
- Re‑test stop/target symmetry for CALLs only; keep PUT logic unchanged if it is less negative.

**Assignment & Risk**
- Keep DTE=1 force‑close and pre‑market ITM check (working).
- Add a pre‑entry assignment buffer check for **all** spreads (not just on exit).

---

# V6.11 Options Engine Audit — 2017 Full Year

**Run:** `V6_11_2017FullYear`  
**Files reviewed:**  
`docs/audits/logs/stage6.5/V6_11_2017FullYear_logs.txt`  
`docs/audits/logs/stage6.5/V6_11_2017FullYear_orders.csv`  
`docs/audits/logs/stage6.5/V6_11_2017FullYear_trades.csv`

**Date:** 2026-02-09

---

## Performance Snapshot (2017)

- Total trades: **35**
- Win%: **57.1%**
- Net P&L: **+25,967**
- Fees: **1,600.30**

**By direction**
- Calls: **35 trades**, **+25,967 P&L**
- Puts: **0 trades**

---

## Signal / Execution Metrics (2017)

- `MICRO_UPDATE`: **7,260**
- `Dir=NONE`: **6,928** (~95.4%)
- `Dir=CALL`: **269**
- `Dir=PUT`: **73**
- `INTRADAY_SIGNAL`: **20**
- `INTRADAY_SIGNAL_APPROVED`: **18**
- `INTRADAY: Selected`: **8**
- `INTRADAY_RESULT`: **1**
- `VASS_ENTRY`: **34**
- `VASS_REJECTION`: **64**
- `PREMARKET_ITM`: **3**

---

## 2017 Observations

1. **Micro still largely inactive** (Dir=NONE ~95%).  
2. **Intraday execution nearly zero** (1 result for the entire year).  
3. **No PUT trades**, even in minor pullbacks.  
4. **Despite low activity, P&L was positive** due to CALL success in bull market.

---

# V6.11 Options Engine Audit — 2015 Full Year

**Run:** `V6_11_2015FullYear`  
**Files reviewed:**  
`docs/audits/logs/stage6.5/V6_11_2015FullYear_logs.txt`  
`docs/audits/logs/stage6.5/V6_11_2015FullYear_orders.csv`  
`docs/audits/logs/stage6.5/V6_11_2015FullYear_trades.csv`

**Date:** 2026-02-09

---

## Performance Snapshot (2015)

- Total trades: **109**
- Win%: **44.0%**
- Net P&L: **+16,003**
- Fees: **4,354.60**

**By direction**
- Calls: **84 trades**, **+24,075 P&L**
- Puts: **25 trades**, **-8,072 P&L**

---

## Signal / Execution Metrics (2015)

- `MICRO_UPDATE`: **7,300**
- `Dir=NONE`: **6,309** (~86.4%)
- `Dir=CALL`: **608**
- `Dir=PUT`: **403**
- `INTRADAY_SIGNAL`: **101**
- `INTRADAY_SIGNAL_APPROVED`: **76**
- `INTRADAY: Selected`: **56**
- `INTRADAY_RESULT`: **18**
- `VASS_ENTRY`: **84**
- `VASS_REJECTION`: **461**
- `ASSIGNMENT_RISK_EXIT`: **2**

---

## 2015 Observations

1. **Overall P&L positive, but PUT trades negative.**  
2. **Dir=NONE still high**, though lower than 2017.  
3. **Intraday conversion weak** (101 signals → 18 results).  
4. **VASS still rejected frequently** (461 rejections).

---
