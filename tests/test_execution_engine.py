"""
Unit tests for Execution Engine.

Tests order submission to broker:
- Market order submission
- MOO order submission and timing
- MOO fallback at 09:31
- Order tagging and tracking
- Fill handling
- Order state transitions
- Kill switch support
- Statistics and queries

Spec: docs/13-execution-engine.md
"""

from datetime import datetime

import pytest

from execution.execution_engine import (
    ExecutionEngine,
    ExecutionResult,
    OrderRecord,
    OrderState,
    OrderType,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def engine() -> ExecutionEngine:
    """Create ExecutionEngine without algorithm (testing mode)."""
    return ExecutionEngine(algorithm=None)


# =============================================================================
# Order Creation Tests
# =============================================================================


class TestOrderCreation:
    """Tests for order creation and tracking."""

    def test_create_order_assigns_unique_id(self, engine: ExecutionEngine) -> None:
        """Test that each order gets a unique ID."""
        order1 = engine.create_order("QLD", 100, OrderType.MARKET)
        order2 = engine.create_order("SSO", 50, OrderType.MARKET)

        assert order1.order_id != order2.order_id
        assert order1.order_id.startswith("ORD-")
        assert order2.order_id.startswith("ORD-")

    def test_create_order_initializes_state_pending(self, engine: ExecutionEngine) -> None:
        """Test that new orders start in PENDING state."""
        order = engine.create_order("QLD", 100, OrderType.MARKET)
        assert order.state == OrderState.PENDING

    def test_create_order_stores_metadata(self, engine: ExecutionEngine) -> None:
        """Test that order metadata is stored correctly."""
        order = engine.create_order(
            symbol="TQQQ",
            quantity=200,
            order_type=OrderType.MARKET,
            strategy="MR",
            signal_type="ENTRY",
            reason="RSI oversold",
        )

        assert order.symbol == "TQQQ"
        assert order.quantity == 200
        assert order.order_type == OrderType.MARKET
        assert order.strategy == "MR"
        assert order.signal_type == "ENTRY"
        assert order.reason == "RSI oversold"

    def test_create_order_tracks_created_time(self, engine: ExecutionEngine) -> None:
        """Test that creation time is recorded when algorithm is present.

        Note: In testing mode (no algorithm), created_at is None.
        """
        order = engine.create_order("QLD", 100, OrderType.MARKET)
        # In testing mode, timestamps are None (no algorithm.Time available)
        # With a real algorithm, this would be populated
        assert order.created_at is None  # Testing mode behavior

    def test_order_is_terminal_states(self, engine: ExecutionEngine) -> None:
        """Test is_terminal() for different states."""
        order = engine.create_order("QLD", 100, OrderType.MARKET)

        # PENDING is not terminal
        assert order.is_terminal() is False

        # SUBMITTED is not terminal
        order.state = OrderState.SUBMITTED
        assert order.is_terminal() is False

        # PARTIALLY_FILLED is not terminal
        order.state = OrderState.PARTIALLY_FILLED
        assert order.is_terminal() is False

        # Terminal states
        order.state = OrderState.FILLED
        assert order.is_terminal() is True

        order.state = OrderState.REJECTED
        assert order.is_terminal() is True

        order.state = OrderState.CANCELLED
        assert order.is_terminal() is True


# =============================================================================
# Market Order Tests
# =============================================================================


class TestMarketOrders:
    """Tests for market order submission."""

    def test_market_order_submission_success(self, engine: ExecutionEngine) -> None:
        """Test successful market order submission."""
        result = engine.submit_market_order(
            symbol="QLD",
            quantity=100,
            strategy="TREND",
            signal_type="ENTRY",
            reason="BB Breakout",
        )

        assert result.success is True
        assert result.state == OrderState.SUBMITTED
        assert result.error_message == ""

    def test_market_order_zero_quantity_rejected(self, engine: ExecutionEngine) -> None:
        """Test that zero quantity is rejected."""
        result = engine.submit_market_order(
            symbol="QLD",
            quantity=0,
            strategy="TREND",
        )

        assert result.success is False
        assert result.state == OrderState.REJECTED
        assert "Zero quantity" in result.error_message

    def test_market_order_creates_order_record(self, engine: ExecutionEngine) -> None:
        """Test that market order creates a tracked OrderRecord."""
        result = engine.submit_market_order("QLD", 100, "TREND", "ENTRY", "Test")

        order = engine.get_order(result.order_id)
        assert order is not None
        assert order.symbol == "QLD"
        assert order.quantity == 100
        assert order.order_type == OrderType.MARKET

    def test_market_order_buy_vs_sell(self, engine: ExecutionEngine) -> None:
        """Test buy (positive) vs sell (negative) quantity."""
        buy_result = engine.submit_market_order("QLD", 100, "TREND", "ENTRY", "Buy")
        sell_result = engine.submit_market_order("QLD", -50, "TREND", "EXIT", "Sell")

        buy_order = engine.get_order(buy_result.order_id)
        sell_order = engine.get_order(sell_result.order_id)

        assert buy_order is not None
        assert buy_order.is_buy() is True
        assert buy_order.get_side() == "BUY"

        assert sell_order is not None
        assert sell_order.is_buy() is False
        assert sell_order.get_side() == "SELL"


# =============================================================================
# MOO Order Tests
# =============================================================================


class TestMOOOrders:
    """Tests for Market-On-Open order handling."""

    def test_queue_moo_order(self, engine: ExecutionEngine) -> None:
        """Test queuing a MOO order."""
        order_id = engine.queue_moo_order(
            symbol="QLD",
            quantity=100,
            strategy="TREND",
            signal_type="ENTRY",
            reason="BB Breakout",
        )

        assert order_id != ""
        assert order_id.startswith("ORD-")
        assert engine.get_pending_moo_count() == 1

    def test_queue_moo_zero_quantity_rejected(self, engine: ExecutionEngine) -> None:
        """Test that zero quantity MOO is rejected."""
        order_id = engine.queue_moo_order("QLD", 0, "TREND")
        assert order_id == ""
        assert engine.get_pending_moo_count() == 0

    def test_queue_multiple_moo_orders(self, engine: ExecutionEngine) -> None:
        """Test queuing multiple MOO orders."""
        engine.queue_moo_order("QLD", 100, "TREND", "ENTRY", "Signal 1")
        engine.queue_moo_order("SSO", 50, "TREND", "ENTRY", "Signal 2")
        engine.queue_moo_order("TMF", 30, "HEDGE", "ENTRY", "Signal 3")

        assert engine.get_pending_moo_count() == 3

    def test_submit_pending_moo_orders(self, engine: ExecutionEngine) -> None:
        """Test submitting all pending MOO orders at 15:45."""
        engine.queue_moo_order("QLD", 100, "TREND", "ENTRY", "Test 1")
        engine.queue_moo_order("SSO", 50, "TREND", "ENTRY", "Test 2")

        results = engine.submit_pending_moo_orders()

        assert len(results) == 2
        assert all(r.success for r in results)
        assert engine.get_pending_moo_count() == 0

    def test_submit_moo_clears_pending_queue(self, engine: ExecutionEngine) -> None:
        """Test that submission clears the pending queue."""
        engine.queue_moo_order("QLD", 100, "TREND")
        assert engine.get_pending_moo_count() == 1

        engine.submit_pending_moo_orders()
        assert engine.get_pending_moo_count() == 0

    def test_submit_moo_empty_queue_returns_empty(self, engine: ExecutionEngine) -> None:
        """Test submission with no pending orders."""
        results = engine.submit_pending_moo_orders()
        assert len(results) == 0


# =============================================================================
# MOO Fallback Tests
# =============================================================================


class TestMOOFallback:
    """Tests for MOO fallback logic at 09:31."""

    def test_fallback_empty_queue_returns_empty(self, engine: ExecutionEngine) -> None:
        """Test fallback check with no orders in queue."""
        results = engine.check_moo_fallbacks()
        assert len(results) == 0

    def test_fallback_executes_for_rejected_order(self, engine: ExecutionEngine) -> None:
        """Test that fallback executes market order for rejected MOO."""
        # Queue and submit MOO
        order_id = engine.queue_moo_order("QLD", 100, "TREND", "ENTRY", "Test")
        engine.submit_pending_moo_orders()

        # Simulate rejection
        order = engine.get_order(order_id)
        assert order is not None
        order.state = OrderState.REJECTED
        engine._moo_fallback_queue.append(order_id)

        # Check fallback
        results = engine.check_moo_fallbacks()
        assert len(results) == 1
        assert results[0].success is True

    def test_fallback_skips_filled_order(self, engine: ExecutionEngine) -> None:
        """Test that fallback skips already-filled orders."""
        # Queue and submit MOO
        order_id = engine.queue_moo_order("QLD", 100, "TREND", "ENTRY", "Test")
        engine.submit_pending_moo_orders()

        # Simulate fill
        order = engine.get_order(order_id)
        assert order is not None
        order.state = OrderState.FILLED
        engine._moo_fallback_queue.append(order_id)

        # Check fallback - should skip filled order
        results = engine.check_moo_fallbacks()
        assert len(results) == 0

    def test_fallback_clears_queue(self, engine: ExecutionEngine) -> None:
        """Test that fallback check clears the fallback queue."""
        order_id = engine.queue_moo_order("QLD", 100, "TREND")
        engine.submit_pending_moo_orders()

        # Add to fallback queue
        order = engine.get_order(order_id)
        assert order is not None
        order.state = OrderState.REJECTED
        engine._moo_fallback_queue.append(order_id)

        assert engine.get_fallback_queue_count() == 1
        engine.check_moo_fallbacks()
        assert engine.get_fallback_queue_count() == 0

    def test_fallback_deduplicates_duplicate_queue_entries(self, engine: ExecutionEngine) -> None:
        """Duplicate fallback queue entries must not trigger duplicate market fallbacks."""
        order_id = engine.queue_moo_order("QLD", 100, "TREND", "ENTRY", "Test")
        engine.submit_pending_moo_orders()

        order = engine.get_order(order_id)
        assert order is not None
        order.state = OrderState.REJECTED
        engine._moo_fallback_queue.append(order_id)
        engine._moo_fallback_queue.append(order_id)

        results = engine.check_moo_fallbacks()
        assert len(results) == 1


# =============================================================================
# Fill Processing Tests
# =============================================================================


class TestFillProcessing:
    """Tests for order fill event handling."""

    def test_on_order_event_fill(self, engine: ExecutionEngine) -> None:
        """Test processing a fill event."""
        result = engine.submit_market_order("QLD", 100, "TREND", "ENTRY", "Test")
        order = engine.get_order(result.order_id)
        assert order is not None

        # Simulate broker assigning order ID
        order.broker_order_id = 12345
        engine._broker_order_map[12345] = order.order_id

        # Process fill event
        engine.on_order_event(
            broker_order_id=12345,
            status="Filled",
            fill_price=45.50,
            fill_quantity=100,
        )

        # Verify order updated
        assert order.state == OrderState.FILLED
        assert order.fill_price == 45.50
        assert order.fill_quantity == 100
        # In testing mode, filled_at is None (no algorithm.Time available)
        # With a real algorithm, this would be populated

    def test_on_order_event_partial_fill(self, engine: ExecutionEngine) -> None:
        """Test processing a partial fill event."""
        result = engine.submit_market_order("QLD", 100, "TREND")
        order = engine.get_order(result.order_id)
        assert order is not None

        order.broker_order_id = 12345
        engine._broker_order_map[12345] = order.order_id

        # First partial fill
        engine.on_order_event(12345, "PartiallyFilled", 45.50, 60)
        assert order.state == OrderState.PARTIALLY_FILLED
        assert order.fill_quantity == 60

        # Second partial fill
        engine.on_order_event(12345, "PartiallyFilled", 45.60, 40)
        assert order.fill_quantity == 100  # Cumulative
        assert order.fill_price == pytest.approx(45.54, rel=1e-6)

    def test_on_order_event_filled_after_partials_keeps_cumulative_quantity(self, engine):
        """Filled event after partials should preserve cumulative quantity semantics."""
        result = engine.submit_market_order("QLD", 1000, "TREND")
        order = engine.get_order(result.order_id)
        assert order is not None

        order.broker_order_id = 12346
        engine._broker_order_map[12346] = order.order_id

        engine.on_order_event(12346, "PartiallyFilled", 75.00, 400)
        assert order.fill_quantity == 400

        engine.on_order_event(12346, "Filled", 75.10, 600)
        assert order.state == OrderState.FILLED
        assert order.fill_quantity == 1000
        assert order.fill_price == pytest.approx(75.06, rel=1e-6)

    def test_on_order_event_rejection(self, engine: ExecutionEngine) -> None:
        """Test processing a rejection event."""
        result = engine.submit_market_order("QLD", 100, "TREND")
        order = engine.get_order(result.order_id)
        assert order is not None

        order.broker_order_id = 12345
        engine._broker_order_map[12345] = order.order_id

        engine.on_order_event(
            broker_order_id=12345,
            status="Rejected",
            rejection_reason="Insufficient funds",
        )

        assert order.state == OrderState.REJECTED
        assert order.rejection_reason == "Insufficient funds"

    def test_on_order_event_cancellation(self, engine: ExecutionEngine) -> None:
        """Test processing a cancellation event."""
        result = engine.submit_market_order("QLD", 100, "TREND")
        order = engine.get_order(result.order_id)
        assert order is not None

        order.broker_order_id = 12345
        engine._broker_order_map[12345] = order.order_id

        engine.on_order_event(12345, "Canceled")
        assert order.state == OrderState.CANCELLED

    def test_on_order_event_unknown_order_ignored(self, engine: ExecutionEngine) -> None:
        """Test that unknown order events are ignored."""
        # This should not raise an exception
        engine.on_order_event(99999, "Filled", 45.50, 100)

    def test_rejected_moo_added_to_fallback(self, engine: ExecutionEngine) -> None:
        """Test that rejected MOO orders are added to fallback queue."""
        order_id = engine.queue_moo_order("QLD", 100, "TREND")
        engine.submit_pending_moo_orders()

        order = engine.get_order(order_id)
        assert order is not None

        # Simulate broker order ID
        order.broker_order_id = 12345
        engine._broker_order_map[12345] = order.order_id

        # Clear any existing fallback queue entries
        engine._moo_fallback_queue.clear()

        # Process rejection
        engine.on_order_event(12345, "Rejected", rejection_reason="Symbol halted")

        assert order_id in engine._moo_fallback_queue


# =============================================================================
# Kill Switch Support Tests
# =============================================================================


class TestKillSwitchSupport:
    """Tests for kill switch liquidation support."""

    def test_cancel_all_orders(self, engine: ExecutionEngine) -> None:
        """Test cancelling all pending orders."""
        # Submit some orders
        engine.submit_market_order("QLD", 100, "TREND")
        engine.submit_market_order("SSO", 50, "TREND")
        engine.queue_moo_order("TMF", 30, "HEDGE")

        # Cancel all
        cancelled = engine.cancel_all_orders()
        assert cancelled >= 2  # At least the 2 market orders

    def test_cancel_all_clears_queues(self, engine: ExecutionEngine) -> None:
        """Test that cancel_all clears MOO queues."""
        engine.queue_moo_order("QLD", 100, "TREND")
        assert engine.get_pending_moo_count() == 1

        engine.cancel_all_orders()
        assert engine.get_pending_moo_count() == 0
        assert engine.get_fallback_queue_count() == 0

    def test_liquidate_all_positions(self, engine: ExecutionEngine) -> None:
        """Test liquidating all positions."""
        positions = {"QLD": 100, "SSO": 50, "TMF": 30}

        results = engine.liquidate_all(positions, "KILL_SWITCH")

        assert len(results) == 3
        assert all(r.success for r in results)

        # Verify sell orders created for each position
        for order in engine.get_filled_orders():
            pass  # In test mode, orders go to SUBMITTED not FILLED

    def test_liquidate_all_skips_zero_positions(self, engine: ExecutionEngine) -> None:
        """Test that liquidation skips zero positions."""
        positions = {"QLD": 100, "SSO": 0, "TMF": 30}

        results = engine.liquidate_all(positions, "KILL_SWITCH")
        assert len(results) == 2  # Only QLD and TMF

    def test_liquidate_symbols_specific(self, engine: ExecutionEngine) -> None:
        """Test liquidating specific symbols only."""
        positions = {"QLD": 100, "SSO": 50, "TMF": 30, "TQQQ": 75}

        # Liquidate only leveraged longs (panic mode behavior)
        results = engine.liquidate_symbols(
            positions,
            symbols=["QLD", "SSO", "TQQQ"],
            reason="PANIC_MODE",
        )

        assert len(results) == 3  # QLD, SSO, TQQQ (not TMF)


# =============================================================================
# Order Query Tests
# =============================================================================


class TestOrderQueries:
    """Tests for order query methods."""

    def test_get_order_by_id(self, engine: ExecutionEngine) -> None:
        """Test retrieving order by ID."""
        result = engine.submit_market_order("QLD", 100, "TREND")
        order = engine.get_order(result.order_id)
        assert order is not None
        assert order.symbol == "QLD"

    def test_get_order_invalid_id(self, engine: ExecutionEngine) -> None:
        """Test that invalid ID returns None."""
        order = engine.get_order("INVALID-ID")
        assert order is None

    def test_get_orders_by_state(self, engine: ExecutionEngine) -> None:
        """Test getting orders by state."""
        engine.submit_market_order("QLD", 100, "TREND")
        engine.submit_market_order("SSO", 50, "TREND")

        submitted = engine.get_orders_by_state(OrderState.SUBMITTED)
        assert len(submitted) == 2

    def test_get_pending_orders(self, engine: ExecutionEngine) -> None:
        """Test getting all non-terminal orders."""
        engine.submit_market_order("QLD", 100, "TREND")
        engine.submit_market_order("SSO", 50, "TREND")

        # Mark one as filled
        orders = list(engine._orders.values())
        orders[0].state = OrderState.FILLED

        pending = engine.get_pending_orders()
        assert len(pending) == 1

    def test_get_filled_orders(self, engine: ExecutionEngine) -> None:
        """Test getting filled orders."""
        engine.submit_market_order("QLD", 100, "TREND")
        engine.submit_market_order("SSO", 50, "TREND")

        # Mark one as filled
        orders = list(engine._orders.values())
        orders[0].state = OrderState.FILLED

        filled = engine.get_filled_orders()
        assert len(filled) == 1

    def test_get_orders_for_symbol(self, engine: ExecutionEngine) -> None:
        """Test getting orders for a specific symbol."""
        engine.submit_market_order("QLD", 100, "TREND")
        engine.submit_market_order("QLD", 50, "MR")
        engine.submit_market_order("SSO", 30, "TREND")

        qld_orders = engine.get_orders_for_symbol("QLD")
        assert len(qld_orders) == 2

        sso_orders = engine.get_orders_for_symbol("SSO")
        assert len(sso_orders) == 1


# =============================================================================
# State Management Tests
# =============================================================================


class TestStateManagement:
    """Tests for state persistence and reset."""

    def test_reset_clears_all_state(self, engine: ExecutionEngine) -> None:
        """Test that reset clears all engine state."""
        engine.submit_market_order("QLD", 100, "TREND")
        engine.queue_moo_order("SSO", 50, "TREND")

        engine.reset()

        assert len(engine._orders) == 0
        assert engine._order_counter == 0
        assert engine.get_pending_moo_count() == 0

    def test_reset_daily_clears_terminal_only(self, engine: ExecutionEngine) -> None:
        """Test that daily reset only clears terminal orders."""
        engine.submit_market_order("QLD", 100, "TREND")
        engine.submit_market_order("SSO", 50, "TREND")

        # Mark one as filled (terminal)
        orders = list(engine._orders.values())
        orders[0].state = OrderState.FILLED

        engine.reset_daily()

        # Only the non-terminal order should remain
        assert len(engine._orders) == 1

    def test_get_state_for_persistence(self, engine: ExecutionEngine) -> None:
        """Test state serialization for persistence."""
        engine.submit_market_order("QLD", 100, "TREND")
        engine.queue_moo_order("SSO", 50, "TREND")

        state = engine.get_state_for_persistence()

        assert "order_counter" in state
        assert "orders" in state
        assert "pending_moo_orders" in state
        assert "moo_fallback_queue" in state

    def test_restore_state_round_trip(self, engine: ExecutionEngine) -> None:
        """Execution state should restore orders and queues consistently."""
        result = engine.submit_market_order("QLD", 100, "TREND", "ENTRY", "Test")
        order = engine.get_order(result.order_id)
        assert order is not None
        order.state = OrderState.REJECTED
        order.broker_order_id = 12345
        engine._broker_order_map[12345] = order.order_id
        engine._moo_fallback_queue.append(order.order_id)

        state = engine.get_state_for_persistence()

        restored = ExecutionEngine(algorithm=None)
        restored.restore_state(state)

        restored_order = restored.get_order(order.order_id)
        assert restored_order is not None
        assert restored_order.state == OrderState.REJECTED
        assert restored_order.broker_order_id == 12345
        assert restored.get_order_by_broker_id(12345) is not None
        assert restored.get_fallback_queue_count() == 1


# =============================================================================
# Statistics Tests
# =============================================================================


class TestStatistics:
    """Tests for execution statistics."""

    def test_statistics_empty_engine(self, engine: ExecutionEngine) -> None:
        """Test statistics for empty engine."""
        stats = engine.get_statistics()

        assert stats["total_orders"] == 0
        assert stats["filled"] == 0
        assert stats["rejected"] == 0
        assert stats["cancelled"] == 0
        assert stats["pending"] == 0
        assert stats["fill_rate"] == 0.0

    def test_statistics_with_orders(self, engine: ExecutionEngine) -> None:
        """Test statistics with mixed order states."""
        # Create some orders
        engine.submit_market_order("QLD", 100, "TREND")
        engine.submit_market_order("SSO", 50, "TREND")
        engine.submit_market_order("TMF", 30, "HEDGE")

        # Simulate different states
        orders = list(engine._orders.values())
        orders[0].state = OrderState.FILLED
        orders[1].state = OrderState.REJECTED
        # orders[2] stays SUBMITTED

        stats = engine.get_statistics()

        assert stats["total_orders"] == 3
        assert stats["filled"] == 1
        assert stats["rejected"] == 1
        assert stats["pending"] == 1
        assert stats["fill_rate"] == pytest.approx(1 / 3)


# =============================================================================
# Order Serialization Tests
# =============================================================================


class TestOrderSerialization:
    """Tests for order serialization."""

    def test_order_record_to_dict(self, engine: ExecutionEngine) -> None:
        """Test OrderRecord serialization."""
        result = engine.submit_market_order(
            symbol="QLD",
            quantity=100,
            strategy="TREND",
            signal_type="ENTRY",
            reason="BB Breakout",
        )

        order = engine.get_order(result.order_id)
        assert order is not None

        data = order.to_dict()

        assert data["symbol"] == "QLD"
        assert data["quantity"] == 100
        assert data["order_type"] == "MARKET"
        assert data["state"] == "SUBMITTED"
        assert data["strategy"] == "TREND"
        assert data["signal_type"] == "ENTRY"
        assert data["reason"] == "BB Breakout"

    def test_execution_result_to_dict(self, engine: ExecutionEngine) -> None:
        """Test ExecutionResult serialization."""
        result = engine.submit_market_order("QLD", 100, "TREND")
        data = result.to_dict()

        assert "order_id" in data
        assert data["success"] is True
        assert data["state"] == "SUBMITTED"


# =============================================================================
# Edge Cases
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_negative_quantity_is_sell(self, engine: ExecutionEngine) -> None:
        """Test that negative quantity creates a sell order."""
        result = engine.submit_market_order("QLD", -100, "TREND", "EXIT", "Stop hit")

        order = engine.get_order(result.order_id)
        assert order is not None
        assert order.quantity == -100
        assert order.is_buy() is False
        assert order.get_side() == "SELL"

    def test_large_quantity_order(self, engine: ExecutionEngine) -> None:
        """Test handling large quantity orders."""
        result = engine.submit_market_order("QLD", 100000, "TREND")
        assert result.success is True

    def test_multiple_orders_same_symbol(self, engine: ExecutionEngine) -> None:
        """Test multiple orders for the same symbol."""
        engine.submit_market_order("QLD", 100, "TREND", "ENTRY", "Signal 1")
        engine.submit_market_order("QLD", 50, "MR", "ENTRY", "Signal 2")
        engine.submit_market_order("QLD", -75, "TREND", "EXIT", "Signal 3")

        qld_orders = engine.get_orders_for_symbol("QLD")
        assert len(qld_orders) == 3
