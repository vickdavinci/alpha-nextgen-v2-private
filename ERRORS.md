# ERRORS.md - Common Errors and Solutions

## Overview

This document catalogs common errors encountered when developing on QuantConnect/LEAN and their solutions. Reference this when debugging issues.

---

## 1. Symbol and Data Errors

### 1.1 Symbol Not Found

**Error:**
```
Runtime Error: 'SPY' wasn't found in the Slice object
```

**Cause:** Symbol not subscribed or data not available for the current time.

**Solution:**
```python
# WRONG
def OnData(self, data):
    price = data["SPY"].Close  # May fail if SPY not in slice

# CORRECT
def OnData(self, data):
    if "SPY" not in data:
        return
    if data["SPY"] is None:
        return
    price = data["SPY"].Close
```

**Also ensure symbol is added in Initialize:**
```python
def Initialize(self):
    self.spy = self.AddEquity("SPY", Resolution.Minute).Symbol
```

---

### 1.2 Indicator Not Ready

**Error:**
```
Runtime Error: Indicator is not ready
```
or unexpected NaN/zero values from indicators.

**Cause:** Indicator hasn't received enough data points to compute.

**Solution:**
```python
# WRONG
def OnData(self, data):
    if self.sma.Current.Value > 100:  # May fail if not ready
        ...

# CORRECT
def OnData(self, data):
    if not self.sma.IsReady:
        return
    if self.sma.Current.Value > 100:
        ...
```

**For multiple indicators:**
```python
def indicators_ready(self) -> bool:
    """Check if all required indicators are ready."""
    return all([
        self.sma_20.IsReady,
        self.sma_50.IsReady,
        self.sma_200.IsReady,
        self.bb.IsReady,
        self.atr.IsReady
    ])
```

---

### 1.3 No Data During Warmup

**Error:**
```
Runtime Error: Sequence contains no elements
```
or empty collections during warmup period.

**Cause:** Attempting to access data before warmup completes.

**Solution:**
```python
def Initialize(self):
    # Set adequate warmup period
    self.SetWarmUp(timedelta(days=252))  # For 200-day SMA + buffer

def OnData(self, data):
    # Skip during warmup
    if self.IsWarmingUp:
        return
```

---

### 1.4 Data Resolution Mismatch

**Error:**
```
Unexpected behavior when mixing resolutions
```

**Cause:** Using minute data with daily indicators or vice versa.

**Solution:**
```python
# For daily indicators on minute data, use consolidators
def Initialize(self):
    self.spy = self.AddEquity("SPY", Resolution.Minute).Symbol
    
    # Create daily consolidator
    daily_consolidator = TradeBarConsolidator(timedelta(days=1))
    daily_consolidator.DataConsolidated += self.OnDailyBar
    self.SubscriptionManager.AddConsolidator(self.spy, daily_consolidator)
    
    # Register indicator with consolidator
    self.sma_daily = SimpleMovingAverage(20)
    self.RegisterIndicator(self.spy, self.sma_daily, daily_consolidator)

def OnDailyBar(self, sender, bar):
    """Called when daily bar completes."""
    if self.sma_daily.IsReady:
        self.Log(f"Daily SMA: {self.sma_daily.Current.Value}")
```

---

## 2. Order and Execution Errors

### 2.1 Insufficient Buying Power

**Error:**
```
Order Error: Insufficient buying power to complete order
```

**Cause:** Attempting to buy more than account can afford.

**Solution:**
```python
# WRONG
self.MarketOrder("TQQQ", 1000)  # Fixed quantity

# CORRECT
def calculate_order_quantity(self, symbol: str, target_value: float) -> int:
    """Calculate shares that fit within buying power."""
    price = self.Securities[symbol].Price
    if price <= 0:
        return 0
    
    # Account for existing position
    current_holdings = self.Portfolio[symbol].HoldingsValue
    delta_value = target_value - current_holdings
    
    # Check against available buying power
    available = self.Portfolio.MarginRemaining
    if delta_value > available:
        delta_value = available * 0.95  # 5% buffer
    
    return int(delta_value / price)
```

---

### 2.2 Order Already Exists

**Error:**
```
Order Error: Order already exists for symbol
```

**Cause:** Submitting duplicate orders for the same symbol.

**Solution:**
```python
# Check for existing orders before submitting
def safe_market_order(self, symbol: str, quantity: int) -> Optional[OrderTicket]:
    """Submit order only if no pending orders exist."""
    open_orders = self.Transactions.GetOpenOrders(symbol)
    if open_orders:
        self.Log(f"ORDER_SKIP: {symbol} has {len(open_orders)} pending orders")
        return None
    
    return self.MarketOrder(symbol, quantity)
```

