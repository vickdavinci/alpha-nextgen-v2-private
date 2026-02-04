# Micro Regime Engine - Sniper Logic V2.3.15

> **Purpose:** Define the decision tree for intraday 0DTE options trading
>
> **Philosophy:** "Sniper, not Machine Gunner" - Wait for high-conviction setups, filter noise
>
> **Last Updated:** 2026-02-01

---

## Executive Summary

The Sniper Logic uses a **4-gate system** to filter out noise and only fire on high-probability setups:

| Gate | Purpose | Threshold |
|------|---------|-----------|
| Gate 0 | Pre-flight checks | Position, trades, time window |
| Gate 1 | Noise filter | QQQ move >= 0.35% |
| Gate 2 | VIX context | Direction determines strategy |
| Gate 3 | Strategy qualification | FADE >= 0.50%, MOMENTUM >= 0.80% |
| Gate 4 | Contract selection | DTE, delta, OI, spread |

---

## Configuration (V2.3.15)

```python
# Gate 1: Noise Filter
QQQ_NOISE_THRESHOLD = 0.35  # Minimum QQQ move to consider trading

# Gate 3a: FADE Strategy
FADE_MIN_MOVE = 0.50           # Minimum move for mean reversion
MICRO_SCORE_MODERATE = 45      # Minimum regime score
FADE_TIME_START = "10:30"      # Entry window start
FADE_TIME_END = "14:00"        # Entry window end

# Gate 3b: MOMENTUM Strategy
MOMENTUM_MIN_MOVE = 0.80       # Minimum move for trend following
INTRADAY_ITM_MIN_VIX = 11.5    # VIX must show some fear
MOMENTUM_TIME_START = "10:00"  # Entry window start
MOMENTUM_TIME_END = "13:30"    # Entry window end

# Trade Management
INTRADAY_MAX_TRADES_PER_DAY = 2  # Sniper gets one retry
OPTIONS_0DTE_STOP_PCT = 0.15     # 15% stop loss
OPTIONS_PROFIT_TARGET_PCT = 0.50 # 50% profit target
```

---

