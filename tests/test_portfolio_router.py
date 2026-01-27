"""
Unit tests for Portfolio Router.

Tests central coordination and order execution:
- TargetWeight collection from all engines
- Signal aggregation by symbol
- Exposure limit validation
- Share delta calculation
- Urgency-based prioritization
- Order execution (ONLY component authorized)

Spec: docs/11-portfolio-router.md
"""

from unittest.mock import MagicMock, call

import pytest

from models.enums import Urgency
from models.target_weight import TargetWeight
from portfolio.portfolio_router import (
    AggregatedWeight,
    OrderIntent,
    OrderSide,
    OrderType,
    PortfolioRouter,
)


class TestAggregatedWeight:
    """Tests for AggregatedWeight dataclass."""

    def test_creation(self):
        """Test creating an aggregated weight."""
        agg = AggregatedWeight(
            symbol="QLD",
            target_weight=0.30,
            sources=["TREND"],
            urgency=Urgency.EOD,
            reasons=["BB Breakout"],
        )

        assert agg.symbol == "QLD"
        assert agg.target_weight == 0.30
        assert agg.sources == ["TREND"]
        assert agg.urgency == Urgency.EOD
        assert agg.reasons == ["BB Breakout"]

    def test_to_dict(self):
        """Test serialization to dict."""
        agg = AggregatedWeight(
            symbol="QLD",
            target_weight=0.30,
            sources=["TREND", "COLD_START"],
            urgency=Urgency.IMMEDIATE,
            reasons=["Reason 1", "Reason 2"],
        )

        result = agg.to_dict()

        assert result["symbol"] == "QLD"
        assert result["target_weight"] == 0.30
        assert result["sources"] == ["TREND", "COLD_START"]
        assert result["urgency"] == "IMMEDIATE"
        assert len(result["reasons"]) == 2


class TestOrderIntent:
    """Tests for OrderIntent dataclass."""

    def test_creation(self):
        """Test creating an order intent."""
        order = OrderIntent(
            symbol="QLD",
            quantity=100,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            urgency=Urgency.IMMEDIATE,
            reason="Test reason",
            target_weight=0.30,
            current_weight=0.10,
        )

        assert order.symbol == "QLD"
        assert order.quantity == 100
        assert order.side == OrderSide.BUY
        assert order.order_type == OrderType.MARKET
        assert order.urgency == Urgency.IMMEDIATE

    def test_to_dict(self):
        """Test serialization to dict."""
        order = OrderIntent(
            symbol="QLD",
            quantity=100,
            side=OrderSide.SELL,
            order_type=OrderType.MOO,
            urgency=Urgency.EOD,
            reason="Test",
            target_weight=0.20,
            current_weight=0.30,
        )

        result = order.to_dict()

        assert result["symbol"] == "QLD"
        assert result["quantity"] == 100
        assert result["side"] == "SELL"
        assert result["order_type"] == "MOO"


class TestPortfolioRouterInit:
    """Tests for PortfolioRouter initialization."""

    def test_init_without_algorithm(self):
        """Test initialization without algorithm."""
        router = PortfolioRouter()

        assert router.algorithm is None
        assert router.get_pending_count() == 0
        assert router.get_risk_status() is True

    def test_init_with_algorithm(self):
        """Test initialization with mock algorithm."""
        mock_algo = MagicMock()
        router = PortfolioRouter(algorithm=mock_algo)

        assert router.algorithm is mock_algo


class TestCollect:
    """Tests for Step 1: Collect."""

    def test_receive_single_signal(self):
        """Test receiving a single signal."""
        router = PortfolioRouter()

        weight = TargetWeight(
            symbol="QLD",
            target_weight=0.30,
            source="TREND",
            urgency=Urgency.EOD,
            reason="Test",
        )
        router.receive_signal(weight)

        assert router.get_pending_count() == 1

    def test_receive_multiple_signals(self):
        """Test receiving multiple signals."""
        router = PortfolioRouter()

        weights = [
            TargetWeight("QLD", 0.30, "TREND", Urgency.EOD, "Test 1"),
            TargetWeight("TMF", 0.10, "HEDGE", Urgency.EOD, "Test 2"),
            TargetWeight("SHV", 0.50, "YIELD", Urgency.EOD, "Test 3"),
        ]
        router.receive_signals(weights)

        assert router.get_pending_count() == 3

    def test_clear_pending(self):
        """Test clearing pending signals."""
        router = PortfolioRouter()

        router.receive_signal(TargetWeight("QLD", 0.30, "TREND", Urgency.EOD, "Test"))
        assert router.get_pending_count() == 1

        router.clear_pending()
        assert router.get_pending_count() == 0


