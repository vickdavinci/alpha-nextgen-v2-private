#!/usr/bin/env python3
"""
Alpha NextGen V2 - ObjectStore Observability Artifact Puller

Pulls structured telemetry CSV artifacts from a completed QuantConnect backtest
via the QC REST API (Object Store). Produces a CROSSCHECK markdown file suitable
for use as the primary RCA source with the log-analyzer agent.

Authentication uses the same pattern as qc_push_individual.py:
    Basic auth with (user_id, sha256(api_token:timestamp)), plus Timestamp header.
Credentials are loaded from ~/.lean/credentials (set by `lean login`).

Usage:
    # Pull by backtest name (partial match)
    python scripts/pull_objectstore.py "V12.21-FullYear2024" --stage stage12.21

    # Pull by backtest ID (exact)
    python scripts/pull_objectstore.py --id abc123def --stage stage12.21

    # Pull and write crosscheck file only (artifacts already on disk)
    python scripts/pull_objectstore.py --crosscheck-only --stage stage12.21 --run V12.21-FullYear2024

    # Dry-run: show what keys would be fetched without downloading
    python scripts/pull_objectstore.py "V12.21-FullYear2024" --stage stage12.21 --dry-run

    # Use a specific year (overrides inference from run name)
    python scripts/pull_objectstore.py "V12.21-FullYear2024" --stage stage12.21 --year 2024

Output:
    docs/audits/logs/<stage>/<RUN>_signal_lifecycle.csv
    docs/audits/logs/<stage>/<RUN>_regime_decisions.csv
    docs/audits/logs/<stage>/<RUN>_regime_timeline.csv
    docs/audits/logs/<stage>/<RUN>_router_rejections.csv
    docs/audits/logs/<stage>/<RUN>_order_lifecycle.csv
    docs/audits/logs/<stage>/<RUN>_OBJECTSTORE_CROSSCHECK.md
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import requests
except ImportError:
    print("Error: requests not installed. Run: pip install requests")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT_ID = 27678023  # AlphaNextGen cloud project
CREDENTIALS_FILE = Path.home() / ".lean" / "credentials"
API_BASE = "https://www.quantconnect.com/api/v2"
LOGS_DIR = Path("docs/audits/logs")

# Artifact specs: (label, objectstore_prefix, max_legacy_shards)
ARTIFACT_SPECS: List[Tuple[str, str, int]] = [
    ("regime_decisions", "regime_observability", 64),
    ("regime_timeline", "regime_timeline_observability", 64),
    ("signal_lifecycle", "signal_lifecycle_observability", 64),
    ("router_rejections", "router_rejection_observability", 64),
    ("order_lifecycle", "order_lifecycle_observability", 64),
]

# ---------------------------------------------------------------------------
# QC API Client (auth identical to qc_push_individual.py)
# ---------------------------------------------------------------------------


def _load_credentials() -> Tuple[str, str]:
    """Load user_id and api_token from lean credentials file."""
    if not CREDENTIALS_FILE.exists():
        print(f"Error: Lean credentials not found at {CREDENTIALS_FILE}")
        print("Run `lean login` to authenticate with QuantConnect.")
        sys.exit(1)
    payload = json.loads(CREDENTIALS_FILE.read_text())
    return payload["user-id"], payload["api-token"]


def _make_auth(api_token: str) -> Tuple[str, dict]:
    """Return (digest, headers) for a QC REST request."""
    ts = str(int(time.time()))
    digest = hashlib.sha256(f"{api_token}:{ts}".encode()).hexdigest()
    return digest, {"Timestamp": ts}


def _qc_get(endpoint: str, params: dict, user_id: str, api_token: str) -> dict:
    digest, headers = _make_auth(api_token)
    resp = requests.get(
        f"{API_BASE}/{endpoint}",
        params=params,
        headers=headers,
        auth=(user_id, digest),
        timeout=60,
    )
    try:
        data = resp.json()
    except Exception:
        data = {}
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
    return data


def _qc_post(endpoint: str, body: dict, user_id: str, api_token: str) -> dict:
    digest, headers = _make_auth(api_token)
    headers["Content-Type"] = "application/json"
    resp = requests.post(
        f"{API_BASE}/{endpoint}",
        json=body,
        headers=headers,
        auth=(user_id, digest),
        timeout=60,
    )
    try:
        data = resp.json()
    except Exception:
        data = {}
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
    return data


# ---------------------------------------------------------------------------
# ObjectStore pull via lean CLI (preferred — handles auth transparently)
# ---------------------------------------------------------------------------


def _lean_available() -> bool:
    return subprocess.run(["lean", "--version"], capture_output=True, text=True).returncode == 0


def _lean_objectstore_get(key: str, dest_dir: Path) -> Optional[Path]:
    """Download a single ObjectStore key using the lean CLI.

    Returns the local file path on success, None if the key does not exist
    or the download fails.
    """
    result = subprocess.run(
        ["lean", "cloud", "object-store", "get", key, "--destination-folder", str(dest_dir)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    candidate = dest_dir / key
    if candidate.exists():
        return candidate
    # lean may strip the path to just the filename
    candidate2 = dest_dir / Path(key).name
    if candidate2.exists():
        return candidate2
    return None


# ---------------------------------------------------------------------------
# ObjectStore pull via QC REST API (fallback when lean CLI is unavailable)
# ---------------------------------------------------------------------------


def _api_objectstore_get(
    key: str,
    dest_dir: Path,
    project_id: int,
    user_id: str,
    api_token: str,
) -> Optional[Path]:
    """Download a single ObjectStore key via the QC REST API.

    Endpoint: GET /api/v2/object-store/read
    """
    try:
        data = _qc_get(
            "object-store/read",
            {"projectId": project_id, "key": key},
            user_id,
            api_token,
        )
    except Exception:
        return None

    # The API returns {success: bool, objectStore: [{key, content, ...}]}
    items = data.get("objectStore") or []
    if not items:
        return None

    content = items[0].get("content", "")
    if not content:
        return None

    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / Path(key).name
    out.write_text(content, encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# Key helpers (matching algorithm's key-builder logic)
# ---------------------------------------------------------------------------


def _safe_key_component(value: str, default: str = "run") -> str:
    text = str(value or "").strip()
    if not text:
        return default
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or default


def _candidate_keys(prefix: str, run_name: str, year: int) -> List[str]:
    """Generate candidate ObjectStore base keys in priority order.

    The algorithm saves as:  <prefix>__<run_suffix>_<year>.csv
    Multiple run_suffix forms are tried to handle label variations.
    """
    run_safe = _safe_key_component(run_name, "run")
    year_str = str(year)
    suffixes = [
        run_safe,
        f"year_{year_str}",
        "DEFAULT",
        "default",
    ]
    seen: set = set()
    keys: List[str] = []
    for s in suffixes:
        k = f"{prefix}__{s}_{year_str}.csv"
        if k not in seen:
            keys.append(k)
            seen.add(k)
    return keys


def _manifest_key(base_key: str) -> str:
    stem = base_key[:-4] if base_key.endswith(".csv") else base_key
    return f"{stem}__manifest.json"


def _part_key(base_key: str, idx: int) -> str:
    stem = base_key[:-4] if base_key.endswith(".csv") else base_key
    return f"{stem}__part{idx:03d}.csv"


# ---------------------------------------------------------------------------
# Merge sharded CSVs
# ---------------------------------------------------------------------------


def _merge_parts(part_paths: List[Path], target: Path) -> bool:
    """Concatenate sharded CSV parts into a single file (header from part 1)."""
    if not part_paths:
        return False
    wrote_header = False
    with open(target, "w", encoding="utf-8", newline="") as out:
        for part_path in part_paths:
            with open(part_path, "r", encoding="utf-8", errors="ignore") as src:
                lines = src.read().splitlines()
            if not lines:
                continue
            if not wrote_header:
                out.write("\n".join(lines) + "\n")
                wrote_header = True
            else:
                out.write("\n".join(lines[1:]) + "\n")
    return wrote_header


# ---------------------------------------------------------------------------
# Downloader orchestrator
# ---------------------------------------------------------------------------


def _download_key(
    key: str,
    tmp_dir: Path,
    use_lean: bool,
    project_id: int,
    user_id: str,
    api_token: str,
) -> Optional[Path]:
    """Try to download a single ObjectStore key. Returns local path or None."""
    if use_lean:
        result = _lean_objectstore_get(key, tmp_dir)
        if result is not None:
            return result
    # Fall back to REST API
    return _api_objectstore_get(key, tmp_dir, project_id, user_id, api_token)


def pull_artifact(
    label: str,
    prefix: str,
    max_shards: int,
    run_name: str,
    year: int,
    output_dir: Path,
    tmp_dir: Path,
    use_lean: bool,
    project_id: int,
    user_id: str,
    api_token: str,
    dry_run: bool = False,
) -> Optional[Path]:
    """Pull a single observability artifact.

    Handles single-file, manifest-sharded, and legacy blind-sharded payloads.
    Returns the merged local CSV path on success, None if not found.
    """
    run_safe = _safe_key_component(run_name, "run")
    target_path = output_dir / f"{run_safe}_{label}.csv"

    for base_key in _candidate_keys(prefix, run_name, year):
        if dry_run:
            print(f"  [DRY-RUN] Would try key: {base_key}")
            continue

        # --- Try single-file first ---
        single = _download_key(base_key, tmp_dir, use_lean, project_id, user_id, api_token)
        if single is not None:
            if single != target_path:
                if target_path.exists():
                    target_path.unlink()
                single.rename(target_path)
            return target_path

        # --- Try manifest-based sharding ---
        manifest_file = _download_key(
            _manifest_key(base_key), tmp_dir, use_lean, project_id, user_id, api_token
        )
        if manifest_file is not None:
            try:
                manifest_data = json.loads(manifest_file.read_text())
                expected_parts = int(manifest_data.get("parts", 0))
            except Exception:
                expected_parts = 0

            if expected_parts > 0:
                parts: List[Path] = []
                ok = True
                for idx in range(1, expected_parts + 1):
                    part = _download_key(
                        _part_key(base_key, idx),
                        tmp_dir,
                        use_lean,
                        project_id,
                        user_id,
                        api_token,
                    )
                    if part is None:
                        ok = False
                        break
                    parts.append(part)
                if ok and parts:
                    if _merge_parts(parts, target_path):
                        # Clean up temp parts and manifest
                        for p in parts:
                            if p.exists():
                                p.unlink()
                        if manifest_file.exists():
                            manifest_file.unlink()
                        return target_path

        # --- Blind shard probe (legacy runs without manifest) ---
        blind_parts: List[Path] = []
        for idx in range(1, max_shards + 1):
            part = _download_key(
                _part_key(base_key, idx),
                tmp_dir,
                use_lean,
                project_id,
                user_id,
                api_token,
            )
            if part is None:
                break
            blind_parts.append(part)

        if blind_parts:
            if _merge_parts(blind_parts, target_path):
                for p in blind_parts:
                    if p.exists():
                        p.unlink()
                return target_path

    return None


# ---------------------------------------------------------------------------
# Crosscheck markdown generator
# ---------------------------------------------------------------------------


def _count_csv_rows(path: Optional[Path]) -> int:
    """Return row count (excluding header) of a CSV, or -1 on error."""
    if path is None or not path.exists():
        return -1
    try:
        with open(path, newline="", encoding="utf-8", errors="ignore") as f:
            reader = csv.reader(f)
            rows = sum(1 for _ in reader) - 1  # subtract header
        return max(0, rows)
    except Exception:
        return -1


def _summarize_signal_lifecycle(path: Optional[Path]) -> str:
    """Generate a textual summary of signal_lifecycle.csv."""
    if path is None or not path.exists():
        return "_No data available._"
    try:
        from collections import Counter

        events: Counter = Counter()
        engines: Counter = Counter()
        codes: Counter = Counter()
        directions: Counter = Counter()
        with open(path, newline="", encoding="utf-8", errors="ignore") as f:
            reader = csv.DictReader(f)
            for row in reader:
                events[row.get("event", "UNKNOWN")] += 1
                engines[row.get("engine", "UNKNOWN")] += 1
                if row.get("code"):
                    codes[row.get("code", "")] += 1
                if row.get("direction"):
                    directions[row.get("direction", "")] += 1

        lines = ["**Event counts:**"]
        for k, v in sorted(events.items(), key=lambda x: -x[1]):
            lines.append(f"- `{k}`: {v:,}")
        lines.append("")
        lines.append("**By engine:**")
        for k, v in sorted(engines.items(), key=lambda x: -x[1]):
            lines.append(f"- `{k}`: {v:,}")
        lines.append("")
        lines.append("**Top drop/reject codes:**")
        for k, v in codes.most_common(10):
            lines.append(f"- `{k}`: {v:,}")
        if directions:
            lines.append("")
            lines.append("**By direction:**")
            for k, v in sorted(directions.items(), key=lambda x: -x[1]):
                lines.append(f"- `{k}`: {v:,}")
        return "\n".join(lines)
    except Exception as exc:
        return f"_Error summarizing: {exc}_"


def _summarize_regime_timeline(path: Optional[Path]) -> str:
    """Generate a textual summary of regime_timeline.csv."""
    if path is None or not path.exists():
        return "_No data available._"
    try:
        from collections import Counter

        bases: Counter = Counter()
        overlays: Counter = Counter()
        score_sum = 0.0
        score_count = 0
        with open(path, newline="", encoding="utf-8", errors="ignore") as f:
            reader = csv.DictReader(f)
            for row in reader:
                bases[row.get("base_regime", "UNKNOWN")] += 1
                overlays[row.get("transition_overlay", "UNKNOWN")] += 1
                try:
                    score_sum += float(row.get("effective_score", 0))
                    score_count += 1
                except Exception:
                    pass

        avg_score = score_sum / score_count if score_count > 0 else 0.0
        lines = [f"**Average effective score:** {avg_score:.1f}", ""]
        lines.append("**Base regime distribution:**")
        for k, v in sorted(bases.items(), key=lambda x: -x[1]):
            pct = 100.0 * v / score_count if score_count > 0 else 0.0
            lines.append(f"- `{k}`: {v:,} rows ({pct:.1f}%)")
        lines.append("")
        lines.append("**Transition overlay distribution:**")
        for k, v in sorted(overlays.items(), key=lambda x: -x[1]):
            pct = 100.0 * v / score_count if score_count > 0 else 0.0
            lines.append(f"- `{k}`: {v:,} rows ({pct:.1f}%)")
        return "\n".join(lines)
    except Exception as exc:
        return f"_Error summarizing: {exc}_"


def _summarize_router_rejections(path: Optional[Path]) -> str:
    """Generate a textual summary of router_rejections.csv."""
    if path is None or not path.exists():
        return "_No data available._"
    try:
        from collections import Counter

        codes: Counter = Counter()
        stages: Counter = Counter()
        engines: Counter = Counter()
        total = 0
        with open(path, newline="", encoding="utf-8", errors="ignore") as f:
            reader = csv.DictReader(f)
            for row in reader:
                total += 1
                codes[row.get("code", "UNKNOWN")] += 1
                stages[row.get("stage", "UNKNOWN")] += 1
                engines[row.get("engine", "UNKNOWN")] += 1

        lines = [f"**Total rejections:** {total:,}", ""]
        lines.append("**Top rejection codes:**")
        for k, v in codes.most_common(10):
            lines.append(f"- `{k}`: {v:,}")
        lines.append("")
        lines.append("**By stage:**")
        for k, v in sorted(stages.items(), key=lambda x: -x[1]):
            lines.append(f"- `{k}`: {v:,}")
        lines.append("")
        lines.append("**By engine:**")
        for k, v in sorted(engines.items(), key=lambda x: -x[1]):
            lines.append(f"- `{k}`: {v:,}")
        return "\n".join(lines)
    except Exception as exc:
        return f"_Error summarizing: {exc}_"


def _summarize_order_lifecycle(path: Optional[Path]) -> str:
    """Generate a textual summary of order_lifecycle.csv."""
    if path is None or not path.exists():
        return "_No data available._"
    try:
        from collections import Counter

        statuses: Counter = Counter()
        sources: Counter = Counter()
        total = 0
        with open(path, newline="", encoding="utf-8", errors="ignore") as f:
            reader = csv.DictReader(f)
            for row in reader:
                total += 1
                statuses[row.get("status", "UNKNOWN")] += 1
                sources[row.get("source", "UNKNOWN")] += 1

        lines = [f"**Total order lifecycle events:** {total:,}", ""]
        lines.append("**By status:**")
        for k, v in sorted(statuses.items(), key=lambda x: -x[1]):
            lines.append(f"- `{k}`: {v:,}")
        lines.append("")
        lines.append("**By source:**")
        for k, v in sources.most_common(10):
            lines.append(f"- `{k}`: {v:,}")
        return "\n".join(lines)
    except Exception as exc:
        return f"_Error summarizing: {exc}_"


def _summarize_regime_decisions(path: Optional[Path]) -> str:
    """Generate a textual summary of regime_decisions.csv."""
    if path is None or not path.exists():
        return "_No data available._"
    try:
        from collections import Counter

        engines: Counter = Counter()
        decisions: Counter = Counter()
        gates: Counter = Counter()
        total = 0
        with open(path, newline="", encoding="utf-8", errors="ignore") as f:
            reader = csv.DictReader(f)
            for row in reader:
                total += 1
                engines[row.get("engine", "UNKNOWN")] += 1
                decisions[row.get("engine_decision", "UNKNOWN")] += 1
                if row.get("gate_name"):
                    gates[row.get("gate_name", "")] += 1

        lines = [f"**Total regime decision events:** {total:,}", ""]
        lines.append("**By engine:**")
        for k, v in sorted(engines.items(), key=lambda x: -x[1]):
            lines.append(f"- `{k}`: {v:,}")
        lines.append("")
        lines.append("**Top gate decisions:**")
        for k, v in decisions.most_common(10):
            lines.append(f"- `{k}`: {v:,}")
        if gates:
            lines.append("")
            lines.append("**Gates fired:**")
            for k, v in gates.most_common(10):
                lines.append(f"- `{k}`: {v:,}")
        return "\n".join(lines)
    except Exception as exc:
        return f"_Error summarizing: {exc}_"


def generate_crosscheck_file(
    run_name: str,
    year: int,
    output_dir: Path,
    artifact_paths: Dict[str, Optional[Path]],
) -> Path:
    """Write the *_OBJECTSTORE_CROSSCHECK.md file for the log-analyzer agent."""
    run_safe = _safe_key_component(run_name, "run")
    crosscheck_path = output_dir / f"{run_safe}_OBJECTSTORE_CROSSCHECK.md"

    label_order = [
        "regime_decisions",
        "regime_timeline",
        "signal_lifecycle",
        "router_rejections",
        "order_lifecycle",
    ]

    # Artifact status block
    status_lines = []
    for label in label_order:
        path = artifact_paths.get(label)
        row_count = _count_csv_rows(path)
        if path is not None and path.exists() and row_count >= 0:
            status = f"[READY] {path.name} ({row_count:,} rows)"
        else:
            status = "[MISSING]"
        status_lines.append(f"- `{label}`: {status}")

    # Summary blocks
    summaries = {
        "regime_decisions": _summarize_regime_decisions(artifact_paths.get("regime_decisions")),
        "regime_timeline": _summarize_regime_timeline(artifact_paths.get("regime_timeline")),
        "signal_lifecycle": _summarize_signal_lifecycle(artifact_paths.get("signal_lifecycle")),
        "router_rejections": _summarize_router_rejections(artifact_paths.get("router_rejections")),
        "order_lifecycle": _summarize_order_lifecycle(artifact_paths.get("order_lifecycle")),
    }

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    content = f"""# ObjectStore Crosscheck: {run_name}

