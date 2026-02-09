# WORKBOARD.md - Alpha NextGen V2 Task & Ownership Board

> **Single source of truth for:** Who owns what, what's in progress, what's next.
>
> **Rules:**
> - Pull before editing (avoid conflicts)
> - Move tasks by cut/paste
> - Update when starting/finishing work

---

## Team

| Member | Initials | Focus Area |
|--------|----------|------------|
| Vigneshwaran | VA | Strategy Engines, Models |
| [Collaborator] | -- | Core Engines, Execution |

---

## V2 Fork Status

> **V2 Forked from V1 v1.0.0** (2026-01-26)
>
> **Completed:**
> - PHASE 0: V1 Structural Audit ✅
> - PHASE 1: Repository Hard Fork ✅
> - PHASE 2: Core-Satellite Refactoring ✅
> - PHASE 3: Master Plan (`V2_IMPLEMENTATION_ROADMAP.md`) ✅
>
> **Architecture:**
> - `engines/core/` - Foundational engines (70%)
> - `engines/satellite/` - Conditional engines (0-30%)
> - `docs/v2-specs/` - V2.1 specifications
>
> **Tests:** 1333 passed (as of 2026-02-08, V6.6)

---

## V3.0 Hardening — Regime Navigation & Governor Simplification

> **Status:** COMPLETE ✅ (V3.0 + V3.1 + V3.2)
> **Goal:** Fix regime detection lag, simplify Governor, enable defensive trades at all levels
> **Branches:** `feature/va/v3.0-hardening`, `feature/va/v3.2-macro-regime-gate`
> **Audit:** `docs/audits/V3_0_FullFix_2015_v2_audit.md`

### Problem Statement

2015 backtest revealed critical failures:
- **-24.16% return** in a flat market (S&P 500 was ~0% in 2015)
- **266 days (73%)** at Governor 0% — unable to trade
- **Zero hedges deployed** despite 34+ days of `TMF_SIGNAL` generated
- **Regime detected crash 3 days late** (Micro Regime detected Aug 21, Daily Regime detected Aug 24)

### V3.0 Fix List

| ID | Category | Fix | Priority | Status |
|:--:|:--------:|-----|:--------:|:------:|
| V3-1 | EOD_LOCK | Exempt hedges (TMF/PSQ) from EOD_LOCK at Governor 0% | P0 | ✅ DONE |
| V3-2 | EOD_LOCK | Exempt bearish PUT spreads from EOD_LOCK at Governor 0% | P0 | ✅ DONE |
| V3-3 | Regime | Add VIX Direction to daily regime score (detect crashes same-day) | P0 | ✅ DONE |
| V3-3b | Regime | Normalize weights to 100% + clamp VIX Direction (safeguard) | P0 | ✅ DONE |
| V3-4 | Governor | Simplify to 3 tiers: 100% / 50% / 0% (eliminate 75%, 25% limbo) | P1 | ✅ DONE |
| V3-5 | Governor | Allow hedges + PUTs at 0% scale (defensive trades always enabled) | P1 | ✅ DONE |
| V3-6 | Governor | HWM Reset after 10 days at 50%+ with positive P&L | P2 | ✅ DONE |
| V3-7 | Regime | Lower CAUTIOUS threshold from 40 to 45 (with VIX direction may not be needed) | P3 | ⏭️ SKIP |

### V3.1 Fixes (P0 from 2015 Audit)

| ID | Area | Fix | Priority | Status |
|:--:|:----:|-----|:--------:|:------:|
| V3.1-1 | Governor | Exempt hedges (TMF/PSQ) from morning GOVERNOR_SHUTDOWN | P0 | ✅ DONE |
| V3.1-2 | Governor | Add equity recovery path from Governor 0% (3% recovery → 50%) | P0 | ✅ DONE |

### V3.2 Fixes (Options-Macro Regime Alignment)

> **Branch:** `feature/va/v3.2-macro-regime-gate`
> **Commit:** `f5c5ad6`
> **Docs:** `docs/audits/P0_OPTIONS_MACRO_REGIME_GATE_IMPLEMENTATION_PLAN.md`

**Problem:** Options engine had independent regime logic that conflicted with macro regime:
- PROTECTIVE_PUTS existed as enum but returned `None` (never executed)
- Intraday options had no Governor check (gap in protection)
- Options could enter CALLs in NEUTRAL/CAUTIOUS regimes (misaligned with macro thesis)

| ID | Area | Fix | Priority | Status |
|:--:|:----:|-----|:--------:|:------:|
| V3.2-1 | Options | Macro Regime Gate: BULL=all, NEUTRAL=PUT@50%, BEAR=PUT@100% | P0 | ✅ DONE |
| V3.2-2 | Options | Intraday Governor Gate: Block CALL at Governor 0%, allow PUT | P0 | ✅ DONE |
| V3.2-3 | Options | Implement PROTECTIVE_PUTS (was returning None, now buys PUTs in crisis) | P0 | ✅ DONE |
| V3.2-4 | Options | Sizing Stack: Multiply all factors (Governor × Macro × ColdStart × Micro) | P1 | ✅ DONE |

**Config Additions:**
- `OPTIONS_MACRO_REGIME_GATE_ENABLED = True`
- `OPTIONS_NEUTRAL_ZONE_SIZE_MULT = 0.50`
- `OPTIONS_MIN_COMBINED_SIZE_PCT = 0.10`
- `INTRADAY_GOVERNOR_GATE_ENABLED = True`
- `PROTECTIVE_PUTS_ENABLED = True` (with DTE/delta/sizing params)

**Files Changed:** `config.py`, `engines/satellite/options_engine.py`, `main.py`, `tests/test_options_engine.py`

**Tests:** 1340 passed ✅

### V3.3 Fixes (Regime Engine Simplification)

> **Branch:** `feature/va/v3.2-macro-regime-gate`
> **Problem:** 7-factor regime engine caused score compression (43-50 in -30% bear markets)

**Solution:** Simplified to 3-factor model with 2 safety guards:

| ID | Area | Fix | Priority | Status |
|:--:|:----:|-----|:--------:|:------:|
| V3.3-1 | Regime | 3-Factor Core: Trend (35%) + VIX (30%) + Drawdown (35%) | P0 | ✅ DONE |
| V3.3-2 | Regime | VIX Shock Cap: Cap regime at CAUTIOUS when VIX spiking >10% | P0 | ✅ DONE |
| V3.3-3 | Regime | Recovery Hysteresis: Require 2 days confirmation for upgrades | P1 | ✅ DONE |

**Why 3 Factors?**
- **Trend (35%)**: Is market going up or down? (SPY vs MA200)
- **VIX (30%)**: Is there fear/panic?
- **Drawdown (35%)**: How far from 52-week high? (Breaks compression!)

**Guards (not weighted, just safety valves):**
- **VIX Shock Cap**: Fast crash response - caps regime when VIX spiking
- **Recovery Hysteresis**: Prevents "sticky bear" and missing V-recoveries

**Config Additions:**
- `V3_REGIME_SIMPLIFIED_ENABLED = True`
- `WEIGHT_TREND_V3 = 0.35`, `WEIGHT_VIX_V3 = 0.30`, `WEIGHT_DRAWDOWN_V3 = 0.35`
- `DRAWDOWN_THRESHOLD_*` and `DRAWDOWN_SCORE_*` parameters
- `VIX_SHOCK_CAP_ENABLED`, `VIX_SHOCK_CAP_THRESHOLD = 10.0`
- `RECOVERY_HYSTERESIS_ENABLED`, `RECOVERY_HYSTERESIS_DAYS = 2`

**Files Changed:** `config.py`, `engines/core/regime_engine.py`, `utils/calculations.py`

**Tests:** 1340 passed ✅

### V3.4 Fixes (Pre-Live Audit + Options Regime Alignment)

> **Branch:** `feature/va/v3.2-macro-regime-gate`
> **Audit:** `docs/audits/V3_3_pre_live_audit_report.md`

**P0 Fixes (State Persistence):**

| ID | Area | Fix | Priority | Status |
|:--:|:----:|-----|:--------:|:------:|
| V3.4-1 | State | V3.3 Regime state persistence (shock_cap, recovery_days, spy_52w_high) | P0 | ✅ DONE |
| V3.4-2 | State | Options state JSON migration (ast.literal_eval → json.loads) | P0 | ✅ DONE |

**Options Regime Alignment:**

| ID | Area | Fix | Priority | Status |
|:--:|:----:|-----|:--------:|:------:|
| V3.4-3 | Options | Upper NEUTRAL (60-69): CALL @ 50%, PUT @ 25% (lean bullish in recovery) | P1 | ✅ DONE |
| V3.4-4 | Options | CALL exit threshold tightened: 45 → 60 (10-point gap, not 25) | P1 | ✅ DONE |
| V3.4-5 | Options | Neutrality zone separated from exit thresholds (45-65 vs 60-60) | P1 | ✅ DONE |

**Config Additions:**
- `OPTIONS_UPPER_NEUTRAL_THRESHOLD = 60`
- `OPTIONS_UPPER_NEUTRAL_CALL_MULT = 0.50`
- `OPTIONS_UPPER_NEUTRAL_PUT_MULT = 0.25`
- `SPREAD_REGIME_EXIT_BULL = 60` (was 45)
- `SPREAD_NEUTRALITY_ZONE_LOW = 45` (independent of exit thresholds)
- `SPREAD_NEUTRALITY_ZONE_HIGH = 65` (independent of exit thresholds)

**Files Changed:** `config.py`, `engines/core/regime_engine.py`, `engines/satellite/options_engine.py`, `main.py`, `tests/test_options_engine.py`

### V3.5 Fixes (Options Dead Zone Removal)

> **Branch:** `feature/va/v3.2-macro-regime-gate`
> **Audit:** `docs/audits/V3_4_2022H1_audit_report.md`
> **Problem:** 2022 H1 backtest fired ZERO PUT options despite -20% market drop

**Root Cause:** Direction selection in main.py created a 20-point dead zone (regime 50-70) where:
- CALLs blocked (regime < 70)
- PUTs blocked (regime >= 50)
- Result: NO options trades in NEUTRAL zone

| ID | Area | Fix | Priority | Status |
|:--:|:----:|-----|:--------:|:------:|
| V3.5-1 | Options | Dual-direction scanning in Upper NEUTRAL (60-70) | P0 | ✅ DONE |
| V3.5-2 | Options | Refactored to `_scan_spread_for_direction()` helper | P0 | ✅ DONE |
| V3.5-3 | Options | Fixed EOD options gating for NEUTRAL zone | P0 | ✅ DONE |
| V3.5-4 | Options | Fixed governor gate: PUT allowed at low governor in NEUTRAL | P0 | ✅ DONE |
| V3.5-5 | Options | Fixed startup gate check for NEUTRAL direction | P0 | ✅ DONE |

**Fix Logic (Dual-Direction Scanning):**
- **BULL (>70):** Scan CALL only
- **Upper NEUTRAL (60-70):** Scan BOTH directions (CALL @ 50%, PUT @ 25%)
- **Lower NEUTRAL (50-59):** Scan PUT only @ 50%
- **CAUTIOUS/BEAR (<50):** Scan PUT only @ 100%

**Files Changed:** `main.py` (refactored direction logic + new helper function)

**Tests:** 1340 passed ✅

### V3.6 Fixes (Margin Optimization for PUT Entries)

> **Branch:** `feature/va/v3.0-hardening`
> **Audit:** `docs/audits/V3_5_2022H1_Regime_Audit.md`
> **Problem:** 2022 H1 backtest PUT signals blocked by over-conservative margin check

**Root Cause:** Even after V3.5 dead zone fix, PUT signals generated but BLOCKED by margin:
- 6× safety factor required $48K margin for $4 spread × 20 contracts
- 20% equity cushion left only $35K available
- Result: PUT signals fired correctly but couldn't execute

| ID | Area | Fix | Priority | Status |
|:--:|:----:|-----|:--------:|:------:|
| V3.6-1 | Margin | Reduce safety factor from 6× to 4× | P1 | ✅ DONE |
| V3.6-2 | Margin | Add margin-aware sizing (size down vs block) | P1 | ✅ DONE |
| V3.6-3 | Margin | Reduce equity cushion from 20% to 10% | P1 | ✅ DONE |
| V3.6-4 | Margin | Increase OPTIONS_MAX_MARGIN_PCT from 20% to 30% | P1 | ✅ DONE |

**Before:**
```
Required=$48,000 | Effective Free=$35,072 | → BLOCKED
```

**After (V3.6):**
```
- 4× safety: $4 × 100 × 4 = $1,600/contract (was $2,400)
- 10% cushion: ~$39K available (was ~$35K)
- 30% max margin for options: $43K × 30% = $12.9K cap (was 20% = $8.6K)
- If still insufficient: Size down to max contracts that fit (instead of block)
```

**Files Changed:** `main.py` (margin calculation + margin-aware sizing), `config.py`

**Tests:** 1340 passed ✅

### V3.7 Fixes (52-Week High Bug - CRITICAL)

> **Branch:** `feature/va/v3.0-hardening`
> **Problem:** Drawdown factor used 25-day rolling window instead of actual 52-week high

**Root Cause:** `spy_closes` rolling window was only 25 days. When `spy_52w_high` wasn't passed
to regime engine, it fell back to `max(spy_closes)` = max of 25 days, not 252 days.

| ID | Area | Fix | Priority | Status |
|:--:|:----:|-----|:--------:|:------:|
| V3.7-1 | Regime | Add SPY 252-day Maximum indicator for true 52-week high | P0 | ✅ DONE |
| V3.7-2 | Regime | Pass spy_52w_high to regime engine calculate() | P0 | ✅ DONE |

**Impact:** In 2022 bear market, drawdown factor was miscalculating because it measured
distance from 25-day high (not 52-week high). A 15% drawdown from ATH might appear as
only 5% drawdown from recent 25-day high, causing regime to stay bullish/neutral too long.

**Files Changed:** `main.py` (added spy_52w_high indicator + pass to regime engine)

**Tests:** 1340 passed ✅

### V3.8 Fixes (Recovery Hysteresis VIX Threshold)

> **Branch:** `feature/va/v3.0-hardening`
> **Problem:** Recovery hysteresis blocked ALL regime upgrades when VIX > 25

**Root Cause:** The recovery hysteresis guard required VIX < 25 to allow regime upgrades:
```python
if trend_improving and vix_level < 25:  # Too restrictive!
    recovery_days += 1
else:
    recovery_days = 0  # Reset every day when VIX > 25
```

**Impact Analysis:**
- Mar 2020: VIX stayed above 25 for 63 days (Feb 24 - Apr 27)
- Market bottomed Mar 23 and rallied 30% by Apr 27
- System would have stayed RISK_OFF the entire time, missing the V-recovery
- Same issue in 2011 debt ceiling recovery (VIX 28)

| ID | Area | Fix | Priority | Status |
|:--:|:----:|-----|:--------:|:------:|
| V3.8-1 | Regime | Raise RECOVERY_HYSTERESIS_VIX_MAX from 25 to 35 | P0 | ✅ DONE |

**Rationale:** VIX 25-35 is common during V-recoveries. Only block upgrades in true crisis (VIX > 35).

**Files Changed:** `config.py`

**Tests:** 1340 passed ✅

---

## V5.3 Options Engine Overhaul — Conviction-Based Trading

> **Status:** ✅ COMPLETE
> **Goal:** Give VASS & Micro engines autonomy to override Macro when they have conviction
> **Branch:** `feature/va/v3.0-hardening`

### Problem Statement

2022H1 backtest revealed options engine failures:
- **-66% return** with Governor stuck at 0% for 147 days
- **PUT spreads not trading** even when market was falling (Macro scored bullish due to VIX 17-20)
- **VASS not tracking VIX direction** — only level classification (LOW/MEDIUM/HIGH)
- **Too many gates blocking trades** — Governor, Macro Gate, Neutrality Exit all compounding

### Architecture Change

```
BEFORE: Macro → Gates → Options (Macro dictates, options obey)
AFTER:  VASS/Micro → Verify vs Macro → Trade if aligned OR has conviction
```

**Key Principle:** VASS and Micro measure volatility directly. If they have strong conviction (VIX spiking, UVXY extreme), they can veto Macro's direction.

### V5.3 Fix List

| ID | Category | Fix | Priority | Status |
|:--:|:--------:|-----|:--------:|:------:|
| V5.3-1 | Governor | Disable Governor for strategy validation | P0 | ✅ DONE |
| V5.3-2 | Options | Disable Neutrality Exit for testing | P0 | ✅ DONE |
| V5.3-3 | Options | Add position limits: max 1 intraday + 2 swing = 3 total | P0 | ✅ DONE |
| V5.3-4 | VASS | Add VIX 5d/20d SMA tracking | P1 | ✅ DONE |
| V5.3-5 | VASS | Add VIX direction (5d/20d change %) | P1 | ✅ DONE |
| V5.3-6 | VASS | Add VIX level crossing detection (25↑, 15↓) | P1 | ✅ DONE |
| V5.3-7 | VASS | Implement `vass_has_conviction()` | P1 | ✅ DONE |
| V5.3-8 | Micro | Implement `micro_has_conviction()` | P1 | ✅ DONE |
| V5.3-9 | Options | Implement `resolve_trade_signal()` | P1 | ✅ DONE |
| V5.3-10 | Options | Add position counting methods | P1 | ✅ DONE |
| V5.3-11 | VASS | Change HIGH IV from credit to debit spreads | P1 | ✅ DONE |
| V5.3-12 | Macro | V5.3 Regime: 4-factor with VIX Combined (Phase 1 & 2) | P2 | ✅ DONE |

### Config Additions

```python
# Governor & Exit Disables
DRAWDOWN_GOVERNOR_ENABLED = False  # V5.3: Disabled for strategy validation
SPREAD_NEUTRALITY_EXIT_ENABLED = False  # V5.3: Disabled for testing

# Position Limits
OPTIONS_MAX_INTRADAY_POSITIONS = 1
OPTIONS_MAX_SWING_POSITIONS = 2
OPTIONS_MAX_TOTAL_POSITIONS = 3

# VASS Conviction Thresholds
VASS_VIX_5D_PERIOD = 5
VASS_VIX_20D_PERIOD = 20
VASS_VIX_5D_BEARISH_THRESHOLD = 0.20  # +20%
VASS_VIX_5D_BULLISH_THRESHOLD = -0.15  # -15%
VASS_VIX_20D_STRONG_BEARISH = 0.30  # +30%
VASS_VIX_20D_STRONG_BULLISH = -0.20  # -20%
VASS_VIX_FEAR_CROSS_LEVEL = 25
VASS_VIX_COMPLACENT_CROSS_LEVEL = 15

# Micro Conviction Thresholds
MICRO_UVXY_BEARISH_THRESHOLD = 0.08  # +8%
MICRO_UVXY_BULLISH_THRESHOLD = -0.05  # -5%
MICRO_VIX_CRISIS_LEVEL = 35
MICRO_VIX_COMPLACENT_LEVEL = 12
MICRO_BEARISH_STATES = ["FULL_PANIC", "SPIKING", "WORSENING_HIGH"]
MICRO_BULLISH_STATES = ["CRASHING", "STABLE_LOW", "IMPROVING"]
```

### Conviction Logic

**VASS Conviction (Swing 7-45 DTE):**
- VIX 5d change > +20% → BEARISH
- VIX 5d change < -15% → BULLISH
- VIX 20d change > +30% → STRONG BEARISH
- VIX 20d change < -20% → STRONG BULLISH
- VIX crosses above 25 → BEARISH
- VIX crosses below 15 → BULLISH

**Micro Conviction (Intraday 0-5 DTE):**
- UVXY > +8% → BEARISH
- UVXY < -5% → BULLISH
- VIX > 35 → CRISIS (BEARISH)
- VIX < 12 → COMPLACENT (BULLISH)
- Micro state FULL_PANIC/SPIKING → BEARISH
- Micro state CRASHING/STABLE_LOW → BULLISH

### Trade Resolution

```
1. Engine generates signal (BULLISH or BEARISH)
2. Engine checks conviction (has_conviction())
3. Compare to Macro direction
4. Resolution:
   - ALIGNED → TRADE
   - MISALIGNED + CONVICTION → VETO MACRO, TRADE
   - MISALIGNED + NO CONVICTION → NO TRADE
```

### Files Changed

- `config.py` — New thresholds, disabled Governor/Neutrality
- `engines/satellite/options_engine.py` — IVSensor overhaul, conviction methods

### V5.3 Macro Regime Implementation (V5.3-12)

**Status:** ✅ COMPLETE

**4-Factor Model with VIX Combined:**
- Momentum (30%): 20-day ROC - catches reversals in days
- VIX Combined (30%): 60% VIX level + 40% VIX direction
- Trend (25%): SPY vs MA200
- Drawdown (15%): Distance from 52-week high

**Key Features:**
- **VIX Combined Score:** Blends absolute fear level with fear velocity
- **High-VIX Clamp:** VIX Combined capped at 47 when VIX >= 25 (prevents bullish signals during elevated fear)
- **Spike Cap:** Macro score capped at 45 when VIX 5d >= +28%
- **Breadth Decay Penalty (Phase 2):** -5 points for 5d RSP/SPY decay <= -10%, -8 points for 10d decay <= -15%

**Config Additions:**
```python
# V5.3 Master Switch
V53_REGIME_ENABLED = True
V4_REGIME_ENABLED = False  # Disabled - V5.3 is now active

# Factor Weights
WEIGHT_MOMENTUM_V53 = 0.30
WEIGHT_VIX_COMBINED_V53 = 0.30
WEIGHT_TREND_V53 = 0.25
WEIGHT_DRAWDOWN_V53 = 0.15

# VIX Combined
VIX_COMBINED_LEVEL_WEIGHT = 0.60
VIX_COMBINED_DIRECTION_WEIGHT = 0.40
VIX_COMBINED_HIGH_VIX_CLAMP = 47
VIX_COMBINED_HIGH_VIX_THRESHOLD = 25.0

# Spike Cap
V53_SPIKE_CAP_THRESHOLD = 0.28  # +28% VIX 5d triggers cap
V53_SPIKE_CAP_MAX_SCORE = 45

# Breadth Decay Penalty
V53_BREADTH_5D_DECAY_THRESHOLD = -0.10
V53_BREADTH_10D_DECAY_THRESHOLD = -0.15
V53_BREADTH_5D_PENALTY = 5
V53_BREADTH_10D_PENALTY = 8
```

**Files Changed:**
- `config.py` — V5.3 parameters
- `utils/calculations.py` — `vix_combined_score()`, `breadth_decay_penalty()`, `aggregate_regime_score_v53()`
- `engines/core/regime_engine.py` — `_calculate_v53()` method, RegimeState V5.3 fields

### Conviction Logic Wiring (V5.3-13) ✅ COMPLETE

**Swing Spreads (VASS Conviction):**
```python
# In main.py _process_options_chain():
can_swing, swing_reason = self.options_engine.can_enter_swing()
vass_has_conviction, vass_direction, vass_reason = self.options_engine._iv_sensor.has_conviction()
macro_direction = self.options_engine.get_macro_direction(regime_score)
should_trade, resolved_direction, resolve_reason = self.options_engine.resolve_trade_signal(
    engine="VASS", engine_direction=vass_direction, engine_conviction=vass_has_conviction, macro_direction=macro_direction
)
```

**Intraday Options (Micro Conviction):**
```python
# In main.py options intraday block:
can_intraday, intraday_limit_reason = self.options_engine.can_enter_intraday()
micro_has_conviction, micro_direction, micro_reason = self.options_engine._micro_regime_engine.has_conviction(...)
should_trade, resolved_direction, resolve_reason = self.options_engine.resolve_trade_signal(
    engine="MICRO", engine_direction=micro_direction, engine_conviction=micro_has_conviction, macro_direction=macro_direction
)
```

### Next Steps

1. Run 2022H1 backtest to validate bear market capture
2. Run 2015 backtest to validate choppy market handling

---

### Fix Details

#### V3-1 & V3-2: EOD_LOCK Exemption (P0)

**Bug:** `main.py:1514` blocks ALL MOO orders at Governor 0%, including hedges and PUT spreads.

```python
# CURRENT (BROKEN)
if self._governor_scale <= 0.0:
    self.Log("EOD_LOCK: Governor scale = 0, skipping MOO order submission")
    self._save_state()
```

**Evidence:**
```
15:45 HEDGE: TMF_SIGNAL | Regime=46.5, TMF target=10%, current=0%
16:00 EOD_LOCK: Governor scale = 0, skipping MOO order submission  ← BLOCKS HEDGE
```

**Fix:** Exempt hedges and bearish options from EOD_LOCK.

**Files:** `main.py`

---

#### V3-3: VIX Direction in Daily Regime (P0) ✅ DONE

**Bug:** Daily regime only uses VIX level (20% weight), no direction. Micro regime uses VIX Level × Direction and detected crash 3 days earlier.

**Evidence:**
```
Aug 21 10:45 MICRO: WORSENING (UVXY +3.1%)
Aug 21 11:15 MICRO: DETERIORATING (UVXY +11.1%)
Aug 21 15:45 DAILY: Score=60 (NEUTRAL)  ← 3 DAYS LATE
```

**Fix:** Add `vix_direction_score()` that penalizes rising VIX momentum.

**Files:** `config.py`, `utils/calculations.py`, `engines/core/regime_engine.py`

---

#### V3-3b: Weight Normalization + Clamping Safeguard (P0) ✅ DONE

**Bug:** With VIX Direction enabled, regime weights summed to 115% (not normalized).

**Fix:** Normalized weights to 100% with research-based allocation:

| Factor | Old | New | Rationale |
|--------|-----|-----|-----------|
| Trend | 25% | 20% | Lagging indicator |
| VIX Level | 20% | 15% | Implied vol level |
| VIX Direction | 15% | 15% | Leading indicator |
| Breadth | 20% | 15% | Market breadth |
| Credit | 15% | 15% | Leading indicator |
| Chop | 5% | 10% | ADX trend quality |
| Volatility | 15% | 10% | Lagging indicator |
| **Total** | **115%** | **100%** | ✅ Normalized |

**Clamping Safeguard:** VIX Direction score clamped to 25-75 range to prevent single-factor boundary crossings. At 15% weight, this limits max swing to 7.5 points (cannot cross 10-point regime boundaries alone).

**Files:** `config.py`, `engines/core/regime_engine.py`

---

#### V3-4: Simplified Governor Tiers (P1)

**Bug:** 5-tier system (100%/75%/50%/25%/0%) creates oscillation and useless limbo states.

```python
# CURRENT (5 tiers)
DRAWDOWN_GOVERNOR_LEVELS = {
    0.03: 0.75,  # -3% → 75% (barely meaningful)
    0.06: 0.50,  # -6% → 50%
    0.10: 0.25,  # -10% → 25% (can't do anything useful)
    0.15: 0.00,  # -15% → 0%
}

# PROPOSED (3 tiers)
DRAWDOWN_GOVERNOR_LEVELS = {
    0.06: 0.50,  # -6% → 50% (defensive mode)
    0.15: 0.00,  # -15% → 0% (hedges/PUTs only)
}
```

