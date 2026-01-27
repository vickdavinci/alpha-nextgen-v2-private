"""
Mean Reversion Engine - Intraday oversold bounce strategy.

Captures intraday bounces in 3× leveraged ETFs (TQQQ, SOXL) when
panic selling drives prices to extreme oversold levels.

Entry: RSI(5) < 25 AND Drop > 2.5% AND Volume > 1.2× AND Regime >= 40
Exit: +2% target OR -2% stop OR 3:45 PM time exit

CRITICAL: All positions must be closed by 3:45 PM. No overnight holds.

Spec: docs/08-mean-reversion-engine.md
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from AlgorithmImports import QCAlgorithm

import config
from models.enums import Urgency
from models.target_weight import TargetWeight


@dataclass
class MRPosition:
    """Tracks an active mean reversion position."""

    symbol: str
    entry_price: float
    entry_time: str
    vwap_at_entry: float
    target_price: float
    stop_price: float

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for persistence."""
        return {
            "symbol": self.symbol,
            "entry_price": self.entry_price,
            "entry_time": self.entry_time,
            "vwap_at_entry": self.vwap_at_entry,
            "target_price": self.target_price,
            "stop_price": self.stop_price,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MRPosition":
        """Deserialize from persistence."""
        return cls(
            symbol=data["symbol"],
            entry_price=data["entry_price"],
            entry_time=data["entry_time"],
            vwap_at_entry=data["vwap_at_entry"],
            target_price=data["target_price"],
            stop_price=data["stop_price"],
        )


class MeanReversionEngine:
    """
    Intraday oversold bounce engine.

    Trades TQQQ and SOXL based on extreme oversold conditions.
    All positions must be closed by 3:45 PM - no overnight holds.

    Note: This engine does NOT place orders. It only provides
    signals via TargetWeight objects for the Portfolio Router.
    """

    # Instruments traded by this engine (scan order matters)
    INSTRUMENTS: List[str] = ["TQQQ", "SOXL"]

    def __init__(self, algorithm: Optional["QCAlgorithm"] = None):
        """Initialize Mean Reversion Engine."""
        self.algorithm = algorithm
        self._position: Optional[MRPosition] = None

    def log(self, message: str) -> None:
        """Log via algorithm or skip for testing."""
        if self.algorithm:
            self.algorithm.Log(message)

    def check_entry_signal(
        self,
        symbol: str,
        current_price: float,
        open_price: float,
        rsi_value: float,
        current_volume: float,
        avg_volume: float,
        vwap: float,
        regime_score: float,
        days_running: int,
        gap_filter_triggered: bool,
        vol_shock_active: bool,
        time_guard_active: bool,
        current_hour: int,
        current_minute: int,
    ) -> Optional[TargetWeight]:
        """
        Check for mean reversion entry signal.

        Args:
            symbol: Symbol to check (TQQQ or SOXL).
            current_price: Current price.
            open_price: Today's opening price.
            rsi_value: Current RSI(5) value.
            current_volume: Current period volume.
            avg_volume: 20-period average volume.
            vwap: Current VWAP value.
            regime_score: Current smoothed regime score.
            days_running: Days since algorithm start.
            gap_filter_triggered: True if gap filter is active.
            vol_shock_active: True if vol shock pause is active.
            time_guard_active: True if time guard (Fed window) is active.
            current_hour: Current hour (0-23) Eastern.
            current_minute: Current minute (0-59).

        Returns:
            TargetWeight for entry, or None if no signal.
        """
        # Check if symbol is valid for this engine
        if symbol not in self.INSTRUMENTS:
            return None

        # Only one MR position at a time
        if self._position is not None:
            self.log(
                f"MR: {symbol} entry blocked - already have position in {self._position.symbol}"
            )
            return None

        # Parse time window
        start_hour, start_minute = self._parse_time(config.MR_WINDOW_START)
        end_hour, end_minute = self._parse_time(config.MR_WINDOW_END)

        # Condition 1: Time window (10:00 AM - 3:00 PM)
        current_time_mins = current_hour * 60 + current_minute
        start_time_mins = start_hour * 60 + start_minute
        end_time_mins = end_hour * 60 + end_minute

        if current_time_mins < start_time_mins or current_time_mins >= end_time_mins:
            return None

        # Condition 2: Safeguards clear
        if gap_filter_triggered:
            self.log(f"MR: {symbol} entry blocked - gap filter active")
            return None

        if vol_shock_active:
            self.log(f"MR: {symbol} entry blocked - vol shock active")
            return None

        if time_guard_active:
            self.log(f"MR: {symbol} entry blocked - time guard active")
            return None

        # Condition 3: RSI oversold (RSI < 25)
        if rsi_value >= config.RSI_THRESHOLD:
            return None

        # Condition 4: Price drop > 2.5% from open
        if open_price <= 0:
            return None
        drop_pct = (open_price - current_price) / open_price
        if drop_pct <= config.MR_DROP_THRESHOLD:
            return None

        # Condition 5: Volume confirmation (> 1.2× average)
        if avg_volume <= 0:
            return None
        volume_ratio = current_volume / avg_volume
        if volume_ratio <= config.MR_VOLUME_MULT:
            return None

        # Condition 6: Regime score >= 40
        if regime_score < config.MR_REGIME_MIN:
            self.log(
                f"MR: {symbol} entry blocked - regime {regime_score:.1f} < {config.MR_REGIME_MIN}"
            )
            return None

        # Condition 7: Not in cold start (days >= 5)
        if days_running < config.COLD_START_DAYS:
            self.log(
                f"MR: {symbol} entry blocked - cold start (day {days_running} < {config.COLD_START_DAYS})"
            )
            return None

        # All conditions passed - generate entry signal
        reason = (
            f"MR Entry: RSI={rsi_value:.1f}, " f"Drop={drop_pct:.1%}, Volume={volume_ratio:.1f}x"
        )

        self.log(
            f"MR: ENTRY_SIGNAL {symbol} | {reason} | "
            f"Price=${current_price:.2f} | Regime={regime_score:.1f}"
        )

        return TargetWeight(
            symbol=symbol,
            target_weight=1.0,  # Full allocation to MR budget
            source="MR",
            urgency=Urgency.IMMEDIATE,
            reason=reason,
        )

    def check_exit_signals(
        self,
        current_price: float,
        current_hour: int,
        current_minute: int,
    ) -> Optional[TargetWeight]:
        """
        Check for mean reversion exit signals.

        Args:
            current_price: Current price.
            current_hour: Current hour (0-23) Eastern.
            current_minute: Current minute (0-59).

        Returns:
            TargetWeight for exit, or None if no exit signal.
        """
        if self._position is None:
            return None

        symbol = self._position.symbol
        entry_price = self._position.entry_price

        # Calculate P&L percentage
        pnl_pct = (current_price - entry_price) / entry_price

        # Exit 1: Target hit (+2% or VWAP)
        if current_price >= self._position.target_price:
            reason = f"TARGET_HIT +{pnl_pct:.1%} (Price: ${current_price:.2f})"
            self.log(f"MR: EXIT_SIGNAL {symbol} | {reason}")
            return TargetWeight(
                symbol=symbol,
                target_weight=0.0,
                source="MR",
                urgency=Urgency.IMMEDIATE,
                reason=reason,
            )

        # Also check VWAP target
        if current_price >= self._position.vwap_at_entry:
            reason = f"VWAP_HIT +{pnl_pct:.1%} (Price: ${current_price:.2f}, VWAP: ${self._position.vwap_at_entry:.2f})"
            self.log(f"MR: EXIT_SIGNAL {symbol} | {reason}")
            return TargetWeight(
                symbol=symbol,
                target_weight=0.0,
                source="MR",
                urgency=Urgency.IMMEDIATE,
                reason=reason,
            )

        # Exit 2: Stop hit (-2%)
        if current_price <= self._position.stop_price:
            reason = f"STOP_HIT {pnl_pct:.1%} (Price: ${current_price:.2f})"
            self.log(f"MR: EXIT_SIGNAL {symbol} | {reason}")
            return TargetWeight(
                symbol=symbol,
                target_weight=0.0,
                source="MR",
                urgency=Urgency.IMMEDIATE,
                reason=reason,
            )

        # Exit 3: Time exit (3:45 PM)
        exit_hour, exit_minute = self._parse_time(config.MR_FORCE_EXIT_TIME)
        current_time_mins = current_hour * 60 + current_minute
        exit_time_mins = exit_hour * 60 + exit_minute

        if current_time_mins >= exit_time_mins:
            reason = f"TIME_EXIT 15:45 (P&L: {pnl_pct:+.1%})"
            self.log(f"MR: EXIT_SIGNAL {symbol} | {reason} | FORCED")
            return TargetWeight(
                symbol=symbol,
                target_weight=0.0,
                source="MR",
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
        Check if force exit time has been reached (3:45 PM).

        This is a separate method for the 15:45 scheduled check.

        Args:
            current_hour: Current hour (0-23) Eastern.
            current_minute: Current minute (0-59).
            current_price: Current price for P&L calculation.

        Returns:
            TargetWeight for forced exit, or None if not time yet.
        """
        if self._position is None:
            return None

        exit_hour, exit_minute = self._parse_time(config.MR_FORCE_EXIT_TIME)

        if current_hour > exit_hour or (
            current_hour == exit_hour and current_minute >= exit_minute
        ):
            symbol = self._position.symbol
            entry_price = self._position.entry_price
            pnl_pct = (current_price - entry_price) / entry_price

            reason = f"TIME_EXIT 15:45 (P&L: {pnl_pct:+.1%})"
            self.log(f"MR: FORCE_EXIT {symbol} | {reason}")

            return TargetWeight(
                symbol=symbol,
                target_weight=0.0,
                source="MR",
                urgency=Urgency.IMMEDIATE,
                reason=reason,
            )

        return None

    def register_entry(
        self,
        symbol: str,
        entry_price: float,
        entry_time: str,
        vwap: float,
    ) -> MRPosition:
        """
        Register a new mean reversion position after fill.

        Args:
            symbol: Symbol entered (TQQQ or SOXL).
            entry_price: Fill price.
            entry_time: Entry timestamp string.
            vwap: VWAP at entry time.

        Returns:
            Created MRPosition.
        """
        # Calculate target and stop prices
        target_price = entry_price * (1 + config.MR_TARGET_PCT)
        stop_price = entry_price * (1 - config.MR_STOP_PCT)

        position = MRPosition(
            symbol=symbol,
            entry_price=entry_price,
            entry_time=entry_time,
            vwap_at_entry=vwap,
            target_price=target_price,
            stop_price=stop_price,
        )

        self._position = position

        self.log(
            f"MR: POSITION_REGISTERED {symbol} | "
            f"Entry=${entry_price:.2f} | "
            f"Target=${target_price:.2f} | "
            f"Stop=${stop_price:.2f} | "
            f"VWAP=${vwap:.2f}"
        )

        return position

    def remove_position(self) -> Optional[MRPosition]:
        """
        Remove the current position after exit.

        Returns:
            Removed position, or None if no position existed.
        """
        if self._position is not None:
            position = self._position
            self._position = None
            self.log(f"MR: POSITION_REMOVED {position.symbol}")
            return position
        return None

    def has_position(self) -> bool:
        """Check if a position exists."""
        return self._position is not None

    def get_position(self) -> Optional[MRPosition]:
        """Get current position."""
        return self._position

    def get_position_symbol(self) -> Optional[str]:
        """Get symbol of current position, if any."""
        return self._position.symbol if self._position else None

    def get_state_for_persistence(self) -> Dict[str, Any]:
        """Get state for ObjectStore."""
        if self._position:
            return {"position": self._position.to_dict()}
        return {"position": None}

    def restore_state(self, state: Dict[str, Any]) -> None:
        """Restore state from ObjectStore."""
        position_data = state.get("position")
        if position_data:
            self._position = MRPosition.from_dict(position_data)
        else:
            self._position = None

    def reset(self) -> None:
        """Reset engine state (clear position)."""
        self._position = None
        self.log("MR: Engine reset - position cleared")

    def _parse_time(self, time_str: str) -> tuple:
        """Parse time string (HH:MM) to (hour, minute) tuple."""
        parts = time_str.split(":")
        return int(parts[0]), int(parts[1])

    def get_entry_price(self) -> Optional[float]:
        """Get entry price of current position."""
        return self._position.entry_price if self._position else None

    def get_target_price(self) -> Optional[float]:
        """Get target price of current position."""
        return self._position.target_price if self._position else None

    def get_stop_price(self) -> Optional[float]:
        """Get stop price of current position."""
        return self._position.stop_price if self._position else None

    def get_vwap_at_entry(self) -> Optional[float]:
        """Get VWAP at entry time."""
        return self._position.vwap_at_entry if self._position else None
