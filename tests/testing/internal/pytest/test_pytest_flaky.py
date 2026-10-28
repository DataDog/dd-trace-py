from __future__ import annotations

from unittest.mock import patch

from _pytest.pytester import Pytester
import pytest

from ddtrace.testing.internal.test_data import ModuleRef
from ddtrace.testing.internal.test_data import SuiteRef
from ddtrace.testing.internal.test_data import TestRef
from tests.testing.internal.pytest.utils import assert_stats
from tests.testing.mocks import EventCapture
from tests.testing.mocks import mock_api_client_settings
from tests.testing.mocks import setup_standard_mocks


_TEST_CONTENT = """
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


def _known_tests(*test_names: str) -> set[TestRef]:
    return {TestRef(SuiteRef(ModuleRef(""), "test_foo.py"), test_name) for test_name in test_names}


class TestPytestFlakyPlugin:
    """Compatibility coverage for the external pytest-flaky plugin with ddtrace.testing."""

    def test_external_flaky_plugin_drives_retries_when_datadog_retries_disabled(self, pytester: Pytester) -> None:
        pytest.importorskip("flaky")

        pytester.makepyfile(test_foo=_TEST_CONTENT)

        with setup_standard_mocks(), EventCapture.capture() as event_capture:
            result = pytester.inline_run("--ddtrace", "-p", "flaky", "-v", "-s")

        assert result.ret == 1
        assert_stats(result, passed=2, failed=1)

        pass_events = list(event_capture.events_by_test_name("test_pass"))
        fail_events = list(event_capture.events_by_test_name("test_fail"))
        flaky_events = list(event_capture.events_by_test_name("test_flaky"))

        assert len(pass_events) == 1
        assert len(fail_events) == 1
        assert len(flaky_events) == 1  # The external flaky plugin retries internally and reports one passing test event
        assert pass_events[0]["content"]["meta"].get("test.status") == "pass"
        assert fail_events[0]["content"]["meta"].get("test.status") == "fail"
        assert flaky_events[0]["content"]["meta"].get("test.status") == "pass"

    def test_datadog_retries_take_precedence_over_external_flaky_plugin(self, pytester: Pytester) -> None:
        pytest.importorskip("flaky")

        pytester.makepyfile(test_foo=_TEST_CONTENT)

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(
                    auto_retries_enabled=True,
                    known_tests_enabled=True,
                    known_tests=_known_tests("test_pass", "test_fail", "test_flaky"),
                ),
            ),
            setup_standard_mocks(),
            EventCapture.capture() as event_capture,
        ):
            result = pytester.inline_run("--ddtrace", "-p", "flaky", "-v", "-s")

        assert result.ret == 1
        assert_stats(result, passed=2, failed=1)

        fail_events = list(event_capture.events_by_test_name("test_fail"))
        flaky_events = list(event_capture.events_by_test_name("test_flaky"))

        assert len(fail_events) == 6
        assert len(flaky_events) == 2
        assert all(event["content"]["meta"].get("test.status") == "fail" for event in fail_events)
        assert flaky_events[0]["content"]["meta"].get("test.status") == "fail"
        assert flaky_events[1]["content"]["meta"].get("test.status") == "pass"

    def test_datadog_retries_run_when_external_flaky_plugin_disabled(self, pytester: Pytester) -> None:
        pytest.importorskip("flaky")

        pytester.makepyfile(test_foo=_TEST_CONTENT)

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(
                    auto_retries_enabled=True,
                    known_tests_enabled=True,
                    known_tests=_known_tests("test_pass", "test_fail", "test_flaky"),
                ),
            ),
            setup_standard_mocks(),
            EventCapture.capture() as event_capture,
        ):
            result = pytester.inline_run("--ddtrace", "-p", "no:flaky", "-v", "-s")

        assert result.ret == 1
        assert_stats(result, passed=2, failed=1)

        pass_events = list(event_capture.events_by_test_name("test_pass"))
        fail_events = list(event_capture.events_by_test_name("test_fail"))
        flaky_events = list(event_capture.events_by_test_name("test_flaky"))

        assert len(pass_events) == 1
        assert len(fail_events) == 6  # 1 initial failure + 5 Datadog retries
        assert len(flaky_events) == 2  # 1 initial failure + 1 passing Datadog retry
        assert pass_events[0]["content"]["meta"].get("test.status") == "pass"
        assert all(event["content"]["meta"].get("test.status") == "fail" for event in fail_events)
        assert flaky_events[0]["content"]["meta"].get("test.status") == "fail"
        assert flaky_events[1]["content"]["meta"].get("test.status") == "pass"

    def test_skipif_without_condition_with_external_flaky_plugin(self, pytester: Pytester) -> None:
        pytest.importorskip("flaky")

        pytester.makepyfile(
            """
            import pytest

            @pytest.mark.skipif()
            def test_foo():
                assert True
        """
        )

        with setup_standard_mocks(), EventCapture.capture() as event_capture:
            result = pytester.inline_run("--ddtrace", "-p", "flaky", "-v", "-s")

        assert result.ret == 0
        assert_stats(result, skipped=1)
        [test_event] = list(event_capture.events_by_test_name("test_foo"))
        assert test_event["content"]["meta"].get("test.status") == "skip"

    def test_skipif_with_keyword_condition_with_external_flaky_plugin(self, pytester: Pytester) -> None:
        pytest.importorskip("flaky")

        pytester.makepyfile(
            """
            import pytest

            @pytest.mark.skipif(condition=1 > 0, reason="because I can")
            def test_skip():
                assert True

            @pytest.mark.skipif(condition=1 < 0, reason="because I can't")
            def test_no_skip():
                assert True
        """
        )

        with setup_standard_mocks(), EventCapture.capture() as event_capture:
            result = pytester.inline_run("--ddtrace", "-p", "flaky", "-v", "-s")

        assert result.ret == 0
        assert_stats(result, skipped=1, passed=1)
        [skip_event] = list(event_capture.events_by_test_name("test_skip"))
        [no_skip_event] = list(event_capture.events_by_test_name("test_no_skip"))
        assert skip_event["content"]["meta"].get("test.status") == "skip"
        assert no_skip_event["content"]["meta"].get("test.status") == "pass"

    def test_skipif_with_string_condition_with_external_flaky_plugin(self, pytester: Pytester) -> None:
        pytest.importorskip("flaky")

        pytester.makepyfile(
            """
            import pytest

            @pytest.mark.skipif("1 > 0")
            def test_skip():
                assert True

            @pytest.mark.skipif("1 < 0")
            def test_no_skip():
                assert True
        """
        )

        with setup_standard_mocks(), EventCapture.capture() as event_capture:
            result = pytester.inline_run("--ddtrace", "-p", "flaky", "-v", "-s")

        assert result.ret == 0
        assert_stats(result, skipped=1, passed=1)
        [skip_event] = list(event_capture.events_by_test_name("test_skip"))
        [no_skip_event] = list(event_capture.events_by_test_name("test_no_skip"))
        assert skip_event["content"]["meta"].get("test.status") == "skip"
        assert no_skip_event["content"]["meta"].get("test.status") == "pass"

    def test_skipif_with_string_keyword_condition_with_external_flaky_plugin(self, pytester: Pytester) -> None:
        pytest.importorskip("flaky")

        pytester.makepyfile(
            """
            import pytest

            @pytest.mark.skipif(condition="1 > 0", reason="because I can")
            def test_skip():
                assert True

            @pytest.mark.skipif(condition="1 < 0", reason="because I can't")
            def test_no_skip():
                assert True
        """
        )

        with setup_standard_mocks(), EventCapture.capture() as event_capture:
            result = pytester.inline_run("--ddtrace", "-p", "flaky", "-v", "-s")

        assert result.ret == 0
        assert_stats(result, skipped=1, passed=1)
        [skip_event] = list(event_capture.events_by_test_name("test_skip"))
        [no_skip_event] = list(event_capture.events_by_test_name("test_no_skip"))
        assert skip_event["content"]["meta"].get("test.status") == "skip"
        assert no_skip_event["content"]["meta"].get("test.status") == "pass"
