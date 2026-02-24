# region imports
# Type hints
import csv
import gzip
import io
import json
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

# V6.4: OptionDirection now imported only from models.enums (fixes P0 duplicate enum bug)
from execution.execution_engine import ExecutionEngine

# OCO Manager for Options exits
from execution.oco_manager import OCOManager
from main_bootstrap_mixin import MainBootstrapMixin
from main_intraday_close_mixin import MainIntradayCloseMixin
from main_market_close_mixin import MainMarketCloseMixin
from main_observability_mixin import MainObservabilityMixin
from main_options_mixin import MainOptionsMixin
from main_orders_mixin import MainOrdersMixin
from main_premarket_mixin import MainPremarketMixin
from main_reconcile_mixin import MainReconcileMixin
from main_regime_mixin import MainRegimeMixin
from main_risk_monitor_mixin import MainRiskMonitorMixin
from main_signal_generation_mixin import MainSignalGenerationMixin

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
    _initialize_runtime_state = MainOptionsMixin._initialize_runtime_state
    _select_intraday_option_contract = MainOptionsMixin._select_intraday_option_contract
    _select_swing_option_contract = MainOptionsMixin._select_swing_option_contract
    _apply_spread_margin_guard = MainOptionsMixin._apply_spread_margin_guard
    _build_option_contract_from_fill = MainOptionsMixin._build_option_contract_from_fill
    _canonical_options_reason_code = MainOptionsMixin._canonical_options_reason_code
    _scan_options_signals = MainOptionsMixin._scan_options_signals
    _check_spread_exit = MainOptionsMixin._check_spread_exit
    _on_vix_spike_check = MainOptionsMixin._on_vix_spike_check
    _on_micro_regime_update = MainOptionsMixin._on_micro_regime_update
    _on_friday_firewall = MainOptionsMixin._on_friday_firewall
    _get_vix_intraday_proxy = MainOptionsMixin._get_vix_intraday_proxy
    _get_vix_level = MainOptionsMixin._get_vix_level
    _should_scan_intraday = MainOptionsMixin._should_scan_intraday
    _can_trade_options_settlement_aware = MainOptionsMixin._can_trade_options_settlement_aware
    _micro_dte_bucket = MainOptionsMixin._micro_dte_bucket
    _inc_micro_dte_counter = MainOptionsMixin._inc_micro_dte_counter
    _record_micro_drop_reason_dte = MainOptionsMixin._record_micro_drop_reason_dte
    _intraday_engine_bucket_from_strategy = MainOptionsMixin._intraday_engine_bucket_from_strategy
    _inc_intraday_engine_counter = MainOptionsMixin._inc_intraday_engine_counter
    _record_intraday_drop_reason = MainOptionsMixin._record_intraday_drop_reason
    _record_vass_reject_reason = MainOptionsMixin._record_vass_reject_reason
    _inc_transition_path_counter = MainOptionsMixin._inc_transition_path_counter
    _is_terminal_exit_retry_tag = MainOrdersMixin._is_terminal_exit_retry_tag
    _on_moo_fallback = MainOrdersMixin._on_moo_fallback
    _cleanup_stale_orders = MainOrdersMixin._cleanup_stale_orders
    _oco_engine_prefix_for_strategy = MainOrdersMixin._oco_engine_prefix_for_strategy
    _record_order_tag_map = MainOrdersMixin._record_order_tag_map
    _record_order_tag_resolve = MainOrdersMixin._record_order_tag_resolve
    _record_order_lifecycle_event = MainOrdersMixin._record_order_lifecycle_event
    _get_order_tag = MainOrdersMixin._get_order_tag
    _cache_order_tag_hint = MainOrdersMixin._cache_order_tag_hint
    _cache_symbol_fill_tag = MainOrdersMixin._cache_symbol_fill_tag
    _get_recent_symbol_fill_tag = MainOrdersMixin._get_recent_symbol_fill_tag
    _extract_trace_id_from_tag = MainOrdersMixin._extract_trace_id_from_tag
    _compact_tag_for_log = MainOrdersMixin._compact_tag_for_log
    _sync_intraday_oco = MainOrdersMixin._sync_intraday_oco
    _log_order_lifecycle_issue = MainOrdersMixin._log_order_lifecycle_issue
    _forward_execution_event = MainOrdersMixin._forward_execution_event
    OnOrderEvent = MainOrdersMixin.OnOrderEvent
    _on_fill = MainOrdersMixin._on_fill
    _cancel_residual_option_orders = MainOrdersMixin._cancel_residual_option_orders
    _handle_order_rejection = MainOrdersMixin._handle_order_rejection
    _handle_spread_leg_close = MainOrdersMixin._handle_spread_leg_close
    _handle_spread_leg_fill = MainOrdersMixin._handle_spread_leg_fill
    _queue_spread_close_retry_on_cancel = MainOrdersMixin._queue_spread_close_retry_on_cancel
    _evaluate_base_regime_candidate = MainRegimeMixin._evaluate_base_regime_candidate
    _advance_detector_state = MainRegimeMixin._advance_detector_state
    _update_regime_detector_state = MainRegimeMixin._update_regime_detector_state
    _get_regime_transition_context = MainRegimeMixin._get_regime_transition_context
    _record_transition_derisk_action = MainRegimeMixin._record_transition_derisk_action
    _get_transition_execution_context = MainRegimeMixin._get_transition_execution_context
    _apply_transition_handoff_open_position_derisk = (
        MainRegimeMixin._apply_transition_handoff_open_position_derisk
    )
    _on_intraday_reconcile = MainReconcileMixin._on_intraday_reconcile
    _is_primary_market_open = MainReconcileMixin._is_primary_market_open
    _reconcile_spread_state = MainReconcileMixin._reconcile_spread_state
    _build_spread_runtime_key = MainReconcileMixin._build_spread_runtime_key
    _record_spread_exit_reason = MainReconcileMixin._record_spread_exit_reason
    _record_spread_removal = MainReconcileMixin._record_spread_removal
    _reconcile_spread_ghosts = MainReconcileMixin._reconcile_spread_ghosts
    _clear_spread_runtime_trackers_by_key = MainReconcileMixin._clear_spread_runtime_trackers_by_key
    _check_splits = MainReconcileMixin._check_splits
    _reconcile_positions = MainReconcileMixin._reconcile_positions
    _check_expiration_hammer_v2 = MainIntradayCloseMixin._check_expiration_hammer_v2
    _close_options_atomic = MainIntradayCloseMixin._close_options_atomic
    _on_intraday_options_force_close = MainIntradayCloseMixin._on_intraday_options_force_close
    _intraday_force_exit_fallback = MainIntradayCloseMixin._intraday_force_exit_fallback
    _reconcile_intraday_close_guards = MainIntradayCloseMixin._reconcile_intraday_close_guards
    _ensure_oco_for_open_options = MainIntradayCloseMixin._ensure_oco_for_open_options
    _liquidate_all_spread_aware = MainIntradayCloseMixin._liquidate_all_spread_aware
    _get_primary_market_close_time = MainMarketCloseMixin._get_primary_market_close_time
    _on_market_close = MainMarketCloseMixin._on_market_close
    _monitor_risk_greeks = MainRiskMonitorMixin._monitor_risk_greeks
    _get_fresh_position_greeks = MainRiskMonitorMixin._get_fresh_position_greeks
    _add_securities = MainBootstrapMixin._add_securities
    _setup_indicators = MainBootstrapMixin._setup_indicators
    _initialize_engines = MainBootstrapMixin._initialize_engines
    _initialize_infrastructure = MainBootstrapMixin._initialize_infrastructure
    _setup_schedules = MainBootstrapMixin._setup_schedules
    _process_eod_signals = MainSignalGenerationMixin._process_eod_signals
    _process_immediate_signals = MainSignalGenerationMixin._process_immediate_signals
    _is_market_close_blackout = MainSignalGenerationMixin._is_market_close_blackout
    _generate_trend_signals_eod = MainSignalGenerationMixin._generate_trend_signals_eod
    _generate_options_signals = MainSignalGenerationMixin._generate_options_signals
    _on_eod_processing = MainSignalGenerationMixin._on_eod_processing
    _handle_kill_switch = MainSignalGenerationMixin._handle_kill_switch
    _scan_mr_signals = MainSignalGenerationMixin._scan_mr_signals
    _on_time_guard_start = MainSignalGenerationMixin._on_time_guard_start
    _on_time_guard_end = MainSignalGenerationMixin._on_time_guard_end
    _on_warm_entry_check = MainSignalGenerationMixin._on_warm_entry_check
    _mr_force_close_fallback = MainSignalGenerationMixin._mr_force_close_fallback
    _on_mr_force_close = MainSignalGenerationMixin._on_mr_force_close
    OnEndOfAlgorithm = MainObservabilityMixin.OnEndOfAlgorithm
    _build_regime_observability_key = MainObservabilityMixin._build_regime_observability_key
    _build_observability_key = MainObservabilityMixin._build_observability_key
    _build_regime_timeline_observability_key = (
        MainObservabilityMixin._build_regime_timeline_observability_key
    )
    _build_signal_lifecycle_observability_key = (
        MainObservabilityMixin._build_signal_lifecycle_observability_key
    )
    _build_router_rejection_observability_key = (
        MainObservabilityMixin._build_router_rejection_observability_key
    )
    _build_order_lifecycle_observability_key = (
        MainObservabilityMixin._build_order_lifecycle_observability_key
    )
    _save_observability_csv_artifact = MainObservabilityMixin._save_observability_csv_artifact
    _on_observability_checkpoint = MainObservabilityMixin._on_observability_checkpoint
    _should_log_backtest_category = MainObservabilityMixin._should_log_backtest_category
    _ensure_daily_proxy_windows_snapshot = (
        MainObservabilityMixin._ensure_daily_proxy_windows_snapshot
    )
    _router_engine_bucket = MainObservabilityMixin._router_engine_bucket
    _capture_router_rejections = MainObservabilityMixin._capture_router_rejections
    _get_recent_router_rejections = MainObservabilityMixin._get_recent_router_rejections
    _record_router_rejection_event = MainObservabilityMixin._record_router_rejection_event
    _classify_exit_path = MainObservabilityMixin._classify_exit_path
    _record_exit_path_pnl = MainObservabilityMixin._record_exit_path_pnl
    _on_pre_market_setup = MainPremarketMixin._on_pre_market_setup
    _on_sod_baseline = MainPremarketMixin._on_sod_baseline
    _on_weekly_reset = MainPremarketMixin._on_weekly_reset
    _is_first_bar_after_market_gap = MainPremarketMixin._is_first_bar_after_market_gap
    _get_unsettled_cash = MainPremarketMixin._get_unsettled_cash
    _check_settlement_cooldown = MainPremarketMixin._check_settlement_cooldown
    _schedule_dynamic_eod_events = MainPremarketMixin._schedule_dynamic_eod_events
    _check_premarket_itm_shorts = MainPremarketMixin._check_premarket_itm_shorts
    _get_premarket_vix_gap_proxy_pct = MainPremarketMixin._get_premarket_vix_gap_proxy_pct
    _update_premarket_vix_ladder = MainPremarketMixin._update_premarket_vix_ladder
    _apply_premarket_vix_actions = MainPremarketMixin._apply_premarket_vix_actions
    _is_premarket_ladder_entry_block_active = (
        MainPremarketMixin._is_premarket_ladder_entry_block_active
    )
    _is_premarket_ladder_call_block_active = (
        MainPremarketMixin._is_premarket_ladder_call_block_active
    )
    _is_premarket_shock_memory_active = MainPremarketMixin._is_premarket_shock_memory_active
    _get_premarket_shock_memory_pct = MainPremarketMixin._get_premarket_shock_memory_pct
    _queue_itm_weekend_gap_exit_signals = MainPremarketMixin._queue_itm_weekend_gap_exit_signals

    _safe_objectstore_key_component = staticmethod(
        MainObservabilityMixin._safe_objectstore_key_component
    )
    _ensure_log_budget_state = MainObservabilityMixin._ensure_log_budget_state
    _is_log_budget_enforced = MainObservabilityMixin._is_log_budget_enforced
    _estimate_log_bytes = MainObservabilityMixin._estimate_log_bytes
    _emit_log_raw = MainObservabilityMixin._emit_log_raw
    _budget_log = MainObservabilityMixin._budget_log

    def Log(self, message: Any) -> None:  # noqa: N802 (QC API method name)
        """Budget-aware log wrapper to avoid QC backtest log-cap truncation."""
        self._budget_log(message, priority=2)

    def Initialize(self) -> None:
        self._ensure_log_budget_state()
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
        self._initialize_runtime_state()

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

    # =========================================================================
    # ONDATA HELPERS
    # =========================================================================

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

    # =========================================================================
    # SCHEDULED EVENT HANDLERS
    # =========================================================================

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

        if (
            not is_live
            and bool(getattr(config, "LOG_DROP_AGGREGATE_BACKTEST_ENABLED", True))
            and category_token in {"INTRADAY_BLOCKED", "INTRADAY_DROPPED", "VASS_FALLBACK"}
        ):
            first_n = max(0, int(getattr(config, "LOG_DROP_AGG_FIRST_N_PER_KEY", 1)))
            every_n = max(0, int(getattr(config, "LOG_DROP_AGG_SAMPLE_EVERY_N", 0)))
        else:
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

    def _record_drop_reason_aggregate(self, category: str, reason_key: str) -> None:
        """Aggregate high-frequency drop/fallback reasons for end-of-day RCA summary."""
        if bool(hasattr(self, "LiveMode") and self.LiveMode):
            return
        if not bool(getattr(config, "LOG_DROP_AGGREGATE_BACKTEST_ENABLED", True)):
            return
        category_token = str(category or "GENERAL").strip().upper() or "GENERAL"
        if category_token not in {"INTRADAY_BLOCKED", "INTRADAY_DROPPED", "VASS_FALLBACK"}:
            return
        reason_token = str(reason_key or "GENERIC").strip().upper()[:120] or "GENERIC"
        hhmm = (
            self.Time.strftime("%H:%M")
            if hasattr(self, "Time") and self.Time is not None
            else "00:00"
        )
        category_bucket = self._daily_drop_reason_agg.setdefault(category_token, {})
        row = category_bucket.get(reason_token)
        if row is None:
            category_bucket[reason_token] = {"count": 1, "first": hhmm, "last": hhmm}
            return
        row["count"] = int(row.get("count", 0)) + 1
        row["last"] = hhmm

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
        self._record_drop_reason_aggregate(category=category, reason_key=reason_key)
        if not self._should_log_high_frequency_backtest(
            config_flag=config_flag,
            category=category,
            reason_key=reason_key,
            default_backtest_enabled=default_backtest_enabled,
        ):
            return False
        return bool(self._budget_log(message, priority=3))

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

    # =========================================================================
    # V2.9: SETTLEMENT-AWARE TRADING (Bug #6 Fix)
    # =========================================================================

    # =========================================================================
    # ORDER EVENT HANDLER
    # =========================================================================

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

    # =========================================================================
    # SIGNAL PROCESSING HELPERS
    # =========================================================================

    def _get_contract_prices(self, contract) -> Tuple[float, float]:
        return self.options_engine.get_contract_prices(contract)

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
        symbol_norm = self._normalize_symbol_str(symbol)
        if not symbol_norm:
            return
        if not hasattr(self, "_force_market_close_inflight"):
            self._force_market_close_inflight = set()
        if symbol_norm in self._force_market_close_inflight:
            self.Log(
                f"EXIT_EMERGENCY_REENTRY_SKIP: {symbol_norm[-20:]} | "
                "Reason=Already processing forced close for symbol"
            )
            return
        self._force_market_close_inflight.add(symbol_norm)
        self.Log(f"EXIT_EMERGENCY: Force market close | {symbol[-20:]}")
        try:
            holding, broker_symbol = self._find_portfolio_holding(symbol)
            if holding and holding.Invested:
                qty = holding.Quantity

                # Single-symbol forced close path: submit direct close and avoid
                # re-entering atomic close loops from inside OnOrderEvent callbacks.
                if holding.Symbol.SecurityType == SecurityType.Option:
                    engine_bucket = self._infer_option_engine_bucket_for_symbol(
                        symbol=broker_symbol
                    )
                    self.Log(
                        f"EXIT_EMERGENCY: Direct option close submit | {symbol[-20:]} | "
                        f"Engine={engine_bucket}"
                    )
                    self._submit_option_close_market_order(
                        symbol=broker_symbol,
                        quantity=-qty,
                        reason="EMERG_OPTION_RETRY_EXHAUSTED",
                        engine_hint=engine_bucket,
                    )
                else:
                    # Equity: Use Liquidate for absolute closure
                    self.Liquidate(broker_symbol, tag="EMERG_ALL_RETRIES_FAILED")
                    self.Log(f"EXIT_EMERGENCY: Liquidated | {symbol[-20:]} x{qty}")
            else:
                self.Log(f"EXIT_EMERGENCY: No position to close | {symbol[-20:]}")
        except Exception as e:
            self.Log(f"EXIT_EMERGENCY_ERROR: Liquidate failed | {e}")
        finally:
            self._force_market_close_inflight.discard(symbol_norm)

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
