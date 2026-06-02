from __future__ import annotations

from unittest.mock import patch

from _pytest.pytester import Pytester

from tests.testing.mocks import EventCapture
from tests.testing.mocks import mock_api_client_settings
from tests.testing.mocks import setup_standard_mocks


class TestDDTraceTags:
    def test_ddtrace_tags_are_reflected_in_testing_events(self, pytester: Pytester) -> None:
        pytester.makepyfile(
            test_foo="""
            def test_set_ddtrace_tags():
                from ddtrace import tracer
                tracer.current_span().set_tag("my_custom_tag", "foo")
                tracer.current_span().set_tag("my_other_tag", "bar")
                tracer.current_span().set_metric("my_custom_metric", 42)
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

        test_event = event_capture.event_by_test_name("test_set_ddtrace_tags")
        assert test_event["content"]["meta"].get("my_custom_tag") == "foo"
        assert test_event["content"]["meta"].get("my_other_tag") == "bar"
        assert test_event["content"]["metrics"].get("my_custom_metric") == 42

    def test_ddtrace_tags_via_ddspan_fixture(self, pytester: Pytester) -> None:
        pytester.makepyfile(
            test_foo="""
            def test_set_ddtrace_tags(ddspan):
                ddspan.set_tag("my_custom_tag", "foo")
                ddspan.set_tag("my_other_tag", "bar")
                ddspan.set_metric("my_custom_metric", 42)
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

        test_event = event_capture.event_by_test_name("test_set_ddtrace_tags")
        assert test_event["content"]["meta"].get("my_custom_tag") == "foo"
        assert test_event["content"]["meta"].get("my_other_tag") == "bar"
        assert test_event["content"]["metrics"].get("my_custom_metric") == 42
