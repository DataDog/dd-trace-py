from typing import Optional

from ddtrace._trace.span import Span
from ddtrace.ext.ci_visibility.api import CISourceFileInfo
from ddtrace.ext.ci_visibility.api import CITestId
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilityItemBase
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilitySessionSettings
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


class CIVisibilityTest(CIVisibilityItemBase):
    def __init__(
        self,
        ci_test_id: CITestId,
        session_settings: CIVisibilitySessionSettings,
        codeowner: Optional[str] = None,
        source_file_info: Optional[CISourceFileInfo] = None,
    ):
        self.span: Optional[Span] = None
        self.ci_test_id = ci_test_id
        self.name = self.ci_test_id.test_name
        self._codeowner = codeowner
        self._source_file_info = source_file_info
        self._session_settings = session_settings

    def start(self):
        log.warning("Starting CI Visibility test %s", self.item_id)

    def finish(self):
        log.warning("Finishing CI Visibility test %s", self.item_id)
