# Section 16: Appendix - Parameters

## 16.1 Purpose

This appendix consolidates **all tunable parameters** from across the Alpha NextGen system into a single reference. All values should be defined in `config.py` for easy modification.

---

## 16.2 Capital Engine Parameters

### Phase Definitions

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `PHASE_SEED_MIN` | $50,000 | Minimum equity for SEED phase |
| `PHASE_SEED_MAX` | $99,999 | Maximum equity for SEED phase |
| `PHASE_GROWTH_MIN` | $100,000 | Minimum equity for GROWTH phase |
| `PHASE_GROWTH_MAX` | $499,999 | Maximum equity for GROWTH phase |

### Phase Transition Rules

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `UPWARD_TRANSITION_DAYS` | 5 | Consecutive days above threshold for upgrade |
| `DOWNWARD_TRANSITION_DAYS` | 1 | Days below threshold for downgrade (immediate) |

### Position Limits by Phase

| Parameter | SEED | GROWTH | Description |
|-----------|:----:|:------:|-------------|
| `MAX_SINGLE_POSITION_PCT` | 50% | 40% | Maximum single position size |
| `TARGET_VOLATILITY` | 20% | 20% | Target annualized volatility |
| `KILL_SWITCH_PCT` | 5% | 5% | Daily loss threshold (V2.3.17: raised from 3%) |

### Lockbox Parameters

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `LOCKBOX_MILESTONE_1` | $100,000 | First lockbox trigger |
| `LOCKBOX_MILESTONE_2` | $200,000 | Second lockbox trigger |
| `LOCKBOX_LOCK_PCT` | 10% | Percentage of equity to lock |

---

## 16.3 Regime Engine Parameters

### Factor Weights (V2.3: Added VIX)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `WEIGHT_TREND` | 0.30 | Trend factor weight (30%) - V2.3: reduced from 0.45 |
| `WEIGHT_VIX` | 0.20 | VIX (implied vol) factor weight (20%) - V2.3 NEW |
| `WEIGHT_VOLATILITY` | 0.15 | Realized volatility factor weight (15%) - V2.3: reduced from 0.25 |
| `WEIGHT_BREADTH` | 0.20 | Breadth factor weight (20%) - V2.3: increased from 0.15 |
| `WEIGHT_CREDIT` | 0.15 | Credit factor weight (15%) |

### VIX Level Factor (V2.3 NEW)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `VIX_LOW_THRESHOLD` | 15 | Below = complacent market, cheap options |
| `VIX_NORMAL_THRESHOLD` | 22 | 15-22 = normal volatility |
| `VIX_HIGH_THRESHOLD` | 30 | 22-30 = elevated fear |
| `VIX_EXTREME_THRESHOLD` | 40 | Above = crisis mode |

### VIX Factor Scoring

| VIX Level | Score | Market State |
|:---------:|:-----:|--------------|
| < 15 | 100 | Complacent, cheap options |
| 15-18 | 85 | Low normal |
| 18-22 | 70 | Normal |
| 22-26 | 50 | Mild fear |
| 26-30 | 30 | Elevated |
| 30-40 | 15 | High fear |
| > 40 | 0 | Crisis mode |

### Smoothing

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `REGIME_SMOOTHING_ALPHA` | 0.30 | Exponential smoothing factor |

### Regime Score Thresholds

| Parameter | Value | State |
|-----------|:-----:|-------|
| `REGIME_RISK_ON` | 70 | RISK_ON (70-100) |
| `REGIME_NEUTRAL` | 50 | NEUTRAL (50-69) |
| `REGIME_CAUTIOUS` | 40 | CAUTIOUS (40-49) |
| `REGIME_DEFENSIVE` | 30 | DEFENSIVE (30-39) |
| `REGIME_RISK_OFF` | 0 | RISK_OFF (0-29) |

### Trend Factor Parameters

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `SMA_FAST` | 20 | Fast moving average period |
| `SMA_MED` | 50 | Medium moving average period |
| `SMA_SLOW` | 200 | Slow moving average period |
| `EXTENDED_THRESHOLD` | 1.05 | 5% above SMA200 = extended |
| `OVERSOLD_THRESHOLD` | 0.95 | 5% below SMA200 = oversold |

### Volatility Factor Parameters

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `VOL_LOOKBACK` | 20 | Realized volatility lookback (days) |
| `VOL_PERCENTILE_LOOKBACK` | 252 | Percentile ranking lookback (days) |

### Breadth Factor Parameters

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `BREADTH_LOOKBACK` | 20 | RSP vs SPY comparison period |

### Credit Factor Parameters

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `CREDIT_LOOKBACK` | 20 | HYG vs IEF comparison period |

---

## 16.4 Cold Start Engine Parameters

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `COLD_START_DAYS` | 5 | Number of days in cold start mode |
| `WARM_ENTRY_SIZE_MULT` | 0.50 | Position size multiplier (50%) |
| `WARM_ENTRY_TIME` | 10:00 ET | Earliest warm entry time |
| `WARM_REGIME_MIN` | 50 | Minimum regime score (exclusive) |
| `WARM_QLD_THRESHOLD` | 60 | Score above which QLD selected |
| `WARM_MIN_SIZE` | $2,000 | Minimum warm entry position |

---

## 16.5 Trend Engine Parameters (V2)

### MA200 + ADX Entry Parameters

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `MA200_PERIOD` | 200 | Moving average period for trend direction |
| `ADX_PERIOD` | 14 | ADX period for momentum strength |
| `ADX_ENTRY_THRESHOLD` | 15 | Minimum ADX for entry (V2.3.12: lowered from 25) |
| `TREND_ADX_EXIT_THRESHOLD` | 10 | ADX below this triggers exit (V2.3.12: lowered from 20) |

### ADX Scoring Thresholds

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `ADX_WEAK_THRESHOLD` | 15 | Below = 0.25 score (V2.3.12: lowered from 20) |
| `ADX_MODERATE_THRESHOLD` | 22 | 15-22 = 0.50 score (V2.5: tuned from 25) |
| `ADX_STRONG_THRESHOLD` | 35 | 22-35 = 0.75, above = 1.0 score |

