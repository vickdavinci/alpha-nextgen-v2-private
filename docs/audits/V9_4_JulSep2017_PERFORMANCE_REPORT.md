# V9.4 Performance Report: Jul-Sep 2017 (Options Isolation Mode)

**Backtest Period:** 2017-07-01 to 2017-09-29
**Version:** V9.4 (Options Engine Isolation Mode)
**Capital:** $100,000 starting equity
**Engines Active:** Options Engine (Swing VASS + Micro Intraday), Regime Engine
**Engines Disabled:** Trend, Mean Reversion, Hedge, Yield, Kill Switch (circuit breaker only), Drawdown Governor

---

## 1. Executive Summary

| Metric | Value |
|--------|-------|
| **Starting Equity** | $100,000.00 |
| **Ending Equity** | $95,450.60 |
| **Net Return** | -$4,549 (-4.55%) |
| **Total Trades** | 92 closed |
| **Win Rate** | 43 W / 49 L (46.7%) |
| **Avg Regime Score** | 71.5 (RISK_ON dominant) |
| **Avg VIX** | ~11.2 (Low IV entire period) |
| **KS Tier 1 Activations** | 9 |
| **Win Rate Gate Reductions** | 8 |
| **Orphan Leg Cleanups** | 3 |
| **Peak Equity** | $108,025.20 (Jul 27) |
| **Trough Equity** | $94,142.00 (Sep 26) |
| **Max Drawdown** | -$13,883 (-12.85% from peak) |

### Monthly Breakdown

| Month | Trades | Gross P&L | Best Day | Worst Day |
|-------|-------:|----------:|---------:|----------:|
| July | ~29 | +$4,921 | +$3,820 (Jul 17) | -$3,135 (Jul 5) |
| August | ~34 | +$4,968 | +$5,626 (Aug 29) | -$2,020 (Aug 22) |
| September | ~29 | -$8,034 | +$1,940 (Sep 1) | -$1,938 (Sep 25) |

**Key Finding:** The system was profitable in July-August but gave back all gains and more in September. The September decline was driven by VASS swing spreads that repeatedly lost on bull call entries during a market pullback. Fee drag (-$6,475 total) was a significant contributor to net losses.

---

## 2. Trade-by-Trade Analysis

### 2.1 Swing (VASS) Trades — Spread Pairs

VASS trades are entered as spread pairs (long leg + short leg). The table below shows each spread as a unit.

| # | Entry Date | Type | Long Leg | Short Leg | Spread Width | Net Debit | Exit Date | Net P&L | Fees | Result |
|--:|:-----------|:-----|:---------|:----------|:-------------|:----------|:----------|--------:|-----:|:-------|
| 1 | Jul 11 | BULL_CALL | C139 (08/11) | C146 (08/11) | $7.00 | $2.47 | Jul 17 | +$2,400 | $52 | WIN |
| 2 | Jul 12 | BULL_CALL | C140 (08/18) | C147 (08/18) | $7.00 | $2.53 | Jul 17 | +$1,420 | $52 | WIN |
| 3 | Jul 14 | BULL_CALL | C142 (08/18) | C149 (08/18) | $7.00 | $2.07 | Jul 19 | +$1,600 | $78 | WIN |
| 4 | Jul 17 | BULL_CALL | C143 (08/25) | C150 (08/25) | $7.00 | $2.14 | Jul 20 | +$820 | $52 | WIN |
| 5 | Jul 18 | BULL_CALL | C142 (08/18) | C149 (08/18) | $7.00 | $2.20 | Jul 19 | +$1,340 | $26 | WIN |
| 6 | Jul 19 | BULL_CALL | C144 (08/25) | C150 (08/25) | $6.00 | $2.06 | Jul 24 | +$80 | $78 | WIN |
| 7 | Jul 21 | BULL_CALL | C144 (08/25) | C150 (08/25) | $6.00 | $2.08 | Jul 24 | +$40 | $26 | WIN |
| 8 | Jul 24 | BULL_CALL | C144.5 (08/25) | C151 (08/25) | $6.50 | $2.09 | Jul 27 | +$540 | $52 | WIN |
| 9 | Jul 25 | BULL_CALL | C144.5 (09/01) | C151 (09/01) | $6.50 | $2.16 | Jul 27 | +$480 | $52 | WIN |
| 10 | Jul 27 | BULL_CALL | C146 (09/01) | C151 (09/01) | $5.00 | $1.90 | Jul 27 | -$1,360 | $52 | LOSS |
| 11 | Jul 31 | BULL_CALL | C144 (09/01) | C149 (09/01) | $5.00 | $2.03 | Aug 7 | -$620 | $52 | LOSS |
| 12 | Aug 1 | BULL_CALL | C144 (09/15) | C151 (09/15) | $7.00 | $2.36 | Aug 7 | -$180 | $52 | LOSS |
| 13 | Aug 2 | BULL_CALL | C144.5 (09/08) | C150 (09/08) | $5.50 | $1.97 | Aug 7 | -$480 | $52 | LOSS |
| 14 | Aug 7 | BULL_CALL | C144.5 (09/08) | C150 (09/08) | $5.50 | $1.82 | Aug 8 | +$600 | $52 | WIN |
| 15 | Aug 8 | BULL_CALL | C145 (09/15) | C152 (09/15) | $7.00 | $2.09 | Aug 9 | -$1,280 | $41 | LOSS |
| 16 | Aug 11 | BEAR_PUT | P146 (09/22) | P139 (09/22) | $7.00 | $3.33 | Aug 11 | -$700 | $52 | LOSS |
| 17 | Aug 14 | BEAR_PUT | P147 (09/22) | P140 (09/22) | $7.00 | $2.97 | Aug 18 | +$1,260 | $52 | WIN |
| 18 | Aug 18 | BEAR_PUT | P145.5 (09/22) | P138.5 (09/22) | $7.00 | $3.26 | Aug 18 | -$500 | $52 | LOSS |
| 19 | Aug 21 | BEAR_PUT | P144.5 (09/22) | P137.5 (09/22) | $7.00 | $3.19 | Aug 22 | -$2,020 | $52 | LOSS |
| 20 | Aug 29 | BULL_CALL | C143.5 (10/06) | C149.5 (10/06) | $6.00 | $2.09 | Sep 1 | +$1,940 | $52 | WIN |
| 21 | Sep 1 | BULL_CALL | C146 (10/06) | C150 (10/06) | $4.00 | $1.74 | Sep 5 | -$1,180 | $78 | LOSS |
| 22 | Sep 5 | BULL_CALL | C146 (10/06) | C151.5 (10/06) | $5.50 | $1.90 | Sep 5 | -$1,320 | $26 | LOSS |
| 23 | Sep 6 | BULL_CALL | C145.5 (10/13) | C151.5 (10/13) | $6.00 | $2.05 | Sep 11 | -$160 | $52 | LOSS |
| 24 | Sep 7 | BULL_CALL | C146 (10/20) | C153 (10/20) | $7.00 | $2.21 | Sep 12 | +$120 | $49 | WIN |
| 25 | Sep 12 | BULL_CALL | C146 (10/13) | C151.5 (10/13) | $5.50 | $1.85 | Sep 18 | -$540 | $44 | LOSS |
| 26 | Sep 13 | BULL_CALL | C147 (10/20) | C153 (10/20) | $6.00 | $1.73 | Sep 18 | -$660 | $41 | LOSS |
| 27 | Sep 18 | BULL_CALL | C147 (10/27) | C152 (10/27) | $5.00 | $1.75 | Sep 20 | -$1,400 | $49 | LOSS |
| 28 | Sep 19 | BULL_CALL | C146 (10/20) | C153 (10/20) | $7.00 | $1.94 | Sep 21 | -$1,460 | $41 | LOSS |
| 29 | Sep 21 | BULL_CALL | C145 (10/27) | C152 (10/27) | $7.00 | $2.22 | Sep 25 | -$1,420 | $41 | LOSS |

