# WORKBOARD.md - Alpha NextGen V2 Task & Ownership Board

> **Single source of truth for:** Who owns what, what's in progress, what's next.
>
> **Rules:**
> - Pull before editing (avoid conflicts)
> - Move tasks by cut/paste
> - Update when starting/finishing work

---

## Team

| Member | Initials | Focus Area |
|--------|----------|------------|
| Vigneshwaran | VA | Strategy Engines, Models |
| [Collaborator] | -- | Core Engines, Execution |

---

## V2 Fork Status

> **V2 Forked from V1 v1.0.0** (2026-01-26)
>
> **Completed:**
> - PHASE 0: V1 Structural Audit ✅
> - PHASE 1: Repository Hard Fork ✅
> - PHASE 2: Core-Satellite Refactoring ✅
> - PHASE 3: Master Plan (`V2_IMPLEMENTATION_ROADMAP.md`) ✅
>
> **Architecture:**
> - `engines/core/` - Foundational engines (70%)
> - `engines/satellite/` - Conditional engines (0-30%)
> - `docs/v2-specs/` - V2.1 specifications
>
> **Tests:** 990 passed (as of 2026-01-27)

---

## Current Sprint: V2.1.1 Options Engine Redesign

> **Status:** V2.1.1 COMPLETE ✅ — Options Engine Dual-Mode + Micro Regime Engine
>
> See `V2_IMPLEMENTATION_ROADMAP.md` for full roadmap.
> See `docs/v2-specs/V2_1_OPTIONS_ENGINE_DESIGN.txt` for complete specification.

### V2.1.1 Completion Summary

| Phase | Focus | Status |
|-------|-------|--------|
| V2.1 Phase 1 | Trend Engine V2 + 5-Level Circuit Breakers | ✅ Complete |
| V2.1 Phase 2 | Mean Reversion VIX Filter | ✅ Complete |
| V2.1 Phase 3 | Options Engine (4-factor scoring, OCO) | ✅ Complete |
| V2.1 Phase 4 | Integration & Orchestration (Core-Satellite) | ✅ Complete |
| V2.1.1 | Options Engine Redesign (Dual-Mode + Micro Regime) | ✅ Complete |

### V2.1.1 Options Engine Redesign (Jan 28, 2026)

| Component | Description | Status |
|-----------|-------------|--------|
| Dual-Mode Architecture | Swing (15%) + Intraday (5%) modes | ✅ Designed |
| Micro Regime Engine | VIX Level × VIX Direction = 21 regimes | ✅ Designed |
| VIX Direction Classification | 7 categories (FALLING_FAST to WHIPSAW) | ✅ Designed |
| Tiered VIX Monitoring | 5min/15min/30min/60min layers | ✅ Designed |
| VIX1D Evaluation | Rejected (0.95 correlation during trading hours) | ✅ Complete |
| Documentation | V2_1_OPTIONS_ENGINE_DESIGN.txt (2,135 lines) | ✅ Complete |

### Backtest Validation Progress

| Stage | Duration | Purpose | Status | Date |
|:-----:|----------|---------|:------:|------|
| 1 | 1 day | Basic validation | **PASS** ✅ | 2026-01-30 |
| 2 | 2 months | Short-term behavior | **V2.3.19 READY** 🟡 | 2026-02-01 |
| 3 | 3 months | Position lifecycle | Pending | — |
| 4 | 1 year | Full annual cycle | Pending | — |
| 5 | 5 years | Long-term stress test | Pending | — |

> **Results Document:** `docs/audits/backtest-results.md`
> **Stage 2 Code Audits:** `docs/audits/stage2-codeaudit.md`, `docs/audits/stage2-codeaudit2.md`
> **Logs:** `docs/audits/logs/stage2/`

### V2.3.12 Backtest Results (2026-01-31)

**Backtest:** V2.3.12-ComboFix-2month | **Result:** +4.09% | **Orders:** 143 | **Options:** 7 only!

| Metric | Value | Notes |
|--------|------:|-------|
| Return | +4.09% | First profitable backtest |
| Equity | $50,000 → $52,047 | |
| Orders | 143 | But only 7 options trades! |
| Sharpe | 0.656 | |
| Sortino | 0.905 | |
| Win Rate | 42% | |
| Drawdown | 9.10% | |

**URL:** https://www.quantconnect.com/project/27678023/99384af2cd3dfa3219d6f95ba2f584fd

**Issue Found:** PART 16 analysis revealed 99% of options signals were blocked by `_entry_attempted_today` throttle. V2.3.14 fixes this - expecting significantly more options trades.

### Stage 2 V2.3.2 Backtest Validation (Historical)

**Previous Backtest:** Smooth Magenta Bat | **Result:** -8.33% | **Orders:** 9
**Latest Backtest:** Casual Orange Cobra | **Result:** -6.98% | **Orders:** 14 | **Fees:** $171.51

**V2.3.2 Critical Fixes Applied (All 5 from Architect Audit Part 1-2):**
1. ✅ **OPT_INTRADAY source limit** - Added to SOURCE_ALLOCATION_LIMITS (5% max)
2. ✅ **Requested quantity enforced** - Router now uses `requested_quantity` from engine
3. ✅ **RegimeState.score fixed** - Changed to `smoothed_score`
4. ✅ **Engines separated** - Intraday positions tracked in `_intraday_position`, not `_position`
5. ✅ **Intraday 15:30 exit working** - Force close now checks correct position variable
6. ✅ **Intraday DTE expanded** - 0-5 DTE for backtest data availability (was 0-2)

**V2.3.3 Fixes from Architect Audit Part 3 (2026-01-31):**
| # | Finding | Severity | Status |
|:-:|---------|:--------:|:------:|
| 1 | **Trend Allocation Flattening** - TrendEngine returns `target_weight=1.0` for all symbols | CRITICAL | ✅ FIXED |
| 2 | **Closing Trade Bypass** - MIN_TRADE_VALUE check skips worthless option closes | MEDIUM | ✅ FIXED |
| 3 | **Exit Race Condition** - Duplicate close orders if fill delayed | LOW | ✅ FIXED |

**V2.3.3 Fixes from Architect Audit Part 4 (2026-01-31):**
| # | Finding | Severity | Status |
|:-:|---------|:--------:|:------:|
| 1 | **Traffic Jam** - Swing spreads blocked by intraday early return in `_scan_options_signals` | CRITICAL | ✅ FIXED |
| 2 | **Trend Throttling** - `MAX_CONCURRENT_TREND_POSITIONS=2` blocking TNA/FAS | HIGH | ✅ FIXED (→4) |
| 3 | **Minified Files Desync** - `main_minified.py` had old single-leg options code | CRITICAL | ✅ REMOVED |

**V2.3.4 Fixes from Architect Audit Part 5-7 (2026-01-31):**
| # | Finding | Severity | Status |
|:-:|---------|:--------:|:------:|
| 1 | **Cold Start Bypass** - Options entering on Day 1 during cold start | CRITICAL | ✅ FIXED |
| 2 | **Direction Mismatch** - Contract selected BEFORE direction determined | CRITICAL | ✅ FIXED |
| 3 | **Inverted Trade** - Bought CALL when should have bought PUT for fade | CRITICAL | ✅ FIXED |
| 4 | **Global Kill Switch** - Options loss liquidating healthy trend positions | HIGH | ✅ FIXED (engine-specific) |
| 5 | **Spread Criteria Too Tight** - OI 5000, delta 0.25-0.40 too restrictive | HIGH | ✅ FIXED (OI→1000, delta 0.15-0.45) |
| 6 | **DTE Too Wide** - 0-5 DTE not true 0DTE trading | MEDIUM | ✅ FIXED (→0-1 DTE) |
| 7 | **VIX Resolution Daily** - VIX only updated once/day, not intraday | CRITICAL | ✅ FIXED (→Minute) |
| 8 | **QQQ Move Not in Regime** - Direction determined separately from regime | HIGH | ✅ FIXED (incorporated) |

**V2.3.4 Key Changes:**
- VIX subscribed at `Resolution.Minute` (was Daily) - live intraday updates
- Added `_vix_15min_ago` tracker for short-term trend detection
- Added `QQQMove` enum (UP_STRONG, UP, FLAT, DOWN, DOWN_STRONG)
- Created `recommend_strategy_and_direction()` - combined decision in Micro Regime
- Direction now determined INSIDE regime assessment, not separately
- Data gathered every minute, processed every 15 minutes (no log spam)

