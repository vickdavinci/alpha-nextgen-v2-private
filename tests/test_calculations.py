"""
Unit tests for utils/calculations.py

Tests all calculation utilities used across the Alpha NextGen system.
"""

import math

import pytest

from utils.calculations import (  # Helper utilities; Loss / percentage calculations; Volatility calculations; Relative performance; Moving average & price position; ATR-based calculations; Volume calculations; Position & capital calculations; Regime score components; Hedge allocation; Yield sleeve
    aggregate_regime_score,
    atr_multiplier_for_profit,
    bollinger_bandwidth,
    breadth_factor_score,
    breadth_spread,
    calculate_hedge_allocation,
    calculate_lockbox_amount,
    calculate_unallocated_cash,
    chandelier_stop,
    clamp,
    credit_factor_score,
    credit_spread,
    daily_loss_pct,
    engine_drop_pct,
    is_extended,
    is_oversold,
    is_vol_shock,
    ma_alignment_bearish,
    ma_alignment_bullish,
    max_position_size,
    period_return,
    position_value,
    price_to_sma_ratio,
    profit_pct,
    realized_volatility,
    rolling_mean,
    rolling_std_dev,
    safe_divide,
    shares_from_value,
    should_adjust_shv,
    smooth_regime_score,
    tradeable_equity,
    trend_factor_score,
    vol_shock_threshold,
    volatility_factor_score,
    volatility_percentile,
    volume_confirmation,
    volume_ratio,
    week_to_date_loss_pct,
)


class TestHelperUtilities:
    """Tests for helper utility functions."""

    def test_clamp_within_range(self):
        """Value within range returns unchanged."""
        assert clamp(50, 0, 100) == 50

    def test_clamp_below_minimum(self):
        """Value below minimum returns minimum."""
        assert clamp(-5, 0, 100) == 0

    def test_clamp_above_maximum(self):
        """Value above maximum returns maximum."""
        assert clamp(105, 0, 100) == 100

    def test_clamp_at_boundary(self):
        """Values at boundaries return unchanged."""
        assert clamp(0, 0, 100) == 0
        assert clamp(100, 0, 100) == 100

    def test_safe_divide_normal(self):
        """Normal division works correctly."""
        assert safe_divide(10, 2) == 5.0

    def test_safe_divide_by_zero(self):
        """Division by zero returns default."""
        assert safe_divide(10, 0) == 0.0

    def test_safe_divide_custom_default(self):
        """Division by zero with custom default."""
        assert safe_divide(10, 0, default=-1) == -1

    def test_rolling_mean_normal(self):
        """Mean calculation works correctly."""
        assert rolling_mean([1, 2, 3, 4, 5]) == 3.0

    def test_rolling_mean_empty(self):
        """Empty list returns 0."""
        assert rolling_mean([]) == 0.0

    def test_rolling_std_dev_normal(self):
        """Standard deviation calculation works correctly."""
        # Known test case: [2, 4, 4, 4, 5, 5, 7, 9] has std dev = 2.0
        result = rolling_std_dev([2, 4, 4, 4, 5, 5, 7, 9])
        assert abs(result - 2.0) < 0.001

    def test_rolling_std_dev_insufficient_data(self):
        """Less than 2 values returns 0."""
        assert rolling_std_dev([5]) == 0.0
        assert rolling_std_dev([]) == 0.0


class TestLossPercentageCalculations:
    """Tests for loss and percentage calculations."""

    def test_daily_loss_pct_with_loss(self):
        """3% loss calculated correctly."""
        result = daily_loss_pct(100000, 97000)
        assert abs(result - 0.03) < 0.0001

    def test_daily_loss_pct_no_loss(self):
        """No loss when equity increases."""
        assert daily_loss_pct(100000, 102000) == 0.0

    def test_daily_loss_pct_breakeven(self):
        """No loss at breakeven."""
        assert daily_loss_pct(100000, 100000) == 0.0

    def test_week_to_date_loss_pct(self):
        """5% WTD loss calculated correctly."""
        result = week_to_date_loss_pct(100000, 95000)
        assert abs(result - 0.05) < 0.0001

    def test_engine_drop_pct(self):
        """4% intraday drop calculated correctly."""
        result = engine_drop_pct(100.0, 96.0)
        assert abs(result - 0.04) < 0.0001

    def test_intraday_drop_pct_price_up(self):
        """No drop when price is up."""
        assert engine_drop_pct(100.0, 105.0) == 0.0

    def test_profit_pct_positive(self):
        """15% profit calculated correctly."""
        result = profit_pct(100.0, 115.0)
        assert abs(result - 0.15) < 0.0001

    def test_profit_pct_negative(self):
        """10% loss calculated correctly."""
        result = profit_pct(100.0, 90.0)
        assert abs(result - (-0.10)) < 0.0001

    def test_period_return(self):
        """5% return calculated correctly."""
        result = period_return(100.0, 105.0)
        assert abs(result - 0.05) < 0.0001