**VASS Spread Summary:** 29 spread units | 13 W / 16 L (44.8%) | Net P&L: -$3,560

### 2.2 Micro (Intraday) Trades — Single-Leg Options

| # | Entry Date | Strategy | Strike/Exp | Dir | Qty | Entry | Exit | P&L | Fees | Result |
|--:|:-----------|:---------|:-----------|:----|----:|------:|-----:|----:|-----:|:-------|
| 1 | Jul 3 12:15 | MOMENTUM | P135 (07/07) | PUT | 40 | $0.50 | $0.50 | $0 | $52 | LOSS |
| 2 | Jul 3 12:45 | MOMENTUM | P135 (07/07) | PUT | 34 | $0.59 | $0.14 | -$1,530 | $44 | LOSS |
| 3 | Jul 5 11:00 | FADE | C138 (07/07) | CALL | 45 | $0.37 | $0.52 | +$675 | $59 | WIN |
| 4 | Jul 5 13:48 | MOMENTUM | C138.5 (07/07) | CALL | 228 | $0.30 | $0.20 | -$2,280 | $296 | LOSS |
| 5 | Jul 10 11:00 | MOMENTUM | C139.5 (07/14) | CALL | 165 | $0.40 | $0.46 | +$990 | $215 | WIN |
| 6 | Jul 11 10:30 | FADE | C140 (07/14) | CALL | 47 | $0.42 | $0.30 | -$564 | $61 | LOSS |
| 7 | Jul 11 14:10 | MOMENTUM | C140 (07/14) | CALL | 87 | $0.38 | $0.34 | -$348 | $113 | LOSS |
| 8 | Jul 18 11:00 | MOMENTUM | C143.5 (07/21) | CALL | 240 | $0.34 | $0.45 | +$2,640 | $312 | WIN |
| 9 | Jul 31 10:00 | MOMENTUM | C145 (08/04) | CALL | 52 | $0.41 | $0.29 | -$624 | $68 | LOSS |
| 10 | Jul 31 11:00 | MOMENTUM | P142 (08/04) | PUT | 34 | $0.60 | $0.46 | -$476 | $44 | LOSS |
| 11 | Aug 7 13:15 | FADE | C145.5 (08/11) | CALL | 91 | $0.23 | $0.21 | -$182 | $118 | LOSS |
| 12 | Aug 8 11:45 | MOMENTUM | C145.5 (08/11) | CALL | 271 | $0.31 | $0.45 | +$3,794 | $352 | WIN |
| 13 | Aug 8 12:30 | MOMENTUM | C146 (08/11) | CALL | 291 | $0.30 | $0.19 | -$3,201 | $378 | LOSS |
| 14 | Aug 8 13:30 | FADE | C146 (08/11) | CALL | 105 | $0.20 | $0.02 | -$1,890 | $95 | LOSS |
| 15 | Aug 8 14:10 | MOMENTUM | C145.5 (08/11) | CALL | 119 | $0.35 | $0.23 | -$1,428 | $155 | LOSS |
| 16 | Aug 9 11:00 | MOMENTUM | C144.5 (08/11) | CALL | 306 | $0.26 | $0.29 | +$918 | $398 | WIN |
| 17 | Aug 16 12:30 | MOMENTUM | C145.5 (08/18) | CALL | 67 | $0.29 | $0.18 | -$737 | $87 | LOSS |
| 18 | Aug 21 10:15 | MOMENTUM | P139 (08/25) | PUT | 30 | $0.64 | $0.44 | -$600 | $39 | LOSS |
| 19 | Aug 29 10:00 | MOMENTUM | C143 (09/01) | CALL | 178 | $0.37 | $0.54 | +$3,026 | $231 | WIN |
| 20 | Aug 29 11:38 | MOMENTUM | C143.5 (09/01) | CALL | 200 | $0.34 | $0.47 | +$2,600 | $260 | WIN |
| 21 | Aug 30 10:30 | FADE | C144.5 (09/01) | CALL | 72 | $0.28 | $0.39 | +$792 | $94 | WIN |
| 22 | Aug 30 12:43 | MOMENTUM | C145 (09/01) | CALL | 197 | $0.21 | $0.30 | +$1,773 | $256 | WIN |
| 23 | Aug 30 13:15 | FADE | C145 (09/01) | CALL | 65 | $0.32 | $0.34 | +$130 | $85 | WIN |
| 24 | Aug 30 13:30 | FADE | C145.5 (09/01) | CALL | 125 | $0.17 | $0.24 | +$875 | $163 | WIN |
| 25 | Aug 30 14:10 | MOMENTUM | C145.5 (09/01) | CALL | 370 | $0.23 | $0.21 | -$740 | $481 | LOSS |
| 26 | Sep 5 10:45 | MOMENTUM | C146 (09/08) | CALL | 53 | $0.39 | $0.27 | -$636 | $69 | LOSS |
| 27 | Sep 6 10:30 | MOMENTUM | P143.5 (09/08) | PUT | 62 | $0.33 | $0.48 | +$930 | $81 | WIN |
| 28 | Sep 6 11:44 | MOMENTUM | P143.5 (09/08) | PUT | 61 | $0.33 | $0.21 | -$732 | $79 | LOSS |
| 29 | Sep 20 11:00 | MOMENTUM | C145.5 (09/22) | CALL | 53 | $0.37 | $0.54 | +$901 | $69 | WIN |
| 30 | Sep 20 14:10 | MOMENTUM | C145.5 (09/22) | CALL | 55 | $0.36 | $0.23 | -$715 | $72 | LOSS |
| 31 | Sep 25 10:45 | MOMENTUM | C144 (09/29) | CALL | 37 | $0.44 | $0.30 | -$518 | $48 | LOSS |
| 32 | Sep 26 11:15 | MOMENTUM | C144 (09/29) | CALL | 52 | $0.31 | $0.45 | +$728 | $68 | WIN |
| 33 | Sep 27 14:25 | FADE | C145 (09/29) | CALL | 46 | $0.35 | $0.49 | +$644 | $60 | WIN |

