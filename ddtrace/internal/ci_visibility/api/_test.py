from pathlib import Path
from time import time_ns
from typing import Dict
from typing import List
from typing import Optional
from typing import Union

from ddtrace.ext import test
from ddtrace.ext.test_visibility import ITR_SKIPPING_LEVEL
from ddtrace.ext.test_visibility._item_ids import TestId
from ddtrace.ext.test_visibility.api import TestExcInfo
from ddtrace.ext.test_visibility.api import TestSourceFileInfo
from ddtrace.ext.test_visibility.api import TestStatus
from ddtrace.internal.ci_visibility.api._base import TestVisibilityChildItem
from ddtrace.internal.ci_visibility.api._base import TestVisibilityItemBase
from ddtrace.internal.ci_visibility.api._base import TestVisibilitySessionSettings
from ddtrace.internal.ci_visibility.api._coverage_data import TestVisibilityCoverageData
from ddtrace.internal.ci_visibility.constants import TEST
from ddtrace.internal.ci_visibility.constants import TEST_EFD_ABORT_REASON
from ddtrace.internal.ci_visibility.constants import TEST_IS_NEW
from ddtrace.internal.ci_visibility.constants import TEST_IS_RETRY
from ddtrace.internal.ci_visibility.telemetry.constants import EVENT_TYPES
from ddtrace.internal.ci_visibility.telemetry.events import record_event_created
from ddtrace.internal.ci_visibility.telemetry.events import record_event_finished
from ddtrace.internal.logger import get_logger
from ddtrace.internal.test_visibility._internal_item_ids import InternalTestId
from ddtrace.internal.test_visibility.coverage_lines import CoverageLines


log = get_logger(__name__)

TID = Union[TestId, InternalTestId]


