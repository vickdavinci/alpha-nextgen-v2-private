# V2.30 "All-Weather" Fix Plan

> **Date:** 4 February 2026
> **Branch:** `testing/va/stage2-backtest`
> **Status:** PROPOSAL — Awaiting approval

---

## Problem Statement

The V2.29 StartupGate + existing engine-level regime gates create a system that is **completely non-functional in bear markets** and loses 1-3 months of trading in bull markets.

### Audit Findings

| # | Issue | Severity | Impact |
|---|-------|----------|--------|
| G1 | StartupGate blocks ALL engines including hedges | **Critical** | Zero TMF/PSQ deployment in bear markets. 100% cash indefinitely. |
| G2 | EOD options `>= 40` outer gate blocks bearish PUT spreads | **High** | PUT spreads only reachable in 40-44 window. Deep bear = no options at all from primary path. |
| G3 | Yield Sleeve (SHV) not wired in main.py | **Medium** | Idle cash earns nothing. Documented but never implemented. |
| G4 | StartupGate 30-day minimum wastes 8-25% of a calendar year | **High** | Every code tweak + redeploy restarts the 30-day clock. |

### How We Got Here

The StartupGate was designed to prevent deploying capital into a breakdown. Valid intent. But the implementation used a single `return` statement (line 1207) that kills **everything** — including the system's defensive engines (hedges, bearish options) that are specifically designed to profit from breakdowns.

---

## Design Principle

> **The gate controls HOW MUCH capital to deploy. The regime controls WHAT to deploy it in.**

The existing regime engine already classifies markets into 5 states (RISK_ON → RISK_OFF) with appropriate responses for each. The startup sequence should respect that intelligence, not override it.

---

## Fix 1: Redesign StartupGate — Regime-Aware Phases

### Current (Broken)

```
REGIME_GATE (wait for 10 days of score > 60) → OBSERVATION (10 days, zero trades)
→ REDUCED (10 days, 10% max) → FULLY_ARMED
```

Problem: Only arms during bull markets. Bear = infinite wait. Hedges never deploy.

### Proposed (All-Weather)

```
INDICATOR_WARMUP (5 days, hedges + yield active)
→ OBSERVATION (5 days, hedges + yield + bearish options active)
→ REDUCED (5 days, all engines at 50% sizing)
→ FULLY_ARMED (permanent)
```

### Phase Details

| Phase | Duration | Hedges (TMF/PSQ) | Yield (SHV) | Trend (Longs) | MR (Intraday) | Options Bearish (PUTs) | Options Bullish (CALLs) |
|-------|----------|:-:|:-:|:-:|:-:|:-:|:-:|
| **INDICATOR_WARMUP** | 5 days | Yes (full) | Yes (full) | No | No | No | No |
| **OBSERVATION** | 5 days | Yes (full) | Yes (full) | No | No | Yes (50% size) | No |
| **REDUCED** | 5 days | Yes (full) | Yes (full) | Yes (50% size) | Yes (50% size) | Yes (50% size) | Yes (50% size) |
| **FULLY_ARMED** | Permanent | Yes (full) | Yes (full) | Yes (full) | Yes (full) | Yes (full) | Yes (full) |

### Key Design Decisions

1. **Hedges and yield are NEVER gated.** They are defensive by nature. Blocking TMF in a bear market is the opposite of risk management.

2. **Bearish options unlock before bullish.** PUT spreads are protective. They should be available during OBSERVATION. Bullish longs wait until REDUCED.

3. **Fixed 15-day total** (5+5+5) instead of variable 30+ days. No regime score dependency for phase progression. Time-based only — the regime engine handles direction decisions.

4. **No regime confirmation gate.** The original 10-day-consecutive-bull requirement is removed. The regime engine already has a smoothing mechanism (EMA) that prevents whipsaws. Adding a second smoothing layer on top was redundant.

5. **Kill switch does NOT reset StartupGate.** Same as V2.29 — once armed, stays armed permanently.

### Config Changes