**Status:** V2.3.4 fixes complete - Ready for backtest

**V2.3.5 Fixes from Architect Audit Part 9 (2026-01-31):**
| # | Finding | Severity | Status |
|:-:|---------|:--------:|:------:|
| 1 | **Open Interest Too High** - 5000 filtered 80% of 0-5 DTE contracts | HIGH | ✅ FIXED (→500) |
| 2 | **Spread Delta Too Narrow** - 0.45-0.55 (±0.05) misses valid ATM | HIGH | ✅ FIXED (→0.40-0.60) |
| 3 | **Intraday Delta Tolerance** - 0.15 too restrictive for 0.30 target | MEDIUM | ✅ FIXED (→0.20) |

**V2.3.5 Stage 3 Backtest Results (2026-01-31):**
- **Name:** Hipster Yellow-Green Hornet
- **Period:** Jan 1 - Mar 31, 2024 (Q1 2024, 3 months)
- **Return:** -1.42% (improved from -3.45% Stage 2)
- **Drawdown:** 12.30%
- **Orders:** 95 (up from 7 - PART 9 fix working!)
- **Win Rate:** 43%
- **Backtest URL:** https://www.quantconnect.com/project/27678023/90fcb04626294aba0c625261fba8002d

**Status:** V2.3.5 Stage 3 complete - Options finding contracts, 88 more trades

**V2.3.6 Fixes from "Upgraded Blue Whale" Analysis (2026-01-31):**
| # | Finding | Severity | Status |
|:-:|---------|:--------:|:------:|
| 1 | **Spread Orphaned Long Leg** - IBKR rejects short leg (margin), long leg fills | CRITICAL | ✅ FIXED |
| 2 | **Margin Pre-Check Missing** - No validation before spread submission | HIGH | ✅ FIXED |
| 3 | **Intraday OI Too High** - 500 OI filters out most 0DTE PUTs on up days | HIGH | ✅ FIXED (→200) |
| 4 | **Intraday Spread Too Tight** - 10% rejects normal 0DTE spreads | HIGH | ✅ FIXED (→15%) |
| 5 | **10:30 Gatekeeper Blocking** - Hardcoded block kills 10:00-10:30 momentum window | HIGH | ✅ FIXED (removed) |
| 6 | **Trend Stops Too Tight** - ATR×3.0 suffocating trades in choppy markets | MEDIUM | ✅ FIXED (→3.5) |
| 7 | **SHV Churn** - $2K threshold causing excessive rebalancing | LOW | ✅ FIXED (→$10K) |

**V2.3.6 Key Changes:**
- Added `_pending_spread_orders` dictionary to track spread order pairs (short→long)
- Pre-submission margin check blocks spread if short leg would fail ($10K/contract estimate)
- OnOrderEvent detects short leg `Invalid` status and liquidates orphaned long leg
- Successful fill cleanup removes spread from tracking
- Relaxed intraday filters: OI 500→200, Spread 10%→15% (0DTE reality)
- Removed 10:30 gatekeeper - intraday window now 10:00-15:00 (was 10:30-15:00)
- Widened Chandelier stops: BASE 3.0→3.5, TIGHT 2.5→3.0, TIGHTER 2.0→2.5
- Raised profit thresholds: TIGHT 10%→15%, TIGHTER 20%→25%
- Raised SHV_MIN_TRADE: $2K→$10K (reduce rebalancing churn)
- Logs: `SPREAD: BLOCKED`, `SPREAD: Tracking order pair`, `SPREAD: LIQUIDATING orphaned long leg`

**Root Cause (Spread):** IBKR treats spread legs as separate orders requiring naked short margin (~$343K) instead of spread margin (~$11K). Without margin check, long leg fills but short leg fails, leaving orphaned position.

**Root Cause (Intraday):** 0DTE PUTs on up days have lower OI and wider spreads. Cascade of filters (DTE→Direction→Delta→OI→Spread) left 0 contracts passing.

**Status:** V2.3.6 fixes complete - Backtest "Pensive Red Rabbit" running

**V2.3.7 Fixes from "Pensive Red Rabbit" Analysis (2026-01-31):**
| # | Finding | Severity | Status |
|:-:|---------|:--------:|:------:|
| 1 | **Cash Death Spiral** - YieldSleeve orders $26K SHV with $430 margin | CRITICAL | ✅ FIXED |
| 2 | **Intraday Filters Too Tight** - OI 200 + Spread 15% still filtering valid contracts | HIGH | ✅ FIXED (OI→100, Spread→25%) |
| 3 | **Spread Short Leg Failures** - 4,578 "No valid OTM contract" errors | HIGH | ✅ FIXED (width $2-5, delta 0.10-0.50) |
| 4 | **ADX Blocking Early Trends** - 71 entries blocked by lagging ADX indicator | MEDIUM | ✅ FIXED (threshold 25→20) |

**V2.3.7 Key Changes:**
- Added margin cap in `yield_sleeve.py`: `min(unallocated, MarginRemaining * 0.95)`
- Widened intraday filters: OI 200→100, Spread 15%→25%
- Relaxed spread short leg: SPREAD_WIDTH_MIN 3→2, SPREAD_WIDTH_TARGET 5→3
- Relaxed spread delta: SHORT_LEG_DELTA_MIN 0.15→0.10, MAX 0.45→0.50
- Lowered ADX thresholds: ENTRY 25→20, WEAK 20→15, MODERATE 25→20

**Root Cause (Cash Death Spiral):** YieldSleeve calculated target from TotalPortfolioValue but didn't check MarginRemaining. Pending orders from other engines consumed margin before SHV order executed.

**Root Cause (Filters):** 0DTE contracts in volatile markets have lower OI and wider spreads. Previous thresholds (OI=200, Spread=15%) filtered out most tradeable contracts.

**Root Cause (ADX):** ADX is lagging - by time it reaches 25, the trend move is often half over. Lowering to 20 captures earlier entries.

**Status:** V2.3.7 fixes complete - Ready for backtest validation

**V2.3.8 Fixes from PART 14 Analysis (2026-01-31):**
| # | Finding | Severity | Status |
|:-:|---------|:--------:|:------:|
| 1 | **TNA/FAS Volatility Trap** - Same ATR×3.5 stops for 2x and 3x ETFs | HIGH | ✅ FIXED (3x→ATR×2.5) |
| 2 | **Spread "Impossible Triangle"** - Width + Delta + Liquidity blocking all spreads | HIGH | ✅ FIXED (delta drives selection) |
| 3 | **0DTE Stop Slippage** - 20-30% stops slip to 60% loss on fast 0DTE moves | HIGH | ✅ FIXED (0DTE→15% stops) |

**V2.3.8 Key Changes:**
- Added `TREND_3X_SYMBOLS = ["TNA", "FAS"]` for 3× leveraged ETFs
- Added tighter 3× Chandelier multipliers: BASE 2.5, TIGHT 2.0, TIGHTER 1.5
- Updated trend_engine with `get_chandelier_multipliers()` for symbol-specific stops
- Removed strict width filter from spread selection - delta now primary criterion
- Widened `SPREAD_WIDTH_MAX` from $5 to $15 (width no longer blocks trades)
- Changed short leg sort from width proximity to delta proximity (target delta ~0.30)
- Added `OPTIONS_0DTE_STOP_PCT = 0.15` for tighter 0DTE stops
- Updated `calculate_position_size()` to accept `days_to_expiry` parameter
- Hard stops already placed via OCO Manager `StopMarketOrder` (confirmed working)

**Root Cause (3x Volatility):** TNA/FAS swing 5-7% daily vs 2-3% for QLD/SSO. Same ATR×3.5 stop was too wide for 3× leverage, allowing 17%+ losses.

**Root Cause (Spread Triangle):** Fixed width ($3-5) + fixed delta (0.15-0.45) + OI (100+) = rarely all satisfied together. Market offers $10 wide at 0.15 delta OR $5 wide at 0.30 delta, not both.

**Root Cause (0DTE Slippage):** StopMarketOrder fills at next available price after trigger. 0DTE options can gap from $0.50 to $0.20 in 30 seconds. 15% stop limits max loss to ~30% even with slippage.

**Status:** V2.3.8 fixes complete - Ready for backtest validation

**V2.3.9 Fix: ComboMarketOrder for Spreads (CTA Memo 2026-01-31):**
| # | Finding | Severity | Status |
|:-:|---------|:--------:|:------:|
| 1 | **$729K Margin Rejection** - Sequential spread legs treated as naked short | CRITICAL | ✅ FIXED |

