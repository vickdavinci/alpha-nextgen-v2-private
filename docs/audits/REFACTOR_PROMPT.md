# REFACTOR PROMPT: Split main.py and options_engine.py Below 256KB

> **Give this entire prompt to the coding agent. It contains everything needed.**

---

## MISSION

Refactor `main.py` (428KB, 9,479 lines) and `engines/satellite/options_engine.py` (413KB, 9,736 lines) into smaller files, each **under 256KB** (262,144 bytes). QuantConnect's file size limit is 256KB per file. No file in the project may exceed this limit after refactoring.

**THE GOLDEN RULE: ZERO LOGIC CHANGES.**

You are performing a **mechanical decomposition** — slicing files into pieces and wiring them back together via imports. The trading logic, state management, order flow, calculations, conditionals, thresholds, and all behavior MUST be identical before and after. If a single `if` statement changes, a single threshold moves, or a single method gets "improved" — you have failed.

---

## CURRENT FILE SIZE INVENTORY

These are the files that exceed or approach the 256KB limit:

| File | Size | Lines | Status |
|------|------|-------|--------|
| `main.py` | 428KB | 9,479 | **OVER LIMIT — must split** |
| `engines/satellite/options_engine.py` | 413KB | 9,736 | **OVER LIMIT — must split** |
| `portfolio/portfolio_router.py` | 117KB | ~2,800 | Under limit but watch |
| `config.py` | 103KB | 2,128 | Under limit but watch |
| `engines/core/risk_engine.py` | 78KB | ~1,800 | OK |
| `engines/core/regime_engine.py` | 72KB | ~1,600 | OK |

Only `main.py` and `options_engine.py` need splitting. Do NOT touch other files unless necessary for imports.

---

## QUANTCONNECT CONSTRAINTS

1. **File size limit: 256KB per file** — Every `.py` file must be under 262,144 bytes after refactoring
2. **Import style:** `from AlgorithmImports import *` at the top of any file that uses QC types
3. **Multi-file projects are supported** — QC allows subdirectories with `__init__.py` files. The existing project already uses this pattern (engines/, portfolio/, execution/, etc.)
4. **Main entry point** — `main.py` must contain the `class AlphaNextGen(QCAlgorithm)` with `Initialize()` and `OnData()`. These cannot be moved out.
5. **No external packages** — Only standard library + what `AlgorithmImports` provides
6. **Relative imports work** — The project already uses `from engines.core.regime_engine import RegimeEngine` etc.

---

## REFACTORING STRATEGY

### Technique: Mixin Classes

Use Python **mixin classes** to split a large class into multiple files. Each mixin contains a logical group of methods. The main class inherits from all mixins.

```python
# main.py (stays as entry point, now thin)
from main_options import OptionsMethodsMixin
from main_risk import RiskMethodsMixin
from main_orders import OrderMethodsMixin
# ... etc

class AlphaNextGen(
    OptionsMethodsMixin,
    RiskMethodsMixin,
    OrderMethodsMixin,
    QCAlgorithm
):
    def Initialize(self):
        ...  # stays here

    def OnData(self, data):
        ...  # stays here
```

```python
# main_options.py (new file)
class OptionsMethodsMixin:
    """Options-related methods extracted from AlphaNextGen."""

    def _generate_options_signals(self, ...):
        ...  # exact same code, moved here

    def _scan_spread_for_direction(self, ...):
        ...  # exact same code, moved here
```

**Why mixins:**
- `self` still refers to the `AlphaNextGen` instance — all `self.xxx` references work unchanged
- No need to pass `algorithm` references around
- No need to change any method signatures
- No need to change any callers
- The MRO (Method Resolution Order) handles everything

### Technique: Module Extraction (for options_engine.py)

`options_engine.py` contains multiple independent classes (data classes, `MicroRegimeEngine`, `IVSensor`, `OptionsEngine`). These can be moved to separate files:

```python
# engines/satellite/options_models.py (new file)
class SpreadStrategy(Enum): ...
class EntryScore: ...
class OptionContract: ...
class OptionsPosition: ...
class SpreadPosition: ...
class SpreadFillTracker: ...
class ExitOrderTracker: ...

# engines/satellite/iv_sensor.py (new file)
class IVSensor: ...

# engines/satellite/micro_regime_engine.py (new file)
class MicroRegimeEngine: ...
class MicroRegimeState: ...
class VIXSnapshot: ...

# engines/satellite/options_engine.py (now smaller — only OptionsEngine class)
from engines.satellite.options_models import *
from engines.satellite.iv_sensor import IVSensor
from engines.satellite.micro_regime_engine import MicroRegimeEngine, MicroRegimeState
class OptionsEngine: ...
```

If `OptionsEngine` is STILL over 256KB after extracting the other classes, split it further using mixins:

