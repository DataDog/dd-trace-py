"""
Updates the supported version fields in ddtrace/contrib/integration_registry/registry.yaml based on
supported_versions_table.csv.
Preserves all other existing fields in registry.yaml.
"""

import csv
import pathlib
import subprocess
import sys
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from packaging.version import InvalidVersion
from packaging.version import parse as parse_version
import yaml


SCRIPT_DIR = pathlib.Path(__file__).parent.parent.resolve()
PROJECT_ROOT = SCRIPT_DIR.parent.resolve()
REGISTRY_YAML_PATH = PROJECT_ROOT / "ddtrace" / "contrib" / "integration_registry" / "registry.yaml"
SUPPORTED_VERSIONS_CSV_PATH = PROJECT_ROOT / "supported_versions_table.csv"
FORMATTER_SCRIPT_PATH = SCRIPT_DIR / "integration_registry" / "_format_integration_registry.py"


def _normalize_version_string(v_str: str) -> str:
    """Ensures version string has MAJOR.MINOR.PATCH, adding .0 if needed."""
    try:
        if not v_str or not v_str[0].isdigit():
            return v_str
        v = parse_version(v_str)
        if v.micro is None:
            if v.minor is None:
                return f"{v.major}.0.0"
            else:
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


def _read_supported_versions(filepath: pathlib.Path) -> Optional[Dict[str, Dict[str, str]]]:
    """Reads the supported versions CSV, returning {integration_name: {'min': str, 'max': str}} or None on error."""
    supported_data: Dict[str, Dict[str, str]] = {}
    if not filepath.is_file():
        print(f"Error: Supported versions file not found: {filepath.relative_to(PROJECT_ROOT)}", file=sys.stderr)
        return None

    print(f"Reading NEW supported versions from: {filepath.relative_to(PROJECT_ROOT)}")
    try:
        with open(filepath, "r", newline="", encoding="utf-8") as csvfile:
            header = next(csv.reader(csvfile))
            csvfile.seek(0)
            reader = csv.DictReader(csvfile)
            col_integration = next((h for h in header if "integration" in h.lower()), None)
            col_min = next((h for h in header if "minimum" in h.lower()), None)
            col_max = next((h for h in header if "max" in h.lower()), None)

            if not (col_integration and col_min and col_max):
                print(
                    f"Error: Missing expected columns in {filepath.relative_to(PROJECT_ROOT)}. Found: {header}",
                    file=sys.stderr,
                )
                return None

            for row in reader:
                integration_name_raw = row.get(col_integration, "").strip()
                min_version = row.get(col_min, "").strip()
                max_version = row.get(col_max, "").strip()
                if not integration_name_raw:
                    continue
                integration_name = integration_name_raw.split("*")[0].strip().lower()
                normalized_min = _normalize_version_string(min_version) if min_version else "N/A"
                normalized_max = _normalize_version_string(max_version) if max_version else "N/A"
                supported_data[integration_name] = {"min": normalized_min, "max": normalized_max}

    except Exception as e:
        print(f"Error reading supported versions file {filepath.relative_to(PROJECT_ROOT)}: {e}", file=sys.stderr)
        return None

    print(f"Loaded supported version info for {len(supported_data)} integrations from {filepath.name}.")
    return supported_data


def _read_registry_yaml(filepath: pathlib.Path) -> Optional[List[Dict[str, Any]]]:
    """Reads the existing registry YAML file, returning the list of integrations or None on error."""
    print(f"Loading existing registry data from: {filepath.relative_to(PROJECT_ROOT)}")
    if not filepath.is_file():
        print(f"Error: Registry YAML file not found: {filepath.relative_to(PROJECT_ROOT)}", file=sys.stderr)
        return None
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            registry_content = yaml.safe_load(f)
        if not isinstance(registry_content, dict) or "integrations" not in registry_content:
            raise ValueError("Invalid registry format: Missing 'integrations' list.")
        integrations_list = registry_content["integrations"]
        if not isinstance(integrations_list, list):
            raise ValueError("Invalid registry format: 'integrations' is not a list.")
        print(f"Loaded {len(integrations_list)} existing integration entries.")
        return integrations_list
    except Exception as e:
        print(
            f"Error reading or parsing existing registry YAML {filepath.relative_to(PROJECT_ROOT)}: {e}",
            file=sys.stderr,
        )
        return None


