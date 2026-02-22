#!/usr/bin/env python3
"""
Fallback QC push: upload files one-by-one via files/update.

Used when `lean cloud push` fails with HTTP 413 due to payload size.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable, List, Tuple

DEFAULT_PROJECT_ID = 27678023
CREDENTIALS_FILE = Path.home() / ".lean" / "credentials"
API_BASE = "https://www.quantconnect.com/api/v2"


def load_credentials() -> Tuple[str, str]:
    if not CREDENTIALS_FILE.exists():
        raise FileNotFoundError(f"Lean credentials not found: {CREDENTIALS_FILE}")
    payload = json.loads(CREDENTIALS_FILE.read_text())
    return payload["user-id"], payload["api-token"]


def make_auth_headers(api_token: str) -> Tuple[str, dict]:
    ts = str(int(time.time()))
    digest = hashlib.sha256(f"{api_token}:{ts}".encode("utf-8")).hexdigest()
    return digest, {"Timestamp": ts, "Content-Type": "application/json"}


def qc_post(
    endpoint: str,
    body: dict,
    user_id: str,
    api_token: str,
) -> dict:
    digest, headers = make_auth_headers(api_token)
    req = urllib.request.Request(
        url=f"{API_BASE}/{endpoint}",
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    password_mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    password_mgr.add_password(None, f"{API_BASE}/", user_id, digest)
    auth_handler = urllib.request.HTTPBasicAuthHandler(password_mgr)
    opener = urllib.request.build_opener(auth_handler)
    try:
        with opener.open(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"HTTP {err.code}: {detail}") from err


def discover_files(workspace: Path) -> List[Path]:
    files = sorted(workspace.rglob("*.py"))
    cfg = workspace / "config.json"
    if cfg.exists():
        files.append(cfg)
    return files


def push_file(
    workspace: Path,
    path: Path,
    project_id: int,
    user_id: str,
    api_token: str,
) -> Tuple[bool, str]:
    rel = path.relative_to(workspace).as_posix()
    content = path.read_text(encoding="utf-8")
    result = qc_post(
        "files/update",
        {"projectId": project_id, "name": rel, "content": content},
        user_id=user_id,
        api_token=api_token,
    )
    if result.get("success", False):
        return True, f"OK ({len(content)/1024:.1f} KB)"
    return False, f"FAIL ({result.get('errors', 'unknown error')})"


def main(argv: Iterable[str]) -> int:
    parser = argparse.ArgumentParser(description="Push QC files individually via API")
    parser.add_argument("--workspace", required=True, help="Path to LEAN workspace project dir")
    parser.add_argument("--project-id", type=int, default=DEFAULT_PROJECT_ID)
    args = parser.parse_args(list(argv))

    workspace = Path(args.workspace).resolve()
    if not workspace.exists():
        print(f"Workspace does not exist: {workspace}", file=sys.stderr)
        return 2

    try:
        user_id, api_token = load_credentials()
    except Exception as err:
        print(f"Credential error: {err}", file=sys.stderr)
        return 2

    files = discover_files(workspace)
    if not files:
        print("No files found to push.", file=sys.stderr)
        return 2

    print(f"Pushing {len(files)} files individually to project {args.project_id}...")
    ok_count = 0
    fail_count = 0
    for path in files:
        try:
            ok, status = push_file(
                workspace=workspace,
                path=path,
                project_id=args.project_id,
                user_id=user_id,
                api_token=api_token,
            )
        except Exception as err:
            ok = False
            status = f"FAIL ({err})"
        rel = path.relative_to(workspace).as_posix()
        print(f"  {rel}: {status}")
        if ok:
            ok_count += 1
        else:
            fail_count += 1

    print(f"Summary: {ok_count} succeeded, {fail_count} failed")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
