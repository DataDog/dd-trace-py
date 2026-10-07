from __future__ import annotations

from dataclasses import replace
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

    @pytest.mark.parametrize(
        "marker,tia_count",
        [
            ('skip(reason="framework")', 0),
            ('skipif(True, reason="framework")', 0),
            ('skipif(condition=True, reason="framework")', 0),
            ('skipif("True", reason="framework")', 0),
            ('skipif(False, True, reason="framework")', 0),
            ('skipif(False, reason="framework")', 1),
        ],
    )
    def test_suite_reporting_excludes_framework_skip_markers(
        self, pytester: Pytester, marker: str, tia_count: int
    ) -> None:
        pytester.makepyfile(
            test_foo=f"""
            import pytest

            @pytest.mark.{marker}
            def test_skip():
                assert False
            """
        )
        skippable = {TestRef(SuiteRef(ModuleRef(""), "test_foo.py"), "test_skip")}
        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(
                    skipping_enabled=True, coverage_enabled=False, skippable_items=skippable
                ),
            ),
            setup_standard_mocks(),
            EventCapture.capture() as capture,
        ):
            result = pytester.inline_run("--ddtrace")
        result.assertoutcome(skipped=1)
        [suite] = capture.events_by_type("test_suite_end")
        assert suite["content"]["metrics"]["test.itr.tests_skipping.count"] == tia_count
        assert suite["content"]["meta"]["_dd.ci.itr.tests_skipped"] == ("true" if tia_count else "false")
        [session] = capture.events_by_type("test_session_end")
        assert session["content"]["metrics"]["test.itr.tests_skipping.count"] == tia_count
        event = capture.event_by_test_name("test_skip")
        assert event["content"]["meta"].get("test.skipped_by_itr") == ("true" if tia_count else None)
        assert event["content"]["meta"]["test.skip_reason"] == (
            "Skipped by Datadog Intelligent Test Runner" if tia_count else "framework"
        )

    @pytest.mark.parametrize("condition_kind", ["string", "object"])
    @pytest.mark.parametrize("first_value", [True, False])
    def test_stateful_skipif_condition_evaluated_once(
        self, pytester: Pytester, condition_kind: str, first_value: bool
    ) -> None:
        condition = '"condition()"' if condition_kind == "string" else "Condition()"
        pytester.makepyfile(
            test_foo=f"""
            from pathlib import Path
            import pytest

            calls = 0

            def condition():
                global calls
                calls += 1
                Path("condition_calls").write_text(str(calls))
                return {first_value!r} if calls == 1 else {not first_value!r}

            class Condition:
                def __bool__(self):
                    return condition()

            @pytest.mark.skipif({condition}, reason="framework")
            def test_skip():
                assert False
            """
        )
        skippable = {TestRef(SuiteRef(ModuleRef(""), "test_foo.py"), "test_skip")}
        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(skipping_enabled=True, skippable_items=skippable),
            ),
            setup_standard_mocks(),
            EventCapture.capture() as capture,
        ):
            result = pytester.inline_run("--ddtrace")
        result.assertoutcome(skipped=1)
        assert (pytester.path / "condition_calls").read_text() == "1"
        [suite] = capture.events_by_type("test_suite_end")
        assert suite["content"]["metrics"]["test.itr.tests_skipping.count"] == int(not first_value)
        event = capture.event_by_test_name("test_skip")
        assert event["content"]["meta"]["test.skip_reason"] == (
            "framework" if first_value else "Skipped by Datadog Intelligent Test Runner"
        )

    def test_tia_skip_does_not_run_test_fixtures(self, pytester: Pytester) -> None:
        pytester.makepyfile(
            test_foo="""
            import pytest

            @pytest.fixture
            def failing_setup():
                raise AssertionError("TIA-skipped tests must not run fixtures")

            def test_skip(failing_setup):
                assert False
            """
        )
        skippable = {TestRef(SuiteRef(ModuleRef(""), "test_foo.py"), "test_skip")}
        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(skipping_enabled=True, skippable_items=skippable),
            ),
            setup_standard_mocks(),
            EventCapture.capture() as capture,
        ):
            result = pytester.inline_run("--ddtrace")
        result.assertoutcome(skipped=1)
        [suite] = capture.events_by_type("test_suite_end")
        assert suite["content"]["metrics"]["test.itr.tests_skipping.count"] == 1

    @pytest.mark.parametrize("other_outcome", ["framework", "disabled", "attempt_to_fix"])
    def test_suite_reporting_requires_every_child_to_be_tia_skipped(
        self, pytester: Pytester, monkeypatch: pytest.MonkeyPatch, other_outcome: str
    ) -> None:
        marker = '@pytest.mark.skip(reason="framework")' if other_outcome == "framework" else ""
        pytester.makepyfile(
            test_foo=f"""
            import pytest

            def test_tia():
                assert False

            {marker}
            def test_other():
                assert True
            """
        )
        suite_ref = SuiteRef(ModuleRef(""), "test_foo.py")
        properties = {}
        if other_outcome == "disabled":
            properties[TestRef(suite_ref, "test_other")] = TestProperties(disabled=True)
        elif other_outcome == "attempt_to_fix":
            properties[TestRef(suite_ref, "test_other")] = TestProperties(attempt_to_fix=True)
        monkeypatch.setenv("_DD_CIVISIBILITY_ITR_SUITE_MODE", "1")
        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(
                    skipping_enabled=True,
                    skippable_items={suite_ref},
                    test_management_enabled=other_outcome != "framework",
                    test_management_properties=properties,
                ),
            ),
            setup_standard_mocks(workspace_path=str(pytester.path)),
            EventCapture.capture() as capture,
        ):
            result = pytester.inline_run("--ddtrace", "test_foo.py")
        assert result.ret == 0
        [suite] = capture.events_by_type("test_suite_end")
        assert suite["content"]["metrics"]["test.itr.tests_skipping.count"] == 0
        assert suite["content"]["meta"]["_dd.ci.itr.tests_skipped"] == "false"
        assert suite["content"]["meta"].get("test.skipped_by_itr") is None
        assert suite["content"]["meta"]["test.status"] == ("pass" if other_outcome == "attempt_to_fix" else "skip")

    @pytest.mark.parametrize("selection", ["test_skippable.py", "test_skippable.py::test_one"])
    def test_suite_reporting_for_explicitly_selected_skippable_suite(
        self, pytester: Pytester, monkeypatch: pytest.MonkeyPatch, selection: str
    ) -> None:
        pytester.makepyfile(
            test_skippable="""
            def test_one():
                assert False

            def test_two():
                assert False
            """
        )
        monkeypatch.setenv("_DD_CIVISIBILITY_ITR_SUITE_MODE", "1")
        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(
                    skipping_enabled=True,
                    coverage_enabled=False,
                    skippable_items={SuiteRef(ModuleRef(""), "test_skippable.py")},
                ),
            ),
            setup_standard_mocks(workspace_path=str(pytester.path)),
            EventCapture.capture() as capture,
        ):
            result = pytester.inline_run("--ddtrace", selection)
        result.assertoutcome(skipped=1 if "::" in selection else 2)
        [suite] = capture.events_by_type("test_suite_end")
        assert suite["content"]["metrics"]["test.itr.tests_skipping.count"] == 1
        assert suite["content"]["meta"]["_dd.ci.itr.tests_skipped"] == "true"
        assert suite["content"]["meta"]["test.skipped_by_itr"] == "true"

    def test_suite_reporting_excludes_disabled_tests(self, pytester: Pytester) -> None:
        pytester.makepyfile(
            test_foo="""
            def test_disabled():
                assert False

            def test_tia():
                assert False
            """
        )
        suite_ref = SuiteRef(ModuleRef(""), "test_foo.py")
        disabled_ref = TestRef(suite_ref, "test_disabled")
        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(
                    skipping_enabled=True,
                    coverage_enabled=False,
                    skippable_items={disabled_ref, TestRef(suite_ref, "test_tia")},
                    test_management_enabled=True,
                    test_management_properties={disabled_ref: TestProperties(disabled=True)},
                ),
            ),
            setup_standard_mocks(),
            EventCapture.capture() as capture,
        ):
            result = pytester.inline_run("--ddtrace")
        result.assertoutcome(skipped=2)
        [suite] = capture.events_by_type("test_suite_end")
        assert suite["content"]["metrics"]["test.itr.tests_skipping.count"] == 1
        assert suite["content"]["meta"]["_dd.ci.itr.tests_skipped"] == "true"
        [session] = capture.events_by_type("test_session_end")
        assert session["content"]["metrics"]["test.itr.tests_skipping.count"] == 1
        disabled_event = capture.event_by_test_name("test_disabled")
        assert disabled_event["content"]["meta"].get("test.skipped_by_itr") is None
        assert disabled_event["content"]["meta"]["test.test_management.is_test_disabled"] == "true"
        assert disabled_event["content"]["meta"]["test.skip_reason"] == "Flaky test is disabled by Datadog"

    def test_suite_reporting_with_tia_enabled_and_skipping_disabled(self, pytester: Pytester) -> None:
        pytester.makepyfile(
            test_foo="""
            import pytest

            def test_pass():
                assert True

            @pytest.mark.skip(reason="framework")
            def test_skip():
                assert False
        """
        )
        client = mock_api_client_settings(skipping_enabled=False, coverage_enabled=False)
        client.get_settings.return_value = replace(client.get_settings.return_value, itr_enabled=True)
        with patch("ddtrace.testing.internal.session_manager.APIClient", return_value=client), setup_standard_mocks():
            with EventCapture.capture() as capture:
                result = pytester.inline_run("--ddtrace")
        result.assertoutcome(passed=1, skipped=1)
        [suite] = capture.events_by_type("test_suite_end")
        assert suite["content"]["metrics"]["test.itr.tests_skipping.count"] == 0
        assert suite["content"]["meta"]["_dd.ci.itr.tests_skipped"] == "false"

    def test_suite_counting_failure_does_not_fail_tests(self, pytester: Pytester) -> None:
        pytester.makepyfile(
            test_foo="""
            def test_tia():
                assert False

            def test_pass():
                assert True
        """
        )
        skippable = {TestRef(SuiteRef(ModuleRef(""), "test_foo.py"), "test_tia")}
        client = mock_api_client_settings(skipping_enabled=True, skippable_items=skippable)
        with (
            patch("ddtrace.testing.internal.session_manager.APIClient", return_value=client),
            patch(
                "ddtrace.testing.internal.test_data.TestSuite.count_itr_skipped", side_effect=RuntimeError("counter")
            ),
            setup_standard_mocks(),
        ):
            with EventCapture.capture() as capture:
                result = pytester.inline_run("--ddtrace")
        result.assertoutcome(passed=1, skipped=1)
        [session] = capture.events_by_type("test_session_end")
        assert session["content"]["metrics"]["test.itr.tests_skipping.count"] == 1

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
        assert session["content"]["meta"]["test.itr.tests_skipping.enabled"] == "true"
        assert session["content"]["meta"]["test.itr.tests_skipping.tests_skipped"] == "true"
        assert session["content"]["meta"]["_dd.ci.itr.tests_skipped"] == "true"
        assert session["content"]["meta"]["test.itr.tests_skipping.type"] == "test"
        assert session["content"]["metrics"]["test.itr.tests_skipping.count"] == 1

        [suite] = event_capture.events_by_type("test_suite_end")
        assert suite["content"]["metrics"]["test.itr.tests_skipping.count"] == 1
        assert suite["content"]["meta"]["_dd.ci.itr.tests_skipped"] == "true"

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

        [suite] = event_capture.events_by_type("test_suite_end")
        assert "test.itr.tests_skipping.count" not in suite["content"]["metrics"]
        assert "_dd.ci.itr.tests_skipped" not in suite["content"]["meta"]

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
        assert skipped_suite["content"]["metrics"]["test.itr.tests_skipping.count"] == 1
        assert skipped_suite["content"]["meta"]["_dd.ci.itr.tests_skipped"] == "true"

        running_suite = next(e for e in suite_events if e["content"]["meta"]["test.suite"] == "test_running.py")
        assert running_suite["content"]["meta"]["test.status"] == "pass"
        assert running_suite["content"]["meta"].get("test.skipped_by_itr") is None
        assert running_suite["content"]["metrics"]["test.itr.tests_skipping.count"] == 0
        assert running_suite["content"]["meta"]["_dd.ci.itr.tests_skipped"] == "false"

        [session] = event_capture.events_by_type("test_session_end")
        assert session["content"]["meta"]["test.itr.tests_skipping.type"] == "suite"
        assert session["content"]["metrics"]["test.itr.tests_skipping.count"] == 1
        assert session["content"]["meta"]["test.itr.tests_skipping.tests_skipped"] == "true"
        assert session["content"]["meta"]["_dd.ci.itr.tests_skipped"] == "true"

    @pytest.mark.parametrize("selection", [None, "test_unskippable.py"])
    def test_itr_suite_level_unskippable_file_runs_normally(
        self, pytester: Pytester, monkeypatch: pytest.MonkeyPatch, selection: t.Optional[str]
    ) -> None:
        """Suite-level ITR: a file with datadog_itr_unskippable is NOT ignored and its tests run."""
        pytester.makepyfile(
            test_unskippable="""
            import pytest

            @pytest.mark.skipif(False, reason='datadog_itr_unskippable')
            def test_forced_run():
                assert True

            def test_sibling_also_runs():
                assert False
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
                result = pytester.inline_run("--ddtrace", "-v", "-s", *([selection] if selection else []))

        assert result.ret == 1
        # The unskippable file was not ignored, and the whole suite ran instead of skipping siblings test-by-test.
        result.assertoutcome(passed=1, failed=1)

        # test event present (file was collected, not ignored)
        test_event = event_capture.event_by_test_name("test_forced_run")
        assert test_event["content"]["meta"]["test.status"] == "pass"
        assert test_event["content"]["meta"]["test.itr.unskippable"] == "true"
        assert test_event["content"]["meta"]["test.itr.forced_run"] == "true"

        sibling_event = event_capture.event_by_test_name("test_sibling_also_runs")
        assert sibling_event["content"]["meta"]["test.status"] == "fail"
        assert sibling_event["content"]["meta"]["test.itr.forced_run"] == "true"
        assert sibling_event["content"]["meta"].get("test.skipped_by_itr") is None

        # No ITR-skip events emitted (the suite ran, it wasn't skipped)
        [session] = event_capture.events_by_type("test_session_end")
        assert session["content"]["metrics"].get("test.itr.tests_skipping.count") == 0
        assert session["content"]["meta"].get("test.itr.tests_skipping.tests_skipped") == "false"

        [suite] = event_capture.events_by_type("test_suite_end")
        assert suite["content"]["metrics"]["test.itr.tests_skipping.count"] == 0
        assert suite["content"]["meta"]["_dd.ci.itr.tests_skipped"] == "false"

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

    def test_itr_coverage_enabled_with_coverage_report_upload(self, pytester: Pytester) -> None:
        """Regression test: setup_coverage_collection() must be called when coverage_enabled=True
        even when coverage_report_upload_enabled=True.

        Both mechanisms can run simultaneously because CollectInContext.__enter__ dynamically
        detects other sys.monitoring tools and disables the DISABLE optimisation + restart_events()
        to avoid corrupting their state.
        """
        pytester.makepyfile(test_placeholder="def test_ok(): pass")

        setup_coverage_calls: list[bool] = []

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(
                    coverage_enabled=True,
                    coverage_report_upload_enabled=True,
                ),
            ),
            setup_standard_mocks(),
            patch(
                "ddtrace.testing.internal.pytest.plugin.setup_coverage_collection",
                side_effect=lambda **kwargs: setup_coverage_calls.append(True),
            ),
        ):
            pytester.inline_run("--ddtrace", "-v", "-s")

        assert len(setup_coverage_calls) == 1, (
            "setup_coverage_collection() must be called when coverage_enabled=True "
            "even when coverage_report_upload_enabled=True; "
            f"was called {len(setup_coverage_calls)} time(s)"
        )
