from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import replace
from itertools import product
import os
import re
from typing import Any
from typing import TypeVar

from tests.environment import TestEnvironment
from tests.environment import TestRun


_REQUIREMENT_NAME = re.compile(r"^([A-Za-z0-9_.-]+)")
_SLUG_PART = re.compile(r"[^a-z0-9]+")
_SPEC_FIELDS = {
    "command",
    "dependencies",
    "dependency_groups",
    "env",
    "name",
    "runs",
}
_T = TypeVar("_T")


class MatrixError(ValueError):
    """Raised when a test matrix declaration is invalid."""


def _string_tuple(value: object, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence):
        return tuple(str(item) for item in value)
    raise MatrixError(f"{field} must be a string or list")


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
    return match.group(1).lower().replace("_", "-")


def _merge_dependencies(*groups: tuple[str, ...]) -> tuple[str, ...]:
    merged: dict[str, str] = {}
    for group in groups:
        for requirement in group:
            merged[_requirement_key(requirement)] = requirement
    return tuple(merged.values())


def _merge_unique(*groups: tuple[_T, ...]) -> tuple[_T, ...]:
    return tuple(dict.fromkeys(item for group in groups for item in group))


def _option_spec(value: object, field: str) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {"dependencies": _string_tuple(value, field)}


def _matches(selector: Mapping[str, Any], selection: Mapping[str, str], axes: set[str]) -> bool:
    for key, expected in selector.items():
        if key not in axes and key != "python":
            raise MatrixError(f"unknown matrix selector: {key}")
        values = _string_tuple(expected, f"selector {key}")
        if selection.get(key) not in values:
            return False
    return True


def _slug(value: str) -> str:
    return _SLUG_PART.sub("-", value.lower()).strip("-")