---

### 2.3 Invalid Order Quantity

**Error:**
```
Order Error: Order quantity must be a whole number
```
or
```
Order Error: Order quantity cannot be zero
```

**Cause:** Fractional shares or zero quantity.

**Solution:**
```python
# WRONG
quantity = target_value / price  # May be fractional

# CORRECT
quantity = int(target_value / price)
if quantity == 0:
    self.Log(f"ORDER_SKIP: {symbol} quantity rounds to 0")
    return None
```

---

### 2.4 Market Order Outside Hours

**Error:**
```
Order Error: Market orders cannot be submitted outside market hours
```

**Cause:** Attempting market order when market is closed.

**Solution:**
```python
def safe_market_order(self, symbol: str, quantity: int) -> Optional[OrderTicket]:
    """Submit market order only during market hours."""
    if not self.IsMarketOpen(symbol):
        self.Log(f"ORDER_SKIP: Market closed for {symbol}")
        return None
    
    return self.MarketOrder(symbol, quantity)
```

---

### 2.5 MOO Order Rejected / Invalid

**Error:**
```
Order Error: MarketOnOpen orders must be submitted before market open
```
or
```
OrderInvalid on MOO
```

**Cause:** MOO order submitted during market hours for same-day execution, or submitted after market has already opened.

**CRITICAL UNDERSTANDING:**
- MOO orders placed **during market hours** are for **tomorrow's open**, not today's
- MOO orders placed **after market close** are for **tomorrow's open**
- You CANNOT use MOO to execute at today's open if the market is already open

**Solution:**
```python
# CORRECT - Submit MOO in OnEndOfDay or scheduled BeforeMarketClose
def OnEndOfDay(self, symbol):
    """EOD processing - MOO orders here execute at TOMORROW's open."""
    if self.should_enter_tomorrow:
        self.MarketOnOpenOrder("QLD", 100)  # Executes tomorrow 09:30

# CORRECT - Schedule before market close
def Initialize(self):
    self.Schedule.On(
        self.DateRules.EveryDay("SPY"),
        self.TimeRules.BeforeMarketClose("SPY", 15),  # 15:45 ET
        self.SubmitMOOOrders
    )

def SubmitMOOOrders(self):
    """Submit MOO orders at 15:45 for next day's open."""
    self.MarketOnOpenOrder("QLD", 100)  # Executes tomorrow 09:30

# WRONG - Submitting MOO during market hours expecting same-day execution
def OnData(self, data):
    if some_condition:
        self.MarketOnOpenOrder("QLD", 100)  # ❌ Will NOT execute today!
```

**If you need immediate execution during market hours, use MarketOrder instead:**
```python
def OnData(self, data):
    if need_immediate_entry:
        self.MarketOrder("QLD", 100)  # Executes now
```

---

### 2.6 MOO Order Not Filling

**Error:**
MOO order submitted but never fills.

**Cause:** Order submitted too late or symbol halted.

**Solution:**
```python
# Check MOO fill status at 09:31 next day
def check_moo_fills(self):
    """Called at 09:31 to verify MOO orders filled."""
    for ticket in self.pending_moo_orders:
        if ticket.Status != OrderStatus.Filled:
            self.Log(f"MOO_FALLBACK: {ticket.Symbol} not filled, using market order")
            # Cancel unfilled MOO
            ticket.Cancel()
            # Submit market order as fallback
            self.MarketOrder(ticket.Symbol, ticket.Quantity)
```

---

## 3. State and Persistence Errors

### 3.1 ObjectStore Key Not Found

**Error:**
```
KeyError: 'ALPHA_NEXTGEN_CAPITAL'
```

**Cause:** Attempting to read key that doesn't exist.

**Solution:**
```python
# WRONG
data = json.loads(self.ObjectStore.Read("ALPHA_NEXTGEN_CAPITAL"))

# CORRECT
def load_capital_state(self):
    """Load capital state with fallback to defaults."""
    key = "ALPHA_NEXTGEN_CAPITAL"
    
    if not self.ObjectStore.ContainsKey(key):
        self.Log(f"STATE_INIT: {key} not found, using defaults")
        return self.get_default_capital_state()
    
    try:
        json_str = self.ObjectStore.Read(key)
        return json.loads(json_str)
    except Exception as e:
        self.Log(f"STATE_ERROR: Failed to load {key}: {e}")
        return self.get_default_capital_state()
```

---

### 3.2 JSON Serialization Error

**Error:**
```
TypeError: Object of type 'Decimal' is not JSON serializable
```

**Cause:** Attempting to serialize non-JSON-native types.

