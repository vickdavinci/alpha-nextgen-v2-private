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

### V5.3 Four-Factor Model (Current)

The V5.3 regime model uses four factors optimized for crash detection while maintaining responsiveness to recoveries. See `docs/system/04-regime-engine.md` for full documentation.

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `V53_REGIME_ENABLED` | True | Enable V5.3 4-factor model |
| `WEIGHT_MOMENTUM_V53` | 0.30 | Momentum factor weight (30%) - 20-day ROC |
| `WEIGHT_VIX_COMBINED_V53` | 0.35 | V6.15: VIX Combined weight (35%) - was 30%, increased for fear sensitivity |
| `WEIGHT_TREND_V53` | 0.20 | V6.15: Trend factor weight (20%) - was 25%, reduced lagging trend dominance |
| `WEIGHT_DRAWDOWN_V53` | 0.15 | Drawdown factor weight (15%) - distance from 52-week high |

### VIX Combined Scoring (V5.3)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `VIX_COMBINED_LEVEL_WEIGHT` | 0.60 | VIX level contribution (60%) |
| `VIX_COMBINED_DIRECTION_WEIGHT` | 0.40 | VIX 5-day direction contribution (40%) |
| `VIX_COMBINED_HIGH_VIX_THRESHOLD` | 25.0 | VIX level that triggers clamp |
| `VIX_COMBINED_HIGH_VIX_CLAMP` | 47 | Max VIX Combined score when VIX >= 25 |

### V5.3 Guards (Safety Mechanisms)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `V53_SPIKE_CAP_ENABLED` | True | Enable spike cap |
| `V53_SPIKE_CAP_THRESHOLD` | 0.28 | VIX 5-day change >= +28% triggers cap |
| `V53_SPIKE_CAP_MAX_SCORE` | 38 | Score cap during spike (V6.6: lowered from 45) |
| `V53_SPIKE_CAP_DECAY_DAYS` | 3 | Days until cap expires |
| `V53_BREADTH_DECAY_ENABLED` | True | Enable breadth decay penalty |
| `V53_BREADTH_5D_DECAY_THRESHOLD` | -0.01 | 5-day decay trigger (V6.9: was -0.02) |
| `V53_BREADTH_10D_DECAY_THRESHOLD` | -0.03 | 10-day decay trigger (V6.9: was -0.04) |
| `V53_BREADTH_5D_PENALTY` | 8 | Points deducted for 5-day decay |
| `V53_BREADTH_10D_PENALTY` | 12 | Points deducted for 10-day decay (additive) |

### Momentum Factor Scoring (V5.3)

| ROC Range | Score | Description |
|:---------:|:-----:|-------------|
| > +5% | 90 | Strong bull momentum |
| +2% to +5% | 75 | Bull momentum |
| +1% to +2% | 60 | Mildly bullish |
| -1% to +1% | 50 | Neutral |
| -2% to -1% | 40 | Mildly bearish |
| -5% to -2% | 25 | Bear momentum |
| < -5% | 10 | Strong bear momentum |

### Drawdown Factor Scoring (V5.3)

| Drawdown | Score | Description |
|:--------:|:-----:|-------------|
| < 5% | 90 | Bull market (near highs) |
| 5-10% | 70 | Correction |
| 10-15% | 50 | Pullback |
| 15-20% | 30 | Bear market |
| > 20% | 10 | Deep bear |

### Smoothing

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `REGIME_SMOOTHING_ALPHA` | 0.30 | Exponential smoothing factor |

### Regime Score Thresholds (V6.15)

| Parameter | Value | State |
|-----------|:-----:|-------|
| `REGIME_RISK_ON` | 70 | RISK_ON (70-100) |
| `REGIME_NEUTRAL` | 50 | NEUTRAL (50-69) |
| `REGIME_CAUTIOUS` | 45 | V6.15: CAUTIOUS (45-49) - was 40 |
| `REGIME_DEFENSIVE` | 35 | V6.15: DEFENSIVE (35-44) - was 30 |
| `REGIME_RISK_OFF` | 0 | RISK_OFF (0-34) |

### VIX Level Thresholds (Shared)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `VIX_LOW_THRESHOLD` | 15 | Below = complacent market, cheap options |
| `VIX_NORMAL_THRESHOLD` | 22 | 15-22 = normal volatility |
| `VIX_HIGH_THRESHOLD` | 30 | 22-30 = elevated fear |
| `VIX_EXTREME_THRESHOLD` | 40 | Above = crisis mode |

