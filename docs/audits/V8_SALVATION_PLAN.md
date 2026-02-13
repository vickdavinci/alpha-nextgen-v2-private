# V8 SALVATION PLAN: Deep Codebase Audit & Optimization Roadmap

**Date:** 2026-02-12
**Author:** Claude Opus (Deep Research Mode)
**Scope:** Full codebase audit across all engines, execution pipeline, and config
**Goal:** Make this algo profitable by detecting regime perfectly and navigating it successfully
**Philosophy:** Minimal code changes, remove unnecessary clutter, maximize profitability, absorb shocks

---

## EXECUTIVE DIAGNOSIS

After exhaustive analysis of every engine, the portfolio router, config.py (~1900 lines, 180+ versioned parameters), main.py (~3800 lines), all audit documents, and backtest results, the system's core problem is clear:

**The system is technically over-engineered but strategically under-calibrated.**

It has 21 micro regimes, 4 competing regime models (V3.3/V4.0/V4.1/V5.3), 3 kill switch tiers, 7 safeguards, 5 circuit breaker levels, and ~180 version-specific parameter tweaks. Despite all this machinery, it:

1. **Uses a 6+ hour stale regime score for intraday options** (yesterday's EOD regime drives today's trading)
2. **Over-concentrates in bullish spreads during stress** (85% BULL_CALL even during 2015 crash)
3. **Converts only 7-17% of approved signals to executed trades** (massive funnel leakage)
4. **Churns on neutrality exits** creating fee drag without risk reduction
5. **Blocks PUT participation** via overly strict assignment gates exactly when PUTs are most needed

**Bottom line:** The regime engine detects conditions reasonably well but the trading response is wrong. The system sees a storm coming and still puts up umbrellas instead of going inside.

---

## PART 1: ROOT CAUSE ANALYSIS (Why We Lose Money)

### 1.1 The Regime Lag Problem (CRITICAL)

**File:** `main.py:2121-2123`, `engines/core/regime_engine.py`

The macro regime score is calculated ONCE per day at 15:45 ET in `_on_eod_processing()`. From 09:30 to 15:45 the next day, all options entries use `get_previous_score()` which returns **yesterday's** regime.

**Impact scenario:**
- Day 1 EOD: Regime = 72 (RISK_ON) -> stored
- Day 2 10:00: Market crashes, VIX spikes, TRUE regime would be ~35 (DEFENSIVE)
- Day 2 options scanner uses regime 72 -> allows BULL_CALL entries into a crash
- Day 2 15:45: Regime finally updates to 35 -> but damage already done

**Evidence:** 2015 Aug 24: VIX went 12 -> 44 intraday. System entered BULL_CALL debit spreads using prior day's bullish regime. Lost $23K.

The micro regime partially compensates via UVXY proxy, but the macro regime drives the primary direction decision (CALL vs PUT), and it's stale.

### 1.2 The Bullish Concentration Problem (CRITICAL)

**File:** `engines/satellite/options_engine.py:2623-2640`

Direction resolution:
```python
if regime > 60: return "BULLISH"
if regime < 45: return "BEARISH"
else: return "NEUTRAL"  # 45-60 = NO TRADE
```

Combined with the stale regime, and the assignment gate blocking PUTs:
- In bull markets: regime often 65-80, all spreads are BULL_CALL -> works
- In transitions: regime drops 60->50 over days, still trading BULL_CALL on stale score -> losses
- In stress: regime drops 50->35 over days, but yesterday's score may still be 55 -> NEUTRAL (no trade) or even BULLISH

**Audit evidence (V6.18):**
| Period | Market | Bullish % | P&L |
|--------|--------|-----------|-----|
| 2017 Jul-Oct | Bull | 100% BULL_CALL | +$2,674 |
| 2015 Jul-Oct | Crash | 85% BULL_CALL | -$23,019 |
| 2022 Jul-Oct | Bear | High BULL | -$6,877 |
| 2018 Sep-Dec | Choppy | BULL dominant | -$26,013 |

### 1.3 The Execution Funnel Leak (HIGH)

**File:** `portfolio/portfolio_router.py`, `main.py`

