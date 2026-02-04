#!/usr/bin/env python3
"""Strip comments and docstrings from Python files in lean workspace to reduce upload size."""

import ast
import os
import sys


def strip_comments_and_docstrings(source: str) -> str:
    """Remove comments and docstrings while preserving functional code."""
    # Step 1: Remove docstrings via AST
    try:
        tree = ast.parse(source)
    except SyntaxError:
        # If we can't parse, just strip comments
        return strip_comments_only(source)

    lines = source.splitlines(keepends=True)

    # Find all docstring line ranges
    docstring_lines = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, (ast.Constant,))
                and isinstance(node.body[0].value.value, str)
            ):
                ds_node = node.body[0]
                if hasattr(ds_node, "lineno") and hasattr(ds_node, "end_lineno"):
                    for i in range(ds_node.lineno - 1, ds_node.end_lineno):
                        docstring_lines.add(i)

    # Step 2: Process lines - remove docstrings and comments
    result = []
    for i, line in enumerate(lines):
        if i in docstring_lines:
            # Replace docstring lines with empty line to preserve line numbers for debugging
            stripped = line.rstrip()
            if stripped:
                result.append("\n")
            else:
                result.append(line)
            continue

        # Strip inline comments (but not # in strings)
        stripped = line.rstrip()
        if stripped.lstrip().startswith("#"):
            # Full comment line - skip it (replace with empty)
            result.append("\n")
            continue

        result.append(line)

    # Step 3: Remove consecutive blank lines (keep max 1)
    final = []
    prev_blank = False
    for line in result:
        is_blank = line.strip() == ""
        if is_blank and prev_blank:
            continue
        final.append(line)
        prev_blank = is_blank

    return "".join(final)


def strip_comments_only(source: str) -> str:
    """Fallback: just strip comment lines."""
    lines = source.splitlines(keepends=True)
    result = []
    for line in lines:
        if line.strip().startswith("#") and not line.strip().startswith("#!"):
            result.append("\n")
        else:
            result.append(line)
    return "".join(result)


def main():
    workspace = os.path.expanduser("~/Documents/Trading Github/lean-workspace/AlphaNextGen")

    if not os.path.isdir(workspace):
        print(f"ERROR: Workspace not found: {workspace}")
        sys.exit(1)

    total_before = 0
    total_after = 0
    files_processed = 0

    for root, dirs, files in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".vscode", ".idea")]
        for f in sorted(files):
            if not f.endswith(".py"):
                continue

            filepath = os.path.join(root, f)
            with open(filepath, "r") as fh:
                original = fh.read()

            before = len(original.encode("utf-8"))
            minified = strip_comments_and_docstrings(original)
            after = len(minified.encode("utf-8"))

            with open(filepath, "w") as fh:
                fh.write(minified)

            total_before += before
            total_after += after
            files_processed += 1

            savings = before - after
            if savings > 1000:
                name = os.path.relpath(filepath, workspace)
                print(f"  {name:45s} {before:>7} -> {after:>7} (-{savings:>5})")

    print(f"\n  Files processed: {files_processed}")
    print(f"  Before: {total_before:>8,} bytes ({total_before/1024:.0f} KB)")
    print(f"  After:  {total_after:>8,} bytes ({total_after/1024:.0f} KB)")
    print(
        f"  Saved:  {total_before-total_after:>8,} bytes ({(total_before-total_after)/1024:.0f} KB)"
    )


if __name__ == "__main__":
    main()
