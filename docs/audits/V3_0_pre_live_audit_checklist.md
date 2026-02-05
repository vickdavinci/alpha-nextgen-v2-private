# V3.0 Pre-Live Audit Checklist

**Date:** 2026-02-04
**Version:** V3.0 Hardening
**Purpose:** Comprehensive audit checklist before deploying to live trading
**Branch:** `feature/va/v3.0-hardening`

---

## Executive Summary

This document outlines 10 critical audit categories that must be validated before going live with the Alpha NextGen V2 trading system. Each audit targets specific failure modes that could result in:

- Margin calls
- Account restrictions
- Lost capital
- Orphaned positions
- Regulatory violations (PDT)

**Audit Priority Levels:**

| Level | Meaning | Timeline |
|:-----:|---------|----------|
| **P0** | Critical - Account damage possible | Must complete before live |
| **P1** | High - Significant losses possible | Must complete before live |
| **P2** | Medium - Operational issues | Complete within first week |
| **P3** | Low - Quality of life | Complete within first month |

---

## Audit 1: State Persistence & Recovery

**Priority:** P0
**Risk:** Loss of position tracking on restart → duplicate orders or orphaned positions

### Checklist

| # | Check | Status | Notes |
|---|-------|:------:|-------|
| 1.1 | Restart mid-position: Does algo recover open equity positions correctly? | ☐ | |
| 1.2 | Restart mid-spread: Are both spread legs tracked after restart? | ☐ | |
| 1.3 | ObjectStore corruption: What happens if state file is corrupted/missing? | ☐ | |
| 1.4 | Kill switch recovery: After KS trigger, does cold start properly reset? | ☐ | |
| 1.5 | Governor scale persistence: Does drawdown scale survive restart? | ☐ | |
| 1.6 | Pending MOO orders: Are queued MOO orders persisted and restored? | ☐ | |
| 1.7 | Options tracking: Are `_active_swing_spreads` and `_active_intraday_spreads` restored? | ☐ | |
| 1.8 | Daily counters: Are `_options_trades_today`, `_intraday_trades_today` reset on new day? | ☐ | |

### Test Procedure

```
1. Enter a trend position (QLD)
2. Enter a spread position (QQQ call spread)
3. Stop the algorithm mid-day
4. Restart the algorithm
5. Verify:
   - QLD position recognized
   - Spread legs both tracked
   - No duplicate entry orders placed
   - Exit logic works correctly
```

### Code Locations

- `persistence/state_manager.py` - ObjectStore save/load
- `main.py: _save_state()` - State serialization
- `main.py: _load_state()` - State restoration
- `main.py: Initialize()` - Startup reconciliation

---

## Audit 2: Timing & Scheduling

**Priority:** P2
**Risk:** Missed events, duplicate events, timezone confusion

### Checklist

| # | Check | Status | Notes |
|---|-------|:------:|-------|
| 2.1 | Early close days: Does algo handle 1:00 PM close (Thanksgiving, Christmas Eve)? | ☐ | |
| 2.2 | Market holidays: Does algo skip trading on holidays? | ☐ | |
| 2.3 | DST transitions: Do scheduled times shift correctly in March/November? | ☐ | |
| 2.4 | MOO order timing: Orders submitted at 15:45, do they execute at 09:30? | ☐ | |
| 2.5 | 09:33 SOD gap: What happens in 3-minute gap between open and SOD check? | ☐ | |
| 2.6 | 15:45 EOD: Are all intraday positions (TQQQ/SOXL) closed? | ☐ | |
| 2.7 | Weekend handling: No orders placed Saturday/Sunday? | ☐ | |
| 2.8 | Pre-market data: Are indicators using regular hours only? | ☐ | |

### Early Close Days (US Markets)

| Date | Event | Close Time |
|------|-------|:----------:|
| Day before Independence Day | July 3 | 1:00 PM ET |
| Day after Thanksgiving | Black Friday | 1:00 PM ET |
| Christmas Eve | December 24 | 1:00 PM ET |
| New Year's Eve | December 31 | 1:00 PM ET (sometimes) |

### Code Locations

