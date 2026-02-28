#!/usr/bin/env python3
"""
Simulate the impact of a conviction floor on V12.8 VASS spread trades.

Reads the V12.8 Trade Detail Report (pre-paired VASS spreads) and the raw
trades CSV (for per-leg MAE/quantity), then simulates what would have happened
if every VASS spread that lost more than X% of its net debit (or max risk for
credits) had been closed at -X% instead.

For winning spreads, we check if the MAE (worst intraday drawdown) exceeded the
floor. If yes, the winner would have been stopped out before recovering.

Usage:
    python scripts/simulate_conviction_floor.py
"""

import csv
import os
import sys
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT_PATH = os.path.join(
    BASE_DIR,
    "docs/audits/logs/stage12.8/V12.8-FullYear2024-R1_TRADE_DETAIL_REPORT.md",
)
CSV_PATH = os.path.join(
    BASE_DIR,
    "docs/audits/logs/stage12.8/V12.8-FullYear2024-R1_trades.csv",
)

FLOOR_LEVELS = [0.25, 0.30, 0.35, 0.40, 0.45, 0.50]
PRIMARY_FLOOR = 0.35


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class VASSSpread:
    """One pre-paired VASS spread from the trade detail report."""

    entry_time: str
    exit_time: str
    strategy: str  # BULL_CALL_DEBIT or BEAR_CALL_CREDIT
    direction: str  # BULLISH or BEARISH
    legs: int
    dte: int
    width: float  # spread width in $
    entry_regime: str
    entry_vix: str
    debit: str  # net debit per share as string (may be "NOT_FOUND")
    exit_reason: str
    gross_pnl: float
    fees: float
    net_pnl: float
    is_win: int
    # Enriched from CSV cross-reference
    quantity: int = 0
    total_debit: float = 0.0  # total $ at risk
    mae_dollars: float = 0.0  # worst drawdown in $ across spread legs
    mae_pct: float = 0.0  # MAE as % of total debit / max risk


@dataclass
class CSVLeg:
    """One leg row from the raw trades CSV."""

    entry_time: str
    symbol: str
    exit_time: str
    direction: int  # 0 = long, 1 = short
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float
    fees: float
    mae: float
    mfe: float
    drawdown: float
    is_win: int
    duration: str
    order_ids: str


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------
def parse_dollar(s: str) -> float:
    """Parse '$-1,234.00' or '$1,234.00' to float."""
    s = s.strip().replace("$", "").replace(",", "")
    return float(s)


def parse_report_table(path: str) -> List[VASSSpread]:
    """Parse the VASS Paired Spread Trades table from the MD report."""
    spreads: List[VASSSpread] = []
    with open(path, "r") as f:
        lines = f.readlines()

    in_table = False
    header_seen = False
    separator_seen = False

    for line in lines:
        stripped = line.strip()

        # Detect start of VASS table
        if "## VASS Paired Spread Trades" in stripped:
            in_table = True
            continue

        if not in_table:
            continue

        # Skip empty lines before table starts
        if not stripped:
            if header_seen and separator_seen:
                # End of table
                break
            continue

        # The header row
        if stripped.startswith("| Entry") and "Strategy" in stripped:
            header_seen = True
            continue

        # The separator row (| --- | --- | ...)
        if stripped.startswith("| ---"):
            separator_seen = True
            continue

        # Stop at the next section
        if stripped.startswith("###") or stripped.startswith("## "):
            break

        if not (header_seen and separator_seen):
            continue

        # Parse a data row
        cols = [c.strip() for c in stripped.split("|")]
        # First and last elements are empty due to leading/trailing |
        cols = [c for c in cols if c != ""]

        if len(cols) < 15:
            continue

        try:
            spread = VASSSpread(
                entry_time=cols[0],
                exit_time=cols[1],
                strategy=cols[2],
                direction=cols[3],
                legs=int(cols[4]),
                dte=int(cols[5]),
                width=float(cols[6]),
                entry_regime=cols[7],
                entry_vix=cols[8],
                debit=cols[9],
                exit_reason=cols[10],
                gross_pnl=parse_dollar(cols[11]),
                fees=parse_dollar(cols[12]),
                net_pnl=parse_dollar(cols[13]),
                is_win=int(cols[14]),
            )
            spreads.append(spread)
        except (ValueError, IndexError) as e:
            print(f"  [WARN] Skipping row parse error: {e} -- {cols[:3]}")
            continue

    return spreads


