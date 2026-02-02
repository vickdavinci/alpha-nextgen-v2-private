"""
Trend Engine - V2.2 MA200 + ADX trend-following strategy.

Captures multi-day momentum moves in leveraged ETFs:
- QLD (20%) - 2× Nasdaq
- SSO (15%) - 2× S&P 500
- TNA (12%) - 3× Russell 2000 (small-cap diversification)
- FAS (8%)  - 3× Financials (sector diversification)

Uses MA200 for trend direction and ADX for momentum confirmation.
Rides trends with tiered Chandelier trailing stops.

V2 Entry: Close > MA200 AND ADX >= 25 (score_adx >= 0.50) AND Regime >= 40
V2 Exit: Close < MA200 OR ADX < 20 OR Chandelier Stop Hit OR Regime < 30

ADX Scoring (V2.1):
- ADX < 20:    0.25 (weak/choppy - LOW confidence)
- ADX 20-25:   0.50 (moderate - MEDIUM confidence)
- ADX 25-35:   0.75 (strong - HIGH confidence)
- ADX >= 35:   1.00 (very strong - BEST confidence)

V2.2 Changes:
- Added TNA (3× Russell 2000) for small-cap diversification
- Added FAS (3× Financials) for sector diversification
- Total allocation: 55% (down from 70%)
- Rationale: Lower QLD-SSO correlation benefits, more entry opportunities

Spec: docs/07-trend-engine.md, docs/v2-specs/V2_1_COMPLETE_ARCHITECTURE.txt
"""

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from AlgorithmImports import QCAlgorithm

import config


def _is_valid_float(value: float) -> bool:
    """Check if a float value is valid (not None, NaN, or infinite)."""
    if value is None:
        return False
    try:
        return not (math.isnan(value) or math.isinf(value))
    except (TypeError, ValueError):
        return False


from models.enums import Urgency
from models.target_weight import TargetWeight
from utils.calculations import atr_multiplier_for_profit, chandelier_stop, profit_pct


def adx_score(adx_value: float) -> float:
    """
    Calculate ADX confidence score per V2.1 specification.

    Args:
        adx_value: Current ADX(14) value (0-100 scale).

    Returns:
        Confidence score: 0.25 (weak) to 1.0 (very strong).
    """
    if adx_value >= config.ADX_STRONG_THRESHOLD:  # >= 35
        return 1.0
    elif adx_value >= config.ADX_MODERATE_THRESHOLD:  # >= 25
        return 0.75
    elif adx_value >= config.ADX_WEAK_THRESHOLD:  # >= 20
        return 0.50
    else:  # < 20
        return 0.25


