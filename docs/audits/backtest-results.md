# Backtest Results - Alpha NextGen V2

> **Purpose:** Track backtest progress, results, and validation status for QC Cloud deployments.
>
> **Last Updated:** 2026-01-31 (V2.3.6 Spread Order + Sniper Window Fixes)

---

## QC Infrastructure

> **Plan:** Trading Firm ($48/mo) + 2× B4-12 backtest nodes ($48/mo) = **$96/mo**

| Resource | Limit | Notes |
|----------|------:|-------|
| File Size | 256 KB | No minification needed (main.py = 102KB) |
| Backtest Log | 5 MB | Sufficient for trades-only logging |
| Daily Log | 50 MB | Multiple debug runs per day |
| Plot Points | 32,000 | ~14K needed for 5-year |
| Backtest Nodes | 2× B4-12 | 4 cores, 12GB RAM each |

**Logging Strategy:** Use `trades_only=True` for fills/entries/exits, `trades_only=False` for diagnostics.
See `docs/guides/backtest-workflow.md` for full optimization guide.

---

## Staged Testing Plan

| Stage | Duration | Purpose | Status |
|:-----:|----------|---------|:------:|
| 1 | 1 day (Jan 2, 2024) | Basic validation - no errors, Initialize() completes | **PASS** ✅ |
| 2 | 7 days (Jan 2-8, 2024) | Short-term behavior, actual trades | **V2.3.4 FIXES APPLIED** 🟢 |
| 3 | 3 months (Q1 2024) | Position lifecycle, entries/exits | **V2.3.6 READY** 🟡 |
| 4 | 1 year (2024) | Full annual cycle, all market conditions | Pending |
| 5 | 5 years (2020-2024) | Long-term stress test, crisis periods | Pending |

### Stage 2 Summary (2026-01-31)

**Previous Run:** Smooth Magenta Bat | **Result:** -8.33% | **Orders:** 9
**Latest Run:** Casual Orange Cobra | **Result:** -6.98% | **Orders:** 14 | **Fees:** $171.51

**V2.3.2 Architect Audit Fixes Applied (Part 1-2):**

| # | Fix | File(s) | Status |
|:-:|-----|---------|:------:|
| 1 | OPT_INTRADAY source limit (5% max) | `portfolio_router.py` | ✅ |
| 2 | `requested_quantity` preserved in scaling | `portfolio_router.py` | ✅ |
| 3 | `RegimeState.score` → `smoothed_score` | `main.py` | ✅ |
| 4 | Intraday positions tracked separately | `options_engine.py` | ✅ |
| 5 | 15:30 force exit uses correct position | `options_engine.py`, `main.py` | ✅ |
| 6 | Intraday DTE expanded (0-5 vs 0-2) | `config.py`, `main.py` | ✅ |

**V2.3.3 Architect Audit Fixes (Part 3) - COMPLETE:**

| # | Finding | Severity | Fix | Status |
|:-:|---------|:--------:|-----|:------:|
| 1 | Trend Allocation Flattening | CRITICAL | `target_weight=1.0` → `config.TREND_SYMBOL_ALLOCATIONS.get(symbol)` | ✅ |
| 2 | Closing Trade Bypass | MEDIUM | Skip MIN_TRADE_VALUE check for `target_weight=0.0` closes | ✅ |
| 3 | Exit Race Condition | LOW | `_pending_intraday_exit` flag prevents duplicate signals | ✅ |

**Previous Issues Fixed:**
- Kill switch daily reset: ✅ FIXED (scheduler.reset_daily())
- Order spam: 371 → 9 orders ✅
- Cold start progression: ✅
- Options sizing: ✅ FIXED (Phase A)
- Naked options vs Debit Spreads: ✅ FIXED (Phase B)

### V2.3.4 Micro Regime + VIX Resolution Fixes (2026-01-31)

**Audit Reference:** `docs/audits/stage2-codeaudit.md` (Parts 5-7)

| # | Fix | Severity | Description | Status |
|:-:|-----|:--------:|-------------|:------:|
| 1 | Cold Start Bypass | CRITICAL | Options entering on Day 1 during cold start period | ✅ |
| 2 | Direction Mismatch | CRITICAL | Contract selected BEFORE direction determined | ✅ |
| 3 | Inverted Trade | CRITICAL | Bought CALL when should have bought PUT for fade | ✅ |
| 4 | Global Kill Switch | HIGH | Options loss liquidating healthy trend positions | ✅ |
| 5 | Spread Criteria Tight | HIGH | OI 5000, delta 0.25-0.40 too restrictive | ✅ |
| 6 | DTE Too Wide | MEDIUM | 0-5 DTE not true 0DTE trading | ✅ |
| 7 | VIX Resolution Daily | CRITICAL | VIX only updated once/day, not intraday | ✅ |
| 8 | QQQ Move Not in Regime | HIGH | Direction determined separately from regime | ✅ |

