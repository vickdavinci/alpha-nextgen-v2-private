# region imports
# Type hints
import csv
import gzip
import io
import json
import re
from base64 import b64encode
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from AlgorithmImports import *

# Configuration
import config
from engines.core.capital_engine import CapitalEngine, CapitalState
from engines.core.cold_start_engine import ColdStartEngine

# Core Engines
from engines.core.regime_engine import RegimeEngine, RegimeState
from engines.core.risk_engine import (
    GreeksSnapshot,
    KSTier,
    RiskCheckResult,
    RiskEngine,
    SafeguardType,
)
from engines.core.startup_gate import StartupGate
from engines.core.trend_engine import TrendEngine
from engines.satellite.hedge_engine import HedgeEngine

# Satellite Engines
from engines.satellite.mean_reversion_engine import MeanReversionEngine
from engines.satellite.options_engine import (
    ExitOrderTracker,
    OptionContract,
    OptionsEngine,
    SpreadFillTracker,
    SpreadStrategy,
    is_expiration_firewall_day,
)
from engines.satellite.premarket_vix_actions import apply_premarket_vix_actions

# V6.4: OptionDirection now imported only from models.enums (fixes P0 duplicate enum bug)
from execution.execution_engine import ExecutionEngine

# OCO Manager for Options exits
from execution.oco_manager import OCOManager
from main_options_mixin import MainOptionsMixin
from main_orders_mixin import MainOrdersMixin

# Models
from models.enums import IntradayStrategy, OptionDirection, RegimeLevel, Urgency
from models.target_weight import TargetWeight

# Infrastructure
from persistence.state_manager import StateKeys, StateManager

# Portfolio & Execution
from portfolio.portfolio_router import PortfolioRouter
from scheduling.daily_scheduler import DailyScheduler
from utils.daily_summary_logger import log_daily_summary

# V6.12: Monthly P&L Tracking
from utils.monthly_pnl_tracker import PNL_TRACKER_KEY, MonthlyPnLTracker

# Telemetry marker retained for minified validation.
# INTRADAY_DTE_ROUTING

# endregion


class AlphaNextGen(QCAlgorithm):
    """Main QuantConnect algorithm entrypoint for Alpha NextGen."""

    # Bind extracted helpers at class definition time to avoid QC managed-class
    # multiple-inheritance/runtime setattr limitations.
    _normalize_intraday_lane = MainOptionsMixin._normalize_intraday_lane
    _set_intraday_lane_cooldown = MainOptionsMixin._set_intraday_lane_cooldown
    _get_intraday_lane_cooldown_until = MainOptionsMixin._get_intraday_lane_cooldown_until
    _is_intraday_lane_cooldown_active = MainOptionsMixin._is_intraday_lane_cooldown_active
    _get_intraday_retry_state = MainOptionsMixin._get_intraday_retry_state
    _clear_intraday_retry = MainOptionsMixin._clear_intraday_retry
    _queue_intraday_retry = MainOptionsMixin._queue_intraday_retry
    _consume_intraday_retry = MainOptionsMixin._consume_intraday_retry
    _select_intraday_option_contract = MainOptionsMixin._select_intraday_option_contract
    _scan_options_signals = MainOptionsMixin._scan_options_signals
    _check_spread_exit = MainOptionsMixin._check_spread_exit
    OnOrderEvent = MainOrdersMixin.OnOrderEvent
    _on_fill = MainOrdersMixin._on_fill
    _cancel_residual_option_orders = MainOrdersMixin._cancel_residual_option_orders

    @staticmethod
    def _safe_objectstore_key_component(raw: Any, default: str = "default") -> str:
        text = str(raw or "").strip()
        if not text:
            return default
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", text)
        safe = re.sub(r"_+", "_", safe).strip("_")
        return safe or default

    def _build_regime_observability_key(self) -> str:
        return self._build_observability_key(
            prefix_config_name="REGIME_OBSERVABILITY_OBJECTSTORE_KEY_PREFIX",
            default_prefix="regime_observability",
        )

    def _build_observability_key(self, prefix_config_name: str, default_prefix: str) -> str:
        prefix = self._safe_objectstore_key_component(
            getattr(config, prefix_config_name, default_prefix),
            default=default_prefix,
        )
        run_suffix_raw = self._run_label or f"year_{self._backtest_year}"
        run_suffix = self._safe_objectstore_key_component(run_suffix_raw, default="run")
        year = self._safe_objectstore_key_component(self._backtest_year, default="year")
        # LocalObjectStore does not support path-style keys ("/"), so keep this flat.
        return f"{prefix}__{run_suffix}_{year}.csv"

    def _build_regime_timeline_observability_key(self) -> str:
        return self._build_observability_key(
            prefix_config_name="REGIME_TIMELINE_OBJECTSTORE_KEY_PREFIX",
            default_prefix="regime_timeline_observability",
        )

    def _build_signal_lifecycle_observability_key(self) -> str:
        return self._build_observability_key(
            prefix_config_name="SIGNAL_LIFECYCLE_OBJECTSTORE_KEY_PREFIX",
            default_prefix="signal_lifecycle_observability",
        )

    def _build_router_rejection_observability_key(self) -> str:
        return self._build_observability_key(
            prefix_config_name="ROUTER_REJECTION_OBJECTSTORE_KEY_PREFIX",
            default_prefix="router_rejection_observability",
        )

    def _build_order_lifecycle_observability_key(self) -> str:
        return self._build_observability_key(
            prefix_config_name="ORDER_LIFECYCLE_OBJECTSTORE_KEY_PREFIX",
            default_prefix="order_lifecycle_observability",
        )

    def Initialize(self) -> None:
        # Algorithm bootstrap entrypoint.
        # =====================================================================
        # STEP 1: Basic Setup
        # =====================================================================
        # Full-year backtest window with optional year/date overrides via QC parameters.
        # Examples:
        #   --parameter backtest_year 2023
        #   --parameter start_date 2024-06-01 --parameter end_date 2024-12-31
        backtest_year = 2024
        backtest_year_param = str(self.GetParameter("backtest_year") or "").strip()
        if backtest_year_param:
            try:
                parsed_year = int(backtest_year_param)
                if 2000 <= parsed_year <= 2100:
                    backtest_year = parsed_year
            except (TypeError, ValueError):
                pass
        start_date_param = str(self.GetParameter("start_date") or "").strip()
        end_date_param = str(self.GetParameter("end_date") or "").strip()
        custom_start = None
        custom_end = None
        if start_date_param:
            try:
                custom_start = datetime.strptime(start_date_param, "%Y-%m-%d")
            except ValueError:
                self.Log(f"INIT_WARN: Invalid start_date '{start_date_param}', expected YYYY-MM-DD")
        if end_date_param:
            try:
                custom_end = datetime.strptime(end_date_param, "%Y-%m-%d")
            except ValueError:
                self.Log(f"INIT_WARN: Invalid end_date '{end_date_param}', expected YYYY-MM-DD")
        run_label = str(self.GetParameter("run_label") or "").strip()
        if custom_start is not None and custom_end is None:
            custom_end = datetime(custom_start.year, 12, 31)
        if custom_end is not None and custom_start is None:
            custom_start = datetime(custom_end.year, 1, 1)
        if custom_start is not None and custom_end is not None and custom_end < custom_start:
            self.Log(
                f"INIT_WARN: end_date {custom_end:%Y-%m-%d} is before start_date {custom_start:%Y-%m-%d}; "
                "falling back to full-year window"
            )
            custom_start = None
            custom_end = None
        if custom_start is not None and custom_end is not None:
            backtest_year = custom_start.year
        self._backtest_year = backtest_year
        self._run_label = run_label
        if custom_start is not None and custom_end is not None:
            self.SetStartDate(custom_start.year, custom_start.month, custom_start.day)
            self.SetEndDate(custom_end.year, custom_end.month, custom_end.day)
            date_window_label = f"{custom_start:%Y-%m-%d}..{custom_end:%Y-%m-%d}"
        else:
            self.SetStartDate(backtest_year, 1, 1)
            self.SetEndDate(backtest_year, 12, 31)
            date_window_label = f"{backtest_year}-01-01..{backtest_year}-12-31"
        self.SetCash(config.INITIAL_CAPITAL)  # Seed capital from config
        run_label_display = run_label if run_label else "DEFAULT"
        self.Log(f"INIT: BacktestYear={backtest_year} | RunLabel={run_label_display}")
        self.Log(
            f"INIT: DateWindow={date_window_label}"
            f" | EffectiveStart={self.StartDate:%Y-%m-%d}"
            f" | EffectiveEnd={self.EndDate:%Y-%m-%d}"
        )
        self.Log(f"INIT: RegimeObservabilityKey={self._build_regime_observability_key()}")

        # All times are Eastern
        self.SetTimeZone("America/New_York")

        # Interactive Brokers brokerage model
        self.SetBrokerageModel(BrokerageName.InteractiveBrokersBrokerage)

        # Validator marker: retain explicit DTE-routing telemetry token post-minify.
        self._diag_intraday_dte_routing_marker = "INTRADAY_DTE_ROUTING"
        self._diag_intraday_router_rejected_marker = "INTRADAY_ROUTER_REJECTED"

        # =====================================================================
        # STEP 2: Add Securities
        # =====================================================================
        self._add_securities()

        # =====================================================================
        # STEP 3: Initialize Indicators
        # =====================================================================
        self._setup_indicators()

        # =====================================================================
        # STEP 4: Set Warmup Period
        # =====================================================================
        # 252 days for SMA200 and vol percentile calculation
        self.SetWarmUp(timedelta(days=config.INDICATOR_WARMUP_DAYS))

        # =====================================================================
        # STEP 5: Initialize Engines
        # =====================================================================
        self._initialize_engines()

        # =====================================================================
        # STEP 6: Initialize Infrastructure
        # =====================================================================
        self._initialize_infrastructure()

        # =====================================================================
        # STEP 7: Register Schedules
        # =====================================================================
        self._setup_schedules()

        # =====================================================================
        # STEP 7b: Clear ObjectStore for Backtests (V3.0 FIX)
        # =====================================================================
        # Prevents state contamination from previous backtests
        # Without this, HWM/Governor/KillSwitch state from prior runs
        # can cause ZERO TRADES (as seen in V3.0-70pct-profit-2017 backtest)
        if not self.LiveMode:
            state_keys = [
                # StateManager-backed keys (keep in sync with persistence layer).
                *StateKeys.ALL_KEYS,
                PNL_TRACKER_KEY,
                # V3.3 P0: Legacy/custom state keys.
                "options_engine_state",
                "oco_manager_state",
                "regime_engine_state",
            ]
            for key in state_keys:
                if self.ObjectStore.ContainsKey(key):
                    self.ObjectStore.Delete(key)
            observability_keys = [
                self._build_regime_observability_key(),
                self._build_regime_timeline_observability_key(),
                self._build_signal_lifecycle_observability_key(),
                self._build_router_rejection_observability_key(),
                self._build_order_lifecycle_observability_key(),
            ]
            for observability_key in observability_keys:
                if self.ObjectStore.ContainsKey(observability_key):
                    self.ObjectStore.Delete(observability_key)

        # =====================================================================
        # STEP 8: Load Persisted State
        # =====================================================================
        self._load_state()

        # =====================================================================
        # STEP 9: Initialize Daily Tracking Variables
        # =====================================================================
        self.equity_prior_close = 0.0
        self.equity_sod = 0.0
        self.spy_prior_close = 0.0
        self.spy_open = 0.0
        self._governor_scale = 1.0  # V2.26: Drawdown Governor allocation multiplier
        self._last_risk_result = None  # V2.27: Last risk check result for EOD access
        self.today_trades = []
        self.today_safeguards = []
        self.symbols_to_skip = set()
        self._splits_logged_today = set()  # Log throttle: only log each split once/day
        self._greeks_breach_logged = False  # Log throttle: only log Greeks breach once/position
        self._kill_switch_handled_today = False  # V2.3: Only handle kill switch once per day
        self._margin_cb_in_progress = False  # V2.27: Re-entry guard for margin CB liquidation
        self._last_vass_rejection_log_by_key: Dict[
            str, datetime
        ] = {}  # Keyed throttle for VASS rejections
        self._last_intraday_diag_log_by_key: Dict[
            str, datetime
        ] = {}  # Keyed throttle for high-frequency intraday candidate/drop diagnostics
        self._high_freq_log_seen_counts: Dict[str, int] = {}
        self._high_freq_log_suppressed_counts: Dict[str, int] = {}
        # Signal-id event ledgers keep funnel telemetry deterministic.
        self._diag_intraday_candidate_ids_logged = set()
        self._diag_intraday_approved_ids_logged = set()
        self._diag_intraday_dropped_ids_logged = set()
        self._last_swing_scan_time = None  # V2.19: Throttle swing spread scans (1/hour)
        self._intraday_force_exit_fallback_date = None  # V6.12: Fallback guard (once/day)
        self._mr_force_close_fallback_date = None  # V6.12: MR force close fallback guard
        # Guard against duplicate static+dynamic schedule callbacks on same day.
        self._intraday_force_close_ran_date = None
        self._mr_force_close_ran_date = None
        self._eod_processing_ran_date = None
        self._market_close_ran_date = None
        # Initialize detector state.
        self._regime_detector_prev_score = None
        self._regime_detector_last_update_key = None
        self._regime_detector_last_raw = {}
        self._regime_overlay_ambiguous_bars = 0
        # Intraday reconciliation cadence (reduce zombie/orphan persistence).
        self._last_reconcile_positions_run = None

        # V2.20: Scoped rejection cooldowns — per-strategy penalty after broker rejection
        # Prevents "machine gun" retries while allowing other strategies to continue
        self._trend_rejection_cooldown_until = None  # Trend: skip until next EOD cycle
        self._options_swing_cooldown_until = None  # Options Swing: 30 min cooldown
        self._options_intraday_cooldown_until = None  # Legacy aggregate cooldown view
        self._options_intraday_cooldown_until_by_lane = {
            "MICRO": None,
            "ITM": None,
        }  # Lane-scoped cooldowns
        self._options_spread_cooldown_until = None  # Options Spread: 30 min cooldown
        self._mr_rejection_cooldown_until = None  # Mean Reversion: 15 min cooldown
        # V6.15: One-shot retry for temporary intraday drops (slot/cooldown/margin timing).
        # Lane-scoped to prevent MICRO/ITM cross-bleed in retry behavior.
        self._intraday_retry_state_by_lane = {
            "MICRO": {
                "pending": False,
                "expires": None,
                "direction": None,
                "reason_code": None,
            },
            "ITM": {
                "pending": False,
                "expires": None,
                "direction": None,
                "reason_code": None,
            },
        }
        # V6.15: Fallback ledger to ensure intraday exits always emit INTRADAY_RESULT.
        self._intraday_entry_snapshot = {}
        # Track live MICRO symbols from fill tags so EOD sweep can recover from state races.
        self._micro_open_symbols = set()
        # V6.16: Force-close safety guards (prevent duplicate close amplification).
        self._intraday_close_in_progress_symbols = set()
        self._intraday_force_exit_submitted_symbols = {}
        self._intraday_hold_loss_block_log_date = {}

        # V6.14: Pre-market VIX shock ladder state (portfolio-wide options guard)
        self._premarket_vix_ladder_level = 0
        self._premarket_vix_ladder_reason = "L0"
        self._premarket_vix_size_mult = 1.0
        self._premarket_vix_entry_block_until = None
        self._premarket_vix_call_block_until = None
        self._premarket_vix_shock_pct = 0.0  # Decimal shock vs prior close (e.g., 0.50 = +50%)
        self._premarket_vix_shock_memory_until = None  # (hour, minute)
        self._vix_prior_close = 15.0
        self._uvxy_prior_close = 0.0

        # V2.3.6: Track pending spread orders to handle leg failures
        # Maps short leg symbol -> long leg symbol (to liquidate long if short fails)
        self._pending_spread_orders = {}
        # V2.6 Bug #5: Add reverse mapping for long leg rejection handling
        self._pending_spread_orders_reverse = {}  # long -> short

        # V2.6: Atomic spread fill tracking (Bugs #1, #6, #7)
        self._spread_fill_tracker = None

        # V6.22: Per-spread close trackers (fixes shared-counter corruption).
        # key: "<long_symbol>|<short_symbol>"
        self._spread_close_trackers = {}
        # V6.22: Track external broker order events to avoid EXEC: UNKNOWN_ORDER spam.
        self._external_exec_event_logged = set()
        # V6.19: Run-level diagnostics counters for hardening validation.
        self._diag_margin_reject_count = 0
        self._diag_intraday_candidate_count = 0
        self._diag_intraday_approved_count = 0
        self._diag_intraday_dropped_count = 0
        self._diag_intraday_router_reject_count = 0
        self._diag_intraday_result_count = 0
        self._diag_vass_block_count = 0
        self._diag_overlay_block_count = 0
        self._diag_overlay_slot_block_count = 0
        self._diag_spread_close_escalation_count = 0
        self._diag_spread_entry_signal_count = 0
        self._diag_spread_entry_submit_count = 0
        self._diag_spread_entry_fill_count = 0
        self._diag_spread_exit_signal_count = 0
        self._diag_spread_exit_submit_count = 0
        self._diag_spread_exit_fill_count = 0
        self._diag_spread_exit_canceled_count = 0
        self._diag_spread_position_removed_count = 0
        self._diag_spread_removed_fill_path_count = 0
        self._diag_spread_ghost_removed_count = 0
        self._diag_spread_loss_beyond_stop_count = 0
        self._diag_micro_tag_recovery_count = 0
        self._diag_micro_eod_sweep_close_count = 0
        self._diag_micro_pending_cancel_ignored_count = 0
        self._order_lifecycle_log_count = 0
        self._order_lifecycle_suppressed_count = 0
        self._diag_micro_dte_candidates = {"2": 0, "3": 0, "4": 0, "5": 0, "OTHER": 0}
        self._diag_micro_dte_approved = {"2": 0, "3": 0, "4": 0, "5": 0, "OTHER": 0}
        self._diag_micro_dte_dropped = {"2": 0, "3": 0, "4": 0, "5": 0, "OTHER": 0}
        self._diag_micro_dte_win = {"2": 0, "3": 0, "4": 0, "5": 0, "OTHER": 0}
        self._diag_micro_dte_loss = {"2": 0, "3": 0, "4": 0, "5": 0, "OTHER": 0}
        self._diag_micro_drop_reason_by_dte = {}
        self._diag_router_reject_reason_counts = {}
        self._diag_router_reject_reason_counts_by_engine = {
            "VASS": {},
            "MICRO": {},
            "ITM": {},
            "OTHER": {},
        }
        self._recent_router_rejections: List[Any] = []
        self._diag_vass_reject_reason_counts = {}
        self._diag_transition_path_counts: Dict[str, int] = {}
        self._diag_vass_mfe_peak_max_profit_pct = 0.0
        self._diag_vass_mfe_t1_hits = 0
        self._diag_vass_mfe_t2_hits = 0
        self._diag_vass_mfe_lock_exits = 0
        self._diag_vass_tail_cap_exits = 0
        self._diag_exit_path_counts = {}
        self._diag_exit_path_pnl = {}
        self._diag_exit_path_counts_by_engine = {"VASS": {}, "MICRO": {}, "ITM": {}, "OTHER": {}}
        self._diag_exit_path_pnl_by_engine = {"VASS": {}, "MICRO": {}, "ITM": {}, "OTHER": {}}
        self._diag_intraday_candidates_by_engine = {"MICRO": 0, "ITM": 0, "OTHER": 0}
        self._diag_intraday_approved_by_engine = {"MICRO": 0, "ITM": 0, "OTHER": 0}
        self._diag_intraday_dropped_by_engine = {"MICRO": 0, "ITM": 0, "OTHER": 0}
        self._diag_intraday_drop_reason_counts = {}
        self._diag_intraday_drop_reason_counts_by_engine = {
            "MICRO": {},
            "ITM": {},
            "OTHER": {},
        }
        self._diag_intraday_results_by_engine = {"MICRO": 0, "ITM": 0, "OTHER": 0}
        self._diag_transition_derisk_counts = {
            "de_risk_on_deterioration": 0,
            "de_risk_on_recovery": 0,
        }
        self._diag_transition_derisk_counts_by_engine = {
            "VASS": {"de_risk_on_deterioration": 0, "de_risk_on_recovery": 0},
            "ITM": {"de_risk_on_deterioration": 0, "de_risk_on_recovery": 0},
            "MICRO": {"de_risk_on_deterioration": 0, "de_risk_on_recovery": 0},
            "OTHER": {"de_risk_on_deterioration": 0, "de_risk_on_recovery": 0},
        }
        self._transition_execution_context: Optional[Dict[str, Any]] = None
        self._transition_execution_context_minute_key: Optional[str] = None
        self._transition_execution_context_sample_seq: int = -1
        self._last_micro_update_log_signature = None
        self._last_micro_update_log_time = None
        self._last_spread_construct_fail_log_at = None
        self._intraday_regime_score = None
        self._intraday_regime_updated_at = None
        self._intraday_regime_momentum_roc = None
        self._intraday_regime_vix_5d_change = None
        self._regime_base_state = "NEUTRAL"
        self._regime_base_candidate_state = "NEUTRAL"
        self._regime_base_candidate_streak = 0
        self._regime_base_state_enter_seq = 0
        self._regime_overlay_state = "STABLE"
        self._regime_overlay_candidate_state = "STABLE"
        self._regime_overlay_candidate_streak = 0
        self._regime_overlay_state_enter_seq = 0
        self._regime_overlay_ambiguous_bars = 0
        self._regime_detector_sample_seq = 0
        self._regime_detector_prev_score: Optional[float] = None
        self._regime_detector_last_update_key = None
        self._regime_detector_last_raw = {}
        self._regime_decision_records: List[Dict[str, Any]] = []
        self._regime_decision_overflow_logged = False
        self._regime_observability_key = self._build_regime_observability_key()
        self._regime_timeline_records: List[Dict[str, Any]] = []
        self._regime_timeline_overflow_logged = False
        self._regime_timeline_observability_key = self._build_regime_timeline_observability_key()
        self._signal_lifecycle_records: List[Dict[str, Any]] = []
        self._signal_lifecycle_overflow_logged = False
        self._signal_lifecycle_observability_key = self._build_signal_lifecycle_observability_key()
        self._router_rejection_records: List[Dict[str, Any]] = []
        self._router_rejection_overflow_logged = False
        self._router_rejection_observability_key = self._build_router_rejection_observability_key()
        self._order_lifecycle_records: List[Dict[str, Any]] = []
        self._order_lifecycle_overflow_logged = False
        self._order_lifecycle_observability_key = self._build_order_lifecycle_observability_key()
        self._observability_log_fallback_signature_by_key: Dict[str, str] = {}
        self._diag_vass_signal_seq = 0
        self._last_regime_effective_log_at = None
        self._last_intraday_dte_routing_log_by_key = {}
        # Preserve best-effort order tags for lifecycle diagnostics when broker tags are blank.
        self._order_tag_hint_cache = {}
        # Per-symbol last order tag/fill time for lifecycle attribution and reconcile guard.
        self._last_option_fill_tag_by_symbol = {}
        self._last_option_fill_time_by_symbol = {}
        self._order_tag_map_logged_ids = set()
        self._order_tag_resolve_logged_ids = set()
        # Throttled intraday ObjectStore persistence marker (live-mode safety).
        self._last_state_persist_at = None
        # Daily guard to keep regime proxy rolling windows on day cadence.
        self._daily_proxy_window_last_update: Dict[str, Any] = {}

        # V2.6 Bug #14: Exit order retry tracking
        self._pending_exit_orders = {}
        # Open-order lifecycle guard: avoid stacked retry schedules for same symbol.
        self._exit_retry_scheduled_at = {}
        # V6.22: Persist forced spread-close retries when broker cancels close legs.
        # key = spread key "<long>|<short>", value = next eligible retry time.
        self._spread_forced_close_retry = {}
        self._spread_forced_close_reason = {}
        self._spread_forced_close_cancel_counts = {}
        self._spread_forced_close_retry_cycles = {}
        self._spread_last_close_submit_at = {}
        # Last known spread leg marks to avoid skipping exit checks on transient quote gaps.
        self._spread_exit_mark_cache = {}
        self._spread_last_exit_reason = {}
        self._single_leg_last_exit_reason = {}
        # Track consecutive intraday ghost detections by spread key.
        self._spread_ghost_flat_streak_by_key = {}
        self._spread_ghost_last_log_by_key = {}
        self._friday_spread_reconcile_date = None
        # Orphan-close idempotency: avoid resubmitting the same orphan liquidation every reconcile cycle.
        self._recon_orphan_close_submitted = {}
        # Guarded orphan reconciliation trackers (intraday mode).
        self._recon_orphan_seen_streak = {}
        self._recon_orphan_first_seen_at = {}
        self._recon_orphan_last_log_at = {}

        # V2.4.4 P0: Margin call circuit breaker tracking
        # Prevents 2765+ margin call spam seen in V2.4.3 backtest
        self._margin_call_consecutive_count = 0
        self._margin_call_cooldown_until = None

    def OnData(self, data: Slice) -> None:
        """
        Process incoming data every minute.

        Execution order (CRITICAL - do not reorder):
            0. Split check (MUST BE FIRST)
            1. Skip during warmup
            2. Update rolling windows
            3. Run risk checks
            4. Handle kill switch
            5. Handle panic mode
            6. MR entry scanning
            7. MR exit checking
            8. Trend stop monitoring
            9. Process immediate signals

        Args:
            data: Current data slice from QuantConnect.
        """
        # =====================================================================
        # STEP 0: MANDATORY SPLIT CHECK - MUST BE FIRST
        # =====================================================================
        if self._check_splits(data):
            return  # Proxy split detected - freeze ALL processing

        # =====================================================================
        # STEP 1: UPDATE ROLLING WINDOWS (before warmup check so they're ready)
        # =====================================================================
        self._update_rolling_windows(data)

        # =====================================================================
        # STEP 2: SKIP DURING WARMUP
        # =====================================================================
        if self.IsWarmingUp:
            return

        # =====================================================================
        # STEP 2.5: V3.0 STALE ORDER CLEANUP (every 5 minutes)
        # =====================================================================
        # Cancel orphaned orders from previous failed cycles to prevent interference
        self._cleanup_stale_orders()

        # =====================================================================
        # STEP 3: RISK ENGINE CHECKS (ALWAYS FIRST AFTER SPLITS)
        # =====================================================================
        risk_result = self._run_risk_checks(data)
        self._last_risk_result = risk_result  # V2.27: Store for EOD signal access

        # =====================================================================
        # STEP 3B: V2.4.4 P0 - EXPIRATION HAMMER V2 (runs every minute after 2 PM)
        # =====================================================================
        # This runs BEFORE kill switch to ensure expiring options are closed
        # even if the account is in margin crisis
        self._check_expiration_hammer_v2()

        # =====================================================================
        # STEP 3C: V6.6.2 OCO RECOVERY (ensure exits exist for open options)
        # =====================================================================
        self._ensure_oco_for_open_options()
        self._reconcile_intraday_close_guards()

        # =====================================================================
        # STEP 4: HANDLE KILL SWITCH (V2.27: Graduated tiers)
        # =====================================================================
        # Tier 1 (REDUCE): Applied through result flags, doesn't need handler
        # Tier 2 (TREND_EXIT): Liquidate trend, keep spreads → needs handler
        # Tier 3 (FULL_EXIT): Liquidate everything → needs handler
        if risk_result.ks_tier in (KSTier.TREND_EXIT, KSTier.FULL_EXIT):
            self._handle_kill_switch(risk_result)
            return  # All other processing skipped

        # =====================================================================
        # STEP 5: HANDLE PANIC MODE
        # =====================================================================
        if SafeguardType.PANIC_MODE in risk_result.active_safeguards:
            self._handle_panic_mode(risk_result)
            # Continue processing - panic mode only liquidates longs

        # =====================================================================
        # STEP 6: MR ENTRY SCANNING (if window open and entries allowed)
        # =====================================================================
        # DEBUG: Bypass scheduler, use direct time check
        current_hour = self.Time.hour
        mr_window_open = 10 <= current_hour < 15

        # V6.0: MR requires TREND/MR permission (REDUCED+ phase)
        if (
            mr_window_open
            and risk_result.can_enter_intraday
            and self._governor_scale > 0.0
            and self.startup_gate.allows_trend_mr()
        ):
            self._scan_mr_signals(data)

        # =====================================================================
        # STEP 6B: V2.1 OPTIONS ENTRY SCANNING (if window open)
        # =====================================================================
        # V2.30: Direction-aware gating — bearish options unlock before bullish
        # V2.27: Also check can_enter_options (Tier 1 blocks new options)
        # V2.32: Direction-aware governor — bear options allowed at lower governor scales
        # Bear options REDUCE risk during drawdowns, bull options INCREASE risk
        if mr_window_open and risk_result.can_enter_intraday and risk_result.can_enter_options:
            regime_score = self._get_effective_regime_score_for_options()

            # V3.5 Fix: Determine governor threshold based on regime direction
            # Allow bearish options (PUT spreads) in NEUTRAL zone - they're defensive
            if regime_score <= config.SPREAD_REGIME_BULLISH:  # <= 70: bearish/defensive
                # NEUTRAL/CAUTIOUS/BEAR: Allow PUT options at low governor (risk-reducing)
                min_governor = config.GOVERNOR_INTRADAY_OPTIONS_MIN_SCALE_BEARISH
            else:
                # BULL (>70): Require higher governor for CALL options (risk-increasing)
                min_governor = config.GOVERNOR_INTRADAY_OPTIONS_MIN_SCALE

            if self._governor_scale >= min_governor:
                self._scan_options_signals_gated(data)

        # =====================================================================
        # STEP 7: CHECK MR EXITS (always if position exists)
        # =====================================================================
        self._check_mr_exits(data)

        # =====================================================================
        # STEP 8: TREND POSITION MONITORING (intraday stop check)
        # =====================================================================
        self._monitor_trend_stops(data)

        # =====================================================================
        # STEP 8B: V2.1 OPTIONS GREEKS MONITORING
        # =====================================================================
        self._monitor_risk_greeks(data)

        # =====================================================================
        # STEP 9: PROCESS IMMEDIATE SIGNALS
        # =====================================================================
        self._process_immediate_signals()

        # =====================================================================
        # STEP 9B: V6.12 FALLBACK INTRADAY FORCE-CLOSE (safety net)
        # =====================================================================
        # If the scheduled force-close misses, enforce once after +5 minutes.
        self._intraday_force_exit_fallback()

        # =====================================================================
        # STEP 9C: V6.12 FALLBACK MR FORCE-CLOSE (safety net)
        # =====================================================================
        # If the scheduled 15:45 MR close missed, enforce once after 15:50.
        self._mr_force_close_fallback()

    # =========================================================================
    # SETUP HELPERS
    # =========================================================================

    def _add_securities(self) -> None:
        """
        Add all required securities at Minute resolution.

        V6.11 Universe:
            - Trend: QLD, SSO, UGL, UCO (diversified w/ commodities)
            - MR: TQQQ, SOXL, SPXL
            - Hedge: SH (1× inverse S&P)
            - Proxy: SPY, RSP, HYG, IEF (for regime calculation)

        Stores symbol references as instance attributes for easy access.
        """
        # Traded symbols - Leveraged longs (Trend Engine)
        self.tqqq = self.AddEquity("TQQQ", Resolution.Minute).Symbol
        self.soxl = self.AddEquity("SOXL", Resolution.Minute).Symbol
        self.qld = self.AddEquity("QLD", Resolution.Minute).Symbol
        self.sso = self.AddEquity("SSO", Resolution.Minute).Symbol

        # V6.11: New Trend symbols for commodity diversification
        self.ugl = self.AddEquity("UGL", Resolution.Minute).Symbol  # 2× Gold
        self.uco = self.AddEquity("UCO", Resolution.Minute).Symbol  # 2× Crude Oil

        # V6.11: SPXL for MR Engine (broader market bounces)
        self.spxl = self.AddEquity("SPXL", Resolution.Minute).Symbol  # 3× S&P 500

        # Traded symbols - Hedges
        self.sh = self.AddEquity("SH", Resolution.Minute).Symbol  # 1× Inverse S&P

        # V2.1: QQQ for options trading
        self.qqq = self.AddEquity("QQQ", Resolution.Minute).Symbol

        # V2.1: Add QQQ options chain with config-driven DTE filter
        qqq_option = self.AddOption("QQQ", Resolution.Minute)
        # V2.23: Wide filter for VASS credit spread short legs at any VIX level
        # Covers OTM strikes needed for delta 0.25-0.40 credit spread short legs
        # Previous: (-8, 5) missed credit spread candidates at high VIX
        qqq_option.SetFilter(
            -25, 25, timedelta(days=config.OPTIONS_DTE_MIN), timedelta(days=config.OPTIONS_DTE_MAX)
        )
        self._qqq_option_symbol = qqq_option.Symbol
        # CRITICAL FIX: Track if options symbol has been validated (first successful chain access)
        # Symbol may not be fully resolved until data arrives
        self._qqq_options_validated = False
        self._qqq_options_validation_attempts = 0

        # Proxy symbols - For regime calculation
        self.spy = self.AddEquity("SPY", Resolution.Minute).Symbol
        self.rsp = self.AddEquity("RSP", Resolution.Minute).Symbol
        self.hyg = self.AddEquity("HYG", Resolution.Minute).Symbol
        self.ief = self.AddEquity("IEF", Resolution.Minute).Symbol

        # V2.1: VIX for Mean Reversion regime filter
        # Default to Index subscription for cloud runtime stability; keep CBOE
        # custom data as explicit fallback option.
        vix_data_source = str(getattr(config, "VIX_DATA_SOURCE", "INDEX")).upper()
        if vix_data_source == "CBOE":
            self.vix = self.AddData(CBOE, "VIX", Resolution.Daily).Symbol
        else:
            self.vix = self.AddIndex("VIX", Resolution.Daily).Symbol
        self._current_vix = 15.0  # Default to normal regime until data arrives
        self._vix_at_open = 15.0  # V2.1.1: VIX at market open for micro regime
        self._vix_5min_ago = 15.0  # V2.1.1: VIX 5 minutes ago for spike detection
        self._vix_15min_ago = 15.0  # V2.3.4: VIX 15 minutes ago for short-term trend
        self._last_vix_spike_log = None  # Log throttle: last VIX spike log time
        self._last_vix_update_date = None  # V10.8: track last day with fresh CBOE VIX print
        self._last_vix_stale_log_date = None  # V10.8: stale fallback log throttle
        self._qqq_at_open = 0.0  # V2.1.1: QQQ at market open

        # V2.3.4: UVXY as intraday VIX proxy (Minute resolution for direction tracking)
        # UVXY tracks ~1.5x daily VIX moves, so we use % change as direction signal
        self.uvxy = self.AddEquity("UVXY", Resolution.Minute).Symbol
        self._uvxy_at_open = 0.0  # UVXY price at market open

        # V2.4.1: Intraday scan throttle - only scan every 15 minutes
        # Fixes 95 scans/hour → 4 scans/hour
        self._last_intraday_scan = None

        # V2.9: Settlement-aware trading (Bug #6 fix)
        # Options settle T+1. Friday closes don't settle until Monday morning.
        # Uses exchange calendar (not weekday()) to handle holidays correctly.
        self._settlement_cooldown_until = None
        self._last_market_close_check = None

        # Store collections for iteration
        # V6.11: Updated traded symbols for diversified universe
        self.traded_symbols = [
            self.tqqq,  # MR: 3× Nasdaq
            self.soxl,  # MR: 3× Semiconductor
            self.spxl,  # MR: 3× S&P 500
            self.qld,  # Trend: 2× Nasdaq
            self.sso,  # Trend: 2× S&P 500
            self.ugl,  # Trend: 2× Gold
            self.uco,  # Trend: 2× Oil
            self.sh,  # Hedge: 1× Inverse S&P
        ]
        self.proxy_symbols = [self.spy, self.rsp, self.hyg, self.ief]

        # V6.11: Trend symbols for diversified universe
        self.trend_symbols = [self.qld, self.sso, self.ugl, self.uco]

        self.Log(
            f"INIT: Added {len(self.traded_symbols)} traded symbols, "
            f"{len(self.proxy_symbols)} proxy symbols"
        )
        self.Log(f"INIT: VIX data source={vix_data_source}")

    def _setup_indicators(self) -> None:
        """
        Initialize all technical indicators.

        Indicators:
            - SPY SMAs (20/50/200): Trend factor for regime engine
            - SPY ATR: Vol shock detection (minute resolution)
            - QLD/SSO MA200: Trend direction for trend engine
            - QLD/SSO ADX: Momentum confirmation for trend engine
            - QLD/SSO ATR: Chandelier stop calculation
            - TQQQ/SOXL RSI(5): Mean reversion oversold detection

        Also initializes rolling windows for historical price access.
        """
        # ---------------------------------------------------------------------
        # Regime Engine Indicators (Daily resolution for end-of-day calculation)
        # ---------------------------------------------------------------------
        self.spy_sma20 = self.SMA(self.spy, config.SMA_FAST, Resolution.Daily)
        self.spy_sma50 = self.SMA(self.spy, config.SMA_MED, Resolution.Daily)
        self.spy_sma200 = self.SMA(self.spy, config.SMA_SLOW, Resolution.Daily)

        # V2.26: SPY ADX(14) for Chop Detection (regime engine 6th factor)
        self.spy_adx_daily = self.ADX(self.spy, config.ADX_PERIOD, Resolution.Daily)

        # V3.7: SPY 52-week high for Drawdown Factor (252 trading days)
        # CRITICAL FIX: Previously used 25-day rolling window, not actual 52-week high
        self.spy_52w_high = self.MAX(self.spy, 252, Resolution.Daily)

        # ---------------------------------------------------------------------
        # Risk Engine Indicators
        # ---------------------------------------------------------------------
        # SPY ATR for vol shock detection (minute resolution for intraday check)
        self.spy_atr = self.ATR(
            self.spy, config.ATR_PERIOD, MovingAverageType.Wilders, Resolution.Minute
        )

        # V6.5: QQQ ATR for options stop calculation (delta-scaled ATR stops)
        self.qqq_atr = self.ATR(
            self.qqq, config.ATR_PERIOD, MovingAverageType.Wilders, Resolution.Daily
        )

        # ---------------------------------------------------------------------
        # Trend Engine Indicators (Daily resolution for EOD signals)
        # ---------------------------------------------------------------------
        # V2: MA200 for trend direction + ADX for momentum confirmation
        self.qld_ma200 = self.SMA(self.qld, config.SMA_SLOW, Resolution.Daily)
        self.sso_ma200 = self.SMA(self.sso, config.SMA_SLOW, Resolution.Daily)

        self.qld_adx = self.ADX(self.qld, config.ADX_PERIOD, Resolution.Daily)
        self.sso_adx = self.ADX(self.sso, config.ADX_PERIOD, Resolution.Daily)

        # ATR for Chandelier stop calculation
        self.qld_atr = self.ATR(
            self.qld, config.ATR_PERIOD, MovingAverageType.Wilders, Resolution.Daily
        )
        self.sso_atr = self.ATR(
            self.sso, config.ATR_PERIOD, MovingAverageType.Wilders, Resolution.Daily
        )

        # V6.11: UGL indicators (2× Gold - commodity diversification)
        self.ugl_ma200 = self.SMA(self.ugl, config.SMA_SLOW, Resolution.Daily)
        self.ugl_adx = self.ADX(self.ugl, config.ADX_PERIOD, Resolution.Daily)
        self.ugl_atr = self.ATR(
            self.ugl, config.ATR_PERIOD, MovingAverageType.Wilders, Resolution.Daily
        )

        # V6.11: UCO indicators (2× Crude Oil - commodity diversification)
        self.uco_ma200 = self.SMA(self.uco, config.SMA_SLOW, Resolution.Daily)
        self.uco_adx = self.ADX(self.uco, config.ADX_PERIOD, Resolution.Daily)
        self.uco_atr = self.ATR(
            self.uco, config.ATR_PERIOD, MovingAverageType.Wilders, Resolution.Daily
        )

        # V2.4: SMA50 for Structural Trend exit (replaces Chandelier trailing stops)
        # SMA50 allows minor volatility (3% drops) without exit if above trend line
        self.qld_sma50 = self.SMA(self.qld, config.TREND_SMA_PERIOD, Resolution.Daily)
        self.sso_sma50 = self.SMA(self.sso, config.TREND_SMA_PERIOD, Resolution.Daily)
        # V6.11: SMA50 for new commodity trend symbols
        self.ugl_sma50 = self.SMA(self.ugl, config.TREND_SMA_PERIOD, Resolution.Daily)
        self.uco_sma50 = self.SMA(self.uco, config.TREND_SMA_PERIOD, Resolution.Daily)

        # ---------------------------------------------------------------------
        # Mean Reversion Engine Indicators (Minute resolution for intraday)
        # ---------------------------------------------------------------------
        self.tqqq_rsi = self.RSI(
            self.tqqq, config.RSI_PERIOD, MovingAverageType.Wilders, Resolution.Minute
        )
        self.soxl_rsi = self.RSI(
            self.soxl, config.RSI_PERIOD, MovingAverageType.Wilders, Resolution.Minute
        )
        # V6.11: SPXL RSI for broader market mean reversion
        self.spxl_rsi = self.RSI(
            self.spxl, config.RSI_PERIOD, MovingAverageType.Wilders, Resolution.Minute
        )

        # ---------------------------------------------------------------------
        # V2.1: Options Engine Indicators (QQQ for options trading)
        # ---------------------------------------------------------------------
        self.qqq_adx = self.ADX(self.qqq, config.ADX_PERIOD, Resolution.Daily)
        self.qqq_sma20 = self.SMA(self.qqq, config.SMA_FAST, Resolution.Daily)
        self.qqq_sma50 = self.SMA(self.qqq, config.SMA_MED, Resolution.Daily)
        self.qqq_sma200 = self.SMA(self.qqq, config.SMA_SLOW, Resolution.Daily)
        # V2.3: QQQ RSI for direction selection (Daily for swing mode)
        self.qqq_rsi = self.RSI(
            self.qqq, config.RSI_PERIOD, MovingAverageType.Wilders, Resolution.Daily
        )

        # ---------------------------------------------------------------------
        # Rolling Windows for Historical Price Access (Regime Engine needs 21+ days)
        # ---------------------------------------------------------------------
        lookback = max(config.VOL_LOOKBACK, config.BREADTH_LOOKBACK, config.CREDIT_LOOKBACK) + 5
        self.spy_closes = RollingWindow[float](lookback)
        self.rsp_closes = RollingWindow[float](lookback)
        self.hyg_closes = RollingWindow[float](lookback)
        self.ief_closes = RollingWindow[float](lookback)

        # Volume rolling windows for MR engine (20-day average)
        # V6.11: Added SPXL
        self.tqqq_volumes = RollingWindow[float](20)
        self.soxl_volumes = RollingWindow[float](20)
        self.spxl_volumes = RollingWindow[float](20)

        # Store last regime score for intraday use
        self._last_regime_score = 50.0
        self._last_regime_momentum_roc = 0.0
        self._last_regime_vix_5d_change = 0.0

        # Store capital state for EOD->Market Close handoff
        self._eod_capital_state = None
        # V6.6.2: OCO recovery throttle (symbol -> last attempt datetime)
        self._last_oco_recovery_attempt = {}

        self.Log(
            f"INIT: Indicators initialized | "
            f"Lookback={lookback} days | "
            f"Warmup={config.INDICATOR_WARMUP_DAYS} days"
        )

    def _initialize_engines(self) -> None:
        # Initialize core + strategy engines.
        # Core Engines
        self.regime_engine = RegimeEngine(self)
        self.capital_engine = CapitalEngine(self)
        self.risk_engine = RiskEngine(self)
        self.cold_start_engine = ColdStartEngine(self)
        self.startup_gate = StartupGate(self)

        # Strategy Engines
        self.trend_engine = TrendEngine(self)
        self.mr_engine = MeanReversionEngine(self)
        self.hedge_engine = HedgeEngine(self)

        # V2.1: Options Engine and OCO Manager
        self.options_engine = OptionsEngine(self)
        self.oco_manager = OCOManager(self)

        self.Log("INIT: All engines initialized")

    def _initialize_infrastructure(self) -> None:
        # Initialize router/execution/state/scheduler infrastructure.
        self.portfolio_router = PortfolioRouter(self)
        self.execution_engine = ExecutionEngine(self)
        self.state_manager = StateManager(self)
        self.scheduler = DailyScheduler(self)

        # V6.12: Monthly P&L Tracker
        self.pnl_tracker = MonthlyPnLTracker(self)

        self.Log("INIT: Infrastructure initialized")

    def _setup_schedules(self) -> None:
        # Register all scheduled events + callbacks.
        # Register events with QuantConnect
        self.scheduler.register_events()

        # Register callbacks for each event
        self.scheduler.on_pre_market_setup(self._on_pre_market_setup)
        self.scheduler.on_moo_fallback(self._on_moo_fallback)
        self.scheduler.on_sod_baseline(self._on_sod_baseline)
        self.scheduler.on_warm_entry_check(self._on_warm_entry_check)
        self.scheduler.on_time_guard_start(self._on_time_guard_start)
        self.scheduler.on_time_guard_end(self._on_time_guard_end)
        self.scheduler.on_mr_force_close(self._on_mr_force_close)
        self.scheduler.on_eod_processing(self._on_eod_processing)
        self.scheduler.on_market_close(self._on_market_close)
        self.scheduler.on_weekly_reset(self._on_weekly_reset)

        # V6.12: CRITICAL FIX - Static fallback EOD schedules
        # The dynamic scheduling in _schedule_dynamic_eod_events() was failing silently,
        # causing NO EOD events to fire. This resulted in:
        # - Positions held overnight when they should close
        # - OCO orders never triggered (0% trigger rate across all backtests)
        # - Missing intraday force-close / 15:45 / 16:00 processing
        #
        # These static schedules act as a fallback for normal trading days (4:00 PM close).
        # For early close days, dynamic scheduling should override these times.
        intraday_force_exit = getattr(config, "INTRADAY_FORCE_EXIT_TIME", "15:15")
        intraday_force_hour, intraday_force_minute = map(int, intraday_force_exit.split(":"))
        self.Schedule.On(
            self.DateRules.EveryDay(),
            self.TimeRules.At(intraday_force_hour, intraday_force_minute),
            self._on_intraday_options_force_close,
        )
        self.Schedule.On(
            self.DateRules.EveryDay(),
            self.TimeRules.At(15, 45),
            self._on_mr_force_close,
        )
        self.Schedule.On(
            self.DateRules.EveryDay(),
            self.TimeRules.At(15, 45),
            self._on_eod_processing,
        )
        self.Schedule.On(
            self.DateRules.EveryDay(),
            self.TimeRules.At(16, 0),
            self._on_market_close,
        )

        # V2.1.1: Tiered VIX Monitoring schedules
        # Layer 1: Spike detection every 5 minutes (10:00 - 15:00)
        for hour in range(10, 15):
            for minute in [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55]:
                self.Schedule.On(
                    self.DateRules.EveryDay(),
                    self.TimeRules.At(hour, minute),
                    self._on_vix_spike_check,
                )

        # Layer 2 & 4: Direction + Regime update every 15 minutes (10:00 - 15:00)
        for hour in range(10, 15):
            for minute in [0, 15, 30, 45]:
                self.Schedule.On(
                    self.DateRules.EveryDay(),
                    self.TimeRules.At(hour, minute),
                    self._on_micro_regime_update,
                )
        # Phase A: intraday macro-regime refresh to reduce stale EOD lag for options gating.
        # Add an early-session refresh so options are not anchored to yesterday's regime
        # during the first half of the trading day.
        refresh_times = [
            # Fast cadence around the open (regime turns cluster here).
            (9, 35),
            (9, 50),
            (10, 5),
            (10, 20),
            (10, 35),
            (10, 50),
            (11, 0),
            # Slower cadence later in the session.
            (11, 30),
            (12, 0),
            (12, 30),
            (13, 0),
            (13, 30),
            (14, 0),
            (14, 30),
            (15, 0),
        ]
        for hour, minute in refresh_times:
            self.Schedule.On(
                self.DateRules.EveryDay(),
                self.TimeRules.At(hour, minute),
                self._refresh_intraday_regime_score,
            )
        # Periodic observability checkpoint flush so runtime errors don't wipe full-day telemetry.
        for hour, minute in [(11, 0), (13, 0), (15, 0)]:
            self.Schedule.On(
                self.DateRules.EveryDay(),
                self.TimeRules.At(hour, minute),
                self._on_observability_checkpoint,
            )
        # #8 fix: intraday reconciliation checkpoints (zombie/orphan cleanup)
        # Add late-day checkpoints so intraday ghost streak policy can converge
        # before close instead of deferring to next SOD.
        for hour, minute in [(11, 30), (13, 30), (15, 0), (15, 30)]:
            self.Schedule.On(
                self.DateRules.EveryDay(),
                self.TimeRules.At(hour, minute),
                self._on_intraday_reconcile,
            )

        # V2.4.1: Friday Firewall - close swing options before weekend
        # V2.9: Holiday-aware expiration firewall (Bug #3 fix)
        # Runs every day at configured time, but only executes on expiration firewall days
        # (Friday normally, Thursday if Friday is a holiday like Good Friday)
        if config.FRIDAY_FIREWALL_ENABLED:
            self.Schedule.On(
                self.DateRules.EveryDay(),
                self.TimeRules.At(
                    config.FRIDAY_FIREWALL_TIME_HOUR,
                    config.FRIDAY_FIREWALL_TIME_MINUTE,
                ),
                self._on_friday_firewall,
            )
            self.Log(
                f"INIT: Expiration Firewall enabled at "
                f"{config.FRIDAY_FIREWALL_TIME_HOUR}:{config.FRIDAY_FIREWALL_TIME_MINUTE:02d} (holiday-aware)"
            )

        self.Log("INIT: Scheduled events and callbacks registered")

    # =========================================================================
    # ONDATA HELPERS
    # =========================================================================

    def _check_splits(self, data: Slice) -> bool:
        """
        Check for stock splits on proxy and traded symbols.

        Split handling:
            - Proxy symbol split (SPY, RSP, HYG, IEF): Freeze ALL processing
            - Traded symbol split: Freeze only that symbol

        Args:
            data: Current data slice from OnData.

        Returns:
            True if proxy split detected (freeze all), False otherwise.
        """
        # Check proxy symbols - freeze EVERYTHING if any split
        for proxy in self.proxy_symbols:
            if data.Splits.ContainsKey(proxy):
                # Only log split once per symbol per day
                proxy_str = str(proxy)
                if proxy_str not in self._splits_logged_today:
                    self.Log(f"SPLIT: {proxy_str} (proxy) - freezing all")
                    self._splits_logged_today.add(proxy_str)
                return True

        # Check traded symbols - freeze only that symbol
        for symbol in self.traded_symbols:
            if data.Splits.ContainsKey(symbol):
                symbol_str = str(symbol)
                self.symbols_to_skip.add(symbol_str)
                # Register with risk engine for tracking
                self.risk_engine.register_split(symbol_str)
                # Only log split once per symbol per day
                if symbol_str not in self._splits_logged_today:
                    self.Log(f"SPLIT: {symbol_str} - freezing symbol")
                    self._splits_logged_today.add(symbol_str)

        return False

    def _cleanup_stale_orders(self) -> None:
        """
        V3.0 P2: Clean up stale orders at start of logic cycle.

        Cancels orders that have been pending for more than 5 minutes to prevent
        orphaned orders from previous failed cycles from interfering with new logic.
        Runs every 5 minutes to avoid excessive API calls.
        """
        # Rate limit: only run every 5 minutes
        if not hasattr(self, "_last_stale_order_check"):
            self._last_stale_order_check = None

        if self._last_stale_order_check is not None:
            minutes_since_check = (self.Time - self._last_stale_order_check).total_seconds() / 60
            if minutes_since_check < 5:
                return  # Too soon, skip

        self._last_stale_order_check = self.Time

        # Get all open orders and cancel those older than 5 minutes
        try:
            open_orders = list(self.Transactions.GetOpenOrders())
            if not open_orders:
                return

            stale_count = 0
            for order in open_orders:
                # V3.0 FIX: Handle timezone-aware vs naive datetime comparison
                try:
                    order_age_minutes = (self.Time - order.Time).total_seconds() / 60
                except TypeError:
                    # If timezone mismatch, use naive comparison
                    order_age_minutes = (
                        self.Time.replace(tzinfo=None) - order.Time.replace(tzinfo=None)
                    ).total_seconds() / 60
                if order_age_minutes > 5:
                    order_tag = str(getattr(order, "Tag", "") or "")
                    # Keep protective OCO orders alive; they are long-lived by design.
                    if "OCO_STOP:" in order_tag or "OCO_PROFIT:" in order_tag:
                        continue
                    try:
                        self.Transactions.CancelOrder(order.Id)
                        stale_count += 1
                    except Exception as e:
                        self.Log(f"STALE_CLEANUP: Failed to cancel order {order.Id} | {e}")

            if stale_count > 0:
                self.Log(f"STALE_CLEANUP: Cancelled {stale_count} orders older than 5 minutes")

        except Exception as e:
            self.Log(f"STALE_CLEANUP: Error checking orders | {e}")

    def _update_rolling_windows(self, data: Slice) -> None:
        """
        Update rolling windows with current close prices.

        Rolling windows are used by the Regime Engine for:
            - Volatility factor (SPY 20-day realized vol percentile)
            - Breadth factor (RSP:SPY ratio)
            - Credit factor (HYG:IEF spread)

        Called once per minute, but only updates at end of day bar.
        For intraday, uses current price as proxy for close.
        """
        # Keep macro proxy rolling windows on day cadence so regime lookbacks remain
        # comparable to daily-tuned thresholds and factors.
        day_key = self.Time.date()
        market_close_dt = self._get_primary_market_close_time()
        close_buffer_minutes = max(
            int(getattr(config, "REGIME_PROXY_CLOSE_BUFFER_MINUTES", 0)),
            0,
        )
        market_close_cutoff = (
            market_close_dt - timedelta(minutes=close_buffer_minutes)
            if market_close_dt is not None
            else None
        )

        def _append_daily_proxy(symbol: Symbol, window: RollingWindow[float], key: str) -> None:
            if not data.Bars.ContainsKey(symbol):
                return
            # Gate updates to near-close window (supports early-close calendars).
            if market_close_cutoff is not None:
                if self.Time < market_close_cutoff:
                    return
            elif self.Time.hour < 15 or (self.Time.hour == 15 and self.Time.minute < 40):
                return
            if self._daily_proxy_window_last_update.get(key) == day_key:
                return
            window.Add(float(self.Securities[symbol].Close))
            self._daily_proxy_window_last_update[key] = day_key

        _append_daily_proxy(self.spy, self.spy_closes, "SPY")
        _append_daily_proxy(self.rsp, self.rsp_closes, "RSP")
        _append_daily_proxy(self.hyg, self.hyg_closes, "HYG")
        _append_daily_proxy(self.ief, self.ief_closes, "IEF")

        # V2.1: Update VIX value for MR regime filter
        if data.ContainsKey(self.vix):
            vix_data = data[self.vix]
            if vix_data is not None:
                self._current_vix = float(vix_data.Close)
                self._last_vix_update_date = self.Time.date()

        # Update volume rolling windows for MR symbols (daily volume)
        # V6.11: Added SPXL
        if data.Bars.ContainsKey(self.tqqq):
            self.tqqq_volumes.Add(float(data.Bars[self.tqqq].Volume))
        if data.Bars.ContainsKey(self.soxl):
            self.soxl_volumes.Add(float(data.Bars[self.soxl].Volume))
        if data.Bars.ContainsKey(self.spxl):
            self.spxl_volumes.Add(float(data.Bars[self.spxl].Volume))

    def _get_primary_market_close_time(self) -> Optional[datetime]:
        """Resolve today's primary-session close time for SPY exchange hours."""
        try:
            exchange_hours = self.Securities[self.spy].Exchange.Hours
            next_close = exchange_hours.GetNextMarketClose(self.Time, False)
            if next_close.date() == self.Time.date():
                return next_close
            # Compatibility path: some QC engine builds can return next-session close.
            # Re-anchor to today's midnight and ask for the next close from there.
            today_anchor = datetime(self.Time.year, self.Time.month, self.Time.day)
            anchor_close = exchange_hours.GetNextMarketClose(today_anchor, False)
            if anchor_close.date() == self.Time.date():
                return anchor_close
        except Exception:
            return None
        return None

    def _on_observability_checkpoint(self) -> None:
        """Periodic telemetry checkpoint to persist RCA artifacts mid-session."""
        if self.IsWarmingUp:
            return
        self._record_regime_timeline_event(source="PERIODIC_CHECKPOINT")
        self._flush_regime_decision_artifact()
        self._flush_regime_timeline_artifact()
        self._flush_signal_lifecycle_artifact()
        self._flush_router_rejection_artifact()
        self._flush_order_lifecycle_artifact()

    def _ensure_daily_proxy_windows_snapshot(self) -> None:
        """Backfill daily proxy windows from latest closes when intraday feed missed close bar."""
        day_key = self.Time.date()
        symbols = (
            (self.spy, self.spy_closes, "SPY"),
            (self.rsp, self.rsp_closes, "RSP"),
            (self.hyg, self.hyg_closes, "HYG"),
            (self.ief, self.ief_closes, "IEF"),
        )
        for symbol, window, key in symbols:
            if self._daily_proxy_window_last_update.get(key) == day_key:
                continue
            try:
                close_px = float(self.Securities[symbol].Close)
            except Exception:
                continue
            if close_px <= 0:
                continue
            window.Add(close_px)
            self._daily_proxy_window_last_update[key] = day_key

    # =========================================================================
    # SCHEDULED EVENT HANDLERS
    # =========================================================================

    def _on_pre_market_setup(self) -> None:
        """
        Pre-market setup at 09:25 ET.

        Resets daily state (kill switch, panic mode, etc.) for new day.
        Sets equity_prior_close baseline for kill switch calculation.
        Sets SPY prior close for gap filter.
        """
        # Skip during warmup
        if self.IsWarmingUp:
            return
        # Reset daily state (kill switch, panic mode, etc.) at start of new day
        self.risk_engine.reset_daily_state()
        self.scheduler.reset_daily()  # V2.3 FIX: Reset scheduler kill switch flag
        self._kill_switch_handled_today = False  # V2.3: Allow kill switch to trigger again today
        # Reset once-per-day handler guards at daily boundary.
        self._intraday_force_close_ran_date = None
        self._mr_force_close_ran_date = None
        self._eod_processing_ran_date = None
        self._market_close_ran_date = None
        # Reset sample key continuity only; keep prior score for overnight transition detection.
        self._regime_detector_last_update_key = None
        self._regime_detector_last_raw = {}
        self._regime_overlay_ambiguous_bars = 0

        # V2.3 FIX: Reset options engine daily state (entry flags, trade counters)
        current_date_str = str(self.Time.date())
        self.options_engine.reset_daily(current_date_str)

        # V3.0 P0-C: Reset satellite engine daily state
        if hasattr(self, "hedge_engine") and self.hedge_engine:
            self.hedge_engine.reset()
        if hasattr(self, "mr_engine") and self.mr_engine:
            self.mr_engine.reset()

        # V2.3 DEBUG: Log daily reset confirmation (only in live mode)
        if self.LiveMode:
            self.Log(f"DAILY_RESET: All flags cleared at {self.Time}")

        self.equity_prior_close = self.Portfolio.TotalPortfolioValue
        self.risk_engine.set_equity_prior_close(self.equity_prior_close)
        self._last_regime_effective_log_at = None

        # V5.1: Get regime score for Governor decisions
        # Regime Guard checks this for step-up eligibility
        regime_score_for_governor = self.regime_engine.get_previous_score()

        # V2.26/V5.1: Drawdown Governor — check cumulative DD from peak, scale allocations
        # V5.1: Now passes regime_score for Regime Guard check on step-up
        self._governor_scale = self.risk_engine.check_drawdown_governor(
            self.equity_prior_close, regime_score_for_governor
        )

        # V3.0: Regime Override — if bullish regime persists, force Governor step-up
        # V5.1: DISABLED by default (GOVERNOR_REGIME_OVERRIDE_ENABLED = False)
        # This was the root cause of the 2017 death spiral (27 liquidation events)
        # Kept for backward compatibility - method checks config flag internally
        current_date_str = str(self.Time.date())
        if self.risk_engine.check_governor_regime_override(
            regime_score_for_governor, current_date_str
        ):
            # Override was applied, update local scale
            self._governor_scale = self.risk_engine.get_governor_scale()

        # V2.26: Governor at 0% = full shutdown — liquidate all positions
        # V3.1/V6.11: EXEMPT hedge positions (SH) - they were legitimately opened at EOD
        # and should NOT be liquidated the next morning (causes guaranteed hedge losses)
        if self._governor_scale == 0.0 and self.Portfolio.Invested:
            # Check which positions to liquidate (exempt hedges)
            hedge_symbols = {self.sh}
            has_non_hedge = any(
                kvp.Value.Invested and kvp.Value.Symbol not in hedge_symbols
                for kvp in self.Portfolio
            )
            if has_non_hedge:
                self.Log(
                    f"GOVERNOR: SHUTDOWN — Liquidating non-hedge positions | "
                    f"Equity=${self.equity_prior_close:,.0f} | Hedges exempt"
                )
                self._liquidate_all_spread_aware("GOVERNOR_SHUTDOWN", exempt_symbols=hedge_symbols)
                self.portfolio_router.clear_pending()

        # Set SPY prior close for gap filter
        self.spy_prior_close = self.Securities[self.spy].Close
        self.risk_engine.set_spy_prior_close(self.spy_prior_close)

        # V6.14: Seed prior closes on first trading day if needed.
        if self._vix_prior_close <= 0:
            self._vix_prior_close = self._get_vix_level()
        if self._uvxy_prior_close <= 0 and hasattr(self, "uvxy"):
            uvxy_close = self.Securities[self.uvxy].Close
            if uvxy_close > 0:
                self._uvxy_prior_close = uvxy_close

        # V6.14: Pre-market VIX shock ladder (shared protection across options modes)
        self._update_premarket_vix_ladder()
        self._apply_premarket_vix_actions()

        # V6.10 P0: Pre-market ITM check for spread positions
        # Check if any short legs went ITM overnight and queue for close
        if getattr(config, "PREMARKET_ITM_CHECK_ENABLED", True):
            self._check_premarket_itm_shorts()
        # Weekend/holiday ITM guard: queue exits on adverse post-gap open risk.
        self._queue_itm_weekend_gap_exit_signals()

        # V3.0: Schedule dynamic EOD events based on actual market close time
        # Handles early close days (1:00 PM) automatically
        self._schedule_dynamic_eod_events()

    def _schedule_dynamic_eod_events(self) -> None:
        """
        V3.0: Schedule EOD events dynamically based on actual market close time.

        Queries Exchange.Hours.GetNextMarketClose() to determine today's actual
        close time, then schedules MR force close, EOD processing, and market
        close events relative to that time.

        Handles:
            - Normal days (4:00 PM close): Events at 15:15/15:45/16:00
            - Early close days (1:00 PM): Events at 12:15/12:45/13:00
        """
        try:
            # Get actual market close time for today
            market_hours = self.Securities[self.spy].Exchange.Hours
            market_close = market_hours.GetNextMarketClose(self.Time, False)
            is_normal_close = market_close.hour == 16 and market_close.minute == 0

            # #10 fix: avoid duplicate static+dynamic schedules on normal close days.
            # Static fallback schedules already exist for configured force-close/15:45/16:00.
            if not is_normal_close:
                self.scheduler.schedule_dynamic_eod_events(market_close)
            else:
                self.Log(
                    "EOD_SCHEDULE: Normal close detected | Using static fallback schedules only"
                )

            # Also schedule intraday options force close dynamically
            from datetime import timedelta

            opt_offset = getattr(config, "INTRADAY_OPTIONS_OFFSET_MINUTES", 45)
            opt_close_time = market_close - timedelta(minutes=opt_offset)
            static_force_exit = getattr(config, "INTRADAY_FORCE_EXIT_TIME", "15:15")
            static_h, static_m = map(int, static_force_exit.split(":"))
            # Keep dynamic scheduling for early-close sessions or when dynamic time differs.
            if not (
                is_normal_close
                and opt_close_time.hour == static_h
                and opt_close_time.minute == static_m
            ):
                self.Schedule.On(
                    self.DateRules.On(self.Time.year, self.Time.month, self.Time.day),
                    self.TimeRules.At(opt_close_time.hour, opt_close_time.minute),
                    self._on_intraday_options_force_close,
                )

        except Exception as e:
            # Fallback to fixed times if dynamic scheduling fails
            self.Log(f"EOD_SCHEDULE_ERROR: {e} - using fixed 15:45/16:00 fallback")
            # Fixed fallback schedules are registered in _setup_schedules() if needed

    def _check_premarket_itm_shorts(self) -> None:
        """
        V6.10 P0: Pre-market ITM check at 09:25 ET.

        Check all spread short legs BEFORE market open to catch overnight gaps.
        If a short leg went ITM overnight, queue for immediate close at 09:30.

        This prevents assignment losses from overnight gaps that bypass
        the regular trading-hours ITM checks.
        """
        # Skip if options engine not initialized or no spread position
        if not hasattr(self, "options_engine") or self.options_engine is None:
            return

        spreads = self.options_engine.get_spread_positions()
        if not spreads:
            return  # V6.12: Silent return, no log needed for normal case

        # Get current QQQ price (pre-market)
        qqq_price = self.Securities[self.qqq].Price
        if qqq_price <= 0:
            self.Log(f"PREMARKET_ITM_CHECK: Invalid QQQ price {qqq_price} - skipping")
            return

        # Call options engine pre-market check
        exit_signals = []
        for spread in spreads:
            signals = self.options_engine.check_premarket_itm_shorts(
                underlying_price=qqq_price,
                spread_override=spread,
            )
            if signals:
                exit_signals.extend(signals)

        if exit_signals:
            # Queue the exit signals for immediate execution at market open
            self.Log(
                f"PREMARKET_ITM_CHECK: ITM short detected - queuing close for market open | "
                f"QQQ={qqq_price:.2f}"
            )
            # Process through portfolio router for proper execution
            for signal in exit_signals:
                signal.symbol = self._normalize_symbol_str(signal.symbol)
                if signal.metadata:
                    short_leg_sym = signal.metadata.get("spread_short_leg_symbol")
                    if short_leg_sym is not None:
                        signal.metadata["spread_short_leg_symbol"] = self._normalize_symbol_str(
                            short_leg_sym
                        )
                self.portfolio_router.receive_signal(signal)

    def _get_premarket_vix_gap_proxy_pct(self) -> float:
        """
        Estimate overnight VIX gap using UVXY close-to-preopen move.

        QC backtests provide CBOE VIX at daily resolution, so pre-open VIX gap
        is approximated from UVXY gap using ~1.5x relationship.
        """
        if self._uvxy_prior_close <= 0:
            return 0.0
        uvxy_now = self.Securities[self.uvxy].Price if hasattr(self, "uvxy") else 0.0
        if uvxy_now <= 0:
            return 0.0
        uvxy_gap_pct = (uvxy_now - self._uvxy_prior_close) / self._uvxy_prior_close * 100.0
        return uvxy_gap_pct / 1.5

    def _update_premarket_vix_ladder(self) -> None:
        """Set the daily pre-market VIX ladder state (L0-L3)."""
        self._premarket_vix_ladder_level = 0
        self._premarket_vix_ladder_reason = "L0_NORMAL"
        self._premarket_vix_size_mult = 1.0
        self._premarket_vix_entry_block_until = None
        self._premarket_vix_call_block_until = None
        self._premarket_vix_shock_pct = 0.0
        self._premarket_vix_shock_memory_until = None

        if not getattr(config, "PREMARKET_VIX_LADDER_ENABLED", True):
            return

        vix_level = self._get_vix_level()
        vix_gap_proxy_pct = self._get_premarket_vix_gap_proxy_pct()
        vix_shock_level = max(vix_level, self._vix_prior_close * (1.0 + vix_gap_proxy_pct / 100.0))

        if (
            vix_shock_level >= config.PREMARKET_VIX_L3_LEVEL
            or vix_gap_proxy_pct >= config.PREMARKET_VIX_L3_GAP_PCT
        ):
            self._premarket_vix_ladder_level = 3
            self._premarket_vix_size_mult = config.PREMARKET_VIX_L3_SIZE_MULT
            self._premarket_vix_entry_block_until = (
                config.PREMARKET_VIX_L3_ENTRY_BLOCK_UNTIL_HOUR,
                config.PREMARKET_VIX_L3_ENTRY_BLOCK_UNTIL_MINUTE,
            )
            self._premarket_vix_ladder_reason = f"L3_PANIC | VIX={vix_level:.1f} | Shock={vix_shock_level:.1f} | GapProxy={vix_gap_proxy_pct:+.1f}%"
        elif (
            vix_shock_level >= config.PREMARKET_VIX_L2_LEVEL
            or vix_gap_proxy_pct >= config.PREMARKET_VIX_L2_GAP_PCT
        ):
            self._premarket_vix_ladder_level = 2
            self._premarket_vix_size_mult = config.PREMARKET_VIX_L2_SIZE_MULT
            self._premarket_vix_call_block_until = (
                config.PREMARKET_VIX_L2_CALL_BLOCK_UNTIL_HOUR,
                config.PREMARKET_VIX_L2_CALL_BLOCK_UNTIL_MINUTE,
            )
            self._premarket_vix_ladder_reason = f"L2_STRESS | VIX={vix_level:.1f} | Shock={vix_shock_level:.1f} | GapProxy={vix_gap_proxy_pct:+.1f}%"
        elif (
            vix_shock_level >= config.PREMARKET_VIX_L1_LEVEL
            or vix_gap_proxy_pct >= config.PREMARKET_VIX_L1_GAP_PCT
        ):
            self._premarket_vix_ladder_level = 1
            self._premarket_vix_size_mult = config.PREMARKET_VIX_L1_SIZE_MULT
            self._premarket_vix_ladder_reason = f"L1_ELEVATED | VIX={vix_level:.1f} | Shock={vix_shock_level:.1f} | GapProxy={vix_gap_proxy_pct:+.1f}%"

        if self._premarket_vix_ladder_level > 0:
            self.Log(f"PREMARKET_VIX_LADDER: {self._premarket_vix_ladder_reason}")

        # V6.16: Persist overnight panic context into early session for Micro/VASS.
        # Without this, intraday logic can "restart" from high VIX open and misclassify calming.
        if (
            getattr(config, "MICRO_SHOCK_MEMORY_ENABLED", True)
            and self._vix_prior_close > 0
            and self._premarket_vix_ladder_level
            >= getattr(config, "MICRO_SHOCK_MEMORY_MIN_LADDER_LEVEL", 2)
        ):
            self._premarket_vix_shock_pct = max(
                0.0, (vix_shock_level - self._vix_prior_close) / self._vix_prior_close
            )
            self._premarket_vix_shock_memory_until = (
                getattr(config, "MICRO_SHOCK_MEMORY_UNTIL_HOUR", 13),
                getattr(config, "MICRO_SHOCK_MEMORY_UNTIL_MINUTE", 0),
            )
            until_h, until_m = self._premarket_vix_shock_memory_until
            self.Log(
                f"PREMARKET_SHOCK_MEMORY: Active until {until_h:02d}:{until_m:02d} | "
                f"Shock={self._premarket_vix_shock_pct:+.1%} | Ladder={self._premarket_vix_ladder_level}"
            )

    def _apply_premarket_vix_actions(self) -> None:
        apply_premarket_vix_actions(self)

    def _is_premarket_ladder_entry_block_active(self) -> bool:
        """Return True when ladder blocks all new options entries."""
        if self._premarket_vix_entry_block_until is None:
            return False
        block_hour, block_minute = self._premarket_vix_entry_block_until
        return (self.Time.hour, self.Time.minute) < (block_hour, block_minute)

    def _is_premarket_ladder_call_block_active(self) -> bool:
        """Return True when ladder blocks new CALL direction entries."""
        if self._premarket_vix_call_block_until is None:
            return False
        block_hour, block_minute = self._premarket_vix_call_block_until
        return (self.Time.hour, self.Time.minute) < (block_hour, block_minute)

    def _is_premarket_shock_memory_active(self) -> bool:
        """Return True while overnight VIX shock memory should affect intraday decisions."""
        if self._premarket_vix_shock_memory_until is None:
            return False
        block_hour, block_minute = self._premarket_vix_shock_memory_until
        return (self.Time.hour, self.Time.minute) < (block_hour, block_minute)

    def _get_premarket_shock_memory_pct(self) -> float:
        """Get active overnight VIX shock memory as decimal percentage."""
        if not self._is_premarket_shock_memory_active():
            return 0.0
        return max(0.0, self._premarket_vix_shock_pct)

    def _normalize_symbol_str(self, symbol) -> str:
        """Normalize QC Symbol/string-like values to plain string for TargetWeight contract."""
        if symbol is None:
            return ""
        if isinstance(symbol, str):
            return symbol.strip().upper()
        try:
            return str(symbol).strip().upper()
        except Exception:
            return ""

    def _clear_intraday_close_guard(self, symbol: str) -> None:
        """Clear intraday close-in-progress guard + per-day submit marker for a symbol."""
        symbol_norm = self._normalize_symbol_str(symbol)
        if not symbol_norm:
            return
        self._intraday_close_in_progress_symbols.discard(symbol_norm)
        self._intraday_force_exit_submitted_symbols.pop(symbol_norm, None)

    def _get_valid_options_chain(self, option_chains: Any, mode_label: str):
        """Return QQQ option chain when present and non-empty, else None with diagnostics."""
        chain = (
            option_chains[self._qqq_option_symbol]
            if self._qqq_option_symbol in option_chains
            else None
        )
        if chain is None:
            return None
        try:
            if not list(chain):
                self.Log(
                    f"VASS_REJECTION_GHOST: No contracts in {mode_label} chain | "
                    "Strike range may be too narrow or data gap"
                )
                return None
        except Exception as e:
            self.Log(f"OPTIONS_CHAIN_ERROR: Failed to iterate chain: {e}")
            return None
        return chain

    def _get_options_market_snapshot(self) -> Tuple[float, float, float, float]:
        """Shared market snapshot used by EOD + intraday options scans."""
        qqq_price = self.Securities[self.qqq].Price
        adx_value = self.qqq_adx.Current.Value
        ma200_value = self.qqq_sma200.Current.Value
        ma50_value = (
            self.qqq_sma50.Current.Value
            if hasattr(self, "qqq_sma50") and self.qqq_sma50.IsReady
            else 0.0
        )
        return qqq_price, adx_value, ma200_value, ma50_value

    def _should_log_intraday_diag(self, reason_key: str) -> bool:
        """Throttle high-frequency intraday candidate/drop telemetry in backtests."""
        interval_min = int(getattr(config, "REJECTION_EVENT_LOG_THROTTLE_MINUTES", 5))
        now = self.Time
        last = self._last_intraday_diag_log_by_key.get(reason_key)
        if last is not None:
            elapsed = (now - last).total_seconds() / 60.0
            if elapsed < interval_min:
                return False
        self._last_intraday_diag_log_by_key[reason_key] = now
        return True

    def _should_log_high_frequency_backtest(
        self,
        *,
        config_flag: str,
        category: str,
        reason_key: str,
        default_backtest_enabled: bool = False,
    ) -> bool:
        """Backtest-aware sampled logging gate for high-volume diagnostic categories."""
        is_live = bool(hasattr(self, "LiveMode") and self.LiveMode)
        if not is_live and self._should_log_backtest_category(
            config_flag, default_backtest_enabled
        ):
            return True

        day_token = (
            self.Time.strftime("%Y-%m-%d")
            if hasattr(self, "Time") and self.Time is not None
            else "UNKNOWN_DAY"
        )
        category_token = str(category or "GENERAL").strip().upper() or "GENERAL"
        reason_token = str(reason_key or "GENERIC").strip().upper()[:120] or "GENERIC"
        sample_key = f"{day_token}|{category_token}|{reason_token}"
        seen = int(self._high_freq_log_seen_counts.get(sample_key, 0)) + 1
        self._high_freq_log_seen_counts[sample_key] = seen

        first_n = max(0, int(getattr(config, "LOG_HIGHFREQ_SAMPLE_FIRST_N_PER_KEY", 1)))
        every_n_key = (
            "LOG_HIGHFREQ_SAMPLE_EVERY_N_LIVE" if is_live else "LOG_HIGHFREQ_SAMPLE_EVERY_N"
        )
        every_n = max(0, int(getattr(config, every_n_key, 0)))
        should_log = seen <= first_n or (every_n > 0 and seen % every_n == 0)
        if not should_log:
            self._high_freq_log_suppressed_counts[category_token] = (
                int(self._high_freq_log_suppressed_counts.get(category_token, 0)) + 1
            )
        return should_log

    def _log_high_frequency_event(
        self,
        *,
        config_flag: str,
        category: str,
        reason_key: str,
        message: str,
        default_backtest_enabled: bool = False,
    ) -> bool:
        """Emit high-frequency diagnostic logs with backtest sampling controls."""
        if not self._should_log_high_frequency_backtest(
            config_flag=config_flag,
            category=category,
            reason_key=reason_key,
            default_backtest_enabled=default_backtest_enabled,
        ):
            return False
        self.Log(message)
        return True

    def _mark_intraday_signal_event(self, event_type: str, signal_id: Optional[str]) -> bool:
        """Return True only on first observation of a signal-id event type."""
        sid = str(signal_id or "").strip()
        if not sid:
            return True
        event_key = str(event_type or "").upper()
        if event_key == "CANDIDATE":
            store = self._diag_intraday_candidate_ids_logged
        elif event_key == "APPROVED":
            store = self._diag_intraday_approved_ids_logged
        else:
            store = self._diag_intraday_dropped_ids_logged
        if sid in store:
            return False
        store.add(sid)
        return True

    def _append_observability_record(
        self,
        records: List[Dict[str, Any]],
        overflow_attr: str,
        max_rows: int,
        overflow_log_prefix: str,
        row: Dict[str, Any],
    ) -> None:
        """Append bounded observability row with single overflow warning."""
        if len(records) >= max_rows:
            if not bool(getattr(self, overflow_attr, False)):
                self.Log(
                    f"{overflow_log_prefix}: buffer full at {max_rows} rows | further rows dropped"
                )
                setattr(self, overflow_attr, True)
            return
        records.append(row)

    def _record_signal_lifecycle_event(
        self,
        engine: str,
        event: str,
        signal_id: str,
        trace_id: str = "",
        direction: str = "",
        strategy: str = "",
        code: str = "",
        gate_name: str = "",
        reason: str = "",
        contract_symbol: str = "",
    ) -> None:
        """Capture signal lifecycle telemetry in structured artifact rows."""
        if not bool(getattr(config, "SIGNAL_LIFECYCLE_OBSERVABILITY_ENABLED", True)):
            return
        max_rows = int(getattr(config, "SIGNAL_LIFECYCLE_OBSERVABILITY_MAX_ROWS", 50000))
        self._append_observability_record(
            records=self._signal_lifecycle_records,
            overflow_attr="_signal_lifecycle_overflow_logged",
            max_rows=max_rows,
            overflow_log_prefix="SIGNAL_LIFECYCLE_OBSERVABILITY",
            row={
                "time": self.Time.strftime("%Y-%m-%d %H:%M:%S"),
                "engine": str(engine or "UNKNOWN").upper(),
                "event": str(event or "").upper(),
                "signal_id": str(signal_id or ""),
                "trace_id": str(trace_id or ""),
                "direction": str(direction or ""),
                "strategy": str(strategy or ""),
                "code": str(code or ""),
                "gate_name": str(gate_name or ""),
                "reason": str(reason or ""),
                "contract_symbol": str(contract_symbol or ""),
            },
        )

    def _log_intraday_signal_dropped(
        self,
        signal_id: str,
        code: str,
        reason: str,
        retry_hint: Optional[str],
        direction: Optional[OptionDirection],
        strategy: Optional[IntradayStrategy],
        contract_symbol: str,
        validation_detail: Optional[str] = None,
    ) -> bool:
        """Emit standardized MICRO dropped-signal telemetry line."""
        if not self._mark_intraday_signal_event("DROPPED", signal_id):
            return False
        validation_detail_fragment = (
            f"ValidationDetail={validation_detail} | " if validation_detail else ""
        )
        self._record_intraday_drop_reason(code, strategy)
        strategy_name = strategy.value if strategy is not None else ""
        direction_name = direction.value if direction is not None else "NONE"
        drop_message = (
            f"INTRADAY_SIGNAL_DROPPED: SignalId={signal_id} | Candidate rejected before order | "
            f"Code={code} | "
            f"Reason={reason} | RetryHint={retry_hint} | "
            f"{validation_detail_fragment}"
            f"Dir={direction_name} | "
            f"Strategy={strategy_name or 'NONE'} | "
            f"Contract={contract_symbol}"
        )
        self._log_high_frequency_event(
            config_flag="LOG_INTRADAY_DROPPED_BACKTEST_ENABLED",
            category="INTRADAY_DROPPED",
            reason_key=f"{self._canonical_options_reason_code(code)}|{strategy_name or 'NONE'}",
            message=drop_message,
        )
        self._record_signal_lifecycle_event(
            engine=self._intraday_engine_bucket_from_strategy(strategy),
            event="DROPPED",
            signal_id=signal_id,
            direction=direction_name if direction is not None else "",
            strategy=strategy_name,
            code=self._canonical_options_reason_code(code),
            gate_name=self._canonical_options_reason_code(code),
            reason=reason,
            contract_symbol=contract_symbol,
        )
        return True

    def _resolve_vass_direction_context(
        self,
        regime_score: float,
        size_multiplier: float,
        bull_profile_log_prefix: str,
        clamp_log_prefix: str,
        shock_log_prefix: str,
        transition_ctx: Optional[Dict[str, Any]] = None,
    ) -> Optional[Tuple[OptionDirection, str, Any, float, bool, str, str, str, str,]]:
        """
        Resolve VASS direction + sizing context with shared guard rails.

        Returns:
            Tuple of (direction, direction_str, overlay_state, size_multiplier,
            has_conviction, conviction_reason, macro_direction, resolve_reason,
            resolved_direction_str) or None when blocked/no-trade.
        """
        current_date_str = self.Time.strftime("%Y-%m-%d")
        transition_ctx = (
            dict(transition_ctx)
            if isinstance(transition_ctx, dict)
            else self._get_transition_execution_context()
        )
        regime_for_vass = float(
            transition_ctx.get("transition_score", regime_score) or regime_score
        )
        block_gate, block_reason = self.options_engine.evaluate_transition_policy_block(
            engine="VASS",
            direction=None,
            transition_ctx=transition_ctx,
        )
        if block_gate:
            self._record_regime_decision_event(
                engine="VASS",
                engine_decision="BLOCK",
                strategy_attempted="VASS_DIRECTION",
                gate_name=block_gate,
                threshold_snapshot={"overlay": transition_ctx.get("transition_overlay", "")},
                context=transition_ctx,
            )
            self.Log(
                f"VASS_TRANSITION_BLOCK: {block_reason} | "
                f"Eff={float(transition_ctx.get('effective_score', regime_score)):.1f} | "
                f"Delta={float(transition_ctx.get('delta', 0.0)):+.1f} | "
                f"MOM={float(transition_ctx.get('momentum_roc', 0.0)):+.2%}"
            )
            return None

        vix_level_for_vass = self._get_vix_level()
        self.options_engine.update_iv_sensor(vix_level_for_vass, current_date_str)
        (
            has_conviction,
            conviction_direction,
            conviction_reason,
        ) = self.options_engine.get_iv_conviction()
        macro_direction = self.options_engine.get_macro_direction(regime_for_vass)
        allow_macro_veto = True
        if has_conviction and conviction_direction == "BEARISH":
            allow_macro_veto, veto_reason = self.options_engine.get_iv_bearish_veto_status()
            if not allow_macro_veto:
                has_conviction = False
                conviction_direction = None
                conviction_reason = f"{conviction_reason} | HARD_VETO_BLOCK={veto_reason}"
        overlay_state = self.options_engine.get_regime_overlay_state(
            vix_current=vix_level_for_vass, regime_score=regime_for_vass
        )
        should_trade, resolved_direction, resolve_reason = self.options_engine.resolve_trade_signal(
            engine="VASS",
            engine_direction=conviction_direction,
            engine_conviction=has_conviction,
            macro_direction=macro_direction,
            conviction_strength=None,
            overlay_state=overlay_state,
            allow_macro_veto=allow_macro_veto,
        )
        if not should_trade:
            self._record_regime_decision_event(
                engine="VASS",
                engine_decision="BLOCK",
                strategy_attempted="VASS_DIRECTION",
                gate_name="VASS_RESOLVER_NO_TRADE",
                threshold_snapshot={"resolve_reason": resolve_reason},
                context=transition_ctx,
            )
            if "E_OVERLAY_STRESS_BULL_BLOCK" in resolve_reason:
                self._diag_overlay_block_count += 1
            return None

        if (
            bool(getattr(config, "VASS_BULL_PROFILE_BEARISH_BLOCK_ENABLED", True))
            and resolved_direction == "BEARISH"
            and float(regime_for_vass)
            >= float(getattr(config, "VASS_BULL_PROFILE_REGIME_MIN", 70.0))
            and str(overlay_state).upper() in {"NORMAL", "RECOVERY"}
        ):
            self._record_regime_decision_event(
                engine="VASS",
                engine_decision="BLOCK",
                strategy_attempted="VASS_BEARISH",
                gate_name="VASS_BULL_PROFILE_BEARISH_BLOCK",
                threshold_snapshot={
                    "regime_min": float(getattr(config, "VASS_BULL_PROFILE_REGIME_MIN", 70.0))
                },
                context=transition_ctx,
            )
            self.Log(
                f"{bull_profile_log_prefix}: Bearish VASS blocked in strong bull profile | "
                f"Regime={float(regime_for_vass):.1f} | Overlay={overlay_state}"
            )
            return None

        if (
            resolved_direction == "BULLISH"
            and str(macro_direction).upper() == "NEUTRAL"
            and self._current_vix
            >= float(getattr(config, "VASS_NEUTRAL_BULL_OVERRIDE_MAX_VIX", 20.0))
        ):
            self._record_regime_decision_event(
                engine="VASS",
                engine_decision="BLOCK",
                strategy_attempted="VASS_BULLISH",
                gate_name="VASS_NEUTRAL_BULL_OVERRIDE_MAX_VIX",
                threshold_snapshot={
                    "vix_limit": float(getattr(config, "VASS_NEUTRAL_BULL_OVERRIDE_MAX_VIX", 20.0))
                },
                context=transition_ctx,
            )
            self.Log(
                f"{clamp_log_prefix}: Neutral macro + elevated VIX blocks bullish override | "
                f"VIX={self._current_vix:.1f} >= {float(getattr(config, 'VASS_NEUTRAL_BULL_OVERRIDE_MAX_VIX', 20.0)):.1f} | "
                f"Resolve={resolve_reason}"
            )
            return None

        resolved_option_dir = (
            OptionDirection.CALL if resolved_direction == "BULLISH" else OptionDirection.PUT
        )
        block_gate, block_reason = self.options_engine.evaluate_transition_policy_block(
            engine="VASS",
            direction=resolved_option_dir,
            transition_ctx=transition_ctx,
        )
        if block_gate:
            self._record_regime_decision_event(
                engine="VASS",
                engine_decision="BLOCK",
                strategy_attempted=f"VASS_{resolved_direction}",
                gate_name=block_gate,
                context=transition_ctx,
            )
            self.Log(
                f"VASS_TRANSITION_BLOCK: {block_reason} | "
                f"Eff={float(transition_ctx.get('effective_score', regime_for_vass)):.1f} | "
                f"Delta={float(transition_ctx.get('delta', 0.0)):+.1f} | "
                f"MOM={float(transition_ctx.get('momentum_roc', 0.0)):+.2%}"
            )
            return None

        if "NEUTRAL_ALIGNED_HALF" in resolve_reason:
            size_multiplier *= config.NEUTRAL_ALIGNED_SIZE_MULT

        if (
            getattr(config, "SHOCK_MEMORY_FORCE_BEARISH_VASS", True)
            and self._is_premarket_shock_memory_active()
            and resolved_direction == "BULLISH"
        ):
            resolved_direction = "BEARISH"
            resolve_reason = f"{resolve_reason} | SHOCK_MEMORY_FORCE_BEARISH"
            self.Log(
                f"{shock_log_prefix}: Forcing BEARISH | "
                f"Shock={self._get_premarket_shock_memory_pct():+.1%} | "
                f"Reason={resolve_reason}"
            )

        if resolved_direction == "BULLISH":
            direction = OptionDirection.CALL
            direction_str = "BULLISH"
        else:
            direction = OptionDirection.PUT
            direction_str = "BEARISH"

        self._record_regime_decision_event(
            engine="VASS",
            engine_decision="ALLOW",
            strategy_attempted=f"VASS_{direction_str}",
            gate_name="VASS_DIRECTION_RESOLVED",
            threshold_snapshot={
                "macro_direction": str(macro_direction),
                "overlay_state": str(overlay_state),
            },
            context=transition_ctx,
        )

        return (
            direction,
            direction_str,
            overlay_state,
            size_multiplier,
            has_conviction,
            conviction_reason,
            str(macro_direction),
            resolve_reason,
            resolved_direction,
        )

    def _attach_option_trace_metadata(
        self,
        signal: TargetWeight,
        source: str,
    ) -> TargetWeight:
        """Attach trace metadata for end-to-end rejection attribution."""
        if signal.metadata is None:
            signal.metadata = {}
        symbol_tail = self._normalize_symbol_str(signal.symbol)[-12:]
        trace_id = (
            f"{source}_{self.Time.strftime('%Y%m%d_%H%M%S')}_{symbol_tail}"
            if symbol_tail
            else f"{source}_{self.Time.strftime('%Y%m%d_%H%M%S')}"
        )
        signal.metadata["trace_id"] = trace_id
        signal.metadata["trace_source"] = source
        signal.metadata["trace_time"] = str(self.Time)
        return signal

    def _get_option_holding_quantity(self, symbol) -> int:
        """Get signed live option holding quantity by symbol string."""
        holding, _ = self._find_portfolio_holding(symbol, security_type=SecurityType.Option)
        if holding is None or not holding.Invested:
            return 0
        try:
            return int(holding.Quantity)
        except Exception:
            return 0

    def _find_portfolio_holding(
        self,
        symbol,
        security_type: Optional[SecurityType] = None,
    ):
        """Best-effort lookup of a live holding by normalized symbol key."""
        symbol_norm = self._normalize_symbol_str(symbol)
        if not symbol_norm:
            return None, None
        try:
            for kvp in self.Portfolio:
                holding = kvp.Value
                if security_type is not None and holding.Symbol.SecurityType != security_type:
                    continue
                if self._normalize_symbol_str(holding.Symbol) != symbol_norm:
                    continue
                return holding, holding.Symbol
        except Exception:
            return None, None
        return None, None

    def _infer_option_engine_bucket_for_symbol(
        self,
        symbol: Any,
        tag_hint: str = "",
        strategy_hint: Optional[Any] = None,
    ) -> str:
        """Best-effort engine attribution for option-exit telemetry isolation."""
        hint_text = str(tag_hint or "").upper()
        if "VASS" in hint_text:
            return "VASS"
        if "ITM" in hint_text:
            return "ITM"
        if "MICRO" in hint_text:
            return "MICRO"

        strategy_bucket = self._intraday_engine_bucket_from_strategy(strategy_hint)
        if strategy_bucket in {"MICRO", "ITM"}:
            return strategy_bucket

        symbol_norm = self._normalize_symbol_str(symbol)
        if not symbol_norm:
            return "OTHER"

        if symbol_norm in getattr(self, "_micro_open_symbols", set()):
            return "MICRO"

        try:
            for intraday_pos in self.options_engine.get_intraday_positions():
                if intraday_pos is None or intraday_pos.contract is None:
                    continue
                pos_symbol = self._normalize_symbol_str(intraday_pos.contract.symbol)
                if pos_symbol != symbol_norm:
                    continue
                bucket = self._intraday_engine_bucket_from_strategy(
                    getattr(intraday_pos, "entry_strategy", "")
                )
                if bucket in {"MICRO", "ITM"}:
                    return bucket
        except Exception:
            pass

        try:
            for spread in self.options_engine.get_spread_positions():
                if spread is None:
                    continue
                long_norm = self._normalize_symbol_str(getattr(spread.long_leg, "symbol", ""))
                short_norm = self._normalize_symbol_str(getattr(spread.short_leg, "symbol", ""))
                if symbol_norm in {long_norm, short_norm}:
                    return "VASS"
        except Exception:
            pass

        recent_tag = str(
            self._get_recent_symbol_fill_tag(symbol_norm, max_age_minutes=480) or ""
        ).upper()
        if "VASS" in recent_tag:
            return "VASS"
        if "ITM" in recent_tag:
            return "ITM"
        if "MICRO" in recent_tag:
            return "MICRO"
        return "OTHER"

    def _build_option_exit_tag(
        self,
        base_reason: str,
        symbol: Any,
        engine_hint: str = "",
        tag_hint: str = "",
        strategy_hint: Optional[Any] = None,
    ) -> str:
        """Attach stable engine prefix to direct option-exit tags."""
        reason = str(base_reason or "OPTION_EXIT").strip().upper()
        if not reason:
            reason = "OPTION_EXIT"
        for prefix in ("VASS:", "MICRO:", "ITM:", "OTHER:"):
            if reason.startswith(prefix):
                return reason

        bucket = str(engine_hint or "").upper().strip()
        if bucket not in {"VASS", "MICRO", "ITM", "OTHER"}:
            bucket = self._infer_option_engine_bucket_for_symbol(
                symbol=symbol,
                tag_hint=tag_hint,
                strategy_hint=strategy_hint,
            )
        return f"{bucket}:{reason}"

    def _cancel_all_open_orders_for_symbol(self, symbol: Any, reason: str = "") -> Tuple[int, int]:
        """Cancel all open orders for symbol before direct option-close submission."""
        symbol_norm = self._normalize_symbol_str(symbol)
        if not symbol_norm:
            return 0, 0

        canceled = 0
        cancel_errors = 0
        try:
            for open_order in list(self.Transactions.GetOpenOrders()):
                try:
                    if self._normalize_symbol_str(open_order.Symbol) != symbol_norm:
                        continue
                    order_id = int(getattr(open_order, "Id", 0) or 0)
                    if order_id <= 0:
                        order_id = int(getattr(open_order, "OrderId", 0) or 0)
                    if order_id <= 0:
                        continue
                    self.Transactions.CancelOrder(order_id)
                    canceled += 1
                except Exception:
                    cancel_errors += 1
        except Exception:
            cancel_errors += 1

        if canceled > 0 or cancel_errors > 0:
            self.Log(
                f"EXIT_PRECLEAR_CANCEL: Symbol={symbol_norm} | Canceled={canceled} | "
                f"CancelErrors={cancel_errors} | Reason={reason or 'NA'}"
            )
        return canceled, cancel_errors

    def _submit_option_close_market_order(
        self,
        symbol: Any,
        quantity: int,
        reason: str,
        engine_hint: str = "",
        tag_hint: str = "",
        strategy_hint: Optional[Any] = None,
    ):
        """Direct options close helper with pre-cancel and engine-scoped telemetry tag."""
        try:
            close_qty = int(quantity)
        except Exception:
            close_qty = 0
        if close_qty == 0:
            return None

        symbol_norm = self._normalize_symbol_str(symbol)
        if not symbol_norm:
            return None

        try:
            self.oco_manager.cancel_by_symbol(symbol_norm, reason=f"{reason}_PRECLEAR")
        except Exception:
            pass

        canceled, cancel_errors = self._cancel_all_open_orders_for_symbol(
            symbol=symbol_norm, reason=reason
        )
        remaining = 0
        try:
            for open_order in self.Transactions.GetOpenOrders():
                if self._normalize_symbol_str(open_order.Symbol) == symbol_norm:
                    remaining += 1
        except Exception:
            remaining = 0
        if canceled > 0 or cancel_errors > 0 or remaining > 0:
            self.Log(
                f"EXIT_PRECLEAR_DIRECT: Symbol={symbol_norm} | Canceled={canceled} | "
                f"Remaining={remaining} | CancelErrors={cancel_errors} | Submit=True"
            )

        order_tag = self._build_option_exit_tag(
            base_reason=reason,
            symbol=symbol_norm,
            engine_hint=engine_hint,
            tag_hint=tag_hint,
            strategy_hint=strategy_hint,
        )
        ticket = self.MarketOrder(symbol, close_qty, tag=order_tag)
        try:
            if ticket is not None and hasattr(ticket, "OrderId"):
                order_id = int(ticket.OrderId)
                self._record_order_tag_map(
                    order_id,
                    symbol_norm,
                    order_tag,
                    source="DIRECT_OPTION_CLOSE",
                )
                self._record_order_lifecycle_event(
                    status="SUBMITTED",
                    order_id=order_id,
                    symbol=symbol_norm,
                    quantity=int(close_qty),
                    fill_price=0.0,
                    order_type="MARKET",
                    order_tag=order_tag,
                    trace_id=self._extract_trace_id_from_tag(order_tag),
                    message=str(reason or ""),
                    source="DIRECT_OPTION_CLOSE",
                )
        except Exception:
            pass
        return ticket

    def _resolve_pending_exit_tracker_key(self, symbol: str) -> Optional[str]:
        """Resolve pending-exit tracker key by normalized symbol."""
        symbol_norm = self._normalize_symbol_str(symbol)
        if not symbol_norm:
            return None
        if symbol in self._pending_exit_orders:
            return symbol
        if symbol_norm in self._pending_exit_orders:
            return symbol_norm
        for key in self._pending_exit_orders.keys():
            if self._normalize_symbol_str(key) == symbol_norm:
                return key
        return None

    def _should_hold_intraday_symbol_overnight(self, symbol: str) -> bool:
        """Return True when symbol matches an active hold-enabled intraday ITM position."""
        if not hasattr(self, "options_engine") or self.options_engine is None:
            return False
        symbol_str = self._normalize_symbol_str(symbol)
        if not symbol_str:
            return False
        intraday_pos = None
        for p in self.options_engine.get_intraday_positions():
            if p is None or p.contract is None:
                continue
            pos_symbol = self._normalize_symbol_str(p.contract.symbol)
            if pos_symbol == symbol_str:
                intraday_pos = p
                break
        if intraday_pos is None:
            return False
        try:
            return bool(self.options_engine.should_hold_intraday_overnight(intraday_pos))
        except Exception:
            return False

    def _is_itm_weekend_firewall_day(self) -> bool:
        """Return True on Friday/holiday-eve sessions where weekend carry risk applies."""
        if not bool(getattr(config, "ITM_WEEKEND_GUARD_ENABLED", True)):
            return False
        try:
            return bool(is_expiration_firewall_day(self))
        except Exception:
            return False

    def _is_itm_weekend_entry_cutoff_active(self) -> bool:
        """Block new ITM entries late on weekend-risk sessions."""
        if not self._is_itm_weekend_firewall_day():
            return False
        cutoff_hour = int(getattr(config, "ITM_WEEKEND_ENTRY_CUTOFF_HOUR", 13))
        cutoff_min = int(getattr(config, "ITM_WEEKEND_ENTRY_CUTOFF_MINUTE", 30))
        now_min = self.Time.hour * 60 + self.Time.minute
        cutoff_total = cutoff_hour * 60 + cutoff_min
        return now_min >= cutoff_total

    def _get_itm_weekend_vix_5d_change(self) -> Optional[float]:
        """Best-effort VIX 5D change for weekend risk screening."""
        try:
            if not hasattr(self, "options_engine") or self.options_engine is None:
                return None
            if not hasattr(self.options_engine, "_iv_sensor"):
                return None
            value = self.options_engine._iv_sensor.get_vix_5d_change()
            if value is None:
                return None
            return float(value)
        except Exception:
            return None

    def _get_intraday_position_entry_price(self, symbol: str, intraday_pos: Any) -> float:
        """Resolve best entry-price source for intraday weekend guards."""
        snapshot = self._intraday_entry_snapshot.get(symbol, {})
        if snapshot and snapshot.get("entry_price", 0) > 0:
            return float(snapshot.get("entry_price", 0.0))
        entry_price = float(getattr(intraday_pos, "entry_price", 0.0) or 0.0)
        if entry_price > 0:
            return entry_price
        holding, _ = self._find_portfolio_holding(symbol, security_type=SecurityType.Option)
        if holding and getattr(holding, "AveragePrice", 0) > 0:
            return float(holding.AveragePrice)
        return 0.0

    def _evaluate_itm_weekend_hold_risk(
        self,
        *,
        symbol: str,
        intraday_pos: Any,
        current_price: float,
        current_vix: float,
        vix_5d_change: Optional[float],
    ) -> Optional[str]:
        """
        Return weekend guard reason when ITM carry is too risky, else None.

        Keep logic simple and explicit:
        - Require minimum live DTE,
        - reject high vol / rising vol,
        - reject thesis break,
        - require profit cushion into weekend.
        """
        symbol_norm = self._normalize_symbol_str(symbol)
        entry_price = self._get_intraday_position_entry_price(symbol_norm, intraday_pos)
        if entry_price <= 0 or current_price <= 0:
            return "INVALID_PRICE_CONTEXT"

        pnl_pct = (current_price - entry_price) / entry_price
        min_live_dte = int(getattr(config, "ITM_WEEKEND_MIN_LIVE_DTE_TO_HOLD", 10))
        max_vix = float(getattr(config, "ITM_WEEKEND_VIX_MAX_TO_HOLD", 22.0))
        max_vix_5d = float(getattr(config, "ITM_WEEKEND_VIX_5D_MAX_TO_HOLD", 0.08))
        min_cushion = float(getattr(config, "ITM_WEEKEND_MIN_PNL_CUSHION_TO_HOLD", 0.10))

        live_dte = None
        try:
            live_dte = self.options_engine._get_position_live_dte(intraday_pos)
        except Exception:
            live_dte = None
        if live_dte is None or int(live_dte) < min_live_dte:
            return f"LIVE_DTE_LOW ({live_dte}<{min_live_dte})"
        if float(current_vix) >= max_vix:
            return f"VIX_HIGH ({float(current_vix):.1f}>={max_vix:.1f})"
        if vix_5d_change is not None and float(vix_5d_change) >= max_vix_5d:
            return f"VIX_5D_RISING ({float(vix_5d_change):+.1%}>={max_vix_5d:.0%})"
        if self._is_itm_overnight_thesis_broken(symbol_norm):
            return "THESIS_BROKEN"
        if pnl_pct < min_cushion:
            return f"LOW_PNL_CUSHION ({pnl_pct:+.1%}<{min_cushion:.0%})"
        return None

    def _collect_itm_weekend_firewall_signals(self, current_vix: float) -> List[TargetWeight]:
        """Build Friday/holiday-eve ITM close signals for risky carries."""
        if not self._is_itm_weekend_firewall_day():
            return []
        signals: List[TargetWeight] = []
        vix_5d_change = self._get_itm_weekend_vix_5d_change()

        for intraday_pos in self.options_engine.get_intraday_positions():
            if intraday_pos is None or intraday_pos.contract is None:
                continue
            strategy = str(getattr(intraday_pos, "entry_strategy", "") or "").upper()
            if strategy != "ITM_MOMENTUM":
                continue
            if not self.options_engine.should_hold_intraday_overnight(intraday_pos):
                continue

            symbol = self._normalize_symbol_str(intraday_pos.contract.symbol)
            live_qty = abs(self._get_option_holding_quantity(symbol))
            if live_qty <= 0:
                continue

            current_price = self._get_option_mark_price(
                symbol,
                fallback=float(getattr(intraday_pos, "entry_price", 0.0) or 0.0),
            )
            if current_price <= 0:
                continue

            weekend_reason = self._evaluate_itm_weekend_hold_risk(
                symbol=symbol,
                intraday_pos=intraday_pos,
                current_price=current_price,
                current_vix=current_vix,
                vix_5d_change=vix_5d_change,
            )
            if not weekend_reason:
                continue

            signals.append(
                TargetWeight(
                    symbol=symbol,
                    target_weight=0.0,
                    source="OPT_INTRADAY",
                    urgency=Urgency.IMMEDIATE,
                    reason=f"FRIDAY_FIREWALL_ITM: {weekend_reason}",
                    requested_quantity=live_qty,
                    metadata={
                        "intraday_strategy": "ITM_MOMENTUM",
                        "weekend_guard": "FRIDAY",
                    },
                )
            )
            self.Log(
                f"FRIDAY_FIREWALL_ITM: Closing {symbol} | Reason={weekend_reason} | "
                f"Qty={live_qty} | VIX={current_vix:.1f}"
            )
        return signals

    def _queue_itm_weekend_gap_exit_signals(self) -> None:
        """Queue post-weekend/holiday ITM exits on adverse gap or vol shock."""
        if not bool(getattr(config, "ITM_WEEKEND_GAP_EXIT_ENABLED", True)):
            return
        if self._last_market_close_check is None:
            return
        days_gap = (self.Time.date() - self._last_market_close_check).days
        if days_gap < 3:
            return

        qqq_prior_close = float(self.Securities[self.qqq].Close or 0.0)
        qqq_now = float(self.Securities[self.qqq].Price or 0.0)
        if qqq_prior_close <= 0 or qqq_now <= 0:
            return

        adverse_gap_threshold = float(getattr(config, "ITM_WEEKEND_GAP_ADVERSE_PCT", 0.01))
        vix_shock_threshold = float(getattr(config, "ITM_WEEKEND_GAP_VIX_SHOCK_PCT", 0.15))
        qqq_gap_pct = (qqq_now - qqq_prior_close) / qqq_prior_close
        vix_shock_pct = max(0.0, float(self._get_premarket_vix_gap_proxy_pct()) / 100.0)

        queued = 0
        for intraday_pos in self.options_engine.get_intraday_positions():
            if intraday_pos is None or intraday_pos.contract is None:
                continue
            strategy = str(getattr(intraday_pos, "entry_strategy", "") or "").upper()
            if strategy != "ITM_MOMENTUM":
                continue
            if not self.options_engine.should_hold_intraday_overnight(intraday_pos):
                continue

            symbol = self._normalize_symbol_str(intraday_pos.contract.symbol)
            is_call = "C" in symbol
            is_put = "P" in symbol
            adverse_gap = (is_call and qqq_gap_pct <= -adverse_gap_threshold) or (
                is_put and qqq_gap_pct >= adverse_gap_threshold
            )
            vix_shock = vix_shock_pct >= vix_shock_threshold
            if not adverse_gap and not vix_shock:
                continue

            live_qty = abs(self._get_option_holding_quantity(symbol))
            if live_qty <= 0:
                continue

            reasons = []
            if adverse_gap:
                reasons.append(f"ADVERSE_GAP {qqq_gap_pct:+.2%}")
            if vix_shock:
                reasons.append(f"VIX_SHOCK {vix_shock_pct:+.1%}")
            reason_text = " + ".join(reasons) if reasons else "POST_GAP_RISK"

            self.portfolio_router.receive_signal(
                TargetWeight(
                    symbol=symbol,
                    target_weight=0.0,
                    source="OPT_INTRADAY",
                    urgency=Urgency.IMMEDIATE,
                    reason=f"ITM_WEEKEND_GAP_EXIT: {reason_text}",
                    requested_quantity=live_qty,
                    metadata={
                        "intraday_strategy": "ITM_MOMENTUM",
                        "weekend_guard": "POST_GAP",
                    },
                )
            )
            queued += 1
            self.Log(
                f"ITM_WEEKEND_GAP_EXIT_QUEUED: {symbol} | Reason={reason_text} | "
                f"Qty={live_qty} | GapDays={days_gap}"
            )

        if queued > 0:
            self.Log(
                f"ITM_WEEKEND_GAP_EXIT: Queued={queued} | QQQ_Gap={qqq_gap_pct:+.2%} | "
                f"VIX_Shock={vix_shock_pct:+.1%} | GapDays={days_gap}"
            )

    def _get_option_mark_price(self, symbol: str, fallback: float = 0.0) -> float:
        """Best-effort option mark for EOD hold checks."""
        try:
            if self.Securities.ContainsKey(symbol):
                sec = self.Securities[symbol]
                last = float(sec.Price) if sec.Price is not None else 0.0
                if last > 0:
                    return last
                bid = float(sec.BidPrice) if sec.BidPrice is not None else 0.0
                ask = float(sec.AskPrice) if sec.AskPrice is not None else 0.0
                if bid > 0 and ask > 0:
                    return (bid + ask) / 2.0
        except Exception:
            pass
        return fallback

    def _is_itm_overnight_thesis_broken(self, symbol: str) -> bool:
        """Return True when ITM directional thesis is broken vs SMA20 band."""
        symbol_norm = self._normalize_symbol_str(symbol)
        if not symbol_norm:
            return False
        is_call = "C" in symbol_norm
        is_put = "P" in symbol_norm
        if not is_call and not is_put:
            return False

        qqq_sma20 = getattr(self, "qqq_sma20", None)
        if qqq_sma20 is None or not getattr(qqq_sma20, "IsReady", False):
            return False
        try:
            qqq_price = float(self.Securities[self.qqq].Price)
            sma20 = float(qqq_sma20.Current.Value)
        except Exception:
            return False
        if qqq_price <= 0 or sma20 <= 0:
            return False

        band = float(getattr(config, "ITM_SMA_BAND_PCT", 0.003))
        upper = sma20 * (1.0 + band)
        lower = sma20 * (1.0 - band)
        if is_call and qqq_price <= upper:
            return True
        if is_put and qqq_price >= lower:
            return True
        return False

    def _is_micro_eod_loss_breach(
        self, symbol: str, entry_price: float, current_price: float
    ) -> bool:
        """
        EOD-only ITM hold guard with staged loss handling.

        Stage A: warning at mild drawdown (log/telemetry only).
        Stage B: force close at deeper drawdown, optionally requiring thesis break.
        Emergency: always close at catastrophic drawdown.
        """
        vix_level = (
            float(self._get_vix_level())
            if hasattr(self, "_get_vix_level")
            else float(self._current_vix)
        )

        warn_loss_pct = float(getattr(config, "ITM_OVERNIGHT_WARN_LOSS_PCT", 0.12))
        low_cut = float(getattr(config, "ITM_OVERNIGHT_EOD_EXIT_LOSS_PCT_LOW_VIX", 0.18))
        med_cut = float(getattr(config, "ITM_OVERNIGHT_EOD_EXIT_LOSS_PCT_MED_VIX", 0.20))
        high_cut = float(getattr(config, "ITM_OVERNIGHT_EOD_EXIT_LOSS_PCT_HIGH_VIX", 0.24))
        high_vix_threshold = float(getattr(config, "ITM_OVERNIGHT_HIGH_VIX_THRESHOLD", 25.0))
        med_vix_threshold = float(getattr(config, "ITM_OVERNIGHT_MED_VIX_THRESHOLD", 18.0))
        emergency_loss_pct = float(getattr(config, "ITM_OVERNIGHT_EMERGENCY_LOSS_PCT", 0.30))
        require_thesis_break = bool(
            getattr(config, "ITM_OVERNIGHT_EOD_EXIT_REQUIRE_THESIS_BREAK", True)
        )

        if entry_price <= 0 or current_price <= 0:
            return False

        if vix_level >= high_vix_threshold:
            cut_loss_pct = high_cut
        elif vix_level >= med_vix_threshold:
            cut_loss_pct = med_cut
        else:
            cut_loss_pct = low_cut

        pnl_pct = (current_price - entry_price) / entry_price
        current_date = str(self.Time.date())
        symbol_str = self._normalize_symbol_str(symbol)

        if (
            pnl_pct <= -warn_loss_pct
            and self._intraday_hold_loss_block_log_date.get(symbol_str) != current_date
        ):
            self.Log(
                f"INTRADAY_HOLD_WARN: {symbol_str} | PnL={pnl_pct:+.1%} <= -{warn_loss_pct:.0%} | "
                f"VIX={vix_level:.1f} | Entry=${entry_price:.2f} | Current=${current_price:.2f}"
            )
            self._intraday_hold_loss_block_log_date[symbol_str] = current_date

        if pnl_pct <= -emergency_loss_pct:
            self.Log(
                f"INTRADAY_HOLD_EMERGENCY_EXIT: {symbol_str} | "
                f"PnL={pnl_pct:+.1%} <= -{emergency_loss_pct:.0%}"
            )
            return True

        if pnl_pct > -cut_loss_pct:
            return False

        thesis_broken = self._is_itm_overnight_thesis_broken(symbol_str)
        if require_thesis_break and not thesis_broken:
            return False

        self.Log(
            f"INTRADAY_HOLD_BLOCK_LOSS_CAP: {symbol_str} | "
            f"PnL={pnl_pct:+.1%} <= -{cut_loss_pct:.0%} | "
            f"VIX={vix_level:.1f} | ThesisBroken={thesis_broken} | "
            f"Entry=${entry_price:.2f} | Current=${current_price:.2f}"
        )
        return True

    def _should_itm_eod_harvest(
        self,
        *,
        symbol: str,
        intraday_pos: Any,
        entry_price: float,
        current_price: float,
    ) -> bool:
        """Conditional ITM EOD harvest: close >=15% winners when conditions weaken."""
        if not bool(getattr(config, "ITM_EOD_HARVEST_15_ENABLED", True)):
            return False
        try:
            strategy = str(getattr(intraday_pos, "entry_strategy", "") or "")
            if strategy.upper() != "ITM_MOMENTUM":
                return False
            if entry_price <= 0 or current_price <= 0:
                return False
            pnl_pct = (current_price - entry_price) / entry_price
            trigger = float(getattr(config, "ITM_EOD_HARVEST_TRIGGER_PCT", 0.15))
            if pnl_pct < trigger:
                return False
            require_weakening = bool(getattr(config, "ITM_EOD_HARVEST_REQUIRE_WEAKENING", True))
            if not require_weakening:
                return True

            regime_now = float(getattr(self, "_last_regime_score", 50.0) or 50.0)
            regime_max = float(getattr(config, "ITM_EOD_HARVEST_REGIME_MAX", 60.0))
            weakening = regime_now <= regime_max

            symbol_norm = self._normalize_symbol_str(symbol)
            is_call = "C" in symbol_norm
            is_put = "P" in symbol_norm
            try:
                vix_5d = (
                    self.options_engine._iv_sensor.get_vix_5d_change()
                    if hasattr(self, "options_engine") and self.options_engine is not None
                    else None
                )
            except Exception:
                vix_5d = None
            if vix_5d is not None:
                vix_5d = float(vix_5d)
                call_adv = float(getattr(config, "ITM_EOD_HARVEST_VIX5D_CALL_ADVERSE", 0.05))
                put_adv = float(getattr(config, "ITM_EOD_HARVEST_VIX5D_PUT_ADVERSE", -0.05))
                if is_call and vix_5d >= call_adv:
                    weakening = True
                if is_put and vix_5d <= put_adv:
                    weakening = True

            if weakening:
                self.Log(
                    f"ITM_EOD_HARVEST_TRIGGER: {symbol_norm} | PnL={pnl_pct:+.1%} >= {trigger:.0%} | "
                    f"Regime={regime_now:.1f} | Weakening=True"
                )
                return True
            return False
        except Exception:
            return False

    def _intraday_force_exit_fallback(self) -> None:
        """
        V6.12: Safety net - force-close intraday position after configured close +5min if scheduled close missed.

        This prevents intraday options from carrying overnight due to scheduler issues.
        """
        # Only run once per day
        if getattr(self, "_intraday_force_exit_fallback_date", None) == self.Time.date():
            return

        # Only after configured force-close + 5 minutes.
        exit_time = getattr(config, "INTRADAY_FORCE_EXIT_TIME", "15:15")
        exit_hour, exit_minute = map(int, exit_time.split(":"))
        fallback_hour = exit_hour
        fallback_minute = exit_minute + 5
        if fallback_minute >= 60:
            fallback_hour += fallback_minute // 60
            fallback_minute = fallback_minute % 60
        if self.Time.hour < fallback_hour or (
            self.Time.hour == fallback_hour and self.Time.minute < fallback_minute
        ):
            return

        # Check if we have intraday positions
        if not hasattr(self, "options_engine") or not self.options_engine.has_intraday_position():
            self._intraday_force_exit_fallback_date = self.Time.date()
            return

        submitted_any = False
        for intraday_pos in self.options_engine.get_intraday_positions():
            if intraday_pos is None or intraday_pos.contract is None:
                continue
            symbol = self._normalize_symbol_str(intraday_pos.contract.symbol)
            mark_price = self._get_option_mark_price(symbol, fallback=0.0)
            entry_price = float(getattr(intraday_pos, "entry_price", 0.0) or 0.0)
            hold_allowed = self._should_hold_intraday_symbol_overnight(symbol)
            eod_loss_breach = hold_allowed and self._is_micro_eod_loss_breach(
                symbol=symbol,
                entry_price=entry_price,
                current_price=mark_price,
            )
            itm_eod_harvest = hold_allowed and self._should_itm_eod_harvest(
                symbol=symbol,
                intraday_pos=intraday_pos,
                entry_price=entry_price,
                current_price=mark_price,
            )
            if hold_allowed and not eod_loss_breach and not itm_eod_harvest:
                self.Log(f"INTRADAY_FORCE_EXIT_FALLBACK: HOLD_SKIP {symbol}")
                continue
            if symbol in self._intraday_close_in_progress_symbols:
                continue
            if self._has_open_non_oco_order_for_symbol(symbol):
                continue
            price = self.Securities[symbol].Price if self.Securities.ContainsKey(symbol) else 0
            if price <= 0:
                try:
                    sec = self.Securities[symbol]
                    bid = sec.BidPrice or 0
                    ask = sec.AskPrice or 0
                    if bid > 0 and ask > 0:
                        price = (bid + ask) / 2
                except Exception:
                    price = 0

            if price <= 0:
                self.Log(f"INTRADAY_FORCE_EXIT_FALLBACK: No valid price for {symbol} - skip")
                continue

            signal = self.options_engine.check_intraday_force_exit(
                current_hour=self.Time.hour,
                current_minute=self.Time.minute,
                current_price=price,
                ignore_hold_policy=(eod_loss_breach or itm_eod_harvest),
                engine=self.options_engine._find_intraday_lane_by_symbol(symbol),
                symbol=symbol,
            )
            if signal:
                live_qty = abs(self._get_option_holding_quantity(signal.symbol))
                if live_qty > 0:
                    signal.requested_quantity = live_qty
                self._intraday_close_in_progress_symbols.add(signal.symbol)
                self._intraday_force_exit_submitted_symbols[signal.symbol] = str(self.Time.date())
                self.Log(f"INTRADAY_FORCE_EXIT_FALLBACK: Triggered for {symbol}")
                self.portfolio_router.receive_signal(signal)
                submitted_any = True

        if submitted_any:
            self._process_immediate_signals()
        # Mark done after handling concrete submit attempts.
        self._intraday_force_exit_fallback_date = self.Time.date()

    def _mr_force_close_fallback(self) -> None:
        """
        V6.12: Safety net - force-close MR positions after 15:50 if scheduled close missed.

        This prevents 3x ETFs (TQQQ, SOXL, SPXL) from carrying overnight due to
        scheduler issues. Overnight gaps can cause catastrophic losses with 3x leverage.
        """
        # Only run once per day
        if getattr(self, "_mr_force_close_fallback_date", None) == self.Time.date():
            return

        # Only after 15:50 ET (5 min after scheduled 15:45 close)
        if self.Time.hour < 15 or (self.Time.hour == 15 and self.Time.minute < 50):
            return

        # Check all MR symbols and force liquidate if still invested
        mr_symbols = [(self.tqqq, "TQQQ"), (self.soxl, "SOXL"), (self.spxl, "SPXL")]
        liquidated_any = False

        for mr_symbol, mr_name in mr_symbols:
            if self.Portfolio[mr_symbol].Invested:
                self.Log(
                    f"MR_FALLBACK: Force liquidating {mr_name} at {self.Time.strftime('%H:%M')} | "
                    f"Qty={self.Portfolio[mr_symbol].Quantity}"
                )
                self.Liquidate(mr_symbol, tag="MR_FALLBACK_15:50")
                liquidated_any = True

        if liquidated_any:
            self.Log("MR_FALLBACK: Completed - all MR positions closed")

        # Mark as done to prevent repeated attempts
        self._mr_force_close_fallback_date = self.Time.date()

    def _liquidate_all_spread_aware(
        self, reason: str = "GOVERNOR_SHUTDOWN", exempt_symbols: set = None
    ) -> None:
        # Portfolio-scan liquidation: close option shorts, then longs, then equities.
        if exempt_symbols is None:
            exempt_symbols = set()

        # V2.33: Scan Portfolio for ALL options positions (not just tracked spread)
        short_options = []  # (symbol, quantity) - negative qty, need to buy back
        long_options = []  # (symbol, quantity) - positive qty, need to sell
        equity_positions = []  # Non-options positions

        for kvp in self.Portfolio:
            holding = kvp.Value
            if not holding.Invested:
                continue

            symbol = holding.Symbol
            qty = holding.Quantity

            # V3.1: Skip exempt symbols (hedges)
            if symbol in exempt_symbols:
                self.Log(f"{reason}: Exempting hedge {symbol}")
                continue

            # Check if this is an options position (SecurityType.Option)
            if symbol.SecurityType == SecurityType.Option:
                if qty < 0:
                    short_options.append((symbol, qty))
                else:
                    long_options.append((symbol, qty))
            else:
                equity_positions.append((symbol, qty))

        # Step 1: Buy back ALL short options first (eliminates naked exposure)
        # This MUST happen before selling any long options
        for symbol, qty in short_options:
            try:
                # qty is negative, so we buy abs(qty) to close
                close_qty = abs(qty)
                self._submit_option_close_market_order(
                    symbol=symbol,
                    quantity=close_qty,
                    reason=reason,
                )
                self.Log(f"{reason}: Closed short option {str(symbol)[-21:]} x{close_qty}")
            except Exception as e:
                self.Log(f"{reason}: Failed to close short {str(symbol)[-21:]} | {e}")

        # Step 2: Sell ALL long options (safe now - all shorts closed)
        for symbol, qty in long_options:
            try:
                self._submit_option_close_market_order(
                    symbol=symbol,
                    quantity=-qty,
                    reason=reason,
                )
                self.Log(f"{reason}: Closed long option {str(symbol)[-21:]} x{qty}")
            except Exception as e:
                self.Log(f"{reason}: Failed to close long {str(symbol)[-21:]} | {e}")

        # Step 3: Clear all options engine tracking state
        if self.options_engine:
            self.options_engine.clear_spread_position()
            self.options_engine.cancel_pending_spread_entry()
            self.options_engine.cancel_pending_intraday_entry()
        if self.portfolio_router:
            self.portfolio_router.clear_all_spread_margins()

        # V2.33: Clear main.py spread tracking dicts
        if self._spread_fill_tracker is not None:
            self.Log(f"{reason}: Clearing spread fill tracker")
            self._spread_fill_tracker = None
        if self._pending_spread_orders:
            self.Log(f"{reason}: Clearing {len(self._pending_spread_orders)} pending spread orders")
            self._pending_spread_orders.clear()
            self._pending_spread_orders_reverse.clear()

        # Step 4: Liquidate equity positions (trend, MR, hedges)
        for symbol, qty in equity_positions:
            try:
                self.MarketOrder(symbol, -qty, tag=reason)
                self.Log(f"{reason}: Closed equity {symbol} x{qty}")
            except Exception as e:
                self.Log(f"{reason}: Failed to close equity {symbol} | {e}")

        # Log summary
        total_closed = len(short_options) + len(long_options) + len(equity_positions)
        self.Log(
            f"{reason}: Liquidation complete | "
            f"Short opts={len(short_options)} | Long opts={len(long_options)} | "
            f"Equity={len(equity_positions)} | Total={total_closed}"
        )

    def _close_options_atomic(
        self,
        symbols_to_close: list = None,
        reason: str = "OPTIONS_CLOSE",
        clear_tracking: bool = True,
    ) -> int:
        """
        V2.33: ATOMIC options close - ALWAYS closes shorts first, then longs.

        This is the ONLY method that should be used to close options positions.
        NEVER call Liquidate() directly on option symbols!

        Args:
            symbols_to_close: Optional list of specific option symbols to close.
                            If None, closes ALL options in portfolio.
            reason: Tag for logging and order tracking.
            clear_tracking: Whether to clear options engine tracking state.

        Returns:
            Number of options positions closed.
        """
        # Collect options to close (from specific list or entire portfolio)
        short_options = []  # (symbol, qty) - shorts have negative qty
        long_options = []  # (symbol, qty) - longs have positive qty

        if symbols_to_close is not None:
            # Close specific symbols
            for symbol in symbols_to_close:
                if symbol in self.Portfolio and self.Portfolio[symbol].Invested:
                    holding = self.Portfolio[symbol]
                    if holding.Symbol.SecurityType == SecurityType.Option:
                        if holding.Quantity < 0:
                            short_options.append((holding.Symbol, holding.Quantity))
                        else:
                            long_options.append((holding.Symbol, holding.Quantity))
        else:
            # Close all options in portfolio
            for kvp in self.Portfolio:
                holding = kvp.Value
                if holding.Invested and holding.Symbol.SecurityType == SecurityType.Option:
                    if holding.Quantity < 0:
                        short_options.append((holding.Symbol, holding.Quantity))
                    else:
                        long_options.append((holding.Symbol, holding.Quantity))

        # CRITICAL: Close ALL shorts FIRST (buy to close)
        for symbol, qty in short_options:
            try:
                close_qty = abs(qty)
                self._submit_option_close_market_order(
                    symbol=symbol,
                    quantity=close_qty,
                    reason=reason,
                )
                self.Log(f"{reason}: Closed SHORT {str(symbol)[-21:]} x{close_qty}")
            except Exception as e:
                self.Log(f"{reason}: FAILED short close {str(symbol)[-21:]} | {e}")

        # THEN close ALL longs (sell to close) - safe now, no naked shorts
        for symbol, qty in long_options:
            try:
                self._submit_option_close_market_order(
                    symbol=symbol,
                    quantity=-qty,
                    reason=reason,
                )
                self.Log(f"{reason}: Closed LONG {str(symbol)[-21:]} x{qty}")
            except Exception as e:
                self.Log(f"{reason}: FAILED long close {str(symbol)[-21:]} | {e}")

        # Clear tracking state if requested
        if clear_tracking:
            if self.options_engine:
                self.options_engine.clear_spread_position()
                self.options_engine.cancel_pending_spread_entry()
                self.options_engine.cancel_pending_intraday_entry()
            if self.portfolio_router:
                self.portfolio_router.clear_all_spread_margins()
            if self._spread_fill_tracker is not None:
                self._spread_fill_tracker = None
            if self._pending_spread_orders:
                self._pending_spread_orders.clear()
                self._pending_spread_orders_reverse.clear()

        total_closed = len(short_options) + len(long_options)
        if total_closed > 0:
            self.Log(
                f"{reason}: Atomic close complete | "
                f"Shorts={len(short_options)} Longs={len(long_options)}"
            )
        return total_closed

    def _on_moo_fallback(self) -> None:
        """
        MOO fallback check at 09:31 ET.

        Checks if MOO orders failed to execute and converts them to market orders.
        """
        # Skip during warmup
        if self.IsWarmingUp:
            return

        results = self.execution_engine.check_moo_fallbacks()
        for result in results:
            if result.get("success"):
                self.Log(f"MOO_FALLBACK: Order {result.get('order_id')} fallback submitted")
            else:
                self.Log(
                    f"MOO_FALLBACK: Order {result.get('order_id')} fallback failed - "
                    f"{result.get('error')}"
                )

    def _on_sod_baseline(self) -> None:
        """
        Start of day baseline at 09:33 ET.

        Sets equity_sod for daily tracking.
        Sets SPY open for panic mode calculation.
        Checks gap filter.
        Reconciles positions with broker.
        """
        # Skip during warmup
        if self.IsWarmingUp:
            return

        # Defensive day-open reset in case pre-market callback was skipped.
        self._regime_detector_last_update_key = None
        self._regime_detector_last_raw = {}
        self._regime_overlay_ambiguous_bars = 0

        self.equity_sod = self.Portfolio.TotalPortfolioValue
        self.risk_engine.set_equity_sod(self.equity_sod)

        # Set SPY open for panic mode
        self.spy_open = self.Securities[self.spy].Open
        self.risk_engine.set_spy_open(self.spy_open)

        # Check gap filter (SPY gap down > 1.5%)
        if self.spy_prior_close > 0:
            gap_activated = self.risk_engine.check_gap_filter(self.spy_open)
            if gap_activated:
                self.today_safeguards.append("GAP_FILTER")

        # V2.1.1: Update market open data for Options Engine Micro Regime
        self._vix_at_open = self._current_vix
        self._qqq_at_open = self.Securities[self.qqq].Open
        # V2.3.4: Track UVXY at open for intraday VIX direction proxy
        self._uvxy_at_open = self.Securities[self.uvxy].Open
        self.options_engine.update_market_open_data(
            vix_open=self._vix_at_open,
            spy_open=self.spy_open,
            spy_prior_close=self.spy_prior_close,
        )

        # V2.9: Check settlement cooldown (Bug #6 fix)
        # Sets cooldown if this is first bar after market gap with unsettled cash
        self._check_settlement_cooldown()

        # V2.19 FIX: Clear stale pending MOO symbols
        # If a symbol was marked pending at 15:45 yesterday but is NOT invested
        # by 09:33 today, the MOO order didn't fill. Clear the stale pending
        # to prevent permanently blocking position limit slots.
        pending_symbols = self.trend_engine.get_pending_moo_symbols()
        if pending_symbols:
            # V2.24: Log pending state for diagnostics
            pending_info = ", ".join(
                f"{sym}(since={self.trend_engine.get_pending_moo_date(sym) or '?'})"
                for sym in pending_symbols
            )
            self.Log(
                f"TREND: PENDING_MOO_CHECK | Count={len(pending_symbols)} | "
                f"Symbols=[{pending_info}]"
            )

            stale_symbols = set()
            for sym in pending_symbols:
                # Check if this pending symbol is actually invested
                # V6.11: Use config for trend symbols
                lean_sym = getattr(self, sym.lower(), None) if sym in config.TREND_SYMBOLS else None
                if lean_sym and not self.Portfolio[lean_sym].Invested:
                    stale_symbols.add(sym)
                elif lean_sym and self.Portfolio[lean_sym].Invested:
                    # V2.24: Symbol filled but pending wasn't cleared — fix it
                    stale_symbols.add(sym)
                    self.Log(
                        f"TREND: PENDING_MOO_INVESTED {sym} | "
                        f"Already invested but still in pending set — clearing"
                    )
            for sym in stale_symbols:
                self.trend_engine.cancel_pending_moo(sym)
                self.Log(
                    f"TREND: STALE_MOO_CLEARED {sym} | "
                    f"Pending but not invested at 09:33 - clearing slot"
                )

        # Reconcile positions with broker
        self._reconcile_positions(mode="sod")

    def _on_intraday_reconcile(self) -> None:
        """
        #8 fix: periodic intraday broker-vs-engine reconciliation.

        Reduces zombie/orphan persistence from full-day to sub-day windows.
        """
        if self.IsWarmingUp:
            return
        if not self._is_primary_market_open():
            return
        now_dt = self.Time
        if self._last_reconcile_positions_run is not None:
            elapsed_min = (now_dt - self._last_reconcile_positions_run).total_seconds() / 60.0
            if elapsed_min < 20:
                return
        self._reconcile_positions(mode="intraday")

    def _is_primary_market_open(self) -> bool:
        """Return True when the primary equity market session is open."""
        try:
            exchange_hours = self.Securities[self.qqq].Exchange.Hours
            return bool(exchange_hours.IsOpen(self.Time, False))
        except Exception:
            # Conservative fallback if exchange metadata is unavailable.
            return self.Time.weekday() < 5

    def _on_warm_entry_check(self) -> None:
        """
        Warm entry check at 10:00 ET.

        During cold start (days 1-5), checks if conditions are favorable
        for a 50% sized entry into QLD.
        """
        # Skip during warmup
        if self.IsWarmingUp:
            return

        # V6.0: Warm entry is TREND — requires REDUCED+ phase
        if not self.startup_gate.allows_trend_mr():
            return

        if not self.cold_start_engine.is_cold_start_active():
            return

        # V6.14: Respect pre-market VIX ladder for warm-entry risk.
        if self._premarket_vix_ladder_level >= 2:
            self.Log(
                f"WARM_ENTRY_BLOCKED: Pre-market VIX ladder {self._premarket_vix_ladder_level} | "
                f"{self._premarket_vix_ladder_reason}"
            )
            return

        # Get regime state (use previous smoothed score)
        regime_score = self.regime_engine.get_previous_score()

        # Check if we have any leveraged positions
        has_leveraged = self._has_leveraged_position()

        # Get capital state
        capital_state = self.capital_engine.calculate(self.Portfolio.TotalPortfolioValue)

        # V2.3 DEBUG: Log cold start check state (only in live mode)
        scheduler_kill_flag = self.scheduler.is_kill_switch_triggered()
        if scheduler_kill_flag and self.LiveMode:
            self.Log(f"COLD_START_CHECK: scheduler.is_kill_switch_triggered()=True at {self.Time}")

        # Check warm entry conditions
        signal = self.cold_start_engine.check_warm_entry(
            regime_score=regime_score,
            has_leveraged_position=has_leveraged,
            kill_switch_triggered=scheduler_kill_flag,
            gap_filter_triggered=self.risk_engine.is_gap_filter_active(),
            vol_shock_active=self.risk_engine.is_vol_shock_active(self.Time),
            tradeable_equity=capital_state.tradeable_eq,
            current_hour=self.Time.hour,
            current_minute=self.Time.minute,
        )

        if signal:
            # V2.19 FIX: Warm entry must respect position limit
            # Cold start SSO entry was bypassing the trend position limit,
            # pushing invested count above MAX_CONCURRENT_TREND_POSITIONS
            trend_symbols = config.TREND_PRIORITY_ORDER  # V6.11: ["QLD", "UGL", "UCO", "SSO"]
            current_trend_count = sum(
                1 for sym in trend_symbols if self.Portfolio[getattr(self, sym.lower())].Invested
            )
            pending_moo_count = self.trend_engine.get_pending_moo_count()
            total_committed = current_trend_count + pending_moo_count

            if total_committed >= config.MAX_CONCURRENT_TREND_POSITIONS:
                self.Log(
                    f"COLD_START: Warm entry blocked by position limit | "
                    f"Invested={current_trend_count} | Pending={pending_moo_count} | "
                    f"Max={config.MAX_CONCURRENT_TREND_POSITIONS}"
                )
            else:
                self.portfolio_router.receive_signal(signal)
                self._process_immediate_signals()

    def _on_time_guard_start(self) -> None:
        """
        Time guard start at 13:55 ET.

        Blocks all entries during Fed announcement window (13:55 - 14:10).
        """
        # Skip during warmup - no logging needed
        if self.IsWarmingUp:
            return
        # Time guard tracking is handled by scheduler, no logging needed

    def _on_time_guard_end(self) -> None:
        """
        Time guard end at 14:10 ET.

        Resumes normal trading after Fed window.
        """
        # Skip during warmup - no logging needed
        if self.IsWarmingUp:
            return
        # Time guard tracking is handled by scheduler, no logging needed

    def _on_mr_force_close(self) -> None:
        """
        MR force close at 15:45 ET.

        Forces close of all Mean Reversion positions (TQQQ, SOXL).
        These are intraday-only symbols that cannot be held overnight.
        """
        # Skip during warmup - no orders allowed
        if self.IsWarmingUp:
            return
        if self._mr_force_close_ran_date == self.Time.date():
            return
        self._mr_force_close_ran_date = self.Time.date()

        # V6.11: Check all MR symbols (TQQQ, SOXL, SPXL)
        for mr_symbol, mr_name in [(self.tqqq, "TQQQ"), (self.soxl, "SOXL"), (self.spxl, "SPXL")]:
            if self.Portfolio[mr_symbol].Invested:
                signal = TargetWeight(
                    symbol=mr_name,
                    target_weight=0.0,
                    source="MR",
                    urgency=Urgency.IMMEDIATE,
                    reason="TIME_EXIT_15:45",
                )
                self.portfolio_router.receive_signal(signal)

        # Process the signals immediately
        self._process_immediate_signals()

        # CRITICAL FAILSAFE: Direct Liquidate() call to ensure 3x ETFs are closed
        # This runs AFTER signal processing as a belt-and-suspenders safety measure
        # 3x ETFs held overnight can suffer catastrophic losses from gaps
        # V6.11: Updated for all MR symbols
        for mr_symbol, mr_name in [(self.tqqq, "TQQQ"), (self.soxl, "SOXL"), (self.spxl, "SPXL")]:
            if self.Portfolio[mr_symbol].Invested:
                self.Log(f"MR_FAILSAFE: Force liquidating {mr_name} via direct Liquidate()")
                self.Liquidate(mr_symbol, tag="MR_FAILSAFE_15:45")

    def _on_eod_processing(self) -> None:
        """
        End of day processing at 15:45 ET.

        Runs all EOD batch processing:
            1. Calculate Regime score
            2. Update Capital Engine
            3. Generate Trend signals
            4. Generate Hedge signals
            5. Generate Yield signals
            6. Queue signals (MOO orders submitted at 16:00)
            7. Update Cold Start
        """
        # Skip during warmup - no orders allowed
        if self.IsWarmingUp:
            return
        if self._eod_processing_ran_date == self.Time.date():
            return
        self._eod_processing_ran_date = self.Time.date()

        total_equity = self.Portfolio.TotalPortfolioValue

        # 1. Calculate Regime
        regime_state = self._calculate_regime()
        # Store for intraday MR scanning
        self._last_regime_score = regime_state.smoothed_score
        self._last_regime_momentum_roc = float(getattr(regime_state, "momentum_roc", 0.0) or 0.0)
        self._last_regime_vix_5d_change = float(getattr(regime_state, "vix_5d_change", 0.0) or 0.0)
        self._regime_detector_sample_seq = int(getattr(self, "_regime_detector_sample_seq", 0)) + 1

        # V6.0: Update Startup Gate (time-based, no regime dependency)
        if not self.startup_gate.is_fully_armed():
            self.startup_gate.end_of_day_update()
            self.Log(
                f"STARTUP_GATE: {self.startup_gate.get_phase()} | "
                f"Hedges={'YES' if self.startup_gate.allows_hedges() else 'NO'} | "
                f"TREND/MR={'YES' if self.startup_gate.allows_trend_mr() else 'NO'} | "
                f"Options={'YES' if self.startup_gate.allows_options() else 'NO'}"
            )

        # 2. Update Capital Engine (always — hedges need tradeable equity)
        capital_state = self.capital_engine.end_of_day_update(total_equity)

        # 3. Generate Trend signals (if gate allows TREND/MR)
        if self.startup_gate.allows_trend_mr():
            self._generate_trend_signals_eod(regime_state)

        # 4. V2.30: Generate Options signals (direction-aware gating)
        self._generate_options_signals_gated(regime_state, capital_state)

        # V6.13 P0: Overnight gap protection for swing spreads
        if self.options_engine.has_spread_position():
            overnight_exit = self.options_engine.check_overnight_gap_protection_exit(
                current_vix=self._current_vix,
                current_date=str(self.Time.date()),
            )
            if overnight_exit:
                for signal in overnight_exit:
                    self.portfolio_router.receive_signal(signal)

        # 5. Generate Hedge signals (V3.0: regime-gated per thesis)
        # Thesis: Hedges should be 0% in Bull (70+) and Neutral (50-69)
        # Only activate hedges when regime < HEDGE_REGIME_GATE (50)
        regime_score = regime_state.smoothed_score
        if regime_score < config.HEDGE_REGIME_GATE:
            self._generate_hedge_signals(regime_state)
        else:
            # V3.0: Exit hedges when regime improves above threshold
            self._generate_hedge_exit_signals()

        # 6. Store capital state for MOO submission at 16:00
        # (MOO orders can't be submitted while market is open)
        self._eod_capital_state = capital_state

        # 7. Update Cold Start
        # V10: Fix dual-reset bug — only reset cold start when config allows for the tier.
        # Previously, end_of_day_update received the raw kill switch flag (True for Tier 2+),
        # which unconditionally reset cold start, ignoring KS_COLD_START_RESET_ON_TIER_2=False.
        ks_tier = self._last_risk_result.ks_tier if self._last_risk_result else KSTier.NONE
        should_reset_cold_start = (
            ks_tier == KSTier.FULL_EXIT and config.KS_COLD_START_RESET_ON_TIER_3
        ) or (ks_tier == KSTier.TREND_EXIT and config.KS_COLD_START_RESET_ON_TIER_2)
        self.cold_start_engine.end_of_day_update(kill_switch_triggered=should_reset_cold_start)

    def _on_market_close(self) -> None:
        """
        Market close at 16:00 ET.

        Submits MOO orders, persists state and logs daily summary.
        Resets daily tracking variables.
        """
        # Skip during warmup - no orders allowed
        if self.IsWarmingUp:
            return
        if self._market_close_ran_date == self.Time.date():
            return
        self._market_close_ran_date = self.Time.date()

        # V3.0 FIX: Always process EOD signals - internal logic handles Governor scaling
        # V6.11: At Governor 0%, hedges (SH) and bearish PUTs are still allowed
        # The scaling logic in _process_eod_signals zeros out bullish positions
        if hasattr(self, "_eod_capital_state") and self._eod_capital_state is not None:
            if self._governor_scale <= 0.0:
                self.Log("EOD_GOVERNOR_0: Processing defensive signals only (hedges + PUTs)")
            self._process_eod_signals(self._eod_capital_state)
            self._eod_capital_state = None

        self._ensure_daily_proxy_windows_snapshot()
        self._record_regime_timeline_event(source="MARKET_CLOSE")
        self._flush_regime_decision_artifact()
        self._flush_regime_timeline_artifact()
        self._flush_signal_lifecycle_artifact()
        self._flush_router_rejection_artifact()
        self._flush_order_lifecycle_artifact()

        # Save all state
        self._save_state()

        # V6.14: Cache closes for next day's pre-market VIX ladder.
        self._vix_prior_close = self._get_vix_level()
        uvxy_close = self.Securities[self.uvxy].Close if hasattr(self, "uvxy") else 0.0
        if uvxy_close > 0:
            self._uvxy_prior_close = uvxy_close
        self._last_market_close_check = self.Time.date()

        # V6.12: Log EOD P&L summary
        if hasattr(self, "pnl_tracker"):
            self.pnl_tracker.log_eod_summary(str(self.Time.date()))
            self.pnl_tracker.log_optimization_summary(str(self.Time.date()))
            # Reset session counters for next day
            self.pnl_tracker.reset_session()

        # V6.19: Emit daily options diagnostics summary for funnel validation.
        self._log_daily_summary()

        # Reset daily tracking
        self.today_trades.clear()
        self.today_safeguards.clear()
        self.symbols_to_skip.clear()
        self._splits_logged_today.clear()
        self._greeks_breach_logged = False
        self._last_swing_scan_time = None  # V2.19: Allow fresh swing scan next trading day
        # V2.20: Clear all rejection cooldowns at end of day
        self._trend_rejection_cooldown_until = None
        self._options_swing_cooldown_until = None
        self._options_intraday_cooldown_until = None
        self._options_intraday_cooldown_until_by_lane = {"MICRO": None, "ITM": None}
        self._options_spread_cooldown_until = None
        self._mr_rejection_cooldown_until = None
        self._diag_margin_reject_count = 0
        self._diag_intraday_candidate_count = 0
        self._diag_intraday_approved_count = 0
        self._diag_intraday_dropped_count = 0
        self._diag_intraday_router_reject_count = 0
        self._diag_intraday_result_count = 0
        self._diag_vass_block_count = 0
        self._diag_overlay_block_count = 0
        self._diag_overlay_slot_block_count = 0
        self._diag_spread_close_escalation_count = 0
        self._diag_spread_entry_signal_count = 0
        self._diag_spread_entry_submit_count = 0
        self._diag_spread_entry_fill_count = 0
        self._diag_spread_exit_signal_count = 0
        self._diag_spread_exit_submit_count = 0
        self._diag_spread_exit_fill_count = 0
        self._diag_spread_exit_canceled_count = 0
        self._diag_spread_position_removed_count = 0
        self._diag_spread_removed_fill_path_count = 0
        self._diag_spread_ghost_removed_count = 0
        self._diag_spread_loss_beyond_stop_count = 0
        self._diag_micro_tag_recovery_count = 0
        self._diag_micro_eod_sweep_close_count = 0
        self._diag_micro_pending_cancel_ignored_count = 0
        self._order_lifecycle_log_count = 0
        self._order_lifecycle_suppressed_count = 0
        self._recon_orphan_close_submitted.clear()
        self._recon_orphan_seen_streak.clear()
        self._recon_orphan_first_seen_at.clear()
        self._recon_orphan_last_log_at.clear()
        self._spread_last_exit_reason.clear()
        self._last_micro_update_log_signature = None
        self._last_micro_update_log_time = None
        self._last_spread_construct_fail_log_at = None
        self._external_exec_event_logged.clear()
        self._order_tag_hint_cache.clear()
        self._spread_ghost_flat_streak_by_key.clear()
        self._spread_ghost_last_log_by_key.clear()
        self._last_intraday_dte_routing_log_by_key.clear()
        self._recent_router_rejections.clear()
        self._diag_router_reject_reason_counts.clear()
        for _store in self._diag_router_reject_reason_counts_by_engine.values():
            _store.clear()
        self._diag_vass_reject_reason_counts.clear()
        self._diag_vass_mfe_peak_max_profit_pct = 0.0
        self._diag_vass_mfe_t1_hits = 0
        self._diag_vass_mfe_t2_hits = 0
        self._diag_vass_mfe_lock_exits = 0
        self._diag_vass_tail_cap_exits = 0
        self._diag_exit_path_counts.clear()
        self._diag_exit_path_pnl.clear()
        for _store in self._diag_exit_path_counts_by_engine.values():
            _store.clear()
        for _store in self._diag_exit_path_pnl_by_engine.values():
            _store.clear()
        for _k in self._diag_intraday_candidates_by_engine.keys():
            self._diag_intraday_candidates_by_engine[_k] = 0
        for _k in self._diag_intraday_approved_by_engine.keys():
            self._diag_intraday_approved_by_engine[_k] = 0
        for _k in self._diag_intraday_dropped_by_engine.keys():
            self._diag_intraday_dropped_by_engine[_k] = 0
        self._diag_intraday_drop_reason_counts.clear()
        for _store in self._diag_intraday_drop_reason_counts_by_engine.values():
            _store.clear()
        self._diag_transition_path_counts.clear()
        for _k in self._diag_transition_derisk_counts.keys():
            self._diag_transition_derisk_counts[_k] = 0
        for _store in self._diag_transition_derisk_counts_by_engine.values():
            for _k in list(_store.keys()):
                _store[_k] = 0
        for _k in self._diag_intraday_results_by_engine.keys():
            self._diag_intraday_results_by_engine[_k] = 0
        self._diag_intraday_candidate_ids_logged.clear()
        self._diag_intraday_approved_ids_logged.clear()
        self._diag_intraday_dropped_ids_logged.clear()
        self._single_leg_last_exit_reason.clear()
        self._last_vass_rejection_log_by_key.clear()
        self._last_intraday_diag_log_by_key.clear()
        self._high_freq_log_seen_counts.clear()
        self._high_freq_log_suppressed_counts.clear()
        self._transition_execution_context = None
        self._transition_execution_context_minute_key = None
        self._transition_execution_context_sample_seq = -1
        if hasattr(self, "options_engine") and self.options_engine is not None:
            try:
                self.options_engine.clear_transition_context_snapshot()
            except Exception:
                pass
        # V3.0 P1-B: Exit retry trackers are intraday plumbing only; clear daily.
        if self._pending_exit_orders:
            cleared = len(self._pending_exit_orders)
            self._pending_exit_orders.clear()
            self._exit_retry_scheduled_at.clear()
            self.Log(f"EOD_CLEANUP: Cleared {cleared} pending exit order trackers")

        # NOTE: _kill_switch_handled_today is NOT reset here - it resets at 09:25 pre-market
        # Resetting here causes double-trigger since OnData runs at 16:00 after EOD handler

    def OnEndOfAlgorithm(self) -> None:
        """Flush end-of-run observability artifacts."""
        self._flush_regime_decision_artifact()
        self._flush_regime_timeline_artifact()
        self._flush_signal_lifecycle_artifact()
        self._flush_router_rejection_artifact()
        self._flush_order_lifecycle_artifact()

    def _on_weekly_reset(self) -> None:
        """
        Weekly reset at Monday 09:30 ET.

        Resets weekly breaker baseline for new week.
        """
        # Skip during warmup
        if self.IsWarmingUp:
            return

        equity = self.Portfolio.TotalPortfolioValue
        self.risk_engine.set_week_start_equity(equity)

    # =========================================================================
    # V2.9: SETTLEMENT-AWARE TRADING (Bug #6 Fix)
    # =========================================================================

    def _is_first_bar_after_market_gap(self) -> bool:
        """
        V2.9: Detect if this is the first bar after a multi-day market closure.

        V2.12 Fix #8: Use simpler weekday check instead of GetPreviousMarketClose
        which doesn't exist in all QC SDK versions.

        Handles:
        - Regular weekends (Sat-Sun) → Monday = gap
        - Does NOT detect holiday gaps (acceptable limitation)

        Returns:
            True if today is Monday (gap after weekend).
        """
        if not config.SETTLEMENT_AWARE_TRADING:
            return False

        try:
            # V2.12: Simple weekday check - Monday (0) after weekend gap
            # This is sufficient for most settlement timing issues
            # Holiday gaps are rare and not worth the API complexity
            is_monday = self.Time.weekday() == 0
            if is_monday:
                self.Log("SETTLEMENT: Monday detected (post-weekend gap)")
            return is_monday
        except Exception as e:
            self.Log(f"SETTLEMENT: Error checking market gap - {e}")
            return False

    def _get_unsettled_cash(self) -> float:
        """
        V2.9: Get Portfolio.UnsettledCash - QC's built-in T+1 tracking.

        Returns:
            Unsettled cash amount from previous session trades.
        """
        try:
            return self.Portfolio.UnsettledCash
        except Exception:
            return 0.0

    def _check_settlement_cooldown(self) -> None:
        """
        V2.11: Smart settlement cooldown with threshold gate.

        Called at SOD baseline (09:33) to set settlement cooldown if:
        1. This is the first bar after a market gap (weekend/holiday)
        2. UnsettledCash > 10% of portfolio (material amount)

        V2.11 Change: Only halt if UnsettledCash is material (>10% of portfolio),
        and halt until 10:30 AM specifically (not arbitrary 60 min from now).
        This prevents unnecessary halts for small UnsettledCash amounts.
        """
        if not config.SETTLEMENT_AWARE_TRADING:
            return

        if self._is_first_bar_after_market_gap():
            unsettled = self._get_unsettled_cash()
            portfolio_value = self.Portfolio.TotalPortfolioValue
            unsettled_pct = unsettled / portfolio_value if portfolio_value > 0 else 0

            # V2.11: Only trigger if UnsettledCash > threshold (10% of portfolio)
            if unsettled_pct < config.SETTLEMENT_UNSETTLED_THRESHOLD_PCT:
                self.Log(
                    f"SETTLEMENT: Gap detected | UnsettledCash=${unsettled:,.0f} "
                    f"({unsettled_pct:.1%}) below {config.SETTLEMENT_UNSETTLED_THRESHOLD_PCT:.0%} threshold | "
                    f"Trading allowed"
                )
                return

            # V2.11: Halt until 10:30 AM (not 60 min from now)
            self._settlement_cooldown_until = self.Time.replace(
                hour=config.SETTLEMENT_HALT_UNTIL_HOUR,
                minute=config.SETTLEMENT_HALT_UNTIL_MINUTE,
                second=0,
                microsecond=0,
            )
            self.Log(
                f"SETTLEMENT_HALT: UnsettledCash=${unsettled:,.0f} ({unsettled_pct:.1%}) > "
                f"{config.SETTLEMENT_UNSETTLED_THRESHOLD_PCT:.0%} threshold | "
                f"Halting until {self._settlement_cooldown_until.strftime('%H:%M')}"
            )

    def _can_trade_options_settlement_aware(self) -> bool:
        """
        V2.9: Check if options trading is allowed based on settlement status.

        Returns:
            False during the first hour after any post-gap market open
            if there is unsettled cash. True otherwise.
        """
        if not config.SETTLEMENT_AWARE_TRADING:
            return True

        # Check if we're in settlement cooldown
        if self._settlement_cooldown_until is not None:
            if self.Time < self._settlement_cooldown_until:
                unsettled = self._get_unsettled_cash()
                if unsettled > 0:
                    # Only log once per minute to avoid spam
                    if self.Time.minute != getattr(self, "_last_settlement_log_minute", -1):
                        self.Log(f"SETTLEMENT: Cooldown active | UnsettledCash=${unsettled:,.0f}")
                        self._last_settlement_log_minute = self.Time.minute
                    return False
            else:
                # V2.13 Fix #9: Log AAP keyword when settlement gate opens
                self.Log("SETTLEMENT_GATE_OPEN: Trading resumed after settlement cooldown")
                self._settlement_cooldown_until = None

        return True

    def _check_expiration_hammer_v2(self) -> None:
        """
        V2.4.4 P0: Expiration Hammer V2 - Close ALL options expiring TODAY.

        This is called every minute during trading hours and checks ALL broker
        positions for options expiring today. If found and it's past 2:00 PM,
        immediately liquidate them.

        This is a CRITICAL safety check that runs independently of the options
        engine's tracked positions. It catches any options that slipped through.

        V2.33: Uses atomic close pattern - ALWAYS closes shorts first, then longs.
        """
        if self.IsWarmingUp:
            return

        # Only check at 2:00 PM or later
        if self.Time.hour < config.OPTIONS_EXPIRING_TODAY_FORCE_CLOSE_HOUR:
            return
        if (
            self.Time.hour == config.OPTIONS_EXPIRING_TODAY_FORCE_CLOSE_HOUR
            and self.Time.minute < config.OPTIONS_EXPIRING_TODAY_FORCE_CLOSE_MINUTE
        ):
            return

        current_date = self.Time.strftime("%Y-%m-%d")

        # V2.33: Collect ALL options to close FIRST, then close atomically
        expiration_hammer_symbols = []  # Options expiring TODAY
        early_exercise_symbols = []  # ITM options near expiry

        # Scan ALL portfolio positions for expiring options
        for holding in self.Portfolio.Values:
            if not holding.Invested:
                continue

            # Check if this is an option
            if holding.Symbol.SecurityType != SecurityType.Option:
                continue

            # Get expiry date from the symbol
            try:
                expiry = holding.Symbol.ID.Date
                expiry_date = expiry.strftime("%Y-%m-%d")

                if expiry_date == current_date:
                    # EXPIRING TODAY - collect for atomic close
                    qty = holding.Quantity
                    symbol_str = str(holding.Symbol)
                    self.Log(
                        f"EXPIRATION_HAMMER_V2: QUEUED {symbol_str} | "
                        f"Qty={qty} | Expires TODAY ({expiry_date}) | "
                        f"Time={self.Time.strftime('%H:%M')} | "
                        f"P0 FIX: Preventing auto-exercise"
                    )
                    expiration_hammer_symbols.append(holding.Symbol)

                # V2.28: Early exercise guard — close ITM single-leg options near expiry
                # Prevents costly early exercise (Q1 2022: 2 exercises cost -$5,614)
                # Only for single-leg options (spreads have their own DTE exit)
                elif not self.options_engine.has_spread_position():
                    days_to_expiry = (expiry - self.Time).days
                    if days_to_expiry <= config.EARLY_EXERCISE_GUARD_DTE:
                        # Check if option is ITM
                        underlying_price = self.Securities[self.qqq].Price
                        strike = holding.Symbol.ID.StrikePrice
                        is_call = holding.Symbol.ID.OptionRight == OptionRight.Call
                        itm_buffer = config.EARLY_EXERCISE_GUARD_ITM_BUFFER
                        is_itm = (is_call and underlying_price > strike * (1 + itm_buffer)) or (
                            not is_call and underlying_price < strike * (1 - itm_buffer)
                        )
                        if is_itm:
                            qty = holding.Quantity
                            symbol_str = str(holding.Symbol)
                            self.Log(
                                f"EARLY_EXERCISE_GUARD: QUEUED {symbol_str} | "
                                f"Qty={qty} | DTE={days_to_expiry} | ITM | "
                                f"Strike={strike} Underlying={underlying_price:.2f}"
                            )
                            early_exercise_symbols.append(holding.Symbol)
            except Exception as e:
                self.Log(f"EXPIRATION_HAMMER_V2: Error checking {holding.Symbol} - {e}")

        # V2.33 CRITICAL: Close all collected options ATOMICALLY (shorts first, then longs)
        if expiration_hammer_symbols:
            self.Log(
                f"EXPIRATION_HAMMER_V2: Closing {len(expiration_hammer_symbols)} expiring options atomically"
            )
            self._close_options_atomic(
                symbols_to_close=expiration_hammer_symbols,
                reason="EXPIRATION_HAMMER_V2",
                clear_tracking=True,
            )

        if early_exercise_symbols:
            # Don't clear tracking again if hammer already did
            clear_tracking = len(expiration_hammer_symbols) == 0
            self.Log(
                f"EARLY_EXERCISE_GUARD: Closing {len(early_exercise_symbols)} ITM options atomically"
            )
            self._close_options_atomic(
                symbols_to_close=early_exercise_symbols,
                reason="EARLY_EXERCISE_GUARD",
                clear_tracking=clear_tracking,
            )

        # V2.25 Fix #2: Safety net — liquidate any QQQ equity from missed assignments
        # If exercise detection (Fix #1) fails, this catches stale QQQ shares daily at 14:00
        try:
            qqq_holding = self.Portfolio[self.qqq]
            if qqq_holding.Invested:
                self.Log(
                    f"ASSIGNMENT_SAFETY_NET: QQQ equity detected | "
                    f"Qty={qqq_holding.Quantity} | Value=${qqq_holding.HoldingsValue:,.2f} | "
                    f"LIQUIDATING stale assignment shares"
                )
                self.Liquidate(self.qqq, tag="ASSIGNMENT_SAFETY_NET")
        except Exception as e:
            self.Log(f"ASSIGNMENT_SAFETY_NET: Error checking QQQ - {e}")

    def _on_intraday_options_force_close(self) -> None:
        """
        V2.1.1: Intraday options force close at configured force-exit time.

        Forces close of all intraday mode options positions (0-2 DTE).
        These must close before the final liquidity fade into the close.
        """
        # Skip during warmup
        if self.IsWarmingUp:
            return
        if self._intraday_force_close_ran_date == self.Time.date():
            return
        self._intraday_force_close_ran_date = self.Time.date()

        # V2.4.4 P0: Run Expiration Hammer V2 as part of force close
        self._check_expiration_hammer_v2()

        # Check for intraday positions to close
        for intraday_pos in self.options_engine.get_intraday_positions():
            if intraday_pos is None:
                continue
            # Get current option price
            symbol = self._normalize_symbol_str(intraday_pos.contract.symbol)
            current_price = self._get_option_mark_price(symbol, fallback=intraday_pos.entry_price)
            hold_allowed = self._should_hold_intraday_symbol_overnight(symbol)
            entry_price = float(getattr(intraday_pos, "entry_price", 0.0) or 0.0)
            eod_loss_breach = hold_allowed and self._is_micro_eod_loss_breach(
                symbol=symbol,
                entry_price=entry_price,
                current_price=current_price,
            )
            itm_eod_harvest = hold_allowed and self._should_itm_eod_harvest(
                symbol=symbol,
                intraday_pos=intraday_pos,
                entry_price=entry_price,
                current_price=current_price,
            )

            if hold_allowed and not eod_loss_breach and not itm_eod_harvest:
                self.Log(f"INTRADAY_FORCE_EXIT: HOLD_SKIP {symbol} (ITM overnight policy)")
                continue

            # V2.25 Fix #4: Double-sell guard — verify position is still held
            # Prevents creating orphan shorts if limit/profit-target already closed
            try:
                if not self.Portfolio[intraday_pos.contract.symbol].Invested:
                    self.Log(
                        f"INTRADAY_FORCE_EXIT: SKIP | {symbol} already closed | "
                        f"Clearing stale _intraday_position"
                    )
                    self.options_engine.remove_intraday_position(symbol=symbol)
                    self._clear_intraday_close_guard(symbol)
                    continue
            except Exception:
                pass  # If symbol lookup fails, proceed with force close

            if self._has_open_non_oco_order_for_symbol(symbol):
                continue

            # Cancel active OCO before force-close to avoid orphan sell orders
            # creating accidental short options after the position is closed.
            try:
                if self.oco_manager.cancel_by_symbol(symbol, reason="INTRADAY_FORCE_CLOSE"):
                    self.Log(f"INTRADAY_FORCE_EXIT: Cancelled active OCO for {symbol}")
            except Exception as e:
                self.Log(f"INTRADAY_FORCE_EXIT: OCO cancel failed for {symbol} | {e}")

            signal = self.options_engine.check_intraday_force_exit(
                current_hour=self.Time.hour,
                current_minute=self.Time.minute,
                current_price=current_price,
                ignore_hold_policy=(eod_loss_breach or itm_eod_harvest),
                engine=self.options_engine._find_intraday_lane_by_symbol(symbol),
                symbol=symbol,
            )

            if signal:
                # Idempotency: only one force-close submit per symbol per day.
                submitted_date = self._intraday_force_exit_submitted_symbols.get(signal.symbol)
                if submitted_date == str(self.Time.date()):
                    live_qty = abs(self._get_option_holding_quantity(signal.symbol))
                    if live_qty <= 0:
                        self.Log(
                            f"INTRADAY_FORCE_EXIT: SKIP duplicate submit | "
                            f"{signal.symbol} | Date={submitted_date}"
                        )
                        continue
                    self.Log(
                        f"INTRADAY_FORCE_EXIT: RETRY | {signal.symbol} | "
                        f"Qty={live_qty} still held"
                    )
                live_qty = abs(self._get_option_holding_quantity(signal.symbol))
                if live_qty <= 0:
                    self.Log(f"INTRADAY_FORCE_EXIT: SKIP no live holding | {signal.symbol}")
                    continue
                signal.requested_quantity = live_qty
                self._intraday_close_in_progress_symbols.add(signal.symbol)
                self._intraday_force_exit_submitted_symbols[signal.symbol] = str(self.Time.date())
                self.portfolio_router.receive_signal(signal)
                self._process_immediate_signals()

        # Safety net: close any live MICRO-tagged holdings even if intraday state is missing.
        # This prevents overnight orphan risk from fill/cancel race conditions.
        if self._micro_open_symbols:
            for holding in self.Portfolio.Values:
                try:
                    if not holding.Invested or holding.Symbol.SecurityType != SecurityType.Option:
                        continue
                    live_symbol = self._normalize_symbol_str(holding.Symbol)
                    if live_symbol not in self._micro_open_symbols:
                        continue
                    hold_allowed = self._should_hold_intraday_symbol_overnight(live_symbol)
                    if hold_allowed:
                        entry_price = 0.0
                        snapshot = self._intraday_entry_snapshot.get(live_symbol, {})
                        if snapshot and snapshot.get("entry_price", 0) > 0:
                            entry_price = float(snapshot.get("entry_price", 0.0))
                        elif holding.AveragePrice > 0:
                            entry_price = float(holding.AveragePrice)
                        current_price = self._get_option_mark_price(
                            live_symbol, fallback=float(holding.Price)
                        )
                        if not self._is_micro_eod_loss_breach(
                            symbol=live_symbol,
                            entry_price=entry_price,
                            current_price=current_price,
                        ):
                            continue
                    live_qty = int(holding.Quantity)
                    if live_qty == 0:
                        self._micro_open_symbols.discard(live_symbol)
                        continue
                    submitted_date = self._intraday_force_exit_submitted_symbols.get(live_symbol)
                    if submitted_date == str(self.Time.date()):
                        # Retry same-day only if still live and no non-OCO order is active.
                        if abs(self._get_option_holding_quantity(live_symbol)) <= 0:
                            self._clear_intraday_close_guard(live_symbol)
                            continue
                        if self._has_open_non_oco_order_for_symbol(live_symbol):
                            continue
                        self.Log(f"INTRADAY_FORCE_EXIT_SWEEP: RETRY same-day | {live_symbol}")

                    try:
                        self.oco_manager.cancel_by_symbol(
                            live_symbol, reason="INTRADAY_FORCE_CLOSE_SWEEP"
                        )
                    except Exception:
                        pass

                    self._intraday_close_in_progress_symbols.add(live_symbol)
                    self._intraday_force_exit_submitted_symbols[live_symbol] = str(self.Time.date())
                    self._diag_micro_eod_sweep_close_count += 1
                    self.Log(
                        f"INTRADAY_FORCE_EXIT_SWEEP: Closing MICRO holding from holdings ledger | "
                        f"{live_symbol} | Qty={live_qty}"
                    )
                    self._submit_option_close_market_order(
                        symbol=holding.Symbol,
                        quantity=-live_qty,
                        reason="MICRO_EOD_SWEEP",
                        engine_hint="MICRO",
                    )
                except Exception as e:
                    self.Log(f"INTRADAY_FORCE_EXIT_SWEEP_ERROR: {e}")

    def _ensure_oco_for_open_options(self) -> None:
        """
        V6.6.2: Ensure every open single-leg options position has an active OCO.

        If a position exists without an OCO (e.g., OCO submission failed after-hours),
        create and submit one at the next market session to prevent expiry losses.
        """
        if (
            self.IsWarmingUp
            or not hasattr(self, "options_engine")
            or not hasattr(self, "oco_manager")
        ):
            return

        # Skip if we currently hold a spread (OCO only for single-leg options)
        if self.options_engine.has_spread_position():
            return

        positions_for_oco = self.options_engine.get_intraday_positions()
        if not positions_for_oco:
            swing_pos = self.options_engine.get_position()
            if swing_pos is not None:
                positions_for_oco = [swing_pos]
        if not positions_for_oco:
            return
        # Process one symbol per call to limit churn.
        position = positions_for_oco[0]
        if position is None or position.contract is None:
            return

        symbol = self._normalize_symbol_str(position.contract.symbol)

        # Don't recover OCO while close is in progress for this symbol.
        if symbol in self._intraday_close_in_progress_symbols:
            return

        # Never re-arm OCO while a non-OCO order is in flight for this symbol.
        # This avoids submit/cancel races between software exits/retries and OCO recovery.
        if self._has_open_non_oco_order_for_symbol(symbol):
            return

        # Skip OCO recovery in force-close window to avoid close-race amplification.
        try:
            exit_hour, exit_min = map(
                int, getattr(config, "INTRADAY_FORCE_EXIT_TIME", "15:15").split(":")
            )
            cutoff = int(getattr(config, "OCO_RECOVERY_CUTOFF_MINUTES_BEFORE_FORCE_EXIT", 20))
            now_minutes = self.Time.hour * 60 + self.Time.minute
            force_minutes = exit_hour * 60 + exit_min
            if now_minutes >= force_minutes - cutoff:
                return
        except Exception:
            pass

        # If OCO already active, nothing to do
        if self.oco_manager.has_active_pair(symbol):
            return

        # Throttle OCO recovery retries per symbol (minutes, not once/day).
        today = str(self.Time.date())
        last_attempt = self._last_oco_recovery_attempt.get(symbol)
        retry_interval_min = max(1, int(getattr(config, "OCO_RECOVERY_RETRY_MINUTES", 5)))
        if isinstance(last_attempt, str):
            # Backward compatibility if old date-string format remains in memory.
            if last_attempt == today:
                return
        elif last_attempt is not None:
            try:
                elapsed_min = (self.Time - last_attempt).total_seconds() / 60.0
                if elapsed_min < retry_interval_min:
                    return
            except Exception:
                pass

        # Ensure we still hold the position
        try:
            qc_symbol = self.Symbol(symbol)
            holding = self.Portfolio[qc_symbol]
            if not holding.Invested:
                self._clear_intraday_close_guard(symbol)
                return
            qty = abs(int(holding.Quantity))
            if qty <= 0:
                self._clear_intraday_close_guard(symbol)
                return
        except Exception:
            # Fallback to tracked quantity if symbol lookup fails
            qty = int(position.num_contracts) if position.num_contracts else 0
            if qty <= 0:
                return

        # Create and submit OCO
        oco_pair = self.oco_manager.create_oco_pair(
            symbol=symbol,
            entry_price=position.entry_price,
            stop_price=position.stop_price,
            target_price=position.target_price,
            quantity=qty,
            current_date=today,
            tag_context=f"{self._oco_engine_prefix_for_strategy(getattr(position, 'entry_strategy', 'UNKNOWN'))}:{getattr(position, 'entry_strategy', 'UNKNOWN')}",
        )
        submitted = False
        if oco_pair:
            submitted = self.oco_manager.submit_oco_pair(oco_pair, current_time=str(self.Time))

        self._last_oco_recovery_attempt[symbol] = self.Time
        if submitted:
            self.Log(
                f"OCO_RECOVER: Created missing OCO | {symbol} | "
                f"Stop=${position.stop_price:.2f} Target=${position.target_price:.2f} Qty={qty}"
            )
        else:
            self.Log(
                f"OCO_RECOVER: Failed to submit (market closed or error) | {symbol} | "
                f"RetryIn={retry_interval_min}m"
            )

    def _reconcile_intraday_close_guards(self) -> None:
        """Clear stale close-in-progress guards after positions are flat."""
        if not self._intraday_close_in_progress_symbols:
            return
        stale = []
        for symbol in self._intraday_close_in_progress_symbols:
            if abs(self._get_option_holding_quantity(symbol)) <= 0:
                stale.append(symbol)
        for symbol in stale:
            self._clear_intraday_close_guard(symbol)

    def _oco_engine_prefix_for_strategy(self, entry_strategy: str) -> str:
        """Map strategy tag to stable engine prefix for OCO attribution."""
        strategy = str(entry_strategy or "UNKNOWN").upper()
        if strategy == "ITM_MOMENTUM":
            return "ITM"
        if strategy == "PROTECTIVE_PUTS":
            return "HEDGE"
        if strategy.startswith("MICRO_") or strategy in (
            "DEBIT_FADE",
            "OTM_MOMENTUM",
            "INTRADAY_DEBIT_FADE",
        ):
            return "MICRO"
        return "OPT"

    def _on_friday_firewall(self) -> None:
        """
        V2.4.1: Friday Firewall - close swing options before weekend.

        V2.9: Holiday-aware (Bug #3 fix). Runs on:
        - Friday (normal weeks)
        - Thursday (when Friday is a holiday like Good Friday)

        Rules:
        1. VIX > 25: Close ALL swing options (high volatility weekend risk)
        2. Fresh trade (opened today) AND VIX >= 15: Close it (gambling protection)
        3. Fresh trade AND VIX < 15: Keep it (calm market exception)
        4. Older trades: Keep them (already survived initial risk)
        """
        # Skip during warmup
        if self.IsWarmingUp:
            return

        # V2.9: Holiday-aware check - only run on expiration firewall days
        if not is_expiration_firewall_day(self):
            return

        # Get current VIX
        vix_current = self._current_vix

        # Check if we have any swing positions
        swing_signals = []
        for spread in self.options_engine.get_spread_positions():
            signals = self.options_engine.check_friday_firewall_exit(
                current_vix=vix_current,
                current_date=str(self.Time.date()),
                vix_close_all_threshold=config.FRIDAY_FIREWALL_VIX_CLOSE_ALL,
                vix_keep_fresh_threshold=config.FRIDAY_FIREWALL_VIX_KEEP_FRESH,
                spread_override=spread,
            )
            if signals:
                swing_signals.extend(signals)

        itm_signals = self._collect_itm_weekend_firewall_signals(current_vix=vix_current)
        firewall_signals = swing_signals + itm_signals

        if firewall_signals:
            for signal in firewall_signals:
                self.Log(f"FRIDAY_FIREWALL: {signal.reason} | VIX={vix_current:.1f}")
                self.portfolio_router.receive_signal(signal)

            # Process immediately
            self._process_immediate_signals()
        else:
            self.Log(f"FRIDAY_FIREWALL: No action needed | VIX={vix_current:.1f}")

        # V9.2: Guarded Friday sweep (single pass/day).
        self._reconcile_spread_state()

    def _reconcile_spread_state(self) -> None:
        """V9.2: Run a single guarded Friday spread reconcile sweep."""
        today = str(self.Time.date())
        if self._friday_spread_reconcile_date == today:
            return
        self._friday_spread_reconcile_date = today
        self._reconcile_positions(mode="friday")

    def _on_vix_spike_check(self) -> None:
        """
        V2.1.1: Layer 1 VIX spike detection (every 5 minutes).

        V2.3.4: Uses UVXY as intraday proxy since CBOE VIX only supports Daily.
        Checks for sudden VIX spikes (>3% in 5 minutes via UVXY).
        Sets spike alert cooldown if triggered.
        """
        # Skip during warmup
        if self.IsWarmingUp:
            return

        # V2.3.4: Use UVXY for intraday spike detection
        uvxy_current = self.Securities[self.uvxy].Price
        if self._uvxy_at_open > 0:
            uvxy_change_pct = (uvxy_current - self._uvxy_at_open) / self._uvxy_at_open * 100
            # Derive VIX proxy from UVXY change
            vix_intraday_proxy = self._vix_at_open * (1 + uvxy_change_pct / 150)
        else:
            vix_intraday_proxy = self._current_vix

        # Check for spike using intraday proxy
        spike_alert = self.options_engine.check_micro_spike_alert(
            vix_current=vix_intraday_proxy,
            vix_5min_ago=self._vix_5min_ago,
            current_time=str(self.Time),
        )

        if spike_alert:
            # Throttle VIX spike logs: 1 per LOG_THROTTLE_MINUTES OR if move > threshold
            vix_move = abs(vix_intraday_proxy - self._vix_5min_ago)
            should_log = (
                not hasattr(self, "_last_vix_spike_log")
                or self._last_vix_spike_log is None
                or (self.Time - self._last_vix_spike_log).total_seconds() / 60
                > config.LOG_THROTTLE_MINUTES
                or vix_move >= config.LOG_VIX_SPIKE_MIN_MOVE
            )
            if should_log:
                self.Log(
                    f"VIX_SPIKE: {self._vix_5min_ago:.1f} -> {vix_intraday_proxy:.1f} (via UVXY)"
                )
                self._last_vix_spike_log = self.Time

        # Update 5-min ago value for next check (using proxy)
        self._vix_5min_ago = vix_intraday_proxy

    def _on_micro_regime_update(self) -> None:
        """
        V2.1.1: Layer 2 & 4 - Direction + Regime update (every 15 minutes).

        Updates the Micro Regime Engine with current market data.
        V2.3.4: Uses UVXY as intraday VIX proxy since CBOE VIX only supports Daily.
        """
        # Skip during warmup
        if self.IsWarmingUp:
            return

        # V2.3.4: Use UVXY % change as intraday VIX direction proxy
        # UVXY tracks ~1.5x daily VIX moves, so direction is reliable
        uvxy_current = self.Securities[self.uvxy].Price
        if self._uvxy_at_open > 0:
            uvxy_change_pct = (uvxy_current - self._uvxy_at_open) / self._uvxy_at_open * 100
            # Derive synthetic "intraday VIX" from UVXY change applied to VIX open
            # If UVXY is up 3%, VIX is approximately up 2% (UVXY is ~1.5x)
            vix_intraday_proxy = self._vix_at_open * (1 + uvxy_change_pct / 150)
        else:
            uvxy_change_pct = 0.0
            vix_intraday_proxy = self._current_vix

        # Get current QQQ price
        qqq_current = self.Securities[self.qqq].Price

        # V2.11 (Pitfall #7): Separate VIX Level from VIX Direction
        # - Level: Use CBOE VIX (daily) - prevents false level spikes from UVXY contango
        # - Direction: Use UVXY proxy (vix_intraday_proxy) - UVXY tracks direction reliably
        vix_level_cboe = self._get_vix_level()  # CBOE VIX for level classification

        # Update micro regime engine with intraday VIX proxy
        # V2.5: Pass macro_regime_score for Grind-Up Override
        vix_open_for_micro = self._vix_at_open
        shock_memory_pct = self._get_premarket_shock_memory_pct()
        if shock_memory_pct > 0:
            anchor = min(max(getattr(config, "MICRO_SHOCK_MEMORY_ANCHOR", 0.60), 0.0), 1.0)
            memory_scale = 1.0 + shock_memory_pct * anchor
            if memory_scale > 1.0:
                vix_open_for_micro = self._vix_at_open / memory_scale

        state = self.options_engine.update_micro_regime_state(
            vix_current=vix_intraday_proxy,  # Use UVXY-derived for direction
            vix_open=vix_open_for_micro,
            qqq_current=qqq_current,
            qqq_open=self._qqq_at_open,
            current_time=str(self.Time),
            macro_regime_score=self._last_regime_score,
            vix_level_override=vix_level_cboe,  # V2.11: Use CBOE VIX for level
        )

        # V8: Backtest log-budget guard for high-frequency MICRO_UPDATE diagnostics.
        micro_dir = state.recommended_direction.value if state.recommended_direction else "NONE"
        # V10: Add VIX tier for telemetry
        vix_tier = "LOW" if vix_level_cboe < 18 else "MED" if vix_level_cboe < 25 else "HIGH"
        micro_msg = (
            f"MICRO_UPDATE: VIX_level={vix_level_cboe:.1f}(CBOE) VIX_tier={vix_tier} VIX_dir_proxy={vix_intraday_proxy:.2f} (UVXY {uvxy_change_pct:+.1f}%) | "
            f"Regime={state.micro_regime.value} | Dir={micro_dir} | "
            f"ShockMem={shock_memory_pct:+.1%}"
        )
        is_live = bool(hasattr(self, "LiveMode") and self.LiveMode)
        if is_live:
            self.Log(micro_msg)
        elif bool(getattr(config, "MICRO_UPDATE_LOG_BACKTEST_ENABLED", True)):
            signature = (
                state.micro_regime.value,
                micro_dir,
                round(float(vix_level_cboe), 1),
                round(float(vix_intraday_proxy), 2),
                round(float(shock_memory_pct), 3),
            )
            on_change_only = bool(getattr(config, "MICRO_UPDATE_LOG_ON_CHANGE_ONLY", True))
            min_minutes = int(getattr(config, "MICRO_UPDATE_LOG_MINUTES", 60))
            should_log = True
            if on_change_only:
                changed = signature != self._last_micro_update_log_signature
                due = (
                    self._last_micro_update_log_time is None
                    or (self.Time - self._last_micro_update_log_time).total_seconds() / 60.0
                    >= min_minutes
                )
                should_log = changed or due
            if should_log:
                self.Log(micro_msg)
                self._last_micro_update_log_signature = signature
                self._last_micro_update_log_time = self.Time

    def _get_vix_intraday_proxy(self) -> float:
        """
        V2.4.1: Get UVXY-derived VIX proxy for intraday direction.

        Calculates synthetic intraday VIX from UVXY % change applied to VIX open.
        This is needed because CBOE VIX only supports Daily resolution in QC.
        UVXY tracks ~1.5x daily VIX moves, so we derive VIX from UVXY change.

        Returns:
            Estimated intraday VIX value derived from UVXY price change.
        """
        uvxy_current = self.Securities[self.uvxy].Price
        if self._uvxy_at_open > 0:
            uvxy_change_pct = (uvxy_current - self._uvxy_at_open) / self._uvxy_at_open * 100
            # Derive synthetic "intraday VIX" from UVXY change applied to VIX open
            # If UVXY is up 3%, VIX is approximately up 2% (UVXY is ~1.5x)
            return self._vix_at_open * (1 + uvxy_change_pct / 150)
        return self._current_vix

    def _get_vix_level(self) -> float:
        """
        V2.11 (Pitfall #7): Get VIX LEVEL from CBOE VIX (daily).

        Uses the actual CBOE VIX for level classification (Low/Medium/High).
        Do NOT derive synthetic VIX level from UVXY - UVXY can gap up while
        VIX is stable due to contango, causing false level spikes.

        V10.8: when CBOE VIX feed appears stale for multiple sessions,
        blend in intraday proxy to avoid frozen-level behavior.

        Returns:
            VIX level used for regime classification.
        """
        stale_threshold = int(getattr(config, "VIX_STALE_MAX_SESSIONS", 3))
        stale_fallback_enabled = bool(getattr(config, "VIX_STALE_LEVEL_FALLBACK_ENABLED", True))
        last_update = getattr(self, "_last_vix_update_date", None)

        if (
            stale_fallback_enabled
            and stale_threshold > 0
            and last_update is not None
            and self.Time.date() is not None
        ):
            days_stale = (self.Time.date() - last_update).days
            if days_stale >= stale_threshold:
                blend = float(getattr(config, "VIX_STALE_LEVEL_FALLBACK_BLEND", 0.35))
                blend = max(0.0, min(1.0, blend))
                proxy = float(self._get_vix_intraday_proxy())
                fallback_level = float(self._current_vix) * (1.0 - blend) + proxy * blend

                if getattr(self, "_last_vix_stale_log_date", None) != self.Time.date():
                    self.Log(
                        "VIX_STALE_LEVEL_FALLBACK: "
                        f"Current={self._current_vix:.2f} | Proxy={proxy:.2f} | "
                        f"Blend={blend:.0%} | DaysStale={days_stale}"
                    )
                    self._last_vix_stale_log_date = self.Time.date()
                return fallback_level

        return self._current_vix  # Use daily CBOE VIX, NOT UVXY-derived proxy

    def _should_scan_intraday(self) -> bool:
        """
        V2.4.1: Check if enough time passed since last intraday scan.

        Implements 15-minute throttle to reduce intraday scanning from
        95 scans/hour (every minute) to 4 scans/hour (every 15 minutes).

        Returns:
            True if throttle allows scanning, False otherwise.
        """
        if self._last_intraday_scan is None:
            self._last_intraday_scan = self.Time
            return True

        elapsed_seconds = (self.Time - self._last_intraday_scan).total_seconds()
        if elapsed_seconds >= 900:  # 15 minutes = 900 seconds
            self._last_intraday_scan = self.Time
            return True

        return False

    def _is_market_close_blackout(self) -> bool:
        """
        V2.18: Check if in market close blackout window (RPT-5 fix).

        Orders submitted 15:58-16:00 may not fill properly due to
        end-of-day auction mechanics. Block orders during this window.

        Returns:
            True if in blackout window, False otherwise.
        """
        if self.Time.hour == 15 and self.Time.minute >= 58:
            return True
        return False

    # =========================================================================
    # ORDER EVENT HANDLER
    # =========================================================================

    def _should_log_backtest_category(self, config_flag: str, default: bool = True) -> bool:
        """Return whether a log category is enabled for current run mode."""
        is_live = bool(hasattr(self, "LiveMode") and self.LiveMode)
        if is_live:
            return True
        return bool(getattr(config, config_flag, default))

    def _record_order_tag_map(self, order_id: int, symbol: str, tag: str, source: str) -> None:
        """Emit deterministic order-id -> tag mapping for RCA even when broker CSV drops tags."""
        try:
            oid = int(order_id or 0)
        except Exception:
            oid = 0
        clean_tag = str(tag or "").strip()
        if oid <= 0 or not clean_tag:
            return
        if oid in self._order_tag_map_logged_ids:
            return
        self._order_tag_map_logged_ids.add(oid)
        if len(self._order_tag_map_logged_ids) > 50000:
            self._order_tag_map_logged_ids.clear()
        trace_id = self._extract_trace_id_from_tag(clean_tag) or "NONE"
        sym = self._normalize_symbol_str(symbol)
        self.Log(
            f"ORDER_TAG_MAP: OrderId={oid} | Symbol={sym or symbol} | Source={source} | "
            f"Tag={self._compact_tag_for_log(clean_tag)} | Trace={trace_id}"
        )

    def _record_order_tag_resolve(
        self,
        order_id: int,
        symbol: str,
        resolved_tag: str,
        source: str,
    ) -> None:
        """Log how lifecycle events resolve tags (event/order/oco/cache/symbol-cache)."""
        try:
            oid = int(order_id or 0)
        except Exception:
            oid = 0
        clean_tag = str(resolved_tag or "").strip()
        if oid <= 0 or not clean_tag:
            return
        if oid in self._order_tag_resolve_logged_ids:
            return
        self._order_tag_resolve_logged_ids.add(oid)
        if len(self._order_tag_resolve_logged_ids) > 50000:
            self._order_tag_resolve_logged_ids.clear()
        trace_id = self._extract_trace_id_from_tag(clean_tag) or "NONE"
        sym = self._normalize_symbol_str(symbol)
        self.Log(
            f"ORDER_TAG_RESOLVE: OrderId={oid} | Symbol={sym or symbol} | Source={source} | "
            f"Tag={self._compact_tag_for_log(clean_tag)} | Trace={trace_id}"
        )

    def _record_order_lifecycle_event(
        self,
        status: str,
        order_id: int,
        symbol: str,
        quantity: int = 0,
        fill_price: float = 0.0,
        order_type: str = "",
        order_tag: str = "",
        trace_id: str = "",
        message: str = "",
        source: str = "",
    ) -> None:
        """Persist order lifecycle records independent of console log budget."""
        if not bool(getattr(config, "ORDER_LIFECYCLE_OBSERVABILITY_ENABLED", True)):
            return
        max_rows = int(getattr(config, "ORDER_LIFECYCLE_OBSERVABILITY_MAX_ROWS", 50000))
        self._append_observability_record(
            records=self._order_lifecycle_records,
            overflow_attr="_order_lifecycle_overflow_logged",
            max_rows=max_rows,
            overflow_log_prefix="ORDER_LIFECYCLE_OBSERVABILITY",
            row={
                "time": self.Time.strftime("%Y-%m-%d %H:%M:%S"),
                "status": str(status or "").upper(),
                "order_id": str(int(order_id or 0)),
                "symbol": str(symbol or ""),
                "quantity": str(int(quantity or 0)),
                "fill_price": f"{float(fill_price or 0.0):.6f}",
                "order_type": str(order_type or ""),
                "order_tag": str(order_tag or ""),
                "trace_id": str(trace_id or ""),
                "message": str(message or ""),
                "source": str(source or ""),
            },
        )

    def _get_order_tag(self, order_event: OrderEvent) -> str:
        """Best-effort extraction of original order tag for fill classification."""
        order_id = int(getattr(order_event, "OrderId", 0) or 0)
        symbol = str(getattr(order_event, "Symbol", "") or "")

        event_tag = str(getattr(order_event, "Tag", "") or "").strip()
        if event_tag:
            self._cache_order_tag_hint(order_id, event_tag)
            self._record_order_tag_resolve(order_id, symbol, event_tag, "event")
            return event_tag

        try:
            order = self.Transactions.GetOrderById(order_event.OrderId)
            if order and getattr(order, "Tag", None):
                order_tag = str(order.Tag).strip()
                if order_tag:
                    self._cache_order_tag_hint(order_id, order_tag)
                    self._record_order_tag_resolve(order_id, symbol, order_tag, "order")
                    return order_tag
        except Exception:
            pass

        try:
            if self.oco_manager is not None:
                hinted = self.oco_manager.get_order_tag_hint(order_id)
                if hinted:
                    self._cache_order_tag_hint(order_id, hinted)
                    self._record_order_tag_resolve(order_id, symbol, hinted, "oco")
                    return hinted
        except Exception:
            pass

        cached = self._order_tag_hint_cache.get(order_id, "")
        if cached:
            self._record_order_tag_resolve(order_id, symbol, cached, "cache")
            return cached

        # Last-resort fallback for broker events that drop order tags on cancel/fill.
        symbol_hint = self._get_recent_symbol_fill_tag(symbol, max_age_minutes=480)
        if symbol_hint:
            self._record_order_tag_resolve(order_id, symbol, symbol_hint, "symbol_cache")
            return symbol_hint
        return ""

    def _cache_order_tag_hint(self, order_id: int, tag: str) -> None:
        """Cache order tag hints for lifecycle attribution when broker tags are blank."""
        if order_id <= 0:
            return
        clean = str(tag or "").strip()
        if not clean:
            return
        self._order_tag_hint_cache[order_id] = clean
        if len(self._order_tag_hint_cache) > 25000:
            self._order_tag_hint_cache.clear()

    def _cache_symbol_fill_tag(self, symbol: str, tag: str) -> None:
        """Cache last non-empty fill tag per option symbol for telemetry fallback/reconcile guards."""
        sym = self._normalize_symbol_str(symbol)
        clean = str(tag or "").strip()
        if not sym or not clean:
            return
        self._last_option_fill_tag_by_symbol[sym] = clean
        self._last_option_fill_time_by_symbol[sym] = self.Time
        if len(self._last_option_fill_tag_by_symbol) > 5000:
            self._last_option_fill_tag_by_symbol.clear()
            self._last_option_fill_time_by_symbol.clear()

    def _get_recent_symbol_fill_tag(self, symbol: str, max_age_minutes: int = 240) -> str:
        """Return last cached fill tag for symbol when fresh enough."""
        sym = self._normalize_symbol_str(symbol)
        if not sym:
            return ""
        tag = str(self._last_option_fill_tag_by_symbol.get(sym, "") or "").strip()
        ts = self._last_option_fill_time_by_symbol.get(sym)
        if not tag or ts is None:
            return ""
        try:
            age = (self.Time - ts).total_seconds() / 60.0
            if age > float(max_age_minutes):
                return ""
        except Exception:
            return ""
        return tag

    def _extract_trace_id_from_tag(self, order_tag: str) -> str:
        """Extract trace id from order tag (best-effort) for RCA joins."""
        if not order_tag:
            return ""
        tag = str(order_tag)
        markers = ("trace=", "trace_id=", "trace:")
        lowered = tag.lower()
        for marker in markers:
            idx = lowered.find(marker)
            if idx < 0:
                continue
            value = tag[idx + len(marker) :].strip()
            if not value:
                return ""
            for sep in ("|", ";", ",", " "):
                cut = value.find(sep)
                if cut >= 0:
                    value = value[:cut]
                    break
            return value.strip()
        return ""

    def _compact_tag_for_log(self, order_tag: str, max_chars: int = 64) -> str:
        """Trim noisy broker tags to keep logs under budget while preserving correlation."""
        tag = str(order_tag or "").strip()
        if not tag:
            return "NO_TAG"
        if len(tag) <= max_chars:
            return tag
        return f"{tag[:max_chars]}..."

    def _micro_dte_bucket(self, dte: Optional[int]) -> str:
        """Normalize intraday DTE to compact telemetry buckets."""
        try:
            d = int(dte) if dte is not None else -1
        except Exception:
            d = -1
        if d in (2, 3, 4, 5):
            return str(d)
        return "OTHER"

    def _inc_micro_dte_counter(self, store: Dict[str, int], dte: Optional[int]) -> str:
        """Increment a MICRO DTE diagnostics counter and return resolved bucket."""
        bucket = self._micro_dte_bucket(dte)
        store[bucket] = int(store.get(bucket, 0)) + 1
        return bucket

    def _record_micro_drop_reason_dte(self, code: str, dte: Optional[int]) -> None:
        """Track drop reason x DTE bucket for funnel RCA."""
        bucket = self._micro_dte_bucket(dte)
        reason = str(code or "E_UNKNOWN")
        key = f"{reason}|{bucket}"
        self._diag_micro_drop_reason_by_dte[key] = (
            int(self._diag_micro_drop_reason_by_dte.get(key, 0)) + 1
        )

    def _intraday_engine_bucket_from_strategy(self, strategy: Optional[Any]) -> str:
        """Normalize intraday strategy into daily summary engine buckets."""
        name = str(getattr(strategy, "value", strategy) or "").upper()
        if "ITM_MOMENTUM" in name:
            return "ITM"
        if "MICRO_" in name:
            return "MICRO"
        return "OTHER"

    def _inc_intraday_engine_counter(self, store: Dict[str, int], strategy: Optional[Any]) -> str:
        """Increment per-engine intraday diagnostics counter and return bucket."""
        bucket = self._intraday_engine_bucket_from_strategy(strategy)
        store[bucket] = int(store.get(bucket, 0)) + 1
        return bucket

    def _record_intraday_drop_reason(self, code: str, strategy: Optional[Any]) -> None:
        """Persist drop-reason metrics independent of log throttling."""
        reason = self._canonical_options_reason_code(code or "E_UNKNOWN")
        self._diag_intraday_drop_reason_counts[reason] = (
            int(self._diag_intraday_drop_reason_counts.get(reason, 0)) + 1
        )
        bucket = self._intraday_engine_bucket_from_strategy(strategy)
        store = self._diag_intraday_drop_reason_counts_by_engine.setdefault(bucket, {})
        store[reason] = int(store.get(reason, 0)) + 1

    def _router_engine_bucket(self, source_tag: str, detail: str = "") -> str:
        """Map router source tags to engine buckets (VASS / ITM / MICRO / OTHER)."""
        text = f"{source_tag or ''} {detail or ''}".upper()
        if "VASS" in text:
            return "VASS"
        if "ITM" in text:
            return "ITM"
        if "MICRO" in text:
            return "MICRO"
        return "OTHER"

    def _record_vass_reject_reason(self, reason_code: str) -> None:
        """Track VASS reject reason counts for daily funnel RCA."""
        code = str(reason_code or "UNKNOWN")
        self._diag_vass_reject_reason_counts[code] = (
            int(self._diag_vass_reject_reason_counts.get(code, 0)) + 1
        )

    def _inc_transition_path_counter(self, key: str) -> None:
        """Track detector/eod transition trigger path usage for daily RCA."""
        label = str(key or "").strip().upper()
        if not label:
            return
        self._diag_transition_path_counts[label] = (
            int(self._diag_transition_path_counts.get(label, 0)) + 1
        )

    def _is_micro_entry_fill(self, symbol: str, fill_qty: float, order_tag: str) -> bool:
        """Classify fill as MICRO entry for recovery and EOD safety sweeps."""
        if fill_qty <= 0:
            return False
        symbol_norm = self._normalize_symbol_str(symbol)
        if "QQQ" not in symbol_norm or ("C" not in symbol_norm and "P" not in symbol_norm):
            return False
        tag_upper = str(order_tag or "").upper()
        if tag_upper.startswith("MICRO:") or tag_upper == "MICRO":
            return True
        if tag_upper.startswith("HEDGE:") or tag_upper == "HEDGE":
            return True
        # Some broker fills resolve tag from trace fallback. Treat MICRO traces as intraday.
        if "|TRACE=MICRO_" in tag_upper and "VASS" not in tag_upper and "ITM:" not in tag_upper:
            return True
        return False

    def _is_spread_fill_symbol(self, symbol: str) -> bool:
        """
        Return True only when symbol matches currently tracked spread-entry legs.

        Prevents MICRO single-leg fills from being misclassified as spread fills when
        stale spread pending state is present.
        """
        symbol_norm = self._normalize_symbol_str(symbol)
        if not symbol_norm:
            return False

        tracker = self._spread_fill_tracker
        if tracker is not None:
            tracker_symbols = {
                self._normalize_symbol_str(tracker.long_leg_symbol),
                self._normalize_symbol_str(tracker.short_leg_symbol),
            }
            if symbol_norm in tracker_symbols:
                return True

        pending_long, pending_short = self.options_engine.get_pending_spread_legs()
        pending_symbols = set()
        if pending_long is not None:
            pending_symbols.add(self._normalize_symbol_str(pending_long.symbol))
        if pending_short is not None:
            pending_symbols.add(self._normalize_symbol_str(pending_short.symbol))
        return symbol_norm in pending_symbols

    def _sync_intraday_oco(
        self,
        symbol: str,
        position: Any,
        quantity: int,
        reason: str,
    ) -> None:
        """
        Ensure exactly one active OCO pair is live for an intraday MICRO symbol.

        Called on entry and partial-close so remaining contracts stay protected.
        """
        try:
            symbol_norm = self._normalize_symbol_str(symbol)
            qty = int(max(0, quantity))
            if not symbol_norm:
                self.Log(f"OCO_SYNC_SKIP: Invalid symbol | Raw={symbol} | Reason={reason}")
                return
            if qty <= 0:
                self.Log(
                    f"OCO_SYNC_SKIP: Non-positive quantity | Symbol={symbol_norm} | Qty={qty} | Reason={reason}"
                )
                return
            if position is None:
                self.Log(
                    f"OCO_SYNC_SKIP: Missing position seed | Symbol={symbol_norm} | Reason={reason}"
                )
                return
            entry_price = float(getattr(position, "entry_price", 0.0) or 0.0)
            stop_price = float(getattr(position, "stop_price", 0.0) or 0.0)
            target_price = float(getattr(position, "target_price", 0.0) or 0.0)
            entry_strategy = str(getattr(position, "entry_strategy", "UNKNOWN") or "UNKNOWN")
            if isinstance(position, dict):
                entry_price = float(position.get("entry_price", entry_price) or 0.0)
                stop_price = float(position.get("stop_price", stop_price) or 0.0)
                target_price = float(position.get("target_price", target_price) or 0.0)
                entry_strategy = str(position.get("entry_strategy", entry_strategy) or "UNKNOWN")
            if entry_price <= 0 or stop_price <= 0 or target_price <= 0:
                self.Log(
                    f"OCO_SYNC_SKIP: Invalid OCO prices | Symbol={symbol_norm} | "
                    f"Entry={entry_price:.2f} Stop={stop_price:.2f} Target={target_price:.2f} | "
                    f"Reason={reason}"
                )
                return

            active_pair = self.oco_manager.get_active_pair(symbol_norm)
            if active_pair is not None:
                active_qty = abs(int(getattr(active_pair.stop_leg, "quantity", 0) or 0))
                active_stop = float(getattr(active_pair.stop_leg, "trigger_price", 0.0) or 0.0)
                active_target = float(getattr(active_pair.profit_leg, "trigger_price", 0.0) or 0.0)
                price_eps = float(getattr(config, "OCO_RESYNC_PRICE_EPS", 0.01))
                qty_same = active_qty == qty
                stop_same = abs(active_stop - stop_price) <= price_eps
                target_same = abs(active_target - target_price) <= price_eps
                if qty_same and stop_same and target_same:
                    return
                self.oco_manager.cancel_by_symbol(symbol_norm, reason=f"OCO_RESYNC_{reason}")
                self.Log(
                    f"OCO_RESYNC: Cancelled stale OCO | {symbol_norm} | "
                    f"OldQty={active_qty} NewQty={qty} | "
                    f"OldStop=${active_stop:.2f} NewStop=${stop_price:.2f} | "
                    f"OldTarget=${active_target:.2f} NewTarget=${target_price:.2f} | "
                    f"Reason={reason}"
                )

            entry_tag_hint = ""
            if isinstance(position, dict):
                entry_tag_hint = str(position.get("entry_tag", "") or "")
            if not entry_tag_hint:
                entry_tag_hint = self._get_recent_symbol_fill_tag(symbol_norm, max_age_minutes=240)
            trace_id = self._extract_trace_id_from_tag(entry_tag_hint)
            engine_prefix = self._oco_engine_prefix_for_strategy(entry_strategy)
            tag_context = f"{engine_prefix}:{entry_strategy}"
            if trace_id:
                tag_context = f"{tag_context}|trace={trace_id}"

            oco_pair = self.oco_manager.create_oco_pair(
                symbol=symbol_norm,
                entry_price=entry_price,
                stop_price=stop_price,
                target_price=target_price,
                quantity=qty,
                current_date=str(self.Time.date()),
                tag_context=tag_context,
            )
            if oco_pair and self.oco_manager.submit_oco_pair(oco_pair, current_time=str(self.Time)):
                self.Log(
                    f"OCO_SYNC: {reason} | {symbol_norm} | Qty={qty} | "
                    f"Stop=${stop_price:.2f} | "
                    f"Target=${target_price:.2f}"
                )
        except Exception as e:
            self.Log(f"OCO_SYNC_ERROR: {symbol} | Reason={reason} | {e}")

    def _build_spread_runtime_key(self, spread: Any) -> str:
        """Build canonical spread key for runtime trackers."""
        return (
            f"{self._normalize_symbol_str(spread.long_leg.symbol)}|"
            f"{self._normalize_symbol_str(spread.short_leg.symbol)}|"
            f"{str(getattr(spread, 'entry_time', '') or '')}"
        )

    def _record_spread_exit_reason(self, spread_key: str, reason: str) -> None:
        """Persist latest spread exit reason for fill-path attribution."""
        if not spread_key:
            return
        reason_str = str(reason or "").strip()
        if not reason_str:
            return
        self._spread_last_exit_reason[spread_key] = reason_str[:180]

    def _capture_router_rejections(self, stage: str) -> None:
        """Aggregate router rejection telemetry for daily summary RCA."""
        try:
            rejects = self.portfolio_router.get_last_rejections()
        except Exception:
            self._recent_router_rejections = []
            return
        self._recent_router_rejections = list(rejects)
        if not rejects:
            return
        for rej in rejects:
            code = str(getattr(rej, "code", "UNKNOWN") or "UNKNOWN")
            source_tag = str(getattr(rej, "source_tag", "") or "")
            detail = str(getattr(rej, "detail", "") or "")
            engine_bucket = self._router_engine_bucket(source_tag=source_tag, detail=detail)
            self._diag_intraday_router_reject_count += 1
            self._diag_router_reject_reason_counts[code] = (
                int(self._diag_router_reject_reason_counts.get(code, 0)) + 1
            )
            engine_store = self._diag_router_reject_reason_counts_by_engine.setdefault(
                engine_bucket, {}
            )
            engine_store[code] = int(engine_store.get(code, 0)) + 1
            self._record_router_rejection_event(
                stage=stage,
                code=code,
                symbol=str(getattr(rej, "symbol", "") or ""),
                source_tag=source_tag,
                trace_id=str(getattr(rej, "trace_id", "") or ""),
                detail=detail,
                engine_bucket=engine_bucket,
            )
        try:
            self.portfolio_router.clear_last_rejections()
        except Exception:
            pass

    def _get_recent_router_rejections(self) -> List[Any]:
        """Last captured router rejection snapshot for trace-level attribution."""
        return list(getattr(self, "_recent_router_rejections", []) or [])

    def _record_router_rejection_event(
        self,
        stage: str,
        code: str,
        symbol: str,
        source_tag: str,
        trace_id: str,
        detail: str,
        engine_bucket: str,
    ) -> None:
        """Persist router rejection details for RCA without relying on console logs."""
        if not bool(getattr(config, "ROUTER_REJECTION_OBSERVABILITY_ENABLED", True)):
            return
        max_rows = int(getattr(config, "ROUTER_REJECTION_OBSERVABILITY_MAX_ROWS", 25000))
        self._append_observability_record(
            records=self._router_rejection_records,
            overflow_attr="_router_rejection_overflow_logged",
            max_rows=max_rows,
            overflow_log_prefix="ROUTER_REJECTION_OBSERVABILITY",
            row={
                "time": self.Time.strftime("%Y-%m-%d %H:%M:%S"),
                "stage": str(stage or ""),
                "code": str(code or "UNKNOWN"),
                "symbol": str(symbol or ""),
                "source_tag": str(source_tag or ""),
                "trace_id": str(trace_id or ""),
                "detail": str(detail or ""),
                "engine": str(engine_bucket or "OTHER").upper(),
            },
        )

    def _classify_exit_path(self, reason: str, order_tag: str = "") -> str:
        """Classify exit path for daily P&L attribution."""
        text = f"{reason or ''} {order_tag or ''}".upper()
        if "ORPHAN" in text:
            return "ORPHAN"
        if "RETRY" in text:
            return "RETRY"
        if "RECON" in text:
            return "RECONCILE"
        if "TRAIL" in text:
            return "TRAIL"
        if "TARGET" in text or "PROFIT" in text:
            return "TARGET"
        if "STOP" in text or "HARD_STOP" in text:
            return "STOP"
        if "DTE" in text or "FORCE_EXIT_DTE" in text:
            return "DTE"
        if "EOD" in text or "SWEEP" in text or "FRIDAY" in text:
            return "EOD"
        return "OTHER"

    def _record_exit_path_pnl(
        self,
        reason: str,
        pnl_dollars: float,
        order_tag: str = "",
        engine_tag: str = "OTHER",
    ) -> None:
        """Track exit-path counts and realized P&L for daily diagnostics."""
        path = self._classify_exit_path(reason=reason, order_tag=order_tag)
        self._diag_exit_path_counts[path] = int(self._diag_exit_path_counts.get(path, 0)) + 1
        self._diag_exit_path_pnl[path] = float(self._diag_exit_path_pnl.get(path, 0.0)) + float(
            pnl_dollars
        )
        engine_bucket = str(engine_tag or "OTHER").upper()
        if engine_bucket not in self._diag_exit_path_counts_by_engine:
            engine_bucket = "OTHER"
        cnt_store = self._diag_exit_path_counts_by_engine.setdefault(engine_bucket, {})
        pnl_store = self._diag_exit_path_pnl_by_engine.setdefault(engine_bucket, {})
        cnt_store[path] = int(cnt_store.get(path, 0)) + 1
        pnl_store[path] = float(pnl_store.get(path, 0.0)) + float(pnl_dollars)

    def _normalize_spread_close_quantities(self, signal: TargetWeight) -> None:
        """Normalize spread close quantities from live holdings to avoid stale qty closes."""
        try:
            md = signal.metadata or {}
            if not bool(md.get("spread_close_short", False)):
                return
            long_symbol = self._normalize_symbol_str(signal.symbol)
            short_symbol = self._normalize_symbol_str(md.get("spread_short_leg_symbol", ""))
            if not long_symbol or not short_symbol:
                return
            live_long = abs(self._get_option_holding_quantity(long_symbol))
            live_short = abs(self._get_option_holding_quantity(short_symbol))
            if live_long <= 0 or live_short <= 0:
                return
            close_qty = min(live_long, live_short)
            if close_qty <= 0:
                return
            signal.requested_quantity = int(close_qty)
            md["spread_short_leg_quantity"] = int(close_qty)
            signal.metadata = md
        except Exception:
            return

    def _record_spread_removal(self, reason: str, count: int = 1, context: str = "") -> None:
        """Centralized spread-removal diagnostics accounting."""
        if count <= 0:
            return
        self._diag_spread_position_removed_count += count
        if reason == "fill_path":
            self._diag_spread_removed_fill_path_count += count
        elif reason == "ghost_path":
            self._diag_spread_ghost_removed_count += count
        else:
            self.Log(
                f"SPREAD_DIAG_WARNING: Unknown removal reason '{reason}' | "
                f"Count={count} | Context={context}"
            )

    def _reconcile_spread_ghosts(self, mode: str) -> int:
        """
        Reconcile spread state ghosts with mode-aware clearing policy.

        Modes:
            - sod: immediate guarded clear when both legs are flat
            - friday: immediate guarded clear once per day sweep
            - intraday: non-destructive health check; emergency clear only after
              N consecutive flat detections
        """
        mode_norm = str(mode or "sod").strip().lower()
        spreads = list(self.options_engine.get_spread_positions())
        if not spreads:
            return 0

        threshold = max(1, int(getattr(config, "SPREAD_GHOST_INTRADAY_CLEAR_CONSECUTIVE", 2)))
        health_log_minutes = max(1, int(getattr(config, "SPREAD_GHOST_HEALTH_LOG_MINUTES", 60)))
        cleared = 0

        for spread in spreads:
            spread_key = self._build_spread_runtime_key(spread)

            long_held = self.Portfolio[spread.long_leg.symbol].Invested
            short_held = self.Portfolio[spread.short_leg.symbol].Invested
            if long_held or short_held:
                self._spread_ghost_flat_streak_by_key.pop(spread_key, None)
                self._spread_ghost_last_log_by_key.pop(spread_key, None)
                continue

            streak = self._spread_ghost_flat_streak_by_key.get(spread_key, 0) + 1
            self._spread_ghost_flat_streak_by_key[spread_key] = streak

            should_clear = mode_norm in {"sod", "friday"}
            if mode_norm == "intraday" and streak >= threshold:
                should_clear = True

            last_log = self._spread_ghost_last_log_by_key.get(spread_key)
            due = (
                last_log is None
                or (self.Time - last_log).total_seconds() / 60.0 >= health_log_minutes
            )
            if (due or should_clear) and self._should_log_backtest_category(
                "LOG_SPREAD_RECONCILE_BACKTEST_ENABLED", False
            ):
                self.Log(
                    f"SPREAD_GHOST_HEALTH: Mode={mode_norm.upper()} | "
                    f"Key={spread_key} | FlatStreak={streak} | "
                    f"Threshold={threshold}"
                )
                self._spread_ghost_last_log_by_key[spread_key] = self.Time

            if not should_clear:
                continue

            self._clear_spread_runtime_trackers_by_key(spread_key)
            removed = self.options_engine.remove_spread_position(str(spread.long_leg.symbol))
            if removed is None:
                continue

            self._record_spread_removal(
                reason="ghost_path",
                count=1,
                context=f"RECON_{mode_norm.upper()}_BOTH_LEGS_FLAT",
            )
            cleared += 1
            self.Log(
                f"SPREAD_GHOST_CLEAR: Mode={mode_norm.upper()} | "
                f"Key={spread_key} | FlatStreak={streak}"
            )
            if self.portfolio_router:
                self.portfolio_router.unregister_spread_margin_by_legs(
                    str(removed.long_leg.symbol),
                    str(removed.short_leg.symbol),
                )

        return cleared

    def _clear_spread_runtime_trackers_by_key(self, spread_key: str) -> None:
        """Clear runtime tracker maps for a spread key (long|short)."""
        self._spread_close_trackers.pop(spread_key, None)
        self._spread_forced_close_retry.pop(spread_key, None)
        self._spread_forced_close_reason.pop(spread_key, None)
        self._spread_forced_close_cancel_counts.pop(spread_key, None)
        self._spread_forced_close_retry_cycles.pop(spread_key, None)
        self._spread_last_close_submit_at.pop(spread_key, None)
        self._spread_last_exit_reason.pop(spread_key, None)
        self._spread_exit_mark_cache.pop(spread_key, None)
        self._spread_ghost_flat_streak_by_key.pop(spread_key, None)
        self._spread_ghost_last_log_by_key.pop(spread_key, None)

    def _log_order_lifecycle_issue(self, order_event: OrderEvent, status: str) -> None:
        """Compact attribution log for canceled/invalid orders."""
        if not self._should_log_backtest_category("LOG_ORDER_LIFECYCLE_BACKTEST_ENABLED", True):
            return
        max_per_day = int(getattr(config, "LOG_ORDER_LIFECYCLE_MAX_PER_DAY", 200))
        if self._order_lifecycle_log_count >= max_per_day:
            self._order_lifecycle_suppressed_count += 1
            return
        order = self.Transactions.GetOrderById(order_event.OrderId)
        order_type = str(getattr(order, "Type", "UNKNOWN")) if order is not None else "UNKNOWN"
        raw_tag = self._get_order_tag(order_event)
        tag = raw_tag if raw_tag else f"NO_TAG:{order_type}"
        msg = str(getattr(order_event, "Message", "") or "")
        self.Log(
            f"ORDER_LIFECYCLE: Status={status} | OrderId={order_event.OrderId} | "
            f"Symbol={order_event.Symbol} | Type={order_type} | Tag={tag} | Msg={msg}"
        )
        self._order_lifecycle_log_count += 1

    def _forward_execution_event(
        self,
        order_event: OrderEvent,
        status: str,
        fill_price: float = 0.0,
        fill_quantity: int = 0,
        rejection_reason: str = "",
    ) -> None:
        """
        V6.22: Forward broker events to ExecutionEngine only for mapped orders.

        OCO/manual/spread-atomic orders are external to ExecutionEngine and should
        not be logged as EXEC: UNKNOWN_ORDER repeatedly.
        """
        broker_id = int(order_event.OrderId)
        if self.execution_engine.get_order_by_broker_id(broker_id) is None:
            if broker_id not in self._external_exec_event_logged:
                order = self.Transactions.GetOrderById(broker_id)
                order_type = (
                    str(getattr(order, "Type", "UNKNOWN")) if order is not None else "UNKNOWN"
                )
                tag = self._get_order_tag(order_event)
                if not tag:
                    tag = f"NO_TAG:{order_type}"
                self.Log(
                    f"EXEC_EXTERNAL: BrokerID={broker_id} | Status={status} | "
                    f"Symbol={order_event.Symbol} | Tag={tag}"
                )
                self._external_exec_event_logged.add(broker_id)
                max_external_cache = 20000
                if len(self._external_exec_event_logged) > max_external_cache:
                    self._external_exec_event_logged.clear()
                    self.Log(
                        f"EXEC_EXTERNAL_CACHE_RESET: Cleared external event cache at "
                        f"{max_external_cache}+ entries"
                    )
            return

        self.execution_engine.on_order_event(
            broker_order_id=broker_id,
            status=status,
            fill_price=fill_price,
            fill_quantity=fill_quantity,
            rejection_reason=str(rejection_reason or ""),
        )

    # =========================================================================
    # STATE MANAGEMENT HELPERS
    # =========================================================================

    def _load_state(self) -> None:
        """
        Load persisted state from ObjectStore.

        Restores:
            - Capital Engine state (phase, lockbox)
            - Cold Start state (days_running)
            - Risk Engine state (baselines, safeguards, governor)
            - Startup Gate state (V2.29)
        """
        try:
            # V3.0 FIX: Pass ALL engines to load_all (was only passing startup_gate)
            self.state_manager.load_all(
                capital_engine=self.capital_engine,
                cold_start_engine=self.cold_start_engine,
                risk_engine=self.risk_engine,
                startup_gate=self.startup_gate,
            )

            # V3.0 FIX: Restore governor scale from risk_engine state
            # Governor scale is stored in risk_engine but used in main.py
            if hasattr(self.risk_engine, "_governor_scale"):
                self._governor_scale = self.risk_engine._governor_scale
                if self._governor_scale < 1.0:
                    self.Log(
                        f"STATE_RESTORE: Governor scale = {self._governor_scale:.0%} "
                        f"(drawdown protection active)"
                    )

            # V3.0: Load options engine state
            if hasattr(self, "options_engine"):
                try:
                    if self.ObjectStore.ContainsKey("options_engine_state"):
                        raw = self.ObjectStore.Read("options_engine_state")
                        # V3.3 P0-2: Use JSON instead of ast.literal_eval for safety
                        opt_state = json.loads(raw)
                        self.options_engine.restore_state(opt_state)
                        self.Log("STATE_RESTORE: Options engine state loaded")
                except Exception as e:
                    self.Log(f"STATE_WARN: Failed to load options state - {e}")

            # V3.0: Load OCO manager state
            if hasattr(self, "oco_manager"):
                try:
                    if self.ObjectStore.ContainsKey("oco_manager_state"):
                        raw = self.ObjectStore.Read("oco_manager_state")
                        oco_state = json.loads(raw)
                        self.oco_manager.restore_state(oco_state)
                        self.Log("STATE_RESTORE: OCO manager state loaded")
                except Exception as e:
                    self.Log(f"STATE_WARN: Failed to load OCO state - {e}")

            # V3.3 P0-1: Load regime engine state
            if hasattr(self, "regime_engine"):
                try:
                    if self.ObjectStore.ContainsKey("regime_engine_state"):
                        raw = self.ObjectStore.Read("regime_engine_state")
                        regime_state = json.loads(raw)
                        self.regime_engine.restore_state(regime_state)
                        self.Log("STATE_RESTORE: Regime engine V3.3 state loaded")
                except Exception as e:
                    self.Log(f"STATE_WARN: Failed to load regime state - {e}")

            # V6.12: Load monthly P&L tracker state
            if hasattr(self, "pnl_tracker"):
                self.pnl_tracker.load()

        except Exception as e:
            self.Log(f"STATE_ERROR: Failed to load state - {e}")

    def _save_state(self) -> None:
        """
        Save state to ObjectStore.

        Persists all engine states for next session or restart recovery.
        """
        try:
            # Save state via state manager with engine instances
            # Note: regime_engine doesn't have get_state_for_persistence, skip it
            self.state_manager.save_all(
                capital_engine=self.capital_engine,
                cold_start_engine=self.cold_start_engine,
                risk_engine=self.risk_engine,
                startup_gate=self.startup_gate,
            )

            # V2.1: Save options engine and OCO manager state
            # V3.3 P0-2: Use JSON instead of str() for safe serialization
            if hasattr(self, "options_engine"):
                opt_state = self.options_engine.get_state_for_persistence()
                self.ObjectStore.Save("options_engine_state", json.dumps(opt_state))

            if hasattr(self, "oco_manager"):
                oco_state = self.oco_manager.get_state_for_persistence()
                self.ObjectStore.Save("oco_manager_state", json.dumps(oco_state))

            # V3.3 P0-1: Save regime engine state (includes V3.3 simplified model state)
            if hasattr(self, "regime_engine"):
                regime_state = self.regime_engine.get_state_for_persistence()
                self.ObjectStore.Save("regime_engine_state", json.dumps(regime_state))

            # V6.12: Save monthly P&L tracker state
            if hasattr(self, "pnl_tracker"):
                self.pnl_tracker.save()

        except Exception as e:
            self.Log(f"STATE_ERROR: Failed to save state - {e}")

    def _save_state_throttled(self, reason: str, min_minutes: int = 5) -> None:
        """Best-effort intraday persistence, throttled to reduce ObjectStore churn."""
        if not bool(getattr(self, "LiveMode", False)):
            return
        now_dt = self.Time if hasattr(self, "Time") else None
        if now_dt is None:
            return
        last_dt = getattr(self, "_last_state_persist_at", None)
        if last_dt is not None:
            try:
                elapsed_min = (now_dt - last_dt).total_seconds() / 60.0
                if elapsed_min < float(max(1, int(min_minutes))):
                    return
            except Exception:
                pass
        try:
            self._save_state()
            self._last_state_persist_at = now_dt
            self.Log(f"STATE_SNAPSHOT: Saved ({reason}) | {now_dt.strftime('%Y-%m-%d %H:%M:%S')}")
        except Exception as e:
            self.Log(f"STATE_WARN: Throttled save failed ({reason}) - {e}")

    def _clear_micro_symbol_tracking(self, symbol: str) -> None:
        """Clear MICRO tracking artifacts for a symbol after flat/orphan handling."""
        sym_norm = self._normalize_symbol_str(symbol)
        if not sym_norm:
            return
        self._micro_open_symbols.discard(sym_norm)
        self._intraday_entry_snapshot.pop(sym_norm, None)
        self._clear_intraday_close_guard(sym_norm)

    def _has_open_order_for_symbol(self, symbol: str, tag_contains: str = "") -> bool:
        """Return True when an open order exists for symbol, optionally filtered by tag."""
        symbol_norm = self._normalize_symbol_str(symbol)
        if not symbol_norm:
            return False
        tag_filter = str(tag_contains or "").upper().strip()
        for open_order in self.Transactions.GetOpenOrders():
            if self._normalize_symbol_str(open_order.Symbol) != symbol_norm:
                continue
            if not tag_filter:
                return True
            open_tag = str(getattr(open_order, "Tag", "") or "").upper()
            if tag_filter in open_tag:
                return True
        return False

    def _has_open_non_oco_order_for_symbol(self, symbol: str) -> bool:
        """Return True when symbol has an open non-OCO order (entry/exit in flight)."""
        symbol_norm = self._normalize_symbol_str(symbol)
        if not symbol_norm:
            return False
        for open_order in self.Transactions.GetOpenOrders():
            if self._normalize_symbol_str(open_order.Symbol) != symbol_norm:
                continue
            open_tag = str(getattr(open_order, "Tag", "") or "").upper()
            if "OCO_STOP:" in open_tag or "OCO_PROFIT:" in open_tag:
                continue
            return True
        return False

    def _reconcile_positions(self, mode: str = "sod") -> None:
        """
        Reconcile internal position tracking with broker state.

        Modes:
            - sod: baseline reconciliation (allows guarded spread ghost clear)
            - intraday: orphan cleanup + guarded emergency spread clear only
            - friday: single guarded late-week sweep
        """
        try:
            if not self._is_primary_market_open():
                return
            mode_norm = str(mode or "sod").strip().lower()
            if mode_norm not in {"sod", "intraday", "friday"}:
                mode_norm = "sod"
            self._last_reconcile_positions_run = self.Time

            # Mode-aware spread ghost health checks and guarded clears.
            self._reconcile_spread_ghosts(mode=mode_norm)

            option_holdings = {}
            option_symbols = {}
            for kvp in self.Portfolio:
                holding = kvp.Value
                if not holding.Invested:
                    continue
                symbol = holding.Symbol
                if symbol.SecurityType != SecurityType.Option:
                    continue
                option_holdings[str(symbol)] = int(holding.Quantity)
                option_symbols[str(symbol)] = symbol

            tracked_symbols = set()
            for spread in self.options_engine.get_spread_positions():
                tracked_symbols.add(str(spread.long_leg.symbol))
                tracked_symbols.add(str(spread.short_leg.symbol))

            for intraday in self.options_engine.get_intraday_positions():
                if intraday is not None and intraday.contract is not None:
                    tracked_symbols.add(str(intraday.contract.symbol))

            single = self.options_engine.get_position()
            if single is not None:
                tracked_symbols.add(str(single.contract.symbol))

            # Keep MICRO tracker symbols in reconcile scope when holdings still exist.
            # This avoids false orphan liquidations during transient state desync.
            for sym in list(self._micro_open_symbols):
                if sym in option_holdings:
                    tracked_symbols.add(sym)

            if tracked_symbols and not option_holdings:
                if mode_norm != "intraday":
                    spread_count_before = len(self.options_engine.get_spread_positions())
                    self.options_engine.clear_all_positions()
                    self._spread_close_trackers.clear()
                    self._spread_forced_close_retry.clear()
                    self._spread_forced_close_reason.clear()
                    self._spread_forced_close_cancel_counts.clear()
                    self._spread_forced_close_retry_cycles.clear()
                    self._spread_last_close_submit_at.clear()
                    self._spread_exit_mark_cache.clear()
                    self._spread_ghost_flat_streak_by_key.clear()
                    self._spread_ghost_last_log_by_key.clear()
                    if spread_count_before > 0:
                        # Broker is flat while engine still tracked live spreads.
                        # Count as state removals only (not confirmed fill reconciliations).
                        self._record_spread_removal(
                            reason="ghost_path",
                            count=spread_count_before,
                            context=f"RECON_{mode_norm.upper()}_ZOMBIE_CLEAR",
                        )
                    if self.portfolio_router:
                        self.portfolio_router.clear_all_spread_margins()
                    self.Log(
                        f"RECON_ZOMBIE_CLEARED: Cleared stale internal option state | "
                        f"Mode={mode_norm.upper()} | Tracked={len(tracked_symbols)}"
                    )
                    tracked_symbols = set()
                else:
                    if self._should_log_backtest_category(
                        "LOG_SPREAD_RECONCILE_BACKTEST_ENABLED", False
                    ):
                        self.Log(
                            f"RECON_INTRADAY_SKIP_ZOMBIE_CLEAR: Tracked={len(tracked_symbols)} | "
                            f"HoldingOptions={len(option_holdings)}"
                        )
                    # Keep guarded spread ghost policy intact intraday, but clear stale
                    # single-leg state when broker is flat and no option orders remain.
                    has_open_option_orders = any(
                        order.Symbol.SecurityType == SecurityType.Option
                        for order in self.Transactions.GetOpenOrders()
                    )
                    if not has_open_option_orders:
                        for intraday_pos in self.options_engine.get_intraday_positions():
                            if intraday_pos is not None and intraday_pos.contract is not None:
                                intraday_sym = self._normalize_symbol_str(
                                    intraday_pos.contract.symbol
                                )
                                self.options_engine.remove_intraday_position(symbol=intraday_sym)
                                self._clear_micro_symbol_tracking(intraday_sym)
                        swing_pos = self.options_engine.get_position()
                        if swing_pos is not None:
                            swing_sym = self._normalize_symbol_str(swing_pos.contract.symbol)
                            self.options_engine.remove_position(swing_sym)
                            self._clear_micro_symbol_tracking(swing_sym)
                        if self.options_engine.has_pending_intraday_entry():
                            self.options_engine.cancel_pending_intraday_entry()
                        if self.options_engine.has_pending_swing_entry():
                            self.options_engine.cancel_pending_swing_entry()
                        self.options_engine.cancel_pending_intraday_exit()
                        for stale_sym in list(self._micro_open_symbols):
                            self._clear_micro_symbol_tracking(stale_sym)
                        tracked_symbols = set()
                        if self._should_log_backtest_category(
                            "LOG_SPREAD_RECONCILE_BACKTEST_ENABLED", False
                        ):
                            self.Log(
                                "RECON_INTRADAY_STALE_SINGLE_CLEARED: Broker flat, no open option orders"
                            )

            # Friday sweep is spread-state only to avoid introducing new close-order behavior.
            if mode_norm == "friday":
                return

            orphan_symbols = [s for s in option_holdings.keys() if s not in tracked_symbols]
            orphan_set = set(orphan_symbols)

            # Clear orphan guard state for symbols that are no longer orphaned.
            stale_symbols = [
                s for s in list(self._recon_orphan_seen_streak.keys()) if s not in orphan_set
            ]
            for stale_sym in stale_symbols:
                self._recon_orphan_seen_streak.pop(stale_sym, None)
                self._recon_orphan_first_seen_at.pop(stale_sym, None)
                self._recon_orphan_last_log_at.pop(stale_sym, None)

            intraday_min_streak = int(getattr(config, "RECON_INTRADAY_ORPHAN_MIN_STREAK", 2))
            intraday_min_age_min = float(
                getattr(config, "RECON_INTRADAY_ORPHAN_MIN_AGE_MINUTES", 20)
            )
            intraday_guard_log_min = float(
                getattr(config, "RECON_INTRADAY_ORPHAN_LOG_THROTTLE_MINUTES", 30)
            )

            for sym_str in orphan_symbols:
                try:
                    today = str(self.Time.date())
                    # V10.8: avoid RECON_ORPHAN churn when same-day MICRO sweep/force-close is already active.
                    sweep_submitted_today = (
                        self._intraday_force_exit_submitted_symbols.get(sym_str) == today
                    )
                    if sweep_submitted_today or sym_str in self._intraday_close_in_progress_symbols:
                        if self._should_log_backtest_category(
                            "LOG_SPREAD_RECONCILE_BACKTEST_ENABLED", False
                        ):
                            self.Log(
                                f"RECON_ORPHAN_SKIP_SWEEP_IN_PROGRESS: {sym_str} | Mode={mode_norm.upper()}"
                            )
                        continue

                    if self._recon_orphan_close_submitted.get(sym_str) == today:
                        if self._has_open_order_for_symbol(
                            sym_str, tag_contains="RECON_ORPHAN_OPTION"
                        ):
                            continue
                        self._recon_orphan_close_submitted.pop(sym_str, None)

                    # Avoid duplicate orphan close submits while a prior order is still open.
                    has_pending_orphan_close = self._has_open_order_for_symbol(
                        sym_str, tag_contains="RECON_ORPHAN_OPTION"
                    )
                    has_any_open_order = self._has_open_order_for_symbol(sym_str)
                    if has_pending_orphan_close:
                        continue

                    broker_symbol = option_symbols[sym_str]
                    holding = self.Portfolio[broker_symbol]
                    if not holding.Invested or abs(float(holding.Quantity)) <= 0:
                        self.Log(
                            f"RECON_ORPHAN_SKIP: {sym_str} | No live position at liquidation time"
                        )
                        self._clear_micro_symbol_tracking(sym_str)
                        self._recon_orphan_seen_streak.pop(sym_str, None)
                        self._recon_orphan_first_seen_at.pop(sym_str, None)
                        self._recon_orphan_last_log_at.pop(sym_str, None)
                        continue

                    # Never orphan-liquidate while any open order exists for the symbol.
                    # This avoids SOD churn when live OCO protection exists but internal
                    # tracking temporarily desynced.
                    if mode_norm != "intraday" and has_any_open_order:
                        if self._should_log_backtest_category(
                            "LOG_SPREAD_RECONCILE_BACKTEST_ENABLED", False
                        ):
                            self.Log(
                                f"RECON_ORPHAN_SKIP_OPEN_ORDERS: {sym_str} | "
                                f"Mode={mode_norm.upper()} | Qty={holding.Quantity}"
                            )
                        continue

                    if mode_norm == "intraday":
                        recent_tag = self._get_recent_symbol_fill_tag(sym_str, max_age_minutes=90)
                        if recent_tag:
                            if self._should_log_backtest_category(
                                "LOG_SPREAD_RECONCILE_BACKTEST_ENABLED", False
                            ):
                                self.Log(
                                    f"RECON_ORPHAN_SKIP_RECENT_FILL: {sym_str} | Tag={recent_tag}"
                                )
                            continue
                        first_seen = self._recon_orphan_first_seen_at.get(sym_str)
                        if first_seen is None:
                            first_seen = self.Time
                            self._recon_orphan_first_seen_at[sym_str] = first_seen
                        streak = int(self._recon_orphan_seen_streak.get(sym_str, 0)) + 1
                        self._recon_orphan_seen_streak[sym_str] = streak
                        age_min = (self.Time - first_seen).total_seconds() / 60.0

                        # Guard intraday orphan liquidation to avoid transient desync churn.
                        if (
                            streak < intraday_min_streak
                            or age_min < intraday_min_age_min
                            or has_any_open_order
                        ):
                            last_log_at = self._recon_orphan_last_log_at.get(sym_str)
                            should_log = (
                                last_log_at is None
                                or (self.Time - last_log_at).total_seconds() / 60.0
                                >= intraday_guard_log_min
                            )
                            if should_log and self._should_log_backtest_category(
                                "LOG_SPREAD_RECONCILE_BACKTEST_ENABLED", False
                            ):
                                self.Log(
                                    f"RECON_ORPHAN_GUARD_HOLD: {sym_str} | "
                                    f"Mode=INTRADAY | Streak={streak}/{intraday_min_streak} | "
                                    f"AgeMin={age_min:.1f}/{intraday_min_age_min:.1f} | "
                                    f"OpenOrders={1 if has_any_open_order else 0}"
                                )
                                self._recon_orphan_last_log_at[sym_str] = self.Time
                            continue

                    close_qty = -int(holding.Quantity)
                    ticket = self._submit_option_close_market_order(
                        symbol=broker_symbol,
                        quantity=close_qty,
                        reason="RECON_ORPHAN_OPTION",
                    )
                    if ticket is None:
                        self.Log(
                            f"RECON_ORPHAN_CLOSE_SKIPPED: {sym_str} | Qty={holding.Quantity} | "
                            "Reason=NoCloseQuantity"
                        )
                        continue
                    self._recon_orphan_close_submitted[sym_str] = today
                    self._clear_micro_symbol_tracking(sym_str)
                    self._recon_orphan_seen_streak.pop(sym_str, None)
                    self._recon_orphan_first_seen_at.pop(sym_str, None)
                    self._recon_orphan_last_log_at.pop(sym_str, None)
                    self.Log(
                        f"RECON_ORPHAN_CLOSE_SUBMITTED: {sym_str} | "
                        f"Qty={option_holdings.get(sym_str, 0)} | Mode={mode_norm.upper()}"
                    )
                except Exception as e:
                    self.Log(f"RECON_ORPHAN_CLOSE_FAILED: {sym_str} | {e}")

            qqq_holding = self.Portfolio[self.qqq]
            # Assignment containment: QQQ equity should never persist in options-only flow.
            # Liquidate whenever QQQ shares are present after reconciliation.
            if qqq_holding.Invested:
                self.Log(
                    f"RECON_ASSIGNMENT_EQUITY_LIQUIDATED: QQQ Qty={qqq_holding.Quantity} | "
                    f"Value=${qqq_holding.HoldingsValue:,.2f}"
                )
                self.Liquidate(self.qqq, tag="ASSIGNMENT_RECONCILE")
        except Exception as e:
            self.Log(f"RECON_ERROR: {e}")

    # =========================================================================
    # SIGNAL PROCESSING HELPERS
    # =========================================================================

    def _process_immediate_signals(self) -> None:
        """
        Process pending signals with IMMEDIATE urgency.

        Routes to PortfolioRouter which validates and executes via MarketOrder.
        """
        # V2.18: Block immediate orders in market close blackout window (RPT-5 fix)
        if self._is_market_close_blackout():
            pending_count = self.portfolio_router.get_pending_count()
            if pending_count > 0:
                self.Log(
                    f"MARKET_CLOSE_GUARD: {pending_count} immediate orders blocked | "
                    f"Time={self.Time.strftime('%H:%M')} in 15:58-16:00 blackout"
                )
                # Clear pending to prevent them executing at wrong time
                self.portfolio_router.clear_pending()
            return

        # Skip if no pending signals
        if self.portfolio_router.get_pending_count() == 0:
            return

        # Get current state
        capital_state = self.capital_engine.calculate(self.Portfolio.TotalPortfolioValue)
        current_positions = self._get_current_positions()
        current_prices = self._get_current_prices()

        # V2.19 FIX: Inject option prices from pending signal metadata
        # _get_current_prices() only includes HELD options (V2.19 perf fix),
        # but NEW entries aren't held yet. Use price from chain data stored in metadata.
        for signal in self.portfolio_router.get_pending_signals():
            if signal.source in ("OPT", "OPT_INTRADAY") and signal.symbol not in current_prices:
                price = signal.metadata.get("contract_price", 0) if signal.metadata else 0

                # V6.12: Fallback to bid/ask mid if metadata price is 0
                if price <= 0 and self.Securities.ContainsKey(signal.symbol):
                    try:
                        sec = self.Securities[signal.symbol]
                        bid = sec.BidPrice or 0
                        ask = sec.AskPrice or 0
                        if bid > 0 and ask > 0:
                            price = (bid + ask) / 2
                            self.Log(
                                f"V2.19_INJECT_FALLBACK: {signal.symbol} | "
                                f"Using bid/ask mid=${price:.2f} (bid={bid:.2f}, ask={ask:.2f})"
                            )
                    except Exception:
                        pass  # Keep price as 0, will be logged below

                if price > 0:
                    current_prices[signal.symbol] = price
                else:
                    self.Log(
                        f"V2.19_INJECT_WARNING: {signal.symbol} | price=0 | "
                        f"No valid price found - sizing may be incorrect"
                    )

        try:
            # Calculate max single position in dollars from percentage
            max_single_position = capital_state.tradeable_eq * config.MAX_SINGLE_POSITION_PCT
            self.portfolio_router.process_immediate(
                tradeable_equity=capital_state.tradeable_eq,
                current_positions=current_positions,
                current_prices=current_prices,
                max_single_position=max_single_position,
                available_cash=self.Portfolio.Cash,
                locked_amount=capital_state.locked_amount,
                current_time=str(self.Time),
            )
            self._capture_router_rejections(stage="IMMEDIATE")
        except Exception as e:
            self.Log(f"SIGNAL_ERROR: Failed to process immediate signals - {e}")
            self._capture_router_rejections(stage="IMMEDIATE_ERROR")

    def _process_eod_signals(self, capital_state: CapitalState) -> None:
        """
        Process pending signals with EOD urgency using SetHoldings.

        Uses QC's SetHoldings which automatically handles sell-before-buy
        ordering to avoid "Insufficient buying power" errors.

        Args:
            capital_state: Current capital state for sizing.
        """
        # Skip if no pending signals
        if self.portfolio_router.get_pending_count() == 0:
            return

        try:
            # Get aggregated weights from router
            weights = self.portfolio_router.drain_pending_signals()

            if not weights:
                return

            # Aggregate weights by symbol (take highest weight for same symbol)
            aggregated = self.portfolio_router.aggregate_weights(weights)

            # V2.26: Apply Drawdown Governor scaling to allocations
            # V2.32/V6.11: EXEMPT hedges (SH) — we want MORE hedging during drawdowns, not less
            # V2.32: Apply sizing floor for options, exempt bearish options if configured
            HEDGE_SYMBOLS = {"SH"}

            # V3.0 FIX: Improved bearish detection - check existing position OR signal source
            # This allows NEW bearish entries at Governor 0%, not just existing positions
            def is_bearish_signal(agg) -> bool:
                """Check if signal is bearish (PUT spread entry or exit)."""
                # Check existing spread position
                if hasattr(self, "options_engine") and self.options_engine.has_spread_position():
                    for spread in self.options_engine.get_spread_positions():
                        if spread.spread_type == "BEAR_PUT":
                            return True
                # Check signal source/reasons for bearish indicators
                for source in agg.sources:
                    if "BEAR" in source.upper() or "PUT" in source.upper():
                        return True
                for reason in agg.reasons:
                    if "BEAR" in reason.upper() or "PUT" in reason.upper():
                        return True
                # Check symbol for PUT option (option symbols contain P for put)
                symbol_str = str(agg.symbol)
                if len(symbol_str) > 10 and "QQQ" in symbol_str:
                    # QQQ option symbol format: QQQ YYMMDDP00123000 (P = put)
                    # Extract option type from symbol
                    if "P0" in symbol_str:  # PUT option
                        return True
                return False

            if self._governor_scale < 1.0:
                scaled_count = 0
                options_exempt_count = 0
                for symbol, agg in aggregated.items():
                    if agg.target_weight > 0:  # Only scale entries/holds, not exits (weight=0)
                        # V2.32: Hedges exempt
                        if symbol in HEDGE_SYMBOLS:
                            continue

                        # V2.32: Check if this is an options position (QQQ options)
                        is_option = len(str(symbol)) > 5 and "QQQ" in str(symbol)

                        if is_option:
                            # V3.0: Exempt bearish options entirely if configured
                            # Uses improved detection that checks signal source, not just existing position
                            if is_bearish_signal(agg) and config.GOVERNOR_EXEMPT_BEARISH_OPTIONS:
                                options_exempt_count += 1
                                continue

                            # V2.32: Apply sizing floor for options
                            effective_scale = max(
                                self._governor_scale, config.GOVERNOR_OPTIONS_SIZING_FLOOR
                            )
                            agg.target_weight *= effective_scale
                        else:
                            # Non-option positions get full governor scaling
                            agg.target_weight *= self._governor_scale

                        scaled_count += 1

                if self._governor_scale == 0.0:
                    self.Log("GOVERNOR: SHUTDOWN | All non-hedge allocations zeroed")
                else:
                    exempt_msg = (
                        f", {options_exempt_count} bear options exempt"
                        if options_exempt_count
                        else ""
                    )
                    self.Log(
                        f"GOVERNOR: Scaling {scaled_count} positions by {self._governor_scale:.0%} "
                        f"(hedges exempt{exempt_msg})"
                    )

            # Validate against max position size
            max_single_position_pct = config.MAX_SINGLE_POSITION_PCT
            for symbol, agg in aggregated.items():
                if agg.target_weight > max_single_position_pct:
                    agg.target_weight = max_single_position_pct

            # Build list of portfolio targets for SetHoldings
            targets = []
            for symbol, agg in aggregated.items():
                # Get the actual Symbol object
                symbol_obj = None
                for s in self.traded_symbols:
                    if str(s.Value) == symbol:
                        symbol_obj = s
                        break

                if symbol_obj and agg.target_weight >= 0:
                    targets.append(PortfolioTarget(symbol_obj, agg.target_weight))

            if targets:
                # Log what we're doing (limited to avoid log overflow)
                if len(targets) <= 5:
                    self.Log(f"EOD_REBALANCE: {len(targets)} targets")

                # SetHoldings handles sell-before-buy automatically
                self.SetHoldings(targets)
            self._capture_router_rejections(stage="EOD")

        except Exception as e:
            self.Log(f"SIGNAL_ERROR: Failed to process EOD signals - {e}")
            self._capture_router_rejections(stage="EOD_ERROR")

    def _generate_trend_signals_eod(self, regime_state: RegimeState) -> None:
        """
        Generate Trend Engine signals at end of day.

        V2.3 Enhancement: Position limits to reserve capital for options.
        - Max 2 concurrent trend positions (config.MAX_CONCURRENT_TREND_POSITIONS)
        - Priority order: config.TREND_PRIORITY_ORDER
        - Always processes exit signals regardless of position count

        Checks for:
            - Entry signals (MA200 + ADX confirmation)
            - Exit signals (Chandelier stop, regime deterioration)

        Args:
            regime_state: Current regime state.
        """
        # V6.4: Skip trend engine in isolation mode if disabled
        if config.ISOLATION_TEST_MODE and not config.ISOLATION_TREND_ENABLED:
            return

        is_cold_start = (
            self.cold_start_engine.is_active()
            if hasattr(self.cold_start_engine, "is_active")
            else False
        )
        has_warm_entry = (
            self.cold_start_engine.has_warm_entry()
            if hasattr(self.cold_start_engine, "has_warm_entry")
            else False
        )
        current_date = str(self.Time.date())

        # V2.18: Count current trend positions AND pending MOO orders for position limit enforcement
        # Fix: Previously only counted invested positions, missing pending MOOs that would fill next day
        trend_symbols = config.TREND_PRIORITY_ORDER  # V6.11: ["QLD", "UGL", "UCO", "SSO"]
        current_trend_positions = sum(
            1 for sym in trend_symbols if self.Portfolio[getattr(self, sym.lower())].Invested
        )
        # V2.18: Also count pending MOO orders to prevent exceeding limit
        pending_moo_count = self.trend_engine.get_pending_moo_count()
        total_committed_positions = current_trend_positions + pending_moo_count
        max_positions = config.MAX_CONCURRENT_TREND_POSITIONS  # Default: 2
        entries_allowed = max_positions - total_committed_positions

        # Log position status (V2.18: Include pending MOO count)
        if entries_allowed < len(trend_symbols) or pending_moo_count > 0:
            self.Log(
                f"TREND: Position limit check | Invested={current_trend_positions} | "
                f"Pending MOO={pending_moo_count} | Total={total_committed_positions} | "
                f"Max={max_positions} | Entries allowed={entries_allowed}"
            )

        # V2.27: Skip-day enforcement after Tier 2+ kill switch (block entries only, exits still run)
        skip_entries = self.risk_engine.is_ks_skip_day(str(self.Time.date()))
        if skip_entries:
            self.Log("TREND: Entry blocked - KS skip day active")

        # V2.3: Collect entry candidates with their ADX scores for prioritization
        entry_candidates = []

        # Build symbol data map for cleaner iteration.
        # V2.4: Added SMA50 for structural trend exit.
        symbol_data = {
            "QLD": (self.qld, self.qld_ma200, self.qld_adx, self.qld_atr, self.qld_sma50),
            "SSO": (self.sso, self.sso_ma200, self.sso_adx, self.sso_atr, self.sso_sma50),
            "UGL": (self.ugl, self.ugl_ma200, self.ugl_adx, self.ugl_atr, self.ugl_sma50),
            "UCO": (self.uco, self.uco_ma200, self.uco_adx, self.uco_atr, self.uco_sma50),
        }

        # Process each symbol in priority order
        for symbol in trend_symbols:
            security, ma200_ind, adx_ind, atr_ind, sma50_ind = symbol_data[symbol]

            # Skip if indicators not ready (SMA50 needs 50 days warmup)
            if not (ma200_ind.IsReady and adx_ind.IsReady and atr_ind.IsReady):
                continue

            close = self.Securities[security].Close
            high = self.Securities[security].High
            ma200 = ma200_ind.Current.Value
            adx = adx_ind.Current.Value
            atr = atr_ind.Current.Value
            # V2.4: SMA50 for structural trend exit (may not be ready during warmup)
            sma50 = sma50_ind.Current.Value if sma50_ind.IsReady else None

            # ALWAYS check exit signals for invested positions
            if self.Portfolio[security].Invested:
                signal = self.trend_engine.check_exit_signals(
                    symbol=symbol,
                    close=close,
                    high=high,
                    ma200=ma200,
                    adx=adx,
                    regime_score=regime_state.smoothed_score,
                    atr=atr,
                    sma50=sma50,  # V2.4: Pass SMA50 for structural trend exit
                )
                if signal:
                    self.portfolio_router.receive_signal(signal)
            else:
                # Collect entry candidates (will filter by position limit later)
                signal = self.trend_engine.check_entry_signal(
                    symbol=symbol,
                    close=close,
                    ma200=ma200,
                    adx=adx,
                    regime_score=regime_state.smoothed_score,
                    is_cold_start_active=is_cold_start,
                    has_warm_entry=has_warm_entry,
                    atr=atr,
                    current_date=current_date,
                )
                if signal:
                    # Store with ADX for sorting (higher ADX = stronger trend)
                    entry_candidates.append((signal, adx))

        # V2.20: Respect rejection cooldown for trend entries
        if (
            self._trend_rejection_cooldown_until is not None
            and self.Time < self._trend_rejection_cooldown_until
        ):
            if entry_candidates:
                self.Log(
                    f"TREND: Entries blocked by rejection cooldown | "
                    f"Until {self._trend_rejection_cooldown_until}"
                )
            entry_candidates = []

        # V2.27: Block entries on KS skip day (exits still processed above)
        if skip_entries:
            entry_candidates = []

        # V2.3: Apply position limit - only send top N entry signals
        if entry_candidates and entries_allowed > 0:
            # Sort by ADX descending (strongest trends first)
            entry_candidates.sort(key=lambda x: x[1], reverse=True)

            # Send only as many entries as allowed
            for signal, adx in entry_candidates[:entries_allowed]:
                # V2.27: Apply KS Tier 1 sizing reduction to trend entries
                if (
                    self._last_risk_result is not None
                    and self._last_risk_result.sizing_multiplier < 1.0
                ):
                    original_weight = signal.target_weight
                    signal.target_weight *= self._last_risk_result.sizing_multiplier
                    self.Log(
                        f"TREND: Tier1 sizing | {signal.symbol} weight "
                        f"{original_weight:.2f} → {signal.target_weight:.2f}"
                    )
                self.Log(
                    f"TREND: ENTRY_APPROVED {signal.symbol} | ADX={adx:.1f} | "
                    f"Slot {current_trend_positions + 1}/{max_positions}"
                )
                self.portfolio_router.receive_signal(signal)
                # V2.19: Mark pending ONLY after approval (not in check_entry_signal)
                self.trend_engine.mark_pending_moo(signal.symbol, current_date)
                current_trend_positions += 1

            # Log blocked entries
            blocked = entry_candidates[entries_allowed:]
            for signal, adx in blocked:
                self.Log(
                    f"TREND: ENTRY_BLOCKED {signal.symbol} | ADX={adx:.1f} | "
                    f"Reason: Position limit ({max_positions}) reached"
                )

    def _generate_options_signals(
        self,
        regime_state: RegimeState,
        capital_state: CapitalState,
        size_multiplier: float = 1.0,
        is_eod_scan: bool = False,
    ) -> None:
        """
        Generate Options Engine signals at end of day.

        V2.3: Debit spread entry using regime-based direction.
        - Regime > 60: Bull Call Spread
        - Regime < 45: Bear Put Spread
        - Regime 45-60: No trade (neutral)

        V2.3.20: Added size_multiplier for cold start reduced sizing.

        Args:
            regime_state: Current regime state.
            capital_state: Current capital state.
            size_multiplier: Position size multiplier (default 1.0). Set to 0.5
                during cold start to reduce risk while still participating.
        """
        size_multiplier *= self._premarket_vix_size_mult

        # Skip if indicators not ready
        if not self.qqq_adx.IsReady or not self.qqq_sma200.IsReady:
            return

        # V2.27: Tier 1 blocks new options entries
        if self._last_risk_result is not None and not self._last_risk_result.can_enter_options:
            self.Log("OPTIONS_EOD: Blocked by KS Tier 1 (REDUCE)")
            return

        # V2.27: Skip-day enforcement after Tier 2+ kill switch
        if self.risk_engine.is_ks_skip_day(str(self.Time.date())):
            self.Log("OPTIONS_EOD: Blocked - KS skip day active")
            return

        # V2.33: Direction-aware governor gating for EOD options
        # This was missing in V2.32 causing the enter→liquidate death spiral
        #
        # Investment thesis alignment:
        # - Bear PUT spreads REDUCE portfolio risk → allowed even at low governor
        # - Bull CALL spreads INCREASE risk → require higher governor
        #
        # The V2.32 death spiral happened because:
        # - Regime was 63-71 (bullish) but portfolio drawdown was 10-16%
        # - System entered BULL_CALL spreads (wrong for drawdown protection)
        # - Governor liquidated next morning → forced loss → repeat
        #
        # V3.5 fix: Allow PUT spreads in NEUTRAL zone at low governor
        # "Bearish" for governor purposes = anything not BULL (regime <= 70)
        # PUT spreads reduce risk → allowed at low governor in NEUTRAL/CAUTIOUS/BEAR
        regime_score_for_governor = self._get_effective_regime_score_for_options()
        is_put_direction = regime_score_for_governor <= config.SPREAD_REGIME_BULLISH  # <= 70

        if self._governor_scale == 0.0:
            # Governor SHUTDOWN (16%+ drawdown)
            # Only PUT spreads allowed - they hedge/profit from continued decline
            if not is_put_direction:
                self.Log(
                    f"OPTIONS_EOD: Blocked by Governor SHUTDOWN | "
                    f"Scale=0% | Regime={regime_score_for_governor:.0f} (BULL) | "
                    f"Only PUT spreads allowed at 0%"
                )
                return
            else:
                self.Log(
                    f"OPTIONS_EOD: PUT spread allowed at Governor 0% | "
                    f"Regime={regime_score_for_governor:.0f} | Thesis: PUT spreads active in non-BULL"
                )
                # Continue to spread entry logic below

        # V5.2 BINARY: No intermediate states - only 100% or 0%
        # If scale is not 100% and not 0%, log warning (shouldn't happen)
        elif self._governor_scale < 1.0:
            if not is_put_direction:
                self.Log(
                    f"OPTIONS_EOD: Blocked by Governor | "
                    f"Scale={self._governor_scale:.0%} < 100% for CALL | "
                    f"Regime={regime_score_for_governor:.0f}"
                )
                return

        # Skip if swing positions already maxed out
        can_swing, _ = self.options_engine.can_enter_swing()
        if not can_swing:
            return

        # CRITICAL FIX: Validate options symbol is resolved before use
        # Symbol may not be fully resolved on first trading day or after gaps
        if not self._validate_options_symbol():
            return

        # Get options chain from CurrentSlice (scheduled function, no Slice param)
        if self.CurrentSlice is None:
            return
        chain = self._get_valid_options_chain(self.CurrentSlice.OptionChains, mode_label="SWING")
        if chain is None:
            return

        # Get current values
        qqq_price, adx_value, ma200_value, ma50_value = self._get_options_market_snapshot()
        transition_ctx = self._get_transition_execution_context()
        regime_score = float(
            transition_ctx.get("transition_score", self._get_decision_regime_score_for_options())
            or self._get_decision_regime_score_for_options()
        )

        # V2.1: Calculate IV rank from options chain
        iv_rank = self._calculate_iv_rank(chain)

        # V5.3: Check position limits before scanning
        can_swing, swing_reason = self.options_engine.can_enter_swing()
        if not can_swing:
            # Debug log - skip in backtest to avoid log limits
            return

        context = self._resolve_vass_direction_context(
            regime_score=regime_score,
            size_multiplier=size_multiplier,
            bull_profile_log_prefix="VASS_BULL_PROFILE_BLOCK",
            clamp_log_prefix="VASS_CLAMP_BLOCK",
            shock_log_prefix="VASS_SHOCK_OVERRIDE_EOD",
            transition_ctx=transition_ctx,
        )
        if context is None:
            return
        (
            direction,
            direction_str,
            _overlay_state,
            size_multiplier,
            vass_has_conviction,
            vass_reason,
            macro_direction,
            resolve_reason,
            resolved_direction,
        ) = context

        # V5.3: Use resolved direction (may be VASS override or Macro alignment)
        if direction == OptionDirection.CALL and self._is_premarket_ladder_call_block_active():
            self.Log(
                f"OPTIONS_EOD: CALL blocked by pre-market ladder | {self._premarket_vix_ladder_reason}"
            )
            return
        directions_to_scan = [(direction, direction_str)]

        if vass_has_conviction:
            self.Log(
                f"OPTIONS_VASS_CONVICTION: {vass_reason} | Macro={macro_direction} | "
                f"Resolved={resolved_direction} | {resolve_reason}"
            )

        # Scan each direction
        for direction, direction_str in directions_to_scan:
            self._scan_spread_for_direction(
                chain,
                direction,
                direction_str,
                regime_score,
                qqq_price,
                adx_value,
                ma200_value,
                ma50_value,
                iv_rank,
                size_multiplier,
                is_eod_scan,
            )

    def _should_log_vass_rejection(self, reason_key: str) -> bool:
        """Per-reason throttle for VASS skip/rejection logs to preserve RCA fidelity."""
        interval_min = int(getattr(config, "VASS_LOG_REJECTION_INTERVAL_MINUTES", 15))
        now = self.Time
        last = self._last_vass_rejection_log_by_key.get(reason_key)
        if last is not None:
            elapsed = (now - last).total_seconds() / 60.0
            if elapsed < interval_min:
                return False
        self._last_vass_rejection_log_by_key[reason_key] = now
        return True

    def _build_vass_spread_signal(
        self,
        *,
        chain,
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
        """Build VASS spread entry signal from pre-filtered candidates."""
        rejection_code = "UNKNOWN"
        signal: Optional[TargetWeight] = None
        dte_min_all = min(r[0] for r in dte_ranges)
        dte_max_all = max(r[1] for r in dte_ranges)
        now_str = str(self.Time)
        max_attempts = max(1, int(getattr(config, "VASS_ROUTE_MAX_CANDIDATE_ATTEMPTS", 3)))
        allow_opposite_fallback = bool(
            getattr(config, "VASS_OPPOSITE_ROUTE_FALLBACK_ENABLED", True)
        )

        def _is_quality_failure(reason: Optional[str]) -> bool:
            return bool(reason and str(reason).startswith("R_CONTRACT_QUALITY:"))

        def _opposite_strategy(primary: SpreadStrategy) -> Optional[SpreadStrategy]:
            mapping = {
                SpreadStrategy.BULL_CALL_DEBIT: SpreadStrategy.BULL_PUT_CREDIT,
                SpreadStrategy.BEAR_PUT_DEBIT: SpreadStrategy.BEAR_CALL_CREDIT,
                SpreadStrategy.BULL_PUT_CREDIT: SpreadStrategy.BULL_CALL_DEBIT,
                SpreadStrategy.BEAR_CALL_CREDIT: SpreadStrategy.BEAR_PUT_DEBIT,
            }
            return mapping.get(primary)

        def _attempt_debit_route(
            route_contracts: List[OptionContract],
            route_strategy: SpreadStrategy,
        ) -> Tuple[Optional[TargetWeight], str, Optional[str]]:
            pool = list(route_contracts)
            last_validation_reason: Optional[str] = None
            route_rejection = "DEBIT_LEG_SELECTION_FAILED"

            if route_strategy == SpreadStrategy.BULL_CALL_DEBIT:
                qqq_open = float(getattr(self, "_qqq_at_open", 0.0) or 0.0)
                if qqq_open <= 0:
                    try:
                        qqq_open = float(self.Securities[self.qqq].Open)
                    except Exception:
                        qqq_open = 0.0

                qqq_sma20 = getattr(self, "qqq_sma20", None)
                qqq_sma20_ready = bool(
                    qqq_sma20 is not None and getattr(qqq_sma20, "IsReady", False)
                )
                qqq_sma20_value = (
                    float(qqq_sma20.Current.Value)
                    if qqq_sma20_ready and getattr(qqq_sma20, "Current", None) is not None
                    else None
                )

                (
                    trend_ok,
                    trend_code,
                    trend_detail,
                ) = self.options_engine.check_vass_bull_debit_trend_confirmation(
                    vix_current=self._current_vix,
                    current_price=qqq_price,
                    qqq_open=qqq_open if qqq_open > 0 else None,
                    qqq_sma20=qqq_sma20_value,
                    qqq_sma20_ready=qqq_sma20_ready,
                )
                if not trend_ok:
                    self.options_engine.set_last_entry_validation_failure(trend_code)
                    self._log_high_frequency_event(
                        config_flag="LOG_VASS_FALLBACK_BACKTEST_ENABLED",
                        category="VASS_FALLBACK",
                        reason_key=f"TREND_CONFIRM_BLOCK|{trend_code}",
                        message=(
                            f"{fallback_log_prefix}: BULL_CALL trend confirmation blocked | "
                            f"{trend_code} | {trend_detail}"
                        ),
                    )
                    return None, "DEBIT_TREND_CONFIRM_BLOCK", trend_code

            for attempt in range(max_attempts):
                if len(pool) < 2:
                    break
                spread_legs = self.options_engine.select_spread_legs_with_fallback(
                    contracts=pool,
                    direction=direction,
                    current_time=now_str,
                    dte_ranges=dte_ranges,
                    set_cooldown=(attempt == max_attempts - 1),
                )
                if spread_legs is None:
                    break

                long_leg, short_leg = spread_legs
                route_rejection = "DEBIT_ENTRY_VALIDATION_FAILED"

                if not (long_leg.ask > 0 and short_leg.bid > 0 and short_leg.ask > 0):
                    route_rejection = "DEBIT_ENTRY_QUOTES_INVALID"
                    drop_syms = {str(long_leg.symbol), str(short_leg.symbol)}
                    pool = [c for c in pool if str(c.symbol) not in drop_syms]
                    continue

                route_signal = self.options_engine.check_spread_entry_signal(
                    regime_score=regime_score,
                    vix_current=self._current_vix,
                    adx_value=adx_value,
                    current_price=qqq_price,
                    ma200_value=ma200_value,
                    ma50_value=ma50_value,
                    iv_rank=iv_rank,
                    current_hour=self.Time.hour,
                    current_minute=self.Time.minute,
                    current_date=str(self.Time.date()),
                    portfolio_value=portfolio_value,
                    long_leg_contract=long_leg,
                    short_leg_contract=short_leg,
                    gap_filter_triggered=self.risk_engine.is_gap_filter_active(),
                    vol_shock_active=self.risk_engine.is_vol_shock_active(self.Time),
                    size_multiplier=size_multiplier,
                    margin_remaining=margin_remaining,
                    dte_min=vass_dte_min,
                    dte_max=vass_dte_max,
                    is_eod_scan=is_eod_scan,
                    direction=direction,
                )
                if route_signal is not None:
                    return route_signal, route_rejection, None

                last_validation_reason = self.options_engine.pop_last_entry_validation_failure()
                retryable_quality = (
                    _is_quality_failure(last_validation_reason) and attempt < max_attempts - 1
                )
                if retryable_quality:
                    self._log_high_frequency_event(
                        config_flag="LOG_VASS_FALLBACK_BACKTEST_ENABLED",
                        category="VASS_FALLBACK",
                        reason_key="DEBIT_QUALITY_RETRY",
                        message=(
                            f"{fallback_log_prefix}: DEBIT quality reject ({last_validation_reason}) | "
                            f"Trying alternate candidate {attempt + 2}/{max_attempts}"
                        ),
                    )
                    # Drop only the short leg to keep long-leg directional intent and
                    # search for a better-priced width pairing.
                    pool = [c for c in pool if str(c.symbol) != str(short_leg.symbol)]
                    continue
                break

            return None, route_rejection, last_validation_reason

        def _attempt_credit_route(
            route_contracts: List[OptionContract],
            route_strategy: SpreadStrategy,
        ) -> Tuple[Optional[TargetWeight], str, Optional[str]]:
            pool = list(route_contracts)
            last_validation_reason: Optional[str] = None
            route_rejection = "CREDIT_LEG_SELECTION_FAILED"

            for attempt in range(max_attempts):
                if len(pool) < 2:
                    break
                spread_legs = self.options_engine.select_credit_spread_legs_with_fallback(
                    contracts=pool,
                    strategy=route_strategy,
                    dte_ranges=dte_ranges,
                    current_time=now_str,
                    set_cooldown=(attempt == max_attempts - 1),
                )
                if spread_legs is None:
                    break

                short_leg, long_leg = spread_legs
                route_rejection = "CREDIT_ENTRY_VALIDATION_FAILED"

                if not (short_leg.bid > 0 and short_leg.ask > 0 and long_leg.ask > 0):
                    route_rejection = "CREDIT_ENTRY_QUOTES_INVALID"
                    drop_syms = {str(short_leg.symbol), str(long_leg.symbol)}
                    pool = [c for c in pool if str(c.symbol) not in drop_syms]
                    continue

                route_signal = self.options_engine.check_credit_spread_entry_signal(
                    regime_score=regime_score,
                    vix_current=self._current_vix,
                    adx_value=adx_value,
                    current_price=qqq_price,
                    ma200_value=ma200_value,
                    iv_rank=iv_rank,
                    current_hour=self.Time.hour,
                    current_minute=self.Time.minute,
                    current_date=str(self.Time.date()),
                    portfolio_value=portfolio_value,
                    short_leg_contract=short_leg,
                    long_leg_contract=long_leg,
                    strategy=route_strategy,
                    gap_filter_triggered=self.risk_engine.is_gap_filter_active(),
                    vol_shock_active=self.risk_engine.is_vol_shock_active(self.Time),
                    size_multiplier=size_multiplier,
                    margin_remaining=margin_remaining,
                    is_eod_scan=is_eod_scan,
                    direction=direction,
                )
                if route_signal is not None:
                    return route_signal, route_rejection, None

                last_validation_reason = self.options_engine.pop_last_entry_validation_failure()
                retryable_quality = (
                    _is_quality_failure(last_validation_reason) and attempt < max_attempts - 1
                )
                if retryable_quality:
                    self._log_high_frequency_event(
                        config_flag="LOG_VASS_FALLBACK_BACKTEST_ENABLED",
                        category="VASS_FALLBACK",
                        reason_key="CREDIT_QUALITY_RETRY",
                        message=(
                            f"{fallback_log_prefix}: CREDIT quality reject ({last_validation_reason}) | "
                            f"Trying alternate candidate {attempt + 2}/{max_attempts}"
                        ),
                    )
                    # Credit quality is mostly driven by short leg economics.
                    pool = [c for c in pool if str(c.symbol) != str(short_leg.symbol)]
                    continue
                break

            return None, route_rejection, last_validation_reason

        # Primary route attempt.
        last_validation_reason: Optional[str] = None
        if is_credit:
            signal, rejection_code, last_validation_reason = _attempt_credit_route(
                candidate_contracts, strategy
            )
        else:
            signal, rejection_code, last_validation_reason = _attempt_debit_route(
                candidate_contracts,
                strategy,
            )

        if signal is not None:
            return signal, rejection_code

        # One opposite-route fallback attempt (quality-first, bounded).
        if allow_opposite_fallback:
            fallback_strategy = _opposite_strategy(strategy)
            if fallback_strategy is not None:
                if (
                    strategy == SpreadStrategy.BEAR_PUT_DEBIT
                    and fallback_strategy == SpreadStrategy.BEAR_CALL_CREDIT
                ):
                    if not bool(
                        getattr(config, "VASS_BEARISH_FALLBACK_TO_BEAR_CALL_CREDIT", False)
                    ):
                        rejection_code = "R_BEAR_FALLBACK_DISABLED"
                        self._log_high_frequency_event(
                            config_flag="LOG_VASS_FALLBACK_BACKTEST_ENABLED",
                            category="VASS_FALLBACK",
                            reason_key="BEAR_FALLBACK_DISABLED",
                            message=(
                                f"{fallback_log_prefix}: Skip opposite {fallback_strategy.value} | "
                                f"Policy disabled"
                            ),
                        )
                        fallback_strategy = None
                    else:
                        max_regime = float(getattr(config, "VASS_BEAR_FALLBACK_MAX_REGIME", 40.0))
                        min_vix = float(getattr(config, "VASS_BEAR_FALLBACK_MIN_VIX", 0.0))
                        vix_now = float(getattr(self, "_current_vix", 0.0) or 0.0)
                        if regime_score > max_regime or (min_vix > 0 and vix_now < min_vix):
                            rejection_code = "R_BEAR_FALLBACK_POLICY"
                            self._log_high_frequency_event(
                                config_flag="LOG_VASS_FALLBACK_BACKTEST_ENABLED",
                                category="VASS_FALLBACK",
                                reason_key="BEAR_FALLBACK_POLICY",
                                message=(
                                    f"{fallback_log_prefix}: Skip opposite {fallback_strategy.value} | "
                                    f"Regime={regime_score:.1f}>{max_regime:.1f} or VIX={vix_now:.1f}<{min_vix:.1f}"
                                ),
                            )
                            fallback_strategy = None

            if fallback_strategy is not None:
                fallback_is_credit = self.options_engine.is_credit_strategy(fallback_strategy)
                fallback_right = self._strategy_option_right(fallback_strategy)
                fallback_contracts = self._build_spread_candidate_contracts(
                    chain,
                    direction,
                    dte_min=dte_min_all,
                    dte_max=dte_max_all,
                    option_right=fallback_right,
                )
                if len(fallback_contracts) >= 2:
                    self._log_high_frequency_event(
                        config_flag="LOG_VASS_FALLBACK_BACKTEST_ENABLED",
                        category="VASS_FALLBACK",
                        reason_key=f"TRY_OPPOSITE|{strategy.value}|{fallback_strategy.value}",
                        message=(
                            f"{fallback_log_prefix}: Primary {strategy.value} failed | "
                            f"Trying opposite {fallback_strategy.value}"
                        ),
                    )
                    if fallback_is_credit:
                        signal, rejection_code, last_validation_reason = _attempt_credit_route(
                            fallback_contracts, fallback_strategy
                        )
                    else:
                        signal, rejection_code, last_validation_reason = _attempt_debit_route(
                            fallback_contracts,
                            fallback_strategy,
                        )
                    if signal is not None:
                        return signal, rejection_code
                else:
                    rejection_code = "OPPOSITE_ROUTE_INSUFFICIENT_CANDIDATES"

        if last_validation_reason is not None:
            self.options_engine.set_last_entry_validation_failure(last_validation_reason)
        return None, rejection_code

    def _scan_spread_for_direction(
        self,
        chain,
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
        """Scan for spread entry in a specific direction."""
        overlay_state = self.options_engine.get_regime_overlay_state(
            vix_current=self._current_vix, regime_score=regime_score
        )
        # V2.23: VASS strategy selection — routes to credit or debit
        strategy, vass_dte_min, vass_dte_max, is_credit = self._route_vass_strategy(
            direction_str=direction_str,
            overlay_state=overlay_state,
        )
        dte_ranges = self._build_vass_dte_fallbacks(vass_dte_min, vass_dte_max)
        dte_min_all = min(r[0] for r in dte_ranges)
        dte_max_all = max(r[1] for r in dte_ranges)
        can_swing_vass, swing_reason_vass = self.options_engine.can_enter_swing(
            direction=direction, overlay_state=overlay_state
        )
        if not can_swing_vass:
            if "R_SLOT_DIRECTION_OVERLAY" in swing_reason_vass:
                self._diag_overlay_slot_block_count += 1
            self._record_vass_reject_reason("SWING_SLOT_BLOCK")
            throttle_key = (
                f"SWING_SLOT_BLOCK|{direction.value}|"
                f"{'CREDIT' if is_credit else 'DEBIT'}|{swing_reason_vass}"
            )
            if self._should_log_vass_rejection(throttle_key):
                self.Log(
                    f"VASS_SKIPPED: Direction={direction.value} | IV_Env=NA | "
                    f"VIX={self._current_vix:.1f} | Regime={regime_score:.0f} | "
                    f"Contracts_checked=0 | Strategy={'CREDIT' if is_credit else 'DEBIT'} | "
                    f"DTE_Ranges={dte_ranges} | ReasonCode=SWING_SLOT_BLOCK | "
                    f"Reason=Swing entry not allowed | ValidationFail={swing_reason_vass}"
                )
            return

        spy_open = float(getattr(self.options_engine, "_spy_at_open", 0.0) or 0.0)
        spy_gap_pct = float(getattr(self.options_engine, "_spy_gap_pct", 0.0) or 0.0)
        try:
            spy_current = float(self.Securities[self.spy].Price)
        except Exception:
            spy_current = spy_open
        spy_intraday_change_pct = (
            ((spy_current - spy_open) / spy_open) * 100.0 if spy_open > 0 else 0.0
        )
        vix_intraday_change_pct = (
            ((float(self._current_vix) - float(self._vix_at_open)) / float(self._vix_at_open))
            * 100.0
            if float(self._vix_at_open) > 0
            else 0.0
        )
        swing_filters_ok, swing_filter_reason = self.options_engine.check_swing_filters(
            direction=direction,
            spy_gap_pct=spy_gap_pct,
            spy_intraday_change_pct=spy_intraday_change_pct,
            vix_intraday_change_pct=vix_intraday_change_pct,
            current_hour=self.Time.hour,
            current_minute=self.Time.minute,
            is_eod_scan=is_eod_scan,
        )
        if not swing_filters_ok:
            self._diag_vass_block_count += 1
            self._record_vass_reject_reason("SWING_FILTER")
            throttle_key = (
                f"SWING_FILTER|{direction.value}|"
                f"{'CREDIT' if is_credit else 'DEBIT'}|{swing_filter_reason}"
            )
            if self._should_log_vass_rejection(throttle_key):
                self.Log(
                    f"VASS_SKIPPED: Direction={direction.value} | "
                    f"VIX={self._current_vix:.1f} | Regime={regime_score:.0f} | "
                    f"Strategy={'CREDIT' if is_credit else 'DEBIT'} | "
                    f"ReasonCode=SWING_FILTER | ValidationFail={swing_filter_reason}"
                )
            return

        required_right = self._strategy_option_right(strategy)

        # V2.23: Build candidate contracts with widest VASS DTE range (fallback uses subranges)
        candidate_contracts = self._build_spread_candidate_contracts(
            chain,
            direction,
            dte_min=dte_min_all,
            dte_max=dte_max_all,
            option_right=required_right,
        )
        self._diag_vass_signal_seq = int(getattr(self, "_diag_vass_signal_seq", 0)) + 1
        vass_signal_id = f"VASS-{self.Time.strftime('%Y%m%d-%H%M')}-{self._diag_vass_signal_seq}"
        if len(candidate_contracts) < 2:
            self._record_vass_reject_reason("INSUFFICIENT_CANDIDATES")
            throttle_key = (
                f"INSUFFICIENT_CANDIDATES|{direction.value}|"
                f"{'CREDIT' if is_credit else 'DEBIT'}|{dte_min_all}-{dte_max_all}"
            )
            if self._should_log_vass_rejection(throttle_key):
                self.Log(
                    f"VASS_REJECTION: Direction={direction.value} | "
                    f"IV_Env={self.options_engine.get_iv_environment()} | "
                    f"VIX={self._current_vix:.1f} | Regime={regime_score:.0f} | "
                    f"Contracts_checked={len(candidate_contracts)} | "
                    f"Strategy={'CREDIT' if is_credit else 'DEBIT'} | "
                    f"DTE_Ranges={dte_ranges} | ReasonCode=INSUFFICIENT_CANDIDATES"
                )
            self._record_signal_lifecycle_event(
                engine="VASS",
                event="DROPPED",
                signal_id=vass_signal_id,
                direction=direction.value if direction else "",
                strategy=strategy.value if strategy else "",
                code="INSUFFICIENT_CANDIDATES",
                gate_name="VASS_CANDIDATE_CONTRACTS",
                reason="No contracts met spread criteria",
                contract_symbol="",
            )
            return
        self._record_signal_lifecycle_event(
            engine="VASS",
            event="CANDIDATE",
            signal_id=vass_signal_id,
            direction=direction.value if direction else "",
            strategy=strategy.value if strategy else "",
            code="R_OK",
            gate_name="VASS_SIGNAL_CANDIDATE",
            reason=f"Contracts={len(candidate_contracts)}",
            contract_symbol="",
        )

        tradeable_eq = self.capital_engine.calculate(
            self.Portfolio.TotalPortfolioValue
        ).tradeable_eq
        margin_remaining = self.portfolio_router.get_effective_margin_remaining()
        if self.options_engine.has_pending_spread_entry():
            self.Log("VASS: Pending spread entry exists - skipping new spread signal")
            self._record_signal_lifecycle_event(
                engine="VASS",
                event="DROPPED",
                signal_id=vass_signal_id,
                direction=direction.value if direction else "",
                strategy=strategy.value if strategy else "",
                code="R_PENDING_SPREAD_ENTRY",
                gate_name="PENDING_SPREAD_ENTRY",
                reason="Pending spread entry exists",
                contract_symbol="",
            )
            return
        signal, rejection_code = self._build_vass_spread_signal(
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
            portfolio_value=tradeable_eq,
            margin_remaining=margin_remaining,
            strategy=strategy,
            vass_dte_min=vass_dte_min,
            vass_dte_max=vass_dte_max,
            dte_ranges=dte_ranges,
            is_credit=is_credit,
            is_eod_scan=is_eod_scan,
            fallback_log_prefix="VASS_FALLBACK",
        )

        if signal:
            self.Log(
                f"VASS_ENTRY: {signal.metadata.get('vass_strategy', 'UNKNOWN') if signal.metadata else 'UNKNOWN'} | "
                f"{signal.symbol} | {signal.reason}"
            )
            signal = self._attach_option_trace_metadata(signal, source="VASS")
            vass_trace_id = signal.metadata.get("trace_id", "") if signal.metadata else ""
            self._record_signal_lifecycle_event(
                engine="VASS",
                event="APPROVED",
                signal_id=vass_signal_id,
                trace_id=vass_trace_id,
                direction=direction.value if direction else "",
                strategy=strategy.value if strategy else "",
                code="R_OK",
                gate_name="VASS_ENTRY",
                reason=str(signal.reason or ""),
                contract_symbol=str(signal.symbol),
            )
            signal = self._apply_spread_margin_guard(signal, source_tag="VASS_SPREAD")
            if signal is None:
                self._record_signal_lifecycle_event(
                    engine="VASS",
                    event="DROPPED",
                    signal_id=vass_signal_id,
                    trace_id=vass_trace_id,
                    direction=direction.value if direction else "",
                    strategy=strategy.value if strategy else "",
                    code="R_MARGIN_PRECHECK",
                    gate_name="VASS_MARGIN_GUARD",
                    reason="Signal dropped by spread margin guard",
                    contract_symbol="",
                )
                return

            # V2.3.6: Track spread order pair for failure handling
            short_symbol = (
                signal.metadata.get("spread_short_leg_symbol", "") if signal.metadata else ""
            )
            long_symbol = str(signal.symbol) if signal.symbol else ""
            if short_symbol and long_symbol:
                self._pending_spread_orders[short_symbol] = long_symbol
                self._pending_spread_orders_reverse[long_symbol] = short_symbol  # V2.6 Bug #5
                self.Log(
                    f"SPREAD: Tracking order pair | Short={short_symbol[-15:]} <-> Long={long_symbol[-15:]}"
                )

            self.portfolio_router.receive_signal(signal)
        else:
            self._record_signal_lifecycle_event(
                engine="VASS",
                event="DROPPED",
                signal_id=vass_signal_id,
                direction=direction.value if direction else "",
                strategy=strategy.value if strategy else "",
                code=self._canonical_options_reason_code(rejection_code or "UNKNOWN"),
                gate_name=str(rejection_code or "UNKNOWN"),
                reason="No spread signal produced",
                contract_symbol="",
            )

    def _build_spread_candidate_contracts(
        self,
        chain,
        direction: OptionDirection,
        dte_min: int = None,
        dte_max: int = None,
        option_right: Optional[OptionRight] = None,
    ) -> List[OptionContract]:
        """
        V2.3: Build list of candidate OptionContract objects for spread selection.

        Filters chain for appropriate DTE range and direction, converting QC
        contracts to our OptionContract dataclass.

        Args:
            chain: QuantConnect options chain.
            direction: CALL or PUT direction.
            dte_min: Minimum DTE override (defaults to config.SPREAD_DTE_MIN).
            dte_max: Maximum DTE override (defaults to config.SPREAD_DTE_MAX).

        Returns:
            List of OptionContract objects for spread leg selection.
        """
        candidates = []
        qqq_price = self.Securities[self.qqq].Price

        for contract in chain:
            # Check option right (strategy-aware). Falls back to direction-based filter.
            if option_right is not None:
                if contract.Right != option_right:
                    continue
                opt_direction = (
                    OptionDirection.CALL
                    if option_right == OptionRight.Call
                    else OptionDirection.PUT
                )
            else:
                if direction == OptionDirection.CALL:
                    if contract.Right != OptionRight.Call:
                        continue
                else:
                    if contract.Right != OptionRight.Put:
                        continue
                opt_direction = direction

            # Check DTE range for spreads (V2.23: VASS-aware DTE override)
            dte = (contract.Expiry - self.Time).days
            effective_dte_min = dte_min if dte_min is not None else config.SPREAD_DTE_MIN
            effective_dte_max = dte_max if dte_max is not None else config.SPREAD_DTE_MAX
            if dte < effective_dte_min or dte > effective_dte_max:
                continue

            # Get bid/ask safely
            bid, ask = self._get_contract_prices(contract)
            # T-20: Keep zero-bid contracts if ask is valid. Long-leg candidates can be buyable
            # with bid=0 in thin chains.
            if ask <= 0:
                continue

            mid_price = (bid + ask) / 2 if bid > 0 else ask

            # Get Greeks if available
            delta = getattr(contract, "Greeks", None)
            delta_val = delta.Delta if delta else 0.0
            gamma_val = delta.Gamma if delta else 0.0
            theta_val = delta.Theta if delta else 0.0
            vega_val = delta.Vega if delta else 0.0

            # Build OptionContract
            opt_contract = OptionContract(
                symbol=str(contract.Symbol),
                underlying="QQQ",
                direction=opt_direction,
                strike=float(contract.Strike),
                expiry=str(contract.Expiry.date()),
                delta=delta_val,
                gamma=gamma_val,
                theta=theta_val,
                vega=vega_val,
                bid=bid,
                ask=ask,
                mid_price=mid_price,
                open_interest=int(contract.OpenInterest),
                days_to_expiry=dte,
            )
            candidates.append(opt_contract)

        return candidates

    def _route_vass_strategy(
        self,
        direction_str: str,
        overlay_state: Optional[str] = None,
    ) -> tuple:
        """
        V2.23: Route to credit or debit strategy based on IV environment.

        Uses IVSensor classification to select strategy from VASS matrix.
        Falls back to debit spread with default DTE if IVSensor not ready or VASS disabled.

        Args:
            direction_str: "BULLISH" or "BEARISH"

        Returns:
            Tuple of (strategy: Optional[SpreadStrategy], dte_min: int, dte_max: int, is_credit: bool)
        """
        if getattr(config, "VASS_ENABLED", True) and self.options_engine.is_iv_sensor_ready():
            iv_environment = self.options_engine.get_iv_environment()
            strategy, dte_min, dte_max = self.options_engine.select_vass_strategy(
                direction_str, iv_environment
            )
            overlay = str(overlay_state or "").upper()
            if overlay == "EARLY_STRESS":
                if (
                    direction_str == "BULLISH"
                    and strategy == SpreadStrategy.BULL_CALL_DEBIT
                    and bool(getattr(config, "VASS_EARLY_STRESS_BULL_STRATEGY_TO_CREDIT", True))
                ):
                    self.Log(
                        "VASS_EARLY_STRESS_REMIX: BULL_CALL_DEBIT->BULL_PUT_CREDIT | "
                        f"IV={iv_environment}"
                    )
                    strategy = SpreadStrategy.BULL_PUT_CREDIT
                    # D8 fix: re-anchor DTE window after strategy remap so credit spreads
                    # do not inherit debit-oriented DTE bounds.
                    dte_min = int(getattr(config, "VASS_HIGH_IV_DTE_MIN", dte_min))
                    dte_max = int(getattr(config, "VASS_HIGH_IV_DTE_MAX", dte_max))
                if (
                    direction_str == "BEARISH"
                    and strategy == SpreadStrategy.BEAR_PUT_DEBIT
                    and iv_environment in {"MEDIUM", "HIGH"}
                    and bool(getattr(config, "VASS_EARLY_STRESS_BEAR_PREFER_CREDIT", True))
                ):
                    self.Log(
                        "VASS_EARLY_STRESS_REMIX: BEAR_PUT_DEBIT->BEAR_CALL_CREDIT | "
                        f"IV={iv_environment}"
                    )
                    strategy = SpreadStrategy.BEAR_CALL_CREDIT
                    # D8 fix: re-anchor DTE window after strategy remap so credit spreads
                    # do not inherit debit-oriented DTE bounds.
                    dte_min = int(getattr(config, "VASS_HIGH_IV_DTE_MIN", dte_min))
                    dte_max = int(getattr(config, "VASS_HIGH_IV_DTE_MAX", dte_max))
            is_credit = self.options_engine.is_credit_strategy(strategy)
            return (strategy, dte_min, dte_max, is_credit)
        return (None, config.SPREAD_DTE_MIN, config.SPREAD_DTE_MAX, False)

    def _strategy_option_right(self, strategy: Optional[SpreadStrategy]) -> Optional[OptionRight]:
        """
        Determine which option right (CALL/PUT) is required for a VASS strategy.
        """
        if strategy is None:
            return None
        if strategy in (
            SpreadStrategy.BULL_CALL_DEBIT,
            SpreadStrategy.BEAR_CALL_CREDIT,
        ):
            return OptionRight.Call
        if strategy in (
            SpreadStrategy.BEAR_PUT_DEBIT,
            SpreadStrategy.BULL_PUT_CREDIT,
        ):
            return OptionRight.Put
        return None

    def _build_vass_dte_fallbacks(self, dte_min: int, dte_max: int) -> List[Tuple[int, int]]:
        """
        V6.12: Build ordered DTE ranges for VASS spread selection.

        Primary range is the configured VASS window. Fallback widens the window
        to avoid "cooldown traps" when primary DTE has no viable contracts.
        """
        ranges = [(dte_min, dte_max)]
        fallback_min = max(5, dte_min - 2)
        fallback_max = min(45, dte_max + 14)
        if (fallback_min, fallback_max) != (dte_min, dte_max):
            ranges.append((fallback_min, fallback_max))
        return ranges

    def _canonical_options_reason_code(self, code: Optional[str]) -> str:
        """
        Normalize legacy/mixed reason codes to explicit E_*/R_* taxonomy.
        """
        raw = (code or "").strip()
        if not raw:
            return "E_NO_REASON_UNCLASSIFIED"
        if raw.startswith(("E_", "R_")):
            return raw

        mapping = {
            "TRADE_LIMIT_BLOCK": "R_TRADE_LIMIT",
            "TIME_WINDOW_BLOCK": "E_TIME_WINDOW",
            "GAP_FILTER_BLOCK": "E_GAP_FILTER",
            "VOL_SHOCK_BLOCK": "E_VOL_SHOCK",
            "VIX_MAX_BLOCK": "E_VIX_MAX",
            "PUT_ENTRY_VIX_MAX_BLOCK": "E_PUT_VIX_MAX",
            "WIN_RATE_GATE_BLOCK": "R_WIN_RATE_GATE",
            "REGIME_CRISIS_BLOCK": "E_REGIME_CRISIS",
            "DIRECTION_MISSING": "E_DIRECTION_MISSING",
            "BULL_CALL_STRESS_BLOCK": "E_CALL_GATE_STRESS",
            "SPREAD_COOLDOWN_ACTIVE": "R_SPREAD_COOLDOWN",
            "CREDIT_ENTRY_VALIDATION_FAILED": "R_SPREAD_SELECTION_FAIL_UNCLASSIFIED",
            "DEBIT_ENTRY_VALIDATION_FAILED": "R_SPREAD_SELECTION_FAIL_UNCLASSIFIED",
            "UNKNOWN": "E_UNKNOWN_UNCLASSIFIED",
        }
        if raw in mapping:
            return mapping[raw]
        if raw.startswith("BEAR_PUT_ASSIGNMENT_GATE_"):
            return f"R_{raw}"
        return f"E_{raw}"

    def _apply_spread_margin_guard(
        self,
        signal: Optional[TargetWeight],
        source_tag: str,
    ) -> Optional[TargetWeight]:
        """
        Final spread margin guard before router submission.
        Applies identical logic for EOD and intraday VASS spread flows.
        """
        if signal is None or not signal.metadata:
            return signal
        if not signal.metadata.get("spread_short_leg_quantity"):
            return signal

        spread_width = signal.metadata.get("spread_width", config.SPREAD_WIDTH_TARGET)
        spread_type = signal.metadata.get("spread_type", "DEBIT")
        credit_received = signal.metadata.get("spread_credit_received")
        short_qty_raw = int(signal.metadata.get("spread_short_leg_quantity", 0))
        contracts_requested = abs(short_qty_raw)
        if contracts_requested <= 0:
            return None

        base_margin_per_contract = self.options_engine.estimate_spread_margin_per_contract(
            spread_width=spread_width,
            spread_type=spread_type,
            credit_received=credit_received,
        )
        safety = max(getattr(config, "SPREAD_MARGIN_SAFETY_FACTOR", 0.80), 0.01)
        required_per_contract = base_margin_per_contract / safety

        free_margin = float(self.Portfolio.MarginRemaining)
        total_equity = float(self.Portfolio.TotalPortfolioValue)
        cushion_pct = getattr(config, "MARGIN_MIN_FREE_EQUITY_PCT", 0.10)
        min_free_margin = total_equity * cushion_pct
        effective_free_margin = max(0.0, free_margin - min_free_margin)

        required_margin = contracts_requested * required_per_contract
        if required_margin <= effective_free_margin:
            # Align with router options budget gate to avoid margin-pass/router-reject churn.
            if self.portfolio_router is not None and bool(
                getattr(config, "OPTIONS_BUDGET_GATE_ENABLED", True)
            ):
                budget_required = required_margin
                try:
                    # Use router's own combo estimator for consistency with execute() gate.
                    per_contract, _ = self.portfolio_router._estimate_combo_margin_per_contract(  # type: ignore[attr-defined]
                        signal.metadata
                    )
                    if per_contract > 0:
                        budget_required = contracts_requested * per_contract
                except Exception:
                    pass

                budget_cap = float(self.portfolio_router.get_options_budget_cap())
                budget_used = float(self.portfolio_router.get_options_budget_used())
                projected = budget_used + budget_required
                if budget_cap > 0 and budget_required > 0 and projected > budget_cap:
                    self._diag_vass_block_count += 1
                    self.Log(
                        f"{source_tag}: BLOCKED - options budget precheck | "
                        f"Used=${budget_used:,.0f} + Req=${budget_required:,.0f} > "
                        f"Cap=${budget_cap:,.0f} ({(projected / budget_cap):.1%})"
                    )
                    return None

            self.Log(
                f"{source_tag}: Margin check passed | Required=${required_margin:,.0f} | "
                f"Effective Free=${effective_free_margin:,.0f} | Equity=${total_equity:,.0f}"
            )
            return signal

        max_contracts = int(effective_free_margin / required_per_contract)
        if max_contracts < 1:
            self.Log(
                f"{source_tag}: BLOCKED - Insufficient margin for 1 spread | "
                f"Required=${required_per_contract:,.0f}/contract | "
                f"Effective Free=${effective_free_margin:,.0f}"
            )
            return None

        short_sign = -1 if short_qty_raw < 0 else 1
        signal.metadata["spread_short_leg_quantity"] = short_sign * max_contracts
        signal.metadata["spread_long_leg_quantity"] = max_contracts
        signal.metadata["contracts"] = max_contracts
        signal.requested_quantity = max_contracts

        self.Log(
            f"{source_tag}: MARGIN-SIZED DOWN | "
            f"Requested={contracts_requested} -> Actual={max_contracts} contracts | "
            f"Per=${required_per_contract:,.0f} | Effective Free=${effective_free_margin:,.0f}"
        )
        return signal

    def _select_swing_option_contract(
        self, chain, direction: OptionDirection = None
    ) -> Optional[OptionContract]:
        """
        V2.3: Select QQQ option contract for SWING mode (5-45 DTE).

        Target delta: 0.70 (slightly ITM for higher directional exposure)

        Criteria:
        - Target 0.70 delta (±0.15 tolerance)
        - DTE 5-45 days (swing mode only)
        - Sufficient open interest
        - Tight bid-ask spread

        Args:
            chain: QuantConnect options chain.
            direction: OptionDirection.CALL or OptionDirection.PUT (default: CALL)

        Returns:
            OptionContract or None if no suitable contract found.
        """
        if chain is None:
            return None

        # Default to CALL if direction not specified
        if direction is None:
            direction = OptionDirection.CALL

        qqq_price = self.Securities[self.qqq].Price
        target_delta = config.OPTIONS_SWING_DELTA_TARGET  # 0.70

        # Determine which option right to filter for
        target_right = OptionRight.Call if direction == OptionDirection.CALL else OptionRight.Put

        # Filter for target direction, target delta, SWING DTE (5-45 days)
        candidates = []
        for contract in chain:
            if contract.Right != target_right:
                continue

            # Check SWING DTE range (5-45 days per spec)
            dte = (contract.Expiry - self.Time).days
            if dte < config.OPTIONS_SWING_DTE_MIN or dte > config.OPTIONS_SWING_DTE_MAX:
                continue

            # V2.3: Get delta and check if within tolerance of target
            # V2.12 Fix #7: Skip contracts with missing or zero Greeks (backtest data gaps)
            if not hasattr(contract, "Greeks") or contract.Greeks.Delta == 0:
                continue  # Skip contracts without valid Greeks data
            contract_delta = abs(contract.Greeks.Delta)
            delta_diff = abs(contract_delta - target_delta)
            if delta_diff > config.OPTIONS_DELTA_TOLERANCE:
                continue

            # Check liquidity
            if contract.OpenInterest < config.OPTIONS_MIN_OPEN_INTEREST:
                continue

            # Check spread - use safe price getter
            bid, ask = self._get_contract_prices(contract)
            if bid <= 0 or ask <= 0:
                continue

            mid_price = (bid + ask) / 2
            spread_pct = (ask - bid) / mid_price if mid_price > 0 else 1.0

            if spread_pct > config.OPTIONS_SPREAD_WARNING_PCT:
                continue

            # Create OptionContract object with specified direction (CALL or PUT)
            opt_contract = OptionContract(
                symbol=str(contract.Symbol),
                underlying="QQQ",
                direction=direction,  # V2.3: Use direction parameter
                strike=contract.Strike,
                expiry=str(contract.Expiry.date()),
                delta=contract_delta,
                gamma=contract.Greeks.Gamma if hasattr(contract, "Greeks") else 0.0,
                vega=contract.Greeks.Vega if hasattr(contract, "Greeks") else 0.0,
                theta=contract.Greeks.Theta if hasattr(contract, "Greeks") else 0.0,
                bid=bid,
                ask=ask,
                mid_price=mid_price,
                open_interest=contract.OpenInterest,
                days_to_expiry=dte,
            )

            # V2.3: Score by proximity to target delta (0.70) + liquidity
            delta_score = 1.0 - (delta_diff / config.OPTIONS_DELTA_TOLERANCE)
            liquidity_score = 1.0 - spread_pct
            score = (delta_score * 0.7) + (liquidity_score * 0.3)
            candidates.append((score, opt_contract))

        if not candidates:
            return None

        # Return best candidate (closest to target delta with good liquidity)
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

    def _get_contract_prices(self, contract) -> Tuple[float, float]:
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

    def _build_option_contract_from_fill(
        self,
        symbol: Any,
        fill_price: float,
        direction_hint: Optional[OptionDirection] = None,
    ) -> Optional[OptionContract]:
        """Best-effort OptionContract reconstruction for fill recovery paths."""
        try:
            sec = self.Securities[symbol] if symbol in self.Securities else None
        except Exception:
            sec = None

        try:
            symbol_obj = symbol
            symbol_str = str(symbol_obj)
            strike = float(getattr(getattr(symbol_obj, "ID", None), "StrikePrice", 0.0) or 0.0)
            expiry_obj = getattr(getattr(symbol_obj, "ID", None), "Date", None)
            expiry = str(expiry_obj.date()) if expiry_obj is not None else ""
            right_obj = getattr(getattr(symbol_obj, "ID", None), "OptionRight", None)
            right_str = str(right_obj).upper() if right_obj is not None else ""
        except Exception:
            return None

        if direction_hint is not None:
            direction = direction_hint
        elif "PUT" in right_str or right_str.endswith("P"):
            direction = OptionDirection.PUT
        else:
            direction = OptionDirection.CALL

        bid = float(getattr(sec, "BidPrice", 0.0) or 0.0) if sec is not None else 0.0
        ask = float(getattr(sec, "AskPrice", 0.0) or 0.0) if sec is not None else 0.0
        mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else float(fill_price)

        delta = 0.0
        gamma = 0.0
        vega = 0.0
        theta = 0.0
        if sec is not None and hasattr(sec, "Greeks") and sec.Greeks is not None:
            try:
                delta = float(abs(sec.Greeks.Delta))
                gamma = float(sec.Greeks.Gamma)
                vega = float(sec.Greeks.Vega)
                theta = float(sec.Greeks.Theta)
            except Exception:
                pass

        open_interest = int(getattr(sec, "OpenInterest", 0) or 0) if sec is not None else 0
        days_to_expiry = 0
        try:
            if expiry_obj is not None:
                days_to_expiry = int((expiry_obj.date() - self.Time.date()).days)
        except Exception:
            pass

        return OptionContract(
            symbol=symbol_str,
            underlying="QQQ",
            direction=direction,
            strike=strike,
            expiry=expiry,
            delta=delta,
            gamma=gamma,
            vega=vega,
            theta=theta,
            bid=bid,
            ask=ask,
            mid_price=mid,
            open_interest=open_interest,
            days_to_expiry=days_to_expiry,
        )

    def _generate_options_signals_gated(
        self, regime_state: RegimeState, capital_state: CapitalState
    ) -> None:
        """V6.0: Simplified EOD options gating.

        Options are independent with their own conviction system (VASS/MICRO).
        Direction is determined by conviction resolution inside _generate_options_signals().
        Startup gate only controls whether options are allowed, not direction.
        """
        # V6.4: Skip options engine in isolation mode if disabled
        if config.ISOLATION_TEST_MODE and not config.ISOLATION_OPTIONS_ENABLED:
            return

        # V6.0: Check startup gate permission (no direction routing)
        if not self.startup_gate.allows_options():
            return  # INDICATOR_WARMUP — no options at all

        # Cold start sizing for options
        is_cold_start = self.cold_start_engine.is_cold_start_active()
        size_mult = config.OPTIONS_COLD_START_MULTIPLIER if is_cold_start else 1.0

        # V6.0: Options at 100% from startup gate (conviction handles direction)
        # No startup gate size reduction for options

        self._generate_options_signals(regime_state, capital_state, size_mult, is_eod_scan=True)

    def _generate_hedge_signals(self, regime_state: RegimeState) -> None:
        """
        Generate Hedge Engine signals at end of day.

        V6.11: Calculates target SH allocation based on regime level.

        Args:
            regime_state: Current regime state.
        """
        # V6.4: Skip hedge engine in isolation mode if disabled
        if config.ISOLATION_TEST_MODE and not config.ISOLATION_HEDGE_ENABLED:
            return

        total_equity = self.Portfolio.TotalPortfolioValue
        if total_equity <= 0:
            return

        # V6.11: Calculate current SH allocation
        sh_pct = self.Portfolio[self.sh].HoldingsValue / total_equity

        signals = self.hedge_engine.get_hedge_signals(
            regime_score=regime_state.smoothed_score,
            current_sh_pct=sh_pct,
            is_panic_mode=False,
        )
        for signal in signals:
            self.portfolio_router.receive_signal(signal)

    def _generate_hedge_exit_signals(self) -> None:
        """
        V3.0/V6.11: Generate signals to exit hedge positions when regime improves.

        Called when regime >= HEDGE_REGIME_GATE (50) to unwind SH positions.
        Per thesis: Hedges should be 0% in Bull (70+) and Neutral (50-69).
        """
        # V6.4: Skip hedge engine in isolation mode if disabled
        if config.ISOLATION_TEST_MODE and not config.ISOLATION_HEDGE_ENABLED:
            return

        total_equity = self.Portfolio.TotalPortfolioValue
        if total_equity <= 0:
            return

        # V6.11: Check if we have SH hedge position to exit
        sh_invested = self.Portfolio[self.sh].Invested

        if not sh_invested:
            return  # No hedges to exit

        # Emit 0% target weight signal to unwind SH
        sh_pct = self.Portfolio[self.sh].HoldingsValue / total_equity
        signal = TargetWeight(
            symbol="SH",
            target_weight=0.0,
            source="HEDGE",
            urgency=Urgency.EOD,
            reason=f"REGIME_EXIT: Score >= {config.HEDGE_REGIME_GATE} (was {sh_pct:.1%})",
        )
        self.portfolio_router.receive_signal(signal)
        self.Log(
            f"HEDGE_EXIT: SH | Regime >= {config.HEDGE_REGIME_GATE} | "
            f"Unwinding {sh_pct:.1%} position"
        )

    # =========================================================================
    # UTILITY HELPERS
    # =========================================================================

    def _run_risk_checks(self, data: Slice) -> RiskCheckResult:
        """
        Run all risk engine checks.

        Args:
            data: Current data slice.

        Returns:
            RiskCheckResult with safeguard status and allowed actions.
        """
        current_equity = self.Portfolio.TotalPortfolioValue
        spy_price = self.Securities[self.spy].Price

        # Get SPY bar range for vol shock detection
        spy_bar_range = 0.0
        if data.Bars.ContainsKey(self.spy):
            bar = data.Bars[self.spy]
            spy_bar_range = bar.High - bar.Low

        # Update SPY ATR for vol shock calculation
        if self.spy_atr.IsReady:
            self.risk_engine.set_spy_atr(self.spy_atr.Current.Value)

        # Run all checks
        result = self.risk_engine.check_all(
            current_equity=current_equity,
            spy_price=spy_price,
            spy_bar_range=spy_bar_range,
            current_time=self.Time,
        )

        # Track triggered safeguards
        for safeguard in result.active_safeguards:
            safeguard_name = safeguard.value
            if safeguard_name not in self.today_safeguards:
                self.today_safeguards.append(safeguard_name)

        return result

    def _handle_kill_switch(self, risk_result: RiskCheckResult) -> None:
        """
        V2.27: Handle graduated kill switch tiers.

        Tier 2 (TREND_EXIT): Liquidate trend + MR. Keep spreads (decouple).
        Tier 3 (FULL_EXIT): Liquidate everything including spreads. Reset cold start.

        Tier 1 (REDUCE) is handled inline through risk_result flags — it doesn't
        call this handler.

        Args:
            risk_result: Risk check result containing ks_tier and symbols to liquidate.
        """
        # V2.3 FIX: Only handle kill switch ONCE per day
        if self._kill_switch_handled_today:
            return
        self._kill_switch_handled_today = True

        tier = risk_result.ks_tier

        # Calculate P&L diagnostics for logging
        options_gross_pnl = 0.0
        options_long_pnl = 0.0
        options_short_pnl = 0.0
        trend_pnl = 0.0
        # V6.11: Use config for trend symbols
        trend_symbols = config.TREND_SYMBOLS

        for kvp in self.Portfolio:
            holding = kvp.Value
            if holding.Invested:
                if holding.Symbol.SecurityType == SecurityType.Option:
                    options_gross_pnl += holding.UnrealizedProfit
                    if holding.Quantity > 0:
                        options_long_pnl += holding.UnrealizedProfit
                    else:
                        options_short_pnl += holding.UnrealizedProfit
                elif str(holding.Symbol.Value) in trend_symbols:
                    trend_pnl += holding.UnrealizedProfit

        self.Log(
            f"SPREAD_PNL_DIAG: Long={options_long_pnl:+,.0f} Short={options_short_pnl:+,.0f} "
            f"Net={options_gross_pnl:+,.0f} | Trend={trend_pnl:+,.0f}"
        )

        total_loss_pct = 0.0
        if self.equity_prior_close > 0:
            total_loss_pct = (
                self.Portfolio.TotalPortfolioValue - self.equity_prior_close
            ) / self.equity_prior_close

        self.Log(
            f"KS_GRADUATED: {tier.value} at {self.Time} | "
            f"Equity={self.Portfolio.TotalPortfolioValue:,.2f} | "
            f"Loss={total_loss_pct:.2%} | Options P&L=${options_gross_pnl:,.0f} | "
            f"Trend P&L=${trend_pnl:,.0f}"
        )

        # Trigger in scheduler (disables all trading)
        self.scheduler.trigger_kill_switch()

        # ---- TIER 3: FULL EXIT ----
        if tier == KSTier.FULL_EXIT:
            self.Log("KS_FULL_EXIT: Liquidating ALL positions")

            # V2.33 CRITICAL: Close ALL options FIRST using atomic close (shorts before longs)
            # This MUST happen before any equity liquidation to prevent naked short margin errors
            self._close_options_atomic(reason="KS_TIER3_OPTIONS", clear_tracking=True)

            # Now safe to liquidate equity positions
            # symbols_to_liquidate is List[str] (equity only, options handled above)
            for symbol in risk_result.symbols_to_liquidate:
                self.Liquidate(symbol)

            # Clear options state and reset cold start
            self.options_engine.clear_all_positions()
            if config.KS_COLD_START_RESET_ON_TIER_3:
                self.cold_start_engine.reset()

            # V3.0 P0-A: Reset all engine internal state after full liquidation
            self.trend_engine.reset()
            if hasattr(self, "mr_engine") and self.mr_engine:
                self.mr_engine.reset()
            if hasattr(self, "hedge_engine") and self.hedge_engine:
                self.hedge_engine.reset()
            # Clear main.py spread tracking dicts (may already be cleared by atomic close)
            self._spread_fill_tracker = None
            self._pending_spread_orders.clear()
            self._pending_spread_orders_reverse.clear()
            self._pending_exit_orders.clear()
            self._exit_retry_scheduled_at.clear()
            self.Log("KS_CLEANUP: All engine state reset after Tier 3 liquidation")

        # ---- TIER 2: TREND EXIT ----
        elif tier == KSTier.TREND_EXIT:
            # V2.33: Close options FIRST using atomic close (shorts before longs)
            # V2.27: Spread decouple — keep active spreads, they have -50% stop
            if config.KILL_SWITCH_SPREAD_DECOUPLE:
                spreads = self.options_engine.get_spread_positions()
                spread_count = sum(s.num_spreads for s in spreads) if spreads else 0
                self.Log(
                    f"KS_SPREAD_DECOUPLE: Keeping {spread_count} active spreads | "
                    f"Monitored by -{config.SPREAD_STOP_LOSS_PCT:.0%} spread stop"
                )
                # Close single-leg options only (NOT spread legs) using atomic close
                self._ks_close_single_leg_options_atomic()
            else:
                # Legacy: close everything including spreads atomically
                self._close_options_atomic(reason="KS_TIER2_OPTIONS", clear_tracking=True)
                self.options_engine.clear_all_positions()

            # NOW liquidate trend + MR equity positions (options already handled above)
            # symbols_to_liquidate is List[str] (equity only, options handled above)
            equity_count = 0
            for symbol in risk_result.symbols_to_liquidate:
                self.Liquidate(symbol)
                equity_count += 1
            self.Log(f"KS_TREND_EXIT: Liquidated {equity_count} equity symbols")

            if config.KS_COLD_START_RESET_ON_TIER_2:
                self.cold_start_engine.reset()

            # V3.0 P0-A: Reset trend + MR state after Tier 2 liquidation (hedge stays)
            self.trend_engine.reset()
            if hasattr(self, "mr_engine") and self.mr_engine:
                self.mr_engine.reset()
            self.Log("KS_CLEANUP: Trend + MR state reset after Tier 2 liquidation")

    def _ks_close_single_leg_options_atomic(self) -> int:
        """
        V2.33: Close ONLY single-leg options (NOT spread legs) using atomic close.

        Used for Tier 2 decouple mode where we want to keep spreads but close
        any single-leg options (intraday positions, protective puts).

        Returns:
            Number of single-leg options closed.
        """
        spread_symbols = set()

        # Get spread leg symbols to exclude
        for spread in self.options_engine.get_spread_positions():
            if hasattr(spread.long_leg, "symbol"):
                spread_symbols.add(str(spread.long_leg.symbol))
            if hasattr(spread.short_leg, "symbol"):
                spread_symbols.add(str(spread.short_leg.symbol))

        # Collect single-leg options (excluding spread legs)
        short_options = []
        long_options = []
        for kvp in self.Portfolio:
            holding = kvp.Value
            if holding.Invested and holding.Symbol.SecurityType == SecurityType.Option:
                symbol_str = str(holding.Symbol)
                # Skip if this is a spread leg
                if any(spread_sym in symbol_str for spread_sym in spread_symbols):
                    continue
                if holding.Quantity < 0:
                    short_options.append((holding.Symbol, holding.Quantity))
                else:
                    long_options.append((holding.Symbol, holding.Quantity))

        # ATOMIC CLOSE: Shorts first, then longs
        for symbol, qty in short_options:
            try:
                close_qty = abs(qty)
                self._submit_option_close_market_order(
                    symbol=symbol,
                    quantity=close_qty,
                    reason="KS_SINGLE_LEG",
                )
                self.Log(f"KS_SINGLE_LEG: Closed SHORT {str(symbol)[-21:]} x{close_qty}")
            except Exception as e:
                self.Log(f"KS_SINGLE_LEG: FAILED short close {str(symbol)[-21:]} | {e}")

        for symbol, qty in long_options:
            try:
                self._submit_option_close_market_order(
                    symbol=symbol,
                    quantity=-qty,
                    reason="KS_SINGLE_LEG",
                )
                self.Log(f"KS_SINGLE_LEG: Closed LONG {str(symbol)[-21:]} x{qty}")
            except Exception as e:
                self.Log(f"KS_SINGLE_LEG: FAILED long close {str(symbol)[-21:]} | {e}")

        total = len(short_options) + len(long_options)
        if total > 0:
            self.Log(f"KS_SINGLE_LEG: Closed {total} single-leg options (spread preserved)")
        return total

    def _handle_panic_mode(self, risk_result: RiskCheckResult) -> None:
        """
        Handle panic mode trigger.

        Liquidates long positions only (keeps hedges).

        Args:
            risk_result: Risk check result containing symbols to liquidate.
        """
        self.Log("PANIC_MODE: Triggered")

        # Trigger in scheduler (for tracking)
        self.scheduler.trigger_panic_mode()

        # Liquidate specified symbols (longs only)
        for symbol in risk_result.symbols_to_liquidate:
            self.Liquidate(symbol)

    def _scan_mr_signals(self, data: Slice) -> None:
        """
        Scan for Mean Reversion entry signals.

        Checks TQQQ and SOXL for oversold conditions (RSI < 25).

        Args:
            data: Current data slice.
        """
        # V6.4: Skip MR engine in isolation mode if disabled
        if config.ISOLATION_TEST_MODE and not config.ISOLATION_MR_ENABLED:
            return

        # V2.20: Respect rejection cooldown before scanning
        if (
            self._mr_rejection_cooldown_until is not None
            and self.Time < self._mr_rejection_cooldown_until
        ):
            return

        # Get required context
        regime_score = self._last_regime_score
        days_running = (
            self.cold_start_engine.get_days_running()
            if hasattr(self.cold_start_engine, "get_days_running")
            else 5
        )
        gap_filter = getattr(self, "_gap_filter_active", False)
        vol_shock = getattr(self, "_vol_shock_active", False)
        time_guard = getattr(self, "_time_guard_active", False)

        # Calculate average volumes from rolling windows
        tqqq_avg_vol = self._get_average_volume(self.tqqq_volumes)
        soxl_avg_vol = self._get_average_volume(self.soxl_volumes)

        # DEBUG: Disabled to save log space
        # if self.Time.weekday() == 0 and self.Time.hour == 10 and self.Time.minute == 5:
        #     mr_pos = self.mr_engine.get_position_symbol()
        #     broker_tqqq = self.Portfolio[self.tqqq].Invested
        #     broker_soxl = self.Portfolio[self.soxl].Invested
        #     self.Log(f"MR_WEEKLY: regime={regime_score:.1f} mr_pos={mr_pos} broker_tqqq={broker_tqqq} broker_soxl={broker_soxl}")

        # Check TQQQ
        if self.tqqq_rsi.IsReady and not self.Portfolio[self.tqqq].Invested:
            tqqq_price = self.Securities[self.tqqq].Price
            tqqq_open = (
                data.Bars[self.tqqq].Open if data.Bars.ContainsKey(self.tqqq) else tqqq_price
            )
            tqqq_volume = (
                float(data.Bars[self.tqqq].Volume) if data.Bars.ContainsKey(self.tqqq) else 0.0
            )
            # Use open price as VWAP approximation for intraday
            tqqq_vwap = (tqqq_open + tqqq_price) / 2.0

            # DEBUG: Disabled to save log space
            # if self.Time.hour == 10 and self.Time.minute == 10:
            #     drop_pct = (tqqq_open - tqqq_price) / tqqq_open if tqqq_open > 0 else 0
            #     vol_ratio = tqqq_volume / tqqq_avg_vol if tqqq_avg_vol > 0 else 0
            #     self.Log(f"MR_TQQQ: RSI={self.tqqq_rsi.Current.Value:.1f} drop={drop_pct:.2%} vol_ratio={vol_ratio:.2f}")

            signal = self.mr_engine.check_entry_signal(
                symbol="TQQQ",
                current_price=tqqq_price,
                open_price=tqqq_open,
                rsi_value=self.tqqq_rsi.Current.Value,
                current_volume=tqqq_volume,
                avg_volume=tqqq_avg_vol,
                vwap=tqqq_vwap,
                regime_score=regime_score,
                days_running=days_running,
                gap_filter_triggered=gap_filter,
                vol_shock_active=vol_shock,
                time_guard_active=time_guard,
                current_hour=self.Time.hour,
                current_minute=self.Time.minute,
                vix_value=self._current_vix,  # V2.1: Pass VIX for regime filter
            )
            if signal:
                self.portfolio_router.receive_signal(signal)

        # Check SOXL
        if self.soxl_rsi.IsReady and not self.Portfolio[self.soxl].Invested:
            soxl_price = self.Securities[self.soxl].Price
            soxl_open = (
                data.Bars[self.soxl].Open if data.Bars.ContainsKey(self.soxl) else soxl_price
            )
            soxl_volume = (
                float(data.Bars[self.soxl].Volume) if data.Bars.ContainsKey(self.soxl) else 0.0
            )
            # Use open price as VWAP approximation for intraday
            soxl_vwap = (soxl_open + soxl_price) / 2.0

            signal = self.mr_engine.check_entry_signal(
                symbol="SOXL",
                current_price=soxl_price,
                open_price=soxl_open,
                rsi_value=self.soxl_rsi.Current.Value,
                current_volume=soxl_volume,
                avg_volume=soxl_avg_vol,
                vwap=soxl_vwap,
                regime_score=regime_score,
                days_running=days_running,
                gap_filter_triggered=gap_filter,
                vol_shock_active=vol_shock,
                time_guard_active=time_guard,
                current_hour=self.Time.hour,
                current_minute=self.Time.minute,
                vix_value=self._current_vix,  # V2.1: Pass VIX for regime filter
            )
            if signal:
                self.portfolio_router.receive_signal(signal)

        # V6.11: Check SPXL (3× S&P 500 for broader market bounce)
        if self.spxl_rsi.IsReady and not self.Portfolio[self.spxl].Invested:
            spxl_price = self.Securities[self.spxl].Price
            spxl_open = (
                data.Bars[self.spxl].Open if data.Bars.ContainsKey(self.spxl) else spxl_price
            )
            spxl_volume = (
                float(data.Bars[self.spxl].Volume) if data.Bars.ContainsKey(self.spxl) else 0.0
            )
            spxl_vwap = (spxl_open + spxl_price) / 2.0
            spxl_avg_vol = self._get_average_volume(self.spxl_volumes)

            signal = self.mr_engine.check_entry_signal(
                symbol="SPXL",
                current_price=spxl_price,
                open_price=spxl_open,
                rsi_value=self.spxl_rsi.Current.Value,
                current_volume=spxl_volume,
                avg_volume=spxl_avg_vol,
                vwap=spxl_vwap,
                regime_score=regime_score,
                days_running=days_running,
                gap_filter_triggered=gap_filter,
                vol_shock_active=vol_shock,
                time_guard_active=time_guard,
                current_hour=self.Time.hour,
                current_minute=self.Time.minute,
                vix_value=self._current_vix,
            )
            if signal:
                self.portfolio_router.receive_signal(signal)

    def _check_mr_exits(self, data: Slice) -> None:
        """
        Check for Mean Reversion exit conditions.

        Checks target profit and stop loss for existing MR positions.

        Args:
            data: Current data slice.
        """
        # Safety check: Detect sync issues between broker and MR engine
        # V6.11: Updated for all MR symbols (TQQQ, SOXL, SPXL)
        mr_pos = self.mr_engine.get_position_symbol()
        broker_tqqq = self.Portfolio[self.tqqq].Invested
        broker_soxl = self.Portfolio[self.soxl].Invested
        broker_spxl = self.Portfolio[self.spxl].Invested

        # If broker has MR position but engine doesn't, we have a sync issue
        if broker_tqqq and mr_pos is None:
            self.Liquidate(self.tqqq)
            return

        if broker_soxl and mr_pos is None:
            self.Liquidate(self.soxl)
            return

        if broker_spxl and mr_pos is None:
            self.Liquidate(self.spxl)
            return

        # If engine has position but broker doesn't, clear engine state
        if mr_pos is not None and not broker_tqqq and not broker_soxl and not broker_spxl:
            self.mr_engine.remove_position()
            return

        # MR engine tracks its own position internally
        # Check TQQQ price if that's the current position
        if self.Portfolio[self.tqqq].Invested:
            signal = self.mr_engine.check_exit_signals(
                current_price=self.Securities[self.tqqq].Price,
                current_hour=self.Time.hour,
                current_minute=self.Time.minute,
            )
            if signal:
                self.portfolio_router.receive_signal(signal)

        # Check SOXL price if that's the current position
        if self.Portfolio[self.soxl].Invested:
            signal = self.mr_engine.check_exit_signals(
                current_price=self.Securities[self.soxl].Price,
                current_hour=self.Time.hour,
                current_minute=self.Time.minute,
            )
            if signal:
                self.portfolio_router.receive_signal(signal)

        # V6.11: Check SPXL price if that's the current position
        if self.Portfolio[self.spxl].Invested:
            signal = self.mr_engine.check_exit_signals(
                current_price=self.Securities[self.spxl].Price,
                current_hour=self.Time.hour,
                current_minute=self.Time.minute,
            )
            if signal:
                self.portfolio_router.receive_signal(signal)

    def _monitor_trend_stops(self, data: Slice) -> None:
        """
        Monitor Trend positions for intraday stop triggers.

        V6.11: Checks Chandelier trailing stops for trend symbols.

        Args:
            data: Current data slice.
        """
        for symbol in config.TREND_SYMBOLS:
            sec_symbol = getattr(self, symbol.lower(), None)
            if sec_symbol is None or not self.Portfolio[sec_symbol].Invested:
                continue
            signal = self.trend_engine.check_stop_hit(
                symbol=symbol,
                current_price=self.Securities[sec_symbol].Price,
            )
            if signal:
                self.portfolio_router.receive_signal(signal)
                self._process_immediate_signals()

    def _scan_options_signals_gated(self, data: Slice) -> None:
        """V6.0: Simplified intraday options gating.

        Options have their own conviction system (VASS/MICRO) for direction.
        Startup gate only controls timing, not direction.
        """
        # V6.4: Skip options engine in isolation mode if disabled
        if config.ISOLATION_TEST_MODE and not config.ISOLATION_OPTIONS_ENABLED:
            return

        # V6.0: Options allowed from REDUCED phase (day 4+)
        if not self.startup_gate.allows_options():
            return

        self._scan_options_signals(data)

    def _monitor_risk_greeks(self, data: Slice) -> None:
        # Monitor spread exits/greeks with fresh chain values when available.
        # Skip if no options position
        if not self.options_engine.has_position():
            return

        # Transition handoff: de-risk wrong-way open positions immediately after overlay flips.
        if self._apply_transition_handoff_open_position_derisk(data):
            return

        # V2.3: Check spread exit conditions if we have a spread position
        if self.options_engine.has_spread_position():
            self._check_spread_exit(data)
            return  # Spread exit handling is separate from single-leg Greeks

        # CRITICAL: Fetch FRESH Greeks from OptionChain (not cached values)
        # Greeks change rapidly, especially for 0-2 DTE options
        greeks = self._get_fresh_position_greeks()
        if greeks is None:
            # Fall back to cached Greeks if chain not available
            greeks = self.options_engine.calculate_position_greeks()
            if greeks is None:
                return

        # Update risk engine with Greeks
        self.risk_engine.update_greeks(greeks)

        # Check for Greeks breach
        breach, reasons = self.options_engine.check_greeks_breach(self.risk_engine)
        if breach:
            # Only log Greeks breach once per position to prevent log overflow
            if not self._greeks_breach_logged:
                self.Log(f"GREEKS_BREACH: {', '.join(reasons)}")
                self._greeks_breach_logged = True
            # Emit exit signals for real option holdings (never synthetic symbols).
            signals_emitted = 0
            seen_symbols = set()
            for kvp in self.Portfolio:
                holding = kvp.Value
                if (
                    not holding.Invested
                    or holding.Symbol.SecurityType != SecurityType.Option
                    or int(holding.Quantity) == 0
                ):
                    continue
                symbol_str = self._normalize_symbol_str(holding.Symbol)
                if symbol_str in seen_symbols:
                    continue
                seen_symbols.add(symbol_str)
                if self._has_open_non_oco_order_for_symbol(symbol_str):
                    continue
                self.portfolio_router.receive_signal(
                    TargetWeight(
                        symbol=symbol_str,
                        target_weight=0.0,
                        source="RISK",
                        urgency=Urgency.IMMEDIATE,
                        reason=f"GREEKS_BREACH: {', '.join(reasons)}",
                        requested_quantity=abs(int(holding.Quantity)),
                    )
                )
                signals_emitted += 1
            if signals_emitted == 0:
                self.Log("GREEKS_BREACH_EXIT_SKIP: No live option holdings found")
            return  # Exit already triggered, don't check other exits

        # V2.3.11: Check expiring options force exit (15:45 for 0 DTE)
        # CRITICAL: Prevents auto-exercise of ITM options held into close
        position = self.options_engine.get_position()
        intraday_positions = self.options_engine.get_intraday_positions()
        expiring_candidates = []
        if position is not None:
            expiring_candidates.append(position)
        expiring_candidates.extend(intraday_positions)

        for any_position in expiring_candidates:
            # Get contract expiry date
            any_symbol = self._normalize_symbol_str(any_position.contract.symbol)
            contract_expiry = self._get_option_expiry_date(any_position.contract.symbol, data)
            current_date = str(self.Time.date())
            current_price = self._get_option_current_price(any_position.contract.symbol, data)

            if (
                current_price is not None
                and contract_expiry is not None
                and not self._has_open_non_oco_order_for_symbol(any_symbol)
            ):
                signal = self.options_engine.check_expiring_options_force_exit(
                    current_date=current_date,
                    current_hour=self.Time.hour,
                    current_minute=self.Time.minute,
                    current_price=current_price,
                    contract_expiry_date=contract_expiry,
                    position=any_position,
                )
                if signal is not None:
                    self.portfolio_router.receive_signal(signal)
                    return  # Force exit takes priority, skip other exit checks

        # V2.3.10: Check single-leg exit signals (profit target, stop, DTE exit)
        # This prevents options from being held to expiration/exercise
        if position is not None:
            swing_symbol = self._normalize_symbol_str(position.contract.symbol)
            if self._has_open_non_oco_order_for_symbol(swing_symbol):
                position = None
            else:
                # Get current option price from chain
                current_price = self._get_option_current_price(position.contract.symbol, data)
                current_dte = self._get_option_current_dte(position.contract.symbol, data)

                if current_price is not None:
                    signal = self.options_engine.check_exit_signals(
                        current_price=current_price,
                        current_dte=current_dte,
                    )
                    if signal is not None:
                        self._single_leg_last_exit_reason[swing_symbol] = str(
                            getattr(signal, "reason", "") or ""
                        )[:180]
                        self.portfolio_router.receive_signal(signal)
                        # V9.2 FIX: Cancel OCO on software exit (swing single-leg)
                        try:
                            sym_str = str(position.contract.symbol)
                            if self.oco_manager.cancel_by_symbol(sym_str, reason="SOFTWARE_EXIT"):
                                self.Log(
                                    f"OCO_CANCEL: {sym_str} | "
                                    f"Reason=SOFTWARE_EXIT ({signal.reason})"
                                )
                        except Exception as e:
                            self.Log(f"OCO_CANCEL_ERROR: Swing exit OCO cancel | {e}")

        # V6.22 FIX: Software backup for intraday positions (OCO single-point-of-failure fix)
        # Previously intraday had NO software stop/profit/DTE check — relied solely on OCO.
        # If OCO was cancelled (199 events in 2022), position bled until force-exit.
        for intraday_position in intraday_positions:
            intra_symbol = self._normalize_symbol_str(intraday_position.contract.symbol)
            if not self._has_open_non_oco_order_for_symbol(intra_symbol):
                intra_price = self._get_option_current_price(
                    intraday_position.contract.symbol, data
                )
                intra_dte = self._get_option_current_dte(intraday_position.contract.symbol, data)

                if intra_price is not None:
                    signal = self.options_engine.check_exit_signals(
                        current_price=intra_price,
                        current_dte=intra_dte,
                        position=intraday_position,
                    )
                    if signal is not None:
                        self._single_leg_last_exit_reason[intra_symbol] = str(
                            getattr(signal, "reason", "") or ""
                        )[:180]
                        self.portfolio_router.receive_signal(signal)
                        # V9.2 FIX: Cancel OCO immediately when software backup triggers
                        # an exit. Without this, the OCO stop/limit orders remain active
                        # and could fire after the software exit order fills, creating
                        # accidental short positions from orphaned sell orders.
                        try:
                            sym_str = str(intraday_position.contract.symbol)
                            if self.oco_manager.cancel_by_symbol(sym_str, reason="SOFTWARE_EXIT"):
                                self.Log(
                                    f"OCO_CANCEL: {sym_str} | "
                                    f"Reason=SOFTWARE_EXIT ({signal.reason})"
                                )
                        except Exception as e:
                            self.Log(f"OCO_CANCEL_ERROR: Software exit OCO cancel | {e}")
                    else:
                        # Keep broker OCO rails aligned with software trailing-stop updates.
                        # Without this, software can tighten stop_price while broker OCO stays stale.
                        live_qty = abs(self._get_option_holding_quantity(intra_symbol))
                        if live_qty <= 0:
                            live_qty = abs(int(getattr(intraday_position, "num_contracts", 0) or 0))
                        if live_qty > 0:
                            self._sync_intraday_oco(
                                symbol=intra_symbol,
                                position=intraday_position,
                                quantity=live_qty,
                                reason="TRAIL_REFRESH",
                            )

    def _get_fresh_position_greeks(self) -> Optional[GreeksSnapshot]:
        """
        Fetch fresh Greeks from OptionChain for current position.

        CRITICAL: Greeks cached at entry become stale within minutes.
        For 0-2 DTE options, Greeks can change 50%+ in an hour.
        This method fetches live Greeks from the data feed.

        Returns:
            Fresh GreeksSnapshot or None if chain/contract not available.
        """
        # Get current position symbol
        position = self.options_engine.get_position()
        if position is None:
            return None

        position_symbol = position.contract.symbol

        # Get options chain from CurrentSlice (this function has no Slice param)
        if self.CurrentSlice is None:
            return None
        chain = (
            self.CurrentSlice.OptionChains[self._qqq_option_symbol]
            if self._qqq_option_symbol in self.CurrentSlice.OptionChains
            else None
        )
        if chain is None:
            return None

        # CRITICAL: Wrap chain iteration in try-catch to handle malformed data
        try:
            # Find our contract in the chain and get fresh Greeks
            for contract in chain:
                if str(contract.Symbol) == position_symbol:
                    # Found our contract - extract fresh Greeks
                    delta = contract.Greeks.Delta if hasattr(contract, "Greeks") else None
                    gamma = contract.Greeks.Gamma if hasattr(contract, "Greeks") else None
                    vega = contract.Greeks.Vega if hasattr(contract, "Greeks") else None
                    theta = contract.Greeks.Theta if hasattr(contract, "Greeks") else None

                    if delta is not None:
                        # Update position with fresh Greeks
                        self.options_engine.update_position_greeks(delta, gamma, vega, theta)

                        return GreeksSnapshot(
                            delta=delta,
                            gamma=gamma or 0.0,
                            vega=vega or 0.0,
                            theta=theta or 0.0,
                        )
                    break
        except Exception as e:
            # Chain iteration failed - log and continue with cached Greeks
            self.Log(f"GREEKS_REFRESH_ERROR: {e}")

        return None

    def _find_option_contract(self, symbol: str, data: Slice):
        if self._qqq_option_symbol is None or self._qqq_option_symbol not in data.OptionChains:
            return None
        try:
            for contract in data.OptionChains[self._qqq_option_symbol]:
                if str(contract.Symbol) == symbol:
                    return contract
        except Exception:
            return None
        return None

    def _get_option_current_price(self, symbol: str, data: Slice) -> Optional[float]:
        """Get current mid/last price for an option from the chain."""
        contract = self._find_option_contract(symbol, data)
        if contract is None:
            return None
        bid = contract.BidPrice
        ask = contract.AskPrice
        if bid > 0 and ask > 0:
            return (bid + ask) / 2
        return contract.LastPrice if contract.LastPrice > 0 else None

    def _get_option_current_dte(self, symbol: str, data: Slice) -> Optional[int]:
        """Get current days-to-expiry for an option symbol."""
        contract = self._find_option_contract(symbol, data)
        if contract is None:
            return None
        dte = (contract.Expiry.date() - self.Time.date()).days
        return max(0, dte)

    def _get_option_expiry_date(self, symbol: str, data: Slice) -> Optional[str]:
        """Get expiry date string (YYYY-MM-DD) for an option symbol."""
        contract = self._find_option_contract(symbol, data)
        return str(contract.Expiry.date()) if contract is not None else None

    def _cancel_spread_linked_oco(self, long_symbol: str, short_symbol: str, reason: str) -> None:
        """Cancel any OCO siblings tied to spread legs before forced-close retries."""
        if not hasattr(self, "oco_manager"):
            return
        for leg_symbol in (long_symbol, short_symbol):
            try:
                self.oco_manager.cancel_by_symbol(leg_symbol, reason=reason)
            except Exception:
                pass

    def _schedule_spread_safe_lock_retry(
        self,
        spread_key: str,
        long_symbol: str,
        short_symbol: str,
        retry_reason: str,
        detail: str,
    ) -> None:
        """Schedule non-fatal safe-lock retry when spread close submission fails."""
        safe_retry_min = int(getattr(config, "SPREAD_CLOSE_SAFE_LOCK_RETRY_MIN", 10))
        max_retry_cycles = int(getattr(config, "SPREAD_CLOSE_MAX_RETRY_CYCLES", 12))
        self.Log(
            f"SPREAD_CLOSE_FALSE_NONFATAL: Long={long_symbol} Short={short_symbol} | "
            f"Reason={retry_reason} | Detail={detail}"
        )
        self.Log(
            f"SPREAD_SAFE_LOCK_RETRY_SCHEDULED: Long={long_symbol} Short={short_symbol} | "
            f"RetryIn={safe_retry_min}m | Reason={retry_reason}"
        )
        self._spread_forced_close_reason[spread_key] = f"SAFE_LOCK_RETRY:{retry_reason}"
        self._spread_forced_close_retry[spread_key] = self.Time + timedelta(minutes=safe_retry_min)
        # Keep retrying at emergency cadence after initial escalation.
        self._spread_forced_close_retry_cycles[spread_key] = max(max_retry_cycles - 1, 0)
        self._spread_last_close_submit_at[spread_key] = self.Time

    def _get_actual_option_count(self) -> int:
        """
        V2.5 PART 19 FIX: Count actual option positions in portfolio (stateless).

        This bypasses internal state trackers and counts what is actually
        held in the broker portfolio. Used to detect "zombie state" where
        internal trackers are set but positions don't exist.

        Returns:
            Number of option positions currently held.
        """
        count = 0
        for kvp in self.Portfolio:
            holding = kvp.Value
            if holding.Invested and holding.Symbol.SecurityType == SecurityType.Option:
                count += 1
        return count

    def _validate_options_symbol(self) -> bool:
        """
        Validate that the QQQ options symbol is set.

        Note: Chain availability is checked separately in the calling function
        which has access to the current Slice data.

        Returns:
            True if options symbol is set, False otherwise.
        """
        # Check if symbol is set
        if self._qqq_option_symbol is None:
            return False
        return True

    def _calculate_iv_rank(self, chain) -> float:
        """
        V2.1: Calculate IV Rank from options chain.

        IV Rank = (Current IV - 52wk Low IV) / (52wk High IV - 52wk Low IV)

        Without historical IV data, we estimate using:
        - VIX as market-wide IV proxy
        - Chain average IV vs typical ranges

        Args:
            chain: Options chain for IV calculation.

        Returns:
            IV rank as percentage (0-100).
        """
        # Use VIX as IV proxy (normalized to IV rank scale)
        # VIX typical ranges: Low ~12, High ~35, Avg ~18
        vix = self._current_vix
        vix_low = 12.0
        vix_high = 35.0

        if vix_high <= vix_low:
            return 50.0

        iv_rank = (vix - vix_low) / (vix_high - vix_low) * 100.0
        return max(0.0, min(100.0, iv_rank))

    def _has_leveraged_position(self) -> bool:
        """
        Check if any leveraged long position exists.

        Returns:
            True if any of TQQQ, SOXL, QLD, SSO is held.
        """
        return (
            self.Portfolio[self.tqqq].Invested
            or self.Portfolio[self.soxl].Invested
            or self.Portfolio[self.qld].Invested
            or self.Portfolio[self.sso].Invested
        )

    def _calculate_regime(self, read_only: bool = False) -> RegimeState:
        """
        Calculate current regime state (V2.3: includes VIX).

        Uses end-of-day prices for daily calculation.

        Returns:
            RegimeState with scores and classification.
        """
        # Check if indicators are ready
        if not (self.spy_sma20.IsReady and self.spy_sma50.IsReady and self.spy_sma200.IsReady):
            # Return neutral default state when indicators not ready
            return RegimeState(
                smoothed_score=50.0,
                raw_score=50.0,
                state=RegimeLevel.NEUTRAL,
                trend_score=50.0,
                vix_score=50.0,
                volatility_score=50.0,
                breadth_score=50.0,
                credit_score=50.0,
                chop_score=50.0,
                vix_level=20.0,
                realized_vol=0.0,
                vol_percentile=50.0,
                breadth_spread_value=0.0,
                credit_spread_value=0.0,
                spy_adx_value=25.0,
                new_longs_allowed=True,
                cold_start_allowed=True,
                tmf_target_pct=0.0,
                psq_target_pct=0.0,
                previous_smoothed=50.0,
            )

        # Get historical data from rolling windows
        spy_prices = list(self.spy_closes) if self.spy_closes.IsReady else []
        rsp_prices = list(self.rsp_closes) if self.rsp_closes.IsReady else []
        hyg_prices = list(self.hyg_closes) if self.hyg_closes.IsReady else []
        ief_prices = list(self.ief_closes) if self.ief_closes.IsReady else []
        if read_only:
            # Use live proxy marks intraday so detector deltas reflect tape changes.
            def _overlay_live_proxy(symbol: Symbol, prices: List[float]) -> None:
                if not prices:
                    return
                try:
                    latest = float(self.Securities[symbol].Price)
                    if latest <= 0:
                        latest = float(self.Securities[symbol].Close)
                except Exception:
                    return
                if latest > 0:
                    prices[-1] = latest

            _overlay_live_proxy(self.spy, spy_prices)
            _overlay_live_proxy(self.rsp, rsp_prices)
            _overlay_live_proxy(self.hyg, hyg_prices)
            _overlay_live_proxy(self.ief, ief_prices)

        # V2.26: Pass VIX + SPY ADX to regime calculation
        # V3.7: Pass actual 52-week high for drawdown factor (CRITICAL FIX)
        spy_adx_val = self.spy_adx_daily.Current.Value if self.spy_adx_daily.IsReady else 25.0
        spy_52w_high_val = self.spy_52w_high.Current.Value if self.spy_52w_high.IsReady else 0.0
        calculate_fn = (
            self.regime_engine.calculate_readonly if read_only else self.regime_engine.calculate
        )
        return calculate_fn(
            spy_closes=spy_prices,
            rsp_closes=rsp_prices,
            hyg_closes=hyg_prices,
            ief_closes=ief_prices,
            spy_sma20=self.spy_sma20.Current.Value,
            spy_sma50=self.spy_sma50.Current.Value,
            spy_sma200=self.spy_sma200.Current.Value,
            vix_level=self._current_vix,
            spy_adx=spy_adx_val,
            spy_52w_high=spy_52w_high_val,
        )

    def _refresh_intraday_regime_score(self) -> None:
        """Phase A: refresh intraday macro regime snapshot for options gating."""
        if self.IsWarmingUp:
            return
        try:
            regime_state = self._calculate_regime(read_only=True)
            self._intraday_regime_score = float(regime_state.smoothed_score)
            self._intraday_regime_momentum_roc = float(
                getattr(regime_state, "momentum_roc", 0.0) or 0.0
            )
            self._intraday_regime_vix_5d_change = float(
                getattr(regime_state, "vix_5d_change", 0.0) or 0.0
            )
            self._intraday_regime_updated_at = self.Time
            self._regime_detector_sample_seq = (
                int(getattr(self, "_regime_detector_sample_seq", 0)) + 1
            )
            # Keep one refresh log per day to preserve RCA signal while reducing volume.
            refresh_day = self.Time.strftime("%Y-%m-%d")
            if getattr(self, "_last_regime_refresh_log_day", None) != refresh_day:
                self.Log(
                    f"REGIME_REFRESH_INTRADAY: Score={self._intraday_regime_score:.1f} | "
                    f"Time={self.Time.strftime('%H:%M')}"
                )
                self._last_regime_refresh_log_day = refresh_day
            self._record_regime_timeline_event(source="INTRADAY_REFRESH")
        except Exception as e:
            self.Log(f"REGIME_REFRESH_INTRADAY_ERROR: {e}")

    def _evaluate_base_regime_candidate(self, effective_score: float) -> str:
        """State-machine candidate for macro base regime with hysteresis thresholds."""
        bull_enter = float(getattr(config, "REGIME_BASE_BULL_ENTER", 57.0))
        bull_exit = float(getattr(config, "REGIME_BASE_BULL_EXIT", 53.0))
        bear_enter = float(getattr(config, "REGIME_BASE_BEAR_ENTER", 43.0))
        bear_exit = float(getattr(config, "REGIME_BASE_BEAR_EXIT", 47.0))
        current = str(getattr(self, "_regime_base_state", "NEUTRAL")).upper()

        if current == "BULLISH":
            if effective_score < bull_exit:
                return "BEARISH" if effective_score <= bear_enter else "NEUTRAL"
            return "BULLISH"
        if current == "BEARISH":
            if effective_score > bear_exit:
                return "BULLISH" if effective_score >= bull_enter else "NEUTRAL"
            return "BEARISH"
        if effective_score >= bull_enter:
            return "BULLISH"
        if effective_score <= bear_enter:
            return "BEARISH"
        return "NEUTRAL"

    def _advance_detector_state(
        self,
        current_state: str,
        candidate_state: str,
        candidate_streak: int,
        desired_state: str,
        dwell_required: int,
    ) -> Tuple[str, str, int]:
        """Advance state-machine with dwell bars."""
        desired = str(desired_state or "").upper() or current_state
        current = str(current_state or "").upper() or "NEUTRAL"
        candidate = str(candidate_state or current).upper()
        dwell = max(int(dwell_required), 1)

        if desired == current:
            return current, desired, 0
        if desired == candidate:
            streak = int(candidate_streak) + 1
        else:
            candidate = desired
            streak = 1
        if streak >= dwell:
            return desired, desired, 0
        return current, candidate, streak

    def _update_regime_detector_state(
        self,
        effective: float,
        detector_score: float,
        eod_delta: float,
        momentum_roc: float,
        vix_5d_change: float,
        sample_seq: int,
    ) -> Dict[str, Any]:
        """Compute transition raw signals and advance base/overlay detector state."""
        recovery_delta_min = float(getattr(config, "REGIME_TRANSITION_RECOVERY_DELTA_MIN", 2.0))
        recovery_detector_delta_min = float(
            getattr(config, "REGIME_TRANSITION_RECOVERY_DETECTOR_DELTA_MIN", 0.8)
        )
        recovery_eod_agreement_min = float(
            getattr(config, "REGIME_TRANSITION_RECOVERY_EOD_AGREEMENT_MIN", 0.15)
        )
        recovery_mom_min = float(getattr(config, "REGIME_TRANSITION_RECOVERY_MOMENTUM_MIN", 0.015))
        recovery_vix_5d_max = float(getattr(config, "REGIME_TRANSITION_RECOVERY_VIX_5D_MAX", 0.05))
        deterioration_delta_max = float(
            getattr(config, "REGIME_TRANSITION_DETERIORATION_DELTA_MAX", -2.0)
        )
        deterioration_detector_delta_max = float(
            getattr(config, "REGIME_TRANSITION_DETERIORATION_DETECTOR_DELTA_MAX", -0.8)
        )
        deterioration_eod_agreement_max = float(
            getattr(config, "REGIME_TRANSITION_DETERIORATION_EOD_AGREEMENT_MAX", -0.15)
        )
        deterioration_mom_max = float(
            getattr(config, "REGIME_TRANSITION_DETERIORATION_MOMENTUM_MAX", -0.015)
        )
        deterioration_vix_5d_min = float(
            getattr(config, "REGIME_TRANSITION_DETERIORATION_VIX_5D_MIN", 0.10)
        )
        deterioration_fast_eod_delta_max = float(
            getattr(config, "REGIME_TRANSITION_DETERIORATION_FAST_EOD_DELTA_MAX", -1.8)
        )
        deterioration_fast_mom_max = float(
            getattr(config, "REGIME_TRANSITION_DETERIORATION_FAST_MOMENTUM_MAX", -0.020)
        )
        deterioration_fast_vix_5d_min = float(
            getattr(config, "REGIME_TRANSITION_DETERIORATION_FAST_VIX_5D_MIN", 0.12)
        )
        deterioration_fast_detector_delta_max = float(
            getattr(config, "REGIME_TRANSITION_DETERIORATION_FAST_DETECTOR_DELTA_MAX", -0.05)
        )
        ambiguous_low = float(getattr(config, "REGIME_TRANSITION_AMBIGUOUS_LOW", 47.0))
        ambiguous_high = float(getattr(config, "REGIME_TRANSITION_AMBIGUOUS_HIGH", 55.0))
        ambiguous_delta_max = float(getattr(config, "REGIME_TRANSITION_AMBIGUOUS_DELTA_MAX", 1.5))
        ambiguous_detector_delta_max = float(
            getattr(config, "REGIME_TRANSITION_AMBIGUOUS_DETECTOR_DELTA_MAX", 0.8)
        )
        ambiguous_max_bars = int(getattr(config, "REGIME_TRANSITION_AMBIGUOUS_MAX_BARS", 6))

        now_key = f"SEQ:{int(sample_seq)}"
        is_new_sample = self._regime_detector_last_update_key != now_key
        if is_new_sample:
            prev_score = getattr(self, "_regime_detector_prev_score", None)
            if prev_score is None:
                detector_delta = 0.0
            else:
                detector_delta = float(detector_score - float(prev_score))
            self._regime_detector_prev_score = float(detector_score)
        else:
            detector_delta = float(self._regime_detector_last_raw.get("detector_delta", 0.0) or 0.0)

        recovery_by_detector = detector_delta >= recovery_detector_delta_min
        recovery_by_eod = (
            eod_delta >= recovery_delta_min and detector_delta >= recovery_eod_agreement_min
        )
        raw_recovery = (
            (recovery_by_detector or recovery_by_eod)
            and momentum_roc >= recovery_mom_min
            and vix_5d_change <= recovery_vix_5d_max
        )
        deterioration_by_detector = detector_delta <= deterioration_detector_delta_max
        deterioration_by_eod = (
            eod_delta <= deterioration_delta_max
            and detector_delta <= deterioration_eod_agreement_max
        )
        deterioration_by_fast = (
            eod_delta <= deterioration_fast_eod_delta_max
            and momentum_roc <= deterioration_fast_mom_max
            and vix_5d_change >= deterioration_fast_vix_5d_min
            and detector_delta <= deterioration_fast_detector_delta_max
        )
        raw_deterioration = (
            (deterioration_by_detector or deterioration_by_eod)
            and momentum_roc <= deterioration_mom_max
            and vix_5d_change >= deterioration_vix_5d_min
        ) or deterioration_by_fast
        ambiguous_by_detector = abs(detector_delta) <= ambiguous_detector_delta_max
        ambiguous_by_eod = abs(eod_delta) <= ambiguous_delta_max
        raw_ambiguous = (
            ambiguous_low <= detector_score <= ambiguous_high
            and ambiguous_by_detector
            and ambiguous_by_eod
            and not raw_recovery
            and not raw_deterioration
        )
        ambiguous_timed_out = False
        if is_new_sample:
            if raw_recovery:
                if recovery_by_detector and recovery_by_eod:
                    self._inc_transition_path_counter("RECOVERY_BOTH")
                elif recovery_by_detector:
                    self._inc_transition_path_counter("RECOVERY_DETECTOR")
                else:
                    self._inc_transition_path_counter("RECOVERY_EOD")
            elif raw_deterioration:
                if deterioration_by_fast and not (
                    deterioration_by_detector or deterioration_by_eod
                ):
                    self._inc_transition_path_counter("DETERIORATION_FAST")
                elif deterioration_by_detector and deterioration_by_eod:
                    self._inc_transition_path_counter("DETERIORATION_BOTH")
                elif deterioration_by_detector:
                    self._inc_transition_path_counter("DETERIORATION_DETECTOR")
                else:
                    self._inc_transition_path_counter("DETERIORATION_EOD")
            elif raw_ambiguous:
                self._inc_transition_path_counter("AMBIGUOUS_BOTH")
        overlay_candidate = "STABLE"
        if raw_recovery:
            overlay_candidate = "RECOVERY"
        elif raw_deterioration:
            overlay_candidate = "DETERIORATION"
        elif raw_ambiguous:
            overlay_candidate = "AMBIGUOUS"

        if is_new_sample:
            base_candidate = self._evaluate_base_regime_candidate(detector_score)
            prev_base_state = str(getattr(self, "_regime_base_state", "NEUTRAL")).upper()
            prev_overlay_state = str(getattr(self, "_regime_overlay_state", "STABLE")).upper()
            overlay_dwell = int(getattr(config, "REGIME_OVERLAY_STATE_DWELL_BARS", 2))
            if overlay_candidate == "RECOVERY":
                overlay_dwell = int(
                    getattr(config, "REGIME_OVERLAY_DWELL_RECOVERY_BARS", overlay_dwell)
                )
            elif overlay_candidate == "DETERIORATION":
                overlay_dwell = int(
                    getattr(config, "REGIME_OVERLAY_DWELL_DETERIORATION_BARS", overlay_dwell)
                )
            elif overlay_candidate == "AMBIGUOUS":
                overlay_dwell = int(
                    getattr(config, "REGIME_OVERLAY_DWELL_AMBIGUOUS_BARS", overlay_dwell)
                )
            if prev_overlay_state == "DETERIORATION" and overlay_candidate != "DETERIORATION":
                overlay_dwell = max(
                    overlay_dwell,
                    int(getattr(config, "REGIME_OVERLAY_EXIT_DETERIORATION_DWELL_BARS", 3)),
                )
            if bool(getattr(config, "REGIME_BASE_STATE_MACHINE_ENABLED", True)):
                (
                    self._regime_base_state,
                    self._regime_base_candidate_state,
                    self._regime_base_candidate_streak,
                ) = self._advance_detector_state(
                    current_state=self._regime_base_state,
                    candidate_state=self._regime_base_candidate_state,
                    candidate_streak=self._regime_base_candidate_streak,
                    desired_state=base_candidate,
                    dwell_required=int(getattr(config, "REGIME_BASE_STATE_DWELL_BARS", 2)),
                )
                (
                    self._regime_overlay_state,
                    self._regime_overlay_candidate_state,
                    self._regime_overlay_candidate_streak,
                ) = self._advance_detector_state(
                    current_state=self._regime_overlay_state,
                    candidate_state=self._regime_overlay_candidate_state,
                    candidate_streak=self._regime_overlay_candidate_streak,
                    desired_state=overlay_candidate,
                    dwell_required=overlay_dwell,
                )
            else:
                self._regime_base_state = base_candidate
                self._regime_overlay_state = overlay_candidate
                self._regime_base_candidate_state = base_candidate
                self._regime_overlay_candidate_state = overlay_candidate
                self._regime_base_candidate_streak = 0
                self._regime_overlay_candidate_streak = 0
            current_seq = int(sample_seq)
            if str(self._regime_base_state).upper() != prev_base_state:
                self._regime_base_state_enter_seq = current_seq
            elif int(getattr(self, "_regime_base_state_enter_seq", 0)) <= 0:
                self._regime_base_state_enter_seq = current_seq
            if str(self._regime_overlay_state).upper() != prev_overlay_state:
                self._regime_overlay_state_enter_seq = current_seq
            elif int(getattr(self, "_regime_overlay_state_enter_seq", 0)) <= 0:
                self._regime_overlay_state_enter_seq = current_seq
            if self._regime_overlay_state == "AMBIGUOUS":
                self._regime_overlay_ambiguous_bars = (
                    int(getattr(self, "_regime_overlay_ambiguous_bars", 0)) + 1
                )
            else:
                self._regime_overlay_ambiguous_bars = 0
            if (
                ambiguous_max_bars > 0
                and self._regime_overlay_state == "AMBIGUOUS"
                and self._regime_overlay_ambiguous_bars >= ambiguous_max_bars
            ):
                self._regime_overlay_state = "STABLE"
                self._regime_overlay_candidate_state = "STABLE"
                self._regime_overlay_candidate_streak = 0
                self._regime_overlay_ambiguous_bars = 0
                ambiguous_timed_out = True
                self._inc_transition_path_counter("AMBIGUOUS_TIMEOUT")
            self._regime_detector_last_update_key = now_key

        self._regime_detector_last_raw = {
            "raw_recovery": bool(raw_recovery),
            "raw_deterioration": bool(raw_deterioration),
            "raw_ambiguous": bool(raw_ambiguous),
            "ambiguous_timed_out": bool(ambiguous_timed_out),
            "overlay_candidate": overlay_candidate,
            "base_candidate": self._evaluate_base_regime_candidate(detector_score),
            "detector_score": float(detector_score),
            "detector_delta": float(detector_delta),
            "eod_delta": float(eod_delta),
            "recovery_by_detector": bool(recovery_by_detector),
            "recovery_by_eod": bool(recovery_by_eod),
            "deterioration_by_detector": bool(deterioration_by_detector),
            "deterioration_by_eod": bool(deterioration_by_eod),
            "deterioration_by_fast": bool(deterioration_by_fast),
            "recovery_eod_agreement_min": float(recovery_eod_agreement_min),
            "deterioration_eod_agreement_max": float(deterioration_eod_agreement_max),
            "ambiguous_by_detector": bool(ambiguous_by_detector),
            "ambiguous_by_eod": bool(ambiguous_by_eod),
            # Backward-compatible alias expected by existing guards/log readers.
            "delta": float(detector_delta),
            "sample_seq": int(sample_seq),
        }
        return dict(self._regime_detector_last_raw)

    def _get_regime_transition_context(self) -> Dict[str, Any]:
        """Build macro detector context with base regime + transition overlay state-machine."""
        effective = float(self._get_effective_regime_score_for_options())
        eod_score = float(getattr(self, "_last_regime_score", effective) or effective)
        intraday_score_raw = getattr(self, "_intraday_regime_score", None)
        intraday_score = (
            float(intraday_score_raw) if intraday_score_raw is not None else float(eod_score)
        )
        eod_delta = float(intraday_score - eod_score)
        detector_score = float(intraday_score if intraday_score_raw is not None else effective)
        momentum_roc = getattr(self, "_intraday_regime_momentum_roc", None)
        if momentum_roc is None:
            momentum_roc = getattr(self, "_last_regime_momentum_roc", 0.0)
        vix_5d_change = getattr(self, "_intraday_regime_vix_5d_change", None)
        if vix_5d_change is None:
            vix_5d_change = getattr(self, "_last_regime_vix_5d_change", 0.0)
        momentum_roc = float(momentum_roc or 0.0)
        vix_5d_change = float(vix_5d_change or 0.0)

        raw = self._update_regime_detector_state(
            effective=effective,
            detector_score=detector_score,
            eod_delta=eod_delta,
            momentum_roc=momentum_roc,
            vix_5d_change=vix_5d_change,
            sample_seq=int(getattr(self, "_regime_detector_sample_seq", 0)),
        )
        detector_delta = float(raw.get("detector_delta", raw.get("delta", 0.0)) or 0.0)
        transition_overlay = str(getattr(self, "_regime_overlay_state", "STABLE")).upper()
        strong_recovery = transition_overlay == "RECOVERY"
        strong_deterioration = transition_overlay == "DETERIORATION"
        ambiguous = transition_overlay == "AMBIGUOUS"
        sample_seq = int(raw.get("sample_seq", getattr(self, "_regime_detector_sample_seq", 0)))
        overlay_enter_seq = int(
            getattr(self, "_regime_overlay_state_enter_seq", sample_seq) or sample_seq
        )
        overlay_bars_since_flip = max(0, sample_seq - overlay_enter_seq)

        transition_score = effective
        if strong_recovery and intraday_score > effective:
            lift_max = float(getattr(config, "REGIME_TRANSITION_RECOVERY_SCORE_LIFT_MAX", 8.0))
            transition_score = min(intraday_score, effective + lift_max)

        return {
            "effective_score": float(effective),
            "detector_score": float(detector_score),
            "eod_score": float(eod_score),
            "intraday_score": float(intraday_score),
            "delta": float(detector_delta),
            "eod_delta": float(eod_delta),
            "momentum_roc": float(momentum_roc),
            "vix_5d_change": float(vix_5d_change),
            "base_regime": str(getattr(self, "_regime_base_state", "NEUTRAL")).upper(),
            "transition_overlay": transition_overlay,
            "strong_recovery": bool(strong_recovery),
            "strong_deterioration": bool(strong_deterioration),
            "ambiguous": bool(ambiguous),
            "overlay_bars_since_flip": int(overlay_bars_since_flip),
            "transition_score": float(transition_score),
            "raw_recovery": bool(raw.get("raw_recovery", False)),
            "raw_deterioration": bool(raw.get("raw_deterioration", False)),
            "raw_ambiguous": bool(raw.get("raw_ambiguous", False)),
            "ambiguous_timed_out": bool(raw.get("ambiguous_timed_out", False)),
            "recovery_by_detector": bool(raw.get("recovery_by_detector", False)),
            "recovery_by_eod": bool(raw.get("recovery_by_eod", False)),
            "deterioration_by_detector": bool(raw.get("deterioration_by_detector", False)),
            "deterioration_by_eod": bool(raw.get("deterioration_by_eod", False)),
            "deterioration_by_fast": bool(raw.get("deterioration_by_fast", False)),
            "overlay_candidate": str(raw.get("overlay_candidate", "STABLE")).upper(),
            "base_candidate": str(raw.get("base_candidate", "NEUTRAL")).upper(),
            "sample_seq": sample_seq,
        }

    def _record_transition_derisk_action(self, action: str, engine: str) -> None:
        """Track transition-time open-position de-risk actions for RCA summaries."""
        action_key = str(action or "").strip().lower()
        if action_key not in {"de_risk_on_deterioration", "de_risk_on_recovery"}:
            return
        self._diag_transition_derisk_counts[action_key] = (
            int(self._diag_transition_derisk_counts.get(action_key, 0)) + 1
        )
        engine_bucket = str(engine or "OTHER").upper()
        if engine_bucket not in self._diag_transition_derisk_counts_by_engine:
            engine_bucket = "OTHER"
        store = self._diag_transition_derisk_counts_by_engine.setdefault(
            engine_bucket,
            {"de_risk_on_deterioration": 0, "de_risk_on_recovery": 0},
        )
        store[action_key] = int(store.get(action_key, 0)) + 1

    def _get_transition_execution_context(self) -> Dict[str, Any]:
        """Return one transition snapshot per minute/sample for all options execution paths."""
        minute_key = self.Time.strftime("%Y-%m-%d %H:%M")
        sample_seq = int(getattr(self, "_regime_detector_sample_seq", 0))
        cached = (
            dict(self._transition_execution_context)
            if isinstance(self._transition_execution_context, dict)
            else None
        )
        if (
            cached is not None
            and self._transition_execution_context_minute_key == minute_key
            and self._transition_execution_context_sample_seq == sample_seq
        ):
            if hasattr(self, "options_engine") and self.options_engine is not None:
                try:
                    self.options_engine.set_transition_context_snapshot(cached)
                except Exception:
                    pass
            return cached

        ctx = self._get_regime_transition_context()
        if not isinstance(ctx, dict):
            ctx = {}
        self._transition_execution_context = dict(ctx)
        self._transition_execution_context_minute_key = minute_key
        self._transition_execution_context_sample_seq = int(
            ctx.get("sample_seq", sample_seq) or sample_seq
        )
        if hasattr(self, "options_engine") and self.options_engine is not None:
            try:
                self.options_engine.set_transition_context_snapshot(ctx)
            except Exception:
                pass
        return dict(ctx)

    def _apply_transition_handoff_open_position_derisk(self, data: Slice) -> bool:
        """
        Transition handoff: de-risk existing wrong-way positions early after overlay flips.

        Returns:
            True when de-risk exits were queued this cycle.
        """
        if not bool(getattr(config, "TRANSITION_HANDOFF_OPEN_DERISK_ENABLED", True)):
            return False
        if not self._is_primary_market_open():
            return False
        if not hasattr(self, "options_engine") or self.options_engine is None:
            return False

        ctx = self._get_transition_execution_context()
        overlay = str(ctx.get("transition_overlay", "") or "").upper()
        if overlay not in {"DETERIORATION", "RECOVERY"}:
            return False

        bars_since_flip = int(ctx.get("overlay_bars_since_flip", 999) or 999)
        intraday_derisk_bars = max(
            1, int(getattr(config, "TRANSITION_HANDOFF_OPEN_DERISK_BARS", 4))
        )
        vass_derisk_bars = max(
            1,
            int(getattr(config, "VASS_TRANSITION_OPEN_DERISK_BARS", intraday_derisk_bars)),
        )
        if bars_since_flip >= max(intraday_derisk_bars, vass_derisk_bars):
            return False

        action_key = (
            "de_risk_on_deterioration" if overlay == "DETERIORATION" else "de_risk_on_recovery"
        )
        queued_any = False

        # De-risk open VASS spreads first.
        for spread in list(self.options_engine.get_spread_positions() or []):
            spread_type = str(getattr(spread, "spread_type", "") or "").upper()
            is_bullish_spread = spread_type in {"BULL_CALL", "BULL_CALL_DEBIT", "BULL_PUT_CREDIT"}
            is_bearish_spread = spread_type in {"BEAR_PUT", "BEAR_PUT_DEBIT", "BEAR_CALL_CREDIT"}
            wrong_way = (overlay == "DETERIORATION" and is_bullish_spread) or (
                overlay == "RECOVERY" and is_bearish_spread
            )
            if not wrong_way:
                continue
            if bars_since_flip >= vass_derisk_bars:
                continue

            long_symbol = self._normalize_symbol_str(spread.long_leg.symbol)
            short_symbol = self._normalize_symbol_str(spread.short_leg.symbol)
            if self._has_open_order_for_symbol(long_symbol) or self._has_open_order_for_symbol(
                short_symbol
            ):
                continue

            reason = f"SPREAD_EXIT: TRANSITION_DERISK_{overlay}"
            signal = TargetWeight(
                symbol=long_symbol,
                target_weight=0.0,
                source="OPT",
                urgency=Urgency.IMMEDIATE,
                reason=reason,
                requested_quantity=spread.num_spreads,
                metadata={
                    "spread_close_short": True,
                    "spread_short_leg_symbol": short_symbol,
                    "spread_short_leg_quantity": spread.num_spreads,
                    "spread_key": self._build_spread_runtime_key(spread),
                    "exit_type": f"TRANSITION_DERISK_{overlay}",
                },
            )
            self._normalize_spread_close_quantities(signal)
            self.portfolio_router.receive_signal(signal)
            self._record_spread_exit_reason(self._build_spread_runtime_key(spread), reason)
            self._diag_spread_exit_signal_count += 1
            self._diag_spread_exit_submit_count += 1
            self._record_transition_derisk_action(action_key, "VASS")
            self.Log(
                f"TRANSITION_OPEN_DERISK: VASS queued | Overlay={overlay} | "
                f"Type={spread_type} | BarsSinceFlip={bars_since_flip}/{vass_derisk_bars}"
            )
            queued_any = True

        # De-risk open ITM/MICRO wrong-way single-leg options.
        for intraday_pos in list(self.options_engine.get_intraday_positions() or []):
            if intraday_pos is None or getattr(intraday_pos, "contract", None) is None:
                continue
            symbol_key = self._normalize_symbol_str(intraday_pos.contract.symbol)
            if not symbol_key:
                continue
            if self._has_open_non_oco_order_for_symbol(symbol_key):
                continue
            if self.options_engine.has_pending_intraday_exit(symbol=symbol_key):
                continue

            strategy_name = str(getattr(intraday_pos, "entry_strategy", "") or "").upper()
            if strategy_name == IntradayStrategy.PROTECTIVE_PUTS.value:
                continue

            direction = getattr(intraday_pos.contract, "direction", None)
            is_call = direction == OptionDirection.CALL or (
                direction is None and "C" in symbol_key and "P" not in symbol_key
            )
            is_put = direction == OptionDirection.PUT or (
                direction is None and "P" in symbol_key and "C" not in symbol_key
            )
            wrong_way = (overlay == "DETERIORATION" and is_call) or (
                overlay == "RECOVERY" and is_put
            )
            if not wrong_way:
                continue
            if bars_since_flip >= intraday_derisk_bars:
                continue
            if not self.options_engine.mark_pending_intraday_exit(symbol_key):
                continue

            live_qty = abs(self._get_option_holding_quantity(symbol_key))
            if live_qty <= 0:
                live_qty = abs(int(getattr(intraday_pos, "num_contracts", 0) or 0))
            if live_qty <= 0:
                continue

            lane = self.options_engine._find_intraday_lane_by_symbol(symbol_key)
            engine_bucket = "ITM" if str(lane or "").upper() == "ITM" else "MICRO"
            self.portfolio_router.receive_signal(
                TargetWeight(
                    symbol=symbol_key,
                    target_weight=0.0,
                    source="OPT_INTRADAY",
                    urgency=Urgency.IMMEDIATE,
                    reason=f"TRANSITION_DERISK_{overlay}",
                    requested_quantity=live_qty,
                    metadata={"intraday_strategy": str(strategy_name or "UNKNOWN")},
                )
            )
            self._record_transition_derisk_action(action_key, engine_bucket)
            self.Log(
                f"TRANSITION_OPEN_DERISK: {engine_bucket} queued | Overlay={overlay} | "
                f"Symbol={symbol_key[-20:]} | BarsSinceFlip={bars_since_flip}/{intraday_derisk_bars}"
            )
            queued_any = True

        if queued_any:
            self._process_immediate_signals()
        return queued_any

    def _record_regime_decision_event(
        self,
        engine: str,
        engine_decision: str,
        strategy_attempted: str = "",
        gate_name: str = "",
        threshold_snapshot: Optional[Any] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Append structured regime decision telemetry row for post-run artifact analysis."""
        if not bool(getattr(config, "REGIME_OBSERVABILITY_ENABLED", True)):
            return
        max_rows = int(getattr(config, "REGIME_OBSERVABILITY_MAX_ROWS", 50000))
        ctx = context if isinstance(context, dict) else self._get_regime_transition_context()
        if isinstance(threshold_snapshot, dict):
            threshold_payload = json.dumps(
                threshold_snapshot, sort_keys=True, separators=(",", ":")
            )
        elif threshold_snapshot is None:
            threshold_payload = ""
        else:
            threshold_payload = str(threshold_snapshot)
        self._append_observability_record(
            records=self._regime_decision_records,
            overflow_attr="_regime_decision_overflow_logged",
            max_rows=max_rows,
            overflow_log_prefix="REGIME_OBSERVABILITY",
            row={
                "time": self.Time.strftime("%Y-%m-%d %H:%M:%S"),
                "eod_score": f"{float(ctx.get('eod_score', 0.0)):.2f}",
                "intraday_score": f"{float(ctx.get('intraday_score', 0.0)):.2f}",
                "delta": f"{float(ctx.get('delta', 0.0)):+.2f}",
                "eod_delta": f"{float(ctx.get('eod_delta', 0.0)):+.2f}",
                "momentum_roc": f"{float(ctx.get('momentum_roc', 0.0)):+.5f}",
                "vix_5d_change": f"{float(ctx.get('vix_5d_change', 0.0)):+.5f}",
                "base_regime": str(ctx.get("base_regime", "NEUTRAL")),
                "transition_overlay": str(ctx.get("transition_overlay", "STABLE")),
                "regime_state": f"{str(ctx.get('base_regime', 'NEUTRAL'))}|{str(ctx.get('transition_overlay', 'STABLE'))}",
                "engine": str(engine or "").upper(),
                "engine_decision": str(engine_decision or "").upper(),
                "strategy_attempted": str(strategy_attempted or ""),
                "gate_name": str(gate_name or ""),
                "threshold_snapshot": threshold_payload,
            },
        )

    def _flush_regime_decision_artifact(self) -> None:
        """Persist structured regime decision rows to ObjectStore CSV artifact."""
        if not bool(getattr(config, "REGIME_OBSERVABILITY_ENABLED", True)):
            return
        if not bool(getattr(config, "REGIME_OBSERVABILITY_OBJECTSTORE_ENABLED", True)):
            return
        if not self._regime_decision_records:
            return
        fields = [
            "time",
            "eod_score",
            "intraday_score",
            "delta",
            "eod_delta",
            "momentum_roc",
            "vix_5d_change",
            "base_regime",
            "transition_overlay",
            "regime_state",
            "engine",
            "engine_decision",
            "strategy_attempted",
            "gate_name",
            "threshold_snapshot",
        ]
        self._save_observability_csv_artifact(
            key=self._regime_observability_key,
            fields=fields,
            rows=self._regime_decision_records,
            error_prefix="REGIME_OBSERVABILITY_SAVE_ERROR",
        )

    def _record_regime_timeline_event(
        self,
        source: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Capture periodic regime detector timeline snapshots."""
        if not bool(getattr(config, "REGIME_TIMELINE_OBSERVABILITY_ENABLED", True)):
            return
        max_rows = int(getattr(config, "REGIME_TIMELINE_OBSERVABILITY_MAX_ROWS", 12000))
        ctx = context if isinstance(context, dict) else self._get_regime_transition_context()
        self._append_observability_record(
            records=self._regime_timeline_records,
            overflow_attr="_regime_timeline_overflow_logged",
            max_rows=max_rows,
            overflow_log_prefix="REGIME_TIMELINE_OBSERVABILITY",
            row={
                "time": self.Time.strftime("%Y-%m-%d %H:%M:%S"),
                "source": str(source or ""),
                "effective_score": f"{float(ctx.get('effective_score', 0.0)):.2f}",
                "detector_score": f"{float(ctx.get('detector_score', ctx.get('intraday_score', 0.0))):.2f}",
                "eod_score": f"{float(ctx.get('eod_score', 0.0)):.2f}",
                "intraday_score": f"{float(ctx.get('intraday_score', 0.0)):.2f}",
                "delta": f"{float(ctx.get('delta', 0.0)):+.2f}",
                "eod_delta": f"{float(ctx.get('eod_delta', 0.0)):+.2f}",
                "momentum_roc": f"{float(ctx.get('momentum_roc', 0.0)):+.5f}",
                "vix_5d_change": f"{float(ctx.get('vix_5d_change', 0.0)):+.5f}",
                "base_regime": str(ctx.get("base_regime", "NEUTRAL")),
                "transition_overlay": str(ctx.get("transition_overlay", "STABLE")),
                "raw_recovery": "1" if bool(ctx.get("raw_recovery", False)) else "0",
                "raw_deterioration": "1" if bool(ctx.get("raw_deterioration", False)) else "0",
                "raw_ambiguous": "1" if bool(ctx.get("raw_ambiguous", False)) else "0",
                "deterioration_by_fast": "1"
                if bool(ctx.get("deterioration_by_fast", False))
                else "0",
                "strong_recovery": "1" if bool(ctx.get("strong_recovery", False)) else "0",
                "strong_deterioration": "1"
                if bool(ctx.get("strong_deterioration", False))
                else "0",
                "ambiguous": "1" if bool(ctx.get("ambiguous", False)) else "0",
                "base_candidate": str(ctx.get("base_candidate", "NEUTRAL")),
                "overlay_candidate": str(ctx.get("overlay_candidate", "STABLE")),
                "overlay_bars_since_flip": str(int(ctx.get("overlay_bars_since_flip", 0))),
                "sample_seq": str(int(ctx.get("sample_seq", 0))),
                "transition_score": f"{float(ctx.get('transition_score', ctx.get('effective_score', 0.0))):.2f}",
            },
        )

    def _save_observability_csv_artifact(
        self,
        key: str,
        fields: List[str],
        rows: List[Dict[str, Any]],
        error_prefix: str,
    ) -> None:
        """Common CSV artifact serializer for observability channels."""
        if not key or not rows:
            return
        retries = max(1, int(getattr(config, "OBSERVABILITY_OBJECTSTORE_SAVE_RETRIES", 2)))

        def _render_csv(payload_rows: List[Dict[str, Any]]) -> str:
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=fields)
            writer.writeheader()
            writer.writerows(payload_rows)
            return output.getvalue()

        def _save_text(target_key: str, payload: str) -> bool:
            for attempt in range(1, retries + 1):
                try:
                    save_result = self.ObjectStore.Save(target_key, payload)
                    if save_result is False:
                        raise RuntimeError("ObjectStore.Save returned False")
                    return True
                except Exception as e:
                    if attempt >= retries:
                        self.Log(f"{error_prefix}: key={target_key} | attempt={attempt} | {e}")
                        return False
            return False

        def _should_fallback_to_logs() -> bool:
            if not bool(getattr(config, "OBSERVABILITY_LOG_FALLBACK_ENABLED", False)):
                return False
            key_norm = str(key or "")
            if key_norm == str(getattr(self, "_regime_observability_key", "")):
                return bool(getattr(config, "REGIME_OBSERVABILITY_LOG_FALLBACK_ENABLED", True))
            if key_norm == str(getattr(self, "_regime_timeline_observability_key", "")):
                return bool(getattr(config, "REGIME_TIMELINE_LOG_FALLBACK_ENABLED", True))
            if key_norm == str(getattr(self, "_signal_lifecycle_observability_key", "")):
                return bool(getattr(config, "SIGNAL_LIFECYCLE_LOG_FALLBACK_ENABLED", False))
            if key_norm == str(getattr(self, "_router_rejection_observability_key", "")):
                return bool(getattr(config, "ROUTER_REJECTION_LOG_FALLBACK_ENABLED", False))
            if key_norm == str(getattr(self, "_order_lifecycle_observability_key", "")):
                return bool(getattr(config, "ORDER_LIFECYCLE_LOG_FALLBACK_ENABLED", False))
            return False

        def _emit_log_fallback(payload_csv: str) -> None:
            if not payload_csv or not _should_fallback_to_logs():
                return
            signature = f"{len(rows)}|{len(payload_csv)}"
            last_signature = self._observability_log_fallback_signature_by_key.get(key)
            if signature == last_signature:
                return
            try:
                compressed = gzip.compress(payload_csv.encode("utf-8"))
                encoded = b64encode(compressed).decode("ascii")
            except Exception as e:
                self.Log(f"OBS_FALLBACK_ENCODE_ERROR: Key={key} | {e}")
                return

            chunk_size = max(
                512,
                int(getattr(config, "OBSERVABILITY_LOG_FALLBACK_CHUNK_SIZE", 3400)),
            )
            total_parts = (len(encoded) + chunk_size - 1) // chunk_size
            self.Log(
                f"OBS_FALLBACK_BEGIN: Key={key} | Rows={len(rows)} | "
                f"Bytes={len(payload_csv)} | Parts={total_parts} | Encoding=gzip+base64"
            )
            for idx in range(total_parts):
                start = idx * chunk_size
                end = min((idx + 1) * chunk_size, len(encoded))
                self.Log(
                    f"OBS_FALLBACK_PART: Key={key} | Part={idx + 1}/{total_parts} | Data={encoded[start:end]}"
                )
            self.Log(f"OBS_FALLBACK_END: Key={key} | Rows={len(rows)} | Parts={total_parts}")
            self._observability_log_fallback_signature_by_key[key] = signature

        fallback_csv: Optional[str] = None
        shard_enabled = bool(getattr(config, "OBSERVABILITY_OBJECTSTORE_SHARD_ENABLED", True))
        shard_max_rows = int(getattr(config, "OBSERVABILITY_OBJECTSTORE_SHARD_MAX_ROWS", 12000))
        max_shards = max(1, int(getattr(config, "OBSERVABILITY_OBJECTSTORE_MAX_SHARDS", 32)))
        if not shard_enabled or shard_max_rows <= 0 or len(rows) <= shard_max_rows:
            fallback_csv = _render_csv(rows)
            saved = _save_text(key, fallback_csv)
            if not saved:
                _emit_log_fallback(fallback_csv)
            return

        shard_total = (len(rows) + shard_max_rows - 1) // shard_max_rows
        shard_total = min(shard_total, max_shards)
        if shard_total <= 1:
            fallback_csv = _render_csv(rows)
            saved = _save_text(key, fallback_csv)
            if not saved:
                _emit_log_fallback(fallback_csv)
            return

        adjusted_rows_per_shard = (len(rows) + shard_total - 1) // shard_total
        key_root = key[:-4] if key.endswith(".csv") else key
        manifest_key = f"{key_root}__manifest.json"
        wrote_all = True
        for shard_idx in range(shard_total):
            start = shard_idx * adjusted_rows_per_shard
            end = min((shard_idx + 1) * adjusted_rows_per_shard, len(rows))
            shard_rows = rows[start:end]
            if not shard_rows:
                continue
            part_csv = _render_csv(shard_rows)
            part_key = f"{key_root}__part{shard_idx + 1:03d}.csv"
            if not _save_text(part_key, part_csv):
                wrote_all = False
        if wrote_all:
            manifest = {
                "base_key": key,
                "parts": shard_total,
                "rows": len(rows),
                "fields": fields,
                "timestamp": self.Time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            if not _save_text(manifest_key, json.dumps(manifest, separators=(",", ":"))):
                wrote_all = False
        if not wrote_all:
            if fallback_csv is None:
                fallback_csv = _render_csv(rows)
            _emit_log_fallback(fallback_csv)

    def _flush_signal_lifecycle_artifact(self) -> None:
        if not bool(getattr(config, "SIGNAL_LIFECYCLE_OBSERVABILITY_ENABLED", True)):
            return
        if not bool(getattr(config, "SIGNAL_LIFECYCLE_OBJECTSTORE_ENABLED", True)):
            return
        if not self._signal_lifecycle_records:
            return
        self._save_observability_csv_artifact(
            key=self._signal_lifecycle_observability_key,
            fields=[
                "time",
                "engine",
                "event",
                "signal_id",
                "trace_id",
                "direction",
                "strategy",
                "code",
                "gate_name",
                "reason",
                "contract_symbol",
            ],
            rows=self._signal_lifecycle_records,
            error_prefix="SIGNAL_LIFECYCLE_SAVE_ERROR",
        )

    def _flush_router_rejection_artifact(self) -> None:
        if not bool(getattr(config, "ROUTER_REJECTION_OBSERVABILITY_ENABLED", True)):
            return
        if not bool(getattr(config, "ROUTER_REJECTION_OBJECTSTORE_ENABLED", True)):
            return
        if not self._router_rejection_records:
            return
        self._save_observability_csv_artifact(
            key=self._router_rejection_observability_key,
            fields=[
                "time",
                "stage",
                "code",
                "symbol",
                "source_tag",
                "trace_id",
                "detail",
                "engine",
            ],
            rows=self._router_rejection_records,
            error_prefix="ROUTER_REJECTION_SAVE_ERROR",
        )

    def _flush_order_lifecycle_artifact(self) -> None:
        if not bool(getattr(config, "ORDER_LIFECYCLE_OBSERVABILITY_ENABLED", True)):
            return
        if not bool(getattr(config, "ORDER_LIFECYCLE_OBJECTSTORE_ENABLED", True)):
            return
        if not self._order_lifecycle_records:
            return
        self._save_observability_csv_artifact(
            key=self._order_lifecycle_observability_key,
            fields=[
                "time",
                "status",
                "order_id",
                "symbol",
                "quantity",
                "fill_price",
                "order_type",
                "order_tag",
                "trace_id",
                "message",
                "source",
            ],
            rows=self._order_lifecycle_records,
            error_prefix="ORDER_LIFECYCLE_SAVE_ERROR",
        )

    def _flush_regime_timeline_artifact(self) -> None:
        if not bool(getattr(config, "REGIME_TIMELINE_OBSERVABILITY_ENABLED", True)):
            return
        if not bool(getattr(config, "REGIME_TIMELINE_OBJECTSTORE_ENABLED", True)):
            return
        if not self._regime_timeline_records:
            return
        self._save_observability_csv_artifact(
            key=self._regime_timeline_observability_key,
            fields=[
                "time",
                "source",
                "effective_score",
                "detector_score",
                "eod_score",
                "intraday_score",
                "delta",
                "eod_delta",
                "momentum_roc",
                "vix_5d_change",
                "base_regime",
                "transition_overlay",
                "raw_recovery",
                "raw_deterioration",
                "raw_ambiguous",
                "deterioration_by_fast",
                "strong_recovery",
                "strong_deterioration",
                "ambiguous",
                "base_candidate",
                "overlay_candidate",
                "overlay_bars_since_flip",
                "sample_seq",
                "transition_score",
            ],
            rows=self._regime_timeline_records,
            error_prefix="REGIME_TIMELINE_SAVE_ERROR",
        )

    def _get_effective_regime_score_for_options(self) -> float:
        """
        Phase A: defensive options regime score.
        Uses worse-of (lower) between previous EOD and intraday refreshed score.
        """
        eod_score = float(self.regime_engine.get_previous_score())
        intraday_score = self._intraday_regime_score
        if intraday_score is None:
            return eod_score
        effective = min(eod_score, float(intraday_score))
        # Phase B: log meaningful intraday downside adjustments for traceability.
        # Throttled to avoid log flooding.
        if eod_score - effective >= 2.0:
            should_log = self._last_regime_effective_log_at is None or (
                (self.Time - self._last_regime_effective_log_at).total_seconds() >= 1800
            )
            if should_log:
                self.Log(
                    f"REGIME_EFFECTIVE_OPTIONS: EOD={eod_score:.1f} | "
                    f"Intraday={float(intraday_score):.1f} | Effective={effective:.1f}"
                )
                self._last_regime_effective_log_at = self.Time
        return effective

    def _get_decision_regime_score_for_options(self) -> float:
        """
        Decision score for options entries.

        Keep `_get_effective_regime_score_for_options()` for defensive/risk gating,
        but route entry-direction decisions through transition-aware score.
        """
        base = float(self._get_effective_regime_score_for_options())
        try:
            ctx = self._get_regime_transition_context()
        except Exception:
            return base
        if not isinstance(ctx, dict):
            return base
        try:
            return float(
                ctx.get(
                    "transition_score",
                    ctx.get("detector_score", ctx.get("effective_score", base)),
                )
                or base
            )
        except Exception:
            return base

    def _log_daily_summary(self) -> None:
        # Keep markers in main.py for minified validator checks.
        _ = (
            "ORDER_LIFECYCLE_CAP_HIT",
            "MICRO_DTE_DIAG_SUMMARY",
            "OPTIONS_DIAG_SUMMARY",
        )
        transition_keys = [
            "RECOVERY_DETECTOR",
            "RECOVERY_EOD",
            "RECOVERY_BOTH",
            "DETERIORATION_DETECTOR",
            "DETERIORATION_EOD",
            "DETERIORATION_BOTH",
            "AMBIGUOUS_BOTH",
            "AMBIGUOUS_TIMEOUT",
        ]
        transition_summary = " | ".join(
            f"{key}={int(self._diag_transition_path_counts.get(key, 0))}" for key in transition_keys
        )
        self.Log(f"REGIME_TRANSITION_PATH_SUMMARY: {transition_summary}")
        log_daily_summary(self)

    def _get_intraday_lane_cooldown_until(self, lane: str):
        lane_key = str(lane or "").upper()
        bucket = getattr(self, "_options_intraday_cooldown_until_by_lane", None)
        if isinstance(bucket, dict):
            return bucket.get(lane_key)
        return self._options_intraday_cooldown_until

    def _set_intraday_lane_cooldown(self, lane: str, until_dt) -> None:
        lane_key = str(lane or "").upper()
        if lane_key not in ("MICRO", "ITM"):
            lane_key = "MICRO"
        if not isinstance(getattr(self, "_options_intraday_cooldown_until_by_lane", None), dict):
            self._options_intraday_cooldown_until_by_lane = {"MICRO": None, "ITM": None}
        self._options_intraday_cooldown_until_by_lane[lane_key] = until_dt
        active = [
            dt for dt in self._options_intraday_cooldown_until_by_lane.values() if dt is not None
        ]
        self._options_intraday_cooldown_until = max(active) if active else None

    def _handle_order_rejection(self, symbol: str, order_event) -> None:
        """
        V2.20: Event-driven state recovery on order rejection/cancellation.

        Mirrors _on_fill() pattern — routes rejection events to the correct
        engine using symbol-based matching to clear pending locks ("zombie states").

        Called from OnOrderEvent for both OrderStatus.Invalid and OrderStatus.Canceled,
        AFTER existing specific handlers (margin CB, orphan legs, exit retry).

        Args:
            symbol: String symbol of the rejected/canceled order.
            order_event: Original OrderEvent from broker.
        """
        # Route 1: Trend symbols - V6.11: Updated to include UGL, UCO
        if symbol in config.TREND_SYMBOLS:
            # Trend MOO recovery — clear stuck pending slot
            if self.trend_engine.is_pending_moo(symbol):
                self.trend_engine.cancel_pending_moo(symbol)
                # Cooldown: skip until next EOD cycle (trend signals only fire at 15:45)
                self._trend_rejection_cooldown_until = self.Time + timedelta(hours=18)
                self.Log(
                    f"TREND_RECOVERY: {symbol} rejected | Pending MOO cleared | "
                    f"Cooldown until next EOD"
                )
            # Cold start warm entry recovery (both checks run, not elif)
            if (
                self.cold_start_engine.is_cold_start_active()
                and self.cold_start_engine.has_warm_entry_executed()
                and self.cold_start_engine.get_warm_entry_symbol() == symbol
            ):
                self.cold_start_engine.cancel_warm_entry()

        # Route 2: MR symbols - V6.11: Updated to include SPXL
        elif symbol in config.MR_SYMBOLS:
            self.mr_engine.cancel_pending_entry()
            # Cooldown: 15 minutes before MR can retry
            self._mr_rejection_cooldown_until = self.Time + timedelta(minutes=15)
            self.Log(
                f"MR_RECOVERY: {symbol} rejected | Pending state reset | "
                f"Cooldown 15min until {self._mr_rejection_cooldown_until}"
            )

        # Route 3: QQQ options
        elif "QQQ" in symbol and ("C" in symbol or "P" in symbol):
            symbol_norm = self._normalize_symbol_str(symbol)
            if self.options_engine.cancel_pending_intraday_exit(symbol_norm):
                self._clear_intraday_close_guard(symbol_norm)
                self.Log(
                    f"OPT_MICRO_EXIT_RECOVERY: Close rejected/canceled | "
                    f"Symbol={symbol_norm} | Exit lock cleared"
                )
            elif symbol_norm in self._intraday_close_in_progress_symbols:
                # Clear stale in-progress close guard even when options-engine lock is absent.
                # Prevents sticky EOD sweep state that blocks OCO recovery/retries.
                self._clear_intraday_close_guard(symbol_norm)
                self.Log(
                    f"OPT_MICRO_EXIT_RECOVERY: Cleared stale close guard | Symbol={symbol_norm}"
                )

            # Check pending state to determine sub-mode (most specific first)
            spread_rejection_handled = False
            if self.options_engine.has_pending_spread_entry():
                pending_long, pending_short = self.options_engine.get_pending_spread_legs()
                pending_symbols = set()
                if pending_long is not None:
                    pending_symbols.add(self._normalize_symbol_str(pending_long.symbol))
                if pending_short is not None:
                    pending_symbols.add(self._normalize_symbol_str(pending_short.symbol))
                if symbol_norm in pending_symbols:
                    # V2.21: Parse broker margin for adaptive retry sizing
                    self._parse_and_store_rejection_margin(order_event)
                    self.options_engine.cancel_pending_spread_entry()
                    if self.portfolio_router:
                        self.portfolio_router.unregister_spread_margin_by_legs(
                            str(pending_long.symbol) if pending_long is not None else "",
                            str(pending_short.symbol) if pending_short is not None else None,
                        )
                    # Cooldown: 30 minutes before spread can retry
                    self._options_spread_cooldown_until = self.Time + timedelta(minutes=30)
                    # V3.0 P0-B: Clear main.py spread tracking state on rejection
                    if self._spread_fill_tracker is not None:
                        self.Log("REJECTION_CLEANUP: Clearing spread fill tracker")
                        self._spread_fill_tracker = None
                    if self._pending_spread_orders:
                        self.Log(
                            f"REJECTION_CLEANUP: Clearing "
                            f"{len(self._pending_spread_orders)} pending spread orders"
                        )
                        self._pending_spread_orders.clear()
                        self._pending_spread_orders_reverse.clear()
                    self.Log(
                        f"OPT_MACRO_RECOVERY: Spread rejected | Pending + margin cleared | "
                        f"Cooldown 30min until {self._options_spread_cooldown_until}"
                    )
                    spread_rejection_handled = True
                else:
                    throttle_key = (
                        "SPREAD_REJECT_UNMATCHED|"
                        f"{symbol_norm}|{','.join(sorted(pending_symbols)) or 'NONE'}"
                    )
                    if self._should_log_vass_rejection(throttle_key):
                        self.Log(
                            f"OPT_MACRO_RECOVERY: Ignored unmatched spread rejection | "
                            f"Canceled={symbol_norm} | Pending={','.join(sorted(pending_symbols)) or 'NONE'}"
                        )
            if (not spread_rejection_handled) and self.options_engine.has_pending_intraday_entry():
                pending_symbol = self.options_engine.get_pending_entry_contract_symbol()
                pending_lane = self.options_engine.get_pending_intraday_entry_lane(symbol_norm)
                if pending_lane is None:
                    self._diag_micro_pending_cancel_ignored_count += 1
                    self.Log(
                        f"OPT_INTRADAY_RECOVERY: Ignored unmatched cancel | "
                        f"Canceled={symbol_norm} Pending={pending_symbol or 'UNKNOWN'}"
                    )
                else:
                    cleared_lane = self.options_engine.cancel_pending_intraday_entry(
                        engine=pending_lane,
                        symbol=symbol_norm,
                    )
                    lane = str(cleared_lane or pending_lane or "MICRO").upper()
                    # Cooldown: 15 minutes before the rejected lane can retry.
                    lane_cooldown_until = self.Time + timedelta(minutes=15)
                    self._set_intraday_lane_cooldown(lane, lane_cooldown_until)
                    self.Log(
                        f"OPT_INTRADAY_RECOVERY: Intraday rejected | Lane={lane} | "
                        f"Pending + counter cleared | Cooldown 15min until {lane_cooldown_until}"
                    )
            elif (not spread_rejection_handled) and self.options_engine.has_pending_swing_entry():
                self.options_engine.cancel_pending_swing_entry()
                # Cooldown: 30 minutes before swing can retry
                self._options_swing_cooldown_until = self.Time + timedelta(minutes=30)
                self.Log(
                    f"OPT_SWING_RECOVERY: Swing rejected | Pending cleared | "
                    f"Cooldown 30min until {self._options_swing_cooldown_until}"
                )

    def _parse_and_store_rejection_margin(self, order_event) -> None:
        """
        V2.21: Parse broker Free Margin from rejection message for adaptive retry.

        Broker message format:
        "Insufficient buying power... Free Margin: 48927, Maintenance Margin Delta: 56172"

        Stores safety_factor * Free Margin as options-engine rejection margin cap.
        """
        import re

        try:
            msg = str(order_event.Message) if hasattr(order_event, "Message") else ""
            patterns = [
                r"Free Margin:\s*\$?([0-9][0-9,]*\.?[0-9]*)",
                r"Available(?:\s+Margin)?[:=]\s*\$?([0-9][0-9,]*\.?[0-9]*)",
                r"Margin Remaining[:=]\s*\$?([0-9][0-9,]*\.?[0-9]*)",
            ]
            free_margin = None
            for pattern in patterns:
                match = re.search(pattern, msg, flags=re.IGNORECASE)
                if not match:
                    continue
                raw = match.group(1).replace(",", "").rstrip(".")
                try:
                    free_margin = float(raw)
                    break
                except ValueError:
                    continue

            if free_margin is not None:
                safety = getattr(config, "SPREAD_REJECTION_MARGIN_SAFETY", 0.80)
                cap = free_margin * safety
                self.options_engine.set_rejection_margin_cap(cap)
                self.Log(
                    f"REJECTION_MARGIN: Free=${free_margin:,.0f} | "
                    f"Cap=${cap:,.0f} (x{safety:.0%})"
                )
            elif "insufficient buying power" in msg.lower() or "margin" in msg.lower():
                self.Log(
                    "REJECTION_MARGIN_PARSE_FAIL: Could not parse free margin from rejection message"
                )
        except Exception as e:
            self.Log(f"REJECTION_MARGIN: Parse error: {e}")

    def _handle_spread_leg_fill(self, symbol: str, fill_price: float, fill_qty: float) -> None:
        """
        V2.6: Handle a spread leg fill with atomic tracking.

        Fixes Bug #1 (race condition), Bug #6 (quantity tracking), Bug #7 (timeout).

        Uses SpreadFillTracker to store symbols at creation time, preventing race
        condition where options_engine pending legs are cleared before second fill.

        Args:
            symbol: Filled option symbol.
            fill_price: Fill price.
            fill_qty: Fill quantity (absolute value).
        """
        fill_qty = int(abs(fill_qty))  # Ensure positive integer

        # Initialize tracker on FIRST leg fill (capture symbols now, before they can be cleared)
        if self._spread_fill_tracker is None:
            seed = self.options_engine.get_pending_spread_tracker_seed()
            if seed is None:
                self.Log(f"SPREAD_ERROR: Fill received but no pending spread | {symbol}")
                return

            self._spread_fill_tracker = SpreadFillTracker(
                long_leg_symbol=seed["long_leg_symbol"],
                short_leg_symbol=seed["short_leg_symbol"],
                expected_quantity=int(seed["expected_quantity"]),
                timeout_minutes=config.SPREAD_FILL_TIMEOUT_MINUTES,
                created_at=str(self.Time),
                spread_type=seed.get("spread_type"),
            )
            self.Log(
                f"SPREAD: Fill tracker created | "
                f"Long={seed['long_leg_symbol'][-15:]} Short={seed['short_leg_symbol'][-15:]} "
                f"Expected={int(seed['expected_quantity'])}"
            )

        tracker = self._spread_fill_tracker

        # Check for timeout
        if tracker.is_expired(str(self.Time)):
            self.Log(
                f"SPREAD_ERROR: Fill tracker expired | "
                f"Created={tracker.created_at} Current={self.Time}"
            )
            self._cleanup_stale_spread_state()
            return

        # Record fill by symbol match (using normalized keys from tracker + event)
        symbol_norm = self._normalize_symbol_str(symbol)
        long_norm = self._normalize_symbol_str(tracker.long_leg_symbol)
        short_norm = self._normalize_symbol_str(tracker.short_leg_symbol)
        if symbol_norm and symbol_norm == long_norm:
            tracker.record_long_fill(fill_price, fill_qty, str(self.Time))
            self.Log(
                f"SPREAD: Long leg filled | {symbol[-20:]} @ ${fill_price:.2f} x{fill_qty} | "
                f"Total={tracker.long_fill_qty}"
            )

        elif symbol_norm and symbol_norm == short_norm:
            tracker.record_short_fill(fill_price, fill_qty, str(self.Time))
            self.Log(
                f"SPREAD: Short leg filled | {symbol[-20:]} @ ${fill_price:.2f} x{fill_qty} | "
                f"Total={tracker.short_fill_qty}"
            )
        else:
            self.Log(
                f"SPREAD_WARNING: Unknown fill symbol | {symbol} | "
                f"Expected Long={tracker.long_leg_symbol[-15:]} Short={tracker.short_leg_symbol[-15:]}"
            )
            return

        # Check if both legs filled with expected quantity
        spread = None
        if tracker.is_complete():
            # Validate quantities match (Bug #6 fix)
            if not tracker.quantities_match():
                self.Log(
                    f"SPREAD_ERROR: Quantity mismatch | "
                    f"Long={tracker.long_fill_qty} Short={tracker.short_fill_qty} "
                    f"Expected={tracker.expected_quantity}"
                )
                if config.SPREAD_FILL_QTY_MISMATCH_ACTION == "LOG_AND_CLOSE":
                    self._emergency_close_spread_legs()
                    self._spread_fill_tracker = None
                    return

            # Both legs filled - register spread position
            spread = self.options_engine.register_spread_entry(
                long_leg_fill_price=tracker.long_fill_price,
                short_leg_fill_price=tracker.short_fill_price,
                entry_time=str(self.Time),
                current_date=str(self.Time.date()),
                regime_score=self._get_effective_regime_score_for_options(),
            )

            if spread:
                self._diag_spread_entry_fill_count += 1
                self.Log(
                    f"SPREAD: Position registered | {spread.spread_type} | "
                    f"Debit=${spread.net_debit:.2f} | Max Profit=${spread.max_profit:.2f} | "
                    f"Qty={tracker.expected_quantity}"
                )

            # Clear tracker
            self._spread_fill_tracker = None

    def _handle_spread_leg_close(self, symbol: str, fill_price: float, fill_qty: float) -> None:
        """
        V2.6: Handle a spread leg close with quantity validation.

        Fixes Bug #8 (no qty validation on close).

        Args:
            symbol: Closed option symbol.
            fill_price: Fill price.
            fill_qty: Fill quantity (negative for sells).
        """
        norm_symbol = self._normalize_symbol_str(symbol)
        spread = None
        for candidate in self.options_engine.get_spread_positions():
            long_norm = self._normalize_symbol_str(candidate.long_leg.symbol)
            short_norm = self._normalize_symbol_str(candidate.short_leg.symbol)
            if norm_symbol == long_norm or norm_symbol == short_norm:
                spread = candidate
                break
        if spread is None:
            return

        fill_qty_abs = int(abs(fill_qty))
        spread_key = self._build_spread_runtime_key(spread)
        tracker = self._spread_close_trackers.get(spread_key)
        if tracker is None:
            tracker = {
                "expected_qty": spread.num_spreads,
                "long_closed": False,
                "short_closed": False,
                "long_qty": 0,
                "short_qty": 0,
                "long_notional": 0.0,
                "short_notional": 0.0,
            }
            self._spread_close_trackers[spread_key] = tracker

        # Track which leg closed and accumulate quantity
        long_norm = self._normalize_symbol_str(spread.long_leg.symbol)
        short_norm = self._normalize_symbol_str(spread.short_leg.symbol)
        if norm_symbol == long_norm:
            tracker["long_qty"] += fill_qty_abs
            tracker["long_notional"] += fill_price * fill_qty_abs
            tracker["long_closed"] = tracker["long_qty"] >= tracker["expected_qty"]
            self.Log(
                f"SPREAD: Long leg closed | {symbol[-20:]} @ ${fill_price:.2f} x{fill_qty_abs} | "
                f"Total closed={tracker['long_qty']}/{tracker['expected_qty']}"
            )
        elif norm_symbol == short_norm:
            tracker["short_qty"] += fill_qty_abs
            tracker["short_notional"] += fill_price * fill_qty_abs
            tracker["short_closed"] = tracker["short_qty"] >= tracker["expected_qty"]
            self.Log(
                f"SPREAD: Short leg closed | {symbol[-20:]} @ ${fill_price:.2f} x{fill_qty_abs} | "
                f"Total closed={tracker['short_qty']}/{tracker['expected_qty']}"
            )

        # Check if both legs reached expected close quantity.
        if tracker["long_closed"] and tracker["short_closed"]:
            expected_qty = int(tracker["expected_qty"])
            if tracker["long_qty"] != expected_qty:
                self.Log(
                    f"SPREAD_WARNING: Long leg quantity mismatch | "
                    f"Closed={tracker['long_qty']} Expected={expected_qty}"
                )
            if tracker["short_qty"] != expected_qty:
                self.Log(
                    f"SPREAD_WARNING: Short leg quantity mismatch | "
                    f"Closed={tracker['short_qty']} Expected={expected_qty}"
                )

            # Count fill reconciliation only when both legs fully match expected quantity.
            if tracker["long_qty"] == expected_qty and tracker["short_qty"] == expected_qty:
                self._diag_spread_exit_fill_count += 1

            # Use weighted average close prices to avoid partial-fill accounting distortion.
            close_long_price = (
                float(tracker["long_notional"]) / float(tracker["long_qty"])
                if tracker["long_qty"] > 0
                else 0.0
            )
            close_short_price = (
                float(tracker["short_notional"]) / float(tracker["short_qty"])
                if tracker["short_qty"] > 0
                else 0.0
            )
            close_value = close_long_price - close_short_price
            # Unified sign convention for debit/credit spreads:
            # win when close value is greater than entry net value.
            is_win = close_value > spread.net_debit
            self.options_engine.record_spread_result(is_win)
            result_str = "WIN" if is_win else "LOSS"
            self.Log(
                f"SPREAD_RESULT: {result_str} | Type={spread.spread_type} | "
                f"Entry={spread.net_debit:.2f} | Close={close_value:.2f}"
            )
            pnl_pct = (
                ((close_value - spread.net_debit) / abs(spread.net_debit))
                if spread.net_debit
                else 0.0
            )
            exit_reason = self._spread_last_exit_reason.get(spread_key, "FILL_CLOSE_RECONCILED")
            self.Log(
                f"SPREAD: EXIT | Reason={exit_reason} | "
                f"Type={spread.spread_type} | Entry={spread.net_debit:.2f} | "
                f"Close={close_value:.2f} | PnL={pnl_pct:+.1%}"
            )
            self._record_exit_path_pnl(
                reason=exit_reason,
                pnl_dollars=(close_value - spread.net_debit) * 100 * spread.num_spreads,
                engine_tag="VASS",
            )

            stop_pct = float(getattr(config, "SPREAD_STOP_LOSS_PCT", 0.0))
            if stop_pct > 0 and pnl_pct <= -stop_pct:
                self._diag_spread_loss_beyond_stop_count += 1
                self.Log(
                    f"SPREAD_STOP_BREACH: Type={spread.spread_type} | "
                    f"PnL={pnl_pct:+.1%} <= -{stop_pct:.0%} | "
                    f"Reason={exit_reason}"
                )

            # V6.12: Record spread trade in monthly P&L tracker
            if hasattr(self, "pnl_tracker"):
                # Calculate realized P&L (×100 for options multiplier, ×num_spreads for quantity)
                spread_pnl = (close_value - spread.net_debit) * 100 * spread.num_spreads
                # Record as single trade with net P&L
                self.pnl_tracker.record_trade(
                    symbol=f"SPREAD:{spread.spread_type}",
                    engine="OPT_SPREAD",
                    entry_date=spread.entry_time[:10]
                    if spread.entry_time
                    else str(self.Time.date()),
                    exit_date=str(self.Time.date()),
                    entry_price=spread.net_debit,
                    exit_price=close_value,
                    quantity=spread.num_spreads,
                    realized_pnl=spread_pnl,
                )

            # Both legs closed - remove spread position
            removed = self.options_engine.remove_spread_position(symbol)
            if removed is not None:
                self._record_spread_removal(
                    reason="fill_path",
                    count=1,
                    context="FILL_CLOSE_RECONCILED",
                )
                if self.portfolio_router:
                    self.portfolio_router.unregister_spread_margin_by_legs(
                        str(removed.long_leg.symbol),
                        str(removed.short_leg.symbol),
                    )
            else:
                self.Log(
                    "SPREAD_DIAG_WARNING: Fill-path counter skipped | "
                    f"Reason=REMOVE_RETURNED_NONE | Symbol={symbol}"
                )
            self._greeks_breach_logged = False  # Reset for next position
            self._spread_forced_close_retry.pop(spread_key, None)
            self._spread_forced_close_reason.pop(spread_key, None)
            self._spread_forced_close_cancel_counts.pop(spread_key, None)
            self._spread_forced_close_retry_cycles.pop(spread_key, None)
            self._spread_last_close_submit_at.pop(spread_key, None)
            self._spread_last_exit_reason.pop(spread_key, None)
            self._spread_close_trackers.pop(spread_key, None)

            self.Log("SPREAD: Position removed - both legs closed")

    def _queue_spread_close_retry_on_cancel(self, symbol: str, order_event) -> None:
        """
        V6.21: Queue forced spread close retry when broker cancels a close leg.

        We only queue retries for active spread symbols where the canceled order
        appears to be reducing an existing position (close-side quantity).
        """
        symbol_norm = self._normalize_symbol_str(symbol)
        if "QQQ" not in symbol_norm or ("C" not in symbol_norm and "P" not in symbol_norm):
            return

        spread = None
        for candidate in self.options_engine.get_spread_positions():
            long_norm = self._normalize_symbol_str(candidate.long_leg.symbol)
            short_norm = self._normalize_symbol_str(candidate.short_leg.symbol)
            if symbol_norm in {long_norm, short_norm}:
                spread = candidate
                break
        if spread is None:
            return

        try:
            order = self.Transactions.GetOrderById(order_event.OrderId)
        except Exception:
            order = None
        if order is None:
            return

        # Only queue retries for close-side cancels:
        # long-leg close => order qty < 0 while holdings qty > 0
        # short-leg close => order qty > 0 while holdings qty < 0
        holding, _ = self._find_portfolio_holding(
            order_event.Symbol, security_type=SecurityType.Option
        )
        holdings_qty = int(getattr(holding, "Quantity", 0) or 0) if holding is not None else 0
        is_close_side = (order.Quantity < 0 < holdings_qty) or (order.Quantity > 0 > holdings_qty)
        if not is_close_side:
            return

        long_symbol = self._normalize_symbol_str(spread.long_leg.symbol)
        short_symbol = self._normalize_symbol_str(spread.short_leg.symbol)
        spread_key = self._build_spread_runtime_key(spread)
        self._cancel_spread_linked_oco(
            long_symbol, short_symbol, reason="SPREAD_CLOSE_CANCELED_RETRY"
        )
        cancel_count = self._spread_forced_close_cancel_counts.get(spread_key, 0) + 1
        self._spread_forced_close_cancel_counts[spread_key] = cancel_count
        self._diag_spread_exit_canceled_count += 1
        escalation_count = int(getattr(config, "SPREAD_CLOSE_CANCEL_ESCALATION_COUNT", 2))

        self._spread_forced_close_retry[spread_key] = self.Time
        self._spread_forced_close_reason[
            spread_key
        ] = f"ORDER_CANCELED:{getattr(order, 'Type', 'UNKNOWN')}"
        self._spread_last_close_submit_at[spread_key] = self.Time

        if cancel_count >= escalation_count:
            self._diag_spread_close_escalation_count += 1
            order_tag = str(getattr(order, "Tag", "") or "")
            self.Log(
                f"SPREAD_CLOSE_ESCALATED: Cancel threshold reached | "
                f"OrderId={order_event.OrderId} | SpreadKey={spread_key} | "
                f"Tag={self._compact_tag_for_log(order_tag)} | "
                f"Long={long_symbol} | Short={short_symbol} | "
                f"CancelCount={cancel_count} >= {escalation_count} | "
                f"Submitting immediate sequential close"
            )
            self.portfolio_router.receive_signal(
                TargetWeight(
                    symbol=long_symbol,
                    target_weight=0.0,
                    source="OPT",
                    urgency=Urgency.IMMEDIATE,
                    reason="SPREAD_CLOSE_ESCALATED",
                    requested_quantity=spread.num_spreads,
                    metadata={
                        "spread_close_short": True,
                        "spread_short_leg_symbol": short_symbol,
                        "spread_short_leg_quantity": spread.num_spreads,
                        "spread_key": self._build_spread_runtime_key(spread),
                        "exit_type": "SPREAD_CLOSE_ESCALATED",
                    },
                )
            )
            self._diag_spread_exit_signal_count += 1
            self._diag_spread_exit_submit_count += 1
            self._spread_last_close_submit_at[spread_key] = self.Time
        else:
            order_tag = str(getattr(order, "Tag", "") or "")
            self.Log(
                f"SPREAD_RETRY_QUEUED: Close leg canceled | "
                f"OrderId={order_event.OrderId} | SpreadKey={spread_key} | "
                f"Tag={self._compact_tag_for_log(order_tag)} | Symbol={symbol} | "
                f"Long={long_symbol} | Short={short_symbol} | "
                f"OrderQty={order.Quantity} | CancelCount={cancel_count}"
            )

    def _cleanup_stale_spread_state(self) -> None:
        """
        V2.6: Clean up stale spread tracking state (Bug #7 fix).

        Called when fill tracker times out or needs reset.
        """
        self.Log("SPREAD: Cleaning up stale tracking state")

        # Clear fill tracker
        self._spread_fill_tracker = None

        # Clear pending orders mappings
        self._pending_spread_orders.clear()
        self._pending_spread_orders_reverse.clear()
        self._spread_close_trackers.clear()
        self._spread_forced_close_retry.clear()
        self._spread_forced_close_reason.clear()
        self._spread_forced_close_cancel_counts.clear()
        self._spread_forced_close_retry_cycles.clear()
        self._spread_last_close_submit_at.clear()

        # Clear options engine pending spread state
        self.options_engine.clear_pending_spread_state_hard()

    def _emergency_close_spread_legs(self) -> None:
        """
        V2.6: Emergency close spread legs when quantity mismatch detected.

        Liquidates whatever is in the portfolio to prevent orphaned positions.
        """
        self.Log("SPREAD: EMERGENCY - Closing mismatched legs")

        if self._spread_fill_tracker is None:
            return

        tracker = self._spread_fill_tracker

        # Try to close any filled legs
        try:
            # Check long leg
            if tracker.long_leg_symbol:
                long_holding, long_symbol = self._find_portfolio_holding(
                    tracker.long_leg_symbol, security_type=SecurityType.Option
                )
                if long_holding and long_holding.Invested:
                    qty = long_holding.Quantity
                    self._submit_option_close_market_order(
                        symbol=long_symbol,
                        quantity=-qty,
                        reason="EMERG_QTY_MISMATCH",
                        engine_hint="VASS",
                    )
                    self.Log(
                        f"SPREAD: Emergency closed long leg | {self._normalize_symbol_str(long_symbol)} x{qty}"
                    )

            # Check short leg
            if tracker.short_leg_symbol:
                short_holding, short_symbol = self._find_portfolio_holding(
                    tracker.short_leg_symbol, security_type=SecurityType.Option
                )
                if short_holding and short_holding.Invested:
                    qty = short_holding.Quantity
                    self._submit_option_close_market_order(
                        symbol=short_symbol,
                        quantity=-qty,
                        reason="EMERG_QTY_MISMATCH",
                        engine_hint="VASS",
                    )
                    self.Log(
                        f"SPREAD: Emergency closed short leg | {self._normalize_symbol_str(short_symbol)} x{qty}"
                    )

        except Exception as e:
            self.Log(f"SPREAD_ERROR: Emergency close failed | {e}")

    def _schedule_exit_retry(self, symbol: str) -> None:
        """
        V2.6 Bug #14: Schedule a retry for a failed exit order.

        Args:
            symbol: Symbol that failed to exit.
        """
        tracker_key = self._resolve_pending_exit_tracker_key(symbol)
        if tracker_key is None:
            return
        try:
            # Schedule retry after delay using QC's scheduling
            retry_seconds = config.EXIT_ORDER_RETRY_DELAY_SECONDS
            retry_dt = self.Time + timedelta(seconds=retry_seconds)
            existing_retry = self._exit_retry_scheduled_at.get(tracker_key)
            if existing_retry is not None and existing_retry > self.Time:
                self.Log(
                    f"EXIT_RETRY: Schedule already pending for {tracker_key[-20:]} | "
                    f"At={existing_retry.strftime('%Y-%m-%d %H:%M:%S')}"
                )
                return

            # Use Schedule.On for proper scheduling
            def retry_action():
                self._exit_retry_scheduled_at.pop(tracker_key, None)
                self._retry_exit_order(tracker_key)

            # Schedule for N seconds from now (handles minute/second rollover safely).
            date_rule = (
                self.DateRules.Today
                if retry_dt.date() == self.Time.date()
                else self.DateRules.On(retry_dt.year, retry_dt.month, retry_dt.day)
            )
            self.Schedule.On(
                date_rule,
                self.TimeRules.At(
                    retry_dt.hour,
                    retry_dt.minute,
                    retry_dt.second,
                ),
                retry_action,
            )
            self._exit_retry_scheduled_at[tracker_key] = retry_dt
            self.Log(
                f"EXIT_RETRY: Scheduled retry for {tracker_key[-20:]} at "
                f"{retry_dt.strftime('%Y-%m-%d %H:%M:%S')}"
            )
        except Exception as e:
            self.Log(f"EXIT_RETRY_ERROR: Failed to schedule retry | {e}")
            self._exit_retry_scheduled_at.pop(tracker_key, None)
            # Fallback: immediate retry
            self._retry_exit_order(tracker_key)

    def _retry_exit_order(self, symbol: str) -> None:
        """
        V2.6 Bug #14: Retry a failed exit order.

        Args:
            symbol: Symbol to retry exit for.
        """
        tracker_key = self._resolve_pending_exit_tracker_key(symbol)
        if tracker_key is None:
            return
        self._exit_retry_scheduled_at.pop(tracker_key, None)

        tracker = self._pending_exit_orders.get(tracker_key)
        if tracker is None:
            return
        self.Log(
            f"EXIT_RETRY: Retrying exit for {tracker_key[-20:]} (attempt {tracker.retry_count})"
        )
        if self._has_open_non_oco_order_for_symbol(tracker_key):
            self.Log(
                f"EXIT_RETRY_DEFER: Non-OCO open order already exists for {tracker_key[-20:]} | "
                "Skipping duplicate retry submit"
            )
            self._schedule_exit_retry(tracker_key)
            return

        try:
            # Ensure OCO legs are canceled before submitting a retry close.
            try:
                self.oco_manager.cancel_by_symbol(tracker_key, reason="EXIT_RETRY")
            except Exception:
                pass
            if self._has_open_order_for_symbol(tracker_key):
                self.Log(
                    f"EXIT_RETRY_DEFER: Waiting for prior order cancel/settle | {tracker_key[-20:]}"
                )
                self._schedule_exit_retry(tracker_key)
                return

            holding, broker_symbol = self._find_portfolio_holding(tracker_key)
            if holding and holding.Invested:
                qty = holding.Quantity
                ticket = self._submit_option_close_market_order(
                    symbol=broker_symbol,
                    quantity=-qty,
                    reason=f"RETRY_{tracker.reason}",
                    tag_hint=str(getattr(tracker, "reason", "") or ""),
                )
                try:
                    if ticket is not None and hasattr(ticket, "OrderId"):
                        tracker.order_id = int(ticket.OrderId)
                except Exception:
                    pass
                self.Log(f"EXIT_RETRY: Submitted market order | {tracker_key[-20:]} x{qty}")
            else:
                # Position already closed
                self._pending_exit_orders.pop(tracker_key, None)
                self._exit_retry_scheduled_at.pop(tracker_key, None)
                self.Log(f"EXIT_RETRY: Position already closed | {tracker_key[-20:]}")
        except Exception as e:
            self.Log(f"EXIT_RETRY_ERROR: Retry failed | {tracker_key[-20:]} | {e}")

    def _force_market_close(self, symbol: str) -> None:
        """
        V2.6 Bug #14: Emergency market close when all retries exhausted.

        V2.33: If symbol is an option, use atomic close to ensure shorts close first.

        Args:
            symbol: Symbol to force close.
        """
        self.Log(f"EXIT_EMERGENCY: Force market close | {symbol[-20:]}")
        try:
            holding, broker_symbol = self._find_portfolio_holding(symbol)
            if holding and holding.Invested:
                qty = holding.Quantity

                # V2.33: If this is an option, use atomic close for safety
                # This ensures shorts close before longs even in emergency
                if holding.Symbol.SecurityType == SecurityType.Option:
                    self.Log(f"EXIT_EMERGENCY: Using atomic close for option | {symbol[-20:]}")
                    self._close_options_atomic(
                        symbols_to_close=[holding.Symbol],
                        reason="EMERG_OPTION_RETRY_EXHAUSTED",
                        clear_tracking=False,  # Don't clear other tracking state
                    )
                else:
                    # Equity: Use Liquidate for absolute closure
                    self.Liquidate(broker_symbol, tag="EMERG_ALL_RETRIES_FAILED")
                    self.Log(f"EXIT_EMERGENCY: Liquidated | {symbol[-20:]} x{qty}")
            else:
                self.Log(f"EXIT_EMERGENCY: No position to close | {symbol[-20:]}")
        except Exception as e:
            self.Log(f"EXIT_EMERGENCY_ERROR: Liquidate failed | {e}")

    def _get_average_volume(self, volume_window: RollingWindow) -> float:
        """
        Calculate average volume from rolling window.

        Args:
            volume_window: RollingWindow containing historical volumes.

        Returns:
            Average volume, or 1.0 if window not ready.
        """
        if not volume_window.IsReady:
            return 1.0  # Default to avoid division issues

        total = sum(volume_window[i] for i in range(volume_window.Count))
        return total / volume_window.Count if volume_window.Count > 0 else 1.0

    def _get_current_positions(self) -> Dict[str, float]:
        """
        Get current position values for all traded symbols including options.

        Returns:
            Dict of symbol -> current holdings value.
        """
        positions = {}
        # Equity positions
        for symbol in self.traded_symbols:
            positions[str(symbol.Value)] = self.Portfolio[symbol].HoldingsValue

        # Options positions - iterate portfolio for any option holdings
        for kvp in self.Portfolio:
            symbol = kvp.Key
            holding = kvp.Value
            if holding.Invested and symbol.SecurityType == SecurityType.Option:
                positions[str(symbol.Value)] = holding.HoldingsValue

        return positions

    def _get_current_prices(self) -> Dict[str, float]:
        """
        Get current prices for all traded symbols including options.

        V2.19 FIX: Don't iterate all Securities (20K+ options).
        Only get prices for symbols we actually hold or are tracking.

        Returns:
            Dict of symbol -> current price.
        """
        prices = {}
        # Equity prices - these are the symbols we actually care about
        for symbol in self.traded_symbols:
            prices[str(symbol.Value)] = self.Securities[symbol].Price

        # Options prices - only for options we actually HOLD (not all 20K+ subscribed)
        # V2.19 FIX: Iterate Portfolio.Values, not Securities
        for holding in self.Portfolio.Values:
            if not holding.Invested:
                continue
            symbol = holding.Symbol
            if symbol.SecurityType == SecurityType.Option:
                price = self.Securities[symbol].Price if symbol in self.Securities else 0
                if price > 0:
                    prices[str(symbol.Value)] = price

        return prices
