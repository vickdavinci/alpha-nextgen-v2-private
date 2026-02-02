# =============================================================================
# ENGINES PACKAGE - V2 Core-Satellite Architecture
# =============================================================================
#
# This package provides re-exports from core/ and satellite/ subdirectories
# for backwards compatibility with V1 imports.
#
# Core Engines (foundational, always active):
#   - RegimeEngine: Market state detection (0-100 score)
#   - CapitalEngine: Position sizing and phase management
#   - RiskEngine: Circuit breakers and safeguards
#   - ColdStartEngine: Days 1-5 warm entry logic
#   - TrendEngine: BB compression breakout signals (70% allocation)
#
# Satellite Engines (conditional, opportunistic):
#   - MeanReversionEngine: Intraday oversold bounce (0-10% allocation)
#   - HedgeEngine: Regime-based TMF/PSQ overlay
#   - OptionsEngine: QQQ options (20-30% allocation)
#
# =============================================================================

from engines.core.capital_engine import CapitalEngine, CapitalState
from engines.core.cold_start_engine import ColdStartEngine, ColdStartState

# Core engine re-exports
from engines.core.regime_engine import RegimeEngine, RegimeState
from engines.core.risk_engine import RiskCheckResult, RiskEngine, SafeguardStatus, SafeguardType
from engines.core.trend_engine import TrendEngine, TrendPosition
from engines.satellite.hedge_engine import HedgeAllocation, HedgeEngine

# Satellite engine re-exports
from engines.satellite.mean_reversion_engine import MeanReversionEngine, MRPosition

__all__ = [
    # Core
    "RegimeEngine",
    "RegimeState",
    "CapitalEngine",
    "CapitalState",
    "RiskEngine",
    "RiskCheckResult",
    "SafeguardType",
    "SafeguardStatus",
    "ColdStartEngine",
    "ColdStartState",
    "TrendEngine",
    "TrendPosition",
    # Satellite
    "MeanReversionEngine",
    "MRPosition",
    "HedgeEngine",
    "HedgeAllocation",
]