### Chandelier Stop Parameters (V2.3.6)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `ATR_PERIOD` | 14 | ATR calculation period |
| `CHANDELIER_BASE_MULT` | 3.5 | Initial multiplier (profit < 15%) - V2.3.6: widened from 3.0 |
| `CHANDELIER_TIGHT_MULT` | 3.0 | Medium multiplier (profit 15-25%) - V2.3.6: widened from 2.5 |
| `CHANDELIER_TIGHTER_MULT` | 2.5 | Tight multiplier (profit > 25%) - V2.3.6: widened from 2.0 |
| `PROFIT_TIGHT_PCT` | 0.15 | First tightening threshold (15%) - V2.3.6: raised from 10% |
| `PROFIT_TIGHTER_PCT` | 0.25 | Second tightening threshold (25%) - V2.3.6: raised from 20% |

> **V2.3.6 Rationale:** In choppy markets (like Q1 2024), tight stops were suffocating trades, cutting +2-3% winners short instead of holding for +20% moves. Widened multipliers give trends more breathing room.

### Entry/Exit Thresholds

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `TREND_ENTRY_REGIME_MIN` | 40 | Minimum regime for entry |
| `TREND_EXIT_REGIME` | 30 | Regime score forcing exit |

### V2 Entry Conditions Summary

| Condition | Requirement |
|-----------|-------------|
| Price vs MA200 | Close > MA200 |
| ADX Score | ADX >= 25 (score >= 0.50) |
| Regime | Score >= 40 |

### SMA50 Structural Exit (V2.4)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `TREND_USE_SMA50_EXIT` | True | Enable SMA50 structural exit |
| `TREND_SMA50_PERIOD` | 50 | SMA50 period |
| `TREND_SMA50_BUFFER` | 0.02 | 2% below SMA50 = structural exit |
| `TREND_SMA_CONFIRM_DAYS` | 2 | Days below SMA50 before exit fires |

### Hard Stop Parameters (V2.4)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `TREND_HARD_STOP_QLD_SSO` | 0.15 | 15% hard stop for 2× ETFs |
| `TREND_HARD_STOP_TNA_FAS` | 0.12 | 12% hard stop for 3× ETFs |

### V2 Exit Conditions Summary

| Condition | Trigger |
|-----------|---------|
| MA200 Cross | Close < MA200 |
| SMA50 Structural | Close < SMA50 - 2% for 2 days (V2.4) |
| ADX Weakness | ADX < 10 (V2.3.12: lowered from 20) |
| Chandelier Stop | Price < Highest High − (ATR × multiplier) |
| Hard Stop | 15% QLD/SSO, 12% TNA/FAS (V2.4) |
| Regime Exit | Score < 30 |

---

## 16.6 Mean Reversion Engine Parameters (V2.1)

### RSI Parameters

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `RSI_PERIOD` | 5 | Fast RSI period |
| `RSI_THRESHOLD` | VIX-adjusted | Oversold threshold (20-30) |

### Entry Conditions

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `MR_DROP_THRESHOLD` | 0.025 | 2.5% drop from open |
| `MR_VOLUME_MULT` | 1.2 | Volume > 1.2× average |
| `MR_WINDOW_START` | 10:00 ET | Earliest entry time |
| `MR_WINDOW_END` | 15:00 ET | Latest entry time |
| `MR_REGIME_MIN` | 40 | Minimum regime score |

### Exit Conditions

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `MR_TARGET_PCT` | 0.02 | +2% profit target |
| `MR_STOP_PCT` | VIX-adjusted | 4-8% stop loss |
| `MR_FORCE_EXIT_TIME` | 15:45 ET | Mandatory close time |

### VIX Regime Filter Parameters (V2.1)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `VIX_NORMAL_THRESHOLD` | 20 | Below = NORMAL regime |
| `VIX_CAUTION_THRESHOLD` | 30 | 20-30 = CAUTION regime |
| `VIX_HIGH_RISK_THRESHOLD` | 40 | 30-40 = HIGH_RISK regime |

### VIX-Adjusted Parameters Table

| VIX Level | Regime | Allocation | RSI Threshold | Stop Loss |
|:---------:|--------|:----------:|:-------------:|:---------:|
| < 20 | NORMAL | 10% | RSI < 30 | 8% |
| 20-30 | CAUTION | 5% | RSI < 25 | 6% |
| 30-40 | HIGH_RISK | 2% | RSI < 20 | 4% |
| > 40 | CRASH | **0%** (disabled) | — | — |

---

## 16.7 Hedge Engine Parameters

### Regime Thresholds

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `HEDGE_LEVEL_1` | 40 | Score below which hedging begins |
| `HEDGE_LEVEL_2` | 30 | Score below which medium hedge |
| `HEDGE_LEVEL_3` | 20 | Score below which full hedge |

### TMF Allocation

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `TMF_LIGHT` | 0.10 | TMF at DEFENSIVE (10%) |
| `TMF_MEDIUM` | 0.15 | TMF at RISK_OFF moderate (15%) |
| `TMF_FULL` | 0.20 | TMF at RISK_OFF severe (20%) |

### PSQ Allocation

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `PSQ_MEDIUM` | 0.05 | PSQ at RISK_OFF moderate (5%) |
| `PSQ_FULL` | 0.10 | PSQ at RISK_OFF severe (10%) |

### Rebalancing

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `HEDGE_REBAL_THRESHOLD` | 0.02 | 2% difference to rebalance |

---

## 16.8 Yield Sleeve Parameters

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `SHV_MIN_TRADE` | $10,000 | Minimum cash for SHV purchase - V2.3.6: raised from $2K to reduce churn |
| `SHV_MAX_ALLOCATION` | None | No maximum (fills available cash) |

> **V2.3.6 Rationale:** Small daily fluctuations in Trend positions triggered excessive SHV rebalancing. Raising threshold from $2K to $10K reduces trading costs and churn.

---

## 16.8.1 Options Engine Parameters (V2.24.2)

### Allocation (V2.3/V2.18)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `OPTIONS_TOTAL_ALLOCATION` | 0.25 | Total options budget (25% — V2.3: raised from 20%) |
| `OPTIONS_SWING_ALLOCATION` | 0.1875 | Swing Mode (18.75% — 75% of 25%) |
| `OPTIONS_INTRADAY_ALLOCATION` | 0.0625 | Intraday Mode (6.25% — 25% of 25%) |
| `OPTIONS_ALLOCATION_MIN` | 0.20 | Floor for options allocation |
| `OPTIONS_ALLOCATION_MAX` | 0.30 | Ceiling for options allocation |

### Contract Selection (V2.3.6)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `OPTIONS_MIN_OPEN_INTEREST` | 50 | Minimum OI - V6.8: lowered from 100 for thin chains |
| `OPTIONS_SPREAD_WARNING_PCT` | 0.30 | Max bid-ask spread - V6.8: widened from 0.25 to reduce rejections |
| `OPTIONS_DELTA_TOLERANCE` | 0.20 | Delta tolerance from target - V2.3.5: widened from 0.15 |

