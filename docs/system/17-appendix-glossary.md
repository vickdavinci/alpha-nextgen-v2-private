# Section 17: Appendix - Glossary

This glossary provides definitions for all acronyms, system-specific terms, mathematical formulas, and asset classes used throughout the Alpha NextGen documentation.

---

## Acronyms

| Acronym | Full Name | Definition |
|---------|-----------|------------|
| **ATR** | Average True Range | Volatility indicator measuring average range of price bars over N periods (default: 14 days) |
| **BB** | Bollinger Bands | Volatility envelope consisting of middle band (SMA) plus/minus standard deviations |
| **EOD** | End of Day | Refers to 15:45 ET processing window when daily signals are generated and MOO orders submitted |
| **ET** | Eastern Time | US Eastern timezone (EST/EDT) used for all system timing |
| **ETF** | Exchange-Traded Fund | Tradeable security that tracks an index, commodity, or basket of assets |
| **EMA** | Exponential Moving Average | Moving average giving more weight to recent data points |
| **IBKR** | Interactive Brokers | The brokerage platform used for live trading execution |
| **LEAN** | LEAN Engine | QuantConnect's open-source algorithmic trading engine |
| **LIFO** | Last In, First Out | Liquidation priority where most recently purchased shares are sold first |
| **MOO** | Market-On-Open | Order type that executes at the next day's opening auction price |
| **MR** | Mean Reversion | Trading strategy that profits from prices returning to average after extreme moves |
| **P&L** | Profit and Loss | Financial gain or loss from trading activities |
| **QC** | QuantConnect | Cloud-based algorithmic trading platform used for development and backtesting |
| **ROC** | Rate of Change | Percentage change in price over a specified period |
| **RSI** | Relative Strength Index | Momentum oscillator measuring speed and magnitude of price changes (0-100 scale) |
| **SMA** | Simple Moving Average | Average price over N periods, equally weighted |
| **SOD** | Start of Day | Refers to 09:33 ET baseline after MOO orders have filled |
| **WTD** | Week-to-Date | Cumulative performance since Monday's market open |

---

## System-Specific Terms

### A-C

| Term | Definition |
|------|------------|
| **Alpha NextGen** | The name of this multi-strategy algorithmic trading system |
| **Authority Hierarchy** | Six-level priority system determining which rules override others (Level 1 = highest) |
| **Band Basis Exit** | Trend exit signal triggered when price closes below the Bollinger middle band |
| **Bandwidth** | Bollinger Band width metric: (Upper - Lower) / Middle; values < 0.10 indicate compression |
| **Breakout** | Price closing above/below a significant level (e.g., BB upper band) |
| **Capital Phase** | Account size classification (SEED or GROWTH) that determines position limits |
| **Chandelier Exit** | Trailing stop calculated as Highest High minus (ATR x Multiplier) |
| **Circuit Breaker** | Automatic safeguard that triggers protective actions when thresholds are breached |
| **Cold Start** | First 5 trading days after algorithm launch or kill switch reset |
| **Compression** | Bollinger Band squeeze (bandwidth < 0.10) indicating low volatility before breakout |

### D-G

| Term | Definition |
|------|------------|
| **Days Running** | Counter tracking consecutive trading days since algorithm start or last kill switch |
| **Delta Shares** | Difference between target position size and current holdings |
| **Exposure Group** | Category of correlated symbols (NASDAQ_BETA, SPY_BETA, RATES) with aggregate limits |
| **Frozen Symbol** | Symbol temporarily excluded from trading due to split detection |
| **Gap Filter** | Safeguard blocking intraday entries when SPY opens down >= 1.5% from prior close |
| **GROWTH Phase** | Capital phase for accounts $100k-$500k with 40% max single position |

### H-L

| Term | Definition |
|------|------------|
| **Hedge Engine** | Strategy engine managing TMF and PSQ allocations based on regime score |
| **Highest High** | Maximum price reached since position entry; used for Chandelier stop calculation |
| **Hub-and-Spoke Architecture** | Design pattern where Portfolio Router (hub) coordinates all strategy engines (spokes) |
| **Indicator Warmup** | Period required for indicators to have sufficient data for valid calculations |
| **Kill Switch** | Emergency safeguard triggered by 3% daily portfolio loss; liquidates all positions |
| **Lockbox** | Virtual protection mechanism that excludes a portion of equity from tradeable capital |
| **Lockbox Milestone** | Equity threshold ($100k, $200k) that triggers locking 10% of capital |

### M-P

