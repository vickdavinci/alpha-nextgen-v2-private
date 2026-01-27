"""
Tests for Trend Engine - Bollinger Band compression breakout strategy.

Tests cover:
- TrendPosition dataclass (creation, serialization, deserialization)
- Entry signal conditions (compression, breakout, regime, cold start)
- Exit signals (band basis, regime exit, stop hit)
- Chandelier stop calculation and tiered multipliers
- Stop never moves down rule
- Position registration and management
- State persistence and restoration

Spec: docs/07-trend-engine.md
"""

import pytest

import config
from engines.trend_engine import TrendEngine, TrendPosition
from models.enums import Urgency
from models.target_weight import TargetWeight


# =============================================================================
# FIXTURES
# =============================================================================


@pytest.fixture
def engine():
    """Create a TrendEngine instance for testing."""
    return TrendEngine(algorithm=None)


@pytest.fixture
def engine_with_position(engine):
    """Create engine with an existing QLD position."""
    engine.register_entry(
        symbol="QLD",
        entry_price=100.0,
        entry_date="2024-01-15",
        atr=2.0,
        strategy_tag="TREND",
    )
    return engine


# =============================================================================
# TRENDPOSITION DATACLASS TESTS
# =============================================================================


class TestTrendPosition:
    """Tests for TrendPosition dataclass."""

    def test_creation(self):
        """Test basic position creation."""
        position = TrendPosition(
            symbol="QLD",
            entry_price=100.0,
            entry_date="2024-01-15",
            highest_high=105.0,
            current_stop=94.0,
            strategy_tag="TREND",
        )
        assert position.symbol == "QLD"
        assert position.entry_price == 100.0
        assert position.entry_date == "2024-01-15"
        assert position.highest_high == 105.0
        assert position.current_stop == 94.0
        assert position.strategy_tag == "TREND"

    def test_default_strategy_tag(self):
        """Test default strategy tag is TREND."""
        position = TrendPosition(
            symbol="SSO",
            entry_price=50.0,
            entry_date="2024-01-10",
            highest_high=50.0,
            current_stop=47.0,
        )
        assert position.strategy_tag == "TREND"

    def test_cold_start_strategy_tag(self):
        """Test cold start strategy tag can be set."""
        position = TrendPosition(
            symbol="QLD",
            entry_price=100.0,
            entry_date="2024-01-01",
            highest_high=100.0,
            current_stop=94.0,
            strategy_tag="COLD_START",
        )
        assert position.strategy_tag == "COLD_START"

    def test_to_dict_serialization(self):
        """Test serialization to dictionary."""
        position = TrendPosition(
            symbol="QLD",
            entry_price=100.0,
            entry_date="2024-01-15",
            highest_high=110.0,
            current_stop=104.0,
            strategy_tag="TREND",
        )
        data = position.to_dict()

        assert data["symbol"] == "QLD"
        assert data["entry_price"] == 100.0
        assert data["entry_date"] == "2024-01-15"
        assert data["highest_high"] == 110.0
        assert data["current_stop"] == 104.0
        assert data["strategy_tag"] == "TREND"

    def test_from_dict_deserialization(self):
        """Test deserialization from dictionary."""
        data = {
            "symbol": "SSO",
            "entry_price": 50.0,
            "entry_date": "2024-01-10",
            "highest_high": 55.0,
            "current_stop": 51.0,
            "strategy_tag": "COLD_START",
        }
        position = TrendPosition.from_dict(data)

        assert position.symbol == "SSO"
        assert position.entry_price == 50.0
        assert position.entry_date == "2024-01-10"
        assert position.highest_high == 55.0
        assert position.current_stop == 51.0
        assert position.strategy_tag == "COLD_START"

    def test_from_dict_missing_strategy_tag(self):
        """Test deserialization defaults strategy_tag to TREND."""
        data = {
            "symbol": "QLD",
            "entry_price": 100.0,
            "entry_date": "2024-01-15",
            "highest_high": 100.0,
            "current_stop": 94.0,
        }
        position = TrendPosition.from_dict(data)
        assert position.strategy_tag == "TREND"

    def test_roundtrip_serialization(self):
        """Test that to_dict/from_dict roundtrip preserves data."""
        original = TrendPosition(
            symbol="QLD",
            entry_price=100.0,
            entry_date="2024-01-15",
            highest_high=115.0,
            current_stop=108.0,
            strategy_tag="TREND",
        )
        restored = TrendPosition.from_dict(original.to_dict())

        assert restored.symbol == original.symbol
        assert restored.entry_price == original.entry_price
        assert restored.entry_date == original.entry_date
        assert restored.highest_high == original.highest_high
        assert restored.current_stop == original.current_stop
        assert restored.strategy_tag == original.strategy_tag