### 4-Factor Entry Scoring

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `OPTIONS_ADX_PERIOD` | 14 | ADX lookback period |
| `OPTIONS_MA_PERIOD` | 200 | Moving average for momentum |
| `OPTIONS_IV_LOOKBACK` | 252 | IV rank lookback (1 year) |
| `OPTIONS_ENTRY_SCORE_MIN` | 3.0 | Minimum score for entry (out of 4.0) |

### Intraday "Sniper" Window (V2.3.6)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| Start Time | 10:00 | V2.3.6: Opened from 10:30 to capture early momentum |
| End Time | 15:00 | Stop scanning for new entries |
| Force Exit | 15:30 | Close all intraday positions |

> **V2.3.6 Rationale:** The 10:00-10:30 window has highest gamma opportunities for momentum strategies. Removed hardcoded gatekeeper that was blocking this window.

### ADX Scoring (Factor 1)

| ADX Value | Score |
|:---------:|:-----:|
| < 20 | 0.00 |
| 20-25 | 0.50 |
| 25-30 | 0.75 |
| > 30 | 1.00 |

### IV Rank Scoring (Factor 3)

| IV Rank | Score |
|:-------:|:-----:|
| 0-20% | 0.25 |
| 20-40% | 0.50 |
| 40-60% | 0.75 |
| 60-80% | 1.00 |
| 80-100% | 0.75 |

### Tiered Stop Losses

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `OPTIONS_STOP_TIER_1` | 0.20 | Score 3.0-3.25: 20% stop |
| `OPTIONS_STOP_TIER_2` | 0.22 | Score 3.25-3.5: 22% stop |
| `OPTIONS_STOP_TIER_3` | 0.25 | Score 3.5-3.75: 25% stop |
| `OPTIONS_STOP_TIER_4` | 0.30 | Score 3.75-4.0: 30% stop |
| `OPTIONS_PROFIT_TARGET_PCT` | 0.50 | +50% profit target |

### Time Constraints

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `OPTIONS_ENTRY_START` | 10:00 ET | Earliest entry time |
| `OPTIONS_ENTRY_END` | 14:30 ET | Latest entry time |
| `OPTIONS_LATE_DAY_TIME` | 14:30 ET | Force tight stops after this |
| `OPTIONS_FORCE_EXIT_HOUR` | 15 | Force close hour (3 PM ET) |
| `OPTIONS_FORCE_EXIT_MINUTE` | 45 | Force close minute (:45) |

### Greeks Monitoring (Circuit Breaker Level 5)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `CB_DELTA_MAX` | 0.80 | Max delta exposure per position |
| `CB_GAMMA_WARNING` | 0.05 | Gamma warning threshold near expiry |
| `CB_VEGA_MAX` | 0.50 | Max vega exposure |
| `CB_THETA_WARNING` | -0.02 | Daily theta decay warning (-2%) |

### Contract Selection

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `OPTIONS_DTE_MIN` | 0 | Minimum days to expiry (global) |
| `OPTIONS_DTE_MAX` | 60 | Maximum days to expiry (V2.23: raised from 30) |
| `OPTIONS_DELTA_MIN` | 0.40 | Minimum delta (ATM range) |
| `OPTIONS_DELTA_MAX` | 0.60 | Maximum delta (ATM range) |
| `OPTIONS_MIN_PREMIUM` | 0.50 | Minimum premium per contract ($0.50) |

### Universe Filter (V2.23)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| Strike range | -25 to +25 | 50 strikes total (V2.23: was -8 to +5) |
| DTE range | 0 to 60 | Full DTE range (V2.23: was 0 to 30) |

### V2.1.1 Dual-Mode Architecture

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `OPTIONS_TOTAL_ALLOCATION` | 0.25 | Total options budget (25% — V2.3) |
| `OPTIONS_SWING_ALLOCATION` | 0.1875 | Swing Mode (18.75%) |
| `OPTIONS_INTRADAY_ALLOCATION` | 0.0625 | Intraday Mode (6.25%) |

### V2.1.1 DTE Boundaries

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `SPREAD_DTE_MIN` | 14 | Minimum DTE for Swing spreads (V2.24.2: raised from 6) |
| `SPREAD_DTE_MAX` | 45 | Maximum DTE for Swing spreads |
| `OPTIONS_INTRADAY_DTE_MIN` | 1 | Minimum DTE for Intraday Mode |
| `OPTIONS_INTRADAY_DTE_MAX` | 5 | Maximum DTE for Intraday Mode |

> **V2.24.2 NOTE:** VASS overrides `SPREAD_DTE_MIN`/`SPREAD_DTE_MAX` per IV environment. Callers must pass `dte_min`/`dte_max` to `select_spread_legs()` and `check_spread_entry_signal()` to avoid the DTE double-filter bug.

### VASS (VIX-Adaptive Strategy Selection) — V2.8

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `VASS_IV_LOW_THRESHOLD` | 15 | Below = Low IV environment |
| `VASS_IV_HIGH_THRESHOLD` | 25 | Above = High IV environment |
| `VASS_IV_SMOOTHING_MINUTES` | 30 | VIX smoothing window (minutes) |

| IV Environment | VIX Range | Strategy | DTE Range |
|----------------|-----------|----------|-----------|
| Low IV | < 15 | Debit Spreads | 30-45 (Monthly) |
| Medium IV | 15-25 | Debit Spreads | 7-21 (Weekly) |
| High IV | > 25 | Credit Spreads | 5-28 (V6.8: was 7-21) |

### Credit Spread Parameters (V2.8/V2.10)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `CREDIT_SPREAD_MIN_CREDIT` | 0.30 | Minimum credit received ($0.30) |
| `CREDIT_SPREAD_WIDTH_TARGET` | 5.0 | Target spread width ($5) |
| `CREDIT_SPREAD_SHORT_LEG_DELTA_MIN` | 0.25 | Short leg minimum delta |
| `CREDIT_SPREAD_SHORT_LEG_DELTA_MAX` | 0.40 | Short leg maximum delta |
| `CREDIT_SPREAD_PROFIT_TARGET` | 0.50 | 50% of max profit |
| `CREDIT_SPREAD_STOP_MULTIPLIER` | 2.0 | Stop = spread doubles |

