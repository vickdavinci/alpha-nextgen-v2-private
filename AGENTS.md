# AGENTS.md — Codex Instructions for Alpha NextGen V2

## Shared Process (Mandatory)

- Follow `PROCESS.md` for workflow, gates, commit contract, backtest/pull/analyze steps.
- If any instruction here conflicts with `PROCESS.md` process enforcement, run `PROCESS.md` checks first.

## Metadata

- Last updated: `2026-03-09 11:15:00 EDT`
- Repo commit baseline: `d7fa2c1`
- Update rule: refresh both fields whenever architecture, workflows, or thresholds change materially.

## Recent Work (V12.30-V12.32)

- Commit `ee5fb28`:
  - Restored V12.24-style `BULL_CALL_DEBIT` exit geometry.
  - Lowered confirmed debit profit target to `35%`.
  - Disabled `BULL_CALL_DEBIT` thesis soft stop and kept tactical exits active.
- Commits `23b99a0`, `6c5b0b9`, `812ec87`, `d6b449b`:
  - Flipped `BEAR_PUT` short-put assignment gate polarity: enforce in `STRESS`, high-VIX, or bullish/neutral-high regime instead of deep-bear tape.
  - Added explicit `ASSIGN_GATE_*` telemetry attribution.
  - Preserved true-bear `STRESS` relax for both `BEAR_PUT_DEBIT` and `BULL_PUT_CREDIT` short-put paths.
- Commits `56036af`, `1bfdfe5`, `9462ec5`, `4204b54`, `4c132d3`, `72d3180`, `f03b247`:
  - Added neutral fallback VASS direction inference in macro-neutral tape, with deep-bear guard blocking bullish inference when regime is `<= 45`.
  - Preferred `BEAR_PUT_DEBIT` over `BEAR_CALL_CREDIT` in high-IV bearish regimes, with fallback back to credit if the PUT candidate set is empty.
  - Added BEAR credit stability gate based on persisted overlay state / bars-since-flip continuity across algorithm restarts.
  - Disabled `RECOVERY`-overlay transition de-risk for VASS credit spreads to avoid premature bearish credit exits.
- Commits `5028e7b`, `658d232`:
  - Made VASS scan throttling attempt-aware so interval budget is consumed only after candidate-evaluation work begins.
  - Added softer neutral fallback delta tier (`0.5`) plus short-lived direction memory (`120m`) to reduce VASS resolver starvation.
- Commits `6b59133`, `d61bd19`, `ac1acf2`, `480d1e6`, `a532cb5`, `1a4bf36`, `2502a96`, `1e5b558`:
  - Hardened RCA observability artifacts: stable empty-schema flushes, deterministic `incident_id`, `spread_key` correlation, tag/trace backfill from caches, and `tag_origin` attribution.
  - Added lifecycle diagnostic counters for retry / invalid / preclear / reconcile paths.
  - Expanded offline exit-reason normalization and made QC Research ObjectStore loading tolerant of empty artifacts plus schema backfill.
- Commits `2d634e5`, `68a84ac`, `04b73a9`, `cacc53d`:
  - Added adaptive insufficient-buying-power retry sizing from broker free-margin / maintenance-delta telemetry, with one immediate reduced-size retry and short cooldown thereafter.
  - Added a 5-minute open-delay gate for VASS profit-target exits to avoid opening-book fills.
  - Tuned `BULL_CALL_DEBIT` intraday QQQ invalidation threshold from `4.0%` to `3.9%`.
  - Gated credit `THETA_FIRST` tail-cap exits to emergency windows only.
- Commits `b84e56e`, `7ef1515`, `a28c46d`, `db35600`, `e8f0ca0`, `71c64e6`, `f2c5a01`:
  - Skipped fresh-trade OGP for `BEAR_CALL_CREDIT` / `BEAR_PUT_DEBIT` when regime is already bearish, while preserving the global `close_all` rail.
  - Forced `BEAR_PUT` / `BEAR_PUT_DEBIT` profit-target eligibility to use tradeable spread value instead of intrinsic-overridden valuation.
  - Biased `BEAR_PUT` width target/min one strike wider and modestly relaxed bearish-put debit-to-width caps within the existing selector/validator helpers.
  - Raised the bear-regime `close_all` OGP threshold to `40.0` for `BEAR_PUT` / `BEAR_PUT_DEBIT`, without changing any other spread path.
  - Forced `BEAR_PUT` / `BEAR_PUT_DEBIT` MFE-lock and trail evaluation to use tradeable spread value so intrinsic override no longer arms early non-profit exits.
  - Removed `MARGIN_BUFFER_INSUFFICIENT` forced exits for debit spreads and narrowed the margin-buffer guard to near-expiry ITM credit spreads only.
  - Scoped spread-close cancel counting by ladder phase so `LIMIT -> COMBO_MARKET` gets its own retry budget before any sequential fallback.
