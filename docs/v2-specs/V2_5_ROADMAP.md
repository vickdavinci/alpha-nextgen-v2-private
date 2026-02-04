# V2.5 Roadmap - Structural Trend & Multi-Asset Basket

> **Status:** PLANNED (Post V2.4 Validation)
> **Created:** 2026-02-01
> **Target:** Q2 2026

---

## Overview

V2.5 represents a strategic evolution from momentum-based swing trading to **position trading** with longer holding periods (30-90 days) and multi-asset diversification.

### Philosophy Shift

| Aspect | V2.2 (Current) | V2.5 (Planned) |
|--------|----------------|----------------|
| **Holding Period** | 5-15 days | 30-90 days |
| **Exit Logic** | Chandelier ATR stops | SMA50 structural trend |
| **Assets** | Equity-only (QLD, SSO, TNA, FAS) | Multi-asset (Stocks + Bonds + Gold) |
| **Volatility Tolerance** | Low (ATR-based stops) | High (3% drops allowed) |
| **Correlation** | High (0.70-0.95 to SPY) | Diversified (negative correlation in crisis) |

---

## V2.4 (Current Release) - Execution Validation

**Goal:** Validate SMA50 + Hard Stop logic with existing symbols before portfolio changes.

### Implemented Features

```python
# config.py
TREND_USE_SMA50_EXIT = True  # Switch from Chandelier to SMA50
TREND_SMA_PERIOD = 50        # 50-day SMA
TREND_SMA_EXIT_BUFFER = 0.02 # 2% buffer below SMA50

# Hard Stop by asset (from entry)
TREND_HARD_STOP_PCT = {
    "QLD": 0.15,  # 15% (2× ETF)
    "SSO": 0.15,  # 15% (2× ETF)
    "TNA": 0.12,  # 12% (3× ETF)
    "FAS": 0.12,  # 12% (3× ETF)
}
```

### Exit Logic

| Condition | Action | Urgency |
|-----------|--------|---------|
| Close < SMA50 * (1 - 2%) | Exit position | EOD |
| Loss >= Hard Stop % | Exit position | IMMEDIATE |
| Regime < 30 | Exit position | EOD |

### Validation Criteria

- [ ] QLD NOT sold during minor volatility (3% drops) if above SMA50
- [ ] Hard stops trigger correctly at 12%/15% thresholds
- [ ] No whipsaws in choppy markets (compared to Chandelier)
- [ ] Holding periods extend to 30+ days in trending markets

---

## V2.5 (Next Release) - Multi-Asset Basket

### New Symbol Additions

| Symbol | Description | Leverage | Allocation | Correlation to SPY |
|--------|-------------|:--------:|:----------:|:------------------:|
| **TMF** | 3× Treasury Bond | 3× | 10% | -0.40 |
| **UGL** | 2× Gold | 2× | 10% | -0.10 |

### Proposed Allocation

| Asset | V2.2 | V2.5 | Change |
|-------|:----:|:----:|--------|
| QLD | 20% | 25% | +5% |
| SSO | 15% | 20% | +5% |
| TNA | 12% | 0% | Removed |
| FAS | 8% | 0% | Removed |
| TMF | 0% | 10% | New |
| UGL | 0% | 10% | New |
| **Total** | **55%** | **65%** | +10% |

### Asset-Specific Rules

```python
# V2.5 Proposed Config
TREND_SYMBOLS_V25 = ["QLD", "SSO", "TMF", "UGL"]

TREND_HARD_STOP_PCT_V25 = {
    "QLD": None,  # No hard stop, SMA50 only
    "SSO": None,  # No hard stop, SMA50 only
    "TMF": 0.10,  # 10% crash stop (bonds can gap)
    "UGL": 0.08,  # 8% crash stop (gold volatile)
}
```

### Implementation Requirements

1. **Subscribe to UGL** in `main.py`
2. **Add UGL to exposure groups** (new `COMMODITIES_BETA` group)
3. **Add UGL leverage** to `SYMBOL_LEVERAGE` config
4. **Resolve TMF conflict** - Remove from Hedge Engine or use different bond ETF
5. **Add SMA50 indicators** for TMF, UGL
6. **Update tests** for new symbols and allocation

### Risk Considerations

| Risk | Mitigation |
|------|------------|
| TMF in both Hedge and Trend | Option A: Remove from Hedge, use PSQ only |
| UGL volatility (5-8% daily swings) | 8% hard stop |
| Reduced equity diversification | TMF/UGL provide crisis hedge |
| Higher total allocation (65%) | Reduce options or MR allocation |

---

## V2.6 (Future) - Mean Reversion Bidirectional

### Current State (V2.2)

- Long-only: TQQQ, SOXL
- Entry: RSI < 25 (oversold)
- Exit: RSI > 50 or time-based

### Proposed V2.6

Add short-side symbols for **rally fading**:

| Symbol | Description | Direction | Trigger |
|--------|-------------|-----------|---------|
| TQQQ | 3× Nasdaq | LONG | RSI < 25 |
| SOXL | 3× Semiconductor | LONG | RSI < 25 |
| **SQQQ** | 3× Inverse Nasdaq | SHORT | RSI > 75 + Rally > 2.5% |
| **SOXS** | 3× Inverse Semiconductor | SHORT | RSI > 75 + Rally > 2.5% |

### Bidirectional Logic

```python
# V2.6 Proposed Config
MR_LONG_SYMBOLS = ["TQQQ", "SOXL"]
MR_SHORT_SYMBOLS = ["SQQQ", "SOXS"]

MR_RSI_OVERSOLD = 25   # Long entry
MR_RSI_OVERBOUGHT = 75 # Short entry
MR_RALLY_THRESHOLD = 0.025  # 2.5% rally required for short

# Mutual Exclusivity
# Block long entry if short held (and vice versa)
# Total MR allocation (long + short) <= 10%
```

### Implementation Requirements

1. **Subscribe to SQQQ, SOXS** in `main.py`
2. **Add to MR_SYMBOLS** config
3. **Implement rally fade logic** in `mean_reversion_engine.py`
4. **Mutual exclusivity check** - block opposing positions
5. **Allocation cap** - ensure total MR <= 10%

---

## Timeline

| Version | Focus | Target Date | Status |
|---------|-------|-------------|--------|
| V2.4 | SMA50 + Hard Stop validation | Feb 2026 | 🟡 In Progress |
| V2.5 | Multi-Asset Basket (TMF, UGL) | Q2 2026 | 📋 Planned |
| V2.6 | MR Bidirectional (SQQQ, SOXS) | Q3 2026 | 📋 Planned |

---

## References

- `docs/07-trend-engine.md` - Current Trend Engine spec
- `docs/08-mean-reversion-engine.md` - Current MR Engine spec
- `config.py` - All tunable parameters
- `engines/core/trend_engine.py` - Trend Engine implementation
- `engines/satellite/mean_reversion_engine.py` - MR Engine implementation

---

*Document created: 2026-02-01*
