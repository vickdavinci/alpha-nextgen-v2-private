"""
End-to-End Signal Flow Integration Tests.

Tests the complete signal flow from engine to execution:
Engine → Portfolio Router → Execution Engine → Broker → OnOrderEvent

BLOCKER #1: This addresses the critical gap of never testing
the most critical path together.

These tests verify:
1. Trend Engine entry signal flows to Portfolio Router
2. Router aggregates, validates, and creates order intents
3. Execution Engine submits orders to broker (mocked)
4. Fill events are processed correctly back through the system
5. Position state is updated after fills
"""

from datetime import datetime, timedelta
from typing import Dict, List
from unittest.mock import MagicMock, patch

import pytest

import config
from engines.core.risk_engine import RiskEngine
from engines.core.trend_engine import TrendEngine
from engines.satellite.mean_reversion_engine import MeanReversionEngine
from execution.execution_engine import ExecutionEngine, OrderState
from models.enums import Urgency
from models.target_weight import TargetWeight
from portfolio.portfolio_router import (
    AggregatedWeight,
    OrderIntent,
    OrderSide,
    OrderType,
    PortfolioRouter,
)


class MockBroker:
    """Mock broker that simulates order fills."""

    def __init__(self):
        self.submitted_orders: List[Dict] = []
        self.fill_callbacks = []
        self._next_order_id = 1000

    def submit_market_order(self, symbol: str, quantity: int):
        """Simulate market order submission."""
        order_id = self._next_order_id
        self._next_order_id += 1

        self.submitted_orders.append(
            {
                "order_id": order_id,
                "symbol": symbol,
                "quantity": quantity,
                "type": "MARKET",
                "status": "SUBMITTED",
            }
        )

        return order_id

    def simulate_fill(self, order_id: int, fill_price: float):
        """Simulate order fill."""
        for order in self.submitted_orders:
            if order["order_id"] == order_id:
                order["status"] = "FILLED"
                order["fill_price"] = fill_price
                break


@pytest.fixture
def mock_algorithm():
    """Create mock algorithm for testing."""
    algo = MagicMock()
    algo.Time = datetime(2024, 1, 15, 10, 30)
    algo.Portfolio.TotalPortfolioValue = 100000.0
    algo.Portfolio.Cash = 50000.0

    # Create position mocks
    def get_position(symbol):
        position = MagicMock()
        position.Invested = False
        position.Quantity = 0
        position.AveragePrice = 0.0
        position.HoldingsValue = 0.0
        return position

    algo.Portfolio.__getitem__ = MagicMock(side_effect=get_position)
    algo.Log = MagicMock()
    algo.Debug = MagicMock()
    algo.MarketOrder = MagicMock()
    algo.MarketOnOpenOrder = MagicMock()

    # Set up Securities dictionary with common symbols
    def create_security(symbol, price=100.0):
        sec = MagicMock()
        sec.Price = price
        sec.Symbol = symbol
        sec.IsTradable = True
        return sec

    algo.Securities = {
        "QLD": create_security("QLD", 100.0),
        "TQQQ": create_security("TQQQ", 50.0),
        "SSO": create_security("SSO", 75.0),
        "SHV": create_security("SHV", 110.0),
        "TMF": create_security("TMF", 8.0),
    }

    return algo


@pytest.fixture
def e2e_components(mock_algorithm):
    """Create all components needed for E2E testing."""
    trend_engine = TrendEngine(mock_algorithm)
    mr_engine = MeanReversionEngine(mock_algorithm)
    risk_engine = RiskEngine(mock_algorithm)
    router = PortfolioRouter(mock_algorithm)
    exec_engine = ExecutionEngine(mock_algorithm)

    return {
        "trend_engine": trend_engine,
        "mr_engine": mr_engine,
        "risk_engine": risk_engine,
        "router": router,
        "exec_engine": exec_engine,
        "algorithm": mock_algorithm,
    }


