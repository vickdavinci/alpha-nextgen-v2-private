"""Options primitive models and micro-regime helpers extracted from options_engine."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Deque, Dict, List, Optional, Tuple

import config
from engines.satellite.iv_sensor import VIXSnapshot
from models.enums import (
    IntradayStrategy,
    MicroRegime,
    OptionDirection,
    QQQMove,
    VIXDirection,
    VIXLevel,
    WhipsawState,
)


class SpreadStrategy(Enum):
    """
    V2.8: Spread strategy types for VASS (Volatility-Adaptive Strategy Selection).

    Debit spreads: Pay premium, max loss = debit paid
    Credit spreads: Collect premium, max loss = width - credit
    """

    BULL_CALL_DEBIT = "BULL_CALL_DEBIT"  # Long call + short higher call (bullish)
    BEAR_PUT_DEBIT = "BEAR_PUT_DEBIT"  # Long put + short lower put (bearish)
    BULL_PUT_CREDIT = "BULL_PUT_CREDIT"  # Short put + long lower put (bullish)
    BEAR_CALL_CREDIT = "BEAR_CALL_CREDIT"  # Short call + long higher call (bearish)


@dataclass
class EntryScore:
    """
    Entry score breakdown for options trade.

    Total score is sum of 4 factors, each ranging 0-1.
    Range: 0-4, Minimum for entry: 3.0
    """

    score_adx: float = 0.0
    score_momentum: float = 0.0
    score_iv: float = 0.0
    score_liquidity: float = 0.0

    @property
    def total(self) -> float:
        """Total entry score (0-4)."""
        return self.score_adx + self.score_momentum + self.score_iv + self.score_liquidity

    @property
    def is_valid(self) -> bool:
        """Check if score meets minimum threshold."""
        return self.total >= config.OPTIONS_ENTRY_SCORE_MIN

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for logging."""
        return {
            "score_adx": round(self.score_adx, 2),
            "score_momentum": round(self.score_momentum, 2),
            "score_iv": round(self.score_iv, 2),
            "score_liquidity": round(self.score_liquidity, 2),
            "total": round(self.total, 2),
            "is_valid": self.is_valid,
        }


