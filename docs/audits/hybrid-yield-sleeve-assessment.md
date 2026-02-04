# ENGINEERING ASSESSMENT: Hybrid Yield Sleeve Proposal

**To:** Chief Technical Architect
**From:** Engineering Team
**Date:** 2026-02-01
**Re:** Phase 1 Assessment - GO/NO-GO Analysis

---

## Executive Summary

| Risk Area | Assessment | Recommendation |
|-----------|:----------:|----------------|
| Execution Risk | LOW | GO |
| Churn Analysis | LOW | GO |
| Settlement/Margin | NONE | GO |
| Logic Loops | MINIMAL | CONDITIONAL GO |
| **OVERALL** | | **GO WITH CAVEATS** |

---

## 1. Execution Risk Analysis

**Question:** Does auto-liquidating SHV in `process_immediate` introduce race conditions or "Insufficient Buying Power" risks?

### Finding: **LOW RISK - GO**

**Current Architecture:**
```python
# portfolio_router.py:912-915
def _add_shv_liquidation_if_needed(...):
    sells = [o for o in orders if o.side == OrderSide.SELL]
    buys = [o for o in orders if o.side == OrderSide.BUY]
    return sells + buys  # SELLs execute first
```

**Why It Works (Margin Account):**

| Event | Buying Power | Notes |
|-------|-------------|-------|
| Before SHV sell | $5,000 | Available cash |
| SHV sell order submitted | $5,000 | Order pending |
| SHV sell **fills** | $25,000 | IBKR credits immediately |
| Options buy submitted | $25,000 | Sufficient for $10K trade |

**Key Insight:** IBKR margin accounts provide **immediate buying power** upon fill, not T+1 settlement. The broker's risk engine calculates provisional credit in real-time.

**SHV Liquidity:**
- Average daily volume: 2-3 million shares
- Typical spread: $0.01-0.02
- Fill probability: >99.9% at market

**Remaining Risk:** If SHV price gaps down significantly between order and fill (unlikely for treasury ETF), the liquidation amount may be slightly less than expected. This is a ~$50 risk on a $10K liquidation, acceptable.

---

## 2. Churn Analysis

**Question:** With a 10% Cash Buffer, will we still "thrash" (buy/sell SHV daily)?

### Finding: **SIGNIFICANTLY REDUCED - GO**

**Current State:**
```python
SHV_MIN_TRADE = 10_000  # V2.3.6: Raised to reduce churn
```

**Proposed State:** 10% buffer + existing $10K threshold

**Analysis on $50K Account:**

| Scenario | Current Behavior | With 10% Buffer |
|----------|-----------------|-----------------|
| $3K unallocated | No SHV buy (<$10K) | No SHV buy (buffer absorbs) |
| $12K unallocated | Buy $12K SHV | Buy $7K SHV ($5K buffer) |
| Next-day $2.5K trade | Sell SHV (-spread) | Use buffer (no SHV touch) |
| Next-day $8K trade | Sell SHV | Sell $3K SHV (buffer covers $5K) |

**Churn Reduction Estimate:**

| Trade Size | Current (SHV touched?) | With Buffer (SHV touched?) |
|:----------:|:----------------------:|:--------------------------:|
| 1-5% ($500-2,500) | Yes | **No** |
| 5-10% ($2,500-5,000) | Yes | **No** |
| 10-15% ($5,000-7,500) | Yes | **Partial** |
| 15%+ ($7,500+) | Yes | Yes |

**Conclusion:** The 10% buffer eliminates churn for ~80% of typical intraday trades (Sniper 5%, MR entries 5-10%).

---

## 3. Settlement/Margin Analysis

**Question:** Does selling SHV and buying Options in the same second trigger PDT or unsettled fund restrictions?

### Finding: **NO RESTRICTIONS - GO**

**Pattern Day Trader (PDT) Rule:**

