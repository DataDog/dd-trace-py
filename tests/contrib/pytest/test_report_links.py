from pathlib import Path
from unittest.mock import Mock

import pytest

from ddtrace.contrib.internal.pytest import _report_links
from ddtrace.ext import ci
from ddtrace.internal.ci_visibility import CIVisibility
from ddtrace.internal.ci_visibility.api._base import TestVisibilitySessionSettings
from ddtrace.internal.ci_visibility.telemetry.constants import TEST_FRAMEWORKS
from tests.utils import DummyTracer
from tests.utils import override_env


# Test cases taken from <https://github.com/DataDog/datadog-ci/blob/v3.7.0/src/helpers/__tests__/app.test.ts>.
@pytest.mark.parametrize(
    "dd_site, dd_subdomain, expected",
    [
        # Usual datadog site.
        ("datadoghq.com", "", "https://app.datadoghq.com"),
        ("datadoghq.com", "app", "https://app.datadoghq.com"),
        ("datadoghq.com", "myorg", "https://myorg.datadoghq.com"),
        # Other datadog site.
        ("dd.datad0g.com", "", "https://dd.datad0g.com"),
        ("dd.datad0g.com", "dd", "https://dd.datad0g.com"),
        ("dd.datad0g.com", "myorg", "https://myorg.datad0g.com"),
        # Different top-level domain.
        ("datadoghq.eu", "", "https://app.datadoghq.eu"),
        ("datadoghq.eu", "app", "https://app.datadoghq.eu"),
        ("datadoghq.eu", "myorg", "https://myorg.datadoghq.eu"),
        # AP1/US3/US5-type datadog site: the datadog site's subdomain is replaced by `subdomain` when `subdomain`
        # is custom. The correct Main DC (US3 in this case) is resolved automatically.
        ("ap1.datadoghq.com", "", "https://ap1.datadoghq.com"),
        ("ap1.datadoghq.com", "app", "https://ap1.datadoghq.com"),
        ("ap1.datadoghq.com", "myorg", "https://myorg.datadoghq.com"),
        ("us3.datadoghq.com", "", "https://us3.datadoghq.com"),
        ("us3.datadoghq.com", "app", "https://us3.datadoghq.com"),
        ("us3.datadoghq.com", "myorg", "https://myorg.datadoghq.com"),
        ("us5.datadoghq.com", "", "https://us5.datadoghq.com"),
        ("us5.datadoghq.com", "app", "https://us5.datadoghq.com"),
        ("us5.datadoghq.com", "myorg", "https://myorg.datadoghq.com"),
    ],
)
def test_get_base_url(dd_site, dd_subdomain, expected):
    assert _report_links._get_base_url(dd_site, dd_subdomain) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("foo", "foo"),
        ("12345", "12345"),
        ("foo bar", '"foo bar"'),
        ("foo's bar", '"foo\'s bar"'),
        ('a bar named "foo"', '"a bar named \\"foo\\""'),
        ("back\\slash", '"back\\\\slash"'),
    ],
)
def test_quote_for_query(text, expected):
    assert _report_links._quote_for_query(text) == expected


def _get_session_settings() -> TestVisibilitySessionSettings:
    return TestVisibilitySessionSettings(
        tracer=DummyTracer(),
        test_service="the_test_service",
        test_command="the_test_command",
        test_framework="the_test_framework",
        test_framework_metric_name=TEST_FRAMEWORKS.MANUAL,
        test_framework_version="0.0",
        session_operation_name="the_session",
        module_operation_name="the_module",
        suite_operation_name="the_suite",
        test_operation_name="the_test",
        workspace_path=Path().absolute(),
    )


class TerminalReporterMock:
    def __init__(self):
        self.lines = []

    def section(self, text, **kwargs):
        self.lines.append(f"=== {text} ===")

    def line(self, text):
        self.lines.append(text)


def test_print_report_links_full(mocker):
    terminalreporter = TerminalReporterMock()

    mocker.patch.object(
        CIVisibility,
        "get_ci_tags",
        lambda: {
            ci.git.REPOSITORY_URL: "https://github.com/some-org/some-repo",
            ci.git.BRANCH: "main",
            ci.git.COMMIT_SHA: "abcd0123",
            ci.JOB_NAME: "the_job",
            ci.PIPELINE_ID: "123456",
        },
    )
    mocker.patch.object(CIVisibility, "get_session_settings", lambda: _get_session_settings())
    mocker.patch.object(_report_links, "ddconfig", Mock(env=None))

    with override_env({}):
        _report_links.print_test_report_links(terminalreporter)

    assert terminalreporter.lines == [
        "=== Datadog Test Reports ===",
        "View detailed reports in Datadog (they may take a few minutes to become available):",
        "",
        "* Commit report:",
        "  → https://app.datadoghq.com/ci/redirect/tests/https%3A%2F%2Fgithub.com%2Fsome-org%2Fsome-repo/-/the_test_service/-/main/-/abcd0123",
        "",
        "* Test runs report:",
        "  → "
        "https://app.datadoghq.com/ci/test-runs?query=%40ci.job.name%3Athe_job%20%40ci.pipeline.id%3A123456&index=citest",
    ]


def test_print_report_links_only_commit_report(mocker):
    terminalreporter = TerminalReporterMock()

    mocker.patch.object(
        CIVisibility,
        "get_ci_tags",
        lambda: {
            ci.git.REPOSITORY_URL: "https://github.com/some-org/some-repo",
            ci.git.BRANCH: "main",
            ci.git.COMMIT_SHA: "abcd0123",
        },
    )
    mocker.patch.object(CIVisibility, "get_session_settings", lambda: _get_session_settings())
    mocker.patch.object(_report_links, "ddconfig", Mock(env=None))

    with override_env({}):
        _report_links.print_test_report_links(terminalreporter)

    assert terminalreporter.lines == [
        "=== Datadog Test Reports ===",
        "View detailed reports in Datadog (they may take a few minutes to become available):",
        "",
        "* Commit report:",
        "  → https://app.datadoghq.com/ci/redirect/tests/https%3A%2F%2Fgithub.com%2Fsome-org%2Fsome-repo/-/the_test_service/-/main/-/abcd0123",
    ]