- Commit `d7fa2c1`:
  - Scoped the hard `VASS_TRANSITION_BLOCK_BEAR_ON_RECOVERY` gate to only the first two `RECOVERY` bars after an overlay flip.
  - Preserved `AMBIGUOUS` and bullish-deterioration transition blocks.
  - Let later bearish `RECOVERY` entries fall through to the existing handoff throttle instead of staying hard-blocked.

Known V12.30/V12.31 issues addressed:
- VASS resolver starvation in macro-neutral tape due to hard-only direction inference.
- Over-blocking of bearish debit access from legacy deep-bear assignment gating.
- Premature `RECOVERY` de-risk exits on bearish credit spreads.
- Router/order observability gaps when artifacts were empty or tags/traces had to be reconstructed.
- Repeated spread rejection churn after broker insufficient-buying-power responses.
- Early-session profit-target exits hitting unstable opening quotes.
- Bear-regime bearish spreads being force-closed the same day by blunt fresh-trade OGP.
- Multi-day `BEAR_PUT` holds being truncated by the generic `VIX >= 30` OGP close-all rail during bear regimes.
- Phantom `BEAR_PUT` profit-target exits caused by intrinsic override on put spreads with high remaining extrinsic value.
- Ultra-narrow `BEAR_PUT` spread selection under skew compressing realized spread value.
- `BEAR_PUT` MFE-lock / trail state being armed by intrinsic-inflated spread value instead of tradeable mark.
- Debit spreads being force-closed by the generic assignment margin-buffer policy despite no credible assignment-danger window.
- Combo-market close retries inheriting prior limit-phase cancel count and jumping straight to sequential fallback.
- Bearish VASS entries staying hard-blocked for the full `RECOVERY` overlay instead of only the immediate flip window.

## Project Summary

**Alpha NextGen V2** is a multi-strategy algorithmic trading system built on QuantConnect (LEAN engine) targeting Interactive Brokers. It trades QQQ options (spreads + single-leg) and leveraged ETFs across three modes:

- **Core (40%)**: Trend Engine — MA200+ADX on QLD, SSO, UGL, UCO
- **Satellite (50%)**: Options Engine — VASS swing spreads, Micro intraday, ITM momentum on QQQ
- **Satellite (10%)**: Mean Reversion — RSI oversold on TQQQ, SPXL, SOXL

**Current version**: V12.32 (working branch). **Main branch**: `develop`. **Python**: 3.11.

---

## Environment Setup

```bash
# Activate venv (MUST do first — system Python is 3.14, project needs 3.11)
source venv/bin/activate && python --version  # Expect 3.11.x

# Run tests
make test                    # or: pytest
pytest tests/test_regime_engine.py -v  # single file
pytest -k "kill_switch" -v             # pattern match

# Lint
make lint                    # Black + isort
```

---

## Codex Skills and RCA Source of Truth

### Canonical Skill Paths

- Canonical runtime skills live in: `~/.codex/skills/`
- Repo copies under `docs/skills/` are documentation/mirror copies unless the runtime skill registry points to them.

Current custom analysis skills:
- `~/.codex/skills/log-analyzer/SKILL.md`
- `~/.codex/skills/trade-analyzer/SKILL.md`
- `~/.codex/skills/qc-backtest-pipeline/SKILL.md`

### Skill Discovery Behavior (Important)

- Codex uses a session-level "Available skills" registry.
- If a newly created/updated skill does not appear in that list, it may be a session cache/discovery issue (not a missing file).
- Action: restart/reopen the Codex session to force skill re-indexing.

### Mandatory Analysis Outputs for Backtest RCA

For each analyzed run, produce these 3 reports in the same stage folder:
- `{RUN_NAME}_REPORT.md`
- `{RUN_NAME}_SIGNAL_FLOW_REPORT.md`
- `{RUN_NAME}_TRADE_DETAIL_REPORT.md`

### Object Store Source-of-Truth Rule

