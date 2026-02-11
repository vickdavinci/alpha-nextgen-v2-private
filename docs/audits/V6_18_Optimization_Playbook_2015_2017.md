# V6.18 Optimization Playbook (2015 + 2017 + 2022 Bear Addendum)

Date: 2026-02-11
Scope: Post-technical-hardening optimization guidance using V6.18 runs:
- `V6_18_Jul_Oct_2015_*`
- `V6_18_Jul_Oct_2017_*`
- `V6_18_Jul_Oct_2022_*`

---

## 1) Executive Readout

The system is now in optimization phase.

What changed vs prior cycles:
- Rejection diagnostics are visible and actionable (`E_*`, `ValidationFail=*`).
- Router-level rejection is no longer the primary bottleneck in these runs.
- Remaining failures are mainly strategy-mix and participation calibration.

V6.18 outcomes:
- 2017 Jul-Oct: net after fees `+2,289.6` (bull/low-vol favorable)
- 2015 Jul-Oct: net after fees `-23,772.75` (shock/chop unfavorable)

Conclusion:
- The framework can work in calm bull regimes.
- It still degrades badly in volatile/choppy shock regimes due to over-concentrated spread behavior and churn exits.

---

## 2) Lessons Learned by Market Type

## Bull / Low-Vol Trend (2017)

Observed strengths:
- Direction quality was mostly right (dropped intraday signals overwhelmingly CALL + macro bullish).
- Positive net result despite conservative gating.

Observed weaknesses:
- Over-blocking by `E_CALL_GATE_MA20` reduced valid participation.
- Upstream gating still heavy (`REGIME_NOT_TRADEABLE`, `CONFIRMATION_FAIL`, `QQQ_FLAT`).

Lesson:
- In risk-on, the system is often right on direction but too restrictive on execution.

## Choppy / Transition (present in both, stronger in 2015)

Observed strengths:
- Stress gates fire correctly.
- Safety logic blocks some poor entries.

Observed weaknesses:
- Frequent `NEUTRALITY_EXIT` in spreads creates churn and fee drag.
- Re-entry/recycle behavior reduces expectancy.

Lesson:
- In chop, hard exits + frequent re-entry is worse than staged de-risking.

## Shock / Volatility Expansion (2015 Aug cluster)

Observed strengths:
- Stress call gates triggered (`E_CALL_GATE_STRESS`, `E_CALL_GATE_VIX5D`).
- Protective behavior activated.

Observed weaknesses:
- VASS remained heavily concentrated in bullish debit structures at bad times.
- Bear-put participation was suppressed (`BEAR_PUT_ASSIGNMENT_GATE`).
- Large spread-leg tail losses dominated drawdown.

Lesson:
- Detection exists, but post-detection trade mix and exit management are not robust enough.

---

## 3) Optimization Objectives (All Markets)

Priority order:
1. Improve participation quality, not raw trade count.
2. Preserve bull capture while reducing chop/shock bleed.
3. Keep changes minimal (5-7 knobs), avoid feature sprawl.

Hard constraints:
- Do not reintroduce router ambiguity.
- Do not remove stress protections.
- Avoid broad logic expansion; tune existing gates first.

---

## 4) Minimal Optimization Set (Recommended)

## A. Bull participation recovery (without opening floodgates)

1. Relax MA20 CALL gate only in persistent risk-on contexts.
- Keep MA20 gate default behavior.
- Add a narrow bypass branch when macro is strongly bullish and stable.

2. Reduce one upstream blocker incrementally.
- Tune either `QQQ_FLAT` or `CONFIRMATION_FAIL` slightly, not both at once.

Expected impact:
- Better conversion of already-correct bullish signals (seen in 2017 drops).

## B. Choppy-market churn control

3. Replace hard `NEUTRALITY_EXIT` with staged de-risking.
- First signal: partial reduce.
- Second confirm: full exit.

4. Add short anti-ping-pong cooldown after neutrality/stop exits.

Expected impact:
- Lower fee drag and fewer whipsaw loops.

## C. Shock-market downside control

5. Keep stress CALL gates active as-is (do not loosen in volatile states).

6. Recalibrate bear-put assignment gate to recover valid bearish participation.
- Reduce over-blocking while preserving assignment safety.

7. Rebalance VASS strategy mix away from persistent BULL_CALL_DEBIT concentration during stress.

Expected impact:
- Better defensive participation in volatility clusters (2015 failure mode).

---

## 5) Metrics to Track Per Run (Promotion Criteria)