### Elastic Delta Bands (V2.24.1)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `ELASTIC_DELTA_STEPS` | [0.0, 0.03, 0.07, 0.12] | Progressive widening steps |
| `ELASTIC_DELTA_FLOOR` | 0.10 | Absolute minimum delta |
| `ELASTIC_DELTA_CEILING` | 0.95 | Absolute maximum delta |

### Width-Based Short Leg Selection (V2.4.3)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `SPREAD_SHORT_LEG_BY_WIDTH` | True | Use strike width instead of delta |
| `SPREAD_WIDTH_MIN` | 2.0 | Minimum spread width ($2) |
| `SPREAD_WIDTH_MAX` | 10.0 | Maximum spread width ($10) |
| `SPREAD_WIDTH_TARGET` | 3.0 | Target spread width (V6.8: was 5.0) |

### Sizing Caps (V2.18)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `SWING_SPREAD_MAX_DOLLARS` | 7,500 | Max $ per swing spread |
| `INTRADAY_SPREAD_MAX_DOLLARS` | 4,000 | Max $ per intraday spread |

### ATR-Based Stops (V6.8)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `OPTIONS_ATR_STOP_MULTIPLIER` | 1.0 | ATR multiplier for stops (V6.8: was 1.5) |
| `OPTIONS_ATR_STOP_MIN_PCT` | 0.15 | Minimum stop % (V6.8: was 0.20) |
| `OPTIONS_ATR_STOP_MAX_PCT` | 0.30 | Maximum stop % (V6.8: was 0.50 - prevents 50% losses) |
| `OPTIONS_USE_ATR_STOPS` | True | Use ATR-based stops (vs legacy tier-based) |

### UVXY Conviction Thresholds (V6.8)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `MICRO_UVXY_BEARISH_THRESHOLD` | +2.5% | UVXY up → PUT conviction (V6.8: was +3%) |
| `MICRO_UVXY_BULLISH_THRESHOLD` | -2.5% | UVXY down → CALL conviction (V6.8: was -3%) |
| `MICRO_VIX_CRISIS_LEVEL` | 35 | VIX > 35 → CRISIS (BEARISH conviction) |
| `MICRO_VIX_COMPLACENT_LEVEL` | 12 | VIX < 12 → COMPLACENT (BULLISH conviction) |

### Assignment Risk Management (V6.8)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `ASSIGNMENT_MARGIN_BUFFER_ENABLED` | True | Require extra margin for ITM shorts |
| `ASSIGNMENT_MARGIN_BUFFER_PCT` | 0.10 | Margin buffer % (V6.8: was 0.20 - reduced instant exits) |
| `ASSIGNMENT_MARGIN_AUTO_REDUCE` | True | Auto-reduce if buffer insufficient |

### Limit Order Execution (V2.19)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `OPTIONS_USE_LIMIT_ORDERS` | True | Use marketable limit orders |
| `OPTIONS_LIMIT_SLIPPAGE_PCT` | 0.05 | 5% slippage tolerance |
| `OPTIONS_MAX_SPREAD_PCT` | 0.20 | Max bid-ask spread % (block if wider) |

### Safety Rules (V2.4.1)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `SWING_FALLBACK_ENABLED` | False | No naked single-leg fallback |
| Friday Firewall | Active | Close swing options before weekend |
| VIX > 25 | Active | Close ALL swing options in high VIX |

### Neutrality Exit (V2.22)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `SPREAD_NEUTRALITY_EXIT_ENABLED` | True | Exit flat spreads in neutral regime |
| `SPREAD_NEUTRALITY_EXIT_PNL_BAND` | 0.10 | Within ±10% of breakeven = flat |

### V2.1.1 VIX Direction Thresholds (Micro Regime Engine)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `VIX_DIRECTION_FALLING_FAST` | -5.0% | Strong recovery threshold |
| `VIX_DIRECTION_FALLING` | -2.0% | Recovery threshold |
| `VIX_DIRECTION_STABLE_LOW` | -2.0% | Stable range lower bound |
| `VIX_DIRECTION_STABLE_HIGH` | 2.0% | Stable range upper bound |
| `VIX_DIRECTION_RISING` | 5.0% | Fear building threshold |
| `VIX_DIRECTION_RISING_FAST` | 10.0% | Panic emerging threshold |
| `VIX_DIRECTION_SPIKING` | 10.0% | Crash mode threshold |

### V2.1.1 VIX Level Thresholds

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `VIX_LEVEL_LOW_MAX` | 20 | VIX < 20: Normal, mean reversion works |
| `VIX_LEVEL_MEDIUM_MAX` | 25 | VIX 20-25: Caution zone |
| (VIX > 25) | — | Elevated, momentum dominates |

### V2.1.1 Micro Regime Score Components

| Component | Score Range | Description |
|-----------|:-----------:|-------------|
| VIX Level | 0-25 | Lower VIX = higher score |
| VIX Direction | -10 to +20 | Falling = higher, Spiking/Whipsaw = penalty |
| QQQ Move | 0-20 | Sweet spot at 0.8-1.25% |
| Move Velocity | 0-15 | Gradual moves score higher |

### V2.1.1 Tiered VIX Monitoring

| Layer | Interval | Purpose |
|-------|:--------:|---------|
| Layer 1 | 5 min | Spike detection (VIX > 5% change) |
| Layer 2 | 15 min | Direction assessment |
| Layer 3 | 60 min | Whipsaw detection (reversal count) |
| Layer 4 | 30 min | Full regime classification |

### V2.1.1 Intraday Strategy Parameters

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `INTRADAY_DEBIT_FADE_MIN_SCORE` | 35 | Minimum micro score for debit fade (V6.8: was 45) |
| `INTRADAY_DEBIT_FADE_VIX_MIN` | 11.5 | Minimum VIX for DEBIT_FADE (V6.8: was 13.5) |
| `INTRADAY_FADE_MIN_MOVE` | 0.50% | Minimum QQQ move for FADE |
| `INTRADAY_FADE_MAX_MOVE` | 1.50% | Max QQQ move for FADE (V6.8: was 1.20%) |
| `INTRADAY_DEBIT_FADE_VIX_MAX` | 25 | Maximum VIX for debit fade |
| `INTRADAY_CREDIT_MIN_VIX` | 18 | Minimum VIX for credit spreads |
| `INTRADAY_ITM_MIN_VIX` | 10.0 | Minimum VIX for ITM momentum (V6.8: was 11.5) |
| `INTRADAY_ITM_MIN_SCORE` | 40 | Minimum micro score for ITM (V6.8: was 50) |
| `INTRADAY_ITM_MIN_MOVE` | 0.8% | Minimum QQQ move for ITM |
| `INTRADAY_FORCE_EXIT_TIME` | 15:30 ET | Intraday positions must close |

