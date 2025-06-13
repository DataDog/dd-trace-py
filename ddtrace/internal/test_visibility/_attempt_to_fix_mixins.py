import typing as t

from ddtrace.ext.test_visibility.status import TestExcInfo
from ddtrace.ext.test_visibility.status import TestStatus
from ddtrace.internal.ci_visibility.service_registry import require_ci_visibility_service
from ddtrace.internal.logger import get_logger
from ddtrace.internal.test_visibility._internal_item_ids import InternalTestId


log = get_logger(__name__)


class AttemptToFixSessionMixin:
    @staticmethod
    def attempt_to_fix_has_failed_tests() -> bool:
        log.debug("Checking if attempt to fix session has failed tests")

        return require_ci_visibility_service().get_session().attempt_to_fix_has_failed_tests()


class AttemptToFixTestMixin:
    @staticmethod
    def attempt_to_fix_should_retry(test_id: InternalTestId) -> bool:
        log.debug("Checking if test %s should be retried by attempt to fix", test_id)
        return require_ci_visibility_service().get_test_by_id(test_id).attempt_to_fix_should_retry()

    @staticmethod
    def attempt_to_fix_add_retry(test_id: InternalTestId, start_immediately: bool = False) -> t.Optional[int]:
        retry_number = (
            require_ci_visibility_service().get_test_by_id(test_id).attempt_to_fix_add_retry(start_immediately)
        )
        log.debug("Adding attempt to fix retry %s for test %s", retry_number, test_id)
        return retry_number

    @staticmethod
    def attempt_to_fix_start_retry(test_id: InternalTestId, retry_number: int) -> None:
        log.debug("Starting attempt to fix retry %s for test %s", retry_number, test_id)
        require_ci_visibility_service().get_test_by_id(test_id).attempt_to_fix_start_retry(retry_number)

    @staticmethod
    def attempt_to_fix_finish_retry(
        test_id: InternalTestId,
        retry_number: int,
        status: TestStatus,
        exc_info: t.Optional[TestExcInfo],
    ) -> None:
        log.debug("Finishing attempt to fix retry %s for test %s", retry_number, test_id)
        require_ci_visibility_service().get_test_by_id(test_id).attempt_to_fix_finish_retry(
            retry_number, status, exc_info
        )

    @staticmethod
    def attempt_to_fix_get_final_status(test_id: InternalTestId) -> TestStatus:
        log.debug("Getting attempt to fix final status for test %s", test_id)
        return require_ci_visibility_service().get_test_by_id(test_id).attempt_to_fix_get_final_status()