def parse_csv_legs(path: str) -> List[CSVLeg]:
    """Parse all rows from the raw trades CSV."""
    legs: List[CSVLeg] = []
    with open(path, "r") as f:
        reader = csv.reader(f)
        header = next(reader)
        for row in reader:
            if len(row) < 15:
                continue
            try:
                leg = CSVLeg(
                    entry_time=row[0].strip(),
                    symbol=row[1].strip().strip('"'),
                    exit_time=row[2].strip(),
                    direction=int(row[3]),
                    entry_price=float(row[4]),
                    exit_price=float(row[5]),
                    quantity=int(row[6]),
                    pnl=float(row[7]),
                    fees=float(row[8]),
                    mae=float(row[9]),
                    mfe=float(row[10]),
                    drawdown=float(row[11]),
                    is_win=int(row[12]),
                    duration=row[13].strip().strip('"'),
                    order_ids=row[14].strip().strip('"'),
                )
                legs.append(leg)
            except (ValueError, IndexError):
                continue
    return legs


def normalize_time(t: str) -> str:
    """Normalize time string for matching.

    Report format: '2024-01-02 15:00'
    CSV format:    '2024-01-02T15:00:00Z'
    """
    # Remove 'T', 'Z', trailing seconds for matching
    t = t.replace("T", " ").replace("Z", "").strip()
    # Remove :00 seconds if present (to match report's HH:MM)
    if len(t) == 19:  # 'YYYY-MM-DD HH:MM:SS'
        t = t[:16]  # -> 'YYYY-MM-DD HH:MM'
    return t


def match_csv_legs_to_spread(spread: VASSSpread, all_legs: List[CSVLeg]) -> List[CSVLeg]:
    """Find the CSV leg rows that correspond to a given spread."""
    entry_norm = normalize_time(spread.entry_time)
    exit_norm = normalize_time(spread.exit_time)

    matched = []
    for leg in all_legs:
        leg_entry = normalize_time(leg.entry_time)
        leg_exit = normalize_time(leg.exit_time)
        if leg_entry == entry_norm and leg_exit == exit_norm:
            matched.append(leg)

    # If exact match fails for 3-leg spreads or other edge cases, try matching
    # just entry time. The report groups by entry time.
    if len(matched) == 0:
        for leg in all_legs:
            leg_entry = normalize_time(leg.entry_time)
            leg_exit = normalize_time(leg.exit_time)
            if leg_entry == entry_norm:
                matched.append(leg)

    return matched


