#!/usr/bin/env python3
"""Fail if a branch adds PEP 484 type comments.

Inline annotations are required for new types::

    def f(x: int) -> str:
        y: list[str] = []

Existing type comments may stay. # type: ignore comments are allowed.

Usage:

    python scripts/check_type_comments.py --base-ref REF
"""

from __future__ import annotations

import argparse
from io import StringIO
from pathlib import Path
import re
import subprocess  # nosec: B404
import sys
import tokenize


_PYTHON_SUFFIXES: tuple[str, ...] = (".py", ".pyi")
_TYPE_COMMENT_RE: re.Pattern[str] = re.compile(r"#\s*type:\s*(?P<annotation>.*)$")
_DIFF_HUNK_RE: re.Pattern[str] = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<line>\d+)(?:,\d+)? @@")


def _is_type_comment(comment: str) -> bool:
    """Return True for a non-ignore type comment.

    >>> _is_type_comment("# type: int")
    True
    >>> _is_type_comment("# type: (int) -> None")
    True
    >>> _is_type_comment("# type: ignore[attr-defined]")
    False
    >>> _is_type_comment("# type: ignore")
    False
    >>> _is_type_comment("# unrelated comment")
    False
    """
    match: re.Match[str] | None = _TYPE_COMMENT_RE.fullmatch(comment)
    if match is None:
        return False
    annotation: str = match.group("annotation").strip()
    return not re.match(r"ignore(?:\[[^\]]*])?(?:\s|$)", annotation)


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


def _is_python_file(path: str) -> bool:
    if path.endswith(_PYTHON_SUFFIXES):
        return True
    file_path: Path = Path(path)
    if not file_path.is_file():
        return False
    try:
        with file_path.open(encoding="utf-8") as file:
            header_lines: list[str] = [file.readline() for _ in range(5)]
    except (OSError, UnicodeDecodeError):
        return False
    first_line: str = header_lines[0]
    return first_line.startswith("#!") and (
        "python" in first_line or any("mode: python" in line for line in header_lines)
    )


def _comment_lines(path: str) -> set[int] | None:
    """Return lines containing Python comment tokens.

    Return None when the tokenizer cannot process the file. The caller then
    falls back to diff-line matching so newer Python syntax is still covered.
    """
    try:
        source: str = Path(path).read_text(encoding="utf-8")
        return {
            token.start[0]
            for token in tokenize.generate_tokens(StringIO(source).readline)
            if token.type == tokenize.COMMENT
        }
    except (OSError, UnicodeDecodeError, IndentationError, tokenize.TokenError):
        return None


def _added_type_comments(merge_base: str, path: str) -> list[tuple[int, str]]:
    result: subprocess.CompletedProcess[str] = _run_git(
        ["diff", "--unified=0", "--no-ext-diff", merge_base, "--", path]
    )
    comments: list[tuple[int, str]] = []
    comment_lines: set[int] | None = _comment_lines(path)
    line_number: int | None = None
    for line in result.stdout.splitlines():
        hunk: re.Match[str] | None = _DIFF_HUNK_RE.match(line)
        if hunk is not None:
            line_number = int(hunk.group("line"))
            continue
        if line_number is None:
            continue
        if line.startswith("+"):
            comment: re.Match[str] | None = _TYPE_COMMENT_RE.search(line[1:])
            if (
                comment is not None
                and (comment_lines is None or line_number in comment_lines)
                and _is_type_comment(comment.group(0))
            ):
                comments.append((line_number, line[1:]))
            line_number += 1
        elif not line.startswith("-"):
            line_number += 1
    return comments


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
    except subprocess.CalledProcessError as exc:
        print(f"error: failed to compute merge-base against {args.base_ref}: {exc.stderr}", file=sys.stderr)
        return 1

    comments: list[tuple[str, int, str]] = []
    for path in _changed_files(merge_base):
        if not _is_python_file(path):
            continue
        comments.extend((path, line_number, line) for line_number, line in _added_type_comments(merge_base, path))

    if not comments:
        print("OK: no new PEP 484 type comments. Use inline annotations for new types.")
        return 0

    print("ERROR: new PEP 484 type comments found.", file=sys.stderr)
    print(
        "Do not add type comments. Use inline annotations instead:\n"
        "  def f(x: int) -> str: ...\n"
        "  x: int = 1\n"
        "# type: ignore comments are still allowed.",
        file=sys.stderr,
    )
    for path, line_number, line in comments:
        print(f"  {path}:{line_number}: {line}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
