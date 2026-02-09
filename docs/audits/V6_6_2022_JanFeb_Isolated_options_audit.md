# V6.6 OPTIONS ENGINE AUDIT — ISOLATED BACKTEST
## Jan-Feb 2022 | Options-Only Mode

---

## 0) Backtest Metadata
| Field | Value |
|-------|-------|
| **Backtest Name** | V6_6_2022_JanFeb_Isolated |
| **Log Path** | `docs/audits/logs/stage6.5/V6_6_2022_JanFeb_Isolated_logs.txt` |
| **Orders CSV** | `docs/audits/logs/stage6.5/V6_6_2022_JanFeb_Isolated_orders.csv` |
| **Trades CSV** | `docs/audits/logs/stage6.5/V6_6_2022_JanFeb_Isolated_trades.csv` |
| **Date Range** | 2022-01-03 to 2022-02-28 |
| **Starting Capital** | $75,000 |
| **Market Context** | Choppy → Bear transition (QQQ -15% Jan-Feb) |
| **Version/Branch** | feature/va/v3.0-hardening |
| **Mode** | Options Engine ISOLATED (Trend/MR disabled) |

---

## 1) Pre-Read Checklist
- [x] `config.py` reviewed for all active thresholds
- [x] `CLAUDE.md` reviewed for architecture rules
- [x] Log file loaded (599KB)
- [x] Orders + trades CSV loaded (102 trades)

---

## 2) Performance Summary (Options-Only Focus)

| Metric | Value | Assessment |
|--------|-------|------------|
| **Net Return** | **-$35,085** (-46.8%) | CRITICAL |
| **Total Options Trades** | 102 | High volume |
| **Win Rate** | 21.6% (22/102) | CRITICAL: Below 25% threshold |
| **Avg Win** | +$1,143 | Reasonable |
| **Avg Loss** | -$594 | Losses smaller but frequent |
| **Profit Factor** | 0.71 | <1.0 = Losing system |
| **Max Drawdown** | ~$8,091 (single trade) | High single-trade risk |
| **Sharpe Ratio** | N/A | Not computed |

### Trade Classification Breakdown
| Category | Count | P&L | Avg P&L | Win Rate |
|----------|-------|-----|---------|----------|
| **Instant Closes (Same-Day)** | 34 | -$13,619 | -$401 | 0% |
| **Normal Trades** | 68 | -$21,466 | -$316 | 32.4% |
| **Spread Trades** | 17 | -$11,760 | -$692 | 0% |
| **Intraday Trades** | 85 | -$23,325 | -$274 | 25.9% |

### Monthly Performance
| Month | Trades | P&L | Win Rate | Worst Trade |
|-------|--------|-----|----------|-------------|
| January | 75 | -$31,721 | 17.3% | -$2,952 (220105C404) |
| February | 27 | -$3,364 | 33.3% | -$1,696 (220225P327) |

**Critical Observation:** February shows improvement (33% win rate vs 17%) after market stabilized. January's brutal selloff exposed systemic weaknesses.

---

## 3) Regime & Navigation

### 3A. Regime Distribution (from logs)
| Regime | Score Range | Observed | Notes |
|--------|-------------|----------|-------|
| RISK_ON | >= 70 | 0 days | Never reached |
| UPPER_NEUTRAL | 60-69 | ~30 days | Most of period |
| LOWER_NEUTRAL | 50-59 | ~10 days | Late Feb recovery |
| CAUTIOUS | 40-49 | 0 days | Skipped |
| DEFENSIVE | 30-39 | 0 days | Skipped |
| RISK_OFF | < 30 | 0 days | Skipped |

**Observation:** Regime stayed 60-68 throughout Jan-Feb despite QQQ -15% decline. Regime lagged market reality.

