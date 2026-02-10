"""
Unit tests for Exposure Groups.

Tests position grouping and limit enforcement:
- NASDAQ_BETA: TQQQ, QLD, SOXL (50% net, 75% gross)
- SPY_BETA: SSO, SPXL, SH (40% net, 50% gross)
- COMMODITIES: UGL, UCO (25% net, 25% gross)

V6.11 Universe Redesign:
- Removed RATES group (TMF/SHV removed)
- Added COMMODITIES group (UGL/UCO)
- SH replaced PSQ in SPY_BETA group

Spec: docs/11-portfolio-router.md
"""

import pytest

from portfolio.exposure_groups import (
    INVERSE_SYMBOLS,
    ExposureCalculator,
    ExposureGroup,
    ExposureGroupName,
    ExposureValidationResult,
    GroupExposure,
)


class TestExposureGroupDataclass:
    """Tests for ExposureGroup dataclass."""

    def test_group_creation(self):
        """Test creating an exposure group."""
        group = ExposureGroup(
            name="TEST",
            symbols={"A", "B", "C"},
            max_net_long=0.50,
            max_net_short=0.30,
            max_gross=0.75,
            inverse_symbols={"C"},
        )

        assert group.name == "TEST"
        assert group.symbols == {"A", "B", "C"}
        assert group.max_net_long == 0.50
        assert group.max_net_short == 0.30
        assert group.max_gross == 0.75
        assert group.inverse_symbols == {"C"}

    def test_contains(self):
        """Test symbol membership check."""
        group = ExposureGroup(
            name="TEST",
            symbols={"A", "B"},
            max_net_long=0.50,
            max_net_short=0.30,
            max_gross=0.75,
        )

        assert group.contains("A") is True
        assert group.contains("B") is True
        assert group.contains("C") is False

    def test_is_inverse(self):
        """Test inverse symbol check."""
        group = ExposureGroup(
            name="TEST",
            symbols={"A", "B", "C"},
            max_net_long=0.50,
            max_net_short=0.30,
            max_gross=0.75,
            inverse_symbols={"C"},
        )

        assert group.is_inverse("A") is False
        assert group.is_inverse("B") is False
        assert group.is_inverse("C") is True


class TestGroupExposure:
    """Tests for GroupExposure dataclass."""

    def test_net_exposure_long_only(self):
        """Test net exposure with only long positions."""
        exposure = GroupExposure(
            group_name="TEST",
            long_exposure=0.40,
            short_exposure=0.0,
        )

        assert exposure.net_exposure == 0.40
        assert exposure.gross_exposure == 0.40

    def test_net_exposure_with_short(self):
        """Test net exposure with long and short positions."""
        exposure = GroupExposure(
            group_name="TEST",
            long_exposure=0.40,
            short_exposure=0.10,
        )

        assert pytest.approx(exposure.net_exposure) == 0.30  # 0.40 - 0.10
        assert pytest.approx(exposure.gross_exposure) == 0.50  # 0.40 + 0.10

    def test_to_dict(self):
        """Test serialization to dict."""
        exposure = GroupExposure(
            group_name="NASDAQ_BETA",
            long_exposure=0.35,
            short_exposure=0.10,
        )

        result = exposure.to_dict()

        assert result["group_name"] == "NASDAQ_BETA"
        assert result["long_exposure"] == 0.35
        assert result["short_exposure"] == 0.10
        assert pytest.approx(result["net_exposure"]) == 0.25
        assert pytest.approx(result["gross_exposure"]) == 0.45


class TestExposureValidationResult:
    """Tests for ExposureValidationResult dataclass."""

    def test_valid_result(self):
        """Test valid result with no exceeded limits."""
        result = ExposureValidationResult(
            group_name="TEST",
            is_valid=True,
        )

        assert result.is_valid is True
        assert result.scale_factor == 1.0

    def test_net_long_exceeded(self):
        """Test result with net long exceeded."""
        result = ExposureValidationResult(
            group_name="TEST",
            is_valid=False,
            net_long_exceeded=True,
            net_long_scale=0.667,
        )

        assert result.is_valid is False
        assert result.net_long_exceeded is True
        assert result.scale_factor == 0.667

    def test_scale_factor_takes_minimum(self):
        """Test scale_factor returns minimum of net and gross."""
        result = ExposureValidationResult(
            group_name="TEST",
            is_valid=False,
            net_long_exceeded=True,
            gross_exceeded=True,
            net_long_scale=0.80,
            gross_scale=0.60,
        )

        assert result.scale_factor == 0.60  # min(0.80, 0.60)