@dataclass
class OptionContract:
    """
    Selected option contract details.

    Represents a specific QQQ option contract for trading.
    """

    symbol: str  # Full option symbol (e.g., "QQQ 260126C00450000")
    underlying: str = "QQQ"
    direction: OptionDirection = OptionDirection.CALL
    strike: float = 0.0
    expiry: str = ""  # Date string "YYYY-MM-DD"
    delta: float = 0.0
    gamma: float = 0.0  # V2.1: Greeks monitoring
    vega: float = 0.0  # V2.1: Greeks monitoring
    theta: float = 0.0  # V2.1: Greeks monitoring (daily decay)
    bid: float = 0.0
    ask: float = 0.0
    mid_price: float = 0.0
    open_interest: int = 0
    days_to_expiry: int = 0

    @property
    def spread_pct(self) -> float:
        """Bid-ask spread as percentage of mid price."""
        if self.mid_price <= 0:
            return 1.0  # 100% if no valid mid
        return (self.ask - self.bid) / self.mid_price

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for persistence."""
        return {
            "symbol": self.symbol,
            "underlying": self.underlying,
            "direction": self.direction.value,
            "strike": self.strike,
            "expiry": self.expiry,
            "delta": self.delta,
            "gamma": self.gamma,
            "vega": self.vega,
            "theta": self.theta,
            "bid": self.bid,
            "ask": self.ask,
            "mid_price": self.mid_price,
            "open_interest": self.open_interest,
            "days_to_expiry": self.days_to_expiry,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OptionContract":
        """Deserialize from persistence."""
        return cls(
            symbol=data["symbol"],
            underlying=data.get("underlying", "QQQ"),
            direction=OptionDirection(data["direction"]),
            strike=data["strike"],
            expiry=data["expiry"],
            delta=data["delta"],
            gamma=data.get("gamma", 0.0),  # V2.1: Default for backwards compat
            vega=data.get("vega", 0.0),  # V2.1: Default for backwards compat
            theta=data.get("theta", 0.0),  # V2.1: Default for backwards compat
            bid=data["bid"],
            ask=data["ask"],
            mid_price=data["mid_price"],
            open_interest=data["open_interest"],
            days_to_expiry=data["days_to_expiry"],
        )


@dataclass
class OptionsPosition:
    """
    Tracks an active options position.

    Includes entry details, targets, and stop levels.
    """

    contract: OptionContract
    entry_price: float  # Fill price
    entry_time: str
    entry_score: float
    num_contracts: int
    stop_price: float  # Based on tiered stop
    target_price: float  # +50% target
    stop_pct: float  # Stop percentage used
    entry_strategy: str = "UNKNOWN"  # Strategy at entry for strategy-aware exits
    highest_price: float = 0.0  # High watermark for trailing stop

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for persistence."""
        return {
            "contract": self.contract.to_dict(),
            "entry_price": self.entry_price,
            "entry_time": self.entry_time,
            "entry_score": self.entry_score,
            "num_contracts": self.num_contracts,
            "stop_price": self.stop_price,
            "target_price": self.target_price,
            "stop_pct": self.stop_pct,
            "entry_strategy": self.entry_strategy,
            "highest_price": self.highest_price,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OptionsPosition":
        """Deserialize from persistence."""
        return cls(
            contract=OptionContract.from_dict(data["contract"]),
            entry_price=data["entry_price"],
            entry_time=data["entry_time"],
            entry_score=data["entry_score"],
            num_contracts=data["num_contracts"],
            stop_price=data["stop_price"],
            target_price=data["target_price"],
            stop_pct=data["stop_pct"],
            entry_strategy=data.get("entry_strategy", "UNKNOWN"),
            highest_price=data.get("highest_price", 0.0),
        )


# =============================================================================
# V2.3 DEBIT SPREAD POSITION
# =============================================================================


@dataclass
class SpreadPosition:
    """
    V2.3: Tracks a debit spread position (two-leg).

    Debit spreads have defined risk (max loss = net debit).
    No stop loss needed - position survives whipsaw.
    """

    long_leg: OptionContract  # Bought leg (ATM)
    short_leg: OptionContract  # Sold leg (OTM)
    spread_type: str  # "BULL_CALL" or "BEAR_PUT"
    net_debit: float  # Cost to open spread
    max_profit: float  # Debit: width - net_debit | Credit: abs(net_debit)
    width: float  # Strike difference ($3-5)
    entry_time: str
    entry_score: float
    num_spreads: int  # Number of spread contracts
    regime_at_entry: float  # Regime score at entry
    entry_vix: Optional[float] = None  # V12.2: freeze exit tier using entry-time VIX
    entry_underlying_price: Optional[
        float
    ] = None  # V12.25: QQQ anchor for thesis invalidation exits
    entry_policy_mode: Optional[
        str
    ] = None  # V12.27: freeze exit-policy mode at entry to avoid mid-run config drift
    is_closing: bool = False  # V2.12 Fix #2: Prevent duplicate exit signals
    highest_pnl_pct: float = 0.0  # V9.4: Track high-water mark for trailing stop
    highest_pnl_max_profit_pct: float = 0.0  # V10.15: MFE as % of max profit
    mfe_lock_tier: int = 0  # V10.15: 0=none, 1=breakeven+fees, 2=harvest floor
    thesis_soft_stop_streak: int = 0  # V12.27: consecutive thesis soft-stop breach bars
    assignment_incident_active: bool = False  # V12.28: idempotent partial-assignment lifecycle
    assignment_incident_id: Optional[str] = None  # V12.28: stable incident key for recovery
    signal_id: str = ""
    trace_id: str = ""
    signal_direction: str = ""
    signal_strategy: str = ""
    signal_reason: str = ""

    @property
    def profit_target(self) -> float:
        """Base configured profit target as spread value (telemetry helper)."""
        base_profit_pct = float(getattr(config, "SPREAD_PROFIT_TARGET_PCT", 0.50))
        return self.net_debit + (self.max_profit * base_profit_pct)

    @property
    def breakeven(self) -> float:
        """Breakeven price (long strike +/- net debit)."""
        if self.spread_type == "BULL_CALL":
            return self.long_leg.strike + self.net_debit
        else:  # BEAR_PUT
            return self.long_leg.strike - self.net_debit

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for persistence."""
        return {
            "long_leg": self.long_leg.to_dict(),
            "short_leg": self.short_leg.to_dict(),
            "spread_type": self.spread_type,
            "net_debit": self.net_debit,
            "max_profit": self.max_profit,
            "width": self.width,
            "entry_time": self.entry_time,
            "entry_score": self.entry_score,
            "num_spreads": self.num_spreads,
            "regime_at_entry": self.regime_at_entry,
            "entry_vix": self.entry_vix,
            "entry_underlying_price": self.entry_underlying_price,
            "entry_policy_mode": self.entry_policy_mode,
            "is_closing": self.is_closing,
            "highest_pnl_pct": self.highest_pnl_pct,
            "highest_pnl_max_profit_pct": self.highest_pnl_max_profit_pct,
            "mfe_lock_tier": self.mfe_lock_tier,
            "thesis_soft_stop_streak": self.thesis_soft_stop_streak,
            "assignment_incident_active": self.assignment_incident_active,
            "assignment_incident_id": self.assignment_incident_id,
            "signal_id": self.signal_id,
            "trace_id": self.trace_id,
            "signal_direction": self.signal_direction,
            "signal_strategy": self.signal_strategy,
            "signal_reason": self.signal_reason,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SpreadPosition":
        """Deserialize from persistence."""
        return cls(
            long_leg=OptionContract.from_dict(data["long_leg"]),
            short_leg=OptionContract.from_dict(data["short_leg"]),
            spread_type=data["spread_type"],
            net_debit=data["net_debit"],
            max_profit=data["max_profit"],
            width=data["width"],
            entry_time=data["entry_time"],
            entry_score=data["entry_score"],
            num_spreads=data["num_spreads"],
            regime_at_entry=data["regime_at_entry"],
            entry_vix=data.get("entry_vix"),
            entry_underlying_price=data.get("entry_underlying_price"),
            entry_policy_mode=data.get("entry_policy_mode"),
            is_closing=data.get("is_closing", False),  # V2.12: Default False for backwards compat
            highest_pnl_pct=data.get("highest_pnl_pct", 0.0),  # V9.4: Trailing stop HWM
            highest_pnl_max_profit_pct=data.get("highest_pnl_max_profit_pct", 0.0),
            mfe_lock_tier=int(data.get("mfe_lock_tier", 0) or 0),
            thesis_soft_stop_streak=int(data.get("thesis_soft_stop_streak", 0) or 0),
            assignment_incident_active=bool(data.get("assignment_incident_active", False)),
            assignment_incident_id=data.get("assignment_incident_id"),
            signal_id=str(data.get("signal_id", "") or ""),
            trace_id=str(data.get("trace_id", "") or ""),
            signal_direction=str(data.get("signal_direction", "") or ""),
            signal_strategy=str(data.get("signal_strategy", "") or ""),
            signal_reason=str(data.get("signal_reason", "") or ""),
        )


# =============================================================================
# V2.6 SPREAD FILL TRACKER (Bug Fix Foundation)
# =============================================================================


@dataclass
class SpreadFillTracker:
    """
    V2.6: Atomic tracking of spread leg fills with timeout and quantity validation.

    Fixes multiple bugs:
    - Bug #1: Race condition in fill tracking (pending legs cleared too early)
    - Bug #6: No quantity tracking on fills
    - Bug #7: Stale fill prices no timeout

    Usage:
        tracker = SpreadFillTracker(
            long_leg_symbol="QQQ 240118C00500000",
            short_leg_symbol="QQQ 240118C00505000",
            expected_quantity=5,
            created_at="2024-01-15 10:30:00",
        )
        tracker.record_long_fill(2.50, 5, "2024-01-15 10:30:01")
        tracker.record_short_fill(1.20, 5, "2024-01-15 10:30:02")
        if tracker.is_complete() and tracker.quantities_match():
            # Safe to register spread position
    """

    # Symbols stored at creation (before they can be cleared elsewhere)
    long_leg_symbol: str
    short_leg_symbol: str
    expected_quantity: int
    timeout_minutes: int = 5

    # Fill tracking - None means not yet filled
    long_fill_price: Optional[float] = None
    long_fill_qty: Optional[int] = None
    long_fill_time: Optional[str] = None
    short_fill_price: Optional[float] = None
    short_fill_qty: Optional[int] = None
    short_fill_time: Optional[str] = None

    # Tracker metadata
    created_at: Optional[str] = None
    spread_type: Optional[str] = None  # "BULL_CALL" or "BEAR_PUT"
    signal_id: str = ""
    trace_id: str = ""
    direction: str = ""
    strategy: str = ""
    signal_reason: str = ""

    def record_long_fill(self, price: float, qty: int, time: str) -> None:
        """Record long leg fill (accumulates for partial fills)."""
        if self.long_fill_qty is None:
            self.long_fill_price = price
            self.long_fill_qty = qty
        else:
            # VWAP for multiple partials
            total_qty = self.long_fill_qty + qty
            old_value = self.long_fill_price * self.long_fill_qty
            new_value = price * qty
            self.long_fill_price = (old_value + new_value) / total_qty
            self.long_fill_qty = total_qty
        self.long_fill_time = time

    def record_short_fill(self, price: float, qty: int, time: str) -> None:
        """Record short leg fill (accumulates for partial fills)."""
        if self.short_fill_qty is None:
            self.short_fill_price = price
            self.short_fill_qty = qty
        else:
            # VWAP for multiple partials
            total_qty = self.short_fill_qty + qty
            old_value = self.short_fill_price * self.short_fill_qty
            new_value = price * qty
            self.short_fill_price = (old_value + new_value) / total_qty
            self.short_fill_qty = total_qty
        self.short_fill_time = time

    def is_long_filled(self) -> bool:
        """Check if long leg has any fills."""
        return self.long_fill_price is not None and self.long_fill_qty is not None

    def is_short_filled(self) -> bool:
        """Check if short leg has any fills."""
        return self.short_fill_price is not None and self.short_fill_qty is not None

    def is_complete(self) -> bool:
        """Check if both legs have fills with expected quantity."""
        if not self.is_long_filled() or not self.is_short_filled():
            return False
        return (
            self.long_fill_qty >= self.expected_quantity
            and self.short_fill_qty >= self.expected_quantity
        )

    def quantities_match(self) -> bool:
        """Check if both leg quantities match expected."""
        if not self.is_complete():
            return False
        return self.long_fill_qty == self.short_fill_qty == self.expected_quantity

    def is_expired(self, current_time_str: str) -> bool:
        """
        Check if tracker has timed out.

        Args:
            current_time_str: Current time as string (format: "YYYY-MM-DD HH:MM:SS")

        Returns:
            True if (current_time - created_at) > timeout_minutes
        """
        if not self.created_at:
            return True

        try:
            # Parse timestamps
            from datetime import datetime

            created = datetime.strptime(self.created_at[:19], "%Y-%m-%d %H:%M:%S")
            current = datetime.strptime(current_time_str[:19], "%Y-%m-%d %H:%M:%S")

            elapsed_minutes = (current - created).total_seconds() / 60
            return elapsed_minutes > self.timeout_minutes
        except (ValueError, TypeError):
            # If parsing fails, consider expired (safe default)
            return True

    def reset(self) -> None:
        """Clear all fill data (for retry or cleanup)."""
        self.long_fill_price = None
        self.long_fill_qty = None
        self.long_fill_time = None
        self.short_fill_price = None
        self.short_fill_qty = None
        self.short_fill_time = None

    def get_net_debit(self) -> Optional[float]:
        """Calculate net debit from fill prices (long - short)."""
        if not self.is_complete():
            return None
        return self.long_fill_price - self.short_fill_price

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for logging/debugging."""
        return {
            "long_leg_symbol": self.long_leg_symbol,
            "short_leg_symbol": self.short_leg_symbol,
            "expected_quantity": self.expected_quantity,
            "spread_type": self.spread_type or "",
            "signal_id": self.signal_id,
            "trace_id": self.trace_id,
            "direction": self.direction,
            "strategy": self.strategy,
            "signal_reason": self.signal_reason,
            "long_fill": f"${self.long_fill_price:.2f} x{self.long_fill_qty}"
            if self.is_long_filled()
            else "PENDING",
            "short_fill": f"${self.short_fill_price:.2f} x{self.short_fill_qty}"
            if self.is_short_filled()
            else "PENDING",
            "complete": self.is_complete(),
            "quantities_match": self.quantities_match() if self.is_complete() else False,
            "created_at": self.created_at,
        }


@dataclass
class ExitOrderTracker:
    """
    V2.6: Track exit order state for retry logic (Bug #14).

    When 0DTE gamma causes rapid price moves, exit orders may be rejected.
    This tracker enables retry with updated prices.
    """

    symbol: str
    order_id: Optional[int] = None
    retry_count: int = 0
    last_attempt_time: Optional[str] = None
    reason: str = ""
    spread_id: Optional[str] = None  # Link to spread position
    engine_bucket: str = ""  # V12.19: Preserve engine attribution for retry tagging

    def should_retry(self, max_retries: int = 3) -> bool:
        """Check if another retry is allowed."""
        return self.retry_count < max_retries

    def record_attempt(self, time_str: str) -> None:
        """Record a retry attempt."""
        self.retry_count += 1
        self.last_attempt_time = time_str


# =============================================================================
# V2.9: HOLIDAY-AWARE EXPIRATION DETECTION
# =============================================================================


def get_expiration_firewall_day(algorithm: Any) -> int:
    """
    V2.9: Get the day to apply expiration firewall (Friday or Thursday if holiday).

    During Friday holiday weeks (like Good Friday), options expire on Thursday.
    This function checks the exchange calendar to determine the correct day.

    Args:
        algorithm: QCAlgorithm instance for exchange calendar access.

    Returns:
        4 = Friday (normal weeks)
        3 = Thursday (Friday holiday weeks like Good Friday)
    """
    if not config.FRIDAY_HOLIDAY_CHECK_ENABLED:
        return 4  # Friday

    try:
        exchange = algorithm.Securities[config.SETTLEMENT_CHECK_SYMBOL].Exchange.Hours

        # Find this week's Friday
        current_date = algorithm.Time.date()
        days_until_friday = (4 - current_date.weekday()) % 7
        if days_until_friday == 0 and current_date.weekday() == 4:
            # Today is Friday
            friday = current_date
        else:
            friday = current_date + timedelta(days=days_until_friday)

        # Check if Friday is a market holiday
        friday_datetime = datetime.combine(friday, datetime.min.time())
        if not exchange.IsOpen(friday_datetime, extendedMarket=False):
            # Friday is closed - use Thursday
            return 3  # Thursday

        return 4  # Friday
    except Exception:
        # Default to Friday if check fails
        return 4


def is_expiration_firewall_day(algorithm: Any) -> bool:
    """
    V2.9: Check if today is the expiration firewall day.

    Returns True if today is Friday (normal weeks) or Thursday (Friday holiday weeks).

    Args:
        algorithm: QCAlgorithm instance.

    Returns:
        True if today is the expiration firewall day.
    """
    return algorithm.Time.weekday() == get_expiration_firewall_day(algorithm)


# =============================================================================
# V2.1.1 MICRO REGIME ENGINE (Intraday Decision Brain)
# =============================================================================


@dataclass
class MicroRegimeState:
    """Current state of the Micro Regime Engine."""

    vix_level: VIXLevel = VIXLevel.LOW
    vix_direction: VIXDirection = VIXDirection.STABLE
    micro_regime: MicroRegime = MicroRegime.NORMAL
    micro_score: float = 50.0
    whipsaw_state: WhipsawState = WhipsawState.TRENDING
    recommended_strategy: IntradayStrategy = IntradayStrategy.NO_TRADE
    qqq_move_pct: float = 0.0
    vix_current: float = 15.0
    vix_open: float = 15.0
    last_update: str = ""
    spike_cooldown_until: str = ""
    # V2.3.4: QQQ move direction and recommended option direction
    qqq_direction: "QQQMove" = None  # UP/DOWN/FLAT
    recommended_direction: "OptionDirection" = None  # PUT or CALL
    recommended_reason: str = ""  # Why the engine produced this recommendation

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for persistence."""
        return {
            "vix_level": self.vix_level.value,
            "vix_direction": self.vix_direction.value,
            "micro_regime": self.micro_regime.value,
            "micro_score": self.micro_score,
            "whipsaw_state": self.whipsaw_state.value,
            "recommended_strategy": self.recommended_strategy.value,
            "qqq_move_pct": self.qqq_move_pct,
            "vix_current": self.vix_current,
            "vix_open": self.vix_open,
            "last_update": self.last_update,
            "spike_cooldown_until": self.spike_cooldown_until,
            # V2.3.4: New fields
            "qqq_direction": self.qqq_direction.value if self.qqq_direction else None,
            "recommended_direction": self.recommended_direction.value
            if self.recommended_direction
            else None,
            "recommended_reason": self.recommended_reason,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MicroRegimeState":
        """Deserialize from persistence."""
        # V2.3.4: Handle new fields
        qqq_dir = data.get("qqq_direction")
        rec_dir = data.get("recommended_direction")

        state = cls(
            vix_level=VIXLevel(data.get("vix_level", "LOW")),
            vix_direction=VIXDirection(data.get("vix_direction", "STABLE")),
            micro_regime=MicroRegime(data.get("micro_regime", "NORMAL")),
            micro_score=data.get("micro_score", 50.0),
            whipsaw_state=WhipsawState(data.get("whipsaw_state", "TRENDING")),
            recommended_strategy=IntradayStrategy(
                _normalize_engine_strategy_value(data.get("recommended_strategy", "NO_TRADE"))
            ),
            qqq_move_pct=data.get("qqq_move_pct", 0.0),
            vix_current=data.get("vix_current", 15.0),
            vix_open=data.get("vix_open", 15.0),
            last_update=data.get("last_update", ""),
            spike_cooldown_until=data.get("spike_cooldown_until", ""),
            recommended_reason=data.get("recommended_reason", ""),
        )
        # Set new fields if present
        if qqq_dir:
            state.qqq_direction = QQQMove(qqq_dir)
        if rec_dir:
            from models.enums import OptionDirection

            state.recommended_direction = OptionDirection(rec_dir)
        return state


def _normalize_engine_strategy_value(strategy_value: Any) -> str:
    """Map legacy/new intraday strategy labels to canonical runtime values."""
    value = str(strategy_value or "NO_TRADE").strip().upper()
    aliases = {
        "DEBIT_MOMENTUM": IntradayStrategy.ITM_MOMENTUM.value,
        "DEBIT_FADE": IntradayStrategy.MICRO_DEBIT_FADE.value,
        "MICRO_DEBIT_FADE": IntradayStrategy.MICRO_DEBIT_FADE.value,
        "MICRO_OTM_MOMENTUM": IntradayStrategy.MICRO_OTM_MOMENTUM.value,
        "ITM_MOMENTUM": IntradayStrategy.ITM_MOMENTUM.value,
        "CREDIT_SPREAD": IntradayStrategy.CREDIT_SPREAD.value,
        "PROTECTIVE_PUTS": IntradayStrategy.PROTECTIVE_PUTS.value,
        "NO_TRADE": IntradayStrategy.NO_TRADE.value,
    }
    return aliases.get(value, IntradayStrategy.NO_TRADE.value)


class MicroRegimeEngine:
    """
    Micro Regime Engine - The "brain" for intraday options trading (0-2 DTE).

    Combines VIX Level × VIX Direction to determine one of 21 trading regimes.
    Each regime has specific strategy deployment rules.

    Key insight: VIX Direction is THE key differentiator.
    Same VIX level + different direction = OPPOSITE strategies!

    Tiered VIX Monitoring:
    - Layer 1 (5 min): Spike detection
    - Layer 2 (15 min): Direction assessment
    - Layer 3 (1 hour): Whipsaw detection
    - Layer 4 (30 min): Full regime recalculation
    """

    def __init__(self, log_func=None, regime_decision_cb=None):
        """Initialize Micro Regime Engine."""
        self._log_func = log_func
        self._regime_decision_cb = regime_decision_cb
        self._state = MicroRegimeState()
        # Rolling 1-hour VIX history (12 data points at 5-min intervals)
        self._vix_history: Deque[VIXSnapshot] = deque(maxlen=12)
        self._vix_15min_ago: float = 0.0
        self._vix_30min_ago: float = 0.0
        self._qqq_open: float = 0.0

    def log(self, message: str) -> None:
        """Log via provided function or skip."""
        if self._log_func:
            self._log_func(f"MICRO: {message}")

    def _record_regime_decision(
        self,
        engine: str,
        decision: str,
        strategy_attempted: str,
        gate_name: str,
        threshold_snapshot: Optional[Any] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Forward structured regime decision telemetry when callback is available."""
        cb = getattr(self, "_regime_decision_cb", None)
        if cb is None:
            return
        try:
            cb(
                engine=engine,
                decision=decision,
                strategy_attempted=strategy_attempted,
                gate_name=gate_name,
                threshold_snapshot=threshold_snapshot,
                context=context,
            )
        except Exception:
            return

    # =========================================================================
    # VIX DIRECTION CLASSIFICATION
    # =========================================================================

    def classify_vix_direction(
        self, vix_current: float, vix_open: float
    ) -> Tuple[VIXDirection, float]:
        """
        Classify VIX direction based on change from open.

        VIX direction tells us WHERE we're going, not just where we are.
        This is THE key differentiator for intraday strategies.

        Args:
            vix_current: Current VIX value.
            vix_open: VIX value at market open.

        Returns:
            Tuple of (VIXDirection enum, direction score for micro score).
        """
        if vix_open <= 0:
            return VIXDirection.STABLE, config.MICRO_SCORE_DIR_STABLE

        vix_change_pct = (vix_current - vix_open) / vix_open * 100

        # V6.9: Adaptive STABLE band based on VIX level (reduces Dir=None in low VIX)
        if vix_current <= config.VIX_STABLE_LOW_VIX_MAX:
            stable_low = -config.VIX_STABLE_BAND_LOW
            stable_high = config.VIX_STABLE_BAND_LOW
        elif vix_current >= config.VIX_STABLE_HIGH_VIX_MIN:
            stable_low = -config.VIX_STABLE_BAND_HIGH
            stable_high = config.VIX_STABLE_BAND_HIGH
        else:
            # Linear interpolation between low and high bands
            span = config.VIX_STABLE_HIGH_VIX_MIN - config.VIX_STABLE_LOW_VIX_MAX
            if span <= 0:
                band = config.VIX_STABLE_BAND_HIGH
            else:
                t = (vix_current - config.VIX_STABLE_LOW_VIX_MAX) / span
                band = config.VIX_STABLE_BAND_LOW + t * (
                    config.VIX_STABLE_BAND_HIGH - config.VIX_STABLE_BAND_LOW
                )
            stable_low = -band
            stable_high = band

        # Check for whipsaw first (if we have history)
        if len(self._vix_history) >= 6:
            whipsaw_state, reversals = self._detect_whipsaw()
            if whipsaw_state == WhipsawState.WHIPSAW:
                return VIXDirection.WHIPSAW, config.MICRO_SCORE_DIR_WHIPSAW

        # Classify by change percentage
        if vix_change_pct < config.VIX_DIRECTION_FALLING_FAST:
            return VIXDirection.FALLING_FAST, config.MICRO_SCORE_DIR_FALLING_FAST
        elif vix_change_pct < config.VIX_DIRECTION_FALLING:
            return VIXDirection.FALLING, config.MICRO_SCORE_DIR_FALLING
        elif stable_low <= vix_change_pct <= stable_high:
            return VIXDirection.STABLE, config.MICRO_SCORE_DIR_STABLE
        elif vix_change_pct <= config.VIX_DIRECTION_RISING:
            return VIXDirection.RISING, config.MICRO_SCORE_DIR_RISING
        elif vix_change_pct <= config.VIX_DIRECTION_RISING_FAST:
            return VIXDirection.RISING_FAST, config.MICRO_SCORE_DIR_RISING_FAST
        else:
            return VIXDirection.SPIKING, config.MICRO_SCORE_DIR_SPIKING

    def classify_vix_level(self, vix_value: float) -> Tuple[VIXLevel, float]:
        """
        Classify VIX level and return score component.

        Args:
            vix_value: Current VIX value.

        Returns:
            Tuple of (VIXLevel enum, level score for micro score).
        """
        # V2.3.11: Use config constants for VIX level boundaries
        # Lowered VERY_CALM from 15 → 11.5 to fire more SNIPER 0DTEs
        if vix_value < config.VIX_LEVEL_VERY_CALM_MAX:  # V2.3.11: < 11.5 (was 15)
            return VIXLevel.LOW, config.MICRO_SCORE_VIX_VERY_CALM
        elif vix_value < config.VIX_LEVEL_CALM_MAX:  # V2.3.11: < 15 (was 18)
            calm_score = int(
                getattr(config, "MICRO_VIX_CALM_SCORE_DEFAULT", config.MICRO_SCORE_VIX_CALM)
            )
            low_vix_max = float(getattr(config, "MICRO_SCORE_LOW_VIX_MAX", 18.0))
            if vix_value < low_vix_max:
                calm_score = int(getattr(config, "MICRO_VIX_CALM_SCORE_LOW_VIX", calm_score))
            return VIXLevel.LOW, calm_score
        elif vix_value < config.VIX_LEVEL_NORMAL_MAX:  # V2.3.11: < 18 (unchanged)
            return VIXLevel.LOW, config.MICRO_SCORE_VIX_NORMAL
        elif vix_value < config.VIX_LEVEL_ELEVATED_MAX:  # V2.23.1: < 22 (was hardcoded)
            return VIXLevel.MEDIUM, config.MICRO_SCORE_VIX_ELEVATED
        elif vix_value < config.VIX_LEVEL_MEDIUM_MAX:
            return VIXLevel.MEDIUM, config.MICRO_SCORE_VIX_HIGH
        else:
            return VIXLevel.HIGH, config.MICRO_SCORE_VIX_EXTREME

    # =========================================================================
    # V2.3.4: QQQ MOVE CLASSIFICATION
    # =========================================================================

    def classify_qqq_move(self, qqq_current: float, qqq_open: float) -> Tuple[QQQMove, float]:
        """
        V2.3.4: Classify QQQ move direction and magnitude.

        This is critical for determining option direction:
        - QQQ UP → Consider PUT (fade) or CALL (momentum)
        - QQQ DOWN → Consider CALL (fade) or PUT (momentum)
        - QQQ FLAT → No edge, skip trade

        Args:
            qqq_current: Current QQQ price.
            qqq_open: QQQ price at market open.

        Returns:
            Tuple of (QQQMove enum, move percentage).
        """
        if qqq_open <= 0:
            return QQQMove.FLAT, 0.0

        move_pct = (qqq_current - qqq_open) / qqq_open * 100

        # Classify move direction and magnitude
        # V2.3.15 SNIPER LOGIC: Raised threshold from 0.15% to 0.35%
        # 0.15% was market noise causing excessive signals (PART 17 finding)
        # Gate 1: QQQ_NOISE_THRESHOLD filters out noise before strategy logic
        if move_pct > 0.8:
            return QQQMove.UP_STRONG, move_pct
        elif move_pct > config.QQQ_NOISE_THRESHOLD:  # V2.3.15: 0.35% (was 0.15%)
            return QQQMove.UP, move_pct
        elif move_pct < -0.8:
            return QQQMove.DOWN_STRONG, move_pct
        elif move_pct < -config.QQQ_NOISE_THRESHOLD:  # V2.3.15: -0.35% (was -0.15%)
            return QQQMove.DOWN, move_pct
        else:
            return QQQMove.FLAT, move_pct

    # =========================================================================
    # WHIPSAW DETECTION
    # =========================================================================

    def _detect_whipsaw(self) -> Tuple[WhipsawState, int]:
        """
        Detect whipsaw using direction reversal count.

        Analyzes rolling 1-hour VIX history for direction reversals.
        5+ reversals indicates chaotic market where both MR and momentum fail.

        Returns:
            Tuple of (WhipsawState, reversal count).
        """
        if len(self._vix_history) < 6:
            return WhipsawState.TRENDING, 0

        reversals = 0
        prev_direction = None

        history_list = list(self._vix_history)
        for i in range(1, len(history_list)):
            change = history_list[i].value - history_list[i - 1].value

            # Ignore tiny moves (noise)
            if abs(change) < config.VIX_REVERSAL_THRESHOLD:
                continue

            current_direction = "UP" if change > 0 else "DOWN"

            if prev_direction and current_direction != prev_direction:
                reversals += 1

            prev_direction = current_direction

        # Classify based on reversal count
        if reversals <= config.VIX_REVERSAL_TRENDING:
            return WhipsawState.TRENDING, reversals
        elif reversals <= config.VIX_REVERSAL_CHOPPY:
            return WhipsawState.CHOPPY, reversals
        else:
            return WhipsawState.WHIPSAW, reversals

    # =========================================================================
    # MICRO REGIME CLASSIFICATION (21 REGIMES)
    # =========================================================================

    def classify_micro_regime(
        self, vix_level: VIXLevel, vix_direction: VIXDirection
    ) -> MicroRegime:
        """
        Classify micro regime using VIX Level × VIX Direction matrix.

        21 distinct regimes, each with specific strategy deployment rules.

        Args:
            vix_level: Current VIX level classification.
            vix_direction: Current VIX direction classification.

        Returns:
            MicroRegime enum value.
        """
        # VIX LOW (< 20) regimes
        if vix_level == VIXLevel.LOW:
            regime_map = {
                VIXDirection.FALLING_FAST: MicroRegime.PERFECT_MR,
                VIXDirection.FALLING: MicroRegime.GOOD_MR,
                VIXDirection.STABLE: MicroRegime.NORMAL,
                VIXDirection.RISING: MicroRegime.CAUTION_LOW,
                VIXDirection.RISING_FAST: MicroRegime.TRANSITION,
                VIXDirection.SPIKING: MicroRegime.RISK_OFF_LOW,
                VIXDirection.WHIPSAW: MicroRegime.CHOPPY_LOW,
            }
        # VIX MEDIUM (20-25) regimes
        elif vix_level == VIXLevel.MEDIUM:
            regime_map = {
                VIXDirection.FALLING_FAST: MicroRegime.RECOVERING,
                VIXDirection.FALLING: MicroRegime.IMPROVING,
                VIXDirection.STABLE: MicroRegime.CAUTIOUS,
                VIXDirection.RISING: MicroRegime.WORSENING,
                VIXDirection.RISING_FAST: MicroRegime.DETERIORATING,
                VIXDirection.SPIKING: MicroRegime.BREAKING,
                VIXDirection.WHIPSAW: MicroRegime.UNSTABLE,
            }
        # VIX HIGH (> 25) regimes
        else:
            regime_map = {
                VIXDirection.FALLING_FAST: MicroRegime.PANIC_EASING,
                VIXDirection.FALLING: MicroRegime.CALMING,
                VIXDirection.STABLE: MicroRegime.ELEVATED,
                VIXDirection.RISING: MicroRegime.WORSENING_HIGH,
                VIXDirection.RISING_FAST: MicroRegime.FULL_PANIC,
                VIXDirection.SPIKING: MicroRegime.CRASH,
                VIXDirection.WHIPSAW: MicroRegime.VOLATILE,
            }

        return regime_map.get(vix_direction, MicroRegime.NORMAL)

    # =========================================================================
    # MICRO SCORE CALCULATION
    # =========================================================================

    def calculate_micro_score(
        self,
        vix_current: float,
        vix_open: float,
        qqq_current: float,
        qqq_open: float,
        move_duration_minutes: int = 120,
    ) -> float:
        """
        Calculate Micro Regime Score (range: -15 to 100).

        Components:
        1. VIX Level (0-25 pts)
        2. VIX Direction (-10 to +20 pts)
        3. QQQ Move Magnitude (0-20 pts)
        4. Move Velocity (0-15 pts)

        Higher scores favor mean reversion, lower favor momentum.

        Args:
            vix_current: Current VIX value.
            vix_open: VIX at market open.
            qqq_current: Current QQQ price.
            qqq_open: QQQ price at market open.
            move_duration_minutes: How long the move took.

        Returns:
            Micro score (-15 to 100).
        """
        score = 0.0

        # Component 1: VIX Level
        _, level_score = self.classify_vix_level(vix_current)
        score += level_score

        # Component 2: VIX Direction
        _, direction_score = self.classify_vix_direction(vix_current, vix_open)
        score += direction_score

        # Component 3: QQQ Move Magnitude
        if qqq_open > 0:
            qqq_move_pct = abs((qqq_current - qqq_open) / qqq_open * 100)
            score += self._score_qqq_move(qqq_move_pct)

        # Component 4: Move Velocity
        score += self._score_move_velocity(move_duration_minutes)

        return score

    def _score_qqq_move(self, move_pct: float) -> float:
        """Score QQQ move magnitude (0-20 points)."""
        if move_pct < 0.3:
            return config.MICRO_SCORE_MOVE_TINY
        elif move_pct < 0.5:
            return config.MICRO_SCORE_MOVE_BUILDING
        elif move_pct < 0.8:
            return config.MICRO_SCORE_MOVE_APPROACHING
        elif move_pct <= 1.25:
            return config.MICRO_SCORE_MOVE_TRIGGER
        else:
            return config.MICRO_SCORE_MOVE_EXTENDED

    def _score_move_velocity(self, duration_minutes: int) -> float:
        """Score move velocity (0-15 points)."""
        if duration_minutes > 120:
            return config.MICRO_SCORE_VELOCITY_GRADUAL
        elif duration_minutes > 60:
            return config.MICRO_SCORE_VELOCITY_MODERATE
        elif duration_minutes > 30:
            return config.MICRO_SCORE_VELOCITY_FAST
        else:
            return config.MICRO_SCORE_VELOCITY_SPIKE

    def _resolve_micro_bullish_confirm_threshold(self, vix_current: float) -> float:
        """Resolve bullish confirmation score threshold by VIX tier."""
        if vix_current >= float(getattr(config, "MICRO_SCORE_BULLISH_HIGH_VIX_MIN", 25.0)):
            return float(getattr(config, "MICRO_SCORE_BULLISH_CONFIRM_HIGH_VIX", 45.0))
        if vix_current < float(getattr(config, "MICRO_SCORE_BULLISH_LOW_VIX_MAX", 18.0)):
            return float(
                getattr(
                    config,
                    "MICRO_SCORE_BULLISH_CONFIRM_LOW_VIX",
                    getattr(config, "MICRO_SCORE_BULLISH_CONFIRM", 42.0),
                )
            )
        return float(getattr(config, "MICRO_SCORE_BULLISH_CONFIRM", 42.0))

    def _resolve_micro_bearish_confirm_threshold(self, vix_current: float) -> float:
        """Resolve bearish confirmation score threshold by VIX tier."""
        if vix_current >= float(getattr(config, "MICRO_SCORE_BEARISH_HIGH_VIX_MIN", 25.0)):
            return float(getattr(config, "MICRO_SCORE_BEARISH_CONFIRM_HIGH_VIX", 42.0))
        if vix_current < float(getattr(config, "MICRO_SCORE_BEARISH_LOW_VIX_MAX", 18.0)):
            return float(
                getattr(
                    config,
                    "MICRO_SCORE_BEARISH_CONFIRM_LOW_VIX",
                    getattr(config, "MICRO_SCORE_BEARISH_CONFIRM", 50.0),
                )
            )
        return float(getattr(config, "MICRO_SCORE_BEARISH_CONFIRM", 50.0))

    # =========================================================================
    # V2.3.4: STRATEGY & DIRECTION RECOMMENDATION (Combined Decision)
    # =========================================================================

    def recommend_strategy_and_direction(
        self,
        micro_regime: MicroRegime,
        micro_score: float,
        vix_current: float,
        vix_direction: VIXDirection,
        qqq_move: QQQMove,
        qqq_move_pct: float,
        macro_regime_score: float = 50.0,
        qqq_atr_pct: Optional[float] = None,
    ) -> Tuple[IntradayStrategy, Optional[OptionDirection], str]:
        """
        V2.3.4: Recommend intraday strategy AND direction based on regime + QQQ move.
        V2.5: Added Grind-Up Override for CAUTIOUS regime.

        This is the core decision engine that combines:
        - VIX Level × VIX Direction (market fear state)
        - QQQ Move Direction (what to fade or ride)

        Key Logic (V6.5 Divergence/Confirmation):
        - DIVERGENCE (opposite moves) = FADE (mean reversion):
          • QQQ UP + VIX RISING = weak rally, fade → Buy PUT
          • QQQ DOWN + VIX FALLING = weak dip, fade → Buy CALL
        - CONFIRMATION (aligned moves) = MOMENTUM (ride trend):
          • QQQ UP + VIX FALLING = confirmed rally → Buy CALL
          • QQQ DOWN + VIX RISING = confirmed selloff → Buy PUT
        - NO TRADE: QQQ flat OR VIX whipsawing
        - V2.5 GRIND-UP: CAUTIOUS + QQQ strong rally + macro safe = Buy CALL (ride grind)

        Args:
            micro_regime: Current micro regime classification.
            micro_score: Current micro score.
            vix_current: Current VIX value.
            vix_direction: VIX direction (FALLING, STABLE, RISING, etc.)
            qqq_move: QQQ move classification (UP, DOWN, FLAT).
            qqq_move_pct: QQQ move percentage (signed).
            macro_regime_score: V2.5 - Macro regime score for Grind-Up Override (default 50).
            qqq_atr_pct: Optional QQQ ATR as percent of price for adaptive move gating.

        Returns:
            Tuple of (IntradayStrategy, OptionDirection or None, reason string).
        """
        # =====================================================================
        # RULE 1: No trade if QQQ is flat (no edge)
        # =====================================================================
        if qqq_move == QQQMove.FLAT:
            return IntradayStrategy.NO_TRADE, None, "QQQ flat - no edge"

        # Fast crash-day trigger: fire protective puts before regime labels fully catch up.
        if bool(getattr(config, "PROTECTIVE_PUTS_CRASH_TRIGGER_ENABLED", True)):
            crash_drop_pct = float(getattr(config, "PROTECTIVE_PUTS_QQQ_DROP_TRIGGER_PCT", -1.0))
            vix_min = float(getattr(config, "PROTECTIVE_PUTS_VIX_MIN_TRIGGER", 18.0))
            require_vix_rising = bool(getattr(config, "PROTECTIVE_PUTS_REQUIRE_VIX_RISING", True))
            vix_rising_now = vix_direction in (
                VIXDirection.RISING,
                VIXDirection.RISING_FAST,
                VIXDirection.SPIKING,
            )
            vix_ok = vix_current >= vix_min and (vix_rising_now or not require_vix_rising)
            if qqq_move_pct <= crash_drop_pct and vix_ok:
                return (
                    IntradayStrategy.PROTECTIVE_PUTS,
                    OptionDirection.PUT,
                    f"Crash trigger: QQQ {qqq_move_pct:+.2f}% | VIX {vix_current:.1f} {vix_direction.value}",
                )

        # =====================================================================
        # RULE 2: Danger regimes - No trade or protective only
        # =====================================================================
        # V6.4: Danger regimes - ALWAYS trade PROTECTIVE_PUTS
        # The regime itself is the signal, no micro_score check needed
        # These regimes indicate crisis conditions where protection is paramount
        crisis_regimes = {
            MicroRegime.FULL_PANIC,
            MicroRegime.CRASH,
            MicroRegime.VOLATILE,
        }
        if micro_regime in crisis_regimes:
            # V9.2 RCA: FULL_PANIC requires QQQ DOWN confirmation before protective puts.
            # In 2022, 46% of FULL_PANIC protective puts were bought on QQQ UP days
            # (counter-trend), amplifying losses. CRASH/VOLATILE remain ungated (true crisis).
            if micro_regime == MicroRegime.FULL_PANIC:
                qqq_is_down_here = qqq_move in (QQQMove.DOWN, QQQMove.DOWN_STRONG)
                if not qqq_is_down_here:
                    return (
                        IntradayStrategy.NO_TRADE,
                        None,
                        f"PANIC_QQQ_GATE: FULL_PANIC but QQQ {qqq_move_pct:+.2f}% not DOWN → skip",
                    )
            # V6.4: Crisis detected - trade protective puts immediately
            return (
                IntradayStrategy.PROTECTIVE_PUTS,
                OptionDirection.PUT,
                f"Crisis: {micro_regime.value}",
            )

        # Other danger regimes - still caution but not crisis
        caution_regimes = {
            MicroRegime.RISK_OFF_LOW,
            MicroRegime.BREAKING,
            MicroRegime.UNSTABLE,
        }
        if micro_regime in caution_regimes:
            # Only trade if score is very negative (severe deterioration)
            if micro_score < 0:
                return IntradayStrategy.PROTECTIVE_PUTS, OptionDirection.PUT, "Caution protection"
            return IntradayStrategy.NO_TRADE, None, f"Caution regime: {micro_regime.value}"

        # =====================================================================
        # RULE 3: Whipsaw/choppy - Skip (direction unclear)
        # =====================================================================
        if vix_direction == VIXDirection.WHIPSAW:
            return IntradayStrategy.NO_TRADE, None, "VIX whipsawing - direction unclear"

        # =====================================================================
        # RULE 4: Determine if this is a FADE or MOMENTUM setup
        # =====================================================================
        qqq_is_up = qqq_move in (QQQMove.UP, QQQMove.UP_STRONG)
        qqq_is_down = qqq_move in (QQQMove.DOWN, QQQMove.DOWN_STRONG)
        vix_is_falling = vix_direction in (VIXDirection.FALLING, VIXDirection.FALLING_FAST)
        vix_is_rising = vix_direction in (
            VIXDirection.RISING,
            VIXDirection.RISING_FAST,
            VIXDirection.SPIKING,
        )

        # Confirmation routing: MICRO only emits MICRO_OTM_MOMENTUM.
        # ITM entries are delegated to ITM_ENGINE sovereign path in main orchestration.
        def confirmation_strategy(
            direction: OptionDirection, reason: str
        ) -> Tuple[IntradayStrategy, Optional[OptionDirection], str]:
            use_otm = bool(getattr(config, "MICRO_OTM_MOMENTUM_ENABLED", False))
            regime_name = str(getattr(micro_regime, "value", micro_regime)).upper()
            if use_otm:
                max_vix = float(getattr(config, "MICRO_OTM_MOMENTUM_MAX_VIX", 22.0))
                min_move = float(getattr(config, "MICRO_OTM_MOMENTUM_MIN_MOVE", 0.40))
                if direction == OptionDirection.CALL:
                    if not bool(getattr(config, "MICRO_OTM_CALL_ENABLED", False)):
                        self._record_regime_decision(
                            engine="MICRO",
                            decision="BLOCK",
                            strategy_attempted="MICRO_OTM_MOMENTUM_CALL",
                            gate_name="MICRO_OTM_CALL_DISABLED",
                        )
                        return (
                            IntradayStrategy.NO_TRADE,
                            None,
                            "MICRO_OTM_CALL_DISABLED: directional CALL OTM path disabled",
                        )
                    min_move = float(getattr(config, "MICRO_OTM_MOMENTUM_MIN_MOVE_CALL", min_move))
                    score_floor = self._resolve_micro_bullish_confirm_threshold(
                        vix_current
                    ) + float(getattr(config, "MICRO_OTM_BULLISH_CONFIRM_SCORE_BUFFER", 0.0))
                    if micro_score < score_floor:
                        self._record_regime_decision(
                            engine="MICRO",
                            decision="BLOCK",
                            strategy_attempted="MICRO_OTM_MOMENTUM_CALL",
                            gate_name="MICRO_OTM_BULLISH_SCORE_FLOOR",
                            threshold_snapshot={"score_floor": score_floor},
                        )
                        return (
                            IntradayStrategy.NO_TRADE,
                            None,
                            f"MICRO_OTM_GATE_BLOCK: weak bullish score {micro_score:.0f} < {score_floor:.0f}",
                        )
                    macro_score_floor = float(
                        getattr(config, "MICRO_OTM_CALL_MIN_MACRO_SCORE", 0.0)
                    )
                    if macro_regime_score < macro_score_floor:
                        self._record_regime_decision(
                            engine="MICRO",
                            decision="BLOCK",
                            strategy_attempted="MICRO_OTM_MOMENTUM_CALL",
                            gate_name="MICRO_OTM_CALL_MIN_MACRO_SCORE",
                            threshold_snapshot={"macro_score_floor": macro_score_floor},
                        )
                        return (
                            IntradayStrategy.NO_TRADE,
                            None,
                            f"MICRO_OTM_GATE_BLOCK: macro {macro_regime_score:.0f} < {macro_score_floor:.0f} for CALL",
                        )
                elif direction == OptionDirection.PUT:
                    if not bool(getattr(config, "MICRO_OTM_PUT_ENABLED", True)):
                        self._record_regime_decision(
                            engine="MICRO",
                            decision="BLOCK",
                            strategy_attempted="MICRO_OTM_MOMENTUM_PUT",
                            gate_name="MICRO_OTM_PUT_DISABLED",
                        )
                        return (
                            IntradayStrategy.NO_TRADE,
                            None,
                            "MICRO_OTM_PUT_DISABLED: directional PUT OTM path disabled",
                        )
                    put_whitelist_cfg = getattr(
                        config, "MICRO_OTM_PUT_REGIME_WHITELIST", ("WORSENING", "WORSENING_HIGH")
                    )
                    put_whitelist = {
                        str(item).upper()
                        for item in (
                            put_whitelist_cfg
                            if isinstance(put_whitelist_cfg, (list, tuple, set))
                            else []
                        )
                    }
                    put_regime_allowed = regime_name in put_whitelist
                    allow_deteriorating = bool(
                        getattr(config, "MICRO_OTM_PUT_ALLOW_DETERIORATING_IF_CONFIRMED", True)
                    )
                    if regime_name == "DETERIORATING" and allow_deteriorating:
                        downside_confirmed = qqq_is_down and vix_direction in (
                            VIXDirection.RISING_FAST,
                            VIXDirection.SPIKING,
                        )
                        if not downside_confirmed:
                            self._record_regime_decision(
                                engine="MICRO",
                                decision="BLOCK",
                                strategy_attempted="MICRO_OTM_MOMENTUM_PUT",
                                gate_name="MICRO_OTM_PUT_DETERIORATING_CONDITION_BLOCK",
                            )
                            return (
                                IntradayStrategy.NO_TRADE,
                                None,
                                "MICRO_OTM_PUT_DETERIORATING_CONDITION_BLOCK: requires QQQ down + VIX RISING_FAST/SPIKING",
                            )
                        put_regime_allowed = True
                    if not put_regime_allowed:
                        self._record_regime_decision(
                            engine="MICRO",
                            decision="BLOCK",
                            strategy_attempted="MICRO_OTM_MOMENTUM_PUT",
                            gate_name="MICRO_OTM_PUT_REGIME_BLOCK",
                            threshold_snapshot={"regime": regime_name},
                        )
                        return (
                            IntradayStrategy.NO_TRADE,
                            None,
                            f"MICRO_OTM_PUT_REGIME_BLOCK: {regime_name}",
                        )
                    min_move = float(getattr(config, "MICRO_OTM_MOMENTUM_MIN_MOVE_PUT", min_move))
                    score_ceiling = self._resolve_micro_bearish_confirm_threshold(
                        vix_current
                    ) - float(getattr(config, "MICRO_OTM_BEARISH_CONFIRM_SCORE_BUFFER", 0.0))
                    if micro_score > score_ceiling:
                        self._record_regime_decision(
                            engine="MICRO",
                            decision="BLOCK",
                            strategy_attempted="MICRO_OTM_MOMENTUM_PUT",
                            gate_name="MICRO_OTM_BEARISH_SCORE_CEILING",
                            threshold_snapshot={"score_ceiling": score_ceiling},
                        )
                        return (
                            IntradayStrategy.NO_TRADE,
                            None,
                            f"MICRO_OTM_GATE_BLOCK: weak bearish score {micro_score:.0f} > {score_ceiling:.0f}",
                        )
                    macro_score_ceiling = float(
                        getattr(config, "MICRO_OTM_PUT_MAX_MACRO_SCORE", 100.0)
                    )
                    if macro_regime_score > macro_score_ceiling:
                        self._record_regime_decision(
                            engine="MICRO",
                            decision="BLOCK",
                            strategy_attempted="MICRO_OTM_MOMENTUM_PUT",
                            gate_name="MICRO_OTM_PUT_MAX_MACRO_SCORE",
                            threshold_snapshot={"macro_score_ceiling": macro_score_ceiling},
                        )
                        return (
                            IntradayStrategy.NO_TRADE,
                            None,
                            f"MICRO_OTM_GATE_BLOCK: macro {macro_regime_score:.0f} > {macro_score_ceiling:.0f} for PUT",
                        )
                if vix_current <= max_vix and abs(qqq_move_pct) >= min_move:
                    gate_name = (
                        "MICRO_OTM_PUT_ALLOWED"
                        if direction == OptionDirection.PUT
                        else "MICRO_OTM_CONFIRMED"
                    )
                    self._record_regime_decision(
                        engine="MICRO",
                        decision="ALLOW",
                        strategy_attempted=f"MICRO_OTM_MOMENTUM_{direction.value}",
                        gate_name=gate_name,
                        threshold_snapshot={
                            "max_vix": max_vix,
                            "min_move": min_move,
                        },
                    )
                    return (
                        IntradayStrategy.MICRO_OTM_MOMENTUM,
                        direction,
                        f"OTM_MOMENTUM: {reason}",
                    )
            return (
                IntradayStrategy.NO_TRADE,
                None,
                f"MICRO_OTM_GATE_BLOCK: {reason}",
            )

        # V10.7 Phase-5: optional simplification to disable DEBIT_FADE.
        def divergence_strategy_or_skip(
            direction: OptionDirection, reason: str
        ) -> Tuple[IntradayStrategy, Optional[OptionDirection], str]:
            if bool(
                getattr(
                    config,
                    "MICRO_DEBIT_FADE_ENABLED",
                    getattr(config, "INTRADAY_DEBIT_FADE_ENABLED", False),
                )
            ):
                return (IntradayStrategy.MICRO_DEBIT_FADE, direction, reason)
            return (IntradayStrategy.NO_TRADE, None, f"MICRO_DEBIT_FADE_DISABLED: {reason}")

        # =====================================================================
        # RULE 5: DIVERGENCE/CONFIRMATION LOGIC (V6.5 Fix)
        # =====================================================================
        # DIVERGENCE = QQQ and VIX moving opposite → FADE (mean reversion)
        #   - QQQ UP + VIX RISING = weak rally, smart money hedging → BUY PUT
        #   - QQQ DOWN + VIX FALLING = weak dip, being bought → BUY CALL
        #
        # CONFIRMATION = QQQ and VIX aligned → MOMENTUM (ride the trend)
        #   - QQQ UP + VIX FALLING = confirmed rally, risk-on → BUY CALL
        #   - QQQ DOWN + VIX RISING = confirmed selloff, fear real → BUY PUT
        # =====================================================================

        # V12.16: MICRO directional policy hard blocks.
        directional_block_regimes = {
            MicroRegime.NORMAL,
            MicroRegime.CAUTION_LOW,
            MicroRegime.CAUTIOUS,
            MicroRegime.TRANSITION,
            MicroRegime.UNSTABLE,
            MicroRegime.ELEVATED,
        }
        if micro_regime in directional_block_regimes:
            return (
                IntradayStrategy.NO_TRADE,
                None,
                f"MICRO_DIRECTIONAL_BLOCK_LOW_CONVICTION: {micro_regime.value}",
            )

        # V12.16: ITM-owned bullish confirmation regimes.
        itm_handoff_only_regimes = {
            MicroRegime.GOOD_MR,
            MicroRegime.RECOVERING,
            MicroRegime.IMPROVING,
        }
        if micro_regime in itm_handoff_only_regimes:
            return (
                IntradayStrategy.NO_TRADE,
                None,
                f"MICRO_ITM_HANDOFF_ONLY: {micro_regime.value}",
            )

        # V6.5: Only apply divergence/confirmation logic in tradeable regimes
        # Choppy/caution regimes should skip this and fall through to RULE 7
        tradeable_regimes = {
            MicroRegime.PERFECT_MR,
            MicroRegime.PANIC_EASING,  # Can fade after panic subsides
            MicroRegime.CALMING,  # Can trade when fear is decreasing
            MicroRegime.WORSENING,
            MicroRegime.WORSENING_HIGH,
            MicroRegime.DETERIORATING,
        }

        if micro_regime not in tradeable_regimes:
            # Not a tradeable regime for divergence/confirmation - skip to other rules
            pass  # Fall through to RULE 6 handoff and RULE 7 (caution)
        else:
            # V2.19: VIX Floor Check - Block trades in "apathy" market
            vix_floor = float(
                getattr(
                    config,
                    "MICRO_DEBIT_FADE_VIX_MIN",
                    getattr(config, "INTRADAY_DEBIT_FADE_VIX_MIN", 13.5),
                )
            )
            if vix_current < vix_floor:
                return (
                    IntradayStrategy.NO_TRADE,
                    None,
                    f"VIX_FLOOR: VIX {vix_current:.1f} < {vix_floor} (apathy market)",
                )

            # V10: VIX-tier move gates (stricter in LOW VIX to filter theta noise)
            if vix_current < 18:
                min_move_gate = float(getattr(config, "MICRO_MIN_MOVE_LOW_VIX", 0.50))
                vix_tier_label = "LOW"
            elif vix_current < 25:
                min_move_gate = float(getattr(config, "MICRO_MIN_MOVE_MED_VIX", 0.40))
                vix_tier_label = "MED"
            else:
                min_move_gate = float(getattr(config, "MICRO_MIN_MOVE_HIGH_VIX", 0.40))
                vix_tier_label = "HIGH"
            if bool(getattr(config, "MICRO_ATR_MIN_MOVE_ENABLED", False)) and qqq_atr_pct:
                atr_mult = float(getattr(config, "MICRO_ATR_MIN_MOVE_MULTIPLIER", 0.50))
                atr_floor = float(getattr(config, "MICRO_ATR_MIN_MOVE_FLOOR_PCT", 0.12))
                atr_cap = float(getattr(config, "MICRO_ATR_MIN_MOVE_CAP_PCT", 0.60))
                atr_gate = max(atr_floor, min(atr_cap, float(qqq_atr_pct) * atr_mult))
                min_move_gate = min(min_move_gate, atr_gate)
            if abs(qqq_move_pct) < min_move_gate:
                return (
                    IntradayStrategy.NO_TRADE,
                    None,
                    f"MIN_MOVE_{vix_tier_label}: |{qqq_move_pct:.2f}%| < {min_move_gate:.2f}% "
                    f"(VIX={vix_current:.1f}, ATR%={float(qqq_atr_pct or 0.0):.2f})",
                )

            # =================================================================
            # SCENARIO 1: QQQ UP
            # =================================================================
            if qqq_is_up:
                if vix_is_rising:
                    # DIVERGENCE: QQQ up but VIX rising = weak rally, fade it
                    # V2.3.16: Don't fade if move too large (runaway trend)
                    if abs(qqq_move_pct) > config.INTRADAY_FADE_MAX_MOVE:
                        return (
                            IntradayStrategy.NO_TRADE,
                            None,
                            f"FADE blocked: |{qqq_move_pct:.2f}%| > {config.INTRADAY_FADE_MAX_MOVE}% (runaway)",
                        )
                    # HIGH-VIX divergence is delegated to ITM_ENGINE sovereign path.
                    if vix_current >= 25:
                        return (
                            IntradayStrategy.NO_TRADE,
                            None,
                            f"MICRO_ITM_HANDOFF: HIGH_VIX_DIVERGENCE PUT | "
                            f"QQQ +{qqq_move_pct:.2f}% | VIX={vix_current:.1f}",
                        )
                    return divergence_strategy_or_skip(
                        OptionDirection.PUT,
                        f"DIVERGENCE: QQQ +{qqq_move_pct:.2f}% but VIX {vix_direction.value} → Fade with PUT",
                    )

                elif vix_is_falling:
                    # CONFIRMATION: QQQ up + VIX falling = confirmed rally, ride it
                    return confirmation_strategy(
                        OptionDirection.CALL,
                        f"CONFIRMATION: QQQ +{qqq_move_pct:.2f}% + VIX {vix_direction.value} → Ride with CALL",
                    )

                elif vix_direction == VIXDirection.STABLE:
                    # V6.9: VIX stable fallback + 2-of-3 confirmation
                    # Require strong QQQ move AND bullish micro score to take CALL
                    if abs(
                        qqq_move_pct
                    ) >= config.INTRADAY_QQQ_FALLBACK_MIN_MOVE and micro_score >= self._resolve_micro_bullish_confirm_threshold(
                        vix_current
                    ):
                        # Confirmation paths are MICRO_OTM-only; ITM is sovereign handoff
                        return confirmation_strategy(
                            OptionDirection.CALL,
                            f"STABLE_FALLBACK: QQQ +{qqq_move_pct:.2f}% + "
                            f"Score={micro_score:.0f} → CALL",
                        )
                    return (
                        IntradayStrategy.NO_TRADE,
                        None,
                        f"STABLE_NO_TRADE: QQQ +{qqq_move_pct:.2f}% "
                        f"Score={micro_score:.0f} (<{self._resolve_micro_bullish_confirm_threshold(vix_current):.0f})",
                    )

            # =================================================================
            # SCENARIO 2: QQQ DOWN
            # =================================================================
            elif qqq_is_down:
                if vix_is_rising:
                    # CONFIRMATION: QQQ down + VIX rising = confirmed selloff, ride it
                    return confirmation_strategy(
                        OptionDirection.PUT,
                        f"CONFIRMATION: QQQ {qqq_move_pct:.2f}% + VIX {vix_direction.value} → Ride with PUT",
                    )

                elif vix_is_falling:
                    # DIVERGENCE: QQQ down but VIX falling = weak dip, fade it
                    # V2.3.16: Don't fade if move too large (crash)
                    if abs(qqq_move_pct) > config.INTRADAY_FADE_MAX_MOVE:
                        return (
                            IntradayStrategy.NO_TRADE,
                            None,
                            f"FADE blocked: |{qqq_move_pct:.2f}%| > {config.INTRADAY_FADE_MAX_MOVE}% (crash)",
                        )
                    # HIGH-VIX divergence is delegated to ITM_ENGINE sovereign path.
                    if vix_current >= 25:
                        return (
                            IntradayStrategy.NO_TRADE,
                            None,
                            f"MICRO_ITM_HANDOFF: HIGH_VIX_DIVERGENCE CALL | "
                            f"QQQ {qqq_move_pct:.2f}% | VIX={vix_current:.1f}",
                        )
                    return divergence_strategy_or_skip(
                        OptionDirection.CALL,
                        f"DIVERGENCE: QQQ {qqq_move_pct:.2f}% but VIX {vix_direction.value} → Fade with CALL",
                    )

                elif vix_direction == VIXDirection.STABLE:
                    # V6.9: VIX stable fallback + 2-of-3 confirmation
                    # Require strong QQQ move AND bearish micro score to take PUT
                    if abs(
                        qqq_move_pct
                    ) >= config.INTRADAY_QQQ_FALLBACK_MIN_MOVE and micro_score <= self._resolve_micro_bearish_confirm_threshold(
                        vix_current
                    ):
                        # Confirmation paths are MICRO_OTM-only; ITM is sovereign handoff
                        return confirmation_strategy(
                            OptionDirection.PUT,
                            f"STABLE_FALLBACK: QQQ {qqq_move_pct:.2f}% + "
                            f"Score={micro_score:.0f} → PUT",
                        )
                    return (
                        IntradayStrategy.NO_TRADE,
                        None,
                        f"STABLE_NO_TRADE: QQQ {qqq_move_pct:.2f}% "
                        f"Score={micro_score:.0f} (>{self._resolve_micro_bearish_confirm_threshold(vix_current):.0f})",
                    )

        # =====================================================================
        # RULE 6: ITM handoff
        # MICRO no longer emits ITM_MOMENTUM. ITM entries are delegated to
        # ITM_ENGINE sovereign path handled by main orchestration.
        # =====================================================================

        # =====================================================================
        # RULE 7: Caution regimes - tightly constrained participation only
        # V2.5: Added Grind-Up Override for strong rallies in safe macro conditions
        # =====================================================================
        caution_regimes = {
            MicroRegime.CHOPPY_LOW,
            # V9: CAUTION_LOW → tradeable_regimes (universal gates handle quality)
            # V9: TRANSITION → already in tradeable_regimes
            # V9: CAUTIOUS → already in tradeable_regimes
            # V6.5: WORSENING moved to momentum_regimes (ITM PUT)
        }
        if micro_regime in caution_regimes:
            # V10: Grind-Up override removed (CAUTIOUS is in tradeable_regimes, not caution_regimes)
            return IntradayStrategy.NO_TRADE, None, f"Caution regime: {micro_regime.value}"

        return IntradayStrategy.NO_TRADE, None, "No matching setup"

    def recommend_strategy(
        self,
        micro_regime: MicroRegime,
        micro_score: float,
        vix_current: float,
        qqq_move_pct: float,
    ) -> IntradayStrategy:
        """
        Legacy method for backwards compatibility.
        Use recommend_strategy_and_direction() for new code.
        """
        # Create QQQMove from percentage
        # V2.3.15 SNIPER LOGIC: Use config threshold (0.35%) not hardcoded 0.15%
        if qqq_move_pct > 0.8:
            qqq_move = QQQMove.UP_STRONG
        elif qqq_move_pct > config.QQQ_NOISE_THRESHOLD:
            qqq_move = QQQMove.UP
        elif qqq_move_pct < -0.8:
            qqq_move = QQQMove.DOWN_STRONG
        elif qqq_move_pct < -config.QQQ_NOISE_THRESHOLD:
            qqq_move = QQQMove.DOWN
        else:
            qqq_move = QQQMove.FLAT

        strategy, _, _ = self.recommend_strategy_and_direction(
            micro_regime=micro_regime,
            micro_score=micro_score,
            vix_current=vix_current,
            vix_direction=VIXDirection.STABLE,  # Default for legacy
            qqq_move=qqq_move,
            qqq_move_pct=qqq_move_pct,
        )
        return strategy

    # =========================================================================
    # FULL UPDATE CYCLE
    # =========================================================================

    def update(
        self,
        vix_current: float,
        vix_open: float,
        qqq_current: float,
        qqq_open: float,
        current_time: str,
        move_duration_minutes: int = 120,
        macro_regime_score: float = 50.0,
        vix_level_override: Optional[float] = None,
        qqq_atr_pct: Optional[float] = None,
    ) -> MicroRegimeState:
        """
        Full update cycle for Micro Regime Engine.

        Should be called every 15-30 minutes during intraday trading.

        Args:
            vix_current: Current VIX value (UVXY-derived proxy for direction).
            vix_open: VIX at market open.
            qqq_current: Current QQQ price.
            qqq_open: QQQ at market open.
            current_time: Current timestamp string.
            move_duration_minutes: How long the current move has taken.
            macro_regime_score: V2.5 - Macro regime score for Grind-Up Override (default 50).
            vix_level_override: V2.11 - If provided, use this for LEVEL classification
                               instead of vix_current. This allows using CBOE VIX for
                               level while using UVXY proxy for direction.
            qqq_atr_pct: Optional QQQ ATR as percent-of-price for adaptive move gate.

        Returns:
            Updated MicroRegimeState.
        """
        # Store open values
        self._state.vix_open = vix_open
        self._state.vix_current = vix_current
        self._qqq_open = qqq_open

        # Add to VIX history
        vix_change_pct = (vix_current - vix_open) / vix_open * 100 if vix_open > 0 else 0
        self._vix_history.append(
            VIXSnapshot(
                timestamp=current_time,
                value=vix_current,
                change_from_open_pct=vix_change_pct,
            )
        )

        # V2.11 (Pitfall #7): Separate VIX Level from VIX Direction
        # Level: Use CBOE VIX (vix_level_override) if provided, prevents false spikes
        # Direction: Use UVXY-derived proxy (vix_current) for accurate direction
        vix_for_level = vix_level_override if vix_level_override is not None else vix_current
        self._state.vix_level, _ = self.classify_vix_level(vix_for_level)
        self._state.vix_direction, _ = self.classify_vix_direction(vix_current, vix_open)

        # Detect whipsaw
        self._state.whipsaw_state, _ = self._detect_whipsaw()

        # Classify micro regime
        self._state.micro_regime = self.classify_micro_regime(
            self._state.vix_level, self._state.vix_direction
        )

        # Calculate micro score
        self._state.micro_score = self.calculate_micro_score(
            vix_current, vix_open, qqq_current, qqq_open, move_duration_minutes
        )

        # V2.3.4: Classify QQQ move direction (not just magnitude)
        self._state.qqq_direction, signed_move_pct = self.classify_qqq_move(qqq_current, qqq_open)
        self._state.qqq_move_pct = abs(signed_move_pct)

        # V2.3.4: Recommend strategy AND direction together
        # V2.5: Pass macro_regime_score for Grind-Up Override
        strategy, direction, reason = self.recommend_strategy_and_direction(
            micro_regime=self._state.micro_regime,
            micro_score=self._state.micro_score,
            vix_current=vix_current,
            vix_direction=self._state.vix_direction,
            qqq_move=self._state.qqq_direction,
            qqq_move_pct=signed_move_pct,
            macro_regime_score=macro_regime_score,
            qqq_atr_pct=qqq_atr_pct,
        )
        self._state.recommended_strategy = strategy
        self._state.recommended_direction = direction
        self._state.recommended_reason = reason or ""

        self._state.last_update = current_time

        # V2.3.4: Log includes QQQ direction and recommended option direction
        # V2.18: Use MICRO_REGIME: prefix for easy log verification (Fix #6)
        # V2.18.1: Only log on strategy CHANGE to avoid backtest timeout (was logging every update)
        dir_str = (
            self._state.recommended_direction.value if self._state.recommended_direction else "NONE"
        )
        qqq_dir_str = self._state.qqq_direction.value if self._state.qqq_direction else "NONE"

        # Only log if strategy changed from previous update (reduces log volume 95%)
        strategy_changed = (
            not hasattr(self, "_prev_strategy")
            or self._prev_strategy != self._state.recommended_strategy
        )
        if strategy_changed:
            self.log(
                f"MICRO_REGIME: VIX={vix_current:.1f} ({self._state.vix_direction.value}) | "
                f"QQQ={qqq_dir_str} ({self._state.qqq_move_pct:+.2f}%) | "
                f"Regime={self._state.micro_regime.value} | Score={self._state.micro_score:.0f} | "
                f"Strategy={self._state.recommended_strategy.value} | Direction={dir_str} | "
                f"Why={self._state.recommended_reason}"
            )
            self._prev_strategy = self._state.recommended_strategy

        return self._state

    def get_state(self) -> MicroRegimeState:
        """Get current state."""
        return self._state

    def check_spike_alert(self, vix_current: float, vix_5min_ago: float, current_time: str) -> bool:
        """
        Layer 1: Spike detection (every 5 minutes).

        Args:
            vix_current: Current VIX value.
            vix_5min_ago: VIX value 5 minutes ago.
            current_time: Current timestamp.

        Returns:
            True if spike detected (should pause entries).
        """
        if vix_5min_ago <= 0:
            return False

        change_pct = abs((vix_current - vix_5min_ago) / vix_5min_ago * 100)

        if change_pct > config.VIX_MONITOR_SPIKE_THRESHOLD:
            self.log(f"SPIKE_ALERT: VIX moved {change_pct:.1f}% in 5 min")
            # Set cooldown (would need proper time handling in real implementation)
            self._state.spike_cooldown_until = current_time
            return True

        return False

    def reset_daily(self) -> None:
        """Reset state at start of new trading day."""
        self._state = MicroRegimeState()
        self._vix_history.clear()
        self._vix_15min_ago = 0.0
        self._vix_30min_ago = 0.0
        self._qqq_open = 0.0
        self.log("Daily reset complete")

    # =========================================================================
    # V5.3: MICRO CONVICTION ENGINE
    # =========================================================================

    def has_conviction(
        self,
        uvxy_intraday_pct: float,
        vix_level: float,
    ) -> Tuple[bool, Optional[str], str]:
        """
        V5.3: Check if Micro has conviction to override Macro.

        Conviction triggers:
        - UVXY > +8% intraday → BEARISH
        - UVXY < -5% intraday → BULLISH
        - VIX > 35 → CRISIS (BEARISH)
        - VIX < 12 → COMPLACENT (BULLISH)
        - Micro state in BEARISH_STATES → BEARISH
        - Micro state in BULLISH_STATES → BULLISH

        Args:
            uvxy_intraday_pct: UVXY change from open as decimal (0.08 = +8%)
            vix_level: Current VIX level

        Returns:
            Tuple of (has_conviction, direction, reason)
        """
        # UVXY-based conviction (fastest signal)
        if uvxy_intraday_pct > config.MICRO_UVXY_BEARISH_THRESHOLD:
            return (
                True,
                "BEARISH",
                f"UVXY +{uvxy_intraday_pct:.0%} > +{config.MICRO_UVXY_BEARISH_THRESHOLD:.0%}",
            )

        if uvxy_intraday_pct < config.MICRO_UVXY_BULLISH_THRESHOLD:
            return (
                True,
                "BULLISH",
                f"UVXY {uvxy_intraday_pct:.0%} < {config.MICRO_UVXY_BULLISH_THRESHOLD:.0%}",
            )

        # VIX level conviction (extreme levels)
        if vix_level > config.MICRO_VIX_CRISIS_LEVEL:
            return (
                True,
                "BEARISH",
                f"VIX {vix_level:.1f} > {config.MICRO_VIX_CRISIS_LEVEL} (CRISIS)",
            )

        if vix_level < config.MICRO_VIX_COMPLACENT_LEVEL:
            return (
                True,
                "BULLISH",
                f"VIX {vix_level:.1f} < {config.MICRO_VIX_COMPLACENT_LEVEL} (COMPLACENT)",
            )

        # V6.3: Removed state-based fallback (was lines 1674-1680)
        # State-based conviction was re-classifying Micro's state, not providing
        # independent validation. Conviction should only fire on extreme signals
        # (UVXY ±threshold, VIX extremes). Let Micro's direction stand otherwise.

        # No extreme signals - trust Micro's computed direction
        return False, None, "No extreme intraday signals - trust Micro direction"