# =============================================================================
# ENGINE INITIALIZATION TESTS
# =============================================================================


class TestTrendEngineInit:
    """Tests for TrendEngine initialization."""

    def test_instruments_list(self, engine):
        """Test INSTRUMENTS class variable."""
        assert "QLD" in TrendEngine.INSTRUMENTS
        assert "SSO" in TrendEngine.INSTRUMENTS
        assert len(TrendEngine.INSTRUMENTS) == 2

    def test_initial_state_empty(self, engine):
        """Test engine starts with no positions."""
        assert len(engine.get_all_positions()) == 0
        assert not engine.has_position("QLD")
        assert not engine.has_position("SSO")


# =============================================================================
# ENTRY SIGNAL TESTS
# =============================================================================


class TestEntrySignals:
    """Tests for entry signal detection."""

    def test_valid_entry_all_conditions_met(self, engine):
        """Test entry signal when all conditions are satisfied."""
        # Compression (bandwidth < 10%), breakout (close > upper), regime >= 40
        result = engine.check_entry_signal(
            symbol="QLD",
            close=105.0,  # Above upper band
            upper_band=103.0,
            middle_band=100.0,
            lower_band=97.0,  # Bandwidth = (103-97)/100 = 0.06 < 0.10
            regime_score=55.0,  # >= 40
            is_cold_start_active=False,
            has_warm_entry=False,
            atr=2.0,
            current_date="2024-01-15",
        )

        assert result is not None
        assert isinstance(result, TargetWeight)
        assert result.symbol == "QLD"
        assert result.target_weight == 1.0
        assert result.source == "TREND"
        assert result.urgency == Urgency.EOD
        assert "BB Compression Breakout" in result.reason

    def test_entry_blocked_no_compression(self, engine):
        """Test entry blocked when bandwidth >= 10%."""
        result = engine.check_entry_signal(
            symbol="QLD",
            close=115.0,
            upper_band=110.0,
            middle_band=100.0,
            lower_band=90.0,  # Bandwidth = (110-90)/100 = 0.20 >= 0.10
            regime_score=60.0,
            is_cold_start_active=False,
            has_warm_entry=False,
            atr=2.0,
            current_date="2024-01-15",
        )
        assert result is None

    def test_entry_blocked_no_breakout(self, engine):
        """Test entry blocked when close <= upper band."""
        result = engine.check_entry_signal(
            symbol="QLD",
            close=102.0,  # Not above upper band
            upper_band=103.0,
            middle_band=100.0,
            lower_band=97.0,
            regime_score=60.0,
            is_cold_start_active=False,
            has_warm_entry=False,
            atr=2.0,
            current_date="2024-01-15",
        )
        assert result is None

    def test_entry_blocked_close_equals_upper(self, engine):
        """Test entry blocked when close exactly equals upper band."""
        result = engine.check_entry_signal(
            symbol="QLD",
            close=103.0,  # Equal to upper band
            upper_band=103.0,
            middle_band=100.0,
            lower_band=97.0,
            regime_score=60.0,
            is_cold_start_active=False,
            has_warm_entry=False,
            atr=2.0,
            current_date="2024-01-15",
        )
        assert result is None

    def test_entry_blocked_low_regime(self, engine):
        """Test entry blocked when regime < 40."""
        result = engine.check_entry_signal(
            symbol="QLD",
            close=105.0,
            upper_band=103.0,
            middle_band=100.0,
            lower_band=97.0,
            regime_score=35.0,  # < 40
            is_cold_start_active=False,
            has_warm_entry=False,
            atr=2.0,
            current_date="2024-01-15",
        )
        assert result is None

    def test_entry_blocked_regime_at_boundary(self, engine):
        """Test entry allowed when regime exactly 40."""
        result = engine.check_entry_signal(
            symbol="QLD",
            close=105.0,
            upper_band=103.0,
            middle_band=100.0,
            lower_band=97.0,
            regime_score=40.0,  # Exactly at threshold
            is_cold_start_active=False,
            has_warm_entry=False,
            atr=2.0,
            current_date="2024-01-15",
        )
        assert result is not None

    def test_entry_blocked_cold_start_no_warm_entry(self, engine):
        """Test entry blocked during cold start without warm entry."""
        result = engine.check_entry_signal(
            symbol="QLD",
            close=105.0,
            upper_band=103.0,
            middle_band=100.0,
            lower_band=97.0,
            regime_score=60.0,
            is_cold_start_active=True,  # Cold start active
            has_warm_entry=False,  # No warm entry
            atr=2.0,
            current_date="2024-01-15",
        )
        assert result is None

    def test_entry_allowed_cold_start_with_warm_entry(self, engine):
        """Test entry allowed during cold start with warm entry."""
        result = engine.check_entry_signal(
            symbol="QLD",
            close=105.0,
            upper_band=103.0,
            middle_band=100.0,
            lower_band=97.0,
            regime_score=60.0,
            is_cold_start_active=True,
            has_warm_entry=True,  # Has warm entry
            atr=2.0,
            current_date="2024-01-15",
        )
        assert result is not None

    def test_entry_blocked_existing_position(self, engine_with_position):
        """Test entry blocked when position already exists."""
        result = engine_with_position.check_entry_signal(
            symbol="QLD",  # Already have a position
            close=105.0,
            upper_band=103.0,
            middle_band=100.0,
            lower_band=97.0,
            regime_score=60.0,
            is_cold_start_active=False,
            has_warm_entry=False,
            atr=2.0,
            current_date="2024-01-15",
        )
        assert result is None

    def test_entry_blocked_invalid_symbol(self, engine):
        """Test entry blocked for non-trend instruments."""
        result = engine.check_entry_signal(
            symbol="TQQQ",  # Not in INSTRUMENTS
            close=105.0,
            upper_band=103.0,
            middle_band=100.0,
            lower_band=97.0,
            regime_score=60.0,
            is_cold_start_active=False,
            has_warm_entry=False,
            atr=2.0,
            current_date="2024-01-15",
        )
        assert result is None

    def test_entry_sso_valid(self, engine):
        """Test entry signal for SSO."""
        result = engine.check_entry_signal(
            symbol="SSO",
            close=52.5,
            upper_band=52.0,
            middle_band=50.0,
            lower_band=48.0,  # Bandwidth = (52-48)/50 = 0.08 < 0.10
            regime_score=50.0,
            is_cold_start_active=False,
            has_warm_entry=False,
            atr=1.0,
            current_date="2024-01-15",
        )
        assert result is not None
        assert result.symbol == "SSO"