# ---------------------------------------------------------------------------
# Enrichment: cross-reference CSV data into spreads
# ---------------------------------------------------------------------------
def estimate_spread_mae(
    long_legs: List[CSVLeg], short_legs: List[CSVLeg], spread: VASSSpread
) -> float:
    """Estimate the spread-level MAE (worst concurrent P&L) from leg data.

    The naive approach of summing leg MAEs is WRONG because leg MAEs occur at
    different times. For a bull call spread, when the long call hits its MAE
    (underlying dropped), the short call also dropped (generating profit for
    the short position).

    Approach:
    - For LOSERS: use the actual final P&L as the spread MAE. Losers exit
      near their worst point (triggered by stops, transitions, etc.), so
      actual P&L is a tight proxy for the MAE.
    - For WINNERS: estimate spread MAE = long_leg_MAE + short_leg_MFE.
      When the long leg hits its worst point, the short leg is typically
      near its best point (both calls decline together). This gives a
      realistic estimate of the spread's worst intraday P&L.
      If that estimate is worse than the actual P&L, use it.
      If it's better (which shouldn't happen), fall back to actual P&L.

    Returns a negative value (dollars lost at worst point).
    """
    if spread.is_win == 0:
        # Losers: actual P&L is a tight proxy for the spread MAE.
        # The trade exited at or near the worst point.
        return spread.gross_pnl  # already negative

    # Winners: estimate the worst concurrent spread P&L.
    # When the long leg hits its MAE, the short leg has likely moved in
    # the same direction (both calls down when underlying drops).
    # For the short position, a declining call = profit (positive MFE).
    # So spread_mae ~= long_leg_mae + short_leg_mfe (at the same moment).
    #
    # This is still an approximation since MFE might not coincide with
    # the long leg's MAE, but it is much better than summing both MAEs.
    total_long_mae = sum(l.mae for l in long_legs)  # negative
    total_short_mfe = sum(l.mfe for l in short_legs)  # positive (profit on short)

    # The estimated spread worst P&L: long at worst + short at best
    # If underlying dropped: long call MAE is very negative, short call MFE is positive
    estimated_mae = total_long_mae + total_short_mfe

    # Sanity: MAE can never be worse than -(total debit) and can never be
    # better than the actual final P&L (it must have been at least this bad
    # at some point if we're computing the minimum).
    # Actually for a winner, MAE should be <= 0 (worst case was at most break-even)
    # or it could be slightly positive if the trade never went negative.
    # Cap at 0 for safety: if estimated_mae > 0, the trade never went negative
    if estimated_mae > 0:
        estimated_mae = 0.0

    return estimated_mae


def enrich_spreads(spreads: List[VASSSpread], all_legs: List[CSVLeg]) -> None:
    """Add quantity, total_debit, and MAE data from CSV legs to each spread."""
    for spread in spreads:
        matched = match_csv_legs_to_spread(spread, all_legs)

        if not matched:
            # Fallback: estimate from report data
            if spread.debit != "NOT_FOUND":
                debit_per_share = float(spread.debit)
            else:
                debit_per_share = spread.width * 0.5
            spread.quantity = 6  # default
            spread.total_debit = debit_per_share * spread.quantity * 100
            # For unmatched losers, use actual P&L as MAE; for winners, assume 0
            spread.mae_dollars = spread.gross_pnl if spread.is_win == 0 else 0
            spread.mae_pct = (
                abs(spread.mae_dollars / spread.total_debit) if spread.total_debit > 0 else 0
            )
            continue

        # Separate long and short legs
        long_legs = [l for l in matched if l.direction == 0]
        short_legs = [l for l in matched if l.direction == 1]

        if spread.strategy == "BULL_CALL_DEBIT":
            if long_legs:
                spread.quantity = long_legs[0].quantity
            elif short_legs:
                spread.quantity = short_legs[0].quantity

            if spread.debit != "NOT_FOUND":
                debit_per_share = float(spread.debit)
            elif long_legs and short_legs:
                debit_per_share = long_legs[0].entry_price - short_legs[0].entry_price
            else:
                debit_per_share = spread.width * 0.5

            spread.total_debit = debit_per_share * spread.quantity * 100
            spread.mae_dollars = estimate_spread_mae(long_legs, short_legs, spread)

        elif spread.strategy == "BEAR_CALL_CREDIT":
            if short_legs:
                spread.quantity = short_legs[0].quantity
            elif long_legs:
                spread.quantity = long_legs[0].quantity

            if long_legs and short_legs:
                credit_received = short_legs[0].entry_price - long_legs[0].entry_price
                max_risk_per_share = spread.width - credit_received
            else:
                max_risk_per_share = spread.width * 0.5

            spread.total_debit = max_risk_per_share * spread.quantity * 100
            # For credit spreads: the risk is on the short leg. When the short
            # call goes against us (underlying rises), the long call hedge also
            # rises. So spread MAE = short_leg_MAE + long_leg_MFE.
            # We reuse estimate_spread_mae with short_legs as "risk legs" and
            # long_legs as "hedge legs".
            if spread.is_win == 0:
                spread.mae_dollars = spread.gross_pnl
            else:
                total_short_mae = sum(l.mae for l in short_legs)
                total_long_mfe = sum(l.mfe for l in long_legs)
                estimated = total_short_mae + total_long_mfe
                spread.mae_dollars = min(estimated, 0.0)

        # Compute MAE as % of total risk
        if spread.total_debit > 0:
            spread.mae_pct = abs(spread.mae_dollars) / spread.total_debit
        else:
            spread.mae_pct = 0.0


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------
@dataclass
class FloorResult:
    """Result of simulating a conviction floor at a given level."""

    floor_pct: float
    actual_gross_pnl: float = 0.0
    simulated_gross_pnl: float = 0.0
    losers_capped: int = 0  # losers where floor helped
    losers_unchanged: int = 0  # losers already within floor
    winners_killed: int = 0  # winners stopped out by MAE > floor
    winners_unchanged: int = 0
    savings_from_capped: float = 0.0  # positive = money saved
    cost_of_killed: float = 0.0  # positive = money lost
    net_delta: float = 0.0
    total_trades: int = 0
    actual_wins: int = 0
    simulated_wins: int = 0
    # Per-trade detail
    details: List[dict] = field(default_factory=list)


