# V3.0 FullFix 2015 v2 - Backtest Audit Report

**Backtest Name:** V3_0_FullFix_2015_v2
**Period:** 2015-01-01 to 2015-12-31
**Starting Capital:** $50,000
**Market Context:** CHOPPY (S&P 500 roughly flat, high intraday volatility)
**Audit Date:** 2026-02-05

---

## STEP 1: Context Summary

- **Log File:** `docs/audits/logs/stage3/V3_0_FullFix_2015_v2_logs.txt`
- **Orders File:** `docs/audits/logs/stage3/V3_0_FullFix_2015_v2_orders.csv`
- **Key Config Settings:**
  - Kill Switch: -5% (Tier 1: -2%, Tier 2: -4%, Tier 3: -6%)
  - Drawdown Governor Levels: 3% → 75%, 6% → 50%, 10% → 25%, 15% → 0%
  - Governor Recovery Base: 8%
  - Regime Override: Enabled (5 days at regime >= 70, jump to 50%)
  - Trend Entry Regime Min: 50 (Neutral+)
  - Options Entry: Regime > 70 for CALL spreads, Regime < 50 for PUT spreads

---

## STEP 2: Performance Summary

| Metric | Value |
|--------|-------|
| **Final Equity** | $37,918 |
| **Net Return** | **-24.16%** |
| **Max Drawdown** | **24.7%** (Nov 20, 2015) |
| **HWM** | $50,029 (Jan 12, 2015) |
| **Total Orders (CSV)** | 124 orders |
| **Total Trading Days** | ~252 |
| **Days at Governor 0%** | **266 days** (73% of year) |

### Key Finding: Death Spiral Trap

The bot entered a death spiral early in January 2015:
1. **Jan 15**: First STEP_DOWN (100% → 75%) at -3.8% drawdown
2. **Jan 22**: Second STEP_DOWN (75% → 50%) at -6.2% drawdown
3. **Mar 20**: Third STEP_DOWN (75% → 25%) at -14.7% drawdown
4. **Apr 3**: First 0% SHUTDOWN at -19.2% drawdown

From April 3 onwards, the bot spent **266 days at Governor 0%**, unable to trade except during brief REGIME_OVERRIDE windows.

---

## STEP 3: Engine-by-Engine Breakdown

### 3A. Trend Engine (QLD/SSO/TNA/FAS)

| Metric | Count |
|--------|-------|
| Total Trend Fills | 32 |
| Entries | 16 |
| Exits | 16 |
| ADX-Blocked Entries | **705** |

**Evidence of ADX Blocking Pattern:**
```
2015-12-31 15:45:00 TREND: QLD entry blocked - ADX 17.6 too weak (score=0.50 < 0.75, regime=65)
2015-12-30 15:45:00 TREND: SSO entry blocked - ADX 17.3 too weak (score=0.50 < 0.75, regime=66)
```

**Trend Trades Timeline:**
1. **Jan 12**: SSO/FAS entry (first trend trades)
2. **Jan 20**: SSO exit, FAS rebalance
3. **Jan 26**: SSO entry
4. **Feb 2**: FAS entry
5. **Mar 2**: QLD entry (first QLD)
6. **Mar 13**: QLD exit (stop triggered)
7. **Mar 16**: QLD re-entry
8. **Apr 6-7**: GOVERNOR_SHUTDOWN forced liquidation (multiple duplicate fills)
9. **Apr 17**: KS_TREND_EXIT at -4.26% daily loss
10. **Apr 18-20**: Cold start warm entry for QLD
11. **Apr 21**: GOVERNOR_SHUTDOWN liquidation
12. **May 18**: TNA entry (first TNA)
13. **May 26**: GOVERNOR_SHUTDOWN liquidation (duplicate fills)
14. **Nov 16**: QLD entry
15. **Nov 20**: GOVERNOR_SHUTDOWN liquidation

**Critical Issue: Duplicate GOVERNOR_SHUTDOWN Orders**
The orders CSV shows multiple identical GOVERNOR_SHUTDOWN orders on the same day (Apr 4-6, May 25-26), suggesting the shutdown logic fires multiple times per day.

### 3B. Options Engine (QQQ Single-Leg)