### Legacy Factor Parameters (V3.0/V4.0)

These parameters are used when `V53_REGIME_ENABLED = False`:

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `V4_REGIME_ENABLED` | False | V4.0 5-factor model (deprecated) |
| `V3_REGIME_SIMPLIFIED_ENABLED` | True | V3.3 3-factor model (fallback) |
| `WEIGHT_TREND_V3` | 0.35 | V3.3 trend weight |
| `WEIGHT_VIX_V3` | 0.30 | V3.3 VIX level weight |
| `WEIGHT_DRAWDOWN_V3` | 0.35 | V3.3 drawdown weight |

### Trend Factor Parameters

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `SMA_FAST` | 20 | Fast moving average period |
| `SMA_MED` | 50 | Medium moving average period |
| `SMA_SLOW` | 200 | Slow moving average period |
| `EXTENDED_THRESHOLD` | 1.05 | 5% above SMA200 = extended |
| `OVERSOLD_THRESHOLD` | 0.95 | 5% below SMA200 = oversold |

### Breadth & Credit Factor Parameters

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `BREADTH_LOOKBACK` | 20 | RSP vs SPY comparison period |
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

## 16.7 Hedge Engine Parameters (V6.11)

### Regime Thresholds

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `HEDGE_LEVEL_1` | 50 | V3.0: Light hedge starts at Cautious (regime < 50) |
| `HEDGE_LEVEL_2` | 40 | V3.0: Medium hedge at Defensive (regime < 40) |
| `HEDGE_LEVEL_3` | 30 | V3.0: Full hedge at Bear (regime < 30) |

### SH Allocation (V6.11)

V6.11 replaced TMF/PSQ with SH (1x Inverse S&P). SH has no decay (unlike VIXY), provides direct equity hedge.

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `HEDGE_SYMBOLS` | ["SH"] | V6.11: Single hedge symbol |
| `SH_LIGHT` | 0.05 | Regime 40-50: 5% inverse |
| `SH_MEDIUM` | 0.08 | Regime 30-40: 8% inverse |
| `SH_FULL` | 0.10 | Regime < 30: 10% inverse |

### Legacy TMF/PSQ (Deprecated in V6.11)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `TMF_LIGHT` | 0.00 | Deprecated |
| `TMF_MEDIUM` | 0.00 | Deprecated |
| `TMF_FULL` | 0.00 | Deprecated |
| `PSQ_MEDIUM` | 0.00 | Deprecated |
| `PSQ_FULL` | 0.00 | Deprecated |

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
| Force Exit | 15:25 | V6.15: Close all intraday positions (was 15:30) |

> **V2.3.6 Rationale:** The 10:00-10:30 window has highest gamma opportunities for momentum strategies. Removed hardcoded gatekeeper that was blocking this window.
> **V6.15 Change:** Force exit moved from 15:30 to 15:25 to prevent OCO race conditions.

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
| `OPTIONS_PROFIT_TARGET_PCT` | 0.60 | V6.15: +60% profit target (was 50%) |

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

### VASS (VIX-Adaptive Strategy Selection) — V2.8/V6.13

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `VASS_IV_LOW_THRESHOLD` | 16 | V6.6: Below = Low IV environment (was 15) |
| `VASS_IV_HIGH_THRESHOLD` | 25 | Above = High IV environment |
| `VASS_IV_SMOOTHING_MINUTES` | 30 | VIX smoothing window (minutes) |

| IV Environment | VIX Range | Strategy | DTE Range |
|----------------|-----------|----------|-----------|
| Low IV | < 16 | Debit Spreads | 30-45 (Monthly) |
| Medium IV | 16-25 | Debit Spreads | 7-30 (V6.12: widened from 7-21) |
| High IV | > 25 | Credit Spreads | 5-40 (V6.13.1: expanded from 5-28) |

