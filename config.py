"""
Alpha NextGen Configuration
All tunable parameters in one place.
"""

# =============================================================================
# CAPITAL ENGINE
# =============================================================================

# Phase Definitions
PHASE_SEED_MIN = 50_000
PHASE_SEED_MAX = 99_999
PHASE_GROWTH_MIN = 100_000
PHASE_GROWTH_MAX = 499_999

# Phase Transitions
UPWARD_TRANSITION_DAYS = 5
DOWNWARD_TRANSITION_DAYS = 1  # Immediate

# Position Limits
MAX_SINGLE_POSITION_PCT = {"SEED": 0.50, "GROWTH": 0.40}

TARGET_VOLATILITY = 0.20
KILL_SWITCH_PCT_BY_PHASE = {"SEED": 0.03, "GROWTH": 0.03}

# Lockbox
LOCKBOX_MILESTONES = [100_000, 200_000]
LOCKBOX_LOCK_PCT = 0.10

# V2.3 Capital Reservation (addresses options buying power issue)
# When all 4 trend tickers trigger simultaneously, they consume ~196% of capital
# via margin, leaving no buying power for options (25% allocation)
# This reserves capital BEFORE trend positions are sized
RESERVED_OPTIONS_PCT = 0.25  # Reserve 25% for options allocation

# =============================================================================
# REGIME ENGINE
# =============================================================================

# Factor Weights (V2.3: Added VIX, rebalanced)
WEIGHT_TREND = 0.30  # V2.3: Reduced from 0.45 to make room for VIX
WEIGHT_VIX = 0.20  # V2.3 NEW: Implied volatility for options pricing
WEIGHT_VOLATILITY = 0.15  # V2.3: Reduced from 0.25 (realized vol)
WEIGHT_BREADTH = 0.20  # V2.3: Increased from 0.15
WEIGHT_CREDIT = 0.15

# Smoothing
REGIME_SMOOTHING_ALPHA = 0.30

# Thresholds
REGIME_RISK_ON = 70
REGIME_NEUTRAL = 50
REGIME_CAUTIOUS = 40
REGIME_DEFENSIVE = 30

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

# Volatility Factor (Realized)
VOL_LOOKBACK = 20
VOL_PERCENTILE_LOOKBACK = 252

# Breadth & Credit
BREADTH_LOOKBACK = 20
CREDIT_LOOKBACK = 20

# =============================================================================
# COLD START ENGINE
# =============================================================================

COLD_START_DAYS = 5  # Number of days in cold start mode
WARM_ENTRY_SIZE_MULT = 0.50
WARM_ENTRY_TIME = "10:00"
WARM_REGIME_MIN = 50
WARM_QLD_THRESHOLD = 60
WARM_MIN_SIZE = 2_000

# =============================================================================
# TREND ENGINE
# =============================================================================

# V2 Entry: MA200 + ADX Confirmation
MA200_PERIOD = 200  # Long-term trend baseline
ADX_PERIOD = 14  # Average Directional Index for momentum confirmation
ADX_ENTRY_THRESHOLD = 25  # Minimum ADX for entry (score_adx >= 0.50)
ADX_STRONG_THRESHOLD = 35  # ADX for highest confidence

# ADX Scoring Thresholds (V2.1 spec)
# ADX < 20: 0.25 (weak)
# ADX 20-25: 0.50 (moderate)
# ADX 25-35: 0.75 (strong)
# ADX >= 35: 1.00 (very strong)
ADX_WEAK_THRESHOLD = 20
ADX_MODERATE_THRESHOLD = 25

# Chandelier Stop
ATR_PERIOD = 14
CHANDELIER_BASE_MULT = 3.0
CHANDELIER_TIGHT_MULT = 2.5  # Updated per V2.1: 2.5x for profit 10-20%
CHANDELIER_TIGHTER_MULT = 2.0  # Updated per V2.1: 2.0x for profit 20%+
PROFIT_TIGHT_PCT = 0.10  # Updated per V2.1: tighten at 10%
PROFIT_TIGHTER_PCT = 0.20  # Updated per V2.1: tighten more at 20%

