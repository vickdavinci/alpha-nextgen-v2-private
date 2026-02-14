# Bull Market MICRO Fix Plan (V10)

## Objective
Deliver a coding-ready, phased plan to fix MICRO profitability by deprecating `DEBIT_MOMENTUM`, making `ITM_MOMENTUM` the primary confirmation strategy, preserving divergence edge via `DEBIT_FADE` only where valid, and keeping order/state/risk plumbing stable.

This document is decision-complete and intended to be implemented directly.

---

## Non-Negotiable Constraints
- No regressions in state consistency (position lifecycle, counters, persistence).
- No regressions in order safety (OCO linkage, orphan cancellation, forced close behavior).
- No regressions in risk guardrails (kill switch, governor, direction/day caps, exposure caps).
- All deprecations must be explicit and stale code removed in the final phase.

---

## Current Problems (RCA Summary)

1. `DEBIT_MOMENTUM` is structurally weak for confirmation:
   - ~1:1 R:R (capped upside with spread, uncapped downside with theta)
   - fee-heavy (higher contract count per trade)
   - low edge after slippage/fees in low-vol intraday conditions
   - **V9.8 2023 Proof:** H1 MEDIUM VIX: -$447 (near flat). H2 LOW VIX: -$11,664 (catastrophic)

2. `ITM_MOMENTUM` is under-routed in bull/normal regimes:
   - confirmation paths choose `DEBIT_MOMENTUM` when enabled
   - ITM_MOMENTUM CALL actually works (56% WR, +$34/trade in 2023) but starved (9/61 trades)
   - 85% of ITM_MOMENTUM trades routed to PUT in a +50% QQQ year

3. Strategy/regime mismatch:
   - HIGH VIX environments produce large directional moves where capped spreads under-capture upside
   - Regime engine scored 92% NEUTRAL in 2023 (+50% QQQ year), never truly RISK_ON

4. Dead code exists:
   - Grind-Up override branch references `CAUTIOUS` inside a block that only handles `CHOPPY_LOW` (unreachable path at options_engine.py lines 1582-1601)

---

## Target Strategy Framework

### Signal-to-Strategy Mapping
- **Divergence** (QQQ and VIX disagree):
  - LOW/MED VIX: `DEBIT_FADE`
  - HIGH VIX (>=25): `ITM_MOMENTUM` (no spread cap in volatile market)

- **Confirmation** (QQQ and VIX align):
  - ALL VIX levels: `ITM_MOMENTUM`

- **Crisis regimes**:
  - `PROTECTIVE_PUTS` / `NO_TRADE` as currently designed (with existing QQQ-direction gates retained)

- `DEBIT_MOMENTUM`:
  - **Deprecated and removed from routing.**

### VIX-Tier Gates
- LOW VIX (< 18): stricter min move gate (0.50%) to filter theta-dominated noise
- MED VIX (18-25): standard move gate (0.40%)
- HIGH VIX (> 25): standard move gate (0.40%), avoid spread capping by using ITM for divergence

### ITM DTE Rules
- ITM min DTE:
  - LOW/MED VIX: `>= 3` (reduce theta decay on ITM singles)
  - HIGH VIX: `>= 2` (vol provides buffer)
- ITM max DTE: `<= 5` (unchanged intraday envelope)

---

## Required Config Changes

Add/ensure these config keys (names must be exact):

### 1. Strategy enable/deprecation
- `INTRADAY_DEBIT_MOMENTUM_ENABLED = False` (final state)
- `INTRADAY_ITM_MOMENTUM_ENABLED = True` (if flag does not exist, add with default True)

### 2. VIX-tier move gates
- `MICRO_MIN_MOVE_LOW_VIX = 0.50`  # 0.50% abs move (stricter for LOW VIX)
- `MICRO_MIN_MOVE_MED_VIX = 0.40`  # 0.40% abs move (standard)
- `MICRO_MIN_MOVE_HIGH_VIX = 0.40`  # 0.40% abs move (standard)

### 3. ITM DTE routing
- `MICRO_DTE_ROUTING_ENABLED = True`  # V10: Enable VIX-aware DTE selection (was False)
- `MICRO_ITM_DTE_MIN_LOW_VIX = 3`
- `MICRO_ITM_DTE_MIN_MED_VIX = 3`
- `MICRO_ITM_DTE_MIN_HIGH_VIX = 2`
- `MICRO_ITM_DTE_MAX = 5`