class TestExposureCalculatorInit:
    """Tests for ExposureCalculator initialization."""

    def test_loads_all_groups(self):
        """Test all groups are loaded from config (V6.11 universe)."""
        calc = ExposureCalculator()

        groups = calc.get_all_groups()
        group_names = {g.name for g in groups}

        assert "NASDAQ_BETA" in group_names
        assert "SPY_BETA" in group_names
        assert "COMMODITIES" in group_names

    def test_nasdaq_beta_symbols(self):
        """Test NASDAQ_BETA contains correct symbols (V6.11)."""
        calc = ExposureCalculator()

        group = calc.get_group("NASDAQ_BETA")

        assert group is not None
        assert "TQQQ" in group.symbols
        assert "QLD" in group.symbols
        assert "SOXL" in group.symbols

    def test_spy_beta_symbols(self):
        """Test SPY_BETA contains correct symbols (V6.11)."""
        calc = ExposureCalculator()

        group = calc.get_group("SPY_BETA")

        assert group is not None
        assert "SSO" in group.symbols
        assert "SPXL" in group.symbols
        assert "SH" in group.symbols  # V6.11: SH replaced PSQ

    def test_commodities_symbols(self):
        """Test COMMODITIES contains UGL and UCO (V6.11)."""
        calc = ExposureCalculator()

        group = calc.get_group("COMMODITIES")

        assert group is not None
        assert "UGL" in group.symbols
        assert "UCO" in group.symbols

    def test_nasdaq_beta_limits(self):
        """Test NASDAQ_BETA has correct limits."""
        calc = ExposureCalculator()

        group = calc.get_group("NASDAQ_BETA")

        assert group.max_net_long == 0.50
        assert group.max_net_short == 0.30
        assert group.max_gross == 0.75

    def test_spy_beta_limits(self):
        """Test SPY_BETA has correct limits (V6.11: allow SH short)."""
        calc = ExposureCalculator()

        group = calc.get_group("SPY_BETA")

        assert group.max_net_long == 0.40
        assert group.max_net_short == 0.15  # V6.11: Allow SH short
        assert group.max_gross == 0.50

    def test_commodities_limits(self):
        """Test COMMODITIES has correct limits (V6.11)."""
        calc = ExposureCalculator()

        group = calc.get_group("COMMODITIES")

        assert group.max_net_long == 0.25
        assert group.max_net_short == 0.00
        assert group.max_gross == 0.25

    def test_sh_is_inverse(self):
        """Test SH is marked as inverse in SPY_BETA (V6.11)."""
        calc = ExposureCalculator()

        group = calc.get_group("SPY_BETA")

        assert "SH" in group.inverse_symbols
        assert group.is_inverse("SH") is True
        assert group.is_inverse("SSO") is False


class TestExposureCalculation:
    """Tests for exposure calculation."""

    def test_calculate_long_only(self):
        """Test calculating exposure with only long positions."""
        calc = ExposureCalculator()

        weights = {"QLD": 0.30, "TQQQ": 0.20}
        exposure = calc.calculate_exposure(weights, "NASDAQ_BETA")

        assert exposure.long_exposure == 0.50
        assert exposure.short_exposure == 0.0
        assert exposure.net_exposure == 0.50
        assert exposure.gross_exposure == 0.50

    def test_calculate_with_inverse(self):
        """Test calculating exposure with inverse symbol (V6.11: SH)."""
        calc = ExposureCalculator()

        weights = {"SSO": 0.25, "SH": 0.10}
        exposure = calc.calculate_exposure(weights, "SPY_BETA")

        assert exposure.long_exposure == 0.25
        assert exposure.short_exposure == 0.10
        assert pytest.approx(exposure.net_exposure) == 0.15  # 0.25 - 0.10
        assert pytest.approx(exposure.gross_exposure) == 0.35  # 0.25 + 0.10

    def test_calculate_ignores_other_groups(self):
        """Test calculation ignores symbols from other groups."""
        calc = ExposureCalculator()

        weights = {"QLD": 0.30, "SSO": 0.20, "UGL": 0.10}
        exposure = calc.calculate_exposure(weights, "NASDAQ_BETA")

        # Should only count QLD, not SSO or UGL
        assert exposure.long_exposure == 0.30

    def test_calculate_all_exposures(self):
        """Test calculating exposure for all groups (V6.11)."""
        calc = ExposureCalculator()

        weights = {"QLD": 0.30, "SSO": 0.20, "UGL": 0.15}
        exposures = calc.calculate_all_exposures(weights)

        assert "NASDAQ_BETA" in exposures
        assert "SPY_BETA" in exposures
        assert "COMMODITIES" in exposures

        assert exposures["NASDAQ_BETA"].long_exposure == 0.30
        assert exposures["SPY_BETA"].long_exposure == 0.20
        assert exposures["COMMODITIES"].long_exposure == 0.15

    def test_calculate_empty_weights(self):
        """Test calculating exposure with no positions."""
        calc = ExposureCalculator()

        exposure = calc.calculate_exposure({}, "NASDAQ_BETA")

        assert exposure.long_exposure == 0.0
        assert exposure.short_exposure == 0.0

    def test_calculate_unknown_group(self):
        """Test calculating exposure for unknown group."""
        calc = ExposureCalculator()

        exposure = calc.calculate_exposure({"QLD": 0.30}, "UNKNOWN")

        assert exposure.long_exposure == 0.0
        assert exposure.short_exposure == 0.0


