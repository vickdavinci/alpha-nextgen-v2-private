"""
Alpha NextGen Configuration
All tunable parameters in one place.
"""

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
KILL_SWITCH_PCT = 0.05  # V3.0: Unified (was phase-dependent)

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
V53_SPIKE_CAP_DECAY_DAYS = 3  # Cap persists for 3 days

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
REGIME_SMOOTHING_ALPHA = 0.30

# Thresholds
REGIME_RISK_ON = 70
REGIME_NEUTRAL = 50
REGIME_CAUTIOUS = 45
REGIME_DEFENSIVE = 35

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
TREND_TOTAL_ALLOCATION = 0.40  # 40% total

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

# =============================================================================
# RISK ENGINE
# =============================================================================

# Kill Switch (V1: Nuclear option - liquidate ALL)
# V2.3.17: Raised from 3% to 5% to reduce false triggers in volatile markets
KILL_SWITCH_PCT = 0.05  # Legacy fallback (used when KS_GRADUATED_ENABLED = False)
# V2.16-BT: Preemptive kill switch when panic mode active AND approaching threshold
# Closes gap between panic mode (4%) and kill switch (5%) where hedges could lose value
KILL_SWITCH_PREEMPTIVE_PCT = 0.045  # 4.5% - triggers kill switch when in panic mode

# V2.27: Graduated Kill Switch (replaces binary -5% nuclear option)
# 3-tier response: REDUCE → TREND_EXIT → FULL_EXIT
KS_GRADUATED_ENABLED = True
KS_TIER_1_PCT = 0.02  # -2% daily loss → REDUCE (halve trend, block new options)
KS_TIER_2_PCT = 0.04  # -4% daily loss → TREND_EXIT (liquidate trend, keep spreads)
KS_TIER_3_PCT = 0.06  # -6% daily loss → FULL_EXIT (liquidate everything)
KS_TIER_1_TREND_REDUCTION = 0.50  # Reduce trend allocation by 50% at Tier 1
KS_TIER_1_BLOCK_NEW_OPTIONS = True  # Block new option entries at Tier 1
KS_SKIP_DAYS = 1  # Block new entries for 1 day after Tier 2+
KS_COLD_START_RESET_ON_TIER_2 = False  # Don't reset cold start on Tier 2
KS_COLD_START_RESET_ON_TIER_3 = True  # Reset cold start on Tier 3 (true emergency)

# V2.27: KS Spread Decouple
# Spreads survive Tier 1 and Tier 2 — they have their own -50% stop (SPREAD_STOP_LOSS_PCT)
# Only Tier 3 (FULL_EXIT) liquidates spreads
KILL_SWITCH_SPREAD_DECOUPLE = False  # V9.4: Spreads now liquidated at all KS tiers

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
OPTIONS_TOTAL_ALLOCATION = 0.50  # 50% total options budget
OPTIONS_SWING_ALLOCATION = 0.375  # 37.5% for Swing Mode (75% of 50%)
OPTIONS_INTRADAY_ALLOCATION = 0.125  # 12.5% for Intraday Mode (25% of 50%)

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
VASS_IV_LOW_THRESHOLD = 16  # V6.6: Was 15, raised to match data distribution
VASS_IV_HIGH_THRESHOLD = (
    25  # V6.9: Reverted to 25 - HIGH IV must use CREDIT spreads per V2.8 design
)
VASS_IV_SMOOTHING_MINUTES = 30  # SMA window to prevent strategy flickering

# DTE Ranges by IV Environment (Swing Mode)
VASS_LOW_IV_DTE_MIN = 30  # Low IV: Monthly expiration
VASS_LOW_IV_DTE_MAX = 45
VASS_MEDIUM_IV_DTE_MIN = 7  # Medium IV: Weekly expiration
VASS_MEDIUM_IV_DTE_MAX = 30  # V6.12: Widen for better contract availability
# V6.6: Widened HIGH IV DTE range - 36 spread failures in 2022H1 due to narrow 7-14 window
VASS_HIGH_IV_DTE_MIN = 5  # V6.8: Was 7, allow trades in high IV
VASS_HIGH_IV_DTE_MAX = 40  # V6.13.1 OPT: Expand candidate pool (was 28)

# V5.3: VASS Conviction Engine (VIX Direction Tracking)
# VASS tracks weekly (5d) and monthly (20d) VIX to determine conviction
VASS_VIX_5D_PERIOD = 5  # Weekly VIX lookback (days)
VASS_VIX_20D_PERIOD = 20  # Monthly VIX lookback (days)