### 3B. Regime Accuracy
| Date Range | Market Reality | Expected Regime | Actual Regime | Match |
|------------|---------------|-----------------|---------------|-------|
| Jan 3-7 | Bull top, reversal | RISK_ON→NEUTRAL | 61-68 | Partial |
| Jan 10-21 | Sharp selloff | CAUTIOUS | 65-68 | **NO** |
| Jan 24-27 | Panic selling | DEFENSIVE | 65 | **NO** |
| Feb 1-10 | Relief rally | NEUTRAL | 65-67 | Yes |
| Feb 14-24 | Ukraine volatility | CAUTIOUS | 65 | **NO** |

**Root Cause:** Regime engine using 4-factor scoring didn't respond fast enough to VIX spike from 17→30+ in late January.

### 3C. Regime Transition Latency
| Event | Date | VIX | Regime Response | Lag |
|-------|------|-----|-----------------|-----|
| QQQ -4% day | Jan 18 | 23 | Score=65 | **No change** |
| QQQ -5% day | Jan 24 | 30 | Score=65 | **No change** |
| VIX >30 | Jan 24 | 32 | Score=65 | **5+ days** |

**Flag:** Regime NEVER dropped below 60 despite VIX 30+ and QQQ -15%.

---

## 4) Conviction Engine Validation

### 4A. VASS Conviction
| Metric | Expected | Actual | Status |
|--------|----------|--------|--------|
| VIX tracked daily | Yes | Yes | OK |
| IV Environment logged | Yes | MEDIUM (16-20) | OK |
| VIX spike detection | Yes | Partial | WARN |
| Strategy selection (DEBIT) | Correct | Yes | OK |

### 4B. Micro Conviction
| Metric | Expected | Actual | Status |
|--------|----------|--------|--------|
| UVXY intraday changes | Yes | +3% to +8% logged | OK |
| BEARISH conviction (UVXY >+3%) | Yes | Triggered | OK |
| BULLISH conviction (UVXY <-3%) | Yes | Not seen | N/A |
| VIX crisis trigger >35 | Yes | Not triggered | OK |
| "MICRO has no direction" | 0 | **158 occurrences** | **BUG** |

### 4C. Conviction Override (Veto) Behavior
| Date | Engine | Conviction | Macro | Final Direction | Correct |
|------|--------|------------|-------|-----------------|---------|
| Jan 4 10:56 | MICRO | UVXY +3% | BULLISH | PUT (VETO) | Yes |
| Jan 4 11:11 | MICRO | UVXY +5% | BULLISH | PUT (VETO) | Yes |
| Jan 5 14:25 | MICRO | UVXY +3% | BULLISH | PUT (VETO) | Yes |

**Veto system working correctly when triggered.**

---

## 5) Options Engine Core Validation

### 5A. Strategy Type
| Check | Expected | Actual | Status |
|-------|----------|--------|--------|
| DEBIT spreads only | Yes | All 17 spreads = BULL_CALL (debit) | OK |
| No credit spreads | 0 | 0 | OK |

### 5B. Position Limits
| Limit | Expected | Observed | Status |
|-------|----------|----------|--------|
| Max intraday | 1 | 1 | OK |
| Max swing (spreads) | 2 | 1 (instant closed) | OK |
| Max total | 3 | Never reached | N/A |

### 5C. Direction by Regime
| Regime Score | Expected Direction | Actual | Status |
|--------------|-------------------|--------|--------|
| 61-68 (UPPER_NEUTRAL) | CALL only | CALL only | OK |
| <60 | PUT eligible | Never reached | N/A |

### 5D. Spread Entry/Exit Integrity
| Check | Expected | Actual | Status |
|-------|----------|--------|--------|
| SPREAD: ENTRY_SIGNAL logged | Yes | 17 entries | OK |
| SPREAD: EXIT_SIGNAL with reason | Yes | All via ASSIGNMENT_RISK | **BUG** |
| Instant close at entry | No | **ALL 17 spreads** | **CRITICAL BUG** |
| Both legs closed atomically | Yes | Yes | OK |

**ROOT CAUSE IDENTIFIED:**
```
ASSIGNMENT_RISK_EXIT: MARGIN_BUFFER_INSUFFICIENT
Assignment exposure=$772,000 | Required buffer=$154,400 (20%)
Available margin=$74,164
```
- 20 contracts × $386 strike × 100 = $772,000 notional
- Required 20% buffer = $154,400
- Account has $74,164 available
- **Max safe size: ~9 contracts (not 20)**