```python
# engines/satellite/options_entry.py — entry signal logic
# engines/satellite/options_exit.py — exit signal logic
# engines/satellite/options_spread_selection.py — spread leg selection
# engines/satellite/options_position_mgmt.py — position registration, removal, state
# engines/satellite/options_intraday.py — intraday/MICRO specific methods

# engines/satellite/options_engine.py — imports all mixins
class OptionsEngine(
    OptionsEntryMixin,
    OptionsExitMixin,
    OptionsSpreadSelectionMixin,
    OptionsPositionMixin,
    OptionsIntradayMixin
):
    def __init__(self, algorithm):
        ...  # stays here
```

---

## DETAILED SPLIT PLAN: main.py

Split `main.py` (9,479 lines, 135 methods) into these files. **Suggested** groupings — adjust based on actual size targets (each file < 200KB ideally, definitely < 256KB):

### File 1: `main.py` (KEEP — entry point, ~2,500 lines target)
Contains:
- `class AlphaNextGen(QCAlgorithm)` definition with mixin inheritance
- `Initialize()` (line 175)
- `OnData()` (line 417)
- `_add_securities()` (line 569)
- `_setup_indicators()` (line 674)
- `_initialize_engines()` (line 826)
- `_initialize_infrastructure()` (line 861)
- `_setup_schedules()` (line 883)
- All scheduled event handlers that are thin wrappers: `_on_pre_market_setup`, `_on_moo_fallback`, `_on_sod_baseline`, `_on_warm_entry_check`, `_on_time_guard_start`, `_on_time_guard_end`, `_on_mr_force_close`, `_on_eod_processing`, `_on_market_close`, `_on_weekly_reset`, `_on_intraday_reconcile`
- State management: `_load_state()`, `_save_state()`, `_reconcile_positions()`
- Core utility methods: `_is_primary_market_open`, `_check_splits`, `_cleanup_stale_orders`, `_update_rolling_windows`, `_is_first_bar_after_market_gap`, `_get_unsettled_cash`, `_check_settlement_cooldown`, `_can_trade_options_settlement_aware`, `_get_tradeable_equity_settlement_aware`
- Regime methods: `_calculate_regime`, `_refresh_intraday_regime_score`, `_get_effective_regime_score_for_options`
- `_log_daily_summary()`

### File 2: `main_options.py` (NEW — options signal generation + scanning, ~2,500 lines)
Contains all options-related methods from `main.py`:
- `_generate_options_signals()` (line 4533) — THIS IS HUGE (~500 lines)
- `_scan_spread_for_direction()` (line 4788) — (~280 lines)
- `_build_spread_candidate_contracts()` (line 5067)
- `_route_vass_strategy()` (line 5156)
- `_strategy_option_right()` (line 5213)
- `_build_vass_dte_fallbacks()` (line 5231)
- `_canonical_options_reason_code()` (line 5245)
- `_apply_spread_margin_guard()` (line 5277)
- `_normalize_option_symbol()` (line 5342)
- `_get_contract_prices()` (line 5357)
- `_select_best_option_contract()` (line 5392)
- `_select_swing_option_contract()` (line 5473)
- `_select_intraday_option_contract()` (line 5573)
- `_generate_options_signals_gated()` (line 5806)
- `_scan_options_signals_gated()` (line 6416)
- `_scan_options_signals()` (line 6432) — ANOTHER HUGE METHOD (~900 lines)
- `_monitor_risk_greeks()` (line 7345)
- `_get_fresh_position_greeks()` (line 7484)
- `_get_option_current_price()` (line 7541)
- `_get_option_current_dte()` (line 7579)
- `_get_option_expiry_date()` (line 7614)
- `_cancel_spread_linked_oco()` (line 7648)
- `_check_spread_exit()` (line 7664) — (~370 lines)
- `_get_actual_option_count()` (line 8038)
- `_validate_options_symbol()` (line 8056)
- `_calculate_iv_rank()` (line 8120)

