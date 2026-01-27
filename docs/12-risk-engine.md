# Section 12: Risk Engine

## 12.1 Purpose and Philosophy

The Risk Engine implements all **circuit breakers and safeguards**. Its job is to prevent catastrophic losses and ensure the system survives adverse conditions.

### 12.1.1 Defense in Depth

Multiple layers of protection address different risk scenarios:

| Safeguard | Threat Addressed |
|-----------|------------------|
| **Kill Switch** | Single-day catastrophic loss |
| **Panic Mode** | Flash crash / market meltdown |
| **Weekly Breaker** | Prolonged bleeding drawdown |
| **Gap Filter** | Gap-down morning weakness |
| **Vol Shock** | Extreme short-term volatility |
| **Time Guard** | Fed announcement volatility |
| **Split Guard** | Corporate action data errors |

Some safeguards will never trigger in normal conditions, but they're ready if needed.

### 12.1.2 Automatic Response

Risk events trigger **automatic responses**—no manual intervention required:

| Property | Value |
|----------|-------|
| Human judgment | Not required during crisis |
| Response time | Immediate (within 1 minute) |
| Consistency | Same response every time |
| Emotion | None (rules-based) |

The system doesn't hesitate or second-guess during crisis; it acts immediately according to predefined rules.

### 12.1.3 Authority Level

The Risk Engine operates at **Level 2** in the authority hierarchy—second only to operational safety:

```
Level 1: Operational Safety (broker, data, halts)
Level 2: CIRCUIT BREAKERS ← Risk Engine
Level 3: Regime Constraints
Level 4: Capital Constraints
Level 5: Strategy Signals
Level 6: Execution Preferences
```

Risk Engine decisions **override** all strategy signals and portfolio allocations.

---

## 12.2 Kill Switch (Daily −3%)

### 12.2.1 Purpose

Prevent a single bad day from causing irreparable damage.

| Loss Level | Recovery Needed | Assessment |
|-----------:|:---------------:|------------|
| 3% | 3.1% | Painful but recoverable |
| 5% | 5.3% | Significant setback |
| 10% | 11.1% | Devastating |
| 20% | 25.0% | Account-threatening |

**The kill switch ensures we never experience the larger losses.**

### 12.2.2 Two-Tier Implementation

The kill switch uses **two baselines** to catch different scenarios:

#### Baseline 1: `equity_prior_close`

| Property | Value |
|----------|-------|
| Set at | 09:25 AM (before market open) |
| Value | Portfolio value at previous day's close |
| Purpose | Catches **gap-down scenarios** |

If the portfolio gaps down 3%+ on open, the kill switch triggers immediately at 09:30.

#### Baseline 2: `equity_sod` (Start of Day)

| Property | Value |
|----------|-------|
| Set at | 09:33 AM (after MOO fills) |
| Value | Portfolio value after opening auction |
| Purpose | Catches **intraday crashes** |

If MOO fills moved the portfolio significantly, we measure subsequent losses from the post-fill level.

### 12.2.3 Trigger Condition

Kill switch triggers if loss from **EITHER baseline** exceeds 3%:

```
TRIGGER if:
    (equity_prior_close - current_equity) / equity_prior_close >= 0.03
    OR
    (equity_sod - current_equity) / equity_sod >= 0.03
```

Whichever happens first.

### 12.2.4 Actions on Trigger

**Immediate actions (in order):**

| Step | Action | Details |
|:----:|--------|---------|
| 1 | Cancel all pending orders | MOO orders, limit orders, any outstanding |
| 2 | Liquidate ALL positions | Longs, hedges, yield—everything |
| 3 | Disable new entries | For remainder of day |
| 4 | Reset `days_running` to 0 | Triggers new cold start period |
| 5 | Save kill date | To persistent storage |
| 6 | Log details | Full information about trigger |

### 12.2.5 Liquidation Details

**ALL positions are liquidated via market orders:**