class TestExposureValidation:
    """Tests for exposure limit validation."""

    def test_within_limits(self):
        """Test validation when within all limits."""
        calc = ExposureCalculator()

        weights = {"QLD": 0.30, "TQQQ": 0.15}  # 45% net, under 50% limit
        exposure = calc.calculate_exposure(weights, "NASDAQ_BETA")
        result = calc.validate_exposure(exposure)

        assert result.is_valid is True
        assert result.net_long_exceeded is False
        assert result.gross_exceeded is False

    def test_net_long_exceeded(self):
        """Test validation when net long exceeds limit."""
        calc = ExposureCalculator()

        # 60% net, over 50% limit
        weights = {"QLD": 0.35, "TQQQ": 0.25}
        exposure = calc.calculate_exposure(weights, "NASDAQ_BETA")
        result = calc.validate_exposure(exposure)

        assert result.is_valid is False
        assert result.net_long_exceeded is True
        assert pytest.approx(result.net_long_scale, rel=0.01) == 0.833  # 50/60

    def test_gross_exceeded(self):
        """Test validation when gross exceeds limit (V6.11: SH)."""
        calc = ExposureCalculator()

        # SPY_BETA has 50% gross limit
        # 40% long + 15% short = 55% gross, over 50% limit
        weights = {"SSO": 0.25, "SPXL": 0.15, "SH": 0.15}
        exposure = calc.calculate_exposure(weights, "SPY_BETA")
        result = calc.validate_exposure(exposure)

        assert result.is_valid is False
        assert result.gross_exceeded is True

    def test_validate_all(self):
        """Test validating all groups at once (V6.11)."""
        calc = ExposureCalculator()

        weights = {"QLD": 0.30, "SSO": 0.20, "UGL": 0.15}
        results = calc.validate_all(weights)

        assert "NASDAQ_BETA" in results
        assert "SPY_BETA" in results
        assert "COMMODITIES" in results

        # NASDAQ_BETA within limits
        assert results["NASDAQ_BETA"].is_valid is True
        # SPY_BETA within limits (20% < 40%)
        assert results["SPY_BETA"].is_valid is True
        # COMMODITIES within limits (15% < 25%)
        assert results["COMMODITIES"].is_valid is True

    def test_validate_spy_beta_exceeded(self):
        """Test SPY_BETA limit exceeded."""
        calc = ExposureCalculator()

        weights = {"SSO": 0.50}  # 50% > 40% limit
        exposure = calc.calculate_exposure(weights, "SPY_BETA")
        result = calc.validate_exposure(exposure)

        assert result.is_valid is False
        assert result.net_long_exceeded is True
        assert pytest.approx(result.net_long_scale, rel=0.01) == 0.80  # 40/50


class TestScaling:
    """Tests for position scaling."""

    def test_scale_weights_for_group(self):
        """Test scaling weights for a specific group."""
        calc = ExposureCalculator()

        weights = {"QLD": 0.40, "TQQQ": 0.20, "SSO": 0.30}
        scaled = calc.scale_weights_for_group(weights, "NASDAQ_BETA", 0.5)

        # NASDAQ_BETA symbols scaled
        assert scaled["QLD"] == 0.20  # 0.40 * 0.5
        assert scaled["TQQQ"] == 0.10  # 0.20 * 0.5
        # Other groups unchanged
        assert scaled["SSO"] == 0.30

    def test_scale_weights_preserves_inverse(self):
        """Test that scaling doesn't scale inverse symbols (V6.11: SH)."""
        calc = ExposureCalculator()

        weights = {"SSO": 0.40, "SH": 0.10}
        scaled = calc.scale_weights_for_group(weights, "SPY_BETA", 0.5)

        assert scaled["SSO"] == 0.20  # Scaled
        assert scaled["SH"] == 0.10  # Not scaled (inverse)

    def test_scale_weights_unknown_group(self):
        """Test scaling unknown group returns unchanged."""
        calc = ExposureCalculator()

        weights = {"QLD": 0.40}
        scaled = calc.scale_weights_for_group(weights, "UNKNOWN", 0.5)

        assert scaled["QLD"] == 0.40  # Unchanged


