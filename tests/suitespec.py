from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from ruamel.yaml import YAML  # noqa


TESTS = Path(__file__).parents[1] / "tests"
BENCHMARKS = Path(__file__).parents[1] / "benchmarks"
SEARCH_ROOTS = ((TESTS, ""), (BENCHMARKS, "benchmarks"))
LOCK_ROOT = Path(".uv")
LOCK_PLATFORM = "linux"

_REQUIREMENT_NAME = re.compile(r"^([A-Za-z0-9_.-]+)(\[[A-Za-z0-9_., -]+\])?")
_SLUG_PART = re.compile(r"[^a-z0-9]+")
_MATRIX_FIELDS = {
    "command",
    "dependencies",
    "env",
    "nightly_env",
    "python",
    "runs",
    "variants",
}
_VARIANT_FIELDS = _MATRIX_FIELDS - {"nightly_env", "variants"} | {"integration", "name"}
_LEGACY_MATRIX_FIELDS = {"axes", "cases", "compatibility", "exclude", "include"}
_DEFAULT_FIELDS = {"dependencies", "env", "nightly_env", "python"}
_RUN_FIELDS = {"command", "env"}


def _collect_suitespecs() -> dict:
    suitespec = {"components": {}, "suites": {}, "matrix_defaults": {}}

    specfiles = []
    for root, ns_prefix in SEARCH_ROOTS:
        for f in root.rglob("suitespec.yml"):
            specfiles.append((f, root, ns_prefix))

    for s, root, ns_prefix in specfiles:
        path_parts = s.relative_to(root).parts[:-1]
        namespace = "::".join(path_parts) if path_parts else ns_prefix or None
        with YAML() as yaml:
            data = yaml.load(s)
            suites = data.get("suites", {})
            if namespace is not None:
                for name, spec in list(suites.items()):
                    if "pattern" not in spec:
                        spec["pattern"] = name
                    suites[f"{namespace}::{name}"] = spec
                    del suites[name]
            for k, v in suitespec.items():
                v.update(data.get(k, {}))

    return suitespec


SUITESPEC = _collect_suitespecs()


@cache
def get_patterns(suite: str) -> set[str]:
    """Get the patterns for a suite

    >>> SUITESPEC["components"] = {"$h": ["tests/s.py"], "core": ["core/*"], "debugging": ["ddtrace/d/*"]}
    >>> SUITESPEC["suites"] = {"debugger": {"paths": ["@core", "@debugging", "tests/d/*"]}}
    >>> sorted(get_patterns("debugger"))  # doctest: +NORMALIZE_WHITESPACE
    ['core/*', 'ddtrace/d/*', 'tests/d/*', 'tests/s.py']
    >>> get_patterns("foobar")
    set()
    """
    compos = SUITESPEC["components"]
    if suite not in SUITESPEC["suites"]:
        return set()

    suite_patterns = set(SUITESPEC["suites"][suite]["paths"])

    # Include patterns from include-always components
    for patterns in (patterns for compo, patterns in compos.items() if compo.startswith("$")):
        suite_patterns |= set(patterns)

    def resolve(patterns: set) -> set:
        refs = {_ for _ in patterns if _.startswith("@")}
        resolved_patterns = patterns - refs

        # Recursively resolve references
        for ref in refs:
            try:
                resolved_patterns |= resolve(set(compos[ref[1:]]))
            except KeyError:
                raise ValueError(f"Unknown component reference: {ref}")

        return resolved_patterns

    return {_.format(suite=suite.replace("::", ".")) for _ in resolve(suite_patterns)}


def get_suites() -> dict[str, dict]:
    """Get the list of suites."""
    return SUITESPEC["suites"]


def get_components() -> dict[str, list[str]]:
    """Get the list of jobs."""
    return SUITESPEC.get("components", {})


def get_matrix_defaults() -> dict:
    """Get defaults inherited by declarative test matrices."""
    return SUITESPEC.get("matrix_defaults", {})


def _slug(value: str) -> str:
    return _SLUG_PART.sub("-", value.lower()).strip("-")


def lockfile_path(suite: str, python: str, environment_hash: str) -> Path:
    """Return the repository-relative lock path for one concrete environment."""
    return LOCK_ROOT / f"{_slug(suite)}--py{python.replace('.', '')}--{environment_hash}.txt"