def test_print_report_links_only_test_runs_report(mocker):
    terminalreporter = TerminalReporterMock()

    mocker.patch.object(
        CIVisibility,
        "get_ci_tags",
        lambda: {
            ci.JOB_NAME: "the_job",
            ci.PIPELINE_ID: "123456",
        },
    )
    mocker.patch.object(CIVisibility, "get_session_settings", lambda: _get_session_settings())
    mocker.patch.object(_report_links, "ddconfig", Mock(env=None))

    with override_env({}):
        _report_links.print_test_report_links(terminalreporter)

    assert terminalreporter.lines == [
        "=== Datadog Test Reports ===",
        "View detailed reports in Datadog (they may take a few minutes to become available):",
        "",
        "* Test runs report:",
        "  → "
        "https://app.datadoghq.com/ci/test-runs?query=%40ci.job.name%3Athe_job%20%40ci.pipeline.id%3A123456&index=citest",
    ]


def test_print_report_links_no_report(mocker):
    terminalreporter = TerminalReporterMock()

    mocker.patch.object(
        CIVisibility,
        "get_ci_tags",
        lambda: {},
    )
    mocker.patch.object(CIVisibility, "get_session_settings", lambda: _get_session_settings())
    mocker.patch.object(_report_links, "ddconfig", Mock(env=None))

    with override_env({}):
        _report_links.print_test_report_links(terminalreporter)

    assert terminalreporter.lines == []


def test_print_report_links_escape_names(mocker):
    terminalreporter = TerminalReporterMock()

    mocker.patch.object(
        CIVisibility,
        "get_ci_tags",
        lambda: {
            ci.git.REPOSITORY_URL: "https://github.com/some-org/some-repo",
            ci.git.BRANCH: "romain/SDTEST-123/what-am-i-doing-here",
            ci.git.COMMIT_SHA: "abcd0123",
            ci.JOB_NAME: "the_job::the_suite 1/2",
            ci.PIPELINE_ID: 'a "strange" id',
        },
    )
    mocker.patch.object(CIVisibility, "get_session_settings", lambda: _get_session_settings())
    mocker.patch.object(_report_links, "ddconfig", Mock(env=None))

    with override_env({}):
        _report_links.print_test_report_links(terminalreporter)

    assert terminalreporter.lines == [
        "=== Datadog Test Reports ===",
        "View detailed reports in Datadog (they may take a few minutes to become available):",
        "",
        "* Commit report:",
        "  → https://app.datadoghq.com/ci/redirect/tests/https%3A%2F%2Fgithub.com%2Fsome-org%2Fsome-repo/-/the_test_service/-/romain%2FSDTEST-123%2Fwhat-am-i-doing-here/-/abcd0123",
        "",
        "* Test runs report:",
        "  → https://app.datadoghq.com/ci/test-runs?query=%40ci.job.name%3A%22the_job%3A%3Athe_suite%201%2F2%22%20%40ci.pipeline.id%3A%22a%20%5C%22strange%5C%22%20id%22&index=citest",
    ]


def test_print_report_links_commit_report_with_env(mocker):
    terminalreporter = TerminalReporterMock()

    mocker.patch.object(
        CIVisibility,
        "get_ci_tags",
        lambda: {
            ci.git.REPOSITORY_URL: "https://github.com/some-org/some-repo",
            ci.git.BRANCH: "main",
            ci.git.COMMIT_SHA: "abcd0123",
        },
    )
    mocker.patch.object(CIVisibility, "get_session_settings", lambda: _get_session_settings())
    mocker.patch.object(_report_links, "ddconfig", Mock(env="the_env"))

    with override_env({}):
        _report_links.print_test_report_links(terminalreporter)

    assert terminalreporter.lines == [
        "=== Datadog Test Reports ===",
        "View detailed reports in Datadog (they may take a few minutes to become available):",
        "",
        "* Commit report:",
        "  → https://app.datadoghq.com/ci/redirect/tests/https%3A%2F%2Fgithub.com%2Fsome-org%2Fsome-repo/-/the_test_service/-/main/-/abcd0123?env=the_env",
    ]


def test_print_report_links_commit_report_with_ci_env(mocker):
    terminalreporter = TerminalReporterMock()

    mocker.patch.object(
        CIVisibility,
        "get_ci_tags",
        lambda: {
            ci.git.REPOSITORY_URL: "https://github.com/some-org/some-repo",
            ci.git.BRANCH: "main",
            ci.git.COMMIT_SHA: "abcd0123",
        },
    )
    mocker.patch.object(CIVisibility, "get_session_settings", lambda: _get_session_settings())
    mocker.patch.object(_report_links, "ddconfig", Mock(env=None))

    with override_env({"_CI_DD_ENV": "ci_env"}):
        _report_links.print_test_report_links(terminalreporter)

    assert terminalreporter.lines == [
        "=== Datadog Test Reports ===",
        "View detailed reports in Datadog (they may take a few minutes to become available):",
        "",
        "* Commit report:",
        "  → https://app.datadoghq.com/ci/redirect/tests/https%3A%2F%2Fgithub.com%2Fsome-org%2Fsome-repo/-/the_test_service/-/main/-/abcd0123?env=ci_env",
    ]