**V2.3.9 Key Changes:**
- Added `submit_combo_order()` to execution_engine.py
- Added `is_combo`, `combo_short_symbol`, `combo_short_quantity` to OrderIntent
- Updated `_generate_orders` to create single combo order for spreads
- Updated `execute_orders` to use `ComboMarketOrder` for combo orders
- Both legs submitted in single ticket = broker sees NET risk

**Root Cause:** Broker treats sequential spread legs as separate orders. Selling short leg first = naked short requiring $729K margin. Broker doesn't "know" we plan to buy protection 100ms later.

**Solution (per CTA Memo):** "Using Combo Orders will automatically calculate the multi-leg margin instead of classical one." ComboMarketOrder submits both legs atomically → broker calculates spread margin (~$42K) not naked margin (~$729K).

**Status:** V2.3.9 fixes complete - Ready for backtest validation

**V2.3.10 Fixes from PART 15 Forensics (2026-01-31):**
| # | Finding | Severity | Status |
|:-:|---------|:--------:|:------:|
| 1 | **ADX Entry/Exit Churn** - Entry at ADX >= 15 but exit at ADX < 20 = immediate churn | HIGH | ✅ FIXED |
| 2 | **Spread Filter 100% Broken** - 5% bid-ask threshold rejects all ATM contracts | CRITICAL | ✅ FIXED (→15%) |
| 3 | **Orphaned Options** - `_pending_contract` not set in intraday signal | CRITICAL | ✅ FIXED |
| 4 | **No DTE Exit** - Single-leg options held to expiration → auto-exercise | CRITICAL | ✅ FIXED (→2 DTE) |
| 5 | **Exit Signals Not Checked** - Single-leg profit target/stop/DTE exit missing | HIGH | ✅ FIXED |

**V2.3.10 Key Changes:**
- Restored ADX thresholds: ADX_WEAK_THRESHOLD 15→20, ADX_MODERATE_THRESHOLD 20→25
- Widened spread filter: OPTIONS_SPREAD_MAX_PCT 5%→15%
- Set `_pending_contract` in `check_intraday_entry_signal()` before `register_entry()`
- Added OPTIONS_SINGLE_LEG_DTE_EXIT = 2 (close by 2 DTE)
- Added `check_exit_signals()` call in `_monitor_risk_greeks()`

**Root Cause (ADX Churn):** Entry allowed at ADX=15 but exit triggers at ADX<20. Position opens, ADX is 15.5, next bar ADX is 15.2, exit triggers immediately.

**Root Cause (Spread Filter):** ATM contracts have wider bid-ask spreads than OTM. 5% threshold rejected all valid contracts - logs showed "No valid ATM contract" 100% of the time.

**Root Cause (Orphaned Options):** `check_intraday_entry_signal()` returned signal but didn't set `_pending_contract`. When `register_entry()` was called, it found no pending contract.

**Status:** V2.3.10 fixes complete - Ready for backtest validation

**V2.3.11 Fixes: SNIPER 0DTE Enhancement + Expiring Options Safety (2026-01-31):**
| # | Finding | Severity | Status |
|:-:|---------|:--------:|:------:|
| 1 | **Auto-Exercise Risk** - ITM options held past 4 PM get exercised → stock position | CRITICAL | ✅ FIXED |
| 2 | **VIX Barrier Too High** - VIX < 15 required for VERY_CALM, blocking calm market trades | HIGH | ✅ FIXED (→11.5) |
| 3 | **VIX Levels Misaligned** - Thresholds not matching market reality | MEDIUM | ✅ FIXED |

**V2.3.11 Key Changes:**
- Added `check_expiring_options_force_exit()` - closes options expiring TODAY at 15:45
- Added config: `OPTIONS_EXPIRING_TODAY_FORCE_CLOSE_HOUR = 15, _MINUTE = 45`
- Added `_get_option_expiry_date()` helper in main.py
- Lowered VIX barriers: VIX_LEVEL_VERY_CALM_MAX 15→11.5, CALM_MAX 18→15, NORMAL_MAX 20→18
- Updated VIX level classification in `classify_vix_level()` to use config constants

**Root Cause (Auto-Exercise):** V2.3.9 backtest held ITM QQQ calls into Friday close. Saturday 5 AM broker auto-exercised → 800 shares assigned = $360K on $50K account (7:1 leverage). Lucky market gap up saved the account.

**Root Cause (VIX Barrier):** VIX typically ranges 12-18 in calm markets. Requiring VIX < 15 for VERY_CALM blocked most normal trading days from firing SNIPER 0DTEs.

**Status:** V2.3.11 fixes complete - Ready for backtest validation

**V2.3.12 Fixes: Enable More 0DTEs + Unchoke Trend Engine (2026-01-31):**
| # | Finding | Severity | Status |
|:-:|---------|:--------:|:------:|
| 1 | **ITM VIX Barrier Too High** - VIX > 25 required for ITM momentum, blocking most days | HIGH | ✅ FIXED (→11.5) |
| 2 | **ADX Choking Trend Engine** - ADX >= 20 blocking entries during grinding rallies | HIGH | ✅ FIXED (→15) |

**V2.3.12 Key Changes:**
- INTRADAY_ITM_MIN_VIX: 25 → 11.5 (enable 0-DTE ITM momentum in calm markets)
- ADX_ENTRY_THRESHOLD: 20 → 15 (catch trends earlier, ADX is lagging indicator)
- ADX_WEAK_THRESHOLD: 20 → 15 (allow entering on grinding trends)
- TREND_ADX_EXIT_THRESHOLD: 20 → 10 (allow holding during low momentum grind)

**Root Cause (ITM VIX):** ITM momentum strategy required VIX > 25, which only occurs during high volatility. Normal VIX range is 12-20, so ITM momentum was blocked 90%+ of the time.

**Root Cause (ADX Choke):** Late March 2024 was a "grinding rally" with low ADX. Market hit ATH but ADX stayed below 20, blocking all trend entries. ADX is lagging - by time it reaches 20, the move is often half over.

**Root Cause (ADX Exit Churn):** Entry at ADX >= 15 but exit at ADX < 20 caused immediate churn. Lowering exit to ADX < 10 allows holding positions during grinding periods.

**Status:** V2.3.12 BACKTEST PASSED ✅ - +4.09% Return, 143 Orders, Sharpe 0.656

**V2.3.13 FIX: Options Orders Not Executing (2026-02-01):**
| # | Finding | Severity | Status |
|:-:|---------|:--------:|:------:|
| 1 | **Intraday signals queued but never processed** - Missing `_process_immediate_signals()` call | CRITICAL | ✅ FIXED |
| 2 | **Spread signals also missing processing** - Same bug for swing spread orders | CRITICAL | ✅ FIXED |

**V2.3.13 Key Changes:**
- Added `_process_immediate_signals()` after intraday signal reception (main.py line 2561)
- Added `_process_immediate_signals()` after spread signal reception (main.py line 2626)

**Root Cause (Intraday):** In `_scan_options_signals()`, intraday signal was added to `_pending_weights` via `receive_signal()` but NEVER processed. Every other place in the codebase calls `_process_immediate_signals()` after receiving an IMMEDIATE signal. The function returned early via swing spread path (line 2575), and by the time `OnData` step 9 ran, the signal was lost.

**Root Cause (Spread):** Same bug - spread signals also use `Urgency.IMMEDIATE` but were not being processed immediately.

**Evidence from logs:**
```
INTRADAY: Selected PUT | Strike=425.0 | Delta=0.24 | DTE=0  ← Contract selected
SPREAD: No valid OTM contract for short leg                   ← Swing mode fails (different issue)
                                                              ← No order fired!
```

**Status:** V2.3.13 fixes complete - Ready for backtest validation

**V2.3.14 FIX: PART 16 Architect Recommendations (2026-02-01):**

| # | Finding | Severity | Status |
|:-:|---------|:--------:|:------:|
| 1 | **Entry throttle blocking 99% of signals** - `_entry_attempted_today` blocked ALL after first signal | CRITICAL | ✅ FIXED |
| 2 | **Hardcoded fade kills momentum** - Direction determined BEFORE engine recommendation | HIGH | ✅ FIXED |
| 3 | **Swing spreads fail, no fallback** - "No valid ATM contract" with no single-leg option | HIGH | ✅ FIXED |

**V2.3.14 Key Changes:**