### 5E. Intraday Entry/Exit Integrity
| Check | Expected | Actual | Status |
|-------|----------|--------|--------|
| INTRADAY_SIGNAL_APPROVED | >0 | 206 approvals | OK |
| Direction logged | Yes | CALL/PUT logged | OK |
| OCO stops set | Yes | 34 OCO orders | OK |
| Time window enforced | Yes | 10:15-20:30 | OK |
| Direction mismatch blocked | Yes | Not observed | N/A |

---

## 6) Strategy-to-Direction Consistency

| Strategy | Expected Direction | Actual | Mismatch? |
|----------|-------------------|--------|-----------|
| DEBIT (swing) | CALL in regime>60 | CALL | No |
| INTRADAY_MOMENTUM | Follows MICRO | CALL/PUT per MICRO | No |
| CONVICTION_VETO | Overrides Macro | PUT when UVXY>+3% | No |
| FOLLOW_MACRO | When MICRO neutral | CALL (regime>60) | No |

**No direction mismatches detected.**

---

## 7) Regime-Based Profit & Stop Behavior

### 7A. Spreads
| Metric | Expected | Actual | Status |
|--------|----------|--------|--------|
| Profit target (regime mult) | 1.0× at regime 65 | N/A (instant closed) | N/A |
| Stop loss 25% | Fixed | N/A (instant closed) | N/A |

### 7B. Single-Leg (Intraday)
| Metric | Expected | Actual | Status |
|--------|----------|--------|--------|
| OCO profit target | 50% | Triggered 13× | OK |
| OCO stop loss | 50% | Triggered 21× | OK |
| ATR-scaled stops | V6.5 feature | **NOT ACTIVE** | BUG |

**Missing:** No `STOP_CALC` logs found. Delta-scaled ATR stops not active in this backtest.

### 7C. OCO Performance
| Outcome | Count | % | P&L Impact |
|---------|-------|---|------------|
| Profit Target Hit | 13 | 38% | +$12,876 |
| Stop Loss Hit | 21 | 62% | -$18,341 |
| Expired/EOD Close | 19 | N/A | -$19,620 |

**Problem:** 62% stop rate is too high. Stops may be too tight or entries poorly timed.

---

## 8) Assignment & Margin Risk

### 8A. Assignment Risk Exit
| Date | Exposure | Buffer Req | Available | Gap |
|------|----------|------------|-----------|-----|
| Jan 3 | $772K | $154K | $74K | -$80K |
| Jan 4 | $794K | $159K | $74K | -$85K |
| Jan 5 | $784K | $157K | $67K | -$90K |
| Jan 6 | $758K | $152K | $68K | -$84K |
| ... | ... | ... | ... | ... |

**Pattern:** Every single spread entry triggered immediate ASSIGNMENT_RISK_EXIT.

### 8B. Margin Gate
| Metric | Expected | Actual | Status |
|--------|----------|--------|--------|
| Margin utilization ≤70% | Yes | ~100% at entry | FAIL |
| Pre-entry margin check | Yes | **NOT IMPLEMENTED** | **P0 BUG** |

---

## 9) Funnel Analysis (Signal Loss)

```
Stage 1: Trading days available              40 days
Stage 2: Intraday signals generated         364 signals
Stage 3: VASS rejections                    274 (75%) ← MAJOR LEAK
Stage 4: MICRO no direction blocks          158 (43%) ← MAJOR LEAK
Stage 5: Entry signals approved             206
Stage 6: OCO orders placed                   34
Stage 7: Spread entries                      17
Stage 8: Spreads instant-closed              17 (100%) ← CRITICAL BUG
```

### Biggest Leakage Points:
1. **VASS Rejections (274):** "No contracts met spread criteria (DTE/delta/credit)"
   - Spread construction too restrictive for volatile market
