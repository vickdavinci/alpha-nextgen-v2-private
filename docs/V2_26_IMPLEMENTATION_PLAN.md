# V2.26+ Implementation Plan — Capital Preservation & Strategy Quality

> **Created:** 2026-02-03
> **Source:** V2.25 2015 Full Year AAP Audit + Architect Review
> **Audit Reports:** `docs/audits/V2_25_2015_FullYear_audit_report.md`, `docs/audits/V2_25_2015_fix_simulation.md`
> **Branch:** `testing/va/stage2-backtest` → merge to `develop` after validation

---

## Problem Statement

The V2.25 2015 full year backtest lost **-41.9%** ($50K → $29K). Root causes:

- **20 kill switch triggers** — each liquidates everything and resets cold start to day 0
- **42 debit spread trades at 28.6% win rate** — structurally unprofitable in 2015's choppy market
- **Regime engine scored 67-74 (NEUTRAL/RISK_ON)** throughout the Aug 2015 -12% crash
- **207 VASS credit spread rejections** — zero credit fills despite system attempting them
- **No cumulative drawdown protection** — daily KS at -5% allows death by 20 cuts

The V2.25 fix simulation proved that risk management fixes alone (KS decouple + -40% stop) produce **~$0 net improvement** because benefits and costs cancel out. The system needs architectural changes, not just tighter stops.

---

## Goals

| Market Condition | Target Behavior |
|-----------------|-----------------|
| **Bull (2013, 2017, 2021)** | Full performance, no drag from new fixes |
| **Chop (2015)** | Flat to -10% max. System detects chop and reduces exposure. |
| **Bear (2022)** | Meaningful profits from credit spreads + hedges |
| **Crash (COVID Mar 2020)** | Survive. Max drawdown capped at -20%. |
| **Recovery (Apr-Dec 2020)** | Fast re-entry, accelerate into rally |

---

## Decision Log — What We Assessed and Chose

> This section documents the analysis behind each decision for future reference.

### Source: Architect's Recommendations

| Architect Proposed | Our Assessment | Decision |
|-------------------|----------------|----------|
| VIX > 22 AND ADX < 20 → block debit spreads | Only 2 of 43 entries in 2015 had VIX > 22. Blocks 5% of trades, saves 32% of losses. Too narrow for 2015 (normal-vol chop, not high-vol chop). | **Rejected as primary filter.** ADX-based regime chop factor is wider net. VIX > 22 kept as secondary guard. |
| Switch to credit spreads in chop | VASS already switches to credit at VIX > 25. Generated 200 rejections, 0 fills. Credit fills don't work in our constraint pipeline (delta 0.25-0.40, DTE 7-14, min credit $0.30). | **Cash in chop, not credit.** Keep existing credit mode for VIX > 25 only. |
| Credit mode with VIX > 15 floor (penny steamroller guard) | Valid design principle. At VIX < 15, credit premium is $0.10-0.20 on $5-wide spread — 25:1 risk:reward. Existing $0.30 min credit floor already rejects these. | **Bank as insurance.** Add `CREDIT_SPREAD_MIN_VIX = 18` to config for if/when credit fill pipeline is fixed. |
| Retain KS decouple + -40% spread stop | Simulation shows +$3,692 gross, ~$0 net. -40% stop kills 3 winners with 50-80% interim DD. | **Adopt KS decouple. Widen stop to -50%** per sensitivity analysis (optimal for 2015). |

### Source: Our Analysis

| Our Proposal | Rationale | Decision |
|-------------|-----------|----------|
| Drawdown Governor (-20% hard cap) | No cumulative DD protection today. 20 KS triggers at -5% each = -42% annual. Governor would have capped at -20%. | **Adopted — P0, non-negotiable for retail.** |
| Chop Detector (ADX regime factor) | Regime engine blind to chop (scored 67-74 during -12% crash). ADX < 20 for 10+ days = choppy. | **Adopted — P0.** Broader than VIX > 22 filter. |
| Win Rate Gate (rolling 10-trade shutoff) | Self-correcting. After 10 trades at 28.6% win rate, options shut down. No static threshold needed. | **Adopted — P1.** |
| KS Reform (graduated tiers) | Binary KS creates cascade: liquidate → cold start → re-enter → liquidate. Graduated tiers break the cycle. | **Adopted — P1.** |
| Recovery Acceleration (fast-track cold start) | 5-day cold start misses V-shaped recovery. Fast-track to 2 days when regime confirms. | **Adopted — P2.** |

