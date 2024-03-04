from enum import Enum
from typing import Dict
from typing import Optional

from ddtrace.ext.ci_visibility.api import CISourceFileInfo
from ddtrace.ext.ci_visibility.api import CISuiteIdType
from ddtrace.ext.ci_visibility.api import CITestIdType
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilityItemBaseType
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilityParentItem
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilitySessionSettings
from ddtrace.internal.ci_visibility.api.ci_test import CIVisibilityTestType
from ddtrace.internal.ci_visibility.errors import CIVisibilityDataError
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


class CIVisibilitySuite(CIVisibilityParentItem[CISuiteIdType, CITestIdType, CIVisibilityTestType]):
    def __init__(
        self,
        item_id: CISuiteIdType,
        session_settings: CIVisibilitySessionSettings,
        codeowner: Optional[str] = None,
        source_file_info: Optional[CISourceFileInfo] = None,
        initial_tags: Optional[Dict[str, str]] = None,
    ):
        super().__init__(item_id, session_settings, initial_tags)
        self._codeowner = codeowner
        self._source_file_info = source_file_info

    def start(self):
        log.warning("Starting CI Visibility suite %s", self.item_id)

    def finish(self, force_finish_children: bool = False, override_status: Optional[Enum] = None):
        log.warning("Finishing CI Visibility suite %s", self.item_id)

    def add_test(self, test: CIVisibilityTestType):
        log.warning("Adding test %s to suite %s", test.item_id, self.item_id)
        if self._session_settings.reject_duplicates and test.item_id in self.children:
            raise CIVisibilityDataError(f"Test {test.item_id} already exists in suite {self.item_id}")
        self.children[test.item_id] = test

    def get_test_by_id(self, test_id: CITestIdType) -> CIVisibilityTestType:
        return super().get_child_by_id(test_id)


class CIVisibilitySuiteType(CIVisibilityItemBaseType, CIVisibilitySuite):
    pass
