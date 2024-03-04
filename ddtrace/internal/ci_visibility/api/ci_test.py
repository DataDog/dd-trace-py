from typing import Dict
from typing import Optional

from ddtrace.ext.ci_visibility.api import CISourceFileInfo
from ddtrace.ext.ci_visibility.api import CITestIdType
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilityItemBase
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilityItemBaseType
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilitySessionSettings
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


class CIVisibilityTest(CIVisibilityItemBase[CITestIdType]):
    def __init__(
        self,
        item_id: CITestIdType,
        session_settings: CIVisibilitySessionSettings,
        codeowner: Optional[str] = None,
        source_file_info: Optional[CISourceFileInfo] = None,
        initial_tags: Optional[Dict[str, str]] = None,
        is_early_flake_retry: bool = False,
    ):
        super().__init__(item_id, session_settings, initial_tags)
        self.test_parameters = item_id.test_parameters
        self._codeowner = codeowner
        self._source_file_info = source_file_info
        self._original_test: Optional[CIVisibilityTest] = None
        self._is_early_flake_retry = is_early_flake_retry

    def start(self):
        log.warning("Starting CI Visibility test %s", self.item_id)

    def finish(self, override_status: Optional[str] = None):
        log.warning("Finishing CI Visibility test %s", self.item_id)

    @classmethod
    def make_early_flake_retry_from_test(cls, original_test, retry_number: int):
        new_test_id = CITestIdType(
            original_test.item_id.parent_id, original_test.name, original_test.test_parameters, retry_number
        )
        return cls(
            new_test_id,
            original_test._session_settings,
            codeowner=original_test._codeowner,
            source_file_info=original_test._source_file_info,
            initial_tags=original_test._tags,
            is_early_flake_retry=True,
        )


class CIVisibilityTestType(CIVisibilityItemBaseType, CIVisibilityTest):
    pass