# Entry/Exit
TREND_ENTRY_REGIME_MIN = 40
TREND_EXIT_REGIME = 30
TREND_ADX_EXIT_THRESHOLD = 20  # Exit if ADX drops below this

# V2.3 Position Limits (addresses simultaneous entry issue)
# In bull markets, all 4 tickers (QLD/SSO/TNA/FAS) may trigger together
# because they're all correlated to US equity market (0.70-0.95 correlation)
# Limiting to 2 positions ensures capital remains for options allocation
MAX_CONCURRENT_TREND_POSITIONS = 2  # Max trend positions at any time

# Priority order when multiple entries trigger simultaneously
# Higher priority symbols get capital first
TREND_PRIORITY_ORDER = ["QLD", "SSO", "TNA", "FAS"]  # Nasdaq > S&P > Russell > Financials

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
MR_REGIME_MIN = 40  # Spec value: regime >= 40

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

# Thresholds
HEDGE_LEVEL_1 = 40
HEDGE_LEVEL_2 = 30
HEDGE_LEVEL_3 = 20

# TMF Allocation
TMF_LIGHT = 0.10
TMF_MEDIUM = 0.15
TMF_FULL = 0.20

# PSQ Allocation
PSQ_MEDIUM = 0.05
PSQ_FULL = 0.10

# Rebalancing
HEDGE_REBAL_THRESHOLD = 0.02

# =============================================================================
# YIELD SLEEVE
# =============================================================================

SHV_MIN_TRADE = 2_000

# =============================================================================
# PORTFOLIO ROUTER
# =============================================================================

# Exposure Limits
EXPOSURE_LIMITS = {
    "NASDAQ_BETA": {"max_net_long": 0.50, "max_net_short": 0.30, "max_gross": 0.75},
    "SPY_BETA": {"max_net_long": 0.40, "max_net_short": 0.00, "max_gross": 0.40},
    "SMALL_CAP_BETA": {"max_net_long": 0.25, "max_net_short": 0.00, "max_gross": 0.25},
    "FINANCIALS_BETA": {"max_net_long": 0.15, "max_net_short": 0.00, "max_gross": 0.15},
    "RATES": {"max_net_long": 0.40, "max_net_short": 0.00, "max_gross": 0.40},
}

# Group Membership
SYMBOL_GROUPS = {
    "TQQQ": "NASDAQ_BETA",
    "QLD": "NASDAQ_BETA",
    "SOXL": "NASDAQ_BETA",
    "PSQ": "NASDAQ_BETA",  # Inverse
    "SSO": "SPY_BETA",
    "TNA": "SMALL_CAP_BETA",  # V2.2: 3× Russell 2000
    "FAS": "FINANCIALS_BETA",  # V2.2: 3× Financials
    "TMF": "RATES",
    "SHV": "RATES",
}

# =============================================================================
# V2.2 BALANCED ALLOCATION MODEL
# =============================================================================
# Addresses capital utilization problem from V1 testing:
# - Trend Engine entry probability ~13.3% (very conservative)
# - Adding TNA/FAS increases diversification and entry opportunities

# Trend Engine Allocations (55% total)
TREND_SYMBOL_ALLOCATIONS = {
    "QLD": 0.20,  # 20% - 2× Nasdaq (primary)
    "SSO": 0.15,  # 15% - 2× S&P 500 (secondary)
    "TNA": 0.12,  # 12% - 3× Russell 2000 (small-cap diversification)
    "FAS": 0.08,  # 8% - 3× Financials (sector diversification)
}
TREND_TOTAL_ALLOCATION = 0.55  # 55% total to Trend Engine

# Mean Reversion Allocations (10% total)
MR_SYMBOL_ALLOCATIONS = {
    "TQQQ": 0.05,  # 5% - 3× Nasdaq
    "SOXL": 0.05,  # 5% - 3× Semiconductor
}
MR_TOTAL_ALLOCATION = 0.10  # 10% total to MR Engine

# Trade Thresholds
MIN_TRADE_VALUE = 2_000
MIN_SHARE_DELTA = 1

# =============================================================================
# RISK ENGINE
# =============================================================================

