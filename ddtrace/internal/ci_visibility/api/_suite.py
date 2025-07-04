from pathlib import Path
from typing import Dict
from typing import List
from typing import Optional

from ddtrace.ext import test
from ddtrace.ext.test_visibility._test_visibility_base import TestId
from ddtrace.ext.test_visibility._test_visibility_base import TestSuiteId
from ddtrace.ext.test_visibility.status import TestSourceFileInfo
from ddtrace.ext.test_visibility.status import TestStatus
from ddtrace.internal.ci_visibility.api._base import TestVisibilityChildItem
from ddtrace.internal.ci_visibility.api._base import TestVisibilityParentItem
from ddtrace.internal.ci_visibility.api._base import TestVisibilitySessionSettings
from ddtrace.internal.ci_visibility.api._coverage_data import TestVisibilityCoverageData
from ddtrace.internal.ci_visibility.api._test import TestVisibilityTest
from ddtrace.internal.ci_visibility.constants import ITR_CORRELATION_ID_TAG_NAME
from ddtrace.internal.ci_visibility.constants import SUITE_ID
from ddtrace.internal.ci_visibility.constants import SUITE_TYPE
from ddtrace.internal.ci_visibility.telemetry.constants import EVENT_TYPES
from ddtrace.internal.ci_visibility.telemetry.events import record_event_created
from ddtrace.internal.ci_visibility.telemetry.events import record_event_finished
from ddtrace.internal.logger import get_logger
from ddtrace.internal.test_visibility.coverage_lines import CoverageLines


log = get_logger(__name__)


class TestVisibilitySuite(TestVisibilityParentItem[TestId, TestVisibilityTest], TestVisibilityChildItem[TestSuiteId]):
    _event_type = SUITE_TYPE
    _event_type_metric_name = EVENT_TYPES.SUITE

    def __init__(
        self,
        name: str,
        session_settings: TestVisibilitySessionSettings,
        codeowners: Optional[List[str]] = None,
        source_file_info: Optional[TestSourceFileInfo] = None,
        initial_tags: Optional[Dict[str, str]] = None,
    ) -> None:
        super().__init__(name, session_settings, session_settings.suite_operation_name, initial_tags)
        self._codeowner = codeowners
        self._source_file_info = source_file_info

        self._coverage_data: TestVisibilityCoverageData = TestVisibilityCoverageData()

    def __repr__(self) -> str:
        module_name = self.parent.name if self.parent is not None else "none"
        return f"{self.__class__.__name__}(name={self.name}, module={module_name})"

    def finish(
        self,
        force: bool = False,
        override_status: Optional[TestStatus] = None,
        override_finish_time: Optional[float] = None,
    ) -> None:
        super().finish(force=force, override_status=override_status, override_finish_time=override_finish_time)

    def finish_itr_skipped(self) -> None:
        """Suites should only count themselves as ITR-skipped if all children are ITR skipped"""
        log.debug("Finishing CI Visibility suite %s as ITR skipped", self)
        for child in self._children.values():
            if not (child.is_finished() and child.is_itr_skipped()):
                log.debug(
                    "Not finishing CI Visibility suite %s child test %s was not skipped by ITR",
                    self,
                    child,
                )
                return

        self.count_itr_skipped()
        self.mark_itr_skipped()
        self.finish()

    def _get_hierarchy_tags(self) -> Dict[str, str]:
        return {
            SUITE_ID: str(self.get_span_id()),
            test.SUITE: self.name,
        }

    def _set_itr_tags(self, itr_enabled: bool) -> None:
        """Set suite-level tags based on ITR enablement status"""
        super()._set_itr_tags(itr_enabled)

        if itr_enabled and self._session_settings.itr_correlation_id:
            self.set_tag(ITR_CORRELATION_ID_TAG_NAME, self._session_settings.itr_correlation_id)

    def _telemetry_record_event_created(self):
        record_event_created(
            event_type=self._event_type_metric_name,
            test_framework=self._session_settings.test_framework_metric_name,
        )

    def _telemetry_record_event_finished(self):
        record_event_finished(
            event_type=self._event_type_metric_name,
            test_framework=self._session_settings.test_framework_metric_name,
        )

    def add_coverage_data(self, coverage_data: Dict[Path, CoverageLines]) -> None:
        self._coverage_data.add_covered_files(coverage_data)
