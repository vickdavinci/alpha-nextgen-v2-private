"""
Comprehensive QuantConnect Mock Infrastructure for Integration Testing.

This module provides realistic mock implementations of QuantConnect classes
for testing trading algorithms without requiring the actual QC platform.

Usage:
    from tests.integration.qc_mocks import (
        MockAlgorithm,
        MockSMA,
        MockRSI,
        MockADX,
        MockATR,
        MockBollingerBands,
    )

    # Create a mock algorithm
    algo = MockAlgorithm()
    algo.set_time(2024, 1, 15, 10, 30)
    algo.Portfolio.TotalPortfolioValue = 100000

    # Configure indicators
    sma = MockSMA(200)
    sma.set_value(450.0)
    sma.set_ready(True)

    # Run tests
    assert sma.Current.Value == 450.0
    assert sma.IsReady == True
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

# =============================================================================
# ENUMS (Match QuantConnect's enums)
# =============================================================================


class Resolution(Enum):
    """QuantConnect Resolution enum."""

    Tick = 0
    Second = 1
    Minute = 2
    Hour = 3
    Daily = 4


class OrderType(Enum):
    """QuantConnect OrderType enum."""

    Market = 0
    Limit = 1
    StopMarket = 2
    StopLimit = 3
    MarketOnOpen = 4
    MarketOnClose = 5


class OrderStatus(Enum):
    """QuantConnect OrderStatus enum."""

    New = 0
    Submitted = 1
    PartiallyFilled = 2
    Filled = 3
    Canceled = 4
    Invalid = 5


class SecurityType(Enum):
    """QuantConnect SecurityType enum."""

    Equity = 1
    Option = 2
    Future = 3
    Forex = 4
    Crypto = 5


class OptionRight(Enum):
    """QuantConnect OptionRight enum."""

    Call = 0
    Put = 1


# =============================================================================
# INDICATOR VALUE WRAPPER
# =============================================================================


@dataclass
class IndicatorValue:
    """Wrapper for indicator current value (mimics QuantConnect's IndicatorDataPoint)."""

    Value: float = 0.0

    def __float__(self) -> float:
        return self.Value

    def __repr__(self) -> str:
        return f"IndicatorValue({self.Value})"


# =============================================================================
# BASE INDICATOR CLASS
# =============================================================================


class MockIndicator:
    """
    Base class for all mock indicators.

    Provides IsReady property and Current.Value access pattern
    that matches QuantConnect's indicator interface.
    """

    def __init__(self, period: int = 14, name: str = "MockIndicator"):
        self._period = period
        self._name = name
        self._is_ready = False
        self._current = IndicatorValue(0.0)
        self._samples = 0
        self._history: List[float] = []

    @property
    def IsReady(self) -> bool:
        """Whether the indicator has enough data to produce valid values."""
        return self._is_ready

    @property
    def Current(self) -> IndicatorValue:
        """Current indicator value."""
        return self._current

    @property
    def Period(self) -> int:
        """Indicator period."""
        return self._period

    @property
    def Name(self) -> str:
        """Indicator name."""
        return self._name

    @property
    def Samples(self) -> int:
        """Number of samples received."""
        return self._samples

    def set_ready(self, ready: bool) -> "MockIndicator":
        """Set the IsReady state (chainable)."""
        self._is_ready = ready
        return self

    def set_value(self, value: float) -> "MockIndicator":
        """Set the current value (chainable)."""
        self._current.Value = value
        self._history.append(value)
        self._samples += 1
        return self

    def Update(self, time: datetime, value: float) -> bool:
        """
        Update the indicator with a new value.

        Returns True if the indicator is ready after this update.
        """
        self._samples += 1
        self._history.append(value)

        # Auto-set ready after receiving enough samples
        if self._samples >= self._period:
            self._is_ready = True

        # Subclasses override to compute actual value
        self._compute_value()
        return self._is_ready

    def _compute_value(self) -> None:
        """Override in subclasses to compute indicator value."""
        pass

    def Reset(self) -> None:
        """Reset the indicator to its initial state."""
        self._is_ready = False
        self._current.Value = 0.0
        self._samples = 0
        self._history.clear()

    def __repr__(self) -> str:
        return f"{self._name}(period={self._period}, value={self._current.Value:.4f}, ready={self._is_ready})"


# =============================================================================
# SPECIFIC INDICATOR IMPLEMENTATIONS
# =============================================================================


class MockSMA(MockIndicator):
    """
    Mock Simple Moving Average indicator.

    Usage:
        sma = MockSMA(200)
        sma.set_value(450.0).set_ready(True)
        assert sma.Current.Value == 450.0
    """

    def __init__(self, period: int = 200):
        super().__init__(period, f"SMA({period})")

    def _compute_value(self) -> None:
        """Compute SMA from history."""
        if len(self._history) >= self._period:
            self._current.Value = sum(self._history[-self._period :]) / self._period


class MockRSI(MockIndicator):
    """
    Mock Relative Strength Index indicator.

    Usage:
        rsi = MockRSI(5)
        rsi.set_value(22.5).set_ready(True)  # Oversold
        assert rsi.Current.Value < 25  # MR entry eligible
    """

    def __init__(self, period: int = 14):
        super().__init__(period, f"RSI({period})")
        self._gains: List[float] = []
        self._losses: List[float] = []

    def _compute_value(self) -> None:
        """Simplified RSI computation (for testing, use set_value for precise control)."""
        if len(self._history) < 2:
            return

        change = self._history[-1] - self._history[-2]
        if change > 0:
            self._gains.append(change)
            self._losses.append(0)
        else:
            self._gains.append(0)
            self._losses.append(abs(change))

        if len(self._gains) >= self._period:
            avg_gain = sum(self._gains[-self._period :]) / self._period
            avg_loss = sum(self._losses[-self._period :]) / self._period

            if avg_loss == 0:
                self._current.Value = 100.0
            else:
                rs = avg_gain / avg_loss
                self._current.Value = 100 - (100 / (1 + rs))


class MockADX(MockIndicator):
    """
    Mock Average Directional Index indicator.

    Usage:
        adx = MockADX(14)
        adx.set_value(28.0).set_ready(True)  # Strong trend
        assert adx.Current.Value >= 25  # Trend entry eligible
    """

    def __init__(self, period: int = 14):
        super().__init__(period, f"ADX({period})")
        self._plus_di = IndicatorValue(0.0)
        self._minus_di = IndicatorValue(0.0)

    @property
    def PositiveDirectionalIndex(self) -> IndicatorValue:
        """+DI component."""
        return self._plus_di

    @property
    def NegativeDirectionalIndex(self) -> IndicatorValue:
        """-DI component."""
        return self._minus_di

    def set_directional_indices(self, plus_di: float, minus_di: float) -> "MockADX":
        """Set the directional index components (chainable)."""
        self._plus_di.Value = plus_di
        self._minus_di.Value = minus_di
        return self


class MockATR(MockIndicator):
    """
    Mock Average True Range indicator.

    Usage:
        atr = MockATR(14)
        atr.set_value(2.5).set_ready(True)
        # Use for volatility-based position sizing
    """

    def __init__(self, period: int = 14):
        super().__init__(period, f"ATR({period})")


class MockBollingerBands(MockIndicator):
    """
    Mock Bollinger Bands indicator.

    Provides Upper, Middle (SMA), and Lower bands.

    Usage:
        bb = MockBollingerBands(20, 2)
        bb.set_bands(upper=455.0, middle=450.0, lower=445.0)
        bb.set_ready(True)

        assert bb.UpperBand.Current.Value == 455.0
        assert bb.MiddleBand.Current.Value == 450.0
        assert bb.LowerBand.Current.Value == 445.0
    """

    def __init__(self, period: int = 20, k: float = 2.0):
        super().__init__(period, f"BB({period},{k})")
        self._k = k
        self._upper_band = MockIndicator(period, "UpperBand")
        self._middle_band = MockIndicator(period, "MiddleBand")
        self._lower_band = MockIndicator(period, "LowerBand")
        self._bandwidth = IndicatorValue(0.0)
        self._percent_b = IndicatorValue(0.0)

    @property
    def UpperBand(self) -> MockIndicator:
        """Upper Bollinger Band."""
        return self._upper_band

    @property
    def MiddleBand(self) -> MockIndicator:
        """Middle Band (SMA)."""
        return self._middle_band

    @property
    def LowerBand(self) -> MockIndicator:
        """Lower Bollinger Band."""
        return self._lower_band

    @property
    def BandWidth(self) -> IndicatorValue:
        """Bandwidth indicator."""
        return self._bandwidth

    @property
    def PercentB(self) -> IndicatorValue:
        """%B indicator (price position within bands)."""
        return self._percent_b

    def set_bands(
        self, upper: float, middle: float, lower: float, bandwidth: Optional[float] = None
    ) -> "MockBollingerBands":
        """Set all band values (chainable)."""
        self._upper_band.set_value(upper)
        self._middle_band.set_value(middle)
        self._lower_band.set_value(lower)

        if bandwidth is not None:
            self._bandwidth.Value = bandwidth
        else:
            # Auto-calculate bandwidth
            self._bandwidth.Value = (upper - lower) / middle if middle != 0 else 0

        return self

    def set_ready(self, ready: bool) -> "MockBollingerBands":
        """Set ready state for all bands (chainable)."""
        super().set_ready(ready)
        self._upper_band.set_ready(ready)
        self._middle_band.set_ready(ready)
        self._lower_band.set_ready(ready)
        return self


class MockEMA(MockIndicator):
    """Mock Exponential Moving Average indicator."""

    def __init__(self, period: int = 12):
        super().__init__(period, f"EMA({period})")
        self._multiplier = 2 / (period + 1)

    def _compute_value(self) -> None:
        """Compute EMA from history."""
        if len(self._history) == 1:
            self._current.Value = self._history[0]
        elif len(self._history) > 1:
            prev_ema = self._current.Value
            self._current.Value = (self._history[-1] - prev_ema) * self._multiplier + prev_ema


class MockMACD(MockIndicator):
    """Mock MACD indicator with Signal and Histogram."""

    def __init__(self, fast_period: int = 12, slow_period: int = 26, signal_period: int = 9):
        super().__init__(slow_period, f"MACD({fast_period},{slow_period},{signal_period})")
        self._fast_period = fast_period
        self._signal_period = signal_period
        self._signal = IndicatorValue(0.0)
        self._histogram = IndicatorValue(0.0)
        self._fast = MockEMA(fast_period)
        self._slow = MockEMA(slow_period)

    @property
    def Signal(self) -> IndicatorValue:
        """MACD Signal line."""
        return self._signal

    @property
    def Histogram(self) -> IndicatorValue:
        """MACD Histogram (MACD - Signal)."""
        return self._histogram

    @property
    def Fast(self) -> MockEMA:
        """Fast EMA component."""
        return self._fast

    @property
    def Slow(self) -> MockEMA:
        """Slow EMA component."""
        return self._slow

    def set_macd_values(
        self, macd: float, signal: float, histogram: Optional[float] = None
    ) -> "MockMACD":
        """Set MACD values (chainable)."""
        self._current.Value = macd
        self._signal.Value = signal
        self._histogram.Value = histogram if histogram is not None else (macd - signal)
        return self


class MockStochastics(MockIndicator):
    """Mock Stochastic Oscillator with %K and %D."""

    def __init__(self, k_period: int = 14, d_period: int = 3):
        super().__init__(k_period, f"STO({k_period},{d_period})")
        self._d_period = d_period
        self._stochastics_k = IndicatorValue(0.0)
        self._stochastics_d = IndicatorValue(0.0)

    @property
    def StochK(self) -> IndicatorValue:
        """%K line."""
        return self._stochastics_k

    @property
    def StochD(self) -> IndicatorValue:
        """%D line (signal)."""
        return self._stochastics_d

    def set_stoch_values(self, k: float, d: float) -> "MockStochastics":
        """Set stochastic values (chainable)."""
        self._stochastics_k.Value = k
        self._stochastics_d.Value = d
        self._current.Value = k  # Current typically tracks %K
        return self


# =============================================================================
# SECURITY HOLDINGS
# =============================================================================


@dataclass
class MockHoldings:
    """Mock SecurityHolding for position information."""

    Quantity: int = 0
    AveragePrice: float = 0.0
    HoldingsValue: float = 0.0
    UnrealizedProfit: float = 0.0
    UnrealizedProfitPercent: float = 0.0
    AbsoluteHoldingsCost: float = 0.0
    AbsoluteQuantity: int = 0

    @property
    def Invested(self) -> bool:
        """Whether we have a position."""
        return self.Quantity != 0

    @property
    def IsLong(self) -> bool:
        """Whether position is long."""
        return self.Quantity > 0

    @property
    def IsShort(self) -> bool:
        """Whether position is short."""
        return self.Quantity < 0

    def set_position(
        self,
        quantity: int,
        avg_price: float,
        current_price: Optional[float] = None,
    ) -> "MockHoldings":
        """Configure position details (chainable)."""
        self.Quantity = quantity
        self.AbsoluteQuantity = abs(quantity)
        self.AveragePrice = avg_price
        self.AbsoluteHoldingsCost = abs(quantity) * avg_price

        if current_price is not None:
            self.HoldingsValue = quantity * current_price
            self.UnrealizedProfit = quantity * (current_price - avg_price)
            if self.AbsoluteHoldingsCost > 0:
                self.UnrealizedProfitPercent = self.UnrealizedProfit / self.AbsoluteHoldingsCost
        else:
            self.HoldingsValue = quantity * avg_price
            self.UnrealizedProfit = 0.0
            self.UnrealizedProfitPercent = 0.0

        return self


# =============================================================================
# MOCK SECURITY
# =============================================================================


class MockSecurity:
    """
    Mock Security class representing a tradeable instrument.

    Provides price data, holdings, and basic security properties.

    Usage:
        security = MockSecurity("SPY")
        security.set_price(450.0, open_=449.0, high=452.0, low=448.0, volume=1000000)
        security.Holdings.set_position(100, 445.0, 450.0)

        assert security.Price == 450.0
        assert security.Holdings.UnrealizedProfit == 500.0
    """

    def __init__(
        self,
        symbol: str,
        security_type: SecurityType = SecurityType.Equity,
    ):
        self._symbol = symbol
        self._security_type = security_type
        self._price = 0.0
        self._open = 0.0
        self._high = 0.0
        self._low = 0.0
        self._close = 0.0
        self._volume = 0
        self._bid_price = 0.0
        self._ask_price = 0.0
        self._bid_size = 0
        self._ask_size = 0
        self._holdings = MockHoldings()
        self._is_tradable = True
        self._has_data = True

    @property
    def Symbol(self) -> str:
        """Security symbol."""
        return self._symbol

    @property
    def Type(self) -> SecurityType:
        """Security type."""
        return self._security_type

    @property
    def Price(self) -> float:
        """Current price."""
        return self._price

    @property
    def Open(self) -> float:
        """Open price."""
        return self._open

    @property
    def High(self) -> float:
        """High price."""
        return self._high

    @property
    def Low(self) -> float:
        """Low price."""
        return self._low

    @property
    def Close(self) -> float:
        """Close price (same as Price for current bar)."""
        return self._close

    @property
    def Volume(self) -> int:
        """Volume."""
        return self._volume

    @property
    def BidPrice(self) -> float:
        """Best bid price."""
        return self._bid_price

    @property
    def AskPrice(self) -> float:
        """Best ask price."""
        return self._ask_price

    @property
    def BidSize(self) -> int:
        """Best bid size."""
        return self._bid_size

    @property
    def AskSize(self) -> int:
        """Best ask size."""
        return self._ask_size

    @property
    def Holdings(self) -> MockHoldings:
        """Position holdings."""
        return self._holdings

    @property
    def Invested(self) -> bool:
        """Whether we have a position in this security."""
        return self._holdings.Invested

    @property
    def IsTradable(self) -> bool:
        """Whether security can be traded."""
        return self._is_tradable

    @property
    def HasData(self) -> bool:
        """Whether security has data."""
        return self._has_data

    def set_price(
        self,
        price: float,
        open_: Optional[float] = None,
        high: Optional[float] = None,
        low: Optional[float] = None,
        volume: Optional[int] = None,
    ) -> "MockSecurity":
        """Set price data (chainable)."""
        self._price = price
        self._close = price
        self._open = open_ if open_ is not None else price
        self._high = high if high is not None else price
        self._low = low if low is not None else price
        self._volume = volume if volume is not None else 0

        # Auto-set bid/ask around price
        spread = price * 0.0001  # 1 basis point spread
        self._bid_price = price - spread
        self._ask_price = price + spread

        return self

    def set_bid_ask(
        self,
        bid: float,
        ask: float,
        bid_size: int = 100,
        ask_size: int = 100,
    ) -> "MockSecurity":
        """Set bid/ask data (chainable)."""
        self._bid_price = bid
        self._ask_price = ask
        self._bid_size = bid_size
        self._ask_size = ask_size
        return self

    def set_tradable(self, tradable: bool) -> "MockSecurity":
        """Set tradability (chainable)."""
        self._is_tradable = tradable
        return self


# =============================================================================
# MOCK OPTION CONTRACT
# =============================================================================


@dataclass
class MockGreeks:
    """Mock option Greeks."""

    Delta: float = 0.0
    Gamma: float = 0.0
    Theta: float = 0.0
    Vega: float = 0.0
    Rho: float = 0.0
    ImpliedVolatility: float = 0.0


class MockOptionContract(MockSecurity):
    """
    Mock Option Contract extending MockSecurity.

    Usage:
        option = MockOptionContract("QQQ", strike=400, expiry=datetime(2024, 2, 16), right=OptionRight.Call)
        option.set_price(5.50)
        option.Greeks.Delta = 0.45
    """

    def __init__(
        self,
        underlying: str,
        strike: float,
        expiry: datetime,
        right: OptionRight = OptionRight.Call,
    ):
        symbol = f"{underlying}_{expiry.strftime('%y%m%d')}_{right.name[0]}{strike:.0f}"
        super().__init__(symbol, SecurityType.Option)
        self._underlying = underlying
        self._strike = strike
        self._expiry = expiry
        self._right = right
        self._greeks = MockGreeks()
        self._underlying_price = 0.0
        self._open_interest = 0
        self._implied_volatility = 0.0

    @property
    def Underlying(self) -> str:
        """Underlying symbol."""
        return self._underlying

    @property
    def Strike(self) -> float:
        """Strike price."""
        return self._strike

    @property
    def Expiry(self) -> datetime:
        """Expiration date."""
        return self._expiry

    @property
    def Right(self) -> OptionRight:
        """Option right (Call/Put)."""
        return self._right

    @property
    def Greeks(self) -> MockGreeks:
        """Option Greeks."""
        return self._greeks

    @property
    def UnderlyingLastPrice(self) -> float:
        """Underlying's last price."""
        return self._underlying_price

    @property
    def OpenInterest(self) -> int:
        """Open interest."""
        return self._open_interest

    @property
    def ImpliedVolatility(self) -> float:
        """Implied volatility."""
        return self._implied_volatility

    def set_greeks(
        self,
        delta: float = 0.0,
        gamma: float = 0.0,
        theta: float = 0.0,
        vega: float = 0.0,
        iv: float = 0.0,
    ) -> "MockOptionContract":
        """Set Greeks (chainable)."""
        self._greeks.Delta = delta
        self._greeks.Gamma = gamma
        self._greeks.Theta = theta
        self._greeks.Vega = vega
        self._greeks.ImpliedVolatility = iv
        self._implied_volatility = iv
        return self

    def set_underlying_price(self, price: float) -> "MockOptionContract":
        """Set underlying price (chainable)."""
        self._underlying_price = price
        return self

    def days_to_expiry(self, current_time: datetime) -> int:
        """Calculate days to expiration."""
        return (self._expiry - current_time).days


# =============================================================================
# MOCK PORTFOLIO
# =============================================================================


class MockPortfolio:
    """
    Mock Portfolio class for tracking positions and values.

    Usage:
        portfolio = MockPortfolio()
        portfolio.TotalPortfolioValue = 100000
        portfolio.Cash = 25000

        # Add a position
        portfolio.add_position("QLD", 100, 75.0, 80.0)

        # Access position
        assert portfolio["QLD"].Invested == True
        assert portfolio["QLD"].Holdings.Quantity == 100
    """

    def __init__(self):
        self._total_value = 100000.0
        self._cash = 50000.0
        self._unsettled_cash = 0.0
        self._margin_remaining = 100000.0
        self._total_unrealized_profit = 0.0
        self._total_profit = 0.0
        self._positions: Dict[str, MockSecurity] = {}
        self._default_security_type = SecurityType.Equity

    @property
    def TotalPortfolioValue(self) -> float:
        """Total portfolio value."""
        return self._total_value

    @TotalPortfolioValue.setter
    def TotalPortfolioValue(self, value: float) -> None:
        self._total_value = value

    @property
    def Cash(self) -> float:
        """Available cash."""
        return self._cash

    @Cash.setter
    def Cash(self, value: float) -> None:
        self._cash = value

    @property
    def UnsettledCash(self) -> float:
        """Unsettled cash from recent trades."""
        return self._unsettled_cash

    @UnsettledCash.setter
    def UnsettledCash(self, value: float) -> None:
        self._unsettled_cash = value

    @property
    def MarginRemaining(self) -> float:
        """Remaining margin."""
        return self._margin_remaining

    @MarginRemaining.setter
    def MarginRemaining(self, value: float) -> None:
        self._margin_remaining = value

    @property
    def TotalUnrealizedProfit(self) -> float:
        """Total unrealized profit across all positions."""
        return sum(
            pos.Holdings.UnrealizedProfit
            for pos in self._positions.values()
            if pos.Holdings.Invested
        )

    @property
    def TotalProfit(self) -> float:
        """Total realized + unrealized profit."""
        return self._total_profit + self.TotalUnrealizedProfit

    @property
    def Invested(self) -> bool:
        """Whether any positions are held."""
        return any(pos.Holdings.Invested for pos in self._positions.values())

    def __getitem__(self, symbol: str) -> MockSecurity:
        """Get security by symbol, creating if not exists."""
        if symbol not in self._positions:
            self._positions[symbol] = MockSecurity(symbol, self._default_security_type)
        return self._positions[symbol]

    def __contains__(self, symbol: str) -> bool:
        """Check if symbol exists in portfolio."""
        return symbol in self._positions

    def add_position(
        self,
        symbol: str,
        quantity: int,
        avg_price: float,
        current_price: Optional[float] = None,
        security_type: SecurityType = SecurityType.Equity,
    ) -> MockSecurity:
        """Add or update a position."""
        if symbol not in self._positions:
            self._positions[symbol] = MockSecurity(symbol, security_type)

        security = self._positions[symbol]
        security.Holdings.set_position(quantity, avg_price, current_price)

        if current_price is not None:
            security.set_price(current_price)
        else:
            security.set_price(avg_price)

        return security

    def clear_position(self, symbol: str) -> None:
        """Clear a position (set quantity to 0)."""
        if symbol in self._positions:
            self._positions[symbol].Holdings.set_position(0, 0.0)

    def get_holdings_value(self) -> float:
        """Get total holdings value (excluding cash)."""
        return sum(
            pos.Holdings.HoldingsValue for pos in self._positions.values() if pos.Holdings.Invested
        )

    def keys(self):
        """Return position symbols."""
        return self._positions.keys()

    def values(self):
        """Return position securities."""
        return self._positions.values()

    def items(self):
        """Return symbol, security pairs."""
        return self._positions.items()


# =============================================================================
# MOCK ORDER TICKET
# =============================================================================


@dataclass
class MockOrderTicket:
    """Mock order ticket returned from order methods."""

    OrderId: int = 0
    Symbol: str = ""
    Quantity: int = 0
    OrderType: OrderType = OrderType.Market
    Status: OrderStatus = OrderStatus.New
    AverageFillPrice: float = 0.0
    QuantityFilled: int = 0
    Tag: str = ""
    SubmitRequest: Any = None

    def Cancel(self, tag: str = "") -> bool:
        """Cancel the order."""
        if self.Status in [OrderStatus.New, OrderStatus.Submitted]:
            self.Status = OrderStatus.Canceled
            return True
        return False

    def Update(self, fields: Dict[str, Any]) -> bool:
        """Update order fields."""
        for key, value in fields.items():
            if hasattr(self, key):
                setattr(self, key, value)
        return True

    def Get(self, field: str) -> Any:
        """Get order field value."""
        return getattr(self, field, None)


# =============================================================================
# MOCK OBJECT STORE
# =============================================================================


class MockObjectStore:
    """
    Mock ObjectStore for state persistence testing.

    Usage:
        store = MockObjectStore()
        store.Save("my_key", json.dumps({"value": 42}))
        assert store.ContainsKey("my_key")
        data = json.loads(store.Read("my_key"))
    """

    def __init__(self):
        self._store: Dict[str, str] = {}
        self._save_count = 0
        self._read_count = 0

    def Save(self, key: str, value: str) -> bool:
        """Save data to store."""
        self._store[key] = value
        self._save_count += 1
        return True

    def Read(self, key: str) -> str:
        """Read data from store."""
        self._read_count += 1
        return self._store.get(key, "")

    def ContainsKey(self, key: str) -> bool:
        """Check if key exists."""
        return key in self._store

    def Delete(self, key: str) -> bool:
        """Delete key from store."""
        if key in self._store:
            del self._store[key]
            return True
        return False

    def Clear(self) -> None:
        """Clear all stored data."""
        self._store.clear()

    @property
    def Keys(self) -> List[str]:
        """Get all keys."""
        return list(self._store.keys())

    def get_stats(self) -> Dict[str, int]:
        """Get usage statistics."""
        return {
            "save_count": self._save_count,
            "read_count": self._read_count,
            "key_count": len(self._store),
        }


# =============================================================================
# MOCK SCHEDULE
# =============================================================================


@dataclass
class ScheduledEvent:
    """Represents a scheduled event."""

    name: str
    date_rule: Any
    time_rule: Any
    callback: Callable
    enabled: bool = True


class MockDateRules:
    """Mock date rules for scheduling."""

    def EveryDay(self, symbol: Optional[str] = None) -> str:
        """Every trading day."""
        return f"EveryDay({symbol})" if symbol else "EveryDay()"

    def Every(self, *days) -> str:
        """Every specified day."""
        return f"Every({days})"

    def MonthStart(self, days_offset: int = 0) -> str:
        """Start of month."""
        return f"MonthStart({days_offset})"

    def MonthEnd(self, days_offset: int = 0) -> str:
        """End of month."""
        return f"MonthEnd({days_offset})"

    def WeekStart(self, days_offset: int = 0) -> str:
        """Start of week."""
        return f"WeekStart({days_offset})"

    def WeekEnd(self, days_offset: int = 0) -> str:
        """End of week."""
        return f"WeekEnd({days_offset})"


class MockTimeRules:
    """Mock time rules for scheduling."""

    def At(self, hour: int, minute: int, second: int = 0) -> str:
        """At specific time."""
        return f"At({hour:02d}:{minute:02d}:{second:02d})"

    def AfterMarketOpen(self, symbol: Optional[str] = None, minutes: int = 0) -> str:
        """After market open."""
        return f"AfterMarketOpen({symbol}, {minutes})"

    def BeforeMarketClose(self, symbol: Optional[str] = None, minutes: int = 0) -> str:
        """Before market close."""
        return f"BeforeMarketClose({symbol}, {minutes})"

    def Every(self, interval: timedelta) -> str:
        """Every interval."""
        return f"Every({interval})"


class MockSchedule:
    """Mock Schedule for event scheduling."""

    def __init__(self):
        self.DateRules = MockDateRules()
        self.TimeRules = MockTimeRules()
        self._events: List[ScheduledEvent] = []

    def On(
        self,
        date_rule: Any,
        time_rule: Any,
        callback: Callable,
        name: Optional[str] = None,
    ) -> ScheduledEvent:
        """Schedule an event."""
        event = ScheduledEvent(
            name=name or f"Event_{len(self._events)}",
            date_rule=date_rule,
            time_rule=time_rule,
            callback=callback,
        )
        self._events.append(event)
        return event

    def get_events(self) -> List[ScheduledEvent]:
        """Get all scheduled events."""
        return self._events.copy()

    def trigger_event(self, name: str) -> bool:
        """Manually trigger a scheduled event by name."""
        for event in self._events:
            if event.name == name and event.enabled:
                event.callback()
                return True
        return False

    def clear(self) -> None:
        """Clear all scheduled events."""
        self._events.clear()


# =============================================================================
# MOCK DATA SLICE
# =============================================================================


@dataclass
class MockBar:
    """Mock OHLCV bar data."""

    Open: float = 0.0
    High: float = 0.0
    Low: float = 0.0
    Close: float = 0.0
    Volume: int = 0
    Time: datetime = field(default_factory=datetime.now)
    Symbol: str = ""

    @property
    def Price(self) -> float:
        """Alias for Close."""
        return self.Close

    @property
    def Value(self) -> float:
        """Alias for Close."""
        return self.Close


class MockSplitCollection:
    """Mock collection for split data."""

    def __init__(self):
        self._splits: Dict[str, Any] = {}

    def ContainsKey(self, symbol: str) -> bool:
        """Check if symbol has a split."""
        return symbol in self._splits

    def add_split(self, symbol: str, ratio: float) -> None:
        """Add a split event."""
        self._splits[symbol] = {"Symbol": symbol, "SplitFactor": ratio}

    def __getitem__(self, symbol: str) -> Any:
        """Get split data."""
        return self._splits.get(symbol)


class MockDividendCollection:
    """Mock collection for dividend data."""

    def __init__(self):
        self._dividends: Dict[str, Any] = {}

    def ContainsKey(self, symbol: str) -> bool:
        """Check if symbol has a dividend."""
        return symbol in self._dividends

    def add_dividend(self, symbol: str, amount: float) -> None:
        """Add a dividend event."""
        self._dividends[symbol] = {"Symbol": symbol, "Distribution": amount}

    def __getitem__(self, symbol: str) -> Any:
        """Get dividend data."""
        return self._dividends.get(symbol)


class MockSlice:
    """
    Mock data slice for OnData simulation.

    Usage:
        slice = MockSlice()
        slice.add_bar("SPY", open_=450, high=452, low=449, close=451, volume=1000000)
        slice.add_bar("QLD", open_=75, high=76, low=74, close=75.5)

        # In test
        if slice.ContainsKey("SPY"):
            bar = slice["SPY"]
            assert bar.Close == 451
    """

    def __init__(self, time: Optional[datetime] = None):
        self._time = time or datetime.now()
        self._bars: Dict[str, MockBar] = {}
        self._splits = MockSplitCollection()
        self._dividends = MockDividendCollection()

    @property
    def Time(self) -> datetime:
        """Slice timestamp."""
        return self._time

    @property
    def Bars(self) -> Dict[str, MockBar]:
        """All bars in the slice."""
        return self._bars

    @property
    def Splits(self) -> MockSplitCollection:
        """Split events."""
        return self._splits

    @property
    def Dividends(self) -> MockDividendCollection:
        """Dividend events."""
        return self._dividends

    def ContainsKey(self, symbol: str) -> bool:
        """Check if symbol has data."""
        return symbol in self._bars

    def __getitem__(self, symbol: str) -> MockBar:
        """Get bar by symbol."""
        return self._bars.get(symbol, MockBar())

    def __contains__(self, symbol: str) -> bool:
        """Check if symbol exists."""
        return symbol in self._bars

    def add_bar(
        self,
        symbol: str,
        open_: float,
        high: Optional[float] = None,
        low: Optional[float] = None,
        close: Optional[float] = None,
        volume: int = 0,
    ) -> "MockSlice":
        """Add a bar to the slice (chainable)."""
        close = close if close is not None else open_
        high = high if high is not None else max(open_, close)
        low = low if low is not None else min(open_, close)

        self._bars[symbol] = MockBar(
            Open=open_,
            High=high,
            Low=low,
            Close=close,
            Volume=volume,
            Time=self._time,
            Symbol=symbol,
        )
        return self

    def set_time(self, time: datetime) -> "MockSlice":
        """Set slice time (chainable)."""
        self._time = time
        return self

    def keys(self):
        """Return symbols."""
        return self._bars.keys()


# Alias for compatibility
MockData = MockSlice


# =============================================================================
# MOCK ALGORITHM
# =============================================================================


class MockAlgorithm:
    """
    Comprehensive mock of QCAlgorithm for integration testing.

    Provides:
    - Time simulation
    - Portfolio management
    - Securities tracking
    - Order tracking (MarketOrder, MarketOnOpenOrder, Liquidate)
    - Scheduling
    - ObjectStore persistence
    - Logging

    Usage:
        algo = MockAlgorithm()
        algo.set_time(2024, 1, 15, 10, 30)
        algo.Portfolio.TotalPortfolioValue = 100000
        algo.Portfolio.add_position("QLD", 100, 75.0, 80.0)

        # Add a security with price
        algo.add_security("SPY", 450.0)

        # Test order submission
        ticket = algo.MarketOrder("SPY", 10, tag="test")
        assert len(algo.get_orders()) == 1
    """

    def __init__(self):
        # Time
        self._time = datetime(2024, 1, 15, 10, 30, 0)
        self._start_date = datetime(2024, 1, 1)
        self._end_date = datetime(2024, 12, 31)

        # Portfolio
        self.Portfolio = MockPortfolio()

        # Securities
        self.Securities: Dict[str, MockSecurity] = {}

        # Order tracking
        self._orders: List[MockOrderTicket] = []
        self._order_id_counter = 1

        # Schedule
        self.Schedule = MockSchedule()

        # ObjectStore
        self.ObjectStore = MockObjectStore()

        # Logging
        self._logs: List[Tuple[str, str]] = []  # (level, message)

        # Indicators
        self._indicators: Dict[str, MockIndicator] = {}

        # Settings
        self._benchmark = None
        self._brokerage_model = None

        # State
        self._is_warming_up = False
        self._live_mode = False

    # -------------------------------------------------------------------------
    # Time Properties and Methods
    # -------------------------------------------------------------------------

    @property
    def Time(self) -> datetime:
        """Current algorithm time."""
        return self._time

    @Time.setter
    def Time(self, value: datetime) -> None:
        self._time = value

    @property
    def StartDate(self) -> datetime:
        """Backtest start date."""
        return self._start_date

    @property
    def EndDate(self) -> datetime:
        """Backtest end date."""
        return self._end_date

    @property
    def IsWarmingUp(self) -> bool:
        """Whether algorithm is in warm-up period."""
        return self._is_warming_up

    @property
    def LiveMode(self) -> bool:
        """Whether running in live mode."""
        return self._live_mode

    def set_time(
        self,
        year_or_datetime: Union[int, datetime],
        month: Optional[int] = None,
        day: Optional[int] = None,
        hour: int = 9,
        minute: int = 30,
        second: int = 0,
    ) -> "MockAlgorithm":
        """Set algorithm time (chainable).

        Can be called as:
        - set_time(datetime_obj)
        - set_time(year, month, day, hour=9, minute=30, second=0)
        """
        if isinstance(year_or_datetime, datetime):
            self._time = year_or_datetime
        else:
            if month is None or day is None:
                raise ValueError("month and day required when passing year as int")
            self._time = datetime(year_or_datetime, month, day, hour, minute, second)
        return self

    def advance_time(self, minutes: int = 1) -> "MockAlgorithm":
        """Advance time by minutes (chainable)."""
        self._time += timedelta(minutes=minutes)
        return self

    def SetStartDate(self, year: int, month: int, day: int) -> None:
        """Set backtest start date."""
        self._start_date = datetime(year, month, day)

    def SetEndDate(self, year: int, month: int, day: int) -> None:
        """Set backtest end date."""
        self._end_date = datetime(year, month, day)

    def SetWarmUp(self, period: Union[int, timedelta]) -> None:
        """Set warm-up period."""
        self._is_warming_up = True

    # -------------------------------------------------------------------------
    # Securities Methods
    # -------------------------------------------------------------------------

    def AddEquity(
        self,
        symbol: str,
        resolution: Resolution = Resolution.Minute,
        market: str = "USA",
        fill_data_forward: bool = True,
        leverage: float = 1.0,
    ) -> MockSecurity:
        """Add an equity security."""
        security = MockSecurity(symbol, SecurityType.Equity)
        self.Securities[symbol] = security
        # Also add to portfolio for consistency
        if symbol not in self.Portfolio._positions:
            self.Portfolio._positions[symbol] = security
        return security

    def AddOption(
        self,
        underlying: str,
        resolution: Resolution = Resolution.Minute,
    ) -> MockSecurity:
        """Add an option chain."""
        security = MockSecurity(underlying, SecurityType.Option)
        self.Securities[underlying] = security
        return security

    def add_security(
        self,
        symbol: str,
        price: float,
        security_type: SecurityType = SecurityType.Equity,
    ) -> MockSecurity:
        """Helper to add a security with a price."""
        security = MockSecurity(symbol, security_type)
        security.set_price(price)
        self.Securities[symbol] = security
        if symbol not in self.Portfolio._positions:
            self.Portfolio._positions[symbol] = security
        return security

    # -------------------------------------------------------------------------
    # Order Methods
    # -------------------------------------------------------------------------

    def MarketOrder(
        self,
        symbol: str,
        quantity: int,
        asynchronous: bool = False,
        tag: str = "",
    ) -> MockOrderTicket:
        """Submit a market order."""
        ticket = MockOrderTicket(
            OrderId=self._order_id_counter,
            Symbol=symbol,
            Quantity=quantity,
            OrderType=OrderType.Market,
            Status=OrderStatus.Filled,  # Assume immediate fill for testing
            Tag=tag,
        )
        self._order_id_counter += 1
        self._orders.append(ticket)
        return ticket

    def MarketOnOpenOrder(
        self,
        symbol: str,
        quantity: int,
        tag: str = "",
    ) -> MockOrderTicket:
        """Submit a market-on-open order."""
        ticket = MockOrderTicket(
            OrderId=self._order_id_counter,
            Symbol=symbol,
            Quantity=quantity,
            OrderType=OrderType.MarketOnOpen,
            Status=OrderStatus.Submitted,  # MOO orders wait for open
            Tag=tag,
        )
        self._order_id_counter += 1
        self._orders.append(ticket)
        return ticket

    def LimitOrder(
        self,
        symbol: str,
        quantity: int,
        limit_price: float,
        tag: str = "",
    ) -> MockOrderTicket:
        """Submit a limit order."""
        ticket = MockOrderTicket(
            OrderId=self._order_id_counter,
            Symbol=symbol,
            Quantity=quantity,
            OrderType=OrderType.Limit,
            Status=OrderStatus.Submitted,
            Tag=tag,
        )
        self._order_id_counter += 1
        self._orders.append(ticket)
        return ticket

    def StopMarketOrder(
        self,
        symbol: str,
        quantity: int,
        stop_price: float,
        tag: str = "",
    ) -> MockOrderTicket:
        """Submit a stop market order."""
        ticket = MockOrderTicket(
            OrderId=self._order_id_counter,
            Symbol=symbol,
            Quantity=quantity,
            OrderType=OrderType.StopMarket,
            Status=OrderStatus.Submitted,
            Tag=tag,
        )
        self._order_id_counter += 1
        self._orders.append(ticket)
        return ticket

    def Liquidate(
        self,
        symbol: Optional[str] = None,
        asynchronous: bool = False,
        tag: str = "",
    ) -> List[MockOrderTicket]:
        """Liquidate positions."""
        tickets = []

        if symbol:
            # Liquidate specific symbol
            if symbol in self.Portfolio._positions:
                pos = self.Portfolio._positions[symbol]
                if pos.Holdings.Invested:
                    ticket = self.MarketOrder(
                        symbol, -pos.Holdings.Quantity, tag=tag or "LIQUIDATE"
                    )
                    tickets.append(ticket)
                    pos.Holdings.set_position(0, 0.0)
        else:
            # Liquidate all positions
            for sym, pos in self.Portfolio._positions.items():
                if pos.Holdings.Invested:
                    ticket = self.MarketOrder(
                        sym, -pos.Holdings.Quantity, tag=tag or "LIQUIDATE_ALL"
                    )
                    tickets.append(ticket)
                    pos.Holdings.set_position(0, 0.0)

        return tickets

    def SetHoldings(
        self,
        symbol: str,
        percentage: float,
        liquidate_existing: bool = False,
        tag: str = "",
    ) -> List[MockOrderTicket]:
        """Set holdings to target percentage."""
        tickets = []
        target_value = self.Portfolio.TotalPortfolioValue * percentage

        if symbol in self.Securities:
            price = self.Securities[symbol].Price
            if price > 0:
                target_quantity = int(target_value / price)
                current_quantity = self.Portfolio[symbol].Holdings.Quantity
                delta = target_quantity - current_quantity

                if delta != 0:
                    ticket = self.MarketOrder(symbol, delta, tag=tag or "SET_HOLDINGS")
                    tickets.append(ticket)

        return tickets

    def get_orders(
        self,
        symbol: Optional[str] = None,
        order_type: Optional[OrderType] = None,
    ) -> List[MockOrderTicket]:
        """Get orders, optionally filtered."""
        orders = self._orders.copy()

        if symbol:
            orders = [o for o in orders if o.Symbol == symbol]

        if order_type:
            orders = [o for o in orders if o.OrderType == order_type]

        return orders

    def clear_orders(self) -> None:
        """Clear order history."""
        self._orders.clear()

    # -------------------------------------------------------------------------
    # Indicator Methods
    # -------------------------------------------------------------------------

    def SMA(self, symbol: str, period: int, resolution: Resolution = Resolution.Daily) -> MockSMA:
        """Create an SMA indicator."""
        key = f"SMA_{symbol}_{period}"
        if key not in self._indicators:
            self._indicators[key] = MockSMA(period)
        return self._indicators[key]

    def RSI(self, symbol: str, period: int, resolution: Resolution = Resolution.Daily) -> MockRSI:
        """Create an RSI indicator."""
        key = f"RSI_{symbol}_{period}"
        if key not in self._indicators:
            self._indicators[key] = MockRSI(period)
        return self._indicators[key]

    def ADX(self, symbol: str, period: int, resolution: Resolution = Resolution.Daily) -> MockADX:
        """Create an ADX indicator."""
        key = f"ADX_{symbol}_{period}"
        if key not in self._indicators:
            self._indicators[key] = MockADX(period)
        return self._indicators[key]

    def ATR(self, symbol: str, period: int, resolution: Resolution = Resolution.Daily) -> MockATR:
        """Create an ATR indicator."""
        key = f"ATR_{symbol}_{period}"
        if key not in self._indicators:
            self._indicators[key] = MockATR(period)
        return self._indicators[key]

    def BB(
        self,
        symbol: str,
        period: int,
        k: float = 2.0,
        resolution: Resolution = Resolution.Daily,
    ) -> MockBollingerBands:
        """Create a Bollinger Bands indicator."""
        key = f"BB_{symbol}_{period}_{k}"
        if key not in self._indicators:
            self._indicators[key] = MockBollingerBands(period, k)
        return self._indicators[key]

    def EMA(self, symbol: str, period: int, resolution: Resolution = Resolution.Daily) -> MockEMA:
        """Create an EMA indicator."""
        key = f"EMA_{symbol}_{period}"
        if key not in self._indicators:
            self._indicators[key] = MockEMA(period)
        return self._indicators[key]

    def MACD(
        self,
        symbol: str,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
        resolution: Resolution = Resolution.Daily,
    ) -> MockMACD:
        """Create a MACD indicator."""
        key = f"MACD_{symbol}_{fast}_{slow}_{signal}"
        if key not in self._indicators:
            self._indicators[key] = MockMACD(fast, slow, signal)
        return self._indicators[key]

    def STO(
        self,
        symbol: str,
        k_period: int = 14,
        d_period: int = 3,
        resolution: Resolution = Resolution.Daily,
    ) -> MockStochastics:
        """Create a Stochastic indicator."""
        key = f"STO_{symbol}_{k_period}_{d_period}"
        if key not in self._indicators:
            self._indicators[key] = MockStochastics(k_period, d_period)
        return self._indicators[key]

    def get_indicator(self, key: str) -> Optional[MockIndicator]:
        """Get indicator by key."""
        return self._indicators.get(key)

    def register_indicator(self, key: str, indicator: MockIndicator) -> None:
        """Register a custom indicator."""
        self._indicators[key] = indicator

    # -------------------------------------------------------------------------
    # Logging Methods
    # -------------------------------------------------------------------------

    def Log(self, message: str) -> None:
        """Log an info message."""
        self._logs.append(("INFO", message))

    def Debug(self, message: str) -> None:
        """Log a debug message."""
        self._logs.append(("DEBUG", message))

    def Error(self, message: str) -> None:
        """Log an error message."""
        self._logs.append(("ERROR", message))

    def get_logs(self, level: Optional[str] = None) -> List[str]:
        """Get log messages, optionally filtered by level."""
        if level:
            return [msg for lvl, msg in self._logs if lvl == level]
        return [msg for _, msg in self._logs]

    def clear_logs(self) -> None:
        """Clear log history."""
        self._logs.clear()

    def find_log(self, pattern: str) -> Optional[str]:
        """Find first log message containing pattern."""
        for _, msg in self._logs:
            if pattern in msg:
                return msg
        return None

    # -------------------------------------------------------------------------
    # Benchmark and Settings
    # -------------------------------------------------------------------------

    def SetBenchmark(self, symbol: str) -> None:
        """Set benchmark symbol."""
        self._benchmark = symbol

    def SetBrokerageModel(self, model: Any) -> None:
        """Set brokerage model."""
        self._brokerage_model = model

    def SetCash(self, amount: float) -> None:
        """Set initial cash."""
        self.Portfolio.Cash = amount

    # -------------------------------------------------------------------------
    # Date Rules and Time Rules (convenience access)
    # -------------------------------------------------------------------------

    @property
    def DateRules(self) -> MockDateRules:
        """Access date rules."""
        return self.Schedule.DateRules

    @property
    def TimeRules(self) -> MockTimeRules:
        """Access time rules."""
        return self.Schedule.TimeRules


# =============================================================================
# FACTORY FUNCTIONS
# =============================================================================


def create_test_algorithm(
    portfolio_value: float = 100000.0,
    cash: float = 50000.0,
    time: Optional[datetime] = None,
) -> MockAlgorithm:
    """
    Factory function to create a pre-configured MockAlgorithm.

    Args:
        portfolio_value: Total portfolio value
        cash: Available cash
        time: Algorithm time (defaults to market hours)

    Returns:
        Configured MockAlgorithm instance
    """
    algo = MockAlgorithm()
    algo.Portfolio.TotalPortfolioValue = portfolio_value
    algo.Portfolio.Cash = cash

    if time:
        algo.Time = time
    else:
        algo.set_time(2024, 1, 15, 10, 30)

    return algo


def create_test_slice(
    time: Optional[datetime] = None,
    prices: Optional[Dict[str, float]] = None,
) -> MockSlice:
    """
    Factory function to create a pre-configured MockSlice.

    Args:
        time: Slice timestamp
        prices: Dictionary of symbol -> close price

    Returns:
        Configured MockSlice instance
    """
    slice_data = MockSlice(time or datetime(2024, 1, 15, 10, 30))

    if prices:
        for symbol, price in prices.items():
            slice_data.add_bar(symbol, open_=price * 0.999, close=price)

    return slice_data


def create_indicator_set(
    sma_value: float = 450.0,
    rsi_value: float = 50.0,
    adx_value: float = 25.0,
    atr_value: float = 5.0,
    ready: bool = True,
) -> Dict[str, MockIndicator]:
    """
    Create a standard set of configured indicators.

    Args:
        sma_value: SMA(200) value
        rsi_value: RSI(14) value
        adx_value: ADX(14) value
        atr_value: ATR(14) value
        ready: Whether indicators are ready

    Returns:
        Dictionary of indicator name -> indicator instance
    """
    return {
        "sma": MockSMA(200).set_value(sma_value).set_ready(ready),
        "rsi": MockRSI(14).set_value(rsi_value).set_ready(ready),
        "adx": MockADX(14).set_value(adx_value).set_ready(ready),
        "atr": MockATR(14).set_value(atr_value).set_ready(ready),
    }


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    # Enums
    "Resolution",
    "OrderType",
    "OrderStatus",
    "SecurityType",
    "OptionRight",
    # Indicators
    "MockIndicator",
    "MockSMA",
    "MockRSI",
    "MockADX",
    "MockATR",
    "MockBollingerBands",
    "MockEMA",
    "MockMACD",
    "MockStochastics",
    "IndicatorValue",
    # Securities
    "MockSecurity",
    "MockHoldings",
    "MockOptionContract",
    "MockGreeks",
    # Portfolio
    "MockPortfolio",
    # Orders
    "MockOrderTicket",
    # Data
    "MockSlice",
    "MockData",
    "MockBar",
    "MockSplitCollection",
    "MockDividendCollection",
    # Algorithm
    "MockAlgorithm",
    "MockObjectStore",
    "MockSchedule",
    "MockDateRules",
    "MockTimeRules",
    "ScheduledEvent",
    # Factory functions
    "create_test_algorithm",
    "create_test_slice",
    "create_indicator_set",
]