---

## Implementation Phases

### Phase 1: V2.26 — Capital Preservation (P0)

**Goal:** Never lose more than 20% from peak. Detect chop and reduce exposure.

#### Fix 1: Drawdown Governor

**File:** `engines/core/risk_engine.py`
**Config:** `config.py`

Tracks equity high watermark. Scales ALL engine allocations based on drawdown from peak.

```
New config parameters:

DRAWDOWN_GOVERNOR_ENABLED = True
DRAWDOWN_GOVERNOR_LEVELS = {
    0.05: 0.75,   # At -5% from peak → 75% allocation
    0.10: 0.50,   # At -10% from peak → 50% allocation
    0.15: 0.25,   # At -15% from peak → 25% allocation
    0.20: 0.00,   # At -20% from peak → CASH ONLY (SHV + hedges)
}
DRAWDOWN_GOVERNOR_RECOVERY_PCT = 0.05  # Must recover 5% from trough before stepping up
```

**Implementation details:**

1. Add `_equity_high_watermark` field to `RiskEngine.__init__()` (default: starting equity)
2. Add `_governor_scale` field (default: 1.0)
3. New method `check_drawdown_governor(current_equity)`:
   - Update high watermark: `max(self._equity_high_watermark, current_equity)`
   - Calculate DD: `(high_watermark - current_equity) / high_watermark`
   - Walk DRAWDOWN_GOVERNOR_LEVELS to find applicable scale
   - Implement hysteresis: only step UP scale when equity recovers RECOVERY_PCT from trough
   - Return `_governor_scale` (float 0.0-1.0)
   - Log: `DRAWDOWN_GOVERNOR: DD={dd:.1%} | Scale={scale:.0%} | HWM={hwm:,.0f} | Current={current:,.0f}`
4. Call from `main.py` at market open (09:25 schedule) BEFORE any signal generation
5. All engines multiply their allocation by `governor_scale`:
   - Trend: `TREND_SYMBOL_ALLOCATIONS[sym] * governor_scale`
   - Options: `OPTIONS_SWING_ALLOCATION * governor_scale`
   - MR: `MR_SYMBOL_ALLOCATIONS[sym] * governor_scale`
6. Persist `_equity_high_watermark` and `_governor_scale` via StateManager

**State persistence fields:**
```python
"drawdown_governor": {
    "equity_high_watermark": float,
    "governor_scale": float,
    "trough_equity": float,      # For recovery tracking
    "scale_at_trough": float,    # Scale when trough was set
}
```

**2015 expected impact:** After ~10% loss by late January (5 KS triggers), allocations drop to 50%. After ~15% by March, allocations at 25%. Limits full-year loss to ~-18% to -22%.

**Bull market impact:** High watermark rises continuously, governor never activates. Zero drag.

---

#### Fix 2: Chop Detector (Regime Engine Enhancement)

**File:** `engines/core/regime_engine.py`, `utils/calculations.py`
**Config:** `config.py`

Adds ADX(14) of SPY as a 5th factor to the regime engine. Detects lack of trend (chop) regardless of price level or VIX.

```
New config parameters:

# Chop Detection Factor
WEIGHT_TREND = 0.25          # REDUCED from 0.30 (free up 5% for chop factor)
WEIGHT_CHOP = 0.05           # NEW: Trend quality/consistency factor
# All other weights unchanged: VIX=0.20, VOL=0.15, BREADTH=0.20, CREDIT=0.15

CHOP_ADX_THRESHOLD_STRONG = 25   # ADX >= 25 = strong trend (score 100)
CHOP_ADX_THRESHOLD_MODERATE = 20 # ADX 20-25 = moderate (score 60)
CHOP_ADX_THRESHOLD_WEAK = 15     # ADX 15-20 = weak (score 30)
                                  # ADX < 15 = dead/choppy (score 10)

CHOP_LOOKBACK_DAYS = 10          # ADX must be below threshold for N days to confirm chop
```

**Implementation details:**

