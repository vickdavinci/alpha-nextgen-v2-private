# Protective Puts RCA & Fix Plan

## Root Cause Analysis Summary

PROTECTIVE_PUTS is structurally broken as a hedge due to 5 compounding design flaws. It is negative-EV in every market regime — bear markets, bull sell-offs, and flash crashes.

### RC-1: Intraday Force Close Destroys the Hedge Thesis
- Force-closed at **15:25 ET daily** (same as DEBIT_FADE, ITM_MOMENTUM)
- Max hold time: ~5.5 hours. Cannot protect against **overnight gaps** — the actual risk for a portfolio holding QLD/SSO/UGL/UCO overnight
- Cannot capture multi-day crash continuation (Mar 2020: SPY -3-5%/day × 5 days)
- Theta decay on a 3-7 DTE OTM put consumed intraday is pure cost

### RC-2: OTM Delta (0.30) is Wrong for Crisis Protection
- 0.30Δ put = strike ~2-3% below QQQ → requires QQQ to fall **>3% intraday** to profit
- QQQ falls >3% intraday only ~2-3 times per year
- Can select 0.20Δ puts (4-5% OTM) — even less responsive
- "Worst of both worlds": too expensive for tail hedging, too far OTM for intraday protection

### RC-3: Position Size Too Small to Hedge Anything
- Budget: **3% of portfolio** = $1,500 on $50K
- Hard cap: **5 contracts** regardless of portfolio size
- Portfolio long exposure (QLD 2x + SSO 2x + options): **$40K+** beta-adjusted
- Even best-case put P&L ($150-$300) is meaningless against $800-$1,600 portfolio loss

### RC-4: Stop Loss (35%) Fires Before Crisis Payoff, No Trailing Stop
- 35% hard stop triggers on normal intraday bounces during volatile sessions
- VIX 30+ → put prices swing 20-30% on a 1% QQQ bounce → stopped out
- **No trailing stop** — cannot ride crash while protecting gains
- 60% profit target **caps upside** on the rare tail event where puts should run 100-500%+

### RC-5: Regime Detection Lag — Buying Insurance After the Fire
- Triggers require VIX > 25 **and** rising 5-10%+ (FULL_PANIC/CRASH)
- At VIX 30, a 0.30Δ put costs 5-8× more than at VIX 15
- V9.2 QQQ-DOWN gate for FULL_PANIC waits for QQQ to already be falling before buying
- System pays maximum IV premium for protection that should have been bought at VIX 15-18

---

## Design Principles for the Fix

1. **Protective puts are insurance, not momentum trades.** They must be able to hold overnight and capture multi-day moves. An intraday-only hedge is an oxymoron.

2. **Buy insurance before the crisis, not during it.** Entry trigger should fire when VIX is rising through 18-22 (cheap), not after it exceeds 25 (expensive).

3. **Use delta that responds immediately.** 0.45-0.55Δ (near-ATM) puts move dollar-for-dollar with the market. OTM puts need a massive move to pay off.

4. **Size to actually hedge something.** The position must offset 30-50% of portfolio long beta, not 5-10%.

5. **Let winners run in a crisis.** No hard profit target. Use an aggressive trailing stop so the put rides a multi-day crash.

6. **Accept that insurance has a cost.** Most protective put entries will lose money. The expected loss is the cost of portfolio insurance. The payoff structure is asymmetric: small frequent losses, rare large gains.

---

## Target Framework: Swing-Mode Protective Puts

### Architecture Decision
Move PROTECTIVE_PUTS from the **MICRO intraday path** (force close at 15:25) to the **swing single-leg path** (hold for DTE-based lifecycle). This reuses the existing `_swing_position` slot and `check_exit_signals()` exit logic.

The MICRO regime engine still **generates the signal**, but the execution path routes through swing mode infrastructure:
- Tracked in `_swing_position` (not `_intraday_position`)
- No intraday force close — position held until exit signal or DTE
- Exit via `check_exit_signals()` with protective-specific trailing stop
- OCO pairs set with protective-specific stop/trail

### Entry Trigger: Two-Tier System

**Tier 1 — Pre-Crisis (Early Warning)**
- VIX crosses above 20 from below AND VIX direction is RISING or SPIKING
- OR: Regime transitions from RISK_ON/NEUTRAL → CAUTIOUS/DEFENSIVE
- **Purpose:** Buy protection when IV is still affordable (VIX 18-22)
- Delta: 0.45 (near-ATM, immediate response)
- DTE: 14-21 (time for multi-day move, lower theta/day)
- Size: 5% of portfolio