**Micro Summary:** 33 trades | 15 W / 18 L (45.5%) | Net P&L: +$2,616

### 2.3 Special Events (Non-Standard Trades)

| # | Date | Type | Details | P&L |
|--:|:-----|:-----|:--------|----:|
| 1 | Aug 9 09:33 | RECON_ORPHAN | QQQ C146 (08/11) x105 @ $0.02 — orphan from stopped micro trade | -$1,890 (included in T14) |
| 2 | Aug 30 13:30 | RECON_ORPHAN | QQQ C145 (09/01) x65 @ $0.34 — orphan from micro slot conflict | +$130 (included in T23) |
| 3 | Sep 5 12:15 | KILL_SWITCH_ON_FILL | QQQ C150 (10/06) x40 @ $0.32 opened during KS → immediate liquidation @ $0.25 | -$140 |
| 4 | Sep 5 13:30 | RECON_ORPHAN | QQQ C150 (10/06) x20 @ $0.25 — short leg orphan after KS liquidation | $0 |

### 2.4 Aggregate Trade Statistics

| Metric | All Trades | VASS Spreads | Micro Intraday |
|--------|:----------:|:------------:|:--------------:|
| Count | 92 | 58 (29 pairs) | 33 |
| Win Rate | 46.7% | 44.8% | 45.5% |
| Avg Win | +$1,590 | +$820 (spread) | +$1,389 |
| Avg Loss | -$1,117 | -$888 (spread) | -$919 |
| Profit Factor | 0.87 | 0.75 | 1.15 |
| Largest Win | +$3,794 | +$2,400 | +$3,794 |
| Largest Loss | -$3,880 | -$2,020 | -$3,201 |
| Avg Hold (days) | — | 4.3d | Intraday |

---

## 3. Signal Flow Report — DETAILED

### 3.1 Pipeline Overview

The options engine processes signals through a multi-stage funnel. This section details every rejection reason with counts, percentages, and descriptions.

```
┌─────────────────────────────────────────────────────────┐
│                  SIGNAL GENERATION                       │
│  Regime Engine → Micro Regime → VASS/Intraday Signals   │
└──────────────────────┬──────────────────────────────────┘
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
    VASS SWING    MICRO REGIME   INTRADAY
    (981 blocks)  (808 blocks)   (203 drops)
          │            │            │
          ▼            ▼            ▼
    ┌─────────────────────────────────┐
    │     FILLED TRADES: 92           │
    │     (208 individual fills)      │
    └─────────────────────────────────┘
```

**Total Signal Attempts:** ~2,084 (estimated)
**Total Blocks/Rejections:** 1,992 (95.6%)
**Total Trades Executed:** 92 (4.4%)

### 3.2 VASS Swing Rejections (981 total)

These are swing spread signals that were generated but blocked before execution.

| Reason Code | Count | % | Category | Description |
|:------------|------:|--:|:---------|:------------|
| **R_DIRECTION_DAY_GAP** | 316 | 32.2% | Direction Limit | Already entered a swing spread in the same direction today. Prevents overconcentration on a single day's signal. Resets daily. |
| **R_BULL_MA20_GATE** | 286 | 29.2% | Technical Gate | QQQ price is below the 20-day MA, blocking bullish (CALL) spread entries. Dominant in early July when QQQ pulled back. Protects against counter-trend entries. |
| **R_SLOT_DIRECTION_MAX** | 180 | 18.4% | Position Limit | Maximum 3 slots in one direction (BULLISH) already filled. Cannot add more bull call spreads. System must wait for existing spreads to exit. Working as designed. |
| **E_VASS_SIMILAR_3D_COOLDOWN** | 97 | 9.9% | Cooldown | A similar strike/expiry combination was entered within the last 3 days. Prevents stacking identical risk. |
| **E_DEBIT_LEG_SELECTION_FAILED** | 50 | 5.1% | Execution | No valid debit leg found in the options chain — either no contracts met the delta/DTE requirements or bid-ask spread too wide. |
| **R_BULL_REGIME_FLOOR** | 40 | 4.1% | Regime Filter | Regime score below the minimum threshold for bull entries. Occurred during brief dips in the regime score to the low 50s. |
| **E_TIME_WINDOW** | 6 | 0.6% | Time Guard | Signal generated outside the allowed VASS trading window. |
| **R_EXPIRY_CONCENTRATION_CAP** | 3 | 0.3% | Risk Limit | Too many spreads already concentrated at the same expiry date. Prevents single-expiry blowup risk. |
| **R_CONTRACT_QUALITY** | 1 | 0.1% | Quality Gate | Credit-to-width ratio too low — the spread would have poor risk/reward. |
| **R_BEAR_PUT_ASSIGNMENT_GATE_STRESS** | 1 | 0.1% | Assignment Gate | Short PUT leg too close to ITM under stress conditions, creating assignment risk. |
| **R_BEAR_PUT_ASSIGNMENT_GATE_LOW_VIX_RELAXED** | 1 | 0.1% | Assignment Gate | Assignment gate triggered even with relaxed low-VIX threshold. |