1. `main.py` `Initialize()`: Add `self.spy_adx = self.ADX("SPY", 14, Resolution.Daily)` indicator
2. `regime_engine.py` `calculate()`: Add new parameter `spy_adx: float = 25.0`
3. New function in `utils/calculations.py`:
   ```python
   def chop_factor_score(adx_value: float, strong: float, moderate: float, weak: float) -> float:
       """Score 0-100 based on ADX trend strength."""
       if adx_value >= strong:
           return 100  # Strong trend, safe for directional plays
       elif adx_value >= moderate:
           return 60   # Moderate, some directional plays OK
       elif adx_value >= weak:
           return 30   # Weak, reduce directional exposure
       else:
           return 10   # Dead/choppy, avoid directional plays
   ```
4. `aggregate_regime_score()` in `utils/calculations.py`: Add `chop_score` and `weight_chop` parameters
5. `RegimeState` dataclass: Add `chop_score: float` and `spy_adx_value: float` fields
6. Log format update: `REGIME: ... C_ADX={chop_score:.0f}`

**Interaction with options engine:**

The chop factor lowers the regime score. When regime drops below `TREND_ENTRY_REGIME_MIN` (40), trend entries are blocked. When below `REGIME_NEUTRAL` (50), cold start is blocked. The options engine already checks regime score for entry decisions.

However, the chop factor (5% weight) alone won't drop the score enough to block entries. The primary mechanism is the **Win Rate Gate** (Fix 4) which self-corrects based on actual results. The chop detector provides *directional pressure* on the regime score, not a hard block.

**2015 expected impact:** SPY ADX was below 20 for most of Feb-Mar, Jun-Jul, Aug-Oct. Chop factor would lower regime scores by 3-5 points during these periods, increasing hedge targets and reducing allocation confidence.

**Bull market impact:** ADX > 25 in trending markets → chop score = 100, no drag.

---

### Phase 2: V2.27 — Strategy Quality (P1)

**Goal:** Self-correcting options throttle. Fewer kill switch cascades.

#### Fix 3: KS Decouple + -50% Spread Stop

**File:** `main.py` (OnOrderEvent / kill switch handler), `engines/satellite/options_engine.py`
**Config:** `config.py`

```
New/modified config parameters:

KILL_SWITCH_SPREAD_DECOUPLE = True   # Spreads not killed by KS
SPREAD_HARD_STOP_PCT = 0.50         # -50% of net debit (widened from -40% per simulation)
```

**Implementation details — KS Decouple:**

1. In `main.py` kill switch handler (currently liquidates ALL positions):
   - Add check: `if KILL_SWITCH_SPREAD_DECOUPLE:`
   - Skip liquidation for symbols that are part of active spread positions
   - Still liquidate: trend positions (QLD, SSO, TNA, FAS), MR (TQQQ, SOXL), hedges
   - Still reset cold start
   - Log: `KILL_SWITCH: SPREAD_DECOUPLE | Keeping {N} active spreads | Liquidating trend/MR`

2. Spread position tracking:
   - The options engine already tracks active spreads via `_active_positions` dict
   - During KS, read this dict to identify option symbols to preserve
   - Preserved spreads continue to be monitored by their own stop (Fix 3b below)

**Implementation details — Spread Hard Stop:**

3. In `options_engine.py`, the spread monitoring loop (runs every minute for active positions):
   - For each active spread, calculate net spread value: `long_leg_value - short_leg_value`
   - Compare to entry net debit: `entry_long_price - entry_short_price`
   - If `(current_net - entry_net) / entry_net <= -SPREAD_HARD_STOP_PCT`:
     - Emit exit signal for both legs simultaneously
     - Log: `SPREAD_STOP: {spread_id} | Entry=${entry_net:.2f} | Current=${current_net:.2f} | Loss={loss_pct:.1%} | CLOSING`
   - CRITICAL: Always close BOTH legs together. Never close one leg alone (naked short risk).

**2015 expected impact:** 18 KS-closed spreads now run with -50% stop. 4 recover to profit (+$15,720). 7 fall to -50% (~$3,500 worse than KS catch). 3 non-KS winners survive (unlike -40% which killed them). Net: approximately +$6,000-8,000 improvement.

---

#### Fix 4: Win Rate Gate (Options Throttle)

**File:** `engines/satellite/options_engine.py`
**Config:** `config.py`

Self-correcting mechanism that scales down and eventually shuts off options when the strategy is failing.

