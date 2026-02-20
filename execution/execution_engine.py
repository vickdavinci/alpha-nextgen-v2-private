"""
Execution Engine - Translates validated orders into broker transactions.

Handles:
- Market order submission (IMMEDIATE urgency)
- MOO order submission (EOD urgency, submitted at 15:45)
- MOO fallback logic (check at 09:31, fallback to market if rejected)
- Order state tracking
- Fill processing
- Order tagging for analysis

Design Principle: Reliability over optimization
- Market orders guarantee fills in liquid ETFs
- MOO orders execute at opening auction price
- No limit orders (avoid non-fill risk)
- No automatic retry on rejection

Spec: docs/13-execution-engine.md
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from AlgorithmImports import QCAlgorithm

import config
from models.enums import Urgency


class OrderState(Enum):
    """
    Order state machine states.

    State Transitions:
    PENDING -> SUBMITTED (on submit to broker)
    PENDING -> CANCELLED (cancel before submit)
    SUBMITTED -> PARTIALLY_FILLED (partial fill)
    SUBMITTED -> FILLED (complete fill)
    SUBMITTED -> REJECTED (broker rejects)
    SUBMITTED -> CANCELLED (cancel request)
    PARTIALLY_FILLED -> FILLED (remaining filled)
    PARTIALLY_FILLED -> CANCELLED (cancel remaining)
    """

    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class OrderType(Enum):
    """Order type for execution."""

    MARKET = "MARKET"  # For IMMEDIATE urgency
    MOO = "MOO"  # Market-on-Open for EOD urgency


@dataclass
class OrderRecord:
    """
    Tracks a single order through its lifecycle.

    Attributes:
        order_id: Unique identifier (format: ORD-YYYYMMDD-NNN).
        symbol: Instrument ticker.
        quantity: Number of shares (positive=buy, negative=sell).
        order_type: MARKET or MOO.
        state: Current state in state machine.
        strategy: Source engine (TREND, MR, HEDGE, etc.).
        signal_type: ENTRY, EXIT_STOP, EXIT_TARGET, etc.
        reason: Human-readable reason.
        created_at: When order was created.
        submitted_at: When order was submitted to broker.
        filled_at: When order was filled.
        fill_price: Execution price (if filled).
        fill_quantity: Executed quantity (if filled).
        broker_order_id: ID from broker (ticket number).
        rejection_reason: Reason for rejection (if rejected).
    """

    order_id: str
    symbol: str
    quantity: int
    order_type: OrderType
    state: OrderState = OrderState.PENDING
    strategy: str = ""
    signal_type: str = ""
    reason: str = ""
    created_at: Optional[datetime] = None
    submitted_at: Optional[datetime] = None
    filled_at: Optional[datetime] = None
    fill_price: float = 0.0
    fill_quantity: int = 0
    broker_order_id: Optional[int] = None
    rejection_reason: str = ""

    def is_terminal(self) -> bool:
        """Check if order is in a terminal state."""
        return self.state in (OrderState.FILLED, OrderState.REJECTED, OrderState.CANCELLED)

    def is_buy(self) -> bool:
        """Check if this is a buy order."""
        return self.quantity > 0

    def get_side(self) -> str:
        """Get order side as string."""
        return "BUY" if self.quantity > 0 else "SELL"

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for logging and persistence."""
        return {
            "order_id": self.order_id,
            "symbol": self.symbol,
            "quantity": self.quantity,
            "order_type": self.order_type.value,
            "state": self.state.value,
            "strategy": self.strategy,
            "signal_type": self.signal_type,
            "reason": self.reason,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "submitted_at": self.submitted_at.isoformat() if self.submitted_at else None,
            "filled_at": self.filled_at.isoformat() if self.filled_at else None,
            "fill_price": self.fill_price,
            "fill_quantity": self.fill_quantity,
            "broker_order_id": self.broker_order_id,
            "rejection_reason": self.rejection_reason,
        }