### Credit Spread Parameters (V2.8/V2.10/V6.14)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `CREDIT_SPREAD_MIN_CREDIT` | 0.20 | Minimum credit (V6.10: was $0.30, lowered for fills) |
| `CREDIT_SPREAD_MIN_CREDIT_HIGH_IV` | 0.10 | V6.13.1: Min credit when VIX > 30 (was 0.20) |
| `CREDIT_SPREAD_HIGH_IV_VIX_THRESHOLD` | 30.0 | VIX level above which reduced floor applies |
| `CREDIT_SPREAD_WIDTH_TARGET` | 5.0 | Target spread width ($5) |
| `CREDIT_SPREAD_SHORT_LEG_DELTA_MIN` | 0.25 | Short leg minimum delta |
| `CREDIT_SPREAD_SHORT_LEG_DELTA_MAX` | 0.45 | V6.13: Improve constructability (was 0.40) |
| `CREDIT_SPREAD_MIN_OPEN_INTEREST` | 35 | V6.14: Liquidity gate (new) |
| `CREDIT_SPREAD_MAX_SPREAD_PCT` | 0.40 | V6.14: Bid-ask spread tolerance (new) |
| `CREDIT_SPREAD_LONG_LEG_MAX_SPREAD_PCT` | 0.55 | V6.14: Long leg spread tolerance (new) |
| `CREDIT_SPREAD_PROFIT_TARGET` | 0.50 | 50% of max profit |
| `CREDIT_SPREAD_STOP_MULTIPLIER` | 2.0 | Stop = spread doubles |
| `CREDIT_SPREAD_FALLBACK_TO_DEBIT` | True | V6.10: Fall back to debit when credit fails |

### V6.10 Spread Delta Requirements

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `SPREAD_LONG_LEG_DELTA_MIN` | 0.35 | V6.10: CALL long min (was 0.40) |
| `SPREAD_LONG_LEG_DELTA_MAX` | 0.90 | V6.10: Allow deeper ITM (was 0.85) |
| `SPREAD_SHORT_LEG_DELTA_MIN` | 0.08 | V6.10: Allow farther OTM (was 0.10) |
| `SPREAD_SHORT_LEG_DELTA_MAX` | 0.60 | V6.10: Allow closer to ATM (was 0.55) |
| `SPREAD_LONG_LEG_DELTA_MIN_PUT` | 0.25 | V6.10: PUT long min (was 0.30) |
| `SPREAD_LONG_LEG_DELTA_MAX_PUT` | 0.90 | V6.10: Allow deeper ITM long PUTs |
| `SPREAD_SHORT_LEG_DELTA_MIN_PUT` | 0.05 | V6.10: Allow farther OTM (was 0.08) |
| `SPREAD_SHORT_LEG_DELTA_MAX_PUT` | 0.60 | V6.10: Allow closer to ATM short PUTs |

### Elastic Delta Bands (V2.24.1/V6.13.1)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `ELASTIC_DELTA_STEPS` | [0.0, 0.05, 0.10, 0.15] | V6.13.1: More aggressive widening (was [0.0, 0.03, 0.07, 0.12]) |
| `ELASTIC_DELTA_FLOOR` | 0.10 | Absolute minimum delta |
| `ELASTIC_DELTA_CEILING` | 0.95 | Absolute maximum delta |

### Width-Based Short Leg Selection (V2.4.3/V6.13)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `SPREAD_SHORT_LEG_BY_WIDTH` | True | Use strike width instead of delta |
| `SPREAD_WIDTH_MIN` | 4.0 | V6.13: Min spread width (optimized for fills, was $5) |
| `SPREAD_WIDTH_MAX` | 10.0 | Maximum spread width ($10) |
| `SPREAD_WIDTH_TARGET` | 4.0 | V6.13: Target spread width (optimized for fills, was $5) |

### Sizing Caps (V2.18)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `SWING_SPREAD_MAX_DOLLARS` | 7,500 | Max $ per swing spread |
| `INTRADAY_SPREAD_MAX_DOLLARS` | 4,000 | Max $ per intraday spread |

### ATR-Based Stops (V6.13)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `OPTIONS_ATR_STOP_MULTIPLIER` | 0.9 | V6.13: ATR multiplier for stops (was 1.0) |
| `OPTIONS_ATR_STOP_MIN_PCT` | 0.12 | V6.13: Minimum stop % (was 0.15) |
| `OPTIONS_ATR_STOP_MAX_PCT` | 0.30 | Maximum stop % (V6.8: was 0.50 - prevents 50% losses) |
| `OPTIONS_USE_ATR_STOPS` | True | Use ATR-based stops (vs legacy tier-based) |