```python
# V2.30: StartupGate — All-Weather (replaces V2.29 regime-gated approach)
STARTUP_GATE_ENABLED = True
STARTUP_GATE_WARMUP_DAYS = 5          # Phase 0: Indicators warming up
STARTUP_GATE_OBSERVATION_DAYS = 5     # Phase 1: Observe + hedges + bearish options
STARTUP_GATE_REDUCED_DAYS = 5         # Phase 2: All engines at 50% sizing
STARTUP_GATE_REDUCED_SIZE_MULT = 0.50 # Position size multiplier during REDUCED phase
```

Removed:
- `STARTUP_GATE_REGIME_DAYS` (no longer needed — no regime gate)
- `STARTUP_GATE_REGIME_MIN_SCORE` (no longer needed)
- `STARTUP_GATE_REDUCED_MAX_WEIGHT` (replaced by `REDUCED_SIZE_MULT`)

### File: `engines/core/startup_gate.py` — Rewrite

```python
class StartupGate:
    """V2.30: All-Weather startup arming sequence.

    Phases:
        INDICATOR_WARMUP: 5 days. Hedges + yield active. No directional trades.
        OBSERVATION: 5 days. Add bearish options (PUT spreads at 50% size).
        REDUCED: 5 days. All engines at 50% sizing.
        FULLY_ARMED: Permanent. No restrictions.
    """

    def __init__(self, algorithm=None):
        self.algorithm = algorithm
        self._phase: str = "INDICATOR_WARMUP"
        self._days_in_phase: int = 0

    # --- Core API ---

    def is_fully_armed(self) -> bool:
        return self._phase == "FULLY_ARMED" or not config.STARTUP_GATE_ENABLED

    def allows_hedges(self) -> bool:
        """Hedges are ALWAYS allowed from day 1."""
        return True

    def allows_yield(self) -> bool:
        """Yield sleeve is ALWAYS allowed from day 1."""
        return True

    def allows_bearish_options(self) -> bool:
        """PUT spreads allowed from OBSERVATION onward."""
        if not config.STARTUP_GATE_ENABLED:
            return True
        return self._phase in ("OBSERVATION", "REDUCED", "FULLY_ARMED")

    def allows_directional_longs(self) -> bool:
        """Trend, MR, bullish options allowed from REDUCED onward."""
        if not config.STARTUP_GATE_ENABLED:
            return True
        return self._phase in ("REDUCED", "FULLY_ARMED")

    def get_size_multiplier(self) -> float:
        """Size multiplier for current phase."""
        if not config.STARTUP_GATE_ENABLED or self._phase == "FULLY_ARMED":
            return 1.0
        if self._phase == "REDUCED":
            return config.STARTUP_GATE_REDUCED_SIZE_MULT  # 0.50
        if self._phase == "OBSERVATION":
            return config.STARTUP_GATE_REDUCED_SIZE_MULT  # 0.50 for bearish options
        return 0.0  # INDICATOR_WARMUP — no directional trades

    def get_phase(self) -> str:
        return self._phase

    # --- Daily update ---

    def end_of_day_update(self) -> str:
        """Advance phase based on calendar days. No regime dependency."""
        if not config.STARTUP_GATE_ENABLED or self._phase == "FULLY_ARMED":
            return self._phase

        self._days_in_phase += 1

        if self._phase == "INDICATOR_WARMUP":
            if self._days_in_phase >= config.STARTUP_GATE_WARMUP_DAYS:
                self._phase = "OBSERVATION"
                self._days_in_phase = 0
                self.log("STARTUP_GATE: Warmup complete → OBSERVATION (hedges + bearish options)")

        elif self._phase == "OBSERVATION":
            if self._days_in_phase >= config.STARTUP_GATE_OBSERVATION_DAYS:
                self._phase = "REDUCED"
                self._days_in_phase = 0
                self.log("STARTUP_GATE: Observation complete → REDUCED (all engines at 50%)")

        elif self._phase == "REDUCED":
            if self._days_in_phase >= config.STARTUP_GATE_REDUCED_DAYS:
                self._phase = "FULLY_ARMED"
                self.log("STARTUP_GATE: FULLY ARMED — all restrictions lifted")

        return self._phase
```

### File: `main.py` — `_on_eod_processing()` Rewrite

**Before (V2.29 — line 1196-1207):**
```python
# V2.29: Single return blocks EVERYTHING
if not self.startup_gate.is_fully_armed():
    self.startup_gate.end_of_day_update(regime_state.smoothed_score)
    if not self.startup_gate.is_trading_allowed():
        return  # ← Kills hedges, options, everything
```

