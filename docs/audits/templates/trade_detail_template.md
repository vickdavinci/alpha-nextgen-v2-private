# {RUN_NAME} TRADE DETAIL REPORT
# Template Version: V12.x (ObjectStore-enriched)
# Use this template when ALL 5 ObjectStore artifacts are [READY]

**Generated:** {DATE}
**Source authority:** ObjectStore crosscheck (primary), trades.csv (P&L), orders.csv (tags)

---

## Data Validation

- [x] ObjectStore crosscheck file: {CROSSCHECK_FILENAME} (REQUIRED)
- [x] Crosscheck artifacts: regime_decisions=[READY] | regime_timeline=[READY] | signal_lifecycle=[READY] | router_rejections=[READY] | order_lifecycle=[READY]
- [x] trades.csv parsed: {N_TRADES} rows
- [x] orders.csv parsed: {N_ORDERS} rows
- [x] signal_lifecycle.csv parsed: {N_SIGNALS} rows
- [x] regime_timeline.csv parsed: {N_REGIME} rows
- [x] router_rejections.csv parsed: {N_REJECTIONS} rows
- [x] order_lifecycle.csv parsed: {N_ORDER_LIFECYCLE} rows
- [x] VASS trades identified: {N_VASS_ROWS} rows = {N_VASS_PAIRS} spread pairs
- [x] ITM trades identified: {N_ITM}
- [x] MICRO trades identified: {N_MICRO}
- [x] Unclassified trades: {N_UNCLASSIFIED}
- [x] Row reconciliation: ({N_VASS_PAIRS} x 2) + {N_ITM} + {N_MICRO} + {N_UNCLASSIFIED} = {TOTAL} == trades.csv {N_TRADES} [PASS/FAIL]
- [x] VASS context filled: {N_VASS_FILLED}/{N_VASS_PAIRS} ({PCT_VASS_FILLED}%)
- [x] MICRO context filled: {N_MICRO_FILLED}/{N_MICRO} ({PCT_MICRO_FILLED}%)
- [x] P&L reconciliation: CSV total ${GROSS_PNL} vs report total ${REPORT_PNL} [PASS/FAIL]
- [x] Discrepancies found: {DISCREPANCIES}

---

## Executive Summary

| Metric | Value |
|--------|-------|
| Backtest | {RUN_NAME} |
| Period | {START_DATE} to {END_DATE} |
| Net Profit | ${NET_PROFIT} ({NET_PROFIT_PCT}%) |
| Gross P&L | ${GROSS_PNL} |
| Total Fees | ${TOTAL_FEES} |
| Win Rate | {WIN_RATE}% ({N_WINS}W / {N_LOSSES}L) |
| Sharpe Ratio | {SHARPE} |
| Max Drawdown | {MAX_DD}% |
| Total Orders | {N_ORDERS} |
| Trades (rows) | {N_TRADES} |
| Best Month | {BEST_MONTH} (${BEST_MONTH_PNL}) |
| Worst Month | {WORST_MONTH} (${WORST_MONTH_PNL}) |

---

## Signal Funnel Summary (from signal_lifecycle.csv)

This section is ONLY available when signal_lifecycle.csv is [READY].

| Stage | VASS | ITM | MICRO | Total |
|-------|------|-----|-------|-------|
| CANDIDATE | {VASS_CANDIDATES} | {ITM_CANDIDATES} | {MICRO_CANDIDATES} | {TOTAL_CANDIDATES} |
| APPROVED | {VASS_APPROVED} | {ITM_APPROVED} | {MICRO_APPROVED} | {TOTAL_APPROVED} |
| FILLED | {VASS_FILLED} | {ITM_FILLED} | {MICRO_FILLED} | {TOTAL_FILLED} |
| DROPPED (gate) | {VASS_DROPPED} | {ITM_DROPPED} | {MICRO_DROPPED} | {TOTAL_DROPPED} |
| DROP RATE | {VASS_DROP_PCT}% | {ITM_DROP_PCT}% | {MICRO_DROP_PCT}% | {TOTAL_DROP_PCT}% |

**Top drop reasons:**
| Engine | Gate | Reason | Count |
|--------|------|--------|-------|
| VASS | {GATE} | {REASON} | {COUNT} |
| ITM | {GATE} | {REASON} | {COUNT} |
| MICRO | {GATE} | {REASON} | {COUNT} |

