"""
Options Engine V2.1.1 - Dual-Mode Architecture for QQQ Options.

V2.1.1 COMPLETE REDESIGN with two distinct operating modes:

MODE 1: SWING MODE (5-45 DTE)
- 15% allocation of portfolio
- Multi-day positions (hold overnight allowed)
- Uses macro regime for direction
- 4-strategy portfolio: Debit Spreads, Credit Spreads, ITM Long, Protective Puts
- Simple intraday filters (not Micro Regime)

MODE 2: INTRADAY MODE (0-2 DTE)
- 5% allocation of portfolio
- Same-day entry and exit (must close by 3:30 PM)
- Uses MICRO REGIME ENGINE for decision making
- VIX Level × VIX Direction = 21 distinct trading regimes
- Strategies: Debit Fade, Credit Spreads, ITM Momentum, Protective Puts

KEY INSIGHT: VIX Direction is THE key differentiator.
Same VIX level + different direction = OPPOSITE strategies!

VIX at 25 and FALLING = Recovery starting, FADE the move (buy calls)
VIX at 25 and RISING = Fear building, RIDE the move (buy puts)

ENTRY TIMING MATTERS MORE FOR SHORTER DTE:
- 2 DTE: 2-hour window = 15% of option's life → Micro Regime ESSENTIAL
- 14 DTE: 2-hour window = 2% of option's life → Simple filters sufficient

Spec: docs/v2-specs/V2_1_OPTIONS_ENGINE_DESIGN.txt
"""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any, Deque, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from AlgorithmImports import QCAlgorithm
    from engines.core.risk_engine import RiskEngine

import config
from engines.core.risk_engine import GreeksSnapshot
from models.enums import (
    IntradayStrategy,
    MicroRegime,
    OptionsMode,
    QQQMove,
    Urgency,
    VIXDirection,
    VIXLevel,
    WhipsawState,
)
from models.target_weight import TargetWeight


class OptionDirection(Enum):
    """Option direction (call or put)."""

    CALL = "CALL"
    PUT = "PUT"


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
    max_profit: float  # Width - net debit
    width: float  # Strike difference ($3-5)
    entry_time: str
    entry_score: float
    num_spreads: int  # Number of spread contracts
    regime_at_entry: float  # Regime score at entry
    is_closing: bool = False  # V2.12 Fix #2: Prevent duplicate exit signals

    @property
    def profit_target(self) -> float:
        """50% of max profit per V2.3 spec."""
        return self.net_debit + (self.max_profit * 0.5)

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
            "is_closing": self.is_closing,
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
            is_closing=data.get("is_closing", False),  # V2.12: Default False for backwards compat
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

    def should_retry(self, max_retries: int = 3) -> bool:
        """Check if another retry is allowed."""
        return self.retry_count < max_retries

    def record_attempt(self, time_str: str) -> None:
        """Record a retry attempt."""
        self.retry_count += 1
        self.last_attempt_time = time_str


# =============================================================================
# V2.8: IV SENSOR (Volatility-Adaptive Strategy Selection)
# =============================================================================


class IVSensor:
    """
    V2.8: Smoothed IV classification to prevent strategy flickering.

    Uses 30-minute SMA of VIX to classify IV environment into three tiers:
    - LOW (< 15): Use debit spreads with monthly expiration
    - MEDIUM (15-25): Use debit spreads with weekly expiration
    - HIGH (> 25): Use credit spreads with weekly expiration

    The smoothing prevents rapid strategy switching when VIX hovers near thresholds.
    """

    def __init__(self, smoothing_minutes: int = 30, log_func=None):
        """
        Initialize IV Sensor.

        Args:
            smoothing_minutes: SMA window size (default 30 minutes)
            log_func: Logging function (optional)
        """
        self._vix_history: Deque[float] = deque(
            maxlen=smoothing_minutes or config.VASS_IV_SMOOTHING_MINUTES
        )
        self._last_classification: Optional[str] = None
        self._log = log_func or (lambda x: None)

    def update(self, vix_value: float) -> None:
        """
        Add VIX reading (called every minute from OnData).

        Args:
            vix_value: Current VIX value
        """
        if vix_value > 0:  # Sanity check
            self._vix_history.append(vix_value)

    def get_smoothed_vix(self) -> float:
        """
        Return 30-min SMA of VIX.

        Returns:
            Smoothed VIX value, or 20.0 (medium IV default) if no history
        """
        if not self._vix_history:
            return 20.0  # Default to medium IV
        return sum(self._vix_history) / len(self._vix_history)

    def classify(self) -> str:
        """
        Classify IV environment: LOW, MEDIUM, HIGH.

        Returns:
            IV environment classification string
        """
        if not self._vix_history or len(self._vix_history) < 5:
            # FALLBACK: Assume MEDIUM IV if insufficient data
            self._log("VASS: Insufficient VIX data, defaulting to MEDIUM")
            return "MEDIUM"

        smoothed = self.get_smoothed_vix()

        if smoothed < config.VASS_IV_LOW_THRESHOLD:
            classification = "LOW"
        elif smoothed > config.VASS_IV_HIGH_THRESHOLD:
            classification = "HIGH"
        else:
            classification = "MEDIUM"

        # Log classification changes
        if self._last_classification != classification:
            self._log(
                f"VASS: IV environment changed {self._last_classification} → {classification} "
                f"(VIX SMA={smoothed:.1f})"
            )
            self._last_classification = classification

        return classification

    def is_ready(self) -> bool:
        """
        True if enough history for reliable smoothing.

        Returns:
            True if at least 10 minutes of VIX data collected
        """
        return len(self._vix_history) >= 10

    def get_history_length(self) -> int:
        """Return current history length (for debugging)."""
        return len(self._vix_history)

    def reset(self) -> None:
        """Clear history (for testing or session reset)."""
        self._vix_history.clear()
        self._last_classification = None


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
class VIXSnapshot:
    """VIX data point for monitoring."""

    timestamp: str
    value: float
    change_from_open_pct: float = 0.0


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
            recommended_strategy=IntradayStrategy(data.get("recommended_strategy", "NO_TRADE")),
            qqq_move_pct=data.get("qqq_move_pct", 0.0),
            vix_current=data.get("vix_current", 15.0),
            vix_open=data.get("vix_open", 15.0),
            last_update=data.get("last_update", ""),
            spike_cooldown_until=data.get("spike_cooldown_until", ""),
        )
        # Set new fields if present
        if qqq_dir:
            state.qqq_direction = QQQMove(qqq_dir)
        if rec_dir:
            from engines.satellite.options_engine import OptionDirection

            state.recommended_direction = OptionDirection(rec_dir)
        return state


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

    def __init__(self, log_func=None):
        """Initialize Micro Regime Engine."""
        self._log_func = log_func
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
        elif vix_change_pct <= config.VIX_DIRECTION_STABLE_HIGH:
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
            return VIXLevel.LOW, config.MICRO_SCORE_VIX_CALM
        elif vix_value < config.VIX_LEVEL_NORMAL_MAX:  # V2.3.11: < 18 (unchanged)
            return VIXLevel.LOW, config.MICRO_SCORE_VIX_NORMAL
        elif vix_value < 22:  # V2.3.11: < 22 (was 23)
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
    ) -> Tuple[IntradayStrategy, Optional[OptionDirection], str]:
        """
        V2.3.4: Recommend intraday strategy AND direction based on regime + QQQ move.
        V2.5: Added Grind-Up Override for CAUTIOUS regime.

        This is the core decision engine that combines:
        - VIX Level × VIX Direction (market fear state)
        - QQQ Move Direction (what to fade or ride)

        Key Logic:
        - FADE: QQQ up + VIX falling = Buy PUT (fade the up move)
        - FADE: QQQ down + VIX falling = Buy CALL (fade the down move)
        - MOMENTUM: QQQ up + VIX rising = Buy CALL (ride momentum)
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

        Returns:
            Tuple of (IntradayStrategy, OptionDirection or None, reason string).
        """
        # =====================================================================
        # RULE 1: No trade if QQQ is flat (no edge)
        # =====================================================================
        if qqq_move == QQQMove.FLAT:
            return IntradayStrategy.NO_TRADE, None, "QQQ flat - no edge"

        # =====================================================================
        # RULE 2: Danger regimes - No trade or protective only
        # =====================================================================
        danger_regimes = {
            MicroRegime.RISK_OFF_LOW,
            MicroRegime.BREAKING,
            MicroRegime.UNSTABLE,
            MicroRegime.FULL_PANIC,
            MicroRegime.CRASH,
            MicroRegime.VOLATILE,
        }
        if micro_regime in danger_regimes:
            if micro_score < 0:
                return IntradayStrategy.PROTECTIVE_PUTS, OptionDirection.PUT, "Crisis protection"
            return IntradayStrategy.NO_TRADE, None, f"Danger regime: {micro_regime.value}"

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

        # =====================================================================
        # RULE 5: FADE SETUP (Mean Reversion)
        # Best setup: QQQ moved + VIX falling = market calming, fade the move
        # V2.3.15 SNIPER LOGIC: Added Gate 3a minimum move check (0.50%)
        # V2.19: Added VIX Floor - block DEBIT_FADE when VIX < 13.5 (apathy market)
        # =====================================================================
        fade_regimes = {
            MicroRegime.PERFECT_MR,
            MicroRegime.GOOD_MR,
            MicroRegime.NORMAL,
            MicroRegime.RECOVERING,
            MicroRegime.IMPROVING,
        }
        if micro_regime in fade_regimes and micro_score >= config.MICRO_SCORE_MODERATE:
            # V2.19: VIX Floor Check - Block DEBIT_FADE in "apathy" market
            # Low VIX (<13.5) markets don't mean-revert - trends persist longer
            vix_floor = getattr(config, "INTRADAY_DEBIT_FADE_VIX_MIN", 13.5)
            if vix_current < vix_floor:
                return (
                    IntradayStrategy.NO_TRADE,
                    None,
                    f"DEBIT_FADE_BLOCKED: VIX {vix_current:.1f} < {vix_floor} (apathy market)",
                )
            # VIX falling = safe to fade
            if vix_is_falling or vix_direction == VIXDirection.STABLE:
                # V2.3.15 SNIPER: Gate 3a - FADE requires minimum move (0.50%)
                # This prevents fading tiny moves that are just noise
                if abs(qqq_move_pct) < config.INTRADAY_FADE_MIN_MOVE:
                    return (
                        IntradayStrategy.NO_TRADE,
                        None,
                        f"FADE blocked: |{qqq_move_pct:.2f}%| < {config.INTRADAY_FADE_MIN_MOVE}% min",
                    )

                # V2.3.16: FADE max cap - don't fade runaway trends/crashes
                # When move exceeds 1.20%, trend is too strong to mean-revert
                if abs(qqq_move_pct) > config.INTRADAY_FADE_MAX_MOVE:
                    return (
                        IntradayStrategy.NO_TRADE,
                        None,
                        f"FADE blocked: |{qqq_move_pct:.2f}%| > {config.INTRADAY_FADE_MAX_MOVE}% max (runaway trend)",
                    )

                if qqq_is_up:
                    # QQQ up + VIX falling/stable = Buy PUT (fade the rally)
                    return (
                        IntradayStrategy.DEBIT_FADE,
                        OptionDirection.PUT,
                        f"Fade rally: QQQ +{qqq_move_pct:.2f}%, VIX {vix_direction.value}",
                    )
                elif qqq_is_down:
                    # QQQ down + VIX falling/stable = Buy CALL (fade the dip)
                    return (
                        IntradayStrategy.DEBIT_FADE,
                        OptionDirection.CALL,
                        f"Fade dip: QQQ {qqq_move_pct:.2f}%, VIX {vix_direction.value}",
                    )

            # VIX rising = DON'T fade, momentum might continue
            if vix_is_rising:
                return (
                    IntradayStrategy.NO_TRADE,
                    None,
                    f"VIX rising ({vix_direction.value}) - don't fade",
                )

        # =====================================================================
        # RULE 6: MOMENTUM SETUP (Ride the move)
        # VIX rising + QQQ moving = fear/greed, ride momentum
        # =====================================================================
        momentum_regimes = {
            MicroRegime.DETERIORATING,
            MicroRegime.ELEVATED,
            MicroRegime.WORSENING_HIGH,
            MicroRegime.PANIC_EASING,
            MicroRegime.CALMING,
        }
        if micro_regime in momentum_regimes:
            if vix_current > config.INTRADAY_ITM_MIN_VIX:
                if abs(qqq_move_pct) >= config.INTRADAY_ITM_MIN_MOVE:
                    if qqq_is_up:
                        # QQQ up strongly = ride momentum with CALL
                        return (
                            IntradayStrategy.ITM_MOMENTUM,
                            OptionDirection.CALL,
                            f"Momentum up: QQQ +{qqq_move_pct:.2f}%",
                        )
                    elif qqq_is_down:
                        # QQQ down strongly = ride momentum with PUT
                        return (
                            IntradayStrategy.ITM_MOMENTUM,
                            OptionDirection.PUT,
                            f"Momentum down: QQQ {qqq_move_pct:.2f}%",
                        )

        # =====================================================================
        # RULE 7: Caution regimes - smaller moves, credits only
        # V2.5: Added Grind-Up Override for strong rallies in safe macro conditions
        # =====================================================================
        caution_regimes = {
            MicroRegime.CAUTION_LOW,
            MicroRegime.TRANSITION,
            MicroRegime.CHOPPY_LOW,
            MicroRegime.CAUTIOUS,
            MicroRegime.WORSENING,
        }
        if micro_regime in caution_regimes:
            # V2.5: Grind-Up Override - capture strong rallies even in CAUTIOUS regime
            # Only triggers when: (1) CAUTIOUS regime, (2) QQQ UP > 0.50%, (3) macro safe
            grind_up_enabled = getattr(config, "GRIND_UP_OVERRIDE_ENABLED", False)
            grind_up_min_move = getattr(config, "GRIND_UP_MIN_MOVE", 0.50)
            grind_up_macro_safe = getattr(config, "GRIND_UP_MACRO_SAFE_MIN", 40)

            if grind_up_enabled and micro_regime == MicroRegime.CAUTIOUS:
                is_strong_rally = qqq_move_pct > grind_up_min_move  # Positive only, not abs()
                is_macro_safe = macro_regime_score > grind_up_macro_safe

                if is_strong_rally and is_macro_safe:
                    # Override: Ride the grind-up with CALL
                    return (
                        IntradayStrategy.ITM_MOMENTUM,
                        OptionDirection.CALL,
                        f"GRIND_UP_OVERRIDE: QQQ +{qqq_move_pct:.2f}% > {grind_up_min_move}% | Macro={macro_regime_score:.1f} > {grind_up_macro_safe}",
                    )
                elif is_strong_rally and not is_macro_safe:
                    # Bear trap protection - log and skip
                    self.log(
                        f"MICRO: Grind-Up Rejected (Bear Market Trap Risk) | QQQ +{qqq_move_pct:.2f}% but Macro={macro_regime_score:.1f} <= {grind_up_macro_safe}"
                    )

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
        )
        self._state.recommended_strategy = strategy
        self._state.recommended_direction = direction

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
                f"Strategy={self._state.recommended_strategy.value} | Direction={dir_str}"
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