class TestVisibilityTest(TestVisibilityChildItem[TID], TestVisibilityItemBase):
    _event_type = TEST
    _event_type_metric_name = EVENT_TYPES.TEST

    def __init__(
        self,
        name: str,
        session_settings: TestVisibilitySessionSettings,
        parameters: Optional[str] = None,
        codeowners: Optional[List[str]] = None,
        source_file_info: Optional[TestSourceFileInfo] = None,
        initial_tags: Optional[Dict[str, str]] = None,
        is_efd_retry: bool = False,
        resource: Optional[str] = None,
        is_new: bool = False,
    ):
        self._parameters = parameters
        super().__init__(
            name,
            session_settings,
            session_settings.test_operation_name,
            initial_tags,
            resource=resource if resource is not None else name,
        )
        self._codeowners = codeowners
        self._source_file_info = source_file_info
        self._original_test: Optional[TestVisibilityTest] = None
        self._exc_info: Optional[TestExcInfo] = None
        self._coverage_data: TestVisibilityCoverageData = TestVisibilityCoverageData()

        if self._parameters is not None:
            self.set_tag(test.PARAMETERS, parameters)

        self._is_new = is_new

        self._is_efd_retry = is_efd_retry
        self._efd_retries: List[TestVisibilityTest] = []
        self._efd_abort_reason: Optional[str] = None
        self._efd_initial_finish_time_ns: Optional[int] = None

        # Currently unsupported
        self._is_benchmark = None

    def __repr__(self) -> str:
        suite_name = self.parent.name if self.parent is not None else "none"
        module_name = self.parent.parent.name if self.parent is not None and self.parent.parent is not None else "none"
        return "{}(name={}, suite={}, module={}, parameters={}, status={})".format(
            self.__class__.__name__, self.name, suite_name, module_name, self._parameters, self._status
        )

    def _get_hierarchy_tags(self) -> Dict[str, str]:
        return {
            test.NAME: self.name,
        }

    def _set_efd_tags(self) -> None:
        if self._is_efd_retry:
            self.set_tag(TEST_IS_RETRY, self._is_efd_retry)
        if self._efd_abort_reason is not None:
            self.set_tag(TEST_EFD_ABORT_REASON, self._efd_abort_reason)

        # NOTE: The is_new tag is currently only being set in the context of EFD (since that is the only context in
        # which unique tests are fetched).
        if self._is_new:
            self.set_tag(TEST_IS_NEW, self._is_new)

    def _set_span_tags(self) -> None:
        """This handles setting tags that can't be properly stored in self._tags

        - exc_info: because it uses span.set_exc_info()
        """
        if self._span is None:
            return
        if self._exc_info is not None:
            self._span.set_exc_info(self._exc_info.exc_type, self._exc_info.exc_value, self._exc_info.exc_traceback)

    def _telemetry_record_event_created(self):
        record_event_created(
            event_type=self._event_type_metric_name,
            test_framework=self._session_settings.test_framework_metric_name,
            is_benchmark=self._is_benchmark if self._is_benchmark is not None else None,
        )

    def _telemetry_record_event_finished(self):
        record_event_finished(
            event_type=self._event_type_metric_name,
            test_framework=self._session_settings.test_framework_metric_name,
            is_benchmark=self._is_benchmark if self._is_benchmark is not None else None,
            is_new=self._is_new if self._is_new is not None else None,
            early_flake_detection_abort_reason=self._efd_abort_reason,
        )

    def finish_test(
        self,
        status: Optional[TestStatus] = None,
        reason: Optional[str] = None,
        exc_info: Optional[TestExcInfo] = None,
        override_finish_time: Optional[float] = None,
    ) -> None:
        log.debug("Test Visibility: finishing %s, with status: %s, reason: %s", self, status, reason)
        if status is not None:
            self.set_status(status)
        if reason is not None:
            self.set_tag(test.SKIP_REASON, reason)
        if exc_info is not None:
            self._exc_info = exc_info
        super().finish(override_finish_time=override_finish_time)

    def count_itr_skipped(self) -> None:
        """Tests do not count skipping on themselves, so only count on the parent.

        When skipping at the suite level, the counting only happens when suites are finished as ITR-skipped.
        """
        if self._session_settings.itr_test_skipping_level == ITR_SKIPPING_LEVEL.TEST and self.parent is not None:
            self.parent.count_itr_skipped()

    def finish_itr_skipped(self) -> None:
        log.debug("Finishing Test Visibility test %s with ITR skipped", self)
        self.count_itr_skipped()
        self.mark_itr_skipped()
        self.finish_test(TestStatus.SKIP)

    def make_early_flake_retry_from_test(self) -> "TestVisibilityTest":
        if self._parameters is not None:
            raise ValueError("Cannot create an early flake retry from a test with parameters")
        retry_test = self.__class__(
            self.name,
            self._session_settings,
            codeowners=self._codeowners,
            source_file_info=self._source_file_info,
            initial_tags=self._tags,
            is_efd_retry=True,
            is_new=self._is_new,
        )
        retry_test.parent = self.parent

        return retry_test

    def add_coverage_data(self, coverage_data: Dict[Path, CoverageLines]) -> None:
        self._coverage_data.add_covered_files(coverage_data)

    def set_parameters(self, parameters: str) -> None:
        self._parameters = parameters
        self.set_tag(test.PARAMETERS, self._parameters)

    def is_new(self):
        # NOTE: this is a hack because tests with parameters cannot currently be counted as new (due to EFD design
        # decisions)
        return self._is_new and (self._parameters is None)

    # EFD (Early Flake Detection) functionality
    def efd_should_retry(self):
        efd_settings = self._session_settings.efd_settings
        if not efd_settings.enabled:
            return False

        if self.get_session().efd_is_faulty_session():
            return False

        if self._efd_abort_reason is not None:
            return False

        if not self.is_new():
            return False

        if self._efd_initial_finish_time_ns is None:
            log.debug("Early Flake Detection: efd_should_retry called but test has initial result")
            return False

        initial_duration_s = (self._efd_initial_finish_time_ns - self._span.start_ns) / 1e9

        num_retries = len(self._efd_retries)

        if initial_duration_s <= 5:
            return num_retries < efd_settings.slow_test_retries_5s
        if initial_duration_s <= 10:
            return num_retries < efd_settings.slow_test_retries_10s
        if initial_duration_s <= 30:
            return num_retries < efd_settings.slow_test_retries_30s
        if initial_duration_s <= 300:
            return num_retries < efd_settings.slow_test_retries_5m

        self._efd_abort_reason = "slow"
        return False

    def _efd_get_retry_test(self, retry_number: int) -> "TestVisibilityTest":
        return self._efd_retries[retry_number - 1]

    def efd_add_retry(self, start_immediately=False) -> Optional[int]:
        if not self.efd_should_retry():
            log.debug("Early Flake Detection: efd_add_retry called but test should not retry")
            return None

        retry_number = len(self._efd_retries) + 1

        retry_test = self.make_early_flake_retry_from_test()
        self._efd_retries.append(retry_test)
        if start_immediately:
            retry_test.start()

        return retry_number

    def efd_start_retry(self, retry_number: int) -> None:
        self._efd_get_retry_test(retry_number).start()

    def efd_finish_retry(self, retry_number: int, status: TestStatus, exc_info: Optional[TestExcInfo] = None) -> None:
        self._efd_get_retry_test(retry_number).finish_test(status, exc_info=exc_info)

    def efd_record_initial(
        self, status: TestStatus, reason: Optional[str] = None, exc_info: Optional[TestExcInfo] = None
    ) -> None:
        if self._efd_initial_finish_time_ns is not None:
            log.debug("Early Flake Detection: initial result already recorded")
            return
        if not self.is_started():
            log.debug("Test needs to have started in order to record initial result")
            return

        if reason is not None:
            self.set_tag(test.SKIP_REASON, reason)
        if exc_info is not None:
            self._exc_info = exc_info

        self._efd_initial_finish_time_ns = time_ns()
        self._status = status

    def efd_get_final_status(self):
        # NOTE: we look at self._status directly because the test has not been finished at the time we want to call this
        # method
        if (self._status == TestStatus.PASS) or any(
            retry.get_status() == TestStatus.PASS for retry in self._efd_retries
        ):
            return TestStatus.PASS
        return TestStatus.FAIL

    def set_efd_abort_reason(self, reason: str) -> None:
        self._efd_abort_reason = reason

    def efd_finish_test(self):
        self.set_status(self.efd_get_final_status())
        self.finish(override_finish_time=(self._efd_initial_finish_time_ns / 1e9))  # Finish expects time in seconds
