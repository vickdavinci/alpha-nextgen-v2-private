#!/usr/bin/env python3
"""
QuantConnect Backtest Audit Agent

This agent:
1. Pulls backtest data from QuantConnect (logs, orders, trades, overview)
2. Analyzes the data using the BACKTEST_AUDIT_AGENT_PROMPT
3. Generates a comprehensive audit report

Usage:
    # Full audit of a backtest
    python scripts/qc_audit_agent.py "V5.1-Governor-2022H1"

    # List available backtests first
    python scripts/qc_audit_agent.py --list

    # Use existing local files (skip pull)
    python scripts/qc_audit_agent.py "V5.1-Governor-2022H1" --local

    # Specify market context
    python scripts/qc_audit_agent.py "V5.1-Governor-2022H1" --market BEAR
"""

import argparse
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# Add project root to path for imports
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.qc_pull_backtest import (
    QCClient,
    find_backtest,
    list_backtests,
    pull_backtest_data,
    sanitize_filename,
)

# ============================================================================
# Configuration
# ============================================================================

STAGE4_DIR = PROJECT_ROOT / "docs" / "audits" / "logs" / "stage4"
AUDITS_DIR = PROJECT_ROOT / "docs" / "audits"
PROMPT_FILE = PROJECT_ROOT / "docs" / "audits" / "BACKTEST_AUDIT_AGENT_PROMPT.md"

# Regime thresholds from config
REGIME_THRESHOLDS = {
    "RISK_ON": 70,
    "UPPER_NEUTRAL": 60,
    "LOWER_NEUTRAL": 50,
    "CAUTIOUS": 40,
    "DEFENSIVE": 30,
    "RISK_OFF": 0,
}


# ============================================================================
# Log Parsing Functions
# ============================================================================


def parse_logs(log_file: Path) -> dict:
    """Parse backtest log file and extract key metrics."""
    if not log_file.exists():
        return {}

    with open(log_file) as f:
        lines = f.readlines()

    data = {
        "regime_logs": [],
        "governor_logs": [],
        "trend_logs": [],
        "options_logs": [],
        "mr_logs": [],
        "hedge_logs": [],
        "risk_logs": [],
        "fill_logs": [],
        "error_logs": [],
        "all_lines": lines,
    }

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        # Categorize logs
        if "REGIME:" in line or "RegimeState" in line:
            data["regime_logs"].append(line)
        if "GOVERNOR" in line or "GOV_SCALE" in line or "DRAWDOWN_GOVERNOR" in line:
            data["governor_logs"].append(line)
        if "TREND" in line:
            data["trend_logs"].append(line)
        if "SPREAD" in line or "VASS" in line or "OPTIONS" in line or "INTRADAY" in line:
            data["options_logs"].append(line)
        if "MR_" in line or "MEAN_REV" in line:
            data["mr_logs"].append(line)
        if "HEDGE" in line:
            data["hedge_logs"].append(line)
        if "KILL_SWITCH" in line or "PANIC" in line or "GAP_FILTER" in line:
            data["risk_logs"].append(line)
        if "FILL" in line:
            data["fill_logs"].append(line)
        if "ERROR" in line or "EXCEPTION" in line:
            data["error_logs"].append(line)

    return data


def extract_regime_distribution(regime_logs: list) -> dict:
    """Extract regime distribution from logs."""
    regime_data = defaultdict(lambda: {"count": 0, "scores": []})

    # Pattern: REGIME: RegimeState(CAUTIOUS | Score=41.9 | ...)
    pattern = r"Score=(\d+\.?\d*)"

    for log in regime_logs:
        match = re.search(pattern, log)
        if match:
            score = float(match.group(1))

            # Classify regime
            if score >= 70:
                regime = "RISK_ON"
            elif score >= 60:
                regime = "UPPER_NEUTRAL"
            elif score >= 50:
                regime = "LOWER_NEUTRAL"
            elif score >= 40:
                regime = "CAUTIOUS"
            elif score >= 30:
                regime = "DEFENSIVE"
            else:
                regime = "RISK_OFF"

            regime_data[regime]["count"] += 1
            regime_data[regime]["scores"].append(score)

    # Calculate averages
    for regime in regime_data:
        scores = regime_data[regime]["scores"]
        if scores:
            regime_data[regime]["avg_score"] = sum(scores) / len(scores)
        else:
            regime_data[regime]["avg_score"] = 0

    return dict(regime_data)