def simulate_floor(spreads: List[VASSSpread], floor_pct: float) -> FloorResult:
    """Simulate conviction floor at the given percentage."""
    result = FloorResult(floor_pct=floor_pct)
    result.total_trades = len(spreads)

    for spread in spreads:
        actual_pnl = spread.gross_pnl
        result.actual_gross_pnl += actual_pnl

        if spread.is_win:
            result.actual_wins += 1

        total_risk = spread.total_debit
        floor_loss = -floor_pct * total_risk  # negative value (max allowed loss)

        detail = {
            "entry": spread.entry_time,
            "strategy": spread.strategy,
            "actual_pnl": actual_pnl,
            "total_risk": total_risk,
            "floor_loss": floor_loss,
            "mae_dollars": spread.mae_dollars,
            "mae_pct": spread.mae_pct,
            "action": "UNCHANGED",
            "simulated_pnl": actual_pnl,
        }

        if spread.is_win == 1:
            # Winner: check if MAE breached the floor
            # mae_dollars is the sum of leg MAEs (negative = loss)
            # If MAE worse than floor, the winner would have been stopped out
            if spread.mae_dollars < floor_loss and total_risk > 0:
                # Winner killed! Would have been stopped at -floor_pct
                simulated_pnl = floor_loss
                result.winners_killed += 1
                result.cost_of_killed += actual_pnl - simulated_pnl  # positive
                result.simulated_gross_pnl += simulated_pnl
                detail["action"] = "WINNER_KILLED"
                detail["simulated_pnl"] = simulated_pnl
            else:
                # Winner unaffected
                result.winners_unchanged += 1
                result.simulated_wins += 1
                result.simulated_gross_pnl += actual_pnl
        else:
            # Loser: check if actual loss exceeded the floor
            if actual_pnl < floor_loss and total_risk > 0:
                # Loss was worse than floor -> cap it
                simulated_pnl = floor_loss
                result.losers_capped += 1
                result.savings_from_capped += simulated_pnl - actual_pnl  # positive (saved money)
                result.simulated_gross_pnl += simulated_pnl
                detail["action"] = "LOSER_CAPPED"
                detail["simulated_pnl"] = simulated_pnl
            else:
                # Loss within floor or total_risk == 0
                result.losers_unchanged += 1
                result.simulated_gross_pnl += actual_pnl
                # Check if this losing trade is close to break-even with floor
                # and might become a "win" at floor level
                # Actually, MAE check for losers: if MAE never breached floor,
                # the loser exited before floor was hit (exits like transition, VIX spike)
                # These are unchanged.

        result.details.append(detail)

    # Re-count simulated wins: winners that survived + losers that weren't capped
    # (some losers within floor might have been barely negative)
    result.simulated_wins = result.winners_unchanged
    result.net_delta = result.simulated_gross_pnl - result.actual_gross_pnl

    return result


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------
def print_header(title: str) -> None:
    width = 72
    print("\n" + "=" * width)
    print(f"  {title}")
    print("=" * width)