**Solution:**
```python
# WRONG
data = {"price": self.Securities["SPY"].Price}  # May be Decimal
json.dumps(data)

# CORRECT
def serialize_state(self, data: dict) -> str:
    """Serialize state with type conversion."""
    def convert(obj):
        if isinstance(obj, (Decimal, np.floating)):
            return float(obj)
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError(f"Cannot serialize {type(obj)}")
    
    return json.dumps(data, default=convert)
```

---

### 3.3 State Corruption After Restart

**Error:**
Unexpected behavior after algorithm restart, positions don't match.

**Cause:** State not reconciled with actual broker positions.

**Solution:**
```python
def reconcile_positions(self):
    """Reconcile persisted state with actual positions."""
    persisted = self.load_position_state()
    
    for symbol, state in persisted.items():
        actual = self.Portfolio[symbol]
        
        if not actual.Invested:
            self.Log(f"RECONCILE: {symbol} in state but not in portfolio, clearing")
            del persisted[symbol]
            continue
        
        if actual.Quantity != state.get("quantity"):
            self.Log(f"RECONCILE: {symbol} quantity mismatch. "
                    f"State={state.get('quantity')}, Actual={actual.Quantity}")
            state["quantity"] = actual.Quantity
    
    # Check for positions not in state
    for kvp in self.Portfolio:
        symbol = kvp.Key
        holding = kvp.Value
        if holding.Invested and str(symbol) not in persisted:
            self.Log(f"RECONCILE: {symbol} in portfolio but not in state, adding")
            persisted[str(symbol)] = {
                "quantity": holding.Quantity,
                "entry_price": holding.AveragePrice,  # Best available
                "strategy_tag": "UNKNOWN"
            }
    
    self.save_position_state(persisted)
```

---

## 4. Timing and Scheduling Errors

### 4.1 Scheduled Event Not Firing

**Error:**
Scheduled function never executes.

**Cause:** Incorrect time rules or date rules.

**Solution:**
```python
# WRONG - May not work as expected
self.Schedule.On(self.DateRules.EveryDay(), 
                 self.TimeRules.At(15, 45), 
                 self.EODProcessing)

# CORRECT - Specify symbol for market-aware scheduling
self.Schedule.On(self.DateRules.EveryDay("SPY"), 
                 self.TimeRules.At(15, 45), 
                 self.EODProcessing)

# Or use AfterMarketOpen/BeforeMarketClose
self.Schedule.On(self.DateRules.EveryDay("SPY"), 
                 self.TimeRules.BeforeMarketClose("SPY", 15),  # 15 min before close
                 self.EODProcessing)
```

---

### 4.2 Time Zone Confusion

**Error:**
Events firing at unexpected times.

**Cause:** Mixing UTC and Eastern time.

**Solution:**
```python
# self.Time is always in algorithm time zone (set in Initialize)
def Initialize(self):
    self.SetTimeZone("America/New_York")  # All times will be Eastern

# When checking time
def is_time_guard_active(self) -> bool:
    """Check if we're in the Fed announcement window."""
    current = self.Time.time()
    guard_start = time(13, 55)
    guard_end = time(14, 10)
    return guard_start <= current <= guard_end
```

---

### 4.3 Weekend/Holiday Processing

**Error:**
Algorithm processes data on weekends or holidays.

**Cause:** Not checking if market is open.

**Solution:**
```python
def OnData(self, data):
    # Skip if market closed (shouldn't happen with proper data, but be safe)
    if not self.IsMarketOpen("SPY"):
        return
    
    # Skip weekends (if somehow triggered)
    if self.Time.weekday() >= 5:
        return
```

---

## 5. Calculation Errors

### 5.1 Division by Zero

**Error:**
```
ZeroDivisionError: division by zero
```

**Cause:** Dividing by price, volume, or other values that could be zero.

**Solution:**
```python
# WRONG
bandwidth = (upper - lower) / middle

# CORRECT
def calculate_bandwidth(self, upper: float, lower: float, middle: float) -> float:
    """Calculate Bollinger Band bandwidth safely."""
    if middle <= 0:
        self.Log("CALC_WARN: Middle band is zero, returning default")
        return 0.0
    return (upper - lower) / middle
```

---

### 5.2 Floating Point Comparison

**Error:**
Conditions not triggering when expected due to floating point precision.

**Solution:**
```python
# WRONG
if profit_pct == 0.15:  # May never be exactly 0.15

# CORRECT
import math

def approx_equal(a: float, b: float, tolerance: float = 1e-9) -> bool:
    return math.isclose(a, b, rel_tol=tolerance)

# Or use threshold comparison
if profit_pct >= 0.15:  # Use >= instead of ==
```