### UVXY Conviction Thresholds (V6.14)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `MICRO_UVXY_BEARISH_THRESHOLD` | +2.8% | V6.14: UVXY up → PUT conviction (was +2.5%) |
| `MICRO_UVXY_BULLISH_THRESHOLD` | -4.5% | V6.14: UVXY down → CALL conviction (was -3.0%) |
| `MICRO_UVXY_CONVICTION_EXTREME` | 3.5% | V6.12: NEUTRAL VETO threshold (was 5%) |
| `MICRO_VIX_CRISIS_LEVEL` | 35 | VIX > 35 → CRISIS (BEARISH conviction) |
| `MICRO_VIX_COMPLACENT_LEVEL` | 12 | VIX < 12 → COMPLACENT (BULLISH conviction) |
| `MICRO_SCORE_BULLISH_CONFIRM` | 47 | V6.14: Bullish confirmation threshold (was 52) |
| `MICRO_SCORE_BEARISH_CONFIRM` | 49 | V6.14: Bearish confirmation threshold (was 48) |

### Assignment Risk Management (V6.10)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `ASSIGNMENT_MARGIN_BUFFER_ENABLED` | True | Require extra margin for ITM shorts |
| `ASSIGNMENT_MARGIN_BUFFER_PCT` | 0.20 | Margin buffer % (V6.9: restored to 0.20 for safety) |
| `ASSIGNMENT_MARGIN_AUTO_REDUCE` | True | Auto-reduce if buffer insufficient |
| `SPREAD_FORCE_CLOSE_DTE` | 1 | V6.10: Mandatory close at DTE=1 (assignment prevention) |
| `SPREAD_FORCE_CLOSE_ENABLED` | True | V6.10: Master switch for DTE=1 force close |
| `PREMARKET_ITM_CHECK_ENABLED` | True | V6.10: Check short legs at 09:25 pre-market |
| `SHORT_LEG_ITM_EXIT_THRESHOLD` | 0.005 | V6.10: Exit when 0.5% ITM (was 1%) |

### Limit Order Execution (V2.19)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `OPTIONS_USE_LIMIT_ORDERS` | True | Use marketable limit orders |
| `OPTIONS_LIMIT_SLIPPAGE_PCT` | 0.05 | 5% slippage tolerance |
| `OPTIONS_MAX_SPREAD_PCT` | 0.20 | Max bid-ask spread % (block if wider) |

### V6.10 Margin Pre-Check

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `MARGIN_CHECK_BEFORE_SIGNAL` | True | V6.10: Check margin before generating signal |
| `MARGIN_PRE_CHECK_BUFFER` | 0.15 | V6.10: 15% buffer (was 2.0 — too restrictive) |
| `OPTIONS_MAX_MARGIN_PCT` | 0.25 | V6.10: Max margin 25% (was 30%) |
| `MAX_MARGIN_UTILIZATION` | 0.60 | V6.9: Cap total margin utilization (was 0.90) |
| `MARGIN_UTILIZATION_WARNING` | 0.60 | Log warning when utilization exceeds 60% |
| `MARGIN_UTILIZATION_ENABLED` | True | Enable margin utilization gate |

> **V6.10 Rationale:** Pre-check margin availability before approving any spread signal. Prevents signals from being generated only to fail at execution.

### Safety Rules (V2.4.1)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `SWING_FALLBACK_ENABLED` | False | No naked single-leg fallback |
| Friday Firewall | Active | Close swing options before weekend |
| VIX > 25 | Active | Close ALL swing options in high VIX |

### Neutrality Exit (V2.22/V6.13)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `SPREAD_NEUTRALITY_EXIT_ENABLED` | True | V6.13: Re-enabled for choppy capital recycling |
| `SPREAD_NEUTRALITY_EXIT_PNL_BAND` | 0.06 | V6.13: Tight "flat" band (was 0.10) |
| `SPREAD_NEUTRALITY_ZONE_LOW` | 48 | V6.13: Narrower neutrality zone (was 45) |
| `SPREAD_NEUTRALITY_ZONE_HIGH` | 62 | V6.13: Narrower neutrality zone (was 60) |

### V6.10 Symmetric Stop/Target

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `SPREAD_PROFIT_TARGET_PCT` | 0.40 | 40% profit target (V6.10: was 50%) |
| `SPREAD_STOP_LOSS_PCT` | 0.40 | 40% stop loss (V6.10: was 35%) |

> **V6.10 Rationale:** Symmetric R:R (40%/40%) requires 1:1 win ratio to break even. Previous asymmetric (50%/35%) required 1.43:1 win ratio which was not achieved.