| Metric | Count |
|--------|-------|
| Options Entries | 36 |
| Options Exits | 36 |
| EXPIRATION_HAMMER_V2 Closes | 4 |
| EARLY_EXERCISE_GUARD Closes | 4 sessions (8 orders) |
| OPTIONS_EOD Blocked | **55** |
| Bearish PUT Spread Allowed | 34 |

**Key Finding: No Spreads Were Executed**
Despite the VASS spread architecture, all options trades were **single-leg puts/calls**. The CSV shows only individual QQQ options (e.g., `QQQ 150109P00102000`), not spread combinations.

**Options Trade Pattern:**
- Entry: Market order for calls/puts
- Stop: StopMarketOrder (often triggered)
- Profit Target: Limit order (sometimes filled, sometimes canceled)

**Governor Blocking:**
- 55 days blocked by Governor (SHUTDOWN or Scale < 50%)
- 34 days where "Bearish PUT spread allowed" but unclear if executed

### 3C. Mean Reversion Engine (TQQQ/SOXL)

| Metric | Count |
|--------|-------|
| MR Entries | 0 |
| MR Exits | 0 |
| TQQQ/SOXL Fills | 0 |

**Observation:** Mean Reversion Engine was completely inactive throughout 2015. No TQQQ or SOXL trades were executed. This is expected given:
1. MR requires regime >= 50 (NEUTRAL+)
2. VIX was elevated during crash periods
3. Governor was at 0% for most of the year

### 3D. Hedge Engine (TMF/PSQ)

| Metric | Count |
|--------|-------|
| TMF Fills | 0 |
| PSQ Fills | 0 |
| Hedge Engine Resets | 365 (daily) |

**Critical Gap:** No hedges were deployed during the -24% drawdown. The logs show `Hedge: TMF=0% PSQ=0%` for all regime states throughout 2015.

This is a **thesis violation** - the system should have deployed TMF/PSQ hedges when regime dropped below 50, which happened 44 times according to regime score analysis.

### 3E. Yield Sleeve (SHV)

| Metric | Count |
|--------|-------|
| SHV Fills | 0 |

**Observation:** Yield Sleeve was inactive. Cash was held as USD, not parked in SHV for yield.

---

## STEP 4: Risk & Safeguard Verification

### 4A. Kill Switch

| Metric | Count |
|--------|-------|
| KS_GRADUATED Triggers | 14 |
| Tier 1 (REDUCE at -2%) | 9 |
| Tier 2 (TREND_EXIT at -4%) | 1 |
| Tier 3 (FULL_EXIT at -6%) | 0 |

**Evidence:**
```
2015-04-17 09:31:00 KS_GRADUATED: NONE → TREND_EXIT | Loss=4.26% from prior_close | Baseline=$39,952.55 | Current=$38,250.55
2015-04-17 09:31:00 KS_GRADUATED: TREND_EXIT at 2015-04-17 09:31:00 | Equity=38,250.55 | Loss=-4.26% | Options P&L=$-1,495 | Trend P&L=$0
2015-04-17 09:31:00 KS_TREND_EXIT: Liquidated 6 equity symbols
```

**Assessment:** Kill switch functioned correctly - Tier 1 triggered 9 times at -2%, Tier 2 triggered once at -4.26%, and spread decouple preserved options positions.

### 4B. Drawdown Governor

| Metric | Count |
|--------|-------|
| STEP_DOWN Events | 11 |
| STEP_DOWN_BLOCKED (Immunity) | 80+ |
| REGIME_OVERRIDE Triggers | 10 |
| Days at Scale 0% | **266** |

**Governor Timeline:**
| Date | Event | DD% | Scale |
|------|-------|-----|-------|
| Jan 15 | STEP_DOWN | 3.8% | 100% → 75% |
| Jan 22 | STEP_DOWN | 6.2% | 75% → 50% |
| Feb 24 | REGIME_OVERRIDE | 9.2% | 50% → 75% |
| Mar 6 | STEP_DOWN | 9.3% | 75% → 50% |
| Mar 10 | REGIME_OVERRIDE | 9.5% | 50% → 75% |
| Mar 20 | STEP_DOWN | 14.7% | 75% → 25% |
| Mar 24 | REGIME_OVERRIDE | 14.6% | 25% → 50% |
| Apr 3 | **STEP_DOWN** | 19.2% | **50% → 0%** |
| Apr 11 | REGIME_OVERRIDE | 20.0% | 0% → 50% |
| Apr 21 | STEP_DOWN | 23.3% | 50% → 0% |
| ... | Pattern repeats | ... | ... |

