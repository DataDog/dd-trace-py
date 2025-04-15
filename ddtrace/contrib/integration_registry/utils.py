import pathlib
from typing import Dict, List, Optional

import yaml

def get_integration_to_dependency_map() -> Optional[Dict[str, List[str]]]:
    REGISTRY_YAML_PATH = pathlib.Path("ddtrace/contrib/integration_registry/registry.yaml")

    dependency_map: Dict[str, List[str]] = {}

    with open(REGISTRY_YAML_PATH, "r", encoding="utf-8") as f:
        registry_content = yaml.safe_load(f)

    integrations_list = registry_content["integrations"]

    for index, entry in enumerate(integrations_list):
        integration_name = entry.get("integration_name")
        dependency_names = entry.get("dependency_name")

        if dependency_names is None:
            valid_dependency_names = []
        elif isinstance(dependency_names, list):
                valid_dependency_names = dependency_names
        dependency_map[integration_name] = valid_dependency_names
    return dependency_map


def invert_integration_to_dependency_map(
    integration_to_deps: Dict[str, List[str]]
) -> Dict[str, str]:
    return {
        dependency: integration
        for integration, dependency_list in integration_to_deps.items()
        if isinstance(dependency_list, list)
        for dependency in dependency_list
        if isinstance(dependency, str) and dependency
    }
