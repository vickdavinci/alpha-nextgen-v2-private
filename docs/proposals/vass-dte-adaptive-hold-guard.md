# Proposal: DTE-Adaptive Hold Guard for VASS Debit Spreads

**Status:** Draft
**Author:** Claude (V12.22 IC implementation as reference)
**Depends on:** V12.22 IC Hold Guard (implemented on `feature/va/iron-condor`)

---

## 1. Problem Statement

The VASS hold guard is currently **240 minutes (4 hours)** — a single partial trading day:

```
config.py:1390  SPREAD_MIN_HOLD_MINUTES = 240
```

This is a blanket value applied to all VASS debit spreads regardless of their entry DTE, which ranges from 7 to 45 calendar days depending on IV tier:

| IV Tier | Entry DTE Range | Hold (Current) | Hold as % of DTE |
|---------|:-----------:|:-----------:|:---------:|
| Low (VIX < 16) | 30–45 DTE | 4 hours | 0.6%–0.9% |
| Medium (VIX 16–25) | 21–45 DTE | 4 hours | 0.6%–1.3% |
| High (VIX > 25) | 7–21 DTE | 4 hours | 1.3%–3.8% |

A 4-hour hold on a 30-DTE debit spread protects against **nothing meaningful**. The directional thesis that drove the entry needs days, not hours, to play out. Meanwhile, the 5 bypass conditions (Profitable, Transition, RegimeBreak, LossBypass at 50% of stop, SevereLoss at 110%) provide so many escape hatches that the hold guard is effectively decorative.

### What Goes Wrong

1. **Normal QQQ oscillation triggers stops**: QQQ moves 1-2% multiple times per week. A 30-DTE BULL_CALL debit spread with 25% adaptive stop gets stopped out on routine noise within 1-3 days.

2. **Regime transition fires bypass immediately**: The Transition bypass fires on any DETERIORATION or RECOVERY overlay — which occurs regularly. Combined with a 4-hour hold, nearly every spread that enters on a regime read can escape on the next regime update.

3. **LossBypass threshold is too generous**: At 50% of adaptive stop (V12.6 change from 75%), a spread with a 25% stop can bypass hold at just -12.5% loss. On a 30-DTE $3.50 debit spread, that's a $0.44 move — equivalent to a 0.3% QQQ move.

4. **No DTE awareness**: A 7-DTE high-IV credit spread and a 45-DTE low-IV debit spread get the same 4-hour hold. The 7-DTE spread has 10× faster theta decay and gamma acceleration — it needs a shorter hold. The 45-DTE spread needs a longer one.

---

## 2. How to Select a Hold Period: The Decision Framework

This section explains the methodology used to derive the IC hold guard and how to adapt it for VASS. The key insight: **the hold period is not arbitrary — it's derived from the mathematical relationship between the strategy's alpha source and the time it needs to express.**

### Step 1: Identify the Alpha Source

Every options strategy has a primary alpha driver:

| Strategy | Alpha Source | Theta Effect | Hold Guard Purpose |
|----------|-------------|:------------:|-------------------|
| **Iron Condor** (credit) | Theta decay | FOR you | Wait for theta to accumulate |
| **VASS Debit Spread** (debit) | Delta/direction | AGAINST you | Wait for directional thesis to play out |
| **VASS Credit Spread** (credit) | Theta + direction | FOR you | Hybrid: theta accumulation + directional confirmation |

This distinction is critical: **the hold guard protects different things for different strategies.**

### Step 2: Model the Noise Period

For OTM options, theta follows the square-root model: `theta(T) ~ 1/sqrt(T)`

**Cumulative theta** from entry DTE `T0` to remaining DTE `T`:
```
theta_captured = 1 - sqrt(T / T0)
```

For **credit spreads** (IC, VASS credit), theta works FOR you, so the question is: "How long until enough theta has accumulated to cushion against noise?"

For **debit spreads** (VASS debit), theta works AGAINST you, so the question becomes: "How long until the directional signal has had a fair chance to express, and how much theta am I paying for that time?"

### Step 3: Quantify Theta Cost/Benefit

If you hold for a fraction `f` of entry DTE (hold_days = f × DTE), the theta effect is always:

```
theta_effect = 1 - sqrt(1 - f)
```

| Fraction (f) | Theta Effect | Credit Spread (benefit) | Debit Spread (cost) |
|:---:|:---:|---|---|
| 1/5 (20%) | 10.6% | 10.6% credit captured | 10.6% of debit eroded |
| 1/4 (25%) | 13.4% | 13.4% credit captured | 13.4% of debit eroded |
| 1/3 (33%) | 18.4% | 18.4% credit captured | 18.4% of debit eroded |
| 1/2 (50%) | 29.3% | 29.3% credit captured | 29.3% of debit eroded |

