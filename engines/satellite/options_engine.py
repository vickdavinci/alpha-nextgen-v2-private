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
- Same-day entry and exit (must close by configured intraday cutoff)
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
from engines.satellite.intraday_exit_profile import (
    apply_intraday_stop_overrides_impl,
    apply_intraday_target_overrides_impl,
    get_intraday_exit_profile_impl,
    get_trail_config_impl,
)
from engines.satellite.itm_horizon_engine import ITMHorizonEngine
from engines.satellite.iv_sensor import IVSensor, VIXSnapshot
from engines.satellite.micro_entry_engine import MicroEntryEngine
from engines.satellite.options_exit_evaluator import check_exit_signals_impl
from engines.satellite.options_intraday_entry import check_intraday_entry_signal_impl
from engines.satellite.vass_assignment_manager import (
    check_assignment_margin_buffer_impl,
    check_assignment_risk_exit_impl,
    check_overnight_itm_short_risk_impl,
    check_premarket_itm_shorts_impl,
    check_short_leg_itm_exit_impl,
    get_assignment_aware_size_multiplier_impl,
    handle_partial_assignment_impl,
    is_short_leg_deep_itm_impl,
)
from engines.satellite.vass_entry_engine import VASSEntryEngine
from engines.satellite.vass_exit_evaluator import check_spread_exit_signals_impl
from engines.satellite.vass_exit_profile import (
    get_vass_exit_profile_impl,
    record_vass_mfe_diag_impl,
    resolve_qqq_atr_pct_impl,
)
from engines.satellite.vass_signal_validator import (
    check_credit_spread_entry_signal_impl,
    check_spread_entry_signal_impl,
)
from models.enums import (
    OptionDirection,  # V6.4: Unified from models.enums (fixes P0 duplicate enum bug)
)
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

# V6.4: OptionDirection moved to models.enums (fixes P0 duplicate enum bug)
# Previously defined here, caused type mismatch when comparing enums


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
    is_closing: bool = False  # V2.12 Fix #2: Prevent duplicate exit signals
    highest_pnl_pct: float = 0.0  # V9.4: Track high-water mark for trailing stop
    highest_pnl_max_profit_pct: float = 0.0  # V10.15: MFE as % of max profit
    mfe_lock_tier: int = 0  # V10.15: 0=none, 1=breakeven+fees, 2=harvest floor

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
            "is_closing": self.is_closing,
            "highest_pnl_pct": self.highest_pnl_pct,
            "highest_pnl_max_profit_pct": self.highest_pnl_max_profit_pct,
            "mfe_lock_tier": self.mfe_lock_tier,
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
            is_closing=data.get("is_closing", False),  # V2.12: Default False for backwards compat
            highest_pnl_pct=data.get("highest_pnl_pct", 0.0),  # V9.4: Trailing stop HWM
            highest_pnl_max_profit_pct=data.get("highest_pnl_max_profit_pct", 0.0),
            mfe_lock_tier=int(data.get("mfe_lock_tier", 0) or 0),
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
                _normalize_intraday_strategy_value(data.get("recommended_strategy", "NO_TRADE"))
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
            from engines.satellite.options_engine import OptionDirection

            state.recommended_direction = OptionDirection(rec_dir)
        return state