def print_separator() -> None:
    print("-" * 72)


def format_dollar(amount: float) -> str:
    if amount >= 0:
        return f"${amount:,.2f}"
    else:
        return f"-${abs(amount):,.2f}"


def format_pct(pct: float) -> str:
    return f"{pct * 100:.1f}%"


def print_baseline(spreads: List[VASSSpread]) -> None:
    """Print baseline V12.8 actual performance."""
    print_header("V12.8 ACTUAL VASS PERFORMANCE (BASELINE)")

    bull_spreads = [s for s in spreads if s.strategy == "BULL_CALL_DEBIT"]
    bear_spreads = [s for s in spreads if s.strategy == "BEAR_CALL_CREDIT"]

    print(f"\n  BULL_CALL_DEBIT:")
    print(f"    Trades:     {len(bull_spreads)}")
    bull_wins = sum(1 for s in bull_spreads if s.is_win)
    print(
        f"    Win Rate:   {bull_wins}/{len(bull_spreads)} ({bull_wins/len(bull_spreads)*100:.1f}%)"
        if bull_spreads
        else "    Win Rate:   N/A"
    )
    bull_pnl = sum(s.gross_pnl for s in bull_spreads)
    print(f"    Gross P&L:  {format_dollar(bull_pnl)}")
    bull_fees = sum(s.fees for s in bull_spreads)
    print(f"    Fees:       {format_dollar(bull_fees)}")
    print(f"    Net P&L:    {format_dollar(bull_pnl - bull_fees)}")

    if bear_spreads:
        print(f"\n  BEAR_CALL_CREDIT:")
        print(f"    Trades:     {len(bear_spreads)}")
        bear_wins = sum(1 for s in bear_spreads if s.is_win)
        print(
            f"    Win Rate:   {bear_wins}/{len(bear_spreads)} ({bear_wins/len(bear_spreads)*100:.1f}%)"
            if bear_spreads
            else "    Win Rate:   N/A"
        )
        bear_pnl = sum(s.gross_pnl for s in bear_spreads)
        print(f"    Gross P&L:  {format_dollar(bear_pnl)}")

    total_pnl = sum(s.gross_pnl for s in spreads)
    total_fees = sum(s.fees for s in spreads)
    total_wins = sum(1 for s in spreads if s.is_win)
    print(f"\n  COMBINED VASS:")
    print(f"    Trades:     {len(spreads)}")
    print(f"    Win Rate:   {total_wins}/{len(spreads)} ({total_wins/len(spreads)*100:.1f}%)")
    print(f"    Gross P&L:  {format_dollar(total_pnl)}")
    print(f"    Fees:       {format_dollar(total_fees)}")
    print(f"    Net P&L:    {format_dollar(total_pnl - total_fees)}")

    # Top 5 worst losers
    losers = sorted([s for s in spreads if s.is_win == 0], key=lambda s: s.gross_pnl)
    print(f"\n  WORST 5 LOSERS:")
    for i, s in enumerate(losers[:5]):
        loss_pct = abs(s.gross_pnl / s.total_debit * 100) if s.total_debit > 0 else 0
        print(
            f"    {i+1}. {s.entry_time} | {s.strategy} | "
            f"P&L={format_dollar(s.gross_pnl)} | "
            f"Risk={format_dollar(s.total_debit)} | "
            f"Loss={loss_pct:.1f}% of risk | "
            f"Exit: {s.exit_reason[:40]}"
        )


