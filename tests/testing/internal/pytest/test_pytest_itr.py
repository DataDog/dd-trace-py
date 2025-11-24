from __future__ import annotations

import sys
import typing as t
from unittest.mock import patch

from _pytest.pytester import Pytester
import pytest

from ddtestpy.internal.test_data import ModuleRef
from ddtestpy.internal.test_data import SuiteRef
from ddtestpy.internal.test_data import TestRef
from ddtestpy.internal.writer import TestCoverageWriter
from tests.mocks import EventCapture
from tests.mocks import mock_api_client_settings
from tests.mocks import setup_standard_mocks


class TestITR:
    @pytest.mark.slow
    def test_itr_one_skipped_test(self, pytester: Pytester) -> None:
        """Test that IntelligentTestRunner skips tests marked as skippable."""
        # Create a test file with multiple tests
        pytester.makepyfile(
            test_foo="""
            def test_should_be_skipped():
                '''A test that should be skipped by ITR.'''
                assert False

            def test_should_run():
                '''A test that should run normally.'''
                assert True
        """
        )

        skippable_items: t.Set[t.Union[TestRef, SuiteRef]] = {
            # Mark one test as skippable.
            TestRef(SuiteRef(ModuleRef("."), "test_foo.py"), "test_should_be_skipped"),
        }

        with patch(
            "ddtestpy.internal.session_manager.APIClient",
            return_value=mock_api_client_settings(skipping_enabled=True, skippable_items=skippable_items),
        ), setup_standard_mocks():
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtestpy", "-p", "no:ddtrace", "-v", "-s")

        # Check that tests completed successfully
        assert result.ret == 0  # Exit code 0 indicates success

        # Verify outcomes: one test skipped by ITR, one test passed
        result.assertoutcome(passed=1, skipped=1)

        # There should be events for 2 tests, 1 suite, 1 module, 1 session
        assert len(list(event_capture.events())) == 5

        # Check that test events have the correct tags.
        skipped_test = event_capture.event_by_test_name("test_should_be_skipped")
        assert skipped_test["content"]["meta"]["test.status"] == "skip"
        assert skipped_test["content"]["meta"]["test.skipped_by_itr"] == "true"
        assert skipped_test["content"]["meta"]["test.skip_reason"] == "Skipped by Datadog Intelligent Test Runner"

        passed_test = event_capture.event_by_test_name("test_should_run")
        assert passed_test["content"]["meta"]["test.status"] == "pass"
        assert passed_test["content"]["meta"].get("test.skipped_by_itr") is None
        assert passed_test["content"]["meta"].get("test.skip_reason") is None

        # Check that session event has the correct tags.
        [session] = event_capture.events_by_type("test_session_end")
        assert session["content"]["meta"]["test.itr.tests_skipping.tests_skipped"] == "true"
        assert session["content"]["meta"]["test.itr.tests_skipping.type"] == "test"
        assert session["content"]["metrics"]["test.itr.tests_skipping.count"] == 1

    @pytest.mark.slow
    def test_itr_disabled(self, pytester: Pytester) -> None:
        """Test that IntelligentTestRunner does not skip tests when ITR is disabled."""
        # Create a test file with multiple tests
        pytester.makepyfile(
            test_foo="""
            def test_should_be_skipped():
                '''A test that should be skipped by ITR.'''
                assert False

            def test_should_run():
                '''A test that should run normally.'''
                assert True
        """
        )

        skippable_items: t.Set[t.Union[TestRef, SuiteRef]] = {
            # Mark one test as skippable.
            TestRef(SuiteRef(ModuleRef("."), "test_foo.py"), "test_should_be_skipped"),
        }

        with patch(
            "ddtestpy.internal.session_manager.APIClient",
            return_value=mock_api_client_settings(skipping_enabled=False, skippable_items=skippable_items),
        ), setup_standard_mocks():
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtestpy", "-p", "no:ddtrace", "-v", "-s")

        # Check that tests completed with failure (1 test failed).
        assert result.ret == 1

        # Verify outcomes: one test failed (not skipped by ITR), one test passed
        result.assertoutcome(passed=1, failed=1)

        # There should be events for 2 tests, 1 suite, 1 module, 1 session
        assert len(list(event_capture.events())) == 5

        # Check that test events have the correct tags.
        skipped_test = event_capture.event_by_test_name("test_should_be_skipped")
        assert skipped_test["content"]["meta"]["test.status"] == "fail"
        assert skipped_test["content"]["meta"].get("test.skipped_by_itr") is None
        assert skipped_test["content"]["meta"].get("test.skip_reason") is None

        passed_test = event_capture.event_by_test_name("test_should_run")
        assert passed_test["content"]["meta"]["test.status"] == "pass"
        assert passed_test["content"]["meta"].get("test.skipped_by_itr") is None
        assert passed_test["content"]["meta"].get("test.skip_reason") is None

        # Check that session event has the correct tags.
        [session] = event_capture.events_by_type("test_session_end")
        assert session["content"]["meta"].get("test.itr.tests_skipping.tests_skipped") is None
        assert session["content"]["meta"].get("test.itr.tests_skipping.type") is None
        assert session["content"]["metrics"].get("test.itr.tests_skipping.count") is None

    @pytest.mark.slow
    def test_itr_one_unskippable_test(self, pytester: Pytester) -> None:
        """Test that IntelligentTestRunner skips tests marked as skippable."""
        # Create a test file with multiple tests
        pytester.makepyfile(
            test_foo="""
            import pytest

            def test_should_be_skipped():
                '''A test that should be skipped by ITR.'''
                assert False

            @pytest.mark.skipif(False, reason='datadog_itr_unskippable')
            def test_unskippable():
                '''A test that should NOT be skipped by ITR due to being unskippable.'''
                assert False

            def test_should_run():
                '''A test that should run normally.'''
                assert True
        """
        )

        skippable_items: t.Set[t.Union[TestRef, SuiteRef]] = {
            # Mark one test as skippable.
            TestRef(SuiteRef(ModuleRef("."), "test_foo.py"), "test_should_be_skipped"),
            TestRef(SuiteRef(ModuleRef("."), "test_foo.py"), "test_unskippable"),
        }

        with patch(
            "ddtestpy.internal.session_manager.APIClient",
            return_value=mock_api_client_settings(skipping_enabled=True, skippable_items=skippable_items),
        ), setup_standard_mocks():
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtestpy", "-p", "no:ddtrace", "-v", "-s")

        # Check that tests completed with failure (1 test failed).
        assert result.ret == 1

        # Verify outcomes: one test skipped by ITR, one failed (not skipped), one test passed
        result.assertoutcome(passed=1, failed=1, skipped=1)

        # There should be events for 3 tests, 1 suite, 1 module, 1 session
        assert len(list(event_capture.events())) == 6

        # Check that test events have the correct tags.
        skipped_test = event_capture.event_by_test_name("test_should_be_skipped")
        assert skipped_test["content"]["meta"]["test.status"] == "skip"
        assert skipped_test["content"]["meta"]["test.skipped_by_itr"] == "true"
        assert skipped_test["content"]["meta"]["test.skip_reason"] == "Skipped by Datadog Intelligent Test Runner"

        unskippable_test = event_capture.event_by_test_name("test_unskippable")
        assert unskippable_test["content"]["meta"]["test.status"] == "fail"
        assert unskippable_test["content"]["meta"].get("test.skipped_by_itr") is None
        assert unskippable_test["content"]["meta"].get("test.skip_reason") is None
        assert unskippable_test["content"]["meta"].get("test.itr.unskippable") == "true"
        assert unskippable_test["content"]["meta"].get("test.itr.forced_run") == "true"

        passed_test = event_capture.event_by_test_name("test_should_run")
        assert passed_test["content"]["meta"]["test.status"] == "pass"
        assert passed_test["content"]["meta"].get("test.skipped_by_itr") is None
        assert passed_test["content"]["meta"].get("test.skip_reason") is None

        # Check that session event has the correct tags.
        [session] = event_capture.events_by_type("test_session_end")
        assert session["content"]["meta"]["test.itr.tests_skipping.tests_skipped"] == "true"
        assert session["content"]["meta"]["test.itr.tests_skipping.type"] == "test"
        assert session["content"]["metrics"]["test.itr.tests_skipping.count"] == 1

    @pytest.mark.slow
    @pytest.mark.skipif("slipcover" in sys.modules, reason="slipcover is incompatible with ITR code coverage")
    def test_itr_code_coverage_enabled(self, pytester: Pytester) -> None:
        pytester.makepyfile(
            lib_constants="""
            ANSWER = 42
            """,
            test_foo="""
            from lib_constants import ANSWER

            def test_answer():
                assert ANSWER == 42
            """,
        )
        with patch(
            "ddtestpy.internal.session_manager.APIClient",
            return_value=mock_api_client_settings(coverage_enabled=True),
        ), setup_standard_mocks():
            with patch.object(TestCoverageWriter, "put_event") as put_event_mock:
                pytester.inline_run("--ddtestpy", "-p", "no:ddtrace", "-v", "-s")

        coverage_events = [args[0] for args, kwargs in put_event_mock.call_args_list]
        covered_files = set(f["filename"] for f in coverage_events[0]["files"])
        assert covered_files == {"/test_foo.py", "/lib_constants.py"}

    @pytest.mark.slow
    @pytest.mark.skipif("slipcover" in sys.modules, reason="slipcover is incompatible with ITR code coverage")
    def test_itr_code_coverage_disabled(self, pytester: Pytester) -> None:
        pytester.makepyfile(
            lib_constants="""
            ANSWER = 42
            """,
            test_foo="""
            from lib_constants import ANSWER

            def test_answer():
                assert ANSWER == 42
            """,
        )
        with patch(
            "ddtestpy.internal.session_manager.APIClient",
            return_value=mock_api_client_settings(coverage_enabled=False),
        ), setup_standard_mocks():
            with patch.object(TestCoverageWriter, "put_event") as put_event_mock:
                pytester.inline_run("--ddtestpy", "-p", "no:ddtrace", "-v", "-s")

        coverage_events = [args[0] for args, kwargs in put_event_mock.call_args_list]
        assert coverage_events == []