class TestEnforceLimits:
    """Tests for enforce_limits comprehensive scaling."""

    def test_enforce_limits_within_limits(self):
        """Test enforce_limits when already within limits (V6.11)."""
        calc = ExposureCalculator()

        weights = {"QLD": 0.30, "SSO": 0.20, "UGL": 0.15}
        result = calc.enforce_limits(weights)

        assert result["QLD"] == 0.30
        assert result["SSO"] == 0.20
        assert result["UGL"] == 0.15

    def test_enforce_limits_scales_nasdaq(self):
        """Test enforce_limits scales NASDAQ_BETA when exceeded."""
        calc = ExposureCalculator()

        # 75% NASDAQ_BETA, over 50% net limit
        weights = {"QLD": 0.35, "TQQQ": 0.25, "SOXL": 0.15}
        result = calc.enforce_limits(weights)

        # Total should be scaled to 50%
        total = result["QLD"] + result["TQQQ"] + result["SOXL"]
        assert pytest.approx(total, rel=0.01) == 0.50

    def test_enforce_limits_example_from_spec(self):
        """Test the example from spec section 11.5.2."""
        calc = ExposureCalculator()

        # From spec: TQQQ 25%, QLD 35%, SOXL 15% = 75% total
        weights = {"TQQQ": 0.25, "QLD": 0.35, "SOXL": 0.15}
        result = calc.enforce_limits(weights)

        # Scale factor = 50/75 = 0.667
        # Adjusted values from spec:
        # TQQQ: 25% × 0.667 = 16.7%
        # QLD: 35% × 0.667 = 23.3%
        # SOXL: 15% × 0.667 = 10.0%
        assert pytest.approx(result["TQQQ"], rel=0.01) == 0.167
        assert pytest.approx(result["QLD"], rel=0.01) == 0.233
        assert pytest.approx(result["SOXL"], rel=0.01) == 0.10

        # Total = 50%
        total = result["TQQQ"] + result["QLD"] + result["SOXL"]
        assert pytest.approx(total, rel=0.01) == 0.50


class TestHelperMethods:
    """Tests for helper methods."""

    def test_get_group_for_symbol(self):
        """Test getting group for a symbol (V6.11)."""
        calc = ExposureCalculator()

        assert calc.get_group_for_symbol("QLD").name == "NASDAQ_BETA"
        assert calc.get_group_for_symbol("SSO").name == "SPY_BETA"
        assert calc.get_group_for_symbol("UGL").name == "COMMODITIES"
        assert calc.get_group_for_symbol("UNKNOWN") is None

    def test_is_symbol_inverse(self):
        """Test checking if symbol is inverse (V6.11: SH)."""
        calc = ExposureCalculator()

        assert calc.is_symbol_inverse("SH") is True
        assert calc.is_symbol_inverse("QLD") is False
        assert calc.is_symbol_inverse("UGL") is False

    def test_get_group_symbols(self):
        """Test getting all symbols in a group (V6.11)."""
        calc = ExposureCalculator()

        symbols = calc.get_group_symbols("NASDAQ_BETA")

        assert "TQQQ" in symbols
        assert "QLD" in symbols
        assert "SOXL" in symbols

    def test_get_group_symbols_unknown(self):
        """Test getting symbols for unknown group."""
        calc = ExposureCalculator()

        symbols = calc.get_group_symbols("UNKNOWN")

        assert symbols == set()


class TestInverseSymbolsConstant:
    """Tests for INVERSE_SYMBOLS module constant."""

    def test_sh_in_inverse_symbols(self):
        """Test SH is in inverse symbols (V6.11)."""
        assert "SH" in INVERSE_SYMBOLS

    def test_regular_symbols_not_inverse(self):
        """Test regular symbols not in inverse."""
        assert "QLD" not in INVERSE_SYMBOLS
        assert "TQQQ" not in INVERSE_SYMBOLS
        assert "SSO" not in INVERSE_SYMBOLS
        assert "UGL" not in INVERSE_SYMBOLS


class TestExposureGroupNameEnum:
    """Tests for ExposureGroupName enum."""

    def test_enum_values(self):
        """Test enum has expected values (V6.11)."""
        assert ExposureGroupName.NASDAQ_BETA.value == "NASDAQ_BETA"
        assert ExposureGroupName.SPY_BETA.value == "SPY_BETA"
        assert ExposureGroupName.COMMODITIES.value == "COMMODITIES"
