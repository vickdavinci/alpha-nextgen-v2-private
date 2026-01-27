# Reporting Mechanism Design

> **Status**: Planning Document (for review)
>
> This document outlines the reporting needs for Alpha NextGen and proposes implementation approaches.

---

## Reporting Requirements

### What We Need to Track

| Category | Metrics | Frequency |
|----------|---------|-----------|
| **Performance** | Daily P&L, Cumulative return, Sharpe ratio | Daily |
| **Risk** | Max drawdown, Current drawdown, Risk events | Real-time |
| **Trading** | Orders placed, Fills, Slippage | Per-trade |
| **Strategy** | Signals generated, Hit rates, Win/loss ratio | Daily |
| **System** | Errors, Warnings, State changes | Real-time |

### Report Types

1. **Daily Summary** - End-of-day email/file with key metrics
2. **Trade Log** - Every order and fill with timestamps
3. **Performance Dashboard** - Weekly/monthly aggregates
4. **Alert System** - Real-time notifications for risk events

---

## Implementation Options

### Option A: QC Built-in Reporting

**QuantConnect provides:**
- Automatic performance charts in backtest UI
- Order/fill history in logs
- Equity curve visualization

**Limitations:**
- Only available in QC web UI
- No custom formatting
- No email notifications

**Effort**: Minimal (already available)

### Option B: Log-Based Reporting

**Approach:**
- Structured logging with tags (e.g., `[TRADE]`, `[RISK]`, `[PERF]`)
- Post-process logs to generate reports

**Example:**
```python
self.Log(f"[TRADE] {symbol} | BUY | Qty={qty} | Price=${price:.2f}")
self.Log(f"[PERF] Daily | P&L=${pnl:.2f} | Return={ret:.2%}")
self.Log(f"[RISK] KillSwitch | Loss={loss:.2%} | Threshold=3%")
```

**Benefits:**
- Simple to implement
- Works in both backtest and live
- Can parse logs later

**Effort**: Low

### Option C: ObjectStore Metrics

**Approach:**
- Store daily metrics in ObjectStore as JSON
- Read back for analysis

**Example:**
```python
# At EOD
metrics = {
    "date": str(self.Time.date()),
    "equity": self.Portfolio.TotalPortfolioValue,
    "pnl": daily_pnl,
    "orders": order_count,
    "regime_score": regime_score,
}
self.ObjectStore.Save("metrics/" + date_key, json.dumps(metrics))
```

**Benefits:**
- Persistent across restarts
- Structured data for analysis
- Can build dashboards

**Effort**: Medium

### Option D: External Reporting Service

**Approach:**
- Send metrics to external service (webhook, database, etc.)
- Build dashboard on external platform

**Example targets:**
- Google Sheets (via Apps Script webhook)
- Notion database (via API)
- Custom database (PostgreSQL, etc.)
- Grafana dashboards

**Benefits:**
- Rich visualization
- Custom alerts
- Historical analysis

**Effort**: High (requires external setup)

---

## Recommended Approach

**Phase 1: Log-Based + ObjectStore (Immediate)**

1. Add structured logging with consistent tags
2. Store daily metrics in ObjectStore
3. Create log parser script for post-analysis

**Phase 2: Daily Summary Report (Short-term)**

1. Generate markdown summary at EOD
2. Store in ObjectStore
3. Optional: webhook to Slack/email

**Phase 3: Dashboard (Future)**

1. Export ObjectStore data to external DB
2. Build Grafana or similar dashboard
3. Real-time alerts for risk events

---

## Phase 1 Implementation

### 1. Logging Standards

Add to `main.py`:

```python
def _log_trade(self, action: str, symbol: str, qty: int, price: float, reason: str) -> None:
    """Structured trade logging."""
    self.Log(f"[TRADE] {action} | {symbol} | Qty={qty} | Price=${price:.2f} | {reason}")

def _log_performance(self, date: str, pnl: float, equity: float, return_pct: float) -> None:
    """Structured performance logging."""
    self.Log(f"[PERF] {date} | P&L=${pnl:,.2f} | Equity=${equity:,.2f} | Return={return_pct:+.2%}")

def _log_risk(self, event: str, details: str) -> None:
    """Structured risk event logging."""
    self.Log(f"[RISK] {event} | {details}")

def _log_signal(self, source: str, symbol: str, action: str, reason: str) -> None:
    """Structured signal logging."""
    self.Log(f"[SIGNAL] {source} | {symbol} | {action} | {reason}")
```

### 2. Daily Metrics Collection

```python
def _collect_daily_metrics(self) -> dict:
    """Collect metrics at end of day."""
    return {
        "date": str(self.Time.date()),
        "equity": round(self.Portfolio.TotalPortfolioValue, 2),
        "cash": round(self.Portfolio.Cash, 2),
        "equity_sod": getattr(self, '_equity_sod', 0),
        "daily_pnl": round(self.Portfolio.TotalPortfolioValue - getattr(self, '_equity_sod', 0), 2),
        "daily_return_pct": round((self.Portfolio.TotalPortfolioValue / getattr(self, '_equity_sod', 1) - 1) * 100, 4),
        "regime_score": getattr(self, '_last_regime_score', 50.0),
        "orders_placed": getattr(self, '_daily_order_count', 0),
        "risk_events": getattr(self, '_daily_risk_events', []),
        "positions": {str(k): v.Quantity for k, v in self.Portfolio.items() if v.Invested},
    }

def _save_daily_metrics(self, metrics: dict) -> None:
    """Save metrics to ObjectStore."""
    key = f"metrics/daily/{metrics['date']}"
    self.ObjectStore.Save(key, json.dumps(metrics))
    self.Log(f"[METRICS] Saved daily metrics to {key}")
```

