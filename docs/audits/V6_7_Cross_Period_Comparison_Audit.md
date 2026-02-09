# V6.7 OPTIONS ENGINE — CROSS-PERIOD COMPARISON AUDIT

## Overview

Comparison of options engine performance across three market periods:
- **2015 H1** (Jan-Jun): Choppy/sideways with Aug flash crash recovery
- **2017 H1** (Jan-Jun): Strong bull market, low volatility
- **2022 Jan-Feb**: Bear market, high volatility selloff

All backtests run in **Options Isolation Mode** (Trend/MR engines disabled).

---

## 1) Performance Summary

| Metric | 2015 H1 | 2017 H1 | 2022 Jan-Feb |
|--------|---------|---------|--------------|
| **Starting Capital** | $75,000 | $75,000 | $75,000 |
| **Total Trades** | 74 | 46 | 102 |
| **Spread Entries** | 33 | 23 | 17 |
| **Intraday Entries** | 137 approved | 73 approved | 206 approved |
| **Assignment Risk Closes** | 8 | 5 | 17 (ALL spreads!) |
| **OCO Profit Triggers** | 2 | 1 | 13 |
| **OCO Stop Triggers** | 1 | 0 | 21 |
| **VASS Rejections** | 92 | 52 | 274 |
| **MICRO No Direction** | 3,279 | 3,597 | 1,029 |

### P&L Analysis (from trades CSV)

| Period | Spread P&L | Intraday P&L | Total P&L | % Return |
|--------|------------|--------------|-----------|----------|
| **2015 H1** | +$6,440 | -$3,100 | **+$3,340** | **+4.5%** |
| **2017 H1** | +$18,120 | +$1,758 | **+$19,878** | **+26.5%** |
| **2022 Jan-Feb** | -$11,760 | -$23,325 | **-$35,085** | **-46.8%** |

---

## 2) Bug Analysis by Period

### Bug #1: ASSIGNMENT_RISK_EXIT (Instant Spread Closes)

| Period | Occurrences | Cause | Impact |
|--------|-------------|-------|--------|
| 2015 H1 | 8 | Margin buffer insufficient | -$2,520 |
| 2017 H1 | 5 | Margin buffer insufficient | -$420 |
| 2022 Jan-Feb | **17 (100%)** | ALL spreads closed instantly | -$11,760 |

**Pattern:** Lower strike prices in 2015/2017 (~$100-140) vs 2022 (~$350-400) meant lower notional exposure. The 20% buffer requirement was easier to meet with lower strikes.

**Fix Status:** V6.7-1 fixed assignment risk calculation to use net debit instead of notional.

### Bug #2: MICRO No Direction (Dir=NONE)

| Period | Occurrences | % of Signals | Impact |
|--------|-------------|--------------|--------|
| 2015 H1 | 3,279 | ~96% | Forced FOLLOW_MACRO |
| 2017 H1 | 3,597 | ~98% | Forced FOLLOW_MACRO |
| 2022 Jan-Feb | 1,029 | ~83% | Forced FOLLOW_MACRO |

**Pattern:** The VIX direction thresholds are still too tight. Even with V6.6 narrowed STABLE zone (±1%), most moves fall within that range.

**FOLLOW_MACRO Breakdown:**

| Period | FOLLOW_MACRO BULLISH | FOLLOW_MACRO BEARISH | PUT Signals |
|--------|---------------------|---------------------|-------------|
| 2015 H1 | 64 | 0 | 80 (via VETO) |
| 2017 H1 | 3 | 0 | 2 (via VETO) |
| 2022 Jan-Feb | 158 | 0 | Variable |

**Critical Finding:** FOLLOW_MACRO only produces BULLISH direction because regime stayed 60+ (NEUTRAL/RISK_ON) even during selloffs. No BEARISH macro signals generated.

### Bug #3: Spike Cap Not Aggressive Enough

| Period | Spike Cap Triggers | Score After Cap | Regime State |
|--------|-------------------|-----------------|--------------|
| 2015 H1 | 4 | 51-65 | NEUTRAL |
| 2017 H1 | 4 | 52-65 | NEUTRAL |
| 2022 Jan-Feb | Many | 45-56 | NEUTRAL/CAUTIOUS |

**Pattern:** Spike cap at 45 only reached CAUTIOUS (40-49), never DEFENSIVE (30-39).

**Fix Applied:** V6.7-9 lowered spike cap from 45 → 38.

### Bug #4: Breadth Decay Never Triggered

| Period | Breadth Decay Triggers | Reason |
|--------|----------------------|--------|
| 2015 H1 | 0 | Threshold -10%/-15% too aggressive |
| 2017 H1 | 0 | Threshold -10%/-15% too aggressive |
| 2022 Jan-Feb | 0 | Threshold -10%/-15% too aggressive |

**Fix Applied:** V6.7-10 relaxed thresholds from -10%/-15% → -2%/-4%.

---

## 3) Market Context Analysis

### 2015 H1: Choppy/Sideways
- QQQ range: ~$98-$112 (14% range)
- VIX range: 12-28 (brief spikes)
- Best for: Swing spreads held to profit target
- Worst for: Intraday (choppy moves, no clear direction)

### 2017 H1: Strong Bull
- QQQ range: ~$117-$143 (22% gain)
- VIX range: 10-16 (very low, complacent)
- Best for: CALL spreads (all directions aligned)
- Worst for: PUT signals (never triggered)

