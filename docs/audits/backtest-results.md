# Backtest Results - Alpha NextGen V2

> **Purpose:** Track backtest progress, results, and validation status for QC Cloud deployments.
>
> **Last Updated:** 2026-02-02 (V2.4.2: AAP Audit Fixes - Dancing Green Bison)

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
| 2 | 1 month (Jan 2025) | Short-term behavior, actual trades | **V2.3.22 +0.93%** ✅ |
| 2b | 2 months (Jan-Feb 2025) | V2.4 SMA50 exit validation | **V2.4 -17.98%** 🔴 |
| 2c | 1 month (Jan 2025) | V2.4.1 AAP Audit | **V2.4.1 -4.64%** 🔴 |
| 2d | 1 month (Jan 2025) | V2.4.2 AAP Fixes | Pending |
| 3 | 3 months (Q1 2024) | Position lifecycle, entries/exits | Pending |
| 4 | 1 year (2024) | Full annual cycle, all market conditions | Pending |
| 5 | 5 years (2020-2024) | Long-term stress test, crisis periods | Pending |

### Stage 2 Summary (2026-02-01)

**V2.3.22 Run:** V2.3.22-Jan2025-1month | **Result:** +0.93% | **Win Rate:** 53% | **Orders:** 57 ✅ PROFITABLE

### V2.3.22 Backtest Results (Jan 2025) — PROFITABLE ✅

| Metric | Value | Notes |
|--------|------:|-------|
| **Return** | **+0.93%** | Profitable despite 3 kill switch events |
| **Equity** | $50,000 → ~$50,465 | |
| **Total Orders** | 57 | |
| **Win Rate** | 53% | 14 wins / 27 trades |
| **Trades** | 27 | |

**Backtest URL:** https://www.quantconnect.com/project/27678023/4dc08006d60f2b25e04d7f7df6f59691

**P&L Breakdown by Component:**

| Component | P&L | Notes |
|-----------|----:|-------|
| SNIPER (Intraday 0DTE) | +$4,770 | Best performer - captured Jan 16-17 rally |
| SHV (Yield) | +$117 | 9 trades, all wins |
| QLD/SSO (Trend) | +$327 | Solid swing trades |
| SWING (Options) | -$1,985 | Only 3 trades, mostly losses |
| TNA (Trend) | -$954 | Underperforming, consider removal |
| Cold Start Bug | -$2,553 | Duplicate QLD orders (FIXED in V2.3.23) |

**Key Findings:**

1. **SNIPER (Intraday) is the profit center** - +$4,770 from 5 trades
2. **SWING mode losing money** - Only 3 swing trades, -$1,985 total
3. **Cold Start duplicate orders** - Critical bug causing 4× position sizing (FIXED)
4. **SHV orders failing** - 10 "Invalid" orders due to margin lock
5. **Combo orders not executing** - Spread signals generated but no fills

**Bugs Found and Fixed:**

| Bug | Impact | Status |
|-----|--------|:------:|
| Cold Start duplicate MOO orders | -$2,553 | ✅ FIXED (V2.3.23) |
| SHV margin lock | Capital starvation | ✅ FIXED (V2.3.24) |
| Swing delta too restrictive (0.55-0.85) | Missing entries | ✅ FIXED (V2.3.24) |
| Combo orders not filling | No spreads traded | ✅ FIXED (V2.3.24) |
| Intraday signal spam | Log noise | ✅ FIXED (V2.3.24) |

### V2.3.23 Fix: Cold Start Duplicate Orders (2026-02-01)

**Problem:** On Jan 17 (Friday), kill switch triggered and reset cold start. Jan 18-20 (weekend + MLK holiday), `check_warm_entry()` was called each day. Since:
- `_warm_entry_executed` was False (waiting for `confirm_warm_entry()`)
- `has_leveraged_position` was False (MOO orders pending, not filled)

Four separate QLD MOO orders were queued and all filled on Jan 21 = 922 shares instead of 193.

**Fix:** Set `_warm_entry_executed = True` immediately when generating the signal in `check_warm_entry()`, not waiting for fill confirmation.

**Commit:** `05140be` - `fix(cold-start): prevent duplicate warm entry orders on weekends/holidays`

**Impact:** Eliminates -$2,553 excess loss from 4× position sizing.

### V2.3.24 Fix: Hard Margin Reservation + Bug Fixes (2026-02-01)

All remaining V2.3.22 bugs have been fixed in V2.3.24:

| Priority | Bug | Fix | Status |
|:--------:|-----|-----|:------:|
| **P1** | Combo orders rejected | Hard margin reservation + contract scaling | ✅ FIXED |
| **P1** | SHV margin lock | Pre-check `MarginRemaining` before sell | ✅ FIXED |
| **P1** | Swing delta too restrictive | Widened from 0.55-0.85 → 0.50-0.85 | ✅ FIXED |
| **P2** | Intraday signal spam | Lower threshold $500 + 15-min throttle | ✅ FIXED |

**Implementation Details:**

1. **Hard Margin Reservation** (`portfolio_router.py`):
   - Added `SYMBOL_LEVERAGE` config to calculate actual margin consumption
   - `_enforce_source_limits()` now uses margin-weighted allocation
   - Example: 55% trend × 2.4× leverage = 132% margin → scaled down to fit 75%

2. **Combo Contract Scaling** (`portfolio_router.py`):
   - When combo order exceeds margin, scale contracts to fit available margin
   - Minimum 2 contracts (`MIN_SPREAD_CONTRACTS`) or skip trade entirely

3. **SHV Margin Lock Check** (`portfolio_router.py`):
   - Before SHV sell, check if `shv_sell_amount > MarginRemaining`
   - If margin locked, skip SHV liquidation (would fail at broker anyway)

4. **Config Changes** (`config.py`):
   ```python
   MIN_INTRADAY_OPTIONS_TRADE_VALUE = 500  # Lower threshold for options
   SPREAD_LONG_LEG_DELTA_MIN = 0.50        # Was 0.55
   REJECTION_LOG_THROTTLE_MINUTES = 15     # Reduce log spam
   SYMBOL_LEVERAGE = {"QLD": 2.0, "SSO": 2.0, "TNA": 3.0, "FAS": 3.0, ...}
   MIN_SPREAD_CONTRACTS = 2                # Minimum viable spread size
   ```

**Tests:** All 1349 tests pass.

**Commit:** `b14c6dc` - `fix(router): V2.3.24 - hard margin reservation + 4 bug fixes`

---

## V2.4 SMA50 Structural Trend - 2 Month Backtest (2026-02-01)

**Backtest:** V2.4-SMA50-2month-Jan-Feb-2025-fix1 | **Result:** -17.98% | **Status:** 🔴 FAIL

**Backtest URL:** https://www.quantconnect.com/project/27678023/0262c1949be5618254d1f79d977924e0

### V2.4 Changes Tested

| Feature | Config | Purpose |
|---------|--------|---------|
| SMA50 Exit | `TREND_USE_SMA50_EXIT = True` | Replace Chandelier with structural trend |
| SMA50 Buffer | `TREND_SMA_EXIT_BUFFER = 0.02` | Exit when Close < SMA50 × 98% |
| Hard Stops | `TREND_HARD_STOP_PCT = {QLD: 15%, SSO: 15%, TNA: 12%, FAS: 12%}` | Asset-specific stop losses |

### AAP Audit Results

#### Phase 1: Three-Way Match (Funnel Analysis)

| Funnel Stage | Metric | Count | Status |
|--------------|--------|------:|--------|
| 1. Signal Generation | ENTRY_SIGNAL | 50 | ✅ |
| 2. Router Processing | ROUTER: logs | 1,109 | ✅ |
| 3. SHV Liquidation | SHV SELL orders | 13 | ✅ |
| 4. Execution | Filled trades | 82 | ✅ |

**Diagnosis:** Funnel is flowing - signals generating, orders processing, trades executing.

#### Phase 2: Logic Integrity Checks

**A. Trend Engine (SMA50 Exit Logic)**

| Exit Type | Count | Status |
|-----------|------:|--------|
| SMA50_BREAK | 24 | ✅ Working |
| HARD_STOP | 0 | ⚠️ Never triggered |
| REGIME_EXIT | 0 | ⚠️ Never triggered |

**Sample SMA50 Exits (working correctly):**
```
2025-01-02 TREND: EXIT_SIGNAL SSO | SMA50_BREAK: Close $45.59 < SMA50 $47.11 * 98% = $46.16
2025-01-06 TREND: EXIT_SIGNAL TNA | SMA50_BREAK: Close $43.42 < SMA50 $47.83 * 98% = $46.88
```

**B. Options Engine**

| Check | Status |
|-------|--------|
| ORDER_ERROR (combo format) | 🔴 15 errors |

**Issue:** Combo orders rejected due to incorrect quantity format.

**C. Risk Engine**

| Check | Count | Status |
|-------|------:|--------|
| KILL_SWITCH triggers | 4 | 🔴 CRITICAL |
| CATASTROPHIC liquidations | 3 | 🔴 CRITICAL |

