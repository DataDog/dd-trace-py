from pathlib import Path
from typing import Dict
from typing import List
from typing import Optional
from typing import Union

from ddtrace.ext import test
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
from ddtrace.internal.test_visibility.api import InternalTestId
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
        self._is_early_flake_retry = is_early_flake_retry  # NOTE: currently unused
        self._exc_info: Optional[TestExcInfo] = None
        self._coverage_data: TestVisibilityCoverageData = TestVisibilityCoverageData()

        if self._parameters is not None:
            self.set_tag(test.PARAMETERS, parameters)

        # Currently unsupported
        self._is_benchmark = None

    def __repr__(self) -> str:
        suite_name = self.parent.name if self.parent is not None else "none"
        module_name = self.parent.parent.name if self.parent is not None and self.parent.parent is not None else "none"
        return "{}(name={}, suite={}, module={}, parameters={})".format(
            self.__class__.__name__, self.name, suite_name, module_name, self._parameters
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
    ) -> None:
        log.debug("Test Visibility: finishing %s, with status: %s, reason: %s", self, status, reason)
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
        log.debug("Finishing Test Visibility test %s with ITR skipped", self)
        self.count_itr_skipped()
        self.mark_itr_skipped()
        self.finish_test(TestStatus.SKIP)

    def make_early_flake_retry_from_test(self, original_test_id: InternalTestId, retry_number: int) -> None:
        if self.parent is None:
            raise ValueError("Cannot make early flake retry from test without a parent")

        new_test_id = InternalTestId(
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

    def add_coverage_data(self, coverage_data: Dict[Path, CoverageLines]) -> None:
        self._coverage_data.add_covered_files(coverage_data)

    def set_parameters(self, parameters: str) -> None:
        self._parameters = parameters
        self.set_tag(test.PARAMETERS, self._parameters)
