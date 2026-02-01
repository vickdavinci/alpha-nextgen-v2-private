"""
Unit tests for Yield Sleeve.

Tests SHV idle cash management:
- Unallocated cash calculation
- Minimum threshold logic
- Lockbox integration
- Liquidation priority
- EOD urgency

Spec: docs/10-yield-sleeve.md
"""

import pytest

import config
from engines.satellite.yield_sleeve import YieldSleeve, YieldState
from models.enums import Urgency
from models.target_weight import TargetWeight


class TestYieldState:
    """Tests for YieldState dataclass."""

    def test_state_creation(self):
        """Test YieldState can be created."""
        state = YieldState(
            shv_target_pct=0.35,
            unallocated_cash=35000.0,
            locked_amount=10500.0,
            available_shv=49000.0,
        )
        assert state.shv_target_pct == 0.35
        assert state.unallocated_cash == 35000.0
        assert state.locked_amount == 10500.0
        assert state.available_shv == 49000.0

    def test_state_to_dict(self):
        """Test serialization to dict."""
        state = YieldState(
            shv_target_pct=0.25,
            unallocated_cash=25000.0,
            locked_amount=0.0,
            available_shv=40000.0,
        )
        data = state.to_dict()
        assert data["shv_target_pct"] == 0.25
        assert data["unallocated_cash"] == 25000.0
        assert data["locked_amount"] == 0.0
        assert data["available_shv"] == 40000.0

    def test_state_from_dict(self):
        """Test deserialization from dict."""
        data = {
            "shv_target_pct": 0.60,
            "unallocated_cash": 60000.0,
            "locked_amount": 10000.0,
            "available_shv": 50000.0,
        }
        state = YieldState.from_dict(data)
        assert state.shv_target_pct == 0.60
        assert state.unallocated_cash == 60000.0
        assert state.locked_amount == 10000.0
        assert state.available_shv == 50000.0


class TestYieldSleeveInit:
    """Tests for YieldSleeve initialization."""

    def test_engine_creation(self):
        """Test engine can be created without algorithm."""
        engine = YieldSleeve()
        assert engine.algorithm is None
        assert engine._last_state is None

    def test_engine_instrument(self):
        """Test engine manages correct instrument."""
        engine = YieldSleeve()
        assert engine.INSTRUMENT == "SHV"


class TestUnallocatedCashCalculation:
    """Tests for unallocated cash calculation."""

    @pytest.fixture
    def engine(self):
        """Create engine for testing."""
        return YieldSleeve()

    def test_basic_calculation(self, engine):
        """Test basic unallocated cash calculation."""
        # Total = $120,000
        # Non-SHV positions = $47,000 (QLD $35,000 + TMF $12,000)
        # Current SHV = $40,000
        # V2.3.17: Cash buffer = $120k * 10% = $12,000
        # Unallocated = $120,000 - $47,000 - $40,000 - $12,000 = $21,000
        unallocated = engine.calculate_unallocated_cash(
            total_equity=120000.0,
            non_shv_positions_value=47000.0,
            current_shv_value=40000.0,
        )
        assert unallocated == 21000.0

    def test_no_unallocated_cash(self, engine):
        """Test when all equity is allocated."""
        unallocated = engine.calculate_unallocated_cash(
            total_equity=100000.0,
            non_shv_positions_value=60000.0,
            current_shv_value=40000.0,
        )
        assert unallocated == 0.0

    def test_negative_result_capped_at_zero(self, engine):
        """Test negative unallocated cash is capped at zero."""
        # Over-allocated scenario
        unallocated = engine.calculate_unallocated_cash(
            total_equity=100000.0,
            non_shv_positions_value=80000.0,
            current_shv_value=30000.0,  # Total positions > equity
        )
        assert unallocated == 0.0

    def test_all_cash_no_positions(self, engine):
        """Test when portfolio is all cash (algorithm start)."""
        # V2.3.17: Cash buffer = $50k * 10% = $5,000 reserved
        # Unallocated = $50k - $0 - $0 - $5k = $45,000
        unallocated = engine.calculate_unallocated_cash(
            total_equity=50000.0,
            non_shv_positions_value=0.0,
            current_shv_value=0.0,
        )
        assert unallocated == 45000.0