def print_primary_analysis(result: FloorResult) -> None:
    """Print detailed analysis for the primary (35%) floor."""
    print_header(f"SIMULATION: {format_pct(result.floor_pct)} CONVICTION FLOOR")

    print(f"\n  SUMMARY:")
    print(f"    Actual Gross P&L:      {format_dollar(result.actual_gross_pnl)}")
    print(f"    Simulated Gross P&L:   {format_dollar(result.simulated_gross_pnl)}")
    print(f"    Net Delta:             {format_dollar(result.net_delta)}")
    if result.actual_gross_pnl != 0:
        improvement_pct = result.net_delta / abs(result.actual_gross_pnl) * 100
        print(f"    Improvement:           {improvement_pct:+.1f}% of actual loss")

    print(f"\n  TRADE IMPACT:")
    print(f"    Total trades:          {result.total_trades}")
    print(f"    Losers capped:         {result.losers_capped} (loss > floor, capped at floor)")
    print(f"    Losers unchanged:      {result.losers_unchanged} (loss already within floor)")
    print(f"    Winners unchanged:     {result.winners_unchanged} (MAE never hit floor)")
    print(
        f"    Winners killed:        {result.winners_killed} (MAE breached floor, would stop out)"
    )

    print(f"\n  DOLLAR IMPACT BREAKDOWN:")
    print(f"    Savings from capped losers:  {format_dollar(result.savings_from_capped)}")
    print(f"    Cost of killed winners:      {format_dollar(result.cost_of_killed)}")
    print(
        f"    NET BENEFIT:                 {format_dollar(result.savings_from_capped - result.cost_of_killed)}"
    )

    # Win rate change
    actual_wr = result.actual_wins / result.total_trades * 100 if result.total_trades else 0
    sim_wins = result.winners_unchanged  # Killed winners are now losses
    sim_total = result.total_trades
    sim_wr = sim_wins / sim_total * 100 if sim_total else 0
    print(f"\n  WIN RATE IMPACT:")
    print(
        f"    Actual win rate:       {result.actual_wins}/{result.total_trades} ({actual_wr:.1f}%)"
    )
    print(f"    Simulated win rate:    {sim_wins}/{sim_total} ({sim_wr:.1f}%)")

    # Detail: which losers were capped
    capped = [d for d in result.details if d["action"] == "LOSER_CAPPED"]
    if capped:
        print(f"\n  CAPPED LOSERS DETAIL:")
        print(
            f"    {'Entry':<20} {'Strategy':<20} {'Actual P&L':>12} {'Capped P&L':>12} {'Saved':>10} {'Risk':>10} {'Loss%':>8}"
        )
        print(f"    {'-'*20} {'-'*20} {'-'*12} {'-'*12} {'-'*10} {'-'*10} {'-'*8}")
        for d in sorted(capped, key=lambda x: x["actual_pnl"]):
            saved = d["simulated_pnl"] - d["actual_pnl"]
            loss_pct = abs(d["actual_pnl"] / d["total_risk"] * 100) if d["total_risk"] > 0 else 0
            print(
                f"    {d['entry']:<20} {d['strategy']:<20} "
                f"{format_dollar(d['actual_pnl']):>12} "
                f"{format_dollar(d['simulated_pnl']):>12} "
                f"{format_dollar(saved):>10} "
                f"{format_dollar(d['total_risk']):>10} "
                f"{loss_pct:>7.1f}%"
            )

    # Detail: which winners were killed
    killed = [d for d in result.details if d["action"] == "WINNER_KILLED"]
    if killed:
        print(f"\n  KILLED WINNERS DETAIL:")
        print(
            f"    {'Entry':<20} {'Strategy':<20} {'Actual P&L':>12} {'Floor P&L':>12} {'Cost':>10} {'MAE%':>8}"
        )
        print(f"    {'-'*20} {'-'*20} {'-'*12} {'-'*12} {'-'*10} {'-'*8}")
        for d in sorted(killed, key=lambda x: -(x["actual_pnl"] - x["simulated_pnl"])):
            cost = d["actual_pnl"] - d["simulated_pnl"]
            mae_pct = abs(d["mae_dollars"] / d["total_risk"] * 100) if d["total_risk"] > 0 else 0
            print(
                f"    {d['entry']:<20} {d['strategy']:<20} "
                f"{format_dollar(d['actual_pnl']):>12} "
                f"{format_dollar(d['simulated_pnl']):>12} "
                f"{format_dollar(cost):>10} "
                f"{mae_pct:>7.1f}%"
            )


