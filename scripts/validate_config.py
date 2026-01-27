#!/usr/bin/env python3
"""
Validate config.py against spec documentation.

This script checks that parameter values in config.py match the values
documented in the specification files (docs/*.md).

Usage:
    python scripts/validate_config.py

Returns:
    Exit code 0 if all parameters match
    Exit code 1 if mismatches found
"""

import re
import sys
from pathlib import Path


def extract_spec_params(spec_file: Path) -> dict:
    """
    Extract parameters mentioned in spec file.

    Looks for patterns like:
        - `KILL_SWITCH_PCT` = 0.03
        - `KILL_SWITCH_PCT`: 0.03
        - | `KILL_SWITCH_PCT` | 0.03 |

    Args:
        spec_file: Path to markdown spec file

    Returns:
        Dictionary of parameter name -> expected value (as string)
    """
    content = spec_file.read_text()
    params = {}

    # Pattern 1: `PARAM_NAME` = value or `PARAM_NAME`: value
    pattern1 = re.findall(r'`([A-Z][A-Z0-9_]+)`\s*[=:]\s*([\d.]+)', content)
    for name, value in pattern1:
        params[name] = value

    # Pattern 2: Table format | `PARAM_NAME` | value |
    pattern2 = re.findall(r'\|\s*`?([A-Z][A-Z0-9_]+)`?\s*\|\s*([\d.]+%?)\s*\|', content)
    for name, value in pattern2:
        # Remove % suffix if present and convert
        clean_value = value.rstrip('%')
        if '%' in value:
            # Convert percentage to decimal
            try:
                clean_value = str(float(clean_value) / 100)
            except ValueError:
                clean_value = value
        params[name] = clean_value

    return params


def load_config_params() -> dict:
    """
    Load parameters from config.py.

    Returns:
        Dictionary of parameter name -> actual value
    """
    # Add project root to path
    project_root = Path(__file__).parent.parent
    sys.path.insert(0, str(project_root))

    try:
        import config

        # Get all uppercase attributes (parameters)
        params = {}
        for name in dir(config):
            if name.isupper() and not name.startswith('_'):
                params[name] = getattr(config, name)
        return params
    except ImportError as e:
        print(f"ERROR: Could not import config.py: {e}")
        return {}


def validate_config() -> list:
    """
    Compare config.py against all spec files.

    Returns:
        List of mismatch dictionaries with keys: file, param, expected, actual
    """
    project_root = Path(__file__).parent.parent
    spec_dir = project_root / "docs"

    if not spec_dir.exists():
        print(f"ERROR: docs/ directory not found at {spec_dir}")
        return []

    config_params = load_config_params()
    if not config_params:
        print("WARNING: No parameters found in config.py (may not be implemented yet)")
        return []

    mismatches = []
    checked_params = set()

    for spec_file in sorted(spec_dir.glob("*.md")):
        spec_params = extract_spec_params(spec_file)

        for param, expected in spec_params.items():
            if param in checked_params:
                continue  # Already checked from another file
            checked_params.add(param)

            if param in config_params:
                actual = config_params[param]

                # Skip validation for complex types (dicts, lists)
                # These are phase-specific or structured parameters that can't
                # be easily compared against markdown table values
                if isinstance(actual, (dict, list)):
                    continue

                # Compare as strings (handle float precision)
                try:
                    if abs(float(actual) - float(expected)) > 0.0001:
                        mismatches.append({
                            "file": spec_file.name,
                            "param": param,
                            "expected": expected,
                            "actual": actual
                        })
                except (ValueError, TypeError):
                    # Non-numeric comparison
                    if str(actual) != expected:
                        mismatches.append({
                            "file": spec_file.name,
                            "param": param,
                            "expected": expected,
                            "actual": actual
                        })

    return mismatches


def main():
    """Main entry point."""
    print("=" * 60)
    print("Alpha NextGen - Config Validation")
    print("=" * 60)
    print()

    mismatches = validate_config()

    if mismatches:
        print("MISMATCHES FOUND:")
        print("-" * 60)
        for m in mismatches:
            print(f"  File: {m['file']}")
            print(f"  Param: {m['param']}")
            print(f"  Expected: {m['expected']}")
            print(f"  Actual: {m['actual']}")
            print()
        print(f"Total mismatches: {len(mismatches)}")
        sys.exit(1)
    else:
        print("All parameters match!")
        print("(Or config.py not yet implemented)")
        sys.exit(0)


if __name__ == "__main__":
    main()
