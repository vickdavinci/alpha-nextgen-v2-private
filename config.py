"""
Alpha NextGen Configuration
All tunable parameters in one place.
"""

# Canonical version label for run naming and audit references.
# Patch line V12.23.1 starts from commit bb0e8e1.
ACTIVE_STRATEGY_VERSION = "V12.23.1"

# =============================================================================
# CAPITAL ENGINE
# =============================================================================

# V3.0: Removed SEED/GROWTH phase system - regime-based safeguards replace it
# - Startup Gate handles new deployment ramp-up (time-based)
# - Drawdown Governor handles capital protection (performance-based)
# - Regime Engine handles market adaptation (conditions-based)
# Account size no longer determines allocation - market conditions do.

INITIAL_CAPITAL = 100_000  # V6.20: Raised capital baseline for options-capacity testing
MAX_SINGLE_POSITION_PCT = 0.40  # V3.0: Max weight for any single position (was phase-dependent)

TARGET_VOLATILITY = 0.20
# V2.3.17: Kill switch raised from 3% to 5% (reduces false triggers in volatile markets)
KILL_SWITCH_PCT = 0.99  # Backtest mode: effectively disable binary kill switch

# Lockbox
LOCKBOX_MILESTONES = [100_000, 200_000]
LOCKBOX_LOCK_PCT = 0.10

# V2.3 Capital Reservation (addresses options buying power issue)
# When all 4 trend tickers trigger simultaneously, they consume ~196% of capital
# via margin, leaving no buying power for options (25% allocation)
# This reserves capital BEFORE trend positions are sized
RESERVED_OPTIONS_PCT = 0.50  # V6.20: Reserve 50% for options allocation

# V2.18: Capital Firewall - Hard partition between engines to prevent starvation
# Previously Trend could consume all capital, leaving Options with "Entries allowed=-1"
CAPITAL_PARTITION_TREND = 0.50  # 50% reserved for Trend Engine (was 55%)
CAPITAL_PARTITION_OPTIONS = 0.50  # 50% reserved for Options Engine (was 25%)

# V2.18: Leverage Cap - Prevent margin overflow (was hitting 196% with all 4 trend tickers)
# Block entries if projected margin exceeds this threshold
MAX_MARGIN_WEIGHTED_ALLOCATION = 0.90  # Never exceed 90% margin consumption

# V3.0: Total Allocation Cap - Prevent over-allocation when all engines active
# Sum of all engine allocations must not exceed this threshold
MAX_TOTAL_ALLOCATION = 0.95  # Never allocate more than 95% of portfolio

# V3.0: Engine Priority System - Lower number = higher priority
# When scaling down due to allocation conflicts, reduce lower priority engines first
ENGINE_PRIORITY = {
    "RISK": 0,  # Highest - emergency liquidations (never scaled)
    "HEDGE": 1,  # Second - defensive positions
    "TREND": 2,  # Core positions
    "OPT": 3,  # Satellite - options
    "OPT_INTRADAY": 4,  # Satellite - intraday options
    "MR": 5,  # Lowest - opportunistic mean reversion
    "COLD_START": 2,  # Same as TREND (subset)
    "ROUTER": 0,  # Same as RISK (internal)
}

# V3.0: Hedge Regime Gating - Only run hedges when regime is below this threshold
# Per thesis: Hedges should be 0% in Bull (70+) and Neutral (50-69)
HEDGE_REGIME_GATE = 50  # Hedges only active when regime < 50

# V2.3.24: Leverage multipliers for margin calculation
# Used by portfolio_router to calculate actual margin consumption, not just allocation %
# V6.11: Universe redesign - diversified trend + commodity hedges
SYMBOL_LEVERAGE = {
    # Trend Engine (diversified)
    "QLD": 2.0,  # 2× Nasdaq
    "SSO": 2.0,  # 2× S&P 500
    "UGL": 2.0,  # 2× Gold (commodity hedge)
    "UCO": 2.0,  # 2× Crude Oil (commodity hedge)
    # Mean Reversion Engine
    "TQQQ": 3.0,  # 3× Nasdaq (MR)
    "SPXL": 3.0,  # 3× S&P 500 (MR)
    "SOXL": 3.0,  # 3× Semiconductor (MR)
    # Hedge Engine
    "SH": 1.0,  # 1× Inverse S&P (Hedge)
}

# V2.3.24: Minimum contracts for scaled spreads
# If margin check would reduce spread below this, skip the trade entirely
MIN_SPREAD_CONTRACTS = 2
COMBO_MARGIN_SAFETY_BUFFER_PCT = 0.05  # Keep 5% buying-power headroom for combo submits

# =============================================================================
# REGIME ENGINE
# =============================================================================

# -----------------------------------------------------------------------------
# V3.3: SIMPLIFIED 3-FACTOR MODEL
# -----------------------------------------------------------------------------
# Problem: 7-factor model caused score compression (stuck at 43-50 in grinding bears)
# Solution: 3 decisive factors that directly measure what matters
#
# Factor 1: TREND (35%) - Is the market going up or down?
# Factor 2: VIX LEVEL (30%) - Is there fear/panic?
# Factor 3: DRAWDOWN (35%) - How far has market fallen from peak?
#
# Guards (not weighted, just safety valves):
# - VIX Direction Shock Cap: Caps regime at CAUTIOUS when VIX spiking
# - Recovery Hysteresis: Prevents "sticky bear" after V-shaped recoveries

V3_REGIME_SIMPLIFIED_ENABLED = True  # Enable simplified 3-factor model

# V3.3 Factor Weights (must sum to 1.0)
WEIGHT_TREND_V3 = 0.35  # Direction indicator (SPY vs MA200)
WEIGHT_VIX_V3 = 0.30  # Fear/panic level
WEIGHT_DRAWDOWN_V3 = 0.35  # Distance from 52-week high (breaks compression)

# Drawdown Factor Thresholds (SPY drawdown from 52-week high)
DRAWDOWN_THRESHOLD_BULL = 0.05  # 0-5% = Bull pullback → Score 90
DRAWDOWN_THRESHOLD_CORRECTION = 0.10  # 5-10% = Correction → Score 70
DRAWDOWN_THRESHOLD_PULLBACK = 0.15  # 10-15% = Pullback → Score 50
DRAWDOWN_THRESHOLD_BEAR = 0.20  # 15-20% = Bear territory → Score 30
# 20%+ = Deep bear → Score 10

# Drawdown Factor Scores (mapped to thresholds above)
DRAWDOWN_SCORE_BULL = 90
DRAWDOWN_SCORE_CORRECTION = 70
DRAWDOWN_SCORE_PULLBACK = 50
DRAWDOWN_SCORE_BEAR = 30
DRAWDOWN_SCORE_DEEP_BEAR = 10

# Guard 1: VIX Direction Shock Cap
# When VIX is spiking, cap regime at CAUTIOUS for fast crash response
VIX_SHOCK_CAP_ENABLED = True
VIX_SHOCK_CAP_THRESHOLD = 10.0  # VIX rising > 10% = shock
VIX_SHOCK_CAP_MAX_REGIME = 49  # Cap at CAUTIOUS (below NEUTRAL)
VIX_SHOCK_CAP_DECAY_DAYS = 2  # Decay cap after 2 sessions if not confirmed

# Guard 2: Recovery Hysteresis
# Prevents "sticky bear" - upgrades require confirmation, downgrades are immediate
RECOVERY_HYSTERESIS_ENABLED = True
RECOVERY_HYSTERESIS_DAYS = 2  # Require 2 days of improvement to upgrade
# V3.8: Raised from 25 to 35 - VIX 25-35 is common during V-recoveries (Mar 2020, 2011)
# Old threshold blocked ALL upgrades for 63 days in Mar 2020 while market rallied 30%
RECOVERY_HYSTERESIS_VIX_MAX = 35.0  # VIX must be below this to allow upgrade

# =============================================================================
# V4.0 REGIME MODEL (5-Factor with Leading Indicators)
# =============================================================================
# Purpose: Fix V3.3's 4-7 day lag in crash detection by using leading indicators.
# V4.0 simulated 88% accuracy vs V3.3's 59% across 17 market scenarios.
#
# Factor 1: SHORT-TERM MOMENTUM (30%) - 20-day Rate of Change (leading)
# Factor 2: VIX DIRECTION (25%) - 5-day VIX change + spike detection (leading)
# Factor 3: MARKET BREADTH (20%) - RSP/SPY ratio (concurrent)
# Factor 4: DRAWDOWN (15%) - Distance from 52-week high (lagging, reduced)
# Factor 5: LONG-TERM TREND (10%) - SPY vs MA200 (lagging, context only)
#
# Key improvement: 55% weight on leading/concurrent indicators vs V3.3's 0%.

V4_REGIME_ENABLED = False  # V4.0: Disabled - V5.3 is now the active model

# V4.1: VIX Level Fix - Replace VIX Direction with VIX Level
# Problem: V4.0's VIX Direction (rising/falling) scores 55 for STABLE high VIX (30+)
#          This creates "score ceiling" at ~65 in both bull AND bear markets
# Fix: Use VIX Level (absolute fear) instead - VIX>30 scores 15, VIX<15 scores 85
# Result: +7.5 points in bull (RISK_ON achieved), -10 points in bear (faster DEFENSIVE)
V4_1_VIX_LEVEL_ENABLED = True  # V4.1: Use VIX Level instead of VIX Direction

# V4.0/V4.1 Factor Weights (must sum to 1.0)
WEIGHT_MOMENTUM_V4 = 0.30  # 20-day ROC - catches reversals in days, not months
WEIGHT_VIX_LEVEL_V4 = 0.25  # V4.1: Absolute VIX level - fear intensity (replaces VIX Direction)
WEIGHT_VIX_DIRECTION_V4 = 0.25  # V4.0 (deprecated): VIX change - spike = danger, falling = safe
WEIGHT_BREADTH_V4 = 0.20  # RSP/SPY - flags narrow rallies and weakness
WEIGHT_DRAWDOWN_V4 = 0.15  # Reduced from V3.3's 35% - still useful but lagging
WEIGHT_TREND_V4 = 0.10  # Reduced from V3.3's 35% - context only

# Factor 1: Short-term Momentum (20-day ROC)
# ROC = (Current Price - Price 20 days ago) / Price 20 days ago
MOMENTUM_LOOKBACK = 20  # 20-day rate of change
MOMENTUM_THRESHOLD_STRONG_BULL = 0.05  # ROC > +5% = Strong bullish → Score 90
MOMENTUM_THRESHOLD_BULL = 0.02  # ROC +2% to +5% = Bullish → Score 75
MOMENTUM_THRESHOLD_NEUTRAL_HIGH = 0.01  # ROC +1% to +2% = Neutral high → Score 60
MOMENTUM_THRESHOLD_NEUTRAL_LOW = -0.01  # ROC -1% to +1% = Neutral → Score 50
MOMENTUM_THRESHOLD_BEAR = -0.02  # ROC -2% to -1% = Bearish → Score 40
MOMENTUM_THRESHOLD_STRONG_BEAR = -0.05  # ROC < -5% = Strong bearish → Score 10
# Score mapping
MOMENTUM_SCORE_STRONG_BULL = 90
MOMENTUM_SCORE_BULL = 75
MOMENTUM_SCORE_NEUTRAL_HIGH = 60
MOMENTUM_SCORE_NEUTRAL = 50
MOMENTUM_SCORE_NEUTRAL_LOW = 40
MOMENTUM_SCORE_BEAR = 25
MOMENTUM_SCORE_STRONG_BEAR = 10

# Factor 2: VIX Direction (5-day change + spike detection)
# Measures fear VELOCITY, not just level - key for early crash detection
VIX_DIRECTION_LOOKBACK = 5  # 5-day VIX change
VIX_DIRECTION_SPIKE_THRESHOLD = 0.20  # VIX up >20% in 5 days = spike (score 10)
VIX_DIRECTION_RISING_FAST = 0.10  # VIX up 10-20% = rising fast → Score 25
VIX_DIRECTION_RISING = 0.05  # VIX up 5-10% = rising → Score 40
VIX_DIRECTION_STABLE_HIGH = 0.02  # VIX up 0-5% = stable/rising → Score 50
VIX_DIRECTION_STABLE_LOW = -0.02  # VIX change -2% to +2% = stable → Score 55
VIX_DIRECTION_FALLING = -0.10  # VIX down 2-10% = falling → Score 70
VIX_DIRECTION_FALLING_FAST = -0.20  # VIX down >10% = falling fast → Score 85
# Score mapping
VIX_DIR_SCORE_SPIKE = 10  # Extreme fear spike
VIX_DIR_SCORE_RISING_FAST = 25  # Fear increasing rapidly
VIX_DIR_SCORE_RISING = 40  # Fear increasing
VIX_DIR_SCORE_STABLE_HIGH = 50  # Slightly rising
VIX_DIR_SCORE_STABLE = 55  # Stable
VIX_DIR_SCORE_FALLING = 70  # Fear decreasing
VIX_DIR_SCORE_FALLING_FAST = 85  # Fear decreasing rapidly

# Factor 3: Market Breadth (RSP/SPY ratio)
# Measures participation - narrow rallies (low breadth) are warning signs
# Already have RSP/SPY calculation, just need scoring
BREADTH_RATIO_STRONG = 1.02  # RSP outperforming SPY by 2%+ → Score 85
BREADTH_RATIO_HEALTHY = 1.00  # RSP ~ SPY → Score 70
BREADTH_RATIO_NARROW = 0.98  # RSP underperforming by 2% → Score 50
BREADTH_RATIO_WEAK = 0.96  # RSP underperforming by 4%+ → Score 30
# Score mapping
BREADTH_SCORE_STRONG = 85  # Broad participation
BREADTH_SCORE_HEALTHY = 70  # Normal participation
BREADTH_SCORE_NARROW = 50  # Narrowing rally
BREADTH_SCORE_WEAK = 30  # Very narrow/defensive

# V4.0 VIX Spike Cap (enhanced from V3.3)
# Immediate regime cap when VIX spikes - critical for crash protection
# V4.0.1 FIX: Added VIX_MIN_LEVEL to prevent false triggers in low-VIX bull markets
V4_SPIKE_CAP_ENABLED = True
V4_SPIKE_CAP_THRESHOLD = (
    0.25  # V4.0.1: Raised from 0.15 to 0.25 (VIX up >25% in 5 days triggers cap)
)
V4_SPIKE_CAP_VIX_MIN_LEVEL = (
    15.0  # V4.0.1 FIX: Only trigger spike cap if VIX > 15 (ignore low-VIX noise)
)
V4_SPIKE_CAP_MAX_SCORE = 45  # Cap score at CAUTIOUS during spike
V4_SPIKE_CAP_DECAY_DAYS = 3  # Cap persists for 3 days unless VIX falls

# =============================================================================
# V5.3 MACRO REGIME MODEL (Phase 1 & 2)
# =============================================================================
# 4-factor model with VIX Combined (Level + Direction) scoring
#
# Key improvements over V4.1:
# - VIX Combined: 60% level + 40% direction for nuanced fear reading
# - High-VIX clamp: VIX Combined capped at 47 when VIX >= 25
# - Spike cap: Score capped at 45 when VIX 5d >= +28%
# - Breadth decay penalty: Detects distribution before price confirms

V53_REGIME_ENABLED = True  # V5.3: Master switch for new regime model

# V5.3 Factor Weights (must sum to 1.0)
WEIGHT_MOMENTUM_V53 = 0.30  # 20-day ROC - catches reversals in days
WEIGHT_VIX_COMBINED_V53 = 0.35  # V6.15 TUNE: Increase fear sensitivity in stress
WEIGHT_TREND_V53 = 0.20  # V6.15 TUNE: Reduce lagging trend dominance
WEIGHT_DRAWDOWN_V53 = 0.15  # Distance from 52-week high

# VIX Combined scoring: 60% VIX Level + 40% VIX Direction
VIX_COMBINED_LEVEL_WEIGHT = 0.65  # V6.15 TUNE: Anchor more on absolute fear level
VIX_COMBINED_DIRECTION_WEIGHT = 0.35  # V6.15 TUNE: Keep directional context
VIX_COMBINED_HIGH_VIX_CLAMP = 44  # V6.15 TUNE: Push high-VIX states faster to caution
VIX_COMBINED_HIGH_VIX_THRESHOLD = 25.0  # VIX level threshold for clamp

# V5.3 Spike Cap: Macro score capped at 45 when VIX 5d change >= +28%
V53_SPIKE_CAP_ENABLED = True
V53_SPIKE_CAP_THRESHOLD = 0.28  # VIX up >28% in 5 days triggers cap
# V6.6: Lowered from 45 to 38 to force DEFENSIVE during VIX spikes
# 45 only reached CAUTIOUS (40-49), missing the defensive posture needed
V53_SPIKE_CAP_MAX_SCORE = 38  # Cap score at DEFENSIVE during spike
V53_SPIKE_CAP_DECAY_DAYS = 2  # V10.31: shorten sticky post-shock penalty window
V53_SPIKE_CAP_EARLY_RELEASE_ENABLED = True
V53_SPIKE_CAP_EARLY_RELEASE_5D_MAX = (
    0.12  # V10.31: release spike cap once 5d VIX surge cools below +12%
)
V53_SPIKE_CAP_EARLY_RELEASE_VIX_MAX = 22.0  # V10.31: only release once VIX itself normalizes

# Phase 2: Breadth Decay Penalty
# Detects distribution (smart money selling) before price confirms
V53_BREADTH_DECAY_ENABLED = True
# V6.6: Relaxed thresholds from -10%/-15% to -2%/-4%
# Old thresholds never triggered - RSP/SPY relative moves are typically 1-3%
# A -2% divergence over 5 days indicates meaningful distribution
V53_BREADTH_5D_DECAY_THRESHOLD = -0.01  # V6.9: Trigger earlier (-1% vs -2%)
V53_BREADTH_10D_DECAY_THRESHOLD = -0.03  # V6.9: Trigger earlier (-3% vs -4%)
V53_BREADTH_5D_PENALTY = 8  # V6.9: Increase penalty to pull regime down faster
V53_BREADTH_10D_PENALTY = 12  # V6.9: Stronger 10d decay penalty (stacks with 5d)
# V12.0: Elastic breadth penalty - avoid over-penalizing narrow leadership in strong uptrends.
V53_BREADTH_PENALTY_ELASTIC_ENABLED = True
V53_BREADTH_PENALTY_STRONG_TREND_SCORE = 75.0
V53_BREADTH_PENALTY_WEAK_TREND_SCORE = 50.0
V53_BREADTH_PENALTY_STRONG_TREND_CAP = 5.0

# -----------------------------------------------------------------------------
# LEGACY 7-FACTOR WEIGHTS (V3.0) - Used if V3_REGIME_SIMPLIFIED_ENABLED = False
# -----------------------------------------------------------------------------
# Factor Weights (V3.0: Normalized to 100% with VIX Direction enabled)
# Research-based allocation: leading indicators (Credit, VIX Dir) balanced with
# lagging indicators (Trend, Realized Vol) for regime identification.
WEIGHT_TREND = 0.20  # V3.0: Lagging indicator, reduced from 0.25
WEIGHT_VIX = 0.15  # V3.0: Implied volatility level, reduced from 0.20
WEIGHT_VOLATILITY = 0.10  # V3.0: Realized vol (lagging), reduced from 0.15
WEIGHT_BREADTH = 0.15  # V3.0: Market breadth, reduced from 0.20
WEIGHT_CREDIT = 0.15  # Leading indicator (unchanged)
WEIGHT_CHOP = 0.10  # V3.0: ADX trend quality, increased from 0.05

# Smoothing
REGIME_SMOOTHING_ALPHA = 0.40  # V10.22: slightly faster macro response to reduce transition lag

# Thresholds
REGIME_RISK_ON = 70
REGIME_NEUTRAL = 50
REGIME_CAUTIOUS = 45
REGIME_DEFENSIVE = 35

# Transition-aware macro guards for ITM/VASS (keep MICRO unchanged).
REGIME_TRANSITION_GUARD_ENABLED = True
REGIME_BASE_STATE_MACHINE_ENABLED = True
REGIME_BASE_BULL_ENTER = 57.0
REGIME_BASE_BULL_EXIT = 53.0
REGIME_BASE_BEAR_ENTER = 43.0
REGIME_BASE_BEAR_EXIT = 47.0
REGIME_BASE_STATE_DWELL_BARS = 2
REGIME_OVERLAY_STATE_DWELL_BARS = 2
REGIME_OVERLAY_DWELL_RECOVERY_BARS = 1
REGIME_OVERLAY_DWELL_DETERIORATION_BARS = 1
REGIME_OVERLAY_DWELL_AMBIGUOUS_BARS = 2
REGIME_OVERLAY_EXIT_DETERIORATION_DWELL_BARS = 3
REGIME_TRANSITION_AMBIGUOUS_LOW = 47.0
REGIME_TRANSITION_AMBIGUOUS_HIGH = 55.0
REGIME_TRANSITION_AMBIGUOUS_DELTA_MAX = 1.5
REGIME_TRANSITION_AMBIGUOUS_DETECTOR_DELTA_MAX = 0.5
REGIME_TRANSITION_AMBIGUOUS_MAX_BARS = 6
REGIME_TRANSITION_RECOVERY_DELTA_MIN = 2.5
REGIME_TRANSITION_RECOVERY_DETECTOR_DELTA_MIN = 0.4
REGIME_TRANSITION_RECOVERY_EOD_AGREEMENT_MIN = 0.15
REGIME_TRANSITION_RECOVERY_MOMENTUM_MIN = 0.015
REGIME_TRANSITION_RECOVERY_VIX_5D_MAX = 0.05
REGIME_TRANSITION_DETERIORATION_DELTA_MAX = -2.5
REGIME_TRANSITION_DETERIORATION_DETECTOR_DELTA_MAX = -0.15
REGIME_TRANSITION_DETERIORATION_EOD_AGREEMENT_MAX = -0.15
REGIME_TRANSITION_DETERIORATION_MOMENTUM_MAX = -0.008
REGIME_TRANSITION_DETERIORATION_VIX_5D_MIN = 0.04
# Fast deterioration path: allow earlier overlay activation when downside pressure is broad.
REGIME_TRANSITION_DETERIORATION_FAST_EOD_DELTA_MAX = -1.8
REGIME_TRANSITION_DETERIORATION_FAST_MOMENTUM_MAX = -0.012
REGIME_TRANSITION_DETERIORATION_FAST_VIX_5D_MIN = 0.03
REGIME_TRANSITION_DETERIORATION_FAST_DETECTOR_DELTA_MAX = -0.02
REGIME_TRANSITION_DETERIORATION_PRICE_LED_ENABLED = True
REGIME_TRANSITION_DETERIORATION_PRICE_LED_EOD_DELTA_MAX = -1.2
REGIME_TRANSITION_DETERIORATION_PRICE_LED_MOMENTUM_MAX = -0.010
REGIME_TRANSITION_DETERIORATION_PRICE_LED_DETECTOR_DELTA_MAX = -0.04
REGIME_TRANSITION_DETERIORATION_PRICE_LED_SCORE_MAX = 57.0
REGIME_TRANSITION_RECOVERY_SCORE_LIFT_MAX = 8.0
# Append regime proxy windows once per day near close (exchange-aware; early close safe).
REGIME_PROXY_CLOSE_BUFFER_MINUTES = 0
REGIME_OBSERVABILITY_ENABLED = True
REGIME_OBSERVABILITY_MAX_ROWS = 50000
REGIME_OBSERVABILITY_OBJECTSTORE_ENABLED = True
REGIME_OBSERVABILITY_OBJECTSTORE_KEY_PREFIX = "regime_observability"
REGIME_TIMELINE_OBSERVABILITY_ENABLED = True
REGIME_TIMELINE_OBSERVABILITY_MAX_ROWS = 12000
REGIME_TIMELINE_OBJECTSTORE_ENABLED = True
REGIME_TIMELINE_OBJECTSTORE_KEY_PREFIX = "regime_timeline_observability"

SIGNAL_LIFECYCLE_OBSERVABILITY_ENABLED = True
SIGNAL_LIFECYCLE_OBSERVABILITY_MAX_ROWS = 50000
SIGNAL_LIFECYCLE_OBJECTSTORE_ENABLED = True
SIGNAL_LIFECYCLE_OBJECTSTORE_KEY_PREFIX = "signal_lifecycle_observability"

ROUTER_REJECTION_OBSERVABILITY_ENABLED = True
ROUTER_REJECTION_OBSERVABILITY_MAX_ROWS = 25000
ROUTER_REJECTION_OBJECTSTORE_ENABLED = True
ROUTER_REJECTION_OBJECTSTORE_KEY_PREFIX = "router_rejection_observability"

ORDER_LIFECYCLE_OBSERVABILITY_ENABLED = True
ORDER_LIFECYCLE_OBSERVABILITY_MAX_ROWS = 50000
ORDER_LIFECYCLE_OBJECTSTORE_ENABLED = True
ORDER_LIFECYCLE_OBJECTSTORE_KEY_PREFIX = "order_lifecycle_observability"
OBSERVABILITY_OBJECTSTORE_SHARD_ENABLED = True
OBSERVABILITY_OBJECTSTORE_SHARD_MAX_ROWS = 12000
OBSERVABILITY_OBJECTSTORE_MAX_SHARDS = 32
OBSERVABILITY_OBJECTSTORE_SAVE_RETRIES = 2
OBSERVABILITY_LOG_FALLBACK_ENABLED = False
OBSERVABILITY_LOG_FALLBACK_CHUNK_SIZE = 3400
REGIME_OBSERVABILITY_LOG_FALLBACK_ENABLED = False
REGIME_TIMELINE_LOG_FALLBACK_ENABLED = False
SIGNAL_LIFECYCLE_LOG_FALLBACK_ENABLED = False
ROUTER_REJECTION_LOG_FALLBACK_ENABLED = False
ORDER_LIFECYCLE_LOG_FALLBACK_ENABLED = False

# Trend Factor
SMA_FAST = 20
SMA_MED = 50
SMA_SLOW = 200
EXTENDED_THRESHOLD = 1.05
OVERSOLD_THRESHOLD = 0.95

# VIX Factor (V2.3 NEW)
VIX_LOW_THRESHOLD = 15  # Below this = complacent, cheap options
VIX_NORMAL_THRESHOLD = 22  # Below this = normal environment
VIX_HIGH_THRESHOLD = 30  # Above this = expensive options
VIX_EXTREME_THRESHOLD = 40  # Above this = crisis, avoid buying

# V2.3.11: VIX Level Boundaries for Micro Regime Engine
# Lowered VERY_CALM from 15 → 11.5 to fire more SNIPER 0DTEs
VIX_LEVEL_VERY_CALM_MAX = 11.5  # V2.3.11: VIX < 11.5 = VERY_CALM (was 15)
VIX_LEVEL_CALM_MAX = 15.0  # VIX 11.5-15 = CALM (shifted down)
VIX_LEVEL_NORMAL_MAX = 18.0  # VIX 15-18 = NORMAL (unchanged)
VIX_LEVEL_ELEVATED_MAX = 22.0  # V2.23.1: VIX 18-22 = ELEVATED (was hardcoded)

# V3.0: VIX Direction for Daily Regime (detect crashes same-day like Micro Regime)
# VIX momentum is a LEADING indicator - catches crashes 2-3 days earlier than level alone
# Direction = % change from prior close (positive = rising fear)
VIX_DIRECTION_ENABLED = True  # Enable VIX direction modifier in daily regime
VIX_DIRECTION_WEIGHT = 0.15  # Weight for direction modifier (normalized with other weights)

# V3.0: VIX Direction Score Clamping Safeguard
# Prevents VIX Direction from single-handedly dragging score across regime boundaries.
# At 15% weight, unclamped range (0-100) creates 15-point swing potential.
# Clamping to 25-75 limits swing to 7.5 points (0.15 × 50 = 7.5).
# This ensures VIX Direction can't cross 10-point boundaries alone (50→40, 40→30).
VIX_DIRECTION_SCORE_CLAMP_MIN = 25.0  # Min VIX direction score (prevents -11.25 point drag)
VIX_DIRECTION_SCORE_CLAMP_MAX = 75.0  # Max VIX direction score (prevents +3.75 point boost)

# Direction thresholds (% change from prior close)
VIX_DAILY_DIRECTION_SPIKING = 15.0  # VIX +15% = crisis building
VIX_DAILY_DIRECTION_RISING_FAST = 8.0  # VIX +8% = significant stress
VIX_DAILY_DIRECTION_RISING = 3.0  # VIX +3% = mild concern
VIX_DAILY_DIRECTION_FALLING = -3.0  # VIX -3% = recovery
VIX_DAILY_DIRECTION_FALLING_FAST = -8.0  # VIX -8% = strong recovery

# Direction score modifiers (applied to VIX score, then weighted into regime)
# Negative = penalty (reduces regime score), Positive = bonus
VIX_DIRECTION_SCORE_SPIKING = 0  # VIX spiking = score 0 (max penalty)
VIX_DIRECTION_SCORE_RISING_FAST = 25  # VIX rising fast = score 25
VIX_DIRECTION_SCORE_RISING = 40  # VIX rising = score 40
VIX_DIRECTION_SCORE_STABLE = 50  # VIX stable = score 50 (neutral)
VIX_DIRECTION_SCORE_FALLING = 70  # VIX falling = score 70
VIX_DIRECTION_SCORE_FALLING_FAST = 100  # VIX falling fast = score 100 (max bonus)

# Volatility Factor (Realized)
VOL_LOOKBACK = 20
VOL_PERCENTILE_LOOKBACK = 252

# Breadth & Credit
BREADTH_LOOKBACK = 20
CREDIT_LOOKBACK = 20

# V2.26: Chop Detection Factor (ADX-based regime sub-score)
# ADX(14) of SPY measures trend strength regardless of direction
CHOP_ADX_THRESHOLD_STRONG = 25  # ADX >= 25 = strong trend (score 100)
CHOP_ADX_THRESHOLD_MODERATE = 20  # ADX 20-25 = moderate (score 60)
CHOP_ADX_THRESHOLD_WEAK = 15  # ADX 15-20 = weak (score 30)
# ADX < 15 = dead/choppy (score 10)