- `trades.csv` is source-of-truth for realized P&L and win/loss metrics.
- For V10.43+ style telemetry runs, event completeness must come from Object Store observability artifacts:
  - `signal_lifecycle`
  - `regime_decisions`
  - `regime_timeline`
  - `router_rejections`
  - `order_lifecycle`
- If local Object Store export is blocked by QC tier, run `scripts/qc_research_objectstore_loader.py` in QC Research and persist:
  - `{RUN_NAME}_OBJECTSTORE_CROSSCHECK.md`
- Any logs-only conclusion without Object Store corroboration must be labeled reduced-confidence.

---

## QuantConnect Backtest Workflow

```bash
# Canonical one-command pipeline (provenance + backtest + pull + reports + objectstore scaffolding)
python3 scripts/qc_backtest_pipeline.py --start-date 2024-01-01 --end-date 2024-12-31
```

Natural-language trigger rule for agents:
- If user says “run backtest for Jul to Sep 2024”, agent must execute the workflow directly.
- Do not ask user to run pipeline commands manually.
- `run_name` can be omitted; script infers latest version from commits/stages and auto-allocates next `R#`.

Pipeline behavior:
1. Git provenance guard (ahead/behind/dirty checks, optional auto-push).
2. Stage scaffolding under `docs/audits/logs/stage<version>/`.
3. Run-pinned ObjectStore loader script generation.
4. Calls `scripts/qc_backtest.sh` (`--open`) to run QC backtest.
5. Pulls artifacts via `scripts/qc_pull_backtest.py --all`.
6. Auto-generates:
   - `{RUN_NAME}_REPORT.md`
   - `{RUN_NAME}_SIGNAL_FLOW_REPORT.md`
   - `{RUN_NAME}_TRADE_DETAIL_REPORT.md`
7. Writes `RUN_PROVENANCE.md`.

Low-level fallback (manual use only):
```bash
./scripts/qc_backtest.sh "V12.8-MyFeature" --open --start-date 2024-07-01 --end-date 2024-10-31
```

**Key constraints**:
- Per-file size limit: 256KB after minification
- `main.py` and `options_engine.py` are the two largest files
- Backtest logs go to `docs/audits/logs/stage{version}/`
- Always use `--open` flag to see results

**Backtest dates** should be passed via algorithm parameters (`start_date`, `end_date`, `backtest_year`) from pipeline/CLI.
`main.py` date defaults are fallback only.

---

## Repository Structure

```
alpha-nextgen-v2-private/
├── main.py                          # QCAlgorithm entry point (~2,538 lines)
├── config.py                        # ALL tunable parameters (~2,868 lines)
├── main_*_mixin.py                  # 11 mixin files decomposed from main.py
│   ├── main_bootstrap_mixin.py
│   ├── main_options_mixin.py
│   ├── main_orders_mixin.py
│   ├── main_regime_mixin.py
│   ├── main_reconcile_mixin.py
│   ├── main_intraday_close_mixin.py
│   ├── main_market_close_mixin.py
│   ├── main_risk_monitor_mixin.py
│   ├── main_signal_generation_mixin.py
│   ├── main_observability_mixin.py
│   └── main_premarket_mixin.py
│
├── engines/
│   ├── core/                        # Always-active engines
│   │   ├── regime_engine.py         # Market state scoring (0-100)
│   │   ├── capital_engine.py        # Position sizing, lockbox
│   │   ├── risk_engine.py           # Kill switch, circuit breakers
│   │   ├── cold_start_engine.py     # Days 1-5 warm entry
│   │   ├── startup_gate.py          # Time-based arming (6 days)
│   │   └── trend_engine.py          # MA200+ADX for QLD/SSO/UGL/UCO
│   └── satellite/                   # Conditional engines
│       ├── options_engine.py        # QQQ options facade (~4,507 lines)
│       ├── vass_entry_engine.py     # VASS swing entry routing (~2,770 lines)
│       ├── vass_exit_evaluator.py   # VASS spread exit cascade (~1,185 lines)
│       ├── vass_signal_validator.py # Entry signal validation
│       ├── vass_assignment_manager.py # Assignment risk checks
│       ├── vass_exit_profile.py     # VIX-tiered stop/target profiles
│       ├── vass_risk_firewall.py    # VASS risk gates
│       ├── micro_entry_engine.py    # Micro intraday entry gates
│       ├── itm_horizon_engine.py    # ITM momentum direction/entry
│       ├── options_exit_evaluator.py # Intraday exit logic
│       ├── options_state_manager.py # State persistence for options
│       ├── options_position_manager.py # Position tracking
│       ├── options_primitives.py    # Data classes, MicroRegimeEngine
│       ├── options_entry_evaluator.py
│       ├── options_intraday_entry.py
│       ├── options_expiration_exit.py
│       ├── options_micro_signal.py
│       ├── options_partial_oco.py
│       ├── options_pending_guard.py
│       ├── options_trade_resolver.py
│       ├── options_models.py
│       ├── intraday_exit_profile.py
│       ├── iv_sensor.py
│       ├── mean_reversion_engine.py
│       ├── hedge_engine.py
│       └── premarket_vix_actions.py
│
├── portfolio/                       # Router, exposure groups
│   └── portfolio_router.py          # ONLY component that places orders
├── execution/                       # Order management
│   ├── execution_engine.py
│   └── oco_manager.py
├── models/                          # Data classes and enums
├── persistence/                     # ObjectStore save/load
├── scheduling/                      # Timed events
├── utils/                           # Helpers
├── data/                            # Symbols, indicators
├── tests/                           # Unit + scenario tests
├── scripts/                         # Build/deploy automation
└── docs/
    ├── system/                      # Spec documents (00-19)
    └── audits/logs/                 # Backtest results by stage
        ├── stage12.7/               # V12.7 Full Year 2024 (-24.07%)
        └── stage12.8/               # V12.8 Full Year 2024 (-5.82%)
```