# Conviction Thresholds (% change triggers override of Macro)
VASS_VIX_5D_BEARISH_THRESHOLD = 0.16  # VIX 5d change > +16% → BEARISH conviction
VASS_VIX_5D_BULLISH_THRESHOLD = -0.20  # VIX 5d change < -20% → BULLISH conviction
VASS_VIX_20D_STRONG_BEARISH = 0.30  # VIX 20d change > +30% → STRONG BEARISH
VASS_VIX_20D_STRONG_BULLISH = -0.20  # VIX 20d change < -20% → STRONG BULLISH
VASS_EARLY_STRESS_BULL_REQUIRE_CONVICTION = (
    True  # D8: In EARLY_STRESS, block bullish VASS unless conviction is present
)
VASS_EARLY_STRESS_BULL_STRATEGY_TO_CREDIT = (
    True  # D8: In EARLY_STRESS, remap bullish debit spreads to bullish credit spreads
)
VASS_EARLY_STRESS_BEAR_PREFER_CREDIT = (
    True  # D8: In EARLY_STRESS, prefer bearish credit over bearish debit
)
VASS_SIMILAR_ENTRY_MIN_GAP_MINUTES = 15  # Block repeated same-signature entries in burst windows
VASS_SIMILAR_ENTRY_COOLDOWN_DAYS = (
    2  # Shorter cooldown to avoid over-throttling quality follow-through
)
VASS_SIMILAR_ENTRY_USE_EXPIRY_BUCKET = True  # Use expiry date bucket (fallback to DTE bucket)
VASS_DIRECTION_DAY_GAP_ENABLED = True  # Hard spacing: max 1 VASS entry per day per direction

# Level Crossing Thresholds (regime shift signals)
VASS_VIX_FEAR_CROSS_LEVEL = 23  # VIX crosses above this → BEARISH
VASS_VIX_COMPLACENT_CROSS_LEVEL = 14  # VIX crosses below this → BULLISH

# Credit Spread Constraints
CREDIT_SPREAD_MIN_CREDIT = 0.20  # V6.10 P3: Was 0.30, lowered to allow more fills
CREDIT_SPREAD_WIDTH_TARGET = 5.0  # $5 width for credit spreads
CREDIT_SPREAD_FALLBACK_TO_DEBIT = True  # V6.10 P3: Fall back to debit when credit fails
CREDIT_SPREAD_PROFIT_TARGET = 0.50  # Exit at 50% of max profit
CREDIT_SPREAD_STOP_MULTIPLIER = 0.40  # V10: tightened from 0.60 (R:R 0.45→0.67)
CREDIT_SPREAD_SHORT_LEG_DELTA_MIN = 0.25  # Short leg delta range (OTM)
CREDIT_SPREAD_SHORT_LEG_DELTA_MAX = 0.45  # V6.13 OPT: Improve credit spread constructability
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
CREDIT_SPREAD_MIN_CREDIT_TO_WIDTH_PCT = 0.35  # VIX < 20: strict quality gate
CREDIT_SPREAD_MIN_CREDIT_TO_WIDTH_PCT_MEDIUM_IV = 0.32  # VIX 20-30: moderate relaxation
CREDIT_SPREAD_MIN_CREDIT_TO_WIDTH_PCT_HIGH_IV = 0.30  # VIX > 30: widest relaxation
CREDIT_SPREAD_MEDIUM_IV_VIX_THRESHOLD = 20.0  # VIX level for medium-IV tier

# V2.3.14: Intraday trade limits (was 1, blocking all re-entries after first trade)
# V2.3.15: Sniper Logic - allow one retry, not machine gun
INTRADAY_MAX_TRADES_PER_DAY = 4  # V9.7: revert to V9.3 (V9.5 halved this, killed MICRO volume)
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
# Reserve swing capacity so intraday activity cannot fully starve VASS entries.
OPTIONS_RESERVE_SWING_DAILY_SLOTS_ENABLED = False
OPTIONS_MIN_SWING_SLOTS_PER_DAY = 1
OPTIONS_RESERVE_INTRADAY_DAILY_SLOTS_ENABLED = False
OPTIONS_MIN_INTRADAY_SLOTS_PER_DAY = 1
OPTIONS_RESERVE_RELEASE_HOUR = 12  # Release reserved slots earlier to reduce midday throttling
OPTIONS_RESERVE_RELEASE_MINUTE = 30
# Replace one-attempt-per-day spread lock with scoped attempt budgets.
SPREAD_MAX_ATTEMPTS_PER_KEY_PER_DAY = 3

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
OPTIONS_0DTE_STOP_PCT = 0.15  # -15% stop for 0DTE

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
OPTIONS_ATR_STOP_MULTIPLIER = 0.9  # V6.13 OPT: Slightly tighter ATR base stop