@dataclass
class ExecutionResult:
    """
    Result of an order execution attempt.

    Attributes:
        order_id: Order identifier.
        success: Whether submission succeeded.
        state: Current order state.
        fill_price: Fill price if filled.
        fill_quantity: Fill quantity if filled.
        error_message: Error message if failed.
    """

    order_id: str
    success: bool
    state: OrderState
    fill_price: float = 0.0
    fill_quantity: int = 0
    error_message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for logging."""
        return {
            "order_id": self.order_id,
            "success": self.success,
            "state": self.state.value,
            "fill_price": self.fill_price,
            "fill_quantity": self.fill_quantity,
            "error_message": self.error_message,
        }


class ExecutionEngine:
    """
    Translates validated orders from Portfolio Router into broker transactions.

    Key Responsibilities:
    1. Submit market orders for IMMEDIATE urgency
    2. Queue and submit MOO orders for EOD urgency
    3. Track order state through lifecycle
    4. Handle MOO fallback at 09:31
    5. Process fill events
    6. Support kill switch liquidation

    Design Principles:
    - Reliability over optimization
    - No automatic retry on rejection
    - Complete logging for audit trail
    """

    def __init__(self, algorithm: Optional["QCAlgorithm"] = None):
        """
        Initialize ExecutionEngine.

        Args:
            algorithm: QuantConnect algorithm instance (None for testing).
        """
        self.algorithm = algorithm
        self._order_counter = 0
        self._orders: Dict[str, OrderRecord] = {}  # order_id -> OrderRecord
        self._broker_order_map: Dict[int, str] = {}  # broker_order_id -> order_id
        self._pending_moo_orders: List[str] = []  # order_ids of MOO orders awaiting submission
        self._moo_fallback_queue: List[str] = []  # order_ids of MOO orders that need fallback

    def log(self, message: str) -> None:
        """Log via algorithm or skip for testing."""
        if self.algorithm:
            self.algorithm.Log(message)  # type: ignore[attr-defined]

    def _get_time(self) -> Optional[datetime]:
        """Get current time from algorithm.

        Returns:
            Current algorithm time, or None in testing mode.
            In testing mode, timestamps are not tracked.
        """
        if self.algorithm:
            return self.algorithm.Time  # type: ignore[attr-defined]
        return None

    def _generate_order_id(self) -> str:
        """Generate unique order ID."""
        self._order_counter += 1
        current_time = self._get_time()
        date_str = current_time.strftime("%Y%m%d") if current_time else "TEST"
        return f"ORD-{date_str}-{self._order_counter:03d}"

    # =========================================================================
    # Order Creation
    # =========================================================================

    def create_order(
        self,
        symbol: str,
        quantity: int,
        order_type: OrderType,
        strategy: str = "",
        signal_type: str = "",
        reason: str = "",
    ) -> OrderRecord:
        """
        Create a new order record.

        Args:
            symbol: Instrument ticker.
            quantity: Number of shares (positive=buy, negative=sell).
            order_type: MARKET or MOO.
            strategy: Source engine.
            signal_type: ENTRY, EXIT_STOP, EXIT_TARGET, etc.
            reason: Human-readable reason.

        Returns:
            Created OrderRecord in PENDING state.
        """
        order_id = self._generate_order_id()
        order = OrderRecord(
            order_id=order_id,
            symbol=symbol,
            quantity=quantity,
            order_type=order_type,
            state=OrderState.PENDING,
            strategy=strategy,
            signal_type=signal_type,
            reason=reason,
            created_at=self._get_time(),
        )
        self._orders[order_id] = order

        self.log(
            f"EXEC: ORDER_CREATED | {order_id} | {order.get_side()} {abs(quantity)} {symbol} | "
            f"Type={order_type.value} | Strategy={strategy}"
        )

        return order

    # =========================================================================
    # Market Order Submission
    # =========================================================================

    def submit_market_order(
        self,
        symbol: str,
        quantity: int,
        strategy: str = "",
        signal_type: str = "",
        reason: str = "",
    ) -> ExecutionResult:
        """
        Submit a market order for immediate execution.

        Used for:
        - Mean reversion entries/exits
        - Stop loss exits
        - Kill switch/panic mode liquidations
        - Cold start warm entries

        Args:
            symbol: Instrument ticker.
            quantity: Number of shares (positive=buy, negative=sell).
            strategy: Source engine.
            signal_type: ENTRY, EXIT, etc.
            reason: Human-readable reason.

        Returns:
            ExecutionResult with submission status.
        """
        # Validate quantity
        if quantity == 0:
            self.log(f"EXEC: REJECTED | {symbol} | Zero quantity")
            return ExecutionResult(
                order_id="",
                success=False,
                state=OrderState.REJECTED,
                error_message="Zero quantity not allowed",
            )

        # Create order record
        order = self.create_order(
            symbol=symbol,
            quantity=quantity,
            order_type=OrderType.MARKET,
            strategy=strategy,
            signal_type=signal_type,
            reason=reason,
        )

        # Submit to broker
        return self._submit_to_broker(order)

    def _validate_order_pre_submit(self, order: OrderRecord) -> Optional[str]:
        """
        T-16 FIX: Pre-submission order validation to reduce failure rate.

        V6.14: 27.6% order failure rate traced to invalid orders reaching broker.
        Validates order before submission to catch common issues early.

        Args:
            order: OrderRecord to validate.

        Returns:
            Error message if validation fails, None if valid.
        """
        # Validate quantity is not zero
        if order.quantity == 0:
            return "Zero quantity"

        # Validate symbol is not empty
        if not order.symbol or order.symbol.strip() == "":
            return "Empty symbol"

        # T-16: Check if security exists and is tradeable (for live algo only)
        if self.algorithm:
            try:
                # Check if symbol exists in securities
                if order.symbol not in self.algorithm.Securities:
                    return f"Symbol {order.symbol} not in Securities"

                security = self.algorithm.Securities[order.symbol]

                # Check if security has valid price (not $0)
                if security.Price <= 0:
                    return f"Invalid price ${security.Price:.2f} for {order.symbol}"

                # For options, validate the option hasn't expired
                if hasattr(security, "Expiry"):
                    if security.Expiry < self.algorithm.Time:
                        return f"Option {order.symbol} has expired"

                # Check if market is open for market orders
                if order.order_type == OrderType.MARKET:
                    if hasattr(security, "Exchange") and not security.Exchange.ExchangeOpen:
                        return f"Market closed for {order.symbol}"

            except Exception as e:
                # Log but don't block - let broker handle edge cases
                self.log(f"EXEC: PRE_VALIDATE_WARN | {order.order_id} | {e}")

        return None  # Validation passed

    def _submit_to_broker(self, order: OrderRecord) -> ExecutionResult:
        """
        Submit order to broker.

        Args:
            order: OrderRecord to submit.

        Returns:
            ExecutionResult with submission status.
        """
        if not self.algorithm:
            # Testing mode - simulate success
            order.state = OrderState.SUBMITTED
            order.submitted_at = self._get_time()
            return ExecutionResult(
                order_id=order.order_id,
                success=True,
                state=OrderState.SUBMITTED,
            )

        # T-16 FIX: Pre-submission validation
        validation_error = self._validate_order_pre_submit(order)
        if validation_error:
            order.state = OrderState.REJECTED
            order.rejection_reason = f"PRE_VALIDATE: {validation_error}"
            self.log(
                f"EXEC: PRE_VALIDATE_REJECT | {order.order_id} | {order.symbol} | {validation_error}"
            )
            return ExecutionResult(
                order_id=order.order_id,
                success=False,
                state=OrderState.REJECTED,
                error_message=validation_error,
            )

        try:
            order.submitted_at = self._get_time()

            tag = f"EXEC:{order.order_id}|{order.get_side()}|{order.symbol}"
            if order.order_type == OrderType.MARKET:
                try:
                    ticket = self.algorithm.MarketOrder(  # type: ignore[attr-defined]
                        order.symbol, order.quantity, tag=tag
                    )
                except TypeError:
                    try:
                        ticket = self.algorithm.MarketOrder(  # type: ignore[attr-defined]
                            order.symbol, order.quantity, tag
                        )
                    except TypeError:
                        ticket = self.algorithm.MarketOrder(  # type: ignore[attr-defined]
                            order.symbol, order.quantity
                        )
            else:
                try:
                    ticket = self.algorithm.MarketOnOpenOrder(  # type: ignore[attr-defined]
                        order.symbol, order.quantity, tag=tag
                    )
                except TypeError:
                    try:
                        ticket = self.algorithm.MarketOnOpenOrder(  # type: ignore[attr-defined]
                            order.symbol, order.quantity, tag
                        )
                    except TypeError:
                        ticket = self.algorithm.MarketOnOpenOrder(  # type: ignore[attr-defined]
                            order.symbol, order.quantity
                        )

            # Store broker order ID mapping
            broker_id = ticket.OrderId  # type: ignore[attr-defined]
            order.broker_order_id = broker_id
            self._broker_order_map[broker_id] = order.order_id
            order.state = OrderState.SUBMITTED

            self.log(
                f"EXEC: SUBMITTED | {order.order_id} | BrokerID={broker_id} | "
                f"{order.get_side()} {abs(order.quantity)} {order.symbol}"
            )

            return ExecutionResult(
                order_id=order.order_id,
                success=True,
                state=OrderState.SUBMITTED,
            )

        except Exception as e:
            order.state = OrderState.REJECTED
            order.rejection_reason = str(e)

            self.log(f"EXEC: SUBMIT_ERROR | {order.order_id} | {order.symbol} | {e}")

            return ExecutionResult(
                order_id=order.order_id,
                success=False,
                state=OrderState.REJECTED,
                error_message=str(e),
            )

    # =========================================================================
    # V2.3.9: Combo Order Handling (Spread Orders)
    # =========================================================================

    def submit_combo_order(
        self,
        long_symbol: str,
        long_quantity: int,
        short_symbol: str,
        short_quantity: int,
        strategy: str = "",
        signal_type: str = "",
        reason: str = "",
    ) -> ExecutionResult:
        """
        V2.3.9: Submit a combo market order for spread trades.

        Uses ComboMarketOrder to submit both legs atomically. This ensures
        the broker calculates multi-leg margin (~$42K) instead of naked
        short margin (~$729K).

        Per CTA guidance: "Using Combo Orders will automatically calculate
        the multi-leg margin instead of classical one."

        Args:
            long_symbol: Long leg contract symbol (BUY).
            long_quantity: Number of long contracts (positive).
            short_symbol: Short leg contract symbol (SELL).
            short_quantity: Number of short contracts (negative).
            strategy: Source engine.
            signal_type: ENTRY, EXIT, etc.
            reason: Human-readable reason.

        Returns:
            ExecutionResult with submission status.
        """
        # Validate quantities
        if long_quantity <= 0:
            self.log(f"EXEC: COMBO_REJECTED | Long quantity must be positive: {long_quantity}")
            return ExecutionResult(
                order_id="",
                success=False,
                state=OrderState.REJECTED,
                error_message="Long quantity must be positive",
            )

        if short_quantity >= 0:
            self.log(f"EXEC: COMBO_REJECTED | Short quantity must be negative: {short_quantity}")
            return ExecutionResult(
                order_id="",
                success=False,
                state=OrderState.REJECTED,
                error_message="Short quantity must be negative",
            )

        # Create order record for tracking (combo orders are logged as single unit)
        order_id = self._generate_order_id()
        combo_reason = f"SPREAD: {long_symbol}(+{long_quantity}) / {short_symbol}({short_quantity})"

        if not self.algorithm:
            # Testing mode - simulate success
            self.log(f"EXEC: COMBO_SUBMITTED (test) | {order_id} | {combo_reason} | {reason}")
            return ExecutionResult(
                order_id=order_id,
                success=True,
                state=OrderState.SUBMITTED,
            )

        try:
            # Import Leg class for combo orders
            from AlgorithmImports import Leg

            # V2.4.1 FIX: Leg.Create takes RATIO, not absolute quantity
            # For a standard 1:1 spread:
            #   - Long leg ratio = 1 (BUY 1 per spread)
            #   - Short leg ratio = -1 (SELL 1 per spread)
            #   - ComboMarketOrder quantity = number of spreads
            # OLD BUG: Passing quantity (e.g., 26) as ratio meant 26 × 26 = 676 contracts!
            num_spreads = abs(long_quantity)
            long_ratio = 1 if long_quantity > 0 else -1
            # V2.10 FIX (Pitfall #3): Short leg ratio must ALWAYS be opposite of long leg
            # OLD BUG: `short_ratio = 1 if short_quantity > 0 else -1` failed when both
            # quantities had the same sign (e.g., both negative), creating naked positions
            # instead of proper spreads. A spread by definition has one BUY and one SELL.
            short_ratio = -long_ratio

            legs = [
                Leg.Create(long_symbol, long_ratio),
                Leg.Create(short_symbol, short_ratio),
            ]

            # Submit combo order - broker calculates NET margin (spread margin)
            tickets = self.algorithm.ComboMarketOrder(legs, num_spreads)

            # Log success
            self.log(
                f"EXEC: COMBO_SUBMITTED | {order_id} | "
                f"Long {long_symbol} x{num_spreads} (ratio={long_ratio}) + "
                f"Short {short_symbol} x{num_spreads} (ratio={short_ratio}) | "
                f"{reason}"
            )

            return ExecutionResult(
                order_id=order_id,
                success=True,
                state=OrderState.SUBMITTED,
            )

        except Exception as e:
            self.log(f"EXEC: COMBO_ERROR | {order_id} | {combo_reason} | {e}")

            return ExecutionResult(
                order_id=order_id,
                success=False,
                state=OrderState.REJECTED,
                error_message=str(e),
            )

    # =========================================================================
    # MOO Order Handling
    # =========================================================================

    def queue_moo_order(
        self,
        symbol: str,
        quantity: int,
        strategy: str = "",
        signal_type: str = "",
        reason: str = "",
    ) -> str:
        """
        Queue a MOO order for submission at 15:45.

        MOO orders are queued during the day and submitted at 15:45 ET
        for execution at the next day's open.

        Args:
            symbol: Instrument ticker.
            quantity: Number of shares (positive=buy, negative=sell).
            strategy: Source engine.
            signal_type: ENTRY, EXIT, etc.
            reason: Human-readable reason.

        Returns:
            Order ID for tracking.
        """
        if quantity == 0:
            self.log(f"EXEC: MOO_REJECTED | {symbol} | Zero quantity")
            return ""

        order = self.create_order(
            symbol=symbol,
            quantity=quantity,
            order_type=OrderType.MOO,
            strategy=strategy,
            signal_type=signal_type,
            reason=reason,
        )

        self._pending_moo_orders.append(order.order_id)

        self.log(
            f"EXEC: MOO_QUEUED | {order.order_id} | "
            f"{order.get_side()} {abs(quantity)} {symbol} | Pending submission at 15:45"
        )

        return order.order_id

    def submit_pending_moo_orders(self) -> List[ExecutionResult]:
        """
        Submit all pending MOO orders.

        Called at 15:45 ET to submit queued MOO orders for next day's open.

        Returns:
            List of ExecutionResult for each order.
        """
        results: List[ExecutionResult] = []

        if not self._pending_moo_orders:
            self.log("EXEC: MOO_SUBMIT | No pending MOO orders")
            return results

        self.log(f"EXEC: MOO_SUBMIT | Submitting {len(self._pending_moo_orders)} MOO orders")

        for order_id in self._pending_moo_orders.copy():
            order = self._orders.get(order_id)
            if not order:
                continue

            result = self._submit_to_broker(order)
            results.append(result)

            # If submission failed, queue for fallback
            if not result.success:
                self._moo_fallback_queue.append(order_id)

        # Clear pending queue
        self._pending_moo_orders.clear()

        return results

    def check_moo_fallbacks(self) -> List[ExecutionResult]:
        """
        Check MOO orders and execute fallbacks if needed.

        Called at 09:31 ET to verify MOO fills and submit fallback
        market orders for any that failed.

        Returns:
            List of ExecutionResult for fallback orders.
        """
        results: List[ExecutionResult] = []

        if not self._moo_fallback_queue:
            self.log("EXEC: MOO_FALLBACK | No orders needing fallback")
            return results

        self.log(f"EXEC: MOO_FALLBACK | Checking {len(self._moo_fallback_queue)} orders")

        for order_id in self._moo_fallback_queue.copy():
            order = self._orders.get(order_id)
            if not order:
                continue

            # Check if order filled (might have recovered)
            if order.state == OrderState.FILLED:
                self.log(f"EXEC: MOO_FALLBACK | {order_id} | Already filled, no fallback needed")
                continue

            # Check if order was rejected or is still pending
            if order.state in (OrderState.REJECTED, OrderState.PENDING, OrderState.CANCELLED):
                self.log(
                    f"EXEC: MOO_FALLBACK | {order_id} | "
                    f"State={order.state.value} | Executing market fallback"
                )

                # Submit fallback market order
                result = self.submit_market_order(
                    symbol=order.symbol,
                    quantity=order.quantity,
                    strategy=order.strategy,
                    signal_type=f"FALLBACK_{order.signal_type}",
                    reason=f"MOO fallback: {order.reason}",
                )
                results.append(result)

        # Clear fallback queue
        self._moo_fallback_queue.clear()

        return results

    # =========================================================================
    # Fill Processing
    # =========================================================================

    def on_order_event(
        self,
        broker_order_id: int,
        status: str,
        fill_price: float = 0.0,
        fill_quantity: int = 0,
        rejection_reason: str = "",
    ) -> None:
        """
        Handle order event from broker.

        Called by algorithm's OnOrderEvent method.

        Args:
            broker_order_id: Broker's order ID.
            status: Order status string (Filled, PartiallyFilled, Rejected, etc.).
            fill_price: Fill price if filled.
            fill_quantity: Fill quantity if filled.
            rejection_reason: Reason if rejected.
        """
        order_id = self._broker_order_map.get(broker_order_id)
        if not order_id:
            self.log(f"EXEC: UNKNOWN_ORDER | BrokerID={broker_order_id}")
            return

        order = self._orders.get(order_id)
        if not order:
            return

        # Update order state based on status
        if status == "Filled":
            order.state = OrderState.FILLED
            order.filled_at = self._get_time()
            order.fill_price = fill_price
            order.fill_quantity = fill_quantity

            self.log(
                f"EXEC: FILLED | {order_id} | {order.symbol} | "
                f"Price={fill_price:.2f} | Qty={fill_quantity}"
            )

        elif status == "PartiallyFilled":
            order.state = OrderState.PARTIALLY_FILLED
            order.fill_price = fill_price  # Average price
            order.fill_quantity += fill_quantity  # Cumulative

            self.log(
                f"EXEC: PARTIAL_FILL | {order_id} | {order.symbol} | "
                f"Filled={order.fill_quantity}/{abs(order.quantity)}"
            )

        elif status == "Rejected" or status == "Invalid":
            order.state = OrderState.REJECTED
            order.rejection_reason = rejection_reason

            self.log(
                f"EXEC: REJECTED | {order_id} | {order.symbol} | " f"Reason={rejection_reason}"
            )

            # If this was a MOO order, add to fallback queue
            if order.order_type == OrderType.MOO:
                self._moo_fallback_queue.append(order_id)

        elif status == "Canceled":
            order.state = OrderState.CANCELLED

            self.log(f"EXEC: CANCELLED | {order_id} | {order.symbol}")

            # T-15 FIX: Immediate fallback for cancelled MOO orders
            # V6.14: Dec 27 MOO canceled caused -$37K loss because no fallback triggered
            # If it's a critical order (LIQUIDATION, ASSIGNMENT, or RISK), submit market order immediately
            if order.order_type == OrderType.MOO:
                critical_signals = ["LIQUIDATION", "ASSIGNMENT", "RISK", "FORCE_CLOSE"]
                is_critical = any(
                    sig in order.signal_type.upper() for sig in critical_signals
                ) or any(sig in order.reason.upper() for sig in critical_signals)
                if is_critical:
                    self.log(
                        f"EXEC: MOO_CRITICAL_FALLBACK | {order_id} | "
                        f"Cancelled MOO triggered immediate market fallback | "
                        f"Signal={order.signal_type} | Reason={order.reason}"
                    )
                    # Submit immediate market order as fallback
                    self.submit_market_order(
                        symbol=order.symbol,
                        quantity=order.quantity,
                        strategy=order.strategy,
                        signal_type=f"CRITICAL_FALLBACK_{order.signal_type}",
                        reason=f"Immediate fallback for cancelled MOO: {order.reason}",
                    )
                else:
                    # Non-critical MOO cancelled - add to fallback queue for 09:31 check
                    self._moo_fallback_queue.append(order_id)

    # =========================================================================
    # Kill Switch Support
    # =========================================================================

    def cancel_all_orders(self) -> int:
        """
        Cancel all pending and submitted orders.

        Called by kill switch to clear all orders before liquidation.

        Returns:
            Number of orders cancelled.
        """
        cancelled_count = 0

        if self.algorithm:
            # Cancel via broker
            try:
                open_orders = self.algorithm.Transactions.GetOpenOrders()  # type: ignore[attr-defined]
                for ticket in open_orders:
                    ticket.Cancel()
                    cancelled_count += 1
            except Exception as e:
                self.log(f"EXEC: CANCEL_ERROR | {e}")

        # Update internal state
        for order_id, order in self._orders.items():
            if not order.is_terminal():
                order.state = OrderState.CANCELLED
                cancelled_count += 1

        # Clear queues
        self._pending_moo_orders.clear()
        self._moo_fallback_queue.clear()

        self.log(f"EXEC: CANCEL_ALL | Cancelled {cancelled_count} orders")

        return cancelled_count

    def liquidate_all(
        self,
        positions: Dict[str, int],
        reason: str = "LIQUIDATION",
    ) -> List[ExecutionResult]:
        """
        Liquidate all positions.

        Called by kill switch or panic mode for emergency liquidation.

        Args:
            positions: Dict of symbol -> quantity held.
            reason: Reason for liquidation.

        Returns:
            List of ExecutionResult for liquidation orders.
        """
        results: List[ExecutionResult] = []

        # Cancel all pending orders first
        self.cancel_all_orders()

        # Submit sell orders for all positions
        for symbol, quantity in positions.items():
            if quantity == 0:
                continue

            # Sell entire position (negative quantity for sell)
            result = self.submit_market_order(
                symbol=symbol,
                quantity=-quantity,
                strategy="RISK",
                signal_type="LIQUIDATION",
                reason=reason,
            )
            results.append(result)

        self.log(f"EXEC: LIQUIDATE_ALL | Submitted {len(results)} liquidation orders")

        return results

    def liquidate_symbols(
        self,
        positions: Dict[str, int],
        symbols: List[str],
        reason: str = "LIQUIDATION",
    ) -> List[ExecutionResult]:
        """
        Liquidate specific symbols only.

        Called by panic mode to liquidate leveraged longs while keeping hedges.

        Args:
            positions: Dict of symbol -> quantity held.
            symbols: Symbols to liquidate.
            reason: Reason for liquidation.

        Returns:
            List of ExecutionResult for liquidation orders.
        """
        results: List[ExecutionResult] = []

        for symbol in symbols:
            quantity = positions.get(symbol, 0)
            if quantity == 0:
                continue

            result = self.submit_market_order(
                symbol=symbol,
                quantity=-quantity,
                strategy="RISK",
                signal_type="LIQUIDATION",
                reason=reason,
            )
            results.append(result)

        self.log(
            f"EXEC: LIQUIDATE_SYMBOLS | {symbols} | " f"Submitted {len(results)} liquidation orders"
        )

        return results

    # =========================================================================
    # Order Queries
    # =========================================================================

    def get_order(self, order_id: str) -> Optional[OrderRecord]:
        """Get order by ID."""
        return self._orders.get(order_id)

    def get_order_by_broker_id(self, broker_order_id: int) -> Optional[OrderRecord]:
        """Get order by broker order ID."""
        order_id = self._broker_order_map.get(broker_order_id)
        if order_id:
            return self._orders.get(order_id)
        return None

    def get_orders_by_state(self, state: OrderState) -> List[OrderRecord]:
        """Get all orders in a specific state."""
        return [o for o in self._orders.values() if o.state == state]

    def get_pending_orders(self) -> List[OrderRecord]:
        """Get all non-terminal orders."""
        return [o for o in self._orders.values() if not o.is_terminal()]

    def get_filled_orders(self) -> List[OrderRecord]:
        """Get all filled orders."""
        return [o for o in self._orders.values() if o.state == OrderState.FILLED]

    def get_orders_for_symbol(self, symbol: str) -> List[OrderRecord]:
        """Get all orders for a symbol."""
        return [o for o in self._orders.values() if o.symbol == symbol]

    def get_pending_moo_count(self) -> int:
        """Get count of MOO orders pending submission."""
        return len(self._pending_moo_orders)

    def get_fallback_queue_count(self) -> int:
        """Get count of MOO orders in fallback queue."""
        return len(self._moo_fallback_queue)

    # =========================================================================
    # State Management
    # =========================================================================

    def get_state_for_persistence(self) -> Dict[str, Any]:
        """Get state for ObjectStore persistence."""
        return {
            "order_counter": self._order_counter,
            "orders": {oid: o.to_dict() for oid, o in self._orders.items()},
            "pending_moo_orders": self._pending_moo_orders,
            "moo_fallback_queue": self._moo_fallback_queue,
        }

    def reset(self) -> None:
        """Reset engine state (e.g., after kill switch)."""
        self._order_counter = 0
        self._orders.clear()
        self._broker_order_map.clear()
        self._pending_moo_orders.clear()
        self._moo_fallback_queue.clear()
        self.log("EXEC: RESET")

    def reset_daily(self) -> None:
        """
        Reset for new trading day.

        Clears terminal orders from previous day while preserving
        order counter for unique IDs.
        """
        # Remove terminal orders from previous day
        terminal_ids = [oid for oid, order in self._orders.items() if order.is_terminal()]
        for oid in terminal_ids:
            order = self._orders.pop(oid)
            if order.broker_order_id:
                self._broker_order_map.pop(order.broker_order_id, None)

        self.log(f"EXEC: DAILY_RESET | Cleared {len(terminal_ids)} terminal orders")

    # =========================================================================
    # Statistics
    # =========================================================================

    def get_statistics(self) -> Dict[str, Any]:
        """Get execution statistics."""
        total = len(self._orders)
        filled = len([o for o in self._orders.values() if o.state == OrderState.FILLED])
        rejected = len([o for o in self._orders.values() if o.state == OrderState.REJECTED])
        cancelled = len([o for o in self._orders.values() if o.state == OrderState.CANCELLED])
        pending = len(self.get_pending_orders())

        return {
            "total_orders": total,
            "filled": filled,
            "rejected": rejected,
            "cancelled": cancelled,
            "pending": pending,
            "pending_moo": len(self._pending_moo_orders),
            "moo_fallback_queue": len(self._moo_fallback_queue),
            "fill_rate": filled / total if total > 0 else 0.0,
        }
