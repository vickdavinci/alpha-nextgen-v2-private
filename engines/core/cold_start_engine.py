"""
Cold Start Engine - Days 1-5 warm entry logic.

Solves the Day 1 Deployment Problem: when the algorithm starts fresh
with no positions, it might wait days for a valid breakout signal.

During the first 5 trading days, if conditions are favorable:
- Deploy capital immediately using simplified "warm entry"
- Use 50% of normal position size (reduced conviction)
- Select QLD (regime > 60) or SSO (regime 50-60)
- Block full trend and mean reversion entries

Spec: docs/06-cold-start-engine.md
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from AlgorithmImports import QCAlgorithm

import config
from models.enums import Urgency
from models.target_weight import TargetWeight


@dataclass
class ColdStartState:
    """Complete cold start engine state output."""

    # Cold start status
    is_cold_start_active: bool
    days_running: int

    # Warm entry status
    warm_entry_executed: bool
    warm_entry_symbol: Optional[str]

    # Strategy permissions
    full_strategies_allowed: bool
    warm_entry_allowed: bool

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for persistence."""
        return {
            "is_cold_start_active": self.is_cold_start_active,
            "days_running": self.days_running,
            "warm_entry_executed": self.warm_entry_executed,
            "warm_entry_symbol": self.warm_entry_symbol,
            "full_strategies_allowed": self.full_strategies_allowed,
            "warm_entry_allowed": self.warm_entry_allowed,
        }

    def __str__(self) -> str:
        """Human-readable summary."""
        status = "COLD_START" if self.is_cold_start_active else "NORMAL"
        warm = f"Warm={'Yes' if self.warm_entry_executed else 'No'}"
        return f"ColdStartState({status} | Day {self.days_running} | {warm})"


