# 18. Options Engine

[Previous: 17 - Appendix: Glossary](17-appendix-glossary.md) | [Table of Contents](00-table-of-contents.md) | [Next: 19 - OCO Manager](19-oco-manager.md)

---

## Overview

> **Last Updated**: 11 February 2026 (V6.15)

The **Options Engine** implements a dual-mode architecture for QQQ options trading. This is a **satellite engine** (25% allocation) with two distinct operating modes based on DTE (days to expiration).

> **V6.15 Revision** (Latest):
> - Profit target increased to 60% (was 50%) for trend riding
> - Intraday force exit moved to 15:25 (was 15:30) to avoid OCO race conditions
> - `INTRADAY_OPTIONS_OFFSET_MINUTES` set to 35 (aligned with 15:25 fallback)
> - UVXY thresholds tuned: `MICRO_UVXY_BEARISH_THRESHOLD` +2.0%, `MICRO_UVXY_BULLISH_THRESHOLD` -4.0%
> - Micro score confirmations: `MICRO_SCORE_BULLISH_CONFIRM` 48, `MICRO_SCORE_BEARISH_CONFIRM` 47
> - `SHORT_LEG_ITM_EXIT_THRESHOLD` relaxed to 1.0% (was 0.5%)
> - Spread stop/target adjusted to 40%/50% (regime-adjusted)
>
> **V6.14 Revision**:
> - UVXY conviction thresholds tuned: `MICRO_UVXY_BEARISH_THRESHOLD` +2.8%, `MICRO_UVXY_BULLISH_THRESHOLD` -4.5%
> - Micro score confirmations adjusted: `MICRO_SCORE_BULLISH_CONFIRM` 47, `MICRO_SCORE_BEARISH_CONFIRM` 49
> - T-14 expiration fix: Moved expiring options close from 14:00 to 12:00 noon
> - Pre-market VIX shock ladder added for de-risking before market open
>
> **V6.13.1 Revision**:
> - Narrowed VIX adaptive stable bands: `VIX_STABLE_BAND_LOW` 0.2% (was 0.3%), `VIX_STABLE_BAND_HIGH` 0.7% (was 1.0%)
> - Narrowed neutrality exit zone: 48-62 (was 45-65)
> - Reduced `SPREAD_NEUTRALITY_EXIT_PNL_BAND` to 6% (was 10%)
> - Reduced `INTRADAY_QQQ_FALLBACK_MIN_MOVE` to 0.30% (was 0.50%)
>
> **V6.12 Revision**:
> - `MICRO_UVXY_CONVICTION_EXTREME` set to 3.5% (was 5%)
> - `NEUTRAL_ALIGNED_SIZE_MULT` added (0.50) for reduced sizing when Macro NEUTRAL
> - Dir=NONE reduction + VASS DTE fallback + cross-blocking fix
>
> **V6.10 Revision**:
> - **Phase 1 - Assignment Prevention (P0)**:
>   - Added `SPREAD_FORCE_CLOSE_DTE=1` - mandatory close ALL spreads at DTE=1
>   - Added `PREMARKET_ITM_CHECK_ENABLED=True` - 09:25 pre-market ITM check
>   - Tightened `SHORT_LEG_ITM_EXIT_THRESHOLD` 1% -> 0.5% ITM for faster exit
>   - Widened `SPREAD_WIDTH_MIN` $3 -> $5 for assignment gap protection
> - **Phase 2 - Direction Generation (Micro Engine)**:
>   - Narrowed `VIX_STABLE_BAND_LOW` +/-0.5% -> +/-0.3% (more signals in low VIX)
>   - Narrowed `VIX_STABLE_BAND_HIGH` +/-2.0% -> +/-1.0% (high VIX still trades)
>   - Lowered `MICRO_UVXY_CONVICTION_EXTREME` 7% -> 5% (capture more conviction)
>   - Lowered `MICRO_UVXY_BEARISH_THRESHOLD` +4% -> +2.5% (more PUT signals)
>   - Raised `MICRO_UVXY_BULLISH_THRESHOLD` -5% -> -3% (more CALL signals)
>   - Lowered `INTRADAY_DEBIT_FADE_VIX_MIN` 13.5 -> 10.5 (low-VIX years can trade)
>   - Lowered `INTRADAY_QQQ_FALLBACK_MIN_MOVE` 0.70% -> 0.50% (more STABLE trades)
>   - Adjusted `MICRO_SCORE_BULLISH_CONFIRM` 55 -> 52, `MICRO_SCORE_BEARISH_CONFIRM` 45 -> 48
> - **Phase 3 - Spread Construction (VASS)**:
>   - Widened delta requirements: CALL long 0.40->0.35, short 0.55->0.60
>   - Widened PUT delta requirements: long 0.30->0.25, short 0.08->0.05
>   - Lowered `CREDIT_SPREAD_MIN_CREDIT` $0.30 -> $0.20
>   - Added `CREDIT_SPREAD_FALLBACK_TO_DEBIT=True` for PUT direction
> - **Phase 4 - Execution Pipeline**:
>   - Lowered `OPTIONS_MAX_MARGIN_PCT` 30% -> 25%
>   - Adjusted `MARGIN_PRE_CHECK_BUFFER` 2.0 -> 0.15 (was too restrictive)
>   - Added `MARGIN_CHECK_BEFORE_SIGNAL=True` (pre-approve before generating)
> - **Phase 5 - Risk/Reward**:
>   - Symmetric `SPREAD_STOP_LOSS_PCT` 35% -> 40%, `SPREAD_PROFIT_TARGET_PCT` 50% -> 40%
>   - Added choppy market filter: 3 reversals in 2hrs = 50% size reduction
>
> **V6.9 Revision**:
> - VIX-adaptive STABLE band introduced: `VIX_STABLE_BAND_LOW` and `VIX_STABLE_BAND_HIGH`
> - High IV now uses credit spreads via VASS routing (BULL_PUT for bullish, BEAR_CALL for bearish)
> - Assignment margin buffer restored to 20% for safety
> - P0 Fix 5: Short leg ITM exit regardless of DTE (catches assignment risk at any DTE)
>
> **V6.8 Revision**:
> - Relaxed liquidity filters: Open interest 100->50, spread warning 25%->30%
> - Widened VASS High IV DTE range: 7-21 -> 5-28 (more contract matches)
> - Tighter ATR-based stops: multiplier 1.5->1.0, max 50%->30% loss cap
> - Lowered delta requirements: long leg min 0.45->0.40, short leg max 0.52->0.55
> - Narrower spread width target: 4.0 -> 3.0 (more matches in chain)
> - Reduced UVXY conviction thresholds: +/-3% -> +/-2.5%
> - Lowered intraday VIX floors: DEBIT_FADE 13.5->11.5, ITM 11.5->10.0
> - Reduced intraday score requirements: DEBIT_FADE 45->35, ITM 50->40
> - Widened FADE_MAX_MOVE: 1.20% -> 1.50% (allow stronger continuations)
> - Reduced assignment margin buffer: 20%->10% (fewer instant exit triggers)
>
> **V6.5 Revision**:
> - Fixed DEBIT_FADE divergence/confirmation logic (was inverted)
> - Added `DEBIT_MOMENTUM` strategy for confirmed trends (QQQ and VIX aligned)
> - WORSENING + WORSENING_HIGH regimes now ALWAYS use ITM PUT (regardless of QQQ direction)
> - Updated 21-regime matrix with correct CALL/PUT directions for all scenarios
> - Divergence (opposite moves) = FADE, Confirmation (aligned moves) = MOMENTUM
>
> **V2.30 Revision**:
> - Bearish Options Path Fix: Removed outer `regime >= 40` gate that blocked PUT spreads below regime 40
> - Direction-aware gating: `_generate_options_signals_gated()` routes bullish vs bearish separately
> - Bullish options (regime > 60) require StartupGate `allows_directional_longs()`
> - Bearish options (regime < 45) require StartupGate `allows_bearish_options()` (available from OBSERVATION phase)
> - Intraday gating via `_scan_options_signals_gated()` (same direction-aware logic)
> - `clear_all_positions()` now clears 16 fields (was 13): added `_pending_stop_price`, `_pending_target_price`, `_pending_intraday_exit`
>
> **V2.24.2 Revision**:
> - DTE Double-Filter Fix: `select_spread_legs()` and `check_spread_entry_signal()` accept `dte_min`/`dte_max` params
> - Elastic Delta Bands (V2.24.1): Progressive widening when no candidates found
> - VASS-routed DTE ranges now properly override `config.SPREAD_DTE_MIN`/`SPREAD_DTE_MAX`
>
> **V2.19 Revision**: Limit orders for options, VIX floor for DEBIT_FADE, position limit enforcement fix
>
> **V2.8 Revision**: VASS (VIX-Adaptive Strategy Selection), Credit Spreads reintroduced
>
> **V2.3.6 Revision**:
> - Spread order protection (margin pre-check, orphan leg cleanup)
> - Intraday filters relaxed: OI 500→200, Spread 10%→15%
> - Sniper window opened: 10:30→10:00 start
>
> **V2.3 Revision**: Simplified Swing Mode to Debit Spreads only. Added VIX to macro regime score.
> Full specification: `docs/specs/v2-1-options-engine-design.txt`

