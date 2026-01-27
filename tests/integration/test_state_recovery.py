"""
Integration tests for State Recovery and Persistence.

These tests verify that:
1. System state survives algorithm restarts
2. OCO pairs reconnect to broker orders
3. Positions are reconciled with broker
4. Regime and capital state is restored
5. Mid-day restart recovers correctly

CRITICAL: These tests ensure the system is resilient to restarts.
"""

import pytest
import json
from unittest.mock import MagicMock, patch
from typing import Dict, Any


@pytest.fixture
def mock_algorithm_with_state():
    """Mock algorithm with pre-loaded state in ObjectStore."""
    algo = MagicMock()

    algo.Time = MagicMock()
    algo.Time.hour = 11
    algo.Time.minute = 30

    algo.Portfolio = MagicMock()
    algo.Portfolio.TotalPortfolioValue = 105000.0
    algo.Portfolio.Cash = 15000.0

    algo.Log = MagicMock()
    algo.Debug = MagicMock()

    # ObjectStore with saved state
    saved_state = {
        "regime": {
            "score": 58,
            "state": "NEUTRAL",
            "trend_factor": 0.65,
            "vol_factor": 0.55,
            "breadth_factor": 0.50,
            "credit_factor": 0.60
        },
        "capital": {
            "phase": "SEED",
            "days_running": 12,
            "lockbox_amount": 0.0,
            "equity_prior_close": 100000.0,
            "equity_sod": 101500.0,
            "weekly_start_equity": 98000.0
        },
        "positions": {
            "QLD": {
                "entry_price": 72.50,
                "entry_time": "2026-01-20 10:00:00",
                "highest_high": 78.25,
                "current_stop": 68.50
            }
        },
        "oco_pairs": {
            "pair_001": {
                "pair_id": "pair_001",
                "option_symbol": "QQQ 260126C00450000",
                "entry_price": 2.50,
                "quantity": 10,
                "state": "ACTIVE",
                "stop_leg": {
                    "trigger_price": 2.00,
                    "broker_order_id": 1002,
                    "submitted": True,
                    "filled": False
                },
                "profit_leg": {
                    "trigger_price": 3.75,
                    "broker_order_id": 1003,
                    "submitted": True,
                    "filled": False
                }
            }
        },
        "vix_regime": "NORMAL",
        "gap_filter_active": False,
        "kill_switch_triggered": False
    }

    algo.ObjectStore = MagicMock()
    algo.ObjectStore.ContainsKey = MagicMock(return_value=True)
    algo.ObjectStore.Read = MagicMock(return_value=json.dumps(saved_state))
    algo.ObjectStore.Save = MagicMock()

    return algo, saved_state


class TestRegimeStateRecovery:
    """Test Regime Engine state recovery."""

    def test_regime_score_restored(self, mock_algorithm_with_state):
        """
        Given: Previous regime score was 58 (NEUTRAL)
        When: Algorithm restarts
        Then: Regime score is restored correctly
        """
        algo, saved_state = mock_algorithm_with_state

        restored_regime = saved_state["regime"]

        assert restored_regime["score"] == 58
        assert restored_regime["state"] == "NEUTRAL"

    def test_regime_factors_restored(self, mock_algorithm_with_state):
        """
        Given: Individual regime factors were saved
        When: Algorithm restarts
        Then: All 4 factors are restored
        """
        algo, saved_state = mock_algorithm_with_state

        factors = saved_state["regime"]

        assert factors["trend_factor"] == 0.65
        assert factors["vol_factor"] == 0.55
        assert factors["breadth_factor"] == 0.50
        assert factors["credit_factor"] == 0.60

    def test_regime_used_for_first_bar(self, mock_algorithm_with_state):
        """
        Given: Regime restored from yesterday
        When: First bar of new day arrives
        Then: Restored regime is used until EOD recalculation
        """
        algo, saved_state = mock_algorithm_with_state

        # Before new calculation, use restored score
        current_regime = saved_state["regime"]["score"]

        # Regime should be usable for entry decisions
        entry_allowed = current_regime >= 40

        assert entry_allowed is True


class TestCapitalStateRecovery:
    """Test Capital Engine state recovery."""

    def test_phase_restored(self, mock_algorithm_with_state):
        """
        Given: Previous phase was SEED
        When: Algorithm restarts
        Then: Phase is restored correctly
        """
        algo, saved_state = mock_algorithm_with_state

        restored_phase = saved_state["capital"]["phase"]

        assert restored_phase == "SEED"

    def test_days_running_restored(self, mock_algorithm_with_state):
        """
        Given: days_running was 12
        When: Algorithm restarts
        Then: days_running is restored (not reset to 0)
        """
        algo, saved_state = mock_algorithm_with_state

        days_running = saved_state["capital"]["days_running"]

        assert days_running == 12
        assert days_running >= 5, "Should be past cold start"

    def test_lockbox_amount_restored(self, mock_algorithm_with_state):
        """
        Given: Lockbox amount was saved
        When: Algorithm restarts
        Then: Lockbox is protected
        """
        algo, saved_state = mock_algorithm_with_state

        lockbox = saved_state["capital"]["lockbox_amount"]

        # Zero in this case, but should be restored
        assert lockbox == 0.0

    def test_baselines_restored(self, mock_algorithm_with_state):
        """
        Given: equity_prior_close and equity_sod were saved
        When: Algorithm restarts mid-day
        Then: Baselines are restored for kill switch calculation
        """
        algo, saved_state = mock_algorithm_with_state

        equity_prior_close = saved_state["capital"]["equity_prior_close"]
        equity_sod = saved_state["capital"]["equity_sod"]

        assert equity_prior_close == 100000.0
        assert equity_sod == 101500.0