# Floor and cap to prevent extreme stops
OPTIONS_ATR_STOP_MIN_PCT = 0.12  # V6.13 OPT: Tighter floor in calm conditions
OPTIONS_ATR_STOP_MAX_PCT = 0.28  # Slightly tighter cap to reduce tail-loss

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
SPREAD_ASSIGNMENT_GRACE_MINUTES = 45  # V6.15 FIX: Allow spread to stabilize before ITM checks
SHORT_LEG_ITM_EXIT_LOG_INTERVAL = 15  # Minutes between log messages
SPREAD_MIN_HOLD_MINUTES = 5760  # V9.9: 4-day min hold guard for VASS spreads
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
PREMARKET_ITM_CHECK_HOUR = 9  # Check at 09:25 ET
PREMARKET_ITM_CHECK_MINUTE = 25

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
BEAR_PUT_ENTRY_MIN_OTM_PCT = 0.02  # V6.4 baseline: tighter OTM requirement for assignment safety.
BEAR_PUT_ENTRY_LOW_VIX_THRESHOLD = 18.0  # Relax assignment gate in calmer IV environments
BEAR_PUT_ENTRY_MIN_OTM_PCT_RELAXED = (
    0.015  # V6.4 baseline relaxed threshold in low-VIX healthy regimes.
)
BEAR_PUT_ENTRY_RELAXED_REGIME_MIN = (
    60.0  # Require healthy regime before applying relaxed OTM threshold
)
# V6.22: During confirmed stress, allow tighter BEAR_PUT shorts to keep bearish access alive.
# V9.4: Lowered from 0.8% to 0.3%. Bear markets need PUT access most — max loss already capped by debit.
BEAR_PUT_ENTRY_MIN_OTM_PCT_STRESS = 0.005

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
MICRO_DTE_DIAG_LOG_INTERVAL_MIN = 30  # Throttle ITM DTE routing diagnostics
MICRO_DTE_DIAG_LOG_BACKTEST_ENABLED = True  # Keep throttled ITM DTE diagnostics in backtests

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
SPREAD_REGIME_BULLISH = 70  # V3.0: CALL spreads ONLY in Bull (regime > 70)
SPREAD_REGIME_BEARISH = 50  # V3.0: PUT spreads in Cautious + Bear (regime < 50)
SPREAD_REGIME_CRISIS = 0  # V3.0: DISABLED — PUT spreads work in ALL bear regimes

# V6.13 P0: Regime deterioration exits for swing spreads
SPREAD_REGIME_DETERIORATION_EXIT_ENABLED = True
SPREAD_REGIME_DETERIORATION_DELTA = 10  # Require at least 10-point regime drop/rise
SPREAD_REGIME_DETERIORATION_BULL_EXIT = 60  # Exit bullish spreads if regime <= 60
SPREAD_REGIME_DETERIORATION_BEAR_EXIT = 55  # Exit bearish spreads if regime >= 55

# VIX filters for entry
SPREAD_VIX_MAX_BULL = 30  # Max VIX for Bull Call Spread entry
SPREAD_VIX_MAX_BEAR = 35  # Max VIX for Bear Put Spread entry (allow higher)
# V6.19: Conditional stress override for BULL_CALL_DEBIT (reduces call bias in corrections).
# Hard block when stress is confirmed; early-stress zone keeps participation at reduced size.
BULL_CALL_STRESS_BLOCK_VIX = 22.0
BULL_CALL_STRESS_ACCEL_VIX = 18.0
BULL_CALL_STRESS_ACCEL_5D = 0.20  # +20% VIX over 5 sessions
BULL_CALL_EARLY_STRESS_VIX_LOW = 16.0
BULL_CALL_EARLY_STRESS_VIX_HIGH = 18.0
BULL_CALL_EARLY_STRESS_SIZE = 0.50
# Bear hardening: block bullish debit spreads when short-term trend is down.
VASS_BULL_CALL_MA50_BLOCK_ENABLED = True
VASS_BULL_CALL_MA50_BLOCK_REGIME_MAX = 60.0
# V6.22: Fast regime overlay thresholds (shared by resolver, slot caps, and exits).
REGIME_OVERLAY_STRESS_VIX = 19.0
REGIME_OVERLAY_STRESS_VIX_5D = 0.12
REGIME_OVERLAY_EARLY_VIX_LOW = 15.0
REGIME_OVERLAY_EARLY_VIX_HIGH = 17.0