### V6.10 Choppy Market Filter

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `CHOPPY_MARKET_FILTER_ENABLED` | True | Enable choppy market detection |
| `CHOPPY_REVERSAL_COUNT` | 3 | 3+ reversals = choppy market |
| `CHOPPY_LOOKBACK_HOURS` | 2 | Look back 2 hours for reversals |
| `CHOPPY_SIZE_REDUCTION` | 0.50 | 50% size reduction in choppy markets |
| `CHOPPY_MIN_MOVE_PCT` | 0.003 | Min 0.3% move to count as reversal |

> **V6.10 Rationale:** 2015 backtest showed 48% win rate but negative P&L due to choppy market conditions triggering stops. Filter reduces position size when 3+ reversals detected.

### V2.1.1 VIX Direction Thresholds (Micro Regime Engine)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `VIX_DIRECTION_FALLING_FAST` | -3.0% | Strong recovery threshold (V6.6: was -5.0%) |
| `VIX_DIRECTION_FALLING` | -1.0% | Recovery threshold (V6.6: was -2.0%) |
| `VIX_DIRECTION_STABLE_LOW` | -1.0% | Stable range lower bound (V6.6: was -2.0%) |
| `VIX_DIRECTION_STABLE_HIGH` | 1.0% | Stable range upper bound (V6.6: was 2.0%) |
| `VIX_DIRECTION_RISING` | 3.0% | Fear building threshold (V6.6: was 5.0%) |
| `VIX_DIRECTION_RISING_FAST` | 6.0% | Panic emerging threshold (V6.6: was 10.0%) |
| `VIX_DIRECTION_SPIKING` | 10.0% | Crash mode threshold |

### V6.13.1 VIX-Adaptive STABLE Band

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `VIX_STABLE_BAND_LOW` | +/-0.2% | V6.13.1: STABLE band when VIX < 15 (was +/-0.3%) |
| `VIX_STABLE_BAND_HIGH` | +/-0.7% | V6.13.1: STABLE band when VIX > 25 (was +/-1.0%) |
| `VIX_STABLE_LOW_VIX_MAX` | 15.0 | Low VIX regime threshold |
| `VIX_STABLE_HIGH_VIX_MIN` | 25.0 | High VIX regime threshold |

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

### V2.1.1 Intraday Strategy Parameters (V6.14)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `INTRADAY_DEBIT_FADE_MIN_SCORE` | 35 | Minimum micro score for debit fade (V6.8: was 45) |
| `INTRADAY_DEBIT_FADE_VIX_MIN` | 9.5 | V6.13: Minimum VIX for DEBIT_FADE (was 10.5) |
| `INTRADAY_FADE_MIN_MOVE` | 0.45% | V6.13: Minimum QQQ move for FADE (was 0.50%) |
| `INTRADAY_FADE_MAX_MOVE` | 1.50% | Max QQQ move for FADE (V6.8: was 1.20%) |
| `INTRADAY_DEBIT_FADE_VIX_MAX` | 25 | Maximum VIX for debit fade |
| `INTRADAY_CREDIT_MIN_VIX` | 18 | Minimum VIX for credit spreads |
| `INTRADAY_ITM_MIN_VIX` | 9.0 | V6.13: Minimum VIX for ITM momentum (was 10.0) |
| `INTRADAY_ITM_MIN_MOVE` | 0.40% | V6.13: Minimum QQQ move for ITM (was 0.8%) |
| `INTRADAY_ITM_MIN_SCORE` | 40 | Minimum micro score for ITM (V6.8: was 50) |
| `INTRADAY_ITM_TARGET` | 0.35 | V6.13: ITM profit target (earlier capture) |
| `INTRADAY_ITM_STOP` | 0.35 | V6.13: ITM stop loss (reduce catastrophic losses) |
| `INTRADAY_FORCE_EXIT_TIME` | 15:25 ET | V6.15: Intraday positions must close (was 15:30) |
| `INTRADAY_OPTIONS_OFFSET_MINUTES` | 35 | V6.15: Dynamic close offset (aligned with 15:25) |
| `INTRADAY_QQQ_FALLBACK_MIN_MOVE` | 0.12% | V6.15: Min move for VIX STABLE fallback (was 0.30%) |
| `QQQ_NOISE_THRESHOLD` | 0.13% | V6.14: Noise filter threshold (was 0.15%) |

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

### Exposure Group Limits (V6.11)