2. **MICRO No Direction (158):** Micro regime engine returning NONE
   - Missing VIX direction signal in choppy conditions
3. **Spread Instant Close (17/17):** Assignment risk buffer check failing
   - Position sizing ignores margin constraints

---

## 10) Smoke Signals (Critical Failure Flags)

| Severity | Pattern | Expected | Found | Status |
|----------|---------|----------|-------|--------|
| CRITICAL | ERROR/EXCEPTION | 0 | 0 | OK |
| CRITICAL | MARGIN_ERROR | 0 | 0 | OK |
| CRITICAL | CREDIT.*spread | 0 | 0 | OK |
| CRITICAL | ASSIGNMENT_RISK_EXIT instant | 0 | **17** | **FAIL** |
| WARN | MICRO.*no direction | 0 | **158** | **FAIL** |
| WARN | VASS_REJECTION | <50 | **274** | **FAIL** |
| INFO | OCO_TRIGGERED | - | 34 | OK |
| INFO | EXPIRATION | - | 53 | OK |

---

## 11) Options-Only Scorecard

| System | Score | Status | Key Finding |
|--------|:-----:|--------|-------------|
| Regime Identification | 2/5 | FAIL | Regime stayed 60+ despite VIX 30+ |
| Regime Navigation | 2/5 | FAIL | No regime-based direction adjustment |
| VASS Conviction | 3/5 | WARN | 274 rejections due to strict criteria |
| Micro Conviction | 2/5 | FAIL | 158 "no direction" signals |
| Options Engine (Spread) | 1/5 | CRITICAL | 100% instant close rate |
| Options Engine (Intraday) | 3/5 | WARN | 38% profit target hit rate |
| Assignment/Margin Safety | 1/5 | CRITICAL | No pre-entry margin validation |
| OCO Manager | 3/5 | WARN | 62% stop hit rate |
| **Overall** | **2/5** | **CRITICAL** | System losing money systematically |

---

## 12) Root Cause Analysis

### P0 — CRITICAL BUGS (Blocking trades or causing losses)

#### P0-1: Spread Sizing Ignores Margin Constraints
- **What:** Spread position sizing (20 contracts) creates $772K notional, but assignment risk requires $154K buffer (20% of notional). Account only has $74K.
- **Evidence:** Every `ASSIGNMENT_RISK_EXIT: MARGIN_BUFFER_INSUFFICIENT` log
- **Impact:** 100% of spread trades instantly closed, -$11,760 direct loss
- **Fix:** Add pre-entry margin validation:
```python
def validate_spread_margin(self, contracts, strike):
    notional = contracts * strike * 100
    required_buffer = notional * 0.20  # 20% for assignment risk
    available_margin = self.Portfolio.MarginRemaining
    max_safe_contracts = int(available_margin / (strike * 100 * 0.20))
    return min(contracts, max_safe_contracts)
```

#### P0-2: Regime Engine Not Responding to VIX Spikes
- **What:** Regime score stayed 60-68 even when VIX hit 30+
- **Evidence:** Jan 24 VIX=32, Regime=65; Jan 27 VIX=31, Regime=65
- **Impact:** Kept entering CALL positions in a bear market
- **Fix:** Add VIX override to regime engine:
```python
if vix > 28:
    regime_score = min(regime_score, 50)  # Force CAUTIOUS
if vix > 35:
    regime_score = min(regime_score, 30)  # Force DEFENSIVE
```

### P1 — HIGH (Major performance leakage)

#### P1-1: Micro Regime "No Direction" Bug
- **What:** MICRO engine returns NONE 158 times, blocking signals
- **Evidence:** `NO_TRADE: MICRO has no direction` in logs
- **Impact:** Missed 43% of potential trades
- **Fix:** Investigate VIX 5d/20d rate of change thresholds. Consider relaxing stable zone from ±2% to ±1%.