# Spread width (strike difference between legs)
# V2.4.3: WIDTH-BASED short leg selection (fixes "delta trap" in backtesting)
# Problem: Delta values jump (0.45 → 0.25) leaving gaps where no "perfect" delta exists
# Solution: Select short leg by STRIKE WIDTH, not delta. Delta is soft preference only.
SPREAD_SHORT_LEG_BY_WIDTH = True  # V2.4.3: Use strike width for short leg (not delta)
# V6.10: Spread width settings for QQQ - WIDENED FOR ASSIGNMENT PROTECTION
# Wider spreads survive larger overnight gaps and reduce assignment risk
SPREAD_WIDTH_MIN = 4.0  # V6.13 OPT: Improve candidate availability with controlled risk
SPREAD_WIDTH_MAX = 10.0  # V2.4.3: Maximum $10 spread (caps risk)
SPREAD_WIDTH_TARGET = 4.0  # V6.13 OPT: Improve fill/constructability in medium IV
SPREAD_WIDTH_EFFECTIVE_MAX = (
    7.0  # V9.1: Preferred width ceiling for R:R sort (avoids lottery-ticket wide spreads)
)

# DTE for debit spreads (per V2.3 spec)
# V2.3.22: Raised from 10 to 14 - spreads need same gap cushion as single-leg
SPREAD_DTE_MIN = 14  # Minimum 14 DTE (avoid gamma acceleration + gap risk)
SPREAD_DTE_MAX = 45  # V2.19: Widened from 21 to 45 to align with VASS_LOW_IV_DTE (30-45)
SPREAD_DTE_EXIT = 5  # Close by 5 DTE remaining
VASS_DEBIT_MAX_HOLD_DAYS = 0  # V9.5: disable debit spread time-stop force exit
VASS_DEBIT_MAX_HOLD_DAYS_LOW_VIX = 0  # V9.5: disable low-VIX debit time-stop override
VASS_DEBIT_LOW_VIX_THRESHOLD = 16.0

# Exit targets
# V6.10 P5: Symmetric R:R (40%/40%) - need 1:1 win ratio to break even
# Was asymmetric (50%/35%) requiring 1.43:1 win ratio
SPREAD_MAX_DEBIT_TO_WIDTH_PCT = (
    0.55  # V9.1: Block spreads where debit > 55% of width (ensures R:R ≥ 0.82:1)
)
SPREAD_PROFIT_TARGET_PCT = 0.40  # V9.4: Lowered from 0.50 (more achievable targets)
SPREAD_STOP_LOSS_PCT = 0.30  # V9.4: Tightened from 0.40 (caps worst-case at 36% in bull)
SPREAD_HARD_STOP_LOSS_PCT = 0.40  # V9.4: Lowered from 0.50 (cap above max adaptive of 0.36)
SPREAD_HARD_STOP_WIDTH_PCT = 0.35  # Hard cap using spread width (debit spreads)
SPREAD_STOP_REGIME_MULTIPLIERS = {
    75: 1.20,  # Bull: give more room (0.30 * 1.2 = 0.36)
    50: 1.00,  # Neutral: base (0.30)
    40: 0.85,  # Cautious: tighter (0.30 * 0.85 = 0.255)
    0: 0.70,  # Bear: tightest (0.30 * 0.70 = 0.21)
}

# V9.4: Spread Trailing Stop — lock in gains after reaching activation threshold
SPREAD_TRAIL_ACTIVATE_PCT = 0.30  # V9.5 tune: avoid cutting swing winners too early
SPREAD_TRAIL_OFFSET_PCT = 0.15  # Trail 15% below high-water mark

# V3.0: Regime-Adaptive Profit Targets
# V9.4: With 40% base, multipliers give: Bull=36%, Neutral=44%, Cautious/Bear=48%
SPREAD_PROFIT_REGIME_MULTIPLIERS = {
    75: 0.90,  # Regime >= 75: 36% target (0.90 × 40% base)
    50: 1.10,  # Regime 50-74: 44% target
    40: 1.20,  # Regime 40-49: 48% target
    0: 1.20,  # Regime < 40: 48% target (ride bear trends)
}

# V9.4: BULL spread entry gates (regime-specific, no impact in bull markets)
VASS_BULL_SPREAD_REGIME_MIN = 55  # Block BULL_CALL when regime < 55
VASS_BULL_MA20_GATE_ENABLED = False  # V9.5 tune: disable for VASS swing pullback participation