### 3. Log Parser Script

Create `scripts/parse_backtest_logs.py`:

```python
#!/usr/bin/env python3
"""Parse backtest logs to extract structured data."""

import re
import json
from pathlib import Path
from collections import defaultdict

def parse_logs(log_file: str) -> dict:
    """Parse structured logs from backtest."""
    trades = []
    performance = []
    risk_events = []
    signals = []

    patterns = {
        'trade': re.compile(r'\[TRADE\] (\w+) \| (\w+) \| Qty=(\d+) \| Price=\$([\d.]+) \| (.+)'),
        'perf': re.compile(r'\[PERF\] ([\d-]+) \| P&L=\$([\d,.+-]+) \| Equity=\$([\d,.]+) \| Return=([\d.+-]+)%'),
        'risk': re.compile(r'\[RISK\] (\w+) \| (.+)'),
        'signal': re.compile(r'\[SIGNAL\] (\w+) \| (\w+) \| (\w+) \| (.+)'),
    }

    with open(log_file) as f:
        for line in f:
            for tag, pattern in patterns.items():
                match = pattern.search(line)
                if match:
                    if tag == 'trade':
                        trades.append({
                            'action': match.group(1),
                            'symbol': match.group(2),
                            'qty': int(match.group(3)),
                            'price': float(match.group(4)),
                            'reason': match.group(5),
                        })
                    elif tag == 'perf':
                        performance.append({
                            'date': match.group(1),
                            'pnl': float(match.group(2).replace(',', '')),
                            'equity': float(match.group(3).replace(',', '')),
                            'return_pct': float(match.group(4)),
                        })
                    elif tag == 'risk':
                        risk_events.append({
                            'event': match.group(1),
                            'details': match.group(2),
                        })
                    elif tag == 'signal':
                        signals.append({
                            'source': match.group(1),
                            'symbol': match.group(2),
                            'action': match.group(3),
                            'reason': match.group(4),
                        })

    return {
        'trades': trades,
        'performance': performance,
        'risk_events': risk_events,
        'signals': signals,
        'summary': {
            'total_trades': len(trades),
            'total_signals': len(signals),
            'risk_event_count': len(risk_events),
            'trading_days': len(performance),
        }
    }

if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("Usage: parse_backtest_logs.py <log_file>")
        sys.exit(1)

    results = parse_logs(sys.argv[1])
    print(json.dumps(results, indent=2))
```

---

## Report Templates

### Daily Summary Template

```markdown
# Daily Report - {date}

## Performance
- Opening Equity: ${equity_sod:,.2f}
- Closing Equity: ${equity:,.2f}
- Daily P&L: ${daily_pnl:+,.2f} ({daily_return_pct:+.2%})

## Market Regime
- Score: {regime_score}/100
- Classification: {regime_class}

## Trading Activity
- Orders Placed: {orders_placed}
- Fills: {fills}
- Signals Generated: {signals}

## Risk Events
{risk_events_list}

## Positions
| Symbol | Quantity | Value | Weight |
|--------|----------|-------|--------|
{positions_table}
```

### Weekly Summary Template

```markdown
# Weekly Report - Week of {week_start}

## Performance Summary
- Starting Equity: ${week_start_equity:,.2f}
- Ending Equity: ${week_end_equity:,.2f}
- Weekly Return: {weekly_return:+.2%}
- Best Day: {best_day} ({best_return:+.2%})
- Worst Day: {worst_day} ({worst_return:+.2%})

## Trading Summary
- Total Orders: {total_orders}
- Win Rate: {win_rate:.1%}
- Avg Win: {avg_win:+.2%}
- Avg Loss: {avg_loss:.2%}

## Strategy Breakdown
| Strategy | Signals | Trades | P&L |
|----------|---------|--------|-----|
| Trend    | {trend_signals} | {trend_trades} | ${trend_pnl:+,.2f} |
| MR       | {mr_signals} | {mr_trades} | ${mr_pnl:+,.2f} |
| Hedge    | {hedge_signals} | {hedge_trades} | ${hedge_pnl:+,.2f} |
```

---

## Implementation Timeline

| Phase | Deliverable | Effort |
|-------|-------------|--------|
| 1.1 | Structured logging methods | 1 hour |
| 1.2 | Daily metrics collection | 2 hours |
| 1.3 | ObjectStore save/load | 1 hour |
| 1.4 | Log parser script | 2 hours |
| 2.1 | Daily summary generation | 2 hours |
| 2.2 | Weekly aggregation | 2 hours |
| 3.1 | External export (future) | TBD |
| 3.2 | Dashboard (future) | TBD |

---

## Open Questions

1. **Email notifications**: Do we need real-time email alerts for risk events?
2. **Data retention**: How long should we keep historical metrics?
3. **External service**: Preference for dashboard platform (Grafana, Google Sheets, Notion)?
4. **Alert thresholds**: What events should trigger immediate notifications?

---

*Created: 2026-01-25*
