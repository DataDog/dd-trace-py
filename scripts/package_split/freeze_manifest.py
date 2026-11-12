"""Freeze manifest: pins the content of files being extracted to another repo.

The freeze gate loads this file from the *base* revision of a pull request,
so a PR cannot weaken its own rules by editing it. Any change to a frozen
path after the extraction revision is blocked -- no exceptions, no
approved rewrites, no time-limited waivers. To un-freeze a path, remove
its entry from this file in a PR that the freeze gate itself allows
(specifically: a PR that does not touch any other frozen path).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


def load_freeze(path: Path) -> dict[str, Any]:
    """Parse and validate the freeze manifest."""
    from ._toml import tomllib

    data = tomllib.loads(path.read_text())
    if not isinstance(data, dict) or "frozen" not in data:
        raise ValueError("freeze manifest: expected a 'frozen' table")
    entries = data["frozen"]
    if not isinstance(entries, list) or not entries:
        raise ValueError("freeze manifest: expected at least one [[frozen]] entry")

    frozen: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("freeze manifest: each [[frozen]] entry must be a table")
        for field in ("extraction_revision", "source_paths", "hashes"):
            if field not in entry:
                raise ValueError(f"freeze manifest: missing required field '{field}'")
        hashes = entry["hashes"]
        if not isinstance(hashes, dict) or not hashes:
            raise ValueError("freeze manifest: 'hashes' must be a non-empty table")
        for file_path, content_hash in hashes.items():
            if not isinstance(content_hash, str) or len(content_hash) != 64:
                raise ValueError(f"freeze manifest: hash for {file_path} must be a 64-char SHA-256")
            frozen[file_path] = content_hash
    return frozen


def file_hash(path: Path) -> str:
    """Return the SHA-256 content hash of a file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()
