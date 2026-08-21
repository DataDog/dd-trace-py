#!/usr/bin/env scripts/uv-run-script
# -*- mode: python -*-
# /// script
# requires-python = ">=3.9"
# dependencies = [
#   "packaging==23.1",
# ]
# ///
"""Shared release-tag parsing helpers.

Used by both `scripts/resolve_previous_version.py` and `scripts/compute_version.py`, since both
need to reason about `vX.Y.Z[rcN]` release tags independently of git ancestry - release branches
(`4.12`, `4.13`, ...) are cut from `main` and never merged back, so a release's tag doesn't
necessarily sit on the branch asking about it.
"""

import re
import typing as t

from packaging.version import InvalidVersion
from packaging.version import Version


BRANCH_RE = re.compile(r"^(\d+)\.(\d+)$")


def parse_tag(tag: str) -> t.Optional[Version]:
    """Parse a `vX.Y.Z[rcN]` release tag into a comparable Version, or None.

    >>> parse_tag("v4.13.0rc1") < parse_tag("v4.13.0")
    True
    >>> parse_tag("v4.12.0") < parse_tag("v4.13.0rc1")
    True
    >>> parse_tag("not-a-tag") is None
    True
    """
    if not tag.startswith("v"):
        return None
    try:
        return Version(tag[1:])
    except InvalidVersion:
        return None


def final_releases(tags: list[str]) -> list[tuple[Version, str]]:
    """The (Version, tag) pairs among `tags` that are final (non-prerelease) releases."""
    parsed = [(parse_tag(tag), tag) for tag in tags]
    return [(v, tag) for v, tag in parsed if v is not None and not v.is_prerelease]


def latest_final_release(tags: list[str]) -> t.Optional[str]:
    """The latest final (non-prerelease) tag among `tags`.

    >>> latest_final_release(["v3.18.0", "v4.12.0", "v4.13.0rc1"])
    'v4.12.0'
    >>> latest_final_release(["v4.13.0rc1"]) is None
    True
    """
    finals = final_releases(tags)
    if not finals:
        return None
    return max(finals)[1]


def upper_bound(ref: str) -> Version:
    """A Version greater than every tag that could belong to `ref`.

    `ref` is either an exact release tag (its own version is the bound) or a
    bare release branch name like "4.13" (nothing has necessarily been tagged
    on it yet, so the bound is "anything on this minor line").

    >>> upper_bound("v4.13.0rc2")
    <Version('4.13.0rc2')>
    >>> upper_bound("4.13") > Version("4.13.999999")
    True
    """
    branch_match = BRANCH_RE.match(ref)
    if branch_match:
        major, minor = (int(part) for part in branch_match.groups())
        return Version(f"{major}.{minor}.999999999")
    target = parse_tag(ref)
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
    bound = upper_bound(ref)
    lower = [(v, tag) for v, tag in final_releases(tags) if v < bound]
    if not lower:
        return None
    return max(lower)[1]