**After (V2.30 — granular gating):**
```python
# V2.30: Update startup gate (time-based, no regime dependency)
if not self.startup_gate.is_fully_armed():
    self.startup_gate.end_of_day_update()

# 2. Update Capital Engine (always — hedges need it)
capital_state = self.capital_engine.end_of_day_update(total_equity)

# 3. Generate Trend signals (if gate allows directional longs)
if self.startup_gate.allows_directional_longs():
    self._generate_trend_signals_eod(regime_state)

# 4. Generate Options signals (regime-gated, see Fix 2)
self._generate_options_signals_gated(regime_state, capital_state)

# 5. Generate Hedge signals (ALWAYS — never gated)
self._generate_hedge_signals(regime_state)

# 6. Generate Yield signals (ALWAYS — never gated)
self._generate_yield_signals(capital_state)
```

### File: `main.py` — `OnData()` Intraday Rewrite

**Before (V2.29 — line 379):**
```python
startup_allows_trading = self.startup_gate.is_trading_allowed()
if mr_window_open and ... and startup_allows_trading:
    self._scan_mr_signals(data)
```

**After (V2.30):**
```python
# MR: requires directional longs permission
if mr_window_open and risk_result.can_enter_intraday and self._governor_scale > 0.0:
    if self.startup_gate.allows_directional_longs():
        self._scan_mr_signals(data)

# Options: split by direction
if (mr_window_open and risk_result.can_enter_intraday and risk_result.can_enter_options
        and self._governor_scale >= config.GOVERNOR_INTRADAY_OPTIONS_MIN_SCALE):
    # Bearish options (PUT spreads) allowed earlier than bullish
    self._scan_options_signals_gated(data)
```

---

## Fix 2: Remove EOD Options `>= 40` Outer Gate

### Current (Broken) — `main.py:1219`

```python
if regime_state.smoothed_score >= 40:
    self._generate_options_signals(regime_state, capital_state, options_size_mult)
```

This outer gate makes the inner PUT direction (`regime < 45`) nearly unreachable. Only 40-44 produces EOD PUTs.

### Proposed

Replace the single gated call with direction-aware gating:

```python
def _generate_options_signals_gated(self, regime_state, capital_state):
    """V2.30: Direction-aware options gating."""
    is_cold_start = self.cold_start_engine.is_cold_start_active()
    size_mult = config.OPTIONS_COLD_START_MULTIPLIER if is_cold_start else 1.0

    # Apply startup gate size multiplier
    if not self.startup_gate.is_fully_armed():
        size_mult *= self.startup_gate.get_size_multiplier()

    regime_score = regime_state.smoothed_score

    # Bullish path (CALL spreads): regime > 60, requires directional longs permission
    if regime_score > config.SPREAD_REGIME_BULLISH:
        if self.startup_gate.allows_directional_longs():
            self._generate_options_signals(regime_state, capital_state, size_mult)
        return

    # Bearish path (PUT spreads): regime < 45, requires bearish options permission
    if regime_score < config.SPREAD_REGIME_BEARISH:
        if self.startup_gate.allows_bearish_options():
            self._generate_options_signals(regime_state, capital_state, size_mult)
        return

    # Neutral (45-60): No options trade (by design)
```

Similarly for `_scan_options_signals_gated()` in the intraday path:

```python
def _scan_options_signals_gated(self, data):
    """V2.30: Gate intraday options by direction."""
    regime_score = self.regime_engine.get_previous_score()

    # Determine if current regime direction is allowed by startup gate
    if regime_score > config.SPREAD_REGIME_BULLISH:
        if not self.startup_gate.allows_directional_longs():
            return
    elif regime_score < config.SPREAD_REGIME_BEARISH:
        if not self.startup_gate.allows_bearish_options():
            return
    # Neutral: _scan_options_signals handles the no-trade return internally

    # Intraday micro regime is VIX-driven, not macro-regime-driven
    # Allow it if EITHER bearish or directional longs is permitted
    self._scan_options_signals(data)
```

**Net effect:** PUT spreads now fire at any regime below 45, not just 40-44. A regime score of 20 correctly produces PUT direction options.

---