```
New config parameters:

WIN_RATE_GATE_ENABLED = True
WIN_RATE_LOOKBACK = 10               # Rolling window of recent closed spread trades
WIN_RATE_FULL_THRESHOLD = 0.40       # Above 40%: full size
WIN_RATE_REDUCED_THRESHOLD = 0.30    # 30-40%: 75% size
WIN_RATE_MINIMUM_THRESHOLD = 0.20    # 20-30%: 50% size
WIN_RATE_SHUTOFF_THRESHOLD = 0.20    # Below 20%: STOP all new spread entries
WIN_RATE_RESTART_THRESHOLD = 0.35    # Resume when paper win rate recovers to 35%
WIN_RATE_SIZING_REDUCED = 0.75       # Multiplier at REDUCED level
WIN_RATE_SIZING_MINIMUM = 0.50       # Multiplier at MINIMUM level
```

**Implementation details:**

1. Add `_spread_result_history: List[bool]` to options engine (circular buffer, max `WIN_RATE_LOOKBACK`)
2. After every spread close (win or loss), append result: `True` for win, `False` for loss
3. New method `get_win_rate_scale() -> float`:
   ```python
   def get_win_rate_scale(self) -> float:
       if len(self._spread_result_history) < WIN_RATE_LOOKBACK:
           return 1.0  # Not enough data, full size
       win_rate = sum(self._spread_result_history[-WIN_RATE_LOOKBACK:]) / WIN_RATE_LOOKBACK
       if win_rate >= WIN_RATE_FULL_THRESHOLD:
           return 1.0
       elif win_rate >= WIN_RATE_REDUCED_THRESHOLD:
           return WIN_RATE_SIZING_REDUCED  # 0.75
       elif win_rate >= WIN_RATE_MINIMUM_THRESHOLD:
           return WIN_RATE_SIZING_MINIMUM  # 0.50
       else:
           return 0.0  # SHUTOFF
   ```
4. When scale = 0.0 (shutoff):
   - Block all new spread entries
   - Existing positions run to natural exit (or -50% stop)
   - Continue paper-tracking signals: count hypothetical wins/losses
   - When paper win rate reaches `WIN_RATE_RESTART_THRESHOLD` over 10 paper trades, resume real trading
   - Log: `WIN_RATE_GATE: SHUTOFF | WinRate={wr:.0%} | LastN={results} | Paper tracking active`
5. When scale = 0.75 or 0.50:
   - Multiply spread contract count by scale (round down, minimum `MIN_SPREAD_CONTRACTS`)
   - Log: `WIN_RATE_GATE: REDUCED | WinRate={wr:.0%} | Scale={scale:.0%}`
6. Persist `_spread_result_history` and `_paper_track_history` via StateManager

**2015 expected impact:** After 10 trades (3W/7L = 30%), sizing drops to 75%. After ~15 trades (~25%), sizing at 50%. After ~20 trades (~20%), options shut off. Remaining ~22 trades never happen, saving ~$12,000 in losses.

**Bull market impact:** Win rate > 40% in trending markets. Gate never activates. Zero drag.

---

#### Fix 5: KS Reform (Graduated Response)

**File:** `engines/core/risk_engine.py`, `main.py`
**Config:** `config.py`

Replace binary kill switch with tiered response. Break the KS → cold start → re-enter → KS cascade.

```
New/modified config parameters:

KS_GRADUATED_ENABLED = True
KS_TIER_1_PCT = 0.03           # -3% daily loss → REDUCE (cut trend 50%, block new options)
KS_TIER_2_PCT = 0.05           # -5% daily loss → TREND_EXIT (liquidate trend, keep spreads)
KS_TIER_3_PCT = 0.08           # -8% daily loss → FULL_EXIT (liquidate everything)

KS_TIER_1_TREND_REDUCTION = 0.50   # Reduce trend allocation by 50% at Tier 1
KS_TIER_1_BLOCK_NEW_OPTIONS = True  # Block new option entries at Tier 1
KS_SKIP_DAYS = 1                    # Block new entries for 1 day after Tier 2+
KS_COLD_START_RESET_ON_TIER_2 = False  # Don't reset cold start on Tier 2 (skip-day only)
KS_COLD_START_RESET_ON_TIER_3 = True   # Reset cold start on Tier 3 (true emergency)
```

**Implementation details:**

1. Modify `check_kill_switch()` in `risk_engine.py`:
   - Instead of single threshold, walk tiers from highest to lowest
   - Return `KSTier` enum: `NONE`, `REDUCE`, `TREND_EXIT`, `FULL_EXIT`
   - Log tier-specific messages

