from __future__ import annotations

from dataclasses import dataclass
from functools import cache
import hashlib
from pathlib import Path
import re
from typing import Any

from ruamel.yaml import YAML  # noqa


TESTS = Path(__file__).parents[1] / "tests"
BENCHMARKS = Path(__file__).parents[1] / "benchmarks"
SEARCH_ROOTS = ((TESTS, ""), (BENCHMARKS, "benchmarks"))
LOCK_ROOT = Path(".riot/requirements")

_REQUIREMENT_NAME = re.compile(r"^([A-Za-z0-9_.-]+)(\[[A-Za-z0-9_., -]+\])?")

DEFAULT_DEPENDENCIES = (
    "mock",
    "pytest",
    "pytest-mock",
    "coverage",
    "pytest-cov",
    "opentracing",
    "hypothesis<6.45.1",
)
DEFAULT_PYTHON_VERSIONS = ("3.9", "3.10", "3.11", "3.12", "3.13", "3.14")
DEFAULT_ENVIRONMENT = {
    "_DD_CIVISIBILITY_USE_CI_CONTEXT_PROVIDER": "1",
    "DD_TESTING_RAISE": "1",
    "DD_REMOTE_CONFIGURATION_ENABLED": "false",
    "DD_INJECTION_ENABLED": "1",
    "DD_INJECT_FORCE": "1",
    "DD_PATCH_MODULES": "unittest:false",
    "CMAKE_BUILD_PARALLEL_LEVEL": "12",
    "CARGO_BUILD_JOBS": "12",
    "DD_TRACE_COMPUTE_STATS": "false",
    "DD_CODE_ORIGIN_FOR_SPANS_ENABLED": "false",
    "DD_CIVISIBILITY_BACKEND_API_TIMEOUT_MILLIS": "2000",
    "_DD_CIVISIBILITY_OUT_OF_SESSION_RETRIES_ENABLED": "1",
}
NIGHTLY_ENVIRONMENT = {"DD_CIVISIBILITY_CODE_COVERAGE_REPORT_UPLOAD_ENABLED": "1"}


class MatrixError(ValueError):
    """Raised when a test matrix declaration is invalid."""


def _collect_suitespecs() -> dict:
    suitespec = {"components": {}, "suites": {}}

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
UV_TEST_SUITES = ("tracer", "tracer-uwsgi", "debugging::debugger") + tuple(
    suite for suite, config in SUITESPEC["suites"].items() if suite.startswith("contrib::") and "matrix" in config
)


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

    suite: str
    name: str
    integration_name: str
    python: str
    direct_dependencies: tuple[str, ...]
    # Preserve historical Riot lock hashes when uv requires a different dependency declaration.
    riot_lock_dependencies: tuple[str, ...]
    runs: tuple[TestRun, ...]

    @property
    def lockfile(self) -> Path:
        return LOCK_ROOT / f"{self.lock_hash}.txt"

    @property
    def lock_hash(self) -> str:
        return _test_environment_hash(self.name, self.python, self.riot_lock_dependencies)

    @property
    def hash(self) -> str:
        name = f"{self.suite}::{self.name}"
        return _test_environment_hash(name, self.python, self.direct_dependencies)


def _requirement_key(requirement: str) -> str:
    match = _REQUIREMENT_NAME.match(requirement)
    if match is None:
        raise MatrixError(f"invalid dependency requirement: {requirement}")
    name, extras = match.groups()
    return f"{name}{extras or ''}".lower().replace("_", "-")


def _test_environment_hash(name: str, python: str, dependencies: tuple[str, ...]) -> str:
    packages = " ".join(f"'{dependency}'" for dependency in dependencies)
    payload = f"{name!r}Interpreter(_hint={python!r}){packages}".encode()
    digest = int(hashlib.sha256(payload).hexdigest(), 16)
    return f"{digest % ((1 << 61) - 1):x}"[:7]


def _merge_dependencies(*groups: tuple[str, ...]) -> tuple[str, ...]:
    merged: dict[str, str] = {}
    for group in groups:
        for requirement in group:
            name = _requirement_key(requirement)
            _, separator, marker = requirement.partition(";")
            key = f"{name};{marker.strip()}" if separator else name
            merged[key] = requirement
    return tuple(merged.values())


def _runs(
    command: str | None, base_environment: dict[str, str], run_specs: list[dict[str, Any]] | None
) -> tuple[TestRun, ...]:
    if run_specs is None:
        if not isinstance(command, str):
            raise MatrixError("each matrix environment needs a command or runs")
        return (TestRun(command=command, env=tuple(sorted(base_environment.items()))),)
    if not run_specs:
        raise MatrixError("runs must not be empty")

    runs = []
    for run in run_specs:
        run_environment = base_environment.copy()
        run_environment.update(run.get("env", {}))
        run_command = run.get("command", command)
        if not isinstance(run_command, str):
            raise MatrixError("each matrix run needs a command")
        runs.append(TestRun(command=run_command, env=tuple(sorted(run_environment.items()))))
    return tuple(runs)


def _variant_settings(
    suite: str,
    suite_config: dict[str, Any],
    matrix: dict[str, Any],
    variant: dict[str, Any],
    nightly: bool,
) -> tuple[tuple[str, ...], tuple[str, ...], str, tuple[TestRun, ...]]:
    dependencies = _merge_dependencies(DEFAULT_DEPENDENCIES, tuple(variant.get("dependencies", ())))
    environment = DEFAULT_ENVIRONMENT.copy()
    if "env" in matrix:
        environment.update(matrix["env"])
    if nightly:
        environment.update(NIGHTLY_ENVIRONMENT)
    if "env" in variant:
        environment.update(variant["env"])

    command = variant.get("command", matrix.get("command"))
    run_specs = variant.get("runs", matrix.get("runs"))
    integration = variant.get("integration", suite_config.get("integration", variant["name"].split(":", 1)[0]))
    riot_lock_dependencies = tuple(variant.get("riot_lock_dependencies", dependencies))
    return dependencies, riot_lock_dependencies, integration, _runs(command, environment, run_specs)


def _expand_suite_matrix(
    suite: str,
    suite_config: dict[str, Any],
    *,
    nightly: bool,
) -> tuple[TestEnvironment, ...]:
    """Expand one compact suite matrix into concrete test environments."""
    matrix = suite_config["matrix"]
    variants = matrix["variants"]
    if not variants:
        raise MatrixError(f"variants for {suite} must not be empty")

    environments = []
    for variant in variants:
        name = variant.get("name")
        if not isinstance(name, str) or not name.strip():
            raise MatrixError(f"every variant for {suite} needs a name")
        python_value = variant.get("python", matrix.get("python", DEFAULT_PYTHON_VERSIONS))
        python_versions = tuple(python_value)
        if not python_versions:
            raise MatrixError(f"variant {name} for {suite} needs a Python version")
        dependencies, riot_lock_dependencies, integration, runs = _variant_settings(
            suite,
            suite_config,
            matrix,
            variant,
            nightly,
        )
        for python in python_versions:
            environments.append(
                TestEnvironment(
                    suite=suite,
                    name=name,
                    integration_name=integration,
                    python=python,
                    direct_dependencies=dependencies,
                    riot_lock_dependencies=riot_lock_dependencies,
                    runs=runs,
                )
            )

    return tuple(environments)


@cache
def get_test_environments(*, nightly: bool) -> dict[str, tuple[TestEnvironment, ...]]:
    """Return every concrete test environment declared by suitespec."""
    return {
        suite: _expand_suite_matrix(suite, config, nightly=nightly)
        for suite, config in get_suites().items()
        if "matrix" in config
    }
