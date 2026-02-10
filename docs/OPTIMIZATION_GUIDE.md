# Alpha NextGen V6.7 — Options Engine Optimization Guide

> **Goal:** Generate consistent income through active intraday and swing options trading.
> **Mode:** Options Isolation (Trend/MR engines disabled)
> **Source of Truth:** Current codebase (`config.py`, `options_engine.py`)
> **Last Updated:** 2026-02-08

---

## Investment Thesis

The Options Engine should **actively trade** to generate consistent returns across all market conditions. Every signal should get a direction — no idle "neutral" zones.

---

## V6.7 Cross-Period Performance Baseline

| Period | Market Type | Return | Intraday Trades | Spread Trades | Win Rate |
|--------|-------------|--------|-----------------|---------------|----------|
| **2015 H1** | Choppy | +4.5% | 8 | 33 | 47% |
| **2017 H1** | Bull | +26.5% | 3 | 23 | 52% |
| **2022 Jan-Feb** | Bear | -46.8% | 68 | 17 | 22% |

**Problem:** Intraday mode fires too few trades in bull/choppy markets (3-8 vs target 50+).

---

## 1. MICRO Regime Engine (Intraday Direction)

### Current VIX Direction Thresholds

```python
# config.py — Lines 1325-1331

VIX_DIRECTION_FALLING_FAST = -3.0   # VIX change < -3%
VIX_DIRECTION_FALLING = -1.0        # VIX change < -1%
VIX_DIRECTION_STABLE_LOW = -1.0     # STABLE zone: -1% to +1%
VIX_DIRECTION_STABLE_HIGH = 1.0     # STABLE zone: -1% to +1%
VIX_DIRECTION_RISING = 3.0          # VIX change > +3%
VIX_DIRECTION_RISING_FAST = 6.0     # VIX change > +6%
VIX_DIRECTION_SPIKING = 10.0        # VIX change > +10%
```

**Issue:** STABLE zone (-1% to +1%) captures 83-98% of signals → Dir=NONE.

### Current VIX Level Classification

```python
# options_engine.py — classify_vix_level()

VIX < 11.5   → VERY_CALM  (Score: 25)
VIX 11.5-15  → CALM       (Score: 20)
VIX 15-18    → NORMAL     (Score: 15)
VIX 18-22    → ELEVATED   (Score: 10)
VIX 22-25    → HIGH       (Score: 5)
VIX > 25     → EXTREME    (Score: 0)
```

### Current MICRO Regime Matrix

```python
# options_engine.py — Lines 1095-1134

# VIX Level LOW:
{
    FALLING_FAST: PERFECT_MR,
    FALLING: GOOD_MR,
    STABLE: NORMAL,        # ← Most signals land here
    RISING: CAUTIOUS,
    RISING_FAST: WORSENING,
    SPIKING: BREAKING,
    WHIPSAW: UNSTABLE
}

# VIX Level MEDIUM:
{
    FALLING_FAST: IMPROVING,
    FALLING: PANIC_EASING,
    STABLE: CAUTIOUS,      # ← Most signals land here
    RISING: DETERIORATING,
    RISING_FAST: WORSENING_HIGH,
    SPIKING: CRASH,
    WHIPSAW: UNSTABLE
}

# VIX Level HIGH:
{
    FALLING_FAST: CALMING,
    FALLING: PANIC_EASING,
    STABLE: ELEVATED,      # ← Most signals land here
    RISING: WORSENING_HIGH,
    RISING_FAST: CRASH,
    SPIKING: FULL_PANIC,
}
```

### MICRO Direction Scores

```python
# config.py — Lines 1376-1382

MICRO_SCORE_DIR_FALLING_FAST = 20   # Fear easing rapidly
MICRO_SCORE_DIR_FALLING = 15        # Fear easing
MICRO_SCORE_DIR_STABLE = 10         # Neutral
MICRO_SCORE_DIR_RISING = 5          # Fear building
MICRO_SCORE_DIR_RISING_FAST = 0     # Fear accelerating
MICRO_SCORE_DIR_SPIKING = -5        # Panic mode penalty
MICRO_SCORE_DIR_WHIPSAW = -10       # Chaos penalty
```

### Optimization Parameters

