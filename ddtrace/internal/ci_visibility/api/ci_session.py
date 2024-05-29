from enum import Enum
from pathlib import Path
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from ddtrace.ext import test
from ddtrace.ext.ci_visibility.api import CIModuleId
from ddtrace.ext.ci_visibility.api import CISessionId
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilityParentItem
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilitySessionSettings
from ddtrace.internal.ci_visibility.api.ci_module import CIVisibilityModule
from ddtrace.internal.ci_visibility.constants import SESSION_ID
from ddtrace.internal.ci_visibility.constants import SESSION_TYPE
from ddtrace.internal.ci_visibility.telemetry.constants import EVENT_TYPES
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


class CIVisibilitySession(CIVisibilityParentItem[CISessionId, CIModuleId, CIVisibilityModule]):
    """This class represents a CI session and is the top level in the hierarchy of CI visibility items.

    It does not access its skip-level descendents directly as they are expected to be managed through their own parent
    instances.
    """

    event_type = SESSION_TYPE
    event_type_metric_name = EVENT_TYPES.SESSION

    def __init__(
        self,
        item_id: CISessionId,
        session_settings: CIVisibilitySessionSettings,
        initial_tags: Optional[Dict[str, str]] = None,
    ):
        log.debug("Initializing CI Visibility session %s", item_id)
        super().__init__(item_id, session_settings, initial_tags)
        self._test_command = self._session_settings.test_command
        self._operation_name = self._session_settings.session_operation_name

    def start(self):
        log.debug("Starting CI Visibility instance %s", self.item_id)
        super().start()

    def finish(self, force: bool = False, override_status: Optional[Enum] = None):
        log.debug("Finishing CI Visibility instance %s", self.item_id)
        super().finish()

    def _get_hierarchy_tags(self) -> Dict[str, Any]:
        return {
            SESSION_ID: str(self.get_span_id()),
        }

    def get_session_settings(self):
        return self._session_settings

    def _set_itr_tags(self):
        """Module (and session) items get a tag for skipping type"""
        super()._set_itr_tags()
        self.set_tag(test.ITR_TEST_SKIPPING_TYPE, self._session_settings.itr_test_skipping_level)

    def add_coverage_data(self, coverage_data: Dict[Path, List[Tuple[int, int]]]):
        raise NotImplementedError("Coverage data cannot be added to sessions.")
