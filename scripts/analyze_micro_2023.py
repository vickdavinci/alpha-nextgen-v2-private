#!/usr/bin/env python3
"""
Analyze MICRO (Protective Puts) performance in V12.18-FullYear2023.
"""
import csv
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

BASE = Path(
    "/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/docs/audits/logs/stage12.18"
)
ORDERS_FILE = BASE / "V12.18-FullYear2023_orders.csv"
TRADES_FILE = BASE / "V12.18-FullYear2023_trades.csv"
LOGS_FILE = BASE / "V12.18-FullYear2023_logs.txt"


def parse_duration(dur_str):
    """Parse HH:MM:SS or D.HH:MM:SS into minutes."""
    dur_str = dur_str.strip().strip('"')
    parts = dur_str.split(":")
    first = parts[0]
    if "." in first:
        dp = first.split(".")
        days, hours = int(dp[0]), int(dp[1])
    else:
        days, hours = 0, int(first)
    mins = int(parts[1])
    secs = int(parts[2])
    return days * 24 * 60 + hours * 60 + mins + secs / 60.0


# ---- Load orders ----
orders = {}
with open(ORDERS_FILE, newline="") as f:
    for row in csv.DictReader(f):
        oid = int(row["ID"])
        orders[oid] = {
            "time": row["Time"],
            "symbol": row["Symbol"].strip(),
            "price": float(row["Price"]),
            "quantity": float(row["Quantity"]),
            "type": row["Type"],
            "status": row["Status"],
            "direction": row["Direction"],
            "value": float(row["Value"]),
            "tag": row["Tag"],
        }

# ---- Load trades ----
trades = []
with open(TRADES_FILE, newline="") as f:
    for row in csv.DictReader(f):
        oids_raw = row["Order IDs"].strip().strip('"')
        oids = [int(x) for x in oids_raw.split(";") if x.strip()]
        entry_oid = oids[0]
        exit_oid = oids[-1] if len(oids) > 1 else None

        entry_tag = orders.get(entry_oid, {}).get("tag", "")
        exit_tag = orders.get(exit_oid, {}).get("tag", "") if exit_oid else ""
        exit_type = orders.get(exit_oid, {}).get("type", "") if exit_oid else ""
        exit_stat = orders.get(exit_oid, {}).get("status", "") if exit_oid else ""

        if "MICRO:PROTECTIVE_PUTS" in entry_tag:
            ttype = "MICRO"
        elif "ITM:ITM_MOMENTUM" in entry_tag:
            ttype = "ITM"
        else:
            ttype = "OTHER"

        entry_price = float(row["Entry Price"])
        exit_price = float(row["Exit Price"])
        qty = float(row["Quantity"])
        pnl = float(row["P&L"])
        fees = float(row["Fees"])
        mae = float(row["MAE"])
        mfe = float(row["MFE"])
        is_win = int(row["IsWin"])

        et = datetime.strptime(row["Entry Time"], "%Y-%m-%dT%H:%M:%SZ")
        xt = datetime.strptime(row["Exit Time"], "%Y-%m-%dT%H:%M:%SZ")
        dur_min = parse_duration(row["Duration"])

        sym = row["Symbols"].strip().strip('"')
        if "C0" in sym:
            odir = "CALL"
        elif "P0" in sym:
            odir = "PUT"
        else:
            odir = "UNK"

        # exit trigger classification
        if (
            "MICRO_EOD_SWEEP" in exit_tag
            or "UNCLASSIFIED" in exit_tag
            or "FORCE" in exit_tag.upper()
        ):
            etrig = "FORCE_CLOSE/EOD_SWEEP"
        elif "RETRY_CANCELED" in exit_tag:
            etrig = "RETRY_CANCELED"
        elif exit_type == "Stop Market" and exit_stat == "Filled":
            etrig = "OCO_STOP"
        elif exit_type == "Limit" and exit_stat == "Filled" and exit_oid != entry_oid:
            etrig = "OCO_PROFIT"
        elif exit_type == "Market" and exit_stat == "Filled":
            if "EOD_SWEEP" in exit_tag or "FORCE" in exit_tag.upper() or "UNCLASSIFIED" in exit_tag:
                etrig = "FORCE_CLOSE/EOD_SWEEP"
            elif "RETRY_CANCELED" in exit_tag:
                etrig = "RETRY_CANCELED"
            else:
                etrig = "MARKET_EXIT"
        else:
            etrig = "OTHER"

        cost_basis = entry_price * qty * 100
        pnl_pct = (pnl / cost_basis * 100) if cost_basis > 0 else 0

        trades.append(
            dict(
                type=ttype,
                entry_time=et,
                exit_time=xt,
                entry_price=entry_price,
                exit_price=exit_price,
                quantity=qty,
                pnl=pnl,
                pnl_net=pnl - fees,
                fees=fees,
                mae=mae,
                mfe=mfe,
                is_win=is_win,
                duration_min=dur_min,
                option_dir=odir,
                exit_trigger=etrig,
                entry_tag=entry_tag,
                exit_tag=exit_tag,
                symbol=sym,
                pnl_pct=pnl_pct,
            )
        )

