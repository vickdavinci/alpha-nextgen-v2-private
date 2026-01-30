# 19. OCO Order Manager

[Previous: 18 - Options Engine](18-options-engine.md) | [Table of Contents](00-table-of-contents.md)

---

## Overview

The **OCO (One-Cancels-Other) Manager** handles linked order pairs for options position exits. When an options position is opened, the OCO Manager creates a stop-loss and profit-target order pair. When one leg fills, the other is automatically cancelled.

**Key Characteristics:**
- **Atomic submission**: Both legs submitted together
- **Automatic cancellation**: Filling one cancels the other
- **No ghost orders**: Prevents orphaned orders causing unexpected fills
- **State persistence**: Survives algorithm restarts

---

## Why OCO Orders?

### The Problem Without OCO

```
1. Enter options position at $2.00
2. Place stop at $1.60 (-20%)
3. Place profit target at $3.00 (+50%)
4. Price hits $3.00, profit target fills
5. ❌ PROBLEM: Stop order still active at $1.60
6. Next day, price drops to $1.60
7. ❌ Stop triggers on ZERO position = short sale or rejection
```

### The Solution With OCO

```
1. Enter options position at $2.00
2. Create OCO pair: stop at $1.60, profit at $3.00
3. Price hits $3.00, profit target fills
4. ✅ OCO Manager automatically cancels stop order
5. No orphaned orders, no unexpected fills
```

---

## OCO State Machine

```
┌─────────┐
│ PENDING │ ─── Created but not submitted
└────┬────┘
     │ submit()
     ▼
┌─────────┐
│ ACTIVE  │ ─── Both orders live in market
└────┬────┘
     │
     ├── Stop fills ──────► STOP_TRIGGERED ──► CLOSED
     │
     ├── Profit fills ────► PROFIT_TRIGGERED ─► CLOSED
     │
     ├── Manual cancel ───► CANCELLED
     │
     └── Option expires ──► EXPIRED
```

### State Definitions

| State | Description |
|-------|-------------|
| `PENDING` | OCO pair created, awaiting submission |
| `ACTIVE` | Both legs submitted and live |
| `STOP_TRIGGERED` | Stop order filled, profit cancelled |
| `PROFIT_TRIGGERED` | Profit order filled, stop cancelled |
| `CANCELLED` | Manually cancelled (both legs) |
| `EXPIRED` | Option expired, orders cancelled |
| `CLOSED` | Final state, position fully exited |

---

## OCO Pair Structure

### OCOPair

```python
@dataclass
class OCOPair:
    pair_id: str           # Unique identifier
    option_symbol: str     # The option contract
    entry_price: float     # Entry price for reference
    entry_time: str        # When position was opened
    quantity: int          # Number of contracts
    stop_leg: OCOLeg       # Stop loss order
    profit_leg: OCOLeg     # Profit target order
    state: OCOState        # Current state
    created_at: str        # Timestamp
    updated_at: str        # Last update
```

### OCOLeg

```python
@dataclass
class OCOLeg:
    leg_type: OCOOrderType  # STOP or PROFIT
    trigger_price: float    # Price that triggers order
    quantity: int           # Contracts (negative for sell)
    broker_order_id: int    # Assigned by broker
    submitted: bool         # Has been submitted
    filled: bool            # Has been filled
    cancelled: bool         # Has been cancelled
    fill_price: float       # Actual fill price
    fill_time: str          # When filled
```

---

## Core Operations

### Creating an OCO Pair

```python
def create_oco_pair(
    self,
    option_symbol: str,
    entry_price: float,
    quantity: int,
    stop_price: float,
    profit_price: float
) -> OCOPair:
    """
    Create a new OCO pair for an options position.

    Args:
        option_symbol: The option contract symbol
        entry_price: Entry price of the position
        quantity: Number of contracts (positive)
        stop_price: Stop loss trigger price
        profit_price: Profit target trigger price

    Returns:
        OCOPair in PENDING state
    """
```

### Submitting Orders

```python
def submit_oco_pair(self, pair_id: str) -> bool:
    """
    Submit both legs of an OCO pair to the market.

    Both orders are submitted atomically. If either fails,
    both are cancelled and the pair moves to CANCELLED state.

    Returns:
        True if both orders submitted successfully
    """
```

### Handling Fills

```python
def on_order_filled(self, order_id: int, fill_price: float) -> None:
    """
    Handle an order fill event.

    When one leg fills:
    1. Mark that leg as filled
    2. Cancel the other leg
    3. Update pair state to STOP_TRIGGERED or PROFIT_TRIGGERED
    """
```

### Cancelling a Pair

```python
def cancel_oco_pair(self, pair_id: str, reason: str) -> bool:
    """
    Cancel both legs of an OCO pair.

    Used for:
    - Manual position close
    - End of day cleanup
    - Option expiration
    """
```

---

## Integration with Options Engine

### Entry Flow

```python
# In OptionsEngine.on_entry_fill()
def on_entry_fill(self, symbol: str, fill_price: float, quantity: int):
    # Calculate stop and profit prices
    stop_pct = self.get_stop_for_score(self.entry_score)
    stop_price = fill_price * (1 - stop_pct)
    profit_price = fill_price * (1 + OPTIONS_PROFIT_TARGET_PCT)

    # Create OCO pair
    oco_pair = self.oco_manager.create_oco_pair(
        option_symbol=symbol,
        entry_price=fill_price,
        quantity=quantity,
        stop_price=stop_price,
        profit_price=profit_price
    )

    # Submit immediately
    self.oco_manager.submit_oco_pair(oco_pair.pair_id)
```

