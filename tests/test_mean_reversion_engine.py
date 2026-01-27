"""
Tests for Mean Reversion Engine - Intraday oversold bounce strategy.

Tests cover:
- MRPosition dataclass (creation, serialization, deserialization)
- Entry conditions (RSI, drop, volume, time window, regime, cold start, safeguards)
- Exit signals (target hit, VWAP hit, stop hit, time exit)
- Position management
- State persistence and restoration
- Force exit at 3:45 PM

Spec: docs/08-mean-reversion-engine.md
"""

import pytest

import config
from engines.mean_reversion_engine import MeanReversionEngine, MRPosition
from models.enums import Urgency
from models.target_weight import TargetWeight


# =============================================================================
# FIXTURES
# =============================================================================


@pytest.fixture
def engine():
    """Create a MeanReversionEngine instance for testing."""
    return MeanReversionEngine(algorithm=None)


@pytest.fixture
def engine_with_position(engine):
    """Create engine with an existing TQQQ position."""
    engine.register_entry(
        symbol="TQQQ",
        entry_price=45.00,
        entry_time="10:47:00",
        vwap=46.50,
    )
    return engine


def make_entry_params(
    symbol="TQQQ",
    current_price=43.87,
    open_price=45.00,
    rsi_value=22.0,
    current_volume=1500000,
    avg_volume=1000000,
    vwap=46.50,
    regime_score=55.0,
    days_running=10,
    gap_filter_triggered=False,
    vol_shock_active=False,
    time_guard_active=False,
    current_hour=10,
    current_minute=47,
):
    """Helper to create entry signal parameters."""
    return {
        "symbol": symbol,
        "current_price": current_price,
        "open_price": open_price,
        "rsi_value": rsi_value,
        "current_volume": current_volume,
        "avg_volume": avg_volume,
        "vwap": vwap,
        "regime_score": regime_score,
        "days_running": days_running,
        "gap_filter_triggered": gap_filter_triggered,
        "vol_shock_active": vol_shock_active,
        "time_guard_active": time_guard_active,
        "current_hour": current_hour,
        "current_minute": current_minute,
    }


# =============================================================================
# MRPOSITION DATACLASS TESTS
# =============================================================================


class TestMRPosition:
    """Tests for MRPosition dataclass."""

    def test_creation(self):
        """Test basic position creation."""
        position = MRPosition(
            symbol="TQQQ",
            entry_price=45.00,
            entry_time="10:47:00",
            vwap_at_entry=46.50,
            target_price=45.90,
            stop_price=44.10,
        )
        assert position.symbol == "TQQQ"
        assert position.entry_price == 45.00
        assert position.entry_time == "10:47:00"
        assert position.vwap_at_entry == 46.50
        assert position.target_price == 45.90
        assert position.stop_price == 44.10

    def test_to_dict_serialization(self):
        """Test serialization to dictionary."""
        position = MRPosition(
            symbol="SOXL",
            entry_price=28.50,
            entry_time="11:15:00",
            vwap_at_entry=29.00,
            target_price=29.07,
            stop_price=27.93,
        )
        data = position.to_dict()

        assert data["symbol"] == "SOXL"
        assert data["entry_price"] == 28.50
        assert data["entry_time"] == "11:15:00"
        assert data["vwap_at_entry"] == 29.00
        assert data["target_price"] == 29.07
        assert data["stop_price"] == 27.93

    def test_from_dict_deserialization(self):
        """Test deserialization from dictionary."""
        data = {
            "symbol": "TQQQ",
            "entry_price": 45.00,
            "entry_time": "10:47:00",
            "vwap_at_entry": 46.50,
            "target_price": 45.90,
            "stop_price": 44.10,
        }
        position = MRPosition.from_dict(data)

        assert position.symbol == "TQQQ"
        assert position.entry_price == 45.00
        assert position.target_price == 45.90

    def test_roundtrip_serialization(self):
        """Test that to_dict/from_dict roundtrip preserves data."""
        original = MRPosition(
            symbol="TQQQ",
            entry_price=45.00,
            entry_time="10:47:00",
            vwap_at_entry=46.50,
            target_price=45.90,
            stop_price=44.10,
        )
        restored = MRPosition.from_dict(original.to_dict())

        assert restored.symbol == original.symbol
        assert restored.entry_price == original.entry_price
        assert restored.entry_time == original.entry_time
        assert restored.vwap_at_entry == original.vwap_at_entry
        assert restored.target_price == original.target_price
        assert restored.stop_price == original.stop_price


