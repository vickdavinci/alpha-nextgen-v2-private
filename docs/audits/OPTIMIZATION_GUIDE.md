# Alpha NextGen Options Optimization Guide (V6.14)

> Goal: maximize regime compatibility and risk-adjusted returns across bull, bear, and choppy markets.
> Scope: Options Engine only (MICRO intraday + VASS swing), with current production code behavior.
> Last Updated: 2026-02-10

---

## 1) Current System State (Implemented)

### 1.1 Core protections in code

- Pre-market ITM short-leg check at `09:25` is enabled.
- Mandatory spread close at `DTE=1` is enabled.
- Spread normal DTE exit at `DTE<=5` is enabled.
- Single-leg options DTE exit at `DTE<=4` is enabled.
- VIX spike exit for bullish spreads is enabled.
- Regime deterioration exit for spreads is enabled.
- Overnight spread gap protection is enabled.
- Intraday forced close logic exists (scheduled + fallback safety net).

### 1.2 V6.14 pre-market VIX ladder (newly implemented)

Implemented in `main.py` + `config.py`:

- Ladder levels:
  - `L1` elevated: size reduction only.
  - `L2` stress: block new CALL entries until configured time + de-risk bullish options.
  - `L3` panic: block new options entries until configured time + flatten options risk.
- Inputs:
  - CBOE VIX level (`_get_vix_level()`)
  - UVXY overnight gap proxy (`_get_premarket_vix_gap_proxy_pct()`)
- Coverage:
  - Applies to BOTH intraday and EOD options entry flows.
  - Applies size multiplier in options sizing stack.

---

## 2) Current Key Parameters (As-In-Code)

### 2.1 Macro + VASS routing

- `VASS_IV_LOW_THRESHOLD = 16`
- `VASS_IV_HIGH_THRESHOLD = 25`
- `VASS_LOW_IV_DTE_MIN/MAX = 30/45`
- `VASS_MEDIUM_IV_DTE_MIN/MAX = 7/30`
- `VASS_HIGH_IV_DTE_MIN/MAX = 5/40`
- `SPREAD_LONG_LEG_DELTA_MIN = 0.35`
- `SPREAD_SHORT_LEG_DELTA_MAX = 0.60`
- `SPREAD_WIDTH_MIN = 4.0`
- `SPREAD_WIDTH_TARGET = 4.0`
- `SWING_FALLBACK_ENABLED = False`

### 2.2 Credit spread construction quality

- `CREDIT_SPREAD_MIN_CREDIT = 0.20`
- `CREDIT_SPREAD_MIN_CREDIT_HIGH_IV = 0.10`
- `CREDIT_SPREAD_SHORT_LEG_DELTA_MAX = 0.45`
- `CREDIT_SPREAD_MIN_OPEN_INTEREST = 35`
- `CREDIT_SPREAD_MAX_SPREAD_PCT = 0.40`
- `CREDIT_SPREAD_LONG_LEG_MAX_SPREAD_PCT = 0.55`

### 2.3 MICRO conviction and direction

- `MICRO_UVXY_BEARISH_THRESHOLD = +2.8%`
- `MICRO_UVXY_BULLISH_THRESHOLD = -4.5%`
- `MICRO_UVXY_CONVICTION_EXTREME = 3.5%`
- `MICRO_SCORE_BULLISH_CONFIRM = 47`
- `MICRO_SCORE_BEARISH_CONFIRM = 49`
- `INTRADAY_QQQ_FALLBACK_MIN_MOVE = 0.30%`
- `QQQ_NOISE_THRESHOLD = 0.13%`

### 2.4 Position limits (shared options budget)

- `OPTIONS_MAX_INTRADAY_POSITIONS = 1`
- `OPTIONS_MAX_SWING_POSITIONS = 2`
- `OPTIONS_MAX_TOTAL_POSITIONS = 3`
- `INTRADAY_MAX_TRADES_PER_DAY = 2`

### 2.5 Expiry and assignment defense

- `SPREAD_FORCE_CLOSE_DTE = 1`
- `SPREAD_DTE_EXIT = 5`
- `OPTIONS_SINGLE_LEG_DTE_EXIT = 4`
- `PREMARKET_ITM_CHECK_ENABLED = True`

### 2.6 Pre-market ladder config

- `PREMARKET_VIX_L1_LEVEL/GAP = 22 / +4%`
- `PREMARKET_VIX_L2_LEVEL/GAP = 28 / +7%`
- `PREMARKET_VIX_L3_LEVEL/GAP = 35 / +12%`
- `PREMARKET_VIX_L1/L2/L3_SIZE_MULT = 0.75 / 0.50 / 0.25`
- `PREMARKET_VIX_L2_CALL_BLOCK_UNTIL = 11:00`
- `PREMARKET_VIX_L3_ENTRY_BLOCK_UNTIL = 12:00`

