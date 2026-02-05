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

INITIAL_CAPITAL = 50_000  # V3.0: Starting capital for backtests
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
RESERVED_OPTIONS_PCT = 0.25  # Reserve 25% for options allocation

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

# Factor Weights (V2.3: Added VIX, rebalanced; V2.26: Added Chop)
WEIGHT_TREND = 0.25  # V2.26: Reduced from 0.30 to make room for chop factor
WEIGHT_VIX = 0.20  # V2.3 NEW: Implied volatility for options pricing
WEIGHT_VOLATILITY = 0.15  # V2.3: Reduced from 0.25 (realized vol)
WEIGHT_BREADTH = 0.20  # V2.3: Increased from 0.15
WEIGHT_CREDIT = 0.15
WEIGHT_CHOP = 0.05  # V2.26 NEW: Trend quality/consistency factor (ADX-based)

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
VIX_LEVEL_ELEVATED_MAX = 22.0  # V2.23.1: VIX 18-22 = ELEVATED (was hardcoded)

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
# V2.30: STARTUP GATE — All-Weather (one-time arming, never resets on kill switch)
# =============================================================================
# Separate from Cold Start. Ramps capital over 15 days while allowing defensive
# engines (hedges, bearish options) from day 1. Regime controls WHAT trades,
# gate controls HOW MUCH. Once FULLY_ARMED, stays armed permanently.
STARTUP_GATE_ENABLED = True
STARTUP_GATE_WARMUP_DAYS = 5  # Phase 0: Indicators warming up (hedges only)
STARTUP_GATE_OBSERVATION_DAYS = 5  # Phase 1: Add bearish options (50% size)
STARTUP_GATE_REDUCED_DAYS = 5  # Phase 2: All engines at 50% sizing
STARTUP_GATE_REDUCED_SIZE_MULT = 0.50  # Position size multiplier during OBSERVATION + REDUCED

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
# TNA/FAS swing 5-7% daily - tighter stops to limit damage
# Use ATR×2.5 instead of ATR×3.5 for 3x ETFs
TREND_3X_SYMBOLS = ["TNA", "FAS"]  # 3× leveraged symbols needing tighter stops
CHANDELIER_3X_BASE_MULT = 2.5  # V2.3.8: Tighter than 2x (3.5) - control 3x volatility
CHANDELIER_3X_TIGHT_MULT = 2.0  # V2.3.8: Tighter than 2x (3.0)
CHANDELIER_3X_TIGHTER_MULT = 1.5  # V2.3.8: Tighter than 2x (2.5)

# Entry/Exit (V3.0: thesis-aligned - trend blocked in Cautious zone)
TREND_ENTRY_REGIME_MIN = 50  # V3.0: Trend entries only in Neutral+ (regime >= 50)
TREND_EXIT_REGIME = 30  # Exit when regime drops to Bear
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

# Trend Engine Allocations (40% total - V2.18 reduced from 55%)
# V2.18: Reduced to prevent margin overflow (was 196% when all 4 triggered)
# Margin impact: 15%×2 + 12%×2 + 8%×3 + 5%×3 = 30% + 24% + 24% + 15% = 93%
TREND_SYMBOL_ALLOCATIONS = {
    "QLD": 0.15,  # V2.18: 15% (was 20%) - 2× Nasdaq (primary)
    "SSO": 0.12,  # V2.18: 12% (was 15%) - 2× S&P 500 (secondary)
    "TNA": 0.08,  # V2.18: 8% (was 12%) - 3× Russell 2000 (small-cap diversification)
    "FAS": 0.05,  # V2.18: 5% (was 8%) - 3× Financials (sector diversification)
}
TREND_TOTAL_ALLOCATION = 0.40  # V2.18: 40% total (was 55%)

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

