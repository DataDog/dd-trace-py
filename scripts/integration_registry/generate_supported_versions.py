#!/usr/bin/env scripts/uv-run-script
# -*- mode: python -*-
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "packaging>=23.1,<24",
#     "pyyaml>=6,<7",
#     "riot>=0.22.0",
# ]
# ///
import ast
from collections import defaultdict
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shlex
import sys
from typing import Any

from packaging.version import Version


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))

from mappings import INTEGRATION_TO_DEPENDENCY_MAPPING  # noqa: E402

import riotfile  # noqa: E402


CONTRIB_INTERNAL_ROOT = PROJECT_ROOT / "ddtrace" / "contrib" / "internal"
DDTRACE_MONKEY_PATH = PROJECT_ROOT / "ddtrace" / "_monkey.py"
SUPPORTED_VERSIONS_PATH = PROJECT_ROOT / "supported_versions.json"

LOCKS_DIR = PROJECT_ROOT / ".uv"
# Allows the version of a dependency to be read from a generated lock when it is formatted
# like anyio==4.9.0
REQUIREMENT_RE = re.compile(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?==([^;\s]+)")
LOCK_NAME_RE = re.compile(r"^(.+)--py(\d)(\d+)--[0-9a-f]{12}$")
LATEST = ""


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


def get_riot_names() -> dict[str, str]:
    """Map uv lock names to their original Riot names."""
    riot_names = {}
    for instance in riotfile.venv.instances():
        if not instance.name:
            continue
        name = instance.name.lower()
        slug = re.sub(r"[^a-z0-9]+", "-", name).strip("-")
        dependencies = sorted(shlex.split(str(instance.full_pkg_str)), key=str.casefold)
        input_hash = hashlib.sha256("\0".join(dependencies).encode()).hexdigest()[:8]
        for lock_name in (slug, f"{slug}-{input_hash}"):
            previous = riot_names.setdefault(lock_name, name)
            if previous != name:
                raise ValueError(f"Riot environment names share the lockfile name {lock_name}: {previous}, {name}")
    return riot_names


def parse_locked_versions(requirements_path: Path) -> dict[str, str]:
    """Parse a generated requirements file into dependency names and locked versions."""
    locked_versions = {}
    for line in requirements_path.read_text().splitlines():
        match = REQUIREMENT_RE.match(line)
        if match:
            dependency, version = match.groups()
            locked_versions[dependency.lower()] = version
    return locked_versions


def collect_tested_versions() -> dict[str, dict[str, set[TestedVersion]]]:
    """Collect tested dependency versions by integration and Python version."""
    tested_versions: dict[str, dict[str, set[TestedVersion]]] = defaultdict(lambda: defaultdict(set))
    riot_names = get_riot_names()

    for requirements_path in sorted(LOCKS_DIR.glob("*.txt")):
        match = LOCK_NAME_RE.fullmatch(requirements_path.stem)
        if not match:
            continue
        lock_name, python_major, python_minor = match.groups()
        riot_name = riot_names.get(lock_name)
        if riot_name is None:
            continue

        integration_name = riot_name.split(":", 1)[0]
        python_version = f"{python_major}.{python_minor}"

        dependency_names = get_dependency_names(integration_name)
        found_dependency_version = False

        if dependency_names:
            locked_versions = parse_locked_versions(requirements_path)
            for dependency in dependency_names:
                version = locked_versions.get(dependency.lower())
                if version:
                    found_dependency_version = True
                    tested_versions[integration_name][dependency].add(
                        TestedVersion(
                            version=version,
                            python_version=python_version,
                        )
                    )

        if is_stdlib_package(integration_name) and not found_dependency_version:
            tested_versions[integration_name][f"stdlib.{integration_name}"].add(
                TestedVersion(
                    version="",
                    python_version=python_version,
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


def _venv_sets_latest_for_package(venv: Any, suite_name: str) -> bool:
    packages = get_dependency_names(suite_name) or [suite_name]
    venv_packages = {package.lower(): version for package, version in venv.pkgs.items()}

    for package in packages:
        if package.lower() in venv_packages and LATEST in venv_packages[package.lower()]:
            return True
    return any(_venv_sets_latest_for_package(child_venv, suite_name) for child_venv in venv.venvs)


def get_pinned_integrations(integration_names: set[str]) -> set[str]:
    """Return integrations that do not have any riot venv setting the dependency to latest."""
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
