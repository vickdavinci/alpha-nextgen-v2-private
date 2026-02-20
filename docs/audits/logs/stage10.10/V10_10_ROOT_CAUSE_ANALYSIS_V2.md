# V10.10 Root Cause Analysis v2 (Current-Code Validated)

**Date:** 2026-02-20  
**Scope:** Revalidated against current `main.py`, `config.py`, `engines/satellite/options_engine.py`, `engines/satellite/itm_horizon_engine.py`, `engines/satellite/vass_entry_engine.py`, `engines/satellite/iv_sensor.py`, `main_options_mixin.py`  
**Intent:** Replace stale assumptions in v1 with code-true causes only.

---

## Executive Summary

The current losses are still driven by a combination of:

1. **VASS directional conviction not firing enough in low/normal VIX**, combined with expensive low-IV routing.
2. **ITM overnight risk architecture** using premium-percentage stops on multi-day options.
3. **MICRO throughput and expectancy pressure** from strict gating + single-slot contention + OTM profile calibration.
4. **Cross-engine contention** in intraday capacity/cooldowns.

Key stale findings from v1 were removed:

- **VASS bullish default under neutral/no-conviction is no longer true.**
- **VASS EOD time-window blocking is no longer true for EOD scans.**
- Some numeric claims in v1 are now historical, not representative of current config.

---

## What Is Confirmed (Code-True)

### 1) VASS: Conviction and Low-IV Routing Still Suppress Quality Participation

#### A. Conviction thresholds remain very strict
- `VASS_VIX_5D_BULLISH_THRESHOLD = -0.20`
- `VASS_VIX_5D_BEARISH_THRESHOLD = +0.16`
- Source: `config.py:870-871`
- Applied in: `engines/satellite/iv_sensor.py:210-248`

In calm markets these thresholds under-fire, so directional confidence remains sparse.

#### B. Low-IV DTE is still long-dated
- `VASS_LOW_IV_DTE_MIN/MAX = 30/45`
- Source: `config.py:856-857`
- Routing in: `engines/satellite/vass_entry_engine.py:37-56`

This can reduce responsiveness and execution quality for swing captures in calm IV regimes.

#### C. Entry quality remains tight (can choke fills)
- Adaptive debit/width caps remain restrictive in practice when chains are expensive/wide.
- Source knobs: `config.py:1370-1372` and checks in `engines/satellite/options_engine.py:5148-5160`.

**Current diagnosis:** VASS is not direction-default broken now, but **conviction sensitivity + low-IV product routing + tight quality gates** can still under-convert good opportunities.

---

### 2) ITM: Multi-Day Structure Still Uses Premium-% Stop Architecture

#### A. Stop logic is still premium-based
- Stop set as `premium * (1 - stop_pct)`
- Source: `engines/satellite/options_engine.py:3277`

For multi-day ITM options, this remains vulnerable to combined gap/theta path-dependency.

#### B. ITM filter stack is active and broad
- Trend band (`ITM_SMA_BAND_PCT`), ADX, VIX bounds, re-entry, breakers, optional DD gate.
- Evaluation: `engines/satellite/itm_horizon_engine.py:173-247`
- Current config includes:
  - `ITM_SMA_BAND_PCT = 0.012` (`config.py:2003`)
  - `ITM_ADX_MIN = 15` (`config.py:2004`)
  - `ITM_CALL_MAX_VIX = 22`, `ITM_PUT_MIN_VIX = 14` (`config.py:2005,2008`)

#### C. ITM currently runs with DD gate disabled
- `ITM_DD_GATE_ENABLED = False`
- Source: `config.py:2015`

**Current diagnosis:** ITM losses are still most likely from **stop architecture + market path noise**, not from missing entry gates.

---

### 3) MICRO: Throughput and Expectancy Still Constrained

#### A. OTM momentum remains hard-capped by VIX gate
- `MICRO_OTM_MOMENTUM_MAX_VIX = 22.0`
- Source: `config.py:1954`
- Applied in signal routing: `engines/satellite/options_engine.py:1125-1133`

#### B. Strategy-aware DTE routing exists and is correct
- `MICRO_DEBIT_FADE`: 0-2 DTE
- `MICRO_OTM_MOMENTUM`: 0-1 DTE
- Source: `main_options_mixin.py:91-106`, config `1866-1871`

#### C. OTM profile still carries fragile payoff profile in chop
- Target/stop/trail currently tuned but still sensitive to intraday gamma reversals.
- Source: `config.py:1920-1924`, exit logic in `engines/satellite/options_engine.py:7390-7487` and `7528-7588`.

