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

from datetime import datetime, timedelta
from unittest.mock import ANY, MagicMock, call

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

    def test_aggregate_intraday_same_symbol_keeps_lane_isolation(self):
        """OPT_INTRADAY signals from ITM and MICRO lanes must not collapse."""
        router = PortfolioRouter()

        weights = [
            TargetWeight(
                "QQQ 260130C00500000",
                0.10,
                "OPT_INTRADAY",
                Urgency.IMMEDIATE,
                "MICRO entry",
                metadata={"intraday_strategy": "MICRO_DEBIT_FADE", "trace_source": "MICRO"},
            ),
            TargetWeight(
                "QQQ 260130C00500000",
                0.15,
                "OPT_INTRADAY",
                Urgency.IMMEDIATE,
                "ITM entry",
                metadata={"intraday_strategy": "ITM_MOMENTUM", "trace_source": "ITM"},
            ),
        ]

        result = router.aggregate_weights(weights)

        assert len(result) == 2
        weights_out = sorted(agg.target_weight for agg in result.values())
        assert weights_out == [0.10, 0.15]


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

    def test_option_close_opt_source_infers_intraday_lane_tag(self):
        """Single-leg close with OPT source and missing metadata should infer ITM/MICRO lane."""
        option_symbol = "QQQ 260130C00500000"
        holding = MagicMock()
        holding.Symbol = option_symbol
        holding.Quantity = 4
        kvp = MagicMock()
        kvp.Value = holding

        mock_algo = MagicMock()
        mock_algo.Portfolio = [kvp]
        mock_algo.options_engine = MagicMock()
        mock_algo.options_engine.find_engine_lane_by_symbol.return_value = "ITM"

        router = PortfolioRouter(algorithm=mock_algo)
        aggregated = {
            option_symbol: AggregatedWeight(
                option_symbol,
                0.0,
                ["OPT"],
                Urgency.IMMEDIATE,
                ["INTRADAY_FORCE_EXIT"],
            )
        }
        orders = router.calculate_order_intents(
            aggregated=aggregated,
            tradeable_equity=100000.0,
            current_positions={option_symbol: 1000.0},
            current_prices={option_symbol: 2.5},
        )

        assert len(orders) == 1
        assert orders[0].tag == "ITM:UNCLASSIFIED"
        assert not orders[0].tag.startswith("VASS:")

    def test_option_close_with_vass_metadata_keeps_vass_tag(self):
        """VASS closes with explicit spread metadata should not be relabeled to intraday lanes."""
        option_symbol = "QQQ 260130C00510000"
        holding = MagicMock()
        holding.Symbol = option_symbol
        holding.Quantity = 2
        kvp = MagicMock()
        kvp.Value = holding

        mock_algo = MagicMock()
        mock_algo.Portfolio = [kvp]
        mock_algo.options_engine = MagicMock()
        mock_algo.options_engine.find_engine_lane_by_symbol.return_value = "MICRO"

        router = PortfolioRouter(algorithm=mock_algo)
        aggregated = {
            option_symbol: AggregatedWeight(
                option_symbol,
                0.0,
                ["OPT"],
                Urgency.IMMEDIATE,
                ["spread close"],
                metadata={"vass_strategy": "BULL_CALL_DEBIT"},
            )
        }
        orders = router.calculate_order_intents(
            aggregated=aggregated,
            tradeable_equity=100000.0,
            current_positions={option_symbol: 500.0},
            current_prices={option_symbol: 2.5},
        )

        assert len(orders) == 1
        assert orders[0].tag == "VASS:BULL_CALL_DEBIT"

    def test_opt_intraday_close_without_lane_metadata_infers_live_lane(self):
        """OPT_INTRADAY close with missing lane should infer lane from live position map."""
        option_symbol = "QQQ 260130C00500000"
        holding = MagicMock()
        holding.Symbol = option_symbol
        holding.Quantity = 2
        kvp = MagicMock()
        kvp.Value = holding

        mock_algo = MagicMock()
        mock_algo.Portfolio = [kvp]
        mock_algo.options_engine = MagicMock()
        mock_algo.options_engine.find_engine_lane_by_symbol.return_value = "ITM"

        router = PortfolioRouter(algorithm=mock_algo)
        weights = [
            TargetWeight(
                option_symbol,
                0.0,
                "OPT_INTRADAY",
                Urgency.IMMEDIATE,
                "forced close",
                metadata={"options_strategy": "UNCLASSIFIED"},
            )
        ]
        aggregated = router.aggregate_weights(weights)
        orders = router.calculate_order_intents(
            aggregated=aggregated,
            tradeable_equity=100000.0,
            current_positions={option_symbol: 500.0},
            current_prices={option_symbol: 2.5},
        )

        assert len(orders) == 1
        assert orders[0].tag == "ITM:UNCLASSIFIED"

    def test_opt_intraday_close_without_lane_metadata_uses_unknown_tag_when_lane_missing(self):
        """OPT_INTRADAY close with unresolved lane must stay lane-neutral."""
        option_symbol = "QQQ 260130C00520000"
        holding = MagicMock()
        holding.Symbol = option_symbol
        holding.Quantity = 2
        kvp = MagicMock()
        kvp.Value = holding

        mock_algo = MagicMock()
        mock_algo.Portfolio = [kvp]
        mock_algo.options_engine = MagicMock()
        mock_algo.options_engine.find_engine_lane_by_symbol.return_value = None

        router = PortfolioRouter(algorithm=mock_algo)
        weights = [
            TargetWeight(
                option_symbol,
                0.0,
                "OPT_INTRADAY",
                Urgency.IMMEDIATE,
                "forced close",
                metadata={"options_strategy": "UNCLASSIFIED"},
            )
        ]
        aggregated = router.aggregate_weights(weights)
        orders = router.calculate_order_intents(
            aggregated=aggregated,
            tradeable_equity=100000.0,
            current_positions={option_symbol: 500.0},
            current_prices={option_symbol: 2.5},
        )

        assert len(orders) == 1
        assert orders[0].tag == "OPT_UNKNOWN:UNCLASSIFIED"

    def test_option_close_risk_source_infers_lane_tag(self):
        """RISK-sourced option closes should retain lane-aware tags, not generic RISK."""
        option_symbol = "QQQ 260130C00500000"
        holding = MagicMock()
        holding.Symbol = option_symbol
        holding.Quantity = 2
        kvp = MagicMock()
        kvp.Value = holding

        mock_algo = MagicMock()
        mock_algo.Portfolio = [kvp]
        mock_algo.options_engine = MagicMock()
        mock_algo.options_engine.find_engine_lane_by_symbol.return_value = "ITM"

        router = PortfolioRouter(algorithm=mock_algo)
        aggregated = {
            option_symbol: AggregatedWeight(
                option_symbol,
                0.0,
                ["RISK"],
                Urgency.IMMEDIATE,
                ["GREEKS_BREACH"],
            )
        }
        orders = router.calculate_order_intents(
            aggregated=aggregated,
            tradeable_equity=100000.0,
            current_positions={option_symbol: 500.0},
            current_prices={option_symbol: 2.5},
        )

        assert len(orders) == 1
        assert orders[0].tag == "ITM:RISK_EXIT"

    def test_extract_trace_context_normalizes_risk_source_for_options(self):
        """Trace context should map option RISK source into canonical lane-aware source tags."""
        option_symbol = "QQQ 260130C00500000"
        mock_algo = MagicMock()
        mock_algo.options_engine = MagicMock()
        mock_algo.options_engine.find_engine_lane_by_symbol.return_value = "MICRO"
        router = PortfolioRouter(algorithm=mock_algo)

        source_tag, trace_id = router._extract_trace_context(
            metadata=None,
            sources=["RISK"],
            symbol=option_symbol,
        )

        assert trace_id == ""
        assert source_tag == "OPT_MICRO"

    def test_option_close_risk_source_defaults_to_micro_when_lane_unknown(self):
        """Unknown-lane option risk exits should remain lane-neutral for RCA fidelity."""
        option_symbol = "QQQ 260130C00510000"
        holding = MagicMock()
        holding.Symbol = option_symbol
        holding.Quantity = 1
        kvp = MagicMock()
        kvp.Value = holding

        mock_algo = MagicMock()
        mock_algo.Portfolio = [kvp]
        mock_algo.options_engine = MagicMock()
        mock_algo.options_engine.find_engine_lane_by_symbol.return_value = None

        router = PortfolioRouter(algorithm=mock_algo)
        aggregated = {
            option_symbol: AggregatedWeight(
                option_symbol,
                0.0,
                ["RISK"],
                Urgency.IMMEDIATE,
                ["GREEKS_BREACH"],
            )
        }
        orders = router.calculate_order_intents(
            aggregated=aggregated,
            tradeable_equity=100000.0,
            current_positions={option_symbol: 250.0},
            current_prices={option_symbol: 2.5},
        )

        assert len(orders) == 1
        assert orders[0].tag == "OPT:RISK_EXIT"

    def test_extract_trace_context_risk_source_uses_opt_unknown_when_lane_unknown(self):
        """Unknown-lane option risk source should normalize to OPT_UNKNOWN."""
        option_symbol = "QQQ 260130C00510000"
        mock_algo = MagicMock()
        mock_algo.options_engine = MagicMock()
        mock_algo.options_engine.find_engine_lane_by_symbol.return_value = None
        router = PortfolioRouter(algorithm=mock_algo)

        source_tag, trace_id = router._extract_trace_context(
            metadata=None,
            sources=["RISK"],
            symbol=option_symbol,
        )

        assert trace_id == ""
        assert source_tag == "OPT_UNKNOWN"

    def test_extract_trace_context_opt_intraday_uses_opt_unknown_when_lane_unknown(self):
        """Unknown-lane OPT_INTRADAY source should normalize to OPT_UNKNOWN."""
        option_symbol = "QQQ 260130C00510000"
        mock_algo = MagicMock()
        mock_algo.options_engine = MagicMock()
        mock_algo.options_engine.find_engine_lane_by_symbol.return_value = None
        router = PortfolioRouter(algorithm=mock_algo)

        source_tag, trace_id = router._extract_trace_context(
            metadata=None,
            sources=["OPT_INTRADAY"],
            symbol=option_symbol,
        )

        assert trace_id == ""
        assert source_tag == "OPT_UNKNOWN"


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
        mock_algo.MarketOrder.assert_called_once_with("TQQQ", 50, tag=ANY)

    def test_execute_moo_order(self):
        """Test executing a MOO order."""
        mock_algo = MagicMock()
        router = PortfolioRouter(algorithm=mock_algo)

        orders = [
            OrderIntent("QLD", 100, OrderSide.BUY, OrderType.MOO, Urgency.EOD, "Test", 0.30, 0.10),
        ]

        executed = router.execute_orders(orders)

        assert len(executed) == 1
        mock_algo.MarketOnOpenOrder.assert_called_once_with("QLD", 100, tag=ANY)

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
        mock_algo.MarketOrder.assert_called_once_with("QLD", -50, tag=ANY)  # Negative for sell

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

    def test_execute_preclear_pending_is_deferred_not_rejected(self):
        """Transient preclear pending should defer close submit without router rejection."""
        mock_algo = MagicMock()
        router = PortfolioRouter(algorithm=mock_algo)
        router._run_option_exit_preclear = MagicMock(
            return_value=(
                False,
                "EXIT_PRE_CLEAR_PENDING: Symbols=QQQ 260130C00500000 | Elapsed=0s/30s",
            )
        )

        orders = [
            OrderIntent(
                "QQQ 260130C00500000",
                1,
                OrderSide.SELL,
                OrderType.MARKET,
                Urgency.IMMEDIATE,
                "TEST_CLOSE",
                0.0,
                0.0,
                metadata={"options_lane": "VASS"},
            ),
        ]

        executed = router.execute_orders(orders)

        assert executed == []
        assert router.get_last_rejections() == []
        mock_algo.MarketOrder.assert_not_called()


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

    def test_restore_state(self):
        """Test restoring persisted router state."""
        router = PortfolioRouter()
        state = {
            "risk_status": False,
            "open_spread_margin": {"LONG|SHORT": 750.0},
        }

        router.restore_state(state)

        assert router.get_risk_status() is False
        assert router.get_reserved_spread_margin() == 750.0

    def test_reset(self):
        """Test resetting router state."""
        router = PortfolioRouter()

        router.receive_signal(TargetWeight("QLD", 0.30, "TREND", Urgency.EOD, "Test"))
        router.set_risk_status(go=False)

        router.reset()

        assert router.get_pending_count() == 0
        assert router.get_risk_status() is True
        assert len(router.get_last_orders()) == 0

    def test_reset_clears_open_spread_margin(self):
        """Reset must clear spread margin reservations to avoid ghost margin."""
        router = PortfolioRouter()
        router.register_spread_margin("LONG_LEG", 1200.0, short_symbol="SHORT_LEG")
        assert router.get_reserved_spread_margin() == 1200.0

        router.reset()
        assert router.get_reserved_spread_margin() == 0.0


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