### File 3: `main_orders.py` (NEW — order events + fill handling, ~2,000 lines)
Contains:
- `OnOrderEvent()` (line 3177) — THIS IS HUGE (~480 lines)
- `_should_log_backtest_category()` (line 3657)
- `_get_order_tag()` (line 3664)
- `_extract_trace_id_from_tag()` (line 3674)
- `_compact_tag_for_log()` (line 3696)
- `_micro_dte_bucket()` (line 3705)
- `_inc_micro_dte_counter()` (line 3715)
- `_record_micro_drop_reason_dte()` (line 3721)
- `_is_micro_entry_fill()` (line 3730)
- `_build_spread_runtime_key()` (line 3739)
- `_record_spread_removal()` (line 3746)
- `_reconcile_spread_ghosts()` (line 3761)
- `_clear_spread_runtime_trackers_by_key()` (line 3835)
- `_log_order_lifecycle_issue()` (line 3846)
- `_forward_execution_event()` (line 3865)
- `_on_fill()` (line 8501) — HUGE (~340 lines)
- `_handle_spread_leg_fill()` (line 8842)
- `_handle_spread_leg_close()` (line 8951)
- `_queue_spread_close_retry_on_cancel()` (line 9095)
- `_cleanup_stale_spread_state()` (line 9188)
- `_emergency_close_spread_legs()` (line 9221)
- `_force_spread_exit()` (line 9259)
- `_schedule_exit_retry()` (line 9307)
- `_retry_exit_order()` (line 9338)
- `_force_market_close()` (line 9364)
- `_handle_order_rejection()` (line 8355)
- `_parse_and_store_rejection_margin()` (line 8456)

### File 4: `main_risk.py` (NEW — risk checks, kill switch, panic, MR, trend stops, ~1,500 lines)
Contains:
- `_run_risk_checks()` (line 5900)
- `_handle_kill_switch()` (line 5939)
- `_ks_close_single_leg_options_atomic()` (line 6066)
- `_handle_panic_mode()` (line 6121)
- `_scan_mr_signals()` (line 6139)
- `_check_mr_exits()` (line 6283)
- `_monitor_trend_stops()` (line 6348)
- `_generate_trend_signals_eod()` (line 4358)
- `_generate_hedge_signals()` (line 5832)
- `_generate_hedge_exit_signals()` (line 5860)
- `_process_immediate_signals()` (line 4158)
- `_process_eod_signals()` (line 4230)
- `_has_leveraged_position()` (line 8148)

### File 5: `main_intraday.py` (NEW — intraday options, MICRO, force exits, ~1,200 lines)
Contains:
- `_on_micro_regime_update()` (line 2990)
- `_get_vix_intraday_proxy()` (line 3075)
- `_get_vix_level()` (line 3094)
- `_get_vix_direction()` (line 3107)
- `_should_scan_intraday()` (line 3138)
- `_is_market_close_blackout()` (line 3159)
- `_intraday_force_exit_fallback()` (line 1676)
- `_on_intraday_options_force_close()` (line 2668)
- `_mr_force_close_fallback()` (line 1743)
- `_liquidate_all_spread_aware()` (line 1777)
- `_close_options_atomic()` (line 1878)
- `_ensure_oco_for_open_options()` (line 2777)
- `_reconcile_intraday_close_guards()` (line 2872)
- `_on_friday_firewall()` (line 2884)
- `_reconcile_spread_state()` (line 2935)
- `_on_vix_spike_check()` (line 2943)
- `_check_expiration_hammer_v2()` (line 2548)
- Premarket methods: `_check_premarket_itm_shorts`, `_get_premarket_vix_gap_proxy_pct`, `_update_premarket_vix_ladder`, `_apply_premarket_vix_actions`, `_is_premarket_ladder_entry_block_active`, `_is_premarket_ladder_call_block_active`, `_is_premarket_shock_memory_active`, `_get_premarket_shock_memory_pct`
- Helper methods: `_normalize_symbol_str`, `_attach_option_trace_metadata`, `_get_option_holding_quantity`, `_get_average_volume`, `_get_volume_ratio`, `_get_current_positions`, `_get_current_prices`

---

## DETAILED SPLIT PLAN: options_engine.py

Split `engines/satellite/options_engine.py` (9,736 lines, 188 methods, 10+ classes) into these files:

### File 1: `engines/satellite/options_models.py` (NEW — data classes, ~800 lines)
Extract all data classes and enums defined at the top of options_engine.py:
- `class SpreadStrategy(Enum)` (line 64)
- `class EntryScore` (line 79)
- `class OptionContract` (line 115)
- `class OptionsPosition` (line 185)
- `class SpreadPosition` (line 241)
- `class SpreadFillTracker` (line 318)
- `class ExitOrderTracker` (line 469)
- Helper functions: `get_expiration_firewall_day()`, `is_expiration_firewall_day()`

### File 2: `engines/satellite/iv_sensor.py` (NEW — IVSensor class, ~250 lines)
- `class IVSensor` (line 499)

### File 3: `engines/satellite/micro_regime_engine.py` (NEW — MicroRegimeEngine + data classes, ~900 lines)
- `class VIXSnapshot` (line 829)
- `class MicroRegimeState` (line 838)
- `class MicroRegimeEngine` (line 910)

### File 4: `engines/satellite/options_engine.py` (KEEP — OptionsEngine class only)
After extracting the above classes, `OptionsEngine` class itself is ~7,700 lines. This is still likely over 256KB.

**If still over 256KB**, split `OptionsEngine` using mixins:

