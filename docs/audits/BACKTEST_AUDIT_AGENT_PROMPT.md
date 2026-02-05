# BACKTEST AUDIT AGENT PROMPT

You are analyzing a backtest for the Alpha NextGen V2 algorithmic trading system.

## YOUR TASK
Read the backtest log file, analyze performance, verify all systems functioned correctly, and produce a structured audit report with actionable recommendations.

## CONTEXT
- Log file: `docs/audits/logs/stage2/{LOG_FILE_NAME}`
- Backtest period: {START_DATE} to {END_DATE}
- Starting capital: $50,000
- Market context: {BULL/BEAR/CHOPPY}

## STEP 1: Read Reference Files (DO THIS FIRST)
1. Read `config.py` — all thresholds, parameters, allocation percentages
2. Read `CLAUDE.md` — system architecture, critical rules, key times
3. Read the backtest log file

## STEP 2: Performance Summary
Extract from the log file:
- Final equity and net return %
- Total orders / trades
- Win rate, average win, average loss
- Max drawdown (date and %)
- Sharpe ratio if available

Present as a table.

## STEP 3: Engine-by-Engine Breakdown
For EACH engine, grep the logs and report:

### 3A. Trend Engine (QLD/SSO/TNA/FAS)
- Search: `TREND_ENTRY`, `TREND_EXIT`, `FILL.*QLD|SSO|TNA|FAS`
- Count: entries, exits, win/loss, avg hold period
- Check: ADX scores at entry (were entries blocked by ADX threshold?)
- Check: Did regime-adaptive ADX work? (regime>75 should allow ADX>15)
- Flag: Any `ENTRY_BLOCKED` with regime>70 (should NOT happen in bull)

### 3B. Options Engine (QQQ Spreads)
- Search: `SPREAD`, `BULL_CALL`, `BEAR_PUT`, `FILL.*QQQ`
- Count: entries, exits, profit/loss per spread
- Check: VASS routing (VIX<15=debit monthly, 15-25=debit weekly, >25=credit weekly)
- Check: Spread width, DTE at entry, contracts per spread
- Check: Any `SPREAD: BLOCKED - Insufficient margin` (count and frequency)
- Check: Expiration Hammer firing (`EXPIRATION_HAMMER`)
- Check: Any option exercises or assignments
- Flag: Any `OPTIONS_EOD: Blocked by Governor` (count blocked days)

### 3C. Mean Reversion Engine (TQQQ/SOXL)
- Search: `MR_ENTRY`, `MR_EXIT`, `FILL.*TQQQ|SOXL`
- Check: All MR positions closed by 15:45 (NO overnight holds)
- Check: Entry conditions (RSI < 25, VIX filter)
- Flag: Any TQQQ/SOXL positions held past 16:00

### 3D. Hedge Engine (TMF/PSQ)
- Search: `HEDGE`, `FILL.*TMF|PSQ`
- Check: Hedges only active when regime < 50 (HEDGE_REGIME_GATE)
- Check: Hedge sizing correct for regime level
- Flag: Any hedges placed during RISK_ON regime (should NOT happen)

### 3E. Yield Sleeve (SHV)
- Search: `YIELD`, `FILL.*SHV`
- Check: SHV used for idle cash management
- Check: Lockbox amount never traded

## STEP 4: Risk & Safeguard Verification

### 4A. Kill Switch
- Search: `KILL_SWITCH`, `KS_TIER`
- Count: Total triggers, by tier (1/2/3)
- Check: Tier 1 at -2%, Tier 2 at -4%, Tier 3 at -6%
- Check: After Tier 3, cold start resets
- Flag: >10 KS triggers = system too aggressive or thresholds too tight

### 4B. Drawdown Governor
- Search: `DRAWDOWN_GOVERNOR`
- Track: All scale changes (100%→75%→50%→25%→0%)
- Track: All STEP_UP recoveries (with recovery %)
- Track: All REGIME_OVERRIDE triggers
- Track: All STEP_DOWN_BLOCKED (immunity active)
- Check: HWM initialized at starting capital ($50,000)
- Check: Governor never stuck at 0% for >10 days without override attempt
- Flag: Death spiral pattern (stuck at 0/25% for >30 days)

### 4C. Other Safeguards
- Search: `PANIC_MODE` — count SPY -4% triggers
- Search: `WEEKLY_BREAKER` — count -5% WTD triggers
- Search: `GAP_FILTER` — count SPY -1.5% gap blocks
- Search: `VOL_SHOCK` — count 3× ATR pauses
- Search: `TIME_GUARD` — verify entries blocked 13:55-14:10
- Search: `SPLIT_GUARD` — any corporate action freezes