class TestAggregate:
    """Tests for Step 2: Aggregate."""

    def test_aggregate_single_weight(self):
        """Test aggregating a single weight."""
        router = PortfolioRouter()

        weights = [TargetWeight("QLD", 0.30, "TREND", Urgency.EOD, "BB Breakout")]
        result = router.aggregate_weights(weights)

        assert "QLD" in result
        assert result["QLD"].target_weight == 0.30
        assert result["QLD"].sources == ["TREND"]
        assert result["QLD"].urgency == Urgency.EOD

    def test_aggregate_same_symbol_same_direction(self):
        """Test aggregating same symbol from multiple sources."""
        router = PortfolioRouter()

        weights = [
            TargetWeight("QLD", 0.20, "TREND", Urgency.EOD, "Trend signal"),
            TargetWeight("QLD", 0.10, "COLD_START", Urgency.EOD, "Cold start"),
        ]
        result = router.aggregate_weights(weights)

        assert pytest.approx(result["QLD"].target_weight) == 0.30  # 0.20 + 0.10
        assert "TREND" in result["QLD"].sources
        assert "COLD_START" in result["QLD"].sources

    def test_aggregate_exit_signal(self):
        """Test aggregating entry and exit signals (exit = 0 weight)."""
        router = PortfolioRouter()

        # Entry signal wants 25%, exit signal wants 0%
        # Aggregated = 25% (exit doesn't reduce, just adds its preference)
        weights = [
            TargetWeight("TQQQ", 0.25, "MR", Urgency.IMMEDIATE, "MR Entry"),
            TargetWeight("TQQQ", 0.0, "RISK", Urgency.IMMEDIATE, "Exit signal"),
        ]
        result = router.aggregate_weights(weights)

        # Sum is 0.25 + 0.0 = 0.25
        assert pytest.approx(result["TQQQ"].target_weight) == 0.25
        # Both sources tracked
        assert "MR" in result["TQQQ"].sources
        assert "RISK" in result["TQQQ"].sources

    def test_aggregate_immediate_takes_precedence(self):
        """Test that IMMEDIATE urgency takes precedence over EOD."""
        router = PortfolioRouter()

        weights = [
            TargetWeight("QLD", 0.20, "TREND", Urgency.EOD, "EOD signal"),
            TargetWeight("QLD", 0.10, "RISK", Urgency.IMMEDIATE, "Stop loss"),
        ]
        result = router.aggregate_weights(weights)

        assert result["QLD"].urgency == Urgency.IMMEDIATE

    def test_aggregate_multiple_symbols(self):
        """Test aggregating multiple symbols."""
        router = PortfolioRouter()

        weights = [
            TargetWeight("QLD", 0.30, "TREND", Urgency.EOD, "Test"),
            TargetWeight("TMF", 0.10, "HEDGE", Urgency.EOD, "Test"),
            TargetWeight("SHV", 0.50, "YIELD", Urgency.EOD, "Test"),
        ]
        result = router.aggregate_weights(weights)

        assert len(result) == 3
        assert "QLD" in result
        assert "TMF" in result
        assert "SHV" in result


