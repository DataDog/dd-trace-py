from enum import Enum
from typing import Dict
from typing import Optional

from ddtrace.ext.ci_visibility.api import CIModuleId
from ddtrace.ext.ci_visibility.api import CIModuleIdType
from ddtrace.ext.ci_visibility.api import CISuiteId
from ddtrace.ext.ci_visibility.api import CISuiteIdType
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilityItemBaseType
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilityParentItem
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilitySessionSettings
from ddtrace.internal.ci_visibility.api.ci_suite import CIVisibilitySuite
from ddtrace.internal.ci_visibility.api.ci_suite import CIVisibilitySuiteType
from ddtrace.internal.ci_visibility.errors import CIVisibilityDataError
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


class CIVisibilityModule(CIVisibilityParentItem[CIModuleIdType, CISuiteIdType, CIVisibilitySuiteType]):
    def __init__(
        self,
        item_id: CIModuleId,
        session_settings: CIVisibilitySessionSettings,
        initial_tags: Optional[Dict[str, str]] = None,
    ):
        super().__init__(item_id, session_settings, initial_tags)

    def start(self):
        log.warning("Starting CI Visibility module %s", self.item_id)

    def finish(self, force_finish_children: bool = False, override_status: Optional[Enum] = None):
        log.warning("Finishing CI Visibility module %s", self.item_id)

    def add_suite(self, suite: CIVisibilitySuite):
        log.warning("Adding suite %s to module %s", suite.item_id, self.item_id)
        if self._session_settings.reject_duplicates and suite.item_id in self.children:
            error_msg = f"Suite {suite.item_id} already exists in module {self.item_id}"
            log.warning(error_msg)
            raise CIVisibilityDataError(error_msg)
        self.children[suite.item_id] = suite

    def get_suite_by_id(self, suite_id: CISuiteId) -> CIVisibilitySuite:
        return super().get_child_by_id(suite_id)


class CIVisibilityModuleType(CIVisibilityItemBaseType, CIVisibilityModule):
    pass
