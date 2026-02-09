# V6.10 COMPREHENSIVE FIX PLAN

**Date:** 2026-02-09
**Based on:** V6.9 Audit of 2015, 2017, 2021-2022 backtests
**Status:** Ready for Implementation

---

## EXECUTIVE SUMMARY

| Metric | Current (V6.9) | Target (V6.10) |
|:-------|:--------------:|:--------------:|
| Assignment Losses | -$150,953 | $0 |
| Dir=NONE | 76-98% | <50% |
| Dir=PUT | 7% | ~25% |
| BEAR Spreads Executed | 0 | >50 |
| Margin Errors | 1,781 | <100 |
| Signal→Execution Rate | 5% | >25% |
| Choppy Year P&L (2015) | -$19,925 | Positive |

**Total Changes:** 18 config parameters + 5 code modifications

---

## PROBLEM CATEGORIES & ROOT CAUSES

| Category | Problems | Root Causes |
|:---------|:---------|:------------|
| **A. Assignment Risk** | -$150k+ losses across years | Overnight gaps, ITM shorts not closed |
| **B. Direction Generation** | Dir=NONE 76-98%, CALL bias | RC-1, RC-2, RC-3, RC-4 |
| **C. Spread Construction** | 0 bearish spreads, VASS rejections | RC-5, RC-6 |
| **D. Execution Pipeline** | 581→30 signals executed | RC-7, RC-8, margin errors |
| **E. Risk/Reward** | 48% win rate but negative P&L | Spread width, stop/target ratio |

---

## PHASE 1: ASSIGNMENT PREVENTION (P0 — CRITICAL)

### Problem

5 assignments in 2021-2022 = **-$139,313** (wiped out +$72,720 profit)
1 assignment in 2015 = **-$11,640**

### Root Causes

- Short legs going ITM overnight
- No mandatory DTE close
- ITM exit threshold too late (1%)
- Spread width too narrow ($3) — insufficient buffer for gaps

### Solution: 4-Layer Defense

| Layer | Parameter | Current | New | Purpose |
|:-----:|:----------|:-------:|:---:|:--------|
| 1 | `SPREAD_FORCE_CLOSE_DTE` | None | **1** | Nuclear option: close ALL spreads at DTE=1 |
| 2 | `PREMARKET_ITM_CHECK_ENABLED` | None | **True** | Check shorts at 09:25 before gap risk |
| 3 | `SHORT_LEG_ITM_EXIT_THRESHOLD` | 0.01 | **0.005** | Exit when 0.5% ITM (was 1%) |
| 4 | `SPREAD_WIDTH_MIN` | 3.0 | **5.0** | Wider spreads survive larger gaps |

### Code Changes Required

**Location:** `options_engine.py` → `_check_assignment_risk()`
**Change:** Add DTE=1 force close logic

**Location:** `main.py` or `options_engine.py`
**Change:** Add 09:25 pre-market ITM check scheduled event

### Validation

- **Test:** 2021-2022 backtest
- **Success:** 0 QQQ underlying trades (assignments)

---

## PHASE 2: DIRECTION GENERATION — MICRO ENGINE (P1)

### Problem

- Dir=NONE: 76% (2021-22), 89.7% (2015), **98.6% (2017)**
- Dir=PUT: Only 7% even in 2022 bear market
- 390 signals blocked by "conviction not extreme"

### Root Causes Mapped

| RC | Root Cause | Parameter | Current | Problem |
|:--:|:-----------|:----------|:-------:|:--------|
| RC-1 | STABLE band too wide | VIX_STABLE_BAND_* | ±0.5-2% | Most UVXY moves fall within |
| RC-2 | Conviction extreme too high | MICRO_UVXY_CONVICTION_EXTREME | 7% | 5-7% moves blocked |
| RC-3 | BEARISH threshold too high | MICRO_UVXY_BEARISH_THRESHOLD | +4% | Rare PUT signals |
| RC-4 | BULLISH threshold too low | MICRO_UVXY_BULLISH_THRESHOLD | -5% | Only extreme drops = CALL |
| RC-7 | VIX floor too high | INTRADAY_DEBIT_FADE_VIX_MIN | 13.5 | 2017 (VIX 11) blocked |
| RC-8 | QQQ fallback too strict | INTRADAY_QQQ_FALLBACK_MIN_MOVE | 0.70% | VIX STABLE rarely trades |

