# Regime Engine Calibration Issue

**Date Identified:** 2026-02-05
**Severity:** HIGH
**Status:** DOCUMENTED - Awaiting Fix
**Affects:** Regime Engine, Options Engine, Hedge Engine

---

## Executive Summary

The regime engine is **not correctly identifying bear market regimes**. During a -30% Nasdaq decline (2022 Q1-Q2), the regime score never dropped below CAUTIOUS (40-49), meaning DEFENSIVE and BEAR playbooks were never activated. This is a fundamental calibration failure.

---

## Problem Statement

### Issue 1: Regime Score Compression

**Observation:** In sustained bear markets, the regime score stays compressed in the NEUTRAL/CAUTIOUS range and never reaches DEFENSIVE or BEAR levels.

**Evidence from 2022 Q1-Q2 Backtest:**
- Market: Nasdaq -30%, S&P -20%
- Expected regime: Should reach BEAR (0-29) at worst points
- Actual regime: Score ranged **43-65**, never below 43
- Transitions observed: NEUTRAL ↔ CAUTIOUS only
- Transitions NOT observed: CAUTIOUS → DEFENSIVE → BEAR

**Regime Boundaries:**
| Regime | Score Range | 2022 Q1-Q2 | Status |
|--------|-------------|------------|--------|
| RISK_ON / BULL | 70-100 | Never reached | Expected |
| NEUTRAL | 50-69 | ✅ Reached (50-65) | OK |
| CAUTIOUS | 40-49 | ✅ Reached (43-49) | OK |
| DEFENSIVE | 30-39 | ❌ Never reached | **PROBLEM** |
| RISK_OFF / BEAR | 0-29 | ❌ Never reached | **PROBLEM** |

### Issue 2: Options Routing Contract Violation

**Observation:** The options engine does not strictly obey macro regime constraints defined in the investment thesis.

**Investment Thesis (What SHOULD Happen):**
| Regime | Options Behavior |
|--------|------------------|
| BULL (70+) | Bullish options allowed |
| NEUTRAL (50-69) | **NO new options** (dead zone) |
| CAUTIOUS (30-49) | **Bearish PUT structures ONLY** |
| BEAR (0-29) | Bearish PUT structures only |

**Actual Code Behavior:**
| Regime | What Code Does | Violation? |
|--------|----------------|------------|
| BULL (70+) | Bullish options allowed | ✅ Correct |
| NEUTRAL (50-69) | Options allowed (via Micro Regime) | ❌ **VIOLATION** |
| CAUTIOUS (30-49) | Mixed options allowed | ❌ **VIOLATION** |
| BEAR (0-29) | Bearish only | ✅ Correct |

**Evidence from 2022 Q1-Q2:**
```
Total Options Trades: 145
Intraday Trades: 127
Regime during most trades: NEUTRAL (50-65)
Win Rate: 30.7% (below 40% target)
```

Options were entered heavily during NEUTRAL regime, violating the "no options in dead zone" thesis.

---

## Root Cause Analysis

### Why Score Compression Happens

The regime factors are calibrated for **panic detection**, not **sustained bear detection**:

| Factor | Weight | Panic Behavior | Grinding Bear Behavior |
|--------|--------|----------------|------------------------|
| Trend | 20% | Price crashes below MAs → Score 0-20 | Price below MAs but not crashing → Score 30-40 |
| VIX Level | 15% | VIX spikes to 40+ → Score 0-20 | VIX elevated at 25-30 → Score 40-50 |
| VIX Direction | 15% | VIX spiking +15% daily → Score 0-25 | VIX choppy → Score 40-50 |
| Breadth | 15% | Capitulation breadth → Score 0-20 | Rolling weakness → Score 40-50 |
| Credit | 15% | Spreads blow out → Score 0-20 | Orderly widening → Score 40-50 |
| Chop/ADX | 10% | Strong downtrend → Score 30-40 | Choppy downtrend → Score 40-50 |
| Volatility | 10% | Extreme realized vol → Score 0-20 | Elevated vol → Score 40-50 |

**In a grinding bear market:**
- Weighted average of factors: ~40-50
- This lands in CAUTIOUS, never reaches DEFENSIVE or BEAR
- The engine is **blind to sustained bear markets**

### Why Options Routing Fails

Two separate systems operate with different rules:

1. **Daily Regime Engine** — Macro state classification (BULL/NEUTRAL/CAUTIOUS/BEAR)
2. **Micro Regime Engine** — Intraday timing (FAVORABLE/NORMAL/DETERIORATING)

**The Problem:**
- Options engine checks **Micro Regime** for entry timing
- Options engine does NOT strictly check **Daily Regime** for permission
- Result: Options trade in NEUTRAL/CAUTIOUS based on micro signals alone

```python
# Current behavior (WRONG):
if micro_regime == "FAVORABLE":
    enter_options()  # No daily regime check!

# Required behavior (CORRECT):
if daily_regime >= BULL and micro_regime == "FAVORABLE":
    enter_bullish_options()
elif daily_regime <= CAUTIOUS and micro_regime indicates bear setup:
    enter_bearish_options()
else:
    # NEUTRAL = no options
    pass
```

