# Options & Governor Flow Analysis

**Date:** 2026-02-05
**Purpose:** Document the complete flow of options decision-making and Governor interaction

---

## Key Finding: PROTECTIVE_PUTS Is NOT Implemented

**Critical Discovery:** The `IntradayStrategy.PROTECTIVE_PUTS` exists as an enum but is **never executed**:

```python
# options_engine.py line 4232-4234
if state.recommended_strategy == IntradayStrategy.PROTECTIVE_PUTS:
    self.log(f"INTRADAY: Protective mode - regime={state.micro_regime.value}")
    return None  # Would emit hedge signal separately  ← DOES NOTHING!
```

**What this means:**
- When Micro Regime detects crisis (micro_score < 0), it recommends PROTECTIVE_PUTS
- But the options engine just logs and returns `None`
- **No PUT options are actually purchased for protection**
- All "hedging" is done via TMF/PSQ through the Hedge Engine

---

## Complete Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           DAILY REGIME ENGINE                                   │
│                        (engines/core/regime_engine.py)                          │
│                                                                                 │
│   7 Factors → Weighted Score → Smoothed (EMA) → Classification                 │
│                                                                                 │
│   RISK_ON (70-100) │ NEUTRAL (50-69) │ CAUTIOUS (40-49) │ DEFENSIVE/BEAR (<40) │
└─────────────────────────────────────────────────────────────────────────────────┘
                                         │
                    ┌────────────────────┼────────────────────┐
                    │                    │                    │
                    ▼                    ▼                    ▼
┌──────────────────────────┐ ┌──────────────────────────┐ ┌──────────────────────────┐
│     HEDGE ENGINE         │ │    OPTIONS ENGINE        │ │   DRAWDOWN GOVERNOR      │
│   (TMF/PSQ Hedging)      │ │  (QQQ Options Trading)   │ │   (Portfolio Risk)       │
│                          │ │                          │ │                          │
│ Regime < 40 → TMF 10%    │ │ Regime > 70 → CALL       │ │ Drawdown → Scale Down    │
│ Regime < 30 → +PSQ 5%    │ │ Regime < 50 → PUT        │ │ 0%/50%/100% Scaling      │
│ Regime < 20 → Full hedge │ │ 50-69 → ??? (gap)        │ │                          │
└──────────────────────────┘ └──────────────────────────┘ └──────────────────────────┘
         │                              │                              │
         │                              │                              │
         ▼                              ▼                              ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              MAIN.PY ORCHESTRATION                              │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                    EOD PROCESSING (15:45 ET)                            │   │
│  │                                                                          │   │
│  │  1. _generate_hedge_signals()     ─────────────────────────────────────┐│   │
│  │     │                                                                   ││   │
│  │     └─► Always runs (V3.1: exempt from Governor)                       ││   │
│  │         TMF/PSQ signals based on regime score                          ││   │
│  │                                                                         ││   │
│  │  2. _scan_options_signals_gated() ─────────────────────────────────────┤│   │
│  │     │                                                                   ││   │
│  │     ├─► Governor 0%?                                                   ││   │
│  │     │   ├─► CALL spreads: BLOCKED                                      ││   │
│  │     │   └─► PUT spreads: ALLOWED (line 2987)                           ││   │
│  │     │                                                                   ││   │
│  │     ├─► Governor < 25%?                                                ││   │
│  │     │   └─► Only bearish allowed                                       ││   │
│  │     │                                                                   ││   │
│  │     └─► Governor >= 25%?                                               ││   │
│  │         └─► All directions per regime                                  ││   │
│  │                                                                         ││   │
│  └─────────────────────────────────────────────────────────────────────────┘│   │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                    INTRADAY PROCESSING (10:00-14:00 ET)                 │   │
│  │                                                                          │   │
│  │  _scan_intraday_options() ──────────────────────────────────────────────┐│  │
│  │     │                                                                   ││   │
│  │     ├─► Micro Regime Engine                                            ││   │
│  │     │   └─► VIX Level × VIX Direction = 21 regimes                     ││   │
│  │     │                                                                   ││   │
│  │     ├─► If crisis (micro_score < 0):                                   ││   │
│  │     │   └─► Returns PROTECTIVE_PUTS                                    ││   │
│  │     │       └─► Options Engine returns None (NOT IMPLEMENTED!)         ││   │
│  │     │                                                                   ││   │
│  │     └─► check_intraday_entry_signal()                                  ││   │
│  │         ├─► No Governor check! (gap in current code)                   ││   │
│  │         └─► Uses macro_regime_score only for Grind-Up Override         ││   │
│  │                                                                         ││   │
│  └─────────────────────────────────────────────────────────────────────────┘│   │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Governor × Options Interaction Matrix