def _merge_specs(*specs: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    dependencies: tuple[str, ...] = ()
    dependency_groups: tuple[str, ...] = ()
    environment: dict[str, str] = {}
    for spec in specs:
        dependencies = _merge_dependencies(
            dependencies,
            _string_tuple(spec.get("dependencies"), "dependencies"),
        )
        dependency_groups = _merge_unique(
            dependency_groups,
            _string_tuple(spec.get("dependency_groups"), "dependency_groups"),
        )
        environment.update({str(key): str(value) for key, value in _mapping(spec.get("env"), "env").items()})
        for field in ("command", "name", "runs"):
            if field in spec:
                merged[field] = spec[field]
    merged["dependencies"] = dependencies
    merged["dependency_groups"] = dependency_groups
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

    runs = []
    for run_spec in run_specs:
        run = _mapping(run_spec, "run")
        run_environment = dict(base_environment)
        run_environment.update({str(key): str(value) for key, value in _mapping(run.get("env"), "run env").items()})
        run_command = str(run.get("command", command))
        if not run_command:
            raise MatrixError("each matrix run needs a command")
        runs.append(TestRun(command=run_command, env=tuple(sorted(run_environment.items()))))
    return tuple(runs)


def _environment_id(name: str, python: str, groups: tuple[str, ...]) -> str:
    parts = [_slug(name), f"py{python.replace('.', '')}", *(_slug(group) for group in groups)]
    return "-".join(part for part in parts if part)


def _build_environment(
    suite: str,
    suite_config: Mapping[str, Any],
    base_spec: Mapping[str, Any],
    python: str,
    selections: Sequence[tuple[str, str, Mapping[str, Any]]],
    override: Mapping[str, Any],
    ordinal: int,
) -> TestEnvironment:
    selected_specs = tuple(option for _, _, option in selections)
    spec = _merge_specs(base_spec, *selected_specs, override)
    selected_groups = tuple(
        str(option.get("group", choice)) for _, choice, option in selections if option.get("group", choice) is not None
    )
    dependency_groups = _merge_unique(spec["dependency_groups"], selected_groups)
    name = str(spec.get("name", suite.rsplit("::", 1)[-1]))
    return TestEnvironment(
        id=_environment_id(name, python, selected_groups),
        suite=suite,
        name=name,
        python=python,
        direct_dependencies=spec["dependencies"],
        dependency_groups=dependency_groups,
        runs=_runs(spec),
        env=tuple(
            sorted((str(key), str(value)) for key, value in _mapping(suite_config.get("env"), "suite env").items())
        ),
        services=_string_tuple(suite_config.get("services"), "services"),
        snapshot=bool(suite_config.get("snapshot", False)),
        retry=suite_config.get("retry"),
        timeout=suite_config.get("timeout"),
        parallelism=suite_config.get("parallelism"),
        environments_per_job=suite_config.get("venvs_per_job"),
        gpu=bool(suite_config.get("gpu", False)),
        skip_pip_cache=bool(suite_config.get("skip_pip_cache", False)),
        ordinal=ordinal,
    )


def _add_environment(environments: dict[str, TestEnvironment], environment: TestEnvironment) -> None:
    existing = environments.get(environment.id)
    if existing is None:
        environments[environment.id] = environment
        return
    comparable = replace(existing, runs=environment.runs, ordinal=environment.ordinal)
    if comparable != environment:
        raise MatrixError(f"semantic environment ID collision: {environment.id}")
    environments[environment.id] = replace(existing, runs=_merge_unique(existing.runs, environment.runs))


def expand_suite_matrix(
    suite: str,
    suite_config: Mapping[str, Any],
    defaults: Mapping[str, Any] | None = None,
    *,
    nightly: bool | None = None,
) -> tuple[TestEnvironment, ...]:
    """Expand one compact suite matrix into concrete test environments."""
    matrix = _mapping(suite_config.get("matrix"), f"matrix for {suite}")
    if not matrix:
        return ()
    defaults = defaults or {}
    nightly = os.environ.get("NIGHTLY_BUILD") == "true" if nightly is None else nightly
    nightly_spec: Mapping[str, Any] = {}
    if nightly:
        nightly_spec = {
            "env": {
                **_mapping(defaults.get("nightly_env"), "nightly_env"),
                **_mapping(matrix.get("nightly_env"), "matrix nightly_env"),
            }
        }
    base_spec = _merge_specs(defaults, matrix, nightly_spec)

    python_versions = _string_tuple(matrix.get("python", defaults.get("python")), "python")
    if not python_versions:
        raise MatrixError(f"matrix for {suite} does not declare any Python versions")
    axes = _mapping(matrix.get("axes"), "axes")
    axis_names = tuple(str(name) for name in axes)
    axis_options: list[tuple[tuple[str, Mapping[str, Any]], ...]] = []
    for axis_name in axis_names:
        options = _mapping(axes[axis_name], f"axis {axis_name}")
        if not options:
            raise MatrixError(f"axis {axis_name} does not declare any options")
        axis_options.append(
            tuple(
                (str(choice), _option_spec(option, f"axis {axis_name} option {choice}"))
                for choice, option in options.items()
            )
        )

    excludes = matrix.get("exclude", ())
    if isinstance(excludes, (str, bytes)) or not isinstance(excludes, Sequence):
        raise MatrixError("exclude must be a list")

    environments: dict[str, TestEnvironment] = {}
    ordinal = 0
    combinations = product(*axis_options) if axis_options else ((),)
    for python in python_versions:
        for combination in combinations:
            selection = {"python": python, **{axis: choice for axis, (choice, _) in zip(axis_names, combination)}}
            if any(
                python not in _string_tuple(option.get("python"), "option python")
                for _, option in combination
                if option.get("python") is not None
            ):
                continue
            if any(_matches(_mapping(item, "exclude entry"), selection, set(axis_names)) for item in excludes):
                continue
            product_selections = tuple(
                (axis, choice, option) for axis, (choice, option) in zip(axis_names, combination)
            )
            environment = _build_environment(suite, suite_config, base_spec, python, product_selections, {}, ordinal)
            _add_environment(environments, environment)
            ordinal += 1
        combinations = product(*axis_options) if axis_options else ((),)

    includes = matrix.get("include", ())
    if isinstance(includes, (str, bytes)) or not isinstance(includes, Sequence):
        raise MatrixError("include must be a list")
    for raw_include in includes:
        include = _mapping(raw_include, "include entry")
        python = str(include.get("python", ""))
        if not python:
            raise MatrixError("include entries must select a Python version")
        include_selections = []
        for axis_name in axis_names:
            choice = str(include.get(axis_name, ""))
            if not choice:
                raise MatrixError(f"include entry must select axis {axis_name}")
            options = _mapping(axes[axis_name], f"axis {axis_name}")
            if choice not in options:
                raise MatrixError(f"unknown {axis_name} option: {choice}")
            include_selections.append(
                (axis_name, choice, _option_spec(options[choice], f"axis {axis_name} option {choice}"))
            )
        override = {key: value for key, value in include.items() if key in _SPEC_FIELDS}
        environment = _build_environment(suite, suite_config, base_spec, python, include_selections, override, ordinal)
        _add_environment(environments, environment)
        ordinal += 1

    return tuple(environments.values())


def expand_declared_matrices(
    suites: Mapping[str, Mapping[str, Any]],
    defaults: Mapping[str, Any] | None = None,
    *,
    nightly: bool | None = None,
) -> dict[str, tuple[TestEnvironment, ...]]:
    """Expand every suite that has a declarative matrix."""
    return {
        suite: expand_suite_matrix(suite, config, defaults, nightly=nightly)
        for suite, config in suites.items()
        if config.get("matrix")
    }
