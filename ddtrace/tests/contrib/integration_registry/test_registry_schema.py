from pathlib import Path

import jsonschema
import pytest


def test_registry_conforms_to_schema(registry_yaml_path: Path, registry_content: dict, registry_schema: dict):
    """Validates registry.yaml content against the defined JSON schema."""
    if jsonschema is None:
        pytest.skip("jsonschema library not installed, skipping schema validation.")

    try:
        jsonschema.validate(instance=registry_content, schema=registry_schema)
    except jsonschema.ValidationError as e:
        pytest.fail(f"Schema validation failed for {registry_yaml_path}:\n{e}", pytrace=False)
    except Exception as e:
        pytest.fail(f"An unexpected error occurred during schema validation: {e}")


def test_all_internal_dirs_accounted_for(
    registry_yaml_path: Path, internal_contrib_dir: Path, project_root: Path, all_integration_names: set[str]
):
    """
    Verify that every directory within ddtrace/contrib/internal is listed
    as an 'integration_name' in registry.yaml.
    """

    accounted_for_dirs = all_integration_names
    found_dirs_on_disk = set()
    unaccounted_dirs = []

    for item in internal_contrib_dir.iterdir():
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
            f"    The following directories exist in '{internal_contrib_dir.relative_to(project_root)}' \n"
            f"    but do NOT have a corresponding 'integration_name' entry in '{registry_yaml_path.name}'.\n"
            f"    Please add entries for them:\n"
            f"      - " + "\n      - ".join(sorted(unaccounted_dirs)) + "\n"
        )
    if missing_dirs_in_yaml:
        error_messages.append(
            f"\n\nMissing Directories for YAML Entries:\n"
            f"    The following integration names are listed in '{registry_yaml_path.name}'\n"
            f"    but do not have a corresponding directory in '{internal_contrib_dir.relative_to(project_root)}':\n\n"
            f"      - " + "\n      - ".join(sorted(list(missing_dirs_in_yaml))) + "\n"
        )

    assert not error_messages, "\n".join(error_messages)