# Kill Switch (V1: Nuclear option - liquidate ALL)
KILL_SWITCH_PCT = 0.03

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
# V2.3 DUAL-MODE ALLOCATION (25% total, 75/25 split)
# -----------------------------------------------------------------------------
OPTIONS_TOTAL_ALLOCATION = 0.25  # 25% total options budget (increased from 20%)
OPTIONS_SWING_ALLOCATION = 0.1875  # 18.75% for Swing Mode (75% of 25%)
OPTIONS_INTRADAY_ALLOCATION = 0.0625  # 6.25% for Intraday Mode (25% of 25%)

# Legacy compatibility (combined min/max)
OPTIONS_ALLOCATION_MIN = 0.25  # 25% minimum
OPTIONS_ALLOCATION_MAX = 0.30  # 30% maximum

# Entry Score Thresholds
OPTIONS_ENTRY_SCORE_MIN = 2.0  # Minimum score for entry (0-4 scale) - lowered from 3.0 for testing
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
OPTIONS_SPREAD_MAX_PCT = 0.05  # Max 5% bid-ask spread
OPTIONS_SPREAD_WARNING_PCT = 0.10  # Avoid > 10% spread
OPTIONS_MIN_OPEN_INTEREST = 5000  # Minimum open interest

# Confidence-Weighted Tiered Stops
# Higher entry score → wider stops, fewer contracts
OPTIONS_STOP_TIERS = {
    3.00: {"stop_pct": 0.20, "contracts": 34},  # Score 3.0-3.25
    3.25: {"stop_pct": 0.22, "contracts": 31},  # Score 3.25-3.5
    3.50: {"stop_pct": 0.25, "contracts": 27},  # Score 3.5-3.75
    3.75: {"stop_pct": 0.30, "contracts": 23},  # Score 3.75-4.0
}

# Profit Target
OPTIONS_PROFIT_TARGET_PCT = 0.50  # +50% profit target

# Contract Selection
# Options chain filter (must cover BOTH Intraday 0-2 DTE AND Swing 5-45 DTE)
OPTIONS_DTE_MIN = 0  # Minimum days to expiration (Intraday mode)
OPTIONS_DTE_MAX = 45  # Maximum days to expiration (Swing mode)
OPTIONS_DELTA_MIN = 0.40  # Minimum delta (ATM range)
OPTIONS_DELTA_MAX = 0.60  # Maximum delta (ATM range)
OPTIONS_MIN_PREMIUM = 0.50  # Minimum premium per contract ($0.50)

# Force Exit Time (V2.1: close options by 3:45 PM like MR)
OPTIONS_FORCE_EXIT_HOUR = 15  # 3 PM
OPTIONS_FORCE_EXIT_MINUTE = 45  # 3:45 PM

# Position Sizing
OPTIONS_RISK_PER_TRADE = 0.01  # 1% portfolio risk per trade

# Time Constraints
OPTIONS_LATE_DAY_HOUR = 14  # 2 PM
OPTIONS_LATE_DAY_MINUTE = 30  # 2:30 PM
OPTIONS_LATE_DAY_MAX_STOP = 0.20  # Only 20% stops after 2:30 PM

# Max Trades Per Day
OPTIONS_MAX_TRADES_PER_DAY = 1

# -----------------------------------------------------------------------------
# V2.1.1 DUAL-MODE DTE BOUNDARIES
# -----------------------------------------------------------------------------
OPTIONS_SWING_DTE_MIN = 5  # Minimum DTE for Swing Mode
OPTIONS_SWING_DTE_MAX = 45  # Maximum DTE for Swing Mode
OPTIONS_INTRADAY_DTE_MIN = 0  # Minimum DTE for Intraday Mode
OPTIONS_INTRADAY_DTE_MAX = 2  # Maximum DTE for Intraday Mode

# -----------------------------------------------------------------------------
# V2.1.1 VIX DIRECTION THRESHOLDS (Micro Regime Engine)
# -----------------------------------------------------------------------------
# VIX direction is THE key differentiator for intraday trading
# Same VIX level + different direction = OPPOSITE strategies

