# V6.14 Jul-Sep 2017 Readiness Report (Before 2022 Rerun)

## Scope
- Logs reviewed:
  - `docs/audits/logs/stage6.14/V6_14_Jul_Sep_2017_logs.txt`
  - `docs/audits/logs/stage6.14/V6_14_Jul_Sep_2017_orders.csv`
  - `docs/audits/logs/stage6.14/V6_14_Jul_Sep_2017_trades.csv`
- Goal: identify remaining bugs and minimal optimization changes before rerunning 2022.

## Executive Summary
- Macro regime behavior in 2017 bull period is mostly correct.
- Primary issues are still instrumentation/flow quality, not a single catastrophic logic fault:
  - VASS rejection KPI is inflated by state-based skips.
  - Intraday result accounting is incomplete.
  - Startup gate persisted phase logging appears stale.
- Micro still over-blocks (high `Dir=NONE` and `NO_TRADE`), reducing participation.

## Evidence Snapshot

### Core Counts
- `INTRADAY_SIGNAL_APPROVED = 79`
- `INTRADAY_SIGNAL = 19`
- `INTRADAY_RESULT = 2`
- `VASS_ENTRY = 9`
- `SPREAD: ENTRY_SIGNAL = 9`
- `VASS_REJECTION = 1018`

### Direction/Regime
- `MICRO_UPDATE Dir=NONE = 1575`
- `MICRO_UPDATE Dir=PUT = 141`
- `MICRO_UPDATE Dir=CALL = 104`
- Regime states:
  - `RISK_ON = 155`
  - `NEUTRAL = 22`
  - `CAUTIOUS = 3`
  - `DEFENSIVE = 2`

### VASS Rejection Breakdown
- ReasonCode:
  - `DEBIT_ENTRY_VALIDATION_FAILED = 1018`
- ValidationFail:
  - `HAS_SPREAD_POSITION = 998`
  - `ENTRY_ALREADY_ATTEMPTED_TODAY = 14`
  - `BEAR_PUT_ASSIGNMENT_GATE = 5`
  - `POST_TRADE_MARGIN_COOLDOWN = 1`

Interpretation:
- Most “rejections” are not contract-quality failures; they are state/slot/cooldown skips.
- Current KPI overstates VASS contract-construction failure rate.

### Micro NO_TRADE Breakdown
- `NO_TRADE: MICRO_BLOCK` total: `941`
- Top blockers:
  - `QQQ_FLAT = 364`
  - `REGIME_NOT_TRADEABLE = 353`
  - `CONFIRMATION_FAIL = 168`
  - `VIX_STABLE_LOW_CONVICTION = 56`
- Regime labels inside blocks (top):
  - `CAUTION_LOW = 506`
  - `GOOD_MR = 190`
  - `NORMAL = 131`
  - `TRANSITION = 46`

## Bugs / Gaps To Fix Before 2022

### B1 - VASS rejection metric contamination
- Problem:
  - `HAS_SPREAD_POSITION` is logged as `VASS_REJECTION`.
- Impact:
  - Makes VASS look structurally broken when it is often simply unavailable due to existing spread.
- Fix:
  - Reclassify `HAS_SPREAD_POSITION`, `ENTRY_ALREADY_ATTEMPTED_TODAY`, `POST_TRADE_MARGIN_COOLDOWN` as `VASS_SKIPPED_*`.
  - Keep `VASS_REJECTION` only for true construction/validation failures.

### B2 - Intraday outcome accounting gap
- Problem:
  - OCO events show stop/profit triggers (`OCO_STOP=7`, `OCO_PROFIT=6`), but `INTRADAY_RESULT=2`.
- Impact:
  - Win-rate and strategy diagnostics are undercounted / misleading.
- Fix:
  - Emit one normalized `INTRADAY_RESULT` for every intraday close path:
    - OCO stop/profit
    - forced EOD
    - fallback exits

### B3 - StartupGate persisted phase appears stale
- Problem:
  - `STATE: SAVED | StartupGate | Phase=INDICATOR_WARMUP` appears throughout full run.
- Impact:
  - Observability bug at minimum; may mask true gating behavior in audits.
- Fix:
  - Save current runtime phase correctly at EOD/state save.

## Optimization (Minimal Parameter Tuning Before 2022)

Objective:
- Increase Micro participation moderately.
- Keep call-bias control in place for bear regimes.
- Avoid introducing new features.

### Recommended parameter adjustments

1. `MICRO_SCORE_BULLISH_CONFIRM`
- Current: `47.0`
- Proposed: `48.0`
- Rationale: slightly tighten CALL confirmation to avoid over-easy bullish entries in mixed tape.

2. `MICRO_SCORE_BEARISH_CONFIRM`
- Current: `49.0`
- Proposed: `47.0`
- Rationale: easier PUT confirmation for earlier bear responsiveness.

3. `INTRADAY_QQQ_FALLBACK_MIN_MOVE`
- Current: `0.30`
- Proposed: `0.20`
- Rationale: reduce `Dir=None` when UVXY not extreme but QQQ has actionable move.

4. `QQQ_NOISE_THRESHOLD`
- Current: `0.13`
- Proposed: `0.10`
- Rationale: reduce `QQQ_FLAT` blockers without opening floodgates.

### Keep unchanged for now
- `MICRO_UVXY_BULLISH_THRESHOLD = -0.045`
- `MICRO_UVXY_BEARISH_THRESHOLD = 0.028`
- `INTRADAY_CALL_BLOCK_VIX_MIN = 25.0`
- `INTRADAY_CALL_BLOCK_REGIME_MAX = 55.0`

Reason:
- These are currently the main protection against 2022-style CALL bias in fear regimes.

## Clarification: Stop/Limit cancellation behavior
- Pattern observed:
  - stop filled -> sibling limit canceled, or limit filled -> sibling stop canceled.
- This is expected OCO behavior and not a bug by itself.

## Pre-2022 Acceptance Checklist

Before launching full 2022 rerun, ensure:
- `VASS_REJECTION` excludes state-based skips (`HAS_SPREAD_POSITION`, etc.).
- `INTRADAY_RESULT` count aligns with actual intraday exit events.
- Startup gate persisted phase reflects runtime state (not constant warmup).
- Micro blockers reduce proportionally after the 4 parameter adjustments.

