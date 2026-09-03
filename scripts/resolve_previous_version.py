#!/usr/bin/env scripts/uv-run-script
# -*- mode: python -*-
# /// script
# requires-python = ">=3.9"
# dependencies = [
#   "packaging==23.1",
# ]
# ///
"""Resolve the release that immediately precedes a given ref, by version number.

Release branches (`4.12`, `4.13`, ...) are cut from `main` and never merged
back, and final tags are cut on those branches rather than on `main`. That
means "what shipped right before this" can't be resolved via git ancestry
(`git describe`, `git merge-base`) - a branch's own tag history simply
doesn't contain the tags this needs. It has to be a plain version-number
comparison over every tag in the repo, independent of which branch/commit
each tag happens to sit on.

Every ref belonging to a minor line that hasn't shipped a final release yet
(a raw branch push, an rc tag, or the eventual final tag) resolves to the
same answer: the latest final release of the *prior* minor. This is
deliberate, not an approximation - resolving every rc/final in the 4.13 cycle
to v4.12.0 (rather than chaining rc2 against rc1, rc1 against v4.12.0) means
callers always get "what's actually shipped", so small regressions can't
compound silently across a chain of rc's. A patch release (`ref` already has
final releases below it in its own minor) instead resolves to the immediately
preceding patch, once one exists.

Usage:
    git tag -l | scripts/resolve_previous_version.py <ref>

Where <ref> is one of:
    main            -> latest final (non-prerelease) release, e.g. v4.12.0
    v4.13.0rc2      -> a release tag; resolves to the prior minor's latest
                       final release, e.g. v4.12.0
    4.13            -> a bare release branch; same rule, e.g. v4.12.0

Prints the resolved tag to stdout, or nothing if no suitable tag exists.
"""

from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).parent))

from version_lib import latest_final_release
from version_lib import nearest_final_release


if __name__ == "__main__":
    ref = sys.argv[1]
    tags = [line.strip() for line in sys.stdin if line.strip()]
    result = latest_final_release(tags) if ref == "main" else nearest_final_release(tags, ref)
    if result:
        print(result)
