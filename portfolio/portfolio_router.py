"""
Portfolio Router - Central coordination hub for trade execution.

Transforms strategy intentions (TargetWeights) into executed trades.
This is the ONLY component authorized to place orders.

Six-Step Workflow:
1. COLLECT - Gather TargetWeight objects from all engines
2. AGGREGATE - Sum weights by symbol
3. VALIDATE - Apply exposure limits and constraints
4. NET - Compare targets to current positions
5. PRIORITIZE - Separate by urgency (IMMEDIATE vs EOD)
6. EXECUTE - Place orders via algorithm

Spec: docs/11-portfolio-router.md
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Set

if TYPE_CHECKING:
    from AlgorithmImports import QCAlgorithm

import config
from models.enums import Urgency
from models.target_weight import TargetWeight
from portfolio.exposure_groups import ExposureCalculator


class OrderSide(Enum):
    """Order side (buy or sell)."""

    BUY = "BUY"
    SELL = "SELL"


class OrderType(Enum):
    """Order type for execution."""

    MARKET = "MARKET"  # For IMMEDIATE urgency
    MOO = "MOO"  # Market-on-Open for EOD urgency


@dataclass
class AggregatedWeight:
    """
    Result of aggregating TargetWeights for a symbol.

    Attributes:
        symbol: Instrument ticker.
        target_weight: Combined target weight from all sources.
        sources: List of source engines that contributed.
        urgency: Highest urgency from all sources (IMMEDIATE > EOD).
        reasons: Combined reasons from all sources.
    """

    symbol: str
    target_weight: float
    sources: List[str] = field(default_factory=list)
    urgency: Urgency = Urgency.EOD
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for logging."""
        return {
            "symbol": self.symbol,
            "target_weight": self.target_weight,
            "sources": self.sources,
            "urgency": self.urgency.value,
            "reasons": self.reasons,
        }


