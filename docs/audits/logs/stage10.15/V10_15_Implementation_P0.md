# V10.15 Implementation (P0) — VASS + ITM

Date: 2026-02-20

## Scope
This document captures the V10.15 P0 implementation requested:
- Add VASS MFE + harvest-lock behavior (to prevent green-to-red roundtrips).
- Assess ITM feedback and apply only the parts that are structurally sound.

---

## VASS P0: MFE + Regime-Aware Harvesting

### What was already present
- Regime-aware spread target/stop/trail multipliers were already in place.
- VASS lacked explicit MFE lock tiers tied to realized max-favorable-excursion.

### Implemented now
1. Added VASS MFE config knobs:
- `VASS_MFE_LOCK_ENABLED = True`
- `VASS_MFE_T1_TRIGGER = 0.25`
- `VASS_MFE_T2_TRIGGER = 0.45`
- `VASS_MFE_T2_FLOOR_PCT = 0.15`

2. Added spread state fields:
- `highest_pnl_max_profit_pct`
- `mfe_lock_tier`

3. Added spread persistence wiring:
- Included both fields in spread `to_dict()/from_dict()`.

4. Added lock logic for both debit and credit exits:
- Track MFE as `% of max profit`.
- Tier 1 (>=25% MFE): floor moves to break-even + fees.
- Tier 2 (>=45% MFE): floor moves to `+15% of max profit` + fees.
- If current PnL falls below active floor, exit reason is tagged as `MFE_LOCK_T1` / `MFE_LOCK_T2`.

### Why this is P0
This directly addresses the VASS failure mode where spreads reached material open profit but reverted to large losses.

---

## ITM Feedback Assessment (Neutral)

### Accepted and implemented
1. **MFE-based protection (accepted)**
- ITM anti-roundtrip logic now keys off **peak MFE**, not just current gain.
- This makes the protection resilient to intrabar giveback.

2. **Conditional 15% EOD harvest (accepted with weakening gate)**
- Added ITM EOD harvest switch and thresholds:
  - `ITM_EOD_HARVEST_15_ENABLED`
  - `ITM_EOD_HARVEST_TRIGGER_PCT = 0.15`
  - `ITM_EOD_HARVEST_REQUIRE_WEAKENING = True`
  - weakening conditions based on regime and adverse VIX-5d by direction
- Hold-skip path now allows forced close when harvest condition is met.

### Assessed but not blindly forced
1. **Hard stop compression to 22% in all cases**
- Not applied as a blanket change in this P0 patch.
- Reason: current ITM path is already VIX-tiered/regime-aware; forcing a global hard 22% without re-run evidence risks overcutting valid trend continuation trades.

2. **"Always close at 15%" without context**
- Not applied as unconditional.
- Reason: implemented as conditional harvest when conditions weaken, which is safer across regimes.

---

## Files Changed
- `config.py`
- `engines/satellite/options_engine.py`
- `main.py`

---

## Validation
- Syntax compile check passed:
  - `python3 -m py_compile config.py engines/satellite/options_engine.py main.py`

---

## Expected Impact
- VASS: fewer roundtrips after meaningful MFE; better profit harvesting discipline.
- ITM: improved conversion of intraday edge into realized PnL under late-day weakening conditions.
- No architectural coupling added between engines.
