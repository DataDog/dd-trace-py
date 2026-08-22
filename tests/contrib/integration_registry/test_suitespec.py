import pathlib

from mappings import EXCLUDED_FROM_TESTING

from tests.suitespec import TestEnvironment as _TestEnvironment


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


def test_contrib_tests_have_valid_environment_name(
    test_environments: tuple[_TestEnvironment, ...], integration_dir_names: set[str]
):
    """
    Verify that every environment with a test path that contains 'contrib' is an actual
    contrib directory.
    """

    failed_environments = []
    for environment in test_environments:
        if any("tests/contrib" in run.command for run in environment.runs):
            name = environment.name.split(":")[0]
            base_name = name.split("-", 1)[0]
            if (
                name not in integration_dir_names
                and name not in EXCLUDED_FROM_TESTING
                and base_name not in integration_dir_names
                and base_name not in EXCLUDED_FROM_TESTING
            ):
                failed_environments.append(environment)

    if failed_environments:
        failure_messages = [f"\n{'*' * 100}"]
        for environment in failed_environments:
            failure_messages.append(
                f"Environment '{environment.name}' has a command containing 'tests/contrib', but is not an "
                "integration under 'ddtrace/contrib/internal'. Update its suite name to match the integration.\n"
            )
        failure_messages.append("*" * 100)
    assert failed_environments == [], "\n".join(failure_messages)