### File 4a: `engines/satellite/options_entry.py` (NEW — entry signal logic mixin)
- `check_entry_signal()` (line 4281) — ~275 lines
- `check_spread_entry_signal()` (line 4556) — ~750 lines
- `check_credit_spread_entry_signal()` (line 5312) — ~500 lines
- `check_intraday_entry_signal()` (line 7587) — ~560 lines
- `resolve_trade_signal()` (line 2142)
- `generate_micro_intraday_signal()` (line 2336)
- `check_swing_filters()` (line 7532)
- `calculate_entry_score()` (line 3101)
- `_score_adx()`, `_score_momentum()`, `_score_iv_rank()`, `_score_liquidity()`
- `get_stop_tier()`, `calculate_position_size()`

### File 4b: `engines/satellite/options_exit.py` (NEW — exit signal logic mixin)
- `check_spread_exit_signals()` (line 6447) — ~400 lines
- `check_friday_firewall_exit()` (line 6846)
- `check_overnight_gap_protection_exit()` (line 6993)
- `check_exit_signals()` (line 7094)
- `check_force_exit()` (line 7194)
- `check_intraday_force_exit()` (line 8150)
- `check_gamma_pin_exit()` (line 8208)
- `check_expiring_options_force_exit()` (line 8286)
- `_is_short_leg_deep_itm()` (line 5827)
- `_check_overnight_itm_short_risk()` (line 5894)
- `_check_assignment_margin_buffer()` (line 5949)
- `_check_short_leg_itm_exit()` (line 6001)
- `check_premarket_itm_shorts()` (line 6067)
- `check_assignment_risk_exit()` (line 6172)
- `_check_neutrality_staged_exit()` (line 2975)
- `_get_intraday_exit_profile()`, `_get_trail_config()`

### File 4c: `engines/satellite/options_spread_select.py` (NEW — spread/contract selection mixin)
- `select_spread_legs()` (line 3338) — ~285 lines
- `select_spread_legs_with_fallback()` (line 3623)
- `select_credit_spread_legs()` (line 3686) — ~410 lines
- `select_credit_spread_legs_with_fallback()` (line 4095)
- `estimate_spread_margin_per_contract()` (line 4230)
- `get_usable_margin()` (line 4261)
- `_get_effective_credit_min()`, `_get_effective_credit_to_width_min()`
- `_calculate_credit_spread_size()` (line 7305)
- `get_assignment_aware_size_multiplier()` (line 6317)
- `handle_partial_assignment()` (line 6372)

### File 4d: `engines/satellite/options_position.py` (NEW — position management mixin)
- `register_entry()` (line 8495)
- `register_spread_entry()` (line 8679)
- `remove_position()`, `remove_intraday_position()` (line 8632, 8646)
- `remove_spread_position()` (line 8882)
- `cancel_pending_swing_entry()`, `cancel_pending_spread_entry()`, `cancel_pending_intraday_entry()`
- `has_spread_position()`, `get_spread_position()`, `has_intraday_position()`, etc.
- `record_spread_result()`, `get_win_rate_scale()`
- `clear_spread_position()`, `reset_spread_closing_lock()`, `clear_all_positions()`
- `calculate_position_greeks()`, `update_position_greeks()`, `check_greeks_breach()`
- `get_state_for_persistence()`, `restore_state()`
- `reset()`, `reset_daily()`
- `_increment_trade_counter()`, `_can_trade_options()`

### File 4e: `engines/satellite/options_engine.py` (KEEP — core OptionsEngine, now smaller)
After extracting mixins, this file contains:
- `class OptionsEngine` definition with mixin inheritance
- `__init__()` method
- `log()` method
- Slot management: `can_enter_swing()`, `can_enter_intraday()`, `count_options_positions()`
- Mode/strategy routing: `determine_mode()`, `_select_strategy()`, `get_mode_allocation()`
- Choppy market: `get_choppy_market_scale()`
- Guard methods: `_check_vass_direction_day_gap()`, `_check_vass_similar_entry_guard()`, etc.
- Failure tracking: `pop_last_spread_failure_stats()`, etc.

---

## CRITICAL IMPLEMENTATION RULES

### Rule 1: EXACT Code Transfer
Copy methods **character-for-character** from the source file to the mixin file. Do NOT:
- Rename any method
- Change any parameter
- Modify any return type
- Add or remove any line of code
- "Improve" any logic
- Add type hints that weren't there
- Remove comments
- Change indentation style
- Reorder methods within a group

### Rule 2: Self References Stay As-Is
Every `self.xxx` reference in extracted methods must work unchanged. Because mixins are inherited by the main class, `self` still refers to `AlphaNextGen` (for main.py splits) or `OptionsEngine` (for options_engine splits). **Do NOT add method parameters to replace self references.**