# =============================================================================
# ENGINE INITIALIZATION TESTS
# =============================================================================


class TestMeanReversionEngineInit:
    """Tests for MeanReversionEngine initialization."""

    def test_instruments_list(self, engine):
        """Test INSTRUMENTS class variable (scan order matters)."""
        assert MeanReversionEngine.INSTRUMENTS == ["TQQQ", "SOXL"]
        assert MeanReversionEngine.INSTRUMENTS[0] == "TQQQ"  # TQQQ scanned first

    def test_initial_state_empty(self, engine):
        """Test engine starts with no position."""
        assert not engine.has_position()
        assert engine.get_position() is None


# =============================================================================
# ENTRY SIGNAL TESTS
# =============================================================================


class TestEntrySignals:
    """Tests for entry signal detection."""

    def test_valid_entry_all_conditions_met(self, engine):
        """Test entry signal when all conditions are satisfied."""
        params = make_entry_params()
        result = engine.check_entry_signal(**params)

        assert result is not None
        assert isinstance(result, TargetWeight)
        assert result.symbol == "TQQQ"
        assert result.target_weight == 1.0
        assert result.source == "MR"
        assert result.urgency == Urgency.IMMEDIATE
        assert "MR Entry" in result.reason
        assert "RSI=" in result.reason

    def test_entry_blocked_invalid_symbol(self, engine):
        """Test entry blocked for non-MR instruments."""
        params = make_entry_params(symbol="QLD")  # Not MR instrument
        result = engine.check_entry_signal(**params)
        assert result is None

    def test_entry_blocked_existing_position(self, engine_with_position):
        """Test only one MR position at a time."""
        params = make_entry_params(symbol="SOXL")  # Different symbol
        result = engine_with_position.check_entry_signal(**params)
        assert result is None

    def test_entry_blocked_rsi_too_high(self, engine):
        """Test entry blocked when RSI >= 25."""
        params = make_entry_params(rsi_value=25.0)  # Exactly at threshold
        result = engine.check_entry_signal(**params)
        assert result is None

        params = make_entry_params(rsi_value=30.0)  # Above threshold
        result = engine.check_entry_signal(**params)
        assert result is None

    def test_entry_allowed_rsi_below_threshold(self, engine):
        """Test entry allowed when RSI < 25."""
        params = make_entry_params(rsi_value=24.9)
        result = engine.check_entry_signal(**params)
        assert result is not None

    def test_entry_blocked_drop_too_small(self, engine):
        """Test entry blocked when drop <= 2.5%."""
        # Drop = (45.00 - 43.875) / 45.00 = 0.025 exactly
        params = make_entry_params(current_price=43.875)  # Exactly 2.5% drop
        result = engine.check_entry_signal(**params)
        assert result is None

        # Less than 2.5% drop
        params = make_entry_params(current_price=44.00)  # ~2.2% drop
        result = engine.check_entry_signal(**params)
        assert result is None

    def test_entry_allowed_sufficient_drop(self, engine):
        """Test entry allowed when drop > 2.5%."""
        # Drop = (45.00 - 43.50) / 45.00 = 0.0333 = 3.3%
        params = make_entry_params(current_price=43.50)
        result = engine.check_entry_signal(**params)
        assert result is not None

    def test_entry_blocked_low_volume(self, engine):
        """Test entry blocked when volume <= 1.2× average."""
        params = make_entry_params(current_volume=1200000, avg_volume=1000000)  # 1.2x
        result = engine.check_entry_signal(**params)
        assert result is None

        params = make_entry_params(current_volume=1000000, avg_volume=1000000)  # 1.0x
        result = engine.check_entry_signal(**params)
        assert result is None

    def test_entry_allowed_high_volume(self, engine):
        """Test entry allowed when volume > 1.2× average."""
        params = make_entry_params(current_volume=1210000, avg_volume=1000000)  # 1.21x
        result = engine.check_entry_signal(**params)
        assert result is not None

    def test_entry_blocked_low_regime(self, engine):
        """Test entry blocked when regime < 40."""
        params = make_entry_params(regime_score=39.9)
        result = engine.check_entry_signal(**params)
        assert result is None

    def test_entry_allowed_regime_at_threshold(self, engine):
        """Test entry allowed when regime exactly 40."""
        params = make_entry_params(regime_score=40.0)
        result = engine.check_entry_signal(**params)
        assert result is not None

    def test_entry_blocked_cold_start(self, engine):
        """Test entry blocked during cold start (days < 5)."""
        params = make_entry_params(days_running=4)
        result = engine.check_entry_signal(**params)
        assert result is None

    def test_entry_allowed_after_cold_start(self, engine):
        """Test entry allowed after cold start (days >= 5)."""
        params = make_entry_params(days_running=5)
        result = engine.check_entry_signal(**params)
        assert result is not None

    def test_entry_blocked_gap_filter(self, engine):
        """Test entry blocked when gap filter is active."""
        params = make_entry_params(gap_filter_triggered=True)
        result = engine.check_entry_signal(**params)
        assert result is None

    def test_entry_blocked_vol_shock(self, engine):
        """Test entry blocked when vol shock is active."""
        params = make_entry_params(vol_shock_active=True)
        result = engine.check_entry_signal(**params)
        assert result is None

    def test_entry_blocked_time_guard(self, engine):
        """Test entry blocked when time guard is active."""
        params = make_entry_params(time_guard_active=True)
        result = engine.check_entry_signal(**params)
        assert result is None

    def test_entry_blocked_before_window(self, engine):
        """Test entry blocked before 10:00 AM."""
        params = make_entry_params(current_hour=9, current_minute=59)
        result = engine.check_entry_signal(**params)
        assert result is None

    def test_entry_allowed_at_window_start(self, engine):
        """Test entry allowed at exactly 10:00 AM."""
        params = make_entry_params(current_hour=10, current_minute=0)
        result = engine.check_entry_signal(**params)
        assert result is not None

    def test_entry_blocked_after_window(self, engine):
        """Test entry blocked at/after 3:00 PM."""
        params = make_entry_params(current_hour=15, current_minute=0)
        result = engine.check_entry_signal(**params)
        assert result is None

    def test_entry_allowed_before_window_end(self, engine):
        """Test entry allowed just before 3:00 PM."""
        params = make_entry_params(current_hour=14, current_minute=59)
        result = engine.check_entry_signal(**params)
        assert result is not None

    def test_entry_soxl_valid(self, engine):
        """Test entry signal for SOXL."""
        params = make_entry_params(symbol="SOXL")
        result = engine.check_entry_signal(**params)
        assert result is not None
        assert result.symbol == "SOXL"