### V2.1.1 Swing Mode Simple Filters

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `SWING_TIME_WINDOW_START` | 10:00 ET | Swing entry window start |
| `SWING_TIME_WINDOW_END` | 14:30 ET | Swing entry window end |
| `SWING_GAP_THRESHOLD` | 1.0% | Skip if SPY gaps > 1.0% |
| `SWING_EXTREME_SPY_DROP` | -2.0% | Pause if SPY drops > 2% |
| `SWING_EXTREME_VIX_SPIKE` | 15.0% | Pause if VIX spikes > 15% |

---

## 16.8.2 OCO Manager Parameters (V2.1)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `OCO_STATE_KEY` | "oco_state" | ObjectStore key for persistence |
| `OCO_RECONCILE_ON_START` | True | Verify orders on restart |
| `OCO_CANCEL_TIMEOUT_SEC` | 30 | Timeout for cancel confirmation |

---

## 16.9 Portfolio Router Parameters

### Exposure Group Limits

| Group | Max Net Long | Max Net Short | Max Gross |
|-------|:------------:|:-------------:|:---------:|
| `NASDAQ_BETA` | 50% | 30% | 75% |
| `SPY_BETA` | 40% | 0% | 40% |
| `SMALL_CAP_BETA` | 25% | 0% | 25% |
| `FINANCIALS_BETA` | 15% | 0% | 15% |
| `RATES` | 40% | 0% | 40% |

### Group Membership

| Symbol | Group | Type | Allocation |
|--------|-------|------|:----------:|
| TQQQ | NASDAQ_BETA | 3× Long Nasdaq | 5% (MR) |
| QLD | NASDAQ_BETA | 2× Long Nasdaq | 15% (Trend — V2.18: was 20%) |
| SOXL | NASDAQ_BETA | 3× Long Semi | 5% (MR) |
| PSQ | NASDAQ_BETA | 1× Inverse Nasdaq | (Hedge) |
| SSO | SPY_BETA | 2× Long S&P | 12% (Trend — V2.18: was 15%) |
| TNA | SMALL_CAP_BETA | 3× Long Russell 2000 | 8% (Trend — V2.18: was 12%) |
| FAS | FINANCIALS_BETA | 3× Long Financials | 5% (Trend — V2.18: was 8%) |
| TMF | RATES | 3× Long Treasury | (Hedge) |
| SHV | RATES | Short Treasury | (Yield) |

### Capital Firewall (V2.18)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `CAPITAL_PARTITION_TREND` | 0.50 | 50% of tradeable equity for Trend |
| `CAPITAL_PARTITION_OPTIONS` | 0.50 | 50% of tradeable equity for Options |
| `MAX_MARGIN_WEIGHTED_ALLOCATION` | 0.90 | Block entries if margin > 90% |

### Trade Thresholds

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `MIN_TRADE_VALUE` | $2,000 | Minimum position value |
| `MIN_SHARE_DELTA` | 1 | Minimum shares to trade |

---

## 16.9.1 Startup Gate Parameters (V2.30)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `STARTUP_GATE_ENABLED` | True | Master toggle for startup gate |
| `STARTUP_GATE_WARMUP_DAYS` | 5 | Indicator warmup phase duration |
| `STARTUP_GATE_OBSERVATION_DAYS` | 5 | Observation phase duration (V2.30: was 10) |
| `STARTUP_GATE_REDUCED_DAYS` | 5 | Reduced phase duration (V2.30: was 10) |
| `STARTUP_GATE_REDUCED_SIZE_MULT` | 0.50 | Size multiplier during OBSERVATION/REDUCED phases |

### Phase Progression

| Phase | Duration | Hedges/Yield | Bearish Options | Directional Longs | Size Mult |
|-------|:--------:|:------------:|:---------------:|:-----------------:|:---------:|
| INDICATOR_WARMUP | 5 days | Allowed | Blocked | Blocked | 0% |
| OBSERVATION | 5 days | Allowed | Allowed (50%) | Blocked | 50% |
| REDUCED | 5 days | Allowed | Allowed (50%) | Allowed (50%) | 50% |
| FULLY_ARMED | Permanent | Allowed | Allowed | Allowed | 100% |

> **V2.30 Design:** All-weather time-based system. No regime dependency. 15 days total. Hedges/yield never gated. Bearish options available from OBSERVATION. One-time and permanent. Never resets on kill switch. Separate from ColdStartEngine.
>
> **V2.30 Removed:** `STARTUP_GATE_REGIME_DAYS`, `STARTUP_GATE_REGIME_MIN_SCORE`, `STARTUP_GATE_REDUCED_MAX_WEIGHT`

---

## 16.10 Risk Engine Parameters

### Kill Switch

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `KILL_SWITCH_PCT` | 0.05 | 5% daily loss threshold (V2.3.17: raised from 3%) |
| `KILL_SWITCH_PREEMPTIVE_PCT` | 0.045 | 4.5% preemptive warning threshold |

### Margin Call Circuit Breaker (V2.4.4)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `MARGIN_CALL_MAX_CONSECUTIVE` | 5 | Max consecutive margin calls before cooldown |
| Cooldown | 4 hours | Cooldown period after margin call streak |

### Panic Mode

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `PANIC_MODE_PCT` | 0.04 | 4% SPY intraday drop |

### Weekly Breaker

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `WEEKLY_BREAKER_PCT` | 0.05 | 5% week-to-date loss |
| `WEEKLY_SIZE_REDUCTION` | 0.50 | 50% sizing reduction |

### Gap Filter

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `GAP_FILTER_PCT` | 0.015 | 1.5% gap down threshold |

### Vol Shock

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `VOL_SHOCK_ATR_MULT` | 3.0 | ATR multiplier for trigger |
| `VOL_SHOCK_PAUSE_MIN` | 15 | Minutes to pause |

### Time Guard

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `TIME_GUARD_START` | 13:55 ET | Start of blocked window |
| `TIME_GUARD_END` | 14:10 ET | End of blocked window |

### Drawdown Governor (V2.26)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `DRAWDOWN_GOVERNOR_LEVELS` | [(0.03, 0.75), (0.06, 0.50), (0.10, 0.25), (0.15, 0.0)] | DD% → scale pairs |