---

## Router Rejection Summary (from router_rejections.csv)

This section is ONLY available when router_rejections.csv is [READY].

| Engine | Stage | Code | Count | Sample Symbol |
|--------|-------|------|-------|--------------|
| VASS | {STAGE} | {CODE} | {COUNT} | {SYMBOL} |
| ITM | {STAGE} | {CODE} | {COUNT} | {SYMBOL} |
| MICRO | {STAGE} | {CODE} | {COUNT} | {SYMBOL} |

---

## Part 1: VASS Spread Trades (ObjectStore-enriched)

When signal_lifecycle.csv and regime_timeline.csv are available, each spread row includes:
- Regime score and band AT ENTRY (from regime_timeline.csv nearest timestamp)
- VIX level and direction AT ENTRY (from regime_timeline.csv)
- Micro regime name AT ENTRY (from regime_timeline.csv micro_regime_name column)
- Signal ID that generated the spread (from signal_lifecycle.csv)
- VASS score from the signal (from signal_lifecycle.csv metadata)

| # | Entry | Exit | Type | Regime | Band | VIX | VIX Dir | DTE | Debit | Width | D/W% | VASS Score | Exit Code | Hold | Net P&L | P&L% | W/L |
|---|-------|------|------|--------|------|-----|---------|-----|-------|-------|------|-----------|----------|------|---------|-------|-----|
| V1 | YYYY-MM-DD | YYYY-MM-DD | BULL_CALL_DEBIT | 62 | NEUTRAL | 18.4 | STABLE | 13 | $2.79 | $7 | 39.9% | 2.33 | VASS_TAIL_RISK_CAP | 4.0d | -$X | -28% | L |
| V2 | ... | ... | ... | ... | ... | ... | ... | ... | ... | ... | ... | ... | ... | ... | ... | ... | ... |

**Fill in all spread rows from enriched_trades.csv output of parse_objectstore_trades.py**

---

### 1a. VASS Summary

**Summary:** {N_VASS_PAIRS} spreads | {N_VASS_WINS}W-{N_VASS_LOSSES}L | WR={VASS_WR}% | Net P&L=${VASS_NET_PNL}
- Avg Win: ${VASS_AVG_WIN} | Avg Loss: ${VASS_AVG_LOSS} | Profit Factor: {VASS_PF}
- Expected Value: ({VASS_WR}% x ${VASS_AVG_WIN}) - ({VASS_LR}% x ${VASS_AVG_LOSS}) = ${VASS_EV} per trade

---

### 1b. VASS Exit Reason Distribution

| Exit Code | Count | WR% | Total P&L | Avg P&L |
|-----------|-------|-----|-----------|---------|
| VASS_TAIL_RISK_CAP | {N} | {WR}% | ${PNL} | ${AVG} |
| SPREAD_RETRY_MAX | {N} | {WR}% | ${PNL} | ${AVG} |
| VASS_CONVICTION_FLOOR | {N} | {WR}% | ${PNL} | ${AVG} |
| TRANSITION_DERISK_DETERIORATION | {N} | {WR}% | ${PNL} | ${AVG} |
| FRIDAY_FIREWALL | {N} | {WR}% | ${PNL} | ${AVG} |
| SPREAD_CLOSE_ESCALATED | {N} | {WR}% | ${PNL} | ${AVG} |
| VASS_REGIME_BREAK_BEAR | {N} | {WR}% | ${PNL} | ${AVG} |
| EXPIRATION_HAMMER_V2 | {N} | {WR}% | ${PNL} | ${AVG} |

---

### 1c. VASS D/W% Analysis

| D/W% Range | Trades | WR% | Avg P&L | Total P&L |
|-----------|--------|-----|---------|-----------|
| 30-35% | {N} | {WR}% | ${AVG} | ${TOT} |
| 35-40% | {N} | {WR}% | ${AVG} | ${TOT} |
| 40-45% | {N} | {WR}% | ${AVG} | ${TOT} |
| 45-50% | {N} | {WR}% | ${AVG} | ${TOT} |
| 50%+ | {N} | {WR}% | ${AVG} | ${TOT} |

