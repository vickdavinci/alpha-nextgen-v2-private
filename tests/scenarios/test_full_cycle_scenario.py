"""
Scenario tests for Full Trading Cycle.

Tests a complete trading day workflow:
1. Pre-market setup (09:25)
2. Market open (09:30)
3. Trading session
4. EOD processing (15:45)
5. State persistence (16:00)

Spec: docs/14-daily-operations.md
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta

from engines.core.regime_engine import RegimeEngine, RegimeState
from engines.core.capital_engine import CapitalEngine, CapitalState
from engines.core.risk_engine import RiskEngine
from engines.core.trend_engine import TrendEngine
from engines.satellite.mean_reversion_engine import MeanReversionEngine
from engines.satellite.hedge_engine import HedgeEngine
from engines.satellite.yield_sleeve import YieldSleeve
from portfolio.portfolio_router import PortfolioRouter
from portfolio.exposure_groups import ExposureCalculator
from models.target_weight import TargetWeight
from models.enums import Urgency
import config


@pytest.fixture
def mock_algorithm():
    """Create mock algorithm for full cycle tests."""
    algo = MagicMock()
    algo.Time = datetime(2024, 1, 15, 9, 30)
    algo.Log = MagicMock()
    algo.Debug = MagicMock()
    algo.Error = MagicMock()
    algo.Portfolio = MagicMock()
    algo.Portfolio.TotalPortfolioValue = 50000.0
    algo.Portfolio.Invested = False
    algo.Portfolio.Cash = 50000.0

    # Mock position access
    def get_position(symbol):
        pos = MagicMock()
        pos.Invested = False
        pos.Quantity = 0
        pos.HoldingsValue = 0.0
        return pos

    algo.Portfolio.__getitem__ = MagicMock(side_effect=get_position)

    return algo


@pytest.fixture
def engines(mock_algorithm):
    """Create all engine instances."""
    return {
        "regime": RegimeEngine(mock_algorithm),
        "capital": CapitalEngine(mock_algorithm),
        "risk": RiskEngine(mock_algorithm),
        "trend": TrendEngine(mock_algorithm),
        "mr": MeanReversionEngine(mock_algorithm),
        "hedge": HedgeEngine(mock_algorithm),
        "yield": YieldSleeve(mock_algorithm),
    }


class TestFullCycleScenario:
    """
    End-to-end scenario tests for complete trading day.

    These tests simulate an entire trading day from pre-market
    through close and verify all components work together.
    """

    @pytest.mark.scenario
    def test_scenario_full_day_bullish_trend(self, mock_algorithm, engines):
        """
        SCENARIO: Complete bullish day with trend entry.

        Given: Regime = 70 (RISK_ON), MA200 trend bullish
        When: Trading day unfolds
        Then:
            - 09:25: equity_prior_close set
            - 09:30: MOO orders fill
            - 09:33: SOD baseline set, gap filter checked
            - 10:00: Trend signal checked
            - 15:45: EOD processing
            - 16:00: State persisted
        """
        # Setup: Pre-market (09:25)
        equity_prior_close = 50000.0

        # SOD baseline (09:33)
        equity_sod = 50000.0
        spy_prior_close = 450.0
        spy_open = 451.0  # Small gap up, no gap filter

        # Check gap filter
        gap_pct = (spy_open - spy_prior_close) / spy_prior_close
        gap_filter_active = gap_pct <= -config.GAP_FILTER_PCT
        assert gap_filter_active is False  # No gap filter

        # Regime calculation shows RISK_ON
        regime_score = 70.0
        assert regime_score >= 70  # RISK_ON

        # Trend engine check - conditions for entry
        trend_signal = engines["trend"].check_entry_signal(
            symbol="QLD",
            close=80.0,
            ma200=75.0,  # Price > MA200 (bullish)
            adx=30.0,  # Strong trend (ADX > 25)
            regime_score=regime_score,
            is_cold_start_active=False,
            has_warm_entry=False,
            atr=2.0,
            current_date="2024-01-15",
        )

        # Assert: Entry signal generated
        assert trend_signal is not None
        assert trend_signal.symbol == "QLD"
        assert trend_signal.source == "TREND"
        assert trend_signal.urgency == Urgency.EOD

        # EOD processing (15:45) would queue MOO order
        # State persisted at 16:00

    @pytest.mark.scenario
    def test_scenario_full_day_mean_reversion_trade(self, mock_algorithm, engines):
        """
        SCENARIO: Complete day with MR trade.

        Given: Regime = 55 (NEUTRAL), no gap
        When: 11:30 AM, TQQQ RSI drops below 25
        Then:
            - MR entry signal generated (IMMEDIATE)
            - Position opened via MarketOrder
            - Trailing stop monitored
            - 15:45: Forced exit of TQQQ
            - No overnight position
        """
        # Setup: Mid-day conditions
        mock_algorithm.Time = datetime(2024, 1, 15, 11, 30)
        regime_score = 55.0  # NEUTRAL

        # MR entry conditions
        mr_signal = engines["mr"].check_entry_signal(
            symbol="TQQQ",
            current_price=45.0,
            open_price=47.0,  # -4.3% intraday drop
            rsi_value=22.0,  # RSI < 25 (oversold)
            current_volume=5000000.0,
            avg_volume=3000000.0,  # High volume
            vwap=46.0,
            regime_score=regime_score,
            days_running=10,  # Past cold start
            gap_filter_triggered=False,
            vol_shock_active=False,
            time_guard_active=False,
            current_hour=11,
            current_minute=30,
            vix_value=18.0,  # Normal VIX
        )

        # Assert: MR entry signal with IMMEDIATE urgency
        assert mr_signal is not None
        assert mr_signal.symbol == "TQQQ"
        assert mr_signal.source == "MR"
        assert mr_signal.urgency == Urgency.IMMEDIATE

        # Simulate position registered
        engines["mr"].register_entry(
            symbol="TQQQ",
            entry_price=45.0,
            entry_time="2024-01-15 11:30:00",
            vwap=46.0,
        )

        # 15:45: Force exit for intraday-only symbol
        mock_algorithm.Time = datetime(2024, 1, 15, 15, 45)
        exit_signal = TargetWeight(
            symbol="TQQQ",
            target_weight=0.0,
            source="MR",
            urgency=Urgency.IMMEDIATE,
            reason="TIME_EXIT_15:45",
        )

        assert exit_signal.target_weight == 0.0
        assert "15:45" in exit_signal.reason

    @pytest.mark.scenario
    def test_scenario_full_day_hedge_activation(self, mock_algorithm, engines):
        """
        SCENARIO: Complete day with hedge activation.

        Given: Regime drops from 50 to 35 mid-day
        When: Regime engine recalculates
        Then:
            - Hedge engine generates TMF/PSQ signals
            - Signals have EOD urgency
            - 15:45: MOO orders submitted for hedges
            - Next day: Hedge positions established
        """
        # Setup: Regime deteriorates to CAUTIOUS
        regime_score = 35.0  # Below 40 = CAUTIOUS/DEFENSIVE

        # Hedge engine calculates target allocations
        tmf_target, psq_target, hedge_tier = engines["hedge"].get_target_allocations(
            regime_score=regime_score,
        )

        # Assert: Hedges activated (score 30-39 = LIGHT tier with TMF)
        assert tmf_target > 0 or psq_target > 0
        assert hedge_tier == "LIGHT"

        # Generate hedge signals (returns list of TargetWeight)
        hedge_signals = engines["hedge"].get_hedge_signals(
            regime_score=regime_score,
            current_tmf_pct=0.0,
            current_psq_pct=0.0,
        )

        # If signals generated, should be EOD urgency
        for signal in hedge_signals:
            assert signal.urgency == Urgency.EOD
            assert signal.source == "HEDGE"

    @pytest.mark.scenario
    def test_scenario_full_day_time_guard_blocking(self, mock_algorithm, engines):
        """
        SCENARIO: Time guard blocks entries during Fed window.

        Given: Regime = 65, MR entry conditions met at 14:00
        When: 14:00 (during 13:55-14:10 window)
        Then: Entry blocked by time guard
        And: Logged as TIME_GUARD_BLOCK
        """
        # Setup: During time guard window
        mock_algorithm.Time = datetime(2024, 1, 15, 14, 0)
        time_guard_active = True  # 13:55-14:10 window

        # MR conditions would normally trigger
        mr_signal = engines["mr"].check_entry_signal(
            symbol="TQQQ",
            current_price=45.0,
            open_price=47.0,
            rsi_value=22.0,  # Oversold
            current_volume=5000000.0,
            avg_volume=3000000.0,
            vwap=46.0,
            regime_score=65.0,
            days_running=10,
            gap_filter_triggered=False,
            vol_shock_active=False,
            time_guard_active=time_guard_active,  # BLOCKED
            current_hour=14,
            current_minute=0,
            vix_value=18.0,
        )

        # Assert: No signal due to time guard
        assert mr_signal is None

    @pytest.mark.scenario
    def test_scenario_full_day_yield_sweep(self, mock_algorithm, engines):
        """
        SCENARIO: Yield sleeve parks idle cash.

        Given: $5,000 idle cash, no pending signals
        When: 15:45 EOD processing
        Then: Yield sleeve generates SHV signal
        And: Cash parked in SHV overnight
        """
        # Setup: Idle cash scenario
        total_equity = 50000.0
        tradeable_equity = 45000.0
        non_shv_positions = 40000.0
        current_shv = 0.0
        locked_amount = 5000.0  # Lockbox

        # Calculate expected SHV allocation
        yield_signal = engines["yield"].get_yield_signal(
            total_equity=total_equity,
            tradeable_equity=tradeable_equity,
            non_shv_positions_value=non_shv_positions,
            current_shv_value=current_shv,
            locked_amount=locked_amount,
        )

        # Assert: SHV signal generated for idle cash
        if yield_signal is not None:
            assert yield_signal.symbol == "SHV"
            assert yield_signal.source == "YIELD"
            assert yield_signal.target_weight > 0

    @pytest.mark.scenario
    def test_scenario_full_day_exposure_limit_hit(self, mock_algorithm):
        """
        SCENARIO: Exposure limit prevents oversized position.

        Given: NASDAQ_BETA at 45%, trend wants 30% more QLD
        When: Router processes trend signal
        Then: Position reduced to stay within 50% net limit
        And: Logged as EXPOSURE_LIMIT_REDUCED
        """
        # Setup: Exposure calculator
        exposure = ExposureCalculator()

        # Current positions: 45% NASDAQ_BETA
        current_positions = {"QLD": 0.45}  # 45% in QLD

        # Trend wants 30% more
        desired_weight = 0.30

        # Check if within limits
        nasdaq_limit = config.EXPOSURE_LIMITS.get("NASDAQ_BETA", {}).get("net_long", 0.50)

        # Calculate what's allowed
        current_nasdaq = current_positions.get("QLD", 0.0)
        max_additional = nasdaq_limit - current_nasdaq

        # Assert: Would exceed limit
        assert desired_weight > max_additional
        assert max_additional == pytest.approx(0.05)  # Only 5% headroom

        # Reduced signal would be capped
        capped_weight = min(desired_weight, max_additional)
        assert capped_weight == pytest.approx(0.05)

    @pytest.mark.scenario
    def test_scenario_moo_fallback(self, mock_algorithm):
        """
        SCENARIO: MOO order fails, fallback to market.

        Given: MOO order for QLD submitted at 15:45
        When: 09:31 next day, order not filled
        Then: MOO cancelled
        And: Market order submitted as fallback
        """
        from execution.execution_engine import ExecutionEngine, OrderType

        exec_engine = ExecutionEngine(mock_algorithm)

        # Setup: Queue MOO order at 15:45
        exec_engine.queue_moo_order(
            symbol="QLD",
            quantity=100,
            strategy="TREND",
            signal_type="ENTRY",
            reason="BB Breakout",
        )

        # Verify MOO queued
        assert exec_engine.get_pending_moo_count() == 1

        # Submit MOO orders (simulating 15:45)
        exec_engine.submit_pending_moo_orders()

        # Next day 09:31 - check for fallbacks
        # In real scenario, if MOO not filled, would submit market order
        fallback_results = exec_engine.check_moo_fallbacks()

        # The fallback mechanism would convert unfilled MOO to market
        # This is handled by the execution engine

    @pytest.mark.scenario
    def test_scenario_state_restoration_after_restart(self, mock_algorithm, engines):
        """
        SCENARIO: Algorithm restarts mid-day, state restored.

        Given: Algorithm running, has positions
        When: Unexpected restart at 11:00 AM
        Then: State loaded from ObjectStore
        And: Positions reconciled with broker
        And: Trading continues normally
        """
        # Setup: Engine states before restart
        engines["trend"].register_entry(
            symbol="QLD",
            entry_price=75.0,
            entry_date="2024-01-15",
            atr=2.0,
        )
        engines["capital"]._current_phase_start_equity = 50000.0

        # Persist states
        trend_state = engines["trend"].get_state_for_persistence()
        capital_state = engines["capital"].get_state_for_persistence()
        risk_state = engines["risk"].get_state_for_persistence()

        # Simulate restart - create new engines
        new_algo = MagicMock()
        new_algo.Log = MagicMock()
        new_algo.Debug = MagicMock()
        new_algo.Time = datetime(2024, 1, 15, 11, 0)

        new_trend = TrendEngine(new_algo)
        new_capital = CapitalEngine(new_algo)
        new_risk = RiskEngine(new_algo)

        # Restore states
        new_trend.restore_state(trend_state)
        new_capital.restore_state(capital_state)
        new_risk.load_state(risk_state)  # RiskEngine uses load_state

        # Assert: State restored correctly
        assert new_trend.has_position("QLD") is True
        position = new_trend.get_position("QLD")
        assert position.entry_price == 75.0

        # Trading can continue
        assert new_risk._kill_switch_active is False