- `scheduling/scheduler.py` - Event scheduling
- `main.py: _on_market_open()` - 09:30 handler
- `main.py: _on_sod_setup()` - 09:33 handler
- `main.py: _on_eod_processing()` - 15:45 handler
- `main.py: _on_market_close()` - 16:00 handler

---

## Audit 3: Regime Transition

**Priority:** P1
**Risk:** Position stuck in wrong direction when regime flips

### Checklist

| # | Check | Status | Notes |
|---|-------|:------:|-------|
| 3.1 | CALL spread held, regime drops to 45: Is exit triggered? | ☐ | |
| 3.2 | PUT spread held, regime rises to 65: Is exit triggered? | ☐ | |
| 3.3 | Hedge (TMF/PSQ) on, regime rises to 55: Are hedges exited? | ☐ | |
| 3.4 | Trend entry at regime 51, drops to 49 same day: Position held or closed? | ☐ | |
| 3.5 | Intraday regime flip: Multiple entries/exits prevented? | ☐ | |
| 3.6 | Regime at boundary (exactly 50, 70): Correct classification? | ☐ | |
| 3.7 | Regime smoothing: Does SMA prevent whipsaw on single-bar spikes? | ☐ | |

### Regime Boundaries Reference

| Regime | Score Range | Trend | MR | CALL | PUT | Hedge |
|--------|:-----------:|:-----:|:--:|:----:|:---:|:-----:|
| Bull | 70-100 | ✅ | ✅ | ✅ | ❌ | ❌ |
| Neutral | 50-69 | ✅ | ✅ | ❌ | ❌ | ❌ |
| Cautious | 40-49 | ❌ | ❌ | ❌ | ✅ | LIGHT |
| Defensive | 30-39 | ❌ | ❌ | ❌ | ✅ | MEDIUM |
| Bear | 0-29 | ❌ | ❌ | ❌ | ✅ | FULL |

### Exit Thresholds (Hysteresis)

| Position Type | Entry Threshold | Exit Threshold | Buffer |
|---------------|:---------------:|:--------------:|:------:|
| CALL Spread | Regime > 70 | Regime < 45 | 25 pts |
| PUT Spread | Regime < 50 | Regime > 60 | 10 pts |
| Hedge (TMF/PSQ) | Regime < 50 | Regime >= 50 | 0 pts |
| Trend Entry | Regime >= 50 | Regime < 30 | 20 pts |

### Code Locations

- `engines/core/regime_engine.py` - Regime scoring
- `config.py: SPREAD_REGIME_*` - Spread thresholds
- `config.py: HEDGE_REGIME_GATE` - Hedge gating
- `main.py: _generate_hedge_exit_signals()` - Hedge exit on regime improve

---

## Audit 4: Options Assignment & Exercise

**Priority:** P0
**Risk:** Unexpected stock position → margin call, account restriction

### Checklist

| # | Check | Status | Notes |
|---|-------|:------:|-------|
| 4.1 | Short leg ITM at expiration: Is auto-assignment handled? | ☐ | |
| 4.2 | Long leg ITM at expiration: Is auto-exercise handled? | ☐ | |
| 4.3 | Early assignment detection: Is OnOrderEvent checking for assignment? | ☐ | |
| 4.4 | Pin risk (strike = spot): Is Friday close monitored? | ☐ | |
| 4.5 | Ex-dividend assignment: Short calls closed before ex-div? | ☐ | |
| 4.6 | Friday Expiration Firewall: Are expiring options closed by Friday 3:00 PM? | ☐ | |
| 4.7 | DTE exit rule: Are spreads closed by 5 DTE? | ☐ | |
| 4.8 | Resulting stock position: If assigned, is stock immediately liquidated? | ☐ | |

### Assignment Risk Scenarios

| Scenario | What Happens | Risk | Mitigation |
|----------|--------------|------|------------|
| Short call ITM at expiry | Assigned -100 shares | Margin call, short stock | Close before expiry |
| Short put ITM at expiry | Assigned +100 shares | Margin call, long stock | Close before expiry |
| Early assignment on short | Unexpected assignment | Orphaned long leg | Monitor OnOrderEvent |
| Ex-dividend early assign | Call assigned day before | Forced dividend payment | Track ex-div dates |