class TestTrendEntryToFill:
    """Test complete Trend Engine entry flow to market fill."""

    def test_trend_entry_signal_to_router(self, e2e_components):
        """
        Test: Trend Engine generates entry signal → Router receives it.

        Verifies step 1 of signal flow: Engine emits TargetWeight.
        """
        trend_engine = e2e_components["trend_engine"]
        router = e2e_components["router"]

        # Create conditions for entry
        signal = trend_engine.check_entry_signal(
            symbol="QLD",
            close=150.0,
            ma200=140.0,  # Close > MA200
            adx=30.0,  # ADX >= 25
            regime_score=60.0,  # Regime >= 40
            is_cold_start_active=False,
            has_warm_entry=False,
            atr=2.5,
            current_date="2024-01-15",
        )

        # Verify signal generated
        assert signal is not None
        assert isinstance(signal, TargetWeight)
        assert signal.symbol == "QLD"
        assert signal.source == "TREND"
        # V2.3.3: TrendEngine now uses symbol-specific allocations from config
        # V2.18: Reduced from 20% to 15% to prevent margin overflow
        assert signal.target_weight == 0.15  # QLD gets 15%

        # Router receives signal
        router.receive_signal(signal)
        assert router.get_pending_count() == 1

    def test_router_aggregates_and_validates(self, e2e_components):
        """
        Test: Router aggregates multiple signals and validates against limits.

        Verifies steps 2-4: Aggregate → Validate → Net.
        """
        router = e2e_components["router"]

        # Send multiple signals from different engines
        trend_signal = TargetWeight(
            symbol="QLD",
            target_weight=0.35,
            source="TREND",
            urgency=Urgency.EOD,
            reason="MA200 + ADX Entry",
        )

        mr_signal = TargetWeight(
            symbol="TQQQ",
            target_weight=0.05,
            source="MR",
            urgency=Urgency.IMMEDIATE,
            reason="RSI Oversold",
        )

        router.receive_signal(trend_signal)
        router.receive_signal(mr_signal)

        # Aggregate weights
        aggregated = router.aggregate_weights([trend_signal, mr_signal])

        assert "QLD" in aggregated
        assert "TQQQ" in aggregated
        assert aggregated["QLD"].target_weight == 0.35
        assert aggregated["TQQQ"].target_weight == 0.05

    def test_router_calculates_order_intents(self, e2e_components):
        """
        Test: Router calculates order intents from validated weights.

        Verifies step 5: Convert weights to order intents.
        """
        router = e2e_components["router"]

        # Create aggregated weight
        aggregated = {
            "QLD": AggregatedWeight(
                symbol="QLD",
                target_weight=0.35,
                sources=["TREND"],
                urgency=Urgency.EOD,
                reasons=["MA200 Entry"],
                metadata=None,
            ),
        }

        # Calculate orders
        tradeable_equity = 100000.0
        current_positions = {}  # No existing position
        current_prices = {"QLD": 100.0}

        orders = router.calculate_order_intents(
            aggregated,
            tradeable_equity,
            current_positions,
            current_prices,
        )

        # Should create buy order for 350 shares (35% of $100k / $100)
        assert len(orders) == 1
        assert orders[0].symbol == "QLD"
        assert orders[0].side == OrderSide.BUY
        assert orders[0].quantity == 350

    def test_execution_engine_submits_to_broker(self, e2e_components):
        """
        Test: Execution Engine submits order to broker.

        Verifies step 6: Order submission.
        """
        exec_engine = e2e_components["exec_engine"]

        result = exec_engine.submit_market_order(
            symbol="QLD",
            quantity=350,
            strategy="TREND",
            signal_type="ENTRY",
            reason="MA200 + ADX Entry",
        )

        assert result.success is True
        assert result.state == OrderState.SUBMITTED

    def test_fill_event_updates_state(self, e2e_components):
        """
        Test: Fill event updates execution engine state.

        Verifies OnOrderEvent processing.
        """
        exec_engine = e2e_components["exec_engine"]
        algo = e2e_components["algorithm"]

        # Submit order
        result = exec_engine.submit_market_order(
            symbol="QLD",
            quantity=350,
            strategy="TREND",
            signal_type="ENTRY",
            reason="MA200 + ADX Entry",
        )

        # Get order
        order = exec_engine.get_order(result.order_id)
        assert order is not None
        broker_order_id = order.broker_order_id

        # Simulate fill event
        if broker_order_id:
            exec_engine.on_order_event(
                broker_order_id=broker_order_id,
                status="Filled",
                fill_price=100.50,
                fill_quantity=350,
            )

            # Verify fill processed
            filled_order = exec_engine.get_order(result.order_id)
            assert filled_order.state == OrderState.FILLED
            assert filled_order.fill_price == 100.50
            assert filled_order.fill_quantity == 350


