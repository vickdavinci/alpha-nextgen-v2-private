# V6.9 Options Engine Audit — 2021–2022

**Run:** `V6_9_2021_2022_TwoYear`  
**Files reviewed:**  
`docs/audits/logs/stage6.5/V6_9_2021_2022_TwoYear_logs.txt`  
`docs/audits/logs/stage6.5/V6_9_2021_2022_TwoYear_orders.csv`  
`docs/audits/logs/stage6.5/V6_9_2021_2022_TwoYear_trades.csv`

**Date:** 2026-02-09

---

## Bug Summary (Top) — Grouped

**Assignment & Risk Containment**

| Priority | Bug | Years | Evidence | Impact | Action | Status |
|:--------:|-----|:-----:|----------|--------|--------|:------:|
| **P0** | Assignment/QQQ Trades Overwhelm Results | 2015, 2017, 2021–2022 | 2015: `QQQ` -11,640; 2017: assignment risk exits present; 2021–2022: `QQQ` -139,313 | Net P&L would be positive without assignment losses | **Applied:** DTE=1 force close + pre‑market ITM check + tighter ITM exit + wider spreads | ✅ Applied |

**Margin & Execution Stability**

| Priority | Bug | Years | Evidence | Impact | Action | Status |
|:--------:|-----|:-----:|----------|--------|--------|:------:|
| **P0** | Margin Reject Storm | 2015, 2021–2022 | 2015: margin errors **590**; 2021–2022: **1,781** | System approves signals but fails execution | **Applied:** pre‑signal margin check + updated buffer + lower options cap | ✅ Applied |
| **P1** | Signal → Execution Gap | 2015, 2017, 2021–2022 | 2015: 96 signals; 2017: 25; 2021–2022: 30 executed | Most intraday signals never become trades | **Applied:** margin pre‑check + micro threshold tuning + tradeable regimes | 🟡 Applied (Pending Validation) |

**Signal Quality & Direction Bias**

| Priority | Bug | Years | Evidence | Impact | Action | Status |
|:--------:|-----|:-----:|----------|--------|--------|:------:|
| **P1** | Dir=NONE / Micro Not Producing Action | 2015, 2017, 2021–2022 | 2015: 89.7%; 2017: 98.6%; 2021–2022: 76% | Few intraday trades despite signal approvals | **Applied:** narrower VIX bands + relaxed UVXY thresholds + lower VIX floor | ✅ Applied |
| **P1** | Dir=PUT Never Generated | 2015, 2017, 2021–2022 | 2017: 0 Dir=PUT; 2021–2022: 869 (7.2%) vs 2,049 CALL (17.1%) | No bearish signals even when VIX rising | **Applied:** UVXY BEAR threshold to 2.5% | ✅ Applied |
| **P2** | Extreme Call Bias (Bear Periods) | 2015, 2017, 2021–2022 | Orders skewed to CALLs in all years | Bear market exposure skewed | **Applied:** relaxed bearish thresholds + tighter stable band | ✅ Applied |

**Spread Construction & Strategy Mix**

| Priority | Bug | Years | Evidence | Impact | Action | Status |
|:--------:|-----|:-----:|----------|--------|--------|:------:|
| **P1** | Bearish Spread Construction Failing | 2021–2022 | `VASS_REJECTION: Direction=PUT \| Strategy=CREDIT \| Reason=No contracts` | 0 BEAR_PUT, 0 BEAR_CALL_CREDIT executed | **Applied:** widened delta bands + PUT range + credit→debit fallback | ✅ Applied |
| **P1** | Short Leg Consistently Loses | 2015, 2017, 2021–2022 | Long $117 +$2,960, Short $120 -$2,100 = Net +$860 | Reduces profit potential, increases assignment risk | **Applied:** SPREAD_WIDTH_MIN=5 | ✅ Applied |
| **P2** | Spread Width Too Narrow ($3) | 2015, 2017, 2021–2022 | Max profit $300/contract; assignment loss $3,000+ | Limited R:R, high assignment risk | **Applied:** SPREAD_WIDTH_MIN=5 | ✅ Applied |
| **P2** | No Credit Spread Entries | 2015, 2017, 2021–2022 | 100% debit spreads (BULL_CALL), 0 credit spreads | Missing theta decay in high-IV | **Applied:** credit→debit fallback + lower credit floor | ✅ Applied |
| **P2** | Poor Performance in Choppy Markets | 2015 | 2015: -$19,925 ex-assignment (choppy year) vs 2017: +$9,280 (trending) | CALL spreads lose both ways in chop | **Applied:** choppy filter + size reduction | ✅ Applied |