| Parameter | Current | Range | Impact |
|-----------|---------|-------|--------|
| `VIX_DIRECTION_STABLE_LOW` | -1.0% | -2.0 to 0.0 | Narrow = more directional signals |
| `VIX_DIRECTION_STABLE_HIGH` | +1.0% | 0.0 to +2.0 | Narrow = more directional signals |
| `VIX_DIRECTION_RISING` | +3.0% | +2.0 to +5.0 | Lower = earlier RISING detection |
| `VIX_DIRECTION_FALLING` | -1.0% | -3.0 to -0.5 | Higher = earlier FALLING detection |

---

## 2. Regime Engine (Macro Direction for Spreads)

### Current Spread Direction Thresholds

```python
# config.py — Lines 1091-1093

SPREAD_REGIME_BULLISH = 70   # Regime > 70 → CALL spreads
SPREAD_REGIME_BEARISH = 50   # Regime < 50 → PUT spreads
SPREAD_REGIME_CRISIS = 0     # Disabled — PUTs work in all bear regimes
```

### Current V5.3 Regime Guards

```python
# config.py — Lines 254-270

# Spike Cap (caps regime during VIX spikes)
V53_SPIKE_CAP_ENABLED = True
V53_SPIKE_CAP_THRESHOLD = 0.28       # VIX up >28% in 5 days
V53_SPIKE_CAP_MAX_SCORE = 38         # Cap at DEFENSIVE (38)
V53_SPIKE_CAP_DECAY_DAYS = 3         # Persists for 3 days

# Breadth Decay (penalizes narrow rallies)
V53_BREADTH_DECAY_ENABLED = True
V53_BREADTH_5D_DECAY_THRESHOLD = -0.02   # RSP/SPY ratio decay <= -2%
V53_BREADTH_10D_DECAY_THRESHOLD = -0.04  # RSP/SPY ratio decay <= -4%
V53_BREADTH_5D_PENALTY = 5               # -5 points for 5d decay
V53_BREADTH_10D_PENALTY = 8              # -8 points for 10d decay
```

### Optimization Parameters

| Parameter | Current | Range | Impact |
|-----------|---------|-------|--------|
| `SPREAD_REGIME_BULLISH` | 70 | 60-75 | Lower = more CALL spreads |
| `SPREAD_REGIME_BEARISH` | 50 | 45-55 | Higher = more PUT spreads |
| `V53_SPIKE_CAP_MAX_SCORE` | 38 | 35-45 | Lower = faster DEFENSIVE |
| `V53_BREADTH_5D_DECAY_THRESHOLD` | -0.02 | -0.01 to -0.03 | Sensitivity |

---

## 3. VASS (Volatility-Adaptive Strategy Selection)

### Current VASS Thresholds

```python
# config.py — Lines 823-834

VASS_ENABLED = True
VASS_IV_LOW_THRESHOLD = 16           # VIX < 16 = Low IV
VASS_IV_HIGH_THRESHOLD = 25          # VIX > 25 = High IV
VASS_IV_SMOOTHING_MINUTES = 30       # SMA window

# DTE by IV Environment
VASS_LOW_IV_DTE_MIN = 30             # Monthly (30-45 DTE)
VASS_LOW_IV_DTE_MAX = 45
VASS_MEDIUM_IV_DTE_MIN = 7           # Weekly (7-21 DTE)
VASS_MEDIUM_IV_DTE_MAX = 21
VASS_HIGH_IV_DTE_MIN = 5             # V6.8: Allow trades in high IV
VASS_HIGH_IV_DTE_MAX = 40            # V6.13.1: Expand candidate pool
```

### VASS Conviction Thresholds

```python
# config.py — Lines 842-849

VASS_VIX_5D_BEARISH_THRESHOLD = 0.20     # VIX 5d change > +20% → BEARISH
VASS_VIX_5D_BULLISH_THRESHOLD = -0.15    # VIX 5d change < -15% → BULLISH
VASS_VIX_20D_STRONG_BEARISH = 0.30       # VIX 20d change > +30% → STRONG BEARISH
VASS_VIX_20D_STRONG_BULLISH = -0.20      # VIX 20d change < -20% → STRONG BULLISH

VASS_VIX_FEAR_CROSS_LEVEL = 25           # VIX crosses above → BEARISH
VASS_VIX_COMPLACENT_CROSS_LEVEL = 15     # VIX crosses below → BULLISH
```

### Optimization Parameters

