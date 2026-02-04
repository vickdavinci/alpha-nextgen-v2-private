"""
Regime Engine - 4-factor market state scoring system.

Calculates a 0-100 regime score from four factors:
- Trend (35%): Price position vs moving averages
- Volatility (25%): Realized vol percentile ranking
- Breadth (25%): RSP vs SPY performance spread
- Credit (15%): HYG vs IEF performance spread

The smoothed score maps to regime states that drive system behavior:
- RISK_ON (70-100): Full leverage, no hedges
- NEUTRAL (50-69): Full leverage, no hedges
- CAUTIOUS (40-49): Full leverage, 10% TMF hedge
- DEFENSIVE (30-39): Reduced leverage, 15% TMF + 5% PSQ
- RISK_OFF (0-29): No new longs, 20% TMF + 10% PSQ

Spec: docs/04-regime-engine.md
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from AlgorithmImports import QCAlgorithm

import config
from models.enums import RegimeLevel
from utils.calculations import (
    aggregate_regime_score,
    breadth_factor_score,
    breadth_spread,
    chop_factor_score,
    credit_factor_score,
    credit_spread,
    period_return,
    realized_volatility,
    smooth_regime_score,
    trend_factor_score,
    vix_factor_score,
    volatility_factor_score,
    volatility_percentile,
)


@dataclass
class RegimeState:
    """
    Complete regime calculation output (V2.26: includes Chop).

    Contains the smoothed score, classification, component scores,
    raw values, and derived flags/targets for other engines.
    """

    # Primary outputs
    smoothed_score: float
    raw_score: float
    state: RegimeLevel

    # Component scores (for logging/debugging)
    trend_score: float
    vix_score: float  # V2.3 NEW: Implied volatility score
    volatility_score: float  # Realized volatility score
    breadth_score: float
    credit_score: float

    # Component raw values
    vix_level: float  # V2.3 NEW: Raw VIX value
    realized_vol: float
    vol_percentile: float
    breadth_spread_value: float
    credit_spread_value: float

    # Derived flags
    new_longs_allowed: bool
    cold_start_allowed: bool

    # Hedge targets
    tmf_target_pct: float
    psq_target_pct: float

    # Fields with defaults (must come after non-default fields)
    previous_smoothed: float = 50.0
    chop_score: float = 50.0  # V2.26 NEW: Trend quality score (ADX-based)
    spy_adx_value: float = 25.0  # V2.26 NEW: Raw SPY ADX(14) value

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for logging or persistence."""
        return {
            "smoothed_score": round(self.smoothed_score, 2),
            "raw_score": round(self.raw_score, 2),
            "state": self.state.value,
            "trend_score": round(self.trend_score, 2),
            "vix_score": round(self.vix_score, 2),
            "volatility_score": round(self.volatility_score, 2),
            "breadth_score": round(self.breadth_score, 2),
            "credit_score": round(self.credit_score, 2),
            "chop_score": round(self.chop_score, 2),
            "vix_level": round(self.vix_level, 2),
            "spy_adx_value": round(self.spy_adx_value, 2),
            "realized_vol": round(self.realized_vol, 4),
            "vol_percentile": round(self.vol_percentile, 2),
            "breadth_spread": round(self.breadth_spread_value, 4),
            "credit_spread": round(self.credit_spread_value, 4),
            "new_longs_allowed": self.new_longs_allowed,
            "cold_start_allowed": self.cold_start_allowed,
            "tmf_target_pct": round(self.tmf_target_pct, 2),
            "psq_target_pct": round(self.psq_target_pct, 2),
        }

    def __str__(self) -> str:
        """Human-readable summary for logging (V2.26: includes Chop)."""
        return (
            f"RegimeState({self.state.value} | "
            f"Score={self.smoothed_score:.1f} | "
            f"T={self.trend_score:.0f} VIX={self.vix_score:.0f} RV={self.volatility_score:.0f} "
            f"B={self.breadth_score:.0f} C={self.credit_score:.0f} C_ADX={self.chop_score:.0f} | "
            f"Hedge: TMF={self.tmf_target_pct:.0%} PSQ={self.psq_target_pct:.0%})"
        )


