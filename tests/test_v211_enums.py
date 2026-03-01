"""
Tests for V2.1.1 Enums (Options Engine - Micro Regime).

Validates that all enums have the expected values and counts.
Catches typos and missing values that could cause runtime crashes.
"""

import pytest

from models.enums import (
    IntradayStrategy,
    MicroRegime,
    OptionsMode,
    VIXDirection,
    VIXLevel,
    WhipsawState,
)

# =============================================================================
# VIX DIRECTION ENUM TESTS
# =============================================================================


class TestVIXDirectionEnum:
    """Tests for VIXDirection enum."""

    def test_vix_direction_has_seven_values(self):
        """VIXDirection should have exactly 7 values."""
        assert len(VIXDirection) == 7

    def test_vix_direction_expected_values(self):
        """VIXDirection should have specific expected values."""
        expected = {
            "FALLING_FAST",
            "FALLING",
            "STABLE",
            "RISING",
            "RISING_FAST",
            "SPIKING",
            "WHIPSAW",
        }
        actual = {d.name for d in VIXDirection}
        assert actual == expected

    def test_vix_direction_values_are_strings(self):
        """All VIXDirection values should be strings."""
        for direction in VIXDirection:
            assert isinstance(direction.value, str)

    def test_vix_direction_falling_fast_exists(self):
        """FALLING_FAST should exist."""
        assert VIXDirection.FALLING_FAST is not None
        assert VIXDirection.FALLING_FAST.value == "FALLING_FAST"

    def test_vix_direction_spiking_exists(self):
        """SPIKING should exist."""
        assert VIXDirection.SPIKING is not None
        assert VIXDirection.SPIKING.value == "SPIKING"

    def test_vix_direction_whipsaw_exists(self):
        """WHIPSAW should exist."""
        assert VIXDirection.WHIPSAW is not None
        assert VIXDirection.WHIPSAW.value == "WHIPSAW"


# =============================================================================
# VIX LEVEL ENUM TESTS
# =============================================================================


class TestVIXLevelEnum:
    """Tests for VIXLevel enum."""

    def test_vix_level_has_three_values(self):
        """VIXLevel should have exactly 3 values."""
        assert len(VIXLevel) == 3

    def test_vix_level_expected_values(self):
        """VIXLevel should have LOW, MEDIUM, HIGH."""
        expected = {"LOW", "MEDIUM", "HIGH"}
        actual = {level.name for level in VIXLevel}
        assert actual == expected

    def test_vix_level_low_exists(self):
        """LOW should exist with correct value."""
        assert VIXLevel.LOW is not None
        assert VIXLevel.LOW.value == "LOW"

    def test_vix_level_medium_exists(self):
        """MEDIUM should exist with correct value."""
        assert VIXLevel.MEDIUM is not None
        assert VIXLevel.MEDIUM.value == "MEDIUM"

    def test_vix_level_high_exists(self):
        """HIGH should exist with correct value."""
        assert VIXLevel.HIGH is not None
        assert VIXLevel.HIGH.value == "HIGH"


# =============================================================================
# MICRO REGIME ENUM TESTS (21 REGIMES)
# =============================================================================


class TestMicroRegimeEnum:
    """Tests for MicroRegime enum - 21 trading regimes."""

    def test_micro_regime_has_21_values(self):
        """MicroRegime should have exactly 21 values (3 levels × 7 directions)."""
        assert len(MicroRegime) == 21

    def test_low_vix_regimes_exist(self):
        """All 7 LOW VIX regimes should exist."""
        low_regimes = {
            MicroRegime.PERFECT_MR,
            MicroRegime.GOOD_MR,
            MicroRegime.NORMAL,
            MicroRegime.CAUTION_LOW,
            MicroRegime.TRANSITION,
            MicroRegime.RISK_OFF_LOW,
            MicroRegime.CHOPPY_LOW,
        }
        assert len(low_regimes) == 7

    def test_medium_vix_regimes_exist(self):
        """All 7 MEDIUM VIX regimes should exist."""
        medium_regimes = {
            MicroRegime.RECOVERING,
            MicroRegime.IMPROVING,
            MicroRegime.CAUTIOUS,
            MicroRegime.WORSENING,
            MicroRegime.DETERIORATING,
            MicroRegime.BREAKING,
            MicroRegime.UNSTABLE,
        }
        assert len(medium_regimes) == 7

    def test_high_vix_regimes_exist(self):
        """All 7 HIGH VIX regimes should exist."""
        high_regimes = {
            MicroRegime.PANIC_EASING,
            MicroRegime.CALMING,
            MicroRegime.ELEVATED,
            MicroRegime.WORSENING_HIGH,
            MicroRegime.FULL_PANIC,
            MicroRegime.CRASH,
            MicroRegime.VOLATILE,
        }
        assert len(high_regimes) == 7

    def test_micro_regime_perfect_mr_exists(self):
        """PERFECT_MR (best MR conditions) should exist."""
        assert MicroRegime.PERFECT_MR is not None
        assert MicroRegime.PERFECT_MR.value == "PERFECT_MR"

    def test_micro_regime_crash_exists(self):
        """CRASH (worst conditions) should exist."""
        assert MicroRegime.CRASH is not None
        assert MicroRegime.CRASH.value == "CRASH"

    def test_micro_regime_full_panic_exists(self):
        """FULL_PANIC (no trade zone) should exist."""
        assert MicroRegime.FULL_PANIC is not None
        assert MicroRegime.FULL_PANIC.value == "FULL_PANIC"

    def test_micro_regime_values_are_unique(self):
        """All MicroRegime values should be unique."""
        values = [r.value for r in MicroRegime]
        assert len(values) == len(set(values))