## STEP 5: Funnel Analysis (Signal Loss)
Track the signal pipeline from generation to execution:

```
Stage 1: Regime scores computed → How many days in each state?
Stage 2: Entry signals generated → Count by engine
Stage 3: Signals blocked → Count by reason (ADX, regime, position limit, margin, Governor)
Stage 4: Orders submitted → Count by type (Market, MOO)
Stage 5: Orders filled → Fill rate %
```

Identify the biggest leakage point. Present as a funnel table.

## STEP 6: Timeline Verification
Verify the daily timeline events fire correctly:

| Time | Event | Log Pattern | Check |
|------|-------|-------------|-------|
| 09:25 | Pre-market setup | `PRE_MARKET` | Equity baseline set |
| 09:33 | SOD baseline | `equity_sod` | Gap filter checked |
| 10:00 | Warm entry | `WARM_ENTRY` | Cold start check |
| 13:55 | Time guard start | `TIME_GUARD` | Entries blocked |
| 15:45 | MR force close | `MR_FORCE_CLOSE` or `TIME_EXIT` | TQQQ/SOXL liquidated |
| 15:45 | EOD processing | `EOD` | Signals queued |
| 16:00 | State persistence | `STATE: SAVED` | All state persisted |

Flag any missing events.

## STEP 7: Regime Analysis
- Search: `REGIME:` or regime score logs
- Distribution: What % of days in RISK_ON / NEUTRAL / CAUTIOUS / DEFENSIVE?
- Latency: How many days to detect bull→bear transition? (compare regime score vs market drawdown dates)
- False signals: Any days regime was RISK_ON during significant market decline?
- Correlation: Does regime score track with actual market performance?

## STEP 8: Smoke Signals (Critical Failure Flags)
Search for these critical keywords:

| Severity | Pattern | Expected | Action if Found |
|----------|---------|----------|-----------------|
| CRITICAL | `ERROR` or `EXCEPTION` | 0 | Investigate crash |
| CRITICAL | `MARGIN_ERROR` | 0 | Position sizing bug |
| CRITICAL | `SIGN_MISMATCH` | 0 | Spread pairing bug |
| CRITICAL | `NAKED` or `ORPHAN` | 0 | Spread leg unpaired |
| WARN | `SLIPPAGE_EXCEEDED` | <10 | Review fill quality |
| WARN | `ASSIGNMENT` or `EXERCISE` | 0 | Options auto-exercise |
| INFO | `EXPIRATION_HAMMER` | Count | Options cleanup working |
| INFO | `FRIDAY_FIREWALL` | ~Weekly | Friday close working |

## STEP 9: Optimization Recommendations

Based on your analysis, provide recommendations in priority order:

### P0 — CRITICAL (Blocking trades or causing cascading failures)
- Issues that prevent the bot from trading entirely
- Issues causing the death spiral or governor trap
- Safety violations (overnight 3x holds, missing force close)

### P1 — HIGH (Major performance leakage)
- Kill switch triggering too frequently
- Regime detection latency (>5 days to detect crash)
- Options blocked for extended periods (>30 days)
- Win rate below 25% for any engine

### P2 — MEDIUM (Optimization opportunities)
- ADX threshold tuning (too restrictive or too loose)
- Position sizing improvements
- Spread width or DTE optimization
- Governor threshold tuning

### P3 — LOW (Minor improvements)
- Logging improvements
- Code cleanup
- Parameter fine-tuning

For EACH recommendation, provide:
1. **What**: The specific issue
2. **Evidence**: Log line or metric that proves it
3. **Impact**: Estimated improvement
4. **Fix**: Specific config change or code change needed

## STEP 10: Scorecard

Rate each system 1-5:

| System | Score | Status | Key Finding |
|--------|:-----:|--------|-------------|
| Trend Engine | /5 | | |
| Options Engine | /5 | | |
| MR Engine | /5 | | |
| Hedge Engine | /5 | | |
| Kill Switch | /5 | | |
| Drawdown Governor | /5 | | |
| Regime Detection | /5 | | |
| Overnight Safety | /5 | | |
| State Persistence | /5 | | |
| Overall | /5 | | |

Scoring:
- 5 = Working perfectly, no issues
- 4 = Working well, minor tuning needed
- 3 = Functional but with notable gaps
- 2 = Significant issues affecting performance
- 1 = Broken or critically impaired

## OUTPUT FORMAT
Save your complete audit report to:
`docs/audits/V3_0_{BACKTEST_NAME}_audit.md`