# V3.0: Regime-Adaptive Stop Multipliers
# In bull markets (regime >= 75), give winners more room to run
# In cautious/bear markets (regime < 50), tighten stops to protect capital
TREND_STOP_REGIME_MULTIPLIERS = {
    75: 1.50,  # Regime >= 75: 150% of base stop (looser - let winners run)
    50: 1.00,  # Regime 50-74: 100% of base stop (standard)
    0: 0.70,  # Regime < 50: 70% of base stop (tighter - protect capital)
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
KILL_SWITCH_SPREAD_DECOUPLE = True

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
# V2.26: DRAWDOWN GOVERNOR (Cumulative Capital Preservation)
# =============================================================================
# Tracks equity high watermark. Scales ALL engine allocations based on
# drawdown from peak. Prevents death-by-a-thousand-cuts (-42% in 2015).
# Bull market impact: zero drag (HWM rises continuously, governor never fires).
DRAWDOWN_GOVERNOR_ENABLED = True
DRAWDOWN_GOVERNOR_LEVELS = {
    0.03: 0.75,  # At -3% from peak → 75% allocation
    0.06: 0.50,  # At -6% from peak → 50% allocation
    0.10: 0.25,  # At -10% from peak → 25% allocation
    0.15: 0.00,  # At -15% from peak → CASH ONLY (SHV + hedges)
}
# V2.29 P1: Dynamic recovery — scales with governor level
# Effective = base × current_scale: 100%→8%, 75%→6%, 50%→4%, 25%→2%
# Replaces flat 12% threshold that trapped governor at 50% for 358 days in 2015
DRAWDOWN_GOVERNOR_RECOVERY_BASE = 0.08

# V3.0: Regime Override for Drawdown Governor
# Problem: In 2017, Governor held bot at 25% for 4 months during bull market.
# HWM was set, market pulled back 11%, Governor scaled to 25%, bot couldn't
# recover because at 25% allocation it couldn't grow equity fast enough.
# This created a death spiral: can't grow → can't escape drawdown → can't grow.
#
# Solution: If regime is clearly bullish (RISK_ON) for N consecutive days,
# trust the regime and force a STEP_UP regardless of recovery percentage.
# Thesis says "regime controls allocation" - stale HWM shouldn't override regime.
GOVERNOR_REGIME_OVERRIDE_ENABLED = True
GOVERNOR_REGIME_OVERRIDE_THRESHOLD = 70  # Regime score must be >= this
GOVERNOR_REGIME_OVERRIDE_DAYS = 5  # Consecutive days at/above threshold
GOVERNOR_REGIME_OVERRIDE_COOLDOWN_DAYS = 10  # Days before another override can trigger

# V2.28: Minimum governor scale for intraday options entry
# V3.0: Lowered from 1.0 to 0.75 — options should reduce sizing during drawdowns,
# not shut off entirely. 1.0 created a 6.5-month dead zone in 2017 where ANY drawdown
# from equity peak blocked ALL options entries, while trend positions continued at reduced size.
# V2.32: Further lowered to 0.5 for BULLISH options. BEARISH options use 0.25 (direction-aware).
#
# V2.33 CRITICAL FIX: Thesis-aligned direction-aware governor gating
#
# ROOT CAUSE of V2.32 -80% loss:
# - Regime was 63-71 (bullish) but portfolio had 10-16% drawdown
# - System entered BULL_CALL spreads (wrong for drawdown protection!)
# - Governor liquidated next morning → forced loss → repeat
#
# V2.33 FIX: Direction-aware gating aligned with investment thesis:
# - Governor 0% (shutdown): ONLY bearish PUT spreads allowed
#   * Thesis says "Bear markets: PUT spreads active" - they hedge/profit from decline
#   * Bull spreads blocked - they increase risk during severe drawdowns
# - Governor 1-24%: Only bearish PUT spreads allowed
# - Governor 25-49%: Only bearish PUT spreads allowed
# - Governor 50%+: Both bullish and bearish spreads allowed
#
# Additional V2.33 fixes:
# - Spread margin estimate increased from 2× to 6× safety (broker uses delta margin)
# - Added 20% equity cushion requirement (prevents over-leveraging)
# - ATOMIC OPTIONS CLOSE: ALL options close paths now use _close_options_atomic()
#   * Kill Switch Tier 3 (full liquidation)
#   * Kill Switch Tier 2 (trend exit)
#   * EXPIRATION_HAMMER (options expiring today)
#   * EARLY_EXERCISE_GUARD (ITM options near expiry)
#   * Emergency close (retry exhausted)
#   * This ALWAYS closes shorts first, then longs (prevents naked short margin errors)
GOVERNOR_INTRADAY_OPTIONS_MIN_SCALE = 0.50  # Bull options at 50%+ governor scale
GOVERNOR_INTRADAY_OPTIONS_MIN_SCALE_BEARISH = 0.25  # Bear options allowed even at 25%

# V2.32: Options sizing floor — don't scale options below this even if governor is lower
# Prevents options positions from becoming too small to be meaningful
# Set to 0.0 to allow full scaling, set to 1.0 to exempt options entirely
GOVERNOR_OPTIONS_SIZING_FLOOR = 0.50  # Options never scaled below 50% size

# V2.32: Exempt bearish options from governor sizing entirely
# Bear options REDUCE portfolio risk during drawdowns, so scaling them down is counterproductive
GOVERNOR_EXEMPT_BEARISH_OPTIONS = True  # Bear spreads keep full size during drawdowns

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
VASS_IV_LOW_THRESHOLD = 15  # VIX < 15 = Low IV (use debit spreads, monthly DTE)
VASS_IV_HIGH_THRESHOLD = 25  # VIX > 25 = High IV (use credit spreads, weekly DTE)
VASS_IV_SMOOTHING_MINUTES = 30  # SMA window to prevent strategy flickering

# DTE Ranges by IV Environment (Swing Mode)
VASS_LOW_IV_DTE_MIN = 30  # Low IV: Monthly expiration
VASS_LOW_IV_DTE_MAX = 45
VASS_MEDIUM_IV_DTE_MIN = 7  # Medium IV: Weekly expiration
VASS_MEDIUM_IV_DTE_MAX = 21
VASS_HIGH_IV_DTE_MIN = 7  # High IV: Weekly expiration (credit spreads)
VASS_HIGH_IV_DTE_MAX = 14

# Credit Spread Constraints
CREDIT_SPREAD_MIN_CREDIT = 0.30  # Min $0.30 credit to justify margin risk
CREDIT_SPREAD_WIDTH_TARGET = 5.0  # $5 width for credit spreads
CREDIT_SPREAD_PROFIT_TARGET = 0.50  # Exit at 50% of max profit
CREDIT_SPREAD_STOP_MULTIPLIER = 2.0  # Stop if spread value doubles (100% loss)
CREDIT_SPREAD_SHORT_LEG_DELTA_MIN = 0.25  # Short leg delta range (OTM)
CREDIT_SPREAD_SHORT_LEG_DELTA_MAX = 0.40

# V2.24.1: Elastic Delta Bands — progressive widening when no candidates found
# Each step widens the delta range by ± the step value (e.g., [0.0, 0.03, 0.07, 0.12])
# Step 0 = original range, Step 1 = ±0.03 wider, etc.
# Capped at ELASTIC_DELTA_FLOOR (min delta) and ELASTIC_DELTA_CEILING (max delta)
ELASTIC_DELTA_STEPS = [0.0, 0.03, 0.07, 0.12]
ELASTIC_DELTA_FLOOR = 0.10  # Never search below this delta (too far OTM)
ELASTIC_DELTA_CEILING = 0.95  # Never search above this delta (deep ITM)

# V2.25: IV-adaptive credit floor — lower min credit in high IV to allow fills
# Q1 2022 audit: 116 VASS rejections at VIX > 30 because $0.30 floor filtered all candidates
CREDIT_SPREAD_MIN_CREDIT_HIGH_IV = 0.20  # Reduced floor when VIX exceeds threshold
CREDIT_SPREAD_HIGH_IV_VIX_THRESHOLD = 30.0  # VIX level above which reduced floor applies

# V2.3.14: Intraday trade limits (was 1, blocking all re-entries after first trade)
# V2.3.15: Sniper Logic - allow one retry, not machine gun
INTRADAY_MAX_TRADES_PER_DAY = 2  # Sniper gets one retry per day

# V2.9: Global options trade limits (Bug #4 fix)
# Prevents over-trading when VIX flickers around strategy thresholds
MAX_OPTIONS_TRADES_PER_DAY = 4  # All options combined (swing + intraday)
MAX_SWING_TRADES_PER_DAY = 2  # Swing mode limit

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
    3.00: {"stop_pct": 0.15, "contracts": 5},  # Low confidence: -15% stop
    3.25: {"stop_pct": 0.18, "contracts": 8},  # Medium-low: -18% stop
    3.50: {"stop_pct": 0.22, "contracts": 10},  # Medium-high: -22% stop
    3.75: {"stop_pct": 0.25, "contracts": 12},  # High confidence: -25% stop
}

