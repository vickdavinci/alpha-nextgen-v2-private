"""
Pytest configuration and shared fixtures for Alpha NextGen tests.

This file provides:
1. Deterministic random seeds for reproducibility
2. Common mock fixtures for QCAlgorithm
3. Shared test utilities

IMPORTANT: All tests use fixed random seeds to ensure reproducibility.
The same test run twice should produce identical results.
"""

import pytest
import random
from unittest.mock import MagicMock
from typing import Dict, Any

# Optional: numpy for deterministic numerical operations
try:
    import numpy as np

    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False


# =============================================================================
# DETERMINISTIC SEEDS
# =============================================================================

# Fixed seed for reproducibility
RANDOM_SEED = 42


@pytest.fixture(autouse=True)
def set_random_seeds():
    """
    Fix random seeds for reproducibility.

    All tests will use the same random sequence,
    making failures reproducible across machines.

    This fixture runs automatically for every test (autouse=True).
    """
    random.seed(RANDOM_SEED)

    if HAS_NUMPY:
        np.random.seed(RANDOM_SEED)

    yield

    # Reset after test (optional, for isolation)
    random.seed()
    if HAS_NUMPY:
        np.random.seed()


# =============================================================================
# MOCK FIXTURES
# =============================================================================


@pytest.fixture
def mock_algorithm():
    """
    Minimal mock of QCAlgorithm for unit testing.

    Provides the essential attributes and methods that engines expect.
    Does NOT simulate real trading - use QuantConnect backtests for that.

    Usage:
        def test_my_engine(mock_algorithm):
            engine = MyEngine(mock_algorithm)
            result = engine.calculate_something()
            assert result == expected
    """
    algo = MagicMock()

    # Time simulation
    algo.Time = MagicMock()
    algo.Time.hour = 10
    algo.Time.minute = 30

    # Portfolio state
    algo.Portfolio = MagicMock()
    algo.Portfolio.TotalPortfolioValue = 50000.0
    algo.Portfolio.Cash = 10000.0
    algo.Portfolio.Invested = False

    # Position access (returns mock for any symbol)
    def get_position(symbol):
        position = MagicMock()
        position.Invested = False
        position.Quantity = 0
        position.AveragePrice = 0.0
        position.HoldingsValue = 0.0
        position.UnrealizedProfit = 0.0
        return position

    algo.Portfolio.__getitem__ = MagicMock(side_effect=get_position)

    # Securities access
    algo.Securities = {}

    # Logging
    algo.Log = MagicMock()
    algo.Debug = MagicMock()
    algo.Error = MagicMock()

    # Order methods (should NOT be called by engines)
    algo.MarketOrder = MagicMock()
    algo.MarketOnOpenOrder = MagicMock()
    algo.LimitOrder = MagicMock()
    algo.Liquidate = MagicMock()

    # ObjectStore for persistence
    algo.ObjectStore = MagicMock()
    algo.ObjectStore.ContainsKey = MagicMock(return_value=False)
    algo.ObjectStore.Read = MagicMock(return_value="{}")
    algo.ObjectStore.Save = MagicMock()

    return algo


@pytest.fixture
def mock_portfolio_with_positions(mock_algorithm):
    """
    Mock algorithm with pre-configured positions.

    Usage:
        def test_with_positions(mock_portfolio_with_positions):
            algo = mock_portfolio_with_positions
            # algo.Portfolio["QLD"].Invested is True
    """
    positions = {
        "QLD": {"Invested": True, "Quantity": 100, "AveragePrice": 75.0, "HoldingsValue": 7500.0},
        "TQQQ": {"Invested": True, "Quantity": 50, "AveragePrice": 45.0, "HoldingsValue": 2250.0},
        "SHV": {"Invested": True, "Quantity": 200, "AveragePrice": 110.0, "HoldingsValue": 22000.0},
    }

    def get_position(symbol):
        pos = MagicMock()
        if symbol in positions:
            for key, value in positions[symbol].items():
                setattr(pos, key, value)
        else:
            pos.Invested = False
            pos.Quantity = 0
            pos.AveragePrice = 0.0
            pos.HoldingsValue = 0.0
        return pos

    mock_algorithm.Portfolio.__getitem__ = MagicMock(side_effect=get_position)
    mock_algorithm.Portfolio.Invested = True
    mock_algorithm.Portfolio.TotalPortfolioValue = 50000.0

    return mock_algorithm


# =============================================================================
# DETERMINISTIC DATA FIXTURES
# =============================================================================


@pytest.fixture
def deterministic_prices() -> Dict[str, list]:
    """
    Generate deterministic price data for testing.

    Same input -> same output, every time.
    Uses the fixed RANDOM_SEED for reproducibility.
    """
    random.seed(RANDOM_SEED)

    if HAS_NUMPY:
        np.random.seed(RANDOM_SEED)
        return {
            "SPY": [450.0 + np.random.randn() * 2 for _ in range(100)],
            "QLD": [75.0 + np.random.randn() * 1 for _ in range(100)],
            "TQQQ": [45.0 + np.random.randn() * 1.5 for _ in range(100)],
            "SSO": [65.0 + np.random.randn() * 1 for _ in range(100)],
            "TMF": [8.0 + np.random.randn() * 0.5 for _ in range(100)],
        }
    else:
        return {
            "SPY": [450.0 + random.gauss(0, 2) for _ in range(100)],
            "QLD": [75.0 + random.gauss(0, 1) for _ in range(100)],
            "TQQQ": [45.0 + random.gauss(0, 1.5) for _ in range(100)],
            "SSO": [65.0 + random.gauss(0, 1) for _ in range(100)],
            "TMF": [8.0 + random.gauss(0, 0.5) for _ in range(100)],
        }