| Parameter | Current | Range | Impact |
|-----------|---------|-------|--------|
| `VASS_IV_LOW_THRESHOLD` | 16 | 14-18 | Credit vs Debit routing |
| `VASS_IV_HIGH_THRESHOLD` | 25 | 25-32 | When to sell premium |
| `VASS_VIX_5D_BEARISH_THRESHOLD` | 0.20 | 0.15-0.25 | BEARISH conviction sensitivity |
| `VASS_VIX_5D_BULLISH_THRESHOLD` | -0.15 | -0.20 to -0.10 | BULLISH conviction sensitivity |

---

## 4. MICRO Conviction Engine

### Current Conviction Thresholds

```python
# config.py — Lines 1343-1352

MICRO_UVXY_BEARISH_THRESHOLD = 0.025     # UVXY +2.5% → BEARISH conviction
MICRO_UVXY_BULLISH_THRESHOLD = -0.03     # UVXY -3% → BULLISH conviction
MICRO_VIX_CRISIS_LEVEL = 35              # VIX > 35 → CRISIS (BEARISH)
MICRO_VIX_COMPLACENT_LEVEL = 12          # VIX < 12 → COMPLACENT (BULLISH)

# Regime States → Direction
MICRO_BEARISH_STATES = ["FULL_PANIC", "CRASH", "WORSENING_HIGH", "BREAKING", "DETERIORATING"]
MICRO_BULLISH_STATES = ["PERFECT_MR", "GOOD_MR", "IMPROVING", "PANIC_EASING", "CALMING"]
```

### Optimization Parameters

| Parameter | Current | Range | Impact |
|-----------|---------|-------|--------|
| `MICRO_UVXY_BEARISH_THRESHOLD` | 0.025 | 0.02-0.05 | BEARISH signal sensitivity |
| `MICRO_UVXY_BULLISH_THRESHOLD` | -0.03 | -0.05 to -0.02 | BULLISH signal sensitivity |
| `MICRO_VIX_CRISIS_LEVEL` | 35 | 30-40 | When to force BEARISH |
| `MICRO_VIX_COMPLACENT_LEVEL` | 12 | 10-14 | When to force BULLISH |

---

## 5. Position Sizing

### Current Allocation

```python
# config.py

OPTIONS_ALLOCATION_MIN = 0.25            # 25% minimum
OPTIONS_ALLOCATION_MAX = 0.30            # 30% maximum
OPTIONS_SWING_ALLOCATION = 0.1875        # 18.75% for Swing
OPTIONS_INTRADAY_ALLOCATION = 0.0625     # 6.25% for Intraday

OPTIONS_MAX_INTRADAY_POSITIONS = 1       # Max 1 intraday at a time
INTRADAY_MAX_TRADES_PER_DAY = 2          # Max 2 trades per day
```

### Spread Sizing

```python
# config.py — Lines 1112, 1273

SPREAD_DTE_MAX = 45                      # Maximum DTE for spreads
INTRADAY_SPREAD_MAX_PCT = 0.08           # 8% of portfolio for intraday spreads
MIN_INTRADAY_OPTIONS_TRADE_VALUE = 500   # Minimum trade value
```

### Optimization Parameters

| Parameter | Current | Range | Impact |
|-----------|---------|-------|--------|
| `OPTIONS_INTRADAY_ALLOCATION` | 0.0625 | 0.05-0.10 | Intraday capital |
| `INTRADAY_MAX_TRADES_PER_DAY` | 2 | 1-4 | Daily activity |
| `OPTIONS_MAX_INTRADAY_POSITIONS` | 1 | 1-2 | Concurrent positions |

---

## 6. Exit Management

### Current Profit/Stop Targets

```python
# config.py

OPTIONS_PROFIT_TARGET_PCT = 0.50         # +50% profit target
SPREAD_PROFIT_TARGET_PCT = 0.50          # +50% for spreads
CREDIT_SPREAD_PROFIT_TARGET = 0.50       # Exit at 50% of max profit

# Intraday-specific
INTRADAY_ITM_TARGET = 0.40               # +40% for ITM momentum
INTRADAY_ITM_STOP = 0.35                 # -35% stop
INTRADAY_ITM_TRAIL_TRIGGER = 0.20        # Trail after +20%
INTRADAY_ITM_TRAIL_PCT = 0.50            # Trail at 50% of gains

INTRADAY_CREDIT_TARGET = 0.50            # 50% of max profit
INTRADAY_CREDIT_STOP = 1.0               # Stop if spread doubles
```

### Optimization Parameters

