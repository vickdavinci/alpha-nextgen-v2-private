# QC_RULES.md - QuantConnect Coding Patterns & Requirements

## Overview

This document defines QuantConnect/LEAN-specific coding patterns that MUST be followed. The LEAN engine has specific requirements that differ from standard Python.

---

## 1. Algorithm Structure

### 1.1 MANDATORY Import Statement

**MUST use `from AlgorithmImports import *`.** Do not import standard libraries directly.

```python
# ✅ CORRECT - Always start with this
from AlgorithmImports import *

class AlphaNextGen(QCAlgorithm):
    ...

# ❌ WRONG - Never import these directly
import datetime          # Already in AlgorithmImports
import pandas as pd      # Already in AlgorithmImports  
import numpy as np       # Already in AlgorithmImports
from datetime import timedelta  # Already in AlgorithmImports
```

**Why:** QuantConnect's `AlgorithmImports` includes all necessary libraries with the correct versions. Direct imports may conflict or use incompatible versions.

### 1.2 Main Class

All algorithms must inherit from `QCAlgorithm`:

```python
from AlgorithmImports import *

class AlphaNextGen(QCAlgorithm):
    """Multi-strategy leveraged ETF trading system."""
    
    def Initialize(self):
        """Called once at algorithm start. Set up everything here."""
        pass
    
    def OnData(self, data: Slice):
        """Called every time step with new data."""
        pass
    
    def OnOrderEvent(self, orderEvent: OrderEvent):
        """Called when order status changes."""
        pass
    
    def OnEndOfDay(self, symbol: Symbol):
        """Called at end of day for each symbol."""
        pass
```

### 1.3 Required Initialize() Setup

```python
def Initialize(self):
    # 1. Set dates and cash (required for backtests)
    self.SetStartDate(2020, 1, 1)
    self.SetEndDate(2024, 1, 1)
    self.SetCash(50000)
    
    # 2. Set timezone (affects self.Time)
    self.SetTimeZone("America/New_York")
    
    # 3. Set brokerage model (affects order handling)
    self.SetBrokerageModel(BrokerageName.InteractiveBrokersBrokerage)
    
    # 4. Add securities
    self.spy = self.AddEquity("SPY", Resolution.Minute).Symbol
    
    # 5. Set warmup period
    self.SetWarmUp(timedelta(days=252))
    
    # 6. Schedule events
    self.Schedule.On(...)
    
    # 7. Initialize indicators
    self.sma = self.SMA("SPY", 20, Resolution.Daily)

    # 8. Load persisted state (MANDATORY for live trading)
    self.load_state()  # Lockbox, days_running, capital_state

    # 9. Reconcile with broker positions (MANDATORY for live trading)
    self.reconcile_positions()
```

### 1.4 MANDATORY Split Check in OnData

**Every `OnData` method MUST check for splits before processing.** Splits corrupt indicator values and can cause erroneous trades.

```python
def OnData(self, data: Slice):
    """Called every minute with new data."""
    
    # ═══════════════════════════════════════════════════════════════
    # MANDATORY SPLIT CHECK - MUST BE FIRST
    # ═══════════════════════════════════════════════════════════════
    
    # 1. Check PROXY symbols (SPY, RSP, HYG, IEF) - freezes EVERYTHING
    #    If SPY splits, the entire Regime Score is invalid
    for proxy in self.proxy_symbols:
        if data.Splits.ContainsKey(proxy):
            self.Log(f"SPLIT_GUARD: Proxy {proxy} split - FREEZING ALL PROCESSING")
            return  # Skip ALL processing for the day
    
    # 2. Check TRADED symbols - freeze only that specific symbol
    #    If TQQQ splits, we only stop trading TQQQ
    symbols_to_skip = set()
    for symbol in self.traded_symbols:
        if data.Splits.ContainsKey(symbol):
            self.Log(f"SPLIT_GUARD: {symbol} split - freezing this symbol only")
            symbols_to_skip.add(symbol)
    
    # ═══════════════════════════════════════════════════════════════
    # END SPLIT CHECK - Continue with normal processing
    # ═══════════════════════════════════════════════════════════════
    
    # Skip warmup
    if self.IsWarmingUp:
        return
    
    # Risk checks
    if self.risk_engine.check_kill_switch():
        return
    
    # Strategy processing (pass symbols_to_skip to avoid trading split symbols)
    self.process_strategies(data, symbols_to_skip)
```

**Symbol Classification:**

