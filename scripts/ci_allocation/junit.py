"""Verify collected-test and Riot metadata parity across allocation strategies."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import typing as t
import xml.etree.ElementTree as ET

from .planner import AllocationError


JUNIT_IDENTITY = re.compile(
    r"(?:^|/)junit\.(legacy|balanced)\.([^.]+)(?:\.s([1-9][0-9]*)of([1-9][0-9]*))?"
    r"(?:\.([0-9a-f]{64}))?\.\d+\.xml$"
)
REQUIRED_EXECUTION_PROPERTIES = {"riot.python.version"}
PARTITION_PROPERTIES = {"riot.test.shard_index", "riot.test.shard_total"}


def _execution_digest(execution: t.Mapping[str, str]) -> str:
    encoded = json.dumps(dict(sorted(execution.items())), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _test_suites(root: ET.Element) -> list[ET.Element]:
    if root.tag == "testsuite":
        return [root]
    return list(root.iter("testsuite"))


def collect_junit(paths: t.Iterable[Path], expected_strategy: str) -> tuple[Counter[tuple[str, ...]], dict[str, str]]:
    """Collect test identities and execution metadata from JUnit XML artifacts."""
    identities: Counter[tuple[str, ...]] = Counter()
    metadata: dict[str, str] = {}
    seen_files = 0
    for path in paths:
        seen_files += 1
        try:
            root = ET.parse(path).getroot()
        except (ET.ParseError, OSError) as exc:
            raise AllocationError(f"cannot read JUnit artifact {path}: {exc}") from exc
        filename_identity = JUNIT_IDENTITY.search(path.as_posix())
        if filename_identity and filename_identity.group(1) != expected_strategy:
            raise AllocationError(f"JUnit filename strategy does not match {expected_strategy}: {path}")
        for suite in _test_suites(root):
            properties = {
                str(item.get("name")): str(item.get("value", ""))
                for item in suite.findall("./properties/property")
                if item.get("name")
            }
            riot_hash = properties.get("riot.hash") or (filename_identity.group(2) if filename_identity else None)
            if not riot_hash:
                raise AllocationError(f"JUnit suite in {path} is missing riot.hash")
            embedded_strategy = properties.get("riot.ci.allocation_strategy")
            if embedded_strategy and embedded_strategy != expected_strategy:
                raise AllocationError(f"JUnit strategy does not match {expected_strategy}: {path}")
            embedded_index = properties.get("riot.test.shard_index")
            embedded_total = properties.get("riot.test.shard_total")
            if bool(embedded_index) != bool(embedded_total):
                raise AllocationError(f"JUnit runtime shard identity is incomplete: {path}")
            if filename_identity and filename_identity.group(3):
                if (embedded_index, embedded_total) != (filename_identity.group(3), filename_identity.group(4)):
                    raise AllocationError(f"JUnit runtime shard identity does not match {path}")
            execution = {
                key: value
                for key, value in properties.items()
                if key.startswith("riot.")
                and key not in {"riot.hash", "riot.ci.allocation_strategy", *PARTITION_PROPERTIES}
            }
            filename_digest = filename_identity.group(5) if filename_identity else None
            if execution:
                missing_properties = REQUIRED_EXECUTION_PROPERTIES - set(execution)
                if missing_properties:
                    raise AllocationError(
                        f"JUnit suite in {path} is missing Riot execution metadata: {sorted(missing_properties)}"
                    )
                execution_digest = _execution_digest(execution)
                if filename_digest and filename_digest != execution_digest:
                    raise AllocationError(f"JUnit filename execution metadata does not match {path}")
            elif filename_digest:
                execution_digest = filename_digest
            else:
                raise AllocationError(f"JUnit suite in {path} has no Riot execution metadata evidence")
            if riot_hash in metadata and metadata[riot_hash] != execution_digest:
                raise AllocationError(f"JUnit execution metadata is inconsistent for Riot hash {riot_hash}")
            metadata[riot_hash] = execution_digest
            for case in suite.findall("./testcase"):
                identities[
                    (
                        riot_hash,
                        str(case.get("classname", "")),
                        str(case.get("name", "")),
                        str(case.get("file", "")),
                    )
                ] += 1
    if not seen_files:
        raise AllocationError(f"no {expected_strategy} JUnit artifacts were provided")
    return identities, metadata


def verify_junit_parity(legacy_paths: t.Iterable[Path], balanced_paths: t.Iterable[Path]) -> dict[str, t.Any]:
    legacy, legacy_metadata = collect_junit(legacy_paths, "legacy")
    balanced, balanced_metadata = collect_junit(balanced_paths, "balanced")
    if legacy != balanced:
        missing = list((legacy - balanced).elements())[:10]
        unexpected = list((balanced - legacy).elements())[:10]
        raise AllocationError(f"JUnit test identity parity failed: missing={missing}, unexpected={unexpected}")
    if legacy_metadata != balanced_metadata:
        raise AllocationError("JUnit Riot execution metadata parity failed")

    normalized = sorted((identity, count) for identity, count in legacy.items())
    digest = hashlib.sha256(json.dumps(normalized, separators=(",", ":")).encode()).hexdigest()
    return {
        "schema_version": 1,
        "kind": "junit-allocation-parity",
        "test_identity_count": sum(legacy.values()),
        "riot_hash_count": len(legacy_metadata),
        "test_identity_sha256": digest,
        "exact_multiset_parity": True,
        "execution_metadata_parity": True,
    }
