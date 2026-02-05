"""
Regime Engine - Multi-factor market state scoring system.

V3.0: Added VIX Direction factor for same-day crash detection.
The Micro Regime Engine detected Aug 2015 crash 3 days before Daily Regime.
VIX Direction captures momentum in fear, not just level.

Calculates a 0-100 regime score from 7 factors (normalized to 100%):
- Trend (20%): Price position vs moving averages (lagging)
- VIX Level (15%): Implied volatility level
- VIX Direction (15%): V3.0 NEW - VIX momentum (leading indicator, clamped 25-75)
- Breadth (15%): RSP vs SPY performance spread
- Credit (15%): HYG vs IEF performance spread (leading)
- Chop (10%): ADX-based trend quality
- Volatility (10%): Realized vol percentile ranking (lagging)

VIX Direction Safeguard: Score clamped to 25-75 range to prevent single-factor
boundary crossings. At 15% weight, this limits max swing to 7.5 points.

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
    clamp,
    credit_factor_score,
    credit_spread,
    period_return,
    realized_volatility,
    smooth_regime_score,
    trend_factor_score,
    vix_direction_score,
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
    vix_direction_score: float = 50.0  # V3.0 NEW: VIX momentum score
    vix_prior: float = 0.0  # V3.0 NEW: Prior day VIX for direction calc

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for logging or persistence."""
        return {
            "smoothed_score": round(self.smoothed_score, 2),
            "raw_score": round(self.raw_score, 2),
            "state": self.state.value,
            "trend_score": round(self.trend_score, 2),
            "vix_score": round(self.vix_score, 2),
            "vix_direction_score": round(self.vix_direction_score, 2),  # V3.0 NEW
            "volatility_score": round(self.volatility_score, 2),
            "breadth_score": round(self.breadth_score, 2),
            "credit_score": round(self.credit_score, 2),
            "chop_score": round(self.chop_score, 2),
            "vix_level": round(self.vix_level, 2),
            "vix_prior": round(self.vix_prior, 2),  # V3.0 NEW
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
        """Human-readable summary for logging (V3.0: includes VIX Direction)."""
        return (
            f"RegimeState({self.state.value} | "
            f"Score={self.smoothed_score:.1f} | "
            f"T={self.trend_score:.0f} VIX={self.vix_score:.0f} VD={self.vix_direction_score:.0f} "
            f"RV={self.volatility_score:.0f} B={self.breadth_score:.0f} C={self.credit_score:.0f} "
            f"ADX={self.chop_score:.0f} | "
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
        self._vix_prior: float = 0.0  # V3.0: Track prior VIX for direction calc

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

        # V3.0: Calculate VIX direction score (detects crashes same-day like Micro Regime)
        # Use prior VIX to calculate momentum - spiking VIX = regime deteriorating
        vix_dir_score = 50.0  # Default neutral
        vix_dir_score_raw = 50.0  # For logging pre-clamp value
        vix_dir_weight = 0.0  # Default disabled
        if config.VIX_DIRECTION_ENABLED and self._vix_prior > 0:
            vix_dir_score_raw = vix_direction_score(
                vix_current=vix_level,
                vix_prior=self._vix_prior,
                spiking_threshold=config.VIX_DAILY_DIRECTION_SPIKING,
                rising_fast_threshold=config.VIX_DAILY_DIRECTION_RISING_FAST,
                rising_threshold=config.VIX_DAILY_DIRECTION_RISING,
                falling_threshold=config.VIX_DAILY_DIRECTION_FALLING,
                falling_fast_threshold=config.VIX_DAILY_DIRECTION_FALLING_FAST,
                score_spiking=config.VIX_DIRECTION_SCORE_SPIKING,
                score_rising_fast=config.VIX_DIRECTION_SCORE_RISING_FAST,
                score_rising=config.VIX_DIRECTION_SCORE_RISING,
                score_stable=config.VIX_DIRECTION_SCORE_STABLE,
                score_falling=config.VIX_DIRECTION_SCORE_FALLING,
                score_falling_fast=config.VIX_DIRECTION_SCORE_FALLING_FAST,
            )
            # V3.0: Clamp VIX direction score to prevent single-factor regime boundary crossings
            # At 15% weight, clamping to 25-75 limits max swing to 7.5 points
            vix_dir_score = clamp(
                vix_dir_score_raw,
                config.VIX_DIRECTION_SCORE_CLAMP_MIN,
                config.VIX_DIRECTION_SCORE_CLAMP_MAX,
            )
            vix_dir_weight = config.VIX_DIRECTION_WEIGHT

        # Store current VIX for next calculation's direction
        vix_prior_for_state = self._vix_prior  # Save for RegimeState before updating
        self._vix_prior = vix_level

        # Aggregate raw score (V3.0: includes VIX Direction)
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
            vix_direction_score=vix_dir_score,
            weight_vix_direction=vix_dir_weight,
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
            vix_direction_score=vix_dir_score,  # V3.0 NEW
            vix_prior=vix_prior_for_state,  # V3.0 NEW
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
        self._vix_prior = 0.0  # V3.0: Reset VIX prior
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

    def get_vix_prior(self) -> float:
        """Get the prior VIX value for state persistence (V3.0)."""
        return self._vix_prior

    def set_vix_prior(self, vix_prior: float) -> None:
        """
        Set the prior VIX value (for state restoration, V3.0).

        Args:
            vix_prior: Prior VIX value to restore.
        """
        self._vix_prior = vix_prior
