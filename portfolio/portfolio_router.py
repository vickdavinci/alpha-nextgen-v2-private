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
from datetime import timedelta
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
        self._last_rejection_event_log_by_key: Dict[str, Any] = {}
        self._suppressed_rejection_event_count_by_key: Dict[str, int] = {}
        self._signal_seq: int = 0
        # Exit pre-clear barrier state: key -> first observed timestamp while waiting for cancels.
        self._exit_preclear_pending_since: Dict[str, Any] = {}
        # Preclear telemetry counters for daily diagnostics.
        self._preclear_diag_counts: Dict[str, int] = {}
        # Per-symbol cooldown for stale close intents (no live holdings).
        self._stale_close_reject_cooldown_until: Dict[str, Any] = {}

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

    def _cache_submitted_order_tags(self, ticket_or_tickets: Any, tag: str) -> None:
        """Backfill order-id -> tag hints when broker lifecycle events omit tags."""
        if self.algorithm is None:
            return
        clean_tag = str(tag or "").strip()
        if not clean_tag:
            return
        cache_fn = getattr(self.algorithm, "_cache_order_tag_hint", None)
        if not callable(cache_fn):
            return

        def _extract_spread_key(raw_tag: str) -> str:
            if not raw_tag:
                return ""
            lowered = raw_tag.lower()
            for marker in ("spread_key=", "skey="):
                idx = lowered.find(marker)
                if idx < 0:
                    continue
                value = raw_tag[idx + len(marker) :].strip()
                if not value:
                    return ""
                for sep in ("|", ";", ","):
                    cut = value.find(sep)
                    if cut >= 0:
                        value = value[:cut]
                        break
                return value.strip().replace("~", "|")
            return ""

        def _cache_ticket(ticket: Any) -> None:
            if ticket is None:
                return
            try:
                order_id = int(getattr(ticket, "OrderId", 0) or 0)
            except Exception:
                order_id = 0
            if order_id > 0:
                try:
                    cache_fn(order_id, clean_tag)
                except Exception:
                    pass
                try:
                    sym_cache_fn = getattr(self.algorithm, "_cache_symbol_fill_tag", None)
                    ticket_symbol = str(getattr(ticket, "Symbol", "") or "")
                    if callable(sym_cache_fn) and ticket_symbol:
                        sym_cache_fn(ticket_symbol, clean_tag)
                except Exception:
                    pass
                try:
                    map_fn = getattr(self.algorithm, "_record_order_tag_map", None)
                    if callable(map_fn):
                        map_fn(
                            order_id, str(getattr(ticket, "Symbol", "")), clean_tag, "router_submit"
                        )
                except Exception:
                    pass
                try:
                    remember_key_fn = getattr(
                        self.algorithm, "_remember_spread_close_order_key", None
                    )
                    spread_key = _extract_spread_key(clean_tag)
                    if callable(remember_key_fn) and spread_key:
                        remember_key_fn(order_id, spread_key)
                except Exception:
                    pass
                try:
                    event_fn = getattr(self.algorithm, "_record_order_lifecycle_event", None)
                    if callable(event_fn):
                        try:
                            qty = int(getattr(ticket, "Quantity", 0) or 0)
                        except Exception:
                            qty = 0
                        try:
                            trace_fn = getattr(self.algorithm, "_extract_trace_id_from_tag", None)
                            trace_id = str(trace_fn(clean_tag) or "") if callable(trace_fn) else ""
                        except Exception:
                            trace_id = ""
                        event_fn(
                            status="SUBMITTED",
                            order_id=order_id,
                            symbol=str(getattr(ticket, "Symbol", "")),
                            quantity=qty,
                            fill_price=0.0,
                            order_type=str(getattr(ticket, "OrderType", "") or ""),
                            order_tag=clean_tag,
                            trace_id=trace_id,
                            message="",
                            source="ROUTER_SUBMIT",
                        )
                except Exception:
                    pass

        if isinstance(ticket_or_tickets, (list, tuple)):
            for ticket in ticket_or_tickets:
                _cache_ticket(ticket)
            return
        _cache_ticket(ticket_or_tickets)

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

    def _should_log_rejection_event(self, code: str, stage: str) -> Tuple[bool, int]:
        """
        Throttle high-frequency ROUTER_REJECT logs by rejection class.

        Keeps structured rejection records intact for diagnostics while reducing
        repeated log-line spam during reject storms (margin, quote, sizing loops).
        """
        if not self.algorithm:
            return True, 0

        from datetime import timedelta

        key = f"{str(code)}|{str(stage)}"
        now = self.algorithm.Time  # type: ignore[attr-defined]
        interval_min = max(1, int(getattr(config, "REJECTION_EVENT_LOG_THROTTLE_MINUTES", 5)))
        last = self._last_rejection_event_log_by_key.get(key)

        if last is None or (now - last) >= timedelta(minutes=interval_min):
            suppressed = int(self._suppressed_rejection_event_count_by_key.pop(key, 0))
            self._last_rejection_event_log_by_key[key] = now
            return True, suppressed

        self._suppressed_rejection_event_count_by_key[key] = (
            int(self._suppressed_rejection_event_count_by_key.get(key, 0)) + 1
        )
        return False, 0

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

    def _normalize_symbol_key(self, symbol: Any) -> str:
        """Normalize symbol-like values to stable uppercase keys."""
        if symbol is None:
            return ""
        text = str(symbol).strip().upper()
        if not text:
            return ""
        # Keep router symbol matching consistent with options engine and OCO manager.
        return " ".join(text.split())

    def _resolve_spread_runtime_key(self, spread: Any) -> str:
        """Resolve canonical spread runtime key for close-order tag correlation."""
        if spread is None:
            return ""
        if self.algorithm is not None:
            try:
                key_fn = getattr(self.algorithm, "_build_spread_runtime_key", None)
                if callable(key_fn):
                    resolved = str(key_fn(spread) or "").strip()
                    if resolved:
                        return resolved
            except Exception:
                pass
        try:
            long_symbol = self._normalize_symbol_key(getattr(spread.long_leg, "symbol", None))
            short_symbol = self._normalize_symbol_key(getattr(spread.short_leg, "symbol", None))
            entry_time = str(getattr(spread, "entry_time", "") or "")
            if long_symbol and short_symbol:
                return f"{long_symbol}|{short_symbol}|{entry_time}"
        except Exception:
            return ""
        return ""

    def _encode_spread_key_for_tag(self, spread_key: str) -> str:
        """Encode spread key for safe transport in order tags."""
        return str(spread_key or "").strip().replace("|", "~")

    def _extract_spread_key_from_tag(self, tag: str) -> str:
        """Best-effort extraction of spread runtime key encoded in router tags."""
        text = str(tag or "").strip()
        if not text:
            return ""
        lowered = text.lower()
        marker = "spread_key="
        idx = lowered.find(marker)
        if idx < 0:
            return ""
        value = text[idx + len(marker) :].strip()
        if not value:
            return ""
        for sep in ("|", ";", ","):
            cut = value.find(sep)
            if cut >= 0:
                value = value[:cut]
                break
        return value.strip().replace("~", "|")

    def _get_live_option_qty(self, symbol: str) -> int:
        """Return live portfolio quantity for symbol (0 when flat/not found)."""
        if not self.algorithm:
            return 0
        target = self._normalize_symbol_key(symbol)
        if not target:
            return 0
        try:
            for kvp in self.algorithm.Portfolio:  # type: ignore[attr-defined]
                holding = kvp.Value
                if self._normalize_symbol_key(holding.Symbol) == target:
                    return int(holding.Quantity)
        except Exception:
            return 0
        return 0

    def _evict_stale_option_close_intent(self, symbol: str) -> List[str]:
        """
        Best-effort cleanup for stale single-leg close intents with no live holdings.

        This prevents repeated router close attempts when engine/pending-close state
        lags behind broker holdings.
        """
        cleaned: List[str] = []
        if not self.algorithm:
            return cleaned
        symbol_key = self._normalize_symbol_key(symbol)
        if not symbol_key:
            return cleaned

        options_engine = getattr(self.algorithm, "options_engine", None)  # type: ignore[attr-defined]
        if options_engine is not None:
            try:
                if options_engine.cancel_pending_engine_exit(symbol_key):
                    cleaned.append("pending_exit")
            except Exception:
                pass
            try:
                removed = options_engine.remove_engine_position(symbol=symbol_key)
                if removed is not None:
                    cleaned.append("engine_position")
            except Exception:
                pass

        try:
            clear_guard = getattr(self.algorithm, "_clear_engine_close_guard", None)
            if callable(clear_guard):
                clear_guard(symbol_key)
                cleaned.append("close_guard")
        except Exception:
            pass

        return cleaned

    def _is_stale_close_reject_cooldown_active(self, symbol: str) -> bool:
        """Return True when stale close reject cooldown is active for a symbol."""
        symbol_key = self._normalize_symbol_key(symbol)
        if not symbol_key or self.algorithm is None:
            return False
        until = self._stale_close_reject_cooldown_until.get(symbol_key)
        now = getattr(self.algorithm, "Time", None)
        if until is None or now is None:
            return False
        try:
            if now < until:
                return True
        except Exception:
            pass
        self._stale_close_reject_cooldown_until.pop(symbol_key, None)
        return False

    def _arm_stale_close_reject_cooldown(self, symbol: str) -> None:
        """Arm short cooldown after rejecting a stale close intent with no live holdings."""
        symbol_key = self._normalize_symbol_key(symbol)
        if not symbol_key or self.algorithm is None:
            return
        cooldown_sec = max(
            0, int(getattr(config, "ROUTER_STALE_CLOSE_REJECT_COOLDOWN_SECONDS", 60))
        )
        if cooldown_sec <= 0:
            self._stale_close_reject_cooldown_until.pop(symbol_key, None)
            return
        now = getattr(self.algorithm, "Time", None)
        if now is None:
            return
        self._stale_close_reject_cooldown_until[symbol_key] = now + timedelta(seconds=cooldown_sec)

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

    def _get_open_orders_for_symbols(self, symbols: List[str]) -> List[Any]:
        """Return live open broker orders whose symbol matches any provided key."""
        if not self.algorithm:
            return []
        wanted = {self._normalize_symbol_key(s) for s in symbols if self._normalize_symbol_key(s)}
        if not wanted:
            return []
        matches: List[Any] = []
        try:
            for open_order in self.algorithm.Transactions.GetOpenOrders():  # type: ignore[attr-defined]
                symbol_key = self._normalize_symbol_key(getattr(open_order, "Symbol", None))
                if symbol_key in wanted:
                    matches.append(open_order)
        except Exception as e:
            self.log(f"ROUTER_EXIT_PRECLEAR_SCAN_ERROR: {e}")
            return []
        return matches

    def _cancel_open_orders_for_symbols(
        self,
        symbols: List[str],
        open_orders: Optional[List[Any]] = None,
    ) -> Tuple[int, int]:
        """Cancel open broker orders for symbols; returns (canceled, cancel_errors)."""
        if not self.algorithm:
            return 0, 0
        open_orders_to_cancel = (
            list(open_orders)
            if open_orders is not None
            else self._get_open_orders_for_symbols(symbols)
        )
        canceled = 0
        cancel_errors = 0
        for open_order in open_orders_to_cancel:
            try:
                order_id = int(getattr(open_order, "Id", 0) or 0)
                if order_id <= 0:
                    order_id = int(getattr(open_order, "OrderId", 0) or 0)
                if order_id <= 0:
                    continue
                self.algorithm.Transactions.CancelOrder(order_id)  # type: ignore[attr-defined]
                canceled += 1
            except Exception as e:
                cancel_errors += 1
                self.log(
                    f"ROUTER_EXIT_PRECLEAR_CANCEL_ERROR: "
                    f"Symbol={getattr(open_order, 'Symbol', '')} | {e}"
                )
        return canceled, cancel_errors

    def _run_option_exit_preclear(self, order: "OrderIntent") -> Tuple[bool, str]:
        """
        Plumbing guard for option exits:
        1) cancel all same-symbol open orders,
        2) wait until broker reports no open orders,
        3) allow submit after timeout as a safety release.
        """
        if not self._is_option_close_order(order):
            return True, ""

        def _record_preclear_event(event: str) -> None:
            key = str(event or "").strip().upper()
            if not key:
                return
            self._preclear_diag_counts[key] = int(self._preclear_diag_counts.get(key, 0) or 0) + 1

        timeout_sec = max(1, int(getattr(config, "EXIT_PRE_CLEAR_TIMEOUT_SECONDS", 30)))
        metadata = order.metadata if isinstance(order.metadata, dict) else {}
        lane = str(metadata.get("options_lane", "") or "").upper()
        if order.is_combo:
            timeout_sec = max(
                1, int(getattr(config, "EXIT_PRE_CLEAR_TIMEOUT_SECONDS_COMBO", timeout_sec))
            )
        elif lane in {"MICRO", "ITM"}:
            timeout_sec = max(
                1, int(getattr(config, "EXIT_PRE_CLEAR_TIMEOUT_SECONDS_INTRADAY", timeout_sec))
            )

        symbols = [order.symbol]
        if order.is_combo and order.combo_short_symbol:
            symbols.append(order.combo_short_symbol)

        normalized = sorted(
            {self._normalize_symbol_key(s) for s in symbols if self._normalize_symbol_key(s)}
        )
        if not normalized:
            return True, ""

        def _order_ids(open_orders: List[Any], limit: int = 6) -> str:
            ids: List[str] = []
            for open_order in open_orders:
                try:
                    oid = int(getattr(open_order, "Id", 0) or 0)
                    if oid <= 0:
                        oid = int(getattr(open_order, "OrderId", 0) or 0)
                    if oid > 0:
                        ids.append(str(oid))
                except Exception:
                    continue
            if not ids:
                return "NONE"
            return ",".join(ids[:limit])

        key = "|".join(normalized)
        open_before = self._get_open_orders_for_symbols(normalized)
        if not open_before:
            self._exit_preclear_pending_since.pop(key, None)
            _record_preclear_event("FAST_OK_NO_OPEN")
            return True, ""

        tag_upper = str(getattr(order, "tag", "") or "").upper()
        reason_upper = str(getattr(order, "reason", "") or "").upper()
        strategy_upper = str(metadata.get("options_strategy", "") or "").upper()
        exit_code_upper = str(
            metadata.get("intraday_exit_code", "") or metadata.get("spread_exit_code", "") or ""
        ).upper()
        is_intraday_hint = (
            lane in {"MICRO", "ITM"}
            or "MICRO:" in tag_upper
            or "ITM:" in tag_upper
            or "OPT_INTRADAY" in tag_upper
            or "INTRADAY" in reason_upper
            or "PREMARKET_STALE_INTRADAY_CLOSE" in reason_upper
            or "INTRADAY" in exit_code_upper
        )
        is_vass_hint = (
            lane == "VASS"
            or bool(metadata.get("spread_close_short", False))
            or "VASS" in strategy_upper
            or "VASS:" in tag_upper
            or "OPT_VASS" in tag_upper
        )
        is_vass_combo_close = bool(
            order.is_combo and is_vass_hint and bool(metadata.get("spread_close_short", False))
        )

        def _is_close_side_open_order(open_order: Any) -> bool:
            open_symbol = self._normalize_symbol_key(getattr(open_order, "Symbol", None))
            open_qty = int(getattr(open_order, "Quantity", 0) or 0)
            if not open_symbol or open_qty == 0:
                return False
            live_qty = self._get_live_option_qty(open_symbol)
            return (open_qty < 0 < live_qty) or (open_qty > 0 > live_qty)

        def _partition_open_orders(open_orders: List[Any]) -> Tuple[List[Any], List[Any]]:
            close_side: List[Any] = []
            blocking: List[Any] = []
            for open_order in open_orders:
                if _is_close_side_open_order(open_order):
                    close_side.append(open_order)
                else:
                    blocking.append(open_order)
            return close_side, blocking

        # V12.23.2: For VASS spread closes, replace same-spread in-flight close
        # orders immediately so a fresh close intent can submit in the same cycle.
        if is_vass_combo_close:
            intended_spread_key = str(metadata.get("spread_key", "") or "").strip()
            if not intended_spread_key:
                intended_spread_key = self._extract_spread_key_from_tag(
                    str(getattr(order, "tag", "") or "")
                )
            if intended_spread_key:
                matching_inflight: List[Any] = []
                for open_order in open_before:
                    open_tag = str(getattr(open_order, "Tag", "") or "")
                    open_spread_key = self._extract_spread_key_from_tag(open_tag)
                    if open_spread_key != intended_spread_key:
                        continue
                    open_symbol = self._normalize_symbol_key(getattr(open_order, "Symbol", None))
                    open_qty = int(getattr(open_order, "Quantity", 0) or 0)
                    live_qty = self._get_live_option_qty(open_symbol)
                    is_close_side = (open_qty < 0 < live_qty) or (open_qty > 0 > live_qty)
                    if is_close_side:
                        matching_inflight.append(open_order)
                if matching_inflight:
                    inflight_ids = _order_ids(matching_inflight)
                    if bool(getattr(config, "VASS_EXIT_PRECLEAR_REPLACE_INFLIGHT_CLOSE", True)):
                        canceled = 0
                        cancel_errors = 0
                        for inflight_order in matching_inflight:
                            try:
                                oid = int(getattr(inflight_order, "Id", 0) or 0)
                                if oid <= 0:
                                    oid = int(getattr(inflight_order, "OrderId", 0) or 0)
                                if oid <= 0:
                                    continue
                                self.algorithm.Transactions.CancelOrder(oid)  # type: ignore[attr-defined]
                                canceled += 1
                            except Exception:
                                cancel_errors += 1
                        self._exit_preclear_pending_since.pop(key, None)
                        _record_preclear_event("VASS_COMBO_INFLIGHT_REPLACED")
                        return (
                            True,
                            "EXIT_PRE_CLEAR_REPLACED_INFLIGHT_CLOSE: "
                            f"Symbols={','.join(normalized)} | "
                            f"OrderIds={inflight_ids} | Canceled={canceled} | "
                            f"CancelErrors={cancel_errors} | Mode=VASS_COMBO_SAME_SPREAD",
                        )
                    _record_preclear_event("INFLIGHT_DEFER")
                    return (
                        False,
                        "EXIT_PRE_CLEAR_INFLIGHT_CLOSE: "
                        f"Symbols={','.join(normalized)} | "
                        f"OrderIds={inflight_ids} | Mode=VASS_COMBO_SAME_SPREAD",
                    )

        # V12.22 scope correction: dedupe in-flight close submits only for VASS-style
        # close intents. Intraday lanes (MICRO/ITM) must not be deferred by this gate,
        # otherwise force-close can leak overnight into stale premarket exits.
        apply_inflight_close_dedupe = (not order.is_combo) and is_vass_hint and not is_intraday_hint

        # If a same-symbol close order is already in-flight, defer duplicate close
        # submits until timeout before force-canceling. This reduces OCO teardown churn.
        if apply_inflight_close_dedupe:
            desired_side = str(getattr(order.side, "value", order.side) or "").upper()
            matching_inflight = []
            for open_order in open_before:
                open_symbol = self._normalize_symbol_key(getattr(open_order, "Symbol", None))
                if open_symbol != normalized[0]:
                    continue
                open_qty = int(getattr(open_order, "Quantity", 0) or 0)
                if open_qty == 0:
                    continue
                open_side = "BUY" if open_qty > 0 else "SELL"
                if open_side == desired_side:
                    matching_inflight.append(open_order)

            if matching_inflight:
                now = getattr(self.algorithm, "Time", None) if self.algorithm else None
                first_seen = self._exit_preclear_pending_since.get(key)
                if first_seen is None:
                    self._exit_preclear_pending_since[key] = now
                    first_seen = now
                elapsed_sec = 0.0
                try:
                    if first_seen is not None and now is not None:
                        elapsed_sec = max(0.0, float((now - first_seen).total_seconds()))
                except Exception:
                    elapsed_sec = 0.0
                if elapsed_sec < timeout_sec:
                    _record_preclear_event("INFLIGHT_DEFER")
                    inflight_ids = _order_ids(matching_inflight)
                    return (
                        False,
                        "EXIT_PRE_CLEAR_INFLIGHT_CLOSE: "
                        f"Symbols={','.join(normalized)} | OrderIds={inflight_ids} | "
                        f"Elapsed={elapsed_sec:.0f}s/{timeout_sec}s",
                    )

        close_before, blocking_before = _partition_open_orders(open_before)
        if close_before and not blocking_before:
            now = getattr(self.algorithm, "Time", None) if self.algorithm else None
            first_seen = self._exit_preclear_pending_since.get(key)
            if first_seen is None:
                self._exit_preclear_pending_since[key] = now
                first_seen = now
            elapsed_sec = 0.0
            try:
                if first_seen is not None and now is not None:
                    elapsed_sec = max(0.0, float((now - first_seen).total_seconds()))
            except Exception:
                elapsed_sec = 0.0
            inflight_ids = _order_ids(close_before)
            if elapsed_sec < timeout_sec:
                _record_preclear_event("INFLIGHT_DEFER")
                return (
                    False,
                    "EXIT_PRE_CLEAR_INFLIGHT_CLOSE: "
                    f"Symbols={','.join(normalized)} | OrderIds={inflight_ids} | "
                    f"Elapsed={elapsed_sec:.0f}s/{timeout_sec}s | Mode=GENERIC_CLOSE_INFLIGHT",
                )
            canceled_inflight, cancel_errors_inflight = self._cancel_open_orders_for_symbols(
                normalized,
                open_orders=close_before,
            )
            after_inflight_cancel = self._get_open_orders_for_symbols(normalized)
            if not after_inflight_cancel:
                self._exit_preclear_pending_since.pop(key, None)
                _record_preclear_event("INFLIGHT_TIMEOUT_REPLACED")
                return (
                    True,
                    "EXIT_PRE_CLEAR_TIMEOUT_REPLACED_INFLIGHT_CLOSE: "
                    f"Symbols={','.join(normalized)} | OrderIds={inflight_ids} | "
                    f"Canceled={canceled_inflight} | CancelErrors={cancel_errors_inflight} | "
                    f"Elapsed={elapsed_sec:.0f}s >= {timeout_sec}s",
                )
            _record_preclear_event("INFLIGHT_TIMEOUT_STILL_PENDING")
            return (
                False,
                "EXIT_PRE_CLEAR_PENDING: "
                f"Symbols={','.join(normalized)} | Canceled={canceled_inflight} | "
                f"Remaining={len(after_inflight_cancel)} | "
                f"RemainingIds={_order_ids(after_inflight_cancel)} | "
                f"CancelErrors={cancel_errors_inflight} | Elapsed={elapsed_sec:.0f}s/{timeout_sec}s | "
                "Mode=GENERIC_INFLIGHT_TIMEOUT_CANCEL",
            )

        canceled, cancel_errors = self._cancel_open_orders_for_symbols(
            normalized,
            open_orders=blocking_before,
        )
        open_after = self._get_open_orders_for_symbols(normalized)
        if not open_after:
            self._exit_preclear_pending_since.pop(key, None)
            _record_preclear_event("BLOCKERS_CLEARED_OK")
            return (
                True,
                f"EXIT_PRE_CLEAR_OK: Symbols={','.join(normalized)} | Canceled={canceled}",
            )
        close_after, blocking_after = _partition_open_orders(open_after)
        remaining = len(open_after)
        remaining_ids = _order_ids(open_after)
        if close_after and not blocking_after:
            now = getattr(self.algorithm, "Time", None) if self.algorithm else None
            first_seen = self._exit_preclear_pending_since.get(key)
            if first_seen is None:
                self._exit_preclear_pending_since[key] = now
                first_seen = now
            elapsed_sec = 0.0
            try:
                if first_seen is not None and now is not None:
                    elapsed_sec = max(0.0, float((now - first_seen).total_seconds()))
            except Exception:
                elapsed_sec = 0.0
            close_after_ids = _order_ids(close_after)
            if elapsed_sec < timeout_sec:
                _record_preclear_event("INFLIGHT_DEFER")
                return (
                    False,
                    "EXIT_PRE_CLEAR_INFLIGHT_CLOSE: "
                    f"Symbols={','.join(normalized)} | OrderIds={close_after_ids} | "
                    f"Elapsed={elapsed_sec:.0f}s/{timeout_sec}s | Mode=POST_CANCEL_CLOSE_INFLIGHT",
                )
            canceled_inflight, cancel_errors_inflight = self._cancel_open_orders_for_symbols(
                normalized,
                open_orders=close_after,
            )
            final_open = self._get_open_orders_for_symbols(normalized)
            if not final_open:
                self._exit_preclear_pending_since.pop(key, None)
                _record_preclear_event("INFLIGHT_TIMEOUT_REPLACED")
                return (
                    True,
                    "EXIT_PRE_CLEAR_TIMEOUT_REPLACED_INFLIGHT_CLOSE: "
                    f"Symbols={','.join(normalized)} | OrderIds={close_after_ids} | "
                    f"Canceled={canceled_inflight} | CancelErrors={cancel_errors_inflight} | "
                    f"Elapsed={elapsed_sec:.0f}s >= {timeout_sec}s | Mode=POST_CANCEL",
                )
            _record_preclear_event("INFLIGHT_TIMEOUT_STILL_PENDING")
            return (
                False,
                "EXIT_PRE_CLEAR_PENDING: "
                f"Symbols={','.join(normalized)} | Canceled={canceled_inflight} | "
                f"Remaining={len(final_open)} | RemainingIds={_order_ids(final_open)} | "
                f"CancelErrors={cancel_errors_inflight} | Elapsed={elapsed_sec:.0f}s/{timeout_sec}s | "
                "Mode=POST_CANCEL_INFLIGHT_TIMEOUT_CANCEL",
            )

        now = getattr(self.algorithm, "Time", None) if self.algorithm else None
        first_seen = self._exit_preclear_pending_since.get(key)
        if first_seen is None:
            self._exit_preclear_pending_since[key] = now
            first_seen = now

        elapsed_sec = 0.0
        try:
            if first_seen is not None and now is not None:
                elapsed_sec = max(0.0, float((now - first_seen).total_seconds()))
        except Exception:
            elapsed_sec = 0.0

        # For time-critical intraday forced exits, avoid close latency caused by
        # waiting for cancel acknowledgment in a bar-based execution loop.
        intraday_exit_code_upper = str(metadata.get("intraday_exit_code", "") or "").upper()
        intraday_time_critical = (
            "INTRADAY_FORCE_EXIT" in reason_upper
            or "INTRADAY_TIME_EXIT_" in reason_upper
            or "INTRADAY_FORCE_CLOSE" in reason_upper
            or "MICRO_EOD_SWEEP" in reason_upper
            or "INTRADAY_TIME_EXIT_" in intraday_exit_code_upper
            or "MICRO_EOD_SWEEP" in intraday_exit_code_upper
        )
        intraday_bypass_after = max(
            0, int(getattr(config, "EXIT_PRE_CLEAR_INTRADAY_BYPASS_AFTER_SECONDS", 5))
        )
        if (
            bool(getattr(config, "EXIT_PRE_CLEAR_ALLOW_IMMEDIATE_INTRADAY_CLOSE", True))
            and intraday_time_critical
            and not order.is_combo
            and (canceled > 0 or elapsed_sec >= intraday_bypass_after)
        ):
            self._exit_preclear_pending_since.pop(key, None)
            _record_preclear_event("INTRADAY_BYPASS")
            return (
                True,
                "EXIT_PRE_CLEAR_BYPASS: "
                f"Symbols={','.join(normalized)} | Canceled={canceled} | Remaining={remaining} | "
                f"RemainingIds={remaining_ids} | Reason=INTRADAY_TIME_CRITICAL",
            )

        if elapsed_sec >= timeout_sec:
            self._exit_preclear_pending_since.pop(key, None)
            _record_preclear_event("BLOCKING_TIMEOUT_RELEASE")
            return (
                True,
                "EXIT_PRE_CLEAR_TIMEOUT: "
                f"Symbols={','.join(normalized)} | Remaining={remaining} | "
                f"RemainingIds={remaining_ids} | Elapsed={elapsed_sec:.0f}s >= {timeout_sec}s | Continuing",
            )

        _record_preclear_event("BLOCKING_PENDING")
        return (
            False,
            "EXIT_PRE_CLEAR_PENDING: "
            f"Symbols={','.join(normalized)} | Canceled={canceled} | Remaining={remaining} | "
            f"RemainingIds={remaining_ids} | CancelErrors={cancel_errors} | "
            f"Elapsed={elapsed_sec:.0f}s/{timeout_sec}s",
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
        spread_key = self._resolve_spread_runtime_key(spread)
        spread_key_token = self._encode_spread_key_for_tag(spread_key)
        spread_key_suffix = f"|spread_key={spread_key_token}" if spread_key_token else ""

        _, _, live_long_qty, live_short_qty = self._get_live_spread_leg_state(
            long_symbol, short_symbol
        )
        if live_long_qty <= 0 and live_short_qty <= 0:
            self.log(
                f"ROUTER: SPREAD_CLOSE_ALREADY_FLAT | {reason} | "
                f"Long={long_symbol} Short={short_symbol}"
            )
            return True

        self.log(
            f"ROUTER: SPREAD_CLOSE_START | {reason} | "
            f"Type={spread.spread_type} x{num_spreads} | "
            f"Long={long_symbol} Short={short_symbol}"
        )

        effective_emergency = bool(is_emergency) or self._is_emergency_spread_exit(
            {
                "spread_close_short": True,
                "spread_exit_code": reason,
                "spread_exit_reason": reason,
            },
            reason,
        )

        # Try atomic ComboMarketOrder first (unless emergency)
        if not effective_emergency:
            combo_success = self._try_combo_close(
                long_symbol,
                short_symbol,
                num_spreads,
                reason,
                tag=f"SPREAD_CLOSE_COMBO|{reason}{spread_key_suffix}",
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
            long_symbol,
            short_symbol,
            num_spreads,
            reason,
            tag_prefix=f"SPREAD_CLOSE_SEQ|{reason}{spread_key_suffix}",
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

    def _is_emergency_spread_exit(
        self,
        metadata: Optional[Dict[str, Any]],
        reason_text: Optional[str] = None,
    ) -> bool:
        """Return True when spread-exit should bypass bounded-loss quote guards."""
        md = dict(metadata or {})
        urgency = self._resolve_vass_exit_urgency(md, reason_text)
        if urgency == "HARD":
            return True
        if urgency == "SOFT":
            return False
        if bool(md.get("spread_exit_emergency", False)):
            return True
        if not bool(md.get("spread_close_short", False)) and not reason_text:
            return False
        fields = [
            str(md.get("spread_exit_code", "") or ""),
            str(md.get("exit_type", "") or ""),
            str(md.get("spread_exit_reason", "") or ""),
            str(reason_text or ""),
        ]
        merged = " | ".join(fields).upper()
        tokens = (
            "VASS_TAIL_RISK_CAP",
            "SPREAD_HARD_STOP_DURING_HOLD",
            "SPREAD_HARD_STOP_TRIGGERED_PCT",
            "SPREAD_HARD_STOP_TRIGGERED_WIDTH",
            "TRANSITION_DERISK",
            "TAIL_RISK_CAP",
            "HARD_STOP",
            "ASSIGNMENT_RISK",
            "SHORT_LEG_ITM_EXIT",
            "DEEP_ITM_SHORT",
            "OVERNIGHT_ITM_BLOCK",
            "PREMARKET_ITM",
            "MANDATORY_DTE_CLOSE",
            "DTE_EXIT_NO_QUOTE",
            "DTE_EXIT",
            "SPREAD_TIME_STOP_NO_QUOTE",
        )
        return any(token in merged for token in tokens)

    def _resolve_vass_exit_urgency(
        self,
        metadata: Optional[Dict[str, Any]],
        reason_text: Optional[str] = None,
    ) -> str:
        """
        Resolve VASS spread exit urgency.

        Returns:
            "HARD" for urgent capital-protection exits, "SOFT" for thesis-driven
            exits where patient close handling is allowed, or empty string when
            no spread-exit context is present.
        """
        md = dict(metadata or {})
        configured = str(md.get("spread_exit_urgency", "") or "").strip().upper()
        if configured in {"SOFT", "HARD"}:
            return configured
        phase = str(md.get("spread_close_phase", "") or "").strip().upper()
        if phase in {"COMBO_MARKET", "SEQ_MARKET"}:
            return "HARD"
        if bool(md.get("spread_exit_emergency", False)):
            return "HARD"
        if not bool(md.get("spread_close_short", False)):
            return ""

        fields = [
            str(md.get("spread_exit_code", "") or ""),
            str(md.get("exit_type", "") or ""),
            str(md.get("spread_exit_reason", "") or ""),
            str(reason_text or ""),
        ]
        merged = " | ".join(fields).upper()
        hard_tokens = (
            "VASS_TAIL_RISK_CAP",
            "SPREAD_HARD_STOP_DURING_HOLD",
            "SPREAD_HARD_STOP_TRIGGERED_PCT",
            "SPREAD_HARD_STOP_TRIGGERED_WIDTH",
            "TRANSITION_DERISK",
            "TAIL_RISK_CAP",
            "HARD_STOP",
            "ASSIGNMENT_RISK",
            "SHORT_LEG_ITM_EXIT",
            "DEEP_ITM_SHORT",
            "OVERNIGHT_ITM_BLOCK",
            "PREMARKET_ITM",
            "MANDATORY_DTE_CLOSE",
            "DTE_EXIT_NO_QUOTE",
            "DTE_EXIT",
            "SPREAD_TIME_STOP_NO_QUOTE",
            "OVERLAY_STRESS_EXIT",
            "KILL_SWITCH",
            "EMERGENCY",
            "FORCE_CLOSE",
        )
        if any(token in merged for token in hard_tokens):
            return "HARD"
        return "SOFT"

    def _execute_emergency_sequential_close_from_order(
        self,
        order: "OrderIntent",
        quote_detail: str,
    ) -> bool:
        """Immediate sequential close fallback for emergency spread-exit orders."""
        if not (order.is_combo and order.combo_short_symbol):
            return False
        close_qty = max(1, abs(int(order.quantity or 0)))
        self.log(
            f"ROUTER_EMERGENCY_SEQ_TRIGGER: {order.symbol} | Qty={close_qty} | Quote={quote_detail}"
        )
        spread_key = ""
        if order.metadata is not None:
            spread_key = str(order.metadata.get("spread_key", "") or "").strip()
        spread_key_token = self._encode_spread_key_for_tag(spread_key)
        spread_key_suffix = f"|spread_key={spread_key_token}" if spread_key_token else ""
        return self._execute_sequential_close(
            order.symbol,
            order.combo_short_symbol,
            close_qty,
            reason=f"EMERGENCY_QUOTE_INVALID:{quote_detail}",
            tag_prefix=f"SPREAD_CLOSE_SEQ|EMERGENCY{spread_key_suffix}",
        )

    def _is_vass_combo_exit_order(self, order: "OrderIntent") -> bool:
        """True when order is a VASS spread close intent."""
        if not (order.is_combo and order.combo_short_symbol):
            return False
        md = order.metadata if isinstance(order.metadata, dict) else {}
        if not bool(md.get("spread_close_short", False)):
            return False
        lane = str(md.get("options_lane", "") or "").upper()
        strategy = str(md.get("options_strategy", "") or "").upper()
        tag = str(order.tag or "").upper()
        return bool(
            lane == "VASS"
            or "VASS" in strategy
            or "OPT_VASS" in tag
            or "VASS:" in tag
            or bool(md.get("vass_strategy"))
            or bool(md.get("spread_type"))
        )

    def _try_vass_quote_invalid_close_fallback(
        self,
        order: "OrderIntent",
        quote_detail: str,
        tag: str,
    ) -> bool:
        """
        Recover VASS close intents on quote-invalid.

        Policy:
        - HARD urgency exits: allow combo-market fallback.
        - SOFT urgency exits: defer (no combo-market fallback).
        """
        if not self._is_vass_combo_exit_order(order):
            return False
        if not bool(getattr(config, "VASS_CLOSE_QUOTE_INVALID_COMBO_MARKET_RETRY", True)):
            return False
        if self.algorithm is None:
            return False

        md = order.metadata if isinstance(order.metadata, dict) else {}
        source_tag, trace_id = self._extract_trace_context(order.metadata, symbol=order.symbol)

        def _record_quote_recovery(recovery_path: str) -> None:
            self._record_rejection(
                code="R_CONTRACT_QUOTE_INVALID_RECOVERED",
                symbol=str(order.symbol),
                detail=f"{quote_detail} | Recovery={recovery_path}",
                stage="EXECUTE_RECOVERED",
                source_tag=source_tag,
                trace_id=trace_id,
            )

        is_emergency_exit = self._is_emergency_spread_exit(
            order.metadata, getattr(order, "reason", "")
        )
        if not is_emergency_exit:
            self.log(
                "ROUTER_VASS_QUOTE_INVALID_DEFER: "
                f"{order.symbol} | Short={order.combo_short_symbol} | "
                f"{quote_detail} | Urgency=SOFT"
            )
            return False

        try:
            from AlgorithmImports import Leg

            (
                long_qc_symbol,
                short_qc_symbol,
                live_long_qty,
                live_short_qty,
            ) = self._get_live_spread_leg_state(order.symbol, order.combo_short_symbol)
            close_qty = min(max(0, int(live_long_qty or 0)), max(0, int(live_short_qty or 0)))
            if close_qty <= 0 or long_qc_symbol is None or short_qc_symbol is None:
                if bool(getattr(config, "VASS_CLOSE_QUOTE_INVALID_SEQ_FALLBACK", True)):
                    seq_ok = self._execute_emergency_sequential_close_from_order(
                        order, f"QUOTE_INVALID:{quote_detail}"
                    )
                    if seq_ok:
                        _record_quote_recovery("SEQUENTIAL_NO_LIVE_QTY")
                    return seq_ok
                return False

            legs = [Leg.Create(long_qc_symbol, -1), Leg.Create(short_qc_symbol, 1)]
            try:
                self.algorithm.ComboMarketOrder(  # type: ignore[attr-defined]
                    legs, close_qty, tag=tag
                )
            except TypeError:
                self.algorithm.ComboMarketOrder(legs, close_qty, tag)  # type: ignore[attr-defined]
            self.log(
                f"ROUTER_VASS_QUOTE_INVALID_COMBO_MARKET_RETRY: {order.symbol} | "
                f"Short={order.combo_short_symbol} | Qty={close_qty} | {quote_detail}"
            )
            _record_quote_recovery("COMBO_MARKET_RETRY")
            return True
        except Exception as e:
            self.log(f"ROUTER_VASS_QUOTE_INVALID_COMBO_MARKET_FAIL: {order.symbol} | {e}")
            if bool(getattr(config, "VASS_CLOSE_QUOTE_INVALID_SEQ_FALLBACK", True)):
                seq_ok = self._execute_emergency_sequential_close_from_order(
                    order, f"QUOTE_INVALID:{quote_detail}"
                )
                if seq_ok:
                    _record_quote_recovery("SEQUENTIAL_AFTER_COMBO_FAIL")
                return seq_ok
            return False

    def _get_live_spread_leg_state(
        self,
        long_symbol: str,
        short_symbol: str,
    ) -> Tuple[Optional[Any], Optional[Any], int, int]:
        """Resolve live portfolio symbols and quantities for spread legs."""
        long_qc_symbol = None
        short_qc_symbol = None
        live_long_qty = 0
        live_short_qty = 0

        if not self.algorithm:
            return long_qc_symbol, short_qc_symbol, live_long_qty, live_short_qty

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

        return long_qc_symbol, short_qc_symbol, live_long_qty, live_short_qty

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

            # Find actual QC Symbol objects + live quantities from portfolio.
            (
                long_qc_symbol,
                short_qc_symbol,
                live_long_qty,
                live_short_qty,
            ) = self._get_live_spread_leg_state(long_symbol, short_symbol)

            if live_long_qty <= 0 and live_short_qty <= 0:
                self.log(
                    f"ROUTER: COMBO_CLOSE_ALREADY_FLAT | {reason} | "
                    f"Long={long_symbol} Short={short_symbol}"
                )
                return True

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
            use_protected_exit = bool(getattr(config, "SPREAD_EXIT_PROTECTED_COMBO_ENABLED", True))
            protected_limit, protected_detail = self._compute_protected_exit_combo_limit(
                long_symbol, short_symbol
            )
            if use_protected_exit and protected_limit is None:
                self.log(
                    f"ROUTER: COMBO_CLOSE_PROTECTED_FALLBACK | {reason} | "
                    f"QuoteDetail={protected_detail}"
                )

            # Retry loop
            for attempt in range(1, config.COMBO_ORDER_MAX_RETRIES + 1):
                try:
                    legs = [
                        Leg.Create(long_qc_symbol, -1),  # Sell long (ratio -1)
                        Leg.Create(short_qc_symbol, 1),  # Buy short (ratio +1)
                    ]

                    submitted_as = "MARKET"
                    if use_protected_exit and protected_limit is not None:
                        submitted_as = "LIMIT"
                        try:
                            self.algorithm.ComboLimitOrder(  # type: ignore[attr-defined]
                                legs, close_qty, float(protected_limit), tag=tag
                            )
                        except TypeError:
                            try:
                                self.algorithm.ComboLimitOrder(  # type: ignore[attr-defined]
                                    legs, close_qty, float(protected_limit), tag
                                )
                            except Exception:
                                # API/runtime fallback keeps close path resilient.
                                submitted_as = "MARKET_FALLBACK"
                                try:
                                    self.algorithm.ComboMarketOrder(  # type: ignore[attr-defined]
                                        legs, close_qty, tag=tag
                                    )
                                except TypeError:
                                    self.algorithm.ComboMarketOrder(  # type: ignore[attr-defined]
                                        legs, close_qty, tag
                                    )
                    else:
                        try:
                            self.algorithm.ComboMarketOrder(  # type: ignore[attr-defined]
                                legs, close_qty, tag=tag
                            )
                        except TypeError:
                            self.algorithm.ComboMarketOrder(  # type: ignore[attr-defined]
                                legs, close_qty, tag
                            )
                    self.log(
                        f"ROUTER: COMBO_CLOSE_SUCCESS | {reason} | "
                        f"Attempt {attempt}/{config.COMBO_ORDER_MAX_RETRIES} | "
                        f"Mode={submitted_as} | Spreads={close_qty} | {protected_detail}"
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
        (
            long_qc_symbol,
            short_qc_symbol,
            live_long_qty,
            live_short_qty,
        ) = self._get_live_spread_leg_state(long_symbol, short_symbol)

        if live_long_qty <= 0 and live_short_qty <= 0:
            self.log(
                f"ROUTER: SEQUENTIAL_ALREADY_FLAT | {reason} | "
                f"Long={long_symbol} Short={short_symbol}"
            )
            return True

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
            if live_short_qty <= 0:
                self.log(f"ROUTER: SEQUENTIAL_SHORT_ALREADY_FLAT | {reason} | {short_symbol}")
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
            if live_long_qty <= 0:
                self.log(f"ROUTER: SEQUENTIAL_LONG_ALREADY_FLAT | {reason} | {long_symbol}")
            else:
                self.log(
                    f"ROUTER: SEQUENTIAL_LONG_NOTFOUND | {reason} | {long_symbol} | "
                    f"LiveQty={live_long_qty}"
                )

        if short_closed and long_closed:
            self.log(f"ROUTER: SEQUENTIAL_COMPLETE | {reason}")
            return True
        elif short_closed and live_long_qty <= 0:
            self.log(f"ROUTER: SEQUENTIAL_COMPLETE | {reason} | Long already flat")
            return True
        elif long_closed and live_short_qty <= 0:
            self.log(f"ROUTER: SEQUENTIAL_COMPLETE | {reason} | Short already flat")
            return True
        elif short_closed:
            self.log(
                f"ROUTER: SEQUENTIAL_PARTIAL_SUBMITTED | {reason} | "
                f"SHORT closed, LONG failed - long exposure remains"
            )
            return True
        elif long_closed:
            self.log(
                f"ROUTER: SEQUENTIAL_PARTIAL_SHORT_REMAIN | {reason} | "
                f"LONG closed, SHORT still open"
            )
            return False
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
                ticket = None
                if tag:
                    try:
                        ticket = self.algorithm.MarketOrder(symbol, quantity, tag=tag)
                    except TypeError:
                        try:
                            ticket = self.algorithm.MarketOrder(symbol, quantity, tag)
                        except TypeError:
                            ticket = self.algorithm.MarketOrder(symbol, quantity)
                else:
                    ticket = self.algorithm.MarketOrder(symbol, quantity)
                self._cache_submitted_order_tags(ticket, str(tag or ""))
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
            ticket = None
            if tag:
                try:
                    ticket = self.algorithm.LimitOrder(symbol, quantity, limit_price, tag=tag)
                except TypeError:
                    try:
                        ticket = self.algorithm.LimitOrder(symbol, quantity, limit_price, tag)
                    except TypeError:
                        ticket = self.algorithm.LimitOrder(symbol, quantity, limit_price)
            else:
                ticket = self.algorithm.LimitOrder(symbol, quantity, limit_price)
            self._cache_submitted_order_tags(ticket, str(tag or ""))
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
        is_emergency_exit = self._is_emergency_spread_exit(
            order.metadata, getattr(order, "reason", "")
        )

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
                if (
                    bool(getattr(config, "SPREAD_EXIT_BOUNDED_LOSS_GUARD_ENABLED", True))
                    and not is_emergency_exit
                ):
                    md = dict(order.metadata or {})
                    exit_net_value = long_bid - short_ask
                    net_floor = float(getattr(config, "SPREAD_EXIT_NET_VALUE_FLOOR", 0.0))
                    net_tol = float(getattr(config, "SPREAD_EXIT_NET_VALUE_TOLERANCE", -0.05))
                    is_credit = bool(md.get("is_credit_spread", False)) or (
                        "CREDIT" in str(md.get("spread_type", "")).upper()
                    )
                    # Debit spreads should normally exit for a non-negative net credit.
                    # Credit spreads often close for a net debit (negative net value),
                    # so this guard must not block them.
                    if (not is_credit) and exit_net_value < (net_floor + net_tol):
                        return (
                            False,
                            "EXIT_NET_VALUE_NEGATIVE "
                            f"Net={exit_net_value:.4f} < Floor={net_floor + net_tol:.4f} | "
                            f"LongBid={long_bid:.4f} ShortAsk={short_ask:.4f}",
                        )
                    if not is_credit:
                        try:
                            entry_debit = float(md.get("spread_entry_debit", 0.0) or 0.0)
                        except (TypeError, ValueError):
                            entry_debit = 0.0
                        close_debit = max(0.0, short_ask - long_bid)
                        if entry_debit > 0:
                            debit_buffer = float(
                                getattr(config, "SPREAD_EXIT_MAX_CLOSE_DEBIT_BUFFER_PCT", 0.10)
                            )
                            max_close_debit = entry_debit * (1.0 + max(0.0, debit_buffer))
                            if close_debit > max_close_debit:
                                return (
                                    False,
                                    "EXIT_CLOSE_DEBIT_EXCEEDS_ENTRY "
                                    f"CloseDebit={close_debit:.4f} > Max={max_close_debit:.4f} "
                                    f"(EntryDebit={entry_debit:.4f})",
                                )
                elif is_emergency_exit:
                    self.log(
                        f"ROUTER_EXIT_EMERGENCY_GUARD_BYPASS: {order.symbol} | "
                        f"Reason={getattr(order, 'reason', '')[:80]}"
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

    def _compute_protected_exit_combo_limit(
        self, long_symbol: str, short_symbol: str
    ) -> Tuple[Optional[float], str]:
        """
        Compute a marketable limit credit for combo exits.

        For spread close (SELL long / BUY short), executable net credit is:
            long_bid - short_ask
        We concede a small buffer so the order stays marketable while still
        protecting against clearly broken fills.
        """
        if self.algorithm is None:
            return None, "NO_ALGO"
        try:
            long_sec = self.algorithm.Securities[long_symbol]  # type: ignore[attr-defined]
            short_sec = self.algorithm.Securities[short_symbol]  # type: ignore[attr-defined]
            long_bid = float(getattr(long_sec, "BidPrice", 0.0) or 0.0)
            long_ask = float(getattr(long_sec, "AskPrice", 0.0) or 0.0)
            short_bid = float(getattr(short_sec, "BidPrice", 0.0) or 0.0)
            short_ask = float(getattr(short_sec, "AskPrice", 0.0) or 0.0)
            if long_bid <= 0 or long_ask <= 0 or short_bid <= 0 or short_ask <= 0:
                return None, (
                    f"BAD_QUOTE long({long_bid:.4f}/{long_ask:.4f}) "
                    f"short({short_bid:.4f}/{short_ask:.4f})"
                )
            executable_credit = long_bid - short_ask
            total_leg_spread = max(0.0, long_ask - long_bid) + max(0.0, short_ask - short_bid)
            slip_pct = float(getattr(config, "SPREAD_EXIT_COMBO_LIMIT_SLIPPAGE_PCT", 0.20))
            min_step = float(getattr(config, "SPREAD_EXIT_COMBO_LIMIT_MIN_STEP", 0.01))
            credit_concession = max(min_step, total_leg_spread * max(0.0, slip_pct))
            limit_credit = executable_credit - credit_concession
            return (
                float(limit_credit),
                (
                    f"Exec={executable_credit:.4f} Limit={limit_credit:.4f} "
                    f"Concession={credit_concession:.4f} Spread={total_leg_spread:.4f}"
                ),
            )
        except Exception as e:
            return None, f"LIMIT_CALC_ERROR: {e}"

    def _append_spread_exit_rca_tag(self, base_tag: str, metadata: Optional[Dict[str, Any]]) -> str:
        """
        Persist compact spread-exit RCA context in order tags.

        This survives QC log truncation because orders.csv always carries tags.
        """
        tag = str(base_tag or "").strip()
        md = dict(metadata or {})
        if not tag or not bool(md.get("spread_close_short", False)):
            return tag

        def _compact_exit_code(raw: Any) -> str:
            token = str(raw or "").strip().upper()
            if not token:
                return ""
            token = token.split(":", 1)[0].split(" ", 1)[0]
            cleaned = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in token)
            cleaned = "_".join(part for part in cleaned.split("_") if part)
            return cleaned[:32]

        additions: List[str] = []
        exit_code = _compact_exit_code(md.get("spread_exit_code", ""))
        if not exit_code:
            exit_code = _compact_exit_code(md.get("exit_type", ""))
        if not exit_code:
            exit_code = _compact_exit_code(md.get("spread_exit_reason", ""))
        if exit_code:
            additions.append(f"xcode={exit_code}")
        spread_type = str(md.get("spread_type", "") or "").strip().upper()
        if spread_type:
            additions.append(f"xst={spread_type[:20]}")
        try:
            entry_debit = float(md.get("spread_entry_debit", 0.0) or 0.0)
            if entry_debit > 0:
                additions.append(f"ed={entry_debit:.2f}")
        except Exception:
            pass
        try:
            est_net = float(md.get("spread_exit_estimated_net_value", 0.0) or 0.0)
            additions.append(f"em={est_net:.2f}")
        except Exception:
            pass
        close_path = str(md.get("spread_close_path", "") or "").strip().upper()
        if close_path:
            additions.append(f"xcp={close_path[:20]}")
        exit_urgency = str(md.get("spread_exit_urgency", "") or "").strip().upper()
        if exit_urgency in {"SOFT", "HARD"}:
            additions.append(f"xurg={exit_urgency}")
        escalation_reason = str(md.get("spread_close_escalation_reason", "") or "").strip().upper()
        if escalation_reason:
            additions.append(f"xer={escalation_reason[:20]}")
        try:
            close_attempt_count = int(md.get("spread_close_attempt_count", 0) or 0)
            if close_attempt_count > 0:
                additions.append(f"xatt={close_attempt_count}")
        except Exception:
            pass
        try:
            close_latency_sec = int(md.get("spread_close_latency_sec", 0) or 0)
            if close_latency_sec >= 0:
                additions.append(f"xlat={close_latency_sec}")
        except Exception:
            pass

        if not additions:
            return tag

        max_tag_len = 220
        out = tag
        for part in additions:
            marker = part.split("=", 1)[0].lower()
            if marker and f"{marker}=" in out.lower():
                continue
            candidate = f"{out}|{part}"
            if len(candidate) > max_tag_len:
                break
            out = candidate
        return out

    # =========================================================================
    # Step 1: COLLECT
    # =========================================================================

    def _ensure_signal_trace(self, weight: TargetWeight) -> None:
        """Ensure every signal has stable trace attribution for RCA."""
        md = dict(weight.metadata or {})
        if not md.get("trace_source"):
            md["trace_source"] = str(weight.source or "UNKNOWN")
        if not md.get("trace_id"):
            self._signal_seq += 1
            date_part = "NO_TIME"
            try:
                if self.algorithm is not None and hasattr(self.algorithm, "Time"):
                    date_part = str(self.algorithm.Time).replace(" ", "T")[:19]
            except Exception:
                date_part = "NO_TIME"
            md["trace_id"] = f"SIG-{date_part}-{self._signal_seq}"
        if not md.get("signal_id"):
            md["signal_id"] = md.get("trace_id")
        weight.metadata = md

    def receive_signal(self, weight: TargetWeight) -> None:
        """
        Receive a TargetWeight signal from a strategy engine.

        Args:
            weight: TargetWeight signal to process.
        """
        self._ensure_signal_trace(weight)
        try:
            md = dict(weight.metadata or {})
            if bool(md.get("spread_close_short", False)):
                trace_id = str(md.get("trace_id", "") or "")
                exit_code = str(md.get("spread_exit_code", "") or "").strip().upper()
                if not exit_code:
                    exit_code = str(md.get("exit_type", "") or "").strip().upper()
                if not exit_code:
                    reason_token = str(weight.reason or "").strip().upper()
                    exit_code = (
                        reason_token.split(":", 1)[0].split(" ", 1)[0] if reason_token else ""
                    )
                recorder = getattr(self.algorithm, "_record_order_lifecycle_event", None)
                if callable(recorder):
                    recorder(
                        status="SPREAD_EXIT_SIGNAL",
                        order_id=0,
                        symbol=str(weight.symbol or ""),
                        quantity=int(weight.requested_quantity or 0),
                        fill_price=0.0,
                        order_type="SIGNAL",
                        order_tag=str(weight.source or "OPT"),
                        trace_id=trace_id,
                        message=(
                            f"Reason={str(weight.reason or '')[:180]} | "
                            f"Short={str(md.get('spread_short_leg_symbol', '') or '')} | "
                            f"Key={str(md.get('spread_key', '') or '')} | "
                            f"ExitCode={exit_code}"
                        ),
                        source="ROUTER_RECEIVE",
                    )
        except Exception:
            pass
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

    def get_pending_signals(self) -> List[TargetWeight]:
        """Return a shallow copy of pending signals."""
        return list(self._pending_weights)

    def drain_pending_signals(self) -> List[TargetWeight]:
        """Atomically copy and clear pending signals."""
        weights = list(self._pending_weights)
        self._pending_weights.clear()
        return weights

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
        source_norm = self._normalize_options_source_tag(source_tag, metadata=None, symbol=symbol)
        item = RouterRejection(
            code=code,
            symbol=symbol,
            source_tag=source_norm,
            trace_id=trace_id,
            detail=detail,
            stage=stage,
        )
        self._last_rejections.append(item)
        should_log, suppressed = self._should_log_rejection_event(code=code, stage=stage)
        if not should_log:
            return
        suppressed_suffix = f" | Suppressed={suppressed}" if suppressed > 0 else ""
        self.log(
            f"ROUTER_REJECT: Code={code} | Stage={stage} | Symbol={symbol} | "
            f"Source={source_norm or 'UNKNOWN'} | Trace={trace_id or 'NONE'} | "
            f"{detail}{suppressed_suffix}"
        )

    def _extract_trace_context(
        self,
        metadata: Optional[Dict[str, Any]],
        sources: Optional[List[str]] = None,
        symbol: Optional[str] = None,
    ) -> Tuple[str, str]:
        """Derive source tag and trace id for diagnostics."""
        source_tag = ""
        trace_id = ""
        if metadata:
            trace_id = str(metadata.get("trace_id", "") or "")
            source_tag = str(metadata.get("trace_source", "") or "")
            if not source_tag:
                lane, strategy = self._extract_options_lane_and_strategy(metadata)
                if lane and strategy:
                    source_tag = f"{lane}:{strategy}"
                elif lane:
                    source_tag = lane
                elif metadata.get("vass_strategy"):
                    source_tag = f"VASS:{metadata.get('vass_strategy')}"
                elif metadata.get("spread_type"):
                    source_tag = f"VASS:{metadata.get('spread_type')}"
        if not source_tag and sources:
            if "OPT_INTRADAY" in sources:
                inferred_lane = ""
                if metadata:
                    inferred_lane, _ = self._extract_options_lane_and_strategy(metadata)
                if not inferred_lane:
                    inferred_lane = self._infer_options_lane_from_symbol(symbol)
                source_tag = (
                    f"OPT_{inferred_lane}" if inferred_lane in {"ITM", "MICRO"} else "OPT_UNKNOWN"
                )
            elif "OPT" in sources:
                inferred_lane = self._infer_options_lane_from_symbol(symbol)
                source_tag = (
                    f"OPT_{inferred_lane}" if inferred_lane in {"ITM", "MICRO"} else "OPT_VASS"
                )
            elif "RISK" in sources and self._is_option_symbol(symbol or ""):
                inferred_lane = ""
                if metadata:
                    inferred_lane, _ = self._extract_options_lane_and_strategy(metadata)
                if not inferred_lane:
                    inferred_lane = self._infer_options_lane_from_symbol(symbol)
                if inferred_lane in {"ITM", "MICRO"}:
                    source_tag = f"OPT_{inferred_lane}"
                elif metadata and (
                    bool(metadata.get("spread_close_short", False))
                    or metadata.get("vass_strategy")
                    or metadata.get("spread_type")
                ):
                    source_tag = "OPT_VASS"
                else:
                    source_tag = "OPT_UNKNOWN"
        source_tag = self._normalize_options_source_tag(source_tag, metadata, symbol)
        return source_tag, trace_id

    def _normalize_options_source_tag(
        self,
        source_tag: str,
        metadata: Optional[Dict[str, Any]],
        symbol: Optional[str],
    ) -> str:
        """Normalize option-related source tags to lane-aware canonical names."""
        normalized = str(source_tag or "").strip()
        if not normalized:
            return ""
        upper = normalized.upper()
        md = metadata or {}
        lane, _ = self._extract_options_lane_and_strategy(md) if md else ("", "")
        lane = str(lane or "").upper()
        inferred_lane = (
            lane if lane in {"ITM", "MICRO"} else self._infer_options_lane_from_symbol(symbol)
        )

        if upper in {"OPT_ITM", "OPT_MICRO", "OPT_VASS", "OPT_UNKNOWN"}:
            return upper
        if upper == "RISK" and self._is_option_symbol(str(symbol or "")):
            if inferred_lane in {"ITM", "MICRO"}:
                return f"OPT_{inferred_lane}"
            if (
                bool(md.get("spread_close_short", False))
                or md.get("vass_strategy")
                or md.get("spread_type")
            ):
                return "OPT_VASS"
            return "OPT_UNKNOWN"
        if upper.startswith("ITM") or " ITM" in f" {upper} ":
            return "OPT_ITM"
        if upper.startswith("MICRO") or " MICRO" in f" {upper} ":
            return "OPT_MICRO"
        if "VASS" in upper or upper.startswith("SPREAD"):
            return "OPT_VASS"
        if upper.startswith("OPT_INTRADAY"):
            if inferred_lane in {"ITM", "MICRO"}:
                return f"OPT_{inferred_lane}"
            return "OPT_UNKNOWN"
        if upper.startswith("OPT"):
            if inferred_lane in {"ITM", "MICRO"}:
                return f"OPT_{inferred_lane}"
            if (
                bool(md.get("spread_close_short", False))
                or md.get("vass_strategy")
                or md.get("spread_type")
            ):
                return "OPT_VASS"
            if self._is_option_symbol(str(symbol or "")):
                return "OPT_VASS"
        return normalized

    def _infer_options_lane_from_symbol(self, symbol: Optional[str]) -> str:
        """
        Infer intraday options lane from live engine position tracking.

        Returns:
            "ITM" or "MICRO" when the symbol is currently tracked in that lane,
            otherwise empty string.
        """
        symbol_key = self._normalize_symbol_key(symbol)
        if not symbol_key or self.algorithm is None:
            return ""
        options_engine = getattr(self.algorithm, "options_engine", None)
        if options_engine is None:
            return ""

        lane: str = ""
        lookup = getattr(options_engine, "find_engine_lane_by_symbol", None)
        if callable(lookup):
            try:
                lane = str(lookup(symbol_key) or "").strip().upper()
            except Exception:
                lane = ""

        if not lane:
            legacy_lookup = getattr(options_engine, "_find_engine_lane_by_symbol", None)
            if callable(legacy_lookup):
                try:
                    lane = str(legacy_lookup(symbol_key) or "").strip().upper()
                except Exception:
                    lane = ""

        return lane if lane in {"ITM", "MICRO"} else ""

    def _extract_options_lane_and_strategy(
        self,
        metadata: Optional[Dict[str, Any]],
    ) -> Tuple[str, str]:
        """Resolve options lane/strategy using lane-neutral keys with legacy fallback."""
        if not isinstance(metadata, dict):
            return "", ""

        lane = str(metadata.get("options_lane", "") or "").strip().upper()
        strategy = (
            str(metadata.get("options_strategy", "") or metadata.get("intraday_strategy", "") or "")
            .strip()
            .upper()
        )

        if not lane:
            trace_source = str(metadata.get("trace_source", "") or "").strip().upper()
            if trace_source.startswith("ITM"):
                lane = "ITM"
            elif trace_source.startswith("MICRO"):
                lane = "MICRO"
            elif "ITM" in strategy:
                lane = "ITM"
            elif strategy and strategy not in {"UNCLASSIFIED", "NO_TRADE", "UNKNOWN"}:
                lane = "MICRO"

        if lane not in {"ITM", "MICRO"}:
            lane = ""
        return lane, strategy

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
        # Track seen keys to prevent duplicates from the same source.
        # Spread closes are keyed by (long, short) legs so concurrent spreads
        # sharing a long leg do not collapse into malformed combo quantities.
        seen_pairs: Set[tuple] = set()

        for weight in weights:
            symbol = weight.symbol
            source = weight.source
            is_spread_close = bool(
                weight.metadata and weight.metadata.get("spread_close_short", False)
            )
            short_symbol = (
                str(weight.metadata.get("spread_short_leg_symbol", "")).strip()
                if is_spread_close and weight.metadata
                else ""
            )
            agg_key = symbol
            if is_spread_close and short_symbol:
                agg_key = f"{symbol}::SPREAD::{short_symbol}"
            elif source == "OPT_INTRADAY":
                lane_tag = ""
                if isinstance(weight.metadata, dict):
                    lane_tag, _ = self._extract_options_lane_and_strategy(weight.metadata)
                if lane_tag:
                    agg_key = f"{symbol}::INTRADAY::{lane_tag}"
            pair_key = (agg_key, source)

            # Skip duplicate signals from same source for same aggregation key.
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)

            if agg_key not in aggregated:
                aggregated[agg_key] = AggregatedWeight(
                    symbol=symbol,
                    target_weight=0.0,
                    sources=[],
                    urgency=Urgency.EOD,
                    reasons=[],
                )

            agg = aggregated[agg_key]

            # SUM weights from different sources (allows strategy layering)
            agg.target_weight += weight.target_weight

            # Track sources
            if weight.source not in agg.sources:
                agg.sources.append(weight.source)

            # Track reasons
            if weight.reason:
                agg.reasons.append(f"[{weight.source}] {weight.reason}")

            # IMMEDIATE takes precedence
            if weight.urgency == Urgency.IMMEDIATE:
                agg.urgency = Urgency.IMMEDIATE

            # Preserve requested quantity for options
            if weight.requested_quantity is not None:
                agg.requested_quantity = weight.requested_quantity

            # Preserve metadata for spread/order plumbing
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

        for agg_key, agg in aggregated.items():
            symbol = agg.symbol
            source_tag, trace_id = self._extract_trace_context(agg.metadata, agg.sources, symbol)
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
                inferred_qty = abs(self._get_live_option_qty(symbol))
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
                is_spread_close = bool(
                    agg.metadata and agg.metadata.get("spread_close_short", False)
                )
                source_tag_upper = str(source_tag or "").upper()
                lane_tag = (
                    str((agg.metadata or {}).get("options_lane", "") or "").strip().upper()
                    if isinstance(agg.metadata, dict)
                    else ""
                )
                is_vass_spread_close = bool(
                    is_spread_close
                    and (
                        source_tag_upper.startswith("OPT_VASS")
                        or source_tag_upper.startswith("VASS:")
                        or lane_tag == "VASS"
                    )
                )
                live_qty = self._get_live_option_qty(symbol)
                if live_qty != 0:
                    self._stale_close_reject_cooldown_until.pop(
                        self._normalize_symbol_key(symbol), None
                    )
                if live_qty == 0:
                    if is_vass_spread_close:
                        self.log(
                            "ROUTER: CLOSE_INTENT_ALREADY_FLAT | "
                            f"{symbol} | Source={source_tag or 'OPT_VASS'}"
                        )
                        self._stale_close_reject_cooldown_until.pop(
                            self._normalize_symbol_key(symbol), None
                        )
                        continue
                    if not is_spread_close and self._is_stale_close_reject_cooldown_active(symbol):
                        continue
                    stale_cleanup = []
                    if not is_spread_close:
                        stale_cleanup = self._evict_stale_option_close_intent(symbol)
                    detail = "Close intent but no live holdings"
                    if stale_cleanup:
                        detail = f"{detail} | Cleared={','.join(stale_cleanup)}"
                    self._record_rejection(
                        code="R_CLOSE_NO_LIVE_HOLDING",
                        symbol=symbol,
                        detail=detail,
                        stage="INTENT_BUILD",
                        source_tag=source_tag,
                        trace_id=trace_id,
                    )
                    if not is_spread_close:
                        self._arm_stale_close_reject_cooldown(symbol)
                    continue

                side = OrderSide.SELL if live_qty > 0 else OrderSide.BUY
                if is_spread_close:
                    requested_qty = int(agg.requested_quantity or 0)
                    if requested_qty <= 0 and agg.metadata:
                        try:
                            requested_qty = int(
                                agg.metadata.get("spread_short_leg_quantity", 0) or 0
                            )
                        except Exception:
                            requested_qty = 0
                    if requested_qty <= 0:
                        self._record_rejection(
                            code="R_CLOSE_NO_QTY",
                            symbol=symbol,
                            detail="Spread close missing requested_quantity",
                            stage="INTENT_BUILD",
                            source_tag=source_tag,
                            trace_id=trace_id,
                        )
                        continue
                    capped_qty = min(abs(live_qty), requested_qty)
                    if capped_qty <= 0:
                        self._record_rejection(
                            code="R_CLOSE_NO_LIVE_HOLDING",
                            symbol=symbol,
                            detail="Spread close has zero capped live quantity",
                            stage="INTENT_BUILD",
                            source_tag=source_tag,
                            trace_id=trace_id,
                        )
                        continue
                    if capped_qty != requested_qty:
                        self.log(
                            f"ROUTER: SPREAD_CLOSE_QTY_CAP | {symbol} | "
                            f"Requested={requested_qty} Live={abs(live_qty)} Used={capped_qty}"
                        )
                    delta_shares = capped_qty
                else:
                    delta_shares = abs(live_qty)

            # V2.3.24: Use lower threshold for intraday options
            # Single option contracts often $500-1,500, below the $2,000 MIN_TRADE_VALUE
            min_trade_value = config.MIN_TRADE_VALUE
            if is_option and any(s in ("OPT_INTRADAY", "OPT") for s in agg.sources):
                min_trade_value = config.MIN_INTRADAY_OPTIONS_TRADE_VALUE

            # V10.7: Exempt close/protective option intents from min-trade floor.
            is_protective_intent = False
            if is_option and agg.metadata:
                _, intraday_strategy = self._extract_options_lane_and_strategy(agg.metadata)
                vass_strategy = str(agg.metadata.get("vass_strategy", "") or "").upper()
                reason_blob = " ".join(str(r) for r in agg.reasons).upper() if agg.reasons else ""
                is_protective_intent = (
                    intraday_strategy == "PROTECTIVE_PUTS"
                    or "PROTECTIVE" in vass_strategy
                    or "PROTECTIVE" in reason_blob
                )

            close_exempt = bool(getattr(config, "OPTIONS_MIN_TRADE_VALUE_CLOSE_EXEMPT", True))
            protective_exempt = bool(
                getattr(config, "OPTIONS_MIN_TRADE_VALUE_PROTECTIVE_EXEMPT", True)
            )
            bypass_min_trade = (is_closing and close_exempt) or (
                is_protective_intent and protective_exempt
            )

            # Skip if position value below minimum trade size (unless exempt intent)
            if abs(delta_value) < min_trade_value and not bypass_min_trade:
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
            # Ensure every intent has a deterministic, non-empty tag for lifecycle attribution.
            tag = None
            if is_option:
                if any(s == "OPT_INTRADAY" for s in agg.sources):
                    lane_tag = ""
                    intraday_strategy = ""
                    if agg.metadata:
                        lane_tag, intraday_strategy = self._extract_options_lane_and_strategy(
                            agg.metadata
                        )
                    if not intraday_strategy:
                        reason_blob_upper = ("; ".join(agg.reasons)).upper() if agg.reasons else ""
                        source_blob_upper = str(source_tag or "").upper()
                        if (
                            "ITM_MOMENTUM" in reason_blob_upper
                            or "ITM_MOMENTUM" in source_blob_upper
                        ):
                            intraday_strategy = "ITM_MOMENTUM"
                        elif (
                            "PROTECTIVE_PUTS" in reason_blob_upper
                            or "PROTECTIVE_PUTS" in source_blob_upper
                            or "PROTECTIVE" in reason_blob_upper
                        ):
                            intraday_strategy = "PROTECTIVE_PUTS"
                        elif (
                            "MICRO_DEBIT_FADE" in reason_blob_upper
                            or "DEBIT_FADE" in reason_blob_upper
                        ):
                            intraday_strategy = "MICRO_DEBIT_FADE"
                        elif (
                            "MICRO_OTM_MOMENTUM" in reason_blob_upper
                            or "OTM_MOMENTUM" in reason_blob_upper
                        ):
                            intraday_strategy = "MICRO_OTM_MOMENTUM"
                        elif (
                            "MICRO_EOD_SWEEP" in reason_blob_upper
                            or "INTRADAY_FORCE_EXIT" in reason_blob_upper
                        ):
                            intraday_strategy = "MICRO_OTM_MOMENTUM"
                    if not lane_tag:
                        if is_closing:
                            lane_tag = self._infer_options_lane_from_symbol(symbol)
                    elif is_closing and intraday_strategy in {"", "UNCLASSIFIED"}:
                        inferred_lane = self._infer_options_lane_from_symbol(symbol)
                        if inferred_lane in {"ITM", "MICRO"}:
                            lane_tag = inferred_lane
                    if not lane_tag:
                        if "ITM_MOMENTUM" in intraday_strategy:
                            lane_tag = "ITM"
                        elif intraday_strategy and intraday_strategy not in {"UNCLASSIFIED"}:
                            lane_tag = "MICRO"
                    if lane_tag == "ITM":
                        tag = f"ITM:{intraday_strategy}"
                    elif "PROTECTIVE_PUTS" in intraday_strategy:
                        tag = f"MICRO:{intraday_strategy}"
                    elif intraday_strategy:
                        if lane_tag:
                            tag = f"{lane_tag}:{intraday_strategy}"
                        else:
                            tag = f"OPT_UNKNOWN:{intraday_strategy}"
                    elif lane_tag:
                        tag = f"{lane_tag}:UNCLASSIFIED"
                    else:
                        tag = "OPT_UNKNOWN:UNCLASSIFIED"
                elif any(s == "OPT" for s in agg.sources):
                    inferred_intraday_lane = ""
                    inferred_intraday_strategy = ""
                    if agg.metadata:
                        (
                            inferred_intraday_lane,
                            inferred_intraday_strategy,
                        ) = self._extract_options_lane_and_strategy(agg.metadata)

                    vass_strategy = None
                    spread_type = None
                    if agg.metadata:
                        vass_strategy = agg.metadata.get("vass_strategy")
                        spread_type = agg.metadata.get("spread_type")
                    has_vass_metadata = bool(
                        vass_strategy
                        or spread_type
                        or (agg.metadata and agg.metadata.get("spread_close_short"))
                    )
                    if not has_vass_metadata and is_closing:
                        if not inferred_intraday_lane:
                            inferred_intraday_lane = self._infer_options_lane_from_symbol(symbol)
                        if inferred_intraday_lane in {"ITM", "MICRO"}:
                            strategy_tag = inferred_intraday_strategy or "UNCLASSIFIED"
                            tag = f"{inferred_intraday_lane}:{strategy_tag}"

                    if not tag:
                        vass_tag_value = str(
                            vass_strategy or spread_type or source_tag or "VASS_UNCLASSIFIED"
                        )
                        tag = f"VASS:{vass_tag_value}"
                elif any(s == "RISK" for s in agg.sources):
                    lane_tag = ""
                    intraday_strategy = ""
                    if agg.metadata:
                        lane_tag, intraday_strategy = self._extract_options_lane_and_strategy(
                            agg.metadata
                        )
                    if not lane_tag:
                        lane_tag = self._infer_options_lane_from_symbol(symbol)
                    if lane_tag in {"ITM", "MICRO"}:
                        strategy_tag = intraday_strategy or "RISK_EXIT"
                        tag = f"{lane_tag}:{strategy_tag}"
                    elif agg.metadata and (
                        bool(agg.metadata.get("spread_close_short", False))
                        or agg.metadata.get("vass_strategy")
                        or agg.metadata.get("spread_type")
                    ):
                        vass_tag_value = str(
                            agg.metadata.get("vass_strategy")
                            or agg.metadata.get("spread_type")
                            or "RISK_EXIT"
                        )
                        tag = f"VASS:{vass_tag_value}"
                    else:
                        tag = "OPT:RISK_EXIT"

            # Fallback for non-option paths or missing metadata.
            if not tag:
                if source_tag:
                    tag = str(source_tag)
                elif agg.sources:
                    tag = str(agg.sources[0])
                else:
                    tag = "UNCLASSIFIED"

            # Preserve signal->order->fill trace linkage in order tags for RCA.
            if trace_id and "trace=" not in tag.lower():
                tag = f"{tag}|trace={trace_id}"
            if agg.metadata and bool(agg.metadata.get("spread_close_short", False)):
                spread_key = str(agg.metadata.get("spread_key", "") or "").strip()
                spread_key_token = self._encode_spread_key_for_tag(spread_key)
                if spread_key_token and "spread_key=" not in tag.lower():
                    tag = f"{tag}|spread_key={spread_key_token}"

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
                        # Closing spread - sell long, buy short.
                        # Keep both legs at identical absolute quantity to avoid
                        # leg mismatch when multiple spreads share symbols.
                        close_qty = abs(int(delta_shares))
                        declared_short_qty = abs(int(short_leg_qty))
                        if declared_short_qty != close_qty:
                            self.log(
                                f"ROUTER: SPREAD_CLOSE_QTY_NORMALIZED | {symbol} | "
                                f"LongQty={close_qty} ShortQty={declared_short_qty} -> {close_qty}"
                            )
                        long_qty = -close_qty
                        short_qty = close_qty  # Positive = BUY back
                        if agg.metadata is not None:
                            agg.metadata["spread_short_leg_quantity"] = close_qty
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
                    order.metadata, [order.tag] if order.tag else None, order.symbol
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
                    order.metadata, [order.tag] if order.tag else None, order.symbol
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
                                    order.metadata, [order.tag] if order.tag else None, order.symbol
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
                order.metadata, [order.tag] if order.tag else None, order.symbol
            )
            if not source_tag and order.tag:
                source_tag = order.tag
            lane_hint = ""
            strategy_hint = ""
            close_hint = ""
            if isinstance(order.metadata, dict):
                lane_hint = str(order.metadata.get("options_lane", "") or "").upper()
                strategy_hint = str(order.metadata.get("options_strategy", "") or "").upper()
                close_hint = str(
                    order.metadata.get("intraday_exit_code", "")
                    or order.metadata.get("spread_exit_code", "")
                    or ""
                ).upper()
            if not close_hint and self._is_option_close_order(order):
                close_hint = "CLOSE"
            # Create unique order key with routing context to avoid false duplicate suppression.
            order_key = (
                f"{order.symbol}:{order.side.value}:{order.quantity}:"
                f"{order.order_type.value}:{int(order.is_combo)}:"
                f"{order.combo_short_symbol or ''}:{source_tag or ''}:{trace_id or ''}:"
                f"{lane_hint}:{strategy_hint}:{close_hint}"
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

            # Option-exit plumbing guard: clear same-symbol open orders before close submit.
            preclear_ok, preclear_detail = self._run_option_exit_preclear(order)
            if not preclear_ok:
                self.log(f"ROUTER: {preclear_detail}")
                # Preclear pending/inflight-close is a transient defer state, not an
                # execution rejection. Preserve close intent and retry on next cycle.
                if preclear_detail.startswith(
                    ("EXIT_PRE_CLEAR_INFLIGHT_CLOSE", "EXIT_PRE_CLEAR_PENDING")
                ):
                    continue
                self._record_rejection(
                    code="R_EXIT_PRECLEAR_PENDING",
                    symbol=order.symbol,
                    detail=preclear_detail,
                    stage="EXECUTE",
                    source_tag=source_tag,
                    trace_id=trace_id,
                )
                continue
            if preclear_detail:
                self.log(f"ROUTER: {preclear_detail}")

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
                            fallback_tag = self._append_spread_exit_rca_tag(
                                (
                                    str(order.tag or "").strip()
                                    or "VASS:QUOTE_INVALID_COMBO_MARKET_RETRY"
                                ),
                                order.metadata,
                            )
                            emergency_seq_ok = self._try_vass_quote_invalid_close_fallback(
                                order=order,
                                quote_detail=quote_detail,
                                tag=fallback_tag,
                            )
                            if not emergency_seq_ok and self._is_emergency_spread_exit(
                                order.metadata, order.reason
                            ):
                                emergency_seq_ok = (
                                    self._execute_emergency_sequential_close_from_order(
                                        order, quote_detail
                                    )
                                )
                            if emergency_seq_ok:
                                self._executed_this_minute.add(order_key)
                                executed.append(order)
                                continue
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
                        combo_margin_buffer_pct = float(
                            getattr(config, "COMBO_MARGIN_SAFETY_BUFFER_PCT", 0.05)
                        )
                        combo_margin_buffer_pct = min(max(combo_margin_buffer_pct, 0.0), 0.25)
                        margin_remaining_safe = margin_remaining * (1.0 - combo_margin_buffer_pct)
                        required_margin = per_contract_margin * order.quantity
                        if per_contract_margin > 0 and required_margin > margin_remaining_safe:
                            # V9.4 P0: Exit combos bypass margin check — closing a spread
                            # releases margin, it should never be blocked by margin requirements.
                            # This fixes the deadlock where exit signals fire every minute but
                            # the close order is perpetually margin-blocked.
                            if is_exit_combo:
                                self.log(
                                    f"ROUTER_EXIT_BYPASS_MARGIN: {order.symbol} | "
                                    f"Required=${required_margin:,.0f} > Available=${margin_remaining:,.0f} | "
                                    f"SafeAvailable=${margin_remaining_safe:,.0f} | "
                                    f"Allowing exit combo (risk-reducing)"
                                )
                            else:
                                max_contracts = int(margin_remaining_safe / per_contract_margin)
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
                                        f"SafeAvailable=${margin_remaining_safe:,.0f} | "
                                        f"Scaled to {max_contracts} spreads"
                                    )
                                else:
                                    self.log(
                                        f"ROUTER_MARGIN_BLOCK_COMBO: {order.symbol} | "
                                        f"Required=${required_margin:,.0f} > Available=${margin_remaining:,.0f} | "
                                        f"SafeAvailable=${margin_remaining_safe:,.0f} | "
                                        f"Max {max_contracts} < min {min_contracts}"
                                    )
                                    self._record_rejection(
                                        code="R_COMBO_MARGIN_BLOCK",
                                        symbol=order.symbol,
                                        detail=(
                                            f"Required=${required_margin:,.0f} > Available=${margin_remaining:,.0f} | "
                                            f"SafeAvailable=${margin_remaining_safe:,.0f} | "
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
                    # margin after reserving options margin for options engine
                    # V6.11: Trend redesign replaced TNA/FAS with UGL/UCO.
                    # V12.15: Use percentage-based reserve (scales with portfolio)
                    trend_symbols = ["QLD", "SSO", "UGL", "UCO"]
                    symbol_str = str(order.symbol)
                    if symbol_str in trend_symbols:
                        options_margin_pct = float(getattr(config, "OPTIONS_MAX_MARGIN_PCT", 0.50))
                        portfolio_value = self.algorithm.Portfolio.TotalPortfolioValue
                        options_reserve = portfolio_value * options_margin_pct
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
                effective_tag = self._append_spread_exit_rca_tag(effective_tag, order.metadata)

                # V2.3.9: Handle combo orders for spreads
                if order.is_combo and order.combo_short_symbol and order.combo_short_quantity:
                    is_exit_combo = bool(
                        order.metadata and order.metadata.get("spread_close_short", False)
                    )
                    spread_md = dict(order.metadata or {})
                    is_vass_combo_exit = bool(
                        is_exit_combo and self._is_vass_combo_exit_order(order)
                    )
                    is_vass_soft_exit = False
                    if is_vass_combo_exit:
                        exit_urgency = self._resolve_vass_exit_urgency(spread_md, order.reason)
                        if exit_urgency:
                            spread_md["spread_exit_urgency"] = exit_urgency
                            if isinstance(order.metadata, dict):
                                order.metadata["spread_exit_urgency"] = exit_urgency
                            effective_tag = self._append_spread_exit_rca_tag(
                                effective_tag, order.metadata
                            )
                        is_vass_soft_exit = (
                            str(spread_md.get("spread_exit_urgency", "") or "").upper() == "SOFT"
                        )
                        phase = str(spread_md.get("spread_close_phase", "") or "").upper()
                        phase_is_escalated = phase in {"COMBO_MARKET", "SEQ_MARKET"}
                        if phase_is_escalated:
                            is_vass_soft_exit = False
                            spread_md["spread_close_force_combo_market"] = True
                            if isinstance(order.metadata, dict):
                                order.metadata["spread_close_force_combo_market"] = True
                    spread_type_upper = str(spread_md.get("spread_type", "") or "").upper()
                    is_credit_exit_combo = bool(
                        is_exit_combo
                        and (
                            spread_md.get("is_credit_spread", False)
                            or "CREDIT" in spread_type_upper
                        )
                    )
                    force_combo_market_exit = bool(
                        is_exit_combo
                        and isinstance(order.metadata, dict)
                        and order.metadata.get("spread_close_force_combo_market", False)
                    )
                    if force_combo_market_exit and is_vass_soft_exit:
                        force_combo_market_exit = False
                        if isinstance(order.metadata, dict):
                            order.metadata["spread_close_force_combo_market"] = False
                        self.log(
                            "ROUTER_VASS_SOFT_SUPPRESS_FORCED_MARKET: "
                            f"{order.symbol} | Reason={order.reason}"
                        )
                    quotes_ok, quote_detail = self._validate_combo_entry_quotes(order)
                    if not quotes_ok:
                        emergency_seq_ok = self._try_vass_quote_invalid_close_fallback(
                            order=order,
                            quote_detail=quote_detail,
                            tag=effective_tag,
                        )
                        if not emergency_seq_ok and self._is_emergency_spread_exit(
                            order.metadata, order.reason
                        ):
                            emergency_seq_ok = self._execute_emergency_sequential_close_from_order(
                                order, quote_detail
                            )
                        if emergency_seq_ok:
                            self._executed_this_minute.add(order_key)
                            executed.append(order)
                            continue
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
                    combo_margin_buffer_pct = float(
                        getattr(config, "COMBO_MARGIN_SAFETY_BUFFER_PCT", 0.05)
                    )
                    combo_margin_buffer_pct = min(max(combo_margin_buffer_pct, 0.0), 0.25)
                    margin_remaining_safe = margin_remaining * (1.0 - combo_margin_buffer_pct)
                    if combo_margin_per_contract > 0 and total_combo_margin > margin_remaining_safe:
                        # V9.4 P0: Exit combos bypass — closing reduces risk, should never be blocked
                        if is_exit_combo:
                            self.log(
                                f"ROUTER_EXIT_BYPASS_MARGIN: {order.symbol} | "
                                f"Required=${total_combo_margin:,.0f} > Available=${margin_remaining:,.0f} | "
                                f"SafeAvailable=${margin_remaining_safe:,.0f} | "
                                f"Allowing exit combo (risk-reducing)"
                            )
                        else:
                            self.log(
                                f"ROUTER_MARGIN_BLOCK_COMBO: {order.symbol} | "
                                f"Required=${total_combo_margin:,.0f} > Available=${margin_remaining:,.0f} | "
                                f"SafeAvailable=${margin_remaining_safe:,.0f}"
                            )
                            self._record_rejection(
                                code="R_COMBO_MARGIN_BLOCK",
                                symbol=order.symbol,
                                detail=(
                                    f"Required=${total_combo_margin:,.0f} > Available=${margin_remaining:,.0f} | "
                                    f"SafeAvailable=${margin_remaining_safe:,.0f}"
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
                    combo_tickets = None
                    submit_mode = "MARKET"
                    if force_combo_market_exit:
                        submit_mode = "FORCED_MARKET"
                    elif is_credit_exit_combo:
                        # Credit spread exits are often net-debit closes; protected combo-limit
                        # pricing tuned for debit exits causes chronic limit-cancel churn.
                        submit_mode = "CREDIT_MARKET"
                    elif is_exit_combo and bool(
                        getattr(config, "SPREAD_EXIT_PROTECTED_COMBO_ENABLED", True)
                    ):
                        limit_credit, limit_detail = self._compute_protected_exit_combo_limit(
                            order.symbol, order.combo_short_symbol
                        )
                        if limit_credit is not None:
                            submit_mode = "LIMIT"
                            try:
                                combo_tickets = self.algorithm.ComboLimitOrder(  # type: ignore[attr-defined]
                                    legs, num_spreads, float(limit_credit), tag=effective_tag
                                )
                            except TypeError:
                                try:
                                    combo_tickets = self.algorithm.ComboLimitOrder(  # type: ignore[attr-defined]
                                        legs,
                                        num_spreads,
                                        float(limit_credit),
                                        False,
                                        effective_tag,
                                    )
                                except TypeError:
                                    try:
                                        combo_tickets = self.algorithm.ComboLimitOrder(  # type: ignore[attr-defined]
                                            legs, num_spreads, float(limit_credit), effective_tag
                                        )
                                    except Exception:
                                        combo_tickets = None
                            if combo_tickets is None:
                                self.log(
                                    f"ROUTER: COMBO_LIMIT_FALLBACK_MARKET | {order.symbol} | "
                                    f"{limit_detail}"
                                )
                        else:
                            self.log(
                                f"ROUTER: COMBO_LIMIT_SKIPPED | {order.symbol} | {limit_detail}"
                            )

                    if combo_tickets is None:
                        if is_vass_soft_exit:
                            self.log(
                                "ROUTER_VASS_SOFT_EXIT_DEFER_LIMIT_ONLY: "
                                f"{order.symbol} | Short={order.combo_short_symbol} | "
                                f"Mode={submit_mode}"
                            )
                            self._record_rejection(
                                code="R_VASS_SOFT_EXIT_DEFER",
                                symbol=order.symbol,
                                detail=(
                                    "Soft VASS exit deferred (no combo market fallback) | "
                                    f"Mode={submit_mode}"
                                ),
                                stage="EXECUTE_DEFERRED",
                                source_tag=source_tag,
                                trace_id=trace_id,
                            )
                            continue
                        try:
                            combo_tickets = self.algorithm.ComboMarketOrder(  # type: ignore[attr-defined]
                                legs, num_spreads, tag=effective_tag
                            )
                        except TypeError:
                            try:
                                combo_tickets = self.algorithm.ComboMarketOrder(  # type: ignore[attr-defined]
                                    legs, num_spreads, False, effective_tag
                                )
                            except TypeError:
                                combo_tickets = self.algorithm.ComboMarketOrder(  # type: ignore[attr-defined]
                                    legs, num_spreads
                                )
                        if submit_mode == "LIMIT":
                            submit_mode = "MARKET_FALLBACK"
                    if is_exit_combo and isinstance(order.metadata, dict):
                        close_path = str(order.metadata.get("spread_close_path", "") or "").strip()
                        if not close_path:
                            if submit_mode == "LIMIT":
                                close_path = "COMBO_LIMIT"
                            elif "MARKET" in submit_mode:
                                close_path = "COMBO_MARKET"
                        if close_path:
                            order.metadata["spread_close_path"] = close_path
                            effective_tag = self._append_spread_exit_rca_tag(
                                effective_tag, order.metadata
                            )
                    if force_combo_market_exit:
                        self.log(
                            f"ROUTER: VASS_FORCE_COMBO_MARKET_EXIT | {order.symbol} | "
                            f"Reason={order.reason}"
                        )
                    ticket_count = 0
                    if combo_tickets is None:
                        ticket_count = 0
                    elif isinstance(combo_tickets, list):
                        ticket_count = len([t for t in combo_tickets if t is not None])
                    else:
                        ticket_count = 1
                    if ticket_count <= 0:
                        if is_exit_combo:
                            emergency_seq_ok = self._execute_emergency_sequential_close_from_order(
                                order,
                                "COMBO_SUBMIT_EMPTY",
                            )
                            if emergency_seq_ok:
                                self._executed_this_minute.add(order_key)
                                executed.append(order)
                                continue
                        self.log(
                            f"ROUTER: R_COMBO_SUBMIT_EMPTY | {order.symbol} | "
                            f"Mode={submit_mode}"
                        )
                        self._record_rejection(
                            code="R_COMBO_SUBMIT_EMPTY",
                            symbol=order.symbol,
                            detail=f"Combo submit returned no tickets | Mode={submit_mode}",
                            stage="EXECUTE",
                            source_tag=source_tag,
                            trace_id=trace_id,
                        )
                        continue
                    self._cache_submitted_order_tags(combo_tickets, effective_tag)
                    self.log(
                        f"ROUTER: COMBO_ORDER_SUBMIT | Mode={submit_mode} | "
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
                        ticket = None
                        try:
                            ticket = self.algorithm.MarketOrder(  # type: ignore[attr-defined]
                                order.symbol, quantity, tag=effective_tag
                            )
                        except TypeError:
                            try:
                                ticket = self.algorithm.MarketOrder(  # type: ignore[attr-defined]
                                    order.symbol, quantity, effective_tag
                                )
                            except TypeError:
                                ticket = self.algorithm.MarketOrder(  # type: ignore[attr-defined]
                                    order.symbol, quantity
                                )
                        self._cache_submitted_order_tags(ticket, effective_tag)
                        self.log(
                            f"ROUTER: MARKET_ORDER | {order.side.value} {order.quantity} {order.symbol}"
                            + f" | Tag={effective_tag}"
                        )
                elif order.order_type == OrderType.MOC:
                    # V2.4.2: Market-On-Close for same-day trend entries
                    ticket = None
                    try:
                        ticket = self.algorithm.MarketOnCloseOrder(  # type: ignore[attr-defined]
                            order.symbol, quantity, tag=effective_tag
                        )
                    except TypeError:
                        try:
                            ticket = self.algorithm.MarketOnCloseOrder(  # type: ignore[attr-defined]
                                order.symbol, quantity, effective_tag
                            )
                        except TypeError:
                            ticket = self.algorithm.MarketOnCloseOrder(  # type: ignore[attr-defined]
                                order.symbol, quantity
                            )
                    self._cache_submitted_order_tags(ticket, effective_tag)
                    self.log(
                        f"ROUTER: MOC_ORDER | {order.side.value} {order.quantity} {order.symbol}"
                        + f" | Tag={effective_tag}"
                    )
                else:  # MOO
                    ticket = None
                    try:
                        ticket = self.algorithm.MarketOnOpenOrder(  # type: ignore[attr-defined]
                            order.symbol, quantity, tag=effective_tag
                        )
                    except TypeError:
                        try:
                            ticket = self.algorithm.MarketOnOpenOrder(  # type: ignore[attr-defined]
                                order.symbol, quantity, effective_tag
                            )
                        except TypeError:
                            ticket = self.algorithm.MarketOnOpenOrder(  # type: ignore[attr-defined]
                                order.symbol, quantity
                            )
                    self._cache_submitted_order_tags(ticket, effective_tag)
                    self.log(
                        f"ROUTER: MOO_ORDER | {order.side.value} {order.quantity} {order.symbol}"
                        + f" | Tag={effective_tag}"
                    )

                # Mark as executed to prevent duplicates
                self._executed_this_minute.add(order_key)
                executed.append(order)

            except Exception as e:
                if (
                    order.is_combo
                    and order.metadata
                    and order.metadata.get("spread_close_short", False)
                ):
                    emergency_seq_ok = self._execute_emergency_sequential_close_from_order(
                        order,
                        f"COMBO_SUBMIT_EXCEPTION:{e}",
                    )
                    if emergency_seq_ok:
                        self._executed_this_minute.add(order_key)
                        executed.append(order)
                        continue
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
            "open_spread_margin": dict(self._open_spread_margin),
        }

    def restore_state(self, state: Dict[str, Any]) -> None:
        """Restore router state from ObjectStore payload."""
        if not isinstance(state, dict):
            return

        self._pending_weights.clear()
        self._last_orders.clear()
        self._risk_engine_go = bool(state.get("risk_status", True))
        self._last_execution_minute = None
        self._executed_this_minute.clear()
        self._last_eod_date = None
        self._exit_preclear_pending_since.clear()
        self._preclear_diag_counts.clear()
        self._stale_close_reject_cooldown_until.clear()

        self._open_spread_margin = {}
        raw_margins = state.get("open_spread_margin", {}) or {}
        if isinstance(raw_margins, dict):
            for key, value in raw_margins.items():
                key_text = str(key or "").strip()
                if not key_text:
                    continue
                try:
                    margin_reserved = float(value)
                except (TypeError, ValueError):
                    continue
                if margin_reserved > 0:
                    self._open_spread_margin[key_text] = margin_reserved

    def reset(self) -> None:
        """Reset router state."""
        self._pending_weights.clear()
        self._last_orders.clear()
        self._risk_engine_go = True
        self._last_execution_minute = None
        self._executed_this_minute.clear()
        self._last_eod_date = None
        self._exit_preclear_pending_since.clear()
        self._preclear_diag_counts.clear()
        self._stale_close_reject_cooldown_until.clear()
        # V2.3.24: Reset rejection log throttle
        self._last_rejection_log_time = None
        self._rejection_log_count = 0
        self._last_rejection_event_log_by_key.clear()
        self._suppressed_rejection_event_count_by_key.clear()
        self._open_spread_margin.clear()
        self.log("ROUTER: RESET")

    def get_preclear_diag_counts(self) -> Dict[str, int]:
        """Return preclear telemetry counters for diagnostic summaries."""
        return {str(k): int(v) for k, v in (self._preclear_diag_counts or {}).items() if int(v) > 0}

    def clear_preclear_diag_counts(self) -> None:
        """Clear preclear telemetry counters."""
        self._preclear_diag_counts.clear()