class TestVolatilityCalculations:
    """Tests for volatility calculations."""

    def test_realized_volatility_annualized(self):
        """Annualized volatility calculation."""
        returns = [0.01, -0.01, 0.01, -0.01, 0.01]
        result = realized_volatility(returns, annualize=True)
        # Should be std_dev * sqrt(252)
        expected_std = rolling_std_dev(returns)
        expected = expected_std * math.sqrt(252)
        assert abs(result - expected) < 0.0001

    def test_realized_volatility_not_annualized(self):
        """Non-annualized volatility calculation."""
        returns = [0.01, -0.01, 0.01, -0.01, 0.01]
        result = realized_volatility(returns, annualize=False)
        expected = rolling_std_dev(returns)
        assert abs(result - expected) < 0.0001

    def test_realized_volatility_insufficient_data(self):
        """Returns 0 with insufficient data."""
        assert realized_volatility([0.01]) == 0.0

    def test_volatility_percentile(self):
        """Percentile rank calculated correctly."""
        vols = [0.10, 0.12, 0.15, 0.18, 0.20]
        # 0.16 is greater than 3 values (0.10, 0.12, 0.15)
        result = volatility_percentile(0.16, vols)
        assert abs(result - 0.6) < 0.0001

    def test_volatility_percentile_empty_history(self):
        """Returns 0.5 (median) with no history."""
        assert volatility_percentile(0.15, []) == 0.5


class TestRelativePerformance:
    """Tests for breadth and credit spread calculations."""

    def test_breadth_spread_positive(self):
        """RSP outperforming SPY."""
        result = breadth_spread(0.05, 0.03)
        assert abs(result - 0.02) < 0.0001

    def test_breadth_spread_negative(self):
        """SPY outperforming RSP."""
        result = breadth_spread(0.03, 0.05)
        assert abs(result - (-0.02)) < 0.0001

    def test_credit_spread_positive(self):
        """HYG outperforming IEF (risk-on)."""
        result = credit_spread(0.03, 0.01)
        assert abs(result - 0.02) < 0.0001

    def test_credit_spread_negative(self):
        """IEF outperforming HYG (risk-off)."""
        result = credit_spread(0.01, 0.03)
        assert abs(result - (-0.02)) < 0.0001


class TestMovingAverageCalculations:
    """Tests for moving average and price position calculations."""

    def test_bollinger_bandwidth(self):
        """Bandwidth calculated correctly."""
        result = bollinger_bandwidth(105, 95, 100)
        assert abs(result - 0.10) < 0.0001

    def test_bollinger_bandwidth_zero_middle(self):
        """Handles zero middle band."""
        assert bollinger_bandwidth(10, 0, 0) == 0.0

    def test_price_to_sma_ratio(self):
        """Ratio calculated correctly."""
        assert abs(price_to_sma_ratio(105, 100) - 1.05) < 0.0001

    def test_is_extended_true(self):
        """Extended when price > 5% above SMA200."""
        assert is_extended(110, 100, 1.05) is True

    def test_is_extended_false(self):
        """Not extended when price within threshold."""
        assert is_extended(104, 100, 1.05) is False

    def test_is_oversold_true(self):
        """Oversold when price < 5% below SMA200."""
        assert is_oversold(90, 100, 0.95) is True

    def test_is_oversold_false(self):
        """Not oversold when price above threshold."""
        assert is_oversold(96, 100, 0.95) is False

    def test_ma_alignment_bullish(self):
        """Bullish when SMA20 > SMA50 > SMA200."""
        assert ma_alignment_bullish(110, 105, 100) is True
        assert ma_alignment_bullish(100, 105, 110) is False

    def test_ma_alignment_bearish(self):
        """Bearish when SMA20 < SMA50 < SMA200."""
        assert ma_alignment_bearish(90, 95, 100) is True
        assert ma_alignment_bearish(100, 95, 90) is False


