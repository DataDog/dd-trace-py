#!/usr/bin/env scripts/uv-run-script
# -*- mode: python -*-
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "packaging>=23.1,<24",
#     "requests>=2.28,<3",
# ]
# ///
"""
Validate that CI tests cover all major versions declared in pyproject.toml.

This script checks that for each dependency in pyproject.toml that is explicitly
tested in CI configuration files (riotfile.py, GitLab CI, GitHub Actions), the test
entries cover all major versions within the declared range.

For example, if pyproject.toml declares `wrapt>=1,<3` and CI has test entries for
wrapt, this script verifies that both major versions 1 and 2 are tested.

IMPORTANT: 'latest' in riotfile.py is NOT counted as coverage for any specific major
version because it's unpredictable - if a new major version is released, 'latest'
would install it even if it's outside pyproject.toml's declared range.

Errors:
- CI only uses 'latest' with no explicit version bounds
- CI has explicit bounds but doesn't cover all required majors
- 'latest' is redundant (latest version's major already covered by explicit bounds)

Warnings:
- Multi-major dependency in pyproject.toml has no CI test coverage
- 'latest' is outside declared bounds (intentional early detection, but should use explicit bounds)

Silencing:
- Add '# ci-deps: allow' at the end of a line in riotfile.py or CI files to silence errors/warnings
- Silenced items are summarized at the end of the output
"""

from dataclasses import dataclass
from dataclasses import field
from functools import lru_cache
from pathlib import Path
import re
import sys
from typing import Dict
from typing import List
from typing import Optional
from typing import Set
from typing import Tuple

from packaging.specifiers import SpecifierSet
from packaging.version import Version
import requests
import tomllib


@dataclass
class Location:
    """Represents a file:line location."""

    file: str
    line: int

    def __str__(self) -> str:
        return f"{self.file}:{self.line}"


@dataclass
class DepInfo:
    """Information about a dependency's test coverage."""

    majors: Set[int] = field(default_factory=set)
    has_latest: bool = False
    locations: List[Location] = field(default_factory=list)
    allowed_locations: List[Location] = field(default_factory=list)  # Lines with # ci-deps: allow


@dataclass
class SilencedItem:
    """A silenced error or warning."""

    level: str  # "error" or "warning"
    package: str
    reason: str
    location: Location


@lru_cache(maxsize=100)
def get_pypi_latest_version(package: str) -> Optional[Version]:
    """Query PyPI for the latest version of a package."""
    try:
        url = f"https://pypi.org/pypi/{package}/json"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return Version(data["info"]["version"])
    except Exception:
        pass
    return None


def load_pyproject() -> Tuple[Dict, str]:
    """Load and parse pyproject.toml from the current directory."""
    pyproject_path = Path("pyproject.toml")
    if not pyproject_path.exists():
        print(f"Error: {pyproject_path} not found")
        sys.exit(1)

    content = pyproject_path.read_text()
    with open(pyproject_path, "rb") as f:
        return tomllib.load(f), content


def load_file(path: Path) -> str:
    """Load file content, returning empty string if file doesn't exist."""
    if not path.exists():
        return ""
    with open(path) as f:
        return f.read()


def find_line_number(content: str, search_text: str, start_pos: int = 0) -> int:
    """Find the line number for a position in content."""
    return content[:start_pos].count("\n") + 1


def get_line_at_position(content: str, pos: int) -> str:
    """Get the full line containing the given position."""
    # Find start of line
    line_start = content.rfind("\n", 0, pos) + 1
    # Find end of line
    line_end = content.find("\n", pos)
    if line_end == -1:
        line_end = len(content)
    return content[line_start:line_end]


def has_allow_comment(content: str, pos: int) -> bool:
    """Check if the line at the given position has '# ci-deps: allow' comment."""
    line = get_line_at_position(content, pos)
    # Allow flexible whitespace: #ci-deps:allow, # ci-deps: allow, #  ci-deps:  allow, etc.
    return bool(re.search(r"#\s*ci-deps:\s*allow", line))


