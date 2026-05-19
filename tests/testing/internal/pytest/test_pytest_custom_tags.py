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
        assert (
            skipped_test["content"]["meta"]["test.module"] == ""
        )  # Empty string for root-level tests (matches old plugin)
        assert skipped_test["content"]["meta"]["test.suite"] == "test_file.py"
        assert skipped_test["content"]["meta"]["test.name"] == "TEST_FOO"

    def test_test_original_name_tag_for_parameterized_tests(self, pytester: Pytester) -> None:
        pytester.makepyfile(
            test_file="""
            import pytest

            @pytest.mark.parametrize("value", ["foo", "bar"], ids=["foo_id", "bar_id"])
            def test_foo(value):
                assert value
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

        test_events = [event for event in event_capture.events_by_type("test")]
        assert len(test_events) == 2

        test_names = {event["content"]["meta"]["test.name"] for event in test_events}
        assert test_names == {"test_foo[foo_id]", "test_foo[bar_id]"}

        test_original_names = {event["content"]["meta"]["test.original_name"] for event in test_events}
        assert test_original_names == {"test_foo"}

        # test.parameterized_name equals current test.name
        for event in test_events:
            meta = event["content"]["meta"]
            assert meta["test.parameterized_name"] == meta["test.name"]

    def test_test_original_name_tag_not_added_when_originalname_is_none(self, pytester: Pytester) -> None:
        """When originalname is None, the test.original_name tag must not be set."""
        pytester.makepyfile(
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
            patch(
                "ddtrace.testing.internal.pytest.plugin._get_test_original_name",
                return_value=None,
            ),
        ):
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-v", "-s")

        assert result.ret == 0
        result.assertoutcome(passed=1)

        test_events = [event for event in event_capture.events_by_type("test")]
        assert len(test_events) == 1
        assert "test.original_name" not in test_events[0]["content"]["meta"]
        # test.parameterized_name is set (same value as test.name)
        assert test_events[0]["content"]["meta"]["test.parameterized_name"] == "test_foo"

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