**Fix 1: Entry Throttle (Critical)**
- Removed `_entry_attempted_today` flag check from `check_intraday_entry_signal()`
- Added `INTRADAY_MAX_TRADES_PER_DAY = 3` config (was implicit 1)
- Increment `_intraday_trades_today` when position is registered (after fill)
- Evidence: 639 DEBIT_FADE recommendations → 7 signals (99% blocked by old throttle)

**Fix 2: Momentum Direction**
- Added `get_intraday_direction()` method to OptionsEngine
- main.py now gets engine recommendation FIRST, then selects matching contract
- OLD: Hardcoded fade (QQQ up → PUT) blocked all momentum trades
- NEW: Engine decides direction based on VIX Level × VIX Direction regime

**Fix 3: Single-Leg Swing Fallback**
- When spread selection fails ("No valid ATM contract"), now falls back to single-leg ITM
- Uses existing `_select_swing_option_contract()` (0.70 delta, 5-45 DTE)
- Logs `SWING_FALLBACK: Single-leg {direction} after spread failure`

**Root Cause Analysis (from V2.3.12 logs):**
```
Jan 8: 52 DEBIT_FADE recommendations → 1 signal fired
  10:00-10:29: Blocked by time window (DEBIT_FADE starts 10:30)
  10:30: First signal fires, _entry_attempted_today = True
  10:33: Trade stops out
  10:34+: ALL subsequent signals blocked by _entry_attempted_today
```

**Status:** V2.3.14 fixes complete - Ready for backtest validation

**V2.3.15 FIX: SNIPER LOGIC - PART 17 Architect Recommendations (2026-02-01):**

| # | Finding | Severity | Status |
|:-:|---------|:--------:|:------:|
| 1 | **QQQ threshold too loose** - 0.15% threshold treating noise as signals | HIGH | ✅ FIXED (→0.35%) |
| 2 | **FADE missing min move check** - MOMENTUM has 0.80% but FADE had none | HIGH | ✅ FIXED (→0.50%) |
| 3 | **Max trades too high** - 3 trades/day is machine-gunner, not sniper | MEDIUM | ✅ FIXED (→2) |

**V2.3.15 Sniper Logic Key Changes:**

**Philosophy:** "Sniper, not Machine Gunner" - Wait for high-conviction setups, filter noise

**4-Gate System:**
| Gate | Purpose | Threshold |
|------|---------|-----------|
| Gate 0 | Pre-flight checks | Position, trades, time window |
| Gate 1 | Noise filter | QQQ move >= 0.35% |
| Gate 2 | VIX context | Direction determines strategy |
| Gate 3 | Strategy qualification | FADE >= 0.50%, MOMENTUM >= 0.80% |
| Gate 4 | Contract selection | DTE, delta, OI, spread |

**Config Changes:**
```python
# Gate 1: Noise Filter
QQQ_NOISE_THRESHOLD = 0.35  # V2.3.15: was 0.15%

# Gate 3a: FADE Strategy
INTRADAY_DEBIT_FADE_MIN_MOVE = 0.50  # V2.3.15: new threshold

# Trade Management
INTRADAY_MAX_TRADES_PER_DAY = 2  # V2.3.15: was 3 (sniper gets one retry)
```

**Code Changes:**
1. `classify_qqq_move()` - Uses `config.QQQ_NOISE_THRESHOLD` (0.35%) for UP/DOWN classification
2. `recommend_strategy_and_direction()` - Added Gate 3a FADE min move check (0.50%)

**Documentation:** `docs/v2-specs/SNIPER_LOGIC_V2.3.15.md` - Complete specification with flowchart

**Status:** V2.3.15 SNIPER LOGIC complete - Ready for backtest validation

**V2.3.16 FIX: PART 17 Delta + Direction Conflict (2026-02-01):**

| # | Finding | Severity | Status |
|:-:|---------|:--------:|:------:|
| 1 | **Delta 0.70 > max 0.60** - Swing mode targeting 0.70 but validation caps at 0.60 | CRITICAL | ✅ FIXED |
| 2 | **Direction conflict** - FADE PUT vs regime bullish (>65) = opposing bets | HIGH | ✅ FIXED |
| 3 | **FADE max cap missing** - No upper bound on fade moves (runaway trends) | MEDIUM | ✅ FIXED |

**V2.3.16 Key Changes:**

**Fix 1: DTE-Based Delta Validation**
- Added `OPTIONS_SWING_DTE_THRESHOLD = 5` - DTE > 5 uses swing bounds
- Added `OPTIONS_SWING_DELTA_MIN = 0.55`, `OPTIONS_SWING_DELTA_MAX = 0.85`
- Added `OPTIONS_INTRADAY_DELTA_MIN = 0.40`, `OPTIONS_INTRADAY_DELTA_MAX = 0.60`
- Updated `check_entry_signal()` to use DTE-based validation

**Fix 2: Direction Conflict Resolution (Centralized in Options Engine)**
- Added `DIRECTION_CONFLICT_BULLISH_THRESHOLD = 65`
- Added `DIRECTION_CONFLICT_BEARISH_THRESHOLD = 40`
- Updated `get_intraday_direction()` to accept `regime_score` parameter
- Conflict check now inside options_engine (single source of truth)
- Skip FADE PUT when regime > 65 (strong bullish)
- Skip FADE CALL when regime < 40 (strong bearish)

**Fix 3: FADE Sniper Window (Max Cap)**
- Added `INTRADAY_FADE_MAX_MOVE = 1.20` - Don't fade runaway trends/crashes
- Renamed `INTRADAY_DEBIT_FADE_MIN_MOVE` → `INTRADAY_FADE_MIN_MOVE` (0.50%)
- FADE now requires: 0.50% <= |QQQ move| <= 1.20%

**Root Cause (Delta 0.70):** V2.3.14 swing fallback calls `_select_swing_option_contract()` which targets 0.70 delta, but `check_entry_signal()` validates against `OPTIONS_DELTA_MAX = 0.60`. All swing fallback trades were rejected.

**Root Cause (Direction Conflict):** Intraday FADE recommends PUT when QQQ is up (mean reversion). But if regime is 70+ (strong bull), fading the rally goes against the macro trend. V2.3.15 backtest lost $469 fading a bullish day with PUTs.

**Root Cause (Runaway Trend):** Without max cap, FADE would fire on 2%+ moves that are unlikely to mean-revert. Adding 1.20% cap prevents fading runaway trends/crashes.

**Status:** V2.3.16 DTE-BASED DELTA + DIRECTION CONFLICT fixes complete - Ready for backtest validation

**V2.3.17 FIX: Hybrid Yield Sleeve + Kill Switch 5% (2026-02-01):**

| # | Finding | Severity | Status |
|:-:|---------|:--------:|:------:|
| 1 | **Kill switch too sensitive** - 3% triggers on normal volatility | HIGH | ✅ FIXED (→5%) |
| 2 | **SHV churn from small trades** - Sniper (5%) trades liquidate SHV | MEDIUM | ✅ FIXED (10% cash buffer) |
| 3 | **RATES exposure limit blocking SHV** - 40% cap prevents post-kill-switch SHV | MEDIUM | ✅ FIXED (→99%) |
| 4 | **YIELD allocation limit low** - 50% cap when all cash should go to SHV | LOW | ✅ FIXED (→99%) |

**V2.3.17 Key Changes:**

**Kill Switch Raised to 5%:**
- `KILL_SWITCH_PCT`: 0.03 → 0.05 (5% daily loss triggers liquidation)
- `KILL_SWITCH_PCT_BY_PHASE`: Both SEED and GROWTH now 0.05
- Rationale: 3% triggered too frequently in volatile markets, 5% reduces false triggers to 2-4/year

**Hybrid Yield Sleeve Implementation:**
- Added `CASH_BUFFER_PCT = 0.10` (10% cash buffer)
- Modified `calculate_unallocated_cash()` to subtract cash buffer
- Buffer absorbs small Sniper (5%) and MR (5-10%) trades without touching SHV
- Reduces SHV churn by ~80% for typical intraday trades

**Exposure Limits Adjusted:**
- `RATES` group: 0.40 → 0.99 (allows near-full SHV post-kill-switch)
- `YIELD` source: 0.50 → 0.99 (allows SHV to absorb idle cash)

**Root Cause (Kill Switch):** At 3%, kill switch was estimated to trigger 4-8 times/year in normal markets. Raising to 5% reduces triggers while still protecting against major losses.

