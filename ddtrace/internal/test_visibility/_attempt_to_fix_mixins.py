import typing as t

from ddtrace.ext.test_visibility._utils import _catch_and_log_exceptions
import ddtrace.ext.test_visibility.api as ext_api
from ddtrace.internal.logger import get_logger
from ddtrace.internal.test_visibility._internal_item_ids import InternalTestId


log = get_logger(__name__)


class AttemptToFixSessionMixin:
    @staticmethod
    @_catch_and_log_exceptions
    def attempt_to_fix_has_failed_tests() -> bool:
        log.debug("Checking if attempt to fix session has failed tests")
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import CIVisibility

        return CIVisibility.get_session().attempt_to_fix_has_failed_tests()


class AttemptToFixTestMixin:
    class AttemptToFixRetryFinishArgs(t.NamedTuple):
        test_id: InternalTestId
        retry_number: int
        status: ext_api.TestStatus
        exc_info: t.Optional[ext_api.TestExcInfo]

    @staticmethod
    @_catch_and_log_exceptions
    def attempt_to_fix_should_retry(test_id: InternalTestId) -> bool:
        log.debug("Checking if test %s should be retried by attempt to fix", test_id)
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import CIVisibility

        return CIVisibility.get_test_by_id(test_id).attempt_to_fix_should_retry()

    @staticmethod
    @_catch_and_log_exceptions
    def attempt_to_fix_add_retry(test_id: InternalTestId, start_immediately: bool = False) -> t.Optional[int]:
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import CIVisibility

        retry_number = CIVisibility.get_test_by_id(test_id).attempt_to_fix_add_retry(start_immediately)
        log.debug("Adding attempt to fix retry %s for test %s", retry_number, test_id)
        return retry_number

    @staticmethod
    @_catch_and_log_exceptions
    def attempt_to_fix_start_retry(test_id: InternalTestId, retry_number: int) -> None:
        log.debug("Starting attempt to fix retry %s for test %s", retry_number, test_id)
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import CIVisibility

        CIVisibility.get_test_by_id(test_id).attempt_to_fix_start_retry(retry_number)

    @staticmethod
    @_catch_and_log_exceptions
    def attempt_to_fix_finish_retry(finish_args: "AttemptToFixTestMixin.AttemptToFixRetryFinishArgs") -> None:
        log.debug("Finishing attempt to fix retry %s for test %s", finish_args.retry_number, finish_args.test_id)
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import CIVisibility

        CIVisibility.get_test_by_id(finish_args.test_id).attempt_to_fix_finish_retry(
            finish_args.retry_number, finish_args.status, finish_args.exc_info
        )

    @staticmethod
    @_catch_and_log_exceptions
    def attempt_to_fix_get_final_status(test_id: InternalTestId) -> ext_api.TestStatus:
        log.debug("Getting attempt to fix final status for test %s", test_id)
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import CIVisibility

        return CIVisibility.get_test_by_id(test_id).attempt_to_fix_get_final_status()