**Tier 2 — Active Crisis (Current Behavior, Improved)**
- Existing crisis regimes: FULL_PANIC (QQQ DOWN), CRASH, VOLATILE
- Existing caution regimes with micro_score < 0: RISK_OFF_LOW, BREAKING, UNSTABLE
- **Purpose:** Add protection if Tier 1 was not already in place
- Delta: 0.50 (ATM, maximum responsiveness)
- DTE: 7-14 (shorter horizon, crisis already underway)
- Size: 5% of portfolio
- **Skipped if Tier 1 position already active** (no stacking)

### Exit Logic: Trailing Stop, No Profit Cap

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Stop loss | 40% | Wider than current 35% — insurance must survive intraday noise |
| Profit target | **None** (removed) | Let winners run during crisis — this is the whole point |
| Trail trigger | +25% gain | Start trailing after meaningful profit |
| Trail percentage | 35% from peak | Lock in ~65% of peak gain, allow room for continuation |
| DTE exit | 2 DTE | Close before expiration week gamma risk |
| Time-based exit | **None** | No intraday force close — hold until signal |

### Position Sizing: Hedge-Ratio Based

Replace fixed 3%/5-contract cap with hedge-ratio sizing:

```
hedge_budget = portfolio_value × PROTECTIVE_PUTS_SIZE_PCT (5%)
contracts = hedge_budget / (premium × 100)
contracts = min(contracts, PROTECTIVE_PUTS_MAX_CONTRACTS)  # Safety cap at 10
```

At $50K portfolio:
- Budget: $2,500 (5%)
- Premium at VIX 20 (Tier 1, 0.45Δ, 14 DTE): ~$2.50 → 10 contracts → capped at 10
- Premium at VIX 30 (Tier 2, 0.50Δ, 10 DTE): ~$5.00 → 5 contracts
- Notional hedged (10 contracts × 100 × 0.45Δ): ~$450/point of QQQ
- QQQ drops 3%: put gains ~$1,350 vs portfolio loss ~$2,000 = **67% hedge**

---

## Required Config Changes

### 1. Entry trigger parameters (NEW)
```python
# V11: Pre-crisis trigger (Tier 1)
PROTECTIVE_PUTS_PRECRISIS_ENABLED = True          # Enable early-warning entries
PROTECTIVE_PUTS_PRECRISIS_VIX_THRESHOLD = 20.0    # VIX cross-above trigger
PROTECTIVE_PUTS_PRECRISIS_REGIME_TRIGGER = True    # Also trigger on regime downgrade
PROTECTIVE_PUTS_PRECRISIS_DTE_MIN = 14             # Longer DTE for time value
PROTECTIVE_PUTS_PRECRISIS_DTE_MAX = 21
PROTECTIVE_PUTS_PRECRISIS_DELTA_TARGET = 0.45      # Near-ATM
PROTECTIVE_PUTS_PRECRISIS_DELTA_TOLERANCE = 0.10   # Accept 0.35-0.55
```

### 2. Crisis trigger parameters (MODIFIED)
```python
# V11: Active crisis trigger (Tier 2) — existing keys, new values
PROTECTIVE_PUTS_DTE_MIN = 7       # Was 3 → longer for multi-day hold
PROTECTIVE_PUTS_DTE_MAX = 14      # Was 7 → wider window
PROTECTIVE_PUTS_DELTA_TARGET = 0.50  # Was 0.30 → ATM for immediate response
PROTECTIVE_PUTS_DELTA_TOLERANCE = 0.10  # Accept 0.40-0.60
```

### 3. Position sizing (MODIFIED)
```python
PROTECTIVE_PUTS_SIZE_PCT = 0.05           # Was 0.03 → 5% for meaningful hedge
PROTECTIVE_PUTS_MAX_CONTRACTS = 10        # Was 5 → larger for proper hedge ratio
```

### 4. Exit parameters (MODIFIED + NEW)
```python
PROTECTIVE_PUTS_STOP_PCT = 0.40           # Was 0.35 → wider to survive intraday noise
PROTECTIVE_PUTS_PROFIT_TARGET = None      # Was 0.60 → REMOVED, let winners run
PROTECTIVE_PUTS_TRAIL_TRIGGER = 0.25      # NEW: Start trailing at +25%
PROTECTIVE_PUTS_TRAIL_PCT = 0.35          # NEW: Trail 35% from peak
PROTECTIVE_PUTS_DTE_EXIT = 2              # NEW: Close at 2 DTE
```

