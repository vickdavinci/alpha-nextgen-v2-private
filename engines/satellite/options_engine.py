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
from engines.satellite.itm_horizon_engine import ITMHorizonEngine
from engines.satellite.micro_entry_engine import MicroEntryEngine
from engines.satellite.vass_entry_engine import VASSEntryEngine
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
    is_closing: bool = False  # V2.12 Fix #2: Prevent duplicate exit signals
    highest_pnl_pct: float = 0.0  # V9.4: Track high-water mark for trailing stop

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
            "is_closing": self.is_closing,
            "highest_pnl_pct": self.highest_pnl_pct,
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
            highest_pnl_pct=data.get("highest_pnl_pct", 0.0),  # V9.4: Trailing stop HWM
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
    V5.3: VASS Conviction Engine - VIX Direction + Level Tracking.

    Tracks VIX on multiple timeframes:
    - Intraday: 30-min SMA for strategy flickering prevention
    - Weekly: 5-day SMA for short-term trend
    - Monthly: 20-day SMA for sustained trend

    Conviction triggers when VIX shows clear directional movement:
    - 5d change > +20% → BEARISH conviction
    - 5d change < -15% → BULLISH conviction
    - 20d change > +30% → STRONG BEARISH
    - 20d change < -20% → STRONG BULLISH
    - VIX crosses above 25 → BEARISH
    - VIX crosses below 15 → BULLISH
    """

    def __init__(self, smoothing_minutes: int = 30, log_func=None):
        """
        Initialize VASS Conviction Engine.

        Args:
            smoothing_minutes: Intraday SMA window (default 30 minutes)
            log_func: Logging function (optional)
        """
        # Intraday smoothing (existing)
        self._vix_history: Deque[float] = deque(
            maxlen=smoothing_minutes or config.VASS_IV_SMOOTHING_MINUTES
        )
        self._last_classification: Optional[str] = None
        self._log = log_func or (lambda x: None)

        # V5.3: Daily VIX tracking for conviction
        self._vix_daily_history: Deque[float] = deque(
            maxlen=config.VASS_VIX_20D_PERIOD + 5  # 25 days buffer
        )
        self._last_vix_daily: Optional[float] = None
        self._last_update_date: Optional[str] = None

        # V5.3: Level crossing tracking
        self._prev_vix_level: Optional[float] = None
        self._crossed_above_fear: bool = False
        self._crossed_below_complacent: bool = False

    def update(self, vix_value: float, current_date: str = None) -> None:
        """
        Add VIX reading (called every minute from OnData).

        Args:
            vix_value: Current VIX value
            current_date: Date string (YYYY-MM-DD) for daily tracking
        """
        if vix_value <= 0:
            return

        # Intraday update
        self._vix_history.append(vix_value)

        # Daily update (once per day)
        if current_date and current_date != self._last_update_date:
            self._vix_daily_history.append(vix_value)
            self._last_update_date = current_date

            # Track level crossings
            if self._prev_vix_level is not None:
                fear_cross_level = float(getattr(config, "VASS_VIX_FEAR_CROSS_LEVEL", 22.0))
                complacent_cross_level = float(
                    getattr(config, "VASS_VIX_COMPLACENT_CROSS_LEVEL", 14.0)
                )

                # V10.8: low-VIX conviction broadening via relaxed crossing levels.
                if bool(getattr(config, "VASS_LOW_VIX_CONVICTION_RELAX_ENABLED", False)):
                    low_vix_max = float(getattr(config, "VASS_LOW_VIX_CONVICTION_MAX_VIX", 16.0))
                    if self._prev_vix_level <= low_vix_max and vix_value <= low_vix_max:
                        crossing_delta = float(getattr(config, "VASS_LOW_VIX_CROSSING_DELTA", 0.80))
                        fear_cross_level = max(0.1, fear_cross_level - crossing_delta)
                        complacent_cross_level = complacent_cross_level + crossing_delta

                # Crossed above fear threshold
                if self._prev_vix_level < fear_cross_level <= vix_value:
                    self._crossed_above_fear = True
                    self._log(f"VASS: VIX crossed ABOVE {fear_cross_level:.1f} (fear threshold)")
                else:
                    self._crossed_above_fear = False

                # Crossed below complacent threshold
                if self._prev_vix_level > complacent_cross_level >= vix_value:
                    self._crossed_below_complacent = True
                    self._log(
                        f"VASS: VIX crossed BELOW {complacent_cross_level:.1f} (complacent threshold)"
                    )
                else:
                    self._crossed_below_complacent = False

            self._prev_vix_level = vix_value
            self._last_vix_daily = vix_value

    def get_smoothed_vix(self) -> float:
        """Return 30-min SMA of VIX (intraday smoothing)."""
        if not self._vix_history:
            return 20.0  # Default to medium IV
        return sum(self._vix_history) / len(self._vix_history)

    def get_vix_5d_sma(self) -> Optional[float]:
        """Return 5-day SMA of VIX (weekly trend)."""
        period = config.VASS_VIX_5D_PERIOD
        if len(self._vix_daily_history) < period:
            return None
        recent = list(self._vix_daily_history)[-period:]
        return sum(recent) / len(recent)

    def get_vix_20d_sma(self) -> Optional[float]:
        """Return 20-day SMA of VIX (monthly trend)."""
        period = config.VASS_VIX_20D_PERIOD
        if len(self._vix_daily_history) < period:
            return None
        recent = list(self._vix_daily_history)[-period:]
        return sum(recent) / len(recent)

    def get_vix_5d_change(self) -> Optional[float]:
        """
        Return 5-day VIX change as percentage.

        Returns:
            Percentage change (0.20 = +20%), or None if insufficient data
        """
        period = config.VASS_VIX_5D_PERIOD
        if len(self._vix_daily_history) < period:
            return None
        history = list(self._vix_daily_history)
        vix_now = history[-1]
        vix_5d_ago = history[-period]
        if vix_5d_ago <= 0:
            return None
        return (vix_now - vix_5d_ago) / vix_5d_ago

    def get_vix_20d_change(self) -> Optional[float]:
        """
        Return 20-day VIX change as percentage.

        Returns:
            Percentage change (0.30 = +30%), or None if insufficient data
        """
        period = config.VASS_VIX_20D_PERIOD
        if len(self._vix_daily_history) < period:
            return None
        history = list(self._vix_daily_history)
        vix_now = history[-1]
        vix_20d_ago = history[-period]
        if vix_20d_ago <= 0:
            return None
        return (vix_now - vix_20d_ago) / vix_20d_ago

    def classify(self) -> str:
        """Classify IV environment: LOW, MEDIUM, HIGH."""
        if not self._vix_history or len(self._vix_history) < 5:
            self._log("VASS: Insufficient VIX data, defaulting to MEDIUM")
            return "MEDIUM"

        smoothed = self.get_smoothed_vix()

        if smoothed < config.VASS_IV_LOW_THRESHOLD:
            classification = "LOW"
        elif smoothed > config.VASS_IV_HIGH_THRESHOLD:
            classification = "HIGH"
        else:
            classification = "MEDIUM"

        if self._last_classification != classification:
            self._log(
                f"VASS: IV environment changed {self._last_classification} → {classification} "
                f"(VIX SMA={smoothed:.1f})"
            )
            self._last_classification = classification

        return classification

    def has_conviction(self) -> Tuple[bool, Optional[str], str]:
        """
        V5.3: Check if VASS has conviction to override Macro.

        Returns:
            Tuple of (has_conviction, direction, reason)
            - has_conviction: True if clear signal
            - direction: "BULLISH" or "BEARISH" or None
            - reason: Human-readable explanation
        """
        vix_5d_change = self.get_vix_5d_change()
        vix_20d_change = self.get_vix_20d_change()

        fear_cross_level = float(getattr(config, "VASS_VIX_FEAR_CROSS_LEVEL", 22.0))
        complacent_cross_level = float(getattr(config, "VASS_VIX_COMPLACENT_CROSS_LEVEL", 14.0))
        bearish_5d_threshold = float(getattr(config, "VASS_VIX_5D_BEARISH_THRESHOLD", 0.16))
        bullish_5d_threshold = float(getattr(config, "VASS_VIX_5D_BULLISH_THRESHOLD", -0.20))

        # V10.8: low-VIX conviction broadening (threshold-first, no new indicators).
        if bool(getattr(config, "VASS_LOW_VIX_CONVICTION_RELAX_ENABLED", False)):
            smoothed_vix = self.get_smoothed_vix()
            low_vix_max = float(getattr(config, "VASS_LOW_VIX_CONVICTION_MAX_VIX", 16.0))
            if smoothed_vix <= low_vix_max:
                crossing_delta = float(getattr(config, "VASS_LOW_VIX_CROSSING_DELTA", 0.80))
                fear_cross_level = max(0.1, fear_cross_level - crossing_delta)
                complacent_cross_level = complacent_cross_level + crossing_delta

                low_vix_5d_threshold = float(
                    getattr(config, "VASS_LOW_VIX_5D_CHANGE_THRESHOLD", 0.12)
                )
                bearish_5d_threshold = min(bearish_5d_threshold, low_vix_5d_threshold)
                bullish_5d_threshold = max(bullish_5d_threshold, -low_vix_5d_threshold)

        # Level crossing conviction (immediate)
        if self._crossed_above_fear:
            return True, "BEARISH", f"VIX crossed above {fear_cross_level:.1f}"

        if self._crossed_below_complacent:
            return True, "BULLISH", f"VIX crossed below {complacent_cross_level:.1f}"

        # 5-day change conviction (fast-moving fear)
        if vix_5d_change is not None:
            if vix_5d_change > bearish_5d_threshold:
                return (
                    True,
                    "BEARISH",
                    f"VIX 5d change +{vix_5d_change:.0%} > +{bearish_5d_threshold:.0%}",
                )

            if vix_5d_change < bullish_5d_threshold:
                return (
                    True,
                    "BULLISH",
                    f"VIX 5d change {vix_5d_change:.0%} < {bullish_5d_threshold:.0%}",
                )

        # 20-day change conviction (sustained direction)
        if vix_20d_change is not None:
            if vix_20d_change > config.VASS_VIX_20D_STRONG_BEARISH:
                return (
                    True,
                    "BEARISH",
                    f"VIX 20d change +{vix_20d_change:.0%} > +{config.VASS_VIX_20D_STRONG_BEARISH:.0%} (STRONG)",
                )

            if vix_20d_change < config.VASS_VIX_20D_STRONG_BULLISH:
                return (
                    True,
                    "BULLISH",
                    f"VIX 20d change {vix_20d_change:.0%} < {config.VASS_VIX_20D_STRONG_BULLISH:.0%} (STRONG)",
                )

        # No conviction
        return False, None, "No clear VIX direction signal"

    def is_ready(self) -> bool:
        """True if enough history for reliable classification."""
        return len(self._vix_history) >= 10

    def is_conviction_ready(self) -> bool:
        """True if enough daily history for conviction signals (5 days min)."""
        return len(self._vix_daily_history) >= config.VASS_VIX_5D_PERIOD

    def get_history_length(self) -> int:
        """Return current intraday history length."""
        return len(self._vix_history)

    def get_daily_history_length(self) -> int:
        """Return current daily history length."""
        return len(self._vix_daily_history)

    def reset(self) -> None:
        """Clear all history (for testing or session reset)."""
        self._vix_history.clear()
        self._vix_daily_history.clear()
        self._last_classification = None
        self._last_vix_daily = None
        self._last_update_date = None
        self._prev_vix_level = None
        self._crossed_above_fear = False
        self._crossed_below_complacent = False

    def get_state_summary(self) -> dict:
        """Return current state for logging/debugging."""
        return {
            "vix_intraday_sma": self.get_smoothed_vix(),
            "vix_5d_sma": self.get_vix_5d_sma(),
            "vix_20d_sma": self.get_vix_20d_sma(),
            "vix_5d_change": self.get_vix_5d_change(),
            "vix_20d_change": self.get_vix_20d_change(),
            "iv_classification": self.classify() if self.is_ready() else "NOT_READY",
            "conviction": self.has_conviction(),
            "daily_history_len": len(self._vix_daily_history),
        }


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
            recommended_strategy=IntradayStrategy(data.get("recommended_strategy", "NO_TRADE")),
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
            return VIXLevel.LOW, config.MICRO_SCORE_VIX_CALM
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

        # V10: All confirmation paths route to ITM_MOMENTUM (DEBIT_MOMENTUM deprecated)
        def confirmation_strategy(
            direction: OptionDirection, reason: str
        ) -> Tuple[IntradayStrategy, Optional[OptionDirection], str]:
            return (IntradayStrategy.ITM_MOMENTUM, direction, reason)

        # V10.7 Phase-5: optional simplification to disable DEBIT_FADE.
        def divergence_strategy_or_skip(
            direction: OptionDirection, reason: str
        ) -> Tuple[IntradayStrategy, Optional[OptionDirection], str]:
            if bool(getattr(config, "INTRADAY_DEBIT_FADE_ENABLED", False)):
                return (IntradayStrategy.DEBIT_FADE, direction, reason)
            return (IntradayStrategy.NO_TRADE, None, f"DEBIT_FADE_DISABLED: {reason}")

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
            pass  # Fall through to RULE 6 (ITM_MOMENTUM) and RULE 7 (caution)
        else:
            # V2.19: VIX Floor Check - Block trades in "apathy" market
            vix_floor = getattr(config, "INTRADAY_DEBIT_FADE_VIX_MIN", 13.5)
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
            if abs(qqq_move_pct) < min_move_gate:
                return (
                    IntradayStrategy.NO_TRADE,
                    None,
                    f"MIN_MOVE_{vix_tier_label}: |{qqq_move_pct:.2f}%| < {min_move_gate}% (VIX={vix_current:.1f})",
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
                    # V10: HIGH VIX divergence → ITM_MOMENTUM (no spread cap in volatile market)
                    if vix_current >= 25:
                        return (
                            IntradayStrategy.ITM_MOMENTUM,
                            OptionDirection.PUT,
                            f"HIGH_VIX_DIVERGENCE: QQQ +{qqq_move_pct:.2f}% but VIX {vix_direction.value} "
                            f"(VIX={vix_current:.1f}>=25) → ITM PUT",
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
                    if (
                        abs(qqq_move_pct) >= config.INTRADAY_QQQ_FALLBACK_MIN_MOVE
                        and micro_score >= config.MICRO_SCORE_BULLISH_CONFIRM
                    ):
                        # V10: All confirmation paths → ITM_MOMENTUM
                        return confirmation_strategy(
                            OptionDirection.CALL,
                            f"STABLE_FALLBACK: QQQ +{qqq_move_pct:.2f}% + "
                            f"Score={micro_score:.0f} → CALL",
                        )
                    return (
                        IntradayStrategy.NO_TRADE,
                        None,
                        f"STABLE_NO_TRADE: QQQ +{qqq_move_pct:.2f}% "
                        f"Score={micro_score:.0f} (<{config.MICRO_SCORE_BULLISH_CONFIRM})",
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
                    # V10: HIGH VIX divergence → ITM_MOMENTUM (no spread cap in volatile market)
                    if vix_current >= 25:
                        return (
                            IntradayStrategy.ITM_MOMENTUM,
                            OptionDirection.CALL,
                            f"HIGH_VIX_DIVERGENCE: QQQ {qqq_move_pct:.2f}% but VIX {vix_direction.value} "
                            f"(VIX={vix_current:.1f}>=25) → ITM CALL",
                        )
                    return divergence_strategy_or_skip(
                        OptionDirection.CALL,
                        f"DIVERGENCE: QQQ {qqq_move_pct:.2f}% but VIX {vix_direction.value} → Fade with CALL",
                    )

                elif vix_direction == VIXDirection.STABLE:
                    # V6.9: VIX stable fallback + 2-of-3 confirmation
                    # Require strong QQQ move AND bearish micro score to take PUT
                    if (
                        abs(qqq_move_pct) >= config.INTRADAY_QQQ_FALLBACK_MIN_MOVE
                        and micro_score <= config.MICRO_SCORE_BEARISH_CONFIRM
                    ):
                        # V10: All confirmation paths → ITM_MOMENTUM
                        return confirmation_strategy(
                            OptionDirection.PUT,
                            f"STABLE_FALLBACK: QQQ {qqq_move_pct:.2f}% + "
                            f"Score={micro_score:.0f} → PUT",
                        )
                    return (
                        IntradayStrategy.NO_TRADE,
                        None,
                        f"STABLE_NO_TRADE: QQQ {qqq_move_pct:.2f}% "
                        f"Score={micro_score:.0f} (>{config.MICRO_SCORE_BEARISH_CONFIRM})",
                    )

        # =====================================================================
        # RULE 6: HIGH VIX MOMENTUM (ITM options for elevated fear)
        # Only in specific high-fear regimes with sufficient move
        # V6.5: WORSENING/WORSENING_HIGH always use PUT (VIX rising = fear real)
        # =====================================================================
        momentum_regimes = {
            MicroRegime.DETERIORATING,
            MicroRegime.ELEVATED,
            MicroRegime.WORSENING,  # V6.5: Added - MEDIUM + RISING, use ITM PUT
            MicroRegime.WORSENING_HIGH,
        }
        if micro_regime in momentum_regimes:
            if vix_current > config.INTRADAY_ITM_MIN_VIX:
                if abs(qqq_move_pct) >= config.INTRADAY_ITM_MIN_MOVE:
                    # V6.5: WORSENING and WORSENING_HIGH - VIX RISING regimes
                    # V9.2 RCA: Only trade PUT when QQQ confirms downward direction.
                    # Previously forced PUT regardless of QQQ direction, causing 52%
                    # wrong-way PUTs against rising markets in 2022.
                    if micro_regime in (MicroRegime.WORSENING, MicroRegime.WORSENING_HIGH):
                        if qqq_is_down:
                            return (
                                IntradayStrategy.ITM_MOMENTUM,
                                OptionDirection.PUT,
                                f"ITM_FEAR: VIX rising in {micro_regime.value}, QQQ {qqq_move_pct:+.2f}% → ITM PUT",
                            )
                        return (
                            IntradayStrategy.NO_TRADE,
                            None,
                            f"WORSENING_QQQ_GATE: {micro_regime.value} but QQQ {qqq_move_pct:+.2f}% UP → skip",
                        )
                    elif qqq_is_up:
                        return (
                            IntradayStrategy.ITM_MOMENTUM,
                            OptionDirection.CALL,
                            f"ITM_MOMENTUM: QQQ +{qqq_move_pct:.2f}% in {micro_regime.value}",
                        )
                    elif qqq_is_down:
                        return (
                            IntradayStrategy.ITM_MOMENTUM,
                            OptionDirection.PUT,
                            f"ITM_MOMENTUM: QQQ {qqq_move_pct:.2f}% in {micro_regime.value}",
                        )

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
        self._pending_entry_strategy: Optional[str] = None

        # V2.3: Pending spread entry state
        self._pending_spread_long_leg: Optional[OptionContract] = None
        self._pending_spread_short_leg: Optional[OptionContract] = None
        self._pending_spread_type: Optional[str] = None
        self._pending_net_debit: Optional[float] = None
        self._pending_max_profit: Optional[float] = None
        self._pending_spread_width: Optional[float] = None

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
        self._last_intraday_validation_failure: Optional[str] = None
        self._last_intraday_validation_detail: Optional[str] = None
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
        self._itm_horizon_engine = ITMHorizonEngine(log_func=self.log)
        self._micro_entry_engine = MicroEntryEngine(log_func=self.log)
        self._vass_entry_engine = VASSEntryEngine(log_func=self.log)

        # V2.27: Win Rate Gate - rolling spread trade result tracker
        self._spread_result_history: List[bool] = []  # True=win, False=loss
        self._win_rate_shutoff: bool = False  # True when win rate < shutoff threshold
        self._win_rate_shutoff_date: Optional[str] = None  # YYYY-MM-DD shutoff activation
        self._paper_track_history: List[bool] = []  # Paper trades during shutoff
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
        # V10.7: Swing loss breaker for VASS spread entries.
        self._vass_consecutive_losses: int = 0
        self._vass_loss_breaker_pause_until: Optional[str] = None  # YYYY-MM-DD

        # V9.4 P0: Exit signal cooldown — prevent per-minute spam when close order fails.
        # Maps spread_key -> last exit signal datetime. If exit fires and close fails,
        # don't re-fire for SPREAD_EXIT_RETRY_MINUTES.
        self._spread_exit_signal_cooldown: Dict[str, datetime] = {}
        # Track which spreads have already logged the hold guard (log once, not every minute)
        self._spread_hold_guard_logged: set = set()
        # Throttle force-exit hold-skip logs (symbol -> YYYY-MM-DD).
        self._intraday_force_exit_hold_skip_log_date: Dict[str, str] = {}

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

    def _get_intraday_force_exit_hhmm(self) -> Tuple[int, int]:
        """Return configured intraday force-exit time as (hour, minute)."""
        force_exit_cfg = str(getattr(config, "INTRADAY_FORCE_EXIT_TIME", "15:30"))
        try:
            hh, mm = force_exit_cfg.split(":")
            return int(hh), int(mm)
        except Exception:
            return 15, 30

    def _canonical_intraday_strategy(
        self, strategy: Optional["IntradayStrategy"]
    ) -> Optional["IntradayStrategy"]:
        """Map deprecated strategy aliases to the active runtime strategy."""
        if strategy == IntradayStrategy.DEBIT_MOMENTUM:
            return IntradayStrategy.ITM_MOMENTUM
        return strategy

    def _canonical_intraday_strategy_name(self, strategy_name: Optional[str]) -> str:
        """Canonical string form used by hold/exit logic."""
        value = str(strategy_name or "").upper()
        if value == IntradayStrategy.DEBIT_MOMENTUM.value:
            return IntradayStrategy.ITM_MOMENTUM.value
        return value

    def _is_itm_momentum_strategy_name(self, strategy_name: Optional[str]) -> bool:
        """True when strategy name maps to ITM momentum."""
        value = self._canonical_intraday_strategy_name(strategy_name)
        return value == IntradayStrategy.ITM_MOMENTUM.value

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
            else:
                current_date = datetime.utcnow().date()
            return max(0, (expiry_date - current_date).days)
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
            hold_enabled = bool(getattr(config, "ITM_V2_HOLD_OVERNIGHT_ENABLED", True))
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
        """
        Track CALL-only consecutive loss streak for adaptive entry cooldown.

        Args:
            symbol: Option symbol string (used to infer CALL/PUT).
            is_win: True if trade closed profitable.
            current_time: Timestamp string (YYYY-MM-DD...) for cooldown date math.
        """
        try:
            symbol_text = str(symbol)
            import re

            # OCC-style parse (e.g., QQQ 211203C00403000)
            is_call = re.search(r"\d{6}C\d{8}", symbol_text) is not None
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

                            # Parse current_time for cooldown date (required for backtest accuracy)
                            if current_time:
                                try:
                                    trade_date = datetime.strptime(
                                        current_time[:10], "%Y-%m-%d"
                                    ).date()
                                    self._call_cooldown_until_date = trade_date + timedelta(
                                        days=cooldown_days
                                    )
                                    self.log(
                                        f"INTRADAY: CALL cooldown armed | LossStreak={self._call_consecutive_losses} | "
                                        f"Until={self._call_cooldown_until_date.isoformat()}",
                                        trades_only=True,
                                    )
                                except Exception:
                                    self.log(
                                        "INTRADAY: CALL cooldown skipped - invalid timestamp",
                                        trades_only=True,
                                    )
                            else:
                                self.log(
                                    "INTRADAY: CALL cooldown skipped - no timestamp",
                                    trades_only=True,
                                )
        except Exception as e:
            self.log(f"INTRADAY: Failed to record CALL result streak: {e}", trades_only=True)

        try:
            self._itm_horizon_engine.on_trade_closed(
                symbol=symbol,
                is_win=is_win,
                current_time=current_time,
                strategy=strategy,
                algorithm=self.algorithm,
            )
        except Exception as e:
            self.log(f"ITM_V2: result tracking failed: {e}", trades_only=True)

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

        state = self._micro_regime_engine.update(
            vix_current=vix_current,
            vix_open=vix_open_for_micro,
            qqq_current=qqq_current,
            qqq_open=qqq_open,
            current_time=current_time,
            macro_regime_score=macro_regime_score,
            vix_level_override=vix_level_override,
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

        # Step 3: Get Macro direction
        macro_direction = self.get_macro_direction(macro_regime_score)

        # Step 4: Resolve trade signal using shared resolver
        # If conviction is not active, fall back to Micro's computed direction
        # so the resolver doesn't treat it as "no direction".
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

        should_trade, resolved_direction, resolve_reason = self.resolve_trade_signal(
            engine="MICRO",
            engine_direction=engine_direction,
            engine_conviction=has_conviction,
            macro_direction=macro_direction,
            conviction_strength=uvxy_pct if has_conviction else None,
            engine_regime=state.micro_regime.value if state is not None else None,
            engine_recommended_direction=recommended_direction_str,
        )

        if not should_trade:
            return False, None, state, resolve_reason

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
            return False, None, state, "NO_DIRECTION: Micro has no recommended direction"

        # Step 6: Direction conflict resolution for FADE strategies
        # Prevents counter-trend trades in strongly trending markets
        if state.recommended_strategy == IntradayStrategy.DEBIT_FADE:
            # Strong bullish regime + PUT = conflict
            if (
                macro_regime_score > config.DIRECTION_CONFLICT_BULLISH_THRESHOLD
                and final_direction == OptionDirection.PUT
            ):
                self.log(
                    f"DIRECTION_CONFLICT: Skipping FADE PUT - regime {macro_regime_score:.1f} "
                    f"> {config.DIRECTION_CONFLICT_BULLISH_THRESHOLD} (strong bullish)"
                )
                return False, None, state, "DIRECTION_CONFLICT: FADE PUT in strong bullish regime"

            # Strong bearish regime + CALL = conflict
            if (
                macro_regime_score < config.DIRECTION_CONFLICT_BEARISH_THRESHOLD
                and final_direction == OptionDirection.CALL
            ):
                self.log(
                    f"DIRECTION_CONFLICT: Skipping FADE CALL - regime {macro_regime_score:.1f} "
                    f"< {config.DIRECTION_CONFLICT_BEARISH_THRESHOLD} (strong bearish)"
                )
                return False, None, state, "DIRECTION_CONFLICT: FADE CALL in strong bearish regime"

        # Build reason string for logging
        if has_conviction:
            reason = f"CONVICTION: {conviction_reason} | Macro={macro_direction} | {resolve_reason}"
        else:
            reason = f"MICRO_DIRECTION: {final_direction.value} | Macro={macro_direction} | {resolve_reason}"

        return True, final_direction, state, reason

    def count_options_positions(self) -> Tuple[int, int, int]:
        """
        V5.3: Count current options positions.

        Returns:
            Tuple of (intraday_count, swing_count, total_count)
        """
        intraday_count = 1 if self._intraday_position is not None else 0
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

    def can_enter_intraday(self) -> Tuple[bool, str]:
        """
        V5.3: Check if intraday entry is allowed.

        Returns:
            Tuple of (allowed, reason)
        """
        intraday_count, _, total_count = self.count_options_positions()

        if total_count >= config.OPTIONS_MAX_TOTAL_POSITIONS:
            return (
                False,
                f"R_SLOT_TOTAL_MAX: {total_count} >= {config.OPTIONS_MAX_TOTAL_POSITIONS}",
            )

        if intraday_count >= config.OPTIONS_MAX_INTRADAY_POSITIONS:
            return (
                False,
                f"R_SLOT_INTRADAY_MAX: {intraday_count} >= {config.OPTIONS_MAX_INTRADAY_POSITIONS}",
            )

        return True, "R_OK"

    def can_enter_swing(
        self,
        direction: Optional[OptionDirection] = None,
        overlay_state: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        V5.3: Check if swing entry is allowed.

        Returns:
            Tuple of (allowed, reason)
        """
        _, swing_count, total_count = self.count_options_positions()

        if total_count >= config.OPTIONS_MAX_TOTAL_POSITIONS:
            return (
                False,
                f"R_SLOT_TOTAL_MAX: {total_count} >= {config.OPTIONS_MAX_TOTAL_POSITIONS}",
            )

        if swing_count >= config.OPTIONS_MAX_SWING_POSITIONS:
            return (
                False,
                f"R_SLOT_SWING_MAX: {swing_count} >= {config.OPTIONS_MAX_SWING_POSITIONS}",
            )

        # Directional slot cap (stage-1 guardrail + stress-overlay shaping).
        if direction is not None:
            wanted_dir = "BULLISH" if direction == OptionDirection.CALL else "BEARISH"
            dir_count = self.get_open_spread_count_by_direction(wanted_dir)
            default_cap = int(getattr(config, "OPTIONS_MAX_SWING_PER_DIRECTION", 2))
            bullish_cap = max(
                int(getattr(config, "OPTIONS_MAX_SWING_BULLISH_POSITIONS", default_cap)),
                0,
            )
            bearish_cap = max(
                int(getattr(config, "OPTIONS_MAX_SWING_BEARISH_POSITIONS", default_cap)),
                0,
            )
            dir_cap = bullish_cap if wanted_dir == "BULLISH" else bearish_cap
            overlay = str(overlay_state or "").upper()
            if overlay == "STRESS":
                if wanted_dir == "BULLISH":
                    dir_cap = int(getattr(config, "MAX_BULLISH_SPREADS_STRESS", 0))
                else:
                    dir_cap = int(getattr(config, "MAX_BEARISH_SPREADS_STRESS", dir_cap))
            elif overlay == "EARLY_STRESS" and wanted_dir == "BULLISH":
                dir_cap = min(dir_cap, int(getattr(config, "MAX_BULLISH_SPREADS_EARLY_STRESS", 1)))
            if dir_count >= dir_cap:
                if overlay in {"STRESS", "EARLY_STRESS"}:
                    return (
                        False,
                        f"R_SLOT_DIRECTION_OVERLAY: {overlay} {wanted_dir} {dir_count} >= {dir_cap}",
                    )
                return (
                    False,
                    f"R_SLOT_DIRECTION_MAX: {wanted_dir} {dir_count} >= {dir_cap}",
                )

        return True, "R_OK"

    def get_macro_direction(self, macro_regime_score: float) -> str:
        """
        V5.3: Determine Macro direction from regime score.

        Args:
            macro_regime_score: Regime score (0-100)

        Returns:
            "BULLISH", "BEARISH", or "NEUTRAL"
        """
        if macro_regime_score > 60:
            return "BULLISH"
        elif macro_regime_score < 40:
            return "BEARISH"
        else:
            return "NEUTRAL"

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
        strategy = str(spread_type or "UNKNOWN").upper()
        direction_key = direction.value if direction is not None else "NONE"
        use_expiry = bool(getattr(config, "VASS_SIMILAR_ENTRY_USE_EXPIRY_BUCKET", True))
        expiry_bucket = ""
        if use_expiry and getattr(long_leg_contract, "expiry", None):
            expiry_bucket = str(long_leg_contract.expiry)
        else:
            expiry_bucket = f"DTE:{int(getattr(long_leg_contract, 'days_to_expiry', -1))}"
        return f"{strategy}|{direction_key}|{expiry_bucket}"

    def _parse_dt(self, date_text: str, hour: int, minute: int) -> Optional[datetime]:
        """Parse current scan timestamp from inputs; fallback to algorithm time."""
        try:
            return datetime.strptime(
                f"{date_text} {int(hour):02d}:{int(minute):02d}:00", "%Y-%m-%d %H:%M:%S"
            )
        except Exception:
            pass
        if self.algorithm is not None:
            try:
                return self.algorithm.Time
            except Exception:
                return None
        return None

    def _check_vass_similar_entry_guard(
        self,
        signature: str,
        now_dt: Optional[datetime],
    ) -> Optional[str]:
        """VASS anti-cluster guard delegated to VASSEntryEngine."""
        return self._vass_entry_engine.check_similar_entry_guard(
            signature=signature,
            now_dt=now_dt,
        )

    def _record_vass_signature_entry(self, signature: str, entry_dt: Optional[datetime]) -> None:
        """Record VASS signature entry via VASSEntryEngine."""
        self._vass_entry_engine.record_signature_entry(signature=signature, entry_dt=entry_dt)

    def _check_vass_direction_day_gap(
        self, direction: Optional[OptionDirection], current_date: str
    ) -> Optional[str]:
        """VASS per-direction day-gap guard delegated to VASSEntryEngine."""
        return self._vass_entry_engine.check_direction_day_gap(
            direction=direction,
            current_date=current_date,
            algorithm=self.algorithm,
        )

    def _record_vass_direction_day_entry(
        self, direction: Optional[OptionDirection], entry_dt: Optional[datetime]
    ) -> None:
        """Record VASS per-direction entry date via VASSEntryEngine."""
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
                self._spread_failure_cooldown_until_by_dir[direction] = cooldown_until
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
        dte_min: int = None,
        dte_max: int = None,
        set_cooldown: bool = True,
        log_filters: bool = True,
        debug_stats: Optional[Dict[str, Any]] = None,
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
        # V2.4.3: Check FAILURE cooldown first (penalty after failed construction)
        if current_time:
            try:
                # V6.12: Direction-scoped cooldown if available
                if hasattr(self, "_spread_failure_cooldown_until_by_dir") and direction:
                    dir_key = direction.value if hasattr(direction, "value") else str(direction)
                    until = self._spread_failure_cooldown_until_by_dir.get(dir_key)
                    if until and current_time < until:
                        return None  # Still in cooldown for this direction
                    elif until:
                        self._spread_failure_cooldown_until_by_dir.pop(dir_key, None)
                elif self._spread_failure_cooldown_until:
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
            if set_cooldown:
                self._set_spread_failure_cooldown(current_time, direction=direction)
            return None

        # For puts, delta is negative so we need to handle that
        is_call = direction == OptionDirection.CALL

        # Direction-specific filters (PUTs need looser liquidity + delta)
        if is_call:
            long_delta_min_base = config.SPREAD_LONG_LEG_DELTA_MIN
            long_delta_max_base = config.SPREAD_LONG_LEG_DELTA_MAX
            short_delta_min = config.SPREAD_SHORT_LEG_DELTA_MIN
            short_delta_max = config.SPREAD_SHORT_LEG_DELTA_MAX
            oi_min_long = config.OPTIONS_MIN_OPEN_INTEREST
            spread_max_long = config.OPTIONS_SPREAD_MAX_PCT
            spread_warn_short = config.OPTIONS_SPREAD_WARNING_PCT
        else:
            long_delta_min_base = config.SPREAD_LONG_LEG_DELTA_MIN_PUT
            long_delta_max_base = config.SPREAD_LONG_LEG_DELTA_MAX_PUT
            short_delta_min = config.SPREAD_SHORT_LEG_DELTA_MIN_PUT
            short_delta_max = config.SPREAD_SHORT_LEG_DELTA_MAX_PUT
            oi_min_long = config.OPTIONS_MIN_OPEN_INTEREST_PUT
            spread_max_long = config.OPTIONS_SPREAD_MAX_PCT_PUT
            spread_warn_short = config.OPTIONS_SPREAD_WARNING_PCT_PUT

        # V2.4.3 FIX: Filter by DTE FIRST, then delta
        # Problem: Chain filter (OPTIONS_SWING_DTE_MAX=45) retrieves 14-45 DTE contracts
        # But spread validation (SPREAD_DTE_MAX=21) rejects anything over 21 DTE
        # If we sort by delta first, a 35 DTE with perfect delta beats 18 DTE with good delta
        # Then the trade gets rejected later for DTE > 21
        # Solution: Filter by SPREAD_DTE_MIN/MAX before considering delta

        # V2.3.21: Find ITM long leg (delta 0.55-0.85 for calls, -0.55 to -0.85 for puts)
        # "Smart Swing" strategy prioritizes ITM for better execution and directional exposure
        # V2.24.1: Elastic Delta Bands — progressively widen if no candidates found
        long_candidates = []
        elastic_widen_used = 0.0

        # Pre-filter by DTE (doesn't change with elastic widening)
        # V2.24.2: Use VASS-aware DTE range if provided, else global config
        effective_dte_min = dte_min if dte_min is not None else config.SPREAD_DTE_MIN
        effective_dte_max = dte_max if dte_max is not None else config.SPREAD_DTE_MAX
        dte_filtered = [
            c for c in filtered if effective_dte_min <= c.days_to_expiry <= effective_dte_max
        ]
        dte_pass = len(dte_filtered)

        for widen in config.ELASTIC_DELTA_STEPS:
            delta_min = max(config.ELASTIC_DELTA_FLOOR, long_delta_min_base - widen)
            delta_max = min(config.ELASTIC_DELTA_CEILING, long_delta_max_base + widen)

            delta_pass = 0
            oi_pass = 0
            spread_pass = 0
            long_candidates = []

            for c in dte_filtered:
                delta_abs = abs(c.delta)
                if delta_min <= delta_abs <= delta_max:
                    delta_pass += 1
                    if c.open_interest >= oi_min_long:
                        oi_pass += 1
                        if c.spread_pct <= spread_max_long:
                            spread_pass += 1
                            long_candidates.append(c)

            if long_candidates:
                elastic_widen_used = widen
                break

        # V2.24: Log filter funnel for debugging
        if log_filters:
            self.log(
                f"SPREAD_FILTER: LongLeg | Total={len(filtered)} | "
                f"DTE_pass={dte_pass} | Delta_pass={delta_pass} | "
                f"OI_pass={oi_pass} | Spread_pass={spread_pass}"
                + (f" | Elastic_widen=±{elastic_widen_used:.2f}" if elastic_widen_used > 0 else "")
            )
        if debug_stats is not None:
            debug_stats.update(
                {
                    "dte_pass": dte_pass,
                    "delta_pass": delta_pass,
                    "oi_pass": oi_pass,
                    "spread_pass": spread_pass,
                    "elastic_widen": elastic_widen_used,
                    "dte_range": f"{effective_dte_min}-{effective_dte_max}",
                }
            )

        if not long_candidates:
            self.log(
                f"SPREAD: No valid long leg | DTE={effective_dte_min}-{effective_dte_max} | "
                f"Delta={long_delta_min_base}-{long_delta_max_base} | "
                f"Elastic steps tried={len(config.ELASTIC_DELTA_STEPS)}"
            )
            if set_cooldown:
                self._set_spread_failure_cooldown(current_time, direction=direction)
            return None

        # V9.1: Direction-specific delta target (CALL=ATM 0.50 for better R:R, PUT=ITM 0.70)
        delta_target = (
            getattr(config, "SPREAD_LONG_LEG_DELTA_TARGET_CALL", 0.50)
            if is_call
            else getattr(config, "SPREAD_LONG_LEG_DELTA_TARGET_PUT", 0.70)
        )
        long_candidates.sort(key=lambda c: abs(abs(c.delta) - delta_target))
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
                if not (short_delta_min <= delta_abs <= short_delta_max):
                    continue

            # Check liquidity (relaxed for short leg)
            if c.open_interest >= oi_min_long // 2:
                if c.spread_pct <= spread_warn_short:
                    short_candidates.append((c, width, delta_abs))

        if not short_candidates:
            self.log(
                f"SPREAD: No valid short leg | LongStrike={long_leg.strike} | "
                f"WidthRange=${config.SPREAD_WIDTH_MIN}-${config.SPREAD_WIDTH_MAX}"
            )
            if set_cooldown:
                self._set_spread_failure_cooldown(current_time, direction=direction)
            return None

        # V9.1: Sort by best R:R (debit/width ascending) within effective width cap
        # Prefer spreads where we pay less relative to width = better risk:reward
        # Cap at SPREAD_WIDTH_EFFECTIVE_MAX to avoid lottery-ticket wide spreads
        effective_max = getattr(config, "SPREAD_WIDTH_EFFECTIVE_MAX", 7.0)
        preferred = [s for s in short_candidates if s[1] <= effective_max]
        if not preferred:
            preferred = short_candidates  # Fall back to all if none within cap

        # Estimate debit for R:R sort: long_leg.mid - short_candidate.mid
        # Lower debit_to_width = better R:R
        def rr_sort_key(x):
            candidate, width, delta_abs = x
            estimated_debit = long_leg.mid_price - candidate.mid_price
            debit_to_width = estimated_debit / width if width > 0 else 1.0
            return (debit_to_width, -width)  # Tiebreak: prefer wider at same R:R

        preferred.sort(key=rr_sort_key)
        short_candidates = preferred
        short_leg = short_candidates[0][0]
        actual_width = abs(short_leg.strike - long_leg.strike)

        self.log(
            f"SPREAD: Selected legs | Long={long_leg.strike} (delta={long_leg.delta:.2f}) | "
            f"Short={short_leg.strike} (delta={short_leg.delta:.2f}) | Width=${actual_width:.0f}"
        )

        return (long_leg, short_leg)

    def select_spread_legs_with_fallback(
        self,
        contracts: List[OptionContract],
        direction: OptionDirection,
        dte_ranges: List[Tuple[int, int]],
        target_width: float = None,
        current_time: str = None,
    ) -> Optional[tuple]:
        """
        V6.12: Try multiple DTE ranges before applying failure cooldown.

        This avoids "cooldown trap" when the primary DTE window has no valid contracts.
        """
        if not dte_ranges:
            return self.select_spread_legs(
                contracts=contracts,
                direction=direction,
                target_width=target_width,
                current_time=current_time,
            )

        failure_stats = []
        for dte_min, dte_max in dte_ranges:
            stats: Dict[str, Any] = {}
            spread_legs = self.select_spread_legs(
                contracts=contracts,
                direction=direction,
                target_width=target_width,
                current_time=current_time,
                dte_min=dte_min,
                dte_max=dte_max,
                set_cooldown=False,
                log_filters=False,
                debug_stats=stats,
            )
            if spread_legs is not None:
                if dte_min is not None and dte_max is not None:
                    self.log(
                        f"SPREAD: Fallback DTE used | Range={dte_min}-{dte_max} | "
                        f"Direction={direction.value}"
                    )
                return spread_legs
            if stats:
                failure_stats.append(stats)

        # All ranges failed -> apply cooldown once
        self._set_spread_failure_cooldown(current_time, direction=direction)
        if failure_stats:
            summary = "; ".join(
                [
                    f"{s.get('dte_range')}|DTE={s.get('dte_pass')}|"
                    f"Delta={s.get('delta_pass')}|OI={s.get('oi_pass')}|"
                    f"Spread={s.get('spread_pass')}|Widen={s.get('elastic_widen')}"
                    for s in failure_stats
                ]
            )
            self._last_spread_failure_stats = summary
        return None

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
        # T-22: Use strategy-scoped cooldown keys so one credit side cannot block the other.
        cooldown_key = strategy.value if hasattr(strategy, "value") else str(strategy)
        legacy_credit_key = "CREDIT"

        # Keep credit spread cooldown handling consistent with debit spread path.
        if current_time:
            try:
                if hasattr(self, "_spread_failure_cooldown_until_by_dir"):
                    until = self._spread_failure_cooldown_until_by_dir.get(cooldown_key)
                    if until is None:
                        until = self._spread_failure_cooldown_until_by_dir.get(legacy_credit_key)
                    if until and current_time < until:
                        return None
                    elif until:
                        self._spread_failure_cooldown_until_by_dir.pop(cooldown_key, None)
                        self._spread_failure_cooldown_until_by_dir.pop(legacy_credit_key, None)
                elif self._spread_failure_cooldown_until:
                    if current_time < self._spread_failure_cooldown_until:
                        return None
                    else:
                        self._spread_failure_cooldown_until = None
            except (ValueError, TypeError):
                pass

        if not contracts:
            self.log("VASS: No contracts provided for credit spread selection")
            return None

        # Filter by DTE
        dte_filtered = [c for c in contracts if dte_min <= c.days_to_expiry <= dte_max]

        # V2.24: Diagnostic logging for credit spread filter funnel
        if log_filters:
            self.log(
                f"SPREAD_FILTER: CreditSpread | Total={len(contracts)} | "
                f"DTE_pass={len(dte_filtered)} (range={dte_min}-{dte_max}) | "
                f"Strategy={strategy.value}"
            )
        if debug_stats is not None:
            debug_stats.update(
                {
                    "dte_pass": len(dte_filtered),
                    "dte_range": f"{dte_min}-{dte_max}",
                }
            )

        if not dte_filtered:
            self.log(
                f"VASS: No contracts in DTE range {dte_min}-{dte_max} "
                f"(available: {[c.days_to_expiry for c in contracts[:5]]}...)"
            )
            if set_cooldown:
                self._set_spread_failure_cooldown(current_time, direction=cooldown_key)
            return None

        if strategy == SpreadStrategy.BULL_PUT_CREDIT:
            # BULL PUT CREDIT: Sell higher put, buy lower put
            # Profit if underlying stays ABOVE short strike
            puts = [c for c in dte_filtered if c.direction == OptionDirection.PUT]

            if not puts:
                self.log("VASS: No PUT contracts available for Bull Put Credit")
                if set_cooldown:
                    self._set_spread_failure_cooldown(current_time, direction=cooldown_key)
                return None

            # Short leg: Delta -0.25 to -0.40 (OTM but decent premium)
            # Must have sufficient bid (premium) to collect
            # V2.24.1: Elastic Delta Bands — progressively widen if no candidates
            short_candidates = []
            delta_pass_count = 0
            credit_pass_count = 0
            oi_pass_count = 0
            spread_pass_count = 0
            elastic_widen_used = 0.0

            for widen in config.ELASTIC_DELTA_STEPS:
                delta_min = max(
                    config.ELASTIC_DELTA_FLOOR,
                    config.CREDIT_SPREAD_SHORT_LEG_DELTA_MIN - widen,
                )
                delta_max = min(
                    config.ELASTIC_DELTA_CEILING,
                    config.CREDIT_SPREAD_SHORT_LEG_DELTA_MAX + widen,
                )

                delta_pass_count = sum(1 for p in puts if delta_min <= abs(p.delta) <= delta_max)
                # V2.25 Fix #3: IV-adaptive credit floor
                # Q1 2022: 116 rejections because $0.30 floor too high at VIX > 30
                smoothed_vix = self._iv_sensor.get_smoothed_vix()
                effective_min_credit = config.CREDIT_SPREAD_MIN_CREDIT
                if smoothed_vix > config.CREDIT_SPREAD_HIGH_IV_VIX_THRESHOLD:
                    effective_min_credit = config.CREDIT_SPREAD_MIN_CREDIT_HIGH_IV

                short_candidates = []
                credit_pass_count = 0
                oi_pass_count = 0
                spread_pass_count = 0
                for p in puts:
                    if not (delta_min <= abs(p.delta) <= delta_max):
                        continue
                    if p.bid < effective_min_credit:
                        continue
                    credit_pass_count += 1
                    if p.open_interest < config.CREDIT_SPREAD_MIN_OPEN_INTEREST:
                        continue
                    oi_pass_count += 1
                    if p.spread_pct > config.CREDIT_SPREAD_MAX_SPREAD_PCT:
                        continue
                    spread_pass_count += 1
                    short_candidates.append(p)

                if short_candidates:
                    elastic_widen_used = widen
                    break

            self.log(
                f"SPREAD_FILTER: BullPut ShortLeg | Puts={len(puts)} | "
                f"Delta_pass={delta_pass_count} | Credit_pass={credit_pass_count} | "
                f"OI_pass={oi_pass_count} | Spread_pass={spread_pass_count} | "
                f"Delta_range={config.CREDIT_SPREAD_SHORT_LEG_DELTA_MIN}-"
                f"{config.CREDIT_SPREAD_SHORT_LEG_DELTA_MAX}"
                + (f" | Elastic_widen=±{elastic_widen_used:.2f}" if elastic_widen_used > 0 else "")
                + f" | Min_credit=${effective_min_credit}"
                + (
                    f" (IV-adaptive, VIX={smoothed_vix:.1f})"
                    if effective_min_credit != config.CREDIT_SPREAD_MIN_CREDIT
                    else ""
                )
            )
            if debug_stats is not None:
                debug_stats.update(
                    {
                        "delta_pass": delta_pass_count,
                        "credit_pass": credit_pass_count,
                        "oi_pass": oi_pass_count,
                        "spread_pass": spread_pass_count,
                        "elastic_widen": elastic_widen_used,
                        "min_credit": effective_min_credit,
                    }
                )

            if not short_candidates:
                self.log(
                    f"VASS: No short put candidates | "
                    f"Delta range: {config.CREDIT_SPREAD_SHORT_LEG_DELTA_MIN}-"
                    f"{config.CREDIT_SPREAD_SHORT_LEG_DELTA_MAX} | "
                    f"Elastic steps tried={len(config.ELASTIC_DELTA_STEPS)} | "
                    f"Min credit: ${config.CREDIT_SPREAD_MIN_CREDIT} | "
                    f"Min OI: {config.CREDIT_SPREAD_MIN_OPEN_INTEREST} | "
                    f"Max spread%: {config.CREDIT_SPREAD_MAX_SPREAD_PCT:.2f}"
                )
                if set_cooldown:
                    self._set_spread_failure_cooldown(current_time, direction=cooldown_key)
                return None

            # Sort by premium (highest first) - we want max credit
            short_candidates.sort(key=lambda x: x.bid, reverse=True)
            short_leg = short_candidates[0]

            # Long leg: $5 below short strike (for defined risk)
            target_long_strike = short_leg.strike - config.CREDIT_SPREAD_WIDTH_TARGET
            long_candidates = [
                p
                for p in puts
                if p.strike == target_long_strike
                and p.expiry == short_leg.expiry
                and p.ask > 0
                and p.open_interest >= max(1, config.CREDIT_SPREAD_MIN_OPEN_INTEREST // 2)
                and p.spread_pct <= config.CREDIT_SPREAD_LONG_LEG_MAX_SPREAD_PCT
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
                    and p.ask > 0
                    and p.open_interest >= max(1, config.CREDIT_SPREAD_MIN_OPEN_INTEREST // 2)
                    and p.spread_pct <= config.CREDIT_SPREAD_LONG_LEG_MAX_SPREAD_PCT
                ]
                if long_candidates:
                    long_candidates.sort(key=lambda x: x.strike, reverse=True)

            if not long_candidates:
                self.log(
                    f"VASS: No long put candidates for protection | "
                    f"Short strike={short_leg.strike} | Target=${target_long_strike} | "
                    f"Min OI={max(1, config.CREDIT_SPREAD_MIN_OPEN_INTEREST // 2)} | "
                    f"Max spread%={config.CREDIT_SPREAD_LONG_LEG_MAX_SPREAD_PCT:.2f}"
                )
                if set_cooldown:
                    self._set_spread_failure_cooldown(current_time, direction=cooldown_key)
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
                if set_cooldown:
                    self._set_spread_failure_cooldown(current_time, direction=cooldown_key)
                return None

            # Short leg: Delta 0.25-0.40 (OTM but decent premium)
            # V2.24.1: Elastic Delta Bands — progressively widen if no candidates
            short_candidates = []
            delta_pass_count = 0
            credit_pass_count = 0
            oi_pass_count = 0
            spread_pass_count = 0
            elastic_widen_used = 0.0

            for widen in config.ELASTIC_DELTA_STEPS:
                delta_min = max(
                    config.ELASTIC_DELTA_FLOOR,
                    config.CREDIT_SPREAD_SHORT_LEG_DELTA_MIN - widen,
                )
                delta_max = min(
                    config.ELASTIC_DELTA_CEILING,
                    config.CREDIT_SPREAD_SHORT_LEG_DELTA_MAX + widen,
                )

                delta_pass_count = sum(1 for c in calls if delta_min <= abs(c.delta) <= delta_max)
                # V2.25 Fix #3: IV-adaptive credit floor
                smoothed_vix = self._iv_sensor.get_smoothed_vix()
                effective_min_credit = config.CREDIT_SPREAD_MIN_CREDIT
                if smoothed_vix > config.CREDIT_SPREAD_HIGH_IV_VIX_THRESHOLD:
                    effective_min_credit = config.CREDIT_SPREAD_MIN_CREDIT_HIGH_IV

                short_candidates = []
                credit_pass_count = 0
                oi_pass_count = 0
                spread_pass_count = 0
                for c in calls:
                    if not (delta_min <= abs(c.delta) <= delta_max):
                        continue
                    if c.bid < effective_min_credit:
                        continue
                    credit_pass_count += 1
                    if c.open_interest < config.CREDIT_SPREAD_MIN_OPEN_INTEREST:
                        continue
                    oi_pass_count += 1
                    if c.spread_pct > config.CREDIT_SPREAD_MAX_SPREAD_PCT:
                        continue
                    spread_pass_count += 1
                    short_candidates.append(c)

                if short_candidates:
                    elastic_widen_used = widen
                    break

            self.log(
                f"SPREAD_FILTER: BearCall ShortLeg | Calls={len(calls)} | "
                f"Delta_pass={delta_pass_count} | Credit_pass={credit_pass_count} | "
                f"OI_pass={oi_pass_count} | Spread_pass={spread_pass_count} | "
                f"Delta_range={config.CREDIT_SPREAD_SHORT_LEG_DELTA_MIN}-"
                f"{config.CREDIT_SPREAD_SHORT_LEG_DELTA_MAX}"
                + (f" | Elastic_widen=±{elastic_widen_used:.2f}" if elastic_widen_used > 0 else "")
                + f" | Min_credit=${effective_min_credit}"
                + (
                    f" (IV-adaptive, VIX={smoothed_vix:.1f})"
                    if effective_min_credit != config.CREDIT_SPREAD_MIN_CREDIT
                    else ""
                )
            )
            if debug_stats is not None:
                debug_stats.update(
                    {
                        "delta_pass": delta_pass_count,
                        "credit_pass": credit_pass_count,
                        "oi_pass": oi_pass_count,
                        "spread_pass": spread_pass_count,
                        "elastic_widen": elastic_widen_used,
                        "min_credit": effective_min_credit,
                    }
                )

            if not short_candidates:
                self.log(
                    f"VASS: No short call candidates | "
                    f"Delta range: {config.CREDIT_SPREAD_SHORT_LEG_DELTA_MIN}-"
                    f"{config.CREDIT_SPREAD_SHORT_LEG_DELTA_MAX} | "
                    f"Elastic steps tried={len(config.ELASTIC_DELTA_STEPS)} | "
                    f"Min credit: ${config.CREDIT_SPREAD_MIN_CREDIT} | "
                    f"Min OI: {config.CREDIT_SPREAD_MIN_OPEN_INTEREST} | "
                    f"Max spread%: {config.CREDIT_SPREAD_MAX_SPREAD_PCT:.2f}"
                )
                if set_cooldown:
                    self._set_spread_failure_cooldown(current_time, direction=cooldown_key)
                return None

            short_candidates.sort(key=lambda x: x.bid, reverse=True)
            short_leg = short_candidates[0]

            # Long leg: $5 above short strike (for defined risk)
            target_long_strike = short_leg.strike + config.CREDIT_SPREAD_WIDTH_TARGET
            long_candidates = [
                c
                for c in calls
                if c.strike == target_long_strike
                and c.expiry == short_leg.expiry
                and c.ask > 0
                and c.open_interest >= max(1, config.CREDIT_SPREAD_MIN_OPEN_INTEREST // 2)
                and c.spread_pct <= config.CREDIT_SPREAD_LONG_LEG_MAX_SPREAD_PCT
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
                    and c.ask > 0
                    and c.open_interest >= max(1, config.CREDIT_SPREAD_MIN_OPEN_INTEREST // 2)
                    and c.spread_pct <= config.CREDIT_SPREAD_LONG_LEG_MAX_SPREAD_PCT
                ]
                if long_candidates:
                    long_candidates.sort(key=lambda x: x.strike)

            if not long_candidates:
                self.log(
                    f"VASS: No long call candidates for protection | "
                    f"Short strike={short_leg.strike} | Target=${target_long_strike} | "
                    f"Min OI={max(1, config.CREDIT_SPREAD_MIN_OPEN_INTEREST // 2)} | "
                    f"Max spread%={config.CREDIT_SPREAD_LONG_LEG_MAX_SPREAD_PCT:.2f}"
                )
                if set_cooldown:
                    self._set_spread_failure_cooldown(current_time, direction=cooldown_key)
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

    def select_credit_spread_legs_with_fallback(
        self,
        contracts: List[OptionContract],
        strategy: SpreadStrategy,
        dte_ranges: List[Tuple[int, int]],
        current_time: Optional[str] = None,
    ) -> Optional[Tuple[OptionContract, OptionContract]]:
        """
        V6.12: Try multiple DTE ranges for credit spreads before cooldown.
        """
        if not dte_ranges:
            return self.select_credit_spread_legs(
                contracts=contracts,
                strategy=strategy,
                dte_min=config.CREDIT_SPREAD_DTE_MIN,
                dte_max=config.CREDIT_SPREAD_DTE_MAX,
                current_time=current_time,
            )

        failure_stats = []
        for dte_min, dte_max in dte_ranges:
            stats: Dict[str, Any] = {}
            spread_legs = self.select_credit_spread_legs(
                contracts=contracts,
                strategy=strategy,
                dte_min=dte_min,
                dte_max=dte_max,
                current_time=current_time,
                set_cooldown=False,
                log_filters=False,
                debug_stats=stats,
            )
            if spread_legs is not None:
                self.log(
                    f"VASS: Credit fallback DTE used | Range={dte_min}-{dte_max} | "
                    f"Strategy={strategy.value}"
                )
                return spread_legs
            if stats:
                failure_stats.append(stats)

        cooldown_key = strategy.value if hasattr(strategy, "value") else str(strategy)
        self._set_spread_failure_cooldown(current_time, direction=cooldown_key)
        if failure_stats:
            summary = "; ".join(
                [
                    f"{s.get('dte_range')}|DTE={s.get('dte_pass')}|"
                    f"Delta={s.get('delta_pass')}|Credit={s.get('credit_pass')}|"
                    f"OI={s.get('oi_pass')}|Spread={s.get('spread_pass')}|"
                    f"Widen={s.get('elastic_widen')}|MinCred={s.get('min_credit')}"
                    for s in failure_stats
                ]
            )
            self._last_credit_failure_stats = summary
        return None

    def pop_last_spread_failure_stats(self) -> Optional[str]:
        stats = self._last_spread_failure_stats
        self._last_spread_failure_stats = None
        return stats

    def pop_last_credit_failure_stats(self) -> Optional[str]:
        stats = self._last_credit_failure_stats
        self._last_credit_failure_stats = None
        return stats

    def set_last_entry_validation_failure(self, reason: Optional[str]) -> None:
        self._last_entry_validation_failure = reason

    def pop_last_entry_validation_failure(self) -> Optional[str]:
        reason = self._last_entry_validation_failure
        self._last_entry_validation_failure = None
        return reason

    def set_last_intraday_validation_failure(
        self, reason: Optional[str], detail: Optional[str] = None
    ) -> None:
        self._last_intraday_validation_failure = reason
        self._last_intraday_validation_detail = detail

    def pop_last_intraday_validation_failure(self) -> Tuple[Optional[str], Optional[str]]:
        reason = self._last_intraday_validation_failure
        detail = self._last_intraday_validation_detail
        self._last_intraday_validation_failure = None
        self._last_intraday_validation_detail = None
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
    ) -> Optional[TargetWeight]:
        """
        V2.3: Check for debit spread entry signal.

        Debit Spreads have defined risk (max loss = net debit).
        V6.0: Direction is now passed in from conviction resolution (VASS/MICRO).
        Caller (main.py) resolves VASS conviction vs macro before calling.

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
            direction: V6.0: Direction from conviction resolution (CALL or PUT).
            ma50_value: Optional 50-day moving average used for bullish debit
                trend blocking during bear transitions.

        Returns:
            TargetWeight for spread entry (with short leg in metadata), or None.
        """

        def fail(reason: str) -> Optional[TargetWeight]:
            self.set_last_entry_validation_failure(reason)
            return None

        def fail_quality(detail: str) -> Optional[TargetWeight]:
            self.set_last_entry_validation_failure(f"R_CONTRACT_QUALITY:{detail}")
            return None

        # Reset previous validation reason for this attempt
        self.set_last_entry_validation_failure(None)

        # V2.8: Update IV sensor with current VIX (for smoothing)
        self._iv_sensor.update(vix_current)

        overlay_state = self.get_regime_overlay_state(
            vix_current=vix_current, regime_score=regime_score
        )
        can_swing, swing_reason = self.can_enter_swing(
            direction=direction, overlay_state=overlay_state
        )
        if not can_swing:
            return fail(swing_reason)

        day_gap_reason = self._check_vass_direction_day_gap(direction, current_date)
        if day_gap_reason is not None:
            return fail(day_gap_reason)

        if bool(getattr(config, "VASS_LOSS_BREAKER_ENABLED", False)):
            pause_until = self._vass_loss_breaker_pause_until
            if pause_until:
                try:
                    trade_date = datetime.strptime(str(current_date)[:10], "%Y-%m-%d").date()
                    pause_date = datetime.strptime(str(pause_until)[:10], "%Y-%m-%d").date()
                    if trade_date <= pause_date:
                        return fail("R_VASS_LOSS_BREAKER_PAUSE")
                    self._vass_loss_breaker_pause_until = None
                except Exception:
                    self._vass_loss_breaker_pause_until = None

        # Scoped daily attempt budget (per spread key), replaces global one-attempt lock.
        attempt_key = f"DEBIT_{direction.value if direction is not None else 'NONE'}"
        if not self._can_attempt_spread_entry(attempt_key):
            return fail("R_COOLDOWN_DIRECTIONAL")

        # V2.27/O-20: Win Rate Gate - block/scale or monitor-only mode.
        win_rate_scale = self.get_win_rate_scale()
        gate_mode = str(getattr(config, "WIN_RATE_GATE_VASS_EXECUTION_MODE", "enforce")).lower()
        if gate_mode == "monitor_only":
            self.log(
                f"WIN_RATE_GATE: MONITOR_ONLY | RawScale={win_rate_scale:.0%} | "
                f"Shutoff={self._win_rate_shutoff} | History={self._spread_result_history}",
                trades_only=True,
            )
            win_rate_scale = 1.0
        elif win_rate_scale == 0.0:
            if getattr(config, "VASS_WIN_RATE_HARD_BLOCK", True):
                self.log(
                    f"WIN_RATE_GATE: BLOCKED | Shutoff active | "
                    f"History={self._spread_result_history}",
                    trades_only=True,
                )
                return fail("WIN_RATE_GATE_BLOCK")
            win_rate_scale = float(
                getattr(config, "VASS_WIN_RATE_SHUTOFF_SCALE", config.WIN_RATE_SIZING_MINIMUM)
            )
            self.log(
                f"WIN_RATE_GATE: SHUTOFF_OVERRIDE | Applying minimum scale {win_rate_scale:.0%} | "
                f"History={self._spread_result_history}",
                trades_only=True,
            )

        # V6.10 P4: Margin Pre-Check BEFORE Signal Approval
        # Check if we have sufficient margin for at least 1 spread before proceeding
        margin_check_enabled = getattr(config, "MARGIN_CHECK_BEFORE_SIGNAL", False)
        if margin_check_enabled and margin_remaining is not None:
            spread_width = getattr(config, "SPREAD_WIDTH_TARGET", 5.0)
            min_spreads = getattr(config, "MARGIN_PRE_CHECK_MIN_SPREADS", 1)
            buffer_pct = getattr(config, "MARGIN_PRE_CHECK_BUFFER", 0.15)

            # Margin required = width × 100 × num_spreads × (1 + buffer)
            per_contract_margin = self.estimate_spread_margin_per_contract(
                spread_width=spread_width,
                spread_type="DEBIT",
            )
            min_margin_required = per_contract_margin * min_spreads * (1 + buffer_pct)

            if margin_remaining < min_margin_required:
                self.log(
                    f"MARGIN_PRE_CHECK: BLOCKED | Available=${margin_remaining:,.0f} | "
                    f"Required=${min_margin_required:,.0f} (width=${spread_width} × {min_spreads} × {1+buffer_pct:.0%})",
                    trades_only=True,
                )
                return fail("R_MARGIN_PRECHECK")

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
                    return fail("R_COOLDOWN_DIRECTIONAL")
                else:
                    # Cooldown expired, clear the tracking
                    self._last_spread_exit_time = None
            except (ValueError, TypeError):
                # If parsing fails, clear the tracking and proceed
                self._last_spread_exit_time = None

        # V2.9: Check trade limits (Bug #4 fix) - Uses comprehensive counter
        if not self._can_trade_options(OptionsMode.SWING):
            return fail("TRADE_LIMIT_BLOCK")

        # Determine spread direction based on regime
        if regime_score < config.SPREAD_REGIME_CRISIS:
            # Regime < 30: Crisis mode - no spreads, protective puts only
            self.log(
                f"SPREAD: No entry - regime {regime_score:.1f} < {config.SPREAD_REGIME_CRISIS} (crisis mode)"
            )
            return fail("REGIME_CRISIS_BLOCK")

        # V6.0: Direction now passed in from conviction resolution
        # Caller (main.py) has already resolved VASS conviction vs macro direction
        if direction is None:
            self.log("SPREAD: No entry - direction not provided (conviction resolution required)")
            return fail("DIRECTION_MISSING")

        # Derive spread type and VIX max from direction
        if direction == OptionDirection.CALL:
            spread_type = "BULL_CALL"
            vix_max = config.SPREAD_VIX_MAX_BULL
        else:
            spread_type = "BEAR_PUT"
            vix_max = config.SPREAD_VIX_MAX_BEAR

        # V9.7: Block BEAR_PUT_DEBIT in RISK_ON — 12.5% WR in 2017 (regime was 88% RISK_ON)
        bear_put_risk_on_max = float(getattr(config, "VASS_BEAR_PUT_REGIME_MAX", 0))
        if (
            bear_put_risk_on_max > 0
            and spread_type == "BEAR_PUT"
            and regime_score >= bear_put_risk_on_max
        ):
            self.log(
                f"SPREAD: BEAR_PUT blocked in RISK_ON | "
                f"Regime={regime_score:.1f} >= {bear_put_risk_on_max:.0f}"
            )
            return fail("R_BEAR_PUT_RISK_ON_BLOCK")

        # V9.4 F4: Require minimum regime for BULL spread entries
        bull_regime_min = float(getattr(config, "VASS_BULL_SPREAD_REGIME_MIN", 0))
        if bull_regime_min > 0 and spread_type == "BULL_CALL" and regime_score < bull_regime_min:
            self.log(
                f"SPREAD: BULL_CALL blocked by regime floor | "
                f"Regime={regime_score:.1f} < {bull_regime_min:.0f}"
            )
            return fail("R_BULL_REGIME_FLOOR")

        # V9.4 F5: Block BULL spreads when QQQ is below 20MA (trend confirmation)
        if (
            getattr(config, "VASS_BULL_MA20_GATE_ENABLED", False)
            and spread_type == "BULL_CALL"
            and self.algorithm is not None
        ):
            qqq_sma20 = getattr(self.algorithm, "qqq_sma20", None)
            if qqq_sma20 is not None and getattr(qqq_sma20, "IsReady", False):
                sma20_value = float(qqq_sma20.Current.Value)
                if current_price < sma20_value:
                    self.log(
                        f"SPREAD: BULL_CALL blocked by MA20 gate | "
                        f"QQQ={current_price:.2f} < MA20={sma20_value:.2f}"
                    )
                    return fail("R_BULL_MA20_GATE")

        # Bear hardening: block bullish debit spreads when short-term trend is down.
        if (
            spread_type == "BULL_CALL"
            and bool(getattr(config, "VASS_BULL_CALL_MA50_BLOCK_ENABLED", True))
            and ma50_value > 0
            and current_price < ma50_value
            and regime_score < float(getattr(config, "VASS_BULL_CALL_MA50_BLOCK_REGIME_MAX", 60.0))
        ):
            self.log(
                f"SPREAD: BULL_CALL blocked by MA50 trend gate | "
                f"QQQ={current_price:.2f} < MA50={ma50_value:.2f} | "
                f"Regime={regime_score:.1f}"
            )
            return fail("E_BULL_CALL_MA50_REGIME_BLOCK")

        # V6.19: Stress override for BULL_CALL_DEBIT to mitigate regime lag during corrections.
        # Rule:
        # - Hard block when VIX is already elevated, or when VIX is elevated + accelerating.
        # - In early-stress zone, keep participation but reduce size to preserve optionality.
        if spread_type == "BULL_CALL":
            if overlay_state == "STRESS":
                self.log(
                    f"SPREAD: BULL_CALL blocked by overlay | "
                    f"Overlay={overlay_state} | VIX={vix_current:.1f} | Regime={regime_score:.1f}"
                )
                return fail("E_OVERLAY_STRESS_BULL_BLOCK")
            vix_5d_change = (
                self._iv_sensor.get_vix_5d_change()
                if self._iv_sensor.is_conviction_ready()
                else None
            )
            hard_vix = float(getattr(config, "BULL_CALL_STRESS_BLOCK_VIX", 22.0))
            accel_vix = float(getattr(config, "BULL_CALL_STRESS_ACCEL_VIX", 18.0))
            accel_5d = float(getattr(config, "BULL_CALL_STRESS_ACCEL_5D", 0.20))
            early_low = float(getattr(config, "BULL_CALL_EARLY_STRESS_VIX_LOW", 16.0))
            early_high = float(getattr(config, "BULL_CALL_EARLY_STRESS_VIX_HIGH", 18.0))
            early_size = float(getattr(config, "BULL_CALL_EARLY_STRESS_SIZE", 0.50))

            hard_block = vix_current >= hard_vix
            accel_block = (
                vix_current >= accel_vix and vix_5d_change is not None and vix_5d_change >= accel_5d
            )
            if hard_block or accel_block:
                reason = (
                    f"VIX={vix_current:.1f} >= {hard_vix:.1f}"
                    if hard_block
                    else f"VIX={vix_current:.1f} >= {accel_vix:.1f} and VIX5d={vix_5d_change:+.1%} >= {accel_5d:.1%}"
                )
                self.log(f"SPREAD: BULL_CALL stress blocked | {reason}")
                return fail("BULL_CALL_STRESS_BLOCK")

            if early_low <= vix_current < early_high:
                adjusted = min(size_multiplier, early_size)
                if adjusted < size_multiplier:
                    self.log(
                        f"SPREAD: BULL_CALL early-stress size reduction | "
                        f"VIX={vix_current:.1f} in [{early_low:.1f},{early_high:.1f}) | "
                        f"Size {size_multiplier:.0%}->{adjusted:.0%}"
                    )
                    size_multiplier = adjusted

        # V6.4: Pre-entry assignment risk gate for BEAR_PUT spreads
        # Block entry if short PUT strike is too close to ATM or ITM
        if (
            spread_type == "BEAR_PUT"
            and config.BEAR_PUT_ENTRY_GATE_ENABLED
            and short_leg_contract is not None
            and current_price > 0
        ):
            min_otm_pct = float(getattr(config, "BEAR_PUT_ENTRY_MIN_OTM_PCT", 0.03))
            stress_otm_pct = float(
                getattr(config, "BEAR_PUT_ENTRY_MIN_OTM_PCT_STRESS", min_otm_pct)
            )
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
            short_strike = short_leg_contract.strike
            # For PUTs: OTM when strike < price, ITM when strike > price
            # Calculate how far OTM the short strike is (negative = ITM)
            otm_pct = (current_price - short_strike) / current_price
            if otm_pct < min_otm_pct:
                self.log(
                    f"SPREAD: Entry blocked - BEAR_PUT assignment risk | "
                    f"Short strike {short_strike:.0f} is {otm_pct:.1%} OTM "
                    f"(min {min_otm_pct:.1%}) | "
                    f"QQQ={current_price:.2f}"
                )
                return fail(f"BEAR_PUT_ASSIGNMENT_GATE_{gate_profile}")

        # VIX filter
        if vix_current > vix_max:
            self.log(f"SPREAD: No entry - VIX {vix_current:.1f} > max {vix_max} for {spread_type}")
            return fail("VIX_MAX_BLOCK")

        # V6.14 OPT: Avoid long PUT debit spreads at panic highs and reduce size in elevated fear.
        if spread_type == "BEAR_PUT":
            put_entry_vix_max = getattr(config, "PUT_ENTRY_VIX_MAX", 36.0)
            if vix_current > put_entry_vix_max:
                self.log(
                    f"SPREAD: BEAR_PUT blocked - VIX {vix_current:.1f} > max {put_entry_vix_max:.1f}"
                )
                return fail("PUT_ENTRY_VIX_MAX_BLOCK")
            put_reduce_start = getattr(config, "PUT_SIZE_REDUCTION_VIX_START", 30.0)
            put_reduce_factor = getattr(config, "PUT_SIZE_REDUCTION_FACTOR", 0.50)
            if vix_current >= put_reduce_start:
                size_multiplier *= put_reduce_factor
                self.log(
                    f"SPREAD: BEAR_PUT size reduced in high VIX | "
                    f"VIX={vix_current:.1f} >= {put_reduce_start:.1f} | "
                    f"Multiplier={size_multiplier:.2f}",
                    trades_only=True,
                )

        # Check safeguards
        if gap_filter_triggered:
            self.log("SPREAD: Entry blocked - gap filter active")
            return fail("GAP_FILTER_BLOCK")

        if vol_shock_active:
            self.log("SPREAD: Entry blocked - vol shock active")
            return fail("VOL_SHOCK_BLOCK")

        # Check time window (10:00 AM - 2:30 PM ET)
        # V3.0: EOD scan at 15:45 bypasses time window — chain is valid at EOD
        time_minutes = current_hour * 60 + current_minute
        if not is_eod_scan and not (10 * 60 <= time_minutes <= 14 * 60 + 30):
            if not self._swing_time_warning_logged:
                self.log("SPREAD: Entry blocked - outside time window (10:00-14:30)")
                self._swing_time_warning_logged = True
            return fail("TIME_WINDOW_BLOCK")

        # Validate contracts
        if long_leg_contract is None or short_leg_contract is None:
            self.log("SPREAD: Entry blocked - missing contract legs")
            return fail_quality("MISSING_SPREAD_LEGS")

        now_dt = self._parse_dt(current_date, current_hour, current_minute)
        signature = self._build_vass_signature(
            spread_type=spread_type,
            direction=direction,
            long_leg_contract=long_leg_contract,
        )
        expiry_bucket = str(getattr(long_leg_contract, "expiry", "") or "").strip()
        if not expiry_bucket:
            expiry_bucket = f"DTE:{int(getattr(long_leg_contract, 'days_to_expiry', -1))}"
        expiry_block_reason = self._check_expiry_concentration_cap(
            expiry_bucket=expiry_bucket,
            direction=direction,
            regime_score=regime_score,
            vix_current=vix_current,
        )
        if expiry_block_reason:
            return fail(expiry_block_reason)
        similar_block_reason = self._check_vass_similar_entry_guard(signature, now_dt)
        if similar_block_reason:
            return fail(similar_block_reason)

        # Validate contract directions match spread type
        if long_leg_contract.direction != direction:
            self.log(
                f"SPREAD: Entry blocked - long leg direction {long_leg_contract.direction.value} "
                f"doesn't match spread type {spread_type}"
            )
            return fail_quality("LONG_LEG_DIRECTION_MISMATCH")

        if short_leg_contract.direction != direction:
            self.log(
                f"SPREAD: Entry blocked - short leg direction {short_leg_contract.direction.value} "
                f"doesn't match spread type {spread_type}"
            )
            return fail_quality("SHORT_LEG_DIRECTION_MISMATCH")

        # Validate DTE range — use VASS-aware bounds if provided
        effective_dte_min = dte_min if dte_min is not None else config.SPREAD_DTE_MIN
        effective_dte_max = dte_max if dte_max is not None else config.SPREAD_DTE_MAX
        if long_leg_contract.days_to_expiry < effective_dte_min:
            self.log(
                f"SPREAD: Entry blocked - DTE {long_leg_contract.days_to_expiry} < "
                f"min {effective_dte_min}"
            )
            return fail_quality("DTE_BELOW_MIN")

        if long_leg_contract.days_to_expiry > effective_dte_max:
            self.log(
                f"SPREAD: Entry blocked - DTE {long_leg_contract.days_to_expiry} > "
                f"max {effective_dte_max}"
            )
            return fail_quality("DTE_ABOVE_MAX")

        # V2.6 Bug #4: Validate short leg DTE matches long leg (within 1 day tolerance)
        dte_diff = abs(long_leg_contract.days_to_expiry - short_leg_contract.days_to_expiry)
        if dte_diff > 1:
            self.log(
                f"SPREAD: Entry blocked - DTE mismatch | "
                f"Long={long_leg_contract.days_to_expiry} Short={short_leg_contract.days_to_expiry} | "
                f"Diff={dte_diff} > 1 day"
            )
            return fail_quality("DTE_LONG_SHORT_MISMATCH")

        # V2.6 Bug #9: Re-validate delta bounds before entry (delta can drift after selection)
        # This is a defensive check - legs were already filtered during selection
        long_delta_abs = abs(long_leg_contract.delta) if long_leg_contract.delta else 0
        short_delta_abs = abs(short_leg_contract.delta) if short_leg_contract.delta else 0

        # Direction-specific delta + liquidity thresholds (PUTs are looser)
        if spread_type == "BEAR_PUT":
            long_delta_min = config.SPREAD_LONG_LEG_DELTA_MIN_PUT
            long_delta_max = config.SPREAD_LONG_LEG_DELTA_MAX_PUT
            short_delta_min = config.SPREAD_SHORT_LEG_DELTA_MIN_PUT
            short_delta_max = config.SPREAD_SHORT_LEG_DELTA_MAX_PUT
            min_oi = config.OPTIONS_MIN_OPEN_INTEREST_PUT
            spread_max = config.OPTIONS_SPREAD_MAX_PCT_PUT
            spread_warn = config.OPTIONS_SPREAD_WARNING_PCT_PUT
        else:
            long_delta_min = config.SPREAD_LONG_LEG_DELTA_MIN
            long_delta_max = config.SPREAD_LONG_LEG_DELTA_MAX
            short_delta_min = config.SPREAD_SHORT_LEG_DELTA_MIN
            short_delta_max = config.SPREAD_SHORT_LEG_DELTA_MAX
            min_oi = config.OPTIONS_MIN_OPEN_INTEREST
            spread_max = config.OPTIONS_SPREAD_MAX_PCT
            spread_warn = config.OPTIONS_SPREAD_WARNING_PCT

        if long_delta_abs < long_delta_min:
            self.log(
                f"SPREAD: Entry blocked - long leg delta drift | "
                f"Delta={long_delta_abs:.2f} < min {long_delta_min}"
            )
            return fail_quality("LONG_DELTA_BELOW_MIN")

        if long_delta_abs > long_delta_max:
            self.log(
                f"SPREAD: Entry blocked - long leg delta drift | "
                f"Delta={long_delta_abs:.2f} > max {long_delta_max}"
            )
            return fail_quality("LONG_DELTA_ABOVE_MAX")

        # Short leg delta validation (only if not using width-based selection)
        if not config.SPREAD_SHORT_LEG_BY_WIDTH:
            if short_delta_abs < short_delta_min:
                self.log(
                    f"SPREAD: Entry blocked - short leg delta drift | "
                    f"Delta={short_delta_abs:.2f} < min {short_delta_min}"
                )
                return fail_quality("SHORT_DELTA_BELOW_MIN")

            if short_delta_abs > short_delta_max:
                self.log(
                    f"SPREAD: Entry blocked - short leg delta drift | "
                    f"Delta={short_delta_abs:.2f} > max {short_delta_max}"
                )
                return fail_quality("SHORT_DELTA_ABOVE_MAX")

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
            min_open_interest=min_oi,
            spread_max_pct=spread_max,
            spread_warn_pct=spread_warn,
        )

        if not entry_score.is_valid:
            self.log(
                f"SPREAD: Entry blocked - score {entry_score.total:.2f} < "
                f"{config.OPTIONS_ENTRY_SCORE_MIN}"
            )
            return fail_quality("ENTRY_SCORE_BELOW_MIN")

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
            return fail_quality("NET_DEBIT_NON_POSITIVE")

        max_profit = width - net_debit
        if max_profit <= 0:
            self.log(f"SPREAD: Entry blocked - max profit ${max_profit:.2f} <= 0")
            return fail_quality("MAX_PROFIT_NON_POSITIVE")

        # V10.7: Debit-to-width quality band — reject both too-expensive and too-cheap structures.
        legacy_max_debit_pct = float(getattr(config, "SPREAD_MAX_DEBIT_TO_WIDTH_PCT", 0.38))
        min_debit_pct = float(getattr(config, "SPREAD_MIN_DEBIT_TO_WIDTH_PCT", 0.28))
        low_vix_cut = float(getattr(config, "SPREAD_DW_LOW_VIX_MAX", 18.0))
        high_vix_cut = float(getattr(config, "SPREAD_DW_HIGH_VIX_MIN", 25.0))
        max_low = float(getattr(config, "SPREAD_MAX_DEBIT_TO_WIDTH_PCT_LOW_VIX", 0.48))
        max_med = float(getattr(config, "SPREAD_MAX_DEBIT_TO_WIDTH_PCT_MED_VIX", 0.44))
        max_high = float(getattr(config, "SPREAD_MAX_DEBIT_TO_WIDTH_PCT_HIGH_VIX", 0.40))
        if vix_current is None:
            max_debit_pct = legacy_max_debit_pct
        elif vix_current < low_vix_cut:
            max_debit_pct = max_low
        elif vix_current < high_vix_cut:
            max_debit_pct = max_med
        else:
            max_debit_pct = max_high

        debit_to_width = net_debit / width if width > 0 else 1.0
        if debit_to_width > max_debit_pct:
            self.log(
                f"SPREAD: Entry blocked - DEBIT_TO_WIDTH {debit_to_width:.1%} > {max_debit_pct:.0%} | "
                f"Debit=${net_debit:.2f} Width=${width:.0f}",
                trades_only=True,
            )
            return fail_quality("DEBIT_TO_WIDTH_TOO_HIGH")
        if debit_to_width < min_debit_pct:
            self.log(
                f"SPREAD: Entry blocked - DEBIT_TO_WIDTH {debit_to_width:.1%} < {min_debit_pct:.0%} | "
                f"Debit=${net_debit:.2f} Width=${width:.0f}",
                trades_only=True,
            )
            return fail_quality("DEBIT_TO_WIDTH_TOO_LOW")

        if bool(getattr(config, "SPREAD_ENTRY_COMMISSION_GATE_ENABLED", False)):
            max_profit_dollars = max_profit * 100.0
            commission_per_spread = float(getattr(config, "SPREAD_COMMISSION_PER_CONTRACT", 2.60))
            ratio_limit = float(getattr(config, "SPREAD_MAX_COMMISSION_TO_MAX_PROFIT_RATIO", 0.15))
            fee_to_profit_ratio = (
                commission_per_spread / max_profit_dollars if max_profit_dollars > 0 else 1.0
            )
            if fee_to_profit_ratio > ratio_limit:
                self.log(
                    f"SPREAD: Entry blocked - COMMISSION_TO_MAX_PROFIT {fee_to_profit_ratio:.1%} "
                    f"> {ratio_limit:.0%} | MaxProfit=${max_profit_dollars:.2f} "
                    f"Commission=${commission_per_spread:.2f}",
                    trades_only=True,
                )
                return fail_quality("COMMISSION_TO_MAX_PROFIT_TOO_HIGH")

        # V2.18: Use sizing cap (Fix for MarginBuyingPower sizing bug)
        # Evidence: Architect found $14K trade vs $5K expected when using allocation-based sizing
        # V3.0 SCALABILITY FIX: Use percentage-based cap instead of hardcoded dollars
        # At $50K: 15% = $7,500, at $200K: 15% = $30,000 (scales with portfolio)
        portfolio_value = self.algorithm.Portfolio.TotalPortfolioValue if self.algorithm else 50000
        swing_max_pct = getattr(
            config, "VASS_RISK_PER_TRADE_PCT", getattr(config, "SWING_SPREAD_MAX_PCT", 0.15)
        )
        swing_max_dollars = portfolio_value * swing_max_pct
        # V2.14: Use conservative net debit for sizing (prevents tier cap violations)
        cost_per_spread = net_debit_for_sizing * 100  # 100 shares per contract
        num_spreads = int(swing_max_dollars / cost_per_spread)
        self.log(
            f"SIZING: SWING | Cap=${swing_max_dollars:,.0f} ({swing_max_pct:.0%} of ${portfolio_value:,.0f}) | "
            f"Cost/spread=${cost_per_spread:.2f} | Qty={num_spreads}"
        )

        # V2.27: Apply win rate gate scaling to contract count
        if win_rate_scale < 1.0:
            scaled = max(1, int(num_spreads * win_rate_scale))
            self.log(
                f"WIN_RATE_GATE: REDUCED | Scale={win_rate_scale:.0%} | "
                f"{num_spreads} -> {scaled} spreads",
                trades_only=True,
            )
            num_spreads = scaled

        # V6.0: Apply cold start multiplier (macro gate removed - conviction handles direction)
        if size_multiplier < 1.0:
            min_size = getattr(config, "OPTIONS_MIN_COMBINED_SIZE_PCT", 0.10)
            if size_multiplier < min_size:
                self.log(
                    f"SPREAD: Entry blocked - cold start size {size_multiplier:.0%} < min {min_size:.0%}"
                )
                return None

            scaled = max(1, int(num_spreads * size_multiplier))
            self.log(
                f"SPREAD: Sizing reduced | {num_spreads} -> {scaled} spreads | "
                f"SizeMult={size_multiplier:.0%}",
                trades_only=True,
            )
            num_spreads = scaled

        # V6.10 P5: Choppy market size reduction
        choppy_scale = self.get_choppy_market_scale()
        if choppy_scale < 1.0 and num_spreads > 1:
            choppy_adjusted = max(1, int(num_spreads * choppy_scale))
            self.log(
                f"SPREAD: Choppy market reduction | {num_spreads} -> {choppy_adjusted} spreads | "
                f"ChoppyScale={choppy_scale:.0%}",
                trades_only=True,
            )
            num_spreads = choppy_adjusted

        # V2.21 Layer 1: Pre-submission margin estimation
        # Scale num_spreads down to fit within available margin
        if margin_remaining is not None and margin_remaining > 0 and width > 0:
            safety_factor = getattr(config, "SPREAD_MARGIN_SAFETY_FACTOR", 0.80)
            usable_margin = self.get_usable_margin(margin_remaining)
            if self._rejection_margin_cap is not None:
                self.log(
                    f"SIZING: Rejection cap active | Cap=${self._rejection_margin_cap:,.0f} | "
                    f"Usable=${usable_margin:,.0f}",
                    trades_only=True,
                )

            estimated_margin_per_spread = self.estimate_spread_margin_per_contract(
                spread_width=width,
                spread_type=spread_type,
            )
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
                f"MARGIN_SCALE_BELOW_MIN_CONTRACTS",
                trades_only=True,
            )
            return fail("MARGIN_SCALE_BELOW_MIN_CONTRACTS")  # Preserve explicit reason

        if num_spreads <= 0:
            self.log(
                f"SPREAD: Entry blocked - cap ${swing_max_dollars} too small "
                f"for debit ${net_debit:.2f}"
            )
            return fail("NUM_SPREADS_NON_POSITIVE")

        # V2.12 Fix #3: Enforce SPREAD_MAX_CONTRACTS hard cap
        # Evidence from V2.11: Position accumulated to 80 contracts (5× intended)
        # This cap prevents runaway position accumulation from exit signal bugs
        hard_cap = int(
            getattr(config, "SPREAD_MAX_CONTRACTS_HARD_CAP", config.SPREAD_MAX_CONTRACTS)
        )
        if num_spreads > hard_cap:
            self.log(f"SPREAD_LIMIT: Capped contracts | Requested={num_spreads} > Max={hard_cap}")
            num_spreads = hard_cap

        # V6.0 Fix: Assignment-aware sizing using spread width
        # Max loss on spread = width * 100 * contracts (NOT underlying * 100 * contracts)
        if self.algorithm:
            portfolio_value = self.algorithm.Portfolio.TotalPortfolioValue
        else:
            portfolio_value = 50000  # Default for testing

        assignment_multiplier = self.get_assignment_aware_size_multiplier(
            spread_width=width,  # V6.0: Use spread width, not underlying price
            portfolio_value=portfolio_value,
            requested_contracts=num_spreads,
        )
        if assignment_multiplier < 1.0:
            adjusted_contracts = max(1, int(num_spreads * assignment_multiplier))
            if adjusted_contracts < num_spreads:
                num_spreads = adjusted_contracts

        # Store pending spread entry details
        self._pending_spread_long_leg = long_leg_contract
        self._pending_spread_short_leg = short_leg_contract
        self._pending_spread_type = spread_type
        self._pending_net_debit = net_debit
        self._pending_max_profit = max_profit
        self._pending_spread_width = width
        self._pending_num_contracts = num_spreads
        self._pending_entry_score = entry_score.total

        # Record attempt for this spread key (successful signal creation).
        self._record_spread_entry_attempt(attempt_key)

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
            symbol=self._symbol_str(long_leg_contract.symbol),
            target_weight=config.OPTIONS_SWING_ALLOCATION,  # V2.4.1: Actual allocation
            source="OPT",
            urgency=Urgency.IMMEDIATE,
            reason=reason,
            requested_quantity=num_spreads,
            metadata={
                "spread_type": spread_type,
                "spread_short_leg_symbol": self._symbol_str(short_leg_contract.symbol),
                "spread_short_leg_quantity": num_spreads,
                "vass_signature_key": signature,
                "spread_net_debit": net_debit,
                "spread_cost_or_credit": net_debit,
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
        is_eod_scan: bool = False,
        direction: Optional[OptionDirection] = None,
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
            direction: V6.0: Direction from conviction resolution (CALL or PUT).

        Returns:
            TargetWeight for credit spread entry, or None.
        """

        def fail(reason: str) -> Optional[TargetWeight]:
            self.set_last_entry_validation_failure(reason)
            return None

        def fail_quality(detail: str) -> Optional[TargetWeight]:
            self.set_last_entry_validation_failure(f"R_CONTRACT_QUALITY:{detail}")
            return None

        # Reset previous validation reason for this attempt
        self.set_last_entry_validation_failure(None)

        # V2.8: Update IV sensor with current VIX (for smoothing)
        self._iv_sensor.update(vix_current)

        overlay_state = self.get_regime_overlay_state(
            vix_current=vix_current, regime_score=regime_score
        )
        can_swing, swing_reason = self.can_enter_swing(
            direction=direction, overlay_state=overlay_state
        )
        if not can_swing:
            return fail(swing_reason)

        day_gap_reason = self._check_vass_direction_day_gap(direction, current_date)
        if day_gap_reason is not None:
            return fail(day_gap_reason)

        if bool(getattr(config, "VASS_LOSS_BREAKER_ENABLED", False)):
            pause_until = self._vass_loss_breaker_pause_until
            if pause_until:
                try:
                    trade_date = datetime.strptime(str(current_date)[:10], "%Y-%m-%d").date()
                    pause_date = datetime.strptime(str(pause_until)[:10], "%Y-%m-%d").date()
                    if trade_date <= pause_date:
                        return fail("R_VASS_LOSS_BREAKER_PAUSE")
                    self._vass_loss_breaker_pause_until = None
                except Exception:
                    self._vass_loss_breaker_pause_until = None

        # Scoped daily attempt budget (strategy-specific), replaces global one-attempt lock.
        attempt_key = f"CREDIT_{strategy.value if strategy is not None else 'NONE'}"
        if not self._can_attempt_spread_entry(attempt_key):
            return fail("R_COOLDOWN_DIRECTIONAL")

        # V2.27/O-20: Win Rate Gate - block/scale or monitor-only mode.
        win_rate_scale = self.get_win_rate_scale()
        gate_mode = str(getattr(config, "WIN_RATE_GATE_VASS_EXECUTION_MODE", "enforce")).lower()
        if gate_mode == "monitor_only":
            self.log(
                f"WIN_RATE_GATE: CREDIT MONITOR_ONLY | RawScale={win_rate_scale:.0%} | "
                f"Shutoff={self._win_rate_shutoff} | History={self._spread_result_history}",
                trades_only=True,
            )
            win_rate_scale = 1.0
        elif win_rate_scale == 0.0:
            if getattr(config, "VASS_WIN_RATE_HARD_BLOCK", True):
                self.log(
                    f"WIN_RATE_GATE: CREDIT BLOCKED | Shutoff active | "
                    f"History={self._spread_result_history}",
                    trades_only=True,
                )
                return fail("WIN_RATE_GATE_BLOCK")
            win_rate_scale = float(
                getattr(config, "VASS_WIN_RATE_SHUTOFF_SCALE", config.WIN_RATE_SIZING_MINIMUM)
            )
            self.log(
                f"WIN_RATE_GATE: CREDIT SHUTOFF_OVERRIDE | Applying minimum scale {win_rate_scale:.0%} | "
                f"History={self._spread_result_history}",
                trades_only=True,
            )

        # V6.10 P4: Margin Pre-Check BEFORE Signal Approval
        # Check if we have sufficient margin for at least 1 spread before proceeding
        margin_check_enabled = getattr(config, "MARGIN_CHECK_BEFORE_SIGNAL", False)
        if margin_check_enabled and margin_remaining is not None:
            # Credit spreads use CREDIT_SPREAD_WIDTH_TARGET
            spread_width = getattr(config, "CREDIT_SPREAD_WIDTH_TARGET", 5.0)
            min_spreads = getattr(config, "MARGIN_PRE_CHECK_MIN_SPREADS", 1)
            buffer_pct = getattr(config, "MARGIN_PRE_CHECK_BUFFER", 0.15)

            # Margin required = width × 100 × num_spreads × (1 + buffer)
            per_contract_margin = self.estimate_spread_margin_per_contract(
                spread_width=spread_width,
                spread_type="CREDIT",
                credit_received=None,
            )
            min_margin_required = per_contract_margin * min_spreads * (1 + buffer_pct)

            if margin_remaining < min_margin_required:
                self.log(
                    f"MARGIN_PRE_CHECK: CREDIT BLOCKED | Available=${margin_remaining:,.0f} | "
                    f"Required=${min_margin_required:,.0f} (width=${spread_width} × {min_spreads} × {1+buffer_pct:.0%})",
                    trades_only=True,
                )
                return fail("R_MARGIN_PRECHECK")

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
                    return fail("R_COOLDOWN_DIRECTIONAL")
                else:
                    self._last_spread_exit_time = None
            except (ValueError, TypeError):
                self._last_spread_exit_time = None

        # Check trade limits
        if not self._can_trade_options(OptionsMode.SWING):
            return fail("TRADE_LIMIT_BLOCK")

        # Regime crisis check
        if regime_score < config.SPREAD_REGIME_CRISIS:
            self.log(
                f"CREDIT_SPREAD: No entry - regime {regime_score:.1f} < "
                f"{config.SPREAD_REGIME_CRISIS} (crisis mode)"
            )
            return fail("REGIME_CRISIS_BLOCK")

        # V6.0: Direction now passed in from conviction resolution
        # Caller (main.py) has already resolved VASS conviction vs macro direction
        if direction is None:
            self.log(
                "CREDIT_SPREAD: No entry - direction not provided (conviction resolution required)"
            )
            return fail("DIRECTION_MISSING")

        if overlay_state == "STRESS" and strategy == SpreadStrategy.BULL_PUT_CREDIT:
            self.log(
                f"CREDIT_SPREAD: BULL_PUT_CREDIT blocked by overlay | "
                f"Overlay={overlay_state} | VIX={vix_current:.1f} | Regime={regime_score:.1f}"
            )
            return fail("E_OVERLAY_STRESS_BULL_BLOCK")

        # Check safeguards
        if gap_filter_triggered:
            self.log("CREDIT_SPREAD: Entry blocked - gap filter active")
            return fail("GAP_FILTER_BLOCK")

        if vol_shock_active:
            self.log("CREDIT_SPREAD: Entry blocked - vol shock active")
            return fail("VOL_SHOCK_BLOCK")

        # Check time window (10:00 AM - 2:30 PM ET)
        # V3.0: EOD scan at 15:45 bypasses time window — chain is valid at EOD
        time_minutes = current_hour * 60 + current_minute
        if not is_eod_scan and not (10 * 60 <= time_minutes <= 14 * 60 + 30):
            return fail("TIME_WINDOW_BLOCK")

        # Validate contracts
        if short_leg_contract is None or long_leg_contract is None:
            self.log("CREDIT_SPREAD: Entry blocked - missing contract legs")
            return fail_quality("MISSING_SPREAD_LEGS")

        # Validate strategy
        if strategy is None or not self.is_credit_strategy(strategy):
            self.log(f"CREDIT_SPREAD: Entry blocked - invalid strategy {strategy}")
            return fail_quality("INVALID_CREDIT_STRATEGY")

        # Determine spread type from strategy
        spread_type = strategy.value  # "BULL_PUT_CREDIT" or "BEAR_CALL_CREDIT"

        now_dt = self._parse_dt(current_date, current_hour, current_minute)
        signature = self._build_vass_signature(
            spread_type=spread_type,
            direction=direction,
            long_leg_contract=long_leg_contract,
        )
        expiry_bucket = str(getattr(long_leg_contract, "expiry", "") or "").strip()
        if not expiry_bucket:
            expiry_bucket = f"DTE:{int(getattr(long_leg_contract, 'days_to_expiry', -1))}"
        expiry_block_reason = self._check_expiry_concentration_cap(
            expiry_bucket=expiry_bucket,
            direction=direction,
            regime_score=regime_score,
            vix_current=vix_current,
        )
        if expiry_block_reason:
            return fail(expiry_block_reason)
        similar_block_reason = self._check_vass_similar_entry_guard(signature, now_dt)
        if similar_block_reason:
            return fail(similar_block_reason)

        # V6.4: Pre-entry assignment risk gate for credit spreads with short PUTs
        # BULL_PUT_CREDIT has a short PUT (higher strike) - check assignment risk
        if (
            strategy == SpreadStrategy.BULL_PUT_CREDIT
            and config.BEAR_PUT_ENTRY_GATE_ENABLED
            and short_leg_contract is not None
            and current_price > 0
        ):
            min_otm_pct = float(getattr(config, "BEAR_PUT_ENTRY_MIN_OTM_PCT", 0.03))
            stress_otm_pct = float(
                getattr(config, "BEAR_PUT_ENTRY_MIN_OTM_PCT_STRESS", min_otm_pct)
            )
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
            short_strike = short_leg_contract.strike
            # For short PUTs: OTM when strike < price, ITM when strike > price
            otm_pct = (current_price - short_strike) / current_price
            if otm_pct < min_otm_pct:
                self.log(
                    f"CREDIT_SPREAD: Entry blocked - BULL_PUT assignment risk | "
                    f"Short strike {short_strike:.0f} is {otm_pct:.1%} OTM "
                    f"(min {min_otm_pct:.1%}) | "
                    f"QQQ={current_price:.2f}"
                )
                return fail(f"BEAR_PUT_ASSIGNMENT_GATE_{gate_profile}")

        # Calculate width
        width = abs(short_leg_contract.strike - long_leg_contract.strike)
        if width <= 0:
            self.log(f"CREDIT_SPREAD: Entry blocked - invalid width {width}")
            return fail_quality("WIDTH_NON_POSITIVE")

        # Calculate credit received (conservative: bid for sell, ask for buy)
        credit_received = short_leg_contract.bid - long_leg_contract.ask
        min_credit_required = self._get_effective_credit_min(vix_current=vix_current)
        if credit_received < min_credit_required:
            self.log(
                f"CREDIT_SPREAD: Entry blocked - credit ${credit_received:.2f} < "
                f"min ${min_credit_required:.2f}"
            )
            return fail_quality("CREDIT_BELOW_MIN")

        credit_to_width = (credit_received / width) if width > 0 else 0.0
        min_credit_to_width = self._get_effective_credit_to_width_min(vix_current=vix_current)
        if credit_to_width < min_credit_to_width:
            self.log(
                f"CREDIT_SPREAD: Entry blocked - CREDIT_TO_WIDTH {credit_to_width:.1%} < {min_credit_to_width:.0%} | "
                f"Credit=${credit_received:.2f} Width=${width:.0f}",
                trades_only=True,
            )
            return fail_quality("CREDIT_TO_WIDTH_TOO_LOW")

        if bool(getattr(config, "SPREAD_ENTRY_COMMISSION_GATE_ENABLED", False)):
            max_profit_dollars = credit_received * 100.0
            commission_per_spread = float(getattr(config, "SPREAD_COMMISSION_PER_CONTRACT", 2.60))
            ratio_limit = float(getattr(config, "SPREAD_MAX_COMMISSION_TO_MAX_PROFIT_RATIO", 0.15))
            fee_to_profit_ratio = (
                commission_per_spread / max_profit_dollars if max_profit_dollars > 0 else 1.0
            )
            if fee_to_profit_ratio > ratio_limit:
                self.log(
                    f"CREDIT_SPREAD: Entry blocked - COMMISSION_TO_MAX_PROFIT {fee_to_profit_ratio:.1%} "
                    f"> {ratio_limit:.0%} | MaxProfit=${max_profit_dollars:.2f} "
                    f"Commission=${commission_per_spread:.2f}",
                    trades_only=True,
                )
                return fail_quality("COMMISSION_TO_MAX_PROFIT_TOO_HIGH")

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
            return fail_quality("ENTRY_SCORE_BELOW_MIN")

        # Size using margin-based calculator
        # V3.0 SCALABILITY FIX: Use percentage-based cap
        portfolio_value = self.algorithm.Portfolio.TotalPortfolioValue if self.algorithm else 50000
        swing_max_pct = getattr(
            config, "VASS_RISK_PER_TRADE_PCT", getattr(config, "SWING_SPREAD_MAX_PCT", 0.15)
        )
        swing_max_dollars = portfolio_value * swing_max_pct
        num_spreads, _credit_per, _max_loss_per, _total_margin = self._calculate_credit_spread_size(
            short_leg_contract, long_leg_contract, swing_max_dollars
        )

        if num_spreads <= 0:
            return fail("NUM_SPREADS_NON_POSITIVE")

        # V2.3.20: Apply cold start size multiplier
        if size_multiplier < 1.0:
            num_spreads = max(1, int(num_spreads * size_multiplier))
            self.log(
                f"CREDIT_SPREAD: Cold start sizing - reduced to {num_spreads} spreads "
                f"(x{size_multiplier})"
            )

        # V2.27: Apply win rate gate scaling
        if win_rate_scale < 1.0:
            scaled = max(1, int(num_spreads * win_rate_scale))
            self.log(
                f"WIN_RATE_GATE: CREDIT REDUCED | Scale={win_rate_scale:.0%} | "
                f"{num_spreads} -> {scaled} spreads",
                trades_only=True,
            )
            num_spreads = scaled

        # V6.0: Apply cold start multiplier (macro gate removed - conviction handles direction)
        if size_multiplier < 1.0:
            min_size = getattr(config, "OPTIONS_MIN_COMBINED_SIZE_PCT", 0.10)
            if size_multiplier < min_size:
                self.log(
                    f"CREDIT_SPREAD: Entry blocked - cold start size {size_multiplier:.0%} < min {min_size:.0%}"
                )
                return fail("COLD_START_BELOW_MIN")

            scaled = max(1, int(num_spreads * size_multiplier))
            self.log(
                f"CREDIT_SPREAD: Sizing reduced | {num_spreads} -> {scaled} spreads | "
                f"SizeMult={size_multiplier:.0%}",
                trades_only=True,
            )
            num_spreads = scaled

        # V6.10 P5: Choppy market size reduction
        choppy_scale = self.get_choppy_market_scale()
        if choppy_scale < 1.0 and num_spreads > 1:
            choppy_adjusted = max(1, int(num_spreads * choppy_scale))
            self.log(
                f"CREDIT_SPREAD: Choppy market reduction | {num_spreads} -> {choppy_adjusted} spreads | "
                f"ChoppyScale={choppy_scale:.0%}",
                trades_only=True,
            )
            num_spreads = choppy_adjusted

        # V2.21: Pre-submission margin estimation
        if margin_remaining is not None and margin_remaining > 0 and width > 0:
            safety_factor = getattr(config, "SPREAD_MARGIN_SAFETY_FACTOR", 0.80)
            usable_margin = self.get_usable_margin(margin_remaining)

            if self._rejection_margin_cap is not None:
                self.log(
                    f"CREDIT_SIZING: Rejection cap active | Cap=${self._rejection_margin_cap:,.0f} | "
                    f"Usable=${usable_margin:,.0f}",
                    trades_only=True,
                )

            estimated_margin_per_spread = self.estimate_spread_margin_per_contract(
                spread_width=width,
                spread_type=spread_type,
                credit_received=credit_received,
            )
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
                f"MARGIN_SCALE_BELOW_MIN_CONTRACTS",
                trades_only=True,
            )
            return fail("MARGIN_SCALE_BELOW_MIN_CONTRACTS")  # Preserve explicit reason

        if num_spreads <= 0:
            self.log(
                f"CREDIT_SPREAD: Entry blocked - cannot size position | "
                f"Width=${width:.2f} Credit=${credit_received:.2f}"
            )
            return fail("NUM_SPREADS_NON_POSITIVE_AFTER_MARGIN")

        # Enforce hard cap
        hard_cap = int(
            getattr(config, "SPREAD_MAX_CONTRACTS_HARD_CAP", config.SPREAD_MAX_CONTRACTS)
        )
        if num_spreads > hard_cap:
            self.log(f"CREDIT_SPREAD_LIMIT: Capped | Requested={num_spreads} > " f"Max={hard_cap}")
            num_spreads = hard_cap

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

        # Record attempt for this credit strategy key (successful signal creation).
        self._record_spread_entry_attempt(attempt_key)

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

        # V2.23.1 APVP Fix: Use LONG leg (protection) as primary symbol to match
        # router combo convention. Router expects: primary=BUY leg, metadata=SELL leg.
        # Broker handles credit/debit mechanics through ComboMarketOrder.
        return TargetWeight(
            symbol=self._symbol_str(long_leg_contract.symbol),
            target_weight=config.OPTIONS_SWING_ALLOCATION,
            source="OPT",
            urgency=Urgency.IMMEDIATE,
            reason=reason,
            requested_quantity=num_spreads,  # Positive (matches debit convention)
            metadata={
                "spread_type": spread_type,
                "spread_short_leg_symbol": self._symbol_str(short_leg_contract.symbol),
                "spread_short_leg_quantity": num_spreads,
                "vass_signature_key": signature,
                "spread_net_debit": -credit_received,  # Negative = credit
                "spread_cost_or_credit": credit_received,
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
                # Prices for router lookup (long leg = primary)
                "contract_price": long_leg_contract.mid_price,
                "short_leg_price": short_leg_contract.mid_price,
            },
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
        """
        V5.3 P0 Fix 1: Check if short leg is deep ITM (assignment risk).

        A short option is considered deep ITM when:
        - DTE <= threshold (default 3)
        - AND (delta > threshold OR intrinsic value > threshold)

        Args:
            short_leg: The short leg of the spread
            underlying_price: Current price of underlying (QQQ)
            current_dte: Days to expiration

        Returns:
            Tuple of (is_deep_itm, reason)
        """
        if not getattr(config, "DEEP_ITM_EXIT_ENABLED", True):
            return False, ""

        dte_threshold = getattr(config, "DEEP_ITM_EXIT_DTE_THRESHOLD", 3)
        delta_threshold = getattr(config, "DEEP_ITM_EXIT_DELTA_THRESHOLD", 0.80)
        intrinsic_pct_threshold = getattr(config, "DEEP_ITM_EXIT_INTRINSIC_PCT", 0.05)

        # Only check if DTE is within threshold
        if current_dte > dte_threshold:
            return False, ""

        # Calculate intrinsic value
        is_put = "P" in str(short_leg.symbol).upper()
        strike = short_leg.strike

        if is_put:
            # Put is ITM when strike > underlying
            intrinsic = max(0, strike - underlying_price)
            is_itm = strike > underlying_price
        else:
            # Call is ITM when strike < underlying
            intrinsic = max(0, underlying_price - strike)
            is_itm = strike < underlying_price

        if not is_itm:
            return False, ""

        intrinsic_pct = intrinsic / underlying_price if underlying_price > 0 else 0

        # Check delta threshold (if available)
        short_delta = abs(getattr(short_leg, "delta", 0.5))
        is_deep_by_delta = short_delta >= delta_threshold

        # Check intrinsic threshold
        is_deep_by_intrinsic = intrinsic_pct >= intrinsic_pct_threshold

        if is_deep_by_delta or is_deep_by_intrinsic:
            reason = (
                f"DEEP_ITM_SHORT: DTE={current_dte} | Delta={short_delta:.2f} | "
                f"Intrinsic=${intrinsic:.2f} ({intrinsic_pct:.1%}) | "
                f"Strike={strike} vs Underlying={underlying_price:.2f}"
            )
            return True, reason

        return False, ""

    def _check_overnight_itm_short_risk(
        self,
        short_leg: "OptionContract",
        underlying_price: float,
        current_dte: int,
        current_hour: int,
        current_minute: int,
    ) -> Tuple[bool, str]:
        """
        V5.3 P0 Fix 2: Block holding short ITM options overnight if DTE <= 2.

        Assignment typically happens overnight. If a short option is ITM
        near market close with low DTE, force close to avoid assignment.

        Returns:
            Tuple of (should_close, reason)
        """
        if not getattr(config, "OVERNIGHT_ITM_SHORT_BLOCK_ENABLED", True):
            return False, ""

        dte_threshold = getattr(config, "OVERNIGHT_ITM_SHORT_DTE_THRESHOLD", 2)
        check_hour = getattr(config, "OVERNIGHT_ITM_SHORT_CHECK_TIME_HOUR", 15)
        check_minute = getattr(config, "OVERNIGHT_ITM_SHORT_CHECK_TIME_MINUTE", 0)

        # Only check at or after the configured time
        current_time_mins = current_hour * 60 + current_minute
        check_time_mins = check_hour * 60 + check_minute

        if current_time_mins < check_time_mins:
            return False, ""

        # Only check if DTE is within threshold
        if current_dte > dte_threshold:
            return False, ""

        # Check if short leg is ITM
        is_put = "P" in str(short_leg.symbol).upper()
        strike = short_leg.strike

        if is_put:
            is_itm = strike > underlying_price
        else:
            is_itm = strike < underlying_price

        if is_itm:
            reason = (
                f"OVERNIGHT_ITM_BLOCK: Short {'PUT' if is_put else 'CALL'} "
                f"Strike={strike} is ITM (Underlying={underlying_price:.2f}) | "
                f"DTE={current_dte} | Time={current_hour}:{current_minute:02d} | "
                f"Closing to prevent overnight assignment risk"
            )
            return True, reason

        return False, ""

    def _check_assignment_margin_buffer(
        self,
        spread: "SpreadPosition",
        underlying_price: float,
        available_margin: float,
    ) -> Tuple[bool, str]:
        """
        V5.3 P0 Fix 3: Check if margin buffer is sufficient for assignment risk.

        V6.7 FIX: Use spread's actual max loss, not naked short exposure.
        For vertical spreads, the long leg covers the short leg, so:
        - Debit spreads: max loss = net debit paid
        - Credit spreads: max loss = width - credit received

        Only triggers if we don't have enough margin buffer to handle
        the spread's actual max loss (not the naked assignment value).

        Returns:
            Tuple of (should_close, reason)
        """
        if not getattr(config, "ASSIGNMENT_MARGIN_BUFFER_ENABLED", True):
            return False, ""

        buffer_pct = getattr(config, "ASSIGNMENT_MARGIN_BUFFER_PCT", 0.20)
        num_contracts = spread.num_spreads

        # V6.7 FIX: Calculate actual max loss for the SPREAD, not naked short
        # In a vertical spread, the long leg covers the short leg on assignment
        # - If short call assigned: Exercise long call to deliver shares
        # - If short put assigned: Exercise long put to sell shares
        # Therefore, max loss is limited to the spread's defined risk
        if spread.spread_type in ["BULL_CALL", "BEAR_PUT"]:
            # Debit spreads: max loss = net debit paid (what we paid to open)
            actual_max_loss = spread.net_debit * 100 * num_contracts
        else:
            # Credit spreads: max loss = width - credit received
            credit_received = abs(spread.net_debit)  # Stored as negative for credits
            actual_max_loss = (spread.width - credit_received) * 100 * num_contracts

        # Required margin buffer based on actual max loss
        required_buffer = actual_max_loss * buffer_pct

        if available_margin < required_buffer:
            reason = (
                f"MARGIN_BUFFER_INSUFFICIENT: Spread max loss=${actual_max_loss:,.0f} | "
                f"Required buffer=${required_buffer:,.0f} ({buffer_pct:.0%}) | "
                f"Available margin=${available_margin:,.0f}"
            )
            return True, reason

        return False, ""

    def _check_short_leg_itm_exit(
        self,
        short_leg: "OptionContract",
        underlying_price: float,
    ) -> Tuple[bool, str]:
        """
        V6.9 P0 Fix 5: Check if short leg is ITM beyond threshold (any DTE).

        Unlike DEEP_ITM_EXIT which requires DTE <= 3, this guard triggers
        at ANY DTE when the short leg goes ITM by the threshold percentage.
        This catches early assignment risk that the Aug 2022 backtest exposed
        (assignments at DTE=4 were missed by DTE<=3 guards).

        Args:
            short_leg: The short leg of the spread
            underlying_price: Current price of underlying (QQQ)

        Returns:
            Tuple of (should_exit, reason)
        """
        if not getattr(config, "SHORT_LEG_ITM_EXIT_ENABLED", True):
            return False, ""

        itm_threshold = getattr(config, "SHORT_LEG_ITM_EXIT_THRESHOLD", 0.02)

        # Determine if call or put
        is_put = "P" in str(short_leg.symbol).upper()
        strike = short_leg.strike

        if is_put:
            # Put is ITM when strike > underlying
            if strike <= underlying_price:
                return False, ""  # Not ITM
            itm_amount = strike - underlying_price
            itm_pct = itm_amount / max(underlying_price, 1e-9)
        else:
            # Call is ITM when strike < underlying
            if strike >= underlying_price:
                return False, ""  # Not ITM
            itm_amount = underlying_price - strike
            itm_pct = itm_amount / max(underlying_price, 1e-9)

        if itm_pct >= itm_threshold:
            # Throttle diagnostic logging to avoid repeated spam in fast loops.
            interval_min = int(getattr(config, "SHORT_LEG_ITM_EXIT_LOG_INTERVAL", 15))
            now_dt = self.algorithm.Time if self.algorithm is not None else None
            if now_dt is not None and interval_min > 0:
                sym_key = str(short_leg.symbol)
                last_dt = self._last_short_leg_itm_exit_log.get(sym_key)
                if last_dt is None or (now_dt - last_dt).total_seconds() >= interval_min * 60:
                    self.log(
                        f"SHORT_LEG_ITM_EXIT_TRIGGER: {sym_key} | ITM={itm_pct:.1%} >= {itm_threshold:.1%} | "
                        f"Underlying={underlying_price:.2f} Strike={strike:.2f}",
                        trades_only=True,
                    )
                    self._last_short_leg_itm_exit_log[sym_key] = now_dt
            reason = (
                f"SHORT_LEG_ITM_EXIT: Short {'PUT' if is_put else 'CALL'} "
                f"Strike={strike} is {itm_pct:.1%} ITM (threshold={itm_threshold:.1%}) | "
                f"Underlying={underlying_price:.2f} | ITM$={itm_amount:.2f} | "
                f"Closing to prevent assignment"
            )
            return True, reason

        return False, ""

    def check_premarket_itm_shorts(
        self,
        underlying_price: float,
        spread_override: Optional[SpreadPosition] = None,
    ) -> Optional[List[TargetWeight]]:
        """
        V6.10 P0: Pre-market ITM check at 09:25 ET.

        Check all short legs BEFORE market open to catch overnight gaps.
        If a short leg went ITM overnight, queue for immediate close at 09:30.

        This is called from main.py at 09:25 ET via scheduled event.

        Args:
            underlying_price: Current/pre-market price of underlying (QQQ)

        Returns:
            List of TargetWeights to close spread at market open, or None
        """
        if not getattr(config, "PREMARKET_ITM_CHECK_ENABLED", True):
            return None

        spread = spread_override or self.get_spread_position()
        if spread is None:
            return None

        # Skip if already closing
        if spread.is_closing:
            return None

        short_leg = spread.short_leg

        # Check if short leg is ITM
        # Use a tighter threshold for pre-market (any ITM = close)
        is_put = "P" in str(short_leg.symbol).upper()
        strike = short_leg.strike

        is_itm = False
        itm_pct = 0.0

        if is_put:
            # Put is ITM when strike > underlying
            if strike > underlying_price:
                is_itm = True
                itm_pct = (strike - underlying_price) / strike
        else:
            # Call is ITM when strike < underlying
            if strike < underlying_price:
                is_itm = True
                itm_pct = (underlying_price - strike) / strike

        if not is_itm:
            self.log(
                f"PREMARKET_ITM_CHECK: Short {'PUT' if is_put else 'CALL'} "
                f"Strike={strike} is OTM | Underlying={underlying_price:.2f} | No action needed",
                trades_only=False,
            )
            return None

        # Short leg is ITM - queue for immediate close
        exit_reason = (
            f"PREMARKET_ITM_CLOSE: Short {'PUT' if is_put else 'CALL'} "
            f"Strike={strike} is {itm_pct:.1%} ITM at pre-market | "
            f"Underlying={underlying_price:.2f} | "
            f"Closing at market open to prevent assignment"
        )

        self.log(
            f"PREMARKET_ITM_CHECK: {exit_reason}",
            trades_only=True,
        )

        # Mark as closing
        spread.is_closing = True

        # Return exit signal for market open
        num_contracts = spread.num_spreads
        is_credit_spread = spread.spread_type in (
            "BULL_PUT_CREDIT",
            "BEAR_CALL_CREDIT",
            SpreadStrategy.BULL_PUT_CREDIT.value,
            SpreadStrategy.BEAR_CALL_CREDIT.value,
        )
        credit_received = abs(spread.net_debit) if is_credit_spread else 0.0
        return [
            TargetWeight(
                symbol=self._symbol_str(spread.long_leg.symbol),
                target_weight=0.0,
                source="OPT",
                urgency=Urgency.IMMEDIATE,
                reason=f"PREMARKET_ITM: {exit_reason}",
                requested_quantity=num_contracts,
                metadata={
                    "spread_type": spread.spread_type,
                    "spread_close_short": True,
                    "spread_short_leg_symbol": self._symbol_str(spread.short_leg.symbol),
                    "spread_short_leg_quantity": num_contracts,
                    "spread_key": self._build_spread_key(spread),
                    "spread_width": spread.width,
                    "is_credit_spread": is_credit_spread,
                    "spread_credit_received": credit_received,
                    "exit_type": "PREMARKET_ITM",
                },
            )
        ]

    def check_assignment_risk_exit(
        self,
        underlying_price: float,
        current_dte: int,
        current_hour: int,
        current_minute: int,
        available_margin: float = 0,
        spread_override: Optional[SpreadPosition] = None,
    ) -> Optional[List[TargetWeight]]:
        """
        V5.3 P0: Check if spread should be closed due to assignment risk.

        This is a PRIORITY check that runs BEFORE normal exit conditions.
        Assignment risk takes precedence over profit/loss targets.

        Args:
            underlying_price: Current price of underlying (QQQ)
            current_dte: Days to expiration
            current_hour: Current hour (ET)
            current_minute: Current minute
            available_margin: Available margin for assignment buffer check

        Returns:
            List of TargetWeights to close spread, or None
        """
        spread = spread_override or self.get_spread_position()
        if spread is None:
            return None

        # Skip if already closing
        if spread.is_closing:
            return None

        short_leg = spread.short_leg
        exit_reason = None

        # Grace period to avoid immediate churn exits right after spread entry.
        # Mandatory DTE close remains active.
        assignment_grace_minutes = getattr(config, "SPREAD_ASSIGNMENT_GRACE_MINUTES", 45)
        in_assignment_grace = False
        if assignment_grace_minutes > 0 and getattr(self, "algorithm", None) is not None:
            try:
                from datetime import datetime

                entry_dt = datetime.strptime(spread.entry_time[:19], "%Y-%m-%d %H:%M:%S")
                now_dt = self.algorithm.Time
                minutes_live = (now_dt - entry_dt).total_seconds() / 60.0
                in_assignment_grace = 0 <= minutes_live < assignment_grace_minutes
            except Exception:
                in_assignment_grace = False

        # V6.10 P0: MANDATORY DTE FORCE CLOSE (Nuclear Option)
        # Close ALL spreads at DTE=1 regardless of P&L - last line of defense
        force_close_enabled = getattr(config, "SPREAD_FORCE_CLOSE_ENABLED", True)
        force_close_dte = getattr(config, "SPREAD_FORCE_CLOSE_DTE", 1)
        if force_close_enabled and current_dte <= force_close_dte:
            exit_reason = (
                f"MANDATORY_DTE_CLOSE: DTE={current_dte} <= {force_close_dte} | "
                f"Closing ALL spreads to prevent assignment risk"
            )

        # V6.9 P0 Fix 5: Short leg ITM exit (any DTE) - CHECK FIRST
        # This catches assignments at any DTE, not just near expiry
        if not in_assignment_grace:
            should_exit, itm_reason = self._check_short_leg_itm_exit(
                short_leg=short_leg,
                underlying_price=underlying_price,
            )
            if should_exit:
                exit_reason = itm_reason

        # P0 Fix 1: Deep ITM short leg (DTE <= 3)
        if exit_reason is None and not in_assignment_grace:
            is_deep_itm, deep_itm_reason = self._is_short_leg_deep_itm(
                short_leg=short_leg,
                underlying_price=underlying_price,
                current_dte=current_dte,
            )
            if is_deep_itm:
                exit_reason = deep_itm_reason

        # P0 Fix 2: Overnight ITM short block
        if exit_reason is None and not in_assignment_grace:
            should_close, overnight_reason = self._check_overnight_itm_short_risk(
                short_leg=short_leg,
                underlying_price=underlying_price,
                current_dte=current_dte,
                current_hour=current_hour,
                current_minute=current_minute,
            )
            if should_close:
                exit_reason = overnight_reason

        # P0 Fix 3: Margin buffer insufficient
        if exit_reason is None and available_margin > 0 and not in_assignment_grace:
            should_close, margin_reason = self._check_assignment_margin_buffer(
                spread=spread,
                underlying_price=underlying_price,
                available_margin=available_margin,
            )
            if should_close:
                exit_reason = margin_reason

        if exit_reason is None:
            return None

        self.log(
            f"ASSIGNMENT_RISK_EXIT: {exit_reason}",
            trades_only=True,
        )

        # Mark as closing to prevent duplicate signals
        spread.is_closing = True

        # Return exit signal (same structure as normal spread exit)
        # V6.5 FIX: Added spread_close_short and requested_quantity for proper combo close
        num_contracts = spread.num_spreads
        is_credit_spread = spread.spread_type in (
            "BULL_PUT_CREDIT",
            "BEAR_CALL_CREDIT",
            SpreadStrategy.BULL_PUT_CREDIT.value,
            SpreadStrategy.BEAR_CALL_CREDIT.value,
        )
        credit_received = abs(spread.net_debit) if is_credit_spread else 0.0
        return [
            TargetWeight(
                symbol=self._symbol_str(spread.long_leg.symbol),
                target_weight=0.0,
                source="OPT",
                urgency=Urgency.IMMEDIATE,
                reason=f"ASSIGNMENT_RISK: {exit_reason}",
                requested_quantity=num_contracts,
                metadata={
                    "spread_type": spread.spread_type,
                    "spread_close_short": True,  # V6.5 FIX: Required for combo close
                    "spread_short_leg_symbol": self._symbol_str(spread.short_leg.symbol),
                    "spread_short_leg_quantity": num_contracts,
                    "spread_key": self._build_spread_key(spread),  # V6.5 FIX: Positive for close
                    "spread_width": spread.width,
                    "is_credit_spread": is_credit_spread,
                    "spread_credit_received": credit_received,
                    "exit_type": "ASSIGNMENT_RISK",
                },
            )
        ]

    def get_assignment_aware_size_multiplier(
        self,
        spread_width: float,
        portfolio_value: float,
        requested_contracts: int,
    ) -> float:
        """
        V6.0 Fix: Assignment-aware position sizing using spread width.

        For vertical spreads, max loss is limited to spread width, NOT underlying price.
        Previous bug used underlying price (~$358) instead of spread width (~$6),
        causing 20 contracts to be reduced to 1.

        Args:
            spread_width: Width between strikes (e.g., $6 for $358/$352 spread)
            portfolio_value: Total portfolio value
            requested_contracts: Requested number of contracts

        Returns:
            Size multiplier (0.0 to 1.0)
        """
        if not getattr(config, "ASSIGNMENT_AWARE_SIZING_ENABLED", True):
            return 1.0

        # Skip assignment-aware sizing in test mode (algorithm is None)
        if self.algorithm is None:
            return 1.0

        max_exposure_pct = getattr(config, "ASSIGNMENT_SIZING_MAX_EXPOSURE_PCT", 0.50)

        # Max exposure we allow = portfolio_value * max_exposure_pct
        max_exposure = portfolio_value * max_exposure_pct

        # V6.0 FIX: Max loss on spread = spread_width * 100 * contracts
        # NOT underlying_price * 100 * contracts (that's for naked options)
        potential_exposure = spread_width * 100 * requested_contracts

        if potential_exposure <= max_exposure:
            return 1.0

        # Calculate how many contracts we can safely hold
        safe_contracts = int(max_exposure / (spread_width * 100))
        if safe_contracts <= 0:
            return 0.0

        multiplier = safe_contracts / requested_contracts
        self.log(
            f"ASSIGNMENT_SIZING: Reduced {requested_contracts} -> {safe_contracts} contracts | "
            f"Max exposure={max_exposure_pct:.0%} of ${portfolio_value:,.0f} = ${max_exposure:,.0f} | "
            f"Potential=${potential_exposure:,.0f} (width=${spread_width:.2f})",
            trades_only=True,
        )

        return multiplier

    def handle_partial_assignment(
        self,
        assigned_symbol: str,
        assigned_quantity: int,
    ) -> Optional[List[TargetWeight]]:
        """
        V5.3 P0 Fix 4: Handle partial assignment of spread.

        When one leg of a spread is assigned, we need to close the remaining leg
        to avoid naked exposure.

        Args:
            assigned_symbol: Symbol that was assigned
            assigned_quantity: Quantity that was assigned

        Returns:
            List of TargetWeights to close orphaned legs, or None
        """
        if not getattr(config, "PARTIAL_ASSIGNMENT_DETECTION_ENABLED", True):
            return None

        spread = None
        for s in self.get_spread_positions():
            if assigned_symbol == s.short_leg.symbol or assigned_symbol == s.long_leg.symbol:
                spread = s
                break
        if spread is None:
            return None
        auto_close = getattr(config, "PARTIAL_ASSIGNMENT_AUTO_CLOSE", True)

        # Check if assigned symbol matches our short leg
        is_short_assigned = assigned_symbol == spread.short_leg.symbol
        is_long_assigned = assigned_symbol == spread.long_leg.symbol

        if not (is_short_assigned or is_long_assigned):
            return None

        self.log(
            f"PARTIAL_ASSIGNMENT_DETECTED: {assigned_symbol} x{assigned_quantity} | "
            f"Spread: {spread.spread_type} | "
            f"{'Short' if is_short_assigned else 'Long'} leg assigned",
            trades_only=True,
        )

        if not auto_close:
            self.log(
                "PARTIAL_ASSIGNMENT: Auto-close disabled, manual intervention required",
                trades_only=True,
            )
            return None

        # Close the remaining leg
        remaining_leg = spread.long_leg if is_short_assigned else spread.short_leg
        remaining_qty = spread.num_spreads

        spread.is_closing = True

        return [
            TargetWeight(
                symbol=self._symbol_str(remaining_leg.symbol),
                target_weight=0.0,
                source="OPT",
                urgency=Urgency.IMMEDIATE,
                reason=f"PARTIAL_ASSIGNMENT: Closing orphaned {'long' if is_short_assigned else 'short'} leg",
                requested_quantity=max(1, int(remaining_qty)),
                metadata={
                    "exit_type": "PARTIAL_ASSIGNMENT",
                    "assigned_leg": assigned_symbol,
                },
            )
        ]

    # =========================================================================
    # V2.3 SPREAD EXIT SIGNALS
    # =========================================================================

    def check_spread_exit_signals(
        self,
        long_leg_price: float,
        short_leg_price: float,
        regime_score: float,
        current_dte: int,
        vix_current: Optional[float] = None,
        spread_override: Optional[SpreadPosition] = None,
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
        spread = spread_override or self.get_spread_position()
        if spread is None:
            return None

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

        # V9.4 P0: Exit signal cooldown — if a previous exit signal was sent but the
        # close order failed (margin, liquidity, etc.), don't re-fire every minute.
        # Wait SPREAD_EXIT_RETRY_MINUTES before retrying.
        retry_minutes = int(getattr(config, "SPREAD_EXIT_RETRY_MINUTES", 15))
        if retry_minutes > 0 and self.algorithm is not None:
            spread_key = self._build_spread_key(spread)
            last_exit_time = self._spread_exit_signal_cooldown.get(spread_key)
            if last_exit_time is not None:
                elapsed = (self.algorithm.Time - last_exit_time).total_seconds() / 60.0
                if elapsed < retry_minutes:
                    return None

        # Phase A: anti-churn hold window for non-emergency spread exits.
        # Emergency exits (assignment/0DTE mandatory) are handled in check_assignment_risk_exit().
        min_hold_minutes = int(getattr(config, "SPREAD_MIN_HOLD_MINUTES", 0))
        if min_hold_minutes > 0 and self.algorithm is not None:
            try:
                entry_dt = datetime.strptime(spread.entry_time[:19], "%Y-%m-%d %H:%M:%S")
                live_minutes = (self.algorithm.Time - entry_dt).total_seconds() / 60.0
                mandatory_dte = int(getattr(config, "SPREAD_FORCE_CLOSE_DTE", 1))
                if 0 <= live_minutes < min_hold_minutes and current_dte > mandatory_dte:
                    hold_guard_bypass = False
                    # Catastrophic hard-stop override must remain active even during hold guard.
                    if spread.spread_type in (
                        "BULL_CALL",
                        "BEAR_PUT",
                        SpreadStrategy.BULL_CALL_DEBIT.value,
                        SpreadStrategy.BEAR_PUT_DEBIT.value,
                    ):
                        entry_debit = float(getattr(spread, "net_debit", 0.0) or 0.0)
                        if entry_debit > 0:
                            current_spread_value = float(long_leg_price) - float(short_leg_price)
                            if current_spread_value < 0:
                                self.log(
                                    f"SPREAD_PNL_CLAMP_APPLIED: HoldGuard | Key={self._build_spread_key(spread)} | "
                                    f"RawValue=${current_spread_value:.2f} -> $0.00",
                                    trades_only=True,
                                )
                                current_spread_value = 0.0
                            pnl = current_spread_value - entry_debit
                            pnl_pct = pnl / entry_debit

                            hard_stop_pct = float(getattr(config, "SPREAD_HARD_STOP_LOSS_PCT", 0.0))
                            if hard_stop_pct > 0 and pnl_pct <= -hard_stop_pct:
                                self.log(
                                    f"SPREAD_HARD_STOP_DURING_HOLD: {pnl_pct:.1%} <= -{hard_stop_pct:.0%} | "
                                    f"Key={self._build_spread_key(spread)} | Held={live_minutes:.0f}m",
                                    trades_only=True,
                                )
                                spread.is_closing = True
                                if self.algorithm is not None:
                                    self._spread_exit_signal_cooldown[
                                        self._build_spread_key(spread)
                                    ] = self.algorithm.Time
                                return [
                                    TargetWeight(
                                        symbol=self._symbol_str(spread.long_leg.symbol),
                                        target_weight=0.0,
                                        source="OPT",
                                        urgency=Urgency.IMMEDIATE,
                                        reason=(
                                            f"SPREAD_EXIT: SPREAD_HARD_STOP_DURING_HOLD {pnl_pct:.1%} "
                                            f"(lost > {hard_stop_pct:.0%} hard cap)"
                                        ),
                                        requested_quantity=spread.num_spreads,
                                        metadata={
                                            "spread_close_short": True,
                                            "spread_type": spread.spread_type,
                                            "spread_short_leg_symbol": self._symbol_str(
                                                spread.short_leg.symbol
                                            ),
                                            "spread_short_leg_quantity": spread.num_spreads,
                                            "spread_key": self._build_spread_key(spread),
                                            "spread_width": spread.width,
                                            "spread_exit_code": "SPREAD_HARD_STOP_DURING_HOLD",
                                            "is_credit_spread": False,
                                            "spread_credit_received": 0.0,
                                        },
                                    )
                                ]

                            # EOD risk gate during hold to reduce overnight tail losses.
                            eod_gate_enabled = bool(
                                getattr(config, "SPREAD_EOD_HOLD_RISK_GATE_ENABLED", False)
                            )
                            eod_gate_pct = float(
                                getattr(config, "SPREAD_EOD_HOLD_RISK_GATE_PCT", -0.25)
                            )
                            if (
                                eod_gate_enabled
                                and pnl_pct <= eod_gate_pct
                                and self.algorithm is not None
                            ):
                                eod_hour, eod_min = 15, 45
                                is_eod = self.algorithm.Time.hour > eod_hour or (
                                    self.algorithm.Time.hour == eod_hour
                                    and self.algorithm.Time.minute >= eod_min
                                )
                                if is_eod:
                                    self.log(
                                        f"SPREAD_EOD_HOLD_RISK_GATE: {pnl_pct:.1%} <= {eod_gate_pct:.0%} | "
                                        f"Key={self._build_spread_key(spread)} | Held={live_minutes/1440:.0f}d",
                                        trades_only=True,
                                    )
                                    spread.is_closing = True
                                    self._spread_exit_signal_cooldown[
                                        self._build_spread_key(spread)
                                    ] = self.algorithm.Time
                                    return [
                                        TargetWeight(
                                            symbol=self._symbol_str(spread.long_leg.symbol),
                                            target_weight=0.0,
                                            source="OPT",
                                            urgency=Urgency.IMMEDIATE,
                                            reason=(
                                                f"SPREAD_EXIT: EOD_HOLD_RISK_GATE {pnl_pct:.1%} "
                                                f"(<= {eod_gate_pct:.0%} at EOD during hold)"
                                            ),
                                            requested_quantity=spread.num_spreads,
                                            metadata={
                                                "spread_close_short": True,
                                                "spread_type": spread.spread_type,
                                                "spread_short_leg_symbol": self._symbol_str(
                                                    spread.short_leg.symbol
                                                ),
                                                "spread_short_leg_quantity": spread.num_spreads,
                                                "spread_key": self._build_spread_key(spread),
                                                "spread_width": spread.width,
                                                "spread_exit_code": "EOD_HOLD_RISK_GATE",
                                                "is_credit_spread": False,
                                                "spread_credit_received": 0.0,
                                            },
                                        )
                                    ]

                            # Profitable debit spreads can bypass hold guard and use normal exit cascade.
                            if pnl_pct > 0:
                                hold_guard_bypass = True

                    if not hold_guard_bypass:
                        spread_key = self._build_spread_key(spread)
                        if spread_key not in self._spread_hold_guard_logged:
                            self._spread_hold_guard_logged.add(spread_key)
                            hold_days = min_hold_minutes / 1440.0
                            self.log(
                                f"SPREAD_EXIT_GUARD_HOLD: Key={spread_key} | Sig={spread.spread_type} | "
                                f"Hold={hold_days:.0f}d ({min_hold_minutes}m) | DTE={current_dte}",
                                trades_only=True,
                            )
                        return None
            except Exception:
                pass

        # V2.8: Determine if credit or debit spread
        is_credit_spread = spread.spread_type in (
            "BULL_PUT_CREDIT",
            "BEAR_CALL_CREDIT",
            SpreadStrategy.BULL_PUT_CREDIT.value,
            SpreadStrategy.BEAR_CALL_CREDIT.value,
        )

        exit_reason = None

        # ---------------------------------------------------------------------
        # P0: VIX Spike Auto-Exit (bullish spreads only)
        # Close CALL spreads if VIX spikes to panic levels or 5d change surges
        # ---------------------------------------------------------------------
        vix_spike_enabled = getattr(config, "SWING_VIX_SPIKE_EXIT_ENABLED", True)
        vix_spike_level = getattr(config, "SWING_VIX_SPIKE_EXIT_LEVEL", 25.0)
        vix_spike_5d = getattr(config, "SWING_VIX_SPIKE_EXIT_5D_PCT", 0.20)
        vix_5d_change = self._iv_sensor.get_vix_5d_change() if self._iv_sensor.is_ready() else None

        is_bullish_spread = spread.spread_type in (
            "BULL_CALL",
            "BULL_PUT_CREDIT",
            SpreadStrategy.BULL_CALL_DEBIT.value,
            SpreadStrategy.BULL_PUT_CREDIT.value,
        )
        is_bearish_spread = spread.spread_type in (
            "BEAR_PUT",
            "BEAR_CALL_CREDIT",
            SpreadStrategy.BEAR_PUT_DEBIT.value,
            SpreadStrategy.BEAR_CALL_CREDIT.value,
        )

        # V6.22: Transition exit priority - force close wrong-way bullish spreads in STRESS.
        if (
            exit_reason is None
            and bool(getattr(config, "SPREAD_OVERLAY_STRESS_EXIT_ENABLED", False))
            and is_bullish_spread
            and vix_current is not None
        ):
            overlay_state = self.get_regime_overlay_state(
                vix_current=vix_current, regime_score=regime_score
            )
            if overlay_state == "STRESS":
                exit_reason = (
                    f"OVERLAY_STRESS_EXIT: Overlay={overlay_state} | "
                    f"Regime={regime_score:.0f} | VIX={vix_current:.1f}"
                )

        if (
            vix_spike_enabled
            and exit_reason is None
            and is_bullish_spread
            and vix_current is not None
        ):
            if vix_current >= vix_spike_level:
                exit_reason = f"VIX_SPIKE_EXIT: VIX {vix_current:.1f} >= {vix_spike_level}"
            elif vix_5d_change is not None and vix_5d_change >= vix_spike_5d:
                exit_reason = (
                    f"VIX_SPIKE_EXIT: 5D change {vix_5d_change:+.0%} >= {vix_spike_5d:.0%}"
                )

        # V10.5: Regime deterioration exits are evaluated after P&L is known.
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

            # Exit 2: Credit Stop Loss (actual loss exceeds % of max loss)
            # Max loss = width - credit received
            # V9.1 FIX: Old formula compared raw spread value against max_loss * multiplier,
            # which sat BELOW entry credit → stop fired on every trade at 20-min hold expiry.
            # Correct formula: stop fires when spread value exceeds entry_credit + (max_loss * multiplier),
            # meaning the trade must actually LOSE multiplier% of max_loss before stopping.
            max_loss = spread.width - entry_credit
            stop_threshold = entry_credit + max_loss * config.CREDIT_SPREAD_STOP_MULTIPLIER
            if exit_reason is None and pnl < 0 and current_spread_value >= stop_threshold:
                loss_pct = (current_spread_value - entry_credit) / max_loss if max_loss > 0 else 0
                exit_reason = (
                    f"CREDIT_STOP_LOSS {loss_pct:.1%} "
                    f"(spread value ${current_spread_value:.2f} >= ${stop_threshold:.2f})"
                )

            # Exit 2B: Regime deterioration de-risk (only once spread is already losing).
            if exit_reason is None and getattr(
                config, "SPREAD_REGIME_DETERIORATION_EXIT_ENABLED", True
            ):
                min_loss_pct = float(
                    getattr(config, "SPREAD_REGIME_DETERIORATION_MIN_LOSS_PCT", -0.15)
                )
                if pnl_pct <= min_loss_pct:
                    delta = getattr(config, "SPREAD_REGIME_DETERIORATION_DELTA", 10)
                    bull_exit = getattr(config, "SPREAD_REGIME_DETERIORATION_BULL_EXIT", 60)
                    bear_exit = getattr(config, "SPREAD_REGIME_DETERIORATION_BEAR_EXIT", 55)
                    if is_bullish_spread:
                        required_drop = spread.regime_at_entry - delta
                        if regime_score <= bull_exit and regime_score <= required_drop:
                            exit_reason = (
                                f"REGIME_DETERIORATION: {spread.regime_at_entry:.0f} → {regime_score:.0f} "
                                f"(<= {bull_exit}, drop {delta}+, loss {pnl_pct:.1%})"
                            )
                    elif is_bearish_spread:
                        required_rise = spread.regime_at_entry + delta
                        if regime_score >= bear_exit and regime_score >= required_rise:
                            exit_reason = (
                                f"REGIME_IMPROVEMENT: {spread.regime_at_entry:.0f} → {regime_score:.0f} "
                                f"(>= {bear_exit}, rise {delta}+, loss {pnl_pct:.1%})"
                            )

            # Exit 3: DTE exit (close by 5 DTE)
            if exit_reason is None and current_dte <= config.SPREAD_DTE_EXIT:
                exit_reason = f"DTE_EXIT ({current_dte} DTE <= {config.SPREAD_DTE_EXIT})"

            # Exit 4: Phase C staged neutrality de-risk.
            if exit_reason is None:
                neutrality_reason = self._check_neutrality_staged_exit(
                    spread=spread,
                    regime_score=regime_score,
                    pnl_pct=pnl_pct,
                )
                if neutrality_reason:
                    exit_reason = neutrality_reason

            # V6.1: Removed Credit Regime reversal exit - legacy logic conflicted with conviction-based entry
            # Credit spreads now exit via: STOP_LOSS, PROFIT_TARGET, DTE_EXIT, NEUTRALITY_EXIT

        else:
            # DEBIT SPREAD P&L: Original logic
            current_spread_value = long_leg_price - short_leg_price
            entry_debit = spread.net_debit
            if entry_debit > 0 and current_spread_value < 0:
                self.log(
                    f"SPREAD_PNL_CLAMP_APPLIED: ExitCheck | Key={self._build_spread_key(spread)} | "
                    f"RawValue=${current_spread_value:.2f} -> $0.00",
                    trades_only=True,
                )
                current_spread_value = 0.0
            pnl = current_spread_value - entry_debit
            pnl_pct = pnl / entry_debit if entry_debit > 0 else 0

            # V10.7: Day-4 EOD decision for debit spreads.
            # Rule: at/after day-4 EOD, close spreads when P&L is above the threshold,
            # keep only deeper losers for additional recovery time (hard stop still active).
            if (
                exit_reason is None
                and bool(getattr(config, "VASS_DAY4_EOD_DECISION_ENABLED", False))
                and self.algorithm is not None
            ):
                try:
                    entry_dt = datetime.strptime(spread.entry_time[:19], "%Y-%m-%d %H:%M:%S")
                    held_days = (self.algorithm.Time.date() - entry_dt.date()).days
                    decision_days = int(getattr(config, "VASS_DAY4_EOD_MIN_HOLD_DAYS", 4))
                    decision_time = str(getattr(config, "VASS_DAY4_EOD_DECISION_TIME", "15:45"))
                    decision_hour, decision_minute = [int(x) for x in decision_time.split(":", 1)]
                    is_eod_window = self.algorithm.Time.hour > decision_hour or (
                        self.algorithm.Time.hour == decision_hour
                        and self.algorithm.Time.minute >= decision_minute
                    )
                    if held_days >= decision_days and is_eod_window:
                        close_threshold = float(
                            getattr(config, "VASS_DAY4_EOD_KEEP_IF_PNL_GT", 0.0)
                        )
                        if pnl_pct <= close_threshold:
                            exit_reason = (
                                f"DAY4_EOD_CLOSE {pnl_pct:.1%} (<= {close_threshold:.0%}) | "
                                f"Held={held_days}d"
                            )
                        else:
                            spread_key = self._build_spread_key(spread)
                            if spread_key not in self._spread_hold_guard_logged:
                                self._spread_hold_guard_logged.add(spread_key)
                                self.log(
                                    f"DAY4_EOD_KEEP: Key={spread_key} | P&L={pnl_pct:.1%} > {close_threshold:.0%} | "
                                    f"Held={held_days}d",
                                    trades_only=True,
                                )
                            return None
                except Exception:
                    pass

            # Exit 1: Profit target (base 50% of max profit)
            # V3.0: Regime-adaptive profit targets - greedy in bull, defensive in bear
            base_profit_pct = config.SPREAD_PROFIT_TARGET_PCT
            profit_multipliers = getattr(
                config, "SPREAD_PROFIT_REGIME_MULTIPLIERS", {75: 1.0, 50: 1.0, 40: 1.0, 0: 1.0}
            )

            # Find applicable multiplier based on regime score
            profit_multiplier = 1.0
            for threshold in sorted(profit_multipliers.keys(), reverse=True):
                if regime_score >= threshold:
                    profit_multiplier = profit_multipliers[threshold]
                    break

            adaptive_profit_pct = base_profit_pct * profit_multiplier

            # V2.16-BT: Commission-aware profit target
            # Require NET profit (after commission) to meet the target, not just gross
            commission_cost = spread.num_spreads * config.SPREAD_COMMISSION_PER_CONTRACT
            raw_profit_target = spread.max_profit * adaptive_profit_pct
            # V6.4 Fix: Convert commission to per-share basis to match pnl units
            # pnl and raw_profit_target are per-share, commission_cost is total dollars
            # Each spread = 100 shares, so divide by (num_spreads * 100)
            commission_per_share = (
                commission_cost / (spread.num_spreads * 100) if spread.num_spreads > 0 else 0
            )
            profit_target = raw_profit_target + commission_per_share
            net_pnl = pnl - commission_per_share
            if pnl >= profit_target:
                exit_reason = (
                    f"PROFIT_TARGET +{pnl_pct:.1%} (Net ${net_pnl:.2f} >= ${raw_profit_target:.2f}) | "
                    f"Target {adaptive_profit_pct:.0%} (regime {regime_score:.0f}) | "
                    f"Gross ${pnl:.2f} - Commission ${commission_cost:.2f}"
                )

            # Exit 1B: TRAILING STOP — lock in gains after reaching activation threshold
            # V9.4: Once spread reaches +X% unrealized, trail stop from high-water mark
            trail_activate_pct = float(getattr(config, "SPREAD_TRAIL_ACTIVATE_PCT", 0.20))
            trail_offset_pct = float(getattr(config, "SPREAD_TRAIL_OFFSET_PCT", 0.15))
            if exit_reason is None and pnl_pct > 0:
                # Update high-water mark
                if pnl_pct > spread.highest_pnl_pct:
                    spread.highest_pnl_pct = pnl_pct
                # Trail stop activates after reaching activation threshold
                if spread.highest_pnl_pct >= trail_activate_pct:
                    trail_stop_level = spread.highest_pnl_pct - trail_offset_pct
                    if pnl_pct <= trail_stop_level:
                        exit_reason = (
                            f"TRAIL_STOP {pnl_pct:.1%} "
                            f"(High={spread.highest_pnl_pct:.1%}, Trail={trail_stop_level:.1%})"
                        )

            # Exit 2: STOP LOSS
            # Add a hard cap across regimes, then apply adaptive stop logic.
            if exit_reason is None and pnl_pct < 0:
                width_stop_pct = float(getattr(config, "SPREAD_HARD_STOP_WIDTH_PCT", 0.0))
                if width_stop_pct > 0 and float(getattr(spread, "width", 0.0)) > 0:
                    width_loss_cap = float(spread.width) * width_stop_pct
                    if pnl <= -width_loss_cap:
                        exit_reason = (
                            f"SPREAD_HARD_STOP_TRIGGERED_WIDTH {pnl:.2f} (loss <= -${width_loss_cap:.2f}, "
                            f"{width_stop_pct:.0%} of width ${float(spread.width):.2f})"
                        )
            if exit_reason is None and pnl_pct < 0:
                hard_stop_pct = float(getattr(config, "SPREAD_HARD_STOP_LOSS_PCT", 0.0))
                if hard_stop_pct > 0 and pnl_pct <= -hard_stop_pct:
                    exit_reason = f"SPREAD_HARD_STOP_TRIGGERED_PCT {pnl_pct:.1%} (lost > {hard_stop_pct:.0%} hard cap)"
            if exit_reason is None and pnl_pct < 0:
                base_stop_pct = config.SPREAD_STOP_LOSS_PCT
                stop_multipliers = getattr(
                    config, "SPREAD_STOP_REGIME_MULTIPLIERS", {75: 1.0, 50: 1.0, 40: 1.0, 0: 1.0}
                )
                stop_multiplier = 1.0
                for threshold in sorted(stop_multipliers.keys(), reverse=True):
                    if regime_score >= threshold:
                        stop_multiplier = stop_multipliers[threshold]
                        break
                adaptive_stop_pct = base_stop_pct * stop_multiplier
                if pnl_pct < -adaptive_stop_pct:
                    exit_reason = (
                        f"STOP_LOSS {pnl_pct:.1%} (lost > {adaptive_stop_pct:.0%} of entry)"
                    )

            # Exit 3: Time stop for debit spreads (hold window cap).
            if exit_reason is None and self.algorithm is not None:
                max_hold_days = int(getattr(config, "VASS_DEBIT_MAX_HOLD_DAYS", 0))
                low_vix_days = int(
                    getattr(config, "VASS_DEBIT_MAX_HOLD_DAYS_LOW_VIX", max_hold_days)
                )
                low_vix_threshold = float(getattr(config, "VASS_DEBIT_LOW_VIX_THRESHOLD", 16.0))
                if (
                    vix_current is not None
                    and low_vix_days > 0
                    and float(vix_current) < low_vix_threshold
                ):
                    max_hold_days = (
                        min(max_hold_days, low_vix_days) if max_hold_days > 0 else low_vix_days
                    )
                if max_hold_days > 0:
                    try:
                        entry_dt = datetime.strptime(spread.entry_time[:19], "%Y-%m-%d %H:%M:%S")
                        held_days = (self.algorithm.Time.date() - entry_dt.date()).days
                        if held_days >= max_hold_days:
                            exit_reason = (
                                f"SPREAD_TIME_STOP ({held_days}d >= {max_hold_days}d max hold)"
                            )
                    except Exception:
                        pass

            # Exit 3B: Regime deterioration de-risk (only once spread is already losing).
            if exit_reason is None and getattr(
                config, "SPREAD_REGIME_DETERIORATION_EXIT_ENABLED", True
            ):
                min_loss_pct = float(
                    getattr(config, "SPREAD_REGIME_DETERIORATION_MIN_LOSS_PCT", -0.15)
                )
                if pnl_pct <= min_loss_pct:
                    delta = getattr(config, "SPREAD_REGIME_DETERIORATION_DELTA", 10)
                    bull_exit = getattr(config, "SPREAD_REGIME_DETERIORATION_BULL_EXIT", 60)
                    bear_exit = getattr(config, "SPREAD_REGIME_DETERIORATION_BEAR_EXIT", 55)
                    if is_bullish_spread:
                        required_drop = spread.regime_at_entry - delta
                        if regime_score <= bull_exit and regime_score <= required_drop:
                            exit_reason = (
                                f"REGIME_DETERIORATION: {spread.regime_at_entry:.0f} → {regime_score:.0f} "
                                f"(<= {bull_exit}, drop {delta}+, loss {pnl_pct:.1%})"
                            )
                    elif is_bearish_spread:
                        required_rise = spread.regime_at_entry + delta
                        if regime_score >= bear_exit and regime_score >= required_rise:
                            exit_reason = (
                                f"REGIME_IMPROVEMENT: {spread.regime_at_entry:.0f} → {regime_score:.0f} "
                                f"(>= {bear_exit}, rise {delta}+, loss {pnl_pct:.1%})"
                            )

            # Exit 4: DTE exit (close by 5 DTE)
            if exit_reason is None and current_dte <= config.SPREAD_DTE_EXIT:
                exit_reason = f"DTE_EXIT ({current_dte} DTE <= {config.SPREAD_DTE_EXIT})"

            # Exit 5: Phase C staged neutrality de-risk.
            if exit_reason is None:
                neutrality_reason = self._check_neutrality_staged_exit(
                    spread=spread,
                    regime_score=regime_score,
                    pnl_pct=pnl_pct,
                )
                if neutrality_reason:
                    exit_reason = neutrality_reason

            # V6.1: Removed Debit Regime reversal exit - legacy logic conflicted with conviction-based entry
            # Debit spreads now exit via: STOP_LOSS, PROFIT_TARGET, DTE_EXIT, NEUTRALITY_EXIT

        if exit_reason is None:
            return None

        # Any non-neutrality terminal exit clears staged-neutrality memory for this spread.
        if not str(exit_reason).startswith("NEUTRALITY_"):
            self._spread_neutrality_warn_by_key.pop(self._build_spread_key(spread), None)

        spread_key = self._build_spread_key(spread)
        self.log(
            f"SPREAD: EXIT_SIGNAL | Key={spread_key} | {exit_reason} | "
            f"Long=${long_leg_price:.2f} Short=${short_leg_price:.2f} | "
            f"P&L={pnl_pct:.1%}",
            trades_only=True,
        )

        exit_code = "SPREAD_EXIT_UNSPECIFIED"
        try:
            exit_code = str(exit_reason).split(" ", 1)[0].split(":", 1)[0]
        except Exception:
            pass

        # V2.12 Fix #2: Lock the position to prevent duplicate exit signals
        spread.is_closing = True

        # V9.4 P0: Record exit signal time for retry cooldown
        if self.algorithm is not None:
            cooldown_key = self._build_spread_key(spread)
            self._spread_exit_signal_cooldown[cooldown_key] = self.algorithm.Time

        # V2.5 FIX: Return SINGLE exit signal with combo metadata
        # (Same structure as entry, so router creates atomic ComboMarketOrder)
        # Previously returned TWO signals which executed as separate orders!
        return [
            TargetWeight(
                symbol=self._symbol_str(spread.long_leg.symbol),
                target_weight=0.0,
                source="OPT",
                urgency=Urgency.IMMEDIATE,
                reason=f"SPREAD_EXIT: {exit_reason}",
                requested_quantity=spread.num_spreads,
                metadata={
                    "spread_close_short": True,  # Tells router this is an exit
                    "spread_type": spread.spread_type,
                    "spread_short_leg_symbol": self._symbol_str(spread.short_leg.symbol),
                    "spread_short_leg_quantity": spread.num_spreads,
                    "spread_key": self._build_spread_key(spread),
                    "spread_width": spread.width,
                    "spread_exit_code": exit_code,
                    "is_credit_spread": is_credit_spread,
                    "spread_credit_received": abs(spread.net_debit) if is_credit_spread else 0.0,
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
        spread_override: Optional[SpreadPosition] = None,
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
        spread = spread_override or self.get_spread_position()
        if spread is not None:
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
                        symbol=self._symbol_str(spread.long_leg.symbol),
                        target_weight=0.0,
                        source="OPT",
                        urgency=Urgency.IMMEDIATE,
                        reason=f"FRIDAY_FIREWALL: {close_reason}",
                        requested_quantity=spread.num_spreads,
                        metadata={
                            "spread_close_short": True,
                            "spread_short_leg_symbol": self._symbol_str(spread.short_leg.symbol),
                            "spread_short_leg_quantity": spread.num_spreads,
                            "spread_key": self._build_spread_key(spread),
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
                        symbol=self._symbol_str(position.contract.symbol),
                        target_weight=0.0,
                        source="OPT",
                        urgency=Urgency.IMMEDIATE,
                        reason=f"FRIDAY_FIREWALL: {close_reason}",
                        requested_quantity=max(1, int(getattr(position, "num_contracts", 1))),
                    )
                )

        return exit_signals if exit_signals else None

    # =========================================================================
    # V6.13 P0: OVERNIGHT GAP PROTECTION (All Days)
    # =========================================================================

    def check_overnight_gap_protection_exit(
        self,
        current_vix: float,
        current_date: str,
    ) -> Optional[List[TargetWeight]]:
        """
        V6.13 P0: Close swing spreads before overnight risk when VIX is elevated.

        Rules:
        1) If VIX >= SWING_OVERNIGHT_VIX_CLOSE_ALL → close ALL spreads
        2) If trade opened today AND VIX >= SWING_OVERNIGHT_VIX_CLOSE_FRESH → close fresh spread
        """
        if not getattr(config, "SWING_OVERNIGHT_GAP_PROTECTION_ENABLED", True):
            return None

        spreads = self.get_spread_positions()
        if not spreads:
            return None

        spread = spreads[0]
        entry_date = (
            spread.entry_time.split()[0] if " " in spread.entry_time else spread.entry_time[:10]
        )
        is_fresh_trade = entry_date == current_date

        close_all_threshold = getattr(config, "SWING_OVERNIGHT_VIX_CLOSE_ALL", 30.0)
        close_fresh_threshold = getattr(config, "SWING_OVERNIGHT_VIX_CLOSE_FRESH", 22.0)

        if current_vix >= close_all_threshold:
            reason = f"OVERNIGHT_GAP_PROTECTION: VIX {current_vix:.1f} >= {close_all_threshold}"
        elif is_fresh_trade and current_vix >= close_fresh_threshold:
            reason = f"OVERNIGHT_GAP_PROTECTION: Fresh trade + VIX {current_vix:.1f} >= {close_fresh_threshold}"
        else:
            return None

        self.log(
            f"OVERNIGHT_GAP_PROTECTION: Closing spread | {reason} | Entry={entry_date} Fresh={is_fresh_trade}",
            trades_only=True,
        )

        return [
            TargetWeight(
                symbol=self._symbol_str(spread.long_leg.symbol),
                target_weight=0.0,
                source="OPT",
                urgency=Urgency.IMMEDIATE,
                reason=reason,
                requested_quantity=spread.num_spreads,
                metadata={
                    "spread_close_short": True,
                    "spread_short_leg_symbol": self._symbol_str(spread.short_leg.symbol),
                    "spread_short_leg_quantity": spread.num_spreads,
                    "spread_key": self._build_spread_key(spread),
                    "exit_type": "OVERNIGHT_GAP_PROTECTION",
                },
            )
        ]

    # =========================================================================
    # EXIT SIGNALS
    # =========================================================================

    def _get_intraday_exit_profile(self, entry_strategy: str) -> Tuple[float, Optional[float]]:
        """Return (target_pct, stop_pct_override) for strategy-aware intraday exits."""
        strategy = self._canonical_intraday_strategy_name(entry_strategy)
        if strategy == IntradayStrategy.ITM_MOMENTUM.value:
            if self._itm_horizon_engine.enabled():
                target, stop, _, _, _ = self._itm_horizon_engine.get_exit_profile()
                return (target, stop)
            return (
                float(getattr(config, "INTRADAY_ITM_TARGET", 0.35)),
                float(getattr(config, "INTRADAY_ITM_STOP", 0.35)),
            )
        if strategy == IntradayStrategy.DEBIT_FADE.value:
            return (
                float(getattr(config, "INTRADAY_DEBIT_FADE_TARGET", 0.40)),
                float(getattr(config, "INTRADAY_DEBIT_FADE_STOP", 0.25)),
            )
        # Protective puts + swing single-leg keep existing defaults
        return (float(getattr(config, "OPTIONS_PROFIT_TARGET_PCT", 0.60)), None)

    def _get_trail_config(self, entry_strategy: str) -> Optional[Tuple[float, float]]:
        """Return (trigger_pct, trail_pct) for intraday strategy trailing stops."""
        strategy = self._canonical_intraday_strategy_name(entry_strategy)
        if strategy == IntradayStrategy.ITM_MOMENTUM.value:
            if self._itm_horizon_engine.enabled():
                _, _, trail_trigger, trail_pct, _ = self._itm_horizon_engine.get_exit_profile()
                return (trail_trigger, trail_pct)
            return (
                float(getattr(config, "INTRADAY_ITM_TRAIL_TRIGGER", 0.20)),
                float(getattr(config, "INTRADAY_ITM_TRAIL_PCT", 0.50)),
            )
        if strategy == IntradayStrategy.DEBIT_FADE.value:
            return (
                float(getattr(config, "INTRADAY_DEBIT_FADE_TRAIL_TRIGGER", 0.25)),
                float(getattr(config, "INTRADAY_DEBIT_FADE_TRAIL_PCT", 0.50)),
            )
        return None

    def check_exit_signals(
        self,
        current_price: float,
        current_dte: Optional[int] = None,
        position: "Optional[OptionsPosition]" = None,
    ) -> Optional[TargetWeight]:
        """
        Check for options exit signals.

        V2.3.10: Added DTE exit to prevent options being held to expiration.
        V6.22 FIX: Accept explicit position param so intraday positions get
        the same software stop/profit/DTE coverage as swing positions.

        Args:
            current_price: Current option price.
            current_dte: Optional current days to expiration.
            position: Explicit position to check. Falls back to self._position.

        Returns:
            TargetWeight for exit, or None if no exit signal.
        """
        pos = position if position is not None else self._position
        if pos is None:
            return None

        symbol = pos.contract.symbol
        symbol_str = self._symbol_str(symbol)
        entry_price = pos.entry_price
        is_intraday_pos = (
            self._intraday_position is not None
            and self._intraday_position.contract is not None
            and self._symbol_str(self._intraday_position.contract.symbol) == symbol_str
        )

        if is_intraday_pos and self.has_pending_intraday_exit():
            return None

        # Calculate P&L percentage
        pnl_pct = (current_price - entry_price) / entry_price

        # Exit 1: Profit target hit (+50%)
        if current_price >= pos.target_price:
            if is_intraday_pos and not self.mark_pending_intraday_exit(symbol_str):
                return None
            reason = f"TARGET_HIT +{pnl_pct:.1%} (Price: ${current_price:.2f})"
            self.log(f"OPT: EXIT_SIGNAL {symbol} | {reason}", trades_only=True)
            return TargetWeight(
                symbol=symbol_str,
                target_weight=0.0,
                source="OPT",
                urgency=Urgency.IMMEDIATE,
                reason=reason,
                requested_quantity=max(1, int(getattr(pos, "num_contracts", 1))),
            )

        # Exit 1.5: Strategy-aware trailing stop for intraday strategies
        if pos.entry_strategy and pos.entry_strategy.upper() != "PROTECTIVE_PUTS":
            if current_price > pos.highest_price:
                pos.highest_price = current_price
            trail_cfg = self._get_trail_config(pos.entry_strategy)
            if trail_cfg is not None:
                trail_trigger, trail_pct = trail_cfg
                gain_pct = (
                    (pos.highest_price - entry_price) / entry_price if entry_price > 0 else 0.0
                )
                if gain_pct >= trail_trigger:
                    trail_stop = pos.highest_price - ((pos.highest_price - entry_price) * trail_pct)
                    if trail_stop > pos.stop_price:
                        pos.stop_price = trail_stop
                    if current_price <= pos.stop_price:
                        if is_intraday_pos and not self.mark_pending_intraday_exit(symbol_str):
                            return None
                        reason = (
                            f"TRAIL_STOP {pnl_pct:.1%} (High=${pos.highest_price:.2f}, "
                            f"Trail=${pos.stop_price:.2f})"
                        )
                        self.log(f"OPT: EXIT_SIGNAL {symbol} | {reason}", trades_only=True)
                        return TargetWeight(
                            symbol=symbol_str,
                            target_weight=0.0,
                            source="OPT",
                            urgency=Urgency.IMMEDIATE,
                            reason=reason,
                            requested_quantity=max(1, int(getattr(pos, "num_contracts", 1))),
                        )

        # Exit 2: Stop hit
        if current_price <= pos.stop_price:
            if is_intraday_pos and not self.mark_pending_intraday_exit(symbol_str):
                return None
            reason = f"STOP_HIT {pnl_pct:.1%} (Price: ${current_price:.2f})"
            self.log(f"OPT: EXIT_SIGNAL {symbol} | {reason}", trades_only=True)
            return TargetWeight(
                symbol=symbol_str,
                target_weight=0.0,
                source="OPT",
                urgency=Urgency.IMMEDIATE,
                reason=reason,
                requested_quantity=max(1, int(getattr(pos, "num_contracts", 1))),
            )

        # ITM_V2 max-hold guard (calendar-based safety cap).
        if self._itm_horizon_engine.enabled() and self._is_itm_momentum_strategy_name(
            getattr(pos, "entry_strategy", "")
        ):
            max_hold_days = self._itm_horizon_engine.get_max_hold_days()
            if max_hold_days > 0:
                try:
                    entry_date = datetime.strptime(
                        str(getattr(pos, "entry_time", ""))[:10], "%Y-%m-%d"
                    ).date()
                    now_date = (
                        self.algorithm.Time.date()
                        if self.algorithm is not None and hasattr(self.algorithm, "Time")
                        else datetime.utcnow().date()
                    )
                    held_days = (now_date - entry_date).days
                except Exception:
                    held_days = 0
                if held_days >= max_hold_days:
                    if is_intraday_pos and not self.mark_pending_intraday_exit(symbol_str):
                        return None
                    reason = f"ITM_V2_MAX_HOLD ({held_days}d >= {max_hold_days}d) P&L={pnl_pct:.1%}"
                    self.log(f"OPT: EXIT_SIGNAL {symbol} | {reason}", trades_only=True)
                    return TargetWeight(
                        symbol=symbol_str,
                        target_weight=0.0,
                        source="OPT",
                        urgency=Urgency.IMMEDIATE,
                        reason=reason,
                        requested_quantity=max(1, int(getattr(pos, "num_contracts", 1))),
                    )

        # V2.3.10: Exit 3 - DTE exit (prevent expiration/exercise)
        # Close single-leg options before expiration to avoid:
        # - OTM expiring worthless (100% loss)
        # - ITM being auto-exercised (creates stock position, margin crisis)
        dte_exit_threshold = int(getattr(config, "OPTIONS_SINGLE_LEG_DTE_EXIT", 4))
        if self._is_itm_momentum_strategy_name(getattr(pos, "entry_strategy", "")):
            if self._itm_horizon_engine.enabled():
                _, _, _, _, dte_exit_threshold = self._itm_horizon_engine.get_exit_profile()
            else:
                dte_exit_threshold = int(
                    getattr(config, "INTRADAY_ITM_DTE_EXIT", dte_exit_threshold)
                )

        if current_dte is not None and current_dte <= dte_exit_threshold:
            if is_intraday_pos and not self.mark_pending_intraday_exit(symbol_str):
                return None
            reason = f"DTE_EXIT ({current_dte} DTE <= {dte_exit_threshold}) P&L={pnl_pct:.1%}"
            self.log(f"OPT: EXIT_SIGNAL {symbol} | {reason}", trades_only=True)
            return TargetWeight(
                symbol=symbol_str,
                target_weight=0.0,
                source="OPT",
                urgency=Urgency.IMMEDIATE,
                reason=reason,
                requested_quantity=max(1, int(getattr(pos, "num_contracts", 1))),
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
    ) -> Tuple[bool, str]:
        """Swing-mode entry filters delegated to VASSEntryEngine."""
        return self._vass_entry_engine.check_swing_filters(
            direction=direction,
            spy_gap_pct=spy_gap_pct,
            spy_intraday_change_pct=spy_intraday_change_pct,
            vix_intraday_change_pct=vix_intraday_change_pct,
            current_hour=current_hour,
            current_minute=current_minute,
        )

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
        governor_scale: float = 1.0,
        direction: Optional[OptionDirection] = None,
        vix_level_override: Optional[float] = None,  # V6.2: CBOE VIX for level consistency
        underlying_atr: float = 0.0,  # V6.5: QQQ ATR for delta-scaled stops
        micro_state: Optional[
            "MicroRegimeState"
        ] = None,  # Reuse approved state; avoid re-eval drift
    ) -> Optional[TargetWeight]:
        """
        Check for intraday mode entry signal using Micro Regime Engine.

        V2.1.1: Uses VIX Level × VIX Direction = 21 trading regimes.
        V2.3.20: Added size_multiplier for cold start reduced sizing.
        V2.5: Added macro_regime_score for Grind-Up Override logic.
        V3.2: Added governor_scale for intraday Governor gate.
        V6.0: Added direction parameter - use pre-resolved direction from conviction.
        V6.2: Added vix_level_override for consistent VIX level classification.
        V6.5: Added underlying_atr for delta-scaled ATR stop calculation.

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
            governor_scale: Governor scaling (0-1). V3.2: At 0%, CALL blocked, PUT allowed.
            direction: V6.0: Pre-resolved direction from conviction. If provided, skip
                recalculation to avoid mismatch with contract selection.
            vix_level_override: V6.2 - CBOE VIX for level classification (ensures consistency
                with scheduled _update_micro_regime calls).
            underlying_atr: V6.5 - QQQ ATR for delta-scaled stop calculation. If 0, falls back
                to fixed percentage stop.

        Returns:
            TargetWeight for intraday entry, or None.
        """

        def fail(reason: str, detail: Optional[str] = None) -> Optional[TargetWeight]:
            self.set_last_intraday_validation_failure(reason, detail)
            return None

        # Reset previous validation reason for this attempt
        self.set_last_intraday_validation_failure(None, None)

        # Check if already have intraday position
        if self._intraday_position is not None:
            # V6.2: Log position block (was silent - Bug #3 instrumentation)
            self.log(
                f"INTRADAY: Blocked - already has position | "
                f"Symbol={self._intraday_position.symbol if hasattr(self._intraday_position, 'symbol') else self._intraday_position}"
            )
            return fail("E_INTRADAY_HAS_POSITION")
        if self._pending_intraday_entry:
            self._clear_stale_pending_intraday_entry_if_orphaned()
        if self._pending_intraday_entry:
            return fail("E_INTRADAY_PENDING_ENTRY")

        # V2.9: Check trade limits (Bug #4 fix) - Uses comprehensive counter
        # Replaces V2.3.14 intraday-only check to also enforce global limit
        if not self._can_trade_options(OptionsMode.INTRADAY, direction=direction):
            # Preserve granular slot-limit cause for funnel diagnostics.
            tl_reason, tl_detail = self.pop_last_trade_limit_failure()
            return fail(tl_reason or "E_INTRADAY_TRADE_LIMIT", tl_detail)

        # Reuse state from generate_micro_intraday_signal when provided.
        # This prevents approved->dropped drift caused by a second update() call.
        state = micro_state
        if state is None:
            state = self._micro_regime_engine.update(
                vix_current=vix_current,
                vix_open=vix_open,
                qqq_current=qqq_current,
                qqq_open=qqq_open,
                current_time=current_time,
                macro_regime_score=macro_regime_score,
                vix_level_override=vix_level_override,  # V6.2: Pass through
            )

        # V6.8: NO_TRADE is now blocked earlier in generate_micro_intraday_signal()
        # This check is a safety net - should never reach here with NO_TRADE
        if state.recommended_strategy == IntradayStrategy.NO_TRADE:
            self.log(
                f"INTRADAY: Blocked - NO_TRADE strategy | "
                f"Regime={state.micro_regime.value} | Score={state.micro_score:.0f}"
            )
            return fail("E_INTRADAY_NO_TRADE_STRATEGY", state.micro_regime.value)

        entry_strategy = self._canonical_intraday_strategy(state.recommended_strategy)
        if entry_strategy is None:
            return fail("E_INTRADAY_NO_STRATEGY")

        itm_v2_mode = False

        # V3.2: Check if strategy is PROTECTIVE_PUTS (crisis hedge)
        if entry_strategy == IntradayStrategy.PROTECTIVE_PUTS:
            # V3.2: Actually implement protective puts (was previously just returning None)
            if not getattr(config, "PROTECTIVE_PUTS_ENABLED", True):
                self.log(
                    f"INTRADAY: Protective mode (disabled) - regime={state.micro_regime.value}"
                )
                return fail("E_PROTECTIVE_PUTS_DISABLED")

            # Force direction to PUT for protection
            direction = OptionDirection.PUT

            # Protective puts bypass macro gate (defensive by definition)
            # But still respect Governor scaling
            protective_size_pct = getattr(config, "PROTECTIVE_PUTS_SIZE_PCT", 0.02)
            effective_size_pct = protective_size_pct * governor_scale

            if effective_size_pct < 0.005:  # Less than 0.5% = not worth it
                self.log(
                    f"INTRADAY: Protective PUT size too small ({effective_size_pct:.1%}) "
                    f"| Governor={governor_scale:.0%}"
                )
                return fail("E_INTRADAY_PROTECTIVE_TOO_SMALL", f"{effective_size_pct:.3f}")

            self.log(
                f"PROTECTIVE_PUT: Crisis detected | Micro={state.micro_regime.value} | "
                f"Score={state.micro_score:.0f} | Size={effective_size_pct:.1%}",
                trades_only=True,
            )

            # Continue to contract selection with protective sizing
            # Will be handled below with special sizing path
            is_protective_put = True
        else:
            is_protective_put = False
            # V6.0: Use passed direction from conviction resolution (avoids mismatch with contract)
            # Previously recalculated from state.recommended_direction which could differ
            if direction is None:
                # Fallback to state direction if not passed (backwards compatibility)
                direction = state.recommended_direction
            if direction is None:
                strategy_value = entry_strategy.value if entry_strategy is not None else "UNKNOWN"
                self.log(f"INTRADAY: No direction recommended for {strategy_value}")
                return fail("E_INTRADAY_NO_DIRECTION", strategy_value)

            itm_v2_mode = bool(
                self._itm_horizon_engine.enabled()
                and entry_strategy == IntradayStrategy.ITM_MOMENTUM
            )
            if itm_v2_mode:
                qqq_sma20 = getattr(self.algorithm, "qqq_sma20", None) if self.algorithm else None
                sma20_value = (
                    float(qqq_sma20.Current.Value)
                    if qqq_sma20 is not None and getattr(qqq_sma20, "IsReady", False)
                    else None
                )
                qqq_adx = getattr(self.algorithm, "qqq_adx", None) if self.algorithm else None
                adx_value = (
                    float(qqq_adx.Current.Value)
                    if qqq_adx is not None and getattr(qqq_adx, "IsReady", False)
                    else None
                )
                vix20_change = (
                    self._iv_sensor.get_vix_20d_change() if self._iv_sensor is not None else None
                )
                effective_vix = float(
                    vix_level_override if vix_level_override is not None else vix_current
                )
                try:
                    if self.algorithm is not None and hasattr(self.algorithm, "_get_vix_level"):
                        effective_vix = float(self.algorithm._get_vix_level())
                except Exception:
                    pass

                active_itm_positions = 0
                if self._intraday_position is not None and self._is_itm_momentum_strategy_name(
                    getattr(self._intraday_position, "entry_strategy", "")
                ):
                    active_itm_positions = 1

                trace_id = (
                    f"ITM|{(current_time or 'NA')[:19]}|{direction.value}|"
                    f"{entry_strategy.value if entry_strategy else 'NA'}"
                )
                itm_ok, itm_code, itm_detail = self._itm_horizon_engine.evaluate_entry(
                    direction=direction,
                    current_time=current_time,
                    current_hour=current_hour,
                    current_minute=current_minute,
                    trace_id=trace_id,
                    qqq_current=qqq_current,
                    sma20_value=sma20_value,
                    adx_value=adx_value,
                    vix_current=effective_vix,
                    vix20_change=vix20_change,
                    portfolio_value=float(portfolio_value),
                    current_itm_positions=active_itm_positions,
                    algorithm=self.algorithm,
                )
                self.log(
                    f"ITM_V2_DECISION|Trace={trace_id}|Dir={direction.value}|QQQ={qqq_current:.2f}|"
                    f"SMA20={sma20_value if sma20_value is not None else 'NA'}|"
                    f"ADX={adx_value if adx_value is not None else 'NA'}|"
                    f"VIX={effective_vix:.1f}|"
                    f"VIX20d={vix20_change if vix20_change is not None else 'NA'}|"
                    f"OpenITM={active_itm_positions}|"
                    f"{'PASS' if itm_ok else 'BLOCK'}|{itm_code}|{itm_detail}",
                    trades_only=True,
                )
                if not itm_ok and not self._itm_horizon_engine.shadow_mode():
                    return fail(itm_code, itm_detail)

            if bool(getattr(config, "MICRO_ENTRY_ENGINE_ENABLED", True)):
                (
                    size_multiplier,
                    micro_fail_code,
                    micro_fail_detail,
                ) = self._micro_entry_engine.apply_pre_contract_gates(
                    state=state,
                    entry_strategy=entry_strategy,
                    direction=direction,
                    itm_v2_mode=itm_v2_mode,
                    current_time=current_time,
                    size_multiplier=size_multiplier,
                    macro_regime_score=macro_regime_score,
                    qqq_current=qqq_current,
                    vix_current=vix_current,
                    vix_level_override=vix_level_override,
                    algorithm=self.algorithm,
                    iv_sensor=self._iv_sensor,
                    call_cooldown_until_date=self._call_cooldown_until_date,
                    call_consecutive_losses=self._call_consecutive_losses,
                )
                if micro_fail_code is not None:
                    return fail(micro_fail_code, micro_fail_detail)
            else:
                self.log(
                    "INTRADAY: MICRO_ENTRY_ENGINE disabled - legacy fallback removed; blocking entry",
                    trades_only=True,
                )
                return fail("E_MICRO_ENGINE_DISABLED")

        # V3.2: Governor Gate for intraday (closes gap)
        if getattr(config, "INTRADAY_GOVERNOR_GATE_ENABLED", True) and not is_protective_put:
            if governor_scale <= 0:
                if direction == OptionDirection.CALL:
                    self.log("INTRADAY: CALL blocked at Governor 0%")
                    return fail("E_INTRADAY_GOVERNOR_CALL_BLOCK")
                # PUT allowed at Governor 0% (reduces risk)
                self.log("INTRADAY: PUT allowed at Governor 0% (defensive)", trades_only=True)

        # V6.0: Macro Regime Gate removed - conviction resolution handles direction
        # Direction comes from Micro Regime Engine's recommend_strategy_and_direction()
        # Conviction resolution (resolve_trade_signal) called in main.py before this function

        # Map strategy to concise logging name (after deprecated-strategy canonicalization).
        strategy_names = {
            IntradayStrategy.DEBIT_FADE: "DEBIT_FADE",
            IntradayStrategy.ITM_MOMENTUM: "ITM_MOM",
            IntradayStrategy.CREDIT_SPREAD: "CREDIT",
            IntradayStrategy.PROTECTIVE_PUTS: "PROTECTIVE_PUTS",
        }
        strategy_name = strategy_names.get(entry_strategy, "UNKNOWN")

        # MICRO anti-churn cooldown: avoid immediate re-entry of same strategy.
        cooldown_min = int(getattr(config, "MICRO_SAME_STRATEGY_COOLDOWN_MINUTES", 0))
        if (
            cooldown_min > 0
            and self._last_intraday_close_time is not None
            and self._last_intraday_close_strategy
        ):
            current_dt = None
            if current_time:
                try:
                    current_dt = datetime.strptime(current_time[:19], "%Y-%m-%d %H:%M:%S")
                except Exception:
                    current_dt = None
            if current_dt is None and self.algorithm is not None:
                current_dt = self.algorithm.Time
            if current_dt is not None:
                elapsed = (current_dt - self._last_intraday_close_time).total_seconds() / 60.0
                if (
                    0 <= elapsed < cooldown_min
                    and strategy_name == self._last_intraday_close_strategy
                ):
                    self.log(
                        f"INTRADAY: Same-strategy cooldown block | Strategy={strategy_name} | "
                        f"Elapsed={elapsed:.1f}m < {cooldown_min}m",
                        trades_only=True,
                    )
                    return fail("E_INTRADAY_SAME_STRATEGY_COOLDOWN")

        if bool(getattr(config, "MICRO_ENTRY_ENGINE_ENABLED", True)):
            tw_ok, tw_code = self._micro_entry_engine.validate_time_window(
                entry_strategy=entry_strategy,
                itm_v2_mode=itm_v2_mode,
                state=state,
                current_hour=current_hour,
                current_minute=current_minute,
            )
            if not tw_ok:
                return fail(tw_code or "E_INTRADAY_TIME_WINDOW")
        else:
            self.log(
                "INTRADAY: MICRO_ENTRY_ENGINE disabled - legacy fallback removed; blocking entry",
                trades_only=True,
            )
            return fail("E_MICRO_ENGINE_DISABLED")

        # Check if we have a valid contract
        if best_contract is None:
            self.log(f"INTRADAY: {strategy_name} signal but no contract available")
            return fail("E_INTRADAY_NO_CONTRACT", strategy_name)

        # V2.3 FIX: Validate contract direction matches signal direction
        # The contract was selected before direction was determined, so we must verify
        if best_contract.direction != direction:
            self.log(
                f"INTRADAY: Direction mismatch - signal wants {direction.value} "
                f"but contract is {best_contract.direction.value}, skipping"
            )
            return fail("E_INTRADAY_DIRECTION_MISMATCH")

        # V2.18: Use sizing cap (Fix for MarginBuyingPower sizing bug)
        # V3.0 SCALABILITY FIX: Use percentage-based cap instead of hardcoded dollars
        # At $50K: 8% = $4,000, at $200K: 8% = $16,000 (scales with portfolio)
        portfolio_value_for_sizing = (
            self.algorithm.Portfolio.TotalPortfolioValue if self.algorithm else 50000
        )

        # V3.2: Protective puts use fixed sizing, regular intraday uses score-based
        # V6.6: Define intraday_max_pct before branch to avoid unbound variable error
        intraday_max_pct = getattr(config, "INTRADAY_SPREAD_MAX_PCT", 0.08)

        if is_protective_put:
            # Protective puts: fixed percentage, already scaled by Governor
            protective_size_pct = getattr(config, "PROTECTIVE_PUTS_SIZE_PCT", 0.02)
            effective_size_pct = protective_size_pct * governor_scale
            adjusted_cap = portfolio_value_for_sizing * effective_size_pct
            size_mult = 1.0  # Already factored in above
            intraday_max_pct = protective_size_pct  # For logging
        else:
            intraday_max_dollars = portfolio_value_for_sizing * intraday_max_pct

            # Adjust size based on micro score
            if state.micro_score >= config.MICRO_SCORE_PRIME_MR:
                micro_mult = 1.0  # Full size
            elif state.micro_score >= config.MICRO_SCORE_GOOD_MR:
                micro_mult = 1.0  # Full size
            elif state.micro_score >= config.MICRO_SCORE_MODERATE:
                micro_mult = 0.5  # Half size
            else:
                micro_mult = 0.5  # Half size

            # V6.14 OPT: Reduce size in fragile transition states even when tradable.
            if state.micro_regime in (MicroRegime.ELEVATED, MicroRegime.WORSENING):
                micro_mult = min(micro_mult, 0.5)

            # V6.0: Apply combined multipliers (cold_start × governor × micro)
            # Macro gate removed - conviction resolution handles direction
            combined_mult = size_multiplier * governor_scale * micro_mult
            min_combined = getattr(config, "OPTIONS_MIN_COMBINED_SIZE_PCT", 0.10)
            if combined_mult < min_combined:
                self.log(
                    f"INTRADAY: Entry blocked - combined size {combined_mult:.0%} < min {min_combined:.0%}"
                )
                return fail("E_INTRADAY_COMBINED_SIZE_MIN")

            adjusted_cap = intraday_max_dollars * combined_mult
            size_mult = micro_mult  # For logging compatibility
        premium = best_contract.mid_price
        if premium <= 0:
            self.log("INTRADAY: Entry blocked - invalid premium price")
            return fail("E_INTRADAY_INVALID_PREMIUM")

        # V2.18: Calculate contracts using cap / (premium * 100)
        num_contracts = int(adjusted_cap / (premium * 100))

        # V9.8: Hard cap all MICRO intraday entries to prevent quantity explosions on cheap options.
        intraday_max_contracts = int(getattr(config, "INTRADAY_MAX_CONTRACTS", 40))
        if bool(getattr(config, "INTRADAY_CONTRACT_CAP_SCALE_WITH_EQUITY", True)):
            base_equity = float(getattr(config, "INTRADAY_MAX_CONTRACTS_BASE_EQUITY", 100000.0))
            min_contract_cap = int(getattr(config, "INTRADAY_MAX_CONTRACTS_MIN", 5))
            if base_equity > 0 and portfolio_value > 0 and intraday_max_contracts > 0:
                equity_scale = min(1.0, float(portfolio_value) / base_equity)
                scaled_cap = max(min_contract_cap, int(intraday_max_contracts * equity_scale))
                if scaled_cap < intraday_max_contracts:
                    self.log(
                        f"INTRADAY_CAP_SCALE: BaseCap={intraday_max_contracts} -> {scaled_cap} | "
                        f"Equity=${portfolio_value:,.0f} | Base=${base_equity:,.0f}",
                        trades_only=True,
                    )
                    intraday_max_contracts = scaled_cap

        if itm_v2_mode:
            itm_v2_cap = int(getattr(config, "ITM_V2_MAX_CONTRACTS_HARD_CAP", 8))
            if itm_v2_cap > 0:
                intraday_max_contracts = (
                    itm_v2_cap
                    if intraday_max_contracts <= 0
                    else min(intraday_max_contracts, itm_v2_cap)
                )

        if intraday_max_contracts > 0 and num_contracts > intraday_max_contracts:
            self.log(
                f"INTRADAY_CAP: {num_contracts} → {intraday_max_contracts} contracts (max)",
                trades_only=True,
            )
            num_contracts = intraday_max_contracts

        # V9.2 RCA: Cap protective puts to prevent outsized 10+ contract bets
        # At 3% sizing on cheap OTM puts, uncapped quantity can reach 10-15 contracts,
        # amplifying losses 5× compared to ITM_MOMENTUM's ~2 contracts.
        if is_protective_put:
            max_protective = int(getattr(config, "PROTECTIVE_PUTS_MAX_CONTRACTS", 5))
            if max_protective > 0 and num_contracts > max_protective:
                self.log(f"PROTECTIVE_CAP: {num_contracts} → {max_protective} contracts (max)")
                num_contracts = max_protective

        self.log(
            f"SIZING: INTRADAY | Cap=${adjusted_cap:,.0f} ({intraday_max_pct:.0%} of ${portfolio_value:,.0f}) | "
            f"Premium=${premium:.2f} | Qty={num_contracts}"
        )
        if num_contracts <= 0:
            allow_one_lot = bool(getattr(config, "INTRADAY_ALLOW_ONE_LOT_WHEN_CAP_TIGHT", False))
            one_lot_max_premium = float(getattr(config, "INTRADAY_ONE_LOT_MAX_PREMIUM", 6.0))
            if (
                allow_one_lot
                and entry_strategy == IntradayStrategy.ITM_MOMENTUM
                and direction == OptionDirection.CALL
                and premium <= one_lot_max_premium
                and adjusted_cap >= premium * 100 * 0.50
            ):
                num_contracts = 1
                self.log(
                    f"INTRADAY: One-lot fallback applied | Cap=${adjusted_cap:.0f} | Premium=${premium:.2f}"
                )
            else:
                self.log(
                    f"INTRADAY: Entry blocked - cap ${adjusted_cap:.0f} "
                    f"too small for premium ${premium:.2f}"
                )
                return fail("E_INTRADAY_CAP_TOO_SMALL")

        # V2.3.4: Use QQQ direction from state
        qqq_dir_str = state.qqq_direction.value if state.qqq_direction else "UNKNOWN"
        reason = (
            f"INTRADAY_{strategy_name}: Regime={state.micro_regime.value} | "
            f"Score={state.micro_score:.0f} | VIX={vix_current:.1f} "
            f"({state.vix_direction.value}) | QQQ={qqq_dir_str} "
            f"({state.qqq_move_pct:+.2f}%) | {direction.value} x{num_contracts}"
        )

        # V2.3.2 FIX #4: Mark this as intraday entry for correct position tracking
        self._pending_intraday_entry = True
        self._pending_intraday_entry_since = self.algorithm.Time if self.algorithm else None

        # V2.3.10 FIX: Set pending contract for register_entry
        # Without this, register_entry fails with "no pending contract"
        self._pending_contract = best_contract
        self._pending_num_contracts = num_contracts
        self._pending_entry_strategy = entry_strategy.value

        # V6.5: Delta-scaled ATR stop calculation
        # Formula: stop_distance = ATR_MULTIPLIER × ATR × abs(delta)
        # This gives more room in high-VIX environments while accounting for option sensitivity
        if getattr(config, "OPTIONS_USE_ATR_STOPS", True) and underlying_atr > 0 and premium > 0:
            atr_multiplier = getattr(config, "OPTIONS_ATR_STOP_MULTIPLIER", 1.5)
            option_delta = abs(best_contract.delta)

            # Calculate stop distance in option price terms
            stop_distance = atr_multiplier * underlying_atr * option_delta
            atr_stop_pct = stop_distance / premium if premium > 0 else 0.20

            # Apply floor and cap
            min_stop = getattr(config, "OPTIONS_ATR_STOP_MIN_PCT", 0.20)
            max_stop = getattr(config, "OPTIONS_ATR_STOP_MAX_PCT", 0.50)

            # V9.2 RCA: Widen stop cap for ITM_MOMENTUM in high-VIX regimes.
            # The standard 28% cap is noise in VIX>25 environments, causing premature
            # stops on trades that would have recovered. Only affects bear-market regimes.
            high_vix_momentum_regimes = {
                MicroRegime.WORSENING_HIGH,
                MicroRegime.DETERIORATING,
                MicroRegime.ELEVATED,
                MicroRegime.WORSENING,
            }
            if (
                entry_strategy == IntradayStrategy.ITM_MOMENTUM
                and state.micro_regime in high_vix_momentum_regimes
            ):
                max_stop = getattr(config, "INTRADAY_HIGH_VIX_STOP_MAX_PCT", 0.40)

            final_stop_pct = max(min_stop, min(atr_stop_pct, max_stop))

            self.log(
                f"STOP_CALC: ATR=${underlying_atr:.2f} × Δ={option_delta:.2f} × {atr_multiplier}× = "
                f"${stop_distance:.2f} | Raw={atr_stop_pct:.0%} → Final={final_stop_pct:.0%} "
                f"(floor={min_stop:.0%}, cap={max_stop:.0%})"
            )
            self._pending_stop_pct = final_stop_pct
        else:
            # Fallback to legacy fixed percentage stop
            self._pending_stop_pct = config.OPTIONS_0DTE_STOP_PCT
            if underlying_atr <= 0:
                self.log(f"STOP_CALC: ATR not ready, using fixed {self._pending_stop_pct:.0%} stop")

        # V6.22 FIX: Protective puts use dedicated stop from config (was dead config).
        # Protective puts are insurance — they need a wider stop (35%) than generic ATR (12-28%)
        # to avoid being stopped out on normal intraday noise before the hedge pays off.
        if is_protective_put:
            self._pending_stop_pct = getattr(config, "PROTECTIVE_PUTS_STOP_PCT", 0.35)
            self.log(
                f"STOP_CALC: Protective PUT override → {self._pending_stop_pct:.0%} "
                f"(config.PROTECTIVE_PUTS_STOP_PCT)"
            )

        # V10.5: widen ITM stops in MED/HIGH VIX only; keep LOW VIX behavior unchanged.
        if (
            not is_protective_put
            and (not itm_v2_mode)
            and entry_strategy == IntradayStrategy.ITM_MOMENTUM
            and self._pending_stop_pct is not None
        ):
            if vix_current >= 25:
                itm_stop_floor = float(getattr(config, "INTRADAY_ITM_STOP_FLOOR_HIGH_VIX", 0.35))
                itm_tier = "HIGH"
            elif vix_current >= 18:
                itm_stop_floor = float(getattr(config, "INTRADAY_ITM_STOP_FLOOR_MED_VIX", 0.30))
                itm_tier = "MED"
            else:
                itm_stop_floor = float(getattr(config, "INTRADAY_ITM_STOP", 0.25))
                itm_tier = "LOW"
            if self._pending_stop_pct < itm_stop_floor:
                self.log(
                    f"STOP_CALC: ITM {itm_tier} VIX floor {itm_stop_floor:.0%} > "
                    f"ATR {self._pending_stop_pct:.0%} → using floor"
                )
                self._pending_stop_pct = itm_stop_floor

        if (
            not is_protective_put
            and itm_v2_mode
            and entry_strategy == IntradayStrategy.ITM_MOMENTUM
            and self._pending_stop_pct is not None
        ):
            _, itm_v2_stop, _, _, _ = self._itm_horizon_engine.get_exit_profile()
            if itm_v2_stop is not None and itm_v2_stop > 0:
                if abs(float(self._pending_stop_pct) - float(itm_v2_stop)) > 1e-6:
                    self.log(
                        f"STOP_CALC: ITM_V2 fixed stop override {self._pending_stop_pct:.0%} -> {itm_v2_stop:.0%}",
                        trades_only=True,
                    )
                self._pending_stop_pct = float(itm_v2_stop)

        self.log(
            f"INTRADAY_SIGNAL: {reason} | Δ={best_contract.delta:.2f} K={best_contract.strike} DTE={best_contract.days_to_expiry} | "
            f"Stop={self._pending_stop_pct:.0%} | TradeCount={self._intraday_trades_today}/{config.INTRADAY_MAX_TRADES_PER_DAY}",
            trades_only=True,
        )

        # V2.4.1 FIX: Use config allocation value, not size_mult
        # Was returning 1.0/0.5 instead of actual allocation (0.0625)
        actual_target_weight = config.OPTIONS_INTRADAY_ALLOCATION * size_mult

        return TargetWeight(
            symbol=self._symbol_str(best_contract.symbol),
            target_weight=actual_target_weight,  # V2.4.1: Actual allocation, not 1.0
            source="OPT_INTRADAY",
            urgency=Urgency.IMMEDIATE,
            reason=reason,
            requested_quantity=num_contracts,  # V2.3.2: Pass calculated contracts
            metadata={
                "intraday_strategy": entry_strategy.value,
                "contract_price": best_contract.mid_price,
            },
        )

    def check_intraday_force_exit(
        self,
        current_hour: int,
        current_minute: int,
        current_price: float,
        ignore_hold_policy: bool = False,
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
        if self._intraday_position is None:
            return None

        # V2.3.3 FIX #3: Prevent duplicate exit signals while waiting for fill
        if self.has_pending_intraday_exit():
            return None

        force_hh, force_mm = self._get_intraday_force_exit_hhmm()
        force_exit_time = current_hour > force_hh or (
            current_hour == force_hh and current_minute >= force_mm
        )

        if not force_exit_time:
            return None

        symbol = self._intraday_position.contract.symbol
        symbol_str = self._symbol_str(symbol)
        if self.should_hold_intraday_overnight(self._intraday_position):
            if ignore_hold_policy:
                self.log(
                    f"INTRADAY_FORCE_EXIT_OVERRIDE_HOLD {symbol_str} | "
                    f"Strategy={getattr(self._intraday_position, 'entry_strategy', 'UNKNOWN')}",
                    trades_only=True,
                )
            else:
                if self.algorithm is not None and hasattr(self.algorithm, "Time"):
                    current_date = str(self.algorithm.Time.date())
                else:
                    current_date = datetime.utcnow().date().isoformat()
                if self._intraday_force_exit_hold_skip_log_date.get(symbol_str) != current_date:
                    live_dte = self._get_position_live_dte(self._intraday_position)
                    self.log(
                        f"INTRADAY_FORCE_EXIT_SKIP_HOLD {symbol_str} | "
                        f"Strategy={getattr(self._intraday_position, 'entry_strategy', 'UNKNOWN')} | "
                        f"LiveDTE={live_dte}",
                        trades_only=True,
                    )
                    self._intraday_force_exit_hold_skip_log_date[symbol_str] = current_date
                return None

        entry_price = self._intraday_position.entry_price
        num_contracts = max(1, int(self._intraday_position.num_contracts))

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

        # Keep intraday software exit lock aligned for expiring-day close signals.
        if self._intraday_position is not None and self._intraday_position is position:
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
        force_intraday: bool = False,
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

        entry_strategy = self._pending_entry_strategy or "SWING_SINGLE"

        # Recalculate stop and target based on actual fill price
        stop_price = fill_price * (1 - stop_pct)

        is_intraday_fill = self._pending_intraday_entry or force_intraday
        if is_intraday_fill:
            target_pct, strategy_floor = self._get_intraday_exit_profile(entry_strategy)
            target_price = fill_price * (1 + target_pct)
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
            self._intraday_position = position
            self._pending_intraday_entry = False  # Clear flag
            self._pending_intraday_entry_since = None
            # Count intraday trades only after a confirmed fill registration.
            intraday_dir = (
                OptionDirection.CALL
                if str(getattr(contract, "right", "")).upper() == "CALL"
                else OptionDirection.PUT
                if str(getattr(contract, "right", "")).upper() == "PUT"
                else None
            )
            self._increment_trade_counter(OptionsMode.INTRADAY, direction=intraday_dir)
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
            try:
                strategy = str(getattr(position, "entry_strategy", "") or "UNKNOWN")
            except Exception:
                strategy = "UNKNOWN"
            self._last_intraday_close_strategy = strategy
            self._last_intraday_close_time = (
                self.algorithm.Time if self.algorithm is not None else None
            )
            self.log(
                f"OPT: INTRADAY_POSITION_REMOVED {position.contract.symbol} | "
                f"Strategy={strategy}",
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
        )

        self._spread_neutrality_warn_by_key.pop(self._build_spread_key(spread), None)
        self._spread_positions.append(spread)
        self._spread_position = self._spread_positions[0] if self._spread_positions else None
        signature = (
            self._build_vass_signature(
                spread_type=spread.spread_type,
                direction=OptionDirection.CALL
                if "CALL" in str(spread.spread_type).upper()
                else OptionDirection.PUT,
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
        spread_dir = (
            OptionDirection.CALL
            if self._spread_direction_label(spread.spread_type) == "BULLISH"
            else OptionDirection.PUT
            if self._spread_direction_label(spread.spread_type) == "BEARISH"
            else None
        )
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
        self._pending_num_contracts = None
        self._pending_entry_score = None
        self.log(
            "OPT_MACRO_RECOVERY: Pending spread entry cancelled | Retry allowed",
            trades_only=True,
        )

    def has_pending_spread_entry(self) -> bool:
        """True when both pending spread legs are populated."""
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
        self._pending_num_contracts = None
        self._pending_entry_score = None
        self._pending_stop_pct = None
        self._pending_stop_price = None
        self._pending_target_price = None

    def _clear_stale_pending_intraday_entry_if_orphaned(self) -> None:
        """
        Clear stale pending intraday entry lock when no open broker order exists.

        Prevents long-lived E_INTRADAY_PENDING_ENTRY lock after missed/implicit
        broker cancel events while preserving normal in-flight order behavior.
        """
        if not self._pending_intraday_entry:
            return
        if self._intraday_position is not None:
            return
        if self.algorithm is None or not hasattr(self.algorithm, "Time"):
            return

        if self._pending_intraday_entry_since is None:
            self._pending_intraday_entry_since = self.algorithm.Time
            return

        stale_minutes = int(getattr(config, "INTRADAY_PENDING_ENTRY_STALE_MINUTES", 5))
        if stale_minutes <= 0:
            return

        age_minutes = (
            self.algorithm.Time - self._pending_intraday_entry_since
        ).total_seconds() / 60.0
        if age_minutes < stale_minutes:
            return

        pending_symbol = self.get_pending_entry_contract_symbol()
        has_open = False
        try:
            for open_order in self.algorithm.Transactions.GetOpenOrders():
                if getattr(open_order.Symbol, "SecurityType", None) != SecurityType.Option:
                    continue
                open_symbol = self._symbol_str(open_order.Symbol)
                if not pending_symbol or open_symbol == pending_symbol:
                    has_open = True
                    break
        except Exception:
            has_open = True

        if has_open:
            return

        self.cancel_pending_intraday_entry()
        self.log(
            f"OPT_MICRO_RECOVERY: Cleared stale pending intraday entry | "
            f"Symbol={pending_symbol or 'UNKNOWN'} | AgeMin={age_minutes:.1f}",
            trades_only=True,
        )

    def cancel_pending_intraday_entry(self) -> None:
        """
        V2.20: Clear pending intraday entry state after broker rejection.

        Resets intraday pending fields after broker rejection/cancel.
        Intraday counters are fill-based, so no decrement rollback is needed.
        Called by main._handle_order_rejection().
        """
        if self._pending_intraday_entry:
            self._pending_intraday_entry = False
            self._pending_intraday_entry_since = None
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

    def has_pending_intraday_entry(self) -> bool:
        """True when an intraday entry is currently pending."""
        return bool(self._pending_intraday_entry)

    def get_pending_entry_contract_symbol(self) -> str:
        """Best-effort symbol for current pending single-leg entry contract."""
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

        if (
            self._intraday_position is not None
            and self._intraday_position.contract is not None
            and self._symbol_str(self._intraday_position.contract.symbol) == symbol_norm
            and float(getattr(self._intraday_position, "stop_price", 0.0) or 0.0) > 0
            and float(getattr(self._intraday_position, "target_price", 0.0) or 0.0) > 0
        ):
            return {
                "entry_price": float(getattr(self._intraday_position, "entry_price", 0.0) or 0.0),
                "stop_price": float(getattr(self._intraday_position, "stop_price", 0.0) or 0.0),
                "target_price": float(getattr(self._intraday_position, "target_price", 0.0) or 0.0),
                "entry_strategy": str(
                    getattr(self._intraday_position, "entry_strategy", "UNKNOWN") or "UNKNOWN"
                ),
            }

        return self.get_pending_intraday_partial_oco_seed(symbol=symbol_norm, fill_price=fill_price)

    def get_pending_intraday_partial_oco_seed(
        self, symbol: str, fill_price: float
    ) -> Optional[Dict[str, Any]]:
        """
        Build temporary OCO pricing for a pending intraday entry partial fill.

        Used when an entry order partially fills before full fill registration.
        """
        if (
            not self._pending_intraday_entry
            or self._pending_contract is None
            or fill_price is None
            or float(fill_price) <= 0
        ):
            return None

        symbol_norm = self._symbol_str(symbol)
        pending_symbol = self._symbol_str(self._pending_contract.symbol)
        if not symbol_norm or symbol_norm != pending_symbol:
            return None

        entry_strategy = self._pending_entry_strategy or "SWING_SINGLE"
        stop_pct = self._pending_stop_pct if self._pending_stop_pct is not None else 0.20
        target_pct, strategy_floor = self._get_intraday_exit_profile(entry_strategy)
        if strategy_floor is not None and strategy_floor > 0:
            stop_pct = max(float(stop_pct), float(strategy_floor))

        entry_px = float(fill_price)
        return {
            "entry_price": entry_px,
            "stop_price": entry_px * (1 - float(stop_pct)),
            "target_price": entry_px * (1 + float(target_pct)),
            "entry_strategy": entry_strategy,
        }

    def has_pending_swing_entry(self) -> bool:
        """True when a single-leg swing entry is pending (not intraday)."""
        return self._pending_contract is not None and not self._pending_intraday_entry

    def has_pending_intraday_exit(self) -> bool:
        """True when an intraday close signal has already been emitted and is in-flight."""
        return bool(self._pending_intraday_exit)

    def mark_pending_intraday_exit(self, symbol: Optional[str] = None) -> bool:
        """
        Mark intraday close as pending to block duplicate software/force exits.

        Args:
            symbol: Optional symbol guard. When provided, lock is only set if it
                matches the tracked intraday position symbol.

        Returns:
            True when lock was set, else False.
        """
        if self._pending_intraday_exit:
            return False

        if symbol and self._intraday_position is not None and self._intraday_position.contract:
            try:
                expected = self._symbol_str(self._intraday_position.contract.symbol)
                actual = self._symbol_str(symbol)
                if expected != actual:
                    return False
            except Exception:
                return False

        self._pending_intraday_exit = True
        return True

    def cancel_pending_intraday_exit(self, symbol: Optional[str] = None) -> bool:
        """
        Clear pending intraday exit lock after a rejected/canceled close order.

        Args:
            symbol: Optional symbol guard. When provided, lock is cleared only if it
                matches the tracked intraday position symbol.

        Returns:
            True when lock was cleared, else False.
        """
        if not self._pending_intraday_exit:
            return False

        if symbol and self._intraday_position is not None and self._intraday_position.contract:
            try:
                expected = self._symbol_str(self._intraday_position.contract.symbol)
                actual = self._symbol_str(symbol)
                if expected != actual:
                    return False
            except Exception:
                return False

        self._pending_intraday_exit = False
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
                spread = spreads[0]

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
        if bool(getattr(config, "VASS_LOSS_BREAKER_ENABLED", False)):
            if is_win:
                self._vass_consecutive_losses = 0
                self._vass_loss_breaker_pause_until = None
            else:
                self._vass_consecutive_losses += 1
                threshold = int(getattr(config, "VASS_LOSS_BREAKER_THRESHOLD", 3))
                if self._vass_consecutive_losses >= threshold:
                    pause_days = max(1, int(getattr(config, "VASS_LOSS_BREAKER_PAUSE_DAYS", 1)))
                    if self.algorithm is not None and hasattr(self.algorithm, "Time"):
                        pause_until = self.algorithm.Time.date() + timedelta(days=pause_days)
                        self._vass_loss_breaker_pause_until = pause_until.isoformat()
                        self.log(
                            "VASS_LOSS_BREAKER_PAUSE | "
                            f"LossStreak={self._vass_consecutive_losses} | "
                            f"PauseUntil={self._vass_loss_breaker_pause_until}",
                            trades_only=True,
                        )
                    self._vass_consecutive_losses = 0

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

    def has_intraday_position(self) -> bool:
        """V2.3.2: Check if an intraday position exists (tracked separately for timed force close)."""
        return self._intraday_position is not None

    def get_intraday_position(self) -> Optional[OptionsPosition]:
        """V2.3.2: Get current intraday position."""
        return self._intraday_position

    def has_position(self) -> bool:
        """Check if any position exists (single-leg, spread, or intraday)."""
        return (
            self._position is not None
            or self.has_spread_position()
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

        if self.has_spread_position():
            self._spread_positions = []
            self._spread_position = None
            self._spread_neutrality_warn_by_key = {}
            self._spread_hold_guard_logged.clear()
            cleared.append("spread")

        if self._intraday_position is not None:
            self._intraday_position = None
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
        self._pending_intraday_exit = False
        self._pending_spread_long_leg = None
        self._pending_spread_short_leg = None
        self._pending_spread_width = None
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
            "intraday_trades_today": self._intraday_trades_today,
            "intraday_call_trades_today": self._intraday_call_trades_today,
            "intraday_put_trades_today": self._intraday_put_trades_today,
            "swing_trades_today": self._swing_trades_today,
            "total_options_trades_today": self._total_options_trades_today,
            "current_mode": self._current_mode.value,
            "micro_regime_state": self._micro_regime_engine.get_state().to_dict(),
            # V2.16-BT: Persist spread position for multi-day backtests
            # Mirror primary from canonical spread list to avoid dual-key drift.
            "spread_position": (primary_spread.to_dict() if primary_spread else None),
            "spread_positions": [s.to_dict() for s in self.get_spread_positions()],
            # Market open data
            "vix_at_open": self._vix_at_open,
            "spy_at_open": self._spy_at_open,
            "spy_gap_pct": self._spy_gap_pct,
            # V2.27: Win Rate Gate state
            "spread_result_history": self._spread_result_history,
            "win_rate_shutoff": self._win_rate_shutoff,
            "win_rate_shutoff_date": self._win_rate_shutoff_date,
            "paper_track_history": self._paper_track_history,
            "vass_consecutive_losses": self._vass_consecutive_losses,
            "vass_loss_breaker_pause_until": self._vass_loss_breaker_pause_until,
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
            elif self.should_hold_intraday_overnight(position):
                self._intraday_position = position
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
        else:
            self._intraday_position = None

        self._intraday_trades_today = state.get("intraday_trades_today", 0)
        self._intraday_call_trades_today = state.get("intraday_call_trades_today", 0)
        self._intraday_put_trades_today = state.get("intraday_put_trades_today", 0)
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

        # Market open data
        self._vix_at_open = state.get("vix_at_open", 0.0)
        self._spy_at_open = state.get("spy_at_open", 0.0)
        self._spy_gap_pct = state.get("spy_gap_pct", 0.0)

        # V2.27: Win Rate Gate state
        self._spread_result_history = state.get("spread_result_history", [])
        self._win_rate_shutoff = state.get("win_rate_shutoff", False)
        raw_shutoff_date = state.get("win_rate_shutoff_date")
        self._win_rate_shutoff_date = str(raw_shutoff_date)[:10] if raw_shutoff_date else None
        self._paper_track_history = state.get("paper_track_history", [])
        self._vass_consecutive_losses = int(state.get("vass_consecutive_losses", 0) or 0)
        raw_breaker_pause = state.get("vass_loss_breaker_pause_until")
        self._vass_loss_breaker_pause_until = (
            str(raw_breaker_pause)[:10] if raw_breaker_pause else None
        )
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
            }
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
        self._spread_positions = []
        self._spread_position = None
        self._intraday_trades_today = 0
        self._intraday_call_trades_today = 0
        self._intraday_put_trades_today = 0
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

        # V2.3.3: Reset pending intraday exit flag
        self._pending_intraday_exit = False
        self._win_rate_shutoff_date = None
        self._vass_consecutive_losses = 0
        self._vass_loss_breaker_pause_until = None
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

            # V2.3.3: Reset pending intraday exit flag
            self._pending_intraday_exit = False
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
            if self._intraday_position is not None:
                keep_position = self.should_hold_intraday_overnight(self._intraday_position)
                if not keep_position and self.algorithm is not None:
                    try:
                        sym = self._intraday_position.contract.symbol
                        broker_symbol = sym
                        if isinstance(sym, str):
                            broker_symbol = self.algorithm.Symbol(sym)
                        sec = self.algorithm.Portfolio[broker_symbol]
                        if sec is not None and sec.Invested and abs(int(sec.Quantity)) > 0:
                            self._intraday_position.num_contracts = abs(int(sec.Quantity))
                            keep_position = True
                    except Exception:
                        keep_position = False

                if keep_position:
                    self.log(
                        "OPT: DAILY_RESET_KEEP - preserving live intraday position",
                        trades_only=True,
                    )
                else:
                    self.log("OPT: WARNING - Intraday position found at daily reset, clearing")
                    self._intraday_position = None

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
        self, mode: OptionsMode, direction: Optional[OptionDirection] = None
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
            if direction == OptionDirection.CALL:
                self._intraday_call_trades_today += 1
            elif direction == OptionDirection.PUT:
                self._intraday_put_trades_today += 1
            self.log(
                f"TRADE_COUNTER: Intraday={self._intraday_trades_today}/{config.INTRADAY_MAX_TRADES_PER_DAY} | "
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
            if self._intraday_trades_today >= config.INTRADAY_MAX_TRADES_PER_DAY:
                detail = (
                    f"Intraday limit reached | "
                    f"{self._intraday_trades_today}/{config.INTRADAY_MAX_TRADES_PER_DAY}"
                )
                self.log(f"TRADE_LIMIT: {detail}")
                return reject("R_SLOT_INTRADAY_MAX", detail)
            per_direction_cap = int(getattr(config, "INTRADAY_MAX_TRADES_PER_DIRECTION_PER_DAY", 0))
            if per_direction_cap > 0 and direction is not None:
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
