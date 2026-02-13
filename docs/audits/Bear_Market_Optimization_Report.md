# Bear Market Optimization Report
## Cross-Version Analysis: Dec 2021 - Feb 2022

**Generated:** 2026-02-12
**Market Context:** QQQ fell ~20% (Dec 2021 highs to Feb 2022 lows). Omicron fears, Fed hawkish pivot, Russia-Ukraine tensions.
**Versions Analyzed:** V6.19, V6.21, V7 — same 3-month bear market period.

---

## 1. Cross-Version Performance Summary

| Metric | V6.19 | V6.21 | V7 | Trend |
|--------|-------|-------|-----|-------|
| **Starting Capital** | $75,000 | $100,000 | $100,000 | — |
| **Net P&L** | -$17,987 | -$46,249 | -$27,140 | V7 improved over V6.21 |
| **Net P&L (normalized to $100K)** | -$23,983 | -$46,249 | -$27,140 | V7 best on $100K |
| **Return** | -24.0% | -46.2% | -27.1% | V7 best |
| **Total Trades** | 127 | 179 | 201 | Increasing (more active) |
| **Win Rate** | 42.5% | 33.0% | 36.8% | V6.19 best |
| **Profit Factor** | 0.54 | 0.66 | ~0.72 | V7 best |
| **Max Drawdown** | -24.0% | -48.6% | ~-27.1% | V6.19 best (lower capital) |
| **Largest Single Loss** | -$21,160 | -$21,160 | -$12,160 | V7 capped losses better |
| **Fees** | ~$1,800 | $2,324 | $2,447 | Similar |

### Trajectory

V6.19 → V6.21 was a **regression** (losses nearly doubled).
V6.21 → V7 was an **improvement** (losses cut 41%), with a key structural change: V7 introduced BEAR_CALL_CREDIT spreads.

---

## 2. VASS Direction Analysis Across Versions

This is the #1 issue. VASS direction in a bear market determines everything.

### VASS Spread Type Distribution

| Spread Type | V6.19 | V6.21 | V7 |
|-------------|-------|-------|-----|
| **BULL_CALL_DEBIT** | 32 (100%) | 53 (100%) | 47 (70.1%) |
| **BEAR_CALL_CREDIT** | 0 (0%) | 0 (0%) | 20 (29.9%) |
| **BEAR_PUT_DEBIT** | 0 | 0 | 0 |
| **BULL_PUT_CREDIT** | 0 | 0 | 0 |

**Key Finding:** V6.19 and V6.21 traded **exclusively BULLISH** in a bear market. V7 introduced 20 BEAR_CALL_CREDIT spreads (29.9%) — the first version to trade bearish VASS.

### VASS P&L by Direction

| Direction | V6.19 | V6.21 | V7 |
|-----------|-------|-------|-----|
| **BULLISH (BULL_CALL)** P&L | -$9,220 | -$28,712 | ~-$20,000 (est.) |
| **BEARISH (BEAR_CALL)** P&L | $0 (none) | $0 (none) | Small positive (est.) |

### Why VASS Was Bullish in a Bear Market

**V6.19:** Regime scores 50-70 (NEUTRAL/RISK_ON). All trades followed macro direction = BULLISH. No conviction overrides logged.

**V6.21:** VASS conviction **actively overrode** NEUTRAL macro:
- `"VIX 5d change -30% < -20% | Macro=NEUTRAL | Resolved=BULLISH"` — VIX falling fast was interpreted as bullish
- `"UVXY -6% < -4% | Macro=NEUTRAL | VETO: MICRO conviction (BULLISH) overrides NEUTRAL Macro"` — UVXY conviction also bullish
- MICRO correctly generated 478 PUT vs 282 CALL, but VASS conviction controlled swing direction

**V7:** Regime averaged 59.9 (NEUTRAL). Still 70% BULL_CALL, but added 20 BEAR_CALL_CREDIT. Conviction overrides reduced but regime lag still caused BULLISH bias.

### Root Cause: Regime Lag

All three versions show regime **never dropping below CAUTIOUS** despite QQQ falling 15-20%:

| Regime State | V6.19 | V6.21 | V7 |
|--------------|-------|-------|-----|
| RISK_ON (70+) | 13.3% | — | 14.2% |
| NEUTRAL (50-69) | 70.0% | — | 66.9% |
| CAUTIOUS (45-49) | 16.7% | — | 16.7% |
| DEFENSIVE (35-44) | 0% | — | 2.2% |
| RISK_OFF (0-34) | 0% | — | 0% |

