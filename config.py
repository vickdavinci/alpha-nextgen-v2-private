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
# V2.3.17: Kill switch raised from 3% to 5% (reduces false triggers in volatile markets)
KILL_SWITCH_PCT_BY_PHASE = {"SEED": 0.05, "GROWTH": 0.05}

# Lockbox
LOCKBOX_MILESTONES = [100_000, 200_000]
LOCKBOX_LOCK_PCT = 0.10

# V2.3 Capital Reservation (addresses options buying power issue)
# When all 4 trend tickers trigger simultaneously, they consume ~196% of capital
# via margin, leaving no buying power for options (25% allocation)
# This reserves capital BEFORE trend positions are sized
RESERVED_OPTIONS_PCT = 0.25  # Reserve 25% for options allocation

# V2.3.24: Leverage multipliers for margin calculation
# Used by portfolio_router to calculate actual margin consumption, not just allocation %
SYMBOL_LEVERAGE = {
    "QLD": 2.0,  # 2× Nasdaq
    "SSO": 2.0,  # 2× S&P 500
    "TNA": 3.0,  # 3× Russell 2000
    "FAS": 3.0,  # 3× Financials
    "TQQQ": 3.0,  # 3× Nasdaq (MR)
    "SOXL": 3.0,  # 3× Semiconductor (MR)
    "TMF": 3.0,  # 3× Treasury (Hedge)
    "PSQ": 1.0,  # 1× Inverse Nasdaq (Hedge)
}

# V2.3.24: Minimum contracts for scaled spreads
# If margin check would reduce spread below this, skip the trade entirely
MIN_SPREAD_CONTRACTS = 2

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

# V2.3.11: VIX Level Boundaries for Micro Regime Engine
# Lowered VERY_CALM from 15 → 11.5 to fire more SNIPER 0DTEs
VIX_LEVEL_VERY_CALM_MAX = 11.5  # V2.3.11: VIX < 11.5 = VERY_CALM (was 15)
VIX_LEVEL_CALM_MAX = 15.0  # VIX 11.5-15 = CALM (shifted down)
VIX_LEVEL_NORMAL_MAX = 18.0  # VIX 15-18 = NORMAL (unchanged)

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

# V2.3.20: Allow options during cold start with reduced sizing
# During Days 1-5, options positions are sized at 50% to reduce risk
# while still participating in opportunities
OPTIONS_COLD_START_MULTIPLIER = 0.50

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

# Chandelier Stop
ATR_PERIOD = 14
CHANDELIER_BASE_MULT = 3.5  # V2.3.6: Widened from 3.0 - allow more room in choppy markets
CHANDELIER_TIGHT_MULT = 3.0  # V2.3.6: Widened from 2.5 - less aggressive tightening
CHANDELIER_TIGHTER_MULT = 2.5  # V2.3.6: Widened from 2.0 - hold winners longer
PROFIT_TIGHT_PCT = 0.15  # V2.3.6: Raised from 0.10 - don't tighten too early
PROFIT_TIGHTER_PCT = 0.25  # V2.3.6: Raised from 0.20 - let trends run

# V2.3.8: 3x ETF Volatility Multipliers (PART 14 Pitfall 3)
# TNA/FAS swing 5-7% daily - tighter stops to limit damage
# Use ATR×2.5 instead of ATR×3.5 for 3x ETFs
TREND_3X_SYMBOLS = ["TNA", "FAS"]  # 3× leveraged symbols needing tighter stops
CHANDELIER_3X_BASE_MULT = 2.5  # V2.3.8: Tighter than 2x (3.5) - control 3x volatility
CHANDELIER_3X_TIGHT_MULT = 2.0  # V2.3.8: Tighter than 2x (3.0)
CHANDELIER_3X_TIGHTER_MULT = 1.5  # V2.3.8: Tighter than 2x (2.5)

# Entry/Exit
TREND_ENTRY_REGIME_MIN = 40
TREND_EXIT_REGIME = 30
TREND_ADX_EXIT_THRESHOLD = 10  # V2.3.12: Lowered to 10 - allow holding during low momentum grind

