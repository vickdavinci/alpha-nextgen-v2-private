# P0: Options Macro Regime Gate — Implementation Plan (V2)

**Date:** 2026-02-05
**Status:** APPROVED
**Priority:** P0 (Critical)
**Reference:** `docs/audits/REGIME_ENGINE_CALIBRATION_ISSUE.md`, `docs/audits/OPTIONS_GOVERNOR_FLOW_ANALYSIS.md`

---

## Executive Summary

Comprehensive fix for options/Governor interaction with four components:

1. **Macro Regime Gate** — PUT-only with 50% sizing in NEUTRAL (not strict block)
2. **Intraday Governor Gate** — Add missing Governor check to intraday options
3. **Protective Puts Implementation** — Actually implement crisis PUT buying
4. **Sizing Stack** — Multiply Governor × Macro Gate reductions

---

## Decision Matrix (Approved)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| NEUTRAL zone | PUT-only @ 50% sizing | Defensive but captures opportunities |
| Intraday Governor | Add check | Close the gap |
| Sizing stack | Multiply | Both signals = extra caution |
| PROTECTIVE_PUTS | Implement | TMF/PSQ alone not sufficient |

---

## Component 1: Macro Regime Gate (Tiered)

### Logic

```python
def _check_macro_regime_gate(
    self,
    macro_regime_score: float,
    requested_direction: OptionDirection,
    mode: OptionsMode,
) -> Tuple[bool, float, str]:
    """
    V3.2: Enforce macro regime constraints on all options entries.

    Returns:
        Tuple of (allowed: bool, size_multiplier: float, reason: str)
    """
    mode_str = mode.value if mode else "UNKNOWN"

    # BULL regime (70+): All directions allowed, full sizing
    if macro_regime_score >= config.REGIME_RISK_ON:
        return True, 1.0, ""

    # NEUTRAL regime (50-69): PUT-only @ 50% sizing
    if macro_regime_score >= config.REGIME_NEUTRAL:
        if requested_direction == OptionDirection.CALL:
            return False, 0.0, f"MACRO_GATE: {mode_str} CALL blocked in NEUTRAL ({macro_regime_score:.0f})"
        else:
            return True, 0.5, f"MACRO_GATE: {mode_str} PUT allowed in NEUTRAL @ 50%"

    # CAUTIOUS/DEFENSIVE/BEAR (<50): PUT-only, full sizing
    if requested_direction == OptionDirection.PUT:
        return True, 1.0, ""
    else:
        return False, 0.0, f"MACRO_GATE: {mode_str} CALL blocked - regime {macro_regime_score:.0f} < 50"
```

### Scenario Matrix

| Regime | Score | CALL | PUT | Size Multiplier |
|--------|-------|------|-----|-----------------|
| RISK_ON | 70-100 | ✅ | ✅ | 1.0 |
| NEUTRAL | 50-69 | ❌ | ✅ | **0.5** |
| CAUTIOUS | 40-49 | ❌ | ✅ | 1.0 |
| DEFENSIVE | 30-39 | ❌ | ✅ | 1.0 |
| RISK_OFF | 0-29 | ❌ | ✅ | 1.0 |

---

## Component 2: Intraday Governor Gate

### Current Gap

```python
# check_intraday_entry_signal() has NO Governor check!
# Intraday trades can fire even at Governor 0%
```

### Fix

```python
def check_intraday_entry_signal(
    self,
    ...,
    governor_scale: float = 1.0,  # NEW parameter
    macro_regime_score: float = 50.0,
) -> Optional[TargetWeight]:
    """V3.2: Added governor_scale parameter."""

    # ... existing checks ...

    # Update Micro Regime Engine
    state = self._micro_regime_engine.update(...)

    direction = state.recommended_direction
    if direction is None:
        return None

    # ═══════════════════════════════════════════════════════════════
    # V3.2: GOVERNOR GATE FOR INTRADAY (closes gap)
    # ═══════════════════════════════════════════════════════════════
    if governor_scale <= 0:
        if direction == OptionDirection.CALL:
            self.log("INTRADAY: CALL blocked at Governor 0%")
            return None
        # PUT allowed at Governor 0% (reduces risk)
        self.log("INTRADAY: PUT allowed at Governor 0% (defensive)")

    # ═══════════════════════════════════════════════════════════════
    # V3.2: MACRO REGIME GATE
    # ═══════════════════════════════════════════════════════════════
    allowed, macro_multiplier, reason = self._check_macro_regime_gate(
        macro_regime_score=macro_regime_score,
        requested_direction=direction,
        mode=OptionsMode.INTRADAY,
    )
    if not allowed:
        self.log(reason)
        return None

    # ═══════════════════════════════════════════════════════════════
    # V3.2: SIZING STACK (Governor × Macro Gate)
    # ═══════════════════════════════════════════════════════════════
    combined_multiplier = governor_scale * macro_multiplier
    if combined_multiplier < 0.1:  # Less than 10% = not worth trading
        self.log(f"INTRADAY: Size too small after stacking ({combined_multiplier:.0%})")
        return None

    # ... rest of entry logic uses combined_multiplier for sizing ...
```