| Parameter | Current | Range | Impact |
|-----------|---------|-------|--------|
| `OPTIONS_PROFIT_TARGET_PCT` | 0.50 | 0.30-0.60 | When to take profits |
| `INTRADAY_ITM_STOP` | 0.35 | 0.25-0.50 | When to cut losses |
| `INTRADAY_ITM_TRAIL_TRIGGER` | 0.20 | 0.15-0.30 | When to start trailing |

---

## 7. Intraday Strategy Parameters

### DEBIT_FADE (Mean Reversion)

```python
# config.py — Lines 1432-1443

INTRADAY_DEBIT_FADE_VIX_MIN = 9.5        # V6.13 OPT: Allow in calm bull/choppy
INTRADAY_DEBIT_FADE_VIX_MAX = 25         # Max VIX for fade
INTRADAY_DEBIT_FADE_MIN_SCORE = 35       # Micro score >= 35
INTRADAY_FADE_MIN_MOVE = 0.50            # Min QQQ move 0.5%
INTRADAY_FADE_MAX_MOVE = 1.50            # Max move 1.5%
INTRADAY_DEBIT_FADE_START = "10:15"      # Entry window start
INTRADAY_DEBIT_FADE_END = "14:00"        # Entry window end
INTRADAY_DEBIT_FADE_DELTA_MIN = 0.20     # OTM delta min
INTRADAY_DEBIT_FADE_DELTA_MAX = 0.50     # Near ATM max
```

### ITM Momentum

```python
# config.py — Lines 1455-1469

INTRADAY_ITM_MIN_VIX = 9.0               # V6.13 OPT: Min VIX for ITM plays
INTRADAY_ITM_MIN_MOVE = 0.8              # QQQ move >= 0.8%
INTRADAY_ITM_MIN_SCORE = 40              # Micro score >= 40
INTRADAY_ITM_START = "10:00"             # Entry window start
INTRADAY_ITM_END = "13:30"               # Entry window end
INTRADAY_ITM_DELTA = 0.70                # ITM delta target
INTRADAY_ITM_DELTA_MIN = 0.60            # ITM min
INTRADAY_ITM_DELTA_MAX = 0.85            # Deep ITM max
```

### CREDIT Spreads

```python
# config.py — Lines 1446-1452

INTRADAY_CREDIT_MIN_VIX = 18             # VIX >= 18 for rich premium
INTRADAY_CREDIT_MAX_MOVE = 1.5           # QQQ move < 1.5%
INTRADAY_CREDIT_START = "10:00"          # Entry window start
INTRADAY_CREDIT_END = "14:30"            # Entry window end
INTRADAY_CREDIT_SPREAD_WIDTH = 2.00      # $2 spread width
```

---

## 8. V6.7 Fixes Applied

| Fix | Change | Impact |
|-----|--------|--------|
| V6.7-1 | Assignment Risk: Notional → Net Debit | Spreads stay open |
| V6.7-9 | Spike Cap Max: reduced to 38 | Faster DEFENSIVE |
| V6.7-10 | Breadth 5D Decay: reduced to -0.02 | Triggers more often |
| V6.7-10 | Breadth 10D Decay: reduced to -0.04 | Triggers more often |

---

## 9. Known Issues (From Backtest Analysis)

| Issue | Metric | Root Cause |
|-------|--------|------------|
| Dir=NONE too frequent | 83-98% of signals | STABLE zone ±1% too wide |
| FOLLOW_MACRO always BULLISH | 100% | Regime never < 50 |
| Low intraday trade count | 3-8 per 6 months | STABLE zone + high min scores |
| Wrong direction in bear | 2022: 22% win rate | Regime lag + BULLISH fallback |

---

## 10. Quick Parameter Reference

### High-Impact (Tune First)

| Parameter | Location | Current |
|-----------|----------|---------|
| `VIX_DIRECTION_STABLE_LOW` | config.py:1327 | -1.0 |
| `VIX_DIRECTION_STABLE_HIGH` | config.py:1328 | +1.0 |
| `SPREAD_REGIME_BULLISH` | config.py:1091 | 70 |
| `SPREAD_REGIME_BEARISH` | config.py:1092 | 50 |
| `V53_SPIKE_CAP_MAX_SCORE` | config.py:258 | 38 |
| `MICRO_VIX_CRISIS_LEVEL` | config.py:1345 | 35 |

### Medium-Impact

