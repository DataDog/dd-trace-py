import csv
import pathlib
import sys
from typing import Dict
from typing import List
from typing import Set
from collections.abc import Iterable

from packaging.version import parse as parse_version


sys.path.append(str(pathlib.Path(__file__).parent.parent.resolve()))
import riotfile  # noqa:E402

# Use try-except for robustness if script is run outside expected env
try:
    from ddtrace._monkey import PATCH_MODULES
    from ddtrace._monkey import _MODULES_FOR_CONTRIB
except ImportError:
    print(
        "Warning: Could not import PATCH_MODULES and _MODULES_FOR_CONTRIB from ddtrace._monkey. Using empty dicts.",
        file=sys.stderr,
    )
    PATCH_MODULES = {}
    _MODULES_FOR_CONTRIB = {}


INTEGRATION_OUT_FILENAME = "min_integration_versions.csv"
OTHER_OUT_FILENAME = "min_compatible_versions.csv"
OUT_DIRECTORIES = (".", "lib-injection/sources")
IGNORED_PACKAGES = {
    "attrs",
    "bcrypt",
    "git+https://github.com/DataDog/dd-trace-api-py",
    "pillow",
    "pytest-asyncio",
    "pytest-randomly",
    "python-json-logger",
    "setuptools",
    # Exclude ddtrace itself if it appears
    "ddtrace",
}

# Define the core dependencies of the ddtrace package itself
# These are the only packages that should end up in min_compatible_versions.csv
CORE_TRACER_DEPENDENCIES = {
    # List core dependencies here, e.g.:
    "wrapt",
    "protobuf",
    "envier",
    "typing_extensions", # Often needed for compatibility
    "importlib_metadata", # Used by core features
    "packaging", # Used by core features
    # Add any other direct, essential dependencies of ddtrace core
}


def _flatten(items):
    """Flatten one level of nesting"""
    for x in items:
        if isinstance(x, Iterable) and not isinstance(x, (str, bytes)):
            yield from _flatten(x)
        else:
            yield x


def _get_integration_package_names(patch_modules: Dict[str, bool], modules_for_contrib: Dict[str, tuple]) -> Set[str]:
    """Get a set of all package names associated with integrations."""
    integration_names = set(patch_modules.keys())
    # Flatten the tuples of module names from _MODULES_FOR_CONTRIB
    remapped_names = set(_flatten(modules_for_contrib.values()))
    all_integration_packages = integration_names.union(remapped_names)
    # Remove any ignored packages that might have slipped in
    return all_integration_packages - IGNORED_PACKAGES


def _create_package_to_integration_map(patch_modules: Dict[str, bool], modules_for_contrib: Dict[str, tuple]) -> Dict[str, str]:
    """Create a mapping from instrumented package names back to the integration name."""
    mapping = {}
    # Direct mapping for integrations where integration name == package name
    for integration_name in patch_modules.keys():
        if integration_name not in modules_for_contrib:
            mapping[integration_name] = integration_name

    # Mapping for integrations where the package name(s) differ
    for integration_name, package_tuples in modules_for_contrib.items():
        for package_name in _flatten(package_tuples):
            mapping[package_name] = integration_name

    # Remove ignored packages from the mapping
    for ignored in IGNORED_PACKAGES:
        mapping.pop(ignored, None)

    return mapping


def _format_version_specifiers(spec: Set[str]) -> Set[str]:
    return set([part for v in [v.split(",") for v in spec if v] for part in v if "!=" not in part])


def tree_pkgs_from_riot() -> Dict[str, Set[str]]:
    return _tree_pkgs_from_riot(riotfile.venv)


def _tree_pkgs_from_riot(node: riotfile.Venv) -> Dict[str, Set]:
    result = {
        pkg: _format_version_specifiers(set(versions))
        for pkg, versions in node.pkgs.items()
        if pkg not in IGNORED_PACKAGES
    }
    for child_venv in node.venvs:
        child_pkgs = _tree_pkgs_from_riot(child_venv)
        for pkg_name, versions in child_pkgs.items():
            if pkg_name in IGNORED_PACKAGES:
                continue
            if pkg_name in result:
                result[pkg_name] = result[pkg_name].union(versions)
            else:
                result[pkg_name] = versions
    return result