micro = [t for t in trades if t["type"] == "MICRO"]
itm = [t for t in trades if t["type"] == "ITM"]
other = [t for t in trades if t["type"] == "OTHER"]


# ==================================================================
def hdr(title):
    print("\n" + "=" * 90)
    print(f"  {title}")
    print("=" * 90)


# ==================================================================
hdr("V12.18-FullYear2023: MICRO (Protective Puts) ROOT CAUSE ANALYSIS")

# ---- SECTION 0 ----
hdr("SECTION 0: TRADE TYPE OVERVIEW")
fmt = f"{'Type':<15} {'Count':>6} {'Wins':>6} {'Losses':>6} {'WR':>8} {'Gross PnL':>12} {'Fees':>10} {'Net PnL':>12}"
print(f"\n{fmt}")
print("-" * 85)
for lab, grp in [("MICRO", micro), ("ITM", itm), ("OTHER", other), ("ALL", trades)]:
    c = len(grp)
    w = sum(1 for t in grp if t["is_win"])
    l = c - w
    wr = w / c * 100 if c else 0
    g = sum(t["pnl"] for t in grp)
    fe = sum(t["fees"] for t in grp)
    n = g - fe
    print(f"{lab:<15} {c:>6} {w:>6} {l:>6} {wr:>7.1f}% ${g:>10,.0f} ${fe:>8,.0f} ${n:>10,.0f}")

# ---- SECTION 1: Monthly ----
hdr("SECTION 1: MICRO MONTHLY BREAKDOWN")
mo = defaultdict(lambda: dict(t=0, w=0, l=0, pnl=0, fees=0))
for t in micro:
    k = t["entry_time"].strftime("%Y-%m")
    mo[k]["t"] += 1
    mo[k]["pnl"] += t["pnl"]
    mo[k]["fees"] += t["fees"]
    if t["is_win"]:
        mo[k]["w"] += 1
    else:
        mo[k]["l"] += 1

print(
    f"\n{'Month':<10} {'Trades':>7} {'Wins':>6} {'Losses':>6} {'WR':>8} {'Gross':>10} {'Fees':>8} {'Net':>10} {'CumNet':>10}"
)
print("-" * 90)
cum = 0
for k in sorted(mo):
    d = mo[k]
    wr = d["w"] / d["t"] * 100 if d["t"] else 0
    net = d["pnl"] - d["fees"]
    cum += net
    print(
        f"{k:<10} {d['t']:>7} {d['w']:>6} {d['l']:>6} {wr:>7.1f}% ${d['pnl']:>8,.0f} ${d['fees']:>6,.0f} ${net:>8,.0f} ${cum:>8,.0f}"
    )
print("-" * 90)
tg = sum(t["pnl"] for t in micro)
tf = sum(t["fees"] for t in micro)
tw = sum(1 for t in micro if t["is_win"])
tn = len(micro)
print(
    f"{'TOTAL':<10} {tn:>7} {tw:>6} {tn-tw:>6} {tw/tn*100 if tn else 0:>7.1f}% ${tg:>8,.0f} ${tf:>6,.0f} ${tg-tf:>8,.0f}"
)