# =============================================================================
# EXIT SIGNAL TESTS
# =============================================================================


class TestExitSignals:
    """Tests for exit signal detection."""

    def test_exit_band_basis_close_below_middle(self, engine_with_position):
        """Test exit when close < middle band."""
        result = engine_with_position.check_exit_signals(
            symbol="QLD",
            close=98.0,  # Below middle band
            high=102.0,
            middle_band=100.0,
            regime_score=60.0,
            atr=2.0,
        )

        assert result is not None
        assert result.symbol == "QLD"
        assert result.target_weight == 0.0
        assert result.urgency == Urgency.EOD
        assert "BAND_EXIT" in result.reason

    def test_no_exit_close_above_middle(self, engine_with_position):
        """Test no exit when close >= middle band."""
        result = engine_with_position.check_exit_signals(
            symbol="QLD",
            close=101.0,  # Above middle band
            high=103.0,
            middle_band=100.0,
            regime_score=60.0,
            atr=2.0,
        )
        assert result is None

    def test_exit_regime_below_30(self, engine_with_position):
        """Test exit when regime < 30."""
        result = engine_with_position.check_exit_signals(
            symbol="QLD",
            close=105.0,  # Above middle band
            high=106.0,
            middle_band=100.0,
            regime_score=25.0,  # Below 30
            atr=2.0,
        )

        assert result is not None
        assert result.target_weight == 0.0
        assert "REGIME_EXIT" in result.reason

    def test_no_exit_regime_at_30(self, engine_with_position):
        """Test no regime exit when score exactly 30."""
        result = engine_with_position.check_exit_signals(
            symbol="QLD",
            close=105.0,
            high=106.0,
            middle_band=100.0,
            regime_score=30.0,  # Exactly at threshold
            atr=2.0,
        )
        assert result is None

    def test_exit_no_position(self, engine):
        """Test no exit signal when no position exists."""
        result = engine.check_exit_signals(
            symbol="QLD",
            close=98.0,  # Would trigger band exit
            high=102.0,
            middle_band=100.0,
            regime_score=25.0,  # Would trigger regime exit
            atr=2.0,
        )
        assert result is None

    def test_updates_highest_high(self, engine_with_position):
        """Test that check_exit_signals updates highest_high."""
        initial_hh = engine_with_position.get_highest_high("QLD")
        assert initial_hh == 100.0

        engine_with_position.check_exit_signals(
            symbol="QLD",
            close=108.0,
            high=112.0,  # New high
            middle_band=100.0,
            regime_score=60.0,
            atr=2.0,
        )

        new_hh = engine_with_position.get_highest_high("QLD")
        assert new_hh == 112.0

    def test_does_not_lower_highest_high(self, engine_with_position):
        """Test that highest_high never decreases."""
        # First update to higher
        engine_with_position.check_exit_signals(
            symbol="QLD",
            close=108.0,
            high=115.0,
            middle_band=100.0,
            regime_score=60.0,
            atr=2.0,
        )
        assert engine_with_position.get_highest_high("QLD") == 115.0

        # Second update with lower high
        engine_with_position.check_exit_signals(
            symbol="QLD",
            close=110.0,
            high=112.0,  # Lower than previous HH
            middle_band=100.0,
            regime_score=60.0,
            atr=2.0,
        )
        assert engine_with_position.get_highest_high("QLD") == 115.0  # Unchanged


