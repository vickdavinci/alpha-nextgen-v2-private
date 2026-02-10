"""
Unit tests for Hedge Engine.

V6.11: Updated for SH-only hedge architecture.

Tests regime-based hedge allocation:
- SH allocation for equity hedging (replaces TMF/PSQ)
- Scaling based on regime score
- EOD urgency for rebalancing
- Rebalancing threshold logic
- Panic mode immediate rebalance

Spec: docs/09-hedge-engine.md
"""

import pytest

import config
from engines.satellite.hedge_engine import HedgeAllocation, HedgeEngine
from models.enums import Urgency
from models.target_weight import TargetWeight


class TestHedgeAllocation:
    """Tests for HedgeAllocation dataclass (V6.11: SH only)."""

    def test_allocation_creation(self):
        """Test HedgeAllocation can be created."""
        alloc = HedgeAllocation(
            sh_target_pct=0.08,
            regime_score=35.0,
            hedge_tier="MEDIUM",
        )
        assert alloc.sh_target_pct == 0.08
        assert alloc.regime_score == 35.0
        assert alloc.hedge_tier == "MEDIUM"

    def test_allocation_to_dict(self):
        """Test serialization to dict."""
        alloc = HedgeAllocation(
            sh_target_pct=0.05,
            regime_score=45.0,
            hedge_tier="LIGHT",
        )
        data = alloc.to_dict()
        assert data["sh_target_pct"] == 0.05
        assert data["regime_score"] == 45.0
        assert data["hedge_tier"] == "LIGHT"

    def test_allocation_from_dict(self):
        """Test deserialization from dict."""
        data = {
            "sh_target_pct": 0.10,
            "regime_score": 25.0,
            "hedge_tier": "FULL",
        }
        alloc = HedgeAllocation.from_dict(data)
        assert alloc.sh_target_pct == 0.10
        assert alloc.regime_score == 25.0
        assert alloc.hedge_tier == "FULL"


class TestHedgeEngineInit:
    """Tests for HedgeEngine initialization."""

    def test_engine_creation(self):
        """Test engine can be created without algorithm."""
        engine = HedgeEngine()
        assert engine.algorithm is None
        assert engine._last_allocation is None

    def test_engine_instruments(self):
        """V6.11: Test engine manages SH only."""
        engine = HedgeEngine()
        assert "SH" in engine.INSTRUMENTS
        assert len(engine.INSTRUMENTS) == 1


class TestHedgeTierAllocation:
    """Tests for regime-based tier allocation logic (V6.11: SH only)."""

    @pytest.fixture
    def engine(self):
        """Create engine for testing."""
        return HedgeEngine()

    def test_tier_none_regime_above_50(self, engine):
        """V6.11: Test no hedge when regime >= 50 (Neutral+)."""
        for score in [50.0, 60.0, 70.0, 100.0]:
            sh, tier = engine.get_target_allocations(score)
            assert sh == 0.0, f"SH should be 0 at regime {score}"
            assert tier == "NONE", f"Tier should be NONE at regime {score}"

    def test_tier_light_regime_40_to_49(self, engine):
        """V6.11: Test light hedge when regime 40-49 (Cautious)."""
        for score in [40.0, 45.0, 49.0, 49.9]:
            sh, tier = engine.get_target_allocations(score)
            assert sh == config.SH_LIGHT, f"SH should be {config.SH_LIGHT} at regime {score}"
            assert tier == "LIGHT", f"Tier should be LIGHT at regime {score}"

    def test_tier_medium_regime_30_to_39(self, engine):
        """V6.11: Test medium hedge when regime 30-39 (Defensive)."""
        for score in [30.0, 35.0, 39.0, 39.9]:
            sh, tier = engine.get_target_allocations(score)
            assert sh == config.SH_MEDIUM, f"SH should be {config.SH_MEDIUM} at regime {score}"
            assert tier == "MEDIUM", f"Tier should be MEDIUM at regime {score}"

    def test_tier_full_regime_below_30(self, engine):
        """V6.11: Test full hedge when regime < 30 (Risk Off)."""
        for score in [0.0, 10.0, 20.0, 29.0, 29.9]:
            sh, tier = engine.get_target_allocations(score)
            assert sh == config.SH_FULL, f"SH should be {config.SH_FULL} at regime {score}"
            assert tier == "FULL", f"Tier should be FULL at regime {score}"

    def test_tier_boundaries(self, engine):
        """V6.11: Test exact boundary transitions."""
        # At exactly 50 - should be NONE
        sh, tier = engine.get_target_allocations(50.0)
        assert tier == "NONE"

        # Just below 50 - should be LIGHT
        sh, tier = engine.get_target_allocations(49.9)
        assert tier == "LIGHT"

        # At exactly 40 - should be LIGHT
        sh, tier = engine.get_target_allocations(40.0)
        assert tier == "LIGHT"

        # Just below 40 - should be MEDIUM
        sh, tier = engine.get_target_allocations(39.9)
        assert tier == "MEDIUM"

        # At exactly 30 - should be MEDIUM
        sh, tier = engine.get_target_allocations(30.0)
        assert tier == "MEDIUM"

        # Just below 30 - should be FULL
        sh, tier = engine.get_target_allocations(29.9)
        assert tier == "FULL"


