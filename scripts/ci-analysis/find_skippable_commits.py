#!/usr/bin/env python3
"""Identify commits on main that triggered profiling_native under the OLD rules
but would NOT trigger under the new extension-based rules.

Usage:
    python3 find_skippable_commits.py [--since 2026-01-01] [--until 2026-02-13]

Outputs:
    - Summary to stdout
    - /tmp/skipped_shas.txt with one SHA per line (consumed by fetch_job_durations.py)
"""
import argparse
import subprocess
import sys


# Old trigger patterns (broad directory globs from .gitlab-ci.yml before this PR)
OLD_TRIGGER_PREFIXES = [
    "ddtrace/internal/datadog/profiling/",
    "ddtrace/profiling/",
    "src/native/",
    "tests/profiling/",
    ".gitlab-ci.yml",
]

# New trigger patterns: extension-based allowlist
NATIVE_EXTS = {".cpp", ".cc", ".h", ".hpp", ".pyx", ".cmake", ".sh", ".supp"}
NATIVE_EXACT_NAMES = {"CMakeLists.txt"}
TOP_LEVEL_TRIGGERS = {"setup.py", "pyproject.toml", ".gitlab-ci.yml"}


def matches_old(path: str) -> bool:
    return any(path.startswith(p) or path == p for p in OLD_TRIGGER_PREFIXES)


def matches_new(path: str) -> bool:
    if path in TOP_LEVEL_TRIGGERS:
        return True
    if path.startswith("ddtrace/internal/datadog/profiling/") or path.startswith("ddtrace/profiling/"):
        basename = path.rsplit("/", 1)[-1]
        ext = ("." + basename.rsplit(".", 1)[-1]) if "." in basename else ""
        if ext in NATIVE_EXTS or basename in NATIVE_EXACT_NAMES:
            return True
    return False


def git(*args: str) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", default="2026-01-01", help="Start date (inclusive)")
    parser.add_argument("--until", default="2026-02-13", help="End date (exclusive)")
    parser.add_argument("--output", default="/tmp/skipped_shas.txt", help="Output file for SHAs")
    args = parser.parse_args()

    shas = git(
        "log", "origin/main", "--first-parent",
        f"--since={args.since}", f"--until={args.until}",
        "--format=%H",
    ).split("\n")
    shas = [s for s in shas if s]
    print(f"Total commits on main ({args.since} to {args.until}): {len(shas)}")

    old_triggered = []
    skipped = []

    for sha in shas:
        files = git("diff-tree", "--no-commit-id", "-r", "--name-only", sha).split("\n")
        files = [f for f in files if f]

        old_match = any(matches_old(f) for f in files)
        new_match = any(matches_new(f) for f in files)

        if old_match:
            old_triggered.append(sha)
            if not new_match:
                skipped.append(sha)

    print(f"Triggered by old rules: {len(old_triggered)}")
    print(f"Would be skipped by new rules: {len(skipped)} ({len(skipped)*100//max(len(old_triggered),1)}% of triggered)")

    with open(args.output, "w") as f:
        for s in skipped:
            f.write(s + "\n")
    print(f"SHAs written to {args.output}")


if __name__ == "__main__":
    main()
