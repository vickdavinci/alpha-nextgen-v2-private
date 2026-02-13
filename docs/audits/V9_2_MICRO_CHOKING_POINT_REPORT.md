# V9.2 MICRO Engine Choking Point Report — All Regimes

**Generated:** 2026-02-12
**Data Source:** V9_1_FullYear2022_r2_logs.txt (48,074 lines)
**Backtest Period:** January 1 – December 30, 2022
**Focus:** Universal parameters (common across ALL micro regimes) that choke signal → trade conversion

---

## Executive Summary

The MICRO engine evaluated **3,297 signal candidates** in 2022. Only **319 became completed trades** — a **9.7% pass-through rate**. The remaining 90.3% were killed by a cascading 6-layer gate pipeline with **66 distinct rejection points**.

This report identifies every universal parameter that chokes conversion regardless of micro regime, ranked by impact.

---

## 1. Complete Signal Funnel

```
┌──────────────────────────────────────────────────────────────────────────┐
│                   MICRO SIGNAL → TRADE FUNNEL (2022)                     │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  15-min Scan Evaluations:         ~3,297 (estimated)                     │
│        ↓                                                                 │
│  ◉ LAYER 1: Micro Regime Blocks    -1,243  ██████████████████            │
│    (CONFIRMATION_FAIL, QQQ_FLAT,                                         │
│     REGIME_NOT_TRADEABLE, VIX_STABLE)                                    │
│        ↓                                                                 │
│  Candidates Formed:                2,054                                 │
│        ↓                                                                 │
│  ◉ LAYER 2: Macro-Micro Resolver   -685   ██████████                    │
│    (NEUTRAL blocks, BEARISH blocks,                                      │
│     conviction not extreme)                                              │
│        ↓                                                                 │
│  Signals with Direction:           1,369                                 │
│        ↓                                                                 │
│  ◉ LAYER 3: Execution Gates        -1,369  ████████████████████          │
│    (CALL_GATE_STRESS, TRADE_LIMIT,                                       │
│     TIME_WINDOW, MA20, CAP, COOLDOWN)                                    │
│        ↓                                                                 │
│  Approved Signals:                  341    ████                          │
│        ↓                                                                 │
│  Completed Trades:                  319    ████                          │
│  (22 missing results)                                                    │
│                                                                          │
│  Pass-through: 319 / 3,297 = 9.7%                                       │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Layer-by-Layer Rejection Breakdown

### Layer 1: Micro Regime Blocks (1,243 rejections — 37.7% of evaluations)

These fire inside `_evaluate_intraday_strategy()` before a candidate is even formed.

| Rank | Block Code | Count | % of Layer | Config Parameter | Current Value | Impact |
|:----:|-----------|------:|:----------:|-----------------|:-------------:|--------|
| 1 | **CONFIRMATION_FAIL** | 564 | 45.4% | *Structural* (QQQ + VIX must confirm) | N/A | QQQ direction and VIX direction don't align for any strategy |
| 2 | **REGIME_NOT_TRADEABLE** | 333 | 26.8% | `tradeable_regimes` set (line 1359) | 10 of 21 regimes | 11 regimes are untradeable in RULE 5 |
| 3 | **VIX_STABLE_LOW_CONVICTION** | 191 | 15.4% | `VIX_STABLE_BAND_*` + `MICRO_SCORE_*_CONFIRM` | Band: ±0.2–0.7%, Score: 47/48 | VIX in STABLE band AND micro score below confirmation threshold |
| 4 | **QQQ_FLAT** | 155 | 12.5% | `QQQ_NOISE_THRESHOLD` | 0.04% | QQQ move < 0.04% = no directional edge |

**Key Insight:** CONFIRMATION_FAIL is the #1 overall chokepoint. The engine generates a direction but the VIX/QQQ confirmation fails. This is structural — the divergence/confirmation logic (RULE 5) requires QQQ and VIX to be in specific alignment. In volatile 2022, QQQ and VIX frequently moved in unexpected patterns, failing confirmation 564 times.

---

### Layer 2: Macro-Micro Resolver Blocks (685 rejections — 20.8% of evaluations)

These fire in `generate_micro_intraday_signal()` during macro-micro alignment resolution.

| Rank | Block Reason | Count | Config Parameter | Current Value |
|:----:|-------------|------:|-----------------|:-------------:|
| 1 | **Macro NEUTRAL, regime not tradeable** | 357 | Resolver tradeable list | FULL_PANIC, WORSENING_HIGH, CRASH, DETERIORATING, VOLATILE not in resolver's tradeable list |
| 2 | **Macro BEARISH blocks non-conviction CALL** | 212 | `MICRO_UVXY_CONVICTION_EXTREME` | 3.0% |
| 3 | **Macro NEUTRAL, conviction not extreme** | 112 | `MICRO_UVXY_CONVICTION_EXTREME` | 3.0% |
| 4 | Other (misaligned, no conviction) | 4 | Various | — |

**Key Insight:** The resolver has its OWN tradeable regime list (separate from RULE 5's `tradeable_regimes` set). When Macro is NEUTRAL (41% of 2022), the resolver requires either (a) extreme UVXY conviction (>3.0%) OR (b) the micro regime to be in ITS tradeable list. FULL_PANIC, WORSENING_HIGH, CRASH, and DETERIORATING are NOT in the resolver's tradeable list — so 357 signals from these regimes were blocked even though they had valid direction. Another 212 CALL signals were blocked because Macro was BEARISH and UVXY conviction wasn't extreme enough.

---

### Layer 3: Execution Gate Drops (1,369 rejections — ALL candidates that passed Layers 1+2)

These fire in `check_intraday_entry_signal()` — fully formed, direction-resolved signals rejected at execution.

| Rank | Rejection Code | Count | % of Layer | Config Parameter(s) | Current Value | Direction |
|:----:|---------------|------:|:----------:|---------------------|:-------------:|:---------:|
| **1** | **E_CALL_GATE_STRESS** | **783** | **57.2%** | `INTRADAY_CALL_BLOCK_VIX_MIN` + `INTRADAY_CALL_BLOCK_REGIME_MAX` | VIX ≥ 22 AND Regime ≤ 58 | CALL only |
| 2 | E_INTRADAY_TRADE_LIMIT | 221 | 16.1% | `INTRADAY_MAX_TRADES_PER_DAY` + `MAX_OPTIONS_TRADES_PER_DAY` | 3/day intraday, 5/day global | ALL |
| 3 | E_INTRADAY_TIME_WINDOW | 129 | 9.4% | `INTRADAY_ITM_END` / `INTRADAY_DEBIT_MOMENTUM_END` / `INTRADAY_DEBIT_FADE_END` | ITM: 13:30, DEBIT_MOM: 13:30, FADE: 14:30 | ALL |
| 4 | E_CALL_GATE_MA20 | 103 | 7.5% | `CALL_GATE_MA20_ENABLED` + `CALL_GATE_MA20_BYPASS_REGIME_MIN` | QQQ < SMA20, bypass needs regime ≥ 68 | CALL only |
| 5 | E_INTRADAY_CAP_TOO_SMALL | 81 | 5.9% | `INTRADAY_SPREAD_MAX_PCT` | 8% of portfolio | PUT only |
| 6 | E_CALL_GATE_LOSS_COOLDOWN | 48 | 3.5% | `CALL_GATE_CONSECUTIVE_LOSSES` + `CALL_GATE_LOSS_COOLDOWN_DAYS` | 3 losses → 2-day pause | CALL only |
| 7 | E_INTRADAY_COMBINED_SIZE_MIN | 4 | 0.3% | `OPTIONS_MIN_COMBINED_SIZE_PCT` | 10% minimum | ALL |

**Key Insight:** E_CALL_GATE_STRESS alone killed **783 signals — 57.2% of all Layer 3 drops and 23.7% of all evaluations.** This single parameter pair (VIX ≥ 22 + Regime ≤ 58) blocked every CALL attempt in 2022 because VIX averaged 27.68 and macro regime never reached RISK_ON. Together with E_CALL_GATE_MA20 (103) and E_CALL_GATE_LOSS_COOLDOWN (48), CALL-specific gates killed **934 signals (68.2% of all drops).**

---

## 3. Universal Choking Parameters — Ranked by Impact

These parameters apply across ALL micro regimes. Sorted by total signals killed.

### Tier 1: Massive Impact (>200 signals killed)

| # | Parameter | Value | Signals Killed | % of All Evals | Layer | Mechanism |
|:-:|----------|:-----:|:--------------:|:--------------:|:-----:|-----------|
| **1** | `INTRADAY_CALL_BLOCK_VIX_MIN` | 22.0 | **783** | **23.7%** | Execution | ALL CALL signals blocked when VIX ≥ 22 AND regime ≤ 58. In 2022 (VIX avg 27.68), this was active ~80% of trading days. |
| **2** | `INTRADAY_CALL_BLOCK_REGIME_MAX` | 58.0 | *(combined above)* | | Execution | Partner threshold for CALL_GATE_STRESS. Regime never hit 58+ in 2022. |
| **3** | *CONFIRMATION logic* (structural) | N/A | **564** | **17.1%** | Micro | RULE 5 divergence/confirmation requires QQQ+VIX alignment. No config parameter — hardcoded logic gates. |
| **4** | `tradeable_regimes` set | 10/21 | **333** | **10.1%** | Micro | 11 of 21 micro regimes are non-tradeable. Signals in CHOPPY_LOW, RISK_OFF_LOW, BREAKING, UNSTABLE, VOLATILE blocked. |
| **5** | Resolver tradeable list | varies | **357** | **10.8%** | Resolver | When Macro is NEUTRAL (41% of year), resolver blocks regimes NOT in its own tradeable list. |
| **6** | `MICRO_UVXY_CONVICTION_EXTREME` | 3.0% | **324** | **9.8%** | Resolver | Macro NEUTRAL needs extreme UVXY conviction (>3.0%) to override. 212 BEARISH blocks + 112 NEUTRAL conviction fails. |
| **7** | `INTRADAY_MAX_TRADES_PER_DAY` | 3 | **221** | **6.7%** | Execution | Daily cap exhausted. 193 were PUT signals blocked after 3 trades already placed. |

### Tier 2: Moderate Impact (50–200 signals killed)

| # | Parameter | Value | Signals Killed | % of All Evals | Layer | Mechanism |
|:-:|----------|:-----:|:--------------:|:--------------:|:-----:|-----------|
| **8** | `VIX_STABLE_BAND_*` thresholds | ±0.2–0.7% | **191** | **5.8%** | Micro | VIX in STABLE band → low conviction → NO_TRADE in many regimes |
| **9** | `QQQ_NOISE_THRESHOLD` | 0.04% | **155** | **4.7%** | Micro | QQQ moves < 0.04% classified as FLAT → no trade |
| **10** | `INTRADAY_ITM_END` / `DEBIT_MOMENTUM_END` | 13:30 | **129** | **3.9%** | Execution | ITM_MOMENTUM and DEBIT_MOMENTUM can't enter after 13:30. 115 PUTs + 14 CALLs blocked. |
| **11** | `CALL_GATE_MA20_ENABLED` | True | **103** | **3.1%** | Execution | QQQ below SMA20 → all CALLs blocked (unless regime ≥ 68, which never happened in 2022) |
| **12** | `INTRADAY_SPREAD_MAX_PCT` | 8% | **81** | **2.5%** | Execution | Capital cap too small → can't buy even 1 contract. All 81 were PUT signals in May (deepest drawdown). |

### Tier 3: Minor Impact (<50 signals killed)

| # | Parameter | Value | Signals Killed | % of All Evals | Layer | Mechanism |
|:-:|----------|:-----:|:--------------:|:--------------:|:-----:|-----------|
| **13** | `CALL_GATE_CONSECUTIVE_LOSSES` | 3 losses | **48** | **1.5%** | Execution | 3 consecutive CALL losses → 2-day cooldown |
| **14** | `OPTIONS_MIN_COMBINED_SIZE_PCT` | 10% | **4** | **0.1%** | Execution | Combined sizing multipliers cascade below 10% floor |
| **15** | 15-minute scan throttle | 900s | Unquantifiable | — | Orchestration | Limits evaluations to max 20/day per window. Hidden opportunity cost. |
| **16** | `OPTIONS_MAX_INTRADAY_POSITIONS` | 2 | Unquantifiable | — | Orchestration | Max concurrent positions. Rarely hit (trades are short-lived). |

---

## 4. Cross-Regime Impact Matrix

How each choking parameter affects specific micro regime groups:

| Choking Parameter | Bull Regimes (LOW VIX) | Transition Regimes (MED VIX) | Bear Regimes (HIGH VIX) | Notes |
|-------------------|:----------------------:|:----------------------------:|:-----------------------:|-------|
| CALL_GATE_STRESS (VIX≥22) | No impact (VIX<22) | **Blocks all CALLs** | **Blocks all CALLs** | Bull regimes unaffected; ALL transition+bear CALL signals killed |
| CONFIRMATION_FAIL | Moderate (STABLE VIX common) | **High** (VIX volatile) | **High** (VIX volatile) | Highest in volatile environments where QQQ/VIX decouple |
| TRADE_LIMIT (3/day) | Moderate | **High** | **High** | More signals in volatile markets → cap hit faster |
| TIME_WINDOW (13:30) | Low | Moderate | **High** | Bear regimes generate more ITM_MOMENTUM signals late in session |
| CALL_GATE_MA20 | **High** (QQQ < MA20 in corrections) | **High** | **Very High** (QQQ below MA20 all year) | Bull regimes affected during pullbacks; bear regimes permanently blocked |
| CAP_TOO_SMALL | No impact | Low | **High** (deep drawdowns) | Portfolio value shrinks → can't afford contracts |
| Macro-Micro Resolver | Low | **Very High** (Macro NEUTRAL 41%) | Moderate (Macro BEARISH clear) | NEUTRAL macro is the deadliest for transition regimes |
| QQQ_FLAT | Moderate | Moderate | Low | Low VIX = low QQQ moves more often |
| VIX_STABLE_LOW_CONVICTION | **High** (VIX often stable) | Moderate | Low (VIX rarely stable) | Predominantly affects bull/calm regimes |

---

## 5. Direction Asymmetry Analysis

The gate structure is heavily asymmetric — CALLs face 5 dedicated gates while PUTs face only 1.

### CALL-Specific Gates (5 gates, 934 total blocks)

| Gate | Blocks | % of All Drops | Condition |
|------|-------:|:--------------:|-----------|
| E_CALL_GATE_STRESS | 783 | 57.2% | VIX ≥ 22 AND regime ≤ 58 |
| E_CALL_GATE_MA20 | 103 | 7.5% | QQQ < SMA20 |
| E_CALL_GATE_LOSS_COOLDOWN | 48 | 3.5% | 3 consecutive CALL losses |
| E_CALL_GATE_VIX5D | 0* | — | 5-day VIX rising ≥ 10% |
| E_INTRADAY_GOVERNOR_CALL_BLOCK | 0* | — | Governor = 0% |

### PUT-Specific Gates (1 gate, 0 blocks in 2022)

| Gate | Blocks | Condition |
|------|-------:|-----------|
| E_PUT_GATE_VIX_MAX | 0 | VIX > 38 (never hit in 2022) |

### Direction-Neutral Gates (4 gates, 435 blocks)

| Gate | CALL | PUT | Total |
|------|-----:|----:|------:|
| E_INTRADAY_TRADE_LIMIT | 28 | 193 | 221 |
| E_INTRADAY_TIME_WINDOW | 14 | 115 | 129 |
| E_INTRADAY_CAP_TOO_SMALL | 0 | 81 | 81 |
| E_INTRADAY_COMBINED_SIZE_MIN | 0 | 4 | 4 |

**Key Finding:** Of 1,369 total drops, **934 (68.2%) were CALL-specific gate blocks.** The remaining 435 direction-neutral blocks hit PUTs 90.3% of the time (393/435) because CALLs were already eliminated before reaching these gates. The MICRO engine is structurally biased against CALL trades in any environment where VIX > 22.

---

## 6. Dropped vs Approved — What Gets Through?

### Dropped Signals (1,369) — Strategy Distribution

| Strategy | Dropped | Approved | Drop Rate | Notes |
|----------|--------:|--------:|:---------:|-------|
| DEBIT_MOMENTUM | 879 | 68 | **92.8%** | Highest drop rate. 647 killed by CALL_GATE_STRESS alone. |
| ITM_MOMENTUM | 403 | 237 | **63.0%** | Better survival. Most approved as PUT. |
| DEBIT_FADE | 77 | 36* | 68.1% | Moderate filtering. |
| PROTECTIVE_PUTS | 10 | 36* | 21.7% | Lowest drop rate — crisis signals bypass most gates. |

*Approved counts from INTRADAY_SIGNAL log lines.

### Approved Signals (341) — Direction Distribution

| Direction | Approved | % |
|-----------|--------:|:-:|
| PUT | 324 | 95.0% |
| CALL | 17 | 5.0% |

**Only 17 CALL trades survived the 5-gate CALL filter in all of 2022.** The system essentially became PUT-only.

---

## 7. Monthly Pattern Analysis

### E_INTRADAY_TRADE_LIMIT hits by month

| Month | Hits | Notes |
|-------|-----:|-------|
| Oct | 34 | Highest — volatile month, many signals exhausted cap |
| Nov | 31 | Bear market rally generated many signals |
| Jun | 29 | Summer drawdown, high signal generation |
| Jan | 28 | Year start, high VIX |
| Apr | 28 | Pre-crash signals |
| Aug | 27 | Jackson Hole period |
| Jul | 15 | Moderate |
| Mar | 15 | Moderate |
| Sep | 7 | Fed rate hike shock — fewer signals passed L1+L2 |
| Feb | 6 | Short month |
| Dec | 1 | Minimal activity |
| May | 0 | Deep drawdown — CAP_TOO_SMALL caught signals first |

**Pattern:** TRADE_LIMIT hits cluster in volatile months (Oct, Nov, Jun, Jan, Apr, Aug) where signal generation is highest. In May, the portfolio was so depleted that CAP_TOO_SMALL caught all signals before TRADE_LIMIT could fire.

---

## 8. The Cascading Multiplier Problem

Multiple size modifiers stack multiplicatively, causing the combined size to frequently drop below the 10% minimum floor:

```
combined_mult = cold_start × governor × micro_mult × strategy_mult
```

| Modifier | Condition | Multiplier |
|----------|-----------|:----------:|
| Cold Start | First 5 days | 0.50× |
| Governor Scale | Drawdown state | 0.00–1.00× |
| Micro Score < MODERATE (40) | Low conviction | 0.50× |
| ELEVATED/WORSENING regime | Fragile states | 0.50× |
| NEUTRAL_ALIGNED_HALF | Macro NEUTRAL, no conviction | 0.50× |
| MISALIGNED_HALF | Micro disagrees with Macro | 0.50× |
| CAUTION_LOW regime | Low VIX caution | 0.50× |
| TRANSITION regime | Mild handoff | 0.50× |
| PUT High VIX | VIX ≥ 32 | 0.60× |
| MA20 Bypass | QQQ < MA20 (CALL only) | 0.70× |

**Example cascade:** NEUTRAL macro + WORSENING regime + score 38:
- `1.0 × governor × 0.50 (NEUTRAL_ALIGNED) × 0.50 (WORSENING) × 0.50 (score < 40) = 0.125 × governor`
- If governor = 0.80: combined = 0.10 → **barely passes** 10% floor
- If governor = 0.79: combined = 0.099 → **E_INTRADAY_COMBINED_SIZE_MIN**

This cascade makes small governor changes cause binary pass/fail outcomes.

---

## 9. The 15-Minute Throttle Hidden Cost

The scan throttle (`_should_scan_intraday()` checks every 900 seconds) limits the engine to ~20 evaluations per day within the 10:00–15:00 window. This means:

- **Best possible daily signal capacity:** ~20 candidates
- **With 3-trade daily limit:** Only 15% of scans can convert (3/20)
- **Actual conversion per scan:** ~1.3 trades/day (319 trades / 251 trading days)

The throttle itself isn't the bottleneck — the 3-trade limit is. But the throttle means that if the first 3 scans produce mediocre signals that fill the daily cap, better signals at 11:00+ never get a chance.

---

## 10. Top 10 Actionable Parameters — Ranked by Recovery Potential

| Priority | Parameter | Current | Signals Killed | Recovery Action | Risk |
|:--------:|----------|:-------:|:--------------:|----------------|------|
| **P0** | `INTRADAY_CALL_BLOCK_VIX_MIN` | 22.0 | 783 | Raise to 28+ (only block in true crisis) | Allows CALLs in moderate fear — needs regime confirmation |
| **P0** | `INTRADAY_CALL_BLOCK_REGIME_MAX` | 58.0 | *(above)* | Lower to 48 (only block in DEFENSIVE/RISK_OFF) | Same as above |
| **P1** | `INTRADAY_MAX_TRADES_PER_DAY` | 3 | 221 | Raise to 4–5 | More exposure in volatile markets |
| **P1** | Resolver tradeable list | Excludes crisis regimes | 357 | Add WORSENING_HIGH, DETERIORATING to resolver tradeable list | Allows signals in high-VIX regimes with NEUTRAL macro |
| **P2** | `INTRADAY_ITM_END` | 13:30 | 129 (partial) | Extend to 14:00 for ITM_MOMENTUM | Afternoon entries closer to close — may not develop |
| **P2** | `CALL_GATE_MA20_ENABLED` | True | 103 | Loosen bypass: regime ≥ 55 (not 68) | Allows some CALLs during moderate conditions |
| **P2** | `MICRO_UVXY_CONVICTION_EXTREME` | 3.0% | 324 | Lower to 2.5% | More signals override NEUTRAL macro |
| **P3** | `INTRADAY_SPREAD_MAX_PCT` | 8% | 81 | Raise to 10% or dynamic (scale with portfolio) | Only matters during deep drawdowns |
| **P3** | `CALL_GATE_CONSECUTIVE_LOSSES` | 3 | 48 | Raise to 4 or reduce cooldown to 1 day | Faster CALL re-engagement |
| **P3** | Confirmation logic (structural) | Hardcoded | 564 | Add "strong move" bypass: if \|QQQ\| > 1.0%, skip confirmation | Allows trading strong directional days even with VIX misalignment |

---

## 11. Data Accuracy Notes

- Total evaluations estimated from: MICRO_BLOCK (1,243) + Resolver blocks (685) + SIGNAL_DROPPED (1,369) = 3,297. Does not include pre-scan orchestration blocks (risk engine, margin, startup gate).
- E_CALL_GATE_VIX5D and E_INTRADAY_GOVERNOR_CALL_BLOCK showed 0 hits in 2022 logs — either never triggered or not logged with the standard rejection code format.
- The 22 missing results (341 approved → 319 completed) remain unexplained — likely end-of-year open positions or logging gaps.
- Direction counts from dropped signals: 976 CALL + 393 PUT = 1,369 ✓ (exact match).
- Rejection code counts: 783 + 221 + 129 + 103 + 81 + 48 + 4 = 1,369 ✓ (exact match).

---

## 12. Conclusion

The MICRO engine's 90.3% rejection rate is driven by **three structural bottlenecks**:

1. **CALL_GATE_STRESS kills 23.7% of all evaluations** — a single VIX/regime threshold pair that becomes permanently active in any market with VIX > 22. This effectively makes the engine PUT-only during moderate-to-high volatility.

2. **Macro-Micro Resolver kills 20.8%** — the NEUTRAL macro regime (41% of 2022) requires extreme UVXY conviction (>3.0%) to override. Most valid signals from crisis regimes (WORSENING_HIGH, FULL_PANIC, CRASH) can't override NEUTRAL macro.

3. **TRADE_LIMIT (3/day) kills 6.7%** — but these are the highest-quality signals (already passed all other gates). Early mediocre trades consume the daily budget before better afternoon signals arrive.

The parameter tuning opportunity is significant: relaxing the top 3 choking parameters could recover **~1,300+ candidate signals** for evaluation while maintaining risk controls through the remaining 63 rejection points in the pipeline.
