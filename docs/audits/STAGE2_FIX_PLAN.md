# Stage 2 Fix Plan - Theory & Implementation

## Analysis: Why Options Trigger at Exactly 10:00 AM

### Root Cause
The options entry fires at exactly 10:00:00 because:

1. **Time Window Opens at 10:00**
   ```python
   # _scan_options_signals()
   if current_hour < 10 or current_hour >= 15:
       return  # Blocked before 10:00
   ```

2. **All Entry Conditions Are Always True in Bull Market**
   ```
   Score=3.25 = 1.00 (ADX) + 1.00 (Momentum) + 0.25 (IV) + 1.00 (Liquidity)
   ```
   - ADX ≥ 25 in trending market → 1.00 ✓
   - Price > MA200 in bull market → 1.00 ✓
   - IV rank is moderate → 0.25 ✓
   - Liquidity is always good → 1.00 ✓

3. **Swing Filters Don't Block at Open**
   - Time window: 10:00-14:30 → passes at 10:00
   - Gap filter: Only blocks if gap is large AND wrong direction
   - Extreme move filter: SPY hasn't dropped, VIX hasn't spiked yet

4. **No "Settling Time" Required**
   - The system fires immediately when window opens
   - No requirement for price confirmation or pullback

### Why This Is Problematic
- **First 30 minutes are volatile** - most false breakouts happen here
- **No intraday price action evaluation** - entering blind based on prior day's conditions
- **Same conditions = same behavior** - enters every single day at 10:00

---

## Fix 1: Trend Trailing Stops Not Triggering (CRITICAL)

### Theory
The trailing stop logic exists but may not be executing properly. Possible causes:
1. Stop prices are set but never checked against current price
2. Stop prices are calculated incorrectly (too wide)
3. The stop check function isn't being called

### Investigation Steps
```python
# Check if _monitor_trend_stops() is actually running
# Add logging:
def _monitor_trend_stops(self, data):
    self._log(f"TREND_STOP_CHECK: Running for {len(invested_symbols)} positions")
    for symbol in invested_symbols:
        stop_price = self.trend_engine.get_stop_price(symbol)
        current_price = self.Securities[symbol].Price
        self._log(f"TREND_STOP: {symbol} | Current={current_price:.2f} | Stop={stop_price:.2f}")
```

### Fix Implementation
```python
# Option A: Verify stop check is running
def _monitor_trend_stops(self, data: Slice) -> None:
    """Check and update trailing stops for trend positions."""
    for symbol_str in ["QLD", "SSO", "TNA", "FAS"]:
        symbol = getattr(self, symbol_str.lower())
        if not self.Portfolio[symbol].Invested:
            continue

        current_price = self.Securities[symbol].Price
        signal = self.trend_engine.check_stop_hit(
            symbol=symbol_str,
            current_price=current_price,
        )

        # ADD LOGGING
        stop_info = self.trend_engine.get_position_info(symbol_str)
        if stop_info:
            self._log(f"TREND_STOP_CHECK: {symbol_str} | Price={current_price:.2f} | Stop={stop_info.stop_price:.2f}")

        if signal:
            self.portfolio_router.receive_signal(signal)
            self._process_immediate_signals()

# Option B: Review stop calculation
# Current: entry - (multiplier * ATR)
# May need tighter multiplier or time-based stop tightening
```

### Config Change
```python
# config.py - Review these values
TREND_STOP_ATR_MULTIPLIER = 2.5  # May be too wide, try 2.0
TREND_TRAILING_PROFIT_START = 0.10  # Start trailing at 10% profit
```

---

## Fix 2: Theta Threshold Too Tight (HIGH)

### Theory
The -0.02 (-2%) daily theta threshold is designed for 30+ DTE options. Short-dated options (5-17 DTE) naturally have 5-15% daily theta decay as they approach expiry.

### Current Behavior
```
Jan 2: Theta=-0.14 < -0.02 → BREACH (14% daily decay on 17 DTE option)
Jan 18: Theta=-0.05 < -0.02 → BREACH (5% daily decay)
```

### Fix Implementation
```python
# Option A: Scale threshold by DTE
def _get_theta_threshold(self, days_to_expiry: int) -> float:
    """Get theta threshold based on DTE."""
    if days_to_expiry <= 7:
        return -0.15  # 15% for weeklies (0-7 DTE)
    elif days_to_expiry <= 14:
        return -0.10  # 10% for 8-14 DTE
    elif days_to_expiry <= 21:
        return -0.05  # 5% for 15-21 DTE
    else:
        return -0.02  # 2% for 22+ DTE

# In risk_engine.py check_cb_greeks_breach():
theta_threshold = self._get_theta_threshold(position_dte)
if greeks.theta < theta_threshold:
    breach_reasons.append(f"Theta={greeks.theta:.2f} < {theta_threshold}")
```

### Alternative: Disable Theta Check for Swing Mode
```python
# Since swing mode uses stops and profit targets, theta monitoring
# may be redundant. Consider disabling for swing, keeping for intraday.
CB_THETA_CHECK_ENABLED = False  # For swing mode
```

---

## Fix 3: Options-Specific Loss Limit (HIGH)

### Theory
Currently, options losses contribute to the portfolio kill switch (3%), which then blocks ALL trading. Options should have their own loss limit that only disables options, not the whole portfolio.