# V9.7: BEAR_PUT entry gate — block in RISK_ON (12.5% WR in 2017 full-year RCA)
VASS_BEAR_PUT_REGIME_MAX = 70  # Block BEAR_PUT_DEBIT when regime >= 70 (RISK_ON)

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
# V6.19 O-20: Keep VASS alive in stress periods; avoid full spread-path freeze.
VASS_WIN_RATE_HARD_BLOCK = False  # If False, shutoff degrades to minimum size instead of blocking
VASS_WIN_RATE_SHUTOFF_SCALE = 0.40  # Size scale used when shutoff is active and hard block disabled
# In elevated VIX, do not allow VASS bullish conviction to force trades from NEUTRAL macro.
VASS_NEUTRAL_BULL_OVERRIDE_MAX_VIX = 18.0
VASS_BULL_PROFILE_BEARISH_BLOCK_ENABLED = True
VASS_BULL_PROFILE_REGIME_MIN = (
    70.0  # Strong-bull profile threshold for blocking bearish VASS entries
)
# V6.1: Removed SPREAD_REGIME_EXIT_BULL/BEAR - legacy logic conflicted with conviction-based entry
# Spreads now exit via: STOP_LOSS, PROFIT_TARGET, DTE_EXIT, NEUTRALITY_EXIT

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
SPREAD_NEUTRALITY_EXIT_ENABLED = True  # V6.13 OPT: Re-enable for choppy capital recycling
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
COMBO_ORDER_MAX_RETRIES = 3  # Try atomic ComboMarketOrder up to 3 times
COMBO_ORDER_FALLBACK_TO_SEQUENTIAL = True  # If all retries fail, use sequential close
SPREAD_CLOSE_SAFE_LOCK_RETRY_MIN = 10  # Retry emergency close after safe-lock alert

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
SPREAD_LONG_LEG_DELTA_TARGET_CALL = 0.50  # V9.1: ATM target for CALLs (cheaper debit, better R:R)
SPREAD_LONG_LEG_DELTA_TARGET_PUT = (
    0.70  # V9.1: ITM target for PUTs (unchanged, directional exposure)
)
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
SPREAD_CLOSE_CANCEL_ESCALATION_COUNT = 2  # Escalate to immediate sequential close after N cancels
SPREAD_CLOSE_RETRY_INTERVAL_MIN = 5  # Retry cadence for forced spread close queue
SPREAD_CLOSE_MAX_RETRY_CYCLES = 12  # Hard cap to avoid infinite forced-close retry loops

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
VASS_LOG_REJECTION_INTERVAL_MINUTES = 15  # Log rejections every 15 min (not every candle)
MICRO_NO_TRADE_LOG_INTERVAL_MINUTES = 5  # Per-block throttle for MICRO_NO_TRADE logs

# -----------------------------------------------------------------------------
# V2.11: PRE-BACKTEST SAFETY FIXES (Pitfalls #6-8)
# -----------------------------------------------------------------------------
# Pitfall #6: Margin Collateral Lock-Out
# Options sizing must cap by actual available margin, not just portfolio %
# V2.12 Fix #4: Raised from $5K to $10K - 8-lot spread requires ~$8K margin
# V3.0 SCALABILITY FIX: Converted to percentage-based for portfolio scaling
OPTIONS_MAX_MARGIN_CAP = 50_000  # V6.20: Align hard cap with $100K capital and 50% options profile
OPTIONS_MAX_MARGIN_PCT = 0.40  # V6.20: Raise margin allowance for options-isolation stress tests

# V2.18: Percentage-based Sizing Caps (scales with portfolio)
# At $75K: 15% = $11,250, 8% = $6,000
# At $200K: 15% = $30,000, 8% = $16,000
SWING_SPREAD_MAX_PCT = 0.15  # 15% of portfolio for swing spreads (14-21 DTE)
INTRADAY_SPREAD_MAX_PCT = 0.08  # 8% of portfolio for intraday spreads (1-5 DTE)

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
SPREAD_MAX_CONTRACTS = 20  # Hard cap per spread position