class TestExitPreclearBypass:
    """Tests for router exit preclear time-critical bypass logic."""

    def test_dynamic_intraday_time_exit_reason_triggers_bypass(self):
        router = PortfolioRouter()
        order = OrderIntent(
            symbol="QQQ 260130C00500000",
            quantity=1,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            urgency=Urgency.IMMEDIATE,
            reason="INTRADAY_TIME_EXIT_1300 lane close",
            target_weight=0.0,
            current_weight=0.0,
        )
        router._is_option_close_order = MagicMock(return_value=True)
        router._get_open_orders_for_symbols = MagicMock(side_effect=[[object()], [object()]])
        router._cancel_open_orders_for_symbols = MagicMock(return_value=(1, 0))

        ok, detail = router._run_option_exit_preclear(order)

        assert ok is True
        assert "EXIT_PRE_CLEAR_BYPASS" in detail

    def test_preclear_defers_when_close_order_already_inflight(self):
        """If only close-side open orders exist, preclear should defer without cancel churn."""
        router = PortfolioRouter()
        order = OrderIntent(
            symbol="QQQ 260130C00500000",
            quantity=1,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            urgency=Urgency.IMMEDIATE,
            reason="TEST_CLOSE",
            target_weight=0.0,
            current_weight=0.0,
        )
        close_order = MagicMock()
        close_order.Symbol = "QQQ 260130C00500000"
        close_order.Quantity = -1
        close_order.Id = 101

        router._is_option_close_order = MagicMock(return_value=True)
        router._get_live_option_qty = MagicMock(return_value=1)
        router._get_open_orders_for_symbols = MagicMock(return_value=[close_order])
        router._cancel_open_orders_for_symbols = MagicMock(return_value=(0, 0))

        ok, detail = router._run_option_exit_preclear(order)

        assert ok is False
        assert "EXIT_PRE_CLEAR_INFLIGHT_CLOSE" in detail
        assert "GENERIC_CLOSE_INFLIGHT" in detail
        router._cancel_open_orders_for_symbols.assert_not_called()

    def test_preclear_cancels_only_blocking_orders_then_defers(self):
        """Mixed open orders should cancel blockers and preserve existing close intent."""
        router = PortfolioRouter()
        order = OrderIntent(
            symbol="QQQ 260130C00500000",
            quantity=1,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            urgency=Urgency.IMMEDIATE,
            reason="TEST_CLOSE",
            target_weight=0.0,
            current_weight=0.0,
        )
        close_order = MagicMock()
        close_order.Symbol = "QQQ 260130C00500000"
        close_order.Quantity = -1
        close_order.Id = 201
        blocker_order = MagicMock()
        blocker_order.Symbol = "QQQ 260130C00500000"
        blocker_order.Quantity = 1
        blocker_order.Id = 202

        router._is_option_close_order = MagicMock(return_value=True)
        router._get_live_option_qty = MagicMock(return_value=1)
        router._get_open_orders_for_symbols = MagicMock(
            side_effect=[[close_order, blocker_order], [close_order]]
        )
        router._cancel_open_orders_for_symbols = MagicMock(return_value=(1, 0))

        ok, detail = router._run_option_exit_preclear(order)

        assert ok is False
        assert "EXIT_PRE_CLEAR_INFLIGHT_CLOSE" in detail
        assert "POST_CANCEL_CLOSE_INFLIGHT" in detail
        router._cancel_open_orders_for_symbols.assert_called_once()
        _, kwargs = router._cancel_open_orders_for_symbols.call_args
        assert "open_orders" in kwargs
        assert len(kwargs["open_orders"]) == 1
        assert int(kwargs["open_orders"][0].Id) == 202

    def test_preclear_timeout_replaces_stale_inflight_close(self):
        """After timeout, stale close-only inflight orders should be replaced."""
        algo = MagicMock()
        now = datetime(2026, 1, 5, 10, 0, 40)
        algo.Time = now
        router = PortfolioRouter(algorithm=algo)
        symbol = "QQQ 260130C00500000"
        key = router._normalize_symbol_key(symbol)
        router._exit_preclear_pending_since[key] = now - timedelta(seconds=40)

        order = OrderIntent(
            symbol=symbol,
            quantity=1,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            urgency=Urgency.IMMEDIATE,
            reason="TEST_CLOSE",
            target_weight=0.0,
            current_weight=0.0,
        )
        close_order = MagicMock()
        close_order.Symbol = symbol
        close_order.Quantity = -1
        close_order.Id = 301

        router._is_option_close_order = MagicMock(return_value=True)
        router._get_live_option_qty = MagicMock(return_value=1)
        router._get_open_orders_for_symbols = MagicMock(side_effect=[[close_order], []])
        router._cancel_open_orders_for_symbols = MagicMock(return_value=(1, 0))

        ok, detail = router._run_option_exit_preclear(order)

        assert ok is True
        assert "EXIT_PRE_CLEAR_TIMEOUT_REPLACED_INFLIGHT_CLOSE" in detail
        router._cancel_open_orders_for_symbols.assert_called_once()

    def test_preclear_timeout_cancel_still_pending_when_inflight_remains(self):
        """If stale inflight close remains after cancel, stay deferred and avoid duplicate submit."""
        algo = MagicMock()
        now = datetime(2026, 1, 5, 10, 0, 40)
        algo.Time = now
        router = PortfolioRouter(algorithm=algo)
        symbol = "QQQ 260130C00500000"
        key = router._normalize_symbol_key(symbol)
        router._exit_preclear_pending_since[key] = now - timedelta(seconds=40)

        order = OrderIntent(
            symbol=symbol,
            quantity=1,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            urgency=Urgency.IMMEDIATE,
            reason="TEST_CLOSE",
            target_weight=0.0,
            current_weight=0.0,
        )
        close_order = MagicMock()
        close_order.Symbol = symbol
        close_order.Quantity = -1
        close_order.Id = 302

        router._is_option_close_order = MagicMock(return_value=True)
        router._get_live_option_qty = MagicMock(return_value=1)
        router._get_open_orders_for_symbols = MagicMock(side_effect=[[close_order], [close_order]])
        router._cancel_open_orders_for_symbols = MagicMock(return_value=(0, 0))

        ok, detail = router._run_option_exit_preclear(order)

        assert ok is False
        assert "EXIT_PRE_CLEAR_PENDING" in detail
        assert "INFLIGHT_TIMEOUT_CANCEL" in detail
        router._cancel_open_orders_for_symbols.assert_called_once()


