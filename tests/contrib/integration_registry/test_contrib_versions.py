from typing import Dict
from typing import Optional

from packaging.version import Version
import pytest

from ddtrace.contrib.integration_registry.mappings import EXCLUDED_FROM_TESTING


def parse_version_spec(version_spec: str) -> Optional[Version]:
    """Parse a version specification and return the minimum version."""
    version_spec = version_spec.strip()

    if version_spec in (r"\*", "*"):
        return None

    if version_spec.startswith(">="):
        version_str = version_spec[2:].strip()
    elif version_spec.startswith("=="):
        version_str = version_spec[2:].strip()
    else:
        return None

    try:
        return Version(version_str)
    except Exception:
        return None


def get_registry_min_version(registry_entry: dict) -> Optional[Version]:
    """Extract the minimum tested version from a registry entry."""
    if not registry_entry.get("is_tested", False):
        return None

    tested_versions = registry_entry.get("tested_versions_by_dependency", {})
    if not tested_versions:
        return None

    min_versions = []
    for dependency, version_info in tested_versions.items():
        if isinstance(version_info, dict) and "min" in version_info:
            try:
                min_versions.append(Version(version_info["min"]))
            except Exception:
                continue

    return min(min_versions) if min_versions else None


class TestContribVersionAlignment:
    """Test that documented versions align with tested versions."""

    def test_documented_vs_tested_versions(self, documented_versions: Dict[str, str], registry_data: list[dict]):
        """Test that minimum documented versions align with minimum tested versions."""
        registry_by_name = {entry["integration_name"]: entry for entry in registry_data}

        misalignments = []

        for integration_name, doc_version_spec in documented_versions.items():
            registry_entry = registry_by_name.get(integration_name)
            if not registry_entry or not registry_entry.get("is_tested", False):
                continue

            doc_version = parse_version_spec(doc_version_spec)
            registry_min_version = get_registry_min_version(registry_entry)

            if doc_version and registry_min_version and doc_version > registry_min_version:
                misalignments.append(f"{integration_name}: docs={doc_version} > tested={registry_min_version}")

        if misalignments:
            pytest.fail("Version misalignments:\n" + "\n".join(f"  {m}" for m in misalignments))

    def test_tested_integrations_have_version_info(self, registry_data: list[dict]):
        """Test that all tested external integrations have version information."""
        missing = [
            entry["integration_name"]
            for entry in registry_data
            if (
                entry.get("is_tested", False)
                and entry.get("is_external_package", False)
                and not entry.get("tested_versions_by_dependency")
            )
        ]

        if missing:
            pytest.fail(f"Missing version info: {', '.join(sorted(missing))}")

    def test_documented_integrations_are_tested(self, documented_versions: Dict[str, str], registry_data: list[dict]):
        """Test that documented external integrations are tested."""
        registry_by_name = {entry["integration_name"]: entry for entry in registry_data}

        untested = [
            name
            for name in documented_versions
            if (
                name in registry_by_name
                and not registry_by_name[name].get("is_tested", False)
                and name not in EXCLUDED_FROM_TESTING
            )
        ]

        if untested:
            pytest.fail(f"Documented but untested: {', '.join(sorted(untested))}")
