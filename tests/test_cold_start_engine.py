"""
Unit tests for Cold Start Engine.

Tests warm entry logic for days 1-5:
- Day counting and tracking
- Regime-gated entries (score > 50)
- Reduced position sizing (50%)
- Instrument selection (QLD vs SSO)
- Transition to full strategies after day 5
- Kill switch reset behavior

Spec: docs/06-cold-start-engine.md
"""

import pytest

import config
from engines.core.cold_start_engine import ColdStartEngine, ColdStartState
from models.enums import Urgency


class TestColdStartState:
    """Tests for ColdStartState dataclass."""

    def test_cold_start_state_creation(self):
        """Test ColdStartState dataclass can be created."""
        state = ColdStartState(
            is_cold_start_active=True,
            days_running=2,
            warm_entry_executed=False,
            warm_entry_symbol=None,
            full_strategies_allowed=False,
            warm_entry_allowed=True,
        )
        assert state.is_cold_start_active is True
        assert state.days_running == 2
        assert state.warm_entry_executed is False
        assert state.warm_entry_symbol is None

    def test_cold_start_state_to_dict(self):
        """Test ColdStartState serialization."""
        state = ColdStartState(
            is_cold_start_active=True,
            days_running=3,
            warm_entry_executed=True,
            warm_entry_symbol="QLD",
            full_strategies_allowed=False,
            warm_entry_allowed=False,
        )
        data = state.to_dict()
        assert data["is_cold_start_active"] is True
        assert data["days_running"] == 3
        assert data["warm_entry_executed"] is True
        assert data["warm_entry_symbol"] == "QLD"

    def test_cold_start_state_str(self):
        """Test ColdStartState string representation."""
        state = ColdStartState(
            is_cold_start_active=True,
            days_running=2,
            warm_entry_executed=False,
            warm_entry_symbol=None,
            full_strategies_allowed=False,
            warm_entry_allowed=True,
        )
        result = str(state)
        assert "COLD_START" in result
        assert "Day 2" in result
        assert "Warm=No" in result


class TestColdStartActivation:
    """Tests for cold start detection and activation."""

    def test_initial_state_is_cold_start(self):
        """Test engine starts in cold start mode."""
        engine = ColdStartEngine()
        assert engine.is_cold_start_active() is True
        assert engine.get_days_running() == 0

    def test_cold_start_active_days_1_to_4(self):
        """Test cold start is active for days 0-4."""
        engine = ColdStartEngine()
        for day in range(config.COLD_START_DAYS):
            assert engine.is_cold_start_active() is True
            engine.end_of_day_update()

    def test_cold_start_inactive_after_day_5(self):
        """Test cold start becomes inactive after 5 days."""
        engine = ColdStartEngine()
        for _ in range(config.COLD_START_DAYS):
            engine.end_of_day_update()
        assert engine.is_cold_start_active() is False
        assert engine.get_days_running() == config.COLD_START_DAYS

    def test_full_strategies_blocked_during_cold_start(self):
        """Test full strategies are blocked during cold start."""
        engine = ColdStartEngine()
        assert engine.are_full_strategies_allowed() is False

    def test_full_strategies_allowed_after_cold_start(self):
        """Test full strategies are allowed after cold start."""
        engine = ColdStartEngine()
        for _ in range(config.COLD_START_DAYS):
            engine.end_of_day_update()
        assert engine.are_full_strategies_allowed() is True


class TestDayCounter:
    """Tests for day counter behavior."""

    def test_day_increment_at_eod(self):
        """Test days_running increments at end of day."""
        engine = ColdStartEngine()
        assert engine.get_days_running() == 0

        engine.end_of_day_update()
        assert engine.get_days_running() == 1

        engine.end_of_day_update()
        assert engine.get_days_running() == 2

    def test_day_counter_resets_on_kill_switch(self):
        """Test counter resets to 0 on kill switch."""
        engine = ColdStartEngine()
        engine.end_of_day_update()
        engine.end_of_day_update()
        assert engine.get_days_running() == 2

        engine.end_of_day_update(kill_switch_triggered=True)
        assert engine.get_days_running() == 0

    def test_warm_entry_resets_on_kill_switch(self):
        """Test warm_entry_executed resets on kill switch."""
        engine = ColdStartEngine()
        engine.confirm_warm_entry("QLD")
        assert engine.has_warm_entry_executed() is True

        engine.end_of_day_update(kill_switch_triggered=True)
        assert engine.has_warm_entry_executed() is False


