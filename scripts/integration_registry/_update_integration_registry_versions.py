"""
Updates the supported version fields in ddtrace/contrib/integration_registry/registry.yaml based on
supported_versions_table.csv.
Preserves all other existing fields in registry.yaml.
"""

from collections import defaultdict
import csv
import os
import pathlib
import sys
from typing import Dict
from typing import Optional

from packaging.version import InvalidVersion
from packaging.version import parse as parse_version


# Add the project root to the Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from tests.contrib.integration_registry.registry_update_helpers.integration_registry_updater import (  # noqa: E402
    IntegrationRegistryUpdater,  # noqa: E402
)


SCRIPT_DIR = pathlib.Path(__file__).parent.parent.resolve()
PROJECT_ROOT = SCRIPT_DIR.parent.resolve()
REGISTRY_YAML_PATH = PROJECT_ROOT / "ddtrace" / "contrib" / "integration_registry" / "registry.yaml"
SUPPORTED_VERSIONS_CSV_PATH = PROJECT_ROOT / "supported_versions_table.csv"


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


def _read_supported_versions(filepath: pathlib.Path) -> Optional[Dict[str, Dict[str, Dict[str, str]]]]:
    """
    Reads the supported versions CSV (potentially multiple rows per integration),
    returning {integration: {dependency: {'min': str, 'max': str}}} or None on error.
    """
    supported_data: Dict[str, Dict[str, Dict[str, str]]] = defaultdict(dict)

    print(f"Reading NEW dependency-specific supported versions from: {filepath.relative_to(PROJECT_ROOT)}")
    try:
        with open(filepath, "r", newline="", encoding="utf-8") as csvfile:
            header = next(csv.reader(csvfile))
            csvfile.seek(0)
            reader = csv.DictReader(csvfile)
            col_integration = next((h for h in header if "integration" in h.lower()), None)
            col_dependency = next(
                (h for h in header if "dependency" in h.lower() and h.lower() != col_integration.lower()), None
            )
            col_min = next((h for h in header if "minimum" in h.lower()), None)
            col_max = next((h for h in header if "max" in h.lower()), None)

            for row_num, row in enumerate(reader, 2):
                integration_name_raw = row.get(col_integration, "").strip()
                dependency_name_raw = row.get(col_dependency, "").strip()
                min_version = row.get(col_min, "").strip()
                max_version = row.get(col_max, "").strip()
                integration_name = integration_name_raw.split("*")[0].strip().lower()
                dependency_name = dependency_name_raw

                normalized_min = _normalize_version_string(min_version) if min_version else "N/A"
                normalized_max = _normalize_version_string(max_version) if max_version else "N/A"

                supported_data[integration_name][dependency_name] = {"min": normalized_min, "max": normalized_max}

    except Exception as e:
        print(f"Error reading supported versions file {filepath.relative_to(PROJECT_ROOT)}: {e}", file=sys.stderr)
        return None

    print(f"Loaded supported version info for {len(supported_data)} integrations from {filepath.name}.")
    return dict(supported_data)


def main() -> int:
    """Reads existing registry, updates versions from CSV, adds entries, writes back, formats."""
    print("\n")
    new_supported_versions = _read_supported_versions(SUPPORTED_VERSIONS_CSV_PATH)
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