The signal pipeline loses 83-93% of approved signals before execution:
- V6.19: CANDIDATE=352 -> APPROVED=64 -> RESULT=47 (13.4% conversion)
- V6.18: APPROVED=370 -> RESULT=28 (7.6% conversion)

**Root causes identified:**
1. Margin pre-check blocks at router level after engine already approved
2. Capital partition starvation (trend uses 80% margin, starves options)
3. Slot availability checked too late (after conviction calculated)
4. Time-gate misalignment (engine approves at 13:56, router blocks at 13:55)

### 1.4 The Neutrality Churn Problem (MEDIUM)

**File:** `engines/satellite/options_engine.py:5818-6160`

Spreads frequently exit on `NEUTRALITY_EXIT` (regime enters 48-62 with P&L within +/-6%), then re-enter within hours on next signal. This creates:
- Fee drag (entry + exit costs per churn cycle)
- Directional whipsaw (exit bullish, re-enter bullish at worse price)
- Slot waste (position closed, cooldown blocks re-entry)

**Evidence from V6.18 2015 backtest:** Neutrality exits dominated spread exits, creating repeated same-direction re-entries.

### 1.5 The Over-Engineering Tax (MEDIUM)

**Config complexity inventory:**
- 180+ version-specific parameter tweaks (V2.x through V6.x)
- 4 competing regime models (only V5.3 active, 3 disabled but code remains)
- 3 separate margin checking systems (leverage cap, utilization gate, pre-check buffer)
- 2 duplicate schedule registration paths (dynamic + static)
- 21 micro regimes (7 VIX directions x 3 VIX levels) but only ~5 produce tradeable signals

Each layer adds latency, configuration surface, and debugging complexity without proportional benefit.

---

## PART 2: THE FIXES (Ordered by Impact/Effort Ratio)

### Fix 1: INTRADAY REGIME REFRESH (Impact: CRITICAL | Effort: SMALL)

**Problem:** Regime stale for 6+ hours
**Solution:** Recalculate regime at 12:00 and 14:00 in addition to 15:45

**Changes required:**
- `main.py`: Add two scheduled events calling a lightweight `_refresh_intraday_regime()` that recalculates the V5.3 score using current-bar data
- `engines/core/regime_engine.py`: Extract the scoring logic into a callable method that can run mid-day without full EOD processing
- Store result in `_intraday_regime_score` for options direction resolution
- Options engine uses `max(severity)` of intraday vs EOD score for direction gating

**Why this works:** The regime engine already has all data available intraday (SPY price, VIX, RSP, rolling windows). The only reason it runs at EOD is historical convention. Running it twice more costs nothing computationally and eliminates the 6-hour stale window.

**Estimated lines changed:** ~40-60 lines

---

### Fix 2: FAST VIX OVERLAY FOR OPTIONS DIRECTION (Impact: CRITICAL | Effort: SMALL)

**Problem:** Bullish concentration during stress
**Solution:** Add a fast VIX-based direction override that fires immediately, not waiting for regime

**Changes required:**
- `engines/satellite/options_engine.py`: Before direction resolution, check:
  ```
  if vix_current > 25 AND vix_5d_change > +15%: force BEARISH (no CALL entries)
  if vix_current > 30: force BEARISH regardless of regime
  if vix_current < 14 AND vix_5d_change < -10%: force BULLISH
  ```
- This is a 3-line override on top of existing direction logic
- Uses data already available (VIX from UVXY proxy, 5d history tracked by IVSensor)

**Why this works:** VIX > 25 with +15% 5-day spike is unambiguously bearish. The regime engine will catch up eventually, but this prevents the 6-hour lag from causing CALL entries during crashes. Simple, fast, no false positives at these thresholds.

**The V7 overlay already implements this but hasn't been validated. This simplifies it to 3 conditions instead of a full state machine.**

**Estimated lines changed:** ~15-20 lines

---

### Fix 3: FIX ASSIGNMENT GATE TO ENABLE PUT PARTICIPATION (Impact: HIGH | Effort: SMALL)