### Code Locations

- `engines/satellite/options_engine.py: check_friday_firewall_exit()` - Friday close
- `main.py: OnOrderEvent()` - Assignment detection
- `config.py: SPREAD_DTE_EXIT` - DTE exit threshold

---

## Audit 5: Order Rejection & Partial Fill

**Priority:** P0
**Risk:** Incomplete positions, orphaned legs, stuck orders

### Checklist

| # | Check | Status | Notes |
|---|-------|:------:|-------|
| 5.1 | Insufficient buying power: Is order rejection handled gracefully? | ☐ | |
| 5.2 | Options not available: Is illiquid strike rejection handled? | ☐ | |
| 5.3 | Price moved too fast: Is limit order timeout implemented? | ☐ | |
| 5.4 | Partial fill on spread: Are both legs eventually filled or cancelled? | ☐ | |
| 5.5 | Market halt during order: Is order stuck detection working? | ☐ | |
| 5.6 | Combo order rejection: Does sequential fallback engage? | ☐ | |
| 5.7 | Orphaned leg detection: Is leg 1 closed if leg 2 fails? | ☐ | |
| 5.8 | Stale order cleanup: Are orders > 5 minutes cancelled? | ☐ | |

### Order State Machine

```
PENDING → SUBMITTED → PARTIALLY_FILLED → FILLED
                   ↘ REJECTED
                   ↘ CANCELLED
                   ↘ INVALID
```

### Rejection Handling

| Rejection Reason | Current Handling | Recommended |
|------------------|------------------|-------------|
| Insufficient margin | Log, skip order | ✅ Implemented |
| Invalid symbol | Log, skip order | ✅ Implemented |
| Market closed | Log, queue for next day | ⚠️ Verify |
| Price too far from market | Log, retry with market order | ⚠️ Verify |
| Partial fill timeout | Cancel remaining | ⚠️ Add timeout |

### Code Locations

- `main.py: OnOrderEvent()` - Order event handling
- `portfolio/portfolio_router.py: _execute_sequential_close()` - Fallback close
- `main.py: _cleanup_stale_orders()` - Stale order cancellation
- `execution/execution_engine.py: on_order_event()` - Order tracking

---

## Audit 6: Multi-Engine Conflict

**Priority:** P2
**Risk:** Engines stepping on each other's positions, over-allocation

### Checklist

| # | Check | Status | Notes |
|---|-------|:------:|-------|
| 6.1 | Trend + Options both want QQQ exposure: Is beta double-counted? | ☐ | |
| 6.2 | MR + Trend both trigger on same symbol: Who wins? | ☐ | |
| 6.3 | Hedge exit + Trend entry same bar: Is order of operations correct? | ☐ | |
| 6.4 | Options close + Kill switch: Do both try to close same position? | ☐ | |
| 6.5 | Priority scaling: Is ENGINE_PRIORITY consistently applied? | ☐ | |
| 6.6 | Total allocation: Does MAX_TOTAL_ALLOCATION (95%) cap work? | ☐ | |
| 6.7 | Margin-weighted allocation: Is SYMBOL_LEVERAGE applied correctly? | ☐ | |

### Engine Priority Reference

| Engine | Priority | Scaled Down |
|--------|:--------:|:-----------:|
| RISK | 0 | Never |
| HEDGE | 1 | Last |
| TREND | 2 | Third |
| OPT | 3 | Second |
| OPT_INTRADAY | 4 | Second |
| MR | 5 | First |

### Allocation Limits

| Engine | Max Allocation | Margin Weight |
|--------|:--------------:|:-------------:|
| Trend (QLD) | 15% | 2.0× |
| Trend (SSO) | 12% | 2.0× |
| Trend (TNA) | 8% | 3.0× |
| Trend (FAS) | 5% | 3.0× |
| Options | 25% | 1.0× |
| MR (TQQQ/SOXL) | 10% | 3.0× |
| Hedge (TMF) | 20% | 3.0× |
| Hedge (PSQ) | 10% | 1.0× |

### Code Locations