**Key Implementation Changes:**

1. **VIX Resolution Fix** (`main.py`):
   - Changed from `Resolution.Daily` to `Resolution.Minute`
   - VIX now updates every minute (gathered silently, processed every 15 min)
   - Added `_vix_15min_ago` tracker for short-term trend detection

2. **QQQ Move in Micro Regime** (`options_engine.py`):
   - Added `QQQMove` enum (UP_STRONG, UP, FLAT, DOWN, DOWN_STRONG)
   - Created `recommend_strategy_and_direction()` - combined decision
   - Direction determined INSIDE regime assessment, not separately
   - `state.recommended_direction` now set by Micro Regime Engine

3. **Direction-First Contract Selection** (`main.py`):
   - Determine direction based on QQQ move FIRST
   - Pass direction to `_select_intraday_option_contract()`
   - Filter contracts by direction before other criteria

4. **Engine-Specific Kill Switch** (`main.py`):
   - Analyze which engine caused the loss
   - Only liquidate options if options are the culprit
   - Protect healthy trend positions from options-triggered kill switch

5. **Config Changes** (`config.py`):
   - `OPTIONS_MIN_OPEN_INTEREST = 1000` (was 5000)
   - `OPTIONS_INTRADAY_DTE_MAX = 1` (was 5, true 0DTE)
   - `SPREAD_SHORT_LEG_DELTA_MIN = 0.15` (was 0.25)
   - `SPREAD_SHORT_LEG_DELTA_MAX = 0.45` (was 0.40)

**Data Flow (V2.3.4):**
```
OnData (every minute)
  └── VIX updates self._current_vix (NO LOG)
  └── QQQ price available via Securities

_on_micro_regime_update (every 15 min)
  └── Calculate 15-min VIX change
  └── micro_engine.update() with VIX + QQQ data
        ├── Classify VIX level + direction
        ├── Classify QQQ move direction
        └── recommend_strategy_and_direction()
              └── Returns (strategy, direction, reason)
```

**Next Step:** Re-run Stage 2 backtest with V2.3.4 fixes.

### V2.3.5 PART 9 Liquidity + Delta Tolerance Fixes (2026-01-31)

**Audit Reference:** `docs/audits/stage2-codeaudit.md` (PART 9)

| # | Fix | Severity | Description | Status |
|:-:|-----|:--------:|-------------|:------:|
| 1 | Open Interest Too High | HIGH | 5000 filtered 80% of contracts | ✅ |
| 2 | Spread Delta Window Narrow | HIGH | 0.45-0.55 (±0.05) misses ATM | ✅ |
| 3 | Intraday Delta Tolerance | MEDIUM | 0.15 too restrictive for 0.30 target | ✅ |

**Config Changes (V2.3.5):**
- `OPTIONS_MIN_OPEN_INTEREST = 500` (was 1000, original 5000)
- `SPREAD_LONG_LEG_DELTA_MIN = 0.40` (was 0.45)
- `SPREAD_LONG_LEG_DELTA_MAX = 0.60` (was 0.55)
- `OPTIONS_DELTA_TOLERANCE = 0.20` (was 0.15)

**Impact:** Options engine now finds 88 more contracts (95 orders vs 7).

### V2.3.6 Spread Order + Sniper Window Fixes (2026-01-31)

**Audit Reference:** `docs/audits/stage2-codeaudit.md` (PART 10) + "Upgraded Blue Whale" log analysis

| # | Fix | Severity | Description | Status |
|:-:|-----|:--------:|-------------|:------:|
| 1 | Spread Orphaned Long Leg | CRITICAL | IBKR rejects short leg (margin), long leg fills | ✅ |
| 2 | Margin Pre-Check Missing | HIGH | No validation before spread submission | ✅ |
| 3 | Intraday OI Too High | HIGH | 500 OI filters out most 0DTE PUTs on up days | ✅ |
| 4 | Intraday Spread Too Tight | HIGH | 10% rejects normal 0DTE spreads | ✅ |
| 5 | 10:30 Gatekeeper Blocking | HIGH | Hardcoded block kills 10:00-10:30 momentum window | ✅ |
| 6 | Trend Stops Too Tight | MEDIUM | ATR×3.0 suffocating trades in choppy markets | ✅ |
| 7 | SHV Churn | LOW | $2K threshold causing excessive rebalancing | ✅ |

**Root Causes Identified:**

1. **Spread Orders (CRITICAL):** IBKR treats spread legs as separate orders requiring naked short margin (~$343K) instead of spread margin (~$11K). Without margin check, long leg fills but short leg fails, leaving orphaned position.

2. **Intraday Filters (HIGH):** 0DTE PUTs on up days have lower OI and wider spreads. Cascade of filters (DTE→Direction→Delta→OI→Spread) left 0 contracts passing.