### 5. Swing mode routing (NEW)
```python
PROTECTIVE_PUTS_USE_SWING_MODE = True     # V11: Route through swing lifecycle
PROTECTIVE_PUTS_BLOCK_IF_SWING_EXISTS = True  # Don't stack on top of VASS swing
```

---

## Code Changes by Area

### A) Regime Trigger Expansion (Options Engine)
File: `engines/satellite/options_engine.py`

**A1. Add Tier 1 Pre-Crisis detection in `recommend_strategy_and_direction()`**
- Before the existing crisis_regimes check (line 1300), add pre-crisis detection:
  ```
  IF vix_current crosses above PROTECTIVE_PUTS_PRECRISIS_VIX_THRESHOLD (20)
     AND vix_direction in (RISING, SPIKING)
     AND no existing swing protective put position
  THEN return PROTECTIVE_PUTS with PRE_CRISIS reason
  ```
- Also trigger on regime transition: if previous regime was RISK_ON/NEUTRAL and current is CAUTIOUS/DEFENSIVE
- Return a new flag or reason string to distinguish Tier 1 vs Tier 2 for parameter selection downstream

**A2. Keep existing crisis routing (lines 1300-1337) for Tier 2**
- No structural change to FULL_PANIC/CRASH/VOLATILE/caution routing
- Tier 2 skips if a Tier 1 protective put already exists (check `_swing_position` entry_strategy == PROTECTIVE_PUTS)

### B) Swing Mode Routing (Options Engine)
File: `engines/satellite/options_engine.py`