**Key Characteristics:**
- **Underlying**: QQQ (Nasdaq 100 ETF)
- **Total Allocation**: 25% of portfolio
- **Dual-Mode Architecture**:
  - **Swing Mode (18.75%)**: 14-45 DTE (VASS-adaptive), Debit Spreads + Credit Spreads (regime-based)
  - **Intraday Mode (6.25%)**: 0-2 DTE, Micro Regime Engine

---

## Dual-Mode Architecture (V2.3, updated V2.8+)

```
┌───────────────────────────────────────────────────────────────────────┐
│                OPTIONS ENGINE V2.24 DUAL-MODE + VASS                   │
├───────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  ┌───────────────────────────┐     ┌─────────────────────────────┐   │
│  │     SWING MODE            │     │     INTRADAY MODE           │   │
│  │  (14-45 DTE, VASS-routed) │     │       (0-2 DTE)             │   │
│  ├───────────────────────────┤     ├─────────────────────────────┤   │
│  │ Allocation: 18.75%        │     │ Allocation: 6.25%           │   │
│  │                           │     │                             │   │
│  │ VASS Strategy Selection:  │     │ Decision Engine:            │   │
│  │ (VIX-Adaptive V2.8)       │     │ MICRO REGIME ENGINE         │   │
│  │ • Low IV: Debit, 30-45DTE │     │ (VIX Level × VIX Direction) │   │
│  │ • Med IV: Debit, 7-21 DTE │     │                             │   │
│  │ • High IV: Credit, 7-14DTE│     │ Strategies:                 │   │
│  │                           │     │ • Debit Fade (MR)           │   │
│  │ Direction by Regime:      │     │ • Credit Spreads            │   │
│  │ • Regime > 60: Bull Call  │     │ • ITM Momentum              │   │
│  │ • Regime < 45: Bear Put   │     │ • Protective Puts           │   │
│  │ • 45-60: NO TRADE         │     │                             │   │
│  │ Regime < 30: Hedge Only   │     │                             │   │
│  └───────────────────────────┘     └─────────────────────────────┘   │
└───────────────────────────────────────────────────────────────────────┘
```

---

## V2.3 Changes: Why Simplified

The original 4-strategy portfolio (Debit Spreads, Credit Spreads, ITM Long, Protective Puts) was simplified due to:

1. **Missing VIX in Regime**: Options are priced off implied volatility, but regime used only realized vol
2. **Over-Engineering**: 4 strategies with different DTE ranges created excessive complexity
3. **Credit Spreads Poor Fit**: QQQ trends strongly; credit spreads lose on breakouts
4. **Always-On Protective Puts**: 3-4% annual drag, mostly wasted in bull markets

**Solution**: Single strategy (Debit Spreads) with regime-based direction, VIX added to regime score.