class TestRebalanceThreshold:
    """Tests for rebalancing threshold logic."""

    @pytest.fixture
    def engine(self):
        """Create engine for testing."""
        return HedgeEngine()

    def test_rebalance_needed_above_threshold(self, engine):
        """Test rebalance needed when diff > 2%."""
        # Target 10%, current 0% -> 10% diff > 2%
        assert engine.check_rebalance_needed(0.10, 0.0) is True
        # Target 5%, current 10% -> 5% diff > 2%
        assert engine.check_rebalance_needed(0.05, 0.10) is True
        # Target 0%, current 5% -> 5% diff > 2%
        assert engine.check_rebalance_needed(0.0, 0.05) is True

    def test_no_rebalance_within_threshold(self, engine):
        """Test no rebalance when diff <= 2%."""
        # Target 5%, current 4% -> 1% diff <= 2%
        assert engine.check_rebalance_needed(0.05, 0.04) is False
        # Target 8%, current 9% -> 1% diff <= 2%
        assert engine.check_rebalance_needed(0.08, 0.09) is False

    def test_rebalance_at_exact_threshold(self, engine):
        """Test exact 2% threshold is NOT rebalanced."""
        # Use values that avoid floating point issues
        assert engine.check_rebalance_needed(0.05, 0.03001) is False


class TestHedgeSignals:
    """Tests for hedge signal generation (V6.11: SH only)."""

    @pytest.fixture
    def engine(self):
        """Create engine for testing."""
        return HedgeEngine()

    def test_signals_when_regime_drops_to_cautious(self, engine):
        """V6.11: Test SH signal generated when regime drops to CAUTIOUS tier."""
        signals = engine.get_hedge_signals(
            regime_score=45.0,
            current_sh_pct=0.0,
        )
        assert len(signals) == 1
        sh_signal = signals[0]
        assert sh_signal.symbol == "SH"
        assert sh_signal.target_weight == config.SH_LIGHT
        assert sh_signal.source == "HEDGE"
        assert sh_signal.urgency == Urgency.EOD

    def test_signals_when_regime_drops_to_defensive(self, engine):
        """V6.11: Test SH signal generated when regime drops to DEFENSIVE tier."""
        signals = engine.get_hedge_signals(
            regime_score=35.0,
            current_sh_pct=0.0,
        )
        assert len(signals) == 1
        sh_signal = signals[0]
        assert sh_signal.symbol == "SH"
        assert sh_signal.target_weight == config.SH_MEDIUM
        assert sh_signal.source == "HEDGE"

    def test_signals_when_regime_drops_to_risk_off_severe(self, engine):
        """V6.11: Test SH signal at full hedge level."""
        signals = engine.get_hedge_signals(
            regime_score=15.0,
            current_sh_pct=0.0,
        )
        assert len(signals) == 1
        assert signals[0].symbol == "SH"
        assert signals[0].target_weight == config.SH_FULL

    def test_no_signals_when_within_threshold(self, engine):
        """V6.11: Test no signals when position is within threshold."""
        signals = engine.get_hedge_signals(
            regime_score=45.0,  # SH target = 5%
            current_sh_pct=0.04,  # 1% diff < 2% threshold
        )
        assert len(signals) == 0

    def test_signals_when_regime_improves(self, engine):
        """V6.11: Test hedge reduction when regime improves."""
        signals = engine.get_hedge_signals(
            regime_score=50.0,  # SH target = 0%
            current_sh_pct=0.08,  # Need to reduce
        )
        assert len(signals) == 1
        assert signals[0].symbol == "SH"
        assert signals[0].target_weight == 0.0

    def test_stores_last_allocation(self, engine):
        """V6.11: Test that get_hedge_signals stores allocation state."""
        assert engine._last_allocation is None
        engine.get_hedge_signals(
            regime_score=35.0,
            current_sh_pct=0.0,
        )
        alloc = engine.get_last_allocation()
        assert alloc is not None
        assert alloc.regime_score == 35.0
        assert alloc.hedge_tier == "MEDIUM"