def extract_governor_states(governor_logs: list) -> dict:
    """Extract governor state changes from logs."""
    governor_data = {
        "scale_changes": [],
        "days_by_scale": defaultdict(int),
        "step_ups": 0,
        "step_downs": 0,
    }

    # Pattern for governor scale
    scale_pattern = r"Governor\s*(\d+)%|GOV_SCALE.*?(\d+)%|scale.*?(\d+)%"

    current_scale = 100
    for log in governor_logs:
        match = re.search(scale_pattern, log, re.IGNORECASE)
        if match:
            scale = int(match.group(1) or match.group(2) or match.group(3))
            if scale != current_scale:
                if scale > current_scale:
                    governor_data["step_ups"] += 1
                else:
                    governor_data["step_downs"] += 1
                governor_data["scale_changes"].append((log[:20], current_scale, scale))
                current_scale = scale

        # Count days at each scale
        if "EOD" in log or "16:00" in log:
            governor_data["days_by_scale"][current_scale] += 1

    return governor_data


def parse_trades_csv(trades_file: Path) -> list:
    """Parse trades CSV file."""
    if not trades_file.exists():
        return []

    import csv

    trades = []
    with open(trades_file) as f:
        reader = csv.DictReader(f)
        for row in reader:
            trades.append(row)
    return trades


def parse_orders_csv(orders_file: Path) -> list:
    """Parse orders CSV file."""
    if not orders_file.exists():
        return []

    import csv

    orders = []
    with open(orders_file) as f:
        reader = csv.DictReader(f)
        for row in reader:
            orders.append(row)
    return orders


def parse_overview(overview_file: Path) -> dict:
    """Parse overview file for key statistics."""
    if not overview_file.exists():
        return {}

    with open(overview_file) as f:
        content = f.read()

    stats = {}
    # Extract key metrics
    patterns = {
        "net_profit": r"Net Profit\s*:\s*(-?\d+\.?\d*%?)",
        "sharpe": r"Sharpe Ratio\s*:\s*(-?\d+\.?\d*)",
        "drawdown": r"Drawdown\s*:\s*(-?\d+\.?\d*%?)",
        "win_rate": r"Win Rate\s*:\s*(\d+%?)",
        "total_orders": r"Total Orders\s*:\s*(\d+)",
        "start_equity": r"Start Equity\s*:\s*(\d+)",
        "end_equity": r"End Equity\s*:\s*(\d+\.?\d*)",
    }

    for key, pattern in patterns.items():
        match = re.search(pattern, content)
        if match:
            stats[key] = match.group(1)

    return stats


# ============================================================================
# Audit Analysis Functions
# ============================================================================


def analyze_regime_accuracy(regime_logs: list, market_context: str) -> dict:
    """Analyze if regime identification matches market context."""
    analysis = {
        "context": market_context,
        "expected_dominant": None,
        "actual_dominant": None,
        "match": False,
        "issues": [],
    }

    # Expected dominant regime based on market context
    context_expected = {
        "BULL": "RISK_ON",
        "BEAR": "DEFENSIVE",
        "CHOPPY": "NEUTRAL",
    }
    analysis["expected_dominant"] = context_expected.get(market_context.upper(), "NEUTRAL")

    # Get actual distribution
    distribution = extract_regime_distribution(regime_logs)

    # Find dominant regime
    max_count = 0
    for regime, data in distribution.items():
        if data["count"] > max_count:
            max_count = data["count"]
            analysis["actual_dominant"] = regime

    # Check match
    expected = analysis["expected_dominant"]
    actual = analysis["actual_dominant"]

    if expected == "RISK_ON" and actual in ["RISK_ON", "UPPER_NEUTRAL"]:
        analysis["match"] = True
    elif expected == "DEFENSIVE" and actual in ["DEFENSIVE", "CAUTIOUS", "RISK_OFF"]:
        analysis["match"] = True
    elif expected == "NEUTRAL" and actual in ["UPPER_NEUTRAL", "LOWER_NEUTRAL", "CAUTIOUS"]:
        analysis["match"] = True
    else:
        analysis["issues"].append(
            f"Expected {expected} regime for {market_context} market, got {actual}"
        )

    return analysis


