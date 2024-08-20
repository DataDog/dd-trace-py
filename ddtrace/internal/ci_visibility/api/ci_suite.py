from pathlib import Path
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from ddtrace.ext import test
from ddtrace.ext.ci_visibility.api import CISourceFileInfo
from ddtrace.ext.ci_visibility.api import CISuiteId
from ddtrace.ext.ci_visibility.api import CITestId
from ddtrace.ext.ci_visibility.api import CITestStatus
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilityChildItem
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilityParentItem
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilitySessionSettings
from ddtrace.internal.ci_visibility.api.ci_coverage_data import CICoverageData
from ddtrace.internal.ci_visibility.api.ci_test import CIVisibilityTest
from ddtrace.internal.ci_visibility.constants import ITR_CORRELATION_ID_TAG_NAME
from ddtrace.internal.ci_visibility.constants import SUITE_ID
from ddtrace.internal.ci_visibility.constants import SUITE_TYPE
from ddtrace.internal.ci_visibility.telemetry.constants import EVENT_TYPES
from ddtrace.internal.ci_visibility.telemetry.events import record_event_created
from ddtrace.internal.ci_visibility.telemetry.events import record_event_finished
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


class CIVisibilitySuite(CIVisibilityParentItem[CITestId, CIVisibilityTest], CIVisibilityChildItem[CISuiteId]):
    _event_type = SUITE_TYPE
    _event_type_metric_name = EVENT_TYPES.SUITE

    def __init__(
        self,
        name: str,
        session_settings: CIVisibilitySessionSettings,
        codeowners: Optional[List[str]] = None,
        source_file_info: Optional[CISourceFileInfo] = None,
        initial_tags: Optional[Dict[str, str]] = None,
    ) -> None:
        super().__init__(name, session_settings, session_settings.suite_operation_name, initial_tags)
        self._codeowner = codeowners
        self._source_file_info = source_file_info

        self._coverage_data: CICoverageData = CICoverageData()

    def finish(self, force: bool = False, override_status: Optional[CITestStatus] = None) -> None:
        super().finish(force=force, override_status=override_status)

    def finish_itr_skipped(self) -> None:
        """Suites should only count themselves as ITR-skipped of all children are ITR skipped"""
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

    def add_coverage_data(self, coverage_data: Dict[Path, List[Tuple[int, int]]]) -> None:
        self._coverage_data.add_coverage_segments(coverage_data)
