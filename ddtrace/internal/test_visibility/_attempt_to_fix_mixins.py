import dataclasses
import typing as t

from ddtrace.ext.test_visibility._utils import _catch_and_log_exceptions
import ddtrace.ext.test_visibility.api as ext_api
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.test_visibility._internal_item_ids import InternalTestId


log = get_logger(__name__)


class AttemptToFixSessionMixin:
    @staticmethod
    @_catch_and_log_exceptions
    def attempt_to_fix_is_enabled() -> bool:
        log.debug("Checking if Auto Test Retries is enabled for session")
        is_enabled = core.dispatch_with_results("test_visibility.attempt_to_fix.is_enabled").is_enabled.value
        log.debug("Auto Test Retries enabled: %s", is_enabled)
        return is_enabled

    @staticmethod
    @_catch_and_log_exceptions
    def attempt_to_fix_has_failed_tests() -> bool:
        log.debug("Checking if session has failed tests for Auto Test Retries")
        has_failed_tests = core.dispatch_with_results(
            "test_visibility.attempt_to_fix.session_has_failed_tests"
        ).has_failed_tests.value
        log.debug("Session has ATTEMPT_TO_FIX failed tests: %s", has_failed_tests)
        return has_failed_tests


class AttemptToFixTestMixin:
    @staticmethod
    @_catch_and_log_exceptions
    def attempt_to_fix_should_retry(item_id: InternalTestId) -> bool:
        log.debug("Checking if item %s should be retried for Auto Test Retries", item_id)
        should_retry_test = core.dispatch_with_results(
            "test_visibility.attempt_to_fix.should_retry_test", (item_id,)
        ).should_retry_test.value
        log.debug("Item %s should be retried: %s", item_id, should_retry_test)
        return should_retry_test

    @staticmethod
    @_catch_and_log_exceptions
    def attempt_to_fix_add_retry(item_id: InternalTestId, start_immediately: bool = False) -> int:
        log.debug("Adding Auto Test Retries retry for item %s", item_id)
        retry_number = core.dispatch_with_results(
            "test_visibility.attempt_to_fix.add_retry", (item_id, start_immediately)
        ).retry_number.value
        log.debug("Added Auto Test Retries retry %s for item %s", retry_number, item_id)
        return retry_number

    @staticmethod
    @_catch_and_log_exceptions
    def attempt_to_fix_start_retry(item_id: InternalTestId) -> None:
        log.debug("Starting retry for item %s", item_id)
        core.dispatch("test_visibility.attempt_to_fix.start_retry", (item_id,))

    class AttemptToFixRetryFinishArgs(t.NamedTuple):
        test_id: InternalTestId
        retry_number: int
        status: ext_api.TestStatus
        skip_reason: t.Optional[str] = None
        exc_info: t.Optional[ext_api.TestExcInfo] = None

    @staticmethod
    @_catch_and_log_exceptions
    def attempt_to_fix_finish_retry(
        item_id: InternalTestId,
        retry_number: int,
        status: ext_api.TestStatus,
        skip_reason: t.Optional[str] = None,
        exc_info: t.Optional[ext_api.TestExcInfo] = None,
    ):
        log.debug(
            "Finishing ATTEMPT_TO_FIX test retry %s for item %s, status: %s, skip_reason: %s, exc_info: %s",
            retry_number,
            item_id,
            status,
            skip_reason,
            exc_info,
        )
        core.dispatch(
            "test_visibility.attempt_to_fix.finish_retry",
            (
                AttemptToFixTestMixin.AttemptToFixRetryFinishArgs(
                    item_id, retry_number, status, skip_reason=skip_reason, exc_info=exc_info
                ),
            ),
        )

    @staticmethod
    @_catch_and_log_exceptions
    def attempt_to_fix_get_final_status(item_id: InternalTestId) -> ext_api.TestStatus:
        log.debug("Getting final ATTEMPT_TO_FIX status for item %s", item_id)
        attempt_to_fix_final_status = core.dispatch_with_results(
            "test_visibility.attempt_to_fix.get_final_status", (item_id,)
        ).attempt_to_fix_final_status.value
        log.debug("Final ATTEMPT_TO_FIX status for item %s: %s", item_id, attempt_to_fix_final_status)
        return attempt_to_fix_final_status