def _normalize_intraday_strategy_value(strategy_value: Any) -> str:
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
            if use_otm:
                max_vix = float(getattr(config, "MICRO_OTM_MOMENTUM_MAX_VIX", 22.0))
                min_move = float(getattr(config, "MICRO_OTM_MOMENTUM_MIN_MOVE", 0.40))
                if direction == OptionDirection.CALL:
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
                    self._record_regime_decision(
                        engine="MICRO",
                        decision="ALLOW",
                        strategy_attempted=f"MICRO_OTM_MOMENTUM_{direction.value}",
                        gate_name="MICRO_OTM_CONFIRMED",
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

        # V6.5: Only apply divergence/confirmation logic in tradeable regimes
        # Choppy/caution regimes should skip this and fall through to RULE 7
        tradeable_regimes = {
            MicroRegime.PERFECT_MR,
            MicroRegime.GOOD_MR,
            MicroRegime.NORMAL,
            MicroRegime.RECOVERING,
            MicroRegime.IMPROVING,
            MicroRegime.PANIC_EASING,  # Can fade after panic subsides
            MicroRegime.CALMING,  # Can trade when fear is decreasing
            MicroRegime.CAUTIOUS,  # V6.10 P3: Added - VIX medium + stable (allow credits)
            MicroRegime.TRANSITION,  # Allow participation during mild handoff regimes
            MicroRegime.CAUTION_LOW,  # V9: tradeable with reduced size, universal gates handle quality
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
        self._intraday_position_engine: Optional[str] = None  # MICRO/ITM ownership
        # Engine-isolated intraday position containers.
        self._intraday_positions: Dict[str, List[OptionsPosition]] = {
            "MICRO": [],
            "ITM": [],
        }

        # V2.3: Spread position tracking (replaces single-leg for swing mode)
        self._spread_position: Optional[SpreadPosition] = None
        self._spread_positions: List[SpreadPosition] = []
        # Reserved compatibility slot for legacy persisted state payloads.

        # Legacy single position (for backwards compatibility)
        self._position: Optional[OptionsPosition] = None

        # Trade counters
        self._trades_today: int = 0
        self._intraday_trades_today: int = 0
        self._intraday_call_trades_today: int = 0
        self._intraday_put_trades_today: int = 0
        self._intraday_itm_trades_today: int = 0
        self._intraday_micro_trades_today: int = 0
        self._swing_trades_today: int = 0  # V2.9: Swing mode counter
        self._total_options_trades_today: int = 0  # V2.9: Global counter (Bug #4 fix)
        self._last_trade_date: Optional[str] = None

        # Current operating mode
        self._current_mode: OptionsMode = OptionsMode.SWING

        # V2.1.1: Micro Regime Engine for intraday trading
        self._micro_regime_engine = MicroRegimeEngine(
            log_func=self.log, regime_decision_cb=self._record_regime_decision
        )

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
        self._pending_entry_strategy: Optional[str] = None

        # V2.3: Pending spread entry state
        self._pending_spread_long_leg: Optional[OptionContract] = None
        self._pending_spread_short_leg: Optional[OptionContract] = None
        self._pending_spread_type: Optional[str] = None
        self._pending_net_debit: Optional[float] = None
        self._pending_max_profit: Optional[float] = None
        self._pending_spread_width: Optional[float] = None
        self._pending_spread_entry_vix: Optional[float] = None
        self._pending_spread_entry_since: Optional[datetime] = None

        # V2.3 FIX: Prevent order spam - track failed entry attempts
        self._entry_attempted_today: bool = False
        self._spread_attempts_today_by_key: Dict[str, int] = {}
        # V2.21: Post-rejection margin cap for adaptive retry sizing
        self._rejection_margin_cap: Optional[float] = None
        self._swing_time_warning_logged: bool = False

        # V2.3.21: Spread scan throttle - only attempt every 15 minutes to reduce log noise
        self._last_spread_scan_time: Optional[str] = None

        # V2.4.3: Spread FAILURE cooldown - don't retry for 4 hours after construction fails
        # Prevents 340+ retries when no valid contracts exist
        self._spread_failure_cooldown_until: Optional[str] = None
        self._spread_failure_cooldown_until_by_dir: dict = {}
        self._last_spread_failure_stats: Optional[str] = None
        self._last_credit_failure_stats: Optional[str] = None
        self._last_entry_validation_failure: Optional[str] = None
        self._last_intraday_validation_failure_by_lane: Dict[str, Optional[str]] = {
            "MICRO": None,
            "ITM": None,
        }
        self._last_intraday_validation_detail_by_lane: Dict[str, Optional[str]] = {
            "MICRO": None,
            "ITM": None,
        }
        # Detailed slot/limit rejection context for MICRO drop telemetry.
        self._last_trade_limit_failure: Optional[str] = None
        self._last_trade_limit_detail: Optional[str] = None
        self._last_micro_no_trade_log_by_key: Dict[str, str] = {}
        # MICRO anti-churn: brief cooldown for same strategy after close.
        self._last_intraday_close_time: Optional[datetime] = None
        self._last_intraday_close_strategy: Optional[str] = None

        # V2.3.2 FIX #4: Track if pending entry is intraday (for correct position registration)
        self._pending_intraday_entry: bool = False
        self._pending_intraday_entry_since: Optional[datetime] = None
        self._pending_intraday_entry_engine: Optional[str] = None  # MICRO/ITM lane
        self._pending_intraday_entries: Dict[str, Dict[str, Any]] = {}  # symbol -> pending payload

        # V2.3.3 FIX #3: Prevent duplicate exit signals while waiting for fill
        self._pending_intraday_exit: bool = False
        self._pending_intraday_exit_engine: Optional[str] = None  # MICRO/ITM lane
        self._pending_intraday_exit_lanes: set = set()
        self._pending_intraday_exit_symbols: set = set()
        self._transition_context_snapshot: Optional[Dict[str, Any]] = None

        # V2.6 Bug #16: Post-trade margin cooldown tracking
        # After closing a spread, wait before new entry (T+1 settlement)
        self._last_spread_exit_time: Optional[str] = None

        # V2.8: VASS (Volatility-Adaptive Strategy Selection)
        self._iv_sensor = IVSensor(
            smoothing_minutes=config.VASS_IV_SMOOTHING_MINUTES,
            log_func=self.log,
        )
        self._itm_horizon_engine = ITMHorizonEngine(log_func=self.log)
        self._micro_entry_engine = MicroEntryEngine(log_func=self.log)
        self._vass_entry_engine = VASSEntryEngine(log_func=self.log)
        self._vass_entry_engine_enabled = bool(getattr(config, "VASS_ENTRY_ENGINE_ENABLED", True))

        # V2.27: Win Rate Gate - rolling spread trade result tracker
        self._spread_result_history: List[bool] = []  # True=win, False=loss
        self._win_rate_shutoff: bool = False  # True when win rate < shutoff threshold
        self._win_rate_shutoff_date: Optional[str] = None  # YYYY-MM-DD shutoff activation
        self._paper_track_history: List[bool] = []  # Paper trades during shutoff
        self._last_win_rate_monitor_log_key: Optional[str] = None
        # V10.10: VASS anti-cluster/day-gap memory is owned by VASSEntryEngine.
        # Phase C: staged neutrality de-risk memory by spread key.
        # key = "<long_symbol>|<short_symbol>", value = first neutrality timestamp.
        self._spread_neutrality_warn_by_key: Dict[str, datetime] = {}

        # V6.5 FIX: Prevent gamma pin exit from firing every minute
        # Once triggered, don't trigger again for the same position
        self._gamma_pin_exit_triggered: bool = False
        # Throttle repeated ITM short-leg risk logs.
        self._last_short_leg_itm_exit_log: Dict[str, datetime] = {}
        # CALL-gate tracking: pause new CALLs after repeated losses.
        self._call_consecutive_losses: int = 0
        self._call_cooldown_until_date: Optional[datetime.date] = None
        self._put_consecutive_losses: int = 0
        self._put_cooldown_until_date: Optional[datetime.date] = None
        # V10.7: Swing loss breaker for VASS spread entries.

        # V9.4 P0: Exit signal cooldown — prevent per-minute spam when close order fails.
        # Maps spread_key -> last exit signal datetime. If exit fires and close fails,
        # don't re-fire for SPREAD_EXIT_RETRY_MINUTES.
        self._spread_exit_signal_cooldown: Dict[str, datetime] = {}
        # Track which spreads have already logged the hold guard (log once, not every minute)
        self._spread_hold_guard_logged: set = set()
        # Throttle force-exit hold-skip logs (symbol -> YYYY-MM-DD).
        self._intraday_force_exit_hold_skip_log_date: Dict[str, str] = {}
        # Throttle expiration-hammer skip logs for MICRO intraday strategies.
        self._expiring_hammer_skip_log_date: Dict[str, str] = {}

    def log(self, message: str, trades_only: bool = False) -> None:
        """
        Log via algorithm with LiveMode awareness.

        Args:
            message: Log message to output.
            trades_only: If True, always log (for trade entries/exits/errors).
                        If False, only log in LiveMode (for diagnostics).
        """
        if self.algorithm:
            is_live = bool(hasattr(self.algorithm, "LiveMode") and self.algorithm.LiveMode)
            text = str(message or "")
            # V2.18.1: Fixed - was logging everything in debug mode, causing backtest timeout
            # Only log if: trades_only=True OR we're in LiveMode
            if trades_only:
                if (not is_live) and text.startswith("WIN_RATE_GATE:"):
                    helper = getattr(self.algorithm, "_log_high_frequency_event", None)
                    if callable(helper):
                        gate_key = text.split("|", 1)[0].strip().replace(" ", "_")
                        helper(
                            config_flag="LOG_WIN_RATE_GATE_BACKTEST_ENABLED",
                            category="WIN_RATE_GATE",
                            reason_key=gate_key,
                            message=text,
                        )
                    elif bool(getattr(config, "LOG_WIN_RATE_GATE_BACKTEST_ENABLED", False)):
                        self.algorithm.Log(text)
                else:
                    self.algorithm.Log(text)
            elif is_live:
                self.algorithm.Log(text)
            # In backtest mode with trades_only=False, skip logging (silent)

    def _symbol_str(self, symbol) -> str:
        """Normalize QC Symbol/string-like values to plain string for TargetWeight."""
        if symbol is None:
            return ""
        if isinstance(symbol, str):
            return symbol.strip().upper()
        try:
            return str(symbol).strip().upper()
        except Exception:
            return ""

    def _symbol_key(self, symbol) -> str:
        """
        Canonical symbol key for internal comparisons/maps.

        Collapses repeated whitespace so fills/order events and contract strings
        that format spacing differently still match the same option contract.
        """
        text = self._symbol_str(symbol)
        if not text:
            return ""
        return " ".join(text.split())

    def _get_intraday_force_exit_hhmm(self) -> Tuple[int, int]:
        """Return configured intraday force-exit time as (hour, minute)."""
        force_exit_cfg = str(getattr(config, "INTRADAY_FORCE_EXIT_TIME", "15:15"))
        try:
            hh, mm = force_exit_cfg.split(":")
            return int(hh), int(mm)
        except Exception:
            return 15, 15

    def _canonical_intraday_strategy(
        self, strategy: Optional["IntradayStrategy"]
    ) -> Optional["IntradayStrategy"]:
        """Map legacy strategy aliases to canonical runtime strategy."""
        if strategy is None:
            return None
        value = _normalize_intraday_strategy_value(getattr(strategy, "value", strategy))
        return IntradayStrategy(value)

    def _canonical_intraday_strategy_name(self, strategy_name: Optional[str]) -> str:
        """Canonical string form used by hold/exit logic."""
        return _normalize_intraday_strategy_value(strategy_name)

    def _is_itm_momentum_strategy_name(self, strategy_name: Optional[str]) -> bool:
        """True when strategy name maps to ITM momentum."""
        value = self._canonical_intraday_strategy_name(strategy_name)
        return value == IntradayStrategy.ITM_MOMENTUM.value

    def _intraday_engine_lane_from_strategy(self, strategy_name: Optional[str]) -> str:
        """Map strategy to engine lane key used by pending-entry/exit locks."""
        return "ITM" if self._is_itm_momentum_strategy_name(strategy_name) else "MICRO"

    def _pending_intraday_entry_key(self, symbol: str, lane: Optional[str]) -> str:
        """Build stable pending-entry key with lane isolation."""
        symbol_norm = self._symbol_key(symbol)
        lane_norm = str(lane or "").upper()
        if not lane_norm:
            return symbol_norm
        return f"{lane_norm}|{symbol_norm}"

    def _pending_intraday_symbol_from_key(self, key: str) -> str:
        key_text = str(key or "")
        if "|" in key_text:
            return self._symbol_key(key_text.split("|", 1)[1])
        return self._symbol_key(key_text)

    def _find_pending_intraday_entry_key(
        self, symbol: str, lane: Optional[str] = None
    ) -> Optional[str]:
        """Find pending-entry key by symbol (+ optional lane), backward compatible."""
        symbol_norm = self._symbol_key(symbol)
        if not symbol_norm:
            return None
        lane_norm = str(lane or "").upper()
        if lane_norm:
            direct_key = self._pending_intraday_entry_key(symbol_norm, lane_norm)
            if direct_key in self._pending_intraday_entries:
                return direct_key

        if symbol_norm in self._pending_intraday_entries:
            payload = self._pending_intraday_entries.get(symbol_norm) or {}
            payload_lane = str(payload.get("lane", "")).upper()
            if not lane_norm or payload_lane == lane_norm:
                return symbol_norm

        for key, payload in self._pending_intraday_entries.items():
            payload_sym = (
                self._symbol_key(payload.get("symbol", "")) if isinstance(payload, dict) else ""
            )
            if not payload_sym:
                payload_sym = self._pending_intraday_symbol_from_key(key)
            if payload_sym != symbol_norm:
                continue
            if lane_norm:
                payload_lane = str((payload or {}).get("lane", "")).upper()
                if payload_lane and payload_lane != lane_norm:
                    continue
            return key
        return None

    def _get_pending_intraday_entry_payload(
        self, symbol: str, lane: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        key = self._find_pending_intraday_entry_key(symbol=symbol, lane=lane)
        if not key:
            return None
        payload = self._pending_intraday_entries.get(key)
        return payload if isinstance(payload, dict) else None

    def _pop_pending_intraday_entry_payload(
        self, symbol: str, lane: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        key = self._find_pending_intraday_entry_key(symbol=symbol, lane=lane)
        if not key:
            return None
        payload = self._pending_intraday_entries.pop(key, None)
        return payload if isinstance(payload, dict) else None

    def _refresh_legacy_intraday_mirrors(self) -> None:
        """Keep legacy single-position mirrors in sync with lane containers."""
        if self._intraday_positions.get("ITM"):
            self._intraday_position = self._intraday_positions["ITM"][0]
            self._intraday_position_engine = "ITM"
            return
        if self._intraday_positions.get("MICRO"):
            self._intraday_position = self._intraday_positions["MICRO"][0]
            self._intraday_position_engine = "MICRO"
            return
        self._intraday_position = None
        self._intraday_position_engine = None

    def _get_intraday_lane_position(self, lane: str) -> Optional[OptionsPosition]:
        lane_key = str(lane or "").upper()
        lane_positions = self._intraday_positions.get(lane_key) or []
        if lane_positions:
            return lane_positions[0]
        return None

    def _set_intraday_lane_position(self, lane: str, position: Optional[OptionsPosition]) -> None:
        lane_key = str(lane or "").upper()
        if lane_key not in self._intraday_positions:
            self._intraday_positions[lane_key] = []
        if position is None:
            self._intraday_positions[lane_key] = []
            self._refresh_legacy_intraday_mirrors()
            return
        self._intraday_positions[lane_key].append(position)
        self._intraday_position = position
        self._intraday_position_engine = lane_key

    def _find_intraday_lane_by_symbol(self, symbol: str) -> Optional[str]:
        symbol_norm = self._symbol_key(symbol)
        if not symbol_norm:
            return None
        for lane in ("ITM", "MICRO"):
            for pos in self._intraday_positions.get(lane) or []:
                if (
                    pos is not None
                    and pos.contract is not None
                    and self._symbol_key(pos.contract.symbol) == symbol_norm
                ):
                    return lane
        return None

    def get_intraday_positions(self) -> List[OptionsPosition]:
        positions: List[OptionsPosition] = []
        for lane in ("ITM", "MICRO"):
            lane_positions = self._intraday_positions.get(lane) or []
            for pos in lane_positions:
                if pos is not None:
                    positions.append(pos)
        return positions

    def _infer_intraday_strategy_from_order_tag(self, order_tag: Optional[str]) -> str:
        """Best-effort strategy inference from order tag for partial-fill OCO recovery."""
        text = str(order_tag or "").upper()
        if not text:
            return IntradayStrategy.NO_TRADE.value
        if "PROTECTIVE_PUTS" in text or text.startswith("HEDGE:"):
            return IntradayStrategy.PROTECTIVE_PUTS.value
        if "ITM_MOMENTUM" in text or "DEBIT_MOMENTUM" in text:
            return IntradayStrategy.ITM_MOMENTUM.value
        if "MICRO_OTM_MOMENTUM" in text:
            return IntradayStrategy.MICRO_OTM_MOMENTUM.value
        if "MICRO_EOD_SWEEP" in text:
            return IntradayStrategy.MICRO_OTM_MOMENTUM.value
        if "MICRO_DEBIT_FADE" in text or "DEBIT_FADE" in text:
            return IntradayStrategy.MICRO_DEBIT_FADE.value
        return IntradayStrategy.NO_TRADE.value

    def _get_position_live_dte(self, position: Optional[OptionsPosition]) -> Optional[int]:
        """Best-effort live DTE using expiry date and current algorithm time."""
        if position is None or position.contract is None:
            return None
        expiry_str = str(getattr(position.contract, "expiry", "") or "")[:10]
        if not expiry_str:
            return None
        try:
            expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d").date()
            if self.algorithm is not None and hasattr(self.algorithm, "Time"):
                current_date = self.algorithm.Time.date()
                return max(0, (expiry_date - current_date).days)
            # QC-compliant fallback for unit contexts without algorithm clock.
            return max(0, int(getattr(position.contract, "days_to_expiry", 0)))
        except Exception:
            try:
                return max(0, int(getattr(position.contract, "days_to_expiry", 0)))
            except Exception:
                return None

    def should_hold_intraday_overnight(
        self,
        position: Optional[OptionsPosition] = None,
    ) -> bool:
        """
        Return True when the current intraday position is eligible for overnight hold.

        V10.1 policy: only ITM_MOMENTUM positions opened with sufficient entry DTE can
        bypass the intraday force-close cutoff.
        """
        hold_enabled = bool(getattr(config, "INTRADAY_ITM_HOLD_OVERNIGHT_ENABLED", False))
        if self._itm_horizon_engine.enabled():
            hold_enabled = bool(getattr(config, "ITM_HOLD_OVERNIGHT_ENABLED", True))
        if not hold_enabled:
            return False
        pos = position if position is not None else self._intraday_position
        if pos is None:
            return False
        if not self._is_itm_momentum_strategy_name(getattr(pos, "entry_strategy", "")):
            return False
        try:
            entry_dte = int(getattr(pos.contract, "days_to_expiry", 0))
        except Exception:
            entry_dte = 0
        live_dte = self._get_position_live_dte(pos)
        if self._itm_horizon_engine.enabled():
            return self._itm_horizon_engine.should_hold_overnight(
                entry_dte=entry_dte,
                live_dte=live_dte,
            )

        min_entry_dte = int(getattr(config, "INTRADAY_ITM_HOLD_MIN_ENTRY_DTE", 3))
        if entry_dte < min_entry_dte:
            return False
        if live_dte is None:
            return False
        force_exit_dte = int(getattr(config, "INTRADAY_ITM_FORCE_EXIT_DTE", 1))
        return live_dte > force_exit_dte

    def record_intraday_result(
        self,
        symbol: str,
        is_win: bool,
        current_time: Optional[str] = None,
        strategy: Optional[str] = None,
    ) -> None:
        """Track MICRO directional loss streaks/cooldowns (ITM is sovereign)."""
        try:
            strategy_name = self._canonical_intraday_strategy_name(strategy)
            if strategy_name not in (
                IntradayStrategy.MICRO_DEBIT_FADE.value,
                IntradayStrategy.MICRO_OTM_MOMENTUM.value,
                IntradayStrategy.PROTECTIVE_PUTS.value,
            ):
                return

            symbol_text = str(symbol)
            import re

            is_call = re.search(r"\d{6}C\d{8}", symbol_text) is not None
            is_put = re.search(r"\d{6}P\d{8}", symbol_text) is not None

            if current_time:
                try:
                    trade_date = datetime.strptime(current_time[:10], "%Y-%m-%d").date()
                except Exception:
                    trade_date = None
            else:
                trade_date = None

            # expose directional cooldown info to MicroEntryEngine via state payload
            try:
                state = getattr(self._micro_regime_engine, "_state", None)
                if state is not None:
                    state.put_cooldown_until_date = self._put_cooldown_until_date
                    state.put_consecutive_losses = self._put_consecutive_losses
            except Exception:
                pass

            if is_call:
                if is_win:
                    self._call_consecutive_losses = 0
                else:
                    self._call_consecutive_losses += 1
                    if getattr(config, "CALL_GATE_CONSECUTIVE_LOSS_ENABLED", True):
                        threshold = int(getattr(config, "CALL_GATE_CONSECUTIVE_LOSSES", 3))
                        if self._call_consecutive_losses >= threshold:
                            vix_for_cooldown = None
                            try:
                                state = getattr(self._micro_regime_engine, "_state", None)
                                if state is not None:
                                    vix_for_cooldown = float(
                                        getattr(state, "vix_current", 0.0) or 0.0
                                    )
                            except Exception:
                                vix_for_cooldown = None

                            if vix_for_cooldown is None or vix_for_cooldown <= 0:
                                cooldown_days = int(
                                    getattr(config, "CALL_GATE_LOSS_COOLDOWN_DAYS", 1)
                                )
                            else:
                                low_vix_max = float(getattr(config, "VIX_LEVEL_LOW_MAX", 18.0))
                                med_vix_max = float(getattr(config, "VIX_LEVEL_MEDIUM_MAX", 25.0))
                                if vix_for_cooldown < low_vix_max:
                                    cooldown_days = int(
                                        getattr(
                                            config,
                                            "CALL_GATE_LOSS_COOLDOWN_DAYS_LOW_VIX",
                                            getattr(config, "CALL_GATE_LOSS_COOLDOWN_DAYS", 1),
                                        )
                                    )
                                elif vix_for_cooldown < med_vix_max:
                                    cooldown_days = int(
                                        getattr(
                                            config,
                                            "CALL_GATE_LOSS_COOLDOWN_DAYS_MED_VIX",
                                            getattr(config, "CALL_GATE_LOSS_COOLDOWN_DAYS", 1),
                                        )
                                    )
                                else:
                                    cooldown_days = int(
                                        getattr(
                                            config,
                                            "CALL_GATE_LOSS_COOLDOWN_DAYS_HIGH_VIX",
                                            getattr(config, "CALL_GATE_LOSS_COOLDOWN_DAYS", 1),
                                        )
                                    )

                            if trade_date is not None:
                                self._call_cooldown_until_date = self._add_trading_days_to_date(
                                    trade_date, cooldown_days
                                )
                                self.log(
                                    f"INTRADAY: CALL cooldown armed | LossStreak={self._call_consecutive_losses} | "
                                    f"Until={self._call_cooldown_until_date.isoformat()}",
                                    trades_only=True,
                                )

            if is_put:
                if is_win:
                    self._put_consecutive_losses = 0
                else:
                    self._put_consecutive_losses += 1
                    if getattr(config, "PUT_GATE_CONSECUTIVE_LOSS_ENABLED", True):
                        threshold = int(getattr(config, "PUT_GATE_CONSECUTIVE_LOSSES", 3))
                        if self._put_consecutive_losses >= threshold and trade_date is not None:
                            vix_for_cooldown = None
                            try:
                                state = getattr(self._micro_regime_engine, "_state", None)
                                if state is not None:
                                    vix_for_cooldown = float(
                                        getattr(state, "vix_current", 0.0) or 0.0
                                    )
                            except Exception:
                                vix_for_cooldown = None

                            if vix_for_cooldown is None or vix_for_cooldown <= 0:
                                cooldown_days = int(
                                    getattr(config, "PUT_GATE_LOSS_COOLDOWN_DAYS", 1)
                                )
                            else:
                                low_vix_max = float(getattr(config, "VIX_LEVEL_LOW_MAX", 18.0))
                                med_vix_max = float(getattr(config, "VIX_LEVEL_MEDIUM_MAX", 25.0))
                                if vix_for_cooldown < low_vix_max:
                                    cooldown_days = int(
                                        getattr(
                                            config,
                                            "PUT_GATE_LOSS_COOLDOWN_DAYS_LOW_VIX",
                                            getattr(config, "PUT_GATE_LOSS_COOLDOWN_DAYS", 1),
                                        )
                                    )
                                elif vix_for_cooldown < med_vix_max:
                                    cooldown_days = int(
                                        getattr(
                                            config,
                                            "PUT_GATE_LOSS_COOLDOWN_DAYS_MED_VIX",
                                            getattr(config, "PUT_GATE_LOSS_COOLDOWN_DAYS", 1),
                                        )
                                    )
                                else:
                                    cooldown_days = int(
                                        getattr(
                                            config,
                                            "PUT_GATE_LOSS_COOLDOWN_DAYS_HIGH_VIX",
                                            getattr(config, "PUT_GATE_LOSS_COOLDOWN_DAYS", 1),
                                        )
                                    )

                            self._put_cooldown_until_date = self._add_trading_days_to_date(
                                trade_date, cooldown_days
                            )
                            self.log(
                                f"INTRADAY: PUT cooldown armed | LossStreak={self._put_consecutive_losses} | "
                                f"Until={self._put_cooldown_until_date.isoformat()}",
                                trades_only=True,
                            )

            try:
                state = getattr(self._micro_regime_engine, "_state", None)
                if state is not None:
                    state.put_cooldown_until_date = self._put_cooldown_until_date
                    state.put_consecutive_losses = self._put_consecutive_losses
            except Exception:
                pass

        except Exception as e:
            self.log(f"INTRADAY: Failed to record directional result streak: {e}", trades_only=True)

        try:
            self._itm_horizon_engine.on_trade_closed(
                symbol=symbol,
                is_win=is_win,
                current_time=current_time,
                strategy=strategy,
                algorithm=self.algorithm,
            )
        except Exception as e:
            self.log(f"ITM_ENGINE: result tracking failed: {e}", trades_only=True)

    # =========================================================================
    # V6.10 P5: CHOPPY MARKET DETECTION
    # =========================================================================

    def get_choppy_market_scale(self) -> float:
        """
        V6.10 P5: Detect choppy market conditions and return size scale factor.

        Uses the MicroRegimeEngine's whipsaw detection to identify choppy markets.
        When market is choppy/whipsawing, reduces position size to limit losses.

        Returns:
            1.0 for normal markets, CHOPPY_SIZE_REDUCTION (0.5) for choppy markets.
        """
        # Check if filter is enabled
        if not getattr(config, "CHOPPY_MARKET_FILTER_ENABLED", False):
            return 1.0

        # Get whipsaw state from Micro Regime Engine
        try:
            whipsaw_state, reversal_count = self._micro_regime_engine._detect_whipsaw()

            # Check against configured threshold
            choppy_threshold = getattr(config, "CHOPPY_REVERSAL_COUNT", 3)

            if reversal_count >= choppy_threshold:
                reduction = getattr(config, "CHOPPY_SIZE_REDUCTION", 0.50)
                self.log(
                    f"CHOPPY_FILTER: Size reduction | Reversals={reversal_count} >= {choppy_threshold} | "
                    f"Scale={reduction:.0%} | WhipsawState={whipsaw_state.value}",
                    trades_only=True,
                )
                return reduction

        except Exception as e:
            # If detection fails, don't apply reduction
            self.log(f"CHOPPY_FILTER: Detection error - {e}", trades_only=True)

        return 1.0

    # =========================================================================
    # V5.3: CONVICTION & POSITION MANAGEMENT
    # =========================================================================

    def resolve_trade_signal(
        self,
        engine: str,  # "MICRO" or "VASS"
        engine_direction: Optional[str],  # "BULLISH" or "BEARISH"
        engine_conviction: bool,
        macro_direction: str,  # "BULLISH", "BEARISH", or "NEUTRAL"
        conviction_strength: Optional[float] = None,  # V6.9: For NEUTRAL VETO gating (e.g., UVXY %)
        engine_regime: Optional[str] = None,  # V6.15: Micro regime name for NEUTRAL veto gating
        engine_recommended_direction: Optional[
            str
        ] = None,  # V6.15: Ensure veto aligns with engine direction
        overlay_state: Optional[
            str
        ] = None,  # V6.22: Fast overlay (NORMAL/EARLY_STRESS/STRESS/RECOVERY)
        allow_macro_veto: bool = True,  # Optional hard-veto guard for conviction overrides
    ) -> Tuple[bool, Optional[str], str]:
        """
        V5.3: Resolve whether to trade based on engine signal vs macro.

        Resolution Logic:
        - ALIGNED: Engine and Macro agree → TRADE
        - MISALIGNED + CONVICTION: Engine overrides Macro → TRADE (veto)
        - MISALIGNED + NO CONVICTION: Uncertainty → NO TRADE

        Args:
            engine: Which engine is signaling ("MICRO" or "VASS")
            engine_direction: Engine's directional view ("BULLISH" or "BEARISH")
            engine_conviction: True if engine has strong signal
            macro_direction: Macro regime's direction

        Returns:
            Tuple of (should_trade, final_direction, reason)
        """
        overlay = str(overlay_state or "").upper()

        # V6.22: Overlay precedence - block bullish VASS routes during STRESS.
        if engine == "VASS" and overlay == "STRESS":
            if engine_direction == "BULLISH":
                return (
                    False,
                    None,
                    "NO_TRADE: E_OVERLAY_STRESS_BULL_BLOCK (VASS bullish blocked in STRESS)",
                )
            if engine_direction is None and macro_direction == "BULLISH":
                return (
                    False,
                    None,
                    "NO_TRADE: E_OVERLAY_STRESS_BULL_BLOCK (Macro bullish blocked in STRESS)",
                )
        # D8: In EARLY_STRESS, require conviction before allowing bullish VASS direction.
        if (
            engine == "VASS"
            and overlay == "EARLY_STRESS"
            and engine_direction == "BULLISH"
            and not engine_conviction
            and bool(getattr(config, "VASS_EARLY_STRESS_BULL_REQUIRE_CONVICTION", True))
        ):
            return (
                False,
                None,
                "NO_TRADE: E_OVERLAY_EARLY_BULL_NO_CONVICTION",
            )

        # V10.7: VASS direction sovereignty.
        # Conviction decides direction; macro direction remains a risk-state input only.
        if engine == "VASS" and bool(getattr(config, "VASS_USE_CONVICTION_ONLY_DIRECTION", False)):
            if engine_conviction and engine_direction in ("BULLISH", "BEARISH"):
                return (
                    True,
                    engine_direction,
                    f"VASS_CONVICTION_DIRECTION: {engine_direction}",
                )

            if bool(getattr(config, "VASS_NO_CONVICTION_NO_TRADE", True)):
                return (
                    False,
                    None,
                    "NO_TRADE: VASS_NO_CONVICTION",
                )

        # V10.9: In CAUTION_LOW, bearish MICRO requires conviction across all macro states.
        if (
            engine == "MICRO"
            and engine_regime == "CAUTION_LOW"
            and engine_direction == "BEARISH"
            and not engine_conviction
        ):
            return (
                False,
                None,
                "NO_TRADE: MICRO CAUTION_LOW bearish requires conviction",
            )

        # No engine direction = follow Macro if it has a clear direction
        if engine_direction is None:
            if macro_direction in ("BULLISH", "BEARISH"):
                return (
                    True,
                    macro_direction,
                    f"FOLLOW_MACRO: {engine} has no direction, following Macro {macro_direction}",
                )
            else:
                return (
                    False,
                    None,
                    f"NO_TRADE: {engine} has no direction, Macro is {macro_direction}",
                )

        # Case 1: Aligned
        if engine_direction == macro_direction:
            return True, engine_direction, f"ALIGNED: {engine} + Macro agree on {engine_direction}"

        # Case 2: Macro is NEUTRAL (no strong opinion)
        if macro_direction == "NEUTRAL":
            if engine_conviction:
                if not allow_macro_veto:
                    return (
                        False,
                        None,
                        f"NO_TRADE: {engine} conviction present but hard-veto guard not satisfied",
                    )
                # V6.9: Only allow MICRO to VETO NEUTRAL on extreme UVXY moves
                if engine == "MICRO" and conviction_strength is not None:
                    if abs(conviction_strength) < config.MICRO_UVXY_CONVICTION_EXTREME:
                        return (
                            False,
                            None,
                            f"NO_TRADE: Macro NEUTRAL, MICRO conviction not extreme "
                            f"({conviction_strength:+.1%} < {config.MICRO_UVXY_CONVICTION_EXTREME:.0%})",
                        )
                # V6.15: In NEUTRAL macro, only allow MICRO veto when regime is tradeable
                # and the resolved conviction direction aligns with Micro's own recommendation.
                if engine == "MICRO":
                    tradeable_regimes = {
                        "PERFECT_MR",
                        "GOOD_MR",
                        "NORMAL",
                        "RECOVERING",
                        "IMPROVING",
                        "PANIC_EASING",
                        "CALMING",
                        "CAUTION_LOW",
                        "CAUTIOUS",
                        "ELEVATED",
                        "WORSENING",
                        "TRANSITION",
                    }
                    if engine_regime not in tradeable_regimes:
                        return (
                            False,
                            None,
                            f"NO_TRADE: Macro NEUTRAL, MICRO regime not tradeable ({engine_regime})",
                        )
                    if (
                        engine_recommended_direction is not None
                        and engine_direction != engine_recommended_direction
                    ):
                        return (
                            False,
                            None,
                            "NO_TRADE: Macro NEUTRAL, MICRO conviction direction misaligned",
                        )
                return (
                    True,
                    engine_direction,
                    f"VETO: {engine} conviction ({engine_direction}) overrides NEUTRAL Macro",
                )
            else:
                return (
                    True,
                    engine_direction,
                    f"NEUTRAL_ALIGNED_HALF: Macro NEUTRAL, {engine} no conviction",
                )

        # Case 3: Misaligned with clear Macro direction
        # Micro owns intraday direction. Resolver acts as a risk gate here.
        # V6.14 OPT: In BEARISH macro, block bullish overrides unless conviction is extreme.
        if macro_direction == "BEARISH" and engine_direction == "BULLISH":
            if engine != "MICRO":
                return (
                    False,
                    None,
                    "NO_TRADE: Macro BEARISH blocks CALL override (non-MICRO)",
                )
            if not engine_conviction:
                return (
                    False,
                    None,
                    "NO_TRADE: Macro BEARISH blocks non-conviction MICRO CALL",
                )
            if (
                conviction_strength is None
                or abs(conviction_strength) < config.MICRO_UVXY_CONVICTION_EXTREME
            ):
                return (
                    False,
                    None,
                    "NO_TRADE: Macro BEARISH requires extreme MICRO bullish conviction",
                )

        if engine == "MICRO" and not engine_conviction:
            return (
                False,
                None,
                f"NO_TRADE: MISALIGNED_NO_CONVICTION {engine}={engine_direction}, Macro={macro_direction}",
            )

        if engine_conviction:
            if not allow_macro_veto:
                return (
                    False,
                    None,
                    f"NO_TRADE: {engine} conviction present but hard-veto guard not satisfied",
                )
            # V6.9: Never allow CALL overrides in BEARISH macro (prevent bull bias in bear markets)
            if engine != "MICRO" and macro_direction == "BEARISH" and engine_direction == "BULLISH":
                return (
                    False,
                    None,
                    "NO_TRADE: Macro BEARISH blocks CALL override (V6.9)",
                )
            self.log(
                f"VETO: {engine} conviction ({engine_direction}) overrides Macro ({macro_direction})",
                trades_only=True,
            )
            return (
                True,
                engine_direction,
                f"VETO: {engine} conviction overrides Macro {macro_direction}",
            )
        else:
            return (
                False,
                None,
                f"NO_TRADE: Misaligned ({engine}={engine_direction}, Macro={macro_direction}), no conviction",
            )

    def generate_micro_intraday_signal(
        self,
        vix_current: float,
        vix_open: float,
        qqq_current: float,
        qqq_open: float,
        uvxy_pct: float,
        macro_regime_score: float,
        current_time: str,
        vix_level_override: Optional[float] = None,
        premarket_shock_pct: float = 0.0,
    ) -> Tuple[bool, Optional[OptionDirection], Optional["MicroRegimeState"], str]:
        """
        V6.3: Unified entry point for Micro intraday signal generation.

        Consolidates update, conviction check, and resolution into a single method
        to eliminate dual-update bug. This replaces the scattered orchestration
        logic that was in main.py.

        Flow:
        1. Single update() call on Micro Regime Engine
        2. Check conviction (UVXY/VIX extremes only - no state fallback)
        3. Resolve vs Macro using shared resolver
        4. Apply direction conflict resolution for FADE strategies
        5. Return final decision

        Args:
            vix_current: Current VIX value (UVXY proxy for intraday).
            vix_open: VIX at market open.
            qqq_current: Current QQQ price.
            qqq_open: QQQ at market open.
            uvxy_pct: UVXY intraday change as decimal (0.08 = +8%).
            macro_regime_score: Macro regime score (0-100).
            current_time: Timestamp string.
            vix_level_override: CBOE VIX for consistent level classification.
            premarket_shock_pct: Overnight VIX shock memory as decimal (0.50 = +50%).

        Returns:
            Tuple of (should_trade, direction, state, reason):
            - should_trade: True if a trade should be executed
            - direction: OptionDirection.CALL or .PUT, or None
            - state: MicroRegimeState for logging/debugging
            - reason: Human-readable explanation of decision
        """
        # Step 1: Single update (eliminates dual-update bug)
        # V6.16: Carry overnight panic context into early session.
        # Adjust effective vix_open baseline so large overnight VIX jumps are not "forgotten" at 10:00.
        vix_open_for_micro = vix_open
        if premarket_shock_pct > 0:
            shock_anchor = min(max(getattr(config, "MICRO_SHOCK_MEMORY_ANCHOR", 0.60), 0.0), 1.0)
            memory_scale = 1.0 + premarket_shock_pct * shock_anchor
            if memory_scale > 1.0:
                vix_open_for_micro = vix_open / memory_scale

        # MICRO owns intraday direction, but macro score is still used by policy gates.
        try:
            macro_for_micro = float(macro_regime_score)
        except Exception:
            macro_for_micro = 50.0
        qqq_atr_pct = None
        if self.algorithm is not None and qqq_current > 0:
            try:
                qqq_atr_indicator = getattr(self.algorithm, "qqq_atr", None)
                if qqq_atr_indicator is not None and bool(
                    getattr(qqq_atr_indicator, "IsReady", False)
                ):
                    qqq_atr_value = float(qqq_atr_indicator.Current.Value)
                    if qqq_atr_value > 0:
                        qqq_atr_pct = (qqq_atr_value / float(qqq_current)) * 100.0
            except Exception:
                qqq_atr_pct = None
        state = self._micro_regime_engine.update(
            vix_current=vix_current,
            vix_open=vix_open_for_micro,
            qqq_current=qqq_current,
            qqq_open=qqq_open,
            current_time=current_time,
            macro_regime_score=macro_for_micro,
            vix_level_override=vix_level_override,
            qqq_atr_pct=qqq_atr_pct,
        )

        # V6.8 P0 FIX: If Micro returns NO_TRADE, skip entirely - no conviction override
        # Micro's NO_TRADE decision is final. Reasons include:
        # - VIX floor not met (apathy market)
        # - QQQ move too small (no edge)
        # - QQQ flat, whipsaw, or caution regime
        # With V6.8 lowered gates, Micro will trade more often without needing overrides.
        if state.recommended_strategy == IntradayStrategy.NO_TRADE:
            # Minimal, keyed throttle to preserve distinct MICRO_NO_TRADE reasons.
            should_log = True

            vix_change_pct = (
                (vix_current - vix_open_for_micro) / vix_open_for_micro * 100
                if vix_open_for_micro > 0
                else 0.0
            )
            qqq_move_pct = (qqq_current - qqq_open) / qqq_open * 100 if qqq_open > 0 else 0.0
            if abs(qqq_move_pct) < config.QQQ_NOISE_THRESHOLD:
                block_code = "QQQ_FLAT"
            elif state.micro_regime in (
                MicroRegime.CHOPPY_LOW,
                MicroRegime.RISK_OFF_LOW,
                MicroRegime.BREAKING,
                MicroRegime.UNSTABLE,
                MicroRegime.VOLATILE,
            ):
                block_code = "REGIME_NOT_TRADEABLE"
            elif state.micro_regime in (MicroRegime.FULL_PANIC, MicroRegime.CRASH) and (
                qqq_move_pct >= 0
            ):
                # V9.2: FULL_PANIC/CRASH are tradeable with QQQ-down confirmation.
                # Keep telemetry aligned with gating logic instead of reporting generic non-tradeable.
                block_code = "PANIC_QQQ_GATE"
            elif (
                state.micro_regime
                in (
                    MicroRegime.NORMAL,
                    MicroRegime.CAUTION_LOW,
                    MicroRegime.CAUTIOUS,
                    MicroRegime.TRANSITION,
                    MicroRegime.ELEVATED,
                )
                and abs(vix_change_pct) <= config.VIX_STABLE_BAND_HIGH
            ):
                block_code = "VIX_STABLE_LOW_CONVICTION"
            else:
                block_code = "CONFIRMATION_FAIL"

            if current_time:
                try:
                    throttle_key = f"{current_time[:10]}|{block_code}"
                    last_log = self._last_micro_no_trade_log_by_key.get(throttle_key)
                    if last_log and last_log[:10] == current_time[:10]:
                        curr_min = int(current_time[11:13]) * 60 + int(current_time[14:16])
                        last_min = int(last_log[11:13]) * 60 + int(last_log[14:16])
                        interval_min = int(
                            getattr(config, "MICRO_NO_TRADE_LOG_INTERVAL_MINUTES", 5)
                        )
                        if curr_min - last_min < interval_min:
                            should_log = False
                    if should_log:
                        self._last_micro_no_trade_log_by_key[throttle_key] = current_time
                except (ValueError, IndexError):
                    pass

            if should_log:
                self.log(
                    f"MICRO_NO_TRADE[{block_code}]: Regime={state.micro_regime.value} | "
                    f"VIXchg={vix_change_pct:+.2f}% | QQQ={qqq_move_pct:+.2f}% | "
                    f"Score={state.micro_score:.0f} | Dir=NONE | Why={state.recommended_reason}"
                )
            return (
                False,
                None,
                state,
                f"NO_TRADE: MICRO_BLOCK:{block_code} ({state.micro_regime.value}) | "
                f"Why={state.recommended_reason}",
            )

        # Step 2: Check conviction (now without state-based fallback)
        (
            has_conviction,
            conviction_direction,
            conviction_reason,
        ) = self._micro_regime_engine.has_conviction(
            uvxy_intraday_pct=uvxy_pct,
            vix_level=vix_level_override if vix_level_override else vix_current,
        )

        # Step 3: Resolve direction.
        # V10.10: MICRO sovereignty option bypasses macro resolver.
        recommended_direction_str = (
            "BULLISH"
            if state.recommended_direction == OptionDirection.CALL
            else "BEARISH"
            if state.recommended_direction == OptionDirection.PUT
            else None
        )
        if has_conviction and conviction_direction:
            use_conviction_direction = True
            if (
                recommended_direction_str is not None
                and conviction_direction != recommended_direction_str
            ):
                # V10.5: Require stronger UVXY shock when conviction conflicts with
                # Micro regime direction to reduce wrong-way overrides.
                base_extreme = float(getattr(config, "MICRO_UVXY_CONVICTION_EXTREME", 0.03))
                conflict_mult = float(getattr(config, "MICRO_CONVICTION_CONFLICT_MULT", 1.5))
                required_extreme = base_extreme * conflict_mult
                if abs(uvxy_pct) < required_extreme:
                    use_conviction_direction = False
                    self.log(
                        f"MICRO_CONVICTION_GATED: Conv={conviction_direction} vs Rec={recommended_direction_str} | "
                        f"|UVXY|={abs(uvxy_pct):.1%} < {required_extreme:.1%}",
                        trades_only=True,
                    )
            if use_conviction_direction:
                engine_direction = conviction_direction
            else:
                engine_direction = recommended_direction_str
        else:
            engine_direction = recommended_direction_str

        if engine_direction not in ("BULLISH", "BEARISH"):
            self._record_regime_decision(
                engine="MICRO",
                decision="BLOCK",
                strategy_attempted="MICRO_INTRADAY_SIGNAL",
                gate_name="MICRO_NO_DIRECTION",
            )
            return (
                False,
                None,
                state,
                f"NO_TRADE: MICRO_NO_DIRECTION ({state.micro_regime.value})",
            )
        should_trade = True
        resolved_direction = engine_direction
        resolve_reason = f"MICRO_SOVEREIGN: {resolved_direction}"

        # Step 5: Determine final direction
        # V6.4 FIX: Use resolved_direction whenever set (includes FOLLOW_MACRO path)
        # Previous bug: `has_conviction and resolved_direction` ignored FOLLOW_MACRO cases
        # where resolved_direction was set but has_conviction=False
        if resolved_direction:
            if resolved_direction == "BULLISH":
                final_direction = OptionDirection.CALL
            else:  # BEARISH
                final_direction = OptionDirection.PUT
        else:
            # No resolved direction - use Micro's computed direction from state
            final_direction = state.recommended_direction

        # If still no direction, can't trade
        if final_direction is None:
            self._record_regime_decision(
                engine="MICRO",
                decision="BLOCK",
                strategy_attempted="MICRO_INTRADAY_SIGNAL",
                gate_name="MICRO_FINAL_DIRECTION_NONE",
            )
            return False, None, state, "NO_DIRECTION: Micro has no recommended direction"

        transition_ctx = self._get_regime_transition_context(macro_regime_score)
        if bool(getattr(config, "MICRO_TRANSITION_GUARD_ENABLED", False)):
            block_gate, block_reason = self.evaluate_transition_policy_block(
                engine="MICRO",
                direction=final_direction,
                transition_ctx=transition_ctx,
            )
            if block_gate:
                self._record_regime_decision(
                    engine="MICRO",
                    decision="BLOCK",
                    strategy_attempted=f"MICRO_{final_direction.value}",
                    gate_name=block_gate,
                    threshold_snapshot={"overlay": transition_ctx.get("transition_overlay", "")},
                    context=transition_ctx,
                )
                return (
                    False,
                    None,
                    state,
                    f"NO_TRADE: {block_gate} ({block_reason})",
                )

        # Build reason string for logging
        if has_conviction:
            reason = f"CONVICTION: {conviction_reason} | {resolve_reason}"
        else:
            reason = f"MICRO_DIRECTION: {final_direction.value} | {resolve_reason}"

        self._record_regime_decision(
            engine="MICRO",
            decision="ALLOW",
            strategy_attempted=f"MICRO_{final_direction.value}",
            gate_name="MICRO_DIRECTION_RESOLVED",
            threshold_snapshot={"has_conviction": bool(has_conviction)},
            context=transition_ctx,
        )
        return True, final_direction, state, reason

    def count_options_positions(self) -> Tuple[int, int, int]:
        """
        V5.3: Count current options positions.

        Returns:
            Tuple of (intraday_count, swing_count, total_count)
        """
        intraday_count = len([p for p in self.get_intraday_positions() if p is not None])
        swing_count = 0

        # Count spread positions
        if self._spread_positions:
            swing_count += len(self._spread_positions)
        elif self._spread_position is not None:
            swing_count += 1

        # Count canonical swing single-leg position only.
        if self._position is not None:
            swing_count += 1

        total_count = intraday_count + swing_count
        return intraday_count, swing_count, total_count

    def get_spread_positions(self) -> List[SpreadPosition]:
        """Get all active spread positions."""
        if self._spread_positions:
            return list(self._spread_positions)
        if self._spread_position is not None:
            return [self._spread_position]
        return []

    def get_open_spread_count(self) -> int:
        """Number of active spread positions."""
        return len(self.get_spread_positions())

    def get_open_spread_count_by_direction(self, direction_label: str) -> int:
        """Count active spreads in a directional bucket (BULLISH/BEARISH)."""
        label = str(direction_label or "").upper()
        count = 0
        for spread in self.get_spread_positions():
            if self._spread_direction_label(spread.spread_type) == label:
                count += 1
        return count

    def get_open_spread_count_for_expiry(
        self, expiry_bucket: str, direction_label: Optional[str] = None
    ) -> int:
        """Count active spreads in a given expiry bucket (optionally by direction)."""
        bucket = str(expiry_bucket or "").strip()
        if not bucket:
            return 0
        wanted_dir = str(direction_label or "").upper() if direction_label else None
        count = 0
        for spread in self.get_spread_positions():
            spread_bucket = str(getattr(spread.long_leg, "expiry", "") or "").strip()
            if not spread_bucket:
                spread_bucket = f"DTE:{int(getattr(spread.long_leg, 'days_to_expiry', -1))}"
            if spread_bucket != bucket:
                continue
            if wanted_dir:
                spread_dir = self._spread_direction_label(spread.spread_type)
                if spread_dir != wanted_dir:
                    continue
            count += 1
        return count

    def _check_expiry_concentration_cap(
        self,
        expiry_bucket: str,
        direction: Optional[OptionDirection],
        regime_score: Optional[float] = None,
        vix_current: Optional[float] = None,
    ) -> Optional[str]:
        """Return reject code when per-expiry spread concentration cap is exceeded."""
        if not bool(getattr(config, "SPREAD_EXPIRY_CONCENTRATION_CAP_ENABLED", False)):
            return None
        bucket = str(expiry_bucket or "").strip()
        if not bucket:
            return None

        total_cap = max(int(getattr(config, "SPREAD_MAX_PER_EXPIRY", 0)), 0)
        if total_cap > 0:
            total_count = self.get_open_spread_count_for_expiry(bucket)
            if total_count >= total_cap:
                self.log(
                    f"EXPIRY_CAP_BLOCK: Bucket={bucket} | Total={total_count} >= Cap={total_cap}"
                )
                return "R_EXPIRY_CONCENTRATION_CAP"

        if direction is None:
            return None

        dir_label = "BULLISH" if direction == OptionDirection.CALL else "BEARISH"
        if dir_label == "BULLISH":
            dir_cap = max(int(getattr(config, "SPREAD_MAX_BULLISH_PER_EXPIRY", 0)), 0)
            # Bull-profile override: allow one additional bullish ladder slot per expiry.
            bull_regime_min = float(getattr(config, "SPREAD_EXPIRY_BULL_PROFILE_REGIME_MIN", 70.0))
            bull_vix_max = float(getattr(config, "SPREAD_EXPIRY_BULL_PROFILE_VIX_MAX", 18.0))
            bull_cap = max(
                int(getattr(config, "SPREAD_MAX_BULLISH_PER_EXPIRY_BULL_PROFILE", dir_cap)),
                dir_cap,
            )
            if (
                regime_score is not None
                and float(regime_score) >= bull_regime_min
                and vix_current is not None
                and float(vix_current) <= bull_vix_max
            ):
                dir_cap = bull_cap
        else:
            dir_cap = max(int(getattr(config, "SPREAD_MAX_BEARISH_PER_EXPIRY", 0)), 0)
        if dir_cap <= 0:
            return None

        dir_count = self.get_open_spread_count_for_expiry(bucket, dir_label)
        if dir_count >= dir_cap:
            self.log(
                f"EXPIRY_CAP_BLOCK: Bucket={bucket} | Direction={dir_label} | "
                f"Count={dir_count} >= Cap={dir_cap}"
            )
            return "R_EXPIRY_CONCENTRATION_CAP_DIRECTION"

        return None

    def _spread_direction_label(self, spread_type: str) -> Optional[str]:
        """Map spread type to directional bucket for slot caps."""
        st = str(spread_type or "").upper()
        if st in {"BULL_CALL", "BULL_CALL_DEBIT", "BULL_PUT_CREDIT"}:
            return "BULLISH"
        if st in {"BEAR_PUT", "BEAR_PUT_DEBIT", "BEAR_CALL_CREDIT"}:
            return "BEARISH"
        return None

    def _resolve_put_assignment_gate_profile(
        self,
        *,
        overlay_state: str,
        vix_current: float,
        regime_score: float,
    ) -> Tuple[bool, float, str]:
        """Return (enforce_gate, min_otm_pct, profile) for short-PUT assignment guard."""
        hard_block_vix = float(getattr(config, "BEAR_PUT_ASSIGNMENT_HARD_BLOCK_VIX", 28.0))
        hard_block_regime_max = float(
            getattr(config, "BEAR_PUT_ASSIGNMENT_HARD_BLOCK_REGIME_MAX", 40.0)
        )
        enforce_assignment_gate = (
            overlay_state in {"STRESS", "EARLY_STRESS"}
            or vix_current >= hard_block_vix
            or regime_score <= hard_block_regime_max
        )
        min_otm_pct = float(getattr(config, "BEAR_PUT_ENTRY_MIN_OTM_PCT", 0.03))
        stress_otm_pct = float(getattr(config, "BEAR_PUT_ENTRY_MIN_OTM_PCT_STRESS", min_otm_pct))
        low_vix_threshold = float(getattr(config, "BEAR_PUT_ENTRY_LOW_VIX_THRESHOLD", 18.0))
        relaxed_otm_pct = float(getattr(config, "BEAR_PUT_ENTRY_MIN_OTM_PCT_RELAXED", 0.015))
        relaxed_regime_min = float(getattr(config, "BEAR_PUT_ENTRY_RELAXED_REGIME_MIN", 60.0))
        gate_profile = "BASE"
        if overlay_state in {"STRESS", "EARLY_STRESS"}:
            min_otm_pct = min(min_otm_pct, stress_otm_pct)
            gate_profile = "STRESS"
        if (
            vix_current <= low_vix_threshold
            and regime_score >= relaxed_regime_min
            and gate_profile == "BASE"
        ):
            min_otm_pct = min(min_otm_pct, relaxed_otm_pct)
            gate_profile = "LOW_VIX_RELAXED"
        return enforce_assignment_gate, float(min_otm_pct), gate_profile

    def _find_safer_bear_put_short_leg(
        self,
        *,
        contracts: Optional[List[OptionContract]],
        long_leg_contract: OptionContract,
        current_price: float,
        min_otm_pct: float,
    ) -> Optional[OptionContract]:
        """
        Pick a farther-OTM short PUT for BEAR_PUT debit when assignment gate blocks the initial leg.
        """
        if not contracts or current_price <= 0:
            return None
        effective_width_min = self._get_effective_spread_width_min()
        width_max = float(getattr(config, "SPREAD_WIDTH_MAX", 10.0))
        oi_min_short = max(0, int(getattr(config, "OPTIONS_MIN_OPEN_INTEREST_PUT", 25)) // 2)
        spread_warn_short = float(getattr(config, "OPTIONS_SPREAD_WARNING_PCT_PUT", 0.35))
        target_width = float(getattr(config, "SPREAD_WIDTH_TARGET", 5.0))
        long_symbol = str(long_leg_contract.symbol)
        long_strike = float(long_leg_contract.strike)
        long_dte = int(long_leg_contract.days_to_expiry)

        candidates: List[Tuple[float, float, float, OptionContract]] = []
        for contract in contracts:
            if contract is None or contract.direction != OptionDirection.PUT:
                continue
            if str(contract.symbol) == long_symbol:
                continue
            if int(contract.days_to_expiry) != long_dte:
                continue
            strike = float(contract.strike)
            # BEAR_PUT short leg must remain below long strike (OTM).
            if strike >= long_strike:
                continue
            width = abs(long_strike - strike)
            if width < effective_width_min or width > width_max:
                continue
            if int(contract.open_interest) < oi_min_short:
                continue
            if float(contract.spread_pct) > spread_warn_short:
                continue
            otm_pct = (current_price - strike) / current_price
            if otm_pct < min_otm_pct:
                continue
            width_penalty = abs(width - target_width)
            delta_pref = abs(abs(float(getattr(contract, "delta", 0.30) or 0.30)) - 0.28)
            # Prefer closest width to target, then deeper OTM, then delta quality.
            candidates.append((width_penalty, -otm_pct, delta_pref, contract))

        if not candidates:
            return None
        candidates.sort(key=lambda row: (row[0], row[1], row[2]))
        return candidates[0][3]

    def _set_directional_spread_cooldown(
        self,
        *,
        cooldown_key: str,
        minutes: int,
        reason: str,
    ) -> None:
        """Apply targeted spread-entry cooldown on a key used by leg selectors."""
        if minutes <= 0 or self.algorithm is None:
            return
        key = str(cooldown_key or "").upper()
        if not key:
            return
        until_dt = self.algorithm.Time + timedelta(minutes=max(0, int(minutes)))
        until_text = until_dt.strftime("%Y-%m-%d %H:%M:%S")
        if not hasattr(self, "_spread_failure_cooldown_until_by_dir"):
            self._spread_failure_cooldown_until_by_dir = {}
        self._spread_failure_cooldown_until_by_dir[key] = until_text
        self.log(
            f"SPREAD_COOLDOWN_SET: {reason} | Key={key} | Minutes={int(minutes)} | Until={until_text}",
            trades_only=True,
        )

    def get_regime_overlay_state(
        self, vix_current: Optional[float], regime_score: Optional[float] = None
    ) -> str:
        """
        V6.22: Fast stress overlay used by resolver, slot caps, and exits.

        Returns one of: NORMAL, EARLY_STRESS, STRESS, RECOVERY.
        """
        try:
            vix = float(vix_current) if vix_current is not None else 0.0
        except (TypeError, ValueError):
            vix = 0.0

        stress_vix = float(getattr(config, "REGIME_OVERLAY_STRESS_VIX", 21.0))
        stress_vix_5d = float(getattr(config, "REGIME_OVERLAY_STRESS_VIX_5D", 0.18))
        early_low = float(getattr(config, "REGIME_OVERLAY_EARLY_VIX_LOW", 16.0))
        early_high = float(getattr(config, "REGIME_OVERLAY_EARLY_VIX_HIGH", 18.0))
        vix_5d_change = (
            self._iv_sensor.get_vix_5d_change() if self._iv_sensor.is_conviction_ready() else None
        )

        if vix >= stress_vix:
            return "STRESS"
        if vix >= early_high and vix_5d_change is not None and vix_5d_change >= stress_vix_5d:
            return "STRESS"
        if early_low <= vix < early_high:
            return "EARLY_STRESS"
        if vix < early_low and vix_5d_change is not None and vix_5d_change <= -0.05:
            return "RECOVERY"
        return "NORMAL"

    def _get_regime_transition_context(
        self, regime_score: Optional[float] = None
    ) -> Dict[str, Any]:
        """Fetch transition context from algorithm if available."""
        ctx: Dict[str, Any] = {}
        if isinstance(self._transition_context_snapshot, dict):
            try:
                ctx = dict(self._transition_context_snapshot)
            except Exception:
                ctx = {}
        if self.algorithm is not None and hasattr(self.algorithm, "_get_regime_transition_context"):
            if not ctx:
                try:
                    raw = self.algorithm._get_regime_transition_context()
                    if isinstance(raw, dict):
                        ctx = dict(raw)
                except Exception:
                    ctx = {}
        if "effective_score" not in ctx and regime_score is not None:
            try:
                ctx["effective_score"] = float(regime_score)
            except Exception:
                pass
        if "transition_score" not in ctx and "effective_score" in ctx:
            try:
                ctx["transition_score"] = float(ctx.get("effective_score"))
            except Exception:
                pass
        return ctx

    def set_transition_context_snapshot(self, transition_ctx: Optional[Dict[str, Any]]) -> None:
        """Cache per-cycle transition context so all engine decisions consume one snapshot."""
        if isinstance(transition_ctx, dict):
            self._transition_context_snapshot = dict(transition_ctx)
        else:
            self._transition_context_snapshot = None

    def clear_transition_context_snapshot(self) -> None:
        """Clear cached transition context snapshot."""
        self._transition_context_snapshot = None

    def evaluate_transition_policy_block(
        self,
        engine: str,
        direction: Optional[OptionDirection],
        transition_ctx: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, str]:
        """
        Unified transition-policy gate for MICRO/ITM/VASS.

        Returns:
            (gate_name, reason). Empty gate_name means no block.
        """
        if not bool(getattr(config, "REGIME_TRANSITION_GUARD_ENABLED", True)):
            return "", ""

        ctx = (
            transition_ctx
            if isinstance(transition_ctx, dict)
            else self._get_regime_transition_context()
        )
        engine_key = str(engine or "").upper()
        if engine_key not in {"MICRO", "ITM", "VASS", "HEDGE"}:
            return "", ""

        if engine_key == "MICRO":
            ambiguous_key = "MICRO_TRANSITION_BLOCK_AMBIGUOUS"
            call_det_key = "MICRO_TRANSITION_BLOCK_CALL_ON_DETERIORATION"
            put_rec_key = "MICRO_TRANSITION_BLOCK_PUT_ON_RECOVERY"
            ambiguous_gate = "MICRO_TRANSITION_AMBIGUOUS"
            call_det_gate = "MICRO_TRANSITION_BLOCK_CALL_ON_DETERIORATION"
            put_rec_gate = "MICRO_TRANSITION_BLOCK_PUT_ON_RECOVERY"
        elif engine_key == "ITM":
            ambiguous_key = "ITM_TRANSITION_BLOCK_AMBIGUOUS"
            call_det_key = "ITM_TRANSITION_BLOCK_BULL_ON_DETERIORATION"
            put_rec_key = "ITM_TRANSITION_BLOCK_BEAR_ON_RECOVERY"
            ambiguous_gate = "REGIME_TRANSITION_AMBIGUOUS"
            call_det_gate = "REGIME_DOWNSHIFT_NO_CALL"
            put_rec_gate = "REGIME_RECOVERY_NO_PUT"
        elif engine_key == "VASS":
            ambiguous_key = "VASS_TRANSITION_BLOCK_AMBIGUOUS"
            call_det_key = "VASS_TRANSITION_BLOCK_BULL_ON_DETERIORATION"
            put_rec_key = "VASS_TRANSITION_BLOCK_BEAR_ON_RECOVERY"
            ambiguous_gate = "VASS_TRANSITION_BLOCK_AMBIGUOUS"
            call_det_gate = "VASS_TRANSITION_BLOCK_BULL_ON_DETERIORATION"
            put_rec_gate = "VASS_TRANSITION_BLOCK_BEAR_ON_RECOVERY"
        else:  # HEDGE (protective hedges/throttle-only handoff policies)
            ambiguous_key = "ITM_TRANSITION_BLOCK_AMBIGUOUS"
            call_det_key = "ITM_TRANSITION_BLOCK_BULL_ON_DETERIORATION"
            put_rec_key = "ITM_TRANSITION_BLOCK_BEAR_ON_RECOVERY"
            ambiguous_gate = "HEDGE_TRANSITION_AMBIGUOUS"
            call_det_gate = "HEDGE_TRANSITION_BLOCK_BULL_ON_DETERIORATION"
            put_rec_gate = "HEDGE_TRANSITION_BLOCK_BEAR_ON_RECOVERY"

        if bool(getattr(config, ambiguous_key, True)) and bool(ctx.get("ambiguous", False)):
            return ambiguous_gate, "ambiguous transition state"

        overlay = str(ctx.get("transition_overlay", "") or "").upper()
        in_deterioration = (
            bool(ctx.get("strong_deterioration", False)) or overlay == "DETERIORATION"
        )
        in_recovery = bool(ctx.get("strong_recovery", False)) or overlay == "RECOVERY"

        if (
            direction == OptionDirection.CALL
            and bool(getattr(config, call_det_key, True))
            and in_deterioration
        ):
            return call_det_gate, "bullish blocked during deterioration"

        if (
            direction == OptionDirection.PUT
            and bool(getattr(config, put_rec_key, True))
            and in_recovery
        ):
            return put_rec_gate, "bearish blocked during recovery"

        if bool(getattr(config, "TRANSITION_HANDOFF_THROTTLE_ENABLED", True)):
            if engine_key == "ITM":
                handoff_enabled = bool(
                    getattr(config, "ITM_TRANSITION_HANDOFF_THROTTLE_ENABLED", True)
                )
            elif engine_key == "MICRO":
                handoff_enabled = bool(
                    getattr(config, "MICRO_TRANSITION_HANDOFF_THROTTLE_ENABLED", True)
                )
            elif engine_key == "VASS":
                handoff_enabled = bool(
                    getattr(config, "VASS_TRANSITION_HANDOFF_THROTTLE_ENABLED", True)
                )
            elif engine_key == "HEDGE":
                handoff_enabled = bool(
                    getattr(config, "HEDGE_TRANSITION_HANDOFF_THROTTLE_ENABLED", True)
                )
            else:
                handoff_enabled = False
            if handoff_enabled and direction in (OptionDirection.CALL, OptionDirection.PUT):
                overlay = str(ctx.get("transition_overlay", "")).upper()
                bars_since_flip = int(ctx.get("overlay_bars_since_flip", 999) or 999)
                handoff_bars = max(
                    1,
                    int(
                        getattr(
                            config,
                            "VASS_TRANSITION_HANDOFF_BARS"
                            if engine_key == "VASS"
                            else "TRANSITION_HANDOFF_BARS",
                            getattr(config, "TRANSITION_HANDOFF_BARS", 2),
                        )
                    ),
                )
                delta = float(ctx.get("delta", 0.0) or 0.0)
                momentum = float(ctx.get("momentum_roc", 0.0) or 0.0)
                hard_downside = delta <= float(
                    getattr(config, "TRANSITION_HANDOFF_HARD_DOWNSIDE_DELTA_MAX", -2.5)
                ) and momentum <= float(
                    getattr(config, "TRANSITION_HANDOFF_HARD_DOWNSIDE_MOM_MAX", -0.02)
                )
                hard_upside = delta >= float(
                    getattr(config, "TRANSITION_HANDOFF_HARD_UPSIDE_DELTA_MIN", 2.5)
                ) and momentum >= float(
                    getattr(config, "TRANSITION_HANDOFF_HARD_UPSIDE_MOM_MIN", 0.02)
                )
                if (
                    direction == OptionDirection.PUT
                    and overlay == "RECOVERY"
                    and bars_since_flip < handoff_bars
                    and not hard_downside
                ):
                    return (
                        "TRANSITION_HANDOFF_PUT_THROTTLE",
                        f"PUT throttled {bars_since_flip}/{handoff_bars} bars into RECOVERY",
                    )
                if (
                    direction == OptionDirection.CALL
                    and overlay == "DETERIORATION"
                    and bars_since_flip < handoff_bars
                    and not hard_upside
                ):
                    return (
                        "TRANSITION_HANDOFF_CALL_THROTTLE",
                        f"CALL throttled {bars_since_flip}/{handoff_bars} bars into DETERIORATION",
                    )

        return "", ""

    def _record_regime_decision(
        self,
        engine: str,
        decision: str,
        strategy_attempted: str,
        gate_name: str,
        threshold_snapshot: Optional[Any] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Forward structured regime decision telemetry to algorithm artifact writer."""
        if self.algorithm is None or not hasattr(self.algorithm, "_record_regime_decision_event"):
            return
        try:
            self.algorithm._record_regime_decision_event(
                engine=engine,
                engine_decision=decision,
                strategy_attempted=strategy_attempted,
                gate_name=gate_name,
                threshold_snapshot=threshold_snapshot,
                context=context,
            )
        except Exception:
            pass

    def can_enter_single_leg(self) -> Tuple[bool, str]:
        """
        Check if a new single-leg options entry is allowed.

        Note:
            This is the canonical slot gate for MICRO/ITM single-leg lanes.
            `can_enter_intraday()` remains as a backward-compatible alias.

        Returns:
            Tuple of (allowed, reason)
        """
        intraday_count, _, total_count = self.count_options_positions()

        if total_count >= config.OPTIONS_MAX_TOTAL_POSITIONS:
            return (
                False,
                f"R_SLOT_TOTAL_MAX: {total_count} >= {config.OPTIONS_MAX_TOTAL_POSITIONS}",
            )

        single_leg_cap = int(
            getattr(config, "SINGLE_LEG_MAX_POSITIONS", config.OPTIONS_MAX_INTRADAY_POSITIONS)
        )
        if intraday_count >= single_leg_cap:
            return (
                False,
                f"R_SLOT_SINGLE_LEG_MAX: {intraday_count} >= {single_leg_cap}",
            )

        return True, "R_OK"

    def preflight_intraday_entry(
        self,
        strategy: Optional["IntradayStrategy"],
        direction: Optional[OptionDirection] = None,
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Fast preflight for single-leg intraday submission.

        This is intentionally lightweight and lane-aware so callers can avoid
        generating candidates that are guaranteed to be dropped.
        """
        strategy_name = (
            self._canonical_intraday_strategy(strategy).value if strategy is not None else ""
        )
        lane = self._intraday_engine_lane_from_strategy(strategy_name)

        if self._pending_intraday_entry or self._pending_intraday_entries:
            self._clear_stale_pending_intraday_entry_if_orphaned()
        if self.has_pending_intraday_entry(engine=lane):
            return False, "E_INTRADAY_PENDING_ENTRY", lane

        lane_cap = int(
            getattr(
                config,
                "ITM_MAX_CONCURRENT_POSITIONS"
                if lane == "ITM"
                else "MICRO_MAX_CONCURRENT_POSITIONS",
                1,
            )
            or 0
        )
        lane_positions = len(self._intraday_positions.get(lane) or [])
        if lane_cap > 0 and lane_positions >= lane_cap:
            code = "R_ITM_CONCURRENT_CAP" if lane == "ITM" else "R_MICRO_CONCURRENT_CAP"
            return False, code, f"{lane}={lane_positions}/{lane_cap}"

        can_single_leg, reason = self.can_enter_single_leg()
        if not can_single_leg:
            code = str(reason or "R_SLOT_LIMIT").split(":", 1)[0].strip() or "R_SLOT_LIMIT"
            return False, code, reason

        if not self._can_trade_options(OptionsMode.INTRADAY, direction=direction):
            tl_reason, tl_detail = self.pop_last_trade_limit_failure()
            return False, tl_reason or "E_INTRADAY_TRADE_LIMIT", tl_detail

        return True, "R_OK", None

    def can_enter_intraday(self) -> Tuple[bool, str]:
        """
        Backward-compatible alias for legacy call-sites.

        Use `can_enter_single_leg()` in new code.
        """
        return self.can_enter_single_leg()

    def can_enter_swing(
        self,
        direction: Optional[OptionDirection] = None,
        overlay_state: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Check if swing entry is allowed."""
        return self._vass_entry_engine.can_enter_swing(
            host=self,
            direction=direction,
            overlay_state=overlay_state,
        )

    def get_macro_direction(self, macro_regime_score: float) -> str:
        """
        V5.3: Determine Macro direction from regime score.

        Args:
            macro_regime_score: Regime score (0-100)

        Returns:
            "BULLISH", "BEARISH", or "NEUTRAL"
        """
        # Prefer regime state-machine output when available.
        transition_ctx = self._get_regime_transition_context(macro_regime_score)
        base_regime = str(transition_ctx.get("base_regime", "") or "").upper()
        transition_overlay = str(transition_ctx.get("transition_overlay", "") or "").upper()
        if transition_overlay == "AMBIGUOUS":
            return "NEUTRAL"
        if base_regime in {"BULLISH", "BEARISH"}:
            return base_regime

        bullish_min = float(getattr(config, "MACRO_DIRECTION_BULLISH_MIN", 55.0))
        bearish_max = float(getattr(config, "MACRO_DIRECTION_BEARISH_MAX", 45.0))
        if macro_regime_score > bullish_min:
            return "BULLISH"
        elif macro_regime_score < bearish_max:
            return "BEARISH"
        else:
            return "NEUTRAL"

    def should_log_vass_rejection(self, reason_key: str) -> bool:
        """Per-reason throttle for VASS skip/rejection logs."""
        now = self.algorithm.Time if self.algorithm is not None else datetime.utcnow()
        return self._vass_entry_engine.should_log_rejection(now=now, reason_key=reason_key)

    def resolve_vass_direction_context(
        self,
        *,
        regime_score: float,
        size_multiplier: float,
        bull_profile_log_prefix: str,
        clamp_log_prefix: str,
        shock_log_prefix: str,
        transition_ctx: Optional[Dict[str, Any]] = None,
    ) -> Optional[Tuple[OptionDirection, str, Any, float, bool, str, str, str, str]]:
        """Resolve VASS direction + sizing context with shared guard rails."""
        return self._vass_entry_engine.resolve_direction_context(
            host=self,
            regime_score=regime_score,
            size_multiplier=size_multiplier,
            bull_profile_log_prefix=bull_profile_log_prefix,
            clamp_log_prefix=clamp_log_prefix,
            shock_log_prefix=shock_log_prefix,
            transition_ctx=transition_ctx,
        )

    def scan_spread_for_direction(
        self,
        *,
        chain: Any,
        direction: OptionDirection,
        direction_str: str,
        regime_score: float,
        qqq_price: float,
        adx_value: float,
        ma200_value: float,
        ma50_value: float,
        iv_rank: float,
        size_multiplier: float,
        is_eod_scan: bool,
    ) -> None:
        """Scan for VASS spread entry in a specific direction."""
        self._vass_entry_engine.scan_spread_for_direction(
            host=self,
            chain=chain,
            direction=direction,
            direction_str=direction_str,
            regime_score=regime_score,
            qqq_price=qqq_price,
            adx_value=adx_value,
            ma200_value=ma200_value,
            ma50_value=ma50_value,
            iv_rank=iv_rank,
            size_multiplier=size_multiplier,
            is_eod_scan=is_eod_scan,
        )

    def build_vass_spread_signal(
        self,
        *,
        chain: Any,
        candidate_contracts: List[OptionContract],
        direction: OptionDirection,
        regime_score: float,
        qqq_price: float,
        adx_value: float,
        ma200_value: float,
        ma50_value: float,
        iv_rank: float,
        size_multiplier: float,
        portfolio_value: float,
        margin_remaining: float,
        strategy: SpreadStrategy,
        vass_dte_min: int,
        vass_dte_max: int,
        dte_ranges: List[Tuple[int, int]],
        is_credit: bool,
        is_eod_scan: bool,
        fallback_log_prefix: str,
    ) -> Tuple[Optional[TargetWeight], str]:
        """Build VASS spread entry signal via VASS entry engine."""
        return self._vass_entry_engine.build_spread_signal(
            host=self,
            chain=chain,
            candidate_contracts=candidate_contracts,
            direction=direction,
            regime_score=regime_score,
            qqq_price=qqq_price,
            adx_value=adx_value,
            ma200_value=ma200_value,
            ma50_value=ma50_value,
            iv_rank=iv_rank,
            size_multiplier=size_multiplier,
            portfolio_value=portfolio_value,
            margin_remaining=margin_remaining,
            strategy=strategy,
            vass_dte_min=vass_dte_min,
            vass_dte_max=vass_dte_max,
            dte_ranges=dte_ranges,
            is_credit=is_credit,
            is_eod_scan=is_eod_scan,
            fallback_log_prefix=fallback_log_prefix,
        )

    def run_vass_entry_cycle(
        self,
        *,
        chain: Any,
        regime_score: float,
        qqq_price: float,
        adx_value: float,
        ma200_value: float,
        ma50_value: float,
        iv_rank: float,
        size_multiplier: float,
        is_eod_scan: bool,
    ) -> None:
        """Resolve VASS direction context and dispatch directional spread scans."""
        self._vass_entry_engine.run_eod_entry_cycle(
            host=self,
            chain=chain,
            regime_score=regime_score,
            qqq_price=qqq_price,
            adx_value=adx_value,
            ma200_value=ma200_value,
            ma50_value=ma50_value,
            iv_rank=iv_rank,
            size_multiplier=size_multiplier,
            is_eod_scan=is_eod_scan,
        )

    def run_vass_intraday_entry_cycle(
        self,
        *,
        chain: Any,
        qqq_price: float,
        adx_value: float,
        ma200_value: float,
        ma50_value: float,
        size_multiplier: float,
        effective_portfolio_value: float,
        margin_remaining: float,
        transition_ctx: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Run intraday VASS spread lane via VASS entry engine."""
        self._vass_entry_engine.run_intraday_entry_cycle(
            host=self,
            chain=chain,
            qqq_price=qqq_price,
            adx_value=adx_value,
            ma200_value=ma200_value,
            ma50_value=ma50_value,
            size_multiplier=size_multiplier,
            effective_portfolio_value=effective_portfolio_value,
            margin_remaining=margin_remaining,
            transition_ctx=transition_ctx,
        )

    def run_itm_intraday_explicit_cycle(
        self,
        *,
        chain: Any,
        qqq_price: float,
        regime_score: float,
        size_multiplier: float,
        effective_portfolio_value: float,
        vix_intraday: float,
        vix_level_cboe: Optional[float],
        transition_ctx: Optional[Dict[str, Any]],
        itm_dir: Optional[OptionDirection],
        itm_reason: str,
        intraday_scan_context_ready: bool,
        itm_intraday_cooldown_active: bool,
    ) -> None:
        """Run explicit ITM intraday lane via ITM horizon engine."""
        self._itm_horizon_engine.run_intraday_explicit_cycle(
            host=self,
            chain=chain,
            qqq_price=qqq_price,
            regime_score=regime_score,
            size_multiplier=size_multiplier,
            effective_portfolio_value=effective_portfolio_value,
            vix_intraday=vix_intraday,
            vix_level_cboe=vix_level_cboe,
            transition_ctx=transition_ctx,
            itm_dir=itm_dir,
            itm_reason=itm_reason,
            intraday_scan_context_ready=intraday_scan_context_ready,
            itm_intraday_cooldown_active=itm_intraday_cooldown_active,
        )

    def run_micro_intraday_cycle(
        self,
        *,
        chain: Any,
        qqq_price: float,
        regime_score: float,
        size_multiplier: float,
        effective_portfolio_value: float,
        vix_intraday: float,
        vix_level_cboe: Optional[float],
        transition_ctx: Optional[Dict[str, Any]],
        uvxy_pct: float,
        micro_intraday_cooldown_active: bool,
    ) -> Tuple[Optional[OptionDirection], str]:
        """Run MICRO intraday lane via micro entry engine."""
        return self._micro_entry_engine.run_intraday_cycle(
            host=self,
            chain=chain,
            qqq_price=qqq_price,
            regime_score=regime_score,
            size_multiplier=size_multiplier,
            effective_portfolio_value=effective_portfolio_value,
            vix_intraday=vix_intraday,
            vix_level_cboe=vix_level_cboe,
            transition_ctx=transition_ctx,
            uvxy_pct=uvxy_pct,
            micro_intraday_cooldown_active=micro_intraday_cooldown_active,
        )

    def _can_attempt_spread_entry(self, attempt_key: str) -> bool:
        """
        Limit spread entry attempts per day by strategy/direction key.

        Replaces the old single global `_entry_attempted_today` lock that
        blocked all subsequent spread opportunities after one failed attempt.
        """
        max_attempts = int(getattr(config, "SPREAD_MAX_ATTEMPTS_PER_KEY_PER_DAY", 3))
        used = int(self._spread_attempts_today_by_key.get(attempt_key, 0))
        if used >= max_attempts:
            self.log(
                f"SPREAD_ATTEMPT_LIMIT: {attempt_key} blocked | "
                f"{used}/{max_attempts} attempts used"
            )
            return False
        return True

    def _build_vass_signature(
        self,
        spread_type: str,
        direction: Optional[OptionDirection],
        long_leg_contract: OptionContract,
    ) -> str:
        """Build same-trade signature key for VASS anti-cluster guard."""
        return self._vass_entry_engine.build_signature(
            spread_type=spread_type,
            direction=direction,
            long_leg_contract=long_leg_contract,
        )

    def _parse_dt(self, date_text: str, hour: int, minute: int) -> Optional[datetime]:
        """Parse current scan timestamp from inputs; fallback to algorithm time."""
        return self._vass_entry_engine.parse_scan_dt(
            date_text=date_text,
            hour=hour,
            minute=minute,
            algorithm=self.algorithm,
        )

    def _check_vass_similar_entry_guard(
        self,
        signature: str,
        now_dt: Optional[datetime],
    ) -> Optional[str]:
        """VASS anti-cluster guard delegated to VASSEntryEngine."""
        if not self._vass_entry_engine_enabled:
            return None
        return self._vass_entry_engine.check_similar_entry_guard(
            signature=signature,
            now_dt=now_dt,
        )

    def _record_vass_signature_entry(self, signature: str, entry_dt: Optional[datetime]) -> None:
        """Record VASS signature entry via VASSEntryEngine."""
        if not self._vass_entry_engine_enabled:
            return
        self._vass_entry_engine.record_signature_entry(signature=signature, entry_dt=entry_dt)

    def _check_vass_direction_day_gap(
        self, direction: Optional[OptionDirection], current_date: str
    ) -> Optional[str]:
        """VASS per-direction day-gap guard delegated to VASSEntryEngine."""
        if not self._vass_entry_engine_enabled:
            return None
        return self._vass_entry_engine.check_direction_day_gap(
            direction=direction,
            current_date=current_date,
            algorithm=self.algorithm,
        )

    def _record_vass_direction_day_entry(
        self, direction: Optional[OptionDirection], entry_dt: Optional[datetime]
    ) -> None:
        """Record VASS per-direction entry date via VASSEntryEngine."""
        if not self._vass_entry_engine_enabled:
            return
        self._vass_entry_engine.record_direction_day_entry(direction=direction, entry_dt=entry_dt)

    def _build_spread_key(self, spread: SpreadPosition) -> str:
        """Stable spread key for per-position state."""
        return (
            f"{self._symbol_str(spread.long_leg.symbol)}|"
            f"{self._symbol_str(spread.short_leg.symbol)}|"
            f"{str(getattr(spread, 'entry_time', '') or '')}"
        )

    def _get_engine_now_dt(self) -> Optional[datetime]:
        """Best-effort engine timestamp for staged timers."""
        if self.algorithm is None:
            return None
        try:
            return self.algorithm.Time
        except Exception:
            return None

    def _check_neutrality_staged_exit(
        self,
        spread: SpreadPosition,
        regime_score: float,
        pnl_pct: float,
    ) -> Optional[str]:
        """
        Phase C: staged neutrality de-risk.

        Stage 1: warn and hold.
        Stage 2: exit if neutrality persists for confirm window or loss breaches damage threshold.
        """
        if not getattr(config, "SPREAD_NEUTRALITY_EXIT_ENABLED", True):
            return None

        key = self._build_spread_key(spread)
        zone_low = float(getattr(config, "SPREAD_NEUTRALITY_ZONE_LOW", 45))
        zone_high = float(getattr(config, "SPREAD_NEUTRALITY_ZONE_HIGH", 65))
        band = float(getattr(config, "SPREAD_NEUTRALITY_EXIT_PNL_BAND", 0.06))

        in_neutral_zone = zone_low <= regime_score <= zone_high
        in_flat_band = -band <= pnl_pct <= band
        if not (in_neutral_zone and in_flat_band):
            if key in self._spread_neutrality_warn_by_key:
                self._spread_neutrality_warn_by_key.pop(key, None)
                self.log(
                    f"NEUTRALITY_WARN_CLEARED: Spread={spread.spread_type} | "
                    f"Score={regime_score:.0f} | PnL={pnl_pct:+.1%}",
                    trades_only=True,
                )
            return None

        if not getattr(config, "SPREAD_NEUTRALITY_STAGED_ENABLED", True):
            return (
                f"NEUTRALITY_EXIT: Score {regime_score:.0f} in dead zone "
                f"({zone_low:.0f}-{zone_high:.0f}) with flat P&L ({pnl_pct:+.1%})"
            )

        now_dt = self._get_engine_now_dt()
        if now_dt is None:
            return (
                f"NEUTRALITY_EXIT: Score {regime_score:.0f} in dead zone "
                f"({zone_low:.0f}-{zone_high:.0f}) with flat P&L ({pnl_pct:+.1%})"
            )

        confirm_hours = float(getattr(config, "SPREAD_NEUTRALITY_CONFIRM_HOURS", 2))
        damage_pct = float(getattr(config, "SPREAD_NEUTRALITY_STAGE1_DAMAGE_PCT", 0.15))

        first_seen = self._spread_neutrality_warn_by_key.get(key)
        if first_seen is None:
            self._spread_neutrality_warn_by_key[key] = now_dt
            self.log(
                f"NEUTRALITY_WARN: Spread={spread.spread_type} | "
                f"Score={regime_score:.0f} | PnL={pnl_pct:+.1%} | Confirm={confirm_hours:.1f}h",
                trades_only=True,
            )
            return None

        elapsed_hours = max(0.0, (now_dt - first_seen).total_seconds() / 3600.0)
        if pnl_pct <= -damage_pct:
            return (
                f"NEUTRALITY_CONFIRMED_EXIT: Damage guard | "
                f"PnL={pnl_pct:+.1%} <= -{damage_pct:.0%} | "
                f"Elapsed={elapsed_hours:.1f}h"
            )
        if elapsed_hours >= confirm_hours:
            return (
                f"NEUTRALITY_CONFIRMED_EXIT: Confirmed in dead zone "
                f"({zone_low:.0f}-{zone_high:.0f}) | "
                f"Elapsed={elapsed_hours:.1f}h | PnL={pnl_pct:+.1%}"
            )
        return None

    def _record_spread_entry_attempt(self, attempt_key: str) -> None:
        """Record spread attempt only after signal construction succeeds."""
        self._spread_attempts_today_by_key[attempt_key] = (
            int(self._spread_attempts_today_by_key.get(attempt_key, 0)) + 1
        )

    def _set_spread_failure_cooldown(
        self, current_time: Optional[str], direction: Optional[str] = None
    ) -> None:
        """
        V2.4.3: Set cooldown after spread construction fails.

        Prevents retry storms when no valid contracts exist.
        Uses minute-level cooldown when configured.

        Args:
            current_time: Current timestamp in "YYYY-MM-DD HH:MM:SS" format.
            direction: Optional direction label to scope cooldown (CALL/PUT).
        """
        if not current_time:
            return

        try:
            from datetime import datetime, timedelta

            now_dt = datetime.strptime(current_time[:19], "%Y-%m-%d %H:%M:%S")
            cooldown_minutes = int(
                getattr(
                    config,
                    "SPREAD_FAILURE_COOLDOWN_MINUTES",
                    int(getattr(config, "SPREAD_FAILURE_COOLDOWN_HOURS", 1) * 60),
                )
            )
            cooldown_until_dt = now_dt + timedelta(minutes=max(cooldown_minutes, 0))
            cooldown_until = cooldown_until_dt.strftime("%Y-%m-%d %H:%M:%S")

            # V6.12: Direction-scoped cooldown (CALL failure doesn't block PUT)
            if direction:
                if not hasattr(self, "_spread_failure_cooldown_until_by_dir"):
                    self._spread_failure_cooldown_until_by_dir = {}
                dir_key = direction.value if hasattr(direction, "value") else str(direction)
                self._spread_failure_cooldown_until_by_dir[str(dir_key).upper()] = cooldown_until
            else:
                self._spread_failure_cooldown_until = cooldown_until
            self.log(
                f"SPREAD: Construction failed - entering {cooldown_minutes}m cooldown until {cooldown_until}"
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
        min_open_interest: Optional[int] = None,
        spread_max_pct: Optional[float] = None,
        spread_warn_pct: Optional[float] = None,
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

        # Factor 4: Liquidity (allow per-direction overrides)
        score.score_liquidity = self._score_liquidity(
            bid_ask_spread_pct,
            open_interest,
            min_open_interest=min_open_interest,
            spread_max_pct=spread_max_pct,
            spread_warn_pct=spread_warn_pct,
        )

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

    def _score_liquidity(
        self,
        spread_pct: float,
        open_interest: int,
        min_open_interest: Optional[int] = None,
        spread_max_pct: Optional[float] = None,
        spread_warn_pct: Optional[float] = None,
    ) -> float:
        """
        Score liquidity factor (0-1).

        Based on bid-ask spread and open interest.
        """
        # Start with spread score
        max_pct = spread_max_pct if spread_max_pct is not None else config.OPTIONS_SPREAD_MAX_PCT
        warn_pct = (
            spread_warn_pct if spread_warn_pct is not None else config.OPTIONS_SPREAD_WARNING_PCT
        )
        min_oi = (
            min_open_interest if min_open_interest is not None else config.OPTIONS_MIN_OPEN_INTEREST
        )

        if spread_pct <= max_pct:
            spread_score = 1.0
        elif spread_pct <= warn_pct:
            spread_score = 0.50
        else:
            spread_score = 0.0  # Too wide

        # OI score
        if open_interest >= min_oi:
            oi_score = 1.0
        elif open_interest >= min_oi // 2:
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

        # V2.3.8: Optional fixed 0DTE stop override.
        # V12.0 keeps this disabled by default so ATR-based stop logic remains sovereign.
        if (
            days_to_expiry is not None
            and days_to_expiry <= 1
            and bool(getattr(config, "OPTIONS_0DTE_STATIC_STOP_OVERRIDE_ENABLED", False))
        ):
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
        dte_min: int = None,
        dte_max: int = None,
        set_cooldown: bool = True,
        log_filters: bool = True,
        debug_stats: Optional[Dict[str, Any]] = None,
    ) -> Optional[tuple]:
        """Select long and short leg contracts for a debit spread."""
        return self._vass_entry_engine.select_spread_legs(
            host=self,
            contracts=contracts,
            direction=direction,
            target_width=target_width,
            current_time=current_time,
            dte_min=dte_min,
            dte_max=dte_max,
            set_cooldown=set_cooldown,
            log_filters=log_filters,
            debug_stats=debug_stats,
        )

    def select_spread_legs_with_fallback(
        self,
        contracts: List[OptionContract],
        direction: OptionDirection,
        dte_ranges: List[Tuple[int, int]],
        target_width: float = None,
        current_time: str = None,
        set_cooldown: bool = True,
    ) -> Optional[tuple]:
        """Try multiple DTE ranges before applying spread failure cooldown."""
        return self._vass_entry_engine.select_spread_legs_with_fallback(
            host=self,
            contracts=contracts,
            direction=direction,
            dte_ranges=dte_ranges,
            target_width=target_width,
            current_time=current_time,
            set_cooldown=set_cooldown,
        )

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
        set_cooldown: bool = True,
        log_filters: bool = True,
        debug_stats: Optional[Dict[str, Any]] = None,
    ) -> Optional[Tuple[OptionContract, OptionContract]]:
        """Select short and long legs for credit spread construction."""
        return self._vass_entry_engine.select_credit_spread_legs(
            host=self,
            contracts=contracts,
            strategy=strategy,
            dte_min=dte_min,
            dte_max=dte_max,
            current_time=current_time,
            set_cooldown=set_cooldown,
            log_filters=log_filters,
            debug_stats=debug_stats,
        )

    def select_credit_spread_legs_with_fallback(
        self,
        contracts: List[OptionContract],
        strategy: SpreadStrategy,
        dte_ranges: List[Tuple[int, int]],
        current_time: Optional[str] = None,
        set_cooldown: bool = True,
    ) -> Optional[Tuple[OptionContract, OptionContract]]:
        """Try multiple DTE ranges for credit spreads before cooldown."""
        return self._vass_entry_engine.select_credit_spread_legs_with_fallback(
            host=self,
            contracts=contracts,
            strategy=strategy,
            dte_ranges=dte_ranges,
            current_time=current_time,
            set_cooldown=set_cooldown,
        )

    def pop_last_spread_failure_stats(self) -> Optional[str]:
        return self._vass_entry_engine.pop_last_spread_failure_stats(host=self)

    def pop_last_credit_failure_stats(self) -> Optional[str]:
        return self._vass_entry_engine.pop_last_credit_failure_stats(host=self)

    def set_last_entry_validation_failure(self, reason: Optional[str]) -> None:
        self._vass_entry_engine.set_last_entry_validation_failure(host=self, reason=reason)

    def pop_last_entry_validation_failure(self) -> Optional[str]:
        return self._vass_entry_engine.pop_last_entry_validation_failure(host=self)

    def _normalize_intraday_lane(self, lane: Optional[str]) -> str:
        lane_key = str(lane or "").upper()
        return lane_key if lane_key in ("MICRO", "ITM") else "MICRO"

    def _ensure_intraday_validation_failure_buffers(self) -> None:
        failures = getattr(self, "_last_intraday_validation_failure_by_lane", None)
        details = getattr(self, "_last_intraday_validation_detail_by_lane", None)
        if not isinstance(failures, dict):
            failures = {}
        if not isinstance(details, dict):
            details = {}
        for lane in ("MICRO", "ITM"):
            failures.setdefault(lane, None)
            details.setdefault(lane, None)
        self._last_intraday_validation_failure_by_lane = failures
        self._last_intraday_validation_detail_by_lane = details

    def set_last_intraday_validation_failure(
        self, lane: Optional[str], reason: Optional[str], detail: Optional[str] = None
    ) -> None:
        self._ensure_intraday_validation_failure_buffers()
        lane_key = self._normalize_intraday_lane(lane)
        self._last_intraday_validation_failure_by_lane[lane_key] = reason
        self._last_intraday_validation_detail_by_lane[lane_key] = detail

    def pop_last_intraday_validation_failure(
        self, lane: Optional[str]
    ) -> Tuple[Optional[str], Optional[str]]:
        self._ensure_intraday_validation_failure_buffers()
        lane_key = self._normalize_intraday_lane(lane)
        reason = self._last_intraday_validation_failure_by_lane.get(lane_key)
        detail = self._last_intraday_validation_detail_by_lane.get(lane_key)
        self._last_intraday_validation_failure_by_lane[lane_key] = None
        self._last_intraday_validation_detail_by_lane[lane_key] = None
        return reason, detail

    def set_last_trade_limit_failure(
        self, reason: Optional[str], detail: Optional[str] = None
    ) -> None:
        self._last_trade_limit_failure = reason
        self._last_trade_limit_detail = detail

    def pop_last_trade_limit_failure(self) -> Tuple[Optional[str], Optional[str]]:
        reason = self._last_trade_limit_failure
        detail = self._last_trade_limit_detail
        self._last_trade_limit_failure = None
        self._last_trade_limit_detail = None
        return reason, detail

    def _get_effective_credit_min(self, vix_current: Optional[float] = None) -> float:
        """
        Return IV-adaptive credit floor used consistently by selection and entry validation.
        """
        smoothed_vix = self._iv_sensor.get_smoothed_vix()
        if vix_current is not None:
            try:
                smoothed_vix = max(smoothed_vix, float(vix_current))
            except (TypeError, ValueError):
                pass
        if smoothed_vix > config.CREDIT_SPREAD_HIGH_IV_VIX_THRESHOLD:
            return config.CREDIT_SPREAD_MIN_CREDIT_HIGH_IV
        return config.CREDIT_SPREAD_MIN_CREDIT

    def _get_effective_credit_to_width_min(self, vix_current: Optional[float] = None) -> float:
        """Return IV-adaptive minimum credit/width ratio for credit spread quality gating.

        Three-tier system to avoid over-filtering in moderate VIX (Pitfall 6):
        - VIX > 30: 30% (high IV, wide credits available)
        - VIX 20-30: 32% (moderate IV, slight relaxation)
        - VIX < 20: 35% (low IV, strict quality gate)
        """
        smoothed_vix = self._iv_sensor.get_smoothed_vix()
        if vix_current is not None:
            try:
                smoothed_vix = max(smoothed_vix, float(vix_current))
            except (TypeError, ValueError):
                pass
        if smoothed_vix > config.CREDIT_SPREAD_HIGH_IV_VIX_THRESHOLD:
            return float(getattr(config, "CREDIT_SPREAD_MIN_CREDIT_TO_WIDTH_PCT_HIGH_IV", 0.30))
        medium_threshold = float(getattr(config, "CREDIT_SPREAD_MEDIUM_IV_VIX_THRESHOLD", 20.0))
        if smoothed_vix > medium_threshold:
            return float(getattr(config, "CREDIT_SPREAD_MIN_CREDIT_TO_WIDTH_PCT_MEDIUM_IV", 0.32))
        return float(getattr(config, "CREDIT_SPREAD_MIN_CREDIT_TO_WIDTH_PCT", 0.35))

    def _get_effective_spread_width_min(self, vix_current: Optional[float] = None) -> float:
        """Return VIX-adaptive minimum spread width for debit/credit leg construction."""
        base_min = float(getattr(config, "SPREAD_WIDTH_MIN", 4.0))
        low_min = float(getattr(config, "SPREAD_WIDTH_MIN_LOW_VIX", base_min))
        low_threshold = float(getattr(config, "SPREAD_WIDTH_LOW_VIX_THRESHOLD", 18.0))

        smoothed_vix = self._iv_sensor.get_smoothed_vix()
        if vix_current is not None:
            try:
                smoothed_vix = max(smoothed_vix, float(vix_current))
            except (TypeError, ValueError):
                pass

        if smoothed_vix < low_threshold:
            return max(1.0, low_min)
        return max(1.0, base_min)

    def estimate_spread_margin_per_contract(
        self,
        spread_width: float,
        spread_type: Optional[str] = None,
        credit_received: Optional[float] = None,
    ) -> float:
        """
        Estimate margin requirement per spread contract using a single canonical formula.

        Debit spread: width * 100
        Credit spread: (width - credit) * 100
        """
        try:
            width = max(0.0, float(spread_width))
        except (TypeError, ValueError):
            return 0.0
        if width <= 0:
            return 0.0

        spread_label = (spread_type or "").upper()
        is_credit = "CREDIT" in spread_label

        if is_credit:
            try:
                credit = max(0.0, float(credit_received or 0.0))
            except (TypeError, ValueError):
                credit = 0.0
            return max(1.0, (width - credit) * 100.0)

        return width * 100.0

    def get_usable_margin(self, margin_remaining: float) -> float:
        """
        Apply global margin safety factor and post-rejection cap.
        """
        if margin_remaining <= 0:
            return 0.0
        safety_factor = getattr(config, "SPREAD_MARGIN_SAFETY_FACTOR", 0.80)
        usable_margin = margin_remaining * safety_factor
        if self._rejection_margin_cap is not None:
            usable_margin = min(usable_margin, self._rejection_margin_cap)
        return max(0.0, usable_margin)

    # V6.0: _check_macro_regime_gate() REMOVED
    # Direction decisions now handled by conviction resolution (resolve_trade_signal)
    # in main.py before calling entry signal functions.

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
        direction: Optional[OptionDirection] = None,
    ) -> Optional[TargetWeight]:
        """
        Check for options entry signal (single-leg fallback path).

        V2.3.20: Added size_multiplier for cold start reduced sizing.
        V6.0: Direction now passed from conviction resolution.

        Args:
            adx_value: Current ADX(14) value.
            current_price: Current QQQ price.
            ma200_value: 200-day moving average value.
            ma50_value: 50-day moving average value (used for bearish trend gate on bullish debit).
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
            direction: V6.0: Direction from conviction resolution (CALL or PUT).

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

        # V6.0: Direction now passed from conviction resolution
        # Caller (main.py) has already resolved VASS conviction vs macro direction
        if direction is None:
            self.log("OPT: Entry blocked - direction not provided (conviction resolution required)")
            return None

        # Validate contract direction matches resolved direction
        if best_contract.direction != direction:
            self.log(
                f"OPT: Entry blocked - contract direction {best_contract.direction.value} "
                f"doesn't match resolved direction {direction.value}"
            )
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
            current_strategy = self._canonical_intraday_strategy(current_strategy)

            if current_strategy == IntradayStrategy.ITM_MOMENTUM:
                # ITM_ENGINE canonical delta source: use ITM_* when enabled.
                if bool(getattr(config, "ITM_ENGINE_ENABLED", False)):
                    delta_min = float(getattr(config, "ITM_DELTA_MIN", 0.70))
                    delta_max = float(getattr(config, "ITM_DELTA_MAX", 0.80))
                else:
                    delta_min = float(getattr(config, "INTRADAY_ITM_DELTA_MIN", 0.70))
                    delta_max = float(getattr(config, "INTRADAY_ITM_DELTA_MAX", 0.80))
                mode_label = "Intraday-ITM"
            elif current_strategy in (
                IntradayStrategy.MICRO_DEBIT_FADE,
                IntradayStrategy.MICRO_OTM_MOMENTUM,
                IntradayStrategy.DEBIT_FADE,
            ):
                # MICRO fade/momentum tracks use dedicated delta bands.
                if current_strategy == IntradayStrategy.MICRO_DEBIT_FADE:
                    delta_min = float(
                        getattr(
                            config,
                            "MICRO_DEBIT_FADE_DELTA_MIN",
                            getattr(config, "INTRADAY_DEBIT_FADE_DELTA_MIN", 0.20),
                        )
                    )
                    delta_max = float(
                        getattr(
                            config,
                            "MICRO_DEBIT_FADE_DELTA_MAX",
                            getattr(config, "INTRADAY_DEBIT_FADE_DELTA_MAX", 0.50),
                        )
                    )
                    mode_label = "Intraday-MICRO_FADE"
                else:
                    delta_min = float(
                        getattr(
                            config,
                            "MICRO_OTM_MOMENTUM_DELTA_MIN",
                            getattr(config, "INTRADAY_DEBIT_FADE_DELTA_MIN", 0.20),
                        )
                    )
                    delta_max = float(
                        getattr(
                            config,
                            "MICRO_OTM_MOMENTUM_DELTA_MAX",
                            getattr(config, "INTRADAY_DEBIT_FADE_DELTA_MAX", 0.50),
                        )
                    )
                    mode_label = "Intraday-MICRO_OTM"
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

        # V6.0: Apply cold start multiplier (macro gate removed - conviction handles direction)
        if size_multiplier < 1.0:
            min_size = getattr(config, "OPTIONS_MIN_COMBINED_SIZE_PCT", 0.10)
            if size_multiplier < min_size:
                self.log(
                    f"OPT: Entry blocked - cold start size {size_multiplier:.0%} < min {min_size:.0%}"
                )
                return None

            num_contracts = max(1, int(num_contracts * size_multiplier))
            self.log(
                f"OPT: Sizing reduced to {num_contracts} contracts (SizeMult={size_multiplier:.0%})"
            )

        # V6.19: Apply choppy-market scaling to single-leg entries too.
        choppy_scale = self.get_choppy_market_scale()
        if choppy_scale < 1.0 and num_contracts > 1:
            choppy_adjusted = max(1, int(num_contracts * choppy_scale))
            self.log(
                f"OPT: Choppy market reduction | {num_contracts} -> {choppy_adjusted} contracts | "
                f"ChoppyScale={choppy_scale:.0%}"
            )
            num_contracts = choppy_adjusted

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
        self._pending_entry_strategy = "SWING_SINGLE"

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
            symbol=self._symbol_str(best_contract.symbol),
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

    def _get_spread_debit_width_cap(self, vix_level: Optional[float]) -> float:
        """Resolve adaptive debit/width cap by current VIX band."""
        if vix_level is None:
            return float(getattr(config, "SPREAD_DW_CAP_NORMAL", 0.42))
        try:
            vix = float(vix_level)
        except Exception:
            return float(getattr(config, "SPREAD_DW_CAP_NORMAL", 0.42))
        if vix > 35:
            return float(getattr(config, "SPREAD_DW_CAP_PANIC", 0.28))
        if vix >= 25:
            return float(getattr(config, "SPREAD_DW_CAP_HIGH", 0.32))
        if vix >= 18:
            return float(getattr(config, "SPREAD_DW_CAP_ELEVATED", 0.36))
        if vix >= 13:
            return float(getattr(config, "SPREAD_DW_CAP_NORMAL", 0.42))
        return float(getattr(config, "SPREAD_DW_CAP_COMPRESSED", 0.48))

    def _get_spread_absolute_debit_cap(self, vix_level: Optional[float], width: float) -> float:
        """Resolve width-scaled absolute debit cap for debit spreads."""
        base_cap = float(getattr(config, "SPREAD_DW_ABSOLUTE_CAP", 2.00))
        if bool(getattr(config, "SPREAD_DW_ABSOLUTE_CAP_DYNAMIC_ENABLED", False)):
            try:
                vix_value = float(vix_level) if vix_level is not None else 20.0
            except Exception:
                vix_value = 20.0
            vix_floor = max(0.1, float(getattr(config, "SPREAD_DW_ABSOLUTE_CAP_VIX_FLOOR", 10.0)))
            baseline = float(getattr(config, "SPREAD_DW_ABSOLUTE_CAP_BASELINE", 1.00))
            vix_scale = float(getattr(config, "SPREAD_DW_ABSOLUTE_CAP_VIX_SCALE", 20.0))
            cap_min = float(getattr(config, "SPREAD_DW_ABSOLUTE_CAP_MIN", 1.60))
            cap_max = float(getattr(config, "SPREAD_DW_ABSOLUTE_CAP_MAX", 2.60))
            dynamic_cap = baseline * (1.0 + (vix_scale / max(vix_value, vix_floor)))
            base_cap = max(cap_min, min(cap_max, dynamic_cap))
        if width <= 0:
            return base_cap
        return base_cap * (width / 5.0)

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
        dte_min: int = None,
        dte_max: int = None,
        is_eod_scan: bool = False,
        direction: Optional[OptionDirection] = None,
        ma50_value: float = 0.0,
        candidate_contracts: Optional[List[OptionContract]] = None,
    ) -> Optional[TargetWeight]:
        """Check for debit spread entry signal."""
        return check_spread_entry_signal_impl(
            self,
            regime_score=regime_score,
            vix_current=vix_current,
            adx_value=adx_value,
            current_price=current_price,
            ma200_value=ma200_value,
            iv_rank=iv_rank,
            current_hour=current_hour,
            current_minute=current_minute,
            current_date=current_date,
            portfolio_value=portfolio_value,
            long_leg_contract=long_leg_contract,
            short_leg_contract=short_leg_contract,
            gap_filter_triggered=gap_filter_triggered,
            vol_shock_active=vol_shock_active,
            size_multiplier=size_multiplier,
            margin_remaining=margin_remaining,
            dte_min=dte_min,
            dte_max=dte_max,
            is_eod_scan=is_eod_scan,
            direction=direction,
            ma50_value=ma50_value,
            candidate_contracts=candidate_contracts,
        )

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
        is_eod_scan: bool = False,
        direction: Optional[OptionDirection] = None,
    ) -> Optional[TargetWeight]:
        """Check for credit spread entry signal."""
        return check_credit_spread_entry_signal_impl(
            self,
            regime_score=regime_score,
            vix_current=vix_current,
            adx_value=adx_value,
            current_price=current_price,
            ma200_value=ma200_value,
            iv_rank=iv_rank,
            current_hour=current_hour,
            current_minute=current_minute,
            current_date=current_date,
            portfolio_value=portfolio_value,
            short_leg_contract=short_leg_contract,
            long_leg_contract=long_leg_contract,
            strategy=strategy,
            gap_filter_triggered=gap_filter_triggered,
            vol_shock_active=vol_shock_active,
            size_multiplier=size_multiplier,
            margin_remaining=margin_remaining,
            is_eod_scan=is_eod_scan,
            direction=direction,
        )

    # =========================================================================
    # V5.3 ASSIGNMENT RISK MANAGEMENT (P0/P1 Fixes)
    # =========================================================================

    def _is_short_leg_deep_itm(
        self,
        short_leg: "OptionContract",
        underlying_price: float,
        current_dte: int,
    ) -> Tuple[bool, str]:
        return is_short_leg_deep_itm_impl(
            self,
            short_leg=short_leg,
            underlying_price=underlying_price,
            current_dte=current_dte,
        )

    def _check_overnight_itm_short_risk(
        self,
        short_leg: "OptionContract",
        underlying_price: float,
        current_dte: int,
        current_hour: int,
        current_minute: int,
    ) -> Tuple[bool, str]:
        return check_overnight_itm_short_risk_impl(
            self,
            short_leg=short_leg,
            underlying_price=underlying_price,
            current_dte=current_dte,
            current_hour=current_hour,
            current_minute=current_minute,
        )

    def _check_assignment_margin_buffer(
        self,
        spread: "SpreadPosition",
        underlying_price: float,
        available_margin: float,
    ) -> Tuple[bool, str]:
        return check_assignment_margin_buffer_impl(
            self,
            spread=spread,
            underlying_price=underlying_price,
            available_margin=available_margin,
        )

    def _check_short_leg_itm_exit(
        self,
        short_leg: "OptionContract",
        underlying_price: float,
    ) -> Tuple[bool, str]:
        return check_short_leg_itm_exit_impl(
            self,
            short_leg=short_leg,
            underlying_price=underlying_price,
        )

    def check_premarket_itm_shorts(
        self,
        underlying_price: float,
        spread_override: Optional[SpreadPosition] = None,
    ) -> Optional[List[TargetWeight]]:
        return check_premarket_itm_shorts_impl(
            self,
            underlying_price=underlying_price,
            spread_override=spread_override,
        )

    def check_assignment_risk_exit(
        self,
        underlying_price: float,
        current_dte: int,
        current_hour: int,
        current_minute: int,
        available_margin: float = 0,
        spread_override: Optional[SpreadPosition] = None,
    ) -> Optional[List[TargetWeight]]:
        return check_assignment_risk_exit_impl(
            self,
            underlying_price=underlying_price,
            current_dte=current_dte,
            current_hour=current_hour,
            current_minute=current_minute,
            available_margin=available_margin,
            spread_override=spread_override,
        )

    def get_assignment_aware_size_multiplier(
        self,
        spread_width: float,
        portfolio_value: float,
        requested_contracts: int,
    ) -> float:
        return get_assignment_aware_size_multiplier_impl(
            self,
            spread_width=spread_width,
            portfolio_value=portfolio_value,
            requested_contracts=requested_contracts,
        )

    def handle_partial_assignment(
        self,
        assigned_symbol: str,
        assigned_quantity: int,
    ) -> Optional[List[TargetWeight]]:
        return handle_partial_assignment_impl(
            self,
            assigned_symbol=assigned_symbol,
            assigned_quantity=assigned_quantity,
        )

    def _record_vass_mfe_diag(self, spread: SpreadPosition, prev_tier: int) -> None:
        return record_vass_mfe_diag_impl(self, spread=spread, prev_tier=prev_tier)

    def _get_vass_exit_profile(
        self, spread: SpreadPosition, vix_current: Optional[float]
    ) -> Dict[str, Any]:
        return get_vass_exit_profile_impl(self, spread=spread, vix_current=vix_current)

    def _resolve_qqq_atr_pct(self, underlying_price: Optional[float]) -> Optional[float]:
        return resolve_qqq_atr_pct_impl(self, underlying_price=underlying_price)

    def check_spread_exit_signals(
        self,
        current_price: float,
        spread: Optional[SpreadPosition] = None,
        current_dte: Optional[int] = None,
        current_vix: Optional[float] = None,
        vix_5d_change: Optional[float] = None,
        current_time: Optional[datetime] = None,
        ma200_value: float = 0.0,
        current_date: Optional[str] = None,
        is_eod_scan: bool = False,
        regime_score: Optional[float] = None,
    ) -> Optional[List[TargetWeight]]:
        return check_spread_exit_signals_impl(
            self,
            current_price=current_price,
            spread=spread,
            current_dte=current_dte,
            current_vix=current_vix,
            vix_5d_change=vix_5d_change,
            current_time=current_time,
            ma200_value=ma200_value,
            current_date=current_date,
            is_eod_scan=is_eod_scan,
            regime_score=regime_score,
        )

    # =========================================================================
    # V2.4.1 FRIDAY FIREWALL
    # =========================================================================

    def check_friday_firewall_exit(
        self,
        current_vix: float,
        current_date: str,
        vix_close_all_threshold: float = 25.0,
        vix_keep_fresh_threshold: float = 15.0,
        spread_override: Optional[SpreadPosition] = None,
    ) -> Optional[List[TargetWeight]]:
        return check_friday_firewall_exit_impl(
            self,
            current_vix=current_vix,
            current_date=current_date,
            vix_close_all_threshold=vix_close_all_threshold,
            vix_keep_fresh_threshold=vix_keep_fresh_threshold,
            spread_override=spread_override,
        )

    def check_overnight_gap_protection_exit(
        self,
        current_vix: float,
        current_date: str,
    ) -> Optional[List[TargetWeight]]:
        return check_overnight_gap_protection_exit_impl(
            self,
            current_vix=current_vix,
            current_date=current_date,
        )

    def _get_intraday_exit_profile(self, entry_strategy: str) -> Tuple[float, Optional[float]]:
        return get_intraday_exit_profile_impl(self, entry_strategy=entry_strategy)

    def _apply_intraday_target_overrides(
        self,
        *,
        entry_strategy: str,
        target_pct: float,
        current_dte: Optional[int],
    ) -> float:
        return apply_intraday_target_overrides_impl(
            self,
            entry_strategy=entry_strategy,
            target_pct=target_pct,
            current_dte=current_dte,
        )

    def _apply_intraday_stop_overrides(
        self,
        *,
        entry_strategy: str,
        stop_pct: float,
        current_dte: Optional[int],
    ) -> float:
        return apply_intraday_stop_overrides_impl(
            self,
            entry_strategy=entry_strategy,
            stop_pct=stop_pct,
            current_dte=current_dte,
        )

    def _get_trail_config(self, entry_strategy: str) -> Optional[Tuple[float, float]]:
        return get_trail_config_impl(self, entry_strategy=entry_strategy)

    def check_exit_signals(
        self,
        current_price: float,
        current_dte: Optional[int] = None,
        position: "Optional[OptionsPosition]" = None,
    ) -> Optional[TargetWeight]:
        return check_exit_signals_impl(
            self,
            current_price=current_price,
            current_dte=current_dte,
            position=position,
        )

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
            symbol=self._symbol_str(symbol),
            target_weight=0.0,
            source="OPT",
            urgency=Urgency.IMMEDIATE,
            reason=reason,
            requested_quantity=max(1, int(getattr(self._position, "num_contracts", 1))),
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

    def _classify_intraday_bucket(self, strategy_name: str) -> str:
        """Map intraday strategy names to reservation buckets."""
        name = str(strategy_name or "").upper()
        if "ITM_MOMENTUM" in name:
            return "ITM"
        return "OTM"

    def _get_reserved_bucket_usage_dollars(self) -> Dict[str, float]:
        """Current reserved capital-at-risk usage per options bucket."""
        usage = {"VASS": 0.0, "ITM": 0.0, "OTM": 0.0}

        # Active swing spreads (VASS)
        for spread in self.get_spread_positions():
            try:
                spread_type = str(getattr(spread, "spread_type", "") or "").upper()
                num = max(1, int(getattr(spread, "num_spreads", 1) or 1))
                width = float(getattr(spread, "width", 0.0) or 0.0)
                net_debit = float(getattr(spread, "net_debit", 0.0) or 0.0)
                if "CREDIT" in spread_type:
                    credit = abs(net_debit)
                    per_spread_risk = max(0.0, (width - credit) * 100.0)
                else:
                    per_spread_risk = max(0.0, net_debit * 100.0)
                usage["VASS"] += per_spread_risk * num
            except Exception:
                continue

        # Pending spread entry
        if self._pending_spread_type is not None and self._pending_num_contracts is not None:
            try:
                spread_type = str(self._pending_spread_type or "").upper()
                num = max(1, int(self._pending_num_contracts or 1))
                width = float(self._pending_spread_width or 0.0)
                net_debit = float(self._pending_net_debit or 0.0)
                if "CREDIT" in spread_type:
                    credit = abs(net_debit)
                    per_spread_risk = max(0.0, (width - credit) * 100.0)
                else:
                    per_spread_risk = max(0.0, net_debit * 100.0)
                usage["VASS"] += per_spread_risk * num
            except Exception:
                pass

        # Active intraday positions
        for intraday_pos in self.get_intraday_positions():
            try:
                bucket = self._classify_intraday_bucket(
                    str(getattr(intraday_pos, "entry_strategy", "") or "")
                )
                risk = (
                    float(getattr(intraday_pos, "entry_price", 0.0) or 0.0)
                    * max(1, int(getattr(intraday_pos, "num_contracts", 1) or 1))
                    * 100.0
                )
                usage[bucket] += max(0.0, risk)
            except Exception:
                pass

        # Pending intraday entry
        if self._pending_intraday_entry and self._pending_num_contracts is not None:
            try:
                strategy = str(self._pending_entry_strategy or "")
                bucket = self._classify_intraday_bucket(strategy)
                premium = float(
                    getattr(self._pending_contract, "mid_price", 0.0)
                    if self._pending_contract
                    else 0.0
                )
                risk = premium * max(1, int(self._pending_num_contracts or 1)) * 100.0
                usage[bucket] += max(0.0, risk)
            except Exception:
                pass

        return usage

    def _get_bucket_remaining_dollars(self, bucket: str, portfolio_value: float) -> float:
        """Hard reservation remaining dollars for the requested bucket."""
        usage = self._get_reserved_bucket_usage_dollars()
        bucket_u = str(bucket or "").upper()
        cap = 0.0
        if bucket_u == "VASS":
            cap = float(getattr(config, "VASS_MAX_RISK_DOLLARS", 0.0) or 0.0)
        elif bucket_u == "ITM":
            cap = float(getattr(config, "INTRADAY_ITM_MAX_DOLLARS", 0.0) or 0.0)
            if cap <= 0:
                cap = float(portfolio_value) * float(getattr(config, "INTRADAY_ITM_MAX_PCT", 0.15))
        elif bucket_u == "OTM":
            cap = float(getattr(config, "INTRADAY_OTM_MAX_DOLLARS", 0.0) or 0.0)
            if cap <= 0:
                cap = float(portfolio_value) * float(getattr(config, "INTRADAY_OTM_MAX_PCT", 0.10))
        used = float(usage.get(bucket_u, 0.0))
        return max(0.0, cap - used)

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
        """VASS strategy routing delegated to VASSEntryEngine."""
        return self._vass_entry_engine.select_strategy(
            direction=direction,
            iv_environment=iv_environment,
            is_intraday=is_intraday,
            spread_strategy_enum=SpreadStrategy,
        )

    def is_credit_strategy(self, strategy: SpreadStrategy) -> bool:
        """Check if strategy is a credit spread (collects premium)."""
        return strategy in (SpreadStrategy.BULL_PUT_CREDIT, SpreadStrategy.BEAR_CALL_CREDIT)

    def is_debit_strategy(self, strategy: SpreadStrategy) -> bool:
        """Check if strategy is a debit spread (pays premium)."""
        return strategy in (SpreadStrategy.BULL_CALL_DEBIT, SpreadStrategy.BEAR_PUT_DEBIT)

    def update_iv_sensor(self, vix_current: float, current_date: str = "") -> None:
        """Update IV sensor with current VIX reading and optional date key."""
        self._iv_sensor.update(vix_current, current_date or None)

    def get_iv_conviction(self) -> Tuple[bool, Optional[str], str]:
        """Return IV conviction tuple from IV sensor."""
        return self._iv_sensor.has_conviction()

    def get_iv_bearish_veto_status(self) -> Tuple[bool, str]:
        """Return strict bearish hard-veto eligibility from IV sensor."""
        return self._iv_sensor.is_bearish_veto_ready()

    def is_iv_sensor_ready(self) -> bool:
        """True when IV sensor has enough intraday history."""
        return self._iv_sensor.is_ready()

    def get_iv_environment(self) -> str:
        """Classify current IV environment (LOW/MEDIUM/HIGH)."""
        return self._iv_sensor.classify()

    def select_vass_strategy(
        self,
        direction: str,
        iv_environment: str,
        is_intraday: bool = False,
    ) -> Tuple[SpreadStrategy, int, int]:
        """Public wrapper for VASS direction+IV strategy routing."""
        return self._select_strategy(direction, iv_environment, is_intraday=is_intraday)

    def resolve_vass_strategy(
        self,
        direction: str,
        overlay_state: Optional[str] = None,
    ) -> Tuple[Optional[SpreadStrategy], int, int, bool]:
        """Resolve VASS route including EARLY_STRESS strategy remap."""
        if getattr(config, "VASS_ENABLED", True) and self.is_iv_sensor_ready():
            iv_environment = self.get_iv_environment()
            return self._vass_entry_engine.resolve_strategy_with_overlay(
                direction=direction,
                overlay_state=overlay_state,
                iv_environment=iv_environment,
                spread_strategy_enum=SpreadStrategy,
                is_credit_strategy_func=self.is_credit_strategy,
            )
        return (None, config.SPREAD_DTE_MIN, config.SPREAD_DTE_MAX, False)

    def strategy_option_right(self, strategy: Optional[SpreadStrategy]) -> Optional[str]:
        """Return required option right key (CALL/PUT) for a VASS spread strategy."""
        return self._vass_entry_engine.strategy_option_right(strategy)

    def build_vass_dte_fallbacks(self, dte_min: int, dte_max: int) -> List[Tuple[int, int]]:
        """Build ordered DTE ranges for VASS spread selection."""
        return self._vass_entry_engine.build_dte_fallbacks(dte_min, dte_max)

    def get_contract_prices(self, contract: Any) -> Tuple[float, float]:
        """Get contract bid/ask with last-price fallback when quotes are stale."""
        bid = getattr(contract, "BidPrice", 0) or 0
        ask = getattr(contract, "AskPrice", 0) or 0
        if bid > 0 and ask > 0:
            return (bid, ask)
        last = getattr(contract, "LastPrice", 0) or 0
        if last <= 0:
            return (0, 0)
        spread_pct = 0.10 if last < 1.0 else (0.05 if last < 5.0 else 0.02)
        half_spread = spread_pct / 2
        return (last * (1 - half_spread), last * (1 + half_spread))

    def build_vass_candidate_contracts(
        self,
        chain: Any,
        direction: OptionDirection,
        dte_min: Optional[int] = None,
        dte_max: Optional[int] = None,
        option_right: Optional[Any] = None,
    ) -> List[OptionContract]:
        """Build VASS candidate contracts via VASS entry engine."""
        return self._vass_entry_engine.build_candidate_contracts(
            host=self,
            chain=chain,
            direction=direction,
            dte_min=dte_min,
            dte_max=dte_max,
            option_right=option_right,
            contract_model_cls=OptionContract,
        )

    def check_vass_bull_debit_trend_confirmation(
        self,
        *,
        vix_current: Optional[float],
        current_price: float,
        qqq_open: Optional[float],
        qqq_sma20: Optional[float],
        qqq_sma20_ready: bool,
    ) -> Tuple[bool, str, str]:
        """Delegate VASS bullish debit trend confirmation to VASSEntryEngine."""
        transition_ctx = self._get_regime_transition_context()
        base_regime = str(transition_ctx.get("base_regime", "") or "").upper()
        transition_overlay = str(transition_ctx.get("transition_overlay", "") or "").upper()
        recovery_relax = bool(getattr(config, "VASS_RECOVERY_RELAX_ENABLED", True)) and (
            base_regime == "BULLISH" or transition_overlay == "RECOVERY"
        )
        return self._vass_entry_engine.check_bull_debit_trend_confirmation(
            vix_current=vix_current,
            current_price=current_price,
            qqq_open=qqq_open,
            qqq_sma20=qqq_sma20,
            qqq_sma20_ready=qqq_sma20_ready,
            relax_recovery=recovery_relax,
            relaxed_day_min_change_pct=float(
                getattr(config, "VASS_RECOVERY_RELAX_DAY_MIN_CHANGE_PCT", -0.05)
            ),
            ma20_tolerance_pct=float(
                getattr(config, "VASS_RECOVERY_RELAX_MA20_TOLERANCE_PCT", 0.003)
            ),
        )

    def get_itm_direction_proposal(
        self,
        qqq_current: float,
        transition_ctx: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Optional[OptionDirection], str]:
        """Return ITM_ENGINE sovereign direction proposal from daily trend context."""
        if not self._itm_horizon_engine.enabled():
            return None, "ITM_ENGINE_DISABLED"
        return self._itm_horizon_engine.get_direction_proposal(
            host=self,
            qqq_current=qqq_current,
            transition_ctx=transition_ctx,
        )

    def check_micro_spike_alert(
        self, vix_current: float, vix_5min_ago: float, current_time: str
    ) -> bool:
        """Expose micro spike-alert check without leaking engine internals."""
        return self._micro_regime_engine.check_spike_alert(vix_current, vix_5min_ago, current_time)

    def update_micro_regime_state(
        self,
        vix_current: float,
        vix_open: float,
        qqq_current: float,
        qqq_open: float,
        current_time: str,
        macro_regime_score: float = 50.0,
        vix_level_override: Optional[float] = None,
    ) -> MicroRegimeState:
        """Public wrapper for full micro-regime update."""
        return self._micro_regime_engine.update(
            vix_current=vix_current,
            vix_open=vix_open,
            qqq_current=qqq_current,
            qqq_open=qqq_open,
            current_time=current_time,
            macro_regime_score=macro_regime_score,
            vix_level_override=vix_level_override,
        )

    def set_rejection_margin_cap(self, cap: Optional[float]) -> None:
        """Set/reset adaptive rejection margin cap used by spread sizing."""
        if cap is None:
            self._rejection_margin_cap = None
            return
        self._rejection_margin_cap = max(0.0, float(cap))

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
        is_eod_scan: bool = False,
    ) -> Tuple[bool, str]:
        """Swing-mode entry filters delegated to VASSEntryEngine."""
        return self._vass_entry_engine.check_swing_filters(
            direction=direction,
            spy_gap_pct=spy_gap_pct,
            spy_intraday_change_pct=spy_intraday_change_pct,
            vix_intraday_change_pct=vix_intraday_change_pct,
            current_hour=current_hour,
            current_minute=current_minute,
            enforce_time_window=not bool(is_eod_scan),
        )

    def check_intraday_entry_signal(
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
        strategy_override: Optional[IntradayStrategy] = None,
        current_dte: Optional[int] = None,
    ) -> Optional[TargetWeight]:
        return check_intraday_entry_signal_impl(
            self,
            regime_score=regime_score,
            vix_current=vix_current,
            adx_value=adx_value,
            current_price=current_price,
            ma200_value=ma200_value,
            iv_rank=iv_rank,
            current_hour=current_hour,
            current_minute=current_minute,
            current_date=current_date,
            portfolio_value=portfolio_value,
            strategy_override=strategy_override,
            current_dte=current_dte,
        )

    def check_intraday_force_exit(
        self,
        current_hour: int,
        current_minute: int,
        current_price: float,
        ignore_hold_policy: bool = False,
        engine: Optional[str] = None,
        symbol: Optional[str] = None,
    ) -> Optional[TargetWeight]:
        """
        Check for forced exit of intraday position at configured intraday cutoff.

        Intraday mode positions are normally closed by configured force-exit time.
        V10.1 exception: hold-enabled ITM_MOMENTUM positions may carry overnight.

        Args:
            current_hour: Current hour (0-23) Eastern.
            current_minute: Current minute (0-59).
            current_price: Current option price.

        Returns:
            TargetWeight for forced exit, or None.
        """
        symbol_key = self._normalize_symbol_key(symbol)
        position = None
        lane = None
        if symbol_key is not None:
            lane = self._find_intraday_lane_by_symbol(symbol_key)
            if lane is None:
                return None
            for pos in self._intraday_positions.get(lane) or []:
                if (
                    pos is not None
                    and pos.contract is not None
                    and self._symbol_str(pos.contract.symbol) == symbol_key
                ):
                    position = pos
                    break
        else:
            position = self.get_intraday_position(engine=engine)
            lane = (
                engine
                or self._intraday_engine_lane_from_strategy(getattr(position, "entry_strategy", ""))
                if position is not None
                else None
            )

        if position is None:
            return None

        # V2.3.3 FIX #3: Prevent duplicate exit signals while waiting for fill
        if self.has_pending_intraday_exit(symbol=self._symbol_str(position.contract.symbol)):
            return None

        force_hh, force_mm = self._get_intraday_force_exit_hhmm()
        force_exit_time = current_hour > force_hh or (
            current_hour == force_hh and current_minute >= force_mm
        )

        if not force_exit_time:
            return None

        symbol = position.contract.symbol
        symbol_str = self._symbol_str(symbol)
        if self.should_hold_intraday_overnight(position):
            if ignore_hold_policy:
                self.log(
                    f"INTRADAY_FORCE_EXIT_OVERRIDE_HOLD {symbol_str} | "
                    f"Strategy={getattr(position, 'entry_strategy', 'UNKNOWN')}",
                    trades_only=True,
                )
            else:
                if self.algorithm is not None and hasattr(self.algorithm, "Time"):
                    current_date = str(self.algorithm.Time.date())
                else:
                    current_date = "NO_ALGO_TIME"
                if self._intraday_force_exit_hold_skip_log_date.get(symbol_str) != current_date:
                    live_dte = self._get_position_live_dte(position)
                    self.log(
                        f"INTRADAY_FORCE_EXIT_SKIP_HOLD {symbol_str} | "
                        f"Strategy={getattr(position, 'entry_strategy', 'UNKNOWN')} | "
                        f"LiveDTE={live_dte}",
                        trades_only=True,
                    )
                    self._intraday_force_exit_hold_skip_log_date[symbol_str] = current_date
                return None

        entry_price = position.entry_price
        num_contracts = max(1, int(position.num_contracts))

        pnl_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0

        reason = (
            f"INTRADAY_TIME_EXIT_{force_hh:02d}{force_mm:02d} "
            f"{pnl_pct:+.1%} (Price: ${current_price:.2f})"
        )
        self.log(f"INTRADAY_FORCE_EXIT {symbol} | {reason}", trades_only=True)

        # V2.3.3: Set pending exit flag to prevent duplicate signals
        if not self.mark_pending_intraday_exit(symbol_str):
            return None

        return TargetWeight(
            symbol=self._symbol_str(symbol),
            target_weight=0.0,
            source="OPT_INTRADAY",
            urgency=Urgency.IMMEDIATE,
            reason=reason,
            requested_quantity=num_contracts,
        )

    def check_gamma_pin_exit(
        self,
        current_price: float,
        current_dte: int,
        spread_override: Optional[SpreadPosition] = None,
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

        # V6.5 FIX: Prevent order spam - only trigger once per position
        if self._gamma_pin_exit_triggered:
            return None

        # Only check spread positions (credit or debit)
        spread = spread_override or self.get_spread_position()
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

        # V6.5 FIX: Mark as triggered to prevent order spam every minute
        self._gamma_pin_exit_triggered = True

        # Get the number of spreads/contracts for the close order
        num_contracts = getattr(spread, "num_spreads", getattr(spread, "num_contracts", 1))

        # Return spread exit signal (same format as spread exit)
        # V6.5 FIX: Added spread_short_leg_quantity to enable combo close order
        return [
            TargetWeight(
                symbol=self._symbol_str(spread.long_leg.symbol),
                target_weight=0.0,
                source="OPT",
                urgency=Urgency.IMMEDIATE,
                reason=f"GAMMA_PIN_BUFFER (price within {distance_pct:.2%} of strike ${short_strike})",
                requested_quantity=num_contracts,
                metadata={
                    "spread_close_short": True,
                    "spread_short_leg_symbol": self._symbol_str(spread.short_leg.symbol),
                    "spread_short_leg_quantity": num_contracts,
                    "spread_key": self._build_spread_key(
                        spread
                    ),  # V6.5 FIX: Required for combo close
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
        position: Optional[OptionsPosition] = None,
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

        # Position is expected from caller; legacy fallback kept for compatibility.
        position = position or self._position or self.get_intraday_position()
        if position is None:
            return None

        # MICRO intraday strategies are explicitly managed by intraday force-exit rules.
        # Keep noon expiration hammer active for PROTECTIVE_PUTS and non-MICRO positions.
        strategy_name = self._canonical_intraday_strategy_name(
            getattr(position, "entry_strategy", None)
        )
        if strategy_name in (
            IntradayStrategy.MICRO_DEBIT_FADE.value,
            IntradayStrategy.MICRO_OTM_MOMENTUM.value,
        ):
            symbol_str = self._symbol_str(getattr(position.contract, "symbol", ""))
            if symbol_str:
                last_logged = self._expiring_hammer_skip_log_date.get(symbol_str)
                if last_logged != current_date:
                    self._expiring_hammer_skip_log_date[symbol_str] = current_date
                    self.log(
                        f"EXPIRATION_HAMMER_V2_SKIP: {symbol_str} | "
                        f"Strategy={strategy_name} managed by intraday force exit",
                        trades_only=True,
                    )
            return None

        # Strategy-aware expiration hammer cutoff.
        # MICRO intraday paths are skipped above; PROTECTIVE_PUTS uses a later cutoff.
        if strategy_name == IntradayStrategy.PROTECTIVE_PUTS.value:
            force_close_hour = int(
                getattr(config, "PROTECTIVE_PUTS_EXPIRING_TODAY_FORCE_CLOSE_HOUR", 15)
            )
            force_close_minute = int(
                getattr(config, "PROTECTIVE_PUTS_EXPIRING_TODAY_FORCE_CLOSE_MINUTE", 15)
            )
        else:
            force_close_hour = config.OPTIONS_EXPIRING_TODAY_FORCE_CLOSE_HOUR
            force_close_minute = config.OPTIONS_EXPIRING_TODAY_FORCE_CLOSE_MINUTE

        force_exit_time = current_hour > force_close_hour or (
            current_hour == force_close_hour and current_minute >= force_close_minute
        )

        if not force_exit_time:
            return None

        symbol = position.contract.symbol
        entry_price = position.entry_price
        source = (
            "OPT_INTRADAY"
            if self._find_intraday_lane_by_symbol(self._symbol_str(symbol)) is not None
            else "OPT_SWING"
        )

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

        # Keep intraday software exit lock aligned for expiring-day close signals.
        if self._find_intraday_lane_by_symbol(self._symbol_str(symbol)) is not None:
            if not self.mark_pending_intraday_exit(self._symbol_str(symbol)):
                return None

        return TargetWeight(
            symbol=self._symbol_str(symbol),
            target_weight=0.0,
            source=source,
            urgency=Urgency.IMMEDIATE,
            reason=reason,
            requested_quantity=max(1, int(getattr(position, "num_contracts", 1))),
        )

    def get_micro_regime_state(self) -> MicroRegimeState:
        """Get current Micro Regime Engine state."""
        return self._micro_regime_engine.get_state()

    def get_itm_horizon_state(self) -> Dict[str, Any]:
        """Return ITM horizon state for diagnostics summaries."""
        try:
            return dict(self._itm_horizon_engine.to_dict())
        except Exception:
            return {}

    def get_intraday_direction(
        self,
        vix_current: float,
        vix_open: float,
        qqq_current: float,
        qqq_open: float,
        current_time: str,
        regime_score: float = 50.0,
        vix_level_override: Optional[float] = None,  # V6.2: CBOE VIX for level consistency
    ) -> Optional[OptionDirection]:
        """
        V2.3.14: Get recommended intraday direction from Micro Regime Engine.
        V2.3.16: Added direction conflict resolution (centralized).
        V6.2: Added vix_level_override for consistent VIX level classification.

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
            vix_level_override: V6.2 - CBOE VIX for level classification (ensures consistency
                with scheduled _update_micro_regime calls).

        Returns:
            Recommended OptionDirection (CALL or PUT), or None if NO_TRADE or conflict.
        """
        # Update Micro Regime Engine (V2.5: pass macro_regime_score for Grind-Up Override)
        # V6.2: Pass vix_level_override for consistent level classification
        state = self._micro_regime_engine.update(
            vix_current=vix_current,
            vix_open=vix_open,
            qqq_current=qqq_current,
            qqq_open=qqq_open,
            current_time=current_time,
            macro_regime_score=regime_score,
            vix_level_override=vix_level_override,  # V6.2: Pass through
        )

        direction = state.recommended_direction

        # If no direction recommended, return None
        if direction is None:
            return None

        # V2.3.16: Direction conflict resolution for FADE strategies
        # Skip intraday FADE when macro regime strongly disagrees with direction
        # This prevents counter-trend trades in strongly trending markets
        if state.recommended_strategy in (
            IntradayStrategy.MICRO_DEBIT_FADE,
            IntradayStrategy.MICRO_OTM_MOMENTUM,
            IntradayStrategy.DEBIT_FADE,
        ):
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
            IntradayStrategy enum (MICRO_DEBIT_FADE, MICRO_OTM_MOMENTUM, ITM_MOMENTUM, NO_TRADE, etc.)
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
        force_intraday: bool = False,
        symbol: Optional[str] = None,
        order_tag: Optional[str] = None,
    ) -> Optional[OptionsPosition]:
        """
        Register a new options position after fill.

        Args:
            fill_price: Actual fill price.
            entry_time: Entry timestamp string.
            current_date: Current date string.
            contract: Option contract (uses pending if not provided).
            force_intraday: If True, classify fill as intraday even when pending
                flag was cleared by a cancel/fallback race.
            order_tag: Optional broker order tag used for recovery strategy inference.

        Returns:
            Created OptionsPosition, or None if no pending contract exists.
        """
        pending_payload = None
        symbol_norm = self._symbol_str(symbol) if symbol else ""
        if symbol_norm:
            pending_payload = self._get_pending_intraday_entry_payload(symbol=symbol_norm)

        # Use pending values from check_entry_signal
        if contract is None:
            contract = (
                pending_payload.get("contract")
                if pending_payload is not None
                else self._pending_contract
            )

        # Guard: If no pending contract exists, we can't register entry
        # This can happen if fill occurs for an order placed outside our signal flow
        if contract is None:
            self.log("OPT: register_entry called but no pending contract - skipping")
            return None

        # Use pending values if set, otherwise defaults
        # Note: getattr defaults don't work when attr exists but is None
        entry_score = (
            pending_payload.get("entry_score")
            if pending_payload is not None and pending_payload.get("entry_score") is not None
            else self._pending_entry_score
            if self._pending_entry_score is not None
            else 3.0
        )
        num_contracts = (
            pending_payload.get("num_contracts")
            if pending_payload is not None and pending_payload.get("num_contracts") is not None
            else self._pending_num_contracts
            if self._pending_num_contracts is not None
            else 1
        )
        stop_pct = (
            pending_payload.get("stop_pct")
            if pending_payload is not None and pending_payload.get("stop_pct") is not None
            else self._pending_stop_pct
            if self._pending_stop_pct is not None
            else 0.20
        )

        recovered_strategy = None
        if force_intraday and pending_payload is None:
            inferred = self._infer_intraday_strategy_from_order_tag(order_tag)
            if inferred and inferred != IntradayStrategy.NO_TRADE.value:
                recovered_strategy = inferred

        entry_strategy = recovered_strategy or (
            pending_payload.get("entry_strategy")
            if pending_payload is not None and pending_payload.get("entry_strategy")
            else self._pending_entry_strategy or "SWING_SINGLE"
        )
        if recovered_strategy is not None:
            self.log(
                f"INTRADAY_RECOVERY_STRATEGY: Symbol={self._symbol_str(contract.symbol)} | "
                f"Strategy={recovered_strategy} | Tag={str(order_tag or 'NO_TAG')[:120]}",
                trades_only=True,
            )

        # Recalculate stop and target based on actual fill price
        stop_price = fill_price * (1 - stop_pct)

        is_intraday_fill = (
            force_intraday or pending_payload is not None or self._pending_intraday_entry
        )
        if is_intraday_fill:
            target_pct, strategy_floor = self._get_intraday_exit_profile(entry_strategy)
            current_dte = int(getattr(contract, "days_to_expiry", 0))
            target_pct = self._apply_intraday_target_overrides(
                entry_strategy=entry_strategy,
                target_pct=float(target_pct),
                current_dte=current_dte,
            )
            stop_pct = self._apply_intraday_stop_overrides(
                entry_strategy=entry_strategy,
                stop_pct=float(stop_pct),
                current_dte=current_dte,
            )
            target_price = fill_price * (1 + target_pct)
            stop_price = fill_price * (1 - stop_pct)
            if strategy_floor is not None and strategy_floor > 0:
                # V9.2 FIX: Use max(ATR stop, strategy floor) to preserve ATR adaptive
                # intelligence. The ATR stop accounts for market volatility (wider in
                # high-VIX), while the strategy floor prevents too-tight stops in calm
                # markets. Using max() means: in calm markets the floor protects, in
                # volatile markets the ATR stop widens appropriately.
                atr_stop_pct = stop_pct  # ATR-calculated value from signal time
                stop_pct = max(stop_pct, float(strategy_floor))
                stop_price = fill_price * (1 - stop_pct)
                if stop_pct > atr_stop_pct:
                    self.log(
                        f"STOP_OVERRIDE: {entry_strategy} floor {strategy_floor:.0%} > "
                        f"ATR {atr_stop_pct:.0%} → using floor"
                    )
                else:
                    self.log(
                        f"STOP_OVERRIDE: ATR {atr_stop_pct:.0%} >= "
                        f"{entry_strategy} floor {strategy_floor:.0%} → keeping ATR"
                    )
        else:
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
            entry_strategy=entry_strategy,
            highest_price=fill_price,
        )

        # V2.3.2 FIX #4: Track position in correct variable based on mode
        if is_intraday_fill:
            lane = self._intraday_engine_lane_from_strategy(entry_strategy)
            self._set_intraday_lane_position(lane, position)
            if symbol_norm:
                self._pop_pending_intraday_entry_payload(symbol=symbol_norm, lane=lane)
            self._pending_intraday_entry = bool(self._pending_intraday_entries)
            self._pending_intraday_entry_since = (
                None if not self._pending_intraday_entries else self._pending_intraday_entry_since
            )
            self._pending_intraday_entry_engine = (
                None if not self._pending_intraday_entries else self._pending_intraday_entry_engine
            )
            # Count intraday trades only after a confirmed fill registration.
            intraday_dir = (
                OptionDirection.CALL
                if str(getattr(contract, "right", "")).upper() == "CALL"
                else OptionDirection.PUT
                if str(getattr(contract, "right", "")).upper() == "PUT"
                else None
            )
            self._increment_trade_counter(
                OptionsMode.INTRADAY, direction=intraday_dir, strategy=entry_strategy
            )
            if force_intraday and not self._pending_intraday_entry:
                self.log(
                    f"OPT: INTRADAY_TAG_RECOVERY | Symbol={contract.symbol} | "
                    f"Strategy={entry_strategy}",
                    trades_only=True,
                )
            self.log(
                f"OPT: INTRADAY position registered (trade #{self._intraday_trades_today}, "
                f"force-close at {self._get_intraday_force_exit_hhmm()[0]:02d}:{self._get_intraday_force_exit_hhmm()[1]:02d})",
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
            f"Target=${target_price:.2f} | "
            f"Stop=${stop_price:.2f} (-{stop_pct:.0%}) | "
            f"Strategy={entry_strategy} | Contracts={num_contracts} | "
            f"Score={entry_score:.2f}"
        )

        # Clear pending state
        self._pending_contract = None
        self._pending_entry_score = None
        self._pending_num_contracts = None
        self._pending_stop_pct = None
        self._pending_stop_price = None
        self._pending_target_price = None
        self._pending_entry_strategy = None

        return position

    def remove_position(self, symbol: Optional[str] = None) -> Optional[OptionsPosition]:
        """
        Remove the current swing single-leg position after exit.

        Args:
            symbol: Optional symbol guard. When provided, removal only occurs
                if it matches the tracked swing position symbol.

        Returns:
            Removed position, or None if no matching position existed.
        """
        if self._position is None:
            return None

        if symbol:
            try:
                expected = self._symbol_str(self._position.contract.symbol)
                actual = self._symbol_str(symbol)
                if expected != actual:
                    return None
            except Exception:
                return None

        position = self._position
        self._position = None
        self.log(f"OPT: POSITION_REMOVED {position.contract.symbol}", trades_only=True)
        return position

    def remove_intraday_position(
        self, symbol: Optional[str] = None, engine: Optional[str] = None
    ) -> Optional[OptionsPosition]:
        """
        V2.3.2: Remove the current intraday position after exit.

        Returns:
            Removed intraday position, or None if no position existed.
        """

        lane = None
        if symbol:
            lane = self._find_intraday_lane_by_symbol(symbol)
            if lane is None:
                return None
        elif engine is not None:
            lane = str(engine).upper()
        else:
            lane = self.get_intraday_position_engine()

        if not lane:
            return None
        lane_key = str(lane).upper()
        lane_positions = self._intraday_positions.get(lane_key) or []
        if not lane_positions:
            return None

        position = None
        if symbol:
            symbol_norm = self._symbol_str(symbol)
            for idx, pos in enumerate(list(lane_positions)):
                if (
                    pos is not None
                    and pos.contract is not None
                    and self._symbol_str(pos.contract.symbol) == symbol_norm
                ):
                    position = pos
                    del lane_positions[idx]
                    break
            if position is None:
                return None
        else:
            position = lane_positions.pop(0)

        self._intraday_positions[lane_key] = lane_positions
        self._refresh_legacy_intraday_mirrors()
        try:
            removed_symbol_key = self._symbol_str(position.contract.symbol)
        except Exception:
            removed_symbol_key = None
        self._pending_intraday_exit_lanes.discard(lane_key)
        if removed_symbol_key:
            self._pending_intraday_exit_symbols.discard(removed_symbol_key)
        self._sync_pending_intraday_exit_flags()
        try:
            strategy = str(getattr(position, "entry_strategy", "") or "UNKNOWN")
        except Exception:
            strategy = "UNKNOWN"
        self._last_intraday_close_strategy = strategy
        self._last_intraday_close_time = self.algorithm.Time if self.algorithm is not None else None
        self.log(
            f"OPT: INTRADAY_POSITION_REMOVED {position.contract.symbol} | " f"Strategy={strategy}",
            trades_only=True,
        )
        return position

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
        # Debit spread: max profit = width - debit paid
        # Credit spread (stored as negative net_debit): max profit = credit received
        max_profit = width - net_debit if net_debit > 0 else abs(net_debit)

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
            entry_vix=self._pending_spread_entry_vix,
        )

        self._spread_neutrality_warn_by_key.pop(self._build_spread_key(spread), None)
        self._spread_positions.append(spread)
        self._spread_position = self._spread_positions[0] if self._spread_positions else None
        spread_dir = (
            OptionDirection.CALL
            if self._spread_direction_label(spread.spread_type) == "BULLISH"
            else OptionDirection.PUT
            if self._spread_direction_label(spread.spread_type) == "BEARISH"
            else None
        )
        signature = (
            self._build_vass_signature(
                spread_type=spread.spread_type,
                direction=spread_dir,
                long_leg_contract=spread.long_leg,
            )
            if spread.long_leg is not None
            else ""
        )
        try:
            entry_dt = datetime.strptime(entry_time[:19], "%Y-%m-%d %H:%M:%S")
        except Exception:
            entry_dt = self.algorithm.Time if self.algorithm is not None else None
        self._record_vass_signature_entry(signature, entry_dt)
        self._record_vass_direction_day_entry(spread_dir, entry_dt)

        # V2.9: Update trade counter (Bug #4 fix) - Spreads are always swing mode
        self._increment_trade_counter(OptionsMode.SWING)

        spread_type_upper = str(spread.spread_type or "").upper()
        if spread_type_upper in {
            "BULL_PUT_CREDIT",
            "BEAR_CALL_CREDIT",
            SpreadStrategy.BULL_PUT_CREDIT.value,
            SpreadStrategy.BEAR_CALL_CREDIT.value,
        }:
            # Credit spread telemetry: target is close-cost threshold to realize configured profit.
            credit_target_pct = float(getattr(config, "CREDIT_SPREAD_PROFIT_TARGET", 0.50))
            target_close_value = abs(net_debit) - (max_profit * credit_target_pct)
            target_telemetry = f"TargetClose<=${target_close_value:.2f} ({credit_target_pct:.0%})"
        else:
            # Debit spread telemetry: mirror configured/adaptive target math from exit logic.
            base_profit_pct = float(getattr(config, "SPREAD_PROFIT_TARGET_PCT", 0.50))
            profit_multipliers = getattr(
                config, "SPREAD_PROFIT_REGIME_MULTIPLIERS", {75: 1.0, 50: 1.0, 40: 1.0, 0: 1.0}
            )
            profit_multiplier = 1.0
            for threshold in sorted(profit_multipliers.keys(), reverse=True):
                if regime_score >= threshold:
                    profit_multiplier = profit_multipliers[threshold]
                    break
            adaptive_profit_pct = base_profit_pct * profit_multiplier
            commission_cost = num_spreads * config.SPREAD_COMMISSION_PER_CONTRACT
            commission_per_share = commission_cost / (num_spreads * 100) if num_spreads > 0 else 0
            target_spread_value = (
                net_debit + (max_profit * adaptive_profit_pct) + commission_per_share
            )
            target_telemetry = f"Target=${target_spread_value:.2f} ({adaptive_profit_pct:.0%}, Comm ${commission_cost:.2f})"

        self.log(
            f"SPREAD: POSITION_REGISTERED | {spread.spread_type} | "
            f"Long={spread.long_leg.strike} @ ${long_leg_fill_price:.2f} | "
            f"Short={spread.short_leg.strike} @ ${short_leg_fill_price:.2f} | "
            f"Net Debit=${net_debit:.2f} | Max Profit=${max_profit:.2f} | "
            f"x{num_spreads} | {target_telemetry}",
            trades_only=True,
        )

        # Clear pending state
        self._pending_spread_long_leg = None
        self._pending_spread_short_leg = None
        self._pending_spread_type = None
        self._pending_net_debit = None
        self._pending_max_profit = None
        self._pending_spread_width = None
        self._pending_spread_entry_vix = None
        self._pending_spread_entry_since = None
        self._pending_num_contracts = None
        self._pending_entry_score = None
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
        self._pending_entry_strategy = None
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
        self._pending_spread_entry_vix = None
        self._pending_spread_entry_since = None
        self._pending_num_contracts = None
        self._pending_entry_score = None
        self.log(
            "OPT_MACRO_RECOVERY: Pending spread entry cancelled | Retry allowed",
            trades_only=True,
        )

    def _clear_stale_pending_spread_entry_if_orphaned(self) -> None:
        """Clear stale pending spread lock when no matching open leg orders exist."""
        if self._pending_spread_long_leg is None or self._pending_spread_short_leg is None:
            return
        if self.algorithm is None or not hasattr(self.algorithm, "Time"):
            return

        if self._pending_spread_entry_since is None:
            self._pending_spread_entry_since = self.algorithm.Time
            return

        stale_minutes = int(getattr(config, "SPREAD_PENDING_ENTRY_STALE_MINUTES", 7))
        if stale_minutes <= 0:
            return

        age_minutes = (
            self.algorithm.Time - self._pending_spread_entry_since
        ).total_seconds() / 60.0
        if age_minutes < stale_minutes:
            return

        pending_symbols = {
            self._symbol_str(self._pending_spread_long_leg.symbol),
            self._symbol_str(self._pending_spread_short_leg.symbol),
        }
        pending_symbols = {s for s in pending_symbols if s}

        has_open_orders = False
        try:
            for open_order in self.algorithm.Transactions.GetOpenOrders():
                if getattr(open_order.Symbol, "SecurityType", None) != SecurityType.Option:
                    continue
                open_sym = self._symbol_str(open_order.Symbol)
                if open_sym in pending_symbols:
                    has_open_orders = True
                    break
        except Exception:
            # Do not clear state when broker/order query fails.
            return

        if has_open_orders:
            return

        self.cancel_pending_spread_entry()
        self.log(
            f"OPT_MACRO_RECOVERY: Cleared stale pending spread entry | "
            f"AgeMin={age_minutes:.1f} | Pending={','.join(sorted(pending_symbols)) or 'NONE'}",
            trades_only=True,
        )

    def has_pending_spread_entry(self) -> bool:
        """True when both pending spread legs are populated."""
        self._clear_stale_pending_spread_entry_if_orphaned()
        return (
            self._pending_spread_long_leg is not None and self._pending_spread_short_leg is not None
        )

    def get_pending_spread_legs(self) -> Tuple[Optional[OptionContract], Optional[OptionContract]]:
        """Expose pending spread legs without direct private-field access."""
        return self._pending_spread_long_leg, self._pending_spread_short_leg

    def get_pending_spread_tracker_seed(self) -> Optional[dict]:
        """Return spread tracker seed payload derived from pending spread state."""
        if not self.has_pending_spread_entry():
            return None
        return {
            "long_leg_symbol": self._symbol_str(self._pending_spread_long_leg.symbol),
            "short_leg_symbol": self._symbol_str(self._pending_spread_short_leg.symbol),
            "expected_quantity": int(self._pending_num_contracts or 1),
            "spread_type": self._pending_spread_type,
        }

    def clear_pending_spread_state_hard(self) -> None:
        """
        Hard reset for stale spread state cleanup.

        Mirrors legacy main.py cleanup fields to preserve behavior.
        """
        self._pending_spread_long_leg = None
        self._pending_spread_short_leg = None
        self._pending_spread_type = None
        self._pending_net_debit = None
        self._pending_max_profit = None
        self._pending_spread_width = None
        self._pending_spread_entry_vix = None
        self._pending_spread_entry_since = None
        self._pending_num_contracts = None
        self._pending_entry_score = None
        self._pending_stop_pct = None
        self._pending_stop_price = None
        self._pending_target_price = None

    def _clear_stale_pending_intraday_entry_if_orphaned(self) -> None:
        """
        Clear stale pending intraday entry locks.

        Prevents long-lived E_INTRADAY_PENDING_ENTRY lock after missed/implicit
        broker cancel events while preserving normal in-flight order behavior.
        """
        if not self._pending_intraday_entries and not self._pending_intraday_entry:
            return
        if self.algorithm is None or not hasattr(self.algorithm, "Time"):
            return
        now = self.algorithm.Time

        if self._pending_intraday_entry_since is None:
            self._pending_intraday_entry_since = now
            return

        stale_minutes = int(getattr(config, "INTRADAY_PENDING_ENTRY_STALE_MINUTES", 5))
        if stale_minutes <= 0:
            return

        fast_clear_seconds = int(getattr(config, "INTRADAY_PENDING_ENTRY_FAST_CLEAR_SECONDS", 90))
        cancel_after_minutes = int(getattr(config, "INTRADAY_PENDING_ENTRY_CANCEL_MINUTES", 20))
        cancel_after_seconds = max(0, cancel_after_minutes * 60)
        hard_clear_minutes = int(getattr(config, "INTRADAY_PENDING_ENTRY_HARD_CLEAR_MINUTES", 60))

        # Normalize legacy single-pending fields into lane-keyed payloads.
        if not self._pending_intraday_entries and self._pending_intraday_entry:
            legacy_symbol = (
                self._symbol_key(self._pending_contract.symbol)
                if self._pending_contract is not None
                else ""
            )
            legacy_lane = str(self._pending_intraday_entry_engine or "MICRO").upper()
            if legacy_symbol:
                legacy_key = self._pending_intraday_entry_key(
                    symbol=legacy_symbol, lane=legacy_lane
                )
                self._pending_intraday_entries[legacy_key] = {
                    "symbol": legacy_symbol,
                    "lane": legacy_lane,
                    "entry_score": self._pending_entry_score,
                    "num_contracts": self._pending_num_contracts,
                    "entry_strategy": self._pending_entry_strategy,
                    "stop_pct": self._pending_stop_pct,
                    "created_at": self._pending_intraday_entry_since.strftime("%Y-%m-%d %H:%M:%S"),
                }

        open_entry_order_ids_by_symbol = {}
        scan_errors = 0
        open_orders = []
        try:
            open_orders = list(self.algorithm.Transactions.GetOpenOrders())
        except Exception:
            open_orders = []

        for open_order in open_orders:
            try:
                open_symbol = getattr(open_order, "Symbol", None)
                if open_symbol is None:
                    continue
                if getattr(open_symbol, "SecurityType", None) != SecurityType.Option:
                    continue
                # Ignore obvious close-path tags so they don't hold entry locks open.
                order_tag = str(getattr(open_order, "Tag", "") or "").upper()
                if (
                    "OCO_" in order_tag
                    or "FORCE_CLOSE" in order_tag
                    or "INTRADAY_TIME_EXIT" in order_tag
                    or "SPREAD_CLOSE" in order_tag
                    or "RECON_ORPHAN" in order_tag
                ):
                    continue
                order_qty = float(getattr(open_order, "Quantity", 0) or 0)
                if order_qty <= 0:
                    # Entry-pending logic should ignore OCO stop/profit exits (negative qty).
                    continue
                symbol_key = self._symbol_key(open_symbol)
                if not symbol_key:
                    continue
                oid = getattr(open_order, "Id", None)
                if oid is None:
                    oid = getattr(open_order, "OrderId", None)
                if oid is None:
                    continue
                open_entry_order_ids_by_symbol.setdefault(symbol_key, []).append(int(oid))
            except Exception:
                scan_errors += 1
                continue

        def _parse_created_at(payload: Optional[Dict[str, Any]]) -> datetime:
            if isinstance(payload, dict):
                created_raw = payload.get("created_at")
                if isinstance(created_raw, datetime):
                    return created_raw
                if isinstance(created_raw, str) and created_raw.strip():
                    text = created_raw.strip()
                    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                        try:
                            return datetime.strptime(text[:19], fmt)
                        except Exception:
                            continue
            return self._pending_intraday_entry_since or now

        cleared_keys = []
        cancel_requests = 0
        max_age_minutes = 0.0
        for key, payload in list(self._pending_intraday_entries.items()):
            symbol_norm = (
                self._symbol_key(payload.get("symbol", "")) if isinstance(payload, dict) else ""
            )
            if not symbol_norm:
                symbol_norm = self._pending_intraday_symbol_from_key(key)
            if not symbol_norm:
                continue

            lane = str((payload or {}).get("lane", "")).upper() if isinstance(payload, dict) else ""
            if lane not in ("MICRO", "ITM") and "|" in str(key):
                lane = str(key).split("|", 1)[0].upper()
            lane_has_position = bool(self._intraday_positions.get(lane) or [])
            created_at = _parse_created_at(payload if isinstance(payload, dict) else None)
            age_seconds = max(0.0, (now - created_at).total_seconds())
            age_minutes = age_seconds / 60.0
            max_age_minutes = max(max_age_minutes, age_minutes)

            open_entry_order_ids = open_entry_order_ids_by_symbol.get(symbol_norm, [])
            if open_entry_order_ids:
                # Active entry order still live. Optionally cancel if it overstays.
                if (
                    (not lane_has_position)
                    and cancel_after_seconds > 0
                    and age_seconds >= cancel_after_seconds
                ):
                    for oid in open_entry_order_ids:
                        try:
                            self.algorithm.Transactions.CancelOrder(
                                oid,
                                f"INTRADAY_PENDING_TIMEOUT {age_minutes:.1f}m",
                            )
                            self.log(
                                f"INTRADAY_PENDING_TIMEOUT_CANCEL: Lane={lane or 'UNKNOWN'} | "
                                f"Symbol={symbol_norm} | OrderId={oid} | AgeMin={age_minutes:.1f}",
                                trades_only=True,
                            )
                            cancel_requests += 1
                        except Exception:
                            continue
                # Safety release: never allow a lane lock to persist for hours.
                if hard_clear_minutes > 0 and age_minutes >= hard_clear_minutes:
                    self._pending_intraday_entries.pop(key, None)
                    cleared_keys.append(key)
                    self.log(
                        f"INTRADAY_PENDING_HARD_CLEAR: Lane={lane or 'UNKNOWN'} | "
                        f"Symbol={symbol_norm} | AgeMin={age_minutes:.1f} | "
                        f"OpenOrders={len(open_entry_order_ids)}",
                        trades_only=True,
                    )
                continue

            # If lane already has a live position, pending-entry lock is stale.
            if lane_has_position:
                self._pending_intraday_entries.pop(key, None)
                cleared_keys.append(key)
                continue

            # Orphan pending (no open entry order + no position): clear on fast/stale thresholds.
            should_clear = age_minutes >= stale_minutes
            if not should_clear and fast_clear_seconds > 0:
                should_clear = age_seconds >= fast_clear_seconds
            if not should_clear:
                continue
            self._pending_intraday_entries.pop(key, None)
            cleared_keys.append(key)

        if not cleared_keys and cancel_requests <= 0:
            if scan_errors > 0:
                self.log(
                    f"OPT_MICRO_RECOVERY: Pending scan errors | Count={scan_errors}",
                    trades_only=True,
                )
            return

        self._pending_intraday_entry = bool(self._pending_intraday_entries)
        self._pending_intraday_entry_since = (
            None if not self._pending_intraday_entries else self._pending_intraday_entry_since
        )
        self._pending_intraday_entry_engine = (
            None if not self._pending_intraday_entries else self._pending_intraday_entry_engine
        )
        if not self._pending_intraday_entries:
            self._pending_contract = None
            self._pending_entry_score = None
            self._pending_num_contracts = None
            self._pending_stop_pct = None
            self._pending_stop_price = None
            self._pending_target_price = None
            self._pending_entry_strategy = None
        self.log(
            f"OPT_MICRO_RECOVERY: Pending entry maintenance | Cleared={len(cleared_keys)} | "
            f"CancelReq={cancel_requests} | MaxAgeMin={max_age_minutes:.1f} | ScanErr={scan_errors}",
            trades_only=True,
        )

    def cancel_pending_intraday_entry(
        self, engine: Optional[str] = None, symbol: Optional[str] = None
    ) -> Optional[str]:
        """
        V2.20: Clear pending intraday entry state after broker rejection.

        When symbol is provided, clears only that pending symbol (optionally scoped by lane).
        When engine is provided (MICRO/ITM), only clears matching lane lock.

        Returns:
            Cleared lane name when identifiable, else None.
        """
        cleared_lane: Optional[str] = None
        if symbol is not None:
            key = self._find_pending_intraday_entry_key(symbol=symbol, lane=engine)
            if key is not None:
                payload = self._pending_intraday_entries.pop(key, None)
                if isinstance(payload, dict):
                    lane = str(payload.get("lane", "")).upper()
                    cleared_lane = lane or None
        elif engine is None:
            if self._pending_intraday_entries:
                lanes = {
                    str(v.get("lane", "")).upper()
                    for v in self._pending_intraday_entries.values()
                    if isinstance(v, dict) and v.get("lane")
                }
                if len(lanes) == 1:
                    cleared_lane = next(iter(lanes))
            self._pending_intraday_entries = {}
        else:
            eng = str(engine).upper()
            before = len(self._pending_intraday_entries)
            self._pending_intraday_entries = {
                k: v
                for k, v in self._pending_intraday_entries.items()
                if str(v.get("lane", "")).upper() != eng
            }
            if len(self._pending_intraday_entries) < before:
                cleared_lane = eng

        self._pending_intraday_entry = bool(self._pending_intraday_entries)
        self._pending_intraday_entry_since = (
            None if not self._pending_intraday_entries else self._pending_intraday_entry_since
        )
        self._pending_intraday_entry_engine = (
            None if not self._pending_intraday_entries else self._pending_intraday_entry_engine
        )
        if not self._pending_intraday_entries:
            self._pending_contract = None
            self._pending_entry_score = None
            self._pending_num_contracts = None
            self._pending_stop_pct = None
            self._pending_stop_price = None
            self._pending_target_price = None
            self._pending_entry_strategy = None
        self.log(
            "OPT_MICRO_RECOVERY: Pending intraday entry cancelled | Retry allowed",
            trades_only=True,
        )
        return cleared_lane

    def has_pending_intraday_entry(self, engine: Optional[str] = None) -> bool:
        """True when an intraday entry is currently pending."""
        if self._pending_intraday_entry or self._pending_intraday_entries:
            self._clear_stale_pending_intraday_entry_if_orphaned()
        if engine is None:
            return bool(self._pending_intraday_entries) or self._pending_intraday_entry
        eng = str(engine).upper()
        for payload in self._pending_intraday_entries.values():
            if str(payload.get("lane", "")).upper() == eng:
                return True
        return (
            self._pending_intraday_entry
            and (self._pending_intraday_entry_engine or "").upper() == eng
        )

    def get_pending_intraday_entry_lane(self, symbol: Optional[str] = None) -> Optional[str]:
        """Best-effort lane lookup for a pending intraday entry."""
        if symbol is not None:
            key = self._find_pending_intraday_entry_key(symbol=symbol)
            if key is None:
                return None
            payload = self._pending_intraday_entries.get(key) or {}
            lane = str(payload.get("lane", "")).upper()
            if lane:
                return lane
            return None
        if self._pending_intraday_entries:
            try:
                payload = next(iter(self._pending_intraday_entries.values()))
                lane = str(payload.get("lane", "")).upper() if isinstance(payload, dict) else ""
                if lane:
                    return lane
            except Exception:
                pass
        if self._pending_intraday_entry_engine:
            lane = str(self._pending_intraday_entry_engine).upper()
            return lane or None
        return None

    def get_pending_entry_contract_symbol(self) -> str:
        """Best-effort symbol for current pending single-leg entry contract."""
        if self._pending_intraday_entries:
            try:
                payload = next(iter(self._pending_intraday_entries.values()))
                if isinstance(payload, dict):
                    sym = self._symbol_str(payload.get("symbol", ""))
                    if sym:
                        return sym
                key = next(iter(self._pending_intraday_entries.keys()))
                return self._pending_intraday_symbol_from_key(key)
            except Exception:
                return ""
        if self._pending_contract is None:
            return ""
        try:
            return self._symbol_str(self._pending_contract.symbol)
        except Exception:
            return ""

    def get_intraday_partial_fill_oco_seed(
        self, symbol: str, fill_price: float
    ) -> Optional[Dict[str, Any]]:
        """Return OCO seed for intraday/pending partial entry fill, if applicable."""
        symbol_norm = self._symbol_str(symbol)
        if not symbol_norm:
            return None

        lane = self._find_intraday_lane_by_symbol(symbol_norm)
        pos = self._get_intraday_lane_position(lane) if lane else None
        if (
            pos is not None
            and pos.contract is not None
            and float(getattr(pos, "stop_price", 0.0) or 0.0) > 0
            and float(getattr(pos, "target_price", 0.0) or 0.0) > 0
        ):
            return {
                "entry_price": float(getattr(pos, "entry_price", 0.0) or 0.0),
                "stop_price": float(getattr(pos, "stop_price", 0.0) or 0.0),
                "target_price": float(getattr(pos, "target_price", 0.0) or 0.0),
                "entry_strategy": str(getattr(pos, "entry_strategy", "UNKNOWN") or "UNKNOWN"),
            }

        return self.get_pending_intraday_partial_oco_seed(symbol=symbol_norm, fill_price=fill_price)

    def get_partial_fill_oco_seed(
        self, symbol: str, fill_price: float, order_tag: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Return OCO seed for partial fills across intraday and swing single-legs."""
        symbol_norm = self._symbol_str(symbol)
        if not symbol_norm:
            return None

        intraday_seed = self.get_intraday_partial_fill_oco_seed(
            symbol=symbol_norm,
            fill_price=fill_price,
        )
        if intraday_seed is not None:
            return intraday_seed

        if (
            self._pending_contract is None
            or self._pending_intraday_entry
            or self._symbol_str(self._pending_contract.symbol) != symbol_norm
        ):
            inferred_strategy = self._infer_intraday_strategy_from_order_tag(order_tag)
            if inferred_strategy == IntradayStrategy.NO_TRADE.value:
                return None
            entry_px = float(fill_price or 0.0)
            if entry_px <= 0:
                return None
            target_pct, stop_pct = self._get_intraday_exit_profile(inferred_strategy)
            if stop_pct is None or float(stop_pct) <= 0 or float(target_pct) <= 0:
                return None
            self.log(
                f"OCO_PARTIAL_FALLBACK: Strategy={inferred_strategy} | Symbol={symbol_norm} | "
                f"Entry=${entry_px:.2f}",
                trades_only=True,
            )
            return {
                "entry_price": entry_px,
                "stop_price": entry_px * (1 - float(stop_pct)),
                "target_price": entry_px * (1 + float(target_pct)),
                "entry_strategy": inferred_strategy,
            }

        stop_price = float(getattr(self, "_pending_stop_price", 0.0) or 0.0)
        target_price = float(getattr(self, "_pending_target_price", 0.0) or 0.0)
        if stop_price <= 0 or target_price <= 0:
            return None

        entry_px = float(fill_price or 0.0)
        if entry_px <= 0:
            entry_px = float(getattr(self._pending_contract, "mid_price", 0.0) or 0.0)
        if entry_px <= 0:
            return None

        return {
            "entry_price": entry_px,
            "stop_price": stop_price,
            "target_price": target_price,
            "entry_strategy": str(self._pending_entry_strategy or "SWING_SINGLE"),
        }

    def get_pending_intraday_partial_oco_seed(
        self, symbol: str, fill_price: float
    ) -> Optional[Dict[str, Any]]:
        """
        Build temporary OCO pricing for a pending intraday entry partial fill.

        Used when an entry order partially fills before full fill registration.
        """
        if fill_price is None or float(fill_price) <= 0:
            return None

        symbol_norm = self._symbol_str(symbol)
        if not symbol_norm:
            return None

        payload = self._get_pending_intraday_entry_payload(symbol=symbol_norm)
        if payload is not None:
            entry_strategy = str(payload.get("entry_strategy") or "SWING_SINGLE")
            stop_pct = float(
                payload.get("stop_pct") if payload.get("stop_pct") is not None else 0.20
            )
            current_dte = 0
        else:
            if not self._pending_intraday_entry or self._pending_contract is None:
                return None
            pending_symbol = self._symbol_str(self._pending_contract.symbol)
            if symbol_norm != pending_symbol:
                return None
            entry_strategy = self._pending_entry_strategy or "SWING_SINGLE"
            stop_pct = self._pending_stop_pct if self._pending_stop_pct is not None else 0.20
            # current_dte set from pending payload fallback above.
        target_pct, strategy_floor = self._get_intraday_exit_profile(entry_strategy)
        if payload is not None:
            current_dte = 0
        else:
            current_dte = int(getattr(self._pending_contract, "days_to_expiry", 0))
        target_pct = self._apply_intraday_target_overrides(
            entry_strategy=entry_strategy,
            target_pct=float(target_pct),
            current_dte=current_dte,
        )
        stop_pct = self._apply_intraday_stop_overrides(
            entry_strategy=entry_strategy,
            stop_pct=float(stop_pct),
            current_dte=current_dte,
        )
        if strategy_floor is not None and strategy_floor > 0:
            stop_pct = max(float(stop_pct), float(strategy_floor))

        entry_px = float(fill_price)
        return {
            "entry_price": entry_px,
            "stop_price": entry_px * (1 - float(stop_pct)),
            "target_price": entry_px * (1 + float(target_pct)),
            "entry_strategy": entry_strategy,
        }

    def _normalize_symbol_key(self, symbol: Optional[str]) -> Optional[str]:
        sym = self._symbol_str(symbol) if symbol else ""
        return sym or None

    def _sync_pending_intraday_exit_flags(self) -> None:
        active = bool(self._pending_intraday_exit_lanes) or bool(
            self._pending_intraday_exit_symbols
        )
        self._pending_intraday_exit = active
        if not active:
            self._pending_intraday_exit_engine = None

    def has_pending_swing_entry(self) -> bool:
        """True when a single-leg swing entry is pending (not intraday)."""
        return self._pending_contract is not None and not self._pending_intraday_entry

    def has_pending_intraday_exit(
        self, engine: Optional[str] = None, symbol: Optional[str] = None
    ) -> bool:
        """True when an intraday close signal has already been emitted and is in-flight."""
        symbol_key = self._normalize_symbol_key(symbol)
        if symbol_key is not None:
            return symbol_key in self._pending_intraday_exit_symbols

        if engine is None:
            return (
                bool(self._pending_intraday_exit_symbols)
                or bool(self._pending_intraday_exit_lanes)
                or self._pending_intraday_exit
            )
        eng = str(engine).upper()
        return eng in self._pending_intraday_exit_lanes or (
            self._pending_intraday_exit
            and (self._pending_intraday_exit_engine or "").upper() == eng
        )

    def mark_pending_intraday_exit(self, symbol: Optional[str] = None) -> bool:
        """
        Mark intraday close as pending to block duplicate software/force exits.

        Args:
            symbol: Optional symbol guard. Symbol-scoped locks are preferred for
                multi-position lanes.

        Returns:
            True when lock was set, else False.
        """
        symbol_key = self._normalize_symbol_key(symbol)
        if symbol_key is not None:
            if self._find_intraday_lane_by_symbol(symbol_key) is None:
                return False
            if symbol_key in self._pending_intraday_exit_symbols:
                return False
            self._pending_intraday_exit_symbols.add(symbol_key)
            self._sync_pending_intraday_exit_flags()
            return True

        target_lane = None
        if self._pending_intraday_exit_engine:
            target_lane = str(self._pending_intraday_exit_engine).upper()
        else:
            target_lane = self.get_intraday_position_engine()
            if target_lane is None:
                return False

        lane_key = str(target_lane).upper()
        if lane_key in self._pending_intraday_exit_lanes:
            return False
        self._pending_intraday_exit_engine = target_lane
        self._pending_intraday_exit_lanes.add(lane_key)
        self._sync_pending_intraday_exit_flags()
        return True

    def cancel_pending_intraday_exit(self, symbol: Optional[str] = None) -> bool:
        """
        Clear pending intraday exit lock after a rejected/canceled close order.

        Args:
            symbol: Optional symbol guard. When provided, clears symbol-scoped lock.

        Returns:
            True when lock was cleared, else False.
        """
        symbol_key = self._normalize_symbol_key(symbol)
        if symbol_key is not None:
            if symbol_key not in self._pending_intraday_exit_symbols:
                return False
            self._pending_intraday_exit_symbols.discard(symbol_key)
            self._sync_pending_intraday_exit_flags()
            self.log(
                f"OPT_MICRO_RECOVERY: Pending intraday exit lock cleared | Symbol={symbol_key}",
                trades_only=True,
            )
            return True

        if (
            not self._pending_intraday_exit_lanes
            and not self._pending_intraday_exit_symbols
            and not self._pending_intraday_exit
        ):
            return False

        self._pending_intraday_exit_lanes.clear()
        self._pending_intraday_exit_symbols.clear()
        self._sync_pending_intraday_exit_flags()
        self.log("OPT_MICRO_RECOVERY: Pending intraday exit lock cleared", trades_only=True)
        return True

    def remove_spread_position(self, symbol: Optional[str] = None) -> Optional[SpreadPosition]:
        """
        V2.3: Remove the current spread position after exit.
        V2.6 Bug #16: Records exit time for post-trade margin cooldown.

        Returns:
            Removed spread position, or None if no spread existed.
        """
        spreads = self.get_spread_positions()
        if spreads:
            spread = None
            if symbol:
                sym = str(symbol)
                for s in spreads:
                    if str(s.long_leg.symbol) == sym or str(s.short_leg.symbol) == sym:
                        spread = s
                        break
            if spread is None:
                if symbol:
                    self.log(
                        f"SPREAD: WARN remove no match for {symbol}, "
                        f"skip removal across {len(spreads)} active spreads",
                        trades_only=True,
                    )
                    return None
                if len(spreads) == 1:
                    spread = spreads[0]
                else:
                    self.log(
                        "SPREAD: WARN remove requested without symbol while multiple spreads active | "
                        f"Count={len(spreads)} | skip removal",
                        trades_only=True,
                    )
                    return None

            if self._spread_positions:
                self._spread_positions = [s for s in self._spread_positions if s is not spread]
            elif self._spread_position is spread:
                self._spread_position = None
            spread_key = self._build_spread_key(spread)
            self._spread_neutrality_warn_by_key.pop(spread_key, None)
            self._spread_exit_signal_cooldown.pop(spread_key, None)  # V9.4 P0: Clear cooldown
            self._spread_hold_guard_logged.discard(spread_key)

            self._spread_position = self._spread_positions[0] if self._spread_positions else None

            # V6.5 FIX: Reset gamma pin flag when position is closed
            if not self._spread_positions:
                self._gamma_pin_exit_triggered = False

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
        return self.get_open_spread_count() > 0

    def get_spread_position(self) -> Optional[SpreadPosition]:
        """Get primary spread position (legacy compatibility)."""
        spreads = self.get_spread_positions()
        return spreads[0] if spreads else None

    # =========================================================================
    # V2.27 WIN RATE GATE
    # =========================================================================

    def record_spread_result(self, is_win: bool) -> None:
        """
        V2.27: Record a spread trade result for win rate tracking.

        Args:
            is_win: True if the spread was profitable, False if a loss.
        """
        if (
            self._vass_entry_engine_enabled
            and self.algorithm is not None
            and hasattr(self.algorithm, "Time")
        ):
            pause_until = self._vass_entry_engine.record_spread_result(
                is_win=is_win,
                now_dt=self.algorithm.Time,
            )
            if pause_until:
                self.log(
                    "VASS_LOSS_BREAKER_PAUSE | " f"PauseUntil={pause_until}",
                    trades_only=True,
                )

        if not config.WIN_RATE_GATE_ENABLED:
            return

        if self._win_rate_shutoff:
            if (
                self._win_rate_shutoff_date is None
                and self.algorithm is not None
                and hasattr(self.algorithm, "Time")
            ):
                self._win_rate_shutoff_date = str(self.algorithm.Time.date())

            # Time-based auto-recovery: avoid prolonged degraded sizing lock.
            max_days = int(getattr(config, "WIN_RATE_GATE_MAX_SHUTOFF_DAYS", 30))
            if (
                max_days > 0
                and self._win_rate_shutoff_date
                and self.algorithm is not None
                and hasattr(self.algorithm, "Time")
            ):
                try:
                    shutoff_dt = datetime.strptime(self._win_rate_shutoff_date, "%Y-%m-%d").date()
                    days_elapsed = (self.algorithm.Time.date() - shutoff_dt).days
                    if days_elapsed >= max_days:
                        self._win_rate_shutoff = False
                        self._win_rate_shutoff_date = None
                        self._paper_track_history = []
                        self._spread_result_history = []
                        self.log(
                            f"WIN_RATE_GATE: AUTO_RESET | {days_elapsed} days in shutoff",
                            trades_only=True,
                        )
                        return
                except Exception:
                    pass

            # During shutoff, record to paper history instead
            self._paper_track_history.append(is_win)
            if len(self._paper_track_history) > config.WIN_RATE_LOOKBACK:
                self._paper_track_history = self._paper_track_history[-config.WIN_RATE_LOOKBACK :]

            # Check if paper win rate recovers enough to resume
            if len(self._paper_track_history) >= config.WIN_RATE_LOOKBACK:
                paper_wr = sum(self._paper_track_history) / len(self._paper_track_history)
                if paper_wr >= config.WIN_RATE_RESTART_THRESHOLD:
                    self._win_rate_shutoff = False
                    self._win_rate_shutoff_date = None
                    self._paper_track_history = []
                    self.log(
                        f"WIN_RATE_GATE: RESUMED | PaperWR={paper_wr:.0%} >= "
                        f"{config.WIN_RATE_RESTART_THRESHOLD:.0%} | Real trading restored",
                        trades_only=True,
                    )
        else:
            # Normal mode: record to real history
            self._spread_result_history.append(is_win)
            if len(self._spread_result_history) > config.WIN_RATE_LOOKBACK:
                self._spread_result_history = self._spread_result_history[
                    -config.WIN_RATE_LOOKBACK :
                ]

            # Check if we should enter shutoff
            if len(self._spread_result_history) >= config.WIN_RATE_LOOKBACK:
                wr = sum(self._spread_result_history) / len(self._spread_result_history)
                if wr < config.WIN_RATE_SHUTOFF_THRESHOLD:
                    self._win_rate_shutoff = True
                    if self.algorithm is not None and hasattr(self.algorithm, "Time"):
                        self._win_rate_shutoff_date = str(self.algorithm.Time.date())
                    else:
                        self._win_rate_shutoff_date = None
                    self.log(
                        f"WIN_RATE_GATE: SHUTOFF | WinRate={wr:.0%} < "
                        f"{config.WIN_RATE_SHUTOFF_THRESHOLD:.0%} | "
                        f"LastN={self._spread_result_history} | Paper tracking active",
                        trades_only=True,
                    )

        result_str = "WIN" if is_win else "LOSS"
        self.log(
            f"WIN_RATE_GATE: Recorded {result_str} | "
            f"History={len(self._spread_result_history)} | "
            f"Shutoff={self._win_rate_shutoff}",
            trades_only=True,
        )

    def get_win_rate_scale(self) -> float:
        """
        V2.27: Get position sizing scale based on rolling win rate.

        Returns:
            1.0 = full size, 0.75 = reduced, 0.50 = minimum, 0.0 = shutoff.
        """
        if not config.WIN_RATE_GATE_ENABLED:
            return 1.0

        if self._win_rate_shutoff:
            return 0.0

        if len(self._spread_result_history) < config.WIN_RATE_LOOKBACK:
            return 1.0  # Not enough data, full size

        win_rate = (
            sum(self._spread_result_history[-config.WIN_RATE_LOOKBACK :]) / config.WIN_RATE_LOOKBACK
        )

        if win_rate >= config.WIN_RATE_FULL_THRESHOLD:
            return 1.0
        elif win_rate >= config.WIN_RATE_REDUCED_THRESHOLD:
            return config.WIN_RATE_SIZING_REDUCED  # 0.75
        elif win_rate >= config.WIN_RATE_MINIMUM_THRESHOLD:
            return config.WIN_RATE_SIZING_MINIMUM  # 0.50
        else:
            return 0.0  # Should trigger shutoff via record_spread_result

    def clear_spread_position(self) -> None:
        """
        V2.12 Fix #5: Force clear spread position tracking.

        Used by margin circuit breaker to reset tracking after forced liquidation.
        Does NOT place orders - just clears internal state.
        """
        spreads = self.get_spread_positions()
        if spreads:
            self.log(
                f"SPREAD: FORCE_CLEARED | Count={len(spreads)} | Margin CB liquidation",
                trades_only=True,
            )
            self._spread_neutrality_warn_by_key = {}
            self._spread_hold_guard_logged.clear()
            self._spread_positions = []
            self._spread_position = None
            self._last_spread_exit_time = None
            max_attempts = int(getattr(config, "SPREAD_MAX_ATTEMPTS_PER_KEY_PER_DAY", 3))
            # Preserve prior behavior: block any same-day spread re-entry after CB liquidation.
            self._spread_attempts_today_by_key = {
                "DEBIT_CALL": max_attempts,
                "DEBIT_PUT": max_attempts,
                f"CREDIT_{SpreadStrategy.BULL_PUT_CREDIT.value}": max_attempts,
                f"CREDIT_{SpreadStrategy.BEAR_CALL_CREDIT.value}": max_attempts,
            }

    def reset_spread_closing_lock(self) -> None:
        """
        V2.17: Clear the is_closing lock if all close attempts failed.

        Called by PortfolioRouter when both combo order retries and
        sequential fallback fail. This allows the spread to be retried
        on subsequent iterations instead of staying permanently locked.

        Does NOT clear the spread position - just resets the lock flag.
        """
        reset_count = 0
        for spread in self.get_spread_positions():
            if spread.is_closing:
                spread.is_closing = False
                reset_count += 1
        if reset_count > 0:
            self.log(
                f"SPREAD: LOCK_RESET | Count={reset_count} | Will retry on next check",
                trades_only=True,
            )

    def has_intraday_position(self, engine: Optional[str] = None) -> bool:
        """V2.3.2: Check if an intraday position exists (optionally by engine lane)."""
        if engine is None:
            return any(len(v or []) > 0 for v in self._intraday_positions.values())
        eng = str(engine).upper()
        return len(self._intraday_positions.get(eng) or []) > 0

    def get_intraday_position(self, engine: Optional[str] = None) -> Optional[OptionsPosition]:
        """V2.3.2: Get current intraday position (optionally by engine lane)."""
        if engine is not None:
            return self._get_intraday_lane_position(str(engine).upper())
        # Deterministic default for legacy callers.
        return self._get_intraday_lane_position("ITM") or self._get_intraday_lane_position("MICRO")

    def get_intraday_position_engine(self) -> Optional[str]:
        """Return default ownership lane for legacy callers."""
        if len(self._intraday_positions.get("ITM") or []) > 0:
            return "ITM"
        if len(self._intraday_positions.get("MICRO") or []) > 0:
            return "MICRO"
        return None

    def has_position(self) -> bool:
        """Check if any position exists (single-leg, spread, or intraday)."""
        return (
            self._position is not None or self.has_spread_position() or self.has_intraday_position()
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

        if self.has_spread_position():
            self._spread_positions = []
            self._spread_position = None
            self._spread_neutrality_warn_by_key = {}
            self._spread_hold_guard_logged.clear()
            cleared.append("spread")

        if self._intraday_position is not None or any(
            len(v or []) > 0 for v in self._intraday_positions.values()
        ):
            self._intraday_position = None
            self._intraday_position_engine = None
            self._intraday_positions = {"MICRO": [], "ITM": []}
            cleared.append("intraday")

        # V2.16-BT: Also clear swing position (V2.1.1 dual-mode)
        if self._swing_position is not None:
            self._swing_position = None
            cleared.append("swing")

        # Clear ALL pending state — every _pending_* and _entry_* field from __init__
        # V2.30: Complete list (V2.29 missed _pending_stop_price, _pending_target_price,
        # _pending_intraday_exit). Stale pending state = zombie bugs.
        self._pending_contract = None
        self._pending_intraday_entry = False
        self._pending_intraday_entry_since = None
        self._pending_intraday_entry_engine = None
        self._pending_intraday_entries = {}
        self._pending_intraday_exit = False
        self._pending_intraday_exit_engine = None
        self._pending_intraday_exit_lanes = set()
        self._pending_intraday_exit_symbols = set()
        self._pending_spread_long_leg = None
        self._pending_spread_short_leg = None
        self._pending_spread_width = None
        self._pending_spread_entry_vix = None
        self._pending_spread_entry_since = None
        self._pending_spread_type = None
        self._pending_net_debit = None
        self._pending_max_profit = None
        self._pending_num_contracts = None
        self._pending_entry_score = None
        self._pending_stop_pct = None
        self._pending_stop_price = None
        self._pending_target_price = None
        self._pending_entry_strategy = None
        self._entry_attempted_today = False
        self._intraday_force_exit_hold_skip_log_date = {}
        self._last_intraday_close_time = None
        self._last_intraday_close_strategy = None

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
        primary_spread = self.get_spread_position()
        return {
            # Legacy position (backwards compatibility)
            "position": self._position.to_dict() if self._position else None,
            "trades_today": self._trades_today,
            "last_trade_date": self._last_trade_date,
            # V2.1.1 dual-mode state
            # Mirror canonical swing position to avoid dual-key drift.
            "swing_position": (self._position.to_dict() if self._position else None),
            "intraday_position": (
                self._intraday_position.to_dict() if self._intraday_position else None
            ),
            "intraday_positions": {
                k: [p.to_dict() for p in (v or []) if p is not None]
                for k, v in self._intraday_positions.items()
            },
            "intraday_position_engine": self._intraday_position_engine,
            "intraday_trades_today": self._intraday_trades_today,
            "intraday_call_trades_today": self._intraday_call_trades_today,
            "intraday_put_trades_today": self._intraday_put_trades_today,
            "intraday_itm_trades_today": self._intraday_itm_trades_today,
            "intraday_micro_trades_today": self._intraday_micro_trades_today,
            "swing_trades_today": self._swing_trades_today,
            "total_options_trades_today": self._total_options_trades_today,
            "current_mode": self._current_mode.value,
            "micro_regime_state": self._micro_regime_engine.get_state().to_dict(),
            "micro_regime_runtime_state": {
                "vix_history": [
                    {
                        "timestamp": snap.timestamp,
                        "value": float(snap.value),
                        "change_from_open_pct": float(snap.change_from_open_pct),
                    }
                    for snap in list(self._micro_regime_engine._vix_history)
                ],
                "vix_15min_ago": float(self._micro_regime_engine._vix_15min_ago),
                "vix_30min_ago": float(self._micro_regime_engine._vix_30min_ago),
                "qqq_open": float(self._micro_regime_engine._qqq_open),
            },
            # V2.16-BT: Persist spread position for multi-day backtests
            # Mirror primary from canonical spread list to avoid dual-key drift.
            "spread_position": (primary_spread.to_dict() if primary_spread else None),
            "spread_positions": [s.to_dict() for s in self.get_spread_positions()],
            # Market open data
            "vix_at_open": self._vix_at_open,
            "spy_at_open": self._spy_at_open,
            "spy_gap_pct": self._spy_gap_pct,
            # Runtime guard/cooldown state (restart-safe)
            "rejection_margin_cap": self._rejection_margin_cap,
            "spread_failure_cooldown_until": self._spread_failure_cooldown_until,
            "spread_failure_cooldown_until_by_dir": {
                str(k.value if hasattr(k, "value") else k).upper(): str(v)
                for k, v in self._spread_failure_cooldown_until_by_dir.items()
            },
            "spread_exit_signal_cooldown": {
                k: v.strftime("%Y-%m-%d %H:%M:%S")
                for k, v in self._spread_exit_signal_cooldown.items()
            },
            "gamma_pin_exit_triggered": bool(self._gamma_pin_exit_triggered),
            "last_spread_exit_time": self._last_spread_exit_time,
            # V2.27: Win Rate Gate state
            "spread_result_history": self._spread_result_history,
            "win_rate_shutoff": self._win_rate_shutoff,
            "win_rate_shutoff_date": self._win_rate_shutoff_date,
            "paper_track_history": self._paper_track_history,
            "vass_entry_state": self._vass_entry_engine.to_dict(),
            "spread_neutrality_warn_by_key": {
                k: v.strftime("%Y-%m-%d %H:%M:%S")
                for k, v in self._spread_neutrality_warn_by_key.items()
            },
            "last_intraday_close_time": (
                self._last_intraday_close_time.strftime("%Y-%m-%d %H:%M:%S")
                if self._last_intraday_close_time is not None
                else None
            ),
            "last_intraday_close_strategy": self._last_intraday_close_strategy,
            "pending_intraday_entry_engine": self._pending_intraday_entry_engine,
            "pending_intraday_exit_engine": self._pending_intraday_exit_engine,
            "pending_intraday_entries": {
                k: {
                    "symbol": v.get("symbol"),
                    "lane": v.get("lane"),
                    "entry_score": v.get("entry_score"),
                    "num_contracts": v.get("num_contracts"),
                    "entry_strategy": v.get("entry_strategy"),
                    "stop_pct": v.get("stop_pct"),
                    "created_at": v.get("created_at"),
                }
                for k, v in self._pending_intraday_entries.items()
            },
            "pending_intraday_exit_lanes": list(self._pending_intraday_exit_lanes),
            "pending_intraday_exit_symbols": list(self._pending_intraday_exit_symbols),
            "call_consecutive_losses": self._call_consecutive_losses,
            "call_cooldown_until_date": (
                self._call_cooldown_until_date.isoformat()
                if self._call_cooldown_until_date is not None
                else None
            ),
            "put_consecutive_losses": self._put_consecutive_losses,
            "put_cooldown_until_date": (
                self._put_cooldown_until_date.isoformat()
                if self._put_cooldown_until_date is not None
                else None
            ),
            "itm_horizon_state": self._itm_horizon_engine.to_dict(),
        }

    def restore_state(self, state: Dict[str, Any]) -> None:
        """
        Restore state from ObjectStore.

        Default behavior clears stale intraday positions on restore.
        V10.1 allows restoring ITM_MOMENTUM intraday positions when hold policy
        explicitly permits overnight carry.
        """
        # V2.16-BT: Get current date for expiry validation (defensive for tests)
        algorithm = getattr(self, "algorithm", None) or getattr(self, "_algorithm", None)
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

        # Canonicalize legacy dual swing keys to prevent slot/state drift.
        if self._position is None and self._swing_position is not None:
            self._position = self._swing_position
        elif self._position is not None and self._swing_position is None:
            self._swing_position = self._position

        intraday_data = state.get("intraday_position")
        if intraday_data:
            position = OptionsPosition.from_dict(intraday_data)
            contract_expiry = position.contract.expiry if position.contract else None
            if contract_expiry and contract_expiry < current_date:
                self.log(
                    f"OPT: ZOMBIE_CLEAR - Intraday position expired {contract_expiry} < {current_date}. "
                    "Clearing stale position."
                )
                self._intraday_position = None
                self._intraday_position_engine = None
            elif self.should_hold_intraday_overnight(position):
                self._intraday_position = position
                self._intraday_position_engine = self._intraday_engine_lane_from_strategy(
                    position.entry_strategy
                )
                live_dte = self._get_position_live_dte(position)
                self.log(
                    f"OPT: STATE_RESTORE - Hold-enabled intraday position restored | "
                    f"Strategy={position.entry_strategy} | DTE={live_dte}"
                )
            else:
                force_hh, force_mm = self._get_intraday_force_exit_hhmm()
                self.log(
                    "OPT: STATE_RESTORE - Clearing intraday position (non-hold strategy/policy) | "
                    f"Cutoff={force_hh:02d}:{force_mm:02d}"
                )
                self._intraday_position = None
                self._intraday_position_engine = None
        else:
            self._intraday_position = None
            self._intraday_position_engine = None

        def _should_restore_intraday_position(
            position: Optional[OptionsPosition], lane_hint: str
        ) -> bool:
            if position is None or position.contract is None:
                return False
            contract_expiry = position.contract.expiry if position.contract else None
            if contract_expiry and contract_expiry < current_date:
                self.log(
                    f"OPT: ZOMBIE_CLEAR - Intraday position expired {contract_expiry} < {current_date}. "
                    f"Clearing stale position | Lane={lane_hint}"
                )
                return False
            if self.should_hold_intraday_overnight(position):
                return True
            force_hh, force_mm = self._get_intraday_force_exit_hhmm()
            self.log(
                "OPT: STATE_RESTORE - Clearing intraday lane position (non-hold strategy/policy) | "
                f"Lane={lane_hint} | Cutoff={force_hh:02d}:{force_mm:02d}"
            )
            return False

        intraday_positions_data = state.get("intraday_positions") or {}
        if isinstance(intraday_positions_data, dict) and intraday_positions_data:
            self._intraday_positions = {"MICRO": [], "ITM": []}
            for lane in ("MICRO", "ITM"):
                row = intraday_positions_data.get(lane)
                if isinstance(row, list):
                    restored = []
                    for item in row:
                        if not item:
                            continue
                        try:
                            pos = OptionsPosition.from_dict(item)
                            if _should_restore_intraday_position(pos, lane):
                                restored.append(pos)
                        except Exception:
                            continue
                    self._intraday_positions[lane] = restored
                elif isinstance(row, dict):
                    # Backward compatibility: single-position payload.
                    try:
                        pos = OptionsPosition.from_dict(row)
                        if _should_restore_intraday_position(pos, lane):
                            self._intraday_positions[lane] = [pos]
                        else:
                            self._intraday_positions[lane] = []
                    except Exception:
                        self._intraday_positions[lane] = []
            self._refresh_legacy_intraday_mirrors()
        else:
            self._intraday_positions = {"MICRO": [], "ITM": []}
            if self._intraday_position is not None:
                lane = self._intraday_engine_lane_from_strategy(
                    getattr(self._intraday_position, "entry_strategy", "")
                )
                self._intraday_positions[lane] = [self._intraday_position]

        self._intraday_position_engine = (
            state.get("intraday_position_engine") or self._intraday_position_engine
        )
        if self._intraday_position is None:
            self._refresh_legacy_intraday_mirrors()

        self._pending_intraday_entry_engine = state.get("pending_intraday_entry_engine")
        self._pending_intraday_exit_engine = state.get("pending_intraday_exit_engine")
        self._pending_intraday_entries = {}
        for sym, row in (state.get("pending_intraday_entries") or {}).items():
            if isinstance(row, dict):
                lane = str(row.get("lane") or "").upper()
                symbol_norm = self._symbol_key(row.get("symbol") or sym)
                key = self._pending_intraday_entry_key(symbol=symbol_norm, lane=lane)
                self._pending_intraday_entries[key] = {
                    "symbol": symbol_norm,
                    "lane": lane,
                    "entry_score": row.get("entry_score"),
                    "num_contracts": row.get("num_contracts"),
                    "entry_strategy": row.get("entry_strategy"),
                    "stop_pct": row.get("stop_pct"),
                    "created_at": row.get("created_at"),
                }
        self._pending_intraday_exit_lanes = set(
            str(x).upper() for x in (state.get("pending_intraday_exit_lanes") or []) if x
        )
        self._pending_intraday_exit_symbols = set(
            self._symbol_str(x) for x in (state.get("pending_intraday_exit_symbols") or []) if x
        )
        self._pending_intraday_exit_symbols.discard("")
        self._sync_pending_intraday_exit_flags()
        self._intraday_trades_today = state.get("intraday_trades_today", 0)
        self._intraday_call_trades_today = state.get("intraday_call_trades_today", 0)
        self._intraday_put_trades_today = state.get("intraday_put_trades_today", 0)
        self._intraday_itm_trades_today = state.get("intraday_itm_trades_today", 0)
        self._intraday_micro_trades_today = state.get("intraday_micro_trades_today", 0)
        self._call_consecutive_losses = int(state.get("call_consecutive_losses", 0) or 0)
        self._put_consecutive_losses = int(state.get("put_consecutive_losses", 0) or 0)
        call_cooldown = state.get("call_cooldown_until_date")
        put_cooldown = state.get("put_cooldown_until_date")
        try:
            self._call_cooldown_until_date = (
                datetime.strptime(call_cooldown, "%Y-%m-%d").date() if call_cooldown else None
            )
        except Exception:
            self._call_cooldown_until_date = None
        try:
            self._put_cooldown_until_date = (
                datetime.strptime(put_cooldown, "%Y-%m-%d").date() if put_cooldown else None
            )
        except Exception:
            self._put_cooldown_until_date = None
        self._swing_trades_today = state.get("swing_trades_today", 0)
        self._total_options_trades_today = state.get(
            "total_options_trades_today", self._trades_today
        )

        # V2.16-BT: Restore spread position with expiry validation
        restored_spreads: List[SpreadPosition] = []
        spread_positions_data = state.get("spread_positions")
        if spread_positions_data:
            for row in spread_positions_data:
                try:
                    spread = SpreadPosition.from_dict(row)
                    if spread.long_leg.expiry and spread.long_leg.expiry < current_date:
                        self.log(
                            f"OPT: ZOMBIE_CLEAR - Spread position expired {spread.long_leg.expiry} < {current_date}. "
                            "Clearing stale spread."
                        )
                        continue
                    restored_spreads.append(spread)
                except Exception:
                    continue
        else:
            spread_data = state.get("spread_position")
            if spread_data:
                spread = SpreadPosition.from_dict(spread_data)
                if spread.long_leg.expiry and spread.long_leg.expiry < current_date:
                    self.log(
                        f"OPT: ZOMBIE_CLEAR - Spread position expired {spread.long_leg.expiry} < {current_date}. "
                        "Clearing stale spread."
                    )
                else:
                    restored_spreads.append(spread)

        self._spread_positions = restored_spreads
        self._spread_position = self._spread_positions[0] if self._spread_positions else None
        if self._spread_positions:
            self.log(
                f"OPT: STATE_RESTORE - Spread positions restored | "
                f"Count={len(self._spread_positions)} | "
                f"Primary={self._spread_positions[0].spread_type} x{self._spread_positions[0].num_spreads}",
            )

        mode_value = state.get("current_mode", "SWING")
        self._current_mode = OptionsMode(mode_value)

        micro_state_data = state.get("micro_regime_state")
        if micro_state_data:
            self._micro_regime_engine._state = MicroRegimeState.from_dict(micro_state_data)

        runtime_state = state.get("micro_regime_runtime_state", {}) or {}
        try:
            self._micro_regime_engine._vix_history.clear()
            for row in runtime_state.get("vix_history", []) or []:
                try:
                    self._micro_regime_engine._vix_history.append(
                        VIXSnapshot(
                            timestamp=str(row.get("timestamp", "")),
                            value=float(row.get("value", 0.0) or 0.0),
                            change_from_open_pct=float(row.get("change_from_open_pct", 0.0) or 0.0),
                        )
                    )
                except Exception:
                    continue
            self._micro_regime_engine._vix_15min_ago = float(
                runtime_state.get("vix_15min_ago", 0.0) or 0.0
            )
            self._micro_regime_engine._vix_30min_ago = float(
                runtime_state.get("vix_30min_ago", 0.0) or 0.0
            )
            self._micro_regime_engine._qqq_open = float(runtime_state.get("qqq_open", 0.0) or 0.0)
        except Exception:
            self._micro_regime_engine._vix_history.clear()
            self._micro_regime_engine._vix_15min_ago = 0.0
            self._micro_regime_engine._vix_30min_ago = 0.0
            self._micro_regime_engine._qqq_open = 0.0

        # Market open data
        self._vix_at_open = state.get("vix_at_open", 0.0)
        self._spy_at_open = state.get("spy_at_open", 0.0)
        self._spy_gap_pct = state.get("spy_gap_pct", 0.0)

        # Runtime guard/cooldown state (restart-safe)
        cap = state.get("rejection_margin_cap")
        try:
            self._rejection_margin_cap = float(cap) if cap is not None else None
        except Exception:
            self._rejection_margin_cap = None

        raw_cooldown = state.get("spread_failure_cooldown_until")
        self._spread_failure_cooldown_until = str(raw_cooldown) if raw_cooldown else None

        raw_by_dir = state.get("spread_failure_cooldown_until_by_dir", {}) or {}
        self._spread_failure_cooldown_until_by_dir = {}
        if isinstance(raw_by_dir, dict):
            for k, v in raw_by_dir.items():
                key = str(k).upper()
                if key in {"BULLISH", "BEARISH", "CALL", "PUT"} and v:
                    self._spread_failure_cooldown_until_by_dir[key] = str(v)

        self._spread_exit_signal_cooldown = {}
        for k, v in (state.get("spread_exit_signal_cooldown", {}) or {}).items():
            try:
                self._spread_exit_signal_cooldown[str(k)] = datetime.strptime(
                    str(v)[:19], "%Y-%m-%d %H:%M:%S"
                )
            except Exception:
                continue

        self._gamma_pin_exit_triggered = bool(state.get("gamma_pin_exit_triggered", False))
        raw_last_exit = state.get("last_spread_exit_time")
        self._last_spread_exit_time = str(raw_last_exit) if raw_last_exit else None

        # V2.27: Win Rate Gate state
        self._spread_result_history = state.get("spread_result_history", [])
        self._win_rate_shutoff = state.get("win_rate_shutoff", False)
        raw_shutoff_date = state.get("win_rate_shutoff_date")
        self._win_rate_shutoff_date = str(raw_shutoff_date)[:10] if raw_shutoff_date else None
        self._paper_track_history = state.get("paper_track_history", [])
        vass_state = state.get("vass_entry_state")
        if not isinstance(vass_state, dict):
            # Backward-compat restore from legacy top-level keys.
            vass_state = {
                "last_entry_at_by_signature": state.get("vass_last_entry_at_by_signature", {})
                or {},
                "cooldown_until_by_signature": state.get("vass_cooldown_until_by_signature", {})
                or {},
                "last_entry_date_by_direction": state.get("vass_last_entry_date_by_direction", {})
                or {},
                "consecutive_losses": state.get("vass_consecutive_losses", 0) or 0,
                "loss_breaker_pause_until": state.get("vass_loss_breaker_pause_until"),
            }
        else:
            # Preserve old top-level fields if present and embedded state lacks them.
            if "consecutive_losses" not in vass_state:
                vass_state["consecutive_losses"] = state.get("vass_consecutive_losses", 0) or 0
            if "loss_breaker_pause_until" not in vass_state:
                vass_state["loss_breaker_pause_until"] = state.get("vass_loss_breaker_pause_until")
        try:
            self._vass_entry_engine.from_dict(vass_state)
        except Exception:
            self._vass_entry_engine.reset()
        self._spread_neutrality_warn_by_key = {}
        for k, v in (state.get("spread_neutrality_warn_by_key", {}) or {}).items():
            try:
                self._spread_neutrality_warn_by_key[str(k)] = datetime.strptime(
                    str(v)[:19], "%Y-%m-%d %H:%M:%S"
                )
            except Exception:
                continue
        if self._spread_neutrality_warn_by_key:
            active_keys = {self._build_spread_key(s) for s in self._spread_positions}
            self._spread_neutrality_warn_by_key = {
                k: v for k, v in self._spread_neutrality_warn_by_key.items() if k in active_keys
            }

        self._last_intraday_close_time = None
        last_close = state.get("last_intraday_close_time")
        if last_close:
            try:
                self._last_intraday_close_time = datetime.strptime(
                    str(last_close)[:19], "%Y-%m-%d %H:%M:%S"
                )
            except Exception:
                self._last_intraday_close_time = None
        self._last_intraday_close_strategy = state.get("last_intraday_close_strategy")
        try:
            self._itm_horizon_engine.from_dict(state.get("itm_horizon_state", {}) or {})
        except Exception:
            self._itm_horizon_engine.reset()

    def reset(self) -> None:
        """Reset engine state."""
        # Legacy
        self._position = None
        self._trades_today = 0
        self._last_trade_date = None

        # V2.1.1
        self._swing_position = None
        self._intraday_position = None
        self._intraday_position_engine = None
        self._intraday_positions = {"MICRO": [], "ITM": []}
        self._spread_positions = []
        self._spread_position = None
        self._intraday_trades_today = 0
        self._intraday_call_trades_today = 0
        self._intraday_put_trades_today = 0
        self._intraday_itm_trades_today = 0
        self._intraday_micro_trades_today = 0
        self._current_mode = OptionsMode.SWING
        self._micro_regime_engine.reset_daily()
        self._vix_at_open = 0.0
        self._spy_at_open = 0.0
        self._spy_gap_pct = 0.0

        # V2.3: Reset spam prevention flags
        self._entry_attempted_today = False
        self._spread_attempts_today_by_key = {}
        self._swing_time_warning_logged = False

        # V2.3.2: Reset pending intraday entry flag
        self._pending_intraday_entry = False
        self._pending_intraday_entry_since = None
        self._pending_intraday_entry_engine = None
        self._pending_intraday_entries = {}

        # V2.3.3: Reset pending intraday exit flag
        self._pending_intraday_exit = False
        self._pending_intraday_exit_engine = None
        self._pending_intraday_exit_lanes = set()
        self._pending_intraday_exit_symbols = set()
        self._transition_context_snapshot = None
        self._rejection_margin_cap = None
        self._spread_failure_cooldown_until = None
        self._spread_failure_cooldown_until_by_dir = {}
        self._last_spread_scan_time = None
        self._last_spread_failure_stats = None
        self._last_credit_failure_stats = None
        self._last_entry_validation_failure = None
        self._last_intraday_validation_failure_by_lane = {"MICRO": None, "ITM": None}
        self._last_intraday_validation_detail_by_lane = {"MICRO": None, "ITM": None}
        self._last_trade_limit_failure = None
        self._last_trade_limit_detail = None
        self._spread_exit_signal_cooldown = {}
        self._call_consecutive_losses = 0
        self._call_cooldown_until_date = None
        self._put_consecutive_losses = 0
        self._put_cooldown_until_date = None
        self._spread_result_history = []
        self._paper_track_history = []
        self._win_rate_shutoff = False
        self._win_rate_shutoff_date = None
        self._vass_entry_engine.reset()
        self._spread_neutrality_warn_by_key = {}
        self._spread_hold_guard_logged.clear()
        self._intraday_force_exit_hold_skip_log_date = {}
        self._last_intraday_close_time = None
        self._last_intraday_close_strategy = None
        self._itm_horizon_engine.reset()

        self.log("OPT: Engine reset - all positions cleared")

    def reset_daily(self, current_date: str) -> None:
        """Reset daily trade counter at start of new day."""
        if current_date != self._last_trade_date:
            self._trades_today = 0
            self._intraday_trades_today = 0
            self._intraday_call_trades_today = 0
            self._intraday_put_trades_today = 0
            self._intraday_itm_trades_today = 0
            self._intraday_micro_trades_today = 0
            self._swing_trades_today = 0  # V2.9
            self._total_options_trades_today = 0  # V2.9
            self._last_trade_date = current_date

            # V2.3 FIX: Reset entry attempt flag for new day
            self._entry_attempted_today = False
            self._spread_attempts_today_by_key = {}
            self._swing_time_warning_logged = False
            # V2.21: Clear rejection margin cap for new day
            self._rejection_margin_cap = None

            # V2.3.2: Reset pending intraday entry flag
            self._pending_intraday_entry = False
            self._pending_intraday_entry_since = None
            self._pending_intraday_entry_engine = None
            self._pending_intraday_entries = {}

            # V2.3.3: Reset pending intraday exit flag
            self._pending_intraday_exit = False
            self._pending_intraday_exit_engine = None
            self._pending_intraday_exit_lanes = set()
            self._pending_intraday_exit_symbols = set()
            self._transition_context_snapshot = None
            if not self.has_intraday_position():
                self._intraday_position_engine = None
            self._intraday_force_exit_hold_skip_log_date = {}
            self._last_intraday_close_time = None
            self._last_intraday_close_strategy = None

            # Reset Micro Regime Engine for new day
            self._micro_regime_engine.reset_daily()

            # V2.4.3: Clear spread failure cooldown for new day
            self._spread_failure_cooldown_until = None
            self._spread_failure_cooldown_until_by_dir = {}
            self._last_spread_scan_time = None

            # Keep intraday state whenever a live broker holding still exists.
            # This avoids reset->orphan churn when an expected force-close fails.
            for lane, lane_positions in list(self._intraday_positions.items()):
                if not lane_positions:
                    continue
                kept_positions = []
                for intraday_pos in list(lane_positions):
                    keep_position = self.should_hold_intraday_overnight(intraday_pos)
                    if not keep_position and self.algorithm is not None:
                        try:
                            sym = intraday_pos.contract.symbol
                            broker_symbol = sym
                            if isinstance(sym, str):
                                broker_symbol = self.algorithm.Symbol(sym)
                            sec = self.algorithm.Portfolio[broker_symbol]
                            if sec is not None and sec.Invested and abs(int(sec.Quantity)) > 0:
                                intraday_pos.num_contracts = abs(int(sec.Quantity))
                                keep_position = True
                        except Exception:
                            keep_position = False

                    if keep_position:
                        kept_positions.append(intraday_pos)
                        self.log(
                            f"OPT: DAILY_RESET_KEEP - preserving live intraday position | Lane={lane}",
                            trades_only=True,
                        )
                    else:
                        self.log(
                            f"OPT: WARNING - Intraday position found at daily reset, clearing | Lane={lane}"
                        )
                self._intraday_positions[str(lane).upper()] = kept_positions
            self._refresh_legacy_intraday_mirrors()

            if self._spread_neutrality_warn_by_key:
                active_keys = {self._build_spread_key(s) for s in self._spread_positions}
                self._spread_neutrality_warn_by_key = {
                    k: v for k, v in self._spread_neutrality_warn_by_key.items() if k in active_keys
                }

            self._itm_horizon_engine.emit_daily_summary(current_date)
            self.log(f"OPT: Daily reset for {current_date}")

    # =========================================================================
    # V2.9: TRADE COUNTER ENFORCEMENT (Bug #4 Fix)
    # =========================================================================

    def _increment_trade_counter(
        self,
        mode: OptionsMode,
        direction: Optional[OptionDirection] = None,
        strategy: Optional[str] = None,
    ) -> None:
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
            strat = self._canonical_intraday_strategy_name(strategy)
            if self._is_itm_momentum_strategy_name(strat):
                self._intraday_itm_trades_today += 1
            else:
                self._intraday_micro_trades_today += 1
            if direction == OptionDirection.CALL:
                self._intraday_call_trades_today += 1
            elif direction == OptionDirection.PUT:
                self._intraday_put_trades_today += 1
            self.log(
                f"TRADE_COUNTER: Intraday={self._intraday_trades_today}/{config.INTRADAY_MAX_TRADES_PER_DAY} | "
                f"ITM={self._intraday_itm_trades_today}/{getattr(config, 'ITM_MAX_TRADES_PER_DAY', 999)} | "
                f"MICRO={self._intraday_micro_trades_today}/{getattr(config, 'MICRO_MAX_TRADES_PER_DAY', 999)} | "
                f"CALL={self._intraday_call_trades_today}/{getattr(config, 'INTRADAY_MAX_TRADES_PER_DIRECTION_PER_DAY', 999)} | "
                f"PUT={self._intraday_put_trades_today}/{getattr(config, 'INTRADAY_MAX_TRADES_PER_DIRECTION_PER_DAY', 999)} | "
                f"Total={self._total_options_trades_today}/{config.MAX_OPTIONS_TRADES_PER_DAY}"
            )
        else:
            self._swing_trades_today += 1
            self.log(
                f"TRADE_COUNTER: Swing={self._swing_trades_today}/{config.MAX_SWING_TRADES_PER_DAY} | "
                f"Total={self._total_options_trades_today}/{config.MAX_OPTIONS_TRADES_PER_DAY}"
            )

    def _can_trade_options(
        self, mode: OptionsMode, direction: Optional[OptionDirection] = None
    ) -> bool:
        """
        V2.9: Check if trading is allowed based on daily limits.

        Prevents over-trading when VIX flickers around strategy thresholds.

        Args:
            mode: The trading mode to check.

        Returns:
            True if trading is allowed, False if limits exceeded.
        """

        def reject(reason: str, detail: str) -> bool:
            self.set_last_trade_limit_failure(reason, detail)
            return False

        # Reset previous limit failure context for this check.
        self.set_last_trade_limit_failure(None, None)

        # Check global limit
        if self._total_options_trades_today >= config.MAX_OPTIONS_TRADES_PER_DAY:
            detail = (
                f"Global limit reached | "
                f"{self._total_options_trades_today}/{config.MAX_OPTIONS_TRADES_PER_DAY}"
            )
            self.log(f"TRADE_LIMIT: {detail}")
            return reject("R_SLOT_TOTAL_MAX", detail)

        reserve_checks_active = True
        if self.algorithm is not None:
            try:
                release_h = int(getattr(config, "OPTIONS_RESERVE_RELEASE_HOUR", 13))
                release_m = int(getattr(config, "OPTIONS_RESERVE_RELEASE_MINUTE", 30))
                release_minute_of_day = release_h * 60 + release_m
                now_minute_of_day = self.algorithm.Time.hour * 60 + self.algorithm.Time.minute
                reserve_checks_active = now_minute_of_day < release_minute_of_day
            except Exception:
                reserve_checks_active = True

        # Check mode-specific limits
        if mode == OptionsMode.INTRADAY:
            if bool(getattr(config, "INTRADAY_ENFORCE_SHARED_DAILY_CAP", False)):
                if self._intraday_trades_today >= config.INTRADAY_MAX_TRADES_PER_DAY:
                    detail = (
                        f"Intraday limit reached | "
                        f"{self._intraday_trades_today}/{config.INTRADAY_MAX_TRADES_PER_DAY}"
                    )
                    self.log(f"TRADE_LIMIT: {detail}")
                    return reject("R_SLOT_INTRADAY_MAX", detail)
            per_direction_cap = int(getattr(config, "INTRADAY_MAX_TRADES_PER_DIRECTION_PER_DAY", 0))
            if (
                bool(getattr(config, "INTRADAY_ENFORCE_SHARED_DIRECTION_CAP", False))
                and per_direction_cap > 0
                and direction is not None
            ):
                dir_count = (
                    self._intraday_call_trades_today
                    if direction == OptionDirection.CALL
                    else self._intraday_put_trades_today
                )
                if dir_count >= per_direction_cap:
                    detail = (
                        f"Intraday direction cap reached | "
                        f"Dir={direction.value} {dir_count}/{per_direction_cap}"
                    )
                    self.log(f"TRADE_LIMIT: {detail}")
                    return reject("R_SLOT_DIRECTION_MAX", detail)
            if reserve_checks_active and getattr(
                config, "OPTIONS_RESERVE_SWING_DAILY_SLOTS_ENABLED", False
            ):
                reserve = max(int(getattr(config, "OPTIONS_MIN_SWING_SLOTS_PER_DAY", 0)), 0)
                if reserve > 0:
                    intraday_cap = max(config.MAX_OPTIONS_TRADES_PER_DAY - reserve, 0)
                    if self._intraday_trades_today >= intraday_cap:
                        detail = (
                            f"Intraday reserve guard | "
                            f"Intraday={self._intraday_trades_today} >= Cap={intraday_cap} | "
                            f"ReservedSwingSlots={reserve}"
                        )
                        self.log(f"TRADE_LIMIT: {detail}")
                        return reject("R_SLOT_INTRADAY_RESERVE", detail)
        else:  # SWING
            if self._swing_trades_today >= config.MAX_SWING_TRADES_PER_DAY:
                detail = (
                    f"Swing limit reached | "
                    f"{self._swing_trades_today}/{config.MAX_SWING_TRADES_PER_DAY}"
                )
                self.log(f"TRADE_LIMIT: {detail}")
                return reject("R_SLOT_SWING_MAX", detail)
            if reserve_checks_active and getattr(
                config, "OPTIONS_RESERVE_INTRADAY_DAILY_SLOTS_ENABLED", False
            ):
                reserve = max(int(getattr(config, "OPTIONS_MIN_INTRADAY_SLOTS_PER_DAY", 0)), 0)
                if reserve > 0:
                    swing_cap = max(config.MAX_OPTIONS_TRADES_PER_DAY - reserve, 0)
                    if self._swing_trades_today >= swing_cap:
                        detail = (
                            f"Swing reserve guard | "
                            f"Swing={self._swing_trades_today} >= Cap={swing_cap} | "
                            f"ReservedIntradaySlots={reserve}"
                        )
                        self.log(f"TRADE_LIMIT: {detail}")
                        return reject("R_SLOT_SWING_RESERVE", detail)

        return True

    def _add_trading_days_to_date(self, trade_date: datetime.date, days: int) -> datetime.date:
        """Add trading days (Mon-Fri) to a date."""
        remaining = max(0, int(days))
        cursor = trade_date
        while remaining > 0:
            cursor = cursor + timedelta(days=1)
            if cursor.weekday() < 5:
                remaining -= 1
        return cursor
