from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
import re

from packaging.specifiers import InvalidSpecifier
from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion
from packaging.version import Version


@dataclass(frozen=True)
class RiotSelectionFilters:
    package_name: str | None = None
    package_version: str | None = None
    python_version: str | None = None

    @property
    def is_active(self) -> bool:
        return any((self.package_name, self.package_version, self.python_version))


@dataclass(frozen=True)
class RiotInstanceMetadata:
    hash: str
    python_version: str | None
    packages: Mapping[str, object]


@dataclass(frozen=True)
class RiotInstanceSelection:
    hashes: tuple[str, ...]
    python_versions: tuple[str, ...]
    package_name: str | None = None


def resolve_suite_name(requested_suite: str, suites: Mapping[str, object]) -> str:
    if requested_suite in suites:
        return requested_suite

    requested_suffix = requested_suite.split("::")[-1]
    candidates = sorted(
        suite_name
        for suite_name in suites
        if suite_name.split("::")[-1] == requested_suffix or suite_name.endswith(f"::{requested_suffix}")
    )
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ValueError(f"Unknown suite {requested_suite!r}")
    raise ValueError(f"Ambiguous suite selector {requested_suite!r}: {candidates}")


def normalize_python_version(version: str) -> str:
    match = re.match(r"^(?P<major>\d+)\.(?P<minor>\d+)(?:\.\d+)?$", version.strip())
    if not match:
        raise ValueError(f"Unsupported Python version selector {version!r}; expected X.Y or X.Y.Z")
    return f"{int(match.group('major'))}.{int(match.group('minor'))}"


def select_instances_for_suite(
    suite_name: str,
    instances: Sequence[RiotInstanceMetadata],
    *,
    filters: RiotSelectionFilters | None = None,
    integration_to_dependencies: Mapping[str, set[str]] | None = None,
) -> RiotInstanceSelection:
    if not instances:
        raise ValueError(f"No riot environments found for suite {suite_name!r}")

    filters = filters or RiotSelectionFilters()
    selected = list(instances)

    if filters.python_version:
        python_version = normalize_python_version(filters.python_version)
        selected = [instance for instance in selected if instance.python_version == python_version]
        if not selected:
            raise ValueError(f"No riot environments found for suite {suite_name!r} on Python {python_version}")

    package_name = None
    if filters.package_version:
        candidate_packages = _resolve_candidate_packages(
            suite_name,
            selected,
            explicit_package=filters.package_name,
            integration_to_dependencies=integration_to_dependencies,
        )
        if not candidate_packages:
            raise ValueError(
                f"Unable to determine which dependency to version-filter for suite {suite_name!r}; "
                "set TEST_PACKAGE to disambiguate"
            )

        matches_by_package: dict[str, list[RiotInstanceMetadata]] = {}
        for candidate_package in candidate_packages:
            matches = [
                instance
                for instance in selected
                if _instance_matches_requested_version(instance, candidate_package, filters.package_version)
            ]
            if matches:
                matches_by_package[candidate_package] = matches

        if not matches_by_package:
            requested_python = (
                f" on Python {normalize_python_version(filters.python_version)}" if filters.python_version else ""
            )
            raise ValueError(
                f"No riot environments found for suite {suite_name!r}{requested_python} "
                f"matching {filters.package_version!r}"
            )

        if filters.package_name:
            package_name = _normalize_package_name(filters.package_name)
            selected = matches_by_package[package_name]
        elif len(matches_by_package) == 1:
            package_name, selected = next(iter(matches_by_package.items()))
        else:
            hash_sets = {
                tuple(sorted(instance.hash for instance in matches)) for matches in matches_by_package.values()
            }
            if len(hash_sets) != 1:
                raise ValueError(
                    f"Multiple dependencies in suite {suite_name!r} match version {filters.package_version!r}: "
                    f"{sorted(matches_by_package)}. Set TEST_PACKAGE to disambiguate"
                )
            package_name, selected = next(iter(sorted(matches_by_package.items())))

    hashes = tuple(sorted({instance.hash for instance in selected}))
    python_versions = tuple(sorted({instance.python_version for instance in selected if instance.python_version}))
    return RiotInstanceSelection(hashes=hashes, python_versions=python_versions, package_name=package_name)