## Fix 3: Wire Yield Sleeve (SHV)

### Current State

- `engines/satellite/yield_sleeve.py` — File deleted from disk (only `.pyc` cache remains)
- Zero references to `yield_sleeve` or `self.shv` in main.py
- SHV is mentioned in comments as a traded symbol but never subscribed or traded

### Proposed: Inline SHV Logic (No Separate Engine)

The yield sleeve concept is simple: park idle cash in SHV (short-term treasury ETF) to earn ~5% yield instead of 0%. No need for a separate engine file.

**main.py `Initialize()`:**
```python
self.shv = self.AddEquity("SHV", Resolution.Daily).Symbol
```

**New method in main.py:**
```python
def _generate_yield_signals(self, capital_state: CapitalState) -> None:
    """V2.30: Park idle cash in SHV for yield.

    Logic: Any cash not allocated to other engines → SHV.
    SHV is liquidated first when other engines need capital.
    Lockbox amount is excluded (never traded).
    """
    total_equity = self.Portfolio.TotalPortfolioValue
    if total_equity <= 0:
        return

    # Calculate total allocated to all other positions
    allocated = 0.0
    for kvp in self.Portfolio:
        if kvp.Value.Invested and kvp.Value.Symbol != self.shv:
            allocated += abs(kvp.Value.HoldingsValue)

    # Idle cash = total - allocated - lockbox
    lockbox = capital_state.lockbox_amount if hasattr(capital_state, 'lockbox_amount') else 0.0
    idle_cash = total_equity - allocated - lockbox
    idle_pct = idle_cash / total_equity if total_equity > 0 else 0.0

    # Only park if > 5% idle (avoid churn from small fluctuations)
    current_shv_pct = self.Portfolio[self.shv].HoldingsValue / total_equity
    target_shv_pct = idle_pct if idle_pct > 0.05 else 0.0

    # Only rebalance if difference > 3% (avoid daily micro-rebalancing)
    if abs(target_shv_pct - current_shv_pct) > 0.03:
        signal = TargetWeight(
            symbol="SHV",
            target_weight=target_shv_pct,
            source="YIELD",
            urgency=Urgency.EOD,
            reason=f"Yield: Idle={idle_pct:.1%} → SHV={target_shv_pct:.1%}",
        )
        self.portfolio_router.receive_signal(signal)
```

**Portfolio Router** needs to treat SHV as lowest priority — liquidated first when other engines need capital. This is likely already handled by the `YIELD` source having lowest priority in the router's signal aggregation.

### Alternative: Defer SHV to a Later Version

If you want to keep V2.30 focused on the gate fixes, SHV can wait. It's a yield optimization, not a safety fix. The three gate issues (G1, G2, G4) are the urgent ones.

---

## Fix 4: Warm Entry Gate

### Current — `main.py:1047`
```python
if not self.startup_gate.is_trading_allowed():
    return
```

### Proposed
```python
if not self.startup_gate.allows_directional_longs():
    return
```

Uses the same granular API. Warm entry is a directional long (QLD/SSO), so it requires the `REDUCED` phase.

---

## Calendar Year Trade Loss Comparison

### V2.29 StartupGate (Current)

| Market | Gate Clears | Days Lost | Trades Lost |
|--------|-------------|-----------|-------------|
| 2017 Bull | ~Day 22 | 30+ | Miss January trend entries |
| 2022 Bear | Never | 252 | **Miss ALL hedges, ALL puts, entire year** |
| 2015 Chop | ~Day 60-90 | 60-90 | Miss Q1-Q2, regime resets repeatedly |
| Code redeploy mid-year | +30 days each time | 30 × N deploys | Compounds with every tweak |

### V2.30 All-Weather (Proposed)

| Market | Phase Progression | Days to Full | What Fires During Ramp |
|--------|-------------------|-------------|------------------------|
| 2017 Bull | 5→10→15 | 15 | Hedges day 1, bullish options day 11, trend day 11 (50%), full day 16 |
| 2022 Bear | 5→10→15 | 15 | **Hedges day 1, PUT spreads day 6 (50%), full hedges + puts day 16** |
| 2015 Chop | 5→10→15 | 15 | Hedges day 1, regime determines direction from day 6 |
| Code redeploy | 5→10→15 | 15 | Fixed 15 days vs 30+ variable. Hedges immediate. |