# =============================================================================
# COLD START ENGINE
# =============================================================================

COLD_START_DAYS = 5  # Number of days in cold start mode
WARM_ENTRY_SIZE_MULT = 0.50
WARM_ENTRY_TIME = "10:00"
WARM_REGIME_MIN = 50
WARM_QLD_THRESHOLD = 60
WARM_MIN_SIZE = 2_000

# V2.3.20: Allow options during cold start with reduced sizing
# During Days 1-5, options positions are sized at 50% to reduce risk
# while still participating in opportunities
OPTIONS_COLD_START_MULTIPLIER = 0.50

# =============================================================================
# V6.0: STARTUP GATE — Simplified (one-time arming, never resets on kill switch)
# =============================================================================
# Simplified 6-day arming sequence. Options are independent with their own
# conviction system (VASS/MICRO). Gate only controls TREND/MR sizing.
#
# Phases:
#   INDICATOR_WARMUP (Days 1-3): Nothing allowed, indicators warming up
#   REDUCED (Days 4-6): TREND/MR at 50%, OPTIONS at 100%
#   FULLY_ARMED (Day 7+): Everything at 100%
STARTUP_GATE_ENABLED = True
STARTUP_GATE_WARMUP_DAYS = 3  # Phase 0: Indicators warming up (nothing allowed)
STARTUP_GATE_REDUCED_DAYS = 3  # Phase 1: TREND/MR at 50%, OPTIONS at 100%
STARTUP_GATE_REDUCED_SIZE_MULT = 0.50  # TREND/MR size multiplier during REDUCED

# =============================================================================
# TREND ENGINE
# =============================================================================

# V2 Entry: MA200 + ADX Confirmation
MA200_PERIOD = 200  # Long-term trend baseline
ADX_PERIOD = 14  # Average Directional Index for momentum confirmation
ADX_ENTRY_THRESHOLD = (
    15  # V2.3.12: Lowered from 20 - avoid choking trend engine in grinding rallies
)
ADX_STRONG_THRESHOLD = 35  # ADX for highest confidence

# ADX Scoring Thresholds (V2.3.12: lowered to catch grinding trends)
# ADX < 15: 0.25 (weak) - matches exit threshold to prevent churning
# ADX 15-25: 0.50 (moderate) - grinding trends still valid
# ADX 25-35: 0.75 (strong)
# ADX >= 35: 1.00 (very strong)
ADX_WEAK_THRESHOLD = 15  # V2.3.12: Lowered to 15 - catch grinding trends (aligned with exit)
# V2.5: Lowered from 25 to 22 - ADX 22+ is sufficient for trend confirmation
# Evidence: Jan-May 2025 backtests showed QLD/SSO blocked at ADX 17-24
ADX_MODERATE_THRESHOLD = 22  # Upper bound of moderate range (V2.5: was 25)

# V3.0: Regime-Adaptive ADX Thresholds
# In strong bull markets, lower the ADX bar - trust the regime signal
# In neutral/bear markets, require stronger momentum confirmation
ADX_REGIME_BULL_THRESHOLD = 75  # Regime score above this = strong bull
ADX_REGIME_BEAR_THRESHOLD = 60  # Regime score below this = neutral/bear
ADX_BULL_MINIMUM = 15  # In strong bull, ADX > 15 is enough
ADX_BEAR_MINIMUM = 25  # In neutral/bear, require ADX > 25

# Chandelier Stop
ATR_PERIOD = 14
CHANDELIER_BASE_MULT = 3.5  # V2.3.6: Widened from 3.0 - allow more room in choppy markets
CHANDELIER_TIGHT_MULT = 3.0  # V2.3.6: Widened from 2.5 - less aggressive tightening
CHANDELIER_TIGHTER_MULT = 2.5  # V2.3.6: Widened from 2.0 - hold winners longer
PROFIT_TIGHT_PCT = 0.15  # V2.3.6: Raised from 0.10 - don't tighten too early
PROFIT_TIGHTER_PCT = 0.25  # V2.3.6: Raised from 0.20 - let trends run

# V2.3.8: 3x ETF Volatility Multipliers (PART 14 Pitfall 3)
# V6.11: No 3x symbols in trend engine (all 2x now)
# Kept for backward compatibility with Chandelier stop logic
TREND_3X_SYMBOLS: list[str] = []  # V6.11: No 3× in trend (QLD/SSO/UGL/UCO are all 2×)
CHANDELIER_3X_BASE_MULT = 2.5  # V2.3.8: Tighter than 2x (3.5) - control 3x volatility
CHANDELIER_3X_TIGHT_MULT = 2.0  # V2.3.8: Tighter than 2x (3.0)
CHANDELIER_3X_TIGHTER_MULT = 1.5  # V2.3.8: Tighter than 2x (2.5)

# Entry/Exit (V3.0: thesis-aligned - trend blocked in Cautious zone)
TREND_ENTRY_REGIME_MIN = 50  # V3.0: Trend entries only in Neutral+ (regime >= 50)
TREND_EXIT_REGIME = 30  # Exit when regime drops to Bear
TREND_ADX_EXIT_THRESHOLD = 10  # V2.3.12: Lowered to 10 - allow holding during low momentum grind

# V2.3.3 Position Limits (Part 4 audit fix)
# V6.11: Diversified trend universe (QLD/SSO/UGL/UCO)
# With commodities, not all will trigger together (uncorrelated)
MAX_CONCURRENT_TREND_POSITIONS = 4  # Allow all 4 trend tickers

# Priority order when multiple entries trigger simultaneously
# Higher priority symbols get capital first
# V6.11: QLD primary, then commodities (hedge value), then SSO
TREND_PRIORITY_ORDER = ["QLD", "UGL", "UCO", "SSO"]  # Nasdaq > Gold > Oil > S&P

# =============================================================================
# MEAN REVERSION ENGINE
# =============================================================================

# RSI
RSI_PERIOD = 5
RSI_THRESHOLD = 25  # Spec value: RSI(5) < 25

# Entry Conditions
MR_DROP_THRESHOLD = 0.025  # Spec value: 2.5% drop from open
MR_VOLUME_MULT = 1.2  # Spec value: 1.2x average volume
MR_WINDOW_START = "10:00"
MR_WINDOW_END = "15:00"
MR_REGIME_MIN = 50  # V3.0: MR entries only in Neutral+ (regime >= 50)

# Exit Conditions
MR_TARGET_PCT = 0.02
MR_STOP_PCT = 0.02
MR_FORCE_EXIT_TIME = "15:45"

# =============================================================================
# VIX REGIME CLASSIFICATION (V2.1)
# =============================================================================
# VIX-based filter to prevent catching falling knives in crashes

# VIX Thresholds
VIX_NORMAL_MAX = 20  # VIX < 20: Normal market
VIX_CAUTION_MAX = 30  # VIX 20-30: Elevated caution
VIX_HIGH_RISK_MAX = 40  # VIX 30-40: High risk
# VIX > 40: Crash mode - MR disabled

# MR Allocation by VIX Regime
MR_ALLOC_NORMAL = 0.10  # 10% allocation in normal VIX
MR_ALLOC_CAUTION = 0.05  # 5% allocation in caution
MR_ALLOC_HIGH_RISK = 0.02  # 2% allocation in high risk
MR_ALLOC_CRASH = 0.00  # 0% allocation in crash (disabled)

# MR Parameters by VIX Regime (V2.1 spec adjustments)
# RSI Thresholds (lower = more conservative in high VIX)
MR_RSI_NORMAL = 30  # RSI < 30 in normal
MR_RSI_CAUTION = 25  # RSI < 25 in caution
MR_RSI_HIGH_RISK = 20  # RSI < 20 in high risk

# Stop Loss by Regime (tighter stops in high VIX)
MR_STOP_NORMAL = 0.08  # 8% stop in normal
MR_STOP_CAUTION = 0.06  # 6% stop in caution
MR_STOP_HIGH_RISK = 0.04  # 4% stop in high risk

# Max MR Exposure by Regime
MR_MAX_EXPOSURE_NORMAL = 0.15  # 15% max in normal
MR_MAX_EXPOSURE_CAUTION = 0.10  # 10% max in caution
MR_MAX_EXPOSURE_HIGH_RISK = 0.05  # 5% max in high risk
MR_MAX_EXPOSURE_CRASH = 0.00  # 0% in crash

# =============================================================================
# HEDGE ENGINE
# =============================================================================

# Thresholds (V3.0: thesis-aligned graduated hedge response)
HEDGE_LEVEL_1 = 50  # V3.0: Light hedge starts at Cautious (regime < 50)
HEDGE_LEVEL_2 = 40  # V3.0: Medium hedge at Defensive (regime < 40)
HEDGE_LEVEL_3 = 30  # V3.0: Full hedge at Bear (regime < 30)

# V6.11: SH (1x Inverse S&P) replaces TMF/PSQ
# SH has no decay (unlike VIXY), provides direct equity hedge
HEDGE_SYMBOLS = ["SH"]
SH_LIGHT = 0.05  # Regime 40-50: 5% inverse
SH_MEDIUM = 0.08  # Regime 30-40: 8% inverse
SH_FULL = 0.10  # Regime < 30: 10% inverse

# Legacy TMF/PSQ (deprecated in V6.11)
TMF_LIGHT = 0.00
TMF_MEDIUM = 0.00
TMF_FULL = 0.00
PSQ_MEDIUM = 0.00
PSQ_FULL = 0.00

# Rebalancing
HEDGE_REBAL_THRESHOLD = 0.02

# =============================================================================
# PORTFOLIO ROUTER
# =============================================================================

# Exposure Limits
# V6.11: Simplified groups - equity + commodities + hedge
EXPOSURE_LIMITS = {
    "NASDAQ_BETA": {"max_net_long": 0.50, "max_net_short": 0.30, "max_gross": 0.75},
    "SPY_BETA": {
        "max_net_long": 0.40,
        "max_net_short": 0.15,
        "max_gross": 0.50,
    },  # V6.11: Allow SH short
    "COMMODITIES": {
        "max_net_long": 0.25,
        "max_net_short": 0.00,
        "max_gross": 0.25,
    },  # V6.11: Gold + Oil
}

# Group Membership
# V6.11: Universe redesign - diversified trend + commodity hedges
SYMBOL_GROUPS = {
    # Trend Engine
    "QLD": "NASDAQ_BETA",
    "SSO": "SPY_BETA",
    "UGL": "COMMODITIES",  # 2× Gold
    "UCO": "COMMODITIES",  # 2× Crude Oil
    # Mean Reversion Engine
    "TQQQ": "NASDAQ_BETA",
    "SPXL": "SPY_BETA",
    "SOXL": "NASDAQ_BETA",
    # Hedge Engine
    "SH": "SPY_BETA",  # 1× Inverse S&P
}

# =============================================================================
# V2.2 BALANCED ALLOCATION MODEL
# =============================================================================
# V6.11: Universe redesign - diversified trend with commodities
# - Equity exposure: QLD (15%) + SSO (7%) = 22%
# - Commodity exposure: UGL (10%) + UCO (8%) = 18%
# - Total: 40% with genuine diversification (not just correlated equity ETFs)

# Trend Engine Allocations (40% total)
# V6.11: Diversified across equities + commodities
# Margin impact: 15%×2 + 7%×2 + 10%×2 + 8%×2 = 30% + 14% + 20% + 16% = 80%
TREND_SYMBOL_ALLOCATIONS = {
    "QLD": 0.15,  # 15% - 2× Nasdaq (primary equity)
    "SSO": 0.07,  # 7% - 2× S&P 500 (broad equity)
    "UGL": 0.10,  # 10% - 2× Gold (commodity hedge, uncorrelated)
    "UCO": 0.08,  # 8% - 2× Crude Oil (energy/inflation hedge)
}
TREND_TOTAL_ALLOCATION = 0.30  # 30% total (rest-engine budget target)

# =============================================================================
# V2.4 STRUCTURAL TREND - SMA50 + HARD STOP
# =============================================================================
# V2.4 replaces Chandelier trailing stops with simpler SMA50 structural trend.
# Benefits:
# - Allows 3% minor volatility without exit (if above SMA50)
# - Longer holding periods (30-90 days vs 5-15 days)
# - Cleaner logic than tiered ATR multipliers
#
# Entry: MA200 + ADX >= 22 (V2.5: lowered from 25)
# Exit: Close < SMA50 * (1 - buffer) for 2 consecutive days (V2.5) OR Hard Stop Hit

TREND_USE_SMA50_EXIT = True  # V2.4: Use SMA50 exit instead of Chandelier
TREND_SMA_PERIOD = 50  # 50-day SMA for structural trend
TREND_SMA_EXIT_BUFFER = 0.02  # Exit when close < SMA50 * (1 - 2%)
# V2.5: Require 2 consecutive days below SMA50 before exit
# Prevents whipsaw exits in choppy markets (Jan-Mar 2025 showed 1-day exits)
TREND_SMA_CONFIRM_DAYS = 2  # Days below SMA50 required before exit

# Hard Stop Percentages (asset-specific, from entry price)
# V6.11: Updated for new universe - all 2× ETFs, wider stops for commodities
# Commodities (UGL, UCO) can be more volatile - use 18% stop
TREND_HARD_STOP_PCT = {
    "QLD": 0.15,  # 15% hard stop (2× Nasdaq)
    "SSO": 0.15,  # 15% hard stop (2× S&P)
    "UGL": 0.18,  # 18% hard stop (2× Gold - higher volatility)
    "UCO": 0.18,  # 18% hard stop (2× Crude - higher volatility)
}

# V3.0: Regime-Adaptive Stop Multipliers
# In bull markets (regime >= 75), give winners more room to run
# In cautious/bear markets (regime < 50), tighten stops to protect capital
TREND_STOP_REGIME_MULTIPLIERS = {
    75: 1.50,  # Regime >= 75: 150% of base stop (looser - let winners run)
    50: 1.00,  # Regime 50-74: 100% of base stop (standard)
    0: 0.70,  # Regime < 50: 70% of base stop (tighter - protect capital)
}

# Mean Reversion Allocations (10% total)
# V6.11: Diversified across Nasdaq, S&P, and Semis
MR_SYMBOL_ALLOCATIONS = {
    "TQQQ": 0.04,  # 4% - 3× Nasdaq (primary MR)
    "SPXL": 0.03,  # 3% - 3× S&P 500 (broad market bounces)
    "SOXL": 0.03,  # 3% - 3× Semiconductor (high-beta bounces)
}
MR_TOTAL_ALLOCATION = 0.10  # 10% total to MR Engine

# Trade Thresholds
MIN_TRADE_VALUE = 2_000
MIN_SHARE_DELTA = 1

# V2.3.24: Lower threshold for intraday options (single contracts often $500-1,500)
MIN_INTRADAY_OPTIONS_TRADE_VALUE = 500
# V10.7: Never block close/protective option intents on min trade value floors.
OPTIONS_MIN_TRADE_VALUE_PROTECTIVE_EXEMPT = True
OPTIONS_MIN_TRADE_VALUE_CLOSE_EXEMPT = True

# =============================================================================
# RISK ENGINE
# =============================================================================

# Kill Switch (V1: Nuclear option - liquidate ALL)
# V2.3.17: Raised from 3% to 5% to reduce false triggers in volatile markets
KILL_SWITCH_PCT = 0.99  # Backtest mode: effectively disable legacy kill switch
# V2.16-BT: Preemptive kill switch when panic mode active AND approaching threshold
# Closes gap between panic mode (4%) and kill switch (5%) where hedges could lose value
KILL_SWITCH_PREEMPTIVE_PCT = 0.045  # 4.5% - triggers kill switch when in panic mode

# V2.27: Graduated Kill Switch (replaces binary -5% nuclear option)
# 3-tier response: REDUCE → TREND_EXIT → FULL_EXIT
KS_GRADUATED_ENABLED = True
KS_TIER_1_PCT = 0.95  # Backtest mode: effectively disable graduated kill switch
KS_TIER_2_PCT = 0.97  # Backtest mode: effectively disable graduated kill switch
KS_TIER_3_PCT = 0.99  # Backtest mode: effectively disable graduated kill switch
KS_TIER_1_TREND_REDUCTION = 0.50  # Reduce trend allocation by 50% at Tier 1
KS_TIER_1_BLOCK_NEW_OPTIONS = True  # Block new option entries at Tier 1
KS_SKIP_DAYS = 1  # Block new entries for 1 day after Tier 2+
KS_COLD_START_RESET_ON_TIER_2 = False  # Don't reset cold start on Tier 2
KS_COLD_START_RESET_ON_TIER_3 = True  # Reset cold start on Tier 3 (true emergency)

# V2.27: KS Spread Decouple
# Spreads survive Tier 1 and Tier 2 — they have their own -50% stop (SPREAD_STOP_LOSS_PCT)
# Only Tier 3 (FULL_EXIT) liquidates spreads
KILL_SWITCH_SPREAD_DECOUPLE = True  # V10.7: Tier-2 kill switch no longer force-liquidates options

# Panic Mode
PANIC_MODE_PCT = 0.04

# Weekly Breaker
WEEKLY_BREAKER_PCT = 0.05
WEEKLY_SIZE_REDUCTION = 0.50

# Gap Filter
GAP_FILTER_PCT = 0.015

# Vol Shock
VOL_SHOCK_ATR_MULT = 3.0
VOL_SHOCK_PAUSE_MIN = 15

# Time Guard
TIME_GUARD_START = "13:55"
TIME_GUARD_END = "14:10"

# =============================================================================
# DRAWDOWN GOVERNOR V5.2 - BINARY GOVERNOR
# =============================================================================
# V5.2 Changes (2026-02-06):
# - BINARY SYSTEM: Only 100% (trading) or 0% (defensive)
# - Single threshold: 15% DD → Defensive mode
# - Eliminated intermediate 50% state (caused oscillation/churn)
# - Simplified recovery: regime guard + equity recovery from 0%
#
# Why Binary?
# - V3.x (5 levels): Massive churn between 100%→75%→50%→25%→0%
# - V5.1 (2 levels): Still had 50% "limbo" state causing confusion
# - V5.2 (binary): Either confident to trade (100%) or not (0%)
#
# Root Cause Analysis:
# - Multiple governor levels create oscillation opportunities
# - Intermediate states (50%, 25%) can't grow equity to recover
# - Each level = decision point = potential failure mode
#
# State Machine:
#   100% ─────(DD≥15%)─────→ 0%
#     ↑                       │
#     └───────────────────────┘
#        (DD<12% + Regime Guard passes)
#
DRAWDOWN_GOVERNOR_ENABLED = False  # V5.3: Disabled for strategy validation

# V5.2: BINARY - Single threshold for maximum simplicity
# Normal bull pullbacks rarely exceed 10-12%
# Genuine corrections hit 15-20%
# Single threshold at 15% catches real danger without false triggers
DRAWDOWN_GOVERNOR_LEVELS = {
    0.15: 0.00,  # V5.2: At -15% from peak → DEFENSIVE ONLY (binary)
}

# V5.2: Recovery threshold - must recover to below this DD to re-arm
# Set slightly below trigger (12% vs 15%) to prevent oscillation
DRAWDOWN_GOVERNOR_RECOVERY_THRESHOLD = 0.12  # DD must fall below 12% to re-arm

# =============================================================================
# V5.2: REMOVED MECHANISMS (Complexity Reduction)
# =============================================================================
# These mechanisms added complexity without improving outcomes.
# Kept as False/disabled for backward compatibility but NOT used.

GOVERNOR_REGIME_OVERRIDE_ENABLED = False  # V5.2: REMOVED (caused death spiral)
GOVERNOR_HWM_RESET_ENABLED = False  # V5.2: REMOVED (artificial manipulation)

# =============================================================================
# V5.2: Regime Guard for Recovery from Defensive Mode
# =============================================================================
# When at 0%, recovery requires:
# 1. DD improves below RECOVERY_THRESHOLD (12%)
# 2. Regime score >= threshold for N consecutive days
# This prevents bear rally traps.
#
GOVERNOR_REGIME_GUARD_ENABLED = True
GOVERNOR_REGIME_GUARD_THRESHOLD = 60  # Regime must be >= NEUTRAL (60+)
GOVERNOR_REGIME_GUARD_DAYS = 5  # For 5 consecutive days

# =============================================================================
# V5.2: Recovery from Defensive Mode (0%)
# =============================================================================
# Requirements to step from 0% back to 100%:
# 1. Equity recovers X% from trough
# 2. At least N days at 0% (prevents whipsaw)
# 3. Regime guard passes (regime >= 60 for 5 days)
#
GOVERNOR_EQUITY_RECOVERY_ENABLED = True
GOVERNOR_EQUITY_RECOVERY_PCT = 0.05  # 5% recovery from trough
GOVERNOR_EQUITY_RECOVERY_MIN_DAYS_AT_ZERO = 7  # V5.2: 7 days (was 10)
GOVERNOR_EQUITY_RECOVERY_REQUIRE_REGIME_GUARD = True

# =============================================================================
# V5.2: Binary Options Gating
# =============================================================================
# With binary governor (100% or 0%), options gating is simple:
# - Governor 100%: All options allowed at full size
# - Governor 0%: ONLY PUT spreads allowed (hedging/profit from decline)
#
GOVERNOR_INTRADAY_OPTIONS_MIN_SCALE = 1.0  # V5.2: Options only when FULL (100%)
GOVERNOR_INTRADAY_OPTIONS_MIN_SCALE_BEARISH = 0.0  # PUTs always allowed for hedging

# V5.2: No sizing floor needed with binary - either full size or defensive only
GOVERNOR_OPTIONS_SIZING_FLOOR = 1.0  # No partial sizing

# V5.2: Bearish options always allowed at full size during drawdowns
GOVERNOR_EXEMPT_BEARISH_OPTIONS = True

# =============================================================================
# V2.1 CIRCUIT BREAKER SYSTEM (5 Levels)
# =============================================================================
# These are graduated responses BEFORE the nuclear kill switch

# Level 1: Daily Loss Circuit Breaker
# At -2% daily loss, reduce sizing but don't liquidate
CB_DAILY_LOSS_THRESHOLD = 0.02  # -2% daily loss
CB_DAILY_SIZE_REDUCTION = 0.50  # Reduce to 50% sizing

# Level 2: Weekly Loss Circuit Breaker (same as V1 WEEKLY_BREAKER_PCT)
# Already defined above: WEEKLY_BREAKER_PCT = 0.05

# Level 3: Portfolio Volatility Circuit Breaker
# If portfolio volatility exceeds threshold, block new entries
CB_PORTFOLIO_VOL_THRESHOLD = 0.015  # 1.5% daily portfolio volatility
CB_PORTFOLIO_VOL_LOOKBACK = 20  # Days for volatility calculation

# Level 4: Correlation Circuit Breaker
# If correlation between positions exceeds threshold, reduce exposure
CB_CORRELATION_THRESHOLD = 0.60  # Correlation > 60%
CB_CORRELATION_REDUCTION = 0.50  # Reduce exposure by 50%

# Level 5: Greeks Breach Circuit Breaker (for Options Engine)
# Thresholds for options risk monitoring
CB_DELTA_MAX = 0.80  # Max delta exposure per position
CB_GAMMA_WARNING = 0.05  # Gamma warning threshold near expiry
CB_VEGA_MAX = 0.50  # Max vega exposure
CB_THETA_WARNING = -0.02  # Daily theta decay warning (-2%)
# Keep intraday lanes (ITM/MICRO) on sovereign exit rails by default.
# Global Greeks CB is calibrated for non-intraday single-leg risk.
CB_GREEKS_INCLUDE_INTRADAY = False
# V12.22: Keep crash hedge (PROTECTIVE_PUTS) under sovereign MICRO exits.
# 0-2 DTE hedge theta/gamma is expected and should not trigger global CB exits.
CB_GREEKS_INCLUDE_PROTECTIVE_PUTS = False
# ITM anti-churn guard after Greeks-breach forced close (RISK source).
ITM_RISK_EXIT_COOLDOWN_MINUTES = 120
# V2.3: Disable theta check for swing mode (5-45 DTE) - theta decay is expected
# and acceptable for longer-dated options where we have time to recover
CB_THETA_SWING_CHECK_ENABLED = False  # Set to True to enforce -2% theta limit on swing

# =============================================================================
# OPTIONS ENGINE (V2.1.1) - DUAL-MODE ARCHITECTURE
# =============================================================================
# V2.1.1 Complete Redesign: Two distinct modes based on DTE
# - SWING MODE (5-45 DTE): 75% of options budget, spread strategies, macro regime
# - INTRADAY MODE (0-2 DTE): 25% of options budget, Micro Regime Engine

# Underlying Symbol
OPTIONS_UNDERLYING = "QQQ"

# -----------------------------------------------------------------------------
# V6.20 DUAL-MODE ALLOCATION (50% total, 75/25 split)
# -----------------------------------------------------------------------------
OPTIONS_TOTAL_ALLOCATION = 0.60  # 60% total options budget (VASS 35% + ITM 15% + OTM 10%)
OPTIONS_SWING_ALLOCATION = 0.35  # 35% reserved for VASS swing engine
OPTIONS_INTRADAY_ALLOCATION = 0.25  # 25% reserved for intraday options engines (ITM+OTM)

# V2.7: Tiered Options Dollar Caps
# Prevents oversizing on small accounts while allowing growth on larger accounts
# Uses min(percentage_allocation, dollar_cap) logic
OPTIONS_DOLLAR_CAP_TIER_1_THRESHOLD = 60_000  # Below this: Tier 1 cap applies
OPTIONS_DOLLAR_CAP_TIER_2_THRESHOLD = 100_000  # Below this: Tier 2 cap applies
OPTIONS_DOLLAR_CAP_TIER_1 = 5_000  # Max $5K per spread when equity < $60K
OPTIONS_DOLLAR_CAP_TIER_2 = 10_000  # Max $10K per spread when equity $60K-$100K
# Above $100K: No dollar cap, use raw percentage-based sizing

# -----------------------------------------------------------------------------
# V2.8: VASS (Volatility-Adaptive Strategy Selection)
# Dynamically selects spread strategy based on IV environment
# -----------------------------------------------------------------------------
VASS_ENABLED = True  # Master switch for VASS

# IV Environment Classification Thresholds
# V6.6: Adjusted based on 2022H1 VIX distribution (16.6-32.0 range observed)
VASS_IV_LOW_THRESHOLD = 18  # V12.10: broaden low-IV routing window for debit-friendly tape.
VASS_IV_HIGH_THRESHOLD = 25  # V12.16: require true high-IV before routing VASS into credit spreads
VASS_IV_SMOOTHING_MINUTES = 30  # SMA window to prevent strategy flickering
# V12.10: Align VASS routing with chain IV rank when available.
# Fallback remains VIX-threshold routing when chain percentile isn't ready.
VASS_ROUTE_USE_CHAIN_IV_RANK = True
VASS_ROUTE_IV_RANK_LOW = 35.0
VASS_ROUTE_IV_RANK_HIGH = 65.0

# DTE Ranges by IV Environment (Swing Mode)
VASS_LOW_IV_DTE_MIN = 30  # Low IV: Monthly expiration
VASS_LOW_IV_DTE_MAX = 45
VASS_MEDIUM_IV_DTE_MIN = (
    21  # V12.9 P1: shift medium-IV debit spreads out of theta-accelerating window.
)
VASS_MEDIUM_IV_DTE_MAX = 45  # V12.9 P1: allow more time for thesis realization in medium IV.
# V6.6: Widened HIGH IV DTE range - 36 spread failures in 2022H1 due to narrow 7-14 window
VASS_HIGH_IV_DTE_MIN = 21  # V12.27: shift high-IV credit entries out of gamma-heavy short-DTE zone
VASS_HIGH_IV_DTE_MAX = 45  # Standard theta-friendly credit runway for VASS high-IV routes
VASS_DTE_FALLBACK_MAX = (
    60  # V12.27: config-driven fallback cap (avoids hardcoded 45 clipping when ranges evolve).
)

# V5.3: VASS Conviction Engine (VIX Direction Tracking)
# VASS tracks weekly (5d) and monthly (20d) VIX to determine conviction
VASS_VIX_5D_PERIOD = 5  # Weekly VIX lookback (days)
VASS_VIX_20D_PERIOD = 20  # Monthly VIX lookback (days)