# V2.3.3 Position Limits (Part 4 audit fix)
# In bull markets, all 4 tickers (QLD/SSO/TNA/FAS) may trigger together
# because they're all correlated to US equity market (0.70-0.95 correlation)
# V2.3.3: Changed from 2 to 4 to allow full trend allocation (55% total)
MAX_CONCURRENT_TREND_POSITIONS = 2  # Max trend positions at any time (reduced for options testing)

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
# PORTFOLIO ROUTER
# =============================================================================

# Exposure Limits
EXPOSURE_LIMITS = {
    "NASDAQ_BETA": {"max_net_long": 0.50, "max_net_short": 0.30, "max_gross": 0.75},
    "SPY_BETA": {"max_net_long": 0.40, "max_net_short": 0.00, "max_gross": 0.40},
    "SMALL_CAP_BETA": {"max_net_long": 0.25, "max_net_short": 0.00, "max_gross": 0.25},
    "FINANCIALS_BETA": {"max_net_long": 0.15, "max_net_short": 0.00, "max_gross": 0.15},
    "RATES": {"max_net_long": 0.40, "max_net_short": 0.00, "max_gross": 0.40},  # TMF only
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
# 3× ETFs need tighter stops due to higher daily volatility (5-7% swings)
# 2× ETFs can tolerate wider stops (2-3% daily swings)
TREND_HARD_STOP_PCT = {
    "QLD": 0.15,  # 15% hard stop (2× ETF)
    "SSO": 0.15,  # 15% hard stop (2× ETF)
    "TNA": 0.12,  # 12% hard stop (3× ETF - more volatile)
    "FAS": 0.12,  # 12% hard stop (3× ETF - more volatile)
}

# Mean Reversion Allocations (10% total)
MR_SYMBOL_ALLOCATIONS = {
    "TQQQ": 0.05,  # 5% - 3× Nasdaq
    "SOXL": 0.05,  # 5% - 3× Semiconductor
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
KILL_SWITCH_PCT = 0.05

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

# V2.3.14: Intraday trade limits (was 1, blocking all re-entries after first trade)
# V2.3.15: Sniper Logic - allow one retry, not machine gun
INTRADAY_MAX_TRADES_PER_DAY = 2  # Sniper gets one retry per day

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
OPTIONS_SPREAD_MAX_PCT = 0.15  # V2.3.10: Widened from 5% to 15% - ATM contracts have wider spreads
OPTIONS_SPREAD_WARNING_PCT = 0.25  # V2.3.7: Widened from 15% - fast markets have wide spreads
OPTIONS_MIN_OPEN_INTEREST = (
    100  # V2.3.7: Lowered from 200 - 0DTE contracts have even lower OI in practice
)

# Confidence-Weighted Tiered Stops
# V2.4.3 FIX: CORRECTED - Higher confidence = MORE contracts (was inverted!)
# Logic: Bet big on high-conviction signals, bet small on low-conviction
OPTIONS_STOP_TIERS = {
    # Low confidence = small position, tight stop (cut losses fast)
    # High confidence = larger position, wider stop (give it room)
    3.00: {"stop_pct": 0.15, "contracts": 5},  # Low confidence: small bet, tight stop
    3.25: {"stop_pct": 0.18, "contracts": 8},  # Medium-low confidence
    3.50: {"stop_pct": 0.22, "contracts": 10},  # Medium-high confidence
    3.75: {"stop_pct": 0.25, "contracts": 12},  # High confidence: biggest bet, wider stop
}

# V2.3.8: 0DTE-specific stop override (PART 14 Pitfall 2)
# 0DTE options move extremely fast - by time stop triggers, slippage can double the loss
# Use tighter stops (15%) to limit max loss even with slippage to ~30%
# NOTE: StopMarketOrder fills at next available price after trigger, not the stop price
# For 0DTE, accept smaller position sizes in exchange for tighter risk control
OPTIONS_0DTE_STOP_PCT = 0.15  # V2.3.8: 15% stop for 0DTE (was using 20-30% tiers)

# Profit Target
OPTIONS_PROFIT_TARGET_PCT = 0.50  # +50% profit target

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
# Gives 2 hours buffer before close for retries and avoids end-of-day volatility
OPTIONS_EXPIRING_TODAY_FORCE_CLOSE_HOUR = 14  # Force close expiring options at 14:00
OPTIONS_EXPIRING_TODAY_FORCE_CLOSE_MINUTE = 0

# Contract Selection
# Options chain filter (must cover BOTH Intraday 0-2 DTE AND Swing 5-45 DTE)
OPTIONS_DTE_MIN = 0  # Minimum days to expiration (Intraday mode)
OPTIONS_DTE_MAX = 45  # Maximum days to expiration (Swing mode)
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

# Max Trades Per Day
OPTIONS_MAX_TRADES_PER_DAY = 1

# -----------------------------------------------------------------------------
# V2.1.1 DUAL-MODE DTE BOUNDARIES
# -----------------------------------------------------------------------------
# V2.3.22: Hard Swing Floor - raised from 6 to 14 DTE
# Evidence from V2.3.20 backtest: 6 DTE lost 90% vs 28 DTE lost 60%
# 14 DTE options react less violently to overnight gaps
# With single-leg exit at DTE=4, entering at DTE=14 gives 10+ days hold
OPTIONS_SWING_DTE_MIN = 14  # Minimum DTE for Swing Mode (reduces gap risk)
OPTIONS_SWING_DTE_MAX = 45  # Maximum DTE for Swing Mode
OPTIONS_INTRADAY_DTE_MIN = 0  # Minimum DTE for Intraday Mode
OPTIONS_INTRADAY_DTE_MAX = 1  # V2.3.4: True 0DTE intraday (0-1 DTE only)

# -----------------------------------------------------------------------------
# V2.3 DEBIT SPREAD CONFIGURATION
# -----------------------------------------------------------------------------
# Debit spreads: defined risk, survives whipsaw, no stop loss needed
# Bull Call Spread: Regime > 60 (bullish)
# Bear Put Spread: Regime < 45 (bearish)
# No Trade: Regime 45-60 (neutral, no edge)

# Regime thresholds for spread direction
SPREAD_REGIME_BULLISH = 60  # Regime > 60: Bull Call Spread
SPREAD_REGIME_BEARISH = 45  # Regime < 45: Bear Put Spread
SPREAD_REGIME_CRISIS = 30  # Regime < 30: Protective Puts only (no spreads)

# VIX filters for entry
SPREAD_VIX_MAX_BULL = 30  # Max VIX for Bull Call Spread entry
SPREAD_VIX_MAX_BEAR = 35  # Max VIX for Bear Put Spread entry (allow higher)

# Spread width (strike difference between legs)
# V2.4.3: WIDTH-BASED short leg selection (fixes "delta trap" in backtesting)
# Problem: Delta values jump (0.45 → 0.25) leaving gaps where no "perfect" delta exists
# Solution: Select short leg by STRIKE WIDTH, not delta. Delta is soft preference only.
SPREAD_SHORT_LEG_BY_WIDTH = True  # V2.4.3: Use strike width for short leg (not delta)
SPREAD_WIDTH_MIN = 2.0  # V2.4.3: Minimum $2 spread (ensures meaningful max profit)
SPREAD_WIDTH_MAX = 10.0  # V2.4.3: Maximum $10 spread (caps risk)
SPREAD_WIDTH_TARGET = 5.0  # V2.4.3: Target $5 width (primary selection criterion)

# DTE for debit spreads (per V2.3 spec)
# V2.3.22: Raised from 10 to 14 - spreads need same gap cushion as single-leg
SPREAD_DTE_MIN = 14  # Minimum 14 DTE (avoid gamma acceleration + gap risk)
SPREAD_DTE_MAX = 21  # Maximum 21 DTE (reasonable theta)
SPREAD_DTE_EXIT = 5  # Close by 5 DTE remaining

# Exit targets
SPREAD_PROFIT_TARGET_PCT = 0.50  # Take profit at 50% of max profit
SPREAD_STOP_LOSS_PCT = 0.50  # V2.4.2: Stop loss at 50% of entry debit (max loss = 50% of net debit)
SPREAD_REGIME_EXIT_BULL = 45  # Exit Bull Call if regime drops below 45
SPREAD_REGIME_EXIT_BEAR = 60  # Exit Bear Put if regime rises above 60

# Delta targets for spread legs - V2.3.21 "Smart Swing" Strategy
# ITM Long Leg / OTM Short Leg: Prioritize execution with wider delta range
# V2.3.24: Widened DELTA_MIN from 0.55 → 0.50 to include ATM contracts
SPREAD_LONG_LEG_DELTA_MIN = 0.50  # V2.3.24: Include ATM (was 0.55)
SPREAD_LONG_LEG_DELTA_MAX = 0.85  # V2.3.21: ITM range (was 0.60 ATM)
SPREAD_SHORT_LEG_DELTA_MIN = 0.10  # V2.3.7: Accept more OTM (was 0.15)
SPREAD_SHORT_LEG_DELTA_MAX = 0.50  # V2.3.7: Accept closer to ATM (was 0.45)

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
QQQ_NOISE_THRESHOLD = 0.35  # Minimum QQQ move to consider trading (was 0.15%)

# Debit Fade (Mean Reversion) - Gate 3a - The Sniper Window
INTRADAY_DEBIT_FADE_MIN_SCORE = 45  # Micro score >= 45 (MICRO_SCORE_MODERATE)
INTRADAY_FADE_MIN_MOVE = 0.50  # V2.3.16: Min move for FADE (was INTRADAY_DEBIT_FADE_MIN_MOVE)
INTRADAY_FADE_MAX_MOVE = 1.20  # V2.3.16: Max move - don't fade runaway trends/crashes
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
INTRADAY_ITM_MIN_VIX = 11.5  # V2.3.12: Lowered from 25 - enable more 0-DTE ITM momentum trades
INTRADAY_ITM_MIN_MOVE = 0.8  # QQQ move >= 0.8%
INTRADAY_ITM_MIN_SCORE = 50  # Micro score >= 50
# V2.3.19: Time window moved from hardcoded to config
INTRADAY_ITM_START = "10:00"  # Entry window start
INTRADAY_ITM_END = "13:30"  # Entry window end (earlier than FADE - momentum fades after lunch)
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

# V2.3.16: Direction Conflict Resolution
# Skip intraday FADE when main regime strongly disagrees
DIRECTION_CONFLICT_BULLISH_THRESHOLD = 65  # Regime > 65 = strong bullish, don't fade rallies
DIRECTION_CONFLICT_BEARISH_THRESHOLD = 40  # Regime < 40 = strong bearish, don't fade dips

# V2.5: Grind-Up Override - capture rallies missed in CAUTIOUS regime
# Problem: Feb 4 missed +1.2% rally because VIX was STABLE (CAUTIOUS regime = NO_TRADE)
# Solution: In CAUTIOUS regime, if QQQ is UP_STRONG and macro score is safe, ride the rally
GRIND_UP_OVERRIDE_ENABLED = True
GRIND_UP_MIN_MOVE = 0.50  # Minimum QQQ move to trigger override (0.50% = UP_STRONG)
GRIND_UP_MACRO_SAFE_MIN = 40  # Macro regime score must be > 40 to avoid bear traps

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

# V2.3.21: Spread scan throttle to reduce log noise
# Only attempt spread selection every 15 minutes (not every minute)
SPREAD_SCAN_THROTTLE_MINUTES = 15

# V2.4.3: Spread FAILURE cooldown - if spread construction fails, don't retry for 4 hours
# Problem: Engine retried 340 times when no valid contracts existed
# Solution: After failure, enter 4-hour cooldown (market conditions won't change that fast)
SPREAD_FAILURE_COOLDOWN_HOURS = 4

# V2.5: Max concurrent spreads - limit exposure from spread positions
# Problem: Mar 25-26 had two spreads open simultaneously ($19K exposure)
# Solution: Only allow 1 active spread at a time
MAX_CONCURRENT_SPREADS = 1

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
MARGIN_PRE_CHECK_BUFFER = 1.20  # Require 20% extra margin buffer

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

TRADED_SYMBOLS = ["TQQQ", "SOXL", "QLD", "SSO", "TNA", "FAS", "TMF", "PSQ"]
PROXY_SYMBOLS = ["SPY", "RSP", "HYG", "IEF"]
ALL_SYMBOLS = TRADED_SYMBOLS + PROXY_SYMBOLS

# Trend symbols (overnight hold allowed)
TREND_SYMBOLS = ["QLD", "SSO", "TNA", "FAS"]

# Mean Reversion symbols (intraday only, must close by 15:45)
MR_SYMBOLS = ["TQQQ", "SOXL"]
