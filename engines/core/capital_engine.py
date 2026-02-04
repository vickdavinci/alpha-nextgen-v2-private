"""
Capital Engine - Phase management and profit preservation.

Manages:
- Account phases (SEED, GROWTH) based on equity levels
- Phase transitions (5-day upward, immediate downward)
- Virtual lockbox for profit preservation
- Tradeable equity calculations

Spec: docs/05-capital-engine.md
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Optional, Set

if TYPE_CHECKING:
    from AlgorithmImports import QCAlgorithm

import config
from models.enums import Phase
from utils.calculations import tradeable_equity


@dataclass
class CapitalState:
    """Complete capital engine state output."""

    # Equity figures
    total_equity: float
    locked_amount: float
    tradeable_eq: float

    # Phase information
    current_phase: Phase
    days_above_threshold: int

    # Phase parameters
    target_volatility: float
    max_single_position_pct: float
    kill_switch_pct: float

    # Lockbox information
    milestones_triggered: Set[float] = field(default_factory=set)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for persistence."""
        return {
            "total_equity": round(self.total_equity, 2),
            "locked_amount": round(self.locked_amount, 2),
            "tradeable_equity": round(self.tradeable_eq, 2),
            "current_phase": self.current_phase.value,
            "days_above_threshold": self.days_above_threshold,
            "target_volatility": self.target_volatility,
            "max_single_position_pct": self.max_single_position_pct,
            "kill_switch_pct": self.kill_switch_pct,
            "milestones_triggered": list(self.milestones_triggered),
        }

    def __str__(self) -> str:
        """Human-readable summary."""
        return (
            f"CapitalState({self.current_phase.value} | "
            f"Total=${self.total_equity:,.0f} | "
            f"Locked=${self.locked_amount:,.0f} | "
            f"Tradeable=${self.tradeable_eq:,.0f} | "
            f"MaxPos={self.max_single_position_pct:.0%})"
        )


