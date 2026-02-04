# Production Readiness Checklist — Live Trading

> **Purpose:** Track items that must be addressed before deploying to Interactive Brokers live trading.
> Items here are NOT bugs — they are backtest-safe but will cause issues in live environments.

---

## 1. Stale Metadata Risk (Signal Freshness Validation)

**Status:** NOT IMPLEMENTED — Required before live deployment

### Problem

In live trading, the `contract_price` stored in `TargetWeight.metadata` could be seconds (or minutes) old by the time the Portfolio Router processes the signal. This creates a stale price risk:

- **Options signals (IMMEDIATE urgency):** Generated in `OnData` and processed in the same call — typically milliseconds apart. Low risk but non-zero in live environments with network latency.
- **Options signals (EOD urgency):** Generated during market hours but sit in `_pending_weights` until 15:45 EOD processing. The `contract_price` could be **hours old**, making it unreliable for order sizing.
- **Trend/Hedge signals (MOO urgency):** Submitted at 15:45 for next-day execution. Price staleness is irrelevant since MOO orders use market-on-open pricing.

### Why This Doesn't Affect Backtests

In QuantConnect backtests, all processing within an `OnData` call happens synchronously on the same bar. The `contract_price` from chain data and the `current_prices` lookup both reference the same point-in-time snapshot. There is no real latency between signal generation and router processing.

### Current State

- `TargetWeight.timestamp` field exists (`Optional[str]`) but is **never populated** by any engine
- No `SIGNAL_EXPIRY_SECONDS` config parameter exists
- No freshness validation in the router's price fallback chain

### Required Implementation (Pre-Live)

#### A. Populate Signal Timestamps

Every engine that creates a `TargetWeight` must set the timestamp:

```python
# In options_engine.py, mean_reversion_engine.py, etc.
from datetime import datetime

signal = TargetWeight(
    symbol="QQQ...",
    weight=0.05,
    source="OPT",
    urgency=Urgency.IMMEDIATE,
    reason="DEBIT_SPREAD_ENTRY",
    timestamp=self.algorithm.Time.strftime("%Y-%m-%d %H:%M:%S"),  # <-- SET THIS
    metadata={"contract_price": contract.LastPrice, ...}
)
```

#### B. Add Freshness Config

```python
# config.py
SIGNAL_EXPIRY_SECONDS = 30  # Reject metadata price if signal is older than this
```

#### C. Add Router Freshness Check

In `portfolio_router.py` → `calculate_order_intents()`, before using `metadata['contract_price']`:

```python
# Check signal freshness before trusting metadata price
if agg.metadata and agg.metadata.get("contract_price", 0) > 0:
    if agg.timestamp:
        signal_age = (self.algorithm.Time - datetime.strptime(agg.timestamp, "%Y-%m-%d %H:%M:%S")).total_seconds()
        if signal_age > config.SIGNAL_EXPIRY_SECONDS:
            self.log(f"ROUTER: STALE_METADATA | {symbol} | age={signal_age:.0f}s > {config.SIGNAL_EXPIRY_SECONDS}s | falling through to bid/ask")
            # Don't use metadata price — fall through to bid/ask mid-price
        else:
            current_prices[symbol] = agg.metadata["contract_price"]
    else:
        # No timestamp — trust metadata in backtest, reject in live
        current_prices[symbol] = agg.metadata["contract_price"]
```

### Risk Assessment

| Scenario | Probability | Impact | Mitigation |
|----------|:-----------:|:------:|------------|
| IMMEDIATE signal processed with stale price | Low | Medium — wrong position size | Freshness check rejects, falls through to bid/ask |
| EOD signal processed with hours-old price | High | High — significantly wrong size | Freshness check forces bid/ask lookup |
| Bid/Ask also stale (market closed) | Low | Low — EOD orders are MOO anyway | MOO orders ignore price |

### Verification (Post-Implementation)

```bash
# Search for signals without timestamps (should be zero)
grep -r "TargetWeight(" engines/ | grep -v "timestamp="

# Verify config exists
grep "SIGNAL_EXPIRY_SECONDS" config.py

# Backtest log patterns to verify:
# "STALE_METADATA" — confirms freshness check is active
# "BIDASK_INJECT" — confirms fallback chain works
```

---

## 2. [Future Items]

_Add additional live-trading-only concerns here as they are identified._

---

*Document created: 2026-02-03 | Last updated: 2026-02-03 (V2.24.1 Hardening)*