### Dynamic Governor Recovery (V2.29)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `DRAWDOWN_GOVERNOR_RECOVERY_BASE` | 0.08 | Base recovery threshold (scaled by governor level) |

**Formula:** `effective_recovery = DRAWDOWN_GOVERNOR_RECOVERY_BASE × governor_scale`

| Governor Scale | Effective Recovery |
|:--------------:|:------------------:|
| 100% | 8% |
| 75% | 6% |
| 50% | 4% |
| 25% | 2% |

> **V2.29 change:** Replaced flat `DRAWDOWN_GOVERNOR_RECOVERY_PCT = 0.12` with dynamic formula. Prevents "recovery trap" at reduced allocations.

### Kill Switch Tiers (V2.28.1)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `KS_TIER_1_PCT` | 0.02 | 2% — Reduce sizing 50% |
| `KS_TIER_2_PCT` | 0.04 | 4% — Block all new entries |
| `KS_TIER_3_PCT` | 0.06 | 6% — Full liquidation + cold start reset |

---

## 16.11 Execution Engine Parameters

### Timing

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `MOO_SUBMISSION_TIME` | 15:45 ET | When to submit MOO orders |
| `MOO_FALLBACK_CHECK` | 09:31 ET | When to verify MOO fills |
| `MARKET_ORDER_TIMEOUT` | 60 sec | Warning threshold |
| `CONNECTION_TIMEOUT` | 5 min | Halt new orders threshold |

---

## 16.12 Scheduling Parameters

### Scheduled Events

| Event | Time (ET) | Description |
|-------|-----------|-------------|
| `PRE_MARKET_SETUP` | 09:25 | Set baselines, load state |
| `SOD_BASELINE` | 09:33 | Set equity_sod, check gap |
| `WARM_ENTRY_CHECK` | 10:00 | Cold start warm entry |
| `TIME_GUARD_START` | 13:55 | Block entries |
| `TIME_GUARD_END` | 14:10 | Resume entries |
| `MR_FORCE_CLOSE` | 15:45 | Close MR positions |
| `EOD_PROCESSING` | 15:45 | Run all EOD logic |
| `WEEKLY_RESET` | Mon 09:30 | Reset weekly breaker |

---

## 16.13 Indicator Warmup Parameters

| Indicator | Warmup Days | Description |
|-----------|:-----------:|-------------|
| SMA(20) | 20 | Fast moving average |
| SMA(50) | 50 | Medium moving average |
| SMA(200) | 200 | Slow moving average (MA200 for Trend Engine) |
| ADX(14) | 14 | Average Directional Index (Trend Engine entry) |
| RSI(5) | 10 | Fast RSI (Mean Reversion) |
| ATR(14) | 14 | Average True Range (Chandelier stops) |
| Realized Vol | 252 | For percentile ranking (Regime Engine) |

**Minimum warmup period: 252 trading days** to ensure all indicators are fully populated.

---

## 16.14 Symbol Configuration

### Traded Symbols

| Symbol | Description | Strategy | Allocation |
|--------|-------------|----------|:----------:|
| QLD | 2× Nasdaq | Trend, Warm Entry | 15% (V2.18: was 20%) |
| SSO | 2× S&P 500 | Trend, Warm Entry | 12% (V2.18: was 15%) |
| TNA | 3× Russell 2000 | Trend | 8% (V2.18: was 12%) |
| FAS | 3× Financials | Trend | 5% (V2.18: was 8%) |
| TQQQ | 3× Nasdaq | Mean Reversion | 5% |
| SOXL | 3× Semiconductor | Mean Reversion | 5% |
| QQQ Options | Options chain | Options (VASS) | 25% (V2.3: was 20%) |
| TMF | 3× Treasury | Hedge | 0-20% |
| PSQ | 1× Inverse Nasdaq | Hedge | 0-10% |
| SHV | Short Treasury | Yield | Remainder |

### Symbol Liquidity Requirements

| Symbol | AUM | Daily Volume | Max Bid-Ask Spread |
|--------|:---:|:------------:|:------------------:|
| QLD | $4.2B | High | 0.03% |
| SSO | $3.8B | High | 0.03% |
| TNA | $2.16B | $472M | 0.05% |
| FAS | $2.55B | ~374K shares | 0.05% |
| TQQQ | $19B | Very High | 0.02% |
| SOXL | $8B | Very High | 0.03% |

### Proxy Symbols (Data Only)

| Symbol | Description | Used For |
|--------|-------------|----------|
| SPY | S&P 500 ETF | Regime trend, panic mode, gap filter, vol shock |
| RSP | Equal-Weight S&P | Regime breadth |
| HYG | High-Yield Corporate | Regime credit |
| IEF | 7-10 Year Treasury | Regime credit |

---

## 16.15 config.py Template