@dataclass
class OrderIntent:
    """
    Represents an order to be placed.

    Attributes:
        symbol: Instrument ticker.
        quantity: Number of shares (positive=buy, negative=sell).
        side: BUY or SELL.
        order_type: MARKET or MOO.
        urgency: Original urgency level.
        reason: Combined reason for the order.
        target_weight: Target weight that generated this order.
        current_weight: Current weight before order.
    """

    symbol: str
    quantity: int
    side: OrderSide
    order_type: OrderType
    urgency: Urgency
    reason: str
    target_weight: float
    current_weight: float

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for logging."""
        return {
            "symbol": self.symbol,
            "quantity": self.quantity,
            "side": self.side.value,
            "order_type": self.order_type.value,
            "urgency": self.urgency.value,
            "reason": self.reason,
            "target_weight": self.target_weight,
            "current_weight": self.current_weight,
        }


class PortfolioRouter:
    """
    Central coordination hub for trade execution.

    This is the ONLY component authorized to place orders. All strategy
    engines emit TargetWeight objects which the router collects, validates,
    and converts to actual orders.

    The router implements a six-step workflow:
    1. Collect TargetWeights from all engines
    2. Aggregate weights by symbol
    3. Validate against exposure limits
    4. Net against current positions
    5. Prioritize by urgency
    6. Execute orders
    """

    def __init__(self, algorithm: Optional["QCAlgorithm"] = None):
        """Initialize Portfolio Router."""
        self.algorithm = algorithm
        self._exposure_calc = ExposureCalculator()
        self._pending_weights: List[TargetWeight] = []
        self._last_orders: List[OrderIntent] = []
        self._risk_engine_go: bool = True  # Default to GO
        # Idempotency: track executed orders per minute to prevent duplicates
        self._last_execution_minute: Optional[str] = None
        self._executed_this_minute: Set[str] = set()  # "SYMBOL:SIDE:QTY" keys
        # Process-level idempotency: prevent process_eod running twice on same day
        self._last_eod_date: Optional[str] = None

    def log(self, message: str) -> None:
        """Log via algorithm or skip for testing."""
        pass  # Logging disabled
        if False and self.algorithm:
            self.algorithm.Log(message)  # type: ignore[attr-defined]

    # =========================================================================
    # Step 1: COLLECT
    # =========================================================================

    def receive_signal(self, weight: TargetWeight) -> None:
        """
        Receive a TargetWeight signal from a strategy engine.

        Args:
            weight: TargetWeight signal to process.
        """
        self._pending_weights.append(weight)
        self.log(
            f"ROUTER: RECEIVED | {weight.symbol} | "
            f"Weight={weight.target_weight:.1%} | Source={weight.source} | "
            f"Urgency={weight.urgency.value}"
        )

    def receive_signals(self, weights: List[TargetWeight]) -> None:
        """
        Receive multiple TargetWeight signals.

        Args:
            weights: List of TargetWeight signals.
        """
        for weight in weights:
            self.receive_signal(weight)

    def get_pending_count(self) -> int:
        """Get count of pending signals."""
        return len(self._pending_weights)

    def clear_pending(self) -> None:
        """Clear all pending signals without processing."""
        self._pending_weights.clear()

    # =========================================================================
    # Step 2: AGGREGATE
    # =========================================================================

    def aggregate_weights(
        self,
        weights: List[TargetWeight],
    ) -> Dict[str, AggregatedWeight]:
        """
        Aggregate TargetWeights by symbol with deduplication.

        Deduplicates signals from the same source (prevents same strategy from
        sending duplicate signals), then SUMs weights from different sources
        (allows strategy layering). IMMEDIATE urgency takes precedence over EOD.

        Args:
            weights: List of TargetWeight objects.

        Returns:
            Dict of symbol -> AggregatedWeight.
        """
        aggregated: Dict[str, AggregatedWeight] = {}
        # Track seen (symbol, source) pairs to prevent duplicates from same source
        seen_pairs: Set[tuple] = set()

        for weight in weights:
            symbol = weight.symbol
            source = weight.source
            pair_key = (symbol, source)

            # Skip duplicate signals from same source for same symbol
            # This prevents the same strategy from inflating allocations
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)

            if symbol not in aggregated:
                aggregated[symbol] = AggregatedWeight(
                    symbol=symbol,
                    target_weight=0.0,
                    sources=[],
                    urgency=Urgency.EOD,
                    reasons=[],
                )

            agg = aggregated[symbol]

            # SUM weights from different sources (allows strategy layering)
            # Deduplication above prevents same source from adding twice
            agg.target_weight += weight.target_weight

            # Track sources
            if weight.source not in agg.sources:
                agg.sources.append(weight.source)

            # Track reasons (only first reason per source)
            if weight.reason:
                agg.reasons.append(f"[{weight.source}] {weight.reason}")

            # IMMEDIATE takes precedence
            if weight.urgency == Urgency.IMMEDIATE:
                agg.urgency = Urgency.IMMEDIATE

        return aggregated

    # =========================================================================
    # Step 3: VALIDATE
    # =========================================================================

    def validate_weights(
        self,
        aggregated: Dict[str, AggregatedWeight],
        max_single_position: float,
    ) -> Dict[str, AggregatedWeight]:
        """
        Validate and adjust weights against constraints.

        Applies:
        - Exposure group limits (via ExposureCalculator)
        - Single position limits
        - Negative weight clamping

        Args:
            aggregated: Dict of symbol -> AggregatedWeight.
            max_single_position: Maximum weight for single position.

        Returns:
            Validated and potentially adjusted weights.
        """
        # Extract raw weights
        raw_weights = {sym: agg.target_weight for sym, agg in aggregated.items()}

        # Apply exposure group limits
        adjusted_weights = self._exposure_calc.enforce_limits(raw_weights)

        # Apply single position limit and clamp negatives
        for symbol in adjusted_weights:
            weight = adjusted_weights[symbol]

            # Clamp negative weights to 0 (long-only)
            if weight < 0:
                self.log(f"ROUTER: CLAMPED | {symbol} | {weight:.1%} -> 0%")
                adjusted_weights[symbol] = 0.0
                continue

            # Apply single position limit
            if weight > max_single_position:
                self.log(
                    f"ROUTER: CAPPED | {symbol} | " f"{weight:.1%} -> {max_single_position:.1%}"
                )
                adjusted_weights[symbol] = max_single_position

        # Update aggregated weights with validated values
        for symbol, agg in aggregated.items():
            if symbol in adjusted_weights:
                agg.target_weight = adjusted_weights[symbol]

        return aggregated

    # =========================================================================
    # Step 4: NET
    # =========================================================================

    def calculate_order_intents(
        self,
        aggregated: Dict[str, AggregatedWeight],
        tradeable_equity: float,
        current_positions: Dict[str, float],
        current_prices: Dict[str, float],
    ) -> List[OrderIntent]:
        """
        Calculate orders needed to reach target weights.

        Args:
            aggregated: Validated aggregated weights.
            tradeable_equity: Total tradeable equity.
            current_positions: Dict of symbol -> current value.
            current_prices: Dict of symbol -> current price.

        Returns:
            List of OrderIntent objects.
        """
        orders: List[OrderIntent] = []

        for symbol, agg in aggregated.items():
            # Skip if no price available
            if symbol not in current_prices or current_prices[symbol] <= 0:
                self.log(f"ROUTER: SKIP | {symbol} | No price available")
                continue

            price = current_prices[symbol]
            target_value = tradeable_equity * agg.target_weight
            current_value = current_positions.get(symbol, 0.0)
            current_weight = current_value / tradeable_equity if tradeable_equity > 0 else 0.0

            # Calculate delta
            delta_value = target_value - current_value
            delta_shares = int(delta_value / price)

            # Skip if delta is below minimum
            if abs(delta_shares) < config.MIN_SHARE_DELTA:
                continue

            # Skip if position value below minimum trade size
            if abs(delta_value) < config.MIN_TRADE_VALUE:
                self.log(
                    f"ROUTER: SKIP | {symbol} | "
                    f"Delta ${delta_value:,.0f} < min ${config.MIN_TRADE_VALUE:,}"
                )
                continue

            # Determine side and order type
            side = OrderSide.BUY if delta_shares > 0 else OrderSide.SELL
            order_type = OrderType.MARKET if agg.urgency == Urgency.IMMEDIATE else OrderType.MOO

            reason = "; ".join(agg.reasons) if agg.reasons else "No reason provided"

            orders.append(
                OrderIntent(
                    symbol=symbol,
                    quantity=abs(delta_shares),
                    side=side,
                    order_type=order_type,
                    urgency=agg.urgency,
                    reason=reason,
                    target_weight=agg.target_weight,
                    current_weight=current_weight,
                )
            )

        return orders

    # =========================================================================
    # Step 5: PRIORITIZE
    # =========================================================================

    def prioritize_orders(
        self,
        orders: List[OrderIntent],
    ) -> tuple[List[OrderIntent], List[OrderIntent]]:
        """
        Separate orders by urgency.

        Args:
            orders: List of OrderIntent objects.

        Returns:
            Tuple of (immediate_orders, eod_orders).
        """
        immediate: List[OrderIntent] = []
        eod: List[OrderIntent] = []

        for order in orders:
            if order.urgency == Urgency.IMMEDIATE:
                immediate.append(order)
            else:
                eod.append(order)

        return immediate, eod

    # =========================================================================
    # Step 6: EXECUTE
    # =========================================================================

    def execute_orders(
        self,
        orders: List[OrderIntent],
        current_minute: Optional[str] = None,
    ) -> List[OrderIntent]:
        """
        Execute orders via algorithm with idempotency protection.

        This is the ONLY method that places actual orders.
        Prevents duplicate execution of the same order within the same minute.

        Args:
            orders: List of OrderIntent objects.
            current_minute: Current time truncated to minute (e.g., "2021-05-10 09:31").

        Returns:
            List of executed orders.
        """
        if not self.algorithm:
            self.log("ROUTER: NO_ALGORITHM | Cannot execute orders")
            return []

        # Check risk engine status
        if not self._risk_engine_go:
            self.log("ROUTER: BLOCKED | Risk engine NO-GO status")
            return []

        # Reset idempotency tracking when minute changes
        if current_minute and current_minute != self._last_execution_minute:
            self._executed_this_minute.clear()
            self._last_execution_minute = current_minute

        executed: List[OrderIntent] = []

        for order in orders:
            # Create unique order key: SYMBOL:SIDE:QTY
            order_key = f"{order.symbol}:{order.side.value}:{order.quantity}"

            # Skip if already executed this minute (idempotency guard)
            if order_key in self._executed_this_minute:
                self.log(f"ROUTER: SKIP_DUPLICATE | {order_key} already executed this minute")
                continue

            try:
                quantity = order.quantity if order.side == OrderSide.BUY else -order.quantity

                if order.order_type == OrderType.MARKET:
                    self.algorithm.MarketOrder(order.symbol, quantity)  # type: ignore[attr-defined]
                    self.log(
                        f"ROUTER: MARKET_ORDER | {order.side.value} {order.quantity} {order.symbol}"
                    )
                else:
                    self.algorithm.MarketOnOpenOrder(order.symbol, quantity)  # type: ignore[attr-defined]
                    self.log(
                        f"ROUTER: MOO_ORDER | {order.side.value} {order.quantity} {order.symbol}"
                    )

                # Mark as executed to prevent duplicates
                self._executed_this_minute.add(order_key)
                executed.append(order)

            except Exception as e:
                self.log(f"ROUTER: ORDER_ERROR | {order.symbol} | {e}")

        return executed

    # =========================================================================
    # Main Processing Methods
    # =========================================================================

    def process_immediate(
        self,
        tradeable_equity: float,
        current_positions: Dict[str, float],
        current_prices: Dict[str, float],
        max_single_position: float,
        available_cash: float = 0.0,
        locked_amount: float = 0.0,
        current_time: Optional[str] = None,
    ) -> List[OrderIntent]:
        """
        Process IMMEDIATE urgency signals.

        Called during market hours when immediate action is needed
        (e.g., stop loss, panic mode, MR entries).

        Args:
            tradeable_equity: Total tradeable equity.
            current_positions: Dict of symbol -> current value.
            current_prices: Dict of symbol -> current price.
            max_single_position: Maximum single position weight.
            available_cash: Current available cash in portfolio.
            locked_amount: Amount locked in lockbox (cannot sell SHV).
            current_time: Current timestamp string for idempotency check.

        Returns:
            List of executed orders.
        """
        # Filter to IMMEDIATE only
        immediate_weights = [w for w in self._pending_weights if w.urgency == Urgency.IMMEDIATE]

        if not immediate_weights:
            return []

        # Remove processed weights from pending
        self._pending_weights = [w for w in self._pending_weights if w.urgency != Urgency.IMMEDIATE]

        # Process through workflow
        aggregated = self.aggregate_weights(immediate_weights)
        validated = self.validate_weights(aggregated, max_single_position)
        orders = self.calculate_order_intents(
            validated, tradeable_equity, current_positions, current_prices
        )
        immediate_orders, _ = self.prioritize_orders(orders)

        # Check if we need to liquidate SHV to fund BUY orders
        immediate_orders = self._add_shv_liquidation_if_needed(
            immediate_orders,
            current_positions,
            current_prices,
            available_cash,
            locked_amount,
            tradeable_equity,
        )

        # Extract minute from timestamp for idempotency (e.g., "2021-05-10 09:31:00" -> "2021-05-10 09:31")
        current_minute = current_time[:16] if current_time and len(current_time) >= 16 else None
        executed = self.execute_orders(immediate_orders, current_minute)

        self._last_orders = executed
        return executed

    def process_eod(
        self,
        tradeable_equity: float,
        current_positions: Dict[str, float],
        current_prices: Dict[str, float],
        max_single_position: float,
        available_cash: float = 0.0,
        locked_amount: float = 0.0,
        current_time: Optional[str] = None,
    ) -> List[OrderIntent]:
        """
        Process all pending signals at end of day.

        Called at 16:00 ET to process EOD signals as MOO orders.

        Args:
            tradeable_equity: Total tradeable equity.
            current_positions: Dict of symbol -> current value.
            current_prices: Dict of symbol -> current price.
            max_single_position: Maximum single position weight.
            available_cash: Current available cash in portfolio.
            locked_amount: Amount locked in lockbox (cannot sell SHV).
            current_time: Current timestamp string for idempotency check.

        Returns:
            List of executed orders.
        """
        # Process-level idempotency: prevent running twice on same day
        if current_time:
            current_date = current_time[:10]  # Extract "YYYY-MM-DD"
            if current_date == self._last_eod_date:
                self.log("ROUTER: EOD | Already processed today, skipping")
                return []
            self._last_eod_date = current_date

        if not self._pending_weights:
            self.log("ROUTER: EOD | No pending signals")
            return []

        # Process all pending weights
        weights = self._pending_weights.copy()
        self._pending_weights.clear()

        # Process through workflow
        aggregated = self.aggregate_weights(weights)
        validated = self.validate_weights(aggregated, max_single_position)
        orders = self.calculate_order_intents(
            validated, tradeable_equity, current_positions, current_prices
        )
        _, eod_orders = self.prioritize_orders(orders)

        # Check if we need to liquidate SHV to fund BUY orders
        eod_orders = self._add_shv_liquidation_if_needed(
            eod_orders,
            current_positions,
            current_prices,
            available_cash,
            locked_amount,
            tradeable_equity,
        )

        # Extract minute from timestamp for idempotency (e.g., "2021-05-10 09:31:00" -> "2021-05-10 09:31")
        current_minute = current_time[:16] if current_time and len(current_time) >= 16 else None
        executed = self.execute_orders(eod_orders, current_minute)

        self._last_orders = executed
        return executed

    def _add_shv_liquidation_if_needed(
        self,
        orders: List[OrderIntent],
        current_positions: Dict[str, float],
        current_prices: Dict[str, float],
        available_cash: float,
        locked_amount: float,
        tradeable_equity: float,
    ) -> List[OrderIntent]:
        """
        Ensure SELL orders execute before BUY orders.

        NOTE: SHV liquidation is handled by _presell_shv_for_pending_buys() at 15:45
        which executes a MARKET order while the market is still open.
        This method no longer adds SHV orders to avoid duplicate liquidation.

        Args:
            orders: List of orders to execute.
            current_positions: Dict of symbol -> current value.
            current_prices: Dict of symbol -> current price.
            available_cash: Current available cash.
            locked_amount: Amount locked in lockbox.
            tradeable_equity: Total tradeable equity.

        Returns:
            Orders with SELL orders first, then BUY orders.
        """
        # Separate sells and buys, ensure sells execute first
        sells = [o for o in orders if o.side == OrderSide.SELL]
        buys = [o for o in orders if o.side == OrderSide.BUY]
        return sells + buys

    # =========================================================================
    # Risk Engine Integration
    # =========================================================================

    def set_risk_status(self, go: bool) -> None:
        """
        Set risk engine GO/NO-GO status.

        Args:
            go: True if orders are allowed, False to block.
        """
        self._risk_engine_go = go
        status = "GO" if go else "NO-GO"
        self.log(f"ROUTER: RISK_STATUS | {status}")

    def get_risk_status(self) -> bool:
        """Get current risk engine status."""
        return self._risk_engine_go

    # =========================================================================
    # SHV Liquidation Support
    # =========================================================================

    def calculate_shv_liquidation(
        self,
        cash_needed: float,
        current_shv_value: float,
        locked_amount: float,
        tradeable_equity: float,
    ) -> Optional[TargetWeight]:
        """
        Calculate SHV liquidation needed for cash.

        Called when new positions require cash and SHV should be
        liquidated first.

        Args:
            cash_needed: Amount of cash required.
            current_shv_value: Current SHV holdings value.
            locked_amount: Amount locked in lockbox.
            tradeable_equity: Total tradeable equity.

        Returns:
            TargetWeight for SHV liquidation, or None if not needed.
        """
        available_shv = max(0.0, current_shv_value - locked_amount)

        if available_shv <= 0:
            return None

        # Calculate how much to sell
        sell_amount = min(cash_needed, available_shv)
        remaining_shv = current_shv_value - sell_amount

        if tradeable_equity <= 0:
            target_weight = 0.0
        else:
            target_weight = remaining_shv / tradeable_equity

        reason = f"Liquidation for ${cash_needed:,.0f} needed, selling ${sell_amount:,.0f}"

        return TargetWeight(
            symbol="SHV",
            target_weight=target_weight,
            source="ROUTER",
            urgency=Urgency.IMMEDIATE,
            reason=reason,
        )

    # =========================================================================
    # State and Utilities
    # =========================================================================

    def get_last_orders(self) -> List[OrderIntent]:
        """Get last executed orders."""
        return self._last_orders.copy()

    def get_state_for_persistence(self) -> Dict[str, Any]:
        """Get state for ObjectStore."""
        return {
            "pending_count": len(self._pending_weights),
            "last_order_count": len(self._last_orders),
            "risk_status": self._risk_engine_go,
        }

    def reset(self) -> None:
        """Reset router state."""
        self._pending_weights.clear()
        self._last_orders.clear()
        self._risk_engine_go = True
        self._last_execution_minute = None
        self._executed_this_minute.clear()
        self._last_eod_date = None
        self.log("ROUTER: RESET")