---

### 1d. VASS Regime Band Distribution (requires regime_timeline.csv)

| Regime Band | Trades | WR% | Total P&L | Avg P&L |
|------------|--------|-----|-----------|---------|
| RISK_ON (>=70) | {N} | {WR}% | ${PNL} | ${AVG} |
| NEUTRAL (50-69) | {N} | {WR}% | ${PNL} | ${AVG} |
| CAUTIOUS (45-49) | {N} | {WR}% | ${PNL} | ${AVG} |
| DEFENSIVE (35-44) | {N} | {WR}% | ${PNL} | ${AVG} |
| RISK_OFF (<35) | {N} | {WR}% | ${PNL} | ${AVG} |

---

### 1e. VASS Monthly Breakdown

| Month | Spreads | WR% | Net P&L |
|-------|---------|-----|---------|
| {YYYY-MM} | {N} | {WR}% | ${PNL} |

---

### 1f. Top 10 Worst VASS Trades

For each trade: date, spread type, regime score, VIX, DTE, D/W%, exit code, hold, P&L, and what went wrong.

| # | Date | Type | Regime | VIX | DTE | D/W% | Exit | Hold | P&L | Root Cause |
|---|------|------|--------|-----|-----|------|------|------|-----|-----------|
| 1 | {DATE} | {TYPE} | {SCORE}/{BAND} | {VIX} | {DTE} | {DW}% | {EXIT} | {HOLD} | ${PNL} | {CAUSE} |

---

## Part 2: ITM Momentum Trades (ObjectStore-enriched)

When regime_timeline.csv is available, each ITM row includes:
- Regime score and band at entry
- VIX level at entry
- Micro regime name at entry (relevant because ITM also uses directional context)
- Hold duration in hours (exact from timestamps)

| # | Date | Entry | Exit | Dir | Entry $ | Exit $ | Qty | Regime | Band | VIX | Hold | Exit Type | P&L | W/L |
|---|------|-------|------|-----|---------|--------|-----|--------|------|-----|------|----------|-----|-----|
| T1 | {DATE} | {TIME} | {TIME} | CALL | {$} | {$} | {N} | {SCORE} | {BAND} | {VIX} | {HOLD} | {EXIT} | ${PNL} | W/L |

---

### 2a. ITM Summary

**Summary:** {N_ITM} trades | {N_ITM_WINS}W-{N_ITM_LOSSES}L | WR={ITM_WR}% | Net P&L=${ITM_NET_PNL}
- Avg Win: ${ITM_AVG_WIN} | Avg Loss: ${ITM_AVG_LOSS} | Profit Factor: {ITM_PF}

---

### 2b. ITM Exit Type Distribution

| Exit Type | Count | WR% | Total P&L | Avg P&L |
|-----------|-------|-----|-----------|---------|
| OCO_STOP | {N} | {WR}% | ${PNL} | ${AVG} |
| OCO_PROFIT | {N} | {WR}% | ${PNL} | ${AVG} |
| ITM:RISK_EXIT | {N} | {WR}% | ${PNL} | ${AVG} |
| ITM:UNCLASSIFIED | {N} | {WR}% | ${PNL} | ${AVG} |

---

### 2c. ITM by Direction

| Direction | Trades | WR% | Total P&L | Avg P&L |
|-----------|--------|-----|-----------|---------|
| CALL | {N} | {WR}% | ${PNL} | ${AVG} |
| PUT | {N} | {WR}% | ${PNL} | ${AVG} |

---

## Part 3: MICRO Intraday Trades (ObjectStore-enriched)

When signal_lifecycle.csv and regime_timeline.csv are available, each MICRO row includes:
- Micro regime name at entry (from regime_timeline.csv micro_regime_name column)
  This is the 21-regime classification: VIX Level x VIX Direction
- VIX level and direction at entry
- Regime score at entry

### Full MICRO Trade Table

| # | Date | Entry | Exit | Strategy | Dir | Micro Regime | VIX | VIX Dir | Hold | Exit Type | P&L | W/L | Notes |
|---|------|-------|------|----------|-----|-------------|-----|---------|------|----------|-----|-----|-------|
| M1 | {DATE} | {TIME} | {TIME} | PROTECTIVE_PUTS | PUT | WORSENING | 21.5 | RISING | 2m | MICRO:RISK_EXIT | ${PNL} | W/L | |

