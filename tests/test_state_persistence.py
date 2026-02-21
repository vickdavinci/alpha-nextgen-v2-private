"""
Unit tests for State Persistence.

Tests ObjectStore save/load:
- Capital state persistence
- Cold start state persistence
- Position state persistence
- Regime and risk state persistence
- State validation on load
- Reconciliation with broker positions
- Error handling for corrupt state

Spec: docs/15-state-persistence.md
"""

import json
from unittest.mock import MagicMock

import pytest

from persistence.state_manager import (
    SCHEMA_VERSION,
    PositionState,
    ReconciliationResult,
    StateKeys,
    StateManager,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def manager() -> StateManager:
    """Create StateManager without algorithm (testing mode)."""
    return StateManager(algorithm=None)


@pytest.fixture
def mock_capital_engine() -> MagicMock:
    """Create mock capital engine."""
    engine = MagicMock()
    engine.get_state_for_persistence.return_value = {
        "current_phase": "SEED",
        "days_above_threshold": 3,
        "locked_amount": 5000.0,
        "milestones_triggered": [],
    }
    return engine


@pytest.fixture
def mock_cold_start_engine() -> MagicMock:
    """Create mock cold start engine."""
    engine = MagicMock()
    engine.get_state_for_persistence.return_value = {
        "days_running": 2,
        "warm_entry_executed": True,
        "warm_entry_symbol": "QLD",
    }
    return engine


@pytest.fixture
def mock_risk_engine() -> MagicMock:
    """Create mock risk engine."""
    engine = MagicMock()
    engine.get_state_for_persistence.return_value = {
        "last_kill_date": "2024-01-15",
        "week_start_equity": 50000.0,
        "weekly_breaker_active": False,
    }
    return engine


@pytest.fixture
def mock_execution_engine() -> MagicMock:
    """Create mock execution engine."""
    engine = MagicMock()
    engine.get_state_for_persistence.return_value = {
        "order_counter": 3,
        "orders": {},
        "pending_moo_orders": [],
        "moo_fallback_queue": [],
    }
    return engine


@pytest.fixture
def mock_router() -> MagicMock:
    """Create mock portfolio router."""
    router = MagicMock()
    router.get_state_for_persistence.return_value = {
        "pending_count": 0,
        "last_order_count": 0,
        "risk_status": True,
        "open_spread_margin": {},
    }
    return router


# =============================================================================
# PositionState Tests
# =============================================================================


class TestPositionState:
    """Tests for PositionState dataclass."""

    def test_position_state_to_dict(self) -> None:
        """Test serialization to dictionary."""
        pos = PositionState(
            symbol="QLD",
            entry_price=82.50,
            entry_date="2024-01-08",
            highest_high=89.25,
            current_stop=83.25,
            strategy_tag="TREND",
            quantity=150,
        )

        data = pos.to_dict()

        assert data["symbol"] == "QLD"
        assert data["entry_price"] == 82.50
        assert data["entry_date"] == "2024-01-08"
        assert data["highest_high"] == 89.25
        assert data["current_stop"] == 83.25
        assert data["strategy_tag"] == "TREND"
        assert data["quantity"] == 150

    def test_position_state_from_dict(self) -> None:
        """Test deserialization from dictionary."""
        data = {
            "symbol": "TMF",
            "entry_price": 45.00,
            "entry_date": "2024-01-12",
            "highest_high": 45.00,
            "current_stop": None,
            "strategy_tag": "HEDGE",
            "quantity": 200,
        }

        pos = PositionState.from_dict(data)

        assert pos.symbol == "TMF"
        assert pos.entry_price == 45.00
        assert pos.current_stop is None
        assert pos.quantity == 200

    def test_position_state_roundtrip(self) -> None:
        """Test serialize and deserialize roundtrip."""
        original = PositionState(
            symbol="SSO",
            entry_price=55.00,
            entry_date="2024-01-10",
            highest_high=58.00,
            current_stop=52.00,
            strategy_tag="COLD_START",
            quantity=100,
        )

        data = original.to_dict()
        restored = PositionState.from_dict(data)

        assert restored.symbol == original.symbol
        assert restored.entry_price == original.entry_price
        assert restored.quantity == original.quantity


# =============================================================================
# Capital State Tests
# =============================================================================


class TestCapitalState:
    """Tests for capital state persistence."""

    def test_save_capital_state(
        self, manager: StateManager, mock_capital_engine: MagicMock
    ) -> None:
        """Test saving capital state."""
        result = manager.save_capital_state(mock_capital_engine)

        assert result is True
        assert StateKeys.CAPITAL in manager._mock_store

    def test_load_capital_state(
        self, manager: StateManager, mock_capital_engine: MagicMock
    ) -> None:
        """Test loading capital state."""
        # First save
        manager.save_capital_state(mock_capital_engine)

        # Then load
        result = manager.load_capital_state(mock_capital_engine)

        assert result is True
        mock_capital_engine.restore_state.assert_called_once()

    def test_load_missing_capital_state_returns_false(
        self, manager: StateManager, mock_capital_engine: MagicMock
    ) -> None:
        """Test loading when no state exists."""
        result = manager.load_capital_state(mock_capital_engine)

        assert result is False
        mock_capital_engine.restore_state.assert_not_called()


# =============================================================================
# Cold Start State Tests
# =============================================================================


class TestColdStartState:
    """Tests for cold start state persistence."""

    def test_save_coldstart_state(
        self, manager: StateManager, mock_cold_start_engine: MagicMock
    ) -> None:
        """Test saving cold start state."""
        result = manager.save_coldstart_state(mock_cold_start_engine)

        assert result is True
        assert StateKeys.COLDSTART in manager._mock_store

    def test_load_coldstart_state(
        self, manager: StateManager, mock_cold_start_engine: MagicMock
    ) -> None:
        """Test loading cold start state."""
        manager.save_coldstart_state(mock_cold_start_engine)

        result = manager.load_coldstart_state(mock_cold_start_engine)

        assert result is True
        mock_cold_start_engine.restore_state.assert_called_once()

    def test_days_running_persists(
        self, manager: StateManager, mock_cold_start_engine: MagicMock
    ) -> None:
        """Test that days_running value persists correctly."""
        mock_cold_start_engine.get_state_for_persistence.return_value = {
            "days_running": 5,
            "warm_entry_executed": False,
            "warm_entry_symbol": None,
        }

        manager.save_coldstart_state(mock_cold_start_engine)
        manager.load_coldstart_state(mock_cold_start_engine)

        call_args = mock_cold_start_engine.restore_state.call_args[0][0]
        assert call_args["days_running"] == 5


# =============================================================================
# Position State Tests
# =============================================================================


class TestPositionStatePersistence:
    """Tests for position state persistence."""

    def test_save_positions(self, manager: StateManager) -> None:
        """Test saving position state."""
        positions = {
            "QLD": PositionState(
                symbol="QLD",
                entry_price=82.50,
                entry_date="2024-01-08",
                highest_high=89.25,
                current_stop=83.25,
                strategy_tag="TREND",
                quantity=150,
            ),
        }

        result = manager.save_positions(positions)

        assert result is True
        assert StateKeys.POSITIONS in manager._mock_store

    def test_load_positions(self, manager: StateManager) -> None:
        """Test loading position state."""
        positions = {
            "QLD": PositionState(
                symbol="QLD",
                entry_price=82.50,
                entry_date="2024-01-08",
                highest_high=89.25,
                current_stop=83.25,
                strategy_tag="TREND",
                quantity=150,
            ),
        }
        manager.save_positions(positions)

        loaded = manager.load_positions()

        assert "QLD" in loaded
        assert loaded["QLD"].entry_price == 82.50
        assert loaded["QLD"].quantity == 150

    def test_add_position(self, manager: StateManager) -> None:
        """Test adding a new position."""
        manager.add_position(
            symbol="QLD",
            entry_price=85.00,
            entry_date="2024-01-15",
            strategy_tag="TREND",
            quantity=100,
        )

        pos = manager.get_position("QLD")
        assert pos is not None
        assert pos.entry_price == 85.00
        assert pos.highest_high == 85.00  # Initialized to entry price

    def test_update_position(self, manager: StateManager) -> None:
        """Test updating an existing position."""
        manager.add_position("QLD", 85.00, "2024-01-15", "TREND", 100)

        manager.update_position("QLD", highest_high=90.00, current_stop=82.00)

        pos = manager.get_position("QLD")
        assert pos is not None
        assert pos.highest_high == 90.00
        assert pos.current_stop == 82.00

    def test_remove_position(self, manager: StateManager) -> None:
        """Test removing a position."""
        manager.add_position("QLD", 85.00, "2024-01-15", "TREND", 100)
        assert manager.get_position("QLD") is not None

        manager.remove_position("QLD")

        assert manager.get_position("QLD") is None


# =============================================================================
# Risk State Tests
# =============================================================================


class TestRiskState:
    """Tests for risk state persistence."""

    def test_save_risk_state(self, manager: StateManager, mock_risk_engine: MagicMock) -> None:
        """Test saving risk state."""
        result = manager.save_risk_state(mock_risk_engine)

        assert result is True
        assert StateKeys.RISK in manager._mock_store

    def test_load_risk_state(self, manager: StateManager, mock_risk_engine: MagicMock) -> None:
        """Test loading risk state."""
        manager.save_risk_state(mock_risk_engine)

        result = manager.load_risk_state(mock_risk_engine)

        assert result is True
        mock_risk_engine.load_state.assert_called_once()


# =============================================================================
# Weekly State Tests
# =============================================================================


class TestWeeklyState:
    """Tests for weekly state persistence."""

    def test_save_weekly_state(self, manager: StateManager) -> None:
        """Test saving weekly state."""
        result = manager.save_weekly_state(
            week_start_equity=50000.0,
            week_start_date="2024-01-15",
            weekly_breaker_triggered=False,
        )

        assert result is True
        assert StateKeys.WEEKLY in manager._mock_store

    def test_load_weekly_state(self, manager: StateManager) -> None:
        """Test loading weekly state."""
        manager.save_weekly_state(50000.0, "2024-01-15", False)

        data = manager.load_weekly_state()

        assert data is not None
        assert data["week_start_equity"] == 50000.0
        assert data["week_start_date"] == "2024-01-15"
        assert data["weekly_breaker_triggered"] is False


# =============================================================================
# Validation Tests
# =============================================================================


class TestStateValidation:
    """Tests for state validation."""

    def test_validate_capital_state_valid(self, manager: StateManager) -> None:
        """Test validation of valid capital state."""
        data = {
            "current_phase": "GROWTH",
            "days_above_threshold": 5,
            "locked_amount": 10000.0,
        }

        validated = manager.validate_capital_state(data)

        assert validated["current_phase"] == "GROWTH"
        assert validated["days_above_threshold"] == 5

    def test_validate_capital_state_invalid_phase(self, manager: StateManager) -> None:
        """Test validation corrects invalid phase."""
        data = {"current_phase": "INVALID", "days_above_threshold": 5}

        validated = manager.validate_capital_state(data)

        assert validated["current_phase"] == "SEED"

    def test_validate_capital_state_negative_days(self, manager: StateManager) -> None:
        """Test validation corrects negative days."""
        data = {"current_phase": "SEED", "days_above_threshold": -5}

        validated = manager.validate_capital_state(data)

        assert validated["days_above_threshold"] == 0

    def test_validate_regime_state_clamps_score(self, manager: StateManager) -> None:
        """Test validation clamps regime score to 0-100."""
        data = {"smoothed_score": 150.0}

        validated = manager.validate_regime_state(data)

        assert validated["smoothed_score"] == 100

    def test_validate_regime_state_negative_score(self, manager: StateManager) -> None:
        """Test validation clamps negative regime score."""
        data = {"smoothed_score": -10.0}

        validated = manager.validate_regime_state(data)

        assert validated["smoothed_score"] == 0


# =============================================================================
# Position Reconciliation Tests
# =============================================================================


class TestPositionReconciliation:
    """Tests for position reconciliation with broker."""

    def test_reconcile_matching_positions(self, manager: StateManager) -> None:
        """Test reconciliation when positions match."""
        manager.add_position("QLD", 85.00, "2024-01-15", "TREND", 100)

        result = manager.reconcile_positions({"QLD": 100})

        assert "QLD" in result.matched
        assert not result.has_issues()

    def test_reconcile_quantity_mismatch(self, manager: StateManager) -> None:
        """Test reconciliation detects quantity mismatch."""
        manager.add_position("QLD", 85.00, "2024-01-15", "TREND", 100)

        result = manager.reconcile_positions({"QLD": 150})

        assert "QLD" in result.quantity_mismatches
        assert result.has_issues()
        # Should update to broker quantity
        assert manager.get_position("QLD").quantity == 150

    def test_reconcile_closed_position(self, manager: StateManager) -> None:
        """Test reconciliation detects closed positions."""
        manager.add_position("QLD", 85.00, "2024-01-15", "TREND", 100)

        result = manager.reconcile_positions({})  # No positions at broker

        assert "QLD" in result.closed_positions
        assert result.has_issues()
        # Should remove from tracking
        assert manager.get_position("QLD") is None

    def test_reconcile_unexpected_position(self, manager: StateManager) -> None:
        """Test reconciliation detects unexpected positions."""
        # No persisted positions

        result = manager.reconcile_positions({"SSO": 50})

        assert "SSO" in result.unexpected_positions
        assert result.has_issues()
        # Should add minimal tracking
        pos = manager.get_position("SSO")
        assert pos is not None
        assert pos.quantity == 50
        assert pos.strategy_tag == "UNKNOWN"


# =============================================================================
# Error Handling Tests
# =============================================================================


class TestErrorHandling:
    """Tests for error handling."""

    def test_missing_state_uses_defaults(self, manager: StateManager) -> None:
        """Test that missing state returns None (caller uses defaults)."""
        data = manager._load_state(StateKeys.CAPITAL)

        assert data is None

    def test_corrupted_state_returns_none(self, manager: StateManager) -> None:
        """Test that corrupted JSON returns None."""
        manager._mock_store[StateKeys.CAPITAL] = "not valid json {"

        data = manager._load_state(StateKeys.CAPITAL)

        assert data is None

    def test_lockbox_persists_across_restarts(
        self, manager: StateManager, mock_capital_engine: MagicMock
    ) -> None:
        """Test that lockbox amount persists correctly."""
        mock_capital_engine.get_state_for_persistence.return_value = {
            "current_phase": "GROWTH",
            "days_above_threshold": 0,
            "locked_amount": 15000.0,
            "milestones_triggered": ["100000"],
        }

        manager.save_capital_state(mock_capital_engine)
        manager.load_capital_state(mock_capital_engine)

        call_args = mock_capital_engine.restore_state.call_args[0][0]
        assert call_args["locked_amount"] == 15000.0


# =============================================================================
# Bulk Operations Tests
# =============================================================================


class TestBulkOperations:
    """Tests for bulk save/load operations."""

    def test_save_all(
        self,
        manager: StateManager,
        mock_capital_engine: MagicMock,
        mock_cold_start_engine: MagicMock,
        mock_risk_engine: MagicMock,
        mock_execution_engine: MagicMock,
        mock_router: MagicMock,
    ) -> None:
        """Test saving all state categories."""
        manager.add_position("QLD", 85.00, "2024-01-15", "TREND", 100)

        saved = manager.save_all(
            capital_engine=mock_capital_engine,
            cold_start_engine=mock_cold_start_engine,
            risk_engine=mock_risk_engine,
            execution_engine=mock_execution_engine,
            router=mock_router,
        )

        assert saved >= 3  # At least capital, coldstart, risk

    def test_load_all(
        self,
        manager: StateManager,
        mock_capital_engine: MagicMock,
        mock_cold_start_engine: MagicMock,
        mock_risk_engine: MagicMock,
        mock_execution_engine: MagicMock,
        mock_router: MagicMock,
    ) -> None:
        """Test loading all state categories."""
        # First save
        manager.save_all(
            capital_engine=mock_capital_engine,
            cold_start_engine=mock_cold_start_engine,
            risk_engine=mock_risk_engine,
            execution_engine=mock_execution_engine,
            router=mock_router,
        )

        # Then load
        loaded = manager.load_all(
            capital_engine=mock_capital_engine,
            cold_start_engine=mock_cold_start_engine,
            risk_engine=mock_risk_engine,
            execution_engine=mock_execution_engine,
            router=mock_router,
        )

        assert loaded >= 2  # At least capital, coldstart loaded

    def test_load_all_restores_execution_and_router(
        self,
        manager: StateManager,
        mock_execution_engine: MagicMock,
        mock_router: MagicMock,
    ) -> None:
        """Execution and router state payloads should be restored through load_all."""
        manager.save_all(execution_engine=mock_execution_engine, router=mock_router)

        manager.load_all(execution_engine=mock_execution_engine, router=mock_router)

        mock_execution_engine.restore_state.assert_called_once()
        mock_router.restore_state.assert_called_once()


# =============================================================================
# Reset Operations Tests
# =============================================================================


class TestResetOperations:
    """Tests for reset operations."""

    def test_reset_all(self, manager: StateManager, mock_capital_engine: MagicMock) -> None:
        """Test resetting all state."""
        manager.save_capital_state(mock_capital_engine)
        manager.add_position("QLD", 85.00, "2024-01-15", "TREND", 100)

        manager.reset_all()

        assert len(manager._mock_store) == 0
        assert len(manager._positions) == 0

    def test_reset_category(self, manager: StateManager, mock_capital_engine: MagicMock) -> None:
        """Test resetting a specific category."""
        manager.save_capital_state(mock_capital_engine)
        manager.add_position("QLD", 85.00, "2024-01-15", "TREND", 100)
        manager.save_positions(manager._positions)

        manager.reset_category(StateKeys.CAPITAL)

        assert StateKeys.CAPITAL not in manager._mock_store
        assert StateKeys.POSITIONS in manager._mock_store


# =============================================================================
# Schema Version Tests
# =============================================================================


class TestSchemaVersion:
    """Tests for schema versioning."""

    def test_state_includes_version(self, manager: StateManager) -> None:
        """Test that saved state includes version."""
        manager._save_state(StateKeys.CAPITAL, {"test": "data"})

        raw = manager._mock_store[StateKeys.CAPITAL]
        parsed = json.loads(raw)

        assert "version" in parsed
        assert parsed["version"] == SCHEMA_VERSION

    def test_load_detects_version_mismatch(self, manager: StateManager) -> None:
        """Test that version mismatch is detected (but still loads)."""
        # Save with different version
        wrapped = {"version": "0.9", "data": {"test": "data"}}
        manager._mock_store[StateKeys.CAPITAL] = json.dumps(wrapped)

        # Should still load (migration would happen here)
        data = manager._load_state(StateKeys.CAPITAL)

        assert data is not None
        assert data["test"] == "data"


# =============================================================================
# Statistics Tests
# =============================================================================


class TestStatistics:
    """Tests for state manager statistics."""

    def test_get_statistics(self, manager: StateManager) -> None:
        """Test getting statistics."""
        manager.add_position("QLD", 85.00, "2024-01-15", "TREND", 100)
        manager.add_position("SSO", 55.00, "2024-01-15", "TREND", 50)

        stats = manager.get_statistics()

        assert stats["position_count"] == 2
        assert "QLD" in stats["positions"]
        assert "SSO" in stats["positions"]
