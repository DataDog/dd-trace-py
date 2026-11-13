#!/usr/bin/env python3
"""Fail CI when a branch adds reStructuredText double-backtick markup to
non-Sphinx-rendered prose under tests/ or scripts/.

AGENTS.md ("Docstrings and Comments") splits documentation style on whether
Sphinx renders it: rendered docstrings use reStructuredText, everything else
is plain prose. Nothing under tests/ or scripts/ is Sphinx-rendered, so
double-backtick literals in comments and docstrings there are noise.

Python strings that are not docstrings are executable data. An assertion can
quote a runtime message that itself contains double backticks, and removing
them would change the test. Those strings are ignored. Other text files under
tests/ and scripts/ are still scanned, because they have no executable strings
to protect.

ast-grep scans the whole tree, so it cannot enforce this only on lines added
in the current diff, and it would fail the build on pre-existing markup. This
check follows scripts/check_no_new_aidev_anchors.py: added diff lines only.

Usage:

    python scripts/check_no_rst_markup_in_prose.py --base-ref FETCH_HEAD
"""

from __future__ import annotations

import argparse
import ast
import io
import pathlib
import re
import subprocess  # nosec B404
import sys
import tokenize
from typing import Optional


# Matches ``text`` including a literal that wraps onto the next source line.
# A third backtick on either side rejects Markdown fences such as ```.
RST_DOUBLE_BACKTICK_RE: re.Pattern[str] = re.compile(r"(?<!`)``([^`]+?)``(?!`)")
_HUNK_NEW_START_RE: re.Pattern[str] = re.compile(r"\+(\d+)")

# Only tests/ and scripts/ are in scope: neither directory is Sphinx-rendered.
IN_SCOPE_PREFIXES: tuple[str, ...] = ("tests/", "scripts/")

# This checker and its own test intentionally contain double-backtick examples.
EXCLUDED_PATHS: frozenset[str] = frozenset(
    {
        "scripts/check_no_rst_markup_in_prose.py",
        "tests/internal/test_check_no_rst_markup_in_prose.py",
    }
)

_SIMPLE_ESCAPES: dict[str, bytes] = {
    "\\": b"\\",
    '"': b'"',
    "n": b"\n",
    "t": b"\t",
    "b": b"\b",
    "f": b"\f",
    "r": b"\r",
    "a": b"\a",
    "v": b"\v",
}


def _is_in_scope_path(path: str) -> bool:
    if path in EXCLUDED_PATHS:
        return False
    return path.startswith(IN_SCOPE_PREFIXES)


def _has_rst_markup(content: str) -> bool:
    return RST_DOUBLE_BACKTICK_RE.search(content) is not None


def _unescape_c_quoted(quoted: str) -> str:
    """Decode a Git C-quoted path, including octal byte escapes."""
    body: str = quoted[1:-1]
    raw: bytearray = bytearray()
    index: int = 0
    while index < len(body):
        char: str = body[index]
        if char != "\\":
            raw.extend(char.encode("utf-8", errors="surrogateescape"))
            index += 1
            continue
        index += 1
        if index >= len(body):
            break
        escaped: str = body[index]
        index += 1
        replacement: Optional[bytes] = _SIMPLE_ESCAPES.get(escaped)
        if replacement is not None:
            raw.extend(replacement)
            continue
        if escaped not in "01234567":
            raw.extend(escaped.encode("utf-8", errors="surrogateescape"))
            continue
        octal: str = escaped
        while index < len(body) and len(octal) < 3 and body[index] in "01234567":
            octal += body[index]
            index += 1
        raw.append(int(octal, 8))
    return raw.decode("utf-8", errors="surrogateescape")


def _parse_new_path(raw: str) -> str:
    """Return the repo-relative path from a +++ diff header, or empty for /dev/null."""
    candidate: str = raw.split("\t", 1)[0].strip()
    if len(candidate) >= 2 and candidate.startswith('"') and candidate.endswith('"'):
        candidate = _unescape_c_quoted(candidate)
    if candidate == "/dev/null":
        return ""
    if candidate.startswith("b/"):
        return candidate[2:]
    return candidate


def _hunk_new_start(header: str) -> int:
    match: Optional[re.Match[str]] = _HUNK_NEW_START_RE.search(header)
    if match is None:
        return 1
    return int(match.group(1))


def _parse_added_lines(diff: str) -> list[tuple[str, int, str]]:
    """Added in-scope lines as (path, new_line_number, content).

    A file header is +++ only before the first hunk. Inside a hunk, a line
    that begins with +++ is an added source line that itself begins with ++.
    """
    current_file: str = ""
    in_hunk: bool = False
    new_line: int = 0
    added: list[tuple[str, int, str]] = []
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            current_file = ""
            in_hunk = False
            continue
        if line.startswith("+++ ") and not in_hunk:
            current_file = _parse_new_path(line[4:])
            continue
        if line.startswith("@@"):
            in_hunk = True
            new_line = _hunk_new_start(line)
            continue
        if not in_hunk:
            continue
        if line.startswith("+"):
            if current_file and _is_in_scope_path(current_file):
                added.append((current_file, new_line, line[1:]))
            new_line += 1
            continue
        if line.startswith("-") or line.startswith("\\"):
            continue
        new_line += 1
    return added


def _lines_overlapping_markup(text: str, line_numbers: list[int]) -> list[int]:
    found: list[int] = []
    if not line_numbers:
        return found
    for match in RST_DOUBLE_BACKTICK_RE.finditer(text):
        start_index: int = text.count("\n", 0, match.start())
        end_index: int = text.count("\n", 0, max(match.end() - 1, match.start()))
        end_index = min(end_index, len(line_numbers) - 1)
        found.extend(line_numbers[start_index : end_index + 1])
    return found


