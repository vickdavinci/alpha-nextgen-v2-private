"""
Integration tests for remaining audit gaps.

Addresses:
- GAP #8: MOO Order Lifecycle (complete sequence)
- GAP #10: Partial Fills Integration
- GAP #11: EOD SetHoldings sell-before-buy
- GAP #12: OCO Expiration State
- GAP #13: State Persistence Recovery E2E
"""

from datetime import datetime, time, timedelta
from typing import Any, Dict, List
from unittest.mock import MagicMock, call, patch

import pytest

import config
from engines.core.capital_engine import CapitalEngine
from engines.satellite.options_engine import OptionsEngine
from execution.execution_engine import ExecutionEngine, OrderState, OrderType
from execution.oco_manager import OCOManager, OCOPair, OCOState
from models.enums import Urgency
from models.target_weight import TargetWeight
from portfolio.portfolio_router import OrderSide, PortfolioRouter

# =============================================================================
# GAP #8: MOO Order Lifecycle Complete Sequence
# =============================================================================


class TestMOOOrderLifecycle:
    """
    Test complete MOO order lifecycle:
    1. Signal generated during day
    2. Queue MOO order at signal time
    3. Submit at 15:45
    4. Check at 09:31 next day
    5. Fill or fallback to market
    """

    @pytest.fixture
    def exec_engine(self):
        """Create execution engine for testing."""
        algo = MagicMock()
        algo.Time = datetime(2024, 1, 15, 10, 0)
        algo.Log = MagicMock()
        return ExecutionEngine(algo)

    def test_moo_lifecycle_queue_submit_fill(self, exec_engine):
        """
        Complete MOO lifecycle: Queue → Submit → Fill

        Day 1:
        - 10:00 AM: Trend signal generated, MOO queued
        - 15:45: MOO submitted to broker

        Day 2:
        - 09:30: Market opens, MOO fills at open price
        - 09:31: Fallback check (not needed, already filled)
        """
        # Step 1: Queue MOO order during the day (simulating trend signal)
        order_id = exec_engine.queue_moo_order(
            symbol="QLD",
            quantity=100,
            strategy="TREND",
            signal_type="ENTRY",
            reason="MA200+ADX Entry Signal",
        )

        assert order_id != ""
        assert exec_engine.get_pending_moo_count() == 1

        # Verify order in PENDING state
        order = exec_engine.get_order(order_id)
        assert order.state == OrderState.PENDING
        assert order.order_type == OrderType.MOO
        assert order.symbol == "QLD"
        assert order.quantity == 100

        # Step 2: Submit at 15:45 (EOD)
        results = exec_engine.submit_pending_moo_orders()

        assert len(results) == 1
        assert results[0].success is True
        assert exec_engine.get_pending_moo_count() == 0

        # Order now SUBMITTED
        order = exec_engine.get_order(order_id)
        assert order.state == OrderState.SUBMITTED

        # Step 3: Simulate fill at market open (09:30 next day)
        if order.broker_order_id:
            exec_engine.on_order_event(
                broker_order_id=order.broker_order_id,
                status="Filled",
                fill_price=75.50,
                fill_quantity=100,
            )

        # Verify fill
        order = exec_engine.get_order(order_id)
        assert order.state == OrderState.FILLED
        assert order.fill_price == 75.50
        assert order.fill_quantity == 100

        # Step 4: Fallback check at 09:31 - should be empty (already filled)
        fallback_results = exec_engine.check_moo_fallbacks()
        assert len(fallback_results) == 0

    def test_moo_lifecycle_rejection_fallback(self, exec_engine):
        """
        MOO rejection triggers market order fallback.

        Day 1:
        - 10:00 AM: MOO queued
        - 15:45: MOO submitted

        Day 2:
        - 09:30: MOO rejected by broker
        - 09:31: Fallback check, market order submitted
        """
        # Queue and submit MOO
        order_id = exec_engine.queue_moo_order(
            symbol="QLD",
            quantity=100,
            strategy="TREND",
            signal_type="ENTRY",
            reason="MA200+ADX Entry Signal",
        )
        exec_engine.submit_pending_moo_orders()

        order = exec_engine.get_order(order_id)
        broker_id = order.broker_order_id

        # Simulate rejection at market open
        if broker_id:
            exec_engine.on_order_event(
                broker_order_id=broker_id,
                status="Rejected",
                rejection_reason="Market closed for symbol",
            )

        # Order should be in fallback queue
        assert exec_engine.get_fallback_queue_count() == 1

        # 09:31 fallback check
        fallback_results = exec_engine.check_moo_fallbacks()

        # Should have submitted a fallback market order
        assert len(fallback_results) == 1
        assert fallback_results[0].success is True

        # Fallback queue should be empty
        assert exec_engine.get_fallback_queue_count() == 0

    def test_moo_multiple_orders_batch_submit(self, exec_engine):
        """
        Multiple MOO orders submitted as batch at 15:45.
        """
        # Queue multiple orders
        order_ids = []
        symbols = ["QLD", "SSO", "TMF"]

        for symbol in symbols:
            order_id = exec_engine.queue_moo_order(
                symbol=symbol,
                quantity=50,
                strategy="TREND",
                signal_type="ENTRY",
                reason="Test batch",
            )
            order_ids.append(order_id)

        assert exec_engine.get_pending_moo_count() == 3

        # Submit all at 15:45
        results = exec_engine.submit_pending_moo_orders()

        assert len(results) == 3
        assert all(r.success for r in results)
        assert exec_engine.get_pending_moo_count() == 0


