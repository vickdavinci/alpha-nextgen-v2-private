# region imports
# Type hints
from typing import Any, Dict, List, Optional, Set

from AlgorithmImports import *

# Configuration
import config
from engines.core.capital_engine import CapitalEngine, CapitalState
from engines.core.cold_start_engine import ColdStartEngine

# Core Engines
from engines.core.regime_engine import RegimeEngine, RegimeState
from engines.core.risk_engine import GreeksSnapshot, RiskCheckResult, RiskEngine, SafeguardType
from engines.core.trend_engine import TrendEngine
from engines.satellite.hedge_engine import HedgeEngine

# Satellite Engines
from engines.satellite.mean_reversion_engine import MeanReversionEngine
from engines.satellite.options_engine import (
    ExitOrderTracker,
    OptionContract,
    OptionDirection,
    OptionsEngine,
    SpreadFillTracker,
)
from execution.execution_engine import ExecutionEngine

# OCO Manager for Options exits
from execution.oco_manager import OCOManager

# Models
from models.enums import Phase, RegimeLevel, Urgency
from models.target_weight import TargetWeight

# Infrastructure
from persistence.state_manager import StateManager

# Portfolio & Execution
from portfolio.portfolio_router import PortfolioRouter
from scheduling.daily_scheduler import DailyScheduler

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
        self.SetStartDate(2023, 1, 1)
        self.SetEndDate(2023, 3, 31)  # V2.5 Jan-Mar 2023 backtest (3 months)
        self.SetCash(config.PHASE_SEED_MIN)  # $50,000 seed capital

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
        self.today_trades = []
        self.today_safeguards = []
        self.symbols_to_skip = set()
        self._splits_logged_today = set()  # Log throttle: only log each split once/day
        self._greeks_breach_logged = False  # Log throttle: only log Greeks breach once/position
        self._kill_switch_handled_today = False  # V2.3: Only handle kill switch once per day

        # V2.3.6: Track pending spread orders to handle leg failures
        # Maps short leg symbol -> long leg symbol (to liquidate long if short fails)
        self._pending_spread_orders: Dict[str, str] = {}
        # V2.6 Bug #5: Add reverse mapping for long leg rejection handling
        self._pending_spread_orders_reverse: Dict[str, str] = {}  # long -> short

        # V2.6: Atomic spread fill tracking (Bugs #1, #6, #7)
        self._spread_fill_tracker: Optional[SpreadFillTracker] = None

        # V2.6 Bug #12: Initialize close tracking flags (not lazy)
        self._spread_long_closed = False
        self._spread_short_closed = False
        self._spread_close_qty_long = 0
        self._spread_close_qty_short = 0
        self._spread_close_expected_qty = 0

        # V2.6 Bug #14: Exit order retry tracking
        self._pending_exit_orders: Dict[str, ExitOrderTracker] = {}

        # V2.4.4 P0: Margin call circuit breaker tracking
        # Prevents 2765+ margin call spam seen in V2.4.3 backtest
        self._margin_call_consecutive_count: int = 0
        self._margin_call_cooldown_until: Optional[str] = None

        self.Log(
            f"INIT: Complete | "
            f"Cash=${config.PHASE_SEED_MIN:,} | "
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
        # STEP 3: RISK ENGINE CHECKS (ALWAYS FIRST AFTER SPLITS)
        # =====================================================================
        risk_result = self._run_risk_checks(data)

        # =====================================================================
        # STEP 3B: V2.4.4 P0 - EXPIRATION HAMMER V2 (runs every minute after 2 PM)
        # =====================================================================
        # This runs BEFORE kill switch to ensure expiring options are closed
        # even if the account is in margin crisis
        self._check_expiration_hammer_v2()

        # =====================================================================
        # STEP 4: HANDLE KILL SWITCH
        # =====================================================================
        if risk_result.reset_cold_start:
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

        if mr_window_open and risk_result.can_enter_intraday:
            self._scan_mr_signals(data)

        # =====================================================================
        # STEP 6B: V2.1 OPTIONS ENTRY SCANNING (if window open)
        # =====================================================================
        # V2.3.4 FIX: Block options during cold start (Days 1-5)
        is_cold_start = self.cold_start_engine.is_cold_start_active()
        if mr_window_open and risk_result.can_enter_intraday and not is_cold_start:
            self._scan_options_signals(data)

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

    # =========================================================================
    # SETUP HELPERS
    # =========================================================================

    def _add_securities(self) -> None:
        """
        Add all required securities at Minute resolution.

        Adds:
            - Traded symbols: TQQQ, SOXL, QLD, SSO, TMF, PSQ, SHV
            - Proxy symbols: SPY, RSP, HYG, IEF (for regime calculation)

        Stores symbol references as instance attributes for easy access.
        """
        # Traded symbols - Leveraged longs (Trend Engine)
        self.tqqq = self.AddEquity("TQQQ", Resolution.Minute).Symbol
        self.soxl = self.AddEquity("SOXL", Resolution.Minute).Symbol
        self.qld = self.AddEquity("QLD", Resolution.Minute).Symbol
        self.sso = self.AddEquity("SSO", Resolution.Minute).Symbol

        # V2.2: New Trend symbols for diversification
        self.tna = self.AddEquity("TNA", Resolution.Minute).Symbol  # 3× Russell 2000
        self.fas = self.AddEquity("FAS", Resolution.Minute).Symbol  # 3× Financials

        # Traded symbols - Hedges
        self.tmf = self.AddEquity("TMF", Resolution.Minute).Symbol
        self.psq = self.AddEquity("PSQ", Resolution.Minute).Symbol

        # V2.1: QQQ for options trading
        self.qqq = self.AddEquity("QQQ", Resolution.Minute).Symbol

        # V2.1: Add QQQ options chain with config-driven DTE filter
        qqq_option = self.AddOption("QQQ", Resolution.Minute)
        # Filter: ATM ±5 strikes, DTE from config (1-4 days for daily volatility harvesting)
        qqq_option.SetFilter(
            -5, 5, timedelta(days=config.OPTIONS_DTE_MIN), timedelta(days=config.OPTIONS_DTE_MAX)
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

        # Store collections for iteration
        self.traded_symbols = [
            self.tqqq,
            self.soxl,
            self.qld,
            self.sso,
            self.tna,  # V2.2
            self.fas,  # V2.2
            self.tmf,
            self.psq,
        ]
        self.proxy_symbols = [self.spy, self.rsp, self.hyg, self.ief]

        # V2.2: Trend symbols for easy iteration
        self.trend_symbols = [self.qld, self.sso, self.tna, self.fas]

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

        # ---------------------------------------------------------------------
        # Risk Engine Indicators
        # ---------------------------------------------------------------------
        # SPY ATR for vol shock detection (minute resolution for intraday check)
        self.spy_atr = self.ATR(
            self.spy, config.ATR_PERIOD, MovingAverageType.Wilders, Resolution.Minute
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

        # V2.2: FAS indicators (3× Financials)
        self.fas_ma200 = self.SMA(self.fas, config.SMA_SLOW, Resolution.Daily)
        self.fas_adx = self.ADX(self.fas, config.ADX_PERIOD, Resolution.Daily)
        self.fas_atr = self.ATR(
            self.fas, config.ATR_PERIOD, MovingAverageType.Wilders, Resolution.Daily
        )

        # V2.4: SMA50 for Structural Trend exit (replaces Chandelier trailing stops)
        # SMA50 allows minor volatility (3% drops) without exit if above trend line
        self.qld_sma50 = self.SMA(self.qld, config.TREND_SMA_PERIOD, Resolution.Daily)
        self.sso_sma50 = self.SMA(self.sso, config.TREND_SMA_PERIOD, Resolution.Daily)
        self.tna_sma50 = self.SMA(self.tna, config.TREND_SMA_PERIOD, Resolution.Daily)
        self.fas_sma50 = self.SMA(self.fas, config.TREND_SMA_PERIOD, Resolution.Daily)

        # ---------------------------------------------------------------------
        # Mean Reversion Engine Indicators (Minute resolution for intraday)
        # ---------------------------------------------------------------------
        self.tqqq_rsi = self.RSI(
            self.tqqq, config.RSI_PERIOD, MovingAverageType.Wilders, Resolution.Minute
        )
        self.soxl_rsi = self.RSI(
            self.soxl, config.RSI_PERIOD, MovingAverageType.Wilders, Resolution.Minute
        )

        # ---------------------------------------------------------------------
        # V2.1: Options Engine Indicators (QQQ for options trading)
        # ---------------------------------------------------------------------
        self.qqq_adx = self.ADX(self.qqq, config.ADX_PERIOD, Resolution.Daily)
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
        self.tqqq_volumes = RollingWindow[float](20)
        self.soxl_volumes = RollingWindow[float](20)

        # Store last regime score for intraday use
        self._last_regime_score = 50.0

        # Store capital state for EOD->Market Close handoff
        self._eod_capital_state = None

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

        # V2.1.1: Add intraday options force exit at 15:30
        self.Schedule.On(
            self.DateRules.EveryDay(),
            self.TimeRules.At(15, 30),
            self._on_intraday_options_force_close,
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

        # V2.4.1: Friday Firewall - close swing options before weekend
        # Runs only on Fridays at configured time (default 3:45 PM)
        if config.FRIDAY_FIREWALL_ENABLED:
            self.Schedule.On(
                self.DateRules.Every(DayOfWeek.Friday),
                self.TimeRules.At(
                    config.FRIDAY_FIREWALL_TIME_HOUR,
                    config.FRIDAY_FIREWALL_TIME_MINUTE,
                ),
                self._on_friday_firewall,
            )
            self.Log(
                f"INIT: Friday Firewall enabled at "
                f"{config.FRIDAY_FIREWALL_TIME_HOUR}:{config.FRIDAY_FIREWALL_TIME_MINUTE:02d}"
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
        if data.Bars.ContainsKey(self.tqqq):
            self.tqqq_volumes.Add(float(data.Bars[self.tqqq].Volume))
        if data.Bars.ContainsKey(self.soxl):
            self.soxl_volumes.Add(float(data.Bars[self.soxl].Volume))

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

        # V2.3 FIX: Reset options engine daily state (entry flags, trade counters)
        current_date_str = str(self.Time.date())
        self.options_engine.reset_daily(current_date_str)

        # V2.3 DEBUG: Log daily reset confirmation (only in live mode)
        if self.LiveMode:
            self.Log(f"DAILY_RESET: All flags cleared at {self.Time}")

        self.equity_prior_close = self.Portfolio.TotalPortfolioValue
        self.risk_engine.set_equity_prior_close(self.equity_prior_close)

        # Set SPY prior close for gap filter
        self.spy_prior_close = self.Securities[self.spy].Close
        self.risk_engine.set_spy_prior_close(self.spy_prior_close)

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

        # Reconcile positions with broker
        self._reconcile_positions()

    def _on_warm_entry_check(self) -> None:
        """
        Warm entry check at 10:00 ET.

        During cold start (days 1-5), checks if conditions are favorable
        for a 50% sized entry into QLD.
        """
        # Skip during warmup
        if self.IsWarmingUp:
            return

        if not self.cold_start_engine.is_cold_start_active():
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

        # Check TQQQ position
        if self.Portfolio[self.tqqq].Invested:
            signal = TargetWeight(
                symbol="TQQQ",
                target_weight=0.0,
                source="MR",
                urgency=Urgency.IMMEDIATE,
                reason="TIME_EXIT_15:45",
            )
            self.portfolio_router.receive_signal(signal)

        # Check SOXL position
        if self.Portfolio[self.soxl].Invested:
            signal = TargetWeight(
                symbol="SOXL",
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
        if self.Portfolio[self.tqqq].Invested:
            self.Log("MR_FAILSAFE: Force liquidating TQQQ via direct Liquidate()")
            self.Liquidate(self.tqqq, tag="MR_FAILSAFE_15:45")
        if self.Portfolio[self.soxl].Invested:
            self.Log("MR_FAILSAFE: Force liquidating SOXL via direct Liquidate()")
            self.Liquidate(self.soxl, tag="MR_FAILSAFE_15:45")

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

        total_equity = self.Portfolio.TotalPortfolioValue

        # 1. Calculate Regime
        regime_state = self._calculate_regime()
        # Store for intraday MR scanning
        self._last_regime_score = regime_state.smoothed_score

        # 2. Update Capital Engine
        capital_state = self.capital_engine.end_of_day_update(total_equity)

        # 3. Generate Trend signals (EOD)
        self._generate_trend_signals_eod(regime_state)

        # 4. V2.1: Generate Options signals (if regime allows)
        # V2.3.20: Allow options during cold start with 50% sizing (was blocked entirely)
        is_cold_start = self.cold_start_engine.is_cold_start_active()
        options_size_mult = config.OPTIONS_COLD_START_MULTIPLIER if is_cold_start else 1.0
        if regime_state.smoothed_score >= 40:
            self._generate_options_signals(regime_state, capital_state, options_size_mult)

        # 5. Generate Hedge signals
        self._generate_hedge_signals(regime_state)

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

        # Submit MOO orders (market is now closed)
        if hasattr(self, "_eod_capital_state") and self._eod_capital_state is not None:
            self._process_eod_signals(self._eod_capital_state)
            self._eod_capital_state = None

        # Save all state
        self._save_state()

        # Skip daily summary to save log space - only fills are logged

        # Reset daily tracking
        self.today_trades.clear()
        self.today_safeguards.clear()
        self.symbols_to_skip.clear()
        self._splits_logged_today.clear()
        self._greeks_breach_logged = False
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

    def _check_expiration_hammer_v2(self) -> None:
        """
        V2.4.4 P0: Expiration Hammer V2 - Close ALL options expiring TODAY.

        This is called every minute during trading hours and checks ALL broker
        positions for options expiring today. If found and it's past 2:00 PM,
        immediately liquidate them.

        This is a CRITICAL safety check that runs independently of the options
        engine's tracked positions. It catches any options that slipped through.
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
                    # EXPIRING TODAY - LIQUIDATE IMMEDIATELY
                    qty = holding.Quantity
                    symbol = str(holding.Symbol)
                    self.Log(
                        f"EXPIRATION_HAMMER_V2: LIQUIDATING {symbol} | "
                        f"Qty={qty} | Expires TODAY ({expiry_date}) | "
                        f"Time={self.Time.strftime('%H:%M')} | "
                        f"P0 FIX: Preventing auto-exercise"
                    )
                    self.Liquidate(holding.Symbol, tag="EXPIRATION_HAMMER_V2")
            except Exception as e:
                self.Log(f"EXPIRATION_HAMMER_V2: Error checking {holding.Symbol} - {e}")

    def _on_intraday_options_force_close(self) -> None:
        """
        V2.1.1: Intraday options force close at 15:30 ET.

        Forces close of all intraday mode options positions (0-2 DTE).
        These must close 30 minutes before market close.
        """
        # Skip during warmup
        if self.IsWarmingUp:
            return

        # V2.4.4 P0: Run Expiration Hammer V2 as part of force close
        self._check_expiration_hammer_v2()

        # Check for intraday position to close
        if self.options_engine._intraday_position is not None:
            # Get current option price
            intraday_pos = self.options_engine._intraday_position
            symbol = intraday_pos.contract.symbol

            # Get current price (best effort)
            current_price = intraday_pos.entry_price  # Fallback

            signal = self.options_engine.check_intraday_force_exit(
                current_hour=self.Time.hour,
                current_minute=self.Time.minute,
                current_price=current_price,
            )

            if signal:
                self.portfolio_router.receive_signal(signal)
                self._process_immediate_signals()

    def _on_friday_firewall(self) -> None:
        """
        V2.4.1: Friday Firewall - close swing options before weekend.

        Runs only on Fridays at configured time (default 3:45 PM).

        Rules:
        1. VIX > 25: Close ALL swing options (high volatility weekend risk)
        2. Fresh trade (opened today) AND VIX >= 15: Close it (gambling protection)
        3. Fresh trade AND VIX < 15: Keep it (calm market exception)
        4. Older trades: Keep them (already survived initial risk)
        """
        # Skip during warmup
        if self.IsWarmingUp:
            return

        # Get current VIX
        vix_current = self._current_vix

        # Check if we have any swing positions
        swing_signals = self.options_engine.check_friday_firewall_exit(
            current_vix=vix_current,
            current_date=str(self.Time.date()),
            vix_close_all_threshold=config.FRIDAY_FIREWALL_VIX_CLOSE_ALL,
            vix_keep_fresh_threshold=config.FRIDAY_FIREWALL_VIX_KEEP_FRESH,
        )

        if swing_signals:
            for signal in swing_signals:
                self.Log(f"FRIDAY_FIREWALL: {signal.reason} | VIX={vix_current:.1f}")
                self.portfolio_router.receive_signal(signal)

            # Process immediately
            self._process_immediate_signals()
        else:
            self.Log(f"FRIDAY_FIREWALL: No action needed | VIX={vix_current:.1f}")

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

        # Update micro regime engine with intraday VIX proxy
        # V2.5: Pass macro_regime_score for Grind-Up Override
        state = self.options_engine._micro_regime_engine.update(
            vix_current=vix_intraday_proxy,  # Use UVXY-derived intraday proxy
            vix_open=self._vix_at_open,
            qqq_current=qqq_current,
            qqq_open=self._qqq_at_open,
            current_time=str(self.Time),
            macro_regime_score=self._last_regime_score,
        )

        # V2.3.4: Log with UVXY proxy info
        self.Log(
            f"MICRO_UPDATE: VIX_proxy={vix_intraday_proxy:.2f} (UVXY {uvxy_change_pct:+.1f}%) | "
            f"Regime={state.micro_regime.value} | Dir={state.recommended_direction.value if state.recommended_direction else 'NONE'}"
        )

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

            # Notify execution engine
            self.execution_engine.on_order_event(
                broker_order_id=orderEvent.OrderId,
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

            # Forward to execution engine for tracking
            self.execution_engine.on_order_event(
                broker_order_id=orderEvent.OrderId,
                status="Filled",
                fill_price=fill_price,
                fill_quantity=fill_qty,
            )

            # V2.3.6: Clean up spread tracking when short leg fills successfully
            if symbol in self._pending_spread_orders:
                long_leg = self._pending_spread_orders.pop(symbol)
                self.Log(f"SPREAD: Both legs filled successfully | Short={symbol} Long={long_leg}")

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
                    self.MarketOrder(orderEvent.Symbol, -fill_qty)

        # V2.4.4 P0 Fix #3: Handle Option Exercise events
        # ITM options get auto-exercised, creating massive stock positions
        # Detect and immediately liquidate the resulting stock position
        elif orderEvent.Status == OrderStatus.Filled and "Exercise" in str(orderEvent.Message):
            symbol = str(orderEvent.Symbol)
            fill_qty = orderEvent.FillQuantity
            self.Log(
                f"EXERCISE_DETECTED: {symbol} | Qty={fill_qty} | "
                f"CRITICAL: Option was exercised - checking for stock position"
            )
            # After exercise, we have stock. Liquidate it immediately.
            # The underlying symbol is QQQ for our options
            qqq_holding = self.Portfolio[self.qqq]
            if qqq_holding.Invested and abs(qqq_holding.Quantity) > 0:
                self.Log(
                    f"EXERCISE_LIQUIDATE: QQQ position from exercise | "
                    f"Qty={qqq_holding.Quantity} | Value=${qqq_holding.HoldingsValue:,.2f}"
                )
                self.Liquidate(self.qqq, tag="EXERCISE_LIQUIDATE")

        elif orderEvent.Status == OrderStatus.Invalid:
            self.Log(f"INVALID: {orderEvent.Symbol} - {orderEvent.Message}")
            self.execution_engine.on_order_event(
                broker_order_id=orderEvent.OrderId,
                status="Invalid",
                rejection_reason=orderEvent.Message,
            )

            # V2.4.4 P0 Fix #4: Margin Call Circuit Breaker
            # Track consecutive margin calls and enter cooldown after hitting limit
            if "Margin" in str(orderEvent.Message):
                self._margin_call_consecutive_count += 1
                if self._margin_call_consecutive_count >= config.MARGIN_CALL_MAX_CONSECUTIVE:
                    # Enter cooldown
                    cooldown_hours = config.MARGIN_CALL_COOLDOWN_HOURS
                    cooldown_until = self.Time + timedelta(hours=cooldown_hours)
                    self._margin_call_cooldown_until = str(cooldown_until)
                    self.Log(
                        f"MARGIN_CALL_CIRCUIT_BREAKER: {self._margin_call_consecutive_count} consecutive "
                        f"margin calls | COOLDOWN until {self._margin_call_cooldown_until}"
                    )
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
                try:
                    if long_leg_symbol in [str(s) for s in self.Securities.Keys]:
                        holding = self.Portfolio[long_leg_symbol]
                        if holding.Invested:
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
                try:
                    if short_leg_symbol in [str(s) for s in self.Securities.Keys]:
                        holding = self.Portfolio[short_leg_symbol]
                        if holding.Invested:
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

        elif orderEvent.Status == OrderStatus.Canceled:
            self.execution_engine.on_order_event(
                broker_order_id=orderEvent.OrderId,
                status="Canceled",
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
            - Risk Engine state (baselines, safeguards)
            - Trend Engine state (positions, stops)
            - Regime Engine state (previous score)
        """
        try:
            self.state_manager.load_all()
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
            )

            # V2.1: Save options engine and OCO manager state
            if hasattr(self, "options_engine"):
                opt_state = self.options_engine.get_state_for_persistence()
                self.ObjectStore.Save("options_engine_state", str(opt_state))

            if hasattr(self, "oco_manager"):
                oco_state = self.oco_manager.get_state_for_persistence()
                self.ObjectStore.Save("oco_manager_state", str(oco_state))

        except Exception as e:
            self.Log(f"STATE_ERROR: Failed to save state - {e}")

    def _reconcile_positions(self) -> None:
        """
        Reconcile internal position tracking with broker state.

        Called at 09:33 - logging disabled to save log space.
        """
        # Position logging disabled to save log space
        # Fills are logged in OnOrderEvent
        pass

    # =========================================================================
    # SIGNAL PROCESSING HELPERS
    # =========================================================================

    def _process_immediate_signals(self) -> None:
        """
        Process pending signals with IMMEDIATE urgency.

        Routes to PortfolioRouter which validates and executes via MarketOrder.
        """
        # Skip if no pending signals
        if self.portfolio_router.get_pending_count() == 0:
            return

        # Get current state
        capital_state = self.capital_engine.calculate(self.Portfolio.TotalPortfolioValue)
        current_positions = self._get_current_positions()
        current_prices = self._get_current_prices()

        try:
            # Calculate max single position in dollars from percentage
            max_single_position = capital_state.tradeable_eq * capital_state.max_single_position_pct
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

            # Validate against max position size
            max_single_position_pct = capital_state.max_single_position_pct
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

        # V2.3: Count current trend positions for position limit enforcement
        trend_symbols = config.TREND_PRIORITY_ORDER  # ["QLD", "SSO", "TNA", "FAS"]
        current_trend_positions = sum(
            1 for sym in trend_symbols if self.Portfolio[getattr(self, sym.lower())].Invested
        )
        max_positions = config.MAX_CONCURRENT_TREND_POSITIONS  # Default: 2
        entries_allowed = max_positions - current_trend_positions

        # Log position status
        if entries_allowed < len(trend_symbols):
            self.Log(
                f"TREND: Position limit check | Current={current_trend_positions} | "
                f"Max={max_positions} | Entries allowed={entries_allowed}"
            )

        # V2.3: Collect entry candidates with their ADX scores for prioritization
        entry_candidates = []

        # Build symbol data map for cleaner iteration
        # V2.4: Added SMA50 for structural trend exit
        symbol_data = {
            "QLD": (self.qld, self.qld_ma200, self.qld_adx, self.qld_atr, self.qld_sma50),
            "SSO": (self.sso, self.sso_ma200, self.sso_adx, self.sso_atr, self.sso_sma50),
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

        # V2.3: Apply position limit - only send top N entry signals
        if entry_candidates and entries_allowed > 0:
            # Sort by ADX descending (strongest trends first)
            entry_candidates.sort(key=lambda x: x[1], reverse=True)

            # Send only as many entries as allowed
            for signal, adx in entry_candidates[:entries_allowed]:
                self.Log(
                    f"TREND: ENTRY_APPROVED {signal.symbol} | ADX={adx:.1f} | "
                    f"Slot {current_trend_positions + 1}/{max_positions}"
                )
                self.portfolio_router.receive_signal(signal)
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
        # Skip if indicators not ready
        if not self.qqq_adx.IsReady or not self.qqq_sma200.IsReady:
            return

        # Skip if already have options position (single-leg or spread)
        if self.options_engine.has_position():
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
                return
        except Exception as e:
            self.Log(f"OPTIONS_CHAIN_ERROR: Failed to iterate chain: {e}")
            return

        # Get current values
        qqq_price = self.Securities[self.qqq].Price
        adx_value = self.qqq_adx.Current.Value
        ma200_value = self.qqq_sma200.Current.Value
        regime_score = regime_state.smoothed_score

        # V2.1: Calculate IV rank from options chain
        iv_rank = self._calculate_iv_rank(chain)

        # V2.3: Determine spread direction from regime score
        if regime_score > config.SPREAD_REGIME_BULLISH:
            direction = OptionDirection.CALL
        elif regime_score < config.SPREAD_REGIME_BEARISH:
            direction = OptionDirection.PUT
        else:
            # Neutral regime (45-60): No trade
            return

        # V2.3: Build list of candidate contracts for spread selection
        candidate_contracts = self._build_spread_candidate_contracts(chain, direction)
        if len(candidate_contracts) < 2:
            return

        # V2.3.21: Select spread legs (ITM long + OTM short) with 15-min throttle
        spread_legs = self.options_engine.select_spread_legs(
            contracts=candidate_contracts,
            direction=direction,
            current_time=str(self.Time),
        )
        if spread_legs is None:
            return

        long_leg, short_leg = spread_legs

        # CRITICAL: Verify both legs have valid bid/ask
        if long_leg.bid <= 0 or long_leg.ask <= 0:
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
            iv_rank=iv_rank,
            current_hour=self.Time.hour,
            current_minute=self.Time.minute,
            current_date=str(self.Time.date()),
            portfolio_value=self.capital_engine.get_tradeable_equity(),
            long_leg_contract=long_leg,
            short_leg_contract=short_leg,
            gap_filter_triggered=self.risk_engine.is_gap_filter_active(),
            vol_shock_active=self.risk_engine.is_vol_shock_active(self.Time),
            size_multiplier=size_multiplier,
        )

        if signal:
            # V2.3.6 FIX: Check margin BEFORE submitting spread
            # IBKR treats spread legs as separate orders and requires naked short margin
            # for the short leg. If we don't have enough margin, skip the spread entirely
            # to avoid "orphaned long leg" situation.
            if signal.metadata and signal.metadata.get("spread_short_leg_quantity"):
                short_qty = signal.metadata.get("spread_short_leg_quantity", 0)
                short_symbol = signal.metadata.get("spread_short_leg_symbol", "")

                # Estimate required margin for naked short (conservative: $10K per contract)
                # IBKR typically requires 20-30% of notional for naked options
                estimated_margin_per_contract = 10000  # $10K per contract (conservative)
                required_margin = abs(short_qty) * estimated_margin_per_contract

                # Get current free margin
                free_margin = self.Portfolio.MarginRemaining

                if required_margin > free_margin:
                    self.Log(
                        f"SPREAD: BLOCKED - Insufficient margin for short leg | "
                        f"Required=${required_margin:,.0f} | Free=${free_margin:,.0f} | "
                        f"Short={short_symbol} x{short_qty}"
                    )
                    # Skip this spread - don't submit orphaned long leg
                    return

                self.Log(
                    f"SPREAD: Margin check passed | Required=${required_margin:,.0f} | "
                    f"Free=${free_margin:,.0f}"
                )

                # V2.3.6: Track spread order pair for failure handling
                # Map short leg symbol -> long leg symbol (and reverse for Bug #5 fix)
                long_symbol = str(signal.symbol) if signal.symbol else ""
                if short_symbol and long_symbol:
                    self._pending_spread_orders[short_symbol] = long_symbol
                    self._pending_spread_orders_reverse[long_symbol] = short_symbol  # V2.6 Bug #5
                    self.Log(
                        f"SPREAD: Tracking order pair | Short={short_symbol[-15:]} <-> Long={long_symbol[-15:]}"
                    )

            self.portfolio_router.receive_signal(signal)

    def _build_spread_candidate_contracts(
        self, chain, direction: OptionDirection
    ) -> List[OptionContract]:
        """
        V2.3: Build list of candidate OptionContract objects for spread selection.

        Filters chain for appropriate DTE range and direction, converting QC
        contracts to our OptionContract dataclass.

        Args:
            chain: QuantConnect options chain.
            direction: CALL or PUT direction.

        Returns:
            List of OptionContract objects for spread leg selection.
        """
        candidates = []
        qqq_price = self.Securities[self.qqq].Price

        for contract in chain:
            # Check direction
            if direction == OptionDirection.CALL:
                if contract.Right != OptionRight.Call:
                    continue
            else:
                if contract.Right != OptionRight.Put:
                    continue

            # Check DTE range for spreads (10-21 days per V2.3 spec)
            dte = (contract.Expiry - self.Time).days
            if dte < config.SPREAD_DTE_MIN or dte > config.SPREAD_DTE_MAX:
                continue

            # Get bid/ask safely
            bid, ask = self._get_contract_prices(contract)
            if bid <= 0 or ask <= 0:
                continue

            mid_price = (bid + ask) / 2

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
                direction=direction,
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
            contract_delta = abs(contract.Greeks.Delta) if hasattr(contract, "Greeks") else 0.0
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
        self, chain, direction: OptionDirection
    ) -> Optional[OptionContract]:
        """
        V2.3.4: Select QQQ option contract for intraday mode (0-1 DTE).

        Target delta: 0.30 (slightly OTM for faster gamma/premium moves)

        V2.3.4 FIX: Now accepts direction parameter to ensure we select
        the correct Call/Put based on the fade strategy direction.

        Criteria:
        - Matches specified direction (CALL or PUT)
        - Target 0.30 delta (±0.15 tolerance)
        - DTE 0-1 days (true 0DTE intraday trading)
        - Sufficient open interest
        - Tight bid-ask spread

        Args:
            chain: QuantConnect options chain.
            direction: Required option direction (CALL or PUT).

        Returns:
            OptionContract or None if no suitable contract found.
        """
        if chain is None:
            return None

        qqq_price = self.Securities[self.qqq].Price
        target_delta = config.OPTIONS_INTRADAY_DELTA_TARGET  # 0.30

        # Determine which OptionRight to filter for
        required_right = OptionRight.Call if direction == OptionDirection.CALL else OptionRight.Put

        # Filter for target delta, 0-1 DTE, and MATCHING DIRECTION
        candidates = []
        for contract in chain:
            # V2.3.4 FIX: Filter by direction FIRST
            if contract.Right != required_right:
                continue

            # Check DTE using config values (0-1 for true intraday)
            dte = (contract.Expiry - self.Time).days
            if dte < config.OPTIONS_INTRADAY_DTE_MIN or dte > config.OPTIONS_INTRADAY_DTE_MAX:
                continue

            # V2.3: Get delta and check if within tolerance of target
            contract_delta = abs(contract.Greeks.Delta) if hasattr(contract, "Greeks") else 0.0
            delta_diff = abs(contract_delta - target_delta)
            if delta_diff > config.OPTIONS_DELTA_TOLERANCE:
                continue

            # Check liquidity (relaxed for 0DTE)
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

            # Create OptionContract object (direction already known)
            opt_contract = OptionContract(
                symbol=str(contract.Symbol),
                underlying="QQQ",
                direction=direction,
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
            self.Log(f"INTRADAY: No {direction.value} contracts found matching criteria")
            return None

        # Return best candidate (closest to target delta with lowest DTE)
        candidates.sort(key=lambda x: x[0], reverse=True)
        best = candidates[0][1]
        self.Log(
            f"INTRADAY: Selected {direction.value} | Strike={best.strike} | "
            f"Delta={best.delta:.2f} | DTE={best.days_to_expiry} | Mid=${best.mid_price:.2f}"
        )
        return best

    def _generate_hedge_signals(self, regime_state: RegimeState) -> None:
        """
        Generate Hedge Engine signals at end of day.

        Calculates target TMF and PSQ allocations based on regime level.

        Args:
            regime_state: Current regime state.
        """
        total_equity = self.Portfolio.TotalPortfolioValue
        if total_equity <= 0:
            return

        # Calculate current allocations as percentages
        tmf_pct = self.Portfolio[self.tmf].HoldingsValue / total_equity
        psq_pct = self.Portfolio[self.psq].HoldingsValue / total_equity

        signals = self.hedge_engine.get_hedge_signals(
            regime_score=regime_state.smoothed_score,
            current_tmf_pct=tmf_pct,
            current_psq_pct=psq_pct,
            is_panic_mode=False,
        )
        for signal in signals:
            self.portfolio_router.receive_signal(signal)

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
        V2.3.4: Handle kill switch trigger with ENGINE-SPECIFIC liquidation.

        Instead of liquidating EVERYTHING, we now:
        1. Check if options are causing the loss (look at options P&L)
        2. If options are the culprit, only liquidate OPTIONS (protect trend)
        3. If trend is also losing badly, liquidate trend too
        4. Only do full liquidation for catastrophic losses (>5%)

        Args:
            risk_result: Risk check result containing symbols to liquidate.
        """
        # V2.3 FIX: Only handle kill switch ONCE per day
        if self._kill_switch_handled_today:
            return  # Already handled today, skip repeated processing

        self._kill_switch_handled_today = True

        # V2.3.4: Calculate options P&L vs trend P&L
        options_pnl = 0.0
        trend_pnl = 0.0
        trend_symbols = ["QLD", "SSO", "TNA", "FAS"]

        for kvp in self.Portfolio:
            holding = kvp.Value
            if holding.Invested:
                if holding.Symbol.SecurityType == SecurityType.Option:
                    options_pnl += holding.UnrealizedProfit
                elif str(holding.Symbol.Value) in trend_symbols:
                    trend_pnl += holding.UnrealizedProfit

        total_loss_pct = (
            self.Portfolio.TotalPortfolioValue - self.equity_prior_close
        ) / self.equity_prior_close

        self.Log(
            f"KILL_SWITCH: Triggered at {self.Time} | "
            f"Equity={self.Portfolio.TotalPortfolioValue:,.2f} | "
            f"Loss={total_loss_pct:.2%} | Options P&L=${options_pnl:,.0f} | Trend P&L=${trend_pnl:,.0f}"
        )

        # Trigger in scheduler (disables all trading)
        self.scheduler.trigger_kill_switch()

        # V2.3.4: ENGINE-SPECIFIC LIQUIDATION
        # If options are the primary cause of loss, protect trend positions
        options_are_culprit = options_pnl < -500 and trend_pnl >= -200  # Options losing, trend OK
        catastrophic_loss = total_loss_pct < -0.05  # More than 5% loss = liquidate everything

        if catastrophic_loss:
            # CATASTROPHIC: Liquidate everything
            self.Log("KILL_SWITCH: CATASTROPHIC LOSS - Full liquidation")
            for symbol in risk_result.symbols_to_liquidate:
                self.Liquidate(symbol)
        elif options_are_culprit:
            # OPTIONS-ONLY: Protect trend positions
            self.Log(
                f"KILL_SWITCH: Options-only liquidation (protecting trend with P&L=${trend_pnl:,.0f})"
            )
            # Don't liquidate trend symbols
        else:
            # Mixed losses: Liquidate specified symbols
            for symbol in risk_result.symbols_to_liquidate:
                self.Liquidate(symbol)

        # ALWAYS liquidate options (they're short-term and risky)
        # V2.4.2 FIX: Close SHORT options first (buy to close), then LONG options (sell to close)
        # This avoids margin issues where QC incorrectly calculates margin for selling longs
        # when shorts are still open
        short_options = []
        long_options = []
        for kvp in self.Portfolio:
            holding = kvp.Value
            if holding.Invested and holding.Symbol.SecurityType == SecurityType.Option:
                if holding.Quantity < 0:
                    short_options.append(holding)
                else:
                    long_options.append(holding)

        # Close shorts first (buy to close)
        for holding in short_options:
            self.Log(f"KILL_SWITCH: Closing SHORT option {holding.Symbol} (qty={holding.Quantity})")
            try:
                # Use MarketOrder with explicit quantity to avoid Liquidate() margin quirks
                self.MarketOrder(holding.Symbol, -int(holding.Quantity))
            except Exception as e:
                self.Log(f"KILL_SWITCH: Short option close error: {e}")

        # Then close longs (sell to close)
        for holding in long_options:
            self.Log(f"KILL_SWITCH: Closing LONG option {holding.Symbol} (qty={holding.Quantity})")
            try:
                # Use MarketOrder with explicit quantity
                self.MarketOrder(holding.Symbol, -int(holding.Quantity))
            except Exception as e:
                self.Log(f"KILL_SWITCH: Long option close error: {e}")

        # V2.5 PART 19 FIX: Clear ALL options engine position state
        # This prevents "zombie state" where internal trackers remain set
        # after broker positions are closed, blocking future trades for months
        self.options_engine.clear_all_positions()

        # V2.3.4: Only reset cold start if we liquidated trend positions
        if catastrophic_loss or not options_are_culprit:
            self.cold_start_engine.reset()

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

    def _check_mr_exits(self, data: Slice) -> None:
        """
        Check for Mean Reversion exit conditions.

        Checks target profit and stop loss for existing MR positions.

        Args:
            data: Current data slice.
        """
        # Safety check: Detect sync issues between broker and MR engine
        mr_pos = self.mr_engine.get_position_symbol()
        broker_tqqq = self.Portfolio[self.tqqq].Invested
        broker_soxl = self.Portfolio[self.soxl].Invested

        # If broker has MR position but engine doesn't, we have a sync issue
        if broker_tqqq and mr_pos is None:
            self.Liquidate(self.tqqq)
            return

        if broker_soxl and mr_pos is None:
            self.Liquidate(self.soxl)
            return

        # If engine has position but broker doesn't, clear engine state
        if mr_pos is not None and not broker_tqqq and not broker_soxl:
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

    def _monitor_trend_stops(self, data: Slice) -> None:
        """
        Monitor Trend positions for intraday stop triggers.

        Checks Chandelier trailing stops for QLD, SSO, TNA, and FAS.

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

        # V2.2: Check TNA
        if self.Portfolio[self.tna].Invested:
            signal = self.trend_engine.check_stop_hit(
                symbol="TNA",
                current_price=self.Securities[self.tna].Price,
            )
            if signal:
                self.portfolio_router.receive_signal(signal)
                self._process_immediate_signals()

        # V2.2: Check FAS
        if self.Portfolio[self.fas].Invested:
            signal = self.trend_engine.check_stop_hit(
                symbol="FAS",
                current_price=self.Securities[self.fas].Price,
            )
            if signal:
                self.portfolio_router.receive_signal(signal)
                self._process_immediate_signals()

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
        # V2.3.20: Calculate size multiplier for cold start
        is_cold_start = self.cold_start_engine.is_cold_start_active()
        size_multiplier = config.OPTIONS_COLD_START_MULTIPLIER if is_cold_start else 1.0

        # Skip if indicators not ready
        if not self.qqq_adx.IsReady or not self.qqq_sma200.IsReady:
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
                # Real position exists, skip scanning
                return

        # V2.3 FIX: Skip if kill switch triggered (prevents new entries after liquidation)
        if self._kill_switch_handled_today:
            # V2.3 DEBUG: Log once per day when options blocked by kill switch (live only)
            if self.Time.hour == 10 and self.Time.minute == 30 and self.LiveMode:
                self.Log("OPT_SCAN: Blocked - Kill switch handled today")
            return

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
                return
        except Exception as e:
            self.Log(f"OPTIONS_CHAIN_ERROR: Failed to iterate chain: {e}")
            return

        # Get current values
        qqq_price = self.Securities[self.qqq].Price
        adx_value = self.qqq_adx.Current.Value
        ma200_value = self.qqq_sma200.Current.Value

        # V2.4.1: Throttle intraday scanning to every 15 minutes (was every minute)
        # This reduces 95 scans/hour to 4 scans/hour
        if self._should_scan_intraday() and self._qqq_at_open > 0:
            # V2.4.1 FIX: Use UVXY-derived VIX proxy instead of stale daily VIX
            # self._current_vix is daily close and doesn't change intraday
            vix_intraday = self._get_vix_intraday_proxy()

            # Get macro regime score for direction conflict check
            regime_score = self.regime_engine.get_previous_score()

            # STEP 1: Get engine recommendation (updates micro regime state)
            # V2.3.16: Now includes direction conflict resolution internally
            # V2.4.1: Pass UVXY-derived VIX proxy, not stale daily VIX
            intraday_direction = self.options_engine.get_intraday_direction(
                vix_current=vix_intraday,  # V2.4.1: UVXY proxy
                vix_open=self._vix_at_open,
                qqq_current=qqq_price,
                qqq_open=self._qqq_at_open,
                current_time=str(self.Time),
                regime_score=regime_score,
            )

            # If engine recommends NO_TRADE or conflict detected, skip contract selection
            if intraday_direction is None:
                intraday_contract = None
            else:
                # STEP 2: Select contract matching ENGINE recommendation (not hardcoded fade)
                intraday_contract = self._select_intraday_option_contract(chain, intraday_direction)

            # Verify contract has valid bid/ask before proceeding
            if (
                intraday_contract is not None
                and intraday_contract.bid > 0
                and intraday_contract.ask > 0
            ):
                # V2.3.20: Pass size_multiplier for cold start reduced sizing
                # V2.4.1: Pass UVXY-derived VIX proxy
                # V2.5: Pass macro_regime_score for Grind-Up Override
                # V2.7: Use tradeable equity (not total portfolio) for cash-only sizing
                intraday_signal = self.options_engine.check_intraday_entry_signal(
                    vix_current=vix_intraday,  # V2.4.1: UVXY proxy
                    vix_open=self._vix_at_open,
                    qqq_current=qqq_price,
                    qqq_open=self._qqq_at_open,
                    current_hour=self.Time.hour,
                    current_minute=self.Time.minute,
                    current_time=str(self.Time),
                    portfolio_value=self.capital_engine.get_tradeable_equity(),
                    best_contract=intraday_contract,
                    size_multiplier=size_multiplier,
                    macro_regime_score=self._last_regime_score,
                )
                if intraday_signal:
                    self.portfolio_router.receive_signal(intraday_signal)
                    # V2.3.13 FIX: MUST process immediately - signal was being queued but never executed!
                    # The function returns early via swing spread path, so signal was lost
                    self._process_immediate_signals()
                    # V2.3.3 FIX: Don't return here - allow swing check to run too
                    # Previously returned early, blocking swing spreads entirely

        # V2.3: Check for SWING mode entry using DEBIT SPREADS
        # Direction based on regime score (not Price/MA200/RSI)
        # - Regime > 60: Bull Call Spread
        # - Regime < 45: Bear Put Spread
        # - Regime 45-60: No trade (neutral)
        regime_score = self.regime_engine.get_previous_score()

        if regime_score > config.SPREAD_REGIME_BULLISH:
            direction = OptionDirection.CALL
        elif regime_score < config.SPREAD_REGIME_BEARISH:
            direction = OptionDirection.PUT
        else:
            # Neutral regime (45-60): No trade
            return

        # Build candidate contracts for spread selection
        candidate_contracts = self._build_spread_candidate_contracts(chain, direction)
        if len(candidate_contracts) < 2:
            return

        # V2.3.21: Select spread legs (ITM long + OTM short) with 15-min throttle
        spread_legs = self.options_engine.select_spread_legs(
            contracts=candidate_contracts,
            direction=direction,
            current_time=str(self.Time),
        )

        # Calculate IV rank from options chain (V2.1)
        iv_rank = self._calculate_iv_rank(chain)

        if spread_legs is not None:
            long_leg, short_leg = spread_legs

            # Verify both legs have valid bid/ask
            if long_leg.bid > 0 and long_leg.ask > 0 and short_leg.bid > 0 and short_leg.ask > 0:
                # V2.3: Check for spread entry signal
                # V2.3.20: Pass size_multiplier for cold start reduced sizing
                # V2.7: Use tradeable equity (not total portfolio) for cash-only sizing
                signal = self.options_engine.check_spread_entry_signal(
                    regime_score=regime_score,
                    vix_current=self._current_vix,
                    adx_value=adx_value,
                    current_price=qqq_price,
                    ma200_value=ma200_value,
                    iv_rank=iv_rank,
                    current_hour=self.Time.hour,
                    current_minute=self.Time.minute,
                    current_date=str(self.Time.date()),
                    portfolio_value=self.capital_engine.get_tradeable_equity(),
                    long_leg_contract=long_leg,
                    short_leg_contract=short_leg,
                    gap_filter_triggered=self.risk_engine.is_gap_filter_active(),
                    vol_shock_active=self.risk_engine.is_vol_shock_active(self.Time),
                    size_multiplier=size_multiplier,
                )

                if signal:
                    self.portfolio_router.receive_signal(signal)
                    # V2.3.13 FIX: MUST process immediately - spread signals use IMMEDIATE urgency
                    self._process_immediate_signals()
                    return  # Spread trade placed, don't try single-leg

        # V2.4.1: Single-leg swing fallback - DISABLED by default for safety
        # Single-leg has higher delta exposure and full premium at risk
        # If spread fails, stay cash (Safety First approach)
        if config.SWING_FALLBACK_ENABLED and not self.options_engine.has_position():
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
                )

                if signal:
                    self.Log(f"SWING_FALLBACK: Single-leg {direction.value} after spread failure")
                    self.portfolio_router.receive_signal(signal)
                    self._process_immediate_signals()
        elif not config.SWING_FALLBACK_ENABLED and not self.options_engine.has_position():
            self.Log("SWING: Spread construction failed - staying cash (fallback disabled)")

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
        spread = self.options_engine.get_spread_position()
        if spread is None:
            return

        # V2.6 Bug #14: Force exit 30 min before close for 0DTE positions
        current_dte = spread.long_leg.days_to_expiry  # Initial estimate
        if (
            current_dte <= 0
            and self.Time.hour == config.ZERO_DTE_FORCE_EXIT_HOUR
            and self.Time.minute >= config.ZERO_DTE_FORCE_EXIT_MINUTE
        ):
            self.Log(
                f"0DTE_FIREWALL: Forcing exit 30 min before close | "
                f"Time={self.Time.strftime('%H:%M')}"
            )
            self._force_spread_exit("0DTE_TIME_DECAY")
            return

        # Get current prices for both legs from chain
        chain = (
            data.OptionChains[self._qqq_option_symbol]
            if self._qqq_option_symbol in data.OptionChains
            else None
        )

        long_leg_price = None
        short_leg_price = None

        if chain is not None:
            try:
                for contract in chain:
                    contract_symbol = str(contract.Symbol)
                    if contract_symbol == spread.long_leg.symbol:
                        bid, ask = self._get_contract_prices(contract)
                        long_leg_price = (bid + ask) / 2 if bid > 0 and ask > 0 else None
                        current_dte = (contract.Expiry - self.Time).days
                    elif contract_symbol == spread.short_leg.symbol:
                        bid, ask = self._get_contract_prices(contract)
                        short_leg_price = (bid + ask) / 2 if bid > 0 and ask > 0 else None
            except Exception as e:
                self.Log(f"SPREAD_EXIT_ERROR: Failed to get prices from chain: {e}")

        # V2.6 Bug #3: Fallback to Security price when chain data missing
        if long_leg_price is None:
            try:
                long_sec = self.Securities.get(spread.long_leg.symbol)
                if long_sec and long_sec.Price > 0:
                    long_leg_price = long_sec.Price
                    self.Log(
                        f"SPREAD_EXIT: Using Security price for long leg: ${long_leg_price:.2f}"
                    )
            except Exception:
                pass

        if short_leg_price is None:
            try:
                short_sec = self.Securities.get(spread.short_leg.symbol)
                if short_sec and short_sec.Price > 0:
                    short_leg_price = short_sec.Price
                    self.Log(
                        f"SPREAD_EXIT: Using Security price for short leg: ${short_leg_price:.2f}"
                    )
            except Exception:
                pass

        # V2.6 Bug #3: Force exit if near expiration and no price data
        if long_leg_price is None or short_leg_price is None:
            if current_dte is not None and current_dte <= 2:
                self.Log(
                    f"SPREAD_EXIT: EMERGENCY - No price data but DTE={current_dte} <= 2 | "
                    f"Forcing exit to avoid expiration risk"
                )
                self._force_spread_exit("NO_PRICE_DATA_NEAR_EXPIRY")
                return

            # Still no prices - log but continue checking on next tick
            self.Log(
                f"SPREAD_EXIT_WARNING: Missing price data | "
                f"Long={long_leg_price} Short={short_leg_price} | "
                f"Will retry next tick"
            )
            return

        if current_dte is None:
            current_dte = spread.long_leg.days_to_expiry  # Fallback

        # Get current regime score
        regime_score = self.regime_engine.get_previous_score()

        # Check for exit signals
        exit_signals = self.options_engine.check_spread_exit_signals(
            long_leg_price=long_leg_price,
            short_leg_price=short_leg_price,
            regime_score=regime_score,
            current_dte=current_dte,
        )

        if exit_signals:
            # Send both exit signals (long leg and short leg)
            for signal in exit_signals:
                self.portfolio_router.receive_signal(signal)

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

    def _calculate_regime(self) -> RegimeState:
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
                vix_level=20.0,
                realized_vol=0.0,
                vol_percentile=50.0,
                breadth_spread_value=0.0,
                credit_spread_value=0.0,
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

        # V2.3: Pass VIX to regime calculation
        return self.regime_engine.calculate(
            spy_closes=spy_prices,
            rsp_closes=rsp_prices,
            hyg_closes=hyg_prices,
            ief_closes=ief_prices,
            spy_sma20=self.spy_sma20.Current.Value,
            spy_sma50=self.spy_sma50.Current.Value,
            spy_sma200=self.spy_sma200.Current.Value,
            vix_level=self._current_vix,
        )

    def _log_daily_summary(self) -> None:
        """
        Log end of day summary.

        Includes P&L, trades, safeguards, and regime state.
        """
        ending_equity = self.Portfolio.TotalPortfolioValue
        regime_state = self._calculate_regime()
        capital_state = self.capital_engine.calculate(ending_equity)

        summary = self.scheduler.get_day_summary(
            starting_equity=self.equity_sod,
            ending_equity=ending_equity,
            trades=self.today_trades,
            safeguards=self.today_safeguards,
            moo_orders=[],  # Already logged when submitted
            regime_score=regime_state.smoothed_score,
            regime_state=regime_state.state.value,
            phase=capital_state.current_phase.value,
            days_running=self.cold_start_engine.get_days_running(),
        )

        self.Log(summary)

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
        # Update trend engine if QLD/SSO/TNA/FAS (V2.2)
        if symbol in ["QLD", "SSO", "TNA", "FAS"]:
            if fill_qty > 0:
                # Get ATR for initial stop calculation
                atr_value = 0.0
                if symbol == "QLD" and self.qld_atr.IsReady:
                    atr_value = self.qld_atr.Current.Value
                elif symbol == "SSO" and self.sso_atr.IsReady:
                    atr_value = self.sso_atr.Current.Value
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
                self.trend_engine.remove_position(symbol)

        # Update MR engine if TQQQ/SOXL
        if symbol in ["TQQQ", "SOXL"]:
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
                    self.mr_engine.remove_position()
            except Exception as e:
                self.Log(f"MR_TRACK_ERROR: {symbol}: {e}")

        # V2.1/V2.3: Update Options engine if QQQ option
        if "QQQ" in symbol and ("C" in symbol or "P" in symbol):
            try:
                # V2.5 FIX: Check spread mode FIRST (before fill_qty sign check)
                # Bull Call Spread: Long leg = BUY (qty > 0), Short leg = SELL (qty < 0)
                # The bug was: "if fill_qty > 0" excluded short leg fills entirely!
                if self.options_engine._pending_spread_long_leg is not None:
                    # Spread mode: track ANY leg fill (long=positive, short=negative)
                    # Use abs(fill_qty) because _handle_spread_leg_fill expects positive qty
                    self._handle_spread_leg_fill(symbol, fill_price, abs(fill_qty))
                elif fill_qty > 0:
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
                            self.oco_manager.submit_oco_pair(oco_pair, current_time=str(self.Time))
                            self.Log(
                                f"OPT: OCO pair created | "
                                f"Stop=${position.stop_price:.2f} | "
                                f"Target=${position.target_price:.2f}"
                            )
                elif fill_qty < 0:
                    # Exit - check position type (spread, intraday, or legacy single-leg)
                    if self.options_engine.has_spread_position():
                        # Spread exit - track leg closes
                        self._handle_spread_leg_close(symbol, fill_price, fill_qty)
                    elif self.options_engine.has_intraday_position():
                        # V2.3.2: Intraday exit
                        self.options_engine.remove_intraday_position()
                        self._greeks_breach_logged = False  # Reset for next position
                    else:
                        # Single-leg exit (legacy swing)
                        self.options_engine.remove_position()
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
                regime_score=self.regime_engine.get_previous_score(),
            )

            if spread:
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
        spread = self.options_engine.get_spread_position()
        if spread is None:
            return

        fill_qty_abs = int(abs(fill_qty))

        # Initialize close tracking if this is first close fill
        if not self._spread_long_closed and not self._spread_short_closed:
            self._spread_close_expected_qty = spread.num_spreads
            self._spread_close_qty_long = 0
            self._spread_close_qty_short = 0

        # Track which leg closed and accumulate quantity
        if spread.long_leg.symbol == symbol:
            self._spread_long_closed = True
            self._spread_close_qty_long += fill_qty_abs
            self.Log(
                f"SPREAD: Long leg closed | {symbol[-20:]} @ ${fill_price:.2f} x{fill_qty_abs} | "
                f"Total closed={self._spread_close_qty_long}/{self._spread_close_expected_qty}"
            )
        elif spread.short_leg.symbol == symbol:
            self._spread_short_closed = True
            self._spread_close_qty_short += fill_qty_abs
            self.Log(
                f"SPREAD: Short leg closed | {symbol[-20:]} @ ${fill_price:.2f} x{fill_qty_abs} | "
                f"Total closed={self._spread_close_qty_short}/{self._spread_close_expected_qty}"
            )

        # Check if both legs closed
        if self._spread_long_closed and self._spread_short_closed:
            # Validate quantities (Bug #8 fix)
            if self._spread_close_qty_long != self._spread_close_expected_qty:
                self.Log(
                    f"SPREAD_WARNING: Long leg quantity mismatch | "
                    f"Closed={self._spread_close_qty_long} Expected={self._spread_close_expected_qty}"
                )

            if self._spread_close_qty_short != self._spread_close_expected_qty:
                self.Log(
                    f"SPREAD_WARNING: Short leg quantity mismatch | "
                    f"Closed={self._spread_close_qty_short} Expected={self._spread_close_expected_qty}"
                )

            # Both legs closed - remove spread position
            self.options_engine.remove_spread_position()
            self._greeks_breach_logged = False  # Reset for next position

            # Clear tracking
            self._spread_long_closed = False
            self._spread_short_closed = False
            self._spread_close_qty_long = 0
            self._spread_close_qty_short = 0
            self._spread_close_expected_qty = 0

            self.Log("SPREAD: Position removed - both legs closed")

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

        # Clear options engine pending state
        self.options_engine._pending_spread_long_leg = None
        self.options_engine._pending_spread_short_leg = None
        self.options_engine._pending_spread_type = None
        self.options_engine._pending_net_debit = None
        self.options_engine._pending_max_profit = None
        self.options_engine._pending_spread_width = None

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

        Args:
            symbol: Symbol to force close.
        """
        self.Log(f"EXIT_EMERGENCY: Force market close | {symbol[-20:]}")
        try:
            holding = self.Portfolio.get(symbol)
            if holding and holding.Invested:
                qty = holding.Quantity
                # Use Liquidate for absolute closure
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

        Returns:
            Dict of symbol -> current price.
        """
        prices = {}
        # Equity prices
        for symbol in self.traded_symbols:
            prices[str(symbol.Value)] = self.Securities[symbol].Price

        # Options prices - get from Securities for any option we're tracking
        for kvp in self.Securities:
            symbol = kvp.Key
            security = kvp.Value
            if symbol.SecurityType == SecurityType.Option and security.Price > 0:
                prices[str(symbol.Value)] = security.Price

        return prices