def parse_dependency(dep_line: str) -> Tuple[str, str]:
    """
    Parse a dependency line into package name and version specifier.

    Returns:
        Tuple of (package_name, version_specifier)
    """
    # Split on environment marker
    parts = dep_line.split(";", 1)
    dep_spec = parts[0].strip()

    # Find where the version specifier starts
    package_name = ""
    version_spec = ""

    for i, char in enumerate(dep_spec):
        if char in (">", "<", "=", "~", "!"):
            package_name = dep_spec[:i].strip()
            version_spec = dep_spec[i:].strip()
            break
    else:
        package_name = dep_spec.strip()
        version_spec = ""

    # Remove extras from package name
    if "[" in package_name:
        package_name = package_name.split("[")[0]

    return package_name, version_spec


def get_major_versions_from_specifier(spec_string: str) -> Set[int]:
    """
    Calculate which major versions are allowed by a specifier.

    Returns the set of major versions that satisfy the specifier.
    We check majors 0-10 as a reasonable range for Python packages.
    """
    if not spec_string.strip():
        return set()

    try:
        spec_set = SpecifierSet(spec_string)
    except Exception:
        return set()

    majors = set()
    # Check major versions 0-10 (reasonable range for Python packages)
    for major in range(11):
        # Test if any version in this major satisfies the spec
        # We test x.0.0, x.99.99 to cover the range
        test_versions = [f"{major}.0.0", f"{major}.99.99"]
        for test_ver in test_versions:
            try:
                if Version(test_ver) in spec_set:
                    majors.add(major)
                    break
            except Exception:
                pass

    return majors


def get_major_from_exact_version(version_str: str) -> int:
    """Extract major version from an exact version string like '1.16.0'."""
    try:
        v = Version(version_str)
        return v.major
    except Exception:
        return -1


@dataclass
class PyprojectDep:
    """A dependency from pyproject.toml with its location."""

    majors: Set[int]
    specifier: str
    location: Location


def extract_pyproject_dependencies(data: Dict, content: str) -> Dict[str, PyprojectDep]:
    """
    Extract all dependencies and their required major versions from pyproject.toml.

    Returns:
        Dict mapping package name to PyprojectDep with majors and location
    """
    deps = {}

    # Check project.dependencies
    if "project" in data and "dependencies" in data["project"]:
        for dep_line in data["project"]["dependencies"]:
            pkg_name, version_spec = parse_dependency(dep_line)
            majors = get_major_versions_from_specifier(version_spec)

            # Find line number
            # Search for the dependency line in content
            match = re.search(rf'^\s*"{re.escape(dep_line)}"', content, re.MULTILINE)
            if not match:
                match = re.search(rf"^\s*'{re.escape(dep_line)}'", content, re.MULTILINE)
            line_num = find_line_number(content, "", match.start()) if match else 0

            if pkg_name in deps:
                deps[pkg_name].majors = deps[pkg_name].majors.union(majors)
            else:
                deps[pkg_name] = PyprojectDep(
                    majors=majors, specifier=version_spec, location=Location("pyproject.toml", line_num)
                )

    # Check project.optional-dependencies
    if "project" in data and "optional-dependencies" in data["project"]:
        for group_deps in data["project"]["optional-dependencies"].values():
            for dep_line in group_deps:
                pkg_name, version_spec = parse_dependency(dep_line)
                majors = get_major_versions_from_specifier(version_spec)

                # Find line number
                match = re.search(rf'^\s*"{re.escape(dep_line)}"', content, re.MULTILINE)
                if not match:
                    match = re.search(rf"^\s*'{re.escape(dep_line)}'", content, re.MULTILINE)
                line_num = find_line_number(content, "", match.start()) if match else 0

                if pkg_name in deps:
                    deps[pkg_name].majors = deps[pkg_name].majors.union(majors)
                else:
                    deps[pkg_name] = PyprojectDep(
                        majors=majors, specifier=version_spec, location=Location("pyproject.toml", line_num)
                    )

    return deps


def analyze_version_spec(spec: str) -> Tuple[Set[int], bool]:
    """
    Analyze a version specifier to determine which major versions it tests.

    Args:
        spec: The version specifier string (e.g., "latest", "~=1.0.0", "<2.0.0", "==1.16.0")

    Returns:
        Tuple of (set of major versions, is_latest_only)
        - For 'latest': (empty set, True)
        - For explicit specs: (set of majors, False)
    """
    spec = spec.strip().strip('"').strip("'")

    if not spec or spec == "latest":
        return set(), True

    # Handle exact version (==X.Y.Z)
    if spec.startswith("=="):
        version_str = spec[2:].strip()
        major = get_major_from_exact_version(version_str)
        if major >= 0:
            return {major}, False
        return set(), False

    try:
        spec_set = SpecifierSet(spec)
    except Exception:
        return set(), False

    majors = set()

    for major in range(11):
        test_versions = [f"{major}.0.0", f"{major}.50.0", f"{major}.99.99"]
        for test_ver in test_versions:
            try:
                if Version(test_ver) in spec_set:
                    majors.add(major)
                    break
            except Exception:
                pass

    return majors, False


