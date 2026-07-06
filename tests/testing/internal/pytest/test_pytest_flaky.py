from __future__ import annotations

from _pytest.pytester import Pytester
import pytest

from tests.testing.internal.pytest.utils import assert_stats
from tests.testing.mocks import EventCapture
from tests.testing.mocks import setup_standard_mocks


class TestPytestFlakyPlugin:
    """Compatibility coverage for the external pytest-flaky plugin with ddtrace.testing."""

    def test_external_flaky_plugin_drives_retries_when_datadog_retries_disabled(self, pytester: Pytester) -> None:
        pytest.importorskip("flaky")

        pytester.makepyfile(
            test_foo="""
            import flaky

            def test_pass():
                assert True

            def test_fail():
                assert False

            flaky_counter = 0

            @flaky.flaky
            def test_flaky():
                global flaky_counter
                flaky_counter += 1
                assert flaky_counter >= 2
            """
        )

        with setup_standard_mocks():
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-p", "flaky", "-v", "-s")

        assert result.ret == 1
        assert_stats(result, passed=2, failed=1)

        pass_events = list(event_capture.events_by_test_name("test_pass"))
        fail_events = list(event_capture.events_by_test_name("test_fail"))
        flaky_events = list(event_capture.events_by_test_name("test_flaky"))

        assert len(pass_events) == 1
        assert len(fail_events) == 1
        assert len(flaky_events) == 1
        assert pass_events[0]["content"]["meta"].get("test.status") == "pass"
        assert fail_events[0]["content"]["meta"].get("test.status") == "fail"
        assert flaky_events[0]["content"]["meta"].get("test.status") == "pass"