| Parameter | Location | Current |
|-----------|----------|---------|
| `VASS_IV_LOW_THRESHOLD` | config.py:823 | 16 |
| `VASS_IV_HIGH_THRESHOLD` | config.py:847 | 25 |
| `OPTIONS_INTRADAY_ALLOCATION` | config.py:804 | 0.0625 |
| `INTRADAY_MAX_TRADES_PER_DAY` | config.py:874 | 2 |
| `MICRO_UVXY_BEARISH_THRESHOLD` | config.py:1429 | 0.025 |

---

## 11. Optimization Workflow

### Step 1: Narrow STABLE Zone
```python
VIX_DIRECTION_STABLE_LOW = -0.5   # Was -1.0
VIX_DIRECTION_STABLE_HIGH = 0.5   # Was +1.0
```
→ Expect more directional signals.

### Step 2: Lower Rising Threshold
```python
VIX_DIRECTION_RISING = 2.0        # Was 3.0
```
→ Earlier RISING detection in bear markets.

### Step 3: Test on Multiple Periods

| Period | Expected Outcome |
|--------|------------------|
| 2017 H1 | More trades, maintain +20% return |
| 2015 H1 | More trades, maintain positive return |
| 2022 Jan-Feb | Earlier PUT signals, reduce losses |

---

## 12. Key Metrics

| Metric | Current | Target |
|--------|---------|--------|
| Monthly Trade Count | 5-15 | 40-60 |
| Dir=NONE Rate | 83-98% | < 30% |
| Intraday Win Rate | 26-50% | > 50% |
| Spread Win Rate | 0-58% | > 55% |
| Direction Accuracy | ~40% | > 60% |

---

## 13. V6.8 Architect Recommendations

> **Status:** Reviewed and validated. Ready for implementation.
> **Philosophy:** Micro drives growth, while system stays safe across bull/bear/choppy.
> **Evidence Base:** 2015/2017/2022 backtest analysis.

---

### A. Micro Engine — Profit Driver + Controlled Risk

| Parameter | Current | Recommended | Purpose |
|-----------|:-------:|:-----------:|---------|
| `INTRADAY_DEBIT_FADE_MIN_SCORE` | 35 | **35** | Allow more trades in bull/chop ✓ |
| `INTRADAY_ITM_MIN_SCORE` | 40 | **40** | Capture momentum earlier ✓ |
| `INTRADAY_FADE_MAX_MOVE` | 1.50 | **1.50** | Don't block strong bull continuation ✓ |
| `INTRADAY_DEBIT_FADE_VIX_MIN` | 9.5 | **9.5** | Allow trades in low VIX bull ✓ |
| `INTRADAY_ITM_MIN_VIX` | 9.0 | **9.0** | Allow momentum in very low VIX ✓ |
| `MICRO_UVXY_BEARISH_THRESHOLD` | 0.025 | **0.025** | More bear conviction signals ✓ |
| `MICRO_UVXY_BULLISH_THRESHOLD` | -0.03 | **-0.025** | More bull conviction signals |

---

### B. Micro Stops — Reduce Catastrophic Losses

| Parameter | Current | Recommended | Purpose |
|-----------|:-------:|:-----------:|---------|
| `OPTIONS_ATR_STOP_MULTIPLIER` | 0.9 | **0.9** | Stop not too wide ✓ |
| `OPTIONS_ATR_STOP_MAX_PCT` | 0.30 | **0.30** | Prevent 50% losses ✓ |
| `OPTIONS_ATR_STOP_MIN_PCT` | 0.12 | **0.12** | Allow slightly tighter stops ✓ |
| `OPTIONS_0DTE_STOP_PCT` | 0.15 | **0.15** | Keep fallback as is |

---

### C. Liquidity / Spread Filters — Reduce Rejections

| Parameter | Current | Recommended | Purpose |
|-----------|:-------:|:-----------:|---------|
| `OPTIONS_MIN_OPEN_INTEREST` | 50 | **50** | Avoid rejection in thin chains ✓ |
| `OPTIONS_SPREAD_WARNING_PCT` | 0.30 | **0.30** | Reduce spread-based rejection ✓ |

---

### D. VASS / Spreads — Keep VASS Alive

