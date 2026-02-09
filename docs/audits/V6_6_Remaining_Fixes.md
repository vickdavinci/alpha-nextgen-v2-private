# V6.6 Remaining Fixes — Consolidated List

This document consolidates **all fixes still needed** based on:
- Current codebase review
- `docs/audits/V6_6_Options_Engine_Audit_Report.md`

Scope: options engine, micro, VASS, execution safety.

**Last Updated:** 2026-02-08 (V6.8)

---

## P0 — Critical

1) **Direction‑None fallback still dominates**
   - Root issue: Micro can approve trades even when its strategy is `NO_TRADE` and direction is `None`, falling back to Macro.
   - Impact: Wrong‑direction CALLs in down moves; poor intraday performance.
   - Required fix: If Micro returns `NO_TRADE`, **do not** approve a trade (even if Macro is bullish).
   - **Status: ✅ FIXED V6.8** — `options_engine.py:2015-2022` blocks NO_TRADE entirely, no fallback.

2) **OCO after‑hours order submission**
   - Root issue: `OCOManager.submit_oco_pair()` has no market‑hours guard.
   - Impact: invalid OCO submission outside market hours.
   - Required fix: block OCO submission outside regular trading hours.
   - **Status: ✅ FIXED V6.8** — `oco_manager.py:262-275` checks `Exchange.ExchangeOpen`.

---

## P1 — High

3) **Prevent approvals when strategy = NO_TRADE**
   - Root issue: approvals can proceed with `Strategy=NO_TRADE`, then contract selection fails.
   - Impact: "No contract selected | Strategy=NO_TRADE" and huge approval→signal leakage.
   - Required fix: if strategy is `NO_TRADE`, return `should_trade = False` with reason.
   - **Status: ✅ FIXED V6.8** — Same fix as #1, NO_TRADE blocks at `generate_micro_intraday_signal()`.

4) **CALLs on down days**
   - Root issue: direction resolution can ignore QQQ direction when Macro is bullish.
   - Impact: CALL trades are executed on down moves.
   - Required fix: add a QQQ direction filter in intraday logic (block CALL on QQQ down unless explicit conviction says otherwise).
   - **Status: ✅ FIXED V6.8** — By blocking NO_TRADE entirely, wrong-direction trades from FOLLOW_MACRO are prevented.

5) **VASS rejection wall (high IV)**
   - Root issue: VASS rejects nearly all candidates due to strict DTE/delta/width filters.
   - Impact: VASS is disabled in high‑IV regimes when it should be active.
   - Required fix: further relax high‑IV filters if next backtest still shows heavy rejection.
   - **Status: ⚠️ MITIGATED V6.8** — DTE 5-28, deltas 0.40-0.55, width 3.0, OI 50, assignment buffer 10%.

---

## P2 — Medium

6) **Margin callback / liquidation guard**
   - Root issue: no explicit guard found in code for margin callback events.
   - Impact: forced liquidation episodes possible during stress.
   - Required fix: add margin utilization guardrails / proactive down‑sizing.
   - **Status: ⚠️ OPEN** — `_margin_cb_in_progress` flag exists but needs verification.

---

## Already Applied (Verified in Code)

- ✅ Assignment‑risk exit uses **spread max loss** (not naked notional) — V6.7
- ✅ ATR stop cap lowered to **30%**, multiplier lowered to **1.0** — V6.8
- ✅ ATR stop min lowered to **15%** — V6.8
- ✅ High‑IV DTE window widened (5-28) and spread deltas loosened (0.40-0.55) — V6.8
- ✅ Spread width reduced (4.0→3.0) for more chain matches — V6.8
- ✅ NO_TRADE blocks entirely, no conviction override — V6.8
- ✅ OCO market hours guard added — V6.8
- ✅ VIX floor lowered (13.5→11.5) — V6.8
- ✅ Micro scores lowered (45/50→35/40) — V6.8
- ✅ UVXY conviction thresholds narrowed (3%→2.5%) — V6.8

---

## Remaining Open Items

| # | Issue | Priority | Status |
|---|-------|----------|--------|
| 6 | Margin callback guard | P2 | OPEN |

---

## Notes

These fixes are prioritized to align with the goal:
**Micro = main profit engine** while maintaining safety in all regimes.