**The regime engine was NEUTRAL 67-70% of the time during a bear market.** This is the structural issue — the 4-factor regime model (Momentum, VIX Combined, Trend, Drawdown) has a 2-3 week lag, and NEUTRAL regime permits BULLISH VASS entries.

---

## 3. MICRO Direction Analysis Across Versions

MICRO's direction selection was **correct** across all versions.

### MICRO Direction Distribution

| Direction | V6.19 | V6.21 | V7 |
|-----------|-------|-------|-----|
| **PUT trades** | 58 (91%) | 478 signals (26.6%) | 74 (94.9%) |
| **CALL trades** | 6 (9%) | 282 signals (15.7%) | 4 (5.1%) |
| **Blocked** | — | 1,040 (57.8%) | 319 dropped |

**MICRO correctly identified the bear market.** In V6.21, the micro regime generated 1.7x more PUT than CALL signals. In V7, 95% of executed MICRO trades were PUT direction.

### MICRO CALL Gate Performance

| Gate | V6.19 | V6.21 | V7 |
|------|-------|-------|-----|
| E_CALL_GATE_STRESS | 129 | 129 | 125 |
| E_CALL_GATE_MA | 51 | 76 | 64 |
| **Total CALL blocks** | 180 | 205 | 189 |

The CALL gate consistently blocked 180-205 CALL signals across all versions. **This is the system's best-performing safety mechanism in bear markets.** Without it, MICRO would have entered 180+ additional CALL trades.

### MICRO Strategy Performance

| Strategy | V6.19 P&L | V6.21 (est.) | V7 (est.) |
|----------|-----------|--------------|-----------|
| ITM_MOMENTUM | -$5,723 (42.1% WR) | — | ~-$4,000 |
| DEBIT_MOMENTUM | -$1,985 (33.3% WR) | — | ~-$3,000 |
| PROTECTIVE_PUTS | -$1,059 (37.5% WR) | — | ~$0 |
| DEBIT_FADE | — | — | +$500 (est.) |

**ITM_MOMENTUM was the worst MICRO strategy** in bear markets across all versions.

---

## 4. Tail Loss Analysis Across Versions

Tail losses are the dominant P&L destroyer in every version.

### Tail Loss Summary

| Metric | V6.19 | V6.21 | V7 |
|--------|-------|-------|-----|
| Tail losses (>2x avg) | 6 trades | 11 trades | 17 trades |
| Tail loss total | -$48,073 | -$80,127 | -$66,286 |
| % of total losses | 138.7%* | 59.3% | 58.6% |
| All tail losses CALL? | **YES** | **YES** | **YES** |

*V6.19's tail losses exceed total losses because winning trades offset some losses.

**Every single tail loss across all three versions was a CALL option.** This is the clearest pattern in the data.

### Top Tail Losses (Cross-Version)

| Date | Version | Strategy | P&L | Type |
|------|---------|----------|-----|------|
| 2021-12-09 | V6.19/V6.21 | VASS BULL_CALL | -$21,160 | Spread long leg |
| 2021-12-13 | V6.21 | VASS BULL_CALL | -$12,160 | Spread long leg |
| 2021-12-13 | V7 | VASS BULL_CALL | -$12,160 | Spread long leg |
| 2021-12-13 | V6.21 | VASS BULL_CALL | -$9,020 | Spread long leg |
| 2022-01-05 | V6.19 | VASS BULL_CALL | -$11,900 | Spread |
| 2022-01-03 | V6.21 | VASS BULL_CALL | -$7,392 | Spread |

**Recurring dates:** Dec 9, Dec 13, Jan 3-5 — market drop days. VASS had BULL_CALL spreads open and held them through the decline.

---

## 5. What Works in Bear Markets (Evidence-Based)

### 5.1 MICRO PUT Direction
- V6.19: 58 PUT trades, 37.9% WR (dominant direction)
- V7: 74 PUT trades, 41.3% WR, +$3,421 net
- **MICRO correctly reads bear conditions via the VIX Level × VIX Direction matrix**

### 5.2 CALL Gate (E_CALL_GATE_STRESS + E_CALL_GATE_MA)
- Blocked 180-205 CALL signals per version
- Prevented estimated $50K+ in additional CALL losses
- **The single most valuable safety mechanism in bear markets**

