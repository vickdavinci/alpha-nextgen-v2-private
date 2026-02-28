# Shared Engineering Process (Codex + Claude)

This is the single process contract for all AI agents in this repo.
Both `AGENTS.md` and `CLAUDE.md` defer to this file.

## Scope

- Applies to: Codex, Claude Code, and humans using AI-generated changes.
- Goal: prevent plumbing regressions in router/positions/order/data/model/persistence/scheduling/utils.

## Definition Of Done (Mandatory)

1. Implement code changes.
2. Run gate: `./scripts/gate.sh changed`
3. Fix all failing checks.
4. Commit with `templates/commit_message.md` structure.
5. For backtest work, run:
   - `./scripts/run_backtest.sh ...`
   - `./scripts/pull_logs.sh ...`
   - `./scripts/analyze.sh ...`
6. Write handoff using `templates/handoff.md`.

Do not commit if gate fails.

## Commands

- Fast gate (changed files): `./scripts/gate.sh changed`
- Full gate (broader checks): `./scripts/gate.sh full`
- Run backtest: `./scripts/run_backtest.sh --run-name <name> --start-date YYYY-MM-DD --end-date YYYY-MM-DD`
- Pull artifacts: `./scripts/pull_logs.sh --run-name <name> --stage-dir docs/audits/logs/<stage>`
- Generate reports: `./scripts/analyze.sh --stage-dir docs/audits/logs/<stage> --run-name <name>`

## Required Artifacts Per Iteration

- `<RUN>_logs.txt`
- `<RUN>_orders.csv`
- `<RUN>_trades.csv`
- `<RUN>_overview.txt`
- `<RUN>_REPORT.md`
- `<RUN>_SIGNAL_FLOW_REPORT.md`
- `<RUN>_TRADE_DETAIL_REPORT.md`

## Enforcement

- Pre-commit runs lightweight shared gate (`--no-tests`).
- Commit hook runs staged-only safety checks (`./scripts/gate.sh changed --no-tests --staged-only`).
- Full gate remains mandatory before commit/push/backtest iteration.
- Checklist source of truth: `policy/checklist.yaml`.
