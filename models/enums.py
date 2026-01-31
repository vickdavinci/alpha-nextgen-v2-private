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


# =============================================================================
# V2.1.1 OPTIONS ENGINE ENUMS
# =============================================================================


class OptionsMode(Enum):
    """
    Options Engine operating mode based on DTE.

    V2.1.1 Dual-Mode Architecture:
    - SWING: 5-45 DTE, multi-day positions, uses macro regime
    - INTRADAY: 0-2 DTE, same-day trades, uses Micro Regime Engine
    """

    SWING = "SWING"  # 5-45 DTE, 15% allocation
    INTRADAY = "INTRADAY"  # 0-2 DTE, 5% allocation


class VIXDirection(Enum):
    """
    VIX Direction Classification for Micro Regime Engine.

    VIX level tells us WHERE we are.
    VIX direction tells us WHERE we're going.

    This is the key differentiator for intraday options trading.
    Same VIX level with different directions = OPPOSITE strategies!
    """

    FALLING_FAST = "FALLING_FAST"  # VIX change < -5%, strong recovery
    FALLING = "FALLING"  # VIX change -5% to -2%, recovery starting
    STABLE = "STABLE"  # VIX change ±2%, range-bound
    RISING = "RISING"  # VIX change +2% to +5%, fear building
    RISING_FAST = "RISING_FAST"  # VIX change +5% to +10%, panic emerging
    SPIKING = "SPIKING"  # VIX change > +10%, crash mode
    WHIPSAW = "WHIPSAW"  # Range > 3× net change, no clear direction


class VIXLevel(Enum):
    """
    VIX Level Classification for regime determination.

    VIX level indicates market fear state and determines
    whether mean reversion or momentum strategies dominate.
    """

    LOW = "LOW"  # VIX < 20: Normal market, mean reversion works
    MEDIUM = "MEDIUM"  # VIX 20-25: Elevated caution
    HIGH = "HIGH"  # VIX > 25: Fear elevated, momentum dominates


class MicroRegime(Enum):
    """
    Micro Regime classification for intraday trading (0-2 DTE).

    Combines VIX Level × VIX Direction = 21 distinct trading regimes.
    Each regime has specific strategy deployment rules.

    See docs/v2-specs/V2_1_OPTIONS_ENGINE_DESIGN.txt for full matrix.
    """

    # VIX LOW (< 20) regimes
    PERFECT_MR = "PERFECT_MR"  # VIX low + falling fast: Full fade + credits
    GOOD_MR = "GOOD_MR"  # VIX low + falling: Full fade
    NORMAL = "NORMAL"  # VIX low + stable: Standard fade
    CAUTION_LOW = "CAUTION_LOW"  # VIX low + rising: Half size
    TRANSITION = "TRANSITION"  # VIX low + rising fast: Skip MR
    RISK_OFF_LOW = "RISK_OFF_LOW"  # VIX low + spiking: Skip all
    CHOPPY_LOW = "CHOPPY_LOW"  # VIX low + whipsaw: Credits only

    # VIX MEDIUM (20-25) regimes
    RECOVERING = "RECOVERING"  # VIX medium + falling fast: Half fade
    IMPROVING = "IMPROVING"  # VIX medium + falling: Half fade
    CAUTIOUS = "CAUTIOUS"  # VIX medium + stable: Credits only
    WORSENING = "WORSENING"  # VIX medium + rising: Credits only
    DETERIORATING = "DETERIORATING"  # VIX medium + rising fast: ITM puts
    BREAKING = "BREAKING"  # VIX medium + spiking: ITM puts only
    UNSTABLE = "UNSTABLE"  # VIX medium + whipsaw: No trade

    # VIX HIGH (> 25) regimes
    PANIC_EASING = "PANIC_EASING"  # VIX high + falling fast: Small fade
    CALMING = "CALMING"  # VIX high + falling: Credits
    ELEVATED = "ELEVATED"  # VIX high + stable: ITM momentum
    WORSENING_HIGH = "WORSENING_HIGH"  # VIX high + rising: ITM puts only
    FULL_PANIC = "FULL_PANIC"  # VIX high + rising fast: Puts or none
    CRASH = "CRASH"  # VIX high + spiking: No trade, protect
    VOLATILE = "VOLATILE"  # VIX high + whipsaw: No trade, protect


class IntradayStrategy(Enum):
    """
    Intraday trading strategies for 0-2 DTE options.

    Each strategy is deployed based on Micro Regime classification.
    """

    DEBIT_FADE = "DEBIT_FADE"  # Mean reversion via debit spread
    CREDIT_SPREAD = "CREDIT_SPREAD"  # Premium collection
    ITM_MOMENTUM = "ITM_MOMENTUM"  # Ride the move with ITM options
    PROTECTIVE_PUTS = "PROTECTIVE_PUTS"  # Hedge during uncertainty
    NO_TRADE = "NO_TRADE"  # Too risky, sit out


class WhipsawState(Enum):
    """
    Whipsaw detection state based on VIX direction reversals.

    Tracked over rolling 1-hour window with 5-minute data points.
    """

    TRENDING = "TRENDING"  # 0-2 reversals: Normal trading
    CHOPPY = "CHOPPY"  # 3-4 reversals: Reduce size 50%
    WHIPSAW = "WHIPSAW"  # 5+ reversals: Credits only or no trade


class QQQMove(Enum):
    """
    V2.3.4: QQQ price move classification for Micro Regime Engine.

    QQQ move direction is critical for determining option direction:
    - QQQ UP + VIX FALLING = Strong fade setup (buy PUT)
    - QQQ DOWN + VIX FALLING = Recovery bounce (buy CALL)
    - QQQ UP + VIX RISING = Momentum, don't fade
    - QQQ FLAT = No edge, no trade
    """

    UP_STRONG = "UP_STRONG"  # QQQ > +0.8% from open
    UP = "UP"  # QQQ +0.3% to +0.8% from open
    FLAT = "FLAT"  # QQQ ±0.3% from open
    DOWN = "DOWN"  # QQQ -0.3% to -0.8% from open
    DOWN_STRONG = "DOWN_STRONG"  # QQQ < -0.8% from open
