from enum import Enum
from typing import Any
from typing import Dict
from typing import Optional

from ddtrace.ext.ci_visibility.api import CIModuleIdType
from ddtrace.ext.ci_visibility.api import CISessionId
from ddtrace.ext.ci_visibility.api import CISessionIdType
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilityItemBaseType
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilityParentItem
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilitySessionSettings
from ddtrace.internal.ci_visibility.api.ci_module import CIVisibilityModuleType
from ddtrace.internal.ci_visibility.constants import SESSION_ID
from ddtrace.internal.ci_visibility.constants import SESSION_TYPE
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


class CIVisibilitySession(CIVisibilityParentItem[CISessionIdType, CIModuleIdType, CIVisibilityModuleType]):
    """This class represents a CI session and is the top level in the hierarchy of CI visibility items.

    It does not access its skip-level descendents directly as they are expected to be managed through their own parent
    instances.
    """

    event_type = SESSION_TYPE

    def __init__(
        self,
        item_id: CISessionId,
        session_settings: CIVisibilitySessionSettings,
        initial_tags: Optional[Dict[str, str]] = None,
    ):
        log.warning("Initializing CI Visibility session %s", item_id)
        super().__init__(item_id, session_settings, initial_tags)
        self._test_command = self._session_settings.test_command
        self._operation_name = self._session_settings.session_operation_name

    def start(self):
        log.warning("Starting CI Visibility instance %s", self.item_id)
        super().start()

    def finish(self, force_finish_children: bool = False, override_status: Optional[Enum] = None):
        log.warning("Finishing CI Visibility instance %s", self.item_id)
        super().finish()

    def _get_hierarchy_tags(self) -> Dict[str, Any]:
        return {
            SESSION_ID: str(self.get_span_id()),
        }

    def get_session_settings(self):
        return self._session_settings


class CIVisibilitySessionType(CIVisibilityItemBaseType, CIVisibilitySession):
    pass