def extract_riotfile_tested_versions(riotfile_content: str, filename: str = "riotfile.py") -> Dict[str, DepInfo]:
    """
    Extract which major versions are tested for packages in riotfile.py.

    Looks for patterns like:
        "package": [latest, "~=1.0.0", "<2.0.0"]
        "package": latest
        "package": "~=1.0.0"

    Returns:
        Dict mapping package name to DepInfo
    """
    tested: Dict[str, DepInfo] = {}

    # Pattern to match package entries in pkgs={...}
    pkg_pattern = re.compile(
        r'"([^"]+)":\s*'  # Package name in quotes
        r"(\[([^\]]+)\]"  # List of specs
        r'|"([^"]*)"'  # Single quoted spec
        r"|latest"  # latest keyword
        r")",
        re.MULTILINE,
    )

    for match in pkg_pattern.finditer(riotfile_content):
        pkg_name = match.group(1)
        line_num = find_line_number(riotfile_content, "", match.start())
        loc = Location(filename, line_num)

        if pkg_name not in tested:
            tested[pkg_name] = DepInfo()

        tested[pkg_name].locations.append(loc)

        # Check for allow comment
        if has_allow_comment(riotfile_content, match.start()):
            tested[pkg_name].allowed_locations.append(loc)

        full_match = match.group(2)

        if full_match == "latest":
            tested[pkg_name].has_latest = True
        elif match.group(3):  # List of specs
            specs_str = match.group(3)
            for item in re.findall(r'"([^"]+)"|latest', specs_str):
                if item == "":
                    tested[pkg_name].has_latest = True
                else:
                    majors, is_latest = analyze_version_spec(item)
                    if is_latest:
                        tested[pkg_name].has_latest = True
                    else:
                        tested[pkg_name].majors = tested[pkg_name].majors.union(majors)
        elif match.group(4) is not None:  # Single quoted spec
            spec = match.group(4)
            majors, is_latest = analyze_version_spec(spec)
            if is_latest:
                tested[pkg_name].has_latest = True
            else:
                tested[pkg_name].majors = tested[pkg_name].majors.union(majors)

    return tested


def extract_ci_file_tested_versions(content: str, filename: str) -> Dict[str, DepInfo]:
    """
    Extract dependency versions from CI configuration files (GitLab/GitHub YAML).

    Looks for patterns like:
        pip install package==1.2.3
        pip install package>=1.0,<2.0
        pip install -e . package==1.2.3

    Returns:
        Dict mapping package name to DepInfo
    """
    tested: Dict[str, DepInfo] = {}

    # Pattern to match pip install commands with version specs
    pip_pattern = re.compile(
        r"pip\s+install[^\n]*?"  # pip install command
        r"([a-zA-Z0-9_-]+)"  # Package name
        r"(==|>=|<=|~=|!=|>|<)"  # Version operator
        r"([0-9][^\s,\"']*)",  # Version string
        re.MULTILINE,
    )

    for match in pip_pattern.finditer(content):
        pkg_name = match.group(1).lower().replace("_", "-")
        operator = match.group(2)
        version = match.group(3)
        line_num = find_line_number(content, "", match.start())
        loc = Location(filename, line_num)

        if pkg_name not in tested:
            tested[pkg_name] = DepInfo()

        tested[pkg_name].locations.append(loc)

        # Check for allow comment
        if has_allow_comment(content, match.start()):
            tested[pkg_name].allowed_locations.append(loc)

        spec = f"{operator}{version}"
        majors, _ = analyze_version_spec(spec)
        tested[pkg_name].majors = tested[pkg_name].majors.union(majors)

    return tested


