"""
OCO Order Manager - One-Cancels-Other order pair management.

Implements linked stop/profit orders for options positions:
- Atomic submission (both legs submitted together)
- Automatic cancellation (when one fills, cancel the other)
- No "ghost orders" after one fills
- Error handling for partial fills

Used by Options Engine for exit management.

V2.1 Modification #3: OCO Order Pairs
- Every options entry creates both stop and profit orders
- Orders are linked - filling one cancels the other
- Prevents orphaned orders that could cause unexpected fills

Spec: docs/v2-specs/V2-1-FINAL-SYNTHESIS.md (Modification #3)
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from AlgorithmImports import QCAlgorithm

import config


class OCOOrderType(Enum):
    """Type of order in OCO pair."""

    STOP = "STOP"  # Stop loss order
    PROFIT = "PROFIT"  # Profit target order


class OCOState(Enum):
    """
    OCO pair state machine.

    State Transitions:
    PENDING -> ACTIVE (both orders submitted)
    ACTIVE -> STOP_TRIGGERED (stop hit, profit cancelled)
    ACTIVE -> PROFIT_TRIGGERED (profit hit, stop cancelled)
    ACTIVE -> CANCELLED (manual cancellation)
    ACTIVE -> EXPIRED (option expired)
    *_TRIGGERED -> CLOSED (position closed)
    """

    PENDING = "PENDING"  # Created but not submitted
    ACTIVE = "ACTIVE"  # Both orders live in market
    STOP_TRIGGERED = "STOP_TRIGGERED"  # Stop filled, profit cancelled
    PROFIT_TRIGGERED = "PROFIT_TRIGGERED"  # Profit filled, stop cancelled
    CANCELLED = "CANCELLED"  # Manually cancelled
    EXPIRED = "EXPIRED"  # Option expired worthless
    CLOSED = "CLOSED"  # Position fully closed


@dataclass
class OCOLeg:
    """
    Single leg of an OCO order pair.

    Represents either the stop or profit target order.
    """

    leg_type: OCOOrderType
    trigger_price: float  # Price that triggers the order
    quantity: int  # Number of contracts (negative for sell)
    broker_order_id: Optional[int] = None  # Assigned by broker
    submitted: bool = False
    filled: bool = False
    cancelled: bool = False
    fill_price: float = 0.0
    fill_time: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for persistence."""
        return {
            "leg_type": self.leg_type.value,
            "trigger_price": self.trigger_price,
            "quantity": self.quantity,
            "broker_order_id": self.broker_order_id,
            "submitted": self.submitted,
            "filled": self.filled,
            "cancelled": self.cancelled,
            "fill_price": self.fill_price,
            "fill_time": self.fill_time,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OCOLeg":
        """Deserialize from persistence."""
        return cls(
            leg_type=OCOOrderType(data["leg_type"]),
            trigger_price=data["trigger_price"],
            quantity=data["quantity"],
            broker_order_id=data.get("broker_order_id"),
            submitted=data.get("submitted", False),
            filled=data.get("filled", False),
            cancelled=data.get("cancelled", False),
            fill_price=data.get("fill_price", 0.0),
            fill_time=data.get("fill_time"),
        )


@dataclass
class OCOPair:
    """
    OCO order pair linking stop and profit orders.

    When one order fills, the other is automatically cancelled.
    """

    oco_id: str  # Unique identifier (format: OCO-YYYYMMDD-NNN)
    symbol: str  # Option symbol
    entry_price: float  # Original entry price
    stop_leg: OCOLeg  # Stop loss leg
    profit_leg: OCOLeg  # Profit target leg
    state: OCOState = OCOState.PENDING
    created_at: Optional[str] = None
    activated_at: Optional[str] = None
    closed_at: Optional[str] = None
    close_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for persistence."""
        return {
            "oco_id": self.oco_id,
            "symbol": self.symbol,
            "entry_price": self.entry_price,
            "stop_leg": self.stop_leg.to_dict(),
            "profit_leg": self.profit_leg.to_dict(),
            "state": self.state.value,
            "created_at": self.created_at,
            "activated_at": self.activated_at,
            "closed_at": self.closed_at,
            "close_reason": self.close_reason,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OCOPair":
        """Deserialize from persistence."""
        return cls(
            oco_id=data["oco_id"],
            symbol=data["symbol"],
            entry_price=data["entry_price"],
            stop_leg=OCOLeg.from_dict(data["stop_leg"]),
            profit_leg=OCOLeg.from_dict(data["profit_leg"]),
            state=OCOState(data["state"]),
            created_at=data.get("created_at"),
            activated_at=data.get("activated_at"),
            closed_at=data.get("closed_at"),
            close_reason=data.get("close_reason", ""),
        )


class OCOManager:
    """
    Manages OCO (One-Cancels-Other) order pairs.

    Ensures atomic execution and proper cleanup:
    - Both stop and profit orders submitted together
    - When one fills, the other is cancelled
    - No orphaned orders in the market
    """

    def __init__(self, algorithm: Optional["QCAlgorithm"] = None):
        """Initialize OCO Manager."""
        self.algorithm = algorithm
        self._active_pairs: Dict[str, OCOPair] = {}  # oco_id -> OCOPair
        self._symbol_to_oco: Dict[str, str] = {}  # symbol -> oco_id
        self._order_to_oco: Dict[int, str] = {}  # broker_order_id -> oco_id
        self._next_oco_number: int = 1
        self._next_mock_order_id: int = 1  # For testing without algorithm

    def log(self, message: str) -> None:
        """Log via algorithm or skip for testing."""
        if self.algorithm:
            self.algorithm.Log(message)

    def create_oco_pair(
        self,
        symbol: str,
        entry_price: float,
        stop_price: float,
        target_price: float,
        quantity: int,
        current_date: str,
    ) -> Optional[OCOPair]:
        """
        Create a new OCO order pair.

        Args:
            symbol: Option symbol.
            entry_price: Fill price of the entry.
            stop_price: Stop loss trigger price.
            target_price: Profit target trigger price.
            quantity: Number of contracts (positive).
            current_date: Date for ID generation.

        Returns:
            Created OCOPair (in PENDING state), or None if validation fails.
        """
        # T-13 FIX: Validate stop_price > 0 before creating OCO pair
        # V6.13: 10 StopMarket orders with Price=0 caused risk mgmt to be disabled
        if stop_price <= 0:
            self.log(
                f"OCO: REJECTED - Invalid stop_price={stop_price:.2f} <= 0 | "
                f"Symbol={symbol} | Entry=${entry_price:.2f}"
            )
            return None

        if target_price <= 0:
            self.log(
                f"OCO: REJECTED - Invalid target_price={target_price:.2f} <= 0 | "
                f"Symbol={symbol} | Entry=${entry_price:.2f}"
            )
            return None

        if entry_price <= 0:
            self.log(
                f"OCO: REJECTED - Invalid entry_price={entry_price:.2f} <= 0 | " f"Symbol={symbol}"
            )
            return None

        if quantity <= 0:
            self.log(f"OCO: REJECTED - Invalid quantity={quantity} <= 0 | " f"Symbol={symbol}")
            return None

        # Generate unique OCO ID
        oco_id = f"OCO-{current_date.replace('-', '')}-{self._next_oco_number:03d}"
        self._next_oco_number += 1

        # Create stop leg (sell to close)
        stop_leg = OCOLeg(
            leg_type=OCOOrderType.STOP,
            trigger_price=stop_price,
            quantity=-quantity,  # Negative = sell
        )

        # Create profit leg (sell to close)
        profit_leg = OCOLeg(
            leg_type=OCOOrderType.PROFIT,
            trigger_price=target_price,
            quantity=-quantity,  # Negative = sell
        )

        # Create OCO pair
        pair = OCOPair(
            oco_id=oco_id,
            symbol=symbol,
            entry_price=entry_price,
            stop_leg=stop_leg,
            profit_leg=profit_leg,
            state=OCOState.PENDING,
            created_at=current_date,
        )

        self.log(
            f"OCO: CREATED {oco_id} | {symbol} | "
            f"Stop=${stop_price:.2f} | Target=${target_price:.2f} | "
            f"Qty={quantity}"
        )

        return pair

    def submit_oco_pair(
        self,
        pair: OCOPair,
        current_time: str,
    ) -> bool:
        """
        Submit both legs of an OCO pair to the broker.

        Args:
            pair: OCOPair to submit.
            current_time: Current timestamp for logging.

        Returns:
            True if both legs submitted successfully.
        """
        if pair.state != OCOState.PENDING:
            self.log(f"OCO: Cannot submit {pair.oco_id} - state is {pair.state.value}")
            return False

        # V6.8: Market hours guard - block OCO submission outside regular trading hours
        if self.algorithm is not None:
            try:
                # Get the equity symbol for market hours check (options follow equity hours)
                underlying = pair.symbol.split()[0] if " " in str(pair.symbol) else str(pair.symbol)
                equity_symbol = self.algorithm.Symbol(underlying)
                if not self.algorithm.Securities[equity_symbol].Exchange.ExchangeOpen:
                    self.log(f"OCO: BLOCKED {pair.oco_id} - market closed for {underlying}")
                    return False
            except Exception as e:
                # If we can't check market hours, log warning but allow submission
                self.log(f"OCO: WARNING - could not verify market hours: {e}")

        # Submit stop order
        stop_order_id = self._submit_stop_order(pair.symbol, pair.stop_leg, pair.oco_id)
        if stop_order_id is None:
            self.log(f"OCO: Failed to submit stop leg for {pair.oco_id}")
            return False
        pair.stop_leg.broker_order_id = stop_order_id
        pair.stop_leg.submitted = True

        # Submit profit order
        profit_order_id = self._submit_limit_order(pair.symbol, pair.profit_leg, pair.oco_id)
        if profit_order_id is None:
            # Cancel the stop order since profit failed
            self._cancel_order(stop_order_id)
            pair.stop_leg.cancelled = True
            self.log(f"OCO: Failed to submit profit leg for {pair.oco_id}, cancelled stop")
            return False
        pair.profit_leg.broker_order_id = profit_order_id
        pair.profit_leg.submitted = True

        # Update state and tracking
        pair.state = OCOState.ACTIVE
        pair.activated_at = current_time
        self._active_pairs[pair.oco_id] = pair
        self._symbol_to_oco[pair.symbol] = pair.oco_id
        self._order_to_oco[stop_order_id] = pair.oco_id
        self._order_to_oco[profit_order_id] = pair.oco_id

        self.log(
            f"OCO: ACTIVATED {pair.oco_id} | {pair.symbol} | "
            f"Stop Order #{stop_order_id} | Profit Order #{profit_order_id}"
        )

        return True

    def _submit_stop_order(self, symbol: str, leg: OCOLeg, oco_id: str) -> Optional[int]:
        """
        Submit a stop order to the broker.

        Returns broker order ID or None if failed.
        """
        if self.algorithm is None:
            # For testing, return a unique mock order ID
            order_id = 10000 + self._next_mock_order_id
            self._next_mock_order_id += 1
            return order_id

        try:
            # Use StopMarketOrder for options
            tag = f"OCO_STOP:{oco_id}"
            try:
                ticket = self.algorithm.StopMarketOrder(
                    symbol,
                    leg.quantity,
                    leg.trigger_price,
                    tag=tag,
                )
            except TypeError:
                ticket = self.algorithm.StopMarketOrder(
                    symbol,
                    leg.quantity,
                    leg.trigger_price,
                )
            return ticket.OrderId
        except Exception as e:
            self.log(f"OCO: Stop order submission failed: {e}")
            return None

    def _submit_limit_order(self, symbol: str, leg: OCOLeg, oco_id: str) -> Optional[int]:
        """
        Submit a limit order to the broker.

        Returns broker order ID or None if failed.
        """
        if self.algorithm is None:
            # For testing, return a unique mock order ID
            order_id = 20000 + self._next_mock_order_id
            self._next_mock_order_id += 1
            return order_id

        try:
            # Use LimitOrder for profit target
            tag = f"OCO_PROFIT:{oco_id}"
            try:
                ticket = self.algorithm.LimitOrder(
                    symbol,
                    leg.quantity,
                    leg.trigger_price,
                    tag=tag,
                )
            except TypeError:
                ticket = self.algorithm.LimitOrder(
                    symbol,
                    leg.quantity,
                    leg.trigger_price,
                )
            return ticket.OrderId
        except Exception as e:
            self.log(f"OCO: Limit order submission failed: {e}")
            return None

    def _cancel_order(self, order_id: int) -> bool:
        """Cancel an order by broker order ID."""
        if self.algorithm is None:
            return True  # For testing

        try:
            self.algorithm.Transactions.CancelOrder(order_id)
            return True
        except Exception as e:
            self.log(f"OCO: Failed to cancel order #{order_id}: {e}")
            return False

    def on_order_fill(
        self,
        broker_order_id: int,
        fill_price: float,
        fill_quantity: int,
        fill_time: str,
    ) -> Optional[OCOPair]:
        """
        Handle order fill event - cancel the other leg.

        Args:
            broker_order_id: ID of the filled order.
            fill_price: Execution price.
            fill_quantity: Executed quantity.
            fill_time: Fill timestamp.

        Returns:
            OCOPair if this was part of an OCO, else None.
        """
        # Check if this order is part of an OCO pair
        if broker_order_id not in self._order_to_oco:
            return None

        oco_id = self._order_to_oco[broker_order_id]
        pair = self._active_pairs.get(oco_id)

        if pair is None:
            return None

        # Determine which leg filled
        if pair.stop_leg.broker_order_id == broker_order_id:
            # Stop filled - cancel profit
            pair.stop_leg.filled = True
            pair.stop_leg.fill_price = fill_price
            pair.stop_leg.fill_time = fill_time
            pair.state = OCOState.STOP_TRIGGERED
            pair.close_reason = f"STOP_HIT at ${fill_price:.2f}"

            # Cancel profit leg
            if pair.profit_leg.broker_order_id:
                self._cancel_order(pair.profit_leg.broker_order_id)
                pair.profit_leg.cancelled = True

            self.log(
                f"OCO: STOP_TRIGGERED {oco_id} | {pair.symbol} | "
                f"Fill=${fill_price:.2f} | Profit order cancelled"
            )

        elif pair.profit_leg.broker_order_id == broker_order_id:
            # Profit filled - cancel stop
            pair.profit_leg.filled = True
            pair.profit_leg.fill_price = fill_price
            pair.profit_leg.fill_time = fill_time
            pair.state = OCOState.PROFIT_TRIGGERED
            pair.close_reason = f"PROFIT_HIT at ${fill_price:.2f}"

            # Cancel stop leg
            if pair.stop_leg.broker_order_id:
                self._cancel_order(pair.stop_leg.broker_order_id)
                pair.stop_leg.cancelled = True

            self.log(
                f"OCO: PROFIT_TRIGGERED {oco_id} | {pair.symbol} | "
                f"Fill=${fill_price:.2f} | Stop order cancelled"
            )

        # Mark as closed
        pair.state = OCOState.CLOSED
        pair.closed_at = fill_time

        # Clean up tracking
        self._cleanup_pair(pair)

        return pair

    def on_order_inactive(
        self,
        broker_order_id: int,
        status: str,
        detail: str,
        event_time: str,
    ) -> Optional[OCOPair]:
        """
        Handle broker invalid/canceled events for an OCO leg.

        If one leg becomes inactive unexpectedly, cancel the sibling leg and
        close the OCO pair to avoid stale orphan orders.
        """
        if broker_order_id not in self._order_to_oco:
            return None

        oco_id = self._order_to_oco[broker_order_id]
        pair = self._active_pairs.get(oco_id)
        if pair is None:
            return None

        if pair.state != OCOState.ACTIVE:
            # Already terminal; just clean stale mappings if any.
            self._cleanup_pair(pair)
            return pair

        leg_name = "UNKNOWN"
        sibling_id: Optional[int] = None
        if pair.stop_leg.broker_order_id == broker_order_id:
            pair.stop_leg.cancelled = True
            leg_name = "STOP"
            sibling_id = pair.profit_leg.broker_order_id
        elif pair.profit_leg.broker_order_id == broker_order_id:
            pair.profit_leg.cancelled = True
            leg_name = "PROFIT"
            sibling_id = pair.stop_leg.broker_order_id

        if sibling_id:
            self._cancel_order(sibling_id)

        pair.state = OCOState.CANCELLED
        pair.closed_at = event_time
        pair.close_reason = f"{status.upper()}_{leg_name}"

        self.log(
            f"OCO: CLOSED {oco_id} due to {status.upper()} on {leg_name} leg | "
            f"Symbol={pair.symbol} | Detail={detail}"
        )
        self._cleanup_pair(pair)
        return pair

    def cancel_oco_pair(self, oco_id: str, reason: str = "MANUAL") -> bool:
        """
        Cancel an active OCO pair.

        Args:
            oco_id: ID of the OCO pair to cancel.
            reason: Reason for cancellation.

        Returns:
            True if cancelled successfully.
        """
        pair = self._active_pairs.get(oco_id)
        if pair is None:
            return False

        if pair.state != OCOState.ACTIVE:
            self.log(f"OCO: Cannot cancel {oco_id} - state is {pair.state.value}")
            return False

        # Cancel both legs
        cancelled_stop = True
        cancelled_profit = True

        if pair.stop_leg.broker_order_id and not pair.stop_leg.filled:
            cancelled_stop = self._cancel_order(pair.stop_leg.broker_order_id)
            pair.stop_leg.cancelled = cancelled_stop

        if pair.profit_leg.broker_order_id and not pair.profit_leg.filled:
            cancelled_profit = self._cancel_order(pair.profit_leg.broker_order_id)
            pair.profit_leg.cancelled = cancelled_profit

        pair.state = OCOState.CANCELLED
        pair.close_reason = reason

        self.log(f"OCO: CANCELLED {oco_id} | Reason: {reason}")

        # Clean up tracking
        self._cleanup_pair(pair)

        return cancelled_stop and cancelled_profit

    def cancel_by_symbol(self, symbol: str, reason: str = "POSITION_CLOSED") -> bool:
        """
        Cancel OCO pair by symbol.

        Args:
            symbol: Option symbol.
            reason: Reason for cancellation.

        Returns:
            True if found and cancelled.
        """
        oco_id = self._symbol_to_oco.get(symbol)
        if oco_id is None:
            return False
        return self.cancel_oco_pair(oco_id, reason)

    def _cleanup_pair(self, pair: OCOPair) -> None:
        """Remove pair from active tracking."""
        if pair.oco_id in self._active_pairs:
            del self._active_pairs[pair.oco_id]

        if pair.symbol in self._symbol_to_oco:
            del self._symbol_to_oco[pair.symbol]

        if pair.stop_leg.broker_order_id in self._order_to_oco:
            del self._order_to_oco[pair.stop_leg.broker_order_id]

        if pair.profit_leg.broker_order_id in self._order_to_oco:
            del self._order_to_oco[pair.profit_leg.broker_order_id]

    def get_active_pair(self, symbol: str) -> Optional[OCOPair]:
        """Get active OCO pair for a symbol."""
        oco_id = self._symbol_to_oco.get(symbol)
        if oco_id:
            return self._active_pairs.get(oco_id)
        return None

    def has_active_pair(self, symbol: str) -> bool:
        """Check if symbol has an active OCO pair."""
        return symbol in self._symbol_to_oco

    def has_order(self, broker_order_id: int) -> bool:
        """Check if a broker order ID is part of any OCO pair."""
        return broker_order_id in self._order_to_oco

    def get_all_active_pairs(self) -> List[OCOPair]:
        """Get all active OCO pairs."""
        return list(self._active_pairs.values())

    # =========================================================================
    # STATE PERSISTENCE
    # =========================================================================

    def get_state_for_persistence(self) -> Dict[str, Any]:
        """Get state for ObjectStore."""
        return {
            "active_pairs": {oco_id: pair.to_dict() for oco_id, pair in self._active_pairs.items()},
            "next_oco_number": self._next_oco_number,
        }

    def restore_state(self, state: Dict[str, Any]) -> None:
        """
        Restore state from ObjectStore.

        CRITICAL FIX: Broker order IDs are SESSION-SPECIFIC and invalid after restart.
        We restore the OCO pairs but NOT the broker_order_id mappings.
        The orders will need to be re-placed or the pairs marked as stale.

        After restart, any pending OCO orders from previous session are likely
        cancelled by the broker. We restore pair metadata for position tracking
        but clear order mappings since they won't match new session IDs.
        """
        self._next_oco_number = state.get("next_oco_number", 1)

        # CRITICAL: Clear order-to-OCO mappings - broker IDs are session-specific
        # Previous session's broker_order_ids will NOT match this session's IDs
        self._order_to_oco.clear()

        pairs_data = state.get("active_pairs", {})
        restored_pairs = 0
        stale_pairs = 0

        for oco_id, pair_data in pairs_data.items():
            pair = OCOPair.from_dict(pair_data)

            # CRITICAL: Clear broker_order_ids as they're invalid after restart
            # The broker assigns NEW IDs each session
            if pair.stop_leg.broker_order_id or pair.profit_leg.broker_order_id:
                pair.stop_leg.broker_order_id = None
                pair.profit_leg.broker_order_id = None
                stale_pairs += 1

            self._active_pairs[oco_id] = pair
            self._symbol_to_oco[pair.symbol] = oco_id
            restored_pairs += 1

        if stale_pairs > 0:
            self.log(
                f"OCO: WARNING - {stale_pairs} pairs had stale broker IDs cleared. "
                f"Orders may need to be re-placed."
            )

        self.log(f"OCO: Restored {restored_pairs} pairs (broker IDs cleared for new session)")

    def reset(self) -> None:
        """Reset manager state."""
        self._active_pairs.clear()
        self._symbol_to_oco.clear()
        self._order_to_oco.clear()
        self._next_oco_number = 1
        self._next_mock_order_id = 1
        self.log("OCO: Manager reset")