## Execution Funnel
- Intraday approved->selected conversion
- Drop-code distribution (`E_CALL_GATE_MA20`, `E_CALL_GATE_STRESS`, etc.)
- Router rejects should remain near zero

## Strategy Mix
- VASS strategy composition by regime
- Bear-put participation rate when bearish/stress regimes exist
- Neutrality-exit frequency and post-exit churn

## Risk Quality
- Count and size of tail losses (single-leg and spread-leg)
- Fee-to-gross ratio
- Number of repeated same-direction re-entries after stop/neutral exits

## Regime Fitness
- Bull: preserve call capture without excessive filtering
- Choppy: reduce churn exits and false re-entries
- Shock: avoid bullish concentration and enable valid defensive spreads

---

## 6) Suggested Iteration Plan (Short Cycles)

Cycle 1 (smallest):
- Bull-side participation tuning only (`MA20 risk-on relaxation` + one upstream threshold)

Cycle 2:
- Neutrality churn mitigation (staged exits + short cooldown)

Cycle 3:
- Bear-put participation calibration (assignment gate tolerance)

Cycle 4:
- Strategy-mix balancing for VASS under stress

After each cycle:
- Re-run 2017 + 2015 first
- Promote to 2022 only if no regression in funnel quality and no new tail-risk failure

---

## 7) Non-Goals (to avoid overfitting)

- No large new feature set.
- No many-threshold broad retune in one batch.
- No optimization based on one period’s net P&L alone.
- No bypassing stress protections for participation.

---

## 8) Bottom Line

There is credible path-to-profitability across regimes, but only if optimization stays disciplined:
- keep safety + observability intact,
- tune a small number of high-impact controls,
- validate in short, comparative cycles.

---

## 9) 2022 Bear-Market Addendum (V6.18 Jul-Oct)

### 9.1 What the 2022 run proved

- Plumbing improved:
  - `Insufficient buying power=0`
  - `ROUTER_MARGIN_WIDTH_INVALID=0`
  - `INTRADAY_SIGNAL_DROPPED` always carries actionable `E_*` codes.
- Bear-bias exists at execution:
  - Executed trades: `PUT=90`, `CALL=22`.
- Core bottleneck moved upstream:
  - `APPROVED=551` but `RESULT=95` (17.2% conversion).
- VASS is functionally frozen most of the period:
  - `VASS_REJECTION=674`, dominated by `WIN_RATE_GATE_BLOCK=610`.
  - Only `4` VASS entries, all late-window `BEAR_CALL_CREDIT`.

### 9.2 Current bear-market blockers

1. Intraday drop concentration:
   - `E_CALL_GATE_STRESS=219`
   - `E_INTRADAY_TRADE_LIMIT=184`
   - `E_INTRADAY_TIME_WINDOW=20`
2. High NO_TRADE gating:
   - `CONFIRMATION_FAIL=249`
   - `REGIME_NOT_TRADEABLE=99`
   - `VIX_STABLE_LOW_CONVICTION=90`
   - `QQQ_FLAT=90`
3. VASS gate coupling:
   - Swing path blocked by global win-rate shutoff (`WIN_RATE_GATE_BLOCK`) instead of spread-quality logic.

### 9.3 Minimal optimization set for bear compatibility

1. Decouple VASS from global win-rate shutoff:
   - Keep intraday shutoff behavior.
   - For VASS, downgrade shutoff to size reduction or stricter score threshold, not hard block.
2. Reduce `E_INTRADAY_TRADE_LIMIT` pressure without raising raw risk:
   - Rebalance per-day trade budget so high-quality late-day PUT opportunities are not starved by early entries.
3. Convert part of `VIX_STABLE_LOW_CONVICTION` from hard block to reduced-size mode in bearish regimes:
   - Keep block for neutral/bull.
   - Allow half-size PUT continuation when macro/micro remain bearish.
4. Keep CALL stress gates unchanged:
   - 2022 data shows they are actively preventing additional bearish-regime CALL damage.

### 9.4 Promotion checks before next 2022 rerun

- Intraday `APPROVED->RESULT` improves from `17.2%` to at least `30%` without margin regression.
- VASS entries become continuous in high-IV bearish windows (not just end-of-period bursts).
- `WIN_RATE_GATE_BLOCK` no longer dominates VASS rejection reasons.

---

## 10) Win Rate Improvement Plan (Aligned to Master Audit)

This section converts optimization into explicit win-rate outcomes and pass/fail checks.

### 10.1 Why win rate is currently suppressed