### Solution: Parameter Tuning

| Parameter | Current | New | Expected Impact |
|:----------|:-------:|:---:|:----------------|
| `VIX_STABLE_BAND_LOW` | 0.005 | **0.003** | ±0.3% for VIX<15 (more signals) |
| `VIX_STABLE_BAND_HIGH` | 0.020 | **0.010** | ±1.0% for VIX>25 (high VIX still trades) |
| `MICRO_UVXY_CONVICTION_EXTREME` | 0.07 | **0.05** | 5% move = conviction (was 7%) |
| `MICRO_UVXY_BEARISH_THRESHOLD` | 0.04 | **0.025** | +2.5% UVXY = PUT (was +4%) |
| `MICRO_UVXY_BULLISH_THRESHOLD` | -0.05 | **-0.03** | -3% UVXY = CALL (was -5%) |
| `INTRADAY_DEBIT_FADE_VIX_MIN` | 13.5 | **10.5** | Low-VIX years (2017) can trade |
| `INTRADAY_QQQ_FALLBACK_MIN_MOVE` | 0.007 | **0.005** | 0.5% QQQ move (was 0.7%) |
| `MICRO_SCORE_BULLISH_CONFIRM` | 55 | **52** | Lower score confirmation |
| `MICRO_SCORE_BEARISH_CONFIRM` | 45 | **48** | Symmetric with bullish |

### Expected Result

- Dir=NONE: 76% → ~40%
- Dir=PUT: 7% → ~25%
- Dir=CALL: 17% → ~35%

### Validation

- **Test:** 2017 (bull) + 2021-2022 (bear)
- **Success:** Dir=NONE < 50%, Dir=PUT > 20%

---

## PHASE 3: SPREAD CONSTRUCTION — VASS ENGINE (P1)

### Problem

- **5,137 VASS rejections** across all years ("No contracts met spread criteria")
- **0 BEAR_PUT, 0 BEAR_CALL_CREDIT** executed in 2021-2022
- VASS correctly routes to CREDIT in HIGH IV, but contract selection fails

### Root Causes Mapped

| RC | Root Cause | Parameters | Problem |
|:--:|:-----------|:-----------|:--------|
| RC-5 | tradeable_regimes too restrictive | Hardcoded set | Only 7 states allowed |
| RC-6 | Delta requirements too tight | SPREAD_*_DELTA_* | 60-218 contracts checked, none pass |

### Solution: Widen Delta + Expand Regimes

| Parameter | Current | New | Purpose |
|:----------|:-------:|:---:|:--------|
| `SPREAD_LONG_LEG_DELTA_MIN` | 0.40 | **0.35** | Allow near-ATM longs |
| `SPREAD_LONG_LEG_DELTA_MAX` | 0.85 | **0.90** | Allow deeper ITM longs |
| `SPREAD_SHORT_LEG_DELTA_MIN` | 0.10 | **0.08** | Allow further OTM shorts |
| `SPREAD_SHORT_LEG_DELTA_MAX` | 0.55 | **0.60** | More short strike candidates |
| `SPREAD_LONG_LEG_DELTA_MIN_PUT` | 0.30 | **0.25** | PUT longs can be further OTM |
| `SPREAD_SHORT_LEG_DELTA_MIN_PUT` | 0.08 | **0.05** | PUT shorts can be very OTM |
| `CREDIT_SPREAD_MIN_CREDIT` | 0.30 | **0.20** | Lower minimum credit |
| `CREDIT_SPREAD_FALLBACK_TO_DEBIT` | None | **True** | If CREDIT fails, try DEBIT |

### Code Changes Required

**Location:** `options_engine.py` → `tradeable_regimes` (line ~1347)
**Change:** Expand set to include CAUTION_LOW, CAUTIOUS