# =============================================================================
# EXIT SIGNAL TESTS
# =============================================================================


class TestExitSignals:
    """Tests for exit signal detection."""

    def test_exit_target_hit(self, engine_with_position):
        """Test exit when target price (+2%) is hit."""
        # Target = 45.00 * 1.02 = 45.90
        result = engine_with_position.check_exit_signals(
            current_price=45.95,  # Above target
            current_hour=11,
            current_minute=30,
        )

        assert result is not None
        assert result.symbol == "TQQQ"
        assert result.target_weight == 0.0
        assert result.urgency == Urgency.IMMEDIATE
        assert "TARGET_HIT" in result.reason

    def test_exit_vwap_hit(self, engine):
        """Test exit when price returns to VWAP (VWAP between entry and target)."""
        # Setup position with VWAP between entry and target
        # Entry = 45.00, Target = 45.90, so use VWAP = 45.50
        engine.register_entry(
            symbol="TQQQ",
            entry_price=45.00,
            entry_time="10:47:00",
            vwap=45.50,  # VWAP below target (45.90)
        )

        # Price at VWAP but below target
        result = engine.check_exit_signals(
            current_price=45.50,  # At VWAP, below target (45.90)
            current_hour=11,
            current_minute=30,
        )

        assert result is not None
        assert "VWAP_HIT" in result.reason

    def test_exit_stop_hit(self, engine_with_position):
        """Test exit when stop price (-2%) is hit."""
        # Stop = 45.00 * 0.98 = 44.10
        result = engine_with_position.check_exit_signals(
            current_price=44.05,  # Below stop
            current_hour=11,
            current_minute=30,
        )

        assert result is not None
        assert result.symbol == "TQQQ"
        assert result.target_weight == 0.0
        assert result.urgency == Urgency.IMMEDIATE
        assert "STOP_HIT" in result.reason

    def test_exit_time_forced(self, engine_with_position):
        """Test forced exit at 3:45 PM."""
        result = engine_with_position.check_exit_signals(
            current_price=45.30,  # Profitable but not at target
            current_hour=15,
            current_minute=45,
        )

        assert result is not None
        assert "TIME_EXIT" in result.reason
        assert result.urgency == Urgency.IMMEDIATE

    def test_no_exit_price_in_range(self, engine_with_position):
        """Test no exit when price between stop and target."""
        result = engine_with_position.check_exit_signals(
            current_price=45.30,  # Above entry, below target
            current_hour=11,
            current_minute=30,
        )
        assert result is None

    def test_no_exit_before_force_time(self, engine_with_position):
        """Test no time exit before 3:45 PM."""
        result = engine_with_position.check_exit_signals(
            current_price=45.30,
            current_hour=15,
            current_minute=44,  # One minute before
        )
        assert result is None

    def test_no_exit_no_position(self, engine):
        """Test no exit signal when no position exists."""
        result = engine.check_exit_signals(
            current_price=44.00,
            current_hour=11,
            current_minute=30,
        )
        assert result is None


