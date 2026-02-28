# ObjectStore Artifacts - stage12.21 (V12.21 Full Year 2024)

**Status:** ARTIFACTS NOT PULLED

This file documents the ObjectStore observability data that is missing from
this stage folder and explains how to pull it.

---

## Why This Data Matters

The two reports in this folder were generated WITHOUT ObjectStore data:

- `V12.21-FullYear2024_PERFORMANCE_REPORT.md`
- `V12.21-FullYear2024_SIGNAL_FLOW_REPORT.md`

As documented in the Data Validation checklist of those reports, all five
observability artifacts are `[MISSING]`. This means:

| What is missing | Impact on reports |
|-----------------|------------------|
| `signal_lifecycle` | Signal funnel counts (VASS/MICRO candidates, drops, approvals) are estimated from log sampling only. H2 2024 counts are unreliable due to log budget exhaustion (8,782 events suppressed). |
| `regime_decisions` | Cannot confirm which gate blocked which engine on each bar. Gate-name attribution is derived from log pattern matching only. |
| `regime_timeline` | Cannot reconstruct the exact regime score timeline or overlay transition timestamps. Regime breakdown uses EOD_PNL log entries only. |
| `router_rejections` | MICRO block reason counts (CONFIRMATION_FAIL, QQQ_FLAT, VIX_STABLE_LOW_CONVICTION, etc.) are estimated from a partial 4-day log sample. Full-year breakdown is unavailable. |
| `order_lifecycle` | Order cancel/invalid attribution (e.g., reconciliation vs. normal entry) cannot be confirmed. SPREAD_RETRY_MAX attribution is inferred from Tag patterns only. |

**Quantified gaps in the current reports:**
- MICRO candidate count: estimated ~3,000-4,000 for the year (log-derived from partial Q1 sampling). True count from `signal_lifecycle` would be exact.
- VASS rejection breakdown: BULL_CALL rejection codes estimated from a 3-day log window in April. Full year exact counts are unknown.
- Regime score distribution: Jan-Aug reconstructed from EOD_PNL log entries. Sep-Dec regime distribution is completely unknown due to log suppression.

---

## What Pulling ObjectStore Data Would Enable

With all 5 artifacts available, the reports could be upgraded to:

1. **Exact MICRO funnel:** Total candidates generated, exact drop codes and counts
   by direction (CALL vs PUT), exact CONFIRMATION_FAIL vs QQQ_FLAT vs TIME_WINDOW
   breakdown for the full year.

2. **Exact VASS rejection funnel:** Full-year R_EXPIRY_CONCENTRATION_CAP vs
   R_SLOT_DIRECTION_MAX vs E_VASS_SIMILAR breakdown by direction (BULL_CALL,
   BEAR_CALL, BULL_PUT, BEAR_PUT).

3. **Regime timeline reconstruction:** Exact score on every bar for all 12
   months. Can confirm: how many days did VASS operate in RISK_ON vs CAUTIOUS vs
   DEFENSIVE? What was the score distribution during the Jan-Mar losing streak?

4. **Order plumbing audit:** Classify every INVALID order by source (RECONCILIATION
   vs NORMAL_ENTRY). Confirm if the CB_LEVEL_5 Greeks breach on 2024-08-22 was
   a one-time event or occurred on other dates.

5. **Gate attribution per trade:** For each losing trade, identify the regime
   score and overlay at entry time — enabling the "did we enter in the correct
   regime?" audit at full fidelity.

---

## How to Pull the Data

### Prerequisites

1. `lean login` must have been run (credentials at `~/.lean/credentials`)
2. The backtest must still be accessible in QC cloud (project 27678023)
3. The run_label must match what was set during the backtest

### Step 1: Identify the run label

The backtest was run as: **V12.21-FullYear2024** (Backtest ID: `e033fd87be2cfee2badda17a8e1718f5`)

### Step 2: Pull the artifacts

```bash
# From the repo root
source venv/bin/activate

# Pull all 5 artifacts and generate crosscheck file
python scripts/pull_objectstore.py "V12.21-FullYear2024" --stage stage12.21

# If the run label doesn't match, try with explicit year:
python scripts/pull_objectstore.py "V12.21-FullYear2024" --stage stage12.21 --year 2024

# Or pull by backtest ID (most reliable):
python scripts/pull_objectstore.py --id e033fd87be2cfee2badda17a8e1718f5 --stage stage12.21
```

### Step 3: Verify artifacts were pulled

After a successful pull, these files should appear in this folder:
```
V12.21-FullYear2024_signal_lifecycle.csv
V12.21-FullYear2024_regime_decisions.csv
V12.21-FullYear2024_regime_timeline.csv
V12.21-FullYear2024_router_rejections.csv
V12.21-FullYear2024_order_lifecycle.csv
V12.21-FullYear2024_OBJECTSTORE_CROSSCHECK.md
```

### Step 4: Regenerate reports

Once the crosscheck file exists, re-run the log-analyzer agent:
```
Use the log-analyzer agent in bypassPermissions mode to re-analyze
docs/audits/logs/stage12.21/ with the V12.21-FullYear2024_logs.txt
and regenerate both reports now that ObjectStore data is available.
```

The agent will automatically detect the crosscheck file (hard gate check),
read all 5 artifacts, and produce exact-count reports.

---

## Alternative: QC Research Notebook

If the `lean cloud object-store` CLI does not work for this run, use the
Research notebook approach:

1. Open project 27678023 in QuantConnect Web IDE
2. Open the Research notebook
3. Upload `scripts/qc_research_objectstore_loader.py` to the project
4. Set `RUN_NAME = "V12.21-FullYear2024"` and `BACKTEST_YEAR = 2024`
5. Run the script — it prints summaries for all 5 artifacts
6. Save the output to `V12.21-FullYear2024_OBJECTSTORE_CROSSCHECK.md` in this folder
7. Re-run the log-analyzer agent

See `docs/guides/objectstore-research-workflow.md` for the full notebook workflow.

---

## Artifact Schema Reference

See `scripts/objectstore_schema.md` for the complete column schema for all
5 artifacts, including value enumerations, key construction logic, and
sharding configuration.

---

## Current RCA Confidence

| Dimension | Confidence | Source Used |
|-----------|-----------|-------------|
| Trade P&L attribution | HIGH | trades.csv (exact) |
| Win/loss by engine | HIGH | orders.csv Tag field (exact) |
| Exit reason by engine | MEDIUM | orders.csv Tag field (derived) |
| MICRO signal funnel | LOW | Log sampling (partial Q1 + suppression) |
| VASS rejection breakdown | LOW | 3-day log window (April only) |
| Regime distribution | MEDIUM | Log EOD_PNL entries Jan-Aug (Sep-Dec unknown) |
| Gate attribution per trade | LOW | Log pattern matching only |
| Order cancel attribution | MEDIUM | Tag field patterns (not confirmed by lifecycle) |