**Files:** `config.py`, `engines/core/risk_engine.py`

---

#### V3-5: Defensive Trades at Governor 0% (P1)

**Design:** At Governor 0%, only BULLISH trades are blocked. Defensive trades always allowed:

| Trade Type | Governor 100% | Governor 50% | Governor 0% |
|------------|:-------------:|:------------:|:-----------:|
| Trend (QLD/SSO/TNA/FAS) | ✅ | ✅ | ❌ |
| Bullish CALL spreads | ✅ | ✅ | ❌ |
| Mean Reversion (TQQQ/SOXL) | ✅ | ✅ | ❌ |
| Hedges (TMF/PSQ) | ✅ | ✅ | ✅ |
| Bearish PUT spreads | ✅ | ✅ | ✅ |
| Yield (SHV) | ✅ | ✅ | ✅ |

**Files:** `main.py`, `config.py`

---

#### V3-6: HWM Reset Mechanism (P2) ✅ DONE

**Bug:** HWM stayed locked at $50,029 for entire 2015. Even 10 REGIME_OVERRIDEs couldn't break the death spiral.

**Fix:** After 10 consecutive days at Governor 50%+ with positive daily P&L, reset HWM to current equity.

```python
GOVERNOR_HWM_RESET_ENABLED = True
GOVERNOR_HWM_RESET_MIN_DAYS = 10  # Changed from 20 to 10
GOVERNOR_HWM_RESET_MIN_SCALE = 0.50
```

**Implementation:**
- Tracks consecutive days with positive P&L at 50%+ scale
- Any negative P&L day resets the counter
- Dropping below 50% scale resets the counter
- When threshold reached, HWM resets to current equity
- State persisted across restarts

**Files:** `config.py`, `engines/core/risk_engine.py`

---

### Implementation Order

```
Phase 1: P0 Fixes (EOD_LOCK + VIX Direction)
  └── V3-1: EOD_LOCK exempt hedges
  └── V3-2: EOD_LOCK exempt PUT spreads
  └── V3-3: VIX Direction in regime

Phase 2: P1 Fixes (Governor Simplification)
  └── V3-4: 3-tier Governor
  └── V3-5: Defensive trades at 0%

Phase 3: P2 Fixes (Recovery)
  └── V3-6: HWM Reset mechanism

Phase 4: Validation
  └── Backtest 2015 with all fixes
  └── Backtest 2017 (bull market)
  └── Backtest 2022 Q1-Q2 (bear market)
```

---

### Prior V3.0 Commits (Already Applied)

| Commit | Description |
|--------|-------------|
| `cdf189f` | fix(imports): remove Phase enum import |
| `b8103a1` | fix(config): add INITIAL_CAPITAL |
| `88951ed` | fix(config): add MAX_SINGLE_POSITION_PCT |
| `1a66f2b` | feat(v3.0): Governor immunity + regime-adaptive ADX |
| `d10e700` | feat(governor): Regime Override jumps to 50% |
| `3ed2875` | fix(kill-switch): handle string symbols in liquidation |

---

## Current Sprint: V2.1.1 Options Engine Redesign

> **Status:** V2.1.1 COMPLETE ✅ — Options Engine Dual-Mode + Micro Regime Engine
>
> See `V2_IMPLEMENTATION_ROADMAP.md` for full roadmap.
> See `docs/v2-specs/V2_1_OPTIONS_ENGINE_DESIGN.txt` for complete specification.

### V2.1.1 Completion Summary

| Phase | Focus | Status |
|-------|-------|--------|
| V2.1 Phase 1 | Trend Engine V2 + 5-Level Circuit Breakers | ✅ Complete |
| V2.1 Phase 2 | Mean Reversion VIX Filter | ✅ Complete |
| V2.1 Phase 3 | Options Engine (4-factor scoring, OCO) | ✅ Complete |
| V2.1 Phase 4 | Integration & Orchestration (Core-Satellite) | ✅ Complete |
| V2.1.1 | Options Engine Redesign (Dual-Mode + Micro Regime) | ✅ Complete |

### V2.1.1 Options Engine Redesign (Jan 28, 2026)

| Component | Description | Status |
|-----------|-------------|--------|
| Dual-Mode Architecture | Swing (15%) + Intraday (5%) modes | ✅ Designed |
| Micro Regime Engine | VIX Level × VIX Direction = 21 regimes | ✅ Designed |
| VIX Direction Classification | 7 categories (FALLING_FAST to WHIPSAW) | ✅ Designed |
| Tiered VIX Monitoring | 5min/15min/30min/60min layers | ✅ Designed |
| VIX1D Evaluation | Rejected (0.95 correlation during trading hours) | ✅ Complete |
| Documentation | V2_1_OPTIONS_ENGINE_DESIGN.txt (2,135 lines) | ✅ Complete |

### Backtest Validation Progress

| Stage | Duration | Purpose | Status | Date |
|:-----:|----------|---------|:------:|------|
| 1 | 1 day | Basic validation | **PASS** ✅ | 2026-01-30 |
| 2 | 1 month (Jan 2025) | Short-term behavior | **V2.3.22 +0.93%** ✅ | 2026-02-01 |
| 2b | 3 months (Q1 2025) | Capital Test | **V2.11 -62%** 🔴 | 2026-02-02 |
| 2c | 3 months (Q1 2025) | AAP Bug Fixes | **V2.12 Complete** ✅ | 2026-02-02 |
| 2d | 1 month (Jan 2025) | V2.18 Architectural Fixes | **Timeout** 🔴 | 2026-02-02 |
| 2e | 1 month (Jan 2025) | V2.19 Execution Patch | **Ready to Run** ⏳ | 2026-02-02 |
| 2f | 1 month (Jan 2025) | V2.20 Rejection Recovery | **Ready to Run** ⏳ | 2026-02-03 |
| 2g | 1 month (Jan 2025) | V2.21 Rejection-Aware Sizing | **Ready to Run** ⏳ | 2026-02-03 |
| 2h | 1 month (Jan 2025) | V2.22 Neutrality Exit | **Ready to Run** ⏳ | 2026-02-03 |
| 2i | 1 month (Jan 2025) | V2.23 VASS Credit Spread Integration | **Ready to Run** ⏳ | 2026-02-03 |
| 2j | 1 month (Jan 2025) | V2.24 Zero-Trade Diagnostic Fixes | **Ready to Run** ⏳ | 2026-02-03 |
| 2k | 1 month (Jan 2025) | V2.24.1 Hardening (Price Discovery + Elastic Delta) | **Ready to Run** ⏳ | 2026-02-03 |
| 2l | 1 month (Jan 2025) | V2.24.2 Runtime Error + DTE Double-Filter + Push Fix | **Ready to Run** ⏳ | 2026-02-03 |
| 2m | 1 year (2015) | V2.26 Drawdown Governor + Chop Detector | **Ready to Run** ⏳ | 2026-02-03 |
| 2n | 1 year (2015) | V2.27 Graduated KS + Win Rate Gate | **Ready to Run** ⏳ | 2026-02-03 |
| 2o | 1 year (2015) | V2.28.1 Tightened Governor | **-9.07%, -9.3% DD** ✅ | 2026-02-04 |
| 2p | 1 year (2015) | V2.29 Strike Force (Ghost Flush + Dynamic Recovery + StartupGate) | **Ready to Run** ⏳ | 2026-02-04 |
| 2q | 1 year (2015) | V2.30 All-Weather StartupGate + Bearish Options Path Fix | **Ready to Run** ⏳ | 2026-02-04 |
| 3 | 3 months | Position lifecycle | Pending | — |
| 4 | 1 year | Full annual cycle | Pending | — |
| 5 | 5 years | Long-term stress test | Pending | — |

> **Results Document:** `docs/audits/backtest-results.md`
> **Stage 2 Code Audits:** `docs/audits/stage2-codeaudit.md`, `docs/audits/stage2-codeaudit2.md`
> **Logs:** `docs/audits/logs/stage2/`

---

### Master Bug List (All Identified)

> **Complete Bug Registry** - Updated 2026-02-02
>
> Status reflects actual fixes in codebase (commits `178d55b`, `fc44648`)

| ID | Category | Bug | Severity | Status | Target |
|:--:|:--------:|-----|:--------:|:------:|:------:|
| USER-1 | Kill Switch | Sequential spread liquidation creates naked exposure | 🔴 CRITICAL | ✅ FIXED | V2.17-BT |
| USER-2 | Config | Delta Wall (0.30 target vs 0.40 min) | 🔴 CRITICAL | ✅ FIXED | V2.15 |
| USER-3 | Architecture | Kill switch bypasses ExecutionEngine/Router | 🔴 CRITICAL | ✅ FIXED | V2.17-BT |
| USER-4 | State | Swing position expiry not checked on restore | 🟡 HIGH | ✅ FIXED | V2.16-BT |
| RPT-1 | Kill Switch | No preemptive trigger at -4% (gap to -5%) | 🟡 HIGH | ✅ FIXED | V2.16-BT |
| RPT-2 | Config | Slippage threshold mismatch (2% vs 10%) | 🔴 CRITICAL | Open | V2.16-PROD |
| RPT-3 | State | Spread position not persisted to ObjectStore | 🔴 CRITICAL | ✅ FIXED | V2.16-BT |
| RPT-4 | State | clear_all_positions() uses wrong variables | 🔴 CRITICAL | ✅ FIXED | V2.16-BT |
| RPT-5 | Timing | No market close timing guard (15:58-16:00) | 🔴 CRITICAL | ✅ FIXED | V2.18 |
| RPT-6 | Margin | No pre-check before orders | 🟡 HIGH | ✅ FIXED | V2.18 |
| RPT-7 | Dead Code | Circuit Breaker Level 4 never triggers | 🟢 LOW | Open | V2.17 |
| RPT-8 | Profit | Profit target ignores commission costs | 🟡 HIGH | ✅ FIXED | V2.16-BT |
| RPT-9 | Fallback | ComboMarketOrder has no retry before fallback | 🟡 HIGH | ✅ FIXED | V2.17-BT |
| RPT-10 | Greeks | Same thresholds for all strategies | 🟡 HIGH | Partial | V2.15 |
| RPT-11 | Scheduler | GetPreviousMarketClose attribute error | 🟢 LOW | Open | V2.17 |
| RPT-12 | Logging | Duplicate log messages per bar | 🟢 LOW | Open | V2.17 |
| RPT-13 | Config | Magic numbers in time checks | 🟢 LOW | Open | V2.17 |

**Summary:**
- ✅ **12 FIXED** (USER-1, USER-2, USER-3, USER-4, RPT-1, RPT-3, RPT-4, RPT-5, RPT-6, RPT-8, RPT-9, RPT-10 partial)
- 🔴 **1 CRITICAL Open** (RPT-2 - slippage threshold)
- 🟢 **4 LOW Open** (RPT-7, RPT-11, RPT-12, RPT-13)

---

### V2.16-BT: Backtest State Persistence Fixes (2026-02-02) — ALL COMPLETE ✅

**Goal:** Fix 5 critical bugs affecting multi-day backtest accuracy.

| # | Fix | Priority | Status |
|:-:|-----|:--------:|:------:|
| 1 | **clear_all_positions()** - Add _swing_position clearing | P0 | ✅ |
| 2 | **Swing expiry check** - ZOMBIE_CLEAR on expired positions | P0 | ✅ |
| 3 | **Spread persistence** - Save/restore _spread_position | P0 | ✅ |
| 4 | **Kill switch -4% preemptive** - Hedge exposure gap | P1 | ✅ |
| 5 | **Commission-aware profit** - Net profit meets target | P1 | ✅ |

**P0 Fixes (State Persistence):**
1. `clear_all_positions()` now clears `_swing_position` (was missing)
2. `restore_state()` validates expiry for legacy, swing, and spread positions
3. `_spread_position` persisted to ObjectStore for multi-day backtests
4. Defensive coding for tests where `_algorithm` not initialized

**P1 Fixes (Risk & Profit):**
4. **Preemptive Kill Switch:** When panic mode active AND loss >= 4.5%, trigger kill switch
   - Closes gap between panic mode (4%) and kill switch (5%)
   - Config: `KILL_SWITCH_PREEMPTIVE_PCT = 0.045`
5. **Commission-Aware Profit Target:** Gross P&L required = target + commission
   - Ensures NET profit after commission meets target (not just gross)
   - Config: `SPREAD_COMMISSION_PER_CONTRACT = $2.60`

**Files Modified:**
- `engines/satellite/options_engine.py` - P0 state fixes + P1 commission fix
- `engines/core/risk_engine.py` - P1 preemptive kill switch
- `config.py` - New config values
- `tests/test_options_engine.py` - Updated fixture expiry dates

**Commits:**
- `178d55b` - P0 fixes (state persistence, zombie clearing)
- `fc44648` - P1 fixes (preemptive kill switch, commission-aware profit)

**Tests:** 211 passed, 6 failed (pre-existing StopTier failures)

---

### V2.17-BT: Atomic Spread Exit & Kill Switch Coordination (2026-02-02) — COMPLETE ✅

**Goal:** Fix 3 interconnected bugs affecting spread close reliability and kill switch coordination.

| # | Fix | Bug ID | Priority | Status |
|:-:|-----|:------:|:--------:|:------:|
| 1 | **ComboMarketOrder retry** - 3 attempts before fallback | RPT-9 | P0 | ✅ |
| 2 | **Sequential close order** - SHORT first to prevent naked exposure | USER-1 | P0 | ✅ |
| 3 | **Kill switch routing** - Use Router instead of direct broker calls | USER-3 | P0 | ✅ |

**Root Cause:** Kill switch in `main.py` directly called `self.ComboMarketOrder()` and `self.Liquidate()`, bypassing Router's coordination. When combo orders failed, there was no retry, and sequential fallback could create naked short exposure.

**Solution:** Unified `execute_spread_close()` method in Router with:
- 3-attempt retry loop for `ComboMarketOrder`
- Margin-safe sequential fallback (SHORT first, then LONG)
- Lock management with `is_closing` flag

**Files Modified:**
- `config.py` - Added `COMBO_ORDER_MAX_RETRIES`, `COMBO_ORDER_FALLBACK_TO_SEQUENTIAL`, `SPREAD_LOCK_CLEAR_ON_FAILURE`
- `portfolio/portfolio_router.py` - Added `execute_spread_close()`, `_try_combo_close()`, `_execute_sequential_close()`
- `main.py` - Kill switch now calls `portfolio_router.execute_spread_close()`
- `engines/satellite/options_engine.py` - Added `reset_spread_closing_lock()`

**Backtest Validation:** V2.17-AtomicSpreadFix
- **Result:** +0.93% | **Equity:** $50,463.48 | **Orders:** 42
- **URL:** https://www.quantconnect.com/project/27678023/c9cc8ea6c2993fb47dbb6a08dd870e42

**Tests:** 174 passed, 6 failed (pre-existing StopTier failures)

---

### V2.18: Consolidated Audit Fixes (2026-02-02) — COMPLETE ✅

**Goal:** Fix 8 architectural and performance issues identified in V2.17 AAP audit.

**Executive Summary:** V2.17 backtest achieved +0.93% return, but masked serious issues:
- Trend Engine violated position limits (3 positions when max=2)
- Options Engine starved (only 2 intraday signals vs 49 expected)
- Leverage overflow possible (196% margin when all Trend tickers fire)

| # | Fix | Priority | File | Status |
|:-:|-----|:--------:|------|:------:|
| 1 | **Position Limit Enforcement** - Check BEFORE MOO submission | CRITICAL | `main.py` | ✅ |
| 2 | **Capital Firewall 50/50** - Hard partition Trend=50%, Options=50% | CRITICAL | `portfolio_router.py`, `config.py` | ✅ |
| 3 | **Leverage Cap 90%** - Block entries if margin > 90% | CRITICAL | `portfolio_router.py` | ✅ |
| 4 | **RPT-5: Market Close Guard** - Block orders 15:58-16:00 ET | CRITICAL | `main.py` | ✅ |
| 5 | **RPT-6: Margin Pre-Check** - Verify margin before order submission | CRITICAL | `portfolio_router.py` | ✅ |
| 6 | **Enable Intraday Signals** - Add MICRO_REGIME: log prefix for tracking | HIGH | `options_engine.py` | ✅ |
| 7 | **Reduce Trend Allocations** - QLD 20%→15%, TNA 12%→8%, SSO 15%→12%, FAS 8%→5% | HIGH | `config.py` | ✅ |
| 8 | **Hardcoded Sizing Caps** - SWING=$7,500, INTRADAY=$4,000 (absolute caps) | HIGH | `config.py`, `options_engine.py` | ✅ |

**Config Changes (V2.18):**
```python
# Capital Partition (50/50 hard firewall)
CAPITAL_PARTITION_TREND = 0.50    # Was 55%
CAPITAL_PARTITION_OPTIONS = 0.50  # Was 25%

# Leverage Cap
MAX_MARGIN_WEIGHTED_ALLOCATION = 0.90  # Never exceed 90%

# Trend Allocations (40% total, was 55%)
TREND_SYMBOL_ALLOCATIONS = {
    "QLD": 0.15,  # Was 0.20
    "SSO": 0.12,  # Was 0.15
    "TNA": 0.08,  # Was 0.12
    "FAS": 0.05,  # Was 0.08
}

# Hardcoded Sizing Caps (replaces MarginBuyingPower-based sizing)
SWING_SPREAD_MAX_DOLLARS = 7500
INTRADAY_SPREAD_MAX_DOLLARS = 4000
```

**Files Modified:**
- `main.py` - Position limit check before MOO, market close blackout
- `config.py` - Capital partition, leverage cap, reduced allocations, sizing caps
- `portfolio/portfolio_router.py` - `get_trend_capital()`, `get_options_capital()`, `check_leverage_cap()`, `verify_margin_available()`
- `engines/satellite/options_engine.py` - MICRO_REGIME logging, hardcoded sizing caps

**Tests:** 1304 passed, 2 skipped (updated tests for new config values)

**Backtest Result:** V2.18/V2.18.1/V2.18.2 all hit 10-minute timeout on single time loop
- Root cause: Excessive logging (fixed in V2.18.1)
- Ghost margin bug discovered (fixed in V2.18.2)

---

### V2.19: Emergency Execution Patch (2026-02-02) — COMPLETE ✅

**Goal:** Bundle 3 critical execution layer fixes before next backtest.

**Executive Summary:** V2.18 backtests revealed execution layer issues, not strategy failures:
1. **Ghost Margin Bug** - Intraday trades blocked after margin CB (FIXED in V2.18.2)
2. **Market Order Slippage** - Filled at crazy Ask/Bid prices on illiquid options
3. **VIX Apathy Trap** - DEBIT_FADE firing in low VIX (<13.5) where mean reversion fails

| # | Fix | Priority | Status |
|:-:|-----|:--------:|:------:|
| 1 | **Ghost Margin Fix** - Clear router margin reservations on CB | CRITICAL | ✅ V2.18.2 |
| 2 | **Limit Order Logic** - Marketable limits with 5% slippage tolerance | CRITICAL | ✅ |
| 3 | **VIX Filter** - Block DEBIT_FADE when VIX < 13.5 | HIGH | ✅ |
| 4 | **20K Loop Fix** - Stop iterating all 20K+ options in Securities/Portfolio | CRITICAL | ✅ |

**Fix 1: Ghost Margin (COMPLETE - V2.18.2)**

**Root Cause:** `clear_spread_position()` cleared OptionsEngine state but NOT the Router's margin reservation. Result: "Ghost" reservations blocked all future intraday trades.

**Solution:** Added `clear_all_spread_margins()` to PortfolioRouter, called from main.py during margin CB.

**Commit:** `4640182`

**Fix 2: Limit Order Logic (COMPLETE)**

**Problem:** Market orders on illiquid options filled at crazy prices (Short at Ask, Long at Bid = immediate loss).

**Solution:** Marketable limit orders with slippage tolerance.

**Config:**
```python
OPTIONS_USE_LIMIT_ORDERS = True           # Enable limit orders for options
OPTIONS_LIMIT_SLIPPAGE_PCT = 0.05         # 5% of spread (ensures fills)
OPTIONS_MAX_SPREAD_PCT = 0.20             # Block if spread > 20% of mid
```

**New Methods in `portfolio_router.py`:**
- `validate_options_spread()` - Bad tick guard + illiquidity check
- `calculate_limit_price()` - Ask + 5% slippage for BUY, Bid - 5% for SELL
- `execute_options_limit_order()` - Unified limit order execution

**Fix 3: VIX Filter (COMPLETE)**

**Problem:** DEBIT_FADE fires in low VIX (<13.5) "apathy" markets where mean reversion fails.

**Solution:** Block DEBIT_FADE when VIX < 13.5.

**Config:**
```python
INTRADAY_DEBIT_FADE_VIX_MIN = 13.5  # Disable in "apathy" market
```

**Options Engine Change:** Added VIX floor check in `recommend_strategy_and_direction()` before returning DEBIT_FADE.

**Fix 4: 20K Loop Performance Bug (COMPLETE)**

**Root Cause:** V2.18.1/V2.18.2 backtests timed out with "Algorithm took longer than 10 minutes on a single time loop". The issue was loops iterating through ALL 20,000+ options contracts in `self.Securities` or `self.Portfolio.Keys`.

**Offending Code (4 locations in main.py):**
```python
# Line 1736: Iterates ALL 20K+ securities during margin CB
for symbol in self.Portfolio.Keys:
    if "QQQ" in symbol_str...  # ALL 20K options have "QQQ"!

# Lines 1788, 1816: Creates 20K+ element list for membership check
if symbol in [str(s) for s in self.Securities.Keys]:

# Line 4633: Iterates ALL securities for price lookup
for kvp in self.Securities:
```

**Solution:** Iterate `Portfolio.Values` with `Invested` check first:
```python
# Fixed: Only iterate holdings we actually have (typically <10)
for holding in self.Portfolio.Values:
    if not holding.Invested:
        continue  # Skip immediately - O(1) vs O(20K)
```

**Files Modified:**
- `config.py` - Limit order config + VIX floor
- `portfolio/portfolio_router.py` - Limit order methods + spread validation
- `engines/satellite/options_engine.py` - VIX filter check
- `main.py` - 20K loop performance fixes (4 locations)

**Tests:** 1304 passed, 2 skipped

**V2.19 Six Critical Bugs (2026-02-02):**

| # | Fix | Priority | Status |
|:-:|-----|:--------:|:------:|
| 1 | **Swing scan throttle 30 min** - Spread construction scans every minute | HIGH | ✅ |
| 2 | **Daily reset for swing scan timer** - `_last_swing_scan_time` not cleared at EOD | HIGH | ✅ |
| 3 | **Margin sizing min()** - Subtraction could yield negative allocation | CRITICAL | ✅ |
| 4 | **Options entry score guard** - Missing guard on `_pending_entry_score` access | HIGH | ✅ |
| 5 | **Trend pending MOO after approval** - Mark pending before router approval | CRITICAL | ✅ |
| 6 | **20K loop timeout** - Iterating all 20K+ options in Portfolio.Keys | CRITICAL | ✅ |

**Commits:**
- `f8292fe` - Execution patch: limit orders + VIX filter
- `fd327e7` - 20K loop timeout fix
- `4df0918` - Trend pending MOO fix
- `d40e475` - Margin sizing min() fix
- `ad8306d` - Six critical bugs blocking options and trend entries

**Tests:** 1304 passed, 2 skipped

**Next Step:** Run V2.19 comprehensive backtest
```bash
./scripts/qc_backtest.sh "V2.19-ExecutionPatch" --open
```

---

### V2.20: Event-Driven State Recovery (Rejection Listener) (2026-02-03) — COMPLETE ✅

**Goal:** Prevent "zombie pending locks" when broker rejects/cancels orders. Without this, internal pending flags remain set forever, blocking future entries.

**Problem:** When broker rejects an order, `OnOrderEvent` notifies the `ExecutionEngine` but NOT the originating strategy engine. Internal pending locks remain set:
- **Trend**: `_pending_moo_symbols` — slot consumed permanently
- **Cold Start**: `_warm_entry_executed = True` — blocks all warm entries
- **Mean Reversion**: `_pending_vix_regime` / `_pending_stop_pct` — stale VIX data
- **Options Swing**: `_pending_contract`, `_entry_attempted_today` — blocks swing entries
- **Options Spread**: `_pending_spread_long_leg`, etc. — blocks spreads, leaks ghost margin
- **Options Intraday**: `_pending_intraday_entry`, pre-incremented counter — wastes trade slot

**Architecture:** Centralized `_handle_order_rejection(symbol, order_event)` in `main.py` routes rejection events to the correct engine using symbol-based matching, called from both `OrderStatus.Invalid` and `OrderStatus.Canceled` branches in `OnOrderEvent`.

| # | Component | Description | Status |
|:-:|-----------|-------------|:------:|
| 1 | **Engine Cancel Methods** | `cancel_warm_entry()`, `cancel_pending_entry()`, 3× options cancel methods | ✅ |
| 2 | **Central Rejection Handler** | `_handle_order_rejection()` with symbol-based routing | ✅ |
| 3 | **Scoped Cooldowns** | Per-strategy time penalties (Trend 18h, MR 15m, Swing/Spread 30m, Intraday 15m) | ✅ |
| 4 | **Gatekeeper Checks** | Cooldown validation at all scanner entry points | ✅ |
| 5 | **Ghost Code Fix** | `clear_all_positions()` had wrong field names (`_pending_spread_long` → `_pending_spread_long_leg`) | ✅ |
| 6 | **Unit Tests** | 14 new tests across 4 engine test files | ✅ |
| 7 | **Scenario Tests** | 5 end-to-end rejection recovery tests | ✅ |

**Routing Logic:**
```
QLD/SSO/TNA/FAS → Trend cancel_pending_moo + Cold Start cancel_warm_entry (both if)
TQQQ/SOXL       → MR cancel_pending_entry
QQQ options      → Options (spread > intraday > swing by pending state priority)
```

**Scoped Cooldowns (prevents infinite retry loops):**
| Strategy | Cooldown | Gatekeeper |
|----------|----------|------------|
| Trend | 18 hours (next EOD) | Empties `entry_candidates` |
| MR | 15 min | Early `return` in scanner |
| Options Intraday | 15 min | Blocks scan condition |
| Options Spread | 30 min | Blocks spread entry path |
| Options Swing | 30 min | Blocks swing fallback path |

**Pitfall Audit (3/3 addressed):**
1. ✅ **Infinite Loop Trap** — Scoped cooldowns prevent immediate retry
2. ✅ **Order Routing Ambiguity** — Symbol sets mutually exclusive; dual routing for Trend+ColdStart uses sequential `if`
3. ✅ **Partial Spread State Cleanup** — All 9 fields + `_entry_attempted_today` + ghost margin cleared