# Conviction Thresholds (% change triggers override of Macro)
VASS_VIX_5D_BEARISH_THRESHOLD = (
    0.20  # V10.35: restore bearish conviction floor after V10.33 overshoot
)
VASS_VIX_5D_BULLISH_THRESHOLD = -0.20  # VIX 5d change < -20% → BULLISH conviction
VASS_VIX_20D_STRONG_BEARISH = 0.30  # VIX 20d change > +30% → STRONG BEARISH
VASS_VIX_20D_STRONG_BULLISH = -0.20  # VIX 20d change < -20% → STRONG BULLISH
# V12.0: Bearish conviction requires both velocity and absolute stress floor.
VASS_VIX_BEARISH_VETO_MIN_LEVEL = 18.0
VASS_VIX_BEARISH_VETO_5D_MIN_CHANGE = 0.25
VASS_LOW_VIX_CONVICTION_RELAX_ENABLED = True  # V10.8: broaden conviction in calm low-vol tapes
VASS_LOW_VIX_CONVICTION_MAX_VIX = 16.0
VASS_LOW_VIX_5D_CHANGE_THRESHOLD = 0.08  # V10.16: improve low-VIX conviction activation
VASS_LOW_VIX_CROSSING_DELTA = 0.80  # loosen level-crossing barriers by absolute VIX points
VASS_EARLY_STRESS_BULL_REQUIRE_CONVICTION = (
    True  # D8: In EARLY_STRESS, block bullish VASS unless conviction is present
)
VASS_EARLY_STRESS_BULL_STRATEGY_TO_CREDIT = (
    False  # V12.16: avoid forced bullish credit remap in unstable early-stress tape
)
VASS_EARLY_STRESS_BEAR_PREFER_CREDIT = (
    False  # V10.10: disable broad BEAR_CALL_CREDIT remix; keep explicit fallback path only
)
VASS_MEDIUM_IV_PREFER_CREDIT = (
    False  # V12.6: medium-IV defaults to debit structures to reduce bad credit exposure
)
VASS_BEAR_HIGH_IV_PREFER_DEBIT_ENABLED = True  # V12.30: in strong bear tape, prefer BEAR_PUT_DEBIT over BEAR_CALL_CREDIT even in HIGH IV.
VASS_BEAR_HIGH_IV_PREFER_DEBIT_REGIME_MAX = (
    45.0  # Apply high-IV bearish debit pivot only when macro regime is clearly bearish.
)
VASS_OPPOSITE_ROUTE_FALLBACK_ENABLED = False  # V12.18: disable opposite-route retry to avoid cross-route gate collisions and preserve signal intent.
VASS_OPPOSITE_ROUTE_BLOCK_ON_STRUCTURAL_FAIL = (
    True  # V12.9 P0: skip opposite-route fallback when primary failure is structural EV/quality.
)
VASS_ROUTE_MAX_CANDIDATE_ATTEMPTS = 3  # V10.17: evaluate top-N candidates before final rejection
VASS_SIMILAR_ENTRY_MIN_GAP_MINUTES = 15  # Block repeated same-signature entries in burst windows
VASS_SIMILAR_ENTRY_COOLDOWN_DAYS = (
    1  # V12.25: loosen non-quality throttle to increase valid bullish throughput
)
VASS_SIMILAR_ENTRY_USE_EXPIRY_BUCKET = True  # Use expiry date bucket (fallback to DTE bucket)
VASS_DIRECTION_MIN_GAP_ENABLED = True  # V12.6: prefer time-gap lock over strict day lock
VASS_DIRECTION_MIN_GAP_MINUTES = 180  # Min spacing between same-direction VASS entries
VASS_DIRECTION_MIN_GAP_MINUTES_BULLISH = (
    60  # V12.25: increase bull throughput while preserving bearish pacing
)
VASS_DIRECTION_MIN_GAP_MINUTES_BEARISH = 180  # Keep bearish spacing conservative
VASS_DIRECTION_DAY_GAP_ENABLED = False  # Legacy date-level lock kept disabled by default
VASS_ENTRY_ENGINE_ENABLED = True  # V10.10: route VASS strategy/filter/guards via dedicated engine
VASS_SCAN_INTERVAL_MINUTES = 5  # V12.31: intraday VASS scan throttle; consumed only when scan reaches candidate-contract evaluation.
VASS_USE_CONVICTION_ONLY_DIRECTION = (
    False  # V12.26: macro-only VASS direction mode; conviction remains telemetry only.
)
VASS_NO_CONVICTION_NO_TRADE = False  # V12.26: allow macro-following VASS entries when conviction absent; downstream gates still apply.
VASS_NEUTRAL_FALLBACK_DIRECTION_ENABLED = True  # V12.30: in macro NEUTRAL, infer VASS direction from transition-score delta to reduce resolver starvation.
VASS_NEUTRAL_FALLBACK_DELTA_MIN = 1.0  # Minimum transition-score delta (points) required to infer BULLISH/BEARISH direction in neutral macro.
VASS_NEUTRAL_FALLBACK_DELTA_SOFT_MIN = 0.5  # V12.31: softer neutral-delta inference tier to reduce resolver starvation without bypassing quality gates.
VASS_NEUTRAL_FALLBACK_ALLOWED_OVERLAYS = (
    "STABLE",
    "DETERIORATION",
    "RECOVERY",
)  # Restrict neutral fallback inference to non-chaotic overlays.
VASS_NEUTRAL_FALLBACK_DEEP_BEAR_MAX = 45.0  # V12.30: when regime <= this, block BULLISH inference from neutral fallback to preserve BEAR_PUT pivot access.
VASS_NEUTRAL_DIRECTION_MEMORY_ENABLED = True  # V12.31: when macro remains neutral and delta signal is weak, allow short-lived direction memory fallback.
VASS_NEUTRAL_DIRECTION_MEMORY_MAX_MINUTES = (
    120  # Memory fallback validity window from most recent VASS direction entry stamp.
)
VASS_NEUTRAL_STABLE_BEAR_RESCUE_ENABLED = True  # V12.37: in stable neutral tape, allow level-aware bearish rescue for slow-grind bear markets.
VASS_NEUTRAL_STABLE_BEAR_SCORE_MAX = 49.0  # Only rescue bearish VASS when stable neutral score is already on the bearish side of neutral.
VASS_NEUTRAL_STABLE_BEAR_MOMENTUM_MAX = (
    -0.008  # Require still-negative momentum before rescuing bearish VASS in stable neutral tape.
)
VASS_STRESS_BEAR_RESCUE_ENABLED = True  # V12.33: in STRESS, let strongly negative transition delta rescue bearish VASS routing from lagging bullish macro.
VASS_STRESS_BEAR_RESCUE_DELTA_MIN = 0.5  # Minimum negative transition delta required to bias VASS bearish inside bullish+STRESS dead zones.
VASS_STRESS_BEAR_RESCUE_SCORE_MAX = (
    65.0  # Only rescue bearish VASS when the transition score is still only mildly bullish.
)
VASS_DETERIORATION_BEAR_HANDOFF_ENABLED = True  # V12.37: when bullish VASS is blocked by deterioration, allow a bearish handoff with extra confirmation.
VASS_DETERIORATION_BEAR_HANDOFF_DELTA_MIN = 0.5  # Minimum negative transition delta required to hand off blocked bullish VASS into bearish routing.
VASS_DETERIORATION_BEAR_HANDOFF_SCORE_MAX = (
    62.0  # Only hand off while the effective score is still only mildly bullish.
)
VASS_DETERIORATION_BEAR_HANDOFF_MOMENTUM_MAX = (
    -0.008  # Require still-negative momentum before converting a deterioration-blocked bullish VASS setup into bearish.
)
VASS_BEARISH_FALLBACK_TO_BEAR_CALL_CREDIT = (
    False  # V10.10 tuning: disable bearish credit fallback while BEAR_PUT gating is rebalanced
)
VASS_BEAR_CREDIT_STABILITY_GATE_ENABLED = (
    True  # V12.30: require stable bearish transition context before BEAR_CALL_CREDIT entries.
)
VASS_BEAR_CREDIT_ALLOWED_OVERLAYS = (
    "DETERIORATION",
    "EARLY_STRESS",
    "STRESS",
)  # Reject BEAR_CALL_CREDIT in RECOVERY/NORMAL/AMBIGUOUS overlays.
VASS_BEAR_CREDIT_MIN_DETERIORATION_BARS = (
    2  # Require at least N bars in DETERIORATION before BEAR_CALL_CREDIT entry.
)
VASS_EV_PRE_GATE_ENABLED = False  # V12.11: disabled — D/W cap is the universal cost gate; IV rank pre-gate is redundant and miscalibrated in low-VIX.
VASS_EV_PRE_BULL_REGIME_MIN = (
    52.0  # V12.10: allow neutral-bull tape to participate in bull debit entries.
)
VASS_EV_PRE_BEAR_REGIME_MAX = 40.0  # Block BEAR debit construction above this regime ceiling.
VASS_EV_PRE_DEBIT_IV_RANK_MAX = 55.0  # Block debit construction when IV rank is too expensive.
VASS_EV_DIAGNOSTICS_ENABLED = (
    True  # V12.9 P2: log shortlist EV diagnostics for selected candidates.
)
VASS_EV_DIAGNOSTICS_TOP_N = 3
VASS_SHORT_LEG_SORT_POP_FIRST = (
    True  # V12.10: prioritize higher PoP structures before cheapest debit.
)
VASS_POP_GATE_ENABLED = False  # V12.12: disabled — PoP ≈ 1 - D/W for debit verticals, so D/W cap IS the PoP gate. Threshold 0.55 is mathematically impossible at long delta < 0.55 (blocks all OTM entries). Was True.
VASS_POP_MIN_DEBIT = 0.55
VASS_POP_MIN_CREDIT = 0.55
VASS_GREEKS_ENTRY_GATE_ENABLED = False  # V12.18: monitor via telemetry while D/W and C/W remain the hard economic quality filters.
VASS_DEBIT_MAX_THETA_TO_DEBIT = 0.08  # Max daily theta burn as fraction of net debit.
VASS_DEBIT_MAX_VEGA_TO_DEBIT = 0.70  # Max absolute net vega as fraction of net debit.
VASS_CREDIT_MIN_NET_THETA = -999.0  # Disabled: replaced by ratio-based gate below.
VASS_CREDIT_THETA_RATIO_GATE_ENABLED = True  # V12.10: ratio-based theta gate scales with credit.
VASS_CREDIT_MIN_NET_THETA_RATIO = -0.03  # Allow up to 3% of credit as daily theta cost.
VASS_CREDIT_MAX_VEGA_TO_CREDIT = 1.20  # Max absolute net vega as fraction of credit received.

# V12.21: D/W-aware pair search — try N candidates inside selection before returning.
# Avoids wasting retry attempts and triggering cooldowns on D/W-resolvable failures.
VASS_MAX_LONG_LEG_CANDIDATES = 5  # Debit: iterate up to 5 long legs by delta proximity
VASS_MAX_SHORT_LEG_CANDIDATES = 5  # Credit: iterate up to 5 short legs by bid descending

# V12.7: Universal adaptive VASS policy (fully reversible via VASS_EXIT_POLICY_MODE).
# LEGACY      -> preserve historical behavior.
# THESIS_FIRST -> regime-confirmed no-stop mode with regime-break exits as stop replacement.
VASS_EXIT_POLICY_MODE = "THESIS_FIRST"
VASS_REGIME_CONFIRMED_NO_STOP = True
VASS_REGIME_CONFIRMED_BULL_MIN = 57.0  # Disable mark stops when bullish spread and regime >= this
VASS_REGIME_CONFIRMED_BEAR_MAX = 43.0  # Disable mark stops when bearish spread and regime <= this
VASS_REGIME_BREAK_EXIT_ENABLED = True
VASS_REGIME_BREAK_BULL_FLOOR = 50.0  # Close bullish spreads when regime falls below this
VASS_REGIME_BREAK_BEAR_CEILING = 50.0  # Close bearish spreads when regime rises above this
VASS_REGIME_BREAK_BEAR_CEILING_CREDIT_BUFFER = (
    1.0  # V12.36: avoid flicker exits on bearish credits just above the 50 ceiling.
)
VASS_REGIME_CONFIRMED_PROFIT_TARGET_PCT = (
    0.55  # V12.23 C1: lower THESIS_FIRST confirmed target to improve realization.
)
VASS_REGIME_CONFIRMED_PROFIT_TARGET_PCT_DEBIT = 0.35  # V12.30: restore V12.24-style BULL_CALL_DEBIT harvest geometry to recover high-WR profile.
VASS_REGIME_CONFIRMED_PROFIT_TARGET_PCT_CREDIT = (
    0.50  # V12.27: harvest confirmed credits at 50% to reduce late-cycle gamma drift.
)
VASS_REGIME_CONFIRMED_DTE_EXIT = 2  # Confirmed conviction mode: hold until 2 DTE
VASS_REGIME_CONFIRMED_DTE_EXIT_DEBIT = 2  # Debit spreads can hold longer in confirmed mode.
VASS_REGIME_CONFIRMED_DTE_EXIT_CREDIT = (
    7  # Credit spreads: reduce early gamma churn while still exiting before expiry zone.
)
VASS_REGIME_CONFIRMED_DISABLE_DEBIT_MARK_STOP = True
VASS_REGIME_CONFIRMED_DISABLE_DEBIT_TRAIL = True
VASS_REGIME_CONFIRMED_DISABLE_DEBIT_MFE_T1 = (
    True  # V12.23.2: T1 breakeven floor contradicts confirmed thesis; T2 still active
)
VASS_REGIME_CONFIRMED_DISABLE_CREDIT_MARK_STOP = False
VASS_REGIME_CONFIRMED_DISABLE_CREDIT_TRAIL = False
VASS_ENABLE_MARK_STOP_EXITS = True  # Runtime-gated by regime confirmation in exit evaluator
VASS_ENABLE_TAIL_CAP_EXITS = True  # Runtime-gated by regime confirmation in exit evaluator
VASS_ENABLE_TRAIL_PROFIT_EXITS = True  # Runtime-gated by regime confirmation in exit evaluator
VASS_ENABLE_PROFIT_TARGET_EXITS = True  # Master allow for profit-target exits
VASS_PROFIT_TARGET_OPEN_DELAY_MINUTES = (
    5  # BR-08: suppress VASS spread profit-target exits during opening quote-discovery window.
)
VASS_ENABLE_MFE_LOCK_EXITS = True  # Runtime-gated by regime confirmation in exit evaluator
VASS_MFE_LOCK_IN_REGIME_CONFIRMED = (
    True  # Keep MFE lock active in confirmed mode to prevent large winner giveback.
)
VASS_ENABLE_NEUTRALITY_EXITS = False  # V12.25: disable neutrality exits for thesis-first VASS flow
VASS_ENABLE_DAY4_EOD_EXITS = True  # Runtime-gated by regime confirmation in exit evaluator

# V12.27: Credit THETA_FIRST scaffolding (behavior wired in exit/risk modules).
# Mirrors debit THESIS_FIRST philosophy for credit spreads: reduce tactical churn,
# keep emergency rails, and allow theta to work.
VASS_CREDIT_THETA_FIRST_ENABLED = True
VASS_CREDIT_THETA_FIRST_REQUIRE_REGIME_CONFIRMED = True
VASS_CREDIT_THETA_FIRST_SUPPRESS_MARK_STOP = True
VASS_CREDIT_THETA_FIRST_SUPPRESS_CONVICTION_FLOOR = True
VASS_CREDIT_THETA_FIRST_SUPPRESS_MFE_T1 = True
VASS_CREDIT_THETA_FIRST_MIN_HOLD_MINUTES = 390  # ~1 trading session
VASS_CREDIT_THETA_FIRST_SUPPRESS_FRIDAY_FIREWALL_DTE_GT = 21
VASS_CREDIT_THETA_FIRST_OGP_VIX_CLOSE_ALL_MIN = 40.0
# V12.31: In credit THETA_FIRST, treat tail-cap as emergency-only.
# Normal post-hold risk control should come from CREDIT_STOP_2X.
VASS_CREDIT_THETA_FIRST_TAIL_CAP_ONLY_EMERGENCY = True
VASS_CREDIT_THETA_FIRST_TAIL_CAP_EMERGENCY_DTE_MAX = 14
VASS_CREDIT_THETA_FIRST_TAIL_CAP_EMERGENCY_LOSS_PCT = 0.70

# Level Crossing Thresholds (regime shift signals)
VASS_VIX_FEAR_CROSS_LEVEL = 23  # VIX crosses above this → BEARISH
VASS_VIX_COMPLACENT_CROSS_LEVEL = 14  # VIX crosses below this → BULLISH

# Credit Spread Constraints
CREDIT_SPREAD_MIN_CREDIT = 0.20  # V6.10 P3: Was 0.30, lowered to allow more fills
CREDIT_SPREAD_WIDTH_TARGET = 5.0  # $5 width for credit spreads
CREDIT_SPREAD_DTE_MIN = 21  # V12.27: avoid short-DTE gamma zone for credit entries
CREDIT_SPREAD_DTE_MAX = 45  # V12.27: align with theta-first credit hold horizon
CREDIT_SPREAD_DTE_EXIT = 7  # Credit-only DTE close threshold (debit keeps SPREAD_DTE_EXIT).
CREDIT_SPREAD_FALLBACK_TO_DEBIT = True  # V6.10 P3: Fall back to debit when credit fails
CREDIT_SPREAD_PROFIT_TARGET = 0.50  # V12.27: standard 50% credit harvest target
CREDIT_SPREAD_STOP_MULTIPLIER = 0.35  # V10.17: trim left-tail bleed on failed credit spreads
CREDIT_SPREAD_TIERED_STOP_ENABLED = True
CREDIT_SPREAD_STOP_MULT_LOW_VIX = 0.30
CREDIT_SPREAD_STOP_MULT_MED_VIX = 0.32  # V12.16: tighter realized-loss cap in medium-IV credit tape
CREDIT_SPREAD_STOP_MULT_HIGH_VIX = 0.35  # V12.16: tighten high-IV left-tail containment
# V12.27: Credit stop mode toggle (legacy percent-of-max-loss vs 2x-credit model).
CREDIT_SPREAD_STOP_MODE = "TWO_X_CREDIT"  # LEGACY | TWO_X_CREDIT
CREDIT_SPREAD_STOP_2X_MULTIPLIER = 2.0
CREDIT_SPREAD_MAX_LOSS_PCT_EQUITY = (
    0.0125  # V10.17: cap theoretical max-loss sizing to 1.25% equity
)
CREDIT_SPREAD_SHORT_LEG_DELTA_MIN = 0.25  # Short leg delta range (OTM)
CREDIT_SPREAD_SHORT_LEG_DELTA_MAX = 0.45  # V6.13 OPT: Improve credit spread constructability
CREDIT_SPREAD_SHORT_LEG_DELTA_MAX_HIGH_VIX = (
    0.40  # V12.33: partially unwind high-IV tightening so quality credits remain constructable.
)
CREDIT_SPREAD_SHORT_LEG_HIGH_VIX_THRESHOLD = 25.0
BULL_PUT_CREDIT_MIN_VIX_FOR_ENTRY = 18.0  # V12.33: below this, route bullish spreads to debits instead of probing low-vol put credits.
# V12.29: credit-risk exit guard for short-strike pressure near expiry.
CREDIT_SPREAD_DELTA_EXIT_ENABLED = True
CREDIT_SPREAD_DELTA_EXIT_THRESHOLD = 0.30
CREDIT_SPREAD_DELTA_EXIT_DTE_MAX = 14
# T-21: Credit-path liquidity quality gates (parity with debit selector).
CREDIT_SPREAD_MIN_OPEN_INTEREST = 20  # V6.15 TUNE: Improve credit spread constructability
CREDIT_SPREAD_MAX_SPREAD_PCT = 0.50  # V6.15 TUNE: Loosen spread-quality gate moderately
CREDIT_SPREAD_LONG_LEG_MAX_SPREAD_PCT = (
    0.70  # V6.15 TUNE: Allow long-leg protection in thinner tails
)

# V2.24.1: Elastic Delta Bands — progressive widening when no candidates found
# Each step widens the delta range by ± the step value (e.g., [0.0, 0.03, 0.07, 0.12])
# Step 0 = original range, Step 1 = ±0.03 wider, etc.
# Capped at ELASTIC_DELTA_FLOOR (min delta) and ELASTIC_DELTA_CEILING (max delta)
ELASTIC_DELTA_STEPS = [0.0, 0.05, 0.10, 0.15]  # V6.13.1 OPT: More aggressive widening
ELASTIC_DELTA_FLOOR = 0.10  # Never search below this delta (too far OTM)
ELASTIC_DELTA_CEILING = 0.95  # Never search above this delta (deep ITM)

# V2.25: IV-adaptive credit floor — lower min credit in high IV to allow fills
# Q1 2022 audit: 116 VASS rejections at VIX > 30 because $0.30 floor filtered all candidates
CREDIT_SPREAD_MIN_CREDIT_HIGH_IV = 0.10  # V6.13.1 OPT: More credit spread fills (was 0.20)
CREDIT_SPREAD_HIGH_IV_VIX_THRESHOLD = 30.0  # VIX level above which reduced floor applies
# V9.2: Structural credit quality floor (prevents low-credit, high-max-loss structures)
# Three-tier system: strict in calm markets, relaxed as VIX rises and credit widens
CREDIT_SPREAD_MIN_CREDIT_TO_WIDTH_PCT = 0.40  # V12.16: raise calm-tape credit quality floor
CREDIT_SPREAD_MIN_CREDIT_TO_WIDTH_PCT_MEDIUM_IV = (
    0.38  # V12.16: raise medium-IV credit quality floor
)
CREDIT_SPREAD_MIN_CREDIT_TO_WIDTH_PCT_HIGH_IV = (
    0.35  # V12.16: raise high-IV floor to stabilize credit R:R
)
BEAR_CALL_CREDIT_MIN_CREDIT_TO_WIDTH_PCT = (
    0.35  # V12.33: flatter call skew supports slightly lower calm-tape C/W for bear-call credits.
)
BEAR_CALL_CREDIT_MIN_CREDIT_TO_WIDTH_PCT_MEDIUM_IV = (
    0.33  # V12.33: medium-IV bear-call credits can remain attractive below put-credit floors.
)
BEAR_CALL_CREDIT_MIN_CREDIT_TO_WIDTH_PCT_HIGH_IV = (
    0.30  # V12.33: high-IV bear-call credits need lower C/W floor than bullish put credits.
)
CREDIT_SPREAD_MEDIUM_IV_VIX_THRESHOLD = 20.0  # VIX level for medium-IV tier

# V2.3.14: Intraday trade limits (was 1, blocking all re-entries after first trade)
# V2.3.15: Sniper Logic - allow one retry, not machine gun
INTRADAY_MAX_TRADES_PER_DAY = 4  # Shared intraday cap (disabled by default for engine isolation)
INTRADAY_ENFORCE_SHARED_DAILY_CAP = False
INTRADAY_ENFORCE_SHARED_DIRECTION_CAP = False
ITM_MAX_TRADES_PER_DAY = 4
MICRO_MAX_TRADES_PER_DAY = 6
INTRADAY_MAX_TRADES_PER_DIRECTION_PER_DAY = (
    2  # Hard cap per intraday direction (CALL/PUT) to curb same-side churn.
)
MICRO_SAME_STRATEGY_COOLDOWN_MINUTES = (
    20  # Block immediate repeat of same MICRO strategy after close
)

# V2.9: Global options trade limits (Bug #4 fix)
# Keep conservative defaults to avoid fee drag/correlation spikes while
# execution plumbing is still being hardened.
MAX_OPTIONS_TRADES_PER_DAY = 5
MAX_SWING_TRADES_PER_DAY = 3
# V12.20: Engine-sovereign daily caps are primary. Keep global daily cap optional.
# When disabled, ITM/MICRO/VASS lane caps and per-engine daily caps govern throughput.
OPTIONS_ENFORCE_GLOBAL_DAILY_CAP = False
# V12.17: Portfolio-growth scaling (capacity/throughput only, R:R unchanged).
# Disabled by default; when enabled, engine slot and daily-trade ceilings scale
# by portfolio equity tier to prevent large-account under-deployment.
OPTIONS_PORTFOLIO_SCALING_ENABLED = True
OPTIONS_PORTFOLIO_SCALING_TIERS = (
    {
        "name": "BASE_100K",
        "max_equity": 100_000,
        "total_positions": 7,
        "max_swing_positions": 4,
        "vass_concurrent": 3,
        "itm_concurrent": 1,
        "micro_concurrent": 1,
        "max_options_trades_per_day": 5,
        "max_swing_trades_per_day": 3,
        "itm_max_trades_per_day": 4,
        "micro_max_trades_per_day": 6,
        "intraday_max_contracts": 50,
    },
    {
        "name": "GROWTH_200K",
        "max_equity": 200_000,
        "total_positions": 8,
        "max_swing_positions": 5,
        "vass_concurrent": 3,
        "itm_concurrent": 1,
        "micro_concurrent": 2,
        "max_options_trades_per_day": 7,
        "max_swing_trades_per_day": 4,
        "itm_max_trades_per_day": 5,
        "micro_max_trades_per_day": 7,
        "intraday_max_contracts": 60,
    },
    {
        "name": "GROWTH_350K",
        "max_equity": 350_000,
        "total_positions": 10,
        "max_swing_positions": 6,
        "vass_concurrent": 3,
        "itm_concurrent": 2,
        "micro_concurrent": 2,
        "max_options_trades_per_day": 9,
        "max_swing_trades_per_day": 5,
        "itm_max_trades_per_day": 6,
        "micro_max_trades_per_day": 8,
        "intraday_max_contracts": 75,
    },
    {
        "name": "SCALE_350K_PLUS",
        "max_equity": None,
        "total_positions": 12,
        "max_swing_positions": 8,
        "vass_concurrent": 4,
        "itm_concurrent": 2,
        "micro_concurrent": 3,
        "max_options_trades_per_day": 12,
        "max_swing_trades_per_day": 6,
        "itm_max_trades_per_day": 8,
        "micro_max_trades_per_day": 10,
        "intraday_max_contracts": 100,
    },
)
# Reserve swing capacity so intraday activity cannot fully starve VASS entries.
OPTIONS_RESERVE_SWING_DAILY_SLOTS_ENABLED = True
OPTIONS_MIN_SWING_SLOTS_PER_DAY = 1
OPTIONS_RESERVE_INTRADAY_DAILY_SLOTS_ENABLED = False
OPTIONS_MIN_INTRADAY_SLOTS_PER_DAY = 1
OPTIONS_RESERVE_RELEASE_HOUR = 12  # Release reserved slots earlier to reduce midday throttling
OPTIONS_RESERVE_RELEASE_MINUTE = 30
# V12.15: Intraday lane fairness guard.
# Prevents scan-order starvation when one intraday lane consumes the global daily budget
# before the other lane has taken its minimum opportunities.
INTRADAY_ENGINE_DAILY_RESERVE_ENABLED = True
INTRADAY_MIN_ITM_TRADES_RESERVED = 1
INTRADAY_MIN_MICRO_TRADES_RESERVED = 1
# V12.23.2: Block entry when candidate leg strike overlaps an active spread's opposite leg.
VASS_STRIKE_REUSE_GUARD_ENABLED = True
# Replace one-attempt-per-day spread lock with scoped attempt budgets.
SPREAD_MAX_ATTEMPTS_PER_KEY_PER_DAY = 3
SPREAD_ATTEMPT_COUNT_ON_VALIDATION_FAILURE = (
    False  # V12.10: count only material entry attempts; avoids validation-churn budget burn.
)
SPREAD_ATTEMPT_DEDUPE_PER_MINUTE = (
    True  # V12.9: prevent same-minute retry loops from over-consuming attempt budget.
)

# Legacy compatibility (combined min/max)
OPTIONS_ALLOCATION_MIN = 0.50  # V6.20: 50% minimum (isolation profile)
OPTIONS_ALLOCATION_MAX = 0.50  # V6.20: 50% maximum (isolation profile)

# Entry Score Thresholds
OPTIONS_ENTRY_SCORE_MIN = (
    2.20  # Relaxed to recover quality throughput without reopening low-conviction noise
)
OPTIONS_ADX_THRESHOLD = 25  # ADX >= 25 for full score

# Entry Score Component Weights (each 0-1, total 0-4)
# ADX Factor
OPTIONS_ADX_WEAK = 20  # ADX < 20 → 0.25
OPTIONS_ADX_MODERATE = 25  # ADX 20-25 → 0.50
OPTIONS_ADX_STRONG = 35  # ADX 25-35 → 0.75, >= 35 → 1.0

# Momentum Factor (price relative to MA200)
OPTIONS_MOMENTUM_MA_PERIOD = 200

# IV Rank Factor
OPTIONS_IV_RANK_LOW = 20  # IV rank < 20 → 0.25
OPTIONS_IV_RANK_HIGH = 80  # IV rank > 80 → 0.25
# IV rank 20-80 → full score
OPTIONS_IV_RANK_USE_CHAIN_PERCENTILE = (
    True  # V12.9 P1: use chain-implied-vol percentile before falling back to VIX proxy.
)
OPTIONS_IV_RANK_HISTORY_DAYS = 126  # ~6 months trading days for rolling IV-rank window.
OPTIONS_IV_RANK_MIN_SAMPLES = 20
# V12.10: strategy-specific IV scoring profiles for VASS.
OPTIONS_IV_SCORE_PROFILE_ENABLED = True
OPTIONS_IV_SCORE_DEBIT_LOW = 35.0
OPTIONS_IV_SCORE_DEBIT_HIGH = 55.0
OPTIONS_IV_SCORE_CREDIT_LOW = 45.0
OPTIONS_IV_SCORE_CREDIT_HIGH = 65.0

# Liquidity Factor
OPTIONS_SPREAD_MAX_PCT = 0.14  # Slightly wider filter to reduce unnecessary spread rejections
OPTIONS_SPREAD_WARNING_PCT = 0.30  # V6.8: Was 0.25, reduce spread-based rejection
OPTIONS_MIN_OPEN_INTEREST = 60  # Loosen OI floor to restore execution coverage

# Confidence-Weighted Tiered Stops
# V2.4.3 FIX: CORRECTED - Higher confidence = MORE contracts (was inverted!)
# Logic: Bet big on high-conviction signals, bet small on low-conviction
OPTIONS_STOP_TIERS = {
    # Low confidence = small position, tight stop (cut losses fast)
    # High confidence = larger position, wider stop (give it room)
    3.00: {"stop_pct": 0.15, "contracts": 5},  # Low confidence: -15% stop
    3.25: {"stop_pct": 0.18, "contracts": 8},  # Medium-low: -18% stop
    3.50: {"stop_pct": 0.22, "contracts": 10},  # Medium-high: -22% stop
    3.75: {"stop_pct": 0.25, "contracts": 12},  # High confidence: -25% stop
}

# V2.3.8: 0DTE-specific stop override (PART 14 Pitfall 2)
# 0DTE options move extremely fast - by time stop triggers, slippage can double the loss
# NOTE: StopMarketOrder fills at next available price after trigger, not the stop price
OPTIONS_0DTE_STOP_PCT = 0.25  # Legacy fallback when ATR stop cannot be computed
OPTIONS_0DTE_STATIC_STOP_OVERRIDE_ENABLED = (
    False  # V12.0: Keep ATR stops sovereign; disable fixed 0DTE override in normal flow
)

# =============================================================================
# V6.5: DELTA-SCALED ATR STOP CALIBRATION
# =============================================================================
# Dynamic stop based on underlying ATR × option delta
# Formula: stop_distance = ATR_MULTIPLIER × underlying_ATR × abs(option_delta)
# This gives more room in high-VIX environments while accounting for option sensitivity
#
# Example with QQQ ATR=$4, delta=0.45, multiplier=1.5:
#   stop_distance = 1.5 × $4 × 0.45 = $2.70 (in option price terms)
#   If entry=$3.50, stop=$0.80 (77% stop)

# ATR multiplier for stop calculation (Chandelier-style)
OPTIONS_ATR_STOP_MULTIPLIER = 2.5  # V12.0: ATR-first stop sizing for intraday options