---

## Architecture Rules

### 1. Strategy engines emit signals, NOT orders
Strategy engines return `TargetWeight` objects. Only `portfolio/portfolio_router.py` calls `MarketOrder()`, `Liquidate()`, etc.

### 2. Risk engine runs FIRST every minute
```
OnData flow:
  1. Split check
  2. Risk engine checks (kill switch, panic mode)
  3. If kill switch Tier 2/3 → return immediately
  4. Strategy scanning (MR, Options, Trend)
```

### 3. Mixin pattern (QC constraint)
QC's managed class prevents multiple inheritance. `main.py` uses explicit class-level method binding:
```python
class AlphaNextGen(QCAlgorithm):
    _normalize_intraday_lane = MainOptionsMixin._normalize_intraday_lane
    # ... 120+ method bindings
```

### 4. Options engine facade pattern
`OptionsEngine` delegates to sub-engines via `host=self`:
```
main_options_mixin → options_engine.run_micro_intraday_cycle()
                   → options_engine.run_itm_intraday_explicit_cycle()
                   → options_engine.run_vass_intraday_entry_cycle()
```

### 5. Config-driven parameters
Never hardcode values. Always use `config.py`:
```python
from config import KILL_SWITCH_PCT, ADX_PERIOD
```

---

## VASS Spread Exit Cascade (V12.8)

Priority order for debit spread exits. Higher priority prevents lower from firing:

```
HOLD GUARD (if position < min-hold):
  1. HARD_STOP_DURING_HOLD
  2. EOD_HOLD_RISK_GATE
  3. Bypass checks (Profitable, Transition, LossBypass, SevereLoss)

MAIN CASCADE (after hold expired or bypassed):
  P0.  VIX Spike Exit
  P1.  Overlay Stress Exit
  P2.  Overnight De-risk
  P3.  MFE Lock Floor
  P4.  TAIL_RISK_CAP (1% of portfolio equity per spread)
  P5.  Day-4 EOD Decision
  P6.  Profit Target (regime-adaptive)
  P7.  Trail Stop
  P8.  Width Hard Stop
  P9.  PCT Hard Stop (VIX-tiered)
  P10. Adaptive Stop (VIX-tiered + regime + ATR)
  P11. Time Stop
  P12. Regime Deterioration
  P13. DTE Exit
  P14. Neutrality Exit
```

---

## Key Thresholds

