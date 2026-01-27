"""
Trend Engine - Bollinger Band compression breakout strategy.

Captures multi-day momentum moves in 2× leveraged ETFs (QLD, SSO).
Identifies periods of low volatility (compression) followed by
directional breakouts, then rides the trend with trailing stops.

Entry: Bandwidth < 10% AND Close > Upper Band AND Regime >= 40
Exit: Close < Middle Band OR Chandelier Stop Hit OR Regime < 30

Spec: docs/07-trend-engine.md
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from AlgorithmImports import QCAlgorithm

import config
from models.enums import Urgency
from models.target_weight import TargetWeight
from utils.calculations import (
    atr_multiplier_for_profit,
    bandwidth,
    chandelier_stop,
    profit_pct,
)


@dataclass
class TrendPosition:
    """Tracks an active trend position."""

    symbol: str
    entry_price: float
    entry_date: str
    highest_high: float
    current_stop: float
    strategy_tag: str = "TREND"

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for persistence."""
        return {
            "symbol": self.symbol,
            "entry_price": self.entry_price,
            "entry_date": self.entry_date,
            "highest_high": self.highest_high,
            "current_stop": self.current_stop,
            "strategy_tag": self.strategy_tag,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TrendPosition":
        """Deserialize from persistence."""
        return cls(
            symbol=data["symbol"],
            entry_price=data["entry_price"],
            entry_date=data["entry_date"],
            highest_high=data["highest_high"],
            current_stop=data["current_stop"],
            strategy_tag=data.get("strategy_tag", "TREND"),
        )


@dataclass
class TrendSignal:
    """Result of trend signal check."""

    has_signal: bool
    symbol: Optional[str] = None
    is_entry: bool = False
    is_exit: bool = False
    exit_reason: Optional[str] = None
    urgency: Urgency = Urgency.EOD
    bandwidth_value: Optional[float] = None
    close_price: Optional[float] = None
    upper_band: Optional[float] = None
    middle_band: Optional[float] = None
    stop_level: Optional[float] = None


class TrendEngine:
    """
    Bollinger Band compression breakout engine.

    Trades QLD and SSO based on BB compression followed by
    breakout. Uses Chandelier trailing stops for exit protection.

    Note: This engine does NOT place orders. It only provides
    signals via TargetWeight objects for the Portfolio Router.
    """

    # Instruments traded by this engine
    INSTRUMENTS: List[str] = ["QLD", "SSO"]

    def __init__(self, algorithm: Optional["QCAlgorithm"] = None):
        """Initialize Trend Engine."""
        self.algorithm = algorithm
        self._positions: Dict[str, TrendPosition] = {}

    def log(self, message: str) -> None:
        """Log via algorithm or skip for testing."""
        if self.algorithm:
            self.algorithm.Log(message)

    def check_entry_signal(
        self,
        symbol: str,
        close: float,
        upper_band: float,
        middle_band: float,
        lower_band: float,
        regime_score: float,
        is_cold_start_active: bool,
        has_warm_entry: bool,
        atr: float,
        current_date: str,
    ) -> Optional[TargetWeight]:
        """
        Check for trend entry signal.

        Args:
            symbol: Symbol to check (QLD or SSO).
            close: Current closing price.
            upper_band: Bollinger upper band value.
            middle_band: Bollinger middle band (SMA).
            lower_band: Bollinger lower band value.
            regime_score: Current smoothed regime score.
            is_cold_start_active: True if in cold start period.
            has_warm_entry: True if warm entry position exists.
            atr: Current 14-period ATR.
            current_date: Current date string for logging.

        Returns:
            TargetWeight for entry, or None if no signal.
        """
        # Check if symbol is valid for this engine
        if symbol not in self.INSTRUMENTS:
            return None

        # Check if position already exists
        if symbol in self._positions:
            return None

        # Calculate bandwidth
        bw = bandwidth(upper_band, lower_band, middle_band)

        # Condition 1: Compression (bandwidth < threshold)
        if bw >= config.COMPRESSION_THRESHOLD:
            return None

        # Condition 2: Breakout (close > upper band)
        if close <= upper_band:
            return None

        # Condition 3: Regime score >= 40
        if regime_score < config.TREND_ENTRY_REGIME_MIN:
            self.log(
                f"TREND: {symbol} entry blocked - regime {regime_score:.1f} < {config.TREND_ENTRY_REGIME_MIN}"
            )
            return None

        # Condition 4: Not in cold start, OR has warm entry
        if is_cold_start_active and not has_warm_entry:
            self.log(f"TREND: {symbol} entry blocked - cold start active, no warm entry")
            return None

        # All conditions passed - generate entry signal
        reason = (
            f"BB Compression Breakout: Bandwidth={bw:.3f}, "
            f"Close={close:.2f} > Upper={upper_band:.2f}"
        )

        self.log(f"TREND: ENTRY_SIGNAL {symbol} | {reason} | Regime={regime_score:.1f}")

        return TargetWeight(
            symbol=symbol,
            target_weight=1.0,  # Full allocation to trend budget
            source="TREND",
            urgency=Urgency.EOD,
            reason=reason,
        )

    def check_exit_signals(
        self,
        symbol: str,
        close: float,
        high: float,
        middle_band: float,
        regime_score: float,
        atr: float,
    ) -> Optional[TargetWeight]:
        """
        Check for trend exit signals (band basis, regime).

        This is the EOD check for band basis and regime exits.
        Chandelier stop is checked separately via check_stop_hit.

        Args:
            symbol: Symbol to check.
            close: Current closing price.
            high: Current day's high price.
            middle_band: Bollinger middle band (SMA).
            regime_score: Current smoothed regime score.
            atr: Current 14-period ATR.

        Returns:
            TargetWeight for exit, or None if no exit signal.
        """
        if symbol not in self._positions:
            return None

        position = self._positions[symbol]

        # Update highest high
        if high > position.highest_high:
            position.highest_high = high

        # Update trailing stop
        self._update_chandelier_stop(position, atr)

        # Check exit conditions
        # Exit 1: Band basis - close < middle band
        if close < middle_band:
            reason = f"BAND_EXIT: Close (${close:.2f}) < Middle Band (${middle_band:.2f})"
            self.log(f"TREND: EXIT_SIGNAL {symbol} | {reason}")
            return TargetWeight(
                symbol=symbol,
                target_weight=0.0,
                source="TREND",
                urgency=Urgency.EOD,
                reason=reason,
            )

        # Exit 2: Regime exit - score < 30
        if regime_score < config.TREND_EXIT_REGIME:
            reason = f"REGIME_EXIT: Score ({regime_score:.1f}) < {config.TREND_EXIT_REGIME}"
            self.log(f"TREND: EXIT_SIGNAL {symbol} | {reason}")
            return TargetWeight(
                symbol=symbol,
                target_weight=0.0,
                source="TREND",
                urgency=Urgency.EOD,
                reason=reason,
            )

        return None

    def check_stop_hit(
        self,
        symbol: str,
        current_price: float,
    ) -> Optional[TargetWeight]:
        """
        Check if price hit the Chandelier stop (intraday check).

        Args:
            symbol: Symbol to check.
            current_price: Current price.

        Returns:
            TargetWeight for immediate exit, or None if not hit.
        """
        if symbol not in self._positions:
            return None

        position = self._positions[symbol]

        if current_price <= position.current_stop:
            reason = (
                f"STOP_HIT: Price (${current_price:.2f}) <= " f"Stop (${position.current_stop:.2f})"
            )
            self.log(f"TREND: EXIT_SIGNAL {symbol} | {reason} | IMMEDIATE")
            return TargetWeight(
                symbol=symbol,
                target_weight=0.0,
                source="TREND",
                urgency=Urgency.IMMEDIATE,
                reason=reason,
            )

        return None

    def _update_chandelier_stop(self, position: TrendPosition, atr: float) -> None:
        """
        Update the Chandelier trailing stop for a position.

        The stop only moves up, never down.
        """
        # Calculate current profit
        current_profit = profit_pct(position.entry_price, position.highest_high)

        # Get appropriate ATR multiplier based on profit level
        multiplier = atr_multiplier_for_profit(
            current_profit,
            config.PROFIT_TIGHT_PCT,
            config.PROFIT_TIGHTER_PCT,
            config.CHANDELIER_BASE_MULT,
            config.CHANDELIER_TIGHT_MULT,
            config.CHANDELIER_TIGHTER_MULT,
        )

        # Calculate new stop level
        new_stop = chandelier_stop(position.highest_high, atr, multiplier)

        # Stop only moves up, never down
        if new_stop > position.current_stop:
            old_stop = position.current_stop
            position.current_stop = new_stop
            self.log(
                f"TREND: STOP_UPDATE {position.symbol} | "
                f"${old_stop:.2f} -> ${new_stop:.2f} | "
                f"HH=${position.highest_high:.2f} | Mult={multiplier}"
            )

    def register_entry(
        self,
        symbol: str,
        entry_price: float,
        entry_date: str,
        atr: float,
        strategy_tag: str = "TREND",
    ) -> TrendPosition:
        """
        Register a new trend position after fill.

        Args:
            symbol: Symbol entered.
            entry_price: Fill price.
            entry_date: Entry date string.
            atr: Current ATR for initial stop.
            strategy_tag: "TREND" or "COLD_START".

        Returns:
            Created TrendPosition.
        """
        # Calculate initial stop
        initial_stop = chandelier_stop(entry_price, atr, config.CHANDELIER_BASE_MULT)

        position = TrendPosition(
            symbol=symbol,
            entry_price=entry_price,
            entry_date=entry_date,
            highest_high=entry_price,
            current_stop=initial_stop,
            strategy_tag=strategy_tag,
        )

        self._positions[symbol] = position

        self.log(
            f"TREND: POSITION_REGISTERED {symbol} | "
            f"Entry=${entry_price:.2f} | Stop=${initial_stop:.2f} | "
            f"Tag={strategy_tag}"
        )

        return position

    def remove_position(self, symbol: str) -> Optional[TrendPosition]:
        """
        Remove a position after exit.

        Args:
            symbol: Symbol to remove.

        Returns:
            Removed position, or None if not found.
        """
        if symbol in self._positions:
            position = self._positions.pop(symbol)
            self.log(f"TREND: POSITION_REMOVED {symbol}")
            return position
        return None

    def has_position(self, symbol: str) -> bool:
        """Check if a position exists for symbol."""
        return symbol in self._positions

    def get_position(self, symbol: str) -> Optional[TrendPosition]:
        """Get position for symbol."""
        return self._positions.get(symbol)

    def get_all_positions(self) -> Dict[str, TrendPosition]:
        """Get all active positions."""
        return self._positions.copy()

    def get_state_for_persistence(self) -> Dict[str, Any]:
        """Get state for ObjectStore."""
        return {"positions": {symbol: pos.to_dict() for symbol, pos in self._positions.items()}}

    def restore_state(self, state: Dict[str, Any]) -> None:
        """Restore state from ObjectStore."""
        positions_data = state.get("positions", {})
        self._positions = {
            symbol: TrendPosition.from_dict(data) for symbol, data in positions_data.items()
        }

    def reset(self) -> None:
        """Reset engine state (clear all positions)."""
        self._positions.clear()
        self.log("TREND: Engine reset - all positions cleared")

    def get_stop_level(self, symbol: str) -> Optional[float]:
        """Get current stop level for a position."""
        position = self._positions.get(symbol)
        return position.current_stop if position else None

    def get_highest_high(self, symbol: str) -> Optional[float]:
        """Get highest high for a position."""
        position = self._positions.get(symbol)
        return position.highest_high if position else None