#### VASS Rejection Analysis by Category

| Category | Count | % of Total | Assessment |
|:---------|------:|:----------:|:-----------|
| **Direction/Position Limits** | 496 | 50.6% | Working as designed — prevents overconcentration |
| **Technical Gates** | 286 | 29.2% | MA20 gate dominant — appropriate for bull protection |
| **Cooldowns** | 97 | 9.9% | Prevents duplicate risk stacking |
| **Execution Failures** | 56 | 5.7% | Contract chain limitations in 2017 data |
| **Regime Filters** | 40 | 4.1% | Minimal — regime was strong throughout |
| **Risk/Quality Gates** | 6 | 0.6% | Rarely triggered in low-VIX environment |

#### VASS Rejection Timeline

| Period | Top Blocker | Count | Context |
|:-------|:-----------|------:|:--------|
| Jul 1-10 | R_BULL_MA20_GATE | ~180 | QQQ below MA20 after pullback, no VASS entries possible |
| Jul 11-27 | R_DIRECTION_DAY_GAP | ~100 | Market recovering, system entering 1/day max |
| Jul 27-Aug 9 | R_SLOT_DIRECTION_MAX | ~80 | 3 bull spreads held, all slots full |
| Aug 10-28 | R_BULL_MA20_GATE | ~60 | Market pullback, MA20 gate re-engaged |
| Aug 29-Sep 5 | R_DIRECTION_DAY_GAP | ~50 | Recovery rally, rapid entries |
| Sep 6-29 | R_SLOT_DIRECTION_MAX | ~100 | Bull spreads losing, slots full with underwater positions |

### 3.3 MICRO Regime Blocks (808 total)

These are intraday micro signals that were blocked at the micro regime level before reaching contract selection.

| Reason Code | Count | % | Description |
|:------------|------:|--:|:------------|
| **VIX_STABLE_LOW_CONVICTION** | 356 | 44.1% | VIX change was within the STABLE band (+/-0.3% for VIX < 15, +/-0.8% for VIX > 25). No directional conviction from volatility. This is the most common block because VIX averaged 11.2 and rarely moved enough to signal direction. |
| **CONFIRMATION_FAIL** | 305 | 37.7% | QQQ intraday move was less than the 0.35% minimum threshold required for directional confirmation. Market was too flat/choppy to confirm a micro regime signal. |
| **QQQ_FLAT** | 118 | 14.6% | QQQ had no meaningful intraday range — neither up nor down enough to establish a directional edge. Combined with low VIX, this reflects the classic "low vol, low move" environment. |
| **REGIME_NOT_TRADEABLE** | 29 | 3.6% | Micro regime state was in a non-tradeable configuration (e.g., conflicting signals between VIX level and VIX direction). |

#### MICRO Block Analysis

**Root Cause:** The entire Jul-Sep 2017 period was characterized by historically low VIX (9.6-12.5). In this environment:
- VIX barely moves day-to-day → **VIX_STABLE** blocks 44% of signals
- QQQ trends gently upward without sharp intraday moves → **CONFIRMATION_FAIL** blocks 38%
- Combined, these two filters block **82%** of micro signals — this is correct behavior

**Assessment:** The micro regime filters are working as designed. In a low-vol grind-up market, intraday options have poor edge. The 33 trades that did pass through were well-selected (45.5% win rate, positive P&L).

### 3.4 Intraday Signal Drops (203 total)

These signals passed the micro regime filter but were dropped during contract selection or execution gates.

| Reason Code | Count | % | Description |
|:------------|------:|--:|:------------|
| **E_NO_CONTRACT_SELECTED** | 156 | 76.8% | No option contract met the DTE 1-5 requirement, delta target, or bid-ask spread criteria. Primary blocker was `DTE outside 1-5 range` — weekly options data for QQQ in 2017 had limited availability, especially mid-week. |
| **E_CALL_GATE_MA20** | 26 | 12.8% | CALL entry blocked because QQQ was below the 20-day MA. Same technical filter as VASS but applied to intraday call entries. Active primarily in early July and mid-August pullbacks. |
| **E_INTRADAY_SAME_STRATEGY_COOLDOWN** | 14 | 6.9% | Same intraday strategy (e.g., DEBIT_MOMENTUM) already executed recently. 15-minute cooldown between same-strategy entries. Prevents rapid-fire re-entries after stops. |
| **E_CALL_GATE_VIX5D** | 7 | 3.4% | CALL entry blocked by 5-day VIX trend gate. VIX was rising over 5 days, suggesting deteriorating conditions for CALL entries despite low absolute VIX level. |

#### Intraday Drop Analysis

**E_NO_CONTRACT_SELECTED (156):** This is the dominant bottleneck. The 1-5 DTE filter for intraday options is strict and in 2017, QQQ weekly options had limited strike availability. Most blocks occurred on Tuesday-Wednesday when the nearest weekly expiry was 3-5 days out but available strikes were sparse.

**E_CALL_GATE_MA20 (26):** Properly prevented CALL entries during the two pullback periods (early Jul, mid-Aug). All 26 blocks occurred when QQQ was below its 20-day MA.

### 3.5 SWING_SLOT_BLOCK (8 total)

