import csv
import pathlib
import sys
from typing import Any, Dict, List, Set, Tuple, Union
import yaml
from packaging.version import InvalidVersion, parse as parse_version


# --- Dynamically add project root to sys.path ---
PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent.parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

# --- Configuration ---
SCRIPT_DIR = pathlib.Path(__file__).parent.resolve()
REGISTRY_DIR = SCRIPT_DIR.parent
CONTRIB_INTERNAL_DIR = PROJECT_ROOT / "ddtrace" / "contrib" / "internal"
REGISTRY_YAML_PATH = REGISTRY_DIR / "registry.yaml"
FORMATTER_SCRIPT_PATH = SCRIPT_DIR / "format_registry.py"
# Path to the static supported versions table - THIS IS THE SOURCE OF TRUTH FOR VERSIONS
SUPPORTED_VERSIONS_CSV_PATH = PROJECT_ROOT / "supported_versions_table.csv"

# --- Integration Classification Sets ---
STDLIB_INTEGRATIONS: Set[str] = {
    "asyncio", "futures", "httplib", "logging", "sqlite3", "subprocess",
    "urllib", "webbrowser", "wsgi", "asgi", "unittest"
}
INTERNAL_HELPER_INTEGRATIONS: Set[str] = {
    "dbapi", "dbapi_async", "ddtrace_api"
}

# --- PIP Dependency Name Correction Mapping ---
INTEGRATION_TO_PYPI_MAP: Dict[str, List[str]] = {
    "consul": ["python-consul"], "cassandra": ["cassandra-driver"],
    "dogpile_cache": ["dogpile.cache"], "graphql": ["graphql-core"],
    "grpc": ["grpcio"], "kafka": ["confluent-kafka"], "mysqldb": ["mysqlclient"],
    "mysql": ["mysql-connector-python"], "rediscluster": ["redis-py-cluster"],
    "snowflake": ["snowflake-connector-python"], "vertica": ["vertica-python"],
    "azure_functions": ["azure-functions"], "google_generativeai": ["google-generativeai"],
    "vertexai": ["google-cloud-aiplatform"],
    "elasticsearch": [
        "elasticsearch", "elasticsearch1", "elasticsearch2", "elasticsearch5",
        "elasticsearch6", "elasticsearch7", "elastic-transport", "opensearchpy"
    ],
}
# --- End Configuration ---

def _normalize_version_string(v_str: str) -> str:
    """Ensures version string has MAJOR.MINOR.PATCH, adding .0 if needed."""
    try:
        if not v_str or not v_str[0].isdigit(): return v_str
        v = parse_version(v_str)
        # Check if micro or minor are None, indicating padding needed
        if v.micro is None:
            if v.minor is None: return f"{v.major}.0.0"
            else: return f"{v.major}.{v.minor}.0"
        # Check if original string was missing .0 (e.g. '1.6' parsed ok but needs padding)
        parts = v_str.split('.')
        if len(parts) < 3:
             if len(parts) == 1: return f"{v.major}.0.0"
             if len(parts) == 2: return f"{v.major}.{v.minor}.0"
        return v_str # Return original if already has M.m.p or is complex
    except InvalidVersion:
        return v_str # Return as-is if not parsable

def read_supported_versions(filepath: pathlib.Path) -> Dict[str, Dict[str, str]]:
    """Reads the supported_versions_table.csv file and normalizes versions."""
    supported_data: Dict[str, Dict[str, str]] = {}
    if not filepath.is_file():
        print(f"Warning: Supported versions file not found at {filepath}. Version info will be missing.", file=sys.stderr)
        return supported_data

    print(f"Reading supported versions from: {filepath}")
    try:
        with open(filepath, 'r', newline='', encoding='utf-8') as csvfile:
            expected_headers = ["integration", "minimum_tracer_supported", "max_tracer_supported"]
            reader = csv.DictReader(csvfile)
            if not all(h in reader.fieldnames for h in expected_headers):
                print(f"Error: Missing expected columns in {filepath}. Expected: {expected_headers}, Found: {reader.fieldnames}", file=sys.stderr)
                return supported_data

            for row in reader:
                integration_name_raw = row.get("integration", "").strip()
                min_version = row.get("minimum_tracer_supported", "").strip()
                max_version = row.get("max_tracer_supported", "").strip()

                if not integration_name_raw: continue

                integration_name = integration_name_raw.split('*')[0].strip().lower()

                # Normalize versions during read
                normalized_min = _normalize_version_string(min_version) if min_version else "N/A"
                normalized_max = _normalize_version_string(max_version) if max_version else "N/A"

                supported_data[integration_name] = {
                    "min": normalized_min,
                    "max": normalized_max,
                }
                # Optional: Warn if normalization changed the value
                # if normalized_min != min_version and min_version:
                #      print(f"  Info: Normalized min version for '{integration_name}': '{min_version}' -> '{normalized_min}'", file=sys.stderr)
                # if normalized_max != max_version and max_version:
                #      print(f"  Info: Normalized max version for '{integration_name}': '{max_version}' -> '{normalized_max}'", file=sys.stderr)

    except Exception as e:
        print(f"Error reading supported versions file {filepath}: {e}", file=sys.stderr)

    print(f"Loaded supported version info for {len(supported_data)} integrations.")
    return supported_data