# V2.3.8: 0DTE-specific stop override (PART 14 Pitfall 2)
# 0DTE options move extremely fast - by time stop triggers, slippage can double the loss
# NOTE: StopMarketOrder fills at next available price after trigger, not the stop price
OPTIONS_0DTE_STOP_PCT = 0.15  # -15% stop for 0DTE

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

# V2.28: Early exercise guard — force close ITM single-leg options near expiry
# Prevents costly early exercise (Q1 2022: 2 exercises cost -$5,614)
EARLY_EXERCISE_GUARD_DTE = 2  # Close if DTE <= 2 and ITM
EARLY_EXERCISE_GUARD_ITM_BUFFER = 0.01  # 1% ITM buffer (strike vs underlying)

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
OPTIONS_INTRADAY_DTE_MIN = 1  # V2.13: Skip 0DTE (QC backtest data gaps)
OPTIONS_INTRADAY_DTE_MAX = (
    5  # V2.13: Match VASS "nearest weekly" (was 1, caused 306 silent failures)
)

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
SPREAD_DTE_MAX = 45  # V2.19: Widened from 21 to 45 to align with VASS_LOW_IV_DTE (30-45)
SPREAD_DTE_EXIT = 5  # Close by 5 DTE remaining

# Exit targets
SPREAD_PROFIT_TARGET_PCT = 0.50  # Take profit at 50% of max profit (base value)
SPREAD_STOP_LOSS_PCT = (
    0.50  # V2.4.2/V2.27: Stop loss at 50% of entry debit (max loss = 50% of net debit)
)

