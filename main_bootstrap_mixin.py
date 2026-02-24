from __future__ import annotations

from AlgorithmImports import *

import config
from engines.core.capital_engine import CapitalEngine
from engines.core.cold_start_engine import ColdStartEngine
from engines.core.regime_engine import RegimeEngine
from engines.core.risk_engine import RiskEngine
from engines.core.startup_gate import StartupGate
from engines.core.trend_engine import TrendEngine
from engines.satellite.hedge_engine import HedgeEngine
from engines.satellite.mean_reversion_engine import MeanReversionEngine
from engines.satellite.options_engine import OptionsEngine
from execution.execution_engine import ExecutionEngine
from execution.oco_manager import OCOManager
from persistence.state_manager import StateManager
from portfolio.portfolio_router import PortfolioRouter
from scheduling.daily_scheduler import DailyScheduler
from utils.monthly_pnl_tracker import MonthlyPnLTracker


class MainBootstrapMixin:
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