# V5.3: Options Position Limits (Margin Error Prevention)
# Max concurrent positions: 2 intraday + 5 swings = 7 total
OPTIONS_MAX_INTRADAY_POSITIONS = 2  # V8.2: Allow 2 concurrent intraday positions
OPTIONS_MAX_SWING_POSITIONS = 5  # Expand swing capacity across regimes
OPTIONS_MAX_TOTAL_POSITIONS = 7  # 2 intraday + up to 5 swings
OPTIONS_MAX_SWING_PER_DIRECTION = 3  # Legacy fallback cap if directional pools are unset
# V8: Separate directional swing pools (prevents one side from monopolizing all swing slots).
OPTIONS_MAX_SWING_BULLISH_POSITIONS = 3
OPTIONS_MAX_SWING_BEARISH_POSITIONS = 3
MAX_BULLISH_SPREADS_STRESS = 0  # No new bullish spreads in confirmed stress
MAX_BULLISH_SPREADS_EARLY_STRESS = 1  # Restrict bullish concentration in early stress
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
MICRO_UVXY_BEARISH_THRESHOLD = (
    0.020  # Slightly easier bearish conviction for faster PUT participation
)
MICRO_UVXY_BULLISH_THRESHOLD = (
    -0.040
)  # Restore bullish participation in bull/chop while gates control bear CALLs
# V6.10: Lower conviction extreme to capture 5-7% moves that were blocked
MICRO_UVXY_CONVICTION_EXTREME = 0.030  # Slightly easier extreme conviction trigger
# V6.10: Micro fallback + confirmation thresholds (Dir=None tuning)
MICRO_SCORE_BULLISH_CONFIRM = (
    42.0  # V6.22: lower threshold to let more VIX-STABLE CALL setups through
)
MICRO_SCORE_BEARISH_CONFIRM = 50.0  # D7: Slightly easier bearish confirmation in downtrends
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
INTRADAY_CALL_BLOCK_VIX_MIN = 22.0  # Block CALLs earlier when fear rises
INTRADAY_CALL_BLOCK_REGIME_MAX = 58.0  # Extend block deeper into weak-neutral macro
# Additional minimal CALL-protection gates (bear-risk controls without major architecture changes)
CALL_GATE_MA20_ENABLED = True  # Block CALL entries when QQQ is below its 20-day SMA
CALL_GATE_MA20_BYPASS_REGIME_MIN = 68.0  # Allow CALLs below MA20 in strong bullish macro
CALL_GATE_MA20_BYPASS_VIX_MAX = 18.0  # Only bypass when fear is still low
CALL_GATE_MA20_BYPASS_SIZE_MULT = 0.85  # Reduce size when using MA20 bypass
CALL_GATE_VIX_5D_RISING_ENABLED = True  # Block CALL entries when 5-day VIX trend is rising
CALL_GATE_VIX_5D_RISING_PCT = 0.10  # +10% over 5 days
CALL_GATE_CONSECUTIVE_LOSS_ENABLED = True  # Pause CALL entries after repeated losses
CALL_GATE_CONSECUTIVE_LOSSES = 3  # Trigger pause after 3 consecutive CALL losses
CALL_GATE_LOSS_COOLDOWN_DAYS = 2  # Pause duration

# V2.19: VIX Floor for DEBIT_FADE
# In low VIX (<13.5) "apathy" markets, mean reversion fails - trends persist longer
# Evidence: V2.18 backtests showed DEBIT_FADE losses when VIX < 13.5
INTRADAY_DEBIT_FADE_VIX_MIN = 9.0  # Slightly wider low-vol participation

# Debit Fade (Mean Reversion) - Gate 3a - The Sniper Window
INTRADAY_DEBIT_FADE_MIN_SCORE = 32  # Increase DEBIT_FADE throughput while preserving quality filter
INTRADAY_FADE_MIN_MOVE = 0.35  # Restore intraday participation while keeping noise filter
# V10: VIX-tier move gates (replace single INTRADAY_FADE_MIN_MOVE for MICRO routing)
MICRO_MIN_MOVE_LOW_VIX = 0.50  # Stricter for LOW VIX — filter theta-dominated noise
MICRO_MIN_MOVE_MED_VIX = 0.40  # Standard move gate
MICRO_MIN_MOVE_HIGH_VIX = 0.40  # Standard move gate
INTRADAY_FADE_MAX_MOVE = 1.50  # V6.8: Was 1.20, don't block strong bull continuation
INTRADAY_DEBIT_FADE_VIX_MAX = 25  # VIX < 25
INTRADAY_DEBIT_FADE_START = "10:00"  # Include early-session mean-reversion setups
INTRADAY_DEBIT_FADE_END = "14:30"  # Extend late-session setup coverage
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
INTRADAY_ITM_MIN_SCORE = 40  # V6.8: Was 50, capture momentum earlier
# V2.3.19: Time window moved from hardcoded to config
INTRADAY_ITM_START = "10:00"  # Entry window start
INTRADAY_ITM_END = "14:30"  # Entry window end (earlier than FADE - momentum fades after lunch)
INTRADAY_ITM_DELTA = 0.70  # ITM delta target
INTRADAY_ITM_TARGET = 0.45  # V10: wider for uncapped upside (was 0.35)

