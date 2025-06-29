from typing import Any
from typing import Dict
from typing import Optional

from ddtrace.ext import test
from ddtrace.ext.test_visibility import ITR_SKIPPING_LEVEL
from ddtrace.ext.test_visibility._test_visibility_base import TestModuleId
from ddtrace.ext.test_visibility.status import TestStatus
from ddtrace.internal.ci_visibility.api._base import TestVisibilityParentItem
from ddtrace.internal.ci_visibility.api._base import TestVisibilitySessionSettings
from ddtrace.internal.ci_visibility.api._module import TestVisibilityModule
from ddtrace.internal.ci_visibility.constants import SESSION_ID
from ddtrace.internal.ci_visibility.constants import SESSION_TYPE
from ddtrace.internal.ci_visibility.constants import SUITE
from ddtrace.internal.ci_visibility.constants import TEST
from ddtrace.internal.ci_visibility.constants import TEST_EFD_ABORT_REASON
from ddtrace.internal.ci_visibility.constants import TEST_EFD_ENABLED
from ddtrace.internal.ci_visibility.constants import TEST_MANAGEMENT_ENABLED
from ddtrace.internal.ci_visibility.telemetry.constants import EVENT_TYPES
from ddtrace.internal.ci_visibility.telemetry.events import record_event_created
from ddtrace.internal.ci_visibility.telemetry.events import record_event_finished
from ddtrace.internal.logger import get_logger
from ddtrace.internal.test_visibility._efd_mixins import EFDTestStatus


log = get_logger(__name__)


