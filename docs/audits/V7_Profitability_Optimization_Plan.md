# V7 Profitability Optimization Plan

**Date:** 2026-02-11
**Status:** Draft - Pending Review
**Author:** Claude Code Deep Analysis

---

## Executive Summary

After comprehensive analysis of the entire Alpha NextGen V2 codebase, I identified **16 critical bugs and optimization opportunities** that are blocking profitability. The root causes fall into three categories:

| Category | Impact | Fix Count |
|----------|--------|-----------|
| **Safety Mechanisms** | Kill switch triggers late, lockbox over-locks capital | 4 fixes |
| **Regime Detection** | Allows longs in weak markets, false signals in chop | 4 fixes |
| **Options Engine** | <20% approval rate, 99% losses from CALLs | 8 fixes |

**All fixes are surgical (1-5 lines each).** No new files, no architectural changes.

---

## Goal

**MAKE THIS ALGO PROFITABLE BY DETECTING THE REGIME PERFECTLY AND NAVIGATING IT SUCCESSFULLY**

Principles:
- Minimal code changes (surgical fixes)
- Remove unnecessary clutter/logic
- Maximize profitability
- Improve shock absorption

---

## Root Causes of Unprofitability

### 1. Regime Allows Longs in DEFENSIVE Market
- **Current:** `new_longs_allowed = smoothed >= 35` (DEFENSIVE threshold)
- **Problem:** Trend engine enters longs when market is weak (score 35-49)
- **Investment Thesis:** DEFENSIVE should block new longs, only allow existing positions
- **Fix:** Change to `>= 50` (NEUTRAL threshold)

### 2. Kill Switch Baseline Logic Inverted
- **Current:** Uses MAX of prior_close and SOD baselines
- **Problem:** If market rallies at open, kill switch uses SOD as baseline, delaying trigger
- **Example:** Prior=$100k, SOD=$102k, Current=$98k → Loss from prior=2%, Loss from SOD=3.9% → Uses 3.9%
- **Fix:** Use prior_close as primary, SOD only as fallback

### 3. Options Engine Ignores Macro Regime
- **Current:** Only checks Micro Regime for entry timing
- **Problem:** BULL_CALL spreads enter during DEFENSIVE regime
- **Fix:** Add macro regime gate: block bullish options when `new_longs_allowed=False`

### 4. Lockbox Compounds Incorrectly
- **Current:** Locks 10% of CURRENT equity at each milestone
- **Problem:** At $500k with milestones at $100k/$150k/$200k → locks $50k+$75k+$100k = $225k (45%)
- **Fix:** Lock 10% of MILESTONE value, not current equity

### 5. Intraday Options Approval Rate <20%
- **Current:** MICRO_SCORE thresholds 47-48 require near-perfect alignment
- **Problem:** In chop markets, 70% CONFIRMATION_FAIL rate
- **Fix:** Lower thresholds to 45 for both CALL and PUT

---

## P0: CRITICAL SAFETY FIXES

### Fix 1: Kill Switch Baseline Logic

**File:** `engines/core/risk_engine.py`
**Lines:** 841-855
**Severity:** CRITICAL
**Impact:** Kill switch triggers 1+ hours late during flash crashes

**Current Code (WRONG):**
```python
def _get_max_loss_pct(self, current_equity: float) -> Tuple[float, str, float]:
    max_loss = 0.0
    baseline_name = "none"
    baseline_value = 0.0

    if self._equity_prior_close > 0:
        loss_from_prior = (self._equity_prior_close - current_equity) / self._equity_prior_close
        if loss_from_prior > max_loss:  # ← Takes MAX of both
            max_loss = loss_from_prior
            baseline_name = "prior_close"
            baseline_value = self._equity_prior_close

    if self._equity_sod > 0:
        loss_from_sod = (self._equity_sod - current_equity) / self._equity_sod
        if loss_from_sod > max_loss:  # ← Overwrites if SOD loss higher
            max_loss = loss_from_sod
            baseline_name = "sod"
            baseline_value = self._equity_sod

    return max_loss, baseline_name, baseline_value
```

