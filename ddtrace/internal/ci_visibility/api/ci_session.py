from enum import Enum
from typing import Dict
from typing import Optional

from ddtrace.ext.ci_visibility.api import CIModuleIdType
from ddtrace.ext.ci_visibility.api import CISessionIdType
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilityItemBaseType
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilityParentItem
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilitySessionSettings
from ddtrace.internal.ci_visibility.api.ci_module import CIVisibilityModuleType
from ddtrace.internal.ci_visibility.errors import CIVisibilityDataError
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


class CIVisibilitySession(CIVisibilityParentItem[CISessionIdType, CIModuleIdType, CIVisibilityModuleType]):
    """This class represents a CI session and is the top level in the hierarchy of CI visibility items.

    It does not access its skip-level descendents directly as they are expected to be managed through their own parent
    instances.
    """

    def __init__(
        self,
        item_id: CISessionIdType,
        test_command: str,
        session_settings: CIVisibilitySessionSettings,
        initial_tags: Optional[Dict[str, str]] = None,
    ):
        log.warning("Initializing CI Visibility session %s", item_id)
        super().__init__(item_id, session_settings, initial_tags)
        self._test_command = test_command

    def start(self):
        log.warning("Starting CI Visibility instance %s", self.item_id)

    def finish(self, force_finish_children: bool = False, override_status: Optional[Enum] = None):
        log.warning("Finishing CI Visibility instance %s", self.item_id)

    def add_module(self, module: CIVisibilityModuleType):
        log.warning("Adding CI Visibility module %s", self.item_id)
        if self._session_settings.reject_duplicates and module.item_id in self.children:
            error_msg = f"Module {module.item_id} already exists in session {self.item_id}"
            log.warning(error_msg)
            raise CIVisibilityDataError(error_msg)
        self.add_child(module)

    def get_module_by_id(self, module_id: CIModuleIdType) -> CIVisibilityModuleType:
        return super().get_child_by_id(module_id)

    def get_session_settings(self):
        return self._session_settings


class CIVisibilitySessionType(CIVisibilityItemBaseType, CIVisibilitySession):
    pass
