from typing import Dict, Optional

from ddtrace._trace.span import Span
from ddtrace.ext.ci_visibility.api import CIModuleId, CISuiteId
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilityItemBase
from ddtrace.internal.ci_visibility.api.ci_suite import CIVisibilitySuite


class CIVisibilityModule(CIVisibilityItemBase):

    def __init__(self, ci_module_id: CIModuleId):
        self.span: Optional[Span] = None
        self.name = ci_module_id.module_name
        self.suites: Dict[CISuiteId, CIVisibilitySuite] = {}