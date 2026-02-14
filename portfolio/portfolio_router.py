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
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Set, Tuple

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
    metadata: Optional[Dict[str, Any]] = None
    tag: Optional[str] = None

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
        if self.metadata is not None:
            result["metadata"] = self.metadata
        if self.tag:
            result["tag"] = self.tag
        return result


@dataclass
class RouterRejection:
    """Structured rejection record for post-run diagnostics."""

    code: str
    symbol: str
    source_tag: str
    trace_id: str
    detail: str
    stage: str


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
        "OPT": config.OPTIONS_SWING_ALLOCATION,  # Swing options share of total portfolio
        "OPT_INTRADAY": config.OPTIONS_INTRADAY_ALLOCATION,  # Intraday options share
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
        # Runtime source limits (starts from class defaults, then isolation profile can override).
        self._source_allocation_limits = dict(self.SOURCE_ALLOCATION_LIMITS)
        self._apply_isolation_source_limits()
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
        self._last_rejections: List[RouterRejection] = []

        # V2.9: Track open spread margin (Bug #1 fix)
        # Stores {spread_id: margin_reserved} for each open spread
        self._open_spread_margin: Dict[str, float] = {}

    def log(self, message: str) -> None:
        """Log via algorithm or skip for testing."""
        if self.algorithm:
            if message.startswith("MARGIN_TRACK:"):
                is_live = bool(
                    hasattr(self.algorithm, "LiveMode") and self.algorithm.LiveMode  # type: ignore[attr-defined]
                )
                enabled = bool(
                    getattr(
                        config,
                        "MARGIN_TRACK_LOG_LIVE_ENABLED"
                        if is_live
                        else "MARGIN_TRACK_LOG_BACKTEST_ENABLED",
                        is_live,
                    )
                )
                if not enabled:
                    return
            self.algorithm.Log(message)  # type: ignore[attr-defined]

    def _apply_isolation_source_limits(self) -> None:
        """
        In isolation mode, disabled engines should not retain budget.
        Normalize enabled source limits to 100% so capacity reflects active engines only.
        """
        if not getattr(config, "ISOLATION_TEST_MODE", False):
            return

        enabled_weights: Dict[str, float] = {}
        if getattr(config, "ISOLATION_OPTIONS_ENABLED", False):
            enabled_weights["OPT"] = float(getattr(config, "OPTIONS_SWING_ALLOCATION", 0.0))
            enabled_weights["OPT_INTRADAY"] = float(
                getattr(config, "OPTIONS_INTRADAY_ALLOCATION", 0.0)
            )
        if getattr(config, "ISOLATION_TREND_ENABLED", False):
            enabled_weights["TREND"] = float(getattr(config, "TREND_TOTAL_ALLOCATION", 0.0))
        if getattr(config, "ISOLATION_MR_ENABLED", False):
            enabled_weights["MR"] = float(getattr(config, "MR_TOTAL_ALLOCATION", 0.0))
        if getattr(config, "ISOLATION_HEDGE_ENABLED", False):
            enabled_weights["HEDGE"] = 0.30
        if getattr(config, "ISOLATION_COLD_START_ENABLED", False):
            enabled_weights["COLD_START"] = 0.35

        total = sum(v for v in enabled_weights.values() if v > 0)
        if total <= 0:
            return

        # Zero out controllable engine budgets first.
        for source in ("TREND", "OPT", "OPT_INTRADAY", "MR", "HEDGE", "COLD_START"):
            self._source_allocation_limits[source] = 0.0

        # Normalize enabled budgets to 100%.
        for source, value in enabled_weights.items():
            if value > 0:
                self._source_allocation_limits[source] = value / total

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

    def _build_spread_margin_key(self, long_symbol: str, short_symbol: Optional[str] = None) -> str:
        """Build stable margin key for a spread reservation."""
        long_str = str(long_symbol or "").strip()
        short_str = str(short_symbol or "").strip()
        return f"{long_str}|{short_str}" if short_str else long_str

    def register_spread_margin(
        self,
        spread_id: str,
        margin_reserved: float,
        short_symbol: Optional[str] = None,
    ) -> None:
        """
        V2.9: Register margin reserved by an open spread position.

        Args:
            spread_id: Long leg symbol or spread id.
            margin_reserved: Margin requirement for this spread.
            short_symbol: Optional short leg symbol for stable composite keying.
        """
        key = self._build_spread_margin_key(spread_id, short_symbol)
        self._open_spread_margin[key] = margin_reserved
        self.log(f"MARGIN_TRACK: Registered spread | ID={key} | Reserved=${margin_reserved:,.0f}")

    def unregister_spread_margin(self, spread_id: str) -> None:
        """Unregister margin when a spread position is closed (direct key)."""
        if spread_id in self._open_spread_margin:
            freed = self._open_spread_margin.pop(spread_id)
            self.log(f"MARGIN_TRACK: Unregistered spread | ID={spread_id} | Freed=${freed:,.0f}")

    def unregister_spread_margin_by_legs(
        self,
        long_symbol: str,
        short_symbol: Optional[str] = None,
    ) -> None:
        """Unregister spread margin by long/short symbols with legacy-key compatibility."""
        long_str = str(long_symbol or "").strip()
        short_str = str(short_symbol or "").strip()
        if not long_str:
            return

        candidates = {self._build_spread_margin_key(long_str, short_str), long_str}
        removed = 0
        for key in list(candidates):
            if key in self._open_spread_margin:
                self.unregister_spread_margin(key)
                removed += 1

        # Legacy fallback: remove any key anchored by this long leg.
        legacy_prefix = f"{long_str}|"
        for key in list(self._open_spread_margin.keys()):
            if key.startswith(legacy_prefix):
                self.unregister_spread_margin(key)
                removed += 1

        if removed == 0:
            self.log(
                f"MARGIN_TRACK: Unregister skipped | Long={long_str} Short={short_str or 'NA'} | No matching reservation"
            )

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

    def get_options_budget_cap(self) -> float:
        """
        V8.2: Hard options entry budget.

        Uses a single percentage-of-equity cap for options exposure to keep
        normal entry gating simple and aligned with configured partition.
        """
        if not self.algorithm:
            return 0.0
        total_equity = self.algorithm.Portfolio.TotalPortfolioValue
        cap_pct = float(getattr(config, "OPTIONS_BUDGET_CAP_PCT", config.CAPITAL_PARTITION_OPTIONS))
        return max(0.0, total_equity * cap_pct)

    def get_options_budget_used(self) -> float:
        """
        V8.2: Current options budget used.

        For swing spreads we use tracked reserved margin (width-based), which is
        stable and avoids mark-to-market noise. This is intentionally simple and
        avoids double-counting spread legs.
        """
        return max(0.0, self.get_reserved_spread_margin())

    def _is_option_symbol(self, symbol: str) -> bool:
        """Heuristic option symbol detector used in router guards."""
        sym = str(symbol).strip()
        return len(sym) > 10 and ("C0" in sym or "P0" in sym)

    def _get_live_option_qty(self, symbol: str) -> int:
        """Return live portfolio quantity for symbol (0 when flat/not found)."""
        if not self.algorithm:
            return 0
        target = str(symbol).strip()
        try:
            for kvp in self.algorithm.Portfolio:  # type: ignore[attr-defined]
                holding = kvp.Value
                if str(holding.Symbol).strip() == target:
                    return int(holding.Quantity)
        except Exception:
            return 0
        return 0

    def _is_option_close_order(self, order: "OrderIntent") -> bool:
        """
        True when an option order reduces/close existing exposure.
        Close orders must always be allowed through budget gate.
        """
        if not self._is_option_symbol(order.symbol):
            return False

        if order.is_combo and bool(
            order.metadata and order.metadata.get("spread_close_short", False)
        ):
            return True

        live_qty = self._get_live_option_qty(order.symbol)
        if live_qty == 0:
            return False

        return (order.side == OrderSide.BUY and live_qty < 0) or (
            order.side == OrderSide.SELL and live_qty > 0
        )

    def _estimate_option_order_margin_requirement(self, order: "OrderIntent") -> float:
        """
        Estimate incremental margin/capital usage for an option BUY entry.
        """
        if order.is_combo:
            per_contract_margin, _ = self._estimate_combo_margin_per_contract(order.metadata)
            if per_contract_margin > 0:
                return per_contract_margin * abs(order.quantity)
            return 0.0

        if not self.algorithm:
            return 0.0
        try:
            sec = self.algorithm.Securities[order.symbol]  # type: ignore[attr-defined]
            price = float(getattr(sec, "Price", 0.0) or 0.0)
            ask = float(getattr(sec, "AskPrice", 0.0) or 0.0)
            if ask > 0:
                price = ask
            if price <= 0:
                return 0.0
            return abs(order.quantity) * price * 100.0
        except Exception:
            return 0.0

    def check_options_budget_gate(self, order: "OrderIntent") -> Tuple[bool, str]:
        """
        V8.2: Primary options gate.

        Blocks new options BUY entries that exceed the configured options budget.
        """
        if not getattr(config, "OPTIONS_BUDGET_GATE_ENABLED", True):
            return True, ""

        if not self._is_option_symbol(order.symbol):
            return True, ""

        # Never block risk-reduction orders.
        if self._is_option_close_order(order):
            return True, ""

        # Options entries are submitted as BUY for combo open and long-option open.
        if order.side != OrderSide.BUY:
            return True, ""

        cap = self.get_options_budget_cap()
        if cap <= 0:
            return True, ""

        used = self.get_options_budget_used()
        required = self._estimate_option_order_margin_requirement(order)
        if required <= 0:
            return True, ""

        warn_pct = float(getattr(config, "OPTIONS_BUDGET_WARN_PCT", 0.90))
        warn_level = cap * max(0.0, warn_pct)
        if used >= warn_level:
            self.log(
                f"OPTIONS_BUDGET_WARN: Used=${used:,.0f} / Cap=${cap:,.0f} " f"({(used / cap):.1%})"
            )

        projected = used + required
        if projected > cap:
            reason = (
                f"OPTIONS_BUDGET_GATE: BLOCKED | Used=${used:,.0f} + "
                f"Req=${required:,.0f} > Cap=${cap:,.0f} ({(projected / cap):.1%})"
            )
            return False, reason

        return True, ""

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

        try:
            total_equity = self.algorithm.Portfolio.TotalPortfolioValue
            margin_used = self.algorithm.Portfolio.TotalMarginUsed

            # Handle case where values are mocks or invalid
            if not isinstance(total_equity, (int, float)) or not isinstance(
                margin_used, (int, float)
            ):
                return 0.0

            if total_equity <= 0:
                return 0.0

            return margin_used / total_equity
        except (TypeError, AttributeError):
            # In test environments with mocks, return 0 (no margin usage)
            return 0.0

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
    # V4.0.2: MARGIN UTILIZATION GATE
    # =========================================================================

    def check_margin_utilization_gate(self, is_buy_order: bool = True) -> Tuple[bool, str]:
        """
        V4.0.2/V8.2: Emergency margin-utilization circuit breaker.

        Uses broker ACTUAL margin numbers (TotalMarginUsed / TotalPortfolioValue).
        In V8.2 this gate is intentionally an emergency brake only; normal
        options throttling is handled by options budget gate.

        Reliable because:
        1. Uses broker's actual margin model (includes all factors)
        2. Accounts for ALL positions (equities, options, everything)
        3. Dynamically adjusts as market conditions change
        4. Prevents margin overflow that caused the March 2017 death spiral

        Args:
            is_buy_order: True for BUY orders (which increase margin usage).
                         SELL orders are always allowed (they reduce exposure).

        Returns:
            Tuple of (allowed: bool, reason: str)
            - allowed: True if margin utilization is below threshold
            - reason: Explanation if blocked or empty string if allowed
        """
        # SELL orders are always allowed (they reduce margin usage)
        if not is_buy_order:
            return (True, "")

        # Check if gate is enabled
        if not getattr(config, "MARGIN_UTILIZATION_ENABLED", True):
            return (True, "")

        if not self.algorithm:
            return (True, "")

        # Get current margin utilization from broker's actual numbers
        utilization = self.get_current_margin_usage()
        max_util = getattr(config, "MAX_MARGIN_UTILIZATION", 0.70)
        warn_util = getattr(config, "MARGIN_UTILIZATION_WARNING", 0.60)

        # Block BUY orders if utilization exceeds maximum
        if utilization >= max_util:
            reason = (
                f"MARGIN_UTIL_GATE: BLOCKED | Utilization={utilization:.1%} >= "
                f"Max={max_util:.0%} | New BUY orders blocked to prevent margin overflow"
            )
            self.log(reason)
            return (False, reason)

        # Warn if utilization is approaching limit
        if utilization >= warn_util:
            self.log(
                f"MARGIN_UTIL_GATE: WARNING | Utilization={utilization:.1%} approaching "
                f"limit {max_util:.0%}"
            )

        return (True, "")

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
            combo_success = self._try_combo_close(
                long_symbol, short_symbol, num_spreads, reason, tag=f"SPREAD_CLOSE_COMBO|{reason}"
            )
            if combo_success:
                self._unregister_spread_margin_if_tracked(long_symbol, short_symbol)
                return True

        # Fallback to sequential close (margin-safe: short first, then long)
        self.log(
            f"ROUTER: SEQUENTIAL_FALLBACK | {reason} | "
            f"Closing short first (buy back), then long (sell)"
        )

        sequential_success = self._execute_sequential_close(
            long_symbol, short_symbol, num_spreads, reason, tag_prefix=f"SPREAD_CLOSE_SEQ|{reason}"
        )

        if sequential_success:
            self._unregister_spread_margin_if_tracked(long_symbol, short_symbol)
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
        tag: str = "SPREAD_CLOSE_COMBO",
    ) -> bool:
        """
        V2.17: Attempt atomic combo close with retries.

        Uses live holdings quantities to avoid stale num_spreads over/under-close.
        """
        if not self.algorithm:
            return False

        try:
            from AlgorithmImports import Leg

            # Find actual QC Symbol objects + live quantities from portfolio
            long_qc_symbol = None
            short_qc_symbol = None
            live_long_qty = 0
            live_short_qty = 0

            for kvp in self.algorithm.Portfolio:  # type: ignore[attr-defined]
                holding = kvp.Value
                if not holding.Invested:
                    continue
                symbol_str = str(holding.Symbol)
                if long_symbol in symbol_str and holding.Quantity > 0:
                    long_qc_symbol = holding.Symbol
                    live_long_qty = int(holding.Quantity)
                elif short_symbol in symbol_str and holding.Quantity < 0:
                    short_qc_symbol = holding.Symbol
                    live_short_qty = abs(int(holding.Quantity))

            if long_qc_symbol is None or short_qc_symbol is None:
                self.log(
                    f"ROUTER: COMBO_CLOSE_MISS | {reason} | "
                    f"Cannot find spread legs in portfolio | "
                    f"Long={long_symbol} Short={short_symbol}"
                )
                return False

            if live_long_qty <= 0 or live_short_qty <= 0:
                self.log(
                    f"ROUTER: COMBO_CLOSE_QTY_INVALID | {reason} | "
                    f"LongQty={live_long_qty} ShortQty={live_short_qty}"
                )
                return False

            if live_long_qty != live_short_qty:
                self.log(
                    f"ROUTER: COMBO_CLOSE_QTY_MISMATCH | {reason} | "
                    f"LongQty={live_long_qty} ShortQty={live_short_qty} | "
                    f"Falling back to sequential"
                )
                return False

            close_qty = live_long_qty

            # Retry loop
            for attempt in range(1, config.COMBO_ORDER_MAX_RETRIES + 1):
                try:
                    legs = [
                        Leg.Create(long_qc_symbol, -1),  # Sell long (ratio -1)
                        Leg.Create(short_qc_symbol, 1),  # Buy short (ratio +1)
                    ]

                    try:
                        self.algorithm.ComboMarketOrder(legs, close_qty, tag=tag)  # type: ignore[attr-defined]
                    except TypeError:
                        self.algorithm.ComboMarketOrder(legs, close_qty, tag)  # type: ignore[attr-defined]
                    self.log(
                        f"ROUTER: COMBO_CLOSE_SUCCESS | {reason} | "
                        f"Attempt {attempt}/{config.COMBO_ORDER_MAX_RETRIES} | "
                        f"Spreads={close_qty}"
                    )
                    return True

                except Exception as e:
                    self.log(
                        f"ROUTER: COMBO_CLOSE_RETRY | {reason} | "
                        f"Attempt {attempt}/{config.COMBO_ORDER_MAX_RETRIES} | "
                        f"Error: {e}"
                    )

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
        tag_prefix: str = "SPREAD_CLOSE_SEQ",
    ) -> bool:
        """
        V2.17: Execute sequential close: SHORT first (buy back), then LONG (sell).

        Uses live leg quantities so stale spread counts cannot over/under-close.
        """
        if not self.algorithm:
            return False

        # Find actual QC Symbol objects + live quantities
        long_qc_symbol = None
        short_qc_symbol = None
        live_long_qty = 0
        live_short_qty = 0

        for kvp in self.algorithm.Portfolio:  # type: ignore[attr-defined]
            holding = kvp.Value
            if not holding.Invested:
                continue
            symbol_str = str(holding.Symbol)
            if long_symbol in symbol_str and holding.Quantity > 0:
                long_qc_symbol = holding.Symbol
                live_long_qty = int(holding.Quantity)
            elif short_symbol in symbol_str and holding.Quantity < 0:
                short_qc_symbol = holding.Symbol
                live_short_qty = abs(int(holding.Quantity))

        short_close_qty = live_short_qty
        long_close_qty = live_long_qty

        short_closed = False
        long_closed = False

        # Step 1: Buy back short leg first (eliminates short exposure)
        if short_qc_symbol is not None and short_close_qty > 0:
            try:
                try:
                    self.algorithm.MarketOrder(
                        short_qc_symbol, short_close_qty, tag=f"{tag_prefix}|SHORT"
                    )  # BUY (positive)
                except TypeError:
                    self.algorithm.MarketOrder(
                        short_qc_symbol, short_close_qty, f"{tag_prefix}|SHORT"
                    )  # BUY (positive)
                short_closed = True
                self.log(
                    f"ROUTER: SEQUENTIAL_SHORT_CLOSED | {reason} | "
                    f"Bought back {short_close_qty} @ {short_symbol}"
                )
            except Exception as e:
                self.log(f"ROUTER: SEQUENTIAL_SHORT_FAIL | {reason} | {e}")
        else:
            self.log(
                f"ROUTER: SEQUENTIAL_SHORT_NOTFOUND | {reason} | {short_symbol} | "
                f"LiveQty={live_short_qty}"
            )

        # Step 2: Sell long leg (after short is closed)
        if long_qc_symbol is not None and long_close_qty > 0:
            try:
                try:
                    self.algorithm.MarketOrder(
                        long_qc_symbol, -long_close_qty, tag=f"{tag_prefix}|LONG"
                    )  # SELL (negative)
                except TypeError:
                    self.algorithm.MarketOrder(
                        long_qc_symbol, -long_close_qty, f"{tag_prefix}|LONG"
                    )  # SELL (negative)
                long_closed = True
                self.log(
                    f"ROUTER: SEQUENTIAL_LONG_CLOSED | {reason} | "
                    f"Sold {long_close_qty} @ {long_symbol}"
                )
            except Exception as e:
                self.log(f"ROUTER: SEQUENTIAL_LONG_FAIL | {reason} | {e}")
        else:
            self.log(
                f"ROUTER: SEQUENTIAL_LONG_NOTFOUND | {reason} | {long_symbol} | "
                f"LiveQty={live_long_qty}"
            )

        if short_closed and long_closed:
            self.log(f"ROUTER: SEQUENTIAL_COMPLETE | {reason}")
            return True
        elif short_closed:
            self.log(
                f"ROUTER: SEQUENTIAL_PARTIAL | {reason} | "
                f"SHORT closed, LONG failed - long exposure remains"
            )
            return True
        else:
            self.log(f"ROUTER: SEQUENTIAL_FAILED | {reason} | Both legs failed")
            return False

    def _unregister_spread_margin_if_tracked(
        self,
        long_symbol: str,
        short_symbol: Optional[str] = None,
    ) -> None:
        """Helper to safely unregister spread margin by spread legs."""
        self.unregister_spread_margin_by_legs(
            str(long_symbol), str(short_symbol) if short_symbol else None
        )

    # =========================================================================
    # V2.19: LIMIT ORDER EXECUTION (Slippage Protection)
    # =========================================================================

    def validate_options_spread(
        self,
        long_bid: float,
        long_ask: float,
        short_bid: float,
        short_ask: float,
        direction: str = "DEBIT",
    ) -> tuple[bool, str]:
        """
        V2.19: Validate spread prices before execution.

        Checks for:
        1. Invalid prices (zero or negative)
        2. Spread too wide (illiquid)
        3. Bad tick / inverted pricing

        Args:
            long_bid: Bid price of long leg.
            long_ask: Ask price of long leg.
            short_bid: Bid price of short leg.
            short_ask: Ask price of short leg.
            direction: "DEBIT" or "CREDIT".

        Returns:
            Tuple of (is_valid, reason).
        """
        # Check for invalid prices
        if long_bid <= 0 or long_ask <= 0:
            return False, f"BAD_TICK: Long leg invalid prices | Bid={long_bid} Ask={long_ask}"

        if short_bid <= 0 or short_ask <= 0:
            return False, f"BAD_TICK: Short leg invalid prices | Bid={short_bid} Ask={short_ask}"

        # Check spread width (illiquidity check)
        max_spread_pct = getattr(config, "OPTIONS_MAX_SPREAD_PCT", 0.20)

        long_mid = (long_bid + long_ask) / 2
        long_spread_pct = (long_ask - long_bid) / long_mid if long_mid > 0 else 1.0
        if long_spread_pct > max_spread_pct:
            return (
                False,
                f"ILLIQUID: Long leg spread {long_spread_pct:.1%} > {max_spread_pct:.0%} | "
                f"Bid={long_bid:.2f} Ask={long_ask:.2f}",
            )

        short_mid = (short_bid + short_ask) / 2
        short_spread_pct = (short_ask - short_bid) / short_mid if short_mid > 0 else 1.0
        if short_spread_pct > max_spread_pct:
            return (
                False,
                f"ILLIQUID: Short leg spread {short_spread_pct:.1%} > {max_spread_pct:.0%} | "
                f"Bid={short_bid:.2f} Ask={short_ask:.2f}",
            )

        # Bad tick guard: Check for inverted pricing on debit spreads
        # For debit spreads: Long should be more expensive than Short
        # If Short premium > Long premium, prices are broken
        if direction == "DEBIT":
            # We're BUYING long (pay Ask) and SELLING short (receive Bid)
            # Net debit = Long_Ask - Short_Bid
            # If Short_Bid > Long_Ask, we'd get a CREDIT for a DEBIT spread = bad tick
            if short_bid > long_ask:
                return (
                    False,
                    f"BAD_TICK_GUARD: Inverted spread pricing | "
                    f"Short_Bid=${short_bid:.2f} > Long_Ask=${long_ask:.2f} | BLOCKING",
                )

        return True, "VALIDATED"

    def calculate_limit_price(
        self,
        symbol: str,
        quantity: int,
    ) -> tuple[Optional[float], str]:
        """
        V2.19: Calculate marketable limit price for options order.

        Uses Ask + slippage for BUYs, Bid - slippage for SELLs.
        This ensures high fill rate while rejecting clearly broken prices.

        Args:
            symbol: Option symbol.
            quantity: Order quantity (positive = BUY, negative = SELL).

        Returns:
            Tuple of (limit_price or None if blocked, reason).
        """
        if not self.algorithm:
            return None, "NO_ALGORITHM"

        try:
            security = self.algorithm.Securities.get(symbol)
            if security is None:
                symbol_str = str(symbol)
                for sec in self.algorithm.Securities.Values:  # type: ignore[attr-defined]
                    if str(sec.Symbol) == symbol_str:
                        security = sec
                        break
            if security is None:
                return None, f"SYMBOL_NOT_FOUND: {symbol}"

            bid = security.BidPrice
            ask = security.AskPrice

            # Check for invalid prices
            if bid <= 0 or ask <= 0:
                return None, f"BAD_TICK: Invalid prices | Bid={bid} Ask={ask}"

            spread = ask - bid
            mid = (bid + ask) / 2

            # Check spread width (illiquidity)
            max_spread_pct = getattr(config, "OPTIONS_MAX_SPREAD_PCT", 0.20)
            spread_pct = spread / mid if mid > 0 else 1.0
            if spread_pct > max_spread_pct:
                return (
                    None,
                    f"ILLIQUID: Spread {spread_pct:.1%} > {max_spread_pct:.0%} | "
                    f"Bid={bid:.2f} Ask={ask:.2f}",
                )

            # Calculate limit price with slippage tolerance
            slippage_pct = getattr(config, "OPTIONS_LIMIT_SLIPPAGE_PCT", 0.05)
            slippage = spread * slippage_pct

            if quantity > 0:  # BUY - willing to pay up to Ask + slippage
                limit_price = ask + slippage
            else:  # SELL - willing to sell down to Bid - slippage
                limit_price = max(0.01, bid - slippage)  # Never negative

            self.log(
                f"LIMIT_PRICE: {symbol} | Qty={quantity} | "
                f"Bid={bid:.2f} | Ask={ask:.2f} | Limit={limit_price:.2f} | "
                f"Slippage={slippage_pct:.0%}"
            )

            return limit_price, "OK"

        except Exception as e:
            return None, f"ERROR: {e}"

    def execute_options_limit_order(
        self,
        symbol: str,
        quantity: int,
        reason: str = "OPTIONS_ORDER",
        tag: Optional[str] = None,
    ) -> bool:
        """
        V2.19: Execute options order using marketable limit.

        Uses Ask + slippage for BUYs, Bid - slippage for SELLs.
        This ensures high fill rate while rejecting clearly broken prices.

        Args:
            symbol: Option symbol.
            quantity: Order quantity (positive = BUY, negative = SELL).
            reason: Reason for order (for logging).

        Returns:
            True if order was submitted, False if blocked.
        """
        if not self.algorithm:
            self.log(f"LIMIT_ORDER_FAIL: {reason} | No algorithm")
            return False

        # Check if limit orders are enabled
        use_limits = getattr(config, "OPTIONS_USE_LIMIT_ORDERS", True)
        if not use_limits:
            # Fall back to market order
            try:
                if tag:
                    try:
                        self.algorithm.MarketOrder(symbol, quantity, tag=tag)
                    except TypeError:
                        self.algorithm.MarketOrder(symbol, quantity)
                else:
                    self.algorithm.MarketOrder(symbol, quantity)
                self.log(f"MARKET_ORDER: {symbol} | Qty={quantity} | {reason}")
                return True
            except Exception as e:
                self.log(f"MARKET_ORDER_FAIL: {symbol} | {e}")
                return False

        # Calculate limit price
        limit_price, calc_reason = self.calculate_limit_price(symbol, quantity)

        if limit_price is None:
            self.log(f"LIMIT_ORDER_BLOCKED: {symbol} | {calc_reason} | {reason}")
            return False

        try:
            if tag:
                try:
                    self.algorithm.LimitOrder(symbol, quantity, limit_price, tag=tag)
                except TypeError:
                    self.algorithm.LimitOrder(symbol, quantity, limit_price)
            else:
                self.algorithm.LimitOrder(symbol, quantity, limit_price)
            self.log(
                f"LIMIT_ORDER: {symbol} | Qty={quantity} | " f"Limit=${limit_price:.2f} | {reason}"
            )
            return True
        except Exception as e:
            self.log(f"LIMIT_ORDER_FAIL: {symbol} | {e}")
            return False

    def _estimate_combo_margin_per_contract(
        self,
        metadata: Optional[Dict[str, Any]],
    ) -> Tuple[float, str]:
        """
        Estimate per-contract spread margin from signal metadata.
        Returns (margin_per_contract, reason_code).
        """
        if not metadata:
            return 0.0, "ROUTER_MARGIN_META_MISSING"

        def _strike_from_occ_symbol(symbol_text: str) -> Optional[float]:
            # OCC option symbol suffix encodes strike as 8 digits with 3 implied decimals.
            # Example: QQQ 211220C00391000 -> 391.000
            if not symbol_text:
                return None
            text = str(symbol_text).strip()
            if len(text) < 8:
                return None
            suffix = text[-8:]
            if not suffix.isdigit():
                return None
            try:
                return float(int(suffix)) / 1000.0
            except Exception:
                return None

        try:
            width = float(metadata.get("spread_width", 0.0))
        except (TypeError, ValueError):
            width = 0.0
        if width <= 0:
            long_symbol = str(metadata.get("spread_long_leg_symbol", "")).strip()
            short_symbol = str(metadata.get("spread_short_leg_symbol", "")).strip()
            long_strike = _strike_from_occ_symbol(long_symbol)
            short_strike = _strike_from_occ_symbol(short_symbol)
            if long_strike is not None and short_strike is not None:
                width = abs(long_strike - short_strike)
                if width > 0:
                    self.log(
                        f"ROUTER_MARGIN_WIDTH_FALLBACK: Derived width=${width:.2f} "
                        f"from legs {long_symbol} / {short_symbol}"
                    )
        if width <= 0:
            return 0.0, "ROUTER_MARGIN_WIDTH_INVALID"

        spread_type = str(metadata.get("spread_type", "")).upper()
        is_credit = bool(metadata.get("is_credit_spread", False)) or ("CREDIT" in spread_type)
        credit_received = 0.0
        if is_credit:
            raw_credit = metadata.get(
                "spread_credit_received", metadata.get("spread_cost_or_credit", 0)
            )
            try:
                credit_received = max(0.0, float(raw_credit))
            except (TypeError, ValueError):
                credit_received = 0.0
            base_margin = max(1.0, (width - credit_received) * 100.0)
        else:
            base_margin = width * 100.0

        # Keep router behavior consistent with engine's usable-margin safety factor.
        safety = max(float(getattr(config, "SPREAD_MARGIN_SAFETY_FACTOR", 0.80)), 0.01)
        margin_per_contract = base_margin / safety
        return margin_per_contract, "OK"

    def _validate_combo_entry_quotes(self, order: "OrderIntent") -> Tuple[bool, str]:
        """
        Validate combo ENTRY quotes before submit:
        - long leg (buy) must have valid ask
        - short leg (sell) must have valid bid
        """
        if not self.algorithm:
            return True, "NO_ALGO"
        if not (order.is_combo and order.combo_short_symbol):
            return True, "NOT_COMBO"
        is_exit_combo = bool(order.metadata and order.metadata.get("spread_close_short", False))

        try:
            long_sec = self.algorithm.Securities[order.symbol]  # type: ignore[attr-defined]
            short_sec = self.algorithm.Securities[order.combo_short_symbol]  # type: ignore[attr-defined]
            if is_exit_combo:
                # Exit combo: sell long leg (needs bid), buy short leg (needs ask)
                long_bid = float(getattr(long_sec, "BidPrice", 0.0) or 0.0)
                short_ask = float(getattr(short_sec, "AskPrice", 0.0) or 0.0)
                if long_bid <= 0 or short_ask <= 0:
                    return (
                        False,
                        f"ExitLongBid={long_bid:.4f} ExitShortAsk={short_ask:.4f} "
                        f"Long={order.symbol} Short={order.combo_short_symbol}",
                    )
                return True, "EXIT_OK"

            long_ask = float(getattr(long_sec, "AskPrice", 0.0) or 0.0)
            short_bid = float(getattr(short_sec, "BidPrice", 0.0) or 0.0)
            if long_ask <= 0 or short_bid <= 0:
                return (
                    False,
                    f"LongAsk={long_ask:.4f} ShortBid={short_bid:.4f} "
                    f"Long={order.symbol} Short={order.combo_short_symbol}",
                )
            return True, "OK"
        except Exception as e:
            return False, f"QUOTE_LOOKUP_ERROR: {e}"

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

    def clear_last_rejections(self) -> None:
        """Clear structured router rejection buffer."""
        self._last_rejections = []

    def get_last_rejections(self) -> List[RouterRejection]:
        """Get a copy of latest structured router rejections."""
        return list(self._last_rejections)

    def _record_rejection(
        self,
        code: str,
        symbol: str,
        detail: str,
        stage: str,
        source_tag: str = "",
        trace_id: str = "",
    ) -> None:
        """Track and log router rejection with source/trace context."""
        item = RouterRejection(
            code=code,
            symbol=symbol,
            source_tag=source_tag,
            trace_id=trace_id,
            detail=detail,
            stage=stage,
        )
        self._last_rejections.append(item)
        self.log(
            f"ROUTER_REJECT: Code={code} | Stage={stage} | Symbol={symbol} | "
            f"Source={source_tag or 'UNKNOWN'} | Trace={trace_id or 'NONE'} | {detail}"
        )

    def _extract_trace_context(
        self,
        metadata: Optional[Dict[str, Any]],
        sources: Optional[List[str]] = None,
    ) -> Tuple[str, str]:
        """Derive source tag and trace id for diagnostics."""
        source_tag = ""
        trace_id = ""
        if metadata:
            trace_id = str(metadata.get("trace_id", "") or "")
            source_tag = str(metadata.get("trace_source", "") or "")
            if not source_tag:
                if metadata.get("intraday_strategy"):
                    source_tag = f"MICRO:{metadata.get('intraday_strategy')}"
                elif metadata.get("vass_strategy"):
                    source_tag = f"VASS:{metadata.get('vass_strategy')}"
                elif metadata.get("spread_type"):
                    source_tag = f"VASS:{metadata.get('spread_type')}"
        if not source_tag and sources:
            if "OPT_INTRADAY" in sources:
                source_tag = "MICRO"
            elif "OPT" in sources:
                source_tag = "VASS"
        return source_tag, trace_id

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
            limit = self._source_allocation_limits.get(source, 0.50)

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
            adjusted_weights = final_weights

        # V3.0: TOTAL ALLOCATION CAP with priority-based scaling
        # Ensure total allocation across all engines doesn't exceed MAX_TOTAL_ALLOCATION
        total_allocation = sum(w.target_weight for w in adjusted_weights)
        max_total = getattr(config, "MAX_TOTAL_ALLOCATION", 0.95)

        if total_allocation > max_total:
            # Priority-based scaling: reduce lower priority engines first
            engine_priority = getattr(config, "ENGINE_PRIORITY", {})

            # Group by priority
            by_priority: Dict[int, List[TargetWeight]] = {}
            for w in adjusted_weights:
                priority = engine_priority.get(w.source, 5)  # Default to low priority
                if priority not in by_priority:
                    by_priority[priority] = []
                by_priority[priority].append(w)

            # Calculate how much we need to reduce
            excess = total_allocation - max_total
            final_weights = []

            # Start from lowest priority (highest number) and scale down
            for priority in sorted(by_priority.keys(), reverse=True):
                priority_weights = by_priority[priority]
                priority_total = sum(w.target_weight for w in priority_weights)

                if excess <= 0:
                    # No more reduction needed, keep as-is
                    final_weights.extend(priority_weights)
                elif priority_total <= excess:
                    # Zero out this entire priority level
                    for w in priority_weights:
                        final_weights.append(
                            TargetWeight(
                                symbol=w.symbol,
                                target_weight=0.0,
                                source=w.source,
                                urgency=w.urgency,
                                reason=f"{w.reason} [priority scaled to 0]",
                                requested_quantity=w.requested_quantity,
                                metadata=w.metadata,
                            )
                        )
                    self.log(
                        f"ROUTER: PRIORITY_SCALE | Priority {priority} ({w.source}) | "
                        f"Zeroed {priority_total:.1%} to reduce excess"
                    )
                    excess -= priority_total
                else:
                    # Partial reduction of this priority level
                    scale_factor = (priority_total - excess) / priority_total
                    for w in priority_weights:
                        scaled_weight = w.target_weight * scale_factor
                        final_weights.append(
                            TargetWeight(
                                symbol=w.symbol,
                                target_weight=scaled_weight,
                                source=w.source,
                                urgency=w.urgency,
                                reason=f"{w.reason} [priority scaled {scale_factor:.0%}]",
                                requested_quantity=w.requested_quantity,
                                metadata=w.metadata,
                            )
                        )
                    self.log(
                        f"ROUTER: PRIORITY_SCALE | Priority {priority} ({w.source}) | "
                        f"Scaled by {scale_factor:.0%} to fit total cap"
                    )
                    excess = 0

            self.log(
                f"ROUTER: TOTAL_ALLOCATION_CAP | Total {total_allocation:.1%} > "
                f"Max {max_total:.1%} | Applied priority-based scaling"
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

    def _try_bid_ask_mid_price(self, symbol_str: str) -> float:
        """
        V2.24.1: Try to get Bid/Ask mid-price from QC Securities.

        Last-resort fallback when both current_prices and metadata fail.
        Searches Securities for a matching symbol string and computes
        (BidPrice + AskPrice) / 2.

        Args:
            symbol_str: Symbol string to look up.

        Returns:
            Mid-price if Bid/Ask available, 0.0 otherwise.
        """
        if not self.algorithm:
            return 0.0
        try:
            # For equities, QC Securities can be accessed by string ticker
            # For options, we need to search by matching the symbol string
            for kvp in self.algorithm.Securities:  # type: ignore[attr-defined]
                sec_symbol = kvp.Key
                if str(sec_symbol.Value) == symbol_str or str(sec_symbol) == symbol_str:
                    security = kvp.Value
                    bid = float(security.BidPrice)
                    ask = float(security.AskPrice)
                    if bid > 0 and ask > 0:
                        mid = (bid + ask) / 2.0
                        self.log(
                            f"ROUTER: BIDASK_INJECT | {symbol_str} | "
                            f"mid=${mid:.4f} (bid=${bid:.4f} ask=${ask:.4f})"
                        )
                        return mid
                    break  # Found symbol but no valid bid/ask
        except Exception as e:
            self.log(f"ROUTER: BIDASK_LOOKUP_FAIL | {symbol_str} | {e}")
        return 0.0

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
            source_tag, trace_id = self._extract_trace_context(agg.metadata, agg.sources)
            # Skip if no price available
            if symbol not in current_prices or current_prices[symbol] <= 0:
                # V2.24: Failsafe — use metadata price for options entries
                # The V2.19 injection in main.py should have added this price,
                # but as a belt-and-suspenders, check metadata directly.
                if agg.metadata and agg.metadata.get("contract_price", 0) > 0:
                    price_from_meta = agg.metadata["contract_price"]
                    current_prices[symbol] = price_from_meta
                    self.log(
                        f"ROUTER: PRICE_INJECT | {symbol} | "
                        f"Using metadata contract_price=${price_from_meta:.2f}"
                    )
                else:
                    # V2.24.1: Third fallback — Bid/Ask mid-price from Securities
                    # Only fires when both current_prices and metadata fail (rare).
                    bid_ask_price = self._try_bid_ask_mid_price(symbol)
                    if bid_ask_price > 0:
                        current_prices[symbol] = bid_ask_price
                    else:
                        self._record_rejection(
                            code="R_NO_PRICE",
                            symbol=symbol,
                            detail="No price available from current_prices/metadata/bid-ask fallback",
                            stage="INTENT_BUILD",
                            source_tag=source_tag,
                            trace_id=trace_id,
                        )
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
                self._record_rejection(
                    code="R_DELTA_TOO_SMALL",
                    symbol=symbol,
                    detail=f"abs(delta_shares)={abs(delta_shares)} < MIN_SHARE_DELTA={config.MIN_SHARE_DELTA}",
                    stage="INTENT_BUILD",
                    source_tag=source_tag,
                    trace_id=trace_id,
                )
                continue

            # V2.3.3: Check if this is a closing trade (going to 0)
            # Allow closing trades even if value is small (e.g., worthless options)
            is_closing = agg.target_weight == 0.0

            # Safety: close intents should carry explicit requested_quantity.
            # As a hardening fallback, infer live quantity for option closes when missing.
            if (
                is_option
                and is_closing
                and (agg.requested_quantity is None or agg.requested_quantity <= 0)
            ):
                inferred_qty = 0
                if self.algorithm:
                    try:
                        for kvp in self.algorithm.Portfolio:  # type: ignore[attr-defined]
                            holding = kvp.Value
                            if str(holding.Symbol) == symbol:
                                inferred_qty = abs(int(holding.Quantity))
                                break
                    except Exception:
                        inferred_qty = 0
                if inferred_qty > 0:
                    agg.requested_quantity = inferred_qty
                    self.log(f"ROUTER: CLOSE_QTY_INFERRED | {symbol} | Qty={inferred_qty}")
                else:
                    self._record_rejection(
                        code="R_CLOSE_NO_QTY",
                        symbol=symbol,
                        detail="Closing option without requested_quantity and no live qty fallback",
                        stage="INTENT_BUILD",
                        source_tag=source_tag,
                        trace_id=trace_id,
                    )
                    continue

            # V6.16: For option close intents, derive side/quantity from live holdings only.
            # This prevents stale weight snapshots from flipping close side (e.g., BUY on long close)
            # and blocks accidental position amplification during force-close fallback.
            if is_option and is_closing:
                live_qty = 0
                if self.algorithm:
                    try:
                        for kvp in self.algorithm.Portfolio:  # type: ignore[attr-defined]
                            holding = kvp.Value
                            if str(holding.Symbol) == symbol:
                                live_qty = int(holding.Quantity)
                                break
                    except Exception:
                        live_qty = 0
                if live_qty == 0:
                    self._record_rejection(
                        code="R_CLOSE_NO_LIVE_HOLDING",
                        symbol=symbol,
                        detail="Close intent but no live holdings",
                        stage="INTENT_BUILD",
                        source_tag=source_tag,
                        trace_id=trace_id,
                    )
                    continue
                side = OrderSide.SELL if live_qty > 0 else OrderSide.BUY
                delta_shares = abs(live_qty)

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
                self._record_rejection(
                    code="R_MIN_TRADE_VALUE",
                    symbol=symbol,
                    detail=f"Delta ${delta_value:,.0f} < min ${min_trade_value:,.0f}",
                    stage="INTENT_BUILD",
                    source_tag=source_tag,
                    trace_id=trace_id,
                )
                continue

            # Determine side and order type
            if not (is_option and is_closing):
                side = OrderSide.BUY if delta_shares > 0 else OrderSide.SELL
            # V2.4.2: Added MOC for same-day trend entries
            if agg.urgency == Urgency.IMMEDIATE:
                order_type = OrderType.MARKET
            elif agg.urgency == Urgency.MOC:
                order_type = OrderType.MOC
            else:  # EOD
                order_type = OrderType.MOO

            reason = "; ".join(agg.reasons) if agg.reasons else "No reason provided"
            tag = None
            if is_option:
                if any(s == "OPT_INTRADAY" for s in agg.sources):
                    intraday_strategy = None
                    if agg.metadata:
                        intraday_strategy = agg.metadata.get("intraday_strategy")
                    tag = f"MICRO:{intraday_strategy}" if intraday_strategy else "MICRO"
                elif any(s == "OPT" for s in agg.sources):
                    vass_strategy = None
                    if agg.metadata:
                        vass_strategy = agg.metadata.get("vass_strategy")
                    tag = f"VASS:{vass_strategy}" if vass_strategy else "VASS"

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
                    metadata=agg.metadata,
                    tag=tag,
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
                    # Enrich metadata so downstream margin estimation can recover width
                    # even if explicit spread_width is missing.
                    agg.metadata.setdefault("spread_long_leg_symbol", symbol)
                    agg.metadata.setdefault("spread_short_leg_symbol", short_leg_symbol)
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
                        self._record_rejection(
                            code="R_SIGN_MISMATCH",
                            symbol=symbol,
                            detail=(f"Spread has two BUYS | Long={long_qty} Short={short_qty}"),
                            stage="INTENT_BUILD",
                            source_tag=source_tag,
                            trace_id=trace_id,
                        )
                        continue  # Skip this malformed spread
                    if long_qty < 0 and short_qty < 0:
                        self._record_rejection(
                            code="R_SIGN_MISMATCH",
                            symbol=symbol,
                            detail=(f"Spread has two SELLS | Long={long_qty} Short={short_qty}"),
                            stage="INTENT_BUILD",
                            source_tag=source_tag,
                            trace_id=trace_id,
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
                            metadata=agg.metadata,
                            tag=tag,
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
            for order in orders:
                source_tag, trace_id = self._extract_trace_context(
                    order.metadata, [order.tag] if order.tag else None
                )
                self._record_rejection(
                    code="R_NO_ALGORITHM",
                    symbol=order.symbol,
                    detail="Router has no algorithm reference",
                    stage="EXECUTE",
                    source_tag=source_tag or (order.tag or ""),
                    trace_id=trace_id,
                )
            return []

        # Check risk engine status
        if not self._risk_engine_go:
            self.log("ROUTER: BLOCKED | Risk engine NO-GO status")
            for order in orders:
                source_tag, trace_id = self._extract_trace_context(
                    order.metadata, [order.tag] if order.tag else None
                )
                self._record_rejection(
                    code="R_RISK_ENGINE_NOGO",
                    symbol=order.symbol,
                    detail="Risk engine GO/NO-GO blocked execution",
                    stage="EXECUTE",
                    source_tag=source_tag or (order.tag or ""),
                    trace_id=trace_id,
                )
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
                            for order in orders:
                                source_tag, trace_id = self._extract_trace_context(
                                    order.metadata, [order.tag] if order.tag else None
                                )
                                self._record_rejection(
                                    code="R_MARGIN_COOLDOWN",
                                    symbol=order.symbol,
                                    detail=f"Margin cooldown active until {cooldown}",
                                    stage="EXECUTE",
                                    source_tag=source_tag or (order.tag or ""),
                                    trace_id=trace_id,
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
            source_tag, trace_id = self._extract_trace_context(
                order.metadata, [order.tag] if order.tag else None
            )
            if not source_tag and order.tag:
                source_tag = order.tag
            # Create unique order key with routing context to avoid false duplicate suppression.
            order_key = (
                f"{order.symbol}:{order.side.value}:{order.quantity}:"
                f"{order.order_type.value}:{int(order.is_combo)}:"
                f"{order.combo_short_symbol or ''}:{source_tag or ''}:{trace_id or ''}"
            )

            # Skip if already executed this minute (idempotency guard)
            if order_key in self._executed_this_minute:
                self.log(f"ROUTER: SKIP_DUPLICATE | {order_key} already executed this minute")
                self._record_rejection(
                    code="R_DUPLICATE_ORDER",
                    symbol=order.symbol,
                    detail=f"Duplicate order key in same minute: {order_key}",
                    stage="EXECUTE",
                    source_tag=source_tag,
                    trace_id=trace_id,
                )
                continue

            # V8.2: Primary options budget gate (simple cap aligned to partition).
            # This runs before generic margin-util checks to avoid over-throttling.
            options_budget_allowed, options_budget_reason = self.check_options_budget_gate(order)
            if not options_budget_allowed:
                self.log(f"ROUTER: {options_budget_reason} | Skipping {order.symbol}")
                self._record_rejection(
                    code="R_OPTIONS_BUDGET_CAP",
                    symbol=order.symbol,
                    detail=options_budget_reason,
                    stage="EXECUTE",
                    source_tag=source_tag,
                    trace_id=trace_id,
                )
                continue

            # V4.0.2: Margin utilization gate (emergency-only brake for BUY orders).
            if order.side == OrderSide.BUY:
                margin_allowed, margin_reason = self.check_margin_utilization_gate(
                    is_buy_order=True
                )
                if not margin_allowed:
                    self.log(f"ROUTER: {margin_reason} | Skipping {order.symbol}")
                    self._record_rejection(
                        code="R_MARGIN_UTILIZATION_GATE",
                        symbol=order.symbol,
                        detail=margin_reason,
                        stage="EXECUTE",
                        source_tag=source_tag,
                        trace_id=trace_id,
                    )
                    continue

            # V2.3.2 FIX: Check buying power before placing BUY orders
            # V2.3.24: For combo orders, scale contracts to fit margin instead of rejecting
            if order.side == OrderSide.BUY:
                try:
                    margin_remaining = self.get_effective_margin_remaining()
                    current_price = self.algorithm.Securities[order.symbol].Price  # type: ignore[attr-defined]
                    # Detect options by symbol pattern (C0 or P0 for calls/puts)
                    is_option = len(order.symbol) > 10 and (
                        "C0" in order.symbol or "P0" in order.symbol
                    )
                    if order.is_combo and is_option:
                        quotes_ok, quote_detail = self._validate_combo_entry_quotes(order)
                        if not quotes_ok:
                            self.log(
                                f"ROUTER: R_CONTRACT_QUOTE_INVALID | {order.symbol} | {quote_detail}"
                            )
                            self._record_rejection(
                                code="R_CONTRACT_QUOTE_INVALID",
                                symbol=order.symbol,
                                detail=quote_detail,
                                stage="EXECUTE",
                                source_tag=source_tag,
                                trace_id=trace_id,
                            )
                            continue
                        is_exit_combo = bool(
                            order.metadata and order.metadata.get("spread_close_short", False)
                        )
                        per_contract_margin, reason_code = self._estimate_combo_margin_per_contract(
                            order.metadata
                        )
                        if per_contract_margin <= 0:
                            if is_exit_combo:
                                self.log(
                                    f"ROUTER_EXIT_BYPASS_MARGIN_ESTIMATE: {reason_code} | "
                                    f"{order.symbol} | Risk-reduction combo close allowed"
                                )
                            else:
                                self.log(
                                    f"ROUTER: {reason_code} | {order.symbol} | Combo order blocked"
                                )
                                self._record_rejection(
                                    code=f"R_{reason_code}",
                                    symbol=order.symbol,
                                    detail="Combo pre-check margin estimate invalid",
                                    stage="EXECUTE",
                                    source_tag=source_tag,
                                    trace_id=trace_id,
                                )
                                continue
                        required_margin = per_contract_margin * order.quantity
                        if per_contract_margin > 0 and required_margin > margin_remaining:
                            # V9.4 P0: Exit combos bypass margin check — closing a spread
                            # releases margin, it should never be blocked by margin requirements.
                            # This fixes the deadlock where exit signals fire every minute but
                            # the close order is perpetually margin-blocked.
                            if is_exit_combo:
                                self.log(
                                    f"ROUTER_EXIT_BYPASS_MARGIN: {order.symbol} | "
                                    f"Required=${required_margin:,.0f} > Available=${margin_remaining:,.0f} | "
                                    f"Allowing exit combo (risk-reducing)"
                                )
                            else:
                                max_contracts = int(margin_remaining / per_contract_margin)
                                min_contracts = getattr(config, "MIN_SPREAD_CONTRACTS", 2)
                                if max_contracts >= min_contracts:
                                    scale_ratio = max_contracts / order.quantity
                                    order.quantity = max_contracts
                                    if order.combo_short_quantity:
                                        # Keep short leg quantity exactly aligned with scaled spread count.
                                        # BUY combo (open): short leg is negative.
                                        # SELL combo (close): short leg is positive.
                                        order.combo_short_quantity = (
                                            -order.quantity
                                            if order.side == OrderSide.BUY
                                            else order.quantity
                                        )
                                    self.log(
                                        f"ROUTER_MARGIN_SCALE_COMBO: {order.symbol} | "
                                        f"Required=${required_margin:,.0f} > Available=${margin_remaining:,.0f} | "
                                        f"Scaled to {max_contracts} spreads"
                                    )
                                else:
                                    self.log(
                                        f"ROUTER_MARGIN_BLOCK_COMBO: {order.symbol} | "
                                        f"Required=${required_margin:,.0f} > Available=${margin_remaining:,.0f} | "
                                        f"Max {max_contracts} < min {min_contracts}"
                                    )
                                    self._record_rejection(
                                        code="R_COMBO_MARGIN_BLOCK",
                                        symbol=order.symbol,
                                        detail=(
                                            f"Required=${required_margin:,.0f} > Available=${margin_remaining:,.0f} | "
                                            f"Max={max_contracts} < Min={min_contracts}"
                                        ),
                                        stage="EXECUTE",
                                        source_tag=source_tag,
                                        trace_id=trace_id,
                                    )
                                    continue
                    else:
                        # Non-combo pre-check uses notional order value
                        multiplier = 100 if is_option else 1
                        order_value = order.quantity * current_price * multiplier
                        if order_value > margin_remaining:
                            self.log(
                                f"ROUTER: INSUFFICIENT_MARGIN | {order.symbol} | "
                                f"Order=${order_value:,.0f} > Margin=${margin_remaining:,.0f} | "
                                f"Qty={order.quantity} @ ${current_price:.2f}"
                            )
                            self._record_rejection(
                                code="R_INSUFFICIENT_MARGIN",
                                symbol=order.symbol,
                                detail=(
                                    f"Order=${order_value:,.0f} > Margin=${margin_remaining:,.0f} | "
                                    f"Qty={order.quantity} Price=${current_price:.2f}"
                                ),
                                stage="EXECUTE",
                                source_tag=source_tag,
                                trace_id=trace_id,
                            )
                            continue

                    # V2.14 Fix #14: MARGIN_ERROR_TREND - Check trend orders don't exceed
                    # margin after reserving OPTIONS_MAX_MARGIN_CAP for options engine
                    # V6.11: Trend redesign replaced TNA/FAS with UGL/UCO.
                    trend_symbols = ["QLD", "SSO", "UGL", "UCO"]
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
                            self._record_rejection(
                                code="R_MARGIN_RESERVE_TREND",
                                symbol=order.symbol,
                                detail=(
                                    f"Trend order exceeds margin after reserve | "
                                    f"Order=${order_value:,.0f} AvailableAfterReserve=${margin_after_reserve:,.0f}"
                                ),
                                stage="EXECUTE",
                                source_tag=source_tag,
                                trace_id=trace_id,
                            )
                            continue
                except Exception:
                    pass  # Continue with order if price lookup fails

            try:
                quantity = order.quantity if order.side == OrderSide.BUY else -order.quantity
                effective_tag = (
                    order.tag.strip()
                    if isinstance(order.tag, str) and order.tag.strip()
                    else f"ROUTER_{order.order_type.value}_{order.side.value}"
                )

                # V2.3.9: Handle combo orders for spreads
                if order.is_combo and order.combo_short_symbol and order.combo_short_quantity:
                    is_exit_combo = bool(
                        order.metadata and order.metadata.get("spread_close_short", False)
                    )
                    quotes_ok, quote_detail = self._validate_combo_entry_quotes(order)
                    if not quotes_ok:
                        self.log(
                            f"ROUTER: R_CONTRACT_QUOTE_INVALID | {order.symbol} | {quote_detail}"
                        )
                        self._record_rejection(
                            code="R_CONTRACT_QUOTE_INVALID",
                            symbol=order.symbol,
                            detail=quote_detail,
                            stage="EXECUTE",
                            source_tag=source_tag,
                            trace_id=trace_id,
                        )
                        continue
                    # Final safety check before submit (same estimator as pre-check)
                    (
                        combo_margin_per_contract,
                        reason_code,
                    ) = self._estimate_combo_margin_per_contract(order.metadata)
                    if combo_margin_per_contract <= 0:
                        if is_exit_combo:
                            self.log(
                                f"ROUTER_EXIT_BYPASS_MARGIN_ESTIMATE: {reason_code} | "
                                f"{order.symbol} | Combo close submit allowed"
                            )
                        else:
                            self.log(
                                f"ROUTER: {reason_code} | {order.symbol} | Combo submit blocked"
                            )
                            self._record_rejection(
                                code=f"R_{reason_code}",
                                symbol=order.symbol,
                                detail="Combo submit blocked due to invalid margin estimate",
                                stage="EXECUTE",
                                source_tag=source_tag,
                                trace_id=trace_id,
                            )
                            if hasattr(self.algorithm, "options_engine") and order.metadata:
                                if order.metadata.get("spread_close_short", False):
                                    self.algorithm.options_engine.reset_spread_closing_lock()
                            continue
                    total_combo_margin = combo_margin_per_contract * abs(order.quantity)
                    margin_remaining = self.get_effective_margin_remaining()
                    if combo_margin_per_contract > 0 and total_combo_margin > margin_remaining:
                        # V9.4 P0: Exit combos bypass — closing reduces risk, should never be blocked
                        if is_exit_combo:
                            self.log(
                                f"ROUTER_EXIT_BYPASS_MARGIN: {order.symbol} | "
                                f"Required=${total_combo_margin:,.0f} > Available=${margin_remaining:,.0f} | "
                                f"Allowing exit combo (risk-reducing)"
                            )
                        else:
                            self.log(
                                f"ROUTER_MARGIN_BLOCK_COMBO: {order.symbol} | "
                                f"Required=${total_combo_margin:,.0f} > Available=${margin_remaining:,.0f}"
                            )
                            self._record_rejection(
                                code="R_COMBO_MARGIN_BLOCK",
                                symbol=order.symbol,
                                detail=(
                                    f"Required=${total_combo_margin:,.0f} > Available=${margin_remaining:,.0f}"
                                ),
                                stage="EXECUTE",
                                source_tag=source_tag,
                                trace_id=trace_id,
                            )
                            continue

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
                    try:
                        self.algorithm.ComboMarketOrder(  # type: ignore[attr-defined]
                            legs, num_spreads, tag=effective_tag
                        )
                    except TypeError:
                        self.algorithm.ComboMarketOrder(legs, num_spreads)  # type: ignore[attr-defined]
                    self.log(
                        f"ROUTER: COMBO_MARKET_ORDER | "
                        f"Long={order.symbol} x{num_spreads} (ratio={long_ratio}) + "
                        f"Short={order.combo_short_symbol} x{num_spreads} (ratio={short_ratio})"
                        + f" | Tag={effective_tag}"
                    )

                    # V2.9: Register spread margin reservation (Bug #1 fix)
                    # Track margin locked by this spread to prevent Trend overcommitment
                    if hasattr(order, "metadata") and order.metadata:
                        spread_width = order.metadata.get("spread_width", 5.0)
                        spread_margin = spread_width * 100 * num_spreads
                        is_exit = order.metadata.get("spread_close_short", False)

                        long_leg_symbol = str(
                            order.metadata.get("spread_long_leg_symbol", order.symbol)
                        )
                        short_leg_symbol = str(
                            order.metadata.get("spread_short_leg_symbol", order.combo_short_symbol)
                        )
                        if is_exit:
                            # Closing spread - unregister composite key reservation
                            self.unregister_spread_margin_by_legs(long_leg_symbol, short_leg_symbol)
                        else:
                            # Opening spread - register composite key reservation
                            self.register_spread_margin(
                                long_leg_symbol,
                                spread_margin,
                                short_symbol=short_leg_symbol,
                            )
                elif order.order_type == OrderType.MARKET:
                    is_option_market = len(str(order.symbol)) > 10 and (
                        "C0" in str(order.symbol) or "P0" in str(order.symbol)
                    )
                    use_option_limits = bool(getattr(config, "OPTIONS_USE_LIMIT_ORDERS", True))
                    if is_option_market and use_option_limits:
                        submitted = self.execute_options_limit_order(
                            symbol=order.symbol,
                            quantity=quantity,
                            reason=order.reason,
                            tag=effective_tag,
                        )
                        if not submitted:
                            self._record_rejection(
                                code="R_LIMIT_EXEC_FAIL",
                                symbol=order.symbol,
                                detail="Marketable limit submission failed",
                                stage="EXECUTE",
                                source_tag=source_tag,
                                trace_id=trace_id,
                            )
                            continue
                    else:
                        try:
                            self.algorithm.MarketOrder(  # type: ignore[attr-defined]
                                order.symbol, quantity, tag=effective_tag
                            )
                        except TypeError:
                            self.algorithm.MarketOrder(order.symbol, quantity)  # type: ignore[attr-defined]
                        self.log(
                            f"ROUTER: MARKET_ORDER | {order.side.value} {order.quantity} {order.symbol}"
                            + f" | Tag={effective_tag}"
                        )
                elif order.order_type == OrderType.MOC:
                    # V2.4.2: Market-On-Close for same-day trend entries
                    try:
                        self.algorithm.MarketOnCloseOrder(  # type: ignore[attr-defined]
                            order.symbol, quantity, tag=effective_tag
                        )
                    except TypeError:
                        self.algorithm.MarketOnCloseOrder(order.symbol, quantity)  # type: ignore[attr-defined]
                    self.log(
                        f"ROUTER: MOC_ORDER | {order.side.value} {order.quantity} {order.symbol}"
                        + f" | Tag={effective_tag}"
                    )
                else:  # MOO
                    try:
                        self.algorithm.MarketOnOpenOrder(  # type: ignore[attr-defined]
                            order.symbol, quantity, tag=effective_tag
                        )
                    except TypeError:
                        self.algorithm.MarketOnOpenOrder(order.symbol, quantity)  # type: ignore[attr-defined]
                    self.log(
                        f"ROUTER: MOO_ORDER | {order.side.value} {order.quantity} {order.symbol}"
                        + f" | Tag={effective_tag}"
                    )

                # Mark as executed to prevent duplicates
                self._executed_this_minute.add(order_key)
                executed.append(order)

            except Exception as e:
                self.log(f"ROUTER: ORDER_ERROR | {order.symbol} | {e}")
                self._record_rejection(
                    code="R_ORDER_EXCEPTION",
                    symbol=order.symbol,
                    detail=str(e),
                    stage="EXECUTE",
                    source_tag=source_tag,
                    trace_id=trace_id,
                )
                if (
                    order.is_combo
                    and order.metadata
                    and order.metadata.get("spread_close_short", False)
                    and hasattr(self.algorithm, "options_engine")
                ):
                    self.algorithm.options_engine.reset_spread_closing_lock()

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

        # New cycle: reset rejection buffer for this immediate processing pass.
        self.clear_last_rejections()

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