**B1. Route PROTECTIVE_PUTS through swing path in `check_intraday_entry_signal()`**
- At lines 7685-7717 where `is_protective_put` is set:
  - If `PROTECTIVE_PUTS_USE_SWING_MODE` is True:
    - Set `is_swing_protective = True`
    - Skip the intraday flow entirely
    - Route to a new method `check_swing_protective_entry()` that:
      1. Checks if `_swing_position` already exists (skip if so, unless it's a VASS position — then check `PROTECTIVE_PUTS_BLOCK_IF_SWING_EXISTS`)
      2. Selects contract with protective-specific DTE/delta (Tier 1 or Tier 2 params based on reason string)
      3. Sizes using `PROTECTIVE_PUTS_SIZE_PCT` and `PROTECTIVE_PUTS_MAX_CONTRACTS`
      4. Returns TargetWeight with `source="OPT_SWING"` (not `OPT_INTRADAY`)
      5. Sets `_pending_intraday_entry = False` (swing path, not intraday)

**B2. Fill registration routes to `_swing_position`**
- In `register_fill()` (line 8545+), when `entry_strategy == "PROTECTIVE_PUTS"`:
  - Store in `_swing_position` (not `_intraday_position`)
  - No intraday force-close timer
  - Set stop/target from protective-specific config

### C) Exit Logic (Options Engine)
File: `engines/satellite/options_engine.py`

**C1. Add protective puts to `_get_trail_config()` (line 7069)**
- Currently returns `None` for protective puts (no trailing stop)
- Add branch:
  ```python
  if strategy == "PROTECTIVE_PUTS":
      return (
          float(getattr(config, "PROTECTIVE_PUTS_TRAIL_TRIGGER", 0.25)),
          float(getattr(config, "PROTECTIVE_PUTS_TRAIL_PCT", 0.35)),
      )
  ```

**C2. Add protective puts to `_get_intraday_exit_profile()` (line 7048)**
- Currently falls through to default 60% target
- Add branch that returns `(None, PROTECTIVE_PUTS_STOP_PCT)` — no profit target, only stop floor
- Note: Since protective puts now route through `check_exit_signals()` (swing path), this may need adjustment — the swing exit path uses `pos.target_price` and `pos.stop_price` directly, not the intraday exit profile

**C3. Modify `check_exit_signals()` (line 7091) for protective puts**
- Currently checks profit target hit at `pos.target_price`:
  - For PROTECTIVE_PUTS, skip profit target check (or set target_price very high at registration time)
- Trailing stop already handled via `_get_trail_config()` at line 7136:
  - Currently **explicitly excludes** PROTECTIVE_PUTS: `if pos.entry_strategy.upper() != "PROTECTIVE_PUTS"`
  - **REMOVE this exclusion** so trailing stop applies to protective puts
- DTE exit at `DTE <= PROTECTIVE_PUTS_DTE_EXIT` (2)

**C4. Exempt protective puts from intraday force close**
- In `check_intraday_force_exit()` (line 8161):
  - This only checks `_intraday_position`, so if protective puts are routed to `_swing_position`, this is **automatically solved** — no code change needed
  - Verify: ensure no other force-close path touches `_swing_position` with intraday timing

### D) Contract Selection (main.py)

**D1. Add protective-specific DTE/delta routing**
- In `_select_intraday_option_contract()` (or new equivalent for swing protective):
  - Tier 1 (pre-crisis): DTE 14-21, delta 0.45 ± 0.10
  - Tier 2 (active crisis): DTE 7-14, delta 0.50 ± 0.10
  - Note: Swing spreads use `_build_spread_candidate_contracts()` for their chain filtering — protective puts need single-leg selection, which exists in `_select_intraday_option_contract()`
  - May reuse the same function with protective-specific DTE/delta overrides (already parameterized)

### E) Telemetry

**E1. Entry logging**
```
PROTECTIVE_PUT_ENTRY: Tier={1|2} | VIX={vix:.1f} | Regime={regime} |
Δ={delta:.2f} K={strike} DTE={dte} | Contracts={n} | Premium=${premium:.2f}
```

**E2. VIX cross detection logging**
```
PROTECTIVE_PUT_TRIGGER: VIX crossed {threshold} (was {prev:.1f} → now {curr:.1f}) |
Direction={vix_dir} | Action={ENTER|SKIP_EXISTS|SKIP_DISABLED}
```

**E3. Exit logging**
```
PROTECTIVE_PUT_EXIT: Reason={TRAIL_STOP|DTE_EXIT|STOP_LOSS} |
P&L={pnl:+.1%} | Held={days}d | VIX_entry={vix_entry:.1f} → VIX_exit={vix_exit:.1f}
```

---

## What Does NOT Change (Verified Safe)

| Component | Why Safe |
|-----------|----------|
| **OCO Manager** | OCO pairs are strategy-agnostic — work for any single-leg position |
| **Portfolio Router** | Routes `OPT_SWING` source same as VASS single-leg entries |
| **Kill Switch** | Kill switch tiers are position-type agnostic. Tier 2+ liquidates all options including protective puts — this is correct behavior (system in distress) |
| **State Persistence** | `OptionsPosition` serialization includes `entry_strategy` string — "PROTECTIVE_PUTS" persists and restores correctly |
| **Daily Counters** | Counters track by MODE (SWING/INTRADAY) + DIRECTION (CALL/PUT), not strategy name |
| **VASS Swing Spreads** | Spreads use `_spread_positions` list, protective puts use `_swing_position` — different slots, no conflict |
| **Existing intraday strategies** | DEBIT_FADE, ITM_MOMENTUM unaffected — protective puts exits their code path entirely |

---

## Interaction with Existing Swing Position

The engine has separate tracking slots:
- `_swing_position` — single-leg swing (currently unused in practice, VASS uses spreads)
- `_spread_positions` — VASS swing spreads
- `_intraday_position` — MICRO intraday

Protective puts would use `_swing_position`. Potential conflict:
- If a VASS single-leg swing entry exists in `_swing_position` → block protective put (configurable via `PROTECTIVE_PUTS_BLOCK_IF_SWING_EXISTS`)
- VASS spreads in `_spread_positions` → **no conflict** (different slot)
- Intraday position in `_intraday_position` → **no conflict** (different slot)

---

## Phased Implementation

### Phase 1 — Swing Mode Migration + Exit Fix (Core Fix)
1. Add config parameters (sections 2-5 above)
2. Route PROTECTIVE_PUTS to `_swing_position` via new `check_swing_protective_entry()` method
3. Remove PROTECTIVE_PUTS exclusion from trailing stop in `check_exit_signals()`
4. Set `target_price` to very high value (effectively no profit cap) at fill registration
5. Add DTE exit at 2 DTE for protective puts
6. Verify: `check_intraday_force_exit()` does NOT touch `_swing_position` (automatic exemption)

**Exit criteria:** Protective puts hold overnight in backtest, trail gains, exit via trail stop or DTE.

### Phase 2 — Delta + Sizing Fix
1. Change delta target from 0.30 → 0.50 (Tier 2) / 0.45 (Tier 1)
2. Increase size from 3% → 5%, max contracts from 5 → 10
3. Widen stop from 35% → 40%

**Exit criteria:** Protective puts show meaningful P&L offset during 2022 bear market backtest.

### Phase 3 — Pre-Crisis Trigger (Tier 1)
1. Add VIX cross-above detection (VIX crosses 20 from below with RISING direction)
2. Add regime transition trigger (RISK_ON/NEUTRAL → CAUTIOUS/DEFENSIVE)
3. Tier 1 uses longer DTE (14-21) and slightly lower delta (0.45)
4. Skip Tier 2 if Tier 1 position already active

**Exit criteria:** Protective puts entered at lower IV (VIX 18-22) show better cost basis than crisis-only entries.

### Phase 4 — Telemetry + Backtest Validation
1. Add all telemetry (section E)
2. Backtest windows:
   - **Bear**: Dec 2021 - Feb 2022 (sustained bear, VIX 25-35)
   - **Bull sell-off**: Jul-Sep 2017 (late-summer correction)
   - **Flash crash**: Aug 2015 (China deval, VIX spike to 40+)
   - **Choppy high-VIX**: Q4 2018 (trade war volatility)
3. Measure:
   - Net protective put P&L (expected: small negative in bull, positive in bear)
   - Portfolio drawdown WITH vs WITHOUT protective puts
   - Cost of insurance (total premium spent / total portfolio value)
   - Number of entries by tier
   - Average hold time and average P&L per tier

---

## Expected Behavior After Fix

| Market Condition | Before (Broken) | After (Fixed) |
|-----------------|-----------------|---------------|
| **Sustained bear** | Daily buy-and-close cycle. Cumulative theta drag. Never captures multi-day moves. Net: deep negative. | Tier 1 enters early (VIX ~20). Holds multi-day. Trails gains. Captures 3-5 day sell-off legs. Net: positive or break-even. |
| **Bull sell-off (2-3 days)** | Buys after VIX >25 at max IV. Force closes same day. Misses overnight continuation. Net: negative. | Tier 1 enters when VIX crosses 20. Holds through sell-off. Trails gains on recovery. Net: small positive or small negative (acceptable insurance cost). |
| **Flash crash (intraday)** | Buys 0.30Δ at max IV. 60% profit cap limits upside. Net: small positive capped. | Buys 0.50Δ at higher responsiveness. No profit cap — trails. Net: significant positive. |
| **False alarm (VIX spikes then reverses)** | Buys at max IV, stopped out. Net: -35% on position (-1% of portfolio). | Tier 1 buys earlier at lower IV. Wider 40% stop + longer DTE = more time for thesis to play out. Net: -20 to -30% on position (-1 to -1.5% of portfolio). **This is the expected cost of insurance.** |
| **Low-VIX bull market** | Never triggers (VIX <25). No protection. | Tier 1 doesn't trigger either (VIX <20). No protection needed — bull market. Net: zero cost. |

---

## Risk Assessment

| Risk | Mitigation |
|------|------------|
| **Insurance drag in extended bull** | Tier 1 trigger (VIX > 20) is infrequent in bull markets. 2023 had VIX >20 only ~15 days. Cost: ~3-5 entries × 0.5% loss = 1.5-2.5% annual drag. Acceptable for tail protection. |
| **Stacking with VASS spreads** | `PROTECTIVE_PUTS_BLOCK_IF_SWING_EXISTS` prevents if single-leg swing exists. Spreads use different slot — no conflict. Total options exposure capped by existing margin utilization check (90%). |
| **Protective put held through recovery** | Trailing stop locks in gains. DTE exit at 2 prevents expiration risk. Stop at -40% prevents total loss. |
| **Governor at 0% blocks entry** | Already solved: protective puts bypass Governor gate (line 7854). No change needed. |
| **State persistence of swing protective** | `_swing_position` already persisted and restored. Entry strategy string "PROTECTIVE_PUTS" round-trips correctly. |

---

## Acceptance Criteria

Go only if all are true:
1. Protective puts hold overnight in backtest (not force-closed at 15:25)
2. Trailing stop activates and locks in gains during multi-day sell-offs
3. Portfolio max drawdown reduced by 15%+ in bear window vs baseline (without protective puts)
4. Insurance cost in bull window < 3% annual drag
5. No state/order/OCO regressions
6. Tier 1 entries show lower average cost basis (IV at entry) than Tier 2 entries

---

## Rollback Plan

1. Set `PROTECTIVE_PUTS_USE_SWING_MODE = False` → reverts to intraday behavior
2. Set `PROTECTIVE_PUTS_PRECRISIS_ENABLED = False` → disables Tier 1 (back to crisis-only)
3. All config changes are additive — old keys preserved for backward compatibility
