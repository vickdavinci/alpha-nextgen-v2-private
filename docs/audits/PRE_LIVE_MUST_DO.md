# Pre-Live State Persistence Audit

**Date:** 2026-02-12
**Scope:** State persistence plumbing between `main.py`, `StateManager`, and all engines
**Severity:** LIVE-ONLY (backtests unaffected)
**Status:** 4 issues identified, all require fixes before live deployment

---

## Executive Summary

The state persistence layer has 4 plumbing gaps where `StateManager` declares save/load capabilities that `main.py` never invokes. These gaps are invisible during backtests (state is cleared on init) but will cause **state loss on any live restart** — including routine deployments, crash recovery, and overnight restarts.

**Risk if deployed unfixed:**

| Scenario | Consequence |
|---|---|
| Algorithm restart mid-week | Weekly breaker state lost, no 50% sizing reduction after a losing week |
| Overnight redeployment | Regime score resets to 0, wrong-direction trades until indicators warm up |
| Crash recovery | Execution engine loses pending MOO orders, router loses pending signals |
| Multi-day outage | All accumulated governor/HWM state survives (risk engine IS persisted), but weekly/execution/regime context lost |

---

## Issue 1: Execution and Router State Not Persisted

**Severity:** MEDIUM
**Impact:** Pending orders and router state lost on restart

### What Exists

`StateManager` has full save/load support for both engines:

```
persistence/state_manager.py:518-528  save_execution_state() / load_execution_state()
persistence/state_manager.py:534-544  save_router_state() / load_router_state()
persistence/state_manager.py:556-557  save_all() accepts execution_engine= and router= params
persistence/state_manager.py:604-610  save_all() calls both if provided
```

`ExecutionEngine` serializes meaningful state:

```python
# execution/execution_engine.py:962-969
def get_state_for_persistence(self):
    return {
        "order_counter": self._order_counter,
        "orders": {oid: o.to_dict() for oid, o in self._orders.items()},
        "pending_moo_orders": self._pending_moo_orders,
        "moo_fallback_queue": self._moo_fallback_queue,
    }
```

`PortfolioRouter` serializes:

```python
# portfolio/portfolio_router.py:2823-2829
def get_state_for_persistence(self):
    return {
        "pending_count": len(self._pending_weights),
        "last_order_count": len(self._last_orders),
        "risk_status": self._risk_engine_go,
    }
```

### What's Missing

`main.py` never passes these engines to `save_all()` or `load_all()`:

```python
# main.py:3684-3689 (_save_state)
self.state_manager.save_all(
    capital_engine=self.capital_engine,
    cold_start_engine=self.cold_start_engine,
    risk_engine=self.risk_engine,
    startup_gate=self.startup_gate,
    # execution_engine=  MISSING
    # router=            MISSING
)

# main.py:3617-3622 (_load_state)
self.state_manager.load_all(
    capital_engine=self.capital_engine,
    cold_start_engine=self.cold_start_engine,
    risk_engine=self.risk_engine,
    startup_gate=self.startup_gate,
    # regime_engine=     MISSING (bypassed, see Issue 2)
)
```

Zero calls to `save_execution_state()` or `save_router_state()` anywhere in `main.py`.

### Live Impact

- Restart after submitting MOO orders at 15:45 but before 09:30 execution → orders lost
- Restart during active trading → `_pending_weights` queue lost, signals dropped
- `_risk_engine_go` flag not restored → router may accept signals when risk engine would block

### Fix

```python
# main.py _save_state():
self.state_manager.save_all(
    capital_engine=self.capital_engine,
    cold_start_engine=self.cold_start_engine,
    risk_engine=self.risk_engine,
    startup_gate=self.startup_gate,
    execution_engine=self.execution_engine,  # ADD
    router=self.portfolio_router,            # ADD
)
```

For `_load_state()`, execution and router need manual restore since `load_all()` doesn't accept those params:

```python
# After load_all():
exec_state = self.state_manager.load_execution_state()
if exec_state:
    self.execution_engine.restore_state(exec_state)

router_state = self.state_manager.load_router_state()
if router_state:
    self.portfolio_router.restore_state(router_state)
```

**Prerequisite:** Verify `execution_engine.restore_state()` and `portfolio_router.restore_state()` methods exist. If not, implement them.

---