2. Modify kill switch handler in `main.py`:
   - **REDUCE (Tier 1):** Halve trend allocations for remainder of day. Block new option entries. No liquidation. No cold start reset.
   - **TREND_EXIT (Tier 2):** Liquidate trend + MR positions. Keep active spreads (per Fix 3 decouple). Skip 1 day of new entries. No cold start reset.
   - **FULL_EXIT (Tier 3):** Liquidate everything including spreads. Reset cold start. This is the true emergency (e.g., -8% flash crash).

3. Add `_ks_skip_until_date` field for skip-day enforcement

**2015 expected impact:** Instead of 20 full liquidations + cold start resets, expect ~30 Tier 1 (REDUCE) events and ~8-10 Tier 2 (TREND_EXIT) events. Very few Tier 3. Spreads survive Tier 1 and 2. Cold start not reset on Tier 2. Faster recovery between episodes.

---

### Phase 3: V2.28 — Recovery & Insurance (P2)

#### Fix 6: Recovery Acceleration

**File:** `engines/core/cold_start_engine.py`
**Config:** `config.py`

```
New config parameters:

COLD_START_FAST_TRACK_ENABLED = True
COLD_START_FAST_TRACK_REGIME_MIN = 65   # Regime score to qualify
COLD_START_FAST_TRACK_VIX_MAX = 25      # VIX must be dropping/moderate
COLD_START_FAST_TRACK_DAYS = 2          # Exit cold start at day 2 (not day 5)
COLD_START_FAST_TRACK_SIZING = 0.75     # 75% sizing on fast-track exit
```

**Implementation details:**

1. In cold start engine daily check (runs at 10:00):
   - If in cold start AND `current_day >= FAST_TRACK_DAYS`:
     - Check: `regime_score >= FAST_TRACK_REGIME_MIN`
     - Check: `vix_level <= FAST_TRACK_VIX_MAX`
     - If both pass: exit cold start early with `FAST_TRACK_SIZING` multiplier
     - Log: `COLD_START_FAST_TRACK: Day={day} | Regime={score} | VIX={vix} | Sizing=75%`
2. The fast-track sizing decays to 100% over the remaining cold start days

**2020 expected impact:** After March crash, fast-track at day 2-3 of cold start when regime confirms recovery. Capture initial bounce.

---

#### Fix 7: Penny Steamroller Guard (Insurance)

**File:** `config.py`

```
New config parameter:

CREDIT_SPREAD_MIN_VIX = 18   # Never attempt credit spreads below VIX 18
```

**Implementation details:**

1. In `options_engine.py` `select_credit_spread_legs()`:
   - At top of method, check: `if vix_level < CREDIT_SPREAD_MIN_VIX: return None`
   - Log: `CREDIT_SPREAD_VIX_FLOOR: VIX={vix:.1f} < {CREDIT_SPREAD_MIN_VIX} | Skipping`
2. This is insurance against future code changes that might enable credit mode at low VIX

**Current impact:** Minimal — existing VASS only activates credit mode at VIX > 25. This guard prevents accidents if thresholds are lowered later.

---

## Files Modified (Complete List)

| File | Phase | Fixes | Changes |
|------|:-----:|-------|---------|
| `config.py` | 1,2,3 | 1,2,3,4,5,6,7 | All new parameters listed above |
| `engines/core/risk_engine.py` | 1,2 | 1,5 | Drawdown governor + graduated KS |
| `engines/core/regime_engine.py` | 1 | 2 | Chop factor (5th regime factor) |
| `utils/calculations.py` | 1 | 2 | `chop_factor_score()` + updated `aggregate_regime_score()` |
| `main.py` | 1,2 | 1,2,3,5 | Governor call at open, SPY ADX indicator, KS handler refactor, governor scale passthrough |
| `engines/satellite/options_engine.py` | 2,3 | 3,4,7 | Spread hard stop, win rate gate, VIX floor on credit |
| `engines/core/cold_start_engine.py` | 3 | 6 | Fast-track logic |
| `persistence/state_manager.py` | 1,2 | 1,4 | New persisted fields |
| `models/enums.py` | 2 | 5 | `KSTier` enum |
| `WORKBOARD.md` | All | All | Version entries |

---

## Config Parameters Summary (All New)