**Root Cause (SHV Churn):** Without cash buffer, every 5% Sniper trade required SHV liquidation (spread cost ~$1.82) then SHV buyback at EOD. With 10% buffer, small trades use cash directly.

**Root Cause (RATES Limit):** After kill switch fires and liquidates all positions, YieldSleeve wanted to put 99% in SHV, but RATES exposure group capped at 40%. Cash sat idle earning 0% instead of 5% in SHV.

**Status:** V2.3.17 HYBRID YIELD SLEEVE + KILL SWITCH 5% complete - Ready for backtest validation

**V2.3.18 FIX: Gamma Trap + Swing DTE Alignment (2026-02-01):**

| # | Finding | Severity | Status |
|:-:|---------|:--------:|:------:|
| 1 | **Single-leg exits after spreads** - 2 DTE vs spreads at 5 DTE | HIGH | ✅ FIXED (→4 DTE) |
| 2 | **Swing DTE min too low** - Entry at DTE=5, exit at DTE=4 = 1-day hold | HIGH | ✅ FIXED (→6 DTE) |

**V2.3.18 Key Changes:**

**Gamma Trap Fix:**
- `OPTIONS_SINGLE_LEG_DTE_EXIT`: 2 → 4 DTE
- Single legs now exit BEFORE spreads (4 DTE vs 5 DTE)
- Avoids gamma explosion in final week of expiration

**Swing DTE Alignment Fix:**
- `OPTIONS_SWING_DTE_MIN`: 5 → 6 DTE
- Ensures minimum 2-day holding period (enter at 6, exit at 4)
- Aligns with `OPTIONS_SWING_DTE_THRESHOLD=5` (DTE > 5 uses swing delta bounds)

**DTE Configuration (Final):**
| Mode | Entry DTE | Exit DTE | Min Hold |
|------|:---------:|:--------:|:--------:|
| Spreads | 10-21 | 5 | 5+ days |
| Single-Leg Swing | **6-45** | **4** | **2+ days** |
| Intraday | 0-1 | 3:30 PM | Same day |

**Root Cause (Gamma Trap):** Gamma risk explodes in the last week before expiration. A small move against you can wipe 50%+ of option value in hours.

**Root Cause (1-Day Hold):** With entry at DTE=5 and exit at DTE=4, swing trades could have only 1-day holding period - not a true swing trade.

**Status:** V2.3.18 GAMMA TRAP + SWING DTE complete - Ready for backtest validation

**V2.3.19 FIX: ITM_MOMENTUM Time Window Config (2026-02-01):**

| # | Finding | Severity | Status |
|:-:|---------|:--------:|:------:|
| 1 | **ITM_MOMENTUM time window hardcoded** - Not configurable like DEBIT_FADE | LOW | ✅ FIXED |

**V2.3.19 Key Changes:**

**Config Values Added:**
- `INTRADAY_ITM_START = "10:00"` - ITM Momentum entry window start
- `INTRADAY_ITM_END = "13:30"` - ITM Momentum entry window end

**Code Updated:**
- `check_intraday_entry_signal()` now parses time windows from config
- Both DEBIT_FADE and ITM_MOMENTUM use config values consistently

**Intraday Time Windows (Final):**
| Strategy | Start | End | Config |
|----------|:-----:|:---:|--------|
| DEBIT_FADE | 10:30 | 14:00 | `INTRADAY_DEBIT_FADE_START/END` |
| ITM_MOMENTUM | 10:00 | 13:30 | `INTRADAY_ITM_START/END` |
| CREDIT_SPREAD | 10:00 | 14:30 | `INTRADAY_CREDIT_START/END` |

**Status:** V2.3.19 TIME WINDOW CONFIG complete - Ready for backtest validation

---

**V2.4.0 Planned: Bidirectional Mean Reversion (PART 12)**
| # | Feature | Severity | Status |
|:-:|---------|:--------:|:------:|
| 1 | **Add SQQQ/SOXS inverse ETFs** - Capture "rally fade" when RSI > 75 | ENHANCEMENT | 🟡 AFTER V2.3.9 |
| 2 | **Mutual exclusivity** - Block TQQQ entry if SQQQ held (and vice versa) | HIGH | 🟡 AFTER V2.3.9 |
| 3 | **MR allocation cap** - Ensure Long + Short ≤ 10% total | HIGH | 🟡 AFTER V2.3.9 |

**V2.4.0 Scope:**
- Add MR_SHORT_SYMBOLS = ["SQQQ", "SOXS"] to config.py
- Add MR_RALLY_THRESHOLD = 0.025, MR_RSI_OVERBOUGHT = 75
- Subscribe to SQQQ, SOXS in main.py
- Update mean_reversion_engine.py with bidirectional logic
- Enforce mutual exclusivity (no simultaneous long + short)

**Rationale for Deferral:** V2.3.9 fixed the critical margin issue. Need to isolate performance impact before adding a new strategy direction. Bidirectional MR is a strategy enhancement, not a bug fix.

### Stage 2 Bugs - Prioritized Fix List

#### 🔴 CRITICAL - V2.3.2 Architect Audit Fixes ✅

| # | Bug | Status | Description |
|:-:|-----|:------:|-------------|
| 1 | **OPT_INTRADAY source unmapped** | ✅ FIXED | Added to SOURCE_ALLOCATION_LIMITS (5% max) |
| 2 | **requested_quantity ignored in scaling** | ✅ FIXED | Preserved in `_apply_source_limits()` |
| 3 | **RegimeState.score attribute error** | ✅ FIXED | Changed to `smoothed_score` |
| 4 | **Intraday position tracked wrong** | ✅ FIXED | Added `_pending_intraday_entry` flag, registers to `_intraday_position` |
| 5 | **15:30 force exit broken** | ✅ FIXED | Now checks `_intraday_position` correctly |
| 6 | **Intraday DTE too restrictive** | ✅ FIXED | Expanded from 0-2 to 0-5 DTE for backtest data |

#### 🔴 CRITICAL - Phase A Complete ✅

| # | Bug | Status | Description |
|:-:|-----|:------:|-------------|
| 1 | **Options sizing uses full portfolio** | ✅ FIXED | Added `requested_quantity` to TargetWeight, router uses it for options |
| 2 | **`_pending_num_contracts` ignored** | ✅ FIXED | Now passed via `requested_quantity` field in TargetWeight |
| 3 | **Insufficient margin for options** | ✅ FIXED | Margin check improved for all options (not just QQQ) |

#### 🟠 HIGH - Phase B Complete ✅

| # | Bug | Status | Description |
|:-:|-----|:------:|-------------|
| 4 | **Naked options vs Debit Spreads** | ✅ FIXED | V2.3 Debit Spreads implemented (Bull Call/Bear Put based on regime) |
| 5 | **Intraday mode strategy mismatch** | ✅ FIXED | Intraday=single-leg (0-5 DTE), Swing=debit spreads (10-21 DTE) |

#### 🔴 CRITICAL - V2.3.3 Part 3 Fixes ✅ COMPLETE

| # | Bug | Status | Description |
|:-:|-----|:------:|-------------|
| 1 | **Trend Allocation Flattening** | ✅ FIXED | TrendEngine now uses `config.TREND_SYMBOL_ALLOCATIONS.get(symbol)` |
| 2 | **Closing Trade Bypass** | ✅ FIXED | MIN_TRADE_VALUE bypassed for `target_weight=0.0` closes |
| 3 | **Exit Race Condition** | ✅ FIXED | `_pending_intraday_exit` flag prevents duplicate signals |

#### 🟡 MEDIUM - After Architecture Stable

| # | Bug | Status | Description |
|:-:|-----|:------:|-------------|
| 6 | Greeks monitoring checks single leg | ⏳ LATER | L5 circuit breaker sees unhedged delta/theta - may trigger too often. |
| 7 | VIX direction uses open gap | ⏳ LATER | Uses `vix_current - vix_open`. Design implies intraday trend. |
| 8 | Option chain validation race | ⏳ LATER | Empty chain during warm-up silently skips entries. |

#### ✅ Previously Fixed

