from unittest.mock import Mock

import pytest

from ddtrace.testing.internal.ci import CITag
from ddtrace.testing.internal.git import GitTag
from ddtrace.testing.internal.pytest import report_links


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
    assert report_links._get_base_url(dd_site, dd_subdomain) == expected


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
    assert report_links._quote_for_query(text) == expected


class TerminalReporterMock:
    def __init__(self):
        self.lines = []

    def section(self, text, **kwargs):
        self.lines.append(f"=== {text} ===")

    def line(self, text):
        self.lines.append(text)


class TestPytestReportLinks:
    def test_print_report_links_full(self):
        terminalreporter = TerminalReporterMock()
        session_manager = Mock(
            service="the_test_service",
            env=None,
            env_tags={
                GitTag.REPOSITORY_URL: "https://github.com/some-org/some-repo",
                GitTag.BRANCH: "main",
                GitTag.COMMIT_SHA: "abcd0123",
                CITag.JOB_NAME: "the_job",
                CITag.PIPELINE_ID: "123456",
            },
        )

        report_links.print_test_report_links(terminalreporter, session_manager)

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

    def test_print_report_links_only_commit_report(self):
        terminalreporter = TerminalReporterMock()
        session_manager = Mock(
            service="the_test_service",
            env=None,
            env_tags={
                GitTag.REPOSITORY_URL: "https://github.com/some-org/some-repo",
                GitTag.BRANCH: "main",
                GitTag.COMMIT_SHA: "abcd0123",
            },
        )
        report_links.print_test_report_links(terminalreporter, session_manager)

        assert terminalreporter.lines == [
            "=== Datadog Test Reports ===",
            "View detailed reports in Datadog (they may take a few minutes to become available):",
            "",
            "* Commit report:",
            "  → https://app.datadoghq.com/ci/redirect/tests/https%3A%2F%2Fgithub.com%2Fsome-org%2Fsome-repo/-/the_test_service/-/main/-/abcd0123",
        ]

    def test_print_report_links_only_test_runs_report(self):
        terminalreporter = TerminalReporterMock()
        session_manager = Mock(
            service="the_test_service",
            env=None,
            env_tags={
                CITag.JOB_NAME: "the_job",
                CITag.PIPELINE_ID: "123456",
            },
        )
        report_links.print_test_report_links(terminalreporter, session_manager)

        assert terminalreporter.lines == [
            "=== Datadog Test Reports ===",
            "View detailed reports in Datadog (they may take a few minutes to become available):",
            "",
            "* Test runs report:",
            "  → "
            "https://app.datadoghq.com/ci/test-runs?query=%40ci.job.name%3Athe_job%20%40ci.pipeline.id%3A123456&index=citest",
        ]

    def test_print_report_links_no_report(self):
        terminalreporter = TerminalReporterMock()
        session_manager = Mock(
            service="the_test_service",
            env=None,
            env_tags={},
        )

        report_links.print_test_report_links(terminalreporter, session_manager)

        assert terminalreporter.lines == []

    def test_print_report_links_escape_names(self):
        terminalreporter = TerminalReporterMock()
        session_manager = Mock(
            service="the_test_service",
            env=None,
            env_tags={
                GitTag.REPOSITORY_URL: "https://github.com/some-org/some-repo",
                GitTag.BRANCH: "romain/SDTEST-123/what-am-i-doing-here",
                GitTag.COMMIT_SHA: "abcd0123",
                CITag.JOB_NAME: "the_job::the_suite 1/2",
                CITag.PIPELINE_ID: 'a "strange" id',
            },
        )
        report_links.print_test_report_links(terminalreporter, session_manager)

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

    def test_print_report_links_commit_report_with_env(self):
        terminalreporter = TerminalReporterMock()
        session_manager = Mock(
            service="the_test_service",
            env="the_env",
            env_tags={
                GitTag.REPOSITORY_URL: "https://github.com/some-org/some-repo",
                GitTag.BRANCH: "main",
                GitTag.COMMIT_SHA: "abcd0123",
            },
        )
        report_links.print_test_report_links(terminalreporter, session_manager)

        assert terminalreporter.lines == [
            "=== Datadog Test Reports ===",
            "View detailed reports in Datadog (they may take a few minutes to become available):",
            "",
            "* Commit report:",
            "  → https://app.datadoghq.com/ci/redirect/tests/https%3A%2F%2Fgithub.com%2Fsome-org%2Fsome-repo/-/the_test_service/-/main/-/abcd0123?env=the_env",
        ]
