"""Static CVE data loader for SCA detection.

Loads vulnerability targets from a bundled JSON file at startup time.
This emulates the Remote Config payload that will be used in production.
The JSON is loaded once and filtered against the currently installed
package versions so only applicable vulnerabilities are registered.
"""

import json
import os
from typing import Any
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.vendor.packaging.version import InvalidVersion
from ddtrace.vendor.packaging.version import Version


log = get_logger(__name__)

_CVE_DATA_PATH = os.path.join(os.path.dirname(__file__), "_cve_data.json")


def _parse_version_constraint(constraint: str) -> Optional[tuple]:
    """Parse a version constraint string into (operator, version).

    Supports: "<1.2.3", "<=1.2.3", ">1.2.3", ">=1.2.3", "==1.2.3"

    Returns:
        Tuple of (operator_str, Version) or None if parsing fails.
    """
    constraint = constraint.strip()
    for op in ("<=", ">=", "==", "!=", "<", ">", "="):
        if constraint.startswith(op):
            ver_str = constraint[len(op) :].strip()
            try:
                return (op, Version(ver_str))
            except InvalidVersion:
                log.debug("Invalid version in constraint: %s", constraint)
                return None
    # Bare version string — treat as exact match
    try:
        return ("==", Version(constraint))
    except InvalidVersion:
        log.debug("Invalid version string: %s", constraint)
        return None


def _version_matches(installed_version: str, constraint: str) -> bool:
    """Check if an installed version satisfies a version constraint.

    Args:
        installed_version: The installed package version string.
        constraint: Version constraint like "<2.32.0", ">=3.1.3", etc.

    Returns:
        True if the installed version matches the constraint.
    """
    try:
        installed = Version(installed_version)
    except InvalidVersion:
        log.debug("Cannot parse installed version: %s", installed_version)
        return False

    parsed = _parse_version_constraint(constraint)
    if parsed is None:
        return False

    op, target = parsed
    if op == "<":
        return installed < target
    elif op == "<=":
        return installed <= target
    elif op == ">":
        return installed > target
    elif op == ">=":
        return installed >= target
    elif op == "==":
        return installed == target
    elif op == "!=":
        return installed != target
    elif op == "=":
        return installed == target
    return False


def _compound_constraint_matches(installed_version: str, compound: str) -> bool:
    """Check if an installed version satisfies a compound constraint.

    A compound constraint is a comma-separated list of sub-constraints
    that must ALL match (AND logic), e.g. ">=42.2.0, <42.2.28".
    """
    parts = [p.strip() for p in compound.split(",") if p.strip()]
    if not parts:
        return False
    return all(_version_matches(installed_version, part) for part in parts)


def _any_version_matches(installed_version: str, constraints: list[str]) -> bool:
    """Check if an installed version satisfies ANY constraint in the list (OR logic).

    Each constraint may itself be a compound constraint (comma-separated AND).
    """
    if not constraints:
        return False
    return any(_compound_constraint_matches(installed_version, c) for c in constraints)


def load_cve_targets(installed_packages: dict[str, str]) -> list[dict[str, Any]]:
    """Load CVE targets from the static JSON, filtering by installed versions.

    Args:
        installed_packages: Dict mapping package names to installed version
            strings.  Keys should match the dependency_name in the JSON
            (e.g., {"requests": "2.28.0", "urllib3": "1.26.15"}).

    Returns:
        List of target dicts that apply to the installed packages.
        Each dict contains: target, dependency_name, cve_id, line.
    """
    try:
        with open(_CVE_DATA_PATH) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log.debug("Failed to load CVE data from %s: %s", _CVE_DATA_PATH, e)
        return []

    applicable_targets: list[dict[str, Any]] = []

    for entry in data.get("targets", []):
        dep_name = entry.get("dependency_name", "")
        installed_ver = installed_packages.get(dep_name)
        if installed_ver is None:
            # Package not installed — skip
            continue

        constraints = entry.get("package_versions", [])
        if not _any_version_matches(installed_ver, constraints):
            log.debug(
                "Skipping %s: installed %s does not match %s",
                entry.get("vulnerability", {}).get("id", "?"),
                installed_ver,
                constraints,
            )
            continue

        vuln = entry.get("vulnerability", {})
        cve_id = vuln.get("id", "")
        targets = entry.get("targets", [])

        if not targets or not cve_id:
            continue

        for target_name in targets:
            if not target_name:
                continue
            applicable_targets.append(
                {
                    "target": target_name,
                    "dependency_name": dep_name,
                    "cve_id": cve_id,
                }
            )
            log.debug(
                "CVE %s applies to %s %s (constraint %s, target %s)",
                cve_id,
                dep_name,
                installed_ver,
                constraints,
                target_name,
            )

    log.debug("Loaded %d applicable CVE targets out of %d total", len(applicable_targets), len(data.get("targets", [])))
    return applicable_targets