| Bug | Severity | Description |
|-----|:--------:|-------------|
| Kill switch never resets daily | 🔴 CRITICAL | Added `_kill_switch_handled_today` flag |
| Scheduler kill switch not reset | 🔴 CRITICAL | Added `scheduler.reset_daily()` at 09:25 |
| Kill switch doesn't liquidate options | 🔴 CRITICAL | Added options liquidation in `_handle_kill_switch` |
| Theta threshold too tight | 🟠 HIGH | Added `CB_THETA_SWING_CHECK_ENABLED=False` |
| Kill switch log spam | 🟡 MEDIUM | Handler now only runs once per day |
| Options 10:00 AM exact entry | 🟡 MEDIUM | Changed to 10:30 entry window start |
| TNA/FAS stops never trigger | 🔴 CRITICAL | `_on_fill()` registration fixed |
| Swing direction hardcoded CALL | 🟠 HIGH | Direction now uses regime score |
| VIX missing from regime score | 🟠 HIGH | V2.3: Added VIX as 20% weight |
| 4-Strategy complexity | 🟠 HIGH | V2.3: Debit Spreads only |
| 300+ Invalid orders per day | 🔴 CRITICAL | Added `_entry_attempted_today` flag |
| Log spam after 14:30 | 🟡 MEDIUM | Time window warning logged once |
| Kill switch not blocking options | 🔴 CRITICAL | Check `_kill_switch_handled_today` in scan |
| Wrong delta selection (ATM) | 🟠 HIGH | Swing=0.70δ, Intraday=0.30δ |

### Architect Audit Summary (2026-01-30)

**Document:** `docs/audits/stage2-codeaudit.md`

| Finding | Assessment | Action |
|---------|:----------:|--------|
| "Naked Options" vs "Debit Spreads" | ✅ Correct | Architecture decision needed |
| "Sizing Disconnect" (`_pending_num_contracts` ignored) | ✅ Correct | Fix in Phase A |
| "Intraday Mode Mismatch" | ⚠️ Partial | Verify V2.3 intraday design |
| Greeks Monitoring Failure | ✅ Correct | Lower priority than sizing |
| Option Chain Validation | ✅ Correct | Minor issue |
| VIX Direction Logic | ⚠️ Minor | Functional but not optimal |

### Recommended Fix Order

**Phase A: Make Backtest Runnable (Issues 1-3)**
1. Fix `target_weight` calculation - pass calculated `num_contracts` to router
2. Add `requested_quantity` field to TargetWeight - router uses it if present
3. Add margin check before options orders - skip if insufficient

**Phase B: Architecture Decision (Issues 4-5) ✅ COMPLETE**
- ~~Option A: Keep single-leg, fix sizing → Quick validation~~
- **Option B: Implement V2.3 debit spreads → Full design compliance** ✅ SELECTED

V2.3 Debit Spreads Implementation (2026-01-31):
- `SpreadPosition` dataclass for two-leg position tracking
- `select_spread_legs()` - ATM long leg + OTM short leg selection
- `check_spread_entry_signal()` - Regime-based direction (>60 Bull Call, <45 Bear Put)
- `check_spread_exit_signals()` - 50% profit target, 5 DTE exit, regime reversal
- `PortfolioRouter` spread metadata handling - creates two orders for spread legs
- `main.py` integration - spread entry/exit monitoring

**Phase C: Polish (Issues 6-8)**
After architecture is stable.

### V2.3 Regime + Options Simplification (2026-01-30)

| Component | Change | Status |
|-----------|--------|:------:|
| Regime Engine | Added VIX Level as 5th factor (20% weight) | ✅ Complete |
| Regime Weights | Rebalanced: Trend 30%, VIX 20%, RV 15%, Breadth 20%, Credit 15% | ✅ Complete |
| Options Engine | Simplified from 4 strategies to Debit Spreads only | ✅ Complete |
| Direction Logic | Regime-based: Score ≥50 = CALL, <50 = PUT | ✅ Complete |
| VIX Thresholds | Low <15, Normal <22, High <30, Extreme <40 | ✅ Complete |

**V2.3 Design Rationale:**
- VIX (implied vol) directly impacts option pricing - more relevant than realized vol alone
- 4-strategy portfolio (Debit/Credit spreads, ITM Long, Protective Puts) was overcomplex
- Credit spreads conflict with mean-reversion on trending QQQ
- Debit spreads: defined risk, no stop-loss needed, survive whipsaw
- Regime score naturally encapsulates market conditions for direction

### Ready to Start (Remaining from Roadmap)

| Ticket | Component | Size | Spec | Priority |
|--------|-----------|:----:|------|----------|
| BT-2 | Stage 2 Backtest (30 days) | S | backtest-results.md | **HIGH** |
| BT-3 | Stage 3 Backtest (3 months) | S | backtest-results.md | **HIGH** |
| BT-4 | Stage 4 Backtest (1 year) | M | backtest-results.md | **HIGH** |
| BT-5 | Stage 5 Backtest (5 years + crisis) | L | backtest-results.md | **HIGH** |
| OPT-6 | Options-specific daily loss limit | S | — | Medium |
| OPT-7 | Separate kill logic for intraday vs swing options | M | — | Medium |
| YLD-1 | SHV Ladder Strategy | M | V2-1-Critical-Fixes-Guide.md | Medium |
| YLD-2 | Daily Cash Sweep | S | V2-1-Critical-Fixes-Guide.md | Medium |
| YLD-3 | Monthly Interest Harvest | S | V2-1-Critical-Fixes-Guide.md | Low |

### In Progress

| Component | Owner | Branch | Started | Spec |
|-----------|-------|--------|---------|------|
| QC Cloud Backtest Validation | VA | testing/va/stage1-1day-backtest | 2026-01-30 | docs/audits/backtest-results.md |

### Done (V2 Phase 1)

| Ticket | Component | Owner | Commit | Merged |
|--------|-----------|-------|--------|--------|
| TRE-1 | MA200 + ADX Signal | VA | develop | 2026-01-26 |
| TRE-2 | Trailing Stop Enhancement | VA | develop | 2026-01-26 |
| TRE-3 | Trend Engine V2 Tests | VA | develop | 2026-01-26 |
| RSK-1 | 5-Level Circuit Breaker | VA | develop | 2026-01-26 |
| RSK-3 | Risk Engine V2 Tests | VA | develop | 2026-01-26 |

### Done (V2 Phase 2)

| Ticket | Component | Owner | Commit | Merged |
|--------|-----------|-------|--------|--------|
| MRE-3 | VIX Data Feed | VA | develop | 2026-01-26 |
| MRE-1 | VIX Regime Integration | VA | develop | 2026-01-26 |
| MRE-2 | Regime-Adjusted Parameters | VA | develop | 2026-01-26 |
| MRE-4 | Mean Reversion V2 Tests | VA | develop | 2026-01-26 |

### Done (V2 Phase 3 - Options Engine)

| Ticket | Component | Owner | Commit | Merged |
|--------|-----------|-------|--------|--------|
| OPT-1 | Options Engine Core (4-factor entry scoring) | VA | develop | 2026-01-26 |
| OPT-2 | Entry Score Model (ADX, Momentum, IV, Liquidity) | VA | develop | 2026-01-26 |
| OPT-3 | OCO Order Manager (`execution/oco_manager.py`) | VA | develop | 2026-01-26 |
| OPT-4 | Options Engine Tests (comprehensive suite) | VA | develop | 2026-01-26 |
| RSK-2 | Greeks Monitoring Integration | VA | develop | 2026-01-26 |
| OPT-5 | Options Wiring Audit (DTE fix, intraday scan, Greeks monitor) | VA | v2.1.2 | 2026-01-27 |

### Done (V2 Phase 4 - Integration & Orchestration)

| Ticket | Component | Owner | Commit | Merged |
|--------|-----------|-------|--------|--------|
| INT-1 | VIX Data Feed wired to main.py | VA | 60ebf55 | 2026-01-26 |
| INT-2 | Options Engine wired to main.py | VA | 60ebf55 | 2026-01-26 |
| INT-3 | OCO Manager wired to main.py | VA | 60ebf55 | 2026-01-26 |
| ORC-1 | Signal Aggregation (70/20-30/0-10 Core-Satellite) | VA | 60ebf55 | 2026-01-26 |
| ORC-2 | Rebalancing Logic (drift > 5%) | VA | 60ebf55 | 2026-01-26 |
| TST-1 | V2 Test Plan + Integration Tests (63 new tests) | VA | 381ac7c | 2026-01-26 |
| TST-2 | Scenario Tests Implementation (25 tests with correct APIs) | VA | v2.1.3 | 2026-01-27 |
| DOC-1 | Architecture Diagrams Update (6 files, Core-Satellite) | VA | 242376f | 2026-01-27 |

### In Review

| Component | Owner | PR | Reviewer |
|-----------|-------|---:|----------|
| _None_ | | | |