| Requirement | Our Status | Applicable? |
|-------------|-----------|:-----------:|
| Account <$25K | $50K+ | No |
| 4+ day trades in 5 days | N/A | No |
| Same security round-trip | Different securities | No |

**Unsettled Funds (Regulation T):**

| Account Type | Restriction | Our Status |
|--------------|-------------|:----------:|
| Cash Account | Cannot use unsettled funds | N/A |
| **Margin Account** | Immediate buying power | **This is us** |

**IBKR-Specific Behavior:**
```
Sell SHV @ 10:00:00 → Fill @ 10:00:01 → Buying power +$X immediately
Buy QQQ Option @ 10:00:02 → Uses provisional credit → Approved
Settlement happens T+1/T+2 in background, invisible to trading
```

**Risk:** If account were ever downgraded to cash (compliance issue, pattern violation), this would break. This is an operational control, not a code concern.

---

## 4. Logic Loop Analysis

**Question:** Will YieldSleeve buy SHV at EOD, only for Router to sell it the next morning?

### Finding: **LOOP EXISTS BUT COST IS MINIMAL - CONDITIONAL GO**

**The Loop Traced:**
```
Day 1, 15:45: YieldSleeve → "Unallocated $15K, buy SHV"
Day 1, 16:00: EOD processing → MOO order queued
Day 2, 09:30: MOO fills → +$15K SHV
Day 2, 10:15: Options signal → needs $10K
Day 2, 10:15: Auto-liquidate → sell $10K SHV ← LOOP!
```

**Cost Per Loop:**
```
SHV shares: $10,000 / $110 = ~91 shares
Spread cost: 91 × $0.02 = $1.82
Yield lost (1 day): $10,000 × 5% / 365 = $1.37
Total loop cost: ~$3.19
```

**With 10% Buffer ($5K reserved):**
```
Day 1, 15:45: YieldSleeve → "Unallocated $15K - $5K buffer = $10K for SHV"
Day 2, 10:15: Options needs $10K → $5K from buffer, $5K from SHV
Loop cost reduced by 50%: ~$1.60
```

**Frequency Estimate:**

| Market Condition | Loop Frequency | Monthly Cost |
|------------------|:--------------:|-------------:|
| High volatility (active trading) | ~5x/month | ~$8 |
| Normal | ~2x/month | ~$3 |
| Low volatility | ~0x/month | $0 |

**Yield Benefit Comparison:**
```
Average SHV holding: $15,000
Monthly yield: $15,000 × 5% / 12 = $62.50
Loop cost: ~$5/month
Net benefit: $57.50/month
```

**Conclusion:** The loop is acceptable. Yield benefit far exceeds spread cost.

---

## 5. CRITICAL FINDING: RATES Exposure Limit Conflict

**This was NOT in the Phase 1 questions but is architecturally significant.**

**Current Configuration:**
```python
# portfolio_router.py
SOURCE_ALLOCATION_LIMITS = {
    "YIELD": 0.50,  # Proposed → 0.99
    ...
}

# exposure_groups.py (from docs)
RATES_GROUP_LIMIT = 0.40  # TMF + SHV combined
```

**Problem:** Setting `YIELD: 0.99` is **ineffective** because the RATES exposure group caps SHV at 40% (minus any TMF allocation).

**Post-Kill-Switch Scenario:**
```
Kill switch fires → 100% cash
Next EOD → YieldSleeve wants 99% SHV
RATES limit → Caps at 40%
Result → Only 40% goes to SHV, 60% sits in cash earning 0%
```

**Options to Resolve:**

| Option | Change | Risk |
|--------|--------|------|
| A | Increase RATES limit to 99% | Allows extreme TMF+SHV concentration |
| B | Remove SHV from RATES group | Requires exposure group refactor |
| C | Create SHV-specific exception | Adds complexity |
| D | Accept 40% cap | Suboptimal yield but safe |

**Recommendation:** Clarify intent before implementation. If the goal is truly 90%+ SHV post-kill-switch, the RATES limit must also change.

