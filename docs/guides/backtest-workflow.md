# Backtest Workflow Guide

> **For Claude Code**: This document defines the automated backtest workflow. Follow these steps exactly when running backtests.

## Quick Reference

```bash
# Push code to QC cloud
cd /Users/vigneshwaranarumugam/Documents/Trading\ Github/lean-workspace
lean cloud push --project AlphaNextGen

# Run backtest
lean cloud backtest AlphaNextGen

# Check results
lean cloud backtest AlphaNextGen --open
```

---

## Pre-Backtest Checklist

Before running a backtest, verify:

- [ ] **Python environment active**: `source venv/bin/activate`
- [ ] **Tests pass locally**: `make test` or `pytest`
- [ ] **Code synced**: Changes in `alpha-nextgen/` copied to `lean-workspace/AlphaNextGen/`
- [ ] **No uncommitted changes**: `git status` shows clean

---

## Repository Structure

The project uses **two directories**:

| Directory | Purpose |
|-----------|---------|
| `alpha-nextgen/` | Main development repo (tests, docs, git history) |
| `lean-workspace/AlphaNextGen/` | QC cloud deployment (clean copy for backtests) |

### Sync Before Backtest

```bash
# Copy main.py and all supporting files
cp alpha-nextgen/main.py lean-workspace/AlphaNextGen/
cp alpha-nextgen/config.py lean-workspace/AlphaNextGen/
cp -r alpha-nextgen/engines lean-workspace/AlphaNextGen/
cp -r alpha-nextgen/portfolio lean-workspace/AlphaNextGen/
cp -r alpha-nextgen/execution lean-workspace/AlphaNextGen/
cp -r alpha-nextgen/models lean-workspace/AlphaNextGen/
cp -r alpha-nextgen/persistence lean-workspace/AlphaNextGen/
cp -r alpha-nextgen/scheduling lean-workspace/AlphaNextGen/
cp -r alpha-nextgen/data lean-workspace/AlphaNextGen/
cp -r alpha-nextgen/utils lean-workspace/AlphaNextGen/
```

---

## Backtest Execution Steps

### Step 1: Push to QuantConnect Cloud

```bash
cd /Users/vigneshwaranarumugam/Documents/Trading\ Github/lean-workspace
lean cloud push --project AlphaNextGen
```

**Expected output**: Files uploaded successfully

### Step 2: Run Backtest

```bash
lean cloud backtest AlphaNextGen
```

**Watch for**:
- Compilation errors (syntax issues)
- Runtime errors (method signatures, missing imports)
- Backtest ID in output (format: `abc123def456...`)

### Step 3: Check Results

If backtest completes:
```bash
lean cloud backtest AlphaNextGen --open
```

Or use the URL printed in console:
```
https://www.quantconnect.com/project/PROJECT_ID/BACKTEST_ID
```

---

## Error Handling Protocol

When a backtest fails, follow this process:

### 1. Identify Error Type

| Error Pattern | Cause | Fix Location |
|---------------|-------|--------------|
| `No method matches given arguments` | Wrong API signature | Check QC API docs |
| `'X' object has no attribute 'Y'` | Interface mismatch | Compare with engine source |
| `TypeError: ... missing required argument` | Missing parameter | Check method signature |
| `AttributeError: module 'X' has no attribute 'Y'` | Import error | Check file structure |

### 2. Fix and Re-push

```bash
# Fix in lean-workspace/AlphaNextGen/
# OR fix in alpha-nextgen/ and re-sync

# Push updated code
lean cloud push --project AlphaNextGen

# Re-run backtest
lean cloud backtest AlphaNextGen
```

### 3. Document the Fix

After successful fix, update `docs/BACKTEST_RESULTS_YYYY-MM-DD.md`:

```markdown
## Backtest Attempts Log

| Version | Error | Fix |
|---------|-------|-----|
| v1 | ATR signature | Added MovingAverageType.Wilders |
| v2 | **SUCCESS** | No errors |
```

---

## Results Documentation Template

Create a new file for each backtest session: `docs/BACKTEST_RESULTS_YYYY-MM-DD.md`

```markdown
# Backtest Results - YYYY-MM-DD

## Summary

**Status:** [Successful | Failed | Partial]

| Metric | Value |
|--------|-------|
| Backtest Period | YYYY-MM-DD to YYYY-MM-DD |
| Starting Capital | $X |
| Ending Equity | $Y |
| Total Return | X% |
| Sharpe Ratio | X.XX |
| Max Drawdown | X% |
| Total Orders | N |
| Runtime Errors | N |

**URL:** https://www.quantconnect.com/project/PROJECT_ID/BACKTEST_ID

## Key Observations

- Observation 1
- Observation 2

## Issues Found

| Issue | Severity | Status |
|-------|----------|--------|
| Issue description | High/Med/Low | Fixed/Pending |

## Next Steps

1. Action item 1
2. Action item 2
```