@pytest.fixture
def sample_regime_scores() -> Dict[str, int]:
    """
    Sample regime scores for different market conditions.

    These are fixed values for testing regime-dependent logic.
    """
    return {
        "RISK_ON": 75,
        "NEUTRAL": 55,
        "CAUTIOUS": 35,
        "DEFENSIVE": 25,
        "RISK_OFF": 15,
    }


# =============================================================================
# TEST MARKERS
# =============================================================================


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')"
    )
    config.addinivalue_line("markers", "integration: marks tests as integration tests")
    config.addinivalue_line("markers", "architecture: marks tests that enforce architecture rules")
    config.addinivalue_line("markers", "options: marks tests related to options engine")
    config.addinivalue_line("markers", "oco: marks tests related to OCO manager")
    config.addinivalue_line("markers", "scenario: marks end-to-end scenario tests")


# =============================================================================
# INTEGRATION TEST FIXTURES (V2.1)
# =============================================================================


@pytest.fixture
def market_data_normal() -> Dict[str, Any]:
    """
    Normal market conditions for testing.

    VIX=15, SPY trending up, regime NEUTRAL.
    """
    return {
        "vix": 15.0,
        "spy_price": 450.0,
        "spy_change_pct": 0.005,  # +0.5%
        "regime_score": 65,
        "regime_state": "NEUTRAL",
        "adx": 28,
        "ma200": 440.0,
        "rsi": 55,
    }


@pytest.fixture
def market_data_high_vix() -> Dict[str, Any]:
    """
    High VIX conditions for testing.

    VIX=35, SPY volatile, regime CAUTIOUS.
    """
    return {
        "vix": 35.0,
        "spy_price": 435.0,
        "spy_change_pct": -0.015,  # -1.5%
        "regime_score": 42,
        "regime_state": "CAUTIOUS",
        "adx": 32,
        "ma200": 440.0,
        "rsi": 38,
    }


@pytest.fixture
def market_data_crash() -> Dict[str, Any]:
    """
    Crash conditions for testing.

    VIX=50, SPY crashing, regime RISK_OFF.
    """
    return {
        "vix": 50.0,
        "spy_price": 410.0,
        "spy_change_pct": -0.045,  # -4.5%
        "regime_score": 18,
        "regime_state": "RISK_OFF",
        "adx": 45,
        "ma200": 440.0,
        "rsi": 22,
    }


@pytest.fixture
def mock_algorithm_with_options(mock_algorithm):
    """
    Mock algorithm with options position for integration tests.

    Includes:
    - QQQ call position
    - Active OCO pair
    - Greeks data
    """
    # Add options position
    options_position = MagicMock()
    options_position.Invested = True
    options_position.Quantity = 10
    options_position.AveragePrice = 2.50
    options_position.HoldingsValue = 2500.0
    options_position.Symbol = "QQQ 260126C00450000"

    original_getitem = mock_algorithm.Portfolio.__getitem__

    def get_position_with_options(symbol):
        if "QQQ" in str(symbol) and "C" in str(symbol):
            return options_position
        return original_getitem(symbol)

    mock_algorithm.Portfolio.__getitem__ = MagicMock(side_effect=get_position_with_options)

    # Add Greeks
    mock_algorithm.current_greeks = {
        "delta": 0.55,
        "gamma": 0.08,
        "theta": -0.12,
        "vega": 0.15,
        "iv": 0.25,
    }

    return mock_algorithm


@pytest.fixture
def multi_engine_signals() -> list:
    """
    Sample signals from multiple engines for aggregation testing.
    """
    from models.target_weight import TargetWeight
    from models.enums import Urgency

    return [
        TargetWeight(
            symbol="QLD",
            target_weight=0.35,
            source="TREND",
            urgency=Urgency.EOD,
            reason="MA200_ADX_ENTRY"
        ),
        TargetWeight(
            symbol="TQQQ",
            target_weight=0.05,
            source="MR",
            urgency=Urgency.IMMEDIATE,
            reason="RSI Oversold"
        ),
        TargetWeight(
            symbol="QQQ_CALL",
            target_weight=0.20,
            source="OPT",
            urgency=Urgency.IMMEDIATE,
            reason="Entry Score=3.5"
        ),
        TargetWeight(
            symbol="TMF",
            target_weight=0.10,
            source="HEDGE",
            urgency=Urgency.EOD,
            reason="Regime=42"
        ),
        TargetWeight(
            symbol="SHV",
            target_weight=0.25,
            source="YIELD",
            urgency=Urgency.EOD,
            reason="Unallocated Cash"
        ),
    ]