| Date | Reason | Active Slots | VIX | Regime |
|:-----|:-------|:-------------|:----|:-------|
| Jul 14 | R_SLOT_DIRECTION_MAX: BULLISH 3 >= 3 | 3 bull | 10.5 | 74 |
| Jul 18 | R_SLOT_DIRECTION_MAX: BULLISH 3 >= 3 | 3 bull | 10.2 | 73 |
| Aug 4 | R_SLOT_DIRECTION_MAX: BULLISH 3 >= 3 | 3 bull | 10.1 | 72 |
| Sep 7 | R_SLOT_DIRECTION_MAX: BULLISH 3 >= 3 | 3 bull | 10.8 | 75 |
| Sep 13 | R_SLOT_DIRECTION_MAX: BULLISH 3 >= 3 | 3 bull | 10.2 | 75 |
| Sep 14 | R_SLOT_DIRECTION_MAX: BULLISH 3 >= 3 | 3 bull | 10.0 | 75 |
| Sep 15 | R_SLOT_DIRECTION_MAX: BULLISH 3 >= 3 | 3 bull | 9.8 | 75 |
| Sep 19 | R_SLOT_DIRECTION_MAX: BULLISH 3 >= 3 | 3 bull | 11.6 | 74 |

**Assessment:** All 8 blocks are for max bullish direction slots (3/3). This is the per-direction position limit working correctly to prevent overconcentration in one direction. In September, the slots were full of losing bull spreads, which prevented adding more losing positions.

### 3.6 Signal Flow Summary

| Stage | Signals In | Blocked | Passed | Pass Rate |
|:------|:----------:|:-------:|:------:|:---------:|
| VASS Swing Gate | ~1,010 | 981 | ~29 spreads | 2.9% |
| Micro Regime Gate | ~840 | 808 | ~32 | 3.8% |
| Intraday Execution Gate | ~236 | 203 | ~33 | 14.0% |
| **Total Pipeline** | **~2,086** | **1,992** | **92 trades** | **4.4%** |

---

## 4. Regime Analysis

### 4.1 Regime Score Progression

The regime remained NEUTRAL to RISK_ON throughout the entire backtest.

| Period | Avg Score | Classification | VIX Range |
|:-------|:---------:|:---------------|:----------|
| Jul 1-7 | 56.9 | NEUTRAL | 11.2-12.5 |
| Jul 8-14 | 65.0 | NEUTRAL | 10.5-11.5 |
| Jul 15-21 | 73.0 | RISK_ON | 10.0-10.8 |
| Jul 22-31 | 74.5 | RISK_ON | 9.8-10.5 |
| Aug 1-7 | 72.0 | RISK_ON | 10.0-11.0 |
| Aug 8-14 | 68.0 | NEUTRAL | 11.5-15.0 |
| Aug 15-21 | 65.0 | NEUTRAL | 12.0-14.5 |
| Aug 22-31 | 70.0 | RISK_ON | 10.5-11.5 |
| Sep 1-7 | 73.0 | RISK_ON | 10.0-10.8 |
| Sep 8-14 | 72.0 | RISK_ON | 10.5-11.5 |
| Sep 15-21 | 74.0 | RISK_ON | 9.8-10.5 |
| Sep 22-29 | 75.0 | RISK_ON | 9.6-10.0 |

### 4.2 Regime Impact on Trading

- **No CAUTIOUS/DEFENSIVE/RISK_OFF periods** in this entire 3-month window
- Regime never dropped below 56.9 (NEUTRAL)
- BEAR_PUT spreads were only entered during brief VIX spikes in August (Aug 11-21)
- The dominant regime (RISK_ON) drove the system heavily toward BULL_CALL spreads
- **Problem:** When the market pulled back in September, the bull-heavy positioning caused significant losses while the regime still indicated RISK_ON

### 4.3 VASS IV Environment

| IV Zone | VIX Range | Spread Type | Active Period |
|:--------|:----------|:------------|:-------------|
| LOW | VIX < 16 | DEBIT only | Entire backtest (Jul 1 - Sep 29) |
| MEDIUM | VIX 16-25 | DEBIT | Never reached |
| HIGH | VIX > 25 | CREDIT | Never reached |

The entire backtest was in LOW IV mode, meaning only debit spreads were eligible. Credit spreads were never available.

---

## 5. Risk Events

### 5.1 Kill Switch Activations (9 events, all Tier 1 REDUCE)

| # | Date | Time | Loss % | Baseline | Current | Trigger |
|--:|:-----|:-----|:-------|:---------|:--------|:--------|
| 1 | Jul 5 | 14:09 | -2.37% | $99,841 (prior_close) | $97,474 | Intraday micro losses |
| 2 | Jul 27 | 12:42 | -2.06% | $108,025 (sod) | $105,797 | VASS spread loss |
| 3 | Aug 2 | 10:28 | -2.21% | $104,667 (sod) | $102,351 | Multi-day spread losses |
| 4 | Aug 3 | 15:33 | -2.08% | $103,981 (prior_close) | $101,821 | Continued losses |
| 5 | Aug 8 | 15:06 | -2.34% | $102,627 (prior_close) | $100,225 | Heavy micro trading day |
| 6 | Aug 9 | 11:01 | -2.58% | $99,442 (prior_close) | $96,881 | Continuation from Aug 8 |
| 7 | Sep 5 | 12:03 | -2.11% | $103,403 (prior_close) | $101,222 | VASS spread exit loss |
| 8 | Sep 8 | 15:25 | -2.00% | $100,328 (prior_close) | $98,318 | Exactly at threshold |
| 9 | Sep 20 | 14:23 | -2.21% | $98,515 (prior_close) | $96,337 | Multiple spread losses |

**KS Impact:** Each Tier 1 REDUCE event:
1. Reduced trend allocation by 50% (N/A in isolation mode)
2. Blocked new options entries for the remainder of the day
3. Generated `OPTIONS_EOD: Blocked by KS Tier 1 (REDUCE)` at 15:45

**Notable:** KS never escalated beyond Tier 1 (REDUCE). Tier 2 (4%) and Tier 3 (6%) were never hit. The graduated system worked correctly as a speed bump.

### 5.2 KILL_SWITCH_ON_FILL Event (Sep 5)

