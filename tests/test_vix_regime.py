"""
Unit tests for VIX Regime Classification.

Tests VIX-based market regime detection and MR parameter adjustments:
- VIX < 20: NORMAL → 10% allocation
- VIX 20-30: CAUTION → 5% allocation
- VIX 30-40: HIGH_RISK → 2% allocation
- VIX > 40: CRASH → 0% allocation (disabled)

Spec: docs/v2-specs/V2-1-Critical-Fixes-Guide.md (Fix #2)
"""

import pytest

from data.vix_regime import (
    VIXDataFeed,
    VIXRegime,
    VIXRegimeState,
    classify_vix_regime,
    get_max_exposure_for_regime,
    get_mr_allocation_for_regime,
    get_rsi_threshold_for_regime,
    get_stop_loss_for_regime,
    get_vix_regime_state,
    is_mr_enabled_for_regime,
)


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def vix_feed():
    """Create a VIXDataFeed instance for testing."""
    return VIXDataFeed()


# =============================================================================
# VIX Classification Tests
# =============================================================================


class TestVIXClassification:
    """Tests for VIX regime classification."""

    def test_classify_normal_below_20(self):
        """Test VIX < 20 is classified as NORMAL."""
        assert classify_vix_regime(10.0) == VIXRegime.NORMAL
        assert classify_vix_regime(15.0) == VIXRegime.NORMAL
        assert classify_vix_regime(19.9) == VIXRegime.NORMAL

    def test_classify_caution_20_to_30(self):
        """Test VIX 20-30 is classified as CAUTION."""
        assert classify_vix_regime(20.0) == VIXRegime.CAUTION
        assert classify_vix_regime(25.0) == VIXRegime.CAUTION
        assert classify_vix_regime(29.9) == VIXRegime.CAUTION

    def test_classify_high_risk_30_to_40(self):
        """Test VIX 30-40 is classified as HIGH_RISK."""
        assert classify_vix_regime(30.0) == VIXRegime.HIGH_RISK
        assert classify_vix_regime(35.0) == VIXRegime.HIGH_RISK
        assert classify_vix_regime(39.9) == VIXRegime.HIGH_RISK

    def test_classify_crash_above_40(self):
        """Test VIX > 40 is classified as CRASH."""
        assert classify_vix_regime(40.0) == VIXRegime.CRASH
        assert classify_vix_regime(50.0) == VIXRegime.CRASH
        assert classify_vix_regime(82.0) == VIXRegime.CRASH  # March 2020 peak

    def test_boundary_values(self):
        """Test exact boundary values."""
        assert classify_vix_regime(20.0) == VIXRegime.CAUTION  # At boundary
        assert classify_vix_regime(30.0) == VIXRegime.HIGH_RISK  # At boundary
        assert classify_vix_regime(40.0) == VIXRegime.CRASH  # At boundary


# =============================================================================
# MR Allocation Tests
# =============================================================================


class TestMRAllocation:
    """Tests for MR allocation by regime."""

    def test_allocation_normal(self):
        """Test NORMAL regime allocation is 10%."""
        assert get_mr_allocation_for_regime(VIXRegime.NORMAL) == 0.10

    def test_allocation_caution(self):
        """Test CAUTION regime allocation is 5%."""
        assert get_mr_allocation_for_regime(VIXRegime.CAUTION) == 0.05

    def test_allocation_high_risk(self):
        """Test HIGH_RISK regime allocation is 2%."""
        assert get_mr_allocation_for_regime(VIXRegime.HIGH_RISK) == 0.02

    def test_allocation_crash(self):
        """Test CRASH regime allocation is 0%."""
        assert get_mr_allocation_for_regime(VIXRegime.CRASH) == 0.00

    def test_allocations_decrease_with_vix(self):
        """Test allocations decrease as VIX increases."""
        normal = get_mr_allocation_for_regime(VIXRegime.NORMAL)
        caution = get_mr_allocation_for_regime(VIXRegime.CAUTION)
        high_risk = get_mr_allocation_for_regime(VIXRegime.HIGH_RISK)
        crash = get_mr_allocation_for_regime(VIXRegime.CRASH)

        assert normal > caution > high_risk > crash