## Issue 2: Regime Persistence Uses Wrong Key Namespace

**Severity:** HIGH
**Impact:** Regime score resets to 0 on live restart, wrong-direction trades

### What Exists

Two parallel persistence paths for regime state:

| Path | ObjectStore Key | Used By |
|---|---|---|
| StateManager canonical | `ALPHA_NEXTGEN_REGIME` | `save_regime_state()` at state_manager.py:443 |
| main.py direct bypass | `"regime_engine_state"` | `_save_state()` at main.py:3702-3704 |

### How It's Split

**Save path (main.py:3684-3704):**

1. `save_all()` is called WITHOUT `regime_engine=` → StateManager skips regime
2. Then `main.py:3702-3704` saves directly: `ObjectStore.Save("regime_engine_state", ...)`

**Load path (main.py:3617-3664):**

1. `load_all()` is called WITHOUT `regime_engine=` → StateManager skips regime
2. Then `main.py:3660-3664` loads directly: `ObjectStore.Read("regime_engine_state")`

**Backtest clear (main.py:250-260):**

Only clears `"regime_engine_state"`, NOT `"ALPHA_NEXTGEN_REGIME"`.

### Live Impact

The current runtime path IS self-consistent (saves and loads from `"regime_engine_state"`), so **this works today**. However:

- `StateManager.save_all()` with `regime_engine=` would save to `ALPHA_NEXTGEN_REGIME` — a different key
- If someone plumbs `regime_engine=` into `save_all()` (fixing Issue 1 pattern), state would be written to two keys
- `StateManager.load_all()` with `regime_engine=` would load from `ALPHA_NEXTGEN_REGIME` — finding nothing
- Any tooling that reads `StateKeys.ALL_KEYS` for diagnostics will miss the actual regime state

### Fix

Consolidate to ONE path. Two options:

**Option A (Recommended): Route through StateManager**

```python
# main.py _save_state():
self.state_manager.save_all(
    ...
    regime_engine=self.regime_engine,  # ADD - uses ALPHA_NEXTGEN_REGIME
)
# REMOVE: direct ObjectStore.Save("regime_engine_state", ...)

# main.py _load_state():
self.state_manager.load_all(
    ...
    regime_engine=self.regime_engine,  # ADD - uses ALPHA_NEXTGEN_REGIME
)
# REMOVE: direct ObjectStore.Read("regime_engine_state") block

# main.py backtest clear:
# REPLACE "regime_engine_state" with "ALPHA_NEXTGEN_REGIME"
```

**Option B: Keep direct path, remove StateManager helpers**

Not recommended — loses the structured logging (`STATE: SAVED | Regime | Score=X`).

---

## Issue 3: Weekly Breaker State Never Persisted Independently

**Severity:** LOW (partially mitigated)
**Impact:** Theoretical — weekly state survives via risk engine, but dedicated path is dead code

### What Exists

`StateManager` has dedicated weekly state helpers:

```python
# persistence/state_manager.py:488-512
save_weekly_state(week_start_equity, week_start_date, weekly_breaker_triggered)
load_weekly_state() -> Optional[Dict]

# StateKeys
WEEKLY = "ALPHA_NEXTGEN_WEEKLY"
```

### What's Missing

- Zero calls to `save_weekly_state()` or `load_weekly_state()` in `main.py`
- `save_all()` / `load_all()` have no `weekly` parameter
- `ALPHA_NEXTGEN_WEEKLY` is not in the backtest clear list at `main.py:250-260`

### Why It's Partially Mitigated

The risk engine's `get_state_for_persistence()` already includes weekly fields:

```python
# engines/core/risk_engine.py:1847-1851
return {
    ...
    "week_start_equity": self._week_start_equity,
    "weekly_breaker_active": self._weekly_breaker_active,
    ...
}
```

Since risk engine IS persisted via `save_all(risk_engine=self.risk_engine)`, the weekly breaker data **does survive restarts** — embedded inside `ALPHA_NEXTGEN_RISK`.

### Remaining Gap

- `week_start_date` is NOT in the risk engine state — only `week_start_equity` and `weekly_breaker_active`
- If risk engine state becomes corrupted/reset, weekly state is also lost (single point of failure)
- The dedicated `ALPHA_NEXTGEN_WEEKLY` path provides redundancy that is currently unused

### Fix

