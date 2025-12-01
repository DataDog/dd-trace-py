#!/usr/bin/env scripts/uv-run-script
# -*- mode: python -*-
# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "packaging>=21.0",
# ]
# ///
"""
Detects active release branches for nightly CI monitoring.

Returns the most recent N minor version branches (e.g., 4.0, 3.19, 3.18)
PLUS the last minor version of the previous major (e.g., if current major is 4.x,
includes the last 3.x release like 3.19, but not 2.x).

Used by .github/workflows/nightly-branch-monitor.yml to dynamically determine
which branches to validate nightly.
"""

import json
import re
import subprocess
import sys

from packaging.version import Version


BRANCH_PATTERN = re.compile(r"^refs/heads/(\d+\.\d+)$")
ACTIVE_BRANCH_COUNT = 3


def get_remote_branches():
    """Query remote branches matching version pattern."""
    try:
        cmd = ["git", "ls-remote", "--heads", "origin", "refs/heads/[0-9]*.[0-9]*"]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error querying remote branches: {e}", file=sys.stderr)
        sys.exit(1)

    branches = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        # Format: "<sha>  refs/heads/3.19"
        parts = line.split("\t")
        if len(parts) != 2:
            continue
        ref = parts[1]
        match = BRANCH_PATTERN.match(ref)
        if match:
            branches.append(match.group(1))

    return branches


def filter_active_branches(branches):
    """
    Return most recent N minor versions PLUS last minor of previous major.

    Example:
    - If branches are [4.2, 4.1, 4.0, 3.19, 3.18, 3.17, 2.21]
    - Top 3: [4.2, 4.1, 4.0] (all major 4)
    - Current major: 4
    - Previous major: 3
    - Last minor of major 3: 3.19
    - Result: [4.2, 4.1, 4.0, 3.19]
    """
    if not branches:
        return []

    # Sort by version (descending)
    try:
        sorted_branches = sorted(branches, key=Version, reverse=True)
    except Exception as e:
        print(f"Error sorting branches: {e}", file=sys.stderr)
        sys.exit(1)

    # Take most recent N
    top_n = sorted_branches[:ACTIVE_BRANCH_COUNT]

    # Determine current major version (from the highest version)
    current_major = Version(sorted_branches[0]).major

    # Determine previous major version
    previous_major = current_major - 1

    # Find the last minor version of the previous major
    last_of_previous_major = None
    for branch in sorted_branches:
        version = Version(branch)
        if version.major == previous_major:
            last_of_previous_major = branch
            break

    # Add last minor of previous major if not already included
    if last_of_previous_major and last_of_previous_major not in top_n:
        result = top_n + [last_of_previous_major]
        print(
            f"\nAdded last minor of previous major: {last_of_previous_major} "
            f"(current major: {current_major}, previous major: {previous_major})",
            file=sys.stderr,
        )
    else:
        result = top_n

    return result


def main():
    branches = get_remote_branches()
    active = filter_active_branches(branches)

    # GitHub Actions output format
    print(f"branches={json.dumps(active)}")
    print(f"count={len(active)}")

    # Also print summary for debugging
    print(f"\nDetected {len(active)} active branches:", file=sys.stderr)
    for branch in active:
        print(f"  - {branch}", file=sys.stderr)


if __name__ == "__main__":
    main()