# =============================================================================
# RSI Threshold Tests
# =============================================================================


class TestRSIThreshold:
    """Tests for RSI threshold by regime."""

    def test_rsi_threshold_normal(self):
        """Test NORMAL regime RSI threshold is 30."""
        assert get_rsi_threshold_for_regime(VIXRegime.NORMAL) == 30

    def test_rsi_threshold_caution(self):
        """Test CAUTION regime RSI threshold is 25."""
        assert get_rsi_threshold_for_regime(VIXRegime.CAUTION) == 25

    def test_rsi_threshold_high_risk(self):
        """Test HIGH_RISK regime RSI threshold is 20."""
        assert get_rsi_threshold_for_regime(VIXRegime.HIGH_RISK) == 20

    def test_rsi_thresholds_decrease_with_vix(self):
        """Test RSI thresholds become stricter as VIX increases."""
        normal = get_rsi_threshold_for_regime(VIXRegime.NORMAL)
        caution = get_rsi_threshold_for_regime(VIXRegime.CAUTION)
        high_risk = get_rsi_threshold_for_regime(VIXRegime.HIGH_RISK)

        # Lower threshold = more conservative (harder to trigger)
        assert normal > caution > high_risk


# =============================================================================
# Stop Loss Tests
# =============================================================================


class TestStopLoss:
    """Tests for stop loss by regime."""

    def test_stop_loss_normal(self):
        """Test NORMAL regime stop loss is 8%."""
        assert get_stop_loss_for_regime(VIXRegime.NORMAL) == 0.08

    def test_stop_loss_caution(self):
        """Test CAUTION regime stop loss is 6%."""
        assert get_stop_loss_for_regime(VIXRegime.CAUTION) == 0.06

    def test_stop_loss_high_risk(self):
        """Test HIGH_RISK regime stop loss is 4%."""
        assert get_stop_loss_for_regime(VIXRegime.HIGH_RISK) == 0.04

    def test_stop_losses_tighten_with_vix(self):
        """Test stop losses become tighter as VIX increases."""
        normal = get_stop_loss_for_regime(VIXRegime.NORMAL)
        caution = get_stop_loss_for_regime(VIXRegime.CAUTION)
        high_risk = get_stop_loss_for_regime(VIXRegime.HIGH_RISK)

        assert normal > caution > high_risk


# =============================================================================
# Max Exposure Tests
# =============================================================================


class TestMaxExposure:
    """Tests for max exposure by regime."""

    def test_max_exposure_normal(self):
        """Test NORMAL regime max exposure is 15%."""
        assert get_max_exposure_for_regime(VIXRegime.NORMAL) == 0.15

    def test_max_exposure_caution(self):
        """Test CAUTION regime max exposure is 10%."""
        assert get_max_exposure_for_regime(VIXRegime.CAUTION) == 0.10

    def test_max_exposure_high_risk(self):
        """Test HIGH_RISK regime max exposure is 5%."""
        assert get_max_exposure_for_regime(VIXRegime.HIGH_RISK) == 0.05

    def test_max_exposure_crash(self):
        """Test CRASH regime max exposure is 0%."""
        assert get_max_exposure_for_regime(VIXRegime.CRASH) == 0.00


# =============================================================================
# MR Enabled Tests
# =============================================================================


class TestMREnabled:
    """Tests for MR enabled status by regime."""

    def test_mr_enabled_normal(self):
        """Test MR is enabled in NORMAL regime."""
        assert is_mr_enabled_for_regime(VIXRegime.NORMAL) is True

    def test_mr_enabled_caution(self):
        """Test MR is enabled in CAUTION regime."""
        assert is_mr_enabled_for_regime(VIXRegime.CAUTION) is True

    def test_mr_enabled_high_risk(self):
        """Test MR is enabled in HIGH_RISK regime."""
        assert is_mr_enabled_for_regime(VIXRegime.HIGH_RISK) is True

    def test_mr_disabled_crash(self):
        """Test MR is DISABLED in CRASH regime."""
        assert is_mr_enabled_for_regime(VIXRegime.CRASH) is False


