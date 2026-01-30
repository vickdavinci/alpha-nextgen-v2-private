#!/usr/bin/env python
"""
Pre-QC Checklist Runner.

Automated validation before deploying to QuantConnect.
Runs all local tests to catch issues before using precious QC backtests.

Usage:
    python scripts/pre_qc_checklist.py

Exit codes:
    0: All checks passed
    1: One or more checks failed
"""

import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple


# ANSI color codes for terminal output
class Colors:
    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    WARNING = "\033[93m"
    FAIL = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"


def print_header(text: str) -> None:
    """Print a section header."""
    print(f"\n{Colors.HEADER}{'=' * 60}{Colors.ENDC}")
    print(f"{Colors.BOLD}{text}{Colors.ENDC}")
    print(f"{Colors.HEADER}{'=' * 60}{Colors.ENDC}")


def print_status(name: str, passed: bool, duration: float) -> None:
    """Print check status."""
    status = f"{Colors.GREEN}PASSED{Colors.ENDC}" if passed else f"{Colors.FAIL}FAILED{Colors.ENDC}"
    print(f"  {name:<40} [{status}] ({duration:.1f}s)")


def run_command(cmd: str, timeout: int = 300) -> Tuple[bool, str, float]:
    """
    Run a shell command and return (success, output, duration).

    Args:
        cmd: Command to run
        timeout: Timeout in seconds

    Returns:
        Tuple of (passed, output, duration_seconds)
    """
    start = time.time()
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        duration = time.time() - start
        passed = result.returncode == 0
        output = result.stdout + result.stderr
        return passed, output, duration
    except subprocess.TimeoutExpired:
        duration = time.time() - start
        return False, f"Command timed out after {timeout}s", duration
    except Exception as e:
        duration = time.time() - start
        return False, str(e), duration


def check_unit_tests() -> Tuple[bool, float]:
    """Run unit tests."""
    passed, output, duration = run_command(
        "python -m pytest tests/ -x --ignore=tests/integration --ignore=tests/scenarios -q"
    )
    return passed, duration


def check_integration_tests() -> Tuple[bool, float]:
    """Run integration tests."""
    passed, output, duration = run_command("python -m pytest tests/integration/ -x -q")
    return passed, duration


def check_scenario_tests() -> Tuple[bool, float]:
    """Run scenario tests."""
    passed, output, duration = run_command("python -m pytest tests/scenarios/ -x -q")
    return passed, duration


def check_log_budget_tests() -> Tuple[bool, float]:
    """Run log budget tests."""
    passed, output, duration = run_command("python -m pytest tests/test_log_budget.py -v -q")
    return passed, duration


def check_qc_simulation_tests() -> Tuple[bool, float]:
    """Run QC simulation tests."""
    passed, output, duration = run_command(
        "python -m pytest tests/integration/test_qc_simulation.py -v -q"
    )
    return passed, duration


def check_data_validation_tests() -> Tuple[bool, float]:
    """Run data validation tests."""
    passed, output, duration = run_command(
        "python -m pytest tests/integration/test_data_validation.py -v -q"
    )
    return passed, duration


def check_config_validation() -> Tuple[bool, float]:
    """Run config validation script."""
    passed, output, duration = run_command("python scripts/validate_config.py")
    return passed, duration


def check_no_print_statements() -> Tuple[bool, float]:
    """Check for print statements in production code."""
    start = time.time()
    try:
        result = subprocess.run(
            "grep -r 'print(' engines/ main.py --include='*.py' | wc -l",
            shell=True,
            capture_output=True,
            text=True,
        )
        count = int(result.stdout.strip())
        duration = time.time() - start
        # Allow 0 print statements
        passed = count == 0
        return passed, duration
    except Exception:
        return False, time.time() - start