| Position Type | Action |
|---------------|--------|
| Trend (QLD, SSO) | Sell all |
| Mean Reversion (TQQQ, SOXL) | Sell all |
| Hedges (TMF, PSQ) | Sell all |
| Yield (SHV) | Sell all |

**Nothing is spared.** The goal is to go to 100% cash immediately.

### 12.2.6 Recovery

The algorithm remains in "killed" state until the next trading day:

| Behavior | Description |
|----------|-------------|
| New positions | ❌ Blocked for remainder of day |
| Final state | 100% cash after liquidation fills |
| Next day | Algorithm starts fresh in cold start mode |
| `days_running` | Reset to 0 (new 5-day cold start) |

### 12.2.7 Example Scenario

```
09:25 AM: equity_prior_close = $52,000
09:33 AM: equity_sod = $51,500 (after MOO fills)

10:42 AM: current_equity = $50,300

Check vs prior_close:
  Loss = ($52,000 - $50,300) / $52,000 = 3.27%
  3.27% >= 3% → TRIGGER

Actions:
  1. Cancel pending orders
  2. Liquidate: QLD (200 shares), TQQQ (150 shares), 
               TMF (100 shares), SHV (500 shares)
  3. Disable entries
  4. days_running = 0
  5. Save: last_kill_date = "2024-01-15"
```

---

## 12.3 Panic Mode (SPY −4% Intraday)

### 12.3.1 Purpose

Respond to **flash crash events** where the broader market is in freefall. Different from kill switch—panic mode is about **market conditions**, not portfolio performance.

### 12.3.2 Trigger Condition

SPY intraday decline exceeds 4% from today's opening price:

```
TRIGGER when:
    (SPY_open - SPY_current) / SPY_open >= 0.04
```

### 12.3.3 Actions on Trigger

| Action | Positions Affected |
|--------|-------------------|
| **Liquidate** | All leveraged longs (TQQQ, QLD, SSO, SOXL) |
| **KEEP** | Hedge positions (TMF, PSQ) |
| **KEEP** | Yield positions (SHV) |
| **Disable** | New long entries for remainder of day |

### 12.3.4 Why Keep Hedges?

During a crash, hedges are **doing their job**:

| Instrument | Behavior During Crash |
|------------|----------------------|
| TMF | Rising (flight to safety) |
| PSQ | Rising (inverse of falling Nasdaq) |

Liquidating hedges during the crash would **remove protection** exactly when it's needed most.

### 12.3.5 Difference from Kill Switch

| Aspect | Kill Switch | Panic Mode |
|--------|-------------|------------|
| Trigger | Portfolio loss (−3%) | Market drop (SPY −4%) |
| Liquidates | EVERYTHING | Only leveraged longs |
| Hedges | Sold | **Kept** |
| Yield | Sold | **Kept** |
| Reset cold start | Yes | No |

### 12.3.6 Example Scenario

```
09:30 AM: SPY opens at $450.00
11:15 AM: SPY drops to $431.50

Check:
  Drop = ($450.00 - $431.50) / $450.00 = 4.11%
  4.11% >= 4% → PANIC MODE TRIGGERED

Actions:
  1. Liquidate QLD position (market sell)
  2. Liquidate TQQQ position (market sell)
  3. KEEP TMF position (hedge working)
  4. KEEP PSQ position (hedge working)
  5. KEEP SHV position (safe)
  6. Block new long entries

Result:
  Portfolio is now: TMF + PSQ + SHV + Cash
  Hedges continue to provide protection
```

---

## 12.4 Weekly Circuit Breaker (−5% WTD)

### 12.4.1 Purpose

Prevent prolonged bleeding from destroying the account. A string of small daily losses can accumulate into large drawdowns.

### 12.4.2 Tracking

| Property | Value |
|----------|-------|
| Baseline set | Monday market open |
| Baseline value | `week_start_equity` |
| Comparison | Throughout the week |

### 12.4.3 Trigger Condition

