import importlib
import pathlib
import re
import shlex
import sys
import types
from typing import Any
from unittest import mock

from mappings import EXCLUDED_FROM_TESTING
import yaml

import riotfile


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


def test_suitespec_matches_riot():
    suitespec_module = _load_suitespec()
    suitespec = suitespec_module.get_test_environments(nightly=False)
    uv_suites = set(suitespec_module.UV_TEST_SUITES)
    assert not uv_suites - set(suitespec), f"uv suites missing environment definitions: {uv_suites - set(suitespec)}"

    suite_patterns = tuple(
        re.compile(suitespec_module.get_suites()[suite].get("pattern", suite)) for suite in uv_suites
    )
    riot_environments = set()
    riot_lockfiles = set()
    for environment in riotfile.venv.instances():
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
                run_environment = dict(run.env)
                riotfile._configure_ci_itr_environment(run_environment, environment.python, environment.lock_hash)
                suitespec_environments.add(
                    (
                        environment.name,
                        environment.python,
                        tuple(shlex.split(run.command)),
                        frozenset(environment.riot_lock_dependencies),
                        frozenset(run_environment.items()),
                    )
                )

    missing_riot_environments = suitespec_environments - riot_environments
    assert not missing_riot_environments, f"uv environments missing from Riot: {missing_riot_environments}"

    suitespec_lockfiles = {environment.lockfile for suite in uv_suites for environment in suitespec[suite]}
    assert suitespec_lockfiles == riot_lockfiles, (
        f"Lock files only in suitespec: {suitespec_lockfiles - riot_lockfiles}\n"
        f"Lock files only in Riot: {riot_lockfiles - suitespec_lockfiles}"
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