**Current diagnosis:** MICRO is no longer miswired, but still faces **strict gating + non-trivial intraday gamma path risk**.

---

### 4) Cross-Engine Interaction: Contention Still Real

#### A. Shared intraday slot remains single-cap
- `OPTIONS_MAX_INTRADAY_POSITIONS = 1`
- Source: `config.py:1652`, checked by `engines/satellite/options_engine.py:2732-2736`

#### B. ITM promotion path from NO_TRADE still exists
- Explicit override can map NO_TRADE state into ITM path when enabled and direction exists.
- Source: `engines/satellite/options_engine.py:8240-8251`

This is explicit logic, not accidental leakage, but it still increases coupling and contention.

---

## What Was Removed from v1 (Stale / Incorrect)

1. **“VASS defaults to bullish on no-conviction + neutral macro.”**
   - Not true now.
   - Resolver returns no-trade when engine direction is `None` and macro is neutral.
   - Source: `engines/satellite/options_engine.py:2198-2210`

2. **“VASS always blocked by TIME_WINDOW at EOD scan.”**
   - Not true now.
   - EOD scan bypasses swing time-window enforcement.
   - Source: `engines/satellite/options_engine.py:8130`

3. **“VASS medium fallback hardcoded to 7/21 DTE.”**
   - Not true now.
   - Uses config medium DTE values.
   - Source: `engines/satellite/vass_entry_engine.py:82-88`

4. **Old trail-formula failure claims.**
   - Incorrect formula in earlier audit.
   - Current trail uses `peak - (peak-entry)*trail_pct`.
   - Source: `engines/satellite/options_engine.py:7556`

---

## Current Root-Cause Ranking (v2)

### P0 (highest impact)
1. **ITM stop architecture mismatch for multi-day option path risk** (premium-% stop under gap/theta sequence).  
2. **VASS conviction sensitivity + low-IV long-DTE routing suppresses actionable directional exposure.**  
3. **Shared intraday slot/coupling creates engine contention and signal starvation.**

### P1
4. **MICRO OTM expectancy fragile in chop despite routing fixes.**  
5. **VASS entry quality gates can over-filter in live chain conditions (debit/width, liquidity interactions).**

### P2
6. Telemetry/readability gaps still make RCA slower when regimes shift quickly (not a hard blocker, but still expensive operationally).

---

## Minimal Fix Direction (No Over-Engineering)

1. **ITM:** keep direction gates, but harden risk architecture (overnight-aware stop policy that is not purely premium-%).  
2. **VASS:** relax conviction sensitivity modestly for calm VIX and reduce low-IV DTE inertia (without removing risk filters).  
3. **Capacity:** split intraday slots so ITM and MICRO do not cannibalize each other.  
4. **MICRO:** keep 0-1/0-2 DTE routing; tune OTM R:R only after slot/coupling and VASS participation are stabilized.

---

## Final Verdict

V10.10 is **not broken by one single bug anymore**. The current underperformance is a **systems-level interaction problem**:

- ITM risk architecture is still too fragile for the holding horizon.
- VASS participation quality is constrained by conviction and low-IV routing design.
- MICRO performance is heavily path-dependent and capacity-constrained.

This is fixable, but requires **sequenced, minimal structural corrections** rather than broad parameter churn.

---

## V10.11 Applied Config Decisions (Agreed)

### Applied now
- `BEAR_PUT_ENTRY_MIN_OTM_PCT_RELAXED: 0.015 -> 0.010`
  - Reason: unblock bearish debit participation while keeping assignment gate active.
- `SPREAD_MAX_DEBIT_TO_WIDTH_PCT_LOW_VIX: 0.46 -> 0.38`
- `SPREAD_MAX_DEBIT_TO_WIDTH_PCT_MED_VIX: 0.44 -> 0.36`
- `SPREAD_MAX_DEBIT_TO_WIDTH_PCT_HIGH_VIX: 0.40 -> 0.34`
  - Reason: improve VASS entry quality / R:R discipline.
- `SPREAD_PROFIT_TARGET_PCT: 0.48 -> 0.40`
  - Reason: improve target hit-rate without removing hold behavior.

### Explicitly not changed
- `SPREAD_MIN_HOLD_MINUTES` kept at `5760` (4-day minimum hold).
  - Reason: prior evidence from V9 / early V10 showed longer-held spreads can drive profits; hold concept retained.
- `ITM_TARGET_PCT` and `ITM_STOP_PCT` kept at `0.40/0.30`.
  - Reason: avoid over-tightening multi-day ITM exits.
- MICRO regime universe not narrowed to only `GOOD_MR`.
  - Reason: would over-choke signal flow.