Week-to-date loss exceeds 5%:

```
TRIGGER when:
    (week_start_equity - current_equity) / week_start_equity >= 0.05
```

### 12.4.4 Actions on Trigger

**Reduce all position sizes by 50%** for the remainder of the week:

| Aspect | Normal | Weekly Breaker Active |
|--------|:------:|:---------------------:|
| New entry sizing | 100% | 50% |
| Existing positions | Unchanged | Unchanged |
| Strategy signals | Active | Active (reduced size) |

**Does NOT force liquidation** of existing positions—only reduces new exposure.

### 12.4.5 Reset

The weekly breaker resets automatically at **Monday open**:

```
Monday 09:30 AM:
  1. Set week_start_equity = current portfolio value
  2. Clear weekly_breaker_triggered flag
  3. Return to normal (100%) sizing
```

### 12.4.6 Example Scenario

```
Monday open: week_start_equity = $100,000

Monday close: $98,000 (−2%)
Tuesday close: $96,500 (−3.5% WTD)
Wednesday 11:00 AM: $94,800 (−5.2% WTD)

WEEKLY BREAKER TRIGGERED

Wednesday-Friday:
  All new positions sized at 50% of normal
  Example: Trend signal would normally be $30k → Now $15k

Next Monday:
  Reset, back to normal sizing
```

---

## 12.5 Gap Filter (−1.5%)

### 12.5.1 Purpose

Avoid entering new positions on days that start poorly. A gap-down morning often continues lower.

### 12.5.2 Trigger Condition

SPY opens 1.5% or more below prior day's close:

```
TRIGGER when:
    (SPY_prior_close - SPY_open) / SPY_prior_close >= 0.015
```

Checked **once at 09:33 AM** after open.

### 12.5.3 Actions on Trigger

**Block intraday entries only:**

| Activity | Status |
|----------|:------:|
| Mean reversion entries | ❌ BLOCKED |
| Cold start warm entry | ❌ BLOCKED |
| Swing entries (MOO already submitted) | ✅ ALLOWED |
| All exits | ✅ ALLOWED |
| Hedge adjustments | ✅ ALLOWED |

### 12.5.4 Duration

Gap filter applies for **entire trading day**. Resets automatically next day.

### 12.5.5 Rationale

| Gap Down Size | Implication |
|:-------------:|-------------|
| < 1% | Normal overnight movement |
| 1% – 1.5% | Notable but not alarming |
| **≥ 1.5%** | Significant weakness, likely to continue |

On gap-down days, mean reversion "buy the dip" signals are **less reliable**—the dip may keep dipping.

---

## 12.6 Vol Shock (3× ATR)

### 12.6.1 Purpose

Pause during extreme short-term volatility. When a single 1-minute bar has an enormous range, something unusual is happening—wait for clarity.

### 12.6.2 Trigger Condition

SPY 1-minute bar range exceeds 3× the 14-period ATR on 1-minute bars:

```
TRIGGER when:
    (Bar_High - Bar_Low) > 3 × ATR(14, 1-minute)
```

### 12.6.3 Actions on Trigger

**Pause new entries for 15 minutes:**

| Activity | Status |
|----------|:------:|
| Mean reversion entries | ⏸️ PAUSED (15 min) |
| Trend entries | ⏸️ PAUSED (15 min) |
| All exits | ✅ CONTINUE |
| Stop loss monitoring | ✅ CONTINUE |

**Capital preservation never pauses.**

### 12.6.4 Duration

**15 minutes from trigger time.** After 15 minutes, normal entry activity resumes.

### 12.6.5 Example

```
11:23 AM: SPY 1-minute bar
  High: $448.50
  Low: $446.00
  Range: $2.50
  
  14-period ATR (1-min): $0.75
  Threshold: 3 × $0.75 = $2.25
  
  $2.50 > $2.25 → VOL SHOCK TRIGGERED

11:23 - 11:38 AM:
  New entries blocked
  Exits continue normally
  
11:38 AM:
  Vol shock expires
  Normal operations resume
```