def load_all_ci_files() -> List[Tuple[str, str]]:
    """Load all CI configuration files and return list of (filename, content) tuples."""
    files = []

    # GitLab CI files
    gitlab_ci = Path(".gitlab-ci.yml")
    if gitlab_ci.exists():
        files.append((str(gitlab_ci), load_file(gitlab_ci)))

    gitlab_dir = Path(".gitlab")
    if gitlab_dir.exists():
        for yml_file in gitlab_dir.glob("**/*.yml"):
            files.append((str(yml_file), load_file(yml_file)))

    # GitHub Actions files
    github_dir = Path(".github/workflows")
    if github_dir.exists():
        for yml_file in github_dir.glob("*.yml"):
            files.append((str(yml_file), load_file(yml_file)))

    return files


def merge_tested_versions(*sources: Dict[str, DepInfo]) -> Dict[str, DepInfo]:
    """Merge tested versions from multiple sources."""
    merged: Dict[str, DepInfo] = {}
    for source in sources:
        for pkg_name, info in source.items():
            if pkg_name not in merged:
                merged[pkg_name] = DepInfo()
            merged[pkg_name].majors = merged[pkg_name].majors.union(info.majors)
            merged[pkg_name].has_latest = merged[pkg_name].has_latest or info.has_latest
            merged[pkg_name].locations.extend(info.locations)
            merged[pkg_name].allowed_locations.extend(info.allowed_locations)
    return merged


def format_locations(locations: List[Location]) -> str:
    """Format a list of locations for display."""
    # Deduplicate and sort
    unique = sorted(set(str(loc) for loc in locations))
    return ", ".join(unique)


def is_version_in_specifier(version: Version, specifier: str) -> bool:
    """Check if a version satisfies a specifier."""
    try:
        spec_set = SpecifierSet(specifier)
        return version in spec_set
    except Exception:
        return False


def check_coverage(
    pyproject_deps: Dict[str, PyprojectDep],
    ci_tested: Dict[str, DepInfo],
) -> Tuple[List[str], List[str], List[SilencedItem]]:
    """
    Check if tested major versions cover all required major versions.

    Returns:
        Tuple of (errors, warnings, silenced):
        - errors: Dependencies in CI that don't cover all required majors with explicit bounds,
                  or redundant 'latest' (latest version is within declared bounds)
        - warnings: Dependencies using only 'latest' or multi-major deps with no CI coverage,
                    or 'latest' outside declared bounds (intentional early detection)
        - silenced: Items that would be errors/warnings but have '# ci-deps: allow' comment
    """
    errors = []
    warnings = []
    silenced = []

    def add_issue(level: str, pkg: str, reason: str, ci_info: DepInfo):
        """Add an issue to errors/warnings or silenced if allowed."""
        if ci_info.allowed_locations:
            for loc in ci_info.allowed_locations:
                silenced.append(SilencedItem(level=level, package=pkg, reason=reason, location=loc))
        elif level == "error":
            errors.append(reason)
        else:
            warnings.append(reason)

    for pkg_name, pyproject_info in pyproject_deps.items():
        required_majors = pyproject_info.majors
        pyproject_loc = pyproject_info.location
        pyproject_spec = pyproject_info.specifier

        if not required_majors:
            continue

        is_multi_major = len(required_majors) > 1

        if pkg_name in ci_tested:
            ci_info = ci_tested[pkg_name]
            tested_majors = ci_info.majors
            has_latest = ci_info.has_latest
            ci_locs = format_locations(ci_info.locations)

            # Check if only using 'latest' with no explicit bounds
            if has_latest and not tested_majors:
                suggested_bounds = ", ".join(f'">={m},<{m + 1}"' for m in sorted(required_majors))
                reason = (
                    f"{pkg_name}: CI only uses 'latest' with no explicit version bounds. "
                    f"Replace 'latest' with {suggested_bounds} to explicitly cover "
                    f"the declared range {sorted(required_majors)}.\n"
                    f"    pyproject.toml: {pyproject_loc}\n"
                    f"    CI: {ci_locs}"
                )
                add_issue("error", pkg_name, reason, ci_info)
            elif tested_majors:
                # Has explicit bounds - check coverage
                missing = required_majors - tested_majors

                if missing:
                    msg = (
                        f"{pkg_name}: declared range requires majors {sorted(required_majors)}, "
                        f"but CI only tests majors {sorted(tested_majors)}."
                    )
                    if has_latest:
                        # Suggest explicit bounds to replace 'latest'
                        suggested_bounds = ", ".join(f'">={m},<{m + 1}"' for m in sorted(missing))
                        msg += (
                            f" Replace 'latest' with {suggested_bounds} to explicitly cover "
                            f"the declared range and avoid potential untested versions if a new major is released."
                        )
                    else:
                        msg += f" Missing coverage for major(s): {sorted(missing)}"
                    msg += f"\n    pyproject.toml: {pyproject_loc}\n    CI: {ci_locs}"
                    add_issue("error", pkg_name, msg, ci_info)

                # Check if 'latest' is used alongside explicit bounds
                if has_latest and pyproject_spec:
                    latest_version = get_pypi_latest_version(pkg_name)
                    if latest_version:
                        latest_major = latest_version.major
                        # Check if latest is redundant (major already covered by explicit bounds)
                        if latest_major in tested_majors:
                            reason = (
                                f"{pkg_name}: CI uses 'latest' but latest version ({latest_version}) "
                                f"major {latest_major} is already covered by explicit bounds. "
                                f"This is redundant and wastes CI resources. Remove 'latest'.\n"
                                f"    pyproject.toml: {pyproject_loc}\n"
                                f"    CI: {ci_locs}"
                            )
                            add_issue("error", pkg_name, reason, ci_info)
                        elif not is_version_in_specifier(latest_version, pyproject_spec):
                            # Latest is outside declared bounds - intentional early detection
                            reason = (
                                f"{pkg_name}: CI uses 'latest' and latest version ({latest_version}) "
                                f"is outside declared bounds '{pyproject_spec}'. "
                                f"This tests future compatibility but won't be installable with ddtrace.\n"
                                f"    pyproject.toml: {pyproject_loc}\n"
                                f"    CI: {ci_locs}"
                            )
                            add_issue("warning", pkg_name, reason, ci_info)
                        # else: latest provides useful coverage for a major within declared bounds
                        # Not ideal (fragile), but not redundant. Missing explicit coverage already reported above.
        elif is_multi_major:
            # Multi-major dependency with no CI coverage - warn (no allow comment possible since not in CI)
            warnings.append(
                f"{pkg_name}: declares multi-major range {sorted(required_majors)} "
                f"but has no explicit CI test coverage\n"
                f"    pyproject.toml: {pyproject_loc}"
            )

    return errors, warnings, silenced


