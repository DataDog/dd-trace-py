from enum import Enum
from pathlib import Path
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from ddtrace.ext import test
from ddtrace.ext.ci_visibility.api import CIModuleId
from ddtrace.ext.ci_visibility.api import CISuiteId
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilityChildItem
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilityParentItem
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilitySessionSettings
from ddtrace.internal.ci_visibility.api.ci_suite import CIVisibilitySuite
from ddtrace.internal.ci_visibility.constants import MODULE_ID
from ddtrace.internal.ci_visibility.constants import MODULE_TYPE
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


class CIVisibilitySuiteType:
    pass


class CIVisibilityModule(
    CIVisibilityChildItem[CIModuleId], CIVisibilityParentItem[CIModuleId, CISuiteId, CIVisibilitySuite]
):
    event_type = MODULE_TYPE

    def __init__(
        self,
        item_id: CIModuleId,
        session_settings: CIVisibilitySessionSettings,
        initial_tags: Optional[Dict[str, str]] = None,
    ):
        super().__init__(item_id, session_settings, initial_tags)
        self._operation_name = session_settings.module_operation_name

    def start(self):
        log.debug("Starting CI Visibility module %s", self.item_id)
        super().start()

    def finish(self, force: bool = False, override_status: Optional[Enum] = None):
        log.debug("Finishing CI Visibility module %s", self.item_id)
        super().finish()

    def _get_hierarchy_tags(self) -> Dict[str, str]:
        return {
            MODULE_ID: str(self.get_span_id()),
            test.MODULE: self.name,
        }

    def add_coverage_data(self, coverage_data: Dict[Path, List[Tuple[int, int]]]):
        raise NotImplementedError("Coverage data cannot be added to modules.")
