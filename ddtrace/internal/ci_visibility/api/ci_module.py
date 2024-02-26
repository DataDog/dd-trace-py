from enum import Enum
from typing import Dict
from typing import Optional

from ddtrace._trace.span import Span
from ddtrace.ext.ci_visibility.api import CIModuleId
from ddtrace.ext.ci_visibility.api import CISuiteId
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilityItemBase
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilitySessionSettings
from ddtrace.internal.ci_visibility.api.ci_suite import CIVisibilitySuite
from ddtrace.internal.ci_visibility.errors import CIVisibilityDataError
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


class CIVisibilityModule(CIVisibilityItemBase):
    def __init__(self, ci_module_id: CIModuleId, session_settings: CIVisibilitySessionSettings):
        self.span: Optional[Span] = None
        self.ci_module_id = ci_module_id
        self.name = self.ci_module_id.module_name
        self.suites: Dict[CISuiteId, CIVisibilitySuite] = {}
        self._session_settings = session_settings

    def start(self):
        log.warning("Starting CI Visibility module %s", self.item_id)

    def finish(self, force_finish_children: bool = False, override_status: Optional[Enum] = None):
        log.warning("Finishing CI Visibility module %s", self.item_id)

    def add_suite(self, suite: CIVisibilitySuite):
        log.warning("Adding suite %s to module %s", suite.item_id, self.item_id)
        if self._session_settings.reject_duplicates and suite.ci_suite_id in self.suites:
            raise CIVisibilityDataError(f"Suite {suite.ci_suite_id} already exists in module {self.item_id}")
        self.suites[suite.ci_suite_id] = suite

    def get_suite_by_id(self, suite_id: CISuiteId) -> CIVisibilitySuite:
        if suite_id in self.suites:
            return self.suites[suite_id]
        raise CIVisibilityDataError(f"Suite {suite_id} not found in module {self.item_id}")