| Term | Definition |
|------|------------|
| **MOO Fallback** | Backup mechanism at 09:31 that converts unfilled MOO orders to market orders |
| **ObjectStore** | QuantConnect's persistent key-value storage for saving state across restarts |
| **Panic Mode** | Safeguard triggered by SPY -4% intraday; liquidates leveraged longs but keeps hedges |
| **Portfolio Router** | Central coordination component that aggregates, validates, and routes all orders |
| **Position Manager** | Component tracking entry prices, highest highs, and stop levels for each position |
| **Proxy Symbol** | Symbol used for data/calculations but never traded (SPY, RSP, HYG, IEF for regime) |

### R-S

| Term | Definition |
|------|------------|
| **Regime Engine** | Core engine calculating market state score (0-100) from four factors |
| **Regime Score** | Composite 0-100 value indicating market conditions; drives hedge and entry decisions |
| **Regime State** | Categorical classification: RISK_ON, NEUTRAL, CAUTIOUS, DEFENSIVE, RISK_OFF |
| **Risk Engine** | Core engine implementing all circuit breakers and safeguards |
| **SEED Phase** | Capital phase for accounts $50k-$100k with 50% max single position |
| **Smoothed Score** | Regime score after exponential smoothing (alpha = 0.30) to reduce whipsaw |
| **Split Guard** | Safeguard that freezes trading on symbols experiencing corporate action splits |

### T-Z

| Term | Definition |
|------|------------|
| **TargetWeight** | Data object expressing strategy intention: symbol, weight, urgency, reason |
| **Time Guard** | Daily 13:55-14:10 ET window blocking all entries (Fed announcement protection) |
| **Tradeable Equity** | Portfolio value minus lockbox amount; basis for position sizing |
| **Traded Symbol** | Symbol that the system actively buys and sells (TQQQ, SOXL, QLD, SSO, TMF, PSQ, SHV) |
| **Trend Engine** | Strategy engine detecting Bollinger Band compression breakouts for QLD/SSO |
| **Urgency** | Signal priority classification: IMMEDIATE (execute now) or EOD (submit as MOO) |
| **Vol Shock** | Safeguard triggered when SPY 1-minute bar range exceeds 3x ATR; pauses entries 15 min |
| **Warm Entry** | Conservative position entry during cold start period (50% of normal size) |
| **Weekly Breaker** | Safeguard triggered by 5% WTD loss; reduces all new position sizes by 50% |
| **Yield Sleeve** | Strategy managing SHV allocation for idle cash |

---

## Regime States

| State | Score Range | New Longs | Hedges | Cold Start |
|-------|:-----------:|:---------:|:------:|:----------:|
| **RISK_ON** | 70-100 | Allowed | None | Allowed |
| **NEUTRAL** | 50-69 | Allowed | None | If > 50 |
| **CAUTIOUS** | 40-49 | Allowed | 10% TMF | Blocked |
| **DEFENSIVE** | 30-39 | Reduced | 15% TMF + 5% PSQ | Blocked |
| **RISK_OFF** | 0-29 | Blocked | 20% TMF + 10% PSQ | Blocked |

---

## Mathematical Formulas

### Bollinger Bands
```
Middle Band = SMA(20)
Upper Band  = Middle + (2.0 x StdDev)
Lower Band  = Middle - (2.0 x StdDev)
Bandwidth   = (Upper - Lower) / Middle
```

### Chandelier Exit (Trailing Stop)
```
Stop Level = Highest High Since Entry - (Multiplier x ATR(14))

Multiplier Selection:
  Profit < 15%  -> 3.0 (widest)
  Profit 15-25% -> 2.0 (medium)
  Profit > 25%  -> 1.5 (tightest)

Rule: Stop NEVER moves down
```

### Regime Score
```
Raw Score = (Trend x 0.35) + (Volatility x 0.25) + (Breadth x 0.25) + (Credit x 0.15)

Smoothed Score = (0.30 x Raw) + (0.70 x Previous Smoothed)
```

### Realized Volatility
```
1. Calculate daily returns: r_i = (Close_i - Close_{i-1}) / Close_{i-1}
2. Standard deviation: sigma = StdDev(r_1 ... r_20)
3. Annualize: Realized Vol = sigma x sqrt(252)
```

### Kill Switch Check
```
Trigger if EITHER:
  (equity_prior_close - current) / equity_prior_close >= 0.03
  OR
  (equity_sod - current) / equity_sod >= 0.03
```

### Position Sizing
```
Target Value = Tradeable Equity x Target Weight
Delta Value  = Target Value - Current Holdings
Delta Shares = floor(Delta Value / Current Price)
```

---

## Symbols Reference

### Traded Symbols