**Key difference:** In 2022, V2.29 earns 0% (cash). V2.30 deploys TMF from day 1 (TMF gained ~20% in Q1 2022) and PUT spreads from day 6.

---

## Files Modified Summary

| File | Fix | Change |
|------|-----|--------|
| `engines/core/startup_gate.py` | F1 | Rewrite — phase-based with granular `allows_*()` API |
| `config.py` | F1 | Replace 6 params with 4 new params |
| `main.py` `_on_eod_processing()` | F1 | Remove single `return`, add granular engine gating |
| `main.py` `OnData()` | F1 | Split intraday gating by direction |
| `main.py` `_on_warm_entry_check()` | F4 | Use `allows_directional_longs()` |
| `main.py` `_generate_options_signals` | F2 | Remove `>= 40` outer gate, add direction-aware wrapper |
| `main.py` `_scan_options_signals` | F2 | Add direction-aware wrapper |
| `main.py` `Initialize()` | F3 | Add SHV subscription + yield signal method |
| `persistence/state_manager.py` | F1 | Update StartupGate save/load (simpler state) |

**Files NOT modified:**
- `engines/core/cold_start_engine.py` — Zero changes (same as V2.29)
- `engines/core/regime_engine.py` — Zero changes
- `engines/core/risk_engine.py` — Zero changes
- `engines/satellite/hedge_engine.py` — Zero changes (already correct)
- `engines/satellite/options_engine.py` — Zero changes (direction logic already correct)
- `engines/satellite/mean_reversion_engine.py` — Zero changes (internal regime gate stays)

---

## Risk Assessment

| Risk | Mitigation |
|------|------------|
| Hedges deploy on day 1 before indicators are warm | MA200/ADX take ~200 bars to warm. Hedge engine uses regime score which warms in ~5 days. During INDICATOR_WARMUP, regime defaults to 50 (NEUTRAL) = no hedges. Hedges only fire when regime drops below 40, which requires real data. |
| PUT spreads on day 6 with incomplete data | Options engine requires `qqq_adx.IsReady` and `qqq_sma200.IsReady`. These guards remain. If indicators aren't ready, options silently skip. |
| Reduced sizing (50%) may still be too aggressive | 50% of 20% options allocation = 10% max exposure. Comparable to current MR sizing. Acceptable risk. |
| SHV liquidation timing when capital needed | SHV is daily resolution, highly liquid, ~0 spread. Router processes YIELD signals at lowest priority. |

---

## Verification Plan

```bash
# 1. Unit tests
pytest tests/ -x --tb=short

# 2. Bear market test — Q1 2022
# Expected: TMF deploys from day 6, PUT spreads from day 11
# V2.29 comparison: 0 trades (cash all year)
./scripts/qc_backtest.sh "V2.30-Q1-2022-bear" --open

# 3. Bull market test — 2017
# Expected: Full trading by day 16. Hedges none (high regime).
# Trend entries start day 11 at 50%, full by day 16.
./scripts/qc_backtest.sh "V2.30-2017-bull" --open

# 4. Choppy market test — 2015
# Expected: Regime oscillates, hedges adjust dynamically.
# No 30-day regime confirmation stall.
./scripts/qc_backtest.sh "V2.30-2015-chop" --open

# 5. Key log checks:
# - "STARTUP_GATE: Warmup complete → OBSERVATION" on day 5
# - "STARTUP_GATE: Observation complete → REDUCED" on day 10
# - "STARTUP_GATE: FULLY ARMED" on day 15
# - TMF/PSQ fills in bear market during INDICATOR_WARMUP
# - PUT spread fills during OBSERVATION phase
# - No longs during INDICATOR_WARMUP or OBSERVATION
```

---

## Decision Point: SHV Scope

The SHV yield sleeve (Fix 3) is independent of the gate fixes (Fix 1, 2, 4). Options:

- **Option A:** Include SHV in V2.30 (complete all-weather package)
- **Option B:** Ship gate fixes as V2.30, defer SHV to V2.31 (smaller change set, faster to verify)

Recommendation: **Option B.** The gate fixes are safety-critical. SHV is a yield optimization. Ship them separately to isolate risk.
