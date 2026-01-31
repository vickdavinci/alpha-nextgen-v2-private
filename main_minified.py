from typing import Any, Dict, List, Optional, Set, Tuple

from AlgorithmImports import *

import config
from engines.core.capital_engine import CapitalEngine, CapitalState
from engines.core.cold_start_engine import ColdStartEngine
from engines.core.regime_engine import RegimeEngine, RegimeState
from engines.core.risk_engine import GreeksSnapshot, RiskCheckResult, RiskEngine, SafeguardType
from engines.core.trend_engine import TrendEngine
from engines.satellite.hedge_engine import HedgeEngine
from engines.satellite.mean_reversion_engine import MeanReversionEngine
from engines.satellite.options_engine import OptionContract, OptionDirection, OptionsEngine
from engines.satellite.yield_sleeve import YieldSleeve
from execution.execution_engine import ExecutionEngine
from execution.oco_manager import OCOManager
from models.enums import Phase, RegimeLevel, Urgency
from models.target_weight import TargetWeight
from persistence.state_manager import StateManager
from portfolio.portfolio_router import PortfolioRouter
from scheduling.daily_scheduler import DailyScheduler


class AlphaNextGen(QCAlgorithm):
    """."""

    spy: Symbol
    rsp: Symbol
    hyg: Symbol
    ief: Symbol
    vix: Symbol

    tqqq: Symbol
    soxl: Symbol
    qld: Symbol
    sso: Symbol
    tna: Symbol
    fas: Symbol
    tmf: Symbol
    psq: Symbol
    shv: Symbol
    qqq: Symbol

    traded_symbols: List[Symbol]
    proxy_symbols: List[Symbol]

    regime_engine: RegimeEngine
    capital_engine: CapitalEngine
    risk_engine: RiskEngine
    cold_start_engine: ColdStartEngine
    trend_engine: TrendEngine
    mr_engine: MeanReversionEngine
    hedge_engine: HedgeEngine
    yield_sleeve: YieldSleeve
    options_engine: OptionsEngine
    oco_manager: OCOManager

    portfolio_router: PortfolioRouter
    execution_engine: ExecutionEngine
    state_manager: StateManager
    scheduler: DailyScheduler

    equity_prior_close: float
    equity_sod: float
    spy_prior_close: float
    spy_open: float

    today_trades: List[str]
    today_safeguards: List[str]
    symbols_to_skip: Set[str]

    def _log(self, message: str, trades_only: bool = False) -> None:
        """."""
        if trades_only or self.LiveMode:
            self._log(message)

    def Initialize(self) -> None:
        """."""
        self.SetStartDate(2024, 1, 1)
        self.SetEndDate(2024, 1, 31)
        self.SetCash(config.PHASE_SEED_MIN)

        self.SetTimeZone("America/New_York")

        self.SetBrokerageModel(BrokerageName.InteractiveBrokersBrokerage)

        self._log("INIT: Basic setup complete")

        self._add_securities()

        self._setup_indicators()

        self.SetWarmUp(timedelta(days=config.INDICATOR_WARMUP_DAYS))
        self._log(f"INIT: Warmup set to {config.INDICATOR_WARMUP_DAYS} days")

        self._initialize_engines()

        self._initialize_infrastructure()

        self._setup_schedules()

        self._load_state()

        self.equity_prior_close = 0.0
        self.equity_sod = 0.0
        self.spy_prior_close = 0.0
        self.spy_open = 0.0
        self.today_trades = []
        self.today_safeguards = []
        self.symbols_to_skip = set()
        self._splits_logged_today = set()
        self._greeks_breach_logged = False

        self._log(
            f"INIT: Complete | "
            f"Cash=${config.PHASE_SEED_MIN:,} | "
            f"Symbols={len(self.traded_symbols) + len(self.proxy_symbols)}"
        )

    def OnData(self, data: Slice) -> None:
        """."""
        if self._check_splits(data):
            return

        self._update_rolling_windows(data)

        if self.IsWarmingUp:
            return

        risk_result = self._run_risk_checks(data)

        if risk_result.reset_cold_start:
            self._handle_kill_switch(risk_result)
            return

        if SafeguardType.PANIC_MODE in risk_result.active_safeguards:
            self._handle_panic_mode(risk_result)

        current_hour = self.Time.hour
        mr_window_open = 10 <= current_hour < 15

        if mr_window_open and risk_result.can_enter_intraday:
            self._scan_mr_signals(data)

        if mr_window_open and risk_result.can_enter_intraday:
            self._scan_options_signals(data)

        self._check_mr_exits(data)

        self._monitor_trend_stops(data)

        self._monitor_risk_greeks(data)

        self._process_immediate_signals()

    def _add_securities(self) -> None:
        """."""
        self.tqqq = self.AddEquity("TQQQ", Resolution.Minute).Symbol
        self.soxl = self.AddEquity("SOXL", Resolution.Minute).Symbol
        self.qld = self.AddEquity("QLD", Resolution.Minute).Symbol
        self.sso = self.AddEquity("SSO", Resolution.Minute).Symbol

        self.tna = self.AddEquity("TNA", Resolution.Minute).Symbol
        self.fas = self.AddEquity("FAS", Resolution.Minute).Symbol

        self.tmf = self.AddEquity("TMF", Resolution.Minute).Symbol
        self.psq = self.AddEquity("PSQ", Resolution.Minute).Symbol

        self.shv = self.AddEquity("SHV", Resolution.Minute).Symbol

        self.qqq = self.AddEquity("QQQ", Resolution.Minute).Symbol

        qqq_option = self.AddOption("QQQ", Resolution.Minute)
        qqq_option.SetFilter(
            -5, 5, timedelta(days=config.OPTIONS_DTE_MIN), timedelta(days=config.OPTIONS_DTE_MAX)
        )
        self._qqq_option_symbol = qqq_option.Symbol
        self._qqq_options_validated = False
        self._qqq_options_validation_attempts = 0

        self.spy = self.AddEquity("SPY", Resolution.Minute).Symbol
        self.rsp = self.AddEquity("RSP", Resolution.Minute).Symbol
        self.hyg = self.AddEquity("HYG", Resolution.Minute).Symbol
        self.ief = self.AddEquity("IEF", Resolution.Minute).Symbol

        self.vix = self.AddData(CBOE, "VIX", Resolution.Daily).Symbol
        self._current_vix = 15.0
        self._vix_at_open = 15.0
        self._vix_5min_ago = 15.0
        self._last_vix_spike_log = None
        self._qqq_at_open = 0.0

        self.traded_symbols = [
            self.tqqq,
            self.soxl,
            self.qld,
            self.sso,
            self.tna,
            self.fas,
            self.tmf,
            self.psq,
            self.shv,
        ]
        self.proxy_symbols = [self.spy, self.rsp, self.hyg, self.ief]

        self.trend_symbols = [self.qld, self.sso, self.tna, self.fas]

        self._log(
            f"INIT: Added {len(self.traded_symbols)} traded symbols, "
            f"{len(self.proxy_symbols)} proxy symbols"
        )

    def _setup_indicators(self) -> None:
        """."""
        self.spy_sma20 = self.SMA(self.spy, config.SMA_FAST, Resolution.Daily)
        self.spy_sma50 = self.SMA(self.spy, config.SMA_MED, Resolution.Daily)
        self.spy_sma200 = self.SMA(self.spy, config.SMA_SLOW, Resolution.Daily)

        self.spy_atr = self.ATR(
            self.spy, config.ATR_PERIOD, MovingAverageType.Wilders, Resolution.Minute
        )

        self.qld_ma200 = self.SMA(self.qld, config.SMA_SLOW, Resolution.Daily)
        self.sso_ma200 = self.SMA(self.sso, config.SMA_SLOW, Resolution.Daily)

        self.qld_adx = self.ADX(self.qld, config.ADX_PERIOD, Resolution.Daily)
        self.sso_adx = self.ADX(self.sso, config.ADX_PERIOD, Resolution.Daily)

        self.qld_atr = self.ATR(
            self.qld, config.ATR_PERIOD, MovingAverageType.Wilders, Resolution.Daily
        )
        self.sso_atr = self.ATR(
            self.sso, config.ATR_PERIOD, MovingAverageType.Wilders, Resolution.Daily
        )

        self.tna_ma200 = self.SMA(self.tna, config.SMA_SLOW, Resolution.Daily)
        self.tna_adx = self.ADX(self.tna, config.ADX_PERIOD, Resolution.Daily)
        self.tna_atr = self.ATR(
            self.tna, config.ATR_PERIOD, MovingAverageType.Wilders, Resolution.Daily
        )

        self.fas_ma200 = self.SMA(self.fas, config.SMA_SLOW, Resolution.Daily)
        self.fas_adx = self.ADX(self.fas, config.ADX_PERIOD, Resolution.Daily)
        self.fas_atr = self.ATR(
            self.fas, config.ATR_PERIOD, MovingAverageType.Wilders, Resolution.Daily
        )

        self.tqqq_rsi = self.RSI(
            self.tqqq, config.RSI_PERIOD, MovingAverageType.Wilders, Resolution.Minute
        )
        self.soxl_rsi = self.RSI(
            self.soxl, config.RSI_PERIOD, MovingAverageType.Wilders, Resolution.Minute
        )

        self.qqq_adx = self.ADX(self.qqq, config.ADX_PERIOD, Resolution.Daily)
        self.qqq_sma200 = self.SMA(self.qqq, config.SMA_SLOW, Resolution.Daily)
        self.qqq_rsi = self.RSI(
            self.qqq, config.RSI_PERIOD, MovingAverageType.Wilders, Resolution.Daily
        )

        lookback = max(config.VOL_LOOKBACK, config.BREADTH_LOOKBACK, config.CREDIT_LOOKBACK) + 5
        self.spy_closes = RollingWindow[float](lookback)
        self.rsp_closes = RollingWindow[float](lookback)
        self.hyg_closes = RollingWindow[float](lookback)
        self.ief_closes = RollingWindow[float](lookback)

        self.tqqq_volumes = RollingWindow[float](20)
        self.soxl_volumes = RollingWindow[float](20)

        self._last_regime_score = 50.0

        self._eod_capital_state = None

        self._log(
            f"INIT: Indicators initialized | "
            f"Lookback={lookback} days | "
            f"Warmup={config.INDICATOR_WARMUP_DAYS} days"
        )

    def _initialize_engines(self) -> None:
        """."""
        self.regime_engine = RegimeEngine(self)
        self.capital_engine = CapitalEngine(self)
        self.risk_engine = RiskEngine(self)
        self.cold_start_engine = ColdStartEngine(self)

        self.trend_engine = TrendEngine(self)
        self.mr_engine = MeanReversionEngine(self)
        self.hedge_engine = HedgeEngine(self)
        self.yield_sleeve = YieldSleeve(self)

        self.options_engine = OptionsEngine(self)
        self.oco_manager = OCOManager(self)

        self._log("INIT: All engines initialized")

    def _initialize_infrastructure(self) -> None:
        """."""
        self.portfolio_router = PortfolioRouter(self)
        self.execution_engine = ExecutionEngine(self)
        self.state_manager = StateManager(self)
        self.scheduler = DailyScheduler(self)

        self._log("INIT: Infrastructure initialized")

    def _setup_schedules(self) -> None:
        """."""
        self.scheduler.register_events()

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

        self.Schedule.On(
            self.DateRules.EveryDay(),
            self.TimeRules.At(15, 30),
            self._on_intraday_options_force_close,
        )

        for hour in range(10, 15):
            for minute in [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55]:
                self.Schedule.On(
                    self.DateRules.EveryDay(),
                    self.TimeRules.At(hour, minute),
                    self._on_vix_spike_check,
                )

        for hour in range(10, 15):
            for minute in [0, 15, 30, 45]:
                self.Schedule.On(
                    self.DateRules.EveryDay(),
                    self.TimeRules.At(hour, minute),
                    self._on_micro_regime_update,
                )

        self._log("INIT: Scheduled events and callbacks registered")

    def _check_splits(self, data: Slice) -> bool:
        """."""
        for proxy in self.proxy_symbols:
            if data.Splits.ContainsKey(proxy):
                proxy_str = str(proxy)
                if proxy_str not in self._splits_logged_today:
                    self._log(f"SPLIT: {proxy_str} (proxy) - freezing all")
                    self._splits_logged_today.add(proxy_str)
                return True

        for symbol in self.traded_symbols:
            if data.Splits.ContainsKey(symbol):
                symbol_str = str(symbol)
                self.symbols_to_skip.add(symbol_str)
                self.risk_engine.register_split(symbol_str)
                if symbol_str not in self._splits_logged_today:
                    self._log(f"SPLIT: {symbol_str} - freezing symbol")
                    self._splits_logged_today.add(symbol_str)

        return False

    def _update_rolling_windows(self, data: Slice) -> None:
        """."""
        if data.Bars.ContainsKey(self.spy):
            self.spy_closes.Add(self.Securities[self.spy].Close)
        if data.Bars.ContainsKey(self.rsp):
            self.rsp_closes.Add(self.Securities[self.rsp].Close)
        if data.Bars.ContainsKey(self.hyg):
            self.hyg_closes.Add(self.Securities[self.hyg].Close)
        if data.Bars.ContainsKey(self.ief):
            self.ief_closes.Add(self.Securities[self.ief].Close)

        if data.ContainsKey(self.vix):
            vix_data = data[self.vix]
            if vix_data is not None:
                self._current_vix = float(vix_data.Close)

        if data.Bars.ContainsKey(self.tqqq):
            self.tqqq_volumes.Add(float(data.Bars[self.tqqq].Volume))
        if data.Bars.ContainsKey(self.soxl):
            self.soxl_volumes.Add(float(data.Bars[self.soxl].Volume))

    def _on_pre_market_setup(self) -> None:
        """."""
        if self.IsWarmingUp:
            return

        # Reset daily state (kill switch, panic mode, etc.) at start of new day
        self.risk_engine.reset_daily_state()

        self.equity_prior_close = self.Portfolio.TotalPortfolioValue
        self.risk_engine.set_equity_prior_close(self.equity_prior_close)

        self.spy_prior_close = self.Securities[self.spy].Close
        self.risk_engine.set_spy_prior_close(self.spy_prior_close)

    def _on_moo_fallback(self) -> None:
        """."""
        if self.IsWarmingUp:
            return

        results = self.execution_engine.check_moo_fallbacks()
        for result in results:
            if result.get("success"):
                self._log(f"MOO_FALLBACK: Order {result.get('order_id')} fallback submitted")
            else:
                self._log(
                    f"MOO_FALLBACK: Order {result.get('order_id')} fallback failed - "
                    f"{result.get('error')}"
                )

    def _on_sod_baseline(self) -> None:
        """."""
        if self.IsWarmingUp:
            return

        self.equity_sod = self.Portfolio.TotalPortfolioValue
        self.risk_engine.set_equity_sod(self.equity_sod)

        self.spy_open = self.Securities[self.spy].Open
        self.risk_engine.set_spy_open(self.spy_open)

        if self.spy_prior_close > 0:
            gap_activated = self.risk_engine.check_gap_filter(self.spy_open)
            if gap_activated:
                self.today_safeguards.append("GAP_FILTER")

        self._vix_at_open = self._current_vix
        self._qqq_at_open = self.Securities[self.qqq].Open
        self.options_engine.update_market_open_data(
            vix_open=self._vix_at_open,
            spy_open=self.spy_open,
            spy_prior_close=self.spy_prior_close,
        )

        self._reconcile_positions()

    def _on_warm_entry_check(self) -> None:
        """."""
        if self.IsWarmingUp:
            return

        if not self.cold_start_engine.is_cold_start_active():
            return

        regime_score = self.regime_engine.get_previous_score()

        has_leveraged = self._has_leveraged_position()

        capital_state = self.capital_engine.calculate(self.Portfolio.TotalPortfolioValue)

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
        """."""
        if self.IsWarmingUp:
            return

    def _on_time_guard_end(self) -> None:
        """."""
        if self.IsWarmingUp:
            return

    def _on_mr_force_close(self) -> None:
        """."""
        if self.IsWarmingUp:
            return

        if self.Portfolio[self.tqqq].Invested:
            signal = TargetWeight(
                symbol="TQQQ",
                target_weight=0.0,
                source="MR",
                urgency=Urgency.IMMEDIATE,
                reason="TIME_EXIT_15:45",
            )
            self.portfolio_router.receive_signal(signal)

        if self.Portfolio[self.soxl].Invested:
            signal = TargetWeight(
                symbol="SOXL",
                target_weight=0.0,
                source="MR",
                urgency=Urgency.IMMEDIATE,
                reason="TIME_EXIT_15:45",
            )
            self.portfolio_router.receive_signal(signal)

        self._process_immediate_signals()

        if self.Portfolio[self.tqqq].Invested:
            self._log("MR_FAILSAFE: Force liquidating TQQQ via direct Liquidate()")
            self.Liquidate(self.tqqq, tag="MR_FAILSAFE_15:45")
        if self.Portfolio[self.soxl].Invested:
            self._log("MR_FAILSAFE: Force liquidating SOXL via direct Liquidate()")
            self.Liquidate(self.soxl, tag="MR_FAILSAFE_15:45")

    def _on_eod_processing(self) -> None:
        """."""
        if self.IsWarmingUp:
            return

        total_equity = self.Portfolio.TotalPortfolioValue

        regime_state = self._calculate_regime()
        self._last_regime_score = regime_state.smoothed_score

        capital_state = self.capital_engine.end_of_day_update(total_equity)

        self._generate_trend_signals_eod(regime_state)

        if regime_state.smoothed_score >= 40:
            self._generate_options_signals(regime_state, capital_state)

        self._generate_hedge_signals(regime_state)

        self._generate_yield_signals(capital_state)

        self._eod_capital_state = capital_state

        self.cold_start_engine.end_of_day_update(
            kill_switch_triggered=self.scheduler.is_kill_switch_triggered()
        )

    def _on_market_close(self) -> None:
        """."""
        if self.IsWarmingUp:
            return

        if hasattr(self, "_eod_capital_state") and self._eod_capital_state is not None:
            self._process_eod_signals(self._eod_capital_state)
            self._eod_capital_state = None

        self._save_state()

        self.today_trades.clear()
        self.today_safeguards.clear()
        self.symbols_to_skip.clear()
        self._splits_logged_today.clear()
        self._greeks_breach_logged = False

    def _on_weekly_reset(self) -> None:
        """."""
        if self.IsWarmingUp:
            return

        equity = self.Portfolio.TotalPortfolioValue
        self.risk_engine.set_week_start_equity(equity)

    def _on_intraday_options_force_close(self) -> None:
        """."""
        if self.IsWarmingUp:
            return

        if self.options_engine._intraday_position is not None:
            intraday_pos = self.options_engine._intraday_position
            symbol = intraday_pos.contract.symbol

            current_price = intraday_pos.entry_price

            signal = self.options_engine.check_intraday_force_exit(
                current_hour=self.Time.hour,
                current_minute=self.Time.minute,
                current_price=current_price,
            )

            if signal:
                self.portfolio_router.receive_signal(signal)
                self._process_immediate_signals()

    def _on_vix_spike_check(self) -> None:
        """."""
        if self.IsWarmingUp:
            return

        spike_alert = self.options_engine._micro_regime_engine.check_spike_alert(
            vix_current=self._current_vix,
            vix_5min_ago=self._vix_5min_ago,
            current_time=str(self.Time),
        )

        if spike_alert:
            vix_move = abs(self._current_vix - self._vix_5min_ago)
            should_log = (
                not hasattr(self, "_last_vix_spike_log")
                or self._last_vix_spike_log is None
                or (self.Time - self._last_vix_spike_log).total_seconds() / 60
                > config.LOG_THROTTLE_MINUTES
                or vix_move >= config.LOG_VIX_SPIKE_MIN_MOVE
            )
            if should_log:
                self._log(f"VIX_SPIKE: {self._vix_5min_ago:.1f} -> {self._current_vix:.1f}")
                self._last_vix_spike_log = self.Time

        self._vix_5min_ago = self._current_vix

    def _on_micro_regime_update(self) -> None:
        """."""
        if self.IsWarmingUp:
            return

        qqq_current = self.Securities[self.qqq].Price

        state = self.options_engine._micro_regime_engine.update(
            vix_current=self._current_vix,
            vix_open=self._vix_at_open,
            qqq_current=qqq_current,
            qqq_open=self._qqq_at_open,
            current_time=str(self.Time),
        )

    def OnOrderEvent(self, orderEvent: OrderEvent) -> None:
        """."""
        if orderEvent.Status == OrderStatus.Filled:
            symbol = str(orderEvent.Symbol)
            fill_price = orderEvent.FillPrice
            fill_qty = orderEvent.FillQuantity
            direction = "BUY" if fill_qty > 0 else "SELL"

            self._log(
                f"FILL: {direction} {abs(fill_qty)} {symbol} @ ${fill_price:.2f}", trades_only=True
            )

            trade_desc = f"{direction} {abs(fill_qty)} {symbol} @ ${fill_price:.2f}"
            self.today_trades.append(trade_desc)

            self._on_fill(symbol, fill_price, fill_qty, orderEvent)

            if self.oco_manager.has_order(orderEvent.OrderId):
                self.oco_manager.on_order_fill(
                    broker_order_id=orderEvent.OrderId,
                    fill_price=fill_price,
                    fill_quantity=fill_qty,
                    fill_time=str(self.Time),
                )

            self.execution_engine.on_order_event(
                broker_order_id=orderEvent.OrderId,
                status="Filled",
                fill_price=fill_price,
                fill_quantity=fill_qty,
            )

        elif orderEvent.Status == OrderStatus.Invalid:
            self._log(f"INVALID: {orderEvent.Symbol} - {orderEvent.Message}")
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

    def _load_state(self) -> None:
        """."""
        try:
            self.state_manager.load_all()
        except Exception as e:
            self._log(f"STATE_ERROR: Failed to load state - {e}")

    def _save_state(self) -> None:
        """."""
        try:
            self.state_manager.save_all(
                capital_engine=self.capital_engine,
                cold_start_engine=self.cold_start_engine,
                risk_engine=self.risk_engine,
            )

            if hasattr(self, "options_engine"):
                opt_state = self.options_engine.get_state_for_persistence()
                self.ObjectStore.Save("options_engine_state", str(opt_state))

            if hasattr(self, "oco_manager"):
                oco_state = self.oco_manager.get_state_for_persistence()
                self.ObjectStore.Save("oco_manager_state", str(oco_state))

        except Exception as e:
            self._log(f"STATE_ERROR: Failed to save state - {e}")

    def _reconcile_positions(self) -> None:
        """."""
        pass

    def _process_immediate_signals(self) -> None:
        """."""
        if self.portfolio_router.get_pending_count() == 0:
            return

        capital_state = self.capital_engine.calculate(self.Portfolio.TotalPortfolioValue)
        current_positions = self._get_current_positions()
        current_prices = self._get_current_prices()

        try:
            max_single_position = capital_state.tradeable_eq * capital_state.max_single_position_pct
            self.portfolio_router.process_immediate(
                tradeable_equity=capital_state.tradeable_eq,
                current_positions=current_positions,
                current_prices=current_prices,
                max_single_position=max_single_position,
            )
        except Exception as e:
            self._log(f"SIGNAL_ERROR: Failed to process immediate signals - {e}")

    def _process_eod_signals(self, capital_state: CapitalState) -> None:
        """."""
        if self.portfolio_router.get_pending_count() == 0:
            return

        try:
            weights = self.portfolio_router._pending_weights.copy()
            self.portfolio_router._pending_weights.clear()

            if not weights:
                return

            aggregated = self.portfolio_router.aggregate_weights(weights)

            max_single_position_pct = capital_state.max_single_position_pct
            for symbol, agg in aggregated.items():
                if agg.target_weight > max_single_position_pct:
                    agg.target_weight = max_single_position_pct

            total_non_shv_allocation = sum(
                agg.target_weight for sym, agg in aggregated.items() if sym != "SHV"
            )

            shv_target_weight = max(0.0, 1.0 - total_non_shv_allocation)

            if "SHV" in aggregated:
                shv_target_weight = min(shv_target_weight, aggregated["SHV"].target_weight)

            targets = []
            for symbol, agg in aggregated.items():
                if symbol == "SHV":
                    continue
                symbol_obj = None
                for s in self.traded_symbols:
                    if str(s.Value) == symbol:
                        symbol_obj = s
                        break

                if symbol_obj and agg.target_weight >= 0:
                    targets.append(PortfolioTarget(symbol_obj, agg.target_weight))

            targets.append(PortfolioTarget(self.shv, shv_target_weight))

            if targets:
                if len(targets) <= 5:
                    self._log(f"EOD_REBALANCE: {len(targets)} targets")

                self.SetHoldings(targets)

        except Exception as e:
            self._log(f"SIGNAL_ERROR: Failed to process EOD signals - {e}")

    def _generate_trend_signals_eod(self, regime_state: RegimeState) -> None:
        """V2.3: Position limits to reserve capital for options."""
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
        trend_symbols = ["QLD", "SSO", "TNA", "FAS"]
        current_trend_positions = sum(
            1 for sym in trend_symbols if self.Portfolio[getattr(self, sym.lower())].Invested
        )
        max_positions = 2
        entries_allowed = max_positions - current_trend_positions
        if entries_allowed < len(trend_symbols):
            self.Log(
                f"TREND: Position limit | Current={current_trend_positions} | Max={max_positions} | Entries allowed={entries_allowed}"
            )
        entry_candidates = []
        symbol_data = {
            "QLD": (self.qld, self.qld_ma200, self.qld_adx, self.qld_atr),
            "SSO": (self.sso, self.sso_ma200, self.sso_adx, self.sso_atr),
            "TNA": (self.tna, self.tna_ma200, self.tna_adx, self.tna_atr),
            "FAS": (self.fas, self.fas_ma200, self.fas_adx, self.fas_atr),
        }
        for symbol in trend_symbols:
            security, ma200_ind, adx_ind, atr_ind = symbol_data[symbol]
            if not (ma200_ind.IsReady and adx_ind.IsReady and atr_ind.IsReady):
                continue
            close = self.Securities[security].Close
            high = self.Securities[security].High
            ma200 = ma200_ind.Current.Value
            adx = adx_ind.Current.Value
            atr = atr_ind.Current.Value
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
                    entry_candidates.append((signal, adx))
        if entry_candidates and entries_allowed > 0:
            entry_candidates.sort(key=lambda x: x[1], reverse=True)
            for signal, adx in entry_candidates[:entries_allowed]:
                self.Log(
                    f"TREND: ENTRY_APPROVED {signal.symbol} | ADX={adx:.1f} | Slot {current_trend_positions + 1}/{max_positions}"
                )
                self.portfolio_router.receive_signal(signal)
                current_trend_positions += 1
            for signal, adx in entry_candidates[entries_allowed:]:
                self.Log(
                    f"TREND: ENTRY_BLOCKED {signal.symbol} | ADX={adx:.1f} | Position limit ({max_positions}) reached"
                )

    def _generate_options_signals(
        self, regime_state: RegimeState, capital_state: CapitalState
    ) -> None:
        """."""
        if not self.qqq_adx.IsReady or not self.qqq_sma200.IsReady:
            return

        if self.options_engine.has_position():
            return

        if not self._validate_options_symbol():
            return

        if self.CurrentSlice is None:
            return
        chain = (
            self.CurrentSlice.OptionChains[self._qqq_option_symbol]
            if self._qqq_option_symbol in self.CurrentSlice.OptionChains
            else None
        )
        if chain is None:
            return

        try:
            chain_list = list(chain)
            if not chain_list:
                return
        except Exception as e:
            self._log(f"OPTIONS_CHAIN_ERROR: Failed to iterate chain: {e}")
            return

        best_contract = self._select_best_option_contract(chain)
        if best_contract is None:
            return

        if best_contract.bid <= 0 or best_contract.ask <= 0:
            return

        qqq_price = self.Securities[self.qqq].Price
        adx_value = self.qqq_adx.Current.Value
        ma200_value = self.qqq_sma200.Current.Value

        iv_rank = self._calculate_iv_rank(chain)

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
            gap_filter_triggered=self.risk_engine.is_gap_filter_active(),
            vol_shock_active=self.risk_engine.is_vol_shock_active(self.Time),
            time_guard_active=self.scheduler.is_time_guard_active(),
        )

        if signal:
            self.portfolio_router.receive_signal(signal)

    def _get_contract_prices(self, contract) -> Tuple[float, float]:
        """."""
        bid = getattr(contract, "BidPrice", 0) or 0
        ask = getattr(contract, "AskPrice", 0) or 0
        if bid <= 0 or ask <= 0:
            last = getattr(contract, "LastPrice", 0) or 0
            if last > 0:
                bid = last * 0.995
                ask = last * 1.005
        return (bid, ask)

    def _select_best_option_contract(self, chain) -> Optional[OptionContract]:
        """."""
        if chain is None:
            return None

        qqq_price = self.Securities[self.qqq].Price

        candidates = []
        for contract in chain:
            if contract.Right != OptionRight.Call:
                continue

            dte = (contract.Expiry - self.Time).days
            if dte < config.OPTIONS_DTE_MIN or dte > config.OPTIONS_DTE_MAX:
                continue

            strike_diff = abs(contract.Strike - qqq_price)
            if strike_diff > qqq_price * 0.02:
                continue

            if contract.OpenInterest < config.OPTIONS_MIN_OPEN_INTEREST:
                continue

            bid, ask = self._get_contract_prices(contract)
            if bid <= 0 or ask <= 0:
                continue

            mid_price = (bid + ask) / 2
            spread_pct = (ask - bid) / mid_price if mid_price > 0 else 1.0

            if spread_pct > config.OPTIONS_SPREAD_WARNING_PCT:
                continue

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

            score = (1.0 / (1.0 + strike_diff)) * (1.0 - spread_pct)
            candidates.append((score, opt_contract))

        if not candidates:
            return None

        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

    def _select_swing_option_contract(
        self, chain, direction: OptionDirection = None
    ) -> Optional[OptionContract]:
        """V2.3: Select SWING contract (5-45 DTE) based on direction."""
        if chain is None:
            return None
        if direction is None:
            direction = OptionDirection.CALL
        target_right = OptionRight.Call if direction == OptionDirection.CALL else OptionRight.Put
        qqq_price = self.Securities[self.qqq].Price
        candidates = []
        for contract in chain:
            if contract.Right != target_right:
                continue
            dte = (contract.Expiry - self.Time).days
            if dte < config.OPTIONS_SWING_DTE_MIN or dte > config.OPTIONS_SWING_DTE_MAX:
                continue
            strike_diff = abs(contract.Strike - qqq_price)
            if strike_diff > qqq_price * 0.02:
                continue
            if contract.OpenInterest < config.OPTIONS_MIN_OPEN_INTEREST:
                continue
            bid, ask = self._get_contract_prices(contract)
            if bid <= 0 or ask <= 0:
                continue
            mid_price = (bid + ask) / 2
            spread_pct = (ask - bid) / mid_price if mid_price > 0 else 1.0
            if spread_pct > config.OPTIONS_SPREAD_WARNING_PCT:
                continue
            opt_contract = OptionContract(
                symbol=str(contract.Symbol),
                underlying="QQQ",
                direction=direction,
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
            score = (1.0 / (1.0 + strike_diff)) * (1.0 - spread_pct)
            candidates.append((score, opt_contract))
        if not candidates:
            self._log(f"SWING: No {direction.value} contracts found")
            return None
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

    def _select_intraday_option_contract(self, chain) -> Optional[OptionContract]:
        """."""
        if chain is None:
            return None

        qqq_price = self.Securities[self.qqq].Price

        candidates = []
        for contract in chain:
            dte = (contract.Expiry - self.Time).days
            if dte < 0 or dte > 2:
                continue

            strike_diff = abs(contract.Strike - qqq_price)
            if strike_diff > qqq_price * 0.02:
                continue

            if contract.OpenInterest < config.OPTIONS_MIN_OPEN_INTEREST:
                continue

            bid, ask = self._get_contract_prices(contract)
            if bid <= 0 or ask <= 0:
                continue

            mid_price = (bid + ask) / 2
            spread_pct = (ask - bid) / mid_price if mid_price > 0 else 1.0

            if spread_pct > config.OPTIONS_SPREAD_WARNING_PCT:
                continue

            direction = (
                OptionDirection.CALL if contract.Right == OptionRight.Call else OptionDirection.PUT
            )

            opt_contract = OptionContract(
                symbol=str(contract.Symbol),
                underlying="QQQ",
                direction=direction,
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

            dte_score = 1.0 / (1.0 + dte)
            atm_score = 1.0 / (1.0 + strike_diff)
            spread_score = 1.0 - spread_pct
            score = dte_score * atm_score * spread_score
            candidates.append((score, opt_contract))

        if not candidates:
            return None

        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

    def _generate_hedge_signals(self, regime_state: RegimeState) -> None:
        """."""
        total_equity = self.Portfolio.TotalPortfolioValue
        if total_equity <= 0:
            return

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
        """."""
        total_equity = self.Portfolio.TotalPortfolioValue
        shv_value = self.Portfolio[self.shv].HoldingsValue

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

    def _run_risk_checks(self, data: Slice) -> RiskCheckResult:
        """."""
        current_equity = self.Portfolio.TotalPortfolioValue
        spy_price = self.Securities[self.spy].Price

        spy_bar_range = 0.0
        if data.Bars.ContainsKey(self.spy):
            bar = data.Bars[self.spy]
            spy_bar_range = bar.High - bar.Low

        if self.spy_atr.IsReady:
            self.risk_engine.set_spy_atr(self.spy_atr.Current.Value)

        result = self.risk_engine.check_all(
            current_equity=current_equity,
            spy_price=spy_price,
            spy_bar_range=spy_bar_range,
            current_time=self.Time,
        )

        for safeguard in result.active_safeguards:
            safeguard_name = safeguard.value
            if safeguard_name not in self.today_safeguards:
                self.today_safeguards.append(safeguard_name)

        return result

    def _handle_kill_switch(self, risk_result: RiskCheckResult) -> None:
        """."""
        self._log("KILL_SWITCH: Triggered")

        self.scheduler.trigger_kill_switch()

        for symbol in risk_result.symbols_to_liquidate:
            self.Liquidate(symbol)

        self.cold_start_engine.reset()

    def _handle_panic_mode(self, risk_result: RiskCheckResult) -> None:
        """."""
        self._log("PANIC_MODE: Triggered")

        self.scheduler.trigger_panic_mode()

        for symbol in risk_result.symbols_to_liquidate:
            self.Liquidate(symbol)

    def _scan_mr_signals(self, data: Slice) -> None:
        """."""
        regime_score = self._last_regime_score
        days_running = (
            self.cold_start_engine.get_days_running()
            if hasattr(self.cold_start_engine, "get_days_running")
            else 5
        )
        gap_filter = getattr(self, "_gap_filter_active", False)
        vol_shock = getattr(self, "_vol_shock_active", False)
        time_guard = getattr(self, "_time_guard_active", False)

        tqqq_avg_vol = self._get_average_volume(self.tqqq_volumes)
        soxl_avg_vol = self._get_average_volume(self.soxl_volumes)

        if self.tqqq_rsi.IsReady and not self.Portfolio[self.tqqq].Invested:
            tqqq_price = self.Securities[self.tqqq].Price
            tqqq_open = (
                data.Bars[self.tqqq].Open if data.Bars.ContainsKey(self.tqqq) else tqqq_price
            )
            tqqq_volume = (
                float(data.Bars[self.tqqq].Volume) if data.Bars.ContainsKey(self.tqqq) else 0.0
            )
            tqqq_vwap = (tqqq_open + tqqq_price) / 2.0

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
                vix_value=self._current_vix,
            )
            if signal:
                self.portfolio_router.receive_signal(signal)

        if self.soxl_rsi.IsReady and not self.Portfolio[self.soxl].Invested:
            soxl_price = self.Securities[self.soxl].Price
            soxl_open = (
                data.Bars[self.soxl].Open if data.Bars.ContainsKey(self.soxl) else soxl_price
            )
            soxl_volume = (
                float(data.Bars[self.soxl].Volume) if data.Bars.ContainsKey(self.soxl) else 0.0
            )
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
                vix_value=self._current_vix,
            )
            if signal:
                self.portfolio_router.receive_signal(signal)

    def _check_mr_exits(self, data: Slice) -> None:
        """."""
        mr_pos = self.mr_engine.get_position_symbol()
        broker_tqqq = self.Portfolio[self.tqqq].Invested
        broker_soxl = self.Portfolio[self.soxl].Invested

        if broker_tqqq and mr_pos is None:
            self.Liquidate(self.tqqq)
            return

        if broker_soxl and mr_pos is None:
            self.Liquidate(self.soxl)
            return

        if mr_pos is not None and not broker_tqqq and not broker_soxl:
            self.mr_engine.remove_position()
            return

        if self.Portfolio[self.tqqq].Invested:
            signal = self.mr_engine.check_exit_signals(
                current_price=self.Securities[self.tqqq].Price,
                current_hour=self.Time.hour,
                current_minute=self.Time.minute,
            )
            if signal:
                self.portfolio_router.receive_signal(signal)

        if self.Portfolio[self.soxl].Invested:
            signal = self.mr_engine.check_exit_signals(
                current_price=self.Securities[self.soxl].Price,
                current_hour=self.Time.hour,
                current_minute=self.Time.minute,
            )
            if signal:
                self.portfolio_router.receive_signal(signal)

    def _monitor_trend_stops(self, data: Slice) -> None:
        """."""
        if self.Portfolio[self.qld].Invested:
            signal = self.trend_engine.check_stop_hit(
                symbol="QLD",
                current_price=self.Securities[self.qld].Price,
            )
            if signal:
                self.portfolio_router.receive_signal(signal)
                self._process_immediate_signals()

        if self.Portfolio[self.sso].Invested:
            signal = self.trend_engine.check_stop_hit(
                symbol="SSO",
                current_price=self.Securities[self.sso].Price,
            )
            if signal:
                self.portfolio_router.receive_signal(signal)
                self._process_immediate_signals()

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

    def _scan_options_signals(self, data: Slice) -> None:
        """."""
        if not self.qqq_adx.IsReady or not self.qqq_sma200.IsReady:
            return

        if self.options_engine.has_position():
            return

        current_hour = self.Time.hour
        if current_hour < 10 or current_hour >= 15:
            return

        if not self._validate_options_symbol():
            return

        chain = (
            data.OptionChains[self._qqq_option_symbol]
            if self._qqq_option_symbol in data.OptionChains
            else None
        )
        if chain is None:
            return

        try:
            chain_list = list(chain)
            if not chain_list:
                return
        except Exception as e:
            self._log(f"OPTIONS_CHAIN_ERROR: Failed to iterate chain: {e}")
            return

        qqq_price = self.Securities[self.qqq].Price
        adx_value = self.qqq_adx.Current.Value
        ma200_value = self.qqq_sma200.Current.Value

        if self._qqq_at_open > 0:
            intraday_contract = self._select_intraday_option_contract(chain)
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
                    return

        # V2.3: Check for SWING mode entry (5-45 DTE) using Simple Intraday Filters
        # STEP 1: Determine direction based on Price vs MA200 + RSI (per spec)
        from engines.satellite.options_engine import OptionDirection

        rsi_value = self.qqq_rsi.Current.Value if self.qqq_rsi.IsReady else 50.0

        direction = None
        if qqq_price > ma200_value and rsi_value < 70:
            direction = OptionDirection.CALL
            self._log(
                f"SWING: Direction=CALL | QQQ={qqq_price:.2f} > MA200={ma200_value:.2f} | RSI={rsi_value:.1f}"
            )
        elif qqq_price < ma200_value and rsi_value > 30:
            direction = OptionDirection.PUT
            self._log(
                f"SWING: Direction=PUT | QQQ={qqq_price:.2f} < MA200={ma200_value:.2f} | RSI={rsi_value:.1f}"
            )
        else:
            return  # No clear signal

        # STEP 2: Select contract based on direction
        swing_contract = self._select_swing_option_contract(chain, direction)
        if swing_contract is None:
            return

        if swing_contract.bid <= 0 or swing_contract.ask <= 0:
            return

        # STEP 3: Calculate filter inputs for swing mode
        spy_price = self.Securities[self.spy].Price
        spy_gap_pct = 0.0
        spy_intraday_pct = 0.0
        vix_intraday_pct = 0.0

        if hasattr(self, "_spy_prior_close") and self._spy_prior_close > 0:
            spy_gap_pct = ((self.spy_open - self._spy_prior_close) / self._spy_prior_close) * 100

        if hasattr(self, "spy_open") and self.spy_open > 0:
            spy_intraday_pct = ((spy_price - self.spy_open) / self.spy_open) * 100

        if self._vix_at_open > 0:
            vix_intraday_pct = ((self._current_vix - self._vix_at_open) / self._vix_at_open) * 100

        # V2.3: Apply Simple Intraday Filters before swing entry
        can_enter, block_reason = self.options_engine.check_swing_filters(
            direction=direction,
            spy_gap_pct=spy_gap_pct,
            spy_intraday_change_pct=spy_intraday_pct,
            vix_intraday_change_pct=vix_intraday_pct,
            current_hour=self.Time.hour,
            current_minute=self.Time.minute,
        )

        if not can_enter:
            self._log(f"SWING: Entry blocked - {block_reason}")
            return

        iv_rank = self._calculate_iv_rank(chain)

        signal = self.options_engine.check_entry_signal(
            adx_value=adx_value,
            current_price=qqq_price,
            ma200_value=ma200_value,
            iv_rank=iv_rank,
            best_contract=swing_contract,
            current_hour=self.Time.hour,
            current_minute=self.Time.minute,
            current_date=str(self.Time.date()),
            portfolio_value=self.Portfolio.TotalPortfolioValue,
            gap_filter_triggered=self.risk_engine.is_gap_filter_active(),
            vol_shock_active=self.risk_engine.is_vol_shock_active(self.Time),
            time_guard_active=self.scheduler.is_time_guard_active(),
        )

        if signal:
            self.portfolio_router.receive_signal(signal)

    def _monitor_risk_greeks(self, data: Slice) -> None:
        """."""
        if not self.options_engine.has_position():
            return

        greeks = self._get_fresh_position_greeks()
        if greeks is None:
            greeks = self.options_engine.calculate_position_greeks()
            if greeks is None:
                return

        self.risk_engine.update_greeks(greeks)

        breach, reasons = self.options_engine.check_greeks_breach(self.risk_engine)
        if breach:
            if not self._greeks_breach_logged:
                self._log(f"GREEKS_BREACH: {', '.join(reasons)}")
                self._greeks_breach_logged = True
            signal = TargetWeight(
                symbol="QQQ_OPT",
                target_weight=0.0,
                source="RISK",
                urgency=Urgency.IMMEDIATE,
                reason=f"GREEKS_BREACH: {', '.join(reasons)}",
            )
            self.portfolio_router.receive_signal(signal)

    def _get_fresh_position_greeks(self) -> Optional[GreeksSnapshot]:
        """."""
        position = self.options_engine.get_position()
        if position is None:
            return None

        position_symbol = position.contract.symbol

        if self.CurrentSlice is None:
            return None
        chain = (
            self.CurrentSlice.OptionChains[self._qqq_option_symbol]
            if self._qqq_option_symbol in self.CurrentSlice.OptionChains
            else None
        )
        if chain is None:
            return None

        try:
            for contract in chain:
                if str(contract.Symbol) == position_symbol:
                    delta = contract.Greeks.Delta if hasattr(contract, "Greeks") else None
                    gamma = contract.Greeks.Gamma if hasattr(contract, "Greeks") else None
                    vega = contract.Greeks.Vega if hasattr(contract, "Greeks") else None
                    theta = contract.Greeks.Theta if hasattr(contract, "Greeks") else None

                    if delta is not None:
                        self.options_engine.update_position_greeks(delta, gamma, vega, theta)

                        return GreeksSnapshot(
                            delta=delta,
                            gamma=gamma or 0.0,
                            vega=vega or 0.0,
                            theta=theta or 0.0,
                        )
                    break
        except Exception as e:
            self._log(f"GREEKS_REFRESH_ERROR: {e}")

        return None

    def _validate_options_symbol(self) -> bool:
        """."""
        if self._qqq_option_symbol is None:
            return False
        return True

    def _validate_options_symbol_UNUSED(self) -> bool:
        """."""
        if self._qqq_options_validated:
            return True

        if self._qqq_option_symbol is None:
            return False

        self._qqq_options_validation_attempts += 1

        try:
            if self.CurrentSlice is None:
                return False
            chain = (
                self.CurrentSlice.OptionChains[self._qqq_option_symbol]
                if self._qqq_option_symbol in self.CurrentSlice.OptionChains
                else None
            )
            if chain is None:
                if self._qqq_options_validation_attempts == 1:
                    self._log("OPTIONS_VALIDATION: Chain not available, will retry")
                return False

            chain_list = list(chain)
            if not chain_list:
                if self._qqq_options_validation_attempts == 1:
                    self._log("OPTIONS_VALIDATION: Chain empty, will retry")
                return False

            self._qqq_options_validated = True
            self._log(
                f"OPTIONS_VALIDATION: Symbol validated after "
                f"{self._qqq_options_validation_attempts} attempts, "
                f"{len(chain_list)} contracts available"
            )
            return True

        except Exception as e:
            if self._qqq_options_validation_attempts == 1:
                self._log(f"OPTIONS_VALIDATION: Error validating symbol: {e}")
            return False

    def _calculate_iv_rank(self, chain) -> float:
        """."""
        vix = self._current_vix
        vix_low = 12.0
        vix_high = 35.0

        if vix_high <= vix_low:
            return 50.0

        iv_rank = (vix - vix_low) / (vix_high - vix_low) * 100.0
        return max(0.0, min(100.0, iv_rank))

    def _has_leveraged_position(self) -> bool:
        """."""
        return (
            self.Portfolio[self.tqqq].Invested
            or self.Portfolio[self.soxl].Invested
            or self.Portfolio[self.qld].Invested
            or self.Portfolio[self.sso].Invested
        )

    def _calculate_regime(self) -> RegimeState:
        """V2.3: Includes VIX in regime calculation."""
        if not (self.spy_sma20.IsReady and self.spy_sma50.IsReady and self.spy_sma200.IsReady):
            return RegimeState(
                smoothed_score=50.0,
                raw_score=50.0,
                state=RegimeLevel.NEUTRAL,
                trend_score=50.0,
                vix_score=50.0,  # V2.3: VIX factor
                volatility_score=50.0,
                breadth_score=50.0,
                credit_score=50.0,
                vix_level=self._current_vix,  # V2.3: Raw VIX
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
        """."""
        ending_equity = self.Portfolio.TotalPortfolioValue
        regime_state = self._calculate_regime()
        capital_state = self.capital_engine.calculate(ending_equity)

        summary = self.scheduler.get_day_summary(
            starting_equity=self.equity_sod,
            ending_equity=ending_equity,
            trades=self.today_trades,
            safeguards=self.today_safeguards,
            moo_orders=[],
            regime_score=regime_state.smoothed_score,
            regime_state=regime_state.state.value,
            phase=capital_state.current_phase.value,
            days_running=self.cold_start_engine.get_days_running(),
        )

        self._log(summary)

    def _on_fill(
        self,
        symbol: str,
        fill_price: float,
        fill_qty: float,
        order_event: OrderEvent,
    ) -> None:
        """."""
        if symbol in ["QLD", "SSO", "TNA", "FAS"]:
            if fill_qty > 0:
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

        if symbol in ["TQQQ", "SOXL"]:
            try:
                if fill_qty > 0:
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
                self._log(f"MR_TRACK_ERROR: {symbol}: {e}")

        if "QQQ" in symbol and ("C" in symbol or "P" in symbol):
            try:
                if fill_qty > 0:
                    position = self.options_engine.register_entry(
                        fill_price=fill_price,
                        entry_time=str(self.Time),
                        current_date=str(self.Time.date()),
                    )

                    if position:
                        oco_pair = self.oco_manager.create_oco_pair(
                            symbol=symbol,
                            entry_price=fill_price,
                            stop_price=position.stop_price,
                            target_price=position.target_price,
                            quantity=int(fill_qty),
                            current_date=str(self.Time.date()),
                        )

                        if oco_pair:
                            self.oco_manager.submit_oco_pair(oco_pair, current_time=str(self.Time))
                            self._log(
                                f"OPT: OCO pair created | "
                                f"Stop=${position.stop_price:.2f} | "
                                f"Target=${position.target_price:.2f}"
                            )
                else:
                    self.options_engine.remove_position()
                    self._greeks_breach_logged = False
            except Exception as e:
                self._log(f"OPT_TRACK_ERROR: {symbol}: {e}")

    def _get_average_volume(self, volume_window: RollingWindow) -> float:
        """."""
        if not volume_window.IsReady:
            return 1.0

        total = sum(volume_window[i] for i in range(volume_window.Count))
        return total / volume_window.Count if volume_window.Count > 0 else 1.0

    def _get_volume_ratio(self, symbol: Symbol, data: Slice) -> float:
        """."""
        if not data.Bars.ContainsKey(symbol):
            return 1.0

        current_volume = data.Bars[symbol].Volume
        return 1.0

    def _get_current_positions(self) -> Dict[str, float]:
        """."""
        positions = {}
        for symbol in self.traded_symbols:
            positions[str(symbol.Value)] = self.Portfolio[symbol].HoldingsValue
        for kvp in self.Portfolio:
            symbol = kvp.Key
            holding = kvp.Value
            if holding.Invested and symbol.SecurityType == SecurityType.Option:
                positions[str(symbol.Value)] = holding.HoldingsValue
        return positions

    def _get_current_prices(self) -> Dict[str, float]:
        """."""
        prices = {}
        for symbol in self.traded_symbols:
            prices[str(symbol.Value)] = self.Securities[symbol].Price
        for kvp in self.Securities:
            symbol = kvp.Key
            security = kvp.Value
            if symbol.SecurityType == SecurityType.Option and security.Price > 0:
                prices[str(symbol.Value)] = security.Price
        return prices