---

## Common QC API Gotchas

### Indicator Signatures

QC indicators require `MovingAverageType` when specifying resolution:

```python
# WRONG
self.ATR(symbol, period, Resolution.Minute)

# CORRECT
self.ATR(symbol, period, MovingAverageType.Wilders, Resolution.Minute)
```

| Indicator | MovingAverageType |
|-----------|-------------------|
| ATR | `Wilders` |
| BB | `Simple` |
| RSI | `Wilders` |
| EMA | `Exponential` |
| SMA | `Simple` |

### Data Access

```python
# Check if data exists before accessing
if data.Bars.ContainsKey(self.spy):
    bar = data.Bars[self.spy]
    price = bar.Close
    volume = bar.Volume
```

### Indicator Readiness

```python
# Always check IsReady before using indicator values
if self.spy_sma.IsReady:
    sma_value = self.spy_sma.Current.Value
```

---

## Automated Sync Script

Save as `scripts/sync_to_lean.sh`:

```bash
#!/bin/bash
# Sync alpha-nextgen to lean-workspace for backtesting

SRC="/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen"
DST="/Users/vigneshwaranarumugam/Documents/Trading Github/lean-workspace/AlphaNextGen"

echo "Syncing to lean-workspace..."

# Core files
cp "$SRC/main.py" "$DST/"
cp "$SRC/config.py" "$DST/"

# Directories
for dir in engines portfolio execution models persistence scheduling data utils; do
    rm -rf "$DST/$dir"
    cp -r "$SRC/$dir" "$DST/"
done

echo "Sync complete. Run: lean cloud push --project AlphaNextGen"
```

---

## Claude Code Instructions

When asked to run a backtest:

1. **Sync files** using the sync script or manual copy
2. **Push to QC**: `lean cloud push --project AlphaNextGen`
3. **Run backtest**: `lean cloud backtest AlphaNextGen`
4. **On error**: Identify, fix, document, re-push
5. **On success**: Create/update results documentation
6. **Track iterations**: Log each attempt in the results file

### Autonomy Guidelines

- **Proceed autonomously** for: syntax errors, signature mismatches, missing imports
- **Ask for guidance** on: architectural changes, business logic changes, unclear requirements
- **Always document**: Every fix in the backtest results file

---

## Version Control

After successful backtest:

1. **Copy fixes back** to `alpha-nextgen/` if made in `lean-workspace/`
2. **Run local tests**: `make test`
3. **Create feature branch** for interface fixes
4. **Commit with context**: Reference backtest in commit message

```bash
git checkout -b fix/qc-interface-alignment
git add main.py
git commit -m "fix: align main.py interfaces with engine methods

Fixes discovered during QC backtest 2026-01-25:
- Added MovingAverageType to indicator calls
- Updated MR engine method signatures
- Added helper methods for position/price retrieval

Backtest: https://www.quantconnect.com/project/27678023/...

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## QuantConnect Plan & Resource Management

> **Current Plan:** Trading Firm ($48/mo) + 2× B4-12 nodes ($48/mo) = **$96/mo**

### Plan Limits

| Resource | Trading Firm Limit | Our Usage |
|----------|-------------------:|-----------|
| **File Size** | 256 KB | ~102 KB (main.py) - no minification needed |
| **Backtest Log** | 5 MB | ~500 KB-2 MB typical |
| **Daily Log** | 50 MB | Allows multiple debug runs |
| **Plot Points** | 32,000 | ~14,000 for 5-year backtest |
| **Series/Chart** | 25 | 11 current, room for 14 more |

### Compute Nodes

| Node | Spec | Purpose | Cost |
|------|------|---------|-----:|
| B4-12 #1 | 4 cores, 12GB | Primary backtests | $24/mo |
| B4-12 #2 | 4 cores, 12GB | Parallel backtests | $24/mo |

**Why B4-12?**
- Options chain data requires 8+ GB RAM
- 5-year backtests with minute resolution need processing power
- 2 nodes allow parallel parameter testing

---

## Logging Optimization Strategy

### The Problem

Logging everything kills backtests:
- 410 log entries/day from INTRADAY_SIGNAL alone
- 5-year backtest = 1,260 days × 400+ logs = 500K+ entries
- Exceeds even Trading Firm limits

### The Solution: `trades_only` Pattern

```python
def _log(self, message: str, trades_only: bool = False) -> None:
    """
    Log with mode awareness.

    Args:
        trades_only: If True, ALWAYS log (trades, errors, critical events)
                    If False, ONLY log in LiveMode (diagnostics, signals)
    """
    if trades_only or self.LiveMode:
        self.Log(message)
