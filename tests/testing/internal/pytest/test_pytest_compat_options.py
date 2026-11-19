"""Tests for legacy plugin compatibility options accepted as deprecated no-ops.

The legacy pytest plugin (ddtrace/contrib/internal/pytest) accepted
``--ddtrace-include-class-name`` (and the ``ddtrace-include-class-name`` ini) to prepend
class names to test names. The replacement plugin always includes the class name in test
names, so the option is a no-op — but it must stay accepted so existing commands do not
fail with "unrecognized arguments", and using it emits a ``DDTraceDeprecationWarning``.
"""

from __future__ import annotations

from unittest.mock import patch

from _pytest.pytester import Pytester

from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from tests.testing.mocks import EventCapture
from tests.testing.mocks import mock_api_client_settings
from tests.testing.mocks import setup_standard_mocks


class TestLegacyIncludeClassNameOption:
    def test_flag_is_accepted_and_tests_run(self, pytester: Pytester) -> None:
        """Commands passing the legacy flag must not fail with an unrecognized-argument error."""
        pytester.makepyfile(
            test_file="""
            class TestClass:
                def test_foo(self):
                    assert True
            """
        )

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(),
            ),
            setup_standard_mocks(),
        ):
            result = pytester.inline_run("--ddtrace", "--ddtrace-include-class-name", "-v")

        assert result.ret == 0
        result.assertoutcome(passed=1)

    def test_ini_is_accepted(self, pytester: Pytester) -> None:
        """The legacy ini key must be accepted without warnings about unknown options."""
        pytester.makeini(
            """
            [pytest]
            ddtrace-include-class-name = 1
            """
        )
        pytester.makepyfile(
            test_file="""
            def test_foo():
                assert True
            """
        )

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(),
            ),
            setup_standard_mocks(),
        ):
            result = pytester.inline_run("--ddtrace", "-v")

        assert result.ret == 0
        result.assertoutcome(passed=1)

    def test_class_name_always_included_in_test_name(self, pytester: Pytester) -> None:
        """Class-based test names always include the class; the option is not needed."""
        pytester.makepyfile(
            test_file="""
            class TestClass:
                def test_foo(self):
                    assert True
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
                result = pytester.inline_run("--ddtrace", "--ddtrace-include-class-name", "-v")

        assert result.ret == 0
        test_event_names = [
            event["content"]["meta"]["test.name"] for event in event_capture.events() if event["type"] == "test"
        ]
        assert test_event_names == ["TestClass::test_foo"]

    def test_usage_of_option_emits_deprecation_warning(self, pytester: Pytester) -> None:
        """Using the flag emits a DDTraceDeprecationWarning via pytest's config-time API."""
        pytester.makepyfile(
            test_file="""
            def test_foo():
                assert True
            """
        )

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(),
            ),
            setup_standard_mocks(),
        ):
            result = pytester.inline_run("--ddtrace", "--ddtrace-include-class-name", "-v")

        assert result.ret == 0
        result.assertoutcome(passed=1)
        option_warnings = [
            call
            for call in result.getcalls("pytest_warning_recorded")
            if isinstance(call.warning_message.message, DDTraceDeprecationWarning)
            and "ddtrace-include-class-name" in str(call.warning_message.message)
        ]
        assert option_warnings, "expected a deprecation warning for --ddtrace-include-class-name"

    def test_no_warning_without_option(self, pytester: Pytester) -> None:
        pytester.makepyfile(
            test_file="""
            def test_foo():
                assert True
            """
        )

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(),
            ),
            setup_standard_mocks(),
        ):
            result = pytester.inline_run("--ddtrace", "-v")

        assert result.ret == 0
        result.assertoutcome(passed=1)
        option_warnings = [
            call
            for call in result.getcalls("pytest_warning_recorded")
            if isinstance(call.warning_message.message, DDTraceDeprecationWarning)
            and "ddtrace-include-class-name" in str(call.warning_message.message)
        ]
        assert not option_warnings
