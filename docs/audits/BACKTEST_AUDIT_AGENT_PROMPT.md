# BACKTEST AUDIT AGENT PROMPT — V5.3

You are analyzing a backtest for the Alpha NextGen V2 algorithmic trading system.

## YOUR TASK
Read the backtest log file, analyze performance, verify all systems functioned correctly, and produce a structured audit report with actionable recommendations.

**PRIMARY FOCUS AREAS (V5.3):**
1. **Regime Identification** — Is the V4.1 regime score (5-factor model) computed correctly? Does it match market reality?
2. **Regime Navigation** — Are trades being placed appropriately for each regime state?
3. **Conviction Engine Validation** — Are VASS and Micro conviction overrides working? Do they fire during extreme conditions?
4. **Binary Governor** — Is the 100%/0% binary system working? Can it recover from 0%?
5. **Options Engine** — Are spreads firing correctly? Check position limits (max 3), direction selection, and DEBIT-only strategy.

---

## V5.3 CRITICAL CHANGES TO VERIFY

### ⚠️ Must Verify These V5.3 Changes Are Working:

| Change | What to Check | Log Pattern |
|--------|---------------|-------------|
| **Binary Governor** | Only 100% or 0% states (no 75%/50%/25%) | `GOVERNOR.*Scale` |
| **Governor Disabled** | Should be OFF for testing | `DRAWDOWN_GOVERNOR.*ENABLED` |
| **Neutrality Exit OFF** | Should NOT trigger | `NEUTRALITY_EXIT` (expect 0) |
| **VASS Daily VIX Tracking** | 5d and 20d VIX history populated | `VASS.*5d.*20d` |
| **VASS Conviction** | Fires on VIX 5d >+20% or <-15% | `VASS.*conviction.*BEARISH\|BULLISH` |
| **Micro Conviction** | Fires on UVXY >+8% or <-5% | `MICRO.*conviction` |
| **All DEBIT Strategy** | No CREDIT spreads in HIGH IV | `BULL_PUT_CREDIT\|BEAR_CALL_CREDIT` (expect 0) |
| **Position Limits** | Max 1 intraday + 2 swing = 3 total | `OPTIONS.*position.*limit` |
| **Regime V4.1** | Uses VIX Level not VIX Direction | `VIX=.*lvl=` in regime logs |

---

## CONTEXT
- Backtest name: V6.3_*.md
- Log file location: `docs/audits/logs/stage6/`
- Backtest period: {START_DATE} to {END_DATE}
- Starting capital: $75,000 (V5.3 increased from $50K)
- Market context: {BULL/BEAR/CHOPPY}

---

## STEP 1: Read Reference Files (DO THIS FIRST)
1. Read `config.py` — all thresholds, parameters, allocation percentages
2. Read `CLAUDE.md` — system architecture, critical rules, key times
3. Read the backtest log file (logs, orders, trades CSVs if available)

---

## STEP 2: Performance Summary
Extract from the log file:
- Final equity and net return %
- Total orders / trades
- Win rate, average win, average loss
- Max drawdown (date and %)
- Sharpe ratio if available

Present as a table.

---

## STEP 3: Regime Deep Dive (CRITICAL)

### 3A. Regime Distribution Overview
Search for `REGIME:` logs and create a complete regime distribution table:

| Regime State | Score Range | Days in Regime | % of Backtest | Avg Score |
|--------------|-------------|----------------|---------------|-----------|
| RISK_ON (Bull) | >= 70 | | | |
| UPPER_NEUTRAL | 60-69 | | | |
| LOWER_NEUTRAL | 50-59 | | | |
| CAUTIOUS | 40-49 | | | |
| DEFENSIVE | 30-39 | | | |
| RISK_OFF (Crisis) | < 30 | | | |

**Key Questions:**
- How many total trading days in the backtest?
- What was the dominant regime? Does it match the market context?
- Were there any regime "stuck" periods (same regime for >20 consecutive days)?

