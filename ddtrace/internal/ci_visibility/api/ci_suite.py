from enum import Enum
from typing import Dict
from typing import Optional

from ddtrace._trace.span import Span
from ddtrace.ext.ci_visibility.api import CISuiteId
from ddtrace.ext.ci_visibility.api import CITestId
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilityItemBase
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilitySessionSettings
from ddtrace.internal.ci_visibility.api.ci_test import CIVisibilityTest
from ddtrace.internal.ci_visibility.errors import CIVisibilityDataError
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


class CIVisibilitySuite(CIVisibilityItemBase):
    def __init__(self, ci_suite_id: CISuiteId, session_settings: CIVisibilitySessionSettings):
        self.span: Optional[Span] = None
        self.ci_suite_id = ci_suite_id
        self.name = self.ci_suite_id.suite_name
        self.tests: Dict[CITestId, CIVisibilityTest] = {}
        self._session_settings = session_settings

    def start(self):
        log.warning("Starting CI Visibility suite %s", self.item_id)

    def finish(self, force_finish_children: bool = False, override_status: Optional[Enum] = None):
        log.warning("Finishing CI Visibility suite %s", self.item_id)

    def add_test(self, test: CIVisibilityTest):
        log.warning("Adding test %s to suite %s", test.item_id, self.item_id)
        if self._session_settings.reject_duplicates and test.ci_test_id in self.tests:
            raise CIVisibilityDataError(f"Test {test.ci_test_id} already exists in suite {self.item_id}")
        self.tests[test.ci_test_id] = test

    def get_test_by_id(self, test_id: CITestId) -> CIVisibilityTest:
        if test_id in self.tests:
            return self.tests[test_id]
        raise CIVisibilityDataError(f"Test {test_id} not found in suite {self.item_id}")