### 4. ITM contract quality
- `INTRADAY_ITM_DELTA_MIN = 0.65`  # (was 0.60)
- `INTRADAY_ITM_DELTA_MAX = 0.80`  # (was 0.85)

### 5. ITM exits (intraday)
- `INTRADAY_ITM_TARGET = 0.45`  # wider for uncapped upside (was 0.35)
- `INTRADAY_ITM_STOP = 0.25`    # tighter — ITM moves predictably with delta (was 0.35)
- `INTRADAY_ITM_TRAIL_TRIGGER = 0.20`  # unchanged
- `INTRADAY_ITM_TRAIL_PCT = 0.50`      # unchanged

### 6. Dead code cleanup
- `GRIND_UP_OVERRIDE_ENABLED = False`  # V10: dead code path, CAUTIOUS not in caution_regimes

### 7. Keep existing safety caps
- Keep per-day and per-direction intraday caps unchanged unless separately approved.

---

## Code Changes by Area

### A) Strategy Routing (Options Engine)
Primary file: `engines/satellite/options_engine.py`

1. **Replace confirmation routing behavior**
   - Replace `momentum_or_disabled()` helper (lines 1358-1367) with `confirmation_strategy()`
   - New helper always returns `ITM_MOMENTUM` — no DEBIT_MOMENTUM fallback
   - Update all 4 call sites: lines 1438, 1460, 1478, 1513
   - Remove CAUTIOUS special-case overrides at lines 1453-1458 and 1506-1511 (now redundant — all confirmation paths use ITM_MOMENTUM anyway)

2. **Divergence routing by VIX tier**
   - LOW/MED VIX divergence: keep `DEBIT_FADE` (lines 1430-1434, 1492-1496 unchanged)
   - HIGH VIX divergence (vix_current >= 25): route to `ITM_MOMENTUM` with `HIGH_VIX_DIVERGENCE:` reason
   - Insert check before DEBIT_FADE returns at lines 1430 and 1492

3. **Apply VIX-tier move gates before strategy selection**
   - Replace single `INTRADAY_FADE_MIN_MOVE` gate at line 1410
   - Compute `min_move_gate` from `vix_current` using 3 tiers (<18, 18-25, >=25)
   - Use config keys: `MICRO_MIN_MOVE_LOW_VIX`, `MICRO_MIN_MOVE_MED_VIX`, `MICRO_MIN_MOVE_HIGH_VIX`
   - Include VIX tier label in rejection reason for machine-parseable telemetry

4. **Apply ITM DTE floors in contract selection for ITM path**
   - In `main.py:_select_intraday_option_contract()` after line 5644
   - If strategy is ITM_MOMENTUM, apply DTE floor: `effective_dte_min = max(effective_dte_min, itm_floor)`
   - Floor selected by VIX tier: `MICRO_ITM_DTE_MIN_LOW_VIX/MED_VIX/HIGH_VIX`
   - DEBIT_FADE keeps base 1-5 DTE range (unaffected)

5. **Preserve crisis routing unchanged**
   - `PROTECTIVE_PUTS` and existing panic logic remain intact (lines 1303-1337)

6. **Remove dead Grind-Up code**
   - Delete lines 1582-1601 (unreachable CAUTIOUS check under CHOPPY_LOW-only block)
   - Keep only `return NO_TRADE, None, f"Caution regime: {micro_regime.value}"`

### B) Exit/Order Behavior
Files: `engines/satellite/options_engine.py`, `main.py`, `execution/oco_manager.py`

**STATUS: VERIFIED SAFE — NO CODE CHANGES NEEDED**

1. **Intraday exit semantics** — No overnight conversion in this phase. Keep force-close at 15:25 and OCO lifecycle as current.

2. **ITM exits use configured profile** — VERIFIED:
   - `_get_intraday_exit_profile()` (line 7061) uses string match on `IntradayStrategy.ITM_MOMENTUM.value`
   - Looks up `INTRADAY_ITM_TARGET` (0.45) and `INTRADAY_ITM_STOP` (0.25)
   - When routing changes from DEBIT_MOMENTUM → ITM_MOMENTUM, the `entry_strategy` field changes, exit profile automatically uses ITM config values
   - Trailing stop: `_get_trail_config()` (line 7082) same pattern — returns ITM trail config

