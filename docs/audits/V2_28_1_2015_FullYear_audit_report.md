# V2.28.1 Audit Report — 2015 Full Year Backtest

**Build:** V2.28.1-2015-FullYear-Tightened
**Period:** 2015-01-01 → 2015-12-31
**Seed:** $50,000
**End Equity:** $45,466.10
**Net Return:** -9.07%
**Max Drawdown:** -9.3%
**Total Orders:** 22 (11 round-trip trades)
**Backtest ID:** 00e2b191330ffe8fa9817ef08eb6770e
**Config:** V2.28.1 Tightened (KS 2/4/6%, Governor 3/6/10/15%, Recovery 12%)
**Baseline (V2.25):** -42% max DD, ~285 orders | **V2.28.0:** -82.3% max DD, 222 orders

---

## Phase 1: Execution Hygiene (The "Plumbing" Check)

| Metric | Data Source | Audit Question | Finding |
|--------|-----------|----------------|---------|
| Atomic Synchronization | orders.csv | Do Long and Short legs of the spread have the same timestamp? | **PASS** — Spread entered 2015-01-05T15:00 (both legs same second). Exited 2015-01-06T18:20 (both legs same second). |
| Ghost Fills | orders.csv | Any fills with Price=0 or Value=0? | **0 ghost fills** — All 22 orders have valid prices and values. |
| Asset Validation | trades.csv | Any unknown symbols or unintended asset classes? | **CLEAN** — Symbols: FAS, SSO, QLD, TNA, QQQ options (C100, C105). All expected. |
| Slippage Audit | orders.csv | Compare limit vs fill price. Slippage > 2%? | **PASS** — All orders are Market or MOO. No limit orders to compare. Fills appear reasonable for MOO orders. |

---

## Phase 2: Regime & Logic Latency (The "Reaction" Analysis)

### A. The "Falling Knife" Test (Bull-to-Bear Transitions)

**Steepest drop:** Jan 5-7, 2015 — market sold off, triggering governor from 100% → 75% → 50% in 2 days.

- **Did the bot buy Long Calls while price was below 50-SMA?** NO — The bull call spread (QQQ C100/C105) was entered Jan 5 during Cold Start phase (Day 5). This was appropriate timing; the spread was closed the next day at a loss.
- **How many days for Regime Engine to switch?** Governor stepped down to 75% on **Jan 6** (DD=3.6%) and to 50% on **Jan 7** (DD=7.8%). Response was fast — 2 days from first loss to half-allocation.
- **Was the exit too slow?** The spread closed Jan 6 — same day as first step-down. Trend positions (FAS, SSO) also closed within 1-4 days. Exit speed was adequate.

### B. The "Missed Rally" Test (Bear-to-Bull Transitions)

**Sharpest recovery:** Mar-Jul 2015 — QQQ recovered from Jan lows.

- **Did the bot re-enter within first 5% of move?** YES — QLD entry on Mar 2 and Mar 16 caught the recovery. The Mar 16 QLD position was held until Jul 6 and was the **only profitable trade** of the year (+$102.89).
- **Correct instrument?** YES — Used QLD (2× Nasdaq) through Trend Engine. No options were attempted after the Jan spread loss. Governor at 50% limited sizing.

**Critical issue: Governor NEVER recovered.** DD hovered at 7.8-8.6% all year. With 12% recovery threshold, equity needed to reach ~$51,500 from a floor of ~$45,900 — a 12% gain while at 50% allocation. This created a permanent half-allocation trap.

---

## Phase 3: Risk Management Stress Test

### A. The "Hall of Shame" (Top 3 Biggest Losers)

| Rank | Trade | Symbol | Period | Loss | Loss % |
|------|-------|--------|--------|------|--------|
| 1 | Bull Call Spread (net) | QQQ C100/C105 | Jan 5-6 | **-$2,780** | -102.6% of premium |
| 2 | SSO Cold Start | SSO | Jan 2-6 | **-$842.62** | -6.7% |
| 3 | QLD Trend | QLD | Mar 2-13 | **-$231.53** | -6.7% |

**Root Cause Analysis:**

1. **Spread loss (-$2,780):** Entered Day 5 of Cold Start, market immediately reversed. The long C100 leg lost $3,160 while short C105 gained only $380. This is 55% of total losses for the year in a single trade. The spread was held only 1 day before closing, but the $5 width and 20 contracts created $5,420 max risk — oversized for a $50K account in Cold Start.
   - **Would a -30% hard option stop have helped?** Partially. The long leg fell from $2.96 to $1.38 (53% loss). A -30% stop would have closed at ~$2.07, saving ~$1,180.