# V6.4: DEBIT_MOMENTUM time window (same as ITM_MOMENTUM - both are momentum strategies)
INTRADAY_DEBIT_MOMENTUM_START = "10:00"  # Entry window start
INTRADAY_DEBIT_MOMENTUM_END = "14:30"  # Entry window end
INTRADAY_ITM_STOP = 0.25  # V10: tighter — ITM moves predictably with delta (was 0.35)
INTRADAY_HIGH_VIX_STOP_MAX_PCT = (
    0.40  # V9.2 RCA: Wider stop cap for VIX>25 regimes (was capped at 28%)
)
INTRADAY_ITM_TRAIL_TRIGGER = 0.20  # V9.8: revert to V9.3 (below 0.35 target, trail works)
INTRADAY_ITM_TRAIL_PCT = 0.50  # Trail at 50% of gains

# V9.2: Per-strategy intraday exits (previously universal target/stop)
INTRADAY_DEBIT_FADE_TARGET = (
    0.40  # V9.8: revert to V9.3 (0.25 was within bid-ask noise on $0.30 options)
)
INTRADAY_DEBIT_FADE_STOP = 0.25
INTRADAY_DEBIT_FADE_TRAIL_TRIGGER = 0.25  # V9.8: revert to V9.3 (below 0.40 target, trail works)
INTRADAY_DEBIT_FADE_TRAIL_PCT = 0.50

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
INTRADAY_DEBIT_FADE_DELTA_MIN = 0.20  # OTM for mean reversion
INTRADAY_DEBIT_FADE_DELTA_MAX = 0.50  # Near ATM max

# V6.4: DEBIT_MOMENTUM: Trend confirmation needs ATM-ish options (delta 0.45-0.65)
# Between DEBIT_FADE (OTM) and ITM_MOMENTUM (ITM) - captures directional moves
INTRADAY_DEBIT_MOMENTUM_ENABLED = (
    False  # V10: deprecated — ITM_MOMENTUM replaces all confirmation paths
)
INTRADAY_ITM_MOMENTUM_ENABLED = True  # V10: primary confirmation strategy
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
INTRADAY_ITM_DELTA_MIN = 0.65  # V10: tightened from 0.60 for better ITM quality
INTRADAY_ITM_DELTA_MAX = 0.80  # V10: tightened from 0.85 to avoid deep ITM illiquidity

# Protective Puts (Intraday Hedge)
INTRADAY_PROTECT_MIN_VIX = 20  # VIX > 20: Add protection
INTRADAY_PROTECT_STRIKE_OTM = 0.03  # 3% OTM strike
INTRADAY_PROTECT_DTE_MIN = 3  # Minimum 3 DTE
INTRADAY_PROTECT_DTE_MAX = 7  # Maximum 7 DTE

# Force close time for intraday
INTRADAY_FORCE_EXIT_TIME = "15:25"  # V6.15 FIX: Earlier close to avoid OCO race at 15:30
OCO_RECOVERY_CUTOFF_MINUTES_BEFORE_FORCE_EXIT = 20  # Disable OCO recovery near force-close window
OCO_RECOVERY_RETRY_MINUTES = 30  # Retry missing OCO creation intraday (bounded cadence)

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
PROTECTIVE_PUTS_ENABLED = True
PROTECTIVE_PUTS_SIZE_PCT = 0.03  # Reduce insurance drag while preserving crash hedge
PROTECTIVE_PUTS_DTE_MIN = 3  # Minimum 3 DTE (time for recovery)
PROTECTIVE_PUTS_DTE_MAX = 7  # Maximum 7 DTE (balance cost vs protection)
PROTECTIVE_PUTS_DELTA_TARGET = 0.30  # OTM puts (cheaper, more leverage)
PROTECTIVE_PUTS_DELTA_TOLERANCE = 0.10  # Accept delta 0.20-0.40
PROTECTIVE_PUTS_STOP_PCT = 0.35  # Tighter stop to reduce repeated deep insurance losses
INTRADAY_MAX_CONTRACTS = 40  # V9.8: Hard cap for all MICRO intraday entries
PROTECTIVE_PUTS_MAX_CONTRACTS = (
    5  # V9.2 RCA: Cap contracts to prevent 10+ lot outsized bets in crisis
)

