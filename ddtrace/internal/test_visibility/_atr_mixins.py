import dataclasses
import typing as t

from ddtrace.ext.test_visibility.status import TestExcInfo
from ddtrace.ext.test_visibility.status import TestStatus
from ddtrace.internal.ci_visibility.service_registry import require_ci_visibility_service
from ddtrace.internal.logger import get_logger
from ddtrace.internal.test_visibility._internal_item_ids import InternalTestId


log = get_logger(__name__)


@dataclasses.dataclass
class AutoTestRetriesSettings:
    enabled: bool = False
    max_retries: int = 5
    max_session_total_retries: int = 1000


class ATRSessionMixin:
    @staticmethod
    def atr_is_enabled() -> bool:
        log.debug("Checking if ATR is enabled")
        return require_ci_visibility_service().is_atr_enabled()

    @staticmethod
    def atr_has_failed_tests() -> bool:
        log.debug("Checking if ATR session has failed tests")
        return require_ci_visibility_service().get_session().atr_has_failed_tests()


class ATRTestMixin:
    @staticmethod
    def atr_should_retry(item_id: InternalTestId) -> bool:
        log.debug("Checking if test %s should be retried by ATR", item_id)
        return require_ci_visibility_service().get_test_by_id(item_id).atr_should_retry()

    @staticmethod
    def atr_add_retry(item_id: InternalTestId, start_immediately: bool = False) -> int:
        retry_number = require_ci_visibility_service().get_test_by_id(item_id).atr_add_retry(start_immediately)
        log.debug("Adding ATR retry %s for test %s", retry_number, item_id)
        return retry_number

    @staticmethod
    def atr_start_retry(item_id: InternalTestId, retry_number: int) -> None:
        log.debug("Starting ATR retry %s for test %s", retry_number, item_id)
        require_ci_visibility_service().get_test_by_id(item_id).atr_start_retry(retry_number)

    @staticmethod
    def atr_finish_retry(
        test_id: InternalTestId,
        retry_number: int,
        status: TestStatus,
        exc_info: t.Optional[TestExcInfo] = None,
    ) -> None:
        log.debug("Finishing ATR retry %s for test %s", retry_number, test_id)
        require_ci_visibility_service().get_test_by_id(test_id).atr_finish_retry(
            retry_number=retry_number, status=status, exc_info=exc_info
        )

    @staticmethod
    def atr_get_final_status(test_id: InternalTestId) -> TestStatus:
        log.debug("Getting ATR final status for test %s", test_id)

        return require_ci_visibility_service().get_test_by_id(test_id).atr_get_final_status()
