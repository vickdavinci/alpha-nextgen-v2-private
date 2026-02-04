# Integration Test Scenarios

This folder contains simulated market data for comprehensive integration testing.

## Scenarios

| Scenario | Description | Tests |
|----------|-------------|-------|
| `bullish_day/` | Strong uptrend, low VIX | Trend signals, RISK_ON regime |
| `crash_day/` | SPY -4%, VIX spike | Panic mode, kill switch, MR oversold |
| `vix_spike/` | VIX jumps 15→35 | Micro regime changes, intraday blocking |
| `vix_whipsaw/` | VIX oscillates wildly | Whipsaw detection, strategy switching |
| `mean_reversion/` | TQQQ drops -5% sharply | MR entry signals, oversold detection |
| `multi_day/` | 5 consecutive days | Cold start, state persistence, SMA200 |

## Data Format

All CSV files use the same format:
```csv
timestamp,open,high,low,close,volume,prior_close
```

Options data includes Greeks:
```csv
timestamp,symbol,call_put,strike,expiry,bid,ask,delta,gamma,vega,theta,iv_rank,open_interest
```

Indicator data (pre-calculated):
```csv
timestamp,symbol,sma_20,sma_200,rsi_14,adx_14,atr_14,bb_upper,bb_lower
```