def analyze_regime_trade_attribution(trades: list, regime_logs: list) -> dict:
    """Analyze trades attributed to each regime."""
    # Build a map of dates to regime scores
    date_to_regime = {}
    score_pattern = r"(\d{4}-\d{2}-\d{2}).*Score=(\d+\.?\d*)"

    for log in regime_logs:
        match = re.search(score_pattern, log)
        if match:
            date = match.group(1)
            score = float(match.group(2))
            date_to_regime[date] = score

    # Attribute trades to regimes
    regime_trades = defaultdict(lambda: {"count": 0, "wins": 0, "losses": 0, "pnl": 0})

    for trade in trades:
        entry_time = trade.get("Entry Time", "")
        entry_date = entry_time[:10] if entry_time else ""
        pnl = float(trade.get("P&L", 0) or 0)
        is_win = trade.get("IsWin", "0") == "1"

        # Get regime at entry
        score = date_to_regime.get(entry_date, 50)  # Default to neutral

        if score >= 70:
            regime = "RISK_ON"
        elif score >= 60:
            regime = "UPPER_NEUTRAL"
        elif score >= 50:
            regime = "LOWER_NEUTRAL"
        elif score >= 40:
            regime = "CAUTIOUS"
        elif score >= 30:
            regime = "DEFENSIVE"
        else:
            regime = "RISK_OFF"

        regime_trades[regime]["count"] += 1
        regime_trades[regime]["pnl"] += pnl
        if is_win:
            regime_trades[regime]["wins"] += 1
        else:
            regime_trades[regime]["losses"] += 1

    # Calculate win rates
    for regime in regime_trades:
        total = regime_trades[regime]["count"]
        wins = regime_trades[regime]["wins"]
        regime_trades[regime]["win_rate"] = (wins / total * 100) if total > 0 else 0

    return dict(regime_trades)


def analyze_governor_impact(governor_logs: list, total_days: int) -> dict:
    """Analyze governor impact on trading capacity."""
    governor_data = extract_governor_states(governor_logs)

    # Calculate percentages
    days_by_scale = governor_data["days_by_scale"]
    total_logged = sum(days_by_scale.values()) or total_days or 1

    impact = {
        "days_at_100": days_by_scale.get(100, 0),
        "days_at_75": days_by_scale.get(75, 0),
        "days_at_50": days_by_scale.get(50, 0),
        "days_at_25": days_by_scale.get(25, 0),
        "days_at_0": days_by_scale.get(0, 0),
        "pct_at_0": (days_by_scale.get(0, 0) / total_logged * 100),
        "pct_full_capacity": (days_by_scale.get(100, 0) / total_logged * 100),
        "step_ups": governor_data["step_ups"],
        "step_downs": governor_data["step_downs"],
        "decapacitated": False,
        "issues": [],
    }

    # Check for decapacitation
    if impact["pct_at_0"] > 30:
        impact["decapacitated"] = True
        impact["issues"].append(
            f"CRITICAL: {impact['pct_at_0']:.1f}% of days at 0% capacity - Governor too aggressive"
        )

    if impact["pct_full_capacity"] < 20:
        impact["issues"].append(
            f"WARN: Only {impact['pct_full_capacity']:.1f}% of days at full capacity"
        )

    return impact


# ============================================================================
# Report Generation
# ============================================================================