| Threshold | Value | Description |
|-----------|-------|-------------|
| Kill Switch Tier 3 | 6% daily loss | Full liquidation |
| Kill Switch Tier 2 | 4% daily loss | Exit trend, keep spreads |
| Kill Switch Tier 1 | 2% daily loss | Reduce trend 50%, block new options |
| Regime RISK_ON | Score >= 70 | Full leverage |
| Regime NEUTRAL | Score 50-69 | Normal operations |
| Regime CAUTIOUS | Score 45-49 | Light hedge (5% SH) |
| Regime DEFENSIVE | Score 35-44 | Medium hedge (8% SH) |
| Regime RISK_OFF | Score 0-34 | No new longs, full hedge |
| VASS Low IV | VIX < 16 | Debit spreads, 30-45 DTE |
| VASS Medium IV | VIX 16-25 | Debit spreads, 7-30 DTE |
| VASS High IV | VIX > 25 | Credit spreads, 5-40 DTE |
| VASS Tail Risk Cap | 1.0% equity | Per-spread $ loss cap |
| VASS Stop Low VIX | 25% | Adaptive stop at VIX < 18 |
| VASS Stop Med VIX | 35% | Adaptive stop at VIX 18-25 |
| VASS Stop High VIX | 40% | Adaptive stop at VIX > 25 |
| VASS Max Contracts | 15 | Hard cap per spread entry |
| Options Budget | 50% | Of TotalPortfolioValue |
| Margin Cap | 90% | Block new entries |

---

## Latest Performance (V12.8 Full Year 2024)

| Metric | Value |
|--------|-------|
| Net Return | -5.82% |
| Win Rate | 47.86% |
| Sharpe | -0.233 |
| Max Drawdown | 25.0% |
| Total Trades | 280 |

| Engine | Trades | WR | Gross P&L |
|--------|-------:|---:|----------:|
| VASS | 107 | 51.4% | -$9,845 |
| MICRO (ITM+OTM) | 117 | 46.2% | -$5,785 |
| HEDGE (Protective Puts) | 56 | 44.6% | +$12,136 |

**Known issues being worked on:**
- VASS BULL_CALL tail losses dominate (-$9,327 from 97 bullish legs)
- MICRO CALL underperformance (94 CALL trades, -$9,363)
- 80% of $50K options budget is idle on a typical day
- Exit plumbing: combo orders fail in high-VIX, need IOC/sequential fallback

---

## Coding Conventions

1. **QuantConnect patterns**: Always `from AlgorithmImports import *`. See `QC_RULES.md`.
2. **Type hints** on all function signatures.
3. **Google-style docstrings**.
4. **Structured logging**: `self.Log(f"ENGINE_ACTION: {symbol} | Key={value}")`, use `trades_only=True` for essential logs.
5. **Error handling**: Always catch with logging, use `get_default_state()` fallback.

---

## What NOT to Do

- **Never place orders from strategy engines** — only `portfolio_router.py` does that.
- **Never hardcode parameter values** — always reference `config.py`.
- **Never hold TQQQ/SPXL/SOXL overnight** — force close by 15:45 ET.
- **Never touch lockbox capital** — it's protected.
- **Never import standard libraries directly** — use `AlgorithmImports`.
- **Never skip risk checks** — risk engine must run before any strategy logic.
- **Never push to main/develop without tests passing**.

---

## File Modification Guide

| If you're changing... | Modify these files |
|----------------------|-------------------|
| VASS entry logic | `engines/satellite/vass_entry_engine.py` |
| VASS exit logic | `engines/satellite/vass_exit_evaluator.py` |
| Micro intraday gates | `engines/satellite/micro_entry_engine.py` |
| ITM momentum | `engines/satellite/itm_horizon_engine.py` |
| Shared spread validation | `engines/satellite/options_engine.py` |
| Any tunable parameter | `config.py` |
| Daily orchestration flow | `main.py` + relevant `main_*_mixin.py` |
| Order execution | `portfolio/portfolio_router.py`, `execution/execution_engine.py` |
| Risk/kill switch | `engines/core/risk_engine.py` |
| Regime scoring | `engines/core/regime_engine.py` |

---

## Testing

```bash
make test                                    # All tests
pytest tests/test_options_engine.py -v       # Options engine
pytest tests/test_regime_engine.py -v        # Regime engine
pytest tests/test_risk_engine.py -v          # Risk engine
pytest tests/test_plumbing_regressions.py -v # Integration regressions
pytest -k "kill_switch" -v                   # Pattern match
```

Tests use mocked QC infrastructure (`conftest.py` provides fixtures). Run all tests before committing.

---

## Git Workflow

- **Main dev branch**: `develop`
- **Feature branches**: `feature/va/<name>` (from `develop`)
- **Release tags**: `V12.x` on develop
- **Commit format**: `V12.X: concise description of change`
- **Remote**: `origin` → `github.com/vickdavinci/alpha-nextgen-v2-private.git`