```python
# CORRECT — move as-is
class OptionsMethodsMixin:
    def _generate_options_signals(self, regime_state, capital_state, ...):
        if self.options_engine.has_spread_position():  # self still works
            ...

# WRONG — do NOT change signatures
class OptionsMethodsMixin:
    def _generate_options_signals(self, algorithm, regime_state, ...):  # ❌ NO
        if algorithm.options_engine.has_spread_position():  # ❌ NO
```

### Rule 3: Import Chain Must Be Complete
Each new mixin file needs the same imports that the extracted methods use. **Audit every method** for:
- `config.XXX` references → add `import config`
- QC types (Symbol, Resolution, etc.) → add `from AlgorithmImports import *`
- Model types → add `from models.enums import ...`
- Engine types → add `from engines.xxx import ...`
- Standard library → add `from typing import ...`, `import json`, etc.

### Rule 4: Update __init__.py Files
After creating new files in `engines/satellite/`, update `engines/satellite/__init__.py` to export the new modules. Same for any `__init__.py` that currently re-exports classes.

### Rule 5: Update All Import Statements Project-Wide
After moving classes out of `options_engine.py`, EVERY file that imports from `options_engine` must be updated:

```python
# BEFORE (in main.py):
from engines.satellite.options_engine import (
    OptionsEngine,
    SpreadPosition,
    SpreadFillTracker,
    ...
)

# AFTER (in main.py):
from engines.satellite.options_engine import OptionsEngine
from engines.satellite.options_models import SpreadPosition, SpreadFillTracker, ...
from engines.satellite.micro_regime_engine import MicroRegimeEngine, MicroRegimeState
```

Search ALL `.py` files for imports from `options_engine` and update them:
```bash
grep -rn "from engines.satellite.options_engine import" --include="*.py" .
grep -rn "from engines.satellite import" --include="*.py" .
```

### Rule 6: Circular Import Prevention
**Mixins must NOT import from the main class file.** The dependency direction is:
```
main.py → main_options.py (mixin)
main.py → main_orders.py (mixin)
main.py → main_risk.py (mixin)
main.py → main_intraday.py (mixin)

options_engine.py → options_entry.py (mixin)
options_engine.py → options_exit.py (mixin)
options_engine.py → options_spread_select.py (mixin)
options_engine.py → options_position.py (mixin)

All mixins → options_models.py, iv_sensor.py, micro_regime_engine.py (data classes)
```

If a mixin needs a type only for type hints, use `TYPE_CHECKING`:
```python
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from main import AlphaNextGen
```

### Rule 7: MRO (Method Resolution Order)
The main class must list `QCAlgorithm` LAST in the inheritance chain:
```python
class AlphaNextGen(
    OptionsMethodsMixin,     # first
    OrderMethodsMixin,       # second
    RiskMethodsMixin,        # third
    IntradayMethodsMixin,    # fourth
    QCAlgorithm              # MUST be last
):
```

For `OptionsEngine`, since it doesn't inherit from a QC class, the order matters less but keep consistent:
```python
class OptionsEngine(
    OptionsEntryMixin,
    OptionsExitMixin,
    OptionsSpreadSelectionMixin,
    OptionsPositionMixin
):
```

### Rule 8: No New `__init__` in Mixins
Mixin classes should NOT have `__init__` methods. All initialization stays in the main class.

```python
# CORRECT
class OptionsMethodsMixin:
    """Options methods for AlphaNextGen."""

    def _generate_options_signals(self, ...):
        ...  # no __init__ needed

# WRONG
class OptionsMethodsMixin:
    def __init__(self):  # ❌ NEVER
        self.some_var = True
```

### Rule 9: Preserve Decorators and Properties
If a method has `@property`, `@staticmethod`, `@classmethod`, or any other decorator, it MUST keep that decorator in the mixin:

```python
class OptionsPositionMixin:
    def has_spread_position(self) -> bool:  # keep exactly as-is
        return self._spread_position is not None
```

---

## VERIFICATION CHECKLIST

After completing the refactoring, verify ALL of the following:

### Size Check
```bash
# Every .py file must be under 262,144 bytes
for f in main.py main_options.py main_orders.py main_risk.py main_intraday.py \
         engines/satellite/options_engine.py engines/satellite/options_models.py \
         engines/satellite/iv_sensor.py engines/satellite/micro_regime_engine.py \
         engines/satellite/options_entry.py engines/satellite/options_exit.py \
         engines/satellite/options_spread_select.py engines/satellite/options_position.py; do
    size=$(stat -f%z "$f" 2>/dev/null || echo "MISSING")
    echo "$size  $f"
done
# ALL must show < 262144
```