class TestAvailableSHV:
    """Tests for available SHV calculation (excluding lockbox)."""

    @pytest.fixture
    def engine(self):
        """Create engine for testing."""
        return YieldSleeve()

    def test_available_without_lockbox(self, engine):
        """Test available SHV when no lockbox."""
        available = engine.get_available_shv(
            current_shv_value=40000.0,
            locked_amount=0.0,
        )
        assert available == 40000.0

    def test_available_with_lockbox(self, engine):
        """Test available SHV with lockbox."""
        available = engine.get_available_shv(
            current_shv_value=59500.0,
            locked_amount=10500.0,
        )
        assert available == 49000.0

    def test_available_all_locked(self, engine):
        """Test when all SHV is locked."""
        available = engine.get_available_shv(
            current_shv_value=10000.0,
            locked_amount=10000.0,
        )
        assert available == 0.0

    def test_locked_exceeds_shv(self, engine):
        """Test when locked amount exceeds SHV (edge case)."""
        # Shouldn't happen but handle gracefully
        available = engine.get_available_shv(
            current_shv_value=5000.0,
            locked_amount=10000.0,
        )
        assert available == 0.0


class TestYieldSignal:
    """Tests for yield signal generation."""

    @pytest.fixture
    def engine(self):
        """Create engine for testing."""
        return YieldSleeve()

    def test_signal_above_threshold(self, engine):
        """Test signal generated when unallocated > $10,000 (V2.3.6 threshold)."""
        signal = engine.get_yield_signal(
            total_equity=100000.0,
            tradeable_equity=100000.0,
            non_shv_positions_value=60000.0,
            current_shv_value=5000.0,
            locked_amount=0.0,
        )
        # V2.3.17: Cash buffer = $100k * 10% = $10k reserved
        # Unallocated = $100k - $60k - $5k - $10k = $25k
        # Target SHV = $5k + $25k = $30k
        # Target weight = $30k / $100k = 30%
        assert signal is not None
        assert signal.symbol == "SHV"
        assert signal.target_weight == 0.30
        assert signal.source == "YIELD"
        assert signal.urgency == Urgency.EOD

    def test_no_signal_below_threshold(self, engine):
        """Test no signal when unallocated <= $2,000."""
        signal = engine.get_yield_signal(
            total_equity=100000.0,
            tradeable_equity=100000.0,
            non_shv_positions_value=60000.0,
            current_shv_value=38500.0,  # Unallocated = $1,500
            locked_amount=0.0,
        )
        assert signal is None

    def test_no_signal_at_threshold(self, engine):
        """Test no signal when unallocated exactly $2,000."""
        signal = engine.get_yield_signal(
            total_equity=100000.0,
            tradeable_equity=100000.0,
            non_shv_positions_value=60000.0,
            current_shv_value=38000.0,  # Unallocated = $2,000
            locked_amount=0.0,
        )
        assert signal is None  # <= threshold, no signal

    def test_signal_with_lockbox(self, engine):
        """Test signal generation with lockbox."""
        # V2.3.17: Cash buffer = $150k * 10% = $15k reserved
        # Unallocated = $150k - $60k - $59.5k - $15k = $15.5k (above $10k threshold)
        signal = engine.get_yield_signal(
            total_equity=150000.0,
            tradeable_equity=139500.0,  # After lockbox
            non_shv_positions_value=60000.0,  # Reduced to allow signal
            current_shv_value=59500.0,
            locked_amount=10500.0,
        )
        assert signal is not None
        assert signal.symbol == "SHV"
        assert "Unallocated cash" in signal.reason

    def test_signal_capped_at_100_percent(self, engine):
        """Test target weight is capped at 100%."""
        signal = engine.get_yield_signal(
            total_equity=100000.0,
            tradeable_equity=50000.0,  # Reduced tradeable due to lockbox
            non_shv_positions_value=0.0,
            current_shv_value=10000.0,
            locked_amount=50000.0,
        )
        # This would calculate >100% but should be capped
        if signal is not None:
            assert signal.target_weight <= 1.0

    def test_no_signal_zero_tradeable_equity(self, engine):
        """Test no signal when tradeable equity is zero."""
        signal = engine.get_yield_signal(
            total_equity=100000.0,
            tradeable_equity=0.0,
            non_shv_positions_value=0.0,
            current_shv_value=0.0,
            locked_amount=100000.0,
        )
        assert signal is None

    def test_stores_last_state(self, engine):
        """Test that get_yield_signal stores state."""
        assert engine._last_state is None
        engine.get_yield_signal(
            total_equity=100000.0,
            tradeable_equity=100000.0,
            non_shv_positions_value=50000.0,
            current_shv_value=10000.0,
            locked_amount=0.0,
        )
        state = engine.get_last_state()
        assert state is not None
        # V2.3.17: Cash buffer = $100k * 10% = $10k reserved
        # Unallocated = $100k - $50k - $10k - $10k = $30k
        assert state.unallocated_cash == 30000.0


