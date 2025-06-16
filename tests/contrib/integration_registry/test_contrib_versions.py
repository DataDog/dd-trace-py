import ast
from pathlib import Path
import re
from typing import Dict
from typing import List
from typing import Optional
from typing import Set
from typing import Tuple
from typing import Union

from packaging.version import Version
import pytest

from ddtrace.contrib.integration_registry.mappings import EXCLUDED_FROM_TESTING
from ddtrace.vendor.packaging.specifiers import Specifier


# allowlist of packages where we can't test the min version
SPECIAL_CASES_ALLOWLIST = {
    # celery 4.x cannot be installed in our test env due to pip version conflict where
    # installing the 4.x version fails because of a celery metadata bug requiring pip<24.1
    "celery": ">=4.4",
    # pytest 6.0 cannot be tested due to our tests targeting later pytest versions.
    "pytest": ">=6.0",
}


def _get_major_minor(version_str: Union[str, Version]) -> Tuple[int, int]:
    """Extract major.minor as integers from a version string."""
    if isinstance(version_str, Version):
        version_str = str(version_str)

    parts = version_str.split(".")
    return (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)


def _get_integration_supported_versions(internal_contrib_dir: Path, integration_name: str) -> Dict[str, str]:
    """Extract _supported_versions from an integration's directory using text parsing. We
    use regex to find the supported versions instead of importing due to not having the patched
    module installed within the test environment. This function searches all .py files in an
    integration's directory.
    """
    integration_dir = internal_contrib_dir / integration_name
    if not integration_dir.is_dir():
        return {}

    for py_file in integration_dir.glob("*.py"):
        try:
            with open(py_file, "r", encoding="utf-8") as f:
                content = f.read()

            # Look for the _supported_versions function and its return statement
            # Pattern matches: def _supported_versions(...): ... return {...}
            pattern = r"def _supported_versions\([^)]*\).*?return\s+(\{[^}]*\})"
            match = re.search(pattern, content, re.DOTALL)

            if match:
                return_dict_str = match.group(1)
                try:
                    # Try to safely evaluate the dictionary literal
                    supported_versions = ast.literal_eval(return_dict_str)
                    return supported_versions
                except (ValueError, SyntaxError):
                    # If it's not a simple literal, try a simpler string-based approach
                    # Look for patterns like {"module": ">=1.0", "other": "*"}
                    simple_pattern = r'"([^"]+)":\s*"([^"]*)"'
                    matches = re.findall(simple_pattern, return_dict_str)
                    if matches:
                        return dict(matches)
        except Exception:
            pass

    return {}


def _parse_version_spec(version_spec: str) -> Optional[Union[str, Version]]:
    """Parse a version specification and return the minimum version."""
    version_spec = version_spec.strip()

    if version_spec in (r"\*", "*"):
        return "*"

    if version_spec.startswith(">=") or version_spec.startswith("=="):
        version_str = version_spec[2:].strip()
        return Version(version_str)
    else:
        return None


def _get_registry_min_version(registry_entry: dict) -> Optional[Version]:
    """Extract the minimum tested version from a registry entry."""
    if not registry_entry.get("is_tested", False):
        return None

    tested_versions = registry_entry.get("tested_versions_by_dependency", {})
    if not tested_versions:
        return None

    min_versions = []
    for _, version_info in tested_versions.items():
        min_versions.append(Version(version_info["min"]))

    return min(min_versions) if min_versions else None