### Current State (As Implemented)

| Governor | Regime | Swing CALL | Swing PUT | Intraday | Hedges (TMF/PSQ) |
|----------|--------|------------|-----------|----------|------------------|
| 100% | BULL (70+) | ✅ Full | ✅ Full | ✅ Full | ✅ (none needed) |
| 100% | NEUTRAL (50-69) | ✅ Full | ✅ Full | ✅ Full | ✅ (none needed) |
| 100% | CAUTIOUS (40-49) | ✅ Full | ✅ Full | ✅ Full | ✅ Light (10% TMF) |
| 100% | BEAR (<30) | ❌ Blocked | ✅ Full | ✅ Full | ✅ Full |
| 50% | Any | ✅ 50% size | ✅ 50% size | ✅ 50% size | ✅ (V3.1 exempt) |
| 0% | Any | ❌ Blocked | ✅ Allowed | **??? No check** | ✅ (V3.1 exempt) |

**GAP IDENTIFIED:** Intraday options at Governor 0% have NO explicit check!

---

## The Three Protection Mechanisms

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         PORTFOLIO PROTECTION LAYERS                             │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  LAYER 1: HEDGE ENGINE (TMF/PSQ)           ◄── ALWAYS ACTIVE                   │
│  ────────────────────────────────                                               │
│  • Regime < 40 → TMF 10% (flight to safety)                                    │
│  • Regime < 30 → TMF 15% + PSQ 5%                                              │
│  • Regime < 20 → TMF 20% + PSQ 10% (full hedge)                                │
│  • V3.1: Exempt from Governor shutdown                                         │
│  • Implementation: COMPLETE ✅                                                  │
│                                                                                 │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  LAYER 2: BEAR PUT SPREADS (Directional)   ◄── PARTIALLY GATED                 │
│  ────────────────────────────────────────                                       │
│  • Regime < 50 → PUT spreads allowed                                           │
│  • Governor 0% → PUT spreads still allowed (line 2987)                         │
│  • Purpose: Profit from market decline (not pure hedging)                      │
│  • Implementation: COMPLETE ✅                                                  │
│                                                                                 │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  LAYER 3: PROTECTIVE PUTS (Crisis Hedge)   ◄── NOT IMPLEMENTED ❌              │
│  ────────────────────────────────────────                                       │
│  • Design: Micro regime crisis → Buy PUT options for protection               │
│  • Current code: Returns None ("Would emit hedge signal separately")          │
│  • No PUT options are actually purchased!                                       │
│  • Gap: Relies entirely on TMF/PSQ which may not be sufficient                 │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Governor Override Mechanisms

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                       GOVERNOR RECOVERY PATHS                                   │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  PATH 1: HWM RESET (V3.0)                                                      │
│  ────────────────────────                                                       │
│  Trigger: 10 consecutive days at Governor 50%+ with positive P&L               │
│  Action: Reset HWM to current equity                                            │
│  Result: Governor recalculates from new baseline                                │
│                                                                                 │
│  PATH 2: EQUITY RECOVERY (V3.1)                                                │
│  ───────────────────────────────                                                │
│  Trigger: At Governor 0% for 5+ days AND equity recovers 3% from trough        │
│  Action: Force step-up to 50%                                                   │
│  Result: Can trade again at 50% sizing                                          │
│                                                                                 │
│  PATH 3: REGIME OVERRIDE (V3.0)                                                │
│  ──────────────────────────────                                                 │
│  Trigger: Regime >= 70 for 5 consecutive days                                  │
│  Action: Force Governor step-up to 50%                                          │
│  Result: Can enter BULLISH trades despite drawdown                              │
│  Cooldown: 10 days before can trigger again                                     │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │  SPECIAL: BEARISH OVERRIDE (Existing in main.py)                        │   │
│  │  ───────────────────────────────────────────────                        │   │
│  │  When: Governor = 0% AND regime < 50                                    │   │
│  │  Action: Allow PUT spreads anyway                                       │   │
│  │  Reason: PUT spreads REDUCE risk, aligned with Governor's intent        │   │
│  │  Location: main.py line 2987                                            │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Conflict Analysis: Macro Gate + Governor

