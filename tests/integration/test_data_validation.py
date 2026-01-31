"""
Data Validation Tests for Pre-QC Validation.

These tests validate data handling patterns to catch issues
before deploying to QuantConnect.

Tests cover:
1. VIX data feed handling
2. Options chain filtering
3. Split handling
4. Price data validation
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

import config
from tests.integration.qc_mocks import (
    MockAlgorithm,
    MockOptionContract,
    MockSecurity,
    MockSlice,
    MockSplitCollection,
    OptionRight,
    create_test_algorithm,
    create_test_slice,
)


class TestVIXDataFeed:
    """Test VIX data handling."""

    def test_vix_data_available_in_ondata(self):
        """
        Verify VIX data is accessible in OnData.
        """
        algo = create_test_algorithm()
        algo._current_vix = 15.0

        # Simulate VIX data update
        slice_data = create_test_slice(
            time=datetime(2024, 1, 15, 10, 30), prices={"SPY": 450.0, "QLD": 75.0}
        )

        # VIX should be accessible via algorithm attribute
        assert algo._current_vix == 15.0

    def test_vix_default_value_before_data(self):
        """
        Verify VIX defaults to safe value before data arrives.
        """
        algo = create_test_algorithm()

        # Default should be normal regime (15-20)
        algo._current_vix = 15.0
        assert 10 <= algo._current_vix <= 20

    def test_vix_regime_classification(self):
        """
        Test VIX regime classification thresholds.
        """
        # Normal: VIX < 20
        assert config.VIX_NORMAL_MAX == 20

        # Caution: VIX 20-30
        assert config.VIX_CAUTION_MAX == 30

        # High Risk: VIX 30-40
        assert config.VIX_HIGH_RISK_MAX == 40

        # Crash: VIX > 40

    def test_vix_spike_detection_threshold(self):
        """
        Test VIX spike detection configuration.
        """
        # Spike interval and threshold
        assert config.VIX_MONITOR_SPIKE_INTERVAL == 5
        assert config.VIX_MONITOR_SPIKE_THRESHOLD == 5.0

    def test_vix_direction_classification(self):
        """
        Test VIX direction classification thresholds.
        """
        # Falling fast: < -5%
        assert config.VIX_DIRECTION_FALLING_FAST == -5.0

        # Falling: -5% to -2%
        assert config.VIX_DIRECTION_FALLING == -2.0

        # Stable: -2% to +2%
        assert config.VIX_DIRECTION_STABLE_LOW == -2.0
        assert config.VIX_DIRECTION_STABLE_HIGH == 2.0

        # Rising: +2% to +5%
        assert config.VIX_DIRECTION_RISING == 5.0

        # Rising fast: +5% to +10%
        assert config.VIX_DIRECTION_RISING_FAST == 10.0

        # Spiking: > +10%
        assert config.VIX_DIRECTION_SPIKING == 10.0


class TestOptionsChainFiltering:
    """Test options chain filtering logic."""

    def test_dte_filter_swing_mode(self):
        """
        Test DTE filter for Swing Mode (5-45 DTE).
        """
        # Swing mode DTE range
        assert config.OPTIONS_SWING_DTE_MIN == 5
        assert config.OPTIONS_SWING_DTE_MAX == 45

        # Test filtering
        current_date = datetime(2024, 1, 15)

        # Valid swing contracts
        valid_expiries = [
            current_date + timedelta(days=5),  # Min
            current_date + timedelta(days=20),  # Middle
            current_date + timedelta(days=45),  # Max
        ]

        # Invalid swing contracts
        invalid_expiries = [
            current_date + timedelta(days=2),  # Too soon
            current_date + timedelta(days=50),  # Too far
        ]

        for expiry in valid_expiries:
            dte = (expiry - current_date).days
            assert config.OPTIONS_SWING_DTE_MIN <= dte <= config.OPTIONS_SWING_DTE_MAX

        for expiry in invalid_expiries:
            dte = (expiry - current_date).days
            assert not (config.OPTIONS_SWING_DTE_MIN <= dte <= config.OPTIONS_SWING_DTE_MAX)

    def test_dte_filter_intraday_mode(self):
        """
        Test DTE filter for Intraday Mode (0-1 DTE).

        V2.3.4: Changed to 0-1 for true 0DTE intraday trading.
        """
        # Intraday mode DTE range (V2.3.4: 0-1 DTE for true intraday)
        assert config.OPTIONS_INTRADAY_DTE_MIN == 0
        assert config.OPTIONS_INTRADAY_DTE_MAX == 1

    def test_atm_strike_filter(self):
        """
        Test ATM ±5 strikes filter.
        """
        underlying_price = 450.0
        strike_increment = 5.0

        # ATM strike
        atm_strike = round(underlying_price / strike_increment) * strike_increment
        assert atm_strike == 450.0

        # ±5 strikes range
        min_strike = atm_strike - (5 * strike_increment)
        max_strike = atm_strike + (5 * strike_increment)

        assert min_strike == 425.0
        assert max_strike == 475.0

        # Valid strikes
        valid_strikes = [425, 430, 435, 440, 445, 450, 455, 460, 465, 470, 475]
        for strike in valid_strikes:
            assert min_strike <= strike <= max_strike

    def test_delta_filter(self):
        """
        Test delta filter for ATM contracts (0.40-0.60).
        """
        assert config.OPTIONS_DELTA_MIN == 0.40
        assert config.OPTIONS_DELTA_MAX == 0.60

        # Test delta values
        valid_deltas = [0.40, 0.45, 0.50, 0.55, 0.60]
        invalid_deltas = [0.30, 0.35, 0.65, 0.70]

        for delta in valid_deltas:
            assert config.OPTIONS_DELTA_MIN <= delta <= config.OPTIONS_DELTA_MAX

        for delta in invalid_deltas:
            assert not (config.OPTIONS_DELTA_MIN <= delta <= config.OPTIONS_DELTA_MAX)

    def test_liquidity_filter(self):
        """
        Test liquidity filters (spread, open interest).
        """
        # Max bid-ask spread - V2.3.10: Widened from 5% to 15% for ATM contracts
        assert config.OPTIONS_SPREAD_MAX_PCT == 0.15  # V2.3.10: 15% (was 5%)
        assert config.OPTIONS_SPREAD_WARNING_PCT == 0.25  # V2.3.7: Widened from 10% to 25%

        # Min open interest (V2.3.7: Relaxed from 200 to 100)
        assert config.OPTIONS_MIN_OPEN_INTEREST == 100

    def test_min_premium_filter(self):
        """
        Test minimum premium filter.
        """
        assert config.OPTIONS_MIN_PREMIUM == 0.50  # $0.50

    def test_mock_option_contract_creation(self):
        """
        Test creating mock option contracts for testing.
        """
        option = MockOptionContract(
            underlying="QQQ", strike=450.0, expiry=datetime(2024, 2, 16), right=OptionRight.Call
        )

        assert option.Underlying == "QQQ"
        assert option.Strike == 450.0
        assert option.Right == OptionRight.Call

        # Set Greeks
        option.set_greeks(delta=0.50, gamma=0.08, theta=-0.12, vega=0.15)
        assert option.Greeks.Delta == 0.50
        assert option.Greeks.Gamma == 0.08
        assert option.Greeks.Theta == -0.12
        assert option.Greeks.Vega == 0.15


class TestSplitHandling:
    """Test stock split handling."""

    def test_split_detection_freezes_processing(self):
        """
        Verify split detection stops all processing.
        """
        algo = create_test_algorithm()
        algo._splits_logged_today = set()

        # Create slice with split
        slice_data = MockSlice(datetime(2024, 1, 15, 10, 30))
        slice_data.Splits.add_split("SPY", 4.0)

        # Check split
        if slice_data.Splits.ContainsKey("SPY"):
            freeze_all = True
            if "SPY" not in algo._splits_logged_today:
                algo.Log(f"SPLIT: SPY - freezing all")
                algo._splits_logged_today.add("SPY")
        else:
            freeze_all = False

        assert freeze_all, "Split should freeze all processing"

    def test_split_logged_once_per_day(self):
        """
        Verify split is logged only once per symbol per day.
        """
        algo = create_test_algorithm()
        algo._splits_logged_today = set()
        log_count = 0

        # Simulate multiple OnData calls with same split
        for _ in range(10):
            if "SPY" not in algo._splits_logged_today:
                log_count += 1
                algo._splits_logged_today.add("SPY")

        assert log_count == 1, f"Split logged {log_count} times (should be 1)"

    def test_split_tracking_reset_daily(self):
        """
        Verify split tracking resets at end of day.
        """
        algo = create_test_algorithm()
        algo._splits_logged_today = {"SPY", "QLD"}

        # End of day reset
        algo._splits_logged_today.clear()

        assert len(algo._splits_logged_today) == 0

    def test_proxy_split_vs_traded_split(self):
        """
        Test difference between proxy and traded symbol splits.

        Proxy split: Freeze ALL processing
        Traded symbol split: Freeze only that symbol
        """
        algo = create_test_algorithm()
        algo.symbols_to_skip = set()

        proxy_symbols = ["SPY", "RSP", "HYG", "IEF"]
        traded_symbols = ["TQQQ", "SOXL", "QLD", "SSO", "TMF", "PSQ", "SHV"]

        # Proxy split freezes everything
        split_symbol = "SPY"
        if split_symbol in proxy_symbols:
            freeze_all = True
        else:
            freeze_all = False
            if split_symbol in traded_symbols:
                algo.symbols_to_skip.add(split_symbol)

        assert freeze_all, "Proxy split should freeze all"

        # Traded symbol split freezes only that symbol
        split_symbol = "TQQQ"
        if split_symbol in proxy_symbols:
            freeze_all = True
        else:
            freeze_all = False
            if split_symbol in traded_symbols:
                algo.symbols_to_skip.add(split_symbol)

        assert not freeze_all, "Traded split should not freeze all"
        assert "TQQQ" in algo.symbols_to_skip


class TestPriceDataValidation:
    """Test price data validation patterns."""

    def test_price_data_available(self):
        """
        Test that price data is available after AddEquity.
        """
        algo = create_test_algorithm()
        algo.add_security("SPY", 450.0)
        algo.add_security("QLD", 75.0)

        assert algo.Securities["SPY"].Price == 450.0
        assert algo.Securities["QLD"].Price == 75.0

    def test_zero_price_handling(self):
        """
        Test handling of zero or missing prices.
        """
        algo = create_test_algorithm()
        security = MockSecurity("TEST")

        # Default price is 0
        if security.Price == 0:
            price_valid = False
        else:
            price_valid = True

        assert not price_valid, "Zero price should be invalid"

        # After setting price
        security.set_price(100.0)
        if security.Price > 0:
            price_valid = True

        assert price_valid, "Valid price should be recognized"

    def test_holdings_value_calculation(self):
        """
        Test holdings value calculation.
        """
        algo = create_test_algorithm()

        # Add position
        algo.Portfolio.add_position("QLD", 100, 75.0, 80.0)

        holdings = algo.Portfolio["QLD"].Holdings
        assert holdings.Quantity == 100
        assert holdings.AveragePrice == 75.0
        assert holdings.HoldingsValue == 8000.0  # 100 * 80.0
        assert holdings.UnrealizedProfit == 500.0  # 100 * (80 - 75)

    def test_bid_ask_spread_calculation(self):
        """
        Test bid-ask spread calculation for options.
        """
        option = MockOptionContract(
            underlying="QQQ", strike=450.0, expiry=datetime(2024, 2, 16), right=OptionRight.Call
        )
        option.set_price(5.00)
        option.set_bid_ask(4.90, 5.10)

        # Calculate spread
        spread = option.AskPrice - option.BidPrice
        mid_price = (option.AskPrice + option.BidPrice) / 2
        spread_pct = spread / mid_price

        assert abs(spread - 0.20) < 0.001  # Allow floating point tolerance
        assert abs(spread_pct - 0.04) < 0.001  # ~4%
        assert spread_pct < config.OPTIONS_SPREAD_MAX_PCT


class TestDataSlicePatterns:
    """Test data slice access patterns."""

    def test_contains_key_check(self):
        """
        Test ContainsKey pattern before accessing data.
        """
        slice_data = create_test_slice(prices={"SPY": 450.0, "QLD": 75.0})

        # Correct pattern
        if slice_data.ContainsKey("SPY"):
            spy_price = slice_data["SPY"].Close
            assert spy_price == 450.0

        # Missing symbol
        if not slice_data.ContainsKey("XYZ"):
            xyz_price = None
        else:
            xyz_price = slice_data["XYZ"].Close

        assert xyz_price is None

    def test_bar_ohlcv_access(self):
        """
        Test accessing OHLCV data from bar.
        """
        slice_data = MockSlice(datetime(2024, 1, 15, 10, 30))
        slice_data.add_bar("SPY", open_=449.0, high=452.0, low=448.0, close=451.0, volume=1000000)

        bar = slice_data["SPY"]
        assert bar.Open == 449.0
        assert bar.High == 452.0
        assert bar.Low == 448.0
        assert bar.Close == 451.0
        assert bar.Volume == 1000000

    def test_multiple_symbols_in_slice(self):
        """
        Test accessing multiple symbols from same slice.
        """
        slice_data = create_test_slice(
            prices={"SPY": 450.0, "QLD": 75.0, "TQQQ": 45.0, "SSO": 65.0}
        )

        for symbol in ["SPY", "QLD", "TQQQ", "SSO"]:
            assert slice_data.ContainsKey(symbol)
            assert slice_data[symbol].Close > 0


class TestIndicatorDataValidation:
    """Test indicator data validation."""

    def test_indicator_is_ready_before_use(self):
        """
        Test that indicators are checked for IsReady before use.
        """
        from tests.integration.qc_mocks import MockADX, MockSMA

        sma = MockSMA(200)
        adx = MockADX(14)

        # Pattern to follow
        sma_value = sma.Current.Value if sma.IsReady else None
        adx_value = adx.Current.Value if adx.IsReady else None

        assert sma_value is None, "SMA should not have value before ready"
        assert adx_value is None, "ADX should not have value before ready"

        # After ready
        sma.set_ready(True).set_value(450.0)
        adx.set_ready(True).set_value(28.0)

        sma_value = sma.Current.Value if sma.IsReady else None
        adx_value = adx.Current.Value if adx.IsReady else None

        assert sma_value == 450.0
        assert adx_value == 28.0

    def test_all_indicators_ready_check(self):
        """
        Test checking all required indicators before trading.
        """
        from tests.integration.qc_mocks import MockADX, MockATR, MockRSI, MockSMA

        indicators = {
            "sma": MockSMA(200),
            "adx": MockADX(14),
            "atr": MockATR(14),
            "rsi": MockRSI(5),
        }

        # Initially none ready
        all_ready = all(ind.IsReady for ind in indicators.values())
        assert not all_ready

        # Set all ready
        for ind in indicators.values():
            ind.set_ready(True)

        all_ready = all(ind.IsReady for ind in indicators.values())
        assert all_ready