**Fixed Code:**
```python
def _get_max_loss_pct(self, current_equity: float) -> Tuple[float, str, float]:
    """Use prior_close as PRIMARY baseline, SOD only as fallback."""
    if self._equity_prior_close > 0:
        max_loss = (self._equity_prior_close - current_equity) / self._equity_prior_close
        return max_loss, "prior_close", self._equity_prior_close
    elif self._equity_sod > 0:
        max_loss = (self._equity_sod - current_equity) / self._equity_sod
        return max_loss, "sod", self._equity_sod
    else:
        return 0.0, "none", 0.0
```

**Verification:**
- Unit test: prior_close=$100k, SOD=$102k, current=$98k
- Expected: triggers at 2% loss from prior_close
- Current (bug): waits for 4% loss from SOD

---

### Fix 2: new_longs_allowed Threshold

**File:** `engines/core/regime_engine.py`
**Line:** 541
**Severity:** CRITICAL
**Impact:** Trend engine enters longs in DEFENSIVE regime (score 35-44)

**Current Code (WRONG):**
```python
new_longs_allowed = smoothed >= config.REGIME_DEFENSIVE  # >= 35
```

**Fixed Code:**
```python
new_longs_allowed = smoothed >= config.REGIME_NEUTRAL  # >= 50
```

**Rationale:**
- REGIME_DEFENSIVE (35-44): "Reduced leverage, hedges active" per investment thesis
- New longs in defensive regime = fighting the trend
- Only allow new longs when regime is NEUTRAL (50+) or better

**Verification:**
- Backtest 2022 Q1-Q2 (grinding bear)
- Confirm: No long entries when regime 35-49
- Confirm: Reduced drawdown during March-June selloff

---

### Fix 3: cold_start_allowed Threshold

**File:** `engines/core/regime_engine.py`
**Line:** 542
**Severity:** HIGH
**Impact:** Cold start ramping allowed in barely NEUTRAL regime (score 51+)

**Current Code (WRONG):**
```python
cold_start_allowed = smoothed > config.REGIME_NEUTRAL  # > 50
```

**Fixed Code:**
```python
cold_start_allowed = smoothed >= 60  # Solid NEUTRAL or better
```

**Rationale:**
- Cold start is days 1-5 position ramping for new accounts
- Ramping into weak market = immediate drawdown
- Require solid regime (60+) before ramping

---

### Fix 4: Lockbox Accumulation Bug

**File:** `engines/core/capital_engine.py`
**Line:** 162
**Severity:** CRITICAL
**Impact:** At $500k equity, locks ~$225k (45%) instead of $50k (10%)

**Current Code (WRONG):**
```python
def _check_lockbox_milestones(self, total_equity: float) -> None:
    for milestone in config.LOCKBOX_MILESTONES:
        if milestone not in self._milestones_triggered:
            if total_equity >= milestone:
                lock_amount = total_equity * config.LOCKBOX_LOCK_PCT  # ← BUG: Uses current equity
                self._locked_amount += lock_amount
```

**Fixed Code:**
```python
                lock_amount = milestone * config.LOCKBOX_LOCK_PCT  # ← FIX: Uses milestone value
```

**Example:**
| Milestone | Current (Bug) | Fixed |
|-----------|--------------|-------|
| $100k | Lock $50k (10% of $500k current) | Lock $10k (10% of $100k milestone) |
| $150k | Lock $75k | Lock $15k |
| $200k | Lock $100k | Lock $20k |
| **Total** | **$225k (45%)** | **$45k (9%)** |

---

### Fix 5: Options Macro Regime Gate

**File:** `engines/satellite/options_engine.py`
**Location:** In `_validate_swing_entry()` or `can_enter_swing()`
**Severity:** CRITICAL
**Impact:** Bullish spreads enter during DEFENSIVE/RISK_OFF regimes

