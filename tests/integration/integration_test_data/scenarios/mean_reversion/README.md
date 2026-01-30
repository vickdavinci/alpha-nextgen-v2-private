# Mean Reversion Scenario

## Overview

This scenario simulates a sharp morning selloff that triggers mean reversion entry
conditions, followed by a bounce that hits the profit target.

## Test Conditions

| Condition | Status | Details |
|-----------|--------|---------|
| RSI(5) < 25 | TRIGGERED | TQQQ drops 12% in first hour |
| Lower BB Touch | TRIGGERED | Sharp decline breaks lower band |
| MR Entry Window | ACTIVE | 10:00-15:00 ET |
| VIX Filter | PASS | VIX at 22-25 (< 30 threshold) |
| Panic Mode | NOT TRIGGERED | SPY -1% (< -4% threshold) |
| Profit Target | HIT | +2% exit around 11:00-11:30 |

## Price Summary

| Symbol | Prior Close | Open | Low | High | Close | Change |
|--------|-------------|------|-----|------|-------|--------|
| TQQQ | 51.00 | 50.00 | 44.00 | 50.00 | 48.00 | -5.9% |
| SOXL | 36.00 | 35.00 | 30.50 | 35.00 | 33.00 | -8.3% |
| SPY | 500.00 | 498.00 | 494.00 | 498.00 | 495.00 | -1.0% |
| QQQ | 440.00 | 436.00 | 422.00 | 436.00 | 429.50 | -2.4% |
| VIX | 21.00 | 22.00 | 22.00 | 24.50 | 22.00 | +4.8% |

## Expected Behavior

1. **9:30-10:00**: Market opens weak, TQQQ/SOXL declining
2. **10:00**: MR entry window opens
3. **10:00-10:30**: RSI(5) drops below 25, prices hit lower BB
4. **10:30**: MR entry triggered for TQQQ at ~$44, SOXL at ~$30.50
5. **10:30-11:30**: Bounce begins, profit target (+2%) hit
6. **11:30+**: Position closed at profit target
7. **15:45**: No positions to force-close (already exited)

## Files

- `TQQQ.csv` - Primary MR symbol with RSI < 25 trigger
- `SOXL.csv` - Secondary MR symbol with similar pattern
- `SPY.csv` - Index for panic mode check (mild -1%)
- `QQQ.csv` - Drives TQQQ movement (-4%)
- `VIX.csv` - Volatility filter (22-25 range = MEDIUM)
- `generate_data.py` - Script that generated these files

## Data Format

```
timestamp,open,high,low,close,volume,prior_close
2026-01-28 09:30:00,50.00,50.12,49.95,50.05,750000,51.00
```

Generated: 2026-01-28