### 3B. V4.1 Regime Factor Validation
**V5.3 uses the 5-factor V4.1 model. Verify each factor is contributing correctly:**

| Factor | Weight | Expected Range | Actual Avg | Issue? |
|--------|--------|----------------|------------|--------|
| Momentum (20d ROC) | 30% | 10-90 | | |
| VIX Level (V4.1) | 25% | 15-85 based on VIX | | |
| Breadth (RSP/SPY) | 20% | 30-85 | | |
| Drawdown (52w high) | 15% | 10-90 | | |
| Trend (MA200) | 10% | 20-80 | | |

**V4.1 VIX Level Check:**
- Search for: `VIX=.*lvl=` in regime logs
- Verify VIX Level score maps correctly:
  - VIX < 15 → Score ~85-100 (complacent)
  - VIX 15-22 → Score ~50-70 (normal)
  - VIX 22-30 → Score ~30-50 (elevated)
  - VIX > 30 → Score ~0-30 (fear)

**🔴 FLAG if VIX Direction (5d change) is used instead of VIX Level — that's V4.0, not V4.1.**

### 3C. Regime Identification Accuracy
Compare the regime score against actual market conditions:

| Date Range | Actual Market | Expected Regime | Actual Regime | Match? |
|------------|---------------|-----------------|---------------|--------|
| {dates} | Rally +X% | RISK_ON (70+) | {actual} | Y/N |
| {dates} | Selloff -X% | DEFENSIVE (<40) | {actual} | Y/N |
| {dates} | Choppy ±X% | NEUTRAL (50-60) | {actual} | Y/N |

**Regime Transition Latency:**
- Search for major market events (SPY drops >3%, VIX spikes >25)
- How many days did it take for regime to respond?
- **V5.3 Target:** 1-3 days detection latency. Flag if >5 days.

---

## STEP 4: Conviction Engine Validation (NEW V5.3)

### 4A. VASS Conviction Engine
**Search:** `VASS.*conviction`, `VASS.*BEARISH`, `VASS.*BULLISH`, `VASS.*5d.*change`

| Metric | Expected | Actual |
|--------|----------|--------|
| VASS daily history populated | Yes (5+ days) | |
| VIX 5d change calculated | Values logged | |
| VIX 20d change calculated | Values logged | |
| Conviction triggers (total) | >0 in volatile periods | |
| BEARISH conviction fires | When VIX 5d >+20% | |
| BULLISH conviction fires | When VIX 5d <-15% | |
| Level crossing triggers | When VIX crosses 25 or 15 | |

**🔴 CRITICAL CHECK:** If `VASS.*5d.*change` shows `None` or is missing, the daily VIX tracking is broken.

**Expected log pattern:**
```
VASS: VIX 5d change +23% > +20% → BEARISH conviction
VASS: VIX crossed ABOVE 25 (fear threshold)
```

### 4B. Micro Conviction Engine
**Search:** `MICRO.*conviction`, `UVXY`, `MICRO.*BEARISH`, `MICRO.*BULLISH`

| Metric | Expected | Actual |
|--------|----------|--------|
| UVXY tracking active | Intraday changes logged | |
| BEARISH conviction | When UVXY >+8% intraday | |
| BULLISH conviction | When UVXY <-5% intraday | |
| VIX crisis trigger | When VIX >35 | |
| VIX complacent trigger | When VIX <12 | |
| State-based conviction | FULL_PANIC, CRASH → BEARISH | |

**🔴 CRITICAL CHECK:** Verify `MICRO_BEARISH_STATES` and `MICRO_BULLISH_STATES` in config match actual MicroRegime enum values:
- Valid BEARISH: `FULL_PANIC`, `CRASH`, `WORSENING_HIGH`, `BREAKING`, `VOLATILE`
- Valid BULLISH: `PERFECT_MR`, `GOOD_MR`, `NORMAL`, `RECOVERING`, `IMPROVING`, `PANIC_EASING`, `CALMING`

