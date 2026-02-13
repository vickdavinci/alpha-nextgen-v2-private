# V9.2 Implementation Plan — MICRO Exit R:R Tuning + VASS Credit R:R Hardening

Date: 2026-02-13
Scope: Minimal, high-leverage changes only. No architecture rewrite.
Goal: Improve realized R:R and expectancy without breaking bull/choppy behavior.

---

## 1) Problem Statement

From recent RCA and stage9.1 runs:

- MICRO intraday has negative expectancy in 2022 despite frequent correct direction calls in some regimes.
- Existing intraday exits are effectively universal (same target/stop style) for very different strategy types.
- VASS credit entries still allow weak payoff structures (absolute min credit exists, but no strict credit/width quality floor).
- We already fixed major plumbing bugs and telemetry mismatches; now this is a quality/expectancy pass.

This plan focuses on two tightly scoped fixes:

1. MICRO: strategy-aware exits (target/stop/trail) with safe state plumbing.
2. VASS: credit entry R:R hardening (credit/width gate + clear telemetry).

---

## 2) Design Principles

- Keep changes additive and reversible.
- Avoid large control-flow rewrites.
- Persist only the minimum state needed for deterministic exits.
- Prefer config-driven tuning to code branching.
- Add telemetry where decisions occur, not post-facto inference.

---

## 3) MICRO Exit R:R Plan

### 3.1 What to change

#### A) Persist entry strategy on position (required)

File: `engines/satellite/options_engine.py`

- Extend `OptionsPosition`:
  - `entry_strategy: str = "UNKNOWN"`
  - `highest_price: float = 0.0`
- Update `to_dict()` and `from_dict()` with backward-compatible defaults.

Rationale:
- Exit logic must know *which strategy opened the trade*.
- Using current engine state at exit time is unsafe (state can drift intraday).

#### B) Capture strategy at signal-time, carry to fill-time

File: `engines/satellite/options_engine.py`

- Add pending field(s):
  - `_pending_entry_strategy: Optional[str]`
- In `check_intraday_entry_signal(...)`, set pending strategy from approved signal context.
- In `register_entry(...)`, read pending strategy and stamp `OptionsPosition.entry_strategy`.
- Clear pending strategy in all existing pending-clear paths.

Rationale:
- Prevent race between signal generation and delayed fills.

#### C) Apply per-strategy target/stop at registration

File: `engines/satellite/options_engine.py`

- In `register_entry(...)`, replace universal intraday target assignment with strategy dispatch:
  - `ITM_MOMENTUM` -> `INTRADAY_ITM_TARGET`
  - `DEBIT_FADE` -> `INTRADAY_DEBIT_FADE_TARGET`
  - `DEBIT_MOMENTUM` -> `INTRADAY_DEBIT_MOMENTUM_TARGET`
  - `PROTECTIVE_PUTS` -> keep existing default target unless explicitly configured
- Keep current stop calculation framework, but allow per-strategy stop override:
  - `INTRADAY_ITM_STOP`
  - `INTRADAY_DEBIT_FADE_STOP`
  - `INTRADAY_DEBIT_MOMENTUM_STOP`
  - `PROTECTIVE_PUTS_STOP_PCT` (existing)

Rationale:
- Strategy payoff profiles are different; one size does not fit all.

#### D) Add lightweight trailing stop logic

File: `engines/satellite/options_engine.py`

- Add helper: `_get_intraday_trail_config(strategy: str) -> (trigger, trail_pct)`.
- In `check_exit_signals(...)`:
  - Update `highest_price` when `current_price` exceeds it.
  - Activate trail when gain >= trigger.
  - Trail stop uses percent of open gain from high watermark.
  - Exit reason code: `TRAIL_STOP`.

Important ordering:
1. Profit target
2. Trailing stop
3. Hard stop
4. DTE exit

Rationale:
- Lock gains; reduce winner-to-loser reversals.

#### E) OCO consistency rule

Files: `main.py` + existing OCO handling path

- Verify OCO recreation/cancel-replace reads *current* position stop/target fields.
- If trailing stop updates software stop, enforce one of:
  - software-only trail with clear precedence, or
  - explicit OCO stop refresh event.

Chosen default for this phase:
- software trailing stop triggers market exit independently; no dynamic OCO mutation beyond existing flows.

Rationale:
- Prevent additional broker race complexity in first pass.

---

### 3.2 MICRO config additions

File: `config.py`

Add:

- `INTRADAY_DEBIT_FADE_TARGET = 0.40`
- `INTRADAY_DEBIT_FADE_STOP = 0.25`
- `INTRADAY_DEBIT_FADE_TRAIL_TRIGGER = 0.25`
- `INTRADAY_DEBIT_FADE_TRAIL_PCT = 0.50`

- `INTRADAY_DEBIT_MOMENTUM_TARGET = 0.45`
- `INTRADAY_DEBIT_MOMENTUM_STOP = 0.30`
- `INTRADAY_DEBIT_MOMENTUM_TRAIL_TRIGGER = 0.20`
- `INTRADAY_DEBIT_MOMENTUM_TRAIL_PCT = 0.50`

Existing (already present, now wired):
- `INTRADAY_ITM_TARGET`
- `INTRADAY_ITM_STOP`
- `INTRADAY_ITM_TRAIL_TRIGGER`
- `INTRADAY_ITM_TRAIL_PCT`