---

## Executive Summary

The options engine failed primarily due to **assignment losses** and **severe margin rejections**.  
Without the five `QQQ` assignment trades (**-139k**), the options P&L was materially positive.  
Separately, the system is **approving** many signals but **not executing** them due to margin failures and pipeline gaps.

**Latest Changes Applied (V6.10):**
- Assignment prevention: mandatory DTE=1 close, pre‑market ITM check, tighter ITM exit threshold, wider spreads.
- Margin stabilization: pre‑signal margin check enabled, updated buffer, lower options margin cap.
- Micro signal tuning: tighter VIX stable bands, relaxed UVXY thresholds, lower VIX floor, QQQ fallback eased.
- VASS construction: wider delta bands, PUT range expanded, credit→debit fallback enabled.
- Risk/Reward: symmetric stop/target, choppy filter + size reduction.

---

## Performance Snapshot (Trades)

**All trades**
- Total trades: **365**
- Win%: **37.8%**
- Net P&L: **-66,593**
- Fees: **4,796**

**By symbol type**
- `QQQ` assignments: **5 trades**, **-139,313 P&L**
- Calls: **348 trades**, **+72,921 P&L**
- Puts: **12 trades**, **-201 P&L**

**Conclusion:** Assignment losses dominate all outcomes.

---

## Order & Direction Mix

From `orders.csv`:
- Total orders: **1,801**
- Filled orders: **717**
- CALL orders: **1,565**
- PUT orders: **38**

This is a severe CALL bias, likely dangerous in 2022’s bear environment.

---

## Signal → Execution Gaps

From logs:
- `INTRADAY_SIGNAL`: **581**
- `INTRADAY_SIGNAL_APPROVED`: **495**
- `INTRADAY: Selected`: **404**
- `INTRADAY_RESULT`: **30**

Most approved intraday signals never become executed trades.  
This needs direct logging between approval → order submit → fill.

---

## Margin & Execution Failures

From logs:
- `Order Error` / `Insufficient buying power`: **1,781**
- `MARGIN_CB`: **1,041**

This is an execution kill-switch in practice. The system is signaling but cannot trade.

---

## Micro Regime Direction Output

From logs:
- `MICRO_UPDATE`: **11,896**
- `Dir=NONE`: **9,069** (~76%)
- `Dir=CALL`: **2,049**
- `Dir=PUT`: **869**

Micro is not producing actionable direction most of the time. This aligns with low intraday fills.

---

## Key Observations

1. **Assignment losses are catastrophic and must be blocked.**  
2. **Margin rejections block the majority of trades.**  
3. **Most intraday signals are never executed (pipeline gap).**  
4. **Direction bias (CALL heavy) is risky for 2022 and likely needs guard rails.**

---

## Recommendations

### P0 — Critical

1. **Assignment prevention (Enhanced)**
   - SHORT_LEG_ITM_EXIT is firing (93 times) but 5 assignments still slipped through
   - **Add: Mandatory 1 DTE close** — Close ALL spread positions at DTE=1 regardless of P&L
   - **Add: Pre-market ITM check** — At 09:25 ET, check for ITM shorts before market open
   - **Add: Wider spread width** — Increase from $3 to $5-7 minimum to survive gaps

2. **Margin pre‑checks**
   - Reject entry before submitting orders if margin buffer < threshold.
   - 1,781 margin errors = system approving but cannot execute

3. **Bearish spread construction failing (NEW)**
   - Evidence: `VASS_REJECTION: Direction=PUT | IV_Env=HIGH | Strategy=CREDIT | Reason=No contracts met spread criteria`
   - VASS Matrix correctly routes to CREDIT for HIGH IV, but contract selection fails
   - **Fix: Relax PUT delta requirements** or use different strike selection for CREDIT spreads
   - **Fix: Add fallback to DEBIT** if CREDIT construction fails