**Notes column:**
- ORPHAN: entry after 15:25 ET or PREMARKET_STALE exit
- QUICK_STOP: held < 5 minutes and stopped
- NEXT_DAY: held overnight (leaked past force close)
- CONSEC_STOP_N: Nth consecutive stop on same day (potential re-entry churn)

---

### 3a. MICRO Summary

**Summary:** {N_MICRO} trades | {N_MICRO_WINS}W-{N_MICRO_LOSSES}L | WR={MICRO_WR}% | Net P&L=${MICRO_NET_PNL}
- Avg Win: ${MICRO_AVG_WIN} | Avg Loss: ${MICRO_AVG_LOSS} | Profit Factor: {MICRO_PF}

---

### 3b. MICRO by Strategy

| Strategy | Count | WR% | Total P&L | Avg P&L |
|----------|-------|-----|-----------|---------|
| PROTECTIVE_PUTS | {N} | {WR}% | ${PNL} | ${AVG} |
| MICRO_OTM_MOMENTUM | {N} | {WR}% | ${PNL} | ${AVG} |

---

### 3c. MICRO by Micro Regime (MOST IMPORTANT TABLE)

Requires regime_timeline.csv with micro_regime_name column.
Sort by Total P&L ascending (worst first).

| Micro Regime | VIX Level | VIX Dir | Trades | Wins | WR% | Total P&L | Avg P&L | Verdict |
|-------------|-----------|---------|--------|------|-----|-----------|---------|---------|
| WORSENING | MEDIUM | RISING | {N} | {W} | {WR}% | ${PNL} | ${AVG} | TOXIC/MARGINAL/OK/PROFITABLE |
| CAUTION_LOW | LOW | RISING | {N} | {W} | {WR}% | ${PNL} | ${AVG} | TOXIC/MARGINAL/OK/PROFITABLE |
| DETERIORATE | MEDIUM | RISING_FAST | {N} | {W} | {WR}% | ${PNL} | ${AVG} | TOXIC/MARGINAL/OK/PROFITABLE |
| GOOD_MR | LOW | FALLING | {N} | {W} | {WR}% | ${PNL} | ${AVG} | TOXIC/MARGINAL/OK/PROFITABLE |
| NORMAL | LOW | STABLE | {N} | {W} | {WR}% | ${PNL} | ${AVG} | TOXIC/MARGINAL/OK/PROFITABLE |
| CAUTIOUS | MEDIUM | STABLE | {N} | {W} | {WR}% | ${PNL} | ${AVG} | TOXIC/MARGINAL/OK/PROFITABLE |
| IMPROVING | MEDIUM | FALLING | {N} | {W} | {WR}% | ${PNL} | ${AVG} | TOXIC/MARGINAL/OK/PROFITABLE |
| RECOVERING | MEDIUM | FALLING_FAST | {N} | {W} | {WR}% | ${PNL} | ${AVG} | TOXIC/MARGINAL/OK/PROFITABLE |
| ... | ... | ... | ... | ... | ... | ... | ... | ... |

**Verdict rules:**
- WR < 30% = TOXIC (block this regime)
- WR 30-45% = MARGINAL (reduce sizing)
- WR 45-55% = OK (normal)
- WR > 55% = PROFITABLE (increase sizing)

---

### 3d. MICRO by Direction

| Direction | Trades | WR% | Total P&L | Avg P&L |
|-----------|--------|-----|-----------|---------|
| CALL | {N} | {WR}% | ${PNL} | ${AVG} |
| PUT | {N} | {WR}% | ${PNL} | ${AVG} |

---

### 3e. MICRO Exit Reason Distribution

| Exit Type | Count | WR% | Total P&L | Avg P&L |
|-----------|-------|-----|-----------|---------|
| MICRO:RISK_EXIT | {N} | {WR}% | ${PNL} | ${AVG} |
| OCO_PROFIT | {N} | {WR}% | ${PNL} | ${AVG} |
| OCO_STOP | {N} | {WR}% | ${PNL} | ${AVG} |
| MICRO:PROTECTIVE_PUTS (force close) | {N} | {WR}% | ${PNL} | ${AVG} |
| INTRADAY_FORCE_EXIT | {N} | {WR}% | ${PNL} | ${AVG} |
| PREMARKET_STALE | {N} | {WR}% | ${PNL} | ${AVG} |