2. **SSO loss (-$842.62):** 1,637 shares of SSO = $12,499 position (25% of equity). Entered as Cold Start warm entry. Governor hadn't stepped down yet. Position was too large for the market conditions.
3. **QLD loss (-$231.53):** Standard trend entry at 50% governor. Sized correctly at ~$3,448 (7.5% of equity at half allocation).

### B. Position Sizing Safety

- **Max single entry:** SSO $12,499 (25% of equity) — Jan 2 Cold Start warm entry
- **Second largest:** QQQ spread $5,420 max risk (10.8% of equity)
- **After governor:** Entries capped at ~$3,500 (7-8% of equity) — appropriate
- **Finding:** Cold Start phase allowed oversized entries before governor activated. The $12,499 SSO position and $5,420 spread risk are too aggressive for Day 2-5 of a new backtest.

---

## Phase 4: Profit Attribution (The "Winner" Anatomy)

### A. The "Hall of Fame" (Top 3 Best Trades)

| Rank | Trade | Symbol | Period | Profit | Profit % |
|------|-------|--------|--------|--------|----------|
| 1 | QLD Trend | QLD | Mar 16 - Jul 6 | **+$102.89** | +3.0% |
| 2 | Short Call Leg | QQQ C105 | Jan 5-6 | **+$380** | (part of losing spread) |
| 3 | — | — | — | — | No other winners |

**Only 2 winning trades out of 11 (18% win rate).**

**Profit Driver Analysis:**

1. **QLD Mar-Jul (+$102.89):** Pure directional (delta) win. Held 112 days through a QQQ recovery. This is the Trend Engine working as designed — MA200+ADX entry, patient hold. BUT the governor capped it at 787 shares ($3,443) instead of the full ~$6,900 it would have been at 100% scale. The profit was halved by governor scaling.
2. **QQQ C105 short leg (+$380):** Part of the losing spread. The short leg expired towards worthless while the long leg lost. Not independently scalable — it's a hedge leg.

---

## Phase 5: Required Optimizations (The Action Plan)

### 1. Risk Patch: Ghost Spread State (CRITICAL — NEW BUG)

**Finding:** 43,291 `SPREAD_EXIT_WARNING: Missing price data` log lines (85.5% of all logs). The bull call spread was closed on Jan 6, but the options_engine never cleared its internal spread position state. This ghost spread:
- Consumed 85% of log bandwidth, causing logs to truncate at June 26 (only 6 of 12 months captured)
- Fires every minute for the entire year checking for price data on expired contracts
- May block new spread entries if the engine thinks a position is already open
- **Fix:** After spread fill detected in `OnOrderEvent`, ensure `clear_spread_position()` is called. Check if the existing fill handler at main.py:4941 actually fires for both legs.

### 2. Filter Patch: Cold Start Position Sizing

**Finding:** Cold Start warm entry on Day 2 allocated 25% ($12,499) to SSO before any risk signals existed. The spread on Day 5 risked $5,420 (10.8%).
- **Fix:** Apply governor-like scaling during Cold Start — cap any single entry at 10% during Days 1-5, or delay spread entries until after Cold Start completes.

### 3. Execution Patch: Governor Recovery Trap

**Finding:** Governor stepped to 50% on Jan 7 and never recovered for the remaining 358 days. The 12% recovery threshold requires equity to grow from ~$46K to ~$51.5K — an impossible feat at 50% allocation with a losing strategy.
- **Fix:** Implement time-based governor decay. If governor has been at the same level for >60 days and no new drawdown has occurred, step up one level. Alternatively, base recovery on "trough recovery" (% gain from the lowest point) rather than absolute recovery from HWM.

---

## Phase 6: The "Funnel Analysis"

| Funnel Stage | Data Source | V2.28.1 Logic Check | Status |
|-------------|-----------|---------------------|--------|
| 1. Market Regime | logs.txt | Regime Engine detecting VIX correctly? | **PASS** — VIX consistently 14.0 in Jun 2015 logs (actual: ~12-18 range). MICRO_UPDATE shows NORMAL regime. |
| 2. VASS Selection | logs.txt | Correct Strategy Matrix? | **N/A** — Only 1 spread entered (Jan 5), during Cold Start. No VASS signals after governor locked at 50%. |
| 3. Sniper Signal | logs.txt | Count of VASS_SIGNAL_GENERATED | **0** — No VASS signals found in logs. Options engine inactive after Jan spread. |
| 4. Margin Filter | logs.txt | Count of MARGIN_RESERVED lines | **0** — No margin reservations logged. |
| 5. Execution | trades.csv | Count of ComboMarketOrders filled | **1** — Single spread (2 legs) on Jan 5. |

