# V6.5 Options Isolation Backtest Audit Report

**Backtest Period:** 2022-01-01 to 2022-06-30 (H1 2022)
**Starting Capital:** $75,000
**Mode:** Engine Isolation (Options Engine only)
**Ending Equity:** ~$61,809 (before Feb 8 order spam)

---

## EXECUTIVE SUMMARY

This backtest revealed a **CRITICAL P0 bug** in the gamma pin exit logic that caused 184 duplicate BUY orders on Feb 8, 2022. The Options Engine functioned correctly for swing spreads and intraday options until this bug triggered near expiration. The intraday system shows promise with the conviction-based direction confirmation working as designed.

### Severity Breakdown
| Priority | Count | Description |
|----------|-------|-------------|
| P0 CRITICAL | 1 | Gamma Pin Exit order spam - 3,660 contracts accumulated |
| P1 HIGH | 2 | INTRADAY_UNKNOWN strategy + Spread assignment risk exit |
| P2 MEDIUM | 3 | Skip duplicate scope, Dir=NONE frequency, stop hit rates |
| P3 LOW | 2 | Logging improvements, time window refinement |

---

## 1. CRITICAL BUG: GAMMA_PIN_EXIT ORDER SPAM (P0)

### What Happened
On Feb 8, 2022, the system placed **184 BUY orders** for the same option contract every minute from 09:38 to 14:20, accumulating 3,660 contracts (184 × 20) worth approximately $530,000.

### Log Evidence
```
2022-02-08 09:38:00 GAMMA_PIN_EXIT: Early exit triggered | Price=$355.33 Strike=$357 Distance=0.47% < 0.50% | DTE=2
2022-02-08 09:38:00 ROUTER: RECEIVED | QQQ   220211C00352000 | Weight=0.0% | Source=OPT | Urgency=IMMEDIATE
2022-02-08 09:38:00 ROUTER: OPTIONS_SIZING | QQQ   220211C00352000 | Using requested_quantity=20 contracts
2022-02-08 09:38:00 FILL: BUY 20.0 QQQ   220211C00352000 @ $6.45   ← WRONG! Should be SELL
2022-02-08 09:39:00 GAMMA_PIN_EXIT: Early exit triggered | ...
2022-02-08 09:39:00 FILL: BUY 20.0 QQQ   220211C00352000 @ $6.52   ← Repeats every minute!
... (182 more orders)
```

### Root Cause
**TWO bugs in `options_engine.py:check_gamma_pin_exit()` (lines 5698-5714):**

1. **Missing `spread_short_leg_quantity`** in metadata:
   ```python
   metadata={
       "spread_close_short": True,
       "spread_short_leg_symbol": spread.short_leg.symbol,
       "exit_type": "GAMMA_PIN",
       # MISSING: "spread_short_leg_quantity": spread.num_spreads
   }
   ```

2. **Positive `requested_quantity`** sends BUY instead of SELL:
   ```python
   requested_quantity=getattr(spread, "num_spreads", ...)  # Returns 20 (positive)
   ```

The router at `portfolio_router.py:1508` checks:
```python
if short_leg_symbol and short_leg_qty:  # ← short_leg_qty is None!
    # COMBO path NOT taken
```

Without `spread_short_leg_quantity`, the combo close path is skipped and the positive `requested_quantity=20` becomes a BUY order.

### Why Duplicate Check Failed
The `ROUTER: SKIP_DUPLICATE` logic only prevents duplicates **within the same minute**:
```
2022-01-03 10:00:00 ROUTER: SKIP_DUPLICATE | QQQ   220218C00382000:BUY:20 already executed this minute
```

But gamma_pin fires on different minutes (09:38, 09:39, 09:41...) so the check doesn't catch it.

### FIX REQUIRED
In `options_engine.py:check_gamma_pin_exit()`:
```python
return [
    TargetWeight(
        symbol=spread.long_leg.symbol,
        target_weight=0.0,
        source="OPT",
        urgency=Urgency.IMMEDIATE,
        reason=f"GAMMA_PIN_BUFFER (price within {distance_pct:.2%} of strike ${short_strike})",
        requested_quantity=getattr(spread, "num_spreads", getattr(spread, "num_contracts", 1)),
        metadata={
            "spread_close_short": True,
            "spread_short_leg_symbol": spread.short_leg.symbol,
            "spread_short_leg_quantity": getattr(spread, "num_spreads", getattr(spread, "num_contracts", 1)),  # ADD THIS
            "exit_type": "GAMMA_PIN",
        },
    )
]
```