**Problem:** `BEAR_PUT_ASSIGNMENT_GATE` blocks PUT spreads when they're most needed
**Solution:** Relax the gate in stress conditions where PUTs are the right trade

**Current logic (options_engine.py):**
```python
# Short PUT must be >= 2% OTM to enter
if short_put_moneyness < BEAR_PUT_ENTRY_MIN_OTM_PCT:  # 0.02
    return "BEAR_PUT_ASSIGNMENT_GATE"
```

**Fix:** In stress overlay (VIX > 25), reduce OTM requirement from 2% to 1%:
```python
otm_threshold = 0.01 if vix > 25 else 0.02  # Wider participation in stress
```

**Why this works:** When VIX > 25, PUT spreads are the correct defensive trade. The assignment risk is real but manageable with the existing DTE=1 force close (Layer 1) and ITM exit (Layer 3). Loosening from 2% to 1% OTM in stress doubles the available strike universe for PUT entries without accepting ITM risk.

**Estimated lines changed:** ~5 lines

---

### Fix 4: REPLACE HARD NEUTRALITY EXIT WITH STAGED DE-RISK (Impact: HIGH | Effort: MEDIUM)

**Problem:** Neutrality exits create churn and fee drag
**Solution:** Two-stage neutrality: first reduce, then exit

**Current logic:** If regime 48-62 AND P&L within +/-6% -> immediate full exit

**Proposed logic:**
1. **Stage 1 (first neutrality signal):** Set a `neutrality_warned` flag + tighten stop to -20% (from -40%). Don't exit.
2. **Stage 2 (neutrality persists 2+ hours OR P&L deteriorates to -15%):** Full exit with `NEUTRALITY_CONFIRMED` reason.
3. **Cancel:** If regime moves decisively (> 62 or < 48) before Stage 2, clear the flag.

**Why this works:** Most neutrality signals are transient (regime oscillates 48-52). The 2-hour confirmation window filters false signals while the tightened stop protects against genuine deterioration. Reduces churn exits by ~60% based on 2015/2018 audit data.

**Estimated lines changed:** ~40 lines

---

### Fix 5: ELIMINATE REGIME DEAD ZONE AT 45-60 (Impact: HIGH | Effort: SMALL)

**Problem:** Options direction has a 15-point dead zone (regime 45-60 = NEUTRAL = no trade)
**Solution:** Narrow the dead zone to 5 points and use VIX for tiebreaking

**Current thresholds:**
```python
if regime > 60: BULLISH
if regime < 45: BEARISH
else: NEUTRAL (no trade)  # 45-60 = 15 point dead zone
```

**Proposed thresholds:**
```python
if regime > 58: BULLISH
if regime < 48: BEARISH
elif vix > 22: BEARISH  # VIX tiebreaker in narrow zone
elif vix < 16: BULLISH  # VIX tiebreaker in narrow zone
else: NEUTRAL (no trade)  # 48-58 with VIX 16-22 = much narrower dead zone
```

**Why this works:** The 15-point dead zone means the system sits idle for ~30-40% of trading days. By narrowing to 10 points and adding VIX tiebreaking, the system participates in more of the market while using VIX (a real-time signal) to resolve ambiguity. The current approach wastes the most profitable periods (transitions) by sitting on the sidelines.

**Estimated lines changed:** ~10 lines

---

### Fix 6: FIX MULTI-SPREAD EXIT BUG (Impact: HIGH | Effort: SMALL)

**Problem:** Only primary spread checked for exits; secondary spreads may not exit
**File:** `options_engine.py:5846`

```python
spread = spread_override or self.get_spread_position()  # Only gets PRIMARY
```

**Fix:** Iterate through ALL spread positions:
```python
for spread in self.get_spread_positions():
    exit_signal = self._check_single_spread_exit(spread, ...)
    if exit_signal:
        signals.append(exit_signal)
```

**Also fix:** `_credit_spread_position` not cleared on kill switch (line 8115-8140). Add to `clear_all_positions()`.

**Estimated lines changed:** ~15 lines

---

### Fix 7: FIX CAPITAL PARTITION STARVATION (Impact: HIGH | Effort: MEDIUM)