```python
# Current:
tradeable_regimes = {
    MicroRegime.PERFECT_MR,
    MicroRegime.GOOD_MR,
    MicroRegime.NORMAL,
    MicroRegime.RECOVERING,
    MicroRegime.IMPROVING,
    MicroRegime.PANIC_EASING,
    MicroRegime.CALMING,
}

# New:
tradeable_regimes = {
    MicroRegime.PERFECT_MR,
    MicroRegime.GOOD_MR,
    MicroRegime.NORMAL,
    MicroRegime.RECOVERING,
    MicroRegime.IMPROVING,
    MicroRegime.PANIC_EASING,
    MicroRegime.CALMING,
    MicroRegime.CAUTION_LOW,      # NEW: Low VIX cautious
    MicroRegime.CAUTIOUS,         # NEW: With size reduction
}
```

**Location:** `options_engine.py` → `_select_spread_contracts()`
**Change:** Add DEBIT fallback when CREDIT construction fails for PUT direction

```python
# Logic:
if direction == PUT and strategy == CREDIT:
    contracts = find_credit_spread_contracts()
    if not contracts and CREDIT_SPREAD_FALLBACK_TO_DEBIT:
        contracts = find_debit_spread_contracts()  # Fallback
```

### Expected Result

- VASS rejections: 5,137 → ~1,000
- BEAR_PUT/BEAR_CALL_CREDIT: 0 → actual trades

### Validation

- **Test:** 2021-2022 (bear market, high VIX)
- **Success:** BEAR spreads executed > 0, VASS rejections < 1,500

---

## PHASE 4: EXECUTION PIPELINE (P1)

### Problem

- **1,781 margin errors** in 2021-2022 (system approves but cannot execute)
- **581 signals → 30 executed** (5% conversion rate)

### Solution: Pre-Check Margin Before Signal Approval

| Parameter | Current | New | Purpose |
|:----------|:-------:|:---:|:--------|
| `OPTIONS_MAX_MARGIN_PCT` | 0.30 | **0.25** | Cap options at 25% of portfolio |
| `MARGIN_PRE_CHECK_BUFFER` | 0.10 | **0.15** | Require 15% margin buffer |
| `MARGIN_CHECK_BEFORE_SIGNAL` | None | **True** | Check BEFORE approving signal |

### Code Changes Required

**Location:** `options_engine.py` → signal approval logic
**Change:** Add margin availability check before INTRADAY_SIGNAL_APPROVED

```python
# Logic:
def approve_signal(signal):
    if MARGIN_CHECK_BEFORE_SIGNAL:
        available_margin = get_available_margin()
        required_margin = estimate_spread_margin(signal)
        if available_margin < required_margin * (1 + MARGIN_PRE_CHECK_BUFFER):
            return False, "MARGIN_PRE_CHECK: Insufficient margin"
    return True, None
```

### Expected Result

- Margin errors: 1,781 → <100
- Signal→Execution: 5% → ~30%

### Validation

- **Test:** 2021-2022
- **Success:** Margin errors < 100, execution rate > 25%

---

## PHASE 5: RISK/REWARD CALIBRATION (P2)

### Problem

- 2015: **48.3% win rate but -$19,925 P&L** (ex-assignment)
- Stop Loss: 22 hits vs Profit Target: 6 hits (21% target hit rate)
- Losses larger than wins despite balanced win/loss count

### Solution: Symmetric Stop/Target + Choppy Filter

| Parameter | Current | New | Purpose |
|:----------|:-------:|:---:|:--------|
| `SPREAD_STOP_LOSS_PCT` | 0.35 | **0.40** | Wider stop (40% loss allowed) |
| `SPREAD_PROFIT_TARGET_PCT` | 0.50 | **0.40** | Lower target (40% gain) |
| `CHOPPY_MARKET_FILTER_ENABLED` | None | **True** | Detect chop |
| `CHOPPY_REVERSAL_COUNT` | None | **3** | 3 reversals in 2hrs = chop |
| `CHOPPY_SIZE_REDUCTION` | None | **0.50** | 50% size in chop |

### Rationale

- Current 35% stop / 50% target = asymmetric (need 1.43:1 win ratio to break even)
- 40% stop / 40% target = symmetric (need 1:1 win ratio to break even)
- With 48% win rate, symmetric targets should be profitable

### Expected Result

