"""
Tests for Trend Engine - V2 MA200 + ADX trend-following strategy.

Tests cover:
- TrendPosition dataclass (creation, serialization, deserialization)
- ADX scoring function (weak/moderate/strong/very strong)
- Entry signal conditions (MA200, ADX >= 25, regime, cold start)
- Exit signals (MA200 cross, ADX exhaustion, regime exit, stop hit)
- Chandelier stop calculation and tiered multipliers
- Stop never moves down rule
- Position registration and management
- State persistence and restoration

Spec: docs/07-trend-engine.md, docs/v2-specs/V2_1_COMPLETE_ARCHITECTURE.txt
"""

import pytest

import config
from engines.core.trend_engine import TrendEngine, TrendPosition, adx_score
from models.enums import Urgency
from models.target_weight import TargetWeight

# =============================================================================
# ADX SCORING FUNCTION TESTS
# =============================================================================


class TestADXScore:
    """Tests for ADX scoring function (V2.1 spec)."""

    def test_adx_very_strong(self):
        """Test ADX >= 35 returns score 1.0 (very strong)."""
        assert adx_score(35.0) == 1.0
        assert adx_score(40.0) == 1.0
        assert adx_score(50.0) == 1.0

    def test_adx_strong(self):
        """Test ADX 25-35 returns score 0.75 (strong)."""
        assert adx_score(25.0) == 0.75
        assert adx_score(30.0) == 0.75
        assert adx_score(34.9) == 0.75

    def test_adx_moderate(self):
        """Test ADX 20-25 returns score 0.50 (moderate)."""
        assert adx_score(20.0) == 0.50
        assert adx_score(22.0) == 0.50
        assert adx_score(24.9) == 0.50

    def test_adx_weak(self):
        """Test ADX < 20 returns score 0.25 (weak)."""
        assert adx_score(19.9) == 0.25
        assert adx_score(15.0) == 0.25
        assert adx_score(10.0) == 0.25
        assert adx_score(0.0) == 0.25


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
        """Test INSTRUMENTS class variable (V2.2: 4 symbols for diversification)."""
        assert "QLD" in TrendEngine.INSTRUMENTS
        assert "SSO" in TrendEngine.INSTRUMENTS
        assert "TNA" in TrendEngine.INSTRUMENTS  # V2.2: 3× Russell 2000
        assert "FAS" in TrendEngine.INSTRUMENTS  # V2.2: 3× Financials
        assert len(TrendEngine.INSTRUMENTS) == 4

    def test_initial_state_empty(self, engine):
        """Test engine starts with no positions."""
        assert len(engine.get_all_positions()) == 0
        assert not engine.has_position("QLD")
        assert not engine.has_position("SSO")
        assert not engine.has_position("TNA")
        assert not engine.has_position("FAS")


# =============================================================================
# ENTRY SIGNAL TESTS
# =============================================================================


