# Backtest Results - Alpha NextGen V2

> **Purpose:** Track backtest progress, results, and validation status for QC Cloud deployments.
>
> **Last Updated:** 2026-01-30

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
| 2 | 30 days (Jan 2024) | Short-term behavior, actual trades | **BLOCKED** 🔴 |
| 3 | 3 months (Q1 2024) | Position lifecycle, entries/exits | Pending |
| 4 | 1 year (2024) | Full annual cycle, all market conditions | Pending |
| 5 | 5 years (2020-2024) | Long-term stress test, crisis periods | Pending |

### Stage 2 Blockers (2026-01-30)

| Issue | Description | Fix | Status |
|-------|-------------|-----|:------:|
| API Mismatch | `RegimeEngine.calculate()` called with wrong params | Fixed `spy_close` → `spy_closes` | ✅ Fixed |
| Log Spam | 410 `INTRADAY_SIGNAL` logs/day | Changed `trades_only=False` | ✅ Fixed |
| No Options Trades | Signals generated but no orders | Under investigation | 🔴 Open |

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
**Status:** **FAIL** 🔴
**Backtest Period:** January 2-31, 2024 (with 300-day warmup)
**Branch:** `testing/va/stage2-backtest`
**Backtest Name:** Formal Blue Dragonfly

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
