# region imports
from AlgorithmImports import *

# Configuration
import config

# Engines
from engines.regime_engine import RegimeEngine, RegimeState
from engines.capital_engine import CapitalEngine, CapitalState
from engines.risk_engine import RiskEngine, RiskCheckResult, SafeguardType
from engines.cold_start_engine import ColdStartEngine
from engines.trend_engine import TrendEngine
from engines.mean_reversion_engine import MeanReversionEngine
from engines.hedge_engine import HedgeEngine
from engines.yield_sleeve import YieldSleeve

# Portfolio & Execution
from portfolio.portfolio_router import PortfolioRouter
from execution.execution_engine import ExecutionEngine

# Infrastructure
from persistence.state_manager import StateManager
from scheduling.daily_scheduler import DailyScheduler

# Models
from models.enums import Urgency, Phase, RegimeLevel
from models.target_weight import TargetWeight

# Type hints
from typing import List, Optional, Set, Dict, Any
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
            tqqq, soxl, qld, sso, tmf, psq, shv: Symbol - Traded symbols
            traded_symbols: List[Symbol] - All traded symbols
            proxy_symbols: List[Symbol] - All proxy symbols

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
            qld_bb, sso_bb: BollingerBands - Compression breakout
            qld_atr, sso_atr: AverageTrueRange - Chandelier stops
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

    # Symbol references (traded)
    tqqq: Symbol
    soxl: Symbol
    qld: Symbol
    sso: Symbol
    tmf: Symbol
    psq: Symbol
    shv: Symbol

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
        self.SetStartDate(2020, 1, 1)
        self.SetEndDate(2024, 12, 31)
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
        # STEP 7: CHECK MR EXITS (always if position exists)
        # =====================================================================
        self._check_mr_exits(data)

        # =====================================================================
        # STEP 8: TREND POSITION MONITORING (intraday stop check)
        # =====================================================================
        self._monitor_trend_stops(data)

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
        # Traded symbols - Leveraged longs
        self.tqqq = self.AddEquity("TQQQ", Resolution.Minute).Symbol
        self.soxl = self.AddEquity("SOXL", Resolution.Minute).Symbol
        self.qld = self.AddEquity("QLD", Resolution.Minute).Symbol
        self.sso = self.AddEquity("SSO", Resolution.Minute).Symbol

        # Traded symbols - Hedges
        self.tmf = self.AddEquity("TMF", Resolution.Minute).Symbol
        self.psq = self.AddEquity("PSQ", Resolution.Minute).Symbol

        # Traded symbols - Yield
        self.shv = self.AddEquity("SHV", Resolution.Minute).Symbol

        # Proxy symbols - For regime calculation
        self.spy = self.AddEquity("SPY", Resolution.Minute).Symbol
        self.rsp = self.AddEquity("RSP", Resolution.Minute).Symbol
        self.hyg = self.AddEquity("HYG", Resolution.Minute).Symbol
        self.ief = self.AddEquity("IEF", Resolution.Minute).Symbol

        # Store collections for iteration
        self.traded_symbols = [
            self.tqqq,
            self.soxl,
            self.qld,
            self.sso,
            self.tmf,
            self.psq,
            self.shv,
        ]
        self.proxy_symbols = [self.spy, self.rsp, self.hyg, self.ief]

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
            - QLD/SSO Bollinger Bands: Compression breakout for trend engine
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
        self.spy_atr = self.ATR(self.spy, config.ATR_PERIOD, MovingAverageType.Wilders, Resolution.Minute)

        # ---------------------------------------------------------------------
        # Trend Engine Indicators (Daily resolution for EOD signals)
        # ---------------------------------------------------------------------
        # Bollinger Bands for compression breakout detection
        self.qld_bb = self.BB(self.qld, config.BB_PERIOD, config.BB_STD_DEV, MovingAverageType.Simple, Resolution.Daily)
        self.sso_bb = self.BB(self.sso, config.BB_PERIOD, config.BB_STD_DEV, MovingAverageType.Simple, Resolution.Daily)

        # ATR for Chandelier stop calculation
        self.qld_atr = self.ATR(self.qld, config.ATR_PERIOD, MovingAverageType.Wilders, Resolution.Daily)
        self.sso_atr = self.ATR(self.sso, config.ATR_PERIOD, MovingAverageType.Wilders, Resolution.Daily)

        # ---------------------------------------------------------------------
        # Mean Reversion Engine Indicators (Minute resolution for intraday)
        # ---------------------------------------------------------------------
        self.tqqq_rsi = self.RSI(self.tqqq, config.RSI_PERIOD, MovingAverageType.Wilders, Resolution.Minute)
        self.soxl_rsi = self.RSI(self.soxl, config.RSI_PERIOD, MovingAverageType.Wilders, Resolution.Minute)

        # ---------------------------------------------------------------------
        # Rolling Windows for Historical Price Access (Regime Engine needs 21+ days)
        # ---------------------------------------------------------------------
        lookback = (
            max(config.VOL_LOOKBACK, config.BREADTH_LOOKBACK, config.CREDIT_LOOKBACK) + 5
        )
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
                self.Log(f"SPLIT: {proxy} (proxy) - freezing all")
                return True

        # Check traded symbols - freeze only that symbol
        for symbol in self.traded_symbols:
            if data.Splits.ContainsKey(symbol):
                symbol_str = str(symbol)
                self.symbols_to_skip.add(symbol_str)
                # Register with risk engine for tracking
                self.risk_engine.register_split(symbol_str)

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

        Sets equity_prior_close baseline for kill switch calculation.
        Sets SPY prior close for gap filter.
        """
        # Skip during warmup
        if self.IsWarmingUp:
            return

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
                self.Log(
                    f"MOO_FALLBACK: Order {result.get('order_id')} fallback submitted"
                )
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

        # Check warm entry conditions
        signal = self.cold_start_engine.check_warm_entry(
            regime_score=regime_score,
            has_leveraged_position=has_leveraged,
            kill_switch_triggered=self.scheduler.is_kill_switch_triggered(),
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

        # 4. Generate Hedge signals
        self._generate_hedge_signals(regime_state)

        # 5. Generate Yield signals
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
        if hasattr(self, '_eod_capital_state') and self._eod_capital_state is not None:
            self._process_eod_signals(self._eod_capital_state)
            self._eod_capital_state = None

        # Save all state
        self._save_state()

        # Skip daily summary to save log space - only fills are logged

        # Reset daily tracking
        self.today_trades.clear()
        self.today_safeguards.clear()
        self.symbols_to_skip.clear()

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

            self.Log(
                f"FILL: {direction} {abs(fill_qty)} {symbol} @ ${fill_price:.2f}"
            )

            # Track trade for daily summary
            trade_desc = f"{direction} {abs(fill_qty)} {symbol} @ ${fill_price:.2f}"
            self.today_trades.append(trade_desc)

            # Update position tracking
            self._on_fill(symbol, fill_price, fill_qty, orderEvent)

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

        Checks for:
            - Entry signals (BB compression breakout)
            - Exit signals (Chandelier stop, regime deterioration)

        Args:
            regime_state: Current regime state.
        """
        is_cold_start = self.cold_start_engine.is_active() if hasattr(self.cold_start_engine, 'is_active') else False
        has_warm_entry = self.cold_start_engine.has_warm_entry() if hasattr(self.cold_start_engine, 'has_warm_entry') else False
        current_date = str(self.Time.date())

        # Check QLD
        if self.qld_bb.IsReady and self.qld_atr.IsReady:
            qld_close = self.Securities[self.qld].Close
            qld_high = self.Securities[self.qld].High
            qld_upper = self.qld_bb.UpperBand.Current.Value
            qld_middle = self.qld_bb.MiddleBand.Current.Value
            qld_lower = self.qld_bb.LowerBand.Current.Value
            qld_bw = (qld_upper - qld_lower) / qld_middle if qld_middle > 0 else 0

            # DEBUG: Disabled to save log space
            # if self.Time.weekday() == 0:
            #     self.Log(f"TREND_DEBUG QLD: close={qld_close:.2f} upper={qld_upper:.2f} bw={qld_bw:.3f} regime={regime_state.smoothed_score:.1f}")

            # Check entry if not invested
            if not self.Portfolio[self.qld].Invested:
                signal = self.trend_engine.check_entry_signal(
                    symbol="QLD",
                    close=qld_close,
                    upper_band=self.qld_bb.UpperBand.Current.Value,
                    middle_band=self.qld_bb.MiddleBand.Current.Value,
                    lower_band=self.qld_bb.LowerBand.Current.Value,
                    regime_score=regime_state.smoothed_score,
                    is_cold_start_active=is_cold_start,
                    has_warm_entry=has_warm_entry,
                    atr=self.qld_atr.Current.Value,
                    current_date=current_date,
                )
                if signal:
                    self.portfolio_router.receive_signal(signal)
            else:
                # Check exit if invested
                signal = self.trend_engine.check_exit_signals(
                    symbol="QLD",
                    close=qld_close,
                    high=qld_high,
                    middle_band=self.qld_bb.MiddleBand.Current.Value,
                    regime_score=regime_state.smoothed_score,
                    atr=self.qld_atr.Current.Value,
                )
                if signal:
                    self.portfolio_router.receive_signal(signal)

        # Check SSO
        if self.sso_bb.IsReady and self.sso_atr.IsReady:
            sso_close = self.Securities[self.sso].Close
            sso_high = self.Securities[self.sso].High

            # Check entry if not invested
            if not self.Portfolio[self.sso].Invested:
                signal = self.trend_engine.check_entry_signal(
                    symbol="SSO",
                    close=sso_close,
                    upper_band=self.sso_bb.UpperBand.Current.Value,
                    middle_band=self.sso_bb.MiddleBand.Current.Value,
                    lower_band=self.sso_bb.LowerBand.Current.Value,
                    regime_score=regime_state.smoothed_score,
                    is_cold_start_active=is_cold_start,
                    has_warm_entry=has_warm_entry,
                    atr=self.sso_atr.Current.Value,
                    current_date=current_date,
                )
                if signal:
                    self.portfolio_router.receive_signal(signal)
            else:
                # Check exit if invested
                signal = self.trend_engine.check_exit_signals(
                    symbol="SSO",
                    close=sso_close,
                    high=sso_high,
                    middle_band=self.sso_bb.MiddleBand.Current.Value,
                    regime_score=regime_state.smoothed_score,
                    atr=self.sso_atr.Current.Value,
                )
                if signal:
                    self.portfolio_router.receive_signal(signal)

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
            self.Portfolio[sym].HoldingsValue
            for sym in self.traded_symbols
            if sym != self.shv
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

        Args:
            risk_result: Risk check result containing symbols to liquidate.
        """
        self.Log("KILL_SWITCH: Triggered")

        # Trigger in scheduler (disables all trading)
        self.scheduler.trigger_kill_switch()

        # Liquidate all positions
        for symbol in risk_result.symbols_to_liquidate:
            self.Liquidate(symbol)

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
        days_running = self.cold_start_engine.get_days_running() if hasattr(self.cold_start_engine, 'get_days_running') else 5
        gap_filter = getattr(self, '_gap_filter_active', False)
        vol_shock = getattr(self, '_vol_shock_active', False)
        time_guard = getattr(self, '_time_guard_active', False)

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
            tqqq_open = data.Bars[self.tqqq].Open if data.Bars.ContainsKey(self.tqqq) else tqqq_price
            tqqq_volume = float(data.Bars[self.tqqq].Volume) if data.Bars.ContainsKey(self.tqqq) else 0.0
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
            )
            if signal:
                self.portfolio_router.receive_signal(signal)

        # Check SOXL
        if self.soxl_rsi.IsReady and not self.Portfolio[self.soxl].Invested:
            soxl_price = self.Securities[self.soxl].Price
            soxl_open = data.Bars[self.soxl].Open if data.Bars.ContainsKey(self.soxl) else soxl_price
            soxl_volume = float(data.Bars[self.soxl].Volume) if data.Bars.ContainsKey(self.soxl) else 0.0
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

        Checks Chandelier trailing stops for QLD and SSO.

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
        Calculate current regime state.

        Uses end-of-day prices for daily calculation.

        Returns:
            RegimeState with scores and classification.
        """
        # Check if indicators are ready
        if not (
            self.spy_sma20.IsReady
            and self.spy_sma50.IsReady
            and self.spy_sma200.IsReady
        ):
            # Return neutral default state when indicators not ready
            return RegimeState(
                smoothed_score=50.0,
                raw_score=50.0,
                state=RegimeLevel.NEUTRAL,
                trend_score=50.0,
                volatility_score=50.0,
                breadth_score=50.0,
                credit_score=50.0,
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

        return self.regime_engine.calculate(
            spy_close=self.Securities[self.spy].Close,
            spy_sma20=self.spy_sma20.Current.Value,
            spy_sma50=self.spy_sma50.Current.Value,
            spy_sma200=self.spy_sma200.Current.Value,
            spy_prices=spy_prices,
            rsp_prices=rsp_prices,
            hyg_prices=hyg_prices,
            ief_prices=ief_prices,
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
        # Update trend engine if QLD/SSO
        if symbol in ["QLD", "SSO"]:
            if fill_qty > 0:
                # Get ATR for initial stop calculation
                atr_value = 0.0
                if symbol == "QLD" and self.qld_atr.IsReady:
                    atr_value = self.qld_atr.Current.Value
                elif symbol == "SSO" and self.sso_atr.IsReady:
                    atr_value = self.sso_atr.Current.Value
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
                    vwap = self.Securities[symbol].Price if hasattr(self, symbol.lower()) else fill_price
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
        Get current position values for all traded symbols.

        Returns:
            Dict of symbol -> current holdings value.
        """
        positions = {}
        for symbol in self.traded_symbols:
            positions[str(symbol.Value)] = self.Portfolio[symbol].HoldingsValue
        return positions

    def _get_current_prices(self) -> Dict[str, float]:
        """
        Get current prices for all traded symbols.

        Returns:
            Dict of symbol -> current price.
        """
        prices = {}
        for symbol in self.traded_symbols:
            prices[str(symbol.Value)] = self.Securities[symbol].Price
        return prices
