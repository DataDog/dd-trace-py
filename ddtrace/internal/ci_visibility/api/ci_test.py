from typing import Optional

from ddtrace._trace.span import Span
from ddtrace.ext.ci_visibility.api import CITestId
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilityItemBase

class CIVisibilityTest(CIVisibilityItemBase):
    def __init__(self, ci_test_id: CITestId):
        self.span: Optional[Span] = None
        self.name = ci_test_id.test_name