Low priority. If desired, add weekly state to the save/load path:

```python
# main.py _save_state(), after save_all():
self.state_manager.save_weekly_state(
    week_start_equity=self.risk_engine._week_start_equity,
    week_start_date=str(self._current_week_start_date),
    weekly_breaker_triggered=self.risk_engine._weekly_breaker_active,
)

# main.py _load_state(), after load_all():
weekly = self.state_manager.load_weekly_state()
if weekly:
    self.risk_engine._week_start_equity = weekly["week_start_equity"]
    self.risk_engine._weekly_breaker_active = weekly["weekly_breaker_triggered"]
```

---

## Issue 4: Backtest State Clear List Is Incomplete

**Severity:** LOW (backtests only)
**Impact:** Stale state from previous backtests could leak if keys are ever written

### Current Clear List (main.py:250-260)

```python
state_keys = [
    "ALPHA_NEXTGEN_RISK",
    "ALPHA_NEXTGEN_CAPITAL",
    "ALPHA_NEXTGEN_COLDSTART",
    "ALPHA_NEXTGEN_STARTUP_GATE",
    "ALPHA_NEXTGEN_POSITIONS",
    "options_engine_state",
    "oco_manager_state",
    "regime_engine_state",
]
```

### Missing Keys

| Key | Why Missing |
|---|---|
| `ALPHA_NEXTGEN_REGIME` | Regime uses bypass key `"regime_engine_state"` instead (Issue 2) |
| `ALPHA_NEXTGEN_WEEKLY` | Never written (Issue 3), but should be cleared if fix is applied |
| `ALPHA_NEXTGEN_EXECUTION` | Never written (Issue 1), but should be cleared if fix is applied |
| `ALPHA_NEXTGEN_ROUTER` | Never written (Issue 1), but should be cleared if fix is applied |

### Fix

Use `StateKeys.ALL_KEYS` for a future-proof clear:

```python
if not self.LiveMode:
    from persistence.state_manager import StateKeys
    state_keys = StateKeys.ALL_KEYS + [
        "options_engine_state",
        "oco_manager_state",
        "regime_engine_state",  # Remove after Issue 2 fix
    ]
    for key in state_keys:
        if self.ObjectStore.ContainsKey(key):
            self.ObjectStore.Delete(key)
```

---

## Implementation Priority

| Priority | Issue | Fix Complexity | Risk Without Fix |
|---|---|---|---|
| **P0** | Issue 2: Regime key namespace | Config-level (move to StateManager path) | Wrong-direction trades after restart |
| **P1** | Issue 1: Execution/router not persisted | Code change (add params + verify restore methods) | Lost MOO orders, dropped signals |
| **P2** | Issue 4: Backtest clear incomplete | Config-level (expand key list) | Stale state contamination |
| **P3** | Issue 3: Weekly state redundancy | Code change (wire save/load calls) | Already mitigated via risk engine |

### Recommended Implementation Order

1. **Fix Issue 2** — Consolidate regime to `ALPHA_NEXTGEN_REGIME` via StateManager
2. **Fix Issue 4** — Update backtest clear list (must happen alongside Issue 2)
3. **Fix Issue 1** — Add execution/router to save/load path (verify `restore_state()` methods first)
4. **Fix Issue 3** — Optional, add weekly redundancy

### Verification Checklist

After implementing fixes, run a backtest and verify these log entries appear:

```
STATE: SAVE_ALL | Saved 7 categories    (was 4)
STATE: SAVED | Regime | Score=XX
STATE: SAVED | Execution
STATE: SAVED | Router
STATE: SAVED | Weekly | Equity=$XX,XXX
STATE: LOAD_ALL | Loaded 0 categories   (backtest — state cleared)
```

For live verification, deploy and trigger a manual restart:

```
STATE: LOAD_ALL | Loaded 7 categories
STATE: LOADED | Regime | Score=XX
STATE: LOADED | Risk
STATE: LOADED | Weekly | Equity=$XX,XXX
STATE_RESTORE: Options engine state loaded
STATE_RESTORE: OCO manager state loaded
```

---

## Appendix: Current vs Target State Persistence Map

### Current State (What Survives a Restart)

