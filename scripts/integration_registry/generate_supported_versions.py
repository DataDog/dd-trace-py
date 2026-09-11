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
import argparse
import ast
from collections import defaultdict
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys

from packaging.requirements import InvalidRequirement
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


def _extract_supported_versions(py_file: Path) -> dict[str, str] | None:
    """Return the dict returned by an integration's ``_supported_versions`` function.

    The function is parsed statically (never imported) because the instrumented
    packages are not necessarily installed in the environment running this script.
    Returns ``None`` when the file does not define ``_supported_versions``.
    """
    module = ast.parse(py_file.read_text())
    for node in ast.walk(module):
        if not (isinstance(node, ast.FunctionDef) and node.name == "_supported_versions"):
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Return) and isinstance(child.value, ast.Dict):
                try:
                    value = ast.literal_eval(child.value)
                except (ValueError, SyntaxError):
                    return None
                if isinstance(value, dict):
                    return {str(key): str(range_) for key, range_ in value.items()}
    return None


def get_supported_ranges() -> tuple[dict[str, dict[str, str]], list[str]]:
    """Collect declared supported ranges per integration.

    Returns a tuple of ``(supported_ranges, integrations_without_declaration)`` where
    ``supported_ranges`` maps an integration name to the dict returned by its
    ``patch.py`` module's ``_supported_versions`` function, and the second element
    lists integrations with a ``patch.py`` module that do not implement it.
    """
    supported_ranges: dict[str, dict[str, str]] = {}
    missing: list[str] = []

    for path in sorted(CONTRIB_INTERNAL_ROOT.iterdir()):
        if not path.is_dir() or path.name == "__pycache__":
            continue

        patch_file = path / "patch.py"
        if not patch_file.is_file():
            continue

        declared = _extract_supported_versions(patch_file)

        if declared is None:
            missing.append(path.name)
        else:
            supported_ranges[path.name] = declared

    return supported_ranges, missing


def _normalize_dependency_name(name: str) -> str:
    return re.sub(r"[-.]", "_", name.lower())


def resolve_supported_range(
    dependency_name: str,
    declared_ranges: dict[str, str] | None,
    is_sole_dependency: bool,
) -> str | None:
    """Resolve the supported range for a dependency from an integration's declaration.

    ``_supported_versions`` is keyed by import/module name, which does not always match
    the distribution name used as ``dependencyName`` (e.g. ``psycopg2`` vs
    ``psycopg2-binary``, or a stdlib module vs its ``stdlib.*`` entry). We match by
    normalized name, then by longest shared-prefix match (which pairs distribution
    variants like ``psycopg2-binary`` with ``psycopg2`` without matching unrelated
    substrings). As a last resort, when the integration has a single dependency and
    declares a single range, that range is used even if the names differ (covers stdlib
    modules).
    """
    if not declared_ranges:
        return None

    normalized = {_normalize_dependency_name(key): range_ for key, range_ in declared_ranges.items()}
    dependency_norm = _normalize_dependency_name(dependency_name)

    if dependency_norm in normalized:
        return normalized[dependency_norm]

    best_match: tuple[str, str] | None = None
    for key_norm, range_ in normalized.items():
        if dependency_norm.startswith(key_norm) or key_norm.startswith(dependency_norm):
            if best_match is None or len(key_norm) > len(best_match[0]):
                best_match = (key_norm, range_)

    if best_match:
        return best_match[1]

    if is_sole_dependency and len(declared_ranges) == 1:
        return next(iter(declared_ranges.values()))

    return None


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
        try:
            requirement = Requirement(dependency)
        except InvalidRequirement:
            continue
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
            if integration_name in integration_names:
                if _environment_sets_latest_for_package(environment, integration_name):
                    integrations_setting_latest.add(integration_name)
                    pinned_integrations.discard(integration_name)
                elif integration_name not in integrations_setting_latest:
                    pinned_integrations.add(integration_name)
    return pinned_integrations


def build_versions(
    tested_versions: set[TestedVersion],
    supported_range: str | None,
) -> list[dict[str, object]]:
    """Group runtimes that tested the same set of package versions.

    Python versions whose tested package versions are identical are merged into a
    single entry to deduplicate repeated version lists.
    """
    tested_by_python: dict[str, set[str]] = defaultdict(set)
    for tested_version in tested_versions:
        tested_by_python[tested_version.python_version].add(tested_version.version)

    runtimes_by_tested: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for python_version, versions in tested_by_python.items():
        tested_sorted = tuple(sorted((version for version in versions if version), key=_version_sort_key))
        runtimes_by_tested[tested_sorted].append(python_version)

    entries: list[dict[str, object]] = []
    for tested_sorted, python_versions in runtimes_by_tested.items():
        entry: dict[str, object] = {
            "testedRuntimes": {"python": sorted(python_versions, key=_python_sort_key)},
        }
        if supported_range is not None:
            entry["supportedRange"] = supported_range
        entry["tested"] = list(tested_sorted)
        entries.append(entry)

    entries.sort(key=lambda entry: _python_sort_key(entry["testedRuntimes"]["python"][0]))
    return entries


def build_supported_versions_entries(
    tested_versions_per_integration: dict[str, dict[str, set[TestedVersion]]],
    supported_ranges: dict[str, dict[str, str]],
):
    """Build the JSON payload for supported_versions.json."""
    entries = []
    integration_names = set(get_integration_names())
    pinned_integrations = get_pinned_integrations(integration_names)

    for integration_name in sorted(integration_names):
        if integration_name == "__pycache__":
            continue

        dependency_names = set(get_dependency_names(integration_name))
        tested_versions_by_dependency = tested_versions_per_integration.get(integration_name, {})
        tested_dependency_names = set(tested_versions_by_dependency)
        declared_ranges = supported_ranges.get(integration_name)
        is_sole_dependency = len(tested_versions_by_dependency) == 1

        for dependency_name, tested_versions in sorted(tested_versions_by_dependency.items()):
            supported_range = resolve_supported_range(dependency_name, declared_ranges, is_sole_dependency)

            entry: dict[str, object] = {
                "dependencyName": dependency_name,
                "integrationName": integration_name,
                "autoInstrumented": is_auto_instrumented_package(integration_name),
                "versions": build_versions(tested_versions, supported_range),
            }

            aliases = sorted(dependency_names - tested_dependency_names)
            if aliases:
                entry["aliases"] = aliases

            if integration_name in pinned_integrations:
                entry["pinned"] = True

            entries.append(entry)

    return sorted(entries, key=lambda entry: (entry["integrationName"], entry["dependencyName"]))


def main() -> None:
    """Generate supported_versions.json from test environment lock files."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--main",
        action="store_true",
        help="Fail when integrations do not declare supported versions (intended for the main branch).",
    )
    args = parser.parse_args()

    tested_versions_per_integration = collect_tested_versions()
    supported_ranges, integrations_without_declaration = get_supported_ranges()

    SUPPORTED_VERSIONS_PATH.write_text(
        json.dumps(build_supported_versions_entries(tested_versions_per_integration, supported_ranges), indent=4) + "\n"
    )

    if args.main and integrations_without_declaration:
        print(
            "ERROR: the following integrations do not implement _supported_versions() and are missing "
            "a supported range:",
            file=sys.stderr,
        )
        for integration_name in integrations_without_declaration:
            print(f"  - {integration_name}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