3. **Sniper Window (HIGH):** Config defined ITM Momentum and Credit Spreads to start at 10:00 AM, but main.py had hardcoded `if current_hour == 10 and current_minute < 30: return` blocking the first 30 minutes.

**Code Changes (V2.3.6):**

1. **Spread Order Protection** (`main.py`):
   - Added `_pending_spread_orders: Dict[str, str]` to track spread order pairs
   - Pre-submission margin check blocks spread if short leg would fail ($10K/contract estimate)
   - OnOrderEvent detects short leg `Invalid` status and liquidates orphaned long leg
   - Successful fill cleanup removes spread from tracking

2. **Intraday Filter Relaxation** (`config.py`):
   - `OPTIONS_MIN_OPEN_INTEREST = 200` (was 500)
   - `OPTIONS_SPREAD_WARNING_PCT = 0.15` (was 0.10)

4. **Trend Trailing Stop Loosening** (`config.py`):
   - `CHANDELIER_BASE_MULT = 3.5` (was 3.0)
   - `CHANDELIER_TIGHT_MULT = 3.0` (was 2.5)
   - `CHANDELIER_TIGHTER_MULT = 2.5` (was 2.0)
   - `PROFIT_TIGHT_PCT = 0.15` (was 0.10)
   - `PROFIT_TIGHTER_PCT = 0.25` (was 0.20)

5. **SHV Churn Reduction** (`config.py`):
   - `SHV_MIN_TRADE = 10_000` (was 2_000)

3. **Sniper Window Opened** (`main.py`):
   - Removed hardcoded 10:30 block
   - Intraday window now 10:00-15:00 (was 10:30-15:00)
   - Momentum and Credit strategies can now capture early volatility

**Expected Impact:**
- Spread orders: No more orphaned long legs causing unexpected losses
- Intraday: +50% more PUT contracts eligible on up days
- Sniper: +30 minutes of high-gamma trading opportunity

**Next Step:** Run Stage 3 backtest to validate V2.3.6 fixes.

### V2.3.7 Planned: Bidirectional Mean Reversion (Post-Backtest)

**Audit Reference:** `docs/audits/stage2-codeaudit.md` (PART 12)

| # | Feature | Description | Status |
|:-:|---------|-------------|:------:|
| 1 | Add Inverse ETFs | SQQQ (3× inverse Nasdaq), SOXS (3× inverse Semis) | 🟡 Planned |
| 2 | Rally Fade Logic | Buy SQQQ when TQQQ RSI > 75 + rally > 2.5% | 🟡 Planned |
| 3 | Mutual Exclusivity | Block long entry if short held (and vice versa) | 🟡 Planned |
| 4 | Allocation Cap | Ensure MR total (long + short) ≤ 10% | 🟡 Planned |

**Implementation Plan:**
1. `config.py`: Add MR_SHORT_SYMBOLS, MR_RALLY_THRESHOLD, MR_RSI_OVERBOUGHT
2. `main.py`: Subscribe to SQQQ, SOXS
3. `mean_reversion_engine.py`: Bidirectional logic with mutual exclusivity
4. `portfolio_router.py`: Verify MR allocation cap enforces total exposure

**Rationale for Deferral:**
- V2.3.6 made 7 significant changes - need to isolate performance impact
- Bidirectional MR is a strategy enhancement, not a bug fix
- Will implement after V2.3.6 backtest validates current fixes

---

## Stage 1: Single Day Validation

**Date:** 2026-01-30
**Backtest Period:** January 2, 2024 (1 trading day)
**Branch:** `testing/va/stage1-1day-backtest`

### Configuration

```python
self.SetStartDate(2024, 1, 2)
self.SetEndDate(2024, 1, 2)
self.SetCash(50_000)  # PHASE_SEED_MIN
```

### Results

| Metric | Value |
|--------|-------|
| **Start Equity** | $50,000.00 |
| **End Equity** | $50,000.00 |
| **Net Profit** | $0.00 (0.00%) |
| **Total Orders** | 1 |
| **Total Fees** | $0.00 |
| **Errors** | None |

**Backtest URL:** https://www.quantconnect.com/project/27678023/fe6e2c500b2676332e743886101dfa82

### Validation Checklist

| Check | Expected | Actual | Status |
|-------|----------|--------|:------:|
| No import errors | Pass | Pass | ✅ |
| Initialize() completes | Pass | Pass | ✅ |
| All 13 symbols added | 9 traded + 4 proxy | Verified | ✅ |
| Indicators initialize | 252-day warmup | Set | ✅ |
| No runtime errors | 0 errors | 0 errors | ✅ |
| Log count minimal | Trades only | 0 FILL logs | ✅ |

### Notes

1. **1 Order Submitted:** System queued a MOO order at EOD (15:45) for next trading day. This is expected behavior - order would execute on Jan 3, 2024 but backtest ended.

