#!/usr/bin/env python3
"""Fail CI when a branch adds deprecated AIDEV-* anchor comment labels.

The guild deprecated AIDEV-NOTE:, AIDEV-TODO:, and AIDEV-QUESTION:
in favor of plain inline comments (see AGENTS.md). Existing anchors are
grandfathered; this check only inspects added diff lines.

Usage:

    python scripts/check_no_new_aidev_anchors.py --base-ref FETCH_HEAD
"""

from __future__ import annotations

import argparse
import re
import subprocess  # nosec B404
import sys
from typing import Optional


# Match comment introducers only — avoids false positives in prose/docs that
# mention the old label name in backticks.
ANCHOR_RE: re.Pattern[str] = re.compile(
    r"^\+\s*.*?(?:#|//)\s*AIDEV-(?:NOTE|TODO|QUESTION):",
)

SKIP_PREFIXES: tuple[str, ...] = ("scripts/check_no_new_aidev_anchors.py",)


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
    result: subprocess.CompletedProcess[str] = subprocess.run(  # nosec B603,B607
        ["git", "diff", "-U0", merge_base, "--", ".", ":(exclude)scripts/check_no_new_aidev_anchors.py"],
        capture_output=True,
        check=True,
        text=True,
    )
    current_file: str = ""
    hits: list[tuple[str, str]] = []
    for line in result.stdout.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:]
            continue
        if not current_file or any(current_file.startswith(prefix) for prefix in SKIP_PREFIXES):
            continue
        if ANCHOR_RE.match(line):
            hits.append((current_file, line[1:].rstrip()))
    return hits


def main(argv: Optional[list[str]] = None) -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base-ref", default="origin/main", help="Git ref to diff against.")
    args: argparse.Namespace = parser.parse_args(argv)

    print(f"Checking for new AIDEV anchors vs base ref: {args.base_ref}")

    try:
        violations: list[tuple[str, str]] = _added_lines(args.base_ref)
    except subprocess.CalledProcessError as exc:
        print(f"error: failed to compute diff against {args.base_ref}: {exc.stderr}", file=sys.stderr)
        return 1

    if not violations:
        print("OK: no new AIDEV-* anchor comments on added lines.")
        return 0

    print(f"ERROR: {len(violations)} new deprecated AIDEV anchor(s) found:", file=sys.stderr)
    for path, content in violations:
        print(f"  - {path}: {content}", file=sys.stderr)
    print(
        "\nUse a plain inline comment instead (see AGENTS.md — Docstrings and Comments).",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
