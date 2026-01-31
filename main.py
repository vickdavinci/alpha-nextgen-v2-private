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
from engines.satellite.options_engine import OptionContract, OptionDirection, OptionsEngine
from engines.satellite.yield_sleeve import YieldSleeve
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
            tqqq, soxl, qld, sso, tna, fas, tmf, psq, shv: Symbol - Traded symbols
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
            yield_sleeve: YieldSleeve - SHV cash management

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
    shv: Symbol
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
    yield_sleeve: YieldSleeve
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
        self.SetStartDate(2024, 1, 2)
        self.SetEndDate(2024, 1, 8)  # 1 week for focused options debugging
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
        if mr_window_open and risk_result.can_enter_intraday:
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

        # Traded symbols - Yield
        self.shv = self.AddEquity("SHV", Resolution.Minute).Symbol

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
        # Add CBOE VIX index data for regime classification
        self.vix = self.AddData(CBOE, "VIX", Resolution.Daily).Symbol
        self._current_vix = 15.0  # Default to normal regime until data arrives
        self._vix_at_open = 15.0  # V2.1.1: VIX at market open for micro regime
        self._vix_5min_ago = 15.0  # V2.1.1: VIX 5 minutes ago for spike detection
        self._last_vix_spike_log = None  # Log throttle: last VIX spike log time
        self._qqq_at_open = 0.0  # V2.1.1: QQQ at market open

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
            self.shv,
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
            - YieldSleeve: SHV cash management

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
        self.yield_sleeve = YieldSleeve(self)

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
        if regime_state.smoothed_score >= 40:
            self._generate_options_signals(regime_state, capital_state)

        # 5. Generate Hedge signals
        self._generate_hedge_signals(regime_state)

        # 6. Generate Yield signals
        self._generate_yield_signals(capital_state)

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

    def _on_intraday_options_force_close(self) -> None:
        """
        V2.1.1: Intraday options force close at 15:30 ET.

        Forces close of all intraday mode options positions (0-2 DTE).
        These must close 30 minutes before market close.
        """
        # Skip during warmup
        if self.IsWarmingUp:
            return

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

    def _on_vix_spike_check(self) -> None:
        """
        V2.1.1: Layer 1 VIX spike detection (every 5 minutes).

        Checks for sudden VIX spikes (>3% in 5 minutes).
        Sets spike alert cooldown if triggered.
        """
        # Skip during warmup
        if self.IsWarmingUp:
            return

        # Check for spike
        spike_alert = self.options_engine._micro_regime_engine.check_spike_alert(
            vix_current=self._current_vix,
            vix_5min_ago=self._vix_5min_ago,
            current_time=str(self.Time),
        )

        if spike_alert:
            # Throttle VIX spike logs: 1 per LOG_THROTTLE_MINUTES OR if move > threshold
            vix_move = abs(self._current_vix - self._vix_5min_ago)
            should_log = (
                not hasattr(self, "_last_vix_spike_log")
                or self._last_vix_spike_log is None
                or (self.Time - self._last_vix_spike_log).total_seconds() / 60
                > config.LOG_THROTTLE_MINUTES
                or vix_move >= config.LOG_VIX_SPIKE_MIN_MOVE
            )
            if should_log:
                self.Log(f"VIX_SPIKE: {self._vix_5min_ago:.1f} -> {self._current_vix:.1f}")
                self._last_vix_spike_log = self.Time

        # Update 5-min ago value for next check
        self._vix_5min_ago = self._current_vix

    def _on_micro_regime_update(self) -> None:
        """
        V2.1.1: Layer 2 & 4 - Direction + Regime update (every 15 minutes).

        Updates the Micro Regime Engine with current market data.
        """
        # Skip during warmup
        if self.IsWarmingUp:
            return

        # Get current QQQ price
        qqq_current = self.Securities[self.qqq].Price

        # Update micro regime engine
        state = self.options_engine._micro_regime_engine.update(
            vix_current=self._current_vix,
            vix_open=self._vix_at_open,
            qqq_current=qqq_current,
            qqq_open=self._qqq_at_open,
            current_time=str(self.Time),
        )

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

        elif orderEvent.Status == OrderStatus.Invalid:
            self.Log(f"INVALID: {orderEvent.Symbol} - {orderEvent.Message}")
            self.execution_engine.on_order_event(
                broker_order_id=orderEvent.OrderId,
                status="Invalid",
                rejection_reason=orderEvent.Message,
            )

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

            # Calculate total allocation from signals (excluding SHV)
            total_non_shv_allocation = sum(
                agg.target_weight for sym, agg in aggregated.items() if sym != "SHV"
            )

            # Calculate SHV target weight (remaining allocation)
            # This ensures SHV is reduced when other positions are added
            shv_target_weight = max(0.0, 1.0 - total_non_shv_allocation)

            # If SHV signal exists, use the lower of calculated vs signal
            if "SHV" in aggregated:
                shv_target_weight = min(shv_target_weight, aggregated["SHV"].target_weight)

            # Build list of portfolio targets for SetHoldings
            targets = []
            for symbol, agg in aggregated.items():
                if symbol == "SHV":
                    continue  # Handle SHV separately
                # Get the actual Symbol object
                symbol_obj = None
                for s in self.traded_symbols:
                    if str(s.Value) == symbol:
                        symbol_obj = s
                        break

                if symbol_obj and agg.target_weight >= 0:
                    targets.append(PortfolioTarget(symbol_obj, agg.target_weight))

            # Add SHV target (this ensures SHV is sold to make room)
            targets.append(PortfolioTarget(self.shv, shv_target_weight))

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
        symbol_data = {
            "QLD": (self.qld, self.qld_ma200, self.qld_adx, self.qld_atr),
            "SSO": (self.sso, self.sso_ma200, self.sso_adx, self.sso_atr),
            "TNA": (self.tna, self.tna_ma200, self.tna_adx, self.tna_atr),
            "FAS": (self.fas, self.fas_ma200, self.fas_adx, self.fas_atr),
        }

        # Process each symbol in priority order
        for symbol in trend_symbols:
            security, ma200_ind, adx_ind, atr_ind = symbol_data[symbol]

            # Skip if indicators not ready
            if not (ma200_ind.IsReady and adx_ind.IsReady and atr_ind.IsReady):
                continue

            close = self.Securities[security].Close
            high = self.Securities[security].High
            ma200 = ma200_ind.Current.Value
            adx = adx_ind.Current.Value
            atr = atr_ind.Current.Value

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
        self, regime_state: RegimeState, capital_state: CapitalState
    ) -> None:
        """
        Generate Options Engine signals at end of day.

        V2.3: Debit spread entry using regime-based direction.
        - Regime > 60: Bull Call Spread
        - Regime < 45: Bear Put Spread
        - Regime 45-60: No trade (neutral)

        Args:
            regime_state: Current regime state.
            capital_state: Current capital state.
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

        # V2.3: Select spread legs (ATM long + OTM short)
        spread_legs = self.options_engine.select_spread_legs(
            contracts=candidate_contracts,
            direction=direction,
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
            portfolio_value=self.Portfolio.TotalPortfolioValue,
            long_leg_contract=long_leg,
            short_leg_contract=short_leg,
            gap_filter_triggered=self.risk_engine.is_gap_filter_active(),
            vol_shock_active=self.risk_engine.is_vol_shock_active(self.Time),
        )

        if signal:
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
                # Estimate bid/ask from last price (assume 1% spread)
                bid = last * 0.995
                ask = last * 1.005

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

    def _select_intraday_option_contract(self, chain) -> Optional[OptionContract]:
        """
        V2.3: Select QQQ option contract for intraday mode (0-5 DTE).

        Target delta: 0.30 (slightly OTM for faster gamma/premium moves)

        Criteria:
        - Target 0.30 delta (±0.15 tolerance)
        - DTE 0-5 days (expanded from 0-2 for backtest data availability)
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
        target_delta = config.OPTIONS_INTRADAY_DELTA_TARGET  # 0.30

        # Filter for target delta, 0-5 DTE (expanded from 0-2 for backtest data availability)
        candidates = []
        for contract in chain:
            # Check DTE using config values
            dte = (contract.Expiry - self.Time).days
            if dte < config.OPTIONS_INTRADAY_DTE_MIN or dte > config.OPTIONS_INTRADAY_DTE_MAX:
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

            # Determine direction
            direction = (
                OptionDirection.CALL if contract.Right == OptionRight.Call else OptionDirection.PUT
            )

            # Create OptionContract object
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

            # V2.3: Score by proximity to target delta (0.30) + liquidity + lower DTE
            delta_score = 1.0 - (delta_diff / config.OPTIONS_DELTA_TOLERANCE)
            dte_score = 1.0 / (1.0 + dte)  # Prefer lower DTE
            spread_score = 1.0 - spread_pct
            score = (delta_score * 0.5) + (dte_score * 0.3) + (spread_score * 0.2)
            candidates.append((score, opt_contract))

        if not candidates:
            return None

        # Return best candidate (closest to target delta with good liquidity)
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

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

    def _generate_yield_signals(self, capital_state: CapitalState) -> None:
        """
        Generate Yield Sleeve signals at end of day.

        Manages SHV (cash) allocation based on available capital.

        Args:
            capital_state: Current capital state.
        """
        total_equity = self.Portfolio.TotalPortfolioValue
        shv_value = self.Portfolio[self.shv].HoldingsValue

        # Calculate non-SHV positions value
        non_shv_value = sum(
            self.Portfolio[sym].HoldingsValue for sym in self.traded_symbols if sym != self.shv
        )

        signal = self.yield_sleeve.get_yield_signal(
            total_equity=total_equity,
            tradeable_equity=capital_state.tradeable_eq,
            non_shv_positions_value=non_shv_value,
            current_shv_value=shv_value,
            locked_amount=capital_state.locked_amount,
        )
        if signal:
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
        Handle kill switch trigger.

        Liquidates ALL positions and resets cold start.
        V2.3 Fix: Only handles once per day to prevent log spam and repeated liquidation.

        Args:
            risk_result: Risk check result containing symbols to liquidate.
        """
        # V2.3 FIX: Only handle kill switch ONCE per day
        if self._kill_switch_handled_today:
            return  # Already handled today, skip repeated processing

        self._kill_switch_handled_today = True
        self.Log(
            f"KILL_SWITCH: Triggered at {self.Time} | "
            f"Equity={self.Portfolio.TotalPortfolioValue:,.2f} | "
            f"Scheduler flag set, entries blocked until next day"
        )

        # Trigger in scheduler (disables all trading)
        self.scheduler.trigger_kill_switch()

        # Liquidate all equity positions
        for symbol in risk_result.symbols_to_liquidate:
            self.Liquidate(symbol)

        # V2.3 FIX: Liquidate ALL options in portfolio (not just tracked positions)
        # This handles orphan positions from previous sessions or untracked orders
        for kvp in self.Portfolio:
            holding = kvp.Value
            if holding.Invested and holding.Symbol.SecurityType == SecurityType.Option:
                self.Log(f"KILL_SWITCH: Liquidating option {holding.Symbol}")
                try:
                    self.Liquidate(holding.Symbol)
                except Exception as e:
                    self.Log(f"KILL_SWITCH: Options liquidation error: {e}")

        # Clear tracked position state if any
        if self.options_engine.has_position():
            self.options_engine.remove_position()
        # Clear any pending entry state
        self.options_engine._pending_contract = None

        # Reset cold start
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

        Args:
            data: Current data slice.
        """
        # Skip if indicators not ready
        if not self.qqq_adx.IsReady or not self.qqq_sma200.IsReady:
            return

        # Skip if already have options position (either mode)
        if self.options_engine.has_position():
            return

        # V2.3 FIX: Skip if kill switch triggered (prevents new entries after liquidation)
        if self._kill_switch_handled_today:
            # V2.3 DEBUG: Log once per day when options blocked by kill switch (live only)
            if self.Time.hour == 10 and self.Time.minute == 30 and self.LiveMode:
                self.Log("OPT_SCAN: Blocked - Kill switch handled today")
            return

        # V2.3 FIX: Only scan during active window (10:30-15:00)
        # 30-minute delay allows market to settle after open volatility
        current_hour = self.Time.hour
        current_minute = self.Time.minute
        # Before 10:30 or after 15:00 -> skip
        if current_hour < 10 or current_hour >= 15:
            return
        if current_hour == 10 and current_minute < 30:
            return  # 10:00-10:29 -> skip, wait for market settling

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

        # V2.1.1: Check for INTRADAY mode entry (0-2 DTE) using Micro Regime Engine
        if self._qqq_at_open > 0:  # Only if we have market open data
            intraday_contract = self._select_intraday_option_contract(chain)
            # Verify contract has valid bid/ask before proceeding
            if (
                intraday_contract is not None
                and intraday_contract.bid > 0
                and intraday_contract.ask > 0
            ):
                intraday_signal = self.options_engine.check_intraday_entry_signal(
                    vix_current=self._current_vix,
                    vix_open=self._vix_at_open,
                    qqq_current=qqq_price,
                    qqq_open=self._qqq_at_open,
                    current_hour=self.Time.hour,
                    current_minute=self.Time.minute,
                    current_time=str(self.Time),
                    portfolio_value=self.Portfolio.TotalPortfolioValue,
                    best_contract=intraday_contract,
                )
                if intraday_signal:
                    self.portfolio_router.receive_signal(intraday_signal)
                    return  # Only one entry per scan

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

        # Select spread legs (ATM long + OTM short)
        spread_legs = self.options_engine.select_spread_legs(
            contracts=candidate_contracts,
            direction=direction,
        )
        if spread_legs is None:
            return

        long_leg, short_leg = spread_legs

        # Verify both legs have valid bid/ask
        if long_leg.bid <= 0 or long_leg.ask <= 0:
            return
        if short_leg.bid <= 0 or short_leg.ask <= 0:
            return

        # Calculate IV rank from options chain (V2.1)
        iv_rank = self._calculate_iv_rank(chain)

        # V2.3: Check for spread entry signal
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
            portfolio_value=self.Portfolio.TotalPortfolioValue,
            long_leg_contract=long_leg,
            short_leg_contract=short_leg,
            gap_filter_triggered=self.risk_engine.is_gap_filter_active(),
            vol_shock_active=self.risk_engine.is_vol_shock_active(self.Time),
        )

        if signal:
            self.portfolio_router.receive_signal(signal)

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

        # Get current prices for both legs from chain
        chain = (
            data.OptionChains[self._qqq_option_symbol]
            if self._qqq_option_symbol in data.OptionChains
            else None
        )
        if chain is None:
            return

        long_leg_price = None
        short_leg_price = None
        current_dte = None

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
            self.Log(f"SPREAD_EXIT_ERROR: Failed to get prices: {e}")
            return

        # Skip if we couldn't get both prices
        if long_leg_price is None or short_leg_price is None:
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
                if fill_qty > 0:
                    # V2.3: Check if this is a spread leg fill
                    if self.options_engine._pending_spread_long_leg is not None:
                        # This is a spread entry - track leg fills
                        self._handle_spread_leg_fill(symbol, fill_price, fill_qty)
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
        V2.3: Handle a spread leg fill.

        Tracks both leg fills and registers spread position when both complete.

        Args:
            symbol: Filled option symbol.
            fill_price: Fill price.
            fill_qty: Fill quantity.
        """
        # Track which leg filled
        pending_long = self.options_engine._pending_spread_long_leg
        pending_short = self.options_engine._pending_spread_short_leg

        if pending_long and pending_long.symbol == symbol:
            # Long leg filled
            if not hasattr(self, "_spread_long_fill_price"):
                self._spread_long_fill_price = None
                self._spread_short_fill_price = None
            self._spread_long_fill_price = fill_price
            self.Log(f"SPREAD: Long leg filled | {symbol} @ ${fill_price:.2f}")

        elif pending_short and pending_short.symbol == symbol:
            # Short leg filled
            if not hasattr(self, "_spread_short_fill_price"):
                self._spread_long_fill_price = None
                self._spread_short_fill_price = None
            self._spread_short_fill_price = fill_price
            self.Log(f"SPREAD: Short leg filled | {symbol} @ ${fill_price:.2f}")

        # Check if both legs filled
        long_fill = getattr(self, "_spread_long_fill_price", None)
        short_fill = getattr(self, "_spread_short_fill_price", None)

        if long_fill is not None and short_fill is not None:
            # Both legs filled - register spread position
            spread = self.options_engine.register_spread_entry(
                long_leg_fill_price=long_fill,
                short_leg_fill_price=short_fill,
                entry_time=str(self.Time),
                current_date=str(self.Time.date()),
                regime_score=self.regime_engine.get_previous_score(),
            )

            if spread:
                self.Log(
                    f"SPREAD: Position registered | {spread.spread_type} | "
                    f"Debit=${spread.net_debit:.2f} | Max Profit=${spread.max_profit:.2f}"
                )

            # Clear tracking
            self._spread_long_fill_price = None
            self._spread_short_fill_price = None

    def _handle_spread_leg_close(self, symbol: str, fill_price: float, fill_qty: float) -> None:
        """
        V2.3: Handle a spread leg close.

        Tracks both leg closes and removes spread position when both complete.

        Args:
            symbol: Closed option symbol.
            fill_price: Fill price.
            fill_qty: Fill quantity.
        """
        spread = self.options_engine.get_spread_position()
        if spread is None:
            return

        # Track which leg closed
        if not hasattr(self, "_spread_long_closed"):
            self._spread_long_closed = False
            self._spread_short_closed = False

        if spread.long_leg.symbol == symbol:
            self._spread_long_closed = True
            self.Log(f"SPREAD: Long leg closed | {symbol} @ ${fill_price:.2f}")
        elif spread.short_leg.symbol == symbol:
            self._spread_short_closed = True
            self.Log(f"SPREAD: Short leg closed | {symbol} @ ${fill_price:.2f}")

        # Check if both legs closed
        if self._spread_long_closed and self._spread_short_closed:
            # Both legs closed - remove spread position
            self.options_engine.remove_spread_position()
            self._greeks_breach_logged = False  # Reset for next position

            # Clear tracking
            self._spread_long_closed = False
            self._spread_short_closed = False

            self.Log("SPREAD: Position removed - both legs closed")

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
