#!/usr/bin/env python3
"""
Check spec parity between code files and documentation.

This script warns developers if they modify a code file without updating
its corresponding spec document. It does NOT block builds (exit code 0).

Usage:
    python scripts/check_spec_parity.py

Environment:
    GITHUB_BASE_REF: Set by GitHub Actions for pull requests (target branch)

Returns:
    Always exits with code 0 (success) to avoid blocking CI.
    Warnings are printed to stdout for visibility in build logs.
"""

import os
import subprocess
import sys

# Mapping of code files to their corresponding spec documents
CODE_TO_SPEC_MAP = {
    # Core Engines
    'engines/regime_engine.py': 'docs/04-regime-engine.md',
    'engines/capital_engine.py': 'docs/05-capital-engine.md',
    'engines/cold_start_engine.py': 'docs/06-cold-start-engine.md',
    'engines/trend_engine.py': 'docs/07-trend-engine.md',
    'engines/mean_reversion_engine.py': 'docs/08-mean-reversion-engine.md',
    'engines/hedge_engine.py': 'docs/09-hedge-engine.md',
    'engines/yield_sleeve.py': 'docs/10-yield-sleeve.md',
    'engines/risk_engine.py': 'docs/12-risk-engine.md',

    # Portfolio Layer
    'portfolio/portfolio_router.py': 'docs/11-portfolio-router.md',

    # Execution Layer
    'execution/execution_engine.py': 'docs/13-execution-engine.md',

    # Persistence
    'persistence/state_manager.py': 'docs/15-state-persistence.md',

    # Configuration
    'config.py': 'docs/16-appendix-parameters.md',
}


def get_changed_files() -> list:
    """
    Get list of files changed in the current commit or PR.

    Returns:
        List of changed file paths relative to repo root.
    """
    try:
        # Check if we're in a PR context (GitHub Actions sets GITHUB_BASE_REF)
        base_ref = os.environ.get('GITHUB_BASE_REF')

        if base_ref:
            # PR mode: compare against target branch
            cmd = ['git', 'diff', '--name-only', f'origin/{base_ref}...HEAD']
        else:
            # Push mode: compare against previous commit
            cmd = ['git', 'diff', '--name-only', 'HEAD~1', 'HEAD']

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True
        )

        files = [f.strip() for f in result.stdout.strip().split('\n') if f.strip()]
        return files

    except subprocess.CalledProcessError as e:
        print(f"INFO: Could not get changed files: {e}")
        print("INFO: This may happen on initial commits or shallow clones.")
        return []
    except Exception as e:
        print(f"INFO: Unexpected error getting changed files: {e}")
        return []


def check_spec_parity(changed_files: list) -> list:
    """
    Check if code changes have corresponding spec updates.

    Args:
        changed_files: List of changed file paths.

    Returns:
        List of warning messages for mismatched files.
    """
    warnings = []
    changed_set = set(changed_files)

    for code_file, spec_file in CODE_TO_SPEC_MAP.items():
        if code_file in changed_set and spec_file not in changed_set:
            warnings.append(
                f"⚠️  SPEC MISMATCH WARNING: '{code_file}' was modified "
                f"but '{spec_file}' was not."
            )

    return warnings


def main():
    """Main entry point."""
    print("=" * 60)
    print("Spec Parity Check")
    print("=" * 60)
    print()

    # Get changed files
    changed_files = get_changed_files()

    if not changed_files:
        print("INFO: No changed files detected (or unable to determine changes).")
        print("INFO: Skipping spec parity check.")
        sys.exit(0)

    print(f"Changed files ({len(changed_files)}):")
    for f in changed_files[:20]:  # Limit display to first 20
        print(f"  - {f}")
    if len(changed_files) > 20:
        print(f"  ... and {len(changed_files) - 20} more")
    print()

    # Check for mismatches
    warnings = check_spec_parity(changed_files)

    if warnings:
        print("SPEC PARITY WARNINGS:")
        print("-" * 60)
        for warning in warnings:
            print(warning)
        print()
        print("ACTION: Please review if the spec document needs updating.")
        print("        If this is a bug fix with no behavior change, this warning")
        print("        can be safely ignored.")
    else:
        print("✅ All modified code files have corresponding spec updates (or are not in the map).")

    print()
    print("=" * 60)

    # Always exit success - this is a warning, not a blocker
    sys.exit(0)


if __name__ == "__main__":
    main()
