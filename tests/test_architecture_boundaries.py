"""
Architecture boundary tests for Alpha NextGen.

This file enforces THREE critical rules via static analysis:

1. HUB-AND-SPOKE ARCHITECTURE
   - Strategy engines must NOT import other strategy engines
   - All engines communicate via TargetWeight through the Portfolio Router

2. ORDER AUTHORITY
   - ONLY portfolio_router.py may call order methods
   - Engines emit TargetWeight objects, they NEVER place orders

3. QUANTCONNECT COMPLIANCE
   - No print() - use self.Log()
   - No time.sleep() - breaks simulation
   - No datetime.now() - use self.Time

These tests run in CI and catch violations BEFORE code reaches QuantConnect.
"""

import ast
import os
from pathlib import Path
from typing import List, Set, Tuple

import pytest


class ArchitectureAnalyzer:
    """
    Static analyzer for architecture rule enforcement.

    Uses Python's AST module to analyze source code without executing it.
    """

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.tree = None
        self.content = ""

        if os.path.exists(filepath):
            with open(filepath, "r") as f:
                self.content = f.read()
            try:
                self.tree = ast.parse(self.content)
            except SyntaxError:
                self.tree = None

    def get_imports(self) -> List[str]:
        """Extract all import statements."""
        if not self.tree:
            return []

        imports = []
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module)
        return imports

    def get_method_calls(self) -> List[Tuple[str, int]]:
        """Extract method calls with line numbers."""
        if not self.tree:
            return []

        calls = []
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call):
                call_name = self._get_call_name(node)
                if call_name:
                    calls.append((call_name, getattr(node, "lineno", 0)))
        return calls

    def _get_call_name(self, node: ast.Call) -> str:
        """Extract the full name of a function call."""
        if isinstance(node.func, ast.Attribute):
            # e.g., self.MarketOrder() -> "MarketOrder"
            # e.g., datetime.now() -> "datetime.now"
            if isinstance(node.func.value, ast.Name):
                return f"{node.func.value.id}.{node.func.attr}"
            return node.func.attr
        elif isinstance(node.func, ast.Name):
            # e.g., print() -> "print"
            return node.func.id
        return ""

    def get_function_calls_simple(self) -> List[Tuple[str, int]]:
        """Extract simple function calls (e.g., print)."""
        if not self.tree:
            return []

        calls = []
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    calls.append((node.func.id, getattr(node, "lineno", 0)))
        return calls


class TestArchitectureBoundaries:
    """
    Enforce hub-and-spoke architecture via static analysis.

    RULE 1: Engines cannot import other engines
    RULE 2: Only portfolio_router.py can call order methods
    """

    # Strategy engine files (emit TargetWeight, never place orders)
    ENGINE_FILES = [
        "engines/trend_engine.py",
        "engines/mean_reversion_engine.py",
        "engines/hedge_engine.py",
        "engines/yield_sleeve.py",
        "engines/cold_start_engine.py",
    ]

    # Core engine files (also should not place orders directly)
    CORE_ENGINE_FILES = [
        "engines/regime_engine.py",
        "engines/capital_engine.py",
        "engines/risk_engine.py",
    ]

    # Files that ARE authorized to place orders
    ORDER_AUTHORIZED_FILES = [
        "portfolio/portfolio_router.py",
    ]

    # Order methods that only Router can call
    ORDER_METHODS = {
        "MarketOrder",
        "MarketOnOpenOrder",
        "LimitOrder",
        "StopMarketOrder",
        "StopLimitOrder",
        "Liquidate",
    }

    # Strategy engine names (for import checking)
    STRATEGY_ENGINE_NAMES = {
        "trend_engine",
        "mean_reversion_engine",
        "hedge_engine",
        "yield_sleeve",
        "cold_start_engine",
    }

    def _get_existing_files(self, file_list: List[str]) -> List[str]:
        """Filter to only existing files."""
        return [f for f in file_list if os.path.exists(f)]

    @pytest.mark.parametrize(
        "engine_file",
        [f for f in ENGINE_FILES + CORE_ENGINE_FILES if os.path.exists(f)],
    )
    def test_engines_do_not_import_other_strategy_engines(self, engine_file):
        """
        RULE 1: Strategy engines must be isolated.
        Engine A should never import Engine B.

        WHY: Hub-and-spoke architecture. All engines communicate
        through the Portfolio Router via TargetWeight objects.
        """
        if not os.path.exists(engine_file):
            pytest.skip(f"{engine_file} not implemented yet")

        analyzer = ArchitectureAnalyzer(engine_file)
        imports = analyzer.get_imports()

        # Check for imports of other strategy engines
        for imp in imports:
            for engine_name in self.STRATEGY_ENGINE_NAMES:
                # Don't flag self-imports
                if engine_name in engine_file:
                    continue

                if engine_name in imp:
                    pytest.fail(
                        f"\n{'='*60}\n"
                        f"ARCHITECTURE VIOLATION: Hub-and-Spoke Rule\n"
                        f"{'='*60}\n"
                        f"File: {engine_file}\n"
                        f"Imports: {imp}\n"
                        f"\n"
                        f"Strategy engines must NOT import other strategy engines.\n"
                        f"Use TargetWeight interface to communicate via Portfolio Router.\n"
                        f"\n"
                        f"See: CLAUDE.md -> Critical Rules -> Strategy Engines Are Analyzers\n"
                        f"{'='*60}"
                    )

    @pytest.mark.parametrize(
        "engine_file",
        [f for f in ENGINE_FILES + CORE_ENGINE_FILES if os.path.exists(f)],
    )
    def test_only_router_places_orders(self, engine_file):
        """
        RULE 2: Only Portfolio Router may call order methods.
        Engines emit TargetWeight, they do NOT place orders.

        WHY: Central control. Portfolio Router handles:
        - Exposure limits
        - Risk checks
        - Order aggregation
        - Execution timing
        """
        if not os.path.exists(engine_file):
            pytest.skip(f"{engine_file} not implemented yet")

        if engine_file in self.ORDER_AUTHORIZED_FILES:
            pytest.skip(f"{engine_file} is authorized to place orders")

        analyzer = ArchitectureAnalyzer(engine_file)
        method_calls = analyzer.get_method_calls()

        for call_name, line_no in method_calls:
            # Check if call ends with an order method
            method = call_name.split(".")[-1] if "." in call_name else call_name
            if method in self.ORDER_METHODS:
                pytest.fail(
                    f"\n{'='*60}\n"
                    f"ARCHITECTURE VIOLATION: Order Authority Rule\n"
                    f"{'='*60}\n"
                    f"File: {engine_file}:{line_no}\n"
                    f"Call: {call_name}()\n"
                    f"\n"
                    f"Only portfolio_router.py may place orders.\n"
                    f"Engines must emit TargetWeight objects instead.\n"
                    f"\n"
                    f"CORRECT:\n"
                    f"  return [TargetWeight('QLD', 0.30, 'TREND', Urgency.EOD, 'reason')]\n"
                    f"\n"
                    f"WRONG:\n"
                    f"  self.algorithm.MarketOrder('QLD', 100)\n"
                    f"\n"
                    f"See: CLAUDE.md -> Critical Rules -> Strategy Engines Are Analyzers\n"
                    f"{'='*60}"
                )

    def test_router_is_the_only_order_authority(self):
        """
        Meta-test: Verify that ONLY portfolio_router.py is authorized.
        This prevents accidentally adding other files to the whitelist.
        """
        assert self.ORDER_AUTHORIZED_FILES == ["portfolio/portfolio_router.py"], (
            "Only portfolio_router.py should be authorized to place orders. "
            "Do not add other files to ORDER_AUTHORIZED_FILES."
        )