# =============================================================================
# VIX Regime State Tests
# =============================================================================


class TestVIXRegimeState:
    """Tests for VIXRegimeState generation."""

    def test_get_state_normal(self):
        """Test complete state for VIX 15 (NORMAL)."""
        state = get_vix_regime_state(15.0)

        assert state.vix_value == 15.0
        assert state.regime == VIXRegime.NORMAL
        assert state.mr_allocation == 0.10
        assert state.rsi_threshold == 30
        assert state.stop_loss_pct == 0.08
        assert state.max_exposure == 0.15
        assert state.mr_enabled is True

    def test_get_state_caution(self):
        """Test complete state for VIX 25 (CAUTION)."""
        state = get_vix_regime_state(25.0)

        assert state.vix_value == 25.0
        assert state.regime == VIXRegime.CAUTION
        assert state.mr_allocation == 0.05
        assert state.rsi_threshold == 25
        assert state.stop_loss_pct == 0.06
        assert state.max_exposure == 0.10
        assert state.mr_enabled is True

    def test_get_state_high_risk(self):
        """Test complete state for VIX 35 (HIGH_RISK)."""
        state = get_vix_regime_state(35.0)

        assert state.vix_value == 35.0
        assert state.regime == VIXRegime.HIGH_RISK
        assert state.mr_allocation == 0.02
        assert state.rsi_threshold == 20
        assert state.stop_loss_pct == 0.04
        assert state.max_exposure == 0.05
        assert state.mr_enabled is True

    def test_get_state_crash(self):
        """Test complete state for VIX 50 (CRASH)."""
        state = get_vix_regime_state(50.0)

        assert state.vix_value == 50.0
        assert state.regime == VIXRegime.CRASH
        assert state.mr_allocation == 0.00
        assert state.max_exposure == 0.00
        assert state.mr_enabled is False

    def test_state_serialization(self):
        """Test state to_dict serialization."""
        state = get_vix_regime_state(25.0)
        d = state.to_dict()

        assert d["vix_value"] == 25.0
        assert d["regime"] == "CAUTION"
        assert d["mr_allocation"] == 0.05
        assert d["mr_enabled"] is True


# =============================================================================
# VIX Data Feed Tests
# =============================================================================


class TestVIXDataFeed:
    """Tests for VIXDataFeed class."""

    def test_default_vix(self, vix_feed):
        """Test default VIX is 15 (NORMAL)."""
        assert vix_feed.get_current_vix() == 15.0
        assert vix_feed.get_current_regime() == VIXRegime.NORMAL

    def test_update_vix(self, vix_feed):
        """Test VIX update."""
        state = vix_feed.update_vix(25.0, "2024-01-15")

        assert vix_feed.get_current_vix() == 25.0
        assert vix_feed.get_current_regime() == VIXRegime.CAUTION
        assert state.regime == VIXRegime.CAUTION

    def test_is_mr_allowed_normal(self, vix_feed):
        """Test MR is allowed in NORMAL."""
        vix_feed.update_vix(15.0, "2024-01-15")
        assert vix_feed.is_mr_allowed() is True

    def test_is_mr_allowed_crash(self, vix_feed):
        """Test MR is NOT allowed in CRASH."""
        vix_feed.update_vix(50.0, "2024-01-15")
        assert vix_feed.is_mr_allowed() is False

    def test_validate_vix_value(self, vix_feed):
        """Test VIX validation."""
        assert vix_feed.validate_vix_value(15.0) is True
        assert vix_feed.validate_vix_value(50.0) is True
        assert vix_feed.validate_vix_value(3.0) is False  # Too low
        assert vix_feed.validate_vix_value(150.0) is False  # Too high

    def test_vix_trend_calculation(self, vix_feed):
        """Test VIX trend detection."""
        # Add rising VIX values
        for vix in [15, 18, 22, 28, 35]:
            vix_feed.update_vix(float(vix), "2024-01-15")

        assert vix_feed.get_vix_trend() == "RISING"

    def test_vix_trend_falling(self, vix_feed):
        """Test VIX trend detection for falling VIX."""
        # Add falling VIX values
        for vix in [35, 28, 22, 18, 15]:
            vix_feed.update_vix(float(vix), "2024-01-15")

        assert vix_feed.get_vix_trend() == "FALLING"

    def test_vix_trend_stable(self, vix_feed):
        """Test VIX trend detection for stable VIX."""
        # Add stable VIX values
        for vix in [15, 15.5, 14.8, 15.2, 15.1]:
            vix_feed.update_vix(float(vix), "2024-01-15")

        assert vix_feed.get_vix_trend() == "STABLE"