def generate_audit_report(backtest_name: str, data: dict, market_context: str = "UNKNOWN") -> str:
    """Generate the full audit report."""

    # Parse all data
    logs_data = data.get("logs_data", {})
    trades = data.get("trades", [])
    orders = data.get("orders", [])
    overview = data.get("overview", {})

    regime_logs = logs_data.get("regime_logs", [])
    governor_logs = logs_data.get("governor_logs", [])
    options_logs = logs_data.get("options_logs", [])
    error_logs = logs_data.get("error_logs", [])

    # Run analyses
    regime_distribution = extract_regime_distribution(regime_logs)
    regime_accuracy = analyze_regime_accuracy(regime_logs, market_context)
    regime_trade_attr = analyze_regime_trade_attribution(trades, regime_logs)
    total_days = sum(d["count"] for d in regime_distribution.values()) or 1
    governor_impact = analyze_governor_impact(governor_logs, total_days)

    # Build report
    report = []
    report.append(f"# Backtest Audit Report: {backtest_name}")
    report.append(f"\n**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    report.append(f"**Market Context:** {market_context}")
    report.append("")

    # Executive Summary
    report.append("---")
    report.append("## Executive Summary")
    report.append("")

    issues = []
    if regime_accuracy["issues"]:
        issues.extend(regime_accuracy["issues"])
    if governor_impact["issues"]:
        issues.extend(governor_impact["issues"])
    if error_logs:
        issues.append(f"Found {len(error_logs)} ERROR/EXCEPTION logs")

    if issues:
        for issue in issues[:5]:
            report.append(f"- {issue}")
    else:
        report.append("- No critical issues detected")
    report.append("")

    # Performance Summary
    report.append("---")
    report.append("## Performance Summary")
    report.append("")
    report.append("| Metric | Value |")
    report.append("|--------|-------|")
    report.append(f"| Net Profit | {overview.get('net_profit', 'N/A')} |")
    report.append(f"| Sharpe Ratio | {overview.get('sharpe', 'N/A')} |")
    report.append(f"| Max Drawdown | {overview.get('drawdown', 'N/A')} |")
    report.append(f"| Win Rate | {overview.get('win_rate', 'N/A')} |")
    report.append(f"| Total Orders | {overview.get('total_orders', 'N/A')} |")
    report.append(f"| Total Trades | {len(trades)} |")
    report.append(f"| Start Equity | ${overview.get('start_equity', 'N/A')} |")
    report.append(f"| End Equity | ${overview.get('end_equity', 'N/A')} |")
    report.append("")

    # Regime Distribution
    report.append("---")
    report.append("## Regime Distribution")
    report.append("")
    report.append("| Regime State | Score Range | Days | % of Backtest | Avg Score |")
    report.append("|--------------|-------------|------|---------------|-----------|")

    regime_order = [
        "RISK_ON",
        "UPPER_NEUTRAL",
        "LOWER_NEUTRAL",
        "CAUTIOUS",
        "DEFENSIVE",
        "RISK_OFF",
    ]
    score_ranges = {
        "RISK_ON": ">= 70",
        "UPPER_NEUTRAL": "60-69",
        "LOWER_NEUTRAL": "50-59",
        "CAUTIOUS": "40-49",
        "DEFENSIVE": "30-39",
        "RISK_OFF": "< 30",
    }

    for regime in regime_order:
        data_r = regime_distribution.get(regime, {"count": 0, "avg_score": 0})
        days = data_r["count"]
        pct = (days / total_days * 100) if total_days > 0 else 0
        avg = data_r.get("avg_score", 0)
        report.append(f"| {regime} | {score_ranges[regime]} | {days} | {pct:.1f}% | {avg:.1f} |")

    report.append(f"| **TOTAL** | | **{total_days}** | **100%** | |")
    report.append("")

    # Regime Accuracy
    report.append("### Regime Identification Accuracy")
    report.append("")
    report.append(f"- **Market Context:** {market_context}")
    report.append(f"- **Expected Dominant Regime:** {regime_accuracy['expected_dominant']}")
    report.append(f"- **Actual Dominant Regime:** {regime_accuracy['actual_dominant']}")
    report.append(f"- **Match:** {'YES' if regime_accuracy['match'] else 'NO'}")
    if regime_accuracy["issues"]:
        report.append(f"- **Issues:** {'; '.join(regime_accuracy['issues'])}")
    report.append("")

    # Regime-Trade Attribution
    report.append("---")
    report.append("## Regime-Trade Attribution")
    report.append("")
    report.append("| Regime at Entry | Trades | Wins | Losses | Win Rate | Total P&L |")
    report.append("|-----------------|--------|------|--------|----------|-----------|")

    total_trades = 0
    total_wins = 0
    total_losses = 0
    total_pnl = 0

    for regime in regime_order:
        data_t = regime_trade_attr.get(
            regime, {"count": 0, "wins": 0, "losses": 0, "pnl": 0, "win_rate": 0}
        )
        report.append(
            f"| {regime} | {data_t['count']} | {data_t['wins']} | {data_t['losses']} | {data_t['win_rate']:.1f}% | ${data_t['pnl']:,.0f} |"
        )
        total_trades += data_t["count"]
        total_wins += data_t["wins"]
        total_losses += data_t["losses"]
        total_pnl += data_t["pnl"]

    overall_wr = (total_wins / total_trades * 100) if total_trades > 0 else 0
    report.append(
        f"| **TOTAL** | **{total_trades}** | **{total_wins}** | **{total_losses}** | **{overall_wr:.1f}%** | **${total_pnl:,.0f}** |"
    )
    report.append("")

    # Governor Impact
    report.append("---")
    report.append("## Governor Impact Analysis")
    report.append("")
    report.append("| Metric | Value |")
    report.append("|--------|-------|")
    report.append(
        f"| Days at 100% capacity | {governor_impact['days_at_100']} ({governor_impact['pct_full_capacity']:.1f}%) |"
    )
    report.append(f"| Days at 75% capacity | {governor_impact['days_at_75']} |")
    report.append(f"| Days at 50% capacity | {governor_impact['days_at_50']} |")
    report.append(f"| Days at 25% capacity | {governor_impact['days_at_25']} |")
    report.append(
        f"| Days at 0% (decapacitated) | {governor_impact['days_at_0']} ({governor_impact['pct_at_0']:.1f}%) |"
    )
    report.append(f"| STEP_DOWN events | {governor_impact['step_downs']} |")
    report.append(f"| STEP_UP events | {governor_impact['step_ups']} |")
    report.append("")

    if governor_impact["decapacitated"]:
        report.append("### CRITICAL: Governor Decapacitation Detected")
        report.append("")
        report.append(
            f"The algo spent {governor_impact['pct_at_0']:.1f}% of the backtest at 0% capacity."
        )
        report.append(
            "This indicates a potential death spiral where the Governor prevents recovery."
        )
        report.append("")

    # Options Analysis
    report.append("---")
    report.append("## Options Engine Analysis")
    report.append("")

    vass_rejections = [log for log in options_logs if "VASS_REJECTION" in log]
    spread_blocked = [log for log in options_logs if "BLOCKED" in log.upper()]
    governor_blocked = [log for log in options_logs if "Governor" in log]

    report.append(f"- **VASS Rejections:** {len(vass_rejections)}")
    report.append(f"- **Spreads Blocked (any reason):** {len(spread_blocked)}")
    report.append(f"- **Blocked by Governor:** {len(governor_blocked)}")
    report.append("")

    if vass_rejections:
        report.append("### Sample VASS Rejections (last 5):")
        report.append("```")
        for log in vass_rejections[-5:]:
            report.append(log[:200])
        report.append("```")
        report.append("")

    # Error Logs
    if error_logs:
        report.append("---")
        report.append("## Errors Detected")
        report.append("")
        report.append(f"Found **{len(error_logs)}** ERROR/EXCEPTION logs:")
        report.append("```")
        for log in error_logs[:10]:
            report.append(log[:200])
        report.append("```")
        report.append("")

    # Scorecard
    report.append("---")
    report.append("## Scorecard")
    report.append("")
    report.append("| System | Score | Status |")
    report.append("|--------|:-----:|--------|")

    # Auto-score based on analysis
    regime_score = 4 if regime_accuracy["match"] else 2
    governor_score = 2 if governor_impact["decapacitated"] else 4
    error_score = 5 if not error_logs else 2

    report.append(
        f"| Regime Identification | {regime_score}/5 | {'OK' if regime_accuracy['match'] else 'ISSUES'} |"
    )
    report.append(f"| Regime Navigation | 3/5 | Needs detailed trade analysis |")
    report.append(
        f"| Drawdown Governor | {governor_score}/5 | {'DECAPACITATED' if governor_impact['decapacitated'] else 'OK'} |"
    )
    report.append(f"| Error Handling | {error_score}/5 | {len(error_logs)} errors |")
    report.append("")

    # Recommendations
    report.append("---")
    report.append("## Recommendations")
    report.append("")

    p0_issues = []
    p1_issues = []

    if governor_impact["decapacitated"]:
        p0_issues.append("Governor too aggressive - adjust GOVERNOR_STEP_* thresholds in config.py")

    if not regime_accuracy["match"]:
        p1_issues.append(
            f"Regime identification not matching {market_context} market - review regime factor weights"
        )

    if len(vass_rejections) > 50:
        p1_issues.append(
            f"High VASS rejection rate ({len(vass_rejections)}) - review spread criteria"
        )

    if p0_issues:
        report.append("### P0 — CRITICAL")
        for issue in p0_issues:
            report.append(f"- {issue}")
        report.append("")

    if p1_issues:
        report.append("### P1 — HIGH")
        for issue in p1_issues:
            report.append(f"- {issue}")
        report.append("")

    if not p0_issues and not p1_issues:
        report.append(
            "No critical issues identified. Review detailed analysis above for optimization opportunities."
        )
        report.append("")

    return "\n".join(report)


# ============================================================================
# Main Entry Point
# ============================================================================


def main():
    parser = argparse.ArgumentParser(
        description="QuantConnect Backtest Audit Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s "V5.1-Governor-2022H1"          Full audit of backtest
  %(prog)s --list                          List available backtests
  %(prog)s "V5.1-Governor" --local         Use existing local files
  %(prog)s "V5.1-Governor" --market BEAR   Specify market context
        """,
    )

    parser.add_argument("name", nargs="?", help="Backtest name (partial match supported)")
    parser.add_argument("--list", action="store_true", help="List recent backtests")
    parser.add_argument(
        "--local", action="store_true", help="Use existing local files (skip QC pull)"
    )
    parser.add_argument(
        "--market",
        choices=["BULL", "BEAR", "CHOPPY", "UNKNOWN"],
        default="UNKNOWN",
        help="Market context for the backtest period",
    )
    parser.add_argument("--output", type=str, help="Output file path (default: auto-generated)")

    args = parser.parse_args()

    # List mode
    if args.list:
        client = QCClient()
        list_backtests(client, limit=20)
        return 0

    if not args.name:
        parser.print_help()
        return 1

    print("=" * 60)
    print("  QUANTCONNECT BACKTEST AUDIT AGENT")
    print("=" * 60)
    print()

    # Step 1: Pull data from QC (unless --local)
    safe_name = sanitize_filename(args.name)

    if not args.local:
        print("[1/3] Pulling data from QuantConnect...")
        client = QCClient()

        bt = find_backtest(client, args.name)
        if not bt:
            return 1

        backtest_id = bt["backtestId"]
        backtest_name = bt["name"]
        safe_name = sanitize_filename(backtest_name)

        print(f"  Found: {backtest_name}")

        success = pull_backtest_data(
            client,
            backtest_id,
            pull_logs=True,
            pull_orders=True,
            pull_trades=True,
            pull_overview=True,
            output_dir=STAGE4_DIR,
        )

        if not success:
            print("Error: Failed to pull backtest data")
            return 1
    else:
        print("[1/3] Using existing local files (--local flag)")
        backtest_name = args.name

    # Step 2: Load and parse data
    print()
    print("[2/3] Loading and parsing data...")

    logs_file = STAGE4_DIR / f"{safe_name}_logs.txt"
    trades_file = STAGE4_DIR / f"{safe_name}_trades.csv"
    orders_file = STAGE4_DIR / f"{safe_name}_orders.csv"
    overview_file = STAGE4_DIR / f"{safe_name}_overview.txt"

    # Check for files
    found_files = []
    missing_files = []

    for f, name in [
        (logs_file, "logs"),
        (trades_file, "trades"),
        (orders_file, "orders"),
        (overview_file, "overview"),
    ]:
        if f.exists():
            found_files.append(name)
        else:
            missing_files.append(name)

    print(f"  Found: {', '.join(found_files)}")
    if missing_files:
        print(f"  Missing: {', '.join(missing_files)}")

    if not logs_file.exists():
        print("Error: Log file not found. Cannot proceed with audit.")
        return 1

    # Parse data
    data = {
        "logs_data": parse_logs(logs_file),
        "trades": parse_trades_csv(trades_file),
        "orders": parse_orders_csv(orders_file),
        "overview": parse_overview(overview_file),
    }

    print(f"  Parsed {len(data['logs_data'].get('all_lines', []))} log lines")
    print(f"  Parsed {len(data['trades'])} trades")
    print(f"  Parsed {len(data['orders'])} orders")

    # Step 3: Generate audit report
    print()
    print("[3/3] Generating audit report...")

    report = generate_audit_report(backtest_name, data, args.market)

    # Save report
    if args.output:
        output_file = Path(args.output)
    else:
        version = safe_name.split("-")[0] if "-" in safe_name else "V0"
        output_file = AUDITS_DIR / f"{safe_name}_audit.md"

    output_file.parent.mkdir(parents=True, exist_ok=True)

    with open(output_file, "w") as f:
        f.write(report)

    print(f"  Saved: {output_file}")
    print()
    print("=" * 60)
    print("  AUDIT COMPLETE")
    print("=" * 60)
    print()
    print(f"Report: {output_file}")
    print()

    # Print executive summary
    print("EXECUTIVE SUMMARY:")
    print("-" * 40)
    for line in report.split("\n"):
        if line.startswith("- ") and "Executive Summary" not in line:
            print(line)
            if line.startswith("- No critical"):
                break
        if "## Performance Summary" in line:
            break

    return 0


if __name__ == "__main__":
    sys.exit(main())