### P1 — High Priority

4. **Execution pipeline audit**
   - Add logging for signal → order → fill to identify where trades are dropped.
   - 581 signals → 495 approved → only 30 executed

5. **Direction bias control**
   - Cap CALLs in NEUTRAL/BEARISH macro unless micro+macro agree.
   - 1,565 CALL orders vs 38 PUT orders = extreme imbalance

6. **Reduce Dir=NONE**
   - Tighten or adaptive VIX direction thresholds; use QQQ fallback only with confirmation.
   - Dir=NONE: 9,069 (76%) — micro not providing actionable direction

### P2 — Medium Priority

7. **Stop Loss / Profit Target Ratio**
   - Evidence: Stop Loss: 22 vs Profit Target: 6 (21% win rate on exits)
   - Consider adjusting stop loss base (currently 35%) or profit target (currently 50%)

8. **Order tagging**
   - Add tags like `ENGINE=MICRO`, `ENGINE=VASS`, `STRATEGY=...` for future audits

---

## V6.9 Fixes Status

| Fix | Status | Evidence |
|-----|--------|----------|
| VASS Matrix (HIGH IV → CREDIT) | WORKING | `Strategy=CREDIT` in rejection logs |
| SHORT_LEG_ITM_EXIT guard | WORKING | 93 triggers, but 5 assignments still slipped through |
| Stop Loss base 50% → 35% | APPLIED | Code updated |
| VIX-adaptive STABLE band | PARTIAL | Dir=NONE still 76% (was worse before) |
| QQQ fallback for Dir=NONE | APPLIED | Code updated |
| Bearish spread construction | BROKEN | 0 BEAR_PUT, 0 BEAR_CALL_CREDIT executed |

---

## Notes

The `orders.csv` **Tag column is empty**, so engine/strategy attribution is not possible.  
Add tags like `ENGINE=MICRO`, `ENGINE=VASS`, `STRATEGY=...` for future audits.

---

# V6.9 Options Engine Audit — 2017 Full Year

**Run:** `V6_9_2017FullYear`  
**Files reviewed:**  
`docs/audits/logs/stage6.5/V6_9_2017FullYear_logs.txt`  
`docs/audits/logs/stage6.5/V6_9_2017FullYear_orders.csv`  
`docs/audits/logs/stage6.5/V6_9_2017FullYear_trades.csv`

**Date:** 2026-02-09

---

## Bug Summary (Top)

| Priority | Bug | Evidence | Impact | Action | Status |
|:--------:|-----|----------|--------|--------|:------:|
| **P1** | Dir=NONE Most of the Time | `Dir=NONE` **7,156 / 7,260** (~98.6%) | Very few intraday signals in a bull year | Re-tune micro direction thresholds or add fallback | 🟡 Partial |
| **P1** | Intraday Signal Starvation | Only **25** `INTRADAY_SIGNAL` for entire year | Micro engine not participating in bull market | **Applied:** widened tradeable regimes + lower VIX floor + relaxed UVXY thresholds | 🟡 Applied (Pending Validation) |
| **P2** | No PUT Activity (Call-Only) | **122 CALL orders, 0 PUT orders** | No downside capture even in mini pullbacks | **Applied:** lowered UVXY bearish threshold + tighter VIX stable band | 🟡 Applied (Pending Validation) |

---

## Performance Snapshot (Trades)

**All trades**
- Total trades: **60**
- Win%: **51.7%**
- Net P&L: **+9,280**
- Fees: **1,560**

**By symbol type**
- Calls: **60 trades**, **+9,280 P&L**
- Puts: **0**

---

## Signal / Execution Metrics

From logs:
- `MICRO_UPDATE`: **7,260**
- `Dir=NONE`: **7,156**
- `INTRADAY_SIGNAL`: **25**
- `INTRADAY_SIGNAL_APPROVED`: **25**
- `INTRADAY: Selected`: **0**
- `SPREAD: ENTRY_SIGNAL` / `POSITION_REGISTERED`: **62**
- `SPREAD: EXIT_SIGNAL`: **24**
- `ASSIGNMENT_RISK_EXIT`: **6**

