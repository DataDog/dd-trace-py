from enum import Enum
import typing as t

from ddtrace.ext.test_visibility._utils import _catch_and_log_exceptions
import ddtrace.ext.test_visibility.api as ext_api
from ddtrace.internal.logger import get_logger
from ddtrace.internal.test_visibility._internal_item_ids import InternalTestId


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
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import CIVisibility

        return CIVisibility.get_session().efd_is_enabled()

    @staticmethod
    @_catch_and_log_exceptions
    def efd_is_faulty_session() -> bool:
        log.debug("Checking if EFD session is faulty")
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import CIVisibility

        return CIVisibility.get_session().efd_is_faulty_session()

    @staticmethod
    @_catch_and_log_exceptions
    def efd_has_failed_tests() -> bool:
        log.debug("Checking if EFD session has failed tests")
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import CIVisibility

        return CIVisibility.get_session().efd_has_failed_tests()


class EFDTestMixin:
    class EFDRetryFinishArgs(t.NamedTuple):
        test_id: InternalTestId
        retry_number: int
        status: ext_api.TestStatus
        exc_info: t.Optional[ext_api.TestExcInfo]

    @staticmethod
    @_catch_and_log_exceptions
    def efd_should_retry(test_id: InternalTestId) -> bool:
        log.debug("Checking if test %s should be retried by EFD", test_id)
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import CIVisibility

        return CIVisibility.get_test_by_id(test_id).efd_should_retry()

    @staticmethod
    @_catch_and_log_exceptions
    def efd_add_retry(test_id: InternalTestId, start_immediately: bool = False) -> t.Optional[int]:
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import CIVisibility

        retry_number = CIVisibility.get_test_by_id(test_id).efd_add_retry(start_immediately)
        log.debug("Adding EFD retry %s for test %s", retry_number, test_id)
        return retry_number

    @staticmethod
    @_catch_and_log_exceptions
    def efd_start_retry(test_id: InternalTestId, retry_number: int) -> None:
        log.debug("Starting EFD retry %s for test %s", retry_number, test_id)
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import CIVisibility

        CIVisibility.get_test_by_id(test_id).efd_start_retry(retry_number)

    @staticmethod
    @_catch_and_log_exceptions
    def efd_finish_retry(
        item_id: InternalTestId,
        retry_number: int,
        status: ext_api.TestStatus,
        skip_reason: t.Optional[str] = None,
        exc_info: t.Optional[ext_api.TestExcInfo] = None,
    ):
        from ddtrace.internal.ci_visibility.recorder import CIVisibility

        log.debug(
            "Finishing EFD test retry %s for item %s, status: %s, skip_reason: %s, exc_info: %s",
            retry_number,
            item_id,
            status,
            skip_reason,
            exc_info,
        )
        CIVisibility.get_test_by_id(item_id).efd_finish_retry(
            retry_number=retry_number, status=status, skip_reason=skip_reason, exc_info=exc_info
        )

    @staticmethod
    @_catch_and_log_exceptions
    def efd_get_final_status(test_id: InternalTestId) -> EFDTestStatus:
        log.debug("Getting EFD final status for test %s", test_id)
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import CIVisibility

        return CIVisibility.get_test_by_id(test_id).efd_get_final_status()