**Files Modified:**
- `engines/core/cold_start_engine.py` - Added `cancel_warm_entry()`
- `engines/satellite/mean_reversion_engine.py` - Added `cancel_pending_entry()`
- `engines/satellite/options_engine.py` - Added 3 cancel methods + ghost code fix in `clear_all_positions()`
- `main.py` - `_handle_order_rejection()`, cooldown variables, gatekeeper checks, OnOrderEvent wiring
- `tests/test_cold_start_engine.py` - 3 new tests
- `tests/test_options_engine.py` - 7 new tests + `clear_all_positions` test fix
- `tests/test_trend_engine.py` - 2 new tests
- `tests/test_mean_reversion_engine.py` - 2 new tests
- `tests/scenarios/test_rejection_recovery_scenario.py` - 5 new scenario tests (NEW file)

**Commit:** `4a12785` - `feat(recovery): V2.20 event-driven state recovery for broker rejections`

**Tests:** 1323 passed, 2 skipped (19 new tests)

**Next Step:** Run V2.20 backtest
```bash
./scripts/qc_backtest.sh "V2.20-RejectionRecovery" --open
```

---

### V2.21: Rejection-Aware Spread Sizing (2026-02-03) — COMPLETE ✅

**Goal:** Eliminate repeated "Insufficient buying power" rejections by adding margin-aware sizing to MACRO spread entries.

**Problem:** V2.20 enables same-day retry after spread rejection (clears `_entry_attempted_today`, 30-min cooldown), but retries with the **same position size**. The broker rejects again because capital hasn't changed. V2.5 evidence: 74 INVALID orders over 2 years, ALL "Insufficient buying power" — same spread retried 3–5 consecutive days at the same failing size. Zero cases where identical sizing succeeded on retry.

**Root Cause:** `check_spread_entry_signal()` sizes to `SWING_SPREAD_MAX_DOLLARS = $7,500` regardless of available margin. The broker's margin requirement (for the naked short leg) far exceeds $7,500, but the engine never checks this before submitting.

**Architecture: Two Layers**

| Layer | Type | Mechanism |
|:-----:|------|-----------|
| 1 | **Proactive** | Pre-submission margin estimation scales `num_spreads` down to fit `margin_remaining × 0.80` |
| 2 | **Reactive** | Parses `Free Margin: XXXXX` from broker rejection message, stores adaptive cap for next retry |

**Key Behaviors:**
- When margin too tight for `MIN_SPREAD_CONTRACTS` (2): returns `None` **without** setting `_entry_attempted_today` → retry preserved when capital frees up mid-day
- Layer 2 cap: `min(live_margin, rejection_cap)` — broker's actual number constrains next attempt
- Cap clears on successful fill or daily reset

| # | Component | Description | Status |
|:-:|-----------|-------------|:------:|
| 1 | **Config Parameters** | `SPREAD_MARGIN_SAFETY_FACTOR`, `SPREAD_REJECTION_MARGIN_SAFETY` (both 0.80) | ✅ |
| 2 | **State Field** | `_rejection_margin_cap` on options engine | ✅ |
| 3 | **Margin-Aware Sizing** | Pre-submission estimation in `check_spread_entry_signal()` | ✅ |
| 4 | **Rejection Parser** | `_parse_and_store_rejection_margin()` extracts broker Free Margin | ✅ |
| 5 | **Wiring** | `margin_remaining` passed at both call sites + parser wired into handler | ✅ |
| 6 | **Cap Lifecycle** | Cleared in `register_spread_entry()` (fill) + `reset_daily()` | ✅ |
| 7 | **Unit Tests** | 7 new tests in `TestRejectionAwareSizing` | ✅ |

**Files Modified:**
- `config.py` — 2 new parameters (`SPREAD_MARGIN_SAFETY_FACTOR`, `SPREAD_REJECTION_MARGIN_SAFETY`)
- `engines/satellite/options_engine.py` — `_rejection_margin_cap` field + margin-aware sizing block + cap clearing
- `main.py` — `margin_remaining` at both call sites + `_parse_and_store_rejection_margin()` + handler wiring
- `tests/test_options_engine.py` — 7 new tests

**Commit:** `56c3dbb` - `feat(options): V2.21 rejection-aware spread sizing with margin estimation`

**Tests:** 1330 passed, 2 skipped (7 new tests)

**Next Step:** Run V2.21 backtest
```bash
./scripts/qc_backtest.sh "V2.21-RejectionAwareSizing" --open
```

---

### V2.22: Symmetric Neutrality Exit (Hysteresis Shield) (2026-02-03) — COMPLETE ✅

**Goal:** Close flat spreads trapped in the regime dead zone (45–60) to free capital and stop theta bleed.

**Problem:** When a spread drifts into the neutral dead zone, no exit trigger fires:
- Bear Put entered at regime 40, regime climbs to 55 → no regime reversal exit (needs > 60)
- Bull Call entered at regime 65, regime drops to 50 → no regime reversal exit (needs < 45)
- Spread sits idle bleeding theta with no directional edge

**Solution:** New Exit 4 in `check_spread_exit_signals()` — fires when:
1. Regime in dead zone: `45 ≤ regime ≤ 60`
2. P&L is flat: `±10%` (configurable)

**P&L Filter Behavior:**
| P&L Range | Action | Rationale |
|-----------|--------|-----------|
| > +10% | **Hold** | Let profit target manage the winner |
| -10% to +10% | **Exit** | Dead money — no edge, theta bleeding |
| < -10% | **Hold** | Let stop loss manage the loser |

| # | Component | Description | Status |
|:-:|-----------|-------------|:------:|
| 1 | **Config Parameters** | `SPREAD_NEUTRALITY_EXIT_ENABLED`, `SPREAD_NEUTRALITY_EXIT_PNL_BAND` (0.10) | ✅ |
| 2 | **Credit Branch** | Neutrality exit after DTE exit, before regime reversal | ✅ |
| 3 | **Debit Branch** | Neutrality exit after DTE exit, before regime reversal | ✅ |
| 4 | **Unit Tests** | 7 new tests in `TestNeutralityExit` | ✅ |

**Files Modified:**
- `config.py` — 2 new parameters
- `engines/satellite/options_engine.py` — Neutrality exit in both credit and debit branches + docstring update
- `tests/test_options_engine.py` — 7 new tests

**Commit:** `e81039e` - `feat(options): V2.22 neutrality exit — close flat spreads in regime dead zone`

**Tests:** 1337 passed, 2 skipped (7 new tests)

**Next Step:** Run V2.22 backtest
```bash
./scripts/qc_backtest.sh "V2.22-NeutralityExit" --open
```

---

### V2.23: VASS Credit Spread Integration + Strike/DTE Fix (2026-02-03) — COMPLETE ✅

**Problem:** Entire credit spread infrastructure was built but orphaned — zero callers from `main.py`:
- `_select_strategy()` — VASS matrix (strategy + DTE range) never called
- `select_credit_spread_legs()` — full credit leg selection never called
- `_calculate_credit_spread_size()` — margin-based sizing never called
- `IVSensor.classify()` — VIX smoothing + LOW/MEDIUM/HIGH classification never used
- Strike filter `(-8, 5)` too narrow for credit spread OTM short legs
- `OPTIONS_DTE_MAX = 45` too short for VASS Low IV monthly expirations (30-45 DTE)

**Solution:** Three surgical changes:

| # | Change | Detail | Status |
|---|--------|--------|:------:|
| 1 | **Strike filter widened** | `(-8, 5)` → `(-25, 25)` in SetFilter | ✅ |
| 2 | **DTE horizon extended** | `OPTIONS_DTE_MAX`: 45 → 60 | ✅ |
| 3 | **VASS routing wired** | `_route_vass_strategy()` → credit/debit branch in both call sites | ✅ |
| 4 | **Credit entry method** | New `check_credit_spread_entry_signal()` with margin-based sizing | ✅ |
| 5 | **DTE params** | `_build_spread_candidate_contracts()` accepts VASS DTE overrides | ✅ |
| 6 | **Unit Tests** | 12 new tests in `TestVASSCreditSpreadEntry` | ✅ |

**VASS Strategy Matrix (now active):**

| Direction | Low IV (VIX<15) | Medium IV (15-25) | High IV (VIX>25) |
|-----------|-----------------|-------------------|-------------------|
| Bullish | Bull Call Debit (30-45 DTE) | Bull Call Debit (7-21 DTE) | **Bull Put Credit (7-14 DTE)** |
| Bearish | Bear Put Debit (30-45 DTE) | Bear Put Debit (7-21 DTE) | **Bear Call Credit (7-14 DTE)** |

**Files Modified:**
- `config.py` — `OPTIONS_DTE_MAX` 45 → 60
- `main.py` — SetFilter widened, `_route_vass_strategy()` helper, VASS routing in both call sites, `SpreadStrategy` import
- `engines/satellite/options_engine.py` — New `check_credit_spread_entry_signal()` (~200 lines)
- `tests/test_options_engine.py` — 12 new tests
- `tests/integration/test_options_flow.py` — Updated DTE assertion

**Commit:** `a5918e5` - `feat(options): V2.23 VASS credit spread integration + strike/DTE fix`

**Tests:** 1349 passed, 2 skipped (12 new tests)

**Next Step:** ~~Run V2.23 backtest~~ → Completed APVP audit first (see below)

### V2.23.1: APVP Audit Fixes (2026-02-03) — COMPLETE ✅

**Trigger:** Ran Algorithmic Pre-Backtest Validation Protocol (`docs/audits/algoauditprotocol-prebacktest.md`).

**CRITICAL finding:** Credit spread `check_credit_spread_entry_signal()` used reversed TargetWeight convention from router — primary symbol was short leg with negative quantity, causing router's `requested_quantity > 0` check to fail and combo order to malform (BUY short_leg + SELL short_leg = same symbol).

**Fixes:**
1. **Credit TargetWeight convention** — Long leg (protection) as primary symbol, positive `requested_quantity` (matches debit convention)
2. **VIX threshold extraction** — Hardcoded `VIX < 22` → `config.VIX_LEVEL_ELEVATED_MAX = 22.0`
3. **Test assertions** — Verify long leg as primary, positive quantity

**Files changed:**
- `engines/satellite/options_engine.py` — Fixed TargetWeight + VIX threshold
- `config.py` — Added `VIX_LEVEL_ELEVATED_MAX = 22.0`
- `tests/test_options_engine.py` — Added convention assertions
- `docs/audits/V2_23_audit_report.md` — Full APVP audit report

**Commit:** `d6bcf1d` - `fix(options): V2.23.1 APVP audit — credit spread router convention + VIX extraction`

**Tests:** 1349 passed, 2 skipped

**Next Step:** ~~Run V2.23.1 backtest~~ → Superseded by V2.24

---

### V2.24: Zero-Trade Diagnostic Fixes (2026-02-03) — COMPLETE ✅

**Problem:** Backtests produced zero options trades despite full options infrastructure. Three root causes identified:

| # | Root Cause | Fix |
|---|-----------|-----|
| 1 | **Router price failsafe skip** — Options symbols missing from `current_prices` dict caused immediate SKIP, even when `metadata['contract_price']` existed | Added 2-layer price fallback: try `current_prices` → try `metadata['contract_price']` → SKIP |
| 2 | **Spread filter silent rejections** — `select_spread_legs()` and `select_credit_spread_legs()` returned `None` with no diagnostic logging when DTE/delta/bid filters eliminated all candidates | Added per-stage rejection counters and diagnostic logging (DTE, delta, width, bid filters) |
| 3 | **Pending MOO ghost signals** — `_pending_moo_dates` dict accumulated stale entries that blocked new MOO submissions for symbols already invested | Added timestamp tracking, cleanup on fill/cancel/expiry, and invested-but-pending detection |

**Files Modified:**
- `main.py` — V2.19 price injection for OPT signals, 30→15 min throttle, pending MOO timestamps
- `portfolio/portfolio_router.py` — 2-layer price fallback in `calculate_order_intents()`
- `engines/satellite/options_engine.py` — Spread filter diagnostic logging with rejection counters
- `engines/core/trend_engine.py` — `_pending_moo_dates` cleanup in all code paths

**Commits:**
- `4df0918` - `fix(trend): V2.19 position limit enforcement bug - mark pending MOO only after approval`
- `fd327e7` - `perf(main): V2.19 fix 20K loop timeout - iterate Portfolio.Values not Keys`
- `f8292fe` - `fix(execution): V2.19 emergency execution patch - limit orders + VIX filter`

**Tests:** 1349 passed, 2 skipped

---

### V2.24.1: V2.23.2 Hardening — Price Discovery + Elastic Delta Bands (2026-02-03) — COMPLETE ✅

**Purpose:** Implement two remaining items from the "Zoom Checklist" V2.23.2 Hardening audit. Third item (Broadened Universe Filter) was already fully implemented.

#### Item 1: Hybrid Price Discovery — 3rd Fallback (Bid/Ask Mid-Price)

**Problem:** When both `current_prices` and `metadata['contract_price']` fail, router SKIPs the order. No attempt to use QC Securities Bid/Ask data.

**Solution:** Added `_try_bid_ask_mid_price()` method to `portfolio_router.py` as 3rd fallback layer:

```
Price Discovery Chain (V2.24.1):
1. current_prices[symbol] (held options + equities)
2. metadata['contract_price'] (from options engine chain data)
3. (bid + ask) / 2 from Securities  ← NEW
4. SKIP (only if all three fail)
```

**Files:**
- `portfolio/portfolio_router.py` — New `_try_bid_ask_mid_price()` method + integration in `calculate_order_intents()`

#### Item 2: Elastic Delta Bands — Progressive Widening

**Problem:** All delta filtering used fixed ranges with no retry. When zero contracts matched, a 4-hour cooldown was set. No progressive widening.

**Solution:** Wrapped candidate selection in retry loops with progressive widening steps:

| Config | Value | Purpose |
|--------|-------|---------|
| `ELASTIC_DELTA_STEPS` | `[0.0, 0.03, 0.07, 0.12]` | Progressive widening increments |
| `ELASTIC_DELTA_FLOOR` | `0.10` | Minimum delta (prevent near-zero delta) |
| `ELASTIC_DELTA_CEILING` | `0.95` | Maximum delta (prevent deep ITM) |

Applied to 3 paths:
1. **Debit long leg** (`select_spread_legs()`) — delta 0.50-0.85 base
2. **Credit BULL_PUT short leg** (`select_credit_spread_legs()`) — delta 0.25-0.40 base, credit minimum enforced inside loop
3. **Credit BEAR_CALL short leg** (`select_credit_spread_legs()`) — delta 0.25-0.40 base, credit minimum enforced inside loop

**Files:**
- `config.py` — Added `ELASTIC_DELTA_STEPS`, `ELASTIC_DELTA_FLOOR`, `ELASTIC_DELTA_CEILING`
- `engines/satellite/options_engine.py` — Elastic loops in all 3 selection paths

#### Item 3: Broadened Universe Filter — Already Implemented ✅

Confirmed: `SetFilter(-25, 25, 0, 60)` — exceeds spec requirement of ±20 strikes, 0-60 DTE.

**Commit:** `d2d2f73` — V2.24.1 changes

**Tests:** 1349 passed, 2 skipped

#### Production Readiness Note

**Stale Metadata Risk** identified during architecture review — not a bug in backtest (same-bar processing) but a risk in live trading where signal metadata prices could be seconds/minutes old. Documented in `docs/guides/production-readiness.md` for pre-live implementation.

**Next Step:** ~~Run V2.24.1 backtest~~ → Superseded by V2.24.2

---

### V2.24.2: Runtime Error + DTE Double-Filter + Push 413 Fix (2026-02-03) — COMPLETE ✅

**Trigger:** V2.24.1 backtest showed zero options trades and a runtime error:
```
Runtime Error: TypeError at _process_immediate_signals → self.Log(... trades_only=False)
```

**Three critical bugs found and fixed:**

| # | Bug | Root Cause | Fix |
|---|-----|-----------|-----|
| 1 | **Runtime crash** | `self.Log(msg, trades_only=False)` — QC native `Log()` doesn't accept kwargs. Crashed before price injection could run. | Removed `trades_only` param from 2 `self.Log()` calls in main.py |
| 2 | **DTE double-filter** | `select_spread_legs()` re-applied `SPREAD_DTE_MIN=14` after `_build_spread_candidate_contracts()` already filtered to VASS range (7-21 DTE for MEDIUM IV). All 7-13 DTE contracts silently rejected. Elastic delta bands fired on empty list. | Added `dte_min`/`dte_max` params to `select_spread_legs()` and `check_spread_entry_signal()`. Callers pass VASS-aware DTE bounds. |
| 3 | **Push 413 error** | Total project 848KB exceeded QC HTTP POST body limit. Script silently swallowed error (`2>/dev/null`), so backtests ran on stale code. | Added `minify_workspace.py` (strips comments/docstrings, 848KB→529KB). Updated script to detect 413 errors and clean `__pycache__`. |

**Files Modified:**
- `main.py` — Removed `trades_only=False` from 2 `self.Log()` calls, pass `vass_dte_min`/`vass_dte_max` to spread leg selection and entry signal
- `engines/satellite/options_engine.py` — Added `dte_min`/`dte_max` params to `select_spread_legs()` and `check_spread_entry_signal()`, use effective DTE bounds in filter and validation
- `scripts/qc_backtest.sh` — Clean `__pycache__`, run minifier, detect 413 errors
- `scripts/minify_workspace.py` — New: strips comments/docstrings from lean workspace

**Commit:** `cfa99a2` - `fix(options): V2.24.2 three critical bugs — runtime error, DTE double-filter, push 413`

**Tests:** 1349 passed, 2 skipped

**Next Step:** Run V2.24.2 backtest
```bash
./scripts/qc_backtest.sh "V2.24.2-DTE-Fix" --open
```

---

### V2.27: Strategy Quality — Graduated KS + Win Rate Gate (2026-02-03) — COMPLETE ✅

**Source:** `docs/V2_26_IMPLEMENTATION_PLAN.md` Phase 2 — Capital Preservation & Strategy Quality
**Branch:** `testing/va/stage2-backtest`

**Goal:** Self-correcting options throttle. Fewer kill switch cascades. Spreads survive soft KS tiers.

| # | Fix | Priority | Status |
|:-:|-----|:--------:|:------:|
| 3 | **KS_DECOUPLE** — Spreads survive Tier 1 (REDUCE) and Tier 2 (TREND_EXIT). Only Tier 3 (FULL_EXIT) forces spread closure. -50% spread stop already in place from V2.4.2. | P1 | ✅ |
| 4 | **WIN_RATE_GATE** — Rolling 10-trade win rate tracker for spreads. >40% = full, 30-40% = 75% sizing, 20-30% = 50% sizing, <20% = shutoff with paper tracking. Resume at 35% paper win rate. Applied to both debit and credit spread entry paths. | P1 | ✅ |
| 5 | **GRADUATED_KS** — Replace binary -5% kill switch with 3 tiers: -3% REDUCE (halve trend, block new options), -5% TREND_EXIT (liquidate trend, keep spreads), -8% FULL_EXIT (liquidate everything). Escalation-only within day. Skip-day enforcement after Tier 2+. | P0 | ✅ |

**Files Modified:**
- `engines/core/risk_engine.py` — KSTier enum, `check_kill_switch_graduated()`, tiered `check_all()`, skip-day persistence
- `engines/core/__init__.py` — Added `KSTier` to exports
- `main.py` — Rewrote `_handle_kill_switch()` for tiers, `_ks_close_all_options()` decouple helper, skip-day gating in `_scan_options_signals`, `_generate_options_signals`, `_generate_trend_signals_eod`
- `engines/satellite/options_engine.py` — `record_spread_result()`, `get_win_rate_scale()`, paper tracking, win rate gate in `check_spread_entry_signal()` + `check_credit_spread_entry_signal()`, sizing scale in both debit and credit paths, persistence
- `config.py` — 19 new params (KS_GRADUATED_ENABLED, KS_TIER_1/2/3_PCT, WIN_RATE_* params, etc.)
- `tests/test_risk_engine.py` — Updated 8 tests for graduated thresholds + KSTier import
- `tests/scenarios/test_kill_switch_scenario.py` — Updated boundary test for 3% Tier 1

**Tests:** All 1349 passed ✅

**Backtest Validation:**

| Period | Version | Return | Sharpe | Max DD | Status |
|--------|---------|-------:|-------:|-------:|:------:|
| 2015 Full Year | V2.27 | | | | ⏳ Pending |

---

### V2.29: "Strike Force" — Ghost Spread Flush + Dynamic Recovery + StartupGate (2026-02-04) — COMPLETE ✅

**Source:** `docs/audits/V2_28_1_2015_FullYear_audit_report.md` — Forensic audit of 2015 full-year backtest.
**Branch:** `testing/va/stage2-backtest`

**Root Cause Analysis:** V2.28.1 2015 backtest revealed 3 critical issues:
1. Ghost spread state persisted for 12 months (43,291 SPREAD_EXIT_WARNING logs)
2. Governor recovery trap — 50% allocation for 358 days (12% threshold unreachable)
3. Cold Start oversizing — 25% SSO + 10.8% spread risk in first 5 days

| # | Fix | Priority | Status |
|:-:|-----|:--------:|:------:|
| P0 | **Ghost Spread State Flush** — 3-layer reconciliation (OnOrderEvent, portfolio check, Friday safety net) | P0 | ✅ |
| P1 | **Dynamic Governor Recovery** — `effective_recovery = base × scale` (8% base). At 50% governor → 4% threshold. Config: `DRAWDOWN_GOVERNOR_RECOVERY_BASE = 0.08` | P1 | ✅ |
| P2+P3 | **StartupGate** — One-time arming sequence (REGIME_GATE → OBSERVATION → REDUCED → FULLY_ARMED). Separate from ColdStart, never resets on kill switch. | P2 | ✅ |
| — | **State Cleanup Audit** — Fixed 2 gaps: governor shutdown missing `cancel_pending_spread_entry()`, kill switch `clear_all_positions()` missing 6 fields | P0 | ✅ |

**P0: Ghost Spread State Flush (3 layers):**
- **Layer A:** `OnOrderEvent` — Clear spread state after both legs fill
- **Layer B:** `OnOrderEvent` — Portfolio-based check after any option fill (neither leg held → clear)
- **Layer C:** `_on_friday_firewall()` → `_reconcile_spread_state()` — Weekly safety net

**P1: Dynamic Governor Recovery:**
```
effective_recovery = DRAWDOWN_GOVERNOR_RECOVERY_BASE × governor_scale
At 100% scale → 8% recovery needed
At 50% scale → 4% recovery needed (was 12% flat — unreachable)
At 25% scale → 2% recovery needed
```

**P2+P3: StartupGate (NEW CLASS: `engines/core/startup_gate.py`):**
```
Launch → REGIME_GATE (100% cash, wait 10 consecutive days regime > 60)
       → OBSERVATION (10 days, no trades)
       → REDUCED (10 days, 10% max per position)
       → FULLY_ARMED (permanent, no restrictions)
```
- Independent from ColdStartEngine (ColdStart unchanged)
- Persisted via StateManager (key: `ALPHA_NEXTGEN_STARTUP_GATE`)
- Config: 6 new `STARTUP_GATE_*` params

**Files Modified:**
- `main.py` — P0 (3 reconciliation layers), P2+P3 (init, EOD gate, warm entry block, intraday block, persistence)
- `config.py` — P1 (`DRAWDOWN_GOVERNOR_RECOVERY_BASE`), P2+P3 (6 new `STARTUP_GATE_*` params)
- `engines/core/risk_engine.py` — P1 (dynamic recovery formula)
- `engines/core/startup_gate.py` — **NEW FILE** (StartupGate class)
- `engines/__init__.py` — StartupGate re-export
- `persistence/state_manager.py` — StartupGate save/load, `save_all`/`load_all` updated
- `engines/satellite/options_engine.py` — `clear_all_positions()` completeness fix (6 missing fields)

**Files NOT Modified:**
- `engines/core/cold_start_engine.py` — ZERO changes (by design)

**Tests:** 1344 passed, 1 failed (pre-existing KS threshold test from V2.28.1)

---

### V2.30: "All-Weather" — StartupGate Redesign + Bearish Options Path Fix (2026-02-04) — COMPLETE ✅

**Source:** V2.29 backtest analysis — StartupGate regime dependency blocked hedges/bearish options during bear markets, defeating the purpose of an "all-weather" system.
**Branch:** `testing/va/stage2-backtest`

**Root Cause Analysis:** V2.29 StartupGate had 3 design problems:
1. REGIME_GATE required 10 consecutive regime > 60 days — impossible in bear markets, blocking ALL engines including hedges
2. OBSERVATION/REDUCED phases blocked ALL engines — hedges should never be gated
3. Options outer `if regime >= 40` gate blocked PUT spreads in regime < 40, limiting bearish window to only 40-44

| # | Fix | Priority | Status |
|:-:|-----|:--------:|:------:|
| P0 | **StartupGate Rewrite** — Time-based phases: INDICATOR_WARMUP (5d) → OBSERVATION (5d) → REDUCED (5d) → FULLY_ARMED. 15 days total, no regime dependency. | P0 | ✅ |
| P1 | **Granular Engine Gating** — `allows_hedges()` (always), `allows_yield()` (always), `allows_bearish_options()` (OBSERVATION+), `allows_directional_longs()` (REDUCED+) | P1 | ✅ |
| P2 | **Bearish Options Path Fix** — Removed outer `regime >= 40` gate. New `_generate_options_signals_gated()` routes by direction. Bearish needs `allows_bearish_options()`, bullish needs `allows_directional_longs()`. | P2 | ✅ |
| P3 | **State Cleanup** — `clear_all_positions()` now clears 16 fields (was 13). Added: `_pending_stop_price`, `_pending_target_price`, `_pending_intraday_exit` | P3 | ✅ |

**Design Principle:** "The gate controls HOW MUCH capital to deploy. The regime controls WHAT to deploy it in."

**StartupGate V2.30 Phases:**
```
Launch → INDICATOR_WARMUP (5 days, hedges + yield only)
       → OBSERVATION (5 days, hedges + yield + bearish options at 50%)
       → REDUCED (5 days, all engines at 50%)
       → FULLY_ARMED (permanent, no restrictions)
```

**Config Changes:**
- Removed: `STARTUP_GATE_REGIME_DAYS`, `STARTUP_GATE_REGIME_MIN_SCORE`, `STARTUP_GATE_REDUCED_MAX_WEIGHT`
- Added: `STARTUP_GATE_WARMUP_DAYS = 5`, `STARTUP_GATE_REDUCED_SIZE_MULT = 0.50`
- Changed: `STARTUP_GATE_OBSERVATION_DAYS` 10→5, `STARTUP_GATE_REDUCED_DAYS` 10→5

**Files Modified:**
- `engines/core/startup_gate.py` — Complete rewrite: time-based phases, granular API
- `main.py` — EOD: hedges always run, direction-aware options gating. Intraday: `_scan_options_signals_gated()`
- `config.py` — 3 params removed, 2 added, 2 changed
- `engines/satellite/options_engine.py` — `clear_all_positions()` +3 fields (16 total)
- `persistence/state_manager.py` — State shape: `{phase, days_in_phase}`, backward compat

