# V6.22 Regime Bias Fix Plan

## Objective
Fix macro and VASS regime bias with minimal code changes.

- `P1`: Predict regime transitions early when possible.
- `P2`: If prediction misses, switch mode quickly and route to correct direction.
- `P3`: Exit wrong-way positions cleanly during transition and ride next wave.

---

## Design (Minimal-Change Architecture)

Use a two-layer regime decision:

1. `Base Regime` (existing macro engine, slow/stable)
2. `Shock Overlay` (fast VIX/price stress flags, temporary override)

Final mode = `Base Regime + Shock Overlay`.

No engine rewrite required. Implement as resolver/routing precedence and parameterized gates.

---

## Phase 1: Regime Bias Core Fix (P1 + P2)

### 1) Add fast overlay states (lightweight)

- `EARLY_STRESS`
- `STRESS`
- `RECOVERY`

Inputs (already available in system):
- VIX level
- VIX 5-day change
- QQQ vs MA20
- Optional existing breadth proxy (if already computed)

### 2) Resolver precedence update

Apply priority order:
1. Overlay hard risk gate
2. Macro + conviction resolver
3. Strategy selection

Rule: in `STRESS`, block new bullish spread entries regardless of macro lag.

### 3) VASS directional slot shaping

Dynamic spread caps by overlay:

- `STRESS`: `MAX_BULLISH_SPREADS=0`, `MAX_BEARISH_SPREADS=3`
- `EARLY_STRESS`: `MAX_BULLISH_SPREADS=1`, `MAX_BEARISH_SPREADS=3`
- `RISK_ON`: normal caps

This is parameter-driven and uses existing multi-spread infra.

---

## Phase 2: Transition Exit Control (P3)

### 4) Transition exit priority

When overlay escalates to `STRESS`:
- close or reduce bullish spreads first
- preserve/allow bearish structures

Priority order for exits:
1. Stress-transition exits
2. Assignment/ITM risk exits
3. DTE exits
4. Neutrality exits

### 5) Close reliability escalation

Keep existing retry queue; add threshold:
- if spread close cancel count >= 2, trigger sequential close fallback immediately.

---

## Phase 3: Participation Rebalance (Dir=None + Bear Access)

### 6) Reduce over-blocking

- In `CAUTION_LOW`, allow half-size directional entries instead of hard no-trade.
- Slightly reduce `QQQ_FLAT` threshold.
- Keep stress CALL gates enabled.

### 7) Keep VASS alive in stress

- Keep win-rate gate soft (`VASS_WIN_RATE_HARD_BLOCK=False`).
- Reduce shutoff scale to conservative participation instead of full block.

### 8) Relax bear-put gate conditionally

For `STRESS` / high-VIX windows:
- relax `BEAR_PUT_ENTRY_MIN_OTM_PCT` (risk-scaled, not unrestricted).

---

## Parameter Set (Initial Defaults)

Use these as first-pass defaults for V6.22:

### Overlay thresholds
- `STRESS_BLOCK_BULL_CALL_VIX = 21.0`
- `STRESS_BLOCK_BULL_CALL_VIX_5D = 0.18`
- `EARLY_STRESS_VIX_LOW = 16.0`
- `EARLY_STRESS_VIX_HIGH = 18.0`

### VASS directional caps
- `MAX_BULLISH_SPREADS_STRESS = 0`
- `MAX_BULLISH_SPREADS_EARLY_STRESS = 1`
- `MAX_BEARISH_SPREADS_STRESS = 3`

### Win-rate gate behavior
- `VASS_WIN_RATE_HARD_BLOCK = False`
- `VASS_WIN_RATE_SHUTOFF_SCALE = 0.40`

### Bear-put participation
- `BEAR_PUT_ENTRY_MIN_OTM_PCT_STRESS = 0.010`
- `BEAR_PUT_ENTRY_MIN_OTM_PCT_NORMAL = existing baseline`

### Micro participation
- `QQQ_NOISE_THRESHOLD`: reduce by ~10-15% from current
- `CAUTION_LOW`: enable reduced-size trade path (`size_mult = 0.50`)

### Close reliability
- `SPREAD_CLOSE_CANCEL_ESCALATION_COUNT = 2`
- `SPREAD_CLOSE_RETRY_INTERVAL_MIN = 5` (keep current)

---

## Backtest Sequence

Run in this order:

1. `2022 Dec-Feb` (bear transition)
2. `2018 Q4` (choppy stress)
3. `2015 Aug crash window` (shock handling)
4. `2017 bull` (regression check)

---

## Success Criteria (Pass/Fail)

### Direction and bias
- Bear windows: bullish spread share `< 30%`
- Bear windows: bearish spread share materially higher than current baseline

### Risk and exits
- Large spread tail-loss events reduced by `> 40%`
- Canceled-close-to-expiry incidents ~= `0`

### Participation
- `Dir=None` reduced from current baseline by `>= 20%` relative
- `BEAR_PUT_ASSIGNMENT_GATE` rejection share reduced by `>= 30%` relative

### Performance guardrails
- 2022/2018 drawdown improves vs V6.21 baseline
- 2017 bull PnL degradation `<= 15%`

---

## Implementation Notes

- Keep changes localized to resolver/gating/routing order and config.
- Do not add new major modules in this phase.
- Log all overlay transitions and spread-cap decisions with explicit reason codes.
- Validate each phase independently before combining further optimization.