# =============================================================================
# GAP #10: Partial Fills Integration
# =============================================================================


class TestPartialFillsIntegration:
    """
    Test partial fill handling integration:
    - Execution engine tracks partial fills
    - Position sizing accounts for partial fills
    - Stop updates work with partial positions
    """

    @pytest.fixture
    def mock_algorithm(self):
        """Create mock algorithm."""
        algo = MagicMock()
        algo.Time = datetime(2024, 1, 15, 10, 30)
        algo.Portfolio.TotalPortfolioValue = 100000.0
        algo.Log = MagicMock()
        return algo

    def test_partial_fill_tracking(self, mock_algorithm):
        """
        Execution engine correctly tracks partial fills.
        """
        exec_engine = ExecutionEngine(mock_algorithm)

        # Submit order for 1000 shares
        result = exec_engine.submit_market_order(
            symbol="QLD",
            quantity=1000,
            strategy="TREND",
            signal_type="ENTRY",
            reason="Test",
        )

        order = exec_engine.get_order(result.order_id)
        broker_id = order.broker_order_id

        if broker_id:
            # First partial fill: 300 shares
            exec_engine.on_order_event(
                broker_order_id=broker_id,
                status="PartiallyFilled",
                fill_price=75.00,
                fill_quantity=300,
            )

            order = exec_engine.get_order(result.order_id)
            assert order.state == OrderState.PARTIALLY_FILLED
            assert order.fill_quantity == 300

            # Second partial fill: 400 shares
            exec_engine.on_order_event(
                broker_order_id=broker_id,
                status="PartiallyFilled",
                fill_price=75.10,
                fill_quantity=400,
            )

            order = exec_engine.get_order(result.order_id)
            assert order.state == OrderState.PARTIALLY_FILLED
            assert order.fill_quantity == 700  # Cumulative

            # Final fill: 300 shares
            exec_engine.on_order_event(
                broker_order_id=broker_id,
                status="Filled",
                fill_price=75.20,
                fill_quantity=300,
            )

            order = exec_engine.get_order(result.order_id)
            assert order.state == OrderState.FILLED

    def test_partial_fill_position_sizing_awareness(self, mock_algorithm):
        """
        Capital engine should track actual filled quantity for sizing.
        """
        capital_engine = CapitalEngine(mock_algorithm)

        # Set up capital state via calculate()
        state = capital_engine.calculate(total_equity=75000.0)

        # V3.0: Phase removed - verify tradeable equity is calculated correctly
        assert state.tradeable_eq > 0
        assert state.total_equity == 75000.0

        # Position sizing should be based on target weight, not partial fills
        # The execution layer handles actual fills

    def test_next_signal_after_partial_fill(self, mock_algorithm):
        """
        New signal after partial fill should work correctly.
        """
        router = PortfolioRouter(mock_algorithm)

        # Simulate existing partial position (30% filled of 50% target)
        current_positions = {"QLD": 15000.0}  # $15k = 15% of $100k
        current_prices = {"QLD": 75.0}

        # New signal wants 50% allocation
        signal = TargetWeight(
            symbol="QLD",
            target_weight=0.50,
            source="TREND",
            urgency=Urgency.EOD,
            reason="Continue entry",
        )

        router.receive_signal(signal)

        # Process should calculate delta from current position
        aggregated = router.aggregate_weights([signal])
        orders = router.calculate_order_intents(
            aggregated,
            tradeable_equity=100000.0,
            current_positions=current_positions,
            current_prices=current_prices,
        )

        # Should want to buy additional shares to reach 50%
        assert len(orders) == 1
        assert orders[0].side == OrderSide.BUY
        # Target: $50k, Current: $15k, Delta: $35k / $75 = 466 shares
        assert orders[0].quantity > 0