### main.py Update

```python
# In _scan_intraday_options() or equivalent:
intraday_signal = self.options_engine.check_intraday_entry_signal(
    vix_current=vix_intraday,
    vix_open=self._vix_at_open,
    qqq_current=qqq_price,
    qqq_open=self._qqq_at_open,
    current_hour=self.Time.hour,
    current_minute=self.Time.minute,
    current_time=str(self.Time),
    portfolio_value=effective_portfolio_value,
    best_contract=intraday_contract,
    size_multiplier=cold_start_multiplier,
    macro_regime_score=regime_score,
    governor_scale=self._governor_scale,  # NEW
)
```

---

## Component 3: Protective Puts Implementation

### Current State (Broken)

```python
# options_engine.py line 4232-4234
if state.recommended_strategy == IntradayStrategy.PROTECTIVE_PUTS:
    self.log(f"INTRADAY: Protective mode - regime={state.micro_regime.value}")
    return None  # ← Does nothing!
```

### Fix: Implement Actual PUT Buying

```python
# In check_intraday_entry_signal(), REPLACE the broken code:

if state.recommended_strategy == IntradayStrategy.PROTECTIVE_PUTS:
    # V3.2: Actually implement protective puts
    if not config.PROTECTIVE_PUTS_ENABLED:
        self.log(f"INTRADAY: Protective mode (disabled) - regime={state.micro_regime.value}")
        return None

    # Force direction to PUT for protection
    direction = OptionDirection.PUT

    # Use reduced sizing for protective puts (insurance, not directional bet)
    protective_size_pct = config.PROTECTIVE_PUTS_SIZE_PCT  # e.g., 2%

    # Skip macro gate for protective puts (they're defensive by definition)
    # But still apply Governor scaling
    effective_size = protective_size_pct * governor_scale

    if effective_size < 0.005:  # Less than 0.5% = not worth it
        self.log(f"INTRADAY: Protective PUT size too small ({effective_size:.1%})")
        return None

    self.log(
        f"PROTECTIVE_PUT: Crisis detected | Micro={state.micro_regime.value} | "
        f"Score={state.micro_score:.0f} | Size={effective_size:.1%}",
        trades_only=True,
    )

    # Continue to contract selection and entry...
    # (rest of the logic similar to other intraday entries)
```

### Config Additions

```python
# config.py - Protective Puts Configuration

# V3.2: Protective Puts (Crisis Hedge via PUT Options)
PROTECTIVE_PUTS_ENABLED = True
PROTECTIVE_PUTS_SIZE_PCT = 0.02  # 2% of portfolio (small, insurance)
PROTECTIVE_PUTS_DTE_MIN = 3      # Minimum 3 DTE (time for recovery)
PROTECTIVE_PUTS_DTE_MAX = 7      # Maximum 7 DTE (balance cost vs protection)
PROTECTIVE_PUTS_DELTA_TARGET = 0.30  # OTM puts (cheaper, more leverage)
PROTECTIVE_PUTS_STOP_PCT = 0.50  # 50% stop (it's insurance, accept loss)
```

### When Protective Puts Trigger

From Micro Regime Engine (line 1107-1109):
```python
danger_regimes = {
    MicroRegime.RISK_OFF_LOW,
    MicroRegime.BREAKING,
    MicroRegime.UNSTABLE,
    MicroRegime.FULL_PANIC,
    MicroRegime.CRASH,
    MicroRegime.VOLATILE,
}
if micro_regime in danger_regimes:
    if micro_score < 0:
        return IntradayStrategy.PROTECTIVE_PUTS, OptionDirection.PUT, "Crisis protection"
```