---

### 3f. Orphan Analysis

List ALL trades flagged as ORPHAN.

| Date | Entry Time | Exit Time | Exit Type | P&L | Issue |
|------|-----------|----------|-----------|-----|-------|
| {DATE} | {TIME} | {TIME} | {TYPE} | ${PNL} | {DESCRIPTION} |

---

### 3g. MICRO Regime x Direction Heatmap (requires regime_timeline.csv)

| Micro Regime | CALL WR% | CALL P&L | PUT WR% | PUT P&L | Best Dir |
|-------------|----------|----------|---------|---------|----------|
| WORSENING | {WR}% | ${PNL} | {WR}% | ${PNL} | PUT/CALL/NEITHER |
| GOOD_MR | {WR}% | ${PNL} | {WR}% | ${PNL} | PUT/CALL/NEITHER |
| NORMAL | {WR}% | ${PNL} | {WR}% | ${PNL} | PUT/CALL/NEITHER |
| ... | ... | ... | ... | ... | ... |

---

### 3h. Top 10 Worst MICRO Trades

| # | Date | Strategy | Dir | Micro Regime | VIX | Exit | Hold | P&L | Root Cause |
|---|------|----------|-----|-------------|-----|------|------|-----|-----------|
| 1 | {DATE} | PROTECTIVE_PUTS | PUT | WORSENING | {VIX} | OCO_STOP | {HOLD} | ${PNL} | {CAUSE} |

---

## Part 4: Combined Root Cause Analysis

### 4a. Loss Concentration

```
Top 20 worst trades: ${TOP20_LOSSES} ({TOP20_PCT}% of total losses)
Top 10 worst trades: ${TOP10_LOSSES} ({TOP10_PCT}% of total losses)
Pattern verdict: TAIL-DOMINATED / DISTRIBUTED / MIXED
```

---

### 4b. Failure Mode Ranking (by total $ impact)

| Rank | Failure Mode | Trades | Total Loss | % of All Losses |
|------|-------------|--------|------------|----------------|
| 1 | {MODE} | {N} | ${LOSS} | {PCT}% |
| 2 | {MODE} | {N} | ${LOSS} | {PCT}% |
| 3 | {MODE} | {N} | ${LOSS} | {PCT}% |
| 4 | {MODE} | {N} | ${LOSS} | {PCT}% |
| 5 | {MODE} | {N} | ${LOSS} | {PCT}% |

---

### 4c. Regime Gate Simulation (requires regime_timeline.csv)

For each MICRO regime with WR < 35%, calculate savings from blocking:

| Blocked Regime | Trades Blocked | P&L Avoided | WR of Blocked | Action |
|---------------|---------------|-------------|---------------|--------|
| {REGIME} | {N} | ${PNL} | {WR}% | BLOCK / REDUCE_SIZE |
| {REGIME} | {N} | ${PNL} | {WR}% | BLOCK / REDUCE_SIZE |
| TOTAL | {N} | ${PNL} | — | — |

---

### 4d. Min-Hold Impact (VASS)

Count VASS trades where:
- Exit code = TAIL_RISK_CAP or CONVICTION_FLOOR
- Hold < 1 day (min-hold was active)
- Exit forced before adaptive stop could recover

| Trade | Hold | Exit Code | P&L | Min-Hold Active? | Assessment |
|-------|------|----------|-----|-----------------|-----------|
| V{N} | {HOLD} | {CODE} | ${PNL} | YES/NO | HOLD_GUARD_BYPASSED / FORCED_DURING_HOLD |

---

### 4e. Top 5 Actionable Fixes (Ranked by $ Impact)

Every $ estimate MUST cite specific trade rows from the tables above.

