# =============================================================================
# SATELLITE ENGINES - Conditional, Opportunistic
# =============================================================================
#
# Satellite engines provide additional alpha sources:
#   - MeanReversionEngine: Intraday oversold bounce (0-10%)
#   - HedgeEngine: Regime-based TMF/PSQ overlay
#   - OptionsEngine: QQQ options (20-30% allocation)
#
# =============================================================================

from engines.satellite.hedge_engine import HedgeAllocation, HedgeEngine
from engines.satellite.mean_reversion_engine import MeanReversionEngine, MRPosition

__all__ = [
    "MeanReversionEngine",
    "MRPosition",
    "HedgeEngine",
    "HedgeAllocation",
]