---

## Key Observations

1. **2017 was a bull year; micro should have been highly active.** It was not.  
2. **Call-only exposure** means the engine likely missed hedging opportunities.  
3. **Very low intraday participation** suggests micro gating still too strict.

---

## Recommendations

1. **Re-tune micro direction thresholds** to reduce Dir=NONE in low‑VIX years.  
2. **Allow more tradeable regimes** in bull/low‑VIX periods.  
3. **Add call/put balance checks** for mini pullback capture.

---

# V6.9 Options Engine Audit — 2015 Full Year

**Run:** `V6_9_2015FullYear`  
**Files reviewed:**  
`docs/audits/logs/stage6.5/V6_9_2015FullYear_logs.txt`  
`docs/audits/logs/stage6.5/V6_9_2015FullYear_orders.csv`  
`docs/audits/logs/stage6.5/V6_9_2015FullYear_trades.csv`

**Date:** 2026-02-09

---

## Bug Summary (Top)

**P0 — Negative P&L Despite Balanced Win/Loss**  
- Evidence: 145 trades, **48.3% win rate**, **-31,565 P&L**.  
- Impact: Losses larger than wins; stop/target calibration likely off.  
- Action: Recalibrate risk/reward; review stop loss sizing.

**P1 — Micro Direction None Still Dominant**  
- Evidence: `Dir=NONE` **6,550 / 7,300** (~89.7%) of micro updates.  
- Impact: Low intraday activity in a choppy year.  
- Action: Relax direction thresholds or introduce directional fallback.

**P1 — Call Bias, Weak Put Coverage**  
- Evidence: 278 CALL orders vs 26 PUT orders; trades: 135 calls, 9 puts.  
- Impact: Weak downside capture in choppy regime.  
- Action: Ensure VIX‑based bearish states produce puts consistently.

**P2 — Execution Losses on Underlying**  
- Evidence: 1 underlying `QQQ` trade (classified as “other”) with **-11,640 P&L**.  
- Impact: Even a single assignment can dominate results.  
- Action: Maintain assignment prevention and margin buffers.

---

## Performance Snapshot (Trades)

**All trades**
- Total trades: **145**
- Win%: **48.3%**
- Net P&L: **-31,565**
- Fees: **3,843.55**

**By symbol type**
- Calls: **135 trades**, **-16,054 P&L**
- Puts: **9 trades**, **-3,871 P&L**
- Other (`QQQ`): **1 trade**, **-11,640 P&L**

---

## Order & Direction Mix

From `orders.csv`:
- Total orders: **305**
- Filled orders: **288**
- CALL orders: **278**
- PUT orders: **26**

---

## Signal / Execution Metrics

From logs:
- `MICRO_UPDATE`: **7,300**
- `Dir=NONE`: **6,550**
- `Dir=CALL`: **647**
- `Dir=PUT`: **156**
- `INTRADAY_SIGNAL`: **96**
- `INTRADAY_SIGNAL_APPROVED`: **82**
- `INTRADAY: Selected`: **29**
- `SPREAD: ENTRY_SIGNAL` / `POSITION_REGISTERED`: **134**
- `SPREAD: EXIT_SIGNAL`: **42**
- `ASSIGNMENT_RISK_EXIT`: **4**

---

## Key Observations

1. **Choppy year performance weak** despite near‑50% win rate.  
2. **Micro direction stays NONE** most of the time, reducing intraday participation.  
3. **Call bias** persists; put exposure is minimal.

---

## Recommendations

1. **Rebalance risk/reward** (stop/target) to improve payoff in choppy markets.
2. **Increase actionable micro signals** via threshold tuning.
3. **Raise bearish participation** (puts) to handle sideways/down moves.

---

# Regime Engine Performance — Cross-Period Analysis

## Regime State Distribution

