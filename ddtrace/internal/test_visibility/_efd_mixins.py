from enum import Enum
import typing as t

from ddtrace.ext.test_visibility._utils import _catch_and_log_exceptions
import ddtrace.ext.test_visibility.api as ext_api
from ddtrace.internal import core
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
        from ddtrace.internal.ci_visibility.recorder import on_efd_is_enabled
        return on_efd_is_enabled()

    @staticmethod
    @_catch_and_log_exceptions
    def efd_is_faulty_session() -> bool:
        log.debug("Checking if EFD session is faulty")
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import on_efd_session_is_faulty
        return on_efd_session_is_faulty()

    @staticmethod
    @_catch_and_log_exceptions
    def efd_has_failed_tests() -> bool:
        log.debug("Checking if EFD session has failed tests")
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import on_efd_session_has_efd_failed_tests
        return on_efd_session_has_efd_failed_tests()


class EFDTestMixin:
    class EFDRetryFinishArgs(t.NamedTuple):
        test_id: InternalTestId
        retry_number: int
        status: ext_api.TestStatus
        exc_info: t.Optional[t.Tuple[t.Type[BaseException], BaseException, t.Any]]

    @staticmethod
    @_catch_and_log_exceptions
    def efd_should_retry(test_id: InternalTestId) -> bool:
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import on_efd_should_retry_test
        log.debug("Checking if test %s should be retried by EFD", test_id)
        return on_efd_should_retry_test(test_id)

    @staticmethod
    @_catch_and_log_exceptions
    def efd_add_retry(test_id: InternalTestId, start_immediately: bool = False) -> t.Optional[int]:
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import on_efd_add_retry
        retry_number = on_efd_add_retry(test_id, start_immediately)
        log.debug("Adding EFD retry %s for test %s", retry_number, test_id)
        return retry_number

    @staticmethod
    @_catch_and_log_exceptions
    def efd_start_retry(test_id: InternalTestId, retry_number: int) -> None:
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import on_efd_start_retry
        log.debug("Starting EFD retry %s for test %s", retry_number, test_id)
        on_efd_start_retry(test_id, retry_number)

    @staticmethod
    @_catch_and_log_exceptions
    def efd_finish_retry(finish_args: "EFDTestMixin.EFDRetryFinishArgs") -> None:
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import on_efd_finish_retry
        log.debug("Finishing EFD retry %s for test %s", finish_args.retry_number, finish_args.test_id)
        on_efd_finish_retry(finish_args)

    @staticmethod
    @_catch_and_log_exceptions
    def efd_get_final_status(test_id: InternalTestId) -> EFDTestStatus:
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import on_efd_get_final_status
        log.debug("Getting EFD final status for test %s", test_id)
        return on_efd_get_final_status(test_id)