---

## 3) What Is Working vs Not Working (Current Read)

### Working

- Technical protections are materially stronger than earlier versions.
- Direction mismatch and missing instrumentation issues are substantially reduced.
- Pre-market shock handling now exists as a unified control layer.

### Still problematic

- Bear/chop strategy P&L robustness is not yet proven.
- VASS rejection rate can still be high when chain quality is poor.
- MICRO still has periods of low participation (`Dir=None` pressure) and may under-fire in some windows.
- Shared daily/position caps can still crowd out one mode if the other consumes slots early.

---

## 4) Optimization Strategy (Minimal-Code, Parameter-First)

Use a strict order. Do NOT tune everything at once.

### Phase A: Participation and constructability

1. Tune VASS constructability first (without loosening safety too far).
2. Tune MICRO participation second (reduce unnecessary `NO_TRADE`).
3. Keep call-bias controls intact while tuning participation.

### Phase B: Regime-compatibility

1. Validate CALL blocking behavior in stress (`L2/L3`) on bear windows.
2. Validate bull participation is not over-suppressed by ladder windows.
3. Validate choppy damage control via size/stops and neutrality behavior.

### Phase C: P&L shape

1. Tighten loser tails first (stop/exit discipline).
2. Then improve hit rate via selective threshold adjustments.
3. Only after that, consider increasing frequency.

---

## 5) Recommended Next Tuning Window (Small Step)

Apply one pass only, then backtest:

### 5.1 VASS (constructability)

- Keep risk filters, but lightly widen candidate pool only if rejection remains extreme:
  - `SPREAD_LONG_LEG_DELTA_MIN`: `0.35 -> 0.32`
  - `CREDIT_SPREAD_SHORT_LEG_DELTA_MAX`: `0.45 -> 0.48`
  - `CREDIT_SPREAD_MIN_CREDIT`: keep `0.20` (do not lower first)
  - `CREDIT_SPREAD_MIN_OPEN_INTEREST`: keep `35` (do not lower first)

### 5.2 MICRO (participation without reintroducing CALL bias)

- Keep asymmetric conviction thresholds (`+2.8% / -4.5%`).
- First participation tweak only:
  - `MICRO_SCORE_BULLISH_CONFIRM`: `47 -> 46`
  - `INTRADAY_QQQ_FALLBACK_MIN_MOVE`: `0.30 -> 0.25`
- Do NOT relax `MICRO_UVXY_BULLISH_THRESHOLD` yet.

### 5.3 Ladder control (avoid over-throttling)

- Keep `L2 CALL block` and `L3 freeze` as-is for one full validation cycle.
- If bull period under-trades materially, shorten only one window:
  - `L2 CALL block until 11:00 -> 10:45`

---

## 6) Backtest Protocol (Required)

Run exactly this sequence after each tuning pass:

1. `2017 H1` (bull integrity)
2. `2022 H1` (bear defense)
3. `2015 Jul-Sep` (crash + chop)

Track these metrics per run:

- Total P&L
- Max drawdown
- Win rate
- CALL/PUT count and ratio
- MICRO approved -> executed conversion
- VASS rejection counts by reason
- Margin reject count
- Assignment/ITM emergency exits
- Overnight holds (intraday should be zero)

Promotion rule for a tuning pass:

- No new technical regressions.
- Bear drawdown does not worsen.
- Bull participation does not collapse.
- VASS rejection rate improves or stays stable with better P&L.

---

## 7) Guardrails (Do Not Break)

Do not remove these while optimizing:

- `SPREAD_FORCE_CLOSE_DTE = 1`
- `OPTIONS_SINGLE_LEG_DTE_EXIT = 4`
- pre-market ITM checks
- pre-market VIX ladder (`L2/L3`) until cross-period evidence supports loosening
- asymmetric UVXY conviction (prevents call bias from returning in bear/chop)

---

## 8) Practical Notes for Developers

- Keep optimization mostly in `config.py`.
- Add strategy logic only when repeated cross-period evidence shows a structural issue.
- Every optimization PR must include:
  - exact parameter diffs
  - before/after metrics table for all three benchmark windows
  - confirmation no technical bug checks regressed

---

## 9) Current Truth Sources

- Code: `main.py`, `config.py`, `engines/satellite/options_engine.py`
- Master audit: `docs/audits/OPTIONS_ENGINE_MASTER_AUDIT.md`
- Work log: `WORKBOARD.md` (V6.14 section)
