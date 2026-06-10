import ast
from collections import defaultdict
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
from typing import Any

from packaging.version import Version


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))

from mappings import INTEGRATION_TO_DEPENDENCY_MAPPING  # noqa: E402
from pypi_python_support import compatible_python_versions  # noqa: E402


CONTRIB_INTERNAL_ROOT = PROJECT_ROOT / "ddtrace" / "contrib" / "internal"
DDTRACE_MONKEY_PATH = PROJECT_ROOT / "ddtrace" / "_monkey.py"
SUPPORTED_VERSIONS_PATH = PROJECT_ROOT / "supported_versions.json"

REQUIREMENTS_DIR = PROJECT_ROOT / ".riot" / "requirements"
REQUIREMENT_RE = re.compile(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?==([^;\s]+)")
LATEST = ""


@dataclass(frozen=True)
class RiotVenv:
    name: str
    python_version: str


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


def get_riot_hash_to_venvs() -> dict[str, RiotVenv]:
    """Map each generated riot requirements hash to its riot venv metadata."""
    import riotfile

    riot_venvs = {}
    for instance in riotfile.venv.instances():
        if not instance.name:
            continue
        riot_venvs[instance.short_hash] = RiotVenv(
            name=instance.name.lower(),
            python_version=instance.py._hint,
        )
    return riot_venvs


def parse_locked_versions(requirements_path: Path) -> dict[str, str]:
    """Parse a generated requirements file into dependency names and locked versions."""
    locked_versions = {}
    for line in requirements_path.read_text().splitlines():
        match = REQUIREMENT_RE.match(line)
        if match:
            dependency, version = match.groups()
            locked_versions[dependency.lower()] = version
    return locked_versions


def collect_tested_versions() -> tuple[dict[str, dict[str, set[TestedVersion]]], set[str]]:
    """Collect tested dependency versions by integration and Python version."""
    tested_versions: dict[str, dict[str, set[TestedVersion]]] = defaultdict(lambda: defaultdict(set))
    python_versions = set()
    riot_hash_to_venvs = get_riot_hash_to_venvs()

    for requirements_path in sorted(REQUIREMENTS_DIR.glob("*.txt")):
        riot_hash = requirements_path.stem
        riot_venv = riot_hash_to_venvs.get(riot_hash, None)

        if not riot_venv:
            continue

        python_versions.add(riot_venv.python_version)
        integration_name = riot_venv.name.split(":", 1)[0]

        if is_stdlib_package(integration_name):
            tested_versions[integration_name][f"stdlib.{integration_name}"].add(
                TestedVersion(
                    version="",
                    python_version=riot_venv.python_version,
                )
            )
            continue

        dependency_names = get_dependency_names(integration_name)
        if not dependency_names:
            continue

        locked_versions = parse_locked_versions(requirements_path)
        for dependency in dependency_names:
            version = locked_versions.get(dependency.lower())
            if version:
                tested_versions[integration_name][dependency].add(
                    TestedVersion(
                        version=version,
                        python_version=riot_venv.python_version,
                    )
                )
    return tested_versions, python_versions


def _version_sort_key(version: str) -> tuple[int, Version]:
    if version == "":
        return (0, Version("0"))
    return (1, Version(version))


def _python_sort_key(python_version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in python_version.split("."))


def _venv_sets_latest_for_package(venv: Any, suite_name: str) -> bool:
    packages = get_dependency_names(suite_name) or [suite_name]
    venv_packages = {package.lower(): version for package, version in venv.pkgs.items()}

    for package in packages:
        if package.lower() in venv_packages and LATEST in venv_packages[package.lower()]:
            return True

    return any(_venv_sets_latest_for_package(child_venv, suite_name) for child_venv in venv.venvs)


def get_pinned_integrations(integration_names: set[str]) -> set[str]:
    """Return integrations that do not have any riot venv setting the dependency to latest."""
    import riotfile

    pinned_integrations = set()
    integrations_setting_latest = set()

    def recurse_venvs(venvs: list[Any], inherited_name: str | None = None) -> None:
        for venv in venvs:
            venv_name = (venv.name or inherited_name or "").lower()
            integration_name = venv_name.split(":", 1)[0]

            if integration_name in integration_names:
                if _venv_sets_latest_for_package(venv, integration_name):
                    integrations_setting_latest.add(integration_name)
                    pinned_integrations.discard(integration_name)
                elif integration_name not in integrations_setting_latest:
                    pinned_integrations.add(integration_name)

            recurse_venvs(venv.venvs, venv_name)

    recurse_venvs(riotfile.venv.venvs)
    return pinned_integrations


def build_python_versions(
    dependency_name: str,
    tested_versions: set[TestedVersion],
    candidate_python_versions: tuple[str, ...],
) -> dict[str, dict[str, object]]:
    tested_by_python: dict[str, set[str]] = defaultdict(set)
    supported_by_python: dict[str, set[str]] = defaultdict(set)

    for tested_version in tested_versions:
        tested_by_python[tested_version.python_version].add(tested_version.version)

    for version in {tested_version.version for tested_version in tested_versions}:
        if version == "":
            compatible_versions = set(tested_by_python)
        else:
            compatible_versions = set(compatible_python_versions(dependency_name, version, candidate_python_versions))
            compatible_versions.update(
                python_version for python_version, versions in tested_by_python.items() if version in versions
            )

        for python_version in compatible_versions:
            supported_by_python[python_version].add(version)

    python_versions = {}
    for python_version in sorted(supported_by_python, key=_python_sort_key):
        supported_versions = sorted(supported_by_python[python_version], key=_version_sort_key)
        python_versions[python_version] = {
            "minimum_package_version": supported_versions[0],
            "maximum_package_version": supported_versions[-1],
            "tested_versions": sorted(tested_by_python.get(python_version, set()), key=_version_sort_key),
        }

    return python_versions


def to_json_data(tested_versions_per_integration: dict[str, dict[str, set[TestedVersion]]], python_versions: set[str]):
    """Build the JSON payload for supported_versions.json."""
    entries = []
    integration_names = set(get_integration_names())
    pinned_integrations = get_pinned_integrations(integration_names)
    candidate_python_versions = tuple(sorted(python_versions, key=_python_sort_key))

    for integration_name in sorted(integration_names):
        if integration_name == "__pycache__":
            continue

        for dependency_name, tested_versions in sorted(
            tested_versions_per_integration.get(integration_name, {}).items()
        ):
            entry = {
                "dependency": dependency_name,
                "integration": integration_name,
                "auto-instrumented": is_auto_instrumented_package(integration_name),
                "python_versions": build_python_versions(
                    dependency_name,
                    tested_versions,
                    candidate_python_versions,
                ),
            }

            if integration_name in pinned_integrations:
                entry["pinned"] = "true"

            entries.append(entry)

    return sorted(entries, key=lambda entry: (entry["integration"], entry["dependency"]))


def main() -> None:
    """Generate supported_versions.json from riot requirement lock files."""
    tested_versions_per_integration, python_versions = collect_tested_versions()
    SUPPORTED_VERSIONS_PATH.write_text(
        json.dumps(to_json_data(tested_versions_per_integration, python_versions), indent=4) + "\n"
    )


if __name__ == "__main__":
    main()
