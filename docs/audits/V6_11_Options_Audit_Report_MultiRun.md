# V6.11 Options Engine Audit — 2022 Full Year

**Run:** `V6_11_2022FullYear`  
**Files reviewed:**  
`docs/audits/logs/stage6.5/V6_11_2022FullYear_logs.txt`  
`docs/audits/logs/stage6.5/V6_11_2022FullYear_orders.csv`  
`docs/audits/logs/stage6.5/V6_11_2022FullYear_trades.csv`

**Date:** 2026-02-09

---

## Executive Summary

V6.11 shows **improved PUT participation** and **no assignment‑driven QQQ equity trades**, but the options engine still **lost money (-$37,784)** with a **low win rate (24.1%)**.  
Core blockers remain: **high Dir=NONE**, **large VASS rejection volume**, and **weak signal→execution conversion**.

---

## Bug List (Current Run) — Technical

| Priority | Bug | Evidence | Impact | Status |
|:--------:|-----|----------|--------|:------:|
| **P0** | 2015: Intraday Trade Escaped Force‑Close | Trade opened 2015‑07‑13 intraday (CALL x144) held until 2015‑07‑17, +$39,168 | Intraday force‑close failed, OCO canceled → position became swing | 🟡 Applied (Pending Validation) |
| **P0** | Scheduler Failure (PRE_MARKET_SETUP callback) | `SCHEDULER: Callback error for PRE_MARKET_SETUP: No method matches given arguments for Log` (observed across 2015/2017/2022 logs; >1,000 occurrences total) | Pre‑market setup fails; downstream EOD/force‑close may not run → positions held overnight | 🟡 Applied (Pending Validation) |
| **P0** | Short-Leg Close Logging Missing | `SPREAD: Short leg closed` **0** vs `SPREAD: POSITION_REMOVED` **71** | Orphaned short risk or missing close logging | 🟡 Applied (Pending Validation) |
| **P0** | Zero-Price Inject (Sizing Bug) | `V2.19_INJECT: ... price=0` **130 occurrences** | Oversized positions from $0 pricing | 🟡 Applied (Pending Validation) |
| **P0** | Intraday Exit Missing | `INTRADAY_POSITION_REMOVED` **90** vs `INTRADAY position registered` **116** | Intraday exits not logged or not executed | 🟡 Applied (Pending Validation) |
| **P1** | Swing/Intraday Cross-Blocking | Swing entry used `has_position()` and intraday scan exited on any position | Intraday trades blocked swing entries and vice‑versa | 🟡 Applied (Pending Validation) |

## Bug List (Current Run) — Optimization

| Priority | Bug | Evidence | Impact | Status |
|:--------:|-----|----------|--------|:------:|
| **P1** | Low Win Rate / Negative P&L | Win% **24.1%**, Net **-37,784** | Core strategy not profitable | 🔴 Open |
| **P1** | High Dir=NONE | `Dir=NONE` **4,706 / 7,280** (~64.6%) | Signal starvation | 🟡 Applied (Pending Validation) |
| **P1** | VASS Rejection Storm | `VASS_REJECTION` **1,734** vs `VASS_ENTRY` **30** | Bear/credit spreads still not executing | 🟡 Applied (Pending Validation) |
| **P1** | Signal → Execution Gap | `INTRADAY_SIGNAL` 360 → `INTRADAY_RESULT` 71 | Most signals never become trades | 🔴 Open |
| **P1** | CALL Loss Concentration | CALL P&L **-32,811** vs PUT **-4,973** | Direction/strategy bias hurting performance | 🟡 Applied (Pending Validation) |
| **P2** | Assignment Risk Still Triggers | `ASSIGNMENT_RISK_EXIT` **14** | Frequent defensive exits reduce opportunity | 🟡 Monitor |
| **P1** | 2017: Dir=NONE Dominates | `Dir=NONE` **6,928 / 7,260** (~95.4%) | Micro still mostly inactive in bull year | 🔴 Open |
| **P1** | 2017: Intraday Execution Near-Zero | `INTRADAY_SIGNAL` 20 → `INTRADAY_RESULT` 1 | Intraday pipeline still starved in bull year | 🔴 Open |
| **P2** | 2017: No PUT Trades | Trades: 35 CALL, 0 PUT | No downside capture even in pullbacks | 🟡 Monitor |
| **P2** | 2015: PUT Trades Negative | PUT P&L **-8,072** vs CALL **+24,075** | Bearish/hedge logic losing in choppy year | 🟡 Monitor |