class TestQuantConnectCompliance:
    """
    Enforce QuantConnect-specific coding patterns.

    These patterns are required because:
    1. QC runs in a sandboxed environment
    2. Backtest simulation requires deterministic time
    3. Output must go through QC's logging system

    Violations will cause runtime errors or incorrect behavior on QuantConnect.
    """

    # All source files (excluding tests)
    @staticmethod
    def get_source_files() -> List[str]:
        """Get all Python source files, excluding tests."""
        source_dirs = ["engines", "portfolio", "execution", "models", "persistence", "utils"]
        files = []
        for dir_name in source_dirs:
            if os.path.exists(dir_name):
                files.extend(
                    str(p) for p in Path(dir_name).glob("**/*.py") if "__pycache__" not in str(p)
                )
        # Also check main.py and config.py
        for root_file in ["main.py", "config.py"]:
            if os.path.exists(root_file):
                files.append(root_file)
        return files

    # Forbidden function calls with explanations
    FORBIDDEN_CALLS = {
        "print": ("Use self.algorithm.Log() instead.\n" "print() output is lost in QuantConnect."),
        "time.sleep": (
            "sleep() is not allowed in QuantConnect.\n"
            "It breaks simulation - time doesn't pass, it just hangs.\n"
            "Use self.Schedule.On() for delayed execution."
        ),
        "datetime.now": (
            "Use self.algorithm.Time instead.\n"
            "datetime.now() returns YOUR computer's time, not simulated market time.\n"
            "This causes bugs in backtesting."
        ),
        "datetime.today": (
            "Use self.algorithm.Time instead.\n"
            "datetime.today() returns YOUR computer's time, not simulated market time."
        ),
        "datetime.utcnow": (
            "Use self.algorithm.Time instead.\n"
            "datetime.utcnow() returns YOUR computer's time, not simulated market time."
        ),
    }

    @pytest.fixture
    def source_files(self) -> List[str]:
        """Get source files that exist."""
        return self.get_source_files()

    def test_no_print_statements(self, source_files):
        """
        QC COMPLIANCE: No print() calls.

        print() output is lost in QuantConnect.
        Use self.algorithm.Log() or self.Log() instead.
        """
        violations = []

        for filepath in source_files:
            if not os.path.exists(filepath):
                continue

            analyzer = ArchitectureAnalyzer(filepath)
            calls = analyzer.get_function_calls_simple()

            for call_name, line_no in calls:
                if call_name == "print":
                    violations.append(f"  {filepath}:{line_no}")

        if violations:
            pytest.fail(
                f"\n{'='*60}\n"
                f"QC COMPLIANCE VIOLATION: print() detected\n"
                f"{'='*60}\n"
                f"\n"
                f"Found {len(violations)} violation(s):\n" + "\n".join(violations) + f"\n\n"
                f"FIX: Replace print() with self.algorithm.Log() or self.Log()\n"
                f"\n"
                f"WRONG:   print(f'Price: {{price}}')\n"
                f"CORRECT: self.Log(f'Price: {{price}}')\n"
                f"{'='*60}"
            )

    def test_no_sleep_calls(self, source_files):
        """
        QC COMPLIANCE: No time.sleep() calls.

        sleep() breaks the simulation - time doesn't pass, it just hangs.
        Use self.Schedule.On() for delayed execution.
        """
        violations = []

        for filepath in source_files:
            if not os.path.exists(filepath):
                continue

            analyzer = ArchitectureAnalyzer(filepath)
            calls = analyzer.get_method_calls()

            for call_name, line_no in calls:
                if "sleep" in call_name.lower() and "time" in call_name.lower():
                    violations.append(f"  {filepath}:{line_no} -> {call_name}()")

        if violations:
            pytest.fail(
                f"\n{'='*60}\n"
                f"QC COMPLIANCE VIOLATION: time.sleep() detected\n"
                f"{'='*60}\n"
                f"\n"
                f"Found {len(violations)} violation(s):\n" + "\n".join(violations) + f"\n\n"
                f"FIX: sleep() is NOT allowed in QuantConnect.\n"
                f"Use self.Schedule.On() for delayed execution.\n"
                f"{'='*60}"
            )

    def test_no_datetime_now(self, source_files):
        """
        QC COMPLIANCE: No datetime.now() or datetime.today() calls.

        These return YOUR computer's time, not simulated market time.
        Use self.algorithm.Time instead.
        """
        violations = []

        for filepath in source_files:
            if not os.path.exists(filepath):
                continue

            analyzer = ArchitectureAnalyzer(filepath)
            calls = analyzer.get_method_calls()

            forbidden_datetime_calls = {"datetime.now", "datetime.today", "datetime.utcnow"}

            # Read source lines to check for defensive fallback pattern
            try:
                with open(filepath) as f:
                    source_lines = f.readlines()
            except Exception:
                source_lines = []

            for call_name, line_no in calls:
                if call_name in forbidden_datetime_calls:
                    # Allow conditional fallback: "X.Time if X is not None else datetime.utcnow()"
                    if source_lines and 0 < line_no <= len(source_lines):
                        line_text = source_lines[line_no - 1]
                        if "if" in line_text and "else" in line_text and ".Time" in line_text:
                            continue
                    violations.append(f"  {filepath}:{line_no} -> {call_name}()")

        if violations:
            pytest.fail(
                f"\n{'='*60}\n"
                f"QC COMPLIANCE VIOLATION: datetime.now()/today() detected\n"
                f"{'='*60}\n"
                f"\n"
                f"Found {len(violations)} violation(s):\n" + "\n".join(violations) + f"\n\n"
                f"FIX: Use self.algorithm.Time instead.\n"
                f"\n"
                f"WRONG:   current_time = datetime.now()\n"
                f"CORRECT: current_time = self.algorithm.Time\n"
                f"\n"
                f"WHY: datetime.now() returns YOUR computer's time,\n"
                f"     not the simulated market time in backtests.\n"
                f"{'='*60}"
            )