**Persistence:** State key `ALPHA_NEXTGEN_STARTUP_GATE` unchanged. Shape changed to `{phase, days_in_phase}`. `restore_state()` maps `REGIME_GATE` → `INDICATOR_WARMUP`, reads `arming_days` as fallback for `days_in_phase`.

---

### V2.28: Recovery & Insurance — Recovery Acceleration + Penny Steamroller Guard — ON HOLD ⏸️

**Source:** `docs/V2_26_IMPLEMENTATION_PLAN.md` Phase 3 — Recovery & Insurance
**Branch:** `testing/va/stage2-backtest`
**Status:** Deferred — Phase 1 (V2.26) and Phase 2 (V2.27) complete. Phase 3 parked pending backtest validation of earlier phases.

**Goal:** Faster recovery from crashes and insurance against future credit spread issues.

| # | Fix | Priority | Status |
|:-:|-----|:--------:|:------:|
| 6 | **RECOVERY_ACCELERATION** — Fast-track cold start exit when regime > 65 AND VIX < 25. Cuts cold start from 5 days to 2. File: `engines/core/cold_start_engine.py` | P2 | ⏸️ On Hold |
| 7 | **PENNY_STEAMROLLER_GUARD** — Block credit spreads below VIX 18. Insurance against selling cheap premium. File: `engines/satellite/options_engine.py`, `config.py` | P2 | ⏸️ On Hold |

---

### V2.26: Capital Preservation — Drawdown Governor + Chop Detector (2026-02-03) — COMPLETE ✅

**Source:** `docs/V2_26_IMPLEMENTATION_PLAN.md` Phase 1 — Capital Preservation & Strategy Quality
**Branch:** `testing/va/stage2-backtest`

**Goal:** Cumulative drawdown protection + chop market detection. Prevents death by 1000 cuts.

| # | Fix | Priority | Status |
|:-:|-----|:--------:|:------:|
| 1 | **DRAWDOWN_GOVERNOR** — Cumulative high-watermark based drawdown protection. Scales all engine allocations: 10% DD → 50%, 15% DD → 25%, 20% DD → 0% (shutdown). Hysteresis recovery (+5% from trough to step back up). Persisted via StateManager. | P0 | ✅ |
| 2 | **CHOP_DETECTOR** — ADX(14) of SPY as 6th regime factor (5% weight). Strong trend (ADX≥25) = 100, moderate (20-25) = 60, weak (15-20) = 30, dead (<15) = 10. Lowers regime score in choppy markets, increasing hedge targets. | P1 | ✅ |

**Files Modified:**
- `engines/core/risk_engine.py` — `check_drawdown_governor()`, `_equity_high_watermark`, `_governor_scale`, hysteresis recovery, persistence
- `engines/core/regime_engine.py` — `spy_adx` parameter, `chop_score` + `spy_adx_value` in RegimeState, `chop_factor_score()` wiring
- `utils/calculations.py` — `chop_factor_score()`, updated `aggregate_regime_score()` with chop weight
- `config.py` — 8 new params (DRAWDOWN_GOVERNOR_LEVELS, WEIGHT_CHOP, CHOP_ADX_THRESHOLD_*, ADX_PERIOD)
- `main.py` — SPY ADX indicator, governor call at market open, governor_scale passthrough to engines
- `tests/` — Updated regime engine tests for chop factor

**Tests:** All 1349 passed ✅

**Backtest Validation:**

| Period | Version | Return | Sharpe | Max DD | Status |
|--------|---------|-------:|-------:|-------:|:------:|
| 2015 Full Year | V2.26 | | | | ⏳ Pending |

---

### V2.25 AAP Audit Bug Fixes (2026-02-03)

**Source:** `docs/audits/V2_24_2_Q1_2022_audit_report.md` — Forensic AAP audit of Q1 2022 backtest.

**Root Cause:** Q1 2022 backtest revealed 5 critical bugs:
- Exercise handler was dead code (elif after if Filled:) → -$330K phantom QQQ position
- 116 VASS credit spread rejections → zero credit spread fills in entire Q1
- Intraday EOD double-sells → orphan short positions get exercised

| # | Fix | Priority | Status |
|:-:|-----|:--------:|:------:|
| 1 | **EXERCISE_HANDLER** - Move exercise detection inside `if Filled:` block (was unreachable `elif`). Check for both "exercise" and "assignment" + `OrderType.OptionExercise`. | P0 | ✅ |
| 2 | **ASSIGNMENT_SAFETY_NET** - Daily QQQ equity scan in `_check_expiration_hammer_v2` at 14:00. Liquidates stale assignment shares if Fix #1 misses. | P0 | ✅ |
| 3 | **IV_ADAPTIVE_CREDIT_FLOOR** - Lower min credit from $0.30 to $0.20 when VIX > 30. Applied to both BULL_PUT and BEAR_CALL credit spread paths. | P1 | ✅ |
| 4 | **INTRADAY_DOUBLE_SELL_GUARD** - Check `Portfolio[symbol].Invested` before emitting force-close. Prevents orphan shorts from double-selling already-closed positions. | P1 | ✅ |
| 5 | **WORKBOARD** - Document V2.25 fixes and backtest validation. | P2 | ✅ |

**Files Modified:**
- `main.py` — Fixes 1, 2, 4 (OnOrderEvent, _check_expiration_hammer_v2, _on_intraday_options_force_close)
- `engines/satellite/options_engine.py` — Fix 3 (select_credit_spread_legs × 2 paths)
- `config.py` — Fix 3 (`CREDIT_SPREAD_MIN_CREDIT_HIGH_IV`, `CREDIT_SPREAD_HIGH_IV_VIX_THRESHOLD`)

**Backtest Validation:**

| Period | Version | Return | Sharpe | Max DD | Status |
|--------|---------|-------:|-------:|-------:|:------:|
| Q1 2022 | V2.25 | | | | ⏳ Pending |

**Next Step:** Run V2.25 backtest to verify fixes
```bash
./scripts/qc_backtest.sh "V2.25-AuditFixes" --open
```

---

### V2.12 AAP Audit Bug Fixes (2026-02-02) — COMPLETE ✅

**Root Cause:** V2.11 3-month backtest showed -62% loss due to **exit signal bug** (not strategy failure):
- Exit signals fired without checking if position existed
- Each "exit" OPENED new positions in reverse direction
- Position accumulated from 16 to 80 contracts (5× intended)

**All 8 Fixes Implemented:**

| # | Fix | Priority | Status |
|:-:|-----|:--------:|:------:|
| 1 | **SPREAD_EXIT_POSITION_CHECK** - Check `num_spreads > 0` before exit | P0 | ✅ |
| 2 | **SPREAD_EXIT_LOCK** - `is_closing` flag prevents duplicates | P0 | ✅ |
| 3 | **SPREAD_MAX_POSITION** - Hard cap at 20 contracts | P0 | ✅ |
| 4 | **MARGIN_GUARD_INCREASE** - Raised from $5K to $10K | P1 | ✅ |
| 5 | **MARGIN_CB_LIQUIDATE** - Circuit breaker now liquidates | P1 | ✅ |
| 6 | **COMBO_ORDER_DIRECTION** - Fixed by #1/#2 position check | P1 | ✅ |
| 7 | **PUT_UNIVERSE** - Widened filter `-8, +5`, handle missing Greeks | P1 | ✅ |
| 8 | **SCHEDULER_ERROR** - Simple weekday check (not GetPreviousMarketClose) | P2 | ✅ |

**Files Modified:**
- `engines/satellite/options_engine.py` - Fixes #1, #2, #5
- `config.py` - Fixes #3, #4
- `main.py` - Fixes #5, #7, #8

**Next Step:** Run V2.12 backtest to validate fixes
```bash
./scripts/qc_backtest.sh "V2.12-AllFixes" --open
```

---

### V2.3.22 Backtest Results (2026-02-01) — PROFITABLE RUN ✅

**Backtest:** V2.3.22-Jan2025-1month | **Result:** +0.93% | **Orders:** 57 | **Win Rate:** 53%

| Metric | Value | Notes |
|--------|------:|-------|
| Return | +0.93% | Profitable despite 3 kill switch events |
| Equity | $50,000 → ~$50,465 | |
| Orders | 57 | |
| Win Rate | 53% | 14 wins / 27 trades |

**URL:** https://www.quantconnect.com/project/27678023/4dc08006d60f2b25e04d7f7df6f59691

**P&L Breakdown:**
| Component | P&L | Notes |
|-----------|----:|-------|
| SNIPER (Intraday) | +$4,770 | Best performer - Jan 16-17 rally captured |
| SHV (Yield) | +$117 | 9 wins, all profitable |
| QLD/SSO (Trend) | +$327 | Solid swing trades |
| SWING (Options) | -$1,985 | 3 trades, mostly losses |
| TNA (Trend) | -$954 | Underperforming |
| Cold Start Bug | -$2,553 | Duplicate QLD orders (FIXED in V2.3.23) |

**Bugs Found:** See V2.3.23 fixes below.

### V2.3.23 FIX: Cold Start Duplicate Orders (2026-02-01)

| # | Finding | Severity | Status |
|:-:|---------|:--------:|:------:|
| 1 | **Cold Start duplicate warm entry orders** | CRITICAL | ✅ FIXED |

**Root Cause:** On Jan 17 (Friday), kill switch reset cold start. Jan 18-20 (weekend + MLK holiday), `check_warm_entry()` was called each day. Since `_warm_entry_executed` was False (waiting for `confirm_warm_entry()` which never came) and `has_leveraged_position` was False (MOO orders pending, not filled), 4 separate QLD MOO orders were queued. All filled on Jan 21 = 922 shares instead of 193.

**Impact:** -$2,553 excess loss from 4× position sizing.

**Fix:** Set `_warm_entry_executed = True` immediately when generating the signal (in `check_warm_entry()`), not waiting for fill confirmation. This prevents duplicate signals during weekends/holidays.

**Commit:** `05140be` - `fix(cold-start): prevent duplicate warm entry orders on weekends/holidays`

### V2.3.24 FIX: Hard Margin Reservation + Bug Fixes (2026-02-01)

| # | Bug | Severity | Fix | Status |
|:-:|-----|:--------:|-----|:------:|
| 1 | **Combo orders rejected (INSUFFICIENT_MARGIN)** | P1 | Hard margin reservation + contract scaling | ✅ FIXED |
| 2 | **SHV margin lock** | P1 | Pre-check `MarginRemaining` before SHV sell | ✅ FIXED |
| 3 | **Swing delta too restrictive (0.55-0.85)** | P1 | Widened to 0.50-0.85 | ✅ FIXED |
| 4 | **Intraday signal spam (44 rejections)** | P2 | Lower threshold $500 + log throttle | ✅ FIXED |

**Root Cause Analysis:**

The fundamental issue was **Allocation Reservation ≠ Margin Reservation**:
- Config says reserve 25% for options (`RESERVED_OPTIONS_PCT = 0.25`)
- But leveraged ETFs consume MORE margin than their allocation:
  - 55% trend allocation × 2.4× avg leverage = ~132% margin consumed
- Result: Options got rejected with `Order=$24K > Margin=$2.5K`

**V2.3.24 Fixes:**

1. **Hard Margin Reservation** (`portfolio_router.py`):
   - Added `SYMBOL_LEVERAGE` config for margin calculation
   - `_enforce_source_limits()` now calculates margin-weighted allocation
   - Non-options scaled down based on actual margin consumption, not just weight

2. **Combo Contract Scaling** (`portfolio_router.py`):
   - When combo order exceeds margin, scale contracts to fit
   - Minimum 2 contracts (`MIN_SPREAD_CONTRACTS`) or skip trade

3. **SHV Margin Lock Check** (`portfolio_router.py`):
   - Before SHV sell, check if `shv_sell_amount > MarginRemaining`
   - If locked, skip liquidation (would fail anyway at broker)

4. **Config Changes** (`config.py`):
   - `MIN_INTRADAY_OPTIONS_TRADE_VALUE = 500` (lower threshold for options)
   - `SPREAD_LONG_LEG_DELTA_MIN = 0.50` (was 0.55)
   - `REJECTION_LOG_THROTTLE_MINUTES = 15`
   - `SYMBOL_LEVERAGE = {...}` for margin calculation
   - `MIN_SPREAD_CONTRACTS = 2`

**Expected Impact:**
- Options can now enter despite trend consuming leverage margin
- Combo spreads scale to available margin instead of being rejected
- SHV margin lock errors reduced (skipped early instead of failing at broker)
- Log spam reduced from 44 rejections to ~3 per day

**Commit:** `b14c6dc` - `fix(router): V2.3.24 - hard margin reservation + 4 bug fixes`

**Tests:** 1349 passed, 2 skipped

### V2.4: Structural Trend - SMA50 + Hard Stop (2026-02-01)

**Goal:** Replace Chandelier trailing stops with simpler SMA50 structural trend exit.

| Feature | Description | Status |
|---------|-------------|:------:|
| SMA50 Exit Logic | Exit when close < SMA50 * (1 - 2%) | ✅ Implemented |
| Hard Stop | Asset-specific: 15% (2×), 12% (3×) | ✅ Implemented |
| Backward Compatible | `TREND_USE_SMA50_EXIT = True/False` switch | ✅ Implemented |
| SMA50 Indicators | Added for QLD, SSO, TNA, FAS | ✅ Implemented |

**Benefits:**
- Allows 3% minor volatility without exit (if above SMA50)
- Longer holding periods (30-90 days vs 5-15 days)
- Cleaner logic than tiered ATR multipliers

**Config:**
```python
TREND_USE_SMA50_EXIT = True
TREND_SMA_PERIOD = 50
TREND_SMA_EXIT_BUFFER = 0.02  # 2%
TREND_HARD_STOP_PCT = {"QLD": 0.15, "SSO": 0.15, "TNA": 0.12, "FAS": 0.12}
```

**Files Changed:**
- `config.py` - SMA50 config, hard stop percentages
- `main.py` - SMA50 indicators, pass to trend engine
- `trend_engine.py` - `_check_sma50_exit()`, `_check_chandelier_exit()`

**Validation Criteria:**
- [ ] QLD NOT sold during 3% drops if above SMA50
- [ ] Hard stops trigger at 12%/15% thresholds
- [ ] No whipsaws in choppy markets
- [ ] Holding periods extend to 30+ days

### V2.4 Backtest Results (2026-02-01) — AAP Audit

**Backtest:** V2.4-SMA50-2month (Jan-Feb 2025) | **Result:** -17.98% | **Options P&L:** -$7,814 (87% of loss)

**AAP Audit Findings:** See `docs/audits/backtest-results.md` § V2.4.1 Consolidated Fix List

### V2.4.1: Options Engine P1 Fixes (2026-02-01)

**Goal:** Fix 5 P1 bugs causing -$7,814 options loss in V2.4 backtest.

| # | Fix | File | Description | Status |
|:-:|-----|------|-------------|:------:|
| 1 | Intraday counter race | `options_engine.py` | Counter increments on signal, not fill | ✅ |
| 2 | Intraday scan throttle | `main.py` | 15-min throttle (was 95 scans/hour) | ✅ |
| 3 | Wrong `target_weight` | `options_engine.py` | Use config allocations (was 1.0/0.5) | ✅ |
| 4 | SHV hard cash reserve | `yield_sleeve.py`, `config.py` | 10% hard cash never deployed to SHV | ✅ |
| 5 | UVXY proxy in scanning | `main.py` | Use live UVXY proxy (was stale daily VIX) | ✅ |

**Key Changes:**

1. **Counter Race Fix:** `_intraday_trades_today` incremented immediately on signal generation (line 2417), removed duplicate increment in `register_entry()`.

2. **Scan Throttle:** Added `_should_scan_intraday()` helper with 15-min throttle. Reduces 95 scans/hour → 4 scans/hour.

3. **Target Weight Fix:**
   - Swing: `config.OPTIONS_SWING_ALLOCATION` (0.1875) instead of `1.0`
   - Intraday: `config.OPTIONS_INTRADAY_ALLOCATION * size_mult` instead of `size_mult`

4. **SHV Hard Cash Reserve:** Added `OPTIONS_HARD_CASH_RESERVE_PCT = 0.10` in config. Yield sleeve now reserves 20% total (10% cash buffer + 10% options reserve). Options always have 10% portfolio as actual cash.

5. **UVXY Proxy:** Added `_get_vix_intraday_proxy()` helper. Scanning loop now uses UVXY-derived VIX instead of stale daily close.

**Commit:** `9a7d99d` - `fix(options): V2.4.1 P1 fixes for options engine reliability`

**Tests:** 1349 passed, 2 skipped

### V2.4.1: P2 Fixes (2026-02-01)

| # | Fix | File | Description | Status |
|:-:|-----|------|-------------|:------:|
| 6 | Combo order format | `portfolio_router.py`, `execution_engine.py` | Leg.Create takes RATIO, not quantity | ✅ |
| 7 | Naked call fallback | `options_engine.py` | Disable SWING_FALLBACK to naked ITM CALL | ⏳ |
| 8 | Kill switch on fills | `main.py` | Check kill switch after options fill | ✅ |

**Fix #6 Details:** `Leg.Create(symbol, ratio)` - ratio is 1/-1 for standard spreads, NOT absolute contract count. Old bug: passing quantity (26) as ratio caused 26×26=676 contracts!

**Fix #8 Details:** In `OnOrderEvent`, after options BUY fill, check `risk_engine.is_kill_switch_active()`. If active, immediately liquidate the new position.

### V2.4.2: AAP Audit Comprehensive Fixes (2026-02-02)

**Goal:** Fix all bugs identified in AAP (Algorithmic Audit Protocol) analysis of Dancing Green Bison backtest.

| # | Fix | File(s) | Description | Status |
|:-:|-----|---------|-------------|:------:|
| 1 | Kill switch double-trade | `main.py` | BUY-to-close was triggering re-SELL | ✅ |
| 2 | Kill switch margin order | `main.py` | Close SHORT options before LONG | ✅ |
| 3 | Trend ADX threshold | `trend_engine.py` | Require score ≥ 0.75 (ADX ≥ 25) | ✅ |
| 4 | Spread stop-loss | `options_engine.py`, `config.py` | Exit at 50% loss of entry debit | ✅ |
| 5 | Stop tier contracts | `config.py` | Reduced from 23-34 to 8-15 | ✅ |
| 6 | Expiration Hammer | `config.py` | Force close at 2:00 PM (was 3:45 PM) | ✅ |
| 7 | Trend MOC timing | `trend_engine.py`, `portfolio_router.py` | Same-day close (was next-day open) | ✅ |

**Key Changes:**

1. **Kill Switch Double-Trade:** `OnOrderEvent` now checks if BUY fill is opening (new long) vs closing (covering short). Only liquidates opening trades.

2. **Kill Switch Margin Order:** Options liquidation now closes SHORT options first, then LONG, avoiding QC margin calculation bugs.

3. **Trend ADX Threshold:** Changed from `score < 0.50` to `score < 0.75`. Now requires ADX ≥ 25 (was allowing 15-24).

4. **Spread Stop-Loss:** Added `SPREAD_STOP_LOSS_PCT = 0.50`. Spreads exit if they lose 50% of entry debit. Prevents holding to expiration.

5. **Stop Tier Contracts:** `OPTIONS_STOP_TIERS` contracts reduced:
   - 3.00: 34 → 15
   - 3.25: 31 → 12
   - 3.50: 27 → 10
   - 3.75: 23 → 8

6. **Expiration Hammer:** Force close moved from 3:45 PM to 2:00 PM. Gives 2-hour buffer for retries.

7. **Trend MOC Timing:** Added `Urgency.MOC` and `OrderType.MOC`. Trend entries now use `MarketOnCloseOrder` (same day) instead of `MarketOnOpenOrder` (next day).

**Commits:**
- `0f1d7a5` - Kill switch double-trade and margin bugs
- `c58e35e` - ADX threshold, spread stop-loss, stop tier contracts
- `30a095a` - Expiration Hammer + Trend MOC timing

**Tests:** 1348 passed, 2 skipped

### V2.4.3: Options Engine Critical Fixes (2026-02-02)

**Goal:** Fix 5 critical bugs causing spread construction failures and position sizing issues.

| # | Fix | File(s) | Description | Status |
|:-:|-----|---------|-------------|:------:|
| 1 | Inverted contract sizing | `config.py` | High confidence → MORE contracts (was inverted) | ✅ |
| 2 | Width-based short leg | `options_engine.py`, `config.py` | Use strike width ($5) instead of delta | ✅ ⚠️ |
| 3 | Spread failure cooldown | `options_engine.py`, `config.py` | 4-hour penalty after failed construction | ✅ |
| 4 | Hard cash reserve | `config.py` | 25% never deployed to SHV (was 10%) | ✅ |
| 5 | DTE filter order | `options_engine.py` | Filter DTE BEFORE delta selection | ✅ |

**Key Changes:**

1. **Inverted Contract Sizing (CRITICAL):** `OPTIONS_STOP_TIERS` was backwards. High confidence (3.75) should bet MORE, not less:
   - 3.00: 5 contracts (low confidence = small bet)
   - 3.25: 8 contracts
   - 3.50: 10 contracts
   - 3.75: 12 contracts (high confidence = biggest bet)

2. **Width-Based Short Leg Selection:** ⚠️ **EXPERIMENTAL** - May revert to Greeks/delta-based after backtesting.
   - **Problem:** Delta-based selection failed because option deltas jump in discrete values (0.45 → 0.25), creating gaps where no contracts match the 0.50-0.70 target range.
   - **Fix:** `SPREAD_SHORT_LEG_BY_WIDTH = True` - Select short leg by strike width ($2-$10, target $5) instead of delta.
   - **Risk:** Width-based ignores Greeks entirely. If backtesting shows poor risk-adjusted returns, revert to delta-based with wider tolerance bands.

3. **Spread Failure Cooldown:** After 340+ consecutive spread construction failures (retry spam), engine now enters 4-hour cooldown:
   - `SPREAD_FAILURE_COOLDOWN_HOURS = 4`
   - Prevents endless retry loops when no valid spreads exist

4. **Hard Cash Reserve:** Increased from 10% to 25% to ensure options always have buying power:
   - `OPTIONS_HARD_CASH_RESERVE_PCT = 0.25`
   - This cash is NEVER deployed to SHV
   - Total reserve now 35% (10% buffer + 25% options)

5. **DTE Filter Order (CRITICAL):** Chain filter allowed 45 DTE but spread validation rejected > 21 DTE:
   - Selection was picking 35 DTE (better delta) over valid 18 DTE
   - **Fix:** Filter by DTE range FIRST, then sort by delta
   - Short leg must also match long leg's expiration

**Commits:**
- `1d0d818` - Inverted sizing + width-based short leg selection
- `9448ce9` - 4-hour cooldown after spread construction failure
- `fe10f94` - 25% hard cash reserve for options
- `ae59e8c` - DTE filter before delta selection

**Tests:** All passing

### V2.4.4: P0 Critical Safety Fixes (2026-02-02)

**Goal:** Fix 4 critical bugs identified in V2.4.3 AAP Audit causing margin disasters and 2,765+ invalid orders.

**Audit Report:** `docs/audits/V2_4_3_AAP_AUDIT.md`

| # | Fix | File(s) | Description | Status |
|:-:|-----|---------|-------------|:------:|
| 1 | Expiration Hammer V2 | `main.py`, `options_engine.py`, `config.py` | Force close ALL options at 2 PM on expiration day | ✅ |
| 2 | Margin Call Circuit Breaker | `main.py`, `portfolio_router.py`, `config.py` | Stop after 5 consecutive margin rejects, 4h cooldown | ✅ |
| 3 | Exercise Detection | `main.py`, `config.py` | Detect option exercises in OnOrderEvent, liquidate resulting shares | ✅ |
| 4 | Margin Pre-Check Buffer | `config.py` | Require 20% extra margin buffer before orders | ✅ |

**Root Causes (from V2.4.3 Audit):**
- **2,765 margin call orders** - Options held to expiration, auto-exercised, created massive QQQ share positions
- **3 option exercises** - ITM options converted to $700K+ QQQ shares, triggering margin death spiral
- **9 kill switch triggers** - Kill switch couldn't close options during margin crisis (orders went Invalid)
- **+17.6% return was FAKE** - Artifact of accidentally holding QQQ shares from exercised options

**Key Changes:**

1. **Expiration Hammer V2 (P0):** Force close ALL options at 2:00 PM on expiration day
   - `EXPIRATION_HAMMER_CLOSE_ALL = True`
   - Scans broker positions for ANY expiring options (not just tracked spreads)
   - Unconditional close regardless of ITM/OTM or VIX level
   - Prevents auto-exercise → share conversion → margin disaster

2. **Margin Call Circuit Breaker (P0):** Stop retry spam after consecutive failures
   - `MARGIN_CALL_MAX_CONSECUTIVE = 5` - Stop after 5 margin rejects
   - `MARGIN_CALL_COOLDOWN_HOURS = 4` - 4-hour trading pause
   - Tracks state in `_margin_call_consecutive_count` and `_margin_call_cooldown_until`
   - Portfolio Router blocks ALL orders during cooldown

3. **Exercise Detection (P0):** Handle option exercises gracefully
   - `OPTIONS_HANDLE_EXERCISE_EVENTS = True`
   - Detects "Exercise" in OnOrderEvent messages
   - Immediately liquidates any resulting QQQ share positions
   - Logs `EXERCISE_DETECTED` and `EXERCISE_LIQUIDATE` for audit trail

4. **Margin Pre-Check Buffer:** Extra margin cushion before orders
   - `MARGIN_PRE_CHECK_BUFFER = 1.20` - Require 20% extra margin
   - Prevents orders that would trigger immediate margin calls

**Config Additions (config.py):**
```python
# V2.4.4 P0 FIXES - CRITICAL OPTIONS SAFETY
MARGIN_CALL_MAX_CONSECUTIVE = 5
MARGIN_CALL_COOLDOWN_HOURS = 4
MARGIN_PRE_CHECK_BUFFER = 1.20
OPTIONS_HANDLE_EXERCISE_EVENTS = True
EXPIRATION_HAMMER_CLOSE_ALL = True
```

**Tests:** 1348 passed, 2 skipped

### V2.4.5: Remove Yield Sleeve (SHV) Entirely (2026-02-02)

**Goal:** Eliminate SHV as root cause of "Insufficient Buying Power" and margin conflicts.

**Rationale:**
1. **Cash Trap**: Broker treats SHV as stock, not cash. Options orders fail because capital is locked in SHV.
2. **Margin Conflicts**: Managing SHV alongside leveraged ETFs causes margin calculation failures and forced liquidations.
3. **Risk/Reward**: Risking catastrophic execution errors to earn ~4.5% yield that broker already pays on idle cash automatically.

**Scope of Removal:**

| Area | Changes | Files |
|------|---------|-------|
| Configuration | Remove SHV from LEVERAGE_MAP, SYMBOL_GROUPS, TRADED_SYMBOLS | `config.py` |
| Main Algorithm | Remove YieldSleeve initialization, SHV subscription, yield signal generation | `main.py` |
| Portfolio Router | Remove SHV liquidation logic, YIELD source limit | `portfolio_router.py` |
| Engine Package | Remove yield_sleeve.py, update __init__.py exports | `engines/satellite/` |
| Tests | Remove test_yield_sleeve.py, update SHV-related assertions | `tests/` |

