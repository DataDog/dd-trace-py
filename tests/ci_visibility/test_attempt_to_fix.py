from pathlib import Path

import pytest

from ddtrace.ext.test_visibility._test_visibility_base import TestId
from ddtrace.ext.test_visibility._test_visibility_base import TestModuleId
from ddtrace.ext.test_visibility._test_visibility_base import TestSuiteId
from ddtrace.ext.test_visibility.api import TestStatus
from ddtrace.internal.ci_visibility._api_client import EarlyFlakeDetectionSettings
from ddtrace.internal.ci_visibility._api_client import TestManagementSettings
from ddtrace.internal.ci_visibility.api._base import TestVisibilitySessionSettings
from ddtrace.internal.ci_visibility.api._module import TestVisibilityModule
from ddtrace.internal.ci_visibility.api._session import TestVisibilitySession
from ddtrace.internal.ci_visibility.api._suite import TestVisibilitySuite
from ddtrace.internal.ci_visibility.api._test import TestVisibilityTest
from ddtrace.internal.ci_visibility.telemetry.constants import TEST_FRAMEWORKS
from ddtrace.internal.test_visibility._atr_mixins import AutoTestRetriesSettings


class TestCIVisibilityTestAttemptToFix:
    """Tests that the classes in the CIVisibility API correctly handle attempt-to-fix."""

    def _get_session_settings(self, tracer) -> TestVisibilitySessionSettings:
        return TestVisibilitySessionSettings(
            tracer=tracer,
            test_service="attempt_to_fix_test_service",
            test_command="attempt_to_fix_test_command",
            test_framework="attempt_to_fix_test_framework",
            test_framework_metric_name=TEST_FRAMEWORKS.MANUAL,
            test_framework_version="0.0",
            session_operation_name="attempt_to_fix_session",
            module_operation_name="attempt_to_fix_module",
            suite_operation_name="attempt_to_fix_suite",
            test_operation_name="attempt_to_fix_test",
            workspace_path=Path().absolute(),
            efd_settings=EarlyFlakeDetectionSettings(enabled=False),
            atr_settings=AutoTestRetriesSettings(enabled=False),
            test_management_settings=TestManagementSettings(enabled=True, attempt_to_fix_retries=1),
        )

    def _make_session_with_test(
        self,
        tracer,
        *,
        is_attempt_to_fix: bool,
        is_quarantined: bool = False,
        is_disabled: bool = False,
    ) -> TestVisibilitySession:
        session_settings = self._get_session_settings(tracer)
        session = TestVisibilitySession(session_settings=session_settings)

        module_id = TestModuleId("module")
        module = TestVisibilityModule(module_id.name, session_settings=session_settings)
        session.add_child(module_id, module)

        suite_id = TestSuiteId(module_id, "suite")
        suite = TestVisibilitySuite(suite_id.name, session_settings=session_settings)
        module.add_child(suite_id, suite)

        test = TestVisibilityTest(
            "test_attempt_to_fix",
            session_settings=session_settings,
            is_attempt_to_fix=is_attempt_to_fix,
            is_quarantined=is_quarantined,
            is_disabled=is_disabled,
        )
        retry = TestVisibilityTest("test_attempt_to_fix", session_settings=session_settings)
        retry.set_status(TestStatus.FAIL)
        test._attempt_to_fix_retries.append(retry)

        assert test.attempt_to_fix_get_final_status() == TestStatus.FAIL

        suite.add_child(TestId(suite_id, test.name), test)
        return session

    @pytest.mark.parametrize(
        "test_properties",
        (
            {"is_attempt_to_fix": True, "is_quarantined": True},
            {"is_attempt_to_fix": True, "is_disabled": True},
        ),
    )
    def test_attempt_to_fix_has_failed_tests_includes_quarantined_and_disabled_tests(self, tracer, test_properties):
        session = self._make_session_with_test(tracer, **test_properties)

        assert session.attempt_to_fix_has_failed_tests() is True

    @pytest.mark.parametrize(
        "test_properties",
        (
            {"is_attempt_to_fix": False, "is_quarantined": True},
            {"is_attempt_to_fix": False, "is_disabled": True},
        ),
    )
    def test_attempt_to_fix_has_failed_tests_ignores_non_attempt_to_fix_tests(self, tracer, test_properties):
        session = self._make_session_with_test(tracer, **test_properties)

        assert session.attempt_to_fix_has_failed_tests() is False
