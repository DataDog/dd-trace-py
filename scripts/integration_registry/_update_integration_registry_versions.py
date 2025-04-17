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
from collections import defaultdict
from typing import Set

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
            col_dependency = next((h for h in header if "dependency" in h.lower() and h.lower() != col_integration.lower()), None)
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


def _read_registry_yaml(filepath: pathlib.Path) -> Optional[List[Dict[str, Any]]]:
    """Reads the existing registry YAML file, returning the list of integrations or None on error."""
    print(f"Loading existing registry data from: {filepath.relative_to(PROJECT_ROOT)}")
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            registry_content = yaml.safe_load(f)
        integrations_list = registry_content["integrations"]
        print(f"Loaded {len(integrations_list)} existing integration entries.")
        return integrations_list
    except Exception as e:
        print(
            f"Error reading or parsing existing registry YAML {filepath.relative_to(PROJECT_ROOT)}: {e}",
            file=sys.stderr,
        )
        return None


def _create_version_info_block(min_v: Optional[str], max_v: Optional[str]) -> Optional[Dict[str, str]]:
    """Creates the {'min': ..., 'max': ...} block, returning None if both are None/N/A."""
    min_final = min_v if min_v and min_v != "N/A" else None
    max_final = max_v if max_v and max_v != "N/A" else None
    if min_final is None and max_final is None:
        return None
    return {"min": min_final or "N/A", "max": max_final or "N/A"}


def _create_new_integration_entry(integration_name: str, dependency_version_map: Dict[str, Dict[str, str]]) -> Dict[str, Any]:
    """Creates a new integration entry with version information."""
    dependency_name_list = sorted(list(dependency_version_map.keys()))
    new_version_map_for_yaml = {}
    
    for dep_name, version_info in dependency_version_map.items():
        version_block = _create_version_info_block(version_info.get("min"), version_info.get("max"))
        if version_block:
            new_version_map_for_yaml[dep_name] = version_block

    new_entry = {
        "integration_name": integration_name,
        "is_external_package": True,
        "dependency_name": dependency_name_list if dependency_name_list else None,
        "tested_versions_by_dependency": new_version_map_for_yaml if new_version_map_for_yaml else None,
    }
    return {k: v for k, v in new_entry.items() if v is not None}


def _update_and_add_integration_versions(
    current_integrations: List[Dict[str, Any]],
    new_versions: Dict[str, Dict[str, Dict[str, str]]]
) -> Tuple[List[Dict[str, Any]], int, int, int]:
    """
    Updates dependency versions using the nested structure and adds new integrations if needed.
    Returns (updated_integrations_list, updated_count, removed_count, added_count).
    """
    final_integrations_list = []
    updated_count = 0
    removed_count = 0
    added_count = 0
    existing_names: Set[str] = set()

    for entry in current_integrations:
        if not isinstance(entry, dict): continue
        integration_name = entry.get("integration_name")
        if not integration_name: continue

        existing_names.add(integration_name.lower())
        updated_entry = entry.copy()

        if updated_entry.get("is_external_package"):
            dependency_version_map = new_versions.get(integration_name.lower())

            if dependency_version_map:
                new_version_map_for_yaml = {}
                for dep_name, version_info in dependency_version_map.items():
                    version_block = _create_version_info_block(version_info.get("min"), version_info.get("max"))
                    if version_block:
                        new_version_map_for_yaml[dep_name] = version_block

                if new_version_map_for_yaml:
                    if updated_entry.get("tested_versions_by_dependency") != new_version_map_for_yaml:
                        updated_entry["tested_versions_by_dependency"] = new_version_map_for_yaml
                        updated_count += 1
                elif "tested_versions_by_dependency" in updated_entry:
                    del updated_entry["tested_versions_by_dependency"]
                    removed_count += 1

        elif "tested_versions_by_dependency" in updated_entry:
            del updated_entry["tested_versions_by_dependency"]
            removed_count += 1

        final_integrations_list.append(updated_entry)

    # Second pass: Add new integrations
    for integration_name_lower, dependency_version_map in new_versions.items():
        if integration_name_lower not in existing_names:
            print(f"  Info: Adding new integration '{integration_name_lower}' to registry.")
            added_count += 1
            new_entry = _create_new_integration_entry(integration_name_lower, dependency_version_map)
            final_integrations_list.append(new_entry)

    final_integrations_list.sort(key=lambda x: x.get("integration_name", ""))

    return final_integrations_list, updated_count, removed_count, added_count


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
    """Reads existing registry, updates versions from CSV, adds entries, writes back, formats."""
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

    updated_integrations, updated_count, removed_count, added_count = _update_and_add_integration_versions(
        original_integrations, new_supported_versions
    )

    print(f"\nUpdate summary: {updated_count} integration(s) had version maps updated/added.")
    print(f"                {removed_count} integration(s) had version info removed.")
    print(f"                {added_count} new integration(s) added.")

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