def _resolve_candidate_packages(
    suite_name: str,
    instances: Sequence[RiotInstanceMetadata],
    *,
    explicit_package: str | None,
    integration_to_dependencies: Mapping[str, set[str]] | None,
) -> list[str]:
    available_packages = sorted(
        {_normalize_package_name(package) for instance in instances for package in instance.packages}
    )
    if explicit_package:
        package_name = _normalize_package_name(explicit_package)
        if package_name not in available_packages:
            raise ValueError(f"Suite {suite_name!r} does not define dependency {explicit_package!r}")
        return [package_name]

    if integration_to_dependencies is None:
        integration_to_dependencies = _load_integration_to_dependency_map()

    suite_leaf = suite_name.split("::")[-1].lower()
    lookup_names = (suite_leaf, suite_leaf.replace(":", "_"), suite_leaf.split(":")[0])
    registry_candidates = {
        _normalize_package_name(package)
        for lookup_name in lookup_names
        for package in integration_to_dependencies.get(lookup_name, set())
    }
    matched_registry_candidates = sorted(registry_candidates.intersection(available_packages))
    if matched_registry_candidates:
        return matched_registry_candidates

    normalized_suite_leaf = _normalize_identifier(suite_leaf)
    similar_packages = sorted(
        package
        for package in available_packages
        if _normalize_identifier(package) == normalized_suite_leaf
        or normalized_suite_leaf in _normalize_identifier(package)
    )
    if len(similar_packages) == 1:
        return similar_packages

    varying_packages = sorted(_varying_packages(instances))
    if len(varying_packages) == 1:
        return varying_packages

    return sorted(set(similar_packages).union(varying_packages))


def _varying_packages(instances: Sequence[RiotInstanceMetadata]) -> set[str]:
    values_by_package: defaultdict[str, set[str]] = defaultdict(set)
    for instance in instances:
        for package_name, package_spec in instance.packages.items():
            values_by_package[_normalize_package_name(package_name)].add(str(package_spec))
    return {package_name for package_name, values in values_by_package.items() if len(values) > 1}


def _instance_matches_requested_version(
    instance: RiotInstanceMetadata, package_name: str, requested_version: str
) -> bool:
    package_spec = _get_package_spec(instance, package_name)
    if isinstance(package_spec, Sequence) and not isinstance(package_spec, (str, bytes)):
        return any(_matches_requested_version(str(item), requested_version) for item in package_spec)
    if package_spec is None:
        return False
    return _matches_requested_version(str(package_spec), requested_version)


def _get_package_spec(instance: RiotInstanceMetadata, package_name: str) -> object | None:
    normalized_package_name = _normalize_package_name(package_name)
    for candidate_name, package_spec in instance.packages.items():
        if _normalize_package_name(candidate_name) == normalized_package_name:
            return package_spec
    return None


def _matches_requested_version(package_spec: str, requested_version: str) -> bool:
    normalized_spec = package_spec.strip()
    normalized_requested = requested_version.strip()

    # AIDEV-NOTE: Exact version targeting intentionally does not match Riot's blank
    # "latest" rows because the concrete latest release can drift after the child
    # pipeline is generated. Callers must request TEST_VERSION=latest explicitly.
    if normalized_requested.lower() == "latest":
        return normalized_spec in ("", "latest")
    if normalized_spec in ("", "latest"):
        return False

    if _looks_like_specifier(normalized_requested):
        return _normalize_specifier_text(normalized_spec) == _normalize_specifier_text(normalized_requested)

    try:
        requested = Version(normalized_requested)
    except InvalidVersion as exc:
        raise ValueError(f"Unsupported dependency version selector {requested_version!r}") from exc

    try:
        specifier_set = SpecifierSet(
            normalized_spec if _looks_like_specifier(normalized_spec) else f"=={normalized_spec}"
        )
    except InvalidSpecifier:
        return normalized_spec == normalized_requested
    return requested in specifier_set


def _looks_like_specifier(value: str) -> bool:
    return any(operator in value for operator in ("<", ">", "=", "!", "~"))


def _normalize_specifier_text(value: str) -> str:
    return value.replace(" ", "")


def _normalize_identifier(value: str) -> str:
    return re.sub(r"[-:.]+", "_", value.lower())


def _normalize_package_name(value: str) -> str:
    return value.strip().lower()


@cache
def _load_integration_to_dependency_map() -> dict[str, set[str]]:
    dependency_map: dict[str, set[str]] = {}
    current_integration: str | None = None
    in_dependency_names = False

    registry_path = Path(__file__).resolve().parent / "integration_registry" / "registry.yaml"
    for raw_line in registry_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        if not line or line.startswith("integrations:"):
            continue

        if line.startswith("- integration_name: "):
            current_integration = _parse_yaml_scalar(line.split(": ", 1)[1]).lower()
            dependency_map[current_integration] = set()
            in_dependency_names = False
            continue

        if current_integration is None:
            continue

        if line == "  dependency_names:":
            in_dependency_names = True
            continue

        if in_dependency_names and line.startswith("  - "):
            dependency_map[current_integration].add(_parse_yaml_scalar(line[4:]).lower())
            continue

        if in_dependency_names and not line.startswith("  - "):
            in_dependency_names = False

    return dependency_map


def _parse_yaml_scalar(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {"'", '"'}:
        return stripped[1:-1]
    return stripped