### 4C. Conviction Override (Veto) Analysis
When conviction fires, does it override Macro regime correctly?

| Date | Engine | Conviction | Macro Direction | Final Direction | Correct? |
|------|--------|------------|-----------------|-----------------|----------|
| | VASS | BEARISH | NEUTRAL | BEARISH (PUT) | |
| | MICRO | BULLISH | BEARISH | BULLISH (CALL) | |

**Search:** `VETO:`, `overrides Macro`

**Expected behavior:**
- ALIGNED: Engine + Macro agree → Trade proceeds
- VETO: Engine has conviction, overrides Macro → Trade with engine's direction
- NO_TRADE: Misaligned, no conviction → No trade

---

## STEP 5: Binary Governor Analysis (V5.3)

### 5A. Governor State Timeline
**V5.3 uses BINARY governor: only 100% or 0%**

| Date | Equity | HWM | Drawdown % | Governor Scale | Event |
|------|--------|-----|------------|----------------|-------|
| Day 1 | $75,000 | $75,000 | 0% | 100% | Initial |
| ... | | | | | |

**🔴 CRITICAL:** If you see 75%, 50%, or 25% scales, the V5.3 binary governor is NOT active.

### 5B. Governor Configuration Check
**Search config flags in logs:**

| Flag | V5.3 Expected | Actual |
|------|---------------|--------|
| `DRAWDOWN_GOVERNOR_ENABLED` | `False` (disabled for testing) | |
| `GOVERNOR_REGIME_OVERRIDE_ENABLED` | `False` (removed) | |
| `GOVERNOR_HWM_RESET_ENABLED` | `False` (removed) | |
| `GOVERNOR_REGIME_GUARD_ENABLED` | `True` | |
| `DRAWDOWN_GOVERNOR_LEVELS` | `{0.15: 0.0}` only | |

### 5C. Governor Recovery Analysis (if enabled)
For recovery from 0% → 100%:

| Requirement | Threshold | Met? |
|-------------|-----------|------|
| DD below recovery | < 12% | |
| Regime guard | Score >= 60 for 5 days | |
| Equity recovery | +5% from trough | |
| Days at 0% | >= 7 days | |

**Search:** `RECOVERY_PENDING`, `RECOVERY_BLOCKED`, `STEP_UP`

### 5D. Death Spiral Detection
**Pattern:** Stuck at 0% with no recovery path

| Metric | Threshold | Actual | Status |
|--------|-----------|--------|--------|
| Consecutive days at 0% | >30 = CRITICAL | | |
| Trades while at 0% | Only PUTs allowed | | |
| Recovery attempts | Should see attempts | | |

---

## STEP 6: Options Engine Deep Dive (V5.3)

### 6A. Strategy Selection Validation
**V5.3 changed to ALL DEBIT spreads (no credit spreads in any IV environment)**

| IV Environment | V5.3 Expected Strategy | Actual | Correct? |
|----------------|------------------------|--------|----------|
| LOW (<15) | BULL_CALL_DEBIT or BEAR_PUT_DEBIT | | |
| MEDIUM (15-25) | BULL_CALL_DEBIT or BEAR_PUT_DEBIT | | |
| HIGH (>25) | BULL_CALL_DEBIT or BEAR_PUT_DEBIT | | |

**🔴 CRITICAL:** Search for `BULL_PUT_CREDIT` or `BEAR_CALL_CREDIT` — should be ZERO in V5.3.

### 6B. Position Limit Validation
**V5.3 limits: Max 1 intraday + 2 swing = 3 total**

| Metric | Limit | Max Observed | Violations |
|--------|-------|--------------|------------|
| Intraday positions | 1 | | |
| Swing positions | 2 | | |
| Total options | 3 | | |

**Search:** `OPTIONS.*position`, `MAX.*POSITIONS`, `limit`