# =============================================================================
# INTRADAY STRATEGY ENUM TESTS
# =============================================================================


class TestIntradayStrategyEnum:
    """Tests for IntradayStrategy enum."""

    def test_intraday_strategy_has_nine_values(self):
        """IntradayStrategy should include legacy + canonical micro strategy aliases + IC."""
        assert len(IntradayStrategy) == 9

    def test_intraday_strategy_expected_values(self):
        """IntradayStrategy should have specific expected values."""
        expected = {
            "NO_TRADE",
            "DEBIT_FADE",
            "DEBIT_MOMENTUM",
            "MICRO_DEBIT_FADE",
            "MICRO_OTM_MOMENTUM",
            "CREDIT_SPREAD",
            "ITM_MOMENTUM",
            "PROTECTIVE_PUTS",
            "IRON_CONDOR",
        }
        actual = {s.name for s in IntradayStrategy}
        assert actual == expected

    def test_no_trade_exists(self):
        """NO_TRADE should exist."""
        assert IntradayStrategy.NO_TRADE is not None
        assert IntradayStrategy.NO_TRADE.value == "NO_TRADE"

    def test_debit_fade_exists(self):
        """DEBIT_FADE (mean reversion) should exist."""
        assert IntradayStrategy.DEBIT_FADE is not None
        assert IntradayStrategy.DEBIT_FADE.value == "DEBIT_FADE"

    def test_credit_spread_exists(self):
        """CREDIT_SPREAD should exist."""
        assert IntradayStrategy.CREDIT_SPREAD is not None
        assert IntradayStrategy.CREDIT_SPREAD.value == "CREDIT_SPREAD"

    def test_itm_momentum_exists(self):
        """ITM_MOMENTUM should exist."""
        assert IntradayStrategy.ITM_MOMENTUM is not None
        assert IntradayStrategy.ITM_MOMENTUM.value == "ITM_MOMENTUM"

    def test_protective_puts_exists(self):
        """PROTECTIVE_PUTS (hedge) should exist."""
        assert IntradayStrategy.PROTECTIVE_PUTS is not None
        assert IntradayStrategy.PROTECTIVE_PUTS.value == "PROTECTIVE_PUTS"


# =============================================================================
# WHIPSAW STATE ENUM TESTS
# =============================================================================


class TestWhipsawStateEnum:
    """Tests for WhipsawState enum."""

    def test_whipsaw_state_has_three_values(self):
        """WhipsawState should have exactly 3 values."""
        assert len(WhipsawState) == 3

    def test_whipsaw_state_expected_values(self):
        """WhipsawState should have TRENDING, CHOPPY, WHIPSAW."""
        expected = {"TRENDING", "CHOPPY", "WHIPSAW"}
        actual = {s.name for s in WhipsawState}
        assert actual == expected

    def test_trending_exists(self):
        """TRENDING should exist."""
        assert WhipsawState.TRENDING is not None
        assert WhipsawState.TRENDING.value == "TRENDING"

    def test_choppy_exists(self):
        """CHOPPY should exist."""
        assert WhipsawState.CHOPPY is not None
        assert WhipsawState.CHOPPY.value == "CHOPPY"

    def test_whipsaw_exists(self):
        """WHIPSAW should exist."""
        assert WhipsawState.WHIPSAW is not None
        assert WhipsawState.WHIPSAW.value == "WHIPSAW"


# =============================================================================
# OPTIONS MODE ENUM TESTS
# =============================================================================


class TestOptionsModeEnum:
    """Tests for OptionsMode enum."""

    def test_options_mode_has_two_values(self):
        """OptionsMode should have exactly 2 values."""
        assert len(OptionsMode) == 2

    def test_options_mode_expected_values(self):
        """OptionsMode should have SWING and INTRADAY."""
        expected = {"SWING", "INTRADAY"}
        actual = {m.name for m in OptionsMode}
        assert actual == expected

    def test_swing_mode_exists(self):
        """SWING mode (5-45 DTE) should exist."""
        assert OptionsMode.SWING is not None
        assert OptionsMode.SWING.value == "SWING"

    def test_intraday_mode_exists(self):
        """INTRADAY mode (0-2 DTE) should exist."""
        assert OptionsMode.INTRADAY is not None
        assert OptionsMode.INTRADAY.value == "INTRADAY"


# =============================================================================
# CROSS-ENUM VALIDATION TESTS
# =============================================================================


class TestEnumCrossValidation:
    """Cross-validation tests between related enums."""

    def test_regime_count_matches_level_times_direction(self):
        """MicroRegime count should equal VIXLevel count × VIXDirection count."""
        expected_count = len(VIXLevel) * len(VIXDirection)
        assert len(MicroRegime) == expected_count

    def test_all_enums_have_string_values(self):
        """All V2.1.1 enums should use string values."""
        all_enums = [
            VIXDirection,
            VIXLevel,
            MicroRegime,
            IntradayStrategy,
            WhipsawState,
            OptionsMode,
        ]
        for enum_class in all_enums:
            for member in enum_class:
                assert isinstance(member.value, str), f"{enum_class.__name__}.{member.name}"

    def test_enum_values_match_names(self):
        """Enum values should match their names (no typos)."""
        all_enums = [
            VIXDirection,
            VIXLevel,
            MicroRegime,
            IntradayStrategy,
            WhipsawState,
            OptionsMode,
        ]
        for enum_class in all_enums:
            for member in enum_class:
                assert (
                    member.name == member.value
                ), f"{enum_class.__name__}.{member.name} has value {member.value}"
