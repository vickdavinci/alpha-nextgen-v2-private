"""
Tests for OCO Order Manager - One-Cancels-Other order pair management.

Tests cover:
- OCO pair creation
- Atomic submission (both legs)
- Order fill handling (cancel other leg)
- Manual cancellation
- State persistence
- Edge cases

Spec: docs/v2-specs/V2-1-FINAL-SYNTHESIS.md (Modification #3)
"""

import pytest

from execution.oco_manager import (
    OCOLeg,
    OCOManager,
    OCOOrderType,
    OCOPair,
    OCOState,
)


# =============================================================================
# FIXTURES
# =============================================================================


@pytest.fixture
def manager():
    """Create an OCOManager instance for testing."""
    return OCOManager(algorithm=None)


@pytest.fixture
def sample_pair(manager):
    """Create a sample OCO pair."""
    return manager.create_oco_pair(
        symbol="QQQ 260126C00450000",
        entry_price=1.45,
        stop_price=1.09,  # -25%
        target_price=2.175,  # +50%
        quantity=27,
        current_date="2026-01-26",
    )


@pytest.fixture
def active_pair(manager, sample_pair):
    """Create and activate an OCO pair."""
    manager.submit_oco_pair(sample_pair, "10:30:00")
    return sample_pair


# =============================================================================
# OCO LEG TESTS
# =============================================================================


class TestOCOLeg:
    """Tests for OCOLeg dataclass."""

    def test_stop_leg_creation(self):
        """Test stop leg creation."""
        leg = OCOLeg(
            leg_type=OCOOrderType.STOP,
            trigger_price=1.09,
            quantity=-27,
        )
        assert leg.leg_type == OCOOrderType.STOP
        assert leg.trigger_price == 1.09
        assert leg.quantity == -27
        assert not leg.submitted
        assert not leg.filled

    def test_profit_leg_creation(self):
        """Test profit leg creation."""
        leg = OCOLeg(
            leg_type=OCOOrderType.PROFIT,
            trigger_price=2.175,
            quantity=-27,
        )
        assert leg.leg_type == OCOOrderType.PROFIT
        assert leg.trigger_price == 2.175

    def test_to_dict(self):
        """Test serialization."""
        leg = OCOLeg(
            leg_type=OCOOrderType.STOP,
            trigger_price=1.09,
            quantity=-27,
            broker_order_id=12345,
            submitted=True,
        )
        data = leg.to_dict()
        assert data["leg_type"] == "STOP"
        assert data["trigger_price"] == 1.09
        assert data["broker_order_id"] == 12345

    def test_from_dict(self):
        """Test deserialization."""
        data = {
            "leg_type": "PROFIT",
            "trigger_price": 2.175,
            "quantity": -27,
            "broker_order_id": 12346,
            "submitted": True,
            "filled": False,
            "cancelled": False,
            "fill_price": 0.0,
            "fill_time": None,
        }
        leg = OCOLeg.from_dict(data)
        assert leg.leg_type == OCOOrderType.PROFIT
        assert leg.trigger_price == 2.175
        assert leg.broker_order_id == 12346


# =============================================================================
# OCO PAIR TESTS
# =============================================================================


class TestOCOPair:
    """Tests for OCOPair dataclass."""

    def test_pair_creation(self, sample_pair):
        """Test OCO pair creation."""
        assert sample_pair.symbol == "QQQ 260126C00450000"
        assert sample_pair.entry_price == 1.45
        assert sample_pair.state == OCOState.PENDING
        assert sample_pair.stop_leg.trigger_price == 1.09
        assert sample_pair.profit_leg.trigger_price == 2.175

    def test_pair_id_format(self, sample_pair):
        """Test OCO ID format."""
        assert sample_pair.oco_id.startswith("OCO-20260126-")

    def test_to_dict(self, sample_pair):
        """Test pair serialization."""
        data = sample_pair.to_dict()
        assert data["oco_id"] == sample_pair.oco_id
        assert data["symbol"] == "QQQ 260126C00450000"
        assert data["state"] == "PENDING"
        assert "stop_leg" in data
        assert "profit_leg" in data

    def test_from_dict(self, sample_pair):
        """Test pair deserialization."""
        data = sample_pair.to_dict()
        restored = OCOPair.from_dict(data)
        assert restored.oco_id == sample_pair.oco_id
        assert restored.symbol == sample_pair.symbol
        assert restored.state == sample_pair.state


# =============================================================================
# OCO MANAGER CREATION TESTS
# =============================================================================


