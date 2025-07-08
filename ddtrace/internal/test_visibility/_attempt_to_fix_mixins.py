import typing as t

from ddtrace.ext.test_visibility._test_visibility_base import TestId
from ddtrace.ext.test_visibility._utils import _catch_and_log_exceptions
from ddtrace.ext.test_visibility.status import TestExcInfo
from ddtrace.ext.test_visibility.status import TestStatus
from ddtrace.internal.ci_visibility.service_registry import require_ci_visibility_service
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


class AttemptToFixSessionMixin:
    @staticmethod
    @_catch_and_log_exceptions
    def attempt_to_fix_has_failed_tests() -> bool:
        log.debug("Checking if attempt to fix session has failed tests")

        return require_ci_visibility_service().get_session().attempt_to_fix_has_failed_tests()


class AttemptToFixTestMixin:
    @staticmethod
    @_catch_and_log_exceptions
    def attempt_to_fix_should_retry(item_id: TestId) -> bool:
        log.debug("Checking if test %s should be retried by attempt to fix", item_id)
        return require_ci_visibility_service().get_test_by_id(item_id).attempt_to_fix_should_retry()

    @staticmethod
    @_catch_and_log_exceptions
    def attempt_to_fix_add_retry(item_id: TestId, start_immediately: bool = False) -> t.Optional[int]:
        retry_number = (
            require_ci_visibility_service().get_test_by_id(item_id).attempt_to_fix_add_retry(start_immediately)
        )
        log.debug("Adding attempt to fix retry %s for test %s", retry_number, item_id)
        return retry_number

    @staticmethod
    @_catch_and_log_exceptions
    def attempt_to_fix_start_retry(item_id: TestId, retry_number: int) -> None:
        log.debug("Starting attempt to fix retry %s for test %s", retry_number, item_id)
        require_ci_visibility_service().get_test_by_id(item_id).attempt_to_fix_start_retry(retry_number)

    @staticmethod
    @_catch_and_log_exceptions
    def attempt_to_fix_finish_retry(
        item_id: TestId,
        retry_number: int,
        status: TestStatus,
        skip_reason: t.Optional[str] = None,
        exc_info: t.Optional[TestExcInfo] = None,
    ) -> None:
        log.debug("Finishing attempt to fix retry %s for test %s", retry_number, item_id)
        require_ci_visibility_service().get_test_by_id(item_id).attempt_to_fix_finish_retry(
            retry_number=retry_number, status=status, skip_reason=skip_reason, exc_info=exc_info
        )

    @staticmethod
    @_catch_and_log_exceptions
    def attempt_to_fix_get_final_status(item_id: TestId) -> TestStatus:
        log.debug("Getting attempt to fix final status for test %s", item_id)
        return require_ci_visibility_service().get_test_by_id(item_id).attempt_to_fix_get_final_status()