### 6C. Direction Selection by Regime
**V5.3 Direction Logic:**

| Regime Score | Expected Direction | Check |
|--------------|-------------------|-------|
| > 70 (RISK_ON) | CALL only | |
| 60-70 (UPPER_NEUTRAL) | CALL at 50% | |
| 50-59 (LOWER_NEUTRAL) | PUT at 50% | |
| 40-49 (CAUTIOUS) | PUT only | |
| < 40 (DEFENSIVE) | PUT only | |

**With Governor at 0%:** Only PUT spreads allowed regardless of regime.

### 6D. VASS Rejection Analysis
**Search:** `VASS_REJECTION`

| Rejection Reason | Count | Action Needed |
|------------------|-------|---------------|
| No contracts met spread criteria | | Relax DTE/delta/credit |
| Insufficient margin | | Check margin utilization |
| Position limit reached | | Expected behavior |
| Governor blocked | | Expected if at 0% |

### 6E. Neutrality Exit Check
**V5.3: Neutrality exit is DISABLED**

**Search:** `NEUTRALITY_EXIT`
- Expected count: **0**
- If >0, config flag `SPREAD_NEUTRALITY_EXIT_ENABLED` is incorrectly True

---

## STEP 7: Regime-Trade Attribution

### 7A. Trades Per Regime Summary
Count all trades (entries) by the regime at time of entry:

| Regime at Entry | Total Trades | Win | Loss | Win Rate | Total P&L |
|-----------------|--------------|-----|------|----------|-----------|
| RISK_ON (70+) | | | | | |
| UPPER_NEUTRAL (60-69) | | | | | |
| LOWER_NEUTRAL (50-59) | | | | | |
| CAUTIOUS (40-49) | | | | | |
| DEFENSIVE (30-39) | | | | | |
| RISK_OFF (<30) | | | | | |
| **TOTAL** | | | | | |

### 7B. Options Trades by Direction and Regime

| Regime | CALL Entries | PUT Entries | CALL P&L | PUT P&L |
|--------|--------------|-------------|----------|---------|
| RISK_ON | | | | |
| UPPER_NEUTRAL | | | | |
| LOWER_NEUTRAL | | | | |
| CAUTIOUS | | | | |
| DEFENSIVE | | | | |

**🔴 FLAG:** CALL entries in CAUTIOUS or DEFENSIVE = Navigation failure

### 7C. Conviction-Driven Trades
List trades where conviction engine overrode macro:

| Date | Trade | Macro Said | Engine Said | Conviction | Outcome |
|------|-------|------------|-------------|------------|---------|
| | | | | | |

---

## STEP 8: Engine-by-Engine Breakdown

### 8A. Trend Engine (QLD/SSO/TNA/FAS)
- Search: `TREND_ENTRY`, `TREND_EXIT`, `FILL.*QLD|SSO|TNA|FAS`
- Count: entries, exits, win/loss, avg hold period
- Check: ADX >= 15 at entry (V2.3.12 threshold)
- Check: MAX_CONCURRENT_TREND_POSITIONS = 4 (V5.3)

### 8B. Mean Reversion Engine (TQQQ/SOXL)
- Search: `MR_ENTRY`, `MR_EXIT`, `FILL.*TQQQ|SOXL`
- Check: All MR positions closed by 15:45 (NO overnight holds)
- Check: Entry conditions (RSI < 25, VIX filter)

### 8C. Hedge Engine (TMF/PSQ)
- Search: `HEDGE`, `FILL.*TMF|PSQ`
- Check: Hedges only active when regime < 50

### 8D. Yield Sleeve (SHV)
- Search: `YIELD`, `FILL.*SHV`
- Check: Lockbox protected

---

## STEP 9: Risk & Safeguard Verification

### 9A. Kill Switch
- Search: `KILL_SWITCH`, `KS_TIER`
- V5.3 threshold: 5% daily loss triggers kill switch

