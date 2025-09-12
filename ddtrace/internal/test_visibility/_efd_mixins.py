from enum import Enum
import typing as t

from ddtrace.ext.test_visibility._test_visibility_base import TestId
from ddtrace.ext.test_visibility._utils import _catch_and_log_exceptions
from ddtrace.ext.test_visibility.status import TestExcInfo
from ddtrace.ext.test_visibility.status import TestStatus
from ddtrace.internal.ci_visibility.service_registry import require_ci_visibility_service
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


class EFDTestStatus(Enum):
    ALL_PASS = "passed"  # nosec B105
    ALL_FAIL = "failed"
    ALL_SKIP = "skipped"
    FLAKY = "flaky"


class EFDSessionMixin:
    @staticmethod
    @_catch_and_log_exceptions
    def efd_enabled() -> bool:
        log.debug("Checking if EFD is enabled")

        return require_ci_visibility_service().get_session().efd_is_enabled()

    @staticmethod
    @_catch_and_log_exceptions
    def efd_is_faulty_session() -> bool:
        log.debug("Checking if EFD session is faulty")

        return require_ci_visibility_service().get_session().efd_is_faulty_session()

    @staticmethod
    @_catch_and_log_exceptions
    def efd_has_failed_tests() -> bool:
        log.debug("Checking if EFD session has failed tests")

        return require_ci_visibility_service().get_session().efd_has_failed_tests()


class EFDTestMixin:
    @staticmethod
    @_catch_and_log_exceptions
    def efd_should_retry(item_id: TestId) -> bool:
        log.debug("Checking if test %s should be retried by EFD", item_id)

        return require_ci_visibility_service().get_test_by_id(item_id).efd_should_retry()

    @staticmethod
    @_catch_and_log_exceptions
    def efd_add_retry(item_id: TestId, start_immediately: bool = False) -> t.Optional[int]:
        retry_number = require_ci_visibility_service().get_test_by_id(item_id).efd_add_retry(start_immediately)
        log.debug("Adding EFD retry %s for test %s", retry_number, item_id)
        return retry_number

    @staticmethod
    @_catch_and_log_exceptions
    def efd_start_retry(item_id: TestId, retry_number: int) -> None:
        log.debug("Starting EFD retry %s for test %s", retry_number, item_id)

        require_ci_visibility_service().get_test_by_id(item_id).efd_start_retry(retry_number)

    @staticmethod
    @_catch_and_log_exceptions
    def efd_finish_retry(
        item_id: TestId,
        retry_number: int,
        status: TestStatus,
        skip_reason: t.Optional[str] = None,
        exc_info: t.Optional[TestExcInfo] = None,
    ):
        log.debug(
            "Finishing EFD test retry %s for item %s, status: %s, skip_reason: %s, exc_info: %s",
            retry_number,
            item_id,
            status,
            skip_reason,
            exc_info,
        )
        require_ci_visibility_service().get_test_by_id(item_id).efd_finish_retry(
            retry_number=retry_number, status=status, skip_reason=skip_reason, exc_info=exc_info
        )

    @staticmethod
    @_catch_and_log_exceptions
    def efd_get_final_status(item_id: TestId) -> EFDTestStatus:
        log.debug("Getting EFD final status for test %s", item_id)

        return require_ci_visibility_service().get_test_by_id(item_id).efd_get_final_status()