#### P1-2: VASS Spread Construction Failures
- **What:** 274 VASS rejections due to "No contracts met spread criteria"
- **Evidence:** `VASS_REJECTION: Reason=No contracts met spread criteria`
- **Impact:** 75% of spread signals never executed
- **Fix:** Relax spread construction criteria:
  - Widen DTE range (7-45 → 5-60)
  - Widen delta range (0.30-0.50 → 0.25-0.55)
  - Accept wider bid-ask spreads in high VIX

#### P1-3: OCO Stop/Profit Ratio Inverted
- **What:** 21 stops vs 13 profits (62% stop rate)
- **Evidence:** OCO logs show consistent stop triggers
- **Impact:** -$5,465 net from OCO ratio imbalance
- **Fix:**
  - Widen stops from 50% to 60-70%
  - Implement delta-scaled ATR stops (V6.5 feature not active)
  - Tighten profit targets to 40%

### P2 — MEDIUM (Optimization opportunities)

#### P2-1: Delta-Scaled ATR Stops Not Active
- **What:** V6.5 feature for ATR-based stops not in logs
- **Evidence:** No `STOP_CALC` log entries
- **Impact:** Using fixed 50% stops in all conditions
- **Fix:** Verify `OPTIONS_USE_ATR_STOPS = True` and QQQ ATR indicator initialized

#### P2-2: Expiration Hammer Working But Losses High
- **What:** 53 expiration events, many at $0.01
- **Evidence:** Multiple trades exiting at $0.01 (worthless)
- **Impact:** -$19,620 from expired positions
- **Fix:** Earlier exit before expiration week. Add "Friday Firewall" for 0-2 DTE positions.

#### P2-3: Entry Timing Poor
- **What:** Many intraday entries followed by immediate reversals
- **Evidence:** 62% stop rate within same session
- **Impact:** Poor entry timing causing premature stops
- **Fix:** Add RSI/momentum confirmation before entry. Wait for pullback after initial UVXY spike.

---

## 13) Recommended Config Changes

```python
# P0-1: Spread sizing margin constraint
SPREAD_MAX_MARGIN_PCT = 0.50  # Max 50% of available margin per spread
SPREAD_ASSIGNMENT_BUFFER_PCT = 0.20  # 20% buffer for assignment risk

# P0-2: VIX regime override
VIX_CAUTIOUS_THRESHOLD = 28  # Force regime ≤50 if VIX > 28
VIX_DEFENSIVE_THRESHOLD = 35  # Force regime ≤30 if VIX > 35

# P1-1: Micro direction thresholds (relax)
VIX_STABLE_PCT = 0.01  # Was 0.02 - narrower stable zone
UVXY_CONVICTION_UP_PCT = 0.025  # Was 0.03 - more sensitive

# P1-2: Spread construction (relax)
SPREAD_DTE_MIN = 5  # Was 7
SPREAD_DTE_MAX = 60  # Was 45
SPREAD_DELTA_MIN = 0.25  # Was 0.30
SPREAD_DELTA_MAX = 0.55  # Was 0.50

# P1-3: OCO ratios (adjust)
OPTIONS_INTRADAY_STOP_PCT = 0.60  # Was 0.50
OPTIONS_INTRADAY_PROFIT_PCT = 0.40  # Was 0.50

# P2-2: Expiration safety
EXPIRATION_FIREWALL_DTE = 2  # Exit all ≤2 DTE positions by Friday 14:00
```

---

## 14) Summary & Next Steps

### Critical Path (Must Fix Before Next Backtest)
1. **P0-1:** Implement pre-entry margin validation for spreads
2. **P0-2:** Add VIX override to regime engine

### High Priority (Fix in V6.7)
3. **P1-1:** Tune micro direction thresholds
4. **P1-2:** Relax spread construction criteria
5. **P1-3:** Adjust OCO stop/profit ratios

### Medium Priority (V6.8+)
6. **P2-1:** Verify delta-scaled ATR stops are active
7. **P2-2:** Add Friday Firewall for expiring positions
8. **P2-3:** Add entry confirmation filters

---

**Audit Completed:** 2026-02-08
**Auditor:** Claude Code (Opus 4.5)
**Next Review:** After P0 fixes implemented