# ---- SECTION 2: Exit triggers ----
hdr("SECTION 2: MICRO EXIT TRIGGER DISTRIBUTION")
es = defaultdict(lambda: dict(c=0, w=0, pnl=0, fees=0))
for t in micro:
    k = t["exit_trigger"]
    es[k]["c"] += 1
    es[k]["pnl"] += t["pnl"]
    es[k]["fees"] += t["fees"]
    if t["is_win"]:
        es[k]["w"] += 1

print(
    f"\n{'Exit Trigger':<25} {'Count':>6} {'Wins':>6} {'WR':>8} {'Gross PnL':>12} {'Avg PnL':>10} {'Fees':>8}"
)
print("-" * 85)
for k in sorted(es, key=lambda x: es[x]["pnl"]):
    d = es[k]
    wr = d["w"] / d["c"] * 100 if d["c"] else 0
    avg = d["pnl"] / d["c"] if d["c"] else 0
    print(
        f"{k:<25} {d['c']:>6} {d['w']:>6} {wr:>7.1f}% ${d['pnl']:>10,.0f} ${avg:>8,.0f} ${d['fees']:>6,.0f}"
    )

# ---- SECTION 3: PnL% distribution ----
hdr("SECTION 3: MICRO PnL PERCENTAGE DISTRIBUTION")
pcts = [t["pnl_pct"] for t in micro]
buckets = [
    ("-60 to -50%", -60, -50),
    ("-50 to -40%", -50, -40),
    ("-40 to -30%", -40, -30),
    ("-30 to -20%", -30, -20),
    ("-20 to -10%", -20, -10),
    ("-10 to   0%", -10, 0),
    ("  0 to +10%", 0, 10),
    ("+10 to +20%", 10, 20),
    ("+20 to +30%", 20, 30),
    ("+30 to +40%", 30, 40),
    ("+40 to +50%", 40, 50),
    ("+50 to +60%", 50, 60),
    ("+60%+", 60, 999),
]
print(f"\n{'Bucket':<18} {'Count':>6} {'Pct':>7}  Bar")
print("-" * 60)
for lab, lo, hi in buckets:
    c = sum(1 for p in pcts if lo <= p < hi)
    pct = c / len(pcts) * 100 if pcts else 0
    print(f"{lab:<18} {c:>6} {pct:>6.1f}%  {'#'*int(pct/2)}")
if pcts:
    sp = sorted(pcts)
    print(
        f"\nMean:   {sum(pcts)/len(pcts):>+.1f}%   Median: {sp[len(sp)//2]:>+.1f}%   Min: {min(pcts):>+.1f}%   Max: {max(pcts):>+.1f}%"
    )

# ---- SECTION 4: Hold duration ----
hdr("SECTION 4: MICRO HOLD DURATION DISTRIBUTION")
db = [
    ("0-5m", 0, 5),
    ("5-15m", 5, 15),
    ("15-30m", 15, 30),
    ("30-60m", 30, 60),
    ("1-2h", 60, 120),
    ("2-4h", 120, 240),
    ("4-8h", 240, 480),
    ("8-24h", 480, 1440),
    ("24h+", 1440, 99999),
]
print(
    f"\n{'Duration':<12} {'Count':>6} {'Wins':>6} {'Losses':>6} {'WR':>8} {'Avg PnL':>10} {'Tot PnL':>10}"
)
print("-" * 65)
for lab, lo, hi in db:
    bt = [t for t in micro if lo <= t["duration_min"] < hi]
    c = len(bt)
    if c == 0:
        continue
    w = sum(1 for t in bt if t["is_win"])
    print(
        f"{lab:<12} {c:>6} {w:>6} {c-w:>6} {w/c*100:>7.1f}% ${sum(t['pnl'] for t in bt)/c:>8,.0f} ${sum(t['pnl'] for t in bt):>8,.0f}"
    )
durs = [t["duration_min"] for t in micro]
if durs:
    sd = sorted(durs)
    print(
        f"\nMean: {sum(durs)/len(durs):.0f}m ({sum(durs)/len(durs)/60:.1f}h)  Median: {sd[len(sd)//2]:.0f}m ({sd[len(sd)//2]/60:.1f}h)"
    )

