#!/usr/bin/env python3
"""
QuantConnect Backtest Data Puller

Fetches LOGS, ORDERS, TRADES, and OVERVIEW from QuantConnect backtests
and saves them to docs/audits/logs/stage4/

Usage:
    # List recent backtests
    python scripts/qc_pull_backtest.py --list

    # Pull all data for a backtest (by name or partial match)
    python scripts/qc_pull_backtest.py "V5.1-Governor-2022H1"

    # Pull specific data types
    python scripts/qc_pull_backtest.py "V5.1-Governor" --logs --orders

    # Pull by backtest ID
    python scripts/qc_pull_backtest.py --id e90efe9e30aacbcc4fe5cfe708d9ac67

    # Pull all data types (default)
    python scripts/qc_pull_backtest.py "V5.1-Governor" --all
"""

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

try:
    import requests
except ImportError:
    print("Error: requests module not found. Run: pip install requests")
    sys.exit(1)


# ============================================================================
# Configuration
# ============================================================================

PROJECT_ID = 27678023  # AlphaNextGen cloud project
OUTPUT_DIR = Path("docs/audits/logs/stage4")
CREDENTIALS_FILE = Path.home() / ".lean" / "credentials"

OBSERVABILITY_ARTIFACT_SPECS = (
    ("regime_decisions", "REGIME_OBSERVABILITY_OBJECTSTORE_KEY_PREFIX", "regime_observability"),
    (
        "regime_timeline",
        "REGIME_TIMELINE_OBJECTSTORE_KEY_PREFIX",
        "regime_timeline_observability",
    ),
    (
        "signal_lifecycle",
        "SIGNAL_LIFECYCLE_OBJECTSTORE_KEY_PREFIX",
        "signal_lifecycle_observability",
    ),
    (
        "router_rejections",
        "ROUTER_REJECTION_OBJECTSTORE_KEY_PREFIX",
        "router_rejection_observability",
    ),
    (
        "order_lifecycle",
        "ORDER_LIFECYCLE_OBJECTSTORE_KEY_PREFIX",
        "order_lifecycle_observability",
    ),
)
OBSERVABILITY_PART_MAX_FETCH = 64


# ============================================================================
# QC API Client
# ============================================================================


class QCClient:
    """QuantConnect API client with timestamp-based authentication."""

    BASE_URL = "https://www.quantconnect.com/api/v2"

    def __init__(self):
        self.user_id, self.api_token = self._load_credentials()

    def _load_credentials(self):
        """Load credentials from lean CLI config."""
        if not CREDENTIALS_FILE.exists():
            print(f"Error: Credentials not found at {CREDENTIALS_FILE}")
            print("Run 'lean login' to authenticate with QuantConnect")
            sys.exit(1)

        with open(CREDENTIALS_FILE) as f:
            creds = json.load(f)

        return creds["user-id"], creds["api-token"]

    def _get_auth(self):
        """Generate timestamp-based authentication."""
        timestamp = str(int(time.time()))
        hash_string = f"{self.api_token}:{timestamp}"
        api_hash = hashlib.sha256(hash_string.encode()).hexdigest()
        return (self.user_id, api_hash), {"Timestamp": timestamp}

    def _request(self, method, endpoint, **kwargs):
        """Make authenticated API request."""
        auth, headers = self._get_auth()
        kwargs.setdefault("headers", {}).update(headers)
        kwargs["auth"] = auth

        url = f"{self.BASE_URL}/{endpoint}"
        response = requests.request(method, url, **kwargs)
        data = response.json()

        if not data.get("success", False) and "errors" in data:
            # Some endpoints return data even with success=false
            if not any(key in data for key in ["backtests", "backtest", "logs", "orders"]):
                raise Exception(f"API Error: {data['errors']}")

        return data

    def list_backtests(self, project_id=PROJECT_ID, limit=20):
        """List recent backtests for a project."""
        data = self._request("GET", "backtests/read", params={"projectId": project_id})
        backtests = data.get("backtests", [])
        return backtests[:limit]

    def get_backtest(self, project_id, backtest_id):
        """Get backtest details including statistics."""
        data = self._request(
            "GET", "backtests/read", params={"projectId": project_id, "backtestId": backtest_id}
        )
        return data.get("backtest", {})

    def get_logs(self, project_id, backtest_id, callback=None):
        """Fetch all logs with pagination."""
        all_logs = []
        start_line = 0
        page_size = 200
        max_pages = 500  # Safety limit (~100k lines)

        for page in range(max_pages):
            data = self._request(
                "POST",
                "backtests/read/log",
                json={
                    "projectId": project_id,
                    "backtestId": backtest_id,
                    "start": start_line,
                    "end": start_line + page_size,
                    "query": " ",  # Space = all logs
                },
            )

            logs = data.get("logs", [])
            if not logs:
                break

            all_logs.extend(logs)

            if callback:
                callback(page + 1, len(all_logs))

            if len(logs) < page_size:
                break

            start_line += page_size
            time.sleep(0.2)  # Rate limiting

        return all_logs

    def get_orders(self, project_id, backtest_id, callback=None):
        """Fetch all orders with pagination (max 100 per request)."""
        all_orders = []
        start_idx = 0
        page_size = 100
        max_pages = 100  # Safety limit (10k orders)

        for page in range(max_pages):
            data = self._request(
                "POST",
                "backtests/orders/read",
                json={
                    "projectId": project_id,
                    "backtestId": backtest_id,
                    "start": start_idx,
                    "end": start_idx + page_size,
                },
            )

            orders = data.get("orders", [])
            if not orders:
                break

            all_orders.extend(orders)

            if callback:
                callback(page + 1, len(all_orders))

            total_length = data.get("length", 0)
            if start_idx + page_size >= total_length:
                break

            start_idx += page_size
            time.sleep(0.2)  # Rate limiting

        return all_orders

    def get_trades(self, backtest):
        """Extract trades from backtest total performance."""
        perf = backtest.get("totalPerformance") or {}
        if not isinstance(perf, dict):
            return []

        closed_trades = perf.get("closedTrades") or []
        if not isinstance(closed_trades, list):
            return []

        return closed_trades