Primary causes from V6.18 data:
- Low signal conversion (`APPROVED -> RESULT`) means many quality setups never become trades.
- VASS is over-blocked by global win-rate shutoff (`WIN_RATE_GATE_BLOCK`), reducing spread diversification.
- Choppy exits and repeated re-entry cycles reduce realized edge.
- Bear windows still have weak strategy mix balance (insufficient productive spread participation).

### 10.2 Target framework (by regime)

These are optimization targets, not guarantees.

| Regime Window | Current Baseline | Target Win Rate | Notes |
|---|---:|---:|---|
| Bull trend (2017 Jul-Oct) | ~44.7% | `>=46%` while preserving positive net | Keep participation without opening floodgates |
| Bear/stress (2022 Dec-Feb + Jul-Oct) | ~33% | `>=38%` | Improve spread mix and reduce hard-block starvation |
| Shock/chop (2015 Jul-Oct) | ~30.7% | `>=35%` | Reduce churn and improve defensive trade quality |

---

## 11) Dec 2021-Feb 2022 Deep-Dive Learnings (Same Code Lineage)

Run inputs:
- `docs/audits/logs/stage6.18/V6_18_Dec2021_Feb2022_logs.txt`
- `docs/audits/logs/stage6.18/V6_18_Dec2021_Feb2022_orders.csv`
- `docs/audits/logs/stage6.18/V6_18_Dec2021_Feb2022_trades.csv`

Observed output:
- Trades: `126`
- Net P&L: `-16,050`
- Win rate: `40.5%`
- Direction quality at execution: `CALL -17,680` vs `PUT +1,630`

### 11.1 What failed (plumbing + mix)

1. Intraday conversion collapsed:
- `INTRADAY_SIGNAL_APPROVED=352`
- `INTRADAY_RESULT=47` (`13.4%`)

2. Intraday drops are concentrated in a small set of blockers:
- `E_CALL_GATE_STRESS=129`
- `E_INTRADAY_TRADE_LIMIT=63`
- `E_CALL_GATE_MA20=51`
- `E_INTRADAY_CAP_TOO_SMALL=33`

3. VASS is still throttled by meta-gates, not market quality:
- `VASS_ENTRY=31`
- `VASS_REJECTION=450`
- `ValidationFail=WIN_RATE_GATE_BLOCK=402` (dominant)
- `ValidationFail=HAS_SPREAD_POSITION=110`

4. Margin reliability regressed:
- `Order Error (Insufficient buying power)=4`
- `ROUTER_MARGIN_WIDTH_INVALID=1`
- `MARGIN_CB_SKIP=2`

5. Spread exit coverage remains sparse in logs:
- `SPREAD: EXIT_SIGNAL=3` despite much higher spread activity.

### 11.2 What worked

1. Diagnostic quality is now useful:
- No generic drop dominance; engine codes are explicit and actionable.

2. Premarket ladder is active:
- `PREMARKET_VIX_LADDER=50` with both `L1_ELEVATED` and `L2_STRESS`.

3. Bear-side directional edge exists:
- PUT side is positive in this window; losses are concentrated in call-side structures.

### 11.3 Optimization priorities from this run (minimal-code tuning)

1. Reduce `E_INTRADAY_TRADE_LIMIT` starvation in bear windows:
- Preserve cap discipline, but reserve one late-session slot for high-quality PUT continuation.

2. Decouple VASS from hard global `WIN_RATE_GATE_BLOCK`:
- Convert hard block to size penalty/score threshold for VASS so spread path can participate.

3. Rebalance spread mix when stress flags persist:
- Reduce repeated `BULL_CALL_DEBIT` concentration while VIX ladder remains `L2_STRESS`.

4. Tighten margin preflight consistency on close/replace paths:
- Eliminate remaining `ROUTER_MARGIN_WIDTH_INVALID`/insufficient-bp errors before optimization promotion.

5. Improve spread lifecycle telemetry:
- Ensure spread exit events and reasons are recorded for all close paths (not only a subset).

### 11.4 Promotion checklist before next optimization cycle

- Intraday conversion: `APPROVED->RESULT >= 25%` (intermediate) before targeting `>=30%`.
- VASS rejects: `WIN_RATE_GATE_BLOCK` no longer the dominant fail reason.
- Margin errors: `0` for both width-invalid and insufficient buying-power rejects.
- Call-side drawdown: no single call-spread cluster dominating period loss.

---

## 12) Sep-Dec 2018 Choppy/Stress Learnings