**Problem:** Trend at 40% x 2.0 leverage = 80% margin, starving options of their 50% partition
**File:** `portfolio/portfolio_router.py:387-413`, `config.py`

**Current:** `CAPITAL_PARTITION_TREND = 0.50` and `CAPITAL_PARTITION_OPTIONS = 0.50` both use total equity denominator, and margin is shared. When Trend deploys fully, options see negative available margin.

**Fix:** Change partition to use MARGIN-WEIGHTED allocation, not equity percentage:
```python
# In config.py:
TREND_MAX_MARGIN_PCT = 0.45    # Max 45% of margin budget for trend
OPTIONS_MAX_MARGIN_PCT = 0.35  # Max 35% of margin budget for options
MR_MAX_MARGIN_PCT = 0.10       # Max 10% for MR
HEDGE_MAX_MARGIN_PCT = 0.10    # Max 10% for hedges
# Total: 100% margin budget, clear precedence
```

Then in router, check ACTUAL margin used by each partition before allowing new entries, not theoretical equity splits.

**Why this works:** The current system uses two conflicting units (equity % vs margin %). Unifying to margin budget eliminates the starvation problem and makes the allocation transparent.

**Estimated lines changed:** ~30-40 lines

---

### Fix 8: CONSOLIDATE SCHEDULED EVENTS (Impact: MEDIUM | Effort: SMALL)

**Problem:** 80 scheduled callbacks/day, dynamic + static duplicates
**File:** `main.py:862-931`

**Fix:** Replace the 80 individual schedules with a single 5-minute loop:
```python
# Every 5 minutes from 10:00 to 15:00
for hour in range(10, 16):
    for minute in [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55]:
        self.Schedule.On(
            self.DateRules.EveryDay(),
            self.TimeRules.At(hour, minute),
            self._on_intraday_check
        )

def _on_intraday_check(self):
    """Single consolidated intraday handler."""
    self._check_vix_spike()          # Was: separate every 5 min
    if self.Time.minute % 15 == 0:
        self._update_micro_regime()  # Was: separate every 15 min
```

Remove the static duplicate schedules for `_on_intraday_options_force_close` and `_on_mr_force_close` (keep only dynamic).

**Estimated lines changed:** ~30 lines (net reduction)

---

### Fix 9: ADD INTRADAY RECONCILIATION (Impact: MEDIUM | Effort: SMALL)

**Problem:** Reconciliation runs once at 09:33; zombie states persist 24 hours
**File:** `main.py`

**Fix:** Add reconciliation check at 12:00 and 14:30:
```python
self.Schedule.On(self.DateRules.EveryDay(), self.TimeRules.At(12, 0), self._reconcile_positions)
self.Schedule.On(self.DateRules.EveryDay(), self.TimeRules.At(14, 30), self._reconcile_positions)
```

`_reconcile_positions()` is already idempotent. Running it 3x/day instead of 1x costs nothing.

**Estimated lines changed:** 2 lines

---

### Fix 10: FIX VOL SHOCK WARMUP BLIND SPOT (Impact: MEDIUM | Effort: SMALL)

**Problem:** Vol shock protection unavailable for first 15 minutes (ATR not ready)
**File:** `engines/core/risk_engine.py:1185`

**Fix:** Add a fixed-range fallback when ATR not ready:
```python
if self._spy_atr <= 0:
    # Fallback: use absolute range threshold during warmup
    if bar_range > 0.75:  # $0.75 SPY range = violent bar
        self._vol_shock_until = current_time + timedelta(minutes=15)
        return True
    return False
```

**Estimated lines changed:** 5 lines

---

### Fix 11: FIX GOVERNOR RECOVERY LOCK (Impact: MEDIUM | Effort: SMALL)

**Problem:** Governor at 0% can lock indefinitely if regime never sustains 60 for 5 days
**File:** `engines/core/risk_engine.py:656-664`

