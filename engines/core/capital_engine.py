"""
Capital Engine - Profit preservation and tradeable equity calculations.

Manages:
- Virtual lockbox for profit preservation at milestones
- Tradeable equity calculations (total - locked)

V3.0: Removed SEED/GROWTH phase system. Regime-based safeguards replace it:
- Startup Gate handles new deployment ramp-up (time-based)
- Drawdown Governor handles capital protection (performance-based)
- Regime Engine handles market adaptation (conditions-based)

Spec: docs/05-capital-engine.md
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Optional, Set

if TYPE_CHECKING:
    from AlgorithmImports import QCAlgorithm

import config
from utils.calculations import tradeable_equity


@dataclass
class CapitalState:
    """Complete capital engine state output."""

    # Equity figures
    total_equity: float
    locked_amount: float
    tradeable_eq: float

    # Parameters
    target_volatility: float
    kill_switch_pct: float

    # Lockbox information
    milestones_triggered: Set[float] = field(default_factory=set)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for persistence."""
        return {
            "total_equity": round(self.total_equity, 2),
            "locked_amount": round(self.locked_amount, 2),
            "tradeable_equity": round(self.tradeable_eq, 2),
            "target_volatility": self.target_volatility,
            "kill_switch_pct": self.kill_switch_pct,
            "milestones_triggered": list(self.milestones_triggered),
        }

    def __str__(self) -> str:
        """Human-readable summary."""
        return (
            f"CapitalState("
            f"Total=${self.total_equity:,.0f} | "
            f"Locked=${self.locked_amount:,.0f} | "
            f"Tradeable=${self.tradeable_eq:,.0f})"
        )


class CapitalEngine:
    """
    Profit preservation engine.

    Manages lockbox for profit preservation and calculates
    tradeable equity excluding lockbox amounts.

    V3.0: Removed SEED/GROWTH phases. Capital allocation is now
    controlled by regime-based safeguards (Startup Gate, Drawdown Governor).

    Note: This engine does NOT place orders. It only provides
    capital state information for other engines.
    """

    def __init__(self, algorithm: Optional["QCAlgorithm"] = None):
        """Initialize Capital Engine."""
        self.algorithm = algorithm
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

        state = CapitalState(
            total_equity=total_equity,
            locked_amount=self._locked_amount,
            tradeable_eq=tradeable,
            target_volatility=config.TARGET_VOLATILITY,
            kill_switch_pct=config.KILL_SWITCH_PCT,
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
        Perform end-of-day capital state update.

        Args:
            total_equity: End of day equity.

        Returns:
            Updated CapitalState.
        """
        state = self.calculate(total_equity)
        self.log(f"CAPITAL: EOD {state}")
        return state

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
        """Reset capital state (lockbox preserved)."""
        self.log("CAPITAL: Reset (lockbox preserved)")

    def reset_full(self) -> None:
        """Full reset including lockbox (testing only)."""
        self._locked_amount = 0.0
        self._milestones_triggered = set()

    def get_state_for_persistence(self) -> Dict[str, Any]:
        """Get state for ObjectStore."""
        return {
            "locked_amount": self._locked_amount,
            "milestones_triggered": list(self._milestones_triggered),
        }

    def restore_state(self, state: Dict[str, Any]) -> None:
        """Restore state from ObjectStore."""
        self._locked_amount = state.get("locked_amount", 0.0)
        self._milestones_triggered = set(state.get("milestones_triggered", []))

    def get_locked_amount(self) -> float:
        """Get locked amount."""
        return self._locked_amount
