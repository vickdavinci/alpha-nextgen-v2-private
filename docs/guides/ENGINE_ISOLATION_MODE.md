# Engine Isolation Mode (V6.4)

## Purpose

Isolation Mode allows targeted backtesting of individual engines by disabling all others. This enables:
- Focused debugging of specific engine behavior
- Performance analysis without interference from other engines
- Validation of engine logic in isolation before integration testing

---

## Quick Start

### Test Options Engine Only

```python
# In config.py
ISOLATION_TEST_MODE = True
```

Run backtest:
```bash
./scripts/qc_backtest.sh "V6.4-OptionsIsolation" --open
```

### Return to Normal

```python
ISOLATION_TEST_MODE = False
```

---

## Configuration Reference

### Master Switch

| Parameter | Default | Description |
|-----------|---------|-------------|
| `ISOLATION_TEST_MODE` | `False` | Master switch for isolation mode |

### Engine Enables

When `ISOLATION_TEST_MODE = True`, these control which engines run:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `ISOLATION_REGIME_ENABLED` | `True` | Regime Engine (required by most engines) |
| `ISOLATION_OPTIONS_ENABLED` | `True` | Options Engine (VASS + Micro) |
| `ISOLATION_TREND_ENABLED` | `False` | Trend Engine (QLD/SSO/TNA/FAS) |
| `ISOLATION_MR_ENABLED` | `False` | Mean Reversion Engine (TQQQ/SOXL) |
| `ISOLATION_HEDGE_ENABLED` | `False` | Hedge Engine (TMF/PSQ) |
| `ISOLATION_YIELD_ENABLED` | `False` | Yield Sleeve (SHV) |

### Safeguard Enables

| Parameter | Default | Description |
|-----------|---------|-------------|
| `ISOLATION_KILL_SWITCH_ENABLED` | `False` | Kill Switch (5% daily loss) |
| `ISOLATION_STARTUP_GATE_ENABLED` | `False` | Startup Gate (15-day warmup) |
| `ISOLATION_COLD_START_ENABLED` | `False` | Cold Start (days 1-5 restrictions) |
| `ISOLATION_DRAWDOWN_GOVERNOR_ENABLED` | `False` | Drawdown Governor (position scaling) |
| `ISOLATION_PANIC_MODE_ENABLED` | `False` | Panic Mode (SPY -4% liquidation) |
| `ISOLATION_WEEKLY_BREAKER_ENABLED` | `False` | Weekly Breaker (5% WTD loss) |
| `ISOLATION_GAP_FILTER_ENABLED` | `False` | Gap Filter (SPY -1.5% gap block) |
| `ISOLATION_VOL_SHOCK_ENABLED` | `False` | Vol Shock (3× ATR pause) |

---

## Preset Configurations

### Options Only (Default)

```python
ISOLATION_TEST_MODE = True
ISOLATION_REGIME_ENABLED = True
ISOLATION_OPTIONS_ENABLED = True
ISOLATION_TREND_ENABLED = False
ISOLATION_MR_ENABLED = False
ISOLATION_HEDGE_ENABLED = False
ISOLATION_YIELD_ENABLED = False
```

### Trend Only

```python
ISOLATION_TEST_MODE = True
ISOLATION_REGIME_ENABLED = True
ISOLATION_OPTIONS_ENABLED = False
ISOLATION_TREND_ENABLED = True
ISOLATION_MR_ENABLED = False
ISOLATION_HEDGE_ENABLED = False
ISOLATION_YIELD_ENABLED = False
```

### Mean Reversion Only

```python
ISOLATION_TEST_MODE = True
ISOLATION_REGIME_ENABLED = True
ISOLATION_OPTIONS_ENABLED = False
ISOLATION_TREND_ENABLED = False
ISOLATION_MR_ENABLED = True
ISOLATION_HEDGE_ENABLED = False
ISOLATION_YIELD_ENABLED = False
```

### Full System (Normal Mode)

```python
ISOLATION_TEST_MODE = False
# All other flags ignored when master switch is False
```

---

## Engine Dependencies

Some engines require other engines to function:

| Engine | Requires |
|--------|----------|
| Options Engine | Regime Engine (for direction) |
| Trend Engine | Regime Engine (for ADX scaling) |
| Mean Reversion | Regime Engine (for VIX filter) |
| Hedge Engine | Regime Engine (for hedge sizing) |
| Yield Sleeve | Capital Engine (for lockbox) |

**Always keep `ISOLATION_REGIME_ENABLED = True`** unless testing Regime Engine itself.

---

## What Stays Active in Isolation Mode

These components are **NOT** affected by isolation mode:

| Component | Why |
|-----------|-----|
| Time Guards (13:55-14:10) | Core safety feature |
| Position Limits | Prevents over-allocation |
| OCO Manager | Required for options exits |
| State Persistence | Required for tracking |
| Options Chain Loading | Required for contract selection |
| Data Feeds | Required for all engines |

---

## Audit Log Analysis

When running isolated backtests, use these log patterns:

### Options Engine

```bash
# Intraday signals
grep "INTRADAY_SIGNAL_APPROVED\|INTRADAY: Blocked" logs.txt

# Swing spreads
grep "SPREAD:\|VASS" logs.txt

# Direction matching
grep "Selected CALL\|Selected PUT" logs.txt

# Micro regime
grep "MICRO_UPDATE" logs.txt
```

### Trend Engine

```bash
grep "TREND_ENTRY\|TREND_EXIT\|TREND: Entry blocked" logs.txt
```

### Mean Reversion

```bash
grep "MR_ENTRY\|MR_EXIT\|MR: Entry blocked" logs.txt
```

---

## Troubleshooting

### "No trades in isolation mode"

1. Check regime is enabled: `ISOLATION_REGIME_ENABLED = True`
2. Check target engine is enabled: `ISOLATION_OPTIONS_ENABLED = True`
3. Verify data feeds are loading (check for chain errors)

### "Kill switch still triggering"

Verify the safeguard is disabled:
```python
ISOLATION_KILL_SWITCH_ENABLED = False
```

### "Startup gate blocking trades"

Verify warmup bypass is active:
```python
ISOLATION_STARTUP_GATE_ENABLED = False
```

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| V6.4 | 2026-02-08 | Initial implementation for Options Engine testing |