| Type | Symbols | Split Action |
|------|---------|--------------|
| **Proxy** | SPY, RSP, HYG, IEF | Freeze EVERYTHING (Regime invalid) |
| **Traded** | TQQQ, SOXL, QLD, SSO, TMF, PSQ, SHV | Freeze only that symbol |

---

## 2. Adding Securities

### 2.1 Equity Subscription

```python
# Basic subscription
self.AddEquity("SPY", Resolution.Minute)

# Store symbol reference
self.spy = self.AddEquity("SPY", Resolution.Minute).Symbol

# With custom settings
self.AddEquity("SPY", Resolution.Minute, 
               Market.USA, 
               fillDataForward=True,
               leverage=1.0)
```

### 2.2 Resolution Options

```python
Resolution.Tick      # Every trade
Resolution.Second    # Every second
Resolution.Minute    # Every minute (recommended for intraday)
Resolution.Hour      # Every hour
Resolution.Daily     # Daily bars
```

### 2.3 All Alpha NextGen Securities

```python
def add_securities(self):
    """Add all required securities."""
    # Traded symbols (minute resolution for intraday)
    self.tqqq = self.AddEquity("TQQQ", Resolution.Minute).Symbol
    self.soxl = self.AddEquity("SOXL", Resolution.Minute).Symbol
    self.qld = self.AddEquity("QLD", Resolution.Minute).Symbol
    self.sso = self.AddEquity("SSO", Resolution.Minute).Symbol
    self.tmf = self.AddEquity("TMF", Resolution.Minute).Symbol
    self.psq = self.AddEquity("PSQ", Resolution.Minute).Symbol
    self.shv = self.AddEquity("SHV", Resolution.Minute).Symbol
    
    # Proxy symbols (data only - used for Regime calculation)
    self.spy = self.AddEquity("SPY", Resolution.Minute).Symbol
    self.rsp = self.AddEquity("RSP", Resolution.Minute).Symbol
    self.hyg = self.AddEquity("HYG", Resolution.Minute).Symbol
    self.ief = self.AddEquity("IEF", Resolution.Minute).Symbol
    
    # Store for split checking
    self.traded_symbols = [self.tqqq, self.soxl, self.qld, self.sso, 
                           self.tmf, self.psq, self.shv]
    self.proxy_symbols = [self.spy, self.rsp, self.hyg, self.ief]
```

---

## 3. Indicators

### 3.1 Built-in Indicator Creation

```python
# Simple Moving Average
self.sma = self.SMA("SPY", 20, Resolution.Daily)

# Bollinger Bands
self.bb = self.BB("SPY", 20, 2, MovingAverageType.Simple, Resolution.Daily)

# RSI
self.rsi = self.RSI("SPY", 14, MovingAverageType.Simple, Resolution.Daily)

# ATR
self.atr = self.ATR("SPY", 14, MovingAverageType.Simple, Resolution.Daily)

# Standard Deviation
self.std = self.STD("SPY", 20, Resolution.Daily)
```

### 3.2 Accessing Indicator Values

```python
# Always check if ready first
if self.sma.IsReady:
    value = self.sma.Current.Value

# Bollinger Bands components
if self.bb.IsReady:
    upper = self.bb.UpperBand.Current.Value
    middle = self.bb.MiddleBand.Current.Value
    lower = self.bb.LowerBand.Current.Value
    bandwidth = self.bb.BandWidth.Current.Value
```

### 3.3 Manual Indicator Registration

```python
# Create indicator manually
self.custom_sma = SimpleMovingAverage(20)

# Register with symbol
self.RegisterIndicator("SPY", self.custom_sma, Resolution.Daily)

# Or register with consolidator for different timeframe
consolidator = TradeBarConsolidator(timedelta(days=1))
self.SubscriptionManager.AddConsolidator("SPY", consolidator)
self.RegisterIndicator("SPY", self.custom_sma, consolidator)
```

### 3.4 Indicator Warmup

```python
# Set warmup to max indicator period + buffer
self.SetWarmUp(timedelta(days=252))  # For 200-day SMA

# Check warmup status
def OnData(self, data):
    if self.IsWarmingUp:
        return
    
    # Safe to use indicators here
```

---

## 4. Scheduling

### 4.1 Date Rules

```python
# Every trading day
self.DateRules.EveryDay()
self.DateRules.EveryDay("SPY")  # Only when SPY trades

# Specific days
self.DateRules.Every(DayOfWeek.Monday)
self.DateRules.MonthStart()
self.DateRules.MonthEnd()
```