3. **OCO tag consistency** — VERIFIED:
   - OCO creation (main.py ~8647) uses `f"MICRO:{position.entry_strategy}"` as tag_context
   - Tags become `OCO_STOP:OCO-{id}|MICRO:ITM_MOMENTUM` and `OCO_PROFIT:OCO-{id}|MICRO:ITM_MOMENTUM`
   - OCO recovery (main.py ~2777) retrieves same tag_context from position.entry_strategy
   - Symbol-aware close/removal (`cancel_by_symbol()`) uses symbol lookup, not tag parsing

4. **Force-close** — VERIFIED:
   - `_on_intraday_force_exit()` (main.py ~2700) cancels OCO by symbol, submits market exit
   - Strategy-agnostic — no code change needed

### C) State Management Guarantees
Files: `main.py`, `engines/satellite/options_engine.py`

**STATUS: VERIFIED SAFE — NO CODE CHANGES NEEDED**

1. **Position state transitions** — VERIFIED:
   - `entry registered -> active -> closing -> removed` lifecycle unchanged
   - No cross-symbol removal risk
   - `OptionsPosition.entry_strategy` field (line 200) is a passthrough string

2. **Daily counters** — VERIFIED:
   - `_intraday_trades_today`, `_intraday_call_trades_today`, `_intraday_put_trades_today` (lines 1901-1907)
   - `_increment_trade_counter(mode, direction)` (line 9633): counts by MODE + DIRECTION, NOT strategy name
   - Deprecating DEBIT_MOMENTUM has zero impact on counters

3. **Persistence** — VERIFIED:
   - `get_state_for_persistence()` (line 9320) serializes counters (strategy-agnostic)
   - `restore_state()` (line 9365) restores counters without strategy name dependency
   - Old DEBIT_MOMENTUM positions restore correctly — exit profile handles any string (falls to default)
   - No new required fields — backward compatible

4. **Ghost/orphan behavior** — VERIFIED:
   - Ghost detection (`_close_orphaned_leg()` line 6394) strategy-agnostic
   - Intraday position validation (line 9419) clears stale positions regardless of strategy
   - No increased churn risk

### D) Telemetry Requirements (Mandatory)

**STATUS: REQUIRES 3 SMALL ADDITIONS**

1. **Routing/funnel** — PARTIALLY COVERED:
   - Reject codes already exist: 14 active `E_INTRADAY_*` codes in `check_intraday_entry_signal()`
   - VIX-tier label added to move gate rejection by Change A3 (machine-parseable)
   - **ADD:** DTE routing diagnostic log in `main.py:_select_intraday_option_contract()` (after line 5644):
     ```
     INTRADAY_DTE_ROUTING: {strategy} | VIX={vix:.1f} tier={tier} | DTE=[{min}-{max}]
     ```
     Use `trades_only=False` (diagnostic, silent in backtests)

2. **Strategy attribution** — MOSTLY COVERED:
   - Entry: `TargetWeight.metadata['intraday_strategy']` carries strategy label (line 8162)
   - OCO tags: `MICRO:{strategy}` format propagated through stop/profit orders
   - Exit: `remove_intraday_position()` (line 8677) logs `Strategy={strategy}`
   - **VERIFY:** Confirm initial entry order tag includes strategy (not just generic `MICRO:`)

3. **DTE diagnostics** — NEW:
   - Selected DTE already logged in `INTRADAY_SIGNAL:` (line 8145)
   - **ADD:** ITM DTE floor application log (per item D.1 above)
   - If no contracts meet DTE floor, existing `E_INTRADAY_NO_CONTRACT` fires with filter funnel showing DTE rejects

4. **VIX-tier diagnostics** — NEW:
   - VIX tier (LOW/MED/HIGH) currently computed but never logged as string
   - **ADD:** VIX tier field to `MICRO_UPDATE` log in `main.py` (~line 3044):
     ```python
     vix_tier = "LOW" if vix_level_cboe < 18 else "MED" if vix_level_cboe < 25 else "HIGH"
     # Append to existing log: f"VIX_tier={vix_tier}"
     ```

5. **Log budget** — VERIFIED OK:
   - MICRO_NO_TRADE throttled at 5-minute intervals (line 2411)
   - INTRADAY_SIGNAL logs use `trades_only=True` (quiet in backtests)
   - New diagnostic logs use `trades_only=False` (silent in backtests)
   - LOW VIX move gate generates more NO_TRADE events but throttle prevents explosion

---

## Phased Implementation Plan

