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
    "python",
    "runs",
    "variants",
}
_VARIANT_FIELDS = _MATRIX_FIELDS - {"variants"} | {"integration", "name"}
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
        suite = _SLUG_PART.sub("-", self.suite.lower()).strip("-")
        return LOCK_ROOT / f"{suite}--py{self.python.replace('.', '')}--{self.hash}.txt"


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


def _runs(command: str, base_environment: dict[str, str], run_specs: object) -> tuple[TestRun, ...]:
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
        run_environment = base_environment.copy()
        run_environment.update({str(key): str(value) for key, value in _mapping(run.get("env"), "run env").items()})
        run_command = str(run.get("command", command))
        if not run_command:
            raise MatrixError("each matrix run needs a command")
        runs.append(TestRun(command=run_command, env=tuple(sorted(run_environment.items()))))
    return tuple(runs)


def _variant_settings(
    suite: str,
    suite_config: Mapping[str, Any],
    defaults: Mapping[str, Any],
    matrix: Mapping[str, Any],
    variant: Mapping[str, Any],
    nightly: bool,
) -> tuple[tuple[str, ...], str, tuple[TestRun, ...]]:
    dependencies = _merge_dependencies(
        *(_string_tuple(spec.get("dependencies"), "dependencies") for spec in (defaults, matrix, variant))
    )
    environment: dict[str, str] = {}
    for spec in (defaults, matrix):
        environment.update({str(key): str(value) for key, value in _mapping(spec.get("env"), "env").items()})
    if nightly:
        environment.update(
            {str(key): str(value) for key, value in _mapping(defaults.get("nightly_env"), "nightly_env").items()}
        )
    environment.update({str(key): str(value) for key, value in _mapping(variant.get("env"), "env").items()})

    command = str(variant.get("command", matrix.get("command", "")))
    run_specs = variant.get("runs", matrix.get("runs"))
    integration = str(variant.get("integration", suite_config.get("integration", suite.split("::", 1)[-1])))
    return dependencies, integration, _runs(command, environment, run_specs)


def _validate_fields(spec: Mapping[str, Any], allowed: set[str], context: str) -> None:
    unknown = set(spec) - allowed
    if unknown:
        raise MatrixError(f"unknown fields in {context}: {', '.join(sorted(unknown))}")


def _python_versions(value: object, defaults: tuple[str, ...], context: str) -> tuple[str, ...]:
    versions = _string_tuple(value, "python")
    if not versions:
        raise MatrixError(f"{context} does not declare any Python versions")
    if len(set(versions)) != len(versions):
        raise MatrixError(f"duplicate Python versions in {context}")
    unsupported = set(versions) - set(defaults) if defaults else set()
    if unsupported:
        raise MatrixError(f"unsupported Python versions in {context}: {', '.join(sorted(unsupported))}")
    return versions


def _expand_suite_matrix(
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
    default_python = _python_versions(defaults.get("python"), (), "matrix defaults")
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
    for variant in variants:
        _validate_fields(variant, _VARIANT_FIELDS, f"variant for {suite}")
        name = str(variant.get("name", ""))
        if not name:
            raise MatrixError(f"every variant for {suite} needs a name")
        python_value = variant.get("python", matrix.get("python", default_python))
        python_versions = _python_versions(python_value, default_python, f"variant {name} for {suite}")
        dependencies, integration, runs = _variant_settings(
            suite,
            suite_config,
            defaults,
            matrix,
            variant,
            nightly,
        )
        for python in python_versions:
            environments.append(
                TestEnvironment(
                    hash=_environment_hash(suite, name, python, dependencies),
                    suite=suite,
                    variant_name=name,
                    integration_name=integration,
                    python=python,
                    direct_dependencies=dependencies,
                    runs=runs,
                )
            )

    return tuple(environments)


@cache
def get_test_environments(*, nightly: bool) -> dict[str, tuple[TestEnvironment, ...]]:
    """Return every concrete test environment declared by suitespec."""
    defaults = SUITESPEC.get("matrix_defaults", {})
    return {
        suite: _expand_suite_matrix(suite, config, defaults, nightly=nightly)
        for suite, config in get_suites().items()
        if config.get("matrix")
    }
