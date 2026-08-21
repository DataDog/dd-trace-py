from collections.abc import Mapping
from pathlib import Path
import re
from typing import Any

from tests.environment import TestEnvironment
from tests.environment import TestRun


def _direct_dependencies(instance: Any) -> tuple[str, ...]:
    nodes = []
    current = instance
    while current is not None:
        nodes.append(current)
        current = current.parent

    dependencies = {}
    for node in reversed(nodes):
        for name, constraint in (node.pkgs or {}).items():
            dependencies[name] = f"{name}{constraint}"
    return tuple(dependencies.values())


def _suite_metadata(suite_config: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "env": tuple(sorted((key, str(value)) for key, value in suite_config.get("env", {}).items())),
        "services": tuple(suite_config.get("services", ())),
        "snapshot": suite_config.get("snapshot", False),
        "retry": suite_config.get("retry"),
        "timeout": suite_config.get("timeout"),
        "parallelism": suite_config.get("parallelism"),
        "environments_per_job": suite_config.get("venvs_per_job"),
        "gpu": suite_config.get("gpu", False),
        "skip_pip_cache": suite_config.get("skip_pip_cache", False),
    }


def load_riot_test_environments(
    suites: Mapping[str, Mapping[str, Any]],
    root: Any = None,
) -> dict[str, tuple[TestEnvironment, ...]]:
    """Translate Riot's expanded configuration into neutral test environments."""
    if root is None:
        import riotfile

        root = riotfile.venv  # type: ignore[attr-defined]

    compiled = {suite: re.compile(config.get("pattern", suite)) for suite, config in suites.items()}
    instances_by_suite: dict[str, dict[str, tuple[int, list[Any]]]] = {suite: {} for suite in suites}

    for ordinal, instance in enumerate(root.instances()):
        if not instance.name:
            continue
        for suite, pattern in compiled.items():
            if instance.matches_pattern(pattern):
                groups = instances_by_suite[suite]
                group = groups.setdefault(instance.short_hash, (ordinal, []))
                group[1].append(instance)

    result = {}
    for suite, groups in instances_by_suite.items():
        metadata = _suite_metadata(suites[suite])
        environments = []
        for environment_id, (ordinal, instances) in groups.items():
            first = instances[0]
            runs = tuple(
                TestRun(
                    command=str(instance.command or ""),
                    env=tuple(sorted((key, str(value)) for key, value in (instance.env or {}).items())),
                )
                for instance in instances
            )
            environments.append(
                TestEnvironment(
                    id=environment_id,
                    suite=suite,
                    name=first.name,
                    python=str(first.py._hint),
                    direct_dependencies=_direct_dependencies(first),
                    runs=runs,
                    lockfile=Path(".riot/requirements") / f"{environment_id}.txt",
                    ordinal=ordinal,
                    **metadata,
                )
            )
        result[suite] = tuple(environments)

    return result
