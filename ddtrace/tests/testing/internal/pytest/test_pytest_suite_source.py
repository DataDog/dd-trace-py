from unittest.mock import patch

from _pytest.pytester import Pytester

from tests.testing.mocks import EventCapture
from tests.testing.mocks import mock_api_client_settings
from tests.testing.mocks import setup_standard_mocks


class TestSuiteSourceLocation:
    def test_suite_source_file_start_and_end(self, pytester: Pytester) -> None:
        pytester.makepyfile(
            test_suite_source="""
        def test_first():
            assert 1 == 1

        def test_second():
            assert 2 == 2
        """
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

        (suite_event,) = event_capture.events_by_type("test_suite_end")
        suite_meta = suite_event["content"]["meta"]

        assert suite_meta["test.source.file"].endswith("test_suite_source.py")

        first_meta = event_capture.event_by_test_name("test_first")["content"]["meta"]
        second_meta = event_capture.event_by_test_name("test_second")["content"]["meta"]

        # Suite start = minimum test start line; suite end = maximum test end line.
        assert int(suite_meta["test.source.start"]) == min(
            int(first_meta["test.source.start"]), int(second_meta["test.source.start"])
        )
        assert int(suite_meta["test.source.end"]) == max(
            int(first_meta["test.source.end"]), int(second_meta["test.source.end"])
        )
