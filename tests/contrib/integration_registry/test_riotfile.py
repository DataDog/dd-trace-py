import importlib
import json
import os
import pathlib
import re
import shlex
import sys
import types
from typing import Any
from unittest import mock

from mappings import EXCLUDED_FROM_TESTING
from packaging.version import Version
import yaml

import riotfile


PYTHON_COMPATIBILITY_VERSIONS = ("3.14", "3.15")


def _riot_venv_instances():
    return getattr(riotfile, "_venv_instances")()


def _load_suitespec():
    ruamel = types.ModuleType("ruamel")
    ruamel_yaml = types.ModuleType("ruamel.yaml")

    class YAML:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def load(self, content):
            return yaml.safe_load(content.read_text())

    ruamel_yaml.YAML = YAML
    ruamel.yaml = ruamel_yaml
    with mock.patch.dict(sys.modules, {"ruamel": ruamel, "ruamel.yaml": ruamel_yaml}):
        return importlib.import_module("tests.suitespec")


def test_uv_suitespec_matches_riot():
    suitespec_module = _load_suitespec()
    suites = suitespec_module.get_suites()
    uv_suites = set(suitespec_module.UV_TEST_SUITES)
    missing_matrices = {
        suite
        for suite, config in suites.items()
        if "benchmark" not in config.get("type", "test") and suite not in uv_suites
    }
    assert not missing_matrices, f"Suites missing a matrix: {missing_matrices}"

    # riotfile injects the nightly-only coverage env var into every venv when NIGHTLY_BUILD is set,
    # so mirror that here to keep the comparison valid on both regular and nightly CI pipelines.
    nightly = os.environ.get("NIGHTLY_BUILD") == "true"
    suitespec = suitespec_module.get_test_environments(nightly=nightly)

    suite_patterns = tuple(re.compile(suites[suite].get("pattern", suite)) for suite in uv_suites)
    riot_environments = set()
    riot_lockfiles = set()
    for environment in _riot_venv_instances():
        if not any(environment.matches_pattern(pattern) for pattern in suite_patterns):
            continue
        riot_environments.add(
            (
                environment.name,
                environment.py._hint,
                tuple(shlex.split(environment.command)),
                frozenset(shlex.split(environment.full_pkg_str)),
                frozenset(environment.env.items()),
            )
        )
        riot_lockfiles.add(suitespec_module.LOCK_ROOT / f"{environment.short_hash}.txt")
    suitespec_environments = set()
    for suite in uv_suites:
        for environment in suitespec[suite]:
            for run in environment.runs:
                suitespec_environments.add(
                    (
                        environment.name,
                        environment.python,
                        tuple(shlex.split(run.command)),
                        frozenset(environment.riot_lock_dependencies),
                        frozenset(run.environment.items()),
                    )
                )

    assert suitespec_environments == riot_environments, (
        f"Environments missing from Riot: {suitespec_environments - riot_environments}\n"
        f"Environments missing from suitespec: {riot_environments - suitespec_environments}"
    )

    suitespec_lockfiles = {environment.lockfile for suite in uv_suites for environment in suitespec[suite]}
    assert suitespec_lockfiles == riot_lockfiles, (
        f"Lock files missing from Riot: {suitespec_lockfiles - riot_lockfiles}\n"
        f"Lock files missing from suitespec: {riot_lockfiles - suitespec_lockfiles}"
    )
    missing_lockfiles = {lockfile for lockfile in suitespec_lockfiles if not lockfile.is_file()}
    assert not missing_lockfiles, f"Missing suitespec lock files: {missing_lockfiles}"


def test_integrations_have_riot_envs(
    integration_dir_names: set[str],
    riot_venv_names: set[str],
    project_root: pathlib.Path,
    internal_contrib_dir: pathlib.Path,
    untested_integrations: set[str],
):
    """
    Verify that every integration directory in ddtrace/contrib/internal has a
    corresponding Venv defined in riotfile.py.
    """
    missing_riot_envs = integration_dir_names - riot_venv_names - untested_integrations

    contrib_internal_rel_path = internal_contrib_dir.relative_to(project_root)

    assert not missing_riot_envs, (
        f"\nThe following integration directories in '{contrib_internal_rel_path}' "
        f"are MISSING a corresponding environment definition in 'riotfile.py':\n"
        f"  - " + "\n  - ".join(sorted(list(missing_riot_envs))) + "\n"
        "\nPlease add a Venv definition in riotfile.py with a matching 'name'."
    )