def _comment_regions(source: str) -> list[tuple[list[int], str]]:
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        comments: list[tuple[int, str]] = [(tok.start[0], tok.string) for tok in tokens if tok.type == tokenize.COMMENT]
    except (tokenize.TokenError, IndentationError, SyntaxError, ValueError):
        return []
    regions: list[tuple[list[int], str]] = []
    line_numbers: list[int] = []
    texts: list[str] = []
    for lineno, text in comments:
        if line_numbers and lineno != line_numbers[-1] + 1:
            regions.append((line_numbers, "\n".join(texts)))
            line_numbers, texts = [], []
        line_numbers.append(lineno)
        texts.append(text)
    if line_numbers:
        regions.append((line_numbers, "\n".join(texts)))
    return regions


def _docstring_regions(source: str) -> list[tuple[list[int], str]]:
    try:
        tree: ast.AST = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    source_lines: list[str] = source.splitlines()
    ranges: list[tuple[int, int]] = []

    def visit(node: ast.AST) -> None:
        body: object = getattr(node, "body", None)
        if isinstance(body, list) and body:
            first: ast.stmt = body[0]
            value: object = getattr(first, "value", None)
            end_lineno: object = getattr(first, "end_lineno", None)
            if (
                isinstance(first, ast.Expr)
                and isinstance(value, ast.Constant)
                and isinstance(value.value, str)
                and isinstance(first.lineno, int)
                and isinstance(end_lineno, int)
            ):
                ranges.append((first.lineno, end_lineno))
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child)

    visit(tree)
    regions: list[tuple[list[int], str]] = []
    for start, end in ranges:
        regions.append((list(range(start, end + 1)), "\n".join(source_lines[start - 1 : end])))
    return regions


def _python_hits(source: str, added: dict[int, str]) -> list[tuple[int, str]]:
    hit_lines: set[int] = set()
    for line_numbers, text in _comment_regions(source) + _docstring_regions(source):
        for lineno in _lines_overlapping_markup(text, line_numbers):
            if lineno in added:
                hit_lines.add(lineno)
    return [(lineno, added[lineno].rstrip()) for lineno in sorted(hit_lines)]


def _text_hits(added: list[tuple[int, str]]) -> list[tuple[int, str]]:
    if not added:
        return []
    ordered: list[tuple[int, str]] = sorted(added)
    runs: list[list[tuple[int, str]]] = [[ordered[0]]]
    for item in ordered[1:]:
        if item[0] == runs[-1][-1][0] + 1:
            runs[-1].append(item)
        else:
            runs.append([item])
    by_line: dict[int, str] = dict(ordered)
    hit_lines: set[int] = set()
    for run in runs:
        line_numbers: list[int] = [lineno for lineno, _content in run]
        text: str = "\n".join(content for _lineno, content in run)
        for lineno in _lines_overlapping_markup(text, line_numbers):
            hit_lines.add(lineno)
    return [(lineno, by_line[lineno].rstrip()) for lineno in sorted(hit_lines)]


def _read_source(path: str) -> Optional[str]:
    try:
        return pathlib.Path(path).read_text(encoding="utf-8", errors="surrogateescape")
    except OSError:
        return None


def _run_git(args: list[str]) -> str:
    # surrogateescape keeps non-UTF-8 fixture bytes in the diff instead of
    # raising UnicodeDecodeError and failing an unrelated change.
    result: subprocess.CompletedProcess[bytes] = subprocess.run(  # nosec B603, B607
        args,
        capture_output=True,
        check=True,
    )
    return result.stdout.decode("utf-8", errors="surrogateescape")


def _added_lines(base_ref: str) -> list[tuple[str, int, str]]:
    merge_base: str = _run_git(["git", "merge-base", base_ref, "HEAD"]).strip()
    diff: str = _run_git(["git", "diff", "-U0", merge_base, "--", "tests", "scripts"])
    by_path: dict[str, list[tuple[int, str]]] = {}
    for path, lineno, content in _parse_added_lines(diff):
        by_path.setdefault(path, []).append((lineno, content))

    violations: list[tuple[str, int, str]] = []
    for path, lines in by_path.items():
        if path.endswith(".py"):
            source: Optional[str] = _read_source(path)
            if source is None:
                continue
            hits: list[tuple[int, str]] = _python_hits(source, dict(lines))
        else:
            hits = _text_hits(lines)
        for lineno, content in hits:
            violations.append((path, lineno, content))
    return violations


def _format_git_error(exc: subprocess.CalledProcessError) -> str:
    err: str | bytes = exc.stderr or ""
    if isinstance(err, bytes):
        return err.decode("utf-8", errors="replace")
    return err


def main(argv: Optional[list[str]] = None) -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base-ref", default="origin/main", help="Git ref to diff against.")
    args: argparse.Namespace = parser.parse_args(argv)

    print(f"Checking for new rST double-backtick markup under tests/ and scripts/ vs base ref: {args.base_ref}")

    try:
        violations: list[tuple[str, int, str]] = _added_lines(args.base_ref)
    except subprocess.CalledProcessError as exc:
        print(f"error: failed to compute diff against {args.base_ref}: {_format_git_error(exc)}", file=sys.stderr)
        return 1

    if not violations:
        print("OK: no new rST double-backtick markup on added lines under tests/ or scripts/.")
        return 0

    print(f"ERROR: {len(violations)} new rST double-backtick literal(s) found:", file=sys.stderr)
    for path, lineno, content in violations:
        print(f"  - {path}:{lineno}: {content}", file=sys.stderr)
    print(
        "\nNothing under tests/ or scripts/ is Sphinx-rendered, so ``like-this`` markup in "
        "comments and docstrings is stray punctuation. Write the identifier as plain text "
        "instead (see AGENTS.md — Docstrings and Comments).",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
