#!/usr/bin/env python3
"""Fail CI when a changed non-rendered docstring or comment uses reStructuredText
double-backtick literal markup.

AGENTS.md's "Docstrings and Comments" convention splits on whether Sphinx renders the
docstring: only modules documented via a `.. automodule::` directive under `docs/`
use reStructuredText. Everything else -- private modules nothing under `docs/` points
at, `.pyi` stubs, tests, and inline comments -- is plain prose, where rST literal
markup such as ``example`` only adds stray backticks for anyone reading the source
directly, since nothing renders it.

This check only inspects lines added in the current diff, only inside comments and
triple-quoted strings (the repository's docstring convention), and only in files
whose module is not one of the ones documented via `.. automodule::`. It therefore
cannot fire on pre-existing code, keeping it safe to introduce without a repository-wide
cleanup.

Usage:

    python scripts/check_no_rst_in_private_docstrings.py --base-ref FETCH_HEAD
"""

from __future__ import annotations

import argparse
import io
from pathlib import Path
import re
import subprocess  # nosec B404
import sys
import tokenize
from typing import Optional


REPO_ROOT: Path = Path(__file__).resolve().parents[1]

# A double-backtick literal with no backtick or newline inside it. Good enough to
# catch the rST ``literal`` pattern without tripping over Markdown-style fixtures
# (```code fences```) which use three or more backticks together.
DOUBLE_BACKTICK_RE: re.Pattern[str] = re.compile(r"(?<!`)``(?!`)[^`\n]+(?<!`)``(?!`)")

# This checker and its own tests are allowed to mention the pattern they check for.
EXCLUDED_PATHS: frozenset[str] = frozenset(
    {
        "scripts/check_no_rst_in_private_docstrings.py",
        "tests/internal/test_check_no_rst_in_private_docstrings.py",
    }
)
EXCLUDED_PREFIXES: tuple[str, ...] = (
    "ddtrace/vendor/",
    "ddtrace/appsec/_iast/_taint_tracking/_vendor/",
)


def _is_excluded_path(path: str) -> bool:
    if path in EXCLUDED_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in EXCLUDED_PREFIXES)


def _merge_base(base_ref: str) -> str:
    result: subprocess.CompletedProcess[str] = subprocess.run(  # nosec B603, B607
        ["git", "merge-base", base_ref, "HEAD"],
        capture_output=True,
        check=True,
        text=True,
    )
    return result.stdout.strip()


def _changed_py_files(merge_base: str) -> list[str]:
    result: subprocess.CompletedProcess[str] = subprocess.run(  # nosec B603, B607
        ["git", "diff", "--name-only", "--diff-filter=ACM", merge_base, "HEAD", "--", "*.py", "*.pyi"],
        capture_output=True,
        check=True,
        text=True,
    )
    return [p for p in result.stdout.splitlines() if p and not _is_excluded_path(p)]


def _added_line_numbers(merge_base: str, path: str) -> set[int]:
    result: subprocess.CompletedProcess[str] = subprocess.run(  # nosec B603, B607
        ["git", "diff", "-U0", merge_base, "HEAD", "--", path],
        capture_output=True,
        check=True,
        text=True,
    )
    added: set[int] = set()
    current_line: Optional[int] = None
    hunk_re: re.Pattern[str] = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
    for line in result.stdout.splitlines():
        hunk_match: Optional[re.Match[str]] = hunk_re.match(line)
        if hunk_match:
            current_line = int(hunk_match.group(1))
            continue
        if current_line is None:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            added.add(current_line)
            current_line += 1
        elif line.startswith("-") and not line.startswith("---"):
            continue
        else:
            current_line += 1
    return added


def _rendered_modules() -> frozenset[str]:
    """Modules Sphinx renders: every dotted path named in a `.. automodule::` directive."""
    modules: set[str] = set()
    pattern: re.Pattern[str] = re.compile(r"^\.\.\s+automodule::\s+(\S+)\s*$")
    docs_dir: Path = REPO_ROOT / "docs"
    for rst_path in docs_dir.glob("*.rst"):
        try:
            text = rst_path.read_text(encoding="utf-8")
        except (FileNotFoundError, UnicodeDecodeError):
            continue
        for line in text.splitlines():
            match: Optional[re.Match[str]] = pattern.match(line.strip())
            if match:
                modules.add(match.group(1))
    return frozenset(modules)


def _module_name(path: str) -> str:
    as_path: Path = Path(path)
    parts: list[str] = list(as_path.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = as_path.stem
    return ".".join(parts)


def _is_rendered(path: str, rendered_modules: frozenset[str]) -> bool:
    # .pyi stubs are never Sphinx-rendered, regardless of the module they describe.
    if path.endswith(".pyi"):
        return False
    return _module_name(path) in rendered_modules


def _doc_and_comment_spans(source: str) -> list[tuple[int, int, str]]:
    """Return (start_line, end_line, text) for every comment and triple-quoted string."""
    spans: list[tuple[int, int, str]] = []
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenizeError, SyntaxError, IndentationError, ValueError):
        return spans
    for tok in tokens:
        if tok.type == tokenize.COMMENT:
            spans.append((tok.start[0], tok.end[0], tok.string))
        elif tok.type == tokenize.STRING:
            stripped = tok.string.lstrip("rRbBuUfF")
            if stripped.startswith('"""') or stripped.startswith("'''"):
                spans.append((tok.start[0], tok.end[0], tok.string))
    return spans


def _violations_in_file(path: str, added_lines: set[int]) -> list[tuple[int, str]]:
    full_path: Path = REPO_ROOT / path
    try:
        source = full_path.read_text(encoding="utf-8")
    except (FileNotFoundError, UnicodeDecodeError):
        return []
    hits: list[tuple[int, str]] = []
    for start, end, text in _doc_and_comment_spans(source):
        if not any(line_no in added_lines for line_no in range(start, end + 1)):
            continue
        for match in DOUBLE_BACKTICK_RE.finditer(text):
            line_offset = text[: match.start()].count("\n")
            line_no = start + line_offset
            if line_no in added_lines:
                hits.append((line_no, match.group(0).strip()))
    return hits


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-ref", default="origin/main", help="Git ref to diff against.")
    args: argparse.Namespace = parser.parse_args(argv)

    print(f"Checking for new rST double-backtick markup in non-rendered docs vs base ref: {args.base_ref}")

    try:
        merge_base: str = _merge_base(args.base_ref)
        files: list[str] = _changed_py_files(merge_base)
    except subprocess.CalledProcessError as exc:
        print(f"error: failed to compute diff against {args.base_ref}: {exc.stderr}", file=sys.stderr)
        return 1

    rendered_modules: frozenset[str] = _rendered_modules()
    violations: list[tuple[str, int, str]] = []
    for path in files:
        if _is_rendered(path, rendered_modules):
            continue
        try:
            added_lines: set[int] = _added_line_numbers(merge_base, path)
        except subprocess.CalledProcessError:
            continue
        for line_no, snippet in _violations_in_file(path, added_lines):
            violations.append((path, line_no, snippet))

    if not violations:
        print("OK: no new rST double-backtick markup in non-rendered docstrings/comments.")
        return 0

    print(f"ERROR: {len(violations)} new rST double-backtick literal(s) found in non-rendered code:", file=sys.stderr)
    for path, line_no, snippet in violations:
        print(f"  - {path}:{line_no}: {snippet}", file=sys.stderr)
    print(
        "\nOnly docstrings Sphinx renders (modules named in a docs/*.rst `.. automodule::` "
        "directive) use reStructuredText. Use plain prose instead "
        "(see AGENTS.md -- Docstrings and Comments).",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
