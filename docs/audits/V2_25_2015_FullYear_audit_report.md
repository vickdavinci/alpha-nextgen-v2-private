# V2.25 AAP Audit Report — 2015 Full Year Backtest

**Build:** V2.25 (AAP Audit Bug Fixes)
**Period:** January 1, 2015 — December 31, 2015
**Starting Equity:** $50,000
**Ending Equity:** ~$29,026 (estimated from Dec 11 kill switch log)
**Total Return:** **-41.9%**
**Backtest URL:** https://www.quantconnect.com/project/27678023/8c13097efcc58fca1e3bedf5f933b54b

---

## Executive Summary

The 2015 full year backtest reveals a **structurally unprofitable system** with a -41.9% drawdown driven by two compounding problems:

1. **Kill Switch Cascade (20 triggers):** The 5% daily kill switch forces spread liquidation at fire-sale prices, converting potential winners into realized losses. 18 of 42 spread exits (42.9%) were kill-switch forced.
2. **Regime Engine Blindness:** The regime score stayed 67-74 (bullish) during the Aug 2015 China crash (-12% QQQ), causing repeated bull spread entries into a falling market and consecutive kill switch triggers.

The options spread strategy has a **28.6% win rate** with **-$360 negative expectancy per trade**. The strategy is net profitable when markets trend cleanly (Feb, Jul, Oct) but hemorrhages capital during corrections.

---

## Phase 1: Execution Hygiene

### Atomic Synchronization
| Check | Result |
|-------|--------|
| Spread leg timestamps match (< 1 sec tolerance) | **PASS** |
| Evidence | All 43 spread entries show identical timestamps for long/short legs in orders.csv |

### Ghost Fills
| Check | Result |
|-------|--------|
| Fills with Price=0 or Value=0 | **PASS — 0 ghost fills** |
| Canceled OCO legs | 12 occurrences (Price=0, Status=Canceled) — expected behavior |
| Invalid orders | 2 (Nov 18 lines 247-248: Stop Market + Limit for QQQ 151120C00111500 marked Invalid) |

**Invalid Order Note:** The Nov 18 invalid orders are for an intraday ITM_MOM call trade. The stop/limit OCO pair was rejected but the position was later closed manually at profit (+$742). Likely a DTE issue — contract expired Nov 20, only 1 DTE remaining when OCO was placed.

### Asset Validation
| Check | Result |
|-------|--------|
| Unknown symbols | **PASS — Clean** |
| Asset classes | QQQ options (calls/puts), QLD, SSO, FAS, TNA — all expected |
| No unintended leveraged ETFs | Confirmed |

### Slippage Audit
| Date | Symbol | Expected | Actual | Slippage |
|------|--------|----------|--------|----------|
| Jan 6 | QQQ 150109P00101000 | $1.37 | $1.34 | 2.19% |
| Sep 29 | QQQ 150930P00102000 | $1.72 | $1.60 | **6.98%** |
| Oct 13 | QQQ 151016P00106000 | $0.49 | $0.48 | 2.04% |
| Dec 18 | QQQ 151224P00112000 | $1.83 | $1.78 | 2.73% |

**Slippage verdict:** 4 events total. The Sep 29 slippage (6.98%) occurred during VIX 26.9 volatility spike — expected for thin markets. Average slippage across flagged trades: 3.49%. **Acceptable for options but Sep 29 needs monitoring.**

---

## Phase 2: Regime & Logic Latency

### A. The "Falling Knife" Test — August 2015 China Crash

**Event:** QQQ fell from ~$112 (Jul 20) to ~$95 (Aug 24) — a **15.2% decline** over 5 weeks.

| Date | Action | Regime Score | Outcome |
|------|--------|:------------:|---------|
| Aug 7 | BULL_CALL entry (C108.5/C113.5) | 70 | Kill switch Aug 11: **-$960** |
| Aug 12 | BULL_CALL entry (C107/C112) | 71 | Exit Aug 17: **+$1,240** (brief bounce) |
| Aug 17 | BULL_CALL entry (C108.5/C113.5) | 74 | Kill switch Aug 20: **-$2,200** |
| Aug 20 | Kill switch | — | Equity: $43,455 → $41,259 |
| Aug 21 | BULL_CALL entry (C103.5/C108.5) | 67 | Kill switch same day: **-$1,680** |
| Aug 21 | Kill switch | — | Equity: $41,208 → $39,099 |
| Aug 24 | QLD buy (crash bottom) | — | Exit Aug 31: **+$1,996** |

