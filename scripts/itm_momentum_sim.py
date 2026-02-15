"""
ITM_MOMENTUM Simulation for 2017 Full Year

Takes the 61 DEBIT_MOMENTUM trades from V9.8 and simulates what would have
happened if they were ITM_MOMENTUM trades instead.

Key differences:
- DEBIT_MOMENTUM: delta 0.45-0.65, avg $0.30 option, 40 contracts
- ITM_MOMENTUM:   delta 0.60-0.85, avg $2.00-2.50 option, 5-8 contracts
- DEBIT_MOMENTUM: stop 30%, target 45%, trail trigger 20%
- ITM_MOMENTUM:   stop 35%, target 35%, trail trigger 20%
"""

import csv
import sys
from datetime import datetime

# ============================================================
# CONFIG: Current DEBIT_MOMENTUM (V9.3 values)
# ============================================================
DM_DELTA_MID = 0.55  # midpoint of 0.45-0.65
DM_STOP_PCT = 0.30
DM_TARGET_PCT = 0.45
DM_CONTRACTS = 40
DM_FEE_PER_CONTRACT = 1.30  # round-trip

# ============================================================
# CONFIG: Simulated ITM_MOMENTUM
# ============================================================
ITM_DELTA_MID = 0.72  # midpoint of 0.60-0.85
ITM_STOP_PCT = 0.35
ITM_TARGET_PCT = 0.35
ITM_TRAIL_TRIGGER = 0.20
ITM_TRAIL_PCT = 0.50
ITM_FEE_PER_CONTRACT = 1.30  # same per-contract fee

# Budget per trade (same as DEBIT_MOMENTUM)
BUDGET_PER_TRADE = 1200  # 40 contracts × $0.30 × 100 = $1,200