**Key Deletions:**
- `engines/satellite/yield_sleeve.py` - Entire file deleted
- `tests/test_yield_sleeve.py` - Entire file deleted
- `_generate_yield_signals()` method in main.py
- `calculate_shv_liquidation()` method in portfolio_router.py
- `_add_shv_liquidation_if_needed()` method in portfolio_router.py

**Config Changes:**
```python
# REMOVED:
# SHV_MIN_TRADE = 10_000
# CASH_BUFFER_PCT = 0.10
# OPTIONS_HARD_CASH_RESERVE_PCT = 0.25
# "SHV": 1.0 in LEVERAGE_MAP
# "SHV": "RATES" in SYMBOL_GROUPS

# UPDATED:
TRADED_SYMBOLS = ["TQQQ", "SOXL", "QLD", "SSO", "TNA", "FAS", "TMF", "PSQ"]  # No SHV
RATES limit changed from 0.99 to 0.40 (TMF only)
```

**Success Criteria:**
- ✅ Bot initializes without loading SHV data
- ✅ Trend/Options signals execute immediately using available cash
- ✅ Zero "Insufficient Buying Power" errors from capital locked in SHV
- ✅ All 1299 tests pass

**Tests:** 1299 passed, 2 skipped

---

### V2.6: Spread Engine Bug Fixes (2026-02-02)

**Goal:** Fix 16 bugs in spread leg selection, entry tracking, exit logic, and cross-engine coordination.

**Analysis Method:** Deep code analysis using Explore agents to investigate:
1. Spread leg selection logic (DTE matching, delta validation)
2. Spread exit logic (stop loss, profit targets, Friday firewall)
3. Spread entry/fill tracking (race conditions, partial fills)

**Bugs Fixed by Severity:**

| Phase | Priority | Bugs Fixed | Status |
|:-----:|:--------:|:----------:|:------:|
| 1 | Foundation | SpreadFillTracker class + config | ✅ DONE |
| 2 | P0 Critical | #1 Race condition, #2 Partial fills, #3 Exit fallback, #14 Greek decay | ✅ DONE |
| 3 | P1 High | #4 DTE mismatch, #5 Bidirectional mapping, #6-8 Qty tracking, #16 Margin cooldown | ✅ DONE |
| 4 | P2 Medium | #9 Delta re-validation, #11 Price fallback, #13 Symbol normalization | ✅ DONE |

**Key Fixes:**

| # | Bug | Root Cause | Fix |
|:-:|-----|------------|-----|
| 1 | Race condition in fill tracking | After first leg fills, pending state cleared before second leg | SpreadFillTracker stores symbols at creation |
| 2 | No partial fill handling | Only `Filled` handled, not `PartiallyFilled` | Added handler with VWAP accumulation |
| 3 | Silent exit failure | Missing chain data causes silent return | Fallback to `Securities[symbol].Price` |
| 4 | DTE mismatch not validated | Only long leg DTE checked | Added short leg DTE validation (±1 day) |
| 5 | No reverse mapping for long rejection | Only `short→long` mapping exists | Added `_pending_spread_orders_reverse` |
| 14 | Greek Decay Exit Failure (0DTE) | Gamma causes price swings → rejected exits | Retry logic + 3:30 PM forced exit |
| 16 | Post-Trade Margin Ghost | T+1 settlement delay | `_last_spread_exit_time` + cooldown check |

**Files Modified:**
- `config.py` - V2.6 spread tracking parameters
- `engines/satellite/options_engine.py` - SpreadFillTracker, ExitOrderTracker, validations
- `main.py` - Fill tracking, close tracking, exit retry logic, symbol helper

**Config Additions:**
```python
SPREAD_FILL_TIMEOUT_MINUTES = 5          # Bug #7
SPREAD_FILL_QTY_MISMATCH_ACTION = "LOG_AND_CLOSE"
OPTIONS_POST_TRADE_COOLDOWN_MINUTES = 2  # Bug #16
EXIT_ORDER_RETRY_COUNT = 3               # Bug #14
EXIT_ORDER_RETRY_DELAY_SECONDS = 5
ZERO_DTE_FORCE_EXIT_HOUR = 15
ZERO_DTE_FORCE_EXIT_MINUTE = 30
```

**Tests:** 1297 passed, 7 failed (6 pre-existing stop tier test mismatches)

---

### V2.7: Options Engine Capital Synchronization (2026-02-02)

**Goal:** Synchronize Options Engine with Trend Engine's safety protocols and prevent over-leveraging.

**Issues Fixed:**

| # | Issue | Root Cause | Fix |
|:-:|-------|------------|-----|
| 1 | **Options uses phantom margin** | `TotalPortfolioValue` includes margin buying power | Switch to `get_tradeable_equity()` |
| 2 | **No dollar caps** | Flat % is volatile for small accounts | Tiered caps: $5K/$10K/uncapped |
| 3 | **Short leg fills ignored** | `fill_qty > 0` check excluded sells | Already fixed in V2.5 (verified) |
| 4 | **Non-atomic exits** | Separate leg orders cause leg risk | Already fixed in V2.5 (verified) |

**Tiered Dollar Caps (The $5K Scale):**

| Tier | Tradeable Equity | Max Per Spread |
|:----:|:----------------:|:--------------:|
| 1 | < $60,000 | $5,000 |
| 2 | $60,000 - $100,000 | $10,000 |
| 3 | > $100,000 | No cap (% only) |

**Files Modified:**

| File | Changes |
|------|---------|
| `config.py` | 4 new tiered cap parameters |
| `options_engine.py` | `_apply_tiered_dollar_cap()` method, updated `get_mode_allocation()` |
| `main.py` | Lines 1950, 2922, 2990: Use `get_tradeable_equity()` |

**Config Additions:**
```python
# V2.7: Tiered Options Dollar Caps
OPTIONS_DOLLAR_CAP_TIER_1_THRESHOLD = 60_000
OPTIONS_DOLLAR_CAP_TIER_2_THRESHOLD = 100_000
OPTIONS_DOLLAR_CAP_TIER_1 = 5_000
OPTIONS_DOLLAR_CAP_TIER_2 = 10_000
```

**Impact on $50K Account:**
- Before: Options sizes on $50K (oversized, uses margin)
- After: Options sizes on $45K tradeable equity, capped at $5K per spread

**Tests:** 1298 passed, 6 failed (pre-existing stop tier mismatches)

---

### V2.8: VASS - Volatility-Adaptive Strategy Selection (2026-02-02)

**Goal:** Implement dynamic strategy selection based on IV environment to optimize spread type and expiration.

**Strategy Matrix (Phase 1 - Defined Risk Only):**

| IV Environment | VIX Range | Direction | Strategy | Expiration |
|----------------|:---------:|-----------|----------|------------|
| **Low** | < 15 | Bullish | Bull Call Debit | Monthly (30-45 DTE) |
| **Low** | < 15 | Bearish | Bear Put Debit | Monthly (30-45 DTE) |
| **Medium** | 15-25 | Bullish | Bull Call Debit | Weekly (7-21 DTE) |
| **Medium** | 15-25 | Bearish | Bear Put Debit | Weekly (7-21 DTE) |
| **High** | > 25 | Bullish | Bull Put Credit | Weekly (7-14 DTE) |
| **High** | > 25 | Bearish | Bear Call Credit | Weekly (7-14 DTE) |

**Key Components:**

| Component | Description | Status |
|-----------|-------------|:------:|
| IVSensor | 30-min VIX SMA for IV classification | ✅ Complete |
| SpreadStrategy Enum | 4 types: BULL_CALL_DEBIT, BEAR_PUT_DEBIT, BULL_PUT_CREDIT, BEAR_CALL_CREDIT | ✅ Complete |
| Strategy Factory | Maps (direction, IV) → (strategy, DTE range) | ✅ Complete |
| Credit Spread Leg Selection | select_credit_spread_legs() for Bull Put/Bear Call | ✅ Complete |
| Credit Spread Sizing | Margin-based sizing (not premium) | ✅ Complete |
| Credit Spread Exit Logic | P&L calculation with sign inversion for credits | ✅ Complete |
| Safety Checks | Atomic combo orders + margin validation | ✅ Complete |

**Files Modified:**

| File | Changes |
|------|---------|
| `config.py` | ~15 VASS parameters (IV thresholds, DTE ranges, credit constraints) |
| `options_engine.py` | IVSensor class, SpreadStrategy enum, _select_strategy(), select_credit_spread_legs(), _calculate_credit_spread_size(), updated exit logic |
| `portfolio_router.py` | Credit spread margin validation before execution |

**Config Additions:**
```python
# V2.8: VASS - IV Environment Classification
VASS_ENABLED = True
VASS_IV_LOW_THRESHOLD = 15      # VIX < 15 = Low IV
VASS_IV_HIGH_THRESHOLD = 25     # VIX > 25 = High IV
VASS_IV_SMOOTHING_MINUTES = 30  # SMA to prevent flickering

# V2.8: DTE Ranges by IV Environment
VASS_LOW_IV_DTE_MIN = 30        # Monthly
VASS_LOW_IV_DTE_MAX = 45
VASS_MEDIUM_IV_DTE_MIN = 7      # Weekly
VASS_MEDIUM_IV_DTE_MAX = 21
VASS_HIGH_IV_DTE_MIN = 7        # Weekly (credit)
VASS_HIGH_IV_DTE_MAX = 14

# V2.8: Credit Spread Constraints
CREDIT_SPREAD_MIN_CREDIT = 0.30
CREDIT_SPREAD_WIDTH_TARGET = 5.0
CREDIT_SPREAD_PROFIT_TARGET = 0.50
CREDIT_SPREAD_STOP_MULTIPLIER = 2.0
```

**Credit Spread Safety:**
- Sizing: `max_contracts = allocation / ((width - credit) × 100)` (NOT premium!)
- Example: $5 width, $0.50 credit → $450 margin per spread → 11 contracts max with $5K cap
- Execution: Atomic ComboMarketOrder (no leg risk)

---

### V2.9: Credit Spread Safety Audit - Bug Fixes (2026-02-02)

**Goal:** Fix 6 critical bugs identified when transitioning to credit spreads in V2.8 VASS implementation.

**Bug Analysis Summary:**

| Bug | Description | Status | Severity |
|:---:|-------------|:------:|:--------:|
| 1 | Buying Power Lock-Out (Margin Interaction) | ✅ FIXED | HIGH |
| 2 | Atomic Exit Failure (Leg Risk) | ✅ ALREADY FIXED | - |
| 3 | Intraday Firewall Conflict | ✅ FIXED | MEDIUM |
| 4 | Over-Trading / Signal Noise | ✅ FIXED | MEDIUM |
| 5 | Ghost Positions (Quantity Check) | ✅ ALREADY FIXED | - |
| 6 | Settlement Lag (Monday Morning Freeze) | ✅ FIXED | CRITICAL |

**Bug #1: Buying Power Lock-Out (Margin Interaction)**

*Issue:* Trend engine attempts position but gets "Limited Buying Power" rejection because credit spread margin isn't tracked.

*Fix:* Track open spread margin reservation.

| Change | File |
|--------|------|
| Added `_open_spread_margin` dict to track reserved margin | `portfolio_router.py` |
| Added `register_spread_margin()` / `unregister_spread_margin()` | `portfolio_router.py` |
| Added `get_effective_margin_remaining()` | `portfolio_router.py` |
| Updated margin check to use effective margin | `portfolio_router.py` |

**Bug #3: Intraday Firewall Conflict**

*Issue:* Friday firewall doesn't handle holiday weeks (e.g., Good Friday when Thursday is expiration day).

*Fix:* Holiday-aware expiration firewall.

| Change | File |
|--------|------|
| Added `get_expiration_firewall_day()` using exchange calendar | `options_engine.py` |
| Added `is_expiration_firewall_day()` for dynamic day detection | `options_engine.py` |
| Updated Friday firewall to run daily with holiday check | `main.py` |

**Bug #4: Over-Trading / Signal Noise**

*Issue:* VIX flickering around thresholds can cause excessive trades (10 opens/closes per day).

*Fix:* Comprehensive trade counter with daily limits.

| Change | File |
|--------|------|
| Added `MAX_OPTIONS_TRADES_PER_DAY = 4` | `config.py` |
| Added `MAX_SWING_TRADES_PER_DAY = 2` | `config.py` |
| Added `_swing_trades_today`, `_total_options_trades_today` counters | `options_engine.py` |
| Added `_can_trade_options()` comprehensive limit check | `options_engine.py` |
| Added `_increment_trade_counter()` to track all trade types | `options_engine.py` |
| Updated entry methods to enforce limits | `options_engine.py` |

**Bug #6: Settlement Lag (Monday Morning Freeze)**

*Issue:* Bot tries to trade with Friday's unsettled cash on Monday morning, causing "Insufficient Funds" errors.

*Fix:* Holiday-aware settlement detection with `Portfolio.UnsettledCash` subtraction.

| Change | File |
|--------|------|
| Added `SETTLEMENT_AWARE_TRADING = True` | `config.py` |
| Added `SETTLEMENT_COOLDOWN_MINUTES = 60` | `config.py` |
| Added `_is_first_bar_after_market_gap()` using exchange calendar | `main.py` |
| Added `_check_settlement_cooldown()` triggered at SOD | `main.py` |
| Added `_can_trade_options_settlement_aware()` | `main.py` |
| Added `get_tradeable_equity_settlement_aware()` | `capital_engine.py` |

**Config Additions:**
```python
# V2.9: Settlement-Aware Trading (Bug #6 Fix)
SETTLEMENT_AWARE_TRADING = True
SETTLEMENT_COOLDOWN_MINUTES = 60
SETTLEMENT_CHECK_SYMBOL = "SPY"
FRIDAY_HOLIDAY_CHECK_ENABLED = True

# V2.9: Global Options Trade Limits (Bug #4 Fix)
MAX_OPTIONS_TRADES_PER_DAY = 4
MAX_SWING_TRADES_PER_DAY = 2
```

**Files Modified:**

| File | Changes |
|------|---------|
| `config.py` | Settlement and trade limit parameters |
| `main.py` | Settlement detection, holiday-aware firewall, trade limit integration |
| `engines/core/capital_engine.py` | Settlement-aware tradeable equity |
| `engines/satellite/options_engine.py` | Trade counters, holiday-aware expiration detection |
| `portfolio/portfolio_router.py` | Spread margin tracking |

---

## Planned Features (V2.5+)

> **Roadmap Document:** `docs/v2-specs/V2_5_ROADMAP.md`

### V2.5: Multi-Asset Basket (Q2 2026)

| Symbol | Description | Allocation | Status |
|--------|-------------|:----------:|:------:|
| TMF | 3× Treasury Bond | 10% | 📋 Planned |
| UGL | 2× Gold | 10% | 📋 Planned |

**Changes:**
- Add TMF, UGL to Trend Engine
- Remove TNA, FAS (or reconfigure)
- Total allocation: 55% → 65%
- Resolve TMF conflict with Hedge Engine

### V2.6: Mean Reversion Bidirectional (Q3 2026)

| Symbol | Description | Direction | Status |
|--------|-------------|-----------|:------:|
| SQQQ | 3× Inverse Nasdaq | SHORT | 📋 Planned |
| SOXS | 3× Inverse Semiconductor | SHORT | 📋 Planned |

**Logic:**
- Short entry: RSI > 75 + Rally > 2.5%
- Mutual exclusivity: block long if short held
- Total MR allocation (long + short) ≤ 10%

---

### V2.3.12 Backtest Results (2026-01-31) — Historical

**Backtest:** V2.3.12-ComboFix-2month | **Result:** +4.09% | **Orders:** 143 | **Options:** 7 only!

| Metric | Value | Notes |
|--------|------:|-------|
| Return | +4.09% | First profitable backtest |
| Equity | $50,000 → $52,047 | |
| Orders | 143 | But only 7 options trades! |
| Sharpe | 0.656 | |
| Sortino | 0.905 | |
| Win Rate | 42% | |
| Drawdown | 9.10% | |

**URL:** https://www.quantconnect.com/project/27678023/99384af2cd3dfa3219d6f95ba2f584fd

**Issue Found:** PART 16 analysis revealed 99% of options signals were blocked by `_entry_attempted_today` throttle. V2.3.14 fixes this - expecting significantly more options trades.

### Stage 2 V2.3.2 Backtest Validation (Historical)

**Previous Backtest:** Smooth Magenta Bat | **Result:** -8.33% | **Orders:** 9
**Latest Backtest:** Casual Orange Cobra | **Result:** -6.98% | **Orders:** 14 | **Fees:** $171.51

**V2.3.2 Critical Fixes Applied (All 5 from Architect Audit Part 1-2):**
1. ✅ **OPT_INTRADAY source limit** - Added to SOURCE_ALLOCATION_LIMITS (5% max)
2. ✅ **Requested quantity enforced** - Router now uses `requested_quantity` from engine
3. ✅ **RegimeState.score fixed** - Changed to `smoothed_score`
4. ✅ **Engines separated** - Intraday positions tracked in `_intraday_position`, not `_position`
5. ✅ **Intraday 15:30 exit working** - Force close now checks correct position variable
6. ✅ **Intraday DTE expanded** - 0-5 DTE for backtest data availability (was 0-2)

**V2.3.3 Fixes from Architect Audit Part 3 (2026-01-31):**
| # | Finding | Severity | Status |
|:-:|---------|:--------:|:------:|
| 1 | **Trend Allocation Flattening** - TrendEngine returns `target_weight=1.0` for all symbols | CRITICAL | ✅ FIXED |
| 2 | **Closing Trade Bypass** - MIN_TRADE_VALUE check skips worthless option closes | MEDIUM | ✅ FIXED |
| 3 | **Exit Race Condition** - Duplicate close orders if fill delayed | LOW | ✅ FIXED |

**V2.3.3 Fixes from Architect Audit Part 4 (2026-01-31):**
| # | Finding | Severity | Status |
|:-:|---------|:--------:|:------:|
| 1 | **Traffic Jam** - Swing spreads blocked by intraday early return in `_scan_options_signals` | CRITICAL | ✅ FIXED |
| 2 | **Trend Throttling** - `MAX_CONCURRENT_TREND_POSITIONS=2` blocking TNA/FAS | HIGH | ✅ FIXED (→4) |
| 3 | **Minified Files Desync** - `main_minified.py` had old single-leg options code | CRITICAL | ✅ REMOVED |

**V2.3.4 Fixes from Architect Audit Part 5-7 (2026-01-31):**
| # | Finding | Severity | Status |
|:-:|---------|:--------:|:------:|
| 1 | **Cold Start Bypass** - Options entering on Day 1 during cold start | CRITICAL | ✅ FIXED |
| 2 | **Direction Mismatch** - Contract selected BEFORE direction determined | CRITICAL | ✅ FIXED |
| 3 | **Inverted Trade** - Bought CALL when should have bought PUT for fade | CRITICAL | ✅ FIXED |
| 4 | **Global Kill Switch** - Options loss liquidating healthy trend positions | HIGH | ✅ FIXED (engine-specific) |
| 5 | **Spread Criteria Too Tight** - OI 5000, delta 0.25-0.40 too restrictive | HIGH | ✅ FIXED (OI→1000, delta 0.15-0.45) |
| 6 | **DTE Too Wide** - 0-5 DTE not true 0DTE trading | MEDIUM | ✅ FIXED (→0-1 DTE) |
| 7 | **VIX Resolution Daily** - VIX only updated once/day, not intraday | CRITICAL | ✅ FIXED (→Minute) |
| 8 | **QQQ Move Not in Regime** - Direction determined separately from regime | HIGH | ✅ FIXED (incorporated) |

**V2.3.4 Key Changes:**
- VIX subscribed at `Resolution.Minute` (was Daily) - live intraday updates
- Added `_vix_15min_ago` tracker for short-term trend detection
- Added `QQQMove` enum (UP_STRONG, UP, FLAT, DOWN, DOWN_STRONG)
- Created `recommend_strategy_and_direction()` - combined decision in Micro Regime
- Direction now determined INSIDE regime assessment, not separately
- Data gathered every minute, processed every 15 minutes (no log spam)

**Status:** V2.3.4 fixes complete - Ready for backtest

**V2.3.5 Fixes from Architect Audit Part 9 (2026-01-31):**
| # | Finding | Severity | Status |
|:-:|---------|:--------:|:------:|
| 1 | **Open Interest Too High** - 5000 filtered 80% of 0-5 DTE contracts | HIGH | ✅ FIXED (→500) |
| 2 | **Spread Delta Too Narrow** - 0.45-0.55 (±0.05) misses valid ATM | HIGH | ✅ FIXED (→0.40-0.60) |
| 3 | **Intraday Delta Tolerance** - 0.15 too restrictive for 0.30 target | MEDIUM | ✅ FIXED (→0.20) |

**V2.3.5 Stage 3 Backtest Results (2026-01-31):**
- **Name:** Hipster Yellow-Green Hornet
- **Period:** Jan 1 - Mar 31, 2024 (Q1 2024, 3 months)
- **Return:** -1.42% (improved from -3.45% Stage 2)
- **Drawdown:** 12.30%
- **Orders:** 95 (up from 7 - PART 9 fix working!)
- **Win Rate:** 43%
- **Backtest URL:** https://www.quantconnect.com/project/27678023/90fcb04626294aba0c625261fba8002d

**Status:** V2.3.5 Stage 3 complete - Options finding contracts, 88 more trades

**V2.3.6 Fixes from "Upgraded Blue Whale" Analysis (2026-01-31):**
| # | Finding | Severity | Status |
|:-:|---------|:--------:|:------:|
| 1 | **Spread Orphaned Long Leg** - IBKR rejects short leg (margin), long leg fills | CRITICAL | ✅ FIXED |
| 2 | **Margin Pre-Check Missing** - No validation before spread submission | HIGH | ✅ FIXED |
| 3 | **Intraday OI Too High** - 500 OI filters out most 0DTE PUTs on up days | HIGH | ✅ FIXED (→200) |
| 4 | **Intraday Spread Too Tight** - 10% rejects normal 0DTE spreads | HIGH | ✅ FIXED (→15%) |
| 5 | **10:30 Gatekeeper Blocking** - Hardcoded block kills 10:00-10:30 momentum window | HIGH | ✅ FIXED (removed) |
| 6 | **Trend Stops Too Tight** - ATR×3.0 suffocating trades in choppy markets | MEDIUM | ✅ FIXED (→3.5) |
| 7 | **SHV Churn** - $2K threshold causing excessive rebalancing | LOW | ✅ FIXED (→$10K) |

**V2.3.6 Key Changes:**
- Added `_pending_spread_orders` dictionary to track spread order pairs (short→long)
- Pre-submission margin check blocks spread if short leg would fail ($10K/contract estimate)
- OnOrderEvent detects short leg `Invalid` status and liquidates orphaned long leg
- Successful fill cleanup removes spread from tracking
- Relaxed intraday filters: OI 500→200, Spread 10%→15% (0DTE reality)
- Removed 10:30 gatekeeper - intraday window now 10:00-15:00 (was 10:30-15:00)
- Widened Chandelier stops: BASE 3.0→3.5, TIGHT 2.5→3.0, TIGHTER 2.0→2.5
- Raised profit thresholds: TIGHT 10%→15%, TIGHTER 20%→25%
- Raised SHV_MIN_TRADE: $2K→$10K (reduce rebalancing churn)
- Logs: `SPREAD: BLOCKED`, `SPREAD: Tracking order pair`, `SPREAD: LIQUIDATING orphaned long leg`

**Root Cause (Spread):** IBKR treats spread legs as separate orders requiring naked short margin (~$343K) instead of spread margin (~$11K). Without margin check, long leg fills but short leg fails, leaving orphaned position.

**Root Cause (Intraday):** 0DTE PUTs on up days have lower OI and wider spreads. Cascade of filters (DTE→Direction→Delta→OI→Spread) left 0 contracts passing.

**Status:** V2.3.6 fixes complete - Backtest "Pensive Red Rabbit" running

**V2.3.7 Fixes from "Pensive Red Rabbit" Analysis (2026-01-31):**
| # | Finding | Severity | Status |
|:-:|---------|:--------:|:------:|
| 1 | **Cash Death Spiral** - YieldSleeve orders $26K SHV with $430 margin | CRITICAL | ✅ FIXED |
| 2 | **Intraday Filters Too Tight** - OI 200 + Spread 15% still filtering valid contracts | HIGH | ✅ FIXED (OI→100, Spread→25%) |
| 3 | **Spread Short Leg Failures** - 4,578 "No valid OTM contract" errors | HIGH | ✅ FIXED (width $2-5, delta 0.10-0.50) |
| 4 | **ADX Blocking Early Trends** - 71 entries blocked by lagging ADX indicator | MEDIUM | ✅ FIXED (threshold 25→20) |

**V2.3.7 Key Changes:**
- Added margin cap in `yield_sleeve.py`: `min(unallocated, MarginRemaining * 0.95)`
- Widened intraday filters: OI 200→100, Spread 15%→25%
- Relaxed spread short leg: SPREAD_WIDTH_MIN 3→2, SPREAD_WIDTH_TARGET 5→3
- Relaxed spread delta: SHORT_LEG_DELTA_MIN 0.15→0.10, MAX 0.45→0.50
- Lowered ADX thresholds: ENTRY 25→20, WEAK 20→15, MODERATE 25→20

**Root Cause (Cash Death Spiral):** YieldSleeve calculated target from TotalPortfolioValue but didn't check MarginRemaining. Pending orders from other engines consumed margin before SHV order executed.

**Root Cause (Filters):** 0DTE contracts in volatile markets have lower OI and wider spreads. Previous thresholds (OI=200, Spread=15%) filtered out most tradeable contracts.

**Root Cause (ADX):** ADX is lagging - by time it reaches 25, the trend move is often half over. Lowering to 20 captures earlier entries.

**Status:** V2.3.7 fixes complete - Ready for backtest validation

**V2.3.8 Fixes from PART 14 Analysis (2026-01-31):**
| # | Finding | Severity | Status |
|:-:|---------|:--------:|:------:|
| 1 | **TNA/FAS Volatility Trap** - Same ATR×3.5 stops for 2x and 3x ETFs | HIGH | ✅ FIXED (3x→ATR×2.5) |
| 2 | **Spread "Impossible Triangle"** - Width + Delta + Liquidity blocking all spreads | HIGH | ✅ FIXED (delta drives selection) |
| 3 | **0DTE Stop Slippage** - 20-30% stops slip to 60% loss on fast 0DTE moves | HIGH | ✅ FIXED (0DTE→15% stops) |