**For IC (credit):** We chose 1/3 because 18.4% theta accumulation creates a meaningful cushion — the credit from time decay (~$0.37 on a $2.00 credit) starts offsetting typical 0.5-1% QQQ oscillations.

**For VASS debit:** We need to balance noise survival time against theta drag. The hold period should be long enough for the directional thesis to play out but short enough that theta hasn't significantly eroded the position.

### Step 4: Match Hold Period to Signal Horizon

VASS debit spreads enter based on regime persistence (≥3 neutral-zone bars) + VIX direction + UVXY conviction. The signal horizon — how long the directional thesis needs to express — depends on:

1. **Regime persistence**: Requires ≥3 consecutive neutral-zone bars to confirm entry. Historical QQQ trends driven by regime persistence take 5-10 days to play out.

2. **QQQ noise cycle**: QQQ routinely oscillates ±1-2% within any 3-5 day window. A hold of < 5 days is almost guaranteed to encounter at least one adverse 1%+ move.

3. **VIX mean reversion**: VIX spikes (which trigger the credit spread tier) revert in 3-5 days on average. VIX direction changes (RISING/FALLING) typically persist for 5-10 days.

4. **Gamma acceleration**: The first sqrt(DTE) days have the highest gamma sensitivity. For 30 DTE, that's ~5.5 days. During this period, delta P&L swings are largest relative to the theta drag.

### Step 5: Select the Fraction

**For VASS debit spreads, the optimal fraction is 1/5 (20%):**

| Entry DTE | Hold Days (1/5) | Theta Cost | Signal Coverage |
|-----------|:-----------:|:--------:|:-------------:|
| 21 DTE | 5 days | 10.6% | Covers 1 full QQQ noise cycle |
| 30 DTE | 6 days | 10.6% | Covers regime persistence horizon |
| 45 DTE | 9 days | 10.6% | Covers VIX mean reversion cycle |
| 7 DTE | 2 days* | 10.6% | Minimum (*clamped to floor) |

*Why 1/5 and not 1/3:*
- 1/3 costs 18.4% of debit in theta — on a $3.50 debit that's $0.64, which represents 8-13% of max profit. That's too expensive for a directional play.
- 1/5 costs 10.6% — $0.37 on $3.50 debit — which is the typical bid-ask round-trip cost, so the theta drag roughly equals friction. This is the break-even point where holding longer costs more than the directional edge provides.
- 1/5 gives 5-9 days across the DTE range, which covers the QQQ noise cycle and regime persistence horizon.

**For VASS credit spreads, the optimal fraction is 1/4 (25%):**