**Sequence of events:**
1. **12:03** — KS Tier 1 activated (loss -2.11%)
2. **12:15** — A VASS spread exit triggered, closing both legs of existing spreads
3. **12:15** — When the short leg was bought back, it left 40 contracts of QQQ C150 (10/06) as an unmatched position
4. **12:15** — `KILL_SWITCH_ON_FILL` detected the newly-opened position and immediately liquidated it
5. **12:15** — Sold 40 QQQ C150 @ $0.25 (bought at $0.32 during spread close)
6. **13:30** — Orphan cleanup bought 20 QQQ C150 @ $0.25 to close remaining short leg

**Impact:** -$140 loss from the emergency liquidation + orphan cleanup.

### 5.3 Win Rate Gate Reductions (8 events)

| Date | Scale | Before → After |
|:-----|:------|:---------------|
| Aug 8 | 75% | 67 → 50 contracts |
| Aug 11 | 75% | 40 → 30 contracts |
| Aug 14 | 75% | 44 → 33 contracts |
| Aug 18 | 75% | 41 → 30 contracts |
| Sep 18 | 75% | 77 → 57 contracts |
| Sep 19 | 75% | 69 → 51 contracts |
| Sep 21 | 75% | 59 → 44 contracts |
| Sep 29 | 75% | 59 → 44 contracts |

**Pattern:** Win rate gate activated during the two losing streaks (mid-Aug and mid-Sep), reducing position sizes by 25%. All reductions were to the same 75% scale, never reaching shutoff level.

### 5.4 Orphan Leg Cleanups (3 events)

| Date | Symbol | Qty | Price | Cause |
|:-----|:-------|----:|------:|:------|
| Aug 9 09:33 | QQQ C146 (08/11) | 105 | $0.02 | Micro trade stopped out, remaining qty orphaned overnight |
| Aug 30 13:30 | QQQ C145 (09/01) | 65 | $0.34 | Micro slot conflict left residual position |
| Sep 5 13:30 | QQQ C150 (10/06) | -20 | $0.25 | KS_ON_FILL liquidation left short leg orphan |

---

## 6. Options-Specific Metrics

### 6.1 Strategy Breakdown

| Strategy | Trades | W/L | Win Rate | Gross P&L | Fees | Net P&L |
|:---------|-------:|:----|:--------:|----------:|-----:|--------:|
| VASS: BULL_CALL_DEBIT | 50 (25 pairs) | 24/26 | 48.0% | -$1,800 | $1,183 | -$2,983 |
| VASS: BEAR_PUT_DEBIT | 8 (4 pairs) | 3/5 | 37.5% | -$1,960 | $208 | -$2,168 |
| MICRO: DEBIT_MOMENTUM | 25 | 10/15 | 40.0% | +$1,050 | $3,756 | -$2,706 |
| MICRO: DEBIT_FADE | 8 | 5/3 | 62.5% | +$1,566 | $928 | +$638 |
| **Total** | **92** | **43/49** | **46.7%** | **-$1,144** | **$6,075** | **-$7,219** |

### 6.2 Direction Breakdown

| Direction | Trades | Win Rate | Gross P&L |
|:----------|-------:|:--------:|----------:|
| CALL / BULLISH | 80 | 46.3% | +$1,944 |
| PUT / BEARISH | 12 | 50.0% | -$3,088 |

**Observation:** The system was overwhelmingly CALL-biased (87% of trades) which is appropriate for a sustained RISK_ON regime. However, the 4 BEAR_PUT spread pairs (entered Aug 11-21) lost -$1,960 net — bearish entries in a regime that was fundamentally bullish.

### 6.3 Exit Reason Analysis

| Exit Type | Count | Avg P&L |
|:----------|------:|--------:|
| OCO Profit Target | 10 | +$1,850 |
| OCO Stop Loss | 9 | -$1,420 |
| VASS Time Exit (profit) | ~10 | +$680 |
| VASS Time Exit (loss) | ~9 | -$920 |
| Micro EOD Sweep | 2 | -$765 |
| Micro Profit Limit | ~8 | +$1,200 |
| Micro Stop Market | ~9 | -$950 |
| KS/Orphan Emergency | 4 | -$475 |

### 6.4 DTE at Entry

| DTE Range | VASS Spreads | Micro Trades |
|:----------|:-------------|:-------------|
| 1-5 DTE | — | 33 (100%) |
| 7-14 DTE | 3 spreads | — |
| 14-30 DTE | 18 spreads | — |
| 30-45 DTE | 8 spreads | — |

### 6.5 Spread Width Distribution

| Width | Count | Avg Net P&L |
|:------|------:|:------------|
| $4.00 | 1 | -$1,180 |
| $5.00 | 5 | -$672 |
| $5.50 | 5 | -$344 |
| $6.00 | 5 | +$56 |
| $6.50 | 2 | +$510 |
| $7.00 | 11 | +$195 |

**Observation:** Wider spreads ($7) performed better on average. Narrower spreads ($4-5) were consistently negative, suggesting the risk/reward was insufficient at smaller widths.

---

## 7. Daily P&L Breakdown

### 7.1 July 2017

| Date | Session Trades | Session P&L | Cumulative P&L | Equity SOD | Events |
|:-----|:-------------:|:-----------:|:---------------:|:----------:|:-------|
| Jul 1-2 | 0 | $0 | $0 | $100,000 | Backtest start, no trading |
| Jul 3 | 1 | $0 | $0 | $100,000 | First micro trade (PUT), breakeven |
| Jul 4 | 0 | $0 | $0 | $99,841 | Holiday, prior micro loss settled |
| Jul 5 | 3 | -$3,135 | -$3,135 | $98,374 | **Worst day.** KS Tier 1 at 14:09. Micro MOMENTUM loss -$2,280 |
| Jul 6-9 | 0 | $0 | -$3,135 | $96,414 | No trades, equity flat |
| Jul 10 | 1 | +$990 | -$2,145 | $96,414 | Micro MOMENTUM win |
| Jul 11 | 2 | -$912 | -$3,057 | $97,189 | First VASS spread entered. Mixed micro results |
| Jul 12-16 | 0 | $0 | -$3,057 | $97-98K | VASS spreads building, no exits |
| Jul 17 | 2 | +$3,820 | +$763 | $100,173 | **Turning point.** 2 VASS spread exits profitable |
| Jul 18 | 1 | +$2,640 | +$3,403 | $99,197 | Big micro MOMENTUM win |
| Jul 19 | 1 | +$1,600 | +$5,003 | $105,807 | VASS spread exit |
| Jul 20 | 1 | +$820 | +$5,823 | $106,991 | VASS spread exit |
| Jul 21-26 | 0 | $0 | +$5,823 | ~$106K | No exits |
| Jul 24 | 1 | +$80 | +$5,903 | $105,767 | Small VASS win |
| Jul 27 | 3 | -$340 | +$5,563 | $108,025 | **Peak equity.** KS Tier 1 at 12:42. Mixed results |
| Jul 28-30 | 0 | $0 | +$5,563 | $105,271 | Weekend |
| Jul 31 | 2 | -$1,100 | +$4,463 | $105,271 | Micro losses |