**V2.3.8 Key Changes:**
- Added `TREND_3X_SYMBOLS = ["TNA", "FAS"]` for 3× leveraged ETFs
- Added tighter 3× Chandelier multipliers: BASE 2.5, TIGHT 2.0, TIGHTER 1.5
- Updated trend_engine with `get_chandelier_multipliers()` for symbol-specific stops
- Removed strict width filter from spread selection - delta now primary criterion
- Widened `SPREAD_WIDTH_MAX` from $5 to $15 (width no longer blocks trades)
- Changed short leg sort from width proximity to delta proximity (target delta ~0.30)
- Added `OPTIONS_0DTE_STOP_PCT = 0.15` for tighter 0DTE stops
- Updated `calculate_position_size()` to accept `days_to_expiry` parameter
- Hard stops already placed via OCO Manager `StopMarketOrder` (confirmed working)

**Root Cause (3x Volatility):** TNA/FAS swing 5-7% daily vs 2-3% for QLD/SSO. Same ATR×3.5 stop was too wide for 3× leverage, allowing 17%+ losses.

**Root Cause (Spread Triangle):** Fixed width ($3-5) + fixed delta (0.15-0.45) + OI (100+) = rarely all satisfied together. Market offers $10 wide at 0.15 delta OR $5 wide at 0.30 delta, not both.

**Root Cause (0DTE Slippage):** StopMarketOrder fills at next available price after trigger. 0DTE options can gap from $0.50 to $0.20 in 30 seconds. 15% stop limits max loss to ~30% even with slippage.

**Status:** V2.3.8 fixes complete - Ready for backtest validation

**V2.3.9 Fix: ComboMarketOrder for Spreads (CTA Memo 2026-01-31):**
| # | Finding | Severity | Status |
|:-:|---------|:--------:|:------:|
| 1 | **$729K Margin Rejection** - Sequential spread legs treated as naked short | CRITICAL | ✅ FIXED |

**V2.3.9 Key Changes:**
- Added `submit_combo_order()` to execution_engine.py
- Added `is_combo`, `combo_short_symbol`, `combo_short_quantity` to OrderIntent
- Updated `_generate_orders` to create single combo order for spreads
- Updated `execute_orders` to use `ComboMarketOrder` for combo orders
- Both legs submitted in single ticket = broker sees NET risk

**Root Cause:** Broker treats sequential spread legs as separate orders. Selling short leg first = naked short requiring $729K margin. Broker doesn't "know" we plan to buy protection 100ms later.

**Solution (per CTA Memo):** "Using Combo Orders will automatically calculate the multi-leg margin instead of classical one." ComboMarketOrder submits both legs atomically → broker calculates spread margin (~$42K) not naked margin (~$729K).

**Status:** V2.3.9 fixes complete - Ready for backtest validation

**V2.3.10 Fixes from PART 15 Forensics (2026-01-31):**
| # | Finding | Severity | Status |
|:-:|---------|:--------:|:------:|
| 1 | **ADX Entry/Exit Churn** - Entry at ADX >= 15 but exit at ADX < 20 = immediate churn | HIGH | ✅ FIXED |
| 2 | **Spread Filter 100% Broken** - 5% bid-ask threshold rejects all ATM contracts | CRITICAL | ✅ FIXED (→15%) |
| 3 | **Orphaned Options** - `_pending_contract` not set in intraday signal | CRITICAL | ✅ FIXED |
| 4 | **No DTE Exit** - Single-leg options held to expiration → auto-exercise | CRITICAL | ✅ FIXED (→2 DTE) |
| 5 | **Exit Signals Not Checked** - Single-leg profit target/stop/DTE exit missing | HIGH | ✅ FIXED |

**V2.3.10 Key Changes:**
- Restored ADX thresholds: ADX_WEAK_THRESHOLD 15→20, ADX_MODERATE_THRESHOLD 20→25
- Widened spread filter: OPTIONS_SPREAD_MAX_PCT 5%→15%
- Set `_pending_contract` in `check_intraday_entry_signal()` before `register_entry()`
- Added OPTIONS_SINGLE_LEG_DTE_EXIT = 2 (close by 2 DTE)
- Added `check_exit_signals()` call in `_monitor_risk_greeks()`

**Root Cause (ADX Churn):** Entry allowed at ADX=15 but exit triggers at ADX<20. Position opens, ADX is 15.5, next bar ADX is 15.2, exit triggers immediately.

**Root Cause (Spread Filter):** ATM contracts have wider bid-ask spreads than OTM. 5% threshold rejected all valid contracts - logs showed "No valid ATM contract" 100% of the time.

**Root Cause (Orphaned Options):** `check_intraday_entry_signal()` returned signal but didn't set `_pending_contract`. When `register_entry()` was called, it found no pending contract.

**Status:** V2.3.10 fixes complete - Ready for backtest validation

**V2.3.11 Fixes: SNIPER 0DTE Enhancement + Expiring Options Safety (2026-01-31):**
| # | Finding | Severity | Status |
|:-:|---------|:--------:|:------:|
| 1 | **Auto-Exercise Risk** - ITM options held past 4 PM get exercised → stock position | CRITICAL | ✅ FIXED |
| 2 | **VIX Barrier Too High** - VIX < 15 required for VERY_CALM, blocking calm market trades | HIGH | ✅ FIXED (→11.5) |
| 3 | **VIX Levels Misaligned** - Thresholds not matching market reality | MEDIUM | ✅ FIXED |

**V2.3.11 Key Changes:**
- Added `check_expiring_options_force_exit()` - closes options expiring TODAY at 15:45
- Added config: `OPTIONS_EXPIRING_TODAY_FORCE_CLOSE_HOUR = 15, _MINUTE = 45`
- Added `_get_option_expiry_date()` helper in main.py
- Lowered VIX barriers: VIX_LEVEL_VERY_CALM_MAX 15→11.5, CALM_MAX 18→15, NORMAL_MAX 20→18
- Updated VIX level classification in `classify_vix_level()` to use config constants

**Root Cause (Auto-Exercise):** V2.3.9 backtest held ITM QQQ calls into Friday close. Saturday 5 AM broker auto-exercised → 800 shares assigned = $360K on $50K account (7:1 leverage). Lucky market gap up saved the account.

**Root Cause (VIX Barrier):** VIX typically ranges 12-18 in calm markets. Requiring VIX < 15 for VERY_CALM blocked most normal trading days from firing SNIPER 0DTEs.

**Status:** V2.3.11 fixes complete - Ready for backtest validation

**V2.3.12 Fixes: Enable More 0DTEs + Unchoke Trend Engine (2026-01-31):**
| # | Finding | Severity | Status |
|:-:|---------|:--------:|:------:|
| 1 | **ITM VIX Barrier Too High** - VIX > 25 required for ITM momentum, blocking most days | HIGH | ✅ FIXED (→11.5) |
| 2 | **ADX Choking Trend Engine** - ADX >= 20 blocking entries during grinding rallies | HIGH | ✅ FIXED (→15) |

**V2.3.12 Key Changes:**
- INTRADAY_ITM_MIN_VIX: 25 → 11.5 (enable 0-DTE ITM momentum in calm markets)
- ADX_ENTRY_THRESHOLD: 20 → 15 (catch trends earlier, ADX is lagging indicator)
- ADX_WEAK_THRESHOLD: 20 → 15 (allow entering on grinding trends)
- TREND_ADX_EXIT_THRESHOLD: 20 → 10 (allow holding during low momentum grind)

**Root Cause (ITM VIX):** ITM momentum strategy required VIX > 25, which only occurs during high volatility. Normal VIX range is 12-20, so ITM momentum was blocked 90%+ of the time.

**Root Cause (ADX Choke):** Late March 2024 was a "grinding rally" with low ADX. Market hit ATH but ADX stayed below 20, blocking all trend entries. ADX is lagging - by time it reaches 20, the move is often half over.

**Root Cause (ADX Exit Churn):** Entry at ADX >= 15 but exit at ADX < 20 caused immediate churn. Lowering exit to ADX < 10 allows holding positions during grinding periods.

**Status:** V2.3.12 BACKTEST PASSED ✅ - +4.09% Return, 143 Orders, Sharpe 0.656

**V2.3.13 FIX: Options Orders Not Executing (2026-02-01):**
| # | Finding | Severity | Status |
|:-:|---------|:--------:|:------:|
| 1 | **Intraday signals queued but never processed** - Missing `_process_immediate_signals()` call | CRITICAL | ✅ FIXED |
| 2 | **Spread signals also missing processing** - Same bug for swing spread orders | CRITICAL | ✅ FIXED |

**V2.3.13 Key Changes:**
- Added `_process_immediate_signals()` after intraday signal reception (main.py line 2561)
- Added `_process_immediate_signals()` after spread signal reception (main.py line 2626)

**Root Cause (Intraday):** In `_scan_options_signals()`, intraday signal was added to `_pending_weights` via `receive_signal()` but NEVER processed. Every other place in the codebase calls `_process_immediate_signals()` after receiving an IMMEDIATE signal. The function returned early via swing spread path (line 2575), and by the time `OnData` step 9 ran, the signal was lost.

**Root Cause (Spread):** Same bug - spread signals also use `Urgency.IMMEDIATE` but were not being processed immediately.

**Evidence from logs:**
```
INTRADAY: Selected PUT | Strike=425.0 | Delta=0.24 | DTE=0  ← Contract selected
SPREAD: No valid OTM contract for short leg                   ← Swing mode fails (different issue)
                                                              ← No order fired!
```

**Status:** V2.3.13 fixes complete - Ready for backtest validation

**V2.3.14 FIX: PART 16 Architect Recommendations (2026-02-01):**

| # | Finding | Severity | Status |
|:-:|---------|:--------:|:------:|
| 1 | **Entry throttle blocking 99% of signals** - `_entry_attempted_today` blocked ALL after first signal | CRITICAL | ✅ FIXED |
| 2 | **Hardcoded fade kills momentum** - Direction determined BEFORE engine recommendation | HIGH | ✅ FIXED |
| 3 | **Swing spreads fail, no fallback** - "No valid ATM contract" with no single-leg option | HIGH | ✅ FIXED |

**V2.3.14 Key Changes:**

**Fix 1: Entry Throttle (Critical)**
- Removed `_entry_attempted_today` flag check from `check_intraday_entry_signal()`
- Added `INTRADAY_MAX_TRADES_PER_DAY = 3` config (was implicit 1)
- Increment `_intraday_trades_today` when position is registered (after fill)
- Evidence: 639 DEBIT_FADE recommendations → 7 signals (99% blocked by old throttle)

**Fix 2: Momentum Direction**
- Added `get_intraday_direction()` method to OptionsEngine
- main.py now gets engine recommendation FIRST, then selects matching contract
- OLD: Hardcoded fade (QQQ up → PUT) blocked all momentum trades
- NEW: Engine decides direction based on VIX Level × VIX Direction regime

**Fix 3: Single-Leg Swing Fallback**
- When spread selection fails ("No valid ATM contract"), now falls back to single-leg ITM
- Uses existing `_select_swing_option_contract()` (0.70 delta, 5-45 DTE)
- Logs `SWING_FALLBACK: Single-leg {direction} after spread failure`

**Root Cause Analysis (from V2.3.12 logs):**
```
Jan 8: 52 DEBIT_FADE recommendations → 1 signal fired
  10:00-10:29: Blocked by time window (DEBIT_FADE starts 10:30)
  10:30: First signal fires, _entry_attempted_today = True
  10:33: Trade stops out
  10:34+: ALL subsequent signals blocked by _entry_attempted_today
```

**Status:** V2.3.14 fixes complete - Ready for backtest validation

**V2.3.15 FIX: SNIPER LOGIC - PART 17 Architect Recommendations (2026-02-01):**

| # | Finding | Severity | Status |
|:-:|---------|:--------:|:------:|
| 1 | **QQQ threshold too loose** - 0.15% threshold treating noise as signals | HIGH | ✅ FIXED (→0.35%) |
| 2 | **FADE missing min move check** - MOMENTUM has 0.80% but FADE had none | HIGH | ✅ FIXED (→0.50%) |
| 3 | **Max trades too high** - 3 trades/day is machine-gunner, not sniper | MEDIUM | ✅ FIXED (→2) |

**V2.3.15 Sniper Logic Key Changes:**

**Philosophy:** "Sniper, not Machine Gunner" - Wait for high-conviction setups, filter noise

**4-Gate System:**
| Gate | Purpose | Threshold |
|------|---------|-----------|
| Gate 0 | Pre-flight checks | Position, trades, time window |
| Gate 1 | Noise filter | QQQ move >= 0.35% |
| Gate 2 | VIX context | Direction determines strategy |
| Gate 3 | Strategy qualification | FADE >= 0.50%, MOMENTUM >= 0.80% |
| Gate 4 | Contract selection | DTE, delta, OI, spread |

**Config Changes:**
```python
# Gate 1: Noise Filter
QQQ_NOISE_THRESHOLD = 0.35  # V2.3.15: was 0.15%

# Gate 3a: FADE Strategy
INTRADAY_DEBIT_FADE_MIN_MOVE = 0.50  # V2.3.15: new threshold

# Trade Management
INTRADAY_MAX_TRADES_PER_DAY = 2  # V2.3.15: was 3 (sniper gets one retry)
```

**Code Changes:**
1. `classify_qqq_move()` - Uses `config.QQQ_NOISE_THRESHOLD` (0.35%) for UP/DOWN classification
2. `recommend_strategy_and_direction()` - Added Gate 3a FADE min move check (0.50%)

**Documentation:** `docs/v2-specs/SNIPER_LOGIC_V2.3.15.md` - Complete specification with flowchart

**Status:** V2.3.15 SNIPER LOGIC complete - Ready for backtest validation

**V2.3.16 FIX: PART 17 Delta + Direction Conflict (2026-02-01):**

| # | Finding | Severity | Status |
|:-:|---------|:--------:|:------:|
| 1 | **Delta 0.70 > max 0.60** - Swing mode targeting 0.70 but validation caps at 0.60 | CRITICAL | ✅ FIXED |
| 2 | **Direction conflict** - FADE PUT vs regime bullish (>65) = opposing bets | HIGH | ✅ FIXED |
| 3 | **FADE max cap missing** - No upper bound on fade moves (runaway trends) | MEDIUM | ✅ FIXED |

**V2.3.16 Key Changes:**

**Fix 1: DTE-Based Delta Validation**
- Added `OPTIONS_SWING_DTE_THRESHOLD = 5` - DTE > 5 uses swing bounds
- Added `OPTIONS_SWING_DELTA_MIN = 0.55`, `OPTIONS_SWING_DELTA_MAX = 0.85`
- Added `OPTIONS_INTRADAY_DELTA_MIN = 0.40`, `OPTIONS_INTRADAY_DELTA_MAX = 0.60`
- Updated `check_entry_signal()` to use DTE-based validation

**Fix 2: Direction Conflict Resolution (Centralized in Options Engine)**
- Added `DIRECTION_CONFLICT_BULLISH_THRESHOLD = 65`
- Added `DIRECTION_CONFLICT_BEARISH_THRESHOLD = 40`
- Updated `get_intraday_direction()` to accept `regime_score` parameter
- Conflict check now inside options_engine (single source of truth)
- Skip FADE PUT when regime > 65 (strong bullish)
- Skip FADE CALL when regime < 40 (strong bearish)

**Fix 3: FADE Sniper Window (Max Cap)**
- Added `INTRADAY_FADE_MAX_MOVE = 1.20` - Don't fade runaway trends/crashes
- Renamed `INTRADAY_DEBIT_FADE_MIN_MOVE` → `INTRADAY_FADE_MIN_MOVE` (0.50%)
- FADE now requires: 0.50% <= |QQQ move| <= 1.20%

**Root Cause (Delta 0.70):** V2.3.14 swing fallback calls `_select_swing_option_contract()` which targets 0.70 delta, but `check_entry_signal()` validates against `OPTIONS_DELTA_MAX = 0.60`. All swing fallback trades were rejected.

**Root Cause (Direction Conflict):** Intraday FADE recommends PUT when QQQ is up (mean reversion). But if regime is 70+ (strong bull), fading the rally goes against the macro trend. V2.3.15 backtest lost $469 fading a bullish day with PUTs.

**Root Cause (Runaway Trend):** Without max cap, FADE would fire on 2%+ moves that are unlikely to mean-revert. Adding 1.20% cap prevents fading runaway trends/crashes.

**Status:** V2.3.16 DTE-BASED DELTA + DIRECTION CONFLICT fixes complete - Ready for backtest validation

**V2.3.17 FIX: Hybrid Yield Sleeve + Kill Switch 5% (2026-02-01):**

| # | Finding | Severity | Status |
|:-:|---------|:--------:|:------:|
| 1 | **Kill switch too sensitive** - 3% triggers on normal volatility | HIGH | ✅ FIXED (→5%) |
| 2 | **SHV churn from small trades** - Sniper (5%) trades liquidate SHV | MEDIUM | ✅ FIXED (10% cash buffer) |
| 3 | **RATES exposure limit blocking SHV** - 40% cap prevents post-kill-switch SHV | MEDIUM | ✅ FIXED (→99%) |
| 4 | **YIELD allocation limit low** - 50% cap when all cash should go to SHV | LOW | ✅ FIXED (→99%) |

**V2.3.17 Key Changes:**

**Kill Switch Raised to 5%:**
- `KILL_SWITCH_PCT`: 0.03 → 0.05 (5% daily loss triggers liquidation)
- `KILL_SWITCH_PCT_BY_PHASE`: Both SEED and GROWTH now 0.05
- Rationale: 3% triggered too frequently in volatile markets, 5% reduces false triggers to 2-4/year

**Hybrid Yield Sleeve Implementation:**
- Added `CASH_BUFFER_PCT = 0.10` (10% cash buffer)
- Modified `calculate_unallocated_cash()` to subtract cash buffer
- Buffer absorbs small Sniper (5%) and MR (5-10%) trades without touching SHV
- Reduces SHV churn by ~80% for typical intraday trades

**Exposure Limits Adjusted:**
- `RATES` group: 0.40 → 0.99 (allows near-full SHV post-kill-switch)
- `YIELD` source: 0.50 → 0.99 (allows SHV to absorb idle cash)

**Root Cause (Kill Switch):** At 3%, kill switch was estimated to trigger 4-8 times/year in normal markets. Raising to 5% reduces triggers while still protecting against major losses.

**Root Cause (SHV Churn):** Without cash buffer, every 5% Sniper trade required SHV liquidation (spread cost ~$1.82) then SHV buyback at EOD. With 10% buffer, small trades use cash directly.

**Root Cause (RATES Limit):** After kill switch fires and liquidates all positions, YieldSleeve wanted to put 99% in SHV, but RATES exposure group capped at 40%. Cash sat idle earning 0% instead of 5% in SHV.

**Status:** V2.3.17 HYBRID YIELD SLEEVE + KILL SWITCH 5% complete - Ready for backtest validation

**V2.3.18 FIX: Gamma Trap + Swing DTE Alignment (2026-02-01):**

| # | Finding | Severity | Status |
|:-:|---------|:--------:|:------:|
| 1 | **Single-leg exits after spreads** - 2 DTE vs spreads at 5 DTE | HIGH | ✅ FIXED (→4 DTE) |
| 2 | **Swing DTE min too low** - Entry at DTE=5, exit at DTE=4 = 1-day hold | HIGH | ✅ FIXED (→6 DTE) |

**V2.3.18 Key Changes:**

**Gamma Trap Fix:**
- `OPTIONS_SINGLE_LEG_DTE_EXIT`: 2 → 4 DTE
- Single legs now exit BEFORE spreads (4 DTE vs 5 DTE)
- Avoids gamma explosion in final week of expiration

**Swing DTE Alignment Fix:**
- `OPTIONS_SWING_DTE_MIN`: 5 → 6 DTE
- Ensures minimum 2-day holding period (enter at 6, exit at 4)
- Aligns with `OPTIONS_SWING_DTE_THRESHOLD=5` (DTE > 5 uses swing delta bounds)

**DTE Configuration (Final):**
| Mode | Entry DTE | Exit DTE | Min Hold |
|------|:---------:|:--------:|:--------:|
| Spreads | 10-21 | 5 | 5+ days |
| Single-Leg Swing | **6-45** | **4** | **2+ days** |
| Intraday | 0-1 | 3:30 PM | Same day |

**Root Cause (Gamma Trap):** Gamma risk explodes in the last week before expiration. A small move against you can wipe 50%+ of option value in hours.

**Root Cause (1-Day Hold):** With entry at DTE=5 and exit at DTE=4, swing trades could have only 1-day holding period - not a true swing trade.

**Status:** V2.3.18 GAMMA TRAP + SWING DTE complete - Ready for backtest validation

**V2.3.19 FIX: ITM_MOMENTUM Time Window Config (2026-02-01):**

| # | Finding | Severity | Status |
|:-:|---------|:--------:|:------:|
| 1 | **ITM_MOMENTUM time window hardcoded** - Not configurable like DEBIT_FADE | LOW | ✅ FIXED |

**V2.3.19 Key Changes:**

**Config Values Added:**
- `INTRADAY_ITM_START = "10:00"` - ITM Momentum entry window start
- `INTRADAY_ITM_END = "13:30"` - ITM Momentum entry window end

**Code Updated:**
- `check_intraday_entry_signal()` now parses time windows from config
- Both DEBIT_FADE and ITM_MOMENTUM use config values consistently

**Intraday Time Windows (Final):**
| Strategy | Start | End | Config |
|----------|:-----:|:---:|--------|
| DEBIT_FADE | 10:30 | 14:00 | `INTRADAY_DEBIT_FADE_START/END` |
| ITM_MOMENTUM | 10:00 | 13:30 | `INTRADAY_ITM_START/END` |
| CREDIT_SPREAD | 10:00 | 14:30 | `INTRADAY_CREDIT_START/END` |

**Status:** V2.3.19 TIME WINDOW CONFIG complete - Ready for backtest validation

**V2.3.20 FIX: Cold Start Options + SHV Auto-Liquidation (2026-02-01):**

| # | Finding | Severity | Status |
|:-:|---------|:--------:|:------:|
| 1 | **SHV Auto-Liquidation missing** - "Insufficient Buying Power" for immediate buys | CRITICAL | ✅ FIXED |
| 2 | **Cold Start blocks ALL options** - 5-day blackout too conservative | HIGH | ✅ FIXED (50% sizing) |

**V2.3.20 Key Changes:**

**Fix 1: SHV Auto-Liquidation (Critical)**
- Updated `_add_shv_liquidation_if_needed()` in portfolio_router.py
- Calculates shortfall: `buy_value - (available_cash + sell_proceeds)`
- Generates SHV SELL order when shortfall > 0
- 5% buffer for slippage: `min(shortfall * 1.05, available_shv)`
- Logs: `SHV_AUTO_LIQUIDATE: Selling $X SHV to fund $Y in buys`

**Fix 2: Cold Start Options with 50% Sizing**
- Added `OPTIONS_COLD_START_MULTIPLIER = 0.50` to config.py
- Modified main.py to allow options during cold start (was blocked)
- Added `size_multiplier` parameter to `get_mode_allocation()`
- Added `size_multiplier` parameter to `check_spread_entry_signal()`
- Added `size_multiplier` parameter to `check_intraday_entry_signal()`
- Added `size_multiplier` parameter to `check_entry_signal()`
- During cold start (Days 1-5), options trade at 50% normal size

**Config Changes:**
```python
# Cold Start Engine
OPTIONS_COLD_START_MULTIPLIER = 0.50  # V2.3.20: 50% sizing during cold start
```

**Root Cause (SHV):** `_add_shv_liquidation_if_needed()` only reordered sells/buys but never calculated shortfall or generated SHV sell orders. Immediate buys failed with "Insufficient Buying Power".

**Root Cause (Cold Start):** Old logic blocked ALL options during cold start (Days 1-5). This was too conservative - missing opportunities. 50% sizing reduces risk while allowing participation.

**Status:** V2.3.20 COLD START OPTIONS + SHV AUTO-LIQUIDATION complete - Ready for backtest validation

**V2.3.21 FIX: PART 18 Options Engine + Router Fixes (2026-02-01):**

| # | Finding | Severity | Status |
|:-:|---------|:--------:|:------:|
| 1 | **SHV auto-liquidation not triggered** - `_process_immediate_signals` missing cash params | CRITICAL | ✅ FIXED |
| 2 | **Router logging disabled** - `pass # Logging disabled` silences all router logs | HIGH | ✅ FIXED |
| 3 | **Spread delta mismatch** - Code uses ATM (0.40-0.60) but strategy needs ITM (0.70) | HIGH | ✅ FIXED |
| 4 | **Trend ignores pending MOO orders** - Generates duplicate signals for same symbol | HIGH | ✅ FIXED |
| 5 | **Cold Start + Trend conflict** - Both engines signal same symbols | HIGH | ✅ FIXED |
| 6 | **Position registered twice** - Duplicate `POSITION_REGISTERED` for same symbol | HIGH | ✅ FIXED |
| 7 | **452 "No valid contract" errors** - No throttling on spread scan (runs every minute) | HIGH | ✅ FIXED |
| 8 | **Kill switch loss % inconsistency** - Logs show two different loss percentages | LOW | ✅ FIXED |

**V2.3.22 FIX: Hard Swing Floor (2026-02-01):**

| # | Finding | Severity | Status |
|:-:|---------|:--------:|:------:|
| 1 | **Swing DTE too low** - 6 DTE minimum exposes to gap risk | HIGH | ✅ FIXED (→14 DTE) |

**V2.3.22 Key Changes:**
- `OPTIONS_SWING_DTE_MIN`: 6 → 14 (reduce overnight gap risk)
- `OPTIONS_SWING_DTE_THRESHOLD`: 5 → 14 (align with min)
- `SPREAD_DTE_MIN`: 10 → 14 (same gap cushion for spreads)

**V2.3.23 FIX: Cold Start Duplicate Orders (2026-02-01):**

| # | Finding | Severity | Status |
|:-:|---------|:--------:|:------:|
| 1 | **Cold Start duplicate warm entry orders** - MOO orders queue on weekends/holidays | CRITICAL | ✅ FIXED |

**Commit:** `05140be` - Set `_warm_entry_executed = True` immediately when generating signal.

**V2.3.22 Remaining Bugs (To Fix in V2.3.24+):**

| Priority | Bug | Description | Status |
|:--------:|-----|-------------|:------:|
| P1 | **SHV margin lock** | 10 SHV sell orders rejected - capital locked as margin collateral | 🟡 TODO |
| P1 | **Swing delta too restrictive** | 0.55-0.85 range missing entries (contracts with delta 0.50-0.54) | 🟡 TODO |
| P2 | **Combo orders not executing** | Spread signals generated but no fills in orders.csv | 🟡 TODO |
| P3 | **Intraday signal throttle** | 49 signals when should be 1-2 per entry | 🟡 TODO |

**V2.3.21 Key Changes:**

**Fix 1: SHV Auto-Liquidation Cash Params (CRITICAL)**
- `_process_immediate_signals()` in main.py now passes `available_cash` and `locked_amount`
- Without these, shortfall calculation used `available_cash=0.0` (default), breaking the logic
- Fix: `available_cash=self.Portfolio.Cash`, `locked_amount=capital_state.locked_amount`