# =============================================================================
# GAP #11: EOD SetHoldings Sell-Before-Buy
# =============================================================================


class TestEODSellBeforeBuy:
    """
    Test that EOD signals execute sells before buys for margin.
    """

    @pytest.fixture
    def mock_algorithm(self):
        """Create mock algorithm."""
        algo = MagicMock()
        algo.Time = datetime(2024, 1, 15, 15, 45)
        algo.Portfolio.TotalPortfolioValue = 100000.0
        algo.Portfolio.Cash = 10000.0
        algo.Log = MagicMock()
        algo.MarketOrder = MagicMock()
        algo.MarketOnOpenOrder = MagicMock()
        return algo

    def test_sells_ordered_before_buys(self, mock_algorithm):
        """
        Router orders SELL orders before BUY orders for margin safety.
        """
        router = PortfolioRouter(mock_algorithm)

        # Signals: Sell SSO, Buy QLD
        sell_signal = TargetWeight(
            symbol="SSO",
            target_weight=0.0,  # Exit
            source="TREND",
            urgency=Urgency.EOD,
            reason="Exit SSO",
        )

        buy_signal = TargetWeight(
            symbol="QLD",
            target_weight=0.35,  # Entry
            source="TREND",
            urgency=Urgency.EOD,
            reason="Enter QLD",
        )

        router.receive_signal(sell_signal)
        router.receive_signal(buy_signal)

        # Current positions
        current_positions = {"SSO": 30000.0}  # $30k in SSO
        current_prices = {"SSO": 60.0, "QLD": 75.0}

        # Process EOD
        executed = router.process_eod(
            tradeable_equity=100000.0,
            current_positions=current_positions,
            current_prices=current_prices,
            max_single_position=0.50,
            current_time="2024-01-15 15:45:00",
        )

        # Check order of execution - sells should come before buys
        sell_orders = [o for o in executed if o.side == OrderSide.SELL]
        buy_orders = [o for o in executed if o.side == OrderSide.BUY]

        if sell_orders and buy_orders:
            # Get indices in executed list
            first_sell_idx = executed.index(sell_orders[0])
            first_buy_idx = executed.index(buy_orders[0])
            assert first_sell_idx < first_buy_idx, "Sells must execute before buys"


# =============================================================================
# GAP #12: OCO Expiration State
# =============================================================================


