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
    _event_type = TEST
    _event_type_metric_name = EVENT_TYPES.TEST

    def __init__(
        self,
        name: str,
        session_settings: CIVisibilitySessionSettings,
        parameters: Optional[str] = None,
        codeowners: Optional[List[str]] = None,
        source_file_info: Optional[CISourceFileInfo] = None,
        initial_tags: Optional[Dict[str, str]] = None,
        is_early_flake_retry: bool = False,
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
        self._original_test: Optional[CIVisibilityTest] = None
        self._is_early_flake_retry = is_early_flake_retry  # NOTE: currently unused
        self._exc_info: Optional[CIExcInfo] = None
        self._coverage_data: CICoverageData = CICoverageData()

        if self._parameters is not None:
            self.set_tag(test.PARAMETERS, parameters)

        # Currently unsupported
        self._is_benchmark = None

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name}, parameters={self._parameters})"

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
        status: CITestStatus,
        reason: Optional[str] = None,
        exc_info: Optional[CIExcInfo] = None,
    ) -> None:
        log.debug("CI Visibility: finishing %s, with status: %s, reason: %s", self, status, reason)
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
        log.debug("Finishing CI Visibility test %s with ITR skipped", self)
        self.count_itr_skipped()
        self.mark_itr_skipped()
        self.finish_test(CITestStatus.SKIP)

    def make_early_flake_retry_from_test(self, original_test_id: CITestId, retry_number: int) -> None:
        if self.parent is None:
            raise ValueError("Cannot make early flake retry from test without a parent")

        new_test_id = CITestId(
            original_test_id.parent_id, original_test_id.name, original_test_id.parameters, retry_number
        )
        self.parent.add_child(
            new_test_id,
            self.__class__(
                original_test_id.name,
                self._session_settings,
                parameters=self._parameters,
                codeowners=self._codeowners,
                source_file_info=self._source_file_info,
                initial_tags=self._tags,
                is_early_flake_retry=True,
            ),
        )

    def add_coverage_data(self, coverage_data: Dict[Path, List[Tuple[int, int]]]) -> None:
        self._coverage_data.add_coverage_segments(coverage_data)
