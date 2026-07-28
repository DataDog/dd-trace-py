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

import re
import sys
import typing as t

from packaging.version import InvalidVersion
from packaging.version import Version


_BRANCH_RE = re.compile(r"^(\d+)\.(\d+)$")


def _parse_tag(tag: str) -> t.Optional[Version]:
    """Parse a `vX.Y.Z[rcN]` release tag into a comparable Version, or None.

    >>> _parse_tag("v4.13.0rc1") < _parse_tag("v4.13.0")
    True
    >>> _parse_tag("v4.12.0") < _parse_tag("v4.13.0rc1")
    True
    >>> _parse_tag("not-a-tag") is None
    True
    """
    if not tag.startswith("v"):
        return None
    try:
        return Version(tag[1:])
    except InvalidVersion:
        return None


def _final_releases(tags: list[str]) -> list[tuple[Version, str]]:
    """The (Version, tag) pairs among `tags` that are final (non-prerelease) releases."""
    parsed = [(_parse_tag(tag), tag) for tag in tags]
    return [(v, tag) for v, tag in parsed if v is not None and not v.is_prerelease]


def latest_final_release(tags: list[str]) -> t.Optional[str]:
    """The latest final (non-prerelease) tag among `tags`.

    >>> latest_final_release(["v3.18.0", "v4.12.0", "v4.13.0rc1"])
    'v4.12.0'
    >>> latest_final_release(["v4.13.0rc1"]) is None
    True
    """
    finals = _final_releases(tags)
    if not finals:
        return None
    return max(finals)[1]


def _upper_bound(ref: str) -> Version:
    """A Version greater than every tag that could belong to `ref`.

    `ref` is either an exact release tag (its own version is the bound) or a
    bare release branch name like "4.13" (nothing has necessarily been tagged
    on it yet, so the bound is "anything on this minor line").

    >>> _upper_bound("v4.13.0rc2")
    <Version('4.13.0rc2')>
    >>> _upper_bound("4.13") > Version("4.13.999999")
    True
    """
    branch_match = _BRANCH_RE.match(ref)
    if branch_match:
        major, minor = (int(part) for part in branch_match.groups())
        return Version(f"{major}.{minor}.999999999")
    target = _parse_tag(ref)
    if target is None:
        raise ValueError(f"Not a recognized release tag or branch: {ref!r}")
    return target


def nearest_final_release(tags: list[str], ref: str) -> t.Optional[str]:
    """The latest already-shipped final release strictly below `ref`'s version.

    Only final releases count as candidates - never rc's - so every rc in an
    unreleased minor line anchors to the same baseline as the eventual final
    tag (the prior minor's latest release), rather than chaining rc2 against
    rc1 and letting small regressions compound silently. A patch release
    (`ref` already has final releases below it in its own minor) instead
    anchors to the immediately preceding patch, once one exists.

    >>> tags = ["v3.18.0", "v4.10.10", "v4.11.0", "v4.11.1", "v4.12.0", "v4.13.0rc1"]
    >>> nearest_final_release(tags, "4.13")
    'v4.12.0'
    >>> nearest_final_release(tags, "v4.13.0rc1")
    'v4.12.0'
    >>> nearest_final_release(tags + ["v4.13.0rc2"], "v4.13.0rc2")
    'v4.12.0'
    >>> nearest_final_release(tags + ["v4.13.0rc2", "v4.13.0"], "v4.13.0")
    'v4.12.0'
    >>> nearest_final_release(tags, "v4.11.2")
    'v4.11.1'
    >>> nearest_final_release([], "4.13") is None
    True
    """
    upper_bound = _upper_bound(ref)
    lower = [(v, tag) for v, tag in _final_releases(tags) if v < upper_bound]
    if not lower:
        return None
    return max(lower)[1]


if __name__ == "__main__":
    ref = sys.argv[1]
    tags = [line.strip() for line in sys.stdin if line.strip()]
    result = latest_final_release(tags) if ref == "main" else nearest_final_release(tags, ref)
    if result:
        print(result)