| Symbol | Name | Leverage | Strategy | Overnight |
|--------|------|:--------:|----------|:---------:|
| **TQQQ** | ProShares UltraPro QQQ | 3x | Mean Reversion | No |
| **SOXL** | Direxion Semiconductor Bull | 3x | Mean Reversion | No |
| **QLD** | ProShares Ultra QQQ | 2x | Trend, Cold Start | Yes |
| **SSO** | ProShares Ultra S&P 500 | 2x | Trend, Cold Start | Yes |
| **TMF** | Direxion 20+ Year Treasury Bull | 3x | Hedge | Yes |
| **PSQ** | ProShares Short QQQ | 1x | Hedge | Yes |
| **SHV** | iShares Short Treasury Bond | 1x | Yield, Lockbox | Yes |

### Proxy Symbols (Data Only - Never Traded)

| Symbol | Name | Purpose |
|--------|------|---------|
| **SPY** | SPDR S&P 500 ETF | Trend factor, volatility, panic mode, gap filter, vol shock |
| **RSP** | Invesco S&P 500 Equal Weight | Breadth factor (equal weight vs cap weight) |
| **HYG** | iShares iBoxx High Yield Corporate Bond | Credit factor (risk appetite) |
| **IEF** | iShares 7-10 Year Treasury Bond | Credit factor (safe haven) |

### Exposure Groups

| Group | Symbols | Max Net Long | Max Net Short | Max Gross |
|-------|---------|:------------:|:-------------:|:---------:|
| **NASDAQ_BETA** | TQQQ, QLD, SOXL, PSQ | 50% | 30% | 75% |
| **SPY_BETA** | SSO | 40% | 0% | 40% |
| **RATES** | TMF, SHV | 40% | 0% | 40% |

---

## Key Thresholds Quick Reference

| Threshold | Value | Triggers |
|-----------|:-----:|----------|
| Kill Switch | 3% daily loss | Full liquidation, cold start reset |
| Panic Mode | SPY -4% intraday | Liquidate leveraged longs only |
| Weekly Breaker | 5% WTD loss | 50% sizing reduction |
| Gap Filter | SPY -1.5% gap | Block MR/warm entries for day |
| Vol Shock | SPY bar > 3x ATR | 15-minute entry pause |
| BB Compression | Bandwidth < 10% | Trend entry eligible |
| RSI Oversold | RSI(5) < 25 | MR entry eligible |
| MR Drop | -2.5% from open | MR entry condition |
| MR Target | +2% profit | MR exit target |
| MR Stop | -2% loss | MR exit stop |
| Trend Regime Min | Score >= 40 | Trend entry allowed |
| Trend Regime Exit | Score < 30 | Force trend exit |
| Warm Entry Regime | Score > 50 | Cold start entry allowed |
| Hedge Start | Score < 40 | Begin TMF allocation |

---

## Time Reference (All Eastern Time)

| Time | Event |
|------|-------|
| 09:00 | Algorithm warmup verification |
| 09:25 | Pre-market setup, set `equity_prior_close` |
| 09:30 | Market open, MOO orders execute |
| 09:31 | MOO fallback check |
| 09:33 | Set `equity_sod`, check gap filter |
| 10:00 | Warm entry check, MR window opens |
| 13:55 | Time guard starts (entries blocked) |
| 14:10 | Time guard ends |
| 15:00 | MR entry window closes |
| 15:45 | TQQQ/SOXL force close, EOD processing, MOO submission |
| 16:00 | Market close, state persistence |

---

## Authority Hierarchy

| Level | Name | Examples | Override Power |
|:-----:|------|----------|----------------|
| 1 | Operational Safety | Broker connection, data freshness, symbol halts | Highest |
| 2 | Circuit Breakers | Kill switch, panic mode, weekly breaker | Overrides 3-6 |
| 3 | Regime Constraints | Score < 30 blocks longs, hedges required | Overrides 4-6 |
| 4 | Capital Constraints | Phase limits, exposure caps, lockbox | Overrides 5-6 |
| 5 | Strategy Signals | Trend, MR, Hedge, Yield, Cold Start | Overrides 6 |
| 6 | Execution Preferences | Order type, SHV liquidation priority | Lowest |

---

## ObjectStore Keys

| Key | Contents |
|-----|----------|
| `ALPHA_NEXTGEN_CAPITAL` | Phase, lockbox, milestones |
| `ALPHA_NEXTGEN_COLDSTART` | Days running, warm entry status |
| `ALPHA_NEXTGEN_POSITIONS` | Entry prices, stops, highest highs |
| `ALPHA_NEXTGEN_REGIME` | Smoothed score, previous raw score |
| `ALPHA_NEXTGEN_RISK` | Kill dates, prior close |
| `ALPHA_NEXTGEN_WEEKLY` | Week start equity, breaker status |

---

*Previous Section: [16 - Appendix: Parameters](16-appendix-parameters.md)*

*[Table of Contents](00-table-of-contents.md)*