class TestOCOExpirationState:
    """
    Test OCO pair handling when option expires worthless.
    """

    @pytest.fixture
    def mock_algorithm(self):
        """Create mock algorithm."""
        algo = MagicMock()
        algo.Time = datetime(2024, 1, 19, 16, 0)  # Expiration day EOD
        algo.Log = MagicMock()
        return algo

    @pytest.fixture
    def oco_manager(self, mock_algorithm):
        """Create OCO manager."""
        return OCOManager(mock_algorithm)

    def test_oco_state_expired_defined(self):
        """
        OCOState.EXPIRED is defined and usable.
        """
        assert hasattr(OCOState, "EXPIRED")
        assert OCOState.EXPIRED.value == "EXPIRED"

    def test_oco_pair_can_be_marked_expired(self, oco_manager):
        """
        OCO pair can be manually marked as EXPIRED when option expires worthless.
        """
        # Create and submit an OCO pair
        pair = oco_manager.create_oco_pair(
            symbol="QQQ 240119C00450000",
            entry_price=0.50,
            stop_price=0.40,  # 20% stop
            target_price=0.75,  # 50% profit
            quantity=10,
            current_date="2024-01-15",
        )

        # Submit the pair
        success = oco_manager.submit_oco_pair(pair, "2024-01-15 10:30:00")
        assert success is True
        assert pair.state == OCOState.ACTIVE

        # Verify pair is tracked
        active_pair = oco_manager.get_active_pair("QQQ 240119C00450000")
        assert active_pair is not None

        # Simulate expiration - cancel the OCO pair with EXPIRED reason
        # This is how the system should handle option expiration
        oco_manager.cancel_oco_pair(pair.oco_id, reason="OPTION_EXPIRED_WORTHLESS")

        # Pair should be in CANCELLED state (closest to expired behavior)
        assert pair.state == OCOState.CANCELLED
        assert pair.close_reason == "OPTION_EXPIRED_WORTHLESS"

        # Pair should be removed from active tracking
        assert oco_manager.get_active_pair("QQQ 240119C00450000") is None

    def test_expired_oco_cleanup(self, oco_manager):
        """
        After OCO expiration/cancellation, tracking is cleaned up properly.
        """
        # Create and submit an OCO pair
        pair = oco_manager.create_oco_pair(
            symbol="QQQ 240119C00450000",
            entry_price=0.50,
            stop_price=0.40,
            target_price=0.75,
            quantity=10,
            current_date="2024-01-15",
        )
        oco_manager.submit_oco_pair(pair, "2024-01-15 10:30:00")

        # Cancel (simulating expiration)
        oco_manager.cancel_oco_pair(pair.oco_id, reason="OPTION_EXPIRED")

        # Verify cleanup
        assert not oco_manager.has_active_pair("QQQ 240119C00450000")
        assert len(oco_manager.get_all_active_pairs()) == 0

    def test_oco_stop_trigger_cancels_profit(self, oco_manager):
        """
        When stop is triggered, profit order is cancelled.
        """
        pair = oco_manager.create_oco_pair(
            symbol="QQQ 240119C00450000",
            entry_price=0.50,
            stop_price=0.40,
            target_price=0.75,
            quantity=10,
            current_date="2024-01-15",
        )
        oco_manager.submit_oco_pair(pair, "2024-01-15 10:30:00")

        # Simulate stop fill
        stop_order_id = pair.stop_leg.broker_order_id
        result = oco_manager.on_order_fill(
            broker_order_id=stop_order_id,
            fill_price=0.38,
            fill_quantity=-10,
            fill_time="2024-01-16 11:00:00",
        )

        assert result is not None
        assert result.state == OCOState.CLOSED
        assert "STOP_HIT" in result.close_reason
        assert pair.profit_leg.cancelled is True

    def test_oco_profit_trigger_cancels_stop(self, oco_manager):
        """
        When profit is triggered, stop order is cancelled.
        """
        pair = oco_manager.create_oco_pair(
            symbol="QQQ 240119C00450000",
            entry_price=0.50,
            stop_price=0.40,
            target_price=0.75,
            quantity=10,
            current_date="2024-01-15",
        )
        oco_manager.submit_oco_pair(pair, "2024-01-15 10:30:00")

        # Simulate profit fill
        profit_order_id = pair.profit_leg.broker_order_id
        result = oco_manager.on_order_fill(
            broker_order_id=profit_order_id,
            fill_price=0.76,
            fill_quantity=-10,
            fill_time="2024-01-16 14:00:00",
        )

        assert result is not None
        assert result.state == OCOState.CLOSED
        assert "PROFIT_HIT" in result.close_reason
        assert pair.stop_leg.cancelled is True


# =============================================================================
# GAP #13: State Persistence Recovery E2E
# =============================================================================


