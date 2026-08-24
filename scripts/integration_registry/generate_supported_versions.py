#!/usr/bin/env scripts/uv-run-script
# -*- mode: python -*-
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "packaging>=23.1,<24",
#     "pyyaml>=6,<7",
#     "ruamel.yaml>=0.17.21",
# ]
# ///
import ast
from collections import defaultdict
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys

from packaging.requirements import Requirement
from packaging.version import Version


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))

from mappings import INTEGRATION_TO_DEPENDENCY_MAPPING  # noqa: E402

from tests.suitespec import TestEnvironment  # noqa: E402
from tests.suitespec import get_test_environments  # noqa: E402


CONTRIB_INTERNAL_ROOT = PROJECT_ROOT / "ddtrace" / "contrib" / "internal"
DDTRACE_MONKEY_PATH = PROJECT_ROOT / "ddtrace" / "_monkey.py"
SUPPORTED_VERSIONS_PATH = PROJECT_ROOT / "supported_versions.json"

REQUIREMENT_RE = re.compile(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?==([^;\s]+)")
PYTHON_VERSION_RE = re.compile(r"^\d+\.\d+$")


@dataclass(frozen=True)
class TestedVersion:
    version: str
    python_version: str


def get_integration_names() -> list[str]:
    """Return the integration names that have an internal contrib package."""
    return sorted(path.name for path in CONTRIB_INTERNAL_ROOT.iterdir() if path.is_dir())


def get_dependency_names(package_name: str) -> list[str]:
    """Return the dependency package names that should be checked for an integration."""
    return sorted(INTEGRATION_TO_DEPENDENCY_MAPPING.get(package_name, set()))


def is_stdlib_package(package_name: str) -> bool:
    """Return whether an integration targets a module from Python's standard library."""
    root_package = package_name.partition(".")[0]
    return root_package in sys.stdlib_module_names


def is_auto_instrumented_package(package_name: str) -> bool:
    """Return whether the integration is enabled by ddtrace.patch_all()."""
    return package_name in PATCH_MODULES and PATCH_MODULES[package_name]


def get_patch_modules() -> dict[str, bool]:
    """Extract PATCH_MODULES without importing ddtrace runtime dependencies."""
    module = ast.parse(DDTRACE_MONKEY_PATH.read_text())
    for node in module.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "PATCH_MODULES" for target in node.targets):
            continue
        if not isinstance(node.value, ast.Dict):
            raise ValueError("PATCH_MODULES must be defined as a dictionary")

        patch_modules = {}
        for key, value in zip(node.value.keys, node.value.values):
            if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                raise ValueError("PATCH_MODULES keys must be string literals")
            patch_modules[key.value] = (
                value.value if isinstance(value, ast.Constant) and isinstance(value.value, bool) else False
            )
        return patch_modules

    raise ValueError("PATCH_MODULES was not found")


PATCH_MODULES = get_patch_modules()


def parse_locked_versions(requirements_path: Path) -> dict[str, str]:
    """Parse a generated requirements file into dependency names and locked versions."""
    locked_versions = {}
    for line in requirements_path.read_text().splitlines():
        match = REQUIREMENT_RE.match(line)
        if match:
            dependency, version = match.groups()
            locked_versions[dependency.lower()] = version
    return locked_versions


def is_concrete_python_version(python_version: str) -> bool:
    """Return whether a Python value identifies one concrete major.minor runtime."""
    return PYTHON_VERSION_RE.match(python_version) is not None