def _update_integration_versions(
    current_integrations: List[Dict[str, Any]], new_versions: Dict[str, Dict[str, str]]
) -> Tuple[List[Dict[str, Any]], int, int]:
    """Updates version fields in the integration list based on new supported versions."""
    updated_list = []
    updated_count = 0
    removed_count = 0

    for entry in current_integrations:
        if not isinstance(entry, dict):
            continue
        integration_name = entry.get("integration_name")
        if not integration_name:
            continue

        updated_entry = entry.copy()  # Work on a copy

        if updated_entry.get("is_external_package"):
            version_info = new_versions.get(integration_name.lower())
            updated = False
            removed = False

            # Process min version
            new_min = version_info.get("min") if version_info and version_info.get("min") != "N/A" else None
            old_min = updated_entry.get("tested_version_min")
            if new_min:
                if old_min != new_min:
                    updated_entry["tested_version_min"] = new_min
                    updated = True
            elif "tested_version_min" in updated_entry:
                del updated_entry["tested_version_min"]
                removed = True

            # Process max version
            new_max = version_info.get("max") if version_info and version_info.get("max") != "N/A" else None
            old_max = updated_entry.get("tested_version_max")
            if new_max:
                if old_max != new_max:
                    updated_entry["tested_version_max"] = new_max
                    updated = True
            elif "tested_version_max" in updated_entry:
                del updated_entry["tested_version_max"]
                removed = True

            if updated:
                updated_count += 1
            if removed and not updated:  # Only count as removed if no update occurred
                removed_count += 1
                print(
                    f"  Info: Removed version info for '{integration_name}' as it was not found "
                    "in the new support table or versions were 'N/A'."
                )

        updated_list.append(updated_entry)

    return updated_list, updated_count, removed_count


def _write_registry_yaml(filepath: pathlib.Path, integrations_list: List[Dict[str, Any]]) -> bool:
    """Writes the updated integration list back to the YAML file."""
    print(f"\nWriting updated registry data back to: {filepath.relative_to(PROJECT_ROOT)}")
    final_yaml_structure = {"integrations": integrations_list}
    try:
        with open(filepath, "w", encoding="utf-8") as yamlfile:
            yaml.dump(
                final_yaml_structure,
                yamlfile,
                default_flow_style=False,
                sort_keys=False,
                indent=2,
                width=100,
            )
        print(f"Successfully updated {filepath.name}")
        return True
    except Exception as e:
        print(f"Error writing updated YAML file {filepath.relative_to(PROJECT_ROOT)}: {e}", file=sys.stderr)
        return False


def _run_formatter_script(formatter_path: pathlib.Path, run_dir: pathlib.Path) -> bool:
    """Executes the external YAML formatter script."""
    print("-" * 120)
    print(f"Attempting to format the YAML file using external script: {formatter_path.name}")
    if not formatter_path.is_file():
        print(f"Warning: Formatter script not found at {formatter_path}. Skipping formatting.", file=sys.stderr)
        return True

    try:
        result = subprocess.run(
            [sys.executable, str(formatter_path)], check=True, capture_output=True, text=True, cwd=run_dir
        )
        print("Formatter script output:")
        print(result.stdout)
        if result.stderr:
            print("Formatter script errors:", result.stderr, file=sys.stderr)
        print("Formatting complete.")
        return True
    except Exception as e:
        print(f"Error running formatter script {formatter_path.name}: {e}", file=sys.stderr)
        if isinstance(e, subprocess.CalledProcessError):
            print("Formatter stdout:", e.stdout, file=sys.stderr)
            print("Formatter stderr:", e.stderr, file=sys.stderr)
        return False


def main() -> int:
    """Reads existing registry, updates versions from CSV, writes back, formats."""
    print("\n")
    new_supported_versions = _read_supported_versions(SUPPORTED_VERSIONS_CSV_PATH)
    if new_supported_versions is None:
        print("Aborting due to errors reading supported versions table.", file=sys.stderr)
        return 1
    print("-" * 120)

    original_integrations = _read_registry_yaml(REGISTRY_YAML_PATH)
    if original_integrations is None:
        print("Aborting due to errors reading existing registry YAML.", file=sys.stderr)
        return 1

    updated_integrations, updated_count, removed_count = _update_integration_versions(
        original_integrations, new_supported_versions
    )

    print(f"\nUpdate summary: {updated_count} integration(s) had versions updated.")
    print(f"                {removed_count} integration(s) had version info removed.")

    if not _write_registry_yaml(REGISTRY_YAML_PATH, updated_integrations):
        return 1

    if not _run_formatter_script(FORMATTER_SCRIPT_PATH, PROJECT_ROOT):
        print("Warning: Formatting step failed.", file=sys.stderr)
        return 0

    print("\n--- Version Update Workflow Completed Successfully ---")
    print("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
