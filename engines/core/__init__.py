# =============================================================================
# CORE ENGINES - Foundational, Always Active
# =============================================================================
#
# Core engines form the foundation of the trading system:
#   - RegimeEngine: Market state detection
#   - CapitalEngine: Position sizing
#   - RiskEngine: Circuit breakers
#   - ColdStartEngine: Startup handling
#   - TrendEngine: Primary alpha generator (70%)
#
# =============================================================================

from engines.core.capital_engine import CapitalEngine, CapitalState
from engines.core.cold_start_engine import ColdStartEngine, ColdStartState
from engines.core.regime_engine import RegimeEngine, RegimeState
from engines.core.risk_engine import (
    KSTier,
    RiskCheckResult,
    RiskEngine,
    SafeguardStatus,
    SafeguardType,
)
from engines.core.trend_engine import TrendEngine, TrendPosition

__all__ = [
    "RegimeEngine",
    "RegimeState",
    "CapitalEngine",
    "CapitalState",
    "KSTier",
    "RiskEngine",
    "RiskCheckResult",
    "SafeguardType",
    "SafeguardStatus",
    "ColdStartEngine",
    "ColdStartState",
    "TrendEngine",
    "TrendPosition",
]