**Generated:** {now}
**Run Name:** {run_name}
**Backtest Year:** {year}
**Stage Dir:** {output_dir}

---

## Artifact Status

{chr(10).join(status_lines)}

---

## regime_decisions

### Status
{"[READY]" if artifact_paths.get("regime_decisions") else "[MISSING]"}

### Summary
{summaries["regime_decisions"]}

---

## regime_timeline

### Status
{"[READY]" if artifact_paths.get("regime_timeline") else "[MISSING]"}

### Summary
{summaries["regime_timeline"]}

---

## signal_lifecycle

### Status
{"[READY]" if artifact_paths.get("signal_lifecycle") else "[MISSING]"}

### Summary
{summaries["signal_lifecycle"]}

---

## router_rejections

### Status
{"[READY]" if artifact_paths.get("router_rejections") else "[MISSING]"}

### Summary
{summaries["router_rejections"]}

---

## order_lifecycle

### Status
{"[READY]" if artifact_paths.get("order_lifecycle") else "[MISSING]"}

### Summary
{summaries["order_lifecycle"]}

---

## Usage Notes

This file is the required input for the `log-analyzer` agent. The agent reads
this file first (hard gate) before producing performance and signal flow reports.

CSV artifacts are co-located in this directory:
```
{output_dir}/
  {run_safe}_signal_lifecycle.csv
  {run_safe}_regime_decisions.csv
  {run_safe}_regime_timeline.csv
  {run_safe}_router_rejections.csv
  {run_safe}_order_lifecycle.csv
```