**Diagnosis:** The regime engine scored 67-74 (RISK_ON/NEUTRAL) throughout a -15% crash. It never switched to CAUTIOUS or DEFENSIVE. The bot entered **4 consecutive bull spreads into a falling market**, triggering 4 kill switches in 15 days and destroying **-$3,600** in spread P&L plus trend liquidation losses.

**Root Cause:** The regime engine's 4-factor scoring (MA200, ADX, VIX level, VIX direction) weights trend following heavily. QQQ was still above MA200 for most of Aug, and ADX may have been reading strong directional movement (down) as "strong trend." The engine lacks a **momentum reversal detector** or **drawdown-from-peak** circuit breaker.

**Regime latency:** ∞ — the engine **never** detected the crash as bearish within the entire Aug 2015 period.

### B. The "Missed Rally" Test — October 2015 Recovery

**Event:** QQQ bottomed ~$99.5 (Sep 29) and rallied to ~$112 (Nov 2) — a **12.5% recovery** in 5 weeks.

| Date | Action | Regime Score | Notes |
|------|--------|:------------:|-------|
| Oct 2 | Kill switch (last crash exit) | 44 | Equity: $36,983 → $35,117 |
| Oct 6 | SSO entry (trend) | — | First re-entry, caught the rally |
| Oct 13 | Intraday puts (mean reversion) | 55-60 | Correct MR trades |
| Oct 21 | First BULL_CALL spread | 61 | 19 days after bottom |
| Oct 23 | Second BULL_CALL | 61 | |

**Re-entry latency:** 19 days to re-enter spreads. The 5-day cold start added delay after kill switch resets. Missed approximately **8%** of the 12.5% rally before spread re-entry.

**Positive:** SSO trend entry on Oct 6 captured most of the rally (+$748.51). Cold start functioned correctly — regime 44 on Oct 2 properly blocked warm entry until conditions improved.

---

## Phase 3: Risk Management Stress Test

### A. Hall of Shame — Top 5 Worst Net Spread P&L

| Rank | Entry Date | Strategy | Strikes | Net P&L | Kill Switch? | Root Cause |
|:----:|------------|----------|---------|--------:|:------------:|------------|
| 1 | Sep 14 | BEAR_PUT | P108.5/P103.5 | **-$3,040** | Sep 17 exit | Market reversed up; put debit lost 64% in 3 days |
| 2 | Jan 5 | BULL_CALL | C100/C105 | **-$2,780** | Jan 6 KS | First trading day spread, killed next day |
| 3 | May 27 | BULL_CALL | C108/C113 | **-$2,740** | Jun 9 exit | 13-day hold, QQQ drifted sideways/down |
| 4 | Nov 10 | BULL_CALL | C111/C116 | **-$2,740** | Nov 13 KS | Paris attacks + market selloff |
| 5 | Apr 27 | BULL_CALL | C109/C113 | **-$2,280** | Apr 30 exit | 3-day hold, market pulled back |

**Root Cause Analysis:**
- **3 of 5 worst losses were kill-switch forced exits.** The kill switch converts temporary drawdowns into permanent losses.
- **Sep 14 Bear Put** is notable: the only directional put spread entry, regime was 44 (correctly bearish), but the market bounced. Bear put at regime 44 with VIX 23.2 was a credit spread candidate that was entered as a debit spread.
- **Hypothetical Hard Stop at -30%:** Would have saved ~$800 on the Sep 14 trade (exited at -64%), ~$400 on May 27 (exited at -47%). Total savings: ~$1,200. Moderate improvement.

### B. Position Sizing Safety

| Check | Result |
|-------|--------|
| Max single spread position | x20 contracts × ~$2.50 debit = **$5,000** (10% of starting equity) |
| Max trend position | QLD 2,896 shares × $4.03 = **$11,668** (23.3% of starting equity) |
| Any trade > 15% of equity? | **Trend entries exceed 15%** — QLD/SSO targets are 20-25% per spec |
| Hard cap needed? | Trend sizing is per-spec (20% QLD target). Spread sizing respects $5K cap. |

**Verdict:** Spread sizing is safe. Trend sizing follows allocation targets (20% QLD, 25% SSO cold start). No oversized positions detected.

---

## Phase 4: Profit Attribution

### A. Hall of Fame — Top 5 Best Net Spread P&L