# ============================================================================
# Data Formatters
# ============================================================================


def sanitize_filename(name):
    """Convert backtest name to safe filename."""
    # Replace special chars with underscore
    safe = re.sub(r"[^\w\-.]", "_", name)
    # Remove consecutive underscores
    safe = re.sub(r"_+", "_", safe)
    return safe.strip("_")


def format_overview(backtest):
    """Format backtest overview/statistics as text."""
    lines = []
    lines.append("=" * 70)
    lines.append(f"BACKTEST OVERVIEW: {backtest.get('name', 'Unknown')}")
    lines.append("=" * 70)
    lines.append("")

    # Basic info
    lines.append("## Basic Info")
    lines.append(f"  Backtest ID:    {backtest.get('backtestId', 'N/A')}")
    lines.append(f"  Project ID:     {backtest.get('projectId', 'N/A')}")
    lines.append(f"  Status:         {backtest.get('status', 'N/A')}")
    lines.append(f"  Created:        {backtest.get('created', 'N/A')}")
    lines.append(f"  Backtest Start: {backtest.get('backtestStart', 'N/A')}")
    lines.append(f"  Backtest End:   {backtest.get('backtestEnd', 'N/A')}")
    lines.append(f"  Node:           {backtest.get('nodeName', 'N/A')}")
    lines.append("")

    # Statistics
    stats = backtest.get("statistics", {})
    if stats:
        lines.append("## Statistics")
        # Key metrics first
        key_metrics = [
            "Net Profit",
            "Compounding Annual Return",
            "Sharpe Ratio",
            "Sortino Ratio",
            "Drawdown",
            "Win Rate",
            "Loss Rate",
            "Total Orders",
            "Total Fees",
            "Start Equity",
            "End Equity",
        ]
        for key in key_metrics:
            if key in stats:
                lines.append(f"  {key:30s}: {stats[key]}")

        lines.append("")
        lines.append("  --- All Statistics ---")
        for key, val in sorted(stats.items()):
            if key not in key_metrics:
                lines.append(f"  {key:30s}: {val}")
        lines.append("")

    # Runtime statistics
    runtime_stats = backtest.get("runtimeStatistics", {})
    if runtime_stats:
        lines.append("## Runtime Statistics")
        for key, val in sorted(runtime_stats.items()):
            lines.append(f"  {key:30s}: {val}")
        lines.append("")

    # Error info if any
    if backtest.get("error"):
        lines.append("## Error")
        lines.append(f"  {backtest.get('error')}")
        if backtest.get("stacktrace"):
            lines.append("")
            lines.append("  Stacktrace:")
            for line in backtest.get("stacktrace", "").split("\n"):
                lines.append(f"    {line}")
        lines.append("")

    return "\n".join(lines)