# Floor and cap to prevent extreme stops
OPTIONS_ATR_STOP_MIN_PCT = 0.12  # V6.13 OPT: Tighter floor in calm conditions
OPTIONS_ATR_STOP_MAX_PCT = 0.45  # V10.8: uncap ATR stops for multi-day ITM hold profile

# Whether to use ATR-based stops (set False to use legacy tier-based stops)
OPTIONS_USE_ATR_STOPS = True

# Profit Target
OPTIONS_PROFIT_TARGET_PCT = 0.60  # +60% profit target (trend riding)

# V2.3.10: DTE Exit for Single-Leg Options (prevents exercise/expiration)
# Close single-leg options when DTE <= this value to avoid:
# - OTM expiring worthless (100% loss)
# - ITM being auto-exercised (creating stock position, margin crisis)
# V2.3.18: Raised from 2 to 4 DTE - gamma risk explodes in final week
# Single legs have undefined risk, should exit BEFORE spreads (5 DTE), not after
OPTIONS_SINGLE_LEG_DTE_EXIT = 4  # Close by 4 DTE (avoid expiration gamma trap)

# V2.3.11: EOD Force Close for Options Expiring TODAY
# Critical safety: Prevent auto-exercise of ITM options held into close
# ITM options held past 4 PM get auto-exercised → stock position → margin crisis
# Example from V2.3.9: 800 shares of QQQ assigned = $360K on $50K account (7:1 leverage)
# V2.4.2: Expiration Hammer - Moved from 3:45 PM to 2:00 PM
# V6.14 T-14 FIX: Moved from 2:00 PM to 12:00 PM (noon)
# T-14 Bug: 14 options sold @ $0.01 on expiry because 14:00 was too late
# Earlier close gives 4 hours buffer and avoids end-of-day volatility/low liquidity
OPTIONS_EXPIRING_TODAY_FORCE_CLOSE_HOUR = 12  # Force close expiring options at 12:00 noon
OPTIONS_EXPIRING_TODAY_FORCE_CLOSE_MINUTE = 0

# Protective puts can hold longer intraday to keep crash-hedge coverage alive.
# Applies only on expiration day (same-day expiry hedge contracts).
PROTECTIVE_PUTS_EXPIRING_TODAY_FORCE_CLOSE_HOUR = 15
PROTECTIVE_PUTS_EXPIRING_TODAY_FORCE_CLOSE_MINUTE = 15

# V2.28: Early exercise guard — force close ITM single-leg options near expiry
# Prevents costly early exercise (Q1 2022: 2 exercises cost -$5,614)
EARLY_EXERCISE_GUARD_DTE = 2  # Close if DTE <= 2 and ITM
EARLY_EXERCISE_GUARD_ITM_BUFFER = 0.01  # 1% ITM buffer (strike vs underlying)

# =============================================================================
# V5.3: ASSIGNMENT RISK MANAGEMENT (P0 Fixes from 2022 H1 Backtest)
# Apr 22-26 2022: Option assignments caused -$37K loss and margin cascade
# =============================================================================

# P0 Fix 1: Early Exit for Deep ITM Shorts
# Force exit when short leg becomes deep ITM before assignment happens
DEEP_ITM_EXIT_ENABLED = True
DEEP_ITM_EXIT_DTE_THRESHOLD = 3  # Exit if DTE <= 3 AND deep ITM
DEEP_ITM_EXIT_DELTA_THRESHOLD = 0.80  # Delta > 0.80 = deep ITM
DEEP_ITM_EXIT_INTRINSIC_PCT = 0.05  # Exit if intrinsic > 5% of underlying price

# P0 Fix 2: Assignment Detection & Prevention
# Block holding short ITM options overnight if DTE <= 2
OVERNIGHT_ITM_SHORT_BLOCK_ENABLED = True
OVERNIGHT_ITM_SHORT_DTE_THRESHOLD = 2  # Block overnight hold if DTE <= 2 and ITM
OVERNIGHT_ITM_SHORT_CHECK_TIME_HOUR = 15  # Check at 3:00 PM
OVERNIGHT_ITM_SHORT_CHECK_TIME_MINUTE = 0

# P0 Fix 3: Margin Buffer Before Assignment Risk
# Require extra margin when ITM short exposure exists
ASSIGNMENT_MARGIN_BUFFER_ENABLED = True
ASSIGNMENT_MARGIN_BUFFER_PCT = 0.20  # V6.9 P0: Restore safer buffer to prevent assignment losses
ASSIGNMENT_MARGIN_AUTO_REDUCE = True  # Auto-reduce if buffer insufficient

# P0 Fix 4: Partial Assignment Handling
# Detect partial assignment and auto-close remaining legs
PARTIAL_ASSIGNMENT_DETECTION_ENABLED = True
PARTIAL_ASSIGNMENT_AUTO_CLOSE = True  # Auto-close orphaned legs

# V6.9 P0 Fix 5: Short Leg ITM Exit (regardless of DTE)
# Exit spread immediately when short leg goes ITM by threshold
# This catches assignments at ANY DTE, not just near expiry
# Aug 2022 assignments happened at DTE=4, missed by DTE<=3 guards
SHORT_LEG_ITM_EXIT_ENABLED = True
SHORT_LEG_ITM_EXIT_THRESHOLD = (
    0.035  # Raised to reduce noise exits; trigger only on deeper ITM risk
)
# V12.28: debit spreads carry short-leg assignment risk near expiry.
# Enable debit checks only in danger zone (near expiry / near-zero extrinsic).
SHORT_LEG_ITM_EXIT_DEBIT_ENABLED = (
    False  # V12.29: disable broad debit assignment exits (BCD edge protection).
)
SHORT_LEG_ITM_EXIT_DEBIT_DTE_MAX = 2
SHORT_LEG_ITM_EXIT_DEBIT_EXTRINSIC_MAX = 0.05
SPREAD_ASSIGNMENT_GRACE_MINUTES = 45  # V6.15 FIX: Allow spread to stabilize before ITM checks
SPREAD_ASSIGNMENT_GRACE_MINUTES_CREDIT = 20  # V12.9 P1: stricter credit assignment response.
SHORT_LEG_ITM_EXIT_LOG_INTERVAL = 30  # Minutes between log messages
SPREAD_MIN_HOLD_MINUTES = 240  # V12.6: partial-day hold window to reduce forced late exits
SPREAD_HOLD_GUARD_SOFT_ENABLED = True
SPREAD_HOLD_GUARD_ALLOW_TRANSITION_BYPASS = True
SPREAD_HOLD_GUARD_SEVERE_STOP_MULTIPLIER = 1.10
SPREAD_HOLD_GUARD_LOSS_BYPASS_STOP_FRACTION = (
    0.50  # V12.6: allow severe losers to bypass hold guard earlier
)
SPREAD_EOD_GATE_MIN_HOLD_MINUTES = (
    240  # Don't fire EOD hold-risk gate until spread has had at least 4h to work
)
VASS_EOD_GATE_BLOCK_SAME_DAY_REENTRY = True
VASS_EOD_GATE_DIRECTION_COOLDOWN_MINUTES = (
    240  # Post EOD-gate directional cooldown to avoid same-day re-entry churn
)
SPREAD_EXIT_RETRY_MINUTES = (
    15  # V9.4 P0: Cooldown between exit signal retries (prevents per-minute spam)
)

# V6.10 P0: Mandatory DTE=1 Force Close (Nuclear Assignment Prevention)
# Close ALL spread positions when DTE reaches this value, regardless of P&L
# This is the last line of defense against overnight assignment risk
SPREAD_FORCE_CLOSE_DTE = 1  # Close at DTE=1 (day before expiry)
SPREAD_FORCE_CLOSE_ENABLED = True  # Master switch for mandatory close

# V6.10 P0: Pre-Market ITM Check (09:25 ET)
# Check all short legs before market open to catch overnight gaps
# If short leg went ITM overnight, queue for immediate close at 09:30
PREMARKET_ITM_CHECK_ENABLED = True  # Enable 09:25 pre-market check
PREMARKET_ITM_CREDIT_GUARD_ENABLED = True  # V12.27: guard closes unless DTE danger or low extrinsic
PREMARKET_ITM_CREDIT_DTE_MAX = 14  # Only force credit exits in assignment-prone zone
PREMARKET_ITM_CREDIT_EXTRINSIC_MAX = 0.15  # Force close when short-leg time value is minimal
PREMARKET_ITM_DEBIT_GUARD_ENABLED = (
    False  # V12.29: disable broad debit premarket ITM guard (credit-only path remains).
)
PREMARKET_ITM_DEBIT_DTE_MAX = 1
PREMARKET_ITM_DEBIT_EXTRINSIC_MAX = 0.05
PREMARKET_ITM_CHECK_HOUR = 9  # Check at 09:25 ET
PREMARKET_ITM_CHECK_MINUTE = 25

# V12.29: Narrow assignment-emergency backstop for BULL_CALL_DEBIT only.
# Preserve BCD edge while still preventing expiry-imminent short-call assignment blowups.
VASS_BCD_ASSIGNMENT_EMERGENCY_ENABLED = True
VASS_BCD_ASSIGNMENT_EMERGENCY_DTE_MAX = 1
VASS_BCD_ASSIGNMENT_EMERGENCY_EXTRINSIC_MAX = 0.03

# V6.14: Pre-market VIX shock ladder (shared guard across options modes)
# Uses CBOE VIX level + UVXY overnight gap proxy to de-risk before market open.
PREMARKET_VIX_LADDER_ENABLED = True

# Level triggers (higher level takes precedence)
# L3: Panic shock -> freeze new options entries and flatten all options risk
PREMARKET_VIX_L3_LEVEL = 35.0
PREMARKET_VIX_L3_GAP_PCT = 12.0  # Approx VIX gap via UVXY gap / 1.5

# L2: High stress -> block new CALLs and de-risk bullish options
PREMARKET_VIX_L2_LEVEL = 28.0
PREMARKET_VIX_L2_GAP_PCT = 7.0

# L1: Elevated -> reduce options size, no forced exits
PREMARKET_VIX_L1_LEVEL = 22.0
PREMARKET_VIX_L1_GAP_PCT = 4.0

# Entry windows after pre-market shock
PREMARKET_VIX_L2_CALL_BLOCK_UNTIL_HOUR = 11
PREMARKET_VIX_L2_CALL_BLOCK_UNTIL_MINUTE = 0
PREMARKET_VIX_L3_ENTRY_BLOCK_UNTIL_HOUR = 12
PREMARKET_VIX_L3_ENTRY_BLOCK_UNTIL_MINUTE = 0

# Size multipliers by level
PREMARKET_VIX_L1_SIZE_MULT = 0.75
PREMARKET_VIX_L2_SIZE_MULT = 0.50
PREMARKET_VIX_L3_SIZE_MULT = 0.25

# De-risk actions
PREMARKET_VIX_L2_CLOSE_BULLISH_OPTIONS = True
PREMARKET_VIX_L3_CLOSE_ALL_OPTIONS = True
PREMARKET_FORCE_CLOSE_INTRADAY_STALE = True

# V6.16: Overnight VIX shock memory (carry overnight panic context into early session)
# Prevents Micro/VASS from "resetting" at open after a large overnight VIX jump.
MICRO_SHOCK_MEMORY_ENABLED = True
MICRO_SHOCK_MEMORY_MIN_LADDER_LEVEL = 2  # Apply only on L2/L3 shock days
MICRO_SHOCK_MEMORY_UNTIL_HOUR = 13
MICRO_SHOCK_MEMORY_UNTIL_MINUTE = 0
MICRO_SHOCK_MEMORY_ANCHOR = 0.60  # 60% of overnight shock retained in effective open baseline
SHOCK_MEMORY_FORCE_BEARISH_VASS = True  # Force VASS bearish direction while shock memory is active

# P1 Fix 5: Assignment-Aware Position Sizing
# Reduce size if max short exposure exceeds safe margin
ASSIGNMENT_AWARE_SIZING_ENABLED = True
ASSIGNMENT_SIZING_MAX_EXPOSURE_PCT = 0.50  # Max 50% of portfolio for assignment risk

# P1 Fix 6: Assignment-Aware Exit Priority
# Close high-risk positions first when exiting
ASSIGNMENT_EXIT_PRIORITY_ENABLED = True

# V6.4: P0 Fix 7: Pre-Entry Assignment Risk Gate for BEAR_PUT Spreads
# Block BEAR_PUT entries when short PUT strike is too close to ATM or ITM
# This prevents opening positions with high assignment risk from the start
# For PUTs: ITM = strike > price, OTM = strike < price
# Example: If MIN_OTM_PCT = 0.03 and QQQ = $350, min short strike = $339.50
BEAR_PUT_ENTRY_GATE_ENABLED = True
BEAR_PUT_ENTRY_MIN_OTM_PCT = (
    0.015  # V10.10: relax slightly to reduce over-blocking of bearish debit entries
)
BEAR_PUT_ENTRY_LOW_VIX_THRESHOLD = 18.0  # Relax assignment gate in calmer IV environments
BEAR_PUT_ENTRY_MIN_OTM_PCT_RELAXED = (
    0.010  # V10.11: relax assignment gate to restore bearish debit access without removing guard
)
BEAR_PUT_ENTRY_RELAXED_REGIME_MIN = (
    60.0  # Require healthy regime before applying relaxed OTM threshold
)
# V6.22: During confirmed stress, allow tighter BEAR_PUT shorts to keep bearish access alive.
# V9.4: Lowered from 0.8% to 0.3%. Bear markets need PUT access most — max loss already capped by debit.
BEAR_PUT_ENTRY_MIN_OTM_PCT_STRESS = 0.005
BEAR_PUT_ASSIGNMENT_HARD_BLOCK_VIX = (
    35.0  # V12.7: relax hard block to preserve bearish access in moderate fear
)
BEAR_PUT_ASSIGNMENT_HARD_BLOCK_REGIME_MAX = (
    40.0  # Legacy (unused in V12.30+ assignment gate polarity)
)
BEAR_PUT_ASSIGNMENT_BULL_BLOCK_REGIME_MIN = (
    55.0  # Enforce short-PUT assignment gate in bullish/neutral-high regimes
)
BULL_PUT_CREDIT_ASSIGNMENT_BULL_REGIME_BLOCK_ENABLED = False  # V12.33: do not suppress bullish put-credit entries solely because macro regime is bullish
BEAR_PUT_ASSIGNMENT_RESELECT_ENABLED = (
    True  # On assignment-gate fail, retry with a farther OTM short PUT from current candidate pool
)
VASS_BEAR_PUT_STRESS_RELAX_ENABLED = True  # V12.30: keep BEAR_PUT access in true bear tape by softening STRESS-only assignment enforcement.
VASS_BEAR_PUT_STRESS_RELAX_REGIME_MAX = (
    45.0  # Apply STRESS relax only when macro regime is clearly bearish/defensive.
)
VASS_BEAR_PUT_STRESS_RELAX_VIX_MAX = (
    30.0  # Do not relax in extreme volatility; hard-VIX guard still dominates.
)
VASS_BEAR_FALLBACK_MAX_REGIME = 40.0  # V10.9: fallback only in clearly weak macro regimes
VASS_BEAR_FALLBACK_MIN_VIX = 20.0  # V10.17: require elevated fear before bearish credit fallback

# Contract Selection
# Options chain filter (must cover BOTH Intraday 0-2 DTE AND Swing 5-45 DTE)
OPTIONS_DTE_MIN = 0  # Minimum days to expiration (Intraday mode)
OPTIONS_DTE_MAX = (
    60  # V2.23: Extended for VASS Low IV monthly expirations (30-45 DTE needs 60-day horizon)
)
OPTIONS_MIN_PREMIUM = 0.50  # Minimum premium per contract ($0.50)

# V2.3.16: DTE-Based Delta Validation
# Intraday mode (DTE <= 5): Narrower ATM range for quick scalps
OPTIONS_INTRADAY_DELTA_MIN = 0.40  # Intraday min delta (ATM)
OPTIONS_INTRADAY_DELTA_MAX = 0.60  # Intraday max delta (ATM)
# Swing mode (DTE > 5): Wider range to allow 0.70 target with tolerance
OPTIONS_SWING_DELTA_MIN = 0.55  # Swing min delta (slightly ITM)
OPTIONS_SWING_DELTA_MAX = 0.85  # Swing max delta (0.70 target + 0.15 tolerance)
# Threshold for switching between intraday/swing delta validation
# V2.3.22: Raised from 5 to 14 to align with OPTIONS_SWING_DTE_MIN
OPTIONS_SWING_DTE_THRESHOLD = 14  # DTE > 14 uses swing delta bounds

# Legacy constants (kept for backward compatibility)
OPTIONS_DELTA_MIN = 0.40  # Minimum delta (ATM range) - for validation only
OPTIONS_DELTA_MAX = 0.60  # Maximum delta (ATM range) - for validation only

# V2.3: Delta Targets by Mode (per trading firm spec)
# Swing mode targets 0.7 delta (slightly ITM) for higher directional exposure
# Intraday mode targets 0.3 delta (slightly OTM) for faster gamma/premium moves
OPTIONS_SWING_DELTA_TARGET = 0.70  # Target delta for Swing mode (macro)
OPTIONS_INTRADAY_DELTA_TARGET = 0.30  # Target delta for Intraday mode (micro)
OPTIONS_DELTA_TOLERANCE = (
    0.20  # V2.3.5: Widened from 0.15 per PART 9 - allows 0.10-0.50 for 0.30 target
)

# Force Exit Time (V2.1: close options by 3:45 PM like MR)
OPTIONS_FORCE_EXIT_HOUR = 15  # 3 PM
OPTIONS_FORCE_EXIT_MINUTE = 45  # 3:45 PM

# Position Sizing
OPTIONS_RISK_PER_TRADE = 0.01  # 1% portfolio risk per trade

# Time Constraints
OPTIONS_LATE_DAY_HOUR = 14  # 2 PM
OPTIONS_LATE_DAY_MINUTE = 30  # 2:30 PM
OPTIONS_LATE_DAY_MAX_STOP = 0.20  # Only 20% stops after 2:30 PM

# V5.3: Removed OPTIONS_MAX_TRADES_PER_DAY = 1 (dead code, conflicts with MAX_OPTIONS_TRADES_PER_DAY = 4)

# -----------------------------------------------------------------------------
# V2.1.1 DUAL-MODE DTE BOUNDARIES
# -----------------------------------------------------------------------------
# V2.3.22: Hard Swing Floor - raised from 6 to 14 DTE
# Evidence from V2.3.20 backtest: 6 DTE lost 90% vs 28 DTE lost 60%
# 14 DTE options react less violently to overnight gaps
# With single-leg exit at DTE=4, entering at DTE=14 gives 10+ days hold
OPTIONS_SWING_DTE_MIN = 14  # Minimum DTE for Swing Mode (reduces gap risk)
OPTIONS_SWING_DTE_MAX = 45  # Maximum DTE for Swing Mode
OPTIONS_INTRADAY_DTE_MIN = 1  # Baseline floor (MICRO routing may override to 2+)
OPTIONS_INTRADAY_DTE_MAX = (
    5  # V2.13: Match VASS "nearest weekly" (was 1, caused 306 silent failures)
)

# V9.5: MICRO DTE routing by volatility regime (keeps logic simple, config-driven)
MICRO_DTE_ROUTING_ENABLED = True  # V10: re-enabled with wide 1-5 ranges (ITM floor as overlay)
MICRO_DTE_LOW_VIX_THRESHOLD = 18.0  # V10: raised from 16 to align with VIX-tier gates
MICRO_DTE_HIGH_VIX_THRESHOLD = 25.0  # Bear/high-vol profile
MICRO_DTE_LOW_VIX_MIN = 1  # V10: widened from 2 (ITM floor applied as overlay)
MICRO_DTE_LOW_VIX_MAX = 5  # V10: widened from 3
MICRO_DTE_MEDIUM_VIX_MIN = 1  # V10: widened from 2
MICRO_DTE_MEDIUM_VIX_MAX = 5  # V10: widened from 3
MICRO_DTE_HIGH_VIX_MIN = 1  # V10: widened from 2
MICRO_DTE_HIGH_VIX_MAX = 5  # V10: widened from 4

# V10: ITM DTE floor overlay (applied on top of base DTE routing for ITM_MOMENTUM only)
MICRO_ITM_DTE_MIN_LOW_VIX = 3  # LOW VIX: reduce theta decay on ITM singles
MICRO_ITM_DTE_MIN_MED_VIX = 3  # MED VIX: reduce theta decay on ITM singles
MICRO_ITM_DTE_MIN_HIGH_VIX = 2  # HIGH VIX: vol provides buffer
MICRO_ITM_DTE_MAX = 5  # Unchanged intraday envelope
MICRO_DTE_DIAG_LOG_INTERVAL_MIN = 120  # Throttle ITM DTE routing diagnostics
MICRO_DTE_DIAG_LOG_BACKTEST_ENABLED = False  # Keep throttled ITM DTE diagnostics in backtests

# -----------------------------------------------------------------------------
# V2.3 DEBIT SPREAD CONFIGURATION
# -----------------------------------------------------------------------------
# Debit spreads: defined risk, survives whipsaw, no stop loss needed
# V3.0 THESIS-ALIGNED:
#   Bull (70+): CALL spreads active
#   Neutral (50-69): NO options (dead zone)
#   Cautious (30-49): PUT spreads active
#   Bear (0-29): PUT spreads active

# Regime thresholds for spread direction (V3.0: thesis-aligned)
SPREAD_REGIME_BULLISH = 60  # Allow bullish debit spreads in neutral-high bullish lean
SPREAD_REGIME_BEARISH = 50  # V3.0: PUT spreads in Cautious + Bear (regime < 50)
SPREAD_REGIME_CRISIS = 0  # V3.0: DISABLED — PUT spreads work in ALL bear regimes

# V6.13 P0: Regime deterioration exits for swing spreads
SPREAD_REGIME_DETERIORATION_EXIT_ENABLED = True  # V10.3: Re-enabled defensive tail-risk exit
SPREAD_REGIME_DETERIORATION_DELTA = 10  # Require at least 10-point regime drop/rise
SPREAD_REGIME_DETERIORATION_BULL_EXIT = 60  # Exit bullish spreads if regime <= 60
SPREAD_REGIME_DETERIORATION_BEAR_EXIT = 55  # Exit bearish spreads if regime >= 55
SPREAD_REGIME_DETERIORATION_MIN_LOSS_PCT = (
    -0.15
)  # V10.5: trigger deterioration exit only when already losing
# V10.2: Keep spread lifecycle simple; disable overlay-forced close unless explicitly enabled.
SPREAD_OVERLAY_STRESS_EXIT_ENABLED = True  # V10.3: Re-enabled defensive tail-risk exit

# VIX filters for entry
SPREAD_VIX_MAX_BULL = 30  # Max VIX for Bull Call Spread entry
SPREAD_VIX_MAX_BEAR = 35  # Max VIX for Bear Put Spread entry (allow higher)
# V6.19: Conditional stress override for BULL_CALL_DEBIT (reduces call bias in corrections).
# V12.22: tighten bullish debit participation in unstable tapes.
BULL_CALL_STRESS_BLOCK_VIX = 24.0
BULL_CALL_STRESS_ACCEL_VIX = 20.0
BULL_CALL_STRESS_ACCEL_5D = 0.20  # +20% VIX over 5 sessions
BULL_CALL_EARLY_STRESS_VIX_LOW = 18.0
BULL_CALL_EARLY_STRESS_VIX_HIGH = 23.0
BULL_CALL_EARLY_STRESS_SIZE = 0.35
# Bear hardening: block bullish debit spreads when short-term trend is down.
VASS_BULL_CALL_MA50_BLOCK_ENABLED = True
VASS_BULL_CALL_MA50_BLOCK_REGIME_MAX = 65.0
# V6.22: Fast regime overlay thresholds (shared by resolver, slot caps, and exits).
REGIME_OVERLAY_STRESS_VIX = 25.0
REGIME_OVERLAY_STRESS_VIX_5D = 0.12
REGIME_OVERLAY_EARLY_VIX_LOW = 20.0
REGIME_OVERLAY_EARLY_VIX_HIGH = 25.0

# Spread width (strike difference between legs)
# V2.4.3: WIDTH-BASED short leg selection (fixes "delta trap" in backtesting)
# Problem: Delta values jump (0.45 → 0.25) leaving gaps where no "perfect" delta exists
# Solution: Select short leg by STRIKE WIDTH, not delta. Delta is soft preference only.
SPREAD_SHORT_LEG_BY_WIDTH = True  # V2.4.3: Use strike width for short leg (not delta)
# V12.12: Spread width settings — percentage-of-underlying for regime universality.
# Fixed dollar widths don't scale: $7 is 1.4% of QQQ at $500 but 2.8% at $250.
# Percentage-based widths auto-adapt to price level across all regimes.
SPREAD_WIDTH_PCT_BASED = True  # V12.12: use percentage-of-underlying for dynamic width scaling
SPREAD_WIDTH_MIN_PCT = 0.008  # 0.8% of QQQ price → $4 at $500, $2.40 at $300
SPREAD_WIDTH_MIN_LOW_VIX_PCT = 0.006  # 0.6% of QQQ price → $3 at $500, $1.80 at $300
SPREAD_WIDTH_MAX_PCT = 0.020  # 2.0% of QQQ price → $10 at $500, $6 at $300
SPREAD_WIDTH_TARGET_PCT = 0.010  # 1.0% of QQQ price → $5 at $500, $3 at $300
SPREAD_WIDTH_EFFECTIVE_MAX_PCT = 0.015  # 1.5% of QQQ price → $7.50 at $500, $4.50 at $300
CREDIT_SPREAD_WIDTH_TARGET_PCT = 0.010  # 1.0% of QQQ price → $5 at $500, $3 at $300
SPREAD_WIDTH_LOW_VIX_THRESHOLD = 18.0  # VIX threshold for low-width allowance
# Legacy fixed-dollar fallbacks (used when PCT_BASED is False or current_price unavailable)
SPREAD_WIDTH_MIN = 4.0
SPREAD_WIDTH_MIN_LOW_VIX = 3.0
SPREAD_WIDTH_MAX = 10.0
SPREAD_WIDTH_TARGET = 4.0
SPREAD_WIDTH_EFFECTIVE_MAX = 7.0
BEAR_PUT_SPREAD_WIDTH_MIN_BUMP = 1.0  # V12.31: avoid ultra-narrow bearish put spreads under skew
BEAR_PUT_SPREAD_WIDTH_TARGET_BUMP = 1.0  # V12.31: bias bearish put selection one strike wider

# DTE for debit spreads (per V2.3 spec)
# V2.3.22: Raised from 10 to 14 - spreads need same gap cushion as single-leg
SPREAD_DTE_MIN = 14  # Minimum 14 DTE (avoid gamma acceleration + gap risk)
SPREAD_DTE_MAX = 45  # V2.19: Widened from 21 to 45 to align with VASS_LOW_IV_DTE (30-45)
SPREAD_DTE_EXIT = 5  # Close by 5 DTE remaining
VASS_DEBIT_MAX_HOLD_DAYS = 0  # V9.5: disable debit spread time-stop force exit
VASS_DEBIT_MAX_HOLD_DAYS_LOW_VIX = 0  # V9.5: disable low-VIX debit time-stop override
# V12.35: BEAR_PUT-specific max hold cap.  Bear moves are fast, mean-reverting
# spikes — extended holds bleed theta on rich put premiums.  7-day cap forces
# exits on stale bear theses before time decay dominates.
VASS_BEAR_PUT_MAX_HOLD_DAYS = 7
VASS_BEAR_PUT_TIME_STOP_REQUIRE_NON_POSITIVE_PNL = True
VASS_BEAR_PUT_TIME_STOP_MIN_MFE_MAX_PROFIT_PCT = (
    0.25  # Skip BEAR_PUT time-stop if trade stayed profitable and reached meaningful MFE.
)
VASS_DEBIT_LOW_VIX_THRESHOLD = 16.0

# V10.5: Day-4 EOD decision for VASS debit spreads
VASS_DAY4_EOD_DECISION_ENABLED = True
VASS_DAY4_EOD_MIN_HOLD_DAYS = 4
VASS_DAY4_EOD_KEEP_IF_PNL_GT = -0.20  # Day-4 EOD: close if P&L <= -20%; keep if P&L > -20%
VASS_DAY4_EOD_DECISION_TIME = "15:45"
SPREAD_CLOSE_SUBMIT_GUARD_SECONDS = 60  # Suppress duplicate close submits within guard window
SPREAD_EOD_HOLD_RISK_GATE_ENABLED = (
    True  # Close hold-window debit spreads at EOD once loss breaches threshold
)
SPREAD_EOD_HOLD_RISK_GATE_PCT = -0.25  # EOD hold risk gate threshold (e.g., -25%)