### Import Check
```bash
# No broken imports — run Python syntax check
python -c "from main import AlphaNextGen; print('main.py OK')"
python -c "from engines.satellite.options_engine import OptionsEngine; print('options_engine.py OK')"
python -c "from engines.satellite.options_models import SpreadPosition, SpreadFillTracker; print('models OK')"
python -c "from engines.satellite.micro_regime_engine import MicroRegimeEngine; print('micro OK')"
python -c "from engines.satellite.iv_sensor import IVSensor; print('iv OK')"
```

### Test Suite
```bash
# Run all existing tests — EVERY test must pass unchanged
source venv/bin/activate
pytest tests/ -v 2>&1 | tail -20
# Expected: All tests pass. Zero failures.
```

### Method Count Verification
```bash
# Total method count must be identical before and after
# BEFORE: main.py has 135 methods, options_engine.py has 188 methods
# Count after split:
for f in main.py main_options.py main_orders.py main_risk.py main_intraday.py; do
    count=$(grep -c "def " "$f")
    echo "$count  $f"
done
# Sum must equal 135 (original main.py method count)

for f in engines/satellite/options_engine.py engines/satellite/options_models.py \
         engines/satellite/iv_sensor.py engines/satellite/micro_regime_engine.py \
         engines/satellite/options_entry.py engines/satellite/options_exit.py \
         engines/satellite/options_spread_select.py engines/satellite/options_position.py; do
    count=$(grep -c "def " "$f")
    echo "$count  $f"
done
# Sum must equal 188 (original options_engine.py method count)
```

### Line Count Verification
```bash
# Total lines across split files should approximately equal original
wc -l main.py main_options.py main_orders.py main_risk.py main_intraday.py
# Should be close to 9,479 + overhead for imports/class declarations

wc -l engines/satellite/options_engine.py engines/satellite/options_models.py \
      engines/satellite/iv_sensor.py engines/satellite/micro_regime_engine.py \
      engines/satellite/options_entry.py engines/satellite/options_exit.py \
      engines/satellite/options_spread_select.py engines/satellite/options_position.py
# Should be close to 9,736 + overhead
```

### No Logic Diff Check
```bash
# For each method, verify the body is identical
# Spot-check critical methods:
# 1. OnOrderEvent — must be byte-identical to original
# 2. _generate_options_signals — must be byte-identical
# 3. check_spread_entry_signal — must be byte-identical
# 4. _on_fill — must be byte-identical
```

---

## EXECUTION ORDER

1. **DO NOT touch git.** No commits, no branches, no pushes. The human handles all git operations.

2. **Split options_engine.py first** (easier — extracting independent classes)
   - Create `options_models.py`, `iv_sensor.py`, `micro_regime_engine.py`
   - Update imports in `options_engine.py`
   - Update imports in ALL files that import from `options_engine`
   - Verify: `pytest tests/ -v`

3. **If OptionsEngine still > 256KB, split with mixins**
   - Create `options_entry.py`, `options_exit.py`, `options_spread_select.py`, `options_position.py`
   - Update `OptionsEngine` class to inherit from mixins
   - Verify: `pytest tests/ -v`

4. **Split main.py with mixins**
   - Create `main_options.py`, `main_orders.py`, `main_risk.py`, `main_intraday.py`
   - Update `AlphaNextGen` class to inherit from mixins
   - Verify: `pytest tests/ -v`

5. **Run full verification checklist**

6. **DO NOT commit, push, or merge.** Leave all changes unstaged. The human will review the diff manually before committing.

---

## GIT SAFETY RULES

- **DO NOT** run `git add`, `git commit`, `git push`, `git merge`, or any git write command
- **DO NOT** create branches (the human will do this)
- **DO NOT** amend, rebase, or modify git history
- You may use `git status` and `git diff` for read-only inspection
- Leave all changes as unstaged working directory modifications for human review

---

## FILES YOU MUST NOT MODIFY (beyond import updates)

- `config.py` — Do not touch parameters
- `engines/core/*.py` — Do not touch core engines
- `portfolio/portfolio_router.py` — Do not touch routing logic
- `execution/execution_engine.py` — Do not touch execution
- `execution/oco_manager.py` — Do not touch OCO
- `persistence/state_manager.py` — Do not touch state management
- `tests/*.py` — Do not modify test logic (only update imports if test files import from moved classes)

---

## SHARED LOGIC BETWEEN VASS (SWING) AND MICRO (INTRADAY)

**CRITICAL:** The OptionsEngine has methods shared by both VASS swing and MICRO intraday paths. These shared methods must stay in the **core `options_engine.py`** file (NOT in a VASS-only or MICRO-only mixin):