def min_version_spec(version_specs: List[str]) -> str:
    min_numeric = ""
    min_spec = ""
    for spec in sorted(version_specs):
        numeric = parse_version(spec.strip("~==<>"))
        if not min_numeric or numeric < min_numeric:
            min_numeric = numeric
            min_spec = spec
    return min_spec


def write_out(
    pkgs_to_write: Dict[str, Set[str]],
    outfile: pathlib.Path,
    title: str,
    include_integration_name: bool = False,
    pkg_to_integration_map: Dict[str, str] | None = None,
) -> None:
    """Write package versions to a specified CSV file."""
    if include_integration_name and pkg_to_integration_map is None:
        raise ValueError("pkg_to_integration_map is required when include_integration_name is True")

    try:
        with open(outfile, "w", newline="") as csvfile:
            csv_writer = csv.writer(csvfile, delimiter=",")
            csv_writer.writerow([f"This file was generated by {pathlib.Path(__file__).name}"])
            csv_writer.writerow([title])
            headers = ["pkg_name", "min_version"]
            if include_integration_name:
                headers.insert(1, "integration_name") # Insert integration_name after pkg_name
            csv_writer.writerow(headers)

            if not pkgs_to_write:
                print(f"No packages to write to {outfile}")
                return

            for pkg, versions in sorted(pkgs_to_write.items()):
                min_version = "0"
                if versions:
                    min_version = str(min_version_spec(list(versions))).strip()  # Pass list to min_version_spec

                row_data = [pkg, min_version]
                if include_integration_name:
                    integration_name = pkg_to_integration_map.get(pkg, "UNKNOWN") # Get integration name
                    if integration_name == "UNKNOWN":
                         print(f"Warning: Could not find integration name for package {pkg}", file=sys.stderr)
                    row_data.insert(1, integration_name) # Insert integration name into row

                # Log output includes integration name if applicable
                log_output = f"{outfile.name}: Pkg: {pkg}"
                if include_integration_name:
                    log_output += f", Integration: {row_data[1]}"
                log_output += f" | Tested: {sorted(list(versions))} | Min: {min_version}"
                print(log_output)

                csv_writer.writerow(row_data)
        print(f"Successfully wrote {len(pkgs_to_write)} package versions to {outfile}")
    except IOError as e:
        print(f"Error writing to {outfile}: {e}", file=sys.stderr)


def main():
    """Discover minimum versions for packages referenced in the riotfile.

    Separates integration packages from core tracer dependencies and writes
    them to different CSV files. Transitive dependencies of integrations are ignored.
    """
    all_pkgs = tree_pkgs_from_riot()

    # Create the reverse mapping for integrations
    package_to_integration_map = _create_package_to_integration_map(PATCH_MODULES, _MODULES_FOR_CONTRIB)
    # Get the set of all integration-related package names using the map keys
    integration_package_names = set(package_to_integration_map.keys())

    integration_pkgs: Dict[str, Set[str]] = {}
    other_pkgs: Dict[str, Set[str]] = {}

    # Separate packages
    for pkg_name, versions in all_pkgs.items():
        if pkg_name in integration_package_names:
            integration_pkgs[pkg_name] = versions
        # Only include core tracer dependencies in the 'other' list
        elif pkg_name in CORE_TRACER_DEPENDENCIES and pkg_name not in IGNORED_PACKAGES:
             other_pkgs[pkg_name] = versions
        elif pkg_name not in IGNORED_PACKAGES:
            # This package is neither an integration nor a core dependency we track
            print(f"Ignoring non-core, non-integration package: {pkg_name}")
        else:
            # This package was explicitly ignored
            print(f"Ignoring package: {pkg_name}")


    # Write the separated package lists to their respective files
    for directory in OUT_DIRECTORIES:
        output_dir = pathlib.Path(directory)
        output_dir.mkdir(parents=True, exist_ok=True) # Ensure directory exists

        # Write integration versions
        write_out(
            integration_pkgs,
            output_dir / INTEGRATION_OUT_FILENAME,
            "Minimum integration package versions",
            include_integration_name=True, # Add integration name column
            pkg_to_integration_map=package_to_integration_map, # Pass the map
        )

        # Write other dependency versions
        write_out(
            other_pkgs,
            output_dir / OTHER_OUT_FILENAME,
            "Minimum core tracer dependency versions", # Updated title
        )

main()