def test_supported_versions_align_with_registry(
    internal_contrib_dir: Path, registry_data: List[dict], integration_dir_names: Set[str]
):
    """Test that minimum tested versions correspond to supported version constraints."""
    errors = []

    registry_by_name = {entry["integration_name"]: entry for entry in registry_data}

    for integration_name in integration_dir_names:
        supported_versions = _get_integration_supported_versions(internal_contrib_dir, integration_name)

        registry_entry = registry_by_name.get(integration_name)
        if (
            not registry_entry
            or not registry_entry.get("is_external_package", False)
            or not registry_entry.get("is_tested", True)
        ):
            continue

        tested_versions = registry_entry.get("tested_versions_by_dependency", {})

        for module_name, version_constraint in supported_versions.items():
            if version_constraint == "*":
                continue

            specifier = Specifier(version_constraint)

            # Check if any dependency in registry has min tested version matching the constraint
            found_matching_dependency = False
            for _, tested_range in tested_versions.items():
                min_tested = tested_range.get("min")
                if not min_tested:
                    continue

                constraint_major_minor = _get_major_minor(specifier.version)
                tested_major_minor = _get_major_minor(min_tested)

                if tested_major_minor == constraint_major_minor:
                    found_matching_dependency = True
                    break

            if not found_matching_dependency:
                constraint_major_minor = _get_major_minor(specifier.version)
                expected_major_minor = f"{constraint_major_minor[0]}.{constraint_major_minor[1]}"

                # we need to allowlist some special cases where we can't test the min version
                if module_name in SPECIAL_CASES_ALLOWLIST:
                    continue

                errors.append(
                    f"Integration '{integration_name}', Module '{module_name}': "
                    f"No dependency found with min tested version matching supported constraint '{version_constraint}' "
                    f"(expected major.minor: {expected_major_minor}). Minimum tested version: {min_tested}"
                )

    assert not errors, "\n".join(errors)


def test_docs_versions_align_with_tested_versions(documented_versions: Dict[str, str], registry_data: List[dict]):
    """Test that minimum documented versions align with minimum tested versions."""
    registry_by_name = {entry["integration_name"]: entry for entry in registry_data}

    misalignments = []

    for integration_name, doc_version_spec in documented_versions.items():
        registry_entry = registry_by_name.get(integration_name)
        if not registry_entry or not registry_entry.get("is_external_package"):
            continue

        doc_version = _parse_version_spec(doc_version_spec)
        registry_min_version = _get_registry_min_version(registry_entry)

        if (
            doc_version == "*"
            or integration_name in SPECIAL_CASES_ALLOWLIST
            or integration_name in EXCLUDED_FROM_TESTING
        ):
            continue

        if doc_version is None or registry_min_version is None:
            misalignments.append(f"{integration_name}: should have doc version and registry min version.")
            continue

        if doc_version > registry_min_version:
            misalignments.append(f"{integration_name}: docs={doc_version} > tested={registry_min_version}")
        # we need to compare the major minor versions only since we disregard the patch version
        elif doc_version < registry_min_version:
            doc_major_minor = _get_major_minor(doc_version)
            tested_major_minor = _get_major_minor(registry_min_version)
            if doc_major_minor != tested_major_minor:
                misalignments.append(f"{integration_name}: docs={doc_version} < tested={registry_min_version}")

    if misalignments:
        pytest.fail("Version misalignments:\n" + "\n".join(f"  {m}" for m in misalignments))


def test_tested_integrations_have_version_info(registry_data: List[dict]):
    """Test that all tested external integrations have version information."""
    missing = [
        entry["integration_name"]
        for entry in registry_data
        if (
            entry.get("is_tested", False)
            and entry.get("is_external_package", False)
            and not entry.get("tested_versions_by_dependency")
        )
    ]

    if missing:
        pytest.fail(f"Missing version info: {', '.join(sorted(missing))}")


def test_documented_integrations_are_tested(documented_versions: Dict[str, str], registry_data: List[dict]):
    """Test that documented external integrations are tested."""
    registry_by_name = {entry["integration_name"]: entry for entry in registry_data}

    untested = [
        name
        for name in documented_versions
        if (
            name in registry_by_name
            and not registry_by_name[name].get("is_tested", False)
            and name not in EXCLUDED_FROM_TESTING
        )
    ]

    if untested:
        pytest.fail(f"Documented but untested: {', '.join(sorted(untested))}")
