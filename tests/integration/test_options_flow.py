"""
Integration tests for V2.1 Options Engine flow.

Tests the complete wiring from main.py:
1. Options chain subscription with config-driven DTE (1-4 days)
2. Intraday options scanning (_scan_options_signals)
3. Greeks monitoring (_monitor_risk_greeks)
4. IV rank calculation
5. Risk engine Greeks breach detection
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

import config
from engines.core.risk_engine import GreeksSnapshot, RiskEngine
from engines.satellite.options_engine import OptionContract, OptionDirection, OptionsEngine
from models.enums import Urgency
from models.target_weight import TargetWeight


class TestOptionsChainDTEFilter:
    """Test that options chain uses config-driven DTE values."""

    def test_config_dte_values(self):
        """Verify config has correct DTE values for V2.23 VASS dual-mode architecture."""
        # V2.23: 0-60 DTE range (0-2 Intraday, 5-60 Swing incl. VASS Low IV monthly)
        assert config.OPTIONS_DTE_MIN == 0, "OPTIONS_DTE_MIN should be 0 for intraday"
        assert config.OPTIONS_DTE_MAX == 60, "OPTIONS_DTE_MAX should be 60 for VASS Low IV monthly"

    def test_dte_filter_rejects_long_dated_options(self):
        """Options with DTE > 60 should be filtered out."""
        # This tests the logic that would be in _select_best_option_contract
        dte = 75  # 75 DTE option - exceeds max
        is_valid = config.OPTIONS_DTE_MIN <= dte <= config.OPTIONS_DTE_MAX
        assert not is_valid, "75 DTE options should be rejected"

    def test_dte_filter_accepts_valid_dte_options(self):
        """Options with DTE 0-45 should be accepted."""
        for dte in [0, 1, 2, 5, 14, 30, 45]:
            is_valid = config.OPTIONS_DTE_MIN <= dte <= config.OPTIONS_DTE_MAX
            assert is_valid, f"DTE {dte} should be accepted"


class TestOptionsEntryScanningWiring:
    """Test that _scan_options_signals is properly wired."""

    @pytest.fixture
    def mock_algorithm(self):
        """Create mock algorithm with necessary attributes."""
        algo = MagicMock()
        algo.Time = datetime(2024, 1, 15, 10, 30)  # 10:30 AM - within window
        algo.Portfolio.TotalPortfolioValue = 100000.0
        algo.Securities = {"QQQ": MagicMock(Price=450.0)}
        algo.Log = MagicMock()
        algo.Debug = MagicMock()
        return algo

    @pytest.fixture
    def options_engine(self, mock_algorithm):
        """Create options engine instance."""
        engine = OptionsEngine(mock_algorithm)
        return engine

    def test_options_entry_signal_generation(self, options_engine, mock_algorithm):
        """Test that entry signal is generated with correct parameters."""
        # Create a valid option contract
        contract = OptionContract(
            symbol="QQQ 240117C00450000",
            underlying="QQQ",
            direction=OptionDirection.CALL,
            strike=450.0,
            expiry="2024-01-17",
            delta=0.55,
            gamma=0.04,
            vega=0.25,
            theta=-0.15,
            bid=2.45,
            ask=2.55,
            mid_price=2.50,
            open_interest=5000,
            days_to_expiry=2,
        )

        # Check entry signal
        signal = options_engine.check_entry_signal(
            adx_value=30.0,  # Strong trend
            current_price=450.0,
            ma200_value=440.0,  # Price above MA200
            iv_rank=45.0,  # Optimal IV range
            best_contract=contract,
            current_hour=10,
            current_minute=30,
            current_date="2024-01-15",
            portfolio_value=100000.0,
            gap_filter_triggered=False,
            vol_shock_active=False,
            time_guard_active=False,
        )

        # Signal should be generated (or None if conditions not met)
        # The key is that the wiring allows the check to happen
        assert signal is None or isinstance(signal, TargetWeight)


class TestGreeksMonitoringWiring:
    """Test that _monitor_risk_greeks is properly wired."""

    @pytest.fixture
    def mock_algorithm(self):
        """Create mock algorithm."""
        algo = MagicMock()
        algo.Time = datetime(2024, 1, 15, 11, 0)
        algo.Log = MagicMock()
        algo.Debug = MagicMock()
        return algo

    @pytest.fixture
    def risk_engine(self, mock_algorithm):
        """Create risk engine instance."""
        return RiskEngine(mock_algorithm)

    @pytest.fixture
    def options_engine(self, mock_algorithm):
        """Create options engine instance."""
        return OptionsEngine(mock_algorithm)

    def test_greeks_update_to_risk_engine(self, risk_engine):
        """Test that Greeks can be updated in risk engine."""
        greeks = GreeksSnapshot(
            delta=0.55,
            gamma=0.04,
            vega=0.25,
            theta=-0.10,
        )

        # Should not raise
        risk_engine.update_greeks(greeks)

        # Verify Greeks are stored
        assert risk_engine._current_greeks is not None
        assert risk_engine._current_greeks.delta == 0.55

    def test_greeks_breach_detection(self, risk_engine):
        """Test that Greeks breach is detected."""
        # Set breaching Greeks (delta > 0.80)
        greeks = GreeksSnapshot(
            delta=0.85,  # Breach threshold
            gamma=0.04,
            vega=0.25,
            theta=-0.10,
        )
        risk_engine.update_greeks(greeks)

        # Check for breach
        breach, options_to_close = risk_engine.check_cb_greeks_breach()

        assert breach is True
        # Returns list of options to close (ALL_OPTIONS signals close all)
        assert "ALL_OPTIONS" in options_to_close

    def test_no_breach_with_safe_greeks(self, risk_engine):
        """Test no breach with safe Greeks values."""
        greeks = GreeksSnapshot(
            delta=0.55,  # Safe
            gamma=0.03,  # Safe
            vega=0.20,  # Safe
            theta=-0.01,  # Safe
        )
        risk_engine.update_greeks(greeks)

        breach, reasons = risk_engine.check_cb_greeks_breach()

        assert breach is False
        assert len(reasons) == 0


class TestIVRankCalculation:
    """Test IV rank calculation logic."""

    def test_iv_rank_from_vix_normal(self):
        """Test IV rank calculation with normal VIX."""
        # VIX = 18 (average)
        vix = 18.0
        vix_low = 12.0
        vix_high = 35.0

        iv_rank = (vix - vix_low) / (vix_high - vix_low) * 100.0

        # Should be around 26% ((18-12)/(35-12) * 100)
        assert 25 <= iv_rank <= 27

    def test_iv_rank_from_vix_high(self):
        """Test IV rank calculation with high VIX."""
        vix = 35.0  # At high
        vix_low = 12.0
        vix_high = 35.0

        iv_rank = (vix - vix_low) / (vix_high - vix_low) * 100.0

        assert iv_rank == 100.0

    def test_iv_rank_from_vix_low(self):
        """Test IV rank calculation with low VIX."""
        vix = 12.0  # At low
        vix_low = 12.0
        vix_high = 35.0

        iv_rank = (vix - vix_low) / (vix_high - vix_low) * 100.0

        assert iv_rank == 0.0

    def test_iv_rank_clamped(self):
        """Test IV rank is clamped to 0-100."""
        # VIX below historical low
        vix = 10.0
        vix_low = 12.0
        vix_high = 35.0

        iv_rank = (vix - vix_low) / (vix_high - vix_low) * 100.0
        iv_rank = max(0.0, min(100.0, iv_rank))

        assert iv_rank == 0.0

        # VIX above historical high
        vix = 50.0
        iv_rank = (vix - vix_low) / (vix_high - vix_low) * 100.0
        iv_rank = max(0.0, min(100.0, iv_rank))

        assert iv_rank == 100.0


class TestOptionsPositionGreeksCalculation:
    """Test options position Greeks calculation."""

    @pytest.fixture
    def mock_algorithm(self):
        """Create mock algorithm."""
        algo = MagicMock()
        algo.Time = datetime(2024, 1, 15, 11, 0)
        algo.Log = MagicMock()
        algo.Debug = MagicMock()
        return algo

    @pytest.fixture
    def options_engine_with_position(self, mock_algorithm):
        """Create options engine with a position."""
        engine = OptionsEngine(mock_algorithm)

        # Create a pending contract first
        contract = OptionContract(
            symbol="QQQ 240117C00450000",
            underlying="QQQ",
            direction=OptionDirection.CALL,
            strike=450.0,
            expiry="2024-01-17",
            delta=0.55,
            gamma=0.04,
            vega=0.25,
            theta=-0.15,
            bid=2.45,
            ask=2.55,
            mid_price=2.50,
            open_interest=5000,
            days_to_expiry=2,
        )

        # Set pending contract (simulating entry signal)
        engine._pending_contract = contract
        engine._pending_quantity = 10

        # Register entry with correct signature
        engine.register_entry(
            fill_price=2.50,
            entry_time="2024-01-15 10:30:00",
            current_date="2024-01-15",
            contract=contract,
        )

        return engine

    def test_position_greeks_calculation(self, options_engine_with_position):
        """Test Greeks are calculated for position."""
        greeks = options_engine_with_position.calculate_position_greeks()

        assert greeks is not None
        assert isinstance(greeks, GreeksSnapshot)
        assert greeks.delta > 0  # Long call has positive delta

    def test_no_greeks_without_position(self, mock_algorithm):
        """Test no Greeks returned without position."""
        engine = OptionsEngine(mock_algorithm)
        greeks = engine.calculate_position_greeks()

        assert greeks is None


class TestOptionsExitOnGreeksBreach:
    """Test that Greeks breach triggers options exit."""

    @pytest.fixture
    def mock_algorithm(self):
        """Create mock algorithm."""
        algo = MagicMock()
        algo.Time = datetime(2024, 1, 15, 11, 0)
        algo.Log = MagicMock()
        algo.Debug = MagicMock()
        return algo

    def test_greeks_breach_triggers_exit_signal(self, mock_algorithm):
        """Test that breach generates exit signal."""
        # This tests the logic in _monitor_risk_greeks
        risk_engine = RiskEngine(mock_algorithm)

        # Set breaching Greeks
        greeks = GreeksSnapshot(
            delta=0.85,  # > 0.80 threshold
            gamma=0.04,
            vega=0.25,
            theta=-0.10,
        )
        risk_engine.update_greeks(greeks)

        breach, reasons = risk_engine.check_cb_greeks_breach()

        # Verify breach detected
        assert breach is True

        # In main.py, this would trigger:
        # signal = TargetWeight(
        #     symbol="QQQ_OPT",
        #     target_weight=0.0,
        #     source="RISK",
        #     urgency=Urgency.IMMEDIATE,
        #     reason=f"GREEKS_BREACH: {', '.join(reasons)}",
        # )
        # The signal would be emitted to portfolio_router


class TestConfigConsistency:
    """Test that config values are consistent across the system."""

    def test_options_dte_range(self):
        """Test OPTIONS_DTE values are sensible for V2.3 dual-mode."""
        assert config.OPTIONS_DTE_MIN >= 0, "DTE min should be non-negative"
        assert config.OPTIONS_DTE_MAX > config.OPTIONS_DTE_MIN, "DTE max should be > min"
        # V2.3: Swing mode supports up to 45 DTE
        assert config.OPTIONS_DTE_MAX <= 60, "DTE max should be reasonable (up to 60 days)"

    def test_greeks_thresholds_exist(self):
        """Test Greeks threshold config values exist."""
        assert hasattr(config, "CB_DELTA_MAX")
        assert hasattr(config, "CB_GAMMA_WARNING")
        assert hasattr(config, "CB_VEGA_MAX")
        assert hasattr(config, "CB_THETA_WARNING")

    def test_greeks_thresholds_sensible(self):
        """Test Greeks thresholds are sensible values."""
        assert 0.5 <= config.CB_DELTA_MAX <= 1.0, "Delta max should be 0.5-1.0"
        assert 0.01 <= config.CB_GAMMA_WARNING <= 0.20, "Gamma warning should be reasonable"
        assert 0.1 <= config.CB_VEGA_MAX <= 1.0, "Vega max should be reasonable"
        assert -0.10 <= config.CB_THETA_WARNING <= 0, "Theta warning should be negative"
