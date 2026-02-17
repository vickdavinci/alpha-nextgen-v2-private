#!/usr/bin/env python3
"""Ultra-aggressive minifier for Python files that exceed QC's 256KB limit.

Applied AFTER the standard minifier. Techniques:
1. Remove ALL blank lines
2. Strip inline comments (after #) from code lines
3. Remove type annotations from function signatures
4. Collapse excessive whitespace in long strings (log messages)
5. Remove `pass` in non-empty bodies
"""

import argparse
import ast
import io
import os
import re
import sys
import tokenize

try:
    import python_minifier
except Exception:
    python_minifier = None


def remove_type_annotations(source: str, allow_var_annotations: bool = False) -> str:
    """Remove type annotations from function defs and variable assignments using AST."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source

    # Collect line ranges that need annotation removal
    # We'll work with source lines directly for precision
    lines = source.splitlines(keepends=True)

    # Strategy: use AST to find annotated constructs, then regex to strip annotations from those lines
    # This is safer than full AST rewrite which can break formatting

    # Find all function definitions and remove annotations
    changes = []  # list of (line_no, col_offset, annotation_text_to_remove)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Remove return annotation
            if node.returns is not None:
                changes.append(("return_annotation", node))
            # Remove argument annotations
            for arg in node.args.args + node.args.posonlyargs + node.args.kwonlyargs:
                if arg.annotation is not None:
                    changes.append(("arg_annotation", arg))
            if node.args.vararg and node.args.vararg.annotation:
                changes.append(("arg_annotation", node.args.vararg))
            if node.args.kwarg and node.args.kwarg.annotation:
                changes.append(("arg_annotation", node.args.kwarg))

        # Remove variable annotations (x: int = 5 -> x = 5)
        if isinstance(node, ast.AnnAssign) and node.value is not None:
            changes.append(("var_annotation", node))

    if not changes:
        return source

    # Rebuild source using ast.unparse would lose formatting
    # Instead, use regex on the function signature lines
    # This is a targeted approach

    result = source

    # Process function signatures - remove ': Type' from args and '-> Type' from return
    # We'll use a line-by-line approach on 'def' lines
    new_lines = []
    i = 0
    src_lines = result.splitlines(keepends=True)

    while i < len(src_lines):
        line = src_lines[i]
        stripped = line.lstrip()

        if stripped.startswith("def ") or stripped.startswith("async def "):
            # Collect the full function signature (may span multiple lines)
            sig_lines = [line]
            # Check if signature continues (no closing paren + colon)
            sig_text = line
            j = i + 1
            while j < len(src_lines) and not _sig_complete(sig_text):
                sig_lines.append(src_lines[j])
                sig_text += src_lines[j]
                j += 1

            # Now strip annotations from the signature
            full_sig = "".join(sig_lines)
            cleaned = _strip_function_annotations(full_sig)
            new_lines.append(cleaned)
            i = j
        else:
            # Variable annotation stripping is optional; disable for dataclass-heavy files.
            if allow_var_annotations:
                cleaned = _strip_var_annotation(line)
                new_lines.append(cleaned)
            else:
                new_lines.append(line)
            i += 1

    return "".join(new_lines)


def _sig_complete(text: str) -> bool:
    """Check if a function signature is complete (has closing paren and colon)."""
    # Count parens
    depth = 0
    in_string = False
    string_char = None
    for ch in text:
        if in_string:
            if ch == string_char:
                in_string = False
            continue
        if ch in ('"', "'"):
            in_string = True
            string_char = ch
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                # Check if colon follows (possibly with return annotation)
                return True
    return False


def _strip_function_annotations(sig: str) -> str:
    """Strip type annotations from a function signature string."""
    # Remove return annotation: ') -> Type:' -> '):'
    sig = re.sub(r"\)\s*->\s*[^:]+:", "):", sig)

    # Remove argument annotations: 'arg: Type' -> 'arg'
    # But be careful not to match dict literals or default values
    # Strategy: work inside the parens only

    # Find the opening paren
    paren_start = sig.find("(")
    if paren_start == -1:
        return sig

    # Find matching close paren
    depth = 0
    paren_end = -1
    for i in range(paren_start, len(sig)):
        if sig[i] == "(":
            depth += 1
        elif sig[i] == ")":
            depth -= 1
            if depth == 0:
                paren_end = i
                break

    if paren_end == -1:
        return sig

    before = sig[: paren_start + 1]
    args_str = sig[paren_start + 1 : paren_end]
    after = sig[paren_end:]

    # Parse args and strip annotations
    cleaned_args = _strip_arg_annotations(args_str)

    return before + cleaned_args + after


def _strip_arg_annotations(args_str: str) -> str:
    """Strip type annotations from argument list string."""
    if not args_str.strip():
        return args_str

    # Split by commas, respecting brackets/parens/strings
    parts = _smart_split(args_str, ",")
    cleaned = []

    for part in parts:
        stripped = part.strip()
        if not stripped:
            cleaned.append(part)
            continue

        # Handle **kwargs: Type or *args: Type
        match = re.match(r"^(\s*\*{0,2}\w+)\s*:\s*(.+?)(\s*=\s*.+)?$", stripped, re.DOTALL)
        if match:
            name = match.group(1)
            default = match.group(3) or ""
            # Preserve leading whitespace from original
            leading = part[: len(part) - len(part.lstrip())]
            cleaned.append(f"{leading}{name}{default}")
        else:
            cleaned.append(part)

    return ",".join(cleaned)


def _smart_split(s: str, delimiter: str) -> list:
    """Split string by delimiter, respecting brackets and strings."""
    parts = []
    depth = 0
    current = []
    in_string = False
    string_char = None

    for ch in s:
        if in_string:
            current.append(ch)
            if ch == string_char:
                in_string = False
            continue

        if ch in ('"', "'"):
            in_string = True
            string_char = ch
            current.append(ch)
            continue

        if ch in ("(", "[", "{"):
            depth += 1
        elif ch in (")", "]", "}"):
            depth -= 1

        if ch == delimiter and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)

    parts.append("".join(current))
    return parts


def _strip_var_annotation(line: str) -> str:
    """Strip annotation from variable assignment: 'x: int = 5' -> 'x = 5'."""
    stripped = line.lstrip()
    indent = line[: len(line) - len(stripped)]

    # Match pattern: identifier: Type = value
    match = re.match(r"^(\w+)\s*:\s*[^=]+=(.+)$", stripped, re.DOTALL)
    if match:
        name = match.group(1)
        value = match.group(2).strip()
        return f"{indent}{name} = {value}\n" if line.endswith("\n") else f"{indent}{name} = {value}"

    return line


def strip_inline_comments(source: str) -> str:
    """Remove inline comments from code lines (preserving # in strings)."""
    lines = source.splitlines(keepends=True)
    result = []

    for line in lines:
        stripped = line.rstrip()

        # Drop comment-only lines to maximize size reduction.
        # Preserve shebang / encoding cookie semantics if present.
        lstripped = stripped.lstrip()
        if lstripped.startswith("#"):
            if lstripped.startswith("#!") or "coding" in lstripped[:30]:
                result.append(line)
            continue

        # Find inline comment position (# not inside string)
        comment_pos = _find_inline_comment(stripped)
        if comment_pos > 0:
            # Remove the comment part, keep the code
            code_part = stripped[:comment_pos].rstrip()
            indent_and_code = code_part
            result.append(indent_and_code + "\n")
        else:
            result.append(line)

    return "".join(result)


def _find_inline_comment(line: str) -> int:
    """Find position of inline # comment, -1 if none or if it's in a string."""
    in_string = False
    string_char = None
    i = 0
    while i < len(line):
        ch = line[i]

        if in_string:
            if ch == "\\":
                i += 2  # skip escaped char
                continue
            if ch == string_char:
                # Check for triple quote
                if line[i : i + 3] == string_char * 3:
                    i += 3
                else:
                    i += 1
                in_string = False
            else:
                i += 1
            continue

        # Check for string start
        if ch in ('"', "'"):
            if line[i : i + 3] in ('"""', "'''"):
                string_char = line[i : i + 3]
                in_string = True
                i += 3
            else:
                string_char = ch
                in_string = True
                i += 1
            continue

        if ch == "#":
            # Make sure there's code before it (not just whitespace)
            before = line[:i].strip()
            if before:
                return i
            return -1

        i += 1

    return -1


def remove_standalone_docstrings(source: str) -> str:
    """Remove standalone triple-quoted docstring/comment blocks."""
    lines = source.splitlines(keepends=True)
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()
        if stripped.startswith('"""') or stripped.startswith("'''"):
            quote = stripped[:3]
            if stripped.count(quote) >= 2 and stripped.strip() != quote:
                i += 1
                continue
            i += 1
            while i < len(lines):
                if quote in lines[i]:
                    i += 1
                    break
                i += 1
            continue
        out.append(line)
        i += 1
    return "".join(out)


def remove_ast_docstrings(source: str) -> str:
    """Remove real docstring expressions from module/class/function scopes."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source

    line_starts = [0]
    for line in source.splitlines(keepends=True):
        line_starts.append(line_starts[-1] + len(line))

    def to_offset(lineno: int, col: int) -> int:
        return line_starts[lineno - 1] + col

    ranges = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not getattr(node, "body", None):
            continue
        first = node.body[0]
        if not (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
            and hasattr(first, "lineno")
            and hasattr(first, "end_lineno")
        ):
            continue

        start = to_offset(first.lineno, first.col_offset)
        end = to_offset(first.end_lineno, first.end_col_offset)
        # Also trim one trailing newline to avoid leaving empty spacer lines.
        if end < len(source) and source[end : end + 1] == "\n":
            end += 1
        ranges.append((start, end))

    if not ranges:
        return source

    ranges.sort()
    merged = []
    for start, end in ranges:
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)

    out = []
    cursor = 0
    for start, end in merged:
        out.append(source[cursor:start])
        cursor = end
    out.append(source[cursor:])
    return "".join(out)


def remove_blank_lines(source: str) -> str:
    """Remove ALL blank lines."""
    lines = source.splitlines(keepends=True)
    return "".join(line for line in lines if line.strip())


def remove_unnecessary_pass(source: str) -> str:
    """Remove 'pass' statements in non-empty class/function bodies."""
    lines = source.splitlines(keepends=True)
    result = []

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "pass":
            # Check if there's another statement in the same block
            # Look at next non-blank line
            has_sibling = False
            indent_level = len(line) - len(line.lstrip())
            for j in range(i + 1, min(i + 5, len(lines))):
                next_stripped = lines[j].strip()
                if not next_stripped:
                    continue
                next_indent = len(lines[j]) - len(lines[j].lstrip())
                if next_indent == indent_level:
                    has_sibling = True
                    break
                elif next_indent < indent_level:
                    break

            # Also check previous line for def/class/if/else etc
            if not has_sibling:
                result.append(line)
            # else: skip the pass
        else:
            result.append(line)

    return "".join(result)


def compress_string_literal_whitespace(source: str) -> str:
    """Compress non-semantic whitespace in long non-f string literals."""
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except tokenize.TokenError:
        return source

    updated = []
    for tok in tokens:
        if tok.type != tokenize.STRING:
            updated.append(tok)
            continue

        literal = tok.string
        prefix_match = re.match(r"^([rRbBuUfF]*)", literal)
        prefix = prefix_match.group(1) if prefix_match else ""
        if "f" in prefix.lower():
            updated.append(tok)
            continue

        try:
            value = ast.literal_eval(literal)
        except Exception:
            updated.append(tok)
            continue

        if not isinstance(value, str) or len(value) < 40:
            updated.append(tok)
            continue

        # Skip regex/escape heavy literals where whitespace can be semantic.
        if "\\" in value:
            updated.append(tok)
            continue

        compact = value.replace(" | ", "|")
        compact = re.sub(r" {2,}", " ", compact)

        if compact != value:
            tok = tokenize.TokenInfo(
                type=tok.type,
                string=repr(compact),
                start=tok.start,
                end=tok.end,
                line=tok.line,
            )

        updated.append(tok)

    try:
        return tokenize.untokenize(updated)
    except Exception:
        return source


def compress_indentation(source: str, target_spaces: int = 1) -> str:
    """Compress indentation to target_spaces while staying idempotent.

    Important: this must be safe to run multiple times. If a file is already
    compressed at or below target_spaces, return it unchanged.
    """
    lines = source.splitlines(keepends=True)
    min_positive_indent = None
    for line in lines:
        stripped = line.lstrip(" ")
        if not stripped:
            continue
        indent = len(line) - len(stripped)
        if indent > 0:
            min_positive_indent = (
                indent if min_positive_indent is None else min(min_positive_indent, indent)
            )

    # Already compressed (or no indentation in file) -> no-op.
    if min_positive_indent is None or min_positive_indent <= target_spaces:
        return source

    result = []
    for line in lines:
        stripped = line.lstrip(" ")
        indent = len(line) - len(stripped)
        if indent == 0:
            result.append(line)
        else:
            # Convert relative to observed base indent width.
            levels = indent // min_positive_indent
            remainder = indent % min_positive_indent
            new_indent = " " * (levels * target_spaces + remainder)
            result.append(new_indent + stripped)
    return "".join(result)


def ultra_minify(source: str, target_spaces: int = 2, allow_var_annotations: bool = False) -> str:
    """Apply all ultra-minification techniques."""
    result = source
    result = strip_inline_comments(result)
    result = remove_standalone_docstrings(result)
    result = remove_ast_docstrings(result)
    result = remove_type_annotations(result, allow_var_annotations=allow_var_annotations)
    result = compress_string_literal_whitespace(result)
    # remove_unnecessary_pass disabled: causes IndentationError on QC after basic minify strips docstrings
    result = remove_blank_lines(result)
    result = compress_indentation(result, target_spaces=target_spaces)
    return result


def python_minifier_fallback(source: str) -> str:
    """Apply python-minifier when available for extra compression near QC limit."""
    if python_minifier is None:
        return source
    try:
        result = python_minifier.minify(
            source,
            remove_literal_statements=True,
            combine_imports=True,
            remove_annotations=True,
            hoist_literals=False,
            rename_locals=False,
            rename_globals=False,
            remove_object_base=True,
            convert_posargs_to_args=True,
            preserve_shebang=True,
        )
        ast.parse(result)
        if len(result) < len(source):
            return result
    except Exception:
        return source
    return source


def main():
    parser = argparse.ArgumentParser(
        description="Ultra minify Lean workspace files without breaking syntax."
    )
    parser.add_argument(
        "--workspace",
        default="~/Documents/Trading Github/lean-workspace/AlphaNextGen",
        help="Workspace root to minify",
    )
    parser.add_argument(
        "--target-indent",
        type=int,
        default=2,
        help="Target indentation width for compression (default: 2)",
    )
    args = parser.parse_args()

    workspace = os.path.expanduser(args.workspace)
    target_indent = max(1, int(args.target_indent))

    if not os.path.isdir(workspace):
        print(f"ERROR: Workspace not found: {workspace}")
        sys.exit(1)

    LIMIT = 256000  # QC hard limit: 256,000 characters per file
    PROJECT_LIMIT = 500 * 1024  # ~500 KB total project for QC

    print("=== Ultra Minification (ALL .py files) ===\n")

    oversized = []
    total_before = 0
    total_after = 0

    for root, dirs, files in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".vscode", ".idea")]
        for f in sorted(files):
            if not f.endswith(".py"):
                continue

            filepath = os.path.join(root, f)
            with open(filepath, "r") as fh:
                content = fh.read()

            size = len(content.encode("utf-8"))
            name = os.path.relpath(filepath, workspace)

            allow_var_annotations = "@dataclass" not in content
            minified = ultra_minify(
                content,
                target_spaces=target_indent,
                allow_var_annotations=allow_var_annotations,
            )
            if len(minified) > 252000:
                minified = python_minifier_fallback(minified)
            # Safety guard: never write syntactically invalid Python.
            try:
                ast.parse(minified)
            except SyntaxError as e:
                print(f"  {name}: SKIP (syntax guard) [{e.msg}]")
                minified = content
            new_size = len(minified.encode("utf-8"))

            with open(filepath, "w") as fh:
                fh.write(minified)

            saved = size - new_size
            if saved > 0:
                print(f"  {name}: {size:,} -> {new_size:,} bytes [saved {saved:,}]")
            else:
                print(f"  {name}: {size:,} bytes (no change)")

            if new_size > LIMIT:
                oversized.append((name, new_size))
                print(f"    ⚠ OVER 256KB LIMIT by {(new_size - LIMIT):,} bytes")

            total_before += size
            total_after += new_size

    print(f"\n  Total before: {total_before:,} bytes ({total_before/1024:.0f} KB)")
    print(f"  Total after:  {total_after:,} bytes ({total_after/1024:.0f} KB)")
    print(f"  Saved:        {total_before - total_after:,} bytes")

    if total_after > PROJECT_LIMIT:
        print(f"\n  ⚠ Total {total_after/1024:.0f} KB exceeds ~500KB QC project limit")

    if oversized:
        print(f"\n  ⚠ {len(oversized)} file(s) still over 256KB:")
        for name, size in oversized:
            print(f"    {name}: {size:,} bytes ({size/1024:.0f} KB)")
        return 1
    else:
        print("\n  ✓ All files under 256KB per-file limit")
        return 0


if __name__ == "__main__":
    sys.exit(main())
