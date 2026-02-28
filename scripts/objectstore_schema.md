# ObjectStore Observability Schema

Alpha NextGen V2 writes five structured telemetry artifacts to QC Object Store
at the end of every backtest day. These artifacts are the primary RCA data
source for the `log-analyzer` agent.

---

## 1. Signal Lifecycle (`signal_lifecycle_observability`)

**ObjectStore key pattern:**
```
signal_lifecycle_observability__{RUN_SUFFIX}_{YEAR}.csv
```

**Config flags:**
```python
SIGNAL_LIFECYCLE_OBSERVABILITY_ENABLED = True       # master on/off
SIGNAL_LIFECYCLE_OBJECTSTORE_ENABLED  = True        # write to ObjectStore
SIGNAL_LIFECYCLE_OBSERVABILITY_MAX_ROWS = 50_000    # hard row cap (oldest rows dropped)
```

**Written by:** `main.py::_record_signal_lifecycle_event()`

**Populated at:** MICRO drop gate, VASS rejection gate, and any engine signal
routing decision that is explicitly logged via `_record_signal_lifecycle_event`.

### CSV Schema

| Column | Type | Values / Notes |
|--------|------|---------------|
| `time` | str | `YYYY-MM-DD HH:MM:SS` |
| `engine` | str | `VASS`, `MICRO`, `ITM`, `TREND`, `MR`, `HEDGE`, `UNKNOWN` |
| `event` | str | `CANDIDATE`, `APPROVED`, `DROPPED`, `REJECTED`, `BLOCKED` |
| `signal_id` | str | Unique identifier for the signal (e.g. `MICRO_20240312_143500`) |
| `trace_id` | str | End-to-end trace key across lifecycle events (optional) |
| `direction` | str | `CALL`, `PUT`, `BULLISH`, `BEARISH`, `BULL_CALL`, `BEAR_PUT`, etc. |
| `strategy` | str | e.g. `DEBIT_FADE`, `ITM_MOMENTUM`, `BULL_CALL_DEBIT`, `PROTECTIVE_PUTS` |
| `code` | str | Machine-readable rejection/drop code (see Code Reference below) |
| `gate_name` | str | Name of the gate that fired (same as `code` for most events) |
| `reason` | str | Human-readable description of why the signal was rejected |
| `contract_symbol` | str | Option contract ticker if available (e.g. `QQQ 240315C00430000`) |

### Event Types

| Event | Meaning |
|-------|---------|
| `CANDIDATE` | Signal was generated and entered the pipeline |
| `APPROVED` | Signal passed all gates and was submitted for execution |
| `DROPPED` | Signal was dropped before order submission (contract or limit issue) |
| `REJECTED` | Signal was explicitly rejected by a named gate |
| `BLOCKED` | Signal was suppressed by a regime or risk block |

### Common Drop/Reject Codes

| Code | Engine | Meaning |
|------|--------|---------|
| `E_NO_CONTRACT_SELECTED` | MICRO/ITM | No suitable contract found in chain |
| `E_INTRADAY_TRADE_LIMIT` | MICRO | Daily intraday trade limit reached |
| `E_INTRADAY_TIME_WINDOW` | MICRO | Outside allowed entry window |
| `R_EXPIRY_CONCENTRATION_CAP_DIRECTION` | VASS | Too many spreads at same expiry for this direction |
| `R_SLOT_DIRECTION_MAX` | VASS | Max concurrent spreads for this direction already open |
| `E_VASS_SIMILAR` | VASS | Similar spread already open (anti-cluster guard) |
| `BEAR_PUT_ASSIGNMENT_GATE` | VASS | BEAR_PUT short leg would be too close to ATM |
| `TIME_WINDOW_BLOCK` | VASS/MICRO | Entry blocked by time guard (13:55-14:10 ET) |
| `TRADE_LIMIT_BLOCK` | VASS | VASS daily trade limit reached |
| `CONFIRMATION_FAIL` | MICRO | Macro confirmation check failed |
| `VIX_STABLE_LOW_CONVICTION` | MICRO | VIX too stable for FADE entry |
| `QQQ_FLAT` | MICRO | QQQ move insufficient for entry |
| `SAME_STRATEGY_COOLDOWN` | MICRO | Same strategy fired too recently |
| `REGIME_NOT_TRADEABLE` | MICRO | Micro regime state does not allow this strategy |
| `MARGIN_EXCEEDED` | ALL | Margin utilization cap hit |
| `DROP_ENGINE_NO_SIGNAL` | ALL | Generic: engine produced no valid signal |