# Exit targets
# V6.10 P5: Symmetric R:R (40%/40%) - need 1:1 win ratio to break even
# Was asymmetric (50%/35%) requiring 1.43:1 win ratio
SPREAD_MAX_DEBIT_TO_WIDTH_PCT = 0.44  # Legacy fallback (kept for backward compatibility)
SPREAD_MIN_DEBIT_TO_WIDTH_PCT = (
    0.28  # V10.7: Reject ultra-cheap/low-quality debit structures (balanced D/W band)
)
SPREAD_MAX_DEBIT_TO_WIDTH_PCT_LOW_VIX = 0.48  # Legacy fallback (deprecated by adaptive D/W)
SPREAD_MAX_DEBIT_TO_WIDTH_PCT_MED_VIX = 0.44  # Legacy fallback (deprecated by adaptive D/W)
SPREAD_MAX_DEBIT_TO_WIDTH_PCT_HIGH_VIX = 0.40  # Legacy fallback (deprecated by adaptive D/W)
SPREAD_DW_LOW_VIX_MAX = 18.0
SPREAD_DW_HIGH_VIX_MIN = 25.0
SPREAD_DW_CAP_PANIC = 0.28  # VIX > 35
SPREAD_DW_CAP_HIGH = 0.32  # 25 <= VIX < 35
SPREAD_DW_CAP_ELEVATED = 0.38  # V12.11: coherent with delta 0.43 target (natural D/W ~0.40-0.43).
SPREAD_DW_CAP_NORMAL = (
    0.42  # V12.23.1: slight loosen to reduce D/W over-blocking while preserving quality control.
)
SPREAD_DW_CAP_COMPRESSED = (
    0.42  # V12.23.1: keep compressed-IV aligned with normal cap after slight D/W loosen.
)
# V12.33: Per-strategy D/W cap table for BEAR_PUT debit spreads.
# Put skew inverts the VIX→D/W relationship vs calls: higher VIX → steeper skew → higher
# natural D/W.  The call-oriented caps (0.28-0.42) choke ~80-95% of viable bear puts in
# elevated IV.  These caps reflect put-skew economics directly.
BEAR_PUT_DW_CAP_PANIC = 0.50  # VIX > 35 (call equiv: 0.28)
BEAR_PUT_DW_CAP_HIGH = 0.52  # 25 <= VIX < 35 (call equiv: 0.32)
BEAR_PUT_DW_CAP_ELEVATED = 0.48  # VIX < 25, HIGH iv_env (call equiv: 0.38)
BEAR_PUT_DW_CAP_NORMAL = 0.46  # VIX < 25, normal/compressed (call equiv: 0.42)
SPREAD_DW_ABSOLUTE_CAP = 2.00  # Max debit dollars on $5 spread in very calm IV
SPREAD_DW_ABSOLUTE_CAP_VIX = 15.0
# V12.0: Elastic absolute debit cap (inversely scaled by VIX, bounded).
SPREAD_DW_ABSOLUTE_CAP_DYNAMIC_ENABLED = True
SPREAD_DW_ABSOLUTE_CAP_APPLY_ALL_VIX = (
    False  # Keep absolute cap scoped to calm IV unless explicitly enabled
)
SPREAD_DW_ABSOLUTE_CAP_BASELINE = 1.00
SPREAD_DW_ABSOLUTE_CAP_VIX_SCALE = 20.0
SPREAD_DW_ABSOLUTE_CAP_VIX_FLOOR = 10.0
SPREAD_DW_ABSOLUTE_CAP_MIN = 1.60
SPREAD_DW_ABSOLUTE_CAP_MAX = 2.70  # V12.2: slight constructability relief in calm-IV tapes
SPREAD_PROFIT_TARGET_PCT = (
    0.40  # V10.11: reduce target to improve realization while preserving hold thesis
)
SPREAD_STOP_LOSS_PCT = 0.35  # V10.5: wider base stop to reduce noise stop-outs
SPREAD_HARD_STOP_LOSS_PCT = 0.40  # V10.9: tighter catastrophic cap to reduce tail losses
SPREAD_HARD_STOP_WIDTH_PCT = 0.35  # Hard cap using spread width (debit spreads)
# V12.2: Tiered VASS debit exits (frozen by entry VIX to avoid intra-trade tier flip).
VASS_EXIT_TIERED_ENABLED = True
VASS_EXIT_USE_ENTRY_VIX_TIER = True
VASS_EXIT_VIX_LOW_MAX = 18.0
VASS_EXIT_VIX_HIGH_MIN = 25.0
VASS_TARGET_PCT_LOW_VIX = 0.35
VASS_TARGET_PCT_MED_VIX = 0.40
VASS_TARGET_PCT_HIGH_VIX = 0.50
VASS_STOP_PCT_LOW_VIX = 0.25
VASS_STOP_PCT_MED_VIX = 0.35
VASS_STOP_PCT_HIGH_VIX = 0.40
VASS_TRAIL_ACTIVATE_LOW_VIX = 0.18
VASS_TRAIL_ACTIVATE_MED_VIX = 0.22
VASS_TRAIL_ACTIVATE_HIGH_VIX = 0.28
VASS_TRAIL_OFFSET_LOW_VIX = 0.12
VASS_TRAIL_OFFSET_MED_VIX = 0.15
VASS_TRAIL_OFFSET_HIGH_VIX = 0.20
# V12.3 C1: Tiered catastrophic and EOD hold exits for VASS debit spreads.
VASS_HARD_STOP_LOW_VIX = 0.35
VASS_HARD_STOP_MED_VIX = 0.40
VASS_HARD_STOP_HIGH_VIX = 0.45
VASS_EOD_GATE_LOW_VIX = -0.20
VASS_EOD_GATE_MED_VIX = -0.25
VASS_EOD_GATE_HIGH_VIX = -0.35
# V12.3 C3: ATR adapter for VASS spread exits.
VASS_ATR_ADAPTIVE_EXITS_ENABLED = True
VASS_ATR_PCT_REF = 0.015
VASS_ATR_EXIT_MULT_MIN = 0.85
VASS_ATR_EXIT_MULT_MAX = 1.25
VASS_ATR_ADAPT_HARD_AND_EOD = True
SPREAD_STOP_REGIME_MULTIPLIERS = {
    75: 1.10,  # Bull: slightly wider stop to reduce pullback churn
    50: 1.00,  # Neutral: base
    40: 0.95,  # Cautious: near-base (avoid over-tightening)
    0: 0.90,  # Bear: still tighter, but not extreme noise-sensitive
}

# V9.4: Spread Trailing Stop — lock in gains after reaching activation threshold
SPREAD_TRAIL_ACTIVATE_PCT = 0.22  # V10.11: earlier trail activation to reduce round-trip giveback
SPREAD_TRAIL_OFFSET_PCT = 0.15  # V10.11: tighter trailing offset once activated

# V10.15: VASS MFE harvesting locks (target-relative)
VASS_MFE_LOCK_ENABLED = True
VASS_MFE_T1_TRIGGER = 0.25  # 25% of max profit reached
VASS_MFE_T2_TRIGGER = 0.45  # 45% of max profit reached
VASS_MFE_T2_FLOOR_PCT = 0.15  # Lock +15% floor once T2 reached
VASS_MFE_T2_FLOOR_LOW_VIX = 0.12
VASS_MFE_T2_FLOOR_MED_VIX = 0.18
VASS_MFE_T2_FLOOR_HIGH_VIX = 0.25
# V12.35: BEAR_PUT-specific MFE T2 floor overrides.
# Put skew drives D/W to 0.46-0.52 for BEAR_PUT vs 0.28-0.42 for BULL_CALL.
# The generic floors (12-25% of max_profit) protect only 16-20% of the debit paid;
# these overrides bring profit protection to ~32-35% of debit, matching BULL_CALL
# economics on a debit-relative basis.
VASS_MFE_T2_FLOOR_BEAR_PUT_LOW_VIX = 0.35
VASS_MFE_T2_FLOOR_BEAR_PUT_MED_VIX = 0.40
VASS_MFE_T2_FLOOR_BEAR_PUT_HIGH_VIX = 0.45
VASS_TAIL_RISK_CAP_ENABLED = True  # Emergency per-trade account-risk kill switch
VASS_TAIL_RISK_CAP_PCT_EQUITY = 0.010  # V12.4: tighten cap to 1.0% of portfolio equity
VASS_TAIL_RISK_CAP_USE_DTE_OVERLAY = True  # V12.6: make cap regime/DTE adaptive
VASS_TAIL_RISK_CAP_DTE_SHORT_MAX = 9
VASS_TAIL_RISK_CAP_DTE_MED_MAX = 21
VASS_TAIL_RISK_CAP_DTE_SHORT_MULT = 0.75
VASS_TAIL_RISK_CAP_DTE_MED_MULT = 1.00
VASS_TAIL_RISK_CAP_DTE_LONG_MULT = 1.25
VASS_TAIL_RISK_CAP_OVERLAY_MULTIPLIERS = {
    "DETERIORATION": 0.80,
    "RECOVERY": 0.95,
    "STABLE": 1.00,
    "AMBIGUOUS": 0.85,
}
VASS_TAIL_RISK_CAP_MIN_PCT_EQUITY = 0.006
VASS_TAIL_RISK_CAP_MAX_PCT_EQUITY = 0.015
VASS_TAIL_RISK_CAP_FLOOR_PCT = 0.45  # V12.22: keep tail-cap path above normal stop band
# V12.22: Tail cap is a last-resort override; equity cap only activates after
# these minimum loss fractions are reached (prevents premature tail-cap exits).
VASS_TAIL_RISK_CAP_DEBIT_MIN_LOSS_PCT = 0.45
VASS_TAIL_RISK_CAP_CREDIT_MIN_LOSS_PCT = 0.45
VASS_CATASTROPHIC_EXIT_NO_REENTRY_REST_OF_SESSION = True
# V12.22: After catastrophic/large-stop exits, block same-direction VASS entries
# for N trading days to avoid immediate loss clustering.
VASS_CATASTROPHIC_DIRECTION_COOLDOWN_DAYS = 1
VASS_CATASTROPHIC_EXIT_LOCK_MINUTES = 0  # 0 => lock until session close
VASS_CATASTROPHIC_EXIT_FALLBACK_LOCK_MINUTES = 120
# Entry friction sanity: reject spreads where expected entry friction consumes too much
# of expected target profit (production-quality cost control).
SPREAD_ENTRY_FRICTION_GATE_ENABLED = True
SPREAD_ENTRY_FRICTION_TO_TARGET_MAX = 0.35  # Max friction / expected target-profit ratio

# V3.0: Regime-Adaptive Profit Targets
# V9.4: With 40% base, multipliers give: Bull=36%, Neutral=44%, Cautious/Bear=48%
SPREAD_PROFIT_REGIME_MULTIPLIERS = {
    75: 0.95,  # Bull: slightly easier than base to capture trend gains
    50: 1.00,  # Neutral: base target
    40: 1.05,  # Cautious: modestly higher target
    0: 1.10,  # Bear: keep higher target for larger moves
}

# V9.4: BULL spread entry gates (regime-specific, no impact in bull markets)
VASS_BULL_SPREAD_REGIME_MIN = 60  # V12.22: require stronger regime for BULL_CALL entries
VASS_BULL_MA20_GATE_ENABLED = False  # V9.5 tune: disable for VASS swing pullback participation
# V10.16.1: Scoped bullish trend confirmation for debit BULL_CALL in low/medium IV.
# Avoids local-top entries without globally reintroducing MA20 starvation.
VASS_BULL_DEBIT_TREND_CONFIRM_ENABLED = True
VASS_BULL_DEBIT_TREND_CONFIRM_MAX_VIX = 22.0  # Apply only in LOW/MEDIUM IV tape
VASS_BULL_DEBIT_REGIME_MAX = (
    72.0  # V12.25: avoid local-peak BULL_CALL_DEBIT entries in extreme RISK_ON
)
VASS_BULL_DEBIT_REQUIRE_MA20 = True
VASS_BULL_DEBIT_REQUIRE_POSITIVE_DAY = (
    False  # V12.2: remove redundant day-change block; keep MA20 confirmation
)
VASS_BULL_DEBIT_MIN_DAY_CHANGE_PCT = (
    0.08  # V10.30: permit gradual uptrend participation in low-vol tape
)
VASS_BULL_SHORT_CALL_DISTANCE_GUARD_ENABLED = True
VASS_BULL_SHORT_CALL_MIN_OTM_PCT = 0.010
VASS_BULL_SHORT_CALL_MIN_ATR_MULT = 0.60
# V12.25: Underlying-based thesis invalidation for BULL_CALL_DEBIT exits.
# Primary stop uses QQQ invalidation (cleaner thesis signal than option mark noise).
VASS_BULL_DEBIT_QQQ_INVALIDATION_ENABLED = True
VASS_BULL_DEBIT_QQQ_INVALIDATION_CLOSE_PCT = 0.040
VASS_BULL_DEBIT_QQQ_INVALIDATION_INTRADAY_PCT = 0.039  # V12.31: tuned from 4.0% to 3.9% (earlier thesis-break detection without winner clipping in RCA sample).
VASS_BULL_DEBIT_QQQ_INVALIDATION_CLOSE_TIME = "15:45"
VASS_BULL_DEBIT_STALE_EXIT_ENABLED = True
VASS_BULL_DEBIT_STALE_MAX_HOLD_DAYS = 15
VASS_BULL_DEBIT_STALE_MAX_CURRENT_PNL_PCT = 0.10
VASS_BULL_DEBIT_STALE_MIN_PROGRESS_PNL_PCT = 0.20
# V12.27: Thesis-soft-stop for BULL_CALL_DEBIT.
# Keep hard invalidation/regime-break behavior, but avoid tactical churn exits.
VASS_BULL_DEBIT_THESIS_SOFT_STOP_ENABLED = (
    False  # V12.30: disable thesis soft stop to restore legacy BCD win profile.
)
VASS_BULL_DEBIT_THESIS_SOFT_STOP_THESIS_ONLY = True
VASS_BULL_DEBIT_THESIS_SOFT_STOP_MIN_TIME_USED = 0.35  # fraction of entry DTE consumed
VASS_BULL_DEBIT_THESIS_SOFT_STOP_MAX_PNL_PCT = -0.22  # mark drawdown guard
VASS_BULL_DEBIT_THESIS_SOFT_STOP_MAX_ATR_MOVE = -0.75  # underlying move in ATR units from entry
VASS_BULL_DEBIT_THESIS_SOFT_STOP_PBE_BASE = 0.12
VASS_BULL_DEBIT_THESIS_SOFT_STOP_PBE_SLOPE = 0.30
VASS_BULL_DEBIT_THESIS_SOFT_STOP_PBE_MIN = 0.12
VASS_BULL_DEBIT_THESIS_SOFT_STOP_PBE_MAX = 0.45
VASS_BULL_DEBIT_THESIS_SOFT_STOP_MIN_EXPECTED_MOVE_ATR = 0.75
VASS_BULL_DEBIT_THESIS_SOFT_STOP_CONSECUTIVE_BARS = 2
VASS_BULL_DEBIT_THESIS_ONLY_DISABLE_TACTICAL_EXITS = (
    False  # V12.30: keep tactical rails active for BCD when soft-stop is disabled.
)
VASS_BULL_DEBIT_THESIS_ONLY_DISABLE_VIX_SPIKE_EXITS = True
# V12.35: Underlying-based thesis invalidation for BEAR_PUT_DEBIT exits.
# Symmetric to BULL_CALL QQQ invalidation but tighter (3.5% vs 3.9%) because
# IV crush on a rally accelerates put premium decay faster than call decay on drops.
VASS_BEAR_DEBIT_QQQ_INVALIDATION_ENABLED = True
VASS_BEAR_DEBIT_QQQ_INVALIDATION_CLOSE_PCT = 0.035
VASS_BEAR_DEBIT_QQQ_INVALIDATION_INTRADAY_PCT = 0.035
VASS_BEAR_DEBIT_QQQ_INVALIDATION_CLOSE_TIME = "15:45"
VASS_RECOVERY_RELAX_ENABLED = False  # V12.11: disabled — fires on ALL bullish entries (not just recovery), silently overrides D/W caps by +9%.
VASS_RECOVERY_RELAX_DAY_MIN_CHANGE_PCT = -0.05
VASS_RECOVERY_RELAX_MA20_TOLERANCE_PCT = 0.003
VASS_RECOVERY_RELAX_DW_CAP_BUMP = 0.09
VASS_RECOVERY_RELAX_MAX_DW_CAP = 0.55

# V12.12: MA20 tolerance decoupled from RECOVERY_RELAX.
# Standalone 0.3% pullback allowance below MA20 for bullish debit trend confirmation.
# Previously bundled into RECOVERY_RELAX — disabling RECOVERY_RELAX killed this tolerance as collateral damage.
VASS_BULL_DEBIT_MA20_TOLERANCE_ENABLED = True
VASS_BULL_DEBIT_MA20_TOLERANCE_PCT = 0.003  # 0.3% below MA20 pullback allowance
VASS_BULL_DEBIT_MIN_DAY_CHANGE_PCT_RELAXED = (
    -0.05
)  # Allow slightly negative day when tolerance active

# V9.7: BEAR_PUT entry gate — block in RISK_ON (12.5% WR in 2017 full-year RCA)
VASS_BEAR_PUT_REGIME_MAX = 60  # V10.32: require clearer macro weakness for bearish debit spreads

# V2.27: Win Rate Gate (Options Self-Correcting Throttle)
# Rolling window of recent closed spread trades. Scales down/shuts off when losing.
WIN_RATE_GATE_ENABLED = True
WIN_RATE_LOOKBACK = 10  # Rolling window of recent closed spread trades
WIN_RATE_FULL_THRESHOLD = 0.40  # Above 40%: full size
WIN_RATE_REDUCED_THRESHOLD = 0.30  # 30-40%: reduced size
WIN_RATE_MINIMUM_THRESHOLD = 0.20  # 20-30%: minimum size
WIN_RATE_SHUTOFF_THRESHOLD = 0.20  # Below 20%: STOP all new spread entries
WIN_RATE_RESTART_THRESHOLD = 0.35  # Resume when paper win rate recovers to 35%
WIN_RATE_SIZING_REDUCED = 0.75  # Multiplier at REDUCED level
WIN_RATE_SIZING_MINIMUM = 0.50  # Multiplier at MINIMUM level
WIN_RATE_GATE_MAX_SHUTOFF_DAYS = 30  # Auto-reset stale shutoff after prolonged degrade window
# V6.19 O-20: Keep VASS alive in stress periods; avoid full spread-path freeze.
VASS_WIN_RATE_HARD_BLOCK = False  # If False, shutoff degrades to minimum size instead of blocking
VASS_WIN_RATE_SHUTOFF_SCALE = (
    0.50  # Soft-mode floor to avoid over-suppressing after kill-switch events
)
# V10.7: Win-rate gate execution mode for VASS spread entries.
# - monitor_only: keep telemetry/state tracking but do not block/scale entries.
# - enforce: apply normal block/scale behavior.
WIN_RATE_GATE_VASS_EXECUTION_MODE = "monitor_only"
# V10.7: Lightweight VASS loss breaker (separate from win-rate gate).
VASS_LOSS_BREAKER_ENABLED = True
VASS_LOSS_BREAKER_THRESHOLD = 3
VASS_LOSS_BREAKER_PAUSE_DAYS = 1
# In elevated VIX, do not allow VASS bullish conviction to force trades from NEUTRAL macro.
VASS_NEUTRAL_BULL_OVERRIDE_MAX_VIX = 20.0
MACRO_DIRECTION_BULLISH_MIN = 55.0
MACRO_DIRECTION_BEARISH_MAX = 45.0
VASS_BULL_PROFILE_BEARISH_BLOCK_ENABLED = True
VASS_BULL_PROFILE_REGIME_MIN = 72.0  # V10.22: only block bearish VASS in stronger bull regimes
VASS_BULL_TRANSITION_MIN_REGIME = 50.0  # Allow only strong recovery overrides below default floor
VASS_TRANSITION_BLOCK_AMBIGUOUS = True
VASS_TRANSITION_BLOCK_BULL_ON_DETERIORATION = True
VASS_TRANSITION_BLOCK_BEAR_ON_RECOVERY = True
VASS_BEAR_RECOVERY_HARD_BLOCK_BARS = (
    2  # Block only the first two RECOVERY bars for bearish VASS, then use handoff throttle.
)
VASS_TRANSITION_HANDOFF_THROTTLE_ENABLED = True
VASS_TRANSITION_HANDOFF_BARS = 4
# V12.4: pre-close transition de-risk for bullish debit VASS to avoid overnight gap carry.
VASS_OVERNIGHT_DERISK_ENABLED = True
VASS_OVERNIGHT_DERISK_TIME = "15:40"
VASS_OVERNIGHT_DERISK_ON_DETERIORATION = True
VASS_OVERNIGHT_DERISK_ON_AMBIGUOUS = False
# V6.1: Removed SPREAD_REGIME_EXIT_BULL/BEAR - legacy logic conflicted with conviction-based entry
# Spreads now exit via stop/target/dte paths (neutrality exit is optional and currently disabled).

# V6.10 P5: Choppy Market Filter
# Detects whipsawing markets and reduces position size to limit losses
# Rationale: 2015 had 48% win rate but negative P&L due to choppy markets hitting stops
CHOPPY_MARKET_FILTER_ENABLED = True  # V6.10 P5: Enable choppy market detection
CHOPPY_REVERSAL_COUNT = 3  # 3+ reversals in lookback window = choppy
CHOPPY_LOOKBACK_HOURS = 2  # Look back 2 hours for reversal count
CHOPPY_SIZE_REDUCTION = 0.65  # V6.19 tune: keep participation while still reducing exposure
CHOPPY_MIN_MOVE_PCT = 0.003  # Minimum 0.3% move to count as reversal (filters noise)

# V2.22: Neutrality Exit (Hysteresis Shield)
# Close flat spreads when regime enters dead zone — no directional edge
# V3.4: Separate neutrality zone from exit thresholds (allows tight exits + wide neutrality)
SPREAD_NEUTRALITY_EXIT_ENABLED = False  # V12.25: disable staged neutrality exits
SPREAD_NEUTRALITY_EXIT_PNL_BAND = 0.06  # V6.13 OPT: Tight "flat" band
SPREAD_NEUTRALITY_ZONE_LOW = 48  # V6.13 OPT: Narrower neutrality zone
SPREAD_NEUTRALITY_ZONE_HIGH = 62  # V6.13 OPT: Narrower neutrality zone
SPREAD_NEUTRALITY_STAGED_ENABLED = True  # Phase C: two-stage de-risk to reduce churn exits
SPREAD_NEUTRALITY_CONFIRM_HOURS = 2  # Stage-2 exit only after neutrality persistence window
SPREAD_NEUTRALITY_STAGE1_DAMAGE_PCT = 0.15  # Exit early if loss breaches this threshold in stage-1

# V2.16-BT: Commission-aware profit targets
# Round-trip commission estimate per spread (entry + exit, both legs)
# IBKR: ~$0.65/contract × 2 legs × 2 (entry+exit) = $2.60/spread
SPREAD_COMMISSION_PER_CONTRACT = 2.60  # Estimated round-trip commission per spread
SPREAD_ENTRY_COMMISSION_GATE_ENABLED = True
SPREAD_MAX_COMMISSION_TO_MAX_PROFIT_RATIO = (
    0.15  # Balanced fee gate to avoid over-throttling entries
)

# -----------------------------------------------------------------------------
# V2.17-BT: COMBO ORDER RETRY & KILL SWITCH COORDINATION
# -----------------------------------------------------------------------------
# Fixes: USER-1 (naked exposure), USER-3 (kill switch bypass), RPT-9 (no retry)
# All spread closes now go through Router with retry + sequential fallback

# Combo order retry settings
COMBO_ORDER_MAX_RETRIES = 2  # V12.6: reduce retry depth and escalate faster to sequential close
COMBO_ORDER_FALLBACK_TO_SEQUENTIAL = True  # If all retries fail, use sequential close
SPREAD_CLOSE_SAFE_LOCK_RETRY_MIN = 10  # Retry emergency close after safe-lock alert
# V12.23.2: VASS close ladder control (limit -> market -> sequential) with no multi-session churn.
VASS_CLOSE_MAX_COMBO_LIMIT_ATTEMPTS = 1
VASS_CLOSE_LIMIT_TIMEOUT_SECONDS = 30
# V12.27: soft exits (thesis/regime-driven) remain limit-only for a bounded defer window.
# Hard exits (tail risk, hard stop, assignment risk, etc.) still escalate immediately.
VASS_CLOSE_SOFT_EXIT_MAX_DEFER_MINUTES = 120
VASS_CLOSE_USE_COMBO_MARKET_AFTER_LIMIT_FAIL = True
VASS_CLOSE_ALLOW_SEQUENTIAL_SAME_CYCLE = True
VASS_CLOSE_DISABLE_MULTISESSION_RETRY = True
VASS_CLOSE_CANCEL_ESCALATION_COUNT = 1  # First cancel -> force market close retry
VASS_CLOSE_SEQUENTIAL_ESCALATION_COUNT = 2  # Second cancel -> emergency sequential close
VASS_CLOSE_QUOTE_INVALID_COMBO_MARKET_RETRY = True
VASS_CLOSE_QUOTE_INVALID_SEQ_FALLBACK = True
# V12.3 F1: bounded-loss guard for spread exits (pre-submit quote sanity).
SPREAD_EXIT_BOUNDED_LOSS_GUARD_ENABLED = True
SPREAD_EXIT_NET_VALUE_FLOOR = 0.0  # Debit spread close value should not be materially negative.
SPREAD_EXIT_NET_VALUE_TOLERANCE = -0.05  # Allow small quote noise (e.g., -$0.05) before blocking.
SPREAD_EXIT_MAX_CLOSE_DEBIT_BUFFER_PCT = (
    0.10  # Allow up to +10% above entry debit before treating close quote as pathological.
)
SPREAD_EXIT_PROTECTED_COMBO_ENABLED = True  # Use combo limit exits for better close-price control.
SPREAD_EXIT_COMBO_LIMIT_SLIPPAGE_PCT = (
    0.20  # Allow 20% of aggregate leg spread as marketability buffer.
)
SPREAD_EXIT_COMBO_LIMIT_MIN_STEP = 0.01  # Minimum credit concession ($/share) for protected exits.

# Sequential fallback: close SHORT leg first (buy back), then LONG leg (sell)
# This prevents naked short exposure - worst case is holding a long temporarily

# State management for stuck spreads
SPREAD_LOCK_CLEAR_ON_FAILURE = True  # Clear is_closing lock if all close attempts fail
# When True: Spread becomes available for retry on next iteration
# When False: Spread stays locked, requires manual intervention

# Delta targets for spread legs - V2.3.21 "Smart Swing" Strategy
# ITM Long Leg / OTM Short Leg: Prioritize execution with wider delta range
# V2.3.24: Widened DELTA_MIN from 0.55 → 0.50 to include ATM contracts
# V6.6: Slightly relaxed delta requirements for better contract matching
# 2022H1 analysis showed 36 spread failures due to strict delta requirements
SPREAD_LONG_LEG_DELTA_MIN = 0.35  # V6.10 P3: Was 0.40, widen range for more candidates
SPREAD_LONG_LEG_DELTA_MAX = 0.65  # V9.1: Was 0.90, cap ITM depth to improve R:R on CALL debits
SPREAD_LONG_LEG_DELTA_TARGET_CALL = 0.43  # V12.11: slightly OTM — D/W ≈ delta, so 0.43 produces D/W ~0.40-0.43 (PoP 57-60%, R:R 1.3-1.5:1). ATM (0.50) was coin-flip; ITM (0.58) was worse.
SPREAD_LONG_LEG_DELTA_TARGET_PUT = (
    0.70  # V9.1: ITM target for PUTs (unchanged, directional exposure)
)
VASS_BULL_DEBIT_NET_DELTA_MIN = 0.0  # V12.12: disabled — redundant with D/W cap. Net delta ≈ D/W for verticals; gate incompatible with delta 0.43 target (net delta ~0.17 < 0.18 threshold). Was 0.18.
SPREAD_SHORT_LEG_DELTA_MIN = 0.08  # V6.10 P3: Was 0.10, allow farther OTM shorts
SPREAD_SHORT_LEG_DELTA_MAX = 0.60  # V6.10 P3: Was 0.55, allow closer-to-ATM shorts
# V6.9: PUT-specific spread filters (bear put spreads need looser liquidity + delta)
SPREAD_LONG_LEG_DELTA_MIN_PUT = 0.25  # V6.10 P3: Was 0.30, widen PUT delta range
SPREAD_LONG_LEG_DELTA_MAX_PUT = 0.90  # V6.10 P3: Allow deeper ITM long PUTs
SPREAD_SHORT_LEG_DELTA_MIN_PUT = 0.05  # V6.10 P3: Was 0.08, allow farther OTM PUT shorts
SPREAD_SHORT_LEG_DELTA_MAX_PUT = 0.60  # V6.10 P3: Allow closer-to-ATM short PUTs
OPTIONS_MIN_OPEN_INTEREST_PUT = 25  # Relax OI filter for puts
OPTIONS_SPREAD_MAX_PCT_PUT = 0.25  # Allow slightly wider spreads for puts
OPTIONS_SPREAD_WARNING_PCT_PUT = 0.35

# -----------------------------------------------------------------------------
# V2.4.1 SWING SAFETY RULES
# -----------------------------------------------------------------------------
# "Safety First" system - protect capital over trying to get filled

# No Naked Fallback: If spread can't be formed, stay cash
# Single-leg fallback has higher delta exposure and full premium at risk
SWING_FALLBACK_ENABLED = False  # V2.4.1: Disabled - if spread fails, stay cash

# Friday Firewall: Close swing options before weekend
# Weekend gaps can be catastrophic for options (theta + gap risk)
FRIDAY_FIREWALL_ENABLED = True  # V2.4.1: Close swing options on Friday
FRIDAY_FIREWALL_TIME_HOUR = 15  # 3:45 PM ET
FRIDAY_FIREWALL_TIME_MINUTE = 45

# VIX Filter: If VIX > threshold, close everything regardless of day
FRIDAY_FIREWALL_VIX_CLOSE_ALL = 25  # VIX > 25: Close ALL swing options

# Fresh Trade Protection: Trades opened same Friday must close (unless VIX < threshold)
# Holding a brand new trade over the weekend is gambling
FRIDAY_FIREWALL_VIX_KEEP_FRESH = 15  # VIX < 15: Calm enough to keep fresh Friday trades
FRIDAY_FIREWALL_APPLY_TO_VASS_DEBIT = (
    False  # Thesis-first: keep long-DTE VASS debit spreads through Friday firewall.
)
FRIDAY_FIREWALL_APPLY_TO_VASS_CREDIT = True  # Keep weekend risk control for VASS credit spreads.

# -----------------------------------------------------------------------------
# V2.6 SPREAD FILL TRACKING & SAFETY (Bug Fixes #1-16)
# -----------------------------------------------------------------------------

# Fill tracking timeout - abort spread if not fully filled within this time
SPREAD_FILL_TIMEOUT_MINUTES = 5  # Bug #7: Stale fill prices timeout

# Action when leg quantities don't match expected
SPREAD_FILL_QTY_MISMATCH_ACTION = "LOG_AND_CLOSE"  # or "LOG_ONLY"

# Post-trade margin cooldown - wait for settlement before new entry
OPTIONS_POST_TRADE_COOLDOWN_MINUTES = 2  # Bug #16: T+1 margin ghost

