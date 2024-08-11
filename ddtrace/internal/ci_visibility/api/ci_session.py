from pathlib import Path
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from ddtrace.ext import test
from ddtrace.ext.ci_visibility.api import CIModuleId
from ddtrace.ext.ci_visibility.api import CISessionId
from ddtrace.ext.ci_visibility.api import CITestStatus
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
    ) -> None:
        log.debug("Initializing CI Visibility session %s", item_id)
        super().__init__(item_id, session_settings, initial_tags, session_settings.session_operation_name)
        self._test_command = self._session_settings.test_command

    def start(self) -> None:
        log.debug("Starting CI Visibility instance %s", self.item_id)
        super().start()

    def finish(self, force: bool = False, override_status: Optional[CITestStatus] = None) -> None:
        log.debug("Finishing CI Visibility session %s", self.item_id)
        super().finish(force=force, override_status=override_status)

    def _get_hierarchy_tags(self) -> Dict[str, Any]:
        return {
            SESSION_ID: str(self.get_span_id()),
        }

    def get_session_settings(self) -> CIVisibilitySessionSettings:
        return self._session_settings

    def _set_itr_tags(self, itr_enabled: bool) -> None:
        """Set session-level tags based in ITR enablement status"""
        super()._set_itr_tags(itr_enabled)

        self.set_tag(test.ITR_TEST_SKIPPING_ENABLED, self._session_settings.itr_test_skipping_enabled)
        self.set_tag(test.ITR_DD_CI_ITR_TESTS_SKIPPED, self._itr_skipped_count > 0)

        if itr_enabled:
            self.set_tag(test.ITR_TEST_SKIPPING_TYPE, self._session_settings.itr_test_skipping_level)

    def add_coverage_data(self, coverage_data: Dict[Path, List[Tuple[int, int]]]) -> None:
        raise NotImplementedError("Coverage data cannot be added to sessions.")
