"""
Unit tests for Capital Engine.

V3.0: Simplified tests after removal of SEED/GROWTH phase system.

Tests lockbox and tradeable equity calculations:
- Virtual lockbox at milestones ($100k, $200k)
- Tradeable equity = Total - Lockbox
- State persistence and restoration

Spec: docs/05-capital-engine.md
"""

import pytest

import config
from engines.core.capital_engine import CapitalEngine, CapitalState


class TestCapitalState:
    """Tests for CapitalState dataclass."""

    def test_capital_state_creation(self):
        """Test CapitalState dataclass can be created with all fields."""
        state = CapitalState(
            total_equity=100_000,
            locked_amount=10_000,
            tradeable_eq=90_000,
            target_volatility=0.20,
            kill_switch_pct=0.05,
            milestones_triggered={100_000},
        )
        assert state.total_equity == 100_000
        assert state.locked_amount == 10_000
        assert state.tradeable_eq == 90_000
        assert 100_000 in state.milestones_triggered

    def test_capital_state_to_dict(self):
        """Test CapitalState serialization."""
        state = CapitalState(
            total_equity=125_000.567,
            locked_amount=12_500.123,
            tradeable_eq=112_500.444,
            target_volatility=0.20,
            kill_switch_pct=0.05,
            milestones_triggered={100_000},
        )
        data = state.to_dict()
        assert data["total_equity"] == 125000.57  # Rounded
        assert data["locked_amount"] == 12500.12
        assert data["tradeable_equity"] == 112500.44
        assert 100_000 in data["milestones_triggered"]

    def test_capital_state_str(self):
        """Test CapitalState string representation."""
        state = CapitalState(
            total_equity=100_000,
            locked_amount=10_000,
            tradeable_eq=90_000,
            target_volatility=0.20,
            kill_switch_pct=0.05,
        )
        result = str(state)
        assert "100,000" in result
        assert "10,000" in result
        assert "90,000" in result


class TestCapitalEngineBasic:
    """Tests for basic capital engine functionality."""

    def test_calculate_basic(self):
        """Test basic calculate returns correct state."""
        engine = CapitalEngine()
        state = engine.calculate(75_000)
        assert state.total_equity == 75_000
        assert state.kill_switch_pct == config.KILL_SWITCH_PCT
        assert state.target_volatility == config.TARGET_VOLATILITY


class TestLockbox:
    """Tests for virtual lockbox functionality."""

    def test_no_lockbox_below_first_milestone(self):
        """Test no lockbox triggered below $100k."""
        engine = CapitalEngine()
        state = engine.calculate(95_000)
        assert state.locked_amount == 0.0
        assert len(state.milestones_triggered) == 0

    def test_lockbox_triggers_at_100k(self):
        """Test lockbox locks 10% at $100k milestone."""
        engine = CapitalEngine()
        equity = 105_000
        state = engine.calculate(equity)

        expected_lock = equity * config.LOCKBOX_LOCK_PCT
        assert state.locked_amount == expected_lock
        assert 100_000 in state.milestones_triggered

    def test_lockbox_triggers_at_200k(self):
        """Test lockbox locks additional 10% at $200k milestone."""
        engine = CapitalEngine()

        # First milestone
        state1 = engine.calculate(105_000)
        first_lock = state1.locked_amount

        # Second milestone
        equity = 210_000
        state2 = engine.calculate(equity)

        # Should have two locks
        expected_additional = equity * config.LOCKBOX_LOCK_PCT
        expected_total = first_lock + expected_additional
        assert state2.locked_amount == expected_total
        assert 100_000 in state2.milestones_triggered
        assert 200_000 in state2.milestones_triggered

    def test_lockbox_milestone_only_triggers_once(self):
        """Test each milestone only triggers once."""
        engine = CapitalEngine()

        # Hit $100k milestone
        state1 = engine.calculate(105_000)
        first_lock = state1.locked_amount

        # Go above and come back
        engine.calculate(120_000)
        state2 = engine.calculate(105_000)

        # Lock amount should not have increased
        assert state2.locked_amount == first_lock

    def test_lockbox_preserved_on_equity_drop(self):
        """Test lockbox amount doesn't decrease when equity drops."""
        engine = CapitalEngine()

        # Trigger milestone
        state1 = engine.calculate(110_000)
        lock_amount = state1.locked_amount

        # Equity drops below milestone
        state2 = engine.calculate(90_000)

        # Lockbox preserved
        assert state2.locked_amount == lock_amount