### 5.3 BEAR_CALL_CREDIT Spreads (V7 only)
- V7 was the first version to execute 20 BEAR_CALL_CREDIT spreads
- These were correctly directional for a bear market
- **Expanding credit spread usage in bear markets is the right direction**

### 5.4 Signal Blocking Rate
- V6.21: 57.8% of MICRO signals blocked
- V6.19: 54.4% of intraday signals blocked
- **Blocking >50% of signals in bear markets is correct — the system is too conservative with what it lets through, not with what it blocks**

### 5.5 V6.19's EV Paradox
- V6.19 had **positive expected value per trade (+$205)** despite losing -$17,987
- 6 tail losses (-$48,073) wiped out the edge
- **The base strategy has edge — tail losses destroy it**

---

## 6. What Fails in Bear Markets (Evidence-Based)

### 6.1 VASS BULL_CALL in Bear Markets
| Version | BULL_CALL Spreads | P&L |
|---------|-------------------|-----|
| V6.19 | 32 | -$9,220 |
| V6.21 | 53 | -$28,712 |
| V7 | 47 | ~-$20,000 |

**BULL_CALL spreads lost money in every version.** The system entered 32-53 bullish spreads during a 20% market decline. This is the primary bear market failure mode.

### 6.2 VASS Conviction Override (V6.21 specific)
V6.21's conviction system (VIX 5d change) interpreted falling VIX as bullish:
- `"VIX 5d change -30% < -20% → BULLISH"` — VIX dropping after spike = "fear subsiding" = bullish
- But in a sustained selloff, VIX oscillates — each drop is a bull trap, not a trend reversal
- V6.21 generated 53 BULL_CALL spreads (most of any version) and lost -$28,712 (most of any version)
- **VASS conviction was the worst offender in the worst-performing version**

### 6.3 Regime Lag (All Versions)
- Regime stayed NEUTRAL (50-69) for 67-70% of the bear market
- Never reached RISK_OFF (0-34) despite QQQ -20%
- NEUTRAL regime permits BULLISH entries → regime lag = continued BULL_CALL entries
- **2-3 week regime lag is structural and affects every version equally**

### 6.4 No Stop-Loss on VASS Spreads (V6.19)
- V6.19 analysis explicitly states: "Spreads had no mechanism to exit losing positions early"
- Spreads held to expiration, allowing losses to compound
- The -$21,160 single trade (21% of starting capital) should have been stopped out at -$3,000

### 6.5 ITM_MOMENTUM in Bear Markets
- V6.19: 38 trades, 42.1% WR, -$5,723
- Consistently the worst MICRO strategy in bear markets
- High VIX environments create premium inflation → ITM options expensive → stops hit quickly

---

## 7. Version-Over-Version Improvement Tracking

### What Each Version Fixed

| Fix | V6.19 | V6.21 | V7 |
|-----|-------|-------|-----|
| BEAR_CALL_CREDIT spreads | No | No | **Yes (20 trades)** |
| CALL gate for MICRO | Yes | Yes | Yes |
| VASS spread stop-loss | No | Partial | Better (max loss reduced) |
| Regime-based direction gate | No | No | Partial |
| Conviction override | Active (no fires) | **Active (destroyed P&L)** | Reduced |
| Trade limit as rejection | 450 rejections | 270 TRADE_LIMIT_BLOCK | 479 TRADE_LIMIT_BLOCK |
| Assignment gate | No | 103 blocks | More stringent |

### What Each Version Made Worse

| Regression | V6.19→V6.21 | V6.21→V7 |
|------------|-------------|----------|
| VASS conviction overrides | None → Active (bullish in bear market) | Active → Reduced |
| VASS trade count | 32 spreads → 53 spreads | 53 → 67 (still too many) |
| MICRO trade count | 64 → 73 | 73 → 78 (marginal increase) |
| Total P&L | -$17,987 → -$46,249 | -$46,249 → -$27,140 (improved) |

---

## 8. Bear Market Optimization Recommendations

### Priority 0: Stop the Bleeding (VASS Direction)

**Problem:** VASS entered 32-53 BULL_CALL spreads per version in a bear market.
**Evidence:** Every version, every tail loss was a BULL_CALL spread.

**Fix 1: Macro Trend Gate for BULL_CALL**
- Block BULL_CALL entries when QQQ < 50-day MA **AND** regime < 60
- Rationale: If QQQ is below its 50-day MA, the trend is down — don't fight it with bullish spreads
- Expected impact: Would have blocked 30-40 of 47-53 BULL_CALL entries
- Estimated savings: $15,000-$25,000