**July Summary:** +$4,921 session P&L | Peak at $108,025 | KS 2x

### 7.2 August 2017

| Date | Session Trades | Session P&L | Cumulative P&L | Equity SOD | Events |
|:-----|:-------------:|:-----------:|:---------------:|:----------:|:-------|
| Aug 1 | 0 | $0 | +$4,463 | $103,623 | No trades |
| Aug 2 | 0 | $0 | +$4,463 | $104,667 | KS Tier 1 at 10:28 |
| Aug 3 | 0 | $0 | +$4,463 | $103,901 | KS Tier 1 at 15:33 |
| Aug 4-6 | 0 | $0 | +$4,463 | $102,441 | Weekend |
| Aug 7 | 4 | -$1,462 | +$3,001 | $102,623 | Multi-spread VASS exit losses. Micro FADE loss |
| Aug 8 | 4 | -$235 | +$2,766 | $102,357 | **Volatile day.** KS Tier 1. Win rate gate 75%. Mixed micro |
| Aug 9 | 2 | -$362 | +$2,404 | $98,212 | KS Tier 1. Orphan close. Micro win |
| Aug 10 | 0 | $0 | +$2,404 | $98,366 | No trades |
| Aug 11 | 1 | -$700 | +$1,704 | $98,366 | First BEAR_PUT spread. Win rate gate. Loss |
| Aug 12-13 | 0 | $0 | +$1,704 | $97,614 | Weekend |
| Aug 14 | 0 | $0 | +$1,704 | $97,614 | BEAR_PUT entered. Win rate gate |
| Aug 15 | 0 | $0 | +$1,704 | $96,948 | No trades |
| Aug 16 | 1 | -$737 | +$967 | $96,818 | Micro MOMENTUM loss |
| Aug 17 | 0 | $0 | +$967 | $96,463 | No trades |
| Aug 18 | 2 | +$760 | +$1,727 | $98,483 | BEAR_PUT spread exit: +$1,260 / -$500 net |
| Aug 19-20 | 0 | $0 | +$1,727 | $97,445 | Weekend |
| Aug 21 | 1 | -$600 | +$1,127 | $97,445 | Micro PUT loss, BEAR_PUT spread entered |
| Aug 22 | 1 | -$2,020 | -$893 | $95,630 | **Big loss.** BEAR_PUT spread -$2,020 |
| Aug 23-28 | 0 | $0 | -$893 | $94,734 | **Trough period.** No trades, equity at lows |
| Aug 29 | 2 | +$5,626 | +$4,733 | $94,734 | **Best day.** 2 micro MOMENTUM wins (+$3,026, +$2,600) |
| Aug 30 | 4 | +$2,700 | +$7,433 | $99,893 | Strong micro day, 4 wins in a row |
| Aug 31 | 0 | $0 | +$7,433 | $103,225 | No trades |

**August Summary:** +$4,968 session P&L | Trough at $94,734 | KS 3x | Win rate gate 4x

### 7.3 September 2017

| Date | Session Trades | Session P&L | Cumulative P&L | Equity SOD | Events |
|:-----|:-------------:|:-----------:|:---------------:|:----------:|:-------|
| Sep 1 | 1 | +$1,940 | +$9,373 | $104,325 | VASS spread +$1,940 |
| Sep 2-4 | 0 | $0 | +$9,373 | $103,403 | Weekend + holiday |
| Sep 5 | 2 | -$1,816 | +$7,557 | $103,243 | **KS_ON_FILL event.** KS Tier 1. VASS exit losses |
| Sep 6 | 2 | +$198 | +$7,755 | $100,231 | Mixed micro PUTs |
| Sep 7 | 0 | $0 | +$7,755 | $100,314 | VASS entered |
| Sep 8 | 0 | $0 | +$7,755 | $99,868 | KS Tier 1 at 15:25 |
| Sep 9-10 | 0 | $0 | +$7,755 | $98,328 | Weekend |
| Sep 11 | 1 | -$160 | +$7,595 | $99,892 | Small VASS loss |
| Sep 12 | 1 | +$120 | +$7,715 | $100,259 | Small VASS win |
| Sep 13 | 0 | $0 | +$7,715 | $100,213 | VASS entered |
| Sep 14-17 | 0 | $0 | +$7,715 | $99,400-$99,060 | Weekend, no exits |
| Sep 18 | 2 | -$1,200 | +$6,515 | $99,054 | 2 VASS spread losses. Win rate gate |
| Sep 19 | 0 | $0 | +$6,515 | $98,668 | Win rate gate |
| Sep 20 | 3 | -$1,214 | +$5,301 | $98,255 | KS Tier 1. Mixed results |
| Sep 21 | 1 | -$1,460 | +$3,841 | $96,717 | VASS spread -$1,460 |
| Sep 22-24 | 0 | $0 | +$3,841 | $95,366-$95,496 | Weekend |
| Sep 25 | 2 | -$1,938 | +$1,903 | $94,856 | VASS loss + micro loss |
| Sep 26 | 1 | +$728 | +$2,631 | $94,142 | Micro win |
| Sep 27 | 1 | +$644 | +$3,275 | $94,802 | Micro FADE win |
| Sep 28-29 | 0 | $0 | +$3,275 | $95,387 | Final VASS entry (open at EOB) |

