from pathlib import Path
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from ddtrace.ext import test
from ddtrace.ext.ci_visibility.api import CIModuleId
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilityParentItem
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilitySessionSettings
from ddtrace.internal.ci_visibility.api.ci_module import CIVisibilityModule
from ddtrace.internal.ci_visibility.constants import SESSION_ID
from ddtrace.internal.ci_visibility.constants import SESSION_TYPE
from ddtrace.internal.ci_visibility.telemetry.constants import EVENT_TYPES
from ddtrace.internal.ci_visibility.telemetry.events import record_event_created
from ddtrace.internal.ci_visibility.telemetry.events import record_event_finished
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


class CIVisibilitySession(CIVisibilityParentItem[CIModuleId, CIVisibilityModule]):
    """This class represents a CI session and is the top level in the hierarchy of CI visibility items.

    It does not access its skip-level descendents directly as they are expected to be managed through their own parent
    instances.
    """

    _event_type = SESSION_TYPE
    _event_type_metric_name = EVENT_TYPES.SESSION

    def __init__(
        self,
        session_settings: CIVisibilitySessionSettings,
        initial_tags: Optional[Dict[str, str]] = None,
    ) -> None:
        log.debug("Initializing CI Visibility session")
        super().__init__(
            "ci_visibility_session", session_settings, session_settings.session_operation_name, initial_tags
        )
        self._test_command = self._session_settings.test_command

        self.set_tag(test.ITR_TEST_CODE_COVERAGE_ENABLED, session_settings.coverage_enabled)

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
        if itr_enabled:
            self.set_tag(test.ITR_TEST_SKIPPING_TYPE, self._session_settings.itr_test_skipping_level)
            self.set_tag(test.ITR_DD_CI_ITR_TESTS_SKIPPED, self._itr_skipped_count > 0)

    def _telemetry_record_event_created(self):
        record_event_created(
            event_type=self._event_type_metric_name,
            test_framework=self._session_settings.test_framework_metric_name,
            has_codeowners=self._codeowners is not None,
            is_unsupported_ci=self._session_settings.is_unsupported_ci,
        )

    def _telemetry_record_event_finished(self):
        record_event_finished(
            event_type=self._event_type_metric_name,
            test_framework=self._session_settings.test_framework_metric_name,
            has_codeowners=self._codeowners is not None,
            is_unsupported_ci=self._session_settings.is_unsupported_ci,
        )

    def add_coverage_data(self, coverage_data: Dict[Path, List[Tuple[int, int]]]) -> None:
        raise NotImplementedError("Coverage data cannot be added to sessions.")