class OptionsEngine:
    """
    Options Engine V2.1.1 - Dual-Mode Architecture.

    Operates in TWO DISTINCT MODES based on DTE:

    MODE 1: SWING MODE (5-45 DTE)
    - 15% allocation, multi-day positions
    - Uses macro regime for direction
    - 4-factor entry scoring (ADX, Momentum, IV, Liquidity)
    - Simple intraday filters (not Micro Regime)

    MODE 2: INTRADAY MODE (0-2 DTE)
    - 5% allocation, same-day trades
    - Uses MICRO REGIME ENGINE for decision making
    - VIX Level × VIX Direction = 21 regimes
    - Strategies: Debit Fade, Credit Spreads, ITM Momentum

    Note: This engine does NOT place orders. It only provides
    signals via TargetWeight objects for the Portfolio Router.

    Spec: docs/v2-specs/V2_1_OPTIONS_ENGINE_DESIGN.txt
    """

    def __init__(self, algorithm: Optional["QCAlgorithm"] = None):
        """Initialize Options Engine with dual-mode support."""
        self.algorithm = algorithm

        # Position tracking (separate for each mode)
        self._swing_position: Optional[OptionsPosition] = None
        self._intraday_position: Optional[OptionsPosition] = None

        # V2.3: Spread position tracking (replaces single-leg for swing mode)
        self._spread_position: Optional[SpreadPosition] = None

        # Legacy single position (for backwards compatibility)
        self._position: Optional[OptionsPosition] = None

        # Trade counters
        self._trades_today: int = 0
        self._intraday_trades_today: int = 0
        self._swing_trades_today: int = 0  # V2.9: Swing mode counter
        self._total_options_trades_today: int = 0  # V2.9: Global counter (Bug #4 fix)
        self._last_trade_date: Optional[str] = None

        # Current operating mode
        self._current_mode: OptionsMode = OptionsMode.SWING

        # V2.1.1: Micro Regime Engine for intraday trading
        self._micro_regime_engine = MicroRegimeEngine(log_func=self.log)

        # V2.1.1: VIX tracking for simple intraday filters (Swing Mode)
        self._vix_at_open: float = 0.0
        self._spy_at_open: float = 0.0
        self._spy_gap_pct: float = 0.0

        # Pending entry state (set by check_entry_signal, used by register_entry)
        self._pending_contract: Optional[OptionContract] = None
        self._pending_entry_score: Optional[float] = None
        self._pending_num_contracts: Optional[int] = None
        self._pending_stop_pct: Optional[float] = None
        self._pending_stop_price: Optional[float] = None
        self._pending_target_price: Optional[float] = None

        # V2.3: Pending spread entry state
        self._pending_spread_long_leg: Optional[OptionContract] = None
        self._pending_spread_short_leg: Optional[OptionContract] = None
        self._pending_spread_type: Optional[str] = None
        self._pending_net_debit: Optional[float] = None
        self._pending_max_profit: Optional[float] = None
        self._pending_spread_width: Optional[float] = None

        # V2.3 FIX: Prevent order spam - track failed entry attempts
        self._entry_attempted_today: bool = False
        # V2.21: Post-rejection margin cap for adaptive retry sizing
        self._rejection_margin_cap: Optional[float] = None
        self._swing_time_warning_logged: bool = False

        # V2.3.21: Spread scan throttle - only attempt every 15 minutes to reduce log noise
        self._last_spread_scan_time: Optional[str] = None

        # V2.4.3: Spread FAILURE cooldown - don't retry for 4 hours after construction fails
        # Prevents 340+ retries when no valid contracts exist
        self._spread_failure_cooldown_until: Optional[str] = None

        # V2.3.2 FIX #4: Track if pending entry is intraday (for correct position registration)
        self._pending_intraday_entry: bool = False

        # V2.3.3 FIX #3: Prevent duplicate exit signals while waiting for fill
        self._pending_intraday_exit: bool = False

        # V2.6 Bug #16: Post-trade margin cooldown tracking
        # After closing a spread, wait before new entry (T+1 settlement)
        self._last_spread_exit_time: Optional[str] = None

        # V2.8: VASS (Volatility-Adaptive Strategy Selection)
        self._iv_sensor = IVSensor(
            smoothing_minutes=config.VASS_IV_SMOOTHING_MINUTES,
            log_func=self.log,
        )

    def log(self, message: str, trades_only: bool = False) -> None:
        """
        Log via algorithm with LiveMode awareness.

        Args:
            message: Log message to output.
            trades_only: If True, always log (for trade entries/exits/errors).
                        If False, only log in LiveMode (for diagnostics).
        """
        if self.algorithm:
            # V2.18.1: Fixed - was logging everything in debug mode, causing backtest timeout
            # Only log if: trades_only=True OR we're in LiveMode
            if trades_only:
                self.algorithm.Log(message)
            elif hasattr(self.algorithm, "LiveMode") and self.algorithm.LiveMode:
                self.algorithm.Log(message)
            # In backtest mode with trades_only=False, skip logging (silent)

    def _set_spread_failure_cooldown(self, current_time: Optional[str]) -> None:
        """
        V2.4.3: Set cooldown after spread construction fails.

        Prevents 340+ retries when no valid contracts exist.
        Cooldown = 4 hours (SPREAD_FAILURE_COOLDOWN_HOURS).

        Args:
            current_time: Current timestamp in "YYYY-MM-DD HH:MM:SS" format.
        """
        if not current_time:
            return

        try:
            # Parse current time
            # Format: "YYYY-MM-DD HH:MM:SS"
            date_part = current_time[:10]  # "YYYY-MM-DD"
            time_part = current_time[11:19]  # "HH:MM:SS"
            hour = int(time_part[:2])
            minute = int(time_part[3:5])
            second = int(time_part[6:8])

            # Add cooldown hours
            cooldown_hours = config.SPREAD_FAILURE_COOLDOWN_HOURS
            new_hour = hour + cooldown_hours

            # Handle day overflow (if cooldown pushes past midnight)
            if new_hour >= 24:
                # Just set to end of day - will reset tomorrow anyway
                cooldown_until = f"{date_part} 23:59:59"
            else:
                cooldown_until = f"{date_part} {new_hour:02d}:{minute:02d}:{second:02d}"

            self._spread_failure_cooldown_until = cooldown_until
            self.log(
                f"SPREAD: Construction failed - entering {cooldown_hours}h cooldown until {cooldown_until}"
            )
        except (ValueError, IndexError) as e:
            self.log(f"SPREAD: Failed to set cooldown: {e}")

    # =========================================================================
    # ENTRY SCORE CALCULATION
    # =========================================================================

    def calculate_entry_score(
        self,
        adx_value: float,
        current_price: float,
        ma200_value: float,
        iv_rank: float,
        bid_ask_spread_pct: float,
        open_interest: int,
    ) -> EntryScore:
        """
        Calculate 4-factor entry score.

        Args:
            adx_value: Current ADX(14) value.
            current_price: Current underlying price.
            ma200_value: 200-day moving average value.
            iv_rank: IV percentile (0-100).
            bid_ask_spread_pct: Bid-ask spread as percentage.
            open_interest: Open interest for the contract.

        Returns:
            EntryScore with all factor scores.
        """
        score = EntryScore()

        # Factor 1: ADX (trend strength)
        score.score_adx = self._score_adx(adx_value)

        # Factor 2: Momentum (price vs MA200)
        score.score_momentum = self._score_momentum(current_price, ma200_value)

        # Factor 3: IV Rank
        score.score_iv = self._score_iv_rank(iv_rank)

        # Factor 4: Liquidity
        score.score_liquidity = self._score_liquidity(bid_ask_spread_pct, open_interest)

        return score

    def _score_adx(self, adx_value: float) -> float:
        """
        Score ADX factor (0-1).

        ADX < 20: 0.25 (weak trend)
        ADX 20-25: 0.50 (moderate)
        ADX 25-35: 0.75 (strong)
        ADX >= 35: 1.00 (very strong)
        """
        if adx_value < config.OPTIONS_ADX_WEAK:
            return 0.25
        elif adx_value < config.OPTIONS_ADX_MODERATE:
            return 0.50
        elif adx_value < config.OPTIONS_ADX_STRONG:
            return 0.75
        else:
            return 1.00

    def _score_momentum(self, current_price: float, ma200_value: float) -> float:
        """
        Score momentum factor (0-1).

        Price significantly above MA200: 1.0
        Price above MA200: 0.75
        Price near MA200: 0.50
        Price below MA200: 0.25
        """
        if ma200_value <= 0:
            return 0.25

        ratio = current_price / ma200_value

        if ratio >= 1.05:  # 5%+ above MA200
            return 1.00
        elif ratio >= 1.00:  # Above MA200
            return 0.75
        elif ratio >= 0.98:  # Near MA200 (within 2%)
            return 0.50
        else:  # Below MA200
            return 0.25

    def _score_iv_rank(self, iv_rank: float) -> float:
        """
        Score IV Rank factor (0-1).

        IV rank 20-80: Optimal range, full score
        IV rank < 20 or > 80: Suboptimal, reduced score
        """
        if config.OPTIONS_IV_RANK_LOW <= iv_rank <= config.OPTIONS_IV_RANK_HIGH:
            # Optimal range: scale from 0.75 to 1.0 based on position in range
            # Closer to middle (50) is better
            distance_from_50 = abs(iv_rank - 50)
            # 0 distance = 1.0, 30 distance = 0.75
            return 1.0 - (distance_from_50 / 120)  # Max distance is 30
        elif iv_rank < config.OPTIONS_IV_RANK_LOW:
            return 0.25  # Too low IV
        else:
            return 0.25  # Too high IV

    def _score_liquidity(self, spread_pct: float, open_interest: int) -> float:
        """
        Score liquidity factor (0-1).

        Based on bid-ask spread and open interest.
        """
        # Start with spread score
        if spread_pct <= config.OPTIONS_SPREAD_MAX_PCT:
            spread_score = 1.0
        elif spread_pct <= config.OPTIONS_SPREAD_WARNING_PCT:
            spread_score = 0.50
        else:
            spread_score = 0.0  # Too wide

        # OI score
        if open_interest >= config.OPTIONS_MIN_OPEN_INTEREST:
            oi_score = 1.0
        elif open_interest >= config.OPTIONS_MIN_OPEN_INTEREST // 2:
            oi_score = 0.50
        else:
            oi_score = 0.0  # Too thin

        # Combined liquidity score (average)
        return (spread_score + oi_score) / 2

    # =========================================================================
    # STOP TIER MAPPING
    # =========================================================================

    def get_stop_tier(self, entry_score: float) -> Dict[str, float]:
        """
        Get stop tier parameters based on entry score.

        Higher entry score → wider stops, fewer contracts.

        Args:
            entry_score: Total entry score (3.0-4.0).

        Returns:
            Dict with "stop_pct" and "contracts" values.
        """
        # Find the appropriate tier
        tiers = sorted(config.OPTIONS_STOP_TIERS.keys())

        for i, threshold in enumerate(tiers):
            if entry_score < threshold:
                if i == 0:
                    return config.OPTIONS_STOP_TIERS[tiers[0]]
                return config.OPTIONS_STOP_TIERS[tiers[i - 1]]

        # At or above highest tier
        return config.OPTIONS_STOP_TIERS[tiers[-1]]

    def calculate_position_size(
        self,
        entry_score: float,
        premium: float,
        portfolio_value: float,
        days_to_expiry: int = None,
    ) -> tuple:
        """
        Calculate position size based on entry score and 1% risk.

        V2.3.8: Uses tighter stops for 0DTE options to limit slippage damage.
        0DTE options move extremely fast - StopMarketOrder can fill at much
        worse prices due to slippage. Using 15% stops limits max loss to ~30%.

        Args:
            entry_score: Total entry score (3.0-4.0).
            premium: Option premium per contract.
            portfolio_value: Total portfolio value.
            days_to_expiry: Days to expiration (0 for 0DTE).

        Returns:
            Tuple of (num_contracts, stop_pct, stop_price, target_price).
        """
        # Get tier parameters
        tier = self.get_stop_tier(entry_score)
        stop_pct = tier["stop_pct"]
        base_contracts = tier["contracts"]

        # V2.3.8: Use tighter stops for 0DTE (PART 14 Pitfall 2)
        # 0DTE options move fast - slippage can double the intended loss
        if days_to_expiry is not None and days_to_expiry <= 1:
            stop_pct = config.OPTIONS_0DTE_STOP_PCT
            self.log(f"POSITION_SIZE: Using 0DTE tight stop {stop_pct:.0%} (DTE={days_to_expiry})")

        # Calculate risk-adjusted contracts
        # Risk = contracts × premium × stop_pct
        # Target risk = portfolio_value × 1%
        target_risk = portfolio_value * config.OPTIONS_RISK_PER_TRADE
        risk_per_contract = premium * stop_pct * 100  # × 100 for contract multiplier

        if risk_per_contract <= 0:
            return (0, stop_pct, 0, 0)

        # Calculate contracts based on risk
        risk_based_contracts = int(target_risk / risk_per_contract)

        # Use minimum of risk-based and tier-based
        num_contracts = min(risk_based_contracts, base_contracts)

        # Ensure at least 1 contract
        num_contracts = max(1, num_contracts)

        # Calculate stop and target prices
        stop_price = premium * (1 - stop_pct)
        target_price = premium * (1 + config.OPTIONS_PROFIT_TARGET_PCT)

        return (num_contracts, stop_pct, stop_price, target_price)

    # =========================================================================
    # V2.3: SPREAD LEG SELECTION
    # =========================================================================

    def select_spread_legs(
        self,
        contracts: List[OptionContract],
        direction: OptionDirection,
        target_width: float = None,
        current_time: str = None,
    ) -> Optional[tuple]:
        """
        V2.3.21: Select long and short leg contracts for a debit spread.

        "Smart Swing" Strategy (V2.3.21):
        - Long leg: ITM (delta 0.55-0.85) - prioritize execution
        - Short leg: OTM (delta 0.10-0.50) - reduce cost basis

        For Bull Call Spread:
        - Long leg: ITM call (delta 0.55-0.85)
        - Short leg: OTM call (higher strike, delta 0.10-0.50)

        For Bear Put Spread:
        - Long leg: ITM put (delta -0.55 to -0.85)
        - Short leg: OTM put (lower strike, delta -0.10 to -0.50)

        Args:
            contracts: List of available OptionContract objects.
            direction: CALL for Bull Call Spread, PUT for Bear Put Spread.
            target_width: Target spread width (default from config).
            current_time: Current timestamp for throttle check.

        Returns:
            Tuple of (long_leg, short_leg) or None if no valid spread found.
        """
        # V2.4.3: Check FAILURE cooldown first (4-hour penalty after failed construction)
        if current_time and self._spread_failure_cooldown_until:
            try:
                # Compare timestamps (format: "YYYY-MM-DD HH:MM:SS")
                if current_time < self._spread_failure_cooldown_until:
                    # Still in cooldown - silently skip (don't spam logs)
                    return None
                else:
                    # Cooldown expired - clear it and proceed
                    self._spread_failure_cooldown_until = None
            except (ValueError, TypeError):
                pass  # If comparison fails, proceed with scan

        # V2.3.21: Throttle spread scanning to reduce log noise
        # Only scan every 15 minutes (config.SPREAD_SCAN_THROTTLE_MINUTES)
        if current_time and self._last_spread_scan_time:
            # Extract hour:minute from timestamps (format: "YYYY-MM-DD HH:MM:SS")
            try:
                current_min_str = current_time[11:16]  # "HH:MM"
                last_min_str = self._last_spread_scan_time[11:16]
                # Parse to minutes since midnight
                curr_h, curr_m = int(current_min_str[:2]), int(current_min_str[3:5])
                last_h, last_m = int(last_min_str[:2]), int(last_min_str[3:5])
                curr_total = curr_h * 60 + curr_m
                last_total = last_h * 60 + last_m
                # Check if same day (compare dates)
                if current_time[:10] == self._last_spread_scan_time[:10]:
                    elapsed = curr_total - last_total
                    if elapsed < config.SPREAD_SCAN_THROTTLE_MINUTES:
                        # Skip scan, throttled
                        return None
            except (ValueError, IndexError):
                pass  # If parsing fails, proceed with scan

        # Update last scan time
        if current_time:
            self._last_spread_scan_time = current_time

        if not contracts:
            self.log("SPREAD: No contracts available for spread selection")
            return None

        if target_width is None:
            target_width = config.SPREAD_WIDTH_TARGET

        # Filter contracts by direction
        filtered = [c for c in contracts if c.direction == direction]

        if len(filtered) < 2:
            self.log(f"SPREAD: Not enough {direction.value} contracts for spread")
            self._set_spread_failure_cooldown(current_time)
            return None

        # For puts, delta is negative so we need to handle that
        is_call = direction == OptionDirection.CALL

        # V2.4.3 FIX: Filter by DTE FIRST, then delta
        # Problem: Chain filter (OPTIONS_SWING_DTE_MAX=45) retrieves 14-45 DTE contracts
        # But spread validation (SPREAD_DTE_MAX=21) rejects anything over 21 DTE
        # If we sort by delta first, a 35 DTE with perfect delta beats 18 DTE with good delta
        # Then the trade gets rejected later for DTE > 21
        # Solution: Filter by SPREAD_DTE_MIN/MAX before considering delta

        # V2.3.21: Find ITM long leg (delta 0.55-0.85 for calls, -0.55 to -0.85 for puts)
        # "Smart Swing" strategy prioritizes ITM for better execution and directional exposure
        long_candidates = []
        for c in filtered:
            # V2.4.3: DTE filter FIRST - must be within spread DTE range
            if c.days_to_expiry < config.SPREAD_DTE_MIN:
                continue
            if c.days_to_expiry > config.SPREAD_DTE_MAX:
                continue

            delta_abs = abs(c.delta)
            if config.SPREAD_LONG_LEG_DELTA_MIN <= delta_abs <= config.SPREAD_LONG_LEG_DELTA_MAX:
                # Check liquidity
                if c.open_interest >= config.OPTIONS_MIN_OPEN_INTEREST:
                    if c.spread_pct <= config.OPTIONS_SPREAD_MAX_PCT:
                        long_candidates.append(c)

        if not long_candidates:
            self.log(
                f"SPREAD: No valid long leg | DTE={config.SPREAD_DTE_MIN}-{config.SPREAD_DTE_MAX} | "
                f"Delta={config.SPREAD_LONG_LEG_DELTA_MIN}-{config.SPREAD_LONG_LEG_DELTA_MAX}"
            )
            self._set_spread_failure_cooldown(current_time)
            return None

        # V2.3.21: Sort by delta proximity to 0.70 (ITM target for Smart Swing)
        long_candidates.sort(key=lambda c: abs(abs(c.delta) - 0.70))
        long_leg = long_candidates[0]

        # V2.4.3: WIDTH-BASED short leg selection (fixes "delta trap" in backtesting)
        # Problem: Delta values jump (0.45 → 0.25) - no "perfect" delta exists
        # Solution: Find short leg by STRIKE WIDTH, use delta only as tiebreaker
        short_candidates = []
        for c in filtered:
            # Skip same strike as long leg
            if c.strike == long_leg.strike:
                continue

            # V2.4.3: Short leg must be same expiration as long leg (standard spread)
            if c.days_to_expiry != long_leg.days_to_expiry:
                continue

            # Check direction-specific strike requirement
            if is_call:
                # Short leg must be higher strike (OTM)
                if c.strike <= long_leg.strike:
                    continue
            else:
                # Short leg must be lower strike (OTM)
                if c.strike >= long_leg.strike:
                    continue

            # Calculate width
            width = abs(c.strike - long_leg.strike)

            # V2.4.3: Filter by WIDTH, not delta
            if config.SPREAD_SHORT_LEG_BY_WIDTH:
                # Width-based selection: must be within min/max width bounds
                if width < config.SPREAD_WIDTH_MIN or width > config.SPREAD_WIDTH_MAX:
                    continue
                # Delta is soft preference only (no hard filter)
                delta_abs = abs(c.delta) if c.delta else 0.30  # Default if missing
            else:
                # Legacy: Hard delta filter (kept for backwards compatibility)
                delta_abs = abs(c.delta)
                if not (
                    config.SPREAD_SHORT_LEG_DELTA_MIN
                    <= delta_abs
                    <= config.SPREAD_SHORT_LEG_DELTA_MAX
                ):
                    continue

            # Check liquidity (relaxed for short leg)
            if c.open_interest >= config.OPTIONS_MIN_OPEN_INTEREST // 2:
                if c.spread_pct <= config.OPTIONS_SPREAD_WARNING_PCT:
                    short_candidates.append((c, width, delta_abs))

        if not short_candidates:
            self.log(
                f"SPREAD: No valid short leg | LongStrike={long_leg.strike} | "
                f"WidthRange=${config.SPREAD_WIDTH_MIN}-${config.SPREAD_WIDTH_MAX}"
            )
            self._set_spread_failure_cooldown(current_time)
            return None

        # V2.4.3: Sort by WIDTH proximity to target, then by delta as tiebreaker
        # Primary: closest to $5 target width
        # Secondary: prefer lower delta (more OTM = cheaper credit)
        short_candidates.sort(key=lambda x: (abs(x[1] - config.SPREAD_WIDTH_TARGET), x[2]))
        short_leg = short_candidates[0][0]
        actual_width = abs(short_leg.strike - long_leg.strike)

        self.log(
            f"SPREAD: Selected legs | Long={long_leg.strike} (delta={long_leg.delta:.2f}) | "
            f"Short={short_leg.strike} (delta={short_leg.delta:.2f}) | Width=${actual_width:.0f}"
        )

        return (long_leg, short_leg)

    # =========================================================================
    # V2.8: CREDIT SPREAD LEG SELECTION (VASS)
    # =========================================================================

    def select_credit_spread_legs(
        self,
        contracts: List[OptionContract],
        strategy: SpreadStrategy,
        dte_min: int,
        dte_max: int,
        current_time: Optional[str] = None,
    ) -> Optional[Tuple[OptionContract, OptionContract]]:
        """
        V2.8: Select legs for credit spread (sell short leg, buy long leg for protection).

        Credit spreads collect premium upfront and have defined max loss (width - credit).

        Bull Put Credit: Sell OTM put (higher strike), Buy further OTM put (lower strike)
            - Bullish bias: Profit if underlying stays above short strike
            - Max profit = credit received
            - Max loss = width - credit

        Bear Call Credit: Sell OTM call (lower strike), Buy further OTM call (higher strike)
            - Bearish bias: Profit if underlying stays below short strike
            - Max profit = credit received
            - Max loss = width - credit

        Args:
            contracts: List of available option contracts
            strategy: SpreadStrategy (BULL_PUT_CREDIT or BEAR_CALL_CREDIT)
            dte_min: Minimum days to expiration
            dte_max: Maximum days to expiration
            current_time: Current time string (optional, for logging)

        Returns:
            (short_leg, long_leg) tuple - NOTE: short leg is the one we SELL
            Returns None if no valid spread can be constructed
        """
        if not contracts:
            self.log("VASS: No contracts provided for credit spread selection")
            return None

        # Filter by DTE
        dte_filtered = [c for c in contracts if dte_min <= c.days_to_expiry <= dte_max]

        if not dte_filtered:
            self.log(
                f"VASS: No contracts in DTE range {dte_min}-{dte_max} "
                f"(available: {[c.days_to_expiry for c in contracts[:5]]}...)"
            )
            return None

        if strategy == SpreadStrategy.BULL_PUT_CREDIT:
            # BULL PUT CREDIT: Sell higher put, buy lower put
            # Profit if underlying stays ABOVE short strike
            puts = [c for c in dte_filtered if c.direction == OptionDirection.PUT]

            if not puts:
                self.log("VASS: No PUT contracts available for Bull Put Credit")
                return None

            # Short leg: Delta -0.25 to -0.40 (OTM but decent premium)
            # Must have sufficient bid (premium) to collect
            short_candidates = [
                p
                for p in puts
                if config.CREDIT_SPREAD_SHORT_LEG_DELTA_MIN
                <= abs(p.delta)
                <= config.CREDIT_SPREAD_SHORT_LEG_DELTA_MAX
                and p.bid >= config.CREDIT_SPREAD_MIN_CREDIT
            ]

            if not short_candidates:
                self.log(
                    f"VASS: No short put candidates | "
                    f"Delta range: {config.CREDIT_SPREAD_SHORT_LEG_DELTA_MIN}-"
                    f"{config.CREDIT_SPREAD_SHORT_LEG_DELTA_MAX} | "
                    f"Min credit: ${config.CREDIT_SPREAD_MIN_CREDIT}"
                )
                return None

            # Sort by premium (highest first) - we want max credit
            short_candidates.sort(key=lambda x: x.bid, reverse=True)
            short_leg = short_candidates[0]

            # Long leg: $5 below short strike (for defined risk)
            target_long_strike = short_leg.strike - config.CREDIT_SPREAD_WIDTH_TARGET
            long_candidates = [
                p for p in puts if p.strike == target_long_strike and p.expiry == short_leg.expiry
            ]

            if not long_candidates:
                # Fallback: closest strike below (within width range)
                long_candidates = [
                    p
                    for p in puts
                    if p.strike < short_leg.strike
                    and p.expiry == short_leg.expiry
                    and config.SPREAD_WIDTH_MIN
                    <= (short_leg.strike - p.strike)
                    <= config.SPREAD_WIDTH_MAX
                ]
                if long_candidates:
                    long_candidates.sort(key=lambda x: x.strike, reverse=True)

            if not long_candidates:
                self.log(
                    f"VASS: No long put candidates for protection | "
                    f"Short strike={short_leg.strike} | Target=${target_long_strike}"
                )
                return None

            long_leg = long_candidates[0]
            width = short_leg.strike - long_leg.strike
            credit = short_leg.bid - long_leg.ask  # Conservative: bid for sell, ask for buy

            self.log(
                f"VASS: BULL_PUT_CREDIT selected | "
                f"Sell {short_leg.strike}P @ ${short_leg.bid:.2f} | "
                f"Buy {long_leg.strike}P @ ${long_leg.ask:.2f} | "
                f"Width=${width:.0f} | Credit=${credit:.2f}"
            )

            return (short_leg, long_leg)

        elif strategy == SpreadStrategy.BEAR_CALL_CREDIT:
            # BEAR CALL CREDIT: Sell lower call, buy higher call
            # Profit if underlying stays BELOW short strike
            calls = [c for c in dte_filtered if c.direction == OptionDirection.CALL]

            if not calls:
                self.log("VASS: No CALL contracts available for Bear Call Credit")
                return None

            # Short leg: Delta 0.25-0.40 (OTM but decent premium)
            short_candidates = [
                c
                for c in calls
                if config.CREDIT_SPREAD_SHORT_LEG_DELTA_MIN
                <= abs(c.delta)
                <= config.CREDIT_SPREAD_SHORT_LEG_DELTA_MAX
                and c.bid >= config.CREDIT_SPREAD_MIN_CREDIT
            ]

            if not short_candidates:
                self.log(
                    f"VASS: No short call candidates | "
                    f"Delta range: {config.CREDIT_SPREAD_SHORT_LEG_DELTA_MIN}-"
                    f"{config.CREDIT_SPREAD_SHORT_LEG_DELTA_MAX} | "
                    f"Min credit: ${config.CREDIT_SPREAD_MIN_CREDIT}"
                )
                return None

            short_candidates.sort(key=lambda x: x.bid, reverse=True)
            short_leg = short_candidates[0]

            # Long leg: $5 above short strike (for defined risk)
            target_long_strike = short_leg.strike + config.CREDIT_SPREAD_WIDTH_TARGET
            long_candidates = [
                c for c in calls if c.strike == target_long_strike and c.expiry == short_leg.expiry
            ]

            if not long_candidates:
                # Fallback: closest strike above (within width range)
                long_candidates = [
                    c
                    for c in calls
                    if c.strike > short_leg.strike
                    and c.expiry == short_leg.expiry
                    and config.SPREAD_WIDTH_MIN
                    <= (c.strike - short_leg.strike)
                    <= config.SPREAD_WIDTH_MAX
                ]
                if long_candidates:
                    long_candidates.sort(key=lambda x: x.strike)

            if not long_candidates:
                self.log(
                    f"VASS: No long call candidates for protection | "
                    f"Short strike={short_leg.strike} | Target=${target_long_strike}"
                )
                return None

            long_leg = long_candidates[0]
            width = long_leg.strike - short_leg.strike
            credit = short_leg.bid - long_leg.ask

            self.log(
                f"VASS: BEAR_CALL_CREDIT selected | "
                f"Sell {short_leg.strike}C @ ${short_leg.bid:.2f} | "
                f"Buy {long_leg.strike}C @ ${long_leg.ask:.2f} | "
                f"Width=${width:.0f} | Credit=${credit:.2f}"
            )

            return (short_leg, long_leg)

        else:
            self.log(f"VASS: Strategy {strategy} is not a credit spread")
            return None

    # =========================================================================
    # ENTRY SIGNAL
    # =========================================================================

    def check_entry_signal(
        self,
        adx_value: float,
        current_price: float,
        ma200_value: float,
        iv_rank: float,
        best_contract: Optional[OptionContract],
        current_hour: int,
        current_minute: int,
        current_date: str,
        portfolio_value: float,
        regime_score: float = 50.0,
        gap_filter_triggered: bool = False,
        vol_shock_active: bool = False,
        time_guard_active: bool = False,
        size_multiplier: float = 1.0,
    ) -> Optional[TargetWeight]:
        """
        Check for options entry signal.

        V2.3.20: Added size_multiplier for cold start reduced sizing.

        Args:
            adx_value: Current ADX(14) value.
            current_price: Current QQQ price.
            ma200_value: 200-day moving average value.
            iv_rank: IV percentile (0-100).
            best_contract: Best available option contract.
            current_hour: Current hour (0-23) Eastern.
            current_minute: Current minute (0-59).
            current_date: Current date string.
            portfolio_value: Total portfolio value.
            regime_score: Market regime score (0-100). Must be >= 40.
            gap_filter_triggered: True if gap filter is active.
            vol_shock_active: True if vol shock pause is active.
            time_guard_active: True if time guard is active.
            size_multiplier: Position size multiplier (default 1.0). V2.3.20: Set to 0.5
                during cold start to reduce risk.

        Returns:
            TargetWeight for entry, or None if no signal.
        """
        # Check if already have a position
        if self._position is not None:
            return None

        # V2.3 FIX: Check if entry already attempted today (prevents order spam)
        if self._entry_attempted_today:
            return None

        # V2.9: Check trade limits (Bug #4 fix) - Uses comprehensive counter
        if not self._can_trade_options(OptionsMode.SWING):
            return None

        # GAP #1 FIX: Check regime score (must be >= 40 per V2.1 spec)
        if regime_score < 40:
            self.log(f"OPT: Entry blocked - regime score {regime_score:.1f} < 40 (RISK_OFF)")
            return None

        # Check safeguards
        if gap_filter_triggered:
            self.log("OPT: Entry blocked - gap filter active")
            return None

        if vol_shock_active:
            self.log("OPT: Entry blocked - vol shock active")
            return None

        if time_guard_active:
            self.log("OPT: Entry blocked - time guard active")
            return None

        # Check if we have a valid contract
        if best_contract is None:
            return None

        # GAP #3 FIX: Minimum premium validation ($0.50 per spec)
        if best_contract.mid_price < config.OPTIONS_MIN_PREMIUM:
            self.log(
                f"OPT: Entry blocked - premium ${best_contract.mid_price:.2f} < "
                f"min ${config.OPTIONS_MIN_PREMIUM:.2f}"
            )
            return None

        # Validate DTE range (1-4 days per spec)
        if best_contract.days_to_expiry < config.OPTIONS_DTE_MIN:
            self.log(
                f"OPT: Entry blocked - DTE {best_contract.days_to_expiry} < "
                f"min {config.OPTIONS_DTE_MIN}"
            )
            return None

        if best_contract.days_to_expiry > config.OPTIONS_DTE_MAX:
            self.log(
                f"OPT: Entry blocked - DTE {best_contract.days_to_expiry} > "
                f"max {config.OPTIONS_DTE_MAX}"
            )
            return None

        # V2.3.16: DTE-based delta validation
        # Swing mode (DTE > 5): Allows higher delta (0.70 target) for directional trades
        # Intraday mode (DTE <= 5): Narrower ATM range (0.40-0.60) for quick scalps
        contract_delta = abs(best_contract.delta)  # Use absolute value
        dte = best_contract.days_to_expiry
        is_swing_dte = dte > config.OPTIONS_SWING_DTE_THRESHOLD

        if is_swing_dte:
            # Swing mode: Use wider delta bounds (0.55-0.85)
            delta_min = config.OPTIONS_SWING_DELTA_MIN
            delta_max = config.OPTIONS_SWING_DELTA_MAX
            mode_label = "Swing"
        else:
            # V2.15: Strategy-aware delta bounds for intraday
            # Use defensive coding in case _state is not initialized (tests)
            state = getattr(self, "_state", None)
            current_strategy = getattr(state, "recommended_strategy", None) if state else None

            if current_strategy == IntradayStrategy.ITM_MOMENTUM:
                # ITM_MOMENTUM: Stock replacement needs ITM (0.60-0.85)
                delta_min = config.INTRADAY_ITM_DELTA_MIN
                delta_max = config.INTRADAY_ITM_DELTA_MAX
                mode_label = "Intraday-ITM"
            elif current_strategy == IntradayStrategy.DEBIT_FADE:
                # DEBIT_FADE: Mean reversion needs OTM (0.20-0.50)
                delta_min = config.INTRADAY_DEBIT_FADE_DELTA_MIN
                delta_max = config.INTRADAY_DEBIT_FADE_DELTA_MAX
                mode_label = "Intraday-FADE"
            else:
                # Default for other strategies (CREDIT_SPREAD, etc.)
                delta_min = config.OPTIONS_INTRADAY_DELTA_MIN
                delta_max = config.OPTIONS_INTRADAY_DELTA_MAX
                mode_label = "Intraday"

        if contract_delta < delta_min:
            self.log(
                f"OPT: Entry blocked - Delta {contract_delta:.2f} < "
                f"min {delta_min} ({mode_label} mode, DTE={dte})"
            )
            return None

        if contract_delta > delta_max:
            self.log(
                f"OPT: Entry blocked - Delta {contract_delta:.2f} > "
                f"max {delta_max} ({mode_label} mode, DTE={dte})"
            )
            return None

        # Calculate entry score
        entry_score = self.calculate_entry_score(
            adx_value=adx_value,
            current_price=current_price,
            ma200_value=ma200_value,
            iv_rank=iv_rank,
            bid_ask_spread_pct=best_contract.spread_pct,
            open_interest=best_contract.open_interest,
        )

        # Check minimum score
        if not entry_score.is_valid:
            return None

        # Late day constraint: only 20% stops after 2:30 PM
        is_late_day = current_hour > config.OPTIONS_LATE_DAY_HOUR or (
            current_hour == config.OPTIONS_LATE_DAY_HOUR
            and current_minute >= config.OPTIONS_LATE_DAY_MINUTE
        )

        if is_late_day:
            tier = self.get_stop_tier(entry_score.total)
            if tier["stop_pct"] > config.OPTIONS_LATE_DAY_MAX_STOP:
                self.log(
                    f"OPT: Entry blocked - late day (after 14:30), "
                    f"stop {tier['stop_pct']:.0%} > max {config.OPTIONS_LATE_DAY_MAX_STOP:.0%}"
                )
                return None

        # Calculate position size
        # V2.3.8: Pass DTE for 0DTE-specific tighter stops
        premium = best_contract.mid_price
        num_contracts, stop_pct, stop_price, target_price = self.calculate_position_size(
            entry_score=entry_score.total,
            premium=premium,
            portfolio_value=portfolio_value,
            days_to_expiry=best_contract.days_to_expiry,
        )

        # V2.3.20: Apply cold start size multiplier
        if size_multiplier < 1.0:
            num_contracts = max(1, int(num_contracts * size_multiplier))
            self.log(
                f"OPT: Cold start sizing - reduced to {num_contracts} contracts (×{size_multiplier})"
            )

        if num_contracts <= 0:
            self.log("OPT: Entry blocked - cannot calculate position size")
            return None

        # Store pending entry details for register_entry
        self._pending_contract = best_contract
        self._pending_entry_score = entry_score.total
        self._pending_num_contracts = num_contracts
        self._pending_stop_pct = stop_pct
        self._pending_stop_price = stop_price
        self._pending_target_price = target_price

        # V2.3 FIX: Mark that we attempted entry today (prevents retry spam)
        self._entry_attempted_today = True

        reason = (
            f"OPT Entry: Score={entry_score.total:.2f} "
            f"({entry_score.score_adx:.2f}+{entry_score.score_momentum:.2f}+"
            f"{entry_score.score_iv:.2f}+{entry_score.score_liquidity:.2f}), "
            f"{best_contract.direction.value} {best_contract.strike}, "
            f"x{num_contracts}, Stop={stop_pct:.0%}"
        )

        self.log(
            f"OPT: ENTRY_SIGNAL | {reason} | "
            f"Δ={best_contract.delta:.2f} DTE={best_contract.days_to_expiry} | "
            f"Premium=${premium:.2f} | Target=${target_price:.2f} | Stop=${stop_price:.2f}",
            trades_only=True,
        )

        # V2.4.1 FIX: Use actual allocation value, not 1.0
        # Was returning 1.0 instead of actual allocation (0.1875)
        return TargetWeight(
            symbol=best_contract.symbol,
            target_weight=config.OPTIONS_SWING_ALLOCATION,  # V2.4.1: Actual allocation
            source="OPT",
            urgency=Urgency.IMMEDIATE,
            reason=reason,
            requested_quantity=num_contracts,  # V2.3.2: Pass risk-calculated contracts
            metadata={"contract_price": best_contract.mid_price},  # V2.19: For router price lookup
        )

    # =========================================================================
    # V2.3 DEBIT SPREAD ENTRY SIGNAL
    # =========================================================================

    def check_spread_entry_signal(
        self,
        regime_score: float,
        vix_current: float,
        adx_value: float,
        current_price: float,
        ma200_value: float,
        iv_rank: float,
        current_hour: int,
        current_minute: int,
        current_date: str,
        portfolio_value: float,
        long_leg_contract: Optional[OptionContract] = None,
        short_leg_contract: Optional[OptionContract] = None,
        gap_filter_triggered: bool = False,
        vol_shock_active: bool = False,
        size_multiplier: float = 1.0,
        margin_remaining: Optional[float] = None,
    ) -> Optional[TargetWeight]:
        """
        V2.3: Check for debit spread entry signal.

        Debit Spreads have defined risk (max loss = net debit).
        Direction determined by regime score:
        - Regime > 60: Bull Call Spread
        - Regime < 45: Bear Put Spread
        - Regime 45-60: NO TRADE (neutral, no edge)
        - Regime < 30: No spread (protective puts only mode)

        Args:
            regime_score: Market regime score (0-100).
            vix_current: Current VIX level.
            adx_value: Current ADX(14) value.
            current_price: Current QQQ price.
            ma200_value: 200-day moving average value.
            iv_rank: IV percentile (0-100).
            current_hour: Current hour (0-23) Eastern.
            current_minute: Current minute (0-59).
            current_date: Current date string.
            portfolio_value: Total portfolio value.
            long_leg_contract: ATM contract for long leg.
            short_leg_contract: OTM contract for short leg.
            gap_filter_triggered: True if gap filter is active.
            vol_shock_active: True if vol shock pause is active.
            size_multiplier: Position size multiplier (default 1.0). V2.3.20: Set to 0.5
                during cold start to reduce risk.
            margin_remaining: Available margin from portfolio router. V2.21: Used for
                pre-submission margin estimation to prevent broker rejections.

        Returns:
            TargetWeight for spread entry (with short leg in metadata), or None.
        """
        # V2.8: Update IV sensor with current VIX (for smoothing)
        self._iv_sensor.update(vix_current)

        # Check if already have a spread position
        if self._spread_position is not None:
            return None

        # V2.3 FIX: Check if entry already attempted today
        if self._entry_attempted_today:
            return None

        # V2.6 Bug #16: Post-trade margin cooldown
        # After closing a spread, broker takes time to settle - wait before new entry
        if self._last_spread_exit_time is not None:
            try:
                from datetime import datetime

                exit_time = datetime.strptime(self._last_spread_exit_time[:19], "%Y-%m-%d %H:%M:%S")
                current_time_dt = datetime.strptime(current_date + " 12:00:00", "%Y-%m-%d %H:%M:%S")
                # Use current_hour/minute if available
                if current_hour is not None and current_minute is not None:
                    current_time_dt = current_time_dt.replace(
                        hour=current_hour, minute=current_minute
                    )

                elapsed_minutes = (current_time_dt - exit_time).total_seconds() / 60
                if elapsed_minutes < config.OPTIONS_POST_TRADE_COOLDOWN_MINUTES:
                    self.log(
                        f"SPREAD: Entry blocked - margin cooldown | "
                        f"Elapsed={elapsed_minutes:.1f}m < {config.OPTIONS_POST_TRADE_COOLDOWN_MINUTES}m"
                    )
                    return None
                else:
                    # Cooldown expired, clear the tracking
                    self._last_spread_exit_time = None
            except (ValueError, TypeError):
                # If parsing fails, clear the tracking and proceed
                self._last_spread_exit_time = None

        # V2.9: Check trade limits (Bug #4 fix) - Uses comprehensive counter
        if not self._can_trade_options(OptionsMode.SWING):
            return None

        # Determine spread direction based on regime
        if regime_score < config.SPREAD_REGIME_CRISIS:
            # Regime < 30: Crisis mode - no spreads, protective puts only
            self.log(
                f"SPREAD: No entry - regime {regime_score:.1f} < {config.SPREAD_REGIME_CRISIS} (crisis mode)"
            )
            return None

        if config.SPREAD_REGIME_BEARISH <= regime_score <= config.SPREAD_REGIME_BULLISH:
            # Neutral regime (45-60): NO TRADE
            self.log(
                f"SPREAD: No entry - regime {regime_score:.1f} is neutral "
                f"({config.SPREAD_REGIME_BEARISH}-{config.SPREAD_REGIME_BULLISH})"
            )
            return None

        # Determine spread type and direction
        if regime_score > config.SPREAD_REGIME_BULLISH:
            spread_type = "BULL_CALL"
            direction = OptionDirection.CALL
            vix_max = config.SPREAD_VIX_MAX_BULL
        else:  # regime_score < config.SPREAD_REGIME_BEARISH
            spread_type = "BEAR_PUT"
            direction = OptionDirection.PUT
            vix_max = config.SPREAD_VIX_MAX_BEAR

        # VIX filter
        if vix_current > vix_max:
            self.log(f"SPREAD: No entry - VIX {vix_current:.1f} > max {vix_max} for {spread_type}")
            return None

        # Check safeguards
        if gap_filter_triggered:
            self.log("SPREAD: Entry blocked - gap filter active")
            return None

        if vol_shock_active:
            self.log("SPREAD: Entry blocked - vol shock active")
            return None

        # Check time window (10:00 AM - 2:30 PM ET)
        time_minutes = current_hour * 60 + current_minute
        if not (10 * 60 <= time_minutes <= 14 * 60 + 30):
            if not self._swing_time_warning_logged:
                self.log("SPREAD: Entry blocked - outside time window (10:00-14:30)")
                self._swing_time_warning_logged = True
            return None

        # Validate contracts
        if long_leg_contract is None or short_leg_contract is None:
            self.log("SPREAD: Entry blocked - missing contract legs")
            return None

        # Validate contract directions match spread type
        if long_leg_contract.direction != direction:
            self.log(
                f"SPREAD: Entry blocked - long leg direction {long_leg_contract.direction.value} "
                f"doesn't match spread type {spread_type}"
            )
            return None

        if short_leg_contract.direction != direction:
            self.log(
                f"SPREAD: Entry blocked - short leg direction {short_leg_contract.direction.value} "
                f"doesn't match spread type {spread_type}"
            )
            return None

        # Validate DTE range (10-21 days per V2.3 spec)
        if long_leg_contract.days_to_expiry < config.SPREAD_DTE_MIN:
            self.log(
                f"SPREAD: Entry blocked - DTE {long_leg_contract.days_to_expiry} < "
                f"min {config.SPREAD_DTE_MIN}"
            )
            return None

        if long_leg_contract.days_to_expiry > config.SPREAD_DTE_MAX:
            self.log(
                f"SPREAD: Entry blocked - DTE {long_leg_contract.days_to_expiry} > "
                f"max {config.SPREAD_DTE_MAX}"
            )
            return None

        # V2.6 Bug #4: Validate short leg DTE matches long leg (within 1 day tolerance)
        dte_diff = abs(long_leg_contract.days_to_expiry - short_leg_contract.days_to_expiry)
        if dte_diff > 1:
            self.log(
                f"SPREAD: Entry blocked - DTE mismatch | "
                f"Long={long_leg_contract.days_to_expiry} Short={short_leg_contract.days_to_expiry} | "
                f"Diff={dte_diff} > 1 day"
            )
            return None

        # V2.6 Bug #9: Re-validate delta bounds before entry (delta can drift after selection)
        # This is a defensive check - legs were already filtered during selection
        long_delta_abs = abs(long_leg_contract.delta) if long_leg_contract.delta else 0
        short_delta_abs = abs(short_leg_contract.delta) if short_leg_contract.delta else 0

        if long_delta_abs < config.SPREAD_LONG_LEG_DELTA_MIN:
            self.log(
                f"SPREAD: Entry blocked - long leg delta drift | "
                f"Delta={long_delta_abs:.2f} < min {config.SPREAD_LONG_LEG_DELTA_MIN}"
            )
            return None

        if long_delta_abs > config.SPREAD_LONG_LEG_DELTA_MAX:
            self.log(
                f"SPREAD: Entry blocked - long leg delta drift | "
                f"Delta={long_delta_abs:.2f} > max {config.SPREAD_LONG_LEG_DELTA_MAX}"
            )
            return None

        # Short leg delta validation (only if not using width-based selection)
        if not config.SPREAD_SHORT_LEG_BY_WIDTH:
            if short_delta_abs < config.SPREAD_SHORT_LEG_DELTA_MIN:
                self.log(
                    f"SPREAD: Entry blocked - short leg delta drift | "
                    f"Delta={short_delta_abs:.2f} < min {config.SPREAD_SHORT_LEG_DELTA_MIN}"
                )
                return None

            if short_delta_abs > config.SPREAD_SHORT_LEG_DELTA_MAX:
                self.log(
                    f"SPREAD: Entry blocked - short leg delta drift | "
                    f"Delta={short_delta_abs:.2f} > max {config.SPREAD_SHORT_LEG_DELTA_MAX}"
                )
                return None

        # V2.3.8: Calculate spread width (for P/L calculation only, not filtering)
        # Removed width validation - delta drives selection now (PART 14 Pitfall 4)
        width = abs(short_leg_contract.strike - long_leg_contract.strike)

        # Calculate entry score
        entry_score = self.calculate_entry_score(
            adx_value=adx_value,
            current_price=current_price,
            ma200_value=ma200_value,
            iv_rank=iv_rank,
            bid_ask_spread_pct=long_leg_contract.spread_pct,
            open_interest=long_leg_contract.open_interest,
        )

        if not entry_score.is_valid:
            self.log(
                f"SPREAD: Entry blocked - score {entry_score.total:.2f} < "
                f"{config.OPTIONS_ENTRY_SCORE_MIN}"
            )
            return None

        # Calculate net debit and max profit
        # V2.14 Fix #22: Use conservative pricing (ASK/BID) to prevent tier cap violations
        # Evidence: Trade #20 sized at mid $2.75 but filled at $3.96 (44% slippage)
        # For debit spreads: BUY long (pay ASK), SELL short (receive BID)
        conservative_long = (
            long_leg_contract.ask if long_leg_contract.ask > 0 else long_leg_contract.mid_price
        )
        conservative_short = (
            short_leg_contract.bid if short_leg_contract.bid > 0 else short_leg_contract.mid_price
        )
        conservative_net_debit = conservative_long - conservative_short

        # Apply slippage buffer for worst-case sizing
        slippage_buffer = getattr(config, "SPREAD_SIZING_SLIPPAGE_BUFFER", 0.10)
        net_debit_for_sizing = conservative_net_debit * (1 + slippage_buffer)

        # Log conservative sizing calculation
        self.log(
            f"SPREAD_SIZE: Conservative=${net_debit_for_sizing:.2f} | "
            f"LongASK=${conservative_long:.2f} ShortBID=${conservative_short:.2f} | "
            f"Buffer={slippage_buffer:.0%}"
        )

        # Use mid price for max profit calculation (actual fill determines P&L)
        net_debit = long_leg_contract.mid_price - short_leg_contract.mid_price
        if net_debit <= 0:
            self.log(f"SPREAD: Entry blocked - net debit ${net_debit:.2f} <= 0")
            return None

        max_profit = width - net_debit
        if max_profit <= 0:
            self.log(f"SPREAD: Entry blocked - max profit ${max_profit:.2f} <= 0")
            return None

        # V2.18: Use hardcoded sizing cap (Fix for MarginBuyingPower sizing bug)
        # Evidence: Architect found $14K trade vs $5K expected when using allocation-based sizing
        # Solution: Absolute dollar cap of $7,500 for swing spreads
        swing_max_dollars = getattr(config, "SWING_SPREAD_MAX_DOLLARS", 7500)
        # V2.14: Use conservative net debit for sizing (prevents tier cap violations)
        cost_per_spread = net_debit_for_sizing * 100  # 100 shares per contract
        num_spreads = int(swing_max_dollars / cost_per_spread)
        self.log(
            f"SIZING: SWING | Cap=${swing_max_dollars} | Cost/spread=${cost_per_spread:.2f} | Qty={num_spreads}"
        )

        # V2.21 Layer 1: Pre-submission margin estimation
        # Scale num_spreads down to fit within available margin
        if margin_remaining is not None and margin_remaining > 0 and width > 0:
            safety_factor = getattr(config, "SPREAD_MARGIN_SAFETY_FACTOR", 0.80)
            usable_margin = margin_remaining * safety_factor

            # V2.21 Layer 2: Apply post-rejection cap if available
            if self._rejection_margin_cap is not None:
                usable_margin = min(usable_margin, self._rejection_margin_cap)
                self.log(
                    f"SIZING: Rejection cap active | Cap=${self._rejection_margin_cap:,.0f} | "
                    f"Usable=${usable_margin:,.0f}",
                    trades_only=True,
                )

            estimated_margin_per_spread = width * 100
            if estimated_margin_per_spread > 0:
                max_by_margin = int(usable_margin / estimated_margin_per_spread)
                if max_by_margin < num_spreads:
                    self.log(
                        f"SIZING: MARGIN_SCALE | {num_spreads} -> {max_by_margin} spreads | "
                        f"Margin=${margin_remaining:,.0f} x{safety_factor:.0%}=${usable_margin:,.0f} | "
                        f"Per-spread=${estimated_margin_per_spread:,.0f}",
                        trades_only=True,
                    )
                    num_spreads = max_by_margin

        # V2.21: Floor at MIN_SPREAD_CONTRACTS — skip without consuming daily attempt
        min_contracts = getattr(config, "MIN_SPREAD_CONTRACTS", 2)
        if 0 < num_spreads < min_contracts:
            self.log(
                f"SPREAD: Entry skipped — {num_spreads} < min {min_contracts} | "
                f"Insufficient margin for minimum position",
                trades_only=True,
            )
            return None  # Does NOT set _entry_attempted_today → retry preserved

        if num_spreads <= 0:
            self.log(
                f"SPREAD: Entry blocked - cap ${swing_max_dollars} too small "
                f"for debit ${net_debit:.2f}"
            )
            return None

        # V2.12 Fix #3: Enforce SPREAD_MAX_CONTRACTS hard cap
        # Evidence from V2.11: Position accumulated to 80 contracts (5× intended)
        # This cap prevents runaway position accumulation from exit signal bugs
        if num_spreads > config.SPREAD_MAX_CONTRACTS:
            self.log(
                f"SPREAD_LIMIT: Capped contracts | Requested={num_spreads} > Max={config.SPREAD_MAX_CONTRACTS}"
            )
            num_spreads = config.SPREAD_MAX_CONTRACTS

        # Store pending spread entry details
        self._pending_spread_long_leg = long_leg_contract
        self._pending_spread_short_leg = short_leg_contract
        self._pending_spread_type = spread_type
        self._pending_net_debit = net_debit
        self._pending_max_profit = max_profit
        self._pending_spread_width = width
        self._pending_num_contracts = num_spreads
        self._pending_entry_score = entry_score.total

        # Mark entry attempted
        self._entry_attempted_today = True

        reason = (
            f"{spread_type}: Regime={regime_score:.0f} | VIX={vix_current:.1f} | "
            f"Long={long_leg_contract.strike} Short={short_leg_contract.strike} | "
            f"Debit=${net_debit:.2f} MaxProfit=${max_profit:.2f} | x{num_spreads}"
        )

        self.log(
            f"SPREAD: ENTRY_SIGNAL | {reason} | "
            f"DTE={long_leg_contract.days_to_expiry} Score={entry_score.total:.2f}",
            trades_only=True,
        )

        # Return TargetWeight for long leg, with short leg info in metadata
        # V2.4.1 FIX: Use actual allocation value, not 1.0
        return TargetWeight(
            symbol=long_leg_contract.symbol,
            target_weight=config.OPTIONS_SWING_ALLOCATION,  # V2.4.1: Actual allocation
            source="OPT",
            urgency=Urgency.IMMEDIATE,
            reason=reason,
            requested_quantity=num_spreads,
            metadata={
                "spread_type": spread_type,
                "spread_short_leg_symbol": short_leg_contract.symbol,
                "spread_short_leg_quantity": num_spreads,
                "spread_net_debit": net_debit,
                "spread_max_profit": max_profit,
                "spread_width": width,
                # V2.8: VASS metadata
                "vass_iv_environment": self._iv_sensor.classify()
                if self._iv_sensor.is_ready()
                else "MEDIUM",
                "vass_smoothed_vix": self._iv_sensor.get_smoothed_vix(),
                "vass_strategy": SpreadStrategy.BULL_CALL_DEBIT.value
                if spread_type == "BULL_CALL"
                else SpreadStrategy.BEAR_PUT_DEBIT.value,
                # V2.19: Store prices for router lookup (_get_current_prices fix)
                "contract_price": long_leg_contract.mid_price,
                "short_leg_price": short_leg_contract.mid_price,
            },
        )

    # =========================================================================
    # V2.23 CREDIT SPREAD ENTRY SIGNAL
    # =========================================================================

    def check_credit_spread_entry_signal(
        self,
        regime_score: float,
        vix_current: float,
        adx_value: float,
        current_price: float,
        ma200_value: float,
        iv_rank: float,
        current_hour: int,
        current_minute: int,
        current_date: str,
        portfolio_value: float,
        short_leg_contract: Optional[OptionContract] = None,
        long_leg_contract: Optional[OptionContract] = None,
        strategy: Optional[SpreadStrategy] = None,
        gap_filter_triggered: bool = False,
        vol_shock_active: bool = False,
        size_multiplier: float = 1.0,
        margin_remaining: Optional[float] = None,
    ) -> Optional[TargetWeight]:
        """
        V2.23: Check for credit spread entry signal.

        Credit spreads collect premium upfront and profit from time decay.
        Selected by VASS when IV environment is HIGH (VIX > 25).

        Strategy Matrix:
        - HIGH IV + BULLISH → Bull Put Credit (sell OTM put, buy further OTM put)
        - HIGH IV + BEARISH → Bear Call Credit (sell OTM call, buy further OTM call)

        Sizing is based on MAX LOSS (width - credit), not premium received.
        This method mirrors check_spread_entry_signal() validation gates but
        uses _calculate_credit_spread_size() for margin-based sizing.

        Args:
            regime_score: Market regime score (0-100).
            vix_current: Current VIX level.
            adx_value: Current ADX(14) value.
            current_price: Current QQQ price.
            ma200_value: 200-day moving average value.
            iv_rank: IV percentile (0-100).
            current_hour: Current hour (0-23) Eastern.
            current_minute: Current minute (0-59).
            current_date: Current date string.
            portfolio_value: Total portfolio value.
            short_leg_contract: Contract we SELL (collect premium).
            long_leg_contract: Contract we BUY (protection).
            strategy: SpreadStrategy (BULL_PUT_CREDIT or BEAR_CALL_CREDIT).
            gap_filter_triggered: True if gap filter is active.
            vol_shock_active: True if vol shock pause is active.
            size_multiplier: Position size multiplier (default 1.0).
            margin_remaining: Available margin from portfolio router.

        Returns:
            TargetWeight for credit spread entry, or None.
        """
        # V2.8: Update IV sensor with current VIX (for smoothing)
        self._iv_sensor.update(vix_current)

        # Check if already have a spread position
        if self._spread_position is not None:
            return None

        # Check if entry already attempted today
        if self._entry_attempted_today:
            return None

        # Post-trade margin cooldown
        if self._last_spread_exit_time is not None:
            try:
                from datetime import datetime

                exit_time = datetime.strptime(self._last_spread_exit_time[:19], "%Y-%m-%d %H:%M:%S")
                current_time_dt = datetime.strptime(current_date + " 12:00:00", "%Y-%m-%d %H:%M:%S")
                if current_hour is not None and current_minute is not None:
                    current_time_dt = current_time_dt.replace(
                        hour=current_hour, minute=current_minute
                    )

                elapsed_minutes = (current_time_dt - exit_time).total_seconds() / 60
                if elapsed_minutes < config.OPTIONS_POST_TRADE_COOLDOWN_MINUTES:
                    self.log(
                        f"CREDIT_SPREAD: Entry blocked - margin cooldown | "
                        f"Elapsed={elapsed_minutes:.1f}m < {config.OPTIONS_POST_TRADE_COOLDOWN_MINUTES}m"
                    )
                    return None
                else:
                    self._last_spread_exit_time = None
            except (ValueError, TypeError):
                self._last_spread_exit_time = None

        # Check trade limits
        if not self._can_trade_options(OptionsMode.SWING):
            return None

        # Regime crisis check
        if regime_score < config.SPREAD_REGIME_CRISIS:
            self.log(
                f"CREDIT_SPREAD: No entry - regime {regime_score:.1f} < "
                f"{config.SPREAD_REGIME_CRISIS} (crisis mode)"
            )
            return None

        # Neutral regime check
        if config.SPREAD_REGIME_BEARISH <= regime_score <= config.SPREAD_REGIME_BULLISH:
            self.log(
                f"CREDIT_SPREAD: No entry - regime {regime_score:.1f} is neutral "
                f"({config.SPREAD_REGIME_BEARISH}-{config.SPREAD_REGIME_BULLISH})"
            )
            return None

        # Check safeguards
        if gap_filter_triggered:
            self.log("CREDIT_SPREAD: Entry blocked - gap filter active")
            return None

        if vol_shock_active:
            self.log("CREDIT_SPREAD: Entry blocked - vol shock active")
            return None

        # Check time window (10:00 AM - 2:30 PM ET)
        time_minutes = current_hour * 60 + current_minute
        if not (10 * 60 <= time_minutes <= 14 * 60 + 30):
            return None

        # Validate contracts
        if short_leg_contract is None or long_leg_contract is None:
            self.log("CREDIT_SPREAD: Entry blocked - missing contract legs")
            return None

        # Validate strategy
        if strategy is None or not self.is_credit_strategy(strategy):
            self.log(f"CREDIT_SPREAD: Entry blocked - invalid strategy {strategy}")
            return None

        # Determine spread type from strategy
        spread_type = strategy.value  # "BULL_PUT_CREDIT" or "BEAR_CALL_CREDIT"

        # Calculate width
        width = abs(short_leg_contract.strike - long_leg_contract.strike)
        if width <= 0:
            self.log(f"CREDIT_SPREAD: Entry blocked - invalid width {width}")
            return None

        # Calculate credit received (conservative: bid for sell, ask for buy)
        credit_received = short_leg_contract.bid - long_leg_contract.ask
        if credit_received < config.CREDIT_SPREAD_MIN_CREDIT:
            self.log(
                f"CREDIT_SPREAD: Entry blocked - credit ${credit_received:.2f} < "
                f"min ${config.CREDIT_SPREAD_MIN_CREDIT}"
            )
            return None

        # Calculate entry score (same scoring as debit)
        entry_score = self.calculate_entry_score(
            adx_value=adx_value,
            current_price=current_price,
            ma200_value=ma200_value,
            iv_rank=iv_rank,
            bid_ask_spread_pct=short_leg_contract.spread_pct,
            open_interest=short_leg_contract.open_interest,
        )

        if not entry_score.is_valid:
            self.log(
                f"CREDIT_SPREAD: Entry blocked - score {entry_score.total:.2f} < "
                f"{config.OPTIONS_ENTRY_SCORE_MIN}"
            )
            return None

        # Size using margin-based calculator
        swing_max_dollars = getattr(config, "SWING_SPREAD_MAX_DOLLARS", 7500)
        num_spreads, _credit_per, _max_loss_per, _total_margin = self._calculate_credit_spread_size(
            short_leg_contract, long_leg_contract, swing_max_dollars
        )

        if num_spreads <= 0:
            return None

        # V2.3.20: Apply cold start size multiplier
        if size_multiplier < 1.0:
            num_spreads = max(1, int(num_spreads * size_multiplier))
            self.log(
                f"CREDIT_SPREAD: Cold start sizing - reduced to {num_spreads} spreads "
                f"(x{size_multiplier})"
            )

        # V2.21: Pre-submission margin estimation
        if margin_remaining is not None and margin_remaining > 0 and width > 0:
            safety_factor = getattr(config, "SPREAD_MARGIN_SAFETY_FACTOR", 0.80)
            usable_margin = margin_remaining * safety_factor

            if self._rejection_margin_cap is not None:
                usable_margin = min(usable_margin, self._rejection_margin_cap)
                self.log(
                    f"CREDIT_SIZING: Rejection cap active | Cap=${self._rejection_margin_cap:,.0f} | "
                    f"Usable=${usable_margin:,.0f}",
                    trades_only=True,
                )

            estimated_margin_per_spread = width * 100
            if estimated_margin_per_spread > 0:
                max_by_margin = int(usable_margin / estimated_margin_per_spread)
                if max_by_margin < num_spreads:
                    self.log(
                        f"CREDIT_SIZING: MARGIN_SCALE | {num_spreads} -> {max_by_margin} spreads | "
                        f"Margin=${margin_remaining:,.0f} x{safety_factor:.0%}=${usable_margin:,.0f} | "
                        f"Per-spread=${estimated_margin_per_spread:,.0f}",
                        trades_only=True,
                    )
                    num_spreads = max_by_margin

        # V2.21: Floor at MIN_SPREAD_CONTRACTS
        min_contracts = getattr(config, "MIN_SPREAD_CONTRACTS", 2)
        if 0 < num_spreads < min_contracts:
            self.log(
                f"CREDIT_SPREAD: Entry skipped - {num_spreads} < min {min_contracts} | "
                f"Insufficient margin for minimum position",
                trades_only=True,
            )
            return None  # Does NOT set _entry_attempted_today → retry preserved

        if num_spreads <= 0:
            self.log(
                f"CREDIT_SPREAD: Entry blocked - cannot size position | "
                f"Width=${width:.2f} Credit=${credit_received:.2f}"
            )
            return None

        # Enforce hard cap
        if num_spreads > config.SPREAD_MAX_CONTRACTS:
            self.log(
                f"CREDIT_SPREAD_LIMIT: Capped | Requested={num_spreads} > "
                f"Max={config.SPREAD_MAX_CONTRACTS}"
            )
            num_spreads = config.SPREAD_MAX_CONTRACTS

        # Calculate max profit and max loss for metadata
        max_profit = credit_received * 100 * num_spreads  # Credit × 100 × contracts
        max_loss = (width - credit_received) * 100 * num_spreads

        # Store pending spread entry details
        # NOTE: For credit spreads, the "long leg" in our naming convention is the
        # protection leg (cheaper), and "short leg" is the one we sell (more expensive).
        # We store them matching the debit convention for register_spread_entry compatibility.
        self._pending_spread_long_leg = long_leg_contract
        self._pending_spread_short_leg = short_leg_contract
        self._pending_spread_type = spread_type
        self._pending_net_debit = -credit_received  # Negative = credit received
        self._pending_max_profit = credit_received  # Max profit per spread = credit
        self._pending_spread_width = width
        self._pending_num_contracts = num_spreads
        self._pending_entry_score = entry_score.total

        # Mark entry attempted
        self._entry_attempted_today = True

        reason = (
            f"{spread_type}: Regime={regime_score:.0f} | VIX={vix_current:.1f} | "
            f"Sell {short_leg_contract.strike} Buy {long_leg_contract.strike} | "
            f"Credit=${credit_received:.2f} Width=${width:.0f} | x{num_spreads}"
        )

        self.log(
            f"CREDIT_SPREAD: ENTRY_SIGNAL | {reason} | "
            f"DTE={short_leg_contract.days_to_expiry} Score={entry_score.total:.2f} | "
            f"MaxProfit=${max_profit:.0f} MaxLoss=${max_loss:.0f}",
            trades_only=True,
        )

        # Return TargetWeight — use short leg as primary symbol (we sell it)
        # Long leg (protection) goes in metadata
        return TargetWeight(
            symbol=short_leg_contract.symbol,
            target_weight=config.OPTIONS_SWING_ALLOCATION,
            source="OPT",
            urgency=Urgency.IMMEDIATE,
            reason=reason,
            requested_quantity=-num_spreads,  # Negative = SELL
            metadata={
                "spread_type": spread_type,
                "spread_short_leg_symbol": short_leg_contract.symbol,
                "spread_short_leg_quantity": num_spreads,
                "spread_long_leg_symbol": long_leg_contract.symbol,
                "spread_net_debit": -credit_received,  # Negative = credit
                "spread_credit_received": credit_received,
                "spread_max_profit": credit_received,
                "spread_max_loss_per_spread": width - credit_received,
                "spread_width": width,
                "is_credit_spread": True,
                # VASS metadata
                "vass_iv_environment": self._iv_sensor.classify()
                if self._iv_sensor.is_ready()
                else "HIGH",
                "vass_smoothed_vix": self._iv_sensor.get_smoothed_vix(),
                "vass_strategy": strategy.value,
                # Prices for router lookup
                "contract_price": short_leg_contract.mid_price,
                "short_leg_price": long_leg_contract.mid_price,
            },
        )

    # =========================================================================
    # V2.3 SPREAD EXIT SIGNALS
    # =========================================================================

    def check_spread_exit_signals(
        self,
        long_leg_price: float,
        short_leg_price: float,
        regime_score: float,
        current_dte: int,
    ) -> Optional[List[TargetWeight]]:
        """
        V2.3: Check for spread exit signals.

        Exit conditions:
        1. Take profit at 50% of max profit
        2. Stop loss at 50% of entry debit
        3. Close by 5 DTE (avoid gamma acceleration)
        4. V2.22 Neutrality exit (regime in dead zone 45-60 with flat P&L)
        5. Regime reversal (Bull exit if < 45, Bear exit if > 60)

        Args:
            long_leg_price: Current price of long leg.
            short_leg_price: Current price of short leg.
            regime_score: Current regime score.
            current_dte: Current days to expiration.

        Returns:
            List of TargetWeights for both legs exit, or None.
        """
        if self._spread_position is None:
            return None

        spread = self._spread_position

        # V2.12 Fix #1: Must have contracts to exit
        if spread.num_spreads <= 0:
            self.log(
                f"SPREAD_EXIT_SKIP: No contracts to exit | num_spreads={spread.num_spreads}",
                trades_only=True,
            )
            return None

        # V2.12 Fix #2: Don't fire duplicate exit signals while closing
        if spread.is_closing:
            return None

        # V2.8: Determine if credit or debit spread
        is_credit_spread = spread.spread_type in (
            "BULL_PUT_CREDIT",
            "BEAR_CALL_CREDIT",
            SpreadStrategy.BULL_PUT_CREDIT.value,
            SpreadStrategy.BEAR_CALL_CREDIT.value,
        )

        exit_reason = None

        if is_credit_spread:
            # CREDIT SPREAD P&L: Profit when spread value DECREASES
            # Entry: Received credit (stored as negative net_debit)
            # Current: Cost to buy back spread (short - long)
            current_spread_value = short_leg_price - long_leg_price  # Cost to close
            entry_credit = abs(spread.net_debit)  # Credit received (stored as negative)

            # Profit = credit_received - current_spread_cost
            pnl = entry_credit - current_spread_value
            pnl_pct = pnl / spread.max_profit if spread.max_profit > 0 else 0

            # Exit 1: Credit Profit Target (50% of max profit)
            profit_target = spread.max_profit * config.CREDIT_SPREAD_PROFIT_TARGET
            if pnl >= profit_target:
                exit_reason = (
                    f"CREDIT_PROFIT_TARGET +{pnl_pct:.1%} "
                    f"(P&L ${pnl:.2f} >= ${profit_target:.2f})"
                )

            # Exit 2: Credit Stop Loss (spread value exceeds max loss threshold)
            # Max loss = width - credit received
            max_loss = spread.width - entry_credit
            if (
                exit_reason is None
                and current_spread_value >= max_loss * config.CREDIT_SPREAD_STOP_MULTIPLIER
            ):
                loss_pct = (current_spread_value - entry_credit) / max_loss if max_loss > 0 else 0
                exit_reason = (
                    f"CREDIT_STOP_LOSS {loss_pct:.1%} "
                    f"(spread value ${current_spread_value:.2f} >= ${max_loss * config.CREDIT_SPREAD_STOP_MULTIPLIER:.2f})"
                )

            # Exit 3: DTE exit (close by 5 DTE)
            if exit_reason is None and current_dte <= config.SPREAD_DTE_EXIT:
                exit_reason = f"DTE_EXIT ({current_dte} DTE <= {config.SPREAD_DTE_EXIT})"

            # Exit 4: V2.22 Neutrality Exit (Hysteresis Shield)
            # Close flat spreads in dead zone — directional bet with no direction
            if (
                exit_reason is None
                and getattr(config, "SPREAD_NEUTRALITY_EXIT_ENABLED", True)
                and config.SPREAD_REGIME_EXIT_BULL <= regime_score <= config.SPREAD_REGIME_EXIT_BEAR
            ):
                neutrality_band = getattr(config, "SPREAD_NEUTRALITY_EXIT_PNL_BAND", 0.10)
                if -neutrality_band <= pnl_pct <= neutrality_band:
                    exit_reason = (
                        f"NEUTRALITY_EXIT: Score {regime_score:.0f} in dead zone "
                        f"({config.SPREAD_REGIME_EXIT_BULL}-{config.SPREAD_REGIME_EXIT_BEAR}) "
                        f"with flat P&L ({pnl_pct:+.1%})"
                    )

            # Exit 5: Credit Regime reversal
            if exit_reason is None:
                if spread.spread_type in ("BULL_PUT_CREDIT", SpreadStrategy.BULL_PUT_CREDIT.value):
                    if regime_score < config.SPREAD_REGIME_EXIT_BULL:
                        exit_reason = f"REGIME_REVERSAL (Bull Put exit: {regime_score:.0f} < {config.SPREAD_REGIME_EXIT_BULL})"
                elif spread.spread_type in (
                    "BEAR_CALL_CREDIT",
                    SpreadStrategy.BEAR_CALL_CREDIT.value,
                ):
                    if regime_score > config.SPREAD_REGIME_EXIT_BEAR:
                        exit_reason = f"REGIME_REVERSAL (Bear Call exit: {regime_score:.0f} > {config.SPREAD_REGIME_EXIT_BEAR})"

        else:
            # DEBIT SPREAD P&L: Original logic
            current_spread_value = long_leg_price - short_leg_price
            entry_debit = spread.net_debit
            pnl = current_spread_value - entry_debit
            pnl_pct = pnl / entry_debit if entry_debit > 0 else 0

            # Exit 1: Profit target (50% of max profit)
            # V2.16-BT: Commission-aware profit target
            # Require NET profit (after commission) to meet the target, not just gross
            commission_cost = spread.num_spreads * config.SPREAD_COMMISSION_PER_CONTRACT
            raw_profit_target = spread.max_profit * config.SPREAD_PROFIT_TARGET_PCT
            # Gross P&L needed = raw_target + commission (ensures net profit meets target)
            profit_target = raw_profit_target + commission_cost
            net_pnl = pnl - commission_cost
            if pnl >= profit_target:
                exit_reason = (
                    f"PROFIT_TARGET +{pnl_pct:.1%} (Net ${net_pnl:.2f} >= ${raw_profit_target:.2f}) | "
                    f"Gross ${pnl:.2f} - Commission ${commission_cost:.2f}"
                )

            # Exit 2: STOP LOSS (V2.4.2 FIX: Max loss = 50% of entry debit)
            # This prevents catastrophic losses from holding spreads to expiration
            # Example: $4 debit spread exits if value drops to $2 (50% loss)
            elif pnl_pct < -config.SPREAD_STOP_LOSS_PCT:
                exit_reason = (
                    f"STOP_LOSS {pnl_pct:.1%} (lost > {config.SPREAD_STOP_LOSS_PCT:.0%} of entry)"
                )

            # Exit 3: DTE exit (close by 5 DTE)
            elif current_dte <= config.SPREAD_DTE_EXIT:
                exit_reason = f"DTE_EXIT ({current_dte} DTE <= {config.SPREAD_DTE_EXIT})"

            # Exit 4: V2.22 Neutrality Exit (Hysteresis Shield)
            # Close flat spreads in dead zone — directional bet with no direction
            elif (
                getattr(config, "SPREAD_NEUTRALITY_EXIT_ENABLED", True)
                and config.SPREAD_REGIME_EXIT_BULL <= regime_score <= config.SPREAD_REGIME_EXIT_BEAR
            ):
                neutrality_band = getattr(config, "SPREAD_NEUTRALITY_EXIT_PNL_BAND", 0.10)
                if -neutrality_band <= pnl_pct <= neutrality_band:
                    exit_reason = (
                        f"NEUTRALITY_EXIT: Score {regime_score:.0f} in dead zone "
                        f"({config.SPREAD_REGIME_EXIT_BULL}-{config.SPREAD_REGIME_EXIT_BEAR}) "
                        f"with flat P&L ({pnl_pct:+.1%})"
                    )

            # Exit 5: Regime reversal
            elif (
                spread.spread_type == "BULL_CALL" and regime_score < config.SPREAD_REGIME_EXIT_BULL
            ):
                exit_reason = f"REGIME_REVERSAL (Bull exit: {regime_score:.0f} < {config.SPREAD_REGIME_EXIT_BULL})"
            elif spread.spread_type == "BEAR_PUT" and regime_score > config.SPREAD_REGIME_EXIT_BEAR:
                exit_reason = f"REGIME_REVERSAL (Bear exit: {regime_score:.0f} > {config.SPREAD_REGIME_EXIT_BEAR})"

        if exit_reason is None:
            return None

        self.log(
            f"SPREAD: EXIT_SIGNAL | {exit_reason} | "
            f"Long=${long_leg_price:.2f} Short=${short_leg_price:.2f} | "
            f"P&L={pnl_pct:.1%}",
            trades_only=True,
        )

        # V2.12 Fix #2: Lock the position to prevent duplicate exit signals
        spread.is_closing = True

        # V2.5 FIX: Return SINGLE exit signal with combo metadata
        # (Same structure as entry, so router creates atomic ComboMarketOrder)
        # Previously returned TWO signals which executed as separate orders!
        return [
            TargetWeight(
                symbol=spread.long_leg.symbol,
                target_weight=0.0,
                source="OPT",
                urgency=Urgency.IMMEDIATE,
                reason=f"SPREAD_EXIT: {exit_reason}",
                requested_quantity=spread.num_spreads,
                metadata={
                    "spread_close_short": True,  # Tells router this is an exit
                    "spread_short_leg_symbol": spread.short_leg.symbol,
                    "spread_short_leg_quantity": spread.num_spreads,
                },
            ),
        ]

    # =========================================================================
    # V2.4.1 FRIDAY FIREWALL
    # =========================================================================

    def check_friday_firewall_exit(
        self,
        current_vix: float,
        current_date: str,
        vix_close_all_threshold: float = 25.0,
        vix_keep_fresh_threshold: float = 15.0,
    ) -> Optional[List[TargetWeight]]:
        """
        V2.4.1: Friday Firewall - close swing options before weekend.

        Safety-first approach to weekend risk management:
        1. VIX > 25: Close ALL swing options (high volatility = high gap risk)
        2. Fresh trade (opened today) AND VIX >= 15: Close it (gambling protection)
        3. Fresh trade AND VIX < 15: Keep it (calm market exception)
        4. Older trades AND VIX <= 25: Keep them (already survived initial risk)

        Args:
            current_vix: Current VIX level.
            current_date: Current date string (YYYY-MM-DD format).
            vix_close_all_threshold: VIX level above which ALL positions close (default 25).
            vix_keep_fresh_threshold: VIX level below which fresh trades can stay (default 15).

        Returns:
            List of TargetWeights for positions to close, or None if no action needed.
        """
        exit_signals = []

        # Check spread position
        if self._spread_position is not None:
            spread = self._spread_position
            entry_date = (
                spread.entry_time.split()[0] if " " in spread.entry_time else spread.entry_time[:10]
            )
            is_fresh_trade = entry_date == current_date

            should_close = False
            close_reason = ""

            # Rule 1: VIX > threshold - close ALL
            if current_vix > vix_close_all_threshold:
                should_close = True
                close_reason = f"VIX_HIGH ({current_vix:.1f} > {vix_close_all_threshold})"

            # Rule 2: Fresh trade + VIX >= 15 - close (gambling protection)
            elif is_fresh_trade and current_vix >= vix_keep_fresh_threshold:
                should_close = True
                close_reason = (
                    f"FRESH_TRADE_PROTECTION (VIX={current_vix:.1f} >= {vix_keep_fresh_threshold})"
                )

            # Rule 3: Fresh trade + VIX < 15 - keep (calm market)
            elif is_fresh_trade and current_vix < vix_keep_fresh_threshold:
                self.log(
                    f"FRIDAY_FIREWALL: Keeping fresh spread (calm market) | "
                    f"VIX={current_vix:.1f} < {vix_keep_fresh_threshold}"
                )

            # Rule 4: Older trade + VIX <= 25 - keep
            else:
                self.log(
                    f"FRIDAY_FIREWALL: Keeping spread (established trade) | "
                    f"Entry={entry_date} | VIX={current_vix:.1f}"
                )

            if should_close:
                self.log(
                    f"FRIDAY_FIREWALL: Closing spread | {close_reason} | "
                    f"Entry={entry_date} Fresh={is_fresh_trade}",
                    trades_only=True,
                )
                # V2.5 FIX: Close both legs via COMBO order (atomic execution)
                exit_signals.append(
                    TargetWeight(
                        symbol=spread.long_leg.symbol,
                        target_weight=0.0,
                        source="OPT",
                        urgency=Urgency.IMMEDIATE,
                        reason=f"FRIDAY_FIREWALL: {close_reason}",
                        requested_quantity=spread.num_spreads,
                        metadata={
                            "spread_close_short": True,
                            "spread_short_leg_symbol": spread.short_leg.symbol,
                            "spread_short_leg_quantity": spread.num_spreads,
                        },
                    )
                )

        # Check single-leg position
        if self._position is not None:
            position = self._position
            entry_date = (
                position.entry_time.split()[0]
                if " " in position.entry_time
                else position.entry_time[:10]
            )
            is_fresh_trade = entry_date == current_date

            should_close = False
            close_reason = ""

            # Rule 1: VIX > threshold - close ALL
            if current_vix > vix_close_all_threshold:
                should_close = True
                close_reason = f"VIX_HIGH ({current_vix:.1f} > {vix_close_all_threshold})"

            # Rule 2: Fresh trade + VIX >= 15 - close
            elif is_fresh_trade and current_vix >= vix_keep_fresh_threshold:
                should_close = True
                close_reason = (
                    f"FRESH_TRADE_PROTECTION (VIX={current_vix:.1f} >= {vix_keep_fresh_threshold})"
                )

            # Rule 3 & 4: Keep if calm or established
            elif is_fresh_trade and current_vix < vix_keep_fresh_threshold:
                self.log(
                    f"FRIDAY_FIREWALL: Keeping fresh single-leg (calm market) | "
                    f"VIX={current_vix:.1f} < {vix_keep_fresh_threshold}"
                )
            else:
                self.log(
                    f"FRIDAY_FIREWALL: Keeping single-leg (established trade) | "
                    f"Entry={entry_date} | VIX={current_vix:.1f}"
                )

            if should_close:
                self.log(
                    f"FRIDAY_FIREWALL: Closing single-leg | {close_reason} | "
                    f"Entry={entry_date} Fresh={is_fresh_trade}",
                    trades_only=True,
                )
                exit_signals.append(
                    TargetWeight(
                        symbol=position.contract.symbol,
                        target_weight=0.0,
                        source="OPT",
                        urgency=Urgency.IMMEDIATE,
                        reason=f"FRIDAY_FIREWALL: {close_reason}",
                    )
                )

        return exit_signals if exit_signals else None

    # =========================================================================
    # EXIT SIGNALS
    # =========================================================================

    def check_exit_signals(
        self,
        current_price: float,
        current_dte: Optional[int] = None,
    ) -> Optional[TargetWeight]:
        """
        Check for options exit signals.

        V2.3.10: Added DTE exit to prevent options being held to expiration.

        Args:
            current_price: Current option price.
            current_dte: Optional current days to expiration.

        Returns:
            TargetWeight for exit, or None if no exit signal.
        """
        if self._position is None:
            return None

        symbol = self._position.contract.symbol
        entry_price = self._position.entry_price

        # Calculate P&L percentage
        pnl_pct = (current_price - entry_price) / entry_price

        # Exit 1: Profit target hit (+50%)
        if current_price >= self._position.target_price:
            reason = f"TARGET_HIT +{pnl_pct:.1%} (Price: ${current_price:.2f})"
            self.log(f"OPT: EXIT_SIGNAL {symbol} | {reason}", trades_only=True)
            return TargetWeight(
                symbol=symbol,
                target_weight=0.0,
                source="OPT",
                urgency=Urgency.IMMEDIATE,
                reason=reason,
            )

        # Exit 2: Stop hit
        if current_price <= self._position.stop_price:
            reason = f"STOP_HIT {pnl_pct:.1%} (Price: ${current_price:.2f})"
            self.log(f"OPT: EXIT_SIGNAL {symbol} | {reason}", trades_only=True)
            return TargetWeight(
                symbol=symbol,
                target_weight=0.0,
                source="OPT",
                urgency=Urgency.IMMEDIATE,
                reason=reason,
            )

        # V2.3.10: Exit 3 - DTE exit (prevent expiration/exercise)
        # Close single-leg options before expiration to avoid:
        # - OTM expiring worthless (100% loss)
        # - ITM being auto-exercised (creates stock position, margin crisis)
        if current_dte is not None and current_dte <= config.OPTIONS_SINGLE_LEG_DTE_EXIT:
            reason = f"DTE_EXIT ({current_dte} DTE <= {config.OPTIONS_SINGLE_LEG_DTE_EXIT}) P&L={pnl_pct:.1%}"
            self.log(f"OPT: EXIT_SIGNAL {symbol} | {reason}", trades_only=True)
            return TargetWeight(
                symbol=symbol,
                target_weight=0.0,
                source="OPT",
                urgency=Urgency.IMMEDIATE,
                reason=reason,
            )

        return None

    def check_force_exit(
        self,
        current_hour: int,
        current_minute: int,
        current_price: float,
    ) -> Optional[TargetWeight]:
        """
        Check for forced exit at 3:45 PM ET.

        Per V2.1 spec, options positions must be closed by 3:45 PM
        to avoid overnight theta decay and regulatory risk.

        Args:
            current_hour: Current hour (0-23) Eastern.
            current_minute: Current minute (0-59).
            current_price: Current option price.

        Returns:
            TargetWeight for forced exit, or None if no position or not time yet.
        """
        if self._position is None:
            return None

        # Check if it's force exit time (15:45 ET)
        force_exit_time = current_hour > config.OPTIONS_FORCE_EXIT_HOUR or (
            current_hour == config.OPTIONS_FORCE_EXIT_HOUR
            and current_minute >= config.OPTIONS_FORCE_EXIT_MINUTE
        )

        if not force_exit_time:
            return None

        symbol = self._position.contract.symbol
        entry_price = self._position.entry_price

        # Calculate P&L percentage
        pnl_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0

        reason = f"TIME_EXIT_1545 {pnl_pct:+.1%} (Price: ${current_price:.2f})"
        self.log(f"OPT: FORCE_EXIT {symbol} | {reason}", trades_only=True)

        return TargetWeight(
            symbol=symbol,
            target_weight=0.0,
            source="OPT",
            urgency=Urgency.IMMEDIATE,
            reason=reason,
        )

    # =========================================================================
    # V2.1.1 DUAL-MODE ARCHITECTURE
    # =========================================================================

    def determine_mode(self, dte: int) -> OptionsMode:
        """
        Determine operating mode based on DTE.

        Critical insight: Entry timing matters more for shorter DTE.
        - 2 DTE: 2-hour window = 15% of option's life → Micro Regime ESSENTIAL
        - 14 DTE: 2-hour window = 2% of option's life → Simple filters sufficient

        Args:
            dte: Days to expiration.

        Returns:
            OptionsMode.SWING or OptionsMode.INTRADAY.
        """
        if dte <= config.OPTIONS_INTRADAY_DTE_MAX:
            return OptionsMode.INTRADAY
        return OptionsMode.SWING

    def _apply_tiered_dollar_cap(self, raw_allocation: float, tradeable_equity: float) -> float:
        """
        V2.7: Apply tiered dollar cap to options allocation.

        Prevents oversizing on small accounts while allowing growth on larger accounts.
        Uses min(percentage_allocation, dollar_cap) logic.

        Tier 1 (<$60K): Cap at $5,000 per spread
        Tier 2 ($60K-$100K): Cap at $10,000 per spread
        Tier 3 (>$100K): No cap, use raw percentage

        Args:
            raw_allocation: Percentage-based allocation in dollars.
            tradeable_equity: Current tradeable equity (settled cash - lockbox).

        Returns:
            Capped allocation amount.
        """
        if tradeable_equity < config.OPTIONS_DOLLAR_CAP_TIER_1_THRESHOLD:
            # Tier 1: Small account protection
            capped = min(raw_allocation, config.OPTIONS_DOLLAR_CAP_TIER_1)
            if capped < raw_allocation:
                self.log(
                    f"SPREAD: Allocation capped at ${capped:,.0f} (Tier 1) | "
                    f"Raw=${raw_allocation:,.0f} | Equity=${tradeable_equity:,.0f}"
                )
            return capped
        elif tradeable_equity < config.OPTIONS_DOLLAR_CAP_TIER_2_THRESHOLD:
            # Tier 2: Medium account
            capped = min(raw_allocation, config.OPTIONS_DOLLAR_CAP_TIER_2)
            if capped < raw_allocation:
                self.log(
                    f"SPREAD: Allocation capped at ${capped:,.0f} (Tier 2) | "
                    f"Raw=${raw_allocation:,.0f} | Equity=${tradeable_equity:,.0f}"
                )
            return capped
        else:
            # Tier 3: Large account - percentage-based only
            return raw_allocation

    def _calculate_credit_spread_size(
        self,
        short_leg: OptionContract,
        long_leg: OptionContract,
        allocation: float,
    ) -> Tuple[int, float, float, float]:
        """
        V2.8 SAFETY: Calculate credit spread size based on MARGIN REQUIREMENT.

        CRITICAL: Size based on MAX LOSS, not premium received!
        Sizing on premium would create positions that exceed risk limits.

        Example:
            Width = $5.00, Credit = $0.50
            WRONG: $5,000 / $50 = 100 contracts (DANGEROUS - $50K risk!)
            CORRECT: $5,000 / $450 = 11 contracts (defined $4,950 max loss)

        Args:
            short_leg: Contract we SELL (collect premium)
            long_leg: Contract we BUY (protection)
            allocation: Maximum dollar amount to risk ($5K cap)

        Returns:
            Tuple of (num_spreads, credit_per_spread, max_loss_per_spread, total_margin)
            Returns (0, 0, 0, 0) if invalid spread
        """
        # Calculate width (always positive)
        if short_leg.direction == OptionDirection.PUT:
            # Bull Put: short strike > long strike
            width = short_leg.strike - long_leg.strike
        else:
            # Bear Call: long strike > short strike
            width = long_leg.strike - short_leg.strike

        if width <= 0:
            self.log(f"SAFETY: Invalid spread width: {width}")
            return (0, 0.0, 0.0, 0.0)

        # Credit = what we receive (conservative: bid for sell, ask for buy)
        credit_received = short_leg.bid - long_leg.ask

        # SAFETY CHECK: Validate credit is positive
        if credit_received <= 0:
            self.log(
                f"SAFETY: Invalid credit spread - no positive credit | "
                f"Short bid={short_leg.bid} | Long ask={long_leg.ask}"
            )
            return (0, 0.0, 0.0, 0.0)

        # MARGIN REQUIREMENT = (Width - Credit) × 100
        # This is the MAX LOSS per spread
        margin_per_spread = (width - credit_received) * 100

        # SAFETY CHECK: Margin must be positive
        if margin_per_spread <= 0:
            self.log(
                f"SAFETY: Invalid margin calculation | "
                f"Width={width} | Credit={credit_received} | Margin={margin_per_spread}"
            )
            return (0, 0.0, 0.0, 0.0)

        # SIZE BASED ON MARGIN, NOT PREMIUM
        # $5,000 / $450 margin = 11 contracts max
        max_contracts = int(allocation / margin_per_spread)

        if max_contracts <= 0:
            self.log(
                f"SAFETY: Allocation too small for even 1 spread | "
                f"Allocation=${allocation:.0f} | MarginPerSpread=${margin_per_spread:.0f}"
            )
            return (0, 0.0, 0.0, 0.0)

        total_margin = max_contracts * margin_per_spread

        # SAFETY CHECK: Never exceed allocation
        if total_margin > allocation:
            self.log(
                f"SAFETY: Reducing contracts - margin {total_margin:.0f} > allocation {allocation:.0f}"
            )
            max_contracts = int(allocation / margin_per_spread)
            total_margin = max_contracts * margin_per_spread

        self.log(
            f"SAFETY: Credit spread sizing | "
            f"Width=${width:.2f} | Credit=${credit_received:.2f} | "
            f"MarginPerSpread=${margin_per_spread:.0f} | "
            f"Allocation=${allocation:.0f} | MaxContracts={max_contracts} | "
            f"TotalMargin=${total_margin:.0f}"
        )

        return (max_contracts, credit_received, margin_per_spread, total_margin)

    def get_mode_allocation(
        self, mode: OptionsMode, portfolio_value: float, size_multiplier: float = 1.0
    ) -> float:
        """
        Get allocation for a specific mode with tiered dollar cap.

        V2.3.20: Added size_multiplier parameter for cold start sizing.
        V2.7: Added tiered dollar cap to prevent oversizing.

        Args:
            mode: Operating mode.
            portfolio_value: Tradeable equity (settled cash - lockbox).
            size_multiplier: Optional multiplier for position sizing (default 1.0).
                During cold start, this is 0.5 to reduce risk.

        Returns:
            Dollar allocation for the mode (adjusted by size_multiplier and tier cap).
        """
        if mode == OptionsMode.INTRADAY:
            base_allocation = portfolio_value * config.OPTIONS_INTRADAY_ALLOCATION
        else:
            base_allocation = portfolio_value * config.OPTIONS_SWING_ALLOCATION

        raw_allocation = base_allocation * size_multiplier

        # V2.7: Apply tiered dollar cap
        capped_allocation = self._apply_tiered_dollar_cap(raw_allocation, portfolio_value)

        return capped_allocation

    # =========================================================================
    # V2.8: VASS (Volatility-Adaptive Strategy Selection)
    # =========================================================================

    def _select_strategy(
        self,
        direction: str,  # "BULLISH" or "BEARISH"
        iv_environment: str,  # "LOW", "MEDIUM", "HIGH"
        is_intraday: bool = False,
    ) -> Tuple[SpreadStrategy, int, int]:
        """
        V2.8: Select spread strategy based on direction + IV environment.

        Strategy Matrix:
        - LOW IV + BULLISH → Bull Call Debit (Monthly 30-45 DTE)
        - LOW IV + BEARISH → Bear Put Debit (Monthly 30-45 DTE)
        - MEDIUM IV + BULLISH → Bull Call Debit (Weekly 7-21 DTE)
        - MEDIUM IV + BEARISH → Bear Put Debit (Weekly 7-21 DTE)
        - HIGH IV + BULLISH → Bull Put Credit (Weekly 7-14 DTE)
        - HIGH IV + BEARISH → Bear Call Credit (Weekly 7-14 DTE)

        For intraday trades, strategy type comes from matrix but DTE is always
        nearest weekly (0-5 DTE).

        Args:
            direction: Market direction ("BULLISH" or "BEARISH")
            iv_environment: IV tier ("LOW", "MEDIUM", "HIGH")
            is_intraday: If True, use nearest weekly expiration

        Returns:
            Tuple of (SpreadStrategy, dte_min, dte_max)
        """
        # Strategy Matrix Lookup
        matrix = {
            ("BULLISH", "LOW"): (
                SpreadStrategy.BULL_CALL_DEBIT,
                config.VASS_LOW_IV_DTE_MIN,
                config.VASS_LOW_IV_DTE_MAX,
            ),
            ("BULLISH", "MEDIUM"): (
                SpreadStrategy.BULL_CALL_DEBIT,
                config.VASS_MEDIUM_IV_DTE_MIN,
                config.VASS_MEDIUM_IV_DTE_MAX,
            ),
            ("BULLISH", "HIGH"): (
                SpreadStrategy.BULL_PUT_CREDIT,
                config.VASS_HIGH_IV_DTE_MIN,
                config.VASS_HIGH_IV_DTE_MAX,
            ),
            ("BEARISH", "LOW"): (
                SpreadStrategy.BEAR_PUT_DEBIT,
                config.VASS_LOW_IV_DTE_MIN,
                config.VASS_LOW_IV_DTE_MAX,
            ),
            ("BEARISH", "MEDIUM"): (
                SpreadStrategy.BEAR_PUT_DEBIT,
                config.VASS_MEDIUM_IV_DTE_MIN,
                config.VASS_MEDIUM_IV_DTE_MAX,
            ),
            ("BEARISH", "HIGH"): (
                SpreadStrategy.BEAR_CALL_CREDIT,
                config.VASS_HIGH_IV_DTE_MIN,
                config.VASS_HIGH_IV_DTE_MAX,
            ),
        }

        key = (direction, iv_environment)
        if key in matrix:
            strategy, dte_min, dte_max = matrix[key]

            # For intraday: use strategy type from matrix but nearest weekly DTE
            if is_intraday:
                dte_min = 0
                dte_max = 5  # Nearest weekly expiration

            self.log(
                f"VASS: {direction} + {iv_environment} IV → {strategy.value} | "
                f"DTE={dte_min}-{dte_max} | Intraday={is_intraday}"
            )
            return (strategy, dte_min, dte_max)

        # Fallback: Medium IV debit spread
        self.log(f"VASS: Unknown key {key}, defaulting to MEDIUM debit spread")
        if direction == "BULLISH":
            return (SpreadStrategy.BULL_CALL_DEBIT, 7, 21)
        else:
            return (SpreadStrategy.BEAR_PUT_DEBIT, 7, 21)

    def is_credit_strategy(self, strategy: SpreadStrategy) -> bool:
        """Check if strategy is a credit spread (collects premium)."""
        return strategy in (SpreadStrategy.BULL_PUT_CREDIT, SpreadStrategy.BEAR_CALL_CREDIT)

    def is_debit_strategy(self, strategy: SpreadStrategy) -> bool:
        """Check if strategy is a debit spread (pays premium)."""
        return strategy in (SpreadStrategy.BULL_CALL_DEBIT, SpreadStrategy.BEAR_PUT_DEBIT)

    # =========================================================================
    # V2.1.1 SIMPLE INTRADAY FILTERS (FOR SWING MODE)
    # =========================================================================

    def check_swing_filters(
        self,
        direction: OptionDirection,
        spy_gap_pct: float,
        spy_intraday_change_pct: float,
        vix_intraday_change_pct: float,
        current_hour: int,
        current_minute: int,
    ) -> Tuple[bool, str]:
        """
        Check simple intraday filters for Swing Mode (5+ DTE).

        For Swing Mode, we use simple filters instead of Micro Regime.
        These are lightweight, rule-based checks.

        Args:
            direction: CALL or PUT.
            spy_gap_pct: SPY gap from prior close (%).
            spy_intraday_change_pct: SPY change since open (%).
            vix_intraday_change_pct: VIX change since open (%).
            current_hour: Current hour (0-23) Eastern.
            current_minute: Current minute (0-59).

        Returns:
            Tuple of (can_enter, reason_if_blocked).
        """
        # Filter 1: Time Window (10:00 AM - 2:30 PM ET)
        time_minutes = current_hour * 60 + current_minute
        window_start = 10 * 60  # 10:00 AM
        window_end = 14 * 60 + 30  # 2:30 PM

        if not (window_start <= time_minutes <= window_end):
            # V2.3 FIX: Only return the message, don't log here (caller logs once)
            return False, "TIME_WINDOW"

        # Filter 2: Gap Filter
        if abs(spy_gap_pct) > config.SWING_GAP_THRESHOLD:
            if direction == OptionDirection.CALL and spy_gap_pct > 0:
                return False, f"Gap up {spy_gap_pct:.1f}% - reversal risk for calls"
            if direction == OptionDirection.PUT and spy_gap_pct < 0:
                return False, f"Gap down {spy_gap_pct:.1f}% - bounce risk for puts"

        # Filter 3: Extreme Move Filter
        if spy_intraday_change_pct < config.SWING_EXTREME_SPY_DROP:
            return False, f"SPY extreme drop {spy_intraday_change_pct:.1f}% - pause entries"

        if vix_intraday_change_pct > config.SWING_EXTREME_VIX_SPIKE:
            return False, f"VIX spike +{vix_intraday_change_pct:.1f}% - pause entries"

        return True, ""

    # =========================================================================
    # V2.1.1 INTRADAY MODE ENTRY (MICRO REGIME ENGINE)
    # =========================================================================

    def check_intraday_entry_signal(
        self,
        vix_current: float,
        vix_open: float,
        qqq_current: float,
        qqq_open: float,
        current_hour: int,
        current_minute: int,
        current_time: str,
        portfolio_value: float,
        best_contract: Optional[OptionContract] = None,
        size_multiplier: float = 1.0,
        macro_regime_score: float = 50.0,
    ) -> Optional[TargetWeight]:
        """
        Check for intraday mode entry signal using Micro Regime Engine.

        V2.1.1: Uses VIX Level × VIX Direction = 21 trading regimes.
        V2.3.20: Added size_multiplier for cold start reduced sizing.
        V2.5: Added macro_regime_score for Grind-Up Override logic.

        Args:
            vix_current: Current VIX value.
            vix_open: VIX at market open.
            qqq_current: Current QQQ price.
            qqq_open: QQQ at market open.
            current_hour: Current hour (0-23) Eastern.
            current_minute: Current minute (0-59).
            current_time: Timestamp string.
            portfolio_value: Total portfolio value.
            best_contract: Best available contract for the signal.
            size_multiplier: Position size multiplier (default 1.0). V2.3.20: Set to 0.5
                during cold start to reduce risk.
            macro_regime_score: Macro regime score (0-100) for Grind-Up Override. V2.5.

        Returns:
            TargetWeight for intraday entry, or None.
        """
        # Check if already have intraday position
        if self._intraday_position is not None:
            return None

        # V2.9: Check trade limits (Bug #4 fix) - Uses comprehensive counter
        # Replaces V2.3.14 intraday-only check to also enforce global limit
        if not self._can_trade_options(OptionsMode.INTRADAY):
            return None

        # Update Micro Regime Engine (V2.5: pass macro_regime_score for Grind-Up Override)
        state = self._micro_regime_engine.update(
            vix_current=vix_current,
            vix_open=vix_open,
            qqq_current=qqq_current,
            qqq_open=qqq_open,
            current_time=current_time,
            macro_regime_score=macro_regime_score,
        )

        # Check if strategy is NO_TRADE
        if state.recommended_strategy == IntradayStrategy.NO_TRADE:
            return None

        # Check if strategy is PROTECTIVE_PUTS (hedge, not directional)
        if state.recommended_strategy == IntradayStrategy.PROTECTIVE_PUTS:
            self.log(f"INTRADAY: Protective mode - regime={state.micro_regime.value}")
            return None  # Would emit hedge signal separately

        # V2.3.4: Use direction from state (determined by recommend_strategy_and_direction)
        direction = state.recommended_direction
        if direction is None:
            self.log(f"INTRADAY: No direction recommended for {state.recommended_strategy.value}")
            return None

        # Map strategy to name for logging
        strategy_names = {
            IntradayStrategy.DEBIT_FADE: "DEBIT_FADE",
            IntradayStrategy.ITM_MOMENTUM: "ITM_MOM",
            IntradayStrategy.CREDIT_SPREAD: "CREDIT",
        }
        strategy_name = strategy_names.get(state.recommended_strategy, "UNKNOWN")

        # Check time windows based on strategy (V2.3.19: use config values)
        time_minutes = current_hour * 60 + current_minute

        if state.recommended_strategy == IntradayStrategy.DEBIT_FADE:
            # Parse config time strings (e.g., "10:30" -> 630 minutes)
            fade_start = config.INTRADAY_DEBIT_FADE_START.split(":")
            fade_end = config.INTRADAY_DEBIT_FADE_END.split(":")
            start_time = int(fade_start[0]) * 60 + int(fade_start[1])
            end_time = int(fade_end[0]) * 60 + int(fade_end[1])
            if not (start_time <= time_minutes <= end_time):
                # V2.13 Fix #17: Log time window rejection (was silent)
                self.log(
                    f"INTRADAY_TIME_REJECT: DEBIT_FADE at {current_hour}:{current_minute:02d} "
                    f"outside window {config.INTRADAY_DEBIT_FADE_START}-{config.INTRADAY_DEBIT_FADE_END}"
                )
                return None

        elif state.recommended_strategy == IntradayStrategy.ITM_MOMENTUM:
            # V2.3.19: Use config values instead of hardcoded
            itm_start = config.INTRADAY_ITM_START.split(":")
            itm_end = config.INTRADAY_ITM_END.split(":")
            start_time = int(itm_start[0]) * 60 + int(itm_start[1])
            end_time = int(itm_end[0]) * 60 + int(itm_end[1])
            if not (start_time <= time_minutes <= end_time):
                # V2.13 Fix #17: Log time window rejection (was silent)
                self.log(
                    f"INTRADAY_TIME_REJECT: ITM_MOMENTUM at {current_hour}:{current_minute:02d} "
                    f"outside window {config.INTRADAY_ITM_START}-{config.INTRADAY_ITM_END}"
                )
                return None

        # Check if we have a valid contract
        if best_contract is None:
            self.log(f"INTRADAY: {strategy_name} signal but no contract available")
            return None

        # V2.3 FIX: Validate contract direction matches signal direction
        # The contract was selected before direction was determined, so we must verify
        if best_contract.direction != direction:
            self.log(
                f"INTRADAY: Direction mismatch - signal wants {direction.value} "
                f"but contract is {best_contract.direction.value}, skipping"
            )
            return None

        # V2.18: Use hardcoded sizing cap (Fix for MarginBuyingPower sizing bug)
        # Solution: Absolute dollar cap of $4,000 for intraday
        intraday_max_dollars = getattr(config, "INTRADAY_SPREAD_MAX_DOLLARS", 4000)

        # Adjust size based on micro score
        if state.micro_score >= config.MICRO_SCORE_PRIME_MR:
            size_mult = 1.0  # Full size
        elif state.micro_score >= config.MICRO_SCORE_GOOD_MR:
            size_mult = 1.0  # Full size
        elif state.micro_score >= config.MICRO_SCORE_MODERATE:
            size_mult = 0.5  # Half size
        else:
            size_mult = 0.5  # Half size

        adjusted_cap = intraday_max_dollars * size_mult
        premium = best_contract.mid_price
        if premium <= 0:
            self.log("INTRADAY: Entry blocked - invalid premium price")
            return None

        # V2.18: Calculate contracts using hardcoded cap / (premium * 100)
        num_contracts = int(adjusted_cap / (premium * 100))
        self.log(
            f"SIZING: INTRADAY | Cap=${adjusted_cap:.0f} | Premium=${premium:.2f} | Qty={num_contracts}"
        )
        if num_contracts <= 0:
            self.log(
                f"INTRADAY: Entry blocked - cap ${adjusted_cap:.0f} "
                f"too small for premium ${premium:.2f}"
            )
            return None

        # V2.3.4: Use QQQ direction from state
        qqq_dir_str = state.qqq_direction.value if state.qqq_direction else "UNKNOWN"
        reason = (
            f"INTRADAY_{strategy_name}: Regime={state.micro_regime.value} | "
            f"Score={state.micro_score:.0f} | VIX={vix_current:.1f} "
            f"({state.vix_direction.value}) | QQQ={qqq_dir_str} "
            f"({state.qqq_move_pct:+.2f}%) | {direction.value} x{num_contracts}"
        )

        # V2.4.1 FIX: Increment counter IMMEDIATELY on signal generation, not on fill
        # This fixes the race condition where multiple signals are generated before
        # the first fill increments the counter (resulted in 4 fills when limit=2)
        # V2.9: Use comprehensive counter to also track global limit
        self._increment_trade_counter(OptionsMode.INTRADAY)

        # V2.3.2 FIX #4: Mark this as intraday entry for correct position tracking
        self._pending_intraday_entry = True

        # V2.3.10 FIX: Set pending contract for register_entry
        # Without this, register_entry fails with "no pending contract"
        self._pending_contract = best_contract
        self._pending_num_contracts = num_contracts
        self._pending_stop_pct = config.OPTIONS_0DTE_STOP_PCT  # Use 0DTE stop

        self.log(
            f"INTRADAY_SIGNAL: {reason} | Δ={best_contract.delta:.2f} K={best_contract.strike} DTE={best_contract.days_to_expiry} | TradeCount={self._intraday_trades_today}/{config.INTRADAY_MAX_TRADES_PER_DAY}",
            trades_only=True,
        )

        # V2.4.1 FIX: Use config allocation value, not size_mult
        # Was returning 1.0/0.5 instead of actual allocation (0.0625)
        actual_target_weight = config.OPTIONS_INTRADAY_ALLOCATION * size_mult

        return TargetWeight(
            symbol=best_contract.symbol,
            target_weight=actual_target_weight,  # V2.4.1: Actual allocation, not 1.0
            source="OPT_INTRADAY",
            urgency=Urgency.IMMEDIATE,
            reason=reason,
            requested_quantity=num_contracts,  # V2.3.2: Pass calculated contracts
        )

    def check_intraday_force_exit(
        self,
        current_hour: int,
        current_minute: int,
        current_price: float,
    ) -> Optional[TargetWeight]:
        """
        Check for forced exit of intraday position at 3:30 PM ET.

        Intraday mode positions MUST be closed by 3:30 PM.

        Args:
            current_hour: Current hour (0-23) Eastern.
            current_minute: Current minute (0-59).
            current_price: Current option price.

        Returns:
            TargetWeight for forced exit, or None.
        """
        if self._intraday_position is None:
            return None

        # V2.3.3 FIX #3: Prevent duplicate exit signals while waiting for fill
        if self._pending_intraday_exit:
            return None

        # Force exit at 15:30 (3:30 PM)
        force_exit_time = current_hour > 15 or (current_hour == 15 and current_minute >= 30)

        if not force_exit_time:
            return None

        symbol = self._intraday_position.contract.symbol
        entry_price = self._intraday_position.entry_price

        pnl_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0

        reason = f"INTRADAY_TIME_EXIT_1530 {pnl_pct:+.1%} (Price: ${current_price:.2f})"
        self.log(f"INTRADAY_FORCE_EXIT {symbol} | {reason}", trades_only=True)

        # V2.3.3: Set pending exit flag to prevent duplicate signals
        self._pending_intraday_exit = True

        return TargetWeight(
            symbol=symbol,
            target_weight=0.0,
            source="OPT_INTRADAY",
            urgency=Urgency.IMMEDIATE,
            reason=reason,
        )

    def check_gamma_pin_exit(
        self,
        current_price: float,
        current_dte: int,
    ) -> Optional[List[TargetWeight]]:
        """
        V2.10 (Pitfall #5): Exit early if price is within buffer zone of short strike.

        Prevents broker auto-liquidation during gamma acceleration near expiration.
        When underlying price pins near the short strike within 2 DTE, gamma explodes
        and the broker may force-liquidate at terrible prices to avoid assignment risk.

        Args:
            current_price: Current underlying (QQQ) price.
            current_dte: Days to expiration for the spread.

        Returns:
            List[TargetWeight] for spread exit if gamma pin detected, else None.
        """
        if not config.GAMMA_PIN_CHECK_ENABLED:
            return None

        # Only check spread positions (credit or debit)
        spread = self._spread_position or self._credit_spread_position
        if spread is None:
            return None

        # Only activate within GAMMA_PIN_EARLY_EXIT_DTE
        if current_dte > config.GAMMA_PIN_EARLY_EXIT_DTE:
            return None

        # Get short strike from spread
        short_strike = spread.short_leg.strike
        distance_pct = abs(current_price - short_strike) / short_strike

        if distance_pct >= config.GAMMA_PIN_BUFFER_PCT:
            return None

        # GAMMA PIN DETECTED - exit early
        # V2.14 Fix #13: Align keyword with AAP protocol (was GAMMA_PIN:)
        self.log(
            f"GAMMA_PIN_EXIT: Early exit triggered | "
            f"Price=${current_price:.2f} Strike=${short_strike:.0f} "
            f"Distance={distance_pct:.2%} < {config.GAMMA_PIN_BUFFER_PCT:.2%} | "
            f"DTE={current_dte}",
            trades_only=True,
        )

        # Return spread exit signal (same format as spread exit)
        return [
            TargetWeight(
                symbol=spread.long_leg.symbol,
                target_weight=0.0,
                source="OPT",
                urgency=Urgency.IMMEDIATE,
                reason=f"GAMMA_PIN_BUFFER (price within {distance_pct:.2%} of strike ${short_strike})",
                requested_quantity=getattr(
                    spread, "num_spreads", getattr(spread, "num_contracts", 1)
                ),
                metadata={
                    "spread_close_short": True,
                    "spread_short_leg_symbol": spread.short_leg.symbol,
                    "exit_type": "GAMMA_PIN",
                },
            )
        ]

    def check_expiring_options_force_exit(
        self,
        current_date: str,
        current_hour: int,
        current_minute: int,
        current_price: float,
        contract_expiry_date: str,
    ) -> Optional[TargetWeight]:
        """
        V2.4.4 P0: EXPIRATION HAMMER V2 - Force close ALL options expiring TODAY.

        CRITICAL SAFETY: ITM options held past 4 PM get auto-exercised by the broker,
        creating massive stock positions that can cause margin crises.

        V2.4.4 Change: Close ALL options on expiration day at 2:00 PM, regardless of
        whether they are ITM/OTM or any other condition. This prevents:
        - Auto-exercise of ITM options creating stock positions
        - OTM options expiring worthless (100% loss)
        - Any exercise-related margin disasters

        Example from V2.4.3 backtest:
        - 3 option exercises created $700K QQQ position on $50K account
        - 2,765 margin call orders, 2,786 invalid orders
        - Kill switch couldn't close options during margin crisis

        Args:
            current_date: Current date as string (YYYY-MM-DD).
            current_hour: Current hour (0-23) Eastern.
            current_minute: Current minute (0-59).
            current_price: Current option price.
            contract_expiry_date: Contract expiry date as string (YYYY-MM-DD).

        Returns:
            TargetWeight for forced exit, or None.
        """
        # Check if option expires TODAY
        if current_date != contract_expiry_date:
            return None

        # Check if ANY position exists (swing or intraday)
        position = self._position or self._intraday_position
        if position is None:
            return None

        # V2.4.4 P0: Expiration Hammer V2 - ALWAYS close at 2:00 PM on expiration day
        # Old behavior: Only closed based on conditions/VIX
        # New behavior: UNCONDITIONALLY close ALL options expiring today
        force_close_hour = config.OPTIONS_EXPIRING_TODAY_FORCE_CLOSE_HOUR
        force_close_minute = config.OPTIONS_EXPIRING_TODAY_FORCE_CLOSE_MINUTE

        force_exit_time = current_hour > force_close_hour or (
            current_hour == force_close_hour and current_minute >= force_close_minute
        )

        if not force_exit_time:
            return None

        symbol = position.contract.symbol
        entry_price = position.entry_price
        source = "OPT_INTRADAY" if self._intraday_position else "OPT_SWING"

        pnl_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0

        # V2.4.4: Stronger messaging - this is a mandatory close
        reason = (
            f"EXPIRATION_HAMMER_V2 {pnl_pct:+.1%} | "
            f"MANDATORY CLOSE - Option expires TODAY ({contract_expiry_date}) | "
            f"Price: ${current_price:.2f}"
        )
        self.log(
            f"EXPIRATION_HAMMER_V2: FORCE CLOSE {symbol} | {reason} | "
            f"P0 FIX: Unconditionally closing ALL expiring options at 2:00 PM",
            trades_only=True,
        )

        return TargetWeight(
            symbol=symbol,
            target_weight=0.0,
            source=source,
            urgency=Urgency.IMMEDIATE,
            reason=reason,
        )

    def get_micro_regime_state(self) -> MicroRegimeState:
        """Get current Micro Regime Engine state."""
        return self._micro_regime_engine.get_state()

    def get_intraday_direction(
        self,
        vix_current: float,
        vix_open: float,
        qqq_current: float,
        qqq_open: float,
        current_time: str,
        regime_score: float = 50.0,
    ) -> Optional[OptionDirection]:
        """
        V2.3.14: Get recommended intraday direction from Micro Regime Engine.
        V2.3.16: Added direction conflict resolution (centralized).

        Updates the engine state and returns the recommended direction.
        This should be called BEFORE selecting the contract to avoid
        direction mismatch (hardcoded fade vs engine momentum recommendation).

        Direction Conflict Resolution (V2.3.16):
        - If FADE strategy + strong bullish regime (>65) + PUT direction → skip
        - If FADE strategy + strong bearish regime (<40) + CALL direction → skip
        This prevents counter-trend trades in strongly trending markets.

        Args:
            vix_current: Current VIX value.
            vix_open: VIX at market open.
            qqq_current: Current QQQ price.
            qqq_open: QQQ at market open.
            current_time: Timestamp string.
            regime_score: Macro regime score (0-100) for direction conflict check.

        Returns:
            Recommended OptionDirection (CALL or PUT), or None if NO_TRADE or conflict.
        """
        # Update Micro Regime Engine (V2.5: pass macro_regime_score for Grind-Up Override)
        state = self._micro_regime_engine.update(
            vix_current=vix_current,
            vix_open=vix_open,
            qqq_current=qqq_current,
            qqq_open=qqq_open,
            current_time=current_time,
            macro_regime_score=regime_score,
        )

        direction = state.recommended_direction

        # If no direction recommended, return None
        if direction is None:
            return None

        # V2.3.16: Direction conflict resolution for FADE strategies
        # Skip intraday FADE when macro regime strongly disagrees with direction
        # This prevents counter-trend trades in strongly trending markets
        if state.recommended_strategy == IntradayStrategy.DEBIT_FADE:
            # Strong bullish regime + FADE PUT (fading rally) = conflict
            if (
                regime_score > config.DIRECTION_CONFLICT_BULLISH_THRESHOLD
                and direction == OptionDirection.PUT
            ):
                self.log(
                    f"DIRECTION_CONFLICT: Skipping FADE PUT - regime {regime_score:.1f} "
                    f"> {config.DIRECTION_CONFLICT_BULLISH_THRESHOLD} (strong bullish)"
                )
                return None

            # Strong bearish regime + FADE CALL (fading dip) = conflict
            if (
                regime_score < config.DIRECTION_CONFLICT_BEARISH_THRESHOLD
                and direction == OptionDirection.CALL
            ):
                self.log(
                    f"DIRECTION_CONFLICT: Skipping FADE CALL - regime {regime_score:.1f} "
                    f"< {config.DIRECTION_CONFLICT_BEARISH_THRESHOLD} (strong bearish)"
                )
                return None

        return direction

    def get_last_intraday_strategy(self) -> IntradayStrategy:
        """
        V2.3.16: Get the last recommended intraday strategy.

        Returns:
            IntradayStrategy enum (DEBIT_FADE, ITM_MOMENTUM, NO_TRADE, etc.)
        """
        return self._micro_regime_engine.get_state().recommended_strategy

    def update_market_open_data(
        self, vix_open: float, spy_open: float, spy_prior_close: float
    ) -> None:
        """
        Update market open data for simple filters.

        Should be called at market open (9:30-9:33 AM).

        Args:
            vix_open: VIX value at open.
            spy_open: SPY price at open.
            spy_prior_close: SPY prior close price.
        """
        self._vix_at_open = vix_open
        self._spy_at_open = spy_open

        if spy_prior_close > 0:
            self._spy_gap_pct = (spy_open - spy_prior_close) / spy_prior_close * 100
        else:
            self._spy_gap_pct = 0.0

        self.log(
            f"Market open data: VIX={vix_open:.1f} | "
            f"SPY={spy_open:.2f} | Gap={self._spy_gap_pct:+.2f}%"
        )

    # =========================================================================
    # POSITION MANAGEMENT
    # =========================================================================

    def register_entry(
        self,
        fill_price: float,
        entry_time: str,
        current_date: str,
        contract: Optional[OptionContract] = None,
    ) -> Optional[OptionsPosition]:
        """
        Register a new options position after fill.

        Args:
            fill_price: Actual fill price.
            entry_time: Entry timestamp string.
            current_date: Current date string.
            contract: Option contract (uses pending if not provided).

        Returns:
            Created OptionsPosition, or None if no pending contract exists.
        """
        # Use pending values from check_entry_signal
        if contract is None:
            contract = self._pending_contract

        # Guard: If no pending contract exists, we can't register entry
        # This can happen if fill occurs for an order placed outside our signal flow
        if contract is None:
            self.log("OPT: register_entry called but no pending contract - skipping")
            return None

        # Use pending values if set, otherwise defaults
        # Note: getattr defaults don't work when attr exists but is None
        entry_score = self._pending_entry_score if self._pending_entry_score is not None else 3.0
        num_contracts = (
            self._pending_num_contracts if self._pending_num_contracts is not None else 1
        )
        stop_pct = self._pending_stop_pct if self._pending_stop_pct is not None else 0.20

        # Recalculate stop and target based on actual fill price
        stop_price = fill_price * (1 - stop_pct)
        target_price = fill_price * (1 + config.OPTIONS_PROFIT_TARGET_PCT)

        position = OptionsPosition(
            contract=contract,
            entry_price=fill_price,
            entry_time=entry_time,
            entry_score=entry_score,
            num_contracts=num_contracts,
            stop_price=stop_price,
            target_price=target_price,
            stop_pct=stop_pct,
        )

        # V2.3.2 FIX #4: Track position in correct variable based on mode
        if self._pending_intraday_entry:
            self._intraday_position = position
            self._pending_intraday_entry = False  # Clear flag
            # V2.4.1: Counter now incremented on signal generation (check_intraday_entry_signal)
            # to prevent race condition. Do NOT increment again here.
            self.log(
                f"OPT: INTRADAY position registered (trade #{self._intraday_trades_today}, force-close at 15:30)",
                trades_only=True,
            )
        else:
            self._position = position
            # V2.9: Increment swing counter (Bug #4 fix)
            # Intraday already counted on signal generation to prevent race condition
            self._increment_trade_counter(OptionsMode.SWING)

        # Update last trade date for backward compatibility
        self._last_trade_date = current_date

        self.log(
            f"OPT: POSITION_REGISTERED {contract.symbol} | "
            f"Entry=${fill_price:.2f} | "
            f"Target=${target_price:.2f} (+{config.OPTIONS_PROFIT_TARGET_PCT:.0%}) | "
            f"Stop=${stop_price:.2f} (-{stop_pct:.0%}) | "
            f"Contracts={num_contracts} | "
            f"Score={entry_score:.2f}"
        )

        # Clear pending state
        self._pending_contract = None
        self._pending_entry_score = None
        self._pending_num_contracts = None
        self._pending_stop_pct = None
        self._pending_stop_price = None
        self._pending_target_price = None

        return position

    def remove_position(self) -> Optional[OptionsPosition]:
        """
        Remove the current position after exit.

        Returns:
            Removed position, or None if no position existed.
        """
        if self._position is not None:
            position = self._position
            self._position = None
            self.log(f"OPT: POSITION_REMOVED {position.contract.symbol}", trades_only=True)
            return position
        return None

    def remove_intraday_position(self) -> Optional[OptionsPosition]:
        """
        V2.3.2: Remove the current intraday position after exit.

        Returns:
            Removed intraday position, or None if no position existed.
        """
        # V2.3.3: Clear pending exit flag when position is removed
        self._pending_intraday_exit = False

        if self._intraday_position is not None:
            position = self._intraday_position
            self._intraday_position = None
            self.log(
                f"OPT: INTRADAY_POSITION_REMOVED {position.contract.symbol}",
                trades_only=True,
            )
            return position
        return None

    # =========================================================================
    # V2.3 SPREAD POSITION MANAGEMENT
    # =========================================================================

    def register_spread_entry(
        self,
        long_leg_fill_price: float,
        short_leg_fill_price: float,
        entry_time: str,
        current_date: str,
        regime_score: float,
    ) -> Optional[SpreadPosition]:
        """
        V2.3: Register a new spread position after both legs fill.

        Args:
            long_leg_fill_price: Actual fill price for long leg.
            short_leg_fill_price: Actual fill price for short leg.
            entry_time: Entry timestamp string.
            current_date: Current date string.
            regime_score: Regime score at entry.

        Returns:
            Created SpreadPosition, or None if no pending spread exists.
        """
        if self._pending_spread_long_leg is None or self._pending_spread_short_leg is None:
            self.log("SPREAD: register_spread_entry called but no pending spread - skipping")
            return None

        # Calculate actual net debit from fills
        net_debit = long_leg_fill_price - short_leg_fill_price
        width = self._pending_spread_width or abs(
            self._pending_spread_short_leg.strike - self._pending_spread_long_leg.strike
        )
        max_profit = width - net_debit

        num_spreads = self._pending_num_contracts or 1
        entry_score = self._pending_entry_score or 3.0

        spread = SpreadPosition(
            long_leg=self._pending_spread_long_leg,
            short_leg=self._pending_spread_short_leg,
            spread_type=self._pending_spread_type or "UNKNOWN",
            net_debit=net_debit,
            max_profit=max_profit,
            width=width,
            entry_time=entry_time,
            entry_score=entry_score,
            num_spreads=num_spreads,
            regime_at_entry=regime_score,
        )

        self._spread_position = spread

        # V2.9: Update trade counter (Bug #4 fix) - Spreads are always swing mode
        self._increment_trade_counter(OptionsMode.SWING)

        self.log(
            f"SPREAD: POSITION_REGISTERED | {spread.spread_type} | "
            f"Long={spread.long_leg.strike} @ ${long_leg_fill_price:.2f} | "
            f"Short={spread.short_leg.strike} @ ${short_leg_fill_price:.2f} | "
            f"Net Debit=${net_debit:.2f} | Max Profit=${max_profit:.2f} | "
            f"x{num_spreads} | Target=${spread.profit_target:.2f}",
            trades_only=True,
        )

        # Clear pending state
        self._pending_spread_long_leg = None
        self._pending_spread_short_leg = None
        self._pending_spread_type = None
        self._pending_net_debit = None
        self._pending_max_profit = None
        self._pending_spread_width = None
        self._rejection_margin_cap = None  # V2.21: Clear on successful fill

        return spread

    # =========================================================================
    # V2.20: REJECTION RECOVERY METHODS
    # =========================================================================

    def cancel_pending_swing_entry(self) -> None:
        """
        V2.20: Clear pending swing entry state after broker rejection.

        Resets all swing single-leg pending fields and allows retry.
        No counter decrement needed — swing counter is only incremented
        in register_entry() on fill, not on signal generation.
        Called by main._handle_order_rejection().
        """
        self._pending_contract = None
        self._pending_entry_score = None
        self._pending_num_contracts = None
        self._pending_stop_pct = None
        self._pending_stop_price = None
        self._pending_target_price = None
        self._entry_attempted_today = False
        self.log(
            "OPT_SWING_RECOVERY: Pending swing entry cancelled | Retry allowed",
            trades_only=True,
        )

    def cancel_pending_spread_entry(self) -> None:
        """
        V2.20: Clear pending spread entry state after broker rejection.

        Resets all spread pending fields and allows retry. No counter
        decrement needed — spread counter is only incremented in
        register_spread_entry() on fill, not on signal generation.
        Caller must also call portfolio_router.clear_all_spread_margins()
        to free ghost margin reservations.
        Called by main._handle_order_rejection().
        """
        self._pending_spread_long_leg = None
        self._pending_spread_short_leg = None
        self._pending_spread_type = None
        self._pending_net_debit = None
        self._pending_max_profit = None
        self._pending_spread_width = None
        self._pending_num_contracts = None
        self._pending_entry_score = None
        self._entry_attempted_today = False
        self.log(
            "OPT_MACRO_RECOVERY: Pending spread entry cancelled | Retry allowed",
            trades_only=True,
        )

    def cancel_pending_intraday_entry(self) -> None:
        """
        V2.20: Clear pending intraday entry state after broker rejection.

        Resets intraday pending fields and DECREMENTS the pre-incremented
        trade counter. V2.4.1 pre-increments at signal generation (line 3769)
        to prevent race conditions; on rejection the counter must roll back.
        Guard: only decrements if _pending_intraday_entry was True to
        prevent double-decrement on repeated calls.
        Called by main._handle_order_rejection().
        """
        if self._pending_intraday_entry:
            self._pending_intraday_entry = False
            self._pending_contract = None
            self._pending_num_contracts = None
            self._pending_stop_pct = None
            # Decrement pre-incremented counters (guard against underflow)
            if self._intraday_trades_today > 0:
                self._intraday_trades_today -= 1
            if self._total_options_trades_today > 0:
                self._total_options_trades_today -= 1
            if self._trades_today > 0:
                self._trades_today -= 1
            self.log(
                f"OPT_MICRO_RECOVERY: Pending intraday entry cancelled | "
                f"Counter decremented | Intraday={self._intraday_trades_today} | "
                f"Total={self._total_options_trades_today} | Retry allowed",
                trades_only=True,
            )

    def remove_spread_position(self) -> Optional[SpreadPosition]:
        """
        V2.3: Remove the current spread position after exit.
        V2.6 Bug #16: Records exit time for post-trade margin cooldown.

        Returns:
            Removed spread position, or None if no spread existed.
        """
        if self._spread_position is not None:
            spread = self._spread_position
            self._spread_position = None

            # V2.6 Bug #16: Record exit time for margin cooldown
            # After closing a spread, broker takes T+1 to settle margin
            # Use algorithm.Time for QC compliance (not system time)
            if self.algorithm is not None and hasattr(self.algorithm, "Time"):
                self._last_spread_exit_time = self.algorithm.Time.strftime("%Y-%m-%d %H:%M:%S")
            else:
                # Fallback for testing without algorithm context
                self._last_spread_exit_time = "1970-01-01 00:00:00"

            self.log(
                f"SPREAD: POSITION_REMOVED | {spread.spread_type} | "
                f"Long={spread.long_leg.symbol} Short={spread.short_leg.symbol} | "
                f"Cooldown until {config.OPTIONS_POST_TRADE_COOLDOWN_MINUTES}min after exit",
                trades_only=True,
            )
            return spread
        return None

    def has_spread_position(self) -> bool:
        """V2.3: Check if a spread position exists."""
        return self._spread_position is not None

    def get_spread_position(self) -> Optional[SpreadPosition]:
        """V2.3: Get current spread position."""
        return self._spread_position

    def clear_spread_position(self) -> None:
        """
        V2.12 Fix #5: Force clear spread position tracking.

        Used by margin circuit breaker to reset tracking after forced liquidation.
        Does NOT place orders - just clears internal state.
        """
        if self._spread_position is not None:
            self.log(
                f"SPREAD: FORCE_CLEARED | {self._spread_position.spread_type} | "
                f"Margin CB liquidation",
                trades_only=True,
            )
            self._spread_position = None
            self._last_spread_exit_time = None
            self._entry_attempted_today = True  # Prevent re-entry today

    def reset_spread_closing_lock(self) -> None:
        """
        V2.17: Clear the is_closing lock if all close attempts failed.

        Called by PortfolioRouter when both combo order retries and
        sequential fallback fail. This allows the spread to be retried
        on subsequent iterations instead of staying permanently locked.

        Does NOT clear the spread position - just resets the lock flag.
        """
        if self._spread_position is not None and self._spread_position.is_closing:
            self._spread_position.is_closing = False
            self.log(
                f"SPREAD: LOCK_RESET | Position remains open | "
                f"Type={self._spread_position.spread_type} x{self._spread_position.num_spreads} | "
                f"Will retry on next check",
                trades_only=True,
            )

    def has_intraday_position(self) -> bool:
        """V2.3.2: Check if an intraday position exists (tracked separately for 15:30 force close)."""
        return self._intraday_position is not None

    def get_intraday_position(self) -> Optional[OptionsPosition]:
        """V2.3.2: Get current intraday position."""
        return self._intraday_position

    def has_position(self) -> bool:
        """Check if any position exists (single-leg, spread, or intraday)."""
        return (
            self._position is not None
            or self._spread_position is not None
            or self._intraday_position is not None
        )

    def get_position(self) -> Optional[OptionsPosition]:
        """Get current position."""
        return self._position

    def clear_all_positions(self) -> None:
        """
        V2.5 PART 19 FIX: Clear ALL position tracking state.

        This is called by kill switch to prevent "zombie state" where
        internal position trackers remain set after broker positions are closed.

        The bug: If a spread is entered and then kill switch liquidates it,
        the internal _spread_position variable stays set, blocking ALL future
        spread entries for months.

        Solution: Use stateless tracking - clear all internal state when
        positions are forcibly closed by kill switch.
        """
        cleared = []

        if self._position is not None:
            self._position = None
            cleared.append("single-leg")

        if self._spread_position is not None:
            self._spread_position = None
            cleared.append("spread")

        if self._intraday_position is not None:
            self._intraday_position = None
            cleared.append("intraday")

        # V2.16-BT: Also clear swing position (V2.1.1 dual-mode)
        if self._swing_position is not None:
            self._swing_position = None
            cleared.append("swing")

        # Also clear pending state
        self._pending_contract = None
        self._pending_intraday_entry = False
        self._pending_spread_long_leg = None
        self._pending_spread_short_leg = None
        self._pending_spread_width = None

        if cleared:
            self.log(
                f"OPT: CLEAR_ALL_POSITIONS (kill switch) | Cleared: {', '.join(cleared)}",
                trades_only=True,
            )

    # =========================================================================
    # GREEKS MONITORING (V2.1 RSK-2)
    # =========================================================================

    def calculate_position_greeks(self) -> Optional[GreeksSnapshot]:
        """
        Calculate Greeks for current position.

        Returns per-contract Greeks for risk limit checking.
        Risk limits are per-contract (e.g., delta 0.80 = too deep ITM).
        Theta is normalized to percentage of position value for threshold comparison.

        Returns:
            GreeksSnapshot for risk engine, or None if no position.
        """
        if self._position is None:
            return None

        contract = self._position.contract

        # Calculate position value for theta normalization
        # Position value = num_contracts × mid_price × 100 (shares per contract)
        position_value = self._position.num_contracts * contract.mid_price * 100
        if position_value <= 0:
            # Fallback to entry price if mid_price not available
            position_value = self._position.num_contracts * self._position.entry_price * 100

        # Normalize theta to percentage of position value
        # Raw theta is in dollars/day, threshold CB_THETA_WARNING=-0.02 means -2%/day max
        # Total theta = per-contract theta × num_contracts
        total_theta_dollars = contract.theta * self._position.num_contracts
        theta_pct = total_theta_dollars / position_value if position_value > 0 else 0.0

        # V2.3 FIX: Skip theta check for swing mode (5-45 DTE)
        # Swing mode options naturally have higher theta decay but more time to recover.
        # Only enforce theta limits for intraday mode (0-2 DTE) where decay matters critically.
        if not config.CB_THETA_SWING_CHECK_ENABLED and contract.days_to_expiry > 2:
            theta_pct = 0.0  # Set to 0 to pass theta check

        # Return per-contract Greeks for delta/gamma/vega, normalized theta for percentage check
        return GreeksSnapshot(
            delta=contract.delta,
            gamma=contract.gamma,
            vega=contract.vega,
            theta=theta_pct,  # Now expressed as percentage (e.g., -0.01 = -1%/day)
        )

    def update_position_greeks(
        self,
        delta: float,
        gamma: float,
        vega: float,
        theta: float,
    ) -> None:
        """
        Update Greeks on current position's contract.

        Called when new Greeks data is received from broker/data feed.

        Args:
            delta: Current delta (-1 to +1 for puts/calls).
            gamma: Current gamma.
            vega: Current vega.
            theta: Current theta (daily decay, typically negative).
        """
        if self._position is None:
            return

        # Update the contract's Greeks
        self._position.contract.delta = delta
        self._position.contract.gamma = gamma
        self._position.contract.vega = vega
        self._position.contract.theta = theta

        self.log(
            f"OPT: Greeks updated | " f"D={delta:.3f} G={gamma:.4f} V={vega:.3f} T={theta:.4f}"
        )

    def check_greeks_breach(
        self,
        risk_engine: "RiskEngine",
    ) -> Tuple[bool, List[str]]:
        """
        Check if current position Greeks breach risk limits.

        Updates risk engine with current Greeks and checks for breach.

        Args:
            risk_engine: Risk engine instance.

        Returns:
            Tuple of (is_breach, list of symbols to close).
        """
        greeks = self.calculate_position_greeks()

        if greeks is None:
            # No position, clear risk engine Greeks state
            risk_engine.update_greeks(GreeksSnapshot())
            return False, []

        # Update risk engine with current Greeks
        risk_engine.update_greeks(greeks)

        # Check for breach
        is_breach, options_to_close = risk_engine.check_cb_greeks_breach()

        if is_breach:
            self.log(
                f"OPT: GREEKS_BREACH | "
                f"D={greeks.delta:.2f} G={greeks.gamma:.4f} "
                f"V={greeks.vega:.2f} T={greeks.theta:.4f}"
            )

        return is_breach, options_to_close

    # =========================================================================
    # STATE PERSISTENCE
    # =========================================================================

    def get_state_for_persistence(self) -> Dict[str, Any]:
        """Get state for ObjectStore."""
        return {
            # Legacy position (backwards compatibility)
            "position": self._position.to_dict() if self._position else None,
            "trades_today": self._trades_today,
            "last_trade_date": self._last_trade_date,
            # V2.1.1 dual-mode state
            "swing_position": (self._swing_position.to_dict() if self._swing_position else None),
            "intraday_position": (
                self._intraday_position.to_dict() if self._intraday_position else None
            ),
            "intraday_trades_today": self._intraday_trades_today,
            "current_mode": self._current_mode.value,
            "micro_regime_state": self._micro_regime_engine.get_state().to_dict(),
            # V2.16-BT: Persist spread position for multi-day backtests
            "spread_position": (self._spread_position.to_dict() if self._spread_position else None),
            # Market open data
            "vix_at_open": self._vix_at_open,
            "spy_at_open": self._spy_at_open,
            "spy_gap_pct": self._spy_gap_pct,
        }

    def restore_state(self, state: Dict[str, Any]) -> None:
        """
        Restore state from ObjectStore.

        CRITICAL: Intraday positions (0-2 DTE) should NEVER be held overnight.
        If we're restoring state and find an intraday position, it's likely
        a critical failure that needs immediate attention.
        """
        # V2.16-BT: Get current date for expiry validation (defensive for tests)
        algorithm = getattr(self, "_algorithm", None)
        if algorithm and hasattr(algorithm, "Time"):
            current_date = algorithm.Time.strftime("%Y-%m-%d")
        else:
            # Fallback for tests where _algorithm not initialized
            # Use far-past date so positions in tests are never considered expired
            current_date = "2020-01-01"

        # Legacy position (backwards compatibility)
        position_data = state.get("position")
        if position_data:
            position = OptionsPosition.from_dict(position_data)
            # V2.16-BT: Validate legacy position hasn't expired
            contract_expiry = position.contract.expiry if position.contract else None
            if contract_expiry and contract_expiry < current_date:
                self.log(
                    f"OPT: ZOMBIE_CLEAR - Legacy position expired {contract_expiry} < {current_date}. "
                    "Clearing stale position."
                )
                self._position = None
            else:
                self._position = position
        else:
            self._position = None

        self._trades_today = state.get("trades_today", 0)
        self._last_trade_date = state.get("last_trade_date")

        # V2.1.1 dual-mode state
        swing_data = state.get("swing_position")
        if swing_data:
            position = OptionsPosition.from_dict(swing_data)
            # V2.16-BT: Validate swing position hasn't expired
            contract_expiry = position.contract.expiry if position.contract else None
            if contract_expiry and contract_expiry < current_date:
                self.log(
                    f"OPT: ZOMBIE_CLEAR - Swing position expired {contract_expiry} < {current_date}. "
                    "Clearing stale position."
                )
                self._swing_position = None
            else:
                self._swing_position = position
        else:
            self._swing_position = None

        intraday_data = state.get("intraday_position")
        if intraday_data:
            # CRITICAL FIX: Intraday positions should NEVER exist overnight
            # If found, it means position wasn't closed at 15:30 (critical failure)
            # Force clear and log warning - the position is likely expired or at extreme risk
            self.log(
                "OPT: CRITICAL - Intraday position found on state restore! "
                "0-2 DTE options should close by 15:30. "
                "Position may be expired or at extreme gap risk. Clearing."
            )
            self._intraday_position = None
        else:
            self._intraday_position = None

        self._intraday_trades_today = state.get("intraday_trades_today", 0)

        # V2.16-BT: Restore spread position with expiry validation
        spread_data = state.get("spread_position")
        if spread_data:
            spread = SpreadPosition.from_dict(spread_data)
            # Check if spread has expired (use long leg expiry)
            if spread.long_leg.expiry and spread.long_leg.expiry < current_date:
                self.log(
                    f"OPT: ZOMBIE_CLEAR - Spread position expired {spread.long_leg.expiry} < {current_date}. "
                    "Clearing stale spread."
                )
                self._spread_position = None
            else:
                self._spread_position = spread
                self.log(
                    f"OPT: STATE_RESTORE - Spread position restored | "
                    f"{spread.spread_type} x{spread.num_spreads} | Expiry={spread.long_leg.expiry}"
                )
        else:
            self._spread_position = None

        mode_value = state.get("current_mode", "SWING")
        self._current_mode = OptionsMode(mode_value)

        micro_state_data = state.get("micro_regime_state")
        if micro_state_data:
            self._micro_regime_engine._state = MicroRegimeState.from_dict(micro_state_data)

        # Market open data
        self._vix_at_open = state.get("vix_at_open", 0.0)
        self._spy_at_open = state.get("spy_at_open", 0.0)
        self._spy_gap_pct = state.get("spy_gap_pct", 0.0)

    def reset(self) -> None:
        """Reset engine state."""
        # Legacy
        self._position = None
        self._trades_today = 0
        self._last_trade_date = None

        # V2.1.1
        self._swing_position = None
        self._intraday_position = None
        self._intraday_trades_today = 0
        self._current_mode = OptionsMode.SWING
        self._micro_regime_engine.reset_daily()
        self._vix_at_open = 0.0
        self._spy_at_open = 0.0
        self._spy_gap_pct = 0.0

        # V2.3: Reset spam prevention flags
        self._entry_attempted_today = False
        self._swing_time_warning_logged = False

        # V2.3.2: Reset pending intraday entry flag
        self._pending_intraday_entry = False

        # V2.3.3: Reset pending intraday exit flag
        self._pending_intraday_exit = False

        self.log("OPT: Engine reset - all positions cleared")

    def reset_daily(self, current_date: str) -> None:
        """Reset daily trade counter at start of new day."""
        if current_date != self._last_trade_date:
            self._trades_today = 0
            self._intraday_trades_today = 0
            self._swing_trades_today = 0  # V2.9
            self._total_options_trades_today = 0  # V2.9
            self._last_trade_date = current_date

            # V2.3 FIX: Reset entry attempt flag for new day
            self._entry_attempted_today = False
            self._swing_time_warning_logged = False
            # V2.21: Clear rejection margin cap for new day
            self._rejection_margin_cap = None

            # V2.3.2: Reset pending intraday entry flag
            self._pending_intraday_entry = False

            # V2.3.3: Reset pending intraday exit flag
            self._pending_intraday_exit = False

            # Reset Micro Regime Engine for new day
            self._micro_regime_engine.reset_daily()

            # V2.4.3: Clear spread failure cooldown for new day
            self._spread_failure_cooldown_until = None
            self._last_spread_scan_time = None

            # Clear intraday position (should not exist overnight)
            if self._intraday_position is not None:
                self.log("OPT: WARNING - Intraday position found at daily reset, clearing")
                self._intraday_position = None

            self.log(f"OPT: Daily reset for {current_date}")

    # =========================================================================
    # V2.9: TRADE COUNTER ENFORCEMENT (Bug #4 Fix)
    # =========================================================================

    def _increment_trade_counter(self, mode: OptionsMode) -> None:
        """
        V2.9: Increment trade counters when a trade is executed.

        Called from register_spread_position() and register_entry() after fills.

        Args:
            mode: The trading mode (SWING or INTRADAY).
        """
        # Increment new counters (V2.9)
        self._total_options_trades_today += 1

        # Backward compatibility: Also increment old counter used by state persistence
        self._trades_today += 1

        if mode == OptionsMode.INTRADAY:
            self._intraday_trades_today += 1
            self.log(
                f"TRADE_COUNTER: Intraday={self._intraday_trades_today}/{config.INTRADAY_MAX_TRADES_PER_DAY} | "
                f"Total={self._total_options_trades_today}/{config.MAX_OPTIONS_TRADES_PER_DAY}"
            )
        else:
            self._swing_trades_today += 1
            self.log(
                f"TRADE_COUNTER: Swing={self._swing_trades_today}/{config.MAX_SWING_TRADES_PER_DAY} | "
                f"Total={self._total_options_trades_today}/{config.MAX_OPTIONS_TRADES_PER_DAY}"
            )

    def _can_trade_options(self, mode: OptionsMode) -> bool:
        """
        V2.9: Check if trading is allowed based on daily limits.

        Prevents over-trading when VIX flickers around strategy thresholds.

        Args:
            mode: The trading mode to check.

        Returns:
            True if trading is allowed, False if limits exceeded.
        """
        # Check global limit
        if self._total_options_trades_today >= config.MAX_OPTIONS_TRADES_PER_DAY:
            self.log(
                f"TRADE_LIMIT: Global limit reached | "
                f"{self._total_options_trades_today}/{config.MAX_OPTIONS_TRADES_PER_DAY}"
            )
            return False

        # Check mode-specific limits
        if mode == OptionsMode.INTRADAY:
            if self._intraday_trades_today >= config.INTRADAY_MAX_TRADES_PER_DAY:
                self.log(
                    f"TRADE_LIMIT: Intraday limit reached | "
                    f"{self._intraday_trades_today}/{config.INTRADAY_MAX_TRADES_PER_DAY}"
                )
                return False
        else:  # SWING
            if self._swing_trades_today >= config.MAX_SWING_TRADES_PER_DAY:
                self.log(
                    f"TRADE_LIMIT: Swing limit reached | "
                    f"{self._swing_trades_today}/{config.MAX_SWING_TRADES_PER_DAY}"
                )
                return False

        return True