class TestWarmEntryConditions:
    """Tests for warm entry condition checking."""

    def _get_passing_conditions(self):
        """Get a set of conditions that should pass warm entry."""
        return {
            "regime_score": 65.0,
            "has_leveraged_position": False,
            "kill_switch_triggered": False,
            "gap_filter_triggered": False,
            "vol_shock_active": False,
            "tradeable_equity": 100_000,
            "current_hour": 10,
            "current_minute": 0,
        }

    def test_warm_entry_all_conditions_pass(self):
        """Test warm entry returns TargetWeight when all conditions pass."""
        engine = ColdStartEngine()
        conditions = self._get_passing_conditions()

        result = engine.check_warm_entry(**conditions)

        assert result is not None
        assert result.source == "COLD_START"
        assert result.urgency == Urgency.IMMEDIATE

    def test_warm_entry_blocked_not_cold_start(self):
        """Test warm entry blocked when not in cold start."""
        engine = ColdStartEngine()
        for _ in range(config.COLD_START_DAYS):
            engine.end_of_day_update()

        conditions = self._get_passing_conditions()
        result = engine.check_warm_entry(**conditions)
        assert result is None

    def test_warm_entry_blocked_already_executed(self):
        """Test warm entry blocked if already executed."""
        engine = ColdStartEngine()
        engine.confirm_warm_entry("QLD")

        conditions = self._get_passing_conditions()
        result = engine.check_warm_entry(**conditions)
        assert result is None

    def test_warm_entry_blocked_before_10am(self):
        """Test warm entry blocked before 10:00 AM."""
        engine = ColdStartEngine()
        conditions = self._get_passing_conditions()
        conditions["current_hour"] = 9
        conditions["current_minute"] = 59

        result = engine.check_warm_entry(**conditions)
        assert result is None

    def test_warm_entry_allowed_at_10am(self):
        """Test warm entry allowed at exactly 10:00 AM."""
        engine = ColdStartEngine()
        conditions = self._get_passing_conditions()
        conditions["current_hour"] = 10
        conditions["current_minute"] = 0

        result = engine.check_warm_entry(**conditions)
        assert result is not None

    def test_warm_entry_blocked_regime_50(self):
        """Test warm entry blocked when regime = 50 (not > 50)."""
        engine = ColdStartEngine()
        conditions = self._get_passing_conditions()
        conditions["regime_score"] = 50.0

        result = engine.check_warm_entry(**conditions)
        assert result is None

    def test_warm_entry_blocked_regime_below_50(self):
        """Test warm entry blocked when regime < 50."""
        engine = ColdStartEngine()
        conditions = self._get_passing_conditions()
        conditions["regime_score"] = 45.0

        result = engine.check_warm_entry(**conditions)
        assert result is None

    def test_warm_entry_allowed_regime_above_50(self):
        """Test warm entry allowed when regime > 50."""
        engine = ColdStartEngine()
        conditions = self._get_passing_conditions()
        conditions["regime_score"] = 50.1

        result = engine.check_warm_entry(**conditions)
        assert result is not None

    def test_warm_entry_blocked_leveraged_position_exists(self):
        """Test warm entry blocked when leveraged position exists."""
        engine = ColdStartEngine()
        conditions = self._get_passing_conditions()
        conditions["has_leveraged_position"] = True

        result = engine.check_warm_entry(**conditions)
        assert result is None

    def test_warm_entry_blocked_kill_switch(self):
        """Test warm entry blocked when kill switch triggered."""
        engine = ColdStartEngine()
        conditions = self._get_passing_conditions()
        conditions["kill_switch_triggered"] = True

        result = engine.check_warm_entry(**conditions)
        assert result is None

    def test_warm_entry_blocked_gap_filter(self):
        """Test warm entry blocked when gap filter triggered."""
        engine = ColdStartEngine()
        conditions = self._get_passing_conditions()
        conditions["gap_filter_triggered"] = True

        result = engine.check_warm_entry(**conditions)
        assert result is None

    def test_warm_entry_blocked_vol_shock(self):
        """Test warm entry blocked when vol shock active."""
        engine = ColdStartEngine()
        conditions = self._get_passing_conditions()
        conditions["vol_shock_active"] = True

        result = engine.check_warm_entry(**conditions)
        assert result is None