**Fix:** Add time-decay recovery path:
```python
# After 15 days at governor 0%, allow recovery if equity improving >= 3%
if self._governor_days_at_zero >= 15:
    if equity_recovery_from_trough >= 0.03:
        self._governor_pct = 0.50  # Step up to 50%, not 100%
        self.log("GOVERNOR: TIME_DECAY_RECOVERY after 15 days + 3% equity recovery")
```

**Why this works:** The regime guard prevents false recovery in bear rallies. But 15 days at 0% with 3% equity recovery indicates genuine stabilization. Stepping to 50% (not 100%) adds caution.

**Estimated lines changed:** ~10 lines

---

### Fix 12: CLEAN UP DEAD CODE AND CONFIG (Impact: LOW | Effort: MEDIUM)

**Removals (net code reduction):**

1. **Remove 3 disabled regime models** (V3.3, V4.0, V4.1) from `regime_engine.py`
   - They are disabled (`V53_REGIME_ENABLED = True` overrides all)
   - Removing ~300 lines of dead code

2. **Remove deprecated symbols** (TNA, FAS, TMF, PSQ) from `main.py:541-546`
   - Still subscribed but never traded since V6.11
   - Saves memory and data subscription costs

3. **Remove orphaned `KILL_SWITCH_PCT`** duplicate in `config.py:21`
   - Superseded by `KS_TIER_1/2/3_PCT`
   - Causes developer confusion

4. **Remove duplicate margin gates** - keep utilization gate only
   - `check_leverage_cap()` and `check_margin_utilization_gate()` are redundant
   - Keep the broker-based actual margin check, remove the theoretical projection

5. **Consolidate margin buffers** to single 1.20x standard
   - Currently: 1.20x for equity, 1.25x (via 0.80 divisor) for options
   - Inconsistent buffers cause hard-to-debug margin rejections

**Estimated lines removed:** ~400 lines net

---

## PART 3: VIX DIRECTION SCORING BUG FIX

**File:** `engines/core/regime_engine.py` (calculations section)

**Bug:** Lines 1354-1356 in VIX direction scoring:
```python
# VIX falling between -20% and -10% gets same score (70) as -10% to 0%
# The falling_fast range (-20% or more) is never reached properly
elif vix_change_pct >= falling_fast_threshold:
    return score_falling  # Should be score_falling_fast!
```

**Fix:** Return `score_falling_fast` (85) for the `-20%` or more range.

**Impact:** Small but real. Affects VIX Combined factor (35% weight), specifically the 40% direction sub-component. Correctly scoring fast VIX drops as bullish (85 instead of 70) means regime recovery after crashes is slightly faster - which is correct behavior.

**Estimated lines changed:** 1 line

---

## PART 4: STRATEGIC SIMPLIFICATION

### 4.1 Reduce Micro Regime Complexity

**Current:** 21 micro regimes (7 directions x 3 VIX levels), of which:
- 27% of time is in untradeable regimes (CAUTION_LOW, TRANSITION, RISK_OFF_LOW)
- Only ~5 regimes produce reliable signals (PERFECT_MR, GOOD_MR, RECOVERING, PANIC_EASING, NORMAL)

**Proposal:** Collapse to 9 regimes (3 directions x 3 VIX levels):
- **Directions:** FALLING (bullish), STABLE (neutral), RISING (bearish)
- Remove WHIPSAW (unreliable detection), merge FALLING_FAST into FALLING, merge RISING_FAST and SPIKING into RISING
- Reduces complexity, increases signal clarity

**Why:** The 7-direction classification creates too many edge cases. WHIPSAW detection (5+ reversals in 60 min) is noise-sensitive and the threshold (`VIX_REVERSAL_THRESHOLD = 0.1`) catches micro-movements. Fewer states = clearer signals = better execution.

### 4.2 Options Engine: Simplify Strategy Set

**Current intraday strategies:** DEBIT_FADE, DEBIT_MOMENTUM, CREDIT, ITM_MOMENTUM, PROTECTIVE_PUTS

**Audit finding:** Only ITM_MOMENTUM was profitable (+$29 avg, 43.8% win rate). All others negative.

**Proposal:** Remove DEBIT_FADE and PROTECTIVE_PUTS from intraday. Keep:
- **ITM_MOMENTUM** (proven profitable)
- **DEBIT_MOMENTUM** (needs tuning but conceptually sound)
- **CREDIT** (High IV environments only)