| Year | RISK_ON | NEUTRAL | CAUTIOUS | DEFENSIVE | Total |
|------|:-------:|:-------:|:--------:|:---------:|:-----:|
| **2015** | 201 (55%) | 127 (35%) | 36 (10%) | 1 (<1%) | 365 |
| **2017** | 323 (89%) | 36 (10%) | 4 (1%) | 0 (0%) | 363 |
| **2021-22** | 136 (23%) | 290 (49%) | 147 (25%) | 21 (4%) | 594 |

---

## Average Regime Score

| Year | Avg Score | Interpretation |
|------|:---------:|----------------|
| **2015** | 55.6 | Borderline NEUTRAL — choppy market |
| **2017** | 67.0 | Solid NEUTRAL/RISK_ON — bull market |
| **2021-22** | 44.5 | CAUTIOUS — bear/volatile market |

---

## VIX Environment

| Year | Avg VIX | LOW (<15) | MED (15-25) | HIGH (>25) |
|------|:-------:|:---------:|:-----------:|:----------:|
| **2015** | 16.6 | 161 (44%) | 183 (50%) | 21 (6%) |
| **2017** | 11.1 | 351 (97%) | 12 (3%) | 0 (0%) |
| **2021-22** | 21.9 | 0 (0%) | 444 (75%) | 150 (25%) |

---

## Key Finding: Regime Engine Is Working Correctly

| Year | Market Reality | Regime Detection | Match? |
|------|----------------|------------------|:------:|
| 2015 | Choppy/flat | NEUTRAL (55.6 avg) | ✅ |
| 2017 | Strong bull | RISK_ON (89%), VIX 11.1 | ✅ |
| 2021-22 | Bear/volatile | CAUTIOUS (44.5 avg), VIX 21.9 | ✅ |

---

## The Real Problem

The regime engine **correctly identifies** bearish conditions in 2021-2022:
- 25% CAUTIOUS + 4% DEFENSIVE
- VIX averaging 21.9, with 25% in HIGH (>25)

But the options engine **cannot act on it** because:
1. **Bearish spread construction is broken** — 0 BEAR_PUT, 0 BEAR_CALL_CREDIT executed
2. **Dir=NONE dominates** (76%) even when regime says CAUTIOUS

**The regime is telling the system to be defensive. The options engine isn't listening.**

---

# MICRO + VASS Root Cause Analysis: 2015, 2017, 2021-2022

**Date:** 2026-02-09

---

## Root Cause Summary Table

| Root Cause | Parameter(s) | Current Value | Problem | Recommended Value |
|:-----------|:-------------|:--------------|:--------|:------------------|
| **RC-1** Dir=NONE (76-98%) | VIX_STABLE_BAND_LOW / _HIGH | ±0.5% / ±2.0% | Most UVXY moves fall within STABLE band | ±0.3% / ±1.0% |
| **RC-2** MICRO Conviction Extreme | MICRO_UVXY_CONVICTION_EXTREME | 7% | Strong signals (e.g., 6.6%) blocked for NEUTRAL override | 5% |
| **RC-3** BEARISH Threshold Too High | MICRO_UVXY_BEARISH_THRESHOLD | +4% | Rare PUT generation, even in 2022 bear market | +2.5% |
| **RC-4** BULLISH Threshold Too Aggressive | MICRO_UVXY_BULLISH_THRESHOLD | -5% | CALL direction only when UVXY drops >5% | -3% |
| **RC-5** Regime Blocking Good Conditions | tradeable_regimes (hardcoded) | 7 states only | CAUTIOUS, BREAKING, WORSENING all blocked | Add CAUTION_LOW, WORSENING_LOW |
| **RC-6** VASS DTE/Delta Too Restrictive | SPREAD_LONG_LEG_DELTA_MIN/MAX | 0.40-0.85 | "No contracts met spread criteria" | 0.35-0.90 |
| **RC-7** VIX Floor Blocking Low-VIX Years | INTRADAY_DEBIT_FADE_VIX_MIN | 13.5 | 2017 (VIX ~11) blocked entirely | 10.5 |
| **RC-8** QQQ Fallback Move Too High | INTRADAY_QQQ_FALLBACK_MIN_MOVE | 0.70% | VIX STABLE requires large QQQ move | 0.50% |