| Parameter | Current | Recommended | Purpose |
|-----------|:-------:|:-----------:|---------|
| `VASS_HIGH_IV_DTE_MIN` | 5 | **5** | Allow trades in high IV ✓ |
| `VASS_HIGH_IV_DTE_MAX` | 40 | **40** | Widen candidate pool ✓ |
| `SPREAD_LONG_LEG_DELTA_MIN` | 0.35 | **0.35** | Allow near-ATM ✓ |
| `SPREAD_SHORT_LEG_DELTA_MAX` | 0.60 | **0.60** | Avoid excessive rejection ✓ |
| `SPREAD_WIDTH_TARGET` | 4.0 | **3.0** | More matches in chain |

---

### E. Assignment Risk — Prevent Instant Close

| Parameter | Current | Recommended | Purpose |
|-----------|:-------:|:-----------:|---------|
| `ASSIGNMENT_MARGIN_BUFFER_PCT` | 0.20 | **0.10** | Reduce instant exit triggers |

---

### Config Changes Applied (V6.8 → V6.13)

```python
# === A. MICRO ENGINE (Applied) ===
INTRADAY_DEBIT_FADE_MIN_SCORE = 35       # ✓ Applied (was: 45)
INTRADAY_ITM_MIN_SCORE = 40              # ✓ Applied (was: 50)
INTRADAY_FADE_MAX_MOVE = 1.50            # ✓ Applied (was: 1.20)
INTRADAY_DEBIT_FADE_VIX_MIN = 9.5        # ✓ Applied (was: 13.5) - V6.13 further reduced
INTRADAY_ITM_MIN_VIX = 9.0               # ✓ Applied (was: 11.5) - V6.13 further reduced
MICRO_UVXY_BEARISH_THRESHOLD = 0.025     # ✓ Applied (was: 0.03)
MICRO_UVXY_BULLISH_THRESHOLD = -0.025    # ✓ Applied (was: -0.03)

# === B. MICRO STOPS (Applied) ===
OPTIONS_ATR_STOP_MULTIPLIER = 0.9        # ✓ Applied (was: 1.5) - V6.13 further tightened
OPTIONS_ATR_STOP_MAX_PCT = 0.30          # ✓ Applied (was: 0.50)
OPTIONS_ATR_STOP_MIN_PCT = 0.12          # ✓ Applied (was: 0.20) - V6.13 further tightened
OPTIONS_0DTE_STOP_PCT = 0.15             # Unchanged

# === C. LIQUIDITY FILTERS (Applied) ===
OPTIONS_MIN_OPEN_INTEREST = 50           # ✓ Applied (was: 100)
OPTIONS_SPREAD_WARNING_PCT = 0.30        # ✓ Applied (was: 0.25)

# === D. VASS / SPREADS (Applied) ===
VASS_HIGH_IV_DTE_MIN = 5                 # ✓ Applied (was: 7)
VASS_HIGH_IV_DTE_MAX = 40                # ✓ Applied (was: 21) - V6.13 expanded
SPREAD_LONG_LEG_DELTA_MIN = 0.35         # ✓ Applied (was: 0.45) - V6.10 further widened
SPREAD_SHORT_LEG_DELTA_MAX = 0.60        # ✓ Applied (was: 0.52) - V6.10 further widened
SPREAD_WIDTH_TARGET = 3.0                # ✓ Applied (was: 4.0)

# === E. ASSIGNMENT RISK (Applied) ===
ASSIGNMENT_MARGIN_BUFFER_PCT = 0.10      # ✓ Applied (was: 0.20)
```

---

### Expected Impact by Period

| Period | Issue | Fix Applied | Expected Improvement |
|--------|-------|-------------|---------------------|
| **2017 H1** (Bull) | VIX floor blocked 90%+ | VIX_MIN 13.5→11.5 | +500-800% intraday trades |
| **2015 H1** (Choppy) | Score gates too high | Scores 45/50→35/40 | +200% intraday trades |
| **2022 Jan-Feb** (Bear) | Spreads closed instantly | Assignment buffer 20%→10% | Spreads stay open |
| **All Periods** | 50% max loss too wide | ATR stop max 50%→30% | Smaller losing trades |

---

### Validation Checklist

Before deploying V6.8:

- [ ] Backtest 2017 H1: Verify intraday count increases from 3 to 20+
- [ ] Backtest 2015 H1: Verify win rate stays above 40%
- [ ] Backtest 2022 Jan-Feb: Verify spreads stay open (not instant-closed)
- [ ] Check 0DTE trades: Verify 30% max stop caps losses appropriately
- [ ] Review VASS routing: Verify DTE 5-28 produces valid candidates

---

**Goal:** Every parameter change should answer: "Does this help us trade more AND trade better?"