class TestValidate:
    """Tests for Step 3: Validate."""

    def test_validate_within_limits(self):
        """Test validation when within all limits."""
        router = PortfolioRouter()

        aggregated = {
            "QLD": AggregatedWeight("QLD", 0.30, ["TREND"], Urgency.EOD, []),
        }
        result = router.validate_weights(aggregated, max_single_position=0.40)

        assert result["QLD"].target_weight == 0.30  # Unchanged

    def test_validate_caps_single_position(self):
        """Test single position limit is enforced."""
        router = PortfolioRouter()

        aggregated = {
            "QLD": AggregatedWeight("QLD", 0.60, ["TREND"], Urgency.EOD, []),
        }
        result = router.validate_weights(aggregated, max_single_position=0.40)

        assert result["QLD"].target_weight == 0.40  # Capped

    def test_validate_clamps_negative(self):
        """Test negative weights are clamped to zero."""
        router = PortfolioRouter()

        aggregated = {
            "QLD": AggregatedWeight("QLD", -0.10, ["RISK"], Urgency.IMMEDIATE, []),
        }
        result = router.validate_weights(aggregated, max_single_position=0.40)

        assert result["QLD"].target_weight == 0.0  # Clamped

    def test_validate_applies_exposure_limits(self):
        """Test exposure group limits are applied."""
        router = PortfolioRouter()

        # 75% NASDAQ_BETA, should be scaled to 50%
        aggregated = {
            "QLD": AggregatedWeight("QLD", 0.35, ["TREND"], Urgency.EOD, []),
            "TQQQ": AggregatedWeight("TQQQ", 0.25, ["MR"], Urgency.IMMEDIATE, []),
            "SOXL": AggregatedWeight("SOXL", 0.15, ["MR"], Urgency.IMMEDIATE, []),
        }
        result = router.validate_weights(aggregated, max_single_position=0.50)

        # Total should be scaled to ~50%
        total = (
            result["QLD"].target_weight
            + result["TQQQ"].target_weight
            + result["SOXL"].target_weight
        )
        assert pytest.approx(total, rel=0.01) == 0.50


class TestCalculateOrderIntents:
    """Tests for Step 4: Net (calculate order intents)."""

    def test_calculate_buy_order(self):
        """Test calculating a buy order."""
        router = PortfolioRouter()

        aggregated = {
            "QLD": AggregatedWeight("QLD", 0.30, ["TREND"], Urgency.EOD, ["Test reason"]),
        }
        orders = router.calculate_order_intents(
            aggregated=aggregated,
            tradeable_equity=100000.0,
            current_positions={"QLD": 10000.0},  # Currently 10%
            current_prices={"QLD": 80.0},
        )

        assert len(orders) == 1
        assert orders[0].symbol == "QLD"
        assert orders[0].side == OrderSide.BUY
        # Target: 30% of $100k = $30k
        # Current: $10k
        # Delta: $20k / $80 = 250 shares
        assert orders[0].quantity == 250
        assert orders[0].order_type == OrderType.MOO  # EOD urgency

    def test_calculate_sell_order(self):
        """Test calculating a sell order."""
        router = PortfolioRouter()

        aggregated = {
            "QLD": AggregatedWeight("QLD", 0.10, ["TREND"], Urgency.EOD, ["Test"]),
        }
        orders = router.calculate_order_intents(
            aggregated=aggregated,
            tradeable_equity=100000.0,
            current_positions={"QLD": 30000.0},  # Currently 30%
            current_prices={"QLD": 80.0},
        )

        assert len(orders) == 1
        assert orders[0].symbol == "QLD"
        assert orders[0].side == OrderSide.SELL
        # Delta: -$20k / $80 = 250 shares to sell
        assert orders[0].quantity == 250

    def test_calculate_immediate_uses_market_order(self):
        """Test IMMEDIATE urgency uses MarketOrder."""
        router = PortfolioRouter()

        aggregated = {
            "TQQQ": AggregatedWeight("TQQQ", 0.15, ["MR"], Urgency.IMMEDIATE, ["MR Entry"]),
        }
        orders = router.calculate_order_intents(
            aggregated=aggregated,
            tradeable_equity=100000.0,
            current_positions={},
            current_prices={"TQQQ": 50.0},
        )

        assert len(orders) == 1
        assert orders[0].order_type == OrderType.MARKET

    def test_skip_below_minimum_trade(self):
        """Test orders below minimum trade value are skipped."""
        router = PortfolioRouter()

        aggregated = {
            "QLD": AggregatedWeight("QLD", 0.01, ["TREND"], Urgency.EOD, ["Test"]),
        }
        orders = router.calculate_order_intents(
            aggregated=aggregated,
            tradeable_equity=100000.0,
            current_positions={},  # No current position
            current_prices={"QLD": 80.0},
        )

        # $1000 delta is below $2000 minimum
        assert len(orders) == 0

    def test_skip_no_price(self):
        """Test symbols without prices are skipped."""
        router = PortfolioRouter()

        aggregated = {
            "QLD": AggregatedWeight("QLD", 0.30, ["TREND"], Urgency.EOD, ["Test"]),
        }
        orders = router.calculate_order_intents(
            aggregated=aggregated,
            tradeable_equity=100000.0,
            current_positions={},
            current_prices={},  # No prices
        )

        assert len(orders) == 0