---

### 5.3 Percentage vs Decimal Confusion

**Error:**
Calculations off by factor of 100.

**Cause:** Mixing percentage (15) with decimal (0.15).

**Solution:**
```python
# Establish convention: ALL internal values are decimals
# Document clearly

# Config uses decimals
KILL_SWITCH_PCT = 0.03  # 3% as decimal

# Calculations use decimals
loss_pct = (baseline - current) / baseline  # Returns decimal

# Only convert for display/logging
self.Log(f"Loss: {loss_pct:.2%}")  # Formats as "3.00%"
```

---

## 6. Portfolio and Position Errors

### 6.1 Checking Wrong Property

**Error:**
Position checks fail unexpectedly.

**Cause:** Using wrong portfolio property.

**Solution:**
```python
# Different properties mean different things
holding = self.Portfolio["SPY"]

# Is there ANY position (long or short)?
if holding.Invested:
    ...

# Specifically long?
if holding.IsLong:
    ...

# Specifically short?
if holding.IsShort:
    ...

# Current value
value = holding.HoldingsValue  # Signed (negative if short)
abs_value = holding.AbsoluteHoldingsValue  # Always positive

# Quantity
qty = holding.Quantity  # Signed
```

---

### 6.2 Position Sizing Overflow

**Error:**
Positions larger than expected or negative.

**Cause:** Integer overflow or sign errors in quantity calculation.

**Solution:**
```python
def calculate_quantity(self, symbol: str, target_value: float) -> int:
    """Calculate order quantity with bounds checking."""
    price = self.Securities[symbol].Price
    
    if price <= 0:
        self.Log(f"SIZE_ERROR: {symbol} price is {price}")
        return 0
    
    quantity = int(target_value / price)
    
    # Bounds check
    MAX_QUANTITY = 100000  # Sanity limit
    if abs(quantity) > MAX_QUANTITY:
        self.Log(f"SIZE_ERROR: {symbol} quantity {quantity} exceeds max")
        return 0
    
    return quantity
```

---

## 7. Split-Related Errors

### 7.1 Indicators Corrupted After Split

**Error:**
Bollinger Bands, ATR, or other indicators show crazy values after a stock split.

**Cause:** Split causes apparent 50% price drop (for 2:1 split), corrupting indicator calculations.

**Solution:**
```python
def OnData(self, data):
    # Check for splits on PROXY symbols (freezes everything)
    if data.Splits.ContainsKey(self.spy):
        self.Log(f"SPLIT_GUARD: SPY split detected, freezing all processing")
        return
    
    # Check for splits on TRADED symbols (freeze that symbol only)
    for symbol in self.traded_symbols:
        if data.Splits.ContainsKey(symbol):
            self.Log(f"SPLIT_GUARD: {symbol} split detected, skipping")
            self.frozen_symbols.add(symbol)
            continue
```

---

## 8. Debugging Tips

### 8.1 Enable Verbose Logging

```python
def Initialize(self):
    self.SetDebugMode(True)  # More detailed logs
```

### 8.2 Log State at Key Points

```python
def log_portfolio_state(self):
    """Log complete portfolio state for debugging."""
    self.Log("=" * 50)
    self.Log(f"TIME: {self.Time}")
    self.Log(f"EQUITY: ${self.Portfolio.TotalPortfolioValue:,.2f}")
    self.Log(f"CASH: ${self.Portfolio.Cash:,.2f}")
    self.Log(f"MARGIN: ${self.Portfolio.MarginRemaining:,.2f}")
    
    for kvp in self.Portfolio:
        if kvp.Value.Invested:
            h = kvp.Value
            self.Log(f"  {kvp.Key}: {h.Quantity} @ ${h.AveragePrice:.2f} = ${h.HoldingsValue:,.2f}")
    self.Log("=" * 50)
```

### 8.3 Use Assertions in Development

```python
def validate_order(self, symbol: str, quantity: int):
    """Validate order before submission (dev only)."""
    assert quantity != 0, f"Zero quantity for {symbol}"
    assert abs(quantity) < 100000, f"Quantity {quantity} too large"
    assert symbol in self.Securities, f"Unknown symbol {symbol}"
```

---

## 9. Backtest Log Limit (100KB)

### 9.1 Log Quota Exhausted

**Error:**
```
Backtest logs truncated at 100KB limit
```
or backtest appears to "stop" at an early date when logs end.

**Cause:** QuantConnect imposes a 100KB log limit per backtest. Verbose logging, especially during the 252-day warmup period, can exhaust this quota before meaningful trading begins.

**Solution:**