def _environment_hash(suite: str, variant_name: str, python: str, dependencies: tuple[str, ...]) -> str:
    identity = {
        "suite": suite,
        "variant": variant_name,
        "python": python,
        "dependencies": sorted(dependencies, key=str.casefold),
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:12]


@dataclass(frozen=True)
class TestRun:
    """One command and environment executed in a test environment."""

    command: str
    env: tuple[tuple[str, str], ...] = ()

    @property
    def environment(self) -> dict[str, str]:
        return dict(self.env)


@dataclass(frozen=True)
class TestEnvironment:
    """A concrete test dependency environment."""

    hash: str
    suite: str
    variant_name: str
    integration_name: str
    python: str
    direct_dependencies: tuple[str, ...]
    runs: tuple[TestRun, ...]

    @property
    def lockfile(self) -> Path:
        return lockfile_path(self.suite, self.python, self.hash)


class MatrixError(ValueError):
    """Raised when a test matrix declaration is invalid."""


def _string_tuple(value: object, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes))
        and all(isinstance(item, str) for item in value)
    ):
        return tuple(value)
    raise MatrixError(f"{field} must be a list")


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return value
    raise MatrixError(f"{field} must be a mapping")


def _requirement_key(requirement: str) -> str:
    match = _REQUIREMENT_NAME.match(requirement)
    if match is None:
        raise MatrixError(f"invalid dependency requirement: {requirement}")
    name, extras = match.groups()
    return f"{name}{extras or ''}".lower().replace("_", "-")


def _merge_dependencies(*groups: tuple[str, ...]) -> tuple[str, ...]:
    merged: list[str] = []
    for group in groups:
        replaced = {_requirement_key(requirement) for requirement in group}
        merged = [requirement for requirement in merged if _requirement_key(requirement) not in replaced]
        merged.extend(group)
    return tuple(merged)