class ColdStartEngine:
    """
    Days 1-5 warm entry engine.

    Tracks trading days since start/reset and manages warm entry
    deployment during the cold start period.

    Note: This engine does NOT place orders. It only provides
    state information and emits TargetWeight for warm entries.
    """

    # Leveraged symbols that count as "existing position"
    LEVERAGED_SYMBOLS: List[str] = ["TQQQ", "QLD", "SSO", "SOXL"]

    def __init__(self, algorithm: Optional["QCAlgorithm"] = None):
        """Initialize Cold Start Engine."""
        self.algorithm = algorithm
        self._days_running: int = 0
        self._warm_entry_executed: bool = False
        self._warm_entry_symbol: Optional[str] = None

    def log(self, message: str) -> None:
        """Log via algorithm or skip for testing."""
        if self.algorithm:
            self.algorithm.Log(message)

    def get_state(self) -> ColdStartState:
        """Get current cold start state."""
        is_active = self.is_cold_start_active()
        return ColdStartState(
            is_cold_start_active=is_active,
            days_running=self._days_running,
            warm_entry_executed=self._warm_entry_executed,
            warm_entry_symbol=self._warm_entry_symbol,
            full_strategies_allowed=not is_active,
            warm_entry_allowed=is_active and not self._warm_entry_executed,
        )

    def is_cold_start_active(self) -> bool:
        """Check if cold start mode is active."""
        return self._days_running < config.COLD_START_DAYS

    def are_full_strategies_allowed(self) -> bool:
        """Check if full trend/MR strategies are allowed."""
        return not self.is_cold_start_active()

    def check_warm_entry(
        self,
        regime_score: float,
        has_leveraged_position: bool,
        kill_switch_triggered: bool,
        gap_filter_triggered: bool,
        vol_shock_active: bool,
        tradeable_equity: float,
        current_hour: int,
        current_minute: int,
    ) -> Optional[TargetWeight]:
        """
        Check warm entry conditions and return TargetWeight if all pass.

        Args:
            regime_score: Current smoothed regime score (0-100).
            has_leveraged_position: True if any leveraged long is held.
            kill_switch_triggered: True if kill switch is active.
            gap_filter_triggered: True if gap filter blocked entries.
            vol_shock_active: True if vol shock pause is active.
            tradeable_equity: Available capital for trading.
            current_hour: Current hour (0-23) in Eastern time.
            current_minute: Current minute (0-59).

        Returns:
            TargetWeight for warm entry, or None if conditions not met.
        """
        # Condition 1: Cold start must be active
        if not self.is_cold_start_active():
            return None

        # Condition 2: Warm entry not already executed
        if self._warm_entry_executed:
            return None

        # Condition 3: Time >= 10:00 AM ET
        warm_hour, warm_minute = self._parse_time(config.WARM_ENTRY_TIME)
        if current_hour < warm_hour or (current_hour == warm_hour and current_minute < warm_minute):
            return None

        # Condition 4: Regime score > 50 (strictly greater)
        if regime_score <= config.WARM_REGIME_MIN:
            self.log(
                f"COLD_START: Warm entry blocked - regime {regime_score:.1f} <= {config.WARM_REGIME_MIN}"
            )
            return None

        # Condition 5: No existing leveraged position
        if has_leveraged_position:
            self.log("COLD_START: Warm entry blocked - leveraged position exists")
            return None

        # Condition 6: No kill switch
        if kill_switch_triggered:
            self.log("COLD_START: Warm entry blocked - kill switch active")
            return None

        # Condition 7: No gap filter
        if gap_filter_triggered:
            self.log("COLD_START: Warm entry blocked - gap filter triggered")
            return None

        # Condition 8: No vol shock
        if vol_shock_active:
            self.log("COLD_START: Warm entry blocked - vol shock active")
            return None

        # All conditions passed - select instrument
        symbol = self._select_instrument(regime_score)

        # Calculate position size (50% of full)
        weight = self._calculate_warm_entry_weight(tradeable_equity)

        # Check minimum size
        position_value = tradeable_equity * weight
        if position_value < config.WARM_MIN_SIZE:
            self.log(
                f"COLD_START: Warm entry skipped - size ${position_value:,.0f} < ${config.WARM_MIN_SIZE:,.0f}"
            )
            return None

        # Create TargetWeight
        reason = f"Warm Entry: Regime={regime_score:.1f}, Day={self._days_running + 1}"
        target = TargetWeight(
            symbol=symbol,
            target_weight=weight,
            source="COLD_START",
            urgency=Urgency.IMMEDIATE,
            reason=reason,
        )

        # V2.3.23: Mark warm entry as executed IMMEDIATELY to prevent duplicates
        # This is critical because MOO orders don't fill until next market open,
        # and check_warm_entry() may be called multiple times (weekends, holidays)
        # before the order fills. Previously we waited for confirm_warm_entry()
        # which caused duplicate orders on Jan 18-20, 2025 (weekend + MLK holiday).
        self._warm_entry_executed = True
        self._warm_entry_symbol = symbol

        self.log(
            f"COLD_START: WARM_ENTRY {symbol} | Weight={weight:.2%} | "
            f"Value=${position_value:,.0f} | Regime={regime_score:.1f}"
        )

        return target

    def confirm_warm_entry(self, symbol: str) -> None:
        """
        Confirm warm entry order was filled.

        Note: As of V2.3.23, warm entry is marked as executed immediately
        when the signal is generated (in check_warm_entry) to prevent
        duplicate orders during weekends/holidays. This method is now
        only used for logging confirmation of the actual fill.

        Args:
            symbol: Symbol that was entered (QLD or SSO).
        """
        # State already set in check_warm_entry(), just log confirmation
        if not self._warm_entry_executed:
            self._warm_entry_executed = True
            self._warm_entry_symbol = symbol
        self.log(f"COLD_START: Warm entry fill confirmed for {symbol}")

    def _select_instrument(self, regime_score: float) -> str:
        """Select instrument based on regime score."""
        if regime_score > config.WARM_QLD_THRESHOLD:
            return "QLD"  # Higher conviction → Nasdaq
        return "SSO"  # Moderate conviction → S&P 500

    def _calculate_warm_entry_weight(self, tradeable_equity: float) -> float:
        """
        Calculate warm entry weight as 50% of full position.

        For simplicity, uses a baseline weight that would be modified
        by full volatility targeting. Here we approximate with max
        single position percentage × warm entry multiplier.
        """
        # Use SEED phase max position as baseline (conservative)
        base_weight = config.MAX_SINGLE_POSITION_PCT.get("SEED", 0.50)
        return base_weight * config.WARM_ENTRY_SIZE_MULT

    def _parse_time(self, time_str: str) -> tuple:
        """Parse time string (HH:MM) to (hour, minute) tuple."""
        parts = time_str.split(":")
        return int(parts[0]), int(parts[1])

    def end_of_day_update(self, kill_switch_triggered: bool = False) -> ColdStartState:
        """
        Perform end-of-day update.

        Args:
            kill_switch_triggered: True if kill switch triggered today.

        Returns:
            Updated ColdStartState.
        """
        if kill_switch_triggered:
            # Kill switch resets everything
            self.log("COLD_START: Kill switch triggered - resetting to day 0")
            self.reset()
        elif self._days_running < config.COLD_START_DAYS:
            # V2.19 FIX: Only increment during cold start period (was counting forever)
            self._days_running += 1
            if self._days_running == config.COLD_START_DAYS:
                self.log(
                    f"COLD_START: Day {self._days_running} complete - "
                    "transitioning to NORMAL mode"
                )
            else:
                self.log(
                    f"COLD_START: Day {self._days_running} complete - "
                    f"{config.COLD_START_DAYS - self._days_running} days remaining"
                )

        return self.get_state()

    def reset(self) -> None:
        """Reset cold start state (called after kill switch)."""
        # Only log if actually resetting (not already at day 0)
        if self._days_running > 0:
            self.log("COLD_START: Reset to day 0")
        self._days_running = 0
        self._warm_entry_executed = False
        self._warm_entry_symbol = None

    def get_state_for_persistence(self) -> Dict[str, Any]:
        """Get state for ObjectStore."""
        return {
            "days_running": self._days_running,
            "warm_entry_executed": self._warm_entry_executed,
            "warm_entry_symbol": self._warm_entry_symbol,
        }

    def restore_state(self, state: Dict[str, Any]) -> None:
        """Restore state from ObjectStore."""
        self._days_running = state.get("days_running", 0)
        self._warm_entry_executed = state.get("warm_entry_executed", False)
        self._warm_entry_symbol = state.get("warm_entry_symbol", None)

    def get_days_running(self) -> int:
        """Get current days running count."""
        return self._days_running

    def has_warm_entry_executed(self) -> bool:
        """Check if warm entry has been executed this period."""
        return self._warm_entry_executed

    def get_warm_entry_symbol(self) -> Optional[str]:
        """Get symbol used for warm entry, if any."""
        return self._warm_entry_symbol