No change in this phase:
- `PROTECTIVE_PUTS_STOP_PCT`
- `OPTIONS_PROFIT_TARGET_PCT` (fallback)

---

## 4) VASS Credit R:R Hardening Plan

### 4.1 What to change

#### A) Add credit/width quality gate (required)

Files: `config.py`, `engines/satellite/options_engine.py`

New config:
- `CREDIT_SPREAD_MIN_CREDIT_TO_WIDTH_PCT = 0.35`
- `CREDIT_SPREAD_MIN_CREDIT_TO_WIDTH_PCT_HIGH_IV = 0.30` (optional high-VIX relaxation)

Entry check:
- `credit_to_width = credit_received / width`
- Reject when below threshold with reason code:
  - `CREDIT_TO_WIDTH_TOO_LOW`

Keep absolute credit gate too:
- `CREDIT_SPREAD_MIN_CREDIT` remains active.

Rationale:
- Prevent low-quality credits that look valid on absolute dollars but poor by structure.

#### B) Keep current stop formula (already fixed)

No logic change to formula in this plan:
- `stop_threshold = entry_credit + max_loss * CREDIT_SPREAD_STOP_MULTIPLIER`

Optional tune (separate pass only):
- Make multiplier regime-aware after one validation cycle.

Rationale:
- Avoid bundling too many behavioral changes.

---

### 4.2 VASS telemetry additions

File: `engines/satellite/options_engine.py`

On credit candidate/entry/reject logs include:
- `Credit`, `Width`, `CreditToWidth`, `RRProxy=credit/(width-credit)`
- active threshold used (normal/high-IV)
- reject code (`CREDIT_TO_WIDTH_TOO_LOW`)

Rationale:
- Deterministic RCA of why credits were accepted/rejected.

---

## 5) Concrete Code Touch List

- `engines/satellite/options_engine.py`
  - `OptionsPosition` dataclass fields + serde
  - pending strategy capture/clear lifecycle
  - `register_entry` per-strategy target/stop assignment
  - `check_exit_signals` trailing stop logic
  - helper `_get_intraday_trail_config`
  - credit entry `credit/width` gate + telemetry

- `config.py`
  - add per-strategy MICRO target/stop/trail params for FADE/MOMENTUM
  - add credit/width thresholds

- `main.py`
  - validate no stale OCO assumptions with strategy-aware fields
  - no broad logic rewrite expected

---

## 6) Acceptance Criteria

### 6.1 Functional

- MICRO positions persist `entry_strategy` and `highest_price` correctly through save/load.
- Intraday exits emit `TRAIL_STOP` where applicable.
- Credit entries below ratio threshold are rejected with canonical code.
- Existing spread and single-leg plumbing continues to pass compile and normal event flow.

### 6.2 Safety

- No regression in force-close behavior (intraday and spread).
- No new duplicate-exit loops.
- No missing clear/reset for new pending fields.

### 6.3 Backtest diagnostics

Daily logs must include enough to answer:
- Which MICRO strategy opened each position and why it exited.
- How many credit candidates failed credit/width gate.
- Distribution of accepted credit/width and R:R proxies.

---

## 7) Test Plan

### 7.1 Static / sanity

- `python3 -m py_compile main.py engines/satellite/options_engine.py config.py`
- grep validation for new codes/fields in logs.

### 7.2 Focused backtests

1. 2017 Jul-Sep (bull)
- Ensure no catastrophic regression in CALL-friendly environment.
- Check trail-stop behavior and profit-lock rate.

2. 2022 full-year (bear)
- Validate MICRO expectancy uplift and reduction in deep giveback.
- Validate VASS credit entry quality improves (fewer weak-credit structures).

### 7.3 Pass/fail metrics

- MICRO:
  - improved realized expectancy vs baseline
  - fewer winner-to-loser reversals after +20%
  - lower concentration of exits at hard-stop floor

- VASS credit:
  - median credit/width rises
  - share of `RRProxy < 0.8` entries materially drops
  - no reappearance of stop-formula pathology

---

## 8) Rollout Strategy

Phase A (ship first)
- MICRO strategy-aware target/stop wiring
- MICRO trailing stop
- Credit/width gate + telemetry

Phase B (only if needed after data)
- Regime-aware credit stop multiplier
- Additional per-regime micro stop multipliers

---

## 9) Risks and Mitigations

Risk: Over-filtering credit spreads -> too few trades
- Mitigation: start threshold at 0.35, add high-IV relaxation 0.30.

Risk: Trailing stop conflicts with OCO state
- Mitigation: keep trailing as software exit trigger in Phase A; avoid dynamic OCO mutation.

Risk: State drift between signal and fill
- Mitigation: persist pending strategy explicitly and clear on all failure/cancel paths.

---

## 10) Defaults Chosen (explicit)

- Credit/width threshold: `0.35` (normal), `0.30` (high-IV)
- MICRO trail pct: `0.50`
- MICRO targets/stops:
  - ITM: `35% / 35%` (existing values now wired)
  - DEBIT_FADE: `40% / 25%`
  - DEBIT_MOMENTUM: `45% / 30%`
- PROTECTIVE_PUTS remains unchanged in this phase.

