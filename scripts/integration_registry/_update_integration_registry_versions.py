"""
Updates the supported version fields in ddtrace/contrib/integration_registry/registry.yaml based on
supported_versions_table.csv.
Preserves all other existing fields in registry.yaml.
"""

import csv
import pathlib
import subprocess
import sys
from typing import Dict

from packaging.version import InvalidVersion
from packaging.version import parse as parse_version
import yaml


# --- Configuration ---
# Script is in ./scripts/
SCRIPT_DIR = pathlib.Path(__file__).parent.parent.resolve()
PROJECT_ROOT = SCRIPT_DIR.parent.resolve()
# Path to the existing registry
REGISTRY_YAML_PATH = PROJECT_ROOT / "ddtrace" / "contrib" / "integration_registry" / "registry.yaml"
# Path to the *new* source of version truth
SUPPORTED_VERSIONS_CSV_PATH = PROJECT_ROOT / "supported_versions_table.csv"
# Path to the formatter script
FORMATTER_SCRIPT_PATH = SCRIPT_DIR / "integration_registry" / "_format_integration_registry.py"
# --- End Configuration ---


# --- Helper Functions ---
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


def read_supported_versions(filepath: pathlib.Path) -> Dict[str, Dict[str, str]]:
    """Reads the supported_versions_table.csv file and normalizes versions."""
    supported_data: Dict[str, Dict[str, str]] = {}
    if not filepath.is_file():
        print(f"Error: Supported versions file not found at {filepath}. Cannot update versions.", file=sys.stderr)
        return supported_data  # Return empty, script should probably exit

    print(f"Reading NEW supported versions from: {filepath}")
    try:
        with open(filepath, "r", newline="", encoding="utf-8") as csvfile:
            # Read the header to dynamically find columns, more robust
            header = next(csv.reader(csvfile))
            csvfile.seek(0)  # Reset for DictReader
            reader = csv.DictReader(csvfile)

            # Dynamically find column names, allowing for variations
            col_integration = next((h for h in header if "integration" in h.lower()), None)
            col_min = next((h for h in header if "minimum" in h.lower()), None)
            col_max = next((h for h in header if "max" in h.lower()), None)

            if not (col_integration and col_min and col_max):
                print(
                    f"Error: Missing expected columns ('integration', 'minimum...', 'max...') in {filepath}."
                    + f" Found: {header}",
                    file=sys.stderr,
                )
                return supported_data

            for row in reader:
                integration_name_raw = row.get(col_integration, "").strip()
                min_version = row.get(col_min, "").strip()
                max_version = row.get(col_max, "").strip()

                if not integration_name_raw:
                    continue

                integration_name = integration_name_raw.split("*")[0].strip().lower()
                normalized_min = _normalize_version_string(min_version) if min_version else "N/A"
                normalized_max = _normalize_version_string(max_version) if max_version else "N/A"

                supported_data[integration_name] = {
                    "min": normalized_min,
                    "max": normalized_max,
                }
    except Exception as e:
        print(f"Error reading supported versions file {filepath}: {e}", file=sys.stderr)

    print(f"Loaded supported version info for {len(supported_data)} integrations from {filepath.name}.")
    return supported_data