# =============================================================================
# State Persistence Tests
# =============================================================================


class TestVIXPersistence:
    """Tests for VIX data feed state persistence."""

    def test_get_state_for_persistence(self, vix_feed):
        """Test state serialization for persistence."""
        vix_feed.update_vix(25.0, "2024-01-15")
        vix_feed.update_vix(26.0, "2024-01-16")

        state = vix_feed.get_state_for_persistence()

        assert state["current_vix"] == 26.0
        assert state["last_update_date"] == "2024-01-16"
        assert len(state["vix_history"]) == 2

    def test_load_state(self, vix_feed):
        """Test state loading from persistence."""
        state = {
            "current_vix": 35.0,
            "last_update_date": "2024-01-15",
            "vix_history": [25.0, 30.0, 35.0],
        }

        vix_feed.load_state(state)

        assert vix_feed.get_current_vix() == 35.0
        assert vix_feed.get_current_regime() == VIXRegime.HIGH_RISK

    def test_reset(self, vix_feed):
        """Test VIX data feed reset."""
        vix_feed.update_vix(50.0, "2024-01-15")
        vix_feed.reset()

        assert vix_feed.get_current_vix() == 15.0
        assert vix_feed.get_current_regime() == VIXRegime.NORMAL


# =============================================================================
# Crisis Scenario Tests
# =============================================================================


class TestCrisisScenarios:
    """Tests for historical crisis VIX values."""

    def test_march_2020_peak(self):
        """Test March 2020 VIX peak (82.69) is classified as CRASH."""
        state = get_vix_regime_state(82.69)
        assert state.regime == VIXRegime.CRASH
        assert state.mr_enabled is False
        assert state.mr_allocation == 0.00

    def test_feb_2018_spike(self):
        """Test Feb 2018 VIX spike (50.30) is classified as CRASH."""
        state = get_vix_regime_state(50.30)
        assert state.regime == VIXRegime.CRASH
        assert state.mr_enabled is False

    def test_dec_2018_spike(self):
        """Test Dec 2018 VIX spike (36.07) is classified as HIGH_RISK."""
        state = get_vix_regime_state(36.07)
        assert state.regime == VIXRegime.HIGH_RISK
        assert state.mr_enabled is True
        assert state.mr_allocation == 0.02

    def test_normal_market_2021(self):
        """Test typical 2021 low VIX (~16) is classified as NORMAL."""
        state = get_vix_regime_state(16.0)
        assert state.regime == VIXRegime.NORMAL
        assert state.mr_enabled is True
        assert state.mr_allocation == 0.10


# =============================================================================
# Edge Cases
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_vix_zero(self):
        """Test VIX = 0 is classified as NORMAL."""
        assert classify_vix_regime(0.0) == VIXRegime.NORMAL

    def test_vix_negative(self):
        """Test negative VIX is classified as NORMAL (shouldn't happen)."""
        assert classify_vix_regime(-5.0) == VIXRegime.NORMAL

    def test_vix_very_high(self):
        """Test extremely high VIX (>100) is CRASH."""
        assert classify_vix_regime(100.0) == VIXRegime.CRASH
        assert classify_vix_regime(200.0) == VIXRegime.CRASH

    def test_vix_regime_enum_values(self):
        """Test VIXRegime enum values."""
        assert VIXRegime.NORMAL.value == "NORMAL"
        assert VIXRegime.CAUTION.value == "CAUTION"
        assert VIXRegime.HIGH_RISK.value == "HIGH_RISK"
        assert VIXRegime.CRASH.value == "CRASH"