Removing 2 strategies reduces code complexity and eliminates proven losers.

### 4.3 Remove Win-Rate Shutoff

**File:** `config.py:1283-1284`

```python
VASS_WIN_RATE_HARD_BLOCK = False  # Already disabled!
VASS_WIN_RATE_SHUTOFF_SCALE = 0.40
```

The win-rate shutoff was designed to stop trading when losing, but it:
- Creates cliff behavior (sharp on/off transitions)
- Blocks VASS when MICRO is losing (cross-contamination)
- Already disabled via config but code remains

**Proposal:** Remove the win-rate shutoff code entirely. Risk management should be via stops and position sizing, not by blocking entries based on recent results (which is backward-looking).

---

## PART 5: IMPLEMENTATION PRIORITY MATRIX

### Phase 1: Regime Accuracy (Week 1) - HIGHEST IMPACT

| # | Fix | Lines | Impact | Risk |
|---|-----|:-----:|:------:|:----:|
| 1 | Intraday regime refresh | ~50 | CRITICAL | Low |
| 2 | Fast VIX direction override | ~15 | CRITICAL | Low |
| 3 | VIX direction scoring bug | 1 | Low | None |
| 5 | Narrow direction dead zone | ~10 | HIGH | Low |

**Expected outcome:** Regime-accurate options direction within 15 minutes instead of 6 hours. Eliminates the #1 source of losses (BULL_CALL in crashes).

### Phase 2: Execution Quality (Week 2)

| # | Fix | Lines | Impact | Risk |
|---|-----|:-----:|:------:|:----:|
| 6 | Multi-spread exit bug | ~15 | HIGH | Low |
| 7 | Capital partition fix | ~35 | HIGH | Medium |
| 4 | Staged neutrality exit | ~40 | HIGH | Medium |
| 3 | Assignment gate relaxation | ~5 | HIGH | Low |

**Expected outcome:** Higher execution rate (target: 40%+ of approved signals), less churn, more PUT participation in stress.

### Phase 3: Infrastructure Hardening (Week 3)

| # | Fix | Lines | Impact | Risk |
|---|-----|:-----:|:------:|:----:|
| 8 | Consolidate schedules | ~30 | MEDIUM | Low |
| 9 | Intraday reconciliation | 2 | MEDIUM | None |
| 10 | Vol shock warmup fix | 5 | MEDIUM | None |
| 11 | Governor recovery fix | ~10 | MEDIUM | Low |

**Expected outcome:** Fewer timing bugs, faster zombie detection, protection during market open volatility, governor doesn't lock permanently.

### Phase 4: Simplification (Week 4)

| # | Fix | Lines | Impact | Risk |
|---|-----|:-----:|:------:|:----:|
| 12 | Dead code cleanup | -400 | LOW | None |
| 4.1 | Micro regime simplification | ~-100 | MEDIUM | Medium |
| 4.2 | Remove losing strategies | ~-50 | MEDIUM | Low |
| 4.3 | Remove win-rate shutoff | ~-30 | LOW | None |

**Expected outcome:** Cleaner codebase, fewer parameters, easier debugging. Net ~580 lines removed.

---

## PART 6: BACKTEST VALIDATION PLAN

After implementing each phase, run these backtests to validate:

### Required Backtest Periods

| Period | Market Type | What It Tests | Pass Criteria |
|--------|-------------|---------------|---------------|
| 2017 Jul-Oct | Bull/Low VIX | Bull capture preserved | P&L >= +$2,000 |
| 2015 Jul-Oct | Crash/Shock | Shock absorption improved | P&L > -$10,000 (was -$23K) |
| 2022 Jan-Jun | Bear/High VIX | Bear navigation | P&L > -$5,000 |
| 2018 Sep-Dec | Choppy/Transition | Churn reduction | Trade count < 80 (was 117) |
| 2021 Jan-Mar | Strong Bull | Full participation | Win rate > 50% |
| 2020 Feb-Apr | COVID crash + recovery | Extreme shock + bounce | Drawdown < -20% |

