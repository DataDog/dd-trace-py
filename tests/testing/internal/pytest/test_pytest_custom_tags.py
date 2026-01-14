from __future__ import annotations

from unittest.mock import patch

from _pytest.pytester import Pytester

from tests.testing.mocks import EventCapture
from tests.testing.mocks import mock_api_client_settings
from tests.testing.mocks import setup_standard_mocks


class TestCustomTags:
    def test_dd_tags(self, pytester: Pytester) -> None:
        """Test that IntelligentTestRunner skips tests marked as skippable."""
        pytester.makepyfile(
            test_file="""
            import pytest

            @pytest.mark.dd_tags(some_tag="hello", some_nonstring_value=42)
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
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-v", "-s")

        assert result.ret == 0
        result.assertoutcome(passed=1)

        # There should be events for 1 tests, 1 suite, 1 module, 1 session
        assert len(list(event_capture.events())) == 4

        # Check that test events have the correct tags.
        skipped_test = event_capture.event_by_test_name("test_foo")
        assert skipped_test["content"]["meta"]["some_tag"] == "hello"
        assert skipped_test["content"]["meta"]["some_nonstring_value"] == "42"  # converted to string

    def test_custom_test_name_hook(self, pytester: Pytester) -> None:
        """Test that test name can be overridden by hooks, and module and suite names keep the default value."""
        pytester.makepyfile(
            conftest="""
            import pytest

            @pytest.hookimpl()
            def pytest_ddtrace_get_item_test_name(item):
                return item.nodeid.split("::")[1].upper()
            """,
            test_file="""
            def test_foo():
                assert True
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
        result.assertoutcome(passed=1)

        # There should be events for 1 tests, 1 suite, 1 module, 1 session
        assert len(list(event_capture.events())) == 4

        # Check that test events have the correct tags.
        skipped_test = event_capture.event_by_test_name("TEST_FOO")
        assert skipped_test["content"]["meta"]["test.module"] == "."
        assert skipped_test["content"]["meta"]["test.suite"] == "test_file.py"
        assert skipped_test["content"]["meta"]["test.name"] == "TEST_FOO"

    def test_custom_test_module_and_suite_hooks(self, pytester: Pytester) -> None:
        """Test that module and suite names can be overridden by hooks, and test name keeps the default value."""
        pytester.makepyfile(
            conftest="""
            import pytest

            @pytest.hookimpl()
            def pytest_ddtrace_get_item_module_name(item):
                return "le_mod"

            @pytest.hookimpl()
            def pytest_ddtrace_get_item_suite_name(item):
                return item.nodeid.split("::")[0].upper()
            """,
            test_file="""
            def test_foo():
                assert True
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
        result.assertoutcome(passed=1)

        # There should be events for 1 tests, 1 suite, 1 module, 1 session
        assert len(list(event_capture.events())) == 4

        # Check that test events have the correct tags.
        skipped_test = event_capture.event_by_test_name("test_foo")
        assert skipped_test["content"]["meta"]["test.module"] == "le_mod"
        assert skipped_test["content"]["meta"]["test.suite"] == "TEST_FILE.PY"
        assert skipped_test["content"]["meta"]["test.name"] == "test_foo"