- Win rate stable at ~50%
- P&L positive due to better R:R

### Validation

- **Test:** 2015 (choppy year)
- **Success:** Positive P&L with ~50% win rate

---

## COMPLETE PARAMETER CHANGE SUMMARY

```python
# ═══════════════════════════════════════════════════════════════════════════
# V6.10 COMPREHENSIVE FIX BUNDLE
# ═══════════════════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────────────────────
# PHASE 1: ASSIGNMENT PREVENTION (P0)
# ─────────────────────────────────────────────────────────────────────────────
SPREAD_FORCE_CLOSE_DTE = 1                    # NEW: Mandatory close at DTE=1
PREMARKET_ITM_CHECK_ENABLED = True            # NEW: 09:25 ITM check
SHORT_LEG_ITM_EXIT_THRESHOLD = 0.005          # Was 0.01 → Exit at 0.5% ITM
SPREAD_WIDTH_MIN = 5.0                        # Was 3.0 → $5 minimum width

# ─────────────────────────────────────────────────────────────────────────────
# PHASE 2: DIRECTION GENERATION - MICRO (P1)
# ─────────────────────────────────────────────────────────────────────────────
VIX_STABLE_BAND_LOW = 0.003                   # Was 0.005 → ±0.3%
VIX_STABLE_BAND_HIGH = 0.010                  # Was 0.020 → ±1.0%
MICRO_UVXY_CONVICTION_EXTREME = 0.05          # Was 0.07 → 5%
MICRO_UVXY_BEARISH_THRESHOLD = 0.025          # Was 0.04 → +2.5%
MICRO_UVXY_BULLISH_THRESHOLD = -0.03          # Was -0.05 → -3%
INTRADAY_DEBIT_FADE_VIX_MIN = 10.5            # Was 13.5
INTRADAY_QQQ_FALLBACK_MIN_MOVE = 0.005        # Was 0.007 → 0.5%
MICRO_SCORE_BULLISH_CONFIRM = 52              # Was 55
MICRO_SCORE_BEARISH_CONFIRM = 48              # Was 45

# ─────────────────────────────────────────────────────────────────────────────
# PHASE 3: SPREAD CONSTRUCTION - VASS (P1)
# ─────────────────────────────────────────────────────────────────────────────
SPREAD_LONG_LEG_DELTA_MIN = 0.35              # Was 0.40
SPREAD_LONG_LEG_DELTA_MAX = 0.90              # Was 0.85
SPREAD_SHORT_LEG_DELTA_MIN = 0.08             # Was 0.10
SPREAD_SHORT_LEG_DELTA_MAX = 0.60             # Was 0.55
SPREAD_LONG_LEG_DELTA_MIN_PUT = 0.25          # Was 0.30
SPREAD_SHORT_LEG_DELTA_MIN_PUT = 0.05         # Was 0.08
CREDIT_SPREAD_MIN_CREDIT = 0.20               # Was 0.30
CREDIT_SPREAD_FALLBACK_TO_DEBIT = True        # NEW

# ─────────────────────────────────────────────────────────────────────────────
# PHASE 4: EXECUTION PIPELINE (P1)
# ─────────────────────────────────────────────────────────────────────────────
OPTIONS_MAX_MARGIN_PCT = 0.25                 # Was 0.30
MARGIN_PRE_CHECK_BUFFER = 0.15                # Was 0.10
MARGIN_CHECK_BEFORE_SIGNAL = True             # NEW

# ─────────────────────────────────────────────────────────────────────────────
# PHASE 5: RISK/REWARD (P2)
# ─────────────────────────────────────────────────────────────────────────────
SPREAD_STOP_LOSS_PCT = 0.40                   # Was 0.35
SPREAD_PROFIT_TARGET_PCT = 0.40               # Was 0.50
CHOPPY_MARKET_FILTER_ENABLED = True           # NEW
CHOPPY_REVERSAL_COUNT = 3                     # NEW
CHOPPY_SIZE_REDUCTION = 0.50                  # NEW
```

---

## CODE CHANGES SUMMARY