# ---- SECTION 5: Direction ----
hdr("SECTION 5: MICRO WIN RATE BY DIRECTION (CALL vs PUT)")
ds = defaultdict(lambda: dict(c=0, w=0, pnl=0, fees=0))
for t in micro:
    k = t["option_dir"]
    ds[k]["c"] += 1
    ds[k]["pnl"] += t["pnl"]
    ds[k]["fees"] += t["fees"]
    if t["is_win"]:
        ds[k]["w"] += 1
print(
    f"\n{'Dir':<8} {'Count':>6} {'Wins':>6} {'Losses':>6} {'WR':>8} {'Gross':>10} {'Avg':>10} {'Net':>10}"
)
print("-" * 75)
for d in sorted(ds):
    s = ds[d]
    wr = s["w"] / s["c"] * 100 if s["c"] else 0
    print(
        f"{d:<8} {s['c']:>6} {s['w']:>6} {s['c']-s['w']:>6} {wr:>7.1f}% ${s['pnl']:>8,.0f} ${s['pnl']/s['c']:>8,.0f} ${s['pnl']-s['fees']:>8,.0f}"
    )

# ---- SECTION 6: Sizing ----
hdr("SECTION 6: MICRO TRADE SIZING ANALYSIS")
sizes = [t["quantity"] * t["entry_price"] * 100 for t in micro]
qtys = [t["quantity"] for t in micro]
eps = [t["entry_price"] for t in micro]
if sizes:
    print(
        f"\nNotional:  avg=${sum(sizes)/len(sizes):,.0f}  min=${min(sizes):,.0f}  max=${max(sizes):,.0f}"
    )
    print(f"Contracts: avg={sum(qtys)/len(qtys):.1f}  min={min(qtys):.0f}  max={max(qtys):.0f}")
    print(f"Premium:   avg=${sum(eps)/len(eps):.2f}  min=${min(eps):.2f}  max=${max(eps):.2f}")

mfes = [t["mfe"] for t in micro]
maes = [t["mae"] for t in micro]
print(f"\nAvg MFE (max favorable excursion): ${sum(mfes)/len(mfes):,.0f}")
print(f"Avg MAE (max adverse excursion):   ${sum(maes)/len(maes):,.0f}")

winners = [t for t in micro if t["is_win"]]
losers = [t for t in micro if not t["is_win"]]
if winners and losers:
    aw = sum(t["pnl"] for t in winners) / len(winners)
    al = sum(t["pnl"] for t in losers) / len(losers)
    print(f"\nAvg winner: ${aw:,.0f}   Avg loser: ${al:,.0f}   W/L ratio: {abs(aw/al):.2f}x")
    print(f"Expectancy: ${sum(t['pnl'] for t in micro)/len(micro):,.0f}/trade")
    be = abs(al) / (abs(aw) + abs(al)) * 100
    print(f"Breakeven WR: {be:.0f}%  (actual: {tw/tn*100:.0f}%)")

# ---- SECTION 7: Trade detail ----
hdr("SECTION 7: ALL MICRO TRADES (chronological)")
print(
    f"\n{'#':>3} {'Date':<11} {'D':<4} {'Symbol':<28} {'Q':>3} {'Ent$':>6} {'Ext$':>6} {'PnL%':>7} {'PnL$':>8} {'Hold':>7} {'Exit':>22} {'W':>2}"
)
print("-" * 125)
for i, t in enumerate(sorted(micro, key=lambda x: x["entry_time"]), 1):
    dm = t["duration_min"]
    dstr = f"{int(dm)}m" if dm < 60 else f"{dm/60:.1f}h"
    print(
        f"{i:>3} {t['entry_time'].strftime('%Y-%m-%d'):<11} {t['option_dir']:<4} {t['symbol']:<28} {t['quantity']:>3.0f} ${t['entry_price']:>4.2f} ${t['exit_price']:>4.2f} {t['pnl_pct']:>+6.1f}% ${t['pnl']:>7,.0f} {dstr:>7} {t['exit_trigger']:>22} {'W' if t['is_win'] else 'L':>2}"
    )

# ---- SECTION 8: Log signal flow ----
hdr("SECTION 8: LOG ANALYSIS - MICRO SIGNAL FLOW")
sig_cand = sig_appr = blk_filter = blk_cap = 0
vix_entries = []
regime_entries = []
regime_labels = []