# =============================================================================
# STOP HIT TESTS
# =============================================================================


class TestStopHit:
    """Tests for Chandelier stop hit detection."""

    def test_stop_hit_price_below_stop(self, engine_with_position):
        """Test stop hit when price <= stop level."""
        # Initial stop = 100 - (3.0 * 2.0) = 94.0
        result = engine_with_position.check_stop_hit(
            symbol="QLD",
            current_price=93.0,  # Below stop
        )

        assert result is not None
        assert result.symbol == "QLD"
        assert result.target_weight == 0.0
        assert result.urgency == Urgency.IMMEDIATE
        assert "STOP_HIT" in result.reason

    def test_stop_hit_price_equals_stop(self, engine_with_position):
        """Test stop hit when price exactly equals stop."""
        result = engine_with_position.check_stop_hit(
            symbol="QLD",
            current_price=94.0,  # Exactly at stop
        )

        assert result is not None
        assert result.urgency == Urgency.IMMEDIATE

    def test_no_stop_hit_price_above_stop(self, engine_with_position):
        """Test no stop hit when price > stop."""
        result = engine_with_position.check_stop_hit(
            symbol="QLD",
            current_price=95.0,  # Above stop
        )
        assert result is None

    def test_stop_hit_no_position(self, engine):
        """Test no stop hit when no position exists."""
        result = engine.check_stop_hit(
            symbol="QLD",
            current_price=50.0,
        )
        assert result is None


# =============================================================================
# CHANDELIER STOP UPDATE TESTS
# =============================================================================