# -----------------------------------------------------------------------------
# V2.9: SETTLEMENT-AWARE TRADING (Bug #6 Fix)
# -----------------------------------------------------------------------------
# Options settle T+1. Friday closes don't settle until Monday morning.
# Holiday-aware: Uses exchange calendar, not weekday() - handles MLK, Presidents Day, etc.
# Key insight: Post-holiday Tuesday has same settlement lag as Monday after weekends.
SETTLEMENT_AWARE_TRADING = True  # Master switch for settlement logic
SETTLEMENT_COOLDOWN_MINUTES = 60  # Wait 1 hour after any post-gap market open
SETTLEMENT_CHECK_SYMBOL = "SPY"  # Use SPY for market calendar (most reliable)

# Thursday Expiration Handling (Friday Holiday Weeks)
# Good Friday, etc. = market closed Friday, options expire Thursday
FRIDAY_HOLIDAY_CHECK_ENABLED = True  # Enable Thursday expiration detection

# Exit order retry settings for gamma decay (0DTE)
EXIT_ORDER_RETRY_COUNT = 3  # Bug #14: Retry failed exits
EXIT_ORDER_RETRY_DELAY_SECONDS = 5  # Delay between retries
SPREAD_CLOSE_CANCEL_ESCALATION_COUNT = 2  # Allow one re-price cycle before sequential fallback
SPREAD_CLOSE_RETRY_INTERVAL_MIN = 5  # Retry cadence for forced spread close queue
SPREAD_CLOSE_MAX_RETRY_CYCLES = (
    3  # V12.21: shorter retry budget before emergency sequential fallback
)
# Stale cleanup policy:
# - Global stale age keeps true orphan orders from lingering.
# - Active spread-close intents get extra grace so combo exits are not canceled too early.
STALE_CLEANUP_MAX_AGE_MINUTES = 5
STALE_CLEANUP_ACTIVE_SPREAD_CLOSE_GRACE_MINUTES = 20

# 0DTE forced exit time (3:30 PM ET = 30 min before close)
ZERO_DTE_FORCE_EXIT_HOUR = 15
ZERO_DTE_FORCE_EXIT_MINUTE = 30

# -----------------------------------------------------------------------------
# V2.10: CREDIT SPREAD SAFETY ENHANCEMENTS
# -----------------------------------------------------------------------------
# Pitfall #1: Mid-Price Slippage - Buffer for bid-ask spread slippage
SLIPPAGE_BUFFER_PCT = 0.02  # 2% assumed slippage per leg
CREDIT_SPREAD_MIN_CREDIT_ADJUSTED = 0.35  # Increased from 0.30 to cover exit slippage

# Pitfall #5: Gamma Pin at Expiry - Buffer zone around short strike
# Exit early if underlying price is within buffer zone of short strike near expiration
GAMMA_PIN_CHECK_ENABLED = True  # Master switch for gamma pin protection
GAMMA_PIN_BUFFER_PCT = 0.005  # 0.5% buffer zone around short strike
GAMMA_PIN_EARLY_EXIT_DTE = 2  # Activate within 2 DTE

# Pitfall #4: VASS Rejection Logging - Throttled logging for silent rejections
VASS_LOG_REJECTION_INTERVAL_MINUTES = 30  # Log rejections every 15 min (not every candle)
MICRO_NO_TRADE_LOG_INTERVAL_MINUTES = 30  # Per-block throttle for MICRO_NO_TRADE logs
VASS_SLOT_BACKOFF_ENABLED = True  # V12.9 P1: suppress repeated slot-block churn loops.
VASS_SLOT_BACKOFF_MINUTES = 20

# -----------------------------------------------------------------------------
# V2.11: PRE-BACKTEST SAFETY FIXES (Pitfalls #6-8)
# -----------------------------------------------------------------------------
# Pitfall #6: Margin Collateral Lock-Out
# Options sizing must cap by actual available margin, not just portfolio %
# V2.12 Fix #4: Raised from $5K to $10K - 8-lot spread requires ~$8K margin
# V3.0 SCALABILITY FIX: Converted to percentage-based for portfolio scaling
# V12.15: DEPRECATED — fixed dollar cap replaced by percentage-based OPTIONS_MAX_MARGIN_PCT.
# Kept for backward compatibility; no longer read by any active code path.
OPTIONS_MAX_MARGIN_CAP = 50_000
OPTIONS_MAX_MARGIN_PCT = 0.50  # V10.9: Align with OPTIONS_BUDGET_CAP_PCT so per-scan sizing isn't squeezed when Trend is active

# V2.18: Percentage-based Sizing Caps (scales with portfolio)
# At $75K: 15% = $11,250, 8% = $6,000
# At $200K: 15% = $30,000, 8% = $16,000
SWING_SPREAD_MAX_PCT = 0.15  # 15% legacy cap (kept for backward compatibility)
VASS_RISK_PER_TRADE_PCT = 0.20  # V12.4: reduce VASS allocation concentration to 20%
VASS_MAX_SPREAD_RISK_PCT = 0.03  # V12.7: size each VASS spread to 3% max defined risk
VASS_MAX_CONCURRENT_SPREADS = 3  # V12.7: cap concurrent VASS spreads (portfolio-level containment)
VASS_MAX_CONCURRENT_SPREADS_HARD_CAP = 3  # V12.9 P2: keep cap locked during EV stabilization.
INTRADAY_SPREAD_MAX_PCT = 0.08  # Legacy fallback for intraday sizing
VASS_MAX_RISK_DOLLARS = (
    0  # V12.14: set to 0 to use percentage-based (OPTIONS_SWING_ALLOCATION of portfolio)
)

# V12.14: Budget-proportional VASS sizing
VASS_DEPLOY_PCT_OF_BUDGET = 0.40  # Deploy up to 40% of VASS budget per trade
# Note: VASS budget = OPTIONS_SWING_ALLOCATION (35%) × TotalPortfolioValue

# V12.14: Unified R:R-aware sizing
VASS_RR_SCALING_ENABLED = True

# V12.22: Universal payoff-quality gate across debit/credit spread entries.
# Uses the same reference/worst R:R thresholds as sizing:
# - Debit: lower D/W is better.
# - Credit: higher C/W is better.
VASS_UNIVERSAL_QUALITY_GATE_ENABLED = True
VASS_UNIVERSAL_QUALITY_SCORE_MIN = 0.25  # 0=allow worst boundary, 1=only reference quality

# Debit spread R:R thresholds (D/W% — lower = better)
VASS_RR_DEBIT_REFERENCE_DW = 0.35  # "Good" D/W — gets full allocation (scale = 1.0)
VASS_RR_DEBIT_WORST_DW = 0.48  # Worst acceptable D/W — gets floor allocation

# Credit spread R:R thresholds (C/W% — higher = better)
VASS_RR_CREDIT_REFERENCE_CW = 0.40  # "Good" C/W — gets full allocation (scale = 1.0)
VASS_RR_CREDIT_WORST_CW = 0.30  # Worst acceptable C/W — gets floor allocation

# Shared R:R floor
VASS_RR_FLOOR_SCALE = 0.60  # Floor multiplier at worst quality (60% of full)

INTRADAY_ITM_MAX_PCT = 0.15  # ITM budget slice (15% / $15k on $100k)
INTRADAY_ITM_MAX_DOLLARS = 0  # V12.15: disable fixed-dollar clamp; ITM uses percent budget
INTRADAY_OTM_MAX_PCT = 0.10  # OTM budget slice (10% / $10k on $100k)
INTRADAY_OTM_MAX_DOLLARS = 0  # V12.15: disable fixed-dollar clamp; MICRO uses percent budget

# V3.0: Minimum margin percentage to allow options trading
# Replaces hardcoded $1,000 check in main.py
OPTIONS_MIN_MARGIN_PCT = 0.02  # 2% of portfolio minimum margin to trade options
MARGIN_MIN_FREE_EQUITY_PCT = 0.10  # Keep 10% equity as free margin cushion for spread entries
NEUTRAL_ALIGNED_SIZE_MULT = 0.50  # V6.12: Reduce size when Macro NEUTRAL and no conviction

# V2.21: Rejection-aware spread sizing
# Pre-submission: use 80% of reported margin (20% buffer for broker calc differences)
SPREAD_MARGIN_SAFETY_FACTOR = 0.80
# Post-rejection: apply to broker-reported Free Margin for adaptive retry cap
SPREAD_REJECTION_MARGIN_SAFETY = 0.80
# V12.31: Broker insufficient-BP recovery controls for spread entries.
# Use broker maintenance delta to size one immediate retry, then short cooldown.
SPREAD_REJECTION_RETRY_MARGIN_UTILIZATION = 0.70
SPREAD_REJECTION_IMMEDIATE_ATTEMPTS = 1
SPREAD_REJECTION_SHORT_COOLDOWN_MINUTES = 5
SPREAD_REJECTION_STREAK_WINDOW_MINUTES = 20

# V8.2 cleanup: options budget gate is now the primary normal-entry limiter.
# Keep margin-utilization gate as emergency brake only.
OPTIONS_BUDGET_GATE_ENABLED = True
OPTIONS_BUDGET_CAP_PCT = (
    CAPITAL_PARTITION_OPTIONS  # Keep options risk aligned to configured partition.
)
OPTIONS_BUDGET_WARN_PCT = 0.90  # Warn when used budget is above 90% of cap.

# V4.0.2: Margin Utilization Gate - emergency-only circuit breaker
MAX_MARGIN_UTILIZATION = 0.90  # Block new BUY orders only in high-stress utilization.
MARGIN_UTILIZATION_WARNING = 0.80  # Early warning threshold.
MARGIN_UTILIZATION_ENABLED = True  # Enable/disable emergency margin circuit breaker.

# Pitfall #8: Settlement Ghost - Smarter threshold-based gate
# Only halt if UnsettledCash is material (>10% of portfolio)
SETTLEMENT_UNSETTLED_THRESHOLD_PCT = 0.10  # 10% threshold to trigger halt
SETTLEMENT_HALT_UNTIL_HOUR = 10  # Halt until 10:30 AM (not arbitrary 60 min)
SETTLEMENT_HALT_UNTIL_MINUTE = 30

# -----------------------------------------------------------------------------
# V2.12: SPREAD EXIT BUG FIXES (Pitfalls #5-9 from AAP Audit)
# -----------------------------------------------------------------------------
# V2.12 Fix #3: Hard cap on spread contracts to prevent position accumulation
# Evidence: V2.11 backtest showed qty=-80 (5× intended) from exit signal bug
SPREAD_MAX_CONTRACTS = 15  # V12.4: align VASS spread sizing cap with reduced tail-risk objective
SPREAD_MAX_CONTRACTS_HARD_CAP = 15  # V12.4: enforce max 15 spread contracts per VASS entry

# V5.3: Options Position Limits (Margin Error Prevention)
# Max concurrent positions: 2 intraday + 5 swings = 7 total
# V12.15: DEPRECATED for slot gating — shared single-leg cap removed from can_enter_single_leg().
# Lane caps remain: ITM_MAX_CONCURRENT_POSITIONS, MICRO_MAX_CONCURRENT_POSITIONS.
# Kept for backward compatibility (not read by slot gate path).
OPTIONS_MAX_INTRADAY_POSITIONS = 2
OPTIONS_MAX_SWING_POSITIONS = 4  # V10.9: reduce concentration risk
OPTIONS_MAX_TOTAL_POSITIONS = 7  # 2 intraday + up to 5 swings

# V12.15: Regime-adaptive total position cap (optional — OFF by default)
# When disabled: total cap is always OPTIONS_MAX_TOTAL_POSITIONS.
# When enabled: adapts using regime score (REGIME_NEUTRAL, REGIME_DEFENSIVE),
# transition overlay (DETERIORATION/AMBIGUOUS), and fast stress overlay (STRESS/EARLY_STRESS).
OPTIONS_REGIME_ADAPTIVE_TOTAL_CAP_ENABLED = False
OPTIONS_TOTAL_CAP_BULLISH = 7  # score >= REGIME_NEUTRAL (50) → full deployment
OPTIONS_TOTAL_CAP_NEUTRAL = 5  # score >= REGIME_DEFENSIVE (35) OR EARLY_STRESS → moderate
OPTIONS_TOTAL_CAP_BEARISH = 4  # score < REGIME_DEFENSIVE (35) → conservative
OPTIONS_TOTAL_CAP_DETERIORATION = (
    3  # DETERIORATION/AMBIGUOUS overlay OR STRESS → capital protection
)

OPTIONS_MAX_SWING_PER_DIRECTION = 3  # Legacy fallback cap if directional pools are unset
# V8: Separate directional swing pools (prevents one side from monopolizing all swing slots).
OPTIONS_MAX_SWING_BULLISH_POSITIONS = 3
OPTIONS_MAX_SWING_BEARISH_POSITIONS = 3
MAX_BULLISH_SPREADS_STRESS = 0  # No new bullish spreads in confirmed stress
MAX_BULLISH_SPREADS_EARLY_STRESS = 2  # V10.5: keep restrictive but not crippling in early stress
MAX_BEARISH_SPREADS_STRESS = 3  # Preserve bearish spread capacity in stress

# V2.14 Fix #22: Conservative spread sizing to prevent tier cap violations
# Evidence: Trade #20 sized at mid price $2.75 but filled at $3.96 (44% slippage)
# Solution: Use ASK/BID prices + buffer for worst-case sizing
SPREAD_SIZING_SLIPPAGE_BUFFER = 0.10  # 10% buffer on top of ASK/BID pricing

# -----------------------------------------------------------------------------
# V2.1.1 VIX DIRECTION THRESHOLDS (Micro Regime Engine)
# -----------------------------------------------------------------------------
# VIX direction is THE key differentiator for intraday trading
# Same VIX level + different direction = OPPOSITE strategies

# V6.6: Narrowed STABLE zone from ±2% to ±1% based on 2022H1 backtest analysis
# Data showed 76% of UVXY moves were in ±2% range, blocking most signals
# New thresholds capture more directional signals while filtering noise
VIX_DIRECTION_FALLING_FAST = -3.0  # V6.6: Was -5.0, now -3% for earlier detection
VIX_DIRECTION_FALLING = -1.0  # V6.6: Was -2.0, aligned with new STABLE boundary
VIX_DIRECTION_STABLE_LOW = -1.0  # V6.6: Was -2.0, narrowed to capture more signals
VIX_DIRECTION_STABLE_HIGH = 1.0  # V6.6: Was +2.0, narrowed to capture more signals
# V6.9: VIX-adaptive STABLE band (for Dir=None tuning)
VIX_STABLE_LOW_VIX_MAX = 15.0  # Low VIX regime
VIX_STABLE_HIGH_VIX_MIN = 25.0  # High VIX regime
# Runtime hardening: default to Index subscription to avoid intermittent
# CBOE custom-data reader failures on QC cloud nodes.
VIX_DATA_SOURCE = "INDEX"  # INDEX or CBOE
VIX_STALE_MAX_SESSIONS = 3  # V10.8: stale CBOE feed threshold (sessions)
VIX_STALE_LEVEL_FALLBACK_ENABLED = True  # Blend intraday proxy when stale
VIX_STALE_LEVEL_FALLBACK_BLEND = 0.35  # 35% proxy / 65% last reliable CBOE level
VIX_STABLE_BAND_LOW = 0.3  # V6.22: tighter band so more low-vol VIX moves register as directional
VIX_STABLE_BAND_HIGH = 0.8  # V8.2 MICRO revive: reduce WHIPSAW / false non-direction blocks

VIX_DIRECTION_RISING = 3.0  # V6.6: Was +5.0, captures +2.7% cluster (46 observations)
VIX_DIRECTION_RISING_FAST = 6.0  # V6.6: Was +10.0, earlier panic detection
VIX_DIRECTION_SPIKING = 10.0  # VIX change > +10%: Crash mode

# Whipsaw detection: Range > threshold × net change
VIX_WHIPSAW_RATIO = 3.0  # Range/NetChange threshold
VIX_WHIPSAW_MIN_RANGE = 5.0  # Minimum range % to consider whipsaw

# V5.3: Micro Conviction Engine Thresholds
# Micro tracks intraday UVXY and VIX to override Macro when signals are extreme
# V6.4: Lowered BEARISH threshold from 8% to 5% to capture more crash signals
# Analysis showed Jan 21 (+6.9%), Jan 24 (+7.4%), Jan 25 (+5.3%) missed by narrow margin
# V6.6: Lowered from ±5% to ±3% based on 2022H1 analysis
# Only 8% of moves exceeded ±5%, missing many valid conviction signals
MICRO_UVXY_BEARISH_THRESHOLD = 0.030  # V10.10: relax PUT conviction gate (easier bearish trigger)
MICRO_UVXY_BULLISH_THRESHOLD = -0.035  # V10.10: relax CALL conviction gate (easier bullish trigger)
# V6.10: Lower conviction extreme to capture 5-7% moves that were blocked
MICRO_UVXY_CONVICTION_EXTREME = 0.030  # Slightly easier extreme conviction trigger
MICRO_CONVICTION_CONFLICT_MULT = (
    1.50  # V10.5: require stronger UVXY shock when conviction conflicts with micro direction
)
# V6.10: Micro fallback + confirmation thresholds (Dir=None tuning)
MICRO_SCORE_BULLISH_CONFIRM = 42.0  # Legacy fallback (deprecated by VIX-tier confirm thresholds)
MICRO_SCORE_BEARISH_CONFIRM = 50.0  # Legacy fallback (deprecated by VIX-tier confirm thresholds)
MICRO_SCORE_BULLISH_CONFIRM_LOW_VIX = 48.0
MICRO_SCORE_BEARISH_CONFIRM_LOW_VIX = 55.0
MICRO_SCORE_BULLISH_CONFIRM_HIGH_VIX = 45.0
MICRO_SCORE_BEARISH_CONFIRM_HIGH_VIX = 44.0
MICRO_SCORE_HIGH_VIX_MIN = 25.0
MICRO_SCORE_LOW_VIX_MAX = 18.0
# V10.16 compatibility aliases consumed by resolver helpers.
MICRO_SCORE_BULLISH_HIGH_VIX_MIN = MICRO_SCORE_HIGH_VIX_MIN
MICRO_SCORE_BEARISH_HIGH_VIX_MIN = MICRO_SCORE_HIGH_VIX_MIN
MICRO_SCORE_BULLISH_LOW_VIX_MAX = MICRO_SCORE_LOW_VIX_MAX
MICRO_SCORE_BEARISH_LOW_VIX_MAX = MICRO_SCORE_LOW_VIX_MAX
MICRO_VIX_CALM_SCORE_LOW_VIX = 15
MICRO_VIX_CALM_SCORE_DEFAULT = 25
INTRADAY_QQQ_FALLBACK_MIN_MOVE = 0.08  # V8.2 MICRO revive: reduce fallback move floor
MICRO_VIX_CRISIS_LEVEL = 35  # VIX > 35 → CRISIS (BEARISH conviction)
MICRO_VIX_COMPLACENT_LEVEL = 12  # VIX < 12 → COMPLACENT (BULLISH conviction)

# Micro states that trigger conviction (must match MicroRegime enum values)
# Bearish: VIX rising/spiking states → expect downside
MICRO_BEARISH_STATES = ["FULL_PANIC", "CRASH", "WORSENING_HIGH", "BREAKING", "DETERIORATING"]
# Bullish: VIX falling states → expect upside
MICRO_BULLISH_STATES = ["PERFECT_MR", "GOOD_MR", "IMPROVING", "PANIC_EASING", "CALMING"]

# -----------------------------------------------------------------------------
# V2.1.1 VIX LEVEL THRESHOLDS
# -----------------------------------------------------------------------------
VIX_LEVEL_LOW_MAX = 20  # VIX < 20: Normal, mean reversion works
VIX_LEVEL_MEDIUM_MAX = 25  # VIX 20-25: Caution zone
# VIX > 25: Elevated, momentum dominates

# -----------------------------------------------------------------------------
# V2.1.1 MICRO REGIME SCORE COMPONENTS
# -----------------------------------------------------------------------------
# Score range: -15 to 100

# VIX Level Score (0-25 points)
# V2.3.11: Adjusted thresholds - VIX_LEVEL_VERY_CALM_MAX lowered from 15 → 11.5
MICRO_SCORE_VIX_VERY_CALM = 25  # VIX < 11.5 (V2.3.11: was < 15)
MICRO_SCORE_VIX_CALM = 20  # VIX 11.5-15 (V2.3.11: was 15-18)
MICRO_SCORE_VIX_NORMAL = 15  # VIX 15-18 (V2.3.11: shifted from 18-20)
MICRO_SCORE_VIX_ELEVATED = 10  # VIX 18-22
MICRO_SCORE_VIX_HIGH = 5  # VIX 22-25
MICRO_SCORE_VIX_EXTREME = 0  # VIX > 25

# VIX Direction Score (-10 to +20 points)
MICRO_SCORE_DIR_FALLING_FAST = 20  # Fear easing rapidly
MICRO_SCORE_DIR_FALLING = 15  # Fear easing
MICRO_SCORE_DIR_STABLE = 10  # Neutral
MICRO_SCORE_DIR_RISING = 5  # Fear building
MICRO_SCORE_DIR_RISING_FAST = 0  # Fear accelerating
MICRO_SCORE_DIR_SPIKING = -5  # Panic mode penalty
MICRO_SCORE_DIR_WHIPSAW = -10  # Chaos penalty

# QQQ Move Magnitude Score (0-20 points)
MICRO_SCORE_MOVE_TINY = 5  # |Move| < 0.3%: No signal
MICRO_SCORE_MOVE_BUILDING = 10  # |Move| 0.3-0.5%
MICRO_SCORE_MOVE_APPROACHING = 15  # |Move| 0.5-0.8%
MICRO_SCORE_MOVE_TRIGGER = 20  # |Move| 0.8-1.25%: Sweet spot
MICRO_SCORE_MOVE_EXTENDED = 15  # |Move| > 1.25%: Caution

# Move Velocity Score (0-15 points)
MICRO_SCORE_VELOCITY_GRADUAL = 15  # > 2 hours: Sustainable
MICRO_SCORE_VELOCITY_MODERATE = 10  # 1-2 hours: Normal
MICRO_SCORE_VELOCITY_FAST = 5  # 30-60 min: Exhaustion risk
MICRO_SCORE_VELOCITY_SPIKE = 0  # < 30 min: News-driven

# Score Thresholds for Strategy Selection
MICRO_SCORE_PRIME_MR = 80  # 80+: Prime mean reversion
MICRO_SCORE_GOOD_MR = 60  # 60-79: Good mean reversion
MICRO_SCORE_MODERATE = 40  # 40-59: Moderate/mixed
MICRO_SCORE_WEAK = 20  # 20-39: Momentum leaning
MICRO_SCORE_MOMENTUM = 0  # 0-19: Strong momentum
# < 0: Chaos/danger

# -----------------------------------------------------------------------------
# V2.1.1 TIERED VIX MONITORING SYSTEM
# -----------------------------------------------------------------------------
VIX_MONITOR_SPIKE_INTERVAL = 5  # Layer 1: Spike detection every 5 minutes
VIX_MONITOR_SPIKE_THRESHOLD = 5.0  # VIX move > 5% in 5 min = spike alert
VIX_MONITOR_SPIKE_COOLDOWN = 10  # Wait 10 min after spike before entries

VIX_MONITOR_DIRECTION_INTERVAL = 15  # Layer 2: Direction every 15 min
VIX_MONITOR_WHIPSAW_INTERVAL = 60  # Layer 3: Whipsaw rolling 1-hour window
VIX_MONITOR_REGIME_INTERVAL = 30  # Layer 4: Regime classification every 30 min

# Whipsaw reversal thresholds
VIX_REVERSAL_THRESHOLD = 0.1  # Ignore VIX moves < 0.1 (noise)
VIX_REVERSAL_TRENDING = 2  # 0-2 reversals: Trending
VIX_REVERSAL_CHOPPY = 4  # 3-4 reversals: Choppy
# 5+ reversals: Whipsaw

# -----------------------------------------------------------------------------
# V2.1.1 INTRADAY STRATEGY PARAMETERS
# -----------------------------------------------------------------------------

# V2.3.16: Sniper Logic - Noise Filter (Gate 1)
QQQ_NOISE_THRESHOLD = 0.04  # D7: Small relaxation to reduce QQQ_FLAT over-blocking
CAUTION_LOW_SIZE_MULT = 0.50  # V6.22: keep trading in CAUTION_LOW but with reduced exposure
TRANSITION_SIZE_MULT = 0.50  # V9: tradeable with reduced exposure (same as CAUTION_LOW)

# D10: Per-expiry concentration cap for spread ladders.
SPREAD_EXPIRY_CONCENTRATION_CAP_ENABLED = True
SPREAD_MAX_PER_EXPIRY = 2
SPREAD_MAX_BULLISH_PER_EXPIRY = 2
SPREAD_MAX_BEARISH_PER_EXPIRY = 2
SPREAD_MAX_BULLISH_PER_EXPIRY_BULL_PROFILE = (
    2  # Allow denser bullish ladders in clean bull profiles
)
SPREAD_EXPIRY_BULL_PROFILE_REGIME_MIN = 70.0
SPREAD_EXPIRY_BULL_PROFILE_VIX_MAX = 18.0

# V6.14 OPT: Bear-market PUT risk controls.
PUT_ENTRY_VIX_MAX = 38.0  # Allow more participation before panic cap
PUT_SIZE_REDUCTION_VIX_START = 32.0  # Delay PUT size haircut for trend capture
PUT_SIZE_REDUCTION_FACTOR = 0.60  # Less aggressive downsizing above threshold
INTRADAY_CALL_BLOCK_VIX_MIN = 20.0  # V10.29: block CALLs earlier as fear rises into medium-high VIX
INTRADAY_CALL_BLOCK_REGIME_MAX = 55.0  # V10.9: reduce over-blocking in neutral-to-bull macro
# Additional minimal CALL-protection gates (bear-risk controls without major architecture changes)
CALL_GATE_MA20_ENABLED = True  # Block CALL entries when QQQ is below its 20-day SMA
CALL_GATE_MA20_BYPASS_REGIME_MIN = 65.0  # V10.18: allow bypass only in stronger bull tapes
CALL_GATE_MA20_BYPASS_VIX_MAX = 16.0  # V10.18: tighter low-fear requirement for CALL bypass
CALL_GATE_MA20_BYPASS_SIZE_MULT = 0.90  # V10.8: lighter haircut when bypass is valid
CALL_GATE_VIX_5D_RISING_ENABLED = True  # Block CALL entries when 5-day VIX trend is rising
CALL_GATE_VIX_5D_RISING_PCT = 0.14  # Legacy fallback threshold
CALL_GATE_VIX_5D_RISING_ENABLED_LOW_VIX = False  # V10.9: disable low-VIX VIX5d call gate
CALL_GATE_VIX_5D_RISING_ENABLED_MED_VIX = True
CALL_GATE_VIX_5D_RISING_ENABLED_HIGH_VIX = True
CALL_GATE_VIX_5D_RISING_PCT_LOW_VIX = 0.25  # If enabled, require very strong fear acceleration
CALL_GATE_VIX_5D_RISING_PCT_MED_VIX = 0.14  # Medium-VIX baseline
CALL_GATE_VIX_5D_RISING_PCT_HIGH_VIX = 0.10  # Stricter in high-VIX tape
CALL_GATE_VIX_5D_MIN_VIX = 20.0  # Legacy key retained for backward compatibility
CALL_GATE_CONSECUTIVE_LOSS_ENABLED = True  # Pause CALL entries after repeated losses
CALL_GATE_CONSECUTIVE_LOSSES = 2  # V10.29: trigger CALL cooldown sooner after loss clustering
CALL_GATE_LOSS_COOLDOWN_DAYS = 1  # Legacy fallback for cooldown days
CALL_GATE_LOSS_COOLDOWN_DAYS_LOW_VIX = 2  # V10.28: reduce repeat CALL churn after loss clusters
CALL_GATE_LOSS_COOLDOWN_DAYS_MED_VIX = 2  # V10.28: reduce repeat CALL churn after loss clusters
CALL_GATE_LOSS_COOLDOWN_DAYS_HIGH_VIX = 2  # V10.9: conservative cooldown in high-vol tape

# Symmetric PUT cooldown controls (mirror CALL defaults)
PUT_GATE_CONSECUTIVE_LOSS_ENABLED = True
PUT_GATE_CONSECUTIVE_LOSSES = 3
PUT_GATE_LOSS_COOLDOWN_DAYS = 1
PUT_GATE_LOSS_COOLDOWN_DAYS_LOW_VIX = 1
PUT_GATE_LOSS_COOLDOWN_DAYS_MED_VIX = 1
PUT_GATE_LOSS_COOLDOWN_DAYS_HIGH_VIX = 2

# V2.19: VIX Floor for DEBIT_FADE
# In low VIX (<13.5) "apathy" markets, mean reversion fails - trends persist longer
# Evidence: V2.18 backtests showed DEBIT_FADE losses when VIX < 13.5
INTRADAY_DEBIT_FADE_VIX_MIN = 12.0  # Legacy alias (kept for compatibility)
MICRO_DEBIT_FADE_VIX_MIN = 12.0  # Canonical: ATM fade minimum VIX