**Fix 2: VASS Conviction Cannot Override to BULLISH When Macro is NEUTRAL + VIX > 18**
- The V6.21 disaster was caused by `"VIX 5d change -30% → BULLISH"` override
- If VIX is above 18 and macro is NEUTRAL, VASS conviction should not be allowed to force BULLISH
- Only allow VASS conviction BEARISH overrides when VIX is elevated
- Estimated savings (V6.21): Would have prevented ~20 BULL_CALL entries = $10,000-$15,000

**Fix 3: VASS Spread Stop-Loss at -35% of Spread Width**
- V6.19 had no stop mechanism → spreads held to expiration
- V6.21/V7 had some stops but still saw -$12K and -$21K single trades
- Hard stop at -35% of spread width (e.g., $4 wide spread → max loss $1.40/contract)
- Estimated savings: Would cap worst trades from $12K-$21K to ~$3K-$4K

### Priority 1: Increase Bearish Capacity

**Problem:** V6.19/V6.21 executed **zero** bearish VASS spreads. V7 executed 20 but still 70% bullish.
**Evidence:** BEAR_CALL_CREDIT in V7 was directionally correct. MICRO PUT was profitable (+$3,421 in V7).

**Fix 4: Relax BEAR_PUT Assignment Gate in Bear Markets**
- V6.21: 103 BEAR_PUT entries blocked by assignment gate (short PUT >= 3% OTM)
- In bear markets (regime < 55 or VIX > 20), relax to 1.5% OTM
- This would allow BEAR_PUT_DEBIT spreads — zero were executed in any version

**Fix 5: Separate BULLISH/BEARISH Slot Caps**
- Current: Single direction max penalizes BEARISH entries when BULLISH slots are full
- Proposed: Independent caps — allow 2 BEARISH + 2 BULLISH concurrently
- Prevents BEARISH starvation when BULLISH slots dominate

**Fix 6: Lower Credit Spread VIX Threshold**
- V6.19: 450 VASS rejections, dominant reason = `CREDIT_ENTRY_VALIDATION_FAILED`
- VIX was 18-31 (should qualify for credit spreads at VIX > 25)
- Review DTE/delta/credit criteria for HIGH VIX environment — may be too strict

### Priority 2: MICRO Bear Market Tuning

**Problem:** MICRO PUT was directionally correct but still lost money in V6.19 (-$7,842).
**Evidence:** V7 PUT was profitable (+$3,421). The difference: V7 had tighter stops and better regime gating.

**Fix 7: Block ITM_MOMENTUM When VIX > 25**
- ITM_MOMENTUM was the worst MICRO strategy in bear markets across all versions
- V6.19: 38 trades, 42.1% WR, -$5,723
- High VIX → inflated premiums → ITM options too expensive → stops hit
- Gate: Disable ITM_MOMENTUM when VIX > 25, allow only DEBIT_FADE + PROTECTIVE_PUTS

**Fix 8: Tighter MICRO CALL Stops in Bear Markets**
- Current ATR stop max: 28%
- In bear markets (regime < 55), reduce to 18-20%
- The few CALL trades that get through should exit fast if wrong

**Fix 9: Increase DEBIT_FADE Frequency**
- DEBIT_FADE was profitable in every version tested (bull and bear markets)
- Currently gated by QQQ_FLAT and regime filters → only 3-4 entries per quarter
- Relax QQQ_FLAT threshold specifically for DEBIT_FADE strategy
- DEBIT_FADE thrives on mean reversion — works in ranges within bear markets

### Priority 3: Regime Engine Bear Market Adaptation

**Problem:** Regime stayed NEUTRAL (50-69) for 67-70% of a bear market, never reaching RISK_OFF.
**Evidence:** Consistent across V6.19, V6.21, V7 — regime never below CAUTIOUS (45).

**Fix 10: Add Fast Regime Decay When QQQ < 200-day MA**
- If QQQ is below its 200-day MA, apply a regime penalty of -10 to -15 points
- This would push NEUTRAL (60) to CAUTIOUS (45-50), triggering bearish VASS gates
- Rationale: Long-term trend break is the strongest bear signal — regime should reflect it

**Fix 11: Intraday Regime Refresh (Already in V8 uncommitted code)**
- Refresh regime at 12:00 and 14:00 instead of only EOD
- BUT: must fix the state mutation bug (regime_engine appends to history on each call)
- Needs a read-only calculation path that doesn't corrupt `_vix_history`

