import fnmatch

from mappings import EXCLUDED_FROM_TESTING

from tests.suitespec import get_patterns
from tests.suitespec import get_test_environments


def test_integrations_have_suitespec_environments(
    integration_dir_names: set[str],
    untested_integrations: set[str],
):
    environments = get_test_environments(nightly=False)
    patterns = {
        pattern
        for suite, suite_environments in environments.items()
        if suite_environments
        for pattern in get_patterns(suite)
    }
    missing_environments = {
        integration
        for integration in integration_dir_names - untested_integrations
        if not any(
            fnmatch.fnmatch(f"ddtrace/contrib/internal/{integration}/__init__.py", pattern) for pattern in patterns
        )
    }

    assert not missing_environments, "Integrations missing a suitespec environment: " + ", ".join(
        sorted(missing_environments)
    )


def test_contrib_environments_use_valid_integration_names(integration_dir_names: set[str]):
    environments = get_test_environments(nightly=False)
    invalid_environments = [
        environment
        for suite, suite_environments in environments.items()
        if suite.startswith("contrib::")
        for environment in suite_environments
        if any("tests/contrib" in run.command for run in environment.runs)
        and environment.integration_name not in integration_dir_names
        and environment.integration_name not in EXCLUDED_FROM_TESTING
    ]

    assert not invalid_environments, "Contrib environments use unknown integration names: " + ", ".join(
        environment.name for environment in invalid_environments
    )