| Group | Max Net Long | Max Net Short | Max Gross |
|-------|:------------:|:-------------:|:---------:|
| `NASDAQ_BETA` | 50% | 30% | 75% |
| `SPY_BETA` | 40% | 15% | 50% |
| `COMMODITIES` | 25% | 0% | 25% |

### Group Membership (V6.11 Universe)

| Symbol | Group | Type | Allocation |
|--------|-------|------|:----------:|
| QLD | NASDAQ_BETA | 2× Long Nasdaq | 15% (Trend) |
| TQQQ | NASDAQ_BETA | 3× Long Nasdaq | 4% (MR) |
| SOXL | NASDAQ_BETA | 3× Long Semi | 3% (MR) |
| SSO | SPY_BETA | 2× Long S&P | 7% (Trend) |
| SPXL | SPY_BETA | 3× Long S&P | 3% (MR) |
| SH | SPY_BETA | 1× Inverse S&P | (Hedge) |
| UGL | COMMODITY | 2× Gold | 10% (Trend) |
| UCO | COMMODITY | 2× Crude Oil | 8% (Trend) |

> **V6.11 Universe Redesign:** TNA/FAS/TMF/PSQ/SHV removed. UGL/UCO added for commodity diversification. All Trend symbols now 2× leverage. SH replaces PSQ as hedge.

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

## 16.9.1 Startup Gate Parameters (V6.0)

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `STARTUP_GATE_ENABLED` | True | Master toggle for startup gate |
| `STARTUP_GATE_WARMUP_DAYS` | 3 | Indicator warmup phase duration |
| `STARTUP_GATE_REDUCED_DAYS` | 3 | Reduced phase duration |
| `STARTUP_GATE_REDUCED_SIZE_MULT` | 0.50 | TREND/MR size multiplier during REDUCED |

### Phase Progression (V6.0 Simplified)

| Phase | Duration | TREND/MR | OPTIONS |
|-------|:--------:|:--------:|:-------:|
| INDICATOR_WARMUP | 3 days | Blocked | Blocked |
| REDUCED | 3 days | 50% | 100% |
| FULLY_ARMED | Permanent | 100% | 100% |

> **V6.0 Design:** Simplified 6-day arming sequence. Options are independent with their own conviction system (VASS/MICRO). Gate only controls TREND/MR sizing. Never resets on kill switch.

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

### Traded Symbols (V6.11 Universe)

| Symbol | Description | Strategy | Allocation |
|--------|-------------|----------|:----------:|
| QLD | 2× Nasdaq | Trend (Core) | 15% |
| SSO | 2× S&P 500 | Trend (Core) | 7% |
| UGL | 2× Gold | Trend (Commodity) | 10% (V6.11 NEW) |
| UCO | 2× Crude Oil | Trend (Commodity) | 8% (V6.11 NEW) |
| TQQQ | 3× Nasdaq | Mean Reversion | 4% |
| SPXL | 3× S&P 500 | Mean Reversion | 3% |
| SOXL | 3× Semiconductor | Mean Reversion | 3% |
| QQQ Options | Options chain | Options (VASS) | 25% |
| SH | 1× Inverse S&P | Hedge | 0-10% |

> **V6.11 Universe Redesign:** Replaced TNA/FAS with UGL/UCO for commodity diversification. All Trend symbols now 2× leverage. TMF/PSQ/SHV removed from default universe.

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
# REGIME ENGINE (V5.3 4-Factor Model)
# =============================================================================

# V5.3 Model Selection
V53_REGIME_ENABLED = True    # Enable V5.3 4-factor model (recommended)
V4_REGIME_ENABLED = False    # V4.0/V4.1 5-factor model (deprecated)
V3_REGIME_SIMPLIFIED_ENABLED = True  # V3.3 3-factor fallback

# V5.3 Factor Weights (must sum to 1.0)
WEIGHT_MOMENTUM_V53 = 0.30   # 20-day ROC - catches reversals in days
WEIGHT_VIX_COMBINED_V53 = 0.30  # 60% level + 40% direction
WEIGHT_TREND_V53 = 0.25      # SPY vs MA200
WEIGHT_DRAWDOWN_V53 = 0.15   # Distance from 52-week high