class CapitalEngine:
    """
    Phase management and profit preservation engine.

    Tracks account phases, manages transitions, and calculates
    tradeable equity excluding lockbox amounts.

    Note: This engine does NOT place orders. It only provides
    capital state information for other engines.
    """

    def __init__(self, algorithm: Optional["QCAlgorithm"] = None):
        """Initialize Capital Engine."""
        self.algorithm = algorithm
        self._current_phase: Phase = Phase.SEED
        self._days_above_threshold: int = 0
        self._locked_amount: float = 0.0
        self._milestones_triggered: Set[float] = set()

    def log(self, message: str) -> None:
        """Log via algorithm or skip for testing."""
        if self.algorithm:
            self.algorithm.Log(message)

    def calculate(self, total_equity: float) -> CapitalState:
        """
        Calculate capital state for given equity.

        Args:
            total_equity: Current total portfolio value.

        Returns:
            CapitalState with all outputs.
        """
        # Check and trigger lockbox milestones
        self._check_lockbox_milestones(total_equity)

        # Calculate tradeable equity
        tradeable = tradeable_equity(total_equity, self._locked_amount)

        # Get phase parameters
        max_pos_pct = config.MAX_SINGLE_POSITION_PCT.get(self._current_phase.value, 0.50)
        kill_pct = config.KILL_SWITCH_PCT_BY_PHASE.get(self._current_phase.value, 0.03)

        state = CapitalState(
            total_equity=total_equity,
            locked_amount=self._locked_amount,
            tradeable_eq=tradeable,
            current_phase=self._current_phase,
            days_above_threshold=self._days_above_threshold,
            target_volatility=config.TARGET_VOLATILITY,
            max_single_position_pct=max_pos_pct,
            kill_switch_pct=kill_pct,
            milestones_triggered=self._milestones_triggered.copy(),
        )

        return state

    def get_tradeable_equity_settlement_aware(
        self, total_equity: float, unsettled_cash: float
    ) -> float:
        """
        V2.9: Calculate tradeable equity minus unsettled cash.

        Options settle T+1. This prevents 'Insufficient Funds' errors
        on post-holiday opens by subtracting Portfolio.UnsettledCash.

        Args:
            total_equity: Current total portfolio value.
            unsettled_cash: Portfolio.UnsettledCash from QC.

        Returns:
            Tradeable equity adjusted for unsettled cash.
        """
        base_tradeable = tradeable_equity(total_equity, self._locked_amount)
        adjusted = max(0.0, base_tradeable - unsettled_cash)

        if unsettled_cash > 0:
            self.log(
                f"CAPITAL: Settlement-aware equity | "
                f"Base=${base_tradeable:,.0f} | Unsettled=${unsettled_cash:,.0f} | "
                f"Adjusted=${adjusted:,.0f}"
            )

        return adjusted

    def end_of_day_update(self, total_equity: float) -> CapitalState:
        """
        Perform end-of-day phase transition check.

        Args:
            total_equity: End of day equity.

        Returns:
            Updated CapitalState.
        """
        self._check_phase_transitions(total_equity)
        state = self.calculate(total_equity)
        self.log(f"CAPITAL: EOD {state}")
        return state

    def _check_phase_transitions(self, total_equity: float) -> None:
        """Check and execute phase transitions."""
        growth_threshold = config.PHASE_GROWTH_MIN

        if self._current_phase == Phase.SEED:
            if total_equity >= growth_threshold:
                self._days_above_threshold += 1
                if self._days_above_threshold >= config.UPWARD_TRANSITION_DAYS:
                    self._transition_to_growth()
            else:
                self._days_above_threshold = 0

        elif self._current_phase == Phase.GROWTH:
            if total_equity < growth_threshold:
                self._transition_to_seed()

    def _transition_to_growth(self) -> None:
        """Execute transition from SEED to GROWTH."""
        self._current_phase = Phase.GROWTH
        self._days_above_threshold = 0
        self.log(
            f"CAPITAL: PHASE_TRANSITION SEED -> GROWTH | "
            f"Max position: {config.MAX_SINGLE_POSITION_PCT['GROWTH']:.0%}"
        )

    def _transition_to_seed(self) -> None:
        """Execute immediate transition from GROWTH to SEED."""
        self._current_phase = Phase.SEED
        self._days_above_threshold = 0
        self.log(
            f"CAPITAL: PHASE_TRANSITION GROWTH -> SEED (IMMEDIATE) | "
            f"Max position: {config.MAX_SINGLE_POSITION_PCT['SEED']:.0%}"
        )

    def _check_lockbox_milestones(self, total_equity: float) -> None:
        """Check and trigger lockbox milestones."""
        for milestone in config.LOCKBOX_MILESTONES:
            if milestone not in self._milestones_triggered:
                if total_equity >= milestone:
                    lock_amount = total_equity * config.LOCKBOX_LOCK_PCT
                    self._locked_amount += lock_amount
                    self._milestones_triggered.add(milestone)
                    self.log(
                        f"CAPITAL: LOCKBOX ${milestone:,.0f} | "
                        f"Locked ${lock_amount:,.0f} | Total: ${self._locked_amount:,.0f}"
                    )

    def reset(self) -> None:
        """Reset phase state (lockbox preserved)."""
        self._current_phase = Phase.SEED
        self._days_above_threshold = 0
        self.log("CAPITAL: Reset to SEED (lockbox preserved)")

    def reset_full(self) -> None:
        """Full reset including lockbox (testing only)."""
        self._current_phase = Phase.SEED
        self._days_above_threshold = 0
        self._locked_amount = 0.0
        self._milestones_triggered = set()

    def get_state_for_persistence(self) -> Dict[str, Any]:
        """Get state for ObjectStore."""
        return {
            "current_phase": self._current_phase.value,
            "days_above_threshold": self._days_above_threshold,
            "locked_amount": self._locked_amount,
            "milestones_triggered": list(self._milestones_triggered),
        }

    def restore_state(self, state: Dict[str, Any]) -> None:
        """Restore state from ObjectStore."""
        self._current_phase = Phase(state.get("current_phase", "SEED"))
        self._days_above_threshold = state.get("days_above_threshold", 0)
        self._locked_amount = state.get("locked_amount", 0.0)
        self._milestones_triggered = set(state.get("milestones_triggered", []))

    def get_current_phase(self) -> Phase:
        """Get current phase."""
        return self._current_phase

    def get_locked_amount(self) -> float:
        """Get locked amount."""
        return self._locked_amount

    def get_max_position_pct(self) -> float:
        """Get max position % for current phase."""
        return config.MAX_SINGLE_POSITION_PCT.get(self._current_phase.value, 0.50)
