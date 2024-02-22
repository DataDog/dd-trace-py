from typing import Dict, Optional

from ddtrace._trace.span import Span
from ddtrace.ext.ci_visibility.api import CISessionId, CIModuleId
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilityItemBase
from ddtrace.internal.ci_visibility.api.ci_module import CIVisibilityModule


class CIVisibilitySession(CIVisibilityItemBase):
    """This class represents a CI session and is the top level in the hierarchy of CI visibility items.

    It does not access its skip-level descendents directly as they are expected to be managed through their own parent
    instances.
    """
    def __init__(self, ci_session_id: Optional[CISessionId] = None):
        self.span: Optional[Span] = None
        self._session = ci_session_id

        self.modules: Dict[CIModuleId, CIVisibilityModule] = {}

    def start(self):
        pass

    def finish(self):
        pass