with open(LOGS_FILE) as f:
    for line in f:
        if "INTRADAY_SIGNAL_CANDIDATE: SignalId=MICRO" in line:
            sig_cand += 1
        if "INTRADAY_SIGNAL_APPROVED: SignalId=MICRO" in line:
            sig_appr += 1
        if "INTRADAY: Blocked" in line and "MICRO_BLOCK" in line:
            blk_filter += 1
        if "INTRADAY: Blocked - R_MICRO_CONCURRENT_CAP" in line:
            blk_cap += 1
        if "INTRADAY_SIGNAL:" in line and "PROTECTIVE_PUTS" in line:
            m = re.search(r"VIX=([\d.]+)", line)
            if m:
                vix_entries.append(float(m.group(1)))
            m = re.search(r"Score=(\d+)", line)
            if m:
                regime_entries.append(int(m.group(1)))
            m = re.search(r"Regime=(\w+)", line)
            if m:
                regime_labels.append(m.group(1))

tot_scan = sig_appr + blk_filter + blk_cap
print(f"\nSignal Candidates: {sig_cand}")
print(f"Signal Approved:   {sig_appr}")
print(f"Blocked (filter):  {blk_filter}")
print(f"Blocked (cap):     {blk_cap}")
print(f"Total Blocked:     {blk_filter + blk_cap}")
print(f"Execution Rate:    {sig_appr/tot_scan*100:.1f}%" if tot_scan else "N/A")

if vix_entries:
    print(f"\nVIX at MICRO entries (n={len(vix_entries)}):")
    print(
        f"  Mean={sum(vix_entries)/len(vix_entries):.1f}  Min={min(vix_entries):.1f}  Max={max(vix_entries):.1f}"
    )
    vl = sum(1 for v in vix_entries if v < 16)
    vm = sum(1 for v in vix_entries if 16 <= v < 25)
    vh = sum(1 for v in vix_entries if v >= 25)
    print(
        f"  <16: {vl} ({vl/len(vix_entries)*100:.0f}%)  16-25: {vm} ({vm/len(vix_entries)*100:.0f}%)  >=25: {vh} ({vh/len(vix_entries)*100:.0f}%)"
    )

if regime_entries:
    print(f"\nRegime Score at entry (n={len(regime_entries)}):")
    print(
        f"  Mean={sum(regime_entries)/len(regime_entries):.0f}  Min={min(regime_entries)}  Max={max(regime_entries)}"
    )

if regime_labels:
    rc = Counter(regime_labels)
    print(f"\nRegime Label at entry:")
    for lab, cnt in rc.most_common():
        print(f"  {lab}: {cnt} ({cnt/len(regime_labels)*100:.0f}%)")

# ---- SECTION 9: DROP_RCA ----
hdr("SECTION 9: AGGREGATED BLOCK REASONS (from DROP_RCA_DAILY)")
db2 = Counter()
dd = Counter()
with open(LOGS_FILE) as f:
    for line in f:
        if "DROP_RCA_DAILY:" not in line:
            continue
        bm = re.search(r"INTRADAY_BLOCKED\[([^\]]+)\]", line)
        if bm:
            for part in bm.group(1).split(";"):
                cm = re.search(r":(\d+)@", part)
                if not cm:
                    continue
                c = int(cm.group(1))
                if "ITM_ENGINE" in part:
                    continue
                if "CONCURRENT_CAP" in part:
                    db2["CONCURRENT_CAP"] += c
                elif "ITM_HANDOFF_ONLY" in part:
                    db2["ITM_HANDOFF_ONLY"] += c
                elif "LOW_CONVICTION" in part:
                    db2["LOW_CONVICTION"] += c
                elif "OTM_GATE_BLOCK" in part:
                    db2["OTM_GATE_BLOCK"] += c
                elif "MIN_MOVE" in part:
                    db2["MIN_MOVE_TOO_SMALL"] += c
                elif "QQQ_FLAT" in part or "QQQ FLAT" in part:
                    db2["QQQ_FLAT"] += c
                elif "DEBIT_FADE_DISABLED" in part:
                    db2["DEBIT_FADE_DISABLED"] += c
                elif "RUNAWAY" in part:
                    db2["FADE_RUNAWAY"] += c
                else:
                    db2["OTHER"] += c
        dm = re.search(r"INTRADAY_DROPPED\[([^\]]+)\]", line)
        if dm:
            for part in dm.group(1).split(";"):
                cm = re.search(r":(\d+)@", part)
                if not cm:
                    continue
                c = int(cm.group(1))
                if "CONCURRENT_CAP" in part and "PROTECTIVE" in part:
                    dd["CAP|PROTECTIVE_PUTS"] += c
                elif "COOLDOWN" in part:
                    dd["STRATEGY_COOLDOWN"] += c
                elif "ITM" in part:
                    dd["ITM_RELATED"] += c