**Trigger conditions:**
- VIX spiking + QQQ crashing
- Micro score goes negative (severe stress)
- Intraday crisis detection

---

## Component 4: Sizing Stack (Multiply)

### Formula

```
final_size = base_size × governor_scale × macro_multiplier × cold_start_multiplier
```

### Example Scenarios

| Scenario | Governor | Macro Gate | Cold Start | Final Size |
|----------|----------|------------|------------|------------|
| Bull, healthy | 100% | 100% | 100% | **100%** |
| Bull, drawdown | 50% | 100% | 100% | **50%** |
| Neutral, healthy | 100% | 50% | 100% | **50%** |
| Neutral, drawdown | 50% | 50% | 100% | **25%** |
| Neutral, cold start | 100% | 50% | 50% | **25%** |
| Recovery | 50% | 50% | 100% | **25%** |

### Minimum Threshold

```python
MINIMUM_POSITION_SIZE_PCT = 0.10  # 10% - below this, don't trade

if combined_multiplier < MINIMUM_POSITION_SIZE_PCT:
    self.log(f"Size too small: {combined_multiplier:.0%} < {MINIMUM_POSITION_SIZE_PCT:.0%}")
    return None
```

---

## Complete Flow Diagram (After Fix)

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           OPTIONS ENTRY FLOW (V3.2)                             │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                         GATE 1: GOVERNOR                                │   │
│  │  Location: Beginning of entry functions                                 │   │
│  │                                                                          │   │
│  │  Governor = 0%?                                                         │   │
│  │  ├─► CALL: BLOCKED                                                      │   │
│  │  └─► PUT: ALLOWED (defensive)                                           │   │
│  │                                                                          │   │
│  │  Governor = 50%? → size_multiplier = 0.5                               │   │
│  │  Governor = 100%? → size_multiplier = 1.0                              │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                         │                                       │
│                                         ▼                                       │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                      GATE 2: MACRO REGIME                               │   │
│  │  Location: After direction determined, before state mutations           │   │
│  │                                                                          │   │
│  │  BULL (70+)?                                                            │   │
│  │  └─► All allowed, macro_mult = 1.0                                      │   │
│  │                                                                          │   │
│  │  NEUTRAL (50-69)?                                                       │   │
│  │  ├─► CALL: BLOCKED                                                      │   │
│  │  └─► PUT: ALLOWED, macro_mult = 0.5                                     │   │
│  │                                                                          │   │
│  │  CAUTIOUS/BEAR (<50)?                                                   │   │
│  │  ├─► CALL: BLOCKED                                                      │   │
│  │  └─► PUT: ALLOWED, macro_mult = 1.0                                     │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                         │                                       │
│                                         ▼                                       │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                      GATE 3: SIZE CALCULATION                           │   │
│  │                                                                          │   │
│  │  combined = governor_scale × macro_mult × cold_start_mult               │   │
│  │                                                                          │   │
│  │  combined < 10%? → BLOCKED (too small)                                  │   │
│  │  combined >= 10%? → CONTINUE with combined size                         │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                         │                                       │
│                                         ▼                                       │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                      SPECIAL: PROTECTIVE PUTS                           │   │
│  │  Trigger: Micro Regime crisis (score < 0)                               │   │
│  │                                                                          │   │
│  │  • Bypasses macro gate (defensive by definition)                        │   │
│  │  • Fixed 2% sizing (insurance)                                          │   │
│  │  • Still applies Governor scaling                                       │   │
│  │  • OTM PUTs (delta ~0.30) for leverage                                  │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                         │                                       │
│                                         ▼                                       │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                      STATE MUTATIONS & ORDER                            │   │
│  │  (Only after all gates pass)                                            │   │
│  │                                                                          │   │
│  │  • Increment trade counter                                              │   │
│  │  • Set pending flags                                                    │   │
│  │  • Generate TargetWeight signal                                         │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Files to Modify

| File | Changes |
|------|---------|
| `config.py` | Add macro gate flag, protective puts config, min size threshold |
| `engines/satellite/options_engine.py` | Add `_check_macro_regime_gate()`, modify 4 entry functions, implement protective puts |
| `main.py` | Pass `governor_scale` to intraday options call |
| `tests/test_options_engine.py` | Add tests for all 4 components |

---

## Config Changes