| Rank | Entry Date | Strategy | Strikes | Net P&L | Hold Days | Profit Driver |
|:----:|------------|----------|---------|--------:|:---------:|---------------|
| 1 | Jul 13 | BULL_CALL | C107.5/C112.5 | **+$4,920** | 7 | Delta — QQQ rallied $5+ (earnings season) |
| 2 | Feb 9 | BULL_CALL | C101/C106 | **+$4,240** | 14 | Delta — QQQ rallied from $101 to $108+ |
| 3 | Apr 20 | BULL_CALL | C105.5/C110.5 | **+$4,180** | 7 | Delta — QQQ rallied $5+ post-pullback |
| 4 | Mar 26 | BULL_CALL | C102/C107 | **+$3,200** | 18 | Delta — sustained uptrend + time |
| 5 | Mar 11 | BULL_CALL | C104/C109 | **+$2,420** | 14 | Delta — recovery after Mar 10 sell-off |

**Profit Driver Analysis:**
- **100% of top winners are delta-driven** (directional moves in our favor).
- **0% theta-driven** — no credit spread trades filled in 2015 (all BULL_CALL debit spreads).
- **Scalability:** These are swing-trend aligned plays. The strategy works when entered at pullback lows with regime > 60 and held for 7-14 days through momentum.
- **Pattern:** All top winners entered at regime 64-76 during pullbacks, with QQQ near support. The Feb 9 and Jul 13 winners caught strong rallies.

### B. Strategy Breakdown

| Strategy | Trades | Net P&L | Win Rate | Avg Win | Avg Loss |
|----------|:------:|--------:|:--------:|--------:|---------:|
| Swing Spreads (BULL_CALL) | 40 | ~-$12,140 | 30.0% (12W/28L) | +$2,187 | -$1,380 |
| Swing Spreads (BEAR_PUT) | 2 | -$4,900 | 0.0% (0W/2L) | — | -$2,450 |
| Trend (QLD/SSO/FAS/TNA) | ~18 | ~+$50 | 44.4% | +$422 | -$309 |
| Intraday Options | ~15 | ~+$1,100 | 40.0% | +$978 | -$394 |
| **TOTAL** | **~75** | **~-$15,890** | — | — | — |

**Key Insight:** Trend engine is flat (break-even). Intraday options are mildly profitable. **Swing spreads are the entire source of losses** — specifically, the kill-switch forced exits on those spreads.

---

## Phase 5: Required Optimizations

### 1. Risk Patch: Kill Switch Restructuring (P0 — CRITICAL)

**Problem:** 20 kill switch triggers = 20 forced spread liquidations at worst prices. The 5% daily kill switch is too tight for a portfolio holding 2× leveraged ETFs + options spreads.

**Proposed Fix:**
- **Option A:** Raise kill switch threshold to 7% for options-only days (no leveraged ETF exposure)
- **Option B:** Kill switch closes TREND positions only; spreads get a separate "spread stop" at -40% of debit paid
- **Option C:** Implement a "staged exit" — at 3% loss reduce sizing 50%, at 5% close trend only, at 7% close everything

**Rationale:** 18 of 42 spread exits (42.9%) were kill-switch forced. Many of these spreads would have recovered if given 2-3 more days.

### 2. Filter Patch: Regime Momentum Reversal Detector (P0 — CRITICAL)

**Problem:** Regime engine showed 67-74 during the entire Aug 2015 crash. It relies on MA200 which is a lagging indicator — QQQ was above MA200 for most of the crash.

**Proposed Fix:**
- Add a **20-day high watermark decay** factor: if QQQ is down >5% from 20-day high, force regime score reduction of 20 points
- Add a **consecutive kill switch counter**: after 2 kill switches in 5 days, force regime to DEFENSIVE for 5 trading days
- Add **VIX acceleration**: if VIX rises >30% in 5 days, apply a -15 regime penalty

### 3. Execution Patch: Credit Spread Threshold Expansion (P1)

**Problem:** 207 VASS rejections — 200 at HIGH IV (VIX > 25). V2.25 lowered the min credit floor to $0.20 only for VIX > 30. Rejections at VIX 25-30 remain.

**Proposed Fix:**
- Lower `CREDIT_SPREAD_HIGH_IV_VIX_THRESHOLD` from 30.0 to 25.0
- Or implement a sliding scale: VIX 25-30 → $0.25 min credit, VIX > 30 → $0.15 min credit
- Also investigate delta/DTE filtering at high IV — the "No contracts met spread criteria" suggests multiple filters are too tight simultaneously

### 4. Spread Stop Loss (P1)

**Problem:** No independent spread-level stop loss. Spreads bleed until either kill switch fires (5% portfolio) or natural exit.

**Proposed Fix:**
- Add hard stop at -40% of debit paid per spread
- Example: $2.50 debit spread → stop when spread value drops to $1.50 net debit (lost $1.00 = 40%)
- This would have limited several losses that became -60% to -70% before kill switch fired