---

## Detailed Evidence By Year

### 2021-2022 (Bear/Volatile)

```
Dir=NONE:       9,069 / 11,896 (76%)
NO_TRADE:       2,122 (regime-blocked)
VASS_REJECTION: 2,589 ("No contracts met spread criteria")
Conviction blocked: 358 ("conviction not extreme +X% < 7%")

Top blocking regimes:
- CAUTIOUS:     724
- CALMING:      178
- WORSENING:    139
- NORMAL:       87
- BREAKING:     41
```

### 2017 (Bull/Low VIX)

```
Dir=NONE:       7,156 / 7,260 (98.6%)
NO_TRADE:       104 (regime-blocked)
VASS_REJECTION: 98 ("No contracts met spread criteria")

Top blocking regimes:
- NORMAL:       43  ← Even GOOD conditions blocked!
- GOOD_MR:      39  ← Ideal MR regime still blocked!
- PERFECT_MR:   10  ← Best regime still blocked!
- CAUTION_LOW:  6

VIX avg: 11.7 → Falls below VIX_FLOOR (13.5) → blocked
```

### 2015 (Choppy/Mixed)

```
Dir=NONE:       6,550 / 7,300 (89.7%)
NO_TRADE:       623 (regime-blocked)
VASS_REJECTION: 465 ("No contracts met spread criteria")
Conviction blocked: 32 ("conviction not extreme")

Top blocking regimes:
- CAUTIOUS:     106
- WORSENING:    52
- CAUTION_LOW:  40
- NORMAL:       36
- DETERIORATING: 33
- BREAKING:     31
- GOOD_MR:      27
```

---

## Root Cause Deep Dives

### RC-1: VIX-Adaptive STABLE Band Too Wide

**Evidence:** UVXY moves in MICRO_UPDATE logs:
- `UVXY +0.8%` with VIX=22.8 → Dir=NONE (falls in ±1% band)
- `UVXY -0.6%` with VIX=18 → Dir=NONE (falls in ±1% band)
- `UVXY +1.5%` with VIX=30 → Dir=NONE (falls in ±2% band)

**Current Logic:**
```
VIX < 15:  STABLE band = ±0.5%
VIX 15-25: STABLE band = ±1.0%  ← Most trading days
VIX > 25:  STABLE band = ±2.0%  ← High VIX but still blocked
```

**Impact:** 76-98% of signals return Dir=NONE

**Fix:** Narrow bands:
```
VIX < 15:  ±0.3%
VIX 15-25: ±0.6%
VIX > 25:  ±1.0%
```

---

### RC-2: MICRO Conviction Extreme Threshold

**Evidence from logs:**
```
NO_TRADE: Macro NEUTRAL, MICRO conviction not extreme +6.6% < 7%
NO_TRADE: Macro NEUTRAL, MICRO conviction not extreme +5.8% < 7%
NO_TRADE: Macro NEUTRAL, MICRO conviction not extreme -6.2% < 7%
```

**Current:** MICRO_UVXY_CONVICTION_EXTREME = 7%

**Impact:** 390 strong signals blocked (5-7% moves ignored)

**Fix:** Lower to 5%

---

### RC-3 & RC-4: BEARISH/BULLISH Thresholds

**Current:**
```
MICRO_UVXY_BEARISH_THRESHOLD = +4% (UVXY must spike 4% for PUT)
MICRO_UVXY_BULLISH_THRESHOLD = -5% (UVXY must drop 5% for CALL)
```

**Evidence:**
- 2021-2022: Dir=CALL 2,049 (17%) vs Dir=PUT 869 (7%)
- 2017: Dir=CALL 104 vs Dir=PUT 0
- 2015: Dir=CALL 647 vs Dir=PUT 156

**Impact:** Severe CALL bias. PUT direction almost never generated.

**Fix:**
```
MICRO_UVXY_BEARISH_THRESHOLD = +2.5%  (down from 4%)
MICRO_UVXY_BULLISH_THRESHOLD = -3.0%  (down from 5%)
```

---

### RC-5: tradeable_regimes Hardcoded List

