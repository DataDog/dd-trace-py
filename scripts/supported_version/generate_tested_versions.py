from collections import defaultdict
import ast
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))

CONTRIB_INTERNAL_ROOT = PROJECT_ROOT / "ddtrace" / "contrib" / "internal"
DDTRACE_MONKEY_PATH = PROJECT_ROOT / "ddtrace" / "_monkey.py"
DEPENDENCY_NAMES_PATH = PROJECT_ROOT / "scripts" / "supported_version" / "dependency_names.json"
CI_TESTED_VERSIONS_PATH = PROJECT_ROOT / "tested_versions.json"

REQUIREMENTS_DIR = PROJECT_ROOT / ".riot" / "requirements"
REQUIREMENT_RE = re.compile(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?==([^;\s]+)")


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
    dependency_names = json.loads(DEPENDENCY_NAMES_PATH.read_text())
    for integration in dependency_names["integrations"]:
        if integration["integration_name"] == package_name:
            return integration["dependency_names"]
    return []


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
            patch_modules[key.value] = value.value if isinstance(value, ast.Constant) and isinstance(value.value, bool) else False
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


def collect_tested_versions() -> dict[str, dict[str, list[TestedVersion]]]:
    """Collect tested dependency versions by integration and Python version."""
    tested_versions: dict[str, dict[str, list[TestedVersion]]] = defaultdict(lambda: defaultdict(list))
    riot_hash_to_venvs = get_riot_hash_to_venvs()

    for requirements_path in sorted(REQUIREMENTS_DIR.glob("*.txt")):
        riot_hash = requirements_path.stem
        riot_venv = riot_hash_to_venvs.get(riot_hash, None)

        if not riot_venv:
            continue

        integration_name = riot_venv.name.split(":", 1)[0]

        if is_stdlib_package(integration_name):
            tested_versions[integration_name][f"stdlib.{integration_name}"].append(
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
                tested_versions[integration_name][dependency].append(
                    TestedVersion(
                        version=version,
                        python_version=riot_venv.python_version,
                    )
                )
    return tested_versions


def to_json_data(tested_versions_per_integration: dict[str, dict[str, list[TestedVersion]]]) -> list[dict[str, object]]:
    """Build one JSON-serializable entry per dependency and integration."""
    entries = []

    for integration_name in get_integration_names():
        if integration_name == "__pycache__":
            continue

        for dependency_name, tested_versions in sorted(
            tested_versions_per_integration.get(integration_name, {}).items()
        ):
            entries.append(
                {
                    "dependency_name": dependency_name,
                    "integration_name": integration_name,
                    "auto_instrumented": is_auto_instrumented_package(integration_name),
                    "tested_versions": sorted(
                        [
                            {
                                "version": tested_version.version,
                                "python_version": tested_version.python_version,
                            }
                            for tested_version in set(tested_versions)
                        ],
                        key=lambda tested_version: (
                            # Sort numerically so 3.9 comes before 3.10.
                            tuple(int(part) for part in tested_version["python_version"].split(".")),
                            tested_version["version"],
                        ),
                    ),
                }
            )

    return sorted(entries, key=lambda entry: (entry["dependency_name"], entry["integration_name"]))


def main() -> None:
    """Generate the supported-version JSON from riot requirement lock files."""
    tested_versions_per_integration = collect_tested_versions()
    CI_TESTED_VERSIONS_PATH.write_text(json.dumps(to_json_data(tested_versions_per_integration), indent=2) + "\n")


if __name__ == "__main__":
    main()
