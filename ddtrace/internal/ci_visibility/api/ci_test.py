from pathlib import Path
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from ddtrace.ext import test
from ddtrace.ext.ci_visibility.api import CIExcInfo
from ddtrace.ext.ci_visibility.api import CISourceFileInfo
from ddtrace.ext.ci_visibility.api import CITestId
from ddtrace.ext.ci_visibility.api import CITestStatus
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilityChildItem
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilityItemBase
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilitySessionSettings
from ddtrace.internal.ci_visibility.api.ci_coverage_data import CICoverageData
from ddtrace.internal.ci_visibility.constants import TEST
from ddtrace.internal.ci_visibility.telemetry.constants import EVENT_TYPES
from ddtrace.internal.ci_visibility.telemetry.events import record_event_created
from ddtrace.internal.ci_visibility.telemetry.events import record_event_finished
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


class CIVisibilityTest(CIVisibilityChildItem[CITestId], CIVisibilityItemBase):
    event_type = TEST
    event_type_metric_name = EVENT_TYPES.TEST

    def __init__(
        self,
        item_id: CITestId,
        session_settings: CIVisibilitySessionSettings,
        codeowners: Optional[List[str]] = None,
        source_file_info: Optional[CISourceFileInfo] = None,
        initial_tags: Optional[Dict[str, str]] = None,
        is_early_flake_retry: bool = False,
        resource: Optional[str] = None,
    ):
        self.parameters = item_id.parameters
        super().__init__(
            item_id,
            session_settings,
            session_settings.test_operation_name,
            initial_tags,
            resource=resource if resource is not None else item_id.name,
        )
        self._codeowners = codeowners
        self._source_file_info = source_file_info
        self._original_test: Optional[CIVisibilityTest] = None
        self._is_early_flake_retry = is_early_flake_retry  # NOTE: currently unused
        self._exc_info: Optional[CIExcInfo] = None
        self._coverage_data: CICoverageData = CICoverageData()

        if item_id.parameters:
            self.set_tag(test.PARAMETERS, item_id.parameters)

        # Currently unsupported
        self._is_benchmark = None

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
            event_type=self.event_type_metric_name,
            test_framework=self._session_settings.test_framework_metric_name,
            is_benchmark=self._is_benchmark if self._is_benchmark is not None else None,
        )

    def _telemetry_record_event_finished(self):
        record_event_finished(
            event_type=self.event_type_metric_name,
            test_framework=self._session_settings.test_framework_metric_name,
            is_benchmark=self._is_benchmark if self._is_benchmark is not None else None,
        )

    def start(self) -> None:
        log.debug("Starting CI Visibility test %s", self.item_id)
        super().start()

    def finish_test(
        self,
        status: CITestStatus,
        reason: Optional[str] = None,
        exc_info: Optional[CIExcInfo] = None,
    ) -> None:
        log.debug("Finishing CI Visibility test %s, with status: %s, reason: %s", self.item_id, status, reason)
        self.set_status(status)
        if reason is not None:
            self.set_tag(test.SKIP_REASON, reason)
        if exc_info is not None:
            self._exc_info = exc_info
        super().finish()

    def count_itr_skipped(self) -> None:
        """Tests do not count skipping on themselves, so only count on the parent.

        When skipping at the suite level, the counting only happens when suites are finished as ITR-skipped.
        """
        if self._session_settings.itr_test_skipping_level is TEST and self.parent is not None:
            self.parent.count_itr_skipped()

    def finish_itr_skipped(self) -> None:
        log.debug("Finishing CI Visibility test %s with ITR skipped", self.item_id)
        self.count_itr_skipped()
        self.mark_itr_skipped()
        self.finish_test(CITestStatus.SKIP)

    @classmethod
    def make_early_flake_retry_from_test(cls, original_test, retry_number: int) -> "CIVisibilityTest":
        new_test_id = CITestId(
            original_test.item_id.parent_id, original_test.name, original_test.parameters, retry_number
        )
        return cls(
            new_test_id,
            original_test._session_settings,
            codeowners=original_test._codeowners,
            source_file_info=original_test._source_file_info,
            initial_tags=original_test._tags,
            is_early_flake_retry=True,
        )

    def add_coverage_data(self, coverage_data: Dict[Path, List[Tuple[int, int]]]) -> None:
        self._coverage_data.add_coverage_segments(coverage_data)