## Decision Tree Flowchart

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         SNIPER ENTRY DECISION TREE                          │
│                    (Evaluated every 15 minutes intraday)                    │
└─────────────────────────────────────────────────────────────────────────────┘

                              ┌──────────────┐
                              │    START     │
                              │  (15-min bar)│
                              └──────┬───────┘
                                     │
                    ┌────────────────▼────────────────┐
                    │  GATE 0: PRE-FLIGHT CHECKS      │
                    │  ─────────────────────────────  │
                    │  • Already have position?       │
                    │  • Trades today >= MAX (2)?     │
                    │  • Time window valid?           │
                    │  • Cold start period?           │
                    └────────────────┬────────────────┘
                                     │
                         ┌───────────▼───────────┐
                         │  Any check fails?     │
                         └───────────┬───────────┘
                                     │
                    ┌────────YES─────┴─────NO─────┐
                    │                             │
                    ▼                             ▼
           ┌──────────────┐            ┌──────────────────┐
           │   BLOCKED    │            │  GATE 1: NOISE   │
           │  (No Trade)  │            │     FILTER       │
           └──────────────┘            └────────┬─────────┘
                                                │
                              ┌─────────────────▼─────────────────┐
                              │      |QQQ Move| >= 0.35% ?        │
                              └─────────────────┬─────────────────┘
                                                │
                           ┌─────────NO─────────┴─────────YES─────────┐
                           │                                          │
                           ▼                                          ▼
                  ┌─────────────────┐                    ┌─────────────────────┐
                  │   NO TRADE      │                    │   GATE 2: VIX       │
                  │   "Market       │                    │   DIRECTION         │
                  │    Noise"       │                    └──────────┬──────────┘
                  └─────────────────┘                               │
                                                                    │
                         ┌──────────────────────────────────────────┼──────────────────────────────────────────┐
                         │                                          │                                          │
                         ▼                                          ▼                                          ▼
              ┌─────────────────────┐                 ┌─────────────────────┐                    ┌─────────────────────┐
              │  VIX WHIPSAW        │                 │  VIX FALLING or     │                    │  VIX RISING or      │
              │  (Direction Unclear)│                 │  VIX STABLE         │                    │  VIX SPIKING        │
              └──────────┬──────────┘                 └──────────┬──────────┘                    └──────────┬──────────┘
                         │                                       │                                          │
                         ▼                                       ▼                                          ▼
              ┌─────────────────────┐                 ┌─────────────────────┐                    ┌─────────────────────┐
              │   NO TRADE          │                 │  GATE 3a: FADE      │                    │  GATE 3b: MOMENTUM  │
              │   "Direction        │                 │  QUALIFICATION      │                    │  QUALIFICATION      │
              │    Unclear"         │                 │  ─────────────────  │                    │  ─────────────────  │
              └─────────────────────┘                 │  • Regime OK?       │                    │  • Regime OK?       │
                                                      │  • Score >= 45?     │                    │  • VIX > 11.5?      │
                                                      │  • |Move| >= 0.50%? │                    │  • |Move| >= 0.80%? │
                                                      └──────────┬──────────┘                    └──────────┬──────────┘
                                                                 │                                          │
                                                      ┌──────────▼──────────┐                    ┌──────────▼──────────┐
                                                      │  All checks pass?   │                    │  All checks pass?   │
                                                      └──────────┬──────────┘                    └──────────┬──────────┘
                                                                 │                                          │
                                          ┌──────────NO──────────┴──────YES──────────┐       ┌──────NO──────┴──────YES──────┐
                                          │                                          │       │                              │
                                          ▼                                          ▼       ▼                              ▼
                               ┌─────────────────┐                        ┌─────────────────┐              ┌─────────────────────────┐
                               │   NO TRADE      │                        │  DEBIT FADE     │              │   ITM MOMENTUM          │
                               │   "No Edge"     │                        │  STRATEGY       │              │   STRATEGY              │
                               └─────────────────┘                        └────────┬────────┘              └───────────┬─────────────┘
                                                                                   │                                   │
                                                                                   ▼                                   ▼
                                                                    ┌──────────────────────────┐         ┌──────────────────────────┐
                                                                    │  DIRECTION (Counter)     │         │  DIRECTION (Follow)      │
                                                                    │  ──────────────────────  │         │  ──────────────────────  │
                                                                    │  QQQ UP   → Buy PUT      │         │  QQQ UP   → Buy CALL     │
                                                                    │  QQQ DOWN → Buy CALL     │         │  QQQ DOWN → Buy PUT      │
                                                                    └────────────┬─────────────┘         └────────────┬─────────────┘
                                                                                 │                                    │
                                                                                 └──────────────┬─────────────────────┘
                                                                                                │
                                                                                                ▼
                                                                               ┌────────────────────────────┐
                                                                               │   GATE 4: CONTRACT         │
                                                                               │   SELECTION                │
                                                                               │   ────────────────────     │
                                                                               │   • DTE: 0-1 (true 0DTE)   │
                                                                               │   • Delta: ~0.30 target    │
                                                                               │   • OI >= 100              │
                                                                               │   • Spread <= 25%          │
                                                                               └─────────────┬──────────────┘
                                                                                             │
                                                                               ┌─────────────▼──────────────┐
                                                                               │   Valid contract found?    │
                                                                               └─────────────┬──────────────┘
                                                                                             │
                                                                          ┌────────NO───────┴───────YES────────┐
                                                                          │                                    │
                                                                          ▼                                    ▼
                                                               ┌─────────────────┐              ┌──────────────────────────┐
                                                               │   NO TRADE      │              │   FIRE ORDER             │
                                                               │   "No valid     │              │   ──────────────────     │
                                                               │    contract"    │              │   • Increment trade cnt  │
                                                               └─────────────────┘              │   • Set OCO (Stop/Target)│
                                                                                                │   • Process immediately  │
                                                                                                └──────────────────────────┘
```

---

## Zone Classification

```
QQQ MOVE FROM OPEN
──────────────────────────────────────────────────────────────────────────────►

0%        0.35%           0.50%                   0.80%
│           │               │                       │
│  NOISE    │   WATCHING    │      FADE ZONE        │    MOMENTUM ZONE
│  (block)  │   (no edge)   │  (if VIX calm)        │   (if VIX rising)
│           │               │                       │
└───────────┴───────────────┴───────────────────────┴──────────────────────

VIX DIRECTION determines which zone is active:
  • VIX Falling/Stable  →  FADE ZONE eligible
  • VIX Rising/Spiking  →  MOMENTUM ZONE eligible
  • VIX Whipsaw         →  NO TRADE (direction unclear)