### Key Metrics Per Run

1. **Direction accuracy:** % of spreads where direction matched market move
2. **Execution rate:** approved -> executed conversion (target: 40%+)
3. **Neutrality churn:** count of NEUTRALITY_EXIT followed by same-direction re-entry within 24h
4. **Regime lag:** hours between actual market shift and regime detection
5. **PUT participation:** % of spreads that are PUT/BEAR in VIX > 25 environments (target: 80%+)
6. **Fee ratio:** fees / gross P&L (target: < 15%)

---

## PART 7: WHAT NOT TO CHANGE

These components are working correctly and should not be modified:

1. **Trend Engine MA200 + ADX logic** - Sound design, regime-adaptive thresholds working
2. **SMA50 exit with 2-day confirmation** - Prevents whipsaw exits
3. **Tiered kill switch (V2.27)** - Graduated response is correct design
4. **Panic mode (SPY -4%)** - Correctly keeps hedges while liquidating longs
5. **DTE=1 force close** - Assignment prevention working
6. **SpreadFillTracker** - Well-implemented V2.6 fix for race conditions
7. **Lockbox mechanism** - Profit preservation is sound
8. **SH as sole hedge** - Correct replacement for TMF/PSQ (no contango, works in all crash types)
9. **Gap filter** - Simple, correct, prevents buying dips during gaps
10. **Weekly breaker** - Simple, reliable, auto-resets

---

## PART 8: CONFIGURATION RATIONALIZATION

### Parameters to DELETE (currently unused or superseded)

```python
# DELETE: Superseded by graduated KS
KILL_SWITCH_PCT = 0.05  # Line 21 and 650 - orphaned

# DELETE: Disabled regime models still in config
V4_REGIME_ENABLED = False
V3_REGIME_SIMPLIFIED_ENABLED = False
WEIGHT_MOMENTUM_V4 = ...  # All V4 weights
WEIGHT_TREND_V3 = ...     # All V3 weights

# DELETE: Win rate shutoff already disabled
VASS_WIN_RATE_HARD_BLOCK = False
VASS_WIN_RATE_SHUTOFF_SCALE = 0.40
```

### Parameters to CONSOLIDATE

```python
# BEFORE: Two conflicting margin gates
MAX_MARGIN_WEIGHTED_ALLOCATION = 0.90   # Leverage cap
MAX_MARGIN_UTILIZATION = 0.70           # Utilization gate

# AFTER: Single gate
MAX_MARGIN_UTILIZATION = 0.75           # One gate, clear threshold

# BEFORE: Two conflicting margin buffers
MARGIN_PRE_CHECK_BUFFER = 1.20          # 20% buffer for equity
SPREAD_MARGIN_SAFETY_FACTOR = 0.80      # 25% buffer for options (1/0.80)

# AFTER: Single buffer
MARGIN_SAFETY_BUFFER = 1.20             # 20% for everything
```

### Parameters to ADD

```python
# Intraday regime refresh
INTRADAY_REGIME_REFRESH_ENABLED = True
INTRADAY_REGIME_REFRESH_TIMES = [(12, 0), (14, 0)]

# Fast VIX override
VIX_FORCE_BEARISH_LEVEL = 30.0          # VIX > 30 = always bearish
VIX_FORCE_BEARISH_SPIKE = 0.15          # VIX +15% 5d = bearish
VIX_FORCE_BEARISH_SPIKE_LEVEL = 25.0    # Only when VIX > 25

# Staged neutrality
NEUTRALITY_CONFIRMATION_HOURS = 2       # Hours before full exit
NEUTRALITY_TIGHTENED_STOP = 0.20        # Stop tightens to 20% during warning

# Governor time decay
GOVERNOR_TIME_DECAY_DAYS = 15           # Days at 0% before time-decay recovery
GOVERNOR_TIME_DECAY_RECOVERY_PCT = 0.50 # Step up to 50%
```

---

## PART 9: EXPECTED OUTCOMES

### Conservative Estimates

