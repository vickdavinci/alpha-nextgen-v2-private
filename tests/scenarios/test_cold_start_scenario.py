"""
Scenario tests for Cold Start behavior.

Tests the complete cold start workflow (days 1-5):
1. Day 1: Algorithm starts, no positions
2. Days 1-5: Warm entry with 50% sizing
3. Day 6: Transition to full strategies

Spec: docs/06-cold-start-engine.md
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime

from engines.core.cold_start_engine import ColdStartEngine
from engines.core.capital_engine import CapitalEngine
from models.target_weight import TargetWeight
from models.enums import Urgency
import config


@pytest.fixture
def mock_algorithm():
    """Create mock algorithm for cold start tests."""
    algo = MagicMock()
    algo.Time = datetime(2024, 1, 15, 10, 0)
    algo.Log = MagicMock()
    algo.Debug = MagicMock()
    algo.Portfolio = MagicMock()
    algo.Portfolio.TotalPortfolioValue = 50000.0
    algo.Portfolio.Invested = False
    return algo


@pytest.fixture
def cold_start_engine(mock_algorithm):
    """Create cold start engine instance."""
    engine = ColdStartEngine(mock_algorithm)
    return engine


class TestColdStartScenario:
    """
    End-to-end scenario tests for Cold Start.

    These tests simulate the first 5 days of algorithm deployment
    and verify the gradual warm-up process works correctly.
    """

    @pytest.mark.scenario
    def test_scenario_cold_start_day_1_no_entry(self, cold_start_engine, mock_algorithm):
        """
        SCENARIO: Day 1 with low regime, no entry.

        Given: Day 1, regime score = 45 (below 50 threshold)
        When: Market opens
        Then: No entry signal generated
        And: Portfolio remains in cash
        """
        # Setup: Day 1, low regime
        cold_start_engine._days_running = 1
        regime_score = 45.0  # Below COLD_START_ENTRY_REGIME (50)

        # Mock portfolio state - no leveraged positions
        mock_algorithm.Portfolio.__getitem__ = MagicMock(
            return_value=MagicMock(Invested=False)
        )

        # Act: Check warm entry
        signal = cold_start_engine.check_warm_entry(
            regime_score=regime_score,
            has_leveraged_position=False,
            kill_switch_triggered=False,
            gap_filter_triggered=False,
            vol_shock_active=False,
            tradeable_equity=50000.0,
            current_hour=10,
            current_minute=0,
        )

        # Assert: No signal due to low regime
        assert signal is None

    @pytest.mark.scenario
    def test_scenario_cold_start_day_2_entry(self, cold_start_engine, mock_algorithm):
        """
        SCENARIO: Day 2 with good regime, warm entry.

        Given: Day 2, regime score = 60
        When: 10:00 AM warm entry check
        Then: Entry signal generated for QLD or SSO
        And: Position size uses cold start sizing
        """
        # Setup: Day 2, favorable regime
        cold_start_engine._days_running = 2
        regime_score = 60.0  # Above COLD_START_ENTRY_REGIME (50)

        # Act: Check warm entry at 10:00 AM
        signal = cold_start_engine.check_warm_entry(
            regime_score=regime_score,
            has_leveraged_position=False,
            kill_switch_triggered=False,
            gap_filter_triggered=False,
            vol_shock_active=False,
            tradeable_equity=50000.0,
            current_hour=10,
            current_minute=0,
        )

        # Assert: Entry signal generated
        assert signal is not None
        assert isinstance(signal, TargetWeight)
        assert signal.source == "COLD_START"
        assert signal.symbol in ["QLD", "SSO"]
        # Cold start uses reduced sizing (actual value depends on config)
        assert signal.target_weight > 0

    @pytest.mark.scenario
    def test_scenario_cold_start_day_increment(self, cold_start_engine):
        """
        SCENARIO: days_running increments correctly.

        Given: days_running = 3
        When: End of trading day
        Then: days_running = 4
        And: State is persisted
        """
        # Setup: Day 3
        cold_start_engine._days_running = 3

        # Act: End of day update
        cold_start_engine.end_of_day_update(kill_switch_triggered=False)

        # Assert: Day incremented
        assert cold_start_engine._days_running == 4

        # Verify state can be persisted
        state = cold_start_engine.get_state_for_persistence()
        assert state["days_running"] == 4

    @pytest.mark.scenario
    def test_scenario_cold_start_transition_day_5(self, cold_start_engine):
        """
        SCENARIO: Transition to full strategies after day 4.

        Given: days_running = 4 (last day of cold start)
        When: End of day 4
        Then: days_running = 5
        And: Cold start ends, full strategies activate

        Note: Cold start active when days_running < COLD_START_DAYS (5)
        So days 0-4 are cold start, day 5+ is full strategies.
        """
        # Setup: Day 4 (last day of cold start)
        cold_start_engine._days_running = 4

        # Verify cold start is still active on day 4 (4 < 5)
        assert cold_start_engine.is_cold_start_active() is True

        # Act: End of day update
        cold_start_engine.end_of_day_update(kill_switch_triggered=False)

        # Assert: Transition to day 5
        assert cold_start_engine._days_running == 5

        # Cold start no longer active on day 5 (5 < 5 is False)
        assert cold_start_engine.is_cold_start_active() is False

    @pytest.mark.scenario
    def test_scenario_cold_start_instruments_qld_sso_only(self, cold_start_engine):
        """
        SCENARIO: Cold start only trades QLD and SSO.

        Given: Cold start active (day 3)
        When: Regime is favorable
        Then: Only QLD or SSO positions opened
        And: No TQQQ, SOXL, TMF, PSQ positions
        """
        # Setup: Day 3, favorable conditions
        cold_start_engine._days_running = 3

        # Act: Check warm entry
        signal = cold_start_engine.check_warm_entry(
            regime_score=65.0,
            has_leveraged_position=False,
            kill_switch_triggered=False,
            gap_filter_triggered=False,
            vol_shock_active=False,
            tradeable_equity=50000.0,
            current_hour=10,
            current_minute=0,
        )

        # Assert: Only QLD or SSO
        assert signal is not None
        assert signal.symbol in ["QLD", "SSO"]
        # Verify NOT in excluded symbols
        assert signal.symbol not in ["TQQQ", "SOXL", "TMF", "PSQ"]

    @pytest.mark.scenario
    def test_scenario_cold_start_persists_across_restart(self, cold_start_engine):
        """
        SCENARIO: Cold start state persists across restart.

        Given: days_running = 3, warm entry executed
        When: Algorithm restarts
        Then: days_running still = 3
        And: Warm entry state recognized
        """
        # Setup: Day 3 with warm entry executed
        cold_start_engine._days_running = 3
        cold_start_engine._warm_entry_executed = True

        # Act: Persist state
        state = cold_start_engine.get_state_for_persistence()

        # Simulate restart - create new engine
        new_engine = ColdStartEngine(MagicMock())
        new_engine.restore_state(state)

        # Assert: State restored correctly
        assert new_engine._days_running == 3
        assert new_engine._warm_entry_executed is True
        assert new_engine.is_cold_start_active() is True

    @pytest.mark.scenario
    def test_scenario_kill_switch_resets_cold_start(self, cold_start_engine):
        """
        SCENARIO: Kill switch resets cold start to day 0.

        Given: days_running = 10 (past cold start)
        When: Kill switch triggers
        Then: days_running = 0
        And: Next day is cold start day 1
        """
        # Setup: Day 10 (past cold start)
        cold_start_engine._days_running = 10
        assert cold_start_engine.is_cold_start_active() is False

        # Act: Kill switch triggers at end of day
        cold_start_engine.end_of_day_update(kill_switch_triggered=True)

        # Assert: Reset to day 0
        assert cold_start_engine._days_running == 0

        # Next day increment will make it day 1
        cold_start_engine.end_of_day_update(kill_switch_triggered=False)
        assert cold_start_engine._days_running == 1
        assert cold_start_engine.is_cold_start_active() is True