class TestVisibilitySession(TestVisibilityParentItem[TestModuleId, TestVisibilityModule]):
    """This class represents a Test session and is the top level in the hierarchy of Test visibility items.

    It does not access its skip-level descendents directly as they are expected to be managed through their own parent
    instances.
    """

    _event_type = SESSION_TYPE
    _event_type_metric_name = EVENT_TYPES.SESSION

    def __init__(
        self,
        session_settings: TestVisibilitySessionSettings,
        initial_tags: Optional[Dict[str, str]] = None,
    ) -> None:
        log.debug("Initializing Test Visibility session")
        super().__init__(
            "test_visibility_session", session_settings, session_settings.session_operation_name, initial_tags
        )
        self._test_command = self._session_settings.test_command

        self._efd_abort_reason: Optional[str] = None
        self._efd_is_faulty_session: Optional[bool] = None
        self._efd_has_efd_failed_tests: bool = False

        self._atr_total_retries: int = 0

        self.set_tag(test.ITR_TEST_CODE_COVERAGE_ENABLED, session_settings.coverage_enabled)

    def _get_hierarchy_tags(self) -> Dict[str, Any]:
        return {
            SESSION_ID: str(self.get_span_id()),
        }

    def get_session_settings(self) -> TestVisibilitySessionSettings:
        return self._session_settings

    def _set_efd_tags(self):
        if self._session_settings.efd_settings.enabled:
            self.set_tag(TEST_EFD_ENABLED, True)
        if self._efd_abort_reason is not None:
            # Allow any set abort reason to override faulty session abort reason
            self.set_tag(TEST_EFD_ABORT_REASON, self._efd_abort_reason)
        elif self.efd_is_faulty_session():
            self.set_tag(TEST_EFD_ABORT_REASON, "faulty")

    def _set_test_management_tags(self):
        self.set_tag(TEST_MANAGEMENT_ENABLED, True)

    def _set_itr_tags(self, itr_enabled: bool) -> None:
        """Set session-level tags based in ITR enablement status"""
        super()._set_itr_tags(itr_enabled)

        self.set_tag(test.ITR_TEST_SKIPPING_ENABLED, self._session_settings.itr_test_skipping_enabled)
        if itr_enabled:
            skipping_level = (
                TEST if self._session_settings.itr_test_skipping_level == ITR_SKIPPING_LEVEL.TEST else SUITE
            )
            self.set_tag(test.ITR_TEST_SKIPPING_TYPE, skipping_level)
            self.set_tag(test.ITR_DD_CI_ITR_TESTS_SKIPPED, self._itr_skipped_count > 0)

    def _telemetry_record_event_created(self):
        record_event_created(
            event_type=self._event_type_metric_name,
            test_framework=self._session_settings.test_framework_metric_name,
            has_codeowners=self._codeowners is not None,
            is_unsupported_ci=self._session_settings.is_unsupported_ci,
        )

    def _telemetry_record_event_finished(self):
        record_event_finished(
            event_type=self._event_type_metric_name,
            test_framework=self._session_settings.test_framework_metric_name,
            has_codeowners=self._codeowners is not None,
            is_unsupported_ci=self._session_settings.is_unsupported_ci,
            early_flake_detection_abort_reason="faulty" if self.efd_is_faulty_session() else None,
        )

    def add_coverage_data(self, *args, **kwargs):
        raise NotImplementedError("Coverage data cannot be added to session.")

    def set_skipped_count(self, skipped_count: int):
        self._itr_skipped_count = skipped_count
        self._set_itr_tags(self._session_settings.itr_test_skipping_enabled)

    def set_covered_lines_pct(self, coverage_pct: float):
        self.set_tag(test.TEST_LINES_PCT, coverage_pct)

    def get_session(self):
        return self

    #
    # EFD (Early Flake Detection) functionality
    #
    def efd_is_enabled(self):
        return self._session_settings.efd_settings.enabled

    def set_efd_abort_reason(self, abort_reason: str):
        self._efd_abort_reason = abort_reason

    def efd_is_faulty_session(self):
        """A session is considered "EFD faulty" if the percentage of tests considered new is greater than the
        given threshold, and the total number of news tests exceeds the threshold.

        NOTE: this behavior is cached on the assumption that this method will only be called once
        """
        if self._efd_is_faulty_session is not None:
            return self._efd_is_faulty_session

        if self._session_settings.efd_settings.enabled is False:
            return False

        total_tests_count = 0
        new_tests_count = 0
        for _module in self._children.values():
            for _suite in _module._children.values():
                for _test in _suite._children.values():
                    total_tests_count += 1
                    if _test.is_new():
                        new_tests_count += 1

        if new_tests_count <= self._session_settings.efd_settings.faulty_session_threshold:
            return False

        new_tests_pct = 100 * (new_tests_count / total_tests_count)

        self._efd_is_faulty_session = new_tests_pct > self._session_settings.efd_settings.faulty_session_threshold

        return self._efd_is_faulty_session

    def efd_has_failed_tests(self):
        if (not self._session_settings.efd_settings.enabled) or self.efd_is_faulty_session():
            return False

        for _module in self._children.values():
            for _suite in _module._children.values():
                for _test in _suite._children.values():
                    if _test.efd_has_retries() and _test.efd_get_final_status() == EFDTestStatus.ALL_FAIL:
                        return True
        return False

    #
    # ATR (Auto Test Retries , AKA Flaky Test Retries) functionality
    #
    def atr_is_enabled(self) -> bool:
        return self._session_settings.atr_settings.enabled

    def atr_max_retries_reached(self) -> bool:
        return self._atr_total_retries >= self._session_settings.atr_settings.max_session_total_retries

    def _atr_count_retry(self):
        self._atr_total_retries += 1

    def atr_has_failed_tests(self):
        if not self._session_settings.atr_settings.enabled:
            return False

        for _module in self._children.values():
            for _suite in _module._children.values():
                for _test in _suite._children.values():
                    if _test.is_quarantined():
                        continue
                    if _test.atr_has_retries() and _test.atr_get_final_status() == TestStatus.FAIL:
                        return True
        return False

    def attempt_to_fix_has_failed_tests(self):
        if not self._session_settings.test_management_settings.enabled:
            return False

        for _module in self._children.values():
            for _suite in _module._children.values():
                for _test in _suite._children.values():
                    if _test.is_quarantined() or _test.is_disabled():
                        continue
                    if _test.is_attempt_to_fix() and _test.attempt_to_fix_get_final_status() == TestStatus.FAIL:
                        return True
        return False