class TestLiquidationSignal:
    """Tests for SHV liquidation signals."""

    @pytest.fixture
    def engine(self):
        """Create engine for testing."""
        return YieldSleeve()

    def test_liquidation_signal_full_coverage(self, engine):
        """Test liquidation when SHV can cover full amount."""
        signal = engine.get_liquidation_signal(
            cash_needed=20000.0,
            current_shv_value=45000.0,
            locked_amount=0.0,
            tradeable_equity=100000.0,
        )
        assert signal is not None
        assert signal.symbol == "SHV"
        # Remaining = $45k - $20k = $25k
        # Target = $25k / $100k = 25%
        assert signal.target_weight == 0.25
        assert signal.urgency == Urgency.IMMEDIATE

    def test_liquidation_partial_coverage(self, engine):
        """Test liquidation when SHV can only partially cover."""
        signal = engine.get_liquidation_signal(
            cash_needed=50000.0,
            current_shv_value=30000.0,
            locked_amount=0.0,
            tradeable_equity=100000.0,
        )
        # Can only sell $30k of the $50k needed
        assert signal is not None
        # Remaining = $0
        assert signal.target_weight == 0.0

    def test_liquidation_respects_lockbox(self, engine):
        """Test liquidation respects locked amount."""
        signal = engine.get_liquidation_signal(
            cash_needed=30000.0,
            current_shv_value=59500.0,
            locked_amount=10500.0,
            tradeable_equity=100000.0,
        )
        # Available = $59.5k - $10.5k = $49k
        # Can sell $30k of the $49k available
        assert signal is not None
        # Remaining = $59.5k - $30k = $29.5k
        assert signal.target_weight == pytest.approx(0.295)

    def test_liquidation_blocked_all_locked(self, engine):
        """Test no liquidation when all SHV is locked."""
        signal = engine.get_liquidation_signal(
            cash_needed=5000.0,
            current_shv_value=10500.0,
            locked_amount=10500.0,
            tradeable_equity=100000.0,
        )
        assert signal is None

    def test_liquidation_includes_reason(self, engine):
        """Test liquidation signal has descriptive reason."""
        signal = engine.get_liquidation_signal(
            cash_needed=15000.0,
            current_shv_value=40000.0,
            locked_amount=0.0,
            tradeable_equity=100000.0,
        )
        assert "Liquidation" in signal.reason
        assert "$15,000" in signal.reason


