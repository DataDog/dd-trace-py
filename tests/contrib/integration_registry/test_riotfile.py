import pathlib

def test_integrations_have_riot_envs(
    integration_dir_names: set[str],
    riot_venv_names: set[str],
    project_root: pathlib.Path,
    internal_contrib_dir: pathlib.Path,
    untested_integrations: set[str]
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
        f"\nPlease add a Venv definition in riotfile.py with a matching 'name'."
    )

def test_contrib_tests_have_valid_contrib_venv_name(
    riot_venvs: set[str],
    integration_dir_names: set[str]
):
    """
    Verify that every riot venv with a test path that contains 'contrib' is an actual
    contrib directory.
    """

    WHITE_LISTED_VENVS = [
        "asynctest",
        "dbapi",
        "dbapi_async",
        "integration_registry",
        "gunicorn"
    ]

    failed_venvs = []
    for venv in riot_venvs:
        if venv.command and "tests/contrib" in venv.command:
            # some venvs have sub-venvs in the form of venv-name[sub-venv-name], we only want the main one
            # e.g. django[django_hosts] -> django
            venv.name = venv.name.split("[")[0]
            if venv.name not in integration_dir_names:
                if venv.name not in WHITE_LISTED_VENVS:
                    failed_venvs.append(venv)

    if failed_venvs:
        failure_messages = ["\n" + "*" * 100]
        for venv in failed_venvs:
            failure_messages.append(
                f"Venv '{venv.name}' has a test command that contains 'tests/contrib': {venv.command}, but "
                f"is not an actual integration with directory in 'ddtrace/contrib/internal'. Please "
                f"update 'riotfile.py' to place this Venv as a sub-venv of the integration it is testing.\n"
            )
        failure_messages.append("*" * 100)
    assert failed_venvs == [], "\n".join(failure_messages)