class TestPrioritize:
    """Tests for Step 5: Prioritize."""

    def test_separate_by_urgency(self):
        """Test orders are separated by urgency."""
        router = PortfolioRouter()

        orders = [
            OrderIntent("QLD", 100, OrderSide.BUY, OrderType.MOO, Urgency.EOD, "Test", 0.30, 0.10),
            OrderIntent(
                "TQQQ", 50, OrderSide.BUY, OrderType.MARKET, Urgency.IMMEDIATE, "Test", 0.15, 0.0
            ),
            OrderIntent("TMF", 25, OrderSide.BUY, OrderType.MOO, Urgency.EOD, "Test", 0.10, 0.05),
        ]

        immediate, eod = router.prioritize_orders(orders)

        assert len(immediate) == 1
        assert immediate[0].symbol == "TQQQ"
        assert len(eod) == 2

    def test_all_immediate(self):
        """Test when all orders are IMMEDIATE."""
        router = PortfolioRouter()

        orders = [
            OrderIntent(
                "TQQQ", 50, OrderSide.BUY, OrderType.MARKET, Urgency.IMMEDIATE, "Test", 0.15, 0.0
            ),
            OrderIntent(
                "SOXL", 30, OrderSide.BUY, OrderType.MARKET, Urgency.IMMEDIATE, "Test", 0.10, 0.0
            ),
        ]

        immediate, eod = router.prioritize_orders(orders)

        assert len(immediate) == 2
        assert len(eod) == 0

    def test_all_eod(self):
        """Test when all orders are EOD."""
        router = PortfolioRouter()

        orders = [
            OrderIntent("QLD", 100, OrderSide.BUY, OrderType.MOO, Urgency.EOD, "Test", 0.30, 0.10),
            OrderIntent("TMF", 25, OrderSide.BUY, OrderType.MOO, Urgency.EOD, "Test", 0.10, 0.05),
        ]

        immediate, eod = router.prioritize_orders(orders)

        assert len(immediate) == 0
        assert len(eod) == 2


