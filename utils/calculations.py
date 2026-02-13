"""
Alpha NextGen Calculation Utilities

Pure calculation functions used across multiple engines.
All functions are stateless and have no side effects.
"""

import math
from typing import List, Optional, Tuple

# =============================================================================
# HELPER UTILITIES
# =============================================================================


def clamp(value: float, min_val: float, max_val: float) -> float:
    """Clamp a value within a specified range.

    Args:
        value: The value to clamp.
        min_val: Minimum allowed value.
        max_val: Maximum allowed value.

    Returns:
        Value constrained to [min_val, max_val].

    Example:
        >>> clamp(105, 0, 100)
        100
        >>> clamp(-5, 0, 100)
        0
    """
    return max(min_val, min(value, max_val))


def safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Safely divide two numbers, returning default if denominator is zero.

    Args:
        numerator: The dividend.
        denominator: The divisor.
        default: Value to return if denominator is zero.

    Returns:
        Result of division or default if division by zero.

    Example:
        >>> safe_divide(10, 2)
        5.0
        >>> safe_divide(10, 0)
        0.0
    """
    if denominator == 0:
        return default
    return numerator / denominator


def rolling_mean(values: List[float]) -> float:
    """Calculate mean of a list of values.

    Args:
        values: List of numeric values.

    Returns:
        Arithmetic mean, or 0.0 if empty list.

    Example:
        >>> rolling_mean([1, 2, 3, 4, 5])
        3.0
    """
    if not values:
        return 0.0
    return sum(values) / len(values)


def rolling_std_dev(values: List[float]) -> float:
    """Calculate population standard deviation of a list of values.

    Args:
        values: List of numeric values.

    Returns:
        Standard deviation, or 0.0 if fewer than 2 values.

    Example:
        >>> rolling_std_dev([2, 4, 4, 4, 5, 5, 7, 9])
        2.0
    """
    if len(values) < 2:
        return 0.0
    mean = rolling_mean(values)
    variance = sum((x - mean) ** 2 for x in values) / len(values)
    return math.sqrt(variance)


# =============================================================================
# LOSS / PERCENTAGE CALCULATIONS
# =============================================================================


def daily_loss_pct(baseline_equity: float, current_equity: float) -> float:
    """Calculate daily loss percentage from baseline.

    Args:
        baseline_equity: Equity at start of day (SOD baseline).
        current_equity: Current portfolio equity.

    Returns:
        Loss as positive decimal (0.03 = 3% loss).
        Returns 0 if no loss (current >= baseline).

    Example:
        >>> daily_loss_pct(100000, 97000)
        0.03
        >>> daily_loss_pct(100000, 102000)
        0.0
    """
    if current_equity >= baseline_equity:
        return 0.0
    return safe_divide(baseline_equity - current_equity, baseline_equity)


def week_to_date_loss_pct(week_start_equity: float, current_equity: float) -> float:
    """Calculate week-to-date loss percentage.

    Args:
        week_start_equity: Equity at Monday open.
        current_equity: Current portfolio equity.

    Returns:
        Loss as positive decimal (0.05 = 5% WTD loss).
        Returns 0 if no loss.

    Example:
        >>> week_to_date_loss_pct(100000, 95000)
        0.05
    """
    if current_equity >= week_start_equity:
        return 0.0
    return safe_divide(week_start_equity - current_equity, week_start_equity)


def intraday_drop_pct(open_price: float, current_price: float) -> float:
    """Calculate intraday drop percentage from open.

    Used for gap filter and panic mode detection.

    Args:
        open_price: Price at market open.
        current_price: Current price.

    Returns:
        Drop as positive decimal (0.04 = 4% drop).
        Returns 0 if price is above open.

    Example:
        >>> intraday_drop_pct(100.0, 96.0)
        0.04
    """
    if current_price >= open_price:
        return 0.0
    return safe_divide(open_price - current_price, open_price)


def profit_pct(entry_price: float, current_price: float) -> float:
    """Calculate profit percentage from entry.

    Args:
        entry_price: Original entry price.
        current_price: Current price.

    Returns:
        Profit as decimal (0.15 = 15% profit, -0.10 = 10% loss).

    Example:
        >>> profit_pct(100.0, 115.0)
        0.15
        >>> profit_pct(100.0, 90.0)
        -0.1
    """
    return safe_divide(current_price - entry_price, entry_price)


def period_return(start_price: float, end_price: float) -> float:
    """Calculate return over a period.

    Args:
        start_price: Price at start of period.
        end_price: Price at end of period.

    Returns:
        Return as decimal (0.05 = 5% gain).

    Example:
        >>> period_return(100.0, 105.0)
        0.05
    """
    return safe_divide(end_price - start_price, start_price)


# =============================================================================
# VOLATILITY CALCULATIONS
# =============================================================================


def realized_volatility(daily_returns: List[float], annualize: bool = True) -> float:
    """Calculate realized volatility from daily returns.

    Args:
        daily_returns: List of daily returns as decimals.
        annualize: If True, annualize using sqrt(252).

    Returns:
        Volatility as decimal (0.20 = 20% annualized vol).

    Example:
        >>> returns = [0.01, -0.005, 0.008, -0.003, 0.012]
        >>> vol = realized_volatility(returns)
    """
    if len(daily_returns) < 2:
        return 0.0
    std_dev = rolling_std_dev(daily_returns)
    if annualize:
        return std_dev * math.sqrt(252)
    return std_dev


def volatility_percentile(current_vol: float, historical_vols: List[float]) -> float:
    """Calculate percentile rank of current volatility.

    Args:
        current_vol: Current realized volatility.
        historical_vols: List of historical volatility readings.

    Returns:
        Percentile as decimal (0.80 = 80th percentile).

    Example:
        >>> vols = [0.10, 0.12, 0.15, 0.18, 0.20]
        >>> volatility_percentile(0.16, vols)
        0.6
    """
    if not historical_vols:
        return 0.5  # Default to median
    count_below = sum(1 for v in historical_vols if v < current_vol)
    return count_below / len(historical_vols)


# =============================================================================
# RELATIVE PERFORMANCE (BREADTH / CREDIT)
# =============================================================================


def breadth_spread(rsp_return: float, spy_return: float) -> float:
    """Calculate breadth spread (equal-weight vs cap-weight).

    Positive spread indicates broad market participation.
    Negative spread indicates narrow leadership.

    Args:
        rsp_return: RSP (equal-weight S&P) return over period.
        spy_return: SPY (cap-weight S&P) return over period.

    Returns:
        Spread as decimal (0.02 = RSP outperforming by 2%).

    Example:
        >>> breadth_spread(0.05, 0.03)
        0.02
    """
    return rsp_return - spy_return


def credit_spread(hyg_return: float, ief_return: float) -> float:
    """Calculate credit spread (high yield vs treasuries).

    Positive spread indicates risk-on sentiment.
    Negative spread indicates flight to safety.

    Args:
        hyg_return: HYG (high yield bonds) return over period.
        ief_return: IEF (7-10yr treasuries) return over period.

    Returns:
        Spread as decimal (0.02 = HYG outperforming by 2%).

    Example:
        >>> credit_spread(0.03, 0.01)
        0.02
    """
    return hyg_return - ief_return


# =============================================================================
# MOVING AVERAGE & PRICE POSITION
# =============================================================================


def bollinger_bandwidth(upper: float, lower: float, middle: float) -> float:
    """Calculate Bollinger Band bandwidth.

    Bandwidth indicates volatility compression/expansion.
    Values < 0.10 indicate compression (potential breakout).

    Args:
        upper: Upper Bollinger Band value.
        lower: Lower Bollinger Band value.
        middle: Middle Bollinger Band (SMA) value.

    Returns:
        Bandwidth as decimal (0.10 = 10% width).

    Example:
        >>> bollinger_bandwidth(105, 95, 100)
        0.1
    """
    return safe_divide(upper - lower, middle)


# Alias for backward compatibility and convenience
bandwidth = bollinger_bandwidth


def price_to_sma_ratio(price: float, sma: float) -> float:
    """Calculate ratio of price to moving average.

    Used to detect extended or oversold conditions.

    Args:
        price: Current price.
        sma: Simple moving average value.

    Returns:
        Ratio (1.05 = price 5% above SMA).

    Example:
        >>> price_to_sma_ratio(105, 100)
        1.05
    """
    return safe_divide(price, sma, default=1.0)


def is_extended(price: float, sma200: float, threshold: float = 1.05) -> bool:
    """Check if price is extended above SMA200.

    Args:
        price: Current price.
        sma200: 200-day simple moving average.
        threshold: Extension threshold (default 1.05 = 5% above).

    Returns:
        True if price is extended above threshold.

    Example:
        >>> is_extended(110, 100, 1.05)
        True
    """
    ratio = price_to_sma_ratio(price, sma200)
    return ratio > threshold


def is_oversold(price: float, sma200: float, threshold: float = 0.95) -> bool:
    """Check if price is oversold below SMA200.

    Args:
        price: Current price.
        sma200: 200-day simple moving average.
        threshold: Oversold threshold (default 0.95 = 5% below).

    Returns:
        True if price is below threshold.

    Example:
        >>> is_oversold(90, 100, 0.95)
        True
    """
    ratio = price_to_sma_ratio(price, sma200)
    return ratio < threshold


def ma_alignment_bullish(sma20: float, sma50: float, sma200: float) -> bool:
    """Check for bullish moving average alignment.

    Args:
        sma20: 20-day SMA.
        sma50: 50-day SMA.
        sma200: 200-day SMA.

    Returns:
        True if SMA20 > SMA50 > SMA200.

    Example:
        >>> ma_alignment_bullish(110, 105, 100)
        True
    """
    return sma20 > sma50 > sma200


def ma_alignment_bearish(sma20: float, sma50: float, sma200: float) -> bool:
    """Check for bearish moving average alignment.

    Args:
        sma20: 20-day SMA.
        sma50: 50-day SMA.
        sma200: 200-day SMA.

    Returns:
        True if SMA20 < SMA50 < SMA200.

    Example:
        >>> ma_alignment_bearish(90, 95, 100)
        True
    """
    return sma20 < sma50 < sma200


# =============================================================================
# ATR-BASED CALCULATIONS
# =============================================================================


def atr_multiplier_for_profit(
    profit_pct_value: float,
    profit_tight_threshold: float = 0.15,
    profit_tighter_threshold: float = 0.25,
    base_mult: float = 3.0,
    tight_mult: float = 2.0,
    tighter_mult: float = 1.5,
) -> float:
    """Get ATR multiplier based on current profit level.

    Used to tighten trailing stops as profit increases.

    Args:
        profit_pct_value: Current profit as decimal (0.15 = 15%).
        profit_tight_threshold: Profit level to tighten (default 0.15).
        profit_tighter_threshold: Profit level to tighten more (default 0.25).
        base_mult: Multiplier for <15% profit (default 3.0).
        tight_mult: Multiplier for 15-25% profit (default 2.0).
        tighter_mult: Multiplier for >25% profit (default 1.5).

    Returns:
        ATR multiplier to use for stop calculation.

    Example:
        >>> atr_multiplier_for_profit(0.10)  # <15% profit
        3.0
        >>> atr_multiplier_for_profit(0.20)  # 15-25% profit
        2.0
        >>> atr_multiplier_for_profit(0.30)  # >25% profit
        1.5
    """
    if profit_pct_value >= profit_tighter_threshold:
        return tighter_mult
    elif profit_pct_value >= profit_tight_threshold:
        return tight_mult
    else:
        return base_mult


def chandelier_stop(highest_high: float, atr: float, multiplier: float) -> float:
    """Calculate Chandelier trailing stop level.

    Simple version: highest_high - (multiplier * ATR).

    Args:
        highest_high: Highest high since entry.
        atr: Current ATR(14) value.
        multiplier: ATR multiplier (use atr_multiplier_for_profit to get this).

    Returns:
        Stop price level.

    Example:
        >>> chandelier_stop(100, 2.0, 3.0)  # Base multiplier
        94.0
        >>> chandelier_stop(100, 2.0, 2.0)  # Tight multiplier
        96.0
        >>> chandelier_stop(100, 2.0, 1.5)  # Tighter multiplier
        97.0
    """
    return highest_high - (multiplier * atr)


def vol_shock_threshold(atr: float, multiplier: float = 3.0) -> float:
    """Calculate vol shock detection threshold.

    A bar range exceeding this threshold triggers vol shock pause.

    Args:
        atr: Current ATR value.
        multiplier: Shock multiplier (default 3.0).

    Returns:
        Threshold value for bar range (high - low).

    Example:
        >>> vol_shock_threshold(2.0)
        6.0
    """
    return atr * multiplier


def is_vol_shock(bar_high: float, bar_low: float, atr: float, multiplier: float = 3.0) -> bool:
    """Check if a bar represents a volatility shock.

    Args:
        bar_high: Bar high price.
        bar_low: Bar low price.
        atr: Current ATR value.
        multiplier: Shock multiplier (default 3.0).

    Returns:
        True if bar range exceeds threshold.

    Example:
        >>> is_vol_shock(106, 100, 2.0)  # Range 6, threshold 6
        False
        >>> is_vol_shock(107, 100, 2.0)  # Range 7, threshold 6
        True
    """
    bar_range = bar_high - bar_low
    threshold = vol_shock_threshold(atr, multiplier)
    return bar_range > threshold


# =============================================================================
# VOLUME CALCULATIONS
# =============================================================================


def volume_ratio(current_volume: float, avg_volume: float) -> float:
    """Calculate volume ratio vs average.

    Args:
        current_volume: Current bar/period volume.
        avg_volume: Average volume (e.g., 20-day SMA).

    Returns:
        Ratio (1.5 = 50% above average).

    Example:
        >>> volume_ratio(1500000, 1000000)
        1.5
    """
    return safe_divide(current_volume, avg_volume, default=1.0)


def volume_confirmation(current_volume: float, avg_volume: float, min_ratio: float = 1.2) -> bool:
    """Check if volume confirms a move (above average threshold).

    Used for mean reversion entry validation.

    Args:
        current_volume: Current volume.
        avg_volume: Average volume.
        min_ratio: Minimum ratio required (default 1.2 = 20% above).

    Returns:
        True if volume exceeds threshold.

    Example:
        >>> volume_confirmation(1200000, 1000000)
        True
    """
    return volume_ratio(current_volume, avg_volume) >= min_ratio


# =============================================================================
# POSITION & CAPITAL CALCULATIONS
# =============================================================================


def tradeable_equity(total_equity: float, locked_amount: float) -> float:
    """Calculate tradeable equity excluding lockbox.

    Args:
        total_equity: Total portfolio value.
        locked_amount: Amount locked in lockbox.

    Returns:
        Equity available for trading.

    Example:
        >>> tradeable_equity(100000, 10000)
        90000
    """
    return max(0.0, total_equity - locked_amount)


def calculate_lockbox_amount(
    current_equity: float,
    milestones_reached: List[float],
    lock_pct: float = 0.10,
) -> float:
    """Calculate total locked amount from reached milestones.

    Args:
        current_equity: Current total equity.
        milestones_reached: List of milestone values that have been reached.
        lock_pct: Percentage to lock at each milestone (default 0.10).

    Returns:
        Total locked amount.

    Example:
        >>> calculate_lockbox_amount(150000, [100000])
        10000.0
        >>> calculate_lockbox_amount(250000, [100000, 200000])
        30000.0
    """
    locked = 0.0
    for milestone in milestones_reached:
        # Lock percentage of the milestone value
        locked += milestone * lock_pct
    return locked


def max_position_size(tradeable_eq: float, max_position_pct: float) -> float:
    """Calculate maximum single position size.

    Args:
        tradeable_eq: Tradeable equity (excluding lockbox).
        max_position_pct: Maximum position as decimal (0.50 = 50%).

    Returns:
        Maximum position value in dollars.

    Example:
        >>> max_position_size(100000, 0.50)
        50000.0
    """
    return tradeable_eq * max_position_pct


def shares_from_value(target_value: float, price: float) -> int:
    """Calculate whole shares from target dollar value.

    Args:
        target_value: Target position value in dollars.
        price: Current share price.

    Returns:
        Number of whole shares (rounded down).

    Example:
        >>> shares_from_value(10000, 45.50)
        219
    """
    if price <= 0:
        return 0
    return int(target_value / price)


def position_value(shares: int, price: float) -> float:
    """Calculate position value from shares.

    Args:
        shares: Number of shares held.
        price: Current share price.

    Returns:
        Position value in dollars.

    Example:
        >>> position_value(100, 45.50)
        4550.0
    """
    return shares * price


# =============================================================================
# REGIME SCORE COMPONENTS
# =============================================================================


def trend_factor_score(
    price: float,
    sma20: float,
    sma50: float,
    sma200: float,
    extended_threshold: float = 1.05,
    oversold_threshold: float = 0.95,
) -> float:
    """Calculate trend factor score (0-100).

    Args:
        price: Current price.
        sma20: 20-day SMA.
        sma50: 50-day SMA.
        sma200: 200-day SMA.
        extended_threshold: Ratio above SMA200 considered extended.
        oversold_threshold: Ratio below SMA200 considered oversold.

    Returns:
        Trend score clamped to 0-100.

    Example:
        >>> trend_factor_score(110, 108, 105, 100)  # Bullish alignment
        95
    """
    score = 50.0  # Base score

    # Price above moving averages
    if price > sma20:
        score += 10
    if price > sma50:
        score += 10
    if price > sma200:
        score += 15

    # Moving average alignment
    if ma_alignment_bullish(sma20, sma50, sma200):
        score += 10
    elif ma_alignment_bearish(sma20, sma50, sma200):
        score -= 15

    # Extended/Oversold adjustments
    if is_extended(price, sma200, extended_threshold):
        score -= 10  # Extended markets are risky
    if is_oversold(price, sma200, oversold_threshold):
        score += 5  # Potential bounce opportunity

    return clamp(score, 0, 100)


def volatility_factor_score(
    vol_percentile: float,
    current_vol: float,
    low_vol_threshold: float = 0.12,
    high_vol_threshold: float = 0.25,
) -> float:
    """Calculate volatility factor score (0-100).

    Lower volatility = higher score (more favorable).

    Args:
        vol_percentile: Current vol percentile (0-1).
        current_vol: Current realized volatility.
        low_vol_threshold: Vol below this adds points.
        high_vol_threshold: Vol above this subtracts points.

    Returns:
        Volatility score clamped to 0-100.

    Example:
        >>> volatility_factor_score(0.30, 0.15)  # Low percentile
        65
    """
    score = 50.0  # Base score

    # Percentile-based scoring
    if vol_percentile < 0.20:
        score += 25  # Very calm
    elif vol_percentile < 0.40:
        score += 15  # Calm
    elif vol_percentile < 0.60:
        score += 0  # Normal
    elif vol_percentile < 0.80:
        score -= 15  # Elevated
    else:
        score -= 25  # High fear

    # Absolute level adjustments
    if current_vol < low_vol_threshold:
        score += 10
    if current_vol > high_vol_threshold:
        score -= 10

    return clamp(score, 0, 100)


def breadth_factor_score(spread: float) -> float:
    """Calculate breadth factor score (0-100).

    Positive spread (RSP > SPY) = broad participation = higher score.

    Args:
        spread: Breadth spread (RSP return - SPY return).

    Returns:
        Breadth score clamped to 0-100.

    Example:
        >>> breadth_factor_score(0.02)  # RSP outperforming by 2%
        75
    """
    score = 50.0  # Base score

    if spread > 0.02:
        score += 25
    elif spread > 0.01:
        score += 15
    elif spread > 0.00:
        score += 5
    elif spread > -0.01:
        score += 0
    elif spread > -0.02:
        score -= 10
    else:
        score -= 20

    return clamp(score, 0, 100)


def credit_factor_score(spread: float) -> float:
    """Calculate credit factor score (0-100).

    Positive spread (HYG > IEF) = risk-on sentiment = higher score.

    Args:
        spread: Credit spread (HYG return - IEF return).

    Returns:
        Credit score clamped to 0-100.

    Example:
        >>> credit_factor_score(0.02)  # HYG outperforming by 2%
        75
    """
    score = 50.0  # Base score

    if spread > 0.02:
        score += 25
    elif spread > 0.01:
        score += 15
    elif spread > 0.00:
        score += 5
    elif spread > -0.01:
        score -= 5
    elif spread > -0.02:
        score -= 15
    else:
        score -= 25

    return clamp(score, 0, 100)


def vix_factor_score(
    vix_level: float,
    low_threshold: float = 15.0,
    normal_threshold: float = 22.0,
    high_threshold: float = 30.0,
    extreme_threshold: float = 40.0,
) -> float:
    """Calculate VIX factor score for regime calculation (V2.3).

    Options are priced off implied volatility (VIX). Low VIX means cheap options
    with better expected returns. High VIX means expensive options.

    Args:
        vix_level: Current VIX value.
        low_threshold: VIX below this is complacent (default 15).
        normal_threshold: VIX below this is normal (default 22).
        high_threshold: VIX above this is elevated (default 30).
        extreme_threshold: VIX above this is crisis (default 40).

    Returns:
        VIX score (0-100). Higher score = better for buying options.

    Example:
        >>> vix_factor_score(15)  # Low VIX
        100
        >>> vix_factor_score(25)  # Elevated
        40
        >>> vix_factor_score(45)  # Crisis
        0
    """
    if vix_level < low_threshold:
        return 100.0  # Complacent, cheap options
    elif vix_level < 18:
        return 85.0  # Low fear
    elif vix_level < normal_threshold:
        return 70.0  # Normal
    elif vix_level < 26:
        return 50.0  # Elevated
    elif vix_level < high_threshold:
        return 30.0  # High fear
    elif vix_level < extreme_threshold:
        return 15.0  # Very high
    else:
        return 0.0  # Crisis mode


def drawdown_factor_score(
    current_price: float,
    high_52w: float,
    threshold_bull: float = 0.05,
    threshold_correction: float = 0.10,
    threshold_pullback: float = 0.15,
    threshold_bear: float = 0.20,
    score_bull: float = 90.0,
    score_correction: float = 70.0,
    score_pullback: float = 50.0,
    score_bear: float = 30.0,
    score_deep_bear: float = 10.0,
) -> float:
    """V3.3: Calculate drawdown factor score for regime calculation.

    This factor directly measures how far the market has fallen from its peak,
    breaking the score compression problem in grinding bear markets.

    In a -20% to -30% bear market, this factor forces the regime score lower
    regardless of whether other factors (VIX, breadth, credit) have normalized.

    Args:
        current_price: Current SPY price.
        high_52w: 52-week high SPY price.
        threshold_bull: Drawdown below this = bull pullback (default 5%).
        threshold_correction: Drawdown below this = correction (default 10%).
        threshold_pullback: Drawdown below this = pullback (default 15%).
        threshold_bear: Drawdown below this = bear (default 20%).
        score_*: Scores for each regime band.

    Returns:
        Drawdown score (0-100). Higher = healthier market.

    Example:
        >>> drawdown_factor_score(100, 100)  # At high
        90
        >>> drawdown_factor_score(85, 100)  # -15% drawdown
        50
        >>> drawdown_factor_score(75, 100)  # -25% drawdown
        10
    """
    if high_52w <= 0:
        return 50.0  # Neutral if no valid high

    drawdown_pct = (high_52w - current_price) / high_52w

    if drawdown_pct <= threshold_bull:
        return score_bull  # 0-5% = Bull pullback
    elif drawdown_pct <= threshold_correction:
        return score_correction  # 5-10% = Correction
    elif drawdown_pct <= threshold_pullback:
        return score_pullback  # 10-15% = Pullback
    elif drawdown_pct <= threshold_bear:
        return score_bear  # 15-20% = Bear territory
    else:
        return score_deep_bear  # 20%+ = Deep bear


def vix_direction_score(
    vix_current: float,
    vix_prior: float,
    spiking_threshold: float = 15.0,
    rising_fast_threshold: float = 8.0,
    rising_threshold: float = 3.0,
    falling_threshold: float = -3.0,
    falling_fast_threshold: float = -8.0,
    score_spiking: float = 0.0,
    score_rising_fast: float = 25.0,
    score_rising: float = 40.0,
    score_stable: float = 50.0,
    score_falling: float = 70.0,
    score_falling_fast: float = 100.0,
) -> float:
    """V3.0: Calculate VIX direction score for regime calculation.

    VIX direction (momentum) is a LEADING indicator that detects stress
    2-3 days earlier than VIX level alone. This enables the daily regime
    to catch crashes same-day like the Micro Regime does.

    Args:
        vix_current: Current VIX value.
        vix_prior: Prior day's VIX close.
        spiking_threshold: VIX change % threshold for spiking (default +15%).
        rising_fast_threshold: VIX change % threshold for rising fast (default +8%).
        rising_threshold: VIX change % threshold for rising (default +3%).
        falling_threshold: VIX change % threshold for falling (default -3%).
        falling_fast_threshold: VIX change % threshold for falling fast (default -8%).
        score_spiking: Score when VIX is spiking (default 0).
        score_rising_fast: Score when VIX is rising fast (default 25).
        score_rising: Score when VIX is rising (default 40).
        score_stable: Score when VIX is stable (default 50).
        score_falling: Score when VIX is falling (default 70).
        score_falling_fast: Score when VIX is falling fast (default 100).

    Returns:
        VIX direction score (0-100). Lower = fear building, Higher = fear receding.

    Example:
        >>> vix_direction_score(25, 20)  # VIX up 25%
        0
        >>> vix_direction_score(18, 20)  # VIX down 10%
        100
        >>> vix_direction_score(20, 20)  # VIX unchanged
        50
    """
    if vix_prior <= 0:
        return score_stable  # No prior data, assume stable

    vix_change_pct = ((vix_current - vix_prior) / vix_prior) * 100

    # Classify direction and return score
    if vix_change_pct >= spiking_threshold:
        return score_spiking  # Crisis building
    elif vix_change_pct >= rising_fast_threshold:
        return score_rising_fast  # Significant stress
    elif vix_change_pct >= rising_threshold:
        return score_rising  # Mild concern
    elif vix_change_pct <= falling_fast_threshold:
        return score_falling_fast  # Strong recovery
    elif vix_change_pct <= falling_threshold:
        return score_falling  # Recovery
    else:
        return score_stable  # Stable


def chop_factor_score(
    adx_value: float,
    strong: float = 25.0,
    moderate: float = 20.0,
    weak: float = 15.0,
) -> float:
    """Score 0-100 based on ADX trend strength (V2.26: Chop Detector).

    Measures trend quality/consistency via ADX(14) of SPY.
    Low ADX = choppy/directionless market = low score = reduces regime.

    Args:
        adx_value: Current ADX(14) value.
        strong: ADX threshold for strong trend (score 100).
        moderate: ADX threshold for moderate trend (score 60).
        weak: ADX threshold for weak trend (score 30).

    Returns:
        Chop factor score (0-100). Lower = choppier.

    Example:
        >>> chop_factor_score(28)
        100
        >>> chop_factor_score(12)
        10
    """
    if adx_value >= strong:
        return 100.0  # Strong trend, safe for directional plays
    elif adx_value >= moderate:
        return 60.0  # Moderate, some directional plays OK
    elif adx_value >= weak:
        return 30.0  # Weak, reduce directional exposure
    else:
        return 10.0  # Dead/choppy, avoid directional plays


def aggregate_regime_score(
    trend_score: float,
    vol_score: float,
    breadth_score: float,
    credit_score: float,
    weight_trend: float = 0.25,
    weight_vol: float = 0.15,
    weight_breadth: float = 0.20,
    weight_credit: float = 0.15,
    vix_score: float = 50.0,
    weight_vix: float = 0.20,
    chop_score: float = 50.0,
    weight_chop: float = 0.05,
    vix_direction_score: float = 50.0,
    weight_vix_direction: float = 0.0,
) -> float:
    """Calculate weighted aggregate regime score (V3.0: includes VIX Direction).

    Args:
        trend_score: Trend factor score (0-100).
        vol_score: Realized volatility factor score (0-100).
        breadth_score: Breadth factor score (0-100).
        credit_score: Credit factor score (0-100).
        weight_trend: Weight for trend factor (V2.26: 0.25, was 0.30).
        weight_vol: Weight for realized volatility factor (V2.3: 0.15).
        weight_breadth: Weight for breadth factor (V2.3: 0.20).
        weight_credit: Weight for credit factor (0.15).
        vix_score: VIX factor score (0-100) - V2.3 NEW.
        weight_vix: Weight for VIX factor (V2.3: 0.20).
        chop_score: Chop/ADX factor score (0-100) - V2.26 NEW.
        weight_chop: Weight for chop factor (V2.26: 0.05).
        vix_direction_score: VIX direction score (0-100) - V3.0 NEW.
        weight_vix_direction: Weight for VIX direction (V3.0: 0.15 when enabled).

    Returns:
        Aggregate score (0-100).

    Example:
        >>> aggregate_regime_score(70, 60, 55, 50, vix_score=80, chop_score=100)
        63.5
        >>> aggregate_regime_score(70, 60, 55, 50, vix_score=80, chop_score=100,
        ...                        vix_direction_score=25, weight_vix_direction=0.15)
        59.75  # Penalized by rising VIX direction
    """
    raw = (
        trend_score * weight_trend
        + vol_score * weight_vol
        + breadth_score * weight_breadth
        + credit_score * weight_credit
        + vix_score * weight_vix
        + chop_score * weight_chop
        + vix_direction_score * weight_vix_direction
    )
    return clamp(raw, 0, 100)


def smooth_regime_score(raw_score: float, previous_smoothed: float, alpha: float = 0.30) -> float:
    """Apply exponential smoothing to regime score.

    Args:
        raw_score: Current raw regime score.
        previous_smoothed: Previous smoothed score.
        alpha: Smoothing factor (0-1). Higher = more responsive.

    Returns:
        Smoothed regime score.

    Example:
        >>> smooth_regime_score(70, 60, 0.30)
        63.0
    """
    return (alpha * raw_score) + ((1 - alpha) * previous_smoothed)


# =============================================================================
# HEDGE ALLOCATION
# =============================================================================


def calculate_hedge_allocation(
    regime_score: float,
    tradeable_eq: float,
    level_1: float = 40,
    level_2: float = 30,
    level_3: float = 20,
    tmf_light: float = 0.10,
    tmf_medium: float = 0.15,
    tmf_full: float = 0.20,
    psq_medium: float = 0.05,
    psq_full: float = 0.10,
) -> Tuple[float, float]:
    """Calculate hedge allocations based on regime score.

    Args:
        regime_score: Current smoothed regime score (0-100).
        tradeable_eq: Tradeable equity for sizing.
        level_1: Score threshold for light hedge.
        level_2: Score threshold for medium hedge.
        level_3: Score threshold for full hedge.
        tmf_light: TMF allocation at level 1.
        tmf_medium: TMF allocation at level 2.
        tmf_full: TMF allocation at level 3.
        psq_medium: PSQ allocation at level 2.
        psq_full: PSQ allocation at level 3.

    Returns:
        Tuple of (TMF allocation $, PSQ allocation $).

    Example:
        >>> calculate_hedge_allocation(35, 100000)
        (10000.0, 0.0)
        >>> calculate_hedge_allocation(25, 100000)
        (15000.0, 5000.0)
    """
    if regime_score >= level_1:
        # Risk-on: no hedges
        tmf_pct = 0.0
        psq_pct = 0.0
    elif regime_score >= level_2:
        # Level 1: Light TMF only
        tmf_pct = tmf_light
        psq_pct = 0.0
    elif regime_score >= level_3:
        # Level 2: Medium TMF + PSQ
        tmf_pct = tmf_medium
        psq_pct = psq_medium
    else:
        # Level 3: Full hedge
        tmf_pct = tmf_full
        psq_pct = psq_full

    return (tradeable_eq * tmf_pct, tradeable_eq * psq_pct)


# =============================================================================
# YIELD SLEEVE
# =============================================================================


def calculate_unallocated_cash(
    total_equity: float,
    non_shv_positions_value: float,
    current_shv_value: float,
) -> float:
    """Calculate unallocated cash available for SHV.

    Args:
        total_equity: Total portfolio equity.
        non_shv_positions_value: Sum of all non-SHV position values.
        current_shv_value: Current SHV holdings value.

    Returns:
        Unallocated cash that could be deployed to SHV.

    Example:
        >>> calculate_unallocated_cash(100000, 60000, 30000)
        10000
    """
    return total_equity - non_shv_positions_value - current_shv_value


def should_adjust_shv(unallocated: float, min_trade: float = 2000) -> bool:
    """Check if SHV adjustment is warranted.

    Args:
        unallocated: Unallocated cash amount.
        min_trade: Minimum trade size (default $2000).

    Returns:
        True if adjustment exceeds minimum threshold.

    Example:
        >>> should_adjust_shv(2500)
        True
        >>> should_adjust_shv(1500)
        False
    """
    return abs(unallocated) >= min_trade


# =============================================================================
# V4.0 REGIME MODEL SCORING FUNCTIONS
# =============================================================================


def momentum_factor_score_v4(
    roc_20: float,
    threshold_strong_bull: float = 0.05,
    threshold_bull: float = 0.02,
    threshold_neutral_high: float = 0.01,
    threshold_neutral_low: float = -0.01,
    threshold_bear: float = -0.02,
    threshold_strong_bear: float = -0.05,
    score_strong_bull: float = 90.0,
    score_bull: float = 75.0,
    score_neutral_high: float = 60.0,
    score_neutral: float = 50.0,
    score_neutral_low: float = 40.0,
    score_bear: float = 25.0,
    score_strong_bear: float = 10.0,
) -> float:
    """V4.0: Calculate momentum factor score from 20-day Rate of Change.

    Momentum is a LEADING indicator that catches reversals in days, not months.
    Unlike MA200 (lagging), ROC detects direction changes immediately.

    Args:
        roc_20: 20-day Rate of Change as decimal (e.g., 0.05 = +5%).
        threshold_strong_bull: ROC threshold for strong bull (default +5%).
        threshold_bull: ROC threshold for bull (default +2%).
        threshold_neutral_high: ROC threshold for neutral high (default +1%).
        threshold_neutral_low: ROC threshold for neutral low (default -1%).
        threshold_bear: ROC threshold for bear (default -2%).
        threshold_strong_bear: ROC threshold for strong bear (default -5%).
        score_*: Scores for each threshold level.

    Returns:
        Momentum factor score (0-100). Higher = bullish momentum.

    Example:
        >>> momentum_factor_score_v4(0.06)  # +6% ROC
        90.0
        >>> momentum_factor_score_v4(-0.03)  # -3% ROC
        25.0
        >>> momentum_factor_score_v4(0.005)  # +0.5% ROC
        50.0
    """
    if roc_20 >= threshold_strong_bull:
        return score_strong_bull  # Strong uptrend
    elif roc_20 >= threshold_bull:
        return score_bull  # Bullish
    elif roc_20 >= threshold_neutral_high:
        return score_neutral_high  # Slightly bullish
    elif roc_20 >= threshold_neutral_low:
        return score_neutral  # Neutral
    elif roc_20 >= threshold_bear:
        return score_neutral_low  # Slightly bearish
    elif roc_20 >= threshold_strong_bear:
        return score_bear  # Bearish
    else:
        return score_strong_bear  # Strong downtrend


def vix_direction_score_v4(
    vix_current: float,
    vix_5d_ago: float,
    spike_threshold: float = 0.20,
    rising_fast_threshold: float = 0.10,
    rising_threshold: float = 0.05,
    stable_high_threshold: float = 0.02,
    stable_low_threshold: float = -0.02,
    falling_threshold: float = -0.10,
    falling_fast_threshold: float = -0.20,
    score_spike: float = 10.0,
    score_rising_fast: float = 25.0,
    score_rising: float = 40.0,
    score_stable_high: float = 50.0,
    score_stable: float = 55.0,
    score_falling: float = 70.0,
    score_falling_fast: float = 85.0,
) -> float:
    """V4.0: Calculate VIX direction score from 5-day VIX change.

    Measures fear VELOCITY over 5 days (not daily like V3.0).
    5-day window smooths noise while still catching trend changes.
    Spike detection is critical for immediate crash identification.

    Args:
        vix_current: Current VIX value.
        vix_5d_ago: VIX value from 5 trading days ago.
        spike_threshold: VIX change % for spike detection (default +20%).
        rising_fast_threshold: VIX change % for rising fast (default +10%).
        rising_threshold: VIX change % for rising (default +5%).
        stable_high_threshold: VIX change % for stable/rising (default +2%).
        stable_low_threshold: VIX change % for stable (default -2%).
        falling_threshold: VIX change % for falling (default -10%).
        falling_fast_threshold: VIX change % for falling fast (default -20%).
        score_*: Scores for each threshold level.

    Returns:
        VIX direction score (0-100). Lower = fear building, Higher = fear receding.

    Example:
        >>> vix_direction_score_v4(30, 20)  # VIX up 50%
        10.0
        >>> vix_direction_score_v4(18, 25)  # VIX down 28%
        85.0
        >>> vix_direction_score_v4(20, 20)  # VIX unchanged
        55.0
    """
    if vix_5d_ago <= 0:
        return score_stable  # No prior data, assume stable

    vix_change_pct = (vix_current - vix_5d_ago) / vix_5d_ago

    # Classify direction and return score
    if vix_change_pct >= spike_threshold:
        return score_spike  # Crisis building - immediate danger
    elif vix_change_pct >= rising_fast_threshold:
        return score_rising_fast  # Significant stress
    elif vix_change_pct >= rising_threshold:
        return score_rising  # Fear increasing
    elif vix_change_pct >= stable_high_threshold:
        return score_stable_high  # Slightly rising
    elif vix_change_pct >= stable_low_threshold:
        return score_stable  # Stable
    elif vix_change_pct >= falling_threshold:
        return score_falling  # Fear decreasing
    elif vix_change_pct >= falling_fast_threshold:
        return score_falling_fast  # Fear decreasing quickly
    else:
        return score_falling_fast  # Fear collapsing - strong bullish


def breadth_factor_score_v4(
    rsp_spy_ratio: float,
    ratio_strong: float = 1.02,
    ratio_healthy: float = 1.00,
    ratio_narrow: float = 0.98,
    ratio_weak: float = 0.96,
    score_strong: float = 85.0,
    score_healthy: float = 70.0,
    score_narrow: float = 50.0,
    score_weak: float = 30.0,
) -> float:
    """V4.0: Calculate breadth factor score from RSP/SPY ratio.

    Measures market participation - narrow rallies (mega-cap only) are warning signs.
    RSP (equal-weight) outperforming SPY (cap-weight) = broad participation = healthy.
    RSP underperforming SPY = narrow rally = potential warning.

    Args:
        rsp_spy_ratio: RSP 20-day return / SPY 20-day return ratio.
        ratio_strong: Ratio threshold for strong breadth (default 1.02).
        ratio_healthy: Ratio threshold for healthy breadth (default 1.00).
        ratio_narrow: Ratio threshold for narrow breadth (default 0.98).
        ratio_weak: Ratio threshold for weak breadth (default 0.96).
        score_*: Scores for each threshold level.

    Returns:
        Breadth factor score (0-100). Higher = broader participation.

    Example:
        >>> breadth_factor_score_v4(1.05)  # RSP outperforming by 5%
        85.0
        >>> breadth_factor_score_v4(0.95)  # RSP underperforming by 5%
        30.0
        >>> breadth_factor_score_v4(1.00)  # RSP = SPY
        70.0
    """
    if rsp_spy_ratio >= ratio_strong:
        return score_strong  # Broad participation
    elif rsp_spy_ratio >= ratio_healthy:
        return score_healthy  # Normal participation
    elif rsp_spy_ratio >= ratio_narrow:
        return score_narrow  # Narrowing
    elif rsp_spy_ratio >= ratio_weak:
        return score_weak  # Weak breadth
    else:
        return score_weak * 0.8  # Very weak (capped at 24)


def aggregate_regime_score_v4(
    momentum_score: float,
    vix_direction_score: float,
    breadth_score: float,
    drawdown_score: float,
    trend_score: float,
    weight_momentum: float = 0.30,
    weight_vix_direction: float = 0.25,
    weight_breadth: float = 0.20,
    weight_drawdown: float = 0.15,
    weight_trend: float = 0.10,
) -> float:
    """V4.0: Calculate weighted aggregate regime score with leading indicators.

    55% weight on leading/concurrent indicators (momentum, VIX direction, breadth)
    vs V3.3's 0%. This enables 1-2 day crash detection vs 4-7 day lag.

    Args:
        momentum_score: 20-day ROC momentum score (0-100).
        vix_direction_score: 5-day VIX direction score (0-100).
        breadth_score: RSP/SPY breadth score (0-100).
        drawdown_score: Drawdown from HWM score (0-100).
        trend_score: SPY vs MA200 trend score (0-100).
        weight_*: Weights for each factor (must sum to 1.0).

    Returns:
        Aggregate V4.0 regime score (0-100).

    Example:
        >>> aggregate_regime_score_v4(25, 10, 42, 85, 85)  # Jan 2022 crash
        42.25  # CAUTIOUS (V3.3 would be 77.5 RISK_ON)
    """
    raw = (
        momentum_score * weight_momentum
        + vix_direction_score * weight_vix_direction
        + breadth_score * weight_breadth
        + drawdown_score * weight_drawdown
        + trend_score * weight_trend
    )
    return clamp(raw, 0, 100)


# =============================================================================
# V5.3 REGIME CALCULATIONS
# =============================================================================


def vix_combined_score(
    vix_level: float,
    vix_level_score: float,
    vix_direction_score: float,
    level_weight: float = 0.60,
    direction_weight: float = 0.40,
    high_vix_threshold: float = 25.0,
    high_vix_clamp: float = 47.0,
) -> float:
    """V5.3: Calculate VIX Combined score (60% level + 40% direction).

    Combines absolute VIX level (fear intensity) with VIX direction (fear velocity)
    for a more nuanced fear reading. When VIX >= threshold, clamps score to prevent
    bullish signals during elevated fear.

    Args:
        vix_level: Current VIX value.
        vix_level_score: VIX level factor score (0-100).
        vix_direction_score: VIX 5-day direction score (0-100).
        level_weight: Weight for VIX level component (default 0.60).
        direction_weight: Weight for VIX direction component (default 0.40).
        high_vix_threshold: VIX level above which clamp is applied (default 25).
        high_vix_clamp: Maximum combined score when VIX >= threshold (default 47).

    Returns:
        VIX Combined score (0-100). Clamped at high_vix_clamp when VIX >= threshold.

    Example:
        >>> vix_combined_score(18, 70, 55)  # Normal VIX
        64.0  # 70*0.6 + 55*0.4 = 64
        >>> vix_combined_score(30, 40, 55)  # High VIX
        47.0  # Clamped at 47
    """
    combined = vix_level_score * level_weight + vix_direction_score * direction_weight

    # High-VIX clamp: Cap score when VIX is elevated
    if vix_level >= high_vix_threshold:
        combined = min(combined, high_vix_clamp)

    return clamp(combined, 0, 100)


def breadth_decay_penalty(
    rsp_spy_ratio_5d_change: float,
    rsp_spy_ratio_10d_change: float,
    threshold_5d: float = -0.10,
    threshold_10d: float = -0.15,
    penalty_5d: float = 5.0,
    penalty_10d: float = 8.0,
) -> float:
    """V5.3: Calculate breadth decay penalty for regime score.

    Detects distribution (smart money selling into strength) before price confirms.
    When breadth is decaying (RSP underperforming SPY on a rolling basis),
    applies penalty to regime score.

    Args:
        rsp_spy_ratio_5d_change: 5-day change in RSP/SPY ratio as decimal.
        rsp_spy_ratio_10d_change: 10-day change in RSP/SPY ratio as decimal.
        threshold_5d: Threshold for 5d decay penalty (default -0.10 = -10%).
        threshold_10d: Threshold for 10d decay penalty (default -0.15 = -15%).
        penalty_5d: Points to subtract for 5d decay (default 5).
        penalty_10d: Points to subtract for 10d decay (default 8).

    Returns:
        Total penalty points (0 to penalty_5d + penalty_10d).
        Penalties stack (10d decay triggers both penalties).

    Example:
        >>> breadth_decay_penalty(-0.08, -0.12)  # Mild decay
        0  # No penalty (neither threshold met)
        >>> breadth_decay_penalty(-0.12, -0.12)  # 5d decay only
        5.0  # 5-day penalty applied
        >>> breadth_decay_penalty(-0.12, -0.18)  # Both decays
        13.0  # 5 + 8 = 13 point penalty
    """
    penalty = 0.0

    # 5-day decay penalty
    if rsp_spy_ratio_5d_change <= threshold_5d:
        penalty += penalty_5d

    # 10-day decay penalty (stacks with 5d)
    if rsp_spy_ratio_10d_change <= threshold_10d:
        penalty += penalty_10d

    return penalty


def aggregate_regime_score_v53(
    momentum_score: float,
    vix_combined_score: float,
    trend_score: float,
    drawdown_score: float,
    breadth_penalty: float = 0.0,
    weight_momentum: float = 0.30,
    weight_vix_combined: float = 0.30,
    weight_trend: float = 0.25,
    weight_drawdown: float = 0.15,
) -> float:
    """V5.3: Calculate weighted aggregate regime score with VIX Combined.

    4-factor model with VIX Combined (level + direction) replacing separate
    VIX factors. Breadth penalty applied after base calculation.

    Args:
        momentum_score: 20-day ROC momentum score (0-100).
        vix_combined_score: VIX Combined score (0-100).
        trend_score: SPY vs MA200 trend score (0-100).
        drawdown_score: Drawdown from HWM score (0-100).
        breadth_penalty: Points to subtract for breadth decay (default 0).
        weight_*: Weights for each factor (must sum to 1.0).

    Returns:
        Aggregate V5.3 regime score (0-100).

    Example:
        >>> aggregate_regime_score_v53(75, 64, 80, 85)  # Bull market
        75.15  # RISK_ON
        >>> aggregate_regime_score_v53(25, 40, 30, 30, breadth_penalty=8)  # Bear with decay
        22.5  # RISK_OFF (further reduced by breadth penalty)
    """
    raw = (
        momentum_score * weight_momentum
        + vix_combined_score * weight_vix_combined
        + trend_score * weight_trend
        + drawdown_score * weight_drawdown
    )

    # Apply breadth decay penalty
    raw -= breadth_penalty

    return clamp(raw, 0, 100)