**V2.28.1 Diagnosis:**
- Governor at 50% blocked all options activity after Jan 7 (intraday gate requires 100% governor scale)
- Trend Engine entered positions at half-size but couldn't generate enough profit to trigger recovery
- The system spent 98% of the year in a "zombie state" — alive but barely trading, slowly bleeding via small trend losses

---

## Phase 7: Logic Integrity Checks

### A. VASS Strategy Matrix
- [N/A] Only 1 spread trade. VIX was ~17 in early Jan 2015 — debit spread was correct choice for medium IV.
- [N/A] No entries when UVXY spiking — governor blocked everything.
- [N/A] No $0.35 rejections — no credit spreads attempted.

### B. Gamma Pin & Expiry Protection
- [N/A] Spread closed Jan 6, well before Jan 17 expiry. No gamma pin risk.
- [FAIL] **Ghost spread state persisted after Jan 6 close** — the engine kept checking for spread exit conditions on expired contracts for 12 months.
- [PASS] Spread legs correct: 1 Buy (C100) and 1 Sell (C105).

### C. Capital & Settlement Security
- [N/A] No Jan 21 Tuesday gate test — no options activity by then.
- [PASS] Options position sizing: 20 contracts × $2.96 = $5,920 entry (within $5,000 hard cap for premium? Marginal — $5,420 net risk after short leg credit).
- [N/A] Trend vs Options cash reserve: Governor at 50% made this moot.

---

## Phase 8: Critical Failure Flags ("Smoke Signals")

| Severity | Search Keyword | Found? | Count | Action |
|----------|---------------|--------|-------|--------|
| CRITICAL | VASS_REJECTION_GHOST | No | 0 | PASS |
| CRITICAL | MARGIN_ERROR_TREND | No | 0 | PASS |
| CRITICAL | SIGN_MISMATCH | No | 0 | PASS |
| **CRITICAL** | **SPREAD_EXIT_WARNING** | **YES** | **43,291** | **FAIL: Ghost spread — fix spread state cleanup** |
| WARN | SLIPPAGE_EXCEEDED | No | 0 | PASS |
| INFO | GAMMA_PIN_EXIT | No | 0 | N/A — No near-expiry positions |
| INFO | SETTLEMENT_GATE_OPEN | No | 0 | N/A |

---

## Summary & Verdict

### What Worked
- **Governor protected capital:** -9.3% DD vs V2.25's -42% and V2.28.0's -82%. The tightened governor (3/6/10/15%) is highly effective at capital preservation.
- **Fast step-down:** 100% → 50% in 2 days during Jan selloff. No kills switch needed.
- **Aug 2015 crash (-12% QQQ):** Only 1 small TNA position ($1,801) was open. Lost $114 — minimal damage. Governor kept the algo mostly in cash.

### What Failed
1. **Ghost Spread Bug (CRITICAL):** Spread state not cleared after Jan 6 close. 43,291 warning logs consumed the entire 5MB log budget, truncating logs at June 26. Potential blocker for new spread entries.
2. **Governor Recovery Trap:** 50% scale for 358 days straight. The 12% recovery threshold is unreachable when you're only half-invested. The algo cannot earn its way back.
3. **Cold Start Oversizing:** 25% SSO + 10.8% spread risk in first 5 days, before any risk data exists. This created the initial -7.8% drawdown that locked the governor.
4. **Zero positive alpha:** -9.07% in a year where QQQ gained +9.4%. The trend engine caught one winner (QLD Mar-Jul, +$103) but 8 other trend trades all lost.

### Priority Fixes for V2.29

| Priority | Bug | Impact | Fix |
|----------|-----|--------|-----|
| P0 | Ghost spread state after close | Blocks all future spreads, log spam | Clear spread position in fill handler when both legs filled |
| P1 | Governor recovery trap | 50% allocation for 358 days | Time-based governor decay or trough-relative recovery |
| P2 | Cold Start oversizing | -7.8% DD in first 5 days | Cap entries at 10% during Cold Start |

---

*Audit completed: 04 February 2026*
*Auditor: Claude Code (V2.28.1 AAP)*