```

### What to Log

| Category | `trades_only=` | When Logged | Examples |
|----------|:--------------:|-------------|----------|
| Trade fills | `True` | Always | `FILL: BUY 100 QLD @ $85.50` |
| Entry/Exit signals (executed) | `True` | Always | `OPT: ENTRY_SIGNAL ...` |
| Kill switch/Panic | `True` | Always | `KILL_SWITCH: Triggered` |
| Errors | `True` | Always | `STATE_ERROR: ...` |
| EOD summary | `True` | Always | Daily P&L summary |
| Signal evaluations | `False` | Live only | `INTRADAY_SIGNAL: ...` |
| Regime updates | `False` | Live only | `REGIME: Score=65` |
| VIX diagnostics | `False` | Live only | `VIX: 15.2 (STABLE)` |

### Log Budget Estimation

| Backtest Duration | Trades-Only Logs | Est. Size | Within 5MB? |
|-------------------|----------------:|----------:|:-----------:|
| 1 day | ~20 | 2 KB | ✅ |
| 30 days | ~500 | 50 KB | ✅ |
| 3 months | ~1,500 | 150 KB | ✅ |
| 1 year | ~5,000 | 500 KB | ✅ |
| 5 years | ~25,000 | 2.5 MB | ✅ |

---

## Charting Best Practices

### Current Charts (11 series)

| Chart | Series | Points/Day |
|-------|:------:|:----------:|
| Strategy Performance | 2 | 2 |
| Drawdown Analysis | 2 | 2 |
| Market Regime | 3 | 3 |
| Portfolio Exposure | 4 | 4 |

### Plot Point Budget

```
5-year backtest: 1,260 days × 11 series = 13,860 points
Trading Firm limit: 32,000 points
Headroom: 18,140 points for additional charts
```

### Optimization Tips

1. **Remove constant reference lines** - They waste points
   ```python
   # DON'T plot constants every day
   self.Plot("Regime", "Risk-On (70)", 70)  # Wastes 1,260 points

   # DO use chart annotations or just document thresholds
   ```

2. **Plot weekly for long backtests**
   ```python
   if self.Time.weekday() == 4:  # Friday only
       self.plot_exposure()  # 5× fewer points
   ```

3. **Conditional plotting**
   ```python
   if abs(new_value - last_value) > threshold:
       self.Plot(chart, series, new_value)
   ```

---

## Backtest Run Configurations

### Stage 1-2: Quick Validation
```python
# config.py or main.py
LOGGING_MODE = "trades_only"  # Minimal logging
# Use free nodes - fast enough for short runs
```

### Stage 3-4: Debug Mode
```python
# Enable more logging for specific issues
LOGGING_MODE = "debug"  # More verbose
# Use B4-12 nodes for speed
```

### Stage 5: Full 5-Year Stress Test
```python
# Trades-only logging to stay under limits
LOGGING_MODE = "trades_only"
# Use B4-12 nodes - required for options data
# Run parallel tests on both nodes
```

---

## Cost Optimization Path

| Phase | Seat | Nodes | Monthly | Use Case |
|-------|------|-------|--------:|----------|
| **Current** | Trading Firm | 2× B4-12 | $96 | Heavy backtesting |
| Paper Trading | Trading Firm | 1× B4-12 + L-MICRO | $96 | Validate live |
| Production | Trading Firm | 1× B4-12 + L1-2 | $150 | Live trading |

---

## Minified Files (When Needed)

If reverting to lower tiers, minified files are required:

| File | Original | Minified | Location |
|------|:--------:|:--------:|----------|
| main.py | 102 KB | 62 KB | `main_minified.py` |
| options_engine.py | 68 KB | 45 KB | `engines/satellite/options_engine_minified.py` |

**Sync minified files:**
```bash
cp main_minified.py ../lean-workspace/AlphaNextGen/main.py
cp engines/satellite/options_engine_minified.py ../lean-workspace/AlphaNextGen/engines/satellite/options_engine.py
```

**Current status (Trading Firm):** Minification NOT required.

---

*Last updated: 2026-01-30*