def test_contrib_tests_have_valid_contrib_venv_name(riot_venvs: Any, integration_dir_names: set[str]):
    """
    Verify that every riot venv with a test path that contains 'contrib' is an actual
    contrib directory.
    """

    failed_venvs = []
    for venv in riot_venvs:
        if venv.command and "tests/contrib" in venv.command:
            # some venvs have sub-venvs in the form of venv-name:sub-venv-name, we only want the main one
            # e.g. django:django_hosts -> django
            venv_name = venv.name.split(":")[0]
            if venv_name not in integration_dir_names:
                if venv_name not in EXCLUDED_FROM_TESTING:
                    failed_venvs.append(venv)

    if failed_venvs:
        failure_messages = [f"\n{'*' * 100}"]
        for venv in failed_venvs:
            failure_messages.append(
                f"Venv '{venv.name}' has a test command that contains 'tests/contrib': {venv.command}, but "
                f"is not an actual integration with directory in 'ddtrace/contrib/internal'. Please "
                f"update 'riotfile.py' to place this Venv as a sub-venv of the integration it is testing.\n"
            )
        failure_messages.append("*" * 100)
    assert failed_venvs == [], "\n".join(failure_messages)


def _contrib_riot_python_versions(integration_dir_names: set[str]) -> dict[str, set[str]]:
    """Collect Python versions for every internal contrib and its Riot environments."""
    # Include integrations marked is_tested=false in registry.yaml when Riot has a test
    # environment for them: this report is specifically intended to expose those matrix gaps.
    supported_integrations = integration_dir_names - EXCLUDED_FROM_TESTING
    versions: dict[str, set[str]] = {name: set() for name in supported_integrations}
    for environment in _riot_venv_instances():
        if not environment.name or "tests/contrib/" not in (environment.command or ""):
            continue
        integration_name = environment.name.split(":", 1)[0]
        if integration_name in supported_integrations:
            versions[integration_name].add(environment.py._hint)
    return versions


def _highest_tested_dependency_versions(project_root: pathlib.Path) -> dict[str, dict[str, dict[str, str]]]:
    """Read the highest locked dependency versions tested by each integration and Python version."""
    supported_versions = json.loads((project_root / "supported_versions.json").read_text())
    highest: dict[str, dict[str, dict[str, str]]] = {}

    for entry in supported_versions:
        integration_name = entry["integrationName"]
        dependency_name = entry["dependencyName"]
        integration_versions = highest.setdefault(integration_name, {})
        for version_group in entry["versions"]:
            tested_versions = version_group.get("tested", [])
            if not tested_versions:
                continue
            version = max(tested_versions, key=Version)
            for python_version in version_group["testedRuntimes"]["python"]:
                dependency_versions = integration_versions.setdefault(dependency_name, {})
                previous = dependency_versions.get(python_version)
                if previous is None or Version(version) > Version(previous):
                    dependency_versions[python_version] = version

    return highest


def test_contrib_python_compatibility_inventory(project_root, integration_dir_names):
    """Record coverage, highest tested versions, and gaps for every supported contrib."""
    versions = _contrib_riot_python_versions(integration_dir_names)
    highest_dependency_versions = _highest_tested_dependency_versions(project_root)
    report = {}

    for integration_name, python_versions in sorted(versions.items()):
        report[integration_name] = {
            "highest_tested_python": max(python_versions, key=lambda version: tuple(map(int, version.split("."))))
            if python_versions
            else None,
            "python": {
                version: "scheduled" if version in python_versions else "not_scheduled"
                for version in PYTHON_COMPATIBILITY_VERSIONS
            },
            "highest_tested_dependency_versions": {
                dependency: max(dependencies.values(), key=Version)
                for dependency, dependencies in highest_dependency_versions.get(integration_name, {}).items()
            },
            "highest_tested_dependency_versions_by_python": highest_dependency_versions.get(integration_name, {}),
        }

    not_scheduled = {
        version: sorted(name for name, values in versions.items() if version not in values)
        for version in PYTHON_COMPATIBILITY_VERSIONS
    }
    scheduled = sorted(name for name, values in versions.items() if set(PYTHON_COMPATIBILITY_VERSIONS) <= values)

    # This inventory describes the generated Riot matrix. Runtime pass/fail remains the result of
    # each integration's own CI job; "scheduled" must not be interpreted as a passing test.
    report_path = project_root / "test-results" / "integration-compatibility.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {
                "python_compatibility_versions": list(PYTHON_COMPATIBILITY_VERSIONS),
                "integrations": report,
                "scheduled_for_both": scheduled,
                "not_scheduled": not_scheduled,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    assert set(scheduled) | set(not_scheduled["3.14"]) | set(not_scheduled["3.15"]) == set(versions)
    assert not set(scheduled) & (set(not_scheduled["3.14"]) | set(not_scheduled["3.15"]))