def format_orders_csv(orders):
    """Format orders as CSV."""
    if not orders:
        return "Time,Symbol,Price,Quantity,Type,Status,Direction,Value,Tag,ID\n"

    order_type_map = {
        0: "Market",
        1: "Limit",
        2: "Stop Market",
        3: "Stop Limit",
        4: "Market On Open",
        5: "Market On Close",
        6: "Option Exercise",
        7: "Option Assignment",
        8: "Combo Market",
        9: "Combo Limit",
        10: "Combo Leg Limit",
    }
    order_status_map = {
        0: "New",
        1: "Submitted",
        2: "Partially Filled",
        3: "Filled",
        4: "Cancel Pending",
        5: "Canceled",
        6: "None",
        7: "Invalid",
        8: "Cancel Pending",
        9: "Update Submitted",
    }
    order_direction_map = {0: "Buy", 1: "Sell", 2: "Hold"}

    def _enum_label(raw_value, mapping):
        text = str(raw_value if raw_value is not None else "").strip()
        if not text:
            return ""
        if text.lstrip("-").isdigit():
            return mapping.get(int(text), text)
        return text

    lines = ["Time,Symbol,Price,Quantity,Type,Status,Direction,Value,Tag,ID"]

    for order in orders:
        time_str = order.get("time", "")
        symbol = order.get("symbol", {})
        # Handle nested symbol format: {"value": "QQQ", "id": "...", ...}
        if isinstance(symbol, dict):
            symbol_str = symbol.get("value", symbol.get("Value", ""))
        else:
            symbol_str = str(symbol)
        price = order.get("price", 0)
        quantity = order.get("quantity", 0)
        order_type = _enum_label(order.get("type", ""), order_type_map)
        status = _enum_label(order.get("status", ""), order_status_map)
        direction = _enum_label(order.get("direction", ""), order_direction_map)
        value = order.get("value", 0)
        tag = str(order.get("tag", "") or "").replace('"', '""')
        order_id = order.get("id", "")

        lines.append(
            f'{time_str},"{symbol_str}",{price},{quantity},{order_type},{status},{direction},{value},"{tag}",{order_id}'
        )

    return "\n".join(lines)


def format_trades_csv(trades):
    """Format closed trades as CSV."""
    if not trades:
        return "Entry Time,Symbols,Exit Time,Direction,Entry Price,Exit Price,Quantity,P&L,Fees,MAE,MFE,Drawdown,IsWin,Duration,Order IDs\n"

    lines = [
        "Entry Time,Symbols,Exit Time,Direction,Entry Price,Exit Price,Quantity,P&L,Fees,MAE,MFE,Drawdown,IsWin,Duration,Order IDs"
    ]
    trade_direction_map = {0: "Buy", 1: "Sell", 2: "Hold"}

    def _trade_direction(raw_value):
        text = str(raw_value if raw_value is not None else "").strip()
        if not text:
            return ""
        if text.lstrip("-").isdigit():
            return trade_direction_map.get(int(text), text)
        return text

    for trade in trades:
        entry_time = trade.get("entryTime", "")

        # Handle symbols - can be a list or single symbol
        symbols = trade.get("symbols", [])
        if isinstance(symbols, list):
            symbol_strs = []
            for s in symbols:
                if isinstance(s, dict):
                    symbol_strs.append(s.get("value", s.get("Value", "")))
                else:
                    symbol_strs.append(str(s))
            symbol_str = "; ".join(symbol_strs)
        elif isinstance(symbols, dict):
            symbol_str = symbols.get("value", symbols.get("Value", ""))
        else:
            symbol_str = str(symbols)

        exit_time = trade.get("exitTime", "")
        direction = _trade_direction(trade.get("direction", ""))
        entry_price = trade.get("entryPrice", 0)
        exit_price = trade.get("exitPrice", 0)
        quantity = trade.get("quantity", 0)
        pnl = trade.get("profitLoss", 0)
        fees = trade.get("totalFees", 0)
        mae = trade.get("mae", 0)  # Max Adverse Excursion
        mfe = trade.get("mfe", 0)  # Max Favorable Excursion
        drawdown = trade.get("endTradeDrawdown", 0)
        is_win = 1 if trade.get("isWin", False) else 0
        duration = trade.get("duration", "")
        order_ids = trade.get("orderIds", [])
        order_ids_str = ";".join(str(x) for x in order_ids) if order_ids else ""

        lines.append(
            f'{entry_time},"{symbol_str}",{exit_time},{direction},{entry_price},{exit_price},{quantity},{pnl},{fees},{mae},{mfe},{drawdown},{is_win},"{duration}","{order_ids_str}"'
        )

    return "\n".join(lines)