class TestEntrySignals:
    """Tests for V2 entry signal detection (MA200 + ADX)."""

    def test_valid_entry_all_conditions_met(self, engine):
        """Test entry signal when all V2 conditions are satisfied."""
        # Close > MA200, ADX >= 25 (score >= 0.50), regime >= 40
        result = engine.check_entry_signal(
            symbol="QLD",
            close=105.0,  # Above MA200
            ma200=100.0,
            adx=30.0,  # Strong (score = 0.75)
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
        assert "MA200+ADX Entry" in result.reason
        assert "STRONG" in result.reason

    def test_entry_moderate_adx(self, engine):
        """Test entry with moderate ADX (20-25, score = 0.50)."""
        result = engine.check_entry_signal(
            symbol="QLD",
            close=105.0,
            ma200=100.0,
            adx=22.0,  # Moderate (score = 0.50)
            regime_score=55.0,
            is_cold_start_active=False,
            has_warm_entry=False,
            atr=2.0,
            current_date="2024-01-15",
        )
        # Score 0.50 meets threshold
        assert result is not None
        assert "MODERATE" in result.reason

    def test_entry_blocked_below_ma200(self, engine):
        """Test entry blocked when close <= MA200."""
        result = engine.check_entry_signal(
            symbol="QLD",
            close=98.0,  # Below MA200
            ma200=100.0,
            adx=30.0,
            regime_score=60.0,
            is_cold_start_active=False,
            has_warm_entry=False,
            atr=2.0,
            current_date="2024-01-15",
        )
        assert result is None

    def test_entry_blocked_close_equals_ma200(self, engine):
        """Test entry blocked when close exactly equals MA200."""
        result = engine.check_entry_signal(
            symbol="QLD",
            close=100.0,  # Equal to MA200
            ma200=100.0,
            adx=30.0,
            regime_score=60.0,
            is_cold_start_active=False,
            has_warm_entry=False,
            atr=2.0,
            current_date="2024-01-15",
        )
        assert result is None

    def test_entry_blocked_weak_adx(self, engine):
        """Test entry blocked when ADX < 20 (score < 0.50)."""
        result = engine.check_entry_signal(
            symbol="QLD",
            close=105.0,
            ma200=100.0,
            adx=18.0,  # Weak (score = 0.25 < 0.50)
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
            ma200=100.0,
            adx=30.0,
            regime_score=35.0,  # < 40
            is_cold_start_active=False,
            has_warm_entry=False,
            atr=2.0,
            current_date="2024-01-15",
        )
        assert result is None

    def test_entry_allowed_regime_at_boundary(self, engine):
        """Test entry allowed when regime exactly 40."""
        result = engine.check_entry_signal(
            symbol="QLD",
            close=105.0,
            ma200=100.0,
            adx=30.0,
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
            ma200=100.0,
            adx=30.0,
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
            ma200=100.0,
            adx=30.0,
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
            ma200=100.0,
            adx=30.0,
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
            ma200=100.0,
            adx=30.0,
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
            ma200=50.0,
            adx=28.0,  # Strong (score = 0.75)
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
    """Tests for V2 exit signal detection (MA200, ADX, regime)."""

    def test_exit_ma200_close_below(self, engine_with_position):
        """Test exit when close < MA200 (trend reversal)."""
        result = engine_with_position.check_exit_signals(
            symbol="QLD",
            close=98.0,  # Below MA200
            high=102.0,
            ma200=100.0,
            adx=30.0,  # Strong ADX
            regime_score=60.0,
            atr=2.0,
        )

        assert result is not None
        assert result.symbol == "QLD"
        assert result.target_weight == 0.0
        assert result.urgency == Urgency.EOD
        assert "MA200_EXIT" in result.reason

    def test_no_exit_close_above_ma200(self, engine_with_position):
        """Test no exit when close >= MA200."""
        result = engine_with_position.check_exit_signals(
            symbol="QLD",
            close=101.0,  # Above MA200
            high=103.0,
            ma200=100.0,
            adx=30.0,
            regime_score=60.0,
            atr=2.0,
        )
        assert result is None

    def test_exit_adx_exhaustion(self, engine_with_position):
        """Test exit when ADX < 20 (momentum exhaustion)."""
        result = engine_with_position.check_exit_signals(
            symbol="QLD",
            close=105.0,  # Above MA200
            high=106.0,
            ma200=100.0,
            adx=18.0,  # Below 20 - momentum exhaustion
            regime_score=60.0,
            atr=2.0,
        )

        assert result is not None
        assert result.target_weight == 0.0
        assert "ADX_EXIT" in result.reason

    def test_exit_regime_below_30(self, engine_with_position):
        """Test exit when regime < 30."""
        result = engine_with_position.check_exit_signals(
            symbol="QLD",
            close=105.0,  # Above MA200
            high=106.0,
            ma200=100.0,
            adx=30.0,
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
            ma200=100.0,
            adx=30.0,
            regime_score=30.0,  # Exactly at threshold
            atr=2.0,
        )
        assert result is None

    def test_exit_no_position(self, engine):
        """Test no exit signal when no position exists."""
        result = engine.check_exit_signals(
            symbol="QLD",
            close=98.0,  # Would trigger MA200 exit
            high=102.0,
            ma200=100.0,
            adx=18.0,  # Would trigger ADX exit
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
            ma200=100.0,
            adx=30.0,
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
            ma200=100.0,
            adx=30.0,
            regime_score=60.0,
            atr=2.0,
        )
        assert engine_with_position.get_highest_high("QLD") == 115.0

        # Second update with lower high
        engine_with_position.check_exit_signals(
            symbol="QLD",
            close=110.0,
            high=112.0,  # Lower than previous HH
            ma200=100.0,
            adx=30.0,
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
            ma200=100.0,
            adx=30.0,
            regime_score=60.0,
            atr=2.0,
        )

        new_stop = engine.get_stop_level("QLD")
        # V2: 10% profit uses tight mult (2.5), so: 110 - (2.5 * 2.0) = 105
        assert new_stop == 105.0
        assert new_stop > initial_stop

    def test_stop_never_moves_down(self, engine):
        """Test stop only moves up, never down."""
        engine.register_entry(
            symbol="QLD",
            entry_price=100.0,
            entry_date="2024-01-15",
            atr=2.0,
        )

        # Move up first - 20% profit uses tighter multiplier (2.0)
        engine.check_exit_signals(
            symbol="QLD",
            close=115.0,
            high=120.0,
            ma200=100.0,
            adx=30.0,
            regime_score=60.0,
            atr=2.0,
        )
        high_stop = engine.get_stop_level("QLD")
        # V2: 20% profit uses tighter mult (2.0), so: 120 - (2.0 * 2.0) = 116
        assert high_stop == 116.0

        # Price drops but stop should not
        engine.check_exit_signals(
            symbol="QLD",
            close=105.0,
            high=106.0,
            ma200=100.0,
            adx=30.0,
            regime_score=60.0,
            atr=2.0,
        )
        # Highest high unchanged (120), stop should stay at 116
        assert engine.get_stop_level("QLD") == high_stop

    def test_tiered_multiplier_base_level(self, engine):
        """Test base multiplier (3.0) for < 10% profit (V2)."""
        engine.register_entry(
            symbol="QLD",
            entry_price=100.0,
            entry_date="2024-01-15",
            atr=2.0,
        )
        # Move up 5% (below 10% threshold)
        engine.check_exit_signals(
            symbol="QLD",
            close=103.0,
            high=105.0,  # 5% profit
            ma200=100.0,
            adx=30.0,
            regime_score=60.0,
            atr=2.0,
        )
        # V2: Stop = 105 - (3.0 * 2.0) = 99
        assert engine.get_stop_level("QLD") == 99.0

    def test_tiered_multiplier_tight_level(self, engine):
        """Test tight multiplier (2.5) for 10-20% profit (V2)."""
        engine.register_entry(
            symbol="QLD",
            entry_price=100.0,
            entry_date="2024-01-15",
            atr=2.0,
        )
        # Move up 15% (between 10% and 20%)
        engine.check_exit_signals(
            symbol="QLD",
            close=113.0,
            high=115.0,  # 15% profit
            ma200=100.0,
            adx=30.0,
            regime_score=60.0,
            atr=2.0,
        )
        # V2: Stop = 115 - (2.5 * 2.0) = 110
        assert engine.get_stop_level("QLD") == 110.0

    def test_tiered_multiplier_tighter_level(self, engine):
        """Test tighter multiplier (2.0) for > 20% profit (V2)."""
        engine.register_entry(
            symbol="QLD",
            entry_price=100.0,
            entry_date="2024-01-15",
            atr=2.0,
        )
        # Move up 30% (above 20%)
        engine.check_exit_signals(
            symbol="QLD",
            close=128.0,
            high=130.0,  # 30% profit
            ma200=100.0,
            adx=30.0,
            regime_score=60.0,
            atr=2.0,
        )
        # V2: Stop = 130 - (2.0 * 2.0) = 126
        assert engine.get_stop_level("QLD") == 126.0


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
            ma200=100.0,
            adx=30.0,
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

    def test_adx_exactly_at_entry_threshold(self, engine):
        """Test ADX exactly at entry threshold (25)."""
        # ADX = 25 should give score 0.75 which is >= 0.50
        result = engine.check_entry_signal(
            symbol="QLD",
            close=105.0,
            ma200=100.0,
            adx=25.0,  # Exactly at strong threshold
            regime_score=60.0,
            is_cold_start_active=False,
            has_warm_entry=False,
            atr=2.0,
            current_date="2024-01-15",
        )
        assert result is not None

    def test_adx_exactly_at_moderate_threshold(self, engine):
        """Test ADX exactly at moderate threshold (20)."""
        # ADX = 20 should give score 0.50 which is exactly at threshold
        result = engine.check_entry_signal(
            symbol="QLD",
            close=105.0,
            ma200=100.0,
            adx=20.0,  # Exactly at moderate threshold
            regime_score=60.0,
            is_cold_start_active=False,
            has_warm_entry=False,
            atr=2.0,
            current_date="2024-01-15",
        )
        assert result is not None

    def test_adx_just_below_threshold(self, engine):
        """Test ADX just below entry threshold."""
        # ADX = 19.9 gives score 0.25 which is < 0.50
        result = engine.check_entry_signal(
            symbol="QLD",
            close=105.0,
            ma200=100.0,
            adx=19.9,  # Just below moderate threshold
            regime_score=60.0,
            is_cold_start_active=False,
            has_warm_entry=False,
            atr=2.0,
            current_date="2024-01-15",
        )
        assert result is None

    def test_multiple_positions_different_symbols(self, engine):
        """Test managing multiple positions simultaneously."""
        engine.register_entry("QLD", 100.0, "2024-01-15", 2.0)
        engine.register_entry("SSO", 50.0, "2024-01-16", 1.0)

        # Check exits for each
        result_qld = engine.check_exit_signals(
            symbol="QLD",
            close=98.0,  # Exit signal (below MA200)
            high=100.0,
            ma200=100.0,
            adx=30.0,
            regime_score=60.0,
            atr=2.0,
        )
        assert result_qld is not None
        assert result_qld.symbol == "QLD"

        result_sso = engine.check_exit_signals(
            symbol="SSO",
            close=55.0,  # No exit (above MA200)
            high=56.0,
            ma200=50.0,
            adx=30.0,
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

        # 50% profit - should use tighter multiplier (2.0 in V2)
        engine.check_exit_signals(
            symbol="QLD",
            close=148.0,
            high=150.0,
            ma200=100.0,
            adx=30.0,
            regime_score=60.0,
            atr=2.0,
        )
        # V2: Stop = 150 - (2.0 * 2.0) = 146
        assert engine.get_stop_level("QLD") == 146.0


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
        engine.check_exit_signals(
            symbol="QLD",
            close=110.0,
            high=112.0,
            ma200=100.0,
            adx=30.0,
            regime_score=60.0,
            atr=2.0,
        )
        engine.remove_position("QLD")
        engine.reset()


# =============================================================================
# INDICATOR READINESS TESTS (Blocker #2 fix)
# =============================================================================


class TestIndicatorReadiness:
    """Tests for indicator readiness validation (None/NaN handling)."""

    def test_entry_blocked_ma200_none(self, engine):
        """Test entry blocked when MA200 is None."""
        result = engine.check_entry_signal(
            symbol="QLD",
            close=105.0,
            ma200=None,  # Not ready
            adx=28.0,
            regime_score=60.0,
            is_cold_start_active=False,
            has_warm_entry=False,
            atr=2.0,
            current_date="2024-01-15",
        )
        assert result is None

    def test_entry_blocked_adx_none(self, engine):
        """Test entry blocked when ADX is None."""
        result = engine.check_entry_signal(
            symbol="QLD",
            close=105.0,
            ma200=100.0,
            adx=None,  # Not ready
            regime_score=60.0,
            is_cold_start_active=False,
            has_warm_entry=False,
            atr=2.0,
            current_date="2024-01-15",
        )
        assert result is None

    def test_entry_blocked_atr_none(self, engine):
        """Test entry blocked when ATR is None."""
        result = engine.check_entry_signal(
            symbol="QLD",
            close=105.0,
            ma200=100.0,
            adx=28.0,
            regime_score=60.0,
            is_cold_start_active=False,
            has_warm_entry=False,
            atr=None,  # Not ready
            current_date="2024-01-15",
        )
        assert result is None

    def test_entry_blocked_atr_zero(self, engine):
        """Test entry blocked when ATR is zero."""
        result = engine.check_entry_signal(
            symbol="QLD",
            close=105.0,
            ma200=100.0,
            adx=28.0,
            regime_score=60.0,
            is_cold_start_active=False,
            has_warm_entry=False,
            atr=0.0,  # Invalid - zero ATR
            current_date="2024-01-15",
        )
        assert result is None

    def test_entry_blocked_ma200_nan(self, engine):
        """Test entry blocked when MA200 is NaN."""
        import math

        result = engine.check_entry_signal(
            symbol="QLD",
            close=105.0,
            ma200=float("nan"),  # NaN
            adx=28.0,
            regime_score=60.0,
            is_cold_start_active=False,
            has_warm_entry=False,
            atr=2.0,
            current_date="2024-01-15",
        )
        assert result is None

    def test_entry_blocked_close_none(self, engine):
        """Test entry blocked when close price is None."""
        result = engine.check_entry_signal(
            symbol="QLD",
            close=None,  # Not ready
            ma200=100.0,
            adx=28.0,
            regime_score=60.0,
            is_cold_start_active=False,
            has_warm_entry=False,
            atr=2.0,
            current_date="2024-01-15",
        )
        assert result is None

    def test_exit_safe_with_none_indicators(self, engine):
        """Test exit check returns None safely when indicators not ready."""
        engine.register_entry("QLD", 100.0, "2024-01-15", 2.0)

        result = engine.check_exit_signals(
            symbol="QLD",
            close=None,  # Not ready
            high=110.0,
            ma200=100.0,
            adx=30.0,
            regime_score=60.0,
            atr=2.0,
        )
        assert result is None  # No crash, returns None

    def test_exit_safe_with_nan_ma200(self, engine):
        """Test exit check handles NaN MA200 gracefully."""
        engine.register_entry("QLD", 100.0, "2024-01-15", 2.0)

        result = engine.check_exit_signals(
            symbol="QLD",
            close=110.0,
            high=112.0,
            ma200=float("nan"),  # NaN
            adx=30.0,
            regime_score=60.0,
            atr=2.0,
        )
        assert result is None  # No crash, returns None