To regenerate this file after re-pulling artifacts:
```bash
python scripts/pull_objectstore.py "{run_name}" --stage {output_dir.name} --crosscheck-only
```
"""

    crosscheck_path.write_text(content, encoding="utf-8")
    return crosscheck_path


# ---------------------------------------------------------------------------
# Backtest discovery
# ---------------------------------------------------------------------------


def list_backtests(user_id: str, api_token: str, project_id: int, limit: int = 20) -> List[dict]:
    data = _qc_get("backtests/read", {"projectId": project_id}, user_id, api_token)
    return (data.get("backtests") or [])[:limit]


def find_backtest(name: str, user_id: str, api_token: str, project_id: int) -> Optional[dict]:
    backtests = list_backtests(user_id, api_token, project_id, limit=100)
    # Exact match first
    for bt in backtests:
        if bt.get("name", "").lower() == name.lower():
            return bt
    # Partial match
    matches = [bt for bt in backtests if name.lower() in bt.get("name", "").lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        print(f"Multiple matches for '{name}':")
        for i, bt in enumerate(matches[:10], 1):
            print(f"  {i}. {bt.get('name')} ({bt.get('status')})")
        print("Use a more specific name or pass --id.")
        return None
    print(f"No backtest found matching '{name}'")
    return None


def infer_year(run_name: str, backtest: Optional[dict] = None) -> int:
    m = re.search(r"(?:FullYear|full[_-]?year)[_-]?(\d{4})", run_name, re.IGNORECASE)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d{4})", run_name)
    if m:
        return int(m.group(1))
    if backtest:
        start = str(backtest.get("backtestStart", "") or "")
        if re.match(r"^\d{4}", start):
            return int(start[:4])
    return datetime.now().year


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pull ObjectStore observability artifacts from a QC backtest",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("name", nargs="?", help="Backtest name (partial match)")
    parser.add_argument("--id", dest="backtest_id", help="Exact backtest ID")
    parser.add_argument("--run", help="Run name override for key construction")
    parser.add_argument(
        "--stage",
        required=True,
        help="Stage directory name under docs/audits/logs/ (e.g. stage12.21)",
    )
    parser.add_argument("--year", type=int, help="Override backtest year for key lookup")
    parser.add_argument("--project", type=int, default=PROJECT_ID, help="QC project ID")
    parser.add_argument(
        "--crosscheck-only",
        action="store_true",
        help="Re-generate crosscheck file from already-downloaded CSVs (no API calls)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print keys without downloading")
    parser.add_argument("--list", action="store_true", help="List recent backtests")
    parser.add_argument("--limit", type=int, default=20, help="Max backtests to list")
    parser.add_argument(
        "--no-lean", action="store_true", help="Skip lean CLI, use REST API directly"
    )
    args = parser.parse_args()

    user_id, api_token = _load_credentials()
    output_dir = LOGS_DIR / args.stage
    output_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = output_dir / ".pull_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    if args.list:
        backtests = list_backtests(user_id, api_token, args.project, args.limit)
        print(f"Recent backtests for project {args.project}:\n")
        for i, bt in enumerate(backtests, 1):
            status_icon = {"Completed.": "[OK]", "In Progress...": "[..]"}.get(
                bt.get("status", ""), "[??]"
            )
            print(f"  {i:2d}. {status_icon} {bt.get('name')}")
            print(f"       ID: {bt.get('backtestId')} | {bt.get('created')}")
        return 0

    # Determine run name
    backtest: Optional[dict] = None
    if not args.crosscheck_only:
        if args.backtest_id:
            data = _qc_get(
                "backtests/read",
                {"projectId": args.project, "backtestId": args.backtest_id},
                user_id,
                api_token,
            )
            backtest = data.get("backtest")
        elif args.name:
            backtest = find_backtest(args.name, user_id, api_token, args.project)
            if backtest is None:
                return 1
        elif not args.run:
            parser.error("Provide a backtest name, --id, or --run with --crosscheck-only")

    run_name = args.run or (backtest.get("name") if backtest else "") or args.name or ""
    if not run_name:
        parser.error("Could not determine run name. Pass --run explicitly.")

    year = args.year or infer_year(run_name, backtest)
    run_safe = _safe_key_component(run_name, "run")
    use_lean = not args.no_lean and _lean_available()

    if not use_lean and not args.crosscheck_only:
        print("Note: lean CLI not available or --no-lean set. Using REST API fallback.")

    print(f"\nRun name : {run_name}")
    print(f"Year     : {year}")
    print(f"Output   : {output_dir}")
    print(f"Lean CLI : {'yes' if use_lean else 'no (REST API fallback)'}")
    print()

    artifact_paths: Dict[str, Optional[Path]] = {}

    if args.crosscheck_only:
        # Just discover existing CSVs
        for label, _, _ in ARTIFACT_SPECS:
            candidate = output_dir / f"{run_safe}_{label}.csv"
            artifact_paths[label] = candidate if candidate.exists() else None
            status = "found" if artifact_paths[label] else "missing"
            print(f"  {label}: {status}")
    else:
        # Pull each artifact
        for label, prefix, max_shards in ARTIFACT_SPECS:
            print(f"Fetching {label}...")
            path = pull_artifact(
                label=label,
                prefix=prefix,
                max_shards=max_shards,
                run_name=run_name,
                year=year,
                output_dir=output_dir,
                tmp_dir=tmp_dir,
                use_lean=use_lean,
                project_id=args.project,
                user_id=user_id,
                api_token=api_token,
                dry_run=args.dry_run,
            )
            artifact_paths[label] = path
            if path is not None:
                rows = _count_csv_rows(path)
                print(f"  Saved: {path.name} ({rows:,} rows)")
            else:
                print(f"  Not found (key variants exhausted)")

    # Clean up tmp dir
    try:
        if tmp_dir.exists() and not any(tmp_dir.iterdir()):
            tmp_dir.rmdir()
    except Exception:
        pass

    if args.dry_run:
        print("\nDry-run complete. No files downloaded.")
        return 0

    # Generate crosscheck file
    print(f"\nGenerating crosscheck file...")
    crosscheck = generate_crosscheck_file(run_name, year, output_dir, artifact_paths)
    print(f"  Saved: {crosscheck}")

    # Summary
    ready = sum(1 for v in artifact_paths.values() if v is not None)
    total = len(artifact_paths)
    print(f"\nArtifacts ready: {ready}/{total}")
    if ready < total:
        missing = [k for k, v in artifact_paths.items() if v is None]
        print(f"Missing: {', '.join(missing)}")
        print("Tip: Verify RUN_NAME matches the run_label used during the backtest.")
        print("     Try the --year flag if year inference is incorrect.")

    return 0 if ready > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
