from pathlib import Path
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
        is_early_flake_retry: bool = False,
        early_flake_retry_number: Optional[int] = None,
        resource: Optional[str] = None,
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
        self._efd_retries: List[TestVisibilityTest] = []
        self._efd_duration_s: Optional[float] = None  # Duration is only used by EFD to decide the number of retries

        self._is_early_flake_retry = is_early_flake_retry
        if is_early_flake_retry:
            if early_flake_retry_number is None:
                raise ValueError("early_flake_retry_number must be set if is_early_flake_retry is True")
            self._early_flake_retry_number = early_flake_retry_number

        if self._parameters is not None:
            self.set_tag(test.PARAMETERS, parameters)

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
        )

    def finish_test(
        self,
        status: TestStatus,
        reason: Optional[str] = None,
        exc_info: Optional[TestExcInfo] = None,
        override_finish_time: Optional[float] = None,
    ) -> None:
        log.debug("Test Visibility: finishing %s, with status: %s, reason: %s", self, status, reason)
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

    def make_early_flake_retry_from_test(self, retry_number: int) -> "TestVisibilityTest":
        if self._parameters is not None:
            raise ValueError("Cannot create an early flake retry from a test with parameters")
        return self.__class__(
            self.name,
            self._session_settings,
            codeowners=self._codeowners,
            source_file_info=self._source_file_info,
            initial_tags=self._tags,
            is_early_flake_retry=True,
            early_flake_retry_number=retry_number,
        )

    def add_coverage_data(self, coverage_data: Dict[Path, CoverageLines]) -> None:
        self._coverage_data.add_covered_files(coverage_data)

    def set_parameters(self, parameters: str) -> None:
        self._parameters = parameters
        self.set_tag(test.PARAMETERS, self._parameters)

    def efd_should_retry(self):
        efd_settings = self._session_settings.efd_settings
        if not efd_settings.enabled:
            return False

        if self._efd_duration_s is None:
            log.debug("Early Flake Detection: efd_should_retry called but test has no duration")
            return False

        if self._efd_duration_s < 5:
            max_retries = efd_settings.slow_test_retries_5s
        elif self._efd_duration_s < 10:
            max_retries = efd_settings.slow_test_retries_10s
        elif self._efd_duration_s < 30:
            max_retries = efd_settings.slow_test_retries_30s
        elif self._efd_duration_s < 300:
            max_retries = efd_settings.slow_test_retries_5m
        else:
            # Tests over 5m are never retried
            return False

        return len(self._efd_retries) < max_retries

    def efd_add_retry(self, retry_number: int, start_immediately=True) -> None:
        if not self.efd_should_retry():
            log.debug("Early Flake Detection: efd_add_retry called but test should not retry")
            return

        retry_test = self.make_early_flake_retry_from_test(retry_number)
        self._efd_retries.append(retry_test)
        if start_immediately:
            retry_test.start()

    def efd_start_retry(self, retry_number: int) -> None:
        self._efd_retries[retry_number - 1].start()

    def efd_finish_retry(self, retry_number: int, status: TestStatus, exc_info: Optional[TestExcInfo] = None) -> None:
        self._efd_retries[retry_number - 1].finish_test(status, exc_info=exc_info)

    def efd_record_duration(self, duration: float) -> None:
        if self._efd_duration_s is not None:
            log.debug("Early Flake Detection: efd_record_duration called but test already has a duration")
            return
        self._efd_duration_s = duration

    def efd_get_final_status(self):
        # NOTE: we look at self._status directly because the test has not been finished at the time we want to call this
        # method
        if (self._status == TestStatus.PASS) or any(
            retry.get_status() == TestStatus.PASS for retry in self._efd_retries
        ):
            return TestStatus.PASS
        return TestStatus.FAIL