### 4.2 Time Rules

```python
# Specific time
self.TimeRules.At(9, 30)  # 9:30 AM
self.TimeRules.At(15, 45)  # 3:45 PM

# Relative to market
self.TimeRules.AfterMarketOpen("SPY", 0)   # At open
self.TimeRules.AfterMarketOpen("SPY", 30)  # 30 min after open
self.TimeRules.BeforeMarketClose("SPY", 15)  # 15 min before close
```

### 4.3 Complete Schedule Setup

```python
def setup_schedules(self):
    """Configure all scheduled events."""
    
    # Pre-market setup (9:25 AM)
    self.Schedule.On(
        self.DateRules.EveryDay("SPY"),
        self.TimeRules.At(9, 25),
        self.pre_market_setup
    )
    
    # Start of day baseline (9:33 AM)
    self.Schedule.On(
        self.DateRules.EveryDay("SPY"),
        self.TimeRules.AfterMarketOpen("SPY", 3),
        self.set_sod_baseline
    )
    
    # Warm entry check (10:00 AM)
    self.Schedule.On(
        self.DateRules.EveryDay("SPY"),
        self.TimeRules.At(10, 0),
        self.check_warm_entry
    )
    
    # End of day processing (15:45) - MOO orders submitted here
    self.Schedule.On(
        self.DateRules.EveryDay("SPY"),
        self.TimeRules.BeforeMarketClose("SPY", 15),
        self.eod_processing
    )
    
    # Weekly reset (Monday 9:30)
    self.Schedule.On(
        self.DateRules.Every(DayOfWeek.Monday),
        self.TimeRules.AfterMarketOpen("SPY", 0),
        self.weekly_reset
    )
```

---

## 5. Order Management

### 5.1 Order Types

```python
# Market order - immediate execution
ticket = self.MarketOrder("SPY", 100)      # Buy 100 shares
ticket = self.MarketOrder("SPY", -100)     # Sell 100 shares

# Market-on-Open - executes at NEXT day's open
# MUST be placed in OnEndOfDay or scheduled BeforeMarketClose
ticket = self.MarketOnOpenOrder("SPY", 100)

# Limit order
ticket = self.LimitOrder("SPY", 100, 450.00)  # Buy at $450 or better

# Stop order
ticket = self.StopMarketOrder("SPY", -100, 440.00)  # Sell if drops to $440
```

### 5.2 MOO Order Timing Rules

**CRITICAL:** MOO orders submitted during market hours are for **TOMORROW's open**, not today's.

```python
# ✅ CORRECT - MOO in OnEndOfDay (executes tomorrow)
def OnEndOfDay(self, symbol):
    self.MarketOnOpenOrder("QLD", 100)  # Fills tomorrow 09:30

# ✅ CORRECT - MOO scheduled before close (executes tomorrow)
# Scheduled at 15:45
def eod_processing(self):
    self.MarketOnOpenOrder("QLD", 100)  # Fills tomorrow 09:30

# ❌ WRONG - MOO in OnData expecting same-day execution
def OnData(self, data):
    self.MarketOnOpenOrder("QLD", 100)  # Will NOT fill today!
```

### 5.3 Order Ticket Handling

```python
# Submit order
ticket = self.MarketOrder("SPY", 100)

# Check order status
if ticket.Status == OrderStatus.Filled:
    fill_price = ticket.AverageFillPrice
    fill_qty = ticket.QuantityFilled

# Cancel order
ticket.Cancel()

# Update order
ticket.Update(UpdateOrderFields(Quantity=50))
```

### 5.4 Position Liquidation

```python
# Liquidate single position
self.Liquidate("SPY")

# Liquidate with tag
self.Liquidate("SPY", tag="KILL_SWITCH")

# Liquidate all positions
self.Liquidate()

# Set holdings to target percentage
self.SetHoldings("SPY", 0.3)  # 30% of portfolio
```

### 5.5 Order Events

```python
def OnOrderEvent(self, orderEvent: OrderEvent):
    """Handle order status changes."""
    
    if orderEvent.Status == OrderStatus.Filled:
        symbol = orderEvent.Symbol
        fill_price = orderEvent.FillPrice
        fill_qty = orderEvent.FillQuantity
        direction = "BUY" if fill_qty > 0 else "SELL"
        
        self.Log(f"FILL: {direction} {abs(fill_qty)} {symbol} @ ${fill_price:.2f}")
        
        # Update position tracking
        self.on_fill(symbol, fill_price, fill_qty)
    
    elif orderEvent.Status == OrderStatus.Canceled:
        self.Log(f"ORDER_CANCELED: {orderEvent.Symbol}")
    
    elif orderEvent.Status == OrderStatus.Invalid:
        self.Log(f"ORDER_INVALID: {orderEvent.Symbol} - {orderEvent.Message}")
```

