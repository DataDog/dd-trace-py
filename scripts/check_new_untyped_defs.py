#!/usr/bin/env python3
"""Fail if a branch adds completely untyped contrib function signatures.

Reviewers have repeatedly asked contributors to add type hints to brand-new
functions in contrib integration code. This check catches that mechanically
instead of relying on a human to notice it in review. Existing untyped
functions are left alone because only newly added definition lines are checked.

Usage:

    python scripts/check_new_untyped_defs.py --base-ref REF
"""

from __future__ import annotations

import argparse
import ast
from pathlib import Path
import re
import subprocess  # nosec: B404
import sys


_TARGET_PREFIXES: tuple[str, ...] = ("ddtrace/contrib/", "tests/contrib/")
_DIFF_HUNK_RE: re.Pattern[str] = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<line>\d+)(?:,\d+)? @@")
_FunctionDef = ast.FunctionDef | ast.AsyncFunctionDef


def _run_git(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec: B603, B607
        ["git", *args],
        capture_output=True,
        check=check,
        text=True,
    )


def _merge_base(base_ref: str) -> str:
    result: subprocess.CompletedProcess[str] = _run_git(["merge-base", base_ref, "HEAD"])
    return result.stdout.strip()


def _changed_files(merge_base: str) -> list[str]:
    result: subprocess.CompletedProcess[str] = _run_git(["diff", "--name-only", "--diff-filter=ACMR", merge_base, "--"])
    return result.stdout.splitlines()


def _is_target_python_file(path: str) -> bool:
    return path.endswith(".py") and path.startswith(_TARGET_PREFIXES)


def _added_lines(merge_base: str, path: str) -> set[int]:
    result: subprocess.CompletedProcess[str] = _run_git(
        ["diff", "--unified=0", "--no-ext-diff", merge_base, "--", path]
    )
    added_lines: set[int] = set()
    line_number: int | None = None
    for line in result.stdout.splitlines():
        hunk: re.Match[str] | None = _DIFF_HUNK_RE.match(line)
        if hunk is not None:
            line_number = int(hunk.group("line"))
            continue
        if line_number is None:
            continue
        if line.startswith("+"):
            added_lines.add(line_number)
            line_number += 1
        elif not line.startswith("-"):
            line_number += 1
    return added_lines


def _parameters(node: _FunctionDef) -> list[ast.arg]:
    positional: list[ast.arg] = [*node.args.posonlyargs, *node.args.args]
    if positional and positional[0].arg in {"self", "cls"}:
        positional = positional[1:]

    parameters: list[ast.arg] = [*positional, *node.args.kwonlyargs]
    if node.args.vararg is not None:
        parameters.append(node.args.vararg)
    if node.args.kwarg is not None:
        parameters.append(node.args.kwarg)
    return parameters


def _is_completely_untyped(node: _FunctionDef) -> bool:
    parameters: list[ast.arg] = _parameters(node)
    return bool(parameters) and node.returns is None and all(parameter.annotation is None for parameter in parameters)


def _find_new_untyped_defs(path: str, source: str, added_lines: set[int]) -> list[tuple[int, str]]:
    if not _is_target_python_file(path):
        return []

    tree: ast.AST = ast.parse(source, filename=path)
    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.lineno in added_lines and _is_completely_untyped(node):
                violations.append((node.lineno, node.name))
    return sorted(violations)


def main(argv: list[str] | None = None) -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--base-ref", default="origin/main", help="Git ref to diff against.")
    args: argparse.Namespace = parser.parse_args(argv)

    print(f"Comparing against base ref: {args.base_ref}")

    try:
        merge_base: str = _merge_base(args.base_ref)
        violations: list[tuple[str, int, str]] = []
        for path in _changed_files(merge_base):
            if not _is_target_python_file(path):
                continue
            source: str = Path(path).read_text(encoding="utf-8")
            violations.extend(
                (path, line_number, name)
                for line_number, name in _find_new_untyped_defs(path, source, _added_lines(merge_base, path))
            )
    except subprocess.CalledProcessError as exc:
        print(f"error: failed to compute diff against {args.base_ref}: {exc.stderr}", file=sys.stderr)
        return 1
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        print(f"error: failed to inspect changed Python file: {exc}", file=sys.stderr)
        return 1

    if not violations:
        print("OK: no new completely untyped contrib function signatures.")
        return 0

    print("ERROR: new completely untyped contrib function signatures found.", file=sys.stderr)
    print(
        "Add inline parameter and/or return type hints following PEP 484 and "
        "the repository's existing typing conventions.",
        file=sys.stderr,
    )
    for path, line_number, name in violations:
        print(f"  {path}:{line_number}: {name}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