| Shared Method | Why It's Shared |
|--------------|-----------------|
| `calculate_position_size()` | Both VASS and MICRO size positions |
| `estimate_spread_margin_per_contract()` | Both check margin before entry |
| `get_usable_margin()` | Both calculate available margin |
| `count_options_positions()` | Both count open positions for slot limits |
| `determine_mode()` | Routes DTE to swing vs intraday mode |
| `get_mode_allocation()` | Both need allocation calculations |
| `check_spread_exit_signals()` | Both swing spreads and intraday spreads exit through this |
| `get_choppy_market_scale()` | Both use choppy market adjustment |
| `log()` | Shared logging |
| `_symbol_str()` | Shared symbol normalization |

**In `main.py`, the same pattern exists.** These main.py methods serve both VASS and MICRO:
- `_scan_options_signals()` — one giant method (~900 lines) handling BOTH swing and intraday scanning
- `_generate_options_signals()` — orchestrates both swing and intraday entries
- `OnOrderEvent()` / `_on_fill()` — handles fills for both VASS spreads and MICRO options
- `_check_spread_exit()` — exit logic for both types
- `_liquidate_all_spread_aware()` — closes both types

**Rule: Do NOT split a method that handles both VASS and MICRO into separate files.** Keep the method intact in one mixin. The method groupings in the split plan above already account for this — follow those groupings exactly.

---

## COMMON MISTAKES TO AVOID

1. **Forgetting an import** — A method uses `config.KILL_SWITCH_PCT` but you forgot `import config` in the mixin file → NameError at runtime
2. **Circular imports** — Mixin A imports from Mixin B which imports from the main class → ImportError
3. **Wrong MRO** — `QCAlgorithm` not listed last in inheritance → TypeError at class creation
4. **Moving `__init__` code** — Instance variables must be initialized in the main class `__init__`, not in mixins
5. **Breaking `super()` calls** — If any method uses `super()`, the MRO must be compatible
6. **Forgetting `self`** — When extracting a method to a mixin, it must still be `def method(self, ...):`
7. **Module-level code** — If `options_engine.py` has module-level constants or variables (not inside a class), they must stay in the right file or be importable
8. **`__all__` exports** — If any `__init__.py` uses `__all__`, update it
9. **Duplicate class definitions** — After moving `SpreadStrategy` to `options_models.py`, delete it from `options_engine.py`. If the SAME class exists in TWO files, `isinstance()` checks and enum comparisons will SILENTLY FAIL (two different `SpreadStrategy` enums are not equal).
10. **Type checking imports** — Use `TYPE_CHECKING` guard for imports only needed by type hints to avoid circular dependencies

---

## CRITICAL PITFALLS (READ CAREFULLY)

### Pitfall 1: `scripts/qc_backtest.sh` Only Syncs `main.py` + `config.py` From Root

The sync script at line 71 does:
```bash
cp "$SRC/main.py" "$SRC/config.py" "$DEST/"
```

New root-level files (`main_options.py`, `main_orders.py`, `main_risk.py`, `main_intraday.py`) **will NOT be synced** to QuantConnect. Backtests will crash with `ImportError`.

**FIX REQUIRED:** Update `scripts/qc_backtest.sh` line 71 to also copy the new root mixin files:
```bash
# Copy main files AND new mixin files
cp "$SRC/main.py" "$SRC/config.py" "$DEST/"
cp "$SRC"/main_*.py "$DEST/" 2>/dev/null || true
```

### Pitfall 2: 20+ Import Sites Reference `options_engine.py` For Moved Classes

These files ALL import classes directly from `engines.satellite.options_engine`:

**Production code:**
- `main.py` (line 28): imports `ExitOrderTracker, OptionContract, OptionsEngine, SpreadFillTracker, SpreadStrategy, is_expiration_firewall_day`
- `portfolio/portfolio_router.py` (line 24): imports `SpreadPosition`

**Test files (10+ files, 20+ import statements):**
- `tests/test_options_engine.py`: imports `EntryScore, IVSensor, OptionContract, OptionDirection, OptionsEngine, OptionsPosition, SpreadPosition, SpreadStrategy`
- `tests/test_micro_regime_engine.py`: imports `MicroRegimeEngine, VIXSnapshot`
- `tests/integration/test_options_flow.py`: imports `OptionContract, OptionDirection, OptionsEngine`
- `tests/integration/test_scenario_integration.py`: imports from options_engine
- `tests/integration/test_full_ondata_simulation.py`: imports `MicroRegimeEngine, OptionsEngine`
- `tests/integration/test_ondata_flow.py`: imports `OptionsEngine`
- `tests/integration/test_options_integration.py`: imports `OptionsEngine`
- `tests/integration/test_remaining_gaps.py`: imports `OptionsEngine`
- `tests/scenarios/test_rejection_recovery_scenario.py`: imports `OptionsEngine`
- Inside `test_options_engine.py` there are also 12+ inline imports within test methods

