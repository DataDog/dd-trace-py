import pathlib

def test_integrations_have_riot_envs(
    integration_dir_names: set[str],
    riot_venv_names: set[str],
    project_root: pathlib.Path,
    internal_contrib_dir: pathlib.Path
): 
    """
    Verify that every integration directory in ddtrace/contrib/internal has a 
    corresponding Venv defined in riotfile.py.
    """
    missing_riot_envs = integration_dir_names - riot_venv_names

    contrib_internal_rel_path = internal_contrib_dir.relative_to(project_root)

    assert not missing_riot_envs, (
        f"\nThe following integration directories in '{contrib_internal_rel_path}' "
        f"are MISSING a corresponding environment definition in 'riotfile.py':\n"
        f"  - " + "\n  - ".join(sorted(list(missing_riot_envs))) + "\n"
        f"\nPlease add a Venv definition in riotfile.py with a matching 'name'."
    )