# =============================================================================
# FORCE EXIT TESTS
# =============================================================================


class TestForceExit:
    """Tests for force exit functionality."""

    def test_force_exit_at_1545(self, engine_with_position):
        """Test force exit triggers at 15:45."""
        result = engine_with_position.check_force_exit(
            current_hour=15,
            current_minute=45,
            current_price=45.50,
        )

        assert result is not None
        assert result.target_weight == 0.0
        assert result.urgency == Urgency.IMMEDIATE
        assert "TIME_EXIT" in result.reason

    def test_force_exit_after_1545(self, engine_with_position):
        """Test force exit still triggers after 15:45."""
        result = engine_with_position.check_force_exit(
            current_hour=15,
            current_minute=50,
            current_price=45.50,
        )
        assert result is not None

    def test_no_force_exit_before_1545(self, engine_with_position):
        """Test no force exit before 15:45."""
        result = engine_with_position.check_force_exit(
            current_hour=15,
            current_minute=44,
            current_price=45.50,
        )
        assert result is None

    def test_force_exit_no_position(self, engine):
        """Test no force exit when no position."""
        result = engine.check_force_exit(
            current_hour=15,
            current_minute=45,
            current_price=45.50,
        )
        assert result is None


# =============================================================================
# POSITION MANAGEMENT TESTS
# =============================================================================


class TestPositionManagement:
    """Tests for position registration and management."""

    def test_register_entry(self, engine):
        """Test position registration."""
        position = engine.register_entry(
            symbol="TQQQ",
            entry_price=45.00,
            entry_time="10:47:00",
            vwap=46.50,
        )

        assert position.symbol == "TQQQ"
        assert position.entry_price == 45.00
        assert position.entry_time == "10:47:00"
        assert position.vwap_at_entry == 46.50
        assert position.target_price == 45.90  # 45.00 * 1.02
        assert position.stop_price == 44.10  # 45.00 * 0.98

    def test_has_position(self, engine):
        """Test has_position check."""
        assert not engine.has_position()

        engine.register_entry("TQQQ", 45.00, "10:47:00", 46.50)
        assert engine.has_position()

    def test_get_position(self, engine):
        """Test get_position returns correct position."""
        engine.register_entry("TQQQ", 45.00, "10:47:00", 46.50)

        position = engine.get_position()
        assert position is not None
        assert position.symbol == "TQQQ"

    def test_get_position_symbol(self, engine):
        """Test get_position_symbol helper."""
        assert engine.get_position_symbol() is None

        engine.register_entry("SOXL", 28.50, "11:15:00", 29.00)
        assert engine.get_position_symbol() == "SOXL"

    def test_remove_position(self, engine_with_position):
        """Test position removal."""
        assert engine_with_position.has_position()

        removed = engine_with_position.remove_position()
        assert removed is not None
        assert removed.symbol == "TQQQ"
        assert not engine_with_position.has_position()

    def test_remove_nonexistent_position(self, engine):
        """Test removing nonexistent position returns None."""
        result = engine.remove_position()
        assert result is None

    def test_get_entry_price(self, engine_with_position):
        """Test get_entry_price helper."""
        assert engine_with_position.get_entry_price() == 45.00

    def test_get_target_price(self, engine_with_position):
        """Test get_target_price helper."""
        assert engine_with_position.get_target_price() == 45.90

    def test_get_stop_price(self, engine_with_position):
        """Test get_stop_price helper."""
        assert engine_with_position.get_stop_price() == 44.10

    def test_get_vwap_at_entry(self, engine_with_position):
        """Test get_vwap_at_entry helper."""
        assert engine_with_position.get_vwap_at_entry() == 46.50