---

## 6. Implementation Risks Not In Scope

| Risk | Severity | Mitigation |
|------|:--------:|------------|
| Lockbox interaction | Low | Existing code protects lockbox; buffer is additive |
| Cold start interaction | Low | Cold start is gradual; auto-liquidation handles it |
| Concurrent signal race | Low | Router processes sequentially; no parallelism |
| Backtest vs Live divergence | Medium | SHV fills may differ; test with paper account first |

---

## 7. Code Locations for Implementation

**If approved, these files require modification:**

| File | Change | Complexity |
|------|--------|:----------:|
| `config.py` | Add `CASH_BUFFER_PCT = 0.10` | Low |
| `engines/satellite/yield_sleeve.py` | Subtract buffer from unallocated calc | Low |
| `portfolio/portfolio_router.py` | Add SHV auto-liquidation in `process_immediate` | Medium |
| `portfolio/portfolio_router.py` | Change `YIELD` limit 0.50 → 0.99 | Low |
| `portfolio/exposure_groups.py` | Possibly increase RATES limit | TBD |

**Estimated Implementation Time:** 2-3 hours (excluding testing)

---

## Final Recommendation

### **GO WITH CAVEATS**

**Approved for Implementation:**
1. 10% Petty Cash Buffer (Point 1)
2. Auto-Liquidation in `process_immediate` (Point 2)
3. YIELD limit 0.99 (Point 3) - **REQUIRES RATES LIMIT DECISION**

**Required Clarification Before Coding:**

> **Question for Architect:** Should the RATES exposure group limit (currently 40%) be increased to allow >40% SHV allocation post-kill-switch? If yes, to what value? This affects TMF hedge capacity.

**Suggested Config Values:**

```python
# New constants needed
CASH_BUFFER_PCT = 0.10          # 10% petty cash buffer
YIELD_ALLOCATION_MAX = 0.99     # Allow near-full SHV post-kill-switch

# May need adjustment (pending architect decision)
RATES_MAX_NET_LONG = 0.40       # Current - may need to increase
```

---

## Appendix: Current Implementation Reference

### YieldSleeve Core Logic (yield_sleeve.py)

```python
def calculate_unallocated_cash(
    self,
    total_equity: float,
    non_shv_positions_value: float,
    current_shv_value: float,
) -> float:
    # Current: Unallocated = Total - All Positions (including SHV)
    unallocated = total_equity - non_shv_positions_value - current_shv_value
    return max(0.0, unallocated)

    # Proposed change:
    # buffer = total_equity * config.CASH_BUFFER_PCT
    # unallocated = total_equity - non_shv_positions_value - current_shv_value - buffer
```

### Router SHV Liquidation (portfolio_router.py)

```python
def calculate_shv_liquidation(
    self,
    cash_needed: float,
    current_shv_value: float,
    locked_amount: float,
    tradeable_equity: float,
) -> Optional[TargetWeight]:
    # Existing method - can be called from process_immediate
    available_shv = max(0.0, current_shv_value - locked_amount)
    if available_shv <= 0:
        return None
    sell_amount = min(cash_needed, available_shv)
    # ... generates TargetWeight for SHV sell
```

### Current Allocation Limits

```python
SOURCE_ALLOCATION_LIMITS: Dict[str, float] = {
    "TREND": 0.55,
    "OPT": 0.30,
    "OPT_INTRADAY": 0.05,
    "MR": 0.10,
    "HEDGE": 0.30,
    "YIELD": 0.50,      # ← Proposed: 0.99
    "COLD_START": 0.35,
    "RISK": 1.00,
    "ROUTER": 1.00,
}
```

---

**Assessment Complete. Awaiting architect sign-off on RATES limit question before proceeding to implementation.**

*Document: docs/audits/hybrid-yield-sleeve-assessment.md*
*Author: Engineering Team (Claude)*
*Version: 1.0*