Also add a once-per-position flag to prevent re-triggering:
```python
# At class level
self._gamma_pin_triggered = False

# In check_gamma_pin_exit:
if self._gamma_pin_triggered:
    return None  # Already triggered, don't spam

# After triggering:
self._gamma_pin_triggered = True
```

---

## 2. PERFORMANCE SUMMARY

### Swing Spreads (VASS)
| Date | Type | Long | Short | Entry Debit | Exit | P&L | Notes |
|------|------|------|-------|-------------|------|-----|-------|
| Jan 3 | BULL_CALL | 382 | 388 | $4.90 | $0.83 | -$8,140 | Closed Jan 21 at near worthless |
| Feb 1 | BULL_CALL | 352 | 357 | $4.18 | Gamma Pin Bug | N/A | Order spam corrupted |

- **Swing Win Rate:** 0% (1 loss before bug)
- **First spread held through Jan correction:** VIX rose from 17→25, spread lost 83% of value

### Intraday Trades
| Date | Strategy | Direction | Contracts | Entry | Exit | P&L | Hold Time |
|------|----------|-----------|-----------|-------|------|-----|-----------|
| Jan 24 | DEBIT_FADE | CALL | 10 | $2.67 | $2.27 | -$400 | 2 min |
| Jan 24 | ITM_MOM | PUT | 2 | $12.09 | $10.26 | -$366 | 1h 23m |
| Jan 25 | DEBIT_FADE | CALL | 8 | $3.20 | $4.07 | +$696 | 2h 15m |
| Jan 26 | UNKNOWN | CALL | 10 | $2.60 | $2.16 | -$440 | 19 min |
| Jan 27 | DEBIT_FADE | PUT | 8 | $3.23 | $2.74 | -$392 | 28 min |
| Jan 27 | DEBIT_FADE | PUT | 8 | $3.21 | $2.71 | -$400 | 6 min |
| Jan 31 | UNKNOWN | CALL | 14 | $1.85 | $1.53 | -$448 | 15 min |
| Jan 31 | UNKNOWN | CALL | 15 | $1.67 | $1.42 | -$375 | 3 min |

- **Intraday Win Rate:** 12.5% (1/8)
- **Average Win:** +$696
- **Average Loss:** -$403
- **Total Intraday P&L:** -$2,125

---

## 3. ISSUES BY PRIORITY

### P1 HIGH: INTRADAY_UNKNOWN Strategy

**Problem:** 3 trades logged as `INTRADAY_UNKNOWN` instead of a valid strategy name.

**Evidence:**
```
2022-01-26 12:45:00 INTRADAY_SIGNAL: INTRADAY_UNKNOWN: Regime=CALMING | Score=40 | VIX=30.0 (FALLING)
2022-01-31 10:15:00 INTRADAY_SIGNAL: INTRADAY_UNKNOWN: Regime=CALMING | Score=40 | VIX=26.7 (FALLING)
2022-01-31 10:45:00 INTRADAY_SIGNAL: INTRADAY_UNKNOWN: Regime=CALMING | Score=40 | VIX=26.7 (FALLING)
```

**Root Cause:** Strategy selection is falling through without matching a defined strategy.

**Fix:** Add fallback or verify all regimes have a corresponding strategy in `_select_intraday_strategy()`.

---

### P1 HIGH: Assignment Risk Exit (Spread Closure)

**Problem:** On Jan 3, immediately after opening the first spread, an assignment risk exit triggered:
```
2022-01-03 10:00:00 ASSIGNMENT_RISK_EXIT: MARGIN_BUFFER_INSUFFICIENT: Assignment exposure=$776,000 | Required buffer=$155,200 (20%) | Available margin=$74,224
```

This created a close signal but was blocked by `SKIP_DUPLICATE`.

**Impact:** The position was NOT closed and continued to lose money through January.

**Fix:** Assignment risk exit should not be blocked by duplicate check if it's a different signal type.

---

### P2 MEDIUM: Dir=NONE Frequency