class TestPanicMode:
    """Tests for panic mode behavior (V6.11: SH only)."""

    @pytest.fixture
    def engine(self):
        """Create engine for testing."""
        return HedgeEngine()

    def test_panic_mode_immediate_urgency(self, engine):
        """V6.11: Test panic mode uses IMMEDIATE urgency."""
        signals = engine.get_panic_mode_signals(
            regime_score=25.0,
            current_sh_pct=0.0,
        )
        assert len(signals) == 1
        assert signals[0].urgency == Urgency.IMMEDIATE

    def test_panic_mode_bypasses_threshold(self, engine):
        """V6.11: Test panic mode generates signals even within threshold."""
        # Normal mode - no signal (within threshold)
        normal_signals = engine.get_hedge_signals(
            regime_score=45.0,
            current_sh_pct=0.04,  # Within 2% threshold of 5% target
            is_panic_mode=False,
        )
        assert len(normal_signals) == 0

        # Panic mode - signal generated anyway
        panic_signals = engine.get_hedge_signals(
            regime_score=45.0,
            current_sh_pct=0.04,
            is_panic_mode=True,
        )
        assert len(panic_signals) == 1

    def test_panic_mode_reason_includes_prefix(self, engine):
        """V6.11: Test panic mode signals include PANIC_MODE prefix in reason."""
        signals = engine.get_panic_mode_signals(
            regime_score=25.0,
            current_sh_pct=0.0,
        )
        assert "PANIC_MODE:" in signals[0].reason


class TestHedgeEngineTargetWeight:
    """Tests that Hedge Engine correctly emits TargetWeight objects (V6.11: SH only)."""

    @pytest.fixture
    def engine(self):
        """Create engine for testing."""
        return HedgeEngine()

    def test_target_weight_valid_source(self, engine):
        """Test signals use valid HEDGE source."""
        signals = engine.get_hedge_signals(
            regime_score=25.0,
            current_sh_pct=0.0,
        )
        for signal in signals:
            assert isinstance(signal, TargetWeight)
            assert signal.source == "HEDGE"

    def test_target_weight_valid_symbols(self, engine):
        """V6.11: Test signals use SH symbol only."""
        signals = engine.get_hedge_signals(
            regime_score=15.0,
            current_sh_pct=0.0,
        )
        symbols = {s.symbol for s in signals}
        assert symbols == {"SH"}

    def test_target_weight_valid_range(self, engine):
        """Test target weights are within valid range (0.0 to 1.0)."""
        for score in [50.0, 45.0, 35.0, 25.0]:
            signals = engine.get_hedge_signals(
                regime_score=score,
                current_sh_pct=0.0,
            )
            for signal in signals:
                assert 0.0 <= signal.target_weight <= 1.0


class TestPersistence:
    """Tests for state persistence (V6.11: SH only)."""

    @pytest.fixture
    def engine(self):
        """Create engine for testing."""
        return HedgeEngine()

    def test_get_state_when_empty(self, engine):
        """Test get_state_for_persistence when no allocation."""
        state = engine.get_state_for_persistence()
        assert state["last_allocation"] is None

    def test_get_state_after_signal(self, engine):
        """V6.11: Test get_state_for_persistence after generating signals."""
        engine.get_hedge_signals(
            regime_score=35.0,
            current_sh_pct=0.0,
        )
        state = engine.get_state_for_persistence()
        assert state["last_allocation"] is not None
        assert state["last_allocation"]["regime_score"] == 35.0
        assert state["last_allocation"]["hedge_tier"] == "MEDIUM"

    def test_restore_state(self, engine):
        """V6.11: Test restore_state correctly loads allocation."""
        state = {
            "last_allocation": {
                "sh_target_pct": 0.08,
                "regime_score": 35.0,
                "hedge_tier": "MEDIUM",
            }
        }
        engine.restore_state(state)
        alloc = engine.get_last_allocation()
        assert alloc is not None
        assert alloc.sh_target_pct == 0.08

    def test_restore_empty_state(self, engine):
        """Test restore_state handles empty state."""
        engine.restore_state({})
        assert engine.get_last_allocation() is None

    def test_round_trip_persistence(self, engine):
        """V6.11: Test state survives save/restore cycle."""
        engine.get_hedge_signals(
            regime_score=25.0,
            current_sh_pct=0.0,
        )
        state = engine.get_state_for_persistence()

        new_engine = HedgeEngine()
        new_engine.restore_state(state)

        original = engine.get_last_allocation()
        restored = new_engine.get_last_allocation()
        assert original.sh_target_pct == restored.sh_target_pct
        assert original.regime_score == restored.regime_score
        assert original.hedge_tier == restored.hedge_tier