class TestInstrumentSelection:
    """Tests for instrument selection logic."""

    def _get_passing_conditions(self, regime_score: float):
        return {
            "regime_score": regime_score,
            "has_leveraged_position": False,
            "kill_switch_triggered": False,
            "gap_filter_triggered": False,
            "vol_shock_active": False,
            "tradeable_equity": 100_000,
            "current_hour": 10,
            "current_minute": 0,
        }

    def test_qld_selected_regime_above_60(self):
        """Test QLD selected when regime > 60."""
        engine = ColdStartEngine()
        conditions = self._get_passing_conditions(65.0)

        result = engine.check_warm_entry(**conditions)

        assert result is not None
        assert result.symbol == "QLD"

    def test_sso_selected_regime_50_to_60(self):
        """Test SSO selected when regime 50-60."""
        engine = ColdStartEngine()
        conditions = self._get_passing_conditions(55.0)

        result = engine.check_warm_entry(**conditions)

        assert result is not None
        assert result.symbol == "SSO"

    def test_sso_selected_regime_exactly_60(self):
        """Test SSO selected when regime = 60 (not > 60)."""
        engine = ColdStartEngine()
        conditions = self._get_passing_conditions(60.0)

        result = engine.check_warm_entry(**conditions)

        assert result is not None
        assert result.symbol == "SSO"

    def test_qld_selected_regime_60_1(self):
        """Test QLD selected when regime = 60.1."""
        engine = ColdStartEngine()
        conditions = self._get_passing_conditions(60.1)

        result = engine.check_warm_entry(**conditions)

        assert result is not None
        assert result.symbol == "QLD"


class TestPositionSizing:
    """Tests for warm entry position sizing."""

    def _get_passing_conditions(self, equity: float):
        return {
            "regime_score": 65.0,
            "has_leveraged_position": False,
            "kill_switch_triggered": False,
            "gap_filter_triggered": False,
            "vol_shock_active": False,
            "tradeable_equity": equity,
            "current_hour": 10,
            "current_minute": 0,
        }

    def test_warm_entry_reduced_sizing(self):
        """Test warm entry uses 50% of normal position size."""
        engine = ColdStartEngine()
        conditions = self._get_passing_conditions(100_000)

        result = engine.check_warm_entry(**conditions)

        # Expected: 50% max position (SEED) × 50% warm entry = 25%
        expected_weight = config.MAX_SINGLE_POSITION_PCT["SEED"] * config.WARM_ENTRY_SIZE_MULT
        assert result is not None
        assert result.target_weight == expected_weight

    def test_warm_entry_skipped_below_minimum(self):
        """Test warm entry skipped when size < $2,000."""
        engine = ColdStartEngine()
        # Very small equity that results in < $2000 position
        conditions = self._get_passing_conditions(5_000)

        result = engine.check_warm_entry(**conditions)

        # At $5000 equity, 25% weight = $1250 < $2000 minimum
        assert result is None

    def test_warm_entry_allowed_at_minimum(self):
        """Test warm entry allowed when size >= $2,000."""
        engine = ColdStartEngine()
        # Calculate minimum equity needed
        # weight = 0.50 * 0.50 = 0.25
        # min_equity = $2000 / 0.25 = $8000
        conditions = self._get_passing_conditions(10_000)

        result = engine.check_warm_entry(**conditions)

        # At $10000 equity, 25% weight = $2500 >= $2000
        assert result is not None


class TestWarmEntryConfirmation:
    """Tests for warm entry confirmation."""

    def test_confirm_warm_entry_sets_flag(self):
        """Test confirm_warm_entry sets executed flag."""
        engine = ColdStartEngine()
        assert engine.has_warm_entry_executed() is False

        engine.confirm_warm_entry("QLD")

        assert engine.has_warm_entry_executed() is True

    def test_confirm_warm_entry_stores_symbol(self):
        """Test confirm_warm_entry stores the symbol."""
        engine = ColdStartEngine()
        engine.confirm_warm_entry("SSO")

        assert engine.get_warm_entry_symbol() == "SSO"

    def test_only_one_warm_entry_per_period(self):
        """Test only one warm entry allowed per cold start period."""
        engine = ColdStartEngine()
        conditions = {
            "regime_score": 65.0,
            "has_leveraged_position": False,
            "kill_switch_triggered": False,
            "gap_filter_triggered": False,
            "vol_shock_active": False,
            "tradeable_equity": 100_000,
            "current_hour": 10,
            "current_minute": 0,
        }

        # First warm entry should succeed
        result1 = engine.check_warm_entry(**conditions)
        assert result1 is not None
        engine.confirm_warm_entry(result1.symbol)

        # Second warm entry should be blocked
        result2 = engine.check_warm_entry(**conditions)
        assert result2 is None