**Kill Switch Events:**

| Date | Loss | Action |
|------|------|--------|
| 2025-01-07 | 5.13% | Options-only liquidation |
| 2025-01-16 | **8.43%** | CATASTROPHIC - Full liquidation |
| 2025-01-27 | 5.29% | CATASTROPHIC - Full liquidation |
| 2025-02-20 | 5.22% | CATASTROPHIC - Full liquidation |

#### Phase 3: Critical Failure Flags

| Severity | Keyword | Count | Status |
|----------|---------|------:|--------|
| 🔴 CRITICAL | INSUFFICIENT_MARGIN | 0 | ✅ PASS |
| 🔴 CRITICAL | ZeroDivisionError | 0 | ✅ PASS |
| 🟡 WARN | Order rejected | 0 | ✅ PASS |
| 🟡 WARN | No data for | 0 | ✅ PASS |
| 🟢 INFO | SHV_AUTO_LIQUIDATE | 5 | ✅ PASS |
| 🔴 CRITICAL | ORDER_ERROR | 15 | 🔴 FAIL |
| 🔴 CRITICAL | KILL_SWITCH | 23 | 🔴 FAIL |

#### Phase 4: Performance Reality Check

| Metric | Value |
|--------|-------|
| **Total P&L** | **-$8,988.24** |
| **Return** | **-17.98%** |
| Win Rate | 46.9% (38/81) |
| Trend P&L | -$1,351.07 (25 trades) |
| **Options P&L** | **-$7,814.00 (27 trades)** |
| SHV P&L | +$176.83 (29 trades) |

**Largest Single Losses:**

| Symbol | P&L | Date |
|--------|----:|------|
| QQQ 250207C00522500 | -$1,093 | 2025-01-22 |
| QLD | -$1,001 | 2025-02-21 |
| QQQ 250221C00530000 | -$955 | 2025-01-24 |

### V2.4 Findings Summary

| Area | Status | Finding |
|------|--------|---------|
| SMA50 Exit Logic | ✅ PASS | Working correctly (24 exits triggered) |
| Hard Stop Logic | ⚠️ UNTESTED | No positions dropped 12-15% from entry |
| Kill Switch | 🔴 FAIL | Triggered 4 times, 3 catastrophic full liquidations |
| Options Engine | 🔴 FAIL | Combo order errors, -$7,814 P&L (main loss driver) |
| Trend Engine | ⚠️ WARN | -$1,351 P&L, frequent SMA50 exits in choppy market |

### Root Cause Analysis

**Primary Issue: Options Engine Bleeding**
- Options lost -$7,814 (87% of total loss)
- 15 combo ORDER_ERROR events (spread orders rejected)
- Kill switches triggered by options losses, not trend losses

**Secondary Issue: SMA50 Too Sensitive in Choppy Markets**
- 24 SMA50_BREAK exits in 2 months = frequent whipsaws
- TNA/FAS repeatedly entering/exiting (below SMA50 most of the period)
- Consider: wider buffer (3% instead of 2%) or regime filter

### Bugs to Fix (V2.4.1)

| Priority | Bug | Evidence | Remediation |
|:--------:|-----|----------|-------------|
| **P1** | Combo order format | 15 ORDER_ERROR | Fix combo quantity format (global qty × ratio) |
| **P2** | Options over-trading | 27 trades, -$7,814 | Review intraday signal throttling |
| **P2** | SMA50 whipsaw | 24 exits in 2 months | Consider wider buffer or ADX filter |

**Commits:**
- `0896796` - `feat(trend): V2.4 - SMA50 structural trend exit + hard stops`
- `8647e82` - `fix(router): use agg.sources instead of agg.source in options check`

---

## V2.3.22 Remaining Bugs - Detailed Analysis (Historical)

### Bug Overview

| Priority | Bug | Evidence | Root Cause | Remediation |
|:--------:|-----|----------|------------|-------------|
| **P1** | Combo orders rejected | 4 INSUFFICIENT_MARGIN: Order=$24K > Margin=$2.5K | Spread value > available margin; margin consumed by trend+SHV | Scale contracts to fit OR hard margin reservation |
| **P1** | SHV margin lock | 10 INVALID: Initial Margin > Free Margin | SHV is collateral for leveraged positions, can't be sold | Check `MarginRemaining` before SHV sell |
| **P1** | Swing delta too restrictive | "No valid ITM contract (delta 0.55-0.85)" | Range misses delta 0.50-0.54 | Widen to 0.50-0.85 |
| **P2** | Intraday signal spam | 44 "Delta $X < min $2,000" | No throttle, `MIN_TRADE_VALUE` too high | Add throttle, lower threshold |

---

### P1-A: Combo Orders Rejected (INSUFFICIENT_MARGIN)

**Evidence from logs:**
```
2025-01-07 10:01:00 SPREAD: ENTRY_SIGNAL | BULL_CALL | x26 | DTE=16
2025-01-07 10:01:00 ROUTER: COMBO_ORDER | Long=QQQ 250124C00521000 x26 + Short=QQQ 250124C00527500 x-26
2025-01-07 10:01:00 ROUTER: INSUFFICIENT_MARGIN | Order=$24,362 > Margin=$2,539
```

**Root Cause: Allocation Reservation ≠ Margin Reservation**

The current architecture has a **fundamental disconnect** between allocation limits and margin consumption:

1. **Config says:** Reserve 25% for options (`RESERVED_OPTIONS_PCT = 0.25`)
2. **Router enforces:** Non-options capped at 75% (`_enforce_source_limits()` at portfolio_router.py:367-398)
3. **BUT:** This reservation happens at the **TargetWeight level**, not the **margin level**

**The Math Problem:**

| Component | Allocation | Leverage | Margin Consumed |
|-----------|:----------:|:--------:|:---------------:|
| QLD (Trend) | 20% | 2× | 40% |
| SSO (Trend) | 15% | 2× | 30% |
| TNA (Trend) | 12% | 3× | 36% |
| FAS (Trend) | 8% | 3× | 24% |
| **Total Trend** | **55%** | — | **~130%** |
| SHV (Yield) | Variable | 1× | Variable |
| **Remaining for Options** | 25% | — | **~0%** |

