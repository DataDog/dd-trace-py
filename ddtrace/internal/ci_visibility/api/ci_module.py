from enum import Enum
from typing import Any
from typing import Dict
from typing import Optional

from ddtrace.ext import test
from ddtrace.ext.ci_visibility.api import CIModuleId
from ddtrace.ext.ci_visibility.api import CIModuleIdType
from ddtrace.ext.ci_visibility.api import CISuiteIdType
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilityItemBaseType
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilityParentItem
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilitySessionSettings
from ddtrace.internal.ci_visibility.api.ci_suite import CIVisibilitySuiteType
from ddtrace.internal.ci_visibility.constants import MODULE_ID
from ddtrace.internal.ci_visibility.constants import MODULE_TYPE
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


class CIVisibilityModule(CIVisibilityParentItem[CIModuleIdType, CISuiteIdType, CIVisibilitySuiteType]):
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
        log.warning("Starting CI Visibility module %s", self.item_id)
        super().start()

    def finish(self, force_finish_children: bool = False, override_status: Optional[Enum] = None):
        log.warning("Finishing CI Visibility module %s", self.item_id)
        super().finish()

    def _get_hierarchy_tags(self) -> Dict[str, Any]:
        hierarchy_tags = self.parent._get_hierarchy_tags()
        hierarchy_tags.update(
            {
                MODULE_ID: str(self.get_span_id()),
                test.MODULE: self.name,
            }
        )
        return hierarchy_tags


class CIVisibilityModuleType(CIVisibilityItemBaseType, CIVisibilityModule):
    pass
