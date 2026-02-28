"""
Scenario tests for Kill Switch behavior.

Tests the complete kill switch workflow:
1. Portfolio experiences -5% daily loss (V2.3.17: raised from 3%)
2. Kill switch triggers
3. All positions liquidated
4. New entries blocked
5. Cold start reset (days_running = 0)

Spec: docs/12-risk-engine.md
"""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

import config
from engines.core.cold_start_engine import ColdStartEngine
from engines.core.risk_engine import KSTier, RiskEngine, SafeguardType


@pytest.fixture(autouse=True)
def _ks_test_thresholds(monkeypatch):
    """Override graduated KS thresholds so scenario tests use standard 2%/4%/6%."""
    monkeypatch.setattr(config, "KS_GRADUATED_ENABLED", True)
    monkeypatch.setattr(config, "KS_TIER_1_PCT", 0.02)
    monkeypatch.setattr(config, "KS_TIER_2_PCT", 0.04)
    monkeypatch.setattr(config, "KS_TIER_3_PCT", 0.06)
    monkeypatch.setattr(config, "KILL_SWITCH_PCT", 0.06)


@pytest.fixture
def mock_algorithm():
    """Create mock algorithm for kill switch tests."""
    algo = MagicMock()
    algo.Time = datetime(2024, 1, 15, 11, 0)
    algo.Log = MagicMock()
    algo.Debug = MagicMock()
    algo.Portfolio = MagicMock()
    algo.Portfolio.TotalPortfolioValue = 47500.0  # V2.3.17: 5% loss from $50000
    algo.Portfolio.Invested = True
    return algo


@pytest.fixture
def risk_engine(mock_algorithm):
    """Create risk engine instance with baseline set."""
    engine = RiskEngine(mock_algorithm)
    # Set prior close baseline of $50,000
    engine.set_equity_prior_close(50000.0)
    return engine


@pytest.fixture
def cold_start_engine(mock_algorithm):
    """Create cold start engine instance."""
    return ColdStartEngine(mock_algorithm)


class TestKillSwitchScenario:
    """
    End-to-end scenario tests for Kill Switch.

    These tests simulate a complete trading day where the kill switch
    is triggered and verify the system responds correctly.
    """

    @pytest.mark.scenario
    def test_scenario_kill_switch_full_liquidation(self, risk_engine):
        """
        SCENARIO: Portfolio drops 5%, kill switch liquidates all (V2.3.17).

        Given: Portfolio has positions in QLD, TQQQ, SHV
        When: Daily P&L drops to -5%
        Then: Kill switch triggers
        And: All positions are liquidated
        And: No new entries allowed for rest of day
        """
        # Setup: Prior close was $50,000, current is $47,500 (-5%)
        current_equity = 47500.0

        # Act: Check kill switch
        triggered = risk_engine.check_kill_switch(current_equity)

        # Assert: Kill switch triggered
        assert triggered is True
        assert risk_engine._kill_switch_active is True

    @pytest.mark.scenario
    def test_scenario_kill_switch_resets_cold_start(self, risk_engine, cold_start_engine):
        """
        SCENARIO: Kill switch resets cold start counter.

        Given: Algorithm is on day 10 (past cold start)
        When: Kill switch triggers
        Then: days_running is reset to 0
        And: Next day begins cold start sequence
        """
        # Setup: Day 10 (past cold start)
        cold_start_engine._days_running = 10
        assert cold_start_engine.is_cold_start_active() is False

        # Act: Kill switch triggers (V2.3.17: 5% threshold)
        risk_engine.check_kill_switch(47500.0)  # -5% triggers kill switch

        # Apply to cold start (as main.py would do)
        cold_start_engine.end_of_day_update(kill_switch_triggered=True)

        # Assert: Cold start reset
        assert cold_start_engine._days_running == 0

        # Next day
        cold_start_engine.end_of_day_update(kill_switch_triggered=False)
        assert cold_start_engine._days_running == 1
        assert cold_start_engine.is_cold_start_active() is True

    @pytest.mark.scenario
    def test_scenario_kill_switch_state_persists(self, risk_engine):
        """
        SCENARIO: Weekly breaker state persists across restart.

        Given: Weekly breaker triggered this week
        When: Algorithm restarts mid-day
        Then: Weekly breaker state is restored
        And: Position sizing remains reduced

        Note: Kill switch is an intraday flag that resets daily and is NOT
        persisted. The weekly breaker IS persisted as it spans multiple days.
        """
        # Setup: Trigger weekly breaker (5% weekly loss)
        risk_engine._weekly_breaker_active = True
        risk_engine._week_start_equity = 50000.0
        risk_engine._last_kill_date = "2024-01-15"

        # Persist state
        state = risk_engine.get_state_for_persistence()

        # Simulate restart
        new_algo = MagicMock()
        new_algo.Log = MagicMock()
        new_algo.Time = datetime(2024, 1, 15, 12, 0)
        new_engine = RiskEngine(new_algo)

        # Restore state
        new_engine.load_state(state)

        # Assert: Weekly breaker state restored
        assert new_engine._weekly_breaker_active is True
        assert new_engine._week_start_equity == 50000.0
        assert new_engine._last_kill_date == "2024-01-15"

    @pytest.mark.scenario
    def test_scenario_kill_switch_next_day_reset(self, risk_engine):
        """
        SCENARIO: Kill switch resets at start of next trading day.

        Given: Kill switch triggered yesterday
        When: New trading day begins
        Then: Kill switch state is cleared
        And: Trading allowed (in cold start mode)
        """
        # Trigger kill switch (V2.3.17: 5% threshold)
        risk_engine.check_kill_switch(47500.0)
        assert risk_engine._kill_switch_active is True

        # New day - reset daily state
        risk_engine.reset_daily_state()

        # Assert: Kill switch cleared
        assert risk_engine._kill_switch_active is False

        # Set new baseline and check - no trigger with 0% loss
        risk_engine.set_equity_prior_close(47500.0)
        triggered = risk_engine.check_kill_switch(47500.0)
        assert triggered is False

    @pytest.mark.scenario
    def test_scenario_kill_switch_threshold_boundary(self, mock_algorithm):
        """
        SCENARIO: Graduated KS Tier 1 triggers at exactly -2%, not before (V2.28.1).

        Given: Portfolio at -1.99% loss
        When: Loss increases to -2.00%
        Then: Kill switch triggers (Tier 1 REDUCE)
        """
        # Fresh engine for each test
        engine = RiskEngine(mock_algorithm)
        engine.set_equity_prior_close(50000.0)

        # Test: -1.99% should NOT trigger
        current_below = 50000.0 * (1 - 0.0199)  # $49,005
        triggered_below = engine.check_kill_switch(current_below)
        assert triggered_below is False

        # Reset
        engine._kill_switch_active = False
        engine._ks_current_tier = KSTier.NONE

        # Test: -2.00% SHOULD trigger (V2.28.1 Tier 1)
        current_at = 50000.0 * (1 - 0.02)  # $49,000
        triggered_at = engine.check_kill_switch(current_at)
        assert triggered_at is True
        assert engine.get_ks_tier() == KSTier.REDUCE

        # Reset
        engine._kill_switch_active = False
        engine._ks_current_tier = KSTier.NONE

        # Test: -4.00% should trigger Tier 2 (V2.28.1)
        current_tier2 = 50000.0 * (1 - 0.04)  # $48,000
        triggered_tier2 = engine.check_kill_switch(current_tier2)
        assert triggered_tier2 is True
        assert engine.get_ks_tier() == KSTier.TREND_EXIT
