from enum import Enum
from typing import Dict
from typing import List
from typing import Optional

from ddtrace.ext import test
from ddtrace.ext.ci_visibility.api import CISourceFileInfo
from ddtrace.ext.ci_visibility.api import CISuiteId
from ddtrace.ext.ci_visibility.api import CITestId
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilityParentItem
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilitySessionSettings
from ddtrace.internal.ci_visibility.api.ci_test import CIVisibilityTest
from ddtrace.internal.ci_visibility.constants import SKIPPED_BY_ITR_REASON
from ddtrace.internal.ci_visibility.constants import SUITE_ID
from ddtrace.internal.ci_visibility.constants import SUITE_TYPE
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


class CIVisibilitySuite(CIVisibilityParentItem[CISuiteId, CITestId, CIVisibilityTest]):
    event_type = SUITE_TYPE

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

    def start(self):
        log.warning("Starting CI Visibility suite %s", self.item_id)
        super().start()

    def finish(self, force: bool = False, override_status: Optional[Enum] = None, is_itr_skipped: bool = False):
        log.warning("Finishing CI Visibility suite %s", self.item_id)
        if is_itr_skipped:
            self.set_tag(test.SKIP_REASON, SKIPPED_BY_ITR_REASON)
            self.set_tag(test.ITR_SKIPPED, "true")
        super().finish()

    def _get_hierarchy_tags(self) -> Dict[str, str]:
        return {
            SUITE_ID: str(self.get_span_id()),
            test.SUITE: self.name,
        }