---

## 2. Regime Decisions (`regime_observability`)

**ObjectStore key pattern:**
```
regime_observability__{RUN_SUFFIX}_{YEAR}.csv
```

**Config flags:**
```python
REGIME_OBSERVABILITY_ENABLED              = True
REGIME_OBSERVABILITY_OBJECTSTORE_ENABLED  = True
REGIME_OBSERVABILITY_MAX_ROWS             = 50_000
```

**Written by:** `main.py::_record_regime_observability_row()`

**Populated at:** Every engine entry decision gate where regime score gates the
trade. Each row is one regime check, not one bar.

### CSV Schema

| Column | Type | Values / Notes |
|--------|------|---------------|
| `time` | str | `YYYY-MM-DD HH:MM:SS` |
| `eod_score` | float | Previous EOD regime score (0-100) |
| `intraday_score` | float | Current intraday-refreshed score (0-100) |
| `delta` | float | Intraday score change from open |
| `eod_delta` | float | EOD score change from previous EOD |
| `momentum_roc` | float | Momentum rate-of-change (raw factor) |
| `vix_5d_change` | float | VIX 5-day percentage change |
| `base_regime` | str | `RISK_ON`, `NEUTRAL`, `CAUTIOUS`, `DEFENSIVE`, `RISK_OFF` |
| `transition_overlay` | str | `STABLE`, `RECOVERING`, `DETERIORATING`, `AMBIGUOUS` |
| `regime_state` | str | `{base_regime}\|{transition_overlay}` composite key |
| `engine` | str | Which engine triggered this gate check |
| `engine_decision` | str | `APPROVED`, `BLOCKED`, `SKIPPED`, `GATED` |
| `strategy_attempted` | str | Strategy name being evaluated (e.g. `BULL_CALL_DEBIT`) |
| `gate_name` | str | Name of the threshold gate that fired |
| `threshold_snapshot` | json str | JSON snapshot of gate thresholds at decision time |

### Key Regime States

| base_regime | Score Range | Trading Posture |
|-------------|------------|-----------------|
| `RISK_ON` | >= 70 | Full leverage, all engines enabled |
| `NEUTRAL` | 50-69 | Full leverage, no hedges |
| `CAUTIOUS` | 45-49 | Light SH hedge (5%), VASS reduced sizing |
| `DEFENSIVE` | 35-44 | Medium SH hedge (8%), VASS credit only |
| `RISK_OFF` | < 35 | No new longs, full hedge (10% SH) |

### Key Transition Overlays

| transition_overlay | Meaning | Options Impact |
|--------------------|---------|---------------|
| `STABLE` | No detected transition | Normal entry rules |
| `RECOVERING` | Score rising after drawdown | ITM_MOMENTUM eligible |
| `DETERIORATING` | Score falling fast | VASS debit exits at EOD (P2 cascade) |
| `AMBIGUOUS` | Mixed signals | VASS exits at EOD if losing (P2 cascade) |

---

## 3. Regime Timeline (`regime_timeline_observability`)

**ObjectStore key pattern:**
```
regime_timeline_observability__{RUN_SUFFIX}_{YEAR}.csv
```

**Config flags:**
```python
REGIME_TIMELINE_OBSERVABILITY_ENABLED    = True
REGIME_TIMELINE_OBJECTSTORE_ENABLED      = True
REGIME_TIMELINE_OBSERVABILITY_MAX_ROWS   = 12_000
```

**Written by:** `main.py::_record_regime_timeline_event()`

**Populated at:** Every intraday refresh and EOD score computation.
Lower row cap (12K) — use for timeline and transition duration analysis.

### CSV Schema

