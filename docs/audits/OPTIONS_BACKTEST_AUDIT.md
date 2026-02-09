# OPTIONS BACKTEST AUDIT — MASTER CHECKLIST

Use this as the definitive audit template for **Options Engine backtests**.  
Derived from `docs/audits/BACKTEST_AUDIT_AGENT_PROMPT.md`, expanded with options‑specific constraints and failure modes.

---
# Files : /docs/logs/stage6.5/V6_6*

# 0) Backtest Metadata
- Backtest name:
- Log path:
- Orders CSV:
- Trades CSV:
- Date range:
- Starting capital:
- Market context: (bull / bear / choppy / transition)
- Version/branch:

---

## 1) Pre‑Read Checklist (Required)
- `config.py` reviewed for all active thresholds.
- `CLAUDE.md` reviewed for architecture rules.
- Log file(s) loaded.
- Orders + trades CSV loaded (if available).

---

## 2) Performance Summary (Options‑Only Focus)
| Metric | Value |
|---|---|
| Net return (options only) | |
| Total options trades | |
| Win rate | |
| Avg win / Avg loss | |
| Max DD (options only) | |
| Sharpe / Sortino (if available) | |

Notes:
- If full portfolio metrics are provided, **separate options‑only** impact when possible.

---

## 3) Regime & Navigation (Options Relevance)
### 3A. Regime Distribution
| Regime | Score Range | Days | % | Avg Score |
|---|---:|---:|---:|---:|
| RISK_ON | >= 70 | | | |
| UPPER_NEUTRAL | 60–69 | | | |
| LOWER_NEUTRAL | 50–59 | | | |
| CAUTIOUS | 40–49 | | | |
| DEFENSIVE | 30–39 | | | |
| RISK_OFF | < 30 | | | |

### 3B. Regime Accuracy vs Market
| Date Range | Market Reality | Expected Regime | Actual Regime | Match |
|---|---|---|---|---|
| | | | | |

### 3C. Regime Transition Latency
- Major events (SPY drops >3%, VIX spikes >25):  
  - Event date → regime response date → lag (days)
- Target latency: **1–3 days**  
  - Flag if >5 days.

---

## 4) Conviction Engine Validation (Options)
### 4A. VASS Conviction
| Metric | Expected | Actual |
|---|---|---|
| VASS daily history populated | Yes | |
| VIX 5d change logged | Yes | |
| VIX 20d change logged | Yes | |
| Conviction triggers in volatility | >0 | |
| BEARISH conviction when VIX 5d > +20% | Yes | |
| BULLISH conviction when VIX 5d < -15% | Yes | |
| Level crossings (>25 / <15) | Yes | |

**Red flags**
- `VASS.*5d.*None` or missing → daily tracking broken.

### 4B. Micro Conviction
| Metric | Expected | Actual |
|---|---|---|
| UVXY intraday changes logged | Yes | |
| BEARISH conviction when UVXY > +8% | Yes | |
| BULLISH conviction when UVXY < -5% | Yes | |
| VIX crisis trigger > 35 | Yes | |
| VIX complacent trigger < 12 | Yes | |

### 4C. Conviction Override (Veto) Behavior
| Date | Engine | Conviction | Macro | Final Direction | Correct |
|---|---|---|---|---|---|
| | | | | | |

---

## 5) Options Engine Core Validation

### 5A. Strategy Type
**Expected:** All DEBIT (no credit spreads)
- Search for: `BULL_PUT_CREDIT`, `BEAR_CALL_CREDIT` (expect 0)

### 5B. Position Limits
| Limit | Expected | Observed |
|---|---|---|
| Max intraday | 1 | |
| Max swing | 2 | |
| Max total | 3 | |

### 5C. Direction by Regime
| Regime | Expected Direction |
|---|---|
| RISK_ON | CALL only |
| UPPER_NEUTRAL | CALL (reduced) |
| LOWER_NEUTRAL | PUT (reduced) |
| CAUTIOUS+ | PUT only |

### 5D. Spread Entry/Exit Integrity
- Entry logs exist: `SPREAD: ENTRY_SIGNAL`, `POSITION_REGISTERED`
- Exit logs exist: `SPREAD: EXIT_SIGNAL` with **reason**
- No “instant close” at entry unless explicitly expected
- Assignment risk exit closes **both legs** (combo exit)
- Gamma‑pin exit fires **once per position**

### 5E. Intraday Entry/Exit Integrity
- `INTRADAY_SIGNAL_APPROVED` → either `INTRADAY_SIGNAL` or explicit rejection log
- “No contract selected” logged if contract selection fails
- Time window rejects are logged (`INTRADAY_TIME_REJECT`)
- Direction mismatch logged and blocked (`INTRADAY: Direction mismatch`)
- Stops and targets set & logged on entry

---

## 6) Strategy‑to‑Direction Consistency
| Strategy | Expected Direction | Actual Direction | Mismatch? |
|---|---|---|---|
| DEBIT_FADE | CALL/PUT (per micro rule) | | |
| ITM_MOMENTUM | PUT in rising VIX / CALL in falling VIX | | |
| DEBIT_MOMENTUM | Momentum direction | | |
| PROTECTIVE_PUTS | PUT only | | |

**Red flag:** Approved direction ≠ selected contract type.

---

## 7) Regime‑Based Profit & Stop Behavior
### 7A. Spreads
- Profit target uses `SPREAD_PROFIT_REGIME_MULTIPLIERS`
- Stop loss uses fixed `SPREAD_STOP_LOSS_PCT`

### 7B. Single‑Leg (Intraday)
- ATR‑scaled stops active?
- Fixed profit target used?
- If regime‑aware targets were implemented, verify multipliers in logs.

---

## 8) Assignment & Margin Risk
### 8A. Assignment Risk Exit
| Date | Reason | Exit Executed | Both Legs Closed |
|---|---|---|---|
| | | | |

### 8B. Margin Gate
| Metric | Expected | Actual |
|---|---|---|
| Margin utilization <= 70% | Yes | |
| Margin blocks logged | | |

---

## 9) Funnel Analysis (Signal Loss)
```
Stage 1: Trading days available
Stage 2: Regime allowed options
Stage 3: Conviction signals generated
Stage 4: Entry signals approved
Stage 5: Contract selected
Stage 6: Passed filters (time, spread, direction)
Stage 7: Orders submitted
Stage 8: Orders filled
```
Identify biggest leakage point.

---

## 10) Smoke Signals (Critical Failure Flags)
| Severity | Pattern | Expected |
|---|---|---|
| CRITICAL | `ERROR` / `EXCEPTION` | 0 |
| CRITICAL | `MARGIN_ERROR` | 0 |
| CRITICAL | `NEUTRALITY_EXIT` | 0 |
| CRITICAL | `CREDIT.*spread` | 0 |
| CRITICAL | Governor scale 75/50/25 | 0 |
| WARN | `conviction.*None` | 0 |
| WARN | `VASS.*5d.*None` | 0 |

---

## 11) Options‑Only Scorecard
| System | Score (/5) | Status | Key Finding |
|---|---:|---|---|
| Regime Identification | | | |
| Regime Navigation | | | |
| VASS Conviction | | | |
| Micro Conviction | | | |
| Options Engine | | | |
| Assignment/Margin Safety | | | |
| Overall | | | |

---

## 12) Required Output File Name
Save final audit report to:
`docs/audits/{VERSION}_{BACKTEST_NAME}_options_audit.md`

