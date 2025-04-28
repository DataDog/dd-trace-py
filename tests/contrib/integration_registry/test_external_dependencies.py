import subprocess
import sys
from typing import Any
from typing import Dict
from typing import List

from packaging.version import InvalidVersion
from packaging.version import parse as parse_version
import pytest


def _validate_version_str(version_str: str, field_name: str, integration_name: str, dep_name: str) -> List[str]:
    errors = []
    if version_str != "N/A":
        try:
            parse_version(version_str)
        except InvalidVersion:
            errors.append(
                f"Integration '{integration_name}', Dependency '{dep_name}': Invalid SemVer format for '{field_name}': "
                f"'{version_str}'."
            )
    return errors


def _validate_external_tested_entry(entry: Dict[str, Any], name: str) -> List[str]:
    errors = []
    dependency_names = entry.get("dependency_names")
    if not dependency_names:
        errors.append(f"External tested integration '{name}' is missing required field 'dependency_names'.")
    elif not isinstance(dependency_names, list) or not all(isinstance(d, str) and d for d in dependency_names):
        errors.append(
            f"External integration '{name}' has an empty or invalid 'dependency_names' field (must be a non-empty "
            "list of strings)."
        )

    version_map = entry.get("tested_versions_by_dependency")
    if not version_map:
        if dependency_names:
            errors.append(
                f"External tested integration '{name}' has 'dependency_names' but is missing "
                "'tested_versions_by_dependency' map."
            )
    elif not isinstance(version_map, dict):
        errors.append(f"External integration '{name}' field 'tested_versions_by_dependency' is not a dictionary.")
    elif dependency_names:  # Only validate map content if dependency_names is valid
        map_keys = set(version_map.keys())
        dep_name_set = set(dependency_names)

        if map_keys != dep_name_set:
            missing_in_map = dep_name_set - map_keys
            extra_in_map = map_keys - dep_name_set
            if missing_in_map:
                errors.append(
                    f"Integration '{name}': Dependencies {sorted(list(missing_in_map))} listed in 'dependency_names' "
                    "are missing from 'tested_versions_by_dependency' map."
                )
            if extra_in_map:
                errors.append(
                    f"Integration '{name}': Dependencies {sorted(list(extra_in_map))} found in "
                    "'tested_versions_by_dependency' map are not listed in 'dependency_names'."
                )

        for dep_name, version_info in version_map.items():
            if not isinstance(version_info, dict):
                errors.append(
                    f"Integration '{name}', Dependency '{dep_name}': Value in 'tested_versions_by_dependency' is not a "
                    "dictionary."
                )
                continue
            min_v = version_info.get("min")
            max_v = version_info.get("max")
            if min_v is None and max_v is None:
                errors.append(
                    f"Integration '{name}', Dependency '{dep_name}': Version info block must contain at least "
                    "'min' or 'max'."
                )
                continue

            if min_v is not None:
                errors.extend(_validate_version_str(min_v, "min", name, dep_name))
            if max_v is not None:
                errors.extend(_validate_version_str(max_v, "max", name, dep_name))
    return errors


def _validate_non_external_entry(entry: Dict[str, Any], name: str) -> List[str]:
    errors = []
    unexpected_fields = [
        "dependency_names",
        "tested_versions_by_dependency",
        "supported_version_min",
        "supported_version_max",
    ]
    for field in unexpected_fields:
        if field in entry:
            errors.append(f"Non-external integration '{name}' unexpectedly contains field '{field}'.")
    return errors


def test_external_package_requirements(registry_data: list[dict]):
    all_errors: list[str] = []

    for entry in registry_data:
        name = entry.get("integration_name", f"UNKNOWN_ENTRY_AT_INDEX_{registry_data.index(entry)}")
        is_external = entry.get("is_external_package")
        is_tested = entry.get("is_tested", True)

        if is_external is None:
            all_errors.append(f"Integration '{name}' is missing the required 'is_external_package' field.")
            continue

        if is_external:
            # special case since we have a few external integrations that are not tested
            # (ie: aioredis which we plan to deprecate)
            if is_tested is not False:
                all_errors.extend(_validate_external_tested_entry(entry, name))
        else:
            all_errors.extend(_validate_non_external_entry(entry, name))

    assert not all_errors, "\n".join(all_errors)


def test_external_dependencies_exist_on_pypi(registry_data: list[dict]):
    """
    Verify that package names listed in 'dependency_names' for external integrations
    can be found on PyPI using 'pip index versions'.
    """
    errors = []
    pip_command = [sys.executable, "-m", "pip"]

    print("\nChecking external dependencies against PyPI...")
    checked_packages = set()

    for entry in registry_data:
        if not entry.get("is_external_package"):
            continue

        integration_name = entry.get("integration_name", "UNKNOWN_ENTRY")
        dependency_names = entry.get("dependency_names", [])

        if not dependency_names:
            continue

        if not isinstance(dependency_names, list):
            errors.append(
                f"External integration '{integration_name}' has invalid dependency_names (not a list): "
                f"{dependency_names}"
            )
            continue

        for dep_name in dependency_names:
            if not isinstance(dep_name, str) or not dep_name:
                errors.append(
                    f"External integration '{integration_name}' has invalid item in dependency_names list: "
                    f"{dep_name}"
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
                    f"{' '.join(command)}"
                )
            except Exception as e:
                errors.append(
                    f"Integration '{integration_name}': Unexpected error checking dependency '{dep_name}': {e}"
                )

    assert not errors, "\n".join(errors)