### Ready to Start

| Component | Assigned | Size | Spec |
|-----------|----------|:----:|------|
| _Phase 6 Complete - See Next Steps above_ | | | |

### Done (Phase 6)

| Component | Owner | Commit | Merged |
|-----------|-------|--------|--------|
| main.py | VA | 7c0baf0 | 2026-01-25 |
| docs/MAIN_PY_IMPLEMENTATION.md | VA | 7c0baf0 | 2026-01-25 |

### Done (Phase 5)

| Component | Owner | PR | Merged |
|-----------|-------|---:|--------|
| execution/execution_engine.py | VA | #40 | 2026-01-26 |
| persistence/state_manager.py | VA | #40 | 2026-01-26 |
| scheduling/daily_scheduler.py | VA | #40 | 2026-01-26 |

### Done (Phase 4)

| Component | Owner | PR | Merged |
|-----------|-------|---:|--------|
| portfolio/exposure_groups.py | VA | #36 | 2026-01-25 |
| portfolio/portfolio_router.py | VA | #37 | 2026-01-25 |
| engines/risk_engine.py | VA | #38 | 2026-01-25 |

### Done (Phase 3)

| Component | Owner | PR | Merged |
|-----------|-------|---:|--------|
| engines/cold_start_engine.py | VA | #26 | 2026-01-25 |
| engines/trend_engine.py | VA | #27 | 2026-01-25 |
| engines/mean_reversion_engine.py | VA | #28 | 2026-01-25 |
| TYPE_CHECKING guards (all engines) | VA | #29 | 2026-01-25 |
| docs: requirements-dev.txt | VA | #30 | 2026-01-25 |
| engines/hedge_engine.py | VA | #32 | 2026-01-25 |
| engines/yield_sleeve.py | VA | #33 | 2026-01-25 |

### Done (Phase 1)

| Component | Owner | PR | Merged |
|-----------|-------|---:|--------|
| config.py | VA | #18 | 2026-01-25 |
| models/enums.py | VA | — | 2026-01-25 |
| models/target_weight.py | VA | — | 2026-01-25 |
| utils/calculations.py | VA | #22 | 2026-01-25 |

---

## Phase 2 - Core Engines (Complete ✓)

### Done (Phase 2)

| Component | Owner | PR | Merged |
|-----------|-------|---:|--------|
| engines/regime_engine.py | VA | #24 | 2026-01-25 |
| engines/capital_engine.py | VA | #25 | 2026-01-25 |

### Deferred

| Component | Assigned | Size | Spec | Notes |
|-----------|----------|:----:|------|-------|
| CI: Enforce coverage threshold (70%) | -- | S | — | Deferred to Phase 4 |

---

## Phase 3 - Strategy Engines (Complete ✓)

> See "Done (Phase 3)" in Current Sprint section.

---

## Phase 4 - Coordination (Complete ✓)

> See "Done (Phase 4)" in Current Sprint section.

---

## Phase 5 - Execution & State (Complete ✓)

> See "Done (Phase 5)" in Current Sprint section.

---

## Phase 6 - Integration (Complete ✓)

> See "Done (Phase 6)" in Current Sprint section.
>
> **Implementation Summary:**
> - `main.py` - QCAlgorithm entry point (1,638 lines)
> - Hub-and-Spoke architecture with PortfolioRouter as central hub
> - All engines, infrastructure, and scheduled events wired together
> - Full documentation in `docs/MAIN_PY_IMPLEMENTATION.md`

---

## Feature Branches (Not Merged)

> **Purpose:** Track self-contained features developed on separate branches, intentionally kept out of `develop` to avoid complexity in the core trading logic.

### `feat/backtest-reporting` — Backtest Analysis Module

**Branch:** `feat/backtest-reporting` (pushed to origin)

**Why Separate?** Reporting/monitoring adds complexity that's not needed for core trading logic. Keeping it isolated allows backtesting the trading system without the overhead, and merging later when ready for production monitoring.

**Contents (7 files, 1,974 lines, 20 tests):**

| File | Purpose |
|------|---------|
| `reporting/__init__.py` | Module exports |
| `reporting/trade_record.py` | `TradeRecord`, `DailyEquity` dataclasses |
| `reporting/performance_metrics.py` | `PerformanceMetrics` with 30+ fields |
| `reporting/metrics_engine.py` | Core engine: Sharpe, Sortino, drawdown, win rate |
| `reporting/chart_manager.py` | QC charting: equity curve, drawdown, regime |
| `reporting/csv_exporter.py` | Trade history export to CSV |
| `tests/test_metrics_engine.py` | 20 unit tests for all components |

**Capabilities:**
- Sharpe/Sortino ratio calculation (annualized)
- Continuous drawdown tracking (max, current, average)
- Win rate, profit factor, expectancy
- Trade entry/exit recording with P&L
- Daily equity snapshots
- CSV export for external analysis (Excel, etc.)
- State persistence via ObjectStore
- QC RuntimeStatistics panel integration

**To Use:**
```bash
git checkout feat/backtest-reporting   # Get the module
git checkout develop                    # Return to clean trading logic
```

**To Merge (when ready):**
```bash
git checkout develop
git merge feat/backtest-reporting
# Then integrate into main.py per plan in .claude/plans/
```

**Created:** 2026-01-27 | **Tests:** 20 passed | **Status:** Complete, awaiting integration decision

---

## Ideas Backlog

> **Purpose:** Capture ideas, enhancements, and "nice-to-haves" that are out of scope for current phases but worth remembering.
>
> **Rules:**
> - Add ideas anytime - don't let them get lost
> - Categorize by type (Enhancement, Research, Tooling, etc.)
> - Move to a Phase when prioritized for implementation
> - Delete if no longer relevant

### Future Enhancements

| Idea | Category | Notes | Added |
|------|----------|-------|-------|
| Intraday options trading (QQQ calls/puts) | Options | Core logic: QQQ averages 1% daily move. Enter at top/bottom. Requires Phase 5+ complete. | 2026-01-25 |
| Report generation and monitoring | Operations | Daily/weekly performance reports, alerts, dashboards. Essential for live trading oversight. | 2026-01-25 |
| Web UI for system management | Operations | Dashboard to view positions, regime state, trigger manual overrides. Consider after v1.0 stable. | 2026-01-25 |

### Research / Exploration

| Idea | Category | Notes | Added |
|------|----------|-------|-------|
| Evaluate adding short positions | Strategy | Currently using PSQ (inverse ETF) as hedge. Research if direct shorting improves returns in sustained downtrends. | 2026-01-25 |
| Comprehensive options strategy matrix | Options | Multiple strategies (spreads, straddles, etc.) based on conditions. Needs separate architecture. Treat as separate project sharing regime engine. | 2026-01-25 |
| Crypto trading with same logic | New Market | Regime/MR/trend logic portable. Challenges: 24/7 market, different infra, higher volatility. Fork-and-modify approach. | 2026-01-25 |

### Technical Debt / Improvements

| Idea | Category | Notes | Added |
|------|----------|-------|-------|
| CI: Enforce coverage threshold (70%) | Tooling | Deferred from Phase 2 | 2026-01-24 |

---

## Next Steps - Production Readiness

> **Current State:** System is feature-complete. QC Cloud backtest validation in progress.
>
> **Stage 1 Backtest:** PASS ✅ (2026-01-30)

### Staged Backtest Validation (Current Focus)

| Stage | Duration | Status | Next Action |
|:-----:|----------|:------:|-------------|
| 1 | 1 day | ✅ PASS | Complete |
| 2 | 30 days | 🟡 LOGIC OK | Analyze logs for performance tuning |
| 3 | 3 months | ⏳ | Run after Stage 2 analysis |
| 4 | 1 year | — | After Stage 3 |
| 5 | 5 years + crisis | — | After Stage 4 |

### After Staged Backtests

1. **Paper Trading** (2 weeks minimum)
   - Deploy to QC paper environment
   - Monitor daily operations
   - Validate state persistence across restarts

2. **Production Deployment**
   - Small deployment ($10-20K) first
   - Full deployment ($50K+) after validation

### Before Live Trading

