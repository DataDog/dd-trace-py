import os
import re

import tomllib


PROJECT_FILENAME = "pyproject.toml"
PRERELEASE_MARKER = "rc"
PRERELEASE_RE = r"[0-9]+(rc|dev)[0-9]+"


def _add_prerelease_marker(dependency_specifier: str) -> str:
    """
    The `dependency_specifier` parameter is expected to be PEP 508-compliant.
    This function does not handle all possible PEP 508 strings, only the ones that are
    used at the time of writing in `pyproject.toml`, and maybe some others.

    >>> _add_prerelease_marker("bytecode>=0.17.0,<1; python_version>='3.14.0'")
    "bytecode>=0.17.0,<1; python_version>='3.14.0'"
    >>> _add_prerelease_marker('envier~=0.6.1')
    "envier~=0.6.1rc99"
    >>> _add_prerelease_marker('opentelemetry-api>=1,<2')
    "opentelemetry-api>=1,<2rc99"
    >>> _add_prerelease_marker('wrapt>=1,<3')
    "wrapt>=1,<3rc99"
    """
    if not re.search(PRERELEASE_RE, dependency_specifier):
        return dependency_specifier
    dependency_parts: list[str] = dependency_specifier.split(";")
    version_bounds: list[str] = dependency_parts[0].split(",")
    if ">" in version_bounds[-1]:
        version_bounds[-1] += f"{PRERELEASE_MARKER}0"
    else:
        version_bounds[-1] += f"{PRERELEASE_MARKER}99"
    dependency_parts[0] = ",".join(version_bounds)
    return ";".join(dependency_parts)


def _replace_dependency_strings(input_: list[str], new_specifiers: list[str]) -> list[str]:
    """
    Return a copy of the input list with the strings representing project dependencies replaced with the
    item from `new_specifiers` at the matching index.

    `input_` is assumed to be a list of lines from a pyproject.toml file
    `new_specifiers` is a list of PEP 508 dependency specifiers

    This index-maintenance approach is chosen to avoid dependence on a third-party toml-writing library.
    """
    input_copy: list[str] = []
    replace_at_idx = -1
    for line in input_:
        if len(new_specifiers) > replace_at_idx >= 0:
            input_copy.append(f'    "{new_specifiers[replace_at_idx]}",\n')
            replace_at_idx += 1
        else:
            input_copy.append(line)
        if line.startswith("dependencies = ["):
            replace_at_idx += 1
    return input_copy


def update_dependencies_to_allow_prereleases():
    """
    Updates the pyproject.toml file in-place, adding pre-release markers like "rc0"
    to the libraries listed in the `dependencies` block. Combined with the `PIP_PRE`
    environment variable configuration, this tells pip, and thus riot, to include
    pre-release versions of dependencies in its package search.
    """
    updated_specifiers: list[str] = []

    with open(PROJECT_FILENAME, "r") as f:
        project_file_lines: list[str] = f.readlines()

    with open(PROJECT_FILENAME, "rb") as f:
        project_file_data: dict = tomllib.load(f)

    for dependency_specifier in project_file_data["project"]["dependencies"]:
        updated_specifiers.append(_add_prerelease_marker(dependency_specifier))

    updated_project_file_lines = _replace_dependency_strings(project_file_lines, updated_specifiers)

    os.remove(PROJECT_FILENAME)
    with open(PROJECT_FILENAME, "w") as f:
        f.writelines(updated_project_file_lines)


if __name__ == "__main__":
    update_dependencies_to_allow_prereleases()
