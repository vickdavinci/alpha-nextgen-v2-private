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

## Stage 2: 30-Day Validation

**Date:** 2026-01-30
**Status:** **PASS** ✅
**Backtest Period:** January 1-31, 2024
**Branch:** `testing/va/stage1-1day-backtest`

### Configuration

```python
self.SetStartDate(2024, 1, 1)
self.SetEndDate(2024, 1, 31)
self.SetCash(50_000)
```

### Results

| Metric | Value |
|--------|-------|
| **Start Equity** | $50,000.00 |
| **End Equity** | $50,100.65 |
| **Net Profit** | +$100.65 (+0.20%) |
| **CAGR** | 2.40% |
| **Drawdown** | 0.00% |
| **Total Orders** | 1 |
| **Total Fees** | $1.24 |
| **Holdings** | $25,036.05 (SHV) |

**Backtest URL:** https://www.quantconnect.com/project/27678023/7617258f3e69f939e2a314aafb6ffc0b

### Validation Checklist

| Check | Expected | Actual | Status |
|-------|----------|--------|:------:|
| No runtime errors | 0 errors | 0 errors | ✅ |
| Yield sleeve deploys capital | SHV position | $25K SHV | ✅ |
| Positive return | ≥ 0% | +0.20% | ✅ |
| Indicators not ready (warmup) | 252 days needed | Correct | ✅ |
| No trend/MR trades | None (warmup) | None | ✅ |

### Analysis

**Why Only 1 Order (SHV)?**
- Indicators (MA200, ADX, RSI) require **252-day warmup period**
- January 2024 is only 30 days - insufficient for indicator readiness
- System correctly waited for indicators before generating signals
- Only yield sleeve (SHV) traded to deploy unused capital

**Key Observations:**
1. System properly respects indicator warmup requirements
2. Yield sleeve correctly deployed ~50% of capital to SHV
3. Small positive return from SHV interest/appreciation
4. Zero drawdown - no risky trades during warmup period
5. This is exactly the expected "safe" behavior during warmup

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
