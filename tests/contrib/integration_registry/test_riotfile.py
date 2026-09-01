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
    riot_generated_env = {
        "_CI_DD_TAGS",
        "DD_CIVISIBILITY_ITR_ENABLED",
        "_DD_CIVISIBILITY_ITR_FORCE_ENABLE_COVERAGE",
        "_DD_CIVISIBILITY_ITR_PREVENT_TEST_SKIPPING",
    }
    suitespec = suitespec_module.get_test_environments(nightly=False)
    declared_suites = set(suitespec)
    assert declared_suites == {
        suite for suite in declared_suites if suite in suitespec_module.UV_TEST_SUITES or suite.startswith("contrib::")
    }
    suite_patterns = tuple(
        re.compile(suitespec_module.get_suites()[suite].get("pattern", suite)) for suite in declared_suites
    )

    riot_environments = set()
    for environment in riotfile.venv.instances():
        if not any(environment.matches_pattern(pattern) for pattern in suite_patterns):
            continue
        riot_environments.add(
            (
                environment.short_hash,
                tuple(shlex.split(environment.command)),
                tuple(shlex.split(environment.full_pkg_str)),
                tuple(sorted((key, value) for key, value in environment.env.items() if key not in riot_generated_env)),
            )
        )

    declared_environments = {
        (
            environment.lock_hash,
            tuple(shlex.split(run.command)),
            environment.riot_lock_dependencies,
            run.env,
        )
        for environments in suitespec.values()
        for environment in environments
        for run in environment.runs
    }

    assert all(environment.lockfile.is_file() for environments in suitespec.values() for environment in environments)
    assert declared_environments == riot_environments


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
            venv.name = venv.name.split(":")[0]
            if venv.name not in integration_dir_names:
                if venv.name not in EXCLUDED_FROM_TESTING:
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
