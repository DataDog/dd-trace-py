#!/usr/bin/env python3
"""Fail CI when a branch adds deprecated anchor comment labels.

The guild deprecated named anchor labels in favor of plain inline comments
(see AGENTS.md). Existing anchors are grandfathered; this check only
inspects added diff lines.

Usage:

    python scripts/check_no_new_aidev_anchors.py --base-ref FETCH_HEAD
"""

from __future__ import annotations

import argparse
import re
import subprocess  # nosec B404
import sys
from typing import Optional


# Deliberately match any occurrence in added lines. False positives are
# preferable to allowing a new deprecated label to bypass the check.
ANCHOR_RE: re.Pattern[str] = re.compile(r"AIDE" r"V")
STRING_RE: re.Pattern[str] = re.compile(r"""(["'`])(?:\\.|(?!\1).)*\1""")
TRIPLE_STRING_RE: re.Pattern[str] = re.compile(r'^\s*[rRuUbBfF]{0,2}(?:\'\'\'|""").*AIDE' r"V")

# Paths allowed to mention deprecated anchors in added lines: policy docs and the
# checker's own tests (fixtures intentionally contain anchor strings).
EXCLUDED_PATHS: frozenset[str] = frozenset(
    {
        "AGENTS.md",
        "scripts/check_no_new_aidev_anchors.py",
        "tests/internal/test_check_no_new_aidev_anchors.py",
    }
)
EXCLUDED_PREFIXES: tuple[str, ...] = (".cursor/rules/",)


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


def _added_lines(base_ref: str) -> list[tuple[str, str]]:
    merge_base: str = _merge_base(base_ref)
    result: subprocess.CompletedProcess[str] = subprocess.run(  # nosec B603, B607
        ["git", "diff", "-U0", merge_base, "--", "."],
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
        if not in_hunk or not line.startswith("+"):
            continue
        if _is_excluded_path(current_file):
            continue
        content: str = line[1:]
        if ANCHOR_RE.search(content) or _is_anchor_line(line):
            hits.append((current_file, content.rstrip()))
    return hits


def _is_anchor_line(line: str) -> bool:
    content: str = line[1:] if line.startswith("+") else line
    if TRIPLE_STRING_RE.search(content) is not None:
        return True
    content_without_strings: str = STRING_RE.sub("", content)
    return ANCHOR_RE.search(content_without_strings) is not None


def main(argv: Optional[list[str]] = None) -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base-ref", default="origin/main", help="Git ref to diff against.")
    args: argparse.Namespace = parser.parse_args(argv)

    print(f"Checking for new deprecated anchors vs base ref: {args.base_ref}")

    try:
        violations: list[tuple[str, str]] = _added_lines(args.base_ref)
    except subprocess.CalledProcessError as exc:
        print(f"error: failed to compute diff against {args.base_ref}: {exc.stderr}", file=sys.stderr)
        return 1

    if not violations:
        print("OK: no new deprecated anchor comments on added lines.")
        return 0

    print(f"ERROR: {len(violations)} new deprecated anchor(s) found:", file=sys.stderr)
    for path, content in violations:
        print(f"  - {path}: {content}", file=sys.stderr)
    print(
        "\nUse a plain inline comment instead (see AGENTS.md — Docstrings and Comments).",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