### Fix Implementation
```python
# config.py
OPTIONS_DAILY_LOSS_LIMIT = 0.05  # 5% of portfolio triggers options pause
OPTIONS_WEEKLY_LOSS_LIMIT = 0.10  # 10% weekly triggers options pause

# risk_engine.py
def check_options_loss_limit(self, options_pnl_today: float, portfolio_value: float) -> bool:
    """Check if options-specific loss limit is breached."""
    options_loss_pct = abs(options_pnl_today) / portfolio_value
    if options_loss_pct >= self._options_daily_loss_limit:
        self.log(f"OPTIONS_PAUSE: Daily loss {options_loss_pct:.2%} >= {self._options_daily_loss_limit:.2%}")
        self._options_paused = True
        return True
    return False

# In RiskCheckResult:
@dataclass
class RiskCheckResult:
    ...
    options_paused: bool = False  # NEW: Options-specific pause
```

---

## Fix 4: 10:00 AM Entry Timing (MEDIUM)

### Theory
Instead of firing immediately at 10:00, require:
1. Time after open settling period (10:15 or 10:30)
2. Intraday price action confirmation
3. Limit entries to once per day with cooldown

### Fix Implementation
```python
# Option A: Delay window start
# config.py
OPTIONS_WINDOW_START_HOUR = 10
OPTIONS_WINDOW_START_MINUTE = 30  # Start at 10:30 instead of 10:00

# Option B: Require intraday confirmation
def check_swing_filters(self, ...):
    ...
    # Filter 4: Minimum time after open
    if current_hour == 10 and current_minute < 30:
        return False, "Waiting for market to settle (10:30)"

    # Filter 5: Price action confirmation (not just gap)
    if abs(spy_intraday_change_pct) < 0.1:
        return False, "No directional conviction yet"

# Option C: Daily entry cooldown
def _scan_options_signals(self, data):
    ...
    # Only allow one swing entry per day
    if self._swing_entry_today:
        return
```

---

## Fix 5: Intraday Options Not Triggering (MEDIUM)

### Theory
Intraday mode (0-2 DTE) never triggers because:
1. 0-2 DTE contracts may not exist in QC backtest data
2. Contracts exist but fail bid/ask or open interest filters
3. Micro Regime conditions are never satisfied

### Investigation Steps
```python
# Add logging to _select_intraday_option_contract()
def _select_intraday_option_contract(self, chain):
    dte_0_2_count = sum(1 for c in chain if (c.Expiry - self.Time).days <= 2)
    self._log(f"INTRADAY_DEBUG: Found {dte_0_2_count} contracts with DTE 0-2")

    candidates = []
    for contract in chain:
        dte = (contract.Expiry - self.Time).days
        if dte < 0 or dte > 2:
            continue

        # Log why contracts are filtered out
        strike_diff = abs(contract.Strike - qqq_price)
        if strike_diff > qqq_price * 0.02:
            self._log(f"INTRADAY_FILTER: Strike {contract.Strike} too far from {qqq_price}")
            continue
        ...
```

### Potential Fix
```python
# If 0-2 DTE data is unavailable, expand to 0-5 DTE for "intraday"
# Or accept that intraday mode won't work in QC backtests
INTRADAY_DTE_MAX = 5  # Expand from 2 to 5
```

---

## Fix 6: Kill Switch Cascade Prevention (HIGH)

### Theory
The cascade happens because:
1. Options lose → portfolio drops 3%+ → kill switch
2. Kill switch blocks warm entries
3. Trend positions continue bleeding
4. Next day: still down 3%+ from new baseline → kill switch again

### Fix Implementation
```python
# Option A: Separate options losses from kill switch calculation
def check_kill_switch(self, current_equity: float, options_unrealized: float = 0) -> bool:
    """Check kill switch, excluding options unrealized P&L."""
    # Calculate loss excluding options
    equity_ex_options = current_equity - options_unrealized
    # ... rest of logic

# Option B: Reset baseline after kill switch to current equity
def trigger_kill_switch(self):
    """Trigger kill switch and reset baseline."""
    self._kill_switch_active = True
    # Reset baseline to prevent cascade
    self._equity_prior_close = current_equity  # NEW

# Option C: Cooldown period after kill switch
def check_kill_switch(self):
    if self._kill_switch_cooldown_until and self.Time < self._kill_switch_cooldown_until:
        return False  # Still in cooldown
```

---

## Implementation Priority

| Fix | Severity | Effort | Order |
|-----|----------|--------|-------|
| Fix 1: Trend stops | CRITICAL | Medium | 1st |
| Fix 3: Options loss limit | HIGH | Low | 2nd |
| Fix 6: Kill switch cascade | HIGH | Medium | 3rd |
| Fix 2: Theta threshold | HIGH | Low | 4th |
| Fix 4: 10:00 AM timing | MEDIUM | Low | 5th |
| Fix 5: Intraday options | MEDIUM | Medium | 6th |

---

## Recommended Testing Approach

### After Fixes
1. Run 1-day backtest to verify no errors
2. Run 7-day backtest to verify trend stops trigger
3. Run 30-day backtest (Stage 2 retest)
4. Only proceed to Phase 3 if Stage 2 passes

### Success Criteria for Phase 3
- [ ] Trend positions exit on stops within reasonable drawdown
- [ ] Kill switch triggers < 5 times in 30 days
- [ ] Cold start completes (reaches day 5)
- [ ] Options win rate ≥ 35%
- [ ] No massive buying power error spam

---

## Summary

The core issue is a **cascade of failures**:
1. Options enter at 10:00 with no confirmation → high stop-out rate
2. Options losses trigger kill switch → blocks recovery
3. Trend positions never stop out → continuous bleeding
4. System stuck in death spiral

The fixes address each point in the cascade to break the cycle.