Run inputs:
- `docs/audits/logs/stage6.18/V6_18_Sep_Dec_2018_logs.txt`
- `docs/audits/logs/stage6.18/V6_18_Sep_Dec_2018_orders.csv`
- `docs/audits/logs/stage6.18/V6_18_Sep_Dec_2018_trades.csv`

Observed output:
- Trades: `116`
- Net P&L: `-23,293`
- Win rate: `30.2%`
- Call/Put P&L split (by symbol right): `CALL=-22,491`, `PUT=-802`

### 12.1 What 2018 adds beyond 2015/2017/2022

1. Choppy conversion collapse is severe:
- `INTRADAY_SIGNAL_APPROVED=370`
- `INTRADAY_RESULT=28` (`7.6%`)

2. Upstream + policy stack jointly suppresses participation:
- `Dir=NONE=66.1%`
- Top drop codes: `E_CALL_GATE_MA20`, `E_INTRADAY_TRADE_LIMIT`, `E_CALL_GATE_STRESS`

3. VASS throughput is constrained by occupancy rather than pure contract quality:
- `HAS_SPREAD_POSITION=603` dominates VASS skips/rejections.

4. Bear-put spread path is still constrained:
- `BEAR_PUT_ASSIGNMENT_GATE=103`.

5. Margin path is not fully clean in chop:
- Insufficient-buying-power error cluster still appears.

### 12.2 Optimization updates from 2018 (minimal changes)

1. Prioritize conversion quality in chop:
- Treat `APPROVED->RESULT` as the lead metric before net P&L tuning.

2. Reduce occupancy lockup side-effects:
- Prefer staged exits over sticky spread occupancy in choppy regimes.

3. Recalibrate assignment gate with regime context:
- Keep protection, but avoid suppressing nearly all bearish spread participation.

4. Keep explicit gate diagnostics and avoid adding new opaque logic:
- Current `E_*` visibility is good and should be preserved.

5. Address residual margin error path before optimization promotion:
- No optimization promotion if insufficient-buying-power errors remain non-zero.

### 12.3 Cross-Run Optimization Priority Order (after 2018)

1. Execution reliability:
- Eliminate residual margin rejects and improve `APPROVED->RESULT`.

2. VASS throughput health:
- Reduce `HAS_SPREAD_POSITION` choke and assignment-gate over-blocking.

3. Strategy mix robustness:
- Reduce call-spread concentration in non-bull windows.

4. Choppy regime behavior:
- Lower neutrality churn and boundary-size over-filtering.
| Cross-run aggregate | low-30s to mid-40s | `>=40%` | Must pass safety gates first |

### 10.3 Execution-linked gates (must improve with win rate)

Mapped to `OPTIONS_ENGINE_MASTER_AUDIT.md` success criteria:
- `SC-06`: Intraday conversion must rise materially (target `>=30%` before full rollout, then toward long-term threshold).
- `SC-13`: VASS participation must be non-trivial in allowed windows.
- `SC-14`: Bear handling must keep call stress control while restoring productive put/credit participation.
- `SC-17`: Tail-loss and risk-adjusted metrics must improve alongside win rate.

### 10.4 Optimization sequence for win-rate lift

Apply in this order, one batch at a time:

1. **Unfreeze VASS quality flow**
- Decouple VASS from global hard shutoff behavior.
- Replace hard-block behavior with size/score penalty for swing path where safe.
- Success signal: `WIN_RATE_GATE_BLOCK` no longer dominates VASS rejections.

2. **Fix conversion choke points**
- Reduce `E_INTRADAY_TRADE_LIMIT` starvation of valid late-window setups.
- Preserve time-window and stress protections.
- Success signal: higher `APPROVED -> RESULT` with no safety regressions.

3. **Reduce choppy churn losses**
- Stage neutrality exits and add anti-ping-pong cooldown.
- Success signal: fewer repeated stop/re-entry loops and improved fee-adjusted win quality.

4. **Bear strategy quality tuning**
- Keep CALL stress gates.
- Improve credit/put spread participation quality in high-IV windows.
- Success signal: better bear-window win rate and fewer one-sided losing clusters.

### 10.5 Measurement protocol (every iteration)

For each run, log and compare:
- Win rate by regime window.
- Win rate by mode (Micro intraday vs VASS spread).
- Win rate by direction (CALL/PUT) and strategy family.
- Conversion funnel (`approved`, `dropped`, `result`).
- Tail-loss count and top 5 loss concentration.
- Fees as a percentage of gross P&L.

Promotion rule:
- Do not accept win-rate improvement if any critical technical safety gate regresses.
- CALL count remains constrained in bearish stress while PUT expectancy improves.