### 5.6 MOO Fallback Pattern (MANDATORY)

MOO orders may fail to fill due to rejection, symbol halt, or timing issues. A fallback check at 09:31 is **REQUIRED** for all swing/trend strategies using MOO orders.

```python
def Initialize(self):
    # Track pending MOO orders for fallback check
    self.pending_moo_orders = []

    # Schedule MOO fallback check at 09:31
    self.Schedule.On(
        self.DateRules.EveryDay("SPY"),
        self.TimeRules.AfterMarketOpen("SPY", 1),  # 09:31 ET
        self.check_moo_fills
    )

def eod_processing(self):
    """Submit MOO orders at 15:45 for next day's open."""
    ticket = self.MarketOnOpenOrder("QLD", 100)
    self.pending_moo_orders.append(ticket)  # Track for fallback

def check_moo_fills(self):
    """MANDATORY: Check MOO fills at 09:31, fallback to market order if unfilled."""
    for ticket in self.pending_moo_orders:
        if ticket.Status != OrderStatus.Filled:
            self.Log(f"MOO_FALLBACK: {ticket.Symbol} not filled, using market order")
            ticket.Cancel()
            self.MarketOrder(ticket.Symbol, ticket.Quantity)
    self.pending_moo_orders.clear()
```

**Why this matters:** MOO orders can silently fail. Without this fallback, intended positions may never be established, causing the strategy to miss entries entirely.

---

## 6. Portfolio Access

### 6.1 Portfolio Properties

```python
# Total portfolio value
total = self.Portfolio.TotalPortfolioValue

# Cash available
cash = self.Portfolio.Cash

# Margin remaining
margin = self.Portfolio.MarginRemaining

# Total holdings value
holdings = self.Portfolio.TotalHoldingsValue

# Unrealized P&L
unrealized = self.Portfolio.TotalUnrealizedProfit
```

### 6.2 Position Properties

```python
# Get holding
holding = self.Portfolio["SPY"]

# Check if invested
if holding.Invested:
    quantity = holding.Quantity           # Signed (+/-)
    avg_price = holding.AveragePrice      # Entry price
    value = holding.HoldingsValue         # Current value (signed)
    abs_value = holding.AbsoluteHoldingsValue
    unrealized = holding.UnrealizedProfit
    unrealized_pct = holding.UnrealizedProfitPercent
    
# Check direction
if holding.IsLong:
    ...
if holding.IsShort:
    ...
```

### 6.3 Iterating Positions

```python
def get_all_positions(self) -> Dict[str, float]:
    """Get all current positions."""
    positions = {}
    for kvp in self.Portfolio:
        symbol = kvp.Key
        holding = kvp.Value
        if holding.Invested:
            positions[str(symbol)] = holding.HoldingsValue
    return positions
```

---

## 7. Data Access

### 7.1 Slice Object

```python
def OnData(self, data: Slice):
    # Check if symbol has data
    if "SPY" not in data:
        return
    
    # Access bar data
    bar = data["SPY"]
    if bar is not None:
        open_price = bar.Open
        high = bar.High
        low = bar.Low
        close = bar.Close
        volume = bar.Volume
    
    # Check for splits (MANDATORY - see Section 1.4)
    if data.Splits.ContainsKey(self.spy):
        self.Log("Split detected!")
        return
```

### 7.2 Securities Dictionary

```python
# Get current price
price = self.Securities["SPY"].Price

# Get last close
close = self.Securities["SPY"].Close

# Get bid/ask
bid = self.Securities["SPY"].BidPrice
ask = self.Securities["SPY"].AskPrice

# Check if market open
is_open = self.Securities["SPY"].Exchange.ExchangeOpen
```

### 7.3 History Requests

```python
# Get historical bars
history = self.History(["SPY"], 20, Resolution.Daily)

# Access data
for bar in history.itertuples():
    date = bar.Index[1]  # (symbol, time) multi-index
    close = bar.close

# Get as DataFrame
df = self.History(["SPY"], 252, Resolution.Daily)
spy_closes = df.loc["SPY"]["close"]
```

---

## 8. ObjectStore (Persistence)

