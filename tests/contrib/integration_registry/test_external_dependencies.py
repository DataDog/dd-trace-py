import subprocess
import sys
from typing import Any
from typing import Dict
from typing import List

from packaging.version import InvalidVersion
from packaging.version import parse as parse_version
import pytest


def _validate_version_str(version_str: str, field_name: str, integration_name: str) -> List[str]:
    """Checks if a version string is 'N/A' or parsable SemVer."""
    errors = []
    if version_str != "N/A":
        try:
            parse_version(version_str)
        except InvalidVersion:
            errors.append(
                f"Integration '{integration_name}': Invalid SemVer format for '{field_name}': '{version_str}'."
            )
    return errors


def _validate_external_tested_entry(entry: Dict[str, Any], name: str) -> List[str]:
    """Runs all validation checks for an external, tested integration entry."""
    errors = []
    required_fields = ["dependency_name", "tested_version_min", "tested_version_max"]
    version_fields_to_check = ["tested_version_min", "tested_version_max"]

    for field in required_fields:
        if field not in entry:
            errors.append(f"External tested integration '{name}' is missing required field '{field}'.")

    if "dependency_name" in entry:
        dep_name_val = entry["dependency_name"]
        if not isinstance(dep_name_val, list) or not dep_name_val:
            errors.append(
                f"External integration '{name}' has an empty or invalid 'dependency_name' field (must be a non-empty "
                "list)."
            )

    for field in version_fields_to_check:
        if field in entry:
            errors.extend(_validate_version_str(entry[field], field, name))

    return errors


def _validate_non_external_entry(entry: Dict[str, Any], name: str) -> List[str]:
    """Checks that external-specific fields are absent for non-external entries."""
    errors = []
    unexpected_fields = ["dependency_name", "tested_version_min", "tested_version_max"]
    for field in unexpected_fields:
        if field in entry:
            errors.append(f"Non-external integration '{name}' unexpectedly contains field '{field}'.")
    return errors


def test_external_package_requirements(registry_data: list[dict]):
    """
    Verify registry entries for external packages have required fields and valid formats,
    and non-external packages do not have external-specific fields.
    """
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


# def test_external_dependencies_exist_on_pypi(registry_data: list[dict]):
#     """
#     Verify that package names listed in 'dependency_name' for external integrations
#     can be found on PyPI using 'pip index versions'.
#     """
#     errors = []
#     pip_command = [sys.executable, "-m", "pip"]

#     print("\nChecking external dependencies against PyPI...")
#     checked_packages = set()

#     for entry in registry_data:
#         if entry.get("is_external_package") is True:
#             integration_name = entry.get("integration_name", "UNKNOWN_ENTRY")
#             dependency_names = entry.get("dependency_name", [])

#             if not dependency_names:
#                 continue

#             if not isinstance(dependency_names, list):
#                 errors.append(
#                     f"External integration '{integration_name}' has invalid dependency_name (not a list): "
#                     + f"{dependency_names}"
#                 )
#                 continue

#             for dep_name in dependency_names:
#                 if not isinstance(dep_name, str) or not dep_name:
#                     errors.append(
#                         f"External integration '{integration_name}' has invalid item in dependency_name list: "
#                         + f"{dep_name}"
#                     )
#                     continue

#                 if dep_name in checked_packages:
#                     continue
#                 checked_packages.add(dep_name)

#                 command = pip_command + ["index", "versions", dep_name]
#                 try:
#                     result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=30)

#                     if result.returncode != 0:
#                         error_detail = f"Return Code: {result.returncode}"
#                         if "no matching distribution found" in result.stderr.lower():
#                             error_detail = "No matching distribution found on PyPI (or configured index)."
#                         else:
#                             error_detail += f"\n  Stderr: {result.stderr.strip()}"
#                         errors.append(
#                             f"Integration '{integration_name}': Dependency '{dep_name}' check failed. {error_detail}"
#                         )

#                 except subprocess.TimeoutExpired:
#                     errors.append(
#                         f"Integration '{integration_name}': Timeout checking dependency '{dep_name}' on PyPI."
#                     )
#                 except FileNotFoundError:
#                     pytest.fail(
#                         "Could not execute pip command. Ensure Python environment is correctly set up. Command: "
#                         + f"{' '.join(command)}"
#                     )
#                 except Exception as e:
#                     errors.append(
#                         f"Integration '{integration_name}': Unexpected error checking dependency '{dep_name}': {e}"
#                     )

#     assert not errors, "\n".join(errors)