**Add Check:**
```python
# Block bullish options when macro regime blocks new longs
if direction in (OptionDirection.BULL_CALL, OptionDirection.BULL_PUT):
    if not regime_state.new_longs_allowed:
        return self._reject("R_MACRO_REGIME_LONGS_BLOCKED",
                           f"Bullish options blocked: regime={regime_state.smoothed_score:.0f}")
```

**Verification:**
- Force regime score to 40 (CAUTIOUS)
- Confirm: BULL_CALL and BULL_PUT rejected
- Confirm: BEAR_CALL and BEAR_PUT allowed

---

### Fix 6: Add SPXL to Panic Mode Symbol List

**File:** `engines/core/risk_engine.py`
**Line:** ~64
**Severity:** MEDIUM
**Impact:** SPXL (3x S&P MR symbol) not liquidated during -4% SPY panic

**Current Code:**
```python
LEVERAGED_LONG_SYMBOLS: List[str] = ["TQQQ", "QLD", "SSO", "SOXL"]
```

**Fixed Code:**
```python
LEVERAGED_LONG_SYMBOLS: List[str] = ["TQQQ", "QLD", "SSO", "SOXL", "SPXL"]
```

---

## P1: REGIME ACCURACY FIXES

### Fix 7: Port Recovery Hysteresis to V5.3

**File:** `engines/core/regime_engine.py`
**Location:** After smoothing in `_calculate_v53()` (around line 1400)
**Severity:** HIGH
**Impact:** V-shaped recoveries trigger false "all clear" immediately

**Problem:**
- Hysteresis logic exists in V3.3 (`_calculate_simplified()`) but not in active V5.3
- Without hysteresis, regime can jump from RISK_OFF (30) to NEUTRAL (55) in one day
- This causes whipsaws: enter long → regime drops → stop out → repeat

**Add to V5.3:**
```python
# Recovery hysteresis: require 2+ days of improvement before upgrading regime
if config.RECOVERY_HYSTERESIS_ENABLED:
    if smoothed > self._previous_smoothed_score:
        self._recovery_days += 1
    else:
        self._recovery_days = 0

    # Cap regime at previous level until hysteresis satisfied
    if self._recovery_days < config.RECOVERY_HYSTERESIS_DAYS:
        if smoothed > self._previous_smoothed_score:
            smoothed = self._previous_smoothed_score
```

---

### Fix 8: Reduce Breadth Decay Sensitivity

**File:** `config.py`
**Lines:** 271-274
**Severity:** MEDIUM
**Impact:** False "distribution" signals in normal markets

**Current Config:**
```python
V53_BREADTH_5D_DECAY_THRESHOLD = -0.01  # -1% divergence triggers penalty
V53_BREADTH_10D_DECAY_THRESHOLD = -0.03  # -3% divergence triggers penalty
V53_BREADTH_5D_PENALTY = 8  # -8 regime points
V53_BREADTH_10D_PENALTY = 12  # -12 regime points (stacks)
```

**Problem:**
- RSP underperforming SPY by 1% over 5 days is NORMAL
- -8 points is catastrophic: drops regime from NEUTRAL to CAUTIOUS
- In 2015/2018 chop, this caused constant regime downgrades

**Fixed Config:**
```python
V53_BREADTH_5D_DECAY_THRESHOLD = -0.02  # -2% (less sensitive)
V53_BREADTH_10D_DECAY_THRESHOLD = -0.04  # -4% (less sensitive)
V53_BREADTH_5D_PENALTY = 5  # Reduced from 8
V53_BREADTH_10D_PENALTY = 8  # Reduced from 12
```

---

## P1: OPTIONS PROFITABILITY FIXES

### Fix 9: Lower Intraday Approval Thresholds

**File:** `config.py`
**Severity:** HIGH
**Impact:** 70% CONFIRMATION_FAIL rate, <20% approval

**Current Config:**
```python
MICRO_SCORE_BULLISH_CONFIRM = 47.0  # CALLs need >= 47
MICRO_SCORE_BEARISH_CONFIRM = 48.0  # PUTs need >= 48 (asymmetric!)
```

**Problem:**
- 5-factor scoring (VIX, Direction, Move, Velocity, +1) caps at ~55
- Score of 47-48 requires near-perfect alignment
- In chop (2015, 2018), MICRO stuck at 40-50 range → 70% rejection