def format_logs(logs, backtest_name, backtest_id, project_id):
    """Format logs with header."""
    header = [
        f"# QuantConnect Backtest Logs",
        f"# Backtest: {backtest_name}",
        f"# Backtest ID: {backtest_id}",
        f"# Project ID: {project_id}",
        f"# Fetched: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"# Total Lines: {len(logs)}",
        "#" + "=" * 60,
        "",
    ]
    return "\n".join(header) + "\n".join(logs)


# ============================================================================
# Main Functions
# ============================================================================


def find_backtest(client, search_term, project_id=PROJECT_ID):
    """Find a backtest by name or partial match."""
    backtests = client.list_backtests(project_id, limit=100)

    # Exact match first
    for bt in backtests:
        if bt.get("name", "").lower() == search_term.lower():
            return bt

    # Partial match
    matches = []
    for bt in backtests:
        if search_term.lower() in bt.get("name", "").lower():
            matches.append(bt)

    if len(matches) == 1:
        return matches[0]
    elif len(matches) > 1:
        print(f"Multiple matches found for '{search_term}':")
        for i, bt in enumerate(matches[:10]):
            print(f"  {i+1}. {bt.get('name')} ({bt.get('status')})")
        print("\nPlease use a more specific name or use --id with the backtest ID")
        return None

    print(f"No backtest found matching '{search_term}'")
    return None


def infer_backtest_year(backtest_name: str, backtest: dict) -> int:
    """Infer expected backtest year from run name, falling back to backtestStart."""
    m = re.search(r"full\s*year[_-]?(\d{4})", str(backtest_name or ""), flags=re.IGNORECASE)
    if m:
        return int(m.group(1))
    start = str(backtest.get("backtestStart", "") or "")
    if re.match(r"^\d{4}-\d{2}-\d{2}", start):
        return int(start[:4])
    created = str(backtest.get("created", "") or "")
    if re.match(r"^\d{4}-\d{2}-\d{2}", created):
        return int(created[:4])
    return datetime.now().year


def _get_observability_prefix(config_attr: str, default_prefix: str) -> str:
    try:
        import config as algo_config  # type: ignore

        raw = str(getattr(algo_config, config_attr, default_prefix) or default_prefix)
    except Exception:
        raw = default_prefix
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("_")
    return safe or default_prefix


def _sanitize_run_suffix(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "")).strip("_")


def _extract_backtest_run_label(backtest: dict) -> str:
    """Best-effort run_label extraction from QC backtest payload."""
    params = backtest.get("parameters")
    if isinstance(params, dict):
        label = str(params.get("run_label", "") or "").strip()
        if label:
            return label
    if isinstance(params, list):
        for item in params:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "") or item.get("key", "") or "").strip().lower()
            if name != "run_label":
                continue
            value = str(item.get("value", "") or "").strip()
            if value:
                return value
    return ""


def _build_run_suffix_candidates(run_name: str, backtest: dict, year: int) -> list:
    """Candidate run suffixes used by algorithm ObjectStore key builder."""
    candidates = []
    for raw in (
        run_name,
        str(backtest.get("name", "") or ""),
        _extract_backtest_run_label(backtest),
        f"year_{year}",
    ):
        safe = _sanitize_run_suffix(raw)
        if safe and safe not in candidates:
            candidates.append(safe)
    return candidates


def _artifact_manifest_key(base_key: str) -> str:
    stem = base_key[:-4] if base_key.endswith(".csv") else base_key
    return f"{stem}__manifest.json"


