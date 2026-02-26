"""
Options Engine V2.1.1 - Dual-Mode Architecture for QQQ Options.

V2.1.1 COMPLETE REDESIGN with two distinct operating modes:

MODE 1: SWING MODE (5-45 DTE)
- 15% allocation of portfolio
- Multi-day positions (hold overnight allowed)
- Uses macro regime for direction
- 4-strategy portfolio: Debit Spreads, Credit Spreads, ITM Long, Protective Puts
- Simple intraday filters (not Micro Regime)

MODE 2: INTRADAY MODE (0-2 DTE)
- 5% allocation of portfolio
- Same-day entry and exit (must close by configured intraday cutoff)
- Uses MICRO REGIME ENGINE for decision making
- VIX Level × VIX Direction = 21 distinct trading regimes
- Strategies: Debit Fade, Credit Spreads, ITM Momentum, Protective Puts

KEY INSIGHT: VIX Direction is THE key differentiator.
Same VIX level + different direction = OPPOSITE strategies!

VIX at 25 and FALLING = Recovery starting, FADE the move (buy calls)
VIX at 25 and RISING = Fear building, RIDE the move (buy puts)

ENTRY TIMING MATTERS MORE FOR SHORTER DTE:
- 2 DTE: 2-hour window = 15% of option's life → Micro Regime ESSENTIAL
- 14 DTE: 2-hour window = 2% of option's life → Simple filters sufficient

Spec: docs/v2-specs/V2_1_OPTIONS_ENGINE_DESIGN.txt
"""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any, Deque, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from AlgorithmImports import QCAlgorithm
    from engines.core.risk_engine import RiskEngine

import config
from engines.core.risk_engine import GreeksSnapshot
from engines.satellite.intraday_exit_profile import (
    apply_intraday_stop_overrides_impl,
    apply_intraday_target_overrides_impl,
    get_intraday_exit_profile_impl,
    get_trail_config_impl,
)
from engines.satellite.itm_horizon_engine import ITMHorizonEngine
from engines.satellite.iv_sensor import IVSensor, VIXSnapshot
from engines.satellite.micro_entry_engine import MicroEntryEngine
from engines.satellite.options_entry_evaluator import check_entry_signal_impl
from engines.satellite.options_exit_evaluator import check_exit_signals_impl
from engines.satellite.options_expiration_exit import check_expiring_options_force_exit_impl
from engines.satellite.options_intraday_entry import check_intraday_entry_signal_impl
from engines.satellite.options_micro_signal import generate_micro_intraday_signal_impl
from engines.satellite.options_partial_oco import (
    get_intraday_partial_fill_oco_seed_impl,
    get_partial_fill_oco_seed_impl,
    get_pending_intraday_partial_oco_seed_impl,
)
from engines.satellite.options_pending_guard import (
    cancel_pending_intraday_entry_impl,
    cancel_pending_intraday_exit_impl,
    clear_stale_pending_intraday_entry_if_orphaned_impl,
    clear_stale_pending_spread_entry_if_orphaned_impl,
    get_pending_entry_contract_symbol_impl,
    get_pending_intraday_entry_lane_impl,
    has_pending_intraday_entry_impl,
    has_pending_intraday_exit_impl,
    mark_pending_intraday_exit_impl,
    normalize_symbol_key_impl,
    sync_pending_intraday_exit_flags_impl,
)
from engines.satellite.options_position_manager import (
    clear_all_positions_impl,
    record_intraday_result_impl,
    register_entry_impl,
    register_spread_entry_impl,
    remove_intraday_position_impl,
    remove_position_impl,
    remove_spread_position_impl,
)
from engines.satellite.options_primitives import (
    EntryScore,
    ExitOrderTracker,
    MicroRegimeEngine,
    MicroRegimeState,
    OptionContract,
    OptionsPosition,
    SpreadFillTracker,
    SpreadPosition,
    SpreadStrategy,
    _normalize_intraday_strategy_value,
    get_expiration_firewall_day,
    is_expiration_firewall_day,
)
from engines.satellite.options_state_manager import (
    calculate_position_greeks_impl,
    check_greeks_breach_impl,
    get_state_for_persistence_impl,
    reset_options_engine_daily_state_impl,
    reset_options_engine_state_impl,
    restore_state_impl,
    update_position_greeks_impl,
)
from engines.satellite.options_trade_resolver import resolve_trade_signal_impl
from engines.satellite.vass_assignment_manager import (
    check_assignment_margin_buffer_impl,
    check_assignment_risk_exit_impl,
    check_overnight_itm_short_risk_impl,
    check_premarket_itm_shorts_impl,
    check_short_leg_itm_exit_impl,
    get_assignment_aware_size_multiplier_impl,
    handle_partial_assignment_impl,
    is_short_leg_deep_itm_impl,
)
from engines.satellite.vass_entry_engine import VASSEntryEngine
from engines.satellite.vass_exit_evaluator import check_spread_exit_signals_impl
from engines.satellite.vass_exit_profile import (
    get_vass_exit_profile_impl,
    record_vass_mfe_diag_impl,
    resolve_qqq_atr_pct_impl,
)
from engines.satellite.vass_risk_firewall import (
    check_friday_firewall_exit_impl,
    check_overnight_gap_protection_exit_impl,
)
from engines.satellite.vass_signal_validator import (
    check_credit_spread_entry_signal_impl,
    check_spread_entry_signal_impl,
)
from models.enums import (
    OptionDirection,  # V6.4: Unified from models.enums (fixes P0 duplicate enum bug)
)
from models.enums import (
    IntradayStrategy,
    MicroRegime,
    OptionsMode,
    QQQMove,
    Urgency,
    VIXDirection,
    VIXLevel,
    WhipsawState,
)
from models.target_weight import TargetWeight

# V6.4: OptionDirection moved to models.enums (fixes P0 duplicate enum bug)
# Previously defined here, caused type mismatch when comparing enums