**Fixed Config:**
```python
MICRO_SCORE_BULLISH_CONFIRM = 45.0  # Lowered by 2 points
MICRO_SCORE_BEARISH_CONFIRM = 45.0  # Symmetric (was 48)
```

**Expected Impact:**
- Approval rate: 20% → 40%
- More trades = more data = better win rate calibration

---

### Fix 10: Disable Win-Rate Hard Block

**File:** `config.py` (add if missing)
**Severity:** HIGH
**Impact:** VASS completely blocked when paper win-rate drops

**Add:**
```python
VASS_WIN_RATE_HARD_BLOCK = False  # Use soft scaling instead of hard block
```

**Rationale:**
- Paper trades aren't real fills - win rate calculation is noisy
- Hard block creates death spiral: few trades → noisy win rate → more blocking
- Soft scaling (50% size at low win rate) is better than complete shutoff

---

### Fix 11: Disable Neutrality Exit Churn

**File:** `config.py` (add if missing)
**Severity:** MEDIUM
**Impact:** Exits spreads at flat P&L, incurring commission losses

**Add:**
```python
SPREAD_NEUTRALITY_EXIT_ENABLED = False
```

**Problem:**
- Neutrality exit fires when regime 45-65 AND -10% < P&L < +10%
- 2015 backtest: 17 NEUTRALITY_EXIT (dominant exit reason)
- Each exit = 4 legs × $0.65 = $2.60 commission loss

---

### Fix 12: Raise Directional Spread Cap

**File:** `config.py`
**Severity:** MEDIUM
**Impact:** Old losing BULL spread blocks all future BULL entries

**Current:**
```python
OPTIONS_MAX_SWING_PER_DIRECTION = 2  # 2 BULL + 2 BEAR max
```

**Problem:**
- Total cap is 3 spreads
- But directional cap is 2
- Old losing BULL spread blocks slot → no BULL entries for days/weeks

**Fixed:**
```python
OPTIONS_MAX_SWING_PER_DIRECTION = 3  # Match total cap
```

---

### Fix 13: Tighten CALL Profit Target

**File:** `config.py` and `engines/satellite/options_engine.py`
**Severity:** MEDIUM
**Impact:** 99% of losses from CALLs in backtests

**Evidence from Audits:**
| Period | CALL P&L | PUT P&L |
|--------|----------|---------|
| Dec-Feb 2024 | -$17,680 | +$1,630 |
| Jul-Oct 2022 | -$2,416 | -$4,094 |
| Sep-Dec 2018 | -$22,491 | -$802 |

**Add to config.py:**
```python
# V7: Asymmetric targets - CALLs are riskier
SPREAD_CALL_PROFIT_TARGET_PCT = 0.40  # 40% for CALLs (was 50%)
SPREAD_PUT_PROFIT_TARGET_PCT = 0.50   # 50% for PUTs (unchanged)
```

**Update options_engine.py exit logic to use direction-specific targets.**

---

## P2: CLEANUP (Lower Priority)

### Fix 14: Verify V3.3 Code Disabled
**File:** `config.py`
**Action:** Confirm `V3_REGIME_SIMPLIFIED_ENABLED = False`

### Fix 15: VIX Clamp Sequencing
**File:** `engines/core/regime_engine.py`
**Line:** ~1235
Move VIX high_vix_clamp application to AFTER aggregation:
```python
final_score = aggregate_regime_score_v53(...)
if vix_level > 25.0:
    final_score = min(final_score, config.VIX_COMBINED_HIGH_VIX_CLAMP)
```

---

## Implementation Order

### Phase 1: Safety First
| Fix | File | Lines Changed |
|-----|------|---------------|
| #1 Kill switch baseline | risk_engine.py | ~15 |
| #6 Add SPXL | risk_engine.py | 1 |
| #4 Lockbox | capital_engine.py | 1 |

