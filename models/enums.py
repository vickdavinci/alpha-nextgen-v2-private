"""
Enums for Alpha NextGen trading system.

Contains:
- Urgency: Order execution timing (IMMEDIATE vs EOD)
- Phase: Capital phase (SEED vs GROWTH)
- RegimeLevel: Market regime classification
- ExposureGroup: Position grouping for exposure limits
"""

from enum import Enum


class Urgency(Enum):
    """
    Order execution urgency.

    IMMEDIATE: Execute now via MarketOrder (used by Mean Reversion, Risk events)
    EOD: Execute at next open via MarketOnOpenOrder (used by Trend, Hedge)
    """

    IMMEDIATE = "IMMEDIATE"
    EOD = "EOD"


class Phase(Enum):
    """
    Capital growth phase.

    SEED: Initial phase, conservative sizing (equity < $75,000)
    GROWTH: Growth phase, full sizing (equity >= $75,000)
    """

    SEED = "SEED"
    GROWTH = "GROWTH"


class RegimeLevel(Enum):
    """
    Market regime classification based on regime score (0-100).

    RISK_ON: Score 70-100, aggressive positioning, no hedges
    NEUTRAL: Score 50-69, balanced positioning, no hedges
    CAUTIOUS: Score 40-49, light hedge (10% TMF)
    DEFENSIVE: Score 30-39, medium hedge (15% TMF + 5% PSQ)
    RISK_OFF: Score 0-29, maximum hedge (20% TMF + 10% PSQ), no new longs
    """

    RISK_ON = "RISK_ON"
    NEUTRAL = "NEUTRAL"
    CAUTIOUS = "CAUTIOUS"
    DEFENSIVE = "DEFENSIVE"
    RISK_OFF = "RISK_OFF"


class ExposureGroup(Enum):
    """
    Position grouping for exposure limit calculations.

    Each group has maximum net long and gross exposure limits
    defined in config.py.
    """

    NASDAQ_BETA = "NASDAQ_BETA"  # TQQQ, QLD, SOXL, PSQ
    SPY_BETA = "SPY_BETA"  # SSO
    RATES = "RATES"  # TMF, SHV