**FIX REQUIRED (choose ONE approach):**

**Option A (RECOMMENDED): Add backward-compatible re-exports in `options_engine.py`:**
After moving classes OUT of `options_engine.py`, add re-exports so ALL existing imports still work:
```python
# engines/satellite/options_engine.py — at the top, after own imports
# Backward-compatible re-exports (moved to separate files)
from engines.satellite.options_models import (
    EntryScore,
    ExitOrderTracker,
    OptionContract,
    OptionsPosition,
    SpreadFillTracker,
    SpreadPosition,
    SpreadStrategy,
    get_expiration_firewall_day,
    is_expiration_firewall_day,
)
from engines.satellite.iv_sensor import IVSensor
from engines.satellite.micro_regime_engine import (
    MicroRegimeEngine,
    MicroRegimeState,
    VIXSnapshot,
)
```
This means `from engines.satellite.options_engine import SpreadPosition` **still works**. Zero changes needed in test files or other production code.

**Option B: Update ALL 20+ import sites** — Riskier, more work, more chance of missing one.

**USE OPTION A.** It's safer and means fewer files to modify.

### Pitfall 3: Module-Level Functions Outside Any Class

`options_engine.py` has two standalone functions NOT inside any class:
- `get_expiration_firewall_day()` (line 767) — used by `main.py`
- `is_expiration_firewall_day()` (line 808) — imported directly by `main.py` line 34

These must move to `options_models.py` AND be re-exported from `options_engine.py` (per Pitfall 2 fix).

### Pitfall 4: `__pycache__` Stale Bytecode

After moving classes between files, old `.pyc` files in `__pycache__/` can shadow the new module structure. Old bytecode for `options_engine.pyc` still "contains" the moved classes, causing confusing import behavior.

**FIX REQUIRED:** After ALL refactoring is complete, clean all caches:
```bash
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find . -name "*.pyc" -delete 2>/dev/null || true
```

### Pitfall 5: `engines/satellite/__init__.py` Does NOT Export Options Classes

Currently `engines/satellite/__init__.py` only exports `HedgeEngine` and `MeanReversionEngine` — it does NOT re-export from options_engine. This means creating new files in `engines/satellite/` does NOT require `__init__.py` changes (imports use full paths like `from engines.satellite.options_models import ...`).

**Action:** No change needed to `engines/satellite/__init__.py`, but verify this is still true after refactoring.

### Pitfall 6: The `minify_workspace.py` Script

This script runs `ast.parse()` on ALL `.py` files in the lean workspace to strip comments/docstrings. After refactoring:
- Each new mixin file must be valid Python that passes `ast.parse()`
- Mixin classes with `self.xxx` references to attributes defined elsewhere are syntactically valid (just a method calling an attribute) — this is fine
- BUT if a mixin file has bare imports that reference QC types not available locally, `ast.parse()` still works (imports are just syntax, not resolved at parse time)

**Action:** After refactoring, verify: `python3.11 scripts/minify_workspace.py` runs without errors.

### Pitfall 7: Inline Imports Inside Methods

Several methods in both `main.py` and `options_engine.py` have **inline imports inside function bodies**:
- `main.py` line 1274: `from datetime import timedelta`
- `options_engine.py` line 424: `from datetime import datetime`
- `options_engine.py` line 904: `from engines.satellite.options_engine import OptionDirection`

That last one (line 904) is a **self-import** inside `MicroRegimeEngine.from_dict()`. After moving `MicroRegimeEngine` to `micro_regime_engine.py`, this self-import path BREAKS because the class is no longer in `options_engine.py`.

**FIX REQUIRED:** When moving `MicroRegimeEngine`, update its inline import from:
```python
from engines.satellite.options_engine import OptionDirection
```
to:
```python
from models.enums import OptionDirection
```
(This is actually the correct import — `OptionDirection` lives in `models/enums.py`, not in options_engine. The V6.4 comment in main.py line 37 confirms this was a known issue.)

---

## SUMMARY

| Action | From | To | Technique |
|--------|------|----|-----------|
| Extract data classes | `options_engine.py` | `options_models.py` | Module extraction |
| Extract IVSensor | `options_engine.py` | `iv_sensor.py` | Module extraction |
| Extract MicroRegimeEngine | `options_engine.py` | `micro_regime_engine.py` | Module extraction |
| Split OptionsEngine methods | `options_engine.py` | `options_entry.py`, `options_exit.py`, `options_spread_select.py`, `options_position.py` | Mixin inheritance |
| Split AlphaNextGen methods | `main.py` | `main_options.py`, `main_orders.py`, `main_risk.py`, `main_intraday.py` | Mixin inheritance |

**Target: Every file < 200KB (safety margin below 256KB limit)**

**Zero logic changes. Zero behavior changes. Just reorganization.**