# V3.0: Regime-Adaptive Profit Targets
# In bull markets (regime >= 75), be greedy - let winners run to 90%
# In cautious/bear markets (regime < 40), take profits quickly at 40%
SPREAD_PROFIT_REGIME_MULTIPLIERS = {
    75: 1.40,  # Regime >= 75: 70% target (1.40 × 50% base = 70%)
    50: 1.00,  # Regime 50-74: 50% target (standard)
    40: 1.00,  # Regime 40-49: 50% target (cautious)
    0: 0.80,  # Regime < 40: 40% target (0.80 × 50% base = 40%)
}

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
SPREAD_REGIME_EXIT_BULL = 45  # Exit Bull Call if regime drops below 45
SPREAD_REGIME_EXIT_BEAR = 60  # Exit Bear Put if regime rises above 60

# V2.22: Neutrality Exit (Hysteresis Shield)
# Close flat spreads when regime enters dead zone (45-60) — no directional edge
SPREAD_NEUTRALITY_EXIT_ENABLED = True
SPREAD_NEUTRALITY_EXIT_PNL_BAND = 0.10  # ±10% P&L considered "flat"

# V2.16-BT: Commission-aware profit targets
# Round-trip commission estimate per spread (entry + exit, both legs)
# IBKR: ~$0.65/contract × 2 legs × 2 (entry+exit) = $2.60/spread
SPREAD_COMMISSION_PER_CONTRACT = 2.60  # Estimated round-trip commission per spread

# -----------------------------------------------------------------------------
# V2.17-BT: COMBO ORDER RETRY & KILL SWITCH COORDINATION
# -----------------------------------------------------------------------------
# Fixes: USER-1 (naked exposure), USER-3 (kill switch bypass), RPT-9 (no retry)
# All spread closes now go through Router with retry + sequential fallback

# Combo order retry settings
COMBO_ORDER_MAX_RETRIES = 3  # Try atomic ComboMarketOrder up to 3 times
COMBO_ORDER_FALLBACK_TO_SEQUENTIAL = True  # If all retries fail, use sequential close

# Sequential fallback: close SHORT leg first (buy back), then LONG leg (sell)
# This prevents naked short exposure - worst case is holding a long temporarily

# State management for stuck spreads
SPREAD_LOCK_CLEAR_ON_FAILURE = True  # Clear is_closing lock if all close attempts fail
# When True: Spread becomes available for retry on next iteration
# When False: Spread stays locked, requires manual intervention

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

# -----------------------------------------------------------------------------
# V2.11: PRE-BACKTEST SAFETY FIXES (Pitfalls #6-8)
# -----------------------------------------------------------------------------
# Pitfall #6: Margin Collateral Lock-Out
# Options sizing must cap by actual available margin, not just portfolio %
# V2.12 Fix #4: Raised from $5K to $10K - 8-lot spread requires ~$8K margin
# V3.0 SCALABILITY FIX: Converted to percentage-based for portfolio scaling
OPTIONS_MAX_MARGIN_CAP = 10000  # $10K — LEGACY, kept for backwards compatibility
OPTIONS_MAX_MARGIN_PCT = 0.20  # 20% of portfolio for all options combined