**Fix 2: Enable Router Logging**
- Removed `pass # Logging disabled` from `portfolio_router.py`
- Fixed `if False and self.algorithm:` → `if self.algorithm:`
- Now logs: `ROUTER: RECEIVED`, `SHV_AUTO_LIQUIDATE`, etc.

**Fix 3: Spread Delta Range (ITM Long / OTM Short)**
- Changed `SPREAD_LONG_LEG_DELTA_MIN` from 0.40 → 0.55
- Changed `SPREAD_LONG_LEG_DELTA_MAX` from 0.60 → 0.85
- Strategy: ITM long leg (delta ~0.70) + OTM short leg = "Smart Swing"
- Wider range (0.55-0.85) prioritizes execution over precision

**Fix 4: Trend Pending MOO Tracking**
- Added `_pending_moo_symbols: Set[str]` to trend_engine.py
- Check pending set before generating new `ENTRY_APPROVED` signals
- Clear symbols from set after MOO orders execute

**Fix 5: Cold Start + Trend Coordination**
- Skip Trend entry signal if Cold Start already signaled same symbol
- Prevents duplicate orders (e.g., BUY 194 QLD + BUY 243 QLD → immediate reversal)

**Fix 6: Position Duplication Prevention**
- Check if symbol already registered before calling `register_position()`
- Log warning if duplicate registration attempted

**Fix 7: Spread Scan Throttling (15-minute timer)**
- Added `_last_spread_scan_time: Optional[datetime]` to main.py
- Skip `select_spread_legs()` if < 15 minutes since last scan
- Reduces log spam from 452 errors to ~30 per day

**Fix 8: Kill Switch Logging Consistency**
- Ensure single loss percentage logged (not two conflicting values)
- Use `loss_from_sod` consistently

**Config Changes:**
```python
# options_engine.py - ITM Long / OTM Short "Smart Swing"
SPREAD_LONG_LEG_DELTA_MIN = 0.55  # Was 0.40 (ATM)
SPREAD_LONG_LEG_DELTA_MAX = 0.85  # Was 0.60 (ATM)
```

**Evidence from V2.3.20 Backtest (Jan 2025):**
- 452 "No valid ATM/OTM contract" errors
- Spread ENTRY_SIGNAL generated but never filled (Jan 14)
- Duplicate QLD orders: BUY 194 + BUY 243 → immediate SELL 243 (Jan 21)
- No `SHV_AUTO_LIQUIDATE` or `ROUTER:` logs (logging disabled)

**Root Cause (SHV):** `_process_immediate_signals()` called `process_immediate()` without `available_cash` or `locked_amount` parameters. These defaulted to 0.0, so shortfall calculation was always wrong.

**Root Cause (Spread Delta):** V2.3 strategy targets ITM (delta 0.70) for directional exposure, but code used ATM range (0.40-0.60). Contracts with delta 0.70 were rejected as "not ATM".

**Root Cause (Duplicate Orders):** Trend Engine generates `ENTRY_APPROVED` every EOD even when MOO order already pending. Weekend/holiday closure queues multiple MOO orders for same symbol.

**Status:** V2.3.21 PART 18 FIXES in progress

---

**V2.4.0 Planned: Bidirectional Mean Reversion (PART 12)**
| # | Feature | Severity | Status |
|:-:|---------|:--------:|:------:|
| 1 | **Add SQQQ/SOXS inverse ETFs** - Capture "rally fade" when RSI > 75 | ENHANCEMENT | 🟡 AFTER V2.3.9 |
| 2 | **Mutual exclusivity** - Block TQQQ entry if SQQQ held (and vice versa) | HIGH | 🟡 AFTER V2.3.9 |
| 3 | **MR allocation cap** - Ensure Long + Short ≤ 10% total | HIGH | 🟡 AFTER V2.3.9 |

**V2.4.0 Scope:**
- Add MR_SHORT_SYMBOLS = ["SQQQ", "SOXS"] to config.py
- Add MR_RALLY_THRESHOLD = 0.025, MR_RSI_OVERBOUGHT = 75
- Subscribe to SQQQ, SOXS in main.py
- Update mean_reversion_engine.py with bidirectional logic
- Enforce mutual exclusivity (no simultaneous long + short)

**Rationale for Deferral:** V2.3.9 fixed the critical margin issue. Need to isolate performance impact before adding a new strategy direction. Bidirectional MR is a strategy enhancement, not a bug fix.

### Stage 2 Bugs - Prioritized Fix List

#### 🔴 CRITICAL - V2.3.2 Architect Audit Fixes ✅

| # | Bug | Status | Description |
|:-:|-----|:------:|-------------|
| 1 | **OPT_INTRADAY source unmapped** | ✅ FIXED | Added to SOURCE_ALLOCATION_LIMITS (5% max) |
| 2 | **requested_quantity ignored in scaling** | ✅ FIXED | Preserved in `_apply_source_limits()` |
| 3 | **RegimeState.score attribute error** | ✅ FIXED | Changed to `smoothed_score` |
| 4 | **Intraday position tracked wrong** | ✅ FIXED | Added `_pending_intraday_entry` flag, registers to `_intraday_position` |
| 5 | **15:30 force exit broken** | ✅ FIXED | Now checks `_intraday_position` correctly |
| 6 | **Intraday DTE too restrictive** | ✅ FIXED | Expanded from 0-2 to 0-5 DTE for backtest data |

#### 🔴 CRITICAL - Phase A Complete ✅

| # | Bug | Status | Description |
|:-:|-----|:------:|-------------|
| 1 | **Options sizing uses full portfolio** | ✅ FIXED | Added `requested_quantity` to TargetWeight, router uses it for options |
| 2 | **`_pending_num_contracts` ignored** | ✅ FIXED | Now passed via `requested_quantity` field in TargetWeight |
| 3 | **Insufficient margin for options** | ✅ FIXED | Margin check improved for all options (not just QQQ) |

#### 🟠 HIGH - Phase B Complete ✅

| # | Bug | Status | Description |
|:-:|-----|:------:|-------------|
| 4 | **Naked options vs Debit Spreads** | ✅ FIXED | V2.3 Debit Spreads implemented (Bull Call/Bear Put based on regime) |
| 5 | **Intraday mode strategy mismatch** | ✅ FIXED | Intraday=single-leg (0-5 DTE), Swing=debit spreads (10-21 DTE) |

#### 🔴 CRITICAL - V2.3.3 Part 3 Fixes ✅ COMPLETE

| # | Bug | Status | Description |
|:-:|-----|:------:|-------------|
| 1 | **Trend Allocation Flattening** | ✅ FIXED | TrendEngine now uses `config.TREND_SYMBOL_ALLOCATIONS.get(symbol)` |
| 2 | **Closing Trade Bypass** | ✅ FIXED | MIN_TRADE_VALUE bypassed for `target_weight=0.0` closes |
| 3 | **Exit Race Condition** | ✅ FIXED | `_pending_intraday_exit` flag prevents duplicate signals |

#### 🟡 MEDIUM - After Architecture Stable

| # | Bug | Status | Description |
|:-:|-----|:------:|-------------|
| 6 | Greeks monitoring checks single leg | ⏳ LATER | L5 circuit breaker sees unhedged delta/theta - may trigger too often. |
| 7 | VIX direction uses open gap | ⏳ LATER | Uses `vix_current - vix_open`. Design implies intraday trend. |
| 8 | Option chain validation race | ⏳ LATER | Empty chain during warm-up silently skips entries. |

#### ✅ Previously Fixed

| Bug | Severity | Description |
|-----|:--------:|-------------|
| Kill switch never resets daily | 🔴 CRITICAL | Added `_kill_switch_handled_today` flag |
| Scheduler kill switch not reset | 🔴 CRITICAL | Added `scheduler.reset_daily()` at 09:25 |
| Kill switch doesn't liquidate options | 🔴 CRITICAL | Added options liquidation in `_handle_kill_switch` |
| Theta threshold too tight | 🟠 HIGH | Added `CB_THETA_SWING_CHECK_ENABLED=False` |
| Kill switch log spam | 🟡 MEDIUM | Handler now only runs once per day |
| Options 10:00 AM exact entry | 🟡 MEDIUM | Changed to 10:30 entry window start |
| TNA/FAS stops never trigger | 🔴 CRITICAL | `_on_fill()` registration fixed |
| Swing direction hardcoded CALL | 🟠 HIGH | Direction now uses regime score |
| VIX missing from regime score | 🟠 HIGH | V2.3: Added VIX as 20% weight |
| 4-Strategy complexity | 🟠 HIGH | V2.3: Debit Spreads only |
| 300+ Invalid orders per day | 🔴 CRITICAL | Added `_entry_attempted_today` flag |
| Log spam after 14:30 | 🟡 MEDIUM | Time window warning logged once |
| Kill switch not blocking options | 🔴 CRITICAL | Check `_kill_switch_handled_today` in scan |
| Wrong delta selection (ATM) | 🟠 HIGH | Swing=0.70δ, Intraday=0.30δ |

### Architect Audit Summary (2026-01-30)

**Document:** `docs/audits/stage2-codeaudit.md`

| Finding | Assessment | Action |
|---------|:----------:|--------|
| "Naked Options" vs "Debit Spreads" | ✅ Correct | Architecture decision needed |
| "Sizing Disconnect" (`_pending_num_contracts` ignored) | ✅ Correct | Fix in Phase A |
| "Intraday Mode Mismatch" | ⚠️ Partial | Verify V2.3 intraday design |
| Greeks Monitoring Failure | ✅ Correct | Lower priority than sizing |
| Option Chain Validation | ✅ Correct | Minor issue |
| VIX Direction Logic | ⚠️ Minor | Functional but not optimal |

### Recommended Fix Order

**Phase A: Make Backtest Runnable (Issues 1-3)**
1. Fix `target_weight` calculation - pass calculated `num_contracts` to router
2. Add `requested_quantity` field to TargetWeight - router uses it if present
3. Add margin check before options orders - skip if insufficient

**Phase B: Architecture Decision (Issues 4-5) ✅ COMPLETE**
- ~~Option A: Keep single-leg, fix sizing → Quick validation~~
- **Option B: Implement V2.3 debit spreads → Full design compliance** ✅ SELECTED

V2.3 Debit Spreads Implementation (2026-01-31):
- `SpreadPosition` dataclass for two-leg position tracking
- `select_spread_legs()` - ATM long leg + OTM short leg selection
- `check_spread_entry_signal()` - Regime-based direction (>60 Bull Call, <45 Bear Put)
- `check_spread_exit_signals()` - 50% profit target, 5 DTE exit, regime reversal
- `PortfolioRouter` spread metadata handling - creates two orders for spread legs
- `main.py` integration - spread entry/exit monitoring

**Phase C: Polish (Issues 6-8)**
After architecture is stable.

### V2.3 Regime + Options Simplification (2026-01-30)

| Component | Change | Status |
|-----------|--------|:------:|
| Regime Engine | Added VIX Level as 5th factor (20% weight) | ✅ Complete |
| Regime Weights | Rebalanced: Trend 30%, VIX 20%, RV 15%, Breadth 20%, Credit 15% | ✅ Complete |
| Options Engine | Simplified from 4 strategies to Debit Spreads only | ✅ Complete |
| Direction Logic | Regime-based: Score ≥50 = CALL, <50 = PUT | ✅ Complete |
| VIX Thresholds | Low <15, Normal <22, High <30, Extreme <40 | ✅ Complete |

**V2.3 Design Rationale:**
- VIX (implied vol) directly impacts option pricing - more relevant than realized vol alone
- 4-strategy portfolio (Debit/Credit spreads, ITM Long, Protective Puts) was overcomplex
- Credit spreads conflict with mean-reversion on trending QQQ
- Debit spreads: defined risk, no stop-loss needed, survive whipsaw
- Regime score naturally encapsulates market conditions for direction

### Ready to Start (Remaining from Roadmap)

| Ticket | Component | Size | Spec | Priority |
|--------|-----------|:----:|------|----------|
| BT-2 | Stage 2 Backtest (30 days) | S | backtest-results.md | **HIGH** |
| BT-3 | Stage 3 Backtest (3 months) | S | backtest-results.md | **HIGH** |
| BT-4 | Stage 4 Backtest (1 year) | M | backtest-results.md | **HIGH** |
| BT-5 | Stage 5 Backtest (5 years + crisis) | L | backtest-results.md | **HIGH** |
| OPT-6 | Options-specific daily loss limit | S | — | Medium |
| OPT-7 | Separate kill logic for intraday vs swing options | M | — | Medium |
| YLD-1 | SHV Ladder Strategy | M | V2-1-Critical-Fixes-Guide.md | Medium |
| YLD-2 | Daily Cash Sweep | S | V2-1-Critical-Fixes-Guide.md | Medium |
| YLD-3 | Monthly Interest Harvest | S | V2-1-Critical-Fixes-Guide.md | Low |

### In Progress

| Component | Owner | Branch | Started | Spec |
|-----------|-------|--------|---------|------|
| - | - | - | - | - |

### In Progress (V6.7 - Bug Fixes from Cross-Year Audit)

> **Audit Reports:**
> - `docs/audits/V6_6_Options_Engine_Audit_Report.md` (2022)
> - `docs/audits/V6_7_VASS_MICRO_Engine_Analysis_Report.md` (Cross-Year Analysis)
> - `docs/audits/logs/stage6.5/` (2015, 2017, 2022 raw logs)
>
> **Cross-Year Backtest Results:**
> | Year | Period | Trades | Win Rate | P&L | Max DD | Version |
> |:----:|:------:|:------:|:--------:|----:|-------:|:-------:|
> | 2015 | H1 | 74 | 40% (30W/45L) | -$1,177 | $9,500 | V6.7 |
> | 2017 | H1 | 45 | 47% (21W/24L) | +$23,758 | $12,630 | V6.6 |
> | 2022 | Jan-Feb | 102 | 22% | -49.08% | - | V6.6 |
>
> **Key Observations:**
> - 2017 Bull Market: Spreads profitable, Bug #1 (ASSIGNMENT_RISK_EXIT) fixed, Dir=NONE still persists
> - 2015 Choppy Market: PUT filtering fails (Greeks=0), Option Exercise events, Margin CB force liquidations
> - 2022 Bear Market: Worst performance - all bugs compounding
>
> **Critical Finding:** 12 bugs identified across all years (4 P0, 5 P1, 3 P2)

| ID | Bug # | Category | Fix | Years | Priority | Status |
|:--:|:-----:|:--------:|-----|:-----:|:--------:|:------:|
| V6.7-1 | #1 | Spreads | Fix ASSIGNMENT_RISK_EXIT: Calculate actual risk (net debit) not notional | 2022 | P0 | ✅ DONE |
| V6.7-2 | #2 | VIX Dir | Dir=NONE always - thresholds too tight (2015: 3276×, 2017: 3597×, 2022: 834×) | ALL | P0 | 🔲 TODO |
| V6.7-3 | #3 | Direction | CALLs on DOWN days - FOLLOW_MACRO fallback when Dir=NONE | ALL | P0 | 🔲 TODO |
| V6.7-4 | #4 | OCO | Options expired worthless - no OCO after roll | 2022 | P1 | 🔲 TODO |
| V6.7-5 | #5 | OCO | Invalid OCO orders after market hours (2015: 5, 2017: 2, 2022: 8) | ALL | P1 | 🔲 TODO |
| V6.7-6 | #6 | VASS | Spread selection too restrictive (2015: 92, 2017: 52, 2022: 274 rejections) | ALL | P1 | 🔲 TODO |
| V6.7-7 | #7 | Margin | MARGIN_CB force liquidation - consecutive margin calls (2015: 4 orders) | 2015 | P2 | 🔲 TODO |
| V6.7-8 | #8 | Stops | 50% stop hit rate high - symptom of #2/#3 wrong direction | ALL | P2 | Symptom |
| V6.7-9 | #9 | Regime | Spike Cap: Lower max score 45→38 to force DEFENSIVE during VIX spikes | 2022 | P0 | ✅ DONE |
| V6.7-10 | #10 | Regime | Breadth Decay: Relax thresholds (-10%/-15% → -2%/-4%) to actually trigger | 2022 | P0 | ✅ DONE |
| V6.7-11 | #11 | Greeks | PUT contracts fail filter (Greeks=0, Delta=0) - 33 failures in 2015 | 2015 | P1 | 🔲 TODO |
| V6.7-12 | #12 | Exercise | Option Exercise handling - ITM options exercised, QQQ stock assigned | 2015 | P2 | 🔲 TODO |

**Cross-Year Bug Analysis:**

| Bug | 2015 H1 | 2017 H1 | 2022 Jan-Feb | Root Cause |
|:---:|:-------:|:-------:|:------------:|------------|
| #1 ASSIGNMENT_RISK | N/A (V6.7) | ✅ Fixed | ❌ Broken | V6.7 fixed - spreads not immediately closed |
| #2 Dir=NONE | 3276× | 3597× | 834× | Thresholds too tight across all years |
| #3 Wrong Direction | FOLLOW_MACRO 64× | FOLLOW_MACRO 3× | FOLLOW_MACRO 62× | Fallback to Macro when Micro=NONE |
| #5 Invalid OCO | 5 orders | 2 orders | 8 orders | OCO placed after 18:00 (market closed) |
| #6 VASS Reject | 92 | 52 | 274 | Spread criteria too restrictive |
| #7 Margin CB | 4 orders | 0 | 5 | Consecutive margin calls force liquidation |
| #11 Greeks=0 | 33 PUT failures | 0 | 0 | PUT contracts missing Greeks in 2015 data |
| #12 Exercise | 3 events | 0 | 0 | ITM options exercised at expiration |

**Exit Type Distribution:**

| Exit Type | 2015 H1 | 2017 H1 | 2022 |
|-----------|:-------:|:-------:|:----:|
| EXPIRATION_HAMMER | 9 | 18 | - |
| STOP_EXIT | - | 0 | 22 |
| PROFIT_EXIT | - | 0 | 18 |
| Option Exercise | 3 | 0 | 0 |
| MARGIN_CB_LIQUIDATE | 4 | 0 | 5 |

**Bug #1 Fix Applied (V6.7):**
```python
# options_engine.py:4178-4219 - FIXED!
# Old: assignment_exposure = strike * 100 * num_contracts  # $772K naked
# New: actual_max_loss = spread.net_debit * 100 * num_contracts  # ~$7K spread
```

**Bug #9 & #10 Fixes Applied (V6.7 - Regime Hardening):**

Audit revealed regime stayed at 60-68 even when VIX hit 30+ in Jan 2022. Root causes:

1. **Spike Cap too lenient**: Capped at 45 (CAUTIOUS) instead of forcing DEFENSIVE
2. **Breadth Decay never triggered**: Thresholds of -10%/-15% unrealistic for RSP/SPY ratio moves

```python
# config.py - V6.7 Regime Hardening

# Bug #9: Spike Cap now forces DEFENSIVE
V53_SPIKE_CAP_MAX_SCORE = 38  # Was 45 (CAUTIOUS), now 38 (DEFENSIVE)

# Bug #10: Breadth Decay now triggers on realistic moves
V53_BREADTH_5D_DECAY_THRESHOLD = -0.02   # Was -0.10 (-10%), now -2%
V53_BREADTH_10D_DECAY_THRESHOLD = -0.04  # Was -0.15 (-15%), now -4%
```

**Expected behavior with fixes:**
- VIX spikes +28%+ → Score capped at 38 (DEFENSIVE) for 3 days
- RSP underperforms SPY by 2%+ in 5d → -5 point penalty
- RSP underperforms SPY by 4%+ in 10d → -8 point penalty (stacks)
- Net effect: Faster regime drop during selloffs → PUT direction for options

**Bug #2 Root Cause (Dir=NONE):**
- V6.6 STABLE zone: ±1%
- UVXY intraday range: -2.1% to +4.7% (mostly within ±1%)
- Result: Dir=NONE 834 times, RISING/FALLING 0 times
- FOLLOW_MACRO fallback: BULLISH 62 times, BEARISH 0 times

**Root Cause Analysis:**
```
Bug #2 (Dir=NONE) → Bug #3 (wrong direction) → Bug #8 (stops hit) → Bug #4 (expiry worthless)
```
Fixing Bug #2 should cascade fix to Bugs #3, #4, #8.

**Bug #11 Root Cause (Greeks=0 for PUTs - 2015 only):**
```
2015-01-02 10:30:00 INTRADAY_FILTER_FAIL: PUT | Total=530 | Dir=265 DTE=265 Greeks=0 Delta=0
```
- 33 PUT contract filter failures in 2015 H1
- Greeks data missing for PUT options in historical data
- UVXY conviction override triggers correctly but can't find valid PUT contracts
- May be a QuantConnect data issue specific to 2015 options data

**Bug #12 Root Cause (Option Exercise - 2015 only):**
```
2015-01-17T05:00:00Z | QQQ 150117C00099000 | Option Exercise | -$1,980
2015-01-17T05:00:00Z | QQQ | EXERCISE_LIQUIDATE | -$202,880 (QQQ stock assigned)
```
- ITM call options exercised at expiration
- System correctly triggered EXERCISE_LIQUIDATE to close stock position
- Need to review exercise handling and early exit before expiration week

**Statistics from Audit (Cross-Year):**

| Metric | 2015 H1 | 2017 H1 | 2022 Jan-Feb |
|--------|:-------:|:-------:|:------------:|
| Total Trades | 74 | 45 | 102 |
| Win Rate | 40% | 47% | 22% |
| Total P&L | -$1,177 | +$23,758 | -49.08% |
| Fees | $2,466 | $1,853 | - |
| Max Drawdown | $9,500 | $12,630 | - |
| Dir=NONE Count | 3,276 | 3,597 | 834 |
| Invalid OCO Orders | 5 | 2 | 8 |
| VASS Rejections | 92 | 52 | 274 |

---

### Done (V6.6 - VIX Direction Threshold Optimization)

> **Problem:** V6.5 2022H1 backtest revealed 76% of intraday signals blocked due to Dir=NONE
> **Root Cause:** VIX STABLE zone too wide (±2%), most market moves fell in STABLE
> **Fix:** Narrowed STABLE zone to ±1%, conservative spread construction relaxation
> **Review:** User-reviewed spread thresholds - kept conservative values to avoid gamma/assignment risk
> **Audit:** V6.6 isolation backtest revealed new critical bugs (see V6.7)

| Ticket | Component | Owner | Branch | Merged |
|--------|-----------|-------|--------|--------|
| V6.6-1 | VIX Direction: Narrow STABLE zone (±2% → ±1%) | VA | feature/va/v3.2-macro-regime-gate | 2026-02-08 |
| V6.6-2 | VIX Direction: Earlier detection thresholds (FALLING_FAST -5%→-3%, RISING +5%→+3%) | VA | feature/va/v3.2-macro-regime-gate | 2026-02-08 |
| V6.6-3 | VIX Direction: RISING_FAST threshold lowered (+10%→+6%) | VA | feature/va/v3.2-macro-regime-gate | 2026-02-08 |
| V6.6-4 | UVXY Conviction: Lower thresholds (±5%→±3%) | VA | feature/va/v3.2-macro-regime-gate | 2026-02-08 |
| V6.6-5 | VASS IV: Widen thresholds (LOW 15→16, HIGH 25→28) | VA | feature/va/v3.2-macro-regime-gate | 2026-02-08 |
| V6.6-6 | HIGH IV DTE: Extend max range (14→21, min kept at 7) | VA | feature/va/v3.2-macro-regime-gate | 2026-02-08 |
| V6.6-7 | Spread Construction: Conservative delta relaxation | VA | feature/va/v3.2-macro-regime-gate | 2026-02-08 |
| V6.6-8 | Tests: Update all threshold tests for V6.6 values | VA | feature/va/v3.2-macro-regime-gate | 2026-02-08 |
| V6.6-9 | Config: Disable ISOLATION_TEST_MODE for normal ops | VA | feature/va/v3.2-macro-regime-gate | 2026-02-08 |

**Config Changes:**
```
# VIX Direction (narrowed STABLE zone)
VIX_DIRECTION_FALLING_FAST = -3.0  # was -5.0
VIX_DIRECTION_FALLING = -1.0       # was -2.0
VIX_DIRECTION_STABLE_LOW = -1.0    # was -2.0
VIX_DIRECTION_STABLE_HIGH = 1.0    # was +2.0
VIX_DIRECTION_RISING = 3.0         # was +5.0
VIX_DIRECTION_RISING_FAST = 6.0    # was +10.0

# UVXY Conviction (lower thresholds)
MICRO_UVXY_BEARISH_THRESHOLD = 0.03  # was 0.05
MICRO_UVXY_BULLISH_THRESHOLD = -0.03 # was -0.05

# VASS IV Classification (wider bands)
VASS_IV_LOW_THRESHOLD = 16   # was 15
VASS_IV_HIGH_THRESHOLD = 28  # was 25
VASS_HIGH_IV_DTE_MAX = 21    # was 14 (DTE_MIN kept at 7 - gamma risk)

# Spread Construction (conservative relaxation)
SPREAD_WIDTH_TARGET = 4.0           # was 5.0 (MIN kept at 2.0 - risk/reward)
SPREAD_LONG_LEG_DELTA_MIN = 0.45    # was 0.50 (MAX kept at 0.85)
SPREAD_SHORT_LEG_DELTA_MAX = 0.52   # was 0.50 (MIN kept at 0.10)
```

**Test Status:** 1333 passed, 9 skipped ✅

**User Review (Conservative Adjustments):**
| Parameter | Initial Proposal | Final Value | Rationale |
|-----------|-----------------|-------------|-----------|
| VASS_HIGH_IV_DTE_MIN | 5 | 7 (kept) | High gamma risk near expiry |
| SPREAD_WIDTH_MIN | 1.0 | 2.0 (kept) | $1 spreads have poor risk/reward |
| SPREAD_WIDTH_TARGET | 3.0 | 4.0 | $3 too narrow for QQQ |
| SPREAD_LONG_LEG_DELTA_MIN | 0.40 | 0.45 | 0.40 too far OTM |
| SPREAD_LONG_LEG_DELTA_MAX | 0.90 | 0.85 (kept) | 0.90 deep ITM expensive |
| SPREAD_SHORT_LEG_DELTA_MIN | 0.08 | 0.10 (kept) | 0.08 collects minimal premium |
| SPREAD_SHORT_LEG_DELTA_MAX | 0.55 | 0.52 | 0.55 risks early assignment |

### Done (V6.5 - Options Isolation Backtest Audit)

> **Audit:** `docs/audits/V6_5_2022H1_OptionsIsolation_audit.md`
> **Backtest Period:** 2022 H1 (Options Engine Isolated)
> **Critical Finding:** Gamma Pin Exit order spam - 184 BUY orders instead of 1 SELL

