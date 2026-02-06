"""
Regime Engine - Multi-factor market state scoring system.

V3.3: Simplified to 3-factor model to fix score compression in grinding bears.
The 7-factor model compressed scores to 43-50 during -30% bear markets because
each factor scored ~40-50. The new model uses 3 decisive factors:

  - Trend (35%): SPY vs MA200 - Is market going up or down?
  - VIX Level (30%): Fear/panic level
  - Drawdown (35%): Distance from 52-week high - Breaks compression!

V3.3 Guards (not weighted, just safety valves):
  - VIX Direction Shock Cap: Caps regime at CAUTIOUS when VIX spiking
  - Recovery Hysteresis: Prevents "sticky bear" after V-shaped recoveries

V3.0: Added VIX Direction factor for same-day crash detection.
The Micro Regime Engine detected Aug 2015 crash 3 days before Daily Regime.
VIX Direction captures momentum in fear, not just level.

Legacy 7-factor model (if V3_REGIME_SIMPLIFIED_ENABLED = False):
- Trend (20%): Price position vs moving averages (lagging)
- VIX Level (15%): Implied volatility level
- VIX Direction (15%): VIX momentum (leading indicator, clamped 25-75)
- Breadth (15%): RSP vs SPY performance spread
- Credit (15%): HYG vs IEF performance spread (leading)
- Chop (10%): ADX-based trend quality
- Volatility (10%): Realized vol percentile ranking (lagging)

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
from utils.calculations import drawdown_factor_score  # V3.3 NEW
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

    # V3.3 NEW: Simplified 3-factor model fields
    drawdown_score: float = 50.0  # V3.3: Drawdown from 52w high
    drawdown_pct: float = 0.0  # V3.3: Raw drawdown percentage
    spy_52w_high: float = 0.0  # V3.3: 52-week high for drawdown calc
    shock_cap_active: bool = False  # V3.3: VIX shock cap currently active
    recovery_days: int = 0  # V3.3: Days of consecutive improvement
    using_simplified_model: bool = False  # V3.3: True if using 3-factor model

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
            # V3.3 NEW fields
            "drawdown_score": round(self.drawdown_score, 2),
            "drawdown_pct": round(self.drawdown_pct, 4),
            "spy_52w_high": round(self.spy_52w_high, 2),
            "shock_cap_active": self.shock_cap_active,
            "recovery_days": self.recovery_days,
            "using_simplified_model": self.using_simplified_model,
        }

    def __str__(self) -> str:
        """Human-readable summary for logging (V3.3: simplified or legacy format)."""
        if self.using_simplified_model:
            # V3.3: Simplified 3-factor format
            shock_flag = " [SHOCK_CAP]" if self.shock_cap_active else ""
            return (
                f"RegimeState({self.state.value} | "
                f"Score={self.smoothed_score:.1f}{shock_flag} | "
                f"T={self.trend_score:.0f} VIX={self.vix_score:.0f} DD={self.drawdown_score:.0f} "
                f"({self.drawdown_pct:.1%}) | "
                f"Recovery={self.recovery_days}d | "
                f"Hedge: TMF={self.tmf_target_pct:.0%} PSQ={self.psq_target_pct:.0%})"
            )
        else:
            # Legacy 7-factor format
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

        # V3.3: State tracking for simplified model guards
        self._spy_52w_high: float = 0.0  # Track 52-week high for drawdown
        self._shock_cap_active: bool = False  # VIX shock cap currently active
        self._shock_cap_days_remaining: int = 0  # Days until shock cap decays
        self._recovery_days: int = 0  # Consecutive days of improvement
        self._previous_regime: RegimeLevel = RegimeLevel.NEUTRAL  # For hysteresis

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
        spy_52w_high: float = 0.0,  # V3.3 NEW: For drawdown calculation
    ) -> RegimeState:
        """
        Calculate regime state from proxy data.

        V3.3: If V3_REGIME_SIMPLIFIED_ENABLED, uses 3-factor model:
        - Trend (35%): SPY vs MA200
        - VIX Level (30%): Fear/panic
        - Drawdown (35%): Distance from 52-week high

        Otherwise falls back to legacy 7-factor model.

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
            spy_52w_high: SPY 52-week high (V3.3 NEW, for drawdown calc).

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

        # =====================================================================
        # V3.3: SIMPLIFIED 3-FACTOR MODEL
        # =====================================================================
        use_simplified = getattr(config, "V3_REGIME_SIMPLIFIED_ENABLED", False)

        if use_simplified:
            # Use max of available prices as fallback for 52w high if not provided
            effective_52w_high = spy_52w_high
            if effective_52w_high <= 0 and len(spy_closes) > 0:
                effective_52w_high = max(spy_closes)

            return self._calculate_simplified(
                current_price=current_price,
                spy_sma200=spy_sma200,
                vix_level=vix_level,
                spy_52w_high=effective_52w_high,
                trend_score=trend,
                vix_score=vix,
            )

        # =====================================================================
        # LEGACY 7-FACTOR MODEL (V3.0)
        # =====================================================================

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

    def _calculate_simplified(
        self,
        current_price: float,
        spy_sma200: float,
        vix_level: float,
        spy_52w_high: float,
        trend_score: float,
        vix_score: float,
    ) -> RegimeState:
        """
        V3.3: Calculate regime using simplified 3-factor model.

        Factors:
        - Trend (35%): SPY vs MA200
        - VIX Level (30%): Fear/panic level
        - Drawdown (35%): Distance from 52-week high

        Guards:
        - VIX Direction Shock Cap: Caps regime at CAUTIOUS when VIX spiking
        - Recovery Hysteresis: Prevents "sticky bear" after V-shaped recoveries
        """
        # Update 52-week high tracking (rolling max)
        if spy_52w_high > 0:
            self._spy_52w_high = max(self._spy_52w_high, spy_52w_high)
        if self._spy_52w_high <= 0:
            self._spy_52w_high = current_price  # Initialize if not set

        # Calculate drawdown factor
        drawdown_pct = 0.0
        if self._spy_52w_high > 0:
            drawdown_pct = (self._spy_52w_high - current_price) / self._spy_52w_high

        drawdown = drawdown_factor_score(
            current_price=current_price,
            high_52w=self._spy_52w_high,
            threshold_bull=getattr(config, "DRAWDOWN_THRESHOLD_BULL", 0.05),
            threshold_correction=getattr(config, "DRAWDOWN_THRESHOLD_CORRECTION", 0.10),
            threshold_pullback=getattr(config, "DRAWDOWN_THRESHOLD_PULLBACK", 0.15),
            threshold_bear=getattr(config, "DRAWDOWN_THRESHOLD_BEAR", 0.20),
            score_bull=getattr(config, "DRAWDOWN_SCORE_BULL", 90.0),
            score_correction=getattr(config, "DRAWDOWN_SCORE_CORRECTION", 70.0),
            score_pullback=getattr(config, "DRAWDOWN_SCORE_PULLBACK", 50.0),
            score_bear=getattr(config, "DRAWDOWN_SCORE_BEAR", 30.0),
            score_deep_bear=getattr(config, "DRAWDOWN_SCORE_DEEP_BEAR", 10.0),
        )

        # Calculate 3-factor core score
        weight_trend = getattr(config, "WEIGHT_TREND_V3", 0.35)
        weight_vix = getattr(config, "WEIGHT_VIX_V3", 0.30)
        weight_drawdown = getattr(config, "WEIGHT_DRAWDOWN_V3", 0.35)

        raw = trend_score * weight_trend + vix_score * weight_vix + drawdown * weight_drawdown

        # Store current VIX for shock cap calculation
        vix_prior_for_state = self._vix_prior
        vix_change_pct = 0.0
        if self._vix_prior > 0:
            vix_change_pct = ((vix_level - self._vix_prior) / self._vix_prior) * 100
        self._vix_prior = vix_level

        # =====================================================================
        # GUARD 1: VIX Direction Shock Cap
        # =====================================================================
        shock_cap_active = False
        shock_threshold = getattr(config, "VIX_SHOCK_CAP_THRESHOLD", 10.0)
        shock_cap_enabled = getattr(config, "VIX_SHOCK_CAP_ENABLED", True)

        if shock_cap_enabled:
            # Check if VIX is spiking (new shock)
            if vix_change_pct >= shock_threshold:
                self._shock_cap_active = True
                self._shock_cap_days_remaining = getattr(config, "VIX_SHOCK_CAP_DECAY_DAYS", 2)
                self.log(f"REGIME: VIX SHOCK CAP ACTIVATED - VIX change={vix_change_pct:.1f}%")

            # Decay shock cap
            if self._shock_cap_active:
                if self._shock_cap_days_remaining > 0:
                    self._shock_cap_days_remaining -= 1
                    shock_cap_active = True
                else:
                    # Cap expired
                    self._shock_cap_active = False
                    self.log("REGIME: VIX shock cap expired")

        # Apply shock cap to raw score
        if shock_cap_active:
            shock_cap_max = getattr(config, "VIX_SHOCK_CAP_MAX_REGIME", 49)
            if raw > shock_cap_max:
                self.log(f"REGIME: Shock cap applied - raw {raw:.1f} capped to {shock_cap_max}")
                raw = shock_cap_max

        # Apply exponential smoothing
        smoothed = smooth_regime_score(
            raw_score=raw,
            previous_smoothed=self._previous_smoothed_score,
            alpha=config.REGIME_SMOOTHING_ALPHA,
        )

        # =====================================================================
        # GUARD 2: Recovery Hysteresis
        # =====================================================================
        hysteresis_enabled = getattr(config, "RECOVERY_HYSTERESIS_ENABLED", True)
        proposed_state = self._classify_regime(smoothed)

        if hysteresis_enabled:
            # Initialize previous regime from previous score if not set
            if (
                self._previous_regime == RegimeLevel.NEUTRAL
                and self._previous_smoothed_score != 50.0
            ):
                self._previous_regime = self._classify_regime(self._previous_smoothed_score)

            # Check if this is an upgrade (moving to higher/better regime)
            is_upgrade = self._is_regime_upgrade(self._previous_regime, proposed_state)

            if is_upgrade:
                # Upgrades require confirmation
                hysteresis_days = getattr(config, "RECOVERY_HYSTERESIS_DAYS", 2)
                hysteresis_vix_max = getattr(config, "RECOVERY_HYSTERESIS_VIX_MAX", 25.0)

                # Check if trend is improving (price > MA200)
                trend_improving = current_price > spy_sma200

                if trend_improving and vix_level < hysteresis_vix_max:
                    self._recovery_days += 1
                else:
                    self._recovery_days = 0

                if self._recovery_days < hysteresis_days:
                    # Not enough confirmation yet - block upgrade
                    proposed_state = self._previous_regime
                    self.log(
                        f"REGIME: Upgrade blocked by hysteresis - "
                        f"recovery_days={self._recovery_days}/{hysteresis_days}"
                    )
                else:
                    # Upgrade confirmed
                    self.log(f"REGIME: Upgrade confirmed after {self._recovery_days} days")
                    self._recovery_days = 0  # Reset for next upgrade
            else:
                # Downgrades are immediate
                self._recovery_days = 0

        # Update state for next calculation
        self._previous_smoothed_score = smoothed
        self._previous_regime = proposed_state

        # Determine flags
        new_longs_allowed = smoothed >= config.REGIME_DEFENSIVE
        cold_start_allowed = smoothed > config.REGIME_NEUTRAL

        # Determine hedge targets
        tmf_pct, psq_pct = self._calculate_hedge_targets(smoothed)

        regime_state = RegimeState(
            smoothed_score=smoothed,
            raw_score=raw,
            state=proposed_state,
            trend_score=trend_score,
            vix_score=vix_score,
            volatility_score=50.0,  # Not used in simplified model
            breadth_score=50.0,  # Not used in simplified model
            credit_score=50.0,  # Not used in simplified model
            chop_score=50.0,  # Not used in simplified model
            vix_level=vix_level,
            realized_vol=0.0,  # Not used in simplified model
            vol_percentile=0.0,  # Not used in simplified model
            breadth_spread_value=0.0,  # Not used in simplified model
            credit_spread_value=0.0,  # Not used in simplified model
            spy_adx_value=0.0,  # Not used in simplified model
            new_longs_allowed=new_longs_allowed,
            cold_start_allowed=cold_start_allowed,
            tmf_target_pct=tmf_pct,
            psq_target_pct=psq_pct,
            previous_smoothed=self._previous_smoothed_score,
            vix_direction_score=50.0,  # Not used in simplified model
            vix_prior=vix_prior_for_state,
            # V3.3 fields
            drawdown_score=drawdown,
            drawdown_pct=drawdown_pct,
            spy_52w_high=self._spy_52w_high,
            shock_cap_active=shock_cap_active,
            recovery_days=self._recovery_days,
            using_simplified_model=True,
        )

        self.log(f"REGIME: {regime_state}")

        return regime_state

    def _is_regime_upgrade(self, previous: RegimeLevel, proposed: RegimeLevel) -> bool:
        """Check if proposed regime is an upgrade (better) from previous."""
        regime_order = {
            RegimeLevel.RISK_OFF: 0,
            RegimeLevel.DEFENSIVE: 1,
            RegimeLevel.CAUTIOUS: 2,
            RegimeLevel.NEUTRAL: 3,
            RegimeLevel.RISK_ON: 4,
        }
        return regime_order.get(proposed, 0) > regime_order.get(previous, 0)

    def reset(self) -> None:
        """Reset engine state (used after kill switch or for testing)."""
        self._previous_smoothed_score = 50.0
        self._vol_history = []
        self._vix_prior = 0.0  # V3.0: Reset VIX prior
        # V3.3: Reset simplified model state
        self._spy_52w_high = 0.0
        self._shock_cap_active = False
        self._shock_cap_days_remaining = 0
        self._recovery_days = 0
        self._previous_regime = RegimeLevel.NEUTRAL
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

    # =========================================================================
    # V3.3: STATE PERSISTENCE
    # =========================================================================

    def get_state_for_persistence(self) -> Dict[str, Any]:
        """
        V3.3: Get all regime engine state for persistence.

        Returns:
            Dictionary containing all state needed for restoration.
        """
        return {
            "previous_smoothed": self._previous_smoothed_score,
            "vix_prior": self._vix_prior,
            "vol_history": self._vol_history[-100:] if self._vol_history else [],
            # V3.3 simplified model state
            "spy_52w_high": self._spy_52w_high,
            "shock_cap_active": self._shock_cap_active,
            "shock_cap_days_remaining": self._shock_cap_days_remaining,
            "recovery_days": self._recovery_days,
            "previous_regime": self._previous_regime.value if self._previous_regime else "NEUTRAL",
        }

    def restore_state(self, state: Dict[str, Any]) -> None:
        """
        V3.3: Restore regime engine state from persistence.

        Args:
            state: Dictionary from get_state_for_persistence().
        """
        if not state:
            return

        # Core state
        self._previous_smoothed_score = state.get("previous_smoothed", 50.0)
        self._vix_prior = state.get("vix_prior", 0.0)
        vol_history = state.get("vol_history", [])
        if vol_history:
            self._vol_history = vol_history[-config.VOL_PERCENTILE_LOOKBACK :]

        # V3.3 simplified model state
        self._spy_52w_high = state.get("spy_52w_high", 0.0)
        self._shock_cap_active = state.get("shock_cap_active", False)
        self._shock_cap_days_remaining = state.get("shock_cap_days_remaining", 0)
        self._recovery_days = state.get("recovery_days", 0)

        # Restore previous regime enum
        prev_regime_str = state.get("previous_regime", "NEUTRAL")
        try:
            self._previous_regime = RegimeLevel[prev_regime_str]
        except (KeyError, TypeError):
            self._previous_regime = RegimeLevel.NEUTRAL

        self.log(
            f"REGIME: State restored | Score={self._previous_smoothed_score:.1f} | "
            f"VIX_prior={self._vix_prior:.1f} | 52w_high={self._spy_52w_high:.2f} | "
            f"Shock_cap={self._shock_cap_active} | Recovery_days={self._recovery_days}"
        )