Credit spreads benefit from theta, so we can afford a longer hold. But they also have directional exposure (unlike IC's symmetric structure), so the hold shouldn't be as long as IC's 1/3.

| Entry DTE | Hold Days (1/4) | Theta Captured | Signal Coverage |
|-----------|:-----------:|:--------:|:-------------:|
| 7 DTE | 2 days* | 13.4% | Minimum (*clamped to floor) |
| 14 DTE | 4 days* | 13.4% | QQQ noise half-cycle |
| 21 DTE | 6 days | 13.4% | Full noise cycle |

### Step 6: Set Floor and Ceiling

- **Floor (2 calendar days)**: Even the shortest VASS entry (7 DTE high-IV credit) needs at least 2 full trading days. A sub-2-day hold offers no noise protection.
- **Ceiling (10 calendar days)**: For 45 DTE debit spreads, 1/5 gives 9 days. Cap at 10 to prevent excessively long holds on extended DTE entries. Unlike IC, VASS debit spreads pay theta drag, so the ceiling should be tighter.

---

## 3. Design: DTE-Adaptive VASS Hold Guard

### Architecture

```
check_spread_exit_signals_impl()
  ── PRE-GUARD (always fire) ─────────────────────────────────────
  HARD_STOP_DURING_HOLD    — VIX-tiered hard stop (unchanged)
  EOD_HOLD_RISK_GATE       — VIX-tiered EOD gate at 15:45+ (unchanged)

  ┌─ HOLD GUARD (position age < hold_minutes) ────────────────────┐
  │                                                                │
  │  Debit spreads:                                                │
  │    hold_days = clamp(ceil(entry_dte × 0.20), 2, 10)          │
  │  Credit spreads:                                               │
  │    hold_days = clamp(ceil(entry_dte × 0.25), 2, 10)          │
  │  hold_minutes = hold_days × 1440                              │
  │                                                                │
  │  BYPASSES (reduced from current 5 → 3):                       │
  │    1. Profitable    — pnl_pct > 0                             │
  │    2. Transition    — DETERIORATION/RECOVERY overlay          │
  │    3. SevereLoss    — pnl <= -(110% of adaptive stop)         │
  │                                                                │
  │  REMOVED bypasses (rationale below):                          │
  │    ✗ LossBypass (50% of stop) — too permissive, fires on      │
  │      routine noise. Severe loss bypass covers catastrophes.    │
  │    ✗ RegimeBreak — overlaps with Transition bypass.            │
  │      Transition fires on DETERIORATION/RECOVERY which is       │
  │      the mechanism through which regime breaks manifest.       │
  │                                                                │
  │  DEFAULT → return None (block main cascade)                   │
  └────────────────────────────────────────────────────────────────┘

  ── POST-GUARD (full cascade after hold expires) ────────────────
  P0-P14 unchanged
```

### Why Reduce Bypasses from 5 to 3

The current 5-bypass architecture (Profitable + Transition + RegimeBreak + LossBypass + SevereLoss) was designed for a 240-minute hold window where the bypasses needed to be generous because the hold was too short to matter. With a multi-day hold:

1. **LossBypass (50% of stop) is redundant**: At 50% of a 25% adaptive stop, this fires at just -12.5%. Every spread that experiences a single bad day will trigger this. It defeats the purpose of a hold guard. The SevereLoss bypass at 110% of stop (27.5%) provides the genuine escape hatch for catastrophic moves.

2. **RegimeBreak duplicates Transition**: A regime break (score dropping below threshold) produces a DETERIORATION transition overlay. The Transition bypass already catches this. Having both means the hold guard has two paths to the same exit, which weakens it.

3. **SevereLoss stays**: This is the genuine "something is very wrong" escape. At 110% of adaptive stop, it only fires on catastrophic moves (2%+ QQQ gap through a short strike). This is worth keeping.

4. **Transition stays but becomes more selective**: Currently fires on ANY DETERIORATION or RECOVERY overlay. With a multi-day hold, consider restricting to `strong_deterioration` or `strong_recovery` only, which require larger regime score changes.

### Config Parameters

```python
# ── VASS Hold Guard (DTE-adaptive) ──
VASS_HOLD_GUARD_DTE_ADAPTIVE = True          # Use DTE-adaptive hold (replaces fixed SPREAD_MIN_HOLD_MINUTES for VASS)
VASS_HOLD_DEBIT_DTE_FRACTION = 0.20          # Debit spreads: hold for 1/5 of entry DTE (10.6% theta cost)
VASS_HOLD_CREDIT_DTE_FRACTION = 0.25         # Credit spreads: hold for 1/4 of entry DTE (13.4% theta captured)
VASS_HOLD_MIN_DAYS = 2                       # Minimum 2 calendar days hold
VASS_HOLD_MAX_DAYS = 10                      # Maximum 10 calendar days hold
VASS_HOLD_LOSS_BYPASS_ENABLED = False        # Disable permissive LossBypass (50% of stop)
VASS_HOLD_REGIME_BREAK_BYPASS_ENABLED = False # Disable redundant RegimeBreak bypass
```

### SpreadPosition Model Change

Add `entry_dte` field to `SpreadPosition` in `options_primitives.py`:

```python
entry_dte: int = 30  # DTE at entry, drives hold guard duration
```

Set at construction in `options_position_manager.py:270`:
```python
entry_dte=self._pending_spread_long_leg.days_to_expiry,
```

### Hold Duration Computation

Replace the fixed `SPREAD_MIN_HOLD_MINUTES` lookup with:

```python
if bool(getattr(config, "VASS_HOLD_GUARD_DTE_ADAPTIVE", False)):
    is_credit = spread.spread_type in ("BULL_PUT_CREDIT", "BEAR_CALL_CREDIT", ...)
    fraction = (
        float(getattr(config, "VASS_HOLD_CREDIT_DTE_FRACTION", 0.25))
        if is_credit
        else float(getattr(config, "VASS_HOLD_DEBIT_DTE_FRACTION", 0.20))
    )
    min_days = int(getattr(config, "VASS_HOLD_MIN_DAYS", 2))
    max_days = int(getattr(config, "VASS_HOLD_MAX_DAYS", 10))
    hold_days = max(min_days, min(max_days, math.ceil(spread.entry_dte * fraction)))
    min_hold_minutes = hold_days * 1440
else:
    min_hold_minutes = int(getattr(config, "SPREAD_MIN_HOLD_MINUTES", 240))
```

---

## 4. Files to Modify

| File | Changes |
|------|---------|
| `config.py` (~line 1394) | Add 7 `VASS_HOLD_*` parameters |
| `engines/satellite/options_primitives.py` (line 219) | Add `entry_dte: int = 30` to SpreadPosition + serialization |
| `engines/satellite/options_position_manager.py` (line 270) | Set `entry_dte` at SpreadPosition construction |
| `engines/satellite/vass_exit_evaluator.py` (line 311) | Replace fixed `min_hold_minutes` with DTE-adaptive computation; gate LossBypass/RegimeBreak behind new config flags |
| `tests/test_options_engine.py` | Update spread fixture with `entry_dte`; add hold guard DTE-adaptive tests |

---

## 5. Migration Strategy

The `VASS_HOLD_GUARD_DTE_ADAPTIVE` flag defaults to `False` (current behavior preserved). This allows:

1. **Backtest A/B comparison**: Run identical periods with flag on vs off
2. **Gradual rollout**: Enable for debit spreads first, then credit spreads
3. **Rollback**: Flip flag to `False` to restore 240-minute fixed hold

Once backtest validation confirms improvement, remove the fixed-hold codepath.

---

## 6. Expected Outcomes

### Reduced Premature Exits

| Scenario | Current (240 min) | Proposed (DTE-adaptive) |
|----------|:-:|:-:|
| 30 DTE debit, -15% at day 2 | Exits (LossBypass at 50%) | Held (day 2 < 6-day hold) |
| 30 DTE debit, -28% at day 3 | Exits (SevereLoss) | Exits (SevereLoss bypass) |
| 45 DTE debit, regime DETERIORATION at day 1 | Exits (Transition) | Held* (unless strong_deterioration) |
| 7 DTE credit, profitable at 4h | Exits (Profitable) | Exits (Profitable bypass) |

### Theta Math Validation

For a 30-DTE BULL_CALL debit spread, $3.50 debit, $5 width, 15 contracts:
- **Position value**: $3.50 × 100 × 15 = $5,250
- **Theta drag per day**: ~$0.06 × 100 × 15 = $90/day
- **6-day hold theta cost**: ~$540 (10.3% of position)
- **Max profit**: ($5.00 - $3.50) × 100 × 15 = $2,250
- **Theta cost as % of max profit**: $540 / $2,250 = 24%

This is significant but acceptable because:
1. The hold prevents converting temporary -15% paper losses into realized -15% losses
2. Many of those held positions will recover as the directional thesis plays out
3. The net effect (fewer premature stops × higher eventual win rate) should outweigh the theta drag on genuine losers

---

## 7. Comparison: IC vs VASS Hold Guard Design Choices

| Design Decision | IC (Credit) | VASS Debit (Proposed) | VASS Credit (Proposed) | Why Different |
|---|---|---|---|---|
| **Fraction** | 1/3 (33%) | 1/5 (20%) | 1/4 (25%) | Theta direction: FOR vs AGAINST |
| **Floor** | 5 days | 2 days | 2 days | VASS high-IV can be 7 DTE |
| **Ceiling** | 15 days | 10 days | 10 days | Debit theta drag limits max hold |
| **Theta at expiry** | 18.4% captured | 10.6% cost | 13.4% captured | Different alpha sources |
| **Bypasses** | 1 (Profitable) | 3 (Prof/Trans/Severe) | 3 (Prof/Trans/Severe) | VASS is directional, needs more escape hatches |
| **Hard stop during hold** | 2.5× credit | VIX-tiered % (existing) | VIX-tiered % (existing) | VASS already has this infrastructure |
| **EOD gate** | 1.5× credit | VIX-tiered % (existing) | VIX-tiered % (existing) | VASS already has this infrastructure |

### Why IC Has Fewer Bypasses

IC is **non-directional** — it profits when QQQ stays in a range. There is no regime thesis to fail, no directional signal to invalidate. The only relevant question during hold is: "Is the position making money?" If yes, let it run. If no, wait for theta.

VASS is **directional** — it profits when QQQ moves in the predicted direction. The regime thesis CAN fail (Transition bypass), and catastrophic moves CAN invalidate the position (SevereLoss bypass). These escape hatches are structurally necessary, but the permissive ones (LossBypass, RegimeBreak) should be removed because they fire on routine noise.

---

## 8. Verification Plan

```bash
source venv/bin/activate

# VASS-specific tests
pytest tests/test_options_engine.py -v -k "hold_guard"

# Full regression
pytest -q
```

### Backtest Validation

Run the following periods with `VASS_HOLD_GUARD_DTE_ADAPTIVE = True` vs `False`:

1. **2023 Q1 (Jan-Mar)**: Low VIX, trending market — tests debit spread hold
2. **2023 Q3 (Jul-Sep)**: Rising VIX, choppy market — tests credit spread hold
3. **2022 Q1 (Jan-Mar)**: High VIX, bear market — stress test for bypass adequacy

Compare:
- Win rate (should increase with DTE-adaptive hold)
- Average hold duration (should increase)
- Average P&L per trade (should improve as fewer premature stops)
- Drawdown (should not increase significantly — SevereLoss bypass protects)