| Rank | Fix | $ Saved (sourced) | Evidence (trade rows) |
|------|-----|------------------|----------------------|
| 1 | {FIX_DESCRIPTION} | ${AMOUNT} (sum of V{n},V{n},...) | {N} trades, {WR}% WR, evidence: ... |
| 2 | {FIX_DESCRIPTION} | ${AMOUNT} (sum of M{n},M{n},...) | {N} trades, {WR}% WR, evidence: ... |
| 3 | {FIX_DESCRIPTION} | ${AMOUNT} (exact / hypothesis) | {N} trades, evidence: ... |
| 4 | {FIX_DESCRIPTION} | ${AMOUNT} (exact / hypothesis) | {N} trades, evidence: ... |
| 5 | {FIX_DESCRIPTION} | ${AMOUNT} (exact / hypothesis) | {N} trades, evidence: ... |

---

## Part 5: Monthly P&L Breakdown

| Month | VASS P&L | ITM P&L | MICRO P&L | Edge/Orphan | Total Gross |
|-------|---------|---------|----------|------------|-------------|
| {YYYY-MM} | ${PNL} | ${PNL} | ${PNL} | ${PNL} | ${TOT} |

---

## Part 6: Signal Funnel Detail (requires signal_lifecycle.csv)

This section is only populated when signal_lifecycle.csv is [READY].

### 6a. VASS Signal Funnel

```
Total VASS candidates: {N}
Passed all gates: {N} ({PCT}%)
Router rejected: {N} ({PCT}%)
Filled: {N}
```

### 6b. MICRO Signal Funnel

```
Total MICRO candidates: {N}
Passed all gates: {N} ({PCT}%)
Router rejected: {N} ({PCT}%)
Filled: {N}
```

### 6c. Most Common Drop Gates

| Engine | Gate Name | Drop Count | % of All Drops |
|--------|----------|-----------|---------------|
| {ENGINE} | {GATE} | {N} | {PCT}% |

---

## Part 7: Row Count Reconciliation (MANDATORY)

| Engine | trades.csv rows | Report rows | Notes |
|--------|----------------|-------------|-------|
| VASS (spread pairs) | {N_VASS_ROWS} | {N_VASS_PAIRS} | Each pair = 2 CSV rows, 1 report row |
| ITM | {N_ITM} | {N_ITM} | 1:1 |
| MICRO | {N_MICRO} | {N_MICRO} | 1:1 |
| Unclassified/Edge | {N_EDGE} | {N_EDGE} | 1:1 |
| TOTAL | {N_TRADES} | — | Verify: ({N_VASS_PAIRS}x2) + {N_ITM} + {N_MICRO} + {N_EDGE} = {N_TRADES} |

**Reconciliation result:** [PASS / FAIL — if FAIL, list discrepancies here]

**P&L check:** Sum from trades.csv = ${GROSS_PNL} | Report total = ${REPORT_PNL} | [PASS / FAIL]

---

## 21-Regime Reference

Used for MICRO regime classification from regime_timeline.csv micro_regime_name column:

```
                    FALLING_FAST  FALLING   STABLE    RISING    RISING_FAST  SPIKING   WHIPSAW
VIX LOW (< 18)      PERFECT_MR    GOOD_MR   NORMAL    CAUTION   TRANSITION   RISK_OFF  CHOPPY
VIX MEDIUM (18-25)  RECOVERING    IMPROVING CAUTIOUS  WORSENING DETERIORATE  BREAKING  UNSTABLE
VIX HIGH (> 25)     PANIC_EASE    CALMING   ELEVATED  WORSE_HI  FULL_PANIC   CRASH     VOLATILE
```

Note: CAUTION_LOW may appear as variant for low-VIX RISING. Use exact string from data.

---

## Generating This Report

1. Verify ObjectStore crosscheck file exists: `ls <stage_dir>/*OBJECTSTORE_CROSSCHECK*`
2. Confirm all 5 artifacts show [READY]
3. Run enrichment script: `python scripts/parse_objectstore_trades.py --stage-dir <dir> --run-name <name>`
4. Open output enriched_trades.csv
5. Fill in all table rows from enriched data
6. Run row count reconciliation
7. Save report as `{RUN_NAME}_TRADE_DETAIL_REPORT.md` in the stage folder

If ObjectStore artifacts are missing, use the logs+CSV fallback path documented in
`docs/agents/trade-analyzer-agent.md` and mark the report as "REDUCED CONFIDENCE."

---
*Template end. Replace all {PLACEHOLDER} values with actual data before finalizing.*