### 8.1 Basic Operations

```python
# Save data
self.ObjectStore.Save("key", "value")

# Check if exists
if self.ObjectStore.ContainsKey("key"):
    ...

# Read data
value = self.ObjectStore.Read("key")

# Delete key
self.ObjectStore.Delete("key")
```

### 8.2 JSON Serialization Pattern

```python
# Note: json is available from AlgorithmImports
def save_state(self, key: str, data: dict):
    """Save dictionary to ObjectStore."""
    json_str = json.dumps(data)
    self.ObjectStore.Save(key, json_str)

def load_state(self, key: str, default: dict = None) -> dict:
    """Load dictionary from ObjectStore."""
    if not self.ObjectStore.ContainsKey(key):
        return default or {}
    
    try:
        json_str = self.ObjectStore.Read(key)
        return json.loads(json_str)
    except Exception as e:
        self.Log(f"STATE_ERROR: Failed to load {key}: {e}")
        return default or {}
```

---

## 9. Logging

### 9.1 Log Methods

```python
# Standard log (appears in backtest logs)
self.Log("Message")

# Debug log (more verbose)
self.Debug("Debug message")

# Error log
self.Error("Error message")
```

### 9.2 CRITICAL: self.Log() Only Accepts a String

**QC's `self.Log()` does NOT accept keyword arguments.** Passing any kwargs (like `trades_only=False`) causes a **TypeError that crashes the entire calling function**.

```python
# ❌ WRONG — causes TypeError at runtime, crashes the function
self.Log(f"INJECT: {symbol} | price={price}", trades_only=False)

# ✅ CORRECT — plain string only
self.Log(f"INJECT: {symbol} | price={price}")
```

**Why this is critical:** The TypeError does not show as a compile error — it only crashes at runtime. If the Log call is inside a critical path (e.g., price injection), everything after it silently fails.

**If you need conditional logging**, use a wrapper:
```python
def log(self, message: str, trades_only: bool = False):
    """Custom wrapper — trades_only controls live vs backtest logging."""
    if trades_only or not self.LiveMode:
        self.Log(message)
```

### 9.3 Logging Best Practices

```python
# Use structured format for parseability
self.Log(f"TRADE|{action}|{symbol}|{quantity}|{price}")

# Include context
self.Log(f"REGIME: Score={score:.1f} | State={state} | TMF={tmf_pct:.1%}")

# Log important events with prefixes
self.Log(f"KILL_SWITCH: Triggered at {self.Time}")
self.Log(f"EOD: Processing complete, MOO orders submitted")
```

---

## 10. Common Patterns

### 10.1 Safe Data Access

```python
def get_price(self, symbol: str) -> Optional[float]:
    """Safely get current price."""
    if symbol not in self.Securities:
        return None
    
    security = self.Securities[symbol]
    if security.Price <= 0:
        return None
    
    return float(security.Price)
```

### 10.2 Safe Order Submission

```python
def safe_order(self, symbol: str, quantity: int, tag: str = "") -> Optional[OrderTicket]:
    """Submit order with validation."""
    # Validate quantity
    if quantity == 0:
        return None
    
    # Check market hours for market orders
    if not self.IsMarketOpen(symbol):
        self.Log(f"ORDER_SKIP: Market closed for {symbol}")
        return None
    
    # Check for existing orders
    if self.Transactions.GetOpenOrders(symbol):
        self.Log(f"ORDER_SKIP: Pending orders exist for {symbol}")
        return None
    
    # Submit order
    return self.MarketOrder(symbol, quantity, tag=tag)
```

### 10.3 Consolidator Pattern

```python
def setup_daily_consolidator(self, symbol: Symbol):
    """Set up daily bar consolidation from minute data."""
    consolidator = TradeBarConsolidator(timedelta(days=1))
    consolidator.DataConsolidated += self.on_daily_bar
    self.SubscriptionManager.AddConsolidator(symbol, consolidator)
    
    # Register indicators with consolidator
    self.daily_sma = SimpleMovingAverage(20)
    self.RegisterIndicator(symbol, self.daily_sma, consolidator)

def on_daily_bar(self, sender, bar: TradeBar):
    """Called when daily bar completes."""
    symbol = bar.Symbol
    self.Log(f"DAILY_BAR: {symbol} Close={bar.Close}")
```

### 10.4 Time-Based Checks