| Task | Status | Notes |
|------|--------|-------|
| Stage 1 backtest (1 day) | ✅ | PASS - 2026-01-30 |
| Stage 2 backtest (30 days) | 🟡 | Logic OK (-9.67%, 8 orders) - needs perf tuning |
| Stage 3 backtest (3 months) | ⏳ | Required |
| Stage 4 backtest (1 year) | ⏳ | Required |
| Stage 5 backtest (5 years + crisis) | ⏳ | Required |
| Paper trading (2 weeks) | ⏳ | Required |
| Monitoring/alerting setup | ⏳ | Recommended |
| Small deployment ($10-20K) | ⏳ | First live phase |
| Full deployment ($50K+) | ⏳ | After small size proves out |

---

## Archive

<details>
<summary>Phase 0 - Pre-Development & Foundation (Complete)</summary>

### Documentation & Setup (2026-01-24)

| Task | Owner | Completed |
|------|-------|-----------|
| Session management setup | VA | 2026-01-24 |
| developer-guide-claude.md rewrite | VA | 2026-01-24 |
| Git workflow established | VA | 2026-01-24 |
| WORKBOARD.md created | VA | 2026-01-24 |

### CI/CD & Infrastructure (2026-01-25)

| Task | Owner | Completed |
|------|-------|-----------|
| GitHub Actions CI workflow (`.github/workflows/test.yml`) | VA | 2026-01-25 |
| Architecture boundary tests (`tests/test_architecture_boundaries.py`) | VA | 2026-01-25 |
| QC compliance tests (print, sleep, datetime.now) | VA | 2026-01-25 |
| Branch protection for `main` and `develop` | VA | 2026-01-25 |
| CONTRIBUTING.md created | VA | 2026-01-25 |
| PR template (`.github/PULL_REQUEST_TEMPLATE.md`) | VA | 2026-01-25 |
| Branch protection docs (`docs/GITHUB-BRANCH-PROTECTION.md`) | VA | 2026-01-25 |
| Lean CLI workspace integration | VA | 2026-01-25 |
| QC cloud backtest verification | VA | 2026-01-25 |
| CI violation detection verified | VA | 2026-01-25 |

### Infrastructure Hardening (2026-01-24)

| Task | Owner | Completed |
|------|-------|-----------|
| Fixed CI pipeline to fail correctly (no silent skips) | VA | 2026-01-24 |
| Created `tests/scenarios/__init__.py` | VA | 2026-01-24 |
| Added explicit `@pytest.mark.skip` to all placeholder tests | VA | 2026-01-24 |
| Created `pyproject.toml` (unified tool configuration) | VA | 2026-01-24 |
| Created `.pre-commit-config.yaml` (pre-commit hooks) | VA | 2026-01-24 |
| Created `Makefile` (workflow automation: make setup, make test, make branch) | VA | 2026-01-24 |
| Updated CONTRIBUTING.md with local dev setup | VA | 2026-01-24 |
| Added "Golden Rule" section to CONTRIBUTING.md (branch protection) | VA | 2026-01-24 |
| Added pre-commit hook to block commits to main/develop | VA | 2026-01-24 |
| Added test scaffolds for all Phase 2-5 components | VA | 2026-01-24 |
| Fixed Black formatting in models/enums.py and models/target_weight.py | VA | 2026-01-24 |

### Documentation Automation (2026-01-24)

| Task | Owner | Completed |
|------|-------|-----------|
| Created `docs/DOCUMENTATION-MAP.md` (code-to-doc mapping) | VA | 2026-01-24 |
| Added "Documentation Update Requirements" section to CLAUDE.md | VA | 2026-01-24 |
| Updated CLAUDE.md repository structure with new files | VA | 2026-01-24 |
| Updated PROJECT-STRUCTURE.md with new files and counts | VA | 2026-01-24 |
| Updated docs/00-table-of-contents.md with DOCUMENTATION-MAP.md | VA | 2026-01-24 |

### Developer Experience Improvements (2026-01-24)

| Task | Owner | Completed |
|------|-------|-----------|
| Created `.vscode/settings.json` (IDE configuration) | VA | 2026-01-24 |
| Created `.editorconfig` (cross-editor consistency) | VA | 2026-01-24 |
| Added `make verify` command (setup verification) | VA | 2026-01-24 |
| Added `make validate-config` command | VA | 2026-01-24 |
| Added `make phase1-check` command | VA | 2026-01-24 |
| Added test fixture documentation to CONTRIBUTING.md | VA | 2026-01-24 |
| Added skip marker explanation to CONTRIBUTING.md | VA | 2026-01-24 |
| Added pre-commit hook troubleshooting to CONTRIBUTING.md | VA | 2026-01-24 |

### Process Standards (2026-01-24)

| Task | Owner | Completed |
|------|-------|-----------|
| Added commit message standards to CONTRIBUTING.md | VA | 2026-01-24 |
| Added PR review guidelines to CONTRIBUTING.md | VA | 2026-01-24 |
| Added Definition of Done to WORKBOARD.md | VA | 2026-01-24 |
| Principal Architect review completed | VA | 2026-01-24 |

**Documentation Automation Process:**
- Claude consults `docs/DOCUMENTATION-MAP.md` after any code change
- Maps code files → documentation that needs updating
- Documentation updates included in same commit/PR as code
- No developer action required - Claude handles automatically

**CI Capabilities:**
- ✅ Catches engines placing orders (architecture violation)
- ✅ Catches `print()` statements (QC compliance)
- ✅ Catches `time.sleep()` calls (QC compliance)
- ✅ Catches `datetime.now()` usage (QC compliance)
- ✅ Blocks PR merge if tests fail
- ✅ Skipped tests allowed, but failures fail build
- ✅ Linting only runs on files with actual content

**Pre-commit Hooks:**
- ✅ Black (code formatting)
- ✅ isort (import sorting)
- ✅ No print() in source files
- ✅ No datetime.now() usage
- ✅ No time.sleep() usage
- ✅ Blocks commits to main/develop branches

**Workflow Automation (Makefile):**
- `make setup` - Create venv, install deps, install pre-commit
- `make test` - Run all tests
- `make lint` - Run black and isort
- `make branch name=feature/va/my-feature` - Create feature branch from develop

**Lean CLI Integration:**
- Workspace: `../lean-workspace/AlphaNextGen/`
- Verified: `lean cloud push` + `lean cloud backtest` working
- Test backtest: $50,000 → $50,147 (+0.29%)

</details>

---

## Definition of Done

A task is **not complete** until ALL of the following are true:

### Code Quality
- [ ] Code implements requirements from the spec document
- [ ] All unit tests pass (`pytest tests/test_<component>.py -v`)
- [ ] All architecture tests pass (`pytest tests/test_architecture_boundaries.py -v`)
- [ ] No linting errors (`make lint` or `black --check`)
- [ ] Type hints added for all function signatures
- [ ] Docstrings added for public functions

### Safety & Compliance
- [ ] No `print()` statements (use `self.algorithm.Log()`)
- [ ] No `datetime.now()` (use `self.algorithm.Time`)
- [ ] No `time.sleep()` (use scheduling)
- [ ] No hardcoded values (use `config.py`)
- [ ] Engines do NOT place orders (emit TargetWeight only)

### Documentation
- [ ] `docs/DOCUMENTATION-MAP.md` consulted
- [ ] All affected documentation updated
- [ ] WORKBOARD.md task moved to "Done" section

### Review
- [ ] PR created targeting `develop`
- [ ] CI passes (all green checks)
- [ ] Self-reviewed the diff
- [ ] Approval received (if targeting `main`)

---

## Interface Change Protocol

If you need to change a shared interface (TargetWeight, RegimeState, etc.):

1. Notify collaborator: "I need to change [interface]. Pause related work."
2. Make the change in a normal feature branch
3. Update all affected components in SAME branch
4. PR, review, merge
5. Notify: "[Interface] change merged. Resume work."

Expected frequency: 1-2 times total project lifetime.

---

## Quick Reference

**Sizes:** S = <100 lines | M = 100-300 lines | L = 300+ lines

**Workflow:**
```
1. Pick from "Ready to Start" -> Move to "In Progress"
2. Create branch: feature/<initials>/<component>
3. Code -> PR -> Move to "In Review"
4. After merge -> Move to "Done"
5. Delete feature branch (local + remote)
```

**Branch format:** `feature/va/config-py` or `feature/vd/regime-engine`

**New Developer Setup:**
```bash
git clone <repo> && cd alpha-nextgen
python3.11 -m venv venv && source venv/bin/activate
pip install -r requirements.lock
pre-commit install
pytest tests/test_smoke_integration.py -v
```

---

*Last Updated: 01 February 2026 (V2.3.19 ITM_MOMENTUM Time Window Config)*