def _artifact_part_key(base_key: str, idx: int) -> str:
    stem = base_key[:-4] if base_key.endswith(".csv") else base_key
    return f"{stem}__part{idx:03d}.csv"


def _lean_object_store_get(key: str, output_dir: Path) -> Optional[Path]:
    cmd = [
        "lean",
        "cloud",
        "object-store",
        "get",
        key,
        "--destination-folder",
        str(output_dir),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    maybe_file = output_dir / key
    return maybe_file if maybe_file.exists() else None


def _load_shard_manifest(manifest_path: Path) -> dict:
    try:
        with open(manifest_path) as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _merge_part_files(part_files: List[Path], target: Path) -> bool:
    if not part_files:
        return False
    with open(target, "w", encoding="utf-8", newline="") as out:
        wrote_header = False
        for idx, path in enumerate(part_files):
            with open(path, "r", encoding="utf-8", errors="ignore") as src:
                lines = src.read().splitlines()
            if not lines:
                continue
            if not wrote_header:
                out.write("\n".join(lines))
                out.write("\n")
                wrote_header = True
                continue
            out.write("\n".join(lines[1:]))
            out.write("\n")
    return wrote_header


def pull_observability_artifacts(run_name: str, backtest: dict, output_dir: Path) -> dict:
    """
    Pull structured telemetry artifacts from QC Object Store.

    Returns dict: artifact_label -> local_path for downloaded files.
    """
    results = {}
    if shutil.which("lean") is None:
        print("  Skipping observability pull: `lean` CLI not found")
        return results

    year = infer_backtest_year(run_name, backtest)
    run_safe = _sanitize_run_suffix(run_name) or "run"
    run_suffix_candidates = _build_run_suffix_candidates(run_name, backtest, year)

    print("\nFetching observability artifacts from Object Store...")
    for label, config_attr, default_prefix in OBSERVABILITY_ARTIFACT_SPECS:
        prefix = _get_observability_prefix(config_attr, default_prefix)
        downloaded = None
        for run_suffix in run_suffix_candidates:
            key = f"{prefix}__{run_suffix}_{year}.csv"
            maybe_file = _lean_object_store_get(key, output_dir)
            if maybe_file is not None:
                downloaded = maybe_file
                break

            manifest_key = _artifact_manifest_key(key)
            manifest_file = _lean_object_store_get(manifest_key, output_dir)
            part_files = []
            if manifest_file is not None:
                manifest = _load_shard_manifest(manifest_file)
                expected_parts = int(manifest.get("parts", 0) or 0)
                for idx in range(1, expected_parts + 1):
                    part_key = _artifact_part_key(key, idx)
                    part_file = _lean_object_store_get(part_key, output_dir)
                    if part_file is None:
                        part_files = []
                        break
                    part_files.append(part_file)
                if part_files:
                    shard_target = output_dir / f"{run_safe}_{label}.csv"
                    if shard_target.exists():
                        shard_target.unlink()
                    if _merge_part_files(part_files, shard_target):
                        downloaded = shard_target
                        print(f"  Saved (sharded): {downloaded}")
                        for part_file in part_files:
                            if part_file.exists():
                                part_file.unlink()
                        if manifest_file.exists():
                            manifest_file.unlink()
                        break

            # Fallback for legacy sharded runs without manifest.
            if downloaded is None:
                part_files = []
                for idx in range(1, OBSERVABILITY_PART_MAX_FETCH + 1):
                    part_key = _artifact_part_key(key, idx)
                    part_file = _lean_object_store_get(part_key, output_dir)
                    if part_file is None:
                        if idx == 1:
                            break
                        break
                    part_files.append(part_file)
                if part_files:
                    shard_target = output_dir / f"{run_safe}_{label}.csv"
                    if shard_target.exists():
                        shard_target.unlink()
                    if _merge_part_files(part_files, shard_target):
                        downloaded = shard_target
                        print(f"  Saved (sharded, legacy): {downloaded}")
                        for part_file in part_files:
                            if part_file.exists():
                                part_file.unlink()
                        break

        if downloaded is None:
            continue

        target = output_dir / f"{run_safe}_{label}.csv"
        if downloaded != target:
            if target.exists():
                target.unlink()
            downloaded.rename(target)
            print(f"  Saved: {target}")
        results[label] = target

    if not results:
        print("  No observability artifacts found for this run label/year")
    return results


def generate_reports_for_run(stage_dir: Path, run_name: str) -> bool:
    """Generate synced REPORT/SIGNAL_FLOW/TRADE_DETAIL files for a pulled run."""
    required = (
        stage_dir / f"{run_name}_logs.txt",
        stage_dir / f"{run_name}_trades.csv",
        stage_dir / f"{run_name}_overview.txt",
    )
    missing = [str(p.name) for p in required if not p.exists()]
    if missing:
        print("  Skipping report generation (missing artifacts): " + ", ".join(missing))
        return True

    script_path = Path(__file__).resolve().parent / "generate_run_reports.py"
    if not script_path.exists():
        print(f"  Error: report generator not found: {script_path}")
        return False

    cmd = [
        sys.executable,
        str(script_path),
        "--stage-dir",
        str(stage_dir),
        "--run-name",
        run_name,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print("  Report generation failed:")
        if proc.stderr.strip():
            print(proc.stderr.strip())
        if proc.stdout.strip():
            print(proc.stdout.strip())
        return False

    if proc.stdout.strip():
        print(proc.stdout.strip())
    return True


def pull_backtest_data(
    client,
    backtest_id,
    project_id=PROJECT_ID,
    pull_logs=True,
    pull_orders=True,
    pull_trades=True,
    pull_overview=True,
    pull_observability=True,
    output_dir=OUTPUT_DIR,
    generate_reports=True,
):
    """Pull all requested data for a backtest."""

    print(f"\nFetching backtest details...")
    backtest = client.get_backtest(project_id, backtest_id)

    if not backtest:
        print("Error: Could not fetch backtest details")
        return False

    name = backtest.get("name", backtest_id)
    safe_name = sanitize_filename(name)

    print(f"  Name: {name}")
    print(f"  Status: {backtest.get('status')}")
    print(f"  Period: {backtest.get('backtestStart')} to {backtest.get('backtestEnd')}")

    # Ensure output directory exists
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    results = {}

    # Pull overview
    if pull_overview:
        print(f"\nGenerating overview...")
        overview = format_overview(backtest)
        overview_file = output_dir / f"{safe_name}_overview.txt"
        with open(overview_file, "w") as f:
            f.write(overview)
        print(f"  Saved: {overview_file}")
        results["overview"] = overview_file

    # Pull logs
    if pull_logs:
        print(f"\nFetching logs...")

        def log_progress(page, total):
            print(f"  Page {page}: {total} lines", end="\r")

        logs = client.get_logs(project_id, backtest_id, callback=log_progress)
        print(f"  Total: {len(logs)} lines" + " " * 20)

        if logs:
            logs_content = format_logs(logs, name, backtest_id, project_id)
            logs_file = output_dir / f"{safe_name}_logs.txt"
            with open(logs_file, "w") as f:
                f.write(logs_content)
            print(f"  Saved: {logs_file}")
            results["logs"] = logs_file

    # Pull orders
    if pull_orders:
        print(f"\nFetching orders...")

        def order_progress(page, total):
            print(f"  Page {page}: {total} orders", end="\r")

        orders = client.get_orders(project_id, backtest_id, callback=order_progress)
        print(f"  Total: {len(orders)} orders" + " " * 20)

        orders_csv = format_orders_csv(orders)
        orders_file = output_dir / f"{safe_name}_orders.csv"
        with open(orders_file, "w") as f:
            f.write(orders_csv)
        print(f"  Saved: {orders_file}")
        results["orders"] = orders_file

    # Pull trades
    if pull_trades:
        print(f"\nExtracting trades...")
        trades = client.get_trades(backtest)
        print(f"  Total: {len(trades)} closed trades")

        trades_csv = format_trades_csv(trades)
        trades_file = output_dir / f"{safe_name}_trades.csv"
        with open(trades_file, "w") as f:
            f.write(trades_csv)
        print(f"  Saved: {trades_file}")
        results["trades"] = trades_file

    if pull_observability:
        obs_files = pull_observability_artifacts(name, backtest, output_dir)
        for label, fpath in obs_files.items():
            results[f"obs_{label}"] = fpath

    print(f"\n{'='*50}")
    print(f"Complete! Files saved to {output_dir}/")
    for dtype, fpath in results.items():
        size = os.path.getsize(fpath)
        print(f"  {dtype:10s}: {fpath.name} ({size:,} bytes)")

    if generate_reports and any([pull_logs, pull_orders, pull_trades, pull_overview]):
        print("\nGenerating synced reports...")
        if not generate_reports_for_run(output_dir, safe_name):
            return False

    return True


def list_backtests(client, project_id=PROJECT_ID, limit=20):
    """List recent backtests."""
    print(f"Recent backtests for project {project_id}:\n")

    backtests = client.list_backtests(project_id, limit=limit)

    for i, bt in enumerate(backtests, 1):
        name = bt.get("name", "Unnamed")
        bt_id = bt.get("backtestId", "N/A")
        status = bt.get("status", "Unknown")
        created = bt.get("created", "N/A")

        status_icon = {
            "Completed.": "[OK]",
            "In Progress...": "[..]",
            "Runtime Error": "[ERR]",
            "Build Error": "[ERR]",
        }.get(status, "[??]")

        print(f"  {i:2d}. {status_icon} {name}")
        print(f"      ID: {bt_id}")
        print(f"      Created: {created} | Status: {status}")
        print()

    print(f"Total: {len(backtests)} backtests shown")
    print(f'\nTo pull data: python scripts/qc_pull_backtest.py "<name>"')


# ============================================================================
# CLI Entry Point
# ============================================================================


def main():
    parser = argparse.ArgumentParser(
        description="Pull backtest data from QuantConnect",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --list                          List recent backtests
  %(prog)s "V5.1-Governor-2022H1"          Pull all data for backtest
  %(prog)s "V5.1-Governor" --logs          Pull only logs
  %(prog)s --id abc123def456 --all         Pull by backtest ID
        """,
    )

    parser.add_argument("name", nargs="?", help="Backtest name (partial match supported)")
    parser.add_argument("--id", dest="backtest_id", help="Backtest ID (exact)")
    parser.add_argument("--list", action="store_true", help="List recent backtests")
    parser.add_argument("--limit", type=int, default=20, help="Number of backtests to list")
    parser.add_argument("--project", type=int, default=PROJECT_ID, help="Project ID")
    parser.add_argument("--output", type=str, default=str(OUTPUT_DIR), help="Output directory")

    # Data type flags
    parser.add_argument("--all", action="store_true", help="Pull all data types (default)")
    parser.add_argument("--logs", action="store_true", help="Pull logs")
    parser.add_argument("--orders", action="store_true", help="Pull orders")
    parser.add_argument("--trades", action="store_true", help="Pull trades")
    parser.add_argument("--overview", action="store_true", help="Pull overview/statistics")
    parser.add_argument(
        "--skip-reports",
        action="store_true",
        help="Skip auto-generation of run reports after pull",
    )
    parser.add_argument(
        "--skip-observability",
        action="store_true",
        help="Skip pulling structured observability CSV artifacts from Object Store",
    )

    args = parser.parse_args()

    # Initialize client
    client = QCClient()

    # List mode
    if args.list:
        list_backtests(client, args.project, args.limit)
        return 0

    # Need either name or ID
    if not args.name and not args.backtest_id:
        parser.print_help()
        return 1

    # Find backtest
    if args.backtest_id:
        backtest_id = args.backtest_id
    else:
        bt = find_backtest(client, args.name, args.project)
        if not bt:
            return 1
        backtest_id = bt["backtestId"]
        print(f"Found: {bt['name']}")

    # Determine what to pull
    pull_all = args.all or not any([args.logs, args.orders, args.trades, args.overview])

    pull_logs = args.logs or pull_all
    pull_orders = args.orders or pull_all
    pull_trades = args.trades or pull_all
    pull_overview = args.overview or pull_all

    # Pull data
    success = pull_backtest_data(
        client,
        backtest_id,
        args.project,
        pull_logs=pull_logs,
        pull_orders=pull_orders,
        pull_trades=pull_trades,
        pull_overview=pull_overview,
        pull_observability=not args.skip_observability,
        output_dir=args.output,
        generate_reports=not args.skip_reports,
    )

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