def parse_orders(filepath):
    """Parse orders CSV and extract DEBIT_MOMENTUM entries + their exits."""
    trades = []
    entries = {}  # symbol -> entry info

    with open(filepath, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            tag = row.get("Tag", "").strip().strip('"')
            symbol = row.get("Symbol", "").strip().strip('"')
            qty = int(row.get("Quantity", "0"))
            price = float(row.get("Price", "0"))
            status = row.get("Status", "").strip()
            order_type = row.get("Type", "").strip()
            time_str = row.get("Time", "").strip()

            if status != "Filled":
                continue

            # DEBIT_MOMENTUM entry
            if "MICRO:DEBIT_MOMENTUM" in tag and qty > 0:
                entries[symbol] = {
                    "entry_time": time_str,
                    "entry_price": price,
                    "quantity": qty,
                    "symbol": symbol,
                }

            # Exit for a known entry
            elif symbol in entries and qty < 0:
                entry = entries.pop(symbol)
                exit_type = "UNKNOWN"
                if order_type == "Stop Market":
                    exit_type = "STOP"
                elif order_type == "Limit":
                    exit_type = "TARGET"
                elif order_type == "Market":
                    exit_type = "TIME_EXIT"

                entry_p = entry["entry_price"]
                exit_p = price
                pnl_pct = (exit_p - entry_p) / entry_p if entry_p > 0 else 0
                contracts = entry["quantity"]
                gross_pnl = (exit_p - entry_p) * contracts * 100
                fees = contracts * DM_FEE_PER_CONTRACT
                net_pnl = gross_pnl - fees

                trades.append(
                    {
                        "entry_time": entry["entry_time"],
                        "symbol": symbol,
                        "entry_price": entry_p,
                        "exit_price": exit_p,
                        "pnl_pct": pnl_pct,
                        "contracts": contracts,
                        "gross_pnl": gross_pnl,
                        "fees": fees,
                        "net_pnl": net_pnl,
                        "exit_type": exit_type,
                    }
                )

    return trades


def estimate_qqq_move(entry_price, exit_price, delta):
    """Estimate the underlying QQQ move from option price change."""
    option_move = exit_price - entry_price
    # option_move ≈ delta × qqq_move (first-order approximation)
    if delta > 0:
        return option_move / delta
    return 0


def simulate_itm_trade(dm_trade):
    """Simulate what this trade would look like as ITM_MOMENTUM."""

    entry_p = dm_trade["entry_price"]
    exit_p = dm_trade["exit_price"]
    dm_pnl_pct = dm_trade["pnl_pct"]
    exit_type = dm_trade["exit_type"]

    # Step 1: Estimate the underlying QQQ move from the ATM option price change
    qqq_move = estimate_qqq_move(entry_p, exit_p, DM_DELTA_MID)

    # Step 2: Estimate ITM option entry price
    # In low VIX (11-14), DTE 1-3, ITM delta 0.72:
    # ITM option is ~$1.50-$3.00 depending on how deep ITM
    # Use a conservative estimate based on typical QQQ options
    # QQQ at ~$120-$155 in 2017, ITM strike ~$2-3 below current
    # DTE 1: ~$1.50-2.00, DTE 2: ~$2.00-2.50, DTE 3: ~$2.50-3.00
    itm_entry_price = 2.20  # conservative estimate for delta 0.72 at DTE 1-3

    # Step 3: Calculate contracts (same budget)
    itm_contracts = max(1, int(BUDGET_PER_TRADE / (itm_entry_price * 100)))

    # Step 4: Calculate ITM option move
    itm_option_move = qqq_move * ITM_DELTA_MID

    # Step 5: Determine exit type for ITM
    itm_pnl_pct = itm_option_move / itm_entry_price if itm_entry_price > 0 else 0

    itm_exit_type = "TIME_EXIT"
    itm_exit_pnl_pct = itm_pnl_pct  # default: full move captured

    # Check if stop would trigger first
    if itm_pnl_pct <= -ITM_STOP_PCT:
        itm_exit_type = "STOP"
        itm_exit_pnl_pct = -ITM_STOP_PCT

    # Check if target would trigger first
    elif itm_pnl_pct >= ITM_TARGET_PCT:
        itm_exit_type = "TARGET"
        itm_exit_pnl_pct = ITM_TARGET_PCT

    # Check trailing stop
    elif itm_pnl_pct > 0:
        # If peak was above trail trigger but current is below trail level
        # This is a simplification - we don't know the intraday path
        # Assume peak ≈ max(itm_pnl_pct, 0) for first approximation
        peak_pnl_pct = max(itm_pnl_pct * 1.2, itm_pnl_pct)  # slight overshoot estimate
        if peak_pnl_pct >= ITM_TRAIL_TRIGGER:
            trail_level = peak_pnl_pct - (peak_pnl_pct * ITM_TRAIL_PCT)
            if itm_pnl_pct <= trail_level:
                itm_exit_type = "TRAIL"
                itm_exit_pnl_pct = trail_level

    # Step 6: Calculate P&L
    itm_exit_price = itm_entry_price * (1 + itm_exit_pnl_pct)
    itm_gross_pnl = (itm_exit_price - itm_entry_price) * itm_contracts * 100
    itm_fees = itm_contracts * ITM_FEE_PER_CONTRACT
    itm_net_pnl = itm_gross_pnl - itm_fees

    is_win = 1 if itm_net_pnl > 0 else 0

    return {
        "entry_time": dm_trade["entry_time"],
        "symbol": dm_trade["symbol"],
        "qqq_move_est": qqq_move,
        "dm_entry": entry_p,
        "dm_exit": exit_p,
        "dm_exit_type": exit_type,
        "dm_pnl_pct": dm_pnl_pct,
        "dm_contracts": dm_trade["contracts"],
        "dm_gross_pnl": dm_trade["gross_pnl"],
        "dm_fees": dm_trade["fees"],
        "dm_net_pnl": dm_trade["net_pnl"],
        "itm_entry": itm_entry_price,
        "itm_exit": itm_exit_price,
        "itm_exit_type": itm_exit_type,
        "itm_pnl_pct": itm_exit_pnl_pct,
        "itm_contracts": itm_contracts,
        "itm_gross_pnl": itm_gross_pnl,
        "itm_fees": itm_fees,
        "itm_net_pnl": itm_net_pnl,
        "itm_is_win": is_win,
    }


def main():
    orders_file = "docs/audits/logs/stage9.8/V9_8_2017fullyear_orders.csv"

    print("=" * 80)
    print("ITM_MOMENTUM vs DEBIT_MOMENTUM Simulation — 2017 Full Year")
    print("=" * 80)

    # Parse actual DEBIT_MOMENTUM trades
    dm_trades = parse_orders(orders_file)
    print(f"\nFound {len(dm_trades)} DEBIT_MOMENTUM trades from orders.csv\n")

    # Simulate ITM_MOMENTUM for each
    itm_results = []
    for trade in dm_trades:
        result = simulate_itm_trade(trade)
        itm_results.append(result)

    # ============================================================
    # DEBIT_MOMENTUM Summary
    # ============================================================
    dm_total_gross = sum(t["dm_gross_pnl"] for t in itm_results)
    dm_total_fees = sum(t["dm_fees"] for t in itm_results)
    dm_total_net = sum(t["dm_net_pnl"] for t in itm_results)
    dm_wins = sum(1 for t in itm_results if t["dm_net_pnl"] > 0)
    dm_losses = len(itm_results) - dm_wins
    dm_stops = sum(1 for t in itm_results if t["dm_exit_type"] == "STOP")
    dm_targets = sum(1 for t in itm_results if t["dm_exit_type"] == "TARGET")
    dm_time = sum(1 for t in itm_results if t["dm_exit_type"] == "TIME_EXIT")

    print("─" * 80)
    print("ACTUAL: DEBIT_MOMENTUM (delta 0.45-0.65, ~$0.30, 40 contracts)")
    print("─" * 80)
    print(f"  Trades:       {len(itm_results)}")
    print(f"  Win Rate:     {dm_wins}/{len(itm_results)} = {dm_wins/len(itm_results)*100:.1f}%")
    print(f"  Exits:        STOP={dm_stops}, TARGET={dm_targets}, TIME={dm_time}")
    print(f"  Gross P&L:    ${dm_total_gross:,.0f}")
    print(f"  Total Fees:   ${dm_total_fees:,.0f}")
    print(f"  Net P&L:      ${dm_total_net:,.0f}")
    print(f"  Avg fee/trade: ${dm_total_fees/len(itm_results):,.1f}")
    if dm_wins > 0:
        avg_win = sum(t["dm_net_pnl"] for t in itm_results if t["dm_net_pnl"] > 0) / dm_wins
        avg_loss = sum(t["dm_net_pnl"] for t in itm_results if t["dm_net_pnl"] <= 0) / max(
            dm_losses, 1
        )
        print(f"  Avg Win:      ${avg_win:,.0f}")
        print(f"  Avg Loss:     ${avg_loss:,.0f}")

    # ============================================================
    # ITM_MOMENTUM Summary
    # ============================================================
    itm_total_gross = sum(t["itm_gross_pnl"] for t in itm_results)
    itm_total_fees = sum(t["itm_fees"] for t in itm_results)
    itm_total_net = sum(t["itm_net_pnl"] for t in itm_results)
    itm_wins = sum(1 for t in itm_results if t["itm_is_win"])
    itm_losses = len(itm_results) - itm_wins
    itm_stops = sum(1 for t in itm_results if t["itm_exit_type"] == "STOP")
    itm_targets = sum(1 for t in itm_results if t["itm_exit_type"] == "TARGET")
    itm_time = sum(1 for t in itm_results if t["itm_exit_type"] == "TIME_EXIT")
    itm_trail = sum(1 for t in itm_results if t["itm_exit_type"] == "TRAIL")

    print()
    print("─" * 80)
    print("SIMULATED: ITM_MOMENTUM (delta 0.60-0.85, ~$2.20, 5 contracts)")
    print("─" * 80)
    print(f"  Trades:       {len(itm_results)}")
    print(f"  Win Rate:     {itm_wins}/{len(itm_results)} = {itm_wins/len(itm_results)*100:.1f}%")
    print(
        f"  Exits:        STOP={itm_stops}, TARGET={itm_targets}, TIME={itm_time}, TRAIL={itm_trail}"
    )
    print(f"  Gross P&L:    ${itm_total_gross:,.0f}")
    print(f"  Total Fees:   ${itm_total_fees:,.0f}")
    print(f"  Net P&L:      ${itm_total_net:,.0f}")
    print(f"  Avg fee/trade: ${itm_total_fees/len(itm_results):,.1f}")
    if itm_wins > 0:
        avg_win = sum(t["itm_net_pnl"] for t in itm_results if t["itm_net_pnl"] > 0) / itm_wins
        avg_loss = sum(t["itm_net_pnl"] for t in itm_results if t["itm_net_pnl"] <= 0) / max(
            itm_losses, 1
        )
        print(f"  Avg Win:      ${avg_win:,.0f}")
        print(f"  Avg Loss:     ${avg_loss:,.0f}")
    avg_contracts = sum(t["itm_contracts"] for t in itm_results) / len(itm_results)
    print(f"  Avg Contracts: {avg_contracts:.1f}")

    # ============================================================
    # Comparison
    # ============================================================
    print()
    print("=" * 80)
    print("COMPARISON: DEBIT_MOMENTUM vs ITM_MOMENTUM")
    print("=" * 80)
    print(f"{'Metric':<25} {'DEBIT_MOM':>12} {'ITM_MOM':>12} {'Delta':>12}")
    print("─" * 61)
    print(
        f"{'Net P&L':<25} {'${:,.0f}'.format(dm_total_net):>12} {'${:,.0f}'.format(itm_total_net):>12} {'${:,.0f}'.format(itm_total_net - dm_total_net):>12}"
    )
    print(
        f"{'Win Rate':<25} {'{:.1f}%'.format(dm_wins/len(itm_results)*100):>12} {'{:.1f}%'.format(itm_wins/len(itm_results)*100):>12} {'{:+.1f}pp'.format((itm_wins-dm_wins)/len(itm_results)*100):>12}"
    )
    print(
        f"{'Total Fees':<25} {'${:,.0f}'.format(dm_total_fees):>12} {'${:,.0f}'.format(itm_total_fees):>12} {'${:,.0f}'.format(itm_total_fees - dm_total_fees):>12}"
    )
    print(f"{'Stop Exits':<25} {dm_stops:>12} {itm_stops:>12} {itm_stops - dm_stops:>+12}")
    print(
        f"{'Target Exits':<25} {dm_targets:>12} {itm_targets:>12} {itm_targets - dm_targets:>+12}"
    )
    print(f"{'Time Exits':<25} {dm_time:>12} {itm_time:>12} {itm_time - dm_time:>+12}")
    print(f"{'Avg Contracts':<25} {40:>12} {avg_contracts:>12.1f} {avg_contracts - 40:>+12.1f}")

    # ============================================================
    # Trade-by-trade detail
    # ============================================================
    print()
    print("=" * 80)
    print("TRADE-BY-TRADE COMPARISON (first 20)")
    print("=" * 80)
    print(
        f"{'Date':<12} {'DM Entry':>9} {'DM Exit':>8} {'DM Type':>8} {'DM P&L':>8} │ {'ITM Type':>9} {'ITM P&L%':>9} {'ITM P&L':>8} {'QQQ$':>7}"
    )
    print("─" * 95)

    for i, r in enumerate(itm_results[:20]):
        date = r["entry_time"][:10]
        print(
            f"{date:<12} "
            f"${r['dm_entry']:.2f}    "
            f"${r['dm_exit']:.2f}   "
            f"{r['dm_exit_type']:<8} "
            f"${r['dm_net_pnl']:>7,.0f} │ "
            f"{r['itm_exit_type']:<9} "
            f"{r['itm_pnl_pct']:>+8.1%} "
            f"${r['itm_net_pnl']:>7,.0f} "
            f"${r['qqq_move_est']:>+6.2f}"
        )

    # ============================================================
    # Stop analysis: how many DM stops would survive as ITM?
    # ============================================================
    print()
    print("=" * 80)
    print("STOP SURVIVAL ANALYSIS")
    print("=" * 80)
    print("(DEBIT_MOMENTUM trades that hit stop — would they stop on ITM too?)")
    print()

    dm_stop_trades = [r for r in itm_results if r["dm_exit_type"] == "STOP"]
    itm_still_stops = sum(1 for r in dm_stop_trades if r["itm_exit_type"] == "STOP")
    itm_now_time = sum(1 for r in dm_stop_trades if r["itm_exit_type"] == "TIME_EXIT")
    itm_now_target = sum(1 for r in dm_stop_trades if r["itm_exit_type"] == "TARGET")
    itm_now_trail = sum(1 for r in dm_stop_trades if r["itm_exit_type"] == "TRAIL")

    print(f"  DM Stop trades:        {len(dm_stop_trades)}")
    print(
        f"  Still stop on ITM:     {itm_still_stops} ({itm_still_stops/len(dm_stop_trades)*100:.0f}%)"
    )
    print(f"  Become TIME_EXIT:      {itm_now_time} ({itm_now_time/len(dm_stop_trades)*100:.0f}%)")
    print(
        f"  Become TARGET:         {itm_now_target} ({itm_now_target/len(dm_stop_trades)*100:.0f}%)"
    )
    print(
        f"  Become TRAIL:          {itm_now_trail} ({itm_now_trail/len(dm_stop_trades)*100:.0f}%)"
    )

    dm_stop_total_loss = sum(r["dm_net_pnl"] for r in dm_stop_trades)
    itm_equiv_pnl = sum(r["itm_net_pnl"] for r in dm_stop_trades)
    print(f"\n  DM Stop total P&L:     ${dm_stop_total_loss:,.0f}")
    print(f"  ITM equiv total P&L:   ${itm_equiv_pnl:,.0f}")
    print(f"  Improvement:           ${itm_equiv_pnl - dm_stop_total_loss:,.0f}")

    # ============================================================
    # Monthly breakdown
    # ============================================================
    print()
    print("=" * 80)
    print("MONTHLY COMPARISON")
    print("=" * 80)
    print(
        f"{'Month':<10} {'DM Trades':>10} {'DM Net':>10} {'ITM Trades':>11} {'ITM Net':>10} {'Delta':>10}"
    )
    print("─" * 61)

    months = {}
    for r in itm_results:
        month = r["entry_time"][:7]
        if month not in months:
            months[month] = {"dm_count": 0, "dm_net": 0, "itm_count": 0, "itm_net": 0}
        months[month]["dm_count"] += 1
        months[month]["dm_net"] += r["dm_net_pnl"]
        months[month]["itm_count"] += 1
        months[month]["itm_net"] += r["itm_net_pnl"]

    for month in sorted(months.keys()):
        m = months[month]
        delta = m["itm_net"] - m["dm_net"]
        print(
            f"{month:<10} "
            f"{m['dm_count']:>10} "
            f"${m['dm_net']:>9,.0f} "
            f"{m['itm_count']:>11} "
            f"${m['itm_net']:>9,.0f} "
            f"${delta:>+9,.0f}"
        )

    total_delta = itm_total_net - dm_total_net
    print("─" * 61)
    print(
        f"{'TOTAL':<10} "
        f"{len(itm_results):>10} "
        f"${dm_total_net:>9,.0f} "
        f"{len(itm_results):>11} "
        f"${itm_total_net:>9,.0f} "
        f"${total_delta:>+9,.0f}"
    )

    # ============================================================
    # System impact projection
    # ============================================================
    print()
    print("=" * 80)
    print("SYSTEM IMPACT PROJECTION")
    print("=" * 80)
    vass_pnl = 21778
    dm_micro_pnl = dm_total_net
    fade_pnl = -1897  # DEBIT_FADE from V9.8

    current_system = vass_pnl + dm_micro_pnl + fade_pnl
    itm_system = vass_pnl + itm_total_net + fade_pnl

    print(f"  VASS P&L:              ${vass_pnl:>10,.0f}  (unchanged)")
    print(f"  DEBIT_FADE P&L:        ${fade_pnl:>10,.0f}  (unchanged)")
    print(f"  DEBIT_MOMENTUM P&L:    ${dm_total_net:>10,.0f}  (actual)")
    print(f"  ITM_MOMENTUM P&L:      ${itm_total_net:>10,.0f}  (simulated)")
    print(f"  ─────────────────────────────────────")
    print(f"  V9.8 System (actual):  ${current_system:>10,.0f}")
    print(f"  V9.9 System (ITM sim): ${itm_system:>10,.0f}")
    print(f"  Improvement:           ${itm_system - current_system:>+10,.0f}")
    print(f"  Return impact:         {(itm_system - current_system)/100000*100:>+.2f}%")


if __name__ == "__main__":
    main()
