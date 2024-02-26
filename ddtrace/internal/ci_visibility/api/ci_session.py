from enum import Enum
from typing import Dict
from typing import Optional

from ddtrace.ext.ci_visibility.api import CIModuleId
from ddtrace.ext.ci_visibility.api import CISessionId
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilityItemBase
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilitySessionSettings
from ddtrace.internal.ci_visibility.api.ci_module import CIVisibilityModule
from ddtrace.internal.ci_visibility.errors import CIVisibilityDataError
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


class CIVisibilitySession(CIVisibilityItemBase):
    """This class represents a CI session and is the top level in the hierarchy of CI visibility items.

    It does not access its skip-level descendents directly as they are expected to be managed through their own parent
    instances.
    """

    def __init__(
        self,
        session_id: CISessionId,
        test_command: str,
        session_settings: CIVisibilitySessionSettings,
        initial_tags: Optional[Dict[str, str]] = None,
    ):
        log.warning("Initializing CI Visibility session %s", session_id)
        super().__init__(session_id, initial_tags)
        self._test_command = test_command
        self.settings = session_settings
        self.modules: Dict[CIModuleId, CIVisibilityModule] = {}

    def start(self):
        log.warning("Starting CI Visibility instance %s", self.item_id)

    def finish(self, force_finish_children: bool = False, override_status: Optional[Enum] = None):
        log.warning("Finishing CI Visibility instance %s", self.item_id)

    def add_module(self, module: CIVisibilityModule):
        log.warning("Adding CI Visibility module %s", self.item_id)
        if self.settings.reject_duplicates and module.ci_module_id in self.modules:
            raise CIVisibilityDataError(f"Module {module.ci_module_id} already exists in session {self.item_id}")
        self.modules[module.ci_module_id] = module

    def get_module_by_id(self, module_id: CIModuleId) -> CIVisibilityModule:
        if module_id not in self.modules:
            return self.modules[module_id]
        raise CIVisibilityDataError(f"Module {module_id} not found in session {self.item_id}")
