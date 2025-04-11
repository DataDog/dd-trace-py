import json
import pathlib
import subprocess
import sys

import jsonschema
from packaging.version import InvalidVersion
from packaging.version import parse as parse_version
import pytest
import yaml


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


def test_registry_conforms_to_schema(registry_content: dict, registry_schema: dict):
    """Validates registry.yaml content against the defined JSON schema."""
    if jsonschema is None:
        pytest.skip("jsonschema library not installed, skipping schema validation.")

    try:
        jsonschema.validate(instance=registry_content, schema=registry_schema)
    except jsonschema.ValidationError as e:
        pytest.fail(f"Schema validation failed for {REGISTRY_YAML_PATH}:\n{e}", pytrace=False)
    except Exception as e:
        pytest.fail(f"An unexpected error occurred during schema validation: {e}")


def test_all_internal_dirs_accounted_for(registry_data: list[dict], all_integration_names: set[str]):
    """
    Verify that every directory within ddtrace/contrib/internal is listed
    as an 'integration_name' in registry.yaml.
    """
    if not CONTRIB_INTERNAL_DIR.is_dir():
        pytest.fail(f"contrib/internal directory not found: {CONTRIB_INTERNAL_DIR}")

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
        error_messages.append(
            f"\n\nUnaccounted Directories Found:\n"
            f"    The following directories exist in '{CONTRIB_INTERNAL_DIR.relative_to(PROJECT_ROOT)}' \n"
            f"    but do NOT have a corresponding 'integration_name' entry in '{REGISTRY_YAML_PATH.name}'.\n"
            f"    Please add entries for them:\n"
            f"      - " + "\n      - ".join(sorted(unaccounted_dirs)) + "\n"
        )
    if missing_dirs_in_yaml:
        error_messages.append(
            f"\n\nMissing Directories for YAML Entries:\n"
            f"    The following integration names are listed in '{REGISTRY_YAML_PATH.name}'\n"
            f"    but do not have a corresponding directory in '{CONTRIB_INTERNAL_DIR.relative_to(PROJECT_ROOT)}':\n\n"
            f"      - " + "\n      - ".join(sorted(list(missing_dirs_in_yaml))) + "\n"
        )

    assert not error_messages, "\n".join(error_messages)


def test_external_package_requirements(registry_data: list[dict]):
    """
    Verify that external packages have dependency names and valid version fields.
    """
    errors = []
    version_fields_to_check = ["tested_version_min", "tested_version_max"]

    for entry in registry_data:
        name = entry.get("integration_name", "UNKNOWN_ENTRY")
        is_external = entry.get("is_external_package")
        is_tested = entry.get("is_tested")

        if is_external is True and is_tested is not False:
            # 1. Check if dependency_name exists
            if "dependency_name" not in entry:
                errors.append(f"External integration '{name}' is missing the required 'dependency_name' field.")
            elif not isinstance(entry["dependency_name"], list) or not entry["dependency_name"]:
                errors.append(f"External integration '{name}' has an empty or invalid 'dependency_name' field.")

            # 2. Check if version fields exist (they should be generated if versions were found)
            #    We check format below, presence is less critical if source table is empty for an integration
            for field in version_fields_to_check:
                if field not in entry:
                    # This might be too strict if the source CSV legitimately lacks data for some integrations
                    errors.append(
                        f"External integration '{name}' is missing the field '{field}' (Expected even if value"
                        + " is 'N/A')."
                    )

            # 3. Check version format if fields *do* exist
            for field in version_fields_to_check:
                if field in entry:
                    version_str = entry[field]
                    # Allow 'N/A' as a valid placeholder if source data was missing
                    if version_str != "N/A":
                        try:
                            # Ensure it's parsable as per SemVer
                            parse_version(version_str)
                        except InvalidVersion:
                            errors.append(
                                f"External integration '{name}' has invalid SemVer format for '{field}': "
                                + f"'{version_str}'."
                            )

        elif is_external is False:
            # Check that non-external packages *don't* have these fields
            if "dependency_name" in entry:
                errors.append(f"Non-external integration '{name}' unexpectedly contains 'dependency_name'.")
            for field in version_fields_to_check:
                if field in entry:
                    errors.append(f"Non-external integration '{name}' unexpectedly contains '{field}'.")

    assert not errors, "\n".join(errors)


def test_external_dependencies_exist_on_pypi(registry_data: list[dict]):
    """
    Verify that package names listed in 'dependency_name' for external integrations
    can be found on PyPI using 'pip index versions'.
    """
    errors = []
    pip_command = [sys.executable, "-m", "pip"]

    print("\nChecking external dependencies against PyPI...")
    checked_packages = set()

    for entry in registry_data:
        if entry.get("is_external_package") is True:
            integration_name = entry.get("integration_name", "UNKNOWN_ENTRY")
            dependency_names = entry.get("dependency_name", [])

            if not dependency_names:
                continue

            if not isinstance(dependency_names, list):
                errors.append(
                    f"External integration '{integration_name}' has invalid dependency_name (not a list): "
                    + f"{dependency_names}"
                )
                continue

            for dep_name in dependency_names:
                if not isinstance(dep_name, str) or not dep_name:
                    errors.append(
                        f"External integration '{integration_name}' has invalid item in dependency_name list: "
                        + f"{dep_name}"
                    )
                    continue

                if dep_name in checked_packages:
                    continue
                checked_packages.add(dep_name)

                command = pip_command + ["index", "versions", dep_name]
                try:
                    result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=30)

                    if result.returncode != 0:
                        error_detail = f"Return Code: {result.returncode}"
                        if "no matching distribution found" in result.stderr.lower():
                            error_detail = "No matching distribution found on PyPI (or configured index)."
                        else:
                            error_detail += f"\n  Stderr: {result.stderr.strip()}"
                        errors.append(
                            f"Integration '{integration_name}': Dependency '{dep_name}' check failed. {error_detail}"
                        )

                except subprocess.TimeoutExpired:
                    errors.append(
                        f"Integration '{integration_name}': Timeout checking dependency '{dep_name}' on PyPI."
                    )
                except FileNotFoundError:
                    pytest.fail(
                        "Could not execute pip command. Ensure Python environment is correctly set up. Command: "
                        + f"{' '.join(command)}"
                    )
                except Exception as e:
                    errors.append(
                        f"Integration '{integration_name}': Unexpected error checking dependency '{dep_name}': {e}"
                    )

    assert not errors, "\n".join(errors)