class TestRouterSymbolNormalization:
    """Tests for router symbol key normalization consistency."""

    def test_normalize_symbol_key_collapses_whitespace(self):
        router = PortfolioRouter()
        assert router._normalize_symbol_key("QQQ   260130C00500000") == "QQQ 260130C00500000"

    def test_get_open_orders_matches_symbols_with_inconsistent_spacing(self):
        mock_algo = MagicMock()
        open_order = MagicMock()
        open_order.Symbol = "QQQ   260130C00500000"
        mock_algo.Transactions.GetOpenOrders.return_value = [open_order]
        router = PortfolioRouter(algorithm=mock_algo)

        matches = router._get_open_orders_for_symbols(["QQQ 260130C00500000"])

        assert len(matches) == 1

    def test_dynamic_intraday_time_exit_metadata_triggers_bypass(self):
        router = PortfolioRouter()
        order = OrderIntent(
            symbol="QQQ 260130C00500000",
            quantity=1,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            urgency=Urgency.IMMEDIATE,
            reason="RETRY_CANCELLED_CLOSE",
            target_weight=0.0,
            current_weight=0.0,
            metadata={"intraday_exit_code": "INTRADAY_TIME_EXIT_1300"},
        )
        router._is_option_close_order = MagicMock(return_value=True)
        router._get_open_orders_for_symbols = MagicMock(side_effect=[[object()], [object()]])
        router._cancel_open_orders_for_symbols = MagicMock(return_value=(1, 0))

        ok, detail = router._run_option_exit_preclear(order)

        assert ok is True
        assert "EXIT_PRE_CLEAR_BYPASS" in detail

    def test_micro_eod_sweep_reason_triggers_bypass(self):
        router = PortfolioRouter()
        order = OrderIntent(
            symbol="QQQ 260130P00500000",
            quantity=1,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            urgency=Urgency.IMMEDIATE,
            reason="MICRO_EOD_SWEEP",
            target_weight=0.0,
            current_weight=0.0,
            metadata={"options_lane": "MICRO", "options_strategy": "PROTECTIVE_PUTS"},
            tag="MICRO:MICRO_EOD_SWEEP|trace=SIG-TEST",
        )
        router._is_option_close_order = MagicMock(return_value=True)
        router._get_open_orders_for_symbols = MagicMock(side_effect=[[object()], [object()]])
        router._cancel_open_orders_for_symbols = MagicMock(return_value=(1, 0))

        ok, detail = router._run_option_exit_preclear(order)

        assert ok is True
        assert "EXIT_PRE_CLEAR_BYPASS" in detail

    def test_intraday_stale_close_skips_inflight_dedupe_and_cancels(self):
        router = PortfolioRouter()
        order = OrderIntent(
            symbol="QQQ 260130P00500000",
            quantity=1,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            urgency=Urgency.IMMEDIATE,
            reason="PREMARKET_STALE_INTRADAY_CLOSE",
            target_weight=0.0,
            current_weight=0.0,
            metadata={"options_lane": "MICRO", "options_strategy": "PROTECTIVE_PUTS"},
            tag="MICRO:UNCLASSIFIED|trace=SIG-TEST",
        )
        inflight = MagicMock()
        inflight.Symbol = "QQQ 260130P00500000"
        inflight.Quantity = -1
        router._is_option_close_order = MagicMock(return_value=True)
        router._get_open_orders_for_symbols = MagicMock(side_effect=[[inflight], []])
        router._cancel_open_orders_for_symbols = MagicMock(return_value=(1, 0))

        ok, detail = router._run_option_exit_preclear(order)

        assert ok is True
        assert "EXIT_PRE_CLEAR_INFLIGHT_CLOSE" not in detail
        assert "EXIT_PRE_CLEAR_OK" in detail
        router._cancel_open_orders_for_symbols.assert_called_once()

    def test_vass_single_leg_close_keeps_inflight_dedupe(self):
        router = PortfolioRouter()
        order = OrderIntent(
            symbol="QQQ 260130C00500000",
            quantity=1,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            urgency=Urgency.IMMEDIATE,
            reason="TRANSITION_OPEN_DERISK",
            target_weight=0.0,
            current_weight=0.0,
            metadata={"options_lane": "VASS", "options_strategy": "BULL_CALL_DEBIT"},
            tag="VASS:UNCLASSIFIED|trace=SIG-TEST",
        )
        inflight = MagicMock()
        inflight.Symbol = "QQQ 260130C00500000"
        inflight.Quantity = -1
        router._is_option_close_order = MagicMock(return_value=True)
        router._get_open_orders_for_symbols = MagicMock(return_value=[inflight])
        router._cancel_open_orders_for_symbols = MagicMock(return_value=(0, 0))

        ok, detail = router._run_option_exit_preclear(order)

        assert ok is False
        assert "EXIT_PRE_CLEAR_INFLIGHT_CLOSE" in detail
        router._cancel_open_orders_for_symbols.assert_not_called()