def get_chandelier_multipliers(symbol: str) -> tuple:
    """
    Get Chandelier stop multipliers for a symbol.

    V2.3.8: 3x ETFs (TNA/FAS) use tighter stops because they swing 5-7% daily.
    2x ETFs (QLD/SSO) use standard multipliers.

    Args:
        symbol: Symbol to get multipliers for.

    Returns:
        Tuple of (base_mult, tight_mult, tighter_mult).
    """
    if symbol in config.TREND_3X_SYMBOLS:
        # 3x ETFs: Tighter stops to control volatility (PART 14 Pitfall 3)
        return (
            config.CHANDELIER_3X_BASE_MULT,
            config.CHANDELIER_3X_TIGHT_MULT,
            config.CHANDELIER_3X_TIGHTER_MULT,
        )
    else:
        # 2x ETFs: Standard multipliers
        return (
            config.CHANDELIER_BASE_MULT,
            config.CHANDELIER_TIGHT_MULT,
            config.CHANDELIER_TIGHTER_MULT,
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
    close_price: Optional[float] = None
    ma200_value: Optional[float] = None
    adx_value: Optional[float] = None
    adx_score: Optional[float] = None
    stop_level: Optional[float] = None


class TrendEngine:
    """
    V2.2 MA200 + ADX trend-following engine.

    Trades QLD, SSO, TNA, and FAS based on MA200 trend direction with ADX
    momentum confirmation. Uses Chandelier trailing stops for exit.

    V2.2 Instruments (55% total allocation):
    - QLD (20%) - 2× Nasdaq
    - SSO (15%) - 2× S&P 500
    - TNA (12%) - 3× Russell 2000 (small-cap diversification)
    - FAS (8%)  - 3× Financials (sector diversification)

    V2 Entry Logic:
    1. Close > MA200 (bullish trend)
    2. ADX >= 25 (score >= 0.50, sufficient momentum)
    3. Regime score >= 40 (favorable market)
    4. Not in cold start (unless warm entry)

    V2 Exit Logic:
    1. Close < MA200 (trend reversal)
    2. ADX < 20 (momentum exhaustion)
    3. Chandelier stop hit (risk management)
    4. Regime < 30 (deteriorating market)

    Note: This engine does NOT place orders. It only provides
    signals via TargetWeight objects for the Portfolio Router.
    """

    # V2.2: Instruments traded by this engine (expanded for diversification)
    INSTRUMENTS: List[str] = ["QLD", "SSO", "TNA", "FAS"]

    def __init__(self, algorithm: Optional["QCAlgorithm"] = None):
        """Initialize Trend Engine."""
        self.algorithm = algorithm
        self._positions: Dict[str, TrendPosition] = {}
        # V2.3.21: Track symbols with pending MOO orders to prevent duplicates
        # When MOO signal generated at 15:45, symbol added here
        # When fill received (9:30 next day), symbol moved to _positions
        self._pending_moo_symbols: set = set()

    def log(self, message: str) -> None:
        """Log via algorithm or skip for testing."""
        if self.algorithm:
            self.algorithm.Log(message)

    def check_entry_signal(
        self,
        symbol: str,
        close: float,
        ma200: float,
        adx: float,
        regime_score: float,
        is_cold_start_active: bool,
        has_warm_entry: bool,
        atr: float,
        current_date: str,
    ) -> Optional[TargetWeight]:
        """
        Check for V2 trend entry signal (MA200 + ADX confirmation).

        Args:
            symbol: Symbol to check (QLD or SSO).
            close: Current closing price.
            ma200: 200-period Simple Moving Average.
            adx: Current ADX(14) value.
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

        # V2.3.21: Check if MOO order already pending (submitted at 15:45, fills at 9:30)
        if symbol in self._pending_moo_symbols:
            return None

        # Validate indicator inputs (prevent crashes from None/NaN)
        if not _is_valid_float(close) or not _is_valid_float(ma200):
            self.log(f"TREND: {symbol} entry blocked - MA200/price not ready")
            return None

        if not _is_valid_float(adx):
            self.log(f"TREND: {symbol} entry blocked - ADX not ready")
            return None

        if not _is_valid_float(atr) or atr <= 0:
            self.log(f"TREND: {symbol} entry blocked - ATR not ready or zero")
            return None

        # Calculate ADX score
        score = adx_score(adx)

        # Condition 1: Price above MA200 (bullish trend)
        if close <= ma200:
            return None

        # Condition 2: ADX >= 25 (score >= 0.50, sufficient momentum)
        if score < 0.50:
            self.log(f"TREND: {symbol} entry blocked - ADX {adx:.1f} too weak (score={score:.2f})")
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
        confidence = "STRONG" if score >= 0.75 else "MODERATE"
        reason = (
            f"MA200+ADX Entry: Close={close:.2f} > MA200={ma200:.2f}, "
            f"ADX={adx:.1f} (score={score:.2f}, {confidence})"
        )

        self.log(f"TREND: ENTRY_SIGNAL {symbol} | {reason} | Regime={regime_score:.1f}")

        # V2.3.21: Mark symbol as pending MOO to prevent duplicate signals
        self._pending_moo_symbols.add(symbol)

        # V2.3.3: Use symbol-specific allocation from config (not 1.0)
        # This ensures QLD gets 20%, SSO 15%, TNA 12%, FAS 8% as designed
        symbol_weight = config.TREND_SYMBOL_ALLOCATIONS.get(symbol, 0.20)

        return TargetWeight(
            symbol=symbol,
            target_weight=symbol_weight,  # V2.3.3: Symbol-specific allocation
            source="TREND",
            urgency=Urgency.EOD,
            reason=reason,
        )

    def check_exit_signals(
        self,
        symbol: str,
        close: float,
        high: float,
        ma200: float,
        adx: float,
        regime_score: float,
        atr: float,
        sma50: Optional[float] = None,
    ) -> Optional[TargetWeight]:
        """
        Check for trend exit signals.

        V2.4: If TREND_USE_SMA50_EXIT is True, uses SMA50 + Hard Stop logic.
        Otherwise, uses original MA200/ADX/Regime + Chandelier stop logic.

        SMA50 Benefits:
        - Allows 3% minor volatility without exit (if above SMA50)
        - Longer holding periods (30-90 days vs 5-15 days)
        - Cleaner logic than tiered ATR multipliers

        Args:
            symbol: Symbol to check.
            close: Current closing price.
            high: Current day's high price.
            ma200: 200-period Simple Moving Average.
            adx: Current ADX(14) value.
            regime_score: Current smoothed regime score.
            atr: Current 14-period ATR.
            sma50: V2.4 - 50-period SMA for structural trend exit (optional).

        Returns:
            TargetWeight for exit, or None if no exit signal.
        """
        if symbol not in self._positions:
            return None

        # Validate indicator inputs (prevent crashes from None/NaN)
        if not _is_valid_float(close) or not _is_valid_float(high):
            self.log(f"TREND: {symbol} exit check skipped - price data not ready")
            return None

        position = self._positions[symbol]

        # Update highest high (used by both exit modes)
        if high > position.highest_high:
            position.highest_high = high

        # V2.4: SMA50 + Hard Stop exit mode
        if config.TREND_USE_SMA50_EXIT and sma50 is not None and _is_valid_float(sma50):
            return self._check_sma50_exit(symbol, close, sma50, position, regime_score)

        # V2.2: Original Chandelier exit mode (fallback)
        return self._check_chandelier_exit(symbol, close, ma200, adx, regime_score, atr, position)

    def _check_sma50_exit(
        self,
        symbol: str,
        close: float,
        sma50: float,
        position: TrendPosition,
        regime_score: float,
    ) -> Optional[TargetWeight]:
        """
        V2.4: SMA50 + Hard Stop exit logic.

        Exit Conditions:
        1. Close < SMA50 * (1 - buffer) - Structural trend break
        2. Loss from entry >= Hard Stop % - Risk management
        3. Regime < 30 - Market deterioration (kept from V2.2)

        Allows minor volatility (3% drops) without exit if price stays above SMA50.
        """
        # Exit 1: SMA50 structural trend break
        sma50_exit_level = sma50 * (1 - config.TREND_SMA_EXIT_BUFFER)
        if close < sma50_exit_level:
            reason = (
                f"SMA50_BREAK: Close ${close:.2f} < SMA50 ${sma50:.2f} * "
                f"(1 - {config.TREND_SMA_EXIT_BUFFER:.0%}) = ${sma50_exit_level:.2f}"
            )
            self.log(f"TREND: EXIT_SIGNAL {symbol} | {reason}")
            return TargetWeight(
                symbol=symbol,
                target_weight=0.0,
                source="TREND",
                urgency=Urgency.EOD,
                reason=reason,
            )

        # Exit 2: Hard stop from entry (asset-specific)
        hard_stop_pct = config.TREND_HARD_STOP_PCT.get(symbol, 0.15)
        if position.entry_price > 0:
            loss_pct = (position.entry_price - close) / position.entry_price
            if loss_pct >= hard_stop_pct:
                reason = (
                    f"HARD_STOP: Loss {loss_pct:.1%} >= {hard_stop_pct:.0%} | "
                    f"Entry ${position.entry_price:.2f} -> ${close:.2f}"
                )
                self.log(f"TREND: EXIT_SIGNAL {symbol} | {reason}")
                return TargetWeight(
                    symbol=symbol,
                    target_weight=0.0,
                    source="TREND",
                    urgency=Urgency.IMMEDIATE,  # Hard stop is urgent
                    reason=reason,
                )

        # Exit 3: Regime deterioration (kept from V2.2)
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

        return None  # Hold position - above SMA50, no hard stop hit

    def _check_chandelier_exit(
        self,
        symbol: str,
        close: float,
        ma200: float,
        adx: float,
        regime_score: float,
        atr: float,
        position: TrendPosition,
    ) -> Optional[TargetWeight]:
        """
        V2.2: Original Chandelier trailing stop exit logic.

        Exit Conditions:
        1. Close < MA200 - Trend reversal
        2. ADX < threshold - Momentum exhaustion
        3. Regime < 30 - Market deterioration

        Note: Chandelier stop hit is checked separately via check_stop_hit().
        """
        # Validate indicators for Chandelier mode
        if not _is_valid_float(ma200) or not _is_valid_float(adx):
            self.log(f"TREND: {symbol} exit check skipped - indicators not ready")
            return None

        if not _is_valid_float(atr) or atr <= 0:
            atr = 0.0  # Will skip stop update

        # Update trailing stop
        self._update_chandelier_stop(position, atr)

        # Exit 1: MA200 exit - close < MA200 (trend reversal)
        if close < ma200:
            reason = f"MA200_EXIT: Close (${close:.2f}) < MA200 (${ma200:.2f})"
            self.log(f"TREND: EXIT_SIGNAL {symbol} | {reason}")
            return TargetWeight(
                symbol=symbol,
                target_weight=0.0,
                source="TREND",
                urgency=Urgency.EOD,
                reason=reason,
            )

        # Exit 2: ADX exit - momentum exhaustion
        if adx < config.TREND_ADX_EXIT_THRESHOLD:
            reason = f"ADX_EXIT: ADX ({adx:.1f}) < {config.TREND_ADX_EXIT_THRESHOLD}"
            self.log(f"TREND: EXIT_SIGNAL {symbol} | {reason}")
            return TargetWeight(
                symbol=symbol,
                target_weight=0.0,
                source="TREND",
                urgency=Urgency.EOD,
                reason=reason,
            )

        # Exit 3: Regime exit - score < 30
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

        V2.3.8: Uses symbol-specific multipliers - 3x ETFs (TNA/FAS) get
        tighter stops because they swing 5-7% daily.
        """
        # Skip if ATR not valid
        if not _is_valid_float(atr) or atr <= 0:
            return

        # Calculate current profit
        current_profit = profit_pct(position.entry_price, position.highest_high)

        # V2.3.8: Get symbol-specific multipliers (3x ETFs use tighter stops)
        base_mult, tight_mult, tighter_mult = get_chandelier_multipliers(position.symbol)

        # Get appropriate ATR multiplier based on profit level
        multiplier = atr_multiplier_for_profit(
            current_profit,
            config.PROFIT_TIGHT_PCT,
            config.PROFIT_TIGHTER_PCT,
            base_mult,
            tight_mult,
            tighter_mult,
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

        V2.3.8: Uses symbol-specific multipliers - 3x ETFs (TNA/FAS) get
        tighter initial stops because they swing 5-7% daily.

        Args:
            symbol: Symbol entered.
            entry_price: Fill price.
            entry_date: Entry date string.
            atr: Current ATR for initial stop.
            strategy_tag: "TREND" or "COLD_START".

        Returns:
            Created TrendPosition.
        """
        # V2.3.8: Get symbol-specific multiplier (3x ETFs use tighter stops)
        base_mult, _, _ = get_chandelier_multipliers(symbol)

        # Calculate initial stop
        initial_stop = chandelier_stop(entry_price, atr, base_mult)

        position = TrendPosition(
            symbol=symbol,
            entry_price=entry_price,
            entry_date=entry_date,
            highest_high=entry_price,
            current_stop=initial_stop,
            strategy_tag=strategy_tag,
        )

        self._positions[symbol] = position

        # V2.3.21: Clear pending MOO flag now that position is registered
        self._pending_moo_symbols.discard(symbol)

        self.log(
            f"TREND: POSITION_REGISTERED {symbol} | "
            f"Entry=${entry_price:.2f} | Stop=${initial_stop:.2f} | "
            f"Mult={base_mult} | Tag={strategy_tag}"
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
        # V2.3.21: Also clear any pending MOO for this symbol
        self._pending_moo_symbols.discard(symbol)

        if symbol in self._positions:
            position = self._positions.pop(symbol)
            self.log(f"TREND: POSITION_REMOVED {symbol}")
            return position
        return None

    def cancel_pending_moo(self, symbol: str) -> None:
        """
        V2.3.21: Cancel pending MOO tracking for a symbol.

        Called when MOO order is cancelled or rejected.

        Args:
            symbol: Symbol to cancel pending MOO for.
        """
        if symbol in self._pending_moo_symbols:
            self._pending_moo_symbols.discard(symbol)
            self.log(f"TREND: PENDING_MOO_CANCELLED {symbol}")

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