print(f"\n{'Block Reason':<35} {'Count':>6} {'Pct':>7}")
print("-" * 55)
tb = sum(db2.values())
for r, c in db2.most_common():
    print(f"{r:<35} {c:>6} {c/tb*100:>6.1f}%")
print(f"{'TOTAL':<35} {tb:>6}")

if dd:
    print(f"\n{'Dropped Reason':<35} {'Count':>6}")
    print("-" * 45)
    for r, c in dd.most_common():
        print(f"{r:<35} {c:>6}")

# ---- SECTION 10: ROOT CAUSE ----
hdr("SECTION 10: ROOT CAUSE DIAGNOSIS")

if micro:
    tp = sum(t["pnl"] for t in micro)
    tfe = sum(t["fees"] for t in micro)
    n = len(micro)
    nw = sum(1 for t in micro if t["is_win"])
    wr = nw / n * 100

    wp = [t["pnl"] for t in micro if t["is_win"]]
    lp = [t["pnl"] for t in micro if not t["is_win"]]
    aw = sum(wp) / len(wp) if wp else 0
    al = sum(lp) / len(lp) if lp else 0

    puts = [t for t in micro if t["option_dir"] == "PUT"]
    calls = [t for t in micro if t["option_dir"] == "CALL"]
    pp = sum(t["pnl"] for t in puts)
    pwr = sum(1 for t in puts if t["is_win"]) / len(puts) * 100 if puts else 0
    cp = sum(t["pnl"] for t in calls)
    cwr = sum(1 for t in calls if t["is_win"]) / len(calls) * 100 if calls else 0

    fc = [t for t in micro if "FORCE" in t["exit_trigger"] or "EOD" in t["exit_trigger"]]
    st = [t for t in micro if t["exit_trigger"] == "OCO_STOP"]
    pt = [t for t in micro if t["exit_trigger"] == "OCO_PROFIT"]
    rc2 = [t for t in micro if t["exit_trigger"] == "RETRY_CANCELED"]

    be = abs(al) / (abs(aw) + abs(al)) * 100 if aw and al else 100

    print(
        f"""
FINDING 1: DIRECTION MISMATCH -- PUT BIAS IN A STRONG BULL YEAR
  2023: QQQ rallied +55%. Protective Puts buys OTM puts (bearish bet).
  - PUTs:  {len(puts)} trades, WR={pwr:.0f}%, PnL=${pp:,.0f}
  - CALLs: {len(calls)} trades, WR={cwr:.0f}%, PnL=${cp:,.0f}
  PUTs are {len(puts)/n*100:.0f}% of all MICRO trades. In a relentless uptrend,
  bought puts bleed from theta and adverse delta. This is the #1 structural problem.

FINDING 2: POOR WIN/LOSS RATIO -- LOSSES ARE LARGER THAN WINS
  Win rate:      {wr:.1f}% ({nw}W / {n-nw}L)
  Avg winner:    ${aw:,.0f}
  Avg loser:     ${al:,.0f}
  W/L ratio:     {abs(aw/al):.2f}x
  Breakeven WR:  {be:.0f}% (actual: {wr:.0f}%)
  {"** BELOW BREAKEVEN -- system bleeds money at this WR **" if wr < be else "System is above breakeven."}

FINDING 3: EXIT MECHANISM BREAKDOWN -- STOPS DOMINATE, FEW PROFIT TAKES
  OCO_STOP:              {len(st):>3} trades  PnL=${sum(t['pnl'] for t in st):>+8,.0f}
  OCO_PROFIT:            {len(pt):>3} trades  PnL=${sum(t['pnl'] for t in pt):>+8,.0f}
  FORCE_CLOSE/EOD_SWEEP: {len(fc):>3} trades  PnL=${sum(t['pnl'] for t in fc):>+8,.0f}
  RETRY_CANCELED:        {len(rc2):>3} trades  PnL=${sum(t['pnl'] for t in rc2):>+8,.0f}
  Stop losses dominate ({len(st)/n*100:.0f}% of exits). In a rising market, put
  value drops quickly and the 30% OCO stop triggers before any downmove appears.

FINDING 4: FORCED EOD CLOSURES DESTROY POTENTIAL RECOVERY
  {len(fc)} trades ({len(fc)/n*100:.0f}%) forced closed at EOD (15:15-15:25).
  Avg PnL of forced closes: ${sum(t['pnl'] for t in fc)/len(fc) if fc else 0:,.0f}
  These might have recovered if held overnight, but the intraday mandate kills them.

FINDING 5: RETRY_CANCELED -- OCO ORDER MANAGEMENT ISSUES
  {len(rc2)} trades ({len(rc2)/n*100:.0f}%) closed via RETRY_CANCELED mechanism.
  PnL: ${sum(t['pnl'] for t in rc2):,.0f}
  This suggests OCO limit/stop orders were canceled and the position was then
  closed at market -- often at worse prices than the original OCO would have gotten.

FINDING 6: FEE DRAG
  Gross PnL: ${tp:,.0f}   Fees: ${tfe:,.0f} ({tfe/abs(tp)*100:.0f}% of gross magnitude)
  With ${tfe/n:,.0f}/trade in fees, even small wins get eaten.

FINDING 7: SIGNAL QUALITY -- MOST BLOCKS ARE CORRECT, BUT SURVIVORS STILL LOSE
  {blk_filter+blk_cap} blocked vs {sig_appr} approved ({sig_appr/tot_scan*100:.0f}% exec rate).
  Top block: ITM_HANDOFF_ONLY (correctly avoids improving/recovering regimes).
  But the {sig_appr} trades that PASS all filters still average ${tp/n:,.0f}/trade.
  The entry thesis itself is wrong in 2023's bull environment."""
    )

    print(
        f"""
{'='*90}
  OVERALL DIAGNOSIS
{'='*90}

MICRO (Protective Puts) lost ${tp-tfe:,.0f} net in 2023 due to a FUNDAMENTAL
STRUCTURAL MISMATCH between strategy and market regime:

1. STRATEGY vs ENVIRONMENT: Buying OTM puts in QQQ's best year since 2020.
   This is like shorting VIX when markets are calm -- it works in theory
   (insurance for drops) but costs dearly in steady bull markets.

2. THETA DEATH: 0-5 DTE puts lose ~5-10%/day from time decay alone.
   Avg hold = {sum(durs)/len(durs):.0f}m. Most positions die from theta before
   any protective move materializes.

3. ASYMMETRIC EXITS: {len(st)} stop losses vs {len(pt)} profit targets.
   In an uptrend, puts lose value steadily (delta + theta both adverse).
   Stops hit quickly; profit targets require sharp, sustained drops that
   almost never happened in 2023.

4. FORCED CLOSES + ORDER ISSUES: {len(fc)+len(rc2)} trades ({(len(fc)+len(rc2))/n*100:.0f}%) exited
   via forced mechanisms, not natural OCO resolution.

RECOMMENDATIONS:
  (a) DISABLE Protective Puts when macro regime > 50 (bull environment)
  (b) REPLACE with long calls in IMPROVING/RECOVERING regimes
  (c) REQUIRE VIX > 20 AND regime WORSENING AND QQQ intraday < -0.5% to enter
  (d) WIDEN the stop to 45% (30% is too tight when theta alone moves 5-10%/day)
  (e) REDUCE size by 50% if keeping the strategy -- limit drag on portfolio
  (f) Consider converting Protective Puts into SPREAD-based protective structure
      (e.g., put debit spreads) to reduce theta exposure
"""
    )