---

## 12.7 Time Guard (13:55 – 14:10 ET)

### 12.7.1 Purpose

Avoid entering positions during the typical **Fed announcement window**. FOMC announcements are at 2:00 PM ET; this buffer protects against volatility around that time.

### 12.7.2 Scope

Applies **every trading day**, not just Fed days:

| Reason | Benefit |
|--------|---------|
| Simpler implementation | No Fed calendar required |
| Minimal opportunity cost | Only 15 minutes |
| Protects against other events | Other 2 PM announcements |

### 12.7.3 Actions During Guard

| Activity | Status |
|----------|:------:|
| All entry signals | ❌ IGNORED |
| Exits | ✅ CONTINUE |
| Stop monitoring | ✅ CONTINUE |

### 12.7.4 Duration

**13:55 to 14:10 ET** each trading day. Automatic activation and deactivation.

```
13:54:59 → Normal operations
13:55:00 → Time guard ACTIVE (entries blocked)
14:09:59 → Time guard ACTIVE
14:10:00 → Normal operations resume
```

---

## 12.8 Split Guard

### 12.8.1 Purpose

Avoid trading symbols experiencing **corporate actions**. Stock splits can cause apparent large price moves that aren't real trading opportunities.

### 12.8.2 Detection

Check incoming data for split events. The QuantConnect data provider flags these in the data stream.

### 12.8.3 Actions on Detection

**Freeze trading on affected symbol for the day:**

| Activity | Status |
|----------|:------:|
| New entries | ❌ BLOCKED |
| Exits | ❌ BLOCKED (let split adjust) |
| Scanning | ⏭️ SKIP this symbol |

### 12.8.4 Duration

Remainder of trading day. Next day, normal trading resumes with split-adjusted prices.

### 12.8.5 Rationale

| Problem | Impact |
|---------|--------|
| 2:1 split | Price appears to drop 50% |
| Indicators | Bollinger Bands, ATR corrupted |
| Stop levels | Invalid until recalculated |

Better to skip one day than to trade on corrupted data.

---

## 12.9 Safeguard Summary Table

| Safeguard | Trigger | Action | Duration | Resets |
|-----------|---------|--------|----------|--------|
| **Kill Switch** | −3% daily (either baseline) | Liquidate ALL, disable trading, reset cold start | Rest of day | Next day (in cold start) |
| **Panic Mode** | SPY −4% intraday | Liquidate leveraged longs, keep hedges | Rest of day | Next day |
| **Weekly Breaker** | −5% week-to-date | Reduce sizing 50% | Rest of week | Monday open |
| **Gap Filter** | SPY gaps down ≥1.5% | Block intraday entries | Rest of day | Next day |
| **Vol Shock** | SPY 1-min range > 3×ATR | Pause entries 15 minutes | 15 minutes | Auto |
| **Time Guard** | 13:55 – 14:10 daily | Block all entries | 15 minutes | 14:10 |
| **Split Guard** | Corporate action detected | Freeze affected symbol | Rest of day | Next day |

---

## 12.10 Mermaid Diagram: Risk Engine Checks