VIX_DIRECTION_FALLING_FAST = -5.0  # VIX change < -5%: Strong recovery
VIX_DIRECTION_FALLING = -2.0  # VIX change -5% to -2%: Recovery
VIX_DIRECTION_STABLE_LOW = -2.0  # VIX stable range lower bound
VIX_DIRECTION_STABLE_HIGH = 2.0  # VIX stable range upper bound
VIX_DIRECTION_RISING = 5.0  # VIX change +2% to +5%: Fear building
VIX_DIRECTION_RISING_FAST = 10.0  # VIX change +5% to +10%: Panic
VIX_DIRECTION_SPIKING = 10.0  # VIX change > +10%: Crash mode

# Whipsaw detection: Range > threshold × net change
VIX_WHIPSAW_RATIO = 3.0  # Range/NetChange threshold
VIX_WHIPSAW_MIN_RANGE = 5.0  # Minimum range % to consider whipsaw

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
MICRO_SCORE_VIX_VERY_CALM = 25  # VIX < 15
MICRO_SCORE_VIX_CALM = 20  # VIX 15-18
MICRO_SCORE_VIX_NORMAL = 15  # VIX 18-20
MICRO_SCORE_VIX_ELEVATED = 10  # VIX 20-23
MICRO_SCORE_VIX_HIGH = 5  # VIX 23-25
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

# Debit Fade (Mean Reversion)
INTRADAY_DEBIT_FADE_MIN_SCORE = 50  # Micro score >= 50
INTRADAY_DEBIT_FADE_MIN_MOVE = 1.0  # QQQ move >= 1.0%
INTRADAY_DEBIT_FADE_VIX_MAX = 25  # VIX < 25
INTRADAY_DEBIT_FADE_START = "10:30"  # Entry window start
INTRADAY_DEBIT_FADE_END = "14:00"  # Entry window end
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
INTRADAY_ITM_MIN_VIX = 25  # VIX > 25 for momentum
INTRADAY_ITM_MIN_MOVE = 0.8  # QQQ move >= 0.8%
INTRADAY_ITM_MIN_SCORE = 50  # Micro score >= 50
INTRADAY_ITM_DELTA = 0.70  # ITM delta target
INTRADAY_ITM_START = "10:00"  # Entry window start
INTRADAY_ITM_END = "13:30"  # Entry window end (need time)
INTRADAY_ITM_TARGET = 0.40  # +40% profit target
INTRADAY_ITM_STOP = 0.50  # -50% stop (wider for momentum)
INTRADAY_ITM_TRAIL_TRIGGER = 0.20  # Trail after +20%
INTRADAY_ITM_TRAIL_PCT = 0.50  # Trail at 50% of gains

# Protective Puts (Intraday Hedge)
INTRADAY_PROTECT_MIN_VIX = 20  # VIX > 20: Add protection
INTRADAY_PROTECT_STRIKE_OTM = 0.03  # 3% OTM strike
INTRADAY_PROTECT_DTE_MIN = 3  # Minimum 3 DTE
INTRADAY_PROTECT_DTE_MAX = 7  # Maximum 7 DTE

# Force close time for intraday
INTRADAY_FORCE_EXIT_TIME = "15:30"  # Must close by 3:30 PM

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
# EXECUTION ENGINE
# =============================================================================

MOO_SUBMISSION_TIME = "15:45"
MOO_FALLBACK_CHECK = "09:31"
MARKET_ORDER_TIMEOUT_SEC = 60
CONNECTION_TIMEOUT_MIN = 5

# =============================================================================
# LOG THROTTLING (Pre-QC Local Testing)
# =============================================================================
# QC has 100KB log limit per backtest - throttle high-frequency logs

LOG_THROTTLE_MINUTES = 15  # VIX spike log throttle interval
LOG_VIX_SPIKE_MIN_MOVE = 2.0  # Minimum VIX move to bypass throttle

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

TRADED_SYMBOLS = ["TQQQ", "SOXL", "QLD", "SSO", "TNA", "FAS", "TMF", "PSQ", "SHV"]
PROXY_SYMBOLS = ["SPY", "RSP", "HYG", "IEF"]
ALL_SYMBOLS = TRADED_SYMBOLS + PROXY_SYMBOLS

# Trend symbols (overnight hold allowed)
TREND_SYMBOLS = ["QLD", "SSO", "TNA", "FAS"]

# Mean Reversion symbols (intraday only, must close by 15:45)
MR_SYMBOLS = ["TQQQ", "SOXL"]