**Death Spiral Pattern:**
1. Drawdown triggers Governor step-down
2. Reduced allocation limits recovery potential
3. REGIME_OVERRIDE provides brief 10-day trading window
4. Immunity expires → immediate STEP_DOWN back to 0%
5. Equity remains flat → HWM never updated → cycle repeats

### 4C. Other Safeguards

| Safeguard | Count | Notes |
|-----------|-------|-------|
| VOL_SHOCK | 239 | Working - paused entries for 15 min |
| WEEKLY_BREAKER | 4 | Triggered week of Jan 22 |
| GAP_FILTER | 0 | Not triggered |
| TIME_GUARD | Not logged explicitly | |
| SPLIT_GUARD | 2 | PSQ split on Nov 5-6, 2014 (warmup) |
| PANIC_MODE | 0 | No SPY -4% intraday events |

---

## STEP 5: Funnel Analysis (Signal Loss)

```
Stage 1: Trading Days                               252 days
         ↓
Stage 2: Days Regime >= 50 (entries allowed)        ~208 days (83%)
         ↓
Stage 3: Days Regime >= 70 (CALL spreads)           114 days (45%)
         Days Regime < 50 (PUT spreads)              44 days (17%)
         ↓
Stage 4: Days Governor > 0% (not shutdown)          **80 days (32%)**
         ↓
Stage 5: ADX >= 15 (trend entry)                    ~50 days
         ↓
Stage 6: Actual Trend Entries                       16 trades
```

**Biggest Leakage Point: GOVERNOR SHUTDOWN (266 days at 0%)**

The Governor was the primary bottleneck. Even when regime was bullish (70+), the Governor was at 0% and blocked ALL bullish entries including both:
- Trend positions (QLD/SSO/TNA/FAS)
- Bullish CALL spreads

The REGIME_OVERRIDE mechanism provided only brief windows (10 days each, 10 times = 100 days max) before snapping back to 0%.

---

## STEP 6: Timeline Verification

| Time | Event | Log Pattern | Status |
|------|-------|-------------|--------|
| 09:25 | Pre-market setup | `RISK: Daily state reset` | WORKING |
| 09:25 | equity_prior_close | `RISK: Set equity_prior_close` | WORKING |
| 09:31 | MOO fallback | `EXEC: MOO_FALLBACK` | WORKING |
| 09:33 | SOD baseline | `RISK: Set equity_sod` | WORKING |
| 10:00 | Warm entry | `COLD_START: WARM_ENTRY` | WORKING |
| 10:00 | VIX spike detection | `VIX_SPIKE:` | WORKING |
| 13:55 | Time guard | Not explicitly logged | N/A |
| 14:00 | Expiration Hammer | `EXPIRATION_HAMMER_V2:` | WORKING |
| 14:00 | Early Exercise Guard | `EARLY_EXERCISE_GUARD:` | WORKING |
| 15:45 | EOD processing | `REGIME:`, `CAPITAL: EOD` | WORKING |
| 15:45 | MR force close | No MR positions | N/A |
| 16:00 | State persistence | `STATE: SAVE_ALL` | WORKING |

**Assessment:** All scheduled events firing correctly. No missing events detected.

---

## STEP 7: Regime Analysis

### Regime Distribution (EOD scores)

| Regime State | Score Range | Days | % |
|--------------|-------------|------|---|
| RISK_ON | 70-100 | 114 | 29% |
| NEUTRAL | 50-69 | 237 | 60% |
| CAUTIOUS | 40-49 | 44 | 11% |
| DEFENSIVE/BEAR | 0-39 | 0 | 0% |

**Key Insight:** The regime engine correctly identified 2015 as primarily NEUTRAL (60% of days) with occasional RISK_ON windows (29%) and CAUTIOUS periods (11%). Despite this mostly neutral/bullish read, the bot lost -24% because:

1. **Early losses locked in HWM** at only $50,029 (day 8)
2. **Governor never reset HWM** - even at regime 74, drawdown was still calculated from Jan 12 peak
3. **89 days of "fake bull"** - regime was 70+ but Governor was at 0%

### Regime Detection Latency

The regime engine detected the August 2015 mini-crash appropriately:
- Aug 24-28 showed regime scores dropping to 44-47 (CAUTIOUS)
- Bearish PUT spreads were correctly allowed during this period