class TestReset:
    """Tests for engine reset."""

    def test_reset_clears_allocation(self):
        """Test reset clears last allocation."""
        engine = HedgeEngine()
        engine.get_hedge_signals(
            regime_score=25.0,
            current_sh_pct=0.0,
        )
        assert engine.get_last_allocation() is not None

        engine.reset()
        assert engine.get_last_allocation() is None


class TestHelperMethods:
    """Tests for helper methods (V6.11: SH only)."""

    @pytest.fixture
    def engine(self):
        """Create engine for testing."""
        return HedgeEngine()

    def test_get_hedge_tier_for_regime(self, engine):
        """V6.11: Test get_hedge_tier_for_regime returns correct tier."""
        assert engine.get_hedge_tier_for_regime(50.0) == "NONE"
        assert engine.get_hedge_tier_for_regime(45.0) == "LIGHT"
        assert engine.get_hedge_tier_for_regime(35.0) == "MEDIUM"
        assert engine.get_hedge_tier_for_regime(25.0) == "FULL"

    def test_get_max_total_hedge(self, engine):
        """V6.11: Test get_max_total_hedge returns SH_FULL."""
        max_hedge = engine.get_max_total_hedge()
        assert max_hedge == config.SH_FULL
        assert max_hedge == pytest.approx(0.10)  # 10%


class TestGraduatedScaling:
    """Tests for graduated hedge scaling (V6.11: SH only)."""

    @pytest.fixture
    def engine(self):
        """Create engine for testing."""
        return HedgeEngine()

    def test_graduated_sh_allocation(self, engine):
        """V6.11: Test SH allocation increases gradually."""
        allocs = []
        for score in [50, 45, 35, 25]:
            sh, _ = engine.get_target_allocations(score)
            allocs.append(sh)

        # Should be monotonically increasing (or equal)
        assert allocs[0] <= allocs[1] <= allocs[2] <= allocs[3]
        # Specifically: 0%, 5%, 8%, 10%
        assert allocs == [0.0, config.SH_LIGHT, config.SH_MEDIUM, config.SH_FULL]

    def test_total_hedge_increases_with_severity(self, engine):
        """V6.11: Test total hedge (SH) increases with severity."""
        totals = []
        for score in [50, 45, 35, 25]:
            sh, _ = engine.get_target_allocations(score)
            totals.append(sh)

        # Should be: 0%, 5%, 8%, 10%
        expected = [0.0, 0.05, 0.08, 0.10]
        for actual, exp in zip(totals, expected):
            assert actual == pytest.approx(exp)


class TestEdgeCases:
    """Tests for edge cases (V6.11: SH only)."""

    @pytest.fixture
    def engine(self):
        """Create engine for testing."""
        return HedgeEngine()

    def test_regime_score_zero(self, engine):
        """Test regime score of 0 (worst case)."""
        sh, tier = engine.get_target_allocations(0.0)
        assert tier == "FULL"
        assert sh == config.SH_FULL

    def test_regime_score_100(self, engine):
        """Test regime score of 100 (best case)."""
        sh, tier = engine.get_target_allocations(100.0)
        assert tier == "NONE"
        assert sh == 0.0

    def test_negative_current_allocation(self, engine):
        """Test with negative current allocation (should not happen but handle gracefully)."""
        needs_rebal = engine.check_rebalance_needed(0.05, -0.05)
        assert needs_rebal is True  # 10% diff > 2%

    def test_current_exceeds_max(self, engine):
        """V6.11: Test when current allocation exceeds max (edge case)."""
        signals = engine.get_hedge_signals(
            regime_score=50.0,  # Target = 0%
            current_sh_pct=0.15,  # Way above any target
        )
        assert len(signals) == 1
        assert signals[0].symbol == "SH"
        assert signals[0].target_weight == 0.0
