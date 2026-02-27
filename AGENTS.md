# AGENTS.md — Codex Instructions for Alpha NextGen V2

## Shared Process (Mandatory)

- Follow `PROCESS.md` for workflow, gates, commit contract, backtest/pull/analyze steps.
- If any instruction here conflicts with `PROCESS.md` process enforcement, run `PROCESS.md` checks first.

## Metadata

- Last updated: `2026-02-27 13:21:42 EST`
- Repo commit baseline: `300eb48`
- Update rule: refresh both fields whenever architecture, workflows, or thresholds change materially.

## Recent Work (V12.20)

- Commit `ca303c0`:
  - Intraday software exits standardized to `OPT_INTRADAY`.
  - Lane metadata added to exit signals (`options_lane`, `options_strategy`).
  - Router `OPT` close fallback now infers ITM/MICRO from live symbol lane before defaulting.
- Commit `300eb48`:
  - Cancel/invalid handlers switched to `_get_order_tag(...)` for robust tag recovery.
  - OCO cancel detection hardened to avoid false retry/escalation loops.
  - Intraday snapshot tracking fixed for stacked fills (aggregate qty + blended entry).
  - Close accounting path now uses snapshot-corrected quantity/entry where applicable.

Known V12.19 failures addressed:
- ITM closes mislabeled as MICRO retry tags.
- Missing close tag attribution on canceled/invalid callbacks.
- Stacked-fill quantity/accounting drift (buy N + buy N then close 2N).
- Retry/exhausted churn from OCO cancel misclassification.

## Project Summary

**Alpha NextGen V2** is a multi-strategy algorithmic trading system built on QuantConnect (LEAN engine) targeting Interactive Brokers. It trades QQQ options (spreads + single-leg) and leveraged ETFs across three modes:

- **Core (40%)**: Trend Engine — MA200+ADX on QLD, SSO, UGL, UCO
- **Satellite (50%)**: Options Engine — VASS swing spreads, Micro intraday, ITM momentum on QQQ
- **Satellite (10%)**: Mean Reversion — RSI oversold on TQQQ, SPXL, SOXL

**Current version**: V12.20 (working branch). **Main branch**: `develop`. **Python**: 3.11.

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