class TestChandelierStopUpdate:
    """Tests for Chandelier trailing stop behavior."""

    def test_stop_increases_with_higher_high(self, engine):
        """Test stop moves up when highest high increases."""
        engine.register_entry(
            symbol="QLD",
            entry_price=100.0,
            entry_date="2024-01-15",
            atr=2.0,
        )
        initial_stop = engine.get_stop_level("QLD")
        assert initial_stop == 94.0  # 100 - (3.0 * 2.0)

        # Price moves up
        engine.check_exit_signals(
            symbol="QLD",
            close=108.0,
            high=110.0,
            middle_band=100.0,
            regime_score=60.0,
            atr=2.0,
        )

        new_stop = engine.get_stop_level("QLD")
        assert new_stop == 104.0  # 110 - (3.0 * 2.0)
        assert new_stop > initial_stop

    def test_stop_never_moves_down(self, engine):
        """Test stop only moves up, never down."""
        engine.register_entry(
            symbol="QLD",
            entry_price=100.0,
            entry_date="2024-01-15",
            atr=2.0,
        )

        # Move up first - 20% profit uses tight multiplier (2.0)
        engine.check_exit_signals(
            symbol="QLD",
            close=115.0,
            high=120.0,
            middle_band=100.0,
            regime_score=60.0,
            atr=2.0,
        )
        high_stop = engine.get_stop_level("QLD")
        assert high_stop == 116.0  # 120 - (2.0 * 2.0) - 20% profit uses tight mult

        # Price drops but stop should not
        engine.check_exit_signals(
            symbol="QLD",
            close=105.0,
            high=106.0,
            middle_band=100.0,
            regime_score=60.0,
            atr=2.0,
        )
        # Highest high unchanged (120), stop should stay at 116
        assert engine.get_stop_level("QLD") == high_stop

    def test_tiered_multiplier_base_level(self, engine):
        """Test base multiplier (3.0) for < 15% profit."""
        engine.register_entry(
            symbol="QLD",
            entry_price=100.0,
            entry_date="2024-01-15",
            atr=2.0,
        )
        # Move up 10% (below 15% threshold)
        engine.check_exit_signals(
            symbol="QLD",
            close=108.0,
            high=110.0,  # 10% profit
            middle_band=100.0,
            regime_score=60.0,
            atr=2.0,
        )
        # Stop = 110 - (3.0 * 2.0) = 104
        assert engine.get_stop_level("QLD") == 104.0

    def test_tiered_multiplier_tight_level(self, engine):
        """Test tight multiplier (2.0) for 15-25% profit."""
        engine.register_entry(
            symbol="QLD",
            entry_price=100.0,
            entry_date="2024-01-15",
            atr=2.0,
        )
        # Move up 20% (between 15% and 25%)
        engine.check_exit_signals(
            symbol="QLD",
            close=118.0,
            high=120.0,  # 20% profit
            middle_band=100.0,
            regime_score=60.0,
            atr=2.0,
        )
        # Stop = 120 - (2.0 * 2.0) = 116
        assert engine.get_stop_level("QLD") == 116.0

    def test_tiered_multiplier_tighter_level(self, engine):
        """Test tighter multiplier (1.5) for > 25% profit."""
        engine.register_entry(
            symbol="QLD",
            entry_price=100.0,
            entry_date="2024-01-15",
            atr=2.0,
        )
        # Move up 30% (above 25%)
        engine.check_exit_signals(
            symbol="QLD",
            close=128.0,
            high=130.0,  # 30% profit
            middle_band=100.0,
            regime_score=60.0,
            atr=2.0,
        )
        # Stop = 130 - (1.5 * 2.0) = 127
        assert engine.get_stop_level("QLD") == 127.0


# =============================================================================
# POSITION MANAGEMENT TESTS
# =============================================================================


class TestPositionManagement:
    """Tests for position registration and management."""

    def test_register_entry(self, engine):
        """Test position registration."""
        position = engine.register_entry(
            symbol="QLD",
            entry_price=100.0,
            entry_date="2024-01-15",
            atr=2.0,
            strategy_tag="TREND",
        )

        assert position.symbol == "QLD"
        assert position.entry_price == 100.0
        assert position.entry_date == "2024-01-15"
        assert position.highest_high == 100.0  # Starts at entry price
        assert position.current_stop == 94.0  # 100 - (3.0 * 2.0)
        assert position.strategy_tag == "TREND"

    def test_register_entry_cold_start_tag(self, engine):
        """Test position registration with cold start tag."""
        position = engine.register_entry(
            symbol="QLD",
            entry_price=100.0,
            entry_date="2024-01-15",
            atr=2.0,
            strategy_tag="COLD_START",
        )
        assert position.strategy_tag == "COLD_START"

    def test_has_position(self, engine):
        """Test has_position check."""
        assert not engine.has_position("QLD")

        engine.register_entry("QLD", 100.0, "2024-01-15", 2.0)
        assert engine.has_position("QLD")
        assert not engine.has_position("SSO")

    def test_get_position(self, engine):
        """Test get_position returns correct position."""
        engine.register_entry("QLD", 100.0, "2024-01-15", 2.0)

        position = engine.get_position("QLD")
        assert position is not None
        assert position.symbol == "QLD"

        assert engine.get_position("SSO") is None

    def test_get_all_positions(self, engine):
        """Test get_all_positions returns copy of all positions."""
        engine.register_entry("QLD", 100.0, "2024-01-15", 2.0)
        engine.register_entry("SSO", 50.0, "2024-01-16", 1.0)

        positions = engine.get_all_positions()
        assert len(positions) == 2
        assert "QLD" in positions
        assert "SSO" in positions

    def test_remove_position(self, engine_with_position):
        """Test position removal."""
        assert engine_with_position.has_position("QLD")

        removed = engine_with_position.remove_position("QLD")
        assert removed is not None
        assert removed.symbol == "QLD"
        assert not engine_with_position.has_position("QLD")

    def test_remove_nonexistent_position(self, engine):
        """Test removing nonexistent position returns None."""
        result = engine.remove_position("QLD")
        assert result is None

    def test_get_stop_level(self, engine_with_position):
        """Test get_stop_level helper."""
        stop = engine_with_position.get_stop_level("QLD")
        assert stop == 94.0

        assert engine_with_position.get_stop_level("SSO") is None

    def test_get_highest_high(self, engine_with_position):
        """Test get_highest_high helper."""
        hh = engine_with_position.get_highest_high("QLD")
        assert hh == 100.0

        assert engine_with_position.get_highest_high("SSO") is None


