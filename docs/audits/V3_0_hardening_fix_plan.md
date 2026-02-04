# V3.0 "Hardening" Fix Plan — State Management Audit

> **Date:** 4 February 2026
> **Branch:** `feature/va/v3.0-hardening`
> **Status:** IMPLEMENTED — P0-A through P1-B complete. Pending backtest verification.

---

## Problem Statement

Comprehensive engine audit found **orphaned state** across 6 fix categories. Kill switch liquidates positions via broker but never tells engines to clear internal tracking. Order rejection cleans engine state but not main.py tracking dicts. Daily pre-market reset misses hedge and MR engines entirely.

### Audit Findings

| # | Issue | Severity | Root Cause |
|---|-------|----------|------------|
| P0-A | Kill switch doesn't reset engine state | Critical | `_handle_kill_switch()` calls `Liquidate()` but not `engine.reset()` |
| P0-B | Order rejection missing spread tracking cleanup | Critical | `_handle_order_rejection()` cleans engine but not main.py dicts |
| P0-C | Daily reset missing for hedge + MR engines | Critical | `_on_pre_market_setup()` resets risk + options but not hedge + MR |
| P0-D | Trend pending MOO not cleared on kill switch | Critical | Covered by P0-A — `trend_engine.reset()` clears all state |
| P1-A | KS skip-day uses calendar days not business days | High | `timedelta(days=N)` counts weekends as skip days |
| P1-B | `_pending_exit_orders` never cleaned in EOD | High | Dict entries accumulate after successful exits |
| P2-A | Weekly breaker across week boundary | Medium | Document only — known limitation, no practical impact |

### How We Got Here

Each engine correctly implements its own `reset()` method. The bug class is **missing call sites** — the caller (main.py) liquidates positions at the broker level but forgets to tell the engine its internal tracking is now stale. This produces "ghost state" where the engine thinks positions exist that the broker already closed.

---

## Fix P0-A: Kill Switch Engine State Reset

### File: `main.py` — `_handle_kill_switch()`

**After Tier 3 FULL_EXIT** (after `cold_start_engine.reset()`):
```python
# V3.0 P0-A: Reset all engine internal state after full liquidation
self.trend_engine.reset()
if hasattr(self, "mr_engine") and self.mr_engine:
    self.mr_engine.reset()
if hasattr(self, "hedge_engine") and self.hedge_engine:
    self.hedge_engine.reset()
# Clear main.py spread tracking dicts
self._spread_fill_tracker = None
self._pending_spread_orders.clear()
self._pending_spread_orders_reverse.clear()
self._pending_exit_orders.clear()
self.Log("KS_CLEANUP: All engine state reset after Tier 3 liquidation")
```

**After Tier 2 TREND_EXIT** (after `cold_start_engine.reset()`):
```python
# V3.0 P0-A: Reset trend + MR state after Tier 2 liquidation (hedge stays)
self.trend_engine.reset()
if hasattr(self, "mr_engine") and self.mr_engine:
    self.mr_engine.reset()
self.Log("KS_CLEANUP: Trend + MR state reset after Tier 2 liquidation")
```

**Tier 1 REDUCE:** No engine reset. Positions stay, only sizing reduced.

### What `engine.reset()` clears

| Engine | State Cleared by `reset()` |
|--------|---------------------------|
| `trend_engine` | `_positions`, `_pending_moo_symbols`, `_pending_moo_dates` |
| `mr_engine` | `_position`, `_pending_vix_regime`, `_pending_stop_pct` |
| `hedge_engine` | `_last_allocation` |

---

## Fix P0-B: Order Rejection Spread Tracking Cleanup

### File: `main.py` — `_handle_order_rejection()`, options spread block

After existing `cancel_pending_spread_entry()` + `clear_all_spread_margins()`:
```python
# V3.0 P0-B: Clear main.py spread tracking state on rejection
if self._spread_fill_tracker is not None:
    self.Log("REJECTION_CLEANUP: Clearing spread fill tracker")
    self._spread_fill_tracker = None
if self._pending_spread_orders:
    self.Log(
        f"REJECTION_CLEANUP: Clearing "
        f"{len(self._pending_spread_orders)} pending spread orders"
    )
    self._pending_spread_orders.clear()
    self._pending_spread_orders_reverse.clear()
```