**Problem:** Direction is NONE ~85% of the time, limiting trade opportunities.

**Evidence:**
- Dir=CALL: ~40 occurrences
- Dir=PUT: ~15 occurrences
- Dir=NONE: ~600+ occurrences

**Cause:** UVXY threshold of 5% is too high for normal market conditions.

**Recommendation:** Consider lowering to 3% or using regime-first strategy selection (deferred per user request).

---

### P2 MEDIUM: Intraday Stop Hit Rate

**Problem:** 8 of 8 intraday trades hit their stop losses very quickly.

**Pattern:**
- Average hold time before stop: 15 minutes
- Win rate: 12.5% (only 1 trade reached profit target)

**Cause:** Stop loss may be too tight for high-VIX environment (VIX was 22-33 during Jan 24-31).

**Recommendation:** Consider widening stops during elevated VIX (e.g., 1.5× ATR instead of 1×).

---

### P2 MEDIUM: Skip Duplicate Scope

**Problem:** `SKIP_DUPLICATE` only works within the same minute.

**Fix:** Add position-level duplicate check:
```python
# Track symbols with pending signals
if symbol in self._pending_exit_symbols:
    self.log(f"ROUTER: SKIP_DUPLICATE | {symbol} | Exit already pending")
    return
```

---

### P3 LOW: Time Window Logging

The DEBIT_MOMENTUM time window check was added but the "outside time window" rejection is not logged consistently.

---

### P3 LOW: Strategy Logging Consistency

Some strategies log `INTRADAY_SIGNAL` with full details, others log minimal info. Standardize the logging format.

---

## 4. ENGINE SCORECARD

| System | Score | Status | Key Finding |
|--------|:-----:|--------|-------------|
| Swing Spreads (VASS) | 3/5 | ⚠️ | Gamma pin bug, held losing spread too long |
| Intraday Entry | 4/5 | ✓ | Conviction override working, signals firing |
| Intraday Exit | 2/5 | ⚠️ | Stops too tight, 87.5% stop hit rate |
| OCO Manager | 3/5 | ⚠️ | OCO working but stops triggering too fast |
| Gamma Pin Exit | 1/5 | ❌ | CRITICAL BUG - order spam |
| Assignment Risk | 2/5 | ⚠️ | Triggered but blocked by duplicate check |
| Direction Logic | 3/5 | ⚠️ | Dir=NONE too frequent |
| Position Limits | 5/5 | ✓ | TradeCount=1/2, 2/2 respected |
| Friday Firewall | 5/5 | ✓ | `FRIDAY_FIREWALL: No action needed` logged |
| **Overall** | **3/5** | ⚠️ | Critical bug must be fixed before next backtest |

---

## 5. WHAT WORKED WELL

1. **Conviction Override:** The micro conviction logic correctly overrode neutral macro signals:
   ```
   INTRADAY_SIGNAL_APPROVED: CONVICTION: UVXY -5% < -5% | Macro=NEUTRAL | VETO: MICRO conviction (BULLISH) overrides NEUTRAL Macro
   ```

2. **Trade Limits:** Position limits (2 trades/day) were respected.

3. **Friday Firewall:** VIX check logged correctly.

4. **Spread Entry:** BULL_CALL spreads entered correctly with proper combo orders.

5. **Micro Regime Updates:** 15-minute updates logged consistently with VIX level, direction proxy, and regime.

---

## 6. IMMEDIATE FIXES REQUIRED

### Before Next Backtest:

1. **FIX gamma_pin_exit metadata** - Add `spread_short_leg_quantity`
2. **ADD gamma_pin once-per-position flag** - Prevent re-triggering
3. **FIX INTRADAY_UNKNOWN** - Ensure all regimes map to strategies
4. **ADD position-level duplicate check** - Prevent gamma pin spam pattern

### Code Changes:
- `engines/satellite/options_engine.py:check_gamma_pin_exit()` - Lines 5698-5714
- `portfolio/portfolio_router.py` - Add position-level duplicate tracking

---

## 7. DEFERRED ITEMS

Per user request, the following are deferred until 3-market backtest validation:
- Dir=NONE frequency (UVXY threshold tuning)
- Regime-first strategy selection proposal

---

**Audit Completed:** 2026-02-08
**Next Action:** Fix P0 gamma pin bug before any further backtesting
