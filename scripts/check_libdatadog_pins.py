#!/usr/bin/env python3
"""Make sure that dd-trace-py uses one libdatadog release tag.

The libdatadog project releases all its crates together. For this reason,
every git dependency on libdatadog must use the same release tag ("vX.Y.Z").
A commit SHA, a branch or the default branch is not permitted. The script
reads all tracked Cargo.toml and Cargo.lock files with a TOML parser, and
makes sure that:

- Each libdatadog git dependency in a Cargo.toml sets rev or tag to a release tag.
- Each libdatadog source in a Cargo.lock uses rev or tag with a release tag.
- All Cargo.lock files together have exactly one libdatadog source.
- The release tag in the Cargo.toml files is the same as in the Cargo.lock files.

Usage:

    python scripts/check_libdatadog_pins.py
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path
import re
import subprocess  # nosec B404
import sys
from typing import Any
from typing import Optional
from urllib.parse import urlsplit

import tomllib


ROOT = Path(__file__).resolve().parents[1]
RELEASE_TAG = re.compile(r"v\d+\.\d+\.\d+")


def is_libdatadog(url: Any) -> bool:
    """Return True if url is the libdatadog GitHub repository.

    >>> is_libdatadog("https://github.com/DataDog/libdatadog")
    True
    >>> is_libdatadog("ssh://git@github.com/datadog/libdatadog.git/")
    True
    >>> is_libdatadog("git+https://github.com/DataDog/libdatadog?rev=v49.0.0#34293ad2")
    True
    >>> is_libdatadog("https://github.com/DataDog/libdatadog-rb")
    False
    >>> is_libdatadog("registry+https://github.com/rust-lang/crates.io-index")
    False
    """
    if not isinstance(url, str):
        return False
    parts = urlsplit(url)
    return (
        parts.hostname == "github.com" and parts.path.lower().rstrip("/").removesuffix(".git") == "/datadog/libdatadog"
    )


def manifest_pins(table: dict[str, Any], prefix: str = "") -> Iterator[tuple[str, Optional[str]]]:
    """Yield the key and the rev or tag of each libdatadog git dependency in a parsed Cargo.toml.

    All tables are examined. This includes the target, workspace and patch tables.

    >>> doc = tomllib.loads('''
    ... [dependencies]
    ... a = { git = 'https://github.com/DataDog/libdatadog', tag = 'v1.0.0' }  # rev = "v0.0.1"
    ... b = { git = "https://github.com/DataDog/libdatadog", branch = "main" }
    ... c = { git = "https://github.com/DataDog/other", rev = "0be9d869" }
    ... [target.'cfg(unix)'.dependencies.d]
    ... git = "https://github.com/DataDog/libdatadog.git"
    ... rev = "0be9d869"
    ... [patch.crates-io]
    ... e = { git = "https://github.com/DataDog/libdatadog" }
    ... ''')
    >>> for key, pin in manifest_pins(doc):
    ...     print(key, pin)
    dependencies.a v1.0.0
    dependencies.b None
    target.cfg(unix).dependencies.d 0be9d869
    patch.crates-io.e None
    """
    for key, value in table.items():
        if not isinstance(value, dict):
            continue
        if is_libdatadog(value.get("git")):
            yield prefix + key, value.get("tag", value.get("rev"))
        else:
            yield from manifest_pins(value, f"{prefix}{key}.")


def lock_pin(source: str) -> Optional[str]:
    """Return the rev or tag of a Cargo.lock git source, or None if it uses a branch or the default branch.

    >>> lock_pin("git+https://github.com/DataDog/libdatadog?rev=v49.0.0#34293ad2")
    'v49.0.0'
    >>> lock_pin("git+https://github.com/DataDog/libdatadog?tag=v49.0.0#34293ad2")
    'v49.0.0'
    >>> lock_pin("git+https://github.com/DataDog/libdatadog?branch=main#34293ad2") is None
    True
    >>> lock_pin("git+https://github.com/DataDog/libdatadog#34293ad2") is None
    True
    """
    key, _, value = urlsplit(source).query.partition("=")
    return value if key in ("rev", "tag") else None


def main() -> int:
    errors: list[str] = []
    manifest_tags: set[str] = set()
    # Each libdatadog source and the Cargo.lock files that use it.
    lock_sources: defaultdict[str, set[str]] = defaultdict(set)

    files = subprocess.run(  # nosec B603, B607
        ["git", "ls-files", "-z", "--", "*Cargo.toml", "*Cargo.lock"],
        cwd=ROOT,
        capture_output=True,
        check=True,
        text=True,
    ).stdout.split("\0")
    for name in filter(None, files):
        path = ROOT / name
        doc = tomllib.loads(path.read_text(encoding="utf-8"))
        if path.name == "Cargo.toml":
            for key, pin in manifest_pins(doc):
                if pin and RELEASE_TAG.fullmatch(pin):
                    manifest_tags.add(pin)
                else:
                    errors.append(f'{name}: {key}: set rev or tag to a libdatadog release tag "vX.Y.Z", not {pin!r}')
        elif path.name == "Cargo.lock":
            for package in doc.get("package", []):
                if is_libdatadog(package.get("source")):
                    lock_sources[package["source"]].add(name)

    lock_tags: set[str] = set()
    for source, lockfiles in sorted(lock_sources.items()):
        pin = lock_pin(source)
        if pin and RELEASE_TAG.fullmatch(pin):
            lock_tags.add(pin)
        else:
            errors.append(f"{', '.join(sorted(lockfiles))}: libdatadog source does not use a release tag: {source}")
    if len(lock_sources) != 1:
        errors.append(f"Cargo.lock files must have exactly one libdatadog source, found: {sorted(lock_sources)}")
    if manifest_tags != lock_tags:
        errors.append(
            f"Cargo.toml release tags {sorted(manifest_tags)} are not the same as "
            f"Cargo.lock release tags {sorted(lock_tags)}"
        )

    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"OK: libdatadog source is {next(iter(lock_sources))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
