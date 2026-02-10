# V6.16 YOLO Implementation Change Log

Date: 2026-02-10

## Scope

This document records the code changes implemented to harden options execution, reduce close-order amplification, improve symbol handling consistency, and tune participation/risk controls from recent multi-regime audits.

## Files Changed

- `main.py`
- `portfolio/portfolio_router.py`
- `engines/satellite/options_engine.py`
- `config.py`

## Implemented Fixes

### 1) Intraday force-close idempotency and quantity safety

File: `main.py`

- Added close guards:
  - `_intraday_close_in_progress_symbols`
  - `_intraday_force_exit_submitted_symbols`
- Added helper normalization and holdings functions:
  - `_normalize_symbol_str(symbol)`
  - `_get_option_holding_quantity(symbol)`
- Updated both force-close paths (`_intraday_force_exit_fallback`, `_on_intraday_options_force_close`) to:
  - normalize symbol
  - prevent duplicate close submits per symbol/day
  - set `requested_quantity` from live holdings before sending close signal
- Added guard reconciliation:
  - `_reconcile_intraday_close_guards()` called in `OnData`
  - clears stale in-progress flags once holdings are flat
- OCO recovery hardening:
  - skip OCO recovery if a close is already in progress
  - skip OCO recovery near force-close window (config-driven cutoff)

Expected impact:
- Prevents repeated same-symbol close submissions
- Prevents stale quantity close orders from amplifying exposure
- Reduces race conditions between OCO recovery and force-close

### 2) Router close-side correctness for options

File: `portfolio/portfolio_router.py`

- In `calculate_order_intents`, for option close intents:
  - derive side and size from live holdings only
  - skip close intent if no live holding exists
  - bypass stale delta-derived side for close path

Expected impact:
- Prevents incorrect close side (for example BUY on long close)
- Reduces accidental position increases during close workflows

### 3) Symbol type normalization across options signals

Files: `main.py`, `engines/satellite/options_engine.py`

- Added `_symbol_str` utility in `OptionsEngine`
- Standardized `TargetWeight.symbol` to string in options signal emit paths
- Standardized `spread_short_leg_symbol` metadata to string
- Added normalization in premarket VIX ladder actions and ITM short premarket exits

Expected impact:
- Removes Symbol-vs-string mismatch paths
- Reduces downstream routing/lookup inconsistencies

### 4) Assignment gate tuning for PUT spread participation

Files: `engines/satellite/options_engine.py`, `config.py`

- Added dynamic assignment gate behavior for PUT spread entry:
  - baseline `BEAR_PUT_ENTRY_MIN_OTM_PCT`
  - relaxed threshold only when:
    - VIX is below low-vol threshold
    - regime score is above minimum healthy threshold
- Applied in both debit and credit PUT spread assignment checks
- Logging now reports effective minimum OTM used

Expected impact:
- Keeps assignment protection
- Reduces over-blocking of valid PUT spread candidates in calmer conditions

### 5) Participation and risk tuning updates

File: `config.py`

- Direction/conviction tuning:
  - `MICRO_UVXY_BEARISH_THRESHOLD = 0.020`
  - `MICRO_UVXY_BULLISH_THRESHOLD = -0.040`
  - `INTRADAY_QQQ_FALLBACK_MIN_MOVE = 0.12`
  - `QQQ_NOISE_THRESHOLD = 0.06`
- PUT spread gate tuning:
  - `BEAR_PUT_ENTRY_MIN_OTM_PCT = 0.02`
  - `BEAR_PUT_ENTRY_LOW_VIX_THRESHOLD = 18.0`
  - `BEAR_PUT_ENTRY_MIN_OTM_PCT_RELAXED = 0.015`
  - `BEAR_PUT_ENTRY_RELAXED_REGIME_MIN = 60.0`
- OCO race protection:
  - `OCO_RECOVERY_CUTOFF_MINUTES_BEFORE_FORCE_EXIT = 20`
- Protective puts drag reduction:
  - `PROTECTIVE_PUTS_SIZE_PCT = 0.03`
  - `PROTECTIVE_PUTS_STOP_PCT = 0.35`

Expected impact:
- Better participation in mixed markets
- Faster PUT engagement in bearish transitions
- Reduced insurance drag and fewer deep protective-put losses

## Logging Improvements

File: `main.py`

- Improved dropped-signal coding from generic `DROP_ROUTER_REJECT` to specific codes:
  - `DROP_ENGINE_NO_SIGNAL`
  - `DROP_NO_CONTRACT`
  - `DROP_NO_DIRECTION`
  - existing margin/duplicate checks retained
- Added retry hint in dropped-signal log line

Expected impact:
- Better root-cause visibility for funnel loss between approval and order placement

## Validation Performed

- Syntax compile check:
  - `python3 -m py_compile main.py engines/satellite/options_engine.py portfolio/portfolio_router.py config.py`
  - Result: passed

## Backtest Validation Required (next)

- Re-run 2022 bear regime validation for:
  - close-order duplication elimination
  - assignment/margin error incidence
  - CALL/PUT balance
  - intraday conversion from approved to executed
- Re-run 2015 and 2017 for regression checks:
  - bull participation retained
  - no new safety regressions