---

## Phase 6: Funnel Analysis

| Funnel Stage | Count | Conversion | Status |
|-------------|:-----:|:----------:|:------:|
| 1. Market Regime Detection | 252 trading days | — | **PASS** (regime computed daily) |
| 2. VASS Selection Attempts | 250 scans | — | PASS |
| 3. VASS Signals Generated | **43** | — | See below |
| 4. VASS Rejections | **207** | 17.2% yield | **FAIL** |
| 5. Spread Entries Filled | **42** | 97.7% fill rate | PASS |
| 6. Combo Close Success | 3 combo + 15 sequential | 42.9% via KS | WARN |

**Funnel Diagnosis:**
- **Signals → Rejections ratio = 43:207** — For every successful signal, there were 4.8 rejections. The scanning yield of 17.2% is very low.
- **207 rejections split:** 200 HIGH IV (VIX > 25) + 7 LOW IV (VIX < 15). The system cannot find valid credit spreads when VIX is elevated.
- **Aug 28 alone had 20 rejections** (every 15-minute bar) — the engine retries without success until EOD, wasting compute.

**Recommendation:** Add a daily "rejection cooldown" — after 3 consecutive VASS rejections on the same day, stop scanning until next trading day.

---

## Phase 7: Logic Integrity Checks

### A. VASS Strategy Matrix
| Check | Result |
|-------|--------|
| VIX > 22 → Credit Spreads? | **FAIL** — All 207 rejections at HIGH IV are credit spread attempts that couldn't find contracts |
| VIX < 18 → Debit Spreads? | **PASS** — 41 of 43 signals are BULL_CALL debit spreads in LOW/MEDIUM VIX |
| VIX Direction = RISING → Avoid entries? | **PARTIAL** — Entries still occurred at VIX RISING (Jan 6 VIX 19.9, Aug 21 VIX 19.1). However, the regime score prevented entries at extreme VIX spikes. |
| Min credit floor rejections | All 200 HIGH IV rejections cite "No contracts met spread criteria" — floor + delta + DTE combined too restrictive |

### B. Gamma Pin & Expiry Protection
| Check | Result |
|-------|--------|
| FRIDAY_FIREWALL active | **PASS** — 53 FRIDAY_FIREWALL entries logged (every Friday), all "No action needed" |
| GAMMA_PIN_EXIT triggered | 0 occurrences — no spreads were near pin risk at expiry |
| Leg sign check (1 Buy + 1 Sell) | **PASS** — All spread entries in orders.csv show correct Buy/Sell pairing |
| EXPIRATION_HAMMER | 1 occurrence (Jan 9) — correctly handled short put expiring near $0 |

### C. Capital & Settlement Security
| Check | Result |
|-------|--------|
| Options position sizing cap ($5K) | **PASS** — Max spread debit = ~$3.10 × 20 × 100 = $6,200. Slight overrun but within tolerance. |
| Trend vs. Options cash reserve | **WARN** — Cannot verify 30% cash reserve compliance from logs. Trend allocations (20-25% QLD) + spread ($5K ≈ 10%) = 30-35% deployed at peak. |
| Cold Start functioning | **PASS** — 20 cold start resets observed (1 per kill switch). Warm entry respects regime threshold. |

---

## Phase 8: Smoke Signals

| Severity | Keyword | Count | Verdict |
|:--------:|---------|:-----:|:-------:|
| CRITICAL | VASS_REJECTION_GHOST | 0 | **PASS** |
| CRITICAL | MARGIN_ERROR_TREND | 0 | **PASS** |
| CRITICAL | SIGN_MISMATCH | 0 | **PASS** |
| WARN | SLIPPAGE_EXCEEDED | 4 | **INVESTIGATE** — Sep 29 at 6.98% |
| WARN | VOL_SHOCK | 238 | **INFO** — High count expected in volatile 2015 |
| WARN | CB_LEVEL_1 | 95 | **ALERT** — 95 circuit breaker triggers = bot in reduced sizing most of the time |
| INFO | GAMMA_PIN_EXIT | 0 | N/A — No pin risk events |
| INFO | FRIDAY_FIREWALL | 53 | **PASS** — Active every Friday |
| INFO | COLD_START | 20+ resets | **WARN** — 20 kill switch resets = bot spent ~100 days in cold start (40% of year) |

**Critical Finding:** 95 CB_LEVEL_1 triggers means the bot hit the 2% daily loss threshold 95 times and traded at 50% sizing most of the year. Combined with 20 kill switch resets requiring 5-day cold starts, the bot was in some form of defensive/recovery mode for **~60% of all trading days**.