**A. Add warmup checks to ALL scheduled callbacks:**
```python
def _on_pre_market_setup(self) -> None:
    # Skip during warmup - no logging needed
    if self.IsWarmingUp:
        return
    # ... rest of function
```

**B. Disable or minimize frequent debug logs:**
```python
# WRONG - Logs every day
def _on_sod_baseline(self):
    self.Log(f"SOD: equity=${equity:,.2f}")

# CORRECT - Only log fills and critical events
def _on_sod_baseline(self):
    # No logging - fills are logged in OnOrderEvent
    pass
```

**C. Use conditional logging for debug info:**
```python
# WRONG - Logs weekly even during warmup
if self.Time.weekday() == 0:
    self.Log(f"DEBUG: {details}")

# CORRECT - Only after warmup
if not self.IsWarmingUp and self.Time.weekday() == 0:
    self.Log(f"DEBUG: {details}")
```

---

### 9.2 Recommended Logging Strategy for Backtests

**Keep these logs (high value, low volume):**

| Log Type | When | Example |
|----------|------|---------|
| INIT | Once at startup | `INIT: Complete \| Cash=$50,000` |
| FILL | On trade execution | `FILL: BUY 100 TQQQ @ $45.00` |
| INVALID | On order errors | `INVALID: TQQQ - Insufficient funds` |
| KILL_SWITCH | On 3% daily loss | `KILL_SWITCH: Triggered` |
| PANIC_MODE | On SPY -4% drop | `PANIC_MODE: Triggered` |
| SPLIT | On proxy splits | `SPLIT: SPY (proxy) - freezing all` |
| STATE_ERROR | On persistence errors | `STATE_ERROR: Failed to load` |

**Remove/disable these logs (high volume):**

| Log Type | Frequency | Why Remove |
|----------|-----------|------------|
| SOD baseline | Daily (252+1260 days) | ~1500 log entries |
| EOD processing | Daily | ~1500 log entries |
| Daily summary | Daily | ~1500 log entries |
| Position reconcile | Daily | ~1500 log entries |
| TIME_GUARD start/end | Daily | ~3000 log entries |
| WEEKLY_RESET | Weekly | ~260 log entries |
| Debug RSI/drop | Daily | ~1500 log entries |
| State save/load | Daily | ~3000 log entries |

**Estimated log budget:**

| Period | Days | Max Budget |
|--------|------|------------|
| Warmup (2019) | 252 | ~0 bytes (skip all) |
| Trading (2020-2024) | 1260 | ~100KB |
| Per trading day | 1 | ~80 bytes |

**Formula:** `(fills × 60 bytes) + (errors × 50 bytes) + (critical events × 30 bytes) < 100KB`

---

### 9.3 Debugging with Limited Logs

When you need detailed logging for debugging but are hitting limits:

**A. Use date-limited logging:**
```python
# Only log during a specific investigation period
if date(2020, 3, 1) <= self.Time.date() <= date(2020, 3, 15):
    self.Log(f"DEBUG: {details}")
```

**B. Log only on anomalies:**
```python
# Only log when something unexpected happens
if rsi < 10:  # Extremely oversold - worth logging
    self.Log(f"EXTREME: RSI={rsi}")
```

**C. Use aggregated end-of-day logging:**
```python
# Accumulate stats, log once per day
self._daily_stats["mr_checks"] += 1
self._daily_stats["signals_generated"] += len(signals)

# At EOD, log summary only
if self._daily_stats["signals_generated"] > 0:
    self.Log(f"DAY_SUMMARY: {self._daily_stats}")
```

**D. Comment out during production:**
```python
# DEBUG: Disabled to save log space
# if self.Time.hour == 10 and self.Time.minute == 10:
#     self.Log(f"MR_DEBUG: RSI={rsi} drop={drop_pct}")
```

---

## Quick Reference: Error Categories

| Error Type | Common Cause | First Check |
|------------|--------------|-------------|
| Symbol not found | Not subscribed | `Initialize()` has `AddEquity()` |
| Indicator not ready | Insufficient warmup | `indicator.IsReady` |
| Order rejected | Timing/quantity | Market hours, whole shares |
| MOO invalid | Wrong timing | Must be in `OnEndOfDay` or scheduled `BeforeMarketClose` |
| State missing | First run | `ContainsKey()` before `Read()` |
| Time issues | Timezone | `SetTimeZone()` in Initialize |
| Calculation wrong | Types/precision | Decimal vs percentage |
| Split corruption | Corporate action | Check `data.Splits.ContainsKey()` |
| Logs truncated | 100KB limit | Add `IsWarmingUp` checks, reduce daily logs |