class TestOCOCreation:
    """Tests for OCO pair creation."""

    def test_create_oco_pair(self, manager):
        """Test basic OCO pair creation."""
        pair = manager.create_oco_pair(
            symbol="QQQ 260126C00450000",
            entry_price=1.45,
            stop_price=1.09,
            target_price=2.175,
            quantity=27,
            current_date="2026-01-26",
        )
        assert pair.state == OCOState.PENDING
        assert pair.stop_leg.quantity == -27  # Sell to close
        assert pair.profit_leg.quantity == -27

    def test_unique_oco_ids(self, manager):
        """Test OCO IDs are unique."""
        pair1 = manager.create_oco_pair(
            symbol="QQQ 260126C00450000",
            entry_price=1.45,
            stop_price=1.09,
            target_price=2.175,
            quantity=27,
            current_date="2026-01-26",
        )
        pair2 = manager.create_oco_pair(
            symbol="QQQ 260126P00440000",
            entry_price=1.20,
            stop_price=0.96,
            target_price=1.80,
            quantity=20,
            current_date="2026-01-26",
        )
        assert pair1.oco_id != pair2.oco_id


# =============================================================================
# OCO SUBMISSION TESTS
# =============================================================================


class TestOCOSubmission:
    """Tests for OCO pair submission."""

    def test_submit_oco_pair(self, manager, sample_pair):
        """Test successful OCO submission."""
        result = manager.submit_oco_pair(sample_pair, "10:30:00")
        assert result is True
        assert sample_pair.state == OCOState.ACTIVE
        assert sample_pair.stop_leg.submitted
        assert sample_pair.profit_leg.submitted
        assert sample_pair.stop_leg.broker_order_id is not None
        assert sample_pair.profit_leg.broker_order_id is not None

    def test_submit_updates_tracking(self, manager, sample_pair):
        """Test submission updates internal tracking."""
        manager.submit_oco_pair(sample_pair, "10:30:00")
        assert sample_pair.oco_id in manager._active_pairs
        assert sample_pair.symbol in manager._symbol_to_oco
        assert sample_pair.stop_leg.broker_order_id in manager._order_to_oco
        assert sample_pair.profit_leg.broker_order_id in manager._order_to_oco

    def test_cannot_submit_twice(self, manager, sample_pair):
        """Test cannot submit already active pair."""
        manager.submit_oco_pair(sample_pair, "10:30:00")
        result = manager.submit_oco_pair(sample_pair, "10:31:00")
        assert result is False

    def test_has_active_pair(self, manager, sample_pair):
        """Test has_active_pair check."""
        assert not manager.has_active_pair(sample_pair.symbol)
        manager.submit_oco_pair(sample_pair, "10:30:00")
        assert manager.has_active_pair(sample_pair.symbol)


# =============================================================================
# ORDER FILL HANDLING TESTS
# =============================================================================


class TestOrderFillHandling:
    """Tests for order fill handling (One-Cancels-Other)."""

    def test_stop_fill_cancels_profit(self, manager, active_pair):
        """Test stop fill cancels profit order."""
        stop_order_id = active_pair.stop_leg.broker_order_id

        result = manager.on_order_fill(
            broker_order_id=stop_order_id,
            fill_price=1.05,
            fill_quantity=-27,
            fill_time="11:00:00",
        )

        assert result is not None
        assert result.state == OCOState.CLOSED
        assert result.stop_leg.filled
        assert result.profit_leg.cancelled
        assert "STOP_HIT" in result.close_reason

    def test_profit_fill_cancels_stop(self, manager, active_pair):
        """Test profit fill cancels stop order."""
        profit_order_id = active_pair.profit_leg.broker_order_id

        result = manager.on_order_fill(
            broker_order_id=profit_order_id,
            fill_price=2.20,
            fill_quantity=-27,
            fill_time="11:00:00",
        )

        assert result is not None
        assert result.state == OCOState.CLOSED
        assert result.profit_leg.filled
        assert result.stop_leg.cancelled
        assert "PROFIT_HIT" in result.close_reason

    def test_fill_removes_from_tracking(self, manager, active_pair):
        """Test fill removes pair from active tracking."""
        stop_order_id = active_pair.stop_leg.broker_order_id

        manager.on_order_fill(
            broker_order_id=stop_order_id,
            fill_price=1.05,
            fill_quantity=-27,
            fill_time="11:00:00",
        )

        assert active_pair.oco_id not in manager._active_pairs
        assert active_pair.symbol not in manager._symbol_to_oco

    def test_unknown_order_returns_none(self, manager, active_pair):
        """Test unknown order ID returns None."""
        result = manager.on_order_fill(
            broker_order_id=99999,
            fill_price=1.50,
            fill_quantity=-27,
            fill_time="11:00:00",
        )
        assert result is None


# =============================================================================
# CANCELLATION TESTS
# =============================================================================