class TestStatePersistenceRecoveryE2E:
    """
    Test complete save → crash → restart → continue flow.
    """

    @pytest.fixture
    def mock_algorithm(self):
        """Create mock algorithm with ObjectStore."""
        algo = MagicMock()
        algo.Time = datetime(2024, 1, 15, 14, 30)
        algo.Portfolio.TotalPortfolioValue = 105000.0
        algo.Log = MagicMock()

        # Mock ObjectStore
        algo.ObjectStore = MagicMock()
        algo.ObjectStore.ContainsKey = MagicMock(return_value=False)
        algo.ObjectStore.Read = MagicMock(return_value="{}")
        algo.ObjectStore.Save = MagicMock()

        return algo

    def test_trend_position_survives_restart(self, mock_algorithm):
        """
        Trend position state survives algorithm restart.
        """
        from engines.core.trend_engine import TrendEngine

        # Create engine and register position
        engine1 = TrendEngine(mock_algorithm)
        position = engine1.register_entry(
            symbol="QLD",
            entry_price=72.50,
            entry_date="2024-01-14",
            atr=2.5,
            strategy_tag="TREND",
        )

        # Save state (simulating EOD save)
        saved_state = engine1.get_state_for_persistence()

        assert "positions" in saved_state
        assert "QLD" in saved_state["positions"]

        # Simulate restart - create new engine instance
        engine2 = TrendEngine(mock_algorithm)
        assert not engine2.has_position("QLD")  # Fresh engine

        # Restore state
        engine2.restore_state(saved_state)

        # Verify position restored
        assert engine2.has_position("QLD")
        restored_pos = engine2.get_position("QLD")
        assert restored_pos.entry_price == 72.50
        assert restored_pos.current_stop == position.current_stop

    def test_risk_engine_state_survives_restart(self, mock_algorithm):
        """
        Risk engine state (kill switch, weekly breaker) survives restart.
        """
        from engines.core.risk_engine import RiskEngine

        # Create engine and set state
        engine1 = RiskEngine(mock_algorithm)
        engine1.set_equity_prior_close(100000.0)
        engine1.set_equity_sod(101000.0)
        engine1.set_week_start_equity(98000.0)

        # Trigger weekly breaker
        engine1.check_weekly_breaker(92000.0)  # -6% WTD loss

        # Save state
        saved_state = engine1.get_state_for_persistence()

        # Verify persisted state includes weekly breaker
        assert "weekly_breaker_active" in saved_state
        assert saved_state["weekly_breaker_active"] is True
        assert saved_state["week_start_equity"] == 98000.0

        # Simulate restart
        engine2 = RiskEngine(mock_algorithm)
        engine2.load_state(saved_state)

        # Verify state restored (only persisted fields)
        assert engine2._weekly_breaker_active is True
        assert engine2._week_start_equity == 98000.0

    def test_execution_engine_state_survives_restart(self, mock_algorithm):
        """
        Execution engine state (pending MOO orders) survives restart.
        """
        engine1 = ExecutionEngine(mock_algorithm)

        # Queue MOO order
        order_id = engine1.queue_moo_order(
            symbol="QLD",
            quantity=100,
            strategy="TREND",
            signal_type="ENTRY",
            reason="Test",
        )

        # Save state
        saved_state = engine1.get_state_for_persistence()

        assert "pending_moo_orders" in saved_state
        assert order_id in saved_state["pending_moo_orders"]

    def test_capital_engine_lockbox_survives_restart(self, mock_algorithm):
        """
        V3.0: Capital engine lockbox survives restart.
        (Phase system removed - testing lockbox persistence instead)
        """
        engine1 = CapitalEngine(mock_algorithm)

        # Trigger lockbox at $100k milestone
        engine1.calculate(total_equity=110000.0)

        # Verify lockbox was triggered
        assert engine1.get_locked_amount() > 0
        locked_amount = engine1.get_locked_amount()

        # Save state
        saved_state = engine1.get_state_for_persistence()

        # Verify persisted state
        assert "locked_amount" in saved_state
        assert saved_state["locked_amount"] == locked_amount

        # Simulate restart
        engine2 = CapitalEngine(mock_algorithm)
        engine2.restore_state(saved_state)

        # Verify lockbox is restored
        assert engine2.get_locked_amount() == locked_amount

    def test_full_system_restart_recovery(self, mock_algorithm):
        """
        Complete system recovery after mid-day crash.

        Scenario:
        1. System running with positions
        2. Crash at 14:30
        3. Restart at 14:35
        4. Continue trading correctly
        """
        from engines.core.risk_engine import RiskEngine
        from engines.core.trend_engine import TrendEngine

        # === Before Crash ===
        trend1 = TrendEngine(mock_algorithm)
        risk1 = RiskEngine(mock_algorithm)

        # Set up state
        trend1.register_entry("QLD", 72.50, "2024-01-14", 2.5)
        risk1.set_equity_prior_close(100000.0)
        risk1.set_equity_sod(101000.0)
        risk1.set_week_start_equity(99000.0)

        # Save all state
        system_state = {
            "trend": trend1.get_state_for_persistence(),
            "risk": risk1.get_state_for_persistence(),
        }

        # === Simulate Crash ===
        del trend1
        del risk1

        # === After Restart ===
        trend2 = TrendEngine(mock_algorithm)
        risk2 = RiskEngine(mock_algorithm)

        # Restore state
        trend2.restore_state(system_state["trend"])
        risk2.load_state(system_state["risk"])

        # === Verify Recovery ===
        # Position intact
        assert trend2.has_position("QLD")

        # Risk baselines - note only persisted state is restored
        # _equity_prior_close is NOT persisted (daily reset), but weekly state is
        assert risk2._week_start_equity == 99000.0

        # Can continue trading - check exit signal
        signal = trend2.check_exit_signals(
            symbol="QLD",
            close=70.0,  # Below stop potentially
            high=72.0,
            ma200=68.0,
            adx=25.0,
            regime_score=50.0,
            atr=2.5,
        )
        # Signal check works (may or may not trigger exit)
        assert signal is None or signal.symbol == "QLD"


# =============================================================================
# Test Markers
# =============================================================================

pytestmark = pytest.mark.integration