def check_no_datetime_now() -> Tuple[bool, float]:
    """Check for datetime.now() usage (should use self.Time)."""
    start = time.time()
    try:
        result = subprocess.run(
            "grep -r 'datetime.now()' engines/ main.py --include='*.py' | wc -l",
            shell=True,
            capture_output=True,
            text=True,
        )
        count = int(result.stdout.strip())
        duration = time.time() - start
        # Allow 0 datetime.now() calls
        passed = count == 0
        return passed, duration
    except Exception:
        return False, time.time() - start


def check_imports() -> Tuple[bool, float]:
    """Verify config and engine imports work.

    Note: main.py requires QCAlgorithm which is only available in QC environment.
    We check config.py and engine modules that can be imported locally.
    """
    start = time.time()
    try:
        # main.py can't be imported locally (requires QCAlgorithm)
        # Check config and models which work locally
        result = subprocess.run(
            "python -c 'import config; from models.enums import Phase, Urgency; print(\"Imports OK\")'",
            shell=True,
            capture_output=True,
            text=True,
        )
        duration = time.time() - start
        passed = result.returncode == 0
        return passed, duration
    except Exception:
        return False, time.time() - start


def check_syntax() -> Tuple[bool, float]:
    """Run Python syntax check."""
    start = time.time()
    try:
        result = subprocess.run(
            "python -m py_compile main.py",
            shell=True,
            capture_output=True,
            text=True,
        )
        duration = time.time() - start
        passed = result.returncode == 0
        return passed, duration
    except Exception:
        return False, time.time() - start


def main() -> int:
    """Run all pre-QC checks."""
    print(f"\n{Colors.CYAN}Pre-QC Checklist Runner{Colors.ENDC}")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # Define all checks
    checks = [
        ("Syntax Check", check_syntax),
        ("Import Validation", check_imports),
        ("Config Validation", check_config_validation),
        ("No print() Statements", check_no_print_statements),
        ("No datetime.now() Usage", check_no_datetime_now),
        ("Unit Tests", check_unit_tests),
        ("Integration Tests", check_integration_tests),
        ("Scenario Tests", check_scenario_tests),
        ("Log Budget Tests", check_log_budget_tests),
        ("QC Simulation Tests", check_qc_simulation_tests),
        ("Data Validation Tests", check_data_validation_tests),
    ]

    results: List[Tuple[str, bool, float]] = []
    total_start = time.time()

    print_header("Running Pre-QC Checks")

    for name, check_func in checks:
        print(f"  Running: {name}...", end="", flush=True)
        passed, duration = check_func()
        results.append((name, passed, duration))
        # Clear line and print status
        print(f"\r", end="")
        print_status(name, passed, duration)

        # Stop on first failure for quick feedback
        if not passed:
            print(f"\n{Colors.FAIL}STOPPING: {name} failed{Colors.ENDC}")
            break

    total_duration = time.time() - total_start

    # Summary
    print_header("Summary")

    passed_count = sum(1 for _, passed, _ in results if passed)
    total_count = len(results)
    all_passed = passed_count == total_count and total_count == len(checks)

    print(f"  Checks Run: {total_count}/{len(checks)}")
    print(f"  Passed: {passed_count}")
    print(f"  Failed: {total_count - passed_count}")
    print(f"  Duration: {total_duration:.1f}s")

    if all_passed:
        print(f"\n{Colors.GREEN}{'=' * 60}")
        print("ALL PRE-QC CHECKS PASSED!")
        print(f"{'=' * 60}{Colors.ENDC}")
        print("\nNext steps:")
        print("  1. Deploy to QC with Stage 1: 1-day backtest (Jan 2, 2024)")
        print("  2. Verify: No import errors, Initialize() completes")
        print("  3. Expected logs: < 10")
        return 0
    else:
        print(f"\n{Colors.FAIL}{'=' * 60}")
        print("PRE-QC CHECKS FAILED")
        print(f"{'=' * 60}{Colors.ENDC}")
        print("\nFailed checks:")
        for name, passed, _ in results:
            if not passed:
                print(f"  - {name}")
        print("\nFix issues before deploying to QC.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
