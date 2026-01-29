import json
from unittest.mock import patch

from _pytest.pytester import Pytester

from tests.testing.mocks import EventCapture
from tests.testing.mocks import mock_api_client_settings
from tests.testing.mocks import setup_standard_mocks


class TestCodeowners:
    def test_pytest_codeowners(self, pytester: Pytester) -> None:
        pytester.makepyfile(
            test_team_a="""
        import pytest

        def test_team_a():
            assert 1 == 1
        """
        )

        pytester.makepyfile(
            test_team_b="""
        import pytest

        def test_team_b():
            assert 1 == 1
        """
        )

        pytester.makefile(
            "",
            CODEOWNERS="""
        * @default-team
        test_team_b.py @team-b @backup-b
        """,
        )

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(),
            ),
            setup_standard_mocks(),
        ):
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-v", "-s")

        assert result.ret == 0
        result.assertoutcome(passed=2)

        # There should be events for 2 tests, 2 suites, 1 module, 1 session
        assert len(list(event_capture.events())) == 6

        team_a_span = event_capture.event_by_test_name("test_team_a")
        assert json.loads(team_a_span["content"]["meta"]["test.codeowners"]) == ["@default-team"]

        team_b_span = event_capture.event_by_test_name("test_team_b")
        assert json.loads(team_b_span["content"]["meta"]["test.codeowners"]) == ["@team-b", "@backup-b"]
