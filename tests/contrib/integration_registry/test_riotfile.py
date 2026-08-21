import pathlib
from typing import Any

from mappings import EXCLUDED_FROM_TESTING


def test_integrations_have_test_environments(
    integration_dir_names: set[str],
    test_environment_names: set[str],
    project_root: pathlib.Path,
    internal_contrib_dir: pathlib.Path,
    untested_integrations: set[str],
):
    """
    Verify that every integration directory in ddtrace/contrib/internal has a
    corresponding test environment.
    """
    missing_test_environments = integration_dir_names - test_environment_names - untested_integrations

    contrib_internal_rel_path = internal_contrib_dir.relative_to(project_root)

    assert not missing_test_environments, (
        f"\nThe following integration directories in '{contrib_internal_rel_path}' "
        "are MISSING a corresponding test environment:\n"
        f"  - " + "\n  - ".join(sorted(missing_test_environments)) + "\n"
        "\nPlease add a matching suite definition."
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
