from __future__ import annotations

from unittest.mock import patch

from _pytest.pytester import Pytester
import pytest

from ddtrace.testing.internal.settings_data import TestProperties
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


def test_explicit_flaky_retries_preserved(pytester: Pytester) -> None:
    pytest.importorskip("flaky")
    pytester.makepyfile(
        test_foo="""
        from flaky import flaky
        import pytest
        count = 0
        @flaky(max_runs=2)
        @pytest.mark.dd_no_auto_retry
        def test_retry():
            global count
            count += 1
            assert count == 2
    """
    )
    with (
        patch(
            "ddtrace.testing.internal.session_manager.APIClient",
            return_value=mock_api_client_settings(auto_retries_enabled=True),
        ),
        setup_standard_mocks(),
        EventCapture.capture() as capture,
    ):
        result = pytester.inline_run("--ddtrace", "-p", "flaky", "-q")
    assert result.ret == 0
    [event] = capture.events_by_test_name("test_retry")
    assert event["content"]["meta"]["test.test_management.no_auto_retry"] == "true"
    assert "test.retry_reason" not in event["content"]["meta"]


def test_excluded_quarantined_failure_retains_outcome(pytester: Pytester) -> None:
    pytest.importorskip("flaky")
    pytester.makepyfile(
        test_foo="""
        from flaky import flaky
        from pathlib import Path
        import pytest
        count = 0
        @flaky(max_runs=2)
        @pytest.mark.dd_no_auto_retry
        def test_quarantined():
            global count
            count += 1
            Path(__file__).with_suffix(".count").write_text(str(count))
            assert False
    """
    )
    known = _known_tests("test_quarantined")
    with (
        patch(
            "ddtrace.testing.internal.session_manager.APIClient",
            return_value=mock_api_client_settings(
                auto_retries_enabled=True,
                test_management_enabled=True,
                known_tests_enabled=True,
                known_tests=known,
                test_management_properties={
                    ref: TestProperties(quarantined=True, attempt_to_fix=True) for ref in known
                },
            ),
        ),
        setup_standard_mocks(),
        EventCapture.capture() as capture,
    ):
        result = pytester.inline_run("--ddtrace", "-p", "flaky", "-q")
    assert result.ret == 0
    assert (pytester.path / "test_foo.count").read_text() == "2"
    [event] = capture.events_by_test_name("test_quarantined")
    meta = event["content"]["meta"]
    assert meta["test.status"] == "fail"
    assert meta["test.test_management.no_auto_retry"] == "true"
    assert meta["test.test_management.is_quarantined"] == "true"
    assert meta["test.test_management.is_attempt_to_fix"] == "true"
    assert "test.retry_reason" not in meta
