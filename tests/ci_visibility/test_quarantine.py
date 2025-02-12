from pathlib import Path
from unittest import mock

from ddtrace.ext.test_visibility.api import TestStatus
from ddtrace.internal.ci_visibility._api_client import EarlyFlakeDetectionSettings
from ddtrace.internal.ci_visibility._api_client import TestManagementSettings
from ddtrace.internal.ci_visibility.api._base import TestVisibilitySessionSettings
from ddtrace.internal.ci_visibility.api._session import TestVisibilitySession
from ddtrace.internal.ci_visibility.api._test import TestVisibilityTest
from ddtrace.internal.ci_visibility.telemetry.constants import TEST_FRAMEWORKS
from ddtrace.internal.test_visibility._atr_mixins import AutoTestRetriesSettings
from tests.utils import DummyTracer


class TestCIVisibilityTestQuarantine:
    """Tests that the classes in the CIVisibility API correctly handle quarantine."""

    def _get_session_settings(
        self,
    ) -> TestVisibilitySessionSettings:
        return TestVisibilitySessionSettings(
            tracer=DummyTracer(),
            test_service="qurantine_test_service",
            test_command="qurantine_test_command",
            test_framework="qurantine_test_framework",
            test_framework_metric_name=TEST_FRAMEWORKS.MANUAL,
            test_framework_version="0.0",
            session_operation_name="qurantine_session",
            module_operation_name="qurantine_module",
            suite_operation_name="qurantine_suite",
            test_operation_name="qurantine_test",
            workspace_path=Path().absolute(),
            efd_settings=EarlyFlakeDetectionSettings(enabled=False),
            atr_settings=AutoTestRetriesSettings(enabled=False),
            test_management_settings=TestManagementSettings(enabled=True),
        )

    def test_quarantine_tags_set(self):
        session = TestVisibilitySession(
            session_settings=self._get_session_settings(),
        )

        test = TestVisibilityTest(
            name="quarantine_test_1",
            session_settings=session._session_settings,
            is_quarantined=True,
        )

        with mock.patch.object(test, "get_session", return_value=session):
            session.start()
            test.start()
            test.finish_test(TestStatus.FAIL)
            session.finish()

        assert test._span.get_tag("test.test_management.is_quarantined") == "true"
        assert session._span.get_tag("test.test_management.enabled") == "true"