However, **no PUT spreads were actually executed** despite the allowance.

---

## STEP 8: Smoke Signals (Critical Failure Flags)

| Severity | Pattern | Found | Notes |
|----------|---------|-------|-------|
| CRITICAL | ERROR/EXCEPTION | 0 | Clean execution |
| CRITICAL | MARGIN_ERROR | 0 | No margin issues |
| CRITICAL | SIGN_MISMATCH | 0 | No spread pairing bugs |
| CRITICAL | NAKED/ORPHAN | 0 | No orphaned legs |
| WARN | SLIPPAGE_EXCEEDED | 2 | Minor (2.46%, 3.23%) |
| WARN | ASSIGNMENT/EXERCISE | 0 | Early Exercise Guard worked |
| INFO | EXPIRATION_HAMMER | 4 | Working correctly |
| INFO | FRIDAY_FIREWALL | 52 | Working (weekly) |
| INFO | GOVERNOR_SHUTDOWN | 26 | Log entries |

**Critical Issue: Duplicate GOVERNOR_SHUTDOWN Orders**

The orders CSV shows concerning duplicate fills:
```
2015-04-04T13:25:00Z,QLD,4.2868437005,-549,Market On Open,Filled,-2353.48,"GOVERNOR_SHUTDOWN"
2015-04-05T13:25:00Z,QLD,4.2868437005,-549,Market On Open,Filled,-2353.48,"GOVERNOR_SHUTDOWN"
2015-04-06T13:25:00Z,QLD,4.2868437005,-549,Market On Open,Filled,-2353.48,"GOVERNOR_SHUTDOWN"
```

Same position liquidated 4 times on consecutive days. This suggests the position tracking or shutdown logic has a bug.

---

## STEP 9: Optimization Recommendations

### P0 - CRITICAL

#### 1. Fix Duplicate GOVERNOR_SHUTDOWN Orders
**What:** GOVERNOR_SHUTDOWN fires multiple times for the same position on consecutive days.
**Evidence:** Orders CSV shows QLD/FAS sold 4 times each (Apr 3-6), TNA sold 2 times (May 25-26).
**Impact:** Creates phantom sells, corrupts position tracking.
**Fix:** In `_handle_governor_shutdown()`, add check:
```python
if symbol in self._shutdown_liquidated_today:
    return  # Already liquidated
self._shutdown_liquidated_today.add(symbol)
```

#### 2. Hedge Engine Not Deploying Hedges
**What:** TMF/PSQ never deployed despite 44 days with regime < 50.
**Evidence:** All regime logs show `Hedge: TMF=0% PSQ=0%` even when score was 44-49.
**Impact:** No downside protection during drawdowns, violates thesis.
**Fix:** Investigate `hedge_engine.py` - the HEDGE_REGIME_GATE (50) should trigger hedges when regime < 50.

### P1 - HIGH

#### 3. Governor Death Spiral - HWM Never Resets
**What:** HWM stuck at $50,029 for entire year. Even 10 REGIME_OVERRIDEs couldn't escape.
**Evidence:** Final day still shows `HWM=$50,029` despite 9 months of trading.
**Impact:** Bot trapped in perpetual drawdown state, unable to trade.
**Fix:** Add `GOVERNOR_HWM_RESET_ENABLED` config option:
```python
# config.py
GOVERNOR_HWM_RESET_ON_REGIME_OVERRIDE = True  # Reset HWM after successful REGIME_OVERRIDE window
GOVERNOR_HWM_RESET_MIN_DAYS = 20  # Minimum days at 50%+ before HWM reset
```

#### 4. ADX Threshold Too Restrictive in Choppy Markets
**What:** 705 trend entries blocked due to ADX requirements.
**Evidence:** `ADX 17.6 too weak (score=0.50 < 0.75, regime=65)` - requires 0.75 score which needs ADX > 25.
**Impact:** Missed trend participation during grinding rallies.
**Fix:** In `config.py`:
```python
# Current: ADX_BULL_MINIMUM = 15  # But score requires 0.75 (ADX > 25)
# Fix: Lower score threshold in neutral regime
ADX_SCORE_THRESHOLD_BULL = 1.0  # Regime > 70
ADX_SCORE_THRESHOLD_NEUTRAL = 0.50  # Regime 50-69 (current value already blocks)
ADX_SCORE_THRESHOLD_BEAR = 0.75  # Regime < 50
```

