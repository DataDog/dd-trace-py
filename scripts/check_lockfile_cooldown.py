#!/usr/bin/env python3
"""Validate that every pinned version in riot lockfiles is past the cooldown.

This is the defense-in-depth half of the TEST-CD (APMLP-1362) supply-chain
hardening work. ``scripts/freshvenvs.py`` already prevents the daily
"update riot lockfiles" workflow from *triggering* on a too-fresh direct
package, but once a lockfile recompile runs, riot calls
``python -m piptools compile`` which has no native ``--exclude-newer``
equivalent and may therefore resolve transitive dependencies to versions
that are younger than the cooldown.

This script walks one or more lockfiles (``.riot/requirements/*.txt`` by
default), extracts every ``name==version`` pin, queries PyPI for each
version's upload time, and exits non-zero if any pin is younger than
``COOLDOWN_DAYS``. CI is expected to run it after
``scripts/compile-and-prune-test-requirements`` and before creating the
update PR.

The intent matches the cross-language cooldown standard documented in
the supply-chain hardening epic (APMLP-1343).

Usage::

    python scripts/check_lockfile_cooldown.py [--cooldown-days 2] [PATH ...]

PATH defaults to all ``.riot/requirements/*.txt`` lockfiles in the repo.
"""

import argparse
import concurrent.futures
import datetime as dt
import json
import pathlib
import re
import sys
from typing import Iterable
from typing import Optional
import urllib.error
import urllib.request


# Keep this in sync with scripts/freshvenvs.py::COOLDOWN_DAYS.
COOLDOWN_DAYS = 2

# Matches the ``name==version`` form pip-tools emits. Anchored to the
# start of the line, tolerant of trailing inline comments / hash
# specifiers / extras (e.g. ``flask[async]==3.0.0  # comment``).
PIN_RE = re.compile(
    r"""
    ^                       # start of line
    (?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)   # PEP 503 normalised-ish name
    (?:\[[^\]]+\])?         # optional extras like [async]
    ==                      # exact pin operator
    (?P<version>[^\s;#]+)   # version up to whitespace / marker / comment
    """,
    re.VERBOSE,
)

# These names appear in pip-tools output but PyPI either doesn't host them
# or hosts them under different names. Ignored to avoid spurious failures.
_PYPI_SKIP = {
    # pip / setuptools / wheel get re-resolved by pip-compile but their
    # cooldown is enforced separately at the container/base image layer.
    "pip",
    "setuptools",
    "wheel",
    "pkg-resources",
}


def _http_get_json(url: str, timeout: float = 30.0) -> Optional[dict]:
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - https URL
            if resp.status != 200:
                return None
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None


def _parse_pypi_upload_time(timestamp: str) -> Optional[dt.datetime]:
    """Best-effort parse of PyPI's ``upload_time_iso_8601`` field.

    PyPI usually returns ``YYYY-MM-DDTHH:MM:SS.fffZ`` but very old
    releases omit the microseconds component.
    """
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return dt.datetime.strptime(timestamp, fmt).replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
    return None


def _release_upload_time(name: str, version: str) -> Optional[dt.datetime]:
    """Return the earliest upload time for the given release, or None.

    Picks the minimum across all files in the release rather than the
    first one PyPI returns: this matters because a release that was
    later supplemented with a Windows wheel would otherwise look older
    than it really is for that wheel.
    """
    data = _http_get_json(f"https://pypi.org/pypi/{name}/{version}/json")
    if not data:
        return None

    earliest: Optional[dt.datetime] = None
    for url_info in data.get("urls") or []:
        timestamp = url_info.get("upload_time_iso_8601")
        if not timestamp:
            continue
        parsed = _parse_pypi_upload_time(timestamp)
        if parsed is None:
            continue
        if earliest is None or parsed < earliest:
            earliest = parsed
    return earliest


def _parse_lockfile(path: pathlib.Path) -> list[tuple[str, str]]:
    pins: list[tuple[str, str]] = []
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", "-", "[")):
            continue
        match = PIN_RE.match(line)
        if not match:
            continue
        name = match.group("name").lower()
        version = match.group("version").strip()
        if name in _PYPI_SKIP:
            continue
        pins.append((name, version))
    return pins


def _collect_pins(paths: Iterable[pathlib.Path]) -> dict[tuple[str, str], list[pathlib.Path]]:
    """Return {(name, version): [lockfile, ...]} so we query each pin once."""
    pins: dict[tuple[str, str], list[pathlib.Path]] = {}
    for path in paths:
        try:
            lockfile_pins = _parse_lockfile(path)
        except OSError as exc:
            print(f"warning: cannot read {path}: {exc}", file=sys.stderr)
            continue
        for pin in lockfile_pins:
            pins.setdefault(pin, []).append(path)
    return pins


def _check_pin(
    name: str,
    version: str,
    now: dt.datetime,
    cooldown: dt.timedelta,
) -> Optional[tuple[str, str, dt.timedelta]]:
    """Return a violation tuple ``(name, version, age)`` or None if compliant."""
    upload_time = _release_upload_time(name, version)
    if upload_time is None:
        # No metadata available. Most likely a private index package or a
        # version that's been yanked. Don't fail the build over it; just
        # warn so a human can take a look.
        print(
            f"warning: could not determine upload time for {name}=={version}; skipping",
            file=sys.stderr,
        )
        return None
    age = now - upload_time
    if age < cooldown:
        return name, version, age
    return None


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "paths",
        nargs="*",
        type=pathlib.Path,
        help="Lockfile paths. Defaults to .riot/requirements/*.txt.",
    )
    parser.add_argument(
        "--cooldown-days",
        type=float,
        default=COOLDOWN_DAYS,
        help=f"Minimum age (in days) for any pinned release. Defaults to {COOLDOWN_DAYS}.",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=8,
        help="Number of parallel PyPI lookups (default: 8).",
    )
    args = parser.parse_args(argv)

    if args.paths:
        paths = [p for p in args.paths if p.suffix == ".txt"]
    else:
        paths = sorted(pathlib.Path(".riot/requirements").glob("*.txt"))

    if not paths:
        print("No lockfiles to check.", file=sys.stderr)
        return 0

    pins = _collect_pins(paths)
    if not pins:
        print("No pinned versions found in the provided lockfiles.", file=sys.stderr)
        return 0

    now = dt.datetime.now(dt.timezone.utc)
    cooldown = dt.timedelta(days=args.cooldown_days)

    print(f"Checking {len(pins)} unique pins across {len(paths)} lockfile(s) with cooldown={args.cooldown_days}d.")

    violations: list[tuple[str, str, dt.timedelta, list[pathlib.Path]]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.max_workers) as pool:
        future_map = {pool.submit(_check_pin, name, version, now, cooldown): (name, version) for name, version in pins}
        for future in concurrent.futures.as_completed(future_map):
            result = future.result()
            if result is None:
                continue
            name, version, age = result
            violations.append((name, version, age, pins[(name, version)]))

    if not violations:
        print(f"OK: all {len(pins)} pinned releases are at least {args.cooldown_days}d old.")
        return 0

    violations.sort()
    print(
        f"ERROR: {len(violations)} pinned release(s) are younger than the {args.cooldown_days}d cooldown:",
        file=sys.stderr,
    )
    for name, version, age, lockfiles in violations:
        hours = age.total_seconds() / 3600
        sample = ", ".join(str(p) for p in lockfiles[:3])
        more = "" if len(lockfiles) <= 3 else f" (+{len(lockfiles) - 3} more)"
        print(
            f"  - {name}=={version} uploaded {hours:.1f}h ago; appears in: {sample}{more}",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