```

---

## Strategy Details

### DEBIT_FADE (Mean Reversion)

**Philosophy:** Market extended, VIX calm = fade the move

| Attribute | Value |
|-----------|-------|
| **Direction** | Counter-trend (QQQ up → PUT, QQQ down → CALL) |
| **Min Move** | 0.50% |
| **VIX Condition** | Falling or Stable |
| **Regime** | PERFECT_MR, GOOD_MR, NORMAL, RECOVERING, IMPROVING |
| **Score** | >= 45 |
| **Time Window** | 10:30 - 14:00 ET |
| **Delta Target** | ~0.30 OTM |

**When it fires:**
```
QQQ rallies +0.65% from open
VIX is down -2% (falling)
Regime = NORMAL, Score = 55
→ Buy PUT (fade the rally)
```

### ITM_MOMENTUM (Trend Following)

**Philosophy:** Fear rising, market moving = ride the wave

| Attribute | Value |
|-----------|-------|
| **Direction** | With-trend (QQQ up → CALL, QQQ down → PUT) |
| **Min Move** | 0.80% |
| **VIX Condition** | Rising or Spiking |
| **Regime** | DETERIORATING, ELEVATED, WORSENING_HIGH, PANIC_EASING, CALMING |
| **VIX Level** | > 11.5 |
| **Time Window** | 10:00 - 13:30 ET |
| **Delta Target** | ~0.70 ITM |

**When it fires:**
```
QQQ drops -1.2% from open
VIX is up +8% (rising fast)
Regime = ELEVATED, VIX = 18.5
→ Buy PUT (ride momentum down)
```

---

## Regime Compatibility Matrix

| Regime | FADE | MOMENTUM | NO_TRADE |
|--------|:----:|:--------:|:--------:|
| PERFECT_MR | ✅ | ❌ | ❌ |
| GOOD_MR | ✅ | ❌ | ❌ |
| NORMAL | ✅ | ❌ | ❌ |
| RECOVERING | ✅ | ❌ | ❌ |
| IMPROVING | ✅ | ❌ | ❌ |
| DETERIORATING | ❌ | ✅ | ❌ |
| ELEVATED | ❌ | ✅ | ❌ |
| WORSENING_HIGH | ❌ | ✅ | ❌ |
| PANIC_EASING | ❌ | ✅ | ❌ |
| CALMING | ❌ | ✅ | ❌ |
| CAUTION_LOW | ❌ | ❌ | ✅ |
| TRANSITION | ❌ | ❌ | ✅ |
| CHOPPY_LOW | ❌ | ❌ | ✅ |
| RISK_OFF_LOW | ❌ | ❌ | ✅ |
| BREAKING | ❌ | ❌ | ✅ |
| UNSTABLE | ❌ | ❌ | ✅ |
| FULL_PANIC | ❌ | ❌ | ✅ |
| CRASH | ❌ | ❌ | ✅ |
| VOLATILE | ❌ | ❌ | ✅ |

---

## VIX Direction Classification

| Direction | Condition | Meaning |
|-----------|-----------|---------|
| FALLING_FAST | VIX down > 5% | Fear collapsing rapidly |
| FALLING | VIX down 2-5% | Fear subsiding |
| STABLE | VIX change < 2% | No significant change |
| RISING | VIX up 2-5% | Fear building |
| RISING_FAST | VIX up 5-10% | Fear accelerating |
| SPIKING | VIX up > 10% | Panic mode |
| WHIPSAW | Direction changed 3+ times | Chaotic, avoid |

---

## Trade Management

### Entry
- Select 0-1 DTE contract matching direction
- Target delta: 0.30 (FADE) or 0.70 (MOMENTUM)
- Verify OI >= 100, spread <= 25%

### Exit (OCO Pair)
- **Stop Loss:** 15% of entry price
- **Profit Target:** 50% of entry price
- **Time Exit:** Force close at 15:30 ET (avoid overnight)
- **Expiry Exit:** Force close at 15:45 if expiring today

### Position Sizing
- Allocation: 6.25% of portfolio per trade
- Max contracts: Based on allocation / contract cost

---

## Example Day

```
09:30  Market opens, QQQ = $420.00, VIX = 14.0
10:00  QQQ = $420.50 (+0.12%), VIX = 13.8 → NOISE (< 0.35%)
10:15  QQQ = $421.20 (+0.29%), VIX = 13.5 → NOISE (< 0.35%)
10:30  QQQ = $422.10 (+0.50%), VIX = 13.2 → FADE eligible!
       Regime = NORMAL, Score = 52, VIX falling
       → BUY PUT @ $0.65, Stop = $0.55, Target = $0.98
11:00  QQQ = $421.50, PUT = $0.85 (+31%)
11:30  QQQ = $420.80, PUT = $0.98 (+51%) → TARGET HIT! Exit.
       Trade #1 complete: +51% profit

12:00  QQQ reverses, now $423.50 (+0.83%), VIX spikes to 15.5
       → MOMENTUM eligible (> 0.80%, VIX rising)
       Regime = ELEVATED, VIX = 15.5 > 11.5
       → BUY CALL @ $1.20, Stop = $1.02, Target = $1.80
13:00  QQQ = $424.20, CALL = $1.45 (+21%)
13:30  QQQ = $423.80, CALL = $1.15 → STOP HIT at $1.02
       Trade #2 complete: -15% loss

Daily result: +51% - 15% = +36% net (2 trades)
```

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| V2.3.12 | 2026-01-31 | QQQ threshold at 0.15% (too loose) |
| V2.3.14 | 2026-02-01 | Removed entry throttle, added 3 trades/day |
| V2.3.15 | 2026-02-01 | Sniper logic: 0.35% noise, 0.50% FADE, 2 trades/day |

---

## Implementation Checklist

- [ ] Update `config.py` with new thresholds
- [ ] Modify `classify_qqq_move()` to use 0.35% threshold
- [ ] Add FADE minimum move check (0.50%) in `recommend_strategy_and_direction()`
- [ ] Change `INTRADAY_MAX_TRADES_PER_DAY` from 3 to 2
- [ ] Run backtest to validate signal quality vs quantity