- `portfolio/portfolio_router.py: _apply_allocation_scaling()` - Priority scaling
- `config.py: ENGINE_PRIORITY` - Priority definitions
- `config.py: MAX_TOTAL_ALLOCATION` - Total cap
- `config.py: SYMBOL_LEVERAGE` - Margin weights

---

## Audit 7: Live vs Backtest Parity

**Priority:** P1
**Risk:** Behavior differs in production vs testing

### Checklist

| # | Check | Status | Notes |
|---|-------|:------:|-------|
| 7.1 | `Securities[x].Price` returns 0 before first tick: Is this handled? | ☐ | |
| 7.2 | `Portfolio[x].Invested` may lag fill by 1 bar: Is this accounted for? | ☐ | |
| 7.3 | Options chain availability: Are delays handled in live? | ☐ | |
| 7.4 | Slippage on 3× ETFs: Is real slippage higher than backtest? | ☐ | |
| 7.5 | Order fill assumptions: Backtest fills at mid, live at worse | ☐ | |
| 7.6 | Data gaps: What happens if a bar is missing? | ☐ | |
| 7.7 | Indicator warmup: Are indicators ready before trading starts? | ☐ | |
| 7.8 | Time resolution: Are minute bars aggregated correctly in live? | ☐ | |

### Known Differences

| Feature | Backtest Behavior | Live Behavior | Impact |
|---------|-------------------|---------------|--------|
| Price access | Always available | May be 0 before first tick | Division by zero |
| Order fills | Instant at mid | Delayed, slippage | Worse entries/exits |
| Options chains | Instant, complete | Delayed, may be partial | Missed opportunities |
| Corporate actions | Automatic adjustment | Manual handling needed | Price discontinuity |
| Market halts | Not simulated | Real halts occur | Stuck orders |

### Code Locations

- `main.py: Initialize()` - Warmup period setting
- `main.py: OnData()` - Data validation
- `engines/core/regime_engine.py: calculate_score()` - Indicator readiness

---

## Audit 8: IBKR-Specific Rules

**Priority:** P1
**Risk:** Broker rules differ from backtest assumptions, account restrictions

### Checklist

| # | Check | Status | Notes |
|---|-------|:------:|-------|
| 8.1 | PDT rule (< $25K): Are day trades limited to 3 per 5 days? | ☐ | |
| 8.2 | Margin requirements: Does IBKR require more than QC assumes? | ☐ | |
| 8.3 | Options trading level: Is account Level 3+ for spreads? | ☐ | |
| 8.4 | Hard-to-borrow for shorts: Can PSQ be shorted? | ☐ | |
| 8.5 | API rate limits: Are order submissions throttled? | ☐ | |
| 8.6 | Reg-T margin vs Portfolio margin: Which is account using? | ☐ | |
| 8.7 | Overnight margin: Are 3× ETF overnight requirements met? | ☐ | |
| 8.8 | Options exercise fee: Is $15 exercise fee accounted for? | ☐ | |

### IBKR Margin Requirements

| Asset Type | Reg-T Initial | Reg-T Maintenance | Portfolio Margin |
|------------|:-------------:|:-----------------:|:----------------:|
| Stock | 50% | 25% | 15% |
| 2× ETF | 75% | 50% | 30% |
| 3× ETF | 90% | 75% | 45% |
| Options (long) | 100% | 100% | 100% |
| Options (spread) | Max loss + buffer | Max loss | Max loss |

### PDT Rule Summary

- **Applies to:** Accounts < $25,000
- **Limit:** 3 day trades per rolling 5-day period
- **Day trade:** Open and close same position same day
- **Violation:** Account restricted to closing trades only for 90 days

### Code Locations

- `config.py: MARGIN_PRE_CHECK_BUFFER` - Margin buffer (1.50)
- `portfolio/portfolio_router.py: verify_margin_available()` - Margin pre-check
- `main.py: _cleanup_stale_orders()` - Rate limiting protection

---

## Audit 9: Capital Flow

**Priority:** P3
**Risk:** External events break allocation assumptions

### Checklist