class TestTargetWeightOutput:
    """Tests for TargetWeight output format."""

    def test_warm_entry_emits_target_weight(self):
        """Test warm entry emits TargetWeight, not orders."""
        engine = ColdStartEngine()
        conditions = {
            "regime_score": 65.0,
            "has_leveraged_position": False,
            "kill_switch_triggered": False,
            "gap_filter_triggered": False,
            "vol_shock_active": False,
            "tradeable_equity": 100_000,
            "current_hour": 10,
            "current_minute": 0,
        }

        result = engine.check_warm_entry(**conditions)

        from models.target_weight import TargetWeight

        assert isinstance(result, TargetWeight)

    def test_warm_entry_urgency_immediate(self):
        """Test warm entry has IMMEDIATE urgency."""
        engine = ColdStartEngine()
        conditions = {
            "regime_score": 65.0,
            "has_leveraged_position": False,
            "kill_switch_triggered": False,
            "gap_filter_triggered": False,
            "vol_shock_active": False,
            "tradeable_equity": 100_000,
            "current_hour": 10,
            "current_minute": 0,
        }

        result = engine.check_warm_entry(**conditions)

        assert result.urgency == Urgency.IMMEDIATE

    def test_warm_entry_strategy_tag(self):
        """Test warm entry has COLD_START strategy tag."""
        engine = ColdStartEngine()
        conditions = {
            "regime_score": 65.0,
            "has_leveraged_position": False,
            "kill_switch_triggered": False,
            "gap_filter_triggered": False,
            "vol_shock_active": False,
            "tradeable_equity": 100_000,
            "current_hour": 10,
            "current_minute": 0,
        }

        result = engine.check_warm_entry(**conditions)

        assert result.source == "COLD_START"

    def test_warm_entry_reason_includes_regime(self):
        """Test warm entry reason includes regime score."""
        engine = ColdStartEngine()
        conditions = {
            "regime_score": 65.0,
            "has_leveraged_position": False,
            "kill_switch_triggered": False,
            "gap_filter_triggered": False,
            "vol_shock_active": False,
            "tradeable_equity": 100_000,
            "current_hour": 10,
            "current_minute": 0,
        }

        result = engine.check_warm_entry(**conditions)

        assert "Regime=65.0" in result.reason


class TestStatePersistence:
    """Tests for state persistence."""

    def test_get_state_for_persistence(self):
        """Test state can be extracted for persistence."""
        engine = ColdStartEngine()
        engine.end_of_day_update()
        engine.end_of_day_update()
        engine.confirm_warm_entry("QLD")

        state = engine.get_state_for_persistence()

        assert state["days_running"] == 2
        assert state["warm_entry_executed"] is True
        assert state["warm_entry_symbol"] == "QLD"

    def test_restore_state(self):
        """Test state can be restored from persistence."""
        engine = ColdStartEngine()

        saved_state = {
            "days_running": 3,
            "warm_entry_executed": True,
            "warm_entry_symbol": "SSO",
        }

        engine.restore_state(saved_state)

        assert engine.get_days_running() == 3
        assert engine.has_warm_entry_executed() is True
        assert engine.get_warm_entry_symbol() == "SSO"

    def test_restore_state_defaults(self):
        """Test restore handles missing keys with defaults."""
        engine = ColdStartEngine()

        saved_state = {"days_running": 2}

        engine.restore_state(saved_state)

        assert engine.get_days_running() == 2
        assert engine.has_warm_entry_executed() is False
        assert engine.get_warm_entry_symbol() is None


class TestGetState:
    """Tests for get_state method."""

    def test_get_state_cold_start_active(self):
        """Test get_state returns correct cold start status."""
        engine = ColdStartEngine()
        state = engine.get_state()

        assert state.is_cold_start_active is True
        assert state.full_strategies_allowed is False

    def test_get_state_warm_entry_allowed(self):
        """Test get_state returns warm_entry_allowed correctly."""
        engine = ColdStartEngine()
        state = engine.get_state()

        assert state.warm_entry_allowed is True

        engine.confirm_warm_entry("QLD")
        state = engine.get_state()

        assert state.warm_entry_allowed is False