```python
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
MAX_SINGLE_POSITION_PCT = {
    "SEED": 0.50,
    "GROWTH": 0.40
}

TARGET_VOLATILITY = 0.20
KILL_SWITCH_PCT_BY_PHASE = {
    "SEED": 0.03,
    "GROWTH": 0.03
}

# Lockbox
LOCKBOX_MILESTONES = [100_000, 200_000]
LOCKBOX_LOCK_PCT = 0.10

# =============================================================================
# REGIME ENGINE
# =============================================================================

# Factor Weights (V2.3: Added VIX, rebalanced)
WEIGHT_TREND = 0.30      # V2.3: Reduced from 0.45
WEIGHT_VIX = 0.20        # V2.3 NEW: Implied volatility
WEIGHT_VOLATILITY = 0.15 # V2.3: Reduced from 0.25 (realized vol)
WEIGHT_BREADTH = 0.20    # V2.3: Increased from 0.15
WEIGHT_CREDIT = 0.15

# VIX Level Factor Thresholds (V2.3 NEW)
VIX_LOW_THRESHOLD = 15       # Below = complacent
VIX_NORMAL_THRESHOLD = 22    # Normal volatility
VIX_HIGH_THRESHOLD = 30      # Elevated fear
VIX_EXTREME_THRESHOLD = 40   # Crisis mode

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

COLD_START_DAYS = 5
WARM_ENTRY_SIZE_MULT = 0.50
WARM_ENTRY_TIME = "10:00"
WARM_REGIME_MIN = 50
WARM_QLD_THRESHOLD = 60
WARM_MIN_SIZE = 2_000

# =============================================================================
# TREND ENGINE (V2 - MA200 + ADX)
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

# =============================================================================
# MEAN REVERSION ENGINE (V2.1 - VIX Filter)
# =============================================================================

# RSI
RSI_PERIOD = 5
# RSI_THRESHOLD is VIX-adjusted (see VIX_MR_PARAMS below)

# Entry Conditions
MR_DROP_THRESHOLD = 0.025
MR_VOLUME_MULT = 1.2
MR_WINDOW_START = "10:00"
MR_WINDOW_END = "15:00"
MR_REGIME_MIN = 40

# Exit Conditions
MR_TARGET_PCT = 0.02
# MR_STOP_PCT is VIX-adjusted (see VIX_MR_PARAMS below)
MR_FORCE_EXIT_TIME = "15:45"

# VIX Regime Filter (V2.1)
VIX_NORMAL_THRESHOLD = 20
VIX_CAUTION_THRESHOLD = 30
VIX_HIGH_RISK_THRESHOLD = 40

# VIX-Adjusted MR Parameters
VIX_MR_PARAMS = {
    "NORMAL":    {"allocation": 0.10, "rsi_threshold": 30, "stop_pct": 0.08},  # VIX < 20
    "CAUTION":   {"allocation": 0.05, "rsi_threshold": 25, "stop_pct": 0.06},  # VIX 20-30
    "HIGH_RISK": {"allocation": 0.02, "rsi_threshold": 20, "stop_pct": 0.04},  # VIX 30-40
    "CRASH":     {"allocation": 0.00, "rsi_threshold": 0,  "stop_pct": 0.00},  # VIX > 40 (disabled)
}

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
# OPTIONS ENGINE (V2.24.2)
# =============================================================================

# Allocation (V2.3/V2.18)
OPTIONS_TOTAL_ALLOCATION = 0.25  # 25% total (V2.3: was 20%)
OPTIONS_SWING_ALLOCATION = 0.1875  # 18.75% (75% of 25%)
OPTIONS_INTRADAY_ALLOCATION = 0.0625  # 6.25% (25% of 25%)
OPTIONS_ALLOCATION_MIN = 0.20  # Floor
OPTIONS_ALLOCATION_MAX = 0.30  # Ceiling

# 4-Factor Entry Scoring
OPTIONS_ADX_PERIOD = 14
OPTIONS_MA_PERIOD = 200
OPTIONS_IV_LOOKBACK = 252
OPTIONS_MAX_SPREAD_PCT = 0.10
OPTIONS_ENTRY_SCORE_MIN = 3.0

# Tiered Stop Losses
OPTIONS_STOP_TIER_1 = 0.20  # Score 3.0-3.25
OPTIONS_STOP_TIER_2 = 0.22  # Score 3.25-3.5
OPTIONS_STOP_TIER_3 = 0.25  # Score 3.5-3.75
OPTIONS_STOP_TIER_4 = 0.30  # Score 3.75-4.0
OPTIONS_PROFIT_TARGET_PCT = 0.50

# Time Constraints
OPTIONS_ENTRY_START = "10:00"
OPTIONS_ENTRY_END = "14:30"
OPTIONS_LATE_DAY_TIME = "14:30"
OPTIONS_FORCE_EXIT_HOUR = 15    # 3 PM ET
OPTIONS_FORCE_EXIT_MINUTE = 45  # 3:45 PM ET

# Greeks Monitoring (Circuit Breaker Level 5)
# Note: These use CB_ prefix as they're part of the circuit breaker system
# See Risk Engine section (12-risk-engine.md) for integration
CB_DELTA_MAX = 0.80  # Max delta exposure per position
CB_GAMMA_WARNING = 0.05  # Gamma warning threshold near expiry
CB_VEGA_MAX = 0.50  # Max vega exposure
CB_THETA_WARNING = -0.02  # Daily theta decay warning (-2%)

# Contract Selection (V2.23 Universe)
OPTIONS_DTE_MIN = 0
OPTIONS_DTE_MAX = 60  # V2.23: was 4
SPREAD_DTE_MIN = 14  # V2.24.2: swing spread minimum
SPREAD_DTE_MAX = 45  # Swing spread maximum
OPTIONS_DELTA_MIN = 0.40
OPTIONS_DELTA_MAX = 0.60
OPTIONS_MIN_PREMIUM = 0.50

# VASS (V2.8)
VASS_IV_LOW_THRESHOLD = 15
VASS_IV_HIGH_THRESHOLD = 25
VASS_IV_SMOOTHING_MINUTES = 30

# Credit Spreads (V2.8/V2.10)
CREDIT_SPREAD_MIN_CREDIT = 0.30
CREDIT_SPREAD_WIDTH_TARGET = 5.0
CREDIT_SPREAD_SHORT_LEG_DELTA_MIN = 0.25
CREDIT_SPREAD_SHORT_LEG_DELTA_MAX = 0.40
CREDIT_SPREAD_PROFIT_TARGET = 0.50
CREDIT_SPREAD_STOP_MULTIPLIER = 2.0

# Elastic Delta Bands (V2.24.1)
ELASTIC_DELTA_STEPS = [0.0, 0.03, 0.07, 0.12]
ELASTIC_DELTA_FLOOR = 0.10
ELASTIC_DELTA_CEILING = 0.95

# Sizing Caps (V2.18)
SWING_SPREAD_MAX_DOLLARS = 7500
INTRADAY_SPREAD_MAX_DOLLARS = 4000

# ATR-Based Stops (V6.8)
OPTIONS_ATR_STOP_MULTIPLIER = 1.0  # V6.8: was 1.5
OPTIONS_ATR_STOP_MIN_PCT = 0.15    # V6.8: was 0.20
OPTIONS_ATR_STOP_MAX_PCT = 0.30    # V6.8: was 0.50
OPTIONS_USE_ATR_STOPS = True

# UVXY Conviction Thresholds (V6.8)
MICRO_UVXY_BEARISH_THRESHOLD = 0.025  # V6.8: was 0.03
MICRO_UVXY_BULLISH_THRESHOLD = -0.025  # V6.8: was -0.03
MICRO_VIX_CRISIS_LEVEL = 35
MICRO_VIX_COMPLACENT_LEVEL = 12

# Assignment Margin Buffer (V6.8)
ASSIGNMENT_MARGIN_BUFFER_ENABLED = True
ASSIGNMENT_MARGIN_BUFFER_PCT = 0.10  # V6.8: was 0.20

# Limit Orders (V2.19)
OPTIONS_USE_LIMIT_ORDERS = True
OPTIONS_LIMIT_SLIPPAGE_PCT = 0.05
OPTIONS_MAX_SPREAD_PCT = 0.20

# =============================================================================
# OCO MANAGER (V2.1)
# =============================================================================

OCO_STATE_KEY = "oco_state"
OCO_RECONCILE_ON_START = True
OCO_CANCEL_TIMEOUT_SEC = 30

# =============================================================================
# PORTFOLIO ROUTER
# =============================================================================

# Exposure Limits
EXPOSURE_LIMITS = {
    "NASDAQ_BETA": {"max_net_long": 0.50, "max_net_short": 0.30, "max_gross": 0.75},
    "SPY_BETA": {"max_net_long": 0.40, "max_net_short": 0.00, "max_gross": 0.40},
    "SMALL_CAP_BETA": {"max_net_long": 0.25, "max_net_short": 0.00, "max_gross": 0.25},
    "FINANCIALS_BETA": {"max_net_long": 0.15, "max_net_short": 0.00, "max_gross": 0.15},
    "RATES": {"max_net_long": 0.99, "max_net_short": 0.00, "max_gross": 0.99}  # V2.3.17: raised from 40% for SHV flexibility
}

# Group Membership
SYMBOL_GROUPS = {
    "TQQQ": "NASDAQ_BETA",
    "QLD": "NASDAQ_BETA",
    "SOXL": "NASDAQ_BETA",
    "PSQ": "NASDAQ_BETA",  # Inverse
    "SSO": "SPY_BETA",
    "TNA": "SMALL_CAP_BETA",
    "FAS": "FINANCIALS_BETA",
    "TMF": "RATES",
    "SHV": "RATES"
}

# Trend Engine Allocations (V2.18: Reduced for Capital Firewall)
TREND_SYMBOL_ALLOCATIONS = {
    "QLD": 0.15,  # 15% - 2× Nasdaq (V2.18: was 20%)
    "SSO": 0.12,  # 12% - 2× S&P 500 (V2.18: was 15%)
    "TNA": 0.08,  # 8% - 3× Russell 2000 (V2.18: was 12%)
    "FAS": 0.05,  # 5% - 3× Financials (V2.18: was 8%)
}
TREND_TOTAL_ALLOCATION = 0.40  # 40% total (V2.18: was 55%)

# Mean Reversion Allocations
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

# Kill Switch
KILL_SWITCH_PCT = 0.05  # V2.3.17: raised from 0.03

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

# Drawdown Governor (V2.26)
DRAWDOWN_GOVERNOR_LEVELS = [(0.03, 0.75), (0.06, 0.50), (0.10, 0.25), (0.15, 0.0)]

# V2.29 P1: Dynamic recovery — scales with governor level
# Effective = base × current_scale: 100%→8%, 75%→6%, 50%→4%, 25%→2%
DRAWDOWN_GOVERNOR_RECOVERY_BASE = 0.08

# Kill Switch Tiers (V2.28.1)
KS_TIER_1_PCT = 0.02   # 2% — Reduce sizing 50%
KS_TIER_2_PCT = 0.04   # 4% — Block all new entries
KS_TIER_3_PCT = 0.06   # 6% — Full liquidation + cold start reset

# V2.30: All-Weather Startup Gate (time-based, no regime dependency, never resets on kill switch)
STARTUP_GATE_ENABLED = True
STARTUP_GATE_WARMUP_DAYS = 5            # 5 days indicator warmup (hedges + yield only)
STARTUP_GATE_OBSERVATION_DAYS = 5       # 5 days observation (+ bearish options at 50%)
STARTUP_GATE_REDUCED_DAYS = 5           # 5 days reduced (all engines at 50%)
STARTUP_GATE_REDUCED_SIZE_MULT = 0.50   # 50% sizing during OBSERVATION/REDUCED phases

# =============================================================================
# V2.1 CIRCUIT BREAKER SYSTEM (5 Levels)
# =============================================================================
# These are graduated responses BEFORE the nuclear kill switch

# Level 1: Daily Loss Circuit Breaker
# At -2% daily loss, reduce sizing but don't liquidate
CB_DAILY_LOSS_THRESHOLD = 0.02  # -2% daily loss
CB_DAILY_SIZE_REDUCTION = 0.50  # Reduce to 50% sizing

# Level 2: Weekly Loss Circuit Breaker (same as WEEKLY_BREAKER_PCT above)

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
    "EOD_PROCESSING": "15:45"
}

# =============================================================================
# INDICATORS
# =============================================================================

INDICATOR_WARMUP_DAYS = 252  # Max of all indicator requirements

# =============================================================================
# SYMBOLS
# =============================================================================

TRADED_SYMBOLS = ["TQQQ", "SOXL", "QLD", "SSO", "TNA", "FAS", "TMF", "PSQ", "SHV"]
PROXY_SYMBOLS = ["SPY", "RSP", "HYG", "IEF"]
ALL_SYMBOLS = TRADED_SYMBOLS + PROXY_SYMBOLS
```