---

## Impact Assessment

### What Breaks When Regime Doesn't Reach BEAR

| System | Expected in BEAR | Actual (stuck in CAUTIOUS) |
|--------|------------------|---------------------------|
| Hedges | TMF 20% + PSQ 10% | TMF 10% only |
| Longs | Blocked | Allowed (scaled) |
| Options | Bearish PUTs only | Mixed (violations) |
| Governor | Defensive posture | Partial defense |

### Quantified Impact (2022 Q1-Q2)

- **Hedge underallocation:** Should have had 30% hedges, had ~10%
- **Options losses:** 145 trades at 30.7% win rate in "dead zone"
- **Result:** -9.8% return (could have been better with proper regime)

---

## Proposed Fixes

### Fix 1: Add Drawdown Factor to Regime Engine

Add a new factor that directly measures market drawdown from peak:

```python
# New factor: Market Drawdown
# Measures SPY drawdown from 52-week high
WEIGHT_DRAWDOWN = 0.15  # Take from other factors

def drawdown_factor_score(spy_current, spy_52w_high):
    """
    Score based on SPY drawdown from peak.

    Drawdown    Score
    0-5%        100 (bull market)
    5-10%       70  (correction)
    10-15%      50  (pullback)
    15-20%      30  (bear territory)
    20%+        10  (deep bear)
    """
    dd_pct = (spy_52w_high - spy_current) / spy_52w_high
    if dd_pct <= 0.05:
        return 100
    elif dd_pct <= 0.10:
        return 70
    elif dd_pct <= 0.15:
        return 50
    elif dd_pct <= 0.20:
        return 30
    else:
        return 10
```

### Fix 2: Recalibrate Existing Factor Thresholds

Current thresholds are too conservative. Adjust to score lower in sustained weakness:

| Factor | Current Low Threshold | Proposed Low Threshold |
|--------|----------------------|------------------------|
| VIX Level | VIX > 30 → Score 20 | VIX > 25 → Score 30 |
| Breadth | RSP/SPY < -2% → Score 20 | RSP/SPY < -1% → Score 30 |
| Credit | HYG/IEF < -1% → Score 20 | HYG/IEF < -0.5% → Score 30 |

### Fix 3: Add Time-Based Regime Decay

If stuck in CAUTIOUS for extended period, force transition to DEFENSIVE:

```python
# If CAUTIOUS for 20+ consecutive days, step down to DEFENSIVE
REGIME_TIME_DECAY_ENABLED = True
REGIME_TIME_DECAY_DAYS = 20
REGIME_TIME_DECAY_STEP = 10  # Reduce score by 10 points
```

### Fix 4: Enforce Options Macro Regime Gate

Add hard gate in options engine:

```python
def can_enter_options(self, daily_regime_score, option_type):
    """
    Enforce macro regime constraints on options.

    BULL (70+): Bullish options allowed
    NEUTRAL (50-69): NO options (dead zone)
    CAUTIOUS (30-49): Bearish PUTs only
    BEAR (0-29): Bearish PUTs only
    """
    if daily_regime_score >= 70:
        return True  # Bull - all options allowed
    elif daily_regime_score >= 50:
        return False  # Neutral - NO options
    else:
        # Cautious/Bear - bearish only
        return option_type in ["BEAR_PUT", "PUT_SPREAD"]
```

---

## Priority and Sequencing

| Fix | Priority | Complexity | Impact |
|-----|----------|------------|--------|
| Fix 4: Options macro gate | P0 | Low | High - stops contract violations |
| Fix 1: Drawdown factor | P1 | Medium | High - fixes score compression |
| Fix 2: Threshold recalibration | P2 | Medium | Medium - improves sensitivity |
| Fix 3: Time decay | P3 | Low | Low - edge case handling |

---

## Validation Plan

After implementing fixes, run backtests on:

1. **2022 Q1-Q2** (grinding bear) — Regime should reach DEFENSIVE/BEAR
2. **2015 Full Year** (choppy/flat) — Regime should identify Aug crash
3. **2020 Mar** (panic crash) — Regime should reach BEAR quickly
4. **2021 Full Year** (bull market) — Regime should stay BULL/NEUTRAL

**Success Criteria:**
- Regime reaches BEAR (0-29) during -20%+ market drawdowns
- Options only trade in BULL regime (or bearish structures in CAUTIOUS/BEAR)
- Win rate improves to 40%+ when options obey macro constraints

---

## References

- Audit Report: `docs/audits/V3_0_2022_Q1Q2_audit.md`
- Audit Report: `docs/audits/V3_0_2015_FullYear_audit.md`
- Regime Engine: `engines/core/regime_engine.py`
- Options Engine: `engines/satellite/options_engine.py`
- Config: `config.py` (WEIGHT_* and threshold parameters)

---

## Changelog

| Date | Author | Change |
|------|--------|--------|
| 2026-02-05 | Claude/VA | Initial documentation |