class TestATRCalculations:
    """Tests for ATR-based calculations."""

    def test_atr_multiplier_base(self):
        """Base multiplier for <15% profit."""
        assert atr_multiplier_for_profit(0.10) == 3.0

    def test_atr_multiplier_tight(self):
        """Tight multiplier for 15-25% profit."""
        assert atr_multiplier_for_profit(0.20) == 2.0

    def test_atr_multiplier_tighter(self):
        """Tighter multiplier for >25% profit."""
        assert atr_multiplier_for_profit(0.30) == 1.5

    def test_atr_multiplier_at_boundaries(self):
        """Test boundary conditions for multiplier selection."""
        # At 15% exactly - should use tight
        assert atr_multiplier_for_profit(0.15) == 2.0
        # At 25% exactly - should use tighter
        assert atr_multiplier_for_profit(0.25) == 1.5

    def test_chandelier_stop_base(self):
        """Chandelier stop with base multiplier."""
        # highest_high=100, atr=2.0, multiplier=3.0
        result = chandelier_stop(100, 2.0, 3.0)
        assert abs(result - 94.0) < 0.0001

    def test_chandelier_stop_tight(self):
        """Chandelier stop with tight multiplier."""
        # highest_high=100, atr=2.0, multiplier=2.0
        result = chandelier_stop(100, 2.0, 2.0)
        assert abs(result - 96.0) < 0.0001

    def test_chandelier_stop_tighter(self):
        """Chandelier stop with tighter multiplier."""
        # highest_high=100, atr=2.0, multiplier=1.5
        result = chandelier_stop(100, 2.0, 1.5)
        assert abs(result - 97.0) < 0.0001

    def test_vol_shock_threshold(self):
        """Threshold calculated correctly."""
        assert abs(vol_shock_threshold(2.0, 3.0) - 6.0) < 0.0001

    def test_is_vol_shock_true(self):
        """Vol shock detected when range exceeds threshold."""
        # Range 7, threshold 6
        assert is_vol_shock(107, 100, 2.0, 3.0) is True

    def test_is_vol_shock_false(self):
        """No vol shock when range at threshold."""
        # Range 6, threshold 6
        assert is_vol_shock(106, 100, 2.0, 3.0) is False


class TestVolumeCalculations:
    """Tests for volume calculations."""

    def test_volume_ratio(self):
        """Ratio calculated correctly."""
        assert abs(volume_ratio(1500000, 1000000) - 1.5) < 0.0001

    def test_volume_ratio_zero_average(self):
        """Handles zero average volume."""
        assert volume_ratio(1000000, 0) == 1.0

    def test_volume_confirmation_true(self):
        """Confirmation when volume > 1.2x average."""
        assert volume_confirmation(1200000, 1000000, 1.2) is True

    def test_volume_confirmation_false(self):
        """No confirmation when volume < 1.2x average."""
        assert volume_confirmation(1100000, 1000000, 1.2) is False


class TestPositionCapitalCalculations:
    """Tests for position and capital calculations."""

    def test_tradeable_equity(self):
        """Tradeable equity excludes lockbox."""
        assert tradeable_equity(100000, 10000) == 90000

    def test_tradeable_equity_full_lockbox(self):
        """Returns 0 if lockbox exceeds equity."""
        assert tradeable_equity(10000, 20000) == 0.0

    def test_calculate_lockbox_amount_single_milestone(self):
        """Single milestone lockbox."""
        result = calculate_lockbox_amount(150000, [100000], 0.10)
        assert abs(result - 10000) < 0.0001

    def test_calculate_lockbox_amount_multiple_milestones(self):
        """Multiple milestones lockbox."""
        result = calculate_lockbox_amount(250000, [100000, 200000], 0.10)
        assert abs(result - 30000) < 0.0001

    def test_max_position_size(self):
        """Max position size calculated correctly."""
        assert abs(max_position_size(100000, 0.50) - 50000) < 0.0001

    def test_shares_from_value(self):
        """Share count calculated correctly."""
        assert shares_from_value(10000, 45.50) == 219

    def test_shares_from_value_zero_price(self):
        """Returns 0 for zero price."""
        assert shares_from_value(10000, 0) == 0

    def test_position_value(self):
        """Position value calculated correctly."""
        assert abs(position_value(100, 45.50) - 4550) < 0.0001