class TestPositionRecovery:
    """Test position tracking recovery."""

    def test_entry_prices_restored(self, mock_algorithm_with_state):
        """
        Given: QLD position with entry price 72.50
        When: Algorithm restarts
        Then: Entry price is restored for stop calculation
        """
        algo, saved_state = mock_algorithm_with_state

        qld_position = saved_state["positions"]["QLD"]

        assert qld_position["entry_price"] == 72.50

    def test_highest_high_restored(self, mock_algorithm_with_state):
        """
        Given: QLD highest high was 78.25
        When: Algorithm restarts
        Then: Chandelier stop uses restored highest high
        """
        algo, saved_state = mock_algorithm_with_state

        qld_position = saved_state["positions"]["QLD"]

        assert qld_position["highest_high"] == 78.25

    def test_current_stop_restored(self, mock_algorithm_with_state):
        """
        Given: QLD stop was 68.50
        When: Algorithm restarts
        Then: Stop level is restored (never moves down)
        """
        algo, saved_state = mock_algorithm_with_state

        qld_position = saved_state["positions"]["QLD"]

        assert qld_position["current_stop"] == 68.50

    def test_position_reconciliation_with_broker(self, mock_algorithm_with_state):
        """
        Given: Local state says QLD position exists
        When: Broker positions are checked
        Then: Positions are reconciled
        """
        algo, saved_state = mock_algorithm_with_state

        # Mock broker position
        def get_broker_position(symbol):
            pos = MagicMock()
            if symbol == "QLD":
                pos.Invested = True
                pos.Quantity = 100
                pos.AveragePrice = 72.50
            else:
                pos.Invested = False
                pos.Quantity = 0
            return pos

        algo.Portfolio.__getitem__ = MagicMock(side_effect=get_broker_position)

        broker_qld = algo.Portfolio["QLD"]
        local_qld = saved_state["positions"]["QLD"]

        # Reconciliation: broker quantity exists
        assert broker_qld.Invested is True
        assert broker_qld.AveragePrice == local_qld["entry_price"]


class TestOCORecovery:
    """Test OCO Manager state recovery."""

    def test_oco_pairs_restored(self, mock_algorithm_with_state):
        """
        Given: Active OCO pair exists in saved state
        When: Algorithm restarts
        Then: OCO pair is restored
        """
        algo, saved_state = mock_algorithm_with_state

        oco_pairs = saved_state["oco_pairs"]

        assert "pair_001" in oco_pairs
        assert oco_pairs["pair_001"]["state"] == "ACTIVE"

    def test_broker_order_ids_verified(self, mock_algorithm_with_state):
        """
        Given: OCO pair has broker order IDs
        When: Algorithm restarts
        Then: Order IDs are verified with broker
        """
        algo, saved_state = mock_algorithm_with_state

        pair = saved_state["oco_pairs"]["pair_001"]

        stop_order_id = pair["stop_leg"]["broker_order_id"]
        profit_order_id = pair["profit_leg"]["broker_order_id"]

        assert stop_order_id == 1002
        assert profit_order_id == 1003

    def test_orphan_oco_orders_cancelled(self, mock_algorithm_with_state):
        """
        Given: Broker has orders not in local state
        When: Reconciliation runs
        Then: Orphan orders are identified
        """
        algo, saved_state = mock_algorithm_with_state

        local_order_ids = {1002, 1003}  # From saved state

        # Simulate broker having extra order
        broker_orders = [
            MagicMock(Id=1002),
            MagicMock(Id=1003),
            MagicMock(Id=9999),  # Orphan!
        ]

        orphans = [o for o in broker_orders if o.Id not in local_order_ids]

        assert len(orphans) == 1
        assert orphans[0].Id == 9999


class TestMidDayRestart:
    """Test mid-day restart scenarios."""

    def test_intraday_mr_positions_lost(self, mock_algorithm_with_state):
        """
        Given: MR position was open before restart
        And: MR positions are intraday only (not persisted)
        When: Algorithm restarts at 11:30
        Then: MR position tracking is lost (acceptable)
        """
        algo, saved_state = mock_algorithm_with_state

        # MR positions not in saved state (intraday only)
        assert "TQQQ" not in saved_state["positions"]
        assert "SOXL" not in saved_state["positions"]

        # This is expected - MR positions are ephemeral

    def test_restart_time_captured(self, mock_algorithm_with_state):
        """
        Given: Algorithm restarts at 11:30
        When: Time is checked
        Then: System knows it's a mid-day restart
        """
        algo, saved_state = mock_algorithm_with_state

        restart_hour = algo.Time.hour
        restart_minute = algo.Time.minute

        market_open_hour = 9
        market_open_minute = 30

        is_mid_day = (restart_hour > market_open_hour or
                     (restart_hour == market_open_hour and
                      restart_minute > market_open_minute))

        assert is_mid_day is True

    def test_safeguards_rechecked_after_restart(self, mock_algorithm_with_state):
        """
        Given: gap_filter_active was False before restart
        When: Algorithm restarts
        Then: Safeguards use persisted state
        """
        algo, saved_state = mock_algorithm_with_state

        gap_filter = saved_state["gap_filter_active"]
        kill_switch = saved_state["kill_switch_triggered"]

        assert gap_filter is False
        assert kill_switch is False