# VIX Combined Scoring
VIX_COMBINED_LEVEL_WEIGHT = 0.60      # VIX level contribution
VIX_COMBINED_DIRECTION_WEIGHT = 0.40  # VIX direction contribution
VIX_COMBINED_HIGH_VIX_THRESHOLD = 25.0  # Clamp threshold
VIX_COMBINED_HIGH_VIX_CLAMP = 47        # Max score when VIX >= 25

# V5.3 Guards
V53_SPIKE_CAP_ENABLED = True
V53_SPIKE_CAP_THRESHOLD = 0.28        # VIX 5d >= +28% triggers
V53_SPIKE_CAP_MAX_SCORE = 38          # V6.6: was 45
V53_SPIKE_CAP_DECAY_DAYS = 3
V53_BREADTH_DECAY_ENABLED = True
V53_BREADTH_5D_DECAY_THRESHOLD = -0.01
V53_BREADTH_10D_DECAY_THRESHOLD = -0.03
V53_BREADTH_5D_PENALTY = 8
V53_BREADTH_10D_PENALTY = 12

# VIX Level Thresholds
VIX_LOW_THRESHOLD = 15       # Below = complacent
VIX_NORMAL_THRESHOLD = 22    # Normal volatility
VIX_HIGH_THRESHOLD = 30      # Elevated fear
VIX_EXTREME_THRESHOLD = 40   # Crisis mode

# Smoothing
REGIME_SMOOTHING_ALPHA = 0.30

# Thresholds (V6.15 update)
REGIME_RISK_ON = 70
REGIME_NEUTRAL = 50
REGIME_CAUTIOUS = 45  # V6.15: Was 40
REGIME_DEFENSIVE = 35  # V6.15: Was 30

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
OPTIONS_PROFIT_TARGET_PCT = 0.60  # V6.15: Raised from 0.50

# Time Constraints
OPTIONS_ENTRY_START = "10:00"
OPTIONS_ENTRY_END = "14:30"
OPTIONS_LATE_DAY_TIME = "14:30"
OPTIONS_FORCE_EXIT_HOUR = 15    # 3 PM ET
OPTIONS_FORCE_EXIT_MINUTE = 45  # 3:45 PM ET
INTRADAY_FORCE_EXIT_TIME = "15:25"  # V6.15: Was 15:30, moved earlier for OCO race

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

# Exposure Limits (V6.11 Universe)
EXPOSURE_LIMITS = {
    "NASDAQ_BETA": {"max_net_long": 0.50, "max_net_short": 0.30, "max_gross": 0.75},
    "SPY_BETA": {"max_net_long": 0.40, "max_net_short": 0.10, "max_gross": 0.50},
    "COMMODITY": {"max_net_long": 0.20, "max_net_short": 0.00, "max_gross": 0.20},
}

# Group Membership (V6.11 Universe)
SYMBOL_GROUPS = {
    "QLD": "NASDAQ_BETA",
    "TQQQ": "NASDAQ_BETA",
    "SOXL": "NASDAQ_BETA",
    "SSO": "SPY_BETA",
    "SPXL": "SPY_BETA",
    "SH": "SPY_BETA",     # Inverse S&P (Hedge)
    "UGL": "COMMODITY",   # 2× Gold
    "UCO": "COMMODITY",   # 2× Crude Oil
}

# Trend Engine Allocations (V6.11)
TREND_SYMBOL_ALLOCATIONS = {
    "QLD": 0.15,  # 15% - 2× Nasdaq
    "SSO": 0.07,  # 7% - 2× S&P 500
    "UGL": 0.10,  # 10% - 2× Gold
    "UCO": 0.08,  # 8% - 2× Crude Oil
}
TREND_TOTAL_ALLOCATION = 0.40  # 40% total

# Mean Reversion Allocations (V6.11)
MR_SYMBOL_ALLOCATIONS = {
    "TQQQ": 0.04,  # 4% - 3× Nasdaq
    "SPXL": 0.03,  # 3% - 3× S&P 500
    "SOXL": 0.03,  # 3% - 3× Semiconductor
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

# V6.0: Simplified Startup Gate (6-day arming, never resets on kill switch)
STARTUP_GATE_ENABLED = True
STARTUP_GATE_WARMUP_DAYS = 3            # Phase 0: Indicators warming up (nothing allowed)
STARTUP_GATE_REDUCED_DAYS = 3           # Phase 1: TREND/MR at 50%, OPTIONS at 100%
STARTUP_GATE_REDUCED_SIZE_MULT = 0.50   # TREND/MR size multiplier during REDUCED

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