| File | Location | Change Type | Description |
|:-----|:---------|:-----------:|:------------|
| `options_engine.py` | ~line 1347 | Modify set | Add `CAUTION_LOW`, `CAUTIOUS` to `tradeable_regimes` |
| `options_engine.py` | `_select_spread_contracts()` | Add logic | DEBIT fallback when CREDIT fails for PUT |
| `options_engine.py` | `_check_assignment_risk()` | Add logic | DTE=1 force close |
| `options_engine.py` | Signal approval | Add logic | Margin pre-check before approval |
| `main.py` | Scheduled events | Add event | 09:25 pre-market ITM check |

---

## VALIDATION BACKTEST SEQUENCE

| Phase | Test Period | Success Criteria | Run After |
|:-----:|:------------|:-----------------|:----------|
| 1 | 2021-2022 | 0 assignments | Phase 1 params |
| 2 | 2017 | Dir=NONE < 50% | Phase 1+2 params |
| 3 | 2021-2022 | BEAR spreads > 0 | Phase 1+2+3 params |
| 4 | 2021-2022 | Margin errors < 100 | Phase 1+2+3+4 params |
| 5 | 2015 | Positive P&L | All params |
| **Final** | **2015-2022** | **Positive all years** | **Complete V6.10** |

---

## EXPECTED OUTCOMES

| Metric | V6.9 | V6.10 | Improvement |
|:-------|:----:|:-----:|:-----------:|
| Assignments | 6 | 0 | **100%** |
| Assignment P&L | -$150,953 | $0 | **+$150,953** |
| Dir=NONE | 76-98% | <50% | **~50%** |
| Dir=PUT | 7% | ~25% | **+257%** |
| VASS Rejections | 5,137 | <1,500 | **-70%** |
| BEAR Spreads | 0 | >50 | **∞** |
| Margin Errors | 1,781 | <100 | **-94%** |
| Execution Rate | 5% | >25% | **+400%** |
| 2015 P&L | -$19,925 | Positive | **Breakeven+** |
| 2017 P&L | +$9,280 | +$15,000+ | **+60%** |
| 2021-22 P&L | -$66,593 | Positive | **Breakeven+** |

---

## IMPLEMENTATION CHECKLIST

### Phase 1: Assignment Prevention

- [x] Add `SPREAD_FORCE_CLOSE_DTE = 1` to config.py
- [x] Add `PREMARKET_ITM_CHECK_ENABLED = True` to config.py
- [x] Update `SHORT_LEG_ITM_EXIT_THRESHOLD = 0.005`
- [x] Update `SPREAD_WIDTH_MIN = 5.0`
- [x] Add DTE=1 force close logic to options_engine.py
- [x] Add 09:25 pre-market ITM check to main.py
- [ ] Run 2021-2022 backtest → verify 0 assignments

### Phase 2: Direction Generation

- [x] Update all 9 MICRO parameters in config.py
- [ ] Run 2017 backtest → verify Dir=NONE < 50%
- [ ] Run 2021-2022 backtest → verify Dir=PUT > 20%

### Phase 3: Spread Construction

- [x] Update all 8 VASS parameters in config.py
- [x] Expand tradeable_regimes set in options_engine.py
- [x] Add DEBIT fallback logic in options_engine.py
- [ ] Run 2021-2022 backtest → verify BEAR spreads > 0

### Phase 4: Execution Pipeline

- [x] Update 3 margin parameters in config.py
- [x] Add margin pre-check logic in options_engine.py
- [ ] Run 2021-2022 backtest → verify margin errors < 100

### Phase 5: Risk/Reward

- [x] Update stop/target parameters in config.py
- [x] Add choppy market filter parameters
- [ ] Run 2015 backtest → verify positive P&L

### Final Validation

- [ ] Run full 2015-2022 backtest with all V6.10 changes
- [ ] Verify positive P&L across all market types
- [ ] Document results in V6.10 audit report

---

## CONCLUSION

**The system design is sound.** The options engine has working components:
- Regime detection ✅
- VASS routing ✅
- Safeguards ✅

The failures are due to **overly conservative thresholds** that block execution.

**V6.10 fixes are:**
- 18 parameter changes in config.py
- 5 targeted code modifications

**No architectural changes needed.**
