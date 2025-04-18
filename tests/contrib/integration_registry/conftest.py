import json
from pathlib import Path
from typing import Any
from typing import List
from typing import Set

import pytest
import yaml

import riotfile


@pytest.fixture(scope="module")
def project_root() -> Path:
    return Path(__file__).parent.parent.parent.parent


@pytest.fixture(scope="module")
def contrib_dir(project_root: Path) -> Path:
    return project_root / "ddtrace" / "contrib"


@pytest.fixture(scope="module")
def internal_contrib_dir(contrib_dir: Path) -> Path:
    return contrib_dir / "internal"


@pytest.fixture(scope="module")
def registry_yaml_path(contrib_dir: Path) -> Path:
    """Returns the path to the registry.yaml file."""
    return contrib_dir / "integration_registry" / "registry.yaml"


@pytest.fixture(scope="module")
def registry_schema_path(contrib_dir: Path) -> Path:
    """Returns the path to the registry.schema.json file."""
    return contrib_dir / "integration_registry" / "_registry_schema.json"


@pytest.fixture(scope="module")
def registry_content(registry_yaml_path: Path) -> dict:
    """Loads the entire content of registry.yaml as a dictionary."""
    if not registry_yaml_path.is_file():
        pytest.fail(f"Registry YAML file not found: {registry_yaml_path}")
    try:
        with open(registry_yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            if not isinstance(data, dict):
                pytest.fail(f"Invalid structure in {registry_yaml_path}: Expected root object.")
            return data
    except yaml.YAMLError as e:
        pytest.fail(f"Error parsing YAML file {registry_yaml_path}: {e}")
    except Exception as e:
        pytest.fail(f"Error reading file {registry_yaml_path}: {e}")
    return {}


@pytest.fixture(scope="module")
def registry_data(registry_content: dict, registry_yaml_path: Path) -> list[dict]:
    """Extracts the list of integrations from the loaded YAML content."""
    integrations = registry_content.get("integrations")
    if not isinstance(integrations, list):
        pytest.fail(f"Invalid structure in {registry_yaml_path}: Expected 'integrations' key with a list value.")
    if not all(isinstance(item, dict) for item in integrations):
        pytest.fail(f"Invalid structure in {registry_yaml_path}: 'integrations' list should contain objects.")
    return integrations


@pytest.fixture(scope="module")
def registry_schema(registry_schema_path: Path) -> dict:
    """Loads the JSON schema definition."""
    if not registry_schema_path.is_file():
        pytest.fail(f"Schema JSON file not found: {registry_schema_path}")
    try:
        with open(registry_schema_path, "r", encoding="utf-8") as f:
            schema = json.load(f)
        return schema
    except json.JSONDecodeError as e:
        pytest.fail(f"Error parsing JSON schema file {registry_schema_path}: {e}")
    except Exception as e:
        pytest.fail(f"Error reading schema file {registry_schema_path}: {e}")
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
def external_integration_names(registry_data: list[dict]) -> set[str]:
    """Extracts names of integrations marked as 'is_external_package: true'."""
    # This fixture depends on registry_data (from conftest.py) and remains here
    names = set()
    for entry in registry_data:
        name = entry.get("integration_name")
        is_external = entry.get("is_external_package")
        if name and isinstance(name, str) and is_external is True:
            names.add(name)
    return names


@pytest.fixture(scope="module")
def untested_integrations(registry_data: list[dict]) -> set[str]:
    """Extracts names of integrations marked as 'is_tested: false'."""
    # This fixture depends on registry_data (from conftest.py) and remains here
    names = set()
    for entry in registry_data:
        name = entry.get("integration_name")
        is_tested = entry.get("is_tested")
        if name and isinstance(name, str) and is_tested is False:
            names.add(name)

    # TODO: wconnti27: remove this and ensure this list populates registry.yaml
    from ddtrace.contrib.integration_registry.mappings import EXCLUDED_FROM_TESTING

    for name in EXCLUDED_FROM_TESTING:
        names.add(name)
    return names


@pytest.fixture(scope="module")
def integration_dir_names(internal_contrib_dir: Path) -> set[str]:
    """
    Scans ddtrace/contrib/internal and returns a set of all directory names.
    """
    names = set()
    if not internal_contrib_dir.is_dir():
        pytest.fail(f"Contrib internal directory not found: {internal_contrib_dir}")

    for item in internal_contrib_dir.iterdir():
        if item.is_dir() and item.name != "__pycache__":
            names.add(item.name)

    if not names:
        pytest.fail(f"No directories (excluding __pycache__) found in {internal_contrib_dir}")
    return names


@pytest.fixture(scope="module")
def riot_venvs() -> set[str]:
    """Gets all Venv defined in riotfile.py."""
    return riotfile.venv.venvs


@pytest.fixture(scope="module")
def riot_venv_names() -> set[str]:
    """Recursively finds all Venv names defined in riotfile.py."""

    names: Set[str] = set()
    nodes_to_visit: List[Any] = [riotfile.venv]

    while nodes_to_visit:
        current_node = nodes_to_visit.pop()
        if hasattr(current_node, "name") and isinstance(current_node.name, str):
            names.add(current_node.name)

        if hasattr(current_node, "venvs") and isinstance(current_node.venvs, list):
            nodes_to_visit.extend(current_node.venvs)

    if not names:
        pytest.fail("No integration Venv names found in riotfile.venv structure.")
    return names