# =============================================================================
# STATE PERSISTENCE TESTS
# =============================================================================


class TestStatePersistence:
    """Tests for state persistence and restoration."""

    def test_get_state_for_persistence_empty(self, engine):
        """Test state serialization with no position."""
        state = engine.get_state_for_persistence()
        assert "position" in state
        assert state["position"] is None

    def test_get_state_for_persistence_with_position(self, engine_with_position):
        """Test state serialization with position."""
        state = engine_with_position.get_state_for_persistence()
        assert state["position"] is not None
        assert state["position"]["symbol"] == "TQQQ"

    def test_restore_state(self, engine):
        """Test state restoration."""
        state = {
            "position": {
                "symbol": "SOXL",
                "entry_price": 28.50,
                "entry_time": "11:15:00",
                "vwap_at_entry": 29.00,
                "target_price": 29.07,
                "stop_price": 27.93,
            }
        }

        engine.restore_state(state)

        assert engine.has_position()
        position = engine.get_position()
        assert position.symbol == "SOXL"
        assert position.entry_price == 28.50

    def test_restore_state_empty(self, engine_with_position):
        """Test restoring empty state clears position."""
        assert engine_with_position.has_position()

        engine_with_position.restore_state({"position": None})
        assert not engine_with_position.has_position()

    def test_roundtrip_persistence(self, engine):
        """Test full save/restore cycle."""
        engine.register_entry("TQQQ", 45.00, "10:47:00", 46.50)

        state = engine.get_state_for_persistence()
        new_engine = MeanReversionEngine()
        new_engine.restore_state(state)

        position = new_engine.get_position()
        assert position.symbol == "TQQQ"
        assert position.entry_price == 45.00
        assert position.target_price == 45.90


# =============================================================================
# RESET TESTS
# =============================================================================


class TestReset:
    """Tests for engine reset."""

    def test_reset_clears_position(self, engine_with_position):
        """Test reset clears position."""
        assert engine_with_position.has_position()

        engine_with_position.reset()

        assert not engine_with_position.has_position()


# =============================================================================
# EDGE CASES
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_zero_open_price(self, engine):
        """Test handles zero open price gracefully."""
        params = make_entry_params(open_price=0)
        result = engine.check_entry_signal(**params)
        assert result is None

    def test_zero_avg_volume(self, engine):
        """Test handles zero average volume gracefully."""
        params = make_entry_params(avg_volume=0)
        result = engine.check_entry_signal(**params)
        assert result is None

    def test_target_and_vwap_priority(self, engine):
        """Test target is checked before VWAP."""
        # Position with VWAP below target
        engine.register_entry(
            symbol="TQQQ",
            entry_price=45.00,
            entry_time="10:47:00",
            vwap=45.50,  # VWAP below target (45.90)
        )

        # Price reaches target (which is above VWAP)
        result = engine.check_exit_signals(
            current_price=45.95,
            current_hour=11,
            current_minute=30,
        )

        assert result is not None
        assert "TARGET_HIT" in result.reason

    def test_vwap_exit_before_target(self, engine):
        """Test VWAP exit when VWAP is below target."""
        engine.register_entry(
            symbol="TQQQ",
            entry_price=45.00,
            entry_time="10:47:00",
            vwap=45.30,  # VWAP below target (45.90)
        )

        # Price reaches VWAP but not target
        result = engine.check_exit_signals(
            current_price=45.30,
            current_hour=11,
            current_minute=30,
        )

        assert result is not None
        assert "VWAP_HIT" in result.reason


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
        engine = MeanReversionEngine(algorithm=mock)

        engine.register_entry("TQQQ", 45.00, "10:47:00", 46.50)

        assert len(mock.logs) > 0
        assert any("POSITION_REGISTERED" in log for log in mock.logs)

    def test_logging_without_algorithm(self, engine):
        """Test that engine works without algorithm (no crash)."""
        engine.register_entry("TQQQ", 45.00, "10:47:00", 46.50)
        engine.check_exit_signals(45.95, 11, 30)
        engine.remove_position()
        engine.reset()
