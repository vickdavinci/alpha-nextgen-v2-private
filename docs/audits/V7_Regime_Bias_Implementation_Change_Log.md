# V7 Regime-Bias Implementation Change Log

## Version Scope
This document captures what was implemented for **Version 7** of the algo from the V6.22 regime-bias fix plan.

Primary objective:
- Reduce bullish bias in stress regimes.
- Switch faster from lagging macro state to defensive spread behavior.
- Improve spread close reliability during regime transitions.
- Keep participation alive with controlled sizing instead of hard freezes.

---

## Files Changed
- `config.py`
- `engines/satellite/options_engine.py`
- `main.py`

---

## 1) Configuration Changes (V7)

### Stress Overlay and Directional Caps
- Added `REGIME_OVERLAY_STRESS_VIX = 21.0`
- Added `REGIME_OVERLAY_STRESS_VIX_5D = 0.18`
- Added `REGIME_OVERLAY_EARLY_VIX_LOW = 16.0`
- Added `REGIME_OVERLAY_EARLY_VIX_HIGH = 18.0`
- Added `MAX_BULLISH_SPREADS_STRESS = 0`
- Added `MAX_BULLISH_SPREADS_EARLY_STRESS = 1`
- Added `MAX_BEARISH_SPREADS_STRESS = 3`

### Bear Access in Stress
- Added `BEAR_PUT_ENTRY_MIN_OTM_PCT_STRESS = 0.010`

### Spread Close Reliability
- Added `SPREAD_CLOSE_CANCEL_ESCALATION_COUNT = 2`
- Added `SPREAD_CLOSE_RETRY_INTERVAL_MIN = 5`

### Participation Rebalance
- Updated `QQQ_NOISE_THRESHOLD: 0.06 -> 0.05`
- Added `CAUTION_LOW_SIZE_MULT = 0.50`
- Updated `VASS_WIN_RATE_SHUTOFF_SCALE: 0.50 -> 0.40`

---

## 2) Engine Logic Changes (`options_engine.py`)

### 2.1 Fast Regime Overlay Classifier
Implemented `get_regime_overlay_state(vix_current, regime_score)` with states:
- `NORMAL`
- `EARLY_STRESS`
- `STRESS`
- `RECOVERY`

This gives a fast VIX-based override path without changing macro engine internals.

### 2.2 Resolver Precedence Upgrade (VASS)
Enhanced `resolve_trade_signal(..., overlay_state=...)`:
- If `overlay_state == STRESS`, bullish VASS resolution is blocked.
- Emits explicit reason:
  - `NO_TRADE: E_OVERLAY_STRESS_BULL_BLOCK (...)`

This prevents VASS from following lagging bullish macro in stress windows.

### 2.3 Overlay-Aware Swing Slot Gating
Enhanced `can_enter_swing(direction, overlay_state)`:
- Applies dynamic directional caps in stress/early stress.
- Uses explicit reject code:
  - `R_SLOT_DIRECTION_OVERLAY: ...`

This replaces static directional slot behavior with regime-aware shaping.

### 2.4 Overlay-Enforced Spread Entry Blocking
In debit spread path (`check_spread_entry_signal`):
- Blocks `BULL_CALL` entries directly in `STRESS`.
- Uses `E_OVERLAY_STRESS_BULL_BLOCK`.

In credit spread path (`check_credit_spread_entry_signal`):
- Blocks `BULL_PUT_CREDIT` entries in `STRESS`.
- Uses `E_OVERLAY_STRESS_BULL_BLOCK`.

### 2.5 Stress-Conditional Bear-Put Assignment Relaxation
Applied `BEAR_PUT_ENTRY_MIN_OTM_PCT_STRESS` during stress overlay:
- In debit bear-put gate.
- In credit bull-put gate.

This avoids over-blocking bearish structures exactly when needed.

### 2.6 Transition Exit Priority (Engine Layer)
Added stress transition trigger in spread exit evaluation:
- `OVERLAY_STRESS_EXIT: Overlay=STRESS ...`