# Debit Fade (Mean Reversion) - Gate 3a - The Sniper Window
INTRADAY_FADE_MIN_MOVE = 0.35  # Restore intraday participation while keeping noise filter
# V10: VIX-tier move gates (replace single INTRADAY_FADE_MIN_MOVE for MICRO routing)
MICRO_MIN_MOVE_LOW_VIX = 0.50  # Stricter for LOW VIX — filter theta-dominated noise
MICRO_MIN_MOVE_MED_VIX = 0.45  # V10.30: filter borderline momentum noise in medium VIX
MICRO_MIN_MOVE_HIGH_VIX = 0.40  # Standard move gate
# V12.0: ATR-indexed move gate (can relax fixed gates in quiet tape).
MICRO_ATR_MIN_MOVE_ENABLED = True
MICRO_ATR_MIN_MOVE_MULTIPLIER = 0.50
MICRO_ATR_MIN_MOVE_FLOOR_PCT = 0.12
MICRO_ATR_MIN_MOVE_CAP_PCT = 0.60
INTRADAY_FADE_MAX_MOVE = 1.50  # V6.8: Was 1.20, don't block strong bull continuation
INTRADAY_DEBIT_FADE_START = "10:00"  # Legacy alias (kept for compatibility)
INTRADAY_DEBIT_FADE_END = "14:30"  # Legacy alias (kept for compatibility)
MICRO_DEBIT_FADE_START = "10:00"  # Canonical ATM fade window start
MICRO_DEBIT_FADE_END = "14:30"  # Canonical ATM fade window end
MICRO_DEBIT_FADE_DTE_MIN = 0  # Strategy-specific fade horizon (same-day/next-day)
MICRO_DEBIT_FADE_DTE_MAX = 2
MICRO_OTM_MOMENTUM_START = "10:10"  # V10.32: avoid early-open noise burst
MICRO_OTM_MOMENTUM_END = "14:30"  # Canonical OTM momentum window end
MICRO_OTM_MOMENTUM_DTE_MIN = 1  # V12.16: directional OTM momentum is 1DTE-only
MICRO_OTM_MOMENTUM_DTE_MAX = 1
MICRO_OTM_MAX_ENTRIES_PER_SESSION = 3
MICRO_OTM_DTE0_MAX_CONTRACTS = 3
MICRO_OTM_DTE1_MAX_CONTRACTS = 6
MICRO_OTM_DTE2_MAX_CONTRACTS = 8
MICRO_OTM_TRANSITION_BLOCK_ENABLED = True
MICRO_OTM_TRANSITION_BLOCK_OVERLAYS = ("DETERIORATION", "RECOVERY")
MICRO_OTM_TRANSITION_BLOCK_BARS = 4
MICRO_OTM_0DTE_LATE_ENTRY_BLOCK_ENABLED = True
MICRO_OTM_0DTE_LATE_ENTRY_BLOCK_START = "13:45"
MICRO_OTM_ADAPTIVE_CONCURRENT_CAP_ENABLED = True
MICRO_OTM_MAX_CONCURRENT_POSITIONS_BASE = 1
MICRO_OTM_MAX_CONCURRENT_POSITIONS_LOW_VIX_CALL = 2
MICRO_OTM_CONCURRENT_CAP_LOW_VIX_MAX = 16.0
MICRO_OTM_CONCURRENT_CAP_RECOVERY_MIN_BARS = 6
MICRO_OTM_STRESS_SOFT_GATE_ENABLED = True
MICRO_OTM_STRESS_SOFT_MAX_VIX = 22.5
MICRO_OTM_STRESS_SOFT_SIZE_MULT = 0.60
MICRO_OTM_STRESS_SOFT_ALLOW_OVERLAYS = ("STABLE", "RECOVERY")
INTRADAY_DEBIT_SPREAD_WIDTH = 2.00  # $2.00 spread width
INTRADAY_DEBIT_FULL_SIZE = 4  # Full size: 4 spreads
INTRADAY_DEBIT_HALF_SIZE = 2  # Half size: 2 spreads

# Credit Spreads (Premium Collection)
INTRADAY_CREDIT_MIN_VIX = 18  # VIX >= 18 for rich premium
INTRADAY_CREDIT_MAX_MOVE = 1.5  # QQQ move < 1.5%
INTRADAY_CREDIT_START = "10:00"  # Entry window start
INTRADAY_CREDIT_END = "14:30"  # Entry window end
INTRADAY_CREDIT_SPREAD_WIDTH = 2.00  # $2.00 spread width
INTRADAY_CREDIT_TARGET = 0.50  # 50% of max profit target
INTRADAY_CREDIT_STOP = 1.0  # Stop if spread doubles

# ITM Momentum
INTRADAY_ITM_MIN_VIX = 9.0  # V6.13 OPT: Allow ITM momentum in low-VIX bull regimes
INTRADAY_ITM_MIN_MOVE = 0.40  # V6.13 OPT: Increase momentum setup availability
# V2.3.19: Time window moved from hardcoded to config
INTRADAY_ITM_START = "10:00"  # Entry window start
INTRADAY_ITM_END = "14:30"  # Entry window end (earlier than FADE - momentum fades after lunch)
INTRADAY_ITM_DELTA = 0.70  # ITM delta target
INTRADAY_ITM_TARGET = 0.80  # V10.8: trailing stop is primary exit; target is distant ceiling
# Production friction caps (single-leg options): tighter than global defaults.
INTRADAY_ITM_MAX_BID_ASK_SPREAD_PCT = 0.12
INTRADAY_MICRO_MAX_BID_ASK_SPREAD_PCT = 0.10
INTRADAY_MICRO_OTM_MAX_BID_ASK_SPREAD_PCT = 0.09
INTRADAY_MICRO_OTM_MAX_BID_ASK_SPREAD_PCT_0DTE = 0.08
INTRADAY_MICRO_OTM_MAX_BID_ASK_SPREAD_PCT_LATE_DAY = 0.08
INTRADAY_MICRO_OTM_MAX_BID_ASK_SPREAD_PCT_STRESS = 0.08
INTRADAY_MICRO_OTM_MAX_BID_ASK_SPREAD_PCT_EARLY_TRANSITION = 0.07
INTRADAY_MICRO_OTM_FRICTION_LATE_START = "13:30"
INTRADAY_MICRO_OTM_FRICTION_STRESS_VIX = 20.0

# V6.4: DEBIT_MOMENTUM time window (same as ITM_MOMENTUM - both are momentum strategies)
INTRADAY_DEBIT_MOMENTUM_START = "10:00"  # Entry window start
INTRADAY_DEBIT_MOMENTUM_END = "14:30"  # Entry window end
INTRADAY_ITM_STOP = 0.50  # V12.13: legacy fallback aligned to multi-day (was 0.40)
INTRADAY_ITM_STOP_FLOOR_MED_VIX = 0.45  # V10.8: wider ITM stop floor in medium VIX
INTRADAY_ITM_STOP_FLOOR_HIGH_VIX = 0.50  # V10.8: wider ITM stop floor in high VIX
INTRADAY_HIGH_VIX_STOP_MAX_PCT = (
    0.55  # V10.8: allow high-VIX ITM stops to breathe on multi-day holds
)
INTRADAY_ITM_TRAIL_TRIGGER = 0.22  # V10.8: start protecting gains earlier on held ITM trades
INTRADAY_ITM_TRAIL_PCT = 0.32  # V10.8: retain more profits once trail is active

# V9.2: Per-strategy intraday exits (previously universal target/stop)
INTRADAY_DEBIT_FADE_TARGET = 0.35  # V10.10: improve DEBIT_FADE R:R for 1-2 DTE
INTRADAY_DEBIT_FADE_STOP = 0.25
INTRADAY_DEBIT_FADE_TRAIL_TRIGGER = 0.25  # V9.8: revert to V9.3 (below 0.40 target, trail works)
INTRADAY_DEBIT_FADE_TRAIL_PCT = 0.30

# Canonical MICRO strategy exits (legacy INTRADAY_DEBIT_FADE_* kept as aliases)
MICRO_DEBIT_FADE_TARGET = INTRADAY_DEBIT_FADE_TARGET
MICRO_DEBIT_FADE_TARGET_0DTE = 0.25  # 0DTE fade has shorter runway before noon force-close
MICRO_DEBIT_FADE_STOP = INTRADAY_DEBIT_FADE_STOP
MICRO_DEBIT_FADE_STOP_0DTE = 0.20  # 0DTE fade: tighter risk due to shorter thesis window
MICRO_DEBIT_FADE_TRAIL_TRIGGER = INTRADAY_DEBIT_FADE_TRAIL_TRIGGER
MICRO_DEBIT_FADE_TRAIL_PCT = INTRADAY_DEBIT_FADE_TRAIL_PCT
MICRO_DEBIT_FADE_DTE_EXIT = 0  # Let intraday force-exit manage same-day lifecycle
MICRO_DEBIT_FADE_MAX_HOLD_MINUTES = 70  # V10.34: mean-reversion thesis should resolve quickly
MICRO_DEBIT_FADE_MAX_HOLD_PROFIT_EXEMPT_PCT = (
    0.20  # Keep strong winners; cut slower fades before theta dominates
)
MICRO_OTM_MOMENTUM_TARGET = 0.75
MICRO_OTM_MOMENTUM_STOP = 0.30
MICRO_OTM_MOMENTUM_TRAIL_TRIGGER = 0.30
MICRO_OTM_MOMENTUM_TRAIL_PCT = 0.50
MICRO_OTM_MOMENTUM_DTE_EXIT = 0  # 0/1 DTE strategy closes via intraday force-exit

# MICRO OTM momentum tiered risk profile (V12.16 PUT-convex profile)
MICRO_OTM_TIERED_RISK_ENABLED = True
MICRO_OTM_FIXED_STOP_OVERRIDE_ENABLED = (
    False  # Keep ATR stop sovereign for OTM momentum unless explicitly forcing fixed-tier stops
)
MICRO_OTM_VIX_LOW_MAX = 16.0
MICRO_OTM_VIX_MED_MAX = 22.0
MICRO_OTM_TARGET_LOW_VIX = 0.55
MICRO_OTM_TARGET_MED_VIX = 0.70
MICRO_OTM_TARGET_HIGH_VIX = 0.90
MICRO_OTM_STOP_LOW_VIX = 0.25
MICRO_OTM_STOP_MED_VIX = 0.30
MICRO_OTM_STOP_HIGH_VIX = 0.38
MICRO_OTM_TRAIL_TRIGGER_LOW_VIX = 0.45
MICRO_OTM_TRAIL_TRIGGER_MED_VIX = 0.55
MICRO_OTM_TRAIL_TRIGGER_HIGH_VIX = 0.65
MICRO_OTM_TRAIL_PCT_LOW_VIX = 0.25
MICRO_OTM_TRAIL_PCT_MED_VIX = 0.30
MICRO_OTM_TRAIL_PCT_HIGH_VIX = 0.35
# V12.19: Directional OTM CALL profile should buy more delta and
# use tighter risk controls than PUT convexity profiles.
MICRO_OTM_CALL_TARGET_LOW_VIX = 0.40
MICRO_OTM_CALL_TARGET_MED_VIX = 0.55
MICRO_OTM_CALL_TARGET_HIGH_VIX = 0.70
MICRO_OTM_CALL_STOP_LOW_VIX = 0.22
MICRO_OTM_CALL_STOP_MED_VIX = 0.25
MICRO_OTM_CALL_STOP_HIGH_VIX = 0.30
MICRO_OTM_CALL_TRAIL_TRIGGER_LOW_VIX = 0.30
MICRO_OTM_CALL_TRAIL_TRIGGER_MED_VIX = 0.40
MICRO_OTM_CALL_TRAIL_TRIGGER_HIGH_VIX = 0.50
MICRO_OTM_CALL_TRAIL_PCT_LOW_VIX = 0.30
MICRO_OTM_CALL_TRAIL_PCT_MED_VIX = 0.35
MICRO_OTM_CALL_TRAIL_PCT_HIGH_VIX = 0.40
# V12.16: explicit PUT-profile aliases (directional OTM path is PUT-only by policy).
MICRO_OTM_PUT_TARGET_LOW_VIX = MICRO_OTM_TARGET_LOW_VIX
MICRO_OTM_PUT_TARGET_MED_VIX = MICRO_OTM_TARGET_MED_VIX
MICRO_OTM_PUT_TARGET_HIGH_VIX = MICRO_OTM_TARGET_HIGH_VIX
MICRO_OTM_PUT_STOP_LOW_VIX = MICRO_OTM_STOP_LOW_VIX
MICRO_OTM_PUT_STOP_MED_VIX = MICRO_OTM_STOP_MED_VIX
MICRO_OTM_PUT_STOP_HIGH_VIX = MICRO_OTM_STOP_HIGH_VIX
MICRO_OTM_PUT_TRAIL_TRIGGER_LOW_VIX = MICRO_OTM_TRAIL_TRIGGER_LOW_VIX
MICRO_OTM_PUT_TRAIL_TRIGGER_MED_VIX = MICRO_OTM_TRAIL_TRIGGER_MED_VIX
MICRO_OTM_PUT_TRAIL_TRIGGER_HIGH_VIX = MICRO_OTM_TRAIL_TRIGGER_HIGH_VIX
MICRO_OTM_PUT_TRAIL_PCT_LOW_VIX = MICRO_OTM_TRAIL_PCT_LOW_VIX
MICRO_OTM_PUT_TRAIL_PCT_MED_VIX = MICRO_OTM_TRAIL_PCT_MED_VIX
MICRO_OTM_PUT_TRAIL_PCT_HIGH_VIX = MICRO_OTM_TRAIL_PCT_HIGH_VIX
# V12.16: keep ATR stop sovereign but cap realized OTM PUT stop by VIX tier.
MICRO_OTM_STOP_CAP_LOW_VIX = 0.25
MICRO_OTM_STOP_CAP_MED_VIX = 0.30
MICRO_OTM_STOP_CAP_HIGH_VIX = 0.38
MICRO_OTM_CALL_STOP_CAP_LOW_VIX = 0.22
MICRO_OTM_CALL_STOP_CAP_MED_VIX = 0.25
MICRO_OTM_CALL_STOP_CAP_HIGH_VIX = 0.30
MICRO_OTM_SIZE_MULT_LOW_VIX = 0.85
MICRO_OTM_SIZE_MULT_MED_VIX = 0.85
MICRO_OTM_SIZE_MULT_HIGH_VIX = 0.60
# Risk budget per trade (portfolio-based) for directional OTM.
# Contracts are capped by BOTH premium budget and stop-risk budget.
MICRO_OTM_MAX_RISK_PCT_OF_PORTFOLIO = 0.010
MICRO_OTM_CALL_MAX_RISK_PCT_OF_PORTFOLIO = 0.008
MICRO_OTM_PUT_MAX_RISK_PCT_OF_PORTFOLIO = 0.010
MICRO_OTM_MAX_HOLD_MINUTES = 80  # V10.33: cap 0-1DTE theta bleed window
MICRO_OTM_MAX_HOLD_MINUTES_0DTE = 65
MICRO_OTM_MAX_HOLD_MINUTES_1DTE = 80
MICRO_OTM_MAX_HOLD_PROFIT_EXEMPT_PCT = 0.35  # Let strong runners bypass max-hold
# V12.16: tiered max-hold windows for directional OTM PUT.
MICRO_OTM_MAX_HOLD_MINUTES_0DTE_LOW_VIX = 45
MICRO_OTM_MAX_HOLD_MINUTES_0DTE_MED_VIX = 50
MICRO_OTM_MAX_HOLD_MINUTES_0DTE_HIGH_VIX = 60
MICRO_OTM_MAX_HOLD_MINUTES_1DTE_LOW_VIX = 60
MICRO_OTM_MAX_HOLD_MINUTES_1DTE_MED_VIX = 75
MICRO_OTM_MAX_HOLD_MINUTES_1DTE_HIGH_VIX = 90
MICRO_STAGNATION_EXIT_ENABLED = True  # V10.11: close flat MICRO positions that stall intraday
MICRO_STAGNATION_MIN_HOLD_MINUTES = 60  # Require at least 60 minutes before stagnation check
MICRO_STAGNATION_FLAT_BAND_PCT = 0.10  # Treat +/-10% as flat for intraday MICRO exits

INTRADAY_DEBIT_MOMENTUM_TARGET = (
    0.45  # V9.8: revert to V9.3 (0.25 was within bid-ask noise on $0.34 options)
)
INTRADAY_DEBIT_MOMENTUM_STOP = 0.30  # V9.8: revert to V9.3
INTRADAY_DEBIT_MOMENTUM_TRAIL_TRIGGER = (
    0.20  # V9.8: revert to V9.3 (below 0.45 target, trail works)
)
INTRADAY_DEBIT_MOMENTUM_TRAIL_PCT = 0.50  # Standard 50% retracement from peak

# V2.15: Strategy-aware intraday delta bounds
# DEBIT_FADE: Mean reversion needs OTM options (delta 0.20-0.50)
INTRADAY_DEBIT_FADE_DELTA_MIN = 0.20  # Legacy alias
INTRADAY_DEBIT_FADE_DELTA_MAX = 0.50  # Legacy alias
MICRO_DEBIT_FADE_DELTA_TARGET = 0.45  # ATM fade target delta
MICRO_DEBIT_FADE_DELTA_MIN = 0.42
MICRO_DEBIT_FADE_DELTA_MAX = 0.52
MICRO_OTM_MOMENTUM_DELTA_MIN = 0.35
MICRO_OTM_MOMENTUM_DELTA_MAX = 0.50
# V12.19: side-aware directional OTM deltas.
# CALL path should be near ATM/ITM to buy delta on bullish tapes.
MICRO_OTM_CALL_DELTA_MIN = 0.58
MICRO_OTM_CALL_DELTA_MAX = 0.72
MICRO_OTM_PUT_DELTA_MIN = MICRO_OTM_MOMENTUM_DELTA_MIN
MICRO_OTM_PUT_DELTA_MAX = MICRO_OTM_MOMENTUM_DELTA_MAX

# V6.4: DEBIT_MOMENTUM: Trend confirmation needs ATM-ish options (delta 0.45-0.65)
# Between DEBIT_FADE (OTM) and ITM_MOMENTUM (ITM) - captures directional moves
INTRADAY_DEBIT_MOMENTUM_ENABLED = (
    False  # V10: deprecated — ITM_MOMENTUM replaces all confirmation paths
)
MICRO_ENTRY_ENGINE_ENABLED = False  # VASS-only backtest mode: disable MICRO entry engine
INTRADAY_DEBIT_FADE_ENABLED = True  # Legacy alias
MICRO_DEBIT_FADE_ENABLED = False  # V10.35: disable persistent-loss fade path pending redesign
MICRO_OTM_MOMENTUM_ENABLED = True  # Canonical OTM momentum switch
# V12.19: side-specific OTM control.
MICRO_OTM_CALL_ENABLED = True
MICRO_OTM_PUT_ENABLED = True
MICRO_OTM_PUT_REGIME_WHITELIST = ["WORSENING", "WORSENING_HIGH"]
MICRO_OTM_PUT_ALLOW_DETERIORATING_IF_CONFIRMED = True
MICRO_OTM_MOMENTUM_MAX_VIX = (
    35.0  # Trade OTM through high-VIX tier (22-35) with reduced size; block >35
)
MICRO_OTM_MOMENTUM_MIN_MOVE = 0.50  # V10.30: reduce fast-reversal OTM entries in weak tapes
MICRO_OTM_MOMENTUM_MIN_MOVE_CALL = 0.55  # V10.33: require stronger confirmation for CALL momentum
MICRO_OTM_MOMENTUM_MIN_MOVE_PUT = 0.60  # V10.33: require stronger confirmation for PUT momentum
MICRO_OTM_CALL_MIN_MACRO_SCORE = (
    45.0  # V10.39: require at least neutralizing macro strength for bullish OTM momentum
)
MICRO_OTM_PUT_MAX_MACRO_SCORE = (
    45.0  # V10.35: allow bearish OTM only in clearly risk-off macro conditions
)
MICRO_OTM_BULLISH_CONFIRM_SCORE_BUFFER = (
    4.0  # V10.33: CALL momentum needs higher micro-score than baseline confirm gate
)
MICRO_OTM_BEARISH_CONFIRM_SCORE_BUFFER = (
    3.0  # V10.33: PUT momentum needs deeper bearish score before entry
)
MICRO_USE_MACRO_RESOLVER = False  # Deprecated no-op: MICRO macro resolver path removed in V10.10
MICRO_USE_MACRO_IN_STATE = (
    False  # Deprecated no-op: MICRO state no longer consumes macro score in V10.10
)
MICRO_USE_MACRO_POLICY_GATES = (
    False  # Deprecated no-op: macro-based CALL/PUT policy gates removed in V10.10
)
MICRO_MISALIGNED_SIZE_MULT = (
    0.50  # Size haircut when signal direction is misaligned with local context
)
MICRO_TRANSITION_GUARD_ENABLED = True
MICRO_TRANSITION_BLOCK_AMBIGUOUS = True
MICRO_TRANSITION_BLOCK_CALL_ON_DETERIORATION = True
MICRO_TRANSITION_BLOCK_PUT_ON_RECOVERY = True
MICRO_TRANSITION_HANDOFF_THROTTLE_ENABLED = True
INTRADAY_DEBIT_MOMENTUM_DELTA_MIN = 0.45  # Near ATM for momentum
INTRADAY_DEBIT_MOMENTUM_DELTA_MAX = 0.65  # Slightly ITM max
INTRADAY_DEBIT_MOMENTUM_BLOCK_REGIMES = [
    "WORSENING",
    "WORSENING_HIGH",
    "BREAKING",
    "FULL_PANIC",
    "CRASH",
]  # Skip weak/choppy transition states for momentum

# ITM_MOMENTUM: Stock replacement needs ITM options (delta 0.60-0.85)
INTRADAY_ITM_DELTA_MIN = 0.70  # Legacy fallback aligned to ITM_ENGINE stock-replacement profile
INTRADAY_ITM_DELTA_MAX = 0.80  # V10: tightened from 0.85 to avoid deep ITM illiquidity
INTRADAY_ITM_HOLD_OVERNIGHT_ENABLED = True  # V12.13: enable multi-day ITM carry (was False V10.22)
INTRADAY_ITM_HOLD_MIN_ENTRY_DTE = 3  # Only hold if entry was opened with >=3 DTE
INTRADAY_ITM_FORCE_EXIT_DTE = 10  # V12.13: aligned to ITM_FORCE_EXIT_DTE (was 8)
INTRADAY_ITM_DTE_EXIT = 2  # Software DTE exit for ITM single-legs (strategy-specific)
INTRADAY_ALLOW_ONE_LOT_WHEN_CAP_TIGHT = (
    True  # V10.9: reduce avoidable CAP_TOO_SMALL drops for valid MICRO CALLs
)
INTRADAY_ONE_LOT_MAX_PREMIUM = 6.0  # Safety cap for one-lot fallback premium
INTRADAY_ITM_TARGET_DTE_LOW_VIX = 4  # Prefer longer dated ITM in theta-dominated tape
INTRADAY_ITM_TARGET_DTE_MED_VIX = 3
INTRADAY_ITM_TARGET_DTE_HIGH_VIX = 3
INTRADAY_ITM_OI_SOFT_CAP = 2000  # OI normalization cap for ITM contract scoring
INTRADAY_ITM_SCORE_DELTA_WEIGHT = 0.45  # Contract selection scoring weights (sum ~= 1.0)
INTRADAY_ITM_SCORE_DTE_WEIGHT = 0.30
INTRADAY_ITM_SCORE_SPREAD_WEIGHT = 0.20
INTRADAY_ITM_SCORE_OI_WEIGHT = 0.05

# ITM_ENGINE (isolated horizon engine; feature-flagged)
ITM_ENGINE_ENABLED = False  # VASS-only backtest mode: disable ITM engine
ITM_SHADOW_MODE = False
ITM_ALLOW_SOVEREIGN_PROMOTION_FROM_MICRO = False  # Keep MICRO/ITM entry paths separated
ITM_SIZE_MULT = 1.0  # ITM_ENGINE sizing is sovereign; do not couple to MICRO score ladder
ITM_SIZE_MULT_LOW_VIX = 1.00
ITM_SIZE_MULT_MED_VIX = 0.75
ITM_SIZE_MULT_HIGH_VIX = 0.50
MICRO_SIZE_MULT_MID_CONVICTION = 0.75  # MICRO score band 60-79

ITM_DECISION_HOUR = 10
ITM_DECISION_MINUTE = 30
ITM_ENTRY_END = "13:00"
ITM_SMA_BAND_PCT = 0.015  # Legacy fallback (deprecated by VIX-tier bands)
ITM_SMA_BAND_PCT_LOW_VIX = 0.012
ITM_SMA_BAND_PCT_MED_VIX = 0.015
ITM_SMA_BAND_PCT_HIGH_VIX = 0.025
ITM_PUT_MAX_REGIME = 45  # V12.13: only bearish regimes for PUTs (was 70)
ITM_CALL_MIN_REGIME = 62  # V12.13: block CAUTIOUS/CAUTION_LOW/WORSENING (was 50)
ITM_ADX_MIN = 20.0
ITM_CALL_ADX_MIN = 22.0  # V12.18: relaxed from 24 to unlock ADX blackout days
ITM_CALL_MAX_VIX = 20.0  # V10.32: avoid ITM CALLs in elevated fear tape
ITM_CALL_LOW_VIX_PREFERRED = 14.0
ITM_REQUIRE_VIX20D_FALLING_FOR_CALL_WHEN_VIX_ABOVE_LOW = True
ITM_PUT_MIN_VIX = 12.0
ITM_PUT_MAX_VIX = 35.0
ITM_CALL_TRANSITION_MIN_REGIME = 58.0  # V12.13: tighter recovery override (was 48.0)
ITM_PUT_TRANSITION_MAX_REGIME = 50.0  # V12.13: tighter deterioration override (was 62.0)
ITM_TRANSITION_BLOCK_AMBIGUOUS = True
ITM_TRANSITION_BLOCK_BULL_ON_DETERIORATION = True
ITM_TRANSITION_BLOCK_BEAR_ON_RECOVERY = True
ITM_TRANSITION_HANDOFF_THROTTLE_ENABLED = True
HEDGE_TRANSITION_HANDOFF_THROTTLE_ENABLED = True
TRANSITION_HANDOFF_THROTTLE_ENABLED = True
TRANSITION_HANDOFF_BARS = 4
TRANSITION_HANDOFF_HARD_DOWNSIDE_DELTA_MAX = -2.5
TRANSITION_HANDOFF_HARD_DOWNSIDE_MOM_MAX = -0.02
TRANSITION_HANDOFF_HARD_UPSIDE_DELTA_MIN = 2.5
TRANSITION_HANDOFF_HARD_UPSIDE_MOM_MIN = 0.02
TRANSITION_HANDOFF_OPEN_DERISK_ENABLED = True
TRANSITION_HANDOFF_OPEN_DERISK_BARS = 4
VASS_TRANSITION_OPEN_DERISK_BARS = 8
VASS_TRANSITION_DERISK_DEBIT_ENABLED = (
    False  # Thesis mode: do not force-close VASS debit spreads on overlay flips.
)
VASS_TRANSITION_DERISK_CREDIT_ENABLED = True  # Keep faster de-risk for VASS credit spreads.
VASS_TRANSITION_DERISK_CREDIT_RECOVERY_ENABLED = False  # V12.30: disable RECOVERY transition de-risk for credits; avoid premature bear-credit exits.
VASS_TRANSITION_DERISK_CREDIT_RECOVERY_MIN_BARS = (
    3  # Require recovery overlay persistence before de-risking bearish credits.
)
ITM_BLOCK_SAME_DAY_SAME_DIRECTION_REENTRY = True
ITM_BREAKER_3_LOSSES_PAUSE_DAYS = 1
ITM_BREAKER_5_LOSSES_PAUSE_DAYS = 2
ITM_DIRECTIONAL_BREAKER_ENABLED = True
ITM_DIRECTIONAL_BREAKER_3_LOSSES_PAUSE_DAYS = 1
ITM_DD_GATE_ENABLED = False  # Backtest mode: disable ITM drawdown gate
ITM_DD_BLOCK_THRESHOLD = 0.90
ITM_DD_RECOVER_THRESHOLD = 0.95
ITM_DD_LOOKBACK_DAYS = 60
ITM_DELTA_MIN = 0.70
ITM_DELTA_MAX = 0.80
ITM_DTE_MIN = 14
ITM_DTE_MAX = 21
ITM_TARGET_DTE = 17
ITM_MAX_CONCURRENT_POSITIONS = 1
ITM_DTE_DIAG_LOG_INTERVAL_MIN = 30
MICRO_MAX_CONCURRENT_POSITIONS = 1
ITM_MAX_CONTRACTS_HARD_CAP = 30  # V12.13: liquidity ceiling, not risk control (was 6)
ITM_TARGET_PCT = 0.45  # V12.15: restored R:R — aligned to med VIX (was 0.30)
ITM_STOP_PCT = 0.45  # V12.13: legacy fallback aligned to multi-day (was 0.25)
ITM_TRAIL_TRIGGER = 0.32  # V12.15: raised for R:R rebalance (was 0.22)
ITM_TRAIL_PCT = 0.32
ITM_MAX_HOLD_DAYS = 4
ITM_FORCE_EXIT_DTE = 10  # V12.13: 2 extra days from gamma cliff (was 8)
ITM_HOLD_OVERNIGHT_ENABLED = True  # V12.13: enable multi-day ITM carry (was False V10.22)
# ITM weekend/holiday carry guard (targeted protection, avoids blanket quarantine).
ITM_WEEKEND_GUARD_ENABLED = True
ITM_WEEKEND_MIN_LIVE_DTE_TO_HOLD = 10
ITM_WEEKEND_VIX_MAX_TO_HOLD = 22.0
ITM_WEEKEND_VIX_5D_MAX_TO_HOLD = 0.08
ITM_WEEKEND_MIN_PNL_CUSHION_TO_HOLD = 0.10
ITM_WEEKEND_ENTRY_CUTOFF_HOUR = 13
ITM_WEEKEND_ENTRY_CUTOFF_MINUTE = 30
ITM_WEEKEND_CARRY_SIZE_HAIRCUT_ENABLED = True
ITM_WEEKEND_CARRY_SIZE_MULT = 0.70
ITM_WEEKEND_GAP_EXIT_ENABLED = True
ITM_WEEKEND_GAP_ADVERSE_PCT = 0.01
ITM_WEEKEND_GAP_VIX_SHOCK_PCT = 0.15
# V10.10: staged overnight ITM guard
ITM_OVERNIGHT_WARN_LOSS_PCT = 0.10  # Stage A: warning only, keep trade open
ITM_OVERNIGHT_MED_VIX_THRESHOLD = 18.0
ITM_OVERNIGHT_HIGH_VIX_THRESHOLD = 25.0
ITM_OVERNIGHT_EOD_EXIT_LOSS_PCT_LOW_VIX = 0.35  # V12.13: early warning below stop (was 0.15)
ITM_OVERNIGHT_EOD_EXIT_LOSS_PCT_MED_VIX = 0.40  # V12.13: early warning below stop (was 0.18)
ITM_OVERNIGHT_EOD_EXIT_LOSS_PCT_HIGH_VIX = 0.45  # V12.13: early warning below stop (was 0.22)
ITM_OVERNIGHT_EOD_EXIT_REQUIRE_THESIS_BREAK = True
ITM_OVERNIGHT_EMERGENCY_LOSS_PCT = 0.50  # V12.13: aligned to med-VIX stop (was 0.28)
# Legacy aliases (kept for compatibility)
ITM_OVERNIGHT_MAX_LOSS_PCT = ITM_OVERNIGHT_EOD_EXIT_LOSS_PCT_MED_VIX
ITM_OVERNIGHT_MAX_LOSS_PCT_HIGH_VIX = ITM_OVERNIGHT_EOD_EXIT_LOSS_PCT_HIGH_VIX

