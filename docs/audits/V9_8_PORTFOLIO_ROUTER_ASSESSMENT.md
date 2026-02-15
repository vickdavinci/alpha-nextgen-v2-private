# Portfolio Router Assessment — Critical Gaps & Bugs

**Date:** 2026-02-14
**Version:** V9.8 (post spread execution, kill switch decoupling, hold guards, micro regime, OCO tagging changes)
**Scope:** `portfolio/portfolio_router.py`, `main.py` (order flow), `execution/`

## Assessment Summary

After thorough exploration of the portfolio router and its interactions with main.py and the execution layer, here are the **verified** findings ranked by severity.

---

## CRITICAL (Could lose money in live trading)

### C1. EOD Processing Bypasses Router Safeguards
**File:** `main.py:4262-4385` (`_process_eod_signals`)

- Directly accesses `self.portfolio_router._pending_weights` (line 4278) and calls `.clear()` (line 4279)
- Calls `self.SetHoldings(targets)` at line 4385 — bypassing the router's `execute_orders()` entirely
- **No margin pre-checks, no options budget gate, no leverage cap, no duplicate detection**
- Options symbols are silently filtered out at line 4370-4374 (`self.traded_symbols` doesn't contain option symbols), so any options exit signal queued as EOD urgency is **silently consumed and lost**

**Impact:** Options exit signals with EOD urgency are drained from the queue but never executed. Equity positions rebalanced without router validation.

### C2. Metadata Overwrite on Aggregation
**File:** `portfolio/portfolio_router.py:1518-1520` (`aggregate_weights`)

```python
if weight.metadata is not None:
    agg.metadata = weight.metadata  # OVERWRITES previous metadata
```

- When multiple signals arrive for the same symbol, the last one's metadata wins
- Spread config (width, credit, short_leg_symbol) from Signal 1 is replaced by Signal 2's metadata
- This breaks combo order creation downstream which reads `spread_short_leg_symbol` from metadata

**Impact:** Spread orders could lose their short leg configuration, creating naked long positions instead of spreads.

### C3. _pending_weights Consumed by Two Separate Processes
**Files:** `main.py:4278-4279` (EOD) and `portfolio_router.py` (immediate processing)

- `_process_eod_signals()` copies and clears ALL `_pending_weights` (line 4278-4279)
- Router's `process_immediate()` filters IMMEDIATE urgency from the same `_pending_weights`
- If EOD processing runs first, all IMMEDIATE signals are consumed and lost
- If IMMEDIATE processing runs first, EOD signals survive — but the order depends on scheduling

**Impact:** Race condition where signal processing order determines which trades execute.

---

## HIGH (Operational risk, may cause tracking issues)

### H1. Kill Switch Clears Tracking But Not In-Flight Orders
**File:** `main.py:3833-3870` (kill switch Tier 3)

- `_pending_spread_orders.clear()` at line 3848 removes tracking
- But `ComboMarketOrder` calls already submitted to broker continue executing
- When those in-flight orders fill, the fill handler can't find them in tracking dicts
- Leads to unrecognized fills and potentially untracked spread positions

**Impact:** Post-KS fills create ghost positions not tracked by the options engine.

### H2. No Deduplication for Combo Orders in execute_orders()
**File:** `portfolio/portfolio_router.py:1287-1295`

- Duplicate detection key: `f"{order.symbol}:{order.side.value}:{order.quantity}"`
- For combos, this only checks the **long leg** — ignores the short leg symbol entirely
- Two different spread types with the same long leg symbol and quantity slip through
- The `_executed_this_minute` guard doesn't distinguish between different spread configurations

**Impact:** Same spread could be submitted twice if signal fires rapidly.

### H3. Spread Fill Tracking Assumes Short Leg Fills First
**File:** `main.py:1955-1960`

- `_pending_spread_orders` is keyed by short symbol
- When a fill arrives, it checks `if symbol in self._pending_spread_orders`
- If the **long leg fills first**, it won't be found in the dict (it's keyed by short)
- The reverse mapping at `_pending_spread_orders_reverse` exists but the fill handler doesn't check it in all code paths

**Impact:** Stale tracking entries persist, potentially blocking future spread entries.

---

## MEDIUM (Correctness issues, unlikely to cause immediate loss)

### M1. Price Injection Modifies Caller's Dict
**File:** `portfolio/portfolio_router.py` (calculate_order_intents)

- When metadata contains `contract_price`, it's injected into `current_prices` dict
- This mutates the caller's dictionary, affecting subsequent orders in the same batch
- Stale metadata prices could override fresh market prices

### M2. Exposure Limits Don't Apply to Options Positions
**File:** `portfolio/portfolio_router.py` (validate_weights)

- Exposure calculator enforces limits on symbol weights (percentage-based)
- Options positions have fixed contract multipliers — a 20% allocation could represent varying contract counts
- Exposure groups track equities well but options exposure is managed separately by margin tracking

### M3. Direct _pending_weights Access Pattern
**Files:** `main.py:4278`, `main.py:2607` (multiple locations)

- Private attribute accessed directly instead of through public API
- Creates coupling between main.py and router internals
- Any internal restructuring of the router will silently break main.py

---

## FALSE POSITIVES (Investigated and confirmed non-issues)

- **Margin double-registration**: `register_spread_margin` uses `=` assignment, not `+=`. Same key overwrites, doesn't accumulate. Safe.
- **EOD options bypass**: Options symbols are filtered by `traded_symbols` lookup, which is by design — options use IMMEDIATE urgency, not EOD. The real issue is C1/C3 where an options signal *could* arrive as EOD.

---

## Recommended Fix Priority

| Priority | Bug | Fix Complexity | Risk if Unfixed |
|----------|-----|:--------------:|-----------------|
| 1 | C1 - EOD bypass | Medium | Options exit signals silently lost |
| 2 | C2 - Metadata overwrite | Low | Naked long instead of spread |
| 3 | C3 - Dual consumption | Medium | Signals lost in race condition |
| 4 | H1 - KS in-flight orders | Medium | Ghost positions post-KS |
| 5 | H2 - Combo dedup | Low | Double spread execution |
| 6 | H3 - Fill tracking order | Low | Stale tracking entries |

## Verification

After any fixes:
1. Run full test suite: `pytest tests/ -v`
2. Run scenario tests: `pytest tests/scenarios/ -v`
3. Backtest V9.8 on 2017 and 2023 — compare order counts and P&L to verify no regression
4. Check logs for `ROUTER:` prefixed messages to verify order flow integrity
