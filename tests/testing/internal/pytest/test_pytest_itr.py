from __future__ import annotations

import sys
import typing as t
from unittest.mock import patch

from _pytest.pytester import Pytester
import pytest

from ddtrace.testing.internal.settings_data import TestProperties
from ddtrace.testing.internal.test_data import ModuleRef
from ddtrace.testing.internal.test_data import SuiteRef
from ddtrace.testing.internal.test_data import TestRef
from ddtrace.testing.internal.writer import TestCoverageWriter
from tests.testing.mocks import EventCapture
from tests.testing.mocks import mock_api_client_settings
from tests.testing.mocks import setup_standard_mocks


COVERAGE_UPLOAD_ENABLED_ENV = "DD_CIVISIBILITY_CODE_COVERAGE_REPORT_UPLOAD_ENABLED"


class TestITR:
    @pytest.fixture(autouse=True)
    def isolate_coverage_upload_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unset coverage upload env var so tests are not affected by external environment."""
        monkeypatch.delenv(COVERAGE_UPLOAD_ENABLED_ENV, raising=False)

    def test_itr_one_skipped_test(self, pytester: Pytester, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that IntelligentTestRunner skips tests marked as skippable."""
        monkeypatch.setenv("_DD_CIVISIBILITY_SEND_DESELECTS", "1")

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

        skippable_items: set[t.Union[TestRef, SuiteRef]] = {
            # Mark one test as skippable.
            TestRef(SuiteRef(ModuleRef(""), "test_foo.py"), "test_should_be_skipped"),
        }

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(skipping_enabled=True, skippable_items=skippable_items),
            ),
            setup_standard_mocks(),
        ):
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-v", "-s")

        # Check that tests completed successfully
        assert result.ret == 0  # Exit code 0 indicates success

        # The ITR-skippable test is deselected at collection time, not skip-marked, so pytest's own
        # outcome counters don't see it — only the passed test is counted here.
        result.assertoutcome(passed=1)

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
        assert session["content"]["meta"]["test.itr.tests_skipping.enabled"] == "true"
        assert session["content"]["meta"]["test.itr.tests_skipping.tests_skipped"] == "true"
        assert session["content"]["meta"]["_dd.ci.itr.tests_skipped"] == "true"
        assert session["content"]["meta"]["test.itr.tests_skipping.type"] == "test"
        assert session["content"]["metrics"]["test.itr.tests_skipping.count"] == 1

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

        skippable_items: set[t.Union[TestRef, SuiteRef]] = {
            # Mark one test as skippable.
            TestRef(SuiteRef(ModuleRef(""), "test_foo.py"), "test_should_be_skipped"),
        }

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(skipping_enabled=False, skippable_items=skippable_items),
            ),
            setup_standard_mocks(),
        ):
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-v", "-s")

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
        assert session["content"]["meta"]["test.itr.tests_skipping.enabled"] == "false"
        assert session["content"]["meta"].get("test.itr.tests_skipping.tests_skipped") is None
        assert session["content"]["meta"].get("_dd.ci.itr.tests_skipped") is None
        assert session["content"]["meta"].get("test.itr.tests_skipping.type") is None
        assert session["content"]["metrics"].get("test.itr.tests_skipping.count") is None

    def test_itr_unskippable_not_emitted_when_skipping_disabled(self, pytester: Pytester) -> None:
        """Regression: unskippable tag and telemetry must not be emitted when ITR skipping is disabled."""
        pytester.makepyfile(
            test_foo="""
            import pytest

            @pytest.mark.skipif(False, reason='datadog_itr_unskippable')
            def test_has_unskippable_marker():
                '''Has datadog_itr_unskippable marker but skipping is disabled.'''
                assert True
        """
        )

        skippable_items: set[t.Union[TestRef, SuiteRef]] = {
            TestRef(SuiteRef(ModuleRef(""), "test_foo.py"), "test_has_unskippable_marker"),
        }

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(skipping_enabled=False, skippable_items=skippable_items),
            ),
            setup_standard_mocks(),
        ):
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-v", "-s")

        assert result.ret == 0
        result.assertoutcome(passed=1)

        test_event = event_capture.event_by_test_name("test_has_unskippable_marker")
        assert test_event["content"]["meta"]["test.status"] == "pass"
        # Must NOT have unskippable tag when skipping is disabled (avoids inflating itr_unskippable telemetry).
        assert test_event["content"]["meta"].get("test.itr.unskippable") is None
        assert test_event["content"]["meta"].get("test.itr.forced_run") is None

    def test_itr_unskippable_not_emitted_when_test_not_in_skippable_list(self, pytester: Pytester) -> None:
        """Regression: unskippable tag and telemetry must not be emitted when the test is not in skippable_items.

        Even with skipping_enabled=True, we only mark unskippable when is_skippable_test(test_ref) is True (test or
        suite in skippable_items). If the test is not in the list, we must not emit itr_unskippable.
        """
        pytester.makepyfile(
            test_foo="""
            import pytest

            @pytest.mark.skipif(False, reason='datadog_itr_unskippable')
            def test_has_unskippable_marker_but_not_skippable():
                '''Has unskippable marker but not in skippable_items (e.g. new test).'''
                assert True
        """
        )

        # Skipping is enabled but this test is NOT in skippable_items (e.g. new test not in ITR response).
        skippable_items: set[t.Union[TestRef, SuiteRef]] = set()

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(skipping_enabled=True, skippable_items=skippable_items),
            ),
            setup_standard_mocks(),
        ):
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-v", "-s")

        assert result.ret == 0
        result.assertoutcome(passed=1)

        test_event = event_capture.event_by_test_name("test_has_unskippable_marker_but_not_skippable")
        assert test_event["content"]["meta"]["test.status"] == "pass"
        # Must NOT have unskippable when test is not in skippable_items (is_skippable_test returns False).
        assert test_event["content"]["meta"].get("test.itr.unskippable") is None
        assert test_event["content"]["meta"].get("test.itr.forced_run") is None

    def test_itr_one_unskippable_test(self, pytester: Pytester, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that IntelligentTestRunner skips tests marked as skippable."""
        monkeypatch.setenv("_DD_CIVISIBILITY_SEND_DESELECTS", "1")

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

        skippable_items: set[t.Union[TestRef, SuiteRef]] = {
            # Mark one test as skippable.
            TestRef(SuiteRef(ModuleRef(""), "test_foo.py"), "test_should_be_skipped"),
            TestRef(SuiteRef(ModuleRef(""), "test_foo.py"), "test_unskippable"),
        }

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(skipping_enabled=True, skippable_items=skippable_items),
            ),
            setup_standard_mocks(),
        ):
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-v", "-s")

        # Check that tests completed with failure (1 test failed).
        assert result.ret == 1

        # The ITR-skippable test is deselected at collection time, not skip-marked, so pytest's own
        # outcome counters don't see it — only the failed and passed tests are counted here.
        result.assertoutcome(passed=1, failed=1)

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
        assert session["content"]["meta"]["test.itr.tests_skipping.enabled"] == "true"
        assert session["content"]["meta"]["test.itr.tests_skipping.tests_skipped"] == "true"
        assert session["content"]["meta"]["_dd.ci.itr.tests_skipped"] == "true"
        assert session["content"]["meta"]["test.itr.tests_skipping.type"] == "test"
        assert session["content"]["metrics"]["test.itr.tests_skipping.count"] == 1

    @pytest.mark.skipif("slipcover" in sys.modules, reason="slipcover is incompatible with ITR code coverage")
    @pytest.mark.skipif(sys.version_info >= (3, 14), reason="ITR code coverage currently not supported in Python 3.14")
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
        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(coverage_enabled=True),
            ),
            setup_standard_mocks(),
        ):
            with patch.object(TestCoverageWriter, "put_event") as put_event_mock:
                pytester.inline_run("--ddtrace", "-v", "-s")

        coverage_events = [args[0] for args, kwargs in put_event_mock.call_args_list]
        covered_files = set(f["filename"] for f in coverage_events[0]["files"])
        assert covered_files == {"/test_foo.py", "/lib_constants.py"}

    def test_itr_deselect_test_level(self, pytester: Pytester, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test-level ITR skip (the default): skippable test is deselected, not skip-marked."""
        monkeypatch.setenv("_DD_CIVISIBILITY_SEND_DESELECTS", "1")

        pytester.makepyfile(
            test_foo="""
            def test_should_be_deselected():
                assert False  # would fail if it ran

            def test_should_run():
                assert True
            """
        )

        skippable_items: set[t.Union[TestRef, SuiteRef]] = {
            TestRef(SuiteRef(ModuleRef(""), "test_foo.py"), "test_should_be_deselected"),
        }

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(skipping_enabled=True, skippable_items=skippable_items),
            ),
            setup_standard_mocks(),
        ):
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-v", "-s")

        assert result.ret == 0
        # Deselected test is never skipped via skip-marker, so only 1 test ran.
        result.assertoutcome(passed=1)

        # 2 test events (deselected + run) + 1 suite + 1 module + 1 session = 5.
        # The deselected test still gets a synthetic skip event, mirroring suite-level ITR.
        assert len(list(event_capture.events())) == 5

        deselected_test = event_capture.event_by_test_name("test_should_be_deselected")
        assert deselected_test["content"]["meta"]["test.status"] == "skip"
        assert deselected_test["content"]["meta"]["test.skipped_by_itr"] == "true"
        assert deselected_test["content"]["meta"]["test.skip_reason"] == "Skipped by Datadog Intelligent Test Runner"

        [session] = event_capture.events_by_type("test_session_end")
        assert session["content"]["meta"]["test.itr.tests_skipping.type"] == "test"
        assert session["content"]["metrics"]["test.itr.tests_skipping.count"] == 1
        assert session["content"]["meta"]["test.itr.tests_skipping.tests_skipped"] == "true"
        assert session["content"]["meta"]["_dd.ci.itr.tests_skipped"] == "true"

    def test_itr_deselect_test_level_attempt_to_fix_not_deselected(
        self, pytester: Pytester, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test-level ITR skip: a skippable test that is also attempt_to_fix must NOT be deselected.

        Mirrors test_itr_one_unskippable_test, but for the attempt_to_fix exemption in
        pytest_collection_modifyitems's deselect branch rather than the unskippable-marker one.
        """
        monkeypatch.setenv("_DD_CIVISIBILITY_SEND_DESELECTS", "1")

        pytester.makepyfile(
            test_foo="""
            def test_should_be_deselected():
                assert False  # would fail if it ran

            def test_attempt_to_fix():
                assert True

            def test_should_run():
                assert True
            """
        )

        deselected_ref = TestRef(SuiteRef(ModuleRef(""), "test_foo.py"), "test_should_be_deselected")
        atf_ref = TestRef(SuiteRef(ModuleRef(""), "test_foo.py"), "test_attempt_to_fix")

        skippable_items: set[t.Union[TestRef, SuiteRef]] = {deselected_ref, atf_ref}
        known_tests: set[TestRef] = {deselected_ref, atf_ref}
        test_management_properties = {atf_ref: TestProperties(attempt_to_fix=True)}

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(
                    skipping_enabled=True,
                    skippable_items=skippable_items,
                    test_management_enabled=True,
                    known_tests_enabled=True,
                    known_tests=known_tests,
                    test_management_properties=test_management_properties,
                ),
            ),
            setup_standard_mocks(),
        ):
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-v", "-s")

        assert result.ret == 0

        # The attempt_to_fix test is not deselected, so it actually executes — and since it always
        # passes, ATF retries it the full default 20 times (see test_atf_passing_test_retried_fully:
        # 1 initial + 20 retries = 21 events). pytest's own outcome counters report retries as "rerun",
        # not "passed", so we assert via events instead of result.assertoutcome.
        atf_events = list(event_capture.events_by_test_name("test_attempt_to_fix"))
        assert len(atf_events) == 21
        for atf_test in atf_events:
            assert atf_test["content"]["meta"]["test.status"] == "pass"
            assert atf_test["content"]["meta"].get("test.skipped_by_itr") is None
            assert atf_test["content"]["meta"].get("test.skip_reason") is None

        run_test = event_capture.event_by_test_name("test_should_run")
        assert run_test["content"]["meta"]["test.status"] == "pass"

        deselected_test = event_capture.event_by_test_name("test_should_be_deselected")
        assert deselected_test["content"]["meta"]["test.status"] == "skip"
        assert deselected_test["content"]["meta"]["test.skipped_by_itr"] == "true"

        [session] = event_capture.events_by_type("test_session_end")
        assert session["content"]["metrics"]["test.itr.tests_skipping.count"] == 1

    def test_itr_deselect_test_level_whole_suite_deselected(
        self, pytester: Pytester, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When every test in a suite is deselected, the suite/module are finished synthetically.

        No test in this suite ever reaches `pytest_runtest_protocol_wrapper`, so the suite/module
        lifecycle can't be closed by the normal runtime path — `_emit_itr_deselected_test_events`
        must do it explicitly, mirroring `_emit_itr_ignored_suite_events` for suite-level ITR.
        """
        monkeypatch.setenv("_DD_CIVISIBILITY_SEND_DESELECTS", "1")

        pytester.makepyfile(
            test_all_deselected="""
            def test_one():
                assert False  # would fail if it ran

            def test_two():
                assert False  # would fail if it ran
            """,
            test_running="""
            def test_passes():
                assert True
            """,
        )

        skippable_items: set[t.Union[TestRef, SuiteRef]] = {
            TestRef(SuiteRef(ModuleRef(""), "test_all_deselected.py"), "test_one"),
            TestRef(SuiteRef(ModuleRef(""), "test_all_deselected.py"), "test_two"),
        }

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(skipping_enabled=True, skippable_items=skippable_items),
            ),
            setup_standard_mocks(),
        ):
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-v", "-s")

        assert result.ret == 0
        result.assertoutcome(passed=1)

        suite_events = list(event_capture.events_by_type("test_suite_end"))
        assert len(suite_events) == 2

        deselected_suite = next(
            e for e in suite_events if e["content"]["meta"]["test.suite"] == "test_all_deselected.py"
        )
        assert deselected_suite["content"]["meta"]["test.status"] == "skip"

        for test_name in ("test_one", "test_two"):
            deselected_test = event_capture.event_by_test_name(test_name)
            assert deselected_test["content"]["meta"]["test.status"] == "skip"
            assert deselected_test["content"]["meta"]["test.skipped_by_itr"] == "true"

        [session] = event_capture.events_by_type("test_session_end")
        assert session["content"]["metrics"]["test.itr.tests_skipping.count"] == 2

    def test_itr_suite_level_emits_skip_events(self, pytester: Pytester, monkeypatch: pytest.MonkeyPatch) -> None:
        """Suite-level ITR: ignored file gets a test_suite_end with status=skip, no test events inside."""
        pytester.makepyfile(
            test_skippable="""
            def test_inside_skipped_suite():
                assert False  # would fail if it ran
            """,
            test_running="""
            def test_passes():
                assert True
            """,
        )

        skippable_items: set[t.Union[TestRef, SuiteRef]] = {
            SuiteRef(ModuleRef(""), "test_skippable.py"),
        }

        monkeypatch.setenv("_DD_CIVISIBILITY_ITR_SUITE_MODE", "1")

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(skipping_enabled=True, skippable_items=skippable_items),
            ),
            setup_standard_mocks(workspace_path=str(pytester.path)),
        ):
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-v", "-s")

        assert result.ret == 0
        # Only test_running.py::test_passes ran; test_skippable.py was ignored before import.
        result.assertoutcome(passed=1)

        all_events = list(event_capture.events())
        # 1 test + 2 suites + 1 module + 1 session = 5 (no test events for the skipped suite)
        assert len(all_events) == 5

        suite_events = list(event_capture.events_by_type("test_suite_end"))
        assert len(suite_events) == 2

        skipped_suite = next(e for e in suite_events if e["content"]["meta"]["test.suite"] == "test_skippable.py")
        assert skipped_suite["content"]["meta"]["test.status"] == "skip"
        assert skipped_suite["content"]["meta"]["test.skipped_by_itr"] == "true"

        running_suite = next(e for e in suite_events if e["content"]["meta"]["test.suite"] == "test_running.py")
        assert running_suite["content"]["meta"]["test.status"] == "pass"
        assert running_suite["content"]["meta"].get("test.skipped_by_itr") is None

        [session] = event_capture.events_by_type("test_session_end")
        assert session["content"]["meta"]["test.itr.tests_skipping.type"] == "suite"
        assert session["content"]["metrics"]["test.itr.tests_skipping.count"] == 1
        assert session["content"]["meta"]["test.itr.tests_skipping.tests_skipped"] == "true"
        assert session["content"]["meta"]["_dd.ci.itr.tests_skipped"] == "true"

    def test_itr_suite_level_unskippable_file_runs_normally(
        self, pytester: Pytester, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Suite-level ITR: a file with datadog_itr_unskippable is NOT ignored and its tests run."""
        pytester.makepyfile(
            test_unskippable="""
            import pytest

            @pytest.mark.skipif(False, reason='datadog_itr_unskippable')
            def test_forced_run():
                assert True
            """,
        )

        skippable_items: set[t.Union[TestRef, SuiteRef]] = {
            SuiteRef(ModuleRef(""), "test_unskippable.py"),
        }

        monkeypatch.setenv("_DD_CIVISIBILITY_ITR_SUITE_MODE", "1")

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(skipping_enabled=True, skippable_items=skippable_items),
            ),
            setup_standard_mocks(workspace_path=str(pytester.path)),
        ):
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-v", "-s")

        assert result.ret == 0
        # The unskippable file was not ignored — its test ran and passed.
        result.assertoutcome(passed=1)

        # test event present (file was collected, not ignored)
        test_event = event_capture.event_by_test_name("test_forced_run")
        assert test_event["content"]["meta"]["test.status"] == "pass"

        # No ITR-skip events emitted (the suite ran, it wasn't skipped)
        [session] = event_capture.events_by_type("test_session_end")
        assert session["content"]["metrics"].get("test.itr.tests_skipping.count") == 0
        assert session["content"]["meta"].get("test.itr.tests_skipping.tests_skipped") == "false"

    @pytest.mark.skipif("slipcover" in sys.modules, reason="slipcover is incompatible with ITR code coverage")
    @pytest.mark.skipif(sys.version_info >= (3, 14), reason="ITR code coverage currently not supported in Python 3.14")
    def test_itr_suite_level_coverage_uses_suite_coverage(
        self, pytester: Pytester, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Suite mode: coverage events carry test_suite_id but no span_id."""
        pytester.makepyfile(
            lib_answer="""
            ANSWER = 42
            """,
            test_foo="""
            from lib_answer import ANSWER

            def test_answer():
                assert ANSWER == 42
            """,
        )

        monkeypatch.setenv("_DD_CIVISIBILITY_ITR_SUITE_MODE", "1")

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(coverage_enabled=True),
            ),
            setup_standard_mocks(workspace_path=str(pytester.path)),
        ):
            with patch.object(TestCoverageWriter, "put_event") as put_event_mock:
                pytester.inline_run("--ddtrace", "-v", "-s")

        coverage_events = [args[0] for args, kwargs in put_event_mock.call_args_list]
        assert len(coverage_events) == 1
        event = coverage_events[0]
        # Suite-level coverage has test_suite_id but no span_id.
        assert "test_suite_id" in event
        assert "span_id" not in event
        assert "test_session_id" in event

    @pytest.mark.skipif("slipcover" in sys.modules, reason="slipcover is incompatible with ITR code coverage")
    @pytest.mark.skipif(sys.version_info >= (3, 14), reason="ITR code coverage currently not supported in Python 3.14")
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
        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(coverage_enabled=False),
            ),
            setup_standard_mocks(),
        ):
            with patch.object(TestCoverageWriter, "put_event") as put_event_mock:
                pytester.inline_run("--ddtrace", "-v", "-s")

        coverage_events = [args[0] for args, kwargs in put_event_mock.call_args_list]
        assert coverage_events == []