---

## 16.16 Parameter Validation

### On Startup

Validate all parameters are within acceptable ranges:

```python
def validate_config():
    """Validate configuration parameters."""

    errors = []

    # Factor weights must sum to 1.0 (V2.3: includes VIX)
    weight_sum = WEIGHT_TREND + WEIGHT_VIX + WEIGHT_VOLATILITY + WEIGHT_BREADTH + WEIGHT_CREDIT
    if abs(weight_sum - 1.0) > 0.001:
        errors.append(f"Factor weights sum to {weight_sum}, must equal 1.0")
    
    # Regime thresholds must be in order
    if not (REGIME_RISK_ON > REGIME_NEUTRAL > REGIME_CAUTIOUS > REGIME_DEFENSIVE):
        errors.append("Regime thresholds must be in descending order")
    
    # Kill switch must be positive
    if KILL_SWITCH_PCT <= 0:
        errors.append("Kill switch percentage must be positive")
    
    # ... additional validations
    
    if errors:
        raise ValueError(f"Configuration errors: {errors}")
```

---

## 16.17 Key Design Decisions Summary

| Decision | Rationale |
|----------|-----------|
| **Single config.py file** | All parameters in one place for easy modification |
| **Named constants** | Self-documenting code |
| **Phase-dependent limits** | Different risk profiles at different account sizes |
| **Conservative defaults** | Err on side of safety |
| **Validation on startup** | Catch configuration errors early |

---

*Next Section: [17 - Appendix: Glossary](17-appendix-glossary.md)*

*Previous Section: [15 - State Persistence](15-state-persistence.md)*
