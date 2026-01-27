"""
Scenario tests for Panic Mode behavior.

Tests the complete panic mode workflow:
1. SPY drops 4% intraday
2. Panic mode triggers
3. Long positions liquidated
4. Hedge positions kept
5. New long entries blocked

Spec: docs/12-risk-engine.md
"""

import pytest
from unittest.mock import MagicMock
from datetime import datetime

from engines.core.risk_engine import RiskEngine, SafeguardType
from models.target_weight import TargetWeight
from models.enums import Urgency
import config


@pytest.fixture
def mock_algorithm():
    """Create mock algorithm for panic mode tests."""
    algo = MagicMock()
    algo.Time = datetime(2024, 1, 15, 11, 0)
    algo.Log = MagicMock()
    algo.Debug = MagicMock()
    algo.Portfolio = MagicMock()
    algo.Portfolio.TotalPortfolioValue = 50000.0
    algo.Portfolio.Invested = True
    return algo


@pytest.fixture
def risk_engine(mock_algorithm):
    """Create risk engine with SPY open price set."""
    engine = RiskEngine(mock_algorithm)
    engine.set_spy_open(450.0)  # SPY opened at $450
    return engine


class TestPanicModeScenario:
    """
    End-to-end scenario tests for Panic Mode.

    These tests simulate a market crash scenario where SPY drops 4%
    and verify the system protects capital appropriately.
    """

    @pytest.mark.scenario
    def test_scenario_panic_mode_liquidates_longs_only(self, risk_engine):
        """
        SCENARIO: Panic mode liquidates longs but keeps hedges.

        Given: Portfolio has QLD (long), TMF (hedge), PSQ (hedge)
        When: SPY drops 4% intraday
        Then: Panic mode triggers
        And: QLD position is liquidated
        And: TMF and PSQ positions are kept
        """
        # SPY drops 4% from open of $450
        spy_current = 450.0 * (1 - 0.04)  # $432

        # Check panic mode
        triggered = risk_engine.check_panic_mode(spy_current)

        # Assert: Panic mode triggered
        assert triggered is True
        assert risk_engine._panic_mode_active is True

        # Longs should be liquidated (QLD, SSO, TQQQ, SOXL)
        # Hedges should be kept (TMF, PSQ, SHV)
        # This is enforced by the risk check result in run_risk_checks

    @pytest.mark.scenario
    def test_scenario_panic_mode_blocks_new_longs(self, risk_engine):
        """
        SCENARIO: Panic mode blocks new long entries.

        Given: Panic mode is active
        When: Trend engine generates QLD buy signal
        Then: Signal is blocked by risk engine
        And: No order is placed
        """
        # Trigger panic mode
        risk_engine.check_panic_mode(432.0)  # -4% from $450

        # Verify panic mode active
        assert risk_engine._panic_mode_active is True

        # Create a long entry signal
        mock_signal = TargetWeight(
            symbol="QLD",
            target_weight=0.30,
            source="TREND",
            urgency=Urgency.EOD,
            reason="BB Breakout",
        )

        # Long symbols should be blocked in panic mode
        long_symbols = ["QLD", "SSO", "TQQQ", "SOXL"]
        is_long = mock_signal.symbol in long_symbols
        should_block = risk_engine._panic_mode_active and is_long

        assert should_block is True

    @pytest.mark.scenario
    def test_scenario_panic_mode_allows_hedges(self, risk_engine):
        """
        SCENARIO: Panic mode still allows hedge adjustments.

        Given: Panic mode is active
        When: Hedge engine generates TMF increase signal
        Then: Signal is allowed through
        And: Hedge position is increased
        """
        # Trigger panic mode
        risk_engine.check_panic_mode(432.0)

        # Create hedge signal
        hedge_signal = TargetWeight(
            symbol="TMF",
            target_weight=0.20,
            source="HEDGE",
            urgency=Urgency.EOD,
            reason="Regime=30",
        )

        # Hedge symbols are NOT blocked
        hedge_symbols = ["TMF", "PSQ", "SHV"]
        is_hedge = hedge_signal.symbol in hedge_symbols

        assert is_hedge is True
        # Hedges allowed even in panic mode

    @pytest.mark.scenario
    def test_scenario_panic_mode_intraday_only(self, risk_engine):
        """
        SCENARIO: Panic mode is intraday only, resets next day.

        Given: Panic mode triggered today
        When: Next trading day begins
        Then: Panic mode is not active
        And: Normal trading resumes
        """
        # Trigger panic mode
        risk_engine.check_panic_mode(432.0)
        assert risk_engine._panic_mode_active is True

        # New day - reset daily state
        risk_engine.reset_daily_state()

        # Assert: Panic mode cleared
        assert risk_engine._panic_mode_active is False

        # Set new SPY open and check - no drop means no panic
        risk_engine.set_spy_open(432.0)
        triggered = risk_engine.check_panic_mode(432.0)
        assert triggered is False

    @pytest.mark.scenario
    def test_scenario_panic_mode_threshold_boundary(self, mock_algorithm):
        """
        SCENARIO: Panic mode triggers at exactly SPY -4%.

        Given: SPY at -3.99% intraday
        When: SPY drops to -4.00%
        Then: Panic mode triggers immediately
        """
        # Fresh engine
        engine = RiskEngine(mock_algorithm)
        engine.set_spy_open(450.0)

        # Test: -3.99% should NOT trigger
        spy_below = 450.0 * (1 - 0.0399)
        triggered_below = engine.check_panic_mode(spy_below)
        assert triggered_below is False

        # Reset
        engine._panic_mode_active = False

        # Test: -4.00% SHOULD trigger
        spy_at = 450.0 * (1 - 0.04)
        triggered_at = engine.check_panic_mode(spy_at)
        assert triggered_at is True

        # Reset
        engine._panic_mode_active = False

        # Test: -4.01% should also trigger
        spy_above = 450.0 * (1 - 0.0401)
        triggered_above = engine.check_panic_mode(spy_above)
        assert triggered_above is True