class TestLogging:
    """Tests for logging behavior."""

    def test_log_without_algorithm(self):
        """Test logging works without algorithm (testing mode)."""
        engine = ColdStartEngine(algorithm=None)
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
        engine = ColdStartEngine(algorithm=algo)

        engine.end_of_day_update()

        assert len(algo.messages) > 0
        assert "COLD_START" in algo.messages[-1]


class TestEdgeCases:
    """Tests for edge cases."""

    def test_reset_clears_all_state(self):
        """Test reset clears all state."""
        engine = ColdStartEngine()
        engine.end_of_day_update()
        engine.confirm_warm_entry("QLD")

        engine.reset()

        assert engine.get_days_running() == 0
        assert engine.has_warm_entry_executed() is False
        assert engine.get_warm_entry_symbol() is None

    def test_kill_switch_on_day_3_resets_to_day_0(self):
        """Test kill switch on day 3 resets counter to 0."""
        engine = ColdStartEngine()
        engine.end_of_day_update()  # Day 1
        engine.end_of_day_update()  # Day 2
        engine.end_of_day_update()  # Day 3

        assert engine.get_days_running() == 3

        engine.end_of_day_update(kill_switch_triggered=True)

        assert engine.get_days_running() == 0
        assert engine.is_cold_start_active() is True

    def test_warm_entry_after_kill_switch_reset(self):
        """Test warm entry is possible again after kill switch reset."""
        engine = ColdStartEngine()
        conditions = {
            "regime_score": 65.0,
            "has_leveraged_position": False,
            "kill_switch_triggered": False,
            "gap_filter_triggered": False,
            "vol_shock_active": False,
            "tradeable_equity": 100_000,
            "current_hour": 10,
            "current_minute": 0,
        }

        # Execute warm entry
        result1 = engine.check_warm_entry(**conditions)
        assert result1 is not None
        engine.confirm_warm_entry(result1.symbol)

        # Trigger kill switch
        engine.end_of_day_update(kill_switch_triggered=True)

        # Warm entry should be possible again
        result2 = engine.check_warm_entry(**conditions)
        assert result2 is not None

    def test_transition_at_exact_day_5(self):
        """Test transition happens at exactly day 5."""
        engine = ColdStartEngine()

        # Days 0-4: cold start active
        for i in range(config.COLD_START_DAYS - 1):
            engine.end_of_day_update()
            assert engine.is_cold_start_active() is True

        # Day 5 update: transition to normal
        engine.end_of_day_update()
        assert engine.get_days_running() == config.COLD_START_DAYS
        assert engine.is_cold_start_active() is False


# =============================================================================
# V2.20: WARM ENTRY REJECTION RECOVERY TESTS
# =============================================================================


class TestWarmEntryRejection:
    """V2.20: Tests for cold start warm entry rejection recovery."""

    def test_cancel_warm_entry_resets_state(self):
        """Test rejection resets warm entry to allow retry."""
        engine = ColdStartEngine(algorithm=None)
        engine._warm_entry_executed = True
        engine._warm_entry_symbol = "QLD"

        engine.cancel_warm_entry()

        assert engine._warm_entry_executed is False
        assert engine._warm_entry_symbol is None

    def test_cancel_warm_entry_when_not_executed(self):
        """Test calling cancel when no warm entry is safe (no-op)."""
        engine = ColdStartEngine(algorithm=None)
        engine.cancel_warm_entry()  # Should not raise
        assert engine._warm_entry_executed is False
        assert engine._warm_entry_symbol is None

    def test_warm_entry_retryable_after_rejection(self):
        """Test that after rejection, check_warm_entry can fire again."""
        engine = ColdStartEngine(algorithm=None)
        engine._days_running = 2  # Active cold start
        engine._warm_entry_executed = True
        engine._warm_entry_symbol = "SSO"

        # Simulate rejection
        engine.cancel_warm_entry()

        # Verify the guard in check_warm_entry would allow retry
        assert engine._warm_entry_executed is False
        assert engine.has_warm_entry_executed() is False
        # get_state should show warm_entry_allowed = True
        state = engine.get_state()
        assert state.warm_entry_allowed is True