# =============================================================================
# STATE PERSISTENCE TESTS
# =============================================================================


class TestStatePersistence:
    """Tests for state persistence and restoration."""

    def test_get_state_for_persistence_empty(self, engine):
        """Test state serialization with no positions."""
        state = engine.get_state_for_persistence()
        assert "positions" in state
        assert len(state["positions"]) == 0

    def test_get_state_for_persistence_with_positions(self, engine):
        """Test state serialization with positions."""
        engine.register_entry("QLD", 100.0, "2024-01-15", 2.0, "TREND")
        engine.register_entry("SSO", 50.0, "2024-01-16", 1.0, "COLD_START")

        state = engine.get_state_for_persistence()
        assert len(state["positions"]) == 2
        assert "QLD" in state["positions"]
        assert "SSO" in state["positions"]

    def test_restore_state(self, engine):
        """Test state restoration."""
        state = {
            "positions": {
                "QLD": {
                    "symbol": "QLD",
                    "entry_price": 100.0,
                    "entry_date": "2024-01-15",
                    "highest_high": 110.0,
                    "current_stop": 104.0,
                    "strategy_tag": "TREND",
                },
                "SSO": {
                    "symbol": "SSO",
                    "entry_price": 50.0,
                    "entry_date": "2024-01-16",
                    "highest_high": 55.0,
                    "current_stop": 51.0,
                    "strategy_tag": "COLD_START",
                },
            }
        }

        engine.restore_state(state)

        assert engine.has_position("QLD")
        assert engine.has_position("SSO")

        qld = engine.get_position("QLD")
        assert qld.entry_price == 100.0
        assert qld.highest_high == 110.0
        assert qld.current_stop == 104.0

        sso = engine.get_position("SSO")
        assert sso.strategy_tag == "COLD_START"

    def test_restore_state_empty(self, engine_with_position):
        """Test restoring empty state clears positions."""
        assert engine_with_position.has_position("QLD")

        engine_with_position.restore_state({"positions": {}})
        assert not engine_with_position.has_position("QLD")

    def test_roundtrip_persistence(self, engine):
        """Test full save/restore cycle."""
        engine.register_entry("QLD", 100.0, "2024-01-15", 2.0, "TREND")

        # Update position
        engine.check_exit_signals(
            symbol="QLD",
            close=115.0,
            high=120.0,
            middle_band=100.0,
            regime_score=60.0,
            atr=2.0,
        )

        # Save and restore to new engine
        state = engine.get_state_for_persistence()
        new_engine = TrendEngine()
        new_engine.restore_state(state)

        # Verify restoration
        position = new_engine.get_position("QLD")
        assert position.highest_high == 120.0
        assert position.current_stop == 116.0  # 120 - (2.0 * 2.0) for 20% profit


# =============================================================================
# RESET TESTS
# =============================================================================