def print_sensitivity_analysis(results: List[FloorResult]) -> None:
    """Print sensitivity analysis across multiple floor levels."""
    print_header("SENSITIVITY ANALYSIS: FLOOR LEVELS 25%-50%")

    print(
        f"\n  {'Floor':>6} | {'Sim P&L':>12} | {'Net Delta':>12} | {'Capped':>7} | {'Killed':>7} | {'Savings':>12} | {'Cost':>12} | {'Net $':>12}"
    )
    print(f"  {'-'*6}-+-{'-'*12}-+-{'-'*12}-+-{'-'*7}-+-{'-'*7}-+-{'-'*12}-+-{'-'*12}-+-{'-'*12}")

    for r in results:
        net_benefit = r.savings_from_capped - r.cost_of_killed
        print(
            f"  {format_pct(r.floor_pct):>6} | "
            f"{format_dollar(r.simulated_gross_pnl):>12} | "
            f"{format_dollar(r.net_delta):>12} | "
            f"{r.losers_capped:>7} | "
            f"{r.winners_killed:>7} | "
            f"{format_dollar(r.savings_from_capped):>12} | "
            f"{format_dollar(r.cost_of_killed):>12} | "
            f"{format_dollar(net_benefit):>12}"
        )

    # Highlight optimal
    best = max(results, key=lambda r: r.net_delta)
    print(
        f"\n  OPTIMAL FLOOR: {format_pct(best.floor_pct)} -> Net Delta = {format_dollar(best.net_delta)}"
    )
    print(f"    ({best.losers_capped} losers capped, {best.winners_killed} winners killed)")


def print_strategy_breakdown(spreads: List[VASSSpread], floor_pct: float) -> None:
    """Print per-strategy breakdown at the primary floor level."""
    print_header(f"STRATEGY BREAKDOWN AT {format_pct(floor_pct)} FLOOR")

    for strategy in ["BULL_CALL_DEBIT", "BEAR_CALL_CREDIT"]:
        subset = [s for s in spreads if s.strategy == strategy]
        if not subset:
            continue

        result = simulate_floor(subset, floor_pct)
        print(f"\n  {strategy}:")
        print(f"    Trades:                {result.total_trades}")
        print(f"    Actual Gross P&L:      {format_dollar(result.actual_gross_pnl)}")
        print(f"    Simulated Gross P&L:   {format_dollar(result.simulated_gross_pnl)}")
        print(f"    Net Delta:             {format_dollar(result.net_delta)}")
        print(f"    Losers capped:         {result.losers_capped}")
        print(f"    Winners killed:        {result.winners_killed}")
        print(f"    Savings:               {format_dollar(result.savings_from_capped)}")
        print(f"    Cost:                  {format_dollar(result.cost_of_killed)}")