class TestRegimeScoreCalculations:
    """Tests for regime score component calculations."""

    def test_trend_factor_score_bullish(self):
        """Bullish trend scores high."""
        # Price above all SMAs, bullish alignment
        result = trend_factor_score(110, 108, 105, 100)
        assert result >= 80  # Should be high score

    def test_trend_factor_score_bearish(self):
        """Bearish trend scores low."""
        # Price below all SMAs, bearish alignment
        # Score: 50 (base) - 15 (bearish alignment) + 5 (oversold) = 40
        result = trend_factor_score(85, 90, 95, 100)
        assert result == 40  # Base 50, -15 bearish, +5 oversold

    def test_trend_factor_score_clamped(self):
        """Score clamped to 0-100."""
        result = trend_factor_score(110, 108, 105, 100)
        assert 0 <= result <= 100

    def test_volatility_factor_score_calm(self):
        """Calm volatility scores high."""
        result = volatility_factor_score(0.15, 0.10)  # 15th percentile, 10% vol
        assert result >= 70

    def test_volatility_factor_score_elevated(self):
        """Elevated volatility scores low."""
        result = volatility_factor_score(0.85, 0.30)  # 85th percentile, 30% vol
        assert result <= 30

    def test_breadth_factor_score_positive(self):
        """Strong breadth scores high."""
        result = breadth_factor_score(0.025)  # RSP +2.5% vs SPY
        assert result == 75

    def test_breadth_factor_score_negative(self):
        """Weak breadth scores low."""
        result = breadth_factor_score(-0.025)  # RSP -2.5% vs SPY
        assert result == 30

    def test_credit_factor_score_risk_on(self):
        """Risk-on credit scores high."""
        result = credit_factor_score(0.025)  # HYG +2.5% vs IEF
        assert result == 75

    def test_credit_factor_score_risk_off(self):
        """Risk-off credit scores low."""
        result = credit_factor_score(-0.025)  # IEF +2.5% vs HYG
        assert result == 25

    def test_aggregate_regime_score(self):
        """Aggregate score weighted correctly."""
        # All at 50 should give 50
        result = aggregate_regime_score(50, 50, 50, 50)
        assert abs(result - 50) < 0.0001

    def test_aggregate_regime_score_weighted(self):
        """V2.26 weights applied correctly (includes VIX + Chop factors)."""
        # V2.26 Formula: Trend=70 (25%), VIX=50 (20%), Vol=60 (15%), Breadth=55 (20%), Credit=50 (15%), Chop=50 (5%)
        # = 70*0.25 + 50*0.20 + 60*0.15 + 55*0.20 + 50*0.15 + 50*0.05
        # = 17.5 + 10.0 + 9.0 + 11.0 + 7.5 + 2.5 = 57.5
        result = aggregate_regime_score(70, 60, 55, 50)  # vix_score & chop_score default to 50
        assert abs(result - 57.5) < 0.0001

    def test_smooth_regime_score(self):
        """Exponential smoothing applied correctly."""
        # alpha=0.30: (0.30 * 70) + (0.70 * 60) = 21 + 42 = 63
        result = smooth_regime_score(70, 60, 0.30)
        assert abs(result - 63) < 0.0001


class TestHedgeAllocation:
    """Tests for hedge allocation calculations."""

    def test_hedge_allocation_risk_on(self):
        """No hedges when regime >= 40."""
        tmf, psq = calculate_hedge_allocation(45, 100000)
        assert tmf == 0.0
        assert psq == 0.0

    def test_hedge_allocation_level_1(self):
        """Light TMF when regime 30-39."""
        tmf, psq = calculate_hedge_allocation(35, 100000)
        assert abs(tmf - 10000) < 0.0001  # 10%
        assert psq == 0.0

    def test_hedge_allocation_level_2(self):
        """Medium hedges when regime 20-29."""
        tmf, psq = calculate_hedge_allocation(25, 100000)
        assert abs(tmf - 15000) < 0.0001  # 15%
        assert abs(psq - 5000) < 0.0001  # 5%

    def test_hedge_allocation_level_3(self):
        """Full hedges when regime < 20."""
        tmf, psq = calculate_hedge_allocation(15, 100000)
        assert abs(tmf - 20000) < 0.0001  # 20%
        assert abs(psq - 10000) < 0.0001  # 10%


class TestYieldSleeve:
    """Tests for yield sleeve calculations."""

    def test_calculate_unallocated_cash(self):
        """Unallocated cash calculated correctly."""
        result = calculate_unallocated_cash(100000, 60000, 30000)
        assert result == 10000

    def test_should_adjust_shv_above_minimum(self):
        """Adjustment warranted above minimum."""
        assert should_adjust_shv(2500) is True

    def test_should_adjust_shv_below_minimum(self):
        """No adjustment below minimum."""
        assert should_adjust_shv(1500) is False

    def test_should_adjust_shv_at_minimum(self):
        """Adjustment at exactly minimum."""
        assert should_adjust_shv(2000) is True

    def test_should_adjust_shv_negative(self):
        """Handles negative unallocated (need to sell)."""
        assert should_adjust_shv(-2500) is True