```python
# === FIX 1: DRAWDOWN GOVERNOR ===
DRAWDOWN_GOVERNOR_ENABLED = True
DRAWDOWN_GOVERNOR_LEVELS = {
    0.05: 0.75,
    0.10: 0.50,
    0.15: 0.25,
    0.20: 0.00,
}
DRAWDOWN_GOVERNOR_RECOVERY_PCT = 0.05

# === FIX 2: CHOP DETECTOR ===
WEIGHT_TREND = 0.25                   # MODIFIED (was 0.30)
WEIGHT_CHOP = 0.05                    # NEW
CHOP_ADX_THRESHOLD_STRONG = 25
CHOP_ADX_THRESHOLD_MODERATE = 20
CHOP_ADX_THRESHOLD_WEAK = 15
CHOP_LOOKBACK_DAYS = 10

# === FIX 3: KS DECOUPLE + SPREAD STOP ===
KILL_SWITCH_SPREAD_DECOUPLE = True
SPREAD_HARD_STOP_PCT = 0.50

# === FIX 4: WIN RATE GATE ===
WIN_RATE_GATE_ENABLED = True
WIN_RATE_LOOKBACK = 10
WIN_RATE_FULL_THRESHOLD = 0.40
WIN_RATE_REDUCED_THRESHOLD = 0.30
WIN_RATE_MINIMUM_THRESHOLD = 0.20
WIN_RATE_SHUTOFF_THRESHOLD = 0.20
WIN_RATE_RESTART_THRESHOLD = 0.35
WIN_RATE_SIZING_REDUCED = 0.75
WIN_RATE_SIZING_MINIMUM = 0.50

# === FIX 5: KS REFORM ===
KS_GRADUATED_ENABLED = True
KS_TIER_1_PCT = 0.03
KS_TIER_2_PCT = 0.05
KS_TIER_3_PCT = 0.08
KS_TIER_1_TREND_REDUCTION = 0.50
KS_TIER_1_BLOCK_NEW_OPTIONS = True
KS_SKIP_DAYS = 1
KS_COLD_START_RESET_ON_TIER_2 = False
KS_COLD_START_RESET_ON_TIER_3 = True

# === FIX 6: RECOVERY ACCELERATION ===
COLD_START_FAST_TRACK_ENABLED = True
COLD_START_FAST_TRACK_REGIME_MIN = 65
COLD_START_FAST_TRACK_VIX_MAX = 25
COLD_START_FAST_TRACK_DAYS = 2
COLD_START_FAST_TRACK_SIZING = 0.75

# === FIX 7: PENNY STEAMROLLER GUARD ===
CREDIT_SPREAD_MIN_VIX = 18
```

---

## Backtest Validation Plan

### Phase 1 Validation (V2.26)

| # | Backtest | Period | Validates | Pass Criteria |
|:-:|----------|--------|-----------|---------------|
| 1 | V2.26-2015-FullYear | Jan-Dec 2015 | Governor + Chop | Max DD < -22%, better than -42% |
| 2 | V2.26-2017-Bull | Jan-Dec 2017 | No bull market drag | Return within 5% of V2.25 baseline |
| 3 | V2.26-2020-Crash | Jan-Jun 2020 | Crash survival | Max DD < -20% during March |

### Phase 2 Validation (V2.27)

| # | Backtest | Period | Validates | Pass Criteria |
|:-:|----------|--------|-----------|---------------|
| 4 | V2.27-2015-FullYear | Jan-Dec 2015 | All P0+P1 fixes | Max DD < -18%, spread count < 25 |
| 5 | V2.27-Q1-2022 | Jan-Mar 2022 | Bear market credit | Credit spread fills > 0 |
| 6 | V2.27-2020-FullYear | Jan-Dec 2020 | Recovery speed | Re-entry within 5 days of March bottom |

### Phase 3 Validation (V2.28)

| # | Backtest | Period | Validates | Pass Criteria |
|:-:|----------|--------|-----------|---------------|
| 7 | V2.28-2020-FullYear | Jan-Dec 2020 | Fast-track recovery | Better return than V2.27 baseline |
| 8 | V2.28-2013-FullYear | Jan-Dec 2013 | Strong bull | No governor activation, full performance |

### Validation Commands

```bash
# Phase 1
./scripts/qc_backtest.sh "V2.26-2015-FullYear" --open
./scripts/qc_backtest.sh "V2.26-2017-Bull" --open
./scripts/qc_backtest.sh "V2.26-2020-Crash" --open

# Phase 2
./scripts/qc_backtest.sh "V2.27-2015-FullYear" --open
./scripts/qc_backtest.sh "V2.27-Q1-2022" --open
./scripts/qc_backtest.sh "V2.27-2020-FullYear" --open

# Phase 3
./scripts/qc_backtest.sh "V2.28-2020-FullYear" --open
./scripts/qc_backtest.sh "V2.28-2013-FullYear" --open
```

