from enum import Enum
from pathlib import Path
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from ddtrace.ext import test
from ddtrace.ext.ci_visibility.api import CISourceFileInfo
from ddtrace.ext.ci_visibility.api import CISuiteId
from ddtrace.ext.ci_visibility.api import CITestId
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilityChildItem
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilityParentItem
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilitySessionSettings
from ddtrace.internal.ci_visibility.api.ci_coverage_data import CICoverageData
from ddtrace.internal.ci_visibility.api.ci_test import CIVisibilityTest
from ddtrace.internal.ci_visibility.constants import SUITE_ID
from ddtrace.internal.ci_visibility.constants import SUITE_TYPE
from ddtrace.internal.ci_visibility.telemetry.constants import EVENT_TYPES
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


class CIVisibilitySuite(
    CIVisibilityChildItem[CISuiteId], CIVisibilityParentItem[CISuiteId, CITestId, CIVisibilityTest]
):
    event_type = SUITE_TYPE
    event_type_metric_name = EVENT_TYPES.SUITE

    def __init__(
        self,
        item_id: CISuiteId,
        session_settings: CIVisibilitySessionSettings,
        codeowners: Optional[List[str]] = None,
        source_file_info: Optional[CISourceFileInfo] = None,
        initial_tags: Optional[Dict[str, str]] = None,
    ):
        super().__init__(item_id, session_settings, initial_tags)
        self._codeowner = codeowners
        self._source_file_info = source_file_info

        self._operation_name = session_settings.suite_operation_name
        self._coverage_data: CICoverageData = CICoverageData()

    def start(self):
        log.debug("Starting CI Visibility suite %s", self.item_id)
        super().start()

    def finish(self, force: bool = False, override_status: Optional[Enum] = None):
        log.debug("Finishing CI Visibility suite %s", self.item_id)
        super().finish()

    def finish_itr_skipped(self):
        log.debug("Finishing CI Visibility suite %s with ITR skipped", self.item_id)
        self.count_itr_skipped()
        self.mark_itr_skipped()
        self.finish()

    def _get_hierarchy_tags(self) -> Dict[str, str]:
        return {
            SUITE_ID: str(self.get_span_id()),
            test.SUITE: self.name,
        }

    def add_coverage_data(self, coverage_data: Dict[Path, List[Tuple[int, int]]]):
        self._coverage_data.add_coverage_segments(coverage_data)