```python
def is_trading_window(self) -> bool:
    """Check if within trading window (10:00-15:00)."""
    current = self.Time.time()
    return time(10, 0) <= current <= time(15, 0)

def is_time_guard_active(self) -> bool:
    """Check if in Fed announcement window."""
    current = self.Time.time()
    return time(13, 55) <= current <= time(14, 10)

def minutes_to_close(self) -> int:
    """Get minutes until market close."""
    close_time = time(16, 0)
    current = self.Time.time()
    
    close_minutes = close_time.hour * 60 + close_time.minute
    current_minutes = current.hour * 60 + current.minute
    
    return close_minutes - current_minutes
```

---

## 11. Live Trading Considerations

### 11.1 Brokerage Model

```python
def Initialize(self):
    # Set Interactive Brokers model for live trading
    self.SetBrokerageModel(BrokerageName.InteractiveBrokersBrokerage)
```

### 11.2 Data Differences

```python
# In live trading, data may be delayed or missing
def OnData(self, data):
    # Always validate data availability
    if data is None or len(data) == 0:
        return
    
    # Check for stale data
    for symbol in self.traded_symbols:
        if symbol in data:
            bar_time = data[symbol].EndTime
            if (self.Time - bar_time).total_seconds() > 60:
                self.Log(f"STALE_DATA: {symbol} bar is {self.Time - bar_time} old")
```

### 11.3 Order Fill Expectations

```python
# Live fills may differ from backtest
# - Slippage is real
# - Partial fills possible
# - Rejections possible

def OnOrderEvent(self, orderEvent):
    if orderEvent.Status == OrderStatus.PartiallyFilled:
        self.Log(f"PARTIAL_FILL: {orderEvent.Symbol} "
                f"{orderEvent.FillQuantity}/{orderEvent.Quantity}")
    
    if orderEvent.Status == OrderStatus.Invalid:
        self.Log(f"ORDER_REJECTED: {orderEvent.Symbol} - {orderEvent.Message}")
        # May need to retry or alert
```

---

## 12. Testing in QC

### 12.1 Backtest Configuration

```python
def Initialize(self):
    # Set backtest period
    self.SetStartDate(2020, 1, 1)
    self.SetEndDate(2024, 1, 1)
    
    # Set starting cash
    self.SetCash(50000)
    
    # Enable extended market hours data if needed
    self.AddEquity("SPY", Resolution.Minute, 
                   extendedMarketHours=True)
```

### 12.2 Using Algorithm Lab

For testing individual components, use the Research environment:

```python
# In Research notebook
qb = QuantBook()

# Add securities
spy = qb.AddEquity("SPY")

# Get history
history = qb.History(spy.Symbol, 252, Resolution.Daily)

# Test indicator
sma = SimpleMovingAverage(20)
for row in history.itertuples():
    sma.Update(row.Index[1], row.close)
    if sma.IsReady:
        print(f"{row.Index[1]}: SMA={sma.Current.Value:.2f}")
```

---

## Quick Reference Card

### Must-Do in Initialize()

1. ✅ `from AlgorithmImports import *` (FIRST LINE)
2. ✅ `SetStartDate()` / `SetEndDate()` (backtest)
3. ✅ `SetCash()`
4. ✅ `SetTimeZone("America/New_York")`
5. ✅ `SetBrokerageModel()`
6. ✅ `AddEquity()` for all symbols
7. ✅ `SetWarmUp()`
8. ✅ `Schedule.On()` for timed events
9. ✅ `load_state()` from ObjectStore (lockbox, days_running, positions)
10. ✅ `reconcile_positions()` with broker state

### Must-Check in OnData()

1. ✅ `if data.Splits.ContainsKey(proxy): return` (FIRST - freezes all)
2. ✅ `if data.Splits.ContainsKey(traded): skip_symbol` (freeze that symbol)
3. ✅ `if self.IsWarmingUp: return`
4. ✅ `if symbol not in data: return`
5. ✅ `if not indicator.IsReady: return`

### Must-Handle for Orders

1. ✅ Validate quantity ≠ 0
2. ✅ Check market hours
3. ✅ MOO orders in `OnEndOfDay` or scheduled `BeforeMarketClose`
4. ✅ MOO fallback check at 09:31 (market order if unfilled)
5. ✅ Handle fill events
6. ✅ Handle rejections

### Never Do

1. ❌ `import datetime` — use `from AlgorithmImports import *`
2. ❌ `import pandas` — use `from AlgorithmImports import *`
3. ❌ MOO orders in `OnData` expecting same-day execution
4. ❌ Skip split checks
5. ❌ Use indicators without checking `IsReady`
