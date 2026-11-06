#!/usr/bin/env python3
"""Fail CI when a branch adds reStructuredText double-backtick markup to
non-Sphinx-rendered prose under tests/ or scripts/.

AGENTS.md ("Docstrings and Comments") splits documentation style on whether
Sphinx renders it: rendered docstrings use reStructuredText, everything else
(private helpers, tests, inline comments) is plain prose, because rST markup
such as double backticks is just stray punctuation to anyone reading the
source in an editor rather than the rendered docs. Nothing under tests/ or
scripts/ is Sphinx-rendered (no automodule directive in docs/ points there),
so double-backtick literals added to those directories are always a
convention violation.

This mirrors repeated code review feedback: human-authored pull requests in
this repository have had reviewers flag newly added ``like-this`` markup in
test and script docstrings/comments and ask for plain prose instead, with
contributors subsequently removing the markup. This check automates that
recurring, low-judgment review comment instead of relying on a human
reviewer catching it every time.

This check only inspects *added* diff lines (not the whole file), the same
incremental approach as scripts/check_no_new_aidev_anchors.py, so pre-existing
double-backtick usage elsewhere in the codebase does not fail the build; only
new occurrences introduced by the current branch are flagged.

Usage:

    python scripts/check_no_rst_markup_in_prose.py --base-ref FETCH_HEAD
"""

from __future__ import annotations

import argparse
import re
import subprocess  # nosec B404
import sys
from typing import Optional


# Matches `` text `` but not triple-or-more backtick fences (for example Markdown
# code fences like ``` that appear legitimately in generated Markdown strings).
RST_DOUBLE_BACKTICK_RE: re.Pattern[str] = re.compile(r"(?<!`)``([^`\n]+?)``(?!`)")

# Only tests/ and scripts/ are in scope: neither directory is Sphinx-rendered (no
# automodule directive under docs/ points at either), so any double-backtick rST
# literal added there is always noise rather than a documentation requirement.
# ddtrace/ is deliberately excluded here because some of its docstrings *are*
# Sphinx-rendered and legitimately use rST markup; distinguishing rendered from
# non-rendered modules there needs the docs/ cross-reference described in
# AGENTS.md and is not a low-false-positive mechanical check.
IN_SCOPE_PREFIXES: tuple[str, ...] = ("tests/", "scripts/")

# This checker and its own test intentionally contain double-backtick examples
# (as fixtures/documentation of the pattern being banned), so they are excluded
# the same way check_no_new_aidev_anchors.py excludes its own files.
EXCLUDED_PATHS: frozenset[str] = frozenset(
    {
        "scripts/check_no_rst_markup_in_prose.py",
        "tests/internal/test_check_no_rst_markup_in_prose.py",
    }
)


def _is_in_scope_path(path: str) -> bool:
    if path in EXCLUDED_PATHS:
        return False
    return path.startswith(IN_SCOPE_PREFIXES)


def _merge_base(base_ref: str) -> str:
    result: subprocess.CompletedProcess[str] = subprocess.run(  # nosec B603, B607
        ["git", "merge-base", base_ref, "HEAD"],
        capture_output=True,
        check=True,
        text=True,
    )
    return result.stdout.strip()


def _has_rst_markup(content: str) -> bool:
    return RST_DOUBLE_BACKTICK_RE.search(content) is not None


def _added_lines(base_ref: str) -> list[tuple[str, str]]:
    merge_base: str = _merge_base(base_ref)
    result: subprocess.CompletedProcess[str] = subprocess.run(  # nosec B603, B607
        ["git", "diff", "-U0", merge_base, "--", "tests", "scripts"],
        capture_output=True,
        check=True,
        text=True,
    )
    current_file: str = ""
    in_hunk: bool = False
    hits: list[tuple[str, str]] = []
    for line in result.stdout.splitlines():
        if line.startswith("diff --git "):
            current_file = ""
            in_hunk = False
            continue
        if line.startswith("+++ b/") and not in_hunk:
            current_file = line[6:]
            continue
        if line.startswith("@@"):
            in_hunk = True
            continue
        if not in_hunk or not line.startswith("+") or line.startswith("+++"):
            continue
        if not _is_in_scope_path(current_file):
            continue
        content: str = line[1:]
        if _has_rst_markup(content):
            hits.append((current_file, content.rstrip()))
    return hits


def main(argv: Optional[list[str]] = None) -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base-ref", default="origin/main", help="Git ref to diff against.")
    args: argparse.Namespace = parser.parse_args(argv)

    print(f"Checking for new rST double-backtick markup under tests/ and scripts/ vs base ref: {args.base_ref}")

    try:
        violations: list[tuple[str, str]] = _added_lines(args.base_ref)
    except subprocess.CalledProcessError as exc:
        print(f"error: failed to compute diff against {args.base_ref}: {exc.stderr}", file=sys.stderr)
        return 1

    if not violations:
        print("OK: no new rST double-backtick markup on added lines under tests/ or scripts/.")
        return 0

    print(f"ERROR: {len(violations)} new rST double-backtick literal(s) found:", file=sys.stderr)
    for path, content in violations:
        print(f"  - {path}: {content}", file=sys.stderr)
    print(
        "\nNothing under tests/ or scripts/ is Sphinx-rendered, so ``like-this`` markup is just "
        "stray punctuation here. Write the identifier as plain text instead "
        "(see AGENTS.md — Docstrings and Comments).",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