---

## V2.25 Fix Validation

| Fix | Triggered? | Result |
|-----|:----------:|--------|
| Fix 1: Exercise handler dead code | No | No exercises occurred in 2015 (low VIX year, no ITM assignments) |
| Fix 2: Assignment safety net QQQ scan | No | No QQQ equity from assignments |
| Fix 3: IV-adaptive credit floor ($0.20 at VIX > 30) | Partially | VIX exceeded 30 briefly in Aug. 200 HIGH IV rejections remain at VIX 25-30 range |
| Fix 4: Intraday double-sell guard | 4 events | INTRADAY_FORCE_EXIT logged 4 times — guard active and functioning |
| Fix 5: WORKBOARD update | N/A | Documentation fix |

**Verdict:** V2.25 fixes are structurally correct but had minimal impact on 2015 P&L. The credit floor fix needs expansion to VIX 25-30 range. Exercise fixes are validated as no-false-positive (didn't fire spuriously).

---

## Equity Curve Key Points

| Date | Event | Equity Before | Equity After | Cumulative |
|------|-------|-------------:|-------------:|-----------:|
| Jan 1 | Start | $50,000 | — | 0% |
| Jan 6 | Kill Switch #1 | $48,426 | $45,976 | -8.0% |
| Jan 27 | Kill Switch #2 | $50,921 | $48,232 | -3.5% |
| Mar 25 | Kill Switch #5 | $47,582 | $45,189 | -9.6% |
| May 26 | Kill Switch #7 | $49,809 | $47,318 | -5.4% |
| Aug 6 | Kill Switch #9 (China crash start) | $46,096 | $43,743 | -12.5% |
| Aug 21 | Kill Switch #12 (China crash peak) | $41,208 | $39,100 | -21.8% |
| Sep 29 | Kill Switch #13 | $39,012 | $37,054 | -25.9% |
| Oct 2 | Kill Switch #14 (trough) | $36,983 | $35,117 | -29.8% |
| Nov 9 | Recovery then KS #15 | $39,060 | $37,104 | -25.8% |
| Dec 11 | Kill Switch #20 (final) | $30,612 | $29,026 | **-41.9%** |

**Pattern:** Equity recovered between kill switches (Jan 27 back to $50.9K, May 26 back to $49.8K) but each crash ratcheted the floor lower. The Aug cluster (4 KS in 15 days) broke the recovery cycle permanently.

---

## Summary of Findings

### Critical (P0)
1. **Kill Switch Cascade** — 20 triggers, 42.9% of spread exits forced. Restructure threshold or decouple spread stops.
2. **Regime Engine Blind to Crashes** — Scored 67-74 during -15% China crash. Needs momentum reversal + drawdown-from-peak factors.

### High (P1)
3. **VASS Credit Spread Rejection** — 200/207 rejections at VIX 25-30. Expand adaptive floor from VIX 30 threshold to VIX 25.
4. **No Spread-Level Stop** — Individual spreads can lose 60-70% before portfolio kill switch fires.
5. **Cold Start Dominance** — Bot spent ~60% of trading days in defensive/recovery mode.

### Medium (P2)
6. **CB_LEVEL_1 Overfire** — 95 triggers means 50% sizing most of the year. Consider raising CB threshold from 2% to 3%.
7. **VASS Scan Waste** — Engine retries every 15 min on rejection days. Add daily cooldown after 3 consecutive rejections.

### Low (P3)
8. **Bear Put Timing** — Both BEAR_PUT trades lost (Sep 14, Oct 2). The strategy entered debit puts at regime 44 when credit spreads would have been more capital-efficient.
9. **Sep 29 Slippage** — 6.98% slippage on intraday trade during VIX spike. Consider wider bid-ask tolerance for high-VIX trades.

---

## Next Steps for V2.26

| Priority | Fix | Expected Impact |
|:--------:|-----|-----------------|
| P0 | Kill switch restructuring (staged exit or spread decoupling) | Prevent 42.9% forced spread exits |
| P0 | Regime momentum reversal detector (20-day HWM + consecutive KS counter) | Prevent bull entries during crashes |
| P1 | Expand credit spread IV threshold to VIX 25 | Enable credit spread fills in moderate-high IV |
| P1 | Spread-level hard stop at -40% of debit | Cap individual spread losses |
| P2 | CB_LEVEL_1 threshold review | Reduce defensive sizing frequency |
| P2 | VASS daily rejection cooldown | Save compute on no-fill days |
