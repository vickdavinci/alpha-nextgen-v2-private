# region imports
# Type hints
import json
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

# Models
from models.enums import IntradayStrategy, OptionDirection, RegimeLevel, Urgency
from models.target_weight import TargetWeight

# Infrastructure
from persistence.state_manager import StateManager

# Portfolio & Execution
from portfolio.portfolio_router import PortfolioRouter
from scheduling.daily_scheduler import DailyScheduler

# V6.12: Monthly P&L Tracking
from utils.monthly_pnl_tracker import MonthlyPnLTracker

# endregion


class AlphaNextGen(QCAlgorithm):
    """
    Alpha NextGen - Multi-Strategy Algorithmic Trading System.

    This is the main entry point for the QuantConnect algorithm.
    Implements Hub-and-Spoke architecture with Portfolio Router as the central hub.

    Architecture:
        - Portfolio Router (Hub) coordinates all strategy engines (Spokes)
        - Strategy engines emit TargetWeight intentions
        - Only Portfolio Router is authorized to place orders

    Attributes:
        Symbol References:
            spy, rsp, hyg, ief: Symbol - Proxy symbols for regime calculation
            tqqq, soxl, qld, sso, tna, fas, tmf, psq: Symbol - Traded symbols
            traded_symbols: List[Symbol] - All traded symbols
            proxy_symbols: List[Symbol] - All proxy symbols
            trend_symbols: List[Symbol] - Trend engine symbols (QLD, SSO, TNA, FAS)

        Engines:
            regime_engine: RegimeEngine - Market state scoring
            capital_engine: CapitalEngine - Phase management, tradeable equity
            risk_engine: RiskEngine - Circuit breakers and safeguards
            cold_start_engine: ColdStartEngine - Days 1-5 warm entry logic
            trend_engine: TrendEngine - BB compression breakout signals
            mr_engine: MeanReversionEngine - Intraday oversold bounce signals
            hedge_engine: HedgeEngine - Regime-based hedge allocation

        Infrastructure:
            portfolio_router: PortfolioRouter - Central coordination, order placement
            execution_engine: ExecutionEngine - Order submission
            state_manager: StateManager - ObjectStore persistence
            scheduler: DailyScheduler - Timed events

        Indicators (registered with QC):
            spy_sma20, spy_sma50, spy_sma200: SimpleMovingAverage - Trend factor
            spy_atr: AverageTrueRange - Vol shock detection
            qld_ma200, sso_ma200, tna_ma200, fas_ma200: SMA(200) - Trend direction
            qld_adx, sso_adx, tna_adx, fas_adx: ADX(14) - Momentum confirmation
            qld_atr, sso_atr, tna_atr, fas_atr: AverageTrueRange - Chandelier stops
            tqqq_rsi, soxl_rsi: RelativeStrengthIndex - MR oversold

        Rolling Windows:
            spy_closes, rsp_closes, hyg_closes, ief_closes: RollingWindow[float]

        Baselines:
            equity_prior_close: float - For kill switch calculation
            equity_sod: float - Start of day equity
            spy_prior_close: float - For gap filter
            spy_open: float - For panic mode

        Daily Tracking:
            today_trades: List[str] - Trades executed today
            today_safeguards: List[str] - Safeguards triggered today
            symbols_to_skip: Set[str] - Split-frozen symbols

    See: CLAUDE.md for component map and authority rules
    See: docs/ for full specification
    """

    # =========================================================================
    # TYPE HINTS FOR CLASS ATTRIBUTES (initialized in Initialize)
    # =========================================================================

    # Symbol references (proxy)
    spy: Symbol
    rsp: Symbol
    hyg: Symbol
    ief: Symbol
    vix: Symbol  # V2.1: VIX for MR regime filter

    # Symbol references (traded)
    tqqq: Symbol
    soxl: Symbol
    qld: Symbol
    sso: Symbol
    tna: Symbol  # V2.2: 3× Russell 2000 (Trend)
    fas: Symbol  # V2.2: 3× Financials (Trend)
    tmf: Symbol
    psq: Symbol
    qqq: Symbol  # V2.1: QQQ for options

    # Symbol collections
    traded_symbols: List[Symbol]
    proxy_symbols: List[Symbol]

    # Engines
    regime_engine: RegimeEngine
    capital_engine: CapitalEngine
    risk_engine: RiskEngine
    cold_start_engine: ColdStartEngine
    trend_engine: TrendEngine
    mr_engine: MeanReversionEngine
    hedge_engine: HedgeEngine
    options_engine: OptionsEngine  # V2.1: Options Engine
    oco_manager: OCOManager  # V2.1: OCO Manager for options exits

    # Infrastructure
    portfolio_router: PortfolioRouter
    execution_engine: ExecutionEngine
    state_manager: StateManager
    scheduler: DailyScheduler

    # Baselines
    equity_prior_close: float
    equity_sod: float
    spy_prior_close: float
    spy_open: float

    # Daily tracking
    today_trades: List[str]
    today_safeguards: List[str]
    symbols_to_skip: Set[str]

    def Initialize(self) -> None:
        """
        Initialize the algorithm.

        Execution order:
            1. Basic setup (dates, cash, timezone, brokerage)
            2. Add securities
            3. Initialize indicators
            4. Set warmup period
            5. Initialize engines
            6. Initialize infrastructure
            7. Register schedules
            8. Load persisted state
            9. Initialize daily tracking variables
        """
        # =====================================================================
        # STEP 1: Basic Setup
        # =====================================================================
        # Stage 1: 1-day test (Jan 2, 2024 - first trading day)
        # Change these dates for different test stages:
        # Stage 2: SetStartDate(2024, 1, 1), SetEndDate(2024, 1, 31) - 30 days
        # Stage 3: SetStartDate(2024, 1, 1), SetEndDate(2024, 3, 31) - 3 months
        # Stage 4: SetStartDate(2024, 1, 1), SetEndDate(2024, 12, 31) - 1 year
        # Stage 5: SetStartDate(2020, 1, 1), SetEndDate(2024, 12, 31) - 5 years
        self.SetStartDate(2021, 12, 1)
        self.SetEndDate(2022, 2, 28)  # Dec 2021 - Feb 2022 backtest (3 months)
        self.SetCash(config.INITIAL_CAPITAL)  # Seed capital from config

        # All times are Eastern
        self.SetTimeZone("America/New_York")

        # Interactive Brokers brokerage model
        self.SetBrokerageModel(BrokerageName.InteractiveBrokersBrokerage)

        self.Log("INIT: Basic setup complete")

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
        self.Log(f"INIT: Warmup set to {config.INDICATOR_WARMUP_DAYS} days")

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
                "ALPHA_NEXTGEN_RISK",
                "ALPHA_NEXTGEN_CAPITAL",
                "ALPHA_NEXTGEN_COLDSTART",
                "ALPHA_NEXTGEN_STARTUP_GATE",
                "ALPHA_NEXTGEN_POSITIONS",
                # V3.3 P0: Add options/OCO/regime state keys
                "options_engine_state",
                "oco_manager_state",
                "regime_engine_state",
            ]
            for key in state_keys:
                if self.ObjectStore.ContainsKey(key):
                    self.ObjectStore.Delete(key)
            self.Log("INIT: ObjectStore cleared for fresh backtest")

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
        self._last_vass_rejection_log = None  # V2.10: Throttle VASS rejection logs
        self._last_swing_scan_time = None  # V2.19: Throttle swing spread scans (1/hour)
        self._intraday_force_exit_fallback_date = None  # V6.12: Fallback guard (once/day)
        self._mr_force_close_fallback_date = None  # V6.12: MR force close fallback guard
        # Guard against duplicate static+dynamic schedule callbacks on same day.
        self._intraday_force_close_ran_date = None
        self._mr_force_close_ran_date = None
        self._eod_processing_ran_date = None
        self._market_close_ran_date = None
        # Intraday reconciliation cadence (reduce zombie/orphan persistence).
        self._last_reconcile_positions_run: Optional[datetime] = None

        # V2.20: Scoped rejection cooldowns — per-strategy penalty after broker rejection
        # Prevents "machine gun" retries while allowing other strategies to continue
        self._trend_rejection_cooldown_until = None  # Trend: skip until next EOD cycle
        self._options_swing_cooldown_until = None  # Options Swing: 30 min cooldown
        self._options_intraday_cooldown_until = None  # Options Intraday: 15 min cooldown
        self._options_spread_cooldown_until = None  # Options Spread: 30 min cooldown
        self._mr_rejection_cooldown_until = None  # Mean Reversion: 15 min cooldown
        # V6.15: One-shot retry for temporary intraday drops (slot/cooldown/margin timing).
        self._intraday_retry_once_pending = False
        self._intraday_retry_expires = None
        self._intraday_retry_direction = None
        self._intraday_retry_reason_code = None
        # V6.15: Fallback ledger to ensure intraday exits always emit INTRADAY_RESULT.
        self._intraday_entry_snapshot = {}
        # V6.16: Force-close safety guards (prevent duplicate close amplification).
        self._intraday_close_in_progress_symbols = set()
        self._intraday_force_exit_submitted_symbols = {}

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
        self._pending_spread_orders: Dict[str, str] = {}
        # V2.6 Bug #5: Add reverse mapping for long leg rejection handling
        self._pending_spread_orders_reverse: Dict[str, str] = {}  # long -> short

        # V2.6: Atomic spread fill tracking (Bugs #1, #6, #7)
        self._spread_fill_tracker: Optional[SpreadFillTracker] = None

        # V6.22: Per-spread close trackers (fixes shared-counter corruption).
        # key: "<long_symbol>|<short_symbol>"
        self._spread_close_trackers: Dict[str, Dict[str, Any]] = {}
        # V6.22: Track external broker order events to avoid EXEC: UNKNOWN_ORDER spam.
        self._external_exec_event_logged: Set[int] = set()
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
        self._order_lifecycle_log_count = 0
        self._last_micro_update_log_signature: Optional[Tuple[str, str, float, float, float]] = None
        self._last_micro_update_log_time: Optional[datetime] = None
        self._last_spread_construct_fail_log_at: Optional[datetime] = None
        self._intraday_regime_score: Optional[float] = None
        self._intraday_regime_updated_at: Optional[datetime] = None
        self._last_regime_effective_log_at: Optional[datetime] = None

        # V2.6 Bug #14: Exit order retry tracking
        self._pending_exit_orders: Dict[str, ExitOrderTracker] = {}
        # V6.22: Persist forced spread-close retries when broker cancels close legs.
        # key = spread key "<long>|<short>", value = next eligible retry time.
        self._spread_forced_close_retry: Dict[str, datetime] = {}
        self._spread_forced_close_reason: Dict[str, str] = {}
        self._spread_forced_close_cancel_counts: Dict[str, int] = {}
        self._spread_forced_close_retry_cycles: Dict[str, int] = {}
        # Last known spread leg marks to avoid skipping exit checks on transient quote gaps.
        self._spread_exit_mark_cache: Dict[str, Dict[str, Any]] = {}
        # Orphan-close idempotency: avoid resubmitting the same orphan liquidation every reconcile cycle.
        self._recon_orphan_close_submitted: Dict[str, str] = {}

        # V2.4.4 P0: Margin call circuit breaker tracking
        # Prevents 2765+ margin call spam seen in V2.4.3 backtest
        self._margin_call_consecutive_count: int = 0
        self._margin_call_cooldown_until: Optional[str] = None

        self.Log(
            f"INIT: Complete | "
            f"Cash=${config.INITIAL_CAPITAL:,} | "
            f"Symbols={len(self.traded_symbols) + len(self.proxy_symbols)}"
        )

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
        # If the scheduled 15:30 close missed, enforce once after 15:35.
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

        # V6.11: Deprecated symbols - kept for indicator compatibility but not actively traded
        self.tna = self.AddEquity("TNA", Resolution.Minute).Symbol  # Deprecated: 3× Russell
        self.fas = self.AddEquity("FAS", Resolution.Minute).Symbol  # Deprecated: 3× Financials
        self.tmf = self.AddEquity("TMF", Resolution.Minute).Symbol  # Deprecated: 3× Treasury
        self.psq = self.AddEquity("PSQ", Resolution.Minute).Symbol  # Deprecated: 1× Inv Nasdaq

        # Traded symbols - Hedges (V6.11: SH replaces TMF/PSQ)
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
        # NOTE: CBOE VIX only supports Daily resolution in QC backtests
        # For intraday VIX direction, we use UVXY (1.5x VIX ETF) as proxy
        self.vix = self.AddData(CBOE, "VIX", Resolution.Daily).Symbol
        self._current_vix = 15.0  # Default to normal regime until data arrives
        self._vix_at_open = 15.0  # V2.1.1: VIX at market open for micro regime
        self._vix_5min_ago = 15.0  # V2.1.1: VIX 5 minutes ago for spike detection
        self._vix_15min_ago = 15.0  # V2.3.4: VIX 15 minutes ago for short-term trend
        self._last_vix_spike_log = None  # Log throttle: last VIX spike log time
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
        self._settlement_cooldown_until: Optional[datetime] = None
        self._last_market_close_check: Optional[datetime] = None

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

        # V2.2: TNA indicators (3× Russell 2000)
        self.tna_ma200 = self.SMA(self.tna, config.SMA_SLOW, Resolution.Daily)
        self.tna_adx = self.ADX(self.tna, config.ADX_PERIOD, Resolution.Daily)
        self.tna_atr = self.ATR(
            self.tna, config.ATR_PERIOD, MovingAverageType.Wilders, Resolution.Daily
        )

        # V2.2: FAS indicators (3× Financials) - DEPRECATED V6.11 but kept for compatibility
        self.fas_ma200 = self.SMA(self.fas, config.SMA_SLOW, Resolution.Daily)
        self.fas_adx = self.ADX(self.fas, config.ADX_PERIOD, Resolution.Daily)
        self.fas_atr = self.ATR(
            self.fas, config.ATR_PERIOD, MovingAverageType.Wilders, Resolution.Daily
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
        self.tna_sma50 = self.SMA(self.tna, config.TREND_SMA_PERIOD, Resolution.Daily)
        self.fas_sma50 = self.SMA(self.fas, config.TREND_SMA_PERIOD, Resolution.Daily)
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

        # Store capital state for EOD->Market Close handoff
        self._eod_capital_state = None
        # V6.6.2: OCO recovery throttle (symbol -> last attempt date)
        self._last_oco_recovery_attempt = {}

        self.Log(
            f"INIT: Indicators initialized | "
            f"Lookback={lookback} days | "
            f"Warmup={config.INDICATOR_WARMUP_DAYS} days"
        )

    def _initialize_engines(self) -> None:
        """
        Initialize all strategy and core engines.

        Core Engines:
            - RegimeEngine: Market state scoring (0-100)
            - CapitalEngine: Phase management, tradeable equity
            - RiskEngine: Circuit breakers and safeguards
            - ColdStartEngine: Days 1-5 warm entry logic

        Strategy Engines:
            - TrendEngine: BB compression breakout signals
            - MeanReversionEngine: Intraday oversold bounce signals
            - HedgeEngine: Regime-based hedge allocation

        All engines receive reference to self (QCAlgorithm) for logging and data access.
        """
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
        """
        Initialize infrastructure components.

        Components:
            - PortfolioRouter: Central coordination hub, ONLY order placer
            - ExecutionEngine: Order submission to broker
            - StateManager: ObjectStore persistence
            - DailyScheduler: Timed event registration

        Note: PositionManager is managed within PortfolioRouter.
        """
        self.portfolio_router = PortfolioRouter(self)
        self.execution_engine = ExecutionEngine(self)
        self.state_manager = StateManager(self)
        self.scheduler = DailyScheduler(self)

        # V6.12: Monthly P&L Tracker
        self.pnl_tracker = MonthlyPnLTracker(self)

        self.Log("INIT: Infrastructure initialized")

    def _setup_schedules(self) -> None:
        """
        Register scheduled events and callbacks.

        Events are registered with QuantConnect's Schedule.On API.
        Callbacks wire main.py event handlers to the scheduler.

        Events (all times Eastern):
            - 09:25: Pre-market setup (set equity_prior_close)
            - 09:31: MOO fallback check
            - 09:33: SOD baseline (set equity_sod, gap filter)
            - 10:00: Warm entry check (cold start)
            - 13:55: Time guard start (block entries)
            - 14:10: Time guard end (resume entries)
            - 15:45: MR force close + EOD processing
            - 16:00: Market close (persist state)
            - Monday 09:30: Weekly reset
        """
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
        # - Missing 15:30/15:45/16:00 processing
        #
        # These static schedules act as a fallback for normal trading days (4:00 PM close).
        # For early close days, dynamic scheduling should override these times.
        intraday_force_exit = getattr(config, "INTRADAY_FORCE_EXIT_TIME", "15:30")
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
        self.Schedule.On(
            self.DateRules.EveryDay(),
            self.TimeRules.At(9, 35),
            self._refresh_intraday_regime_score,
        )
        self.Schedule.On(
            self.DateRules.EveryDay(),
            self.TimeRules.At(12, 0),
            self._refresh_intraday_regime_score,
        )
        self.Schedule.On(
            self.DateRules.EveryDay(),
            self.TimeRules.At(14, 0),
            self._refresh_intraday_regime_score,
        )
        # #8 fix: intraday reconciliation checkpoints (zombie/orphan cleanup)
        for hour, minute in [(11, 30), (13, 30)]:
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
        # Get current prices (use close for historical, current for live)
        if data.Bars.ContainsKey(self.spy):
            self.spy_closes.Add(self.Securities[self.spy].Close)
        if data.Bars.ContainsKey(self.rsp):
            self.rsp_closes.Add(self.Securities[self.rsp].Close)
        if data.Bars.ContainsKey(self.hyg):
            self.hyg_closes.Add(self.Securities[self.hyg].Close)
        if data.Bars.ContainsKey(self.ief):
            self.ief_closes.Add(self.Securities[self.ief].Close)

        # V2.1: Update VIX value for MR regime filter
        if data.ContainsKey(self.vix):
            vix_data = data[self.vix]
            if vix_data is not None:
                self._current_vix = float(vix_data.Close)

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
                self.portfolio_router._pending_weights.clear()

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
            - Normal days (4:00 PM close): Events at 15:30/15:45/16:00
            - Early close days (1:00 PM): Events at 12:30/12:45/13:00
        """
        try:
            # Get actual market close time for today
            market_hours = self.Securities[self.spy].Exchange.Hours
            market_close = market_hours.GetNextMarketClose(self.Time, False)
            is_normal_close = market_close.hour == 16 and market_close.minute == 0

            # #10 fix: avoid duplicate static+dynamic schedules on normal close days.
            # Static fallback schedules already exist for 15:30/15:45/16:00.
            if not is_normal_close:
                self.scheduler.schedule_dynamic_eod_events(market_close)
            else:
                self.Log(
                    "EOD_SCHEDULE: Normal close detected | Using static fallback schedules only"
                )

            # Also schedule intraday options force close dynamically
            from datetime import timedelta

            opt_offset = getattr(config, "INTRADAY_OPTIONS_OFFSET_MINUTES", 30)
            opt_close_time = market_close - timedelta(minutes=opt_offset)
            static_force_exit = getattr(config, "INTRADAY_FORCE_EXIT_TIME", "15:30")
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
        """Apply pre-market de-risk actions based on ladder level."""
        if not getattr(config, "PREMARKET_VIX_LADDER_ENABLED", True):
            return

        # Always flush stale intraday option carry first.
        if (
            getattr(config, "PREMARKET_FORCE_CLOSE_INTRADAY_STALE", True)
            and self.options_engine.has_intraday_position()
        ):
            intraday_pos = self.options_engine.get_intraday_position()
            if intraday_pos is not None:
                self.Log(
                    f"PREMARKET_LADDER: Closing stale intraday carry | {intraday_pos.contract.symbol}"
                )
                self.portfolio_router.receive_signal(
                    TargetWeight(
                        symbol=self._normalize_symbol_str(intraday_pos.contract.symbol),
                        target_weight=0.0,
                        source="OPT_INTRADAY",
                        urgency=Urgency.IMMEDIATE,
                        reason=f"PREMARKET_STALE_INTRADAY_CLOSE | {self._premarket_vix_ladder_reason}",
                    )
                )

        if self._premarket_vix_ladder_level >= 3 and getattr(
            config, "PREMARKET_VIX_L3_CLOSE_ALL_OPTIONS", True
        ):
            queued = 0
            spreads = self.options_engine.get_spread_positions()
            for spread in spreads:
                self.portfolio_router.receive_signal(
                    TargetWeight(
                        symbol=self._normalize_symbol_str(spread.long_leg.symbol),
                        target_weight=0.0,
                        source="OPT",
                        urgency=Urgency.IMMEDIATE,
                        reason=f"PREMARKET_VIX_L3_FLATTEN | {self._premarket_vix_ladder_reason}",
                        requested_quantity=spread.num_spreads,
                        metadata={
                            "spread_close_short": True,
                            "spread_short_leg_symbol": self._normalize_symbol_str(
                                spread.short_leg.symbol
                            ),
                            "spread_short_leg_quantity": spread.num_spreads,
                            "exit_type": "PREMARKET_VIX_L3",
                        },
                    )
                )
                queued += 1

            intraday_pos = self.options_engine.get_intraday_position()
            if intraday_pos is not None:
                self.portfolio_router.receive_signal(
                    TargetWeight(
                        symbol=self._normalize_symbol_str(intraday_pos.contract.symbol),
                        target_weight=0.0,
                        source="OPT_INTRADAY",
                        urgency=Urgency.IMMEDIATE,
                        reason=f"PREMARKET_VIX_L3_FLATTEN | {self._premarket_vix_ladder_reason}",
                    )
                )
                queued += 1

            # Queue exits for any orphan option holdings not covered by tracked state.
            tracked_symbols = set()
            for spread in spreads:
                tracked_symbols.add(spread.long_leg.symbol)
                tracked_symbols.add(spread.short_leg.symbol)
            if intraday_pos is not None:
                tracked_symbols.add(intraday_pos.contract.symbol)

            for kvp in self.Portfolio:
                holding = kvp.Value
                if not holding.Invested or holding.Symbol.SecurityType != SecurityType.Option:
                    continue
                if holding.Symbol in tracked_symbols:
                    continue
                self.portfolio_router.receive_signal(
                    TargetWeight(
                        symbol=self._normalize_symbol_str(holding.Symbol),
                        target_weight=0.0,
                        source="OPT",
                        urgency=Urgency.IMMEDIATE,
                        reason=f"PREMARKET_VIX_L3_ORPHAN_CLOSE | {self._premarket_vix_ladder_reason}",
                    )
                )
                queued += 1

            if queued > 0:
                self.Log(
                    f"PREMARKET_LADDER: L3 queued option flatten exits | Queued={queued} | "
                    f"{self._premarket_vix_ladder_reason}"
                )
            return

        if self._premarket_vix_ladder_level >= 2 and getattr(
            config, "PREMARKET_VIX_L2_CLOSE_BULLISH_OPTIONS", True
        ):
            # Tracked spread-aware close for bullish swing structures.
            spreads = self.options_engine.get_spread_positions()
            bullish_spreads = {
                "BULL_CALL",
                "BULL_CALL_DEBIT",
                "BULL_PUT_CREDIT",
                SpreadStrategy.BULL_CALL_DEBIT.value,
                SpreadStrategy.BULL_PUT_CREDIT.value,
            }
            for spread in spreads:
                if spread.spread_type not in bullish_spreads:
                    continue
                self.portfolio_router.receive_signal(
                    TargetWeight(
                        symbol=self._normalize_symbol_str(spread.long_leg.symbol),
                        target_weight=0.0,
                        source="OPT",
                        urgency=Urgency.IMMEDIATE,
                        reason=f"PREMARKET_VIX_L2_BULLISH_SPREAD | {self._premarket_vix_ladder_reason}",
                        requested_quantity=spread.num_spreads,
                        metadata={
                            "spread_close_short": True,
                            "spread_short_leg_symbol": self._normalize_symbol_str(
                                spread.short_leg.symbol
                            ),
                            "spread_short_leg_quantity": spread.num_spreads,
                            "exit_type": "PREMARKET_VIX_L2",
                        },
                    )
                )

            # Close bullish single-leg calls.
            symbols_to_close = []
            intraday_pos = self.options_engine.get_intraday_position()
            if intraday_pos is not None and intraday_pos.contract.direction == OptionDirection.CALL:
                symbols_to_close.append(intraday_pos.contract.symbol)

            for kvp in self.Portfolio:
                holding = kvp.Value
                if not holding.Invested or holding.Symbol.SecurityType != SecurityType.Option:
                    continue
                if holding.Symbol.ID.OptionRight == OptionRight.Call:
                    symbols_to_close.append(holding.Symbol)

            # Deduplicate and close atomically (shorts first, then longs).
            if symbols_to_close:
                unique_symbols = list(dict.fromkeys(symbols_to_close))
                queued = 0
                for symbol in unique_symbols:
                    self.portfolio_router.receive_signal(
                        TargetWeight(
                            symbol=self._normalize_symbol_str(symbol),
                            target_weight=0.0,
                            source="OPT",
                            urgency=Urgency.IMMEDIATE,
                            reason=(
                                "PREMARKET_VIX_L2_CALL_DELEVER | "
                                f"{self._premarket_vix_ladder_reason}"
                            ),
                        )
                    )
                    queued += 1
                if queued > 0:
                    self.Log(
                        f"PREMARKET_LADDER: L2 de-risked bullish options | Queued={queued} | "
                        f"{self._premarket_vix_ladder_reason}"
                    )

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
        symbol_str = self._normalize_symbol_str(symbol)
        if not symbol_str:
            return 0
        try:
            for kvp in self.Portfolio:
                holding = kvp.Value
                if (
                    holding.Invested
                    and holding.Symbol.SecurityType == SecurityType.Option
                    and self._normalize_symbol_str(holding.Symbol) == symbol_str
                ):
                    return int(holding.Quantity)
        except Exception:
            return 0
        return 0

    def _intraday_force_exit_fallback(self) -> None:
        """
        V6.12: Safety net - force-close intraday position after configured close +5min if scheduled close missed.

        This prevents intraday options from carrying overnight due to scheduler issues.
        """
        # Only run once per day
        if getattr(self, "_intraday_force_exit_fallback_date", None) == self.Time.date():
            return

        # Only after configured force-close + 5 minutes.
        exit_time = getattr(config, "INTRADAY_FORCE_EXIT_TIME", "15:30")
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

        # Check if we have an intraday position
        if not hasattr(self, "options_engine") or not self.options_engine.has_intraday_position():
            self._intraday_force_exit_fallback_date = self.Time.date()
            return

        # Get current option price
        symbol = self._normalize_symbol_str(self.options_engine._intraday_position.contract.symbol)
        if symbol in self._intraday_close_in_progress_symbols:
            self._intraday_force_exit_fallback_date = self.Time.date()
            return
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
            self._intraday_force_exit_fallback_date = self.Time.date()
            return

        signal = self.options_engine.check_intraday_force_exit(
            current_hour=self.Time.hour,
            current_minute=self.Time.minute,
            current_price=price,
        )
        if signal:
            live_qty = abs(self._get_option_holding_quantity(signal.symbol))
            if live_qty > 0:
                signal.requested_quantity = live_qty
            self._intraday_close_in_progress_symbols.add(signal.symbol)
            self._intraday_force_exit_submitted_symbols[signal.symbol] = str(self.Time.date())
            self.Log(f"INTRADAY_FORCE_EXIT_FALLBACK: Triggered for {symbol}")
            self.portfolio_router.receive_signal(signal)
            self._process_immediate_signals()

        # Always mark as done after 15:35 check to prevent repeated attempts
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
        """
        V2.33: Portfolio-scan based liquidation with atomic options handling.

        V2.28's approach only handled ONE tracked spread, but multiple spreads or
        orphaned legs could exist. V2.33 scans the actual Portfolio for ALL options
        positions and closes them in the correct order:
        1. Buy back ALL short options first (eliminates naked short risk)
        2. Sell ALL long options (now safe - no naked exposure)
        3. Liquidate equity positions last

        This prevents the "long leg sold → naked short → margin rejection" death spiral
        that caused -80% loss in V2.32.

        V3.1/V6.11: Added exempt_symbols parameter to exclude hedge positions (SH)
        from GOVERNOR_SHUTDOWN liquidation. Hedges should persist overnight.
        """
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
                self.MarketOrder(symbol, close_qty, tag=reason)
                self.Log(f"{reason}: Closed short option {str(symbol)[-21:]} x{close_qty}")
            except Exception as e:
                self.Log(f"{reason}: Failed to close short {str(symbol)[-21:]} | {e}")

        # Step 2: Sell ALL long options (safe now - all shorts closed)
        for symbol, qty in long_options:
            try:
                self.MarketOrder(symbol, -qty, tag=reason)
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
                self.MarketOrder(symbol, close_qty, tag=reason)
                self.Log(f"{reason}: Closed SHORT {str(symbol)[-21:]} x{close_qty}")
            except Exception as e:
                self.Log(f"{reason}: FAILED short close {str(symbol)[-21:]} | {e}")

        # THEN close ALL longs (sell to close) - safe now, no naked shorts
        for symbol, qty in long_options:
            try:
                self.MarketOrder(symbol, -qty, tag=reason)
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
        if hasattr(self.trend_engine, "_pending_moo_symbols"):
            # V2.24: Log pending state for diagnostics
            if self.trend_engine._pending_moo_symbols:
                pending_info = ", ".join(
                    f"{sym}(since={self.trend_engine._pending_moo_dates.get(sym, '?')})"
                    for sym in self.trend_engine._pending_moo_symbols
                )
                self.Log(
                    f"TREND: PENDING_MOO_CHECK | Count={len(self.trend_engine._pending_moo_symbols)} | "
                    f"Symbols=[{pending_info}]"
                )

            stale_symbols = set()
            current_date_str = str(self.Time.date())
            for sym in self.trend_engine._pending_moo_symbols:
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
                self.trend_engine._pending_moo_symbols.discard(sym)
                self.trend_engine._pending_moo_dates.pop(sym, None)
                self.Log(
                    f"TREND: STALE_MOO_CLEARED {sym} | "
                    f"Pending but not invested at 09:33 - clearing slot"
                )

        # Reconcile positions with broker
        self._reconcile_positions()

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
            if elapsed_min < 45:
                return
        self._reconcile_positions()

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
            pending_moo_count = (
                len(self.trend_engine._pending_moo_symbols)
                if hasattr(self.trend_engine, "_pending_moo_symbols")
                else 0
            )
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
        self.cold_start_engine.end_of_day_update(
            kill_switch_triggered=self.scheduler.is_kill_switch_triggered()
        )

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

        # Save all state
        self._save_state()

        # V6.14: Cache closes for next day's pre-market VIX ladder.
        self._vix_prior_close = self._get_vix_level()
        uvxy_close = self.Securities[self.uvxy].Close if hasattr(self, "uvxy") else 0.0
        if uvxy_close > 0:
            self._uvxy_prior_close = uvxy_close

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
        self._order_lifecycle_log_count = 0
        self._recon_orphan_close_submitted.clear()
        self._last_micro_update_log_signature = None
        self._last_micro_update_log_time = None
        self._last_spread_construct_fail_log_at = None
        self._external_exec_event_logged.clear()
        # V3.0 P1-B: Clean stale pending exit orders at EOD
        if self._pending_exit_orders:
            stale_keys = [
                k
                for k, v in self._pending_exit_orders.items()
                if v.retry_count >= 3 or v.order_id is None
            ]
            for k in stale_keys:
                self._pending_exit_orders.pop(k, None)
            if stale_keys:
                self.Log(f"EOD_CLEANUP: Cleared {len(stale_keys)} stale pending exit orders")

        # NOTE: _kill_switch_handled_today is NOT reset here - it resets at 09:25 pre-market
        # Resetting here causes double-trigger since OnData runs at 16:00 after EOD handler

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

    def _get_tradeable_equity_settlement_aware(self) -> float:
        """
        V2.9: Get tradeable equity minus unsettled cash.

        This prevents 'Insufficient Funds' errors on post-holiday opens
        by subtracting Portfolio.UnsettledCash from tradeable equity.

        Returns:
            Tradeable equity adjusted for unsettled cash.
        """
        capital_state = self.capital_engine.calculate(self.Portfolio.TotalPortfolioValue)
        unsettled = self._get_unsettled_cash()
        return max(0.0, capital_state.tradeable_eq - unsettled)

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
        V2.1.1: Intraday options force close at 15:30 ET.

        Forces close of all intraday mode options positions (0-2 DTE).
        These must close 30 minutes before market close.
        """
        # Skip during warmup
        if self.IsWarmingUp:
            return
        if self._intraday_force_close_ran_date == self.Time.date():
            return
        self._intraday_force_close_ran_date = self.Time.date()

        # V2.4.4 P0: Run Expiration Hammer V2 as part of force close
        self._check_expiration_hammer_v2()

        # Check for intraday position to close
        if self.options_engine._intraday_position is not None:
            # Get current option price
            intraday_pos = self.options_engine._intraday_position
            symbol = self._normalize_symbol_str(intraday_pos.contract.symbol)

            # V2.25 Fix #4: Double-sell guard — verify position is still held
            # Prevents creating orphan shorts if limit/profit-target already closed
            try:
                if not self.Portfolio[intraday_pos.contract.symbol].Invested:
                    self.Log(
                        f"INTRADAY_FORCE_EXIT: SKIP | {symbol} already closed | "
                        f"Clearing stale _intraday_position"
                    )
                    self.options_engine._intraday_position = None
                    self._intraday_close_in_progress_symbols.discard(symbol)
                    self._intraday_force_exit_submitted_symbols.pop(symbol, None)
                    return
            except Exception:
                pass  # If symbol lookup fails, proceed with force close

            # Get current price (best effort)
            current_price = intraday_pos.entry_price  # Fallback

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
            )

            if signal:
                # Idempotency: only one force-close submit per symbol per day.
                submitted_date = self._intraday_force_exit_submitted_symbols.get(signal.symbol)
                if submitted_date == str(self.Time.date()):
                    self.Log(
                        f"INTRADAY_FORCE_EXIT: SKIP duplicate submit | {signal.symbol} | Date={submitted_date}"
                    )
                    return
                live_qty = abs(self._get_option_holding_quantity(signal.symbol))
                if live_qty <= 0:
                    self.Log(f"INTRADAY_FORCE_EXIT: SKIP no live holding | {signal.symbol}")
                    return
                signal.requested_quantity = live_qty
                self._intraday_close_in_progress_symbols.add(signal.symbol)
                self._intraday_force_exit_submitted_symbols[signal.symbol] = str(self.Time.date())
                self.portfolio_router.receive_signal(signal)
                self._process_immediate_signals()

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

        position = self.options_engine.get_intraday_position() or self.options_engine.get_position()
        if position is None or position.contract is None:
            return

        symbol = self._normalize_symbol_str(position.contract.symbol)

        # Don't recover OCO while close is in progress for this symbol.
        if symbol in self._intraday_close_in_progress_symbols:
            return

        # Skip OCO recovery in force-close window to avoid close-race amplification.
        try:
            exit_hour, exit_min = map(
                int, getattr(config, "INTRADAY_FORCE_EXIT_TIME", "15:25").split(":")
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

        # Throttle to once per day per symbol
        today = str(self.Time.date())
        last_attempt = self._last_oco_recovery_attempt.get(symbol)
        if last_attempt == today:
            return

        # Ensure we still hold the position
        try:
            qc_symbol = self.Symbol(symbol)
            holding = self.Portfolio[qc_symbol]
            if not holding.Invested:
                self._intraday_close_in_progress_symbols.discard(symbol)
                self._intraday_force_exit_submitted_symbols.pop(symbol, None)
                return
            qty = abs(int(holding.Quantity))
            if qty <= 0:
                self._intraday_close_in_progress_symbols.discard(symbol)
                self._intraday_force_exit_submitted_symbols.pop(symbol, None)
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
        )
        submitted = False
        if oco_pair:
            submitted = self.oco_manager.submit_oco_pair(oco_pair, current_time=str(self.Time))

        self._last_oco_recovery_attempt[symbol] = today
        if submitted:
            self.Log(
                f"OCO_RECOVER: Created missing OCO | {symbol} | "
                f"Stop=${position.stop_price:.2f} Target=${position.target_price:.2f} Qty={qty}"
            )
        else:
            self.Log(
                f"OCO_RECOVER: Failed to submit (market closed or error) | {symbol} | "
                f"Will retry next day"
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
            self._intraday_close_in_progress_symbols.discard(symbol)
            self._intraday_force_exit_submitted_symbols.pop(symbol, None)

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

        if swing_signals:
            for signal in swing_signals:
                self.Log(f"FRIDAY_FIREWALL: {signal.reason} | VIX={vix_current:.1f}")
                self.portfolio_router.receive_signal(signal)

            # Process immediately
            self._process_immediate_signals()
        else:
            self.Log(f"FRIDAY_FIREWALL: No action needed | VIX={vix_current:.1f}")

        # V2.29 P0: Weekly reconciliation — clear ghost spread if no options held
        self._reconcile_spread_state()

    def _reconcile_spread_state(self) -> None:
        """V2.29 P0: Weekly reconciliation — clear ghost spread if no options held."""
        if not self.options_engine.has_spread_position():
            return
        spread_count_before = len(self.options_engine.get_spread_positions())
        has_options = any(
            kvp.Value.Invested
            for kvp in self.Portfolio
            if kvp.Value.Symbol.SecurityType == SecurityType.Option
        )
        if not has_options:
            if self._should_log_backtest_category("LOG_SPREAD_RECONCILE_BACKTEST_ENABLED", False):
                self.Log("SPREAD_RECONCILE: Friday check — no options held, clearing ghost spread")
            for spread in list(self.options_engine.get_spread_positions()):
                spread_key = (
                    f"{self._normalize_symbol_str(spread.long_leg.symbol)}|"
                    f"{self._normalize_symbol_str(spread.short_leg.symbol)}"
                )
                self._clear_spread_runtime_trackers_by_key(spread_key)
            self.options_engine.clear_spread_position()
            if spread_count_before > 0:
                self._diag_spread_exit_fill_count += spread_count_before
                self._diag_spread_position_removed_count += spread_count_before
            if self.portfolio_router:
                self.portfolio_router.clear_all_spread_margins()

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
        spike_alert = self.options_engine._micro_regime_engine.check_spike_alert(
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

        state = self.options_engine._micro_regime_engine.update(
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
        micro_msg = (
            f"MICRO_UPDATE: VIX_level={vix_level_cboe:.1f}(CBOE) VIX_dir_proxy={vix_intraday_proxy:.2f} (UVXY {uvxy_change_pct:+.1f}%) | "
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

        Returns:
            CBOE VIX daily value for level classification.
        """
        return self._current_vix  # Use daily CBOE VIX, NOT UVXY-derived proxy

    def _get_vix_direction(self) -> str:
        """
        V2.11 (Pitfall #7): Get VIX DIRECTION from raw UVXY percentage change.

        Uses UVXY percentage change directly for direction classification.
        This is valid because UVXY tracks VIX direction reliably (~1.5x).
        Do NOT derive synthetic VIX level for direction - use raw UVXY %.

        Returns:
            Direction string: FALLING_FAST, FALLING, STABLE, RISING, RISING_FAST, SPIKING
        """
        if self._uvxy_at_open <= 0:
            return "STABLE"

        uvxy_current = self.Securities[self.uvxy].Price
        uvxy_change_pct = (uvxy_current - self._uvxy_at_open) / self._uvxy_at_open * 100

        # Use raw UVXY % for direction (NOT synthetic VIX level)
        if uvxy_change_pct < config.VIX_DIRECTION_FALLING_FAST:
            return "FALLING_FAST"
        elif uvxy_change_pct < config.VIX_DIRECTION_FALLING:
            return "FALLING"
        elif uvxy_change_pct <= config.VIX_DIRECTION_STABLE_HIGH:
            return "STABLE"
        elif uvxy_change_pct <= config.VIX_DIRECTION_RISING:
            return "RISING"
        elif uvxy_change_pct <= config.VIX_DIRECTION_RISING_FAST:
            return "RISING_FAST"
        else:
            return "SPIKING"

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

    def OnOrderEvent(self, orderEvent: OrderEvent) -> None:
        """
        Handle order status changes.

        Processes filled orders:
            - Logs trade details
            - Tracks trade for daily summary
            - Updates position tracking
            - Forwards to execution engine

        Handles rejected orders:
            - Logs rejection reason
            - Notifies execution engine

        Args:
            orderEvent: Order event from QuantConnect.
        """
        # V2.6 Bug #2: Handle partial fills for spread orders
        if orderEvent.Status == OrderStatus.PartiallyFilled:
            symbol = str(orderEvent.Symbol)
            fill_price = orderEvent.FillPrice
            fill_qty = orderEvent.FillQuantity

            self.Log(
                f"PARTIAL_FILL: {symbol[-20:]} | Qty={fill_qty} @ ${fill_price:.2f} | "
                f"Remaining={orderEvent.Quantity - orderEvent.FillQuantity}"
            )

            # Route partial fills to spread handler if applicable
            if "QQQ" in symbol and ("C" in symbol or "P" in symbol):
                if (
                    self._spread_fill_tracker is not None
                    or self.options_engine._pending_spread_long_leg is not None
                ):
                    # This is a spread fill - accumulate in tracker
                    self._handle_spread_leg_fill(symbol, fill_price, abs(fill_qty))

            # Notify execution engine only if the order originated there.
            self._forward_execution_event(
                order_event=orderEvent,
                status="PartiallyFilled",
                fill_price=fill_price,
                fill_quantity=fill_qty,
            )
            return  # Don't fall through to other handlers

        if orderEvent.Status == OrderStatus.Filled:
            symbol = str(orderEvent.Symbol)
            fill_price = orderEvent.FillPrice
            fill_qty = orderEvent.FillQuantity
            direction = "BUY" if fill_qty > 0 else "SELL"

            self.Log(f"FILL: {direction} {abs(fill_qty)} {symbol} @ ${fill_price:.2f}")

            # V2.14 Fix #12: SLIPPAGE_EXCEEDED check
            # Compare fill price to expected market price (bid for sells, ask for buys)
            try:
                security = self.Securities[orderEvent.Symbol]
                if fill_qty > 0:  # BUY - compare to ask
                    expected_price = security.AskPrice
                else:  # SELL - compare to bid
                    expected_price = security.BidPrice

                if expected_price > 0:
                    slippage_pct = abs(fill_price - expected_price) / expected_price
                    if slippage_pct > config.SLIPPAGE_BUFFER_PCT:
                        self.Log(
                            f"SLIPPAGE_EXCEEDED: {symbol} | "
                            f"Expected=${expected_price:.2f} Actual=${fill_price:.2f} | "
                            f"Slippage={slippage_pct:.2%} > {config.SLIPPAGE_BUFFER_PCT:.0%}"
                        )
            except Exception:
                pass  # Skip slippage check if price lookup fails

            # Track trade for daily summary
            trade_desc = f"{direction} {abs(fill_qty)} {symbol} @ ${fill_price:.2f}"
            self.today_trades.append(trade_desc)

            # Update position tracking
            self._on_fill(symbol, fill_price, fill_qty, orderEvent)

            # V2.1: Check if this is an OCO order fill
            if self.oco_manager.has_order(orderEvent.OrderId):
                self.oco_manager.on_order_fill(
                    broker_order_id=orderEvent.OrderId,
                    fill_price=fill_price,
                    fill_quantity=fill_qty,
                    fill_time=str(self.Time),
                )

            # Forward to execution engine only for mapped orders.
            self._forward_execution_event(
                order_event=orderEvent,
                status="Filled",
                fill_price=fill_price,
                fill_quantity=fill_qty,
            )

            # V2.3.6: Clean up spread tracking when short leg fills successfully
            if symbol in self._pending_spread_orders:
                long_leg = self._pending_spread_orders.pop(symbol)
                self._pending_spread_orders_reverse.pop(long_leg, None)
                self.Log(f"SPREAD: Both legs filled successfully | Short={symbol} Long={long_leg}")
                # Entry-leg tracking cleanup only.
                # Do NOT clear live spread state here; spread was just opened and must remain
                # tracked for exits/diagnostics. Clearing it here causes ghost/orphan churn.
                self.Log("SPREAD: Entry tracking map cleared after paired fill")

            # V2.4.1 FIX #8: Kill switch check on options fills
            # Kill switch may trip between signal generation and fill.
            # If active, immediately liquidate the new options position.
            # V2.4.2 FIX: Only for OPENING buys, not closing buys (buying back shorts)
            is_option = orderEvent.Symbol.SecurityType == SecurityType.Option
            if is_option and fill_qty > 0:  # BUY fills
                # Check current position AFTER fill to determine if this was opening or closing
                current_position = self.Portfolio[orderEvent.Symbol].Quantity
                # If position is now positive, this was an opening buy (new long)
                # If position is 0 or negative, this was a closing buy (covering short)
                is_opening_trade = current_position > 0

                if is_opening_trade and self.risk_engine.is_kill_switch_active():
                    self.Log(
                        f"KILL_SWITCH_ON_FILL: Options position opened while kill switch active | "
                        f"{symbol} x{fill_qty} @ ${fill_price:.2f} | LIQUIDATING IMMEDIATELY"
                    )
                    # Immediately liquidate the options position
                    self.MarketOrder(orderEvent.Symbol, -fill_qty, tag="KILL_SWITCH_ON_FILL")

            # V2.29 P0: Reconcile ghost spread after any option fill
            # Safety net: if spread state exists but neither leg is held, clear it
            if is_option and self.options_engine.has_spread_position():
                for spread in self.options_engine.get_spread_positions():
                    long_held = self.Portfolio[spread.long_leg.symbol].Invested
                    short_held = self.Portfolio[spread.short_leg.symbol].Invested
                    if not long_held and not short_held:
                        spread_key = (
                            f"{self._normalize_symbol_str(spread.long_leg.symbol)}|"
                            f"{self._normalize_symbol_str(spread.short_leg.symbol)}"
                        )
                        self._clear_spread_runtime_trackers_by_key(spread_key)
                        self.options_engine.remove_spread_position(str(spread.long_leg.symbol))
                        self._diag_spread_exit_fill_count += 1
                        self._diag_spread_position_removed_count += 1
                        if self._should_log_backtest_category(
                            "LOG_SPREAD_RECONCILE_BACKTEST_ENABLED", False
                        ):
                            self.Log("SPREAD_RECONCILE: Both legs flat — cleared ghost state")
                        if self.portfolio_router:
                            self.portfolio_router.clear_all_spread_margins()

            # V2.25 Fix #1: Exercise/Assignment Detection
            # Was unreachable dead code (elif after if Filled:). Moved inside.
            # QC backtester uses "Simulated option assignment" not "Exercise".
            try:
                order = self.Transactions.GetOrderById(orderEvent.OrderId)
                is_exercise = order.Type == OrderType.OptionExercise
            except Exception:
                is_exercise = False
            if not is_exercise:
                msg_lower = str(orderEvent.Message).lower()
                is_exercise = "exercise" in msg_lower or "assignment" in msg_lower
            if is_exercise:
                partial_signals = self.options_engine.handle_partial_assignment(
                    symbol, abs(fill_qty)
                )
                if partial_signals:
                    self.portfolio_router.receive_signals(partial_signals)
                    self.Log(
                        f"PARTIAL_ASSIGNMENT_SUBMITTED: {symbol} | "
                        f"Signals={len(partial_signals)}"
                    )

                self.Log(
                    f"EXERCISE_DETECTED: {symbol} | Qty={fill_qty} | "
                    f"Msg='{orderEvent.Message}' | "
                    f"CRITICAL: Option exercise/assignment detected"
                )
                qqq_holding = self.Portfolio[self.qqq]
                if qqq_holding.Invested:
                    # V6.4 Fix: Check for protective long PUT before liquidating
                    # If we have a spread with a long PUT that's ITM, exercise it instead
                    # of blindly liquidating QQQ at market price
                    spread = self.options_engine.get_spread_position()
                    exercised_long_put = False

                    if spread is not None and "PUT" in spread.spread_type.upper():
                        # We have a PUT spread - check if long PUT can offset
                        long_leg = spread.long_leg
                        qqq_price = self.Securities[self.qqq].Price
                        long_strike = long_leg.strike

                        # Long PUT is ITM if QQQ price < strike
                        if qqq_price < long_strike:
                            # Exercise the long PUT to sell QQQ at strike price
                            # This is better than liquidating at market when long PUT is ITM
                            try:
                                long_symbol = self.Symbol(long_leg.symbol)
                                long_holding = self.Portfolio.get(long_symbol)
                                if (
                                    long_holding
                                    and long_holding.Invested
                                    and long_holding.Quantity > 0
                                ):
                                    exercise_qty = min(
                                        int(long_holding.Quantity),
                                        abs(int(qqq_holding.Quantity / 100)),
                                    )
                                    if exercise_qty > 0:
                                        self.Log(
                                            f"EXERCISE_LONG_PUT: Exercising protective PUT instead of market liquidation | "
                                            f"Strike=${long_strike:.2f} vs Market=${qqq_price:.2f} | "
                                            f"Benefit=${(long_strike - qqq_price) * exercise_qty * 100:,.2f} | "
                                            f"Qty={exercise_qty}"
                                        )
                                        self.ExerciseOption(long_symbol, exercise_qty)
                                        exercised_long_put = True
                            except Exception as e:
                                self.Log(
                                    f"EXERCISE_LONG_PUT_ERROR: Failed to exercise long PUT: {e}"
                                )

                    if not exercised_long_put:
                        # No protective long PUT or exercise failed - liquidate at market
                        self.Log(
                            f"EXERCISE_LIQUIDATE: QQQ position from exercise | "
                            f"Qty={qqq_holding.Quantity} | Value=${qqq_holding.HoldingsValue:,.2f}"
                        )
                        self.Liquidate(self.qqq, tag="EXERCISE_LIQUIDATE")

                    # Clear spread tracking for this option
                    if symbol in self._pending_spread_orders:
                        self._pending_spread_orders.pop(symbol)

        elif orderEvent.Status == OrderStatus.Invalid:
            self.Log(f"INVALID: {orderEvent.Symbol} - {orderEvent.Message}")
            self._log_order_lifecycle_issue(orderEvent, "Invalid")
            if "Margin" in str(orderEvent.Message) or "buying power" in str(orderEvent.Message):
                self._diag_margin_reject_count += 1
            self._forward_execution_event(
                order_event=orderEvent,
                status="Invalid",
                rejection_reason=orderEvent.Message,
            )
            if self.oco_manager.has_order(orderEvent.OrderId):
                self.oco_manager.on_order_inactive(
                    broker_order_id=orderEvent.OrderId,
                    status="Invalid",
                    detail=str(orderEvent.Message),
                    event_time=str(self.Time),
                )

            # V2.4.4 P0 Fix #4: Margin Call Circuit Breaker
            # Track consecutive margin calls and enter cooldown after hitting limit
            if "Margin" in str(orderEvent.Message):
                # V6.6.1: Only count margin rejections for OPENING orders
                # Closing/liquidation rejects should NOT trigger the circuit breaker
                order = self.Transactions.GetOrderById(orderEvent.OrderId)
                is_opening = True
                counted = False
                try:
                    if order is not None:
                        current_qty = self.Portfolio[order.Symbol].Quantity
                        # If order direction is opposite current position, it's a closing order
                        if current_qty != 0 and (order.Quantity * current_qty) < 0:
                            is_opening = False
                        # Explicit liquidation/forced tags should never count
                        if order.Tag and any(
                            k in order.Tag
                            for k in (
                                "LIQUIDATE",
                                "KILL_SWITCH",
                                "KS_",
                                "GOVERNOR",
                                "MARGIN_CB",
                                "FORCE_",
                                "ORPHAN_",
                                "EMERG_",
                                "ASSIGNMENT",
                                "EXERCISE",
                            )
                        ):
                            is_opening = False
                except Exception:
                    # If we can't classify, default to counting (safer than ignoring)
                    is_opening = True

                # V6.6.1: Guard with margin utilization (avoid false positives)
                margin_stressed = True
                if self.portfolio_router and config.MARGIN_UTILIZATION_ENABLED:
                    try:
                        utilization = self.portfolio_router.get_current_margin_usage()
                        margin_stressed = utilization >= (config.MAX_MARGIN_UTILIZATION + 0.05)
                    except Exception:
                        margin_stressed = True

                if not is_opening or not margin_stressed:
                    reason = "closing/forced order" if not is_opening else "low utilization"
                    self.Log(
                        f"MARGIN_CB_SKIP: Not counting margin reject ({reason}) | "
                        f"OrderId={orderEvent.OrderId}"
                    )
                else:
                    self._margin_call_consecutive_count += 1
                    counted = True
                if (
                    counted
                    and self._margin_call_consecutive_count >= config.MARGIN_CALL_MAX_CONSECUTIVE
                    and not self._margin_cb_in_progress
                ):
                    # V2.27: Re-entry guard — MarketOrder inside OnOrderEvent can recurse
                    self._margin_cb_in_progress = True
                    # V2.12 Fix #5: LIQUIDATE positions, not just cooldown
                    # Evidence: V2.11 showed positions held overnight into gap → kill switch
                    self.Log(
                        f"MARGIN_CB_LIQUIDATE: {self._margin_call_consecutive_count} consecutive "
                        f"margin calls | Force closing all options positions"
                    )

                    # Cancel all pending options orders
                    try:
                        for order in self.Transactions.GetOpenOrders():
                            order_symbol = str(order.Symbol)
                            if "QQQ" in order_symbol and (
                                "C0" in order_symbol or "P0" in order_symbol
                            ):
                                self.Transactions.CancelOrder(order.Id)
                                self.Log(f"MARGIN_CB_LIQUIDATE: Cancelled order {order.Id}")
                    except Exception as e:
                        self.Log(f"MARGIN_CB_LIQUIDATE: Error cancelling orders | {e}")

                    # V2.28: Use spread-aware liquidation instead of blind iteration
                    # Previous code iterated Portfolio.Values in unpredictable order,
                    # which could sell long leg first → naked short → margin rejection
                    self._liquidate_all_spread_aware("MARGIN_CB_LIQUIDATE")

                    self._margin_cb_in_progress = False

                    # Clear spread tracking
                    if self.options_engine:
                        self.options_engine.clear_spread_position()

                    # V2.18.2: Clear ghost margin reservations in router
                    # Bug fix: clear_spread_position() only clears OptionsEngine state,
                    # but leaves margin reservation in router causing permanent lockout
                    if self.portfolio_router:
                        self.portfolio_router.clear_all_spread_margins()

                    # Enter cooldown
                    cooldown_hours = config.MARGIN_CALL_COOLDOWN_HOURS
                    cooldown_until = self.Time + timedelta(hours=cooldown_hours)
                    self._margin_call_cooldown_until = str(cooldown_until)
                    self.Log(f"MARGIN_CB_COOLDOWN: Until {self._margin_call_cooldown_until}")
            else:
                # Reset counter on non-margin invalid
                self._margin_call_consecutive_count = 0

            # V2.3.6 FIX: Handle spread leg failure - liquidate orphaned leg
            failed_symbol = str(orderEvent.Symbol)

            # Case 1: Short leg failed - liquidate orphaned long leg
            if failed_symbol in self._pending_spread_orders:
                long_leg_symbol = self._pending_spread_orders.pop(failed_symbol)
                self._pending_spread_orders_reverse.pop(
                    long_leg_symbol, None
                )  # V2.6: Clean reverse
                self.Log(
                    f"SPREAD: Short leg FAILED - checking long leg | "
                    f"Short={failed_symbol[-15:]} | Long={long_leg_symbol[-15:]}"
                )

                # Check if we have a position in the long leg that needs liquidating
                # V2.19 FIX: Don't iterate Securities.Keys (20K+ loop)
                # Just try to access Portfolio directly - returns default if not found
                try:
                    holding = self.Portfolio.get(long_leg_symbol)
                    if holding and holding.Invested:
                        qty = holding.Quantity
                        self.Log(
                            f"SPREAD: LIQUIDATING orphaned long leg | "
                            f"{long_leg_symbol[-20:]} x{qty}"
                        )
                        self.MarketOrder(long_leg_symbol, -qty, tag="ORPHAN_LONG")
                    else:
                        self.Log(
                            f"SPREAD: No position in long leg - no cleanup needed | "
                            f"{long_leg_symbol[-20:]}"
                        )
                except Exception as e:
                    self.Log(f"SPREAD: ERROR liquidating orphaned long leg | {e}")

            # V2.6 Bug #5: Case 2: Long leg failed - liquidate orphaned short leg
            elif failed_symbol in self._pending_spread_orders_reverse:
                short_leg_symbol = self._pending_spread_orders_reverse.pop(failed_symbol)
                self._pending_spread_orders.pop(short_leg_symbol, None)  # Clean forward mapping
                self.Log(
                    f"SPREAD: Long leg FAILED - checking short leg | "
                    f"Long={failed_symbol[-15:]} | Short={short_leg_symbol[-15:]}"
                )

                # Check if we have a position in the short leg that needs closing
                # V2.19 FIX: Don't iterate Securities.Keys (20K+ loop)
                # Just try to access Portfolio directly - returns default if not found
                try:
                    holding = self.Portfolio.get(short_leg_symbol)
                    if holding and holding.Invested:
                        qty = holding.Quantity
                        self.Log(
                            f"SPREAD: BUYING BACK orphaned short leg | "
                            f"{short_leg_symbol[-20:]} x{abs(qty)}"
                        )
                        # Short leg is negative qty, buy back means positive order
                        self.MarketOrder(short_leg_symbol, -qty, tag="ORPHAN_SHORT")
                    else:
                        self.Log(
                            f"SPREAD: No position in short leg - no cleanup needed | "
                            f"{short_leg_symbol[-20:]}"
                        )
                except Exception as e:
                    self.Log(f"SPREAD: ERROR buying back orphaned short leg | {e}")

            # V2.6 Bug #14: Check if this is a failed exit order that needs retry
            if failed_symbol in self._pending_exit_orders:
                exit_tracker = self._pending_exit_orders[failed_symbol]
                if exit_tracker.should_retry(config.EXIT_ORDER_RETRY_COUNT):
                    exit_tracker.record_attempt(str(self.Time))
                    self.Log(
                        f"EXIT_RETRY: {failed_symbol[-20:]} attempt "
                        f"{exit_tracker.retry_count}/{config.EXIT_ORDER_RETRY_COUNT}"
                    )
                    # Schedule retry after delay
                    self._schedule_exit_retry(failed_symbol)
                else:
                    # All retries exhausted - emergency close
                    self.Log(
                        f"EXIT_EMERGENCY: {failed_symbol[-20:]} all retries failed - "
                        f"forcing market close"
                    )
                    self._force_market_close(failed_symbol)
                    self._pending_exit_orders.pop(failed_symbol, None)

            # V2.20: Event-driven state recovery — notify source engine
            # Runs AFTER existing handlers (margin CB, orphan legs, exit retry)
            self._handle_order_rejection(failed_symbol, orderEvent)

        elif orderEvent.Status == OrderStatus.Canceled:
            self._log_order_lifecycle_issue(orderEvent, "Canceled")
            self._forward_execution_event(
                order_event=orderEvent,
                status="Canceled",
            )
            is_oco_cancel = self.oco_manager.has_order(orderEvent.OrderId)
            if is_oco_cancel:
                self.oco_manager.on_order_inactive(
                    broker_order_id=orderEvent.OrderId,
                    status="Canceled",
                    detail=str(getattr(orderEvent, "Message", "")),
                    event_time=str(self.Time),
                )
            # V2.20: Event-driven state recovery — notify source engine
            canceled_symbol = str(orderEvent.Symbol)
            self._queue_spread_close_retry_on_cancel(canceled_symbol, orderEvent)
            # V9.1 FIX: Skip rejection handler for OCO cancels.
            # OCO cancels occur when one leg fills (e.g., profit target hit cancels
            # the paired stop). These are normal operational events, not order failures.
            # Routing them through _handle_order_rejection incorrectly clears pending
            # state for NEW entries submitted at the same timestamp (Bug: Aug 8 2017
            # orphan position cascade — 669 contracts registered as SWING instead of
            # INTRADAY because OCO cancel wiped _pending_intraday_entry flag).
            if not is_oco_cancel:
                self._handle_order_rejection(canceled_symbol, orderEvent)

    def _should_log_backtest_category(self, config_flag: str, default: bool = True) -> bool:
        """Return whether a log category is enabled for current run mode."""
        is_live = bool(hasattr(self, "LiveMode") and self.LiveMode)
        if is_live:
            return True
        return bool(getattr(config, config_flag, default))

    def _clear_spread_runtime_trackers_by_key(self, spread_key: str) -> None:
        """Clear runtime tracker maps for a spread key (long|short)."""
        self._spread_close_trackers.pop(spread_key, None)
        self._spread_forced_close_retry.pop(spread_key, None)
        self._spread_forced_close_reason.pop(spread_key, None)
        self._spread_forced_close_cancel_counts.pop(spread_key, None)
        self._spread_forced_close_retry_cycles.pop(spread_key, None)
        self._spread_exit_mark_cache.pop(spread_key, None)

    def _log_order_lifecycle_issue(self, order_event: OrderEvent, status: str) -> None:
        """Compact attribution log for canceled/invalid orders."""
        if not self._should_log_backtest_category("LOG_ORDER_LIFECYCLE_BACKTEST_ENABLED", True):
            return
        max_per_day = int(getattr(config, "LOG_ORDER_LIFECYCLE_MAX_PER_DAY", 200))
        if self._order_lifecycle_log_count >= max_per_day:
            return
        order = self.Transactions.GetOrderById(order_event.OrderId)
        order_type = str(getattr(order, "Type", "UNKNOWN")) if order is not None else "UNKNOWN"
        raw_tag = str(getattr(order, "Tag", "") or "") if order is not None else ""
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
                tag = getattr(order, "Tag", "") if order is not None else ""
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

    def _reconcile_positions(self) -> None:
        """
        Reconcile internal position tracking with broker state.

        Called at 09:33.
        """
        try:
            if not self._is_primary_market_open():
                return
            self._last_reconcile_positions_run = self.Time
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

            intraday = self.options_engine.get_intraday_position()
            if intraday is not None:
                tracked_symbols.add(str(intraday.contract.symbol))

            single = self.options_engine.get_position()
            if single is not None:
                tracked_symbols.add(str(single.contract.symbol))

            if tracked_symbols and not option_holdings:
                spread_count_before = len(self.options_engine.get_spread_positions())
                self.options_engine.clear_all_positions()
                self._spread_close_trackers.clear()
                self._spread_forced_close_retry.clear()
                self._spread_forced_close_reason.clear()
                self._spread_forced_close_cancel_counts.clear()
                self._spread_forced_close_retry_cycles.clear()
                self._spread_exit_mark_cache.clear()
                if spread_count_before > 0:
                    # Broker is flat while engine still tracked live spreads.
                    self._diag_spread_exit_fill_count += spread_count_before
                    self._diag_spread_position_removed_count += spread_count_before
                if self.portfolio_router:
                    self.portfolio_router.clear_all_spread_margins()
                self.Log(
                    f"RECON_ZOMBIE_CLEARED: Cleared stale internal option state | "
                    f"Tracked={len(tracked_symbols)}"
                )
                tracked_symbols = set()

            orphan_symbols = [s for s in option_holdings.keys() if s not in tracked_symbols]
            for sym_str in orphan_symbols:
                try:
                    today = str(self.Time.date())
                    if self._recon_orphan_close_submitted.get(sym_str) == today:
                        continue

                    # Avoid duplicate orphan close submits while a prior order is still open.
                    has_pending_orphan_close = False
                    for open_order in self.Transactions.GetOpenOrders():
                        if str(open_order.Symbol) != sym_str:
                            continue
                        if "RECON_ORPHAN_OPTION" in str(getattr(open_order, "Tag", "") or ""):
                            has_pending_orphan_close = True
                            break
                    if has_pending_orphan_close:
                        continue

                    broker_symbol = option_symbols[sym_str]
                    holding = self.Portfolio[broker_symbol]
                    if not holding.Invested or abs(float(holding.Quantity)) <= 0:
                        self.Log(
                            f"RECON_ORPHAN_SKIP: {sym_str} | No live position at liquidation time"
                        )
                        continue
                    self.Liquidate(broker_symbol, tag="RECON_ORPHAN_OPTION")
                    self._recon_orphan_close_submitted[sym_str] = today
                    self.Log(
                        f"RECON_ORPHAN_CLOSE_SUBMITTED: {sym_str} | "
                        f"Qty={option_holdings.get(sym_str, 0)}"
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
        for signal in self.portfolio_router._pending_weights:
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
        except Exception as e:
            self.Log(f"SIGNAL_ERROR: Failed to process immediate signals - {e}")

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
            weights = self.portfolio_router._pending_weights.copy()
            self.portfolio_router._pending_weights.clear()

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

        except Exception as e:
            self.Log(f"SIGNAL_ERROR: Failed to process EOD signals - {e}")

    def _generate_trend_signals_eod(self, regime_state: RegimeState) -> None:
        """
        Generate Trend Engine signals at end of day.

        V2.3 Enhancement: Position limits to reserve capital for options.
        - Max 2 concurrent trend positions (config.MAX_CONCURRENT_TREND_POSITIONS)
        - Priority order: QLD > SSO > TNA > FAS (config.TREND_PRIORITY_ORDER)
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
        pending_moo_count = (
            len(self.trend_engine._pending_moo_symbols)
            if hasattr(self.trend_engine, "_pending_moo_symbols")
            else 0
        )
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

        # Build symbol data map for cleaner iteration
        # V2.4: Added SMA50 for structural trend exit
        # V6.11: Updated for diversified universe (UGL, UCO replace TNA, FAS)
        symbol_data = {
            "QLD": (self.qld, self.qld_ma200, self.qld_adx, self.qld_atr, self.qld_sma50),
            "SSO": (self.sso, self.sso_ma200, self.sso_adx, self.sso_atr, self.sso_sma50),
            "UGL": (self.ugl, self.ugl_ma200, self.ugl_adx, self.ugl_atr, self.ugl_sma50),
            "UCO": (self.uco, self.uco_ma200, self.uco_adx, self.uco_atr, self.uco_sma50),
            # Deprecated symbols - kept for backward compatibility
            "TNA": (self.tna, self.tna_ma200, self.tna_adx, self.tna_atr, self.tna_sma50),
            "FAS": (self.fas, self.fas_ma200, self.fas_adx, self.fas_atr, self.fas_sma50),
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
        # Defensive default to avoid NameError in minified/stale variants.
        ma50_value = 0.0
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
        chain = (
            self.CurrentSlice.OptionChains[self._qqq_option_symbol]
            if self._qqq_option_symbol in self.CurrentSlice.OptionChains
            else None
        )
        if chain is None:
            return

        # CRITICAL: Verify chain has valid contracts (not empty)
        # Chain can exist but be empty on first trading day, holidays, or data issues
        # Wrap in try-catch to handle malformed chain data gracefully
        try:
            chain_list = list(chain)
            if not chain_list:
                # V2.14 Fix #10: Log VASS_REJECTION_GHOST when chain is empty
                self.Log(
                    "VASS_REJECTION_GHOST: No contracts in SWING chain | "
                    "Strike range may be too narrow or data gap"
                )
                return
        except Exception as e:
            self.Log(f"OPTIONS_CHAIN_ERROR: Failed to iterate chain: {e}")
            return

        # Get current values
        qqq_price = self.Securities[self.qqq].Price
        adx_value = self.qqq_adx.Current.Value
        ma200_value = self.qqq_sma200.Current.Value
        ma50_value = (
            self.qqq_sma50.Current.Value
            if hasattr(self, "qqq_sma50") and self.qqq_sma50.IsReady
            else 0.0
        )
        regime_score = regime_state.smoothed_score

        # V2.1: Calculate IV rank from options chain
        iv_rank = self._calculate_iv_rank(chain)

        # V2.23: Update IVSensor BEFORE strategy selection
        # V5.3: Pass date for daily VIX tracking (conviction logic)
        current_date_str = self.Time.strftime("%Y-%m-%d")
        self.options_engine._iv_sensor.update(self._current_vix, current_date_str)

        # V5.3: Check position limits before scanning
        can_swing, swing_reason = self.options_engine.can_enter_swing()
        if not can_swing:
            # Debug log - skip in backtest to avoid log limits
            return

        # V5.3: Get VASS conviction for potential Macro override
        (
            vass_has_conviction,
            vass_direction,
            vass_reason,
        ) = self.options_engine._iv_sensor.has_conviction()

        # V5.3: Get Macro direction from regime score
        macro_direction = self.options_engine.get_macro_direction(regime_score)
        overlay_state = self.options_engine.get_regime_overlay_state(
            vix_current=self._current_vix, regime_score=regime_score
        )

        # V5.3: Resolve trade signal - VASS conviction can override Macro
        should_trade, resolved_direction, resolve_reason = self.options_engine.resolve_trade_signal(
            engine="VASS",
            engine_direction=vass_direction,
            engine_conviction=vass_has_conviction,
            macro_direction=macro_direction,
            conviction_strength=None,
            overlay_state=overlay_state,
        )

        if not should_trade:
            if "E_OVERLAY_STRESS_BULL_BLOCK" in resolve_reason:
                self._diag_overlay_block_count += 1
            # Debug log - skip in backtest to avoid log limits
            return

        if (
            bool(getattr(config, "VASS_BULL_PROFILE_BEARISH_BLOCK_ENABLED", True))
            and resolved_direction == "BEARISH"
            and float(regime_score) >= float(getattr(config, "VASS_BULL_PROFILE_REGIME_MIN", 70.0))
            and str(overlay_state).upper() in {"NORMAL", "RECOVERY"}
        ):
            self.Log(
                f"VASS_BULL_PROFILE_BLOCK: Bearish VASS blocked in strong bull profile | "
                f"Regime={float(regime_score):.1f} | Overlay={overlay_state}"
            )
            return

        # Bear hardening: do not allow bullish VASS override from NEUTRAL macro
        # when volatility is already elevated.
        if (
            resolved_direction == "BULLISH"
            and str(macro_direction).upper() == "NEUTRAL"
            and self._current_vix
            >= float(getattr(config, "VASS_NEUTRAL_BULL_OVERRIDE_MAX_VIX", 18.0))
        ):
            self.Log(
                f"VASS_CLAMP_BLOCK: Neutral macro + elevated VIX blocks bullish override | "
                f"VIX={self._current_vix:.1f} >= {float(getattr(config, 'VASS_NEUTRAL_BULL_OVERRIDE_MAX_VIX', 18.0)):.1f} | "
                f"Resolve={resolve_reason}"
            )
            return

        if "NEUTRAL_ALIGNED_HALF" in resolve_reason:
            size_multiplier *= config.NEUTRAL_ALIGNED_SIZE_MULT

        # V6.16: Overnight shock memory override for EOD VASS path.
        if (
            getattr(config, "SHOCK_MEMORY_FORCE_BEARISH_VASS", True)
            and self._is_premarket_shock_memory_active()
            and resolved_direction == "BULLISH"
        ):
            resolved_direction = "BEARISH"
            resolve_reason = f"{resolve_reason} | SHOCK_MEMORY_FORCE_BEARISH"
            self.Log(
                f"VASS_SHOCK_OVERRIDE_EOD: Forcing BEARISH | "
                f"Shock={self._get_premarket_shock_memory_pct():+.1%} | "
                f"Reason={resolve_reason}"
            )

        # V5.3: Use resolved direction (may be VASS override or Macro alignment)
        if resolved_direction == "BULLISH":
            if self._is_premarket_ladder_call_block_active():
                self.Log(
                    f"OPTIONS_EOD: CALL blocked by pre-market ladder | {self._premarket_vix_ladder_reason}"
                )
                return
            directions_to_scan = [(OptionDirection.CALL, "BULLISH")]
        else:  # BEARISH
            directions_to_scan = [(OptionDirection.PUT, "BEARISH")]

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
            if (
                self._last_vass_rejection_log is None
                or (self.Time - self._last_vass_rejection_log).total_seconds() / 60
                >= config.VASS_LOG_REJECTION_INTERVAL_MINUTES
            ):
                self.Log(
                    f"VASS_SKIPPED: Direction={direction.value} | IV_Env=NA | "
                    f"VIX={self._current_vix:.1f} | Regime={regime_score:.0f} | "
                    f"Contracts_checked=0 | Strategy={'CREDIT' if is_credit else 'DEBIT'} | "
                    f"DTE_Ranges={dte_ranges} | ReasonCode=SWING_SLOT_BLOCK | "
                    f"Reason=Swing entry not allowed | ValidationFail={swing_reason_vass}"
                )
                self._last_vass_rejection_log = self.Time
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
        if len(candidate_contracts) < 2:
            should_log = (
                self._last_vass_rejection_log is None
                or (self.Time - self._last_vass_rejection_log).total_seconds() / 60
                >= config.VASS_LOG_REJECTION_INTERVAL_MINUTES
            )
            if should_log:
                self.Log(
                    f"VASS_REJECTION: Direction={direction.value} | "
                    f"IV_Env={self.options_engine._iv_sensor.classify()} | "
                    f"VIX={self._current_vix:.1f} | Regime={regime_score:.0f} | "
                    f"Contracts_checked={len(candidate_contracts)} | "
                    f"Strategy={'CREDIT' if is_credit else 'DEBIT'} | "
                    f"DTE_Ranges={dte_ranges} | ReasonCode=INSUFFICIENT_CANDIDATES"
                )
                self._last_vass_rejection_log = self.Time
            return

        tradeable_eq = self.capital_engine.calculate(
            self.Portfolio.TotalPortfolioValue
        ).tradeable_eq
        margin_remaining = self.portfolio_router.get_effective_margin_remaining()

        if is_credit:
            # V2.23: Credit spread path
            spread_legs = self.options_engine.select_credit_spread_legs_with_fallback(
                contracts=candidate_contracts,
                strategy=strategy,
                dte_ranges=dte_ranges,
                current_time=str(self.Time),
            )
            if spread_legs is None:
                # V6.10 P3: DEBIT fallback when CREDIT fails
                fallback_enabled = getattr(config, "CREDIT_SPREAD_FALLBACK_TO_DEBIT", False)
                if fallback_enabled and direction == OptionDirection.PUT:
                    fallback_strategy = SpreadStrategy.BEAR_PUT_DEBIT
                    fallback_right = self._strategy_option_right(fallback_strategy)
                    fallback_contracts = self._build_spread_candidate_contracts(
                        chain,
                        direction,
                        dte_min=dte_min_all,
                        dte_max=dte_max_all,
                        option_right=fallback_right,
                    )
                    # For PUT direction, fall back to BEAR_PUT_DEBIT
                    self.Log(
                        f"VASS_FALLBACK: CREDIT spread failed for PUT | "
                        f"Trying BEAR_PUT_DEBIT fallback | Strategy={strategy.value}"
                    )
                    spread_legs = (
                        self.options_engine.select_spread_legs_with_fallback(
                            contracts=fallback_contracts,
                            direction=direction,
                            current_time=str(self.Time),
                            dte_ranges=dte_ranges,
                        )
                        if len(fallback_contracts) >= 2
                        else None
                    )
                    if spread_legs is not None:
                        long_leg, short_leg = spread_legs  # DEBIT returns (long, short)
                        if (
                            long_leg.bid > 0
                            and long_leg.ask > 0
                            and short_leg.bid > 0
                            and short_leg.ask > 0
                        ):
                            self.Log(
                                f"VASS_FALLBACK: DEBIT spread found | "
                                f"Long={long_leg.strike} | Short={short_leg.strike}"
                            )
                            signal = self.options_engine.check_spread_entry_signal(
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
                                portfolio_value=tradeable_eq,
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
                            if signal:
                                # V2.3.6 FIX: Check margin BEFORE submitting spread
                                if signal.metadata and signal.metadata.get(
                                    "spread_short_leg_quantity"
                                ):
                                    short_qty = signal.metadata.get("spread_short_leg_quantity", 0)
                                    short_symbol = signal.metadata.get(
                                        "spread_short_leg_symbol", ""
                                    )
                                    spread_width = getattr(config, "SPREAD_WIDTH_TARGET", 5.0)
                                    spread_margin = short_qty * spread_width * 100 * 1.5
                                    if spread_margin > margin_remaining:
                                        self.Log(
                                            f"SPREAD_MARGIN_BLOCK: DEBIT fallback insufficient margin | "
                                            f"Required=${spread_margin:,.0f} | Available=${margin_remaining:,.0f}"
                                        )
                                        return
                                self.Log(
                                    f"VASS_FALLBACK: DEBIT entry signal generated | {signal.symbol}"
                                )
                                self.Log(
                                    f"VASS_ENTRY: {signal.metadata.get('vass_strategy', 'UNKNOWN') if signal.metadata else 'UNKNOWN'} | "
                                    f"{signal.symbol} | {signal.reason}"
                                )
                                signal = self._attach_option_trace_metadata(signal, source="VASS")
                                self.portfolio_router.process_signal(signal)
                            return
                return
            short_leg, long_leg = spread_legs  # Credit returns (short, long)

            # CRITICAL: Verify both legs have valid bid/ask
            if short_leg.bid <= 0 or short_leg.ask <= 0:
                return
            if long_leg.ask <= 0:
                return

            signal = self.options_engine.check_credit_spread_entry_signal(
                regime_score=regime_score,
                vix_current=self._current_vix,
                adx_value=adx_value,
                current_price=qqq_price,
                ma200_value=ma200_value,
                iv_rank=iv_rank,
                current_hour=self.Time.hour,
                current_minute=self.Time.minute,
                current_date=str(self.Time.date()),
                portfolio_value=tradeable_eq,
                short_leg_contract=short_leg,
                long_leg_contract=long_leg,
                strategy=strategy,
                gap_filter_triggered=self.risk_engine.is_gap_filter_active(),
                vol_shock_active=self.risk_engine.is_vol_shock_active(self.Time),
                size_multiplier=size_multiplier,
                margin_remaining=margin_remaining,
                is_eod_scan=is_eod_scan,
                direction=direction,  # V6.0: Pass conviction-resolved direction
            )
        else:
            # Existing debit spread path
            # V2.24.2: Pass VASS DTE range to prevent double-filter bug
            spread_legs = self.options_engine.select_spread_legs_with_fallback(
                contracts=candidate_contracts,
                direction=direction,
                current_time=str(self.Time),
                dte_ranges=dte_ranges,
            )
            if spread_legs is None:
                return

            long_leg, short_leg = spread_legs

            # CRITICAL: Verify both legs have valid bid/ask
            if long_leg.ask <= 0:
                return
            if short_leg.bid <= 0 or short_leg.ask <= 0:
                return

            # V2.3: Check for spread entry signal
            # V2.3.20: Pass size_multiplier for cold start reduced sizing
            # V2.7: Use tradeable equity (not total portfolio) for cash-only sizing
            signal = self.options_engine.check_spread_entry_signal(
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
                portfolio_value=tradeable_eq,
                long_leg_contract=long_leg,
                short_leg_contract=short_leg,
                gap_filter_triggered=self.risk_engine.is_gap_filter_active(),
                vol_shock_active=self.risk_engine.is_vol_shock_active(self.Time),
                size_multiplier=size_multiplier,
                margin_remaining=margin_remaining,
                dte_min=vass_dte_min,
                dte_max=vass_dte_max,
                is_eod_scan=is_eod_scan,
                direction=direction,  # V6.0: Pass conviction-resolved direction
            )

        if signal:
            self.Log(
                f"VASS_ENTRY: {signal.metadata.get('vass_strategy', 'UNKNOWN') if signal.metadata else 'UNKNOWN'} | "
                f"{signal.symbol} | {signal.reason}"
            )
            signal = self._attach_option_trace_metadata(signal, source="VASS")
            signal = self._apply_spread_margin_guard(signal, source_tag="VASS_SPREAD")
            if signal is None:
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
        if getattr(config, "VASS_ENABLED", True) and self.options_engine._iv_sensor.is_ready():
            iv_environment = self.options_engine._iv_sensor.classify()
            strategy, dte_min, dte_max = self.options_engine._select_strategy(
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

        self.Log(
            f"{source_tag}: MARGIN-SIZED DOWN | "
            f"Requested={contracts_requested} -> Actual={max_contracts} contracts | "
            f"Per=${required_per_contract:,.0f} | Effective Free=${effective_free_margin:,.0f}"
        )
        return signal

    def _normalize_option_symbol(self, symbol) -> str:
        """
        V2.6 Bug #13: Normalize option symbol for consistent comparison.

        Handles different symbol formats from QC (Symbol objects vs strings)
        and ensures consistent string representation.

        Args:
            symbol: QC Symbol object, string, or any object with str() representation.

        Returns:
            Normalized symbol string (stripped, uppercase).
        """
        return str(symbol).strip().upper()

    def _get_contract_prices(self, contract) -> Tuple[float, float]:
        """
        Safely get bid/ask prices from an option contract.

        QC's historical options data might not include bid/ask quotes.
        Falls back to LastPrice if bid/ask not available.

        Args:
            contract: QC OptionContract from chain.

        Returns:
            Tuple of (bid_price, ask_price). Both 0 if no price data.
        """
        bid = getattr(contract, "BidPrice", 0) or 0
        ask = getattr(contract, "AskPrice", 0) or 0

        # Fall back to LastPrice if bid/ask not available
        if bid <= 0 or ask <= 0:
            last = getattr(contract, "LastPrice", 0) or 0
            if last > 0:
                # V2.6 Bug #11: Dynamic spread estimate based on premium
                # Lower-priced options have wider spreads (% wise)
                if last < 1.0:
                    spread_pct = 0.10  # 10% spread for < $1 options
                elif last < 5.0:
                    spread_pct = 0.05  # 5% spread for $1-$5 options
                else:
                    spread_pct = 0.02  # 2% spread for > $5 options

                half_spread = spread_pct / 2
                bid = last * (1 - half_spread)
                ask = last * (1 + half_spread)

        return (bid, ask)

    def _select_best_option_contract(self, chain) -> Optional[OptionContract]:
        """
        Select the best QQQ option contract for trading.

        Criteria (V2.1):
        - ATM or slightly ITM call
        - DTE from config (1-4 days for daily volatility harvesting)
        - Sufficient open interest
        - Tight bid-ask spread

        Args:
            chain: QuantConnect options chain.

        Returns:
            OptionContract or None if no suitable contract found.
        """
        if chain is None:
            return None

        qqq_price = self.Securities[self.qqq].Price

        # Filter for calls, ATM±2 strikes, DTE from config (V2.1: 1-4 days)
        candidates = []
        for contract in chain:
            if contract.Right != OptionRight.Call:
                continue

            # Check DTE from config (V2.1: 1-4 days for daily volatility harvesting)
            dte = (contract.Expiry - self.Time).days
            if dte < config.OPTIONS_DTE_MIN or dte > config.OPTIONS_DTE_MAX:
                continue

            # Check if ATM±2 strikes
            strike_diff = abs(contract.Strike - qqq_price)
            if strike_diff > qqq_price * 0.02:  # Within 2% of ATM
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

            # Create OptionContract object
            opt_contract = OptionContract(
                symbol=str(contract.Symbol),
                underlying="QQQ",
                direction=OptionDirection.CALL,
                strike=contract.Strike,
                expiry=str(contract.Expiry.date()),
                delta=contract.Greeks.Delta if hasattr(contract, "Greeks") else 0.5,
                gamma=contract.Greeks.Gamma if hasattr(contract, "Greeks") else 0.0,
                vega=contract.Greeks.Vega if hasattr(contract, "Greeks") else 0.0,
                theta=contract.Greeks.Theta if hasattr(contract, "Greeks") else 0.0,
                bid=bid,
                ask=ask,
                mid_price=mid_price,
                open_interest=contract.OpenInterest,
                days_to_expiry=dte,
            )

            # Score by: proximity to ATM + liquidity
            score = (1.0 / (1.0 + strike_diff)) * (1.0 - spread_pct)
            candidates.append((score, opt_contract))

        if not candidates:
            return None

        # Return best candidate
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

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

    def _select_intraday_option_contract(
        self, chain, direction: OptionDirection, strategy: IntradayStrategy = None
    ) -> Optional[OptionContract]:
        """
        V2.3.4: Select QQQ option contract for intraday mode (1-5 DTE).

        V2.14 FIX: Now strategy-aware delta selection:
        - DEBIT_FADE: Target 0.30 delta (OTM for gamma moves)
        - ITM_MOMENTUM: Target 0.70 delta (ITM for stock replacement)

        V2.3.4 FIX: Now accepts direction parameter to ensure we select
        the correct Call/Put based on the fade strategy direction.

        Criteria:
        - Matches specified direction (CALL or PUT)
        - Strategy-appropriate delta (0.30 for OTM, 0.70 for ITM)
        - DTE 1-5 days (V2.13: Weekly expiration)
        - Sufficient open interest
        - Tight bid-ask spread

        Args:
            chain: QuantConnect options chain.
            direction: Required option direction (CALL or PUT).
            strategy: V2.14 - Intraday strategy (DEBIT_FADE or ITM_MOMENTUM).

        Returns:
            OptionContract or None if no suitable contract found.
        """
        if chain is None:
            return None

        qqq_price = self.Securities[self.qqq].Price

        # V2.14 Fix #20: Strategy-aware delta selection
        if strategy == IntradayStrategy.ITM_MOMENTUM:
            target_delta = config.INTRADAY_ITM_DELTA  # 0.70 for stock replacement
            self.Log(f"INTRADAY_DELTA: ITM_MOMENTUM using delta={target_delta}")
        else:
            target_delta = config.OPTIONS_INTRADAY_DELTA_TARGET  # 0.30 for DEBIT_FADE

        # Determine which OptionRight to filter for
        required_right = OptionRight.Call if direction == OptionDirection.CALL else OptionRight.Put

        # V2.13 Fix #16: Add filter diagnostics to track why contracts are rejected
        filter_counts = {
            "direction": 0,
            "dte": 0,
            "greeks": 0,
            "delta": 0,
            "oi": 0,
            "prices": 0,
            "spread": 0,
        }
        total_contracts = 0

        # Filter for target delta, 1-5 DTE, and MATCHING DIRECTION
        candidates = []
        for contract in chain:
            total_contracts += 1

            # V2.3.4 FIX: Filter by direction FIRST
            if contract.Right != required_right:
                filter_counts["direction"] += 1
                continue

            # V6.0 P0 FIX: Triple validation - contract.Right vs Symbol.ID.OptionRight vs OCC symbol
            # This catches any internal inconsistency in the contract data
            symbol_right = contract.Symbol.ID.OptionRight
            occ_symbol = str(contract.Symbol)
            # OCC format: "QQQ   YYMMDDCSSSSSSSS" where C/P is at position after date
            occ_right_char = None
            for char in occ_symbol:
                if char in ("C", "P"):
                    occ_right_char = char
                    break

            expected_occ_char = "C" if required_right == OptionRight.Call else "P"

            # Validate all three sources agree
            if contract.Right != symbol_right:
                self.Log(
                    f"RIGHT_MISMATCH_BLOCKED: contract.Right={contract.Right} vs "
                    f"Symbol.ID.OptionRight={symbol_right} | {occ_symbol}"
                )
                filter_counts["direction"] += 1
                continue

            if occ_right_char and occ_right_char != expected_occ_char:
                self.Log(
                    f"RIGHT_MISMATCH_BLOCKED: OCC symbol has '{occ_right_char}' but "
                    f"expected '{expected_occ_char}' | {occ_symbol}"
                )
                filter_counts["direction"] += 1
                continue

            # Check DTE using config values (1-5 for intraday, V2.13)
            dte = (contract.Expiry - self.Time).days
            if dte < config.OPTIONS_INTRADAY_DTE_MIN or dte > config.OPTIONS_INTRADAY_DTE_MAX:
                filter_counts["dte"] += 1
                continue

            # V2.3: Get delta and check if within tolerance of target
            # V2.12 Fix #7: Skip contracts with missing or zero Greeks (backtest data gaps)
            if not hasattr(contract, "Greeks") or contract.Greeks.Delta == 0:
                filter_counts["greeks"] += 1
                continue  # Skip contracts without valid Greeks data
            contract_delta = abs(contract.Greeks.Delta)
            delta_diff = abs(contract_delta - target_delta)
            if delta_diff > config.OPTIONS_DELTA_TOLERANCE:
                filter_counts["delta"] += 1
                continue

            # Check liquidity (relaxed for 0DTE)
            if contract.OpenInterest < config.OPTIONS_MIN_OPEN_INTEREST:
                filter_counts["oi"] += 1
                continue

            # Check spread - use safe price getter
            bid, ask = self._get_contract_prices(contract)
            if bid <= 0 or ask <= 0:
                filter_counts["prices"] += 1
                continue

            mid_price = (bid + ask) / 2
            spread_pct = (ask - bid) / mid_price if mid_price > 0 else 1.0

            if spread_pct > config.OPTIONS_SPREAD_WARNING_PCT:
                filter_counts["spread"] += 1
                continue

            # V6.0 P0 FIX: Derive direction from ACTUAL contract.Right, not requested direction
            # This ensures OptionContract.direction matches what we're actually trading
            actual_direction = (
                OptionDirection.CALL if contract.Right == OptionRight.Call else OptionDirection.PUT
            )

            # Create OptionContract object with direction from actual contract
            opt_contract = OptionContract(
                symbol=str(contract.Symbol),
                underlying="QQQ",
                direction=actual_direction,
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

            # V2.3.4: Score by proximity to target delta (0.30) + lower DTE (prefer 0DTE) + liquidity
            delta_score = 1.0 - (delta_diff / config.OPTIONS_DELTA_TOLERANCE)
            dte_score = 1.0 / (1.0 + dte)  # Strongly prefer 0 DTE
            spread_score = 1.0 - spread_pct
            score = (delta_score * 0.4) + (dte_score * 0.4) + (spread_score * 0.2)
            candidates.append((score, opt_contract))

        if not candidates:
            # V2.13 Fix #16: Log filter diagnostics to identify Black Hole causes
            # T-17 FIX: Enhanced diagnostics with actionable insights
            # Calculate where contracts are being lost in the funnel
            passed_direction = total_contracts - filter_counts["direction"]
            passed_dte = passed_direction - filter_counts["dte"]
            passed_greeks = passed_dte - filter_counts["greeks"]
            passed_delta = passed_greeks - filter_counts["delta"]

            # Identify the primary blocker
            if filter_counts["direction"] > total_contracts * 0.9:
                primary_blocker = "DIRECTION (wrong CALL/PUT ratio in chain)"
            elif filter_counts["dte"] > passed_direction * 0.9:
                primary_blocker = f"DTE (outside {config.OPTIONS_INTRADAY_DTE_MIN}-{config.OPTIONS_INTRADAY_DTE_MAX} range)"
            elif filter_counts["greeks"] > passed_dte * 0.9:
                primary_blocker = "GREEKS (missing or zero delta data)"
            elif filter_counts["delta"] > passed_greeks * 0.5:
                primary_blocker = (
                    f"DELTA (outside {target_delta:.2f} +/- {config.OPTIONS_DELTA_TOLERANCE})"
                )
            elif filter_counts["prices"] > 0:
                primary_blocker = "PRICES (bid/ask <= 0)"
            else:
                primary_blocker = "SPREAD or OI"

            self.Log(
                f"INTRADAY_FILTER_FAIL: {direction.value} | Total={total_contracts} | "
                f"Dir={filter_counts['direction']} DTE={filter_counts['dte']} "
                f"Greeks={filter_counts['greeks']} Delta={filter_counts['delta']} "
                f"OI={filter_counts['oi']} Prices={filter_counts['prices']} "
                f"Spread={filter_counts['spread']} | "
                f"Funnel: {total_contracts}→{passed_direction}→{passed_dte}→{passed_greeks}→{passed_delta} | "
                f"Blocker={primary_blocker}"
            )
            return None

        # Return best candidate (closest to target delta with lowest DTE)
        candidates.sort(key=lambda x: x[0], reverse=True)
        best = candidates[0][1]
        # V6.0 P0 FIX: Log the ACTUAL direction from contract, not requested direction
        self.Log(
            f"INTRADAY: Selected {best.direction.value} | Strike={best.strike} | "
            f"Delta={best.delta:.2f} | DTE={best.days_to_expiry} | Mid=${best.mid_price:.2f} | "
            f"Symbol={best.symbol}"
        )
        return best

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
                self.MarketOrder(symbol, close_qty, tag="KS_SINGLE_LEG")
                self.Log(f"KS_SINGLE_LEG: Closed SHORT {str(symbol)[-21:]} x{close_qty}")
            except Exception as e:
                self.Log(f"KS_SINGLE_LEG: FAILED short close {str(symbol)[-21:]} | {e}")

        for symbol, qty in long_options:
            try:
                self.MarketOrder(symbol, -qty, tag="KS_SINGLE_LEG")
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

        V6.11: Checks Chandelier trailing stops for QLD, SSO, UGL, UCO.

        Args:
            data: Current data slice.
        """
        # Check QLD
        if self.Portfolio[self.qld].Invested:
            signal = self.trend_engine.check_stop_hit(
                symbol="QLD",
                current_price=self.Securities[self.qld].Price,
            )
            if signal:
                self.portfolio_router.receive_signal(signal)
                self._process_immediate_signals()

        # Check SSO
        if self.Portfolio[self.sso].Invested:
            signal = self.trend_engine.check_stop_hit(
                symbol="SSO",
                current_price=self.Securities[self.sso].Price,
            )
            if signal:
                self.portfolio_router.receive_signal(signal)
                self._process_immediate_signals()

        # V6.11: Check UGL (2× Gold)
        if self.Portfolio[self.ugl].Invested:
            signal = self.trend_engine.check_stop_hit(
                symbol="UGL",
                current_price=self.Securities[self.ugl].Price,
            )
            if signal:
                self.portfolio_router.receive_signal(signal)
                self._process_immediate_signals()

        # V6.11: Check UCO (2× Crude Oil)
        if self.Portfolio[self.uco].Invested:
            signal = self.trend_engine.check_stop_hit(
                symbol="UCO",
                current_price=self.Securities[self.uco].Price,
            )
            if signal:
                self.portfolio_router.receive_signal(signal)
                self._process_immediate_signals()

        # DEPRECATED V6.11: TNA/FAS kept for backward compatibility
        if self.Portfolio[self.tna].Invested:
            signal = self.trend_engine.check_stop_hit(
                symbol="TNA",
                current_price=self.Securities[self.tna].Price,
            )
            if signal:
                self.portfolio_router.receive_signal(signal)
                self._process_immediate_signals()

        if self.Portfolio[self.fas].Invested:
            signal = self.trend_engine.check_stop_hit(
                symbol="FAS",
                current_price=self.Securities[self.fas].Price,
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

    def _scan_options_signals(self, data: Slice) -> None:
        """
        V2.1.1: Scan for Options entry signals during intraday session.

        Dual-Mode Architecture:
        - Swing Mode (5-45 DTE): 4-factor entry scoring
        - Intraday Mode (0-2 DTE): Micro Regime Engine

        V2.3.20: Now allows options during cold start with 50% sizing.

        Args:
            data: Current data slice.
        """
        # Defensive default to avoid NameError in minified/stale variants.
        ma50_value = 0.0
        # V2.3.20: Calculate size multiplier for cold start
        is_cold_start = self.cold_start_engine.is_cold_start_active()
        size_multiplier = config.OPTIONS_COLD_START_MULTIPLIER if is_cold_start else 1.0
        size_multiplier *= self._premarket_vix_size_mult

        # Skip if indicators not ready
        if not self.qqq_adx.IsReady or not self.qqq_sma200.IsReady:
            return

        # V6.14: L3 freeze window blocks all new options entries intraday.
        if self._is_premarket_ladder_entry_block_active():
            if self.Time.minute % 15 == 0:
                until_h, until_m = self._premarket_vix_entry_block_until
                self.Log(
                    f"PREMARKET_LADDER_BLOCK: Options blocked until {until_h:02d}:{until_m:02d} | "
                    f"{self._premarket_vix_ladder_reason}"
                )
            return

        # V2.5 PART 19 FIX: Stateless reconciliation - detect zombie state
        # If internal tracker thinks we have a position, but portfolio is empty,
        # clear the zombie state to unblock trading
        if self.options_engine.has_position():
            actual_option_count = self._get_actual_option_count()
            if actual_option_count == 0:
                self.Log(
                    "OPT_ZOMBIE_FIX: Internal state shows position but portfolio empty - clearing zombie state"
                )
                self.options_engine.clear_all_positions()
            else:
                # If an intraday position is open, skip intraday scan
                if self.options_engine.has_intraday_position():
                    return

        # V2.3 FIX: Skip if kill switch triggered (prevents new entries after liquidation)
        if self._kill_switch_handled_today:
            # V2.3 DEBUG: Log once per day when options blocked by kill switch (live only)
            if self.Time.hour == 10 and self.Time.minute == 30 and self.LiveMode:
                self.Log("OPT_SCAN: Blocked - Kill switch handled today")
            return

        # V2.27: Skip-day enforcement after Tier 2+ kill switch
        if self.risk_engine.is_ks_skip_day(str(self.Time.date())):
            if self.Time.hour == 10 and self.Time.minute == 30:
                self.Log("OPT_SCAN: Blocked - KS skip day active")
            return

        # V2.9: Skip if in settlement cooldown (Bug #6 fix)
        if not self._can_trade_options_settlement_aware():
            return

        # V2.11 (Pitfall #6): Margin-aware sizing guard
        # Check available margin BEFORE calculating any allocation
        margin_remaining = self.portfolio_router.get_effective_margin_remaining()
        # V3.0 SCALABILITY FIX: Use percentage-based minimum (was hardcoded $1,000)
        # At $50K: 2% = $1,000, at $200K: 2% = $4,000 (scales with portfolio)
        portfolio_value = self.Portfolio.TotalPortfolioValue
        min_margin_required = portfolio_value * config.OPTIONS_MIN_MARGIN_PCT
        if margin_remaining < min_margin_required:
            if self.Time.minute == 0:  # Log once per hour to avoid spam
                self.Log(
                    f"OPT_MARGIN_GUARD: Margin ${margin_remaining:,.0f} < "
                    f"${min_margin_required:,.0f} ({config.OPTIONS_MIN_MARGIN_PCT:.0%} of portfolio) | Options blocked"
                )
            return

        # Calculate effective portfolio value capped by available margin
        # V2.19 FIX: Use min() to cap at OPTIONS_MAX_MARGIN_CAP (not subtract it)
        # V3.0 SCALABILITY FIX: Use percentage-based cap (OPTIONS_MAX_MARGIN_PCT)
        base_tradeable = self.capital_engine.calculate(
            self.Portfolio.TotalPortfolioValue
        ).tradeable_eq
        # Cap margin used for options at percentage of portfolio (scales with size)
        options_max_margin = portfolio_value * config.OPTIONS_MAX_MARGIN_PCT
        margin_available_for_options = min(margin_remaining, options_max_margin)
        # max_portfolio_from_margin = margin_available / OPTIONS_SWING_ALLOCATION
        # This ensures: effective_portfolio * OPTIONS_SWING_ALLOCATION <= margin_available
        max_portfolio_from_margin = (
            margin_available_for_options / config.OPTIONS_SWING_ALLOCATION
            if config.OPTIONS_SWING_ALLOCATION > 0
            else base_tradeable
        )
        effective_portfolio_value = min(base_tradeable, max_portfolio_from_margin)

        if effective_portfolio_value < base_tradeable:
            self.Log(
                f"OPT_MARGIN_CAP: Sizing capped by margin | "
                f"Base=${base_tradeable:,.0f} | Effective=${effective_portfolio_value:,.0f} | "
                f"Margin_remaining=${margin_remaining:,.0f}"
            )

        # V2.3.6 FIX: Scan during active window (10:00-15:00)
        # Removed 10:30 delay - momentum/credit strategies need 10:00-10:30 window
        # Strategy-specific timing (if needed) should be handled in Options Engine
        current_hour = self.Time.hour
        current_minute = self.Time.minute
        # Before 10:00 or after 15:00 -> skip
        if current_hour < 10 or current_hour >= 15:
            return

        # CRITICAL FIX: Validate options symbol is resolved before use
        if not self._validate_options_symbol():
            return

        # Get options chain
        chain = (
            data.OptionChains[self._qqq_option_symbol]
            if self._qqq_option_symbol in data.OptionChains
            else None
        )
        if chain is None:
            return

        # CRITICAL: Verify chain has valid contracts (not empty)
        # Wrap in try-catch to handle malformed chain data gracefully
        try:
            chain_list = list(chain)
            if not chain_list:
                # V2.14 Fix #10: Log VASS_REJECTION_GHOST when chain is empty
                self.Log(
                    "VASS_REJECTION_GHOST: No contracts in INTRADAY chain | "
                    "Strike range may be too narrow or data gap"
                )
                return
        except Exception as e:
            self.Log(f"OPTIONS_CHAIN_ERROR: Failed to iterate chain: {e}")
            return

        # Get current values
        qqq_price = self.Securities[self.qqq].Price
        adx_value = self.qqq_adx.Current.Value
        ma200_value = self.qqq_sma200.Current.Value
        ma50_value = (
            self.qqq_sma50.Current.Value
            if hasattr(self, "qqq_sma50") and self.qqq_sma50.IsReady
            else 0.0
        )

        # V2.4.1: Throttle intraday scanning to every 15 minutes (was every minute)
        # This reduces 95 scans/hour to 4 scans/hour
        # V2.20: Also check rejection cooldown for intraday mode
        intraday_cooldown_active = (
            self._options_intraday_cooldown_until is not None
            and self.Time < self._options_intraday_cooldown_until
        )
        if self._should_scan_intraday() and self._qqq_at_open > 0 and not intraday_cooldown_active:
            # V5.3: Check position limits before scanning
            can_intraday, intraday_limit_reason = self.options_engine.can_enter_intraday()
            if not can_intraday:
                # V6.2: Log position limit block (was silent - Bug #3 instrumentation)
                self.Log(f"INTRADAY: Blocked - {intraday_limit_reason}")
            else:
                # V2.4.1 FIX: Use UVXY-derived VIX proxy instead of stale daily VIX
                # self._current_vix is daily close and doesn't change intraday
                vix_intraday = self._get_vix_intraday_proxy()

                # V6.2: Get CBOE VIX level for consistent Micro Regime classification
                # This ensures all _micro_regime_engine.update() calls use same VIX level
                vix_level_cboe = self._get_vix_level()

                # Get macro regime score for direction conflict check
                regime_score = self._get_effective_regime_score_for_options()

                # V6.3: Calculate UVXY intraday change for conviction check
                uvxy_pct = 0.0
                if hasattr(self, "_uvxy_at_open") and self._uvxy_at_open > 0:
                    uvxy_current = self.Securities[self.uvxy].Price if hasattr(self, "uvxy") else 0
                    if uvxy_current > 0:
                        uvxy_pct = (uvxy_current - self._uvxy_at_open) / self._uvxy_at_open

                # V6.3: Unified intraday signal generation (fixes dual-update bug)
                # Single method handles: update, conviction, resolution, direction conflict
                # Replaces scattered orchestration that caused state divergence
                (
                    should_trade,
                    intraday_direction,
                    micro_state,
                    signal_reason,
                ) = self.options_engine.generate_micro_intraday_signal(
                    vix_current=vix_intraday,
                    vix_open=self._vix_at_open,
                    qqq_current=qqq_price,
                    qqq_open=self._qqq_at_open,
                    uvxy_pct=uvxy_pct,
                    macro_regime_score=regime_score,
                    current_time=str(self.Time),
                    vix_level_override=vix_level_cboe,
                    premarket_shock_pct=self._get_premarket_shock_memory_pct(),
                )

                intraday_size_multiplier = size_multiplier
                if "NEUTRAL_ALIGNED_HALF" in signal_reason:
                    intraday_size_multiplier *= config.NEUTRAL_ALIGNED_SIZE_MULT
                if "MISALIGNED_HALF" in signal_reason:
                    intraday_size_multiplier *= getattr(config, "MICRO_MISALIGNED_SIZE_MULT", 0.50)

                intraday_signal_id = (
                    f"MICRO-{self.Time.strftime('%Y%m%d-%H%M')}-"
                    f"{self._diag_intraday_candidate_count + 1}"
                )

                if not should_trade:
                    self.Log(f"INTRADAY: Blocked - {signal_reason}")
                    intraday_direction = None
                    # V6.15: Allow one retry if prior approved signal was dropped for temporary reasons.
                    if (
                        self._intraday_retry_once_pending
                        and self._intraday_retry_expires is not None
                        and self.Time <= self._intraday_retry_expires
                        and self._intraday_retry_direction is not None
                    ):
                        should_trade = True
                        intraday_direction = self._intraday_retry_direction
                        signal_reason = (
                            f"RETRY_ONCE: {self._intraday_retry_reason_code} | "
                            f"Reusing prior direction={intraday_direction.value}"
                        )
                        self._intraday_retry_once_pending = False
                        self._intraday_retry_expires = None
                        self._intraday_retry_direction = None
                        self._intraday_retry_reason_code = None
                        self.Log(f"INTRADAY_RETRY: {signal_reason}")
                else:
                    if (
                        intraday_direction == OptionDirection.CALL
                        and self._is_premarket_ladder_call_block_active()
                    ):
                        intraday_direction = None
                        signal_reason = (
                            f"PREMARKET_LADDER_CALL_BLOCK: {self._premarket_vix_ladder_reason}"
                        )
                        self.Log(f"INTRADAY: Blocked - {signal_reason}")
                    else:
                        self._diag_intraday_candidate_count += 1
                        self.Log(
                            f"INTRADAY_SIGNAL_CANDIDATE: SignalId={intraday_signal_id} | {signal_reason} | "
                            f"Direction={intraday_direction.value if intraday_direction else 'NONE'}"
                        )

                # If engine recommends NO_TRADE or conflict detected, skip contract selection
                if intraday_direction is None:
                    intraday_contract = None
                else:
                    self.Log(
                        f"INTRADAY: Proceeding with ladder size mult {self._premarket_vix_size_mult:.2f}"
                    )
                    # STEP 2: Select contract matching ENGINE recommendation (not hardcoded fade)
                    # V2.14 Fix #20: Pass strategy for delta-aware contract selection
                    intraday_strategy = self.options_engine.get_last_intraday_strategy()
                    intraday_contract = self._select_intraday_option_contract(
                        chain, intraday_direction, strategy=intraday_strategy
                    )
                    if intraday_contract is None:
                        self.Log(
                            f"INTRADAY: No contract selected | "
                            f"Dir={intraday_direction.value} | "
                            f"Strategy={intraday_strategy.value if intraday_strategy else 'NONE'}"
                        )
                        # V9.1 FIX: Emit DROPPED log for contract selection failures.
                        # Previously 124 candidates/quarter silently fell through with
                        # no INTRADAY_SIGNAL_DROPPED, making funnel analysis incomplete.
                        self.Log(
                            f"INTRADAY_SIGNAL_DROPPED: SignalId={intraday_signal_id} | Candidate rejected before order | "
                            f"Code=E_NO_CONTRACT_SELECTED | "
                            f"Reason={signal_reason} | RetryHint=None | "
                            f"Dir={intraday_direction.value} | "
                            f"Strategy={intraday_strategy.value if intraday_strategy else 'NONE'} | "
                            f"Contract=NONE"
                        )
                        self._diag_intraday_dropped_count += 1

                # V2.13 Fix #18: Log bid/ask rejection (was silent)
                if intraday_contract is not None and (
                    intraday_contract.bid <= 0 or intraday_contract.ask <= 0
                ):
                    self.Log(
                        f"INTRADAY_PRICE_REJECT: {intraday_contract.symbol} | "
                        f"Bid={intraday_contract.bid} Ask={intraday_contract.ask}"
                    )
                    # V9.1 FIX: Emit DROPPED log for bid/ask rejection
                    self.Log(
                        f"INTRADAY_SIGNAL_DROPPED: SignalId={intraday_signal_id} | Candidate rejected before order | "
                        f"Code=E_BID_ASK_INVALID | "
                        f"Reason={signal_reason} | RetryHint=None | "
                        f"Dir={intraday_direction.value if intraday_direction else 'NONE'} | "
                        f"Strategy={intraday_strategy.value if intraday_strategy else 'NONE'} | "
                        f"Contract={intraday_contract.symbol}"
                    )
                    self._diag_intraday_dropped_count += 1
                    intraday_contract = None  # Clear invalid contract

                # Verify contract has valid bid/ask before proceeding
                if intraday_contract is not None:
                    # V2.3.20: Pass size_multiplier for cold start reduced sizing
                    # V2.4.1: Pass UVXY-derived VIX proxy
                    # V2.5: Pass macro_regime_score for Grind-Up Override
                    # V2.7: Use tradeable equity (not total portfolio) for cash-only sizing
                    # V2.11: Use margin-capped effective_portfolio_value (Pitfall #6)
                    # V3.2: Pass governor_scale for intraday Governor gate
                    # V6.2: Pass CBOE VIX level for consistent classification
                    # V6.5: Pass QQQ ATR for delta-scaled stop calculation
                    qqq_atr_value = self.qqq_atr.Current.Value if self.qqq_atr.IsReady else 0.0
                    intraday_signal = self.options_engine.check_intraday_entry_signal(
                        vix_current=vix_intraday,  # V2.4.1: UVXY proxy
                        vix_open=self._vix_at_open,
                        qqq_current=qqq_price,
                        qqq_open=self._qqq_at_open,
                        current_hour=self.Time.hour,
                        current_minute=self.Time.minute,
                        current_time=str(self.Time),
                        portfolio_value=effective_portfolio_value,  # V2.11: Margin-capped
                        best_contract=intraday_contract,
                        size_multiplier=intraday_size_multiplier,
                        macro_regime_score=regime_score,
                        governor_scale=self._governor_scale,  # V3.2: Intraday Governor gate
                        direction=intraday_direction,  # V6.0: Pass resolved direction
                        vix_level_override=vix_level_cboe,  # V6.2: CBOE VIX for level
                        underlying_atr=qqq_atr_value,  # V6.5: For delta-scaled ATR stops
                        micro_state=micro_state,  # Reuse approved state; avoid second-update drift
                    )
                    if intraday_signal:
                        intraday_signal = self._attach_option_trace_metadata(
                            intraday_signal, source="MICRO"
                        )
                        self.Log(
                            f"INTRADAY_SIGNAL_APPROVED: SignalId={intraday_signal_id} | {signal_reason} | "
                            f"Direction={intraday_direction.value if intraday_direction else 'NONE'} | "
                            f"Strategy={intraday_strategy.value if intraday_strategy else 'NONE'} | "
                            f"Contract={intraday_contract.symbol if intraday_contract else 'NONE'}"
                        )
                        self._diag_intraday_approved_count += 1
                        intraday_trace_id = (
                            intraday_signal.metadata.get("trace_id", "")
                            if intraday_signal.metadata
                            else ""
                        )
                        self.portfolio_router.receive_signal(intraday_signal)
                        # V2.3.13 FIX: MUST process immediately - signal was being queued but never executed!
                        # The function returns early via swing spread path, so signal was lost
                        self._process_immediate_signals()
                        # Clear retry state after successful signal creation.
                        self._intraday_retry_once_pending = False
                        self._intraday_retry_expires = None
                        self._intraday_retry_direction = None
                        self._intraday_retry_reason_code = None
                        # Emit explicit router rejection telemetry for this trace if execution blocked.
                        if intraday_trace_id:
                            for rej in self.portfolio_router.get_last_rejections():
                                if rej.trace_id == intraday_trace_id and rej.source_tag.startswith(
                                    "MICRO"
                                ):
                                    self._diag_intraday_router_reject_count += 1
                                    self.Log(
                                        f"INTRADAY_ROUTER_REJECTED: SignalId={intraday_signal_id} | "
                                        f"Trace={rej.trace_id} | Code={rej.code} | Stage={rej.stage} | {rej.detail}"
                                    )
                                    break
                        # V2.3.3 FIX: Don't return here - allow swing check to run too
                        # Previously returned early, blocking swing spreads entirely
                    else:
                        # P1 Fix: Explicitly log approved signals that failed to produce an order
                        if should_trade:
                            # V6.15: Canonical drop reason coding for audit/debug.
                            drop_code = "E_INTRADAY_NO_SIGNAL_UNCLASSIFIED"
                            (
                                intraday_validation_reason,
                                intraday_validation_detail,
                            ) = self.options_engine.pop_last_intraday_validation_failure()
                            (
                                can_retry_now,
                                retry_reason_now,
                            ) = self.options_engine.can_enter_intraday()
                            retry_code_now = (retry_reason_now or "").split(":", 1)[0].strip()
                            if intraday_validation_reason:
                                drop_code = intraday_validation_reason
                            elif not can_retry_now:
                                drop_code = retry_code_now or "R_SLOT_LIMIT"
                            elif self._options_intraday_cooldown_until and (
                                self.Time < self._options_intraday_cooldown_until
                            ):
                                drop_code = "R_COOLDOWN_INTRADAY"
                            elif self._margin_cb_in_progress or self._margin_call_cooldown_until:
                                drop_code = "R_MARGIN_CB_ACTIVE"
                            elif self.options_engine.has_intraday_position():
                                drop_code = "R_DUPLICATE_INTRADAY_POSITION"
                            elif intraday_contract is None:
                                drop_code = "E_INTRADAY_NO_CONTRACT"
                            elif intraday_direction is None:
                                drop_code = "E_INTRADAY_NO_DIRECTION"

                            drop_code = self._canonical_options_reason_code(drop_code)
                            validation_detail_fragment = (
                                f"ValidationDetail={intraday_validation_detail} | "
                                if intraday_validation_detail
                                else ""
                            )
                            self.Log(
                                f"INTRADAY_SIGNAL_DROPPED: SignalId={intraday_signal_id} | Candidate rejected before order | "
                                f"Code={drop_code} | "
                                f"Reason={signal_reason} | RetryHint={retry_reason_now} | "
                                f"{validation_detail_fragment}"
                                f"Dir={intraday_direction.value if intraday_direction else 'NONE'} | "
                                f"Strategy={intraday_strategy.value if intraday_strategy else 'NONE'} | "
                                f"Contract={intraday_contract.symbol if intraday_contract else 'NONE'}"
                            )
                            self._diag_intraday_dropped_count += 1
                            # V6.15: One retry on next eligible scan for temporary drop causes only.
                            if drop_code in {
                                "R_SLOT_TOTAL_MAX",
                                "R_SLOT_INTRADAY_MAX",
                                "R_COOLDOWN_INTRADAY",
                                "R_MARGIN_CB_ACTIVE",
                            }:
                                self._intraday_retry_once_pending = True
                                self._intraday_retry_expires = self.Time + timedelta(minutes=20)
                                self._intraday_retry_direction = intraday_direction
                                self._intraday_retry_reason_code = drop_code
                                self.Log(
                                    f"INTRADAY_RETRY_QUEUED: Code={drop_code} | "
                                    f"Expires={self._intraday_retry_expires.strftime('%H:%M')}"
                                )

        # V2.20: Check rejection cooldowns for swing/spread modes
        swing_cooldown_active = (
            self._options_swing_cooldown_until is not None
            and self.Time < self._options_swing_cooldown_until
        )
        spread_cooldown_active = (
            self._options_spread_cooldown_until is not None
            and self.Time < self._options_spread_cooldown_until
        )
        if swing_cooldown_active and spread_cooldown_active:
            return  # Both modes in cooldown, skip entire swing/spread scan
        if spread_cooldown_active:
            # Only spread is in cooldown — skip spread path but allow single-leg swing
            pass  # Will be checked again below before spread entry

        # V2.3: Check for SWING mode entry using SPREADS
        # V2.23: VASS routes to credit or debit based on IV environment
        # - Regime > 60: Bull (Call Debit or Put Credit depending on IV)
        # - Regime < 45: Bear (Put Debit or Call Credit depending on IV)
        # - Regime 45-60: No trade (neutral)
        # V2.24: Throttle swing spread scans to once per 15min (was 30min V2.19, was every minute before)
        if hasattr(self, "_last_swing_scan_time") and self._last_swing_scan_time is not None:
            minutes_since = (self.Time - self._last_swing_scan_time).total_seconds() / 60
            if minutes_since < 15:
                return
        self._last_swing_scan_time = self.Time

        # V2.23: Update IVSensor BEFORE strategy selection
        # V5.3: Pass date for daily VIX tracking (conviction logic)
        current_date_str = self.Time.strftime("%Y-%m-%d")
        self.options_engine._iv_sensor.update(self._current_vix, current_date_str)

        regime_score = self._get_effective_regime_score_for_options()

        # V6.0: Add VASS conviction resolution to intraday path (matches EOD path)
        # Get VASS conviction for potential Macro override
        (
            vass_has_conviction,
            vass_direction,
            vass_reason,
        ) = self.options_engine._iv_sensor.has_conviction()

        # Get Macro direction from regime score
        macro_direction = self.options_engine.get_macro_direction(regime_score)
        overlay_state = self.options_engine.get_regime_overlay_state(
            vix_current=self._current_vix, regime_score=regime_score
        )

        # Resolve trade signal - VASS conviction can override Macro
        should_trade, resolved_direction, resolve_reason = self.options_engine.resolve_trade_signal(
            engine="VASS",
            engine_direction=vass_direction,
            engine_conviction=vass_has_conviction,
            macro_direction=macro_direction,
            conviction_strength=None,
            overlay_state=overlay_state,
        )

        if not should_trade:
            if "E_OVERLAY_STRESS_BULL_BLOCK" in resolve_reason:
                self._diag_overlay_block_count += 1
            # No trade - either NEUTRAL macro with no conviction, or conflict without conviction
            return

        if (
            bool(getattr(config, "VASS_BULL_PROFILE_BEARISH_BLOCK_ENABLED", True))
            and resolved_direction == "BEARISH"
            and float(regime_score) >= float(getattr(config, "VASS_BULL_PROFILE_REGIME_MIN", 70.0))
            and str(overlay_state).upper() in {"NORMAL", "RECOVERY"}
        ):
            self.Log(
                f"VASS_BULL_PROFILE_BLOCK_INTRADAY: Bearish VASS blocked in strong bull profile | "
                f"Regime={float(regime_score):.1f} | Overlay={overlay_state}"
            )
            return

        # Bear hardening: clamp bullish VASS override in elevated VIX when macro is neutral.
        if (
            resolved_direction == "BULLISH"
            and str(macro_direction).upper() == "NEUTRAL"
            and self._current_vix
            >= float(getattr(config, "VASS_NEUTRAL_BULL_OVERRIDE_MAX_VIX", 18.0))
        ):
            self.Log(
                f"VASS_CLAMP_BLOCK_INTRADAY: Neutral macro + elevated VIX blocks bullish override | "
                f"VIX={self._current_vix:.1f} >= {float(getattr(config, 'VASS_NEUTRAL_BULL_OVERRIDE_MAX_VIX', 18.0)):.1f} | "
                f"Resolve={resolve_reason}"
            )
            return

        if "NEUTRAL_ALIGNED_HALF" in resolve_reason:
            size_multiplier *= config.NEUTRAL_ALIGNED_SIZE_MULT

        # V6.16: If overnight shock memory is active, force VASS into defensive direction.
        # This prevents bullish spread selection immediately after large overnight VIX shock.
        if (
            getattr(config, "SHOCK_MEMORY_FORCE_BEARISH_VASS", True)
            and self._is_premarket_shock_memory_active()
            and resolved_direction == "BULLISH"
        ):
            resolved_direction = "BEARISH"
            resolve_reason = f"{resolve_reason} | SHOCK_MEMORY_FORCE_BEARISH"
            self.Log(
                f"VASS_SHOCK_OVERRIDE: Forcing BEARISH | "
                f"Shock={self._get_premarket_shock_memory_pct():+.1%} | "
                f"Reason={resolve_reason}"
            )

        # Use resolved direction (may be VASS override or Macro alignment)
        if resolved_direction == "BULLISH":
            direction = OptionDirection.CALL
            direction_str = "BULLISH"
        else:  # BEARISH
            direction = OptionDirection.PUT
            direction_str = "BEARISH"

        # V6.14: L2 call block applies to VASS path too.
        if direction == OptionDirection.CALL and self._is_premarket_ladder_call_block_active():
            if self.Time.minute % 15 == 0:
                until_h, until_m = self._premarket_vix_call_block_until
                self.Log(
                    f"VASS_BLOCKED: CALL blocked until {until_h:02d}:{until_m:02d} | "
                    f"{self._premarket_vix_ladder_reason}"
                )
            return

        if vass_has_conviction:
            self.Log(
                f"OPTIONS_VASS_CONVICTION_INTRADAY: {vass_reason} | Macro={macro_direction} | "
                f"Resolved={resolved_direction} | {resolve_reason}"
            )

        # V2.23: VASS strategy selection — routes to credit or debit
        strategy, vass_dte_min, vass_dte_max, is_credit = self._route_vass_strategy(
            direction_str=direction_str,
            overlay_state=overlay_state,
        )
        dte_ranges = self._build_vass_dte_fallbacks(vass_dte_min, vass_dte_max)
        dte_min_all = min(r[0] for r in dte_ranges)
        dte_max_all = max(r[1] for r in dte_ranges)

        required_right = self._strategy_option_right(strategy)

        # V2.23: Build candidate contracts with widest VASS DTE range (fallback uses subranges)
        candidate_contracts = self._build_spread_candidate_contracts(
            chain,
            direction,
            dte_min=dte_min_all,
            dte_max=dte_max_all,
            option_right=required_right,
        )
        if len(candidate_contracts) < 2:
            return

        # Calculate IV rank from options chain (V2.1)
        iv_rank = self._calculate_iv_rank(chain)

        signal = None
        rejection_code = "UNKNOWN"

        if not spread_cooldown_active:
            if is_credit:
                # V2.23: Credit spread path
                spread_legs = self.options_engine.select_credit_spread_legs_with_fallback(
                    contracts=candidate_contracts,
                    strategy=strategy,
                    dte_ranges=dte_ranges,
                    current_time=str(self.Time),
                )
                if spread_legs is not None:
                    short_leg, long_leg = spread_legs  # Credit returns (short, long)
                    rejection_code = "CREDIT_ENTRY_VALIDATION_FAILED"
                    if (
                        short_leg.bid > 0
                        and short_leg.ask > 0
                        and long_leg.bid > 0
                        and long_leg.ask > 0
                    ):
                        signal = self.options_engine.check_credit_spread_entry_signal(
                            regime_score=regime_score,
                            vix_current=self._current_vix,
                            adx_value=adx_value,
                            current_price=qqq_price,
                            ma200_value=ma200_value,
                            iv_rank=iv_rank,
                            current_hour=self.Time.hour,
                            current_minute=self.Time.minute,
                            current_date=str(self.Time.date()),
                            portfolio_value=effective_portfolio_value,
                            short_leg_contract=short_leg,
                            long_leg_contract=long_leg,
                            strategy=strategy,
                            gap_filter_triggered=self.risk_engine.is_gap_filter_active(),
                            vol_shock_active=self.risk_engine.is_vol_shock_active(self.Time),
                            size_multiplier=size_multiplier,
                            margin_remaining=margin_remaining,
                            direction=direction,
                        )
                else:
                    rejection_code = "CREDIT_LEG_SELECTION_FAILED"
                    # V6.10 P3: DEBIT fallback when CREDIT fails (intraday path)
                    fallback_enabled = getattr(config, "CREDIT_SPREAD_FALLBACK_TO_DEBIT", False)
                    if fallback_enabled and direction == OptionDirection.PUT:
                        fallback_strategy = SpreadStrategy.BEAR_PUT_DEBIT
                        fallback_right = self._strategy_option_right(fallback_strategy)
                        fallback_contracts = self._build_spread_candidate_contracts(
                            chain,
                            direction,
                            dte_min=dte_min_all,
                            dte_max=dte_max_all,
                            option_right=fallback_right,
                        )
                        self.Log(
                            f"VASS_FALLBACK_INTRADAY: CREDIT spread failed for PUT | "
                            f"Trying BEAR_PUT_DEBIT fallback | Strategy={strategy.value}"
                        )
                        debit_spread_legs = (
                            self.options_engine.select_spread_legs_with_fallback(
                                contracts=fallback_contracts,
                                direction=direction,
                                current_time=str(self.Time),
                                dte_ranges=dte_ranges,
                            )
                            if len(fallback_contracts) >= 2
                            else None
                        )
                        if debit_spread_legs is not None:
                            rejection_code = "DEBIT_ENTRY_VALIDATION_FAILED"
                            long_leg, short_leg = debit_spread_legs
                            if long_leg.ask > 0 and short_leg.bid > 0 and short_leg.ask > 0:
                                self.Log(
                                    f"VASS_FALLBACK_INTRADAY: DEBIT spread found | "
                                    f"Long={long_leg.strike} | Short={short_leg.strike}"
                                )
                                signal = self.options_engine.check_spread_entry_signal(
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
                                    portfolio_value=effective_portfolio_value,
                                    long_leg_contract=long_leg,
                                    short_leg_contract=short_leg,
                                    gap_filter_triggered=self.risk_engine.is_gap_filter_active(),
                                    vol_shock_active=self.risk_engine.is_vol_shock_active(
                                        self.Time
                                    ),
                                    size_multiplier=size_multiplier,
                                    margin_remaining=margin_remaining,
                                    dte_min=vass_dte_min,
                                    dte_max=vass_dte_max,
                                    direction=direction,
                                )
                        else:
                            rejection_code = "DEBIT_FALLBACK_LEG_SELECTION_FAILED"
            else:
                # Existing debit spread path
                # V2.24.2: Pass VASS DTE range to prevent double-filter bug
                spread_legs = self.options_engine.select_spread_legs_with_fallback(
                    contracts=candidate_contracts,
                    direction=direction,
                    current_time=str(self.Time),
                    dte_ranges=dte_ranges,
                )
                if spread_legs is not None:
                    rejection_code = "DEBIT_ENTRY_VALIDATION_FAILED"
                    long_leg, short_leg = spread_legs
                    if long_leg.ask > 0 and short_leg.bid > 0 and short_leg.ask > 0:
                        signal = self.options_engine.check_spread_entry_signal(
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
                            portfolio_value=effective_portfolio_value,
                            long_leg_contract=long_leg,
                            short_leg_contract=short_leg,
                            gap_filter_triggered=self.risk_engine.is_gap_filter_active(),
                            vol_shock_active=self.risk_engine.is_vol_shock_active(self.Time),
                            size_multiplier=size_multiplier,
                            margin_remaining=margin_remaining,
                            dte_min=vass_dte_min,
                            dte_max=vass_dte_max,
                            direction=direction,  # V6.0: Pass conviction-resolved direction
                        )
                else:
                    rejection_code = "DEBIT_LEG_SELECTION_FAILED"
        else:
            rejection_code = "SPREAD_COOLDOWN_ACTIVE"

        if signal:
            self._diag_spread_entry_signal_count += 1
            self.Log(
                f"VASS_ENTRY: {signal.metadata.get('vass_strategy', 'UNKNOWN') if signal.metadata else 'UNKNOWN'} | "
                f"{signal.symbol} | {signal.reason}"
            )
            signal = self._attach_option_trace_metadata(signal, source="VASS")
            vass_trace_id = signal.metadata.get("trace_id", "") if signal.metadata else ""
            signal = self._apply_spread_margin_guard(signal, source_tag="VASS_INTRADAY_SPREAD")
            if signal is None:
                return
            self._diag_spread_entry_submit_count += 1
            self.portfolio_router.receive_signal(signal)
            # V2.3.13 FIX: MUST process immediately - spread signals use IMMEDIATE urgency
            self._process_immediate_signals()
            if vass_trace_id:
                for rej in self.portfolio_router.get_last_rejections():
                    if rej.trace_id == vass_trace_id and rej.source_tag.startswith("VASS"):
                        self.Log(
                            f"VASS_ROUTER_REJECTED: Trace={rej.trace_id} | "
                            f"Code={rej.code} | Stage={rej.stage} | {rej.detail}"
                        )
                        break
            return  # Spread trade placed, don't try single-leg

        if signal is None and not spread_cooldown_active:
            self._diag_vass_block_count += 1
            # V2.10 (Pitfall #4): Throttled VASS rejection logging
            # Log rejections every 15 min for visibility, not every candle to avoid spam
            should_log = (
                self._last_vass_rejection_log is None
                or (self.Time - self._last_vass_rejection_log).total_seconds() / 60
                >= config.VASS_LOG_REJECTION_INTERVAL_MINUTES
            )
            if should_log:
                fail_stats = (
                    self.options_engine.pop_last_credit_failure_stats()
                    if is_credit
                    else self.options_engine.pop_last_spread_failure_stats()
                )
                validation_reason = self.options_engine.pop_last_entry_validation_failure()
                iv_env = (
                    self.options_engine._iv_sensor.classify()
                    if hasattr(self.options_engine, "_iv_sensor")
                    else "UNKNOWN"
                )
                skip_reasons = {
                    "R_SLOT_SWING_MAX",
                    "R_SLOT_TOTAL_MAX",
                    "R_SLOT_DIRECTION_MAX",
                    "R_COOLDOWN_DIRECTIONAL",
                }
                log_prefix = (
                    "VASS_SKIPPED" if (validation_reason in skip_reasons) else "VASS_REJECTION"
                )
                reason_code = self._canonical_options_reason_code(
                    validation_reason or rejection_code
                )
                reason_text = "No contracts met spread criteria (DTE/delta/credit)"
                if validation_reason in {
                    "R_SLOT_SWING_MAX",
                    "R_SLOT_DIRECTION_MAX",
                }:
                    reason_text = "Skipped - existing spread position"
                elif validation_reason == "R_SLOT_TOTAL_MAX":
                    reason_text = "Skipped - total options slot limit reached"
                elif validation_reason == "R_COOLDOWN_DIRECTIONAL":
                    reason_text = "Skipped - entry attempt limit reached"
                elif validation_reason == "R_MARGIN_PRECHECK":
                    reason_text = "Skipped - margin precheck failed"
                elif validation_reason and validation_reason.startswith("R_CONTRACT_QUALITY:"):
                    reason_text = (
                        "Rejected - contract quality: " + validation_reason.split(":", 1)[1]
                    )
                elif validation_reason == "WIN_RATE_GATE_BLOCK":
                    reason_text = "Skipped - win-rate gate shutoff active"
                elif validation_reason == "TRADE_LIMIT_BLOCK":
                    reason_text = "Skipped - daily trade limit reached"
                self.Log(
                    f"{log_prefix}: Direction={direction.value} | "
                    f"IV_Env={iv_env} | VIX={self._current_vix:.1f} | "
                    f"Regime={regime_score:.0f} | "
                    f"Contracts_checked={len(candidate_contracts)} | "
                    f"Strategy={'CREDIT' if is_credit else 'DEBIT'} | "
                    f"DTE_Ranges={dte_ranges} | "
                    f"ReasonCode={reason_code} | "
                    f"Reason={reason_text}"
                    + (f" | FailStats={fail_stats}" if fail_stats else "")
                    + (
                        f" | ValidationFail={validation_reason}"
                        if (not fail_stats and validation_reason)
                        else ""
                    )
                )
                self._last_vass_rejection_log = self.Time

        # V2.4.1: Single-leg swing fallback - DISABLED by default for safety
        # Single-leg has higher delta exposure and full premium at risk
        # If spread fails, stay cash (Safety First approach)
        # V2.20: Also check swing cooldown
        can_swing, _ = self.options_engine.can_enter_swing()
        if config.SWING_FALLBACK_ENABLED and can_swing and not swing_cooldown_active:
            best_contract = self._select_swing_option_contract(chain, direction)
            if best_contract is not None and best_contract.bid > 0 and best_contract.ask > 0:
                # V2.3.20: Pass size_multiplier for cold start reduced sizing
                signal = self.options_engine.check_entry_signal(
                    adx_value=adx_value,
                    current_price=qqq_price,
                    ma200_value=ma200_value,
                    iv_rank=iv_rank,
                    best_contract=best_contract,
                    current_hour=self.Time.hour,
                    current_minute=self.Time.minute,
                    current_date=str(self.Time.date()),
                    portfolio_value=self.Portfolio.TotalPortfolioValue,
                    regime_score=regime_score,
                    gap_filter_triggered=self.risk_engine.is_gap_filter_active(),
                    vol_shock_active=self.risk_engine.is_vol_shock_active(self.Time),
                    size_multiplier=size_multiplier,
                    direction=direction,  # V6.0: Pass conviction-resolved direction
                )

                if signal:
                    self.Log(f"SWING_FALLBACK: Single-leg {direction.value} after spread failure")
                    self.portfolio_router.receive_signal(signal)
                    self._process_immediate_signals()
        elif not config.SWING_FALLBACK_ENABLED and can_swing:
            throttle_min = int(getattr(config, "SPREAD_CONSTRUCTION_FAIL_LOG_INTERVAL_MINUTES", 60))
            is_live = bool(hasattr(self, "LiveMode") and self.LiveMode)
            backtest_logs_enabled = bool(
                getattr(config, "SPREAD_CONSTRUCTION_FAIL_LOG_BACKTEST_ENABLED", False)
            )
            if (not is_live) and (not backtest_logs_enabled):
                return
            should_log = (
                self._last_spread_construct_fail_log_at is None
                or (self.Time - self._last_spread_construct_fail_log_at).total_seconds() / 60.0
                >= throttle_min
            )
            if should_log:
                self.Log("SWING: Spread construction failed - staying cash (fallback disabled)")
                self._last_spread_construct_fail_log_at = self.Time

    def _monitor_risk_greeks(self, data: Slice) -> None:
        """
        V2.1/V2.3: Monitor options Greeks and spread exit conditions.

        CRITICAL FIX: Fetches FRESH Greeks from OptionChain each bar.
        Greeks change rapidly for short-dated options (0-2 DTE) and stale
        Greeks can miss critical risk breaches.

        V2.3: Also checks spread exit conditions:
        - Take profit at 50% of max profit
        - Close by 5 DTE (avoid gamma acceleration)
        - Regime reversal

        Updates risk engine with current Greeks and checks for breaches:
        - Delta > 0.80 (too deep ITM)
        - Gamma > 0.05 (high gamma risk)
        - Vega > 0.50 (vol exposure too high)
        - Theta < -0.02 (excessive time decay)

        Args:
            data: Current data slice.
        """
        # Skip if no options position
        if not self.options_engine.has_position():
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
            # Emit exit signal for options position
            signal = TargetWeight(
                symbol="QQQ_OPT",
                target_weight=0.0,
                source="RISK",
                urgency=Urgency.IMMEDIATE,
                reason=f"GREEKS_BREACH: {', '.join(reasons)}",
            )
            self.portfolio_router.receive_signal(signal)
            return  # Exit already triggered, don't check other exits

        # V2.3.11: Check expiring options force exit (15:45 for 0 DTE)
        # CRITICAL: Prevents auto-exercise of ITM options held into close
        position = self.options_engine.get_position()
        intraday_position = self.options_engine._intraday_position
        any_position = position or intraday_position

        if any_position is not None:
            # Get contract expiry date
            contract_expiry = self._get_option_expiry_date(any_position.contract.symbol, data)
            current_date = str(self.Time.date())
            current_price = self._get_option_current_price(any_position.contract.symbol, data)

            if current_price is not None and contract_expiry is not None:
                signal = self.options_engine.check_expiring_options_force_exit(
                    current_date=current_date,
                    current_hour=self.Time.hour,
                    current_minute=self.Time.minute,
                    current_price=current_price,
                    contract_expiry_date=contract_expiry,
                )
                if signal is not None:
                    self.portfolio_router.receive_signal(signal)
                    return  # Force exit takes priority, skip other exit checks

        # V2.3.10: Check single-leg exit signals (profit target, stop, DTE exit)
        # This prevents options from being held to expiration/exercise
        if position is not None:
            # Get current option price from chain
            current_price = self._get_option_current_price(position.contract.symbol, data)
            current_dte = self._get_option_current_dte(position.contract.symbol, data)

            if current_price is not None:
                signal = self.options_engine.check_exit_signals(
                    current_price=current_price,
                    current_dte=current_dte,
                )
                if signal is not None:
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
        # If OCO was cancelled (199 events in 2022), position bled until 15:25 forced exit.
        if intraday_position is not None:
            intra_price = self._get_option_current_price(intraday_position.contract.symbol, data)
            intra_dte = self._get_option_current_dte(intraday_position.contract.symbol, data)

            if intra_price is not None:
                signal = self.options_engine.check_exit_signals(
                    current_price=intra_price,
                    current_dte=intra_dte,
                    position=intraday_position,
                )
                if signal is not None:
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

    def _get_option_current_price(self, symbol: str, data: Slice) -> Optional[float]:
        """
        V2.3.10: Get current price for an option from the chain.

        Args:
            symbol: Option symbol string.
            data: Current data slice.

        Returns:
            Current mid price or None if not available.
        """
        if self._qqq_option_symbol is None:
            return None

        chain = (
            data.OptionChains[self._qqq_option_symbol]
            if self._qqq_option_symbol in data.OptionChains
            else None
        )
        if chain is None:
            return None

        try:
            for contract in chain:
                if str(contract.Symbol) == symbol:
                    # Return mid price
                    bid = contract.BidPrice
                    ask = contract.AskPrice
                    if bid > 0 and ask > 0:
                        return (bid + ask) / 2
                    elif contract.LastPrice > 0:
                        return contract.LastPrice
                    break
        except Exception:
            pass

        return None

    def _get_option_current_dte(self, symbol: str, data: Slice) -> Optional[int]:
        """
        V2.3.10: Get current days to expiration for an option.

        Args:
            symbol: Option symbol string.
            data: Current data slice.

        Returns:
            Days to expiration or None if not available.
        """
        if self._qqq_option_symbol is None:
            return None

        chain = (
            data.OptionChains[self._qqq_option_symbol]
            if self._qqq_option_symbol in data.OptionChains
            else None
        )
        if chain is None:
            return None

        try:
            for contract in chain:
                if str(contract.Symbol) == symbol:
                    # Calculate DTE from expiry
                    expiry = contract.Expiry
                    current_date = self.Time.date()
                    dte = (expiry.date() - current_date).days
                    return max(0, dte)  # Ensure non-negative
        except Exception:
            pass

        return None

    def _get_option_expiry_date(self, symbol: str, data: Slice) -> Optional[str]:
        """
        V2.3.11: Get expiry date for an option as a string (YYYY-MM-DD).

        Used for expiring options force exit check.

        Args:
            symbol: Option symbol string.
            data: Current data slice.

        Returns:
            Expiry date as string (YYYY-MM-DD) or None if not available.
        """
        if self._qqq_option_symbol is None:
            return None

        chain = (
            data.OptionChains[self._qqq_option_symbol]
            if self._qqq_option_symbol in data.OptionChains
            else None
        )
        if chain is None:
            return None

        try:
            for contract in chain:
                if str(contract.Symbol) == symbol:
                    # Return expiry date as string
                    return str(contract.Expiry.date())
        except Exception:
            pass

        return None

    def _cancel_spread_linked_oco(self, long_symbol: str, short_symbol: str, reason: str) -> None:
        """
        Cancel any OCO siblings tied to spread legs before forced-close retries.

        Spread exits use combo/sequential flow, so stale leg-level OCO orders can
        race forced closes and create cancel/reject loops.
        """
        if not hasattr(self, "oco_manager"):
            return
        for leg_symbol in (long_symbol, short_symbol):
            try:
                if self.oco_manager.cancel_by_symbol(leg_symbol, reason=reason):
                    self.Log(f"SPREAD_OCO_CANCEL: {leg_symbol} | Reason={reason}")
            except Exception as e:
                self.Log(f"SPREAD_OCO_CANCEL_ERROR: {leg_symbol} | {e}")

    def _check_spread_exit(self, data: Slice) -> None:
        """
        V2.3: Check for spread exit conditions.

        Exit conditions:
        1. Take profit at 50% of max profit
        2. Close by 5 DTE (avoid gamma acceleration)
        3. Regime reversal (Bull exit if < 45, Bear exit if > 60)

        Args:
            data: Current data slice.
        """
        # Plumbing guard: never emit spread-exit orders while the primary market is closed.
        # Off-hours time-stop signals were creating submit->reconcile churn and masking true fill flow.
        if not self._is_primary_market_open():
            return

        spreads = self.options_engine.get_spread_positions()
        if not spreads:
            return
        active_spread_keys = set()
        for s in spreads:
            active_spread_keys.add(
                f"{self._normalize_symbol_str(s.long_leg.symbol)}|"
                f"{self._normalize_symbol_str(s.short_leg.symbol)}"
            )
        for key in list(self._spread_forced_close_retry.keys()):
            if key not in active_spread_keys:
                self._spread_forced_close_retry.pop(key, None)
                self._spread_forced_close_reason.pop(key, None)
                self._spread_forced_close_cancel_counts.pop(key, None)
                self._spread_forced_close_retry_cycles.pop(key, None)
        for key in list(self._spread_close_trackers.keys()):
            if key not in active_spread_keys:
                self._spread_close_trackers.pop(key, None)
        for key in list(self._spread_exit_mark_cache.keys()):
            if key not in active_spread_keys:
                self._spread_exit_mark_cache.pop(key, None)

        underlying_price = self.Securities["QQQ"].Price
        current_hour = self.Time.hour
        current_minute = self.Time.minute
        available_margin = self.Portfolio.MarginRemaining
        regime_score = self._get_effective_regime_score_for_options()

        for spread in spreads:
            long_symbol = self._normalize_symbol_str(spread.long_leg.symbol)
            short_symbol = self._normalize_symbol_str(spread.short_leg.symbol)
            spread_key = f"{long_symbol}|{short_symbol}"

            # V6.21: If a spread close was canceled, keep retrying close until flat.
            retry_at = self._spread_forced_close_retry.get(spread_key)
            if retry_at is not None and self.Time >= retry_at:
                retry_reason = self._spread_forced_close_reason.get(
                    spread_key, "CANCELED_CLOSE_RETRY"
                )
                # D1 fix: cancel any linked OCO orders before each retry/escalation cycle.
                self._cancel_spread_linked_oco(
                    long_symbol, short_symbol, reason="SPREAD_CLOSE_RETRY"
                )
                retry_cycles = self._spread_forced_close_retry_cycles.get(spread_key, 0) + 1
                self._spread_forced_close_retry_cycles[spread_key] = retry_cycles
                max_retry_cycles = int(getattr(config, "SPREAD_CLOSE_MAX_RETRY_CYCLES", 12))
                if retry_cycles >= max_retry_cycles:
                    self._diag_spread_close_escalation_count += 1
                    self._diag_spread_exit_signal_count += 1
                    self._diag_spread_exit_submit_count += 1
                    self.Log(
                        f"SPREAD_RETRY_MAX: Escalating to emergency sequential close | "
                        f"Long={long_symbol} Short={short_symbol} | "
                        f"Cycles={retry_cycles}/{max_retry_cycles} | Reason={retry_reason}"
                    )
                    try:
                        close_ok = self.portfolio_router.execute_spread_close(
                            spread=spread,
                            reason=f"SPREAD_RETRY_MAX:{retry_reason}",
                            is_emergency=True,
                        )
                        if not close_ok:
                            raise RuntimeError("execute_spread_close returned False")
                    except Exception as e:
                        # D5 fix: terminal close failure enters safe-lock retry loop
                        # instead of silently abandoning the position.
                        safe_retry_min = int(
                            getattr(config, "SPREAD_CLOSE_SAFE_LOCK_RETRY_MIN", 10)
                        )
                        self.Log(
                            f"SAFE_LOCK_ALERT: Emergency close failed | Long={long_symbol} "
                            f"Short={short_symbol} | Error={e} | RetryIn={safe_retry_min}m"
                        )
                        self._spread_forced_close_reason[
                            spread_key
                        ] = f"SAFE_LOCK_RETRY:{retry_reason}"
                        self._spread_forced_close_retry[spread_key] = self.Time + timedelta(
                            minutes=safe_retry_min
                        )
                        # Keep retrying at emergency cadence.
                        self._spread_forced_close_retry_cycles[spread_key] = max(
                            max_retry_cycles - 1, 0
                        )
                        continue
                    self._spread_forced_close_retry.pop(spread_key, None)
                    self._spread_forced_close_reason.pop(spread_key, None)
                    self._spread_forced_close_cancel_counts.pop(spread_key, None)
                    self._spread_forced_close_retry_cycles.pop(spread_key, None)
                    continue
                self.Log(
                    f"SPREAD_RETRY: Re-submitting forced close | Long={long_symbol} "
                    f"Short={short_symbol} | Reason={retry_reason} | "
                    f"Cycle={retry_cycles}/{max_retry_cycles}"
                )
                self._diag_spread_exit_signal_count += 1
                self._diag_spread_exit_submit_count += 1
                self.portfolio_router.receive_signal(
                    TargetWeight(
                        symbol=long_symbol,
                        target_weight=0.0,
                        source="OPT",
                        urgency=Urgency.IMMEDIATE,
                        reason=f"SPREAD_CLOSE_RETRY:{retry_reason}",
                        requested_quantity=spread.num_spreads,
                        metadata={
                            "spread_close_short": True,
                            "spread_short_leg_symbol": short_symbol,
                            "spread_short_leg_quantity": spread.num_spreads,
                        },
                    )
                )
                # Backoff retries to reduce order spam while preserving persistence.
                retry_minutes = int(getattr(config, "SPREAD_CLOSE_RETRY_INTERVAL_MIN", 5))
                self._spread_forced_close_retry[spread_key] = self.Time + timedelta(
                    minutes=retry_minutes
                )
                continue

            current_dte = spread.long_leg.days_to_expiry
            try:
                if spread.long_leg.expiry:
                    from datetime import datetime

                    expiry_date = datetime.strptime(spread.long_leg.expiry, "%Y-%m-%d").date()
                    current_dte = (expiry_date - self.Time.date()).days
                    spread.long_leg.days_to_expiry = current_dte
                    spread.short_leg.days_to_expiry = current_dte
            except Exception as e:
                self.Log(f"SPREAD_EXIT_WARNING: Failed to parse spread expiry date: {e}")

            if (
                current_dte <= 0
                and self.Time.hour == config.ZERO_DTE_FORCE_EXIT_HOUR
                and self.Time.minute >= config.ZERO_DTE_FORCE_EXIT_MINUTE
            ):
                self.Log(
                    f"0DTE_FIREWALL: Forcing exit 30 min before close | "
                    f"Spread={spread.spread_type} | Time={self.Time.strftime('%H:%M')}"
                )
                self.portfolio_router.receive_signal(
                    TargetWeight(
                        symbol=self._normalize_symbol_str(spread.long_leg.symbol),
                        target_weight=0.0,
                        source="OPT",
                        urgency=Urgency.IMMEDIATE,
                        reason="0DTE_TIME_DECAY",
                        requested_quantity=spread.num_spreads,
                        metadata={
                            "spread_close_short": True,
                            "spread_short_leg_symbol": self._normalize_symbol_str(
                                spread.short_leg.symbol
                            ),
                            "spread_short_leg_quantity": spread.num_spreads,
                        },
                    )
                )
                self._diag_spread_exit_signal_count += 1
                self._diag_spread_exit_submit_count += 1
                continue

            long_leg_price = None
            short_leg_price = None
            try:
                long_sec = self.Securities.get(spread.long_leg.symbol)
                if long_sec:
                    if long_sec.BidPrice > 0 and long_sec.AskPrice > 0:
                        long_leg_price = (long_sec.BidPrice + long_sec.AskPrice) / 2
                    elif long_sec.Price > 0:
                        long_leg_price = long_sec.Price
                short_sec = self.Securities.get(spread.short_leg.symbol)
                if short_sec:
                    if short_sec.BidPrice > 0 and short_sec.AskPrice > 0:
                        short_leg_price = (short_sec.BidPrice + short_sec.AskPrice) / 2
                    elif short_sec.Price > 0:
                        short_leg_price = short_sec.Price
            except Exception:
                pass

            if long_leg_price is None or short_leg_price is None:
                cached = self._spread_exit_mark_cache.get(spread_key, {})
                if long_leg_price is None:
                    long_leg_price = cached.get("long_leg_price")
                if short_leg_price is None:
                    short_leg_price = cached.get("short_leg_price")
                if long_leg_price is not None and short_leg_price is not None:
                    self.Log(
                        f"SPREAD_EXIT_MARK_FALLBACK: Using cached leg marks | "
                        f"Type={spread.spread_type} | DTE={current_dte}"
                    )
                else:
                    # Time-based exits must still fire even when option quotes are unavailable.
                    # This avoids holding stale spreads simply because leg marks are missing.
                    time_exit_reason = None
                    try:
                        max_hold_days = int(getattr(config, "VASS_DEBIT_MAX_HOLD_DAYS", 0))
                        low_vix_days = int(
                            getattr(config, "VASS_DEBIT_MAX_HOLD_DAYS_LOW_VIX", max_hold_days)
                        )
                        low_vix_threshold = float(
                            getattr(config, "VASS_DEBIT_LOW_VIX_THRESHOLD", 16.0)
                        )
                        if (
                            self._current_vix is not None
                            and low_vix_days > 0
                            and float(self._current_vix) < low_vix_threshold
                        ):
                            max_hold_days = (
                                min(max_hold_days, low_vix_days)
                                if max_hold_days > 0
                                else low_vix_days
                            )
                        if max_hold_days > 0:
                            from datetime import datetime as _dt

                            entry_dt = _dt.strptime(spread.entry_time[:19], "%Y-%m-%d %H:%M:%S")
                            held_days = (self.Time.date() - entry_dt.date()).days
                            if held_days >= max_hold_days:
                                time_exit_reason = (
                                    f"SPREAD_TIME_STOP_NO_QUOTE ({held_days}d >= {max_hold_days}d)"
                                )
                    except Exception:
                        pass
                    if time_exit_reason is None and current_dte <= int(config.SPREAD_DTE_EXIT):
                        time_exit_reason = f"DTE_EXIT_NO_QUOTE ({current_dte} DTE <= {int(config.SPREAD_DTE_EXIT)})"

                    if time_exit_reason is not None:
                        self.Log(
                            f"SPREAD_EXIT_NO_QUOTE_TIME_BASED: {time_exit_reason} | "
                            f"Type={spread.spread_type}"
                        )
                        self.portfolio_router.receive_signal(
                            TargetWeight(
                                symbol=long_symbol,
                                target_weight=0.0,
                                source="OPT",
                                urgency=Urgency.IMMEDIATE,
                                reason=f"SPREAD_EXIT: {time_exit_reason}",
                                requested_quantity=spread.num_spreads,
                                metadata={
                                    "spread_close_short": True,
                                    "spread_short_leg_symbol": short_symbol,
                                    "spread_short_leg_quantity": spread.num_spreads,
                                    "exit_type": "TIME_BASED_NO_QUOTE",
                                },
                            )
                        )
                        self._diag_spread_exit_signal_count += 1
                        self._diag_spread_exit_submit_count += 1
                        continue

                    self.Log(
                        f"SPREAD_EXIT_SKIPPED_NO_QUOTE: Missing leg marks | "
                        f"Type={spread.spread_type} | DTE={current_dte} | "
                        f"LongPx={'NA' if long_leg_price is None else f'{long_leg_price:.2f}'} | "
                        f"ShortPx={'NA' if short_leg_price is None else f'{short_leg_price:.2f}'}"
                    )
                    continue

            self._spread_exit_mark_cache[spread_key] = {
                "long_leg_price": float(long_leg_price),
                "short_leg_price": float(short_leg_price),
                "updated_at": self.Time.strftime("%Y-%m-%d %H:%M:%S"),
            }

            min_hold_minutes = int(getattr(config, "SPREAD_MIN_HOLD_MINUTES", 0))
            hold_block_active = False
            if min_hold_minutes > 0:
                try:
                    from datetime import datetime as _dt

                    entry_dt = _dt.strptime(spread.entry_time[:19], "%Y-%m-%d %H:%M:%S")
                    live_minutes = (self.Time - entry_dt).total_seconds() / 60.0
                    mandatory_dte = int(getattr(config, "SPREAD_FORCE_CLOSE_DTE", 1))
                    hold_block_active = (
                        0 <= live_minutes < min_hold_minutes and current_dte > mandatory_dte
                    )
                except Exception:
                    hold_block_active = False

            gamma_pin_signals = self.options_engine.check_gamma_pin_exit(
                current_price=underlying_price,
                current_dte=current_dte,
                spread_override=spread,
            )
            if gamma_pin_signals:
                for signal in gamma_pin_signals:
                    self.portfolio_router.receive_signal(signal)
                self._diag_spread_exit_signal_count += len(gamma_pin_signals)
                self._diag_spread_exit_submit_count += len(gamma_pin_signals)
                continue

            assignment_risk_signals = self.options_engine.check_assignment_risk_exit(
                underlying_price=underlying_price,
                current_dte=current_dte,
                current_hour=current_hour,
                current_minute=current_minute,
                available_margin=available_margin,
                spread_override=spread,
            )
            if assignment_risk_signals:
                for signal in assignment_risk_signals:
                    self.portfolio_router.receive_signal(signal)
                self._diag_spread_exit_signal_count += len(assignment_risk_signals)
                self._diag_spread_exit_submit_count += len(assignment_risk_signals)
                continue

            # V6.22: Transition exit priority - close wrong-way bullish spreads first in STRESS.
            # Apply this only when anti-churn minimum-hold window has elapsed.
            if not hold_block_active:
                overlay_state = self.options_engine.get_regime_overlay_state(
                    vix_current=self._current_vix, regime_score=regime_score
                )
                spread_type_upper = str(spread.spread_type).upper()
                is_bullish_spread = spread_type_upper in {
                    "BULL_CALL",
                    "BULL_CALL_DEBIT",
                    "BULL_PUT_CREDIT",
                }
                if overlay_state == "STRESS" and is_bullish_spread:
                    self.Log(
                        f"SPREAD_OVERLAY_EXIT: Forcing close in STRESS | "
                        f"Type={spread.spread_type} | VIX={self._current_vix:.1f} | Regime={regime_score:.0f}"
                    )
                    self.portfolio_router.receive_signal(
                        TargetWeight(
                            symbol=long_symbol,
                            target_weight=0.0,
                            source="OPT",
                            urgency=Urgency.IMMEDIATE,
                            reason="SPREAD_EXIT: OVERLAY_STRESS_EXIT",
                            requested_quantity=spread.num_spreads,
                            metadata={
                                "spread_close_short": True,
                                "spread_short_leg_symbol": short_symbol,
                                "spread_short_leg_quantity": spread.num_spreads,
                                "exit_type": "OVERLAY_STRESS_EXIT",
                            },
                        )
                    )
                    self._diag_spread_exit_signal_count += 1
                    self._diag_spread_exit_submit_count += 1
                    continue

            exit_signals = self.options_engine.check_spread_exit_signals(
                long_leg_price=long_leg_price,
                short_leg_price=short_leg_price,
                regime_score=regime_score,
                current_dte=current_dte,
                vix_current=self._current_vix,
                spread_override=spread,
            )
            if exit_signals:
                for signal in exit_signals:
                    self.portfolio_router.receive_signal(signal)
                self._diag_spread_exit_signal_count += len(exit_signals)
                self._diag_spread_exit_submit_count += len(exit_signals)

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

    def _validate_options_symbol_UNUSED(self) -> bool:
        """DEPRECATED - Chain validation moved to calling functions."""
        # Already validated in this session
        if self._qqq_options_validated:
            return True

        # Check if symbol is set
        if self._qqq_option_symbol is None:
            return False

        # Track validation attempts (log only periodically to avoid spam)
        self._qqq_options_validation_attempts += 1

        # Try to get the options chain using CurrentSlice
        try:
            if self.CurrentSlice is None:
                return False
            chain = (
                self.CurrentSlice.OptionChains[self._qqq_option_symbol]
                if self._qqq_option_symbol in self.CurrentSlice.OptionChains
                else None
            )
            if chain is None:
                # Chain not available yet - retry next bar
                if self._qqq_options_validation_attempts == 1:
                    self.Log("OPTIONS_VALIDATION: Chain not available, will retry")
                return False

            # Try to iterate chain (catches malformed data)
            chain_list = list(chain)
            if not chain_list:
                if self._qqq_options_validation_attempts == 1:
                    self.Log("OPTIONS_VALIDATION: Chain empty, will retry")
                return False

            # Validation successful
            self._qqq_options_validated = True
            self.Log(
                f"OPTIONS_VALIDATION: Symbol validated after "
                f"{self._qqq_options_validation_attempts} attempts, "
                f"{len(chain_list)} contracts available"
            )
            return True

        except Exception as e:
            if self._qqq_options_validation_attempts == 1:
                self.Log(f"OPTIONS_VALIDATION: Error validating symbol: {e}")
            return False

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
            self._intraday_regime_updated_at = self.Time
            self.Log(
                f"REGIME_REFRESH_INTRADAY: Score={self._intraday_regime_score:.1f} | "
                f"Time={self.Time.strftime('%H:%M')}"
            )
        except Exception as e:
            self.Log(f"REGIME_REFRESH_INTRADAY_ERROR: {e}")

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

    def _log_daily_summary(self) -> None:
        """
        Log end of day summary.

        Includes P&L, trades, safeguards, and regime state.
        """
        ending_equity = self.Portfolio.TotalPortfolioValue
        regime_score = float(
            self._last_regime_score
            if self._last_regime_score is not None
            else self.regime_engine.get_previous_score()
        )
        if regime_score >= config.REGIME_RISK_ON:
            regime_state_label = RegimeLevel.RISK_ON.value
        elif regime_score >= config.REGIME_NEUTRAL:
            regime_state_label = RegimeLevel.NEUTRAL.value
        elif regime_score >= config.REGIME_CAUTIOUS:
            regime_state_label = RegimeLevel.CAUTIOUS.value
        elif regime_score >= config.REGIME_DEFENSIVE:
            regime_state_label = RegimeLevel.DEFENSIVE.value
        else:
            regime_state_label = RegimeLevel.RISK_OFF.value
        capital_state = self.capital_engine.calculate(ending_equity)

        summary = self.scheduler.get_day_summary(
            starting_equity=self.equity_sod,
            ending_equity=ending_equity,
            trades=self.today_trades,
            safeguards=self.today_safeguards,
            moo_orders=[],  # Already logged when submitted
            regime_score=regime_score,
            regime_state=regime_state_label,
            days_running=self.cold_start_engine.get_days_running(),
        )

        self.Log(summary)
        self.Log(
            "OPTIONS_DIAG_SUMMARY: "
            f"Candidates={self._diag_intraday_candidate_count} | "
            f"Approved={self._diag_intraday_approved_count} | "
            f"Dropped={self._diag_intraday_dropped_count} | "
            f"RouterRejects={self._diag_intraday_router_reject_count} | "
            f"Results={self._diag_intraday_result_count} | "
            f"VASS_Blocks={self._diag_vass_block_count} | "
            f"OverlayBlocks={self._diag_overlay_block_count} | "
            f"OverlaySlotBlocks={self._diag_overlay_slot_block_count} | "
            f"SpreadCloseEscalations={self._diag_spread_close_escalation_count} | "
            f"SpreadEntrySignal={self._diag_spread_entry_signal_count} | "
            f"SpreadEntrySubmit={self._diag_spread_entry_submit_count} | "
            f"SpreadEntryFill={self._diag_spread_entry_fill_count} | "
            f"SpreadExitSignal={self._diag_spread_exit_signal_count} | "
            f"SpreadExitSubmit={self._diag_spread_exit_submit_count} | "
            f"SpreadExitFill={max(self._diag_spread_exit_fill_count, self._diag_spread_position_removed_count)} | "
            f"SpreadExitCanceled={self._diag_spread_exit_canceled_count} | "
            f"SpreadRemoved={self._diag_spread_position_removed_count} | "
            f"MarginRejects={self._diag_margin_reject_count}"
        )

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
            if symbol in self.trend_engine._pending_moo_symbols:
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
            # Check pending state to determine sub-mode (most specific first)
            if self.options_engine._pending_spread_long_leg is not None:
                # V2.21: Parse broker margin for adaptive retry sizing
                self._parse_and_store_rejection_margin(order_event)
                self.options_engine.cancel_pending_spread_entry()
                if self.portfolio_router:
                    self.portfolio_router.clear_all_spread_margins()
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
            elif self.options_engine._pending_intraday_entry:
                self.options_engine.cancel_pending_intraday_entry()
                # Cooldown: 15 minutes before intraday can retry
                self._options_intraday_cooldown_until = self.Time + timedelta(minutes=15)
                self.Log(
                    f"OPT_MICRO_RECOVERY: Intraday rejected | Pending + counter cleared | "
                    f"Cooldown 15min until {self._options_intraday_cooldown_until}"
                )
            elif self.options_engine._pending_contract is not None:
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

        Stores safety_factor * Free Margin as options_engine._rejection_margin_cap.
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
                self.options_engine._rejection_margin_cap = cap
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

    def _on_fill(
        self,
        symbol: str,
        fill_price: float,
        fill_qty: float,
        order_event: OrderEvent,
    ) -> None:
        """
        Handle order fill event.

        Updates position tracking in relevant engines.

        Args:
            symbol: Symbol that was filled.
            fill_price: Fill price.
            fill_qty: Fill quantity (positive=buy, negative=sell).
            order_event: Original order event.
        """
        # Update trend engine - V6.11: Updated for diversified universe
        if symbol in config.TREND_SYMBOLS:
            if fill_qty > 0:
                # Get ATR for initial stop calculation - V6.11: Added UGL, UCO
                atr_value = 0.0
                if symbol == "QLD" and self.qld_atr.IsReady:
                    atr_value = self.qld_atr.Current.Value
                elif symbol == "SSO" and self.sso_atr.IsReady:
                    atr_value = self.sso_atr.Current.Value
                elif symbol == "UGL" and self.ugl_atr.IsReady:
                    atr_value = self.ugl_atr.Current.Value
                elif symbol == "UCO" and self.uco_atr.IsReady:
                    atr_value = self.uco_atr.Current.Value
                # Deprecated but kept for backward compatibility
                elif symbol == "TNA" and self.tna_atr.IsReady:
                    atr_value = self.tna_atr.Current.Value
                elif symbol == "FAS" and self.fas_atr.IsReady:
                    atr_value = self.fas_atr.Current.Value
                self.trend_engine.register_entry(
                    symbol=symbol,
                    entry_price=fill_price,
                    entry_date=str(self.Time.date()),
                    atr=atr_value,
                    strategy_tag="TREND",
                )
            else:
                # V6.12: Record trade P&L before removing position
                position = self.trend_engine.get_position(symbol)
                if position and hasattr(self, "pnl_tracker"):
                    self.pnl_tracker.record_trade(
                        symbol=symbol,
                        engine="TREND",
                        entry_date=position.entry_date,
                        exit_date=str(self.Time.date()),
                        entry_price=position.entry_price,
                        exit_price=fill_price,
                        quantity=abs(int(fill_qty)),
                    )
                self.trend_engine.remove_position(symbol)

        # Update MR engine - V6.11: Updated to include SPXL
        if symbol in config.MR_SYMBOLS:
            try:
                if fill_qty > 0:
                    # Use current price as VWAP approximation
                    vwap = (
                        self.Securities[symbol].Price
                        if hasattr(self, symbol.lower())
                        else fill_price
                    )
                    self.mr_engine.register_entry(
                        symbol=symbol,
                        entry_price=fill_price,
                        entry_time=str(self.Time),
                        vwap=vwap,
                    )
                else:
                    # V6.12: Record trade P&L before removing position
                    mr_position = self.mr_engine.get_position()
                    if mr_position and hasattr(self, "pnl_tracker"):
                        entry_date = mr_position.get("entry_time", str(self.Time))[:10]
                        self.pnl_tracker.record_trade(
                            symbol=symbol,
                            engine="MR",
                            entry_date=entry_date,
                            exit_date=str(self.Time.date()),
                            entry_price=mr_position.get("entry_price", fill_price),
                            exit_price=fill_price,
                            quantity=abs(int(fill_qty)),
                        )
                    self.mr_engine.remove_position()
            except Exception as e:
                self.Log(f"MR_TRACK_ERROR: {symbol}: {e}")

        # V2.1/V2.3: Update Options engine if QQQ option
        symbol_norm = self._normalize_symbol_str(symbol)
        if "QQQ" in symbol_norm and ("C" in symbol_norm or "P" in symbol_norm):
            try:
                # V2.5 FIX: Check spread mode FIRST (before fill_qty sign check)
                # Bull Call Spread: Long leg = BUY (qty > 0), Short leg = SELL (qty < 0)
                # The bug was: "if fill_qty > 0" excluded short leg fills entirely!
                if self.options_engine._pending_spread_long_leg is not None:
                    # Spread mode: track ANY leg fill (long=positive, short=negative)
                    # Use abs(fill_qty) because _handle_spread_leg_fill expects positive qty
                    self._handle_spread_leg_fill(symbol, fill_price, abs(fill_qty))
                elif fill_qty > 0:
                    # V6.12 FIX: Check if this is a BUY to close a spread short leg
                    # Short leg close = BUY (fill_qty > 0), but it's an EXIT not an entry
                    is_spread_short_close = False
                    for spread in self.options_engine.get_spread_positions():
                        if (
                            spread.short_leg
                            and self._normalize_symbol_str(spread.short_leg.symbol) == symbol_norm
                        ):
                            is_spread_short_close = True
                            break
                    if is_spread_short_close:
                        # This is a short leg close (BUY to close)
                        self._handle_spread_leg_close(symbol, fill_price, fill_qty)
                    else:
                        # Single-leg entry (legacy or intraday)
                        position = self.options_engine.register_entry(
                            fill_price=fill_price,
                            entry_time=str(self.Time),
                            current_date=str(self.Time.date()),
                        )

                        if position:
                            # Create OCO pair for stop and profit exits
                            oco_pair = self.oco_manager.create_oco_pair(
                                symbol=symbol,
                                entry_price=fill_price,
                                stop_price=position.stop_price,
                                target_price=position.target_price,
                                quantity=int(fill_qty),
                                current_date=str(self.Time.date()),
                            )

                            if oco_pair:
                                # Submit OCO orders
                                self.oco_manager.submit_oco_pair(
                                    oco_pair, current_time=str(self.Time)
                                )
                                self.Log(
                                    f"OPT: OCO pair created | "
                                    f"Stop=${position.stop_price:.2f} | "
                                    f"Target=${position.target_price:.2f}"
                                )
                            # Record intraday entry snapshot for robust exit accounting.
                            if self.options_engine.has_intraday_position():
                                self._intraday_entry_snapshot[symbol_norm] = {
                                    "entry_price": position.entry_price,
                                    "entry_time": position.entry_time,
                                    "quantity": abs(int(fill_qty)),
                                }
                elif fill_qty < 0:
                    # Exit routing must be symbol-aware because spread + intraday can coexist.
                    is_spread_leg = False
                    for spread in self.options_engine.get_spread_positions():
                        spread_long_norm = (
                            self._normalize_symbol_str(spread.long_leg.symbol)
                            if spread.long_leg
                            else ""
                        )
                        spread_short_norm = (
                            self._normalize_symbol_str(spread.short_leg.symbol)
                            if spread.short_leg
                            else ""
                        )
                        if symbol_norm in {spread_long_norm, spread_short_norm}:
                            is_spread_leg = True
                            break
                    if is_spread_leg:
                        # Spread exit - track leg closes
                        self._handle_spread_leg_close(symbol, fill_price, fill_qty)
                    elif self.options_engine.has_intraday_position():
                        # P0 fix: keep intraday state until symbol is fully flat.
                        intraday_pos = self.options_engine.get_intraday_position()
                        intraday_symbol_norm = (
                            self._normalize_symbol_str(intraday_pos.contract.symbol)
                            if intraday_pos is not None and intraday_pos.contract is not None
                            else ""
                        )
                        live_qty_after_fill = abs(self._get_option_holding_quantity(symbol))
                        if intraday_symbol_norm and symbol_norm == intraday_symbol_norm:
                            if live_qty_after_fill > 0:
                                if intraday_pos is not None:
                                    intraday_pos.num_contracts = int(live_qty_after_fill)
                                self.Log(
                                    f"INTRADAY_PARTIAL_CLOSE: {symbol_norm} | RemainingQty={live_qty_after_fill} | State retained"
                                )
                                self._greeks_breach_logged = False
                                return

                            removed_position = self.options_engine.remove_intraday_position()
                            if removed_position:
                                # Cancel any lingering OCO pair after explicit close fill.
                                try:
                                    self.oco_manager.cancel_by_symbol(
                                        removed_position.contract.symbol,
                                        reason="INTRADAY_POSITION_CLOSED",
                                    )
                                except Exception as e:
                                    self.Log(
                                        f"OCO_CLEANUP_ERROR: {removed_position.contract.symbol} | {e}"
                                    )
                            if removed_position and removed_position.entry_price > 0:
                                is_win = fill_price > removed_position.entry_price
                                self.options_engine.record_spread_result(is_win)
                                self.options_engine.record_intraday_result(
                                    symbol=symbol,
                                    is_win=is_win,
                                    current_time=str(self.Time),
                                )
                                result_str = "WIN" if is_win else "LOSS"
                                self.Log(
                                    f"INTRADAY_RESULT: {result_str} | "
                                    f"Entry=${removed_position.entry_price:.2f} | Exit=${fill_price:.2f} | "
                                    f"P&L={((fill_price - removed_position.entry_price) / removed_position.entry_price):.1%}"
                                )
                                self._diag_intraday_result_count += 1
                                # V6.12: Record trade in monthly P&L tracker
                                if hasattr(self, "pnl_tracker"):
                                    self.pnl_tracker.record_trade(
                                        symbol=symbol,
                                        engine="OPT_INTRADAY",
                                        entry_date=removed_position.entry_time[:10]
                                        if removed_position.entry_time
                                        else str(self.Time.date()),
                                        exit_date=str(self.Time.date()),
                                        entry_price=removed_position.entry_price,
                                        exit_price=fill_price,
                                        quantity=abs(int(fill_qty)),
                                    )
                                self._intraday_entry_snapshot.pop(symbol_norm, None)
                            self._greeks_breach_logged = False  # Reset for next position
                        else:
                            # Not tracked as current intraday symbol; fall back to single-leg handling.
                            removed_position = self.options_engine.remove_position()
                            if removed_position:
                                try:
                                    self.oco_manager.cancel_by_symbol(
                                        removed_position.contract.symbol,
                                        reason="SINGLE_LEG_POSITION_CLOSED",
                                    )
                                except Exception as e:
                                    self.Log(
                                        f"OCO_CLEANUP_ERROR: {removed_position.contract.symbol} | {e}"
                                    )
                            self._greeks_breach_logged = False
                    else:
                        # Single-leg exit (legacy swing)
                        removed_position = self.options_engine.remove_position()
                        if removed_position:
                            try:
                                self.oco_manager.cancel_by_symbol(
                                    removed_position.contract.symbol,
                                    reason="SINGLE_LEG_POSITION_CLOSED",
                                )
                            except Exception as e:
                                self.Log(
                                    f"OCO_CLEANUP_ERROR: {removed_position.contract.symbol} | {e}"
                                )
                        # V6.15: Fallback intraday result accounting for orphan/implicit exits.
                        snapshot = self._intraday_entry_snapshot.get(symbol_norm)
                        live_qty_after_fill = abs(self._get_option_holding_quantity(symbol))
                        if (
                            snapshot
                            and snapshot.get("entry_price", 0) > 0
                            and live_qty_after_fill <= 0
                        ):
                            self._intraday_entry_snapshot.pop(symbol_norm, None)
                            entry_price = float(snapshot["entry_price"])
                            is_win = fill_price > entry_price
                            self.options_engine.record_spread_result(is_win)
                            self.options_engine.record_intraday_result(
                                symbol=symbol,
                                is_win=is_win,
                                current_time=str(self.Time),
                            )
                            result_str = "WIN" if is_win else "LOSS"
                            self.Log(
                                f"INTRADAY_RESULT: {result_str} | "
                                f"Entry=${entry_price:.2f} | Exit=${fill_price:.2f} | "
                                f"P&L={((fill_price - entry_price) / entry_price):.1%} | "
                                f"Path=FALLBACK"
                            )
                            self._diag_intraday_result_count += 1
                            if hasattr(self, "pnl_tracker"):
                                self.pnl_tracker.record_trade(
                                    symbol=symbol,
                                    engine="OPT_INTRADAY",
                                    entry_date=str(snapshot.get("entry_time", str(self.Time)))[:10],
                                    exit_date=str(self.Time.date()),
                                    entry_price=entry_price,
                                    exit_price=fill_price,
                                    quantity=abs(int(fill_qty)),
                                )
                        self._greeks_breach_logged = False  # Reset for next position
            except Exception as e:
                self.Log(f"OPT_TRACK_ERROR: {symbol}: {e}")

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
            pending_long = self.options_engine._pending_spread_long_leg
            pending_short = self.options_engine._pending_spread_short_leg

            if pending_long is None or pending_short is None:
                self.Log(f"SPREAD_ERROR: Fill received but no pending spread | {symbol}")
                return

            # Get expected quantity from pending state
            expected_qty = getattr(self.options_engine, "_pending_num_contracts", 1) or 1

            self._spread_fill_tracker = SpreadFillTracker(
                long_leg_symbol=pending_long.symbol,
                short_leg_symbol=pending_short.symbol,
                expected_quantity=expected_qty,
                timeout_minutes=config.SPREAD_FILL_TIMEOUT_MINUTES,
                created_at=str(self.Time),
                spread_type=getattr(self.options_engine, "_pending_spread_type", None),
            )
            self.Log(
                f"SPREAD: Fill tracker created | "
                f"Long={pending_long.symbol[-15:]} Short={pending_short.symbol[-15:]} "
                f"Expected={expected_qty}"
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

        # Record fill by symbol match (using tracker's stored symbols)
        if tracker.long_leg_symbol == symbol:
            tracker.record_long_fill(fill_price, fill_qty, str(self.Time))
            self.Log(
                f"SPREAD: Long leg filled | {symbol[-20:]} @ ${fill_price:.2f} x{fill_qty} | "
                f"Total={tracker.long_fill_qty}"
            )

        elif tracker.short_leg_symbol == symbol:
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
        spread_key = (
            f"{self._normalize_symbol_str(spread.long_leg.symbol)}|"
            f"{self._normalize_symbol_str(spread.short_leg.symbol)}"
        )
        tracker = self._spread_close_trackers.get(spread_key)
        if tracker is None:
            tracker = {
                "expected_qty": spread.num_spreads,
                "long_closed": False,
                "short_closed": False,
                "long_qty": 0,
                "short_qty": 0,
                "long_price": 0.0,
                "short_price": 0.0,
            }
            self._spread_close_trackers[spread_key] = tracker

        # Track which leg closed and accumulate quantity
        long_norm = self._normalize_symbol_str(spread.long_leg.symbol)
        short_norm = self._normalize_symbol_str(spread.short_leg.symbol)
        if norm_symbol == long_norm:
            tracker["long_closed"] = True
            tracker["long_qty"] += fill_qty_abs
            tracker["long_price"] = fill_price
            self.Log(
                f"SPREAD: Long leg closed | {symbol[-20:]} @ ${fill_price:.2f} x{fill_qty_abs} | "
                f"Total closed={tracker['long_qty']}/{tracker['expected_qty']}"
            )
        elif norm_symbol == short_norm:
            tracker["short_closed"] = True
            tracker["short_qty"] += fill_qty_abs
            tracker["short_price"] = fill_price
            self.Log(
                f"SPREAD: Short leg closed | {symbol[-20:]} @ ${fill_price:.2f} x{fill_qty_abs} | "
                f"Total closed={tracker['short_qty']}/{tracker['expected_qty']}"
            )

        # Check if both legs closed
        if tracker["long_closed"] and tracker["short_closed"]:
            self._diag_spread_exit_fill_count += 1
            # Validate quantities (Bug #8 fix)
            if tracker["long_qty"] != tracker["expected_qty"]:
                self.Log(
                    f"SPREAD_WARNING: Long leg quantity mismatch | "
                    f"Closed={tracker['long_qty']} Expected={tracker['expected_qty']}"
                )

            if tracker["short_qty"] != tracker["expected_qty"]:
                self.Log(
                    f"SPREAD_WARNING: Short leg quantity mismatch | "
                    f"Closed={tracker['short_qty']} Expected={tracker['expected_qty']}"
                )

            # V2.27: Record win/loss for Win Rate Gate before removing position
            close_long_price = float(tracker["long_price"])
            close_short_price = float(tracker["short_price"])
            is_credit = spread.spread_type in (
                "BULL_PUT_CREDIT",
                "BEAR_CALL_CREDIT",
            )
            if is_credit:
                # Credit spread: profit when close value < credit received
                close_value = close_long_price - close_short_price
                is_win = close_value < spread.net_debit  # net_debit = net credit for credits
            else:
                # Debit spread: profit when close value > entry cost
                close_value = close_long_price - close_short_price
                is_win = close_value > spread.net_debit
            self.options_engine.record_spread_result(is_win)
            result_str = "WIN" if is_win else "LOSS"
            self.Log(
                f"SPREAD_RESULT: {result_str} | Type={spread.spread_type} | "
                f"Entry={spread.net_debit:.2f} | Close={close_value:.2f}"
            )
            pnl_pct = (
                ((close_value - spread.net_debit) / spread.net_debit) if spread.net_debit else 0.0
            )
            self.Log(
                f"SPREAD: EXIT | Reason=FILL_CLOSE_RECONCILED | "
                f"Type={spread.spread_type} | Entry={spread.net_debit:.2f} | "
                f"Close={close_value:.2f} | PnL={pnl_pct:+.1%}"
            )

            # V6.12: Record spread trade in monthly P&L tracker
            if hasattr(self, "pnl_tracker"):
                # Calculate realized P&L (×100 for options multiplier, ×num_spreads for quantity)
                spread_pnl = (close_value - spread.net_debit) * 100 * spread.num_spreads
                if is_credit:
                    spread_pnl = -spread_pnl  # Credit spreads: profit when close < credit
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
            self.options_engine.remove_spread_position(symbol)
            self._diag_spread_position_removed_count += 1
            self._greeks_breach_logged = False  # Reset for next position
            self._spread_forced_close_retry.pop(spread_key, None)
            self._spread_forced_close_reason.pop(spread_key, None)
            self._spread_forced_close_cancel_counts.pop(spread_key, None)
            self._spread_forced_close_retry_cycles.pop(spread_key, None)
            self._spread_close_trackers.pop(spread_key, None)

            self.Log("SPREAD: Position removed - both legs closed")

    def _queue_spread_close_retry_on_cancel(self, symbol: str, order_event) -> None:
        """
        V6.21: Queue forced spread close retry when broker cancels a close leg.

        We only queue retries for active spread symbols where the canceled order
        appears to be reducing an existing position (close-side quantity).
        """
        if "QQQ" not in symbol or ("C" not in symbol and "P" not in symbol):
            return

        spread = None
        for candidate in self.options_engine.get_spread_positions():
            if candidate.long_leg.symbol == symbol or candidate.short_leg.symbol == symbol:
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
        try:
            holdings_qty = self.Portfolio[order_event.Symbol].Quantity
            is_close_side = (order.Quantity < 0 < holdings_qty) or (
                order.Quantity > 0 > holdings_qty
            )
        except Exception:
            is_close_side = False
        if not is_close_side:
            return

        long_symbol = self._normalize_symbol_str(spread.long_leg.symbol)
        short_symbol = self._normalize_symbol_str(spread.short_leg.symbol)
        spread_key = f"{long_symbol}|{short_symbol}"
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

        if cancel_count >= escalation_count:
            self._diag_spread_close_escalation_count += 1
            self.Log(
                f"SPREAD_CLOSE_ESCALATED: Cancel threshold reached | "
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
                        "exit_type": "SPREAD_CLOSE_ESCALATED",
                    },
                )
            )
            self._diag_spread_exit_signal_count += 1
            self._diag_spread_exit_submit_count += 1
        else:
            self.Log(
                f"SPREAD_RETRY_QUEUED: Close leg canceled | Symbol={symbol} | "
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

        # Clear options engine pending state
        self.options_engine._pending_spread_long_leg = None
        self.options_engine._pending_spread_short_leg = None
        self.options_engine._pending_spread_type = None
        self.options_engine._pending_net_debit = None
        self.options_engine._pending_max_profit = None
        self.options_engine._pending_spread_width = None
        self.options_engine._pending_num_contracts = None
        self.options_engine._pending_entry_score = None
        self.options_engine._pending_stop_pct = None
        self.options_engine._pending_stop_price = None
        self.options_engine._pending_target_price = None

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
                long_holding = self.Portfolio.get(tracker.long_leg_symbol)
                if long_holding and long_holding.Invested:
                    qty = long_holding.Quantity
                    self.MarketOrder(tracker.long_leg_symbol, -qty, tag="EMERG_QTY_MISMATCH")
                    self.Log(
                        f"SPREAD: Emergency closed long leg | {tracker.long_leg_symbol} x{qty}"
                    )

            # Check short leg
            if tracker.short_leg_symbol:
                short_holding = self.Portfolio.get(tracker.short_leg_symbol)
                if short_holding and short_holding.Invested:
                    qty = short_holding.Quantity
                    self.MarketOrder(tracker.short_leg_symbol, -qty, tag="EMERG_QTY_MISMATCH")
                    self.Log(
                        f"SPREAD: Emergency closed short leg | {tracker.short_leg_symbol} x{qty}"
                    )

        except Exception as e:
            self.Log(f"SPREAD_ERROR: Emergency close failed | {e}")

    def _force_spread_exit(self, reason: str) -> None:
        """
        V2.6 Bug #3/#14: Force exit spread when normal price data unavailable or 0DTE.

        Args:
            reason: Reason for forced exit (for logging).
        """
        spread = self.options_engine.get_spread_position()
        if spread is None:
            return

        self.Log(f"SPREAD: FORCE_EXIT | Reason={reason}")

        # Track exit orders for potential retry (Bug #14)
        self._pending_exit_orders[spread.long_leg.symbol] = ExitOrderTracker(
            symbol=spread.long_leg.symbol,
            reason=reason,
            spread_id=f"{spread.spread_type}_{spread.entry_time}",
        )
        self._pending_exit_orders[spread.short_leg.symbol] = ExitOrderTracker(
            symbol=spread.short_leg.symbol,
            reason=reason,
            spread_id=f"{spread.spread_type}_{spread.entry_time}",
        )

        try:
            # Close long leg (sell)
            self.MarketOrder(
                spread.long_leg.symbol,
                -spread.num_spreads,
                tag=f"FORCE_{reason}",
            )
            self.Log(
                f"SPREAD: Force exit long leg | {spread.long_leg.symbol[-20:]} x{spread.num_spreads}"
            )

            # Close short leg (buy back)
            self.MarketOrder(
                spread.short_leg.symbol,
                spread.num_spreads,
                tag=f"FORCE_{reason}",
            )
            self.Log(
                f"SPREAD: Force exit short leg | {spread.short_leg.symbol[-20:]} x{spread.num_spreads}"
            )
        except Exception as e:
            self.Log(f"SPREAD_ERROR: Force exit failed | {e}")

    def _schedule_exit_retry(self, symbol: str) -> None:
        """
        V2.6 Bug #14: Schedule a retry for a failed exit order.

        Args:
            symbol: Symbol that failed to exit.
        """
        try:
            # Schedule retry after delay using QC's scheduling
            retry_seconds = config.EXIT_ORDER_RETRY_DELAY_SECONDS

            # Use Schedule.On for proper scheduling
            def retry_action():
                self._retry_exit_order(symbol)

            # Schedule for N seconds from now
            self.Schedule.On(
                self.DateRules.Today,
                self.TimeRules.At(
                    self.Time.hour,
                    self.Time.minute,
                    self.Time.second + retry_seconds,
                ),
                retry_action,
            )
            self.Log(f"EXIT_RETRY: Scheduled retry for {symbol[-20:]} in {retry_seconds}s")
        except Exception as e:
            self.Log(f"EXIT_RETRY_ERROR: Failed to schedule retry | {e}")
            # Fallback: immediate retry
            self._retry_exit_order(symbol)

    def _retry_exit_order(self, symbol: str) -> None:
        """
        V2.6 Bug #14: Retry a failed exit order.

        Args:
            symbol: Symbol to retry exit for.
        """
        if symbol not in self._pending_exit_orders:
            return

        tracker = self._pending_exit_orders[symbol]
        self.Log(f"EXIT_RETRY: Retrying exit for {symbol[-20:]} (attempt {tracker.retry_count})")

        try:
            holding = self.Portfolio.get(symbol)
            if holding and holding.Invested:
                qty = holding.Quantity
                self.MarketOrder(symbol, -qty, tag=f"RETRY_{tracker.reason}")
                self.Log(f"EXIT_RETRY: Submitted market order | {symbol[-20:]} x{qty}")
            else:
                # Position already closed
                self._pending_exit_orders.pop(symbol, None)
                self.Log(f"EXIT_RETRY: Position already closed | {symbol[-20:]}")
        except Exception as e:
            self.Log(f"EXIT_RETRY_ERROR: Retry failed | {symbol[-20:]} | {e}")

    def _force_market_close(self, symbol: str) -> None:
        """
        V2.6 Bug #14: Emergency market close when all retries exhausted.

        V2.33: If symbol is an option, use atomic close to ensure shorts close first.

        Args:
            symbol: Symbol to force close.
        """
        self.Log(f"EXIT_EMERGENCY: Force market close | {symbol[-20:]}")
        try:
            holding = self.Portfolio.get(symbol)
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
                    self.Liquidate(symbol, tag="EMERG_ALL_RETRIES_FAILED")
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

    def _get_volume_ratio(self, symbol: Symbol, data: Slice) -> float:
        """
        Get current volume ratio vs average.

        Args:
            symbol: Symbol to check.
            data: Current data slice.

        Returns:
            Ratio of current volume to average (1.0 = average).
        """
        if not data.Bars.ContainsKey(symbol):
            return 1.0

        current_volume = data.Bars[symbol].Volume
        # Use a simple approximation - would need historical volume for accurate ratio
        # For now, assume current volume is at average
        return 1.0

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
