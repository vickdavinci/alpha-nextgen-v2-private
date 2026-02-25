# Object Store Research Workflow

Use this workflow when `lean cloud object-store get` is blocked by account tier and you still need observability artifacts (`signal_lifecycle`, `regime_timeline`, `router_rejections`, `order_lifecycle`).

## Goal

Load Object Store artifacts directly in QC Research and run RCA without export/download.

## Canonical Loader

Use:

`scripts/qc_research_objectstore_loader.py`

This loader:
- reads single-file and sharded artifacts (`__manifest.json` + `__partNNN.csv`)
- prints loaded key metadata
- prints detector/handoff summary
- prints VASS overlay + exit-plumbing checks

## Steps (QC Web IDE)

1. Open your project in QuantConnect Web IDE.
2. Open `Research` and create a Python notebook.
3. Upload/paste `scripts/qc_research_objectstore_loader.py`.
4. Set:
   - `RUN_NAME`
   - `BACKTEST_YEAR`
5. Run the cell.
6. Save outputs into the stage folder (for audit traceability), e.g.:
   - `docs/audits/logs/<stage>/<RUN_NAME>_OBJECTSTORE_CROSSCHECK.md`

## Validation Checklist

- `[OK]` printed for all expected artifacts.
- `Load Metadata` contains row counts and key names.
- `Detector/Handoff Summary` prints overlay flips and decision/event tuples.
- `VASS Overlay + Exit Plumbing` prints:
  - VASS events by overlay
  - catastrophic exit rows vs same-session re-entry
  - quote-invalid counts and proximity

## Fallback Policy for Analyzers

When local stage folder is missing observability CSVs:
1. Try `python3 scripts/qc_pull_backtest.py "<RUN_NAME>" --all ...`
2. If CLI export is blocked, run this Research workflow.
3. Do not finalize RCA conclusions from logs-only in full-year runs without explicitly marking reduced confidence.