```mermaid
flowchart TD
    START["Every Minute<br/>During Market Hours"]
    
    subgraph KILL["Kill Switch Check"]
        K1["Get current_equity"]
        K2{"Loss from prior_close<br/>≥ 3%?"}
        K3{"Loss from SOD<br/>≥ 3%?"}
        K_TRIGGER["🚨 KILL SWITCH<br/>Liquidate ALL"]
    end
    
    subgraph PANIC["Panic Mode Check"]
        P1["Get SPY price"]
        P2{"SPY drop from open<br/>≥ 4%?"}
        P_TRIGGER["⚠️ PANIC MODE<br/>Liquidate longs only"]
    end
    
    subgraph VOL["Vol Shock Check"]
        V1["Get SPY 1-min bar"]
        V2{"Bar range > 3× ATR?"}
        V_TRIGGER["⏸️ VOL SHOCK<br/>Pause entries 15 min"]
    end
    
    subgraph TIME["Time Guard Check"]
        T1{"Time 13:55-14:10?"}
        T_TRIGGER["🕐 TIME GUARD<br/>Block entries"]
    end
    
    CONTINUE["Continue Normal<br/>Processing"]
    
    START --> K1
    K1 --> K2
    K2 -->|Yes| K_TRIGGER
    K2 -->|No| K3
    K3 -->|Yes| K_TRIGGER
    K3 -->|No| P1
    
    P1 --> P2
    P2 -->|Yes| P_TRIGGER
    P2 -->|No| V1
    
    V1 --> V2
    V2 -->|Yes| V_TRIGGER
    V2 -->|No| T1
    
    T1 -->|Yes| T_TRIGGER
    T1 -->|No| CONTINUE
    
    V_TRIGGER --> T1
    P_TRIGGER --> CONTINUE
```

---

## 12.11 Mermaid Diagram: Kill Switch Flow

```mermaid
flowchart TD
    TRIGGER["Kill Switch Triggered<br/>(Loss ≥ 3%)"]
    
    subgraph CANCEL["Step 1: Cancel Orders"]
        C1["Cancel all MOO orders"]
        C2["Cancel all limit orders"]
        C3["Cancel any pending orders"]
    end
    
    subgraph LIQUIDATE["Step 2: Liquidate ALL"]
        L1["Sell all TQQQ"]
        L2["Sell all QLD"]
        L3["Sell all SSO"]
        L4["Sell all SOXL"]
        L5["Sell all TMF"]
        L6["Sell all PSQ"]
        L7["Sell all SHV"]
    end
    
    subgraph DISABLE["Step 3: Disable"]
        D1["Set kill_switch_active = True"]
        D2["Block all new entries"]
    end
    
    subgraph RESET["Step 4: Reset State"]
        R1["days_running = 0"]
        R2["Save last_kill_date"]
        R3["Clear position tracking"]
    end
    
    subgraph LOG["Step 5: Log"]
        LOG1["Log trigger details"]
        LOG2["Log liquidation fills"]
    end
    
    FINAL["Algorithm in KILLED state<br/>100% Cash<br/>Wait for next day"]
    
    TRIGGER --> CANCEL
    C1 --> C2 --> C3
    CANCEL --> LIQUIDATE
    L1 --> L2 --> L3 --> L4 --> L5 --> L6 --> L7
    LIQUIDATE --> DISABLE
    D1 --> D2
    DISABLE --> RESET
    R1 --> R2 --> R3
    RESET --> LOG
    LOG1 --> LOG2
    LOG --> FINAL
```

---

## 12.12 Interaction Between Safeguards

### 12.12.1 Priority Order

When multiple safeguards could apply, they're checked in order:

```
1. Kill Switch (highest priority - checked first)
2. Panic Mode
3. Vol Shock
4. Gap Filter (checked once at 09:33)
5. Time Guard
6. Split Guard
```

### 12.12.2 Cascading Effects

| If This Triggers | Then This Happens |
|------------------|-------------------|
| Kill Switch | Everything else irrelevant (all liquidated) |
| Panic Mode | Vol shock still checked for remaining positions |
| Vol Shock | Time guard still applies |
| Gap Filter | Applies all day; vol shock can still trigger |

### 12.12.3 Example: Multiple Safeguards

```
09:33 AM: SPY gapped down 1.8%
  → Gap Filter ACTIVE (MR entries blocked all day)

10:15 AM: SPY 1-min bar range = 3.5× ATR
  → Vol Shock ACTIVE (additional 15 min pause)
  
10:30 AM: Vol Shock expires
  → Gap Filter still active (MR still blocked)
  
13:55 PM: Time Guard activates
  → All entries blocked (redundant with gap filter for MR)
  
14:10 PM: Time Guard expires
  → Gap Filter still active until close
```