class TestOCOCancellation:
    """Tests for OCO pair cancellation."""

    def test_cancel_by_oco_id(self, manager, active_pair):
        """Test cancel by OCO ID."""
        result = manager.cancel_oco_pair(active_pair.oco_id, "MANUAL")
        assert result is True
        assert active_pair.state == OCOState.CANCELLED
        assert active_pair.stop_leg.cancelled
        assert active_pair.profit_leg.cancelled

    def test_cancel_by_symbol(self, manager, active_pair):
        """Test cancel by symbol."""
        result = manager.cancel_by_symbol(active_pair.symbol, "POSITION_CLOSED")
        assert result is True
        assert active_pair.state == OCOState.CANCELLED

    def test_cancel_removes_from_tracking(self, manager, active_pair):
        """Test cancel removes from tracking."""
        manager.cancel_oco_pair(active_pair.oco_id, "MANUAL")
        assert active_pair.oco_id not in manager._active_pairs
        assert active_pair.symbol not in manager._symbol_to_oco

    def test_cancel_unknown_returns_false(self, manager):
        """Test cancel unknown OCO returns False."""
        result = manager.cancel_oco_pair("OCO-99999999-999", "MANUAL")
        assert result is False

    def test_cancel_unknown_symbol_returns_false(self, manager):
        """Test cancel unknown symbol returns False."""
        result = manager.cancel_by_symbol("UNKNOWN_SYMBOL", "MANUAL")
        assert result is False


# =============================================================================
# QUERY TESTS
# =============================================================================


class TestOCOQueries:
    """Tests for OCO pair queries."""

    def test_get_active_pair(self, manager, active_pair):
        """Test get active pair by symbol."""
        pair = manager.get_active_pair(active_pair.symbol)
        assert pair is not None
        assert pair.oco_id == active_pair.oco_id

    def test_get_active_pair_not_found(self, manager):
        """Test get active pair when none exists."""
        pair = manager.get_active_pair("UNKNOWN_SYMBOL")
        assert pair is None

    def test_get_all_active_pairs(self, manager):
        """Test get all active pairs."""
        pair1 = manager.create_oco_pair(
            "QQQ 260126C00450000", 1.45, 1.09, 2.175, 27, "2026-01-26"
        )
        pair2 = manager.create_oco_pair(
            "QQQ 260126P00440000", 1.20, 0.96, 1.80, 20, "2026-01-26"
        )
        manager.submit_oco_pair(pair1, "10:30:00")
        manager.submit_oco_pair(pair2, "10:31:00")

        pairs = manager.get_all_active_pairs()
        assert len(pairs) == 2


# =============================================================================
# STATE PERSISTENCE TESTS
# =============================================================================


class TestOCOPersistence:
    """Tests for OCO state persistence."""

    def test_get_state_empty(self, manager):
        """Test state when no active pairs."""
        state = manager.get_state_for_persistence()
        assert state["active_pairs"] == {}
        assert state["next_oco_number"] == 1

    def test_get_state_with_pairs(self, manager, active_pair):
        """Test state with active pairs."""
        state = manager.get_state_for_persistence()
        assert active_pair.oco_id in state["active_pairs"]
        assert state["next_oco_number"] > 1

    def test_restore_state(self, manager, active_pair):
        """Test state restoration."""
        state = manager.get_state_for_persistence()

        new_manager = OCOManager()
        new_manager.restore_state(state)

        assert active_pair.oco_id in new_manager._active_pairs
        assert active_pair.symbol in new_manager._symbol_to_oco

    def test_reset(self, manager, active_pair):
        """Test reset clears all state."""
        manager.reset()
        assert len(manager._active_pairs) == 0
        assert len(manager._symbol_to_oco) == 0
        assert manager._next_oco_number == 1


# =============================================================================
# EDGE CASE TESTS
# =============================================================================


class TestOCOEdgeCases:
    """Tests for edge cases."""

    def test_multiple_pairs_different_symbols(self, manager):
        """Test multiple OCO pairs for different symbols."""
        pair1 = manager.create_oco_pair(
            "QQQ 260126C00450000", 1.45, 1.09, 2.175, 27, "2026-01-26"
        )
        pair2 = manager.create_oco_pair(
            "QQQ 260126C00460000", 2.00, 1.50, 3.00, 20, "2026-01-26"
        )

        manager.submit_oco_pair(pair1, "10:30:00")
        manager.submit_oco_pair(pair2, "10:31:00")

        # Fill one, should only affect that pair
        manager.on_order_fill(
            pair1.stop_leg.broker_order_id,
            fill_price=1.05,
            fill_quantity=-27,
            fill_time="11:00:00",
        )

        assert pair1.state == OCOState.CLOSED
        assert pair2.state == OCOState.ACTIVE

    def test_fill_price_recorded(self, manager, active_pair):
        """Test fill price is recorded correctly."""
        stop_order_id = active_pair.stop_leg.broker_order_id

        manager.on_order_fill(
            broker_order_id=stop_order_id,
            fill_price=1.05,
            fill_quantity=-27,
            fill_time="11:00:00",
        )

        assert active_pair.stop_leg.fill_price == 1.05
        assert active_pair.stop_leg.fill_time == "11:00:00"

    def test_cannot_cancel_closed_pair(self, manager, active_pair):
        """Test cannot cancel already closed pair."""
        # First close via fill
        manager.on_order_fill(
            active_pair.stop_leg.broker_order_id,
            fill_price=1.05,
            fill_quantity=-27,
            fill_time="11:00:00",
        )

        # Try to cancel - should fail since already closed
        result = manager.cancel_oco_pair(active_pair.oco_id, "MANUAL")
        assert result is False