class TestCashProvision:
    """Tests for cash provision checking."""

    @pytest.fixture
    def engine(self):
        """Create engine for testing."""
        return YieldSleeve()

    def test_can_provide_full_amount(self, engine):
        """Test can_provide_cash returns True when sufficient."""
        result = engine.can_provide_cash(
            amount=20000.0,
            current_shv_value=50000.0,
            locked_amount=10000.0,
        )
        # Available = $50k - $10k = $40k >= $20k
        assert result is True

    def test_cannot_provide_full_amount(self, engine):
        """Test can_provide_cash returns False when insufficient."""
        result = engine.can_provide_cash(
            amount=50000.0,
            current_shv_value=40000.0,
            locked_amount=10000.0,
        )
        # Available = $40k - $10k = $30k < $50k
        assert result is False

    def test_max_liquidatable_without_lockbox(self, engine):
        """Test max liquidatable without lockbox."""
        max_liq = engine.get_max_liquidatable(
            current_shv_value=40000.0,
            locked_amount=0.0,
        )
        assert max_liq == 40000.0

    def test_max_liquidatable_with_lockbox(self, engine):
        """Test max liquidatable with lockbox."""
        max_liq = engine.get_max_liquidatable(
            current_shv_value=59500.0,
            locked_amount=10500.0,
        )
        assert max_liq == 49000.0


class TestPersistence:
    """Tests for state persistence."""

    @pytest.fixture
    def engine(self):
        """Create engine for testing."""
        return YieldSleeve()

    def test_get_state_when_empty(self, engine):
        """Test get_state_for_persistence when no state."""
        state = engine.get_state_for_persistence()
        assert state["last_state"] is None

    def test_get_state_after_signal(self, engine):
        """Test get_state_for_persistence after generating signal."""
        engine.get_yield_signal(
            total_equity=100000.0,
            tradeable_equity=100000.0,
            non_shv_positions_value=50000.0,
            current_shv_value=10000.0,
            locked_amount=0.0,
        )
        state = engine.get_state_for_persistence()
        assert state["last_state"] is not None
        # V2.3.17: Cash buffer = $100k * 10% = $10k reserved
        # Unallocated = $100k - $50k - $10k - $10k = $30k
        assert state["last_state"]["unallocated_cash"] == 30000.0

    def test_restore_state(self, engine):
        """Test restore_state correctly loads state."""
        state = {
            "last_state": {
                "shv_target_pct": 0.35,
                "unallocated_cash": 35000.0,
                "locked_amount": 10000.0,
                "available_shv": 40000.0,
            }
        }
        engine.restore_state(state)
        last = engine.get_last_state()
        assert last is not None
        assert last.shv_target_pct == 0.35
        assert last.unallocated_cash == 35000.0

    def test_restore_empty_state(self, engine):
        """Test restore_state handles empty state."""
        engine.restore_state({})
        assert engine.get_last_state() is None

    def test_round_trip_persistence(self, engine):
        """Test state survives save/restore cycle."""
        engine.get_yield_signal(
            total_equity=120000.0,
            tradeable_equity=120000.0,
            non_shv_positions_value=47000.0,
            current_shv_value=40000.0,
            locked_amount=0.0,
        )
        state = engine.get_state_for_persistence()

        new_engine = YieldSleeve()
        new_engine.restore_state(state)

        original = engine.get_last_state()
        restored = new_engine.get_last_state()
        assert original.shv_target_pct == restored.shv_target_pct
        assert original.unallocated_cash == restored.unallocated_cash


class TestReset:
    """Tests for engine reset."""

    def test_reset_clears_state(self):
        """Test reset clears last state."""
        engine = YieldSleeve()
        engine.get_yield_signal(
            total_equity=100000.0,
            tradeable_equity=100000.0,
            non_shv_positions_value=50000.0,
            current_shv_value=10000.0,
            locked_amount=0.0,
        )
        assert engine.get_last_state() is not None

        engine.reset()
        assert engine.get_last_state() is None


class TestHelperMethods:
    """Tests for helper methods."""

    def test_get_minimum_trade_threshold(self):
        """Test get_minimum_trade_threshold returns config value."""
        engine = YieldSleeve()
        assert engine.get_minimum_trade_threshold() == config.SHV_MIN_TRADE
        # V2.3.6: Changed from 2000 to 10000 to reduce SHV churn