---

## Fix P0-C: Daily Reset for Hedge + MR Engines

### File: `main.py` — `_on_pre_market_setup()`, after `options_engine.reset_daily()`

```python
# V3.0 P0-C: Reset satellite engine daily state
if hasattr(self, "hedge_engine") and self.hedge_engine:
    self.hedge_engine.reset()
if hasattr(self, "mr_engine") and self.mr_engine:
    self.mr_engine.reset()
```

**Note:** `mr_engine.reset()` clears `_position`. MR positions should already have been force-closed at 15:45 the prior day. If an MR position survived overnight (fill failure), the reset correctly clears stale tracking. The actual portfolio position is reconciled separately.

---

## Fix P0-D: Trend Pending MOO on Kill Switch

Covered by P0-A. `trend_engine.reset()` clears `_pending_moo_symbols` and `_pending_moo_dates` (line 743-748). No additional fix needed.

---

## Fix P1-A: KS Skip-Day Business Days

### File: `engines/core/risk_engine.py` — `check_kill_switch_graduated()`

**Before:**
```python
self._ks_skip_until_date = str(skip_date + td(days=config.KS_SKIP_DAYS))
```

**After:**
```python
# V3.0 P1-A: Skip business days, not calendar days
try:
    from datetime import date as date_type
    from datetime import timedelta as td

    skip_date = self.algorithm.Time.date()
    skip_days_remaining = config.KS_SKIP_DAYS
    candidate = skip_date
    while skip_days_remaining > 0:
        candidate += td(days=1)
        if candidate.weekday() < 5:  # Mon=0..Fri=4
            skip_days_remaining -= 1
    self._ks_skip_until_date = str(candidate)
    self.log(
        f"KS_SKIP_DAY: New entries blocked until {self._ks_skip_until_date} "
        f"({config.KS_SKIP_DAYS} business days)"
    )
except (TypeError, AttributeError):
    self.log("KS_SKIP_DAY: Could not compute skip date")
```

**Impact:** Friday KS with `KS_SKIP_DAYS=1` now skips Monday (next business day), not Saturday.

---

## Fix P1-B: EOD Cleanup for `_pending_exit_orders`

### File: `main.py` — `_on_market_close()`, before `_kill_switch_handled_today` comment

```python
# V3.0 P1-B: Clean stale pending exit orders at EOD
if self._pending_exit_orders:
    stale_keys = [
        k for k, v in self._pending_exit_orders.items()
        if v.retry_count >= 3 or v.order_id is None
    ]
    for k in stale_keys:
        self._pending_exit_orders.pop(k, None)
    if stale_keys:
        self.Log(f"EOD_CLEANUP: Cleared {len(stale_keys)} stale pending exit orders")
```

---

## Fix P2-A: Weekly Breaker Across Week Boundary (Document Only)

`_weekly_breaker_active` resets Monday at 09:25 via `set_week_start_equity()`. If the algo starts fresh mid-week (no persisted state), the breaker won't be active even if it should be. Edge case with no practical impact — fresh deploys happen on weekends.

**Decision:** Documented as known limitation. No code change.

---

## Architectural Decision: Why NOT Centralized `_reset_logic_engine()`

### Recommendation Assessed

> "Ensure that `_reset_logic_engine()` is the only place where pending flags are cleared."

### Finding: Not Recommended

Audited **29 distinct reset contexts** with **150+ individual state clearing operations** across main.py. These contexts serve fundamentally different purposes:

| Context | What resets | When |
|---------|------------|------|
| Daily pre-market | All daily flags, all engine daily state | 09:25 every day |
| Order rejection (spread) | That one spread's pending state | On that specific rejection |
| Kill switch Tier 3 | Everything — all engines, all tracking | Once per day max |
| Kill switch Tier 2 | Trend + MR only, hedges explicitly stay | Once per day max |
| Position exit fill | One position in one engine | On each individual fill |
| Spread leg fill | Fill tracker for that leg | On each leg fill |
| Friday reconciliation | Ghost spread state only | Friday 15:45 |

A single method either **over-resets** (wipes all state on a single rejection — creating new bugs) or requires **15+ parameters** to select scope (a dispatcher that adds indirection without reducing risk).