| Column | Type | Values / Notes |
|--------|------|---------------|
| `time` | str | `YYYY-MM-DD HH:MM:SS` |
| `source` | str | `EOD`, `INTRADAY_REFRESH`, `MANUAL` |
| `effective_score` | float | Score used for gate decisions (min of EOD and intraday) |
| `detector_score` | float | Raw detector output before effective-min |
| `eod_score` | float | Last EOD regime score |
| `intraday_score` | float | Current intraday score |
| `delta` | float | Score change from previous sample |
| `eod_delta` | float | EOD score change |
| `momentum_roc` | float | Momentum factor (raw) |
| `vix_5d_change` | float | VIX 5-day change |
| `base_regime` | str | `RISK_ON`, `NEUTRAL`, `CAUTIOUS`, `DEFENSIVE`, `RISK_OFF` |
| `transition_overlay` | str | `STABLE`, `RECOVERING`, `DETERIORATING`, `AMBIGUOUS` |
| `raw_recovery` | int | 0/1 — raw fast-detector sees recovery |
| `raw_deterioration` | int | 0/1 — raw fast-detector sees deterioration |
| `raw_ambiguous` | int | 0/1 — raw fast-detector sees conflicting signals |
| `deterioration_by_fast` | int | 0/1 — deterioration driven by fast detector only |
| `strong_recovery` | int | 0/1 — confirmed strong recovery signal |
| `strong_deterioration` | int | 0/1 — confirmed strong deterioration signal |
| `ambiguous` | int | 0/1 — confirmed ambiguous state |
| `base_candidate` | str | Next candidate base regime (before hysteresis) |
| `overlay_candidate` | str | Next candidate overlay (before hysteresis) |
| `overlay_bars_since_flip` | int | Bars since last overlay transition |
| `sample_seq` | int | Sequential sample counter (for ordering) |
| `transition_score` | float | Score at time of overlay transition (if applicable) |

---

## 4. Router Rejections (`router_rejection_observability`)

**ObjectStore key pattern:**
```
router_rejection_observability__{RUN_SUFFIX}_{YEAR}.csv
```

**Config flags:**
```python
ROUTER_REJECTION_OBSERVABILITY_ENABLED    = True
ROUTER_REJECTION_OBJECTSTORE_ENABLED      = True
ROUTER_REJECTION_OBSERVABILITY_MAX_ROWS   = 25_000
```

**Written by:** `main.py::_record_router_rejection_event()`

**Populated at:** Portfolio Router (`portfolio_router.py`) — every signal that
is rejected or blocked at the routing layer (after engine proposes but before
order submission).

### CSV Schema

| Column | Type | Values / Notes |
|--------|------|---------------|
| `time` | str | `YYYY-MM-DD HH:MM:SS` |
| `stage` | str | Router processing stage where rejection occurred |
| `code` | str | Machine-readable rejection code |
| `symbol` | str | Target symbol or contract |
| `source_tag` | str | Order tag of the originating signal |
| `trace_id` | str | End-to-end trace key |
| `detail` | str | Human-readable rejection detail |
| `engine` | str | Engine that proposed the signal |

### Common Router Rejection Codes

| Code | Stage | Meaning |
|------|-------|---------|
| `MARGIN_EXCEEDED` | VALIDATE | Margin utilization cap would be breached |
| `EXPOSURE_LIMIT` | VALIDATE | NASDAQ/SPY/COMMODITIES exposure group cap |
| `DUPLICATE_SIGNAL` | AGGREGATE | Same symbol+direction already queued this bar |
| `KILL_SWITCH_ACTIVE` | GATE | Kill switch prevents new entries |
| `PANIC_MODE` | GATE | Panic mode blocks new longs |
| `STARTUP_GATE` | GATE | Startup gate not yet armed |
| `GAP_FILTER` | GATE | Gap filter prevents MR entry |
| `VOL_SHOCK` | GATE | Vol shock pause active |

---

## 5. Order Lifecycle (`order_lifecycle_observability`)

**ObjectStore key pattern:**
```
order_lifecycle_observability__{RUN_SUFFIX}_{YEAR}.csv
```

**Config flags:**
```python
ORDER_LIFECYCLE_OBSERVABILITY_ENABLED    = True
ORDER_LIFECYCLE_OBJECTSTORE_ENABLED      = True
ORDER_LIFECYCLE_OBSERVABILITY_MAX_ROWS   = 50_000
```

**Written by:** `portfolio_router.py::_record_order_lifecycle_event()`

**Populated at:** Every order state transition — submission, fill, cancel,
invalid, and OCO trigger. This is the order-plumbing audit trail.

### CSV Schema

