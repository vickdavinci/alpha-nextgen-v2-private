"""
VIX Regime Classification - V2.1 Mean Reversion filter.

Classifies market regime based on VIX levels to prevent
catching falling knives during crashes.

VIX Regimes:
- NORMAL (VIX < 20): Full MR allocation (10%)
- CAUTION (VIX 20-30): Reduced allocation (5%)
- HIGH_RISK (VIX 30-40): Minimal allocation (2%)
- CRASH (VIX > 40): MR disabled (0%)

Spec: docs/v2-specs/V2-1-Critical-Fixes-Guide.md (Fix #2)
"""

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from AlgorithmImports import QCAlgorithm

import config


class VIXRegime(Enum):
    """VIX-based market regime classification."""

    NORMAL = "NORMAL"  # VIX < 20
    CAUTION = "CAUTION"  # VIX 20-30
    HIGH_RISK = "HIGH_RISK"  # VIX 30-40
    CRASH = "CRASH"  # VIX > 40


@dataclass
class VIXRegimeState:
    """
    Current VIX regime state with derived parameters.

    Attributes:
        vix_value: Current VIX value.
        regime: Classified regime.
        mr_allocation: Mean Reversion allocation for this regime.
        rsi_threshold: RSI threshold for this regime.
        stop_loss_pct: Stop loss percentage for this regime.
        max_exposure: Maximum MR exposure for this regime.
        mr_enabled: Whether MR is enabled in this regime.
    """

    vix_value: float
    regime: VIXRegime
    mr_allocation: float
    rsi_threshold: float
    stop_loss_pct: float
    max_exposure: float
    mr_enabled: bool

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for logging/persistence."""
        return {
            "vix_value": self.vix_value,
            "regime": self.regime.value,
            "mr_allocation": self.mr_allocation,
            "rsi_threshold": self.rsi_threshold,
            "stop_loss_pct": self.stop_loss_pct,
            "max_exposure": self.max_exposure,
            "mr_enabled": self.mr_enabled,
        }


def classify_vix_regime(vix_value: float) -> VIXRegime:
    """
    Classify VIX value into regime.

    Args:
        vix_value: Current VIX value (typically 10-80 range).

    Returns:
        VIXRegime classification.
    """
    if vix_value < config.VIX_NORMAL_MAX:
        return VIXRegime.NORMAL
    elif vix_value < config.VIX_CAUTION_MAX:
        return VIXRegime.CAUTION
    elif vix_value < config.VIX_HIGH_RISK_MAX:
        return VIXRegime.HIGH_RISK
    else:
        return VIXRegime.CRASH


def get_mr_allocation_for_regime(regime: VIXRegime) -> float:
    """
    Get MR allocation percentage for a VIX regime.

    Args:
        regime: VIXRegime classification.

    Returns:
        Allocation as decimal (e.g., 0.10 = 10%).
    """
    if regime == VIXRegime.NORMAL:
        return config.MR_ALLOC_NORMAL
    elif regime == VIXRegime.CAUTION:
        return config.MR_ALLOC_CAUTION
    elif regime == VIXRegime.HIGH_RISK:
        return config.MR_ALLOC_HIGH_RISK
    else:  # CRASH
        return config.MR_ALLOC_CRASH


def get_rsi_threshold_for_regime(regime: VIXRegime) -> float:
    """
    Get RSI threshold for a VIX regime.

    Lower threshold = more conservative (harder to trigger).

    Args:
        regime: VIXRegime classification.

    Returns:
        RSI threshold value.
    """
    if regime == VIXRegime.NORMAL:
        return config.MR_RSI_NORMAL
    elif regime == VIXRegime.CAUTION:
        return config.MR_RSI_CAUTION
    elif regime == VIXRegime.HIGH_RISK:
        return config.MR_RSI_HIGH_RISK
    else:  # CRASH - not used, but return strictest
        return 15.0


def get_stop_loss_for_regime(regime: VIXRegime) -> float:
    """
    Get stop loss percentage for a VIX regime.

    Tighter stops in higher VIX environments.

    Args:
        regime: VIXRegime classification.

    Returns:
        Stop loss as decimal (e.g., 0.08 = 8%).
    """
    if regime == VIXRegime.NORMAL:
        return config.MR_STOP_NORMAL
    elif regime == VIXRegime.CAUTION:
        return config.MR_STOP_CAUTION
    elif regime == VIXRegime.HIGH_RISK:
        return config.MR_STOP_HIGH_RISK
    else:  # CRASH - not used
        return 0.02


def get_max_exposure_for_regime(regime: VIXRegime) -> float:
    """
    Get maximum MR exposure for a VIX regime.

    Args:
        regime: VIXRegime classification.

    Returns:
        Maximum exposure as decimal (e.g., 0.15 = 15%).
    """
    if regime == VIXRegime.NORMAL:
        return config.MR_MAX_EXPOSURE_NORMAL
    elif regime == VIXRegime.CAUTION:
        return config.MR_MAX_EXPOSURE_CAUTION
    elif regime == VIXRegime.HIGH_RISK:
        return config.MR_MAX_EXPOSURE_HIGH_RISK
    else:  # CRASH
        return config.MR_MAX_EXPOSURE_CRASH


def is_mr_enabled_for_regime(regime: VIXRegime) -> bool:
    """
    Check if MR is enabled for a VIX regime.

    MR is disabled in CRASH regime (VIX > 40).

    Args:
        regime: VIXRegime classification.

    Returns:
        True if MR is enabled.
    """
    return regime != VIXRegime.CRASH


def get_vix_regime_state(vix_value: float) -> VIXRegimeState:
    """
    Get complete VIX regime state with all derived parameters.

    Args:
        vix_value: Current VIX value.

    Returns:
        VIXRegimeState with all parameters for current regime.
    """
    regime = classify_vix_regime(vix_value)
    return VIXRegimeState(
        vix_value=vix_value,
        regime=regime,
        mr_allocation=get_mr_allocation_for_regime(regime),
        rsi_threshold=get_rsi_threshold_for_regime(regime),
        stop_loss_pct=get_stop_loss_for_regime(regime),
        max_exposure=get_max_exposure_for_regime(regime),
        mr_enabled=is_mr_enabled_for_regime(regime),
    )


class VIXDataFeed:
    """
    VIX data feed manager.

    Handles VIX data retrieval and caching for the algorithm.
    """

    def __init__(self, algorithm: Optional["QCAlgorithm"] = None):
        """
        Initialize VIX data feed.

        Args:
            algorithm: QuantConnect algorithm instance.
        """
        self.algorithm = algorithm
        self._current_vix: float = 15.0  # Default to normal
        self._last_update_date: Optional[str] = None
        self._vix_history: list = []  # Recent VIX values for analysis

    def log(self, message: str) -> None:
        """Log via algorithm or skip for testing."""
        if self.algorithm:
            self.algorithm.Log(message)

    def update_vix(self, vix_value: float, date_str: str) -> VIXRegimeState:
        """
        Update VIX value and return regime state.

        Args:
            vix_value: New VIX value.
            date_str: Date of the update (for logging).

        Returns:
            Updated VIXRegimeState.
        """
        self._current_vix = vix_value
        self._last_update_date = date_str

        # Keep history for analysis (last 20 values)
        self._vix_history.append(vix_value)
        if len(self._vix_history) > 20:
            self._vix_history = self._vix_history[-20:]

        state = get_vix_regime_state(vix_value)

        self.log(
            f"VIX: Updated to {vix_value:.2f} | "
            f"Regime={state.regime.value} | "
            f"MR_Alloc={state.mr_allocation:.0%} | "
            f"MR_Enabled={state.mr_enabled}"
        )

        return state

    def get_current_vix(self) -> float:
        """Get current VIX value."""
        return self._current_vix

    def get_current_regime(self) -> VIXRegime:
        """Get current VIX regime."""
        return classify_vix_regime(self._current_vix)

    def get_current_state(self) -> VIXRegimeState:
        """Get current VIX regime state with all parameters."""
        return get_vix_regime_state(self._current_vix)

    def is_mr_allowed(self) -> bool:
        """Check if MR is allowed based on current VIX."""
        return is_mr_enabled_for_regime(self.get_current_regime())

    def get_vix_trend(self) -> str:
        """
        Get VIX trend direction based on recent history.

        Returns:
            "RISING", "FALLING", or "STABLE".
        """
        if len(self._vix_history) < 5:
            return "STABLE"

        recent = self._vix_history[-5:]
        first_avg = sum(recent[:2]) / 2
        last_avg = sum(recent[-2:]) / 2

        change_pct = (last_avg - first_avg) / first_avg if first_avg > 0 else 0

        if change_pct > 0.10:  # >10% increase
            return "RISING"
        elif change_pct < -0.10:  # >10% decrease
            return "FALLING"
        else:
            return "STABLE"

    def validate_vix_value(self, vix_value: float) -> bool:
        """
        Validate VIX value is within reasonable range.

        Args:
            vix_value: VIX value to validate.

        Returns:
            True if value is reasonable (5-100 range).
        """
        return 5.0 <= vix_value <= 100.0

    def get_state_for_persistence(self) -> Dict[str, Any]:
        """Get state for ObjectStore persistence."""
        return {
            "current_vix": self._current_vix,
            "last_update_date": self._last_update_date,
            "vix_history": self._vix_history,
        }

    def load_state(self, state: Dict[str, Any]) -> None:
        """Load state from ObjectStore."""
        self._current_vix = state.get("current_vix", 15.0)
        self._last_update_date = state.get("last_update_date")
        self._vix_history = state.get("vix_history", [])
        self.log(
            f"VIX: State loaded | "
            f"VIX={self._current_vix:.2f} | "
            f"Regime={self.get_current_regime().value}"
        )

    def reset(self) -> None:
        """Reset VIX data feed to default state."""
        self._current_vix = 15.0
        self._last_update_date = None
        self._vix_history = []
        self.log("VIX: Data feed reset to defaults")