| # | Check | Status | Notes |
|---|-------|:------:|-------|
| 9.1 | Deposit mid-day: Are allocations recalculated? | ☐ | |
| 9.2 | Withdrawal mid-day: Are positions scaled down if over-allocated? | ☐ | |
| 9.3 | Dividend received: Is SHV target adjusted? | ☐ | |
| 9.4 | Stock split: Are position quantities updated? | ☐ | |
| 9.5 | Merger/acquisition: Are merged symbols handled? | ☐ | |
| 9.6 | Interest/fees deducted: Is overnight equity change handled? | ☐ | |
| 9.7 | Lockbox calculation: Is lockbox updated with capital changes? | ☐ | |

### Capital Event Handling

| Event | Current Handling | Risk |
|-------|------------------|------|
| Deposit | Recalc on next cycle | Undersized positions until recalc |
| Withdrawal | Recalc on next cycle | Over-allocated until recalc |
| Dividend | Added to cash | SHV target may be off |
| Split | Freeze symbol | May miss trading day |
| Margin call | Kill switch tier 3 | Forces liquidation |

### Code Locations

- `engines/core/capital_engine.py` - Capital calculations
- `main.py: _check_splits()` - Split handling
- `config.py: LOCKBOX_*` - Lockbox configuration

---

## Audit 10: Logging & Monitoring

**Priority:** P3
**Risk:** Can't debug issues in production

### Checklist

| # | Check | Status | Notes |
|---|-------|:------:|-------|
| 10.1 | Critical events logged: Entries, exits, stops, kill switch | ☐ | |
| 10.2 | Log size manageable: Won't hit 5MB QC daily limit | ☐ | |
| 10.3 | Kill switch alerts: External notification on trigger? | ☐ | |
| 10.4 | Daily P&L tracking: Is performance logged daily? | ☐ | |
| 10.5 | Position reconciliation: Match QC vs IBKR positions | ☐ | |
| 10.6 | Error logging: Are exceptions captured with stack traces? | ☐ | |
| 10.7 | Regime logging: Is regime score logged for analysis? | ☐ | |
| 10.8 | Governor logging: Is drawdown scale logged? | ☐ | |

### Log Categories

| Category | Prefix | When Logged | trades_only |
|----------|--------|-------------|:-----------:|
| Fills | `FILL:` | Every order fill | True |
| Entries | `*_ENTRY:` | Position opened | True |
| Exits | `*_EXIT:` | Position closed | True |
| Kill Switch | `KS_*:` | Kill switch trigger | True |
| Governor | `GOVERNOR:` | Scale changes | True |
| Regime | `REGIME:` | Score updates | False |
| Debug | `DEBUG:` | Development only | False |

### Code Locations

- `main.py: Log()` - Primary logging
- `engines/*/: log()` - Engine-specific logging
- QC_RULES.md - Logging patterns

---

## Audit Execution Plan

### Phase 1: Pre-Live (Must Complete)

| Week | Audits | Owner | Status |
|:----:|--------|:-----:|:------:|
| 1 | Audit 1 (State Persistence) | | ☐ |
| 1 | Audit 4 (Options Assignment) | | ☐ |
| 1 | Audit 5 (Order Rejection) | | ☐ |
| 2 | Audit 3 (Regime Transition) | | ☐ |
| 2 | Audit 7 (Live vs Backtest) | | ☐ |
| 2 | Audit 8 (IBKR-Specific) | | ☐ |

### Phase 2: First Week Live

| Day | Audits | Owner | Status |
|:---:|--------|:-----:|:------:|
| 1-2 | Audit 2 (Timing) | | ☐ |
| 3-4 | Audit 6 (Multi-Engine) | | ☐ |
| 5 | Audit 10 (Logging) | | ☐ |

### Phase 3: First Month Live

| Week | Audits | Owner | Status |
|:----:|--------|:-----:|:------:|
| 2-3 | Audit 9 (Capital Flow) | | ☐ |
| 4 | Full regression test | | ☐ |

---

## Sign-Off

| Role | Name | Date | Signature |
|------|------|------|-----------|
| Developer | | | |
| Reviewer | | | |
| Account Owner | | | |

---

## Revision History

| Version | Date | Author | Changes |
|:-------:|------|--------|---------|
| 1.0 | 2026-02-04 | Claude Opus 4.5 | Initial checklist |