def main() -> int:
    """Main entry point."""
    # Load pyproject.toml
    pyproject_data, pyproject_content = load_pyproject()
    pyproject_deps = extract_pyproject_dependencies(pyproject_data, pyproject_content)

    # Load riotfile.py
    riotfile_content = load_file(Path("riotfile.py"))
    riotfile_tested = extract_riotfile_tested_versions(riotfile_content, "riotfile.py")

    # Load GitLab and GitHub CI files
    ci_files = load_all_ci_files()
    ci_tested_list = [extract_ci_file_tested_versions(content, filename) for filename, content in ci_files]

    # Merge all CI sources
    all_tested = merge_tested_versions(riotfile_tested, *ci_tested_list)

    # Check coverage
    errors, warnings, silenced = check_coverage(pyproject_deps, all_tested)

    exit_code = 0

    if errors:
        print("❌ CI coverage validation failed:\n")
        for error in errors:
            print(f"  {error}\n")
        print("For each dependency in pyproject.toml that is explicitly tested in CI,")
        print("ensure that all declared major versions are covered with explicit version bounds.")
        print("For example, if pyproject.toml declares 'wrapt>=1,<3', CI should test")
        print("both major version 1 and 2 (e.g., pkgs={'wrapt': ['>=1,<2', '>=2,<3']})")
        exit_code = 1

    if warnings:
        if errors:
            print()
        print("⚠️  Warnings:\n")
        for warning in warnings:
            print(f"  {warning}\n")
        print("Consider adding explicit version bounds to ensure CI tests compatible versions.")

    if not errors and not warnings:
        print("✅ All dependencies have appropriate CI coverage for declared version ranges")

    if silenced:
        print()
        print("ℹ️  Silenced items (via '# ci-deps: allow'):\n")
        for item in silenced:
            level_icon = "❌" if item.level == "error" else "⚠️"
            print(f"  {level_icon} [{item.level.upper()}] {item.package} (silenced at {item.location})")
            # Print a condensed version of the reason (first line only)
            reason_first_line = item.reason.split("\n")[0]
            print(f"      {reason_first_line}\n")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