def format_yaml_with_newlines(yaml_path: pathlib.Path):
    """Reads a YAML file and rewrites it with blank lines between top-level list items."""
    print("-" * 30)
    print(f"Formatting YAML file: {yaml_path}")
    if not yaml_path.is_file():
        print(f"  Warning: Cannot format - file not found: {yaml_path}", file=sys.stderr)
        return

    try:
        with open(yaml_path, 'r', encoding='utf-8') as infile:
            input_lines = infile.readlines()
    except IOError as e:
        print(f"  Error reading {yaml_path} for formatting: {e}", file=sys.stderr)
        return

    output_lines = []
    found_first_integration = False

    for line in input_lines:
        # Check if the line marks the beginning of a top-level integration item
        # by checking if the stripped line starts with '- integration_name:'
        stripped_line = line.lstrip() # Remove leading whitespace only
        is_integration_start = stripped_line.startswith("- integration_name:")

        if is_integration_start:
            if found_first_integration:
                # Add blank line only if previous line wasn't already blank
                if output_lines and output_lines[-1].strip() != "":
                    output_lines.append("\n")
            else:
                found_first_integration = True

        output_lines.append(line) # Preserve original line ending

    try:
        with open(yaml_path, 'w', encoding='utf-8') as outfile:
            outfile.writelines(output_lines)
        print(f"Successfully applied formatting to {yaml_path.name}")
    except IOError as e:
        print(f"  Error writing formatted file {yaml_path}: {e}", file=sys.stderr)

def main():
    """Scans directories, uses support table for versions, generates simplified registry.yaml."""

    print("Loading supported version data...")
    supported_versions_data = read_supported_versions(SUPPORTED_VERSIONS_CSV_PATH)
    print("-" * 30)

    integration_data_list: List[Dict[str, Any]] = []
    processed_integration_names: Set[str] = set()

    print(f"Scanning integration directories in: {CONTRIB_INTERNAL_DIR}")
    if not CONTRIB_INTERNAL_DIR.is_dir():
         print(f"Error: Contrib internal directory not found: {CONTRIB_INTERNAL_DIR}", file=sys.stderr)
         sys.exit(1)

    for item in CONTRIB_INTERNAL_DIR.iterdir():
        if not item.is_dir() or item.name == "__pycache__": continue

        integration_name = item.name
        if integration_name in processed_integration_names: continue

        print(f"Processing directory: {integration_name}")
        processed_integration_names.add(integration_name)

        # --- Determine Properties ---
        is_stdlib = integration_name in STDLIB_INTEGRATIONS
        is_internal_helper = integration_name in INTERNAL_HELPER_INTEGRATIONS
        is_external_package = not (is_stdlib or is_internal_helper)

        # --- Determine Dependency Name(s) --- Only if external
        dependency_name_list = []
        if is_external_package:
            # Priority 1: Explicit mapping
            dependency_name_list = INTEGRATION_TO_PYPI_MAP.get(integration_name, [])
            # Priority 2: Instrumented modules (if they seem like package names)
            if not dependency_name_list and instrumented_modules_list:
                 # Use instrumented modules if they don't contain dots (heuristic)
                 potential_deps = [mod for mod in instrumented_modules_list if '.' not in mod]
                 if potential_deps:
                      dependency_name_list = potential_deps
            # Priority 3: Fallback to integration name itself (if not stdlib/testing)
            if not dependency_name_list:
                 if integration_name not in STDLIB_INTEGRATIONS and integration_name not in TESTING_INTEGRATIONS:
                      dependency_name_list = [integration_name]

            # Final cleanup
            dependency_name_list = sorted(list(set(dependency_name_list)))


        # --- Get SUPPORTED Versions --- Only if external
        supported_version_min = None
        supported_version_max = None
        if is_external_package:
             version_info = supported_versions_data.get(integration_name.lower())
             if version_info:
                  supported_version_min = version_info.get("min")
                  supported_version_max = version_info.get("max")
             else:
                  print(f"  Warning: No supported version info found for EXTERNAL '{integration_name}' in {SUPPORTED_VERSIONS_CSV_PATH.name}", file=sys.stderr)


        # --- Build Final Entry ---
        integration_entry = {
            "integration_name": integration_name,
            "is_external_package": is_external_package,
            "dependency_name": dependency_name_list if dependency_name_list else None,
            "supported_version_min": supported_version_min if supported_version_min and supported_version_min != "N/A" else None,
            "supported_version_max": supported_version_max if supported_version_max and supported_version_max != "N/A" else None,
        }

        # Clean up fields that are None
        final_entry = {k: v for k, v in integration_entry.items() if v is not None}

        integration_data_list.append(final_entry)

    # --- Sort Final List ---
    final_integration_list = sorted(integration_data_list, key=lambda x: x["integration_name"])
    final_yaml_structure = {"integrations": final_integration_list}

    # --- Write YAML Output ---
    print(f"\nWriting final registry data to YAML file: {REGISTRY_YAML_PATH}")
    try:
        with open(REGISTRY_YAML_PATH, 'w', encoding='utf-8') as yamlfile:
            yaml.dump(
                final_yaml_structure,
                yamlfile,
                default_flow_style=False,
                sort_keys=False,
                indent=2,
                width=100
             )
        print("Successfully generated registry.yaml")

        # --- Call INLINE Formatter Logic ---
        format_yaml_with_newlines(REGISTRY_YAML_PATH)

    except Exception as e:
         print(f"Error during YAML generation or formatting: {e}", file=sys.stderr)
         sys.exit(1)

if __name__ == "__main__":
    main()