| Ticket | Component | Owner | Commit | Merged |
|--------|-----------|-------|--------|--------|
| V6.5-1 | GAMMA_PIN_EXIT: Add spread_short_leg_quantity to metadata | VA | feature/va/v3.0-hardening | 2026-02-08 |
| V6.5-2 | GAMMA_PIN_EXIT: Add once-per-position trigger flag | VA | feature/va/v3.0-hardening | 2026-02-08 |
| V6.5-3 | GAMMA_PIN_EXIT: Reset flag on spread position removal | VA | feature/va/v3.0-hardening | 2026-02-08 |
| V6.5-4 | INTRADAY_UNKNOWN: Add DEBIT_MOMENTUM + PROTECTIVE_PUTS to strategy_names | VA | feature/va/v3.0-hardening | 2026-02-08 |
| V6.5-5 | ASSIGNMENT_RISK_EXIT: Add spread_close_short + spread_short_leg_quantity | VA | feature/va/v3.0-hardening | 2026-02-08 |
| V6.5-6 | Delta-Scaled ATR Stops: Dynamic stop based on ATR × delta | VA | feature/va/v3.0-hardening | 2026-02-08 |

**Root Cause Analysis:**
- Gamma pin exit fired 184 times on Feb 8, 2022 (once per minute)
- Missing `spread_short_leg_quantity` in metadata caused combo close path to be skipped
- Router interpreted positive `requested_quantity=20` as BUY instead of SELL
- No flag to prevent re-triggering after first signal

**V6.5-6 Delta-Scaled ATR Stops:**
- **Problem:** Intraday stops hit too quickly in high VIX (8/8 trades stopped in 15 min avg)
- **Solution:** `stop_distance = ATR_MULTIPLIER × ATR × abs(delta)`
- **Config:** `OPTIONS_ATR_STOP_MULTIPLIER=1.5`, floor=20%, cap=50%
- **Files:** `config.py`, `main.py` (QQQ ATR), `options_engine.py`

### Done (V6.4 - Options Engine Hardening)

| Ticket | Component | Owner | Commit | Merged |
|--------|-----------|-------|--------|--------|
| V6.4-1 | BEAR_PUT Entry Gate (3% OTM min) | VA | feature/va/v3.0-hardening | 2026-02-08 |
| V6.4-2 | UVXY Threshold lowered (8%→5%) | VA | feature/va/v3.0-hardening | 2026-02-08 |
| V6.4-3 | Isolation Mode docs (CLAUDE.md + guide) | VA | feature/va/v3.0-hardening | 2026-02-08 |
| V6.4-4 | Engine Isolation Mode implementation | VA | feature/va/v3.0-hardening | 2026-02-08 |
| V6.4-5 | DEBIT_MOMENTUM delta wiring (0.45-0.65) | VA | feature/va/v3.0-hardening | 2026-02-08 |
| V6.4-6 | DEBIT_MOMENTUM time gate (10:00-13:30) | VA | feature/va/v3.0-hardening | 2026-02-08 |
| V6.4-7 | PROTECTIVE_PUTS crisis fix (no score required) | VA | feature/va/v3.0-hardening | 2026-02-08 |

### Done (V6.3 - VIX Fix)

| Ticket | Component | Owner | Commit | Merged |
|--------|-----------|-------|--------|--------|
| V6.3-1 | VIX Data Source Fix (CBOE vs SecDef) | VA | feature/va/v3.0-hardening | 2026-02-07 |
| V6.3-2 | OptionDirection Enum Dedup | VA | feature/va/v3.0-hardening | 2026-02-07 |
| V6.3-3 | FOLLOW_MACRO Logic Fix | VA | feature/va/v3.0-hardening | 2026-02-07 |

### Archived

| Component | Owner | Branch | Started | Spec |
|-----------|-------|--------|---------|------|
| QC Cloud Backtest Validation | VA | testing/va/stage1-1day-backtest | 2026-01-30 | docs/audits/backtest-results.md |

### Done (V2 Phase 1)

| Ticket | Component | Owner | Commit | Merged |
|--------|-----------|-------|--------|--------|
| TRE-1 | MA200 + ADX Signal | VA | develop | 2026-01-26 |
| TRE-2 | Trailing Stop Enhancement | VA | develop | 2026-01-26 |
| TRE-3 | Trend Engine V2 Tests | VA | develop | 2026-01-26 |
| RSK-1 | 5-Level Circuit Breaker | VA | develop | 2026-01-26 |
| RSK-3 | Risk Engine V2 Tests | VA | develop | 2026-01-26 |

### Done (V2 Phase 2)

| Ticket | Component | Owner | Commit | Merged |
|--------|-----------|-------|--------|--------|
| MRE-3 | VIX Data Feed | VA | develop | 2026-01-26 |
| MRE-1 | VIX Regime Integration | VA | develop | 2026-01-26 |
| MRE-2 | Regime-Adjusted Parameters | VA | develop | 2026-01-26 |
| MRE-4 | Mean Reversion V2 Tests | VA | develop | 2026-01-26 |

### Done (V2 Phase 3 - Options Engine)

| Ticket | Component | Owner | Commit | Merged |
|--------|-----------|-------|--------|--------|
| OPT-1 | Options Engine Core (4-factor entry scoring) | VA | develop | 2026-01-26 |
| OPT-2 | Entry Score Model (ADX, Momentum, IV, Liquidity) | VA | develop | 2026-01-26 |
| OPT-3 | OCO Order Manager (`execution/oco_manager.py`) | VA | develop | 2026-01-26 |
| OPT-4 | Options Engine Tests (comprehensive suite) | VA | develop | 2026-01-26 |
| RSK-2 | Greeks Monitoring Integration | VA | develop | 2026-01-26 |
| OPT-5 | Options Wiring Audit (DTE fix, intraday scan, Greeks monitor) | VA | v2.1.2 | 2026-01-27 |

### Done (V2 Phase 4 - Integration & Orchestration)

| Ticket | Component | Owner | Commit | Merged |
|--------|-----------|-------|--------|--------|
| INT-1 | VIX Data Feed wired to main.py | VA | 60ebf55 | 2026-01-26 |
| INT-2 | Options Engine wired to main.py | VA | 60ebf55 | 2026-01-26 |
| INT-3 | OCO Manager wired to main.py | VA | 60ebf55 | 2026-01-26 |
| ORC-1 | Signal Aggregation (70/20-30/0-10 Core-Satellite) | VA | 60ebf55 | 2026-01-26 |
| ORC-2 | Rebalancing Logic (drift > 5%) | VA | 60ebf55 | 2026-01-26 |
| TST-1 | V2 Test Plan + Integration Tests (63 new tests) | VA | 381ac7c | 2026-01-26 |
| TST-2 | Scenario Tests Implementation (25 tests with correct APIs) | VA | v2.1.3 | 2026-01-27 |
| DOC-1 | Architecture Diagrams Update (6 files, Core-Satellite) | VA | 242376f | 2026-01-27 |

### In Review

| Component | Owner | PR | Reviewer |
|-----------|-------|---:|----------|
| _None_ | | | |

### Ready to Start

| Component | Assigned | Size | Spec |
|-----------|----------|:----:|------|
| _Phase 6 Complete - See Next Steps above_ | | | |

### Done (Phase 6)

| Component | Owner | Commit | Merged |
|-----------|-------|--------|--------|
| main.py | VA | 7c0baf0 | 2026-01-25 |
| docs/MAIN_PY_IMPLEMENTATION.md | VA | 7c0baf0 | 2026-01-25 |

### Done (Phase 5)

| Component | Owner | PR | Merged |
|-----------|-------|---:|--------|
| execution/execution_engine.py | VA | #40 | 2026-01-26 |
| persistence/state_manager.py | VA | #40 | 2026-01-26 |
| scheduling/daily_scheduler.py | VA | #40 | 2026-01-26 |

### Done (Phase 4)

| Component | Owner | PR | Merged |
|-----------|-------|---:|--------|
| portfolio/exposure_groups.py | VA | #36 | 2026-01-25 |
| portfolio/portfolio_router.py | VA | #37 | 2026-01-25 |
| engines/risk_engine.py | VA | #38 | 2026-01-25 |

### Done (Phase 3)

| Component | Owner | PR | Merged |
|-----------|-------|---:|--------|
| engines/cold_start_engine.py | VA | #26 | 2026-01-25 |
| engines/trend_engine.py | VA | #27 | 2026-01-25 |
| engines/mean_reversion_engine.py | VA | #28 | 2026-01-25 |
| TYPE_CHECKING guards (all engines) | VA | #29 | 2026-01-25 |
| docs: requirements-dev.txt | VA | #30 | 2026-01-25 |
| engines/hedge_engine.py | VA | #32 | 2026-01-25 |
| engines/yield_sleeve.py | VA | #33 | 2026-01-25 |

### Done (Phase 1)

| Component | Owner | PR | Merged |
|-----------|-------|---:|--------|
| config.py | VA | #18 | 2026-01-25 |
| models/enums.py | VA | — | 2026-01-25 |
| models/target_weight.py | VA | — | 2026-01-25 |
| utils/calculations.py | VA | #22 | 2026-01-25 |

---

## Phase 2 - Core Engines (Complete ✓)

### Done (Phase 2)

| Component | Owner | PR | Merged |
|-----------|-------|---:|--------|
| engines/regime_engine.py | VA | #24 | 2026-01-25 |
| engines/capital_engine.py | VA | #25 | 2026-01-25 |

### Deferred

| Component | Assigned | Size | Spec | Notes |
|-----------|----------|:----:|------|-------|
| CI: Enforce coverage threshold (70%) | -- | S | — | Deferred to Phase 4 |

---

## Phase 3 - Strategy Engines (Complete ✓)

> See "Done (Phase 3)" in Current Sprint section.

---

## Phase 4 - Coordination (Complete ✓)

> See "Done (Phase 4)" in Current Sprint section.

---

## Phase 5 - Execution & State (Complete ✓)

> See "Done (Phase 5)" in Current Sprint section.

---

## Phase 6 - Integration (Complete ✓)

> See "Done (Phase 6)" in Current Sprint section.
>
> **Implementation Summary:**
> - `main.py` - QCAlgorithm entry point (1,638 lines)
> - Hub-and-Spoke architecture with PortfolioRouter as central hub
> - All engines, infrastructure, and scheduled events wired together
> - Full documentation in `docs/MAIN_PY_IMPLEMENTATION.md`

---

## Feature Branches (Not Merged)

> **Purpose:** Track self-contained features developed on separate branches, intentionally kept out of `develop` to avoid complexity in the core trading logic.

### `feat/backtest-reporting` — Backtest Analysis Module

**Branch:** `feat/backtest-reporting` (pushed to origin)

**Why Separate?** Reporting/monitoring adds complexity that's not needed for core trading logic. Keeping it isolated allows backtesting the trading system without the overhead, and merging later when ready for production monitoring.

**Contents (7 files, 1,974 lines, 20 tests):**

| File | Purpose |
|------|---------|
| `reporting/__init__.py` | Module exports |
| `reporting/trade_record.py` | `TradeRecord`, `DailyEquity` dataclasses |
| `reporting/performance_metrics.py` | `PerformanceMetrics` with 30+ fields |
| `reporting/metrics_engine.py` | Core engine: Sharpe, Sortino, drawdown, win rate |
| `reporting/chart_manager.py` | QC charting: equity curve, drawdown, regime |
| `reporting/csv_exporter.py` | Trade history export to CSV |
| `tests/test_metrics_engine.py` | 20 unit tests for all components |

**Capabilities:**
- Sharpe/Sortino ratio calculation (annualized)
- Continuous drawdown tracking (max, current, average)
- Win rate, profit factor, expectancy
- Trade entry/exit recording with P&L
- Daily equity snapshots
- CSV export for external analysis (Excel, etc.)
- State persistence via ObjectStore
- QC RuntimeStatistics panel integration

**To Use:**
```bash
git checkout feat/backtest-reporting   # Get the module
git checkout develop                    # Return to clean trading logic
```

**To Merge (when ready):**
```bash
git checkout develop
git merge feat/backtest-reporting
# Then integrate into main.py per plan in .claude/plans/
```

**Created:** 2026-01-27 | **Tests:** 20 passed | **Status:** Complete, awaiting integration decision

---

## Ideas Backlog

> **Purpose:** Capture ideas, enhancements, and "nice-to-haves" that are out of scope for current phases but worth remembering.
>
> **Rules:**
> - Add ideas anytime - don't let them get lost
> - Categorize by type (Enhancement, Research, Tooling, etc.)
> - Move to a Phase when prioritized for implementation
> - Delete if no longer relevant

### Future Enhancements

| Idea | Category | Notes | Added |
|------|----------|-------|-------|
| Intraday options trading (QQQ calls/puts) | Options | Core logic: QQQ averages 1% daily move. Enter at top/bottom. Requires Phase 5+ complete. | 2026-01-25 |
| Report generation and monitoring | Operations | Daily/weekly performance reports, alerts, dashboards. Essential for live trading oversight. | 2026-01-25 |
| Web UI for system management | Operations | Dashboard to view positions, regime state, trigger manual overrides. Consider after v1.0 stable. | 2026-01-25 |

### Research / Exploration

| Idea | Category | Notes | Added |
|------|----------|-------|-------|
| Evaluate adding short positions | Strategy | Currently using PSQ (inverse ETF) as hedge. Research if direct shorting improves returns in sustained downtrends. | 2026-01-25 |
| Comprehensive options strategy matrix | Options | Multiple strategies (spreads, straddles, etc.) based on conditions. Needs separate architecture. Treat as separate project sharing regime engine. | 2026-01-25 |
| Crypto trading with same logic | New Market | Regime/MR/trend logic portable. Challenges: 24/7 market, different infra, higher volatility. Fork-and-modify approach. | 2026-01-25 |

### Technical Debt / Improvements

| Idea | Category | Notes | Added |
|------|----------|-------|-------|
| CI: Enforce coverage threshold (70%) | Tooling | Deferred from Phase 2 | 2026-01-24 |

---

## Next Steps - Production Readiness

> **Current State:** System is feature-complete. QC Cloud backtest validation in progress.
>
> **Stage 1 Backtest:** PASS ✅ (2026-01-30)

### Staged Backtest Validation (Current Focus)

| Stage | Duration | Status | Next Action |
|:-----:|----------|:------:|-------------|
| 1 | 1 day | ✅ PASS | Complete |
| 2 | 30 days | 🟡 LOGIC OK | Analyze logs for performance tuning |
| 3 | 3 months | ⏳ | Run after Stage 2 analysis |
| 4 | 1 year | — | After Stage 3 |
| 5 | 5 years + crisis | — | After Stage 4 |

### After Staged Backtests

1. **Paper Trading** (2 weeks minimum)
   - Deploy to QC paper environment
   - Monitor daily operations
   - Validate state persistence across restarts

2. **Production Deployment**
   - Small deployment ($10-20K) first
   - Full deployment ($50K+) after validation

### Before Live Trading

| Task | Status | Notes |
|------|--------|-------|
| Stage 1 backtest (1 day) | ✅ | PASS - 2026-01-30 |
| Stage 2 backtest (30 days) | 🟡 | Logic OK (-9.67%, 8 orders) - needs perf tuning |
| Stage 3 backtest (3 months) | ⏳ | Required |
| Stage 4 backtest (1 year) | ⏳ | Required |
| Stage 5 backtest (5 years + crisis) | ⏳ | Required |
| Paper trading (2 weeks) | ⏳ | Required |
| Monitoring/alerting setup | ⏳ | Recommended |
| Small deployment ($10-20K) | ⏳ | First live phase |
| Full deployment ($50K+) | ⏳ | After small size proves out |

---

## Archive

<details>
<summary>Phase 0 - Pre-Development & Foundation (Complete)</summary>

### Documentation & Setup (2026-01-24)

| Task | Owner | Completed |
|------|-------|-----------|
| Session management setup | VA | 2026-01-24 |
| developer-guide-claude.md rewrite | VA | 2026-01-24 |
| Git workflow established | VA | 2026-01-24 |
| WORKBOARD.md created | VA | 2026-01-24 |

### CI/CD & Infrastructure (2026-01-25)

| Task | Owner | Completed |
|------|-------|-----------|
| GitHub Actions CI workflow (`.github/workflows/test.yml`) | VA | 2026-01-25 |
| Architecture boundary tests (`tests/test_architecture_boundaries.py`) | VA | 2026-01-25 |
| QC compliance tests (print, sleep, datetime.now) | VA | 2026-01-25 |
| Branch protection for `main` and `develop` | VA | 2026-01-25 |
| CONTRIBUTING.md created | VA | 2026-01-25 |
| PR template (`.github/PULL_REQUEST_TEMPLATE.md`) | VA | 2026-01-25 |
| Branch protection docs (`docs/GITHUB-BRANCH-PROTECTION.md`) | VA | 2026-01-25 |
| Lean CLI workspace integration | VA | 2026-01-25 |
| QC cloud backtest verification | VA | 2026-01-25 |
| CI violation detection verified | VA | 2026-01-25 |

### Infrastructure Hardening (2026-01-24)

| Task | Owner | Completed |
|------|-------|-----------|
| Fixed CI pipeline to fail correctly (no silent skips) | VA | 2026-01-24 |
| Created `tests/scenarios/__init__.py` | VA | 2026-01-24 |
| Added explicit `@pytest.mark.skip` to all placeholder tests | VA | 2026-01-24 |
| Created `pyproject.toml` (unified tool configuration) | VA | 2026-01-24 |
| Created `.pre-commit-config.yaml` (pre-commit hooks) | VA | 2026-01-24 |
| Created `Makefile` (workflow automation: make setup, make test, make branch) | VA | 2026-01-24 |
| Updated CONTRIBUTING.md with local dev setup | VA | 2026-01-24 |
| Added "Golden Rule" section to CONTRIBUTING.md (branch protection) | VA | 2026-01-24 |
| Added pre-commit hook to block commits to main/develop | VA | 2026-01-24 |
| Added test scaffolds for all Phase 2-5 components | VA | 2026-01-24 |
| Fixed Black formatting in models/enums.py and models/target_weight.py | VA | 2026-01-24 |

### Documentation Automation (2026-01-24)

| Task | Owner | Completed |
|------|-------|-----------|
| Created `docs/DOCUMENTATION-MAP.md` (code-to-doc mapping) | VA | 2026-01-24 |
| Added "Documentation Update Requirements" section to CLAUDE.md | VA | 2026-01-24 |
| Updated CLAUDE.md repository structure with new files | VA | 2026-01-24 |
| Updated PROJECT-STRUCTURE.md with new files and counts | VA | 2026-01-24 |
| Updated docs/00-table-of-contents.md with DOCUMENTATION-MAP.md | VA | 2026-01-24 |

### Developer Experience Improvements (2026-01-24)

| Task | Owner | Completed |
|------|-------|-----------|
| Created `.vscode/settings.json` (IDE configuration) | VA | 2026-01-24 |
| Created `.editorconfig` (cross-editor consistency) | VA | 2026-01-24 |
| Added `make verify` command (setup verification) | VA | 2026-01-24 |
| Added `make validate-config` command | VA | 2026-01-24 |
| Added `make phase1-check` command | VA | 2026-01-24 |
| Added test fixture documentation to CONTRIBUTING.md | VA | 2026-01-24 |
| Added skip marker explanation to CONTRIBUTING.md | VA | 2026-01-24 |
| Added pre-commit hook troubleshooting to CONTRIBUTING.md | VA | 2026-01-24 |

### Process Standards (2026-01-24)

| Task | Owner | Completed |
|------|-------|-----------|
| Added commit message standards to CONTRIBUTING.md | VA | 2026-01-24 |
| Added PR review guidelines to CONTRIBUTING.md | VA | 2026-01-24 |
| Added Definition of Done to WORKBOARD.md | VA | 2026-01-24 |
| Principal Architect review completed | VA | 2026-01-24 |

**Documentation Automation Process:**
- Claude consults `docs/DOCUMENTATION-MAP.md` after any code change
- Maps code files → documentation that needs updating
- Documentation updates included in same commit/PR as code
- No developer action required - Claude handles automatically

**CI Capabilities:**
- ✅ Catches engines placing orders (architecture violation)
- ✅ Catches `print()` statements (QC compliance)
- ✅ Catches `time.sleep()` calls (QC compliance)
- ✅ Catches `datetime.now()` usage (QC compliance)
- ✅ Blocks PR merge if tests fail
- ✅ Skipped tests allowed, but failures fail build
- ✅ Linting only runs on files with actual content

**Pre-commit Hooks:**
- ✅ Black (code formatting)
- ✅ isort (import sorting)
- ✅ No print() in source files
- ✅ No datetime.now() usage
- ✅ No time.sleep() usage
- ✅ Blocks commits to main/develop branches

**Workflow Automation (Makefile):**
- `make setup` - Create venv, install deps, install pre-commit
- `make test` - Run all tests
- `make lint` - Run black and isort
- `make branch name=feature/va/my-feature` - Create feature branch from develop

**Lean CLI Integration:**
- Workspace: `../lean-workspace/AlphaNextGen/`
- Verified: `lean cloud push` + `lean cloud backtest` working
- Test backtest: $50,000 → $50,147 (+0.29%)

</details>

---

## Definition of Done

A task is **not complete** until ALL of the following are true:

### Code Quality
- [ ] Code implements requirements from the spec document
- [ ] All unit tests pass (`pytest tests/test_<component>.py -v`)
- [ ] All architecture tests pass (`pytest tests/test_architecture_boundaries.py -v`)
- [ ] No linting errors (`make lint` or `black --check`)
- [ ] Type hints added for all function signatures
- [ ] Docstrings added for public functions

### Safety & Compliance
- [ ] No `print()` statements (use `self.algorithm.Log()`)
- [ ] No `datetime.now()` (use `self.algorithm.Time`)
- [ ] No `time.sleep()` (use scheduling)
- [ ] No hardcoded values (use `config.py`)
- [ ] Engines do NOT place orders (emit TargetWeight only)

### Documentation
- [ ] `docs/DOCUMENTATION-MAP.md` consulted
- [ ] All affected documentation updated
- [ ] WORKBOARD.md task moved to "Done" section

### Review
- [ ] PR created targeting `develop`
- [ ] CI passes (all green checks)
- [ ] Self-reviewed the diff
- [ ] Approval received (if targeting `main`)

---

## Interface Change Protocol

If you need to change a shared interface (TargetWeight, RegimeState, etc.):

1. Notify collaborator: "I need to change [interface]. Pause related work."
2. Make the change in a normal feature branch
3. Update all affected components in SAME branch
4. PR, review, merge
5. Notify: "[Interface] change merged. Resume work."

Expected frequency: 1-2 times total project lifetime.

---

## Quick Reference

**Sizes:** S = <100 lines | M = 100-300 lines | L = 300+ lines

**Workflow:**
```
1. Pick from "Ready to Start" -> Move to "In Progress"
2. Create branch: feature/<initials>/<component>
3. Code -> PR -> Move to "In Review"
4. After merge -> Move to "Done"
5. Delete feature branch (local + remote)
```

**Branch format:** `feature/va/config-py` or `feature/vd/regime-engine`

**New Developer Setup:**
```bash
git clone <repo> && cd alpha-nextgen
python3.11 -m venv venv && source venv/bin/activate
pip install -r requirements.lock
pre-commit install
pytest tests/test_smoke_integration.py -v
```

---

---

## V2.28 Risk Tuning Log (2026-02-04)

> **Purpose:** Track all governor/kill switch threshold changes across backtest runs.
> Use this to optimize for different market regimes without losing history.

### V2.28.0 — Initial Bug Fixes (Commit: `2527f4f`)

**5 bugs fixed from V2.27 Q1 2022 diagnosis (-33.1% DD):**

1. Spread-aware Governor SHUTDOWN liquidation (close short leg first)
2. Gate intraday options when governor < threshold
3. Early exercise guard (ITM single-leg at DTE ≤ 2)
4. Record intraday option results for Win Rate Gate
5. Raise governor recovery threshold

### Threshold History

| Parameter | V2.27 (original) | V2.28.0 | V2.28.1 (tightened) | Notes |
|-----------|:-:|:-:|:-:|-------|
| **KS Tier 1** | 3% | 3% | **2%** | Daily loss → REDUCE |
| **KS Tier 2** | 5% | 5% | **4%** | Daily loss → TREND_EXIT |
| **KS Tier 3** | 8% | 8% | **6%** | Daily loss → FULL_EXIT |
| **Governor -75%** | -5% | -5% | **-3%** | First scaling step |
| **Governor -50%** | -10% | -10% | **-6%** | Half allocation |
| **Governor -25%** | -15% | -15% | **-10%** | Quarter allocation |
| **Governor SHUTDOWN** | -20% | -20% | **-15%** | Full cash mode |
| **Governor Recovery** | 5% | 10% | **12%** | Recovery from trough before step-up |
| **Intraday Options Gate** | > 0% | ≥ 0.75 | **= 1.0** | Governor scale needed for intraday options |

### Backtest Results by Configuration

| Config | Period | Return | Max DD | Orders | Verdict |
|--------|--------|:------:|:------:|:------:|---------|
| V2.27 | Q1 2022 | -10.4% | **-33.1%** | 209 | Spreads stuck, governor didn't protect |
| V2.28.0 | Q1 2022 | -10.4% | **-10.4%** | 22 | DD cut by 22pp, governor working |
| V2.25 | 2015 Full | ~-42% | **-42%** | ~285 | 19 kill switches, options bled capital |
| V2.28.0 | 2015 Full | -78.9% | **-82.3%** | 222 | Governor blocked recovery, worse than V2.25 |
| V2.28.0 | 2025 Full | -37.2% | **-71.3%** | 443 | Bull market, still losing — core spread problem |
| V2.28.1 | Q1 2022 | -9.3% | **-9.3%** | 15 | Marginal improvement over V2.28.0, fewer trades |
| V2.28.1 | 2015 Full | -9.1% | **-9.3%** | 22 | DD cut from -82% to -9.3%, governor keeping algo in cash |
| V2.28.1 | 2025 Full | -4.9% | **-6.5%** | 31 | DD cut from -71% to -6.5%, but missed bull rally (QQQ +25%) |

### Key Insight

The governor/KS **trade off bear protection vs bull recovery:**
- Tight triggers → great in bear (Q1 2022: -10% DD) but cripple bull recovery (2015: -79%)
- Loose triggers → allow bull profits but amplify bear losses (V2.27 Q1 2022: -33% DD)
- **The core problem may be the spread strategy itself**, not the safety triggers. Spreads show 94% loss rate in 2015 and net-negative in all tested periods.

### Regime-Specific Optimization Notes

| Regime | Ideal KS Tier 1 | Ideal Governor Start | Notes |
|--------|:---:|:---:|-------|
| Bear (Q1 2022) | 2-3% | -3% to -5% | Cut losses early, go to cash |
| Choppy/Flat (2015) | 5%+ | -10%+ | Avoid whipsaw, let positions breathe |
| Bull (2024-2025) | 5%+ | -10%+ | Stay invested, ride momentum |
| Crisis (Aug 2015) | 2-3% | -3% | Immediate protection |

> **Next Steps:** Consider regime-adaptive thresholds — use VIX level or regime score
> to dynamically adjust KS/governor sensitivity instead of static values.

*Last Updated: 04 February 2026 (V2.30 All-Weather StartupGate)*