class TestReset:
    """Tests for engine reset."""

    def test_reset_clears_positions(self, engine):
        """Test reset clears all positions."""
        engine.register_entry("QLD", 100.0, "2024-01-15", 2.0)
        engine.register_entry("SSO", 50.0, "2024-01-16", 1.0)

        assert len(engine.get_all_positions()) == 2

        engine.reset()

        assert len(engine.get_all_positions()) == 0
        assert not engine.has_position("QLD")
        assert not engine.has_position("SSO")


# =============================================================================
# EDGE CASES
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_bandwidth_exactly_at_threshold(self, engine):
        """Test bandwidth exactly at compression threshold."""
        # Bandwidth = (110 - 90) / 100 = 0.20 = exactly 0.10 * 2
        # Need bandwidth < 0.10, so exactly 0.10 should block
        result = engine.check_entry_signal(
            symbol="QLD",
            close=115.0,
            upper_band=105.0,
            middle_band=100.0,
            lower_band=95.0,  # Bandwidth = 10/100 = 0.10 exactly
            regime_score=60.0,
            is_cold_start_active=False,
            has_warm_entry=False,
            atr=2.0,
            current_date="2024-01-15",
        )
        assert result is None  # Blocked - needs < 0.10, not <=

    def test_very_tight_compression(self, engine):
        """Test very tight bandwidth compression."""
        result = engine.check_entry_signal(
            symbol="QLD",
            close=102.0,
            upper_band=101.0,
            middle_band=100.0,
            lower_band=99.0,  # Bandwidth = 2/100 = 0.02
            regime_score=60.0,
            is_cold_start_active=False,
            has_warm_entry=False,
            atr=0.5,
            current_date="2024-01-15",
        )
        assert result is not None

    def test_multiple_positions_different_symbols(self, engine):
        """Test managing multiple positions simultaneously."""
        engine.register_entry("QLD", 100.0, "2024-01-15", 2.0)
        engine.register_entry("SSO", 50.0, "2024-01-16", 1.0)

        # Check exits for each
        result_qld = engine.check_exit_signals(
            symbol="QLD",
            close=98.0,  # Exit signal
            high=100.0,
            middle_band=100.0,
            regime_score=60.0,
            atr=2.0,
        )
        assert result_qld is not None
        assert result_qld.symbol == "QLD"

        result_sso = engine.check_exit_signals(
            symbol="SSO",
            close=55.0,  # No exit
            high=56.0,
            middle_band=50.0,
            regime_score=60.0,
            atr=1.0,
        )
        assert result_sso is None  # Still holding

    def test_zero_atr_handling(self, engine):
        """Test behavior with zero ATR."""
        # This shouldn't happen in practice, but engine should handle it
        position = engine.register_entry(
            symbol="QLD",
            entry_price=100.0,
            entry_date="2024-01-15",
            atr=0.0,  # Zero ATR
        )
        # Stop = 100 - (3.0 * 0.0) = 100
        assert position.current_stop == 100.0

    def test_very_high_profit_stop_tightening(self, engine):
        """Test stop tightening at very high profit levels."""
        engine.register_entry(
            symbol="QLD",
            entry_price=100.0,
            entry_date="2024-01-15",
            atr=2.0,
        )

        # 50% profit - should use tighter multiplier
        engine.check_exit_signals(
            symbol="QLD",
            close=148.0,
            high=150.0,
            middle_band=100.0,
            regime_score=60.0,
            atr=2.0,
        )
        # Stop = 150 - (1.5 * 2.0) = 147
        assert engine.get_stop_level("QLD") == 147.0


# =============================================================================
# LOGGING TESTS
# =============================================================================


class TestLogging:
    """Tests for logging functionality."""

    def test_logging_with_algorithm(self):
        """Test that logging works when algorithm is provided."""

        class MockAlgorithm:
            def __init__(self):
                self.logs = []

            def Log(self, message):
                self.logs.append(message)

        mock = MockAlgorithm()
        engine = TrendEngine(algorithm=mock)

        engine.register_entry("QLD", 100.0, "2024-01-15", 2.0)

        assert len(mock.logs) > 0
        assert any("POSITION_REGISTERED" in log for log in mock.logs)

    def test_logging_without_algorithm(self, engine):
        """Test that engine works without algorithm (no crash)."""
        # Should not raise any errors
        engine.register_entry("QLD", 100.0, "2024-01-15", 2.0)
        engine.check_exit_signals("QLD", 110.0, 112.0, 100.0, 60.0, 2.0)
        engine.remove_position("QLD")
        engine.reset()
