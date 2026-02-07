# Governor V5.2 Binary - Change Report

**Date:** 2026-02-06
**Version:** V5.2 (Binary Governor)

---

## Executive Summary

Simplified the Drawdown Governor from a multi-level system to a **binary** (two-state) system:
- **100% (Full Trading):** All engines active
- **0% (Defensive Only):** Only PUT spreads and hedges allowed

---

## Changes Made

### 1. config.py — Governor Configuration

| Setting | V5.1 (Old) | V5.2 (New) | Rationale |
|---------|-----------|-----------|-----------|
| `DRAWDOWN_GOVERNOR_LEVELS` | `{0.10: 0.50, 0.18: 0.00}` | `{0.15: 0.00}` | Single threshold, no intermediate state |
| `DRAWDOWN_GOVERNOR_RECOVERY_THRESHOLD` | N/A | `0.12` | NEW: DD must fall below 12% to recover |
| `GOVERNOR_INTRADAY_OPTIONS_MIN_SCALE` | `0.50` | `1.0` | Options only at full trading |
| `GOVERNOR_OPTIONS_SIZING_FLOOR` | `0.50` | `1.0` | No partial sizing |
| `GOVERNOR_EQUITY_RECOVERY_MIN_DAYS_AT_ZERO` | `10` | `7` | Slightly faster recovery |

**Removed/Deprecated:**
- Multi-level comments and documentation
- References to 50%, 25%, 75% states
- Complex state machine diagrams

### 2. engines/core/risk_engine.py — Governor Logic

**Method: `check_drawdown_governor()`**
- CHANGED: Complete rewrite for binary logic
- OLD: Walk through multiple levels, apply hysteresis per level
- NEW: Simple if/else — either 100% or 0%
- Removed: Override immunity check (REGIME_OVERRIDE disabled)

**Method: `_check_equity_recovery_from_zero()`**
- CHANGED: Recovery target from 50% to 100%
- OLD: `self._governor_scale = 0.50`
- NEW: `self._governor_scale = 1.0`
- Log message updated: "FULL TRADING RESTORED"

### 3. main.py — Options Gating

**Section: OPTIONS_EOD swing spread logic (~line 3028-3046)**
- REMOVED: Check for governor between 0-25%
- REMOVED: Check for governor between 25-50%
- SIMPLIFIED: Single check — if not 100%, only PUT allowed
- Updated comments to reflect binary system

---

## What Was NOT Changed

| Component | Status | Reason |
|-----------|--------|--------|
| `GOVERNOR_REGIME_OVERRIDE_ENABLED` | Kept (False) | Already disabled, keep for compatibility |
| `GOVERNOR_HWM_RESET_ENABLED` | Kept (False) | Already disabled, keep for compatibility |
| `_check_regime_guard()` | Kept as-is | Still needed for recovery gating |
| `check_governor_regime_override()` | Kept as-is | Disabled via config flag, no code change needed |
| `_check_hwm_reset()` | Kept as-is | Disabled via config flag, no code change needed |
| Kill Switch logic | Unchanged | Separate from Governor |
| Weekly Breaker | Unchanged | Separate from Governor |
| All other circuit breakers | Unchanged | Independent systems |

---

## Binary Governor State Machine

```
                    ┌─────────────────┐
                    │   100%          │
                    │ FULL TRADING    │
                    │ All engines ON  │
                    └────────┬────────┘
                             │
                      DD >= 15%
                             │
                             ▼
                    ┌─────────────────┐
                    │     0%          │
                    │   DEFENSIVE     │
                    │ PUT spreads +   │
                    │ Hedges only     │
                    └────────┬────────┘
                             │
                      DD < 12% AND
                      Regime Guard PASSES AND
                      Equity Recovery 5%+
                             │
                             ▼
                    ┌─────────────────┐
                    │   100%          │
                    │ FULL TRADING    │
                    └─────────────────┘
```

---

## Testing

| Test Suite | Result |
|------------|--------|
| `test_risk_engine.py` | ✅ 80/80 passed |
| Syntax validation | ✅ All files OK |

---

## Expected Behavior Changes

### Bull Market (e.g., 2017)
- **Old V5.1:** DD 10% → 50% scale, DD 18% → 0%, OVERRIDE could force step-up
- **New V5.2:** DD 15% → 0%, no intermediate state, cleaner recovery

### Bear Market (e.g., 2022)
- **Old V5.1:** Multiple step-downs through 50%, possible oscillation
- **New V5.2:** Single step to defensive at 15%, stays defensive until confirmed recovery

### Choppy Market (e.g., 2015)
- **Old V5.1:** Could oscillate between 100%/50%/0%
- **New V5.2:** Either trading or defensive, no in-between churn

---

## Rollback Plan

If V5.2 underperforms:
1. Restore old `DRAWDOWN_GOVERNOR_LEVELS = {0.10: 0.50, 0.18: 0.00}`
2. Restore `GOVERNOR_INTRADAY_OPTIONS_MIN_SCALE = 0.50`
3. Restore `check_drawdown_governor()` method from git history

---

## Files Modified

1. `config.py` — Governor configuration (lines 616-741)
2. `engines/core/risk_engine.py` — Binary governor logic (lines 352-480, 661-679)
3. `main.py` — Options gating simplification (lines 3028-3046, 3096-3099)

---

*Report generated: 2026-02-06*
*Author: Claude Code*