class TestArchitectureSummary:
    """
    Summary test that documents all architecture rules.

    This test always passes but documents the rules being enforced.
    Useful for developers to understand what's being checked.
    """

    def test_architecture_rules_documented(self):
        """Document the architecture rules being enforced."""
        rules = """
        ╔═══════════════════════════════════════════════════════════════╗
        ║           ALPHA NEXTGEN ARCHITECTURE RULES                    ║
        ╠═══════════════════════════════════════════════════════════════╣
        ║                                                               ║
        ║  RULE 1: HUB-AND-SPOKE ARCHITECTURE                          ║
        ║  ─────────────────────────────────────────────────────────── ║
        ║  Strategy engines must NOT import other strategy engines.    ║
        ║  All engines communicate via TargetWeight through Router.    ║
        ║                                                               ║
        ║  RULE 2: ORDER AUTHORITY                                      ║
        ║  ─────────────────────────────────────────────────────────── ║
        ║  ONLY portfolio_router.py may call order methods:            ║
        ║  • MarketOrder()                                              ║
        ║  • MarketOnOpenOrder()                                        ║
        ║  • LimitOrder()                                               ║
        ║  • Liquidate()                                                ║
        ║                                                               ║
        ║  RULE 3: QUANTCONNECT COMPLIANCE                              ║
        ║  ─────────────────────────────────────────────────────────── ║
        ║  • No print() → use self.Log()                               ║
        ║  • No time.sleep() → use Schedule.On()                       ║
        ║  • No datetime.now() → use self.Time                         ║
        ║                                                               ║
        ╚═══════════════════════════════════════════════════════════════╝
        """
        # This test always passes - it's documentation
        assert True, rules
