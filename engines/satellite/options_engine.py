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
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MicroRegimeState":
        """Deserialize from persistence."""
        return cls(
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
        if vix_value < 15:
            return VIXLevel.LOW, config.MICRO_SCORE_VIX_VERY_CALM
        elif vix_value < 18:
            return VIXLevel.LOW, config.MICRO_SCORE_VIX_CALM
        elif vix_value < config.VIX_LEVEL_LOW_MAX:
            return VIXLevel.LOW, config.MICRO_SCORE_VIX_NORMAL
        elif vix_value < 23:
            return VIXLevel.MEDIUM, config.MICRO_SCORE_VIX_ELEVATED
        elif vix_value < config.VIX_LEVEL_MEDIUM_MAX:
            return VIXLevel.MEDIUM, config.MICRO_SCORE_VIX_HIGH
        else:
            return VIXLevel.HIGH, config.MICRO_SCORE_VIX_EXTREME

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
    # STRATEGY RECOMMENDATION
    # =========================================================================

    def recommend_strategy(
        self,
        micro_regime: MicroRegime,
        micro_score: float,
        vix_current: float,
        qqq_move_pct: float,
    ) -> IntradayStrategy:
        """
        Recommend intraday strategy based on regime and score.

        Args:
            micro_regime: Current micro regime classification.
            micro_score: Current micro score.
            vix_current: Current VIX value.
            qqq_move_pct: QQQ move from open (absolute).

        Returns:
            Recommended IntradayStrategy.
        """
        # Danger regimes: No trade or protective only
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
                return IntradayStrategy.PROTECTIVE_PUTS
            return IntradayStrategy.NO_TRADE

        # Whipsaw/choppy: Credits only
        choppy_regimes = {
            MicroRegime.CHOPPY_LOW,
            MicroRegime.CAUTIOUS,
            MicroRegime.WORSENING,
        }
        if micro_regime in choppy_regimes:
            if vix_current >= config.INTRADAY_CREDIT_MIN_VIX:
                return IntradayStrategy.CREDIT_SPREAD
            return IntradayStrategy.NO_TRADE

        # Prime/Good MR: Debit Fade
        mr_regimes = {
            MicroRegime.PERFECT_MR,
            MicroRegime.GOOD_MR,
            MicroRegime.NORMAL,
            MicroRegime.RECOVERING,
            MicroRegime.IMPROVING,
        }
        if micro_regime in mr_regimes:
            if micro_score >= config.MICRO_SCORE_PRIME_MR:
                return IntradayStrategy.DEBIT_FADE
            elif micro_score >= config.MICRO_SCORE_GOOD_MR:
                return IntradayStrategy.DEBIT_FADE
            elif micro_score >= config.MICRO_SCORE_MODERATE:
                if vix_current >= config.INTRADAY_CREDIT_MIN_VIX:
                    return IntradayStrategy.CREDIT_SPREAD
                return IntradayStrategy.DEBIT_FADE
            else:
                return IntradayStrategy.CREDIT_SPREAD

        # Transition/Caution: Reduced activity
        caution_regimes = {
            MicroRegime.CAUTION_LOW,
            MicroRegime.TRANSITION,
        }
        if micro_regime in caution_regimes:
            if micro_score >= config.MICRO_SCORE_MODERATE:
                return IntradayStrategy.CREDIT_SPREAD
            return IntradayStrategy.NO_TRADE

        # Momentum regimes: ITM options or puts
        momentum_regimes = {
            MicroRegime.DETERIORATING,
            MicroRegime.ELEVATED,
            MicroRegime.WORSENING_HIGH,
            MicroRegime.PANIC_EASING,
            MicroRegime.CALMING,
        }
        if micro_regime in momentum_regimes:
            if vix_current > config.INTRADAY_ITM_MIN_VIX:
                if qqq_move_pct >= config.INTRADAY_ITM_MIN_MOVE:
                    return IntradayStrategy.ITM_MOMENTUM
            if vix_current >= config.INTRADAY_CREDIT_MIN_VIX:
                return IntradayStrategy.CREDIT_SPREAD
            return IntradayStrategy.NO_TRADE

        return IntradayStrategy.NO_TRADE

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
    ) -> MicroRegimeState:
        """
        Full update cycle for Micro Regime Engine.

        Should be called every 15-30 minutes during intraday trading.

        Args:
            vix_current: Current VIX value.
            vix_open: VIX at market open.
            qqq_current: Current QQQ price.
            qqq_open: QQQ at market open.
            current_time: Current timestamp string.
            move_duration_minutes: How long the current move has taken.

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

        # Classify VIX level and direction
        self._state.vix_level, _ = self.classify_vix_level(vix_current)
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

        # Calculate QQQ move
        if qqq_open > 0:
            self._state.qqq_move_pct = abs((qqq_current - qqq_open) / qqq_open * 100)

        # Recommend strategy
        self._state.recommended_strategy = self.recommend_strategy(
            self._state.micro_regime,
            self._state.micro_score,
            vix_current,
            self._state.qqq_move_pct,
        )

        self._state.last_update = current_time

        self.log(
            f"Update: VIX={vix_current:.1f} ({self._state.vix_direction.value}) | "
            f"Regime={self._state.micro_regime.value} | "
            f"Score={self._state.micro_score:.0f} | "
            f"Strategy={self._state.recommended_strategy.value}"
        )

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

        # Legacy single position (for backwards compatibility)
        self._position: Optional[OptionsPosition] = None

        # Trade counters
        self._trades_today: int = 0
        self._intraday_trades_today: int = 0
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

    def log(self, message: str, trades_only: bool = False) -> None:
        """
        Log via algorithm with LiveMode awareness.

        Args:
            message: Log message to output.
            trades_only: If True, always log (for trade entries/exits).
                        If False, only log in LiveMode (for diagnostics).
        """
        if self.algorithm:
            # Only show diagnostic logs in LiveMode, always show trade logs
            if trades_only or self.algorithm.LiveMode:
                self.algorithm.Log(message)

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
    ) -> tuple:
        """
        Calculate position size based on entry score and 1% risk.

        Args:
            entry_score: Total entry score (3.0-4.0).
            premium: Option premium per contract.
            portfolio_value: Total portfolio value.

        Returns:
            Tuple of (num_contracts, stop_pct, stop_price, target_price).
        """
        # Get tier parameters
        tier = self.get_stop_tier(entry_score)
        stop_pct = tier["stop_pct"]
        base_contracts = tier["contracts"]

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
    ) -> Optional[TargetWeight]:
        """
        Check for options entry signal.

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

        Returns:
            TargetWeight for entry, or None if no signal.
        """
        # Check if already have a position
        if self._position is not None:
            return None

        # Check max trades per day
        if current_date == self._last_trade_date:
            if self._trades_today >= config.OPTIONS_MAX_TRADES_PER_DAY:
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

        # Validate delta range (0.40-0.60 for ATM contracts per spec)
        contract_delta = abs(best_contract.delta)  # Use absolute value
        if contract_delta < config.OPTIONS_DELTA_MIN:
            self.log(
                f"OPT: Entry blocked - Delta {contract_delta:.2f} < "
                f"min {config.OPTIONS_DELTA_MIN} (too far OTM)"
            )
            return None

        if contract_delta > config.OPTIONS_DELTA_MAX:
            self.log(
                f"OPT: Entry blocked - Delta {contract_delta:.2f} > "
                f"max {config.OPTIONS_DELTA_MAX} (too deep ITM)"
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
        premium = best_contract.mid_price
        num_contracts, stop_pct, stop_price, target_price = self.calculate_position_size(
            entry_score=entry_score.total,
            premium=premium,
            portfolio_value=portfolio_value,
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

        reason = (
            f"OPT Entry: Score={entry_score.total:.2f} "
            f"({entry_score.score_adx:.2f}+{entry_score.score_momentum:.2f}+"
            f"{entry_score.score_iv:.2f}+{entry_score.score_liquidity:.2f}), "
            f"{best_contract.direction.value} {best_contract.strike}, "
            f"x{num_contracts}, Stop={stop_pct:.0%}"
        )

        self.log(
            f"OPT: ENTRY_SIGNAL | {reason} | "
            f"Premium=${premium:.2f} | Target=${target_price:.2f} | "
            f"Stop=${stop_price:.2f}",
            trades_only=True,
        )

        return TargetWeight(
            symbol=best_contract.symbol,
            target_weight=1.0,  # Full allocation to options budget
            source="OPT",
            urgency=Urgency.IMMEDIATE,
            reason=reason,
        )

    # =========================================================================
    # EXIT SIGNALS
    # =========================================================================

    def check_exit_signals(
        self,
        current_price: float,
    ) -> Optional[TargetWeight]:
        """
        Check for options exit signals.

        Args:
            current_price: Current option price.

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

    def get_mode_allocation(self, mode: OptionsMode, portfolio_value: float) -> float:
        """
        Get allocation for a specific mode.

        Args:
            mode: Operating mode.
            portfolio_value: Total portfolio value.

        Returns:
            Dollar allocation for the mode.
        """
        if mode == OptionsMode.INTRADAY:
            return portfolio_value * config.OPTIONS_INTRADAY_ALLOCATION
        return portfolio_value * config.OPTIONS_SWING_ALLOCATION

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
            return False, "Outside Swing time window (10:00-14:30)"

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
    ) -> Optional[TargetWeight]:
        """
        Check for intraday mode entry signal using Micro Regime Engine.

        V2.1.1: Uses VIX Level × VIX Direction = 21 trading regimes.

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

        Returns:
            TargetWeight for intraday entry, or None.
        """
        # Check if already have intraday position
        if self._intraday_position is not None:
            return None

        # Update Micro Regime Engine
        state = self._micro_regime_engine.update(
            vix_current=vix_current,
            vix_open=vix_open,
            qqq_current=qqq_current,
            qqq_open=qqq_open,
            current_time=current_time,
        )

        # Check if strategy is NO_TRADE
        if state.recommended_strategy == IntradayStrategy.NO_TRADE:
            return None

        # Check if strategy is PROTECTIVE_PUTS (hedge, not directional)
        if state.recommended_strategy == IntradayStrategy.PROTECTIVE_PUTS:
            self.log(f"INTRADAY: Protective mode - regime={state.micro_regime.value}")
            return None  # Would emit hedge signal separately

        # Determine direction based on QQQ move and strategy
        qqq_move = qqq_current - qqq_open
        qqq_up = qqq_move > 0

        # Debit Fade: Fade the move
        if state.recommended_strategy == IntradayStrategy.DEBIT_FADE:
            direction = OptionDirection.PUT if qqq_up else OptionDirection.CALL
            strategy_name = "DEBIT_FADE"

        # ITM Momentum: Ride the move
        elif state.recommended_strategy == IntradayStrategy.ITM_MOMENTUM:
            direction = OptionDirection.CALL if qqq_up else OptionDirection.PUT
            strategy_name = "ITM_MOM"

        # Credit Spread: Contrarian if score > 50, else momentum
        elif state.recommended_strategy == IntradayStrategy.CREDIT_SPREAD:
            if state.micro_score > 50:
                direction = OptionDirection.PUT if qqq_up else OptionDirection.CALL
            else:
                direction = OptionDirection.CALL if qqq_up else OptionDirection.PUT
            strategy_name = "CREDIT"

        else:
            return None

        # Check time windows based on strategy
        time_minutes = current_hour * 60 + current_minute

        if state.recommended_strategy == IntradayStrategy.DEBIT_FADE:
            start_time = 10 * 60 + 30  # 10:30 AM
            end_time = 14 * 60  # 2:00 PM
            if not (start_time <= time_minutes <= end_time):
                return None

        elif state.recommended_strategy == IntradayStrategy.ITM_MOMENTUM:
            start_time = 10 * 60  # 10:00 AM
            end_time = 13 * 60 + 30  # 1:30 PM
            if not (start_time <= time_minutes <= end_time):
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

        # Calculate allocation based on micro score
        allocation = self.get_mode_allocation(OptionsMode.INTRADAY, portfolio_value)

        # Adjust size based on score
        if state.micro_score >= config.MICRO_SCORE_PRIME_MR:
            size_mult = 1.0  # Full size
        elif state.micro_score >= config.MICRO_SCORE_GOOD_MR:
            size_mult = 1.0  # Full size
        elif state.micro_score >= config.MICRO_SCORE_MODERATE:
            size_mult = 0.5  # Half size
        else:
            size_mult = 0.5  # Half size

        reason = (
            f"INTRADAY_{strategy_name}: Regime={state.micro_regime.value} | "
            f"Score={state.micro_score:.0f} | VIX={vix_current:.1f} "
            f"({state.vix_direction.value}) | QQQ {'+' if qqq_up else ''}"
            f"{state.qqq_move_pct:.2f}% | {direction.value}"
        )

        self.log(f"INTRADAY_SIGNAL: {reason}", trades_only=False)

        return TargetWeight(
            symbol=best_contract.symbol,
            target_weight=size_mult,
            source="OPT_INTRADAY",
            urgency=Urgency.IMMEDIATE,
            reason=reason,
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

        # Force exit at 15:30 (3:30 PM)
        force_exit_time = current_hour > 15 or (current_hour == 15 and current_minute >= 30)

        if not force_exit_time:
            return None

        symbol = self._intraday_position.contract.symbol
        entry_price = self._intraday_position.entry_price

        pnl_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0

        reason = f"INTRADAY_TIME_EXIT_1530 {pnl_pct:+.1%} (Price: ${current_price:.2f})"
        self.log(f"INTRADAY_FORCE_EXIT {symbol} | {reason}", trades_only=True)

        return TargetWeight(
            symbol=symbol,
            target_weight=0.0,
            source="OPT_INTRADAY",
            urgency=Urgency.IMMEDIATE,
            reason=reason,
        )

    def get_micro_regime_state(self) -> MicroRegimeState:
        """Get current Micro Regime Engine state."""
        return self._micro_regime_engine.get_state()

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

        self._position = position

        # Update trade counter
        if current_date != self._last_trade_date:
            self._trades_today = 1
            self._last_trade_date = current_date
        else:
            self._trades_today += 1

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

    def has_position(self) -> bool:
        """Check if a position exists."""
        return self._position is not None

    def get_position(self) -> Optional[OptionsPosition]:
        """Get current position."""
        return self._position

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
        # Legacy position (backwards compatibility)
        position_data = state.get("position")
        if position_data:
            self._position = OptionsPosition.from_dict(position_data)
        else:
            self._position = None

        self._trades_today = state.get("trades_today", 0)
        self._last_trade_date = state.get("last_trade_date")

        # V2.1.1 dual-mode state
        swing_data = state.get("swing_position")
        if swing_data:
            self._swing_position = OptionsPosition.from_dict(swing_data)
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

        self.log("OPT: Engine reset - all positions cleared")

    def reset_daily(self, current_date: str) -> None:
        """Reset daily trade counter at start of new day."""
        if current_date != self._last_trade_date:
            self._trades_today = 0
            self._intraday_trades_today = 0
            self._last_trade_date = current_date

            # Reset Micro Regime Engine for new day
            self._micro_regime_engine.reset_daily()

            # Clear intraday position (should not exist overnight)
            if self._intraday_position is not None:
                self.log("OPT: WARNING - Intraday position found at daily reset, clearing")
                self._intraday_position = None

            self.log(f"OPT: Daily reset for {current_date}")