### Exit Flow

```python
# In main.py OnOrderEvent()
def OnOrderEvent(self, orderEvent):
    if orderEvent.Status == OrderStatus.Filled:
        # Check if this is an OCO leg
        self.oco_manager.on_order_filled(
            orderEvent.OrderId,
            orderEvent.FillPrice
        )
```

---

## State Persistence

OCO pairs are persisted to ObjectStore for survival across restarts.

### Save Format

```json
{
    "oco_pairs": {
        "pair_123": {
            "pair_id": "pair_123",
            "option_symbol": "QQQ 260126C00450000",
            "entry_price": 2.50,
            "quantity": 10,
            "state": "ACTIVE",
            "stop_leg": {
                "leg_type": "STOP",
                "trigger_price": 2.00,
                "broker_order_id": 456,
                "submitted": true,
                "filled": false
            },
            "profit_leg": {
                "leg_type": "PROFIT",
                "trigger_price": 3.75,
                "broker_order_id": 457,
                "submitted": true,
                "filled": false
            }
        }
    }
}
```

### Recovery on Restart

```python
def load_state(self) -> None:
    """Load OCO pairs from ObjectStore."""
    data = self.algorithm.ObjectStore.Read(OCO_STATE_KEY)
    self._pairs = {
        pair_id: OCOPair.from_dict(pair_data)
        for pair_id, pair_data in data["oco_pairs"].items()
    }

    # Verify broker order IDs still valid
    self._reconcile_with_broker()
```

---

## Error Handling

### Partial Submission Failure

If one leg fails to submit, cancel the other:

```python
def submit_oco_pair(self, pair_id: str) -> bool:
    pair = self._pairs[pair_id]

    # Submit stop leg
    stop_order = self.algorithm.StopMarketOrder(...)
    if stop_order is None:
        pair.state = OCOState.CANCELLED
        return False

    # Submit profit leg
    profit_order = self.algorithm.LimitOrder(...)
    if profit_order is None:
        # Cancel the stop we just submitted
        self.algorithm.Transactions.CancelOrder(stop_order.OrderId)
        pair.state = OCOState.CANCELLED
        return False

    pair.state = OCOState.ACTIVE
    return True
```

### Order Rejection

```python
def on_order_rejected(self, order_id: int, reason: str) -> None:
    """Handle broker order rejection."""
    pair = self._find_pair_by_order_id(order_id)
    if pair:
        self.log(f"OCO_REJECTED: {pair.pair_id} | Reason: {reason}")
        # Cancel the other leg
        self._cancel_other_leg(pair, order_id)
        pair.state = OCOState.CANCELLED
```

### Orphan Detection

Run periodically to detect orphaned orders:

```python
def check_for_orphans(self) -> List[int]:
    """Find orders that don't belong to any OCO pair."""
    known_order_ids = set()
    for pair in self._pairs.values():
        if pair.stop_leg.broker_order_id:
            known_order_ids.add(pair.stop_leg.broker_order_id)
        if pair.profit_leg.broker_order_id:
            known_order_ids.add(pair.profit_leg.broker_order_id)

    orphans = []
    for order in self.algorithm.Transactions.GetOpenOrders():
        if order.Id not in known_order_ids:
            orphans.append(order.Id)

    return orphans
```

---

## Configuration Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `OCO_STATE_KEY` | "oco_state" | ObjectStore key for persistence |
| `OCO_RECONCILE_ON_START` | True | Verify orders on restart |
| `OCO_CANCEL_TIMEOUT_SEC` | 30 | Timeout for cancel confirmation |

---

## Logging

### Log Prefixes

| Prefix | Meaning |
|--------|---------|
| `OCO_CREATE` | New pair created |
| `OCO_SUBMIT` | Pair submitted to market |
| `OCO_FILL` | One leg filled |
| `OCO_CANCEL` | Leg or pair cancelled |
| `OCO_ERROR` | Error occurred |

### Example Logs

```
OCO_CREATE: pair_123 | QQQ 260126C00450000 | Entry=$2.50 | Stop=$2.00 | Profit=$3.75
OCO_SUBMIT: pair_123 | StopOrderId=456 | ProfitOrderId=457
OCO_FILL: pair_123 | PROFIT leg filled @ $3.80 | Cancelling STOP leg
OCO_CANCEL: pair_123 | STOP leg cancelled | OrderId=456
```

---

## Implementation Notes

### File Location

`execution/oco_manager.py`

### Key Classes

| Class | Purpose |
|-------|---------|
| `OCOManager` | Main manager class |
| `OCOPair` | Complete order pair |
| `OCOLeg` | Single order (stop or profit) |
| `OCOState` | State machine enum |
| `OCOOrderType` | STOP or PROFIT enum |

### Dependencies

- `config.py` (parameters)
- Algorithm's `Transactions` API
- `ObjectStore` for persistence

---

## Related Sections

- [18 - Options Engine](18-options-engine.md) - Creates OCO pairs
- [13 - Execution Engine](13-execution-engine.md) - Order submission
- [15 - State Persistence](15-state-persistence.md) - ObjectStore usage

---

[Previous: 18 - Options Engine](18-options-engine.md) | [Table of Contents](00-table-of-contents.md)