### Scenario: Bear Market Recovery

```
Timeline:
─────────────────────────────────────────────────────────────────────────────────
Day 1:   Market crash → Regime drops to 25 (BEAR)
         Governor steps down to 0% (drawdown > 10%)

Day 5:   Market stabilizes → Regime rises to 45 (CAUTIOUS)
         Governor still at 0% (no recovery path triggered yet)

         CURRENT BEHAVIOR:
         • Swing PUT spreads: ALLOWED (Governor bearish override)
         • Intraday options: ??? (no Governor check)

         WITH MACRO GATE (strict):
         • Swing PUT spreads: ALLOWED (regime < 50 = PUT only)
         • Intraday PUT: ALLOWED
         • Intraday CALL: BLOCKED (macro gate)

Day 10:  Market recovers → Regime rises to 55 (NEUTRAL)
         Governor recovers to 50% (equity recovery triggered)

         CURRENT BEHAVIOR:
         • All options allowed at 50% sizing

         WITH MACRO GATE (strict):
         • All options BLOCKED (dead zone)
         • Governor says "go", Macro Gate says "no"
         ← THIS IS THE CONFLICT

Day 15:  Bull market → Regime rises to 72 (BULL)
         Governor at 50% or 100%

         BOTH AGREE: Full options allowed
─────────────────────────────────────────────────────────────────────────────────
```

### The Core Conflict

| State | Governor Says | Macro Gate (Strict) Says | Result |
|-------|---------------|--------------------------|--------|
| Recovery from bear | "Start trading at 50%" | "NEUTRAL = dead zone" | **CONFLICT** |
| Bull market | "Full trading" | "BULL = all allowed" | Agreement |
| Bear market | "Defensive only" | "PUT only" | Agreement |

---

## Recommendations

### 1. Don't Use Strict Dead Zone

The NEUTRAL dead zone (50-69 = no options) conflicts with Governor recovery:
- Governor says "you've recovered, start trading"
- Macro Gate says "market unclear, don't trade"
- **Result:** Portfolio can't capture recovery opportunities

**Alternative:** PUT-only with reduced sizing in NEUTRAL:
```python
if config.REGIME_NEUTRAL <= macro_regime_score < config.REGIME_RISK_ON:
    # NEUTRAL: PUT-only at 50% sizing (defensive stance)
    if requested_direction == OptionDirection.CALL:
        return False, "CALL blocked in NEUTRAL"
    return True, "NEUTRAL_PUT_REDUCED"
```

### 2. Add Governor Check to Intraday Options

Current gap: `check_intraday_entry_signal()` has no Governor check!

```python
def check_intraday_entry_signal(self, ..., governor_scale: float = 1.0):
    # V3.2: Governor gate for intraday options
    if governor_scale <= 0:
        # At Governor 0%, only allow defensive (PUT) intraday
        if direction == OptionDirection.CALL:
            self.log("INTRADAY: CALL blocked at Governor 0%")
            return None
        # PUT allowed - continues to sizing
```

### 3. Implement PROTECTIVE_PUTS (Optional)

Currently returns `None`. Options:
- A) Keep as-is (rely on TMF/PSQ for hedging)
- B) Implement actual PUT option purchase for crisis protection
- C) Remove the dead code if not planning to implement

### 4. Sizing Stack: Multiply vs Max

When both Governor and Macro Gate reduce sizing:

| Approach | Governor 50% × NEUTRAL 50% | Pros | Cons |
|----------|---------------------------|------|------|
| Multiply | 25% sizing | Very conservative | May miss opportunities |
| Max | 50% sizing | Captures more | Less conservative |

**Recommendation:** Accept multiply (25%). Both signals say "be careful".

---

## Summary Matrix: Who Controls What

| Control | Owned By | Affects | Can Override? |
|---------|----------|---------|---------------|
| Portfolio sizing | Governor | All trades | Master (except hedges V3.1) |
| Direction (CALL/PUT) | Regime Engine | Options only | — |
| Hedge activation | Hedge Engine | TMF/PSQ only | Always runs |
| Entry timing | Micro Regime | Intraday only | — |
| Dead zone block | **NEW: Macro Gate** | All options | Proposed |
| Crisis protection | ~~PROTECTIVE_PUTS~~ | None (broken) | — |

---

## Changelog

| Date | Author | Change |
|------|--------|--------|
| 2026-02-05 | Claude/VA | Initial flow analysis |