### Phase 0 - Branch Safety and Baseline Lock
1. Create branch and freeze baseline metrics for two windows:
   - Bull: Jul-Sep 2017
   - Bear: Dec-Feb 2021/2022
2. Store baseline reports for strategy counts, funnel, P&L, fees, and log volume.

Exit criteria:
- Baseline artifacts committed under `docs/audits/...`.

### Phase 1 - Routing Refactor (No Plumbing Mutation)
1. Config changes: disable DEBIT_MOMENTUM, add VIX-tier gates, ITM DTE floors, update ITM exit profile
2. Replace `momentum_or_disabled()` with `confirmation_strategy()` (always ITM)
3. HIGH VIX divergence → ITM_MOMENTUM (not DEBIT_FADE)
4. VIX-tier move gates replace single INTRADAY_FADE_MIN_MOVE
5. ITM DTE floor in main.py contract selection
6. Remove dead Grind-Up code
7. Keep force-close/state logic unchanged (verified safe)

Exit criteria:
- Compiles clean
- No failing unit tests introduced
- Smoke backtest executes without runtime errors

### Phase 2 - Telemetry Hardening
1. Add DTE routing diagnostic log in main.py
2. Add VIX tier field to MICRO_UPDATE log in main.py
3. Verify strategy attribution in order tags (entry tag includes strategy)
4. Verify log budget (no truncation on short smoke windows)

Exit criteria:
- Full funnel reconstructable from logs + orders/trades
- No log truncation on short smoke windows

### Phase 3 - Plumbing Validation (Verify Only)
1. State checks (VERIFIED SAFE in analysis):
   - Counters strategy-agnostic
   - Persistence backward compatible
   - Ghost cleanup unchanged
2. Order checks (VERIFIED SAFE in analysis):
   - OCO tag propagation correct
   - Force-close strategy-agnostic
   - No orphan risk from routing change
3. Risk checks (VERIFIED SAFE in analysis):
   - Per-day/per-direction caps strategy-agnostic
   - Kill switch/governor unaffected

Exit criteria:
- Zero new plumbing anomalies versus baseline

### Phase 4 - Backtest Sequence
1. Short bull smoke run (Jul-Sep 2017)
2. Short bear smoke run (Dec 2021-Feb 2022)
3. Full bull year run (if smoke passes)
4. Full bear year run (if smoke passes)

For each run, publish:
- strategy mix
- win/loss and expectancy by strategy
- fee drag
- top choke reasons
- state/order anomaly summary
- log size and truncation status

### Phase 5 - Stale Code Removal (Finalization)
After validation passes:
1. Remove deprecated helpers/branches tied only to `DEBIT_MOMENTUM` fallback behavior
2. Remove DEBIT_MOMENTUM branches from exit profile and trail config
3. Update docs/matrix references to final behavior
4. Keep migration notes for one version cycle, then delete obsolete flags/docs

Exit criteria:
- No unreachable strategy branches
- No deprecated path references in runtime code

---

## Test Plan (Must Pass)

### Unit/Component
1. Routing tests for all 21 regimes with VIX-tier gates
2. Confirmation paths return ITM (except crisis/no-trade branches)
3. High-VIX divergence routes to ITM, not FADE
4. ITM DTE floor rejects correctly by VIX tier
5. Strategy attribution never unknown for valid MICRO path

### Integration
1. Entry -> OCO -> exit lifecycle works for ITM and FADE
2. Force-close and OCO cancel interplay remains clean
3. State restore/reset does not create stale or phantom positions

### Regression
1. No increase in ghost reconciliation churn
2. No increase in orphan order incidents
3. No increase in duplicate close events

---

## Acceptance Criteria (Go/No-Go)

Go only if all are true:
1. `DEBIT_MOMENTUM` entries = 0 by design
2. `ITM_MOMENTUM` materially present in confirmation paths (bull and bear windows)
3. MICRO net expectancy improves versus baseline in bull window
4. Crisis safety behavior preserved in bear window
5. No state/order regression events
6. Logs support complete RCA without truncation

---

## Rollback Plan
If any critical regression appears:
1. Revert Phase 1 routing commit set
2. Keep telemetry additions if safe (observability-only)
3. Re-run baseline windows to confirm parity restored

---

## Deliverables
1. Updated Bull Market Fix Plan doc (this document)
2. Updated code implementing Phases 1-3
3. Backtest reports for Phase 4 windows
4. Final stale-code cleanup commit (Phase 5)
5. Updated strategy matrix documentation