**Fix 12: Shock Memory Duration**
- After VIX spike > 30, force BEARISH direction for minimum 5-10 trading days
- V8 has partial implementation (`SHOCK_MEMORY_FORCE_BEARISH_VASS`)
- Extend: if VIX crossed above 30 in last 10 days, block all BULL_CALL entries

---

## 9. Projected Impact of Bear Market Fixes

### Conservative Estimate (Fixes 1-3 Only)

Applied to V6.21 (worst-performing version, -$46,249):

| Fix | Trades Affected | Estimated Savings |
|-----|-----------------|-------------------|
| Fix 1: Macro trend gate | Block ~35 BULL_CALL entries | +$18,000 |
| Fix 2: Conviction override restriction | Block ~15 additional | +$8,000 |
| Fix 3: Spread stop-loss at -35% | Cap 6 tail losses | +$12,000 |
| **Total Estimated Savings** | | **+$38,000** |
| **Projected V6.21 P&L** | | **-$8,249** |

That reduces V6.21's bear market loss from -46.2% to ~-8.2%.

### Aggressive Estimate (All Fixes)

Applied to V7 (-$27,140):

| Fix | Estimated Savings |
|-----|-------------------|
| Fix 1-3: VASS direction + stops | +$15,000 |
| Fix 4-6: Bearish capacity | +$3,000 (more bearish entries) |
| Fix 7-9: MICRO tuning | +$5,000 |
| Fix 10-12: Regime adaptation | +$4,000 (indirect via faster direction) |
| **Total Estimated Savings** | **+$27,000** |
| **Projected V7 P&L** | **~breakeven to +$2,000** |

### Reality Check

These are back-fit estimates — the actual savings would be less due to:
1. Blocked BULL_CALL entries may include some that would have been winners
2. Relaxed BEARISH gates may allow some losers through
3. Stop-loss caps prevent recovery on spreads that would have bounced
4. Fixes interact in ways that aren't captured by simple addition

**Realistic expectation:** Fixes 1-3 alone could reduce bear market losses by 50-70%. Full implementation might achieve breakeven. Profitability in bear markets likely requires either credit spread expansion or hedging overlay.

---

## 10. The Bull vs Bear Asymmetry

| Metric | Bull (Jul-Sep 2017) V8 | Bear (Dec-Feb 2022) V7 |
|--------|------------------------|------------------------|
| VASS Win Rate | 84.0% | ~35% |
| MICRO CALL P&L | +$6,615 | -$28,114 (all CALL) |
| MICRO PUT P&L | -$7,354 | +$3,421 |
| DEBIT_FADE | +$2,020 (75% WR) | — |
| Net P&L | -$6,163 | -$27,140 |

**The system has opposite failure modes:**
- **Bull market:** VASS wins (84% WR) but MICRO PUT destroys it
- **Bear market:** MICRO PUT wins but VASS BULL_CALL destroys it

**This is actually good news.** It means the system has genuine edge in both directions — it just applies the wrong engine to the wrong market. The fix is not to change the engines but to correctly route which engine controls direction in which regime.

### Proposed Routing Logic

| Market Condition | VASS Direction Source | MICRO Direction Source |
|-----------------|----------------------|----------------------|
| **Bull (Regime > 65, QQQ > MA200)** | Macro regime (BULLISH) | MICRO regime matrix |
| **Neutral (Regime 50-65)** | Macro regime ONLY (no conviction override) | MICRO regime matrix |
| **Bear (Regime < 50 OR QQQ < MA200)** | Force BEARISH or NO_TRADE | MICRO regime matrix (trust PUT) |
| **Crisis (VIX > 30)** | BEARISH only (credit spreads) | PROTECTIVE_PUTS only |

The key insight: **MICRO's direction is correct in bear markets. VASS's direction is wrong. Let MICRO's signal quality inform VASS's direction decisions in bear markets.**

---

## 11. Summary

### The One-Line Finding
Every bear market loss trace back to VASS entering BULL_CALL spreads while MICRO correctly said PUT.