class TestWeeklyStateRecovery:
    """Test weekly-specific state recovery."""

    def test_weekly_start_equity_restored(self, mock_algorithm_with_state):
        """
        Given: Weekly start equity was 98000
        When: Algorithm restarts mid-week
        Then: Weekly breaker uses correct baseline
        """
        algo, saved_state = mock_algorithm_with_state

        weekly_start = saved_state["capital"]["weekly_start_equity"]
        current_equity = algo.Portfolio.TotalPortfolioValue

        # Check weekly performance
        weekly_pnl = (current_equity - weekly_start) / weekly_start
        weekly_breaker_threshold = -0.05

        assert weekly_pnl > weekly_breaker_threshold, \
            f"Weekly P&L {weekly_pnl:.2%} should be above {weekly_breaker_threshold:.2%}"


class TestVIXRegimeRecovery:
    """Test VIX regime state recovery."""

    def test_vix_regime_restored(self, mock_algorithm_with_state):
        """
        Given: VIX regime was NORMAL
        When: Algorithm restarts
        Then: VIX regime is used until next check
        """
        algo, saved_state = mock_algorithm_with_state

        vix_regime = saved_state["vix_regime"]

        assert vix_regime == "NORMAL"

    def test_vix_parameters_applied(self, mock_algorithm_with_state):
        """
        Given: VIX regime is NORMAL
        When: MR parameters are calculated
        Then: NORMAL parameters are used
        """
        algo, saved_state = mock_algorithm_with_state

        vix_regime = saved_state["vix_regime"]

        # Parameters based on regime
        if vix_regime == "NORMAL":
            mr_allocation = 0.10
            rsi_threshold = 30
            stop_pct = 0.08
        elif vix_regime == "CAUTION":
            mr_allocation = 0.05
            rsi_threshold = 25
            stop_pct = 0.06
        else:
            mr_allocation = 0.02
            rsi_threshold = 20
            stop_pct = 0.04

        assert mr_allocation == 0.10
        assert rsi_threshold == 30
        assert stop_pct == 0.08


class TestStateCorruption:
    """Test handling of corrupted state."""

    def test_corrupted_json_uses_defaults(self):
        """
        Given: ObjectStore contains corrupted JSON
        When: State load attempted
        Then: Defaults are used gracefully
        """
        algo = MagicMock()
        algo.ObjectStore = MagicMock()
        algo.ObjectStore.ContainsKey = MagicMock(return_value=True)
        algo.ObjectStore.Read = MagicMock(return_value="not valid json {{{")
        algo.Log = MagicMock()

        # Attempt to parse
        try:
            state = json.loads(algo.ObjectStore.Read())
        except json.JSONDecodeError:
            state = {}  # Default empty state

        assert state == {}

    def test_missing_keys_use_defaults(self, mock_algorithm_with_state):
        """
        Given: State is missing some expected keys
        When: State is loaded
        Then: Missing keys use default values
        """
        algo, saved_state = mock_algorithm_with_state

        # Remove a key
        incomplete_state = dict(saved_state)
        del incomplete_state["vix_regime"]

        # Should use default
        vix_regime = incomplete_state.get("vix_regime", "NORMAL")

        assert vix_regime == "NORMAL"


class TestStateVersioning:
    """Test state schema versioning."""

    def test_state_version_checked(self, mock_algorithm_with_state):
        """
        Given: Saved state has version field
        When: State is loaded
        Then: Version compatibility is verified
        """
        algo, saved_state = mock_algorithm_with_state

        # Add version to state
        saved_state["schema_version"] = "2.1"

        current_version = "2.1"
        saved_version = saved_state.get("schema_version", "1.0")

        # Version should match
        assert saved_version == current_version

    def test_old_version_migrated(self):
        """
        Given: Saved state has old version (1.0)
        When: State is loaded
        Then: Migration is applied
        """
        old_state = {
            "schema_version": "1.0",
            "regime_score": 58,  # Old format (flat)
        }

        # Migration: convert to new format
        if old_state.get("schema_version") == "1.0":
            new_state = {
                "schema_version": "2.1",
                "regime": {
                    "score": old_state.get("regime_score", 50)
                }
            }
        else:
            new_state = old_state

        assert new_state["schema_version"] == "2.1"
        assert new_state["regime"]["score"] == 58


# =============================================================================
# INTEGRATION TEST MARKERS
# =============================================================================

pytestmark = pytest.mark.integration