> **Note (V2.8)**: Credit Spreads were reintroduced via VASS when VIX > 25. See [VASS](#vass-vix-adaptive-strategy-selection-v28) section below.

---

## Swing Mode (14-45 DTE, VASS-Adaptive)

### Allocation: 18.75% of Portfolio

Swing Mode uses **Debit Spreads** (Low/Medium IV) or **Credit Spreads** (High IV) as selected by VASS (V2.8). Direction is determined by the macro regime score (which includes VIX). DTE range is set by VASS per IV environment (see below).

**Config Parameters:**
- `OPTIONS_SWING_ALLOCATION` = 0.1875 (18.75%)
- `SPREAD_DTE_MIN` = 14 (default minimum, overridden by VASS)
- `SPREAD_DTE_MAX` = 45 (default maximum, overridden by VASS)

---

## VASS: VIX-Adaptive Strategy Selection (V2.8)

VASS dynamically selects the spread strategy and DTE range based on the current VIX level. This replaces the static "debit spreads only" approach from V2.3, allowing the engine to exploit different volatility environments.

### IV Environment Classification (V6.9)

| IV Environment | VIX Range | Strategy | DTE Range | Rationale |
|:--------------:|:---------:|----------|:---------:|-----------|
| **Low IV** | VIX < 15 | Debit Spreads | 30-45 DTE (monthly) | Cheap options, need more time for move |
| **Medium IV** | VIX 15-25 | Debit Spreads | 7-21 DTE (weekly) | Fair pricing, standard DTE |
| **High IV** | VIX > 25 | Credit Spreads | 5-28 DTE (V6.8: widened from 7-14) | Rich premium, sell into fear |

> **V6.9 Change**: High IV now routes to credit spreads. Direction determines spread type:
> - Bullish (regime > 60): **BULL_PUT** credit spread (sell put spread)
> - Bearish (regime < 45): **BEAR_CALL** credit spread (sell call spread)

### How VASS Routing Works

```
VIX Current → Smoothed VIX (30-min SMA) → IV Classification → Strategy + DTE Selection
```

The 30-minute smoothing window prevents strategy flickering when VIX oscillates near thresholds.

### VASS Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `VASS_ENABLED` | True | Master switch for VASS |
| `VASS_IV_LOW_THRESHOLD` | 15 | VIX below this = Low IV |
| `VASS_IV_HIGH_THRESHOLD` | 25 | VIX above this = High IV |
| `VASS_IV_SMOOTHING_MINUTES` | 30 | SMA window to prevent flickering |
| `VASS_LOW_IV_DTE_MIN` | 30 | Low IV: Monthly DTE minimum |
| `VASS_LOW_IV_DTE_MAX` | 45 | Low IV: Monthly DTE maximum |
| `VASS_MEDIUM_IV_DTE_MIN` | 7 | Medium IV: Weekly DTE minimum |
| `VASS_MEDIUM_IV_DTE_MAX` | 21 | Medium IV: Weekly DTE maximum |
| `VASS_HIGH_IV_DTE_MIN` | 5 | High IV: Weekly DTE minimum (V6.8: was 7) |
| `VASS_HIGH_IV_DTE_MAX` | 28 | High IV: Weekly DTE maximum (V6.8: was 21) |
| `VASS_LOG_REJECTION_INTERVAL_MINUTES` | 15 | Throttled rejection logging |

> **V2.24.2 Note**: When VASS routes to a specific DTE range, the `dte_min`/`dte_max` parameters are passed through to `select_spread_legs()` and `check_spread_entry_signal()`, overriding the global `SPREAD_DTE_MIN`/`SPREAD_DTE_MAX`. See [DTE Double-Filter Fix](#v2242-dte-double-filter-fix) below.

---

## Credit Spreads (V2.8/V2.10)

Credit Spreads were reintroduced in V2.8 via VASS for High IV environments (VIX > 25). Unlike the V2.1 version which was always-on, credit spreads now only activate when implied volatility is elevated, making them a premium-selling strategy in fearful markets.

### Direction by Regime

| Regime Score | Spread Type | Setup |
|:------------:|-------------|-------|
| > 60 (Bullish) | **BULL_PUT** | Sell put spread (bullish credit) |
| < 45 (Bearish) | **BEAR_CALL** | Sell call spread (bearish credit) |
| 45-60 (Neutral) | **NO TRADE** | Neutral zone, skip |

### Short Leg Selection

- **Delta Range**: 0.25-0.40 (OTM)
- **Width Target**: $5.00

### Exit Rules

- **Profit Target**: 50% of max profit (collect half the credit, close)
- **Stop Loss**: Spread value doubles (100% loss of credit received)

### Credit Spread Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `CREDIT_SPREAD_MIN_CREDIT` | 0.30 | Minimum $0.30 credit to justify margin |
| `CREDIT_SPREAD_MIN_CREDIT_ADJUSTED` | 0.35 | Adjusted for exit slippage |
| `CREDIT_SPREAD_WIDTH_TARGET` | 5.0 | $5 width for credit spreads |
| `CREDIT_SPREAD_PROFIT_TARGET` | 0.50 | Exit at 50% of max profit |
| `CREDIT_SPREAD_STOP_MULTIPLIER` | 2.0 | Stop if spread doubles |
| `CREDIT_SPREAD_SHORT_LEG_DELTA_MIN` | 0.25 | Short leg minimum delta |
| `CREDIT_SPREAD_SHORT_LEG_DELTA_MAX` | 0.40 | Short leg maximum delta |

---

## V2.4.3: Width-Based Short Leg Selection

Prior to V2.4.3, the short leg of a spread was selected by delta target. This caused a "delta trap" where delta jumps between strikes left gaps, producing no valid candidates.

### The Fix

Select the short leg by **strike width** (distance from long leg), not delta:

```
Long leg at $480 → Target short leg at $480 + $5 = $485
(instead of searching for delta 0.15 which may not exist)
```

### Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `SPREAD_SHORT_LEG_BY_WIDTH` | True | Use width-based selection |
| `SPREAD_WIDTH_MIN` | 5.0 | Minimum $5 spread width (V6.10: was $2, assignment protection) |
| `SPREAD_WIDTH_MAX` | 10.0 | Maximum $10 spread width |
| `SPREAD_WIDTH_TARGET` | 5.0 | Target $5 width (V6.10: was $3, assignment protection) |

Candidates are sorted by distance from target width, then by open interest. This eliminates the silent failure mode where no short leg was found due to delta gaps.

---

## V2.24.1: Elastic Delta Bands

When no option contracts match the target delta range, the engine progressively widens the search band in steps rather than giving up immediately.

### Progressive Widening Steps

```
Step 0: [target - tolerance, target + tolerance]         (standard range)
Step 1: [target - tolerance - 0.03, target + tolerance + 0.03]
Step 2: [target - tolerance - 0.07, target + tolerance + 0.07]
Step 3: [target - tolerance - 0.12, target + tolerance + 0.12]
```

### Hard Limits

- **Floor**: 0.10 (never search below this delta -- too far OTM, illiquid)
- **Ceiling**: 0.95 (never search above this delta -- deep ITM, poor leverage)

### Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `ELASTIC_DELTA_STEPS` | [0.0, 0.03, 0.07, 0.12] | Widening increments |
| `ELASTIC_DELTA_FLOOR` | 0.10 | Absolute minimum delta |
| `ELASTIC_DELTA_CEILING` | 0.95 | Absolute maximum delta |

---

## V2.24.2: DTE Double-Filter Fix

Prior to V2.24.2, VASS would select a DTE range (e.g., 7-21 for Medium IV), but `select_spread_legs()` still applied the global `config.SPREAD_DTE_MIN`/`SPREAD_DTE_MAX` as an additional filter. If the VASS range was narrower than the global range, contracts would be filtered twice, sometimes producing no results.

### The Fix

Both `select_spread_legs()` and `check_spread_entry_signal()` now accept optional `dte_min`/`dte_max` parameters:

```python
def select_spread_legs(self, contracts, direction, target_width=None,
                       current_time=None, dte_min=None, dte_max=None):
    ...

def check_spread_entry_signal(self, regime_score, vix_current, ...,
                              dte_min=None, dte_max=None):
    ...
```

Inside these methods, the effective DTE range is determined by:

```python
effective_dte_min = dte_min if dte_min is not None else config.SPREAD_DTE_MIN
effective_dte_max = dte_max if dte_max is not None else config.SPREAD_DTE_MAX
```

When VASS routes to a specific IV environment, it passes the VASS DTE range directly, bypassing the global defaults.

---

## V2.22: Neutrality Exit (Updated V6.13)

When the regime score enters the neutral zone, existing spread positions that are near break-even should be closed rather than held through directionless chop.

### Rules

- **Trigger**: Regime score between neutrality zone boundaries
- **P&L Band**: Position P&L within defined band of entry (considered "flat")
- **Action**: Close the spread to free capital for clearer setups

### Configuration (V6.13 Values)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `SPREAD_NEUTRALITY_EXIT_ENABLED` | True | Enable neutrality exit |
| `SPREAD_NEUTRALITY_EXIT_PNL_BAND` | 0.06 | V6.13: +/-6% P&L considered flat (was 10%) |
| `SPREAD_NEUTRALITY_ZONE_LOW` | 48 | V6.13: Lower bound of neutral zone (was 45) |
| `SPREAD_NEUTRALITY_ZONE_HIGH` | 62 | V6.13: Upper bound of neutral zone (was 65) |

> **V6.13 Change**: Narrowed neutrality zone from 45-65 to 48-62 and tightened P&L band from 10% to 6% for faster capital recycling in choppy markets.

---

## V2.19: Limit Orders for Options

Options now use **marketable limit orders** instead of market orders to control slippage.

### Rules

- Limit price set at mid-price + 5% of bid-ask spread (slightly aggressive to fill)
- If bid-ask spread exceeds 20% of mid-price, the option is considered **too illiquid** and the trade is blocked
- Applies to both entry and exit orders

### Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `OPTIONS_USE_LIMIT_ORDERS` | True | Use limit orders for options |
| `OPTIONS_LIMIT_SLIPPAGE_PCT` | 0.05 | 5% of spread added to mid |
| `OPTIONS_MAX_SPREAD_PCT` | 0.20 | Block if spread > 20% of mid |

---

## V2.19: Price Discovery Chain (V2.19/V2.24)

Options pricing uses a 3-layer fallback chain for determining current prices:

```
Layer 1: current_prices[symbol]         ← from portfolio holdings (most reliable)
    ↓ (if not found)
Layer 2: metadata['contract_price']     ← from options engine chain data (V2.19)
    ↓ (if not found)
Layer 3: Bid/Ask mid-price              ← from Securities object (V2.24.1, partial)
```

The `metadata` field on `TargetWeight` objects now includes `contract_price` to enable the Portfolio Router to look up prices when the contract is not yet in the portfolio.

---

## V2.18: Hardcoded Sizing Caps

To prevent outsized options positions, hard dollar caps are enforced regardless of percentage-based sizing:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `SWING_SPREAD_MAX_DOLLARS` | $7,500 | Hard cap for swing spreads (14-45 DTE) |
| `INTRADAY_SPREAD_MAX_DOLLARS` | $4,000 | Hard cap for intraday spreads (0-5 DTE) |

These caps act as a safety net on top of the percentage-based allocation system.

---

## V2.19: VIX Floor for DEBIT_FADE

The intraday DEBIT_FADE strategy (mean-reversion bounce) is disabled when VIX is below 13.5. In ultra-low-volatility "apathy" markets, the QQQ moves are too small for a fade to generate meaningful profit.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `INTRADAY_DEBIT_FADE_VIX_MIN` | 11.5 | Minimum VIX for DEBIT_FADE (V6.8: was 13.5) |
| `INTRADAY_DEBIT_FADE_VIX_MAX` | 25 | Maximum VIX for DEBIT_FADE |

---

## V2.23: Broadened Universe Filter

The options chain filter was significantly widened to support VASS routing across all DTE ranges:

| Parameter | Before V2.23 | After V2.23 |
|-----------|:------------:|:-----------:|
| Strike range | -8 / +5 | **-25 / +25** |
| DTE range | 0-30 | **0-60** |

```python
qqq_option.SetFilter(-25, 25, timedelta(0), timedelta(60))
```

The wider strike range is required for credit spread short legs (delta 0.25-0.40 OTM), and the wider DTE range accommodates VASS Low IV monthly expirations (30-45 DTE).

---

## V2.4.1: Safety Rules

### No Naked Single-Leg Fallback

If spread construction fails (no valid short leg found), the engine does **not** fall back to a naked long option. It stays in cash.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `SWING_FALLBACK_ENABLED` | False | No naked single-leg fallback |

### Friday Firewall

Close swing options before the weekend to avoid gap risk:

- **Time**: 3:45 PM ET on Fridays
- **VIX > 25**: Close **ALL** swing options regardless of P&L
- **VIX < 15**: Calm enough to keep fresh Friday trades through weekend

| Parameter | Default | Description |
|-----------|---------|-------------|
| `FRIDAY_FIREWALL_ENABLED` | True | Enable Friday close |
| `FRIDAY_FIREWALL_TIME_HOUR` | 15 | Close hour |
| `FRIDAY_FIREWALL_TIME_MINUTE` | 45 | Close minute |
| `FRIDAY_FIREWALL_VIX_CLOSE_ALL` | 25 | VIX above this: close all |
| `FRIDAY_FIREWALL_VIX_KEEP_FRESH` | 15 | VIX below this: keep fresh trades |

---

## 4-Factor Entry Scoring System

Each factor contributes 0-1 points. Total score ranges 0-4, minimum threshold: **3.0**

### Factor 1: ADX (Trend Strength)

| ADX Value | Score | Interpretation |
|:---------:|:-----:|----------------|
| < 20 | 0.00 | No trend, avoid |
| 20-25 | 0.50 | Weak trend |
| 25-30 | 0.75 | Moderate trend |
| > 30 | 1.00 | Strong trend, ideal |

**Config Parameter:** `OPTIONS_ADX_PERIOD` (default: 14)

### Factor 2: Momentum (MA200 Position)

| Condition | Score | Interpretation |
|-----------|:-----:|----------------|
| Price > MA200 | 1.00 | Bullish momentum (favor calls) |
| Price < MA200 | 1.00 | Bearish momentum (favor puts) |
| Price = MA200 (within 0.1%) | 0.50 | Neutral, reduced conviction |

**Config Parameter:** `OPTIONS_MA_PERIOD` (default: 200)

### Factor 3: IV Rank

Implied Volatility percentile over the past year.

| IV Rank | Score | Interpretation |
|:-------:|:-----:|----------------|
| 0-20% | 0.25 | Very low IV, cheap options |
| 20-40% | 0.50 | Low IV |
| 40-60% | 0.75 | Normal IV |
| 60-80% | 1.00 | Elevated IV, good premium |
| 80-100% | 0.75 | Very high IV, potential mean reversion |

**Config Parameters:** `OPTIONS_IV_LOOKBACK` (default: 252)

### Factor 4: Liquidity (Bid-Ask Spread)

| Spread (% of mid) | Score | Interpretation |
|:-----------------:|:-----:|----------------|
| < 2% | 1.00 | Excellent liquidity |
| 2-5% | 0.75 | Good liquidity |
| 5-10% | 0.50 | Acceptable |
| 10-15% | 0.25 | Wide but acceptable for 0DTE |
| > 15% | 0.00 | Too wide, avoid |

**Config Parameter:** `OPTIONS_SPREAD_WARNING_PCT` (default: 0.15)

> **V2.3.6 Change:** Widened from 10% to 15% to accommodate 0DTE options which naturally have wider spreads.

---

## Confidence-Weighted Tiered Stops

Higher entry scores indicate higher conviction, allowing wider stops with fewer contracts (risk-adjusted sizing).

| Entry Score | Stop Loss | Contract Size | Risk Profile |
|:-----------:|:---------:|:-------------:|--------------|
| 3.00-3.25 | 20% | 34 contracts | Tight stops, higher volume |
| 3.25-3.50 | 22% | 31 contracts | |
| 3.50-3.75 | 25% | 27 contracts | |
| 3.75-4.00 | 30% | 23 contracts | Wide stops, lower volume |

**Formula:**
```
contracts = floor(allocation / (entry_price * 100 * stop_pct))
```

**Config Parameters:**
- `OPTIONS_STOP_TIER_1` (default: 0.20)
- `OPTIONS_STOP_TIER_2` (default: 0.22)
- `OPTIONS_STOP_TIER_3` (default: 0.25)
- `OPTIONS_STOP_TIER_4` (default: 0.30)

---

## Entry Rules

### Prerequisites

1. **Regime Score >= 40** (not RISK_OFF)
2. **No existing options position**
3. **Time within trading window** (10:00 AM - 2:30 PM ET)
4. **Risk Engine green light** (no active safeguards)
5. **Entry Score >= 3.0**

### Direction Selection

| Condition | Direction | Rationale |
|-----------|-----------|-----------|
| Price > MA200, RSI < 70 | CALL | Bullish momentum, not overbought |
| Price < MA200, RSI > 30 | PUT | Bearish momentum, not oversold |
| Otherwise | NONE | Wait for clearer signal |

### Contract Selection (V2.3.6/V6.8)

**Intraday Mode (0DTE):**
1. **Expiry**: 1-5 DTE (V2.13: Skip 0DTE due to QC data gaps)
2. **Delta Target**: 0.30 (OTM for faster gamma/premium moves)
3. **Delta Tolerance**: +/-0.20 (allows 0.10-0.50 range)
4. **Minimum OI**: 50 (V6.8: lowered from 100)
5. **Max Spread**: 15% (V2.3.6: widened from 10%)

**Swing Mode (Spreads):**
1. **Expiry**: 14-45 DTE (VASS overrides per IV environment)
2. **Long Leg Delta**: 0.35-0.90 (V6.10: widened from 0.55-0.85)
3. **Short Leg**: Selected by strike width (V2.4.3), not delta
4. **Spread Width**: $4-$10 (V6.13: target $4, was $5)

**Liquidity Thresholds (V6.8):**

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `OPTIONS_MIN_OPEN_INTEREST` | 50 | V6.8: Minimum OI (was 100) |
| `OPTIONS_MIN_OPEN_INTEREST_PUT` | 25 | V6.9: Relaxed for PUT spreads |
| `OPTIONS_SPREAD_WARNING_PCT` | 0.15 | Bid-ask spread warning threshold |
| `OPTIONS_SPREAD_MAX_PCT_PUT` | 0.25 | V6.9: Relaxed for PUT spreads |

**Config Parameters:**
- `OPTIONS_INTRADAY_DTE_MIN` (default: 1, V2.13: was 0)
- `OPTIONS_INTRADAY_DTE_MAX` (default: 5, V2.13: was 1)
- `OPTIONS_INTRADAY_DELTA_TARGET` (default: 0.30)
- `OPTIONS_SWING_DELTA_TARGET` (default: 0.70)
- `OPTIONS_DELTA_TOLERANCE` (default: 0.20)

---

## Exit Rules

### Profit Target

**+60% gain** from entry price (V6.15: raised from 50% for trend riding).

```python
if current_price >= entry_price * 1.60:
    emit_exit_signal("PROFIT_TARGET")
```

**Config Parameter:** `OPTIONS_PROFIT_TARGET_PCT` (default: 0.60)

> **V6.15 Change:** Profit target increased from 50% to 60% to capture more upside in trending markets.

### Stop Loss

Tiered based on entry score (see table above).

```python
stop_pct = get_stop_for_score(entry_score)
if current_price <= entry_price * (1 - stop_pct):
    emit_exit_signal("STOP_LOSS")
```

### Late Day Constraint

After **2:30 PM ET**, only 20% stops are allowed (tightest tier). This prevents holding options with significant theta decay into close.

```python
if current_time >= "14:30" and stop_pct > 0.20:
    stop_pct = 0.20  # Force tight stop
```

**Config Parameter:** `OPTIONS_LATE_DAY_TIME` (default: "14:30")

### Time Exit

Force close by **3:45 PM ET** if still holding. This aligns with the Mean Reversion Engine's forced close time to ensure no intraday positions are held overnight.

**Config Parameters:**
- `OPTIONS_FORCE_EXIT_HOUR` (default: 15)
- `OPTIONS_FORCE_EXIT_MINUTE` (default: 45)

---

## Greeks Monitoring (V2.1)

The Options Engine tracks real-time Greeks for risk management.

### Monitored Greeks

| Greek | Purpose | Alert Threshold |
|-------|---------|-----------------|
| **Delta** | Directional exposure | Delta > 0.70 or < 0.30 |
| **Gamma** | Delta sensitivity | Gamma > 0.10 |
| **Theta** | Time decay | Theta < -$0.15/contract |
| **Vega** | Volatility sensitivity | Vega > 0.20 |

### Greeks Snapshot

```python
@dataclass
class GreeksSnapshot:
    delta: float
    gamma: float
    theta: float
    vega: float
    iv: float
    underlying_price: float
    timestamp: str
```

### Risk Engine Integration

The Risk Engine monitors portfolio-level Greeks exposure:

```python
if abs(portfolio_delta) > MAX_PORTFOLIO_DELTA:
    trigger_delta_hedge_alert()
```

**Config Parameters:**
- `OPTIONS_MAX_DELTA` (default: 0.70)
- `OPTIONS_MAX_GAMMA` (default: 0.10)
- `OPTIONS_MIN_THETA` (default: -0.15)

---

## OCO Order Integration

All options positions use **One-Cancels-Other (OCO)** order pairs for exit management:

1. **Entry**: Place market order for contracts
2. **Immediately after fill**: Submit OCO pair
   - Stop order at stop price
   - Limit order at profit target
3. **On either fill**: Cancel the other leg

See [19 - OCO Manager](19-oco-manager.md) for implementation details.

---

## Spread Order Protection (V2.3.6)

Debit spreads consist of two legs (long and short). IBKR treats these as **separate orders**, which can cause issues:

### The Problem
When submitting a spread:
1. Long leg order submitted → Fills successfully
2. Short leg order submitted → **Rejected** (insufficient margin for naked short)
3. Result: **Orphaned long leg** (naked option position)

IBKR requires ~$10K/contract margin for naked shorts, not spread margin (~$300/contract).

### V2.3.6 Solution

**Pre-Submission Margin Check:**
```python
# Estimate required margin for naked short leg
required_margin = abs(short_qty) * 10_000  # $10K per contract
if required_margin > free_margin:
    self.Log("SPREAD: BLOCKED - Insufficient margin")
    return  # Skip spread entirely
```

**Orphan Leg Cleanup:**
If short leg fails despite margin check:
```python
# In OnOrderEvent when short leg is Invalid
if failed_symbol in self._pending_spread_orders:
    long_leg = self._pending_spread_orders[failed_symbol]
    if self.Portfolio[long_leg].Invested:
        self.MarketOrder(long_leg, -quantity)  # Liquidate orphan
```

**Order Pair Tracking:**
- `_pending_spread_orders: Dict[str, str]` maps short leg → long leg
- On short leg fill: remove from tracking (success)
- On short leg failure: liquidate long leg (cleanup)

---

## Intraday "Sniper" Window (V2.3.6)

The intraday options scanning window was adjusted:

| Parameter | Before V2.3.6 | V2.3.6 | V6.15 |
|-----------|:-------------:|:------:|:-----:|
| Start Time | 10:30 | 10:00 | **10:00** |
| End Time | 15:00 | 15:00 | **15:00** |
| Force Exit | 15:30 | 15:30 | **15:25** |

**Rationale:** The 10:00-10:30 window has highest gamma opportunities. Config defined strategy start times at 10:00, but a hardcoded gatekeeper blocked until 10:30. Removed to capture early momentum.

> **V6.15 Change:** Force exit moved from 15:30 to 15:25 to prevent OCO race conditions.

---

## Data Flow

```
┌─────────────────┐
│  Market Data    │ ─── QQQ price, IV, option chain
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 4-Factor Scoring│ ─── ADX + Momentum + IV + Liquidity
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Entry Decision  │ ─── Score >= 3.0? Direction?
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Contract Select │ ─── ATM, 0-1 DTE, liquidity check
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  TargetWeight   │ ─── Emit to Portfolio Router
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  OCO Manager    │ ─── Stop + Profit target pair
└─────────────────┘
```

---

## Configuration Parameters

### Core Allocation

| Parameter | Default | Description |
|-----------|---------|-------------|
| `OPTIONS_ALLOCATION_MIN` | 0.25 | Minimum allocation to options (25%) |
| `OPTIONS_ALLOCATION_MAX` | 0.30 | Maximum allocation to options (30%) |
| `OPTIONS_SWING_ALLOCATION` | 0.1875 | Swing Mode allocation (18.75%) |
| `OPTIONS_INTRADAY_ALLOCATION` | 0.0625 | Intraday Mode allocation (6.25%) |

### Entry Scoring

| Parameter | Default | Description |
|-----------|---------|-------------|
| `OPTIONS_ENTRY_SCORE_MIN` | 3.0 | Minimum score for entry |
| `OPTIONS_ADX_PERIOD` | 14 | ADX lookback period |
| `OPTIONS_MA_PERIOD` | 200 | Moving average period |
| `OPTIONS_IV_LOOKBACK` | 252 | IV rank lookback (1 year) |

### Spread Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `SPREAD_DTE_MIN` | 14 | Default minimum DTE (VASS overrides) |
| `SPREAD_DTE_MAX` | 45 | Default maximum DTE (VASS overrides) |
| `SPREAD_SHORT_LEG_BY_WIDTH` | True | V2.4.3: Width-based short leg selection |
| `SPREAD_WIDTH_MIN` | 5.0 | Minimum spread width (V6.10: $5, was $2) |
| `SPREAD_WIDTH_MAX` | 10.0 | Maximum spread width ($10) |
| `SPREAD_WIDTH_TARGET` | 3.0 | Target spread width (V6.8: was 5.0, lower for more matches) |
| `SPREAD_LONG_LEG_DELTA_MIN` | 0.40 | Long leg minimum delta (V6.8: was 0.45) |
| `SPREAD_SHORT_LEG_DELTA_MIN` | 0.10 | Short leg minimum delta |
| `SPREAD_SHORT_LEG_DELTA_MAX` | 0.55 | Short leg maximum delta (V6.8: was 0.52) |

### Exits and Stops

| Parameter | Default | Description |
|-----------|---------|-------------|
| `OPTIONS_PROFIT_TARGET_PCT` | 0.60 | V6.15: Profit target (+60%, was 50%) |
| `OPTIONS_STOP_TIER_1` | 0.20 | Tightest stop (score 3.0-3.25) |
| `OPTIONS_STOP_TIER_2` | 0.22 | Stop for score 3.25-3.5 |
| `OPTIONS_STOP_TIER_3` | 0.25 | Stop for score 3.5-3.75 |
| `OPTIONS_STOP_TIER_4` | 0.30 | Widest stop (score 3.75-4.0) |
| `OPTIONS_LATE_DAY_TIME` | "14:30" | Force tight stops after this |
| `OPTIONS_FORCE_EXIT_HOUR` | 15 | Force close hour (3 PM) |
| `OPTIONS_FORCE_EXIT_MINUTE` | 45 | Force close minute (3:45 PM) |
| `OPTIONS_MAX_SPREAD_PCT` | 0.20 | Block if bid-ask spread > 20% of mid |

### Greeks Monitoring

| Parameter | Default | Description |
|-----------|---------|-------------|
| `OPTIONS_MAX_DELTA` | 0.70 | Delta alert threshold |
| `OPTIONS_MAX_GAMMA` | 0.10 | Gamma alert threshold |
| `OPTIONS_MIN_THETA` | -0.15 | Theta alert threshold |

### Sizing Caps (V2.18)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `SWING_SPREAD_MAX_DOLLARS` | 7500 | Hard cap for swing spreads |
| `INTRADAY_SPREAD_MAX_DOLLARS` | 4000 | Hard cap for intraday spreads |

### Limit Orders (V2.19)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `OPTIONS_USE_LIMIT_ORDERS` | True | Use marketable limit orders |
| `OPTIONS_LIMIT_SLIPPAGE_PCT` | 0.05 | 5% of spread tolerance |

### Safety (V2.4.1)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `SWING_FALLBACK_ENABLED` | False | No naked fallback on spread failure |
| `FRIDAY_FIREWALL_ENABLED` | True | Close swing options on Friday |
| `FRIDAY_FIREWALL_VIX_CLOSE_ALL` | 25 | VIX > 25: close all swing |

---

## Implementation Notes

### File Location

`engines/satellite/options_engine.py`

### Key Classes

| Class | Purpose |
|-------|---------|
| `OptionsEngine` | Main engine class |
| `EntryScore` | 4-factor score breakdown |
| `OptionContract` | Contract details with Greeks |
| `OptionDirection` | CALL or PUT enum |

### Dependencies

- `engines/core/risk_engine.py` (Greeks monitoring)
- `execution/oco_manager.py` (exit orders)
- `config.py` (all parameters)

---

## Intraday Mode (0-2 DTE) - Micro Regime Engine

### Allocation: 6.25% of Portfolio

Intraday Mode uses the **Micro Regime Engine** to determine optimal strategy based on VIX conditions.

### Why VIX Direction Matters

**Key Insight**: VIX level alone is insufficient. VIX direction determines whether mean reversion or momentum works.

```
VIX at 25 and FALLING = Recovery starting, FADE the move (buy calls)
VIX at 25 and RISING = Fear building, RIDE the move (buy puts)

Same VIX level → OPPOSITE strategies!
```

### VIX Direction Classification

| Direction | VIX Change (15min) | Score | Implication |
|-----------|:------------------:|:-----:|-------------|
| FALLING_FAST | < -3.0% | +2 | Strong recovery |
| FALLING | -1.0% to -3.0% | +1 | Recovery starting |
| STABLE | -1.0% to +1.0% | 0 | Range-bound (V6.6: narrowed from +/-2%) |
| RISING | +1.0% to +3.0% | -1 | Fear building |
| RISING_FAST | +3.0% to +6.0% | -2 | Panic emerging |
| SPIKING | > +10.0% | -3 | Crash mode |
| WHIPSAW | 5+ reversals/hour | 0 | No direction |

### VIX-Adaptive STABLE Band (V6.9/V6.13.1)

The STABLE zone uses adaptive thresholds based on current VIX level to reduce "Dir=NONE" signals.

| VIX Level | STABLE Band | Rationale |
|-----------|:-----------:|-----------|
| VIX < 15 (Low) | +/-0.2% | V6.13.1: Very narrow band captures more signals |
| VIX 15-25 (Medium) | Linear interpolation | Gradual transition |
| VIX > 25 (High) | +/-0.7% | V6.13.1: Wider band still allows trades |

> **V6.13.1 Change**: Narrowed bands from 0.3%/1.0% to 0.2%/0.7% to reduce Dir=NONE frequency.

### 21 Micro-Regime Matrix (V6.5 Complete)

VIX Level × VIX Direction = **21 distinct trading regimes**. Each regime maps to a specific strategy and direction.

> **V6.5 Update**: Fixed divergence/confirmation logic. Added `DEBIT_MOMENTUM` strategy. WORSENING regimes now always use ITM PUT.

#### V6.5 Core Logic: Divergence vs Confirmation

```
DIVERGENCE (QQQ and VIX opposite) = Weak move → FADE (mean reversion)
  • QQQ UP + VIX RISING = weak rally → BUY PUT
  • QQQ DOWN + VIX FALLING = weak dip → BUY CALL

CONFIRMATION (QQQ and VIX aligned) = Strong move → MOMENTUM (ride trend)
  • QQQ UP + VIX FALLING = confirmed rally → BUY CALL
  • QQQ DOWN + VIX RISING = confirmed selloff → BUY PUT
```

#### Complete 21-Regime Matrix with CALL/PUT Direction

```
                              VIX DIRECTION
              ┌──────────────┬──────────────┬──────────────┬──────────────┬──────────────┬──────────────┬──────────────┐
              │ FALLING_FAST │   FALLING    │    STABLE    │    RISING    │ RISING_FAST  │   SPIKING    │   WHIPSAW    │
              │   (< -5%)    │  (-5% to -2%)│    (±2%)     │ (+2% to +5%) │ (+5% to +10%)│   (> +10%)   │  (choppy)    │
┌─────────────┼──────────────┼──────────────┼──────────────┼──────────────┼──────────────┼──────────────┼──────────────┤
│             │  PERFECT_MR  │   GOOD_MR    │    NORMAL    │  CAUTION_LOW │  TRANSITION  │ RISK_OFF_LOW │  CHOPPY_LOW  │
│     LOW     ├──────────────┼──────────────┼──────────────┼──────────────┼──────────────┼──────────────┼──────────────┤
│   (VIX<20)  │ QQQ↑: CALL   │ QQQ↑: CALL   │ QQQ↑: PUT    │              │              │  PROTECTIVE  │              │
│             │ QQQ↓: CALL   │ QQQ↓: CALL   │ QQQ↓: CALL   │   NO_TRADE   │   NO_TRADE   │  PUTS ONLY   │   NO_TRADE   │
│             │  ✅ TRADEABLE │  ✅ TRADEABLE │  ✅ TRADEABLE │  ⚠️ CAUTION   │  ⚠️ CAUTION   │  🔴 DANGER   │  ⚠️ CAUTION   │
├─────────────┼──────────────┼──────────────┼──────────────┼──────────────┼──────────────┼──────────────┼──────────────┤
│             │  RECOVERING  │   IMPROVING  │   CAUTIOUS   │   WORSENING  │ DETERIORATING│   BREAKING   │   UNSTABLE   │
│   MEDIUM    ├──────────────┼──────────────┼──────────────┼──────────────┼──────────────┼──────────────┼──────────────┤
│  (VIX20-25) │ QQQ↑: CALL   │ QQQ↑: CALL   │              │ QQQ↑: PUT    │ QQQ↑: CALL   │  PROTECTIVE  │  PROTECTIVE  │
│             │ QQQ↓: CALL   │ QQQ↓: CALL   │   NO_TRADE   │ QQQ↓: PUT    │ QQQ↓: PUT    │  PUTS ONLY   │  PUTS ONLY   │
│             │  ✅ TRADEABLE │  ✅ TRADEABLE │  ⚠️ CAUTION   │  ✅ ITM PUT   │  ✅ ITM ONLY  │  🔴 DANGER   │  🔴 DANGER   │
├─────────────┼──────────────┼──────────────┼──────────────┼──────────────┼──────────────┼──────────────┼──────────────┤
│             │ PANIC_EASING │   CALMING    │   ELEVATED   │WORSENING_HIGH│  FULL_PANIC  │    CRASH     │   VOLATILE   │
│    HIGH     ├──────────────┼──────────────┼──────────────┼──────────────┼──────────────┼──────────────┼──────────────┤
│   (VIX>25)  │ QQQ↑: CALL   │ QQQ↑: CALL   │ QQQ↑: CALL   │ QQQ↑: PUT    │  PROTECTIVE  │  PROTECTIVE  │  PROTECTIVE  │
│             │ QQQ↓: CALL   │ QQQ↓: CALL   │ QQQ↓: PUT    │ QQQ↓: PUT    │  PUTS ONLY   │  PUTS ONLY   │  PUTS ONLY   │
│             │  ✅ TRADEABLE │  ✅ TRADEABLE │  ✅ ITM ONLY  │  ✅ ITM PUT   │  🔴 DANGER   │  🔴 DANGER   │  🔴 DANGER   │
└─────────────┴──────────────┴──────────────┴──────────────┴──────────────┴──────────────┴──────────────┴──────────────┘
```

#### Quick Reference: All 21 Regimes

| Regime | VIX Level + Dir | QQQ UP | QQQ DOWN | Strategy | Notes |
|--------|-----------------|:------:|:--------:|----------|-------|
| **PERFECT_MR** | LOW + FALL_FAST | CALL | CALL | DEBIT_FADE/MOMENTUM | Best MR conditions |
| **GOOD_MR** | LOW + FALLING | CALL | CALL | DEBIT_FADE/MOMENTUM | Good MR conditions |
| **NORMAL** | LOW + STABLE | PUT | CALL | DEBIT_FADE (small) | Stable fade |
| **CAUTION_LOW** | LOW + RISING | — | — | NO_TRADE | Skip |
| **TRANSITION** | LOW + RISE_FAST | — | — | NO_TRADE | Skip |
| **RISK_OFF_LOW** | LOW + SPIKING | PUT | PUT | PROTECTIVE_PUTS | Danger |
| **CHOPPY_LOW** | LOW + WHIPSAW | — | — | NO_TRADE | Skip |
| **RECOVERING** | MED + FALL_FAST | CALL | CALL | DEBIT_FADE/MOMENTUM | Recovery starting |
| **IMPROVING** | MED + FALLING | CALL | CALL | DEBIT_FADE/MOMENTUM | Conditions improving |
| **CAUTIOUS** | MED + STABLE | — | — | NO_TRADE | Grind-Up exception |
| **WORSENING** | MED + RISING | **PUT** | **PUT** | ITM_MOMENTUM | V6.5: Always PUT |
| **DETERIORATING** | MED + RISE_FAST | CALL | PUT | ITM_MOMENTUM | ITM follows QQQ |
| **BREAKING** | MED + SPIKING | PUT | PUT | PROTECTIVE_PUTS | Danger |
| **UNSTABLE** | MED + WHIPSAW | PUT | PUT | PROTECTIVE_PUTS | Danger |
| **PANIC_EASING** | HIGH + FALL_FAST | CALL | CALL | DEBIT_FADE/MOMENTUM | Panic subsiding |
| **CALMING** | HIGH + FALLING | CALL | CALL | DEBIT_FADE/MOMENTUM | Fear decreasing |
| **ELEVATED** | HIGH + STABLE | CALL | PUT | ITM_MOMENTUM | ITM follows QQQ |
| **WORSENING_HIGH** | HIGH + RISING | **PUT** | **PUT** | ITM_MOMENTUM | V6.5: Always PUT |
| **FULL_PANIC** | HIGH + RISE_FAST | PUT | PUT | PROTECTIVE_PUTS | Danger |
| **CRASH** | HIGH + SPIKING | PUT | PUT | PROTECTIVE_PUTS | Danger |
| **VOLATILE** | HIGH + WHIPSAW | PUT | PUT | PROTECTIVE_PUTS | Danger |

#### V6.5 Key Insights

| VIX Direction | Bias | Logic |
|---------------|------|-------|
| 🟢 **VIX FALLING** | CALL bias | Fear decreasing = bullish. Confirmation (QQQ↑) or Divergence (QQQ↓) both favor CALL |
| 🔴 **VIX RISING** | PUT bias | Fear increasing = bearish. WORSENING regimes ALWAYS use PUT regardless of QQQ |
| ⚪ **VIX STABLE** | Small fade | No conviction. QQQ UP → small PUT fade, QQQ DOWN → small CALL fade |

**Key Insight**: 6 of 21 regimes result in **DANGER/NO TRADE** (SPIKING, WHIPSAW in MEDIUM/HIGH VIX). This is by design—some market conditions have no edge.

### Micro Score Calculation

The Micro Regime Engine calculates a **composite score** (range: -15 to 80) to determine trade eligibility and sizing.

#### Score Components

| Component | Score Range | Calculation |
|-----------|:-----------:|-------------|
| **VIX Level** | 0-25 pts | Lower VIX = higher score |
| **VIX Direction** | -10 to +20 pts | Falling = positive, Spiking = negative |
| **QQQ Move** | 0-20 pts | Sweet spot at 0.8-1.25% move |
| **Move Velocity** | 0-15 pts | Gradual moves score higher than spikes |

#### VIX Level Scoring

```
VIX < 15:     25 points (ideal conditions)
VIX 15-20:    20 points
VIX 20-25:    15 points
VIX 25-30:    10 points
VIX 30-40:     5 points
VIX > 40:      0 points
```

#### VIX Direction Scoring

```
FALLING_FAST:  +20 points (strong recovery)
FALLING:       +10 points (recovery)
STABLE:         0 points (neutral)
RISING:        -5 points (fear building)
RISING_FAST:  -10 points (panic)
SPIKING:      -10 points + DANGER FLAG
WHIPSAW:       -5 points + WHIPSAW FLAG
```

#### Score Interpretation

| Score Range | Action | Allocation Multiplier |
|:-----------:|--------|:---------------------:|
| 60+ | Strong entry | 100% of regime allocation |
| 40-59 | Normal entry | 75% of regime allocation |
| 20-39 | Cautious entry | 50% of regime allocation |
| 0-19 | Skip or minimal | 25% or skip |
| < 0 | **NO TRADE** | 0% |

---

### UVXY Conviction Thresholds (V6.15)

The Micro Regime Engine uses UVXY intraday moves to override macro direction when conviction is extreme.

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `MICRO_UVXY_BEARISH_THRESHOLD` | +2.0% | V6.15: UVXY rise triggers PUT conviction (was +2.8%) |
| `MICRO_UVXY_BULLISH_THRESHOLD` | -4.0% | V6.15: UVXY drop triggers CALL conviction (was -4.5%) |
| `MICRO_UVXY_CONVICTION_EXTREME` | 3.0% | V6.15: NEUTRAL VETO threshold (was 3.5%) |
| `MICRO_SCORE_BULLISH_CONFIRM` | 48 | V6.15: Score threshold for bullish confirmation (was 47) |
| `MICRO_SCORE_BEARISH_CONFIRM` | 47 | V6.15: Score threshold for bearish confirmation (was 49) |
| `INTRADAY_QQQ_FALLBACK_MIN_MOVE` | 0.12% | V6.15: QQQ move for STABLE direction fallback (was 0.30%) |

**Evolution of UVXY Thresholds:**
- V6.4: +/-5% (too wide, missed many signals)
- V6.6: +/-3% (better but still restrictive)
- V6.10: +2.5%/-3% (asymmetric for better PUT signals)
- V6.14: +2.8%/-4.5% (tuned for reduced CALL bias)
- V6.15: +2.0%/-4.0% (further refinement for balanced signals)

---

### VIX Monitoring System

**IMPORTANT**: We use VIX only, NOT VIX1D.

VIX1D was evaluated and rejected because:
1. VIX and VIX1D move together during trading hours (0.95 correlation)
2. VIX1D only diverges at market open (9:30-10:00 AM)
3. Our trading window starts at 10:00 AM - divergence already resolved
4. Adding VIX1D increases complexity without actionable benefit

### Tiered VIX Monitoring

| Layer | Check Frequency | Purpose |
|-------|-----------------|---------|
| Layer 1 | 5 minutes | Spike detection (VIX change > 3%) |
| Layer 2 | 15 minutes | Direction confirmation |
| Layer 3 | 60 minutes | Whipsaw detection (5+ reversals) |
| Layer 4 | 30 minutes | Full regime recalculation |

---

### Intraday Strategy Deployment (V6.5)

Based on micro regime, deploy one of five intraday strategies:

#### Strategy 1: Debit Fade (Divergence - Mean Reversion)

**When**: QQQ and VIX moving in OPPOSITE directions (divergence = weak move)
**Setup**: Fade the weak move with opposite direction option
**Target**: Mean reversion to VWAP
**Max Allocation**: 3%

```
V6.5 DIVERGENCE LOGIC:
  • QQQ UP + VIX RISING = weak rally → BUY PUT (fade)
  • QQQ DOWN + VIX FALLING = weak dip → BUY CALL (fade)

Trigger: tradeable_regime AND qqqMove >= 0.15% AND vix >= 13.5 AND vix < 25
Strategy: OTM debit spread, 0-1 DTE
Exit: +30% profit OR return to VWAP OR 15:30 time stop
```

> **V6.8 Change**: Lowered `INTRADAY_DEBIT_FADE_VIX_MIN` from 13.5 to 11.5 to allow trades in low VIX bull markets. Also reduced `INTRADAY_DEBIT_FADE_MIN_SCORE` from 45 to 35 and widened `INTRADAY_FADE_MAX_MOVE` from 1.20% to 1.50% to capture more opportunities.

#### Strategy 1b: Debit Momentum (Confirmation - Trend Following) [V6.5 NEW]

**When**: QQQ and VIX moving in ALIGNED directions (confirmation = strong move)
**Setup**: Ride the confirmed trend with same direction option
**Target**: Momentum continuation
**Max Allocation**: 3%

```
V6.5 CONFIRMATION LOGIC:
  • QQQ UP + VIX FALLING = confirmed rally → BUY CALL (ride)
  • QQQ DOWN + VIX RISING = confirmed selloff → BUY PUT (ride)

Trigger: tradeable_regime AND qqqMove >= 0.15% AND vix >= 13.5 AND vix < 25
Strategy: OTM debit spread, 0-1 DTE
Exit: +30% profit OR trailing stop OR 15:30 time stop
```

#### Strategy 2: Credit Spreads (Range-Bound)

**When**: VIX STABLE or WHIPSAW regimes
**Setup**: Sell OTM credit spread (iron condor)
**Target**: Collect theta decay in range
**Max Allocation**: 2%

```
Trigger: vixDirection in (STABLE, WHIPSAW) AND vix >= 18
Strategy: 10-delta wings, 0-1 DTE
Exit: 50% of max profit OR breach of short strike
```

#### Strategy 3: ITM Momentum (V6.5 Updated)

**When**: VIX elevated + clear directional move (or VIX RISING regimes)
**Setup**: Buy ITM option (0.70 delta) - reduced vega exposure
**Target**: Ride momentum continuation
**Max Allocation**: 3%

```
V6.5 ITM MOMENTUM REGIMES:
  • WORSENING (MED + RISING): Always ITM PUT regardless of QQQ direction
  • WORSENING_HIGH (HIGH + RISING): Always ITM PUT regardless of QQQ direction
  • DETERIORATING (MED + RISE_FAST): ITM follows QQQ direction
  • ELEVATED (HIGH + STABLE): ITM follows QQQ direction

Trigger: momentum_regime AND vix >= INTRADAY_ITM_MIN_VIX AND qqqMove >= 0.8%
Strategy: ITM option (delta 0.70), 0-1 DTE
Exit: +25% profit OR trailing stop after +15%
```

> **V6.5 Change**: WORSENING and WORSENING_HIGH regimes now ALWAYS use ITM PUT regardless of QQQ direction. Rationale: VIX rising = fear is real/building, so bearish bias is correct even if QQQ is temporarily up (bear market rally).

#### Strategy 4: Protective Puts

**When**: VIX SPIKING or CRISIS conditions
**Setup**: Buy OTM puts for portfolio protection
**Target**: Hedge existing long exposure
**Max Allocation**: 2%

```
Trigger: vixDirection in (SPIKING, RISING_FAST) AND vix >= 30
Strategy: 5% OTM puts, 1-2 DTE
Exit: Crisis resolution OR expiry
```

### Intraday Force Exit (15:25 ET)

**All intraday options positions MUST close by 15:25 ET** (not 15:45 like Mean Reversion). This gives:
- 35 minutes before market close
- Time to handle any execution issues
- Avoids overnight gamma risk on 0-DTE
- Prevents OCO race conditions near market close

> **V6.15 Change:** Moved from 15:30 to 15:25 to avoid OCO recovery race at force-close window. `INTRADAY_OPTIONS_OFFSET_MINUTES` set to 35 to align with this fallback time.

---

## V6.10 Assignment Prevention System

V6.10 introduces a comprehensive 4-layer defense against option assignment risk, which caused -$150,953 in losses across 2015, 2017, 2021-2022 backtests.

### Layer 1: Mandatory DTE=1 Force Close

**All spread positions close when DTE reaches 1** (day before expiration), regardless of P&L.

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `SPREAD_FORCE_CLOSE_DTE` | 1 | Close at DTE=1 (nuclear option) |
| `SPREAD_FORCE_CLOSE_ENABLED` | True | Master switch |

### Layer 2: Pre-Market ITM Check (09:25 ET)

Check all short legs before market open to catch overnight gaps. If short leg went ITM overnight, queue for immediate close at 09:30.

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `PREMARKET_ITM_CHECK_ENABLED` | True | Enable 09:25 check |
| `PREMARKET_ITM_CHECK_HOUR` | 9 | Check hour |
| `PREMARKET_ITM_CHECK_MINUTE` | 25 | Check minute |

### Layer 3: Tighter ITM Exit Threshold

Exit spread immediately when short leg goes ITM by threshold (at ANY DTE, not just near expiry).

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `SHORT_LEG_ITM_EXIT_THRESHOLD` | 0.005 | Exit at 0.5% ITM (was 1%) |
| `SHORT_LEG_ITM_EXIT_ENABLED` | True | Master switch |

### Layer 4: Wider Spread Width

Wider spreads survive larger overnight gaps and reduce assignment risk.

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `SPREAD_WIDTH_MIN` | 5.0 | Minimum $5 width (was $3) |
| `SPREAD_WIDTH_TARGET` | 5.0 | Target $5 width (was $3) |

### Assignment Prevention Summary

```
Timeline of Protection:
09:25 ET → Layer 2: Pre-market ITM check (catch overnight gaps)
Intraday → Layer 3: ITM exit at 0.5% threshold (real-time monitoring)
DTE = 1  → Layer 1: Force close ALL spreads (nuclear option)
All DTE  → Layer 4: Wider spreads survive larger gaps
```

---

## V6.10 Choppy Market Filter

Detects whipsawing markets and reduces position size to limit losses. Added after 2015 backtest showed 48% win rate but negative P&L due to choppy conditions.

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `CHOPPY_MARKET_FILTER_ENABLED` | True | Enable filter |
| `CHOPPY_REVERSAL_COUNT` | 3 | Reversals to trigger |
| `CHOPPY_LOOKBACK_HOURS` | 2 | Lookback window |
| `CHOPPY_SIZE_REDUCTION` | 0.50 | 50% size reduction |
| `CHOPPY_MIN_MOVE_PCT` | 0.003 | Min 0.3% move (filter noise) |

---

## V6.14 Pre-Market VIX Shock Ladder

A 3-tier pre-market guard that uses CBOE VIX level + UVXY overnight gap proxy to de-risk before market open. This shared guard applies across all options modes.

### Tier Levels

| Level | VIX Threshold | UVXY Gap | Actions |
|:-----:|:-------------:|:--------:|---------|
| **L1** (Elevated) | VIX >= 22 | Gap >= 4% | Reduce size to 75%, no forced exits |
| **L2** (High Stress) | VIX >= 28 | Gap >= 7% | Block CALLs until 11:00, close bullish options, 50% size |
| **L3** (Panic) | VIX >= 35 | Gap >= 12% | Block ALL entries until 12:00, flatten all options, 25% size |

### Configuration

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `PREMARKET_VIX_LADDER_ENABLED` | True | Master switch |
| `PREMARKET_VIX_L1_LEVEL` | 22.0 | L1 VIX threshold |
| `PREMARKET_VIX_L1_GAP_PCT` | 4.0 | L1 UVXY gap threshold |
| `PREMARKET_VIX_L2_LEVEL` | 28.0 | L2 VIX threshold |
| `PREMARKET_VIX_L2_GAP_PCT` | 7.0 | L2 UVXY gap threshold |
| `PREMARKET_VIX_L3_LEVEL` | 35.0 | L3 VIX threshold |
| `PREMARKET_VIX_L3_GAP_PCT` | 12.0 | L3 UVXY gap threshold |
| `PREMARKET_VIX_L1_SIZE_MULT` | 0.75 | L1 size multiplier |
| `PREMARKET_VIX_L2_SIZE_MULT` | 0.50 | L2 size multiplier |
| `PREMARKET_VIX_L3_SIZE_MULT` | 0.25 | L3 size multiplier |

### Entry Block Windows

| Level | CALL Block Until | Entry Block Until |
|:-----:|:----------------:|:-----------------:|
| L2 | 11:00 ET | - |
| L3 | - | 12:00 ET |

---

## 18.1 State Cleanup Completeness (V2.29/V2.30)

### 18.1.1 clear_all_positions()

The `clear_all_positions()` method now clears ALL pending entry state fields (16 total). V2.29 added 6 fields, V2.30 added 3 more, preventing ghost pending state after kill switch or governor shutdown:

**Complete field list cleared (16 fields):**
- `_spread_position` → None
- `_swing_position` → None
- `_pending_spread_long_leg` → None
- `_pending_spread_short_leg` → None
- `_pending_spread_orders` → {}
- `_pending_intraday_entry` → None
- `_pending_spread_type` → None (V2.29 fix)
- `_pending_net_debit` → None (V2.29 fix)
- `_pending_max_profit` → None (V2.29 fix)
- `_pending_num_contracts` → None (V2.29 fix)
- `_pending_entry_score` → None (V2.29 fix)
- `_pending_stop_pct` → None (V2.29 fix)
- `_entry_attempted_today` → False (V2.29 fix)
- `_pending_stop_price` → None (V2.30 fix)
- `_pending_target_price` → None (V2.30 fix)
- `_pending_intraday_exit` → None (V2.30 fix)

### 18.1.2 cancel_pending_spread_entry() and cancel_pending_intraday_entry()

These two methods are now called in ALL liquidation paths:
- Kill switch Tier 3 (full liquidation)
- Governor shutdown (`_liquidate_all_spread_aware`)
- Margin call circuit breaker

This prevents stale pending state from blocking new entries after a crisis recovery.

---

## Related Sections

- [12 - Risk Engine](12-risk-engine.md) - Greeks monitoring integration
- [19 - OCO Manager](19-oco-manager.md) - Exit order management
- [11 - Portfolio Router](11-portfolio-router.md) - Order authorization
- [16 - Appendix: Parameters](16-appendix-parameters.md) - All config values
- [v2-specs/V2_1_OPTIONS_ENGINE_DESIGN.txt](v2-specs/V2_1_OPTIONS_ENGINE_DESIGN.txt) - Full V2.1.1 specification

---

[Previous: 17 - Appendix: Glossary](17-appendix-glossary.md) | [Table of Contents](00-table-of-contents.md) | [Next: 19 - OCO Manager](19-oco-manager.md)