| Column | Type | Values / Notes |
|--------|------|---------------|
| `time` | str | `YYYY-MM-DD HH:MM:SS` |
| `status` | str | `SUBMITTED`, `FILLED`, `CANCELED`, `INVALID`, `OCO_TRIGGERED` |
| `order_id` | str | QC order ID |
| `symbol` | str | Symbol or option contract |
| `quantity` | float | Signed quantity (negative = sell) |
| `fill_price` | float | Fill price (0 if not filled) |
| `order_type` | str | `Market`, `Limit`, `Combo Market`, `Combo Limit`, etc. |
| `order_tag` | str | Full order tag (e.g. `VASS:BULL_CALL_DEBIT:ENTRY:...`) |
| `trace_id` | str | End-to-end trace key |
| `message` | str | Broker/engine message on cancel or invalid |
| `source` | str | Attribution: `ENTRY`, `EXIT`, `OCO`, `RECONCILIATION`, `EMERGENCY` |

### Order Status Values

| status | Meaning |
|--------|---------|
| `SUBMITTED` | Order sent to broker |
| `FILLED` | Order fully executed |
| `CANCELED` | Order canceled (OCO, timeout, or explicit) |
| `INVALID` | Order rejected by broker (buying power, market hours, etc.) |
| `OCO_TRIGGERED` | OCO pair fired (either profit or stop leg) |
| `PARTIAL_FILL` | Partially filled (rare in backtests) |

---

## ObjectStore Key Construction

The algorithm builds keys as:
```
{PREFIX}__{RUN_SUFFIX}_{YEAR}.csv
```

Where:
- `PREFIX` is the config value (e.g. `signal_lifecycle_observability`)
- `RUN_SUFFIX` is sanitized from the algorithm's `run_label` parameter
- `YEAR` is the backtest year (4-digit integer)

For sharded payloads (row count exceeds `OBSERVABILITY_OBJECTSTORE_SHARD_MAX_ROWS`):
```
{PREFIX}__{RUN_SUFFIX}_{YEAR}__manifest.json   (shard index)
{PREFIX}__{RUN_SUFFIX}_{YEAR}__part001.csv
{PREFIX}__{RUN_SUFFIX}_{YEAR}__part002.csv
...
```

### Key Variants Tried (in order)

The `pull_objectstore.py` script tries these run suffix variants:
1. Sanitized `run_name` argument (e.g. `V12_21_FullYear2024`)
2. `year_{YEAR}` (e.g. `year_2024`)
3. `DEFAULT`
4. `default`

If none match, no artifact is found for that label.

---

## Sharding Configuration

```python
OBSERVABILITY_OBJECTSTORE_SHARD_ENABLED  = True    # write as shards
OBSERVABILITY_OBJECTSTORE_SHARD_MAX_ROWS = 12_000  # rows per shard
OBSERVABILITY_OBJECTSTORE_MAX_SHARDS     = 32      # max shards (= 384K rows total)
OBSERVABILITY_OBJECTSTORE_SAVE_RETRIES   = 2       # write retry attempts
```

The `pull_objectstore.py` script handles manifest-based and legacy blind-probed
sharding automatically, merging all parts into a single CSV in the output dir.

---

## Pull Workflow

```bash
# 1. Identify the backtest
python scripts/pull_objectstore.py --list

# 2. Pull all 5 artifacts + generate crosscheck file
python scripts/pull_objectstore.py "V12.21-FullYear2024" --stage stage12.21

# 3. Run log-analyzer (crosscheck file is now present)
# Use the log-analyzer agent from .claude/agents/log-analyzer.md
```

The crosscheck file (`*_OBJECTSTORE_CROSSCHECK.md`) is a required input for
the `log-analyzer` agent. It summarizes artifact row counts and key statistics
so the agent can validate data completeness before producing reports.

---

## Completeness vs. Log Budget

In full-year backtests, console log budget (5 MB) is typically exhausted by
Q3 or early Q4. The ObjectStore artifacts are written independently of the log
budget and contain the full-year event stream. Use ObjectStore data as primary
RCA source for H2 analysis.

The `signal_lifecycle` artifact has a 50K row hard cap. For high-volume runs
with many MICRO candidates, rows are dropped (oldest first) when the cap is
hit. The `_signal_lifecycle_overflow_logged` flag in logs indicates overflow.
If overflow occurred, Q4 signal counts may undercount true candidates.