---

## 12.13 State Tracking

### 12.13.1 Intraday State Variables

| Variable | Type | Purpose |
|----------|------|---------|
| `equity_prior_close` | Float | Kill switch baseline 1 |
| `equity_sod` | Float | Kill switch baseline 2 |
| `kill_switch_active` | Boolean | Trading disabled flag |
| `panic_mode_active` | Boolean | Longs-only liquidation mode |
| `vol_shock_until` | DateTime | When vol shock expires |
| `gap_filter_active` | Boolean | Intraday entry block |
| `weekly_breaker_active` | Boolean | 50% sizing mode |

### 12.13.2 Persisted State Variables

| Variable | Type | Purpose |
|----------|------|---------|
| `last_kill_date` | String | Date of most recent kill switch |
| `week_start_equity` | Float | Monday baseline for weekly breaker |

---

## 12.14 Integration with Other Engines

### 12.14.1 Risk Engine Inputs

| Source | Data | Used For |
|--------|------|----------|
| **Data Layer** | SPY prices (minute) | Panic mode, gap filter, vol shock |
| **Data Layer** | SPY ATR (1-min) | Vol shock threshold |
| **Capital Engine** | Portfolio equity | Kill switch calculation |
| **Scheduling** | Current time | Time guard |

### 12.14.2 Risk Engine Outputs

| Destination | Data | Purpose |
|-------------|------|---------|
| **All Engines** | `can_enter_new_positions` | Gate for new entries |
| **Portfolio Router** | `sizing_multiplier` | 1.0 or 0.5 (weekly breaker) |
| **Execution Engine** | Liquidation orders | Kill switch / panic mode |
| **Cold Start Engine** | `days_running` reset | Kill switch effect |

---

## 12.15 Parameter Reference

### Kill Switch Parameters

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `KILL_SWITCH_PCT` | 0.03 | 3% daily loss threshold |

### Panic Mode Parameters

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `PANIC_MODE_PCT` | 0.04 | 4% SPY intraday drop |

### Weekly Breaker Parameters

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `WEEKLY_BREAKER_PCT` | 0.05 | 5% week-to-date loss |
| `WEEKLY_SIZE_REDUCTION` | 0.50 | 50% sizing reduction |

### Gap Filter Parameters

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `GAP_FILTER_PCT` | 0.015 | 1.5% gap down threshold |

### Vol Shock Parameters

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `VOL_SHOCK_ATR_MULT` | 3.0 | ATR multiplier for trigger |
| `VOL_SHOCK_PAUSE_MIN` | 15 | Minutes to pause |

### Time Guard Parameters

| Parameter | Value | Description |
|-----------|:-----:|-------------|
| `TIME_GUARD_START` | 13:55 | Start of blocked window |
| `TIME_GUARD_END` | 14:10 | End of blocked window |

---

## 12.16 Key Design Decisions Summary

| Decision | Rationale |
|----------|-----------|
| **Two kill switch baselines** | Catches both gap-down and intraday crashes |
| **3% kill switch threshold** | Painful but recoverable; prevents catastrophic losses |
| **Panic mode keeps hedges** | Hedges are working during crash; don't remove protection |
| **Weekly breaker reduces, doesn't liquidate** | Stay in game but reduce bleeding |
| **Gap filter blocks MR only** | Trend MOOs already submitted; MR relies on "normal" conditions |
| **15-minute vol shock pause** | Brief pause for clarity; not full-day block |
| **Daily time guard** | Simpler than tracking Fed calendar; minimal cost |
| **All liquidations via market orders** | Certainty of execution during crisis |
| **Kill switch resets cold start** | After significant loss, restart conservatively |

---

*Next Section: [13 - Execution Engine](13-execution-engine.md)*

*Previous Section: [11 - Portfolio Router](11-portfolio-router.md)*