### 2022 Jan-Feb: Bear Selloff
- QQQ range: ~$400 → $340 (-15%)
- VIX range: 17-32 (sustained high)
- Best for: PUT signals (if regime detected it)
- Worst for: CALL signals (regime lagged, kept calling BULLISH)

---

## 4) Win Rate Analysis

| Period | Total Win Rate | Spread Win Rate | Intraday Win Rate |
|--------|---------------|-----------------|-------------------|
| 2015 H1 | 47% (35/74) | 55% (18/33) | 41% (17/41) |
| 2017 H1 | 52% (24/46) | 58% (14/24) | 45% (10/22) |
| 2022 Jan-Feb | 22% (22/102) | 0% (0/17)* | 26% (22/85) |

*All 2022 spreads instantly closed by assignment risk bug

---

## 5) Critical Bugs Identified

### P0 (Critical - Blocking or Causing Major Losses)

| Bug | Description | 2015 | 2017 | 2022 | Status |
|-----|-------------|------|------|------|--------|
| **Assignment Risk** | Calculated notional instead of net debit | 8× | 5× | 17× | ✅ V6.7-1 |
| **Spike Cap Too High** | 45 → CAUTIOUS, not DEFENSIVE | ✓ | ✓ | ✓ | ✅ V6.7-9 |
| **Breadth Decay Useless** | -10%/-15% thresholds never trigger | ✓ | ✓ | ✓ | ✅ V6.7-10 |

### P1 (High - Major Performance Impact)

| Bug | Description | 2015 | 2017 | 2022 | Status |
|-----|-------------|------|------|------|--------|
| **MICRO Dir=NONE** | VIX STABLE zone still too wide | 96% | 98% | 83% | 🔲 TODO |
| **FOLLOW_MACRO Always BULLISH** | Regime never goes BEARISH | ✓ | ✓ | ✓ | 🔲 TODO |
| **No PUT Macro Signals** | Even in bear markets | 0 | 0 | 0 | 🔲 TODO |

### P2 (Medium - Optimization)

| Bug | Description | 2015 | 2017 | 2022 | Status |
|-----|-------------|------|------|------|--------|
| **VASS Rejections High** | Spread criteria too strict | 92 | 52 | 274 | 🔲 TODO |
| **OCO Stop Rate High** | 62% stops vs 38% profits | N/A | N/A | ✓ | 🔲 TODO |

---

## 6) Root Cause: Regime Never Goes Bearish

The fundamental issue across all periods:

```
Regime Score Composition (V5.3):
- Momentum (30%): 20-day ROC - slow to react
- VIX Combined (30%): Working but only 30% weight
- Trend (25%): Price vs MA200 - very slow to react
- Drawdown (15%): Distance from 52w high - only matters in deep drops

Result: Even when VIX hits 30+, regime stays 50-65 (NEUTRAL)
```

**Why FOLLOW_MACRO is always BULLISH:**
1. Regime score > 50 → Macro direction = BULLISH
2. MICRO has no direction (Dir=NONE) 83-98% of time
3. Fallback: FOLLOW_MACRO → BULLISH
4. Result: CALLs in bear markets

---

## 7) Recommendations

### Already Fixed (V6.7)
1. ✅ Assignment risk: Net debit instead of notional
2. ✅ Spike cap: 45 → 38 (forces DEFENSIVE)
3. ✅ Breadth decay: -10%/-15% → -2%/-4%

### Still TODO
4. 🔲 **MICRO Direction**: Need to widen thresholds further or use different indicator
5. 🔲 **Regime BEARISH threshold**: Add explicit VIX level override
   ```python
   if vix_level >= 28:
       raw_score = min(raw_score, 45)  # Force CAUTIOUS
   if vix_level >= 32:
       raw_score = min(raw_score, 35)  # Force DEFENSIVE
   ```
6. 🔲 **VASS Criteria**: Relax DTE/delta/spread width in high VIX
7. 🔲 **OCO Ratio**: Widen stops (50% → 60%), tighten profits (50% → 40%)

---

## 8) Expected Impact of V6.7 Fixes

| Fix | 2015 H1 Impact | 2017 H1 Impact | 2022 Jan-Feb Impact |
|-----|---------------|----------------|---------------------|
| Assignment Risk | +$2,520 recovered | +$420 recovered | +$11,760 recovered |
| Spike Cap 38 | Faster CAUTIOUS | No change | Earlier DEFENSIVE |
| Breadth Decay -2%/-4% | May trigger | Unlikely | Would trigger |
| **Net Improvement** | ~+$3,000 | ~+$500 | ~+$15,000 |

---

## 9) Conclusion

**2017 H1 (Bull Market):** Best performance (+26.5%) because:
- All CALL signals aligned with market direction
- Low VIX = no need for PUT signals
- Spreads held to profit target

**2015 H1 (Choppy):** Modest gain (+4.5%) because:
- Mixed signals, some worked, some didn't
- Intraday struggles with no clear direction
- Spreads performed reasonably well

**2022 Jan-Feb (Bear):** Major loss (-46.8%) because:
- Assignment risk bug killed all spreads instantly
- Regime never went BEARISH (lagging indicators)
- CALL signals in a bear market = consistent losses

**Key Insight:** The system works in bull markets but fails in bear markets due to regime detection lag. The V6.7 fixes address some issues, but the fundamental problem of slow regime response remains.

---

**Audit Date:** 2026-02-08
**Auditor:** Claude Code (Opus 4.5)