# ITM_ENGINE tiered exit profile (V10.14 sealed horizon profile)
ITM_TIERED_EXIT_ENABLED = True
ITM_MED_VIX_THRESHOLD = 18.0
ITM_HIGH_VIX_THRESHOLD = 25.0
ITM_TARGET_PCT_LOW_VIX = 0.40  # V12.15: restored R:R (was 0.25)
ITM_TARGET_PCT_MED_VIX = 0.45  # V12.15: restored R:R (was 0.30)
ITM_TARGET_PCT_HIGH_VIX = 0.50  # V12.15: restored R:R (was 0.35)
ITM_STOP_PCT_LOW_VIX = 0.45  # V12.13: wider for multi-day hold (was 0.22)
ITM_STOP_PCT_MED_VIX = 0.50  # V12.13: wider for multi-day hold (was 0.25)
ITM_STOP_PCT_HIGH_VIX = 0.55  # V12.13: wider for multi-day hold (was 0.28)
ITM_TRAIL_TRIGGER_LOW_VIX = 0.30  # V12.15: raised for R:R rebalance (was 0.20)
ITM_TRAIL_TRIGGER_MED_VIX = 0.32  # V12.15: raised for R:R rebalance (was 0.22)
ITM_TRAIL_TRIGGER_HIGH_VIX = 0.35  # V12.15: raised for R:R rebalance (was 0.25)
ITM_TRAIL_PCT_LOW_VIX = 0.30
ITM_TRAIL_PCT_MED_VIX = 0.32
ITM_TRAIL_PCT_HIGH_VIX = 0.35

# V12.20: Day-adaptive trail trigger — lower trigger for positions held overnight.
# Day 0 keeps 30/32/35% trigger (R:R symmetry preserved for same-day OCO).
# Day 1+ uses 12% trigger because multi-day OCO never resolves and 30% is unreachable.
ITM_DAY_ADAPTIVE_TRAIL_ENABLED = True
ITM_OVERNIGHT_TRAIL_TRIGGER = 0.12  # 12% MFE → trail activates for overnight holds

# ITM_ENGINE ATR guardrail: keep ATR widening in high-vol while preserving tier floors.
ITM_ATR_GUARDRAIL_ENABLED = True
ITM_ATR_GUARDRAIL_MAX_STOP_LOW_VIX = 0.50  # V12.13: must be >= stop (was 0.30)
ITM_ATR_GUARDRAIL_MAX_STOP_MED_VIX = 0.55  # V12.13: must be >= stop (was 0.35)
ITM_ATR_GUARDRAIL_MAX_STOP_HIGH_VIX = 0.60  # V12.13: must be >= stop (was 0.40)

# V12.13: ITM budget-proportional sizing — deploy fraction of budget, not all.
ITM_DEPLOY_PCT_OF_BUDGET = 0.60  # Deploy up to 60% of ITM budget per position

# V12.13: ADX-adaptive max hold — stronger trends get longer leash.
ITM_ADX_ADAPTIVE_HOLD_ENABLED = True
ITM_MAX_HOLD_DAYS_STRONG_ADX = 4  # ADX >= 28
ITM_MAX_HOLD_DAYS_MODERATE_ADX = 3  # ADX 24-28
ITM_MAX_HOLD_DAYS_WEAK_ADX = 2  # ADX 20-24
ITM_ADX_STRONG_THRESHOLD = 28.0
ITM_ADX_MODERATE_THRESHOLD = 24.0

# V12.13: VIX spike exit for ITM — close before event volatility crushes position.
ITM_VIX_SPIKE_EXIT_ENABLED = True
ITM_VIX_SPIKE_INTRADAY_PCT = 0.15  # Exit if VIX jumps 15%+ intraday

# ITM_ENGINE anti-roundtrip profit-lock floors
ITM_PROFIT_LOCK_BREAKEVEN_TRIGGER = 0.20
ITM_PROFIT_LOCK_BREAKEVEN_FLOOR_PCT = 0.01
ITM_PROFIT_LOCK_STRONG_TRIGGER = 0.35
ITM_PROFIT_LOCK_STRONG_FLOOR_PCT = 0.10

# V10.15: Conditional ITM EOD harvest for late-day winner protection
ITM_EOD_HARVEST_15_ENABLED = True
ITM_EOD_HARVEST_TRIGGER_PCT = 0.15
ITM_EOD_HARVEST_REQUIRE_WEAKENING = True
ITM_EOD_HARVEST_REGIME_MAX = 60.0
ITM_EOD_HARVEST_VIX5D_CALL_ADVERSE = 0.05
ITM_EOD_HARVEST_VIX5D_PUT_ADVERSE = -0.05

# Canonical ITM alias wiring (prevents drift between ITM_ENGINE and legacy fallback keys).
INTRADAY_ITM_DELTA_MIN = ITM_DELTA_MIN
INTRADAY_ITM_DELTA_MAX = ITM_DELTA_MAX
INTRADAY_ITM_TRAIL_TRIGGER = ITM_TRAIL_TRIGGER
INTRADAY_ITM_TRAIL_PCT = ITM_TRAIL_PCT
INTRADAY_ITM_FORCE_EXIT_DTE = ITM_FORCE_EXIT_DTE

# Legacy ITM knobs below this line are fallback-only when ITM_ENGINE_ENABLED = False.

# Protective Puts (Intraday Hedge)
INTRADAY_PROTECT_MIN_VIX = 20  # VIX > 20: Add protection
INTRADAY_PROTECT_STRIKE_OTM = 0.03  # 3% OTM strike
INTRADAY_PROTECT_DTE_MIN = 3  # Minimum 3 DTE
INTRADAY_PROTECT_DTE_MAX = 7  # Maximum 7 DTE

# Force close time for intraday
INTRADAY_FORCE_EXIT_TIME = "15:15"  # Close before end-of-day liquidity decay window
OCO_RECOVERY_CUTOFF_MINUTES_BEFORE_FORCE_EXIT = 20  # Disable OCO recovery near force-close window
OCO_RECOVERY_RETRY_MINUTES = 5  # Retry missing OCO creation intraday (bounded cadence)
OCO_RESYNC_PRICE_EPS = 0.01  # Min stop/target delta to trigger OCO reprice sync
OCO_SUBMIT_MAX_FAILURES_PER_SYMBOL_PER_DAY = (
    6  # Suppress repeated terminal submit failures per contract/day
)
OCO_SUBMIT_FAILURE_COOLDOWN_MINUTES = 15  # Cooldown after per-symbol failure budget is exhausted

# V2.3.16: Direction Conflict Resolution
# Skip intraday FADE when main regime strongly disagrees
DIRECTION_CONFLICT_BULLISH_THRESHOLD = 65  # Regime > 65 = strong bullish, don't fade rallies
DIRECTION_CONFLICT_BEARISH_THRESHOLD = 40  # Regime < 40 = strong bearish, don't fade dips

# V2.5: Grind-Up Override - capture rallies missed in CAUTIOUS regime
# Problem: Feb 4 missed +1.2% rally because VIX was STABLE (CAUTIOUS regime = NO_TRADE)
# Solution: In CAUTIOUS regime, if QQQ is UP_STRONG and macro score is safe, ride the rally
GRIND_UP_OVERRIDE_ENABLED = False  # V10: dead code path — CAUTIOUS not in caution_regimes
GRIND_UP_MIN_MOVE = 0.50  # Minimum QQQ move to trigger override (0.50% = UP_STRONG)
GRIND_UP_MACRO_SAFE_MIN = 40  # Macro regime score must be > 40 to avoid bear traps

# -----------------------------------------------------------------------------
# V6.0: OPTIONS MACRO REGIME GATE - REMOVED
# -----------------------------------------------------------------------------
# Direction decisions now handled by conviction resolution (resolve_trade_signal)
# in main.py. VASS & MICRO engines make direction decisions with conviction,
# and resolve_trade_signal() handles alignment/override with macro direction.
# See options_engine.py for details.

# Minimum combined size multiplier to proceed with trade
# If Governor × ColdStart < this, skip trade (too small)
OPTIONS_MIN_COMBINED_SIZE_PCT = 0.10  # 10% minimum

# -----------------------------------------------------------------------------
# V3.2: INTRADAY GOVERNOR GATE
# -----------------------------------------------------------------------------
# Closes gap: intraday options previously had no Governor check
# At Governor 0%: CALL blocked, PUT allowed (defensive)
INTRADAY_GOVERNOR_GATE_ENABLED = True

# -----------------------------------------------------------------------------
# V3.2: PROTECTIVE PUTS (Crisis Hedge via PUT Options)
# -----------------------------------------------------------------------------
# When Micro Regime detects crisis (score < 0), buy protective PUTs
# This supplements TMF/PSQ hedging with direct options protection
PROTECTIVE_PUTS_ENABLED = False  # VASS-only backtest mode: disable protective puts
PROTECTIVE_PUTS_SIZE_PCT = 0.03  # Reduce insurance drag while preserving crash hedge
PROTECTIVE_PUTS_DTE_MIN = 0  # Crash-day hedge: allow same-day/near-term convexity
PROTECTIVE_PUTS_DTE_MAX = 2  # Keep gamma high for same-day crash response
PROTECTIVE_PUTS_DELTA_TARGET = 0.45  # Near-ATM puts for stronger same-day hedge beta
PROTECTIVE_PUTS_DELTA_TOLERANCE = 0.12  # Accept ~0.33-0.57 delta band
PROTECTIVE_PUTS_STOP_PCT = 0.30  # Cut failed crash hedges faster when shock fades
PROTECTIVE_PUTS_TARGET_PCT = 0.60  # Crash hedge convexity target for near-ATM puts
PROTECTIVE_PUTS_DTE_EXIT = 0  # Keep same-day hedges alive until force-exit unless stop/target hit
PROTECTIVE_PUTS_CRASH_TRIGGER_ENABLED = True
PROTECTIVE_PUTS_QQQ_DROP_TRIGGER_PCT = -1.0  # Trigger hedge on >=1% intraday QQQ drop
PROTECTIVE_PUTS_VIX_MIN_TRIGGER = 15.0  # Allow earlier crash-day hedges in rising-vol tapes
PROTECTIVE_PUTS_REQUIRE_VIX_RISING = True
PROTECTIVE_PUTS_LATE_DAY_MIN_DTE_HOUR = 13  # After this hour, avoid opening new 0DTE hedges
INTRADAY_MAX_CONTRACTS = 50  # V10.32: reduce tail-risk concentration from intraday clustering
INTRADAY_CONTRACT_CAP_SCALE_WITH_EQUITY = False
INTRADAY_MAX_CONTRACTS_BASE_EQUITY = 100_000
INTRADAY_MAX_CONTRACTS_MIN = 5
INTRADAY_PENDING_ENTRY_STALE_MINUTES = (
    5  # Auto-clear orphan pending entry lock when no open broker order exists
)
INTRADAY_PENDING_ENTRY_FAST_CLEAR_SECONDS = 60
INTRADAY_PENDING_ENTRY_CANCEL_MINUTES = 5  # V10.18: cancel stale live entry orders sooner
INTRADAY_PENDING_ENTRY_HARD_CLEAR_MINUTES = (
    30  # V10.19: force-clear stale pending lock to avoid multi-hour lane starvation
)
EXIT_PRE_CLEAR_ALLOW_IMMEDIATE_INTRADAY_CLOSE = (
    True  # V10.22: restore time-critical intraday close bypass to reduce stale close latency
)
EXIT_PRE_CLEAR_TIMEOUT_SECONDS = (
    30  # Base wait window for open-order preclear before forced continuation
)
EXIT_PRE_CLEAR_TIMEOUT_SECONDS_INTRADAY = 8  # Faster preclear timeout for ITM/MICRO close intents
EXIT_PRE_CLEAR_TIMEOUT_SECONDS_COMBO = 30  # Keep stricter timeout for spread combo closes
EXIT_PRE_CLEAR_INTRADAY_BYPASS_AFTER_SECONDS = (
    5  # Time-critical intraday closes can bypass after this elapsed preclear wait
)
VASS_EXIT_PRECLEAR_REPLACE_INFLIGHT_CLOSE = (
    True  # V12.23.2: cancel same-spread inflight close orders and submit replacement immediately
)
ROUTER_STALE_CLOSE_REJECT_COOLDOWN_SECONDS = (
    60  # Suppress repeated no-live close intents per symbol to reduce reject churn
)
PROTECTIVE_PUTS_MAX_CONTRACTS = (
    0  # V12.15: disable dedicated protective cap; rely on % sizing + global intraday contract cap
)

# -----------------------------------------------------------------------------
# V2.1.1 SWING MODE SIMPLE FILTERS
# -----------------------------------------------------------------------------
# For Swing Mode (5+ DTE), use simple filters instead of Micro Regime

SWING_TIME_WINDOW_START = "10:00"  # Entry window start
SWING_TIME_WINDOW_END = "15:30"  # Entry window end

# Gap Filter for Swing Mode
SWING_GAP_THRESHOLD = 1.0  # Skip if SPY gaps > 1.0%
SWING_PUT_BOUNCE_FILTER_DETERIORATION_BYPASS_ENABLED = True  # V12.33: allow bearish swing puts after a gap-down when transition overlay already confirms deterioration.
SWING_PUT_BOUNCE_FILTER_DETERIORATION_DELTA_MIN = 0.5  # Minimum negative transition delta required to bypass the gap-down bounce-risk block for puts.

# Extreme Move Filter
SWING_EXTREME_SPY_DROP = -2.0  # Pause if SPY drops > 2% intraday
SWING_EXTREME_VIX_SPIKE = 15.0  # Pause if VIX spikes > 15% intraday

# V6.13 P0: Swing spread risk exits (VIX spike + overnight gap protection)
SWING_VIX_SPIKE_EXIT_ENABLED = False  # V12.23.3: Disabled — too sensitive in low-VIX (20% 5D threshold fires at VIX 15). ITM uses separate ITM_VIX_SPIKE_EXIT_ENABLED.
SWING_VIX_SPIKE_EXIT_LEVEL = 25.0  # Exit bullish spreads if VIX >= 25
SWING_VIX_SPIKE_EXIT_5D_PCT = 0.20  # Or if VIX 5D change >= +20%

SPREAD_EXIT_USE_EXECUTABLE_MARKS = (
    True  # Use long bid / short ask for conservative spread exit marks
)

SWING_OVERNIGHT_GAP_PROTECTION_ENABLED = True
SWING_OVERNIGHT_VIX_CLOSE_ALL = 30.0  # Close all spreads if VIX >= 30 at EOD
SWING_OVERNIGHT_VIX_CLOSE_FRESH = 22.0  # Close fresh spreads if VIX >= 22 at EOD
BEAR_PUT_OVERNIGHT_VIX_CLOSE_ALL_BEAR_REGIME = (
    40.0  # V12.31: let BEAR_PUT_DEBIT survive elevated bear-regime VIX unless stress is extreme
)
BEAR_CALL_CREDIT_OVERNIGHT_VIX_CLOSE_ALL_BEAR_REGIME = (
    40.0  # V12.37: let BEAR_CALL_CREDIT survive elevated bear-regime VIX unless stress is extreme
)

# -----------------------------------------------------------------------------
# V2.1.1 QQQ MOVE TRIGGER THRESHOLDS BY REGIME
# -----------------------------------------------------------------------------
# Trigger thresholds vary based on VIX level and direction

QQQ_TRIGGER_NORMAL_FALLING = 0.8  # VIX normal + falling: 0.8%
QQQ_TRIGGER_NORMAL_STABLE = 1.0  # VIX normal + stable: 1.0%
QQQ_TRIGGER_NORMAL_RISING = 1.25  # VIX normal + rising: 1.25% (extra caution)
QQQ_TRIGGER_CAUTION_FALLING = 1.0  # VIX caution + falling: 1.0%
QQQ_TRIGGER_CAUTION_RISING = 0.8  # VIX caution + rising: 0.8% puts only
QQQ_TRIGGER_ELEVATED = 0.8  # VIX elevated: 0.8% for momentum

# =============================================================================
# V2.19: LIMIT ORDER CONFIGURATION (Execution Protection)
# =============================================================================
# Problem: Market orders on illiquid options get filled at crazy bid/ask prices
# Solution: Marketable limit orders with slippage tolerance
#
# Key insight: We're not trying to get better fills - we're trying to REJECT
# clearly broken prices while still getting filled on normal liquid options.

# Master switch for limit orders on options
OPTIONS_USE_LIMIT_ORDERS = True

# Slippage tolerance as percentage of bid-ask spread
# BUY orders: Use Ask + (spread × slippage%)
# SELL orders: Use Bid - (spread × slippage%)
# 5% gives ~99% fill rate while rejecting clearly broken prices
OPTIONS_LIMIT_SLIPPAGE_PCT = 0.05  # 5% of spread

# Maximum acceptable spread as percentage of mid price
# If spread > 20% of mid, option is too illiquid - block the trade
OPTIONS_MAX_SPREAD_PCT = 0.20  # Block if spread > 20% of mid price

# =============================================================================
# EXECUTION ENGINE
# =============================================================================

MOO_SUBMISSION_TIME = "15:45"
MOO_FALLBACK_CHECK = "09:31"
MARKET_ORDER_TIMEOUT_SEC = 60
CONNECTION_TIMEOUT_MIN = 5

# =============================================================================
# V3.0: DYNAMIC EOD SCHEDULING (Early Close Day Support)
# =============================================================================
# EOD events are scheduled dynamically based on actual market close time.
# On early close days (1:00 PM), events fire earlier automatically.
EOD_OFFSET_MINUTES = 15  # MR force close & EOD processing = market_close - 15 min
INTRADAY_OPTIONS_OFFSET_MINUTES = 45  # Align dynamic close with 15:15 force-exit

# =============================================================================
# LOG THROTTLING (Pre-QC Local Testing)
# =============================================================================
# QC has 100KB log limit per backtest - throttle high-frequency logs

LOG_THROTTLE_MINUTES = 15  # VIX spike log throttle interval
LOG_VIX_SPIKE_MIN_MOVE = 2.0  # Minimum VIX move to bypass throttle
MARGIN_TRACK_LOG_BACKTEST_ENABLED = False  # Suppress minute-level margin spam in backtests
MARGIN_TRACK_LOG_LIVE_ENABLED = True  # Keep margin visibility in live trading
MICRO_UPDATE_LOG_BACKTEST_ENABLED = False  # Keep micro logs in backtest, but throttle heavily
MICRO_UPDATE_LOG_ON_CHANGE_ONLY = True  # Backtest: log only on state change (+heartbeat)
MICRO_UPDATE_LOG_MINUTES = 60  # Heartbeat interval when micro state remains unchanged
SPREAD_CONSTRUCTION_FAIL_LOG_INTERVAL_MINUTES = 60  # Throttle repeated spread-build failure logs
SPREAD_CONSTRUCTION_FAIL_LOG_BACKTEST_ENABLED = True  # Backtest: keep for RCA (still throttled)
LOG_SPREAD_RECONCILE_BACKTEST_ENABLED = (
    False  # Suppress repetitive reconcile-clear logs in backtests
)
LOG_ORDER_LIFECYCLE_BACKTEST_ENABLED = (
    False  # Full-year backtests: rely on daily summaries to stay within log budget
)
LOG_ORDER_LIFECYCLE_MAX_PER_DAY = 20  # Guardrail for lifecycle log budget
LOG_INTRADAY_BLOCKED_BACKTEST_ENABLED = False  # Keep drop reasons in artifacts; sample console
LOG_INTRADAY_CANDIDATE_BACKTEST_ENABLED = (
    False  # Candidate details captured in signal lifecycle CSV
)
LOG_INTRADAY_DROPPED_BACKTEST_ENABLED = False  # Drop RCA preserved in signal lifecycle CSV
LOG_VASS_FALLBACK_BACKTEST_ENABLED = True  # Fallback retry detail captured via lifecycle + counters
LOG_WIN_RATE_GATE_BACKTEST_ENABLED = False  # Keep win-rate gate details sampled in full-year runs
LOG_REGIME_ENGINE_DETAIL_BACKTEST_ENABLED = (
    False  # Suppress minute-level regime factor dumps in backtests
)
LOG_HIGHFREQ_SAMPLE_FIRST_N_PER_KEY = 1  # Backtest console: keep first occurrence per key/day
LOG_HIGHFREQ_SAMPLE_EVERY_N = 100  # Then log every Nth repeat for continuity
LOG_HIGHFREQ_SAMPLE_EVERY_N_LIVE = 10  # Live-mode sampled cadence for high-frequency diagnostics
LOG_DROP_AGGREGATE_BACKTEST_ENABLED = True
LOG_DROP_AGG_FIRST_N_PER_KEY = 1
LOG_DROP_AGG_SAMPLE_EVERY_N = (
    0  # 0 = no periodic repeats; rely on DROP_RCA_DAILY aggregate for totals
)
LOG_DROP_AGG_MAX_REASONS_PER_CATEGORY = 8
LOG_BUDGET_GUARD_ENABLED = True
LOG_BUDGET_GUARD_BACKTEST_ENABLED = True
LOG_BUDGET_GUARD_LIVE_ENABLED = False
LOG_BUDGET_SOFT_LIMIT_BYTES = 4_000_000
LOG_BUDGET_EXTREME_LIMIT_BYTES = 4_500_000
LOG_BUDGET_ESTIMATED_OVERHEAD_BYTES_PER_LINE = 50
LOG_BUDGET_SUPPRESSION_CHECKPOINT_ENABLED = True
LOG_BUDGET_SUPPRESSION_CHECKPOINT_EVERY_N = 500
LOG_BUDGET_SUPPRESSION_CHECKPOINT_PREVIEW_CHARS = 120
SPREAD_GHOST_INTRADAY_CLEAR_CONSECUTIVE = (
    2  # Intraday guarded clear requires N consecutive flat checks
)
SPREAD_GHOST_HEALTH_LOG_MINUTES = 60  # Throttle ghost-health diagnostics
STATE_MANAGER_POSITION_PERSIST_ENABLED = (
    False  # PositionState persistence path disabled until fully wired
)

# V2.3.21: Spread scan throttle to reduce log noise
# Retry often enough to catch fast-moving contract availability changes.
SPREAD_SCAN_THROTTLE_MINUTES = 10

# V2.4.3: Spread FAILURE cooldown after construction failure.
# Keep short so valid chains can be re-attempted intra-session.
SPREAD_FAILURE_COOLDOWN_HOURS = 1  # Legacy fallback if minute override is absent
SPREAD_FAILURE_COOLDOWN_MINUTES = 30

# V12.27 P3: Broker-invalid VASS entry symbols can cluster across scan cycles.
# Apply a short, contract-level quarantine to force alternate leg selection.
VASS_INVALID_ENTRY_SYMBOL_COOLDOWN_ENABLED = True
VASS_INVALID_ENTRY_SYMBOL_COOLDOWN_MINUTES = 60

# V2.5: Max concurrent spreads - limit exposure from spread positions
# Problem: Mar 25-26 had two spreads open simultaneously ($19K exposure)
# Solution: Only allow 1 active spread at a time

# =============================================================================
# V2.4.4 P0 FIXES - CRITICAL OPTIONS SAFETY
# =============================================================================

# P0 Fix #1: Margin Call Circuit Breaker
# After N consecutive margin call rejects, stop attempting orders for 4 hours
# Prevents 2765+ margin call spam seen in V2.4.3 backtest
MARGIN_CALL_MAX_CONSECUTIVE = 5  # Stop after 5 margin call rejects
MARGIN_CALL_COOLDOWN_HOURS = 4  # 4-hour cooldown after hitting limit

# P0 Fix #2: Margin Pre-Check Buffer
# Before placing any order, verify MarginRemaining > order_cost * buffer
# Buffer accounts for potential price movement during execution
MARGIN_PRE_CHECK_BUFFER = 0.15  # V6.10 P4: Lowered from 2.00 to 15% buffer (was too restrictive)

# V6.10 P4: Margin Check BEFORE Signal Approval
# Check margin availability before approving ANY spread signal
# This prevents signals from being generated only to fail at execution
MARGIN_CHECK_BEFORE_SIGNAL = True  # V6.10 P4: Enable pre-signal margin check
MARGIN_PRE_CHECK_MIN_SPREADS = 1  # Minimum spreads to check margin for (1 spread)

# P0 Fix #3: Options Exercise Detection
# Handle exercise events in OnOrderEvent to prevent margin disasters
OPTIONS_HANDLE_EXERCISE_EVENTS = True

# P0 Fix #4: Expiration Hammer V2 - Force close ALL options on expiration day
# Close at 2:00 PM (not just ITM or VIX-based) to prevent ANY exercise risk
# V2.4.2 only closed based on VIX threshold; V2.4.4 closes unconditionally
EXPIRATION_HAMMER_CLOSE_ALL = True  # Close ALL options expiring today at 2 PM

# V2.3.24: Rejection log throttle to reduce log spam
# Only log MIN_TRADE_VALUE rejections once per interval
REJECTION_LOG_THROTTLE_MINUTES = 30
REJECTION_EVENT_LOG_THROTTLE_MINUTES = 15  # Throttle repeated ROUTER_REJECT lines by code/stage

# =============================================================================
# SCHEDULING
# =============================================================================

SCHEDULED_EVENTS = {
    "PRE_MARKET_SETUP": "09:25",
    "SOD_BASELINE": "09:33",
    "WARM_ENTRY_CHECK": "10:00",
    "TIME_GUARD_START": "13:55",
    "TIME_GUARD_END": "14:10",
    "MR_FORCE_CLOSE": "15:45",
    "EOD_PROCESSING": "15:45",
}

# =============================================================================
# INDICATORS
# =============================================================================

# SMA200 needs 200 trading days. 200 × (7/5) + holidays buffer ≈ 300 calendar days
INDICATOR_WARMUP_DAYS = 300  # Calendar days (not trading days)

# =============================================================================
# SYMBOLS
# =============================================================================

# V6.11: Universe redesign - diversified trend + commodity hedges
TRADED_SYMBOLS = [
    # Trend Engine (2× leveraged, overnight hold)
    "QLD",
    "SSO",
    "UGL",
    "UCO",
    # Mean Reversion Engine (3× leveraged, intraday only)
    "TQQQ",
    "SPXL",
    "SOXL",
    # Hedge Engine
    "SH",
]
PROXY_SYMBOLS = [
    "SPY",
    "RSP",
    "HYG",
    "IEF",
    "GLD",
    "USO",
]  # V6.11: Added GLD, USO for commodity tracking
ALL_SYMBOLS = TRADED_SYMBOLS + PROXY_SYMBOLS

# Trend symbols (overnight hold allowed)
# V6.11: Diversified - equities (QLD, SSO) + commodities (UGL, UCO)
TREND_SYMBOLS = ["QLD", "SSO", "UGL", "UCO"]

# Mean Reversion symbols (intraday only, must close by 15:45)
# V6.11: Added SPXL for broader market bounces
MR_SYMBOLS = ["TQQQ", "SPXL", "SOXL"]

# =============================================================================
# V6.4: ENGINE ISOLATION MODE
# =============================================================================
# Allows targeted backtesting of individual engines by disabling all others.
# Use for focused debugging, performance analysis, and validation.
# See docs/guides/ENGINE_ISOLATION_MODE.md for full documentation.

# Master switch - when True, only enabled engines/safeguards run
ISOLATION_TEST_MODE = True  # V6.6: Options Engine isolation backtest

# Engine enables (only checked when ISOLATION_TEST_MODE = True)
ISOLATION_REGIME_ENABLED = True  # Regime Engine (required by most engines)
ISOLATION_OPTIONS_ENABLED = True  # Options Engine (VASS + Micro Intraday)
ISOLATION_TREND_ENABLED = False  # Trend Engine (QLD/SSO/UGL/UCO) - V6.11 updated
ISOLATION_MR_ENABLED = False  # Mean Reversion Engine (TQQQ/SPXL/SOXL) - V6.11 updated
ISOLATION_HEDGE_ENABLED = False  # Hedge Engine (SH) - V6.11 updated
ISOLATION_YIELD_ENABLED = False  # Yield Sleeve (SHV)

# Safeguard enables (only checked when ISOLATION_TEST_MODE = True)
ISOLATION_KILL_SWITCH_ENABLED = False  # Backtest mode: disable isolation kill switch
ISOLATION_STARTUP_GATE_ENABLED = False  # Startup Gate (15-day warmup)
ISOLATION_COLD_START_ENABLED = False  # Cold Start (days 1-5 restrictions)
ISOLATION_DRAWDOWN_GOVERNOR_ENABLED = False  # Drawdown Governor (position scaling)
ISOLATION_PANIC_MODE_ENABLED = False  # Panic Mode (SPY -4% liquidation)
ISOLATION_WEEKLY_BREAKER_ENABLED = False  # Weekly Breaker (5% WTD loss)
ISOLATION_GAP_FILTER_ENABLED = False  # Gap Filter (SPY -1.5% gap block)
ISOLATION_VOL_SHOCK_ENABLED = False  # Vol Shock (3× ATR pause)

RECON_INTRADAY_ORPHAN_MIN_STREAK = 2
RECON_INTRADAY_ORPHAN_MIN_AGE_MINUTES = 20
RECON_INTRADAY_ORPHAN_LOG_THROTTLE_MINUTES = 30