class TestExecute:
    """Tests for Step 6: Execute."""

    def test_execute_market_order(self):
        """Test executing a market order."""
        mock_algo = MagicMock()
        router = PortfolioRouter(algorithm=mock_algo)

        orders = [
            OrderIntent(
                "TQQQ", 50, OrderSide.BUY, OrderType.MARKET, Urgency.IMMEDIATE, "Test", 0.15, 0.0
            ),
        ]

        executed = router.execute_orders(orders)

        assert len(executed) == 1
        mock_algo.MarketOrder.assert_called_once_with("TQQQ", 50)

    def test_execute_moo_order(self):
        """Test executing a MOO order."""
        mock_algo = MagicMock()
        router = PortfolioRouter(algorithm=mock_algo)

        orders = [
            OrderIntent("QLD", 100, OrderSide.BUY, OrderType.MOO, Urgency.EOD, "Test", 0.30, 0.10),
        ]

        executed = router.execute_orders(orders)

        assert len(executed) == 1
        mock_algo.MarketOnOpenOrder.assert_called_once_with("QLD", 100)

    def test_execute_sell_order(self):
        """Test executing a sell order (negative quantity)."""
        mock_algo = MagicMock()
        router = PortfolioRouter(algorithm=mock_algo)

        orders = [
            OrderIntent(
                "QLD", 50, OrderSide.SELL, OrderType.MARKET, Urgency.IMMEDIATE, "Test", 0.10, 0.20
            ),
        ]

        executed = router.execute_orders(orders)

        assert len(executed) == 1
        mock_algo.MarketOrder.assert_called_once_with("QLD", -50)  # Negative for sell

    def test_execute_blocked_by_risk_engine(self):
        """Test orders blocked when risk engine is NO-GO."""
        mock_algo = MagicMock()
        router = PortfolioRouter(algorithm=mock_algo)
        router.set_risk_status(go=False)

        orders = [
            OrderIntent(
                "QLD", 100, OrderSide.BUY, OrderType.MARKET, Urgency.IMMEDIATE, "Test", 0.30, 0.0
            ),
        ]

        executed = router.execute_orders(orders)

        assert len(executed) == 0
        mock_algo.MarketOrder.assert_not_called()

    def test_execute_no_algorithm(self):
        """Test execution without algorithm returns empty."""
        router = PortfolioRouter()  # No algorithm

        orders = [
            OrderIntent(
                "QLD", 100, OrderSide.BUY, OrderType.MARKET, Urgency.IMMEDIATE, "Test", 0.30, 0.0
            ),
        ]

        executed = router.execute_orders(orders)

        assert len(executed) == 0


class TestProcessImmediate:
    """Tests for process_immediate method."""

    def test_process_immediate_only(self):
        """Test only IMMEDIATE signals are processed."""
        mock_algo = MagicMock()
        router = PortfolioRouter(algorithm=mock_algo)

        # Add both IMMEDIATE and EOD signals
        router.receive_signal(TargetWeight("TQQQ", 0.15, "MR", Urgency.IMMEDIATE, "MR Entry"))
        router.receive_signal(TargetWeight("QLD", 0.30, "TREND", Urgency.EOD, "Trend Entry"))

        executed = router.process_immediate(
            tradeable_equity=100000.0,
            current_positions={},
            current_prices={"TQQQ": 50.0, "QLD": 80.0},
            max_single_position=0.40,
        )

        # Only TQQQ should be executed
        assert len(executed) == 1
        assert executed[0].symbol == "TQQQ"

        # EOD signal should still be pending
        assert router.get_pending_count() == 1

    def test_process_immediate_no_signals(self):
        """Test process_immediate with no IMMEDIATE signals."""
        router = PortfolioRouter()

        router.receive_signal(TargetWeight("QLD", 0.30, "TREND", Urgency.EOD, "Test"))

        executed = router.process_immediate(
            tradeable_equity=100000.0,
            current_positions={},
            current_prices={"QLD": 80.0},
            max_single_position=0.40,
        )

        assert len(executed) == 0
        assert router.get_pending_count() == 1  # EOD signal still pending


class TestProcessEOD:
    """Tests for process_eod method."""

    def test_process_eod_all_pending(self):
        """Test all pending signals processed at EOD."""
        mock_algo = MagicMock()
        router = PortfolioRouter(algorithm=mock_algo)

        router.receive_signals(
            [
                TargetWeight("QLD", 0.30, "TREND", Urgency.EOD, "Test 1"),
                TargetWeight("TMF", 0.10, "HEDGE", Urgency.EOD, "Test 2"),
            ]
        )

        executed = router.process_eod(
            tradeable_equity=100000.0,
            current_positions={},
            current_prices={"QLD": 80.0, "TMF": 25.0},
            max_single_position=0.40,
        )

        assert len(executed) == 2
        assert router.get_pending_count() == 0  # All processed

    def test_process_eod_no_pending(self):
        """Test process_eod with no pending signals."""
        router = PortfolioRouter()

        executed = router.process_eod(
            tradeable_equity=100000.0,
            current_positions={},
            current_prices={},
            max_single_position=0.40,
        )

        assert len(executed) == 0


