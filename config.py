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

# Kill Switch
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