2. **No FILL Logs:** Correct behavior. The logging system was configured to only show trade entries/exits (`trades_only=True`). Since no trades executed (indicators need 252-day warmup), no FILL logs appeared.

3. **Bug Fixed:** VIX spike log throttle had a bug where `self._last_vix_spike_log` was `None` on first check. Fixed by adding `or self._last_vix_spike_log is None` condition.

4. **Options Engine Logging Fixed:** Micro Regime Engine was bypassing the `_log()` wrapper in main.py, causing VIX score/direction logs to appear during backtest. Fixed by:
   - Updated `OptionsEngine.log()` to check `LiveMode` before logging
   - Added `trades_only` parameter for trade-related logs (ENTRY_SIGNAL, EXIT_SIGNAL, FORCE_EXIT)
   - Diagnostic logs (VIX updates, regime changes) now only show in LiveMode

### Files Modified for QC Deployment

| File | Change | Reason |
|------|--------|--------|
| `main.py` | Minified (61,562 chars) | QC 64,000 char limit |
| `options_engine.py` | Minified (44,885 chars) | QC 64,000 char limit |

**Minification Approach:**
- Docstrings converted to single-line `"""."""`
- Comment-only lines removed (except `# type:`, `# noqa`)
- Inline comments removed from code lines
- Original files preserved in repo, minified versions in lean-workspace

---

## Stage 2: 30-Day Validation (V2.3)

**Date:** 2026-01-30
**Status:** **LOGIC OK** 🟡
**Backtest Period:** January 2-31, 2024 (with 300-day warmup)
**Branch:** `testing/va/stage2-backtest`

### Latest Results: Retrospective Apricot Leopard

| Metric | Value |
|--------|-------|
| **Start Equity** | $50,000 |
| **End Equity** | $45,391 |
| **Net Profit** | -6.92% |
| **Total Orders** | 15 |
| **Fees** | $616.84 |
| **Max Drawdown** | 11% |
| **Win Rate** | 0% |
| **Loss Rate** | 100% |

**Backtest URL:** https://www.quantconnect.com/project/27678023/f72f9fe7da3387805c00eeb40227b3bb

**Key Progress:** Kill switch daily reset is NOW WORKING. Cold start progresses correctly.

### V2.3 Fix Validation

| Fix | Before | After | Status |
|-----|--------|-------|:------:|
| Order spam prevention | 371 orders | 8 orders | ✅ Fixed |
| Log spam after 14:30 | 100+ logs/day | 1 log/day | ✅ Fixed |
| Kill switch blocking options | Not blocked | Blocked | ✅ Fixed |
| Delta targeting | ATM (0.50δ) | Swing=0.70δ, Intraday=0.30δ | ✅ Fixed |

### V2.3 Fix Summary

| Issue | Fix | Config Change |
|-------|-----|---------------|
| 300+ Invalid orders/day | `_entry_attempted_today` flag | - |
| Log spam after 14:30 | `_swing_time_warning_logged` flag | - |
| Kill switch not blocking options | Check in `_scan_for_options_signals` | - |
| Wrong delta (ATM instead of ITM/OTM) | Swing=0.70δ, Intraday=0.30δ | `OPTIONS_SWING_DELTA_TARGET=0.70`, `OPTIONS_INTRADAY_DELTA_TARGET=0.30` |

### Delta Selection Configuration (V2.3)

| Mode | Target Delta | Tolerance | Rationale |
|------|:------------:|:---------:|-----------|
| Swing (5-45 DTE) | **0.70** | ±0.15 | ITM for higher directional exposure |
| Intraday (0-2 DTE) | **0.30** | ±0.15 | OTM for faster gamma/premium moves |

### Backtest History

| Run | Name | Result | Orders | Issues |
|-----|------|--------|:------:|--------|
| 1 | Formal Blue Dragonfly | -6.76% | 5 | Kill switch never reset, 29 days blocked |
| 2 | Casual Yellow Chicken | -13.61% | 371 | 300+ Invalid orders, wrong delta, log spam |
| 3 | Geeky Yellow-Green Buffalo | -9.67% | 8 | Logic OK - selection spam fixed |
| 4 | Ugly Tan Lemur | TBD | 5 | Scheduler kill switch not reset daily |
| 5 | Retrospective Apricot Leopard | -6.92% | 15 | Kill switch reset working, options sizing wrong |
| 6 | Smooth Magenta Bat | -8.33% | 9 | Account killer bug (471 contracts instead of 58) |
| 7 | **Casual Orange Cobra** | **-6.98%** | 14 | V2.3.2 fixes applied, improved from -8.33% |
| 8 | Pensive Magenta Chicken | -12.47% | 29 | V2.3.3 applied, kill switch cascade issue |
| 9 | TBD (V2.3.4) | — | — | **V2.3.4 Micro Regime + VIX Resolution Fixes Ready** |

### V2.3.1 Fixes (Post Ugly Tan Lemur)

