"""
Hedge Engine - Regime-based tail risk protection.

V6.11: Uses SH (1x inverse S&P) as sole hedge instrument. SH has no contango
decay unlike VIXY/VXX, making it suitable for longer holds during risk-off periods.

Allocation Tiers:
- Score >= 50: SH 0% (no hedge)
- Score 40-49: SH 5% (light hedge)
- Score 30-39: SH 8% (medium hedge)
- Score < 30: SH 10% (full hedge)

Rebalancing: EOD only (15:45 ET) with 2% threshold.
Exception: Panic mode triggers immediate rebalance.

Spec: docs/09-hedge-engine.md
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from AlgorithmImports import QCAlgorithm

import config
from models.enums import Urgency
from models.target_weight import TargetWeight


@dataclass
class HedgeAllocation:
    """Current hedge allocation state. V6.11: Uses SH only."""

    sh_target_pct: float
    regime_score: float
    hedge_tier: str  # "NONE", "LIGHT", "MEDIUM", "FULL"

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for persistence."""
        return {
            "sh_target_pct": self.sh_target_pct,
            "regime_score": self.regime_score,
            "hedge_tier": self.hedge_tier,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HedgeAllocation":
        """Deserialize from persistence."""
        return cls(
            sh_target_pct=data.get("sh_target_pct", 0.0),
            regime_score=data["regime_score"],
            hedge_tier=data["hedge_tier"],
        )


class HedgeEngine:
    """
    Regime-based hedge allocation engine.

    V6.11: Uses SH (1x inverse S&P 500) as sole hedge instrument.
    SH provides direct equity offset without contango decay issues
    that affect volatility products like VIXY/VXX.

    Note: This engine does NOT place orders. It only provides signals
    via TargetWeight objects for the Portfolio Router.
    """

    # Instruments managed by this engine - V6.11: SH only
    INSTRUMENTS: List[str] = ["SH"]

    def __init__(self, algorithm: Optional["QCAlgorithm"] = None):
        """Initialize Hedge Engine."""
        self.algorithm = algorithm
        self._last_allocation: Optional[HedgeAllocation] = None

    def log(self, message: str) -> None:
        """Log via algorithm or skip for testing."""
        if self.algorithm:
            self.algorithm.Log(message)

    def get_target_allocations(self, regime_score: float) -> Tuple[float, str]:
        """
        Calculate target hedge allocations based on regime score.

        V6.11: Returns SH allocation only (no TMF/PSQ).

        Args:
            regime_score: Current smoothed regime score (0-100).

        Returns:
            Tuple of (sh_target_pct, hedge_tier).
        """
        if regime_score >= config.HEDGE_LEVEL_1:
            # Score >= 50: No hedge
            return 0.0, "NONE"
        elif regime_score >= config.HEDGE_LEVEL_2:
            # Score 40-49: Light hedge
            return config.SH_LIGHT, "LIGHT"
        elif regime_score >= config.HEDGE_LEVEL_3:
            # Score 30-39: Medium hedge
            return config.SH_MEDIUM, "MEDIUM"
        else:
            # Score < 30: Full hedge
            return config.SH_FULL, "FULL"

    def check_rebalance_needed(
        self,
        target_pct: float,
        current_pct: float,
    ) -> bool:
        """
        Check if rebalancing is needed based on threshold.

        Rebalance only if target differs from current by more than
        the threshold (default 2%).

        Args:
            target_pct: Target allocation percentage.
            current_pct: Current allocation percentage.

        Returns:
            True if rebalancing is needed.
        """
        return abs(target_pct - current_pct) > config.HEDGE_REBAL_THRESHOLD

    def get_hedge_signals(
        self,
        regime_score: float,
        current_sh_pct: float,
        is_panic_mode: bool = False,
    ) -> List[TargetWeight]:
        """
        Generate hedge allocation signals based on regime score.

        V6.11: Simplified to use SH only. This is the main entry point
        called at EOD (15:45 ET) to determine hedge position adjustments.

        Args:
            regime_score: Current smoothed regime score (0-100).
            current_sh_pct: Current SH allocation as decimal (0.05 = 5%).
            is_panic_mode: True if panic mode is active (immediate rebalance).

        Returns:
            List of TargetWeight signals for SH.
        """
        signals: List[TargetWeight] = []

        # Get target allocations based on regime
        sh_target, hedge_tier = self.get_target_allocations(regime_score)

        # Store current allocation state
        self._last_allocation = HedgeAllocation(
            sh_target_pct=sh_target,
            regime_score=regime_score,
            hedge_tier=hedge_tier,
        )

        # Determine urgency - panic mode triggers immediate rebalance
        urgency = Urgency.IMMEDIATE if is_panic_mode else Urgency.EOD

        # Check if SH rebalancing is needed
        sh_needs_rebalance = self.check_rebalance_needed(sh_target, current_sh_pct)
        if sh_needs_rebalance or is_panic_mode:
            reason = (
                f"Regime={regime_score:.1f}, SH target={sh_target:.0%}, "
                f"current={current_sh_pct:.0%}, tier={hedge_tier}"
            )
            if is_panic_mode:
                reason = f"PANIC_MODE: {reason}"

            self.log(f"HEDGE: SH_SIGNAL | {reason}")

            signals.append(
                TargetWeight(
                    symbol="SH",
                    target_weight=sh_target,
                    source="HEDGE",
                    urgency=urgency,
                    reason=reason,
                )
            )

        # Log if no rebalancing needed
        if not signals and not is_panic_mode:
            self.log(
                f"HEDGE: NO_REBALANCE | Regime={regime_score:.1f}, "
                f"SH: {current_sh_pct:.0%} (target {sh_target:.0%})"
            )

        return signals

    def get_panic_mode_signals(
        self,
        regime_score: float,
        current_sh_pct: float,
    ) -> List[TargetWeight]:
        """
        Generate immediate hedge signals for panic mode.

        V6.11: Uses SH only. Called when panic mode triggers (SPY -4% intraday).
        May need to increase hedges immediately rather than waiting for EOD.

        Args:
            regime_score: Current smoothed regime score.
            current_sh_pct: Current SH allocation.

        Returns:
            List of TargetWeight signals with IMMEDIATE urgency.
        """
        self.log(f"HEDGE: PANIC_MODE_CHECK | Regime={regime_score:.1f}")
        return self.get_hedge_signals(
            regime_score=regime_score,
            current_sh_pct=current_sh_pct,
            is_panic_mode=True,
        )

    def get_last_allocation(self) -> Optional[HedgeAllocation]:
        """Get the last calculated allocation."""
        return self._last_allocation

    def get_state_for_persistence(self) -> Dict[str, Any]:
        """Get state for ObjectStore."""
        return {
            "last_allocation": (self._last_allocation.to_dict() if self._last_allocation else None)
        }

    def restore_state(self, state: Dict[str, Any]) -> None:
        """Restore state from ObjectStore."""
        last_alloc_data = state.get("last_allocation")
        if last_alloc_data:
            self._last_allocation = HedgeAllocation.from_dict(last_alloc_data)
        else:
            self._last_allocation = None

    def reset(self) -> None:
        """Reset engine state."""
        self._last_allocation = None
        self.log("HEDGE: Engine reset")

    def get_hedge_tier_for_regime(self, regime_score: float) -> str:
        """
        Get the hedge tier name for a given regime score.

        Useful for logging and debugging.

        Args:
            regime_score: Regime score (0-100).

        Returns:
            Hedge tier name ("NONE", "LIGHT", "MEDIUM", "FULL").
        """
        _, tier = self.get_target_allocations(regime_score)
        return tier

    def get_max_total_hedge(self) -> float:
        """
        Get maximum total hedge allocation.

        V6.11: Returns SH_FULL (10%).

        Returns:
            Maximum SH allocation (0.10 = 10%).
        """
        return config.SH_FULL