The orphaned state bugs were caused by **missing call sites**, not by decentralization. A centralized method has the same risk — you still need to remember to call it, and you still need to pass the correct scope.

### Recommended Alternative: Post-Event Assertions + Reconciliation Sweeps

**Post-kill-switch assertions** (add to `_handle_kill_switch()` after engine resets):
```python
# V3.0: Assert no stale engine state after liquidation
assert not self.trend_engine._pending_moo_symbols, \
    f"STALE_STATE: Pending MOO {self.trend_engine._pending_moo_symbols} after KS"
assert self._spread_fill_tracker is None, \
    "STALE_STATE: Spread fill tracker active after KS"
assert not self._pending_spread_orders, \
    f"STALE_STATE: {len(self._pending_spread_orders)} pending spread orders after KS"
```

**Post-rejection assertions** (add to `_handle_order_rejection()` after spread cleanup):
```python
assert self.options_engine._pending_spread_long_leg is None, \
    "STALE_STATE: Pending spread long leg after rejection"
assert self._spread_fill_tracker is None, \
    "STALE_STATE: Spread fill tracker after rejection"
```

**Trend reconciliation sweep** (new method, called from OnData like existing `_check_mr_exits()`):
```python
def _check_trend_reconciliation(self) -> None:
    """V3.0: Reconcile trend engine state against actual portfolio."""
    for symbol in list(self.trend_engine._positions.keys()):
        if not self.Portfolio[symbol].Invested:
            self.trend_engine.remove_position(symbol)
            self.Log(f"TREND_RECONCILE: {symbol} in engine but not portfolio — cleared")
```

### Comparison

| Approach | Prevents orphaned state? | Risk of new bugs? | Maintenance cost |
|----------|:------------------------:|:-----------------:|:----------------:|
| Centralized reset method | Partially — still need correct calls | High — over-reset danger | High — god method |
| Post-event assertions | Yes — catches immediately in backtest | Zero — assertions only | Low — add-only |
| Reconciliation sweeps | Yes — catches at runtime | Zero — read-only checks | Low — one method |

Assertions fire during backtests (thousands of market days), so any orphaned state surfaces immediately as a test failure rather than silently degrading live performance.

---

## Files Modified

| File | Fix | Change |
|------|-----|--------|
| `main.py` | P0-A | Engine resets in `_handle_kill_switch()` for Tier 2 + Tier 3 |
| `main.py` | P0-B | Spread tracking cleanup in `_handle_order_rejection()` |
| `main.py` | P0-C | Daily `hedge_engine.reset()` + `mr_engine.reset()` at 09:25 |
| `main.py` | P1-B | Stale `_pending_exit_orders` cleanup at 16:00 |
| `engines/core/risk_engine.py` | P1-A | Business-day-aware KS skip calculation |

**Files NOT modified:**
- `engines/core/trend_engine.py` — `reset()` already exists, now called (P0-A)
- `engines/satellite/mean_reversion_engine.py` — `reset()` already exists, now called (P0-A/P0-C)
- `engines/satellite/hedge_engine.py` — `reset()` already exists, now called (P0-A/P0-C)
- `engines/core/cold_start_engine.py` — No changes
- `config.py` — No changes

---

## Verification

```bash
# 1. All tests must pass
pytest tests/ -x --tb=short

# 2. Kill switch tests specifically
pytest tests/test_risk_engine.py -k "kill_switch" -v
pytest tests/scenarios/test_kill_switch_scenario.py -v

# 3. Key log checks in backtest:
# - "KS_CLEANUP: All engine state reset" → P0-A Tier 3
# - "KS_CLEANUP: Trend + MR state reset" → P0-A Tier 2
# - "REJECTION_CLEANUP: Clearing spread" → P0-B
# - "KS_SKIP_DAY: ... (N business days)" → P1-A
# - "EOD_CLEANUP: Cleared N stale pending exit orders" → P1-B

# 4. Backtest: 2015 Full Year (choppy — exercises KS + spread paths)
./scripts/qc_backtest.sh "V3.0-hardening-2015" --open
```

---

## Next Steps (Post-Backtest)

1. **Add post-event assertions** — catches regression in future changes
2. **Add trend reconciliation sweep** — extends existing MR zombie check pattern
3. **Backtest verification** — 2015 full year (choppy), Q1 2022 (bear)
