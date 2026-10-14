"""package-freeze.toml: the protected extraction provenance manifest.

Schema v1. One file per repository, describing what was frozen at extraction
time. The freeze gate loads it from the *base* revision (git show) whenever
possible, so a pull request cannot weaken its own rules by editing the file.

::

    schema_version = 1

    [[frozen]]
    destination_repository = "datadog-apm-python"
    extraction_revision = "<git sha>"
    owner = "@DataDog/apm-core-python"
    source_paths = ["ddtrace/internal/ci_visibility"]
    build_inputs = ["pyproject.toml"]

    [frozen.hashes]
    "ddtrace/internal/ci_visibility/__init__.py" = "<sha256>"

    [[frozen.approved_rewrites]]
    path = "ddtrace/internal/ci_visibility/api.py"
    reason = "import shim for the compatibility window"
    approver = "@DataDog/apm-core-python"
    sync_record = "https://github.com/DataDog/datadog-apm-python/pull/1"

    [[freeze_exceptions]]
    path = "ddtrace/internal/ci_visibility/"
    reason = "..."
    owner = "..."
    expiry = "2026-01-01"
    sync_record = "..."
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

from . import FREEZE_SCHEMA_VERSION
from ._toml import tomllib


class FreezeManifestError(Exception):
    def __init__(self, path: Path, issues: list[str]) -> None:
        self.path = path
        self.issues = issues
        super().__init__("invalid freeze manifest %s:\n  - %s" % (path, "\n  - ".join(issues)))


def validate_freeze(data: Any) -> list[str]:
    issues: list[str] = []
    if not isinstance(data, dict):
        return ["freeze manifest: expected a TOML document at the top level"]

    unknown_top = set(data) - {"schema_version", "frozen", "freeze_exceptions"}
    for key in sorted(unknown_top):
        issues.append("unknown top-level table %r" % key)

    version = data.get("schema_version")
    if version != FREEZE_SCHEMA_VERSION:
        issues.append("schema_version must be %d, got %r" % (FREEZE_SCHEMA_VERSION, version))

    frozen_entries = data.get("frozen", [])
    if not isinstance(frozen_entries, list) or not frozen_entries:
        issues.append("at least one [[frozen]] entry is required")
        frozen_entries = []
    for i, entry in enumerate(frozen_entries):
        where = "[[frozen]] #%d" % i
        if not isinstance(entry, dict):
            issues.append("%s: expected a table" % where)
            continue
        for field in ("destination_repository", "extraction_revision", "owner"):
            value = entry.get(field)
            if not isinstance(value, str) or not value.strip():
                issues.append("%s: missing or empty %r" % (where, field))
        paths = entry.get("source_paths")
        if not isinstance(paths, list) or not paths or not all(isinstance(p, str) for p in paths):
            issues.append("%s: source_paths must be a non-empty list of strings" % where)
        hashes = entry.get("hashes")
        if not isinstance(hashes, dict) or not hashes:
            issues.append("%s: [frozen.hashes] with at least one path is required" % where)
        build_inputs = entry.get("build_inputs", [])
        if not isinstance(build_inputs, list) or not all(isinstance(p, str) for p in build_inputs):
            issues.append("%s: build_inputs must be a list of strings" % where)
        rewrites = entry.get("approved_rewrites", [])
        if not isinstance(rewrites, list):
            issues.append("%s: approved_rewrites must be an array of tables" % where)
            rewrites = []
        for j, rewrite in enumerate(rewrites):
            if not isinstance(rewrite, dict):
                issues.append("%s.approved_rewrites[%d]: expected a table" % (where, j))
                continue
            for field in ("path", "reason", "approver"):
                value = rewrite.get(field)
                if not isinstance(value, str) or not value.strip():
                    issues.append("%s.approved_rewrites[%d]: missing or empty %r" % (where, j, field))

    exceptions = data.get("freeze_exceptions", [])
    if not isinstance(exceptions, list):
        issues.append("freeze_exceptions must be an array of tables")
        exceptions = []
    for i, exception in enumerate(exceptions):
        where = "[[freeze_exceptions]] #%d" % i
        if not isinstance(exception, dict):
            issues.append("%s: expected a table" % where)
            continue
        for field in ("path", "reason", "owner", "expiry"):
            value = exception.get(field)
            if not isinstance(value, str) or not value.strip():
                issues.append("%s: missing or empty %r" % (where, field))
                continue
            if field == "expiry":
                try:
                    datetime.date.fromisoformat(value)
                except ValueError:
                    issues.append("%s: expiry must be an ISO date (YYYY-MM-DD)" % where)
        if not isinstance(exception.get("sync_record"), str) or not exception.get("sync_record", "").strip():
            issues.append("%s: missing or empty 'sync_record'" % where)

    return issues


def load_freeze(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    issues = validate_freeze(data)
    if issues:
        raise FreezeManifestError(Path(path), issues)
    return data