class RegimeEngine:
    """
    Four-factor market regime scoring engine.

    Calculates regime score from proxy symbols (SPY, RSP, HYG, IEF)
    and classifies market state for use by other engines.

    Usage:
        engine = RegimeEngine(algorithm)
        state = engine.calculate(spy_prices, rsp_prices, hyg_prices, ief_prices)
        if state.new_longs_allowed:
            # proceed with entry signals

    Note:
        This engine does NOT place orders. It only provides market state
        information for other engines to use in their decision-making.
    """

    def __init__(self, algorithm: Optional["QCAlgorithm"] = None):
        """
        Initialize the Regime Engine.

        Args:
            algorithm: QuantConnect algorithm instance for logging.
                      Can be None for unit testing.
        """
        self.algorithm = algorithm
        self._previous_smoothed_score: float = 50.0  # Start at neutral
        self._vol_history: List[float] = []  # For percentile calculation

    def log(self, message: str) -> None:
        """Log message via algorithm or print for testing."""
        if self.algorithm:
            self.algorithm.Log(message)

    def calculate(
        self,
        spy_closes: List[float],
        rsp_closes: List[float],
        hyg_closes: List[float],
        ief_closes: List[float],
        spy_sma20: float,
        spy_sma50: float,
        spy_sma200: float,
        vix_level: float = 20.0,
        spy_adx: float = 25.0,
    ) -> RegimeState:
        """
        Calculate regime state from proxy data (V2.26: includes Chop).

        Args:
            spy_closes: List of SPY closing prices (most recent last).
                       Needs at least 21 for volatility calculation.
            rsp_closes: List of RSP closing prices (at least 21).
            hyg_closes: List of HYG closing prices (at least 21).
            ief_closes: List of IEF closing prices (at least 21).
            spy_sma20: Current SPY 20-day SMA.
            spy_sma50: Current SPY 50-day SMA.
            spy_sma200: Current SPY 200-day SMA.
            vix_level: Current VIX value (V2.3 NEW, default 20.0).
            spy_adx: Current SPY ADX(14) value (V2.26 NEW, default 25.0).

        Returns:
            RegimeState with all scores, flags, and hedge targets.

        Raises:
            ValueError: If insufficient data provided.
        """
        # Validate inputs
        min_length = config.BREADTH_LOOKBACK + 1  # Need 21 prices for 20 returns
        if len(spy_closes) < min_length:
            raise ValueError(f"Need at least {min_length} SPY prices, got {len(spy_closes)}")

        # Current price is the last close
        current_price = spy_closes[-1]

        # Calculate trend factor
        trend = trend_factor_score(
            price=current_price,
            sma20=spy_sma20,
            sma50=spy_sma50,
            sma200=spy_sma200,
            extended_threshold=config.EXTENDED_THRESHOLD,
            oversold_threshold=config.OVERSOLD_THRESHOLD,
        )

        # Calculate VIX factor (V2.3 NEW: Implied volatility)
        vix = vix_factor_score(
            vix_level=vix_level,
            low_threshold=config.VIX_LOW_THRESHOLD,
            normal_threshold=config.VIX_NORMAL_THRESHOLD,
            high_threshold=config.VIX_HIGH_THRESHOLD,
            extreme_threshold=config.VIX_EXTREME_THRESHOLD,
        )

        # Calculate realized volatility factor
        daily_returns = self._calculate_returns(spy_closes)
        current_vol = realized_volatility(daily_returns[-config.VOL_LOOKBACK :])

        # Update volatility history for percentile calculation
        self._update_vol_history(current_vol)
        vol_pct = volatility_percentile(current_vol, self._vol_history)

        volatility = volatility_factor_score(vol_pct, current_vol)

        # Calculate breadth factor (RSP vs SPY)
        rsp_return = period_return(rsp_closes[-config.BREADTH_LOOKBACK - 1], rsp_closes[-1])
        spy_return = period_return(spy_closes[-config.BREADTH_LOOKBACK - 1], spy_closes[-1])
        breadth_spread_val = breadth_spread(rsp_return, spy_return)
        breadth = breadth_factor_score(breadth_spread_val)

        # Calculate credit factor (HYG vs IEF)
        hyg_return = period_return(hyg_closes[-config.CREDIT_LOOKBACK - 1], hyg_closes[-1])
        ief_return = period_return(ief_closes[-config.CREDIT_LOOKBACK - 1], ief_closes[-1])
        credit_spread_val = credit_spread(hyg_return, ief_return)
        credit = credit_factor_score(credit_spread_val)

        # Calculate chop factor (V2.26 NEW: ADX-based trend quality)
        chop = chop_factor_score(
            adx_value=spy_adx,
            strong=config.CHOP_ADX_THRESHOLD_STRONG,
            moderate=config.CHOP_ADX_THRESHOLD_MODERATE,
            weak=config.CHOP_ADX_THRESHOLD_WEAK,
        )

        # Aggregate raw score (V2.26: includes Chop)
        raw = aggregate_regime_score(
            trend_score=trend,
            vol_score=volatility,
            breadth_score=breadth,
            credit_score=credit,
            weight_trend=config.WEIGHT_TREND,
            weight_vol=config.WEIGHT_VOLATILITY,
            weight_breadth=config.WEIGHT_BREADTH,
            weight_credit=config.WEIGHT_CREDIT,
            vix_score=vix,
            weight_vix=config.WEIGHT_VIX,
            chop_score=chop,
            weight_chop=config.WEIGHT_CHOP,
        )

        # Apply exponential smoothing
        smoothed = smooth_regime_score(
            raw_score=raw,
            previous_smoothed=self._previous_smoothed_score,
            alpha=config.REGIME_SMOOTHING_ALPHA,
        )

        # Update state for next calculation
        self._previous_smoothed_score = smoothed

        # Classify regime state
        state = self._classify_regime(smoothed)

        # Determine flags
        new_longs_allowed = smoothed >= config.REGIME_DEFENSIVE
        cold_start_allowed = smoothed > config.REGIME_NEUTRAL

        # Determine hedge targets
        tmf_pct, psq_pct = self._calculate_hedge_targets(smoothed)

        regime_state = RegimeState(
            smoothed_score=smoothed,
            raw_score=raw,
            state=state,
            trend_score=trend,
            vix_score=vix,
            volatility_score=volatility,
            breadth_score=breadth,
            credit_score=credit,
            chop_score=chop,
            vix_level=vix_level,
            realized_vol=current_vol,
            vol_percentile=vol_pct,
            breadth_spread_value=breadth_spread_val,
            credit_spread_value=credit_spread_val,
            spy_adx_value=spy_adx,
            new_longs_allowed=new_longs_allowed,
            cold_start_allowed=cold_start_allowed,
            tmf_target_pct=tmf_pct,
            psq_target_pct=psq_pct,
            previous_smoothed=self._previous_smoothed_score,
        )

        self.log(f"REGIME: {regime_state}")

        return regime_state

    def _calculate_returns(self, prices: List[float]) -> List[float]:
        """Calculate daily returns from price series."""
        if len(prices) < 2:
            return []
        returns = []
        for i in range(1, len(prices)):
            if prices[i - 1] != 0:
                returns.append((prices[i] - prices[i - 1]) / prices[i - 1])
        return returns

    def _update_vol_history(self, current_vol: float) -> None:
        """
        Maintain rolling volatility history for percentile calculation.

        Keeps up to VOL_PERCENTILE_LOOKBACK (252) readings.
        """
        self._vol_history.append(current_vol)
        if len(self._vol_history) > config.VOL_PERCENTILE_LOOKBACK:
            self._vol_history = self._vol_history[-config.VOL_PERCENTILE_LOOKBACK :]

    def _classify_regime(self, score: float) -> RegimeLevel:
        """
        Classify smoothed score into regime state.

        Args:
            score: Smoothed regime score (0-100).

        Returns:
            RegimeLevel enum value.
        """
        if score >= config.REGIME_RISK_ON:
            return RegimeLevel.RISK_ON
        elif score >= config.REGIME_NEUTRAL:
            return RegimeLevel.NEUTRAL
        elif score >= config.REGIME_CAUTIOUS:
            return RegimeLevel.CAUTIOUS
        elif score >= config.REGIME_DEFENSIVE:
            return RegimeLevel.DEFENSIVE
        else:
            return RegimeLevel.RISK_OFF

    def _calculate_hedge_targets(self, score: float) -> tuple:
        """
        Determine hedge allocation percentages based on regime score.

        Args:
            score: Smoothed regime score (0-100).

        Returns:
            Tuple of (tmf_pct, psq_pct).
        """
        if score >= config.HEDGE_LEVEL_1:
            # Score >= 40: No hedges
            return (0.0, 0.0)
        elif score >= config.HEDGE_LEVEL_2:
            # Score 30-39: Light hedge (TMF only)
            return (config.TMF_LIGHT, 0.0)
        elif score >= config.HEDGE_LEVEL_3:
            # Score 20-29: Medium hedge
            return (config.TMF_MEDIUM, config.PSQ_MEDIUM)
        else:
            # Score < 20: Full hedge
            return (config.TMF_FULL, config.PSQ_FULL)

    def reset(self) -> None:
        """Reset engine state (used after kill switch or for testing)."""
        self._previous_smoothed_score = 50.0
        self._vol_history = []
        self.log("REGIME: Engine reset to neutral (score=50)")

    def get_previous_score(self) -> float:
        """Get the previous smoothed score for continuity."""
        return self._previous_smoothed_score

    def set_previous_score(self, score: float) -> None:
        """
        Set the previous smoothed score (for state restoration).

        Args:
            score: Score to restore (typically from persistence).
        """
        self._previous_smoothed_score = score

    def set_vol_history(self, history: List[float]) -> None:
        """
        Set volatility history (for state restoration).

        Args:
            history: List of historical volatility readings.
        """
        self._vol_history = history[-config.VOL_PERCENTILE_LOOKBACK :]