| Engine | Persisted? | Key | Path |
|---|---|---|---|
| Capital Engine | YES | `ALPHA_NEXTGEN_CAPITAL` | StateManager via `save_all()` |
| Cold Start Engine | YES | `ALPHA_NEXTGEN_COLDSTART` | StateManager via `save_all()` |
| Risk Engine | YES | `ALPHA_NEXTGEN_RISK` | StateManager via `save_all()` |
| Startup Gate | YES | `ALPHA_NEXTGEN_STARTUP_GATE` | StateManager via `save_all()` |
| Options Engine | YES | `"options_engine_state"` | Direct ObjectStore bypass |
| OCO Manager | YES | `"oco_manager_state"` | Direct ObjectStore bypass |
| Regime Engine | YES | `"regime_engine_state"` | Direct ObjectStore bypass |
| Execution Engine | **NO** | `ALPHA_NEXTGEN_EXECUTION` | Dead code |
| Portfolio Router | **NO** | `ALPHA_NEXTGEN_ROUTER` | Dead code |
| Weekly Breaker | Partial | Embedded in `ALPHA_NEXTGEN_RISK` | Missing `week_start_date` |

### Target State (After Fixes)

| Engine | Persisted? | Key | Path |
|---|---|---|---|
| Capital Engine | YES | `ALPHA_NEXTGEN_CAPITAL` | StateManager via `save_all()` |
| Cold Start Engine | YES | `ALPHA_NEXTGEN_COLDSTART` | StateManager via `save_all()` |
| Risk Engine | YES | `ALPHA_NEXTGEN_RISK` | StateManager via `save_all()` |
| Startup Gate | YES | `ALPHA_NEXTGEN_STARTUP_GATE` | StateManager via `save_all()` |
| Regime Engine | YES | `ALPHA_NEXTGEN_REGIME` | StateManager via `save_all()` |
| Execution Engine | YES | `ALPHA_NEXTGEN_EXECUTION` | StateManager via `save_all()` |
| Portfolio Router | YES | `ALPHA_NEXTGEN_ROUTER` | StateManager via `save_all()` |
| Options Engine | YES | `"options_engine_state"` | Direct ObjectStore (unchanged) |
| OCO Manager | YES | `"oco_manager_state"` | Direct ObjectStore (unchanged) |
| Weekly Breaker | YES | `ALPHA_NEXTGEN_WEEKLY` | StateManager dedicated path |

---

## Deferred: MICRO Reversal Gate (Strict Scope + Telemetry)

**Status:** Deferred (implement later)
**Priority:** P1 before live promotion of MICRO
**Intent:** Reduce extreme-VIX intraday whipsaw losses without changing VASS behavior.

### Scope Guardrails (Must Hold)

- Apply to `MICRO` engine only.
- Do not change VASS direction, sizing, or conviction behavior.
- Activate only when VIX is in extreme buckets:
  - `VIX < 14` OR
  - `VIX > 25`
- Trigger only on repeated direction instability:
  - At least `2` direction flips in a rolling `45-60` minute window.
- Response is a short cooldown (`20-30` minutes), not a long hard block.

### Required Telemetry

Add structured rejection + summary telemetry:

- Reason code: `R_MICRO_VIX_REVERSAL_EXTREME`
- Event log fields:
  - `timestamp`
  - `vix_level`
  - `vix_bucket`
  - `flip_count`
  - `window_minutes`
  - `blocked_direction`
  - `cooldown_until`
  - `strategy_at_signal`
- Daily diagnostics:
  - `MicroReversalGateHits`
  - `MicroReversalGateBlockedSignals`
  - `MicroReversalGateBypassSignals`

### State Management Requirements

Keep state minimal and isolated from VASS:

- `micro_vix_dir_history` (rolling flip history with timestamps)
- `micro_reversal_cooldown_until`
- `micro_reversal_last_trigger_at` (dedupe)
- `micro_reversal_block_count_today`

Lifecycle rules:

- Initialize in `Initialize`.
- Reset daily counters/state at EOD/SOD.
- Do not carry stale intraday flip history overnight.
- Fail-open if state unavailable/corrupt (do not block trades silently).

### Acceptance Criteria

- VASS behavior and metrics remain unchanged vs baseline.
- MICRO logs include explicit reversal-gate hits and cooldown lifecycle.
- No broad rise in `Dir=None`/choke outside extreme VIX windows.
- Aug 8-style repeated same-direction intraday entries are reduced.