#### 5. No Spread Execution Despite VASS Architecture
**What:** All 36 options trades were single-leg, not spreads.
**Evidence:** Orders CSV shows individual options, no spread leg pairs.
**Impact:** Higher risk exposure, no defined-risk trades.
**Fix:** Investigate why spread construction is failing silently. Add logging:
```python
self.Log(f"SPREAD_CONSTRUCT: Failed | Reason={reason} | Long={long_strike} | Short={short_strike}")
```

### P2 - MEDIUM

#### 6. REGIME_OVERRIDE Cooldown Too Short
**What:** 10-day cooldown allows rapid cycling between override and shutdown.
**Evidence:** 10 REGIME_OVERRIDE triggers, each followed by immediate STEP_DOWN.
**Impact:** Creates oscillating behavior without sustained recovery.
**Fix:** In `config.py`:
```python
GOVERNOR_REGIME_OVERRIDE_COOLDOWN_DAYS = 20  # Increase from 10 to 20
```

#### 7. SHV Yield Sleeve Inactive
**What:** Cash held as USD, not parked in SHV.
**Evidence:** Zero SHV fills in orders CSV.
**Impact:** Missed yield on idle cash during 266 shutdown days.
**Fix:** Verify `yield_sleeve.py` is being called during Governor shutdown state.

### P3 - LOW

#### 8. Excessive VOL_SHOCK Triggers
**What:** 239 VOL_SHOCK pauses during the year.
**Evidence:** Often triggers on normal market moves (e.g., `Bar range=$0.2490 | Threshold=$0.2455`).
**Impact:** May block valid entries during volatile but tradeable periods.
**Fix:** Consider raising threshold:
```python
VOL_SHOCK_ATR_MULT = 3.5  # Increase from 3.0
```

#### 9. Log Verbosity
**What:** Duplicate STATE: SAVE_ALL entries per day.
**Evidence:** Two saves at 16:00 daily (`Save #575`, `Save #576`).
**Impact:** Minor log bloat, potential performance overhead.
**Fix:** Deduplicate EOD state save calls.

---

## STEP 10: Scorecard

| System | Score | Status | Key Finding |
|--------|:-----:|--------|-------------|
| Trend Engine | 2/5 | Impaired | 705 ADX blocks, only 16 entries all year |
| Options Engine | 2/5 | Impaired | No spreads executed, only single-leg trades |
| MR Engine | 1/5 | Inactive | Zero trades - completely blocked |
| Hedge Engine | 1/5 | **BROKEN** | No TMF/PSQ deployed despite thesis requirements |
| Kill Switch | 4/5 | Working | Graduated tiers functioned correctly |
| Drawdown Governor | 2/5 | Death Spiral | 266 days at 0%, HWM never reset |
| Regime Detection | 4/5 | Working | Accurate classification, latency acceptable |
| Overnight Safety | 5/5 | Working | No 3x overnight holds detected |
| State Persistence | 4/5 | Working | Consistent saves, minor duplicate issue |
| **Overall** | **2/5** | Significant Issues | Death spiral trapped bot for 73% of year |

---

## Executive Summary

The V3.0 FullFix 2015 backtest reveals a **critical death spiral pattern** that rendered the bot unable to trade for 73% of the year. The core issues are:

1. **Governor Death Spiral**: Early losses (-6%) triggered STEP_DOWN → HWM locked → 266 days at 0%
2. **Hedge Engine Failure**: No hedges deployed despite thesis requirement, leaving portfolio unprotected
3. **Spread Execution Failure**: Options engine reverted to single-leg trades instead of defined-risk spreads
4. **ADX Overcorrection**: 705 trend entries blocked, leaving bot sidelined during market recovery

The REGIME_OVERRIDE mechanism provided brief trading windows but the immediate STEP_DOWN on cooldown expiry created an oscillating trap rather than sustained recovery.

**Recommended Priority:**
1. **P0**: Fix duplicate shutdown orders + investigate hedge engine failure
2. **P1**: Implement HWM reset mechanism for sustained REGIME_OVERRIDE recovery
3. **P1**: Debug spread construction to enable defined-risk options
4. **P2**: Tune ADX thresholds for choppy market conditions

---

*Report generated by Claude Audit Agent*