### 9B. Margin Utilization Gate (V5.3 NEW)
**Search:** `MARGIN_UTILIZATION`, `margin.*used`

| Metric | Threshold | Actual |
|--------|-----------|--------|
| Max margin utilization | 70% | |
| Warning threshold | 60% | |
| Trades blocked by margin | Count | |

### 9C. Other Safeguards
- `PANIC_MODE` — SPY -4% triggers
- `WEEKLY_BREAKER` — -5% WTD triggers
- `GAP_FILTER` — SPY -1.5% gap blocks
- `VOL_SHOCK` — 3× ATR pauses
- `TIME_GUARD` — entries blocked 13:55-14:10

---

## STEP 10: Funnel Analysis (Signal Loss)

```
Stage 1: Trading days available         → {N} days
Stage 2: Regime allowed trading         → {N} days (blocked by regime: X)
Stage 3: Governor allowed trading       → {N} days (blocked by Governor: X)
Stage 4: Conviction signals generated   → {N} signals (NEW V5.3)
Stage 5: Entry signals generated        → {N} signals
Stage 6: Signals passed filters         → {N} signals (blocked: margin, position limit)
Stage 7: Orders submitted               → {N} orders
Stage 8: Orders filled                  → {N} fills
```

**Identify biggest leakage point.**

---

## STEP 11: Smoke Signals (Critical Failure Flags)

| Severity | Pattern | Expected | V5.3 Notes |
|----------|---------|----------|------------|
| CRITICAL | `ERROR` or `EXCEPTION` | 0 | |
| CRITICAL | `MARGIN_ERROR` | 0 | Check margin utilization gate |
| CRITICAL | `NEUTRALITY_EXIT` | 0 | Should be disabled |
| CRITICAL | `CREDIT.*spread` entries | 0 | All DEBIT in V5.3 |
| CRITICAL | Governor scale 75%/50%/25% | 0 | Binary only |
| WARN | `conviction.*None` | 0 | Conviction should resolve |
| WARN | `VASS.*5d.*None` | 0 | Daily tracking broken |
| INFO | `VETO:` | Count | Conviction overrides |

---

## STEP 12: V5.3 Specific Validations

### 12A. Config Consistency Check
Verify these V5.3 config values are active:

| Config Key | V5.3 Value | Check |
|------------|------------|-------|
| `INITIAL_CAPITAL` | 75000 | |
| `DRAWDOWN_GOVERNOR_ENABLED` | False | |
| `SPREAD_NEUTRALITY_EXIT_ENABLED` | False | |
| `V4_1_VIX_LEVEL_ENABLED` | True | |
| `V4_REGIME_ENABLED` | True | |
| `OPTIONS_MAX_TOTAL_POSITIONS` | 3 | |
| `OPTIONS_MAX_INTRADAY_POSITIONS` | 1 | |
| `OPTIONS_MAX_SWING_POSITIONS` | 2 | |
| `MAX_CONCURRENT_TREND_POSITIONS` | 4 | |

### 12B. IVSensor Daily Update Check
**🔴 CRITICAL:** Verify IVSensor receives date parameter for daily VIX tracking.

**Search:** `VASS.*daily.*history`, `vix_daily_history`
- If daily history length stays at 0, the update is broken
- Expected: History grows by 1 each trading day

---

## STEP 13: Optimization Recommendations

### P0 — CRITICAL (Must fix before live)
- Conviction engine not firing (VASS/Micro silent during volatility)
- Daily VIX tracking not populating
- Wrong strategy type selected (CREDIT instead of DEBIT)
- Position limits exceeded
- Governor showing intermediate states (not binary)

### P1 — HIGH (Major performance leakage)
- Conviction thresholds too tight/loose (no overrides or too many)
- Regime detection latency >5 days
- Wrong direction trades (CALLs in bearish regime)
- Options blocked for >30 consecutive days

### P2 — MEDIUM (Optimization opportunities)
- Conviction threshold tuning
- VIX timeframe adjustments (5d/20d)
- Position limit adjustments
- Spread DTE optimization