# V2.18: Hardcoded Sizing Caps (Fix for MarginBuyingPower sizing bug)
# Evidence: Architect found $14K trade vs $5K expected - sizing used MarginBuyingPower
# V3.0 SCALABILITY FIX: Converted to percentage-based for portfolio scaling
# At $50K: 15% = $7,500, 8% = $4,000 (same as original hardcoded values)
# At $200K: 15% = $30,000, 8% = $16,000 (scales properly)
SWING_SPREAD_MAX_DOLLARS = 7500  # LEGACY — kept for backwards compatibility
SWING_SPREAD_MAX_PCT = 0.15  # 15% of portfolio for swing spreads (14-21 DTE)
INTRADAY_SPREAD_MAX_DOLLARS = 4000  # LEGACY — kept for backwards compatibility
INTRADAY_SPREAD_MAX_PCT = 0.08  # 8% of portfolio for intraday spreads (1-5 DTE)

# V3.0: Minimum margin percentage to allow options trading
# Replaces hardcoded $1,000 check in main.py
OPTIONS_MIN_MARGIN_PCT = 0.02  # 2% of portfolio minimum margin to trade options

# V2.21: Rejection-aware spread sizing
# Pre-submission: use 80% of reported margin (20% buffer for broker calc differences)
SPREAD_MARGIN_SAFETY_FACTOR = 0.80
# Post-rejection: apply to broker-reported Free Margin for adaptive retry cap
SPREAD_REJECTION_MARGIN_SAFETY = 0.80

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

# V2.14 Fix #22: Conservative spread sizing to prevent tier cap violations
# Evidence: Trade #20 sized at mid price $2.75 but filled at $3.96 (44% slippage)
# Solution: Use ASK/BID prices + buffer for worst-case sizing
SPREAD_SIZING_SLIPPAGE_BUFFER = 0.10  # 10% buffer on top of ASK/BID pricing

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

# V2.19: VIX Floor for DEBIT_FADE
# In low VIX (<13.5) "apathy" markets, mean reversion fails - trends persist longer
# Evidence: V2.18 backtests showed DEBIT_FADE losses when VIX < 13.5
INTRADAY_DEBIT_FADE_VIX_MIN = 13.5  # Disable DEBIT_FADE in "apathy" market

# Debit Fade (Mean Reversion) - Gate 3a - The Sniper Window
INTRADAY_DEBIT_FADE_MIN_SCORE = 45  # Micro score >= 45 (MICRO_SCORE_MODERATE)
INTRADAY_FADE_MIN_MOVE = 0.50  # V2.3.16: Min move for FADE (was INTRADAY_DEBIT_FADE_MIN_MOVE)
INTRADAY_FADE_MAX_MOVE = 1.20  # V2.3.16: Max move - don't fade runaway trends/crashes
INTRADAY_DEBIT_FADE_VIX_MAX = 25  # VIX < 25
INTRADAY_DEBIT_FADE_START = "10:15"  # V2.14: Widened from 10:30 to capture more signals
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
INTRADAY_ITM_STOP = 0.50  # -50% stop
INTRADAY_ITM_TRAIL_TRIGGER = 0.20  # Trail after +20%
INTRADAY_ITM_TRAIL_PCT = 0.50  # Trail at 50% of gains

# V2.15: Strategy-aware intraday delta bounds
# DEBIT_FADE: Mean reversion needs OTM options (delta 0.20-0.50)
INTRADAY_DEBIT_FADE_DELTA_MIN = 0.20  # OTM for mean reversion
INTRADAY_DEBIT_FADE_DELTA_MAX = 0.50  # Near ATM max

# ITM_MOMENTUM: Stock replacement needs ITM options (delta 0.60-0.85)
INTRADAY_ITM_DELTA_MIN = 0.60  # ITM for momentum
INTRADAY_ITM_DELTA_MAX = 0.85  # Deep ITM max

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
INTRADAY_OPTIONS_OFFSET_MINUTES = 30  # Intraday options close = market_close - 30 min

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
MARGIN_PRE_CHECK_BUFFER = 1.50  # V3.0: Require 50% extra margin buffer (was 1.20)

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