```python
# config.py additions for V3.2

# ═══════════════════════════════════════════════════════════════
# V3.2: OPTIONS MACRO REGIME GATE
# ═══════════════════════════════════════════════════════════════
OPTIONS_MACRO_REGIME_GATE_ENABLED = True

# Sizing multiplier for NEUTRAL regime (50-69)
# PUT-only allowed, at reduced sizing
OPTIONS_NEUTRAL_ZONE_SIZE_MULT = 0.50

# Minimum combined size to proceed with trade
OPTIONS_MIN_COMBINED_SIZE_PCT = 0.10  # 10%

# ═══════════════════════════════════════════════════════════════
# V3.2: PROTECTIVE PUTS (Crisis Hedge)
# ═══════════════════════════════════════════════════════════════
PROTECTIVE_PUTS_ENABLED = True
PROTECTIVE_PUTS_SIZE_PCT = 0.02       # 2% of portfolio
PROTECTIVE_PUTS_DTE_MIN = 3           # Minimum DTE
PROTECTIVE_PUTS_DTE_MAX = 7           # Maximum DTE
PROTECTIVE_PUTS_DELTA_TARGET = 0.30   # OTM for leverage
PROTECTIVE_PUTS_DELTA_TOLERANCE = 0.10  # Accept 0.20-0.40
PROTECTIVE_PUTS_STOP_PCT = 0.50       # 50% stop (insurance)

# ═══════════════════════════════════════════════════════════════
# V3.2: INTRADAY GOVERNOR GATE
# ═══════════════════════════════════════════════════════════════
INTRADAY_GOVERNOR_GATE_ENABLED = True  # Close the gap
```

---

## Implementation Checklist

### Phase 1: Macro Regime Gate
- [ ] Add `OPTIONS_MACRO_REGIME_GATE_ENABLED` to config.py
- [ ] Add `OPTIONS_NEUTRAL_ZONE_SIZE_MULT` to config.py
- [ ] Add `_check_macro_regime_gate()` method to options_engine.py
- [ ] Modify `check_spread_entry_signal()` to use gate
- [ ] Modify `check_credit_spread_entry_signal()` to use gate
- [ ] Modify `check_intraday_entry_signal()` to use gate
- [ ] Modify `check_entry_signal()` (fallback) to use gate

### Phase 2: Intraday Governor Gate
- [ ] Add `INTRADAY_GOVERNOR_GATE_ENABLED` to config.py
- [ ] Add `governor_scale` parameter to `check_intraday_entry_signal()`
- [ ] Update main.py to pass `self._governor_scale`
- [ ] Add Governor check logic before macro gate

### Phase 3: Protective Puts
- [ ] Add all `PROTECTIVE_PUTS_*` config values
- [ ] Implement protective puts logic in `check_intraday_entry_signal()`
- [ ] Add contract selection for protective puts (OTM, 3-7 DTE)
- [ ] Add position tracking for protective puts

### Phase 4: Sizing Stack
- [ ] Add `OPTIONS_MIN_COMBINED_SIZE_PCT` to config.py
- [ ] Implement multiply logic in all entry functions
- [ ] Add minimum threshold check

### Phase 5: Testing
- [ ] Unit tests for macro gate (all regime × direction combinations)
- [ ] Unit tests for intraday governor gate
- [ ] Unit tests for protective puts trigger
- [ ] Unit tests for sizing stack (multiply behavior)
- [ ] Integration test: 2022 Q1-Q2 backtest
- [ ] Integration test: 2015 full year backtest

---

## Rollback Plan

```python
# Disable all V3.2 features:
OPTIONS_MACRO_REGIME_GATE_ENABLED = False
INTRADAY_GOVERNOR_GATE_ENABLED = False
PROTECTIVE_PUTS_ENABLED = False
```

---

## Success Criteria

1. **No CALL options in NEUTRAL or below** (unless bull regime)
2. **Intraday respects Governor** (CALL blocked at 0%)
3. **Protective puts fire during crisis** (micro_score < 0)
4. **Size stacking works** (Governor 50% × NEUTRAL 50% = 25%)
5. **Win rate improves** (fewer dead-zone trades)

---

## Changelog

| Date | Author | Change |
|------|--------|--------|
| 2026-02-05 | Claude/VA | Initial plan |
| 2026-02-05 | Claude/VA | V2: Added 4 components per user decisions |