class OptionsEngine:
    """
    Options Engine V2.1.1 - Dual-Mode Architecture.

    Operates in TWO DISTINCT MODES based on DTE:

    MODE 1: SWING MODE (5-45 DTE)
    - 15% allocation, multi-day positions
    - Uses macro regime for direction
    - 4-factor entry scoring (ADX, Momentum, IV, Liquidity)
    - Simple intraday filters (not Micro Regime)

    MODE 2: INTRADAY MODE (0-2 DTE)
    - 5% allocation, same-day trades
    - Uses MICRO REGIME ENGINE for decision making
    - VIX Level × VIX Direction = 21 regimes
    - Strategies: Debit Fade, Credit Spreads, ITM Momentum

    Note: This engine does NOT place orders. It only provides
    signals via TargetWeight objects for the Portfolio Router.

    Spec: docs/v2-specs/V2_1_OPTIONS_ENGINE_DESIGN.txt
    """

    def __init__(self, algorithm: Optional["QCAlgorithm"] = None):
        """Initialize Options Engine with dual-mode support."""
        self.algorithm = algorithm

        # Position tracking (separate for each mode)
        self._swing_position: Optional[OptionsPosition] = None
        self._intraday_position: Optional[OptionsPosition] = None
        self._intraday_position_engine: Optional[str] = None  # MICRO/ITM ownership
        # Engine-isolated intraday position containers.
        self._intraday_positions: Dict[str, List[OptionsPosition]] = {
            "MICRO": [],
            "ITM": [],
        }

        # V2.3: Spread position tracking (replaces single-leg for swing mode)
        self._spread_position: Optional[SpreadPosition] = None
        self._spread_positions: List[SpreadPosition] = []
        # Reserved compatibility slot for legacy persisted state payloads.

        # Legacy single position (for backwards compatibility)
        self._position: Optional[OptionsPosition] = None

        # Trade counters
        self._trades_today: int = 0
        self._intraday_trades_today: int = 0
        self._intraday_call_trades_today: int = 0
        self._intraday_put_trades_today: int = 0
        self._intraday_itm_trades_today: int = 0
        self._intraday_micro_trades_today: int = 0
        self._swing_trades_today: int = 0  # V2.9: Swing mode counter
        self._total_options_trades_today: int = 0  # V2.9: Global counter (Bug #4 fix)
        self._last_trade_date: Optional[str] = None

        # Current operating mode
        self._current_mode: OptionsMode = OptionsMode.SWING

        # V2.1.1: Micro Regime Engine for intraday trading
        self._micro_regime_engine = MicroRegimeEngine(
            log_func=self.log, regime_decision_cb=self._record_regime_decision
        )

        # V2.1.1: VIX tracking for simple intraday filters (Swing Mode)
        self._vix_at_open: float = 0.0
        self._spy_at_open: float = 0.0
        self._spy_gap_pct: float = 0.0

        # Pending entry state (set by check_entry_signal, used by register_entry)
        self._pending_contract: Optional[OptionContract] = None
        self._pending_entry_score: Optional[float] = None
        self._pending_num_contracts: Optional[int] = None
        self._pending_stop_pct: Optional[float] = None
        self._pending_stop_price: Optional[float] = None
        self._pending_target_price: Optional[float] = None
        self._pending_entry_strategy: Optional[str] = None

        # V2.3: Pending spread entry state
        self._pending_spread_long_leg: Optional[OptionContract] = None
        self._pending_spread_short_leg: Optional[OptionContract] = None
        self._pending_spread_type: Optional[str] = None
        self._pending_net_debit: Optional[float] = None
        self._pending_max_profit: Optional[float] = None
        self._pending_spread_width: Optional[float] = None
        self._pending_spread_entry_vix: Optional[float] = None
        self._pending_spread_entry_since: Optional[datetime] = None

        # V2.3 FIX: Prevent order spam - track failed entry attempts
        self._entry_attempted_today: bool = False
        self._spread_attempts_today_by_key: Dict[str, int] = {}
        self._spread_attempt_last_mark_by_key: Dict[str, str] = {}
        # V2.21: Post-rejection margin cap for adaptive retry sizing
        self._rejection_margin_cap: Optional[float] = None
        self._swing_time_warning_logged: bool = False

        # V2.3.21: Spread scan throttle - only attempt every 15 minutes to reduce log noise
        self._last_spread_scan_time: Optional[str] = None

        # V2.4.3: Spread FAILURE cooldown - don't retry for 4 hours after construction fails
        # Prevents 340+ retries when no valid contracts exist
        self._spread_failure_cooldown_until: Optional[str] = None
        self._spread_failure_cooldown_until_by_dir: dict = {}
        self._last_spread_failure_stats: Optional[str] = None
        self._last_credit_failure_stats: Optional[str] = None
        self._last_entry_validation_failure: Optional[str] = None
        self._last_intraday_validation_failure_by_lane: Dict[str, Optional[str]] = {
            "MICRO": None,
            "ITM": None,
        }
        self._last_intraday_validation_detail_by_lane: Dict[str, Optional[str]] = {
            "MICRO": None,
            "ITM": None,
        }
        # Detailed slot/limit rejection context for MICRO drop telemetry.
        self._last_trade_limit_failure: Optional[str] = None
        self._last_trade_limit_detail: Optional[str] = None
        self._last_micro_no_trade_log_by_key: Dict[str, str] = {}
        # MICRO anti-churn: brief cooldown for same strategy after close.
        self._last_intraday_close_time: Optional[datetime] = None
        self._last_intraday_close_strategy: Optional[str] = None

        # V2.3.2 FIX #4: Track if pending entry is intraday (for correct position registration)
        self._pending_intraday_entry: bool = False
        self._pending_intraday_entry_since: Optional[datetime] = None
        self._pending_intraday_entry_engine: Optional[str] = None  # MICRO/ITM lane
        self._pending_intraday_entries: Dict[str, Dict[str, Any]] = {}  # symbol -> pending payload

        # V2.3.3 FIX #3: Prevent duplicate exit signals while waiting for fill
        self._pending_intraday_exit: bool = False
        self._pending_intraday_exit_engine: Optional[str] = None  # MICRO/ITM lane
        self._pending_intraday_exit_lanes: set = set()
        self._pending_intraday_exit_symbols: set = set()
        self._transition_context_snapshot: Optional[Dict[str, Any]] = None

        # V2.6 Bug #16: Post-trade margin cooldown tracking
        # After closing a spread, wait before new entry (T+1 settlement)
        self._last_spread_exit_time: Optional[str] = None

        # V2.8: VASS (Volatility-Adaptive Strategy Selection)
        self._iv_sensor = IVSensor(
            smoothing_minutes=config.VASS_IV_SMOOTHING_MINUTES,
            log_func=self.log,
        )
        self._itm_horizon_engine = ITMHorizonEngine(log_func=self.log)
        self._micro_entry_engine = MicroEntryEngine(log_func=self.log)
        self._vass_entry_engine = VASSEntryEngine(log_func=self.log)
        self._vass_entry_engine_enabled = bool(getattr(config, "VASS_ENTRY_ENGINE_ENABLED", True))

        # V2.27: Win Rate Gate - rolling spread trade result tracker
        self._spread_result_history: List[bool] = []  # True=win, False=loss
        self._win_rate_shutoff: bool = False  # True when win rate < shutoff threshold
        self._win_rate_shutoff_date: Optional[str] = None  # YYYY-MM-DD shutoff activation
        self._paper_track_history: List[bool] = []  # Paper trades during shutoff
        self._last_win_rate_monitor_log_key: Optional[str] = None
        # V10.10: VASS anti-cluster/day-gap memory is owned by VASSEntryEngine.
        # Phase C: staged neutrality de-risk memory by spread key.
        # key = "<long_symbol>|<short_symbol>", value = first neutrality timestamp.
        self._spread_neutrality_warn_by_key: Dict[str, datetime] = {}

        # V6.5 FIX: Prevent gamma pin exit from firing every minute
        # Once triggered, don't trigger again for the same position
        self._gamma_pin_exit_triggered: bool = False
        # Throttle repeated ITM short-leg risk logs.
        self._last_short_leg_itm_exit_log: Dict[str, datetime] = {}
        # CALL-gate tracking: pause new CALLs after repeated losses.
        self._call_consecutive_losses: int = 0
        self._call_cooldown_until_date: Optional[datetime.date] = None
        self._put_consecutive_losses: int = 0
        self._put_cooldown_until_date: Optional[datetime.date] = None
        # V10.7: Swing loss breaker for VASS spread entries.

        # V9.4 P0: Exit signal cooldown — prevent per-minute spam when close order fails.
        # Maps spread_key -> last exit signal datetime. If exit fires and close fails,
        # don't re-fire for SPREAD_EXIT_RETRY_MINUTES.
        self._spread_exit_signal_cooldown: Dict[str, datetime] = {}
        # Track which spreads have already logged the hold guard (log once, not every minute)
        self._spread_hold_guard_logged: set = set()
        # Throttle force-exit hold-skip logs (symbol -> YYYY-MM-DD).
        self._intraday_force_exit_hold_skip_log_date: Dict[str, str] = {}
        # Throttle expiration-hammer skip logs for MICRO intraday strategies.
        self._expiring_hammer_skip_log_date: Dict[str, str] = {}

    def log(self, message: str, trades_only: bool = False) -> None:
        """
        Log via algorithm with LiveMode awareness.

        Args:
            message: Log message to output.
            trades_only: If True, always log (for trade entries/exits/errors).
                        If False, only log in LiveMode (for diagnostics).
        """
        if self.algorithm:
            is_live = bool(hasattr(self.algorithm, "LiveMode") and self.algorithm.LiveMode)
            text = str(message or "")
            # V2.18.1: Fixed - was logging everything in debug mode, causing backtest timeout
            # Only log if: trades_only=True OR we're in LiveMode
            if trades_only:
                if (not is_live) and text.startswith("WIN_RATE_GATE:"):
                    helper = getattr(self.algorithm, "_log_high_frequency_event", None)
                    if callable(helper):
                        gate_key = text.split("|", 1)[0].strip().replace(" ", "_")
                        helper(
                            config_flag="LOG_WIN_RATE_GATE_BACKTEST_ENABLED",
                            category="WIN_RATE_GATE",
                            reason_key=gate_key,
                            message=text,
                        )
                    elif bool(getattr(config, "LOG_WIN_RATE_GATE_BACKTEST_ENABLED", False)):
                        self.algorithm.Log(text)
                else:
                    self.algorithm.Log(text)
            elif is_live:
                self.algorithm.Log(text)
            # In backtest mode with trades_only=False, skip logging (silent)

    def _symbol_str(self, symbol) -> str:
        """Normalize QC Symbol/string-like values to plain string for TargetWeight."""
        if symbol is None:
            return ""
        if isinstance(symbol, str):
            return symbol.strip().upper()
        try:
            return str(symbol).strip().upper()
        except Exception:
            return ""

    def _symbol_key(self, symbol) -> str:
        """
        Canonical symbol key for internal comparisons/maps.

        Collapses repeated whitespace so fills/order events and contract strings
        that format spacing differently still match the same option contract.
        """
        text = self._symbol_str(symbol)
        if not text:
            return ""
        return " ".join(text.split())

    def _get_intraday_force_exit_hhmm(self) -> Tuple[int, int]:
        """Return effective intraday force-exit time as (hour, minute).

        Uses scheduler-provided dynamic close time when available (early-close days),
        otherwise falls back to static INTRADAY_FORCE_EXIT_TIME.
        """
        try:
            scheduler = (
                getattr(self.algorithm, "scheduler", None) if self.algorithm is not None else None
            )
            getter = getattr(scheduler, "get_intraday_options_close_hhmm", None)
            if callable(getter):
                hh, mm = getter()
                return int(hh), int(mm)
        except Exception:
            pass

        force_exit_cfg = str(getattr(config, "INTRADAY_FORCE_EXIT_TIME", "15:15"))
        try:
            hh, mm = force_exit_cfg.split(":")
            return int(hh), int(mm)
        except Exception:
            return 15, 15

    def _canonical_intraday_strategy(
        self, strategy: Optional["IntradayStrategy"]
    ) -> Optional["IntradayStrategy"]:
        """Map legacy strategy aliases to canonical runtime strategy."""
        if strategy is None:
            return None
        value = _normalize_intraday_strategy_value(getattr(strategy, "value", strategy))
        return IntradayStrategy(value)

    def _canonical_intraday_strategy_name(self, strategy_name: Optional[str]) -> str:
        """Canonical string form used by hold/exit logic."""
        return _normalize_intraday_strategy_value(strategy_name)

    def _is_itm_momentum_strategy_name(self, strategy_name: Optional[str]) -> bool:
        """True when strategy name maps to ITM momentum."""
        value = self._canonical_intraday_strategy_name(strategy_name)
        return value == IntradayStrategy.ITM_MOMENTUM.value

    def _intraday_engine_lane_from_strategy(self, strategy_name: Optional[str]) -> str:
        """Map strategy to engine lane key used by pending-entry/exit locks."""
        return "ITM" if self._is_itm_momentum_strategy_name(strategy_name) else "MICRO"

    def _pending_intraday_entry_key(self, symbol: str, lane: Optional[str]) -> str:
        """Build stable pending-entry key with lane isolation."""
        symbol_norm = self._symbol_key(symbol)
        lane_norm = str(lane or "").upper()
        if not lane_norm:
            return symbol_norm
        return f"{lane_norm}|{symbol_norm}"

    def _pending_intraday_symbol_from_key(self, key: str) -> str:
        key_text = str(key or "")
        if "|" in key_text:
            return self._symbol_key(key_text.split("|", 1)[1])
        return self._symbol_key(key_text)

    def get_vix_5d_change(self) -> Optional[float]:
        """Public accessor for current VIX 5D change."""
        try:
            return self._iv_sensor.get_vix_5d_change()
        except Exception:
            return None

    def get_position_live_dte(self, position: Optional[OptionsPosition]) -> Optional[int]:
        """Public wrapper for live DTE resolution."""
        return self._get_position_live_dte(position)

    def find_intraday_lane_by_symbol(self, symbol: Optional[str]) -> Optional[str]:
        """Public wrapper for intraday lane lookup."""
        return self._find_intraday_lane_by_symbol(symbol)

    def _find_pending_intraday_entry_key(
        self, symbol: str, lane: Optional[str] = None
    ) -> Optional[str]:
        """Find pending-entry key by symbol (+ optional lane), backward compatible."""
        symbol_norm = self._symbol_key(symbol)
        if not symbol_norm:
            return None
        lane_norm = str(lane or "").upper()
        if lane_norm:
            direct_key = self._pending_intraday_entry_key(symbol_norm, lane_norm)
            if direct_key in self._pending_intraday_entries:
                return direct_key

        if symbol_norm in self._pending_intraday_entries:
            payload = self._pending_intraday_entries.get(symbol_norm) or {}
            payload_lane = str(payload.get("lane", "")).upper()
            if not lane_norm or payload_lane == lane_norm:
                return symbol_norm

        for key, payload in self._pending_intraday_entries.items():
            payload_sym = (
                self._symbol_key(payload.get("symbol", "")) if isinstance(payload, dict) else ""
            )
            if not payload_sym:
                payload_sym = self._pending_intraday_symbol_from_key(key)
            if payload_sym != symbol_norm:
                continue
            if lane_norm:
                payload_lane = str((payload or {}).get("lane", "")).upper()
                if payload_lane and payload_lane != lane_norm:
                    continue
            return key
        return None

    def _get_pending_intraday_entry_payload(
        self, symbol: str, lane: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        key = self._find_pending_intraday_entry_key(symbol=symbol, lane=lane)
        if not key:
            return None
        payload = self._pending_intraday_entries.get(key)
        return payload if isinstance(payload, dict) else None

    def _pop_pending_intraday_entry_payload(
        self, symbol: str, lane: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        key = self._find_pending_intraday_entry_key(symbol=symbol, lane=lane)
        if not key:
            return None
        payload = self._pending_intraday_entries.pop(key, None)
        return payload if isinstance(payload, dict) else None

    def _refresh_legacy_intraday_mirrors(self) -> None:
        """Keep legacy single-position mirrors in sync with lane containers."""
        if self._intraday_positions.get("ITM"):
            self._intraday_position = self._intraday_positions["ITM"][0]
            self._intraday_position_engine = "ITM"
            return
        if self._intraday_positions.get("MICRO"):
            self._intraday_position = self._intraday_positions["MICRO"][0]
            self._intraday_position_engine = "MICRO"
            return
        self._intraday_position = None
        self._intraday_position_engine = None

    def _get_intraday_lane_position(self, lane: str) -> Optional[OptionsPosition]:
        lane_key = str(lane or "").upper()
        lane_positions = self._intraday_positions.get(lane_key) or []
        if lane_positions:
            return lane_positions[0]
        return None

    def _set_intraday_lane_position(self, lane: str, position: Optional[OptionsPosition]) -> None:
        lane_key = str(lane or "").upper()
        if lane_key not in self._intraday_positions:
            self._intraday_positions[lane_key] = []
        if position is None:
            self._intraday_positions[lane_key] = []
            self._refresh_legacy_intraday_mirrors()
            return
        self._intraday_positions[lane_key].append(position)
        self._intraday_position = position
        self._intraday_position_engine = lane_key

    def _find_intraday_lane_by_symbol(self, symbol: str) -> Optional[str]:
        symbol_norm = self._symbol_key(symbol)
        if not symbol_norm:
            return None
        for lane in ("ITM", "MICRO"):
            for pos in self._intraday_positions.get(lane) or []:
                if (
                    pos is not None
                    and pos.contract is not None
                    and self._symbol_key(pos.contract.symbol) == symbol_norm
                ):
                    return lane
        return None

    def get_intraday_positions(self) -> List[OptionsPosition]:
        positions: List[OptionsPosition] = []
        for lane in ("ITM", "MICRO"):
            lane_positions = self._intraday_positions.get(lane) or []
            for pos in lane_positions:
                if pos is not None:
                    positions.append(pos)
        return positions

    def _infer_intraday_strategy_from_order_tag(self, order_tag: Optional[str]) -> str:
        """Best-effort strategy inference from order tag for partial-fill OCO recovery."""
        text = str(order_tag or "").upper()
        if not text:
            return IntradayStrategy.NO_TRADE.value
        if "PROTECTIVE_PUTS" in text or text.startswith("HEDGE:"):
            return IntradayStrategy.PROTECTIVE_PUTS.value
        if "ITM_MOMENTUM" in text or "DEBIT_MOMENTUM" in text:
            return IntradayStrategy.ITM_MOMENTUM.value
        if "MICRO_OTM_MOMENTUM" in text:
            return IntradayStrategy.MICRO_OTM_MOMENTUM.value
        if "MICRO_EOD_SWEEP" in text:
            return IntradayStrategy.MICRO_OTM_MOMENTUM.value
        if "MICRO_DEBIT_FADE" in text or "DEBIT_FADE" in text:
            return IntradayStrategy.MICRO_DEBIT_FADE.value
        return IntradayStrategy.NO_TRADE.value

    def _get_position_live_dte(self, position: Optional[OptionsPosition]) -> Optional[int]:
        """Best-effort live DTE using expiry date and current algorithm time."""
        if position is None or position.contract is None:
            return None
        expiry_str = str(getattr(position.contract, "expiry", "") or "")[:10]
        if not expiry_str:
            return None
        try:
            expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d").date()
            if self.algorithm is not None and hasattr(self.algorithm, "Time"):
                current_date = self.algorithm.Time.date()
                return max(0, (expiry_date - current_date).days)
            # QC-compliant fallback for unit contexts without algorithm clock.
            return max(0, int(getattr(position.contract, "days_to_expiry", 0)))
        except Exception:
            try:
                return max(0, int(getattr(position.contract, "days_to_expiry", 0)))
            except Exception:
                return None

    def should_hold_intraday_overnight(
        self,
        position: Optional[OptionsPosition] = None,
    ) -> bool:
        """
        Return True when the current intraday position is eligible for overnight hold.

        V10.1 policy: only ITM_MOMENTUM positions opened with sufficient entry DTE can
        bypass the intraday force-close cutoff.
        """
        hold_enabled = bool(getattr(config, "INTRADAY_ITM_HOLD_OVERNIGHT_ENABLED", False))
        if self._itm_horizon_engine.enabled():
            hold_enabled = bool(getattr(config, "ITM_HOLD_OVERNIGHT_ENABLED", True))
        if not hold_enabled:
            return False
        pos = position if position is not None else self._intraday_position
        if pos is None:
            return False
        if not self._is_itm_momentum_strategy_name(getattr(pos, "entry_strategy", "")):
            return False
        try:
            entry_dte = int(getattr(pos.contract, "days_to_expiry", 0))
        except Exception:
            entry_dte = 0
        live_dte = self._get_position_live_dte(pos)
        if self._itm_horizon_engine.enabled():
            return self._itm_horizon_engine.should_hold_overnight(
                entry_dte=entry_dte,
                live_dte=live_dte,
            )

        min_entry_dte = int(getattr(config, "INTRADAY_ITM_HOLD_MIN_ENTRY_DTE", 3))
        if entry_dte < min_entry_dte:
            return False
        if live_dte is None:
            return False
        force_exit_dte = int(getattr(config, "INTRADAY_ITM_FORCE_EXIT_DTE", 1))
        return live_dte > force_exit_dte

    def record_intraday_result(
        self,
        symbol: str,
        is_win: bool,
        current_time: Optional[str] = None,
        strategy: Optional[str] = None,
    ) -> None:
        """Track MICRO directional loss streaks/cooldowns (ITM is sovereign)."""
        record_intraday_result_impl(
            self,
            symbol=symbol,
            is_win=is_win,
            current_time=current_time,
            strategy=strategy,
        )

    # =========================================================================
    # V6.10 P5: CHOPPY MARKET DETECTION
    # =========================================================================

    def get_choppy_market_scale(self) -> float:
        """
        V6.10 P5: Detect choppy market conditions and return size scale factor.

        Uses the MicroRegimeEngine's whipsaw detection to identify choppy markets.
        When market is choppy/whipsawing, reduces position size to limit losses.

        Returns:
            1.0 for normal markets, CHOPPY_SIZE_REDUCTION (0.5) for choppy markets.
        """
        # Check if filter is enabled
        if not getattr(config, "CHOPPY_MARKET_FILTER_ENABLED", False):
            return 1.0

        # Get whipsaw state from Micro Regime Engine
        try:
            whipsaw_state, reversal_count = self._micro_regime_engine._detect_whipsaw()

            # Check against configured threshold
            choppy_threshold = getattr(config, "CHOPPY_REVERSAL_COUNT", 3)

            if reversal_count >= choppy_threshold:
                reduction = getattr(config, "CHOPPY_SIZE_REDUCTION", 0.50)
                self.log(
                    f"CHOPPY_FILTER: Size reduction | Reversals={reversal_count} >= {choppy_threshold} | "
                    f"Scale={reduction:.0%} | WhipsawState={whipsaw_state.value}",
                    trades_only=True,
                )
                return reduction

        except Exception as e:
            # If detection fails, don't apply reduction
            self.log(f"CHOPPY_FILTER: Detection error - {e}", trades_only=True)

        return 1.0

    # =========================================================================
    # V5.3: CONVICTION & POSITION MANAGEMENT
    # =========================================================================

    def resolve_trade_signal(
        self,
        engine: str,  # "MICRO" or "VASS"
        engine_direction: Optional[str],  # "BULLISH" or "BEARISH"
        engine_conviction: bool,
        macro_direction: str,  # "BULLISH", "BEARISH", or "NEUTRAL"
        conviction_strength: Optional[float] = None,  # V6.9: For NEUTRAL VETO gating (e.g., UVXY %)
        engine_regime: Optional[str] = None,  # V6.15: Micro regime name for NEUTRAL veto gating
        engine_recommended_direction: Optional[
            str
        ] = None,  # V6.15: Ensure veto aligns with engine direction
        overlay_state: Optional[
            str
        ] = None,  # V6.22: Fast overlay (NORMAL/EARLY_STRESS/STRESS/RECOVERY)
        allow_macro_veto: bool = True,  # Optional hard-veto guard for conviction overrides
    ) -> Tuple[bool, Optional[str], str]:
        """
        V5.3: Resolve whether to trade based on engine signal vs macro.

        Resolution Logic:
        - ALIGNED: Engine and Macro agree → TRADE
        - MISALIGNED + CONVICTION: Engine overrides Macro → TRADE (veto)
        - MISALIGNED + NO CONVICTION: Uncertainty → NO TRADE

        Args:
            engine: Which engine is signaling ("MICRO" or "VASS")
            engine_direction: Engine's directional view ("BULLISH" or "BEARISH")
            engine_conviction: True if engine has strong signal
            macro_direction: Macro regime's direction

        Returns:
            Tuple of (should_trade, final_direction, reason)
        """
        return resolve_trade_signal_impl(
            self,
            engine=engine,
            engine_direction=engine_direction,
            engine_conviction=engine_conviction,
            macro_direction=macro_direction,
            conviction_strength=conviction_strength,
            engine_regime=engine_regime,
            engine_recommended_direction=engine_recommended_direction,
            overlay_state=overlay_state,
            allow_macro_veto=allow_macro_veto,
        )

    def generate_micro_intraday_signal(
        self,
        vix_current: float,
        vix_open: float,
        qqq_current: float,
        qqq_open: float,
        uvxy_pct: float,
        macro_regime_score: float,
        current_time: str,
        vix_level_override: Optional[float] = None,
        premarket_shock_pct: float = 0.0,
    ) -> Tuple[bool, Optional[OptionDirection], Optional["MicroRegimeState"], str]:
        """
        V6.3: Unified entry point for Micro intraday signal generation.

        Consolidates update, conviction check, and resolution into a single method
        to eliminate dual-update bug. This replaces the scattered orchestration
        logic that was in main.py.

        Flow:
        1. Single update() call on Micro Regime Engine
        2. Check conviction (UVXY/VIX extremes only - no state fallback)
        3. Resolve vs Macro using shared resolver
        4. Apply direction conflict resolution for FADE strategies
        5. Return final decision

        Args:
            vix_current: Current VIX value (UVXY proxy for intraday).
            vix_open: VIX at market open.
            qqq_current: Current QQQ price.
            qqq_open: QQQ at market open.
            uvxy_pct: UVXY intraday change as decimal (0.08 = +8%).
            macro_regime_score: Macro regime score (0-100).
            current_time: Timestamp string.
            vix_level_override: CBOE VIX for consistent level classification.
            premarket_shock_pct: Overnight VIX shock memory as decimal (0.50 = +50%).

        Returns:
            Tuple of (should_trade, direction, state, reason):
            - should_trade: True if a trade should be executed
            - direction: OptionDirection.CALL or .PUT, or None
            - state: MicroRegimeState for logging/debugging
            - reason: Human-readable explanation of decision
        """
        return generate_micro_intraday_signal_impl(
            self,
            vix_current=vix_current,
            vix_open=vix_open,
            qqq_current=qqq_current,
            qqq_open=qqq_open,
            uvxy_pct=uvxy_pct,
            macro_regime_score=macro_regime_score,
            current_time=current_time,
            vix_level_override=vix_level_override,
            premarket_shock_pct=premarket_shock_pct,
        )

    def count_options_positions(self) -> Tuple[int, int, int]:
        """
        V5.3: Count current options positions.

        Returns:
            Tuple of (intraday_count, swing_count, total_count)
        """
        intraday_count = len([p for p in self.get_intraday_positions() if p is not None])
        swing_count = 0

        # Count spread positions
        if self._spread_positions:
            swing_count += len(self._spread_positions)
        elif self._spread_position is not None:
            swing_count += 1

        # Count canonical swing single-leg position only.
        if self._position is not None:
            swing_count += 1

        total_count = intraday_count + swing_count
        return intraday_count, swing_count, total_count

    def get_spread_positions(self) -> List[SpreadPosition]:
        """Get all active spread positions."""
        if self._spread_positions:
            return list(self._spread_positions)
        if self._spread_position is not None:
            return [self._spread_position]
        return []

    def get_open_spread_count(self) -> int:
        """Number of active spread positions."""
        return len(self.get_spread_positions())

    def get_open_spread_count_by_direction(self, direction_label: str) -> int:
        """Count active spreads in a directional bucket (BULLISH/BEARISH)."""
        label = str(direction_label or "").upper()
        count = 0
        for spread in self.get_spread_positions():
            if self._spread_direction_label(spread.spread_type) == label:
                count += 1
        return count

    def get_open_spread_count_for_expiry(
        self, expiry_bucket: str, direction_label: Optional[str] = None
    ) -> int:
        """Count active spreads in a given expiry bucket (optionally by direction)."""
        bucket = str(expiry_bucket or "").strip()
        if not bucket:
            return 0
        wanted_dir = str(direction_label or "").upper() if direction_label else None
        count = 0
        for spread in self.get_spread_positions():
            spread_bucket = str(getattr(spread.long_leg, "expiry", "") or "").strip()
            if not spread_bucket:
                spread_bucket = f"DTE:{int(getattr(spread.long_leg, 'days_to_expiry', -1))}"
            if spread_bucket != bucket:
                continue
            if wanted_dir:
                spread_dir = self._spread_direction_label(spread.spread_type)
                if spread_dir != wanted_dir:
                    continue
            count += 1
        return count

    def _check_expiry_concentration_cap(
        self,
        expiry_bucket: str,
        direction: Optional[OptionDirection],
        regime_score: Optional[float] = None,
        vix_current: Optional[float] = None,
    ) -> Optional[str]:
        """Return reject code when per-expiry spread concentration cap is exceeded."""
        if not bool(getattr(config, "SPREAD_EXPIRY_CONCENTRATION_CAP_ENABLED", False)):
            return None
        bucket = str(expiry_bucket or "").strip()
        if not bucket:
            return None

        total_cap = max(int(getattr(config, "SPREAD_MAX_PER_EXPIRY", 0)), 0)
        if total_cap > 0:
            total_count = self.get_open_spread_count_for_expiry(bucket)
            if total_count >= total_cap:
                self.log(
                    f"EXPIRY_CAP_BLOCK: Bucket={bucket} | Total={total_count} >= Cap={total_cap}"
                )
                return "R_EXPIRY_CONCENTRATION_CAP"

        if direction is None:
            return None

        dir_label = "BULLISH" if direction == OptionDirection.CALL else "BEARISH"
        if dir_label == "BULLISH":
            dir_cap = max(int(getattr(config, "SPREAD_MAX_BULLISH_PER_EXPIRY", 0)), 0)
            # Bull-profile override: allow one additional bullish ladder slot per expiry.
            bull_regime_min = float(getattr(config, "SPREAD_EXPIRY_BULL_PROFILE_REGIME_MIN", 70.0))
            bull_vix_max = float(getattr(config, "SPREAD_EXPIRY_BULL_PROFILE_VIX_MAX", 18.0))
            bull_cap = max(
                int(getattr(config, "SPREAD_MAX_BULLISH_PER_EXPIRY_BULL_PROFILE", dir_cap)),
                dir_cap,
            )
            if (
                regime_score is not None
                and float(regime_score) >= bull_regime_min
                and vix_current is not None
                and float(vix_current) <= bull_vix_max
            ):
                dir_cap = bull_cap
        else:
            dir_cap = max(int(getattr(config, "SPREAD_MAX_BEARISH_PER_EXPIRY", 0)), 0)
        if dir_cap <= 0:
            return None

        dir_count = self.get_open_spread_count_for_expiry(bucket, dir_label)
        if dir_count >= dir_cap:
            self.log(
                f"EXPIRY_CAP_BLOCK: Bucket={bucket} | Direction={dir_label} | "
                f"Count={dir_count} >= Cap={dir_cap}"
            )
            return "R_EXPIRY_CONCENTRATION_CAP_DIRECTION"

        return None

    def _spread_direction_label(self, spread_type: str) -> Optional[str]:
        """Map spread type to directional bucket for slot caps."""
        st = str(spread_type or "").upper()
        if st in {"BULL_CALL", "BULL_CALL_DEBIT", "BULL_PUT_CREDIT"}:
            return "BULLISH"
        if st in {"BEAR_PUT", "BEAR_PUT_DEBIT", "BEAR_CALL_CREDIT"}:
            return "BEARISH"
        return None

    def _resolve_put_assignment_gate_profile(
        self,
        *,
        overlay_state: str,
        vix_current: float,
        regime_score: float,
    ) -> Tuple[bool, float, str]:
        """Return (enforce_gate, min_otm_pct, profile) for short-PUT assignment guard."""
        hard_block_vix = float(getattr(config, "BEAR_PUT_ASSIGNMENT_HARD_BLOCK_VIX", 28.0))
        hard_block_regime_max = float(
            getattr(config, "BEAR_PUT_ASSIGNMENT_HARD_BLOCK_REGIME_MAX", 40.0)
        )
        enforce_assignment_gate = (
            overlay_state in {"STRESS", "EARLY_STRESS"}
            or vix_current >= hard_block_vix
            or regime_score <= hard_block_regime_max
        )
        min_otm_pct = float(getattr(config, "BEAR_PUT_ENTRY_MIN_OTM_PCT", 0.03))
        stress_otm_pct = float(getattr(config, "BEAR_PUT_ENTRY_MIN_OTM_PCT_STRESS", min_otm_pct))
        low_vix_threshold = float(getattr(config, "BEAR_PUT_ENTRY_LOW_VIX_THRESHOLD", 18.0))
        relaxed_otm_pct = float(getattr(config, "BEAR_PUT_ENTRY_MIN_OTM_PCT_RELAXED", 0.015))
        relaxed_regime_min = float(getattr(config, "BEAR_PUT_ENTRY_RELAXED_REGIME_MIN", 60.0))
        gate_profile = "BASE"
        if overlay_state in {"STRESS", "EARLY_STRESS"}:
            min_otm_pct = min(min_otm_pct, stress_otm_pct)
            gate_profile = "STRESS"
        if (
            vix_current <= low_vix_threshold
            and regime_score >= relaxed_regime_min
            and gate_profile == "BASE"
        ):
            min_otm_pct = min(min_otm_pct, relaxed_otm_pct)
            gate_profile = "LOW_VIX_RELAXED"
        return enforce_assignment_gate, float(min_otm_pct), gate_profile

    def _find_safer_bear_put_short_leg(
        self,
        *,
        contracts: Optional[List[OptionContract]],
        long_leg_contract: OptionContract,
        current_price: float,
        min_otm_pct: float,
    ) -> Optional[OptionContract]:
        """
        Pick a farther-OTM short PUT for BEAR_PUT debit when assignment gate blocks the initial leg.
        """
        if not contracts or current_price <= 0:
            return None
        dyn_widths = self._get_dynamic_spread_widths(current_price)
        effective_width_min = self._get_effective_spread_width_min(current_price=current_price)
        width_max = dyn_widths["width_max"]
        oi_min_short = max(0, int(getattr(config, "OPTIONS_MIN_OPEN_INTEREST_PUT", 25)) // 2)
        spread_warn_short = float(getattr(config, "OPTIONS_SPREAD_WARNING_PCT_PUT", 0.35))
        target_width = dyn_widths["width_target"]
        long_symbol = str(long_leg_contract.symbol)
        long_strike = float(long_leg_contract.strike)
        long_dte = int(long_leg_contract.days_to_expiry)

        candidates: List[Tuple[float, float, float, OptionContract]] = []
        for contract in contracts:
            if contract is None or contract.direction != OptionDirection.PUT:
                continue
            if str(contract.symbol) == long_symbol:
                continue
            if int(contract.days_to_expiry) != long_dte:
                continue
            strike = float(contract.strike)
            # BEAR_PUT short leg must remain below long strike (OTM).
            if strike >= long_strike:
                continue
            width = abs(long_strike - strike)
            if width < effective_width_min or width > width_max:
                continue
            if int(contract.open_interest) < oi_min_short:
                continue
            if float(contract.spread_pct) > spread_warn_short:
                continue
            otm_pct = (current_price - strike) / current_price
            if otm_pct < min_otm_pct:
                continue
            width_penalty = abs(width - target_width)
            delta_pref = abs(abs(float(getattr(contract, "delta", 0.30) or 0.30)) - 0.28)
            # Prefer closest width to target, then deeper OTM, then delta quality.
            candidates.append((width_penalty, -otm_pct, delta_pref, contract))

        if not candidates:
            return None
        candidates.sort(key=lambda row: (row[0], row[1], row[2]))
        return candidates[0][3]

    def _set_directional_spread_cooldown(
        self,
        *,
        cooldown_key: str,
        minutes: int,
        reason: str,
    ) -> None:
        """Apply targeted spread-entry cooldown on a key used by leg selectors."""
        if minutes <= 0 or self.algorithm is None:
            return
        key = str(cooldown_key or "").upper()
        if not key:
            return
        until_dt = self.algorithm.Time + timedelta(minutes=max(0, int(minutes)))
        until_text = until_dt.strftime("%Y-%m-%d %H:%M:%S")
        if not hasattr(self, "_spread_failure_cooldown_until_by_dir"):
            self._spread_failure_cooldown_until_by_dir = {}
        self._spread_failure_cooldown_until_by_dir[key] = until_text
        self.log(
            f"SPREAD_COOLDOWN_SET: {reason} | Key={key} | Minutes={int(minutes)} | Until={until_text}",
            trades_only=True,
        )

    def get_regime_overlay_state(
        self, vix_current: Optional[float], regime_score: Optional[float] = None
    ) -> str:
        """
        V6.22: Fast stress overlay used by resolver, slot caps, and exits.

        Returns one of: NORMAL, EARLY_STRESS, STRESS, RECOVERY.
        """
        try:
            vix = float(vix_current) if vix_current is not None else 0.0
        except (TypeError, ValueError):
            vix = 0.0

        stress_vix = float(getattr(config, "REGIME_OVERLAY_STRESS_VIX", 21.0))
        stress_vix_5d = float(getattr(config, "REGIME_OVERLAY_STRESS_VIX_5D", 0.18))
        early_low = float(getattr(config, "REGIME_OVERLAY_EARLY_VIX_LOW", 16.0))
        early_high = float(getattr(config, "REGIME_OVERLAY_EARLY_VIX_HIGH", 18.0))
        vix_5d_change = (
            self._iv_sensor.get_vix_5d_change() if self._iv_sensor.is_conviction_ready() else None
        )

        if vix >= stress_vix:
            return "STRESS"
        if vix >= early_high and vix_5d_change is not None and vix_5d_change >= stress_vix_5d:
            return "STRESS"
        if early_low <= vix < early_high:
            return "EARLY_STRESS"
        if vix < early_low and vix_5d_change is not None and vix_5d_change <= -0.05:
            return "RECOVERY"
        return "NORMAL"

    def _get_regime_transition_context(
        self, regime_score: Optional[float] = None
    ) -> Dict[str, Any]:
        """Fetch transition context from algorithm if available."""
        ctx: Dict[str, Any] = {}
        if isinstance(self._transition_context_snapshot, dict):
            try:
                ctx = dict(self._transition_context_snapshot)
            except Exception:
                ctx = {}
        if self.algorithm is not None and hasattr(self.algorithm, "_get_regime_transition_context"):
            if not ctx:
                try:
                    raw = self.algorithm._get_regime_transition_context()
                    if isinstance(raw, dict):
                        ctx = dict(raw)
                except Exception:
                    ctx = {}
        if "effective_score" not in ctx and regime_score is not None:
            try:
                ctx["effective_score"] = float(regime_score)
            except Exception:
                pass
        if "transition_score" not in ctx and "effective_score" in ctx:
            try:
                ctx["transition_score"] = float(ctx.get("effective_score"))
            except Exception:
                pass
        return ctx

    def set_transition_context_snapshot(self, transition_ctx: Optional[Dict[str, Any]]) -> None:
        """Cache per-cycle transition context so all engine decisions consume one snapshot."""
        if isinstance(transition_ctx, dict):
            self._transition_context_snapshot = dict(transition_ctx)
        else:
            self._transition_context_snapshot = None

    def clear_transition_context_snapshot(self) -> None:
        """Clear cached transition context snapshot."""
        self._transition_context_snapshot = None

    def evaluate_transition_policy_block(
        self,
        engine: str,
        direction: Optional[OptionDirection],
        transition_ctx: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, str]:
        """
        Unified transition-policy gate for MICRO/ITM/VASS.

        Returns:
            (gate_name, reason). Empty gate_name means no block.
        """
        if not bool(getattr(config, "REGIME_TRANSITION_GUARD_ENABLED", True)):
            return "", ""

        ctx = (
            transition_ctx
            if isinstance(transition_ctx, dict)
            else self._get_regime_transition_context()
        )
        engine_key = str(engine or "").upper()
        if engine_key not in {"MICRO", "ITM", "VASS", "HEDGE"}:
            return "", ""

        if engine_key == "MICRO":
            ambiguous_key = "MICRO_TRANSITION_BLOCK_AMBIGUOUS"
            call_det_key = "MICRO_TRANSITION_BLOCK_CALL_ON_DETERIORATION"
            put_rec_key = "MICRO_TRANSITION_BLOCK_PUT_ON_RECOVERY"
            ambiguous_gate = "MICRO_TRANSITION_AMBIGUOUS"
            call_det_gate = "MICRO_TRANSITION_BLOCK_CALL_ON_DETERIORATION"
            put_rec_gate = "MICRO_TRANSITION_BLOCK_PUT_ON_RECOVERY"
        elif engine_key == "ITM":
            ambiguous_key = "ITM_TRANSITION_BLOCK_AMBIGUOUS"
            call_det_key = "ITM_TRANSITION_BLOCK_BULL_ON_DETERIORATION"
            put_rec_key = "ITM_TRANSITION_BLOCK_BEAR_ON_RECOVERY"
            ambiguous_gate = "REGIME_TRANSITION_AMBIGUOUS"
            call_det_gate = "REGIME_DOWNSHIFT_NO_CALL"
            put_rec_gate = "REGIME_RECOVERY_NO_PUT"
        elif engine_key == "VASS":
            ambiguous_key = "VASS_TRANSITION_BLOCK_AMBIGUOUS"
            call_det_key = "VASS_TRANSITION_BLOCK_BULL_ON_DETERIORATION"
            put_rec_key = "VASS_TRANSITION_BLOCK_BEAR_ON_RECOVERY"
            ambiguous_gate = "VASS_TRANSITION_BLOCK_AMBIGUOUS"
            call_det_gate = "VASS_TRANSITION_BLOCK_BULL_ON_DETERIORATION"
            put_rec_gate = "VASS_TRANSITION_BLOCK_BEAR_ON_RECOVERY"
        else:  # HEDGE (protective hedges/throttle-only handoff policies)
            ambiguous_key = "ITM_TRANSITION_BLOCK_AMBIGUOUS"
            call_det_key = "ITM_TRANSITION_BLOCK_BULL_ON_DETERIORATION"
            put_rec_key = "ITM_TRANSITION_BLOCK_BEAR_ON_RECOVERY"
            ambiguous_gate = "HEDGE_TRANSITION_AMBIGUOUS"
            call_det_gate = "HEDGE_TRANSITION_BLOCK_BULL_ON_DETERIORATION"
            put_rec_gate = "HEDGE_TRANSITION_BLOCK_BEAR_ON_RECOVERY"

        if bool(getattr(config, ambiguous_key, True)) and bool(ctx.get("ambiguous", False)):
            return ambiguous_gate, "ambiguous transition state"

        overlay = str(ctx.get("transition_overlay", "") or "").upper()
        in_deterioration = (
            bool(ctx.get("strong_deterioration", False)) or overlay == "DETERIORATION"
        )
        in_recovery = bool(ctx.get("strong_recovery", False)) or overlay == "RECOVERY"

        if (
            direction == OptionDirection.CALL
            and bool(getattr(config, call_det_key, True))
            and in_deterioration
        ):
            return call_det_gate, "bullish blocked during deterioration"

        if (
            direction == OptionDirection.PUT
            and bool(getattr(config, put_rec_key, True))
            and in_recovery
        ):
            return put_rec_gate, "bearish blocked during recovery"

        if bool(getattr(config, "TRANSITION_HANDOFF_THROTTLE_ENABLED", True)):
            if engine_key == "ITM":
                handoff_enabled = bool(
                    getattr(config, "ITM_TRANSITION_HANDOFF_THROTTLE_ENABLED", True)
                )
            elif engine_key == "MICRO":
                handoff_enabled = bool(
                    getattr(config, "MICRO_TRANSITION_HANDOFF_THROTTLE_ENABLED", True)
                )
            elif engine_key == "VASS":
                handoff_enabled = bool(
                    getattr(config, "VASS_TRANSITION_HANDOFF_THROTTLE_ENABLED", True)
                )
            elif engine_key == "HEDGE":
                handoff_enabled = bool(
                    getattr(config, "HEDGE_TRANSITION_HANDOFF_THROTTLE_ENABLED", True)
                )
            else:
                handoff_enabled = False
            if handoff_enabled and direction in (OptionDirection.CALL, OptionDirection.PUT):
                overlay = str(ctx.get("transition_overlay", "")).upper()
                bars_since_flip = int(ctx.get("overlay_bars_since_flip", 999) or 999)
                handoff_bars = max(
                    1,
                    int(
                        getattr(
                            config,
                            "VASS_TRANSITION_HANDOFF_BARS"
                            if engine_key == "VASS"
                            else "TRANSITION_HANDOFF_BARS",
                            getattr(config, "TRANSITION_HANDOFF_BARS", 2),
                        )
                    ),
                )
                delta = float(ctx.get("delta", 0.0) or 0.0)
                momentum = float(ctx.get("momentum_roc", 0.0) or 0.0)
                hard_downside = delta <= float(
                    getattr(config, "TRANSITION_HANDOFF_HARD_DOWNSIDE_DELTA_MAX", -2.5)
                ) and momentum <= float(
                    getattr(config, "TRANSITION_HANDOFF_HARD_DOWNSIDE_MOM_MAX", -0.02)
                )
                hard_upside = delta >= float(
                    getattr(config, "TRANSITION_HANDOFF_HARD_UPSIDE_DELTA_MIN", 2.5)
                ) and momentum >= float(
                    getattr(config, "TRANSITION_HANDOFF_HARD_UPSIDE_MOM_MIN", 0.02)
                )
                if (
                    direction == OptionDirection.PUT
                    and overlay == "RECOVERY"
                    and bars_since_flip < handoff_bars
                    and not hard_downside
                ):
                    return (
                        "TRANSITION_HANDOFF_PUT_THROTTLE",
                        f"PUT throttled {bars_since_flip}/{handoff_bars} bars into RECOVERY",
                    )
                if (
                    direction == OptionDirection.CALL
                    and overlay == "DETERIORATION"
                    and bars_since_flip < handoff_bars
                    and not hard_upside
                ):
                    return (
                        "TRANSITION_HANDOFF_CALL_THROTTLE",
                        f"CALL throttled {bars_since_flip}/{handoff_bars} bars into DETERIORATION",
                    )

        return "", ""

    def _record_regime_decision(
        self,
        engine: str,
        decision: str,
        strategy_attempted: str,
        gate_name: str,
        threshold_snapshot: Optional[Any] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Forward structured regime decision telemetry to algorithm artifact writer."""
        if self.algorithm is None or not hasattr(self.algorithm, "_record_regime_decision_event"):
            return
        try:
            self.algorithm._record_regime_decision_event(
                engine=engine,
                engine_decision=decision,
                strategy_attempted=strategy_attempted,
                gate_name=gate_name,
                threshold_snapshot=threshold_snapshot,
                context=context,
            )
        except Exception:
            pass

    def _get_effective_total_cap(self) -> int:
        """V12.15: Compute effective total position cap.

        Default: returns OPTIONS_MAX_TOTAL_POSITIONS (7).
        When OPTIONS_REGIME_ADAPTIVE_TOTAL_CAP_ENABLED is True, adapts using
        regime score (REGIME_NEUTRAL, REGIME_DEFENSIVE thresholds), transition
        overlay (DETERIORATION/AMBIGUOUS), and fast stress overlay (STRESS/EARLY_STRESS).
        """
        base = int(getattr(config, "OPTIONS_MAX_TOTAL_POSITIONS", 7))
        if not bool(getattr(config, "OPTIONS_REGIME_ADAPTIVE_TOTAL_CAP_ENABLED", False)):
            return base

        # Fast stress overlay (VIX-based: STRESS, EARLY_STRESS, RECOVERY, NORMAL)
        smoothed_vix = self._iv_sensor.get_smoothed_vix()
        fast_overlay = self.get_regime_overlay_state(vix_current=smoothed_vix)
        if fast_overlay == "STRESS":
            return int(getattr(config, "OPTIONS_TOTAL_CAP_DETERIORATION", 3))

        # Transition overlay (regime engine: DETERIORATION, AMBIGUOUS, STABLE, RECOVERY)
        ctx = self._get_regime_transition_context()
        score = float(ctx.get("effective_score", ctx.get("transition_score", 50)) or 50)
        overlay = str(ctx.get("transition_overlay", "") or "").upper()

        if overlay in ("DETERIORATION", "AMBIGUOUS"):
            return int(getattr(config, "OPTIONS_TOTAL_CAP_DETERIORATION", 3))

        # EARLY_STRESS: cap at neutral level (between full and deterioration)
        if fast_overlay == "EARLY_STRESS":
            return int(getattr(config, "OPTIONS_TOTAL_CAP_NEUTRAL", 5))

        neutral = float(getattr(config, "REGIME_NEUTRAL", 50))
        defensive = float(getattr(config, "REGIME_DEFENSIVE", 35))

        if score >= neutral:
            return int(getattr(config, "OPTIONS_TOTAL_CAP_BULLISH", base))
        if score >= defensive:
            return int(getattr(config, "OPTIONS_TOTAL_CAP_NEUTRAL", 5))
        return int(getattr(config, "OPTIONS_TOTAL_CAP_BEARISH", 4))

    def can_enter_single_leg(self) -> Tuple[bool, str]:
        """V12.15: Portfolio-level slot gate for single-leg entries.

        Lane-level caps (ITM_MAX_CONCURRENT_POSITIONS, MICRO_MAX_CONCURRENT_POSITIONS)
        are enforced in preflight_intraday_entry() / validate_lane_caps().
        This method checks the portfolio total cap (regime-adaptive when enabled).
        """
        _, _, total_count = self.count_options_positions()
        effective_cap = self._get_effective_total_cap()

        if total_count >= effective_cap:
            return (
                False,
                f"R_SLOT_TOTAL_MAX: {total_count} >= {effective_cap}",
            )

        return True, "R_OK"

    def preflight_intraday_entry(
        self,
        strategy: Optional["IntradayStrategy"],
        direction: Optional[OptionDirection] = None,
        state: Optional[Any] = None,
        vix_current: Optional[float] = None,
        transition_ctx: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Fast preflight for single-leg intraday submission.

        This is intentionally lightweight and lane-aware so callers can avoid
        generating candidates that are guaranteed to be dropped.
        """
        strategy_name = (
            self._canonical_intraday_strategy(strategy).value if strategy is not None else ""
        )
        lane = self._intraday_engine_lane_from_strategy(strategy_name)

        if self._pending_intraday_entry or self._pending_intraday_entries:
            self._clear_stale_pending_intraday_entry_if_orphaned()
        if self.has_pending_intraday_entry(engine=lane):
            return False, "E_INTRADAY_PENDING_ENTRY", lane

        lane_ok, lane_code, lane_detail, _ = self._micro_entry_engine.validate_lane_caps(
            entry_strategy=self._canonical_intraday_strategy(strategy_name),
            intraday_positions=self._intraday_positions,
            has_pending_intraday_entry=self.has_pending_intraday_entry,
            intraday_itm_trades_today=self._intraday_itm_trades_today,
            intraday_micro_trades_today=self._intraday_micro_trades_today,
            lane_resolver=self._intraday_engine_lane_from_strategy,
            state=state,
            direction=direction,
            vix_current=vix_current,
            transition_ctx=transition_ctx,
        )
        if not lane_ok:
            return False, lane_code or "E_INTRADAY_LANE_CAP", lane_detail

        can_single_leg, reason = self.can_enter_single_leg()
        if not can_single_leg:
            code = str(reason or "R_SLOT_LIMIT").split(":", 1)[0].strip() or "R_SLOT_LIMIT"
            return False, code, reason

        if not self._can_trade_options(OptionsMode.INTRADAY, direction=direction):
            tl_reason, tl_detail = self.pop_last_trade_limit_failure()
            return False, tl_reason or "E_INTRADAY_TRADE_LIMIT", tl_detail

        return True, "R_OK", None

    def can_enter_intraday(self) -> Tuple[bool, str]:
        """
        Backward-compatible alias for legacy call-sites.

        Use `can_enter_single_leg()` in new code.
        """
        return self.can_enter_single_leg()

    def can_enter_swing(
        self,
        direction: Optional[OptionDirection] = None,
        overlay_state: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Check if swing entry is allowed."""
        return self._vass_entry_engine.can_enter_swing(
            host=self,
            direction=direction,
            overlay_state=overlay_state,
        )

    def get_macro_direction(self, macro_regime_score: float) -> str:
        """
        V5.3: Determine Macro direction from regime score.

        Args:
            macro_regime_score: Regime score (0-100)

        Returns:
            "BULLISH", "BEARISH", or "NEUTRAL"
        """
        # Prefer regime state-machine output when available.
        transition_ctx = self._get_regime_transition_context(macro_regime_score)
        base_regime = str(transition_ctx.get("base_regime", "") or "").upper()
        transition_overlay = str(transition_ctx.get("transition_overlay", "") or "").upper()
        if transition_overlay == "AMBIGUOUS":
            return "NEUTRAL"
        if base_regime in {"BULLISH", "BEARISH"}:
            return base_regime

        bullish_min = float(getattr(config, "MACRO_DIRECTION_BULLISH_MIN", 55.0))
        bearish_max = float(getattr(config, "MACRO_DIRECTION_BEARISH_MAX", 45.0))
        if macro_regime_score > bullish_min:
            return "BULLISH"
        elif macro_regime_score < bearish_max:
            return "BEARISH"
        else:
            return "NEUTRAL"

    def should_log_vass_rejection(self, reason_key: str) -> bool:
        """Per-reason throttle for VASS skip/rejection logs."""
        now = self.algorithm.Time if self.algorithm is not None else datetime.utcnow()
        return self._vass_entry_engine.should_log_rejection(now=now, reason_key=reason_key)

    def resolve_vass_direction_context(
        self,
        *,
        regime_score: float,
        size_multiplier: float,
        bull_profile_log_prefix: str,
        clamp_log_prefix: str,
        shock_log_prefix: str,
        transition_ctx: Optional[Dict[str, Any]] = None,
    ) -> Optional[Tuple[OptionDirection, str, Any, float, bool, str, str, str, str]]:
        """Resolve VASS direction + sizing context with shared guard rails."""
        return self._vass_entry_engine.resolve_direction_context(
            host=self,
            regime_score=regime_score,
            size_multiplier=size_multiplier,
            bull_profile_log_prefix=bull_profile_log_prefix,
            clamp_log_prefix=clamp_log_prefix,
            shock_log_prefix=shock_log_prefix,
            transition_ctx=transition_ctx,
        )

    def scan_spread_for_direction(
        self,
        *,
        chain: Any,
        direction: OptionDirection,
        direction_str: str,
        regime_score: float,
        qqq_price: float,
        adx_value: float,
        ma200_value: float,
        ma50_value: float,
        iv_rank: float,
        size_multiplier: float,
        is_eod_scan: bool,
    ) -> None:
        """Scan for VASS spread entry in a specific direction."""
        self._vass_entry_engine.scan_spread_for_direction(
            host=self,
            chain=chain,
            direction=direction,
            direction_str=direction_str,
            regime_score=regime_score,
            qqq_price=qqq_price,
            adx_value=adx_value,
            ma200_value=ma200_value,
            ma50_value=ma50_value,
            iv_rank=iv_rank,
            size_multiplier=size_multiplier,
            is_eod_scan=is_eod_scan,
        )

    def build_vass_spread_signal(
        self,
        *,
        chain: Any,
        candidate_contracts: List[OptionContract],
        direction: OptionDirection,
        regime_score: float,
        qqq_price: float,
        adx_value: float,
        ma200_value: float,
        ma50_value: float,
        iv_rank: float,
        size_multiplier: float,
        portfolio_value: float,
        margin_remaining: float,
        strategy: SpreadStrategy,
        vass_dte_min: int,
        vass_dte_max: int,
        dte_ranges: List[Tuple[int, int]],
        is_credit: bool,
        is_eod_scan: bool,
        fallback_log_prefix: str,
    ) -> Tuple[Optional[TargetWeight], str]:
        """Build VASS spread entry signal via VASS entry engine."""
        return self._vass_entry_engine.build_spread_signal(
            host=self,
            chain=chain,
            candidate_contracts=candidate_contracts,
            direction=direction,
            regime_score=regime_score,
            qqq_price=qqq_price,
            adx_value=adx_value,
            ma200_value=ma200_value,
            ma50_value=ma50_value,
            iv_rank=iv_rank,
            size_multiplier=size_multiplier,
            portfolio_value=portfolio_value,
            margin_remaining=margin_remaining,
            strategy=strategy,
            vass_dte_min=vass_dte_min,
            vass_dte_max=vass_dte_max,
            dte_ranges=dte_ranges,
            is_credit=is_credit,
            is_eod_scan=is_eod_scan,
            fallback_log_prefix=fallback_log_prefix,
        )

    def run_vass_entry_cycle(
        self,
        *,
        chain: Any,
        regime_score: float,
        qqq_price: float,
        adx_value: float,
        ma200_value: float,
        ma50_value: float,
        iv_rank: float,
        size_multiplier: float,
        is_eod_scan: bool,
    ) -> None:
        """Resolve VASS direction context and dispatch directional spread scans."""
        self._vass_entry_engine.run_eod_entry_cycle(
            host=self,
            chain=chain,
            regime_score=regime_score,
            qqq_price=qqq_price,
            adx_value=adx_value,
            ma200_value=ma200_value,
            ma50_value=ma50_value,
            iv_rank=iv_rank,
            size_multiplier=size_multiplier,
            is_eod_scan=is_eod_scan,
        )

    def run_vass_intraday_entry_cycle(
        self,
        *,
        chain: Any,
        qqq_price: float,
        adx_value: float,
        ma200_value: float,
        ma50_value: float,
        size_multiplier: float,
        effective_portfolio_value: float,
        margin_remaining: float,
        transition_ctx: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Run intraday VASS spread lane via VASS entry engine."""
        self._vass_entry_engine.run_intraday_entry_cycle(
            host=self,
            chain=chain,
            qqq_price=qqq_price,
            adx_value=adx_value,
            ma200_value=ma200_value,
            ma50_value=ma50_value,
            size_multiplier=size_multiplier,
            effective_portfolio_value=effective_portfolio_value,
            margin_remaining=margin_remaining,
            transition_ctx=transition_ctx,
        )

    def run_itm_intraday_explicit_cycle(
        self,
        *,
        chain: Any,
        qqq_price: float,
        regime_score: float,
        size_multiplier: float,
        effective_portfolio_value: float,
        vix_intraday: float,
        vix_level_cboe: Optional[float],
        transition_ctx: Optional[Dict[str, Any]],
        itm_dir: Optional[OptionDirection],
        itm_reason: str,
        intraday_scan_context_ready: bool,
        itm_intraday_cooldown_active: bool,
    ) -> None:
        """Run explicit ITM intraday lane via ITM horizon engine."""
        self._itm_horizon_engine.run_intraday_explicit_cycle(
            host=self,
            chain=chain,
            qqq_price=qqq_price,
            regime_score=regime_score,
            size_multiplier=size_multiplier,
            effective_portfolio_value=effective_portfolio_value,
            vix_intraday=vix_intraday,
            vix_level_cboe=vix_level_cboe,
            transition_ctx=transition_ctx,
            itm_dir=itm_dir,
            itm_reason=itm_reason,
            intraday_scan_context_ready=intraday_scan_context_ready,
            itm_intraday_cooldown_active=itm_intraday_cooldown_active,
        )

    def run_micro_intraday_cycle(
        self,
        *,
        chain: Any,
        qqq_price: float,
        regime_score: float,
        size_multiplier: float,
        effective_portfolio_value: float,
        vix_intraday: float,
        vix_level_cboe: Optional[float],
        transition_ctx: Optional[Dict[str, Any]],
        uvxy_pct: float,
        micro_intraday_cooldown_active: bool,
    ) -> Tuple[Optional[OptionDirection], str]:
        """Run MICRO intraday lane via micro entry engine."""
        return self._micro_entry_engine.run_intraday_cycle(
            host=self,
            chain=chain,
            qqq_price=qqq_price,
            regime_score=regime_score,
            size_multiplier=size_multiplier,
            effective_portfolio_value=effective_portfolio_value,
            vix_intraday=vix_intraday,
            vix_level_cboe=vix_level_cboe,
            transition_ctx=transition_ctx,
            uvxy_pct=uvxy_pct,
            micro_intraday_cooldown_active=micro_intraday_cooldown_active,
        )

    def _can_attempt_spread_entry(self, attempt_key: str) -> bool:
        """
        Limit spread entry attempts per day by strategy/direction key.

        Replaces the old single global `_entry_attempted_today` lock that
        blocked all subsequent spread opportunities after one failed attempt.
        """
        max_attempts = int(getattr(config, "SPREAD_MAX_ATTEMPTS_PER_KEY_PER_DAY", 3))
        used = int(self._spread_attempts_today_by_key.get(attempt_key, 0))
        if used >= max_attempts:
            self.log(
                f"SPREAD_ATTEMPT_LIMIT: {attempt_key} blocked | "
                f"{used}/{max_attempts} attempts used"
            )
            return False
        return True

    def _build_vass_signature(
        self,
        spread_type: str,
        direction: Optional[OptionDirection],
        long_leg_contract: OptionContract,
    ) -> str:
        """Build same-trade signature key for VASS anti-cluster guard."""
        return self._vass_entry_engine.build_signature(
            spread_type=spread_type,
            direction=direction,
            long_leg_contract=long_leg_contract,
        )

    def _parse_dt(self, date_text: str, hour: int, minute: int) -> Optional[datetime]:
        """Parse current scan timestamp from inputs; fallback to algorithm time."""
        return self._vass_entry_engine.parse_scan_dt(
            date_text=date_text,
            hour=hour,
            minute=minute,
            algorithm=self.algorithm,
        )

    def _check_vass_similar_entry_guard(
        self,
        signature: str,
        now_dt: Optional[datetime],
    ) -> Optional[str]:
        """VASS anti-cluster guard delegated to VASSEntryEngine."""
        if not self._vass_entry_engine_enabled:
            return None
        return self._vass_entry_engine.check_similar_entry_guard(
            signature=signature,
            now_dt=now_dt,
        )

    def _record_vass_signature_entry(self, signature: str, entry_dt: Optional[datetime]) -> None:
        """Record VASS signature entry via VASSEntryEngine."""
        if not self._vass_entry_engine_enabled:
            return
        self._vass_entry_engine.record_signature_entry(signature=signature, entry_dt=entry_dt)

    def _check_vass_direction_day_gap(
        self, direction: Optional[OptionDirection], current_date: str
    ) -> Optional[str]:
        """VASS per-direction day-gap guard delegated to VASSEntryEngine."""
        if not self._vass_entry_engine_enabled:
            return None
        return self._vass_entry_engine.check_direction_day_gap(
            direction=direction,
            current_date=current_date,
            algorithm=self.algorithm,
        )

    def _record_vass_direction_day_entry(
        self, direction: Optional[OptionDirection], entry_dt: Optional[datetime]
    ) -> None:
        """Record VASS per-direction entry date via VASSEntryEngine."""
        if not self._vass_entry_engine_enabled:
            return
        self._vass_entry_engine.record_direction_day_entry(direction=direction, entry_dt=entry_dt)

    def _build_spread_key(self, spread: SpreadPosition) -> str:
        """Stable spread key for per-position state."""
        return (
            f"{self._symbol_str(spread.long_leg.symbol)}|"
            f"{self._symbol_str(spread.short_leg.symbol)}|"
            f"{str(getattr(spread, 'entry_time', '') or '')}"
        )

    def _get_engine_now_dt(self) -> Optional[datetime]:
        """Best-effort engine timestamp for staged timers."""
        if self.algorithm is None:
            return None
        try:
            return self.algorithm.Time
        except Exception:
            return None

    def _check_neutrality_staged_exit(
        self,
        spread: SpreadPosition,
        regime_score: float,
        pnl_pct: float,
    ) -> Optional[str]:
        """
        Phase C: staged neutrality de-risk.

        Stage 1: warn and hold.
        Stage 2: exit if neutrality persists for confirm window or loss breaches damage threshold.
        """
        if not getattr(config, "SPREAD_NEUTRALITY_EXIT_ENABLED", True):
            return None

        key = self._build_spread_key(spread)
        zone_low = float(getattr(config, "SPREAD_NEUTRALITY_ZONE_LOW", 45))
        zone_high = float(getattr(config, "SPREAD_NEUTRALITY_ZONE_HIGH", 65))
        band = float(getattr(config, "SPREAD_NEUTRALITY_EXIT_PNL_BAND", 0.06))

        in_neutral_zone = zone_low <= regime_score <= zone_high
        in_flat_band = -band <= pnl_pct <= band
        if not (in_neutral_zone and in_flat_band):
            if key in self._spread_neutrality_warn_by_key:
                self._spread_neutrality_warn_by_key.pop(key, None)
                self.log(
                    f"NEUTRALITY_WARN_CLEARED: Spread={spread.spread_type} | "
                    f"Score={regime_score:.0f} | PnL={pnl_pct:+.1%}",
                    trades_only=True,
                )
            return None

        if not getattr(config, "SPREAD_NEUTRALITY_STAGED_ENABLED", True):
            return (
                f"NEUTRALITY_EXIT: Score {regime_score:.0f} in dead zone "
                f"({zone_low:.0f}-{zone_high:.0f}) with flat P&L ({pnl_pct:+.1%})"
            )

        now_dt = self._get_engine_now_dt()
        if now_dt is None:
            return (
                f"NEUTRALITY_EXIT: Score {regime_score:.0f} in dead zone "
                f"({zone_low:.0f}-{zone_high:.0f}) with flat P&L ({pnl_pct:+.1%})"
            )

        confirm_hours = float(getattr(config, "SPREAD_NEUTRALITY_CONFIRM_HOURS", 2))
        damage_pct = float(getattr(config, "SPREAD_NEUTRALITY_STAGE1_DAMAGE_PCT", 0.15))

        first_seen = self._spread_neutrality_warn_by_key.get(key)
        if first_seen is None:
            self._spread_neutrality_warn_by_key[key] = now_dt
            self.log(
                f"NEUTRALITY_WARN: Spread={spread.spread_type} | "
                f"Score={regime_score:.0f} | PnL={pnl_pct:+.1%} | Confirm={confirm_hours:.1f}h",
                trades_only=True,
            )
            return None

        elapsed_hours = max(0.0, (now_dt - first_seen).total_seconds() / 3600.0)
        if pnl_pct <= -damage_pct:
            return (
                f"NEUTRALITY_CONFIRMED_EXIT: Damage guard | "
                f"PnL={pnl_pct:+.1%} <= -{damage_pct:.0%} | "
                f"Elapsed={elapsed_hours:.1f}h"
            )
        if elapsed_hours >= confirm_hours:
            return (
                f"NEUTRALITY_CONFIRMED_EXIT: Confirmed in dead zone "
                f"({zone_low:.0f}-{zone_high:.0f}) | "
                f"Elapsed={elapsed_hours:.1f}h | PnL={pnl_pct:+.1%}"
            )
        return None

    def _record_spread_entry_attempt(self, attempt_key: str) -> None:
        """Record spread attempt with optional same-minute dedupe."""
        if bool(getattr(config, "SPREAD_ATTEMPT_DEDUPE_PER_MINUTE", False)) and self.algorithm:
            try:
                minute_key = self.algorithm.Time.strftime("%Y-%m-%d %H:%M")
                if self._spread_attempt_last_mark_by_key.get(attempt_key) == minute_key:
                    return
                self._spread_attempt_last_mark_by_key[attempt_key] = minute_key
            except Exception:
                pass
        self._spread_attempts_today_by_key[attempt_key] = (
            int(self._spread_attempts_today_by_key.get(attempt_key, 0)) + 1
        )

    def _set_spread_failure_cooldown(
        self, current_time: Optional[str], direction: Optional[str] = None
    ) -> None:
        """
        V2.4.3: Set cooldown after spread construction fails.

        Prevents retry storms when no valid contracts exist.
        Uses minute-level cooldown when configured.

        Args:
            current_time: Current timestamp in "YYYY-MM-DD HH:MM:SS" format.
            direction: Optional direction label to scope cooldown (CALL/PUT).
        """
        if not current_time:
            return

        try:
            from datetime import datetime, timedelta

            now_dt = datetime.strptime(current_time[:19], "%Y-%m-%d %H:%M:%S")
            cooldown_minutes = int(
                getattr(
                    config,
                    "SPREAD_FAILURE_COOLDOWN_MINUTES",
                    int(getattr(config, "SPREAD_FAILURE_COOLDOWN_HOURS", 1) * 60),
                )
            )
            cooldown_until_dt = now_dt + timedelta(minutes=max(cooldown_minutes, 0))
            cooldown_until = cooldown_until_dt.strftime("%Y-%m-%d %H:%M:%S")

            # V6.12: Direction-scoped cooldown (CALL failure doesn't block PUT)
            if direction:
                if not hasattr(self, "_spread_failure_cooldown_until_by_dir"):
                    self._spread_failure_cooldown_until_by_dir = {}
                dir_key = direction.value if hasattr(direction, "value") else str(direction)
                self._spread_failure_cooldown_until_by_dir[str(dir_key).upper()] = cooldown_until
            else:
                self._spread_failure_cooldown_until = cooldown_until
            self.log(
                f"SPREAD: Construction failed - entering {cooldown_minutes}m cooldown until {cooldown_until}"
            )
        except (ValueError, IndexError) as e:
            self.log(f"SPREAD: Failed to set cooldown: {e}")

    # =========================================================================
    # ENTRY SCORE CALCULATION
    # =========================================================================

    def calculate_entry_score(
        self,
        adx_value: float,
        current_price: float,
        ma200_value: float,
        iv_rank: float,
        bid_ask_spread_pct: float,
        open_interest: int,
        min_open_interest: Optional[int] = None,
        spread_max_pct: Optional[float] = None,
        spread_warn_pct: Optional[float] = None,
        iv_profile: str = "GENERIC",
    ) -> EntryScore:
        """
        Calculate 4-factor entry score.

        Args:
            adx_value: Current ADX(14) value.
            current_price: Current underlying price.
            ma200_value: 200-day moving average value.
            iv_rank: IV percentile (0-100).
            bid_ask_spread_pct: Bid-ask spread as percentage.
            open_interest: Open interest for the contract.

        Returns:
            EntryScore with all factor scores.
        """
        score = EntryScore()

        # Factor 1: ADX (trend strength)
        score.score_adx = self._score_adx(adx_value)

        # Factor 2: Momentum (price vs MA200)
        score.score_momentum = self._score_momentum(current_price, ma200_value)

        # Factor 3: IV Rank
        score.score_iv = self._score_iv_rank(iv_rank, iv_profile=iv_profile)

        # Factor 4: Liquidity (allow per-direction overrides)
        score.score_liquidity = self._score_liquidity(
            bid_ask_spread_pct,
            open_interest,
            min_open_interest=min_open_interest,
            spread_max_pct=spread_max_pct,
            spread_warn_pct=spread_warn_pct,
        )

        return score

    def _score_adx(self, adx_value: float) -> float:
        """
        Score ADX factor (0-1).

        ADX < 20: 0.25 (weak trend)
        ADX 20-25: 0.50 (moderate)
        ADX 25-35: 0.75 (strong)
        ADX >= 35: 1.00 (very strong)
        """
        if adx_value < config.OPTIONS_ADX_WEAK:
            return 0.25
        elif adx_value < config.OPTIONS_ADX_MODERATE:
            return 0.50
        elif adx_value < config.OPTIONS_ADX_STRONG:
            return 0.75
        else:
            return 1.00

    def _score_momentum(self, current_price: float, ma200_value: float) -> float:
        """
        Score momentum factor (0-1).

        Price significantly above MA200: 1.0
        Price above MA200: 0.75
        Price near MA200: 0.50
        Price below MA200: 0.25
        """
        if ma200_value <= 0:
            return 0.25

        ratio = current_price / ma200_value

        if ratio >= 1.05:  # 5%+ above MA200
            return 1.00
        elif ratio >= 1.00:  # Above MA200
            return 0.75
        elif ratio >= 0.98:  # Near MA200 (within 2%)
            return 0.50
        else:  # Below MA200
            return 0.25

    def _score_iv_rank(self, iv_rank: float, iv_profile: str = "GENERIC") -> float:
        """
        Score IV Rank factor (0-1).

        IV rank 20-80: Optimal range, full score
        IV rank < 20 or > 80: Suboptimal, reduced score
        """
        profile = str(iv_profile or "GENERIC").upper()
        if bool(getattr(config, "OPTIONS_IV_SCORE_PROFILE_ENABLED", False)):
            if profile == "DEBIT":
                low = float(getattr(config, "OPTIONS_IV_SCORE_DEBIT_LOW", 35.0))
                high = float(getattr(config, "OPTIONS_IV_SCORE_DEBIT_HIGH", 55.0))
                if iv_rank <= low:
                    return 1.0
                if iv_rank >= high:
                    return 0.25
                span = max(1e-6, high - low)
                return max(0.25, 1.0 - 0.75 * ((iv_rank - low) / span))
            if profile == "CREDIT":
                low = float(getattr(config, "OPTIONS_IV_SCORE_CREDIT_LOW", 45.0))
                high = float(getattr(config, "OPTIONS_IV_SCORE_CREDIT_HIGH", 65.0))
                if iv_rank <= low:
                    return 0.25
                if iv_rank >= high:
                    return 1.0
                span = max(1e-6, high - low)
                return min(1.0, 0.25 + 0.75 * ((iv_rank - low) / span))

        if config.OPTIONS_IV_RANK_LOW <= iv_rank <= config.OPTIONS_IV_RANK_HIGH:
            # Optimal range: scale from 0.75 to 1.0 based on position in range
            # Closer to middle (50) is better
            distance_from_50 = abs(iv_rank - 50)
            # 0 distance = 1.0, 30 distance = 0.75
            return 1.0 - (distance_from_50 / 120)  # Max distance is 30
        elif iv_rank < config.OPTIONS_IV_RANK_LOW:
            return 0.25  # Too low IV
        else:
            return 0.25  # Too high IV

    def _score_liquidity(
        self,
        spread_pct: float,
        open_interest: int,
        min_open_interest: Optional[int] = None,
        spread_max_pct: Optional[float] = None,
        spread_warn_pct: Optional[float] = None,
    ) -> float:
        """
        Score liquidity factor (0-1).

        Based on bid-ask spread and open interest.
        """
        # Start with spread score
        max_pct = spread_max_pct if spread_max_pct is not None else config.OPTIONS_SPREAD_MAX_PCT
        warn_pct = (
            spread_warn_pct if spread_warn_pct is not None else config.OPTIONS_SPREAD_WARNING_PCT
        )
        min_oi = (
            min_open_interest if min_open_interest is not None else config.OPTIONS_MIN_OPEN_INTEREST
        )

        if spread_pct <= max_pct:
            spread_score = 1.0
        elif spread_pct <= warn_pct:
            spread_score = 0.50
        else:
            spread_score = 0.0  # Too wide

        # OI score
        if open_interest >= min_oi:
            oi_score = 1.0
        elif open_interest >= min_oi // 2:
            oi_score = 0.50
        else:
            oi_score = 0.0  # Too thin

        # Combined liquidity score (average)
        return (spread_score + oi_score) / 2

    # =========================================================================
    # STOP TIER MAPPING
    # =========================================================================

    def get_stop_tier(self, entry_score: float) -> Dict[str, float]:
        """
        Get stop tier parameters based on entry score.

        Higher entry score → wider stops, fewer contracts.

        Args:
            entry_score: Total entry score (3.0-4.0).

        Returns:
            Dict with "stop_pct" and "contracts" values.
        """
        # Find the appropriate tier
        tiers = sorted(config.OPTIONS_STOP_TIERS.keys())

        for i, threshold in enumerate(tiers):
            if entry_score < threshold:
                if i == 0:
                    return config.OPTIONS_STOP_TIERS[tiers[0]]
                return config.OPTIONS_STOP_TIERS[tiers[i - 1]]

        # At or above highest tier
        return config.OPTIONS_STOP_TIERS[tiers[-1]]

    def calculate_position_size(
        self,
        entry_score: float,
        premium: float,
        portfolio_value: float,
        days_to_expiry: int = None,
    ) -> tuple:
        """
        Calculate position size based on entry score and 1% risk.

        V2.3.8: Uses tighter stops for 0DTE options to limit slippage damage.
        0DTE options move extremely fast - StopMarketOrder can fill at much
        worse prices due to slippage. Using 15% stops limits max loss to ~30%.

        Args:
            entry_score: Total entry score (3.0-4.0).
            premium: Option premium per contract.
            portfolio_value: Total portfolio value.
            days_to_expiry: Days to expiration (0 for 0DTE).

        Returns:
            Tuple of (num_contracts, stop_pct, stop_price, target_price).
        """
        # Get tier parameters
        tier = self.get_stop_tier(entry_score)
        stop_pct = tier["stop_pct"]
        base_contracts = tier["contracts"]

        # V2.3.8: Optional fixed 0DTE stop override.
        # V12.0 keeps this disabled by default so ATR-based stop logic remains sovereign.
        if (
            days_to_expiry is not None
            and days_to_expiry <= 1
            and bool(getattr(config, "OPTIONS_0DTE_STATIC_STOP_OVERRIDE_ENABLED", False))
        ):
            stop_pct = config.OPTIONS_0DTE_STOP_PCT
            self.log(f"POSITION_SIZE: Using 0DTE tight stop {stop_pct:.0%} (DTE={days_to_expiry})")

        # Calculate risk-adjusted contracts
        # Risk = contracts × premium × stop_pct
        # Target risk = portfolio_value × 1%
        target_risk = portfolio_value * config.OPTIONS_RISK_PER_TRADE
        risk_per_contract = premium * stop_pct * 100  # × 100 for contract multiplier

        if risk_per_contract <= 0:
            return (0, stop_pct, 0, 0)

        # Calculate contracts based on risk
        risk_based_contracts = int(target_risk / risk_per_contract)

        # Use minimum of risk-based and tier-based
        num_contracts = min(risk_based_contracts, base_contracts)

        # Ensure at least 1 contract
        num_contracts = max(1, num_contracts)

        # Calculate stop and target prices
        stop_price = premium * (1 - stop_pct)
        target_price = premium * (1 + config.OPTIONS_PROFIT_TARGET_PCT)

        return (num_contracts, stop_pct, stop_price, target_price)

    # =========================================================================
    # V2.3: SPREAD LEG SELECTION
    # =========================================================================

    def select_spread_legs(
        self,
        contracts: List[OptionContract],
        direction: OptionDirection,
        target_width: float = None,
        current_time: str = None,
        dte_min: int = None,
        dte_max: int = None,
        set_cooldown: bool = True,
        log_filters: bool = True,
        debug_stats: Optional[Dict[str, Any]] = None,
    ) -> Optional[tuple]:
        """Select long and short leg contracts for a debit spread."""
        return self._vass_entry_engine.select_spread_legs(
            host=self,
            contracts=contracts,
            direction=direction,
            target_width=target_width,
            current_time=current_time,
            dte_min=dte_min,
            dte_max=dte_max,
            set_cooldown=set_cooldown,
            log_filters=log_filters,
            debug_stats=debug_stats,
        )

    def select_spread_legs_with_fallback(
        self,
        contracts: List[OptionContract],
        direction: OptionDirection,
        dte_ranges: List[Tuple[int, int]],
        target_width: float = None,
        current_time: str = None,
        set_cooldown: bool = True,
    ) -> Optional[tuple]:
        """Try multiple DTE ranges before applying spread failure cooldown."""
        return self._vass_entry_engine.select_spread_legs_with_fallback(
            host=self,
            contracts=contracts,
            direction=direction,
            dte_ranges=dte_ranges,
            target_width=target_width,
            current_time=current_time,
            set_cooldown=set_cooldown,
        )

    # =========================================================================
    # V2.8: CREDIT SPREAD LEG SELECTION (VASS)
    # =========================================================================

    def select_credit_spread_legs(
        self,
        contracts: List[OptionContract],
        strategy: SpreadStrategy,
        dte_min: int,
        dte_max: int,
        current_time: Optional[str] = None,
        set_cooldown: bool = True,
        log_filters: bool = True,
        debug_stats: Optional[Dict[str, Any]] = None,
    ) -> Optional[Tuple[OptionContract, OptionContract]]:
        """Select short and long legs for credit spread construction."""
        return self._vass_entry_engine.select_credit_spread_legs(
            host=self,
            contracts=contracts,
            strategy=strategy,
            dte_min=dte_min,
            dte_max=dte_max,
            current_time=current_time,
            set_cooldown=set_cooldown,
            log_filters=log_filters,
            debug_stats=debug_stats,
        )

    def select_credit_spread_legs_with_fallback(
        self,
        contracts: List[OptionContract],
        strategy: SpreadStrategy,
        dte_ranges: List[Tuple[int, int]],
        current_time: Optional[str] = None,
        set_cooldown: bool = True,
    ) -> Optional[Tuple[OptionContract, OptionContract]]:
        """Try multiple DTE ranges for credit spreads before cooldown."""
        return self._vass_entry_engine.select_credit_spread_legs_with_fallback(
            host=self,
            contracts=contracts,
            strategy=strategy,
            dte_ranges=dte_ranges,
            current_time=current_time,
            set_cooldown=set_cooldown,
        )

    def pop_last_spread_failure_stats(self) -> Optional[str]:
        return self._vass_entry_engine.pop_last_spread_failure_stats(host=self)

    def pop_last_credit_failure_stats(self) -> Optional[str]:
        return self._vass_entry_engine.pop_last_credit_failure_stats(host=self)

    def set_last_entry_validation_failure(self, reason: Optional[str]) -> None:
        self._vass_entry_engine.set_last_entry_validation_failure(host=self, reason=reason)

    def pop_last_entry_validation_failure(self) -> Optional[str]:
        return self._vass_entry_engine.pop_last_entry_validation_failure(host=self)

    def _normalize_intraday_lane(self, lane: Optional[str]) -> str:
        lane_key = str(lane or "").upper()
        return lane_key if lane_key in ("MICRO", "ITM") else "MICRO"

    def _ensure_intraday_validation_failure_buffers(self) -> None:
        failures = getattr(self, "_last_intraday_validation_failure_by_lane", None)
        details = getattr(self, "_last_intraday_validation_detail_by_lane", None)
        if not isinstance(failures, dict):
            failures = {}
        if not isinstance(details, dict):
            details = {}
        for lane in ("MICRO", "ITM"):
            failures.setdefault(lane, None)
            details.setdefault(lane, None)
        self._last_intraday_validation_failure_by_lane = failures
        self._last_intraday_validation_detail_by_lane = details

    def set_last_intraday_validation_failure(
        self, lane: Optional[str], reason: Optional[str], detail: Optional[str] = None
    ) -> None:
        self._ensure_intraday_validation_failure_buffers()
        lane_key = self._normalize_intraday_lane(lane)
        self._last_intraday_validation_failure_by_lane[lane_key] = reason
        self._last_intraday_validation_detail_by_lane[lane_key] = detail

    def pop_last_intraday_validation_failure(
        self, lane: Optional[str]
    ) -> Tuple[Optional[str], Optional[str]]:
        self._ensure_intraday_validation_failure_buffers()
        lane_key = self._normalize_intraday_lane(lane)
        reason = self._last_intraday_validation_failure_by_lane.get(lane_key)
        detail = self._last_intraday_validation_detail_by_lane.get(lane_key)
        self._last_intraday_validation_failure_by_lane[lane_key] = None
        self._last_intraday_validation_detail_by_lane[lane_key] = None
        return reason, detail

    def set_last_trade_limit_failure(
        self, reason: Optional[str], detail: Optional[str] = None
    ) -> None:
        self._last_trade_limit_failure = reason
        self._last_trade_limit_detail = detail

    def pop_last_trade_limit_failure(self) -> Tuple[Optional[str], Optional[str]]:
        reason = self._last_trade_limit_failure
        detail = self._last_trade_limit_detail
        self._last_trade_limit_failure = None
        self._last_trade_limit_detail = None
        return reason, detail

    def _get_effective_credit_min(self, vix_current: Optional[float] = None) -> float:
        """
        Return IV-adaptive credit floor used consistently by selection and entry validation.
        """
        smoothed_vix = self._iv_sensor.get_smoothed_vix()
        if vix_current is not None:
            try:
                smoothed_vix = max(smoothed_vix, float(vix_current))
            except (TypeError, ValueError):
                pass
        if smoothed_vix > config.CREDIT_SPREAD_HIGH_IV_VIX_THRESHOLD:
            return config.CREDIT_SPREAD_MIN_CREDIT_HIGH_IV
        return config.CREDIT_SPREAD_MIN_CREDIT

    def _get_effective_credit_to_width_min(self, vix_current: Optional[float] = None) -> float:
        """Return IV-adaptive minimum credit/width ratio for credit spread quality gating.

        Three-tier system to avoid over-filtering in moderate VIX (Pitfall 6):
        - VIX > 30: 30% (high IV, wide credits available)
        - VIX 20-30: 32% (moderate IV, slight relaxation)
        - VIX < 20: 35% (low IV, strict quality gate)
        """
        smoothed_vix = self._iv_sensor.get_smoothed_vix()
        if vix_current is not None:
            try:
                smoothed_vix = max(smoothed_vix, float(vix_current))
            except (TypeError, ValueError):
                pass
        if smoothed_vix > config.CREDIT_SPREAD_HIGH_IV_VIX_THRESHOLD:
            return float(getattr(config, "CREDIT_SPREAD_MIN_CREDIT_TO_WIDTH_PCT_HIGH_IV", 0.30))
        medium_threshold = float(getattr(config, "CREDIT_SPREAD_MEDIUM_IV_VIX_THRESHOLD", 20.0))
        if smoothed_vix > medium_threshold:
            return float(getattr(config, "CREDIT_SPREAD_MIN_CREDIT_TO_WIDTH_PCT_MEDIUM_IV", 0.32))
        return float(getattr(config, "CREDIT_SPREAD_MIN_CREDIT_TO_WIDTH_PCT", 0.35))

    def _snap_width_to_strike_grid(self, raw_width: float) -> float:
        """Round width to nearest dollar (QQQ strike spacing)."""
        return max(1.0, round(raw_width))

    def _get_dynamic_spread_widths(self, current_price: Optional[float] = None) -> Dict[str, float]:
        """Return all spread width parameters, dynamically scaled by underlying price.

        V12.12: percentage-of-underlying for regime-universal width scaling.
        Falls back to fixed-dollar config when PCT_BASED is False or price unavailable.
        """
        use_pct = bool(getattr(config, "SPREAD_WIDTH_PCT_BASED", False))
        price = float(current_price) if current_price and float(current_price) > 0 else 0.0

        if use_pct and price > 0:
            return {
                "width_min": self._snap_width_to_strike_grid(
                    price * float(getattr(config, "SPREAD_WIDTH_MIN_PCT", 0.008))
                ),
                "width_min_low_vix": self._snap_width_to_strike_grid(
                    price * float(getattr(config, "SPREAD_WIDTH_MIN_LOW_VIX_PCT", 0.006))
                ),
                "width_max": self._snap_width_to_strike_grid(
                    price * float(getattr(config, "SPREAD_WIDTH_MAX_PCT", 0.020))
                ),
                "width_target": self._snap_width_to_strike_grid(
                    price * float(getattr(config, "SPREAD_WIDTH_TARGET_PCT", 0.010))
                ),
                "width_effective_max": self._snap_width_to_strike_grid(
                    price * float(getattr(config, "SPREAD_WIDTH_EFFECTIVE_MAX_PCT", 0.015))
                ),
                "credit_width_target": self._snap_width_to_strike_grid(
                    price * float(getattr(config, "CREDIT_SPREAD_WIDTH_TARGET_PCT", 0.010))
                ),
            }

        return {
            "width_min": float(getattr(config, "SPREAD_WIDTH_MIN", 4.0)),
            "width_min_low_vix": float(getattr(config, "SPREAD_WIDTH_MIN_LOW_VIX", 3.0)),
            "width_max": float(getattr(config, "SPREAD_WIDTH_MAX", 10.0)),
            "width_target": float(getattr(config, "SPREAD_WIDTH_TARGET", 4.0)),
            "width_effective_max": float(getattr(config, "SPREAD_WIDTH_EFFECTIVE_MAX", 7.0)),
            "credit_width_target": float(getattr(config, "CREDIT_SPREAD_WIDTH_TARGET", 5.0)),
        }

    def _get_effective_spread_width_min(
        self,
        vix_current: Optional[float] = None,
        current_price: Optional[float] = None,
    ) -> float:
        """Return VIX-adaptive minimum spread width for debit/credit leg construction."""
        widths = self._get_dynamic_spread_widths(current_price)
        base_min = widths["width_min"]
        low_min = widths["width_min_low_vix"]
        low_threshold = float(getattr(config, "SPREAD_WIDTH_LOW_VIX_THRESHOLD", 18.0))

        smoothed_vix = self._iv_sensor.get_smoothed_vix()
        if vix_current is not None:
            try:
                smoothed_vix = max(smoothed_vix, float(vix_current))
            except (TypeError, ValueError):
                pass

        if smoothed_vix < low_threshold:
            return max(1.0, low_min)
        return max(1.0, base_min)

    def estimate_spread_margin_per_contract(
        self,
        spread_width: float,
        spread_type: Optional[str] = None,
        credit_received: Optional[float] = None,
    ) -> float:
        """
        Estimate margin requirement per spread contract using a single canonical formula.

        Debit spread: width * 100
        Credit spread: (width - credit) * 100
        """
        try:
            width = max(0.0, float(spread_width))
        except (TypeError, ValueError):
            return 0.0
        if width <= 0:
            return 0.0

        spread_label = (spread_type or "").upper()
        is_credit = "CREDIT" in spread_label

        if is_credit:
            try:
                credit = max(0.0, float(credit_received or 0.0))
            except (TypeError, ValueError):
                credit = 0.0
            return max(1.0, (width - credit) * 100.0)

        return width * 100.0

    def get_usable_margin(self, margin_remaining: float) -> float:
        """
        Apply global margin safety factor and post-rejection cap.
        """
        if margin_remaining <= 0:
            return 0.0
        safety_factor = getattr(config, "SPREAD_MARGIN_SAFETY_FACTOR", 0.80)
        usable_margin = margin_remaining * safety_factor
        if self._rejection_margin_cap is not None:
            usable_margin = min(usable_margin, self._rejection_margin_cap)
        return max(0.0, usable_margin)

    # V6.0: _check_macro_regime_gate() REMOVED
    # Direction decisions now handled by conviction resolution (resolve_trade_signal)
    # in main.py before calling entry signal functions.

    # =========================================================================
    # ENTRY SIGNAL
    # =========================================================================

    def check_entry_signal(
        self,
        adx_value: float,
        current_price: float,
        ma200_value: float,
        iv_rank: float,
        best_contract: Optional[OptionContract] = None,
        current_hour: int = 0,
        current_minute: int = 0,
        current_date: str = "",
        portfolio_value: float = 0.0,
        regime_score: float = 50.0,
        gap_filter_triggered: bool = False,
        vol_shock_active: bool = False,
        time_guard_active: bool = False,
        size_multiplier: float = 1.0,
        direction: Optional[OptionDirection] = None,
        strategy_override: Optional[str] = None,
        dte_range: Optional[Tuple[int, int]] = None,
        is_eod_scan: bool = False,
    ) -> Optional[TargetWeight]:
        # Keep compatibility knobs accepted at wrapper boundary.
        _ = (strategy_override, dte_range, is_eod_scan)
        return check_entry_signal_impl(
            self,
            adx_value=adx_value,
            current_price=current_price,
            ma200_value=ma200_value,
            iv_rank=iv_rank,
            best_contract=best_contract,
            current_hour=current_hour,
            current_minute=current_minute,
            current_date=current_date,
            portfolio_value=portfolio_value,
            regime_score=regime_score,
            gap_filter_triggered=gap_filter_triggered,
            vol_shock_active=vol_shock_active,
            time_guard_active=time_guard_active,
            size_multiplier=size_multiplier,
            direction=direction,
        )

    def _get_spread_debit_width_cap(self, vix_level: Optional[float]) -> float:
        """Resolve adaptive debit/width cap by current VIX band."""
        if vix_level is None:
            return float(getattr(config, "SPREAD_DW_CAP_NORMAL", 0.42))
        try:
            vix = float(vix_level)
        except Exception:
            return float(getattr(config, "SPREAD_DW_CAP_NORMAL", 0.42))
        if vix > 35:
            return float(getattr(config, "SPREAD_DW_CAP_PANIC", 0.28))
        if vix >= 25:
            return float(getattr(config, "SPREAD_DW_CAP_HIGH", 0.32))
        if vix >= 18:
            return float(getattr(config, "SPREAD_DW_CAP_ELEVATED", 0.36))
        if vix >= 13:
            return float(getattr(config, "SPREAD_DW_CAP_NORMAL", 0.42))
        return float(getattr(config, "SPREAD_DW_CAP_COMPRESSED", 0.48))

    def _get_spread_absolute_debit_cap(self, vix_level: Optional[float], width: float) -> float:
        """Resolve width-scaled absolute debit cap for debit spreads."""
        base_cap = float(getattr(config, "SPREAD_DW_ABSOLUTE_CAP", 2.00))
        if bool(getattr(config, "SPREAD_DW_ABSOLUTE_CAP_DYNAMIC_ENABLED", False)):
            try:
                vix_value = float(vix_level) if vix_level is not None else 20.0
            except Exception:
                vix_value = 20.0
            vix_floor = max(0.1, float(getattr(config, "SPREAD_DW_ABSOLUTE_CAP_VIX_FLOOR", 10.0)))
            baseline = float(getattr(config, "SPREAD_DW_ABSOLUTE_CAP_BASELINE", 1.00))
            vix_scale = float(getattr(config, "SPREAD_DW_ABSOLUTE_CAP_VIX_SCALE", 20.0))
            cap_min = float(getattr(config, "SPREAD_DW_ABSOLUTE_CAP_MIN", 1.60))
            cap_max = float(getattr(config, "SPREAD_DW_ABSOLUTE_CAP_MAX", 2.60))
            dynamic_cap = baseline * (1.0 + (vix_scale / max(vix_value, vix_floor)))
            base_cap = max(cap_min, min(cap_max, dynamic_cap))
        if width <= 0:
            return base_cap
        return base_cap * (width / 5.0)

    def check_spread_entry_signal(
        self,
        regime_score: float,
        vix_current: float,
        adx_value: float,
        current_price: float,
        ma200_value: float,
        iv_rank: float,
        current_hour: int,
        current_minute: int,
        current_date: str,
        portfolio_value: float,
        long_leg_contract: Optional[OptionContract] = None,
        short_leg_contract: Optional[OptionContract] = None,
        gap_filter_triggered: bool = False,
        vol_shock_active: bool = False,
        size_multiplier: float = 1.0,
        margin_remaining: Optional[float] = None,
        dte_min: int = None,
        dte_max: int = None,
        is_eod_scan: bool = False,
        direction: Optional[OptionDirection] = None,
        ma50_value: float = 0.0,
        candidate_contracts: Optional[List[OptionContract]] = None,
    ) -> Optional[TargetWeight]:
        """Check for debit spread entry signal."""
        return check_spread_entry_signal_impl(
            self,
            regime_score=regime_score,
            vix_current=vix_current,
            adx_value=adx_value,
            current_price=current_price,
            ma200_value=ma200_value,
            iv_rank=iv_rank,
            current_hour=current_hour,
            current_minute=current_minute,
            current_date=current_date,
            portfolio_value=portfolio_value,
            long_leg_contract=long_leg_contract,
            short_leg_contract=short_leg_contract,
            gap_filter_triggered=gap_filter_triggered,
            vol_shock_active=vol_shock_active,
            size_multiplier=size_multiplier,
            margin_remaining=margin_remaining,
            dte_min=dte_min,
            dte_max=dte_max,
            is_eod_scan=is_eod_scan,
            direction=direction,
            ma50_value=ma50_value,
            candidate_contracts=candidate_contracts,
        )

    def check_credit_spread_entry_signal(
        self,
        regime_score: float,
        vix_current: float,
        adx_value: float,
        current_price: float,
        ma200_value: float,
        iv_rank: float,
        current_hour: int,
        current_minute: int,
        current_date: str,
        portfolio_value: float,
        short_leg_contract: Optional[OptionContract] = None,
        long_leg_contract: Optional[OptionContract] = None,
        strategy: Optional[SpreadStrategy] = None,
        gap_filter_triggered: bool = False,
        vol_shock_active: bool = False,
        size_multiplier: float = 1.0,
        margin_remaining: Optional[float] = None,
        is_eod_scan: bool = False,
        direction: Optional[OptionDirection] = None,
    ) -> Optional[TargetWeight]:
        """Check for credit spread entry signal."""
        return check_credit_spread_entry_signal_impl(
            self,
            regime_score=regime_score,
            vix_current=vix_current,
            adx_value=adx_value,
            current_price=current_price,
            ma200_value=ma200_value,
            iv_rank=iv_rank,
            current_hour=current_hour,
            current_minute=current_minute,
            current_date=current_date,
            portfolio_value=portfolio_value,
            short_leg_contract=short_leg_contract,
            long_leg_contract=long_leg_contract,
            strategy=strategy,
            gap_filter_triggered=gap_filter_triggered,
            vol_shock_active=vol_shock_active,
            size_multiplier=size_multiplier,
            margin_remaining=margin_remaining,
            is_eod_scan=is_eod_scan,
            direction=direction,
        )

    # =========================================================================
    # V5.3 ASSIGNMENT RISK MANAGEMENT (P0/P1 Fixes)
    # =========================================================================

    def _is_short_leg_deep_itm(
        self,
        short_leg: "OptionContract",
        underlying_price: float,
        current_dte: int,
    ) -> Tuple[bool, str]:
        return is_short_leg_deep_itm_impl(
            self,
            short_leg=short_leg,
            underlying_price=underlying_price,
            current_dte=current_dte,
        )

    def _check_overnight_itm_short_risk(
        self,
        short_leg: "OptionContract",
        underlying_price: float,
        current_dte: int,
        current_hour: int,
        current_minute: int,
    ) -> Tuple[bool, str]:
        return check_overnight_itm_short_risk_impl(
            self,
            short_leg=short_leg,
            underlying_price=underlying_price,
            current_dte=current_dte,
            current_hour=current_hour,
            current_minute=current_minute,
        )

    def _check_assignment_margin_buffer(
        self,
        spread: "SpreadPosition",
        underlying_price: float,
        available_margin: float,
    ) -> Tuple[bool, str]:
        return check_assignment_margin_buffer_impl(
            self,
            spread=spread,
            underlying_price=underlying_price,
            available_margin=available_margin,
        )

    def _check_short_leg_itm_exit(
        self,
        short_leg: "OptionContract",
        underlying_price: float,
    ) -> Tuple[bool, str]:
        return check_short_leg_itm_exit_impl(
            self,
            short_leg=short_leg,
            underlying_price=underlying_price,
        )

    def check_premarket_itm_shorts(
        self,
        underlying_price: float,
        spread_override: Optional[SpreadPosition] = None,
    ) -> Optional[List[TargetWeight]]:
        return check_premarket_itm_shorts_impl(
            self,
            underlying_price=underlying_price,
            spread_override=spread_override,
        )

    def check_assignment_risk_exit(
        self,
        underlying_price: float,
        current_dte: int,
        current_hour: int,
        current_minute: int,
        available_margin: float = 0,
        spread_override: Optional[SpreadPosition] = None,
    ) -> Optional[List[TargetWeight]]:
        return check_assignment_risk_exit_impl(
            self,
            underlying_price=underlying_price,
            current_dte=current_dte,
            current_hour=current_hour,
            current_minute=current_minute,
            available_margin=available_margin,
            spread_override=spread_override,
        )

    def get_assignment_aware_size_multiplier(
        self,
        spread_width: float,
        portfolio_value: float,
        requested_contracts: int,
    ) -> float:
        return get_assignment_aware_size_multiplier_impl(
            self,
            spread_width=spread_width,
            portfolio_value=portfolio_value,
            requested_contracts=requested_contracts,
        )

    def handle_partial_assignment(
        self,
        assigned_symbol: str,
        assigned_quantity: int,
    ) -> Optional[List[TargetWeight]]:
        return handle_partial_assignment_impl(
            self,
            assigned_symbol=assigned_symbol,
            assigned_quantity=assigned_quantity,
        )

    def _record_vass_mfe_diag(self, spread: SpreadPosition, prev_tier: int) -> None:
        return record_vass_mfe_diag_impl(self, spread=spread, prev_tier=prev_tier)

    def _get_vass_exit_profile(
        self, spread: SpreadPosition, vix_current: Optional[float]
    ) -> Dict[str, Any]:
        return get_vass_exit_profile_impl(self, spread=spread, vix_current=vix_current)

    def _resolve_qqq_atr_pct(self, underlying_price: Optional[float]) -> Optional[float]:
        return resolve_qqq_atr_pct_impl(self, underlying_price=underlying_price)

    def check_spread_exit_signals(
        self,
        long_leg_price: Optional[float] = None,
        short_leg_price: Optional[float] = None,
        regime_score: Optional[float] = None,
        current_dte: Optional[int] = None,
        vix_current: Optional[float] = None,
        spread_override: Optional[SpreadPosition] = None,
        underlying_price: Optional[float] = None,
        # Compatibility aliases retained at wrapper boundary.
        current_price: Optional[float] = None,
        spread: Optional[SpreadPosition] = None,
        current_vix: Optional[float] = None,
        vix_5d_change: Optional[float] = None,
        current_time: Optional[datetime] = None,
        ma200_value: float = 0.0,
        current_date: Optional[str] = None,
        is_eod_scan: bool = False,
    ) -> Optional[List[TargetWeight]]:
        _ = (vix_5d_change, current_time, ma200_value, current_date, is_eod_scan)
        active_spread = spread_override if spread_override is not None else spread
        if long_leg_price is None:
            long_leg_price = float(current_price or 0.0)
        if short_leg_price is None:
            short_leg_price = 0.0
            try:
                if active_spread is not None and active_spread.short_leg is not None:
                    short_leg_price = float(active_spread.short_leg.mid_price)
            except Exception:
                short_leg_price = 0.0
        if regime_score is None:
            regime_score = 50.0
        if current_dte is None:
            current_dte = 0
        if vix_current is None:
            vix_current = current_vix
        return check_spread_exit_signals_impl(
            self,
            long_leg_price=float(long_leg_price),
            short_leg_price=float(short_leg_price),
            regime_score=float(regime_score),
            current_dte=current_dte,
            vix_current=vix_current,
            spread_override=active_spread,
            underlying_price=underlying_price,
        )

    # =========================================================================
    # V2.4.1 FRIDAY FIREWALL
    # =========================================================================

    def check_friday_firewall_exit(
        self,
        current_vix: float,
        current_date: str,
        vix_close_all_threshold: float = 25.0,
        vix_keep_fresh_threshold: float = 15.0,
        spread_override: Optional[SpreadPosition] = None,
    ) -> Optional[List[TargetWeight]]:
        return check_friday_firewall_exit_impl(
            self,
            current_vix=current_vix,
            current_date=current_date,
            vix_close_all_threshold=vix_close_all_threshold,
            vix_keep_fresh_threshold=vix_keep_fresh_threshold,
            spread_override=spread_override,
        )

    def check_overnight_gap_protection_exit(
        self,
        current_vix: float,
        current_date: str,
    ) -> Optional[List[TargetWeight]]:
        return check_overnight_gap_protection_exit_impl(
            self,
            current_vix=current_vix,
            current_date=current_date,
        )

    def _get_intraday_exit_profile(self, entry_strategy: str) -> Tuple[float, Optional[float]]:
        return get_intraday_exit_profile_impl(self, entry_strategy=entry_strategy)

    def _apply_intraday_target_overrides(
        self,
        *,
        entry_strategy: str,
        target_pct: float,
        current_dte: Optional[int],
    ) -> float:
        return apply_intraday_target_overrides_impl(
            self,
            entry_strategy=entry_strategy,
            target_pct=target_pct,
            current_dte=current_dte,
        )

    def _apply_intraday_stop_overrides(
        self,
        *,
        entry_strategy: str,
        stop_pct: float,
        current_dte: Optional[int],
    ) -> float:
        return apply_intraday_stop_overrides_impl(
            self,
            entry_strategy=entry_strategy,
            stop_pct=stop_pct,
            current_dte=current_dte,
        )

    def _get_trail_config(self, entry_strategy: str) -> Optional[Tuple[float, float]]:
        return get_trail_config_impl(self, entry_strategy=entry_strategy)

    def check_exit_signals(
        self,
        current_price: float,
        current_dte: Optional[int] = None,
        position: "Optional[OptionsPosition]" = None,
    ) -> Optional[TargetWeight]:
        return check_exit_signals_impl(
            self,
            current_price=current_price,
            current_dte=current_dte,
            position=position,
        )

    def check_force_exit(
        self,
        current_hour: int,
        current_minute: int,
        current_price: float,
    ) -> Optional[TargetWeight]:
        """
        Check for forced exit at 3:45 PM ET.

        Per V2.1 spec, options positions must be closed by 3:45 PM
        to avoid overnight theta decay and regulatory risk.

        Args:
            current_hour: Current hour (0-23) Eastern.
            current_minute: Current minute (0-59).
            current_price: Current option price.

        Returns:
            TargetWeight for forced exit, or None if no position or not time yet.
        """
        if self._position is None:
            return None

        # Check if it's force exit time (15:45 ET)
        force_exit_time = current_hour > config.OPTIONS_FORCE_EXIT_HOUR or (
            current_hour == config.OPTIONS_FORCE_EXIT_HOUR
            and current_minute >= config.OPTIONS_FORCE_EXIT_MINUTE
        )

        if not force_exit_time:
            return None

        symbol = self._position.contract.symbol
        entry_price = self._position.entry_price

        # Calculate P&L percentage
        pnl_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0

        reason = f"TIME_EXIT_1545 {pnl_pct:+.1%} (Price: ${current_price:.2f})"
        self.log(f"OPT: FORCE_EXIT {symbol} | {reason}", trades_only=True)

        return TargetWeight(
            symbol=self._symbol_str(symbol),
            target_weight=0.0,
            source="OPT",
            urgency=Urgency.IMMEDIATE,
            reason=reason,
            requested_quantity=max(1, int(getattr(self._position, "num_contracts", 1))),
        )

    # =========================================================================
    # V2.1.1 DUAL-MODE ARCHITECTURE
    # =========================================================================

    def determine_mode(self, dte: int) -> OptionsMode:
        """
        Determine operating mode based on DTE.

        Critical insight: Entry timing matters more for shorter DTE.
        - 2 DTE: 2-hour window = 15% of option's life → Micro Regime ESSENTIAL
        - 14 DTE: 2-hour window = 2% of option's life → Simple filters sufficient

        Args:
            dte: Days to expiration.

        Returns:
            OptionsMode.SWING or OptionsMode.INTRADAY.
        """
        if dte <= config.OPTIONS_INTRADAY_DTE_MAX:
            return OptionsMode.INTRADAY
        return OptionsMode.SWING

    def _apply_tiered_dollar_cap(self, raw_allocation: float, tradeable_equity: float) -> float:
        """
        V2.7: Apply tiered dollar cap to options allocation.

        Prevents oversizing on small accounts while allowing growth on larger accounts.
        Uses min(percentage_allocation, dollar_cap) logic.

        Tier 1 (<$60K): Cap at $5,000 per spread
        Tier 2 ($60K-$100K): Cap at $10,000 per spread
        Tier 3 (>$100K): No cap, use raw percentage

        Args:
            raw_allocation: Percentage-based allocation in dollars.
            tradeable_equity: Current tradeable equity (settled cash - lockbox).

        Returns:
            Capped allocation amount.
        """
        if tradeable_equity < config.OPTIONS_DOLLAR_CAP_TIER_1_THRESHOLD:
            # Tier 1: Small account protection
            capped = min(raw_allocation, config.OPTIONS_DOLLAR_CAP_TIER_1)
            if capped < raw_allocation:
                self.log(
                    f"SPREAD: Allocation capped at ${capped:,.0f} (Tier 1) | "
                    f"Raw=${raw_allocation:,.0f} | Equity=${tradeable_equity:,.0f}"
                )
            return capped
        elif tradeable_equity < config.OPTIONS_DOLLAR_CAP_TIER_2_THRESHOLD:
            # Tier 2: Medium account
            capped = min(raw_allocation, config.OPTIONS_DOLLAR_CAP_TIER_2)
            if capped < raw_allocation:
                self.log(
                    f"SPREAD: Allocation capped at ${capped:,.0f} (Tier 2) | "
                    f"Raw=${raw_allocation:,.0f} | Equity=${tradeable_equity:,.0f}"
                )
            return capped
        else:
            # Tier 3: Large account - percentage-based only
            return raw_allocation

    def _calculate_credit_spread_size(
        self,
        short_leg: OptionContract,
        long_leg: OptionContract,
        allocation: float,
    ) -> Tuple[int, float, float, float]:
        """
        V2.8 SAFETY: Calculate credit spread size based on MARGIN REQUIREMENT.

        CRITICAL: Size based on MAX LOSS, not premium received!
        Sizing on premium would create positions that exceed risk limits.

        Example:
            Width = $5.00, Credit = $0.50
            WRONG: $5,000 / $50 = 100 contracts (DANGEROUS - $50K risk!)
            CORRECT: $5,000 / $450 = 11 contracts (defined $4,950 max loss)

        Args:
            short_leg: Contract we SELL (collect premium)
            long_leg: Contract we BUY (protection)
            allocation: Maximum dollar amount to risk ($5K cap)

        Returns:
            Tuple of (num_spreads, credit_per_spread, max_loss_per_spread, total_margin)
            Returns (0, 0, 0, 0) if invalid spread
        """
        # Calculate width (always positive)
        if short_leg.direction == OptionDirection.PUT:
            # Bull Put: short strike > long strike
            width = short_leg.strike - long_leg.strike
        else:
            # Bear Call: long strike > short strike
            width = long_leg.strike - short_leg.strike

        if width <= 0:
            self.log(f"SAFETY: Invalid spread width: {width}")
            return (0, 0.0, 0.0, 0.0)

        # Credit = what we receive (conservative: bid for sell, ask for buy)
        credit_received = short_leg.bid - long_leg.ask

        # SAFETY CHECK: Validate credit is positive
        if credit_received <= 0:
            self.log(
                f"SAFETY: Invalid credit spread - no positive credit | "
                f"Short bid={short_leg.bid} | Long ask={long_leg.ask}"
            )
            return (0, 0.0, 0.0, 0.0)

        # MARGIN REQUIREMENT = (Width - Credit) × 100
        # This is the MAX LOSS per spread
        margin_per_spread = (width - credit_received) * 100

        # SAFETY CHECK: Margin must be positive
        if margin_per_spread <= 0:
            self.log(
                f"SAFETY: Invalid margin calculation | "
                f"Width={width} | Credit={credit_received} | Margin={margin_per_spread}"
            )
            return (0, 0.0, 0.0, 0.0)

        # SIZE BASED ON MARGIN, NOT PREMIUM
        # $5,000 / $450 margin = 11 contracts max
        max_contracts = int(allocation / margin_per_spread)

        if max_contracts <= 0:
            self.log(
                f"SAFETY: Allocation too small for even 1 spread | "
                f"Allocation=${allocation:.0f} | MarginPerSpread=${margin_per_spread:.0f}"
            )
            return (0, 0.0, 0.0, 0.0)

        total_margin = max_contracts * margin_per_spread

        # SAFETY CHECK: Never exceed allocation
        if total_margin > allocation:
            self.log(
                f"SAFETY: Reducing contracts - margin {total_margin:.0f} > allocation {allocation:.0f}"
            )
            max_contracts = int(allocation / margin_per_spread)
            total_margin = max_contracts * margin_per_spread

        self.log(
            f"SAFETY: Credit spread sizing | "
            f"Width=${width:.2f} | Credit=${credit_received:.2f} | "
            f"MarginPerSpread=${margin_per_spread:.0f} | "
            f"Allocation=${allocation:.0f} | MaxContracts={max_contracts} | "
            f"TotalMargin=${total_margin:.0f}"
        )

        return (max_contracts, credit_received, margin_per_spread, total_margin)

    def _classify_intraday_bucket(self, strategy_name: str) -> str:
        """Map intraday strategy names to reservation buckets."""
        name = str(strategy_name or "").upper()
        if "ITM_MOMENTUM" in name:
            return "ITM"
        return "OTM"

    def _get_reserved_bucket_usage_dollars(self) -> Dict[str, float]:
        """Current reserved capital-at-risk usage per options bucket."""
        usage = {"VASS": 0.0, "ITM": 0.0, "OTM": 0.0}

        # Active swing spreads (VASS)
        for spread in self.get_spread_positions():
            try:
                spread_type = str(getattr(spread, "spread_type", "") or "").upper()
                num = max(1, int(getattr(spread, "num_spreads", 1) or 1))
                width = float(getattr(spread, "width", 0.0) or 0.0)
                net_debit = float(getattr(spread, "net_debit", 0.0) or 0.0)
                if "CREDIT" in spread_type:
                    credit = abs(net_debit)
                    per_spread_risk = max(0.0, (width - credit) * 100.0)
                else:
                    per_spread_risk = max(0.0, net_debit * 100.0)
                usage["VASS"] += per_spread_risk * num
            except Exception:
                continue

        # Pending spread entry
        if self._pending_spread_type is not None and self._pending_num_contracts is not None:
            try:
                spread_type = str(self._pending_spread_type or "").upper()
                num = max(1, int(self._pending_num_contracts or 1))
                width = float(self._pending_spread_width or 0.0)
                net_debit = float(self._pending_net_debit or 0.0)
                if "CREDIT" in spread_type:
                    credit = abs(net_debit)
                    per_spread_risk = max(0.0, (width - credit) * 100.0)
                else:
                    per_spread_risk = max(0.0, net_debit * 100.0)
                usage["VASS"] += per_spread_risk * num
            except Exception:
                pass

        # Active intraday positions
        for intraday_pos in self.get_intraday_positions():
            try:
                bucket = self._classify_intraday_bucket(
                    str(getattr(intraday_pos, "entry_strategy", "") or "")
                )
                risk = (
                    float(getattr(intraday_pos, "entry_price", 0.0) or 0.0)
                    * max(1, int(getattr(intraday_pos, "num_contracts", 1) or 1))
                    * 100.0
                )
                usage[bucket] += max(0.0, risk)
            except Exception:
                pass

        # Pending intraday entry
        if self._pending_intraday_entry and self._pending_num_contracts is not None:
            try:
                strategy = str(self._pending_entry_strategy or "")
                bucket = self._classify_intraday_bucket(strategy)
                premium = float(
                    getattr(self._pending_contract, "mid_price", 0.0)
                    if self._pending_contract
                    else 0.0
                )
                risk = premium * max(1, int(self._pending_num_contracts or 1)) * 100.0
                usage[bucket] += max(0.0, risk)
            except Exception:
                pass

        return usage

    def _get_bucket_remaining_dollars(self, bucket: str, portfolio_value: float) -> float:
        """Hard reservation remaining dollars for the requested bucket."""
        usage = self._get_reserved_bucket_usage_dollars()
        bucket_u = str(bucket or "").upper()
        cap = 0.0
        if bucket_u == "VASS":
            cap = float(getattr(config, "VASS_MAX_RISK_DOLLARS", 0.0) or 0.0)
            if cap <= 0:
                cap = float(portfolio_value) * float(
                    getattr(config, "OPTIONS_SWING_ALLOCATION", 0.35)
                )
        elif bucket_u == "ITM":
            cap = float(getattr(config, "INTRADAY_ITM_MAX_DOLLARS", 0.0) or 0.0)
            if cap <= 0:
                cap = float(portfolio_value) * float(getattr(config, "INTRADAY_ITM_MAX_PCT", 0.15))
        elif bucket_u == "OTM":
            cap = float(getattr(config, "INTRADAY_OTM_MAX_DOLLARS", 0.0) or 0.0)
            if cap <= 0:
                cap = float(portfolio_value) * float(getattr(config, "INTRADAY_OTM_MAX_PCT", 0.10))
        used = float(usage.get(bucket_u, 0.0))
        return max(0.0, cap - used)

    def get_mode_allocation(
        self, mode: OptionsMode, portfolio_value: float, size_multiplier: float = 1.0
    ) -> float:
        """
        Get allocation for a specific mode with tiered dollar cap.

        V2.3.20: Added size_multiplier parameter for cold start sizing.
        V2.7: Added tiered dollar cap to prevent oversizing.

        Args:
            mode: Operating mode.
            portfolio_value: Tradeable equity (settled cash - lockbox).
            size_multiplier: Optional multiplier for position sizing (default 1.0).
                During cold start, this is 0.5 to reduce risk.

        Returns:
            Dollar allocation for the mode (adjusted by size_multiplier and tier cap).
        """
        if mode == OptionsMode.INTRADAY:
            base_allocation = portfolio_value * config.OPTIONS_INTRADAY_ALLOCATION
        else:
            base_allocation = portfolio_value * config.OPTIONS_SWING_ALLOCATION

        raw_allocation = base_allocation * size_multiplier

        # V2.7: Apply tiered dollar cap
        capped_allocation = self._apply_tiered_dollar_cap(raw_allocation, portfolio_value)

        return capped_allocation

    # =========================================================================
    # V2.8: VASS (Volatility-Adaptive Strategy Selection)
    # =========================================================================

    def _select_strategy(
        self,
        direction: str,  # "BULLISH" or "BEARISH"
        iv_environment: str,  # "LOW", "MEDIUM", "HIGH"
        is_intraday: bool = False,
    ) -> Tuple[SpreadStrategy, int, int]:
        """VASS strategy routing delegated to VASSEntryEngine."""
        return self._vass_entry_engine.select_strategy(
            direction=direction,
            iv_environment=iv_environment,
            is_intraday=is_intraday,
            spread_strategy_enum=SpreadStrategy,
        )

    def is_credit_strategy(self, strategy: SpreadStrategy) -> bool:
        """Check if strategy is a credit spread (collects premium)."""
        return strategy in (SpreadStrategy.BULL_PUT_CREDIT, SpreadStrategy.BEAR_CALL_CREDIT)

    def is_debit_strategy(self, strategy: SpreadStrategy) -> bool:
        """Check if strategy is a debit spread (pays premium)."""
        return strategy in (SpreadStrategy.BULL_CALL_DEBIT, SpreadStrategy.BEAR_PUT_DEBIT)

    def update_iv_sensor(self, vix_current: float, current_date: str = "") -> None:
        """Update IV sensor with current VIX reading and optional date key."""
        self._iv_sensor.update(vix_current, current_date or None)

    def get_iv_conviction(self) -> Tuple[bool, Optional[str], str]:
        """Return IV conviction tuple from IV sensor."""
        return self._iv_sensor.has_conviction()

    def get_iv_bearish_veto_status(self) -> Tuple[bool, str]:
        """Return strict bearish hard-veto eligibility from IV sensor."""
        return self._iv_sensor.is_bearish_veto_ready()

    def is_iv_sensor_ready(self) -> bool:
        """True when IV sensor has enough intraday history."""
        return self._iv_sensor.is_ready()

    def _classify_iv_environment_from_rank(self, iv_rank: Optional[float]) -> Optional[str]:
        """Map chain IV rank percentile to LOW/MEDIUM/HIGH buckets."""
        if iv_rank is None:
            return None
        try:
            rank = float(iv_rank)
        except Exception:
            return None
        if not (0.0 <= rank <= 100.0):
            return None
        low = float(getattr(config, "VASS_ROUTE_IV_RANK_LOW", 35.0))
        high = float(getattr(config, "VASS_ROUTE_IV_RANK_HIGH", 65.0))
        if high < low:
            low, high = high, low
        if rank < low:
            return "LOW"
        if rank > high:
            return "HIGH"
        return "MEDIUM"

    def get_iv_environment(self, iv_rank: Optional[float] = None) -> str:
        """Classify current IV environment (LOW/MEDIUM/HIGH)."""
        use_chain_rank = bool(getattr(config, "VASS_ROUTE_USE_CHAIN_IV_RANK", False)) and bool(
            getattr(config, "OPTIONS_IV_RANK_USE_CHAIN_PERCENTILE", True)
        )
        if use_chain_rank:
            routed = self._classify_iv_environment_from_rank(iv_rank)
            if routed is not None:
                return routed
        return self._iv_sensor.classify()

    def select_vass_strategy(
        self,
        direction: str,
        iv_environment: str,
        is_intraday: bool = False,
    ) -> Tuple[SpreadStrategy, int, int]:
        """Public wrapper for VASS direction+IV strategy routing."""
        return self._select_strategy(direction, iv_environment, is_intraday=is_intraday)

    def resolve_vass_strategy(
        self,
        direction: str,
        overlay_state: Optional[str] = None,
        iv_rank: Optional[float] = None,
    ) -> Tuple[Optional[SpreadStrategy], int, int, bool]:
        """Resolve VASS route including EARLY_STRESS strategy remap."""
        if getattr(config, "VASS_ENABLED", True) and self.is_iv_sensor_ready():
            iv_environment = self.get_iv_environment(iv_rank=iv_rank)
            return self._vass_entry_engine.resolve_strategy_with_overlay(
                direction=direction,
                overlay_state=overlay_state,
                iv_environment=iv_environment,
                spread_strategy_enum=SpreadStrategy,
                is_credit_strategy_func=self.is_credit_strategy,
            )
        return (None, config.SPREAD_DTE_MIN, config.SPREAD_DTE_MAX, False)

    def strategy_option_right(self, strategy: Optional[SpreadStrategy]) -> Optional[str]:
        """Return required option right key (CALL/PUT) for a VASS spread strategy."""
        return self._vass_entry_engine.strategy_option_right(strategy)

    def build_vass_dte_fallbacks(self, dte_min: int, dte_max: int) -> List[Tuple[int, int]]:
        """Build ordered DTE ranges for VASS spread selection."""
        return self._vass_entry_engine.build_dte_fallbacks(dte_min, dte_max)

    def get_contract_prices(self, contract: Any) -> Tuple[float, float]:
        """Get contract bid/ask with last-price fallback when quotes are stale."""
        bid = getattr(contract, "BidPrice", 0) or 0
        ask = getattr(contract, "AskPrice", 0) or 0
        if bid > 0 and ask > 0:
            return (bid, ask)
        last = getattr(contract, "LastPrice", 0) or 0
        if last <= 0:
            return (0, 0)
        spread_pct = 0.10 if last < 1.0 else (0.05 if last < 5.0 else 0.02)
        half_spread = spread_pct / 2
        return (last * (1 - half_spread), last * (1 + half_spread))

    def build_vass_candidate_contracts(
        self,
        chain: Any,
        direction: OptionDirection,
        dte_min: Optional[int] = None,
        dte_max: Optional[int] = None,
        option_right: Optional[Any] = None,
    ) -> List[OptionContract]:
        """Build VASS candidate contracts via VASS entry engine."""
        return self._vass_entry_engine.build_candidate_contracts(
            host=self,
            chain=chain,
            direction=direction,
            dte_min=dte_min,
            dte_max=dte_max,
            option_right=option_right,
            contract_model_cls=OptionContract,
        )

    def check_vass_bull_debit_trend_confirmation(
        self,
        *,
        vix_current: Optional[float],
        current_price: float,
        qqq_open: Optional[float],
        qqq_sma20: Optional[float],
        qqq_sma20_ready: bool,
    ) -> Tuple[bool, str, str]:
        """Delegate VASS bullish debit trend confirmation to VASSEntryEngine."""
        transition_ctx = self._get_regime_transition_context()
        base_regime = str(transition_ctx.get("base_regime", "") or "").upper()
        transition_overlay = str(transition_ctx.get("transition_overlay", "") or "").upper()
        # V12.12: MA20 tolerance decoupled from RECOVERY_RELAX.
        # Tolerance is a standalone pullback allowance (0.3% below MA20)
        # that should always apply when the trend confirmation gate is active.
        ma20_tol_enabled = bool(getattr(config, "VASS_BULL_DEBIT_MA20_TOLERANCE_ENABLED", True))
        return self._vass_entry_engine.check_bull_debit_trend_confirmation(
            vix_current=vix_current,
            current_price=current_price,
            qqq_open=qqq_open,
            qqq_sma20=qqq_sma20,
            qqq_sma20_ready=qqq_sma20_ready,
            relax_recovery=ma20_tol_enabled,
            relaxed_day_min_change_pct=float(
                getattr(config, "VASS_BULL_DEBIT_MIN_DAY_CHANGE_PCT_RELAXED", -0.05)
            ),
            ma20_tolerance_pct=float(getattr(config, "VASS_BULL_DEBIT_MA20_TOLERANCE_PCT", 0.003)),
        )

    def get_itm_direction_proposal(
        self,
        qqq_current: float,
        transition_ctx: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Optional[OptionDirection], str]:
        """Return ITM_ENGINE sovereign direction proposal from daily trend context."""
        if not self._itm_horizon_engine.enabled():
            return None, "ITM_ENGINE_DISABLED"
        return self._itm_horizon_engine.get_direction_proposal(
            host=self,
            qqq_current=qqq_current,
            transition_ctx=transition_ctx,
        )

    def check_micro_spike_alert(
        self, vix_current: float, vix_5min_ago: float, current_time: str
    ) -> bool:
        """Expose micro spike-alert check without leaking engine internals."""
        return self._micro_regime_engine.check_spike_alert(vix_current, vix_5min_ago, current_time)

    def update_micro_regime_state(
        self,
        vix_current: float,
        vix_open: float,
        qqq_current: float,
        qqq_open: float,
        current_time: str,
        macro_regime_score: float = 50.0,
        vix_level_override: Optional[float] = None,
    ) -> MicroRegimeState:
        """Public wrapper for full micro-regime update."""
        return self._micro_regime_engine.update(
            vix_current=vix_current,
            vix_open=vix_open,
            qqq_current=qqq_current,
            qqq_open=qqq_open,
            current_time=current_time,
            macro_regime_score=macro_regime_score,
            vix_level_override=vix_level_override,
        )

    def set_rejection_margin_cap(self, cap: Optional[float]) -> None:
        """Set/reset adaptive rejection margin cap used by spread sizing."""
        if cap is None:
            self._rejection_margin_cap = None
            return
        self._rejection_margin_cap = max(0.0, float(cap))

    # =========================================================================
    # V2.1.1 SIMPLE INTRADAY FILTERS (FOR SWING MODE)
    # =========================================================================

    def check_swing_filters(
        self,
        direction: OptionDirection,
        spy_gap_pct: float,
        spy_intraday_change_pct: float,
        vix_intraday_change_pct: float,
        current_hour: int,
        current_minute: int,
        is_eod_scan: bool = False,
    ) -> Tuple[bool, str]:
        """Swing-mode entry filters delegated to VASSEntryEngine."""
        return self._vass_entry_engine.check_swing_filters(
            direction=direction,
            spy_gap_pct=spy_gap_pct,
            spy_intraday_change_pct=spy_intraday_change_pct,
            vix_intraday_change_pct=vix_intraday_change_pct,
            current_hour=current_hour,
            current_minute=current_minute,
            enforce_time_window=not bool(is_eod_scan),
        )

    def check_intraday_entry_signal(
        self,
        vix_current: float,
        vix_open: float = 0.0,
        qqq_current: float = 0.0,
        qqq_open: float = 0.0,
        current_hour: int = 0,
        current_minute: int = 0,
        current_time: str = "",
        portfolio_value: float = 0.0,
        raw_portfolio_value: Optional[float] = None,
        best_contract: Optional[OptionContract] = None,
        size_multiplier: float = 1.0,
        macro_regime_score: float = 50.0,
        governor_scale: float = 1.0,
        direction: Optional[OptionDirection] = None,
        forced_entry_strategy: Optional[IntradayStrategy] = None,
        vix_level_override: Optional[float] = None,
        underlying_atr: float = 0.0,
        micro_state: Optional[MicroRegimeState] = None,
        transition_ctx: Optional[Dict[str, Any]] = None,
        # Compatibility aliases retained at wrapper boundary.
        regime_score: Optional[float] = None,
        adx_value: float = 0.0,
        current_price: float = 0.0,
        ma200_value: float = 0.0,
        iv_rank: float = 0.0,
        current_date: str = "",
        strategy_override: Optional[IntradayStrategy] = None,
        current_dte: Optional[int] = None,
    ) -> Optional[TargetWeight]:
        _ = (adx_value, ma200_value, iv_rank, current_dte)
        if qqq_current <= 0:
            qqq_current = float(current_price or 0.0)
        if qqq_open <= 0:
            qqq_open = float(current_price or qqq_current or 0.0)
        if not current_time:
            current_time = current_date
        if raw_portfolio_value is None:
            raw_portfolio_value = portfolio_value
        if forced_entry_strategy is None and strategy_override is not None:
            forced_entry_strategy = strategy_override
        if regime_score is not None:
            macro_regime_score = float(regime_score)
        return check_intraday_entry_signal_impl(
            self,
            vix_current=vix_current,
            vix_open=vix_open,
            qqq_current=qqq_current,
            qqq_open=qqq_open,
            current_hour=current_hour,
            current_minute=current_minute,
            current_time=current_time,
            portfolio_value=portfolio_value,
            raw_portfolio_value=raw_portfolio_value,
            best_contract=best_contract,
            size_multiplier=size_multiplier,
            macro_regime_score=macro_regime_score,
            governor_scale=governor_scale,
            direction=direction,
            forced_entry_strategy=forced_entry_strategy,
            vix_level_override=vix_level_override,
            underlying_atr=underlying_atr,
            micro_state=micro_state,
            transition_ctx=transition_ctx,
        )

    def check_intraday_force_exit(
        self,
        current_hour: int,
        current_minute: int,
        current_price: float,
        ignore_hold_policy: bool = False,
        engine: Optional[str] = None,
        symbol: Optional[str] = None,
    ) -> Optional[TargetWeight]:
        """
        Check for forced exit of intraday position at configured intraday cutoff.

        Intraday mode positions are normally closed by configured force-exit time.
        V10.1 exception: hold-enabled ITM_MOMENTUM positions may carry overnight.

        Args:
            current_hour: Current hour (0-23) Eastern.
            current_minute: Current minute (0-59).
            current_price: Current option price.

        Returns:
            TargetWeight for forced exit, or None.
        """
        symbol_key = self._normalize_symbol_key(symbol)
        position = None
        lane = None
        if symbol_key is not None:
            lane = self._find_intraday_lane_by_symbol(symbol_key)
            if lane is None:
                return None
            for pos in self._intraday_positions.get(lane) or []:
                if (
                    pos is not None
                    and pos.contract is not None
                    and self._symbol_str(pos.contract.symbol) == symbol_key
                ):
                    position = pos
                    break
        else:
            position = self.get_intraday_position(engine=engine)
            lane = (
                engine
                or self._intraday_engine_lane_from_strategy(getattr(position, "entry_strategy", ""))
                if position is not None
                else None
            )

        if position is None:
            return None

        # V2.3.3 FIX #3: Prevent duplicate exit signals while waiting for fill
        if self.has_pending_intraday_exit(symbol=self._symbol_str(position.contract.symbol)):
            return None

        force_hh, force_mm = self._get_intraday_force_exit_hhmm()
        force_exit_time = current_hour > force_hh or (
            current_hour == force_hh and current_minute >= force_mm
        )

        if not force_exit_time:
            return None

        symbol = position.contract.symbol
        symbol_str = self._symbol_str(symbol)
        if self.should_hold_intraday_overnight(position):
            if ignore_hold_policy:
                self.log(
                    f"INTRADAY_FORCE_EXIT_OVERRIDE_HOLD {symbol_str} | "
                    f"Strategy={getattr(position, 'entry_strategy', 'UNKNOWN')}",
                    trades_only=True,
                )
            else:
                if self.algorithm is not None and hasattr(self.algorithm, "Time"):
                    current_date = str(self.algorithm.Time.date())
                else:
                    current_date = "NO_ALGO_TIME"
                if self._intraday_force_exit_hold_skip_log_date.get(symbol_str) != current_date:
                    live_dte = self._get_position_live_dte(position)
                    self.log(
                        f"INTRADAY_FORCE_EXIT_SKIP_HOLD {symbol_str} | "
                        f"Strategy={getattr(position, 'entry_strategy', 'UNKNOWN')} | "
                        f"LiveDTE={live_dte}",
                        trades_only=True,
                    )
                    self._intraday_force_exit_hold_skip_log_date[symbol_str] = current_date
                return None

        entry_price = position.entry_price
        num_contracts = max(1, int(position.num_contracts))

        pnl_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0

        reason = (
            f"INTRADAY_TIME_EXIT_{force_hh:02d}{force_mm:02d} "
            f"{pnl_pct:+.1%} (Price: ${current_price:.2f})"
        )
        self.log(f"INTRADAY_FORCE_EXIT {symbol} | {reason}", trades_only=True)

        # V2.3.3: Set pending exit flag to prevent duplicate signals
        if not self.mark_pending_intraday_exit(symbol_str):
            return None

        return TargetWeight(
            symbol=self._symbol_str(symbol),
            target_weight=0.0,
            source="OPT_INTRADAY",
            urgency=Urgency.IMMEDIATE,
            reason=reason,
            requested_quantity=num_contracts,
        )

    def check_gamma_pin_exit(
        self,
        current_price: float,
        current_dte: int,
        spread_override: Optional[SpreadPosition] = None,
    ) -> Optional[List[TargetWeight]]:
        """
        V2.10 (Pitfall #5): Exit early if price is within buffer zone of short strike.

        Prevents broker auto-liquidation during gamma acceleration near expiration.
        When underlying price pins near the short strike within 2 DTE, gamma explodes
        and the broker may force-liquidate at terrible prices to avoid assignment risk.

        Args:
            current_price: Current underlying (QQQ) price.
            current_dte: Days to expiration for the spread.

        Returns:
            List[TargetWeight] for spread exit if gamma pin detected, else None.
        """
        if not config.GAMMA_PIN_CHECK_ENABLED:
            return None

        # V6.5 FIX: Prevent order spam - only trigger once per position
        if self._gamma_pin_exit_triggered:
            return None

        # Only check spread positions (credit or debit)
        spread = spread_override or self.get_spread_position()
        if spread is None:
            return None

        # Only activate within GAMMA_PIN_EARLY_EXIT_DTE
        if current_dte > config.GAMMA_PIN_EARLY_EXIT_DTE:
            return None

        # Get short strike from spread
        short_strike = spread.short_leg.strike
        distance_pct = abs(current_price - short_strike) / short_strike

        if distance_pct >= config.GAMMA_PIN_BUFFER_PCT:
            return None

        # GAMMA PIN DETECTED - exit early
        # V2.14 Fix #13: Align keyword with AAP protocol (was GAMMA_PIN:)
        self.log(
            f"GAMMA_PIN_EXIT: Early exit triggered | "
            f"Price=${current_price:.2f} Strike=${short_strike:.0f} "
            f"Distance={distance_pct:.2%} < {config.GAMMA_PIN_BUFFER_PCT:.2%} | "
            f"DTE={current_dte}",
            trades_only=True,
        )

        # V6.5 FIX: Mark as triggered to prevent order spam every minute
        self._gamma_pin_exit_triggered = True

        # Get the number of spreads/contracts for the close order
        num_contracts = getattr(spread, "num_spreads", getattr(spread, "num_contracts", 1))

        # Return spread exit signal (same format as spread exit)
        # V6.5 FIX: Added spread_short_leg_quantity to enable combo close order
        return [
            TargetWeight(
                symbol=self._symbol_str(spread.long_leg.symbol),
                target_weight=0.0,
                source="OPT",
                urgency=Urgency.IMMEDIATE,
                reason=f"GAMMA_PIN_BUFFER (price within {distance_pct:.2%} of strike ${short_strike})",
                requested_quantity=num_contracts,
                metadata={
                    "spread_close_short": True,
                    "spread_short_leg_symbol": self._symbol_str(spread.short_leg.symbol),
                    "spread_short_leg_quantity": num_contracts,
                    "spread_key": self._build_spread_key(
                        spread
                    ),  # V6.5 FIX: Required for combo close
                    "exit_type": "GAMMA_PIN",
                    "spread_exit_code": "GAMMA_PIN",
                    "spread_exit_reason": (
                        f"GAMMA_PIN_BUFFER (price within {distance_pct:.2%} of strike ${short_strike})"
                    ),
                },
            )
        ]

    def check_expiring_options_force_exit(
        self,
        current_date: str,
        current_hour: int,
        current_minute: int,
        current_price: float,
        contract_expiry_date: str,
        position: Optional[OptionsPosition] = None,
    ) -> Optional[TargetWeight]:
        return check_expiring_options_force_exit_impl(
            self,
            current_date=current_date,
            current_hour=current_hour,
            current_minute=current_minute,
            current_price=current_price,
            contract_expiry_date=contract_expiry_date,
            position=position,
        )

    def get_micro_regime_state(self) -> MicroRegimeState:
        """Get current Micro Regime Engine state."""
        return self._micro_regime_engine.get_state()

    def get_itm_horizon_state(self) -> Dict[str, Any]:
        """Return ITM horizon state for diagnostics summaries."""
        try:
            return dict(self._itm_horizon_engine.to_dict())
        except Exception:
            return {}

    def get_intraday_direction(
        self,
        vix_current: float,
        vix_open: float,
        qqq_current: float,
        qqq_open: float,
        current_time: str,
        regime_score: float = 50.0,
        vix_level_override: Optional[float] = None,  # V6.2: CBOE VIX for level consistency
    ) -> Optional[OptionDirection]:
        """
        V2.3.14: Get recommended intraday direction from Micro Regime Engine.
        V2.3.16: Added direction conflict resolution (centralized).
        V6.2: Added vix_level_override for consistent VIX level classification.

        Updates the engine state and returns the recommended direction.
        This should be called BEFORE selecting the contract to avoid
        direction mismatch (hardcoded fade vs engine momentum recommendation).

        Direction Conflict Resolution (V2.3.16):
        - If FADE strategy + strong bullish regime (>65) + PUT direction → skip
        - If FADE strategy + strong bearish regime (<40) + CALL direction → skip
        This prevents counter-trend trades in strongly trending markets.

        Args:
            vix_current: Current VIX value.
            vix_open: VIX at market open.
            qqq_current: Current QQQ price.
            qqq_open: QQQ at market open.
            current_time: Timestamp string.
            regime_score: Macro regime score (0-100) for direction conflict check.
            vix_level_override: V6.2 - CBOE VIX for level classification (ensures consistency
                with scheduled _update_micro_regime calls).

        Returns:
            Recommended OptionDirection (CALL or PUT), or None if NO_TRADE or conflict.
        """
        # Update Micro Regime Engine (V2.5: pass macro_regime_score for Grind-Up Override)
        # V6.2: Pass vix_level_override for consistent level classification
        state = self._micro_regime_engine.update(
            vix_current=vix_current,
            vix_open=vix_open,
            qqq_current=qqq_current,
            qqq_open=qqq_open,
            current_time=current_time,
            macro_regime_score=regime_score,
            vix_level_override=vix_level_override,  # V6.2: Pass through
        )

        direction = state.recommended_direction

        # If no direction recommended, return None
        if direction is None:
            return None

        # V2.3.16: Direction conflict resolution for FADE strategies
        # Skip intraday FADE when macro regime strongly disagrees with direction
        # This prevents counter-trend trades in strongly trending markets
        if state.recommended_strategy in (
            IntradayStrategy.MICRO_DEBIT_FADE,
            IntradayStrategy.MICRO_OTM_MOMENTUM,
            IntradayStrategy.DEBIT_FADE,
        ):
            # Strong bullish regime + FADE PUT (fading rally) = conflict
            if (
                regime_score > config.DIRECTION_CONFLICT_BULLISH_THRESHOLD
                and direction == OptionDirection.PUT
            ):
                self.log(
                    f"DIRECTION_CONFLICT: Skipping FADE PUT - regime {regime_score:.1f} "
                    f"> {config.DIRECTION_CONFLICT_BULLISH_THRESHOLD} (strong bullish)"
                )
                return None

            # Strong bearish regime + FADE CALL (fading dip) = conflict
            if (
                regime_score < config.DIRECTION_CONFLICT_BEARISH_THRESHOLD
                and direction == OptionDirection.CALL
            ):
                self.log(
                    f"DIRECTION_CONFLICT: Skipping FADE CALL - regime {regime_score:.1f} "
                    f"< {config.DIRECTION_CONFLICT_BEARISH_THRESHOLD} (strong bearish)"
                )
                return None

        return direction

    def get_last_intraday_strategy(self) -> IntradayStrategy:
        """
        V2.3.16: Get the last recommended intraday strategy.

        Returns:
            IntradayStrategy enum (MICRO_DEBIT_FADE, MICRO_OTM_MOMENTUM, ITM_MOMENTUM, NO_TRADE, etc.)
        """
        return self._micro_regime_engine.get_state().recommended_strategy

    def update_market_open_data(
        self, vix_open: float, spy_open: float, spy_prior_close: float
    ) -> None:
        """
        Update market open data for simple filters.

        Should be called at market open (9:30-9:33 AM).

        Args:
            vix_open: VIX value at open.
            spy_open: SPY price at open.
            spy_prior_close: SPY prior close price.
        """
        self._vix_at_open = vix_open
        self._spy_at_open = spy_open

        if spy_prior_close > 0:
            self._spy_gap_pct = (spy_open - spy_prior_close) / spy_prior_close * 100
        else:
            self._spy_gap_pct = 0.0

        self.log(
            f"Market open data: VIX={vix_open:.1f} | "
            f"SPY={spy_open:.2f} | Gap={self._spy_gap_pct:+.2f}%"
        )

    # =========================================================================
    # POSITION MANAGEMENT
    # =========================================================================

    def register_entry(
        self,
        fill_price: float,
        entry_time: str,
        current_date: str,
        contract: Optional[OptionContract] = None,
        force_intraday: bool = False,
        symbol: Optional[str] = None,
        order_tag: Optional[str] = None,
    ) -> Optional[OptionsPosition]:
        """
        Register a new options position after fill.

        Args:
            fill_price: Actual fill price.
            entry_time: Entry timestamp string.
            current_date: Current date string.
            contract: Option contract (uses pending if not provided).
            force_intraday: If True, classify fill as intraday even when pending
                flag was cleared by a cancel/fallback race.
            order_tag: Optional broker order tag used for recovery strategy inference.

        Returns:
            Created OptionsPosition, or None if no pending contract exists.
        """
        return register_entry_impl(
            self,
            fill_price=fill_price,
            entry_time=entry_time,
            current_date=current_date,
            contract=contract,
            force_intraday=force_intraday,
            symbol=symbol,
            order_tag=order_tag,
        )

    def remove_position(self, symbol: Optional[str] = None) -> Optional[OptionsPosition]:
        """
        Remove the current swing single-leg position after exit.

        Args:
            symbol: Optional symbol guard. When provided, removal only occurs
                if it matches the tracked swing position symbol.

        Returns:
            Removed position, or None if no matching position existed.
        """
        return remove_position_impl(self, symbol=symbol)

    def remove_intraday_position(
        self, symbol: Optional[str] = None, engine: Optional[str] = None
    ) -> Optional[OptionsPosition]:
        """
        V2.3.2: Remove the current intraday position after exit.

        Returns:
            Removed intraday position, or None if no position existed.
        """
        return remove_intraday_position_impl(self, symbol=symbol, engine=engine)

    # =========================================================================
    # V2.3 SPREAD POSITION MANAGEMENT
    # =========================================================================

    def register_spread_entry(
        self,
        long_leg_fill_price: float,
        short_leg_fill_price: float,
        entry_time: str,
        current_date: str,
        regime_score: float,
    ) -> Optional[SpreadPosition]:
        """
        V2.3: Register a new spread position after both legs fill.

        Args:
            long_leg_fill_price: Actual fill price for long leg.
            short_leg_fill_price: Actual fill price for short leg.
            entry_time: Entry timestamp string.
            current_date: Current date string.
            regime_score: Regime score at entry.

        Returns:
            Created SpreadPosition, or None if no pending spread exists.
        """
        return register_spread_entry_impl(
            self,
            long_leg_fill_price=long_leg_fill_price,
            short_leg_fill_price=short_leg_fill_price,
            entry_time=entry_time,
            current_date=current_date,
            regime_score=regime_score,
        )

    # =========================================================================
    # V2.20: REJECTION RECOVERY METHODS
    # =========================================================================

    def cancel_pending_swing_entry(self) -> None:
        """
        V2.20: Clear pending swing entry state after broker rejection.

        Resets all swing single-leg pending fields and allows retry.
        No counter decrement needed — swing counter is only incremented
        in register_entry() on fill, not on signal generation.
        Called by main._handle_order_rejection().
        """
        self._pending_contract = None
        self._pending_entry_score = None
        self._pending_num_contracts = None
        self._pending_stop_pct = None
        self._pending_stop_price = None
        self._pending_target_price = None
        self._pending_entry_strategy = None
        self._entry_attempted_today = False
        self.log(
            "OPT_SWING_RECOVERY: Pending swing entry cancelled | Retry allowed",
            trades_only=True,
        )

    def cancel_pending_spread_entry(self) -> None:
        """
        V2.20: Clear pending spread entry state after broker rejection.

        Resets all spread pending fields and allows retry. No counter
        decrement needed — spread counter is only incremented in
        register_spread_entry() on fill, not on signal generation.
        Caller must also call portfolio_router.clear_all_spread_margins()
        to free ghost margin reservations.
        Called by main._handle_order_rejection().
        """
        self._pending_spread_long_leg = None
        self._pending_spread_short_leg = None
        self._pending_spread_type = None
        self._pending_net_debit = None
        self._pending_max_profit = None
        self._pending_spread_width = None
        self._pending_spread_entry_vix = None
        self._pending_spread_entry_since = None
        self._pending_num_contracts = None
        self._pending_entry_score = None
        self.log(
            "OPT_MACRO_RECOVERY: Pending spread entry cancelled | Retry allowed",
            trades_only=True,
        )

    def _clear_stale_pending_spread_entry_if_orphaned(self) -> None:
        """Clear stale pending spread lock when no matching open leg orders exist."""
        clear_stale_pending_spread_entry_if_orphaned_impl(self)

    def has_pending_spread_entry(self) -> bool:
        """True when both pending spread legs are populated."""
        self._clear_stale_pending_spread_entry_if_orphaned()
        return (
            self._pending_spread_long_leg is not None and self._pending_spread_short_leg is not None
        )

    def get_pending_spread_legs(self) -> Tuple[Optional[OptionContract], Optional[OptionContract]]:
        """Expose pending spread legs without direct private-field access."""
        return self._pending_spread_long_leg, self._pending_spread_short_leg

    def get_pending_spread_tracker_seed(self) -> Optional[dict]:
        """Return spread tracker seed payload derived from pending spread state."""
        if not self.has_pending_spread_entry():
            return None
        return {
            "long_leg_symbol": self._symbol_str(self._pending_spread_long_leg.symbol),
            "short_leg_symbol": self._symbol_str(self._pending_spread_short_leg.symbol),
            "expected_quantity": int(self._pending_num_contracts or 1),
            "spread_type": self._pending_spread_type,
        }

    def clear_pending_spread_state_hard(self) -> None:
        """
        Hard reset for stale spread state cleanup.

        Mirrors legacy main.py cleanup fields to preserve behavior.
        """
        self._pending_spread_long_leg = None
        self._pending_spread_short_leg = None
        self._pending_spread_type = None
        self._pending_net_debit = None
        self._pending_max_profit = None
        self._pending_spread_width = None
        self._pending_spread_entry_vix = None
        self._pending_spread_entry_since = None
        self._pending_num_contracts = None
        self._pending_entry_score = None
        self._pending_stop_pct = None
        self._pending_stop_price = None
        self._pending_target_price = None

    def _clear_stale_pending_intraday_entry_if_orphaned(self) -> None:
        """
        Clear stale pending intraday entry locks.

        Prevents long-lived E_INTRADAY_PENDING_ENTRY lock after missed/implicit
        broker cancel events while preserving normal in-flight order behavior.
        """
        clear_stale_pending_intraday_entry_if_orphaned_impl(self)

    def cancel_pending_intraday_entry(
        self, engine: Optional[str] = None, symbol: Optional[str] = None
    ) -> Optional[str]:
        """
        V2.20: Clear pending intraday entry state after broker rejection.

        When symbol is provided, clears only that pending symbol (optionally scoped by lane).
        When engine is provided (MICRO/ITM), only clears matching lane lock.

        Returns:
            Cleared lane name when identifiable, else None.
        """
        return cancel_pending_intraday_entry_impl(self, engine=engine, symbol=symbol)

    def has_pending_intraday_entry(self, engine: Optional[str] = None) -> bool:
        """True when an intraday entry is currently pending."""
        return has_pending_intraday_entry_impl(self, engine=engine)

    def get_pending_intraday_entry_lane(self, symbol: Optional[str] = None) -> Optional[str]:
        """Best-effort lane lookup for a pending intraday entry."""
        return get_pending_intraday_entry_lane_impl(self, symbol=symbol)

    def get_pending_entry_contract_symbol(self) -> str:
        """Best-effort symbol for current pending single-leg entry contract."""
        return get_pending_entry_contract_symbol_impl(self)

    def get_intraday_partial_fill_oco_seed(
        self, symbol: str, fill_price: float
    ) -> Optional[Dict[str, Any]]:
        """Return OCO seed for intraday/pending partial entry fill, if applicable."""
        return get_intraday_partial_fill_oco_seed_impl(self, symbol=symbol, fill_price=fill_price)

    def get_partial_fill_oco_seed(
        self, symbol: str, fill_price: float, order_tag: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Return OCO seed for partial fills across intraday and swing single-legs."""
        return get_partial_fill_oco_seed_impl(
            self,
            symbol=symbol,
            fill_price=fill_price,
            order_tag=order_tag,
        )

    def get_pending_intraday_partial_oco_seed(
        self, symbol: str, fill_price: float
    ) -> Optional[Dict[str, Any]]:
        """
        Build temporary OCO pricing for a pending intraday entry partial fill.

        Used when an entry order partially fills before full fill registration.
        """
        return get_pending_intraday_partial_oco_seed_impl(
            self,
            symbol=symbol,
            fill_price=fill_price,
        )

    def _normalize_symbol_key(self, symbol: Optional[str]) -> Optional[str]:
        return normalize_symbol_key_impl(self, symbol=symbol)

    def _sync_pending_intraday_exit_flags(self) -> None:
        sync_pending_intraday_exit_flags_impl(self)

    def has_pending_swing_entry(self) -> bool:
        """True when a single-leg swing entry is pending (not intraday)."""
        return self._pending_contract is not None and not self._pending_intraday_entry

    def has_pending_intraday_exit(
        self, engine: Optional[str] = None, symbol: Optional[str] = None
    ) -> bool:
        """True when an intraday close signal has already been emitted and is in-flight."""
        return has_pending_intraday_exit_impl(self, engine=engine, symbol=symbol)

    def mark_pending_intraday_exit(self, symbol: Optional[str] = None) -> bool:
        """
        Mark intraday close as pending to block duplicate software/force exits.

        Args:
            symbol: Optional symbol guard. Symbol-scoped locks are preferred for
                multi-position lanes.

        Returns:
            True when lock was set, else False.
        """
        return mark_pending_intraday_exit_impl(self, symbol=symbol)

    def cancel_pending_intraday_exit(self, symbol: Optional[str] = None) -> bool:
        """
        Clear pending intraday exit lock after a rejected/canceled close order.

        Args:
            symbol: Optional symbol guard. When provided, clears symbol-scoped lock.

        Returns:
            True when lock was cleared, else False.
        """
        return cancel_pending_intraday_exit_impl(self, symbol=symbol)

    def remove_spread_position(self, symbol: Optional[str] = None) -> Optional[SpreadPosition]:
        """
        V2.3: Remove the current spread position after exit.
        V2.6 Bug #16: Records exit time for post-trade margin cooldown.

        Returns:
            Removed spread position, or None if no spread existed.
        """
        return remove_spread_position_impl(self, symbol=symbol)

    def has_spread_position(self) -> bool:
        """V2.3: Check if a spread position exists."""
        return self.get_open_spread_count() > 0

    def get_spread_position(self) -> Optional[SpreadPosition]:
        """Get primary spread position (legacy compatibility)."""
        spreads = self.get_spread_positions()
        return spreads[0] if spreads else None

    # =========================================================================
    # V2.27 WIN RATE GATE
    # =========================================================================

    def record_spread_result(self, is_win: bool) -> None:
        """
        V2.27: Record a spread trade result for win rate tracking.

        Args:
            is_win: True if the spread was profitable, False if a loss.
        """
        if (
            self._vass_entry_engine_enabled
            and self.algorithm is not None
            and hasattr(self.algorithm, "Time")
        ):
            pause_until = self._vass_entry_engine.record_spread_result(
                is_win=is_win,
                now_dt=self.algorithm.Time,
            )
            if pause_until:
                self.log(
                    "VASS_LOSS_BREAKER_PAUSE | " f"PauseUntil={pause_until}",
                    trades_only=True,
                )

        if not config.WIN_RATE_GATE_ENABLED:
            return

        if self._win_rate_shutoff:
            if (
                self._win_rate_shutoff_date is None
                and self.algorithm is not None
                and hasattr(self.algorithm, "Time")
            ):
                self._win_rate_shutoff_date = str(self.algorithm.Time.date())

            # Time-based auto-recovery: avoid prolonged degraded sizing lock.
            max_days = int(getattr(config, "WIN_RATE_GATE_MAX_SHUTOFF_DAYS", 30))
            if (
                max_days > 0
                and self._win_rate_shutoff_date
                and self.algorithm is not None
                and hasattr(self.algorithm, "Time")
            ):
                try:
                    shutoff_dt = datetime.strptime(self._win_rate_shutoff_date, "%Y-%m-%d").date()
                    days_elapsed = (self.algorithm.Time.date() - shutoff_dt).days
                    if days_elapsed >= max_days:
                        self._win_rate_shutoff = False
                        self._win_rate_shutoff_date = None
                        self._paper_track_history = []
                        self._spread_result_history = []
                        self.log(
                            f"WIN_RATE_GATE: AUTO_RESET | {days_elapsed} days in shutoff",
                            trades_only=True,
                        )
                        return
                except Exception:
                    pass

            # During shutoff, record to paper history instead
            self._paper_track_history.append(is_win)
            if len(self._paper_track_history) > config.WIN_RATE_LOOKBACK:
                self._paper_track_history = self._paper_track_history[-config.WIN_RATE_LOOKBACK :]

            # Check if paper win rate recovers enough to resume
            if len(self._paper_track_history) >= config.WIN_RATE_LOOKBACK:
                paper_wr = sum(self._paper_track_history) / len(self._paper_track_history)
                if paper_wr >= config.WIN_RATE_RESTART_THRESHOLD:
                    self._win_rate_shutoff = False
                    self._win_rate_shutoff_date = None
                    self._paper_track_history = []
                    self.log(
                        f"WIN_RATE_GATE: RESUMED | PaperWR={paper_wr:.0%} >= "
                        f"{config.WIN_RATE_RESTART_THRESHOLD:.0%} | Real trading restored",
                        trades_only=True,
                    )
        else:
            # Normal mode: record to real history
            self._spread_result_history.append(is_win)
            if len(self._spread_result_history) > config.WIN_RATE_LOOKBACK:
                self._spread_result_history = self._spread_result_history[
                    -config.WIN_RATE_LOOKBACK :
                ]

            # Check if we should enter shutoff
            if len(self._spread_result_history) >= config.WIN_RATE_LOOKBACK:
                wr = sum(self._spread_result_history) / len(self._spread_result_history)
                if wr < config.WIN_RATE_SHUTOFF_THRESHOLD:
                    self._win_rate_shutoff = True
                    if self.algorithm is not None and hasattr(self.algorithm, "Time"):
                        self._win_rate_shutoff_date = str(self.algorithm.Time.date())
                    else:
                        self._win_rate_shutoff_date = None
                    self.log(
                        f"WIN_RATE_GATE: SHUTOFF | WinRate={wr:.0%} < "
                        f"{config.WIN_RATE_SHUTOFF_THRESHOLD:.0%} | "
                        f"LastN={self._spread_result_history} | Paper tracking active",
                        trades_only=True,
                    )

        result_str = "WIN" if is_win else "LOSS"
        self.log(
            f"WIN_RATE_GATE: Recorded {result_str} | "
            f"History={len(self._spread_result_history)} | "
            f"Shutoff={self._win_rate_shutoff}",
            trades_only=True,
        )

    def get_win_rate_scale(self) -> float:
        """
        V2.27: Get position sizing scale based on rolling win rate.

        Returns:
            1.0 = full size, 0.75 = reduced, 0.50 = minimum, 0.0 = shutoff.
        """
        if not config.WIN_RATE_GATE_ENABLED:
            return 1.0

        if self._win_rate_shutoff:
            return 0.0

        if len(self._spread_result_history) < config.WIN_RATE_LOOKBACK:
            return 1.0  # Not enough data, full size

        win_rate = (
            sum(self._spread_result_history[-config.WIN_RATE_LOOKBACK :]) / config.WIN_RATE_LOOKBACK
        )

        if win_rate >= config.WIN_RATE_FULL_THRESHOLD:
            return 1.0
        elif win_rate >= config.WIN_RATE_REDUCED_THRESHOLD:
            return config.WIN_RATE_SIZING_REDUCED  # 0.75
        elif win_rate >= config.WIN_RATE_MINIMUM_THRESHOLD:
            return config.WIN_RATE_SIZING_MINIMUM  # 0.50
        else:
            return 0.0  # Should trigger shutoff via record_spread_result

    def clear_spread_position(self) -> None:
        """
        V2.12 Fix #5: Force clear spread position tracking.

        Used by margin circuit breaker to reset tracking after forced liquidation.
        Does NOT place orders - just clears internal state.
        """
        spreads = self.get_spread_positions()
        if spreads:
            self.log(
                f"SPREAD: FORCE_CLEARED | Count={len(spreads)} | Margin CB liquidation",
                trades_only=True,
            )
            self._spread_neutrality_warn_by_key = {}
            self._spread_hold_guard_logged.clear()
            self._spread_positions = []
            self._spread_position = None
            self._last_spread_exit_time = None
            max_attempts = int(getattr(config, "SPREAD_MAX_ATTEMPTS_PER_KEY_PER_DAY", 3))
            # Preserve prior behavior: block any same-day spread re-entry after CB liquidation.
            self._spread_attempts_today_by_key = {
                "DEBIT_CALL": max_attempts,
                "DEBIT_PUT": max_attempts,
                f"CREDIT_{SpreadStrategy.BULL_PUT_CREDIT.value}": max_attempts,
                f"CREDIT_{SpreadStrategy.BEAR_CALL_CREDIT.value}": max_attempts,
            }

    def reset_spread_closing_lock(self) -> None:
        """
        V2.17: Clear the is_closing lock if all close attempts failed.

        Called by PortfolioRouter when both combo order retries and
        sequential fallback fail. This allows the spread to be retried
        on subsequent iterations instead of staying permanently locked.

        Does NOT clear the spread position - just resets the lock flag.
        """
        reset_count = 0
        for spread in self.get_spread_positions():
            if spread.is_closing:
                spread.is_closing = False
                reset_count += 1
        if reset_count > 0:
            self.log(
                f"SPREAD: LOCK_RESET | Count={reset_count} | Will retry on next check",
                trades_only=True,
            )

    def has_intraday_position(self, engine: Optional[str] = None) -> bool:
        """V2.3.2: Check if an intraday position exists (optionally by engine lane)."""
        if engine is None:
            return any(len(v or []) > 0 for v in self._intraday_positions.values())
        eng = str(engine).upper()
        return len(self._intraday_positions.get(eng) or []) > 0

    def get_intraday_position(self, engine: Optional[str] = None) -> Optional[OptionsPosition]:
        """V2.3.2: Get current intraday position (optionally by engine lane)."""
        if engine is not None:
            return self._get_intraday_lane_position(str(engine).upper())
        # Deterministic default for legacy callers.
        return self._get_intraday_lane_position("ITM") or self._get_intraday_lane_position("MICRO")

    def get_intraday_position_engine(self) -> Optional[str]:
        """Return default ownership lane for legacy callers."""
        if len(self._intraday_positions.get("ITM") or []) > 0:
            return "ITM"
        if len(self._intraday_positions.get("MICRO") or []) > 0:
            return "MICRO"
        return None

    def has_position(self) -> bool:
        """Check if any position exists (single-leg, spread, or intraday)."""
        return (
            self._position is not None or self.has_spread_position() or self.has_intraday_position()
        )

    def get_position(self) -> Optional[OptionsPosition]:
        """Get current position."""
        return self._position

    def clear_all_positions(self) -> None:
        """
        V2.5 PART 19 FIX: Clear ALL position tracking state.

        This is called by kill switch to prevent "zombie state" where
        internal position trackers remain set after broker positions are closed.

        The bug: If a spread is entered and then kill switch liquidates it,
        the internal _spread_position variable stays set, blocking ALL future
        spread entries for months.

        Solution: Use stateless tracking - clear all internal state when
        positions are forcibly closed by kill switch.
        """
        clear_all_positions_impl(self)

    # =========================================================================
    # GREEKS MONITORING (V2.1 RSK-2)
    # =========================================================================

    def calculate_position_greeks(self) -> Optional[GreeksSnapshot]:
        return calculate_position_greeks_impl(self)

    def update_position_greeks(
        self,
        delta: Optional[float] = None,
        gamma: Optional[float] = None,
        vega: Optional[float] = None,
        theta: Optional[float] = None,
        # Compatibility aliases retained at wrapper boundary.
        current_price: Optional[float] = None,
        current_delta: Optional[float] = None,
        current_gamma: Optional[float] = None,
        current_vega: Optional[float] = None,
        current_theta: Optional[float] = None,
    ) -> None:
        _ = current_price
        resolved_delta = delta if delta is not None else current_delta
        resolved_gamma = gamma if gamma is not None else current_gamma
        resolved_vega = vega if vega is not None else current_vega
        resolved_theta = theta if theta is not None else current_theta
        if resolved_delta is None or resolved_gamma is None or resolved_vega is None:
            return None
        if resolved_theta is None:
            resolved_theta = 0.0
        return update_position_greeks_impl(
            self,
            delta=float(resolved_delta),
            gamma=float(resolved_gamma),
            vega=float(resolved_vega),
            theta=float(resolved_theta),
        )

    def check_greeks_breach(
        self,
        risk_engine: Optional["RiskEngine"] = None,
        current_price: Optional[float] = None,
    ) -> Tuple[bool, List[str]]:
        _ = current_price
        resolved_risk_engine = risk_engine
        if resolved_risk_engine is None:
            resolved_risk_engine = getattr(self.algorithm, "risk_engine", None)
        elif not hasattr(resolved_risk_engine, "check_cb_greeks_breach"):
            resolved_risk_engine = getattr(self.algorithm, "risk_engine", None)
        if resolved_risk_engine is None:
            return False, []
        return check_greeks_breach_impl(self, risk_engine=resolved_risk_engine)

    def get_state_for_persistence(self) -> Dict[str, Any]:
        return get_state_for_persistence_impl(self)

    def restore_state(self, state: Dict[str, Any]) -> None:
        return restore_state_impl(self, state=state)

    def reset(self) -> None:
        return reset_options_engine_state_impl(self)

    def reset_daily(self, current_date: str) -> None:
        return reset_options_engine_daily_state_impl(self, current_date=current_date)

    def _increment_trade_counter(
        self,
        mode: OptionsMode,
        direction: Optional[OptionDirection] = None,
        strategy: Optional[str] = None,
    ) -> None:
        """
        V2.9: Increment trade counters when a trade is executed.

        Called from register_spread_position() and register_entry() after fills.

        Args:
            mode: The trading mode (SWING or INTRADAY).
        """
        # Increment new counters (V2.9)
        self._total_options_trades_today += 1

        # Backward compatibility: Also increment old counter used by state persistence
        self._trades_today += 1

        if mode == OptionsMode.INTRADAY:
            self._intraday_trades_today += 1
            strat = self._canonical_intraday_strategy_name(strategy)
            if self._is_itm_momentum_strategy_name(strat):
                self._intraday_itm_trades_today += 1
            else:
                self._intraday_micro_trades_today += 1
            if direction == OptionDirection.CALL:
                self._intraday_call_trades_today += 1
            elif direction == OptionDirection.PUT:
                self._intraday_put_trades_today += 1
            self.log(
                f"TRADE_COUNTER: Intraday={self._intraday_trades_today}/{config.INTRADAY_MAX_TRADES_PER_DAY} | "
                f"ITM={self._intraday_itm_trades_today}/{getattr(config, 'ITM_MAX_TRADES_PER_DAY', 999)} | "
                f"MICRO={self._intraday_micro_trades_today}/{getattr(config, 'MICRO_MAX_TRADES_PER_DAY', 999)} | "
                f"CALL={self._intraday_call_trades_today}/{getattr(config, 'INTRADAY_MAX_TRADES_PER_DIRECTION_PER_DAY', 999)} | "
                f"PUT={self._intraday_put_trades_today}/{getattr(config, 'INTRADAY_MAX_TRADES_PER_DIRECTION_PER_DAY', 999)} | "
                f"Total={self._total_options_trades_today}/{config.MAX_OPTIONS_TRADES_PER_DAY}"
            )
        else:
            self._swing_trades_today += 1
            self.log(
                f"TRADE_COUNTER: Swing={self._swing_trades_today}/{config.MAX_SWING_TRADES_PER_DAY} | "
                f"Total={self._total_options_trades_today}/{config.MAX_OPTIONS_TRADES_PER_DAY}"
            )

    def _can_trade_options(
        self, mode: OptionsMode, direction: Optional[OptionDirection] = None
    ) -> bool:
        """
        V2.9: Check if trading is allowed based on daily limits.

        Prevents over-trading when VIX flickers around strategy thresholds.

        Args:
            mode: The trading mode to check.

        Returns:
            True if trading is allowed, False if limits exceeded.
        """

        def reject(reason: str, detail: str) -> bool:
            self.set_last_trade_limit_failure(reason, detail)
            return False

        # Reset previous limit failure context for this check.
        self.set_last_trade_limit_failure(None, None)

        # Check global daily options trade limit (distinct from slot caps).
        if self._total_options_trades_today >= config.MAX_OPTIONS_TRADES_PER_DAY:
            detail = (
                f"Global limit reached | "
                f"{self._total_options_trades_today}/{config.MAX_OPTIONS_TRADES_PER_DAY}"
            )
            self.log(f"TRADE_LIMIT: {detail}")
            return reject("R_TRADE_DAILY_TOTAL_MAX", detail)

        reserve_checks_active = True
        if self.algorithm is not None:
            try:
                release_h = int(getattr(config, "OPTIONS_RESERVE_RELEASE_HOUR", 13))
                release_m = int(getattr(config, "OPTIONS_RESERVE_RELEASE_MINUTE", 30))
                release_minute_of_day = release_h * 60 + release_m
                now_minute_of_day = self.algorithm.Time.hour * 60 + self.algorithm.Time.minute
                reserve_checks_active = now_minute_of_day < release_minute_of_day
            except Exception:
                reserve_checks_active = True

        # Check mode-specific limits
        if mode == OptionsMode.INTRADAY:
            if bool(getattr(config, "INTRADAY_ENFORCE_SHARED_DAILY_CAP", False)):
                if self._intraday_trades_today >= config.INTRADAY_MAX_TRADES_PER_DAY:
                    detail = (
                        f"Intraday limit reached | "
                        f"{self._intraday_trades_today}/{config.INTRADAY_MAX_TRADES_PER_DAY}"
                    )
                    self.log(f"TRADE_LIMIT: {detail}")
                    return reject("R_SLOT_INTRADAY_MAX", detail)
            per_direction_cap = int(getattr(config, "INTRADAY_MAX_TRADES_PER_DIRECTION_PER_DAY", 0))
            if (
                bool(getattr(config, "INTRADAY_ENFORCE_SHARED_DIRECTION_CAP", False))
                and per_direction_cap > 0
                and direction is not None
            ):
                dir_count = (
                    self._intraday_call_trades_today
                    if direction == OptionDirection.CALL
                    else self._intraday_put_trades_today
                )
                if dir_count >= per_direction_cap:
                    detail = (
                        f"Intraday direction cap reached | "
                        f"Dir={direction.value} {dir_count}/{per_direction_cap}"
                    )
                    self.log(f"TRADE_LIMIT: {detail}")
                    return reject("R_SLOT_DIRECTION_MAX", detail)
            if reserve_checks_active and getattr(
                config, "OPTIONS_RESERVE_SWING_DAILY_SLOTS_ENABLED", False
            ):
                reserve = max(int(getattr(config, "OPTIONS_MIN_SWING_SLOTS_PER_DAY", 0)), 0)
                if reserve > 0:
                    intraday_cap = max(config.MAX_OPTIONS_TRADES_PER_DAY - reserve, 0)
                    if self._intraday_trades_today >= intraday_cap:
                        detail = (
                            f"Intraday reserve guard | "
                            f"Intraday={self._intraday_trades_today} >= Cap={intraday_cap} | "
                            f"ReservedSwingSlots={reserve}"
                        )
                        self.log(f"TRADE_LIMIT: {detail}")
                        return reject("R_SLOT_INTRADAY_RESERVE", detail)
        else:  # SWING
            if self._swing_trades_today >= config.MAX_SWING_TRADES_PER_DAY:
                detail = (
                    f"Swing limit reached | "
                    f"{self._swing_trades_today}/{config.MAX_SWING_TRADES_PER_DAY}"
                )
                self.log(f"TRADE_LIMIT: {detail}")
                return reject("R_SLOT_SWING_MAX", detail)
            if reserve_checks_active and getattr(
                config, "OPTIONS_RESERVE_INTRADAY_DAILY_SLOTS_ENABLED", False
            ):
                reserve = max(int(getattr(config, "OPTIONS_MIN_INTRADAY_SLOTS_PER_DAY", 0)), 0)
                if reserve > 0:
                    swing_cap = max(config.MAX_OPTIONS_TRADES_PER_DAY - reserve, 0)
                    if self._swing_trades_today >= swing_cap:
                        detail = (
                            f"Swing reserve guard | "
                            f"Swing={self._swing_trades_today} >= Cap={swing_cap} | "
                            f"ReservedIntradaySlots={reserve}"
                        )
                        self.log(f"TRADE_LIMIT: {detail}")
                        return reject("R_SLOT_SWING_RESERVE", detail)

        return True

    def _add_trading_days_to_date(self, trade_date: datetime.date, days: int) -> datetime.date:
        """Add trading days (Mon-Fri) to a date."""
        remaining = max(0, int(days))
        cursor = trade_date
        while remaining > 0:
            cursor = cursor + timedelta(days=1)
            if cursor.weekday() < 5:
                remaining -= 1
        return cursor