# -----------------------------------------------------------------------------
# V2.1.1 SWING MODE SIMPLE FILTERS
# -----------------------------------------------------------------------------
# For Swing Mode (5+ DTE), use simple filters instead of Micro Regime

SWING_TIME_WINDOW_START = "10:00"  # Entry window start
SWING_TIME_WINDOW_END = "14:30"  # Entry window end

# Gap Filter for Swing Mode
SWING_GAP_THRESHOLD = 1.0  # Skip if SPY gaps > 1.0%

# Extreme Move Filter
SWING_EXTREME_SPY_DROP = -2.0  # Pause if SPY drops > 2% intraday
SWING_EXTREME_VIX_SPIKE = 15.0  # Pause if VIX spikes > 15% intraday

# V6.13 P0: Swing spread risk exits (VIX spike + overnight gap protection)
SWING_VIX_SPIKE_EXIT_ENABLED = True
SWING_VIX_SPIKE_EXIT_LEVEL = 25.0  # Exit bullish spreads if VIX >= 25
SWING_VIX_SPIKE_EXIT_5D_PCT = 0.20  # Or if VIX 5D change >= +20%

SWING_OVERNIGHT_GAP_PROTECTION_ENABLED = True
SWING_OVERNIGHT_VIX_CLOSE_ALL = 30.0  # Close all spreads if VIX >= 30 at EOD
SWING_OVERNIGHT_VIX_CLOSE_FRESH = 22.0  # Close fresh spreads if VIX >= 22 at EOD

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
INTRADAY_OPTIONS_OFFSET_MINUTES = 35  # V6.15 FIX: Align dynamic close with 15:25 fallback

# =============================================================================
# LOG THROTTLING (Pre-QC Local Testing)
# =============================================================================
# QC has 100KB log limit per backtest - throttle high-frequency logs

LOG_THROTTLE_MINUTES = 15  # VIX spike log throttle interval
LOG_VIX_SPIKE_MIN_MOVE = 2.0  # Minimum VIX move to bypass throttle
MARGIN_TRACK_LOG_BACKTEST_ENABLED = False  # Suppress minute-level margin spam in backtests
MARGIN_TRACK_LOG_LIVE_ENABLED = True  # Keep margin visibility in live trading
MICRO_UPDATE_LOG_BACKTEST_ENABLED = True  # Keep micro logs in backtest, but throttle heavily
MICRO_UPDATE_LOG_ON_CHANGE_ONLY = True  # Backtest: log only on state change (+heartbeat)
MICRO_UPDATE_LOG_MINUTES = 60  # Heartbeat interval when micro state remains unchanged
SPREAD_CONSTRUCTION_FAIL_LOG_INTERVAL_MINUTES = 60  # Throttle repeated spread-build failure logs
SPREAD_CONSTRUCTION_FAIL_LOG_BACKTEST_ENABLED = True  # Backtest: keep for RCA (still throttled)
LOG_SPREAD_RECONCILE_BACKTEST_ENABLED = (
    False  # Suppress repetitive reconcile-clear logs in backtests
)
LOG_ORDER_LIFECYCLE_BACKTEST_ENABLED = (
    True  # Keep compact invalid/cancel attribution logs in backtests
)
LOG_ORDER_LIFECYCLE_MAX_PER_DAY = 200  # Guardrail for lifecycle log budget
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
REJECTION_LOG_THROTTLE_MINUTES = 15
REJECTION_EVENT_LOG_THROTTLE_MINUTES = 5  # Throttle repeated ROUTER_REJECT lines by code/stage

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
ISOLATION_KILL_SWITCH_ENABLED = True  # Kill Switch (5% daily loss)
ISOLATION_STARTUP_GATE_ENABLED = False  # Startup Gate (15-day warmup)
ISOLATION_COLD_START_ENABLED = False  # Cold Start (days 1-5 restrictions)
ISOLATION_DRAWDOWN_GOVERNOR_ENABLED = False  # Drawdown Governor (position scaling)
ISOLATION_PANIC_MODE_ENABLED = False  # Panic Mode (SPY -4% liquidation)
ISOLATION_WEEKLY_BREAKER_ENABLED = False  # Weekly Breaker (5% WTD loss)
ISOLATION_GAP_FILTER_ENABLED = False  # Gap Filter (SPY -1.5% gap block)
ISOLATION_VOL_SHOCK_ENABLED = False  # Vol Shock (3× ATR pause)
