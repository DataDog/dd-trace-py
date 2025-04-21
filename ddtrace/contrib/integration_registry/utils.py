from collections import defaultdict
import pathlib
from typing import DefaultDict
from typing import Dict
from typing import List
from typing import Optional

import yaml


def get_integration_to_dependency_map(special_cases: Optional[Dict[str, str]] = None) -> DefaultDict[str, set]:
    REGISTRY_YAML_PATH = pathlib.Path("ddtrace/contrib/integration_registry/registry.yaml")
    dependency_map: DefaultDict[str, set] = defaultdict(set)

    with open(REGISTRY_YAML_PATH, "r", encoding="utf-8") as f:
        registry_content = yaml.safe_load(f)

    integrations_list = registry_content["integrations"]

    for index, entry in enumerate(integrations_list):
        integration_name = entry.get("integration_name").lower()
        dependency_names = entry.get("dependency_name")

        if dependency_names is None:
            valid_dependency_names = set()
        elif isinstance(dependency_names, list):
            valid_dependency_names = set(dependency_name.lower() for dependency_name in dependency_names)

        dependency_map[integration_name] = valid_dependency_names

    for dependency, integration in special_cases.items():
        dependency_map[integration].add(dependency)

    return dependency_map


def invert_integration_to_dependency_map(integration_to_deps: Dict[str, List[str]]) -> Dict[str, str]:
    dependency_to_integration_map: Dict[str, str] = {}
    for integration, dependency_list in integration_to_deps.items():
        for dependency in dependency_list:
            dependency_to_integration_map[dependency.lower()] = integration.lower()

    return dependency_to_integration_map