# --- Main Update Logic ---
def main():
    """Reads existing registry, updates versions from CSV, writes back."""

    # 1. Load the *new* source of truth for versions
    print("Loading NEW supported version data...")
    new_supported_versions = read_supported_versions(SUPPORTED_VERSIONS_CSV_PATH)
    if not new_supported_versions:
        print("Error: Could not load data from supported versions table. Aborting.", file=sys.stderr)
        sys.exit(1)
    print("-" * 30)

    # 2. Load the *existing* registry content
    print(f"Loading existing registry data from: {REGISTRY_YAML_PATH}")
    if not REGISTRY_YAML_PATH.is_file():
        print(f"Error: Registry YAML file not found at {REGISTRY_YAML_PATH}", file=sys.stderr)
        sys.exit(1)
    try:
        with open(REGISTRY_YAML_PATH, "r", encoding="utf-8") as f:
            registry_content = yaml.safe_load(f)
            if not isinstance(registry_content, dict) or "integrations" not in registry_content:
                raise ValueError("Invalid registry format: Missing 'integrations' list.")
            original_integrations_list = registry_content["integrations"]
    except Exception as e:
        print(f"Error reading or parsing existing registry YAML {REGISTRY_YAML_PATH}: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Loaded {len(original_integrations_list)} existing integration entries.")

    # 3. Iterate and Update
    updated_integrations_list = []
    updated_count = 0
    keys_removed_count = 0

    for entry in original_integrations_list:
        if not isinstance(entry, dict):
            continue  # Skip invalid entries

        integration_name = entry.get("integration_name")
        if not integration_name:
            continue  # Skip entries without a name

        is_external = entry.get("is_external_package")  # Check if it's external

        if is_external:
            # Lookup in the *new* version data
            new_version_info = new_supported_versions.get(integration_name.lower())

            if new_version_info:
                # Get new min/max, defaulting to None if not "N/A"
                new_min = new_version_info.get("min") if new_version_info.get("min") != "N/A" else None
                new_max = new_version_info.get("max") if new_version_info.get("max") != "N/A" else None

                # Update or add the fields
                updated = False
                if new_min and entry.get("tested_version_min") != new_min:
                    entry["tested_version_min"] = new_min
                    updated = True
                elif not new_min and "tested_version_min" in entry:
                    del entry["tested_version_min"]  # Remove if new data has no min
                    updated = True

                if new_max and entry.get("tested_version_max") != new_max:
                    entry["tested_version_max"] = new_max
                    updated = True
                elif not new_max and "tested_version_max" in entry:
                    del entry["tested_version_max"]  # Remove if new data has no max
                    updated = True

                if updated:
                    updated_count += 1

            else:
                # Integration not found in *new* support table, remove old version info
                removed = False
                if "tested_version_min" in entry:
                    del entry["tested_version_min"]
                    removed = True
                if "tested_version_max" in entry:
                    del entry["tested_version_max"]
                    removed = True
                if removed:
                    keys_removed_count += 1
                    print(
                        f"  Info: Removed version info for '{integration_name}' as it was not found in the new "
                        + "support table."
                    )
        # Else (not external): keep existing entry as is, don't touch version fields

        updated_integrations_list.append(entry)  # Add modified or original entry

    # Ensure the final structure has the root key
    final_yaml_structure = {"integrations": updated_integrations_list}

    # 4. Write Updated YAML Output
    print(f"\nWriting updated registry data back to: {REGISTRY_YAML_PATH}")
    print(f"  Updated version info for {updated_count} integration(s).")
    print(f"  Removed version info for {keys_removed_count} integration(s) not found in new support table.")
    try:
        with open(REGISTRY_YAML_PATH, "w", encoding="utf-8") as yamlfile:
            yaml.dump(
                final_yaml_structure,
                yamlfile,
                default_flow_style=False,
                sort_keys=False,  # Preserve original order as much as possible
                indent=2,
                width=100,
            )
        print("Successfully updated registry.yaml")

        # --- Call Formatter Script ---
        print("-" * 30)
        print("Attempting to format the generated YAML file using external script...")
        if FORMATTER_SCRIPT_PATH.is_file():
            try:
                # Use sys.executable to ensure the same Python interpreter is used
                result = subprocess.run(
                    [sys.executable, str(FORMATTER_SCRIPT_PATH)],
                    check=True,  # Raise exception on non-zero exit code
                    capture_output=True,  # Capture stdout/stderr
                    text=True,  # Decode output as text
                    cwd=PROJECT_ROOT,  # Run formatter from project root
                )
                print("Formatter script output:")
                print(result.stdout)
                if result.stderr:
                    print("Formatter script errors:", file=sys.stderr)
                    print(result.stderr, file=sys.stderr)
                print("Formatting complete.")
            except FileNotFoundError:
                print(f"Error: Formatter script not found at {FORMATTER_SCRIPT_PATH}", file=sys.stderr)
            except subprocess.CalledProcessError as e:
                print(f"Error: Formatter script failed with exit code {e.returncode}", file=sys.stderr)
                print("Formatter stdout:", file=sys.stderr)
                print(e.stdout, file=sys.stderr)
                print("Formatter stderr:", file=sys.stderr)
                print(e.stderr, file=sys.stderr)
            except Exception as e:
                print(f"Error running formatter script: {e}", file=sys.stderr)
        else:
            print(
                f"Warning: Formatter script not found at {FORMATTER_SCRIPT_PATH}. Skipping formatting.", file=sys.stderr
            )

    except Exception as e:
        print(f"Error during YAML update: {e}", file=sys.stderr)  # Removed formatting from error msg


if __name__ == "__main__":
    main()
