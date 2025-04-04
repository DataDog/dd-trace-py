import os
import pathlib
import pytest
import yaml
import sys
import json
import jsonschema
from packaging.version import parse as parse_version, InvalidVersion

from ddtrace._monkey import PATCH_MODULES, _MODULES_FOR_CONTRIB

PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))


CONTRIB_INTERNAL_DIR = PROJECT_ROOT / "ddtrace" / "contrib" / "internal"
REGISTRY_YAML_PATH = PROJECT_ROOT / "ddtrace" / "contrib" / "integration_registry" / "registry.yaml"
SCHEMA_JSON_PATH = PROJECT_ROOT / "ddtrace" / "contrib" / "integration_registry" / "_registry_schema.json"


@pytest.fixture(scope="module")
def registry_content() -> dict:
    """Loads the entire content of registry.yaml as a dictionary."""
    if not REGISTRY_YAML_PATH.is_file():
        pytest.fail(f"Registry YAML file not found: {REGISTRY_YAML_PATH}")
    try:
        with open(REGISTRY_YAML_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            if not isinstance(data, dict):
                 pytest.fail(f"Invalid structure in {REGISTRY_YAML_PATH}: Expected root object.")
            return data
    except yaml.YAMLError as e:
        pytest.fail(f"Error parsing YAML file {REGISTRY_YAML_PATH}: {e}")
    except Exception as e:
        pytest.fail(f"Error reading file {REGISTRY_YAML_PATH}: {e}")
    return {}

@pytest.fixture(scope="module")
def registry_data(registry_content: dict) -> list[dict]:
    """Extracts the list of integrations from the loaded YAML content."""
    integrations = registry_content.get("integrations")
    if not isinstance(integrations, list):
        pytest.fail(f"Invalid structure in {REGISTRY_YAML_PATH}: Expected 'integrations' key with a list value.")
    if not all(isinstance(item, dict) for item in integrations):
         pytest.fail(f"Invalid structure in {REGISTRY_YAML_PATH}: 'integrations' list should contain objects.")
    return integrations

@pytest.fixture(scope="module")
def registry_schema() -> dict:
    """Loads the JSON schema definition."""
    if not SCHEMA_JSON_PATH.is_file():
        pytest.fail(f"Schema JSON file not found: {SCHEMA_JSON_PATH}")
    try:
        with open(SCHEMA_JSON_PATH, "r", encoding="utf-8") as f:
            schema = json.load(f)
        return schema
    except json.JSONDecodeError as e:
         pytest.fail(f"Error parsing JSON schema file {SCHEMA_JSON_PATH}: {e}")
    except Exception as e:
         pytest.fail(f"Error reading schema file {SCHEMA_JSON_PATH}: {e}")
    return {}

@pytest.fixture(scope="module")
def all_integration_names(registry_data: list[dict]) -> set[str]:
    """Extracts all unique integration names from the registry data."""
    names = set()
    for entry in registry_data:
        name = entry.get("integration_name")
        if name and isinstance(name, str):
            names.add(name)
        else:
            pytest.fail(f"Found entry in registry without a valid string 'integration_name': {entry}")
    if not names:
         pytest.fail("No integration names found in loaded registry data.")
    return names

@pytest.fixture(scope="module")
def patchable_integration_names(registry_data: list[dict]) -> set[str]:
    """Extracts integration names where 'is_patchable' is true in the registry."""
    names = set()
    for entry in registry_data:
        name = entry.get("integration_name")
        is_patchable = entry.get("is_patchable") # Explicitly check the field

        # Check if name is valid and is_patchable is explicitly True
        if name and isinstance(name, str) and is_patchable is True:
            names.add(name)
        # Log or fail if is_patchable is missing? For now, only include if True.
        elif name and isinstance(name, str) and is_patchable is None:
             print(f"Warning: Integration '{name}' is missing the 'is_patchable' field in registry.", file=sys.stderr)

    return names


def test_all_internal_dirs_accounted_for(registry_data: list[dict], all_integration_names: set[str]):
    """
    Verify that every directory within ddtrace/contrib/internal is listed
    as an 'integration_name' in registry.yaml.
    """
    if not CONTRIB_INTERNAL_DIR.is_dir():
        pytest.fail(f"contrib/internal directory not found: {CONTRIB_INTERNAL_DIR}")

    # We only check against names defined in the YAML
    accounted_for_dirs = all_integration_names
    found_dirs_on_disk = set()
    unaccounted_dirs = []

    for item in CONTRIB_INTERNAL_DIR.iterdir():
        if item.is_dir() and item.name != "__pycache__":
            dir_name = item.name
            found_dirs_on_disk.add(dir_name)
            if dir_name not in accounted_for_dirs:
                unaccounted_dirs.append(dir_name)

    # Check for entries in YAML that don't have a corresponding directory
    missing_dirs_in_yaml = accounted_for_dirs - found_dirs_on_disk

    error_messages = []
    if unaccounted_dirs:
        instructions = (
            f"      Please add an entry for each directory listed below to '{REGISTRY_YAML_PATH.relative_to(PROJECT_ROOT)}'.\n"
            f"      Determine 'is_external_package' and 'is_patchable' based on the module's nature."
        )
        error_messages.append(
            f"\n\nUnaccounted Directories Found:\n"
            f"    The following directories exist in '{CONTRIB_INTERNAL_DIR.relative_to(PROJECT_ROOT)}' \n"
            f"    but do NOT have a corresponding 'integration_name' entry in '{REGISTRY_YAML_PATH.name}'.\n\n"
            f"{instructions}\n\n"
            f"      Unaccounted directories:\n"
            f"        - " + "\n        - ".join(sorted(unaccounted_dirs)) + "\n"
        )
    if missing_dirs_in_yaml:
         error_messages.append(
             f"\n\nMissing Directories for YAML Entries:\n"
             f"    The following integration names are listed in '{REGISTRY_YAML_PATH.name}'\n"
             f"    but do not have a corresponding directory in '{CONTRIB_INTERNAL_DIR.relative_to(PROJECT_ROOT)}':\n\n"
             f"      - " + "\n      - ".join(sorted(list(missing_dirs_in_yaml))) + "\n"
         )

    assert not error_messages, "\n".join(error_messages)


def test_registry_integrations_in_patch_modules(patchable_integration_names: set[str]):
    """
    Verify that every integration marked as 'is_patchable: true' in the registry YAML
    exists as a key in ddtrace._monkey.PATCH_MODULES.
    """
    if PATCH_MODULES is None:
        pytest.fail("Could not import PATCH_MODULES from ddtrace._monkey. Ensure ddtrace is installed correctly.")

    patch_module_keys = set(PATCH_MODULES.keys())

    # Find patchable integrations from registry missing in PATCH_MODULES
    missing_in_patch_modules = patchable_integration_names - patch_module_keys

    error_messages = []
    if missing_in_patch_modules:
        sorted_missing = sorted(list(missing_in_patch_modules))
        error_messages.append(
            f"\n\nMissing Integrations in ddtrace._monkey.PATCH_MODULES:\n"
            f"  The following integrations are marked 'is_patchable: true' in '{REGISTRY_YAML_PATH.relative_to(PROJECT_ROOT)}' \n"
            f"  but are MISSING as keys in the PATCH_MODULES dictionary in 'ddtrace/_monkey.py'.\n\n"
            f"  Please ADD these integrations to PATCH_MODULES (set value to True/False/config as appropriate):\n\n"
            f"    Missing integrations:\n"
            f"      - " + "\n      - ".join(sorted_missing) + "\n"
        )

    # Optional: Check for keys in PATCH_MODULES that are NOT marked as patchable in the registry
    # extraneous_in_patch_modules = patch_module_keys - patchable_integration_names
    # if extraneous_in_patch_modules:
    #     sorted_extraneous = sorted(list(extraneous_in_patch_modules))
    #     error_messages.append(
    #         f"\n\nExtraneous Keys in ddtrace._monkey.PATCH_MODULES:\n"
    #         f"  The following keys exist in PATCH_MODULES in 'ddtrace/_monkey.py' \n"
    #         f"  but are NOT marked as 'is_patchable: true' in '{REGISTRY_YAML_PATH.relative_to(PROJECT_ROOT)}'.\n\n"
    #         f"  Consider REMOVING them from PATCH_MODULES or correcting their 'is_patchable' status in the registry:\n\n"
    #         f"    Extraneous keys:\n"
    #         f"      - " + "\n      - ".join(sorted_extraneous) + "\n"
    #     )

    assert not error_messages, "\n".join(error_messages)


def test_registry_conforms_to_schema(registry_content: dict, registry_schema: dict):
    """Validates registry.yaml content against the defined JSON schema."""
    if jsonschema is None:
        pytest.skip("jsonschema library not installed, skipping schema validation.")

    try:
        jsonschema.validate(instance=registry_content, schema=registry_schema)
    except jsonschema.ValidationError as e:
        # Provide a more detailed error message upon failure
        pytest.fail(f"Schema validation failed for {REGISTRY_YAML_PATH}: {e}", pytrace=False)
    except Exception as e:
         pytest.fail(f"An unexpected error occurred during schema validation: {e}")


def test_dependency_name_only_if_external(registry_data: list[dict]):
    """Verify 'dependency_name' only exists if 'is_external_package' is true."""
    errors = []
    for entry in registry_data:
        name = entry.get("integration_name", "UNKNOWN_ENTRY")
        is_external = entry.get("is_external_package")
        has_deps = "dependency_name" in entry

        if is_external is False and has_deps:
            errors.append(f"Integration '{name}' has 'is_external_package: false' but contains a 'dependency_name' field.")
        # Check that external packages have a defined dependency_name
        elif is_external is True and not has_deps:
            errors.append(f"Integration '{name}' has 'is_external_package: true' but is missing 'dependency_name' field.")

    assert not errors, "\n".join(errors)

def test_version_fields_only_if_external(registry_data: list[dict]):
    """Verify version fields only exist if 'is_external_package' is true."""
    errors = []
    version_fields = ["tested_version_min", "tested_version_max", "tested_versions_list"]
    for entry in registry_data:
        name = entry.get("integration_name", "UNKNOWN_ENTRY")
        is_external = entry.get("is_external_package")

        if is_external is False:
            for field in version_fields:
                if field in entry:
                    errors.append(f"Integration '{name}' has 'is_external_package: false' but contains a '{field}' field.")
        elif is_external is True:
            # Check min/max format (should be parsable or N/A)
            for field in ["tested_version_min", "tested_version_max"]:
                if field in entry:
                    version_str = entry[field]
                    try:
                        parse_version(version_str)
                    except InvalidVersion:
                        errors.append(f"External integration '{name}' has invalid SemVer format for '{field}': '{version_str}'.")

            # Check list items format
            if "tested_versions_list" in entry:
                 version_list = entry["tested_versions_list"]
                 if not isinstance(version_list, list):
                      errors.append(f"External integration '{name}' field 'tested_versions_list' is not a list.")
                 else:
                      for i, v_str in enumerate(version_list):
                           try:
                               parse_version(v_str)
                           except InvalidVersion:
                                errors.append(f"External integration '{name}' has invalid SemVer format in 'tested_versions_list' at index {i}: '{v_str}'.")

    assert not errors, "\n".join(errors)

def test_patched_by_default_only_if_patchable(registry_data: list[dict]):
    """Verify 'patched_by_default' only exists if 'is_patchable' is true or status is MISSING."""
    errors = []
    for entry in registry_data:
        name = entry.get("integration_name", "UNKNOWN_ENTRY")
        is_patchable = entry.get("is_patchable")
        patch_status = entry.get("patched_by_default")
        has_patch_status_field = "patched_by_default" in entry

        if is_patchable is False and has_patch_status_field and patch_status != "MISSING_IN_PATCH_MODULES":
             errors.append(f"Integration '{name}' has 'is_patchable: false' but contains 'patched_by_default: {patch_status}' (expected field to be absent or value MISSING...).")
        elif is_patchable is True and not has_patch_status_field:
             errors.append(f"Integration '{name}' has 'is_patchable: true' but is missing the 'patched_by_default' field.")

    assert not errors, "\n".join(errors)

def test_instrumented_modules_only_if_patchable(registry_data: list[dict]):
    """Verify 'instrumented_modules' generally only exists if 'is_patchable' is true."""
    errors = []
    for entry in registry_data:
        name = entry.get("integration_name", "UNKNOWN_ENTRY")
        is_patchable = entry.get("is_patchable")
        has_instr_mods = "instrumented_modules" in entry

        if is_patchable is True and not has_instr_mods:
             errors.append(f"Integration '{name}' has 'is_patchable: true' but is missing 'instrumented_modules' field.")

    assert not errors, "\n".join(errors)

def test_external_packages_have_version_fields(registry_data: list[dict]):
    """
    Verify that every integration marked as 'is_external_package: true'
    has the tested version fields populated (even if N/A).
    The generation script should always add these for external packages.
    """
    errors = []
    # These fields should always be generated for external packages by the script
    # even if the value ends up being "N/A" or an empty list.
    expected_version_fields = ["tested_version_min", "tested_version_max", "tested_versions_list"]
    for entry in registry_data:
        name = entry.get("integration_name", "UNKNOWN_ENTRY")
        is_external = entry.get("is_external_package")

        if is_external is True: # Only check external packages
            for field in expected_version_fields:
                if field not in entry:
                    errors.append(f"External integration '{name}' is missing the required field '{field}'.")

    assert not errors, "\n".join(errors)

def test_remapped_modules_in_contrib_map(registry_data: list[dict]):
    """
    Verify that if instrumented_modules doesn't contain the integration_name itself,
    then the integration_name must be a key in _MODULES_FOR_CONTRIB.
    """

    errors = []
    contrib_map_keys = set(_MODULES_FOR_CONTRIB.keys())

    for entry in registry_data:
        name = entry.get("integration_name")
        instr_modules = entry.get("instrumented_modules")
        is_patchable = entry.get("is_patchable")

        # Only check patchable integrations that have instrumented_modules defined
        if not is_patchable or not name or not isinstance(instr_modules, list) or not instr_modules:
            continue

        # Check if the integration name itself is listed as one of the instrumented modules
        # This covers the default case and cases where _MODULES_FOR_CONTRIB includes the integration name
        name_is_in_modules = name in instr_modules

        # If the integration name is NOT listed in its own instrumented modules,
        # it implies a remapping occurred, so it MUST be in _MODULES_FOR_CONTRIB
        if not name_is_in_modules:
            if name not in contrib_map_keys:
                 errors.append(
                     f"Integration '{name}' has instrumented_modules ({instr_modules}) that do not include '{name}', "
                     f"but '{name}' is not found as a key in ddtrace._monkey._MODULES_FOR_CONTRIB. "
                     f"Add it to _MODULES_FOR_CONTRIB if the mapping is intentional."
                 )

    assert not errors, "\n".join(errors)
