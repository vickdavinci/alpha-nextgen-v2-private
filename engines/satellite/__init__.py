# =============================================================================
# SATELLITE ENGINES - Conditional, Opportunistic
# =============================================================================
#
# Satellite engines provide additional alpha sources:
#   - MeanReversionEngine: Intraday oversold bounce (0-10%)
#   - HedgeEngine: Regime-based TMF/PSQ overlay
#   - YieldSleeve: SHV cash management
#
# V2.1 NOTE: Options Engine will be added here (20-30% allocation)
#
# =============================================================================

from engines.satellite.mean_reversion_engine import MeanReversionEngine, MRPosition
from engines.satellite.hedge_engine import HedgeEngine, HedgeAllocation
from engines.satellite.yield_sleeve import YieldSleeve, YieldState

__all__ = [
    "MeanReversionEngine",
    "MRPosition",
    "HedgeEngine",
    "HedgeAllocation",
    "YieldSleeve",
    "YieldState",
]
