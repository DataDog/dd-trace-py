"""
Updates the supported version fields in scripts/integration_registry/registry.yaml based on
supported_versions.json.
Preserves all other existing fields in registry.yaml.
"""

from collections import defaultdict
import json
import os
import pathlib
import sys
from typing import Optional

from packaging.version import InvalidVersion
from packaging.version import parse as parse_version


# Add scripts/integration_registry/ to the Python path so registry modules are importable
_integration_registry_dir = os.path.dirname(__file__)
if _integration_registry_dir not in sys.path:
    sys.path.insert(0, _integration_registry_dir)

from registry_update_helpers.integration_registry_updater import IntegrationRegistryUpdater  # noqa: E402


SCRIPT_DIR = pathlib.Path(__file__).parent.parent.resolve()
PROJECT_ROOT = SCRIPT_DIR.parent.resolve()
REGISTRY_YAML_PATH = PROJECT_ROOT / "scripts" / "integration_registry" / "registry.yaml"
SUPPORTED_VERSIONS_JSON_PATH = PROJECT_ROOT / "supported_versions.json"


def _normalize_version_string(v_str: str) -> str:
    """Ensures version string has MAJOR.MINOR.PATCH, adding .0 if needed."""
    try:
        if not v_str or not v_str[0].isdigit():
            return v_str
        v = parse_version(v_str)
        if v.micro is None:
            if v.minor is None:
                return f"{v.major}.0.0"
            return f"{v.major}.{v.minor}.0"
        parts = v_str.split(".")
        if len(parts) < 3:
            if len(parts) == 1:
                return f"{v.major}.0.0"
            if len(parts) == 2:
                return f"{v.major}.{v.minor}.0"
        return v_str
    except InvalidVersion:
        return v_str


def _version_key(version: str):
    try:
        return parse_version(version)
    except InvalidVersion:
        return parse_version("0")


def _aggregate_python_version_bounds(python_versions: dict) -> tuple[str, str]:
    minimum_versions = []
    maximum_versions = []
    for python_version_data in python_versions.values():
        minimum_version = python_version_data.get("minimum_package_version", "")
        maximum_version = python_version_data.get("maximum_package_version", "")
        if minimum_version:
            minimum_versions.append(minimum_version)
        if maximum_version:
            maximum_versions.append(maximum_version)

    if not minimum_versions or not maximum_versions:
        return "N/A", "N/A"

    return min(minimum_versions, key=_version_key), max(maximum_versions, key=_version_key)


def _read_supported_versions(filepath: pathlib.Path) -> Optional[dict[str, dict[str, dict[str, str]]]]:
    """
    Reads supported_versions.json (potentially multiple entries per integration),
    returning {integration: {dependency: {'min': str, 'max': str}}} or None on error.
    """
    supported_data: dict[str, dict[str, dict[str, str]]] = defaultdict(dict)

    print(f"Reading NEW dependency-specific supported versions from: {filepath.relative_to(PROJECT_ROOT)}")
    try:
        entries = json.loads(filepath.read_text())
        for entry in entries:
            integration_name = entry.get("integration", "").strip().lower()
            dependency_name = entry.get("dependency", "").strip()
            min_version, max_version = _aggregate_python_version_bounds(entry.get("python_versions", {}))

            supported_data[integration_name][dependency_name] = {
                "min": _normalize_version_string(min_version) if min_version else "N/A",
                "max": _normalize_version_string(max_version) if max_version else "N/A",
            }

    except Exception as e:
        print(f"Error reading supported versions file {filepath.relative_to(PROJECT_ROOT)}: {e}", file=sys.stderr)
        return None

    print(f"Loaded supported version info for {len(supported_data)} integrations from {filepath.name}.")
    return dict(supported_data)


def main() -> int:
    """Reads existing registry, updates versions from JSON, adds entries, writes back, formats."""
    print("\n")
    new_supported_versions = _read_supported_versions(SUPPORTED_VERSIONS_JSON_PATH)
    print("-" * 120)

    print(f"Loading existing registry data from: {REGISTRY_YAML_PATH.relative_to(PROJECT_ROOT)}")

    updater = IntegrationRegistryUpdater()
    updater.load_registry_data()

    print(f"Loaded {len(updater.integrations)} existing integration entries.")

    # update the integrations with the new supported versions
    added_count, updated_count = updater.merge_data(new_supported_versions)

    print(f"\nUpdate summary: {updated_count} integration(s) had version maps updated/added.")
    print(f"                {added_count} new integration(s) added.")

    if not updater.write_registry_data():
        return 1

    print("\n--- Version Update Workflow Completed Successfully ---")
    print("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
