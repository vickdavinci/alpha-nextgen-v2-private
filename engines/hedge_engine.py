"""
Hedge Engine - Regime-based tail risk protection.

Provides graduated hedge allocation using TMF (3x Treasury) and PSQ (1x inverse
Nasdaq) based on regime score. Hedges are insurance - they cost money in good
times but protect the portfolio during crashes.

Allocation Tiers:
- Score >= 40: TMF 0%, PSQ 0% (no hedge)
- Score 30-39: TMF 10%, PSQ 0% (light hedge)
- Score 20-29: TMF 15%, PSQ 5% (medium hedge)
- Score < 20: TMF 20%, PSQ 10% (full hedge)

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
    """Current hedge allocation state."""

    tmf_target_pct: float
    psq_target_pct: float
    regime_score: float
    hedge_tier: str  # "NONE", "LIGHT", "MEDIUM", "FULL"

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for persistence."""
        return {
            "tmf_target_pct": self.tmf_target_pct,
            "psq_target_pct": self.psq_target_pct,
            "regime_score": self.regime_score,
            "hedge_tier": self.hedge_tier,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HedgeAllocation":
        """Deserialize from persistence."""
        return cls(
            tmf_target_pct=data["tmf_target_pct"],
            psq_target_pct=data["psq_target_pct"],
            regime_score=data["regime_score"],
            hedge_tier=data["hedge_tier"],
        )


class HedgeEngine:
    """
    Regime-based hedge allocation engine.

    Provides tail risk protection through graduated hedge allocation
    based on regime score. Uses TMF as primary hedge (flight-to-safety)
    and PSQ as secondary hedge (direct equity offset).

    Note: This engine does NOT place orders. It only provides signals
    via TargetWeight objects for the Portfolio Router.
    """

    # Instruments managed by this engine
    INSTRUMENTS: List[str] = ["TMF", "PSQ"]

    def __init__(self, algorithm: Optional["QCAlgorithm"] = None):
        """Initialize Hedge Engine."""
        self.algorithm = algorithm
        self._last_allocation: Optional[HedgeAllocation] = None

    def log(self, message: str) -> None:
        """Log via algorithm or skip for testing."""
        if self.algorithm:
            self.algorithm.Log(message)

    def get_target_allocations(self, regime_score: float) -> Tuple[float, float, str]:
        """
        Calculate target hedge allocations based on regime score.

        Args:
            regime_score: Current smoothed regime score (0-100).

        Returns:
            Tuple of (tmf_target_pct, psq_target_pct, hedge_tier).
        """
        if regime_score >= config.HEDGE_LEVEL_1:
            # Score >= 40: No hedge
            return 0.0, 0.0, "NONE"
        elif regime_score >= config.HEDGE_LEVEL_2:
            # Score 30-39: Light hedge (TMF only)
            return config.TMF_LIGHT, 0.0, "LIGHT"
        elif regime_score >= config.HEDGE_LEVEL_3:
            # Score 20-29: Medium hedge (TMF + PSQ)
            return config.TMF_MEDIUM, config.PSQ_MEDIUM, "MEDIUM"
        else:
            # Score < 20: Full hedge
            return config.TMF_FULL, config.PSQ_FULL, "FULL"

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
        current_tmf_pct: float,
        current_psq_pct: float,
        is_panic_mode: bool = False,
    ) -> List[TargetWeight]:
        """
        Generate hedge allocation signals based on regime score.

        This is the main entry point called at EOD (15:45 ET) to determine
        hedge position adjustments.

        Args:
            regime_score: Current smoothed regime score (0-100).
            current_tmf_pct: Current TMF allocation as decimal (0.10 = 10%).
            current_psq_pct: Current PSQ allocation as decimal (0.05 = 5%).
            is_panic_mode: True if panic mode is active (immediate rebalance).

        Returns:
            List of TargetWeight signals for TMF and/or PSQ.
        """
        signals: List[TargetWeight] = []

        # Get target allocations based on regime
        tmf_target, psq_target, hedge_tier = self.get_target_allocations(regime_score)

        # Store current allocation state
        self._last_allocation = HedgeAllocation(
            tmf_target_pct=tmf_target,
            psq_target_pct=psq_target,
            regime_score=regime_score,
            hedge_tier=hedge_tier,
        )

        # Determine urgency - panic mode triggers immediate rebalance
        urgency = Urgency.IMMEDIATE if is_panic_mode else Urgency.EOD

        # Check if TMF rebalancing is needed
        tmf_needs_rebalance = self.check_rebalance_needed(tmf_target, current_tmf_pct)
        if tmf_needs_rebalance or is_panic_mode:
            reason = (
                f"Regime={regime_score:.1f}, TMF target={tmf_target:.0%}, "
                f"current={current_tmf_pct:.0%}, tier={hedge_tier}"
            )
            if is_panic_mode:
                reason = f"PANIC_MODE: {reason}"

            self.log(f"HEDGE: TMF_SIGNAL | {reason}")

            signals.append(
                TargetWeight(
                    symbol="TMF",
                    target_weight=tmf_target,
                    source="HEDGE",
                    urgency=urgency,
                    reason=reason,
                )
            )

        # Check if PSQ rebalancing is needed
        psq_needs_rebalance = self.check_rebalance_needed(psq_target, current_psq_pct)
        if psq_needs_rebalance or is_panic_mode:
            reason = (
                f"Regime={regime_score:.1f}, PSQ target={psq_target:.0%}, "
                f"current={current_psq_pct:.0%}, tier={hedge_tier}"
            )
            if is_panic_mode:
                reason = f"PANIC_MODE: {reason}"

            self.log(f"HEDGE: PSQ_SIGNAL | {reason}")

            signals.append(
                TargetWeight(
                    symbol="PSQ",
                    target_weight=psq_target,
                    source="HEDGE",
                    urgency=urgency,
                    reason=reason,
                )
            )

        # Log if no rebalancing needed
        if not signals and not is_panic_mode:
            self.log(
                f"HEDGE: NO_REBALANCE | Regime={regime_score:.1f}, "
                f"TMF: {current_tmf_pct:.0%} (target {tmf_target:.0%}), "
                f"PSQ: {current_psq_pct:.0%} (target {psq_target:.0%})"
            )

        return signals

    def get_panic_mode_signals(
        self,
        regime_score: float,
        current_tmf_pct: float,
        current_psq_pct: float,
    ) -> List[TargetWeight]:
        """
        Generate immediate hedge signals for panic mode.

        Called when panic mode triggers (SPY -4% intraday). May need to
        increase hedges immediately rather than waiting for EOD.

        Args:
            regime_score: Current smoothed regime score.
            current_tmf_pct: Current TMF allocation.
            current_psq_pct: Current PSQ allocation.

        Returns:
            List of TargetWeight signals with IMMEDIATE urgency.
        """
        self.log(f"HEDGE: PANIC_MODE_CHECK | Regime={regime_score:.1f}")
        return self.get_hedge_signals(
            regime_score=regime_score,
            current_tmf_pct=current_tmf_pct,
            current_psq_pct=current_psq_pct,
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
        _, _, tier = self.get_target_allocations(regime_score)
        return tier

    def get_max_total_hedge(self) -> float:
        """
        Get maximum total hedge allocation.

        Returns:
            Maximum combined TMF + PSQ allocation (0.30 = 30%).
        """
        return config.TMF_FULL + config.PSQ_FULL