### P3 — LOW (Minor improvements)
- Logging enhancements
- Parameter fine-tuning

---

## STEP 14: Scorecard

| System | Score | Status | Key Finding |
|--------|:-----:|--------|-------------|
| **Regime Identification (V4.1)** | /5 | | VIX Level factor working? |
| **Regime Navigation** | /5 | | Correct trades per regime? |
| **VASS Conviction Engine** | /5 | | Daily tracking + conviction firing? |
| **Micro Conviction Engine** | /5 | | UVXY + state-based conviction? |
| **Binary Governor** | /5 | | 100%/0% only, recovery works? |
| **Options Engine** | /5 | | All DEBIT, position limits? |
| Trend Engine | /5 | | |
| MR Engine | /5 | | |
| Hedge Engine | /5 | | |
| Kill Switch | /5 | | |
| Margin Utilization Gate | /5 | | |
| **Overall** | /5 | | |

---

## OUTPUT FORMAT
Save your complete audit report to:
`docs/audits/{VERSION}_{BACKTEST_NAME}_audit.md`

---

## QUICK REFERENCE: V5.3 Regime Thresholds

| Regime State | Score Range | Allowed Actions |
|--------------|-------------|-----------------|
| RISK_ON | >= 70 | All engines, CALL spreads only |
| UPPER_NEUTRAL | 60-69 | CALL spreads @ 50%, no PUTs |
| LOWER_NEUTRAL | 50-59 | PUT spreads @ 50%, no CALLs |
| CAUTIOUS | 40-49 | PUT spreads only, Hedges active |
| DEFENSIVE | 30-39 | PUT spreads + Hedges only |
| RISK_OFF | < 30 | Hedges only, crisis mode |

## QUICK REFERENCE: V5.3 Binary Governor

| Drawdown from HWM | Governor Scale | Effect |
|-------------------|----------------|--------|
| 0-15% | 100% | Full trading |
| >= 15% | 0% | Defensive only (PUTs/Hedges) |

**Recovery from 0% requires:**
1. DD falls below 12%
2. Regime >= 60 for 5 consecutive days
3. Equity +5% from trough
4. At least 7 days at 0%

## QUICK REFERENCE: V5.3 Conviction Thresholds

| Engine | Signal | Threshold | Conviction |
|--------|--------|-----------|------------|
| VASS | VIX 5d change | > +20% | BEARISH |
| VASS | VIX 5d change | < -15% | BULLISH |
| VASS | VIX 20d change | > +30% | STRONG BEARISH |
| VASS | VIX 20d change | < -20% | STRONG BULLISH |
| VASS | VIX crosses | > 25 | BEARISH |
| VASS | VIX crosses | < 15 | BULLISH |
| Micro | UVXY intraday | > +8% | BEARISH |
| Micro | UVXY intraday | < -5% | BULLISH |
| Micro | VIX level | > 35 | BEARISH (crisis) |
| Micro | VIX level | < 12 | BULLISH (complacent) |

## QUICK REFERENCE: V5.3 Options Strategy Matrix

| IV Environment | Direction | Strategy (V5.3) | DTE |
|----------------|-----------|-----------------|-----|
| LOW (<15) | BULLISH | BULL_CALL_DEBIT | 30-45 |
| LOW (<15) | BEARISH | BEAR_PUT_DEBIT | 30-45 |
| MEDIUM (15-25) | BULLISH | BULL_CALL_DEBIT | 7-21 |
| MEDIUM (15-25) | BEARISH | BEAR_PUT_DEBIT | 7-21 |
| HIGH (>25) | BULLISH | BULL_CALL_DEBIT | 7-14 |
| HIGH (>25) | BEARISH | BEAR_PUT_DEBIT | 7-14 |

**Note:** V5.3 uses ALL DEBIT spreads for gamma capture. No credit spreads.