def print_mae_distribution(spreads: List[VASSSpread]) -> None:
    """Print MAE distribution to understand where drawdowns cluster."""
    print_header("MAE DISTRIBUTION (% OF TOTAL RISK)")

    winners = [s for s in spreads if s.is_win == 1 and s.total_debit > 0]
    losers = [s for s in spreads if s.is_win == 0 and s.total_debit > 0]

    buckets = [0.10, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50, 0.60, 0.75, 1.00]

    print(f"\n  WINNERS (MAE = worst drawdown before recovery):")
    print(f"    {'MAE Bucket':>12} | {'Count':>6} | {'Cumulative':>11}")
    print(f"    {'-'*12}-+-{'-'*6}-+-{'-'*11}")
    cum = 0
    for bucket in buckets:
        count = sum(1 for s in winners if s.mae_pct <= bucket) - cum
        cum += count
        print(f"    {'<=' + format_pct(bucket):>12} | {count:>6} | {cum:>11}")
    over = len(winners) - cum
    if over > 0:
        print(f"    {'>100%':>12} | {over:>6} | {cum + over:>11}")

    print(f"\n  LOSERS (MAE ~ final loss magnitude):")
    print(f"    {'MAE Bucket':>12} | {'Count':>6} | {'Cumulative':>11}")
    print(f"    {'-'*12}-+-{'-'*6}-+-{'-'*11}")
    cum = 0
    for bucket in buckets:
        count = sum(1 for s in losers if s.mae_pct <= bucket) - cum
        cum += count
        print(f"    {'<=' + format_pct(bucket):>12} | {count:>6} | {cum:>11}")
    over = len(losers) - cum
    if over > 0:
        print(f"    {'>100%':>12} | {over:>6} | {cum + over:>11}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    print("\n" + "=" * 72)
    print("  V12.8 CONVICTION FLOOR SIMULATION")
    print("  Evaluating impact of always-on loss floor on VASS spreads")
    print("=" * 72)

    # Validate files exist
    for path, label in [(REPORT_PATH, "Trade Detail Report"), (CSV_PATH, "Trades CSV")]:
        if not os.path.exists(path):
            print(f"\n  ERROR: {label} not found at:\n    {path}")
            sys.exit(1)

    # Parse data
    print("\n  Loading data...")
    spreads = parse_report_table(REPORT_PATH)
    print(f"    Parsed {len(spreads)} VASS spread trades from report")

    all_legs = parse_csv_legs(CSV_PATH)
    print(f"    Parsed {len(all_legs)} leg rows from CSV")

    # Enrich with CSV data
    print("  Cross-referencing CSV legs for quantity and MAE data...")
    enrich_spreads(spreads, all_legs)

    # Validate enrichment
    enriched_count = sum(1 for s in spreads if s.total_debit > 0)
    print(f"    Enriched {enriched_count}/{len(spreads)} spreads with risk/MAE data")

    # Debug: show a few enrichment samples
    print("\n  ENRICHMENT SAMPLES (first 5):")
    for s in spreads[:5]:
        print(
            f"    {s.entry_time} | {s.strategy} | "
            f"Qty={s.quantity} | Debit/share={s.debit} | "
            f"Total Risk={format_dollar(s.total_debit)} | "
            f"MAE={format_dollar(s.mae_dollars)} ({format_pct(s.mae_pct)}) | "
            f"P&L={format_dollar(s.gross_pnl)} | Win={s.is_win}"
        )

    # 1. Baseline
    print_baseline(spreads)

    # 2. MAE distribution
    print_mae_distribution(spreads)

    # 3. Primary floor analysis (35%)
    primary_result = simulate_floor(spreads, PRIMARY_FLOOR)
    print_primary_analysis(primary_result)

    # 4. Strategy breakdown
    print_strategy_breakdown(spreads, PRIMARY_FLOOR)

    # 5. Sensitivity analysis
    results = []
    for level in FLOOR_LEVELS:
        results.append(simulate_floor(spreads, level))
    print_sensitivity_analysis(results)

    # 6. Final verdict
    print_header("VERDICT")
    best = max(results, key=lambda r: r.net_delta)
    primary = [r for r in results if r.floor_pct == PRIMARY_FLOOR][0]

    print(f"\n  Proposed 35% conviction floor:")
    print(f"    Would improve VASS gross P&L by {format_dollar(primary.net_delta)}")
    print(
        f"    Savings from {primary.losers_capped} capped losers:    {format_dollar(primary.savings_from_capped)}"
    )
    print(
        f"    Cost from {primary.winners_killed} killed winners:     {format_dollar(primary.cost_of_killed)}"
    )
    if primary.net_delta > 0:
        print(f"\n    RECOMMENDATION: IMPLEMENT. Floor recovers more from tail losses")
        print(f"    than it costs in prematurely stopped winners.")
    elif primary.net_delta == 0:
        print(f"\n    RECOMMENDATION: NEUTRAL. Floor has no net impact.")
    else:
        print(f"\n    RECOMMENDATION: REVIEW. Floor costs more in killed winners")
        print(f"    than it saves from capped losers. Consider a wider floor.")

    if best.floor_pct != PRIMARY_FLOOR:
        print(f"\n    ALTERNATIVE: {format_pct(best.floor_pct)} floor is optimal with")
        print(f"    net delta = {format_dollar(best.net_delta)}")

    print()


if __name__ == "__main__":
    main()