This prioritizes wrong-way bullish spread exits during stress.

### 2.7 Micro Participation Adjustment
Added `CAUTION_LOW` sizing logic:
- If micro regime is `CAUTION_LOW`, enforce half-size via `CAUTION_LOW_SIZE_MULT`.
- Keeps participation while reducing exposure.

---

## 3) Orchestration / Plumbing Changes (`main.py`)

### 3.1 Overlay Wired into VASS Resolver Paths
Both EOD and intraday VASS resolver flows now pass overlay state:
- `resolve_trade_signal(..., overlay_state=overlay_state)`

### 3.2 Overlay Wired into Swing Slot Check
Spread scan now calls:
- `can_enter_swing(direction=..., overlay_state=overlay_state)`

This makes slot decisions consistent with overlay policy.

### 3.3 Transition Exit Priority in Runtime Loop
In `_check_spread_exit`:
- Added early branch that force-closes bullish spreads if overlay is `STRESS`.
- Sends explicit close signal with reason:
  - `SPREAD_EXIT: OVERLAY_STRESS_EXIT`

### 3.4 Spread Close Cancel Escalation
In `_queue_spread_close_retry_on_cancel`:
- Added per-spread cancel counter.
- After `SPREAD_CLOSE_CANCEL_ESCALATION_COUNT`, immediate sequential close is submitted.
- Emits:
  - `SPREAD_CLOSE_ESCALATED`

Also added lifecycle cleanup for this counter when spreads close or state is cleaned.

### 3.5 Diagnostics Counters Added
New diagnostics counters tracked and emitted in daily summary:
- `OverlayBlocks`
- `OverlaySlotBlocks`
- `SpreadCloseEscalations`

This makes V7 regime-bias and close-escalation behavior measurable in backtest logs.

---

## 4) New/Important Reason Codes and Log Markers

### Decision / Entry
- `E_OVERLAY_STRESS_BULL_BLOCK`
- `R_SLOT_DIRECTION_OVERLAY`

### Exit / Reliability
- `OVERLAY_STRESS_EXIT`
- `SPREAD_CLOSE_ESCALATED`

### Diagnostics
- `OPTIONS_DIAG_SUMMARY` now includes overlay and escalation counters.

---

## 5) Expected Behavior Changes

### In Stress (VIX shock / rapid vol expansion)
- New bullish VASS spreads should be blocked.
- Bullish spread concentration should be forced down by directional caps.
- Existing bullish spreads should be exited faster.

### In Early Stress
- Bullish spread participation allowed but capped.
- Bearish spread capacity preserved.

### In Caution-Low Micro Regime
- Entries are allowed with reduced size instead of hard blocking.

### Spread Exit Reliability
- Canceled close legs should no longer silently drift to expiry without escalation.

---

## 6) Validation Performed

### Completed
- `python3 -m py_compile main.py engines/satellite/options_engine.py config.py` passed.

### Not Completed Locally
- Unit tests could not be executed due to missing local dependency:
  - `pytest` not installed in current runtime.

---

## 7) Backtest Validation Checklist for V7
Use these checks on the next runs:
1. `E_OVERLAY_STRESS_BULL_BLOCK` appears in bear/choppy stress windows.
2. `R_SLOT_DIRECTION_OVERLAY` appears when bullish slots are saturated under stress.
3. `SPREAD_CLOSE_ESCALATED` appears only when close cancels repeat.
4. Bullish spread share drops materially in stress periods.
5. Expiry hammer tail-loss events reduce versus V6.21/V6.22 baseline.
6. 2017 bull performance degradation stays within agreed tolerance.

---

## 8) Notes
This V7 release is a **targeted hardening and bias-control update**. It is intentionally minimal in architecture impact (no engine rewrite), but adds explicit overlay-driven precedence and measurable telemetry to verify behavior in bear/choppy transitions.
