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

# =============================================================================
# REGIME ENGINE
# =============================================================================

# Factor Weights (adjusted to reduce breadth sensitivity during narrow rallies)
WEIGHT_TREND = 0.45  # Increased from 0.35 - follow price trend even if breadth lags
WEIGHT_VOLATILITY = 0.25
WEIGHT_BREADTH = 0.15  # Reduced from 0.25 - prevents blocking during mega-cap rallies
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

# Volatility Factor
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

# V1 Legacy: Bollinger Bands (kept for backwards compatibility)
BB_PERIOD = 20
BB_STD_DEV = 2.0
COMPRESSION_THRESHOLD = 0.10

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
    "RATES": {"max_net_long": 0.40, "max_net_short": 0.00, "max_gross": 0.40},
}

# Group Membership
SYMBOL_GROUPS = {
    "TQQQ": "NASDAQ_BETA",
    "QLD": "NASDAQ_BETA",
    "SOXL": "NASDAQ_BETA",
    "PSQ": "NASDAQ_BETA",  # Inverse
    "SSO": "SPY_BETA",
    "TMF": "RATES",
    "SHV": "RATES",
}

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

# =============================================================================
# OPTIONS ENGINE (V2.1)
# =============================================================================
# Harvests daily volatility on QQQ options with 4-factor entry scoring

# Underlying Symbol
OPTIONS_UNDERLYING = "QQQ"

# Allocation
OPTIONS_ALLOCATION_MIN = 0.20  # 20% minimum
OPTIONS_ALLOCATION_MAX = 0.30  # 30% maximum

# Entry Score Thresholds
OPTIONS_ENTRY_SCORE_MIN = 3.0  # Minimum score for entry (0-4 scale)
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
OPTIONS_DTE_MIN = 1  # Minimum days to expiration
OPTIONS_DTE_MAX = 4  # Maximum days to expiration
OPTIONS_DELTA_MIN = 0.40  # Minimum delta (ATM range)
OPTIONS_DELTA_MAX = 0.60  # Maximum delta (ATM range)

# Position Sizing
OPTIONS_RISK_PER_TRADE = 0.01  # 1% portfolio risk per trade

# Time Constraints
OPTIONS_LATE_DAY_HOUR = 14  # 2 PM
OPTIONS_LATE_DAY_MINUTE = 30  # 2:30 PM
OPTIONS_LATE_DAY_MAX_STOP = 0.20  # Only 20% stops after 2:30 PM

# Max Trades Per Day
OPTIONS_MAX_TRADES_PER_DAY = 1

# =============================================================================
# EXECUTION ENGINE
# =============================================================================

MOO_SUBMISSION_TIME = "15:45"
MOO_FALLBACK_CHECK = "09:31"
MARKET_ORDER_TIMEOUT_SEC = 60
CONNECTION_TIMEOUT_MIN = 5

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

INDICATOR_WARMUP_DAYS = 252  # Max of all indicator requirements

# =============================================================================
# SYMBOLS
# =============================================================================

TRADED_SYMBOLS = ["TQQQ", "SOXL", "QLD", "SSO", "TMF", "PSQ", "SHV"]
PROXY_SYMBOLS = ["SPY", "RSP", "HYG", "IEF"]
ALL_SYMBOLS = TRADED_SYMBOLS + PROXY_SYMBOLS
