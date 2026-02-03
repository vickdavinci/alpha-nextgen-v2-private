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
    from engines.satellite.options_engine import SpreadPosition

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
    MOC = "MOC"  # V2.4.2: Market-on-Close for MOC urgency (Trend entries)


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
        "COLD_START": 0.35,  # Cold Start: 35% max (subset of TREND)
        "RISK": 1.00,  # Risk: No limit (emergency liquidations)
        "ROUTER": 1.00,  # Router: No limit
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
        # V2.3.24: Rejection log throttle to reduce spam
        self._last_rejection_log_time: Optional[Any] = None
        self._rejection_log_count: int = 0

        # V2.9: Track open spread margin (Bug #1 fix)
        # Stores {spread_id: margin_reserved} for each open spread
        self._open_spread_margin: Dict[str, float] = {}

    def log(self, message: str) -> None:
        """Log via algorithm or skip for testing."""
        # V2.3.21: Re-enabled logging for SHV auto-liquidation visibility
        if self.algorithm:
            self.algorithm.Log(message)  # type: ignore[attr-defined]

    def _should_log_rejection(self) -> bool:
        """
        V2.3.24: Throttle rejection logging to reduce spam.

        Only log MIN_TRADE_VALUE rejections once per REJECTION_LOG_THROTTLE_MINUTES.
        Returns True if we should log, False to skip.
        """
        if not self.algorithm:
            return True

        current_time = self.algorithm.Time  # type: ignore[attr-defined]

        # First rejection - always log
        if self._last_rejection_log_time is None:
            self._last_rejection_log_time = current_time
            self._rejection_log_count = 1
            return True

        # Check if throttle interval has passed
        from datetime import timedelta

        throttle_minutes = getattr(config, "REJECTION_LOG_THROTTLE_MINUTES", 15)
        time_since_last = current_time - self._last_rejection_log_time

        if time_since_last >= timedelta(minutes=throttle_minutes):
            # Throttle period passed - log summary and reset
            if self._rejection_log_count > 1:
                self.log(
                    f"ROUTER: REJECTION_SUMMARY | {self._rejection_log_count} orders skipped "
                    f"(below min trade value) in last {throttle_minutes} min"
                )
            self._last_rejection_log_time = current_time
            self._rejection_log_count = 1
            return True
        else:
            # Within throttle period - count but don't log
            self._rejection_log_count += 1
            return False

    # =========================================================================
    # V2.9: SPREAD MARGIN TRACKING (Bug #1 Fix)
    # =========================================================================

    def register_spread_margin(self, spread_id: str, margin_reserved: float) -> None:
        """
        V2.9: Register margin reserved by an open spread position.

        Called when a spread fills to track margin locked by open positions.
        This prevents Trend engine from overcommitting margin.

        Args:
            spread_id: Unique identifier for the spread (e.g., long_leg_symbol).
            margin_reserved: Margin requirement for this spread (width × 100 × num_spreads).
        """
        self._open_spread_margin[spread_id] = margin_reserved
        self.log(
            f"MARGIN_TRACK: Registered spread | ID={spread_id} | Reserved=${margin_reserved:,.0f}"
        )

    def unregister_spread_margin(self, spread_id: str) -> None:
        """
        V2.9: Unregister margin when a spread position is closed.

        Args:
            spread_id: Unique identifier for the spread being closed.
        """
        if spread_id in self._open_spread_margin:
            freed = self._open_spread_margin.pop(spread_id)
            self.log(f"MARGIN_TRACK: Unregistered spread | ID={spread_id} | Freed=${freed:,.0f}")

    def clear_all_spread_margins(self) -> None:
        """
        V2.18.2: Clear all spread margin reservations.

        Called during margin circuit breaker liquidation to prevent
        "Ghost Margin" lockout where cleared positions still have
        margin reserved in the router.

        Bug Fix: When clear_spread_position() is called, it only clears
        OptionsEngine state but NOT the router's margin tracking. This
        leaves "ghost" reservations that block all future options trades.
        """
        if self._open_spread_margin:
            total_freed = sum(self._open_spread_margin.values())
            count = len(self._open_spread_margin)
            self._open_spread_margin.clear()
            self.log(
                f"MARGIN_TRACK: Cleared ALL spread margins | "
                f"Count={count} | Freed=${total_freed:,.0f}"
            )

    def get_reserved_spread_margin(self) -> float:
        """
        V2.9: Get total margin reserved by all open spread positions.

        Returns:
            Sum of margin requirements for all tracked open spreads.
        """
        return sum(self._open_spread_margin.values())

    def get_effective_margin_remaining(self) -> float:
        """
        V2.9: Get effective margin remaining after subtracting spread reservations.

        This prevents 'Buying Power Lock-Out' where Trend positions are rejected
        because credit spreads have locked up margin.

        Returns:
            Portfolio.MarginRemaining minus spread reservations.
        """
        if not self.algorithm:
            return 0.0

        margin_remaining = self.algorithm.Portfolio.MarginRemaining  # type: ignore[attr-defined]
        spread_reserved = self.get_reserved_spread_margin()
        effective = max(0.0, margin_remaining - spread_reserved)

        if spread_reserved > 0:
            self.log(
                f"MARGIN_TRACK: Effective margin | "
                f"Raw=${margin_remaining:,.0f} | SpreadReserved=${spread_reserved:,.0f} | "
                f"Effective=${effective:,.0f}"
            )

        return effective

    # =========================================================================
    # V2.18: CAPITAL PARTITION (Fix for Trend/Options starvation)
    # =========================================================================

    def get_trend_capital(self) -> float:
        """
        V2.18: Get capital reserved for Trend Engine (hard partition).

        Prevents Trend from consuming all capital and starving Options.

        Returns:
            50% of tradeable equity reserved for Trend.
        """
        if not self.algorithm:
            return 0.0

        total_equity = self.algorithm.Portfolio.TotalPortfolioValue
        return total_equity * config.CAPITAL_PARTITION_TREND

    def get_options_capital(self) -> float:
        """
        V2.18: Get capital reserved for Options Engine (hard partition).

        Returns:
            50% of tradeable equity reserved for Options.
        """
        if not self.algorithm:
            return 0.0

        total_equity = self.algorithm.Portfolio.TotalPortfolioValue
        return total_equity * config.CAPITAL_PARTITION_OPTIONS

    def check_capital_partition(self, source: str, order_value: float) -> bool:
        """
        V2.18: Check if order would exceed capital partition for source.

        Args:
            source: Signal source ("TREND", "OPTIONS", etc.)
            order_value: Dollar value of proposed order.

        Returns:
            True if order is within partition limits, False if blocked.
        """
        if source == "TREND":
            available = self.get_trend_capital()
            # Subtract current trend holdings
            if self.algorithm:
                trend_holdings = sum(
                    self.algorithm.Portfolio[getattr(self.algorithm, sym.lower())].HoldingsValue
                    for sym in config.TREND_PRIORITY_ORDER
                    if hasattr(self.algorithm, sym.lower())
                )
                available = available - trend_holdings

            if order_value > available:
                self.log(
                    f"PARTITION_BLOCK: TREND | Requested=${order_value:,.0f} > Available=${available:,.0f}"
                )
                return False

        elif source == "OPTIONS":
            available = self.get_options_capital() - self.get_reserved_spread_margin()
            if order_value > available:
                self.log(
                    f"PARTITION_BLOCK: OPTIONS | Requested=${order_value:,.0f} > Available=${available:,.0f}"
                )
                return False

        return True

    # =========================================================================
    # V2.18: LEVERAGE CAP (Fix for 196% margin overflow)
    # =========================================================================

    def check_leverage_cap(self, projected_margin_pct: float) -> bool:
        """
        V2.18: Check if projected margin exceeds leverage cap.

        Args:
            projected_margin_pct: Projected margin as percentage of equity (0.0-1.0+).

        Returns:
            True if within cap, False if would exceed.
        """
        max_margin = config.MAX_MARGIN_WEIGHTED_ALLOCATION

        if projected_margin_pct > max_margin:
            self.log(
                f"LEVERAGE_CAP: Blocked | Projected={projected_margin_pct:.1%} > Max={max_margin:.0%}"
            )
            return False

        return True

    def get_current_margin_usage(self) -> float:
        """
        V2.18: Get current margin usage as percentage of equity.

        Returns:
            Margin used / Total equity (0.0-1.0+).
        """
        if not self.algorithm:
            return 0.0

        total_equity = self.algorithm.Portfolio.TotalPortfolioValue
        margin_used = self.algorithm.Portfolio.TotalMarginUsed

        if total_equity <= 0:
            return 0.0

        return margin_used / total_equity

    # =========================================================================
    # V2.18: MARGIN PRE-CHECK (RPT-6 Fix)
    # =========================================================================

    def verify_margin_available(self, order_value: float) -> bool:
        """
        V2.18: Pre-check margin before order submission (RPT-6 fix).

        Verifies sufficient margin with 20% buffer to prevent rejections.

        Args:
            order_value: Dollar value of proposed order.

        Returns:
            True if sufficient margin, False if insufficient.
        """
        if not self.algorithm:
            return True

        margin_remaining = self.algorithm.Portfolio.MarginRemaining
        buffer = getattr(config, "MARGIN_PRE_CHECK_BUFFER", 1.20)
        required_with_buffer = order_value * buffer

        if required_with_buffer > margin_remaining:
            self.log(
                f"MARGIN_PRECHECK_FAIL: Required=${required_with_buffer:,.0f} (with {buffer:.0%} buffer) "
                f"> Available=${margin_remaining:,.0f}"
            )
            return False

        return True

    # =========================================================================
    # V2.17: UNIFIED SPREAD CLOSE (Fixes USER-1, USER-3, RPT-9)
    # =========================================================================

    def execute_spread_close(
        self,
        spread: "SpreadPosition",
        reason: str = "SPREAD_CLOSE",
        is_emergency: bool = False,
    ) -> bool:
        """
        V2.17: Unified spread close with retry and sequential fallback.

        This is the SINGLE point for spread exits. Used by:
        - Normal spread exit (options_engine check_spread_exit_conditions)
        - Kill switch spread liquidation
        - Friday firewall
        - DTE exit

        Args:
            spread: SpreadPosition to close (from options_engine._spread_position).
            reason: Reason for exit (for logging).
            is_emergency: If True, skip retries and go straight to sequential.

        Returns:
            True if spread was closed (combo or sequential), False if all failed.
        """
        if not self.algorithm:
            self.log("ROUTER: NO_ALGORITHM | Cannot execute spread close")
            return False

        if spread is None or spread.num_spreads <= 0:
            self.log(f"ROUTER: SPREAD_CLOSE_SKIP | No spread to close | Reason={reason}")
            return True  # Nothing to close = success

        # Mark spread as closing to prevent duplicate signals
        spread.is_closing = True

        long_symbol = spread.long_leg.symbol
        short_symbol = spread.short_leg.symbol
        num_spreads = spread.num_spreads

        self.log(
            f"ROUTER: SPREAD_CLOSE_START | {reason} | "
            f"Type={spread.spread_type} x{num_spreads} | "
            f"Long={long_symbol} Short={short_symbol}"
        )

        # Try atomic ComboMarketOrder first (unless emergency)
        if not is_emergency:
            combo_success = self._try_combo_close(long_symbol, short_symbol, num_spreads, reason)
            if combo_success:
                self._unregister_spread_margin_if_tracked(long_symbol)
                return True

        # Fallback to sequential close (margin-safe: short first, then long)
        self.log(
            f"ROUTER: SEQUENTIAL_FALLBACK | {reason} | "
            f"Closing short first (buy back), then long (sell)"
        )

        sequential_success = self._execute_sequential_close(
            long_symbol, short_symbol, num_spreads, reason
        )

        if sequential_success:
            self._unregister_spread_margin_if_tracked(long_symbol)
            return True

        # All close attempts failed - clear the lock to allow retry
        if config.SPREAD_LOCK_CLEAR_ON_FAILURE:
            spread.is_closing = False
            self.log(
                f"ROUTER: SPREAD_LOCK_CLEARED | {reason} | "
                f"All close attempts failed - position remains open for retry"
            )

        return False

    def _try_combo_close(
        self,
        long_symbol: str,
        short_symbol: str,
        num_spreads: int,
        reason: str,
    ) -> bool:
        """
        V2.17: Attempt atomic combo close with retries.

        Args:
            long_symbol: Symbol of the long leg to sell.
            short_symbol: Symbol of the short leg to buy back.
            num_spreads: Number of spreads to close.
            reason: Reason for close (for logging).

        Returns:
            True if combo order succeeded, False if all retries exhausted.
        """
        if not self.algorithm:
            return False

        try:
            from AlgorithmImports import Leg

            # Find actual QC Symbol objects from portfolio
            long_qc_symbol = None
            short_qc_symbol = None

            for kvp in self.algorithm.Portfolio:  # type: ignore[attr-defined]
                holding = kvp.Value
                if not holding.Invested:
                    continue
                symbol_str = str(holding.Symbol)
                if long_symbol in symbol_str and holding.Quantity > 0:
                    long_qc_symbol = holding.Symbol
                elif short_symbol in symbol_str and holding.Quantity < 0:
                    short_qc_symbol = holding.Symbol

            if long_qc_symbol is None or short_qc_symbol is None:
                self.log(
                    f"ROUTER: COMBO_CLOSE_MISS | {reason} | "
                    f"Cannot find spread legs in portfolio | "
                    f"Long={long_symbol} Short={short_symbol}"
                )
                return False

            # Retry loop
            for attempt in range(1, config.COMBO_ORDER_MAX_RETRIES + 1):
                try:
                    legs = [
                        Leg.Create(long_qc_symbol, -1),  # Sell long (ratio -1)
                        Leg.Create(short_qc_symbol, 1),  # Buy short (ratio +1)
                    ]

                    self.algorithm.ComboMarketOrder(legs, num_spreads)  # type: ignore[attr-defined]
                    self.log(
                        f"ROUTER: COMBO_CLOSE_SUCCESS | {reason} | "
                        f"Attempt {attempt}/{config.COMBO_ORDER_MAX_RETRIES} | "
                        f"Spreads={num_spreads}"
                    )
                    return True

                except Exception as e:
                    self.log(
                        f"ROUTER: COMBO_CLOSE_RETRY | {reason} | "
                        f"Attempt {attempt}/{config.COMBO_ORDER_MAX_RETRIES} | "
                        f"Error: {e}"
                    )
                    # Continue to next retry

            self.log(
                f"ROUTER: COMBO_CLOSE_EXHAUSTED | {reason} | "
                f"All {config.COMBO_ORDER_MAX_RETRIES} retries failed"
            )
            return False

        except Exception as e:
            self.log(f"ROUTER: COMBO_CLOSE_ERROR | {reason} | {e}")
            return False

    def _execute_sequential_close(
        self,
        long_symbol: str,
        short_symbol: str,
        num_spreads: int,
        reason: str,
    ) -> bool:
        """
        V2.17: Execute sequential close: SHORT first (buy back), then LONG (sell).

        This order prevents naked short exposure during the close.
        Worst case: long leg remains open temporarily.

        Args:
            long_symbol: Symbol of the long leg to sell.
            short_symbol: Symbol of the short leg to buy back.
            num_spreads: Number of spreads to close.
            reason: Reason for close (for logging).

        Returns:
            True if at least short leg was closed, False if both failed.
        """
        if not self.algorithm:
            return False

        # Find actual QC Symbol objects
        long_qc_symbol = None
        short_qc_symbol = None

        for kvp in self.algorithm.Portfolio:  # type: ignore[attr-defined]
            holding = kvp.Value
            if not holding.Invested:
                continue
            symbol_str = str(holding.Symbol)
            if long_symbol in symbol_str and holding.Quantity > 0:
                long_qc_symbol = holding.Symbol
            elif short_symbol in symbol_str and holding.Quantity < 0:
                short_qc_symbol = holding.Symbol

        short_closed = False
        long_closed = False

        # Step 1: Buy back short leg first (eliminates short exposure)
        if short_qc_symbol is not None:
            try:
                self.algorithm.MarketOrder(short_qc_symbol, num_spreads)  # BUY (positive)
                short_closed = True
                self.log(
                    f"ROUTER: SEQUENTIAL_SHORT_CLOSED | {reason} | "
                    f"Bought back {num_spreads} @ {short_symbol}"
                )
            except Exception as e:
                self.log(f"ROUTER: SEQUENTIAL_SHORT_FAIL | {reason} | {e}")
        else:
            self.log(f"ROUTER: SEQUENTIAL_SHORT_NOTFOUND | {reason} | {short_symbol}")

        # Step 2: Sell long leg (after short is closed)
        if long_qc_symbol is not None:
            try:
                self.algorithm.MarketOrder(long_qc_symbol, -num_spreads)  # SELL (negative)
                long_closed = True
                self.log(
                    f"ROUTER: SEQUENTIAL_LONG_CLOSED | {reason} | "
                    f"Sold {num_spreads} @ {long_symbol}"
                )
            except Exception as e:
                self.log(f"ROUTER: SEQUENTIAL_LONG_FAIL | {reason} | {e}")
        else:
            self.log(f"ROUTER: SEQUENTIAL_LONG_NOTFOUND | {reason} | {long_symbol}")

        # Log final state
        if short_closed and long_closed:
            self.log(f"ROUTER: SEQUENTIAL_COMPLETE | {reason}")
            return True
        elif short_closed:
            self.log(
                f"ROUTER: SEQUENTIAL_PARTIAL | {reason} | "
                f"SHORT closed, LONG failed - long exposure remains"
            )
            return True  # Partial success - at least no naked short
        else:
            self.log(f"ROUTER: SEQUENTIAL_FAILED | {reason} | Both legs failed")
            return False

    def _unregister_spread_margin_if_tracked(self, spread_id: str) -> None:
        """
        V2.17: Helper to safely unregister spread margin if it was tracked.

        Args:
            spread_id: Spread identifier (usually long_leg_symbol).
        """
        if spread_id in self._open_spread_margin:
            self.unregister_spread_margin(spread_id)

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

        V2.3.24: Hard Margin Reservation - caps based on MARGIN, not just weight:
        - Allocation reservation (25% weight) ≠ Margin reservation
        - 2× and 3× ETFs consume more margin than their allocation suggests
        - Example: 55% trend allocation × 2.4× avg leverage = 132% margin
        - This version calculates margin-adjusted weights to truly reserve options capacity

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

        # V2.3.24: HARD MARGIN RESERVATION
        # Calculate margin-adjusted allocation for non-options sources
        # This accounts for leverage: 2× ETF at 20% allocation = 40% margin consumption
        max_non_options_margin = 1.0 - config.RESERVED_OPTIONS_PCT  # 75% of margin

        # Calculate margin-weighted total for non-options
        margin_weighted_total = 0.0
        for w in adjusted_weights:
            if w.source in self.NON_OPTIONS_SOURCES:
                leverage = config.SYMBOL_LEVERAGE.get(w.symbol, 1.0)
                margin_weighted_total += w.target_weight * leverage

        # If margin-weighted total exceeds limit, scale down
        if margin_weighted_total > max_non_options_margin:
            margin_scale_factor = max_non_options_margin / margin_weighted_total
            final_weights = []

            for w in adjusted_weights:
                if w.source in self.NON_OPTIONS_SOURCES:
                    # Scale by margin factor to respect hard margin reservation
                    scaled_weight = w.target_weight * margin_scale_factor
                    final_weights.append(
                        TargetWeight(
                            symbol=w.symbol,
                            target_weight=scaled_weight,
                            source=w.source,
                            urgency=w.urgency,
                            reason=f"{w.reason} [margin reserve scaled {margin_scale_factor:.0%}]",
                            requested_quantity=w.requested_quantity,
                            metadata=w.metadata,
                        )
                    )
                else:
                    final_weights.append(w)

            self.log(
                f"ROUTER: HARD_MARGIN_RESERVE | Margin-weighted total {margin_weighted_total:.1%} > "
                f"Max {max_non_options_margin:.1%} | Scaled by {margin_scale_factor:.0%}"
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

            # V2.3.24: Use lower threshold for intraday options
            # Single option contracts often $500-1,500, below the $2,000 MIN_TRADE_VALUE
            min_trade_value = config.MIN_TRADE_VALUE
            if is_option and any(s in ("OPT_INTRADAY", "OPT") for s in agg.sources):
                min_trade_value = config.MIN_INTRADAY_OPTIONS_TRADE_VALUE

            # Skip if position value below minimum trade size (unless closing)
            if abs(delta_value) < min_trade_value and not is_closing:
                # V2.3.24: Throttle rejection logging to reduce spam
                should_log = self._should_log_rejection()
                if should_log:
                    self.log(
                        f"ROUTER: SKIP | {symbol} | "
                        f"Delta ${delta_value:,.0f} < min ${min_trade_value:,}"
                    )
                continue

            # Determine side and order type
            side = OrderSide.BUY if delta_shares > 0 else OrderSide.SELL
            # V2.4.2: Added MOC for same-day trend entries
            if agg.urgency == Urgency.IMMEDIATE:
                order_type = OrderType.MARKET
            elif agg.urgency == Urgency.MOC:
                order_type = OrderType.MOC
            else:  # EOD
                order_type = OrderType.MOO

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

                    # V2.14 Fix #11: SIGN_MISMATCH validation
                    # A valid spread must have one BUY and one SELL (opposite signs)
                    if long_qty > 0 and short_qty > 0:
                        self.log(
                            f"SIGN_MISMATCH: Spread has two BUYS | "
                            f"Long={long_qty} Short={short_qty} | BLOCKED"
                        )
                        continue  # Skip this malformed spread
                    if long_qty < 0 and short_qty < 0:
                        self.log(
                            f"SIGN_MISMATCH: Spread has two SELLS | "
                            f"Long={long_qty} Short={short_qty} | BLOCKED"
                        )
                        continue  # Skip this malformed spread

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

        # V2.4.4 P0: Check margin call cooldown before executing any orders
        # This prevents 2765+ margin call spam seen in V2.4.3 backtest
        if hasattr(self.algorithm, "_margin_call_cooldown_until"):
            cooldown = getattr(self.algorithm, "_margin_call_cooldown_until", None)
            if cooldown and isinstance(cooldown, str):
                try:
                    current_time = str(self.algorithm.Time)  # type: ignore[attr-defined]
                    # Only compare if we got a valid timestamp string
                    if isinstance(current_time, str) and len(current_time) > 10:
                        if current_time < cooldown:
                            self.log(
                                f"ROUTER: MARGIN_COOLDOWN_ACTIVE | "
                                f"Blocked until {cooldown} | Current={current_time}"
                            )
                            return []
                        else:
                            # Cooldown expired, reset
                            self.algorithm._margin_call_cooldown_until = None  # type: ignore[attr-defined]
                            self.algorithm._margin_call_consecutive_count = 0  # type: ignore[attr-defined]
                            self.log("ROUTER: MARGIN_COOLDOWN_EXPIRED | Trading resumed")
                except (TypeError, AttributeError):
                    # In test environments, Time may be mocked - skip cooldown check
                    pass

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
            # V2.3.24: For combo orders, scale contracts to fit margin instead of rejecting
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

                    # V2.9: Use effective margin (accounts for open spread reservations)
                    margin_remaining = self.get_effective_margin_remaining()
                    if order_value > margin_remaining:
                        # V2.3.24: For combo orders, try to scale contracts to fit margin
                        if order.is_combo and is_option:
                            # Calculate max contracts that fit in available margin
                            per_contract_cost = current_price * multiplier
                            if per_contract_cost > 0:
                                max_contracts = int(margin_remaining / per_contract_cost)
                                min_contracts = getattr(config, "MIN_SPREAD_CONTRACTS", 2)

                                if max_contracts >= min_contracts:
                                    # Scale down the order
                                    scale_ratio = max_contracts / order.quantity
                                    order.quantity = max_contracts
                                    # Scale short leg proportionally
                                    if order.combo_short_quantity:
                                        order.combo_short_quantity = int(
                                            order.combo_short_quantity * scale_ratio
                                        )
                                    self.log(
                                        f"ROUTER: COMBO_SCALED | {order.symbol} | "
                                        f"Scaled from original to {max_contracts} contracts | "
                                        f"Margin=${margin_remaining:,.0f}"
                                    )
                                else:
                                    self.log(
                                        f"ROUTER: COMBO_SKIP | {order.symbol} | "
                                        f"Max {max_contracts} < min {min_contracts} contracts | "
                                        f"Margin=${margin_remaining:,.0f}"
                                    )
                                    continue
                            else:
                                continue  # Can't calculate, skip
                        else:
                            # Non-combo order - reject as before
                            self.log(
                                f"ROUTER: INSUFFICIENT_MARGIN | {order.symbol} | "
                                f"Order=${order_value:,.0f} > Margin=${margin_remaining:,.0f} | "
                                f"Qty={order.quantity} @ ${current_price:.2f}"
                            )
                            continue

                    # V2.14 Fix #14: MARGIN_ERROR_TREND - Check trend orders don't exceed
                    # margin after reserving OPTIONS_MAX_MARGIN_CAP for options engine
                    trend_symbols = ["QLD", "SSO", "TNA", "FAS"]
                    symbol_str = str(order.symbol)
                    if symbol_str in trend_symbols:
                        options_reserve = getattr(config, "OPTIONS_MAX_MARGIN_CAP", 10000)
                        margin_after_reserve = margin_remaining - options_reserve
                        if order_value > margin_after_reserve and margin_after_reserve > 0:
                            self.log(
                                f"MARGIN_ERROR_TREND: {symbol_str} x{order.quantity} = ${order_value:,.0f} "
                                f"exceeds available ${margin_after_reserve:,.0f} "
                                f"(reserved ${options_reserve:,.0f} for options)"
                            )
                            continue
                except Exception:
                    pass  # Continue with order if price lookup fails

            try:
                quantity = order.quantity if order.side == OrderSide.BUY else -order.quantity

                # V2.3.9: Handle combo orders for spreads
                # V2.8: Add credit spread margin validation
                if order.is_combo and order.combo_short_symbol and order.combo_short_quantity:
                    # V2.8 SAFETY: Validate credit spread margin before execution
                    if hasattr(order, "metadata") and order.metadata:
                        if order.metadata.get("spread_type") == "CREDIT":
                            width = order.metadata.get("spread_width", 5.0)
                            credit = abs(order.metadata.get("spread_cost_or_credit", 0))
                            num_spreads = abs(order.quantity)
                            margin_per_spread = (width - credit) * 100
                            total_margin = margin_per_spread * num_spreads

                            # SAFETY CHECK: Total margin must not exceed available
                            if total_margin > margin_remaining:
                                self.log(
                                    f"SAFETY BLOCK: Credit spread margin ${total_margin:.0f} "
                                    f"exceeds available ${margin_remaining:.0f} | "
                                    f"Spreads={num_spreads} @ ${margin_per_spread:.0f}/spread"
                                )
                                continue  # Skip this order

                            self.log(
                                f"SAFETY: Credit spread margin validated | "
                                f"Total=${total_margin:.0f} <= Available=${margin_remaining:.0f}"
                            )

                    # Import Leg class for combo orders
                    from AlgorithmImports import Leg

                    # V2.4.1 FIX: Leg.Create takes RATIO, not absolute quantity
                    # For a standard 1:1 spread:
                    #   - Long leg ratio = 1 (BUY 1 per spread)
                    #   - Short leg ratio = -1 (SELL 1 per spread)
                    #   - ComboMarketOrder quantity = number of spreads
                    # OLD BUG: Passing quantity (e.g., 26) as ratio meant 26 × 26 = 676 contracts!
                    num_spreads = abs(order.quantity)
                    long_ratio = 1 if order.side == OrderSide.BUY else -1
                    short_ratio = -1 if order.side == OrderSide.BUY else 1  # Opposite of long

                    legs = [
                        Leg.Create(order.symbol, long_ratio),
                        Leg.Create(order.combo_short_symbol, short_ratio),
                    ]

                    # Submit combo order - broker calculates NET margin (spread margin)
                    self.algorithm.ComboMarketOrder(legs, num_spreads)  # type: ignore[attr-defined]
                    self.log(
                        f"ROUTER: COMBO_MARKET_ORDER | "
                        f"Long={order.symbol} x{num_spreads} (ratio={long_ratio}) + "
                        f"Short={order.combo_short_symbol} x{num_spreads} (ratio={short_ratio})"
                    )

                    # V2.9: Register spread margin reservation (Bug #1 fix)
                    # Track margin locked by this spread to prevent Trend overcommitment
                    if hasattr(order, "metadata") and order.metadata:
                        spread_width = order.metadata.get("spread_width", 5.0)
                        spread_margin = spread_width * 100 * num_spreads
                        is_exit = order.metadata.get("spread_close_short", False)

                        if is_exit:
                            # Closing spread - unregister margin
                            self.unregister_spread_margin(order.symbol)
                        else:
                            # Opening spread - register margin
                            self.register_spread_margin(order.symbol, spread_margin)
                elif order.order_type == OrderType.MARKET:
                    self.algorithm.MarketOrder(order.symbol, quantity)  # type: ignore[attr-defined]
                    self.log(
                        f"ROUTER: MARKET_ORDER | {order.side.value} {order.quantity} {order.symbol}"
                    )
                elif order.order_type == OrderType.MOC:
                    # V2.4.2: Market-On-Close for same-day trend entries
                    self.algorithm.MarketOnCloseOrder(order.symbol, quantity)  # type: ignore[attr-defined]
                    self.log(
                        f"ROUTER: MOC_ORDER | {order.side.value} {order.quantity} {order.symbol}"
                    )
                else:  # MOO
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

        # Extract minute from timestamp for idempotency (e.g., "2021-05-10 09:31:00" -> "2021-05-10 09:31")
        current_minute = current_time[:16] if current_time and len(current_time) >= 16 else None
        executed = self.execute_orders(eod_orders, current_minute)

        self._last_orders = executed
        return executed

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
        # V2.3.24: Reset rejection log throttle
        self._last_rejection_log_time = None
        self._rejection_log_count = 0
        self.log("ROUTER: RESET")
