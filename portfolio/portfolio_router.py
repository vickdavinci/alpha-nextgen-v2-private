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
        requested_quantity: Optional explicit quantity for options (V2.3.2).
        metadata: Optional metadata for spread orders (V2.3).
    """

    symbol: str
    target_weight: float
    sources: List[str] = field(default_factory=list)
    urgency: Urgency = Urgency.EOD
    reasons: List[str] = field(default_factory=list)
    requested_quantity: Optional[int] = None  # V2.3.2: For options risk-based sizing
    metadata: Optional[Dict[str, Any]] = None  # V2.3: For spread order info

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for logging."""
        return {
            "symbol": self.symbol,
            "target_weight": self.target_weight,
            "sources": self.sources,
            "urgency": self.urgency.value,
            "reasons": self.reasons,
            "metadata": self.metadata,
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
        is_combo: V2.3.9 - True if this is a combo/spread order.
        combo_short_symbol: V2.3.9 - Short leg symbol for combo orders.
        combo_short_quantity: V2.3.9 - Short leg quantity (negative) for combo orders.
    """

    symbol: str
    quantity: int
    side: OrderSide
    order_type: OrderType
    urgency: Urgency
    reason: str
    target_weight: float
    current_weight: float
    # V2.3.9: Combo order support for spreads
    is_combo: bool = False
    combo_short_symbol: Optional[str] = None
    combo_short_quantity: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for logging."""
        result = {
            "symbol": self.symbol,
            "quantity": self.quantity,
            "side": self.side.value,
            "order_type": self.order_type.value,
            "urgency": self.urgency.value,
            "reason": self.reason,
            "target_weight": self.target_weight,
            "current_weight": self.current_weight,
        }
        # V2.3.9: Include combo order fields if present
        if self.is_combo:
            result["is_combo"] = self.is_combo
            result["combo_short_symbol"] = self.combo_short_symbol
            result["combo_short_quantity"] = self.combo_short_quantity
        return result


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

    V2.1 Core-Satellite Architecture:
    - Core (TREND): 70% max allocation
    - Satellite (OPT): 20-30% max allocation
    - Satellite (MR): 0-10% max allocation
    """

    # V2.3: Source-based allocation limits (Core-Satellite)
    # Updated to use config values and reserve capital for options
    # RESERVED_OPTIONS_PCT (25%) ensures buying power is available for options
    SOURCE_ALLOCATION_LIMITS: Dict[str, float] = {
        "TREND": config.TREND_TOTAL_ALLOCATION,  # 55% max (was 70%)
        "OPT": config.OPTIONS_ALLOCATION_MAX,  # 30% max (reserved for swing spreads)
        "OPT_INTRADAY": 0.05,  # 5% max for intraday "Sniper" mode
        "MR": config.MR_TOTAL_ALLOCATION,  # 10% max
        "HEDGE": 0.30,  # Hedge: 30% max (TMF 20% + PSQ 10%)
        # V2.3.17: YIELD raised from 0.50 to 0.99 to allow near-full SHV post-kill-switch
        "YIELD": 0.99,  # Yield (SHV): 99% max (absorb idle cash after kill switch)
        "COLD_START": 0.35,  # Cold Start: 35% max (subset of TREND)
        "RISK": 1.00,  # Risk: No limit (emergency liquidations)
        "ROUTER": 1.00,  # Router: No limit (SHV liquidations)
    }

    # V2.3: Non-options sources that must respect reserved options capital
    NON_OPTIONS_SOURCES = {"TREND", "MR", "HEDGE", "COLD_START"}

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

            # V2.3.2: Preserve requested_quantity for options
            if weight.requested_quantity is not None:
                agg.requested_quantity = weight.requested_quantity

            # V2.3: Preserve metadata for spread orders
            if weight.metadata is not None:
                agg.metadata = weight.metadata

        return aggregated

    # =========================================================================
    # Step 2.5: ENFORCE SOURCE LIMITS (V2.1)
    # =========================================================================

    def _enforce_source_limits(
        self,
        weights: List[TargetWeight],
    ) -> List[TargetWeight]:
        """
        Enforce Core-Satellite allocation limits by source.

        V2.3: Caps total allocation per source engine AND reserves capital for options:
        - TREND (Core): 55% max (config.TREND_TOTAL_ALLOCATION)
        - OPT (Options): 30% max (reserved via RESERVED_OPTIONS_PCT)
        - MR (Mean Reversion): 10% max
        - Total non-options: capped at (1.0 - RESERVED_OPTIONS_PCT) = 75%

        Args:
            weights: List of TargetWeight objects.

        Returns:
            List with weights scaled down if source exceeds limit.
        """
        # Group weights by source
        by_source: Dict[str, List[TargetWeight]] = {}
        for w in weights:
            if w.source not in by_source:
                by_source[w.source] = []
            by_source[w.source].append(w)

        adjusted_weights: List[TargetWeight] = []

        for source, source_weights in by_source.items():
            # Get limit for this source
            limit = self.SOURCE_ALLOCATION_LIMITS.get(source, 0.50)

            # Calculate total requested by this source
            total_requested = sum(w.target_weight for w in source_weights)

            if total_requested <= limit:
                # Under limit - keep as-is
                adjusted_weights.extend(source_weights)
            else:
                # Over limit - scale down proportionally
                scale_factor = limit / total_requested if total_requested > 0 else 1.0

                for w in source_weights:
                    scaled_weight = w.target_weight * scale_factor
                    adjusted_weights.append(
                        TargetWeight(
                            symbol=w.symbol,
                            target_weight=scaled_weight,
                            source=w.source,
                            urgency=w.urgency,
                            reason=f"{w.reason} [scaled {scale_factor:.0%}]",
                            requested_quantity=w.requested_quantity,  # V2.3.2: Preserve risk-based sizing
                            metadata=w.metadata,  # V2.3: Preserve spread metadata
                        )
                    )

                self.log(
                    f"ROUTER: SOURCE_LIMIT | {source} | "
                    f"Requested {total_requested:.1%} > Limit {limit:.1%} | "
                    f"Scaled by {scale_factor:.0%}"
                )

        # V2.3: Enforce total non-options cap (reserve capital for options)
        # This ensures TREND + MR + HEDGE never consume all buying power
        max_non_options = 1.0 - config.RESERVED_OPTIONS_PCT  # 75%

        non_options_total = sum(
            w.target_weight for w in adjusted_weights if w.source in self.NON_OPTIONS_SOURCES
        )

        if non_options_total > max_non_options:
            scale_factor = max_non_options / non_options_total
            final_weights = []

            for w in adjusted_weights:
                if w.source in self.NON_OPTIONS_SOURCES:
                    scaled_weight = w.target_weight * scale_factor
                    final_weights.append(
                        TargetWeight(
                            symbol=w.symbol,
                            target_weight=scaled_weight,
                            source=w.source,
                            urgency=w.urgency,
                            reason=f"{w.reason} [options reserve scaled {scale_factor:.0%}]",
                        )
                    )
                else:
                    final_weights.append(w)

            self.log(
                f"ROUTER: OPTIONS_RESERVE | Non-options total {non_options_total:.1%} > "
                f"Max {max_non_options:.1%} | Scaled by {scale_factor:.0%}"
            )
            return final_weights

        return adjusted_weights

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

            # Check if this is an options contract (contains C or P with strike price pattern)
            # Options symbols look like "QQQ 260126C00455000" or similar
            is_option = len(symbol) > 10 and ("C0" in symbol or "P0" in symbol)

            if is_option:
                # V2.3.2: Use requested_quantity if provided (risk-based sizing)
                if agg.requested_quantity is not None and agg.requested_quantity > 0:
                    # Options engine calculated the correct contract count
                    delta_shares = agg.requested_quantity
                    self.log(
                        f"ROUTER: OPTIONS_SIZING | {symbol} | "
                        f"Using requested_quantity={agg.requested_quantity} contracts"
                    )
                else:
                    # Fallback: calculate from target_weight (legacy behavior)
                    # Options: 1 contract = 100 shares, so cost = price * 100
                    delta_shares = int(delta_value / (price * 100))
                    self.log(
                        f"ROUTER: OPTIONS_SIZING | {symbol} | "
                        f"Fallback to weight-based: {delta_shares} contracts"
                    )
            else:
                # Equities: 1 share = 1 share
                delta_shares = int(delta_value / price)

            # Skip if delta is below minimum
            if abs(delta_shares) < config.MIN_SHARE_DELTA:
                continue

            # V2.3.3: Check if this is a closing trade (going to 0)
            # Allow closing trades even if value is small (e.g., worthless options)
            is_closing = agg.target_weight == 0.0

            # Skip if position value below minimum trade size (unless closing)
            if abs(delta_value) < config.MIN_TRADE_VALUE and not is_closing:
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

            # V2.3.9: Handle spread orders as COMBO (atomic execution)
            # Per CTA guidance: ComboMarketOrder calculates multi-leg margin (~$42K)
            # instead of naked short margin (~$729K)
            if agg.metadata is not None:
                short_leg_symbol = agg.metadata.get("spread_short_leg_symbol")
                short_leg_qty = agg.metadata.get("spread_short_leg_quantity")
                spread_close_short = agg.metadata.get("spread_close_short", False)

                if short_leg_symbol and short_leg_qty:
                    # Remove the separate long leg order we just added
                    # Replace with a combo order that includes both legs
                    if orders and orders[-1].symbol == symbol:
                        orders.pop()  # Remove the long leg

                    # Determine quantities for combo order
                    # Entry: BUY long (positive), SELL short (negative)
                    # Exit: SELL long (negative), BUY short (positive)
                    if spread_close_short:
                        # Closing spread - sell long, buy short
                        long_qty = -abs(delta_shares)
                        short_qty = abs(short_leg_qty)  # Positive = BUY back
                        combo_reason = f"[OPT] COMBO Close Spread: {reason}"
                    else:
                        # Opening spread - buy long, sell short
                        long_qty = abs(delta_shares)
                        short_qty = -abs(short_leg_qty)  # Negative = SELL
                        combo_reason = f"[OPT] COMBO Open Spread: {reason}"

                    orders.append(
                        OrderIntent(
                            symbol=symbol,  # Long leg symbol
                            quantity=abs(long_qty),
                            side=OrderSide.BUY if long_qty > 0 else OrderSide.SELL,
                            order_type=order_type,
                            urgency=agg.urgency,
                            reason=combo_reason,
                            target_weight=agg.target_weight,
                            current_weight=current_weight,
                            is_combo=True,
                            combo_short_symbol=short_leg_symbol,
                            combo_short_quantity=short_qty,
                        )
                    )
                    self.log(
                        f"ROUTER: COMBO_ORDER | Long={symbol} x{long_qty} + "
                        f"Short={short_leg_symbol} x{short_qty}"
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

            # V2.3.2 FIX: Check buying power before placing BUY orders
            if order.side == OrderSide.BUY:
                try:
                    current_price = self.algorithm.Securities[order.symbol].Price  # type: ignore[attr-defined]
                    # Detect options by symbol pattern (C0 or P0 for calls/puts)
                    is_option = len(order.symbol) > 10 and (
                        "C0" in order.symbol or "P0" in order.symbol
                    )
                    # Options: 1 contract = 100 shares
                    multiplier = 100 if is_option else 1
                    order_value = order.quantity * current_price * multiplier

                    margin_remaining = self.algorithm.Portfolio.MarginRemaining  # type: ignore[attr-defined]
                    if order_value > margin_remaining:
                        self.log(
                            f"ROUTER: INSUFFICIENT_MARGIN | {order.symbol} | "
                            f"Order=${order_value:,.0f} > Margin=${margin_remaining:,.0f} | "
                            f"Qty={order.quantity} @ ${current_price:.2f}"
                        )
                        continue
                except Exception:
                    pass  # Continue with order if price lookup fails

            try:
                quantity = order.quantity if order.side == OrderSide.BUY else -order.quantity

                # V2.3.9: Handle combo orders for spreads
                if order.is_combo and order.combo_short_symbol and order.combo_short_quantity:
                    # Import Leg class for combo orders
                    from AlgorithmImports import Leg

                    # Create legs for combo order
                    # Long leg: symbol with quantity (positive=BUY, negative=SELL)
                    # Short leg: combo_short_symbol with combo_short_quantity
                    legs = [
                        Leg.Create(order.symbol, quantity),
                        Leg.Create(order.combo_short_symbol, order.combo_short_quantity),
                    ]

                    # Submit combo order - broker calculates NET margin (spread margin)
                    self.algorithm.ComboMarketOrder(legs, abs(order.quantity))  # type: ignore[attr-defined]
                    self.log(
                        f"ROUTER: COMBO_MARKET_ORDER | "
                        f"Long={order.symbol} x{quantity} + "
                        f"Short={order.combo_short_symbol} x{order.combo_short_quantity}"
                    )
                elif order.order_type == OrderType.MARKET:
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

        # V2.1: Enforce source limits first (Core-Satellite)
        immediate_weights = self._enforce_source_limits(immediate_weights)

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

        # V2.1: Enforce source limits first (Core-Satellite)
        weights = self._enforce_source_limits(weights)

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
        V2.3.20: Calculate shortfall and generate SHV SELL order if needed.

        When BUY orders require more cash than available, automatically liquidate
        SHV to fund the purchase. This prevents "Insufficient Buying Power" errors
        for options and other immediate trades.

        Args:
            orders: List of orders to execute.
            current_positions: Dict of symbol -> current value.
            current_prices: Dict of symbol -> current price.
            available_cash: Current available cash.
            locked_amount: Amount locked in lockbox.
            tradeable_equity: Total tradeable equity.

        Returns:
            Orders with SHV SELL first (if needed), then other SELLs, then BUYs.
        """
        # Separate sells and buys
        sells = [o for o in orders if o.side == OrderSide.SELL]
        buys = [o for o in orders if o.side == OrderSide.BUY]

        # V2.3.20: Calculate total BUY value needed (quantity × price)
        def get_order_value(order: OrderIntent) -> float:
            price = current_prices.get(order.symbol, 0.0)
            return abs(order.quantity) * price

        buy_value = sum(get_order_value(o) for o in buys)

        if buy_value <= 0:
            return sells + buys

        # Calculate cash that will be available after existing sells
        sell_proceeds = sum(get_order_value(o) for o in sells)
        projected_cash = available_cash + sell_proceeds

        # Calculate shortfall
        shortfall = buy_value - projected_cash

        if shortfall > 0:
            # Need to liquidate SHV to cover shortfall
            current_shv_value = current_positions.get("SHV", 0.0)
            available_shv = max(0.0, current_shv_value - locked_amount)

            if available_shv > 0:
                # Calculate how much SHV to sell (with 5% buffer for slippage)
                shv_sell_amount = min(shortfall * 1.05, available_shv)
                remaining_shv = current_shv_value - shv_sell_amount

                if tradeable_equity > 0:
                    target_weight = remaining_shv / tradeable_equity
                else:
                    target_weight = 0.0

                # Create SHV sell order
                shv_price = current_prices.get("SHV", 110.0)  # Default ~$110
                shv_shares = int(shv_sell_amount / shv_price) if shv_price > 0 else 0

                if shv_shares > 0:
                    current_shv_weight = (
                        current_shv_value / tradeable_equity if tradeable_equity > 0 else 0.0
                    )
                    shv_order = OrderIntent(
                        symbol="SHV",
                        quantity=-shv_shares,  # Negative for SELL
                        side=OrderSide.SELL,
                        order_type=OrderType.MARKET,
                        urgency=Urgency.IMMEDIATE,
                        reason=f"AUTO_LIQUIDATE: ${shortfall:,.0f} shortfall for pending buys",
                        target_weight=target_weight,
                        current_weight=current_shv_weight,
                    )
                    self.log(
                        f"SHV_AUTO_LIQUIDATE: Selling ${shv_sell_amount:,.0f} SHV "
                        f"({shv_shares} shares) to fund ${buy_value:,.0f} in buys | "
                        f"Available cash=${available_cash:,.0f} | Shortfall=${shortfall:,.0f}"
                    )
                    # Insert SHV sell at the beginning
                    sells.insert(0, shv_order)
            else:
                self.log(
                    f"SHV_SHORTFALL_WARNING: Need ${shortfall:,.0f} but no SHV available "
                    f"(locked=${locked_amount:,.0f})"
                )

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
    # V2.1: REBALANCING (ORC-2)
    # =========================================================================

    # Drift threshold for rebalancing
    REBALANCE_DRIFT_THRESHOLD: float = 0.05  # 5% drift triggers rebalance

    def check_rebalancing_needed(
        self,
        target_allocations: Dict[str, float],
        current_positions: Dict[str, float],
        tradeable_equity: float,
    ) -> List[TargetWeight]:
        """
        Check if portfolio rebalancing is needed due to drift.

        V2.1: Triggers rebalancing when any position drifts > 5% from target.

        Args:
            target_allocations: Dict of symbol -> target weight (0-1).
            current_positions: Dict of symbol -> current value.
            tradeable_equity: Total tradeable equity.

        Returns:
            List of TargetWeight signals for rebalancing.
        """
        rebalance_signals: List[TargetWeight] = []

        if tradeable_equity <= 0:
            return rebalance_signals

        # Calculate current allocations
        current_allocations: Dict[str, float] = {}
        for symbol, value in current_positions.items():
            current_allocations[symbol] = value / tradeable_equity

        # Check each target for drift
        all_symbols = set(target_allocations.keys()) | set(current_allocations.keys())

        for symbol in all_symbols:
            target = target_allocations.get(symbol, 0.0)
            current = current_allocations.get(symbol, 0.0)

            drift = abs(target - current)

            if drift > self.REBALANCE_DRIFT_THRESHOLD:
                # Drift exceeds threshold - add rebalancing signal
                self.log(
                    f"ROUTER: REBALANCE | {symbol} | "
                    f"Target={target:.1%} | Current={current:.1%} | "
                    f"Drift={drift:.1%} > {self.REBALANCE_DRIFT_THRESHOLD:.1%}"
                )

                rebalance_signals.append(
                    TargetWeight(
                        symbol=symbol,
                        target_weight=target,
                        source="ROUTER",
                        urgency=Urgency.EOD,
                        reason=f"REBALANCE: Drift {drift:.1%} > {self.REBALANCE_DRIFT_THRESHOLD:.1%}",
                    )
                )

        return rebalance_signals

    def get_target_allocations(self) -> Dict[str, float]:
        """
        Get current target allocations based on pending signals.

        This provides a snapshot of target allocations for drift calculation.

        Returns:
            Dict of symbol -> target weight.
        """
        # Aggregate pending weights to get targets
        if not self._pending_weights:
            return {}

        aggregated = self.aggregate_weights(self._pending_weights)
        return {sym: agg.target_weight for sym, agg in aggregated.items()}

    def calculate_portfolio_drift(
        self,
        target_allocations: Dict[str, float],
        current_positions: Dict[str, float],
        tradeable_equity: float,
    ) -> Dict[str, float]:
        """
        Calculate drift for each position.

        Args:
            target_allocations: Dict of symbol -> target weight.
            current_positions: Dict of symbol -> current value.
            tradeable_equity: Total tradeable equity.

        Returns:
            Dict of symbol -> drift (absolute difference from target).
        """
        drift_map: Dict[str, float] = {}

        if tradeable_equity <= 0:
            return drift_map

        all_symbols = set(target_allocations.keys()) | set(current_positions.keys())

        for symbol in all_symbols:
            target = target_allocations.get(symbol, 0.0)
            current_value = current_positions.get(symbol, 0.0)
            current_weight = current_value / tradeable_equity

            drift_map[symbol] = abs(target - current_weight)

        return drift_map

    def get_max_drift(
        self,
        target_allocations: Dict[str, float],
        current_positions: Dict[str, float],
        tradeable_equity: float,
    ) -> tuple:
        """
        Get the maximum drift symbol and value.

        Args:
            target_allocations: Dict of symbol -> target weight.
            current_positions: Dict of symbol -> current value.
            tradeable_equity: Total tradeable equity.

        Returns:
            Tuple of (symbol, drift_value) for the highest drift position.
        """
        drift_map = self.calculate_portfolio_drift(
            target_allocations, current_positions, tradeable_equity
        )

        if not drift_map:
            return (None, 0.0)

        max_symbol = max(drift_map.keys(), key=lambda k: drift_map[k])
        return (max_symbol, drift_map[max_symbol])

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