---

## Log Keywords for Audit

| Keyword | Fix | Meaning |
|---------|:---:|---------|
| `DRAWDOWN_GOVERNOR` | 1 | Governor activated/scaled |
| `CHOP_FACTOR` | 2 | Chop score computed |
| `KILL_SWITCH: SPREAD_DECOUPLE` | 3 | KS fired but spreads preserved |
| `SPREAD_STOP` | 3 | Spread hit -50% hard stop |
| `WIN_RATE_GATE` | 4 | Win rate scaled/shutoff/resumed |
| `KS_TIER_1: REDUCE` | 5 | -3% daily, reduced sizing |
| `KS_TIER_2: TREND_EXIT` | 5 | -5% daily, trend liquidated |
| `KS_TIER_3: FULL_EXIT` | 5 | -8% daily, everything liquidated |
| `COLD_START_FAST_TRACK` | 6 | Early cold start exit |
| `CREDIT_SPREAD_VIX_FLOOR` | 7 | Credit spread blocked by VIX floor |

---

## Risk Matrix

| Fix | If Calibrated Wrong | Mitigation |
|-----|--------------------:|------------|
| Drawdown Governor | Too aggressive → misses recovery rally | Recovery hysteresis (must recover 5% before stepping up) |
| Chop Detector | False positives in slow grind-up → blocks valid trend entries | Only 5% weight; won't block alone. Win rate gate is the self-correcting layer. |
| -50% Spread Stop | Still kills some winners with deep interim DD | Sensitivity analysis showed -50% is optimal for 2015. Monitor in bull backtests. |
| Win Rate Gate | Shuts off too early → misses recovery trades | Paper tracking with 35% restart threshold. 10-trade window resets quickly. |
| KS Graduated | Tier 1 at -3% too sensitive → constant 50% sizing | Monitor Tier 1 trigger frequency. Raise to -3.5% if > 2 per week average. |
| Recovery Fast-Track | Re-enters too early → gets caught in dead cat bounce | Requires BOTH regime > 65 AND VIX < 25. Two-factor confirmation. |
| VIX Floor on Credit | Too high (18) → blocks valid credit entries | Only blocks credit at VIX < 18. Most credit premium is above VIX 20 anyway. |

---

## Dependencies Between Fixes

```
Fix 1 (Governor)     ── standalone, no dependencies
Fix 2 (Chop)         ── standalone, no dependencies
Fix 3 (KS Decouple)  ── requires Fix 5 (graduated KS) for full effect
Fix 4 (Win Rate)     ── standalone, but amplifies Fix 2 (chop reduces entries → fewer losses → higher win rate)
Fix 5 (KS Reform)    ── requires Fix 3 (decouple) to be meaningful
Fix 6 (Recovery)     ── requires Fix 5 (graduated KS, since cold start changes tie to KS tiers)
Fix 7 (VIX Floor)    ── standalone insurance
```

**Implementation order matters:** Fix 1 and 2 can be implemented independently (Phase 1). Fix 3 and 5 should be implemented together (they interact). Fix 4 is standalone but benefits from Phase 1 being in place first.

---

## Quick Reference for Context Recovery

> **If this is your first read or you just lost context, here's the 30-second summary:**
>
> V2.25 lost 42% in 2015 because the system kept trading directional debit spreads in a choppy market.
> Risk fixes alone (stop losses, KS decouple) cancel each other out (~$0 net).
>
> V2.26+ adds:
> 1. **Drawdown Governor** — hard cap losses at -20% from peak
> 2. **Chop Detector** — ADX-based regime factor to detect directionless markets
> 3. **KS Decouple + -50% Stop** — spreads have their own stop, not killed by KS
> 4. **Win Rate Gate** — shut off options when rolling 10-trade win rate < 20%
> 5. **Graduated KS** — -3% reduce, -5% exit trend, -8% exit all (replaces binary -5% nuke)
> 6. **Recovery Fast-Track** — exit cold start at day 2 when regime confirms recovery
> 7. **VIX Floor on Credit** — never sell credit spreads below VIX 18 (insurance)
>
> **Start here:** Read WORKBOARD.md for current task, then implement fixes in order (1→7).
