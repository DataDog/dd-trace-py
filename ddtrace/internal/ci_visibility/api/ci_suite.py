from typing import Dict, Optional

from ddtrace._trace.span import Span
from ddtrace.ext.ci_visibility.api import CISuiteId, CITestId
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilityItemBase
from ddtrace.internal.ci_visibility.api.ci_test import CIVisibilityTest


class CIVisibilitySuite(CIVisibilityItemBase):
    def __init__(self, ci_suite_id: CISuiteId):
        self.span: Optional[Span] = None
        self.name = ci_suite_id.suite_name
        self.tests: Dict[CITestId, CIVisibilityTest] = {}