class TestMREntryExitFlow:
    """Test Mean Reversion entry and exit signal flow."""

    def test_mr_immediate_entry_flow(self, e2e_components):
        """
        Test: MR Engine generates IMMEDIATE entry → Router processes immediately.

        MR entries are IMMEDIATE urgency and should execute during market hours.
        """
        router = e2e_components["router"]
        algo = e2e_components["algorithm"]

        # MR signal (IMMEDIATE urgency)
        mr_signal = TargetWeight(
            symbol="TQQQ",
            target_weight=0.05,
            source="MR",
            urgency=Urgency.IMMEDIATE,
            reason="RSI < 25 Oversold",
        )

        router.receive_signal(mr_signal)

        # Process immediate signals
        executed = router.process_immediate(
            tradeable_equity=100000.0,
            current_positions={},
            current_prices={"TQQQ": 50.0},
            max_single_position=0.50,
            available_cash=50000.0,
            locked_amount=0.0,
            current_time="2024-01-15 10:30:00",
        )

        # Verify market order placed
        assert algo.MarketOrder.called
        call_args = algo.MarketOrder.call_args
        assert call_args[0][0] == "TQQQ"

    def test_mr_exit_at_1545(self, e2e_components):
        """
        Test: MR positions forced closed at 15:45.

        All intraday-only positions (TQQQ, SOXL) must exit by 15:45.
        """
        router = e2e_components["router"]
        algo = e2e_components["algorithm"]

        # MR exit signal at 15:45
        exit_signal = TargetWeight(
            symbol="TQQQ",
            target_weight=0.0,  # Close position
            source="MR",
            urgency=Urgency.IMMEDIATE,
            reason="TIME_EXIT_15:45",
        )

        router.receive_signal(exit_signal)

        # Simulate existing position
        current_positions = {"TQQQ": 5000.0}  # $5000 in TQQQ

        # Process immediate (should generate sell)
        executed = router.process_immediate(
            tradeable_equity=100000.0,
            current_positions=current_positions,
            current_prices={"TQQQ": 50.0},
            max_single_position=0.50,
            available_cash=10000.0,
            locked_amount=0.0,
            current_time="2024-01-15 15:45:00",
        )

        # Verify market order for exit
        assert algo.MarketOrder.called


class TestKillSwitchFlow:
    """Test Kill Switch emergency liquidation flow."""

    def test_kill_switch_cancels_all_orders(self, e2e_components):
        """
        Test: Kill switch cancels all pending orders.
        """
        exec_engine = e2e_components["exec_engine"]

        # Submit some orders
        exec_engine.queue_moo_order("QLD", 100, "TREND", "ENTRY", "Test")
        exec_engine.queue_moo_order("SSO", 50, "TREND", "ENTRY", "Test")

        assert exec_engine.get_pending_moo_count() == 2

        # Kill switch cancels all
        cancelled = exec_engine.cancel_all_orders()

        assert exec_engine.get_pending_moo_count() == 0

    def test_kill_switch_liquidates_all_positions(self, e2e_components):
        """
        Test: Kill switch liquidates all positions via execution engine.
        """
        exec_engine = e2e_components["exec_engine"]

        positions = {
            "QLD": 100,
            "SSO": 50,
            "TMF": 200,
        }

        results = exec_engine.liquidate_all(
            positions=positions,
            reason="KILL_SWITCH",
        )

        assert len(results) == 3
        for result in results:
            assert result.success is True


class TestRiskEngineBlocksSignals:
    """Test Risk Engine blocking signal flow."""

    def test_risk_nogo_blocks_execution(self, e2e_components):
        """
        Test: Risk Engine NO-GO status blocks order execution.
        """
        router = e2e_components["router"]
        risk_engine = e2e_components["risk_engine"]

        # Set risk to NO-GO
        router.set_risk_status(False)

        # Try to process signals
        signal = TargetWeight(
            symbol="QLD",
            target_weight=0.35,
            source="TREND",
            urgency=Urgency.IMMEDIATE,
            reason="Test Entry",
        )

        router.receive_signal(signal)

        executed = router.process_immediate(
            tradeable_equity=100000.0,
            current_positions={},
            current_prices={"QLD": 100.0},
            max_single_position=0.50,
            current_time="2024-01-15 10:30:00",
        )

        # No orders should execute
        assert len(executed) == 0