class TestTradeableEquity:
    """Tests for tradeable equity calculations."""

    def test_tradeable_equity_no_lockbox(self):
        """Test tradeable equity equals total when no lockbox."""
        engine = CapitalEngine()
        equity = 80_000
        state = engine.calculate(equity)
        assert state.tradeable_eq == equity

    def test_tradeable_equity_with_lockbox(self):
        """Test tradeable equity excludes lockbox amount."""
        engine = CapitalEngine()
        equity = 110_000
        state = engine.calculate(equity)

        expected_lock = equity * config.LOCKBOX_LOCK_PCT
        expected_tradeable = equity - expected_lock
        assert state.tradeable_eq == expected_tradeable

    def test_tradeable_equity_multiple_milestones(self):
        """Test tradeable equity with multiple lockbox milestones."""
        engine = CapitalEngine()

        # First milestone
        engine.calculate(105_000)

        # Second milestone
        equity = 220_000
        state = engine.calculate(equity)

        # Two locks: 10% of 105k + 10% of 220k
        assert state.tradeable_eq == equity - state.locked_amount


class TestStateManagement:
    """Tests for state persistence and restoration."""

    def test_get_state_for_persistence(self):
        """Test state can be extracted for persistence."""
        engine = CapitalEngine()

        engine.calculate(110_000)  # Trigger lockbox

        state = engine.get_state_for_persistence()

        assert "locked_amount" in state
        assert "milestones_triggered" in state

    def test_restore_state(self):
        """Test state can be restored from persistence."""
        engine = CapitalEngine()

        saved_state = {
            "locked_amount": 15_000.0,
            "milestones_triggered": [100_000],
        }

        engine.restore_state(saved_state)

        assert engine.get_locked_amount() == 15_000.0
        assert 100_000 in engine._milestones_triggered

    def test_restore_state_defaults(self):
        """Test restore handles missing keys with defaults."""
        engine = CapitalEngine()

        # Empty state
        saved_state = {}

        engine.restore_state(saved_state)

        assert engine.get_locked_amount() == 0.0

    def test_reset_preserves_lockbox(self):
        """Test reset() preserves lockbox."""
        engine = CapitalEngine()

        engine.calculate(110_000)
        lock_before = engine.get_locked_amount()

        # Reset
        engine.reset()

        assert engine.get_locked_amount() == lock_before  # Preserved

    def test_reset_full_clears_lockbox(self):
        """Test reset_full() clears everything including lockbox."""
        engine = CapitalEngine()

        engine.calculate(110_000)

        # Full reset
        engine.reset_full()

        assert engine.get_locked_amount() == 0.0
        assert len(engine._milestones_triggered) == 0


class TestHelperMethods:
    """Tests for helper methods."""

    def test_get_locked_amount(self):
        """Test get_locked_amount returns correct value."""
        engine = CapitalEngine()
        assert engine.get_locked_amount() == 0.0

        engine.calculate(110_000)
        assert engine.get_locked_amount() > 0


class TestLogging:
    """Tests for logging behavior."""

    def test_log_without_algorithm(self):
        """Test logging works without algorithm (testing mode)."""
        engine = CapitalEngine(algorithm=None)
        # Should not raise
        engine.log("Test message")

    def test_log_with_mock_algorithm(self):
        """Test logging delegates to algorithm."""

        class MockAlgorithm:
            def __init__(self):
                self.messages = []

            def Log(self, message):
                self.messages.append(message)

        algo = MockAlgorithm()
        engine = CapitalEngine(algorithm=algo)

        engine.end_of_day_update(100_000)

        assert len(algo.messages) > 0
        assert "CAPITAL" in algo.messages[-1]


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_zero_equity(self):
        """Test handling of zero equity."""
        engine = CapitalEngine()
        state = engine.calculate(0)
        assert state.total_equity == 0
        assert state.tradeable_eq == 0

    def test_negative_equity(self):
        """Test handling of negative equity (theoretical)."""
        engine = CapitalEngine()
        state = engine.calculate(-1000)
        assert state.total_equity == -1000
        # Tradeable should handle negative gracefully
        assert state.tradeable_eq <= 0

    def test_very_large_equity(self):
        """Test handling of very large equity values."""
        engine = CapitalEngine()
        equity = 10_000_000
        state = engine.calculate(equity)
        assert state.total_equity == equity
        # Both milestones should be triggered
        assert len(state.milestones_triggered) == 2

    def test_milestones_triggered_in_single_jump(self):
        """Test both milestones trigger if equity jumps past both."""
        engine = CapitalEngine()
        # Jump directly to $250k (past both $100k and $200k)
        state = engine.calculate(250_000)

        assert 100_000 in state.milestones_triggered
        assert 200_000 in state.milestones_triggered
        # Lock amounts from both milestones
        assert state.locked_amount == 250_000 * config.LOCKBOX_LOCK_PCT * 2

    def test_multiple_calculate_calls_same_day(self):
        """Test multiple calculate calls don't double-count lockbox."""
        engine = CapitalEngine()

        state1 = engine.calculate(110_000)
        state2 = engine.calculate(115_000)
        state3 = engine.calculate(110_000)

        # Lockbox should only be triggered once
        assert state1.locked_amount == state2.locked_amount == state3.locked_amount