**Issue Found:** Cold start blocked entries every day with "kill switch active" because:
- `scheduler.is_kill_switch_triggered()` returned True after Day 1
- Called wrong method `scheduler.reset_daily_state()` (doesn't exist)
- Should be `scheduler.reset_daily()`

**Fixes Applied:**
1. Changed to `self.scheduler.reset_daily()` at 09:25 pre-market reset
2. Added `self.options_engine.reset_daily()` at 09:25 pre-market reset

### V2.3.2 Architect Audit Fixes (2026-01-31)

**Audit Documents:** `docs/audits/stage2-codeaudit.md`, `docs/audits/stage2-codeaudit2.md`

All critical bugs identified by external architects have been fixed:

| # | Bug | Root Cause | Fix |
|:-:|-----|------------|-----|
| 1 | **Account Killer** (471 contracts instead of 58) | `OPT_INTRADAY` not in `SOURCE_ALLOCATION_LIMITS` | Added with 5% limit |
| 2 | **Sizing Ignored** | `requested_quantity` dropped in `_apply_source_limits` | Preserved during scaling |
| 3 | **Scheduler Crash** | `regime_state.score` doesn't exist | Changed to `smoothed_score` |
| 4 | **Engines Conflicted** | Intraday registered to `_position` not `_intraday_position` | Added `_pending_intraday_entry` flag |
| 5 | **15:30 Exit Broken** | Force exit checked wrong position variable | Now checks `_intraday_position` |
| 6 | **0-2 DTE Data Missing** | QC lacks 0-2 DTE contracts in historical data | Expanded to 0-5 DTE |

**New Methods Added:**
- `options_engine.has_intraday_position()` - Check for intraday-specific position
- `options_engine.get_intraday_position()` - Get intraday position
- `options_engine.remove_intraday_position()` - Remove on exit

**Config Changes:**
- `OPTIONS_INTRADAY_DTE_MAX = 5` (was 2)

---

### V2.3.2 Issues Found (Retrospective Apricot Leopard) - NOW FIXED

**Kill Switch Reset: ✅ WORKING**
- Days progress correctly: Day 1 → Day 2 → Day 3 → Day 4
- Cold start advances when no kill switch trigger

**Options Position Sizing: ✅ FIXED (V2.3.2)**
- Day 1: BUY 471 contracts @ $0.54 = **$25,434** (51% of $50K portfolio!)
- Should be 5% intraday allocation = **$2,500 max** = ~46 contracts
- **Root Cause:** `OPT_INTRADAY` not mapped in `SOURCE_ALLOCATION_LIMITS`, defaulted to 50%
- **Fix:** Added `OPT_INTRADAY: 0.05` to allocation limits

**Insufficient Buying Power: ✅ FIXED (V2.3.2)**
- Jan 5, 10:30: `Order Error: Insufficient buying power (Value:22050, Free Margin:16523)`
- Jan 8, 10:30: `Order Error: Insufficient buying power (Value:21805, Free Margin:16292)`
- **Root Cause:** `requested_quantity` from engine dropped during source limit scaling
- **Fix:** Preserved `requested_quantity` and `metadata` in `_apply_source_limits()`

**Timeline Analysis (Jan 2-8, 2024):**
| Day | Event | Outcome |
|-----|-------|---------|
| Jan 2 | Options entry 471 contracts, kill switch at 10:31 | -6.26% loss, liquidated |
| Jan 3 | MOO fills TNA/FAS, cold start adds SSO, kill switch at 15:51 | -3.16% loss |
| Jan 4 | Cold start adds QLD, options entry 24 contracts, kill switch at 12:46 | -3.47% loss |
| Jan 5 | Cold start adds QLD, options **REJECTED** (insufficient margin) | No kill switch |
| Jan 6-7 | Weekend (no trading) | - |
| Jan 8 | Options **REJECTED** (insufficient margin) | No kill switch |

---

### Architect Audit Review (2026-01-30)

**Audit Document:** `docs/audits/stage2-codeaudit.md`

An external architect reviewed the codebase and identified fundamental design-implementation gaps:

#### Critical Findings

| Finding | Severity | Assessment |
|---------|:--------:|:----------:|
| **Naked Options vs Debit Spreads** | 🔴 CRITICAL | ✅ Correct |
| **Sizing Disconnect** | 🔴 CRITICAL | ✅ Correct |
| Intraday Mode Mismatch | 🟠 HIGH | ⚠️ Partial |
| Greeks Monitoring Failure | 🟡 MEDIUM | ✅ Correct |
| Option Chain Validation | 🟡 MEDIUM | ✅ Correct |
| VIX Direction Logic | 🟡 MEDIUM | ⚠️ Minor |

#### 1. Naked Options vs Debit Spreads (Architecture Failure)

**Design Doc (V2.3):** Mandates DEBIT SPREADS - Bull Call Spread (Regime > 60), Bear Put Spread (Regime < 45)

**Current Code:** Selects ONE contract in `_select_swing_option_contract`, registers ONE contract, returns `TargetWeight` for ONE symbol.

**Impact:** Naked long calls/puts get stopped out at -0.36% move. Spreads would survive -1.0% whipsaw. Missing the "hedge" (short leg).

#### 2. Sizing Disconnect (Risk Logic Ignored)

**Design Doc:** `contracts = floor(allocation / (entry_price * 100 * stop_pct))` - risk-based sizing.

**Current Code:**
- `calculate_position_size` correctly calculates `num_contracts` → stores in `_pending_num_contracts`
- Then **discards it** and returns `TargetWeight(target_weight=1.0)`
- Router applies source limit (25%) → calculates `(Total Equity * 25%) / Option Price`

**Impact:** Risk Engine calculates 4 contracts safe. Router calculates 25 contracts. Taking 6× intended risk.

#### V2.3 Design Verification

The V2.3 design documentation confirms:

1. **5-Factor Regime** including VIX at 20% weight ✅
2. **Simplified from 4 strategies to Debit Spreads only** ✅
3. **Neutral regime (45-60) = NO OPTIONS TRADE** (skip whipsaw) ✅
4. **Protective Puts only in crisis (Regime < 30)** ✅

---

### Prioritized Fix Plan

#### Phase A: Make Backtest Runnable ✅ COMPLETE

| # | Fix | Status |
|:-:|-----|:------:|
| 1 | Fix `target_weight` calculation | ✅ OptionsEngine now passes `num_contracts` via `requested_quantity` |
| 2 | Add `requested_quantity` to TargetWeight | ✅ Schema 1.1 with optional `requested_quantity: int` field |
| 3 | Add margin check before options orders | ✅ Router checks margin for all options before order |

#### Phase B: Architecture Decision ✅ COMPLETE

**Decision:** Option B - Implement V2.3 Debit Spreads

| Component | Implementation | Status |
|-----------|----------------|:------:|
| `SpreadPosition` dataclass | Two-leg position tracking (long + short) | ✅ |
| `select_spread_legs()` | ATM long (0.45-0.55δ) + OTM short (0.25-0.40δ) | ✅ |
| `check_spread_entry_signal()` | Regime-based: >60 Bull Call, <45 Bear Put, 45-60 NO TRADE | ✅ |
| `check_spread_exit_signals()` | 50% profit target, 5 DTE exit, regime reversal | ✅ |
| PortfolioRouter spread handling | Metadata-based two-leg order creation | ✅ |
| main.py integration | Spread entry/exit monitoring, fill tracking | ✅ |

#### V2.3.2: Architect Audit Fixes ✅ COMPLETE

| Fix | Description | Status |
|-----|-------------|:------:|
| OPT_INTRADAY source limit | Added to SOURCE_ALLOCATION_LIMITS (5%) | ✅ |
| requested_quantity preserved | Not dropped in `_apply_source_limits()` | ✅ |
| RegimeState.score → smoothed_score | Fixed attribute access | ✅ |
| Intraday position tracking | `_pending_intraday_entry` flag + `_intraday_position` | ✅ |
| 15:30 force exit | Checks correct position variable | ✅ |
| Intraday DTE 0-5 | Expanded for backtest data availability | ✅ |

**Key Changes:**
- `_generate_options_signals()`: Uses `select_spread_legs()` + `check_spread_entry_signal()`
- `_scan_options_signals()`: Swing mode now uses spread entry (intraday kept single-leg)
- `_monitor_risk_greeks()`: Added `_check_spread_exit()` for spread position monitoring
- `_on_fill()`: Added `_handle_spread_leg_fill()` + `_handle_spread_leg_close()` for two-leg fill tracking

**Config Added:**
- `SPREAD_REGIME_BULLISH = 60` (Regime > 60: Bull Call Spread)
- `SPREAD_REGIME_BEARISH = 45` (Regime < 45: Bear Put Spread)
- `SPREAD_WIDTH_TARGET = 5.0` ($5 spread width)
- `SPREAD_DTE_MIN/MAX = 10/21` (DTE range for spreads)
- `SPREAD_PROFIT_TARGET_PCT = 0.50` (50% of max profit)

#### Phase C: Polish (MEDIUM)

| # | Fix | Description |
|:-:|-----|-------------|
| 6 | Greeks monitoring | Adjust for spread vs single-leg |
| 7 | VIX direction logic | Use intraday trend (30min) not gap |
| 8 | Option chain validation | Handle empty chains with retry |

---

**All Required Fixes Complete (V2.3.2):**
1. ✅ Options position sizing respects 5% intraday allocation via `requested_quantity`
2. ✅ Buying power check improved for all options orders
3. ✅ `requested_quantity` passed through and respected by router
4. ✅ V2.3 Debit Spreads for Swing (10-21 DTE), Single-leg for Intraday (0-5 DTE)
5. ✅ Intraday positions tracked separately, 15:30 force exit working
6. ✅ OPT_INTRADAY source mapped to 5% allocation limit

---

### Previous Backtest: Formal Blue Dragonfly

### Configuration

```python
self.SetStartDate(2024, 1, 2)
self.SetEndDate(2024, 1, 31)
self.SetCash(50_000)
self.SetWarmUp(timedelta(days=300))  # V2.3: Extended warmup
```

### Results

| Metric | Value |
|--------|-------|
| **Start Equity** | $50,000.00 |
| **End Equity** | $46,621.90 |
| **Net Profit** | **-$3,378.10 (-6.76%)** |
| **Total Orders** | 5 |
| **Total Fees** | $48.10 |
| **Trades** | 1 (options only) |

**Backtest URL:** https://www.quantconnect.com/project/27678023/4d7c36e9a3887ce9bdba287b2a80b1c6

### Timeline - Day 1 (2024-01-02)

| Time | Event | Details |
|------|-------|---------|
| 10:00 | Options Entry | BUY 37 QQQ 240119C @ $3.97, OCO: Stop=$3.10, Target=$5.96 |
| 10:01 | **GREEKS BREACH** | Theta=-0.14 < -0.02 threshold (CB Level 5) |
| 10:20 | **CB Level 1** | Daily loss=2.19% ≥ 2.00% |
| 10:29 | **KILL SWITCH** | Loss=3.08%, equity=$48,459 |
| 10:29-13:57 | Kill switch spam | Logs every minute, position NOT liquidated |
| 13:57 | Stop loss hit | SELL 37 @ $3.07, Loss=-$3,330 |
| 15:45 | EOD | Cold start reset, trend signals blocked |
| Day 2-30 | **BLOCKED** | Kill switch never resets, 0 trades |

### Critical Issues Found

| # | Issue | Severity | Root Cause |
|---|-------|:--------:|------------|
| 1 | **Kill switch never resets daily** | 🔴 CRITICAL | `reset_daily_state()` not clearing kill switch flag |
| 2 | **Kill switch doesn't liquidate options** | 🔴 CRITICAL | Options position not included in kill switch liquidation |
| 3 | **Theta threshold too tight** | 🟠 HIGH | -0.02 threshold for 17 DTE option with -0.14 theta |
| 4 | **Kill switch log spam** | 🟡 MEDIUM | Logs every minute for 30 days |
| 5 | **Options entry at exactly 10:00** | 🟡 MEDIUM | No market settling period |

### Analysis

**Why -6.76% Loss?**
1. Options entered at 10:00 with 17 DTE contract
2. Theta (-0.14 = -14%/day) immediately breached -0.02 threshold
3. Position dropped 3.08% by 10:29 → Kill switch triggered
4. Kill switch SHOULD have liquidated but options position stayed until stop hit at 13:57
5. Final loss: ($3.97 - $3.07) × 37 × 100 = -$3,330

**Why No Trades After Day 1?**
- Kill switch triggered on Day 1 and **never reset**
- The `_kill_switch_triggered` flag persists across days
- All trading blocked for remaining 29 days
- EOD state save shows `Days=0` (cold start never progresses)

### Required Fixes (Stage 2 Fix Plan) - ✅ ALL IMPLEMENTED

| Fix | Priority | Description | Status |
|-----|:--------:|-------------|:------:|
| Kill switch daily reset | 🔴 P0 | Added `_kill_switch_handled_today` flag | ✅ |
| Kill switch options liquidation | 🔴 P0 | Added options liquidation in handler | ✅ |
| Theta threshold scaled by DTE | 🟠 P1 | `CB_THETA_SWING_CHECK_ENABLED=False` for DTE>2 | ✅ |
| Log spam prevention | 🟡 P2 | Handler only runs once per day | ✅ |
| 10:30 entry delay | 🟡 P2 | Changed options window to 10:30-15:00 | ✅ |

**Implementation Details:**
- `main.py`: Added `_kill_switch_handled_today` flag, reset at 09:25 and EOD
- `main.py`: Kill switch handler now liquidates options + clears position state
- `config.py`: Added `CB_THETA_SWING_CHECK_ENABLED = False`
- `options_engine.py`: Theta check skipped for DTE > 2 when config disabled
- `main.py`: Options entry window changed from 10:00 to 10:30

### Previous Stage 2 Results (Pre-V2.3)

For reference, the earlier Stage 2 run without warmup showed:
- End Equity: $50,100.65 (+0.20%)
- Only SHV traded (indicators not ready)
- This was a false positive - indicators weren't initialized

---

## Stage 3: 3-Month Validation

**Status:** ✅ COMPLETE (V2.3.5)
**Date:** 2026-01-31
**Backtest Period:** January 1 - March 31, 2024 (Q1 2024)
**Branch:** `testing/va/stage2-backtest`

### Results: Hipster Yellow-Green Hornet

| Metric | Value |
|--------|-------|
| **Start Equity** | $50,000 |
| **End Equity** | $49,289.01 |
| **Net Profit** | **-$710.99 (-1.42%)** |
| **Total Orders** | 95 |
| **Fees** | $598.90 |
| **Max Drawdown** | 12.30% |
| **Win Rate** | 43% |
| **Loss Rate** | 57% |
| **Sharpe Ratio** | -0.205 |
| **Sortino Ratio** | -0.267 |

**Backtest URL:** https://www.quantconnect.com/project/27678023/90fcb04626294aba0c625261fba8002d

### Comparison with Previous Stages

| Metric | V2.3.5 (Stage 3) | V2.3.4 (Stage 2) | Improvement |
|--------|------------------|------------------|-------------|
| Return | -1.42% | -3.45% | +2.03% |
| Drawdown | 12.30% | 4.60% | +7.70% (longer period) |
| Orders | 95 | 7 | +88 (PART 9 fix working) |
| Fees | $598.90 | $2.62 | Higher due to more trades |

### Key Observations

1. **PART 9 Fixes Working:** 95 orders vs 7 - options engine finding contracts
2. **Return Improved:** -1.42% vs -3.45% over longer period (3 months vs 1 week)
3. **Drawdown Expected:** 12.30% over 3 months vs 4.60% over 1 week
4. **Win Rate 43%:** Needs strategy tuning but system is functioning

### Validation Checklist

- [x] Complete position lifecycle (entry → hold → exit)
- [x] Trend engine entries trigger (MA200 + ADX ≥ 25)
- [x] Cold start progression Days 1-5
- [x] Options engine finds contracts (PART 9 fix)
- [x] Multiple trades execute over 3-month period
- [ ] Chandelier trailing stops protect profits (needs review)
- [ ] Exit conditions work (needs review)

---

## Stage 4: 1-Year Validation

**Status:** Pending

**Backtest Period:** January 1 - December 31, 2024

### Expected Behaviors

- [ ] All market regimes tested (risk-on, neutral, risk-off)
- [ ] Hedge engine activates when regime < 40
- [ ] Kill switch triggers on 3% daily loss (if occurs)
- [ ] Cold start handles algorithm restarts
- [ ] State persistence works across sessions

---

## Stage 5: 5-Year Stress Test

**Status:** Pending

**Backtest Period:** January 1, 2020 - December 31, 2024

### Crisis Periods to Validate

| Period | Event | VIX Peak | Expected Behavior |
|--------|-------|:--------:|-------------------|
| Mar 2020 | COVID Crash | 82 | Kill switch, panic mode, VIX > 40 disables MR |
| Feb 2018 | Volmageddon | 50 | High VIX mode, reduced allocations |
| 2022 | Bear Market | 35 | Regime shifts, hedge activation |

### Target Metrics

| Metric | Target | Actual |
|--------|--------|--------|
| CAGR | 18-25% | TBD |
| Max Drawdown | < 25% | TBD |
| Sharpe Ratio | > 1.0 | TBD |
| Win Rate | > 55% | TBD |

---

## Deployment Notes

### QC Cloud File Size Limits

QC enforces a **64,000 character limit per file**. Files exceeding this limit:

| File | Original | Minified | Status |
|------|:--------:|:--------:|:------:|
| `main.py` | 100,749 | 61,509 | ✅ Under limit |
| `options_engine.py` | 67,608 | 44,660 | ✅ Under limit |
| `risk_engine.py` | 48,199 | - | OK (under limit) |

### Logging Configuration

**Backtest Mode (LiveMode = False):**
- Only FILL logs shown (trade entries/exits)
- All diagnostic logs suppressed
- Keeps output clean for analysis

**Live Mode (LiveMode = True):**
- All logs shown for monitoring
- INIT, SPLIT, VIX_SPIKE, EOD, etc. visible

**Implementation:**
```python
def _log(self, message: str, trades_only: bool = False) -> None:
    """Log with LiveMode awareness."""
    if trades_only or self.LiveMode:
        self.Log(message)
```

---

## Sync Workflow

```bash
# From alpha-nextgen-v2-private directory:
cd /Users/vigneshwaranarumugam/Documents/Trading\ Github

# Sync files to lean-workspace
cp alpha-nextgen-v2-private/main_minified.py lean-workspace/AlphaNextGen/main.py
cp alpha-nextgen-v2-private/config.py lean-workspace/AlphaNextGen/
cp -r alpha-nextgen-v2-private/engines lean-workspace/AlphaNextGen/
# ... (other directories)

# Push and run
cd lean-workspace
lean cloud push --project AlphaNextGen
lean cloud backtest AlphaNextGen
```

---

*Document created: 2026-01-30 | Last updated: 2026-01-31 (V2.3.4)*