def _merge_specs(*specs: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    dependencies: tuple[str, ...] = ()
    environment: dict[str, str] = {}
    for spec in specs:
        dependencies = _merge_dependencies(
            dependencies,
            _string_tuple(spec.get("dependencies"), "dependencies"),
        )
        environment.update({str(key): str(value) for key, value in _mapping(spec.get("env"), "env").items()})
        for field in ("command", "integration", "runs"):
            if field in spec:
                merged[field] = spec[field]
    merged["dependencies"] = dependencies
    merged["env"] = environment
    return merged


def _runs(spec: Mapping[str, Any]) -> tuple[TestRun, ...]:
    base_environment = {str(key): str(value) for key, value in _mapping(spec.get("env"), "env").items()}
    command = str(spec.get("command", ""))
    run_specs = spec.get("runs")
    if run_specs is None:
        if not command:
            raise MatrixError("each matrix environment needs a command")
        return (TestRun(command=command, env=tuple(sorted(base_environment.items()))),)
    if isinstance(run_specs, (str, bytes)) or not isinstance(run_specs, Sequence):
        raise MatrixError("runs must be a list")
    if not run_specs:
        raise MatrixError("runs must not be empty")

    runs = []
    for run_spec in run_specs:
        run = _mapping(run_spec, "run")
        _validate_fields(run, _RUN_FIELDS, "run")
        run_environment = dict(base_environment)
        run_environment.update({str(key): str(value) for key, value in _mapping(run.get("env"), "run env").items()})
        run_command = str(run.get("command", command))
        if not run_command:
            raise MatrixError("each matrix run needs a command")
        runs.append(TestRun(command=run_command, env=tuple(sorted(run_environment.items()))))
    return tuple(runs)


def _suite_identity(suite: str) -> str:
    return suite.split("::", 1)[-1]


def _build_environment(
    suite: str,
    suite_config: Mapping[str, Any],
    base_spec: Mapping[str, Any],
    variant: Mapping[str, Any],
    python: str,
) -> TestEnvironment:
    spec = _merge_specs(base_spec, variant)
    variant_name = str(variant.get("name", "default"))
    integration_name = str(spec.get("integration", suite_config.get("integration", _suite_identity(suite))))
    dependencies = spec["dependencies"]
    return TestEnvironment(
        hash=_environment_hash(suite, variant_name, python, dependencies),
        suite=suite,
        variant_name=variant_name,
        integration_name=integration_name,
        python=python,
        direct_dependencies=dependencies,
        runs=_runs(spec),
    )


def _validate_fields(spec: Mapping[str, Any], allowed: set[str], context: str) -> None:
    unknown = set(spec) - allowed
    if not unknown:
        return
    legacy = unknown & _LEGACY_MATRIX_FIELDS
    if legacy:
        raise MatrixError(f"legacy matrix fields are not supported in {context}: {', '.join(sorted(legacy))}")
    raise MatrixError(f"unknown fields in {context}: {', '.join(sorted(unknown))}")


def _python_versions(value: object, defaults: tuple[str, ...], context: str, *, explicit: bool) -> tuple[str, ...]:
    versions = _string_tuple(value, "python")
    if not versions:
        raise MatrixError(f"{context} does not declare any Python versions")
    if len(set(versions)) != len(versions):
        raise MatrixError(f"duplicate Python versions in {context}")
    unsupported = set(versions) - set(defaults) if defaults else set()
    if unsupported:
        raise MatrixError(f"unsupported Python versions in {context}: {', '.join(sorted(unsupported))}")
    if defaults and explicit and len(versions) == len(defaults) and set(versions) == set(defaults):
        raise MatrixError(f"{context} repeats the complete default Python range")
    return versions


def expand_suite_matrix(
    suite: str,
    suite_config: Mapping[str, Any],
    defaults: Mapping[str, Any],
    *,
    nightly: bool,
) -> tuple[TestEnvironment, ...]:
    """Expand one compact suite matrix into concrete test environments."""
    matrix = _mapping(suite_config.get("matrix"), f"matrix for {suite}")
    if not matrix:
        return ()
    _validate_fields(matrix, _MATRIX_FIELDS, f"matrix for {suite}")
    _validate_fields(defaults, _DEFAULT_FIELDS, "matrix defaults")
    default_python = _python_versions(defaults.get("python"), (), "matrix defaults", explicit=False)
    nightly_spec: Mapping[str, Any] = {}
    if nightly:
        nightly_spec = {
            "env": {
                **_mapping(defaults.get("nightly_env"), "nightly_env"),
                **_mapping(matrix.get("nightly_env"), "matrix nightly_env"),
            }
        }
    base_spec = _merge_specs(defaults, matrix, nightly_spec)
    raw_variants = matrix.get("variants")
    if raw_variants is None:
        variants: tuple[Mapping[str, Any], ...] = ({"name": "default"},)
    else:
        if isinstance(raw_variants, (str, bytes)) or not isinstance(raw_variants, Sequence):
            raise MatrixError(f"variants for {suite} must be a list")
        if not raw_variants:
            raise MatrixError(f"variants for {suite} must not be empty")
        if "python" in matrix:
            raise MatrixError(f"matrix-level python cannot be combined with variants for {suite}")
        variants = tuple(_mapping(variant, f"variant for {suite}") for variant in raw_variants)

    environments = []
    names = set()
    environment_hashes = set()
    for variant in variants:
        _validate_fields(variant, _VARIANT_FIELDS, f"variant for {suite}")
        name = str(variant.get("name", ""))
        if not name:
            raise MatrixError(f"every variant for {suite} needs a name")
        if name in names:
            raise MatrixError(f"duplicate variant name for {suite}: {name}")
        names.add(name)
        explicit_python = "python" in variant or (raw_variants is None and "python" in matrix)
        python_value = variant.get("python", matrix.get("python", default_python))
        python_versions = _python_versions(
            python_value,
            default_python,
            f"variant {name} for {suite}",
            explicit=explicit_python,
        )
        for python in python_versions:
            environment = _build_environment(suite, suite_config, base_spec, variant, python)
            if environment.hash in environment_hashes:
                raise MatrixError(f"environment hash collision: {environment.hash}")
            environment_hashes.add(environment.hash)
            environments.append(environment)

    return tuple(environments)


def expand_declared_matrices(
    suites: Mapping[str, Mapping[str, Any]],
    defaults: Mapping[str, Any],
    *,
    nightly: bool,
) -> dict[str, tuple[TestEnvironment, ...]]:
    """Expand every suite that declares a test matrix."""
    return {
        suite: expand_suite_matrix(suite, config, defaults, nightly=nightly)
        for suite, config in suites.items()
        if config.get("matrix")
    }


def get_test_environments(*, nightly: bool) -> dict[str, tuple[TestEnvironment, ...]]:
    """Return every concrete test environment declared by suitespec."""
    return expand_declared_matrices(get_suites(), get_matrix_defaults(), nightly=nightly)