class TestTargetWeightValidation:
    """Tests that Yield Sleeve correctly emits TargetWeight objects."""

    @pytest.fixture
    def engine(self):
        """Create engine for testing."""
        return YieldSleeve()

    def test_target_weight_valid_source(self, engine):
        """Test signal uses valid YIELD source."""
        signal = engine.get_yield_signal(
            total_equity=100000.0,
            tradeable_equity=100000.0,
            non_shv_positions_value=50000.0,
            current_shv_value=10000.0,
            locked_amount=0.0,
        )
        assert isinstance(signal, TargetWeight)
        assert signal.source == "YIELD"

    def test_target_weight_valid_symbol(self, engine):
        """Test signal uses SHV symbol."""
        signal = engine.get_yield_signal(
            total_equity=100000.0,
            tradeable_equity=100000.0,
            non_shv_positions_value=50000.0,
            current_shv_value=10000.0,
            locked_amount=0.0,
        )
        assert signal.symbol == "SHV"

    def test_target_weight_valid_range(self, engine):
        """Test target weights are within valid range."""
        signal = engine.get_yield_signal(
            total_equity=100000.0,
            tradeable_equity=100000.0,
            non_shv_positions_value=20000.0,  # Most is unallocated
            current_shv_value=10000.0,
            locked_amount=0.0,
        )
        assert 0.0 <= signal.target_weight <= 1.0


class TestRiskOffScenario:
    """Tests for RISK_OFF scenario with high SHV allocation."""

    @pytest.fixture
    def engine(self):
        """Create engine for testing."""
        return YieldSleeve()

    def test_high_allocation_risk_off(self, engine):
        """Test high SHV allocation in RISK_OFF (all longs closed)."""
        # Only hedges active, rest goes to SHV
        signal = engine.get_yield_signal(
            total_equity=100000.0,
            tradeable_equity=100000.0,
            non_shv_positions_value=30000.0,  # Only TMF + PSQ
            current_shv_value=5000.0,
            locked_amount=0.0,
        )
        # V2.3.17: Cash buffer = $100k * 10% = $10k reserved
        # Unallocated = $100k - $30k - $5k - $10k = $55k
        # Target = ($5k + $55k) / $100k = 60%
        assert signal is not None
        assert signal.target_weight == pytest.approx(0.60)

    def test_algorithm_start_all_cash(self, engine):
        """Test algorithm start with all cash goes to SHV."""
        signal = engine.get_yield_signal(
            total_equity=50000.0,
            tradeable_equity=50000.0,
            non_shv_positions_value=0.0,
            current_shv_value=0.0,
            locked_amount=0.0,
        )
        # V2.3.17: Cash buffer = $50k * 10% = $5k reserved
        # Unallocated = $50k - $0 - $0 - $5k = $45k
        # Target SHV = ($0 + $45k) / $50k = 90%
        assert signal is not None
        assert signal.target_weight == 0.9


class TestEdgeCases:
    """Tests for edge cases."""

    @pytest.fixture
    def engine(self):
        """Create engine for testing."""
        return YieldSleeve()

    def test_negative_tradeable_equity(self, engine):
        """Test handling of negative tradeable equity."""
        signal = engine.get_yield_signal(
            total_equity=100000.0,
            tradeable_equity=-5000.0,  # Shouldn't happen but handle
            non_shv_positions_value=50000.0,
            current_shv_value=10000.0,
            locked_amount=0.0,
        )
        assert signal is None

    def test_liquidation_zero_tradeable_equity(self, engine):
        """Test liquidation with zero tradeable equity."""
        signal = engine.get_liquidation_signal(
            cash_needed=5000.0,
            current_shv_value=10000.0,
            locked_amount=0.0,
            tradeable_equity=0.0,
        )
        # Should still generate signal, target = 0
        assert signal is not None
        assert signal.target_weight == 0.0

    def test_very_small_unallocated(self, engine):
        """Test with very small unallocated cash."""
        signal = engine.get_yield_signal(
            total_equity=100000.0,
            tradeable_equity=100000.0,
            non_shv_positions_value=60000.0,
            current_shv_value=39500.0,  # Unallocated = $500
            locked_amount=0.0,
        )
        assert signal is None  # Below threshold