## Performance Snapshot

**All trades**
- Total trades: **141**
- Win%: **24.1%** (34 wins / 107 losses)
- Net P&L: **-37,784**
- Fees: **1,641.05**

**By direction**
- Calls: **71 trades**, **P&L -32,811**, Wins **11**
- Puts: **70 trades**, **P&L -4,973**, Wins **23**

**Underlying assignments**
- `QQQ` equity trades: **0**

---

## Order Mix

- Total orders: **396**
- Filled orders: **278**
- PUT orders: **214**
- CALL orders: **182**

**Observation:** Direction mix is now balanced (puts slightly higher).

---

## Signal → Execution Metrics

- `MICRO_UPDATE`: **7,280**
- `Dir=NONE`: **4,706** (~64.6%)
- `INTRADAY_SIGNAL`: **360**
- `INTRADAY_SIGNAL_APPROVED`: **247**
- `INTRADAY: Selected`: **247**
- `INTRADAY_RESULT`: **71**

**Conversion:** 71 results / 360 signals ≈ **19.7%**  
Still low; most intraday signals do not become executed results.

---

## VASS Activity

- `VASS_REJECTION`: **1,734**
- `VASS_ENTRY`: **30**

**Observation:** VASS is still heavily rejected despite relaxed deltas and fallback.

---

## Assignment Prevention

- `ASSIGNMENT_RISK_EXIT`: **14**
- `PREMARKET_ITM` checks: **1**
- `QQQ` equity assignments: **0**

**Conclusion:** Assignment controls appear effective (no equity assignments).

---

## Major Issues (Ranked)

**P1 — Low Win Rate + Negative P&L**
   - Calls are heavily negative (**-32.8k**) despite balanced direction mix.
   - Indicates poor strategy profitability, not just direction imbalance.

**P1 — High Dir=NONE**
   - ~65% of micro updates return no direction, still limiting trade flow.

**P1 — VASS Rejections Still Dominant**
   - 1,734 rejections vs 30 entries → spread construction still too restrictive.

**P1 — Signal→Execution Gap**
   - Only 71 outcomes from 360 signals; pipeline is still leaky.

---

## What Improved vs V6.9

✅ **Assignments eliminated** (no QQQ equity trades)  
✅ **PUT participation increased** (near 50/50)  
✅ **Margin‑related failures not observed in logs**  

---

## Fixes Applied (Pending Validation)

- **VASS spread failure cooldown trap**: Added DTE fallback ranges and only apply cooldown after all ranges fail.
- **VASS rejection visibility**: Added compact `FailStats` summary to throttled `VASS_REJECTION` logs.
- **Swing/Intraday cross‑blocking**: Replaced `has_position()` gate with mode‑specific `can_enter_swing()` and `has_intraday_position()` checks.
- **Micro Dir=None reduction**: Lowered `QQQ_NOISE_THRESHOLD` (0.35→0.25), `INTRADAY_QQQ_FALLBACK_MIN_MOVE` (0.50→0.35), `MICRO_SCORE_*_CONFIRM` (52/48→50/50), and `INTRADAY_ITM_MIN_MOVE` (0.80→0.45).
- **CALL bias control**: Asymmetric UVXY conviction thresholds (CALL -5%, PUT +2.5%) and NEUTRAL aligned trades allowed at reduced size.

---

## What Still Needs Fixing

1. **Reduce Dir=NONE further** (micro thresholds still too conservative)  
2. **Improve VASS contract selection rate** (rejections still very high)  
3. **Close the signal→execution gap**  
4. **Improve call profitability** (strategy logic or stop/target tuning)

---

## Recommended Next Actions

1. **Lower VASS rejection rate**
   - Re‑examine delta bounds vs market data in 2022.
2. **Tighten intraday execution pipeline**
   - Add explicit logging for approval→submit→fill to trace drop‑offs.
3. **Audit CALL strategy**
   - Losses are concentrated in CALLs; check for wrong regime usage or stop/target scaling.

---

## Optimization Fix List (Summary)