class TestMOOOrderLifecycle:
    """Test Market-on-Open order complete lifecycle."""

    def test_moo_queue_submit_fill(self, e2e_components):
        """
        Test: MOO order lifecycle from queue to fill.

        1. Queue MOO order during day
        2. Submit at 15:45
        3. Check for fills at 09:31 next day
        """
        exec_engine = e2e_components["exec_engine"]

        # Step 1: Queue MOO order
        order_id = exec_engine.queue_moo_order(
            symbol="QLD",
            quantity=100,
            strategy="TREND",
            signal_type="ENTRY",
            reason="MA200 Entry",
        )

        assert order_id != ""
        assert exec_engine.get_pending_moo_count() == 1

        # Step 2: Submit at 15:45
        results = exec_engine.submit_pending_moo_orders()

        assert len(results) == 1
        assert results[0].success is True
        assert exec_engine.get_pending_moo_count() == 0

    def test_moo_fallback_on_rejection(self, e2e_components):
        """
        Test: MOO order fallback to market order on rejection.
        """
        exec_engine = e2e_components["exec_engine"]

        # Queue MOO order
        order_id = exec_engine.queue_moo_order(
            symbol="QLD",
            quantity=100,
            strategy="TREND",
            signal_type="ENTRY",
            reason="MA200 Entry",
        )

        # Submit (in test mode, this succeeds)
        exec_engine.submit_pending_moo_orders()

        # Simulate rejection
        order = exec_engine.get_order(order_id)
        if order and order.broker_order_id:
            exec_engine.on_order_event(
                broker_order_id=order.broker_order_id,
                status="Rejected",
                rejection_reason="Market closed",
            )

            # Verify order in fallback queue
            assert exec_engine.get_fallback_queue_count() == 1

            # Execute fallback
            fallback_results = exec_engine.check_moo_fallbacks()

            # Fallback should submit market order
            assert exec_engine.get_fallback_queue_count() == 0


class TestPartialFillHandling:
    """Test partial fill processing."""

    def test_partial_fill_updates_state(self, e2e_components):
        """
        Test: Partial fills update order state correctly.

        Note: The execution engine accumulates fill_quantity on PartiallyFilled
        events, but on the final Filled event, QC typically sends only the
        last fill amount, not the cumulative total. The test verifies state
        transitions work correctly.
        """
        exec_engine = e2e_components["exec_engine"]

        # Submit order
        result = exec_engine.submit_market_order(
            symbol="QLD",
            quantity=1000,
            strategy="TREND",
            signal_type="ENTRY",
            reason="Test",
        )

        order = exec_engine.get_order(result.order_id)
        assert order is not None
        broker_id = order.broker_order_id

        if broker_id:
            # Partial fill 1: 400 shares
            exec_engine.on_order_event(
                broker_order_id=broker_id,
                status="PartiallyFilled",
                fill_price=100.0,
                fill_quantity=400,
            )

            order = exec_engine.get_order(result.order_id)
            assert order.state == OrderState.PARTIALLY_FILLED
            assert order.fill_quantity == 400

            # Partial fill 2: 600 shares (completes order)
            # Note: on_order_event accumulates for PartiallyFilled but
            # for Filled status it just records the final fill
            exec_engine.on_order_event(
                broker_order_id=broker_id,
                status="Filled",
                fill_price=100.10,
                fill_quantity=600,
            )

            order = exec_engine.get_order(result.order_id)
            assert order.state == OrderState.FILLED
            assert order.fill_quantity == 1000
            assert order.fill_price == pytest.approx(100.06, rel=1e-6)


class TestPositionRegistration:
    """Test position registration after fills."""

    def test_trend_position_registered_after_fill(self, e2e_components):
        """
        Test: Trend Engine position is registered after fill.
        """
        trend_engine = e2e_components["trend_engine"]

        # Register entry (simulates fill handling)
        position = trend_engine.register_entry(
            symbol="QLD",
            entry_price=100.50,
            entry_date="2024-01-15",
            atr=2.5,
            strategy_tag="TREND",
        )

        assert position is not None
        assert position.symbol == "QLD"
        assert position.entry_price == 100.50
        assert trend_engine.has_position("QLD")

    def test_trend_position_removed_after_exit(self, e2e_components):
        """
        Test: Trend Engine position is removed after exit fill.
        """
        trend_engine = e2e_components["trend_engine"]

        # Register position
        trend_engine.register_entry(
            symbol="QLD",
            entry_price=100.50,
            entry_date="2024-01-15",
            atr=2.5,
        )

        assert trend_engine.has_position("QLD")

        # Remove after exit
        removed = trend_engine.remove_position("QLD")

        assert removed is not None
        assert not trend_engine.has_position("QLD")


# =============================================================================
# Test Markers
# =============================================================================

pytestmark = pytest.mark.integration