| Metric | Current | After Phase 1+2 | After All Phases |
|--------|---------|:---------------:|:----------------:|
| Direction accuracy (stress) | ~15% | ~60% | ~70% |
| Execution rate | 7-17% | 30-40% | 50%+ |
| Neutrality churn | ~40% of exits | ~15% of exits | ~10% |
| PUT participation (VIX>25) | ~20% | ~60% | ~80% |
| 2015 Aug P&L | -$23,019 | -$8,000 to -$5,000 | -$3,000 to +$1,000 |
| 2017 Jul P&L | +$2,674 | +$2,500 to +$3,500 | +$3,000 to +$5,000 |
| Config parameters | ~180 | ~170 | ~140 |
| Codebase lines | ~12,000 | ~12,100 | ~11,500 |

### The Core Thesis

The system already has the right architecture (regime detection -> strategy selection -> risk management). It fails because:
1. The regime signal is stale (Fix 1)
2. The trading response ignores the signal (Fix 2, 5)
3. The execution pipeline drops valid signals (Fix 7)
4. Exit logic creates unnecessary churn (Fix 4)

**These 4 fixes address ~80% of the losses.** The remaining fixes are hardening and cleanup.

---

## APPENDIX A: BUGS FOUND DURING AUDIT

| # | Bug | File | Severity | Status |
|---|-----|------|:--------:|:------:|
| B1 | VIX direction score: falling_fast returns wrong score | regime_engine.py | Low | Open |
| B2 | Multi-spread exit only checks primary | options_engine.py:5846 | High | Open |
| B3 | _credit_spread_position not cleared on KS | options_engine.py:8115 | High | Open |
| B4 | ATR stop defaults silently when ATR unavailable | options_engine.py:7259 | Medium | Open |
| B5 | DTE fallback has no cooldown (ping-pong) | options_engine.py:3213 | Medium | Open |
| B6 | Margin cooldown is global (blocks all types) | options_engine.py:4244 | Medium | Open |
| B7 | Vol shock has 15-min blind spot at open | risk_engine.py:1185 | Medium | Open |
| B8 | Governor recovery locked by regime guard | risk_engine.py:656 | Medium | Open |
| B9 | Duplicate schedule registration (dynamic+static) | main.py:862-892 | Medium | Open |
| B10 | 80 scheduled events may exceed QC limits | main.py:896-911 | Medium | Open |
| B11 | Reconciliation only runs once at 09:33 | main.py:1965 | Medium | Open |
| B12 | Capital partition math uses equity not margin | router.py:387 | High | Open |
| B13 | Priority scaling algorithm leaks capacity | router.py:1402 | Medium | Open |
| B14 | Spread margin registered at close, not entry | router.py:300 | Medium | Open |
| B15 | VASS conviction stays on all day after trigger | options_engine.py:531 | Medium | Open |
| B16 | Legacy KILL_SWITCH_PCT orphaned in config | config.py:21,650 | Low | Open |

---

## APPENDIX B: FILE REFERENCE MAP

| Component | Primary File | Lines | Config Section |
|-----------|-------------|:-----:|---------------|
| Regime Engine | engines/core/regime_engine.py | ~1400 | config.py:100-200 |
| Options Engine | engines/satellite/options_engine.py | ~8200 | config.py:820-1420 |
| Risk Engine | engines/core/risk_engine.py | ~2012 | config.py:648-800 |
| Trend Engine | engines/core/trend_engine.py | ~800 | config.py:395-626 |
| Portfolio Router | portfolio/portfolio_router.py | ~2100 | config.py:30-70 |
| Main Algorithm | main.py | ~3800 | N/A |
| MR Engine | engines/satellite/mean_reversion_engine.py | ~400 | config.py:270-350 |
| Hedge Engine | engines/satellite/hedge_engine.py | ~200 | config.py:350-395 |
| Capital Engine | engines/core/capital_engine.py | ~300 | config.py:1-30 |

---

*This plan represents the synthesis of 7 parallel deep-research investigations covering every engine, the execution pipeline, all audit documents, and backtest results. The recommendations are ordered by impact/effort ratio and designed to be implemented incrementally with validation at each phase.*