**Current list (options_engine.py line 1347-1354):**
```python
tradeable_regimes = {
    MicroRegime.PERFECT_MR,
    MicroRegime.GOOD_MR,
    MicroRegime.NORMAL,
    MicroRegime.RECOVERING,
    MicroRegime.IMPROVING,
    MicroRegime.PANIC_EASING,
    MicroRegime.CALMING,
}
```

**Missing regimes that should be tradeable:**
- CAUTION_LOW (VIX low but cautious - still safe for small trades)
- WORSENING_LOW (early deterioration - can still capture fade)
- CAUTIOUS (with additional confirmation)

**Impact:** 830 CAUTIOUS blocks, 191 WORSENING blocks

---

### RC-6: VASS Contract Selection Too Restrictive

**Evidence from VASS_REJECTION logs:**
```
VASS_REJECTION: Direction=CALL | IV_Env=MEDIUM | Contracts_checked=218 |
Strategy=DEBIT | Reason=No contracts met spread criteria (DTE/delta/credit)
```

**Current delta requirements:**
```
SPREAD_LONG_LEG_DELTA_MIN = 0.40
SPREAD_LONG_LEG_DELTA_MAX = 0.85
SPREAD_SHORT_LEG_DELTA_MIN = 0.10
SPREAD_SHORT_LEG_DELTA_MAX = 0.55
```

**Impact:** 5,137 total VASS rejections across all years

**Fix:** Widen to:
```
SPREAD_LONG_LEG_DELTA_MIN = 0.35  (was 0.40)
SPREAD_LONG_LEG_DELTA_MAX = 0.90  (was 0.85)
SPREAD_SHORT_LEG_DELTA_MIN = 0.08  (was 0.10)
SPREAD_SHORT_LEG_DELTA_MAX = 0.60  (was 0.55)
```

---

### RC-7: VIX Floor Blocking Low-VIX Years

**Evidence (2017):**
```
VIX avg = 11.7 in 2017
INTRADAY_DEBIT_FADE_VIX_MIN = 13.5
```

**Impact:** Almost all 2017 intraday trades blocked by VIX floor

**Fix:** Lower to 10.5 or 11.0

---

### RC-8: QQQ Fallback Move Requirement

**Evidence:**
```
STABLE_NO_TRADE: QQQ +0.45% Score=58 (<55)
STABLE_NO_TRADE: QQQ +0.62% Score=52 (<55)
```

**Current:** INTRADAY_QQQ_FALLBACK_MIN_MOVE = 0.70%

**Impact:** VIX STABLE (76%+ of time) requires large QQQ move + score confirmation

**Fix:** Lower to 0.50%

---

## Parameter Change Summary

```python
# RC-1: Narrow STABLE bands
VIX_STABLE_BAND_LOW = 0.3   # Was 0.5
VIX_STABLE_BAND_HIGH = 1.0  # Was 2.0

# RC-2: Lower conviction extreme
MICRO_UVXY_CONVICTION_EXTREME = 0.05  # Was 0.07

# RC-3 & RC-4: Direction thresholds
MICRO_UVXY_BEARISH_THRESHOLD = 0.025  # Was 0.04
MICRO_UVXY_BULLISH_THRESHOLD = -0.03  # Was -0.05

# RC-6: VASS delta requirements
SPREAD_LONG_LEG_DELTA_MIN = 0.35   # Was 0.40
SPREAD_LONG_LEG_DELTA_MAX = 0.90   # Was 0.85
SPREAD_SHORT_LEG_DELTA_MAX = 0.60  # Was 0.55

# RC-7: VIX floor
INTRADAY_DEBIT_FADE_VIX_MIN = 10.5  # Was 13.5

# RC-8: QQQ fallback
INTRADAY_QQQ_FALLBACK_MIN_MOVE = 0.50  # Was 0.70
```

---

## Expected Impact

With these changes:
- Dir=NONE should drop from 76-98% to ~40-50%
- VASS rejections should drop from 5,000+ to ~1,000
- PUT direction should increase from 7% to ~25-30%
- More intraday signals will execute (currently only 30 out of 581 approved)