class TestRiskEngineIntegration:
    """Tests for risk engine GO/NO-GO status."""

    def test_default_go_status(self):
        """Test default risk status is GO."""
        router = PortfolioRouter()

        assert router.get_risk_status() is True

    def test_set_no_go(self):
        """Test setting NO-GO status."""
        router = PortfolioRouter()

        router.set_risk_status(go=False)

        assert router.get_risk_status() is False

    def test_set_go(self):
        """Test setting GO status."""
        router = PortfolioRouter()

        router.set_risk_status(go=False)
        router.set_risk_status(go=True)

        assert router.get_risk_status() is True


class TestSHVLiquidation:
    """Tests for SHV liquidation support."""

    def test_calculate_shv_liquidation(self):
        """Test calculating SHV liquidation."""
        router = PortfolioRouter()

        result = router.calculate_shv_liquidation(
            cash_needed=20000.0,
            current_shv_value=45000.0,
            locked_amount=10000.0,
            tradeable_equity=100000.0,
        )

        assert result is not None
        assert result.symbol == "SHV"
        assert result.urgency == Urgency.IMMEDIATE
        # Remaining SHV: $45k - $20k = $25k
        # Target weight: $25k / $100k = 25%
        assert pytest.approx(result.target_weight, rel=0.01) == 0.25

    def test_calculate_shv_liquidation_limited_by_available(self):
        """Test liquidation limited by available SHV."""
        router = PortfolioRouter()

        result = router.calculate_shv_liquidation(
            cash_needed=50000.0,  # Need more than available
            current_shv_value=45000.0,
            locked_amount=10000.0,  # Only $35k available
            tradeable_equity=100000.0,
        )

        assert result is not None
        # Can only sell $35k (available), remaining = $10k (locked)
        # Target weight: $10k / $100k = 10%
        assert pytest.approx(result.target_weight, rel=0.01) == 0.10

    def test_calculate_shv_liquidation_no_available(self):
        """Test no liquidation when no SHV available."""
        router = PortfolioRouter()

        result = router.calculate_shv_liquidation(
            cash_needed=20000.0,
            current_shv_value=10000.0,
            locked_amount=10000.0,  # All locked
            tradeable_equity=100000.0,
        )

        assert result is None


class TestStateManagement:
    """Tests for state management."""

    def test_get_last_orders(self):
        """Test getting last executed orders."""
        mock_algo = MagicMock()
        router = PortfolioRouter(algorithm=mock_algo)

        router.receive_signal(TargetWeight("QLD", 0.30, "TREND", Urgency.EOD, "Test"))
        router.process_eod(
            tradeable_equity=100000.0,
            current_positions={},
            current_prices={"QLD": 80.0},
            max_single_position=0.40,
        )

        orders = router.get_last_orders()
        assert len(orders) == 1

    def test_get_state_for_persistence(self):
        """Test getting state for persistence."""
        router = PortfolioRouter()

        router.receive_signal(TargetWeight("QLD", 0.30, "TREND", Urgency.EOD, "Test"))
        router.set_risk_status(go=False)

        state = router.get_state_for_persistence()

        assert state["pending_count"] == 1
        assert state["risk_status"] is False

    def test_reset(self):
        """Test resetting router state."""
        router = PortfolioRouter()

        router.receive_signal(TargetWeight("QLD", 0.30, "TREND", Urgency.EOD, "Test"))
        router.set_risk_status(go=False)

        router.reset()

        assert router.get_pending_count() == 0
        assert router.get_risk_status() is True
        assert len(router.get_last_orders()) == 0


class TestOrderEnums:
    """Tests for OrderSide and OrderType enums."""

    def test_order_side_values(self):
        """Test OrderSide enum values."""
        assert OrderSide.BUY.value == "BUY"
        assert OrderSide.SELL.value == "SELL"

    def test_order_type_values(self):
        """Test OrderType enum values."""
        assert OrderType.MARKET.value == "MARKET"
        assert OrderType.MOO.value == "MOO"