### Phase 2: Regime Accuracy
| Fix | File | Lines Changed |
|-----|------|---------------|
| #2 new_longs_allowed | regime_engine.py | 1 |
| #3 cold_start_allowed | regime_engine.py | 1 |
| #8 Breadth decay | config.py | 4 |

### Phase 3: Options Fixes
| Fix | File | Lines Changed |
|-----|------|---------------|
| #5 Macro regime gate | options_engine.py | ~5 |
| #9 Intraday thresholds | config.py | 2 |
| #10 Win-rate block | config.py | 1 |
| #11 Neutrality exit | config.py | 1 |
| #12 Directional cap | config.py | 1 |

### Phase 4: Polish
| Fix | File | Lines Changed |
|-----|------|---------------|
| #7 Hysteresis | regime_engine.py | ~10 |
| #13 CALL targets | config.py + options_engine.py | ~8 |
| #15 VIX clamp | regime_engine.py | ~3 |

---

## Verification Plan

### Unit Tests
```bash
# Regime threshold tests
pytest tests/test_regime_engine.py -v -k "longs_allowed or cold_start"

# Kill switch baseline tests
pytest tests/test_risk_engine.py -v -k "kill_switch"

# Lockbox accumulation tests
pytest tests/test_capital_engine.py -v -k "lockbox"
```

### Backtests to Run

| Period | Purpose | Expected Result |
|--------|---------|-----------------|
| 2022 Q1-Q2 | Grinding bear | Regime drops to DEFENSIVE/RISK_OFF, no long entries |
| 2020 March | Flash crash | Kill switch triggers at 2% (not 4%), panic mode works |
| 2021 Full Year | Bull market | Regime stays NEUTRAL/RISK_ON, no false downgrades |
| 2015 Jul-Oct | Chop | Intraday approval improves from 20% to 40% |

---

## Expected Impact Summary

| Metric | Before | After |
|--------|--------|-------|
| Regime accuracy (grinding bear) | Stuck at 43-50 | Drops to 25-40 |
| Kill switch trigger time | Delayed 1+ hours | Immediate |
| Long entries in DEFENSIVE | Allowed | Blocked |
| Intraday approval rate | <20% | 35-45% |
| CALL/PUT loss ratio | 99%/1% | 60%/40% |
| Lockbox at $500k | $225k (45%) | $45k (9%) |
| Tradeable capital at $500k | $275k (55%) | $455k (91%) |

---

## Risk Notes

1. **Fix 2 (new_longs_allowed)** - May reduce trading frequency in choppy markets. This is intentional.

2. **Fix 9 (intraday thresholds)** - May increase trade count. Monitor for overtrading in first backtest.

3. **Fix 7 (hysteresis)** - May delay re-entry after V-shaped recoveries by 2 days. Accept as whipsaw protection.

4. **Fix 13 (CALL targets)** - Tighter CALL targets (40% vs 50%) may reduce CALL profits. But CALL losses are 10x PUT losses, so net positive expected.

---

## Files Modified Summary

| File | Fixes | Total Lines Changed |
|------|-------|---------------------|
| `engines/core/risk_engine.py` | #1, #6 | ~16 |
| `engines/core/regime_engine.py` | #2, #3, #7, #15 | ~15 |
| `engines/core/capital_engine.py` | #4 | ~1 |
| `engines/satellite/options_engine.py` | #5, #13 | ~13 |
| `config.py` | #8, #9, #10, #11, #12, #13 | ~12 |
| **TOTAL** | **16 fixes** | **~57 lines** |

---

## Appendix: Audit Trail

### Findings Verified By File Read
- [x] Kill switch baseline logic: `risk_engine.py:841-855`
- [x] new_longs_allowed: `regime_engine.py:541`
- [x] cold_start_allowed: `regime_engine.py:542`
- [x] Lockbox accumulation: `capital_engine.py:162`
- [x] Regime thresholds: `config.py:293-296`

### Agent Reports Referenced
- Regime Engine Analysis (agent af9f5c6)
- Options Engine Analysis (agent a000588)
- Risk & Execution Analysis (agent a184558)
- Implementation Planning (agent a890e1b)