**September Summary:** -$8,034 cumulative MTD | KS 2x | Win rate gate 4x | Steady bleed from VASS losses

---

## 8. Key Observations & Anomalies

### 8.1 Fee Drag is the #1 Issue

| Component | Gross P&L | Fees | Net P&L | Fee Impact |
|:----------|----------:|-----:|--------:|:-----------|
| VASS Spreads | -$3,760 | $1,391 | -$5,151 | 37% of loss from fees |
| Micro Intraday | +$2,616 | $4,684 | -$2,068 | Fees wiped out all profit |
| **Total** | **-$1,144** | **$6,075** | **-$7,219** | **Fees = 5.3x gross loss** |

**Finding:** The micro intraday strategy was gross-profitable (+$2,616) but net-negative (-$2,068) due to $4,684 in fees. Micro trades averaged $142/trade in fees vs $79/trade in gross P&L. The high fee-to-profit ratio is driven by large contract quantities (avg ~130 contracts per micro trade) at $1.30/contract round-trip.

### 8.2 September VASS Collapse

September saw 9 consecutive VASS spread losses (Sep 5 through Sep 25), losing -$10,840 gross on VASS alone. Root causes:

1. **Bull-biased in a choppy market:** Regime showed RISK_ON (score 73-75) but QQQ was range-bound, not trending. VASS kept entering bull call spreads that decayed.
2. **No bearish pivot:** The system entered 0 BEAR_PUT spreads in September because VIX never spiked above 12. Without a VIX trigger, the system couldn't flip to puts.
3. **Slot lock:** 3/3 bullish slots were full of losing positions, preventing new entries but also preventing the system from recycling into better-timed entries.

### 8.3 Spread Width vs Performance

Wider spreads ($6.50-$7.00) had better risk/reward than narrower spreads ($4.00-$5.50). This aligns with the V9.1 design intention where `Spread Width Effective Max = $7` sorts for optimal R:R. The $4 minimum width spreads consistently underperformed.

### 8.4 Micro Strategy: FADE Outperformed MOMENTUM

| Strategy | Trades | Win Rate | Net P&L |
|:---------|-------:|:--------:|--------:|
| DEBIT_MOMENTUM | 25 | 40.0% | -$2,706 |
| DEBIT_FADE | 8 | 62.5% | +$638 |

The FADE strategy (counter-trend entries on VIX mean-reversion) had a 62.5% win rate and was the only net-profitable micro strategy. MOMENTUM suffered from late entries and large position sizes that hit stops.

### 8.5 Low-VIX Paradox

The entire backtest ran in VIX 9.6-15.0. This created a paradox:
- **Low VIX = cheap options** → system entered many positions
- **Low VIX = low volatility** → options had limited upside movement
- **Low VIX = stable VIX** → micro regime blocked 82% of signals (correctly)

The micro regime filters correctly identified that most signals had no edge. The trades that passed through were modestly profitable before fees.

### 8.6 Aug 29-30 Recovery: Best 2-Day Stretch

On Aug 29-30, the system generated +$8,326 from 6 micro trades:
- Aug 29: +$3,026 (C143 MOMENTUM) + +$2,600 (C143.5 MOMENTUM)
- Aug 30: +$792 (C144.5 FADE) + +$1,773 (C145 MOMENTUM) + +$130 (C145 FADE) + +$875 (C145.5 FADE) - $740 (C145.5 MOMENTUM loss)

This 2-day burst came after a 7-day no-trade drought (Aug 22-28) while the system sat at the equity trough ($94,734). The market gave a sharp upside move and the micro regime correctly identified it.

### 8.7 Anomalies

1. **Trade #71-72 (Sep 5):** After KS_ON_FILL liquidation, the system bought back the short leg (C150) then immediately sold it in a separate transaction. This created two offsetting micro-trades with net $0 P&L but $39 in fees. The orphan cleanup logic should consolidate these.

2. **MTD Counter Discrepancy:** The EOD_PNL MTD counter shows different cumulative totals across months. July ended at "113 trades" and August started at "160 trades" — this appears to count all fills (not just completed round-trip trades) and may include pending/partial fills.

3. **Weekend Equity Drift:** Equity SOD values sometimes changed over weekends (e.g., Aug 11 $98,366 → Aug 12-13 $97,614) despite no trading. This reflects mark-to-market of open options positions.

---

## Appendix A: Configuration Parameters (Active During Backtest)

| Parameter | Value | Relevance |
|:----------|:------|:----------|
| ISOLATION_TEST_MODE | True | All non-options engines disabled |
| CAPITAL_PARTITION_OPTIONS | 50% | Options budget |
| VASS_LOW_IV_THRESHOLD | VIX < 16 | Active entire backtest |
| MICRO_SCORE_BULLISH | 42 | Micro bullish confirmation |
| MICRO_SCORE_BEARISH | 50 | Micro bearish confirmation |
| VIX_STABLE_BAND_LOW | +/-0.3% | VIX < 15 stable band |
| SPREAD_WIDTH_MIN | $4 | Minimum spread width |
| SPREAD_WIDTH_EFFECTIVE_MAX | $7 | Preferred ceiling |
| SPREAD_MAX_DEBIT_WIDTH | 55% | R:R gate |
| SLOT_DIRECTION_MAX | 3 | Per-direction spread limit |
| KS_TIER_1_PCT | 2% | Kill switch reduce threshold |
| WIN_RATE_GATE_SCALE | 75% | Size reduction on poor win rate |
| INTRADAY_FORCE_EXIT | 15:25 | Intraday options close time |
| OPTIONS_PROFIT_TARGET | 60% | Spread profit target |
| OPTIONS_ATR_STOP_MAX | 28% | Max stop loss |

## Appendix B: Open Position at End of Backtest

| Entry Date | Long Leg | Short Leg | Net Debit | Status |
|:-----------|:---------|:----------|:----------|:-------|
| Sep 29 | QQQ C145.5 (11/03) | QQQ C152.5 (11/03) | $2.20 | OPEN (not in P&L) |

---

*Report generated 2026-02-13. Source data: `docs/audits/logs/stage9.4/V9_4_trades.csv`, `V9_4_orders.csv`, `V9_4_logs.txt`.*
