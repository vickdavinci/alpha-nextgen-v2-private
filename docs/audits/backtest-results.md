# Backtest Results - Alpha NextGen V2

> **Purpose:** Track backtest progress, results, and validation status for QC Cloud deployments.
>
> **Last Updated:** 2026-01-31

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
| 2 | 7 days (Jan 2-8, 2024) | Short-term behavior, actual trades | **SIZING BUG** 🔴 |
| 3 | 3 months (Q1 2024) | Position lifecycle, entries/exits | Pending |
| 4 | 1 year (2024) | Full annual cycle, all market conditions | Pending |
| 5 | 5 years (2020-2024) | Long-term stress test, crisis periods | Pending |

### Stage 2 Summary (2026-01-30)

**Latest Run:** Retrospective Apricot Leopard | **Result:** -6.92% | **Orders:** 15

**Progress:**
- Kill switch daily reset: ✅ FIXED (scheduler.reset_daily())
- Order spam: 371 → 15 orders ✅
- Cold start progression: Day 1 → Day 2 → Day 3 → Day 4 ✅

**Remaining Issues:**
- Options sizing uses full portfolio ($25K) instead of 5% allocation ($2.5K) 🔴
- Insufficient buying power when trend positions consume margin 🔴

**Next Step:** Fix options position sizing to use `get_mode_allocation()` properly.

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
| 5 | Retrospective Apricot Leopard | -6.92% | 15 | **Kill switch reset working!** But options sizing wrong |

### V2.3.1 Fixes (Post Ugly Tan Lemur)

**Issue Found:** Cold start blocked entries every day with "kill switch active" because:
- `scheduler.is_kill_switch_triggered()` returned True after Day 1
- Called wrong method `scheduler.reset_daily_state()` (doesn't exist)
- Should be `scheduler.reset_daily()`

**Fixes Applied:**
1. Changed to `self.scheduler.reset_daily()` at 09:25 pre-market reset
2. Added `self.options_engine.reset_daily()` at 09:25 pre-market reset

### V2.3.2 Issues Found (Retrospective Apricot Leopard)

**Kill Switch Reset: ✅ WORKING**
- Days progress correctly: Day 1 → Day 2 → Day 3 → Day 4
- Cold start advances when no kill switch trigger

**Options Position Sizing: 🔴 BROKEN**
- Day 1: BUY 471 contracts @ $0.54 = **$25,434** (51% of $50K portfolio!)
- Should be 5% intraday allocation = **$2,500 max** = ~46 contracts
- Position sizing using full portfolio value, not mode allocation

**Insufficient Buying Power: 🔴 NEW BUG**
- Jan 5, 10:30: `Order Error: Insufficient buying power (Value:22050, Free Margin:16523)`
- Jan 8, 10:30: `Order Error: Insufficient buying power (Value:21805, Free Margin:16292)`
- Trend positions (TNA, FAS, QLD) consume margin, leaving too little for options

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

#### Phase A: Make Backtest Runnable (CRITICAL)

| # | Fix | Description |
|:-:|-----|-------------|
| 1 | Fix `target_weight` calculation | Pass calculated `num_contracts` to router instead of 1.0 |
| 2 | Add `requested_quantity` to TargetWeight | Router uses it if present, else fallback to % |
| 3 | Add margin check before options orders | Skip order if insufficient buying power |

#### Phase B: Architecture Decision (HIGH)

| Option | Approach | Pros | Cons |
|--------|----------|------|------|
| A | Keep single-leg, fix sizing | Quick validation | Not V2.3 compliant |
| B | Implement V2.3 debit spreads | Full design compliance | More complex |

#### Phase C: Polish (MEDIUM)

| # | Fix | Description |
|:-:|-----|-------------|
| 6 | Greeks monitoring | Adjust for spread vs single-leg |
| 7 | VIX direction logic | Use intraday trend (30min) not gap |
| 8 | Option chain validation | Handle empty chains with retry |

---

**Required Fixes (Summary):**
1. Options position sizing must use `get_mode_allocation()` (5% for intraday)
2. Add buying power check before placing options orders
3. Pass `_pending_num_contracts` to router instead of `target_weight=1.0`
4. Decide: Single-leg with stops OR V2.3 Debit Spreads

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

**Status:** Pending

**Backtest Period:** January 1 - March 31, 2024

### Expected Behaviors

- [ ] Complete position lifecycle (entry → hold → exit)
- [ ] Trend engine entries trigger (MA200 + ADX ≥ 25)
- [ ] Chandelier trailing stops protect profits
- [ ] Exit conditions work (MA200 cross, ADX < 20, stop hit)
- [ ] Mean reversion intraday trades execute
- [ ] MR positions close by 15:45

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

*Document created: 2026-01-30*