### The Three Essential Fixes
1. **Block BULL_CALL when QQQ < 50-day MA** (prevents the primary loss source)
2. **Stop-loss VASS spreads at -35%** (caps tail losses that destroy EV)
3. **Block VASS conviction BULLISH override when VIX > 18** (prevents V6.21's worst failure)

### What Does NOT Need Fixing
- MICRO regime matrix (direction is correct)
- CALL gate (blocks 180-200 bad CALL signals every time)
- Signal blocking rate (50-58% blocked is correct for bear markets)
- DEBIT_FADE strategy (profitable in all environments)

### Version Recommendation
V7 is the best bear market performer (-$27,140 vs V6.21's -$46,249) because it:
- Added BEAR_CALL_CREDIT spreads (first version to trade bearish VASS)
- Reduced conviction overrides
- Improved stop management

Build bear market fixes on V7's foundation, not V6.21's.

---

## 12. Neutral Validation Addendum (Action Scope for Next Run)

This addendum captures a neutral review of this report and narrows implementation scope to minimize overfitting and code churn.

### 12.1 What Is Strong and Supported

1. Bear-market losses are consistently dominated by **VASS BULL_CALL** tail events.
2. MICRO direction quality in bear windows is generally better than VASS direction quality.
3. The highest ROI bear fixes are:
   - block bullish VASS entries in downtrend conditions,
   - enforce hard spread loss caps,
   - prevent bullish conviction override in elevated volatility.

### 12.2 What Should Be Treated as Hypothesis

1. Projected savings and breakeven projections are back-fit estimates, not causal proof.
2. Cross-version comparisons include mixed capital bases (`$75K` vs `$100K`) and should be normalized when used for sign-off.
3. “All tail losses are CALL” is strongly indicated in the sampled windows, but should be re-validated after latest plumbing fixes.

### 12.3 Phase-1 Bear Patch Set (Minimal, Recommended)

Implement only these 3 changes before the next Dec-Feb 2022 validation run:

1. **VASS BULL_CALL trend gate**
   - Block `BULL_CALL_*` when `QQQ < MA50` **and** `regime_score < 60`.
2. **Hard spread stop**
   - Enforce stop at `-35% of spread width` with existing close-retry path.
3. **Conviction clamp in elevated VIX**
   - If `VIX > 18` and macro is `NEUTRAL`, disallow bullish VASS conviction override.

### 12.4 Run Discipline

1. Apply only Phase-1 changes for the next bear run (no additional tuning knobs).
2. Run one clean A/B comparison (baseline vs Phase-1).
3. Add further tuning only if A/B confirms lower tail loss and improved downside control.

---

## 13. Stop-Loss Miss RCA (Debit Spreads)

### 13.1 Observed Failure Pattern

In multiple loss cases, debit spreads remained open longer than intended despite configured stop logic. The issue is not only threshold choice; it is also execution-path behavior.

### 13.2 Why Stops Can Be Missed in Practice

1. **Quote dependency gap**
   - Spread exit logic requires both `long_leg_price` and `short_leg_price`.
   - If either leg quote is unavailable, the monitor loop skips exit evaluation for that cycle.
2. **Close-order cancellation/retry delay**
   - A stop signal may be generated, but close legs can be canceled/rejected.
   - Retry logic exists, but repeated cancel/retry cycles still allow adverse drift before flattening.
3. **Adaptive stop looseness in favorable regimes**
   - Base stop is multiplied by regime factors; in bull-like states this can widen tolerance.
   - Combined with quote gaps, realized loss can exceed intended threshold before successful close.

### 13.3 Minimal Corrective Actions (Before Next Bear Backtest)

1. **Debit max-hold time stop**
   - Add/enable max hold for debit spreads only: `7 calendar days` (credit spreads unchanged).
2. **Quote-fallback for stop evaluation**
   - If one leg lacks bid/ask, fallback to best available mark (last/previous valid), rather than skipping exit cycle.
3. **Hard-stop execution priority**
   - Keep hard stop as first loss exit and route through forced-close retry path immediately.
4. **Explicit telemetry**
   - Log `SPREAD_EXIT_SKIPPED_NO_QUOTE`, `SPREAD_HARD_STOP_TRIGGERED`, `SPREAD_CLOSE_RETRY`, `SPREAD_TIME_STOP_7D`.

This keeps code change minimal while directly targeting the stop-miss failure mode.

---

**Report Generated:** 2026-02-12
**Data Sources:**
- V6.19: `docs/audits/logs/stage6.19/V6_19_Dec2021_Feb2022_Analysis_20260211.md`
- V6.21: `docs/audits/logs/stage6.21/V6_21_Dec2021_Feb2022_Analysis_20260211.md`
- V7: `docs/audits/logs/stage7/V7_Dec2021_Feb2022_Analysis.md`
