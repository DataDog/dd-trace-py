#!/usr/bin/env python3
"""Identify commits on main where detect-global-locks ran but would be skipped
under the new pr_matches_patterns() guard.

Old behavior: detect-global-locks runs on EVERY commit (unconditional).
New behavior: only runs when changed files match the trigger patterns.

Usage:
    python3 find_skippable_commits.py [--since 2026-01-01] [--until 2026-02-13]
"""
import argparse
import fnmatch
import subprocess
import sys

TRIGGER_PATTERNS = {
    "ddtrace/*",
    "setup.py",
    "setup.cfg",
    "pyproject.toml",
    "src/native/*",
    "scripts/global-lock-detection.py",
    ".gitlab/templates/detect-global-locks.yml",
}


def matches_new_rules(files: list[str]) -> bool:
    """Return True if any file matches the new trigger patterns (using fnmatch)."""
    return any(
        fnmatch.fnmatch(f, p)
        for f in files
        for p in TRIGGER_PATTERNS
    )


def git(*args: str) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", default="2026-01-01", help="Start date (inclusive)")
    parser.add_argument("--until", default="2026-02-13", help="End date (exclusive)")
    parser.add_argument("--output", default="/tmp/ci-analysis-global-locks/skipped_shas.txt")
    args = parser.parse_args()

    shas = git(
        "log", "origin/main", "--first-parent",
        f"--since={args.since}", f"--until={args.until}",
        "--format=%H",
    ).split("\n")
    shas = [s for s in shas if s]
    print(f"Total commits on main ({args.since} to {args.until}): {len(shas)}")

    skipped = []
    still_triggered = []

    for sha in shas:
        files = git("diff-tree", "--no-commit-id", "-r", "--name-only", sha).split("\n")
        files = [f for f in files if f]

        if matches_new_rules(files):
            still_triggered.append(sha)
        else:
            skipped.append(sha)

    print(f"Still triggered (match new patterns): {len(still_triggered)}")
    print(f"Would be skipped: {len(skipped)} ({len(skipped)*100//max(len(shas),1)}% of all commits)")

    # Show a few examples of skipped commits
    if skipped:
        print(f"\nExample skipped commits (first 5):")
        for sha in skipped[:5]:
            msg = git("log", "--format=%s", "-1", sha)
            files = git("diff-tree", "--no-commit-id", "-r", "--name-only", sha).split("\n")
            files = [f for f in files if f]
            print(f"  {sha[:10]} {msg}")
            for f in files[:3]:
                print(f"             {f}")
            if len(files) > 3:
                print(f"             ... +{len(files)-3} more")

    with open(args.output, "w") as f:
        for s in skipped:
            f.write(s + "\n")
    print(f"\nSHAs written to {args.output}")


if __name__ == "__main__":
    main()