def collect_tested_versions() -> dict[str, dict[str, set[TestedVersion]]]:
    """Collect tested dependency versions by integration and Python version."""
    tested_versions: dict[str, dict[str, set[TestedVersion]]] = defaultdict(lambda: defaultdict(set))
    environments = (
        environment
        for suite_environments in get_test_environments(nightly=False).values()
        for environment in suite_environments
    )
    for environment in environments:
        if not is_concrete_python_version(environment.python):
            continue
        integration_name = environment.integration_name

        dependency_names = get_dependency_names(integration_name)
        found_dependency_version = False

        if dependency_names:
            locked_versions = parse_locked_versions(PROJECT_ROOT / environment.lockfile)
            for dependency in dependency_names:
                version = locked_versions.get(dependency.lower())
                if version:
                    found_dependency_version = True
                    tested_versions[integration_name][dependency].add(
                        TestedVersion(
                            version=version,
                            python_version=environment.python,
                        )
                    )

        if is_stdlib_package(integration_name) and not found_dependency_version:
            tested_versions[integration_name][f"stdlib.{integration_name}"].add(
                TestedVersion(
                    version="",
                    python_version=environment.python,
                )
            )
            continue

    return tested_versions


def _version_sort_key(version: str) -> tuple[int, Version]:
    if version == "":
        return (0, Version("0"))
    return (1, Version(version))


def _python_sort_key(python_version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in python_version.split("."))


def _environment_sets_latest_for_package(environment: TestEnvironment, integration_name: str) -> bool:
    packages = {package.lower() for package in get_dependency_names(integration_name) or [integration_name]}
    for dependency in environment.direct_dependencies:
        requirement = Requirement(dependency)
        if requirement.name.lower() in packages and not requirement.specifier:
            return True
    return False


def get_pinned_integrations(integration_names: set[str]) -> set[str]:
    """Return integrations that do not have an environment testing the latest dependency."""
    pinned_integrations = set()
    integrations_setting_latest = set()
    for suite_environments in get_test_environments(nightly=False).values():
        for environment in suite_environments:
            integration_name = environment.integration_name
            if integration_name not in integration_names:
                continue
            if _environment_sets_latest_for_package(environment, integration_name):
                integrations_setting_latest.add(integration_name)
                pinned_integrations.discard(integration_name)
            elif integration_name not in integrations_setting_latest:
                pinned_integrations.add(integration_name)
    return pinned_integrations


def build_python_versions(
    tested_versions: set[TestedVersion],
) -> dict[str, dict[str, object]]:
    tested_by_python: dict[str, set[str]] = defaultdict(set)

    for tested_version in tested_versions:
        tested_by_python[tested_version.python_version].add(tested_version.version)

    python_versions = {}
    for python_version in sorted(tested_by_python, key=_python_sort_key):
        tested_python_versions = tested_by_python[python_version]
        supported_versions = sorted(tested_python_versions, key=_version_sort_key)
        python_versions[python_version] = {
            "minimum_package_version": supported_versions[0],
            "maximum_package_version": supported_versions[-1],
            "tested_versions": supported_versions,
        }

    return python_versions


def build_supported_versions_entries(tested_versions_per_integration: dict[str, dict[str, set[TestedVersion]]]):
    """Build the JSON payload for supported_versions.json."""
    entries = []
    integration_names = set(get_integration_names())
    pinned_integrations = get_pinned_integrations(integration_names)

    for integration_name in sorted(integration_names):
        if integration_name == "__pycache__":
            continue

        dependency_names = set(get_dependency_names(integration_name))
        tested_dependency_names = set(tested_versions_per_integration.get(integration_name, {}))

        for dependency_name, tested_versions in sorted(
            tested_versions_per_integration.get(integration_name, {}).items()
        ):
            entry = {
                "dependency": dependency_name,
            }

            aliases = sorted(dependency_names - tested_dependency_names)
            if aliases:
                entry["aliases"] = aliases

            entry.update(
                {
                    "integration": integration_name,
                    "auto-instrumented": is_auto_instrumented_package(integration_name),
                    "python_versions": build_python_versions(
                        tested_versions,
                    ),
                }
            )

            if integration_name in pinned_integrations:
                entry["pinned"] = True

            entries.append(entry)

    return sorted(entries, key=lambda entry: (entry["integration"], entry["dependency"]))


def main() -> None:
    """Generate supported_versions.json from uv test locks."""
    tested_versions_per_integration = collect_tested_versions()
    SUPPORTED_VERSIONS_PATH.write_text(
        json.dumps(build_supported_versions_entries(tested_versions_per_integration), indent=4) + "\n"
    )


if __name__ == "__main__":
    main()