**Execution & Pipeline**
- Add explicit log markers at: signal approved → order submit → fill → register.
- Track “approved but not submitted” reasons (margin pre‑check, cooldown, time window).

**Micro Signal Quality**
- Further reduce `Dir=NONE` by narrowing VIX‑stable band or adding a secondary QQQ fallback for low‑VIX days.
- Add a minimum conviction threshold that scales with VIX (lower in low‑VIX regimes).

**VASS Construction**
- Instrument rejection reasons by category (delta, DTE, credit, liquidity) and log counts.
- Relax delta/DTE only for the **rejection-dominant category**, not globally.

**Strategy Profitability**
- Review CALL losses by regime; disable CALLs in CAUTIOUS/DEFENSIVE unless macro+micro align.
- Re‑test stop/target symmetry for CALLs only; keep PUT logic unchanged if it is less negative.

**Assignment & Risk**
- Keep DTE=1 force‑close and pre‑market ITM check (working).
- Add a pre‑entry assignment buffer check for **all** spreads (not just on exit).

---

# V6.11 Options Engine Audit — 2017 Full Year

**Run:** `V6_11_2017FullYear`  
**Files reviewed:**  
`docs/audits/logs/stage6.5/V6_11_2017FullYear_logs.txt`  
`docs/audits/logs/stage6.5/V6_11_2017FullYear_orders.csv`  
`docs/audits/logs/stage6.5/V6_11_2017FullYear_trades.csv`

**Date:** 2026-02-09

---

## Performance Snapshot (2017)

- Total trades: **35**
- Win%: **57.1%**
- Net P&L: **+25,967**
- Fees: **1,600.30**

**By direction**
- Calls: **35 trades**, **+25,967 P&L**
- Puts: **0 trades**

---

## Signal / Execution Metrics (2017)

- `MICRO_UPDATE`: **7,260**
- `Dir=NONE`: **6,928** (~95.4%)
- `Dir=CALL`: **269**
- `Dir=PUT`: **73**
- `INTRADAY_SIGNAL`: **20**
- `INTRADAY_SIGNAL_APPROVED`: **18**
- `INTRADAY: Selected`: **8**
- `INTRADAY_RESULT`: **1**
- `VASS_ENTRY`: **34**
- `VASS_REJECTION`: **64**
- `PREMARKET_ITM`: **3**

---

## 2017 Observations

1. **Micro still largely inactive** (Dir=NONE ~95%).  
2. **Intraday execution nearly zero** (1 result for the entire year).  
3. **No PUT trades**, even in minor pullbacks.  
4. **Despite low activity, P&L was positive** due to CALL success in bull market.

---

# V6.11 Options Engine Audit — 2015 Full Year

**Run:** `V6_11_2015FullYear`  
**Files reviewed:**  
`docs/audits/logs/stage6.5/V6_11_2015FullYear_logs.txt`  
`docs/audits/logs/stage6.5/V6_11_2015FullYear_orders.csv`  
`docs/audits/logs/stage6.5/V6_11_2015FullYear_trades.csv`

**Date:** 2026-02-09

---

## Performance Snapshot (2015)

- Total trades: **109**
- Win%: **44.0%**
- Net P&L: **+16,003**
- Fees: **4,354.60**

**By direction**
- Calls: **84 trades**, **+24,075 P&L**
- Puts: **25 trades**, **-8,072 P&L**

---

## Signal / Execution Metrics (2015)

- `MICRO_UPDATE`: **7,300**
- `Dir=NONE`: **6,309** (~86.4%)
- `Dir=CALL`: **608**
- `Dir=PUT`: **403**
- `INTRADAY_SIGNAL`: **101**
- `INTRADAY_SIGNAL_APPROVED`: **76**
- `INTRADAY: Selected`: **56**
- `INTRADAY_RESULT`: **18**
- `VASS_ENTRY`: **84**
- `VASS_REJECTION`: **461**
- `ASSIGNMENT_RISK_EXIT`: **2**

---

## 2015 Observations

1. **Overall P&L positive, but PUT trades negative.**  
2. **Dir=NONE still high**, though lower than 2017.  
3. **Intraday conversion weak** (101 signals → 18 results).  
4. **VASS still rejected frequently** (461 rejections).

---