When all 4 trend tickers trigger simultaneously (which they do in bull markets since they're 0.70-0.95 correlated), they consume **more margin than their allocation** due to leverage.

**Why SHV Makes It Worse:**

SHV acts as collateral for leveraged positions. When SHV is held:
- It provides buying power for leveraged ETFs
- But it also **locks up margin** - you can't sell it without releasing the leverage first
- This creates a death spiral: more SHV = more trend positions = more margin locked = less margin for options

**Remediation Options:**

| Option | Description | Pros | Cons |
|--------|-------------|------|------|
| **A: Hard Margin Reservation** | Before trend entries, calculate: `margin_for_options = 25% × equity`. Block trend if it would consume this. | Guarantees options capacity | Reduces trend participation |
| **B: Leverage-Adjusted Allocation** | Calculate `margin_adjusted_weight = allocation × leverage`. Cap total at 100%. | More accurate | Complex calculation |
| **C: Scale Combo Contracts** | If margin insufficient, scale down from 26 to N contracts that fit | No architecture change | May result in tiny positions |
| **D: Sequential Entry** | Enter options FIRST (day 1), then trend (day 2+) | Simple | Delays trend participation |

**Recommended Fix (V2.3.24):**

Implement **Option C + A hybrid**:
1. In `execute_orders()`, if combo order > margin, scale contracts to fit (minimum 2 contracts)
2. In `_enforce_source_limits()`, calculate margin consumption not just allocation %

---

### P1-B: SHV Margin Lock (10 Invalid Orders)

**Evidence from logs:**
```
2025-01-XX: SHV | INVALID | Initial Margin > Free Margin
```

**Root Cause:**

SHV auto-liquidation (`_add_shv_liquidation_if_needed()`) tries to sell SHV to fund option buys, but:
1. SHV is acting as **collateral** for leveraged trend positions
2. IBKR rejects the sell because selling SHV would violate margin requirements
3. This is a margin safety check by the broker, not a bug

**Why This Happens:**

```
Portfolio State:
- Cash: $2,000
- SHV: $15,000 (collateral for $45K in leveraged positions)
- Margin Requirement: $12,000
- Free Margin: $2,000 (not enough to sell SHV)

Options wants: $5,000
Router tries: SELL $5,000 SHV
IBKR says: REJECTED (Initial Margin $12K > Free Margin $2K)
```

**Remediation (V2.3.24):**

Before generating SHV sell order, check:
```python
# In _add_shv_liquidation_if_needed():
if shv_sell_value > self.algorithm.Portfolio.MarginRemaining:
    self.log(f"SHV_MARGIN_LOCK: Cannot sell ${shv_sell_value:,.0f} SHV, margin locked")
    return sells + buys  # Skip SHV liquidation
```

---

### P1-C: Swing Delta Too Restrictive (0.55-0.85)

**Evidence from logs:**
```
2025-01-XX: No valid ITM contract (delta 0.55-0.85) for CALL
```

**Root Cause:**

Current config:
```python
SPREAD_LONG_LEG_DELTA_MIN = 0.55  # V2.3.21: ITM range
SPREAD_LONG_LEG_DELTA_MAX = 0.85  # V2.3.21: ITM range
```

This misses delta 0.50-0.54 which is often the "sweet spot" for ATM-to-slightly-ITM contracts.

**Remediation (V2.3.24):**

```python
SPREAD_LONG_LEG_DELTA_MIN = 0.50  # Include ATM (delta 0.50)
SPREAD_LONG_LEG_DELTA_MAX = 0.85  # Keep upper bound
```

---

### P2: Intraday Signal Spam (44 Rejections)

**Evidence from logs:**
```
2025-01-XX 10:15: INTRADAY: Delta $847 < min $2,000
2025-01-XX 10:16: INTRADAY: Delta $923 < min $2,000
... (44 times)
```

**Root Cause:**

1. `MIN_TRADE_VALUE = 2,000` too high for intraday options (single contracts often $500-1,500)
2. No throttle on rejection logging - logs every minute even when conditions haven't changed

**Remediation (V2.3.24):**

1. Lower intraday minimum: `MIN_INTRADAY_TRADE_VALUE = 500`
2. Add throttle: Only log rejection once per 15 minutes
3. Or: Use contract count threshold instead of dollar value for options

---

### Architecture Recommendation: Hard Allocation vs Soft Allocation

**Current State (Soft Allocation):**
- `RESERVED_OPTIONS_PCT = 0.25` caps non-options at 75% of **target weights**
- Leveraged ETFs consume **more margin** than their weight suggests
- Options get starved despite "having" 25% allocation

**Proposed State (Hard Allocation):**
- Reserve 25% of **actual margin**, not just weight
- Before any trend entry, calculate: `available_margin - 25%`
- Trend can only use margin up to that limit

**Implementation Sketch:**
```python
# In process_immediate() or process_eod():
total_margin = self.algorithm.Portfolio.MarginRemaining
reserved_for_options = total_margin * config.RESERVED_OPTIONS_PCT
margin_for_non_options = total_margin - reserved_for_options

# Scale non-options orders to fit in margin_for_non_options
for order in non_options_orders:
    if cumulative_margin > margin_for_non_options:
        # Scale down or skip
```

This would ensure options **always** have 25% margin available, regardless of trend leverage.

---

## V2.4.1 Consolidated Fix List (2026-02-01)

Based on AAP audit of V2.4 SMA50 2-Month Backtest (Jan-Feb 2025). Total Loss: **-$8,988.24 (-17.98%)**, Options P&L: **-$7,814 (87% of loss)**.

| # | Fix | Severity | File | Description | Status |
|:-:|-----|:--------:|------|-------------|:------:|
| 1 | Intraday counter race | P1 | `options_engine.py` | 4 fills when limit=2 - counter increments AFTER fill, not on signal | ✅ |
| 2 | Intraday scan throttle | P1 | `main.py` | 95 scans/hour instead of 4 - no 15-min throttle before calling intraday check | ✅ |
| 3 | Wrong `target_weight` | P1 | `options_engine.py` | Returns `1.0`/`0.5` instead of actual allocation (0.1875/0.0625) | ✅ |
| 4 | SHV cash reserve | P1 | `yield_sleeve.py` + `config.py` | 25% reserved for options BUT deployed as yield → now 10% hard cash reserve | ✅ |
| 5 | UVXY proxy not in scanning | P1 | `main.py` | Scanning passes `self._current_vix` (stale daily) instead of UVXY-derived proxy | ✅ |
| 6 | Combo order format | P2 | `portfolio_router.py`, `execution_engine.py` | Leg.Create takes RATIO (1/-1), not quantity - was causing 26×26=676 contracts! | ✅ |
| 7 | Naked call fallback | P2 | `options_engine.py` | SWING_FALLBACK to single ITM CALL creates unlimited loss exposure | ⏳ |
| 8 | Kill switch on fills | P2 | `main.py` | After options fill, check if kill switch already tripped → immediate exit | ✅ |

### Fix #1: Intraday Counter Race Condition

**Problem:** `_intraday_trades_today` counter incremented on FILL, not on signal. Multiple signals fired before first fill incremented counter.

**Evidence:**
```
2025-01-21: 4 fills when limit=2
  10:30:00 INTRADAY_SIGNAL (counter=0)
  10:30:01 INTRADAY_SIGNAL (counter=0 still!)
  10:30:15 FILL (now counter=1)
  10:30:16 FILL (now counter=2)
  ... 2 more fills leaked through
```

**Fix (options_engine.py ~line 2300):**
```python
# BEFORE
if self._intraday_trades_today >= config.INTRADAY_MAX_TRADES_PER_DAY:
    return None
# Counter only incremented after broker fill!

# AFTER
if self._intraday_trades_today >= config.INTRADAY_MAX_TRADES_PER_DAY:
    return None
# Increment counter IMMEDIATELY on signal generation
self._intraday_trades_today += 1
# In on_fill(), do NOT increment again
```

### Fix #2: Intraday Scan Throttle

**Problem:** No throttle in main.py before calling `get_intraday_direction()` and `check_intraday_entry_signal()`. Engine scans every minute (95 scans/hour).

**Evidence:**
```
Logs show MICRO_UPDATE 95 times per trading hour (every minute)
Should be 4 times per hour (every 15 minutes)
```

**Fix (main.py ~line 2567):**
```python
# Add throttle check before intraday scanning
if not self._should_scan_intraday():  # 15-min throttle
    return

def _should_scan_intraday(self) -> bool:
    """Check if enough time passed since last intraday scan."""
    if self._last_intraday_scan is None:
        self._last_intraday_scan = self.Time
        return True
    if (self.Time - self._last_intraday_scan).total_seconds() >= 900:  # 15 min
        self._last_intraday_scan = self.Time
        return True
    return False
```

### Fix #3: Wrong target_weight Values

**Problem:** Options signals return `target_weight=1.0` or `target_weight=size_mult` instead of actual allocation.

**Evidence:**
```python
# options_engine.py line 1704 (swing)
target_weight=1.0,  # Should be config.OPTIONS_SWING_ALLOCATION (0.1875)

# options_engine.py line 2431 (intraday)
target_weight=size_mult,  # Should be config.OPTIONS_INTRADAY_ALLOCATION * size_mult (0.0625)
```

**Fix:**
```python
# Swing (line 1704)
target_weight=config.OPTIONS_SWING_ALLOCATION,  # 0.1875

# Intraday (line 2431)
target_weight=config.OPTIONS_INTRADAY_ALLOCATION * size_mult,  # 0.0625 * 0.5
```

### Fix #4: SHV Cash Reserve (Architecture Change)

**Problem:** 25% reserved at allocation level, but SHV deployed as yield becomes margin collateral for leveraged positions. When options need funds, SHV can't be sold.

**Evidence:**
```
34 SHV_MARGIN_LOCK events in logs
Router tries to sell SHV → IBKR rejects (margin violation)
```

**Fix (yield_sleeve.py, portfolio_router.py):**
```python
# Option A: Hard Cash Reserve (no yield on reserved portion)
HARD_CASH_RESERVE_PCT = 0.10  # 10% stays as CASH, not SHV
# This cash is never deployed, always available for options

# Option B: Options Priority Queue
# Before any trend entry, ensure 25% margin remains available
available_margin = Portfolio.MarginRemaining * (1 - RESERVED_OPTIONS_PCT)
# Scale trend orders to fit within available_margin
```

### Fix #5: UVXY Proxy Not in Scanning Function (NEW)

**Problem:** `_update_micro_regime()` correctly calculates `vix_intraday_proxy` from UVXY. But scanning loop passes `self._current_vix` (stale daily close), overwriting the correct state.

**Evidence:**
```python
# main.py line 1142 (_update_micro_regime)
vix_intraday_proxy = self._vix_at_open * (1 + uvxy_change_pct / 150)  # Correct!

# main.py line 2574 (scanning loop)
vix_current=self._current_vix,  # WRONG! Uses stale daily VIX
```

**Impact:** SNIPER sees daily VIX (unchanged all day), not live intraday VIX changes from UVXY proxy.

**Fix (main.py ~line 2570):**
```python
# Create shared helper
def _get_vix_intraday_proxy(self) -> float:
    """Get UVXY-derived VIX proxy for intraday direction."""
    uvxy_current = self.Securities[self.uvxy].Price
    if self._uvxy_at_open > 0:
        uvxy_change_pct = (uvxy_current - self._uvxy_at_open) / self._uvxy_at_open * 100
        return self._vix_at_open * (1 + uvxy_change_pct / 150)
    return self._current_vix

# In scanning loop (line 2574)
intraday_direction = self.options_engine.get_intraday_direction(
    vix_current=self._get_vix_intraday_proxy(),  # Use UVXY proxy!
    vix_open=self._vix_at_open,
    ...
)

# Also update check_intraday_entry_signal (line 2597)
intraday_signal = self.options_engine.check_intraday_entry_signal(
    vix_current=self._get_vix_intraday_proxy(),  # Use UVXY proxy!
    ...
)
```

### Fix #6: Combo Order Format ✅ IMPLEMENTED

**Problem:** 15 `ORDER_ERROR: Order type does not support this order type` for combo/spread orders.

**Root Cause:** `Leg.Create(symbol, quantity)` takes a **RATIO**, not absolute quantity. For a standard 1:1 spread, ratio should be 1 (long) and -1 (short). Old code passed quantity (e.g., 26), so `ComboMarketOrder(legs, 26)` tried to create 26 × 26 = 676 contracts!

**Fix (portfolio_router.py, execution_engine.py):**
```python
# V2.4.1 FIX: Leg.Create takes RATIO, not absolute quantity
num_spreads = abs(order.quantity)
long_ratio = 1 if order.side == OrderSide.BUY else -1
short_ratio = -1 if order.side == OrderSide.BUY else 1

legs = [
    Leg.Create(order.symbol, long_ratio),        # Ratio = 1
    Leg.Create(order.combo_short_symbol, short_ratio),  # Ratio = -1
]

# ComboMarketOrder multiplies ratios by num_spreads
self.algorithm.ComboMarketOrder(legs, num_spreads)
```

### Fix #7: Disable Naked Call Fallback ⏳ PENDING

**Problem:** SWING_FALLBACK creates single ITM CALL on failed spread construction → unlimited loss potential.

**Fix (options_engine.py):**
```python
# Disable naked call fallback
if config.DISABLE_SWING_NAKED_FALLBACK:
    self.log("SWING: Spread construction failed, skipping (naked fallback disabled)")
    return None
```

### Fix #8: Kill Switch Check on Fills ✅ IMPLEMENTED

**Problem:** Kill switch may trip between signal generation and fill. Options continue trading when account should be frozen.

**Fix (main.py OnOrderEvent):**
```python
# V2.4.1 FIX #8: Kill switch check on options fills
is_option = orderEvent.Symbol.SecurityType == SecurityType.Option
if is_option and fill_qty > 0:  # Only check BUY fills (new positions)
    if self.risk_engine.is_kill_switch_active():
        self.Log(
            f"KILL_SWITCH_ON_FILL: Options position opened while kill switch active | "
            f"{symbol} x{fill_qty} @ ${fill_price:.2f} | LIQUIDATING IMMEDIATELY"
        )
        self.MarketOrder(orderEvent.Symbol, -fill_qty)
```

---

## V2.4.2 AAP Audit Fixes - Dancing Green Bison (2026-02-02)

**Backtest Audited:** V2.4.1-1month-DancingGreenBison | **Result:** -4.64% | **Win Rate:** 57%
**AAP Audit:** `docs/audits/AAP_Dancing_Green_Bison_V2.4.1.md`

### Why 57% Win Rate = Loss?

**Asymmetric P&L Distribution:**
- Wins: 24 trades averaging +$350 (small profits)
- Losses: 18 trades with catastrophic single losses up to -$25,116

**Root Cause:** Long legs of spreads held to expiration, options traded during kill switch chaos.

### V2.4.2 Fixes (7 Total)

| # | Fix | Severity | File | Description | Status |
|:-:|-----|:--------:|------|-------------|:------:|
| 1 | Kill switch double-trade | P1 | `main.py` | BUY-to-close short options triggered KILL_SWITCH_ON_FILL which re-sold | ✅ |
| 2 | Kill switch margin order | P1 | `main.py` | QC margin bug - must close SHORT options before LONG | ✅ |
| 3 | ADX threshold | P2 | `trend_engine.py` | Score 0.50 = ADX 15-24 (weak), changed to 0.75 = ADX 25-34 | ✅ |
| 4 | Spread stop-loss | P2 | `options_engine.py` | Exit at 50% loss of entry debit (was no stop) | ✅ |
| 5 | Stop tier contracts | P2 | `config.py` | Reduced from 23-34 to 8-15 contracts per tier | ✅ |
| 6 | Expiration Hammer | P2 | `config.py` | Force close at 2:00 PM (was 3:45 PM) | ✅ |
| 7 | Trend MOC timing | P2 | `trend_engine.py`, `portfolio_router.py` | Same-day close with MOC (was next-day open MOO) | ✅ |

### Fix Details

#### Fix #1: Kill Switch Double-Trade Bug

**Problem:** When kill switch triggered, it tried to close ALL positions. BUY-to-close of a SHORT option (credit spread short leg) triggered `KILL_SWITCH_ON_FILL` which immediately re-SOLD the just-bought position, creating an infinite loop.

**Fix (main.py OnOrderEvent):**
```python
# V2.4.2 FIX: Only for OPENING buys, not closing buys
if is_option and fill_qty > 0:  # BUY fills
    current_position = self.Portfolio[orderEvent.Symbol].Quantity
    is_opening_trade = current_position > 0  # After fill, still long = was opening
    if is_opening_trade and self.risk_engine.is_kill_switch_active():
        self.MarketOrder(orderEvent.Symbol, -fill_qty)
```

#### Fix #2: Kill Switch Margin Order Bug

**Problem:** QC has margin calculation bugs. When closing options during kill switch, closing LONG legs first causes margin violation because the SHORT legs add theoretical risk.

**Fix (main.py _handle_kill_switch):**
```python
# V2.4.2 FIX: Close SHORT options first, then LONG options
short_options = []
long_options = []
for kvp in self.Portfolio:
    holding = kvp.Value
    if holding.Invested and holding.Symbol.SecurityType == SecurityType.Option:
        if holding.Quantity < 0:
            short_options.append(holding)
        else:
            long_options.append(holding)

# Close shorts first (reduces margin requirement)
for holding in short_options:
    self.MarketOrder(holding.Symbol, -holding.Quantity)

# Then close longs
for holding in long_options:
    self.MarketOrder(holding.Symbol, -holding.Quantity)
```

#### Fix #3: ADX Threshold Fix

**Problem:** `ADX_WEAK_THRESHOLD = 15` made score 0.50 correspond to ADX 15-24 (weak trends). Entries happening at ADX 18-22 had 29% win rate.

**Fix (trend_engine.py):**
```python
# Condition 2: ADX >= 25 (score >= 0.75, sufficient momentum)
# V2.4.2 FIX: Changed from 0.50 to 0.75
if score < 0.75:
    self.log(f"TREND: {symbol} entry blocked - ADX {adx:.1f} too weak (score={score:.2f} < 0.75)")
    return None
```

#### Fix #4: Spread Stop-Loss

**Problem:** No stop-loss on spreads. Long legs of debit spreads held to expiration, losing 100% of entry debit.

**Fix (config.py + options_engine.py):**
```python
# config.py
SPREAD_STOP_LOSS_PCT = 0.50  # V2.4.2: Stop loss at 50% of entry debit

# options_engine.py check_spread_exit_conditions()
elif pnl_pct < -config.SPREAD_STOP_LOSS_PCT:
    exit_reason = f"STOP_LOSS {pnl_pct:.1%} (lost > {config.SPREAD_STOP_LOSS_PCT:.0%} of entry)"
```

#### Fix #5: Stop Tier Contracts

**Problem:** `OPTIONS_STOP_TIERS` allowed 23-34 contracts per trade. Single losses reached -$25,116.

**Fix (config.py):**
```python
OPTIONS_STOP_TIERS = {
    # V2.4.2 FIX: Reduced contract limits from 23-34 to 8-15
    3.00: {"stop_pct": 0.20, "contracts": 15},  # Score 3.0-3.25
    3.25: {"stop_pct": 0.22, "contracts": 12},  # Score 3.25-3.5
    3.50: {"stop_pct": 0.25, "contracts": 10},  # Score 3.5-3.75
    3.75: {"stop_pct": 0.30, "contracts": 8},   # Score 3.75-4.0
}
```

#### Fix #6: Expiration Hammer (14:00)

**Problem:** 3:45 PM force close for expiring options was too late. Options lose most extrinsic value in final hours, and last-minute exits face wide spreads + slippage.

**Fix (config.py):**
```python
# V2.4.2: Expiration Hammer - Moved from 3:45 PM to 2:00 PM
OPTIONS_EXPIRING_TODAY_FORCE_CLOSE_HOUR = 14
OPTIONS_EXPIRING_TODAY_FORCE_CLOSE_MINUTE = 0
```

**Rationale:**
- 2:00 PM gives 1h55m buffer before close
- Theta decay accelerates after 2 PM
- Better liquidity for exit fills
- Aligns with institutional "no 0DTE after 2 PM" risk rules

#### Fix #7: Trend MOC Timing

**Problem:** Trend entries used `Urgency.EOD` → `OrderType.MOO` for next-day open fills. Overnight gaps caused entries at unfavorable prices.

**Fix (trend_engine.py, models/enums.py, portfolio_router.py):**
```python
# models/enums.py - Added new urgency
class Urgency(Enum):
    IMMEDIATE = "IMMEDIATE"
    EOD = "EOD"
    MOC = "MOC"  # V2.4.2: Market-On-Close for same-day trend entries

# trend_engine.py - All entry signals now use:
urgency=Urgency.MOC,  # V2.4.2: Same-day close (was EOD/next-day open)

# portfolio_router.py - MOC order handling:
class OrderType(Enum):
    MARKET = "MARKET"
    MOO = "MOO"
    MOC = "MOC"  # V2.4.2

# Order execution:
elif order.order_type == OrderType.MOC:
    self.algorithm.MarketOnCloseOrder(order.symbol, quantity)
```

**Rationale:**
- Entries fill same day at market close
- Avoids overnight gap risk
- More predictable fill prices
- Matches institutional execution patterns

### Commits

- `0f1d7a5` - `fix(kill-switch): V2.4.2 - double-trade and margin order bugs`
- `c58e35e` - `fix(options): V2.4.2 - ADX threshold, spread stop-loss, contract limits`
- `30a095a` - `feat(timing): V2.4.2 - Expiration Hammer 14:00 + Trend MOC orders`

### Expected Impact

| Issue | Before V2.4.2 | After V2.4.2 |
|-------|---------------|--------------|
| Kill switch chaos | Double-trades, margin errors | Clean single liquidation |
| Weak trend entries | ADX 15-24 allowed (29% win rate) | ADX 25+ required |
| Spread losses | Held to 100% loss | Capped at 50% loss |
| Single trade loss | Up to $25,116 | Max ~$8,000 (15 contracts × $5.30) |
| Expiring options | Exit 3:45 PM (theta decay) | Exit 2:00 PM (better fills) |
| Trend entry gaps | Overnight gap risk (MOO) | Same-day fills (MOC) |

### Next Steps

1. Sync V2.4.2 to QC cloud
2. Run 1-month backtest (Jan 2025)
3. Compare metrics to V2.4.1 Dancing Green Bison

---

### Historical Runs

**V2.3.12 Run:** V2.3.12-ComboFix-2month | **Result:** +4.09% | **Orders:** 143 (but only 7 options!)
**V2.3.15 Run:** V2.3.15-SniperLogic-1week | **Result:** -1.23% | **Analysis:** Delta 0.70 blocked, direction conflict
**V2.3.16 Run:** Pending | **Expected:** DTE-based delta + direction conflict fixes enable proper swing/intraday trades
**V2.3.17 Run:** Pending | **Expected:** Kill switch 5% + 10% cash buffer reduces false triggers and SHV churn
**V2.3.18 Run:** Pending | **Expected:** Gamma trap fix (exit 4 DTE) + swing entry 6 DTE ensures 2+ day hold
**V2.3.19 Run:** Pending | **Expected:** ITM_MOMENTUM time window now configurable (10:00-13:30)
**V2.3.20 Run:** Pending | **Expected:** Cold start options (50% sizing) + SHV auto-liquidation prevents buying power errors

#### V2.3.12 Backtest Results (Jan 1 - Feb 29, 2024)

| Metric | Value | Notes |
|--------|------:|-------|
| **Return** | **+4.09%** | First positive backtest! |
| **Equity** | $50,000 → $52,047 | +$2,047 net profit |
| **Total Orders** | 143 | Up from 9-14 (system trading!) |
| **Sharpe Ratio** | 0.656 | Positive risk-adjusted return |
| **Sortino Ratio** | 0.905 | Good downside protection |
| **Win Rate** | 42% | Room for improvement |
| **Avg Win** | +1.45% | |
| **Avg Loss** | -1.44% | Balanced risk/reward |
| **Drawdown** | 9.10% | Manageable |
| **Fees** | $120.49 | |
| **Compounding Annual Return** | 27.65% | Projected |

**Backtest URL:** https://www.quantconnect.com/project/27678023/99384af2cd3dfa3219d6f95ba2f584fd

**Key Fixes That Enabled This Result:**
1. ✅ **ComboMarketOrder** (V2.3.9) - No more $729K margin errors
2. ✅ **ADX Entry Threshold** (V2.3.12) - 20 → 15 catches grinding trends
3. ✅ **ADX Exit Threshold** (V2.3.12) - 20 → 10 holds during consolidation
4. ✅ **VIX Barriers Lowered** (V2.3.11/12) - More 0DTE opportunities
5. ✅ **Expiring Options Safety** (V2.3.11) - 15:45 force close prevents auto-exercise

### V2.3.13 Critical Fix: Options Orders Not Executing (2026-02-01)

**Problem:** Micro regime engine was selecting contracts but orders never fired.

**Root Cause:** In `_scan_options_signals()` (main.py), intraday signals were added to `_pending_weights` via `receive_signal()` but `_process_immediate_signals()` was NEVER called. The function returned early via swing spread path, and by the time `OnData` step 9 ran, the signal was lost.

**Evidence from logs:**
```
INTRADAY: Selected PUT | Strike=425.0 | Delta=0.24 | DTE=0   ← Contract selected OK
SPREAD: No valid OTM contract for short leg                   ← Swing mode fails (separate issue)
                                                              ← No order fired! Signal lost.
```

**Comparison:** Every other place in the codebase calls `_process_immediate_signals()` after receiving IMMEDIATE urgency signals (MR entries, stop hits, force exits). Only options signals were missing this call.

**Fix Applied:**
- Added `_process_immediate_signals()` after intraday signal reception (main.py line 2561)
- Added `_process_immediate_signals()` after spread signal reception (main.py line 2626)

**Status:** Ready for V2.3.13 backtest validation

### V2.3.14 Fix: PART 16 Architect Recommendations (2026-02-01)

**Based on analysis of V2.3.12 logs (V2_3_12_ComboFix_2month_logs.txt)**

| Issue | Evidence | Impact | Fix |
|-------|----------|--------|-----|
| Entry throttle too aggressive | 639 DEBIT_FADE → 7 signals | 99% signal loss | Use trades count, not attempt flag |
| Hardcoded fade direction | 0 ITM_MOMENTUM signals | 100% momentum loss | Get engine direction first |
| No single-leg fallback | "No valid ATM" thousands of times | 100% swing spread loss | Add single-leg fallback |

**Key Evidence from Logs:**
```
Jan 8, 2024:
  10:00-10:29: DEBIT_FADE recommended (52 times) but blocked by time window
  10:30:00: INTRADAY_SIGNAL fires! Entry @ $0.49
  10:33:00: STOP_TRIGGERED! Exit @ $0.42 (-15%)
  10:34+: ALL subsequent DEBIT_FADE blocked by _entry_attempted_today
```

**Fixes Applied:**
1. Replaced `_entry_attempted_today` with `_intraday_trades_today >= INTRADAY_MAX_TRADES_PER_DAY`
2. Added `get_intraday_direction()` - engine decides direction, not hardcoded fade
3. Added single-leg swing fallback when spread selection fails

**Config Change:** `INTRADAY_MAX_TRADES_PER_DAY = 3` (allows 3 intraday trades per day)

**Status:** Ready for V2.3.14 backtest validation

### V2.3.15 Fix: SNIPER LOGIC - PART 17 Architect Recommendations (2026-02-01)

**Problem:** V2.3.14 may fire too many options orders - micro engine needs to be a sniper, not machine gunner.

**Root Cause Analysis (from PART 17):**
1. QQQ threshold at 0.15% was too loose - treating market noise as tradeable signals
2. FADE strategy had no minimum move check (unlike MOMENTUM which has 0.80%)
3. 3 trades/day allows too much churn - sniper gets one shot + one retry

**Sniper Philosophy:** "Wait for high-conviction setups, filter noise"

**4-Gate Filtering System:**
| Gate | Purpose | Threshold |
|------|---------|-----------|
| Gate 0 | Pre-flight checks | Position, trades, time window |
| Gate 1 | Noise filter | QQQ move >= 0.35% |
| Gate 2 | VIX context | Direction determines strategy |
| Gate 3 | Strategy qualification | FADE >= 0.50%, MOMENTUM >= 0.80% |
| Gate 4 | Contract selection | DTE, delta, OI, spread |

**Config Changes (V2.3.15):**
```python
# Gate 1: Noise Filter
QQQ_NOISE_THRESHOLD = 0.35  # V2.3.15: was 0.15%

# Gate 3a: FADE Strategy
INTRADAY_DEBIT_FADE_MIN_MOVE = 0.50  # V2.3.15: new (QQQ must move >= 0.50% for FADE)

# Trade Management
INTRADAY_MAX_TRADES_PER_DAY = 2  # V2.3.15: was 3 (sniper gets one retry)
```

**Code Changes:**
1. `classify_qqq_move()` - Now uses `config.QQQ_NOISE_THRESHOLD` (0.35%) for UP/DOWN classification
2. `recommend_strategy_and_direction()` - Added Gate 3a FADE min move check:
   - If `abs(qqq_move_pct) < 0.50%`, returns `NO_TRADE` with reason "FADE blocked"

**Zone Classification:**
```
QQQ MOVE FROM OPEN
0%        0.35%           0.50%                   0.80%
│  NOISE   │   WATCHING    │      FADE ZONE        │    MOMENTUM ZONE
│  (block) │   (no edge)   │  (if VIX calm)        │   (if VIX rising)
```

**Documentation:** Created `docs/v2-specs/SNIPER_LOGIC_V2.3.15.md` - Complete specification with flowchart

**Status:** V2.3.15 SNIPER LOGIC complete - Ready for backtest validation

### V2.3.16 Fix: PART 17 Delta + Direction Conflict (2026-02-01)

**Problem:** V2.3.15 1-week backtest showed:
1. Swing fallback blocked by delta validation (0.70 > max 0.60)
2. FADE PUT trades losing on bullish days (regime > 65)

**Root Cause Analysis (from 1-week backtest logs):**
```
Jan 8, 2024 (regime score = 70.5, strong bullish):
  10:00-10:29: "Delta 0.70 > max 0.6" - Swing fallback blocked!
  10:30: FADE PUT fires @ $0.49 (fading rally = wrong direction)
  10:33: STOP @ $0.42 (-14%)
  10:33: FADE PUT fires @ $0.43 (same mistake)
  10:50: STOP @ $0.36 (-16%)
  Result: -$469 on bullish day by fading the trend
```

**3 Issues Fixed:**

| # | Issue | Root Cause | Fix |
|:-:|-------|------------|-----|
| 1 | **Delta 0.70 blocked** | Swing targets 0.70 but validation caps at 0.60 | DTE-based validation |
| 2 | **Direction conflict** | FADE PUT vs regime 70+ = opposing bets | Skip FADE when regime strongly disagrees |
| 3 | **No FADE max cap** | Fading runaway trends/crashes | Add 1.20% upper bound |

**Config Changes (V2.3.16):**
```python
# DTE-Based Delta Validation
OPTIONS_SWING_DTE_THRESHOLD = 5    # DTE > 5 uses swing bounds
OPTIONS_SWING_DELTA_MIN = 0.55     # Swing min (0.70 target - tolerance)
OPTIONS_SWING_DELTA_MAX = 0.85     # Swing max (0.70 target + tolerance)
OPTIONS_INTRADAY_DELTA_MIN = 0.40  # Intraday min (ATM)
OPTIONS_INTRADAY_DELTA_MAX = 0.60  # Intraday max (ATM)

# Direction Conflict Resolution
DIRECTION_CONFLICT_BULLISH_THRESHOLD = 65  # Skip FADE PUT if regime > 65
DIRECTION_CONFLICT_BEARISH_THRESHOLD = 40  # Skip FADE CALL if regime < 40

# FADE Sniper Window (min + max)
INTRADAY_FADE_MIN_MOVE = 0.50   # Min: don't fade noise
INTRADAY_FADE_MAX_MOVE = 1.20   # Max: don't fade runaway trends
```

**Code Changes:**
1. `check_entry_signal()` - DTE-based delta validation (DTE > 5 → swing bounds, else intraday bounds)
2. `recommend_strategy_and_direction()` - FADE max cap (1.20%)
3. `get_intraday_direction()` - Now accepts `regime_score` param, conflict check centralized here
4. `_scan_options_signals()` - Passes regime_score to options engine (decision-making centralized)

**FADE Sniper Window:**
```
QQQ MOVE FROM OPEN
0%        0.35%       0.50%              1.20%              2%+
│  NOISE   │  WATCHING  │   FADE ZONE      │   RUNAWAY         │
│  (block) │  (no edge) │  (sniper fires)  │   (don't fade!)   │
```

**Direction Conflict Logic:**
```
IF strategy == DEBIT_FADE:
  IF regime > 65 AND direction == PUT: SKIP (strong bull, don't fade rally)
  IF regime < 40 AND direction == CALL: SKIP (strong bear, don't fade dip)
```

**Status:** V2.3.16 DTE-BASED DELTA + DIRECTION CONFLICT complete - Ready for backtest validation

### V2.3.17 Fix: Hybrid Yield Sleeve + Kill Switch 5% (2026-02-01)

**Problem:** Kill switch too sensitive (3%), SHV churn from small trades, post-kill-switch cash idle.

| # | Issue | Root Cause | Fix |
|:-:|-------|------------|-----|
| 1 | **Kill switch too sensitive** | 3% triggers 4-8×/year in volatile markets | 5% threshold |
| 2 | **SHV churn** | Every 5% Sniper trade liquidates SHV | 10% cash buffer |
| 3 | **RATES exposure blocks SHV** | 40% cap prevents post-kill-switch SHV | 99% limit |
| 4 | **YIELD allocation low** | 50% cap when all cash should go to SHV | 99% limit |

**Config Changes (V2.3.17):**
```python
# Kill Switch raised from 3% to 5%
KILL_SWITCH_PCT = 0.05
KILL_SWITCH_PCT_BY_PHASE = {"SEED": 0.05, "GROWTH": 0.05}

# Hybrid Yield Sleeve - 10% Cash Buffer
CASH_BUFFER_PCT = 0.10  # Reserve 10% as "petty cash"

# Exposure Limits adjusted
RATES = {"max_net_long": 0.99, ...}  # Was 0.40

# Source Allocation adjusted
"YIELD": 0.99  # Was 0.50
```

**Code Changes:**
1. `yield_sleeve.py` - `calculate_unallocated_cash()` now subtracts 10% cash buffer
2. `portfolio_router.py` - YIELD source limit raised to 0.99
3. `config.py` - RATES exposure limit raised to 0.99

**Churn Reduction Analysis:**
| Trade Size | Without Buffer | With 10% Buffer |
|:----------:|:--------------:|:---------------:|
| 1-5% ($500-2,500) | SHV touched | **No SHV touch** |
| 5-10% ($2,500-5,000) | SHV touched | **No SHV touch** |
| 10-15% ($5,000-7,500) | SHV touched | **Partial SHV** |
| 15%+ ($7,500+) | SHV touched | SHV touched |

**Expected Impact:** ~80% reduction in SHV churn for typical intraday trades.

**Status:** V2.3.17 complete - Ready for backtest validation

### V2.3.18 Fix: Gamma Trap + Swing DTE Alignment (2026-02-01)

**Problem 1:** Single-leg options (undefined risk) were held closer to expiration than spreads (defined risk).

**Problem 2:** Swing entry at DTE=5 with exit at DTE=4 gave only 1-day holding period.

| Position Type | Old Entry | New Entry | Old Exit | New Exit | Min Hold |
|---------------|:---------:|:---------:|:--------:|:--------:|:--------:|
| Spreads | 10-21 | 10-21 | 5 | 5 | 5+ days |
| Single Legs | 5-45 | **6-45** | 2 | **4** | **2+ days** |

**Config Changes (V2.3.18):**
```python
OPTIONS_SINGLE_LEG_DTE_EXIT = 4  # Raised from 2 (exit BEFORE spreads)
OPTIONS_SWING_DTE_MIN = 6        # Raised from 5 (ensures 2+ day hold)
```

**Root Cause (Gamma Trap):** Gamma risk explodes in the final week before expiration. A small adverse move can wipe 50%+ of option value in hours.

**Root Cause (1-Day Hold):** With entry at DTE=5 and exit at DTE=4, "swing" trades had only 1-day holding period. Raising entry to DTE=6 ensures minimum 2-day hold.

**Status:** V2.3.18 complete - Ready for backtest validation

### V2.3.19 Fix: ITM_MOMENTUM Time Window Config (2026-02-01)

**Problem:** ITM_MOMENTUM time window was hardcoded (10:00-13:30) while DEBIT_FADE used config values.

**Config Added (V2.3.19):**
```python
INTRADAY_ITM_START = "10:00"  # Entry window start
INTRADAY_ITM_END = "13:30"    # Entry window end
```

**Intraday Time Windows (Final):**
| Strategy | Start | End | Config Keys |
|----------|:-----:|:---:|-------------|
| DEBIT_FADE | 10:30 | 14:00 | `INTRADAY_DEBIT_FADE_START/END` |
| ITM_MOMENTUM | 10:00 | 13:30 | `INTRADAY_ITM_START/END` |
| CREDIT_SPREAD | 10:00 | 14:30 | `INTRADAY_CREDIT_START/END` |

**Status:** V2.3.19 complete - Ready for backtest validation

### V2.3.20 Fix: Cold Start Options + SHV Auto-Liquidation (2026-02-01)

**Critical Issues Fixed:**

| # | Finding | Severity | Fix |
|:-:|---------|:--------:|-----|
| 1 | SHV Auto-Liquidation missing | CRITICAL | Calculate shortfall, generate SHV SELL |
| 2 | Cold Start blocks ALL options | HIGH | Allow with 50% sizing |

**Fix 1: SHV Auto-Liquidation**

**Problem:** `_add_shv_liquidation_if_needed()` only reordered sells/buys but never calculated shortfall or generated SHV sell orders. Immediate BUY orders failed with "Insufficient Buying Power".

**Solution:**
```python
# portfolio_router.py - _add_shv_liquidation_if_needed()
buy_value = sum(get_order_value(o) for o in buys)
sell_proceeds = sum(get_order_value(o) for o in sells)
projected_cash = available_cash + sell_proceeds
shortfall = buy_value - projected_cash

if shortfall > 0:
    shv_sell_amount = min(shortfall * 1.05, available_shv)  # 5% buffer
    # Generate OrderIntent for SHV SELL, insert at beginning of sells
```

**Fix 2: Cold Start Options with 50% Sizing**

**Problem:** Old logic blocked ALL options during cold start (Days 1-5). Too conservative - missing opportunities.

**Solution:**
- Added `OPTIONS_COLD_START_MULTIPLIER = 0.50` to config.py
- Modified main.py to allow options during cold start
- Added `size_multiplier` parameter to:
  - `get_mode_allocation()` - base allocation calculation
  - `check_spread_entry_signal()` - swing spread entries
  - `check_intraday_entry_signal()` - intraday entries
  - `check_entry_signal()` - single-leg swing fallback

**Config Changes:**
```python
OPTIONS_COLD_START_MULTIPLIER = 0.50  # 50% sizing during Days 1-5
```

**Impact:**
- Options can now trade during cold start at reduced risk (50% size)
- SHV auto-liquidation ensures buying power for immediate trades
- Tests updated: 1349 passed, 2 skipped

**Status:** V2.3.20 complete - Ready for backtest validation

### V2.3.21 Fix: PART 18 Options Engine + Router Fixes (2026-02-01)

**Based on analysis of V2.3.20 backtest logs (V2_3_20_Jan2025_1month_logs.txt)**

**Critical Issues Found:**

| # | Finding | Severity | Root Cause |
|:-:|---------|:--------:|------------|
| 1 | SHV auto-liquidation never triggered | CRITICAL | `_process_immediate_signals` missing `available_cash`, `locked_amount` params |
| 2 | Router logging completely disabled | HIGH | `pass # Logging disabled` + `if False and` blocks all logs |
| 3 | Spread delta mismatch (ATM vs ITM) | HIGH | Code uses 0.40-0.60 (ATM) but strategy needs 0.55-0.85 (ITM) |
| 4 | Trend ignores pending MOO orders | HIGH | No tracking of pending symbols → duplicate ENTRY_APPROVED |
| 5 | Cold Start + Trend signal same symbols | HIGH | Both engines generate signals for QLD → wasteful roundtrip |
| 6 | Position registered twice | HIGH | Duplicate `POSITION_REGISTERED` for same symbol on same bar |
| 7 | 452 "No valid contract" errors | HIGH | Spread scan runs every minute with no throttling |
| 8 | Kill switch logs inconsistent % | LOW | Two different loss percentages logged for same event |

**Fix 1: SHV Auto-Liquidation Cash Params (CRITICAL)**

Problem: `_process_immediate_signals()` called `process_immediate()` without cash parameters.

```python
# AFTER (fixed) - main.py _process_immediate_signals():
self.portfolio_router.process_immediate(
    tradeable_equity=capital_state.tradeable_eq,
    current_positions=current_positions,
    current_prices=current_prices,
    max_single_position=max_single_position,
    available_cash=self.Portfolio.Cash,           # NEW
    locked_amount=capital_state.locked_amount,    # NEW
)
```

**Fix 2: Enable Router Logging**

```python
# AFTER (fixed) - portfolio_router.py:
def log(self, message: str) -> None:
    if self.algorithm:
        self.algorithm.Log(message)
```

**Fix 3: Spread Delta Range - ITM Long / OTM Short ("Smart Swing")**

```python
# config.py - Widened for better execution
SPREAD_LONG_LEG_DELTA_MIN = 0.55  # Was 0.40 (ATM)
SPREAD_LONG_LEG_DELTA_MAX = 0.85  # Was 0.60 (ATM)
```

**Fix 4-6: Trend Pending MOO Tracking + Cold Start Coordination + Position Duplication**

- Add `_pending_moo_symbols: Set[str]` to track pending MOO orders
- Skip Trend entry if Cold Start already signaled same symbol
- Check if position already registered before `register_position()`

**Fix 7: Spread Scan Throttling (15-minute timer)**

- Add `_last_spread_scan_time` tracker
- Skip `select_spread_legs()` if < 15 minutes since last scan
- Reduces 452 errors/day to ~30 errors/day

**Fix 8: Kill Switch Logging Consistency**

- Use single consistent loss calculation (`loss_from_sod`)

**Status:** V2.3.21 in progress

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

### V2.3.7 Cash Margin + Intraday Filter Fixes (2026-01-31)

**Audit Reference:** `docs/audits/stage2-codeaudit.md` (PART 11)

| # | Fix | Severity | Description | Status |
|:-:|-----|:--------:|-------------|:------:|
| 1 | Cash Death Spiral | CRITICAL | Margin cap at 50% prevents over-leverage | ✅ |
| 2 | Intraday Filters Too Tight | HIGH | Relaxed for 0DTE market conditions | ✅ |
| 3 | Spread Short Leg Selection | HIGH | Delta-first selection, wider delta range | ✅ |
| 4 | ADX Threshold High | MEDIUM | Reduced from 25 to 20 for more entries | ✅ |

**Code Changes (V2.3.7):**

1. **Cash Margin Cap** (`portfolio_router.py`):
   - Added `MARGIN_UTILIZATION_CAP = 0.50` (50% max margin)
   - Portfolio equity capped at `cash * 2` to prevent death spiral
   - Prevents broker margin calls triggering liquidation cascade

2. **Intraday Filter Relaxation** (`config.py`):
   - `OPTIONS_SPREAD_WARNING_PCT = 0.20` (was 0.15)
   - `OPTIONS_MIN_OPEN_INTEREST = 100` (was 200)
   - Allows more 0DTE contracts to qualify

3. **Spread Short Leg** (`config.py`):
   - `SPREAD_SHORT_LEG_DELTA_MIN = 0.10` (was 0.15)
   - `SPREAD_SHORT_LEG_DELTA_MAX = 0.50` (was 0.45)
   - Wider delta range for short leg selection

4. **ADX Threshold** (`config.py`):
   - `TREND_ADX_THRESHOLD = 20` (was 25)
   - More trend entries qualify

### V2.3.8 PART14 Volatility + Delta Selection Fixes (2026-01-31)

**Audit Reference:** `docs/audits/stage2-codeaudit.md` (PART 14)

| # | Fix | Severity | Description | Status |
|:-:|-----|:--------:|-------------|:------:|
| 1 | 3× ETF Volatility | HIGH | TNA/FAS tighter stops (2.5× vs 3.5× ATR) | ✅ |
| 2 | Spread Width Filter | HIGH | Delta-first selection, width as tiebreaker | ✅ |
| 3 | 0DTE Stop Too Wide | MEDIUM | 15% stop for 0DTE (was 20-30%) | ✅ |

**Code Changes (V2.3.8):**

1. **3× ETF Symbol-Specific Stops** (`config.py`, `trend_engine.py`):
   ```python
   # config.py
   TREND_3X_SYMBOLS = ["TNA", "FAS"]
   CHANDELIER_3X_BASE_MULT = 2.5    # vs 3.5 for 2×
   CHANDELIER_3X_TIGHT_MULT = 2.0   # vs 3.0 for 2×
   CHANDELIER_3X_TIGHTER_MULT = 1.5 # vs 2.5 for 2×
   ```
   - `get_chandelier_multipliers()` returns symbol-specific multipliers
   - TNA/FAS swing 5-7% daily vs 2-3% for QLD/SSO, need tighter stops

2. **Delta-First Spread Selection** (`options_engine.py`):
   - Removed width filter from `_find_spread_legs()`
   - Sort by delta proximity to target (0.15-0.20), not width
   - Width used only as tiebreaker, not filter

3. **0DTE Tight Stops** (`config.py`, `options_engine.py`):
   ```python
   OPTIONS_0DTE_STOP_PCT = 0.15  # 15% stop for 0DTE
   ```
   - `calculate_position_size()` accepts `days_to_expiry` parameter
   - 0DTE uses 15% stop (limits max loss to ~30% with slippage)

**Backtest Results: V2.3.8-PART14-Fixes**

| Metric | Value |
|--------|-------|
| **Start Equity** | $50,000 |
| **End Equity** | $45,382.80 |
| **Net Profit** | **-$5,946.19 (-9.23%)** |
| **Total Orders** | 141 |
| **Fees** | $465.85 |
| **Max Drawdown** | 17.30% |
| **Win Rate** | 55% |
| **Loss Rate** | 45% |
| **Sharpe Ratio** | -1.02 |
| **Sortino Ratio** | -1.609 |

**Backtest URL:** https://www.quantconnect.com/project/27678023/d6b80fd2d6e288fea94bf9315c36cbc6

**Analysis:**
- **141 orders** vs 95 (V2.3.5) - delta-first selection finding more spreads
- **Win Rate 55%** - improved from 43% (V2.3.5)
- **Drawdown 17.3%** - higher, investigating spread leg fills
- **Sharpe -1.02** - negative due to drawdown, but system functioning

### V2.3.9 ComboMarketOrder for Spreads (2026-01-31)

**Root Cause Analysis:** CTA Technical Memo identified why spread orders rejected with $729K margin:
- IBKR treats sequential leg orders as separate positions
- Selling short leg first = naked short requiring huge margin
- Solution: `ComboMarketOrder` for atomic multi-leg execution

| # | Fix | Severity | Description | Status |
|:-:|-----|:--------:|-------------|:------:|
| 1 | Spread Atomic Execution | CRITICAL | ComboMarketOrder instead of sequential legs | ✅ |
| 2 | Leg.Create API | HIGH | Proper QC combo order construction | ✅ |

**Code Changes (V2.3.9):**

1. **Combo Order Method** (`execution_engine.py`):
   ```python
   def submit_combo_order(
       self,
       long_symbol: str,
       long_quantity: int,
       short_symbol: str,
       short_quantity: int,
       ...
   ) -> ExecutionResult:
       from AlgorithmImports import Leg
       legs = [
           Leg.Create(long_symbol, long_quantity),
           Leg.Create(short_symbol, short_quantity),
       ]
       tickets = self.algorithm.ComboMarketOrder(legs, abs(long_quantity))
   ```

2. **OrderIntent Combo Fields** (`portfolio_router.py`):
   ```python
   @dataclass
   class OrderIntent:
       is_combo: bool = False
       combo_short_symbol: Optional[str] = None
       combo_short_quantity: Optional[int] = None
   ```

3. **Router Combo Execution** (`portfolio_router.py`):
   - `_generate_orders()` creates single combo order for spreads
   - `execute_orders()` uses `ComboMarketOrder` for combo orders
   - Spread margin: ~$42K vs $729K for sequential legs

**Expected Impact:**
- Spreads now execute atomically with proper spread margin
- No more orphaned long legs from rejected short legs
- Margin utilization reduced ~95%

**Next Step:** Run V2.3.9 backtest to validate combo order execution.

### V2.3.10 Critical Pitfalls Fix (2026-01-31)

**Audit Reference:** `docs/audits/stage2-codeaudit.md` (PART 15 Forensics)

**V2.3.9 Backtest Analysis Results:**
- Account up +$12,079 BUT $20,900 came from accidental stock assignment (lucky)
- ITM calls held into Friday close → auto-exercised Saturday 5 AM
- 800 shares QQQ assigned = $360K notional on $50K account (7:1 leverage)
- Market gap up saved the account - if gap down 2%, account wiped

| # | Fix | Severity | Description | Status |
|:-:|-----|:--------:|-------------|:------:|
| 1 | ADX Entry/Exit Alignment | HIGH | Entry at ADX >= 15 but exit at ADX < 20 = churn | ✅ |
| 2 | Spread Filter Widened | HIGH | ATM contracts need 15% spread (was 5%) | ✅ |
| 3 | Pending Contract Intraday | CRITICAL | `_pending_contract` not set in intraday signal | ✅ |
| 4 | DTE Exit Single-Leg | CRITICAL | Close by 2 DTE to prevent expiration/exercise | ✅ |
| 5 | Single-Leg Exit Checking | HIGH | check_exit_signals call added to _monitor_risk_greeks | ✅ |

**Code Changes (V2.3.10):**

1. **ADX Thresholds Aligned** (`config.py`):
   ```python
   ADX_WEAK_THRESHOLD = 20    # V2.3.10: Restored (was 15)
   ADX_MODERATE_THRESHOLD = 25  # V2.3.10: Restored (was 20)
   ```

2. **Spread Filter Widened** (`config.py`):
   ```python
   OPTIONS_SPREAD_MAX_PCT = 0.15  # V2.3.10: 15% (was 5%)
   ```

3. **Pending Contract for Intraday** (`options_engine.py`):
   ```python
   # In check_intraday_entry_signal():
   self._pending_contract = best_contract
   self._pending_num_contracts = num_contracts
   self._pending_stop_pct = config.OPTIONS_0DTE_STOP_PCT
   ```

4. **DTE Exit for Single-Leg** (`config.py`, `options_engine.py`):
   ```python
   OPTIONS_SINGLE_LEG_DTE_EXIT = 2  # Close by 2 DTE
   ```

### V2.3.11 SNIPER 0DTE Enhancement + Expiring Options Safety (2026-01-31)

**Audit Reference:** `docs/audits/stage2-codeaudit.md` (PART 15 - Remaining Issues)

**Root Causes from PART 15:**
1. EOD Force Close missing for expiring (0 DTE) options → auto-exercise risk
2. VIX barrier at 15 too high → blocking SNIPER 0DTE entries during calm markets
3. SHV_MIN_TRADE already at $10K (fixed in V2.3.6)

| # | Fix | Severity | Description | Status |
|:-:|-----|:--------:|-------------|:------:|
| 1 | EOD Force Close 15:45 | CRITICAL | Force liquidate options expiring TODAY at 15:45 | ✅ |
| 2 | VIX Barrier Lowered | HIGH | VIX_LEVEL_VERY_CALM_MAX: 15 → 11.5 for more 0DTEs | ✅ |
| 3 | VIX Level Boundaries | MEDIUM | Shifted all VIX level thresholds down | ✅ |

**Code Changes (V2.3.11):**

1. **VIX Level Boundaries Shifted** (`config.py`):
   ```python
   # V2.3.11: Fire more SNIPER 0DTEs by lowering VIX barrier
   VIX_LEVEL_VERY_CALM_MAX = 11.5  # VIX < 11.5 = VERY_CALM (was 15)
   VIX_LEVEL_CALM_MAX = 15.0       # VIX 11.5-15 = CALM (was 15-18)
   VIX_LEVEL_NORMAL_MAX = 18.0     # VIX 15-18 = NORMAL (was 18-20)
   # VIX 18-22 = ELEVATED (was 20-23)
   # VIX 22-25 = HIGH (was 23-25)
   ```

2. **EOD Force Close for Expiring Options** (`config.py`, `options_engine.py`, `main.py`):
   ```python
   # config.py
   OPTIONS_EXPIRING_TODAY_FORCE_CLOSE_HOUR = 15
   OPTIONS_EXPIRING_TODAY_FORCE_CLOSE_MINUTE = 45

   # options_engine.py - new function
   def check_expiring_options_force_exit(
       self, current_date, current_hour, current_minute,
       current_price, contract_expiry_date
   ) -> Optional[TargetWeight]:
       # If option expires TODAY and time >= 15:45, force close
   ```

3. **Main.py Integration**:
   - Added `_get_option_expiry_date()` helper
   - Call `check_expiring_options_force_exit()` in `_monitor_risk_greeks()`
   - Force exit takes priority over other exit signals

**Expected Impact:**
- More SNIPER 0DTE trades in calm markets (VIX 11.5-15)
- No more accidental stock assignments from ITM options held to close
- Eliminates 7:1 leverage overnight risk from auto-exercise

### V2.3.12 Enable More 0DTEs + Unchoke Trend Engine (2026-01-31)

**Audit Reference:** `docs/audits/stage2-codeaudit.md` (PART 15 - Trend Engine Choking)

**Root Causes:**
1. ITM momentum strategy required VIX > 25 - only met during high vol events
2. ADX entry threshold at 20 blocked trend entries during grinding rallies (March 2024)

| # | Fix | Severity | Description | Status |
|:-:|-----|:--------:|-------------|:------:|
| 1 | INTRADAY_ITM_MIN_VIX | HIGH | 25 → 11.5 (enable 0-DTE ITM in calm markets) | ✅ |
| 2 | ADX_ENTRY_THRESHOLD | HIGH | 20 → 15 (catch trends earlier, ADX lags) | ✅ |
| 3 | ADX_WEAK_THRESHOLD | HIGH | 20 → 15 (allow entering on grinding trends) | ✅ |
| 4 | TREND_ADX_EXIT_THRESHOLD | CRITICAL | 20 → 10 (allow holding during low momentum) | ✅ |

**Code Changes (V2.3.12):**

```python
# config.py
INTRADAY_ITM_MIN_VIX = 11.5        # V2.3.12: was 25
ADX_ENTRY_THRESHOLD = 15           # V2.3.12: was 20
ADX_WEAK_THRESHOLD = 15            # V2.3.12: was 20
TREND_ADX_EXIT_THRESHOLD = 10      # V2.3.12: was 20 - CRITICAL for grind
```

**Expected Impact:**
- ITM momentum trades now fire when VIX > 11.5 (vs 25) - ~90% more opportunities
- Trend engine enters on ADX >= 15 (vs 20) - catches earlier trend starts
- Positions held during grinding periods (exit only when ADX < 10)
- March 2024 grinding rally would now generate AND hold entries

### V2.4.0 Planned: Bidirectional Mean Reversion (Post-Backtest)

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
- V2.3.9 fixes critical margin issue - need to validate first
- Bidirectional MR is a strategy enhancement, not a bug fix
- Will implement after V2.3.9 backtest validates combo orders

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

*Document created: 2026-01-30 | Last updated: 2026-02-02 (V2.4.2 AAP Audit Fixes - 7 Bugs)*
