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
        log.debug("Checking if Early Flake Detection is enabled for the session")
        core.dispatch("test_visibility.efd.is_enabled")
        is_enabled = core.get_item("test_visibility.efd.is_enabled")
        log.debug("Early Flake Detection enabled: %s", is_enabled)
        return is_enabled

    @staticmethod
    @_catch_and_log_exceptions
    def efd_is_faulty_session() -> bool:
        log.debug("Checking if session is faulty for Early Flake Detection")
        core.dispatch("test_visibility.efd.session_is_faulty")
        is_faulty_session = core.get_item("test_visibility.efd.session_is_faulty")
        log.debug("Session faulty: %s", is_faulty_session)
        return is_faulty_session

    @staticmethod
    @_catch_and_log_exceptions
    def efd_has_failed_tests() -> bool:
        log.debug("Checking if session has failed tests for Early Flake Detection")
        core.dispatch("test_visibility.efd.session_has_failed_tests")
        has_failed_tests = core.get_item("test_visibility.efd.session_has_failed_tests")
        log.debug("Session has EFD failed tests: %s", has_failed_tests)
        return has_failed_tests


class EFDTestMixin:
    @staticmethod
    @_catch_and_log_exceptions
    def efd_should_retry(item_id: InternalTestId) -> bool:
        """Checks whether a test should be retried

        This does not differentiate between the feature being disabled, the test retries having completed, or the
        session maximums having been reached.
        """
        log.debug("Checking if item %s should be retried for Early Flake Detection", item_id)
        core.dispatch("test_visibility.efd.should_retry_test", (item_id,))
        should_retry_test = core.get_item(f"test_visibility.efd.should_retry_test.{item_id}")
        return should_retry_test

    @staticmethod
    @_catch_and_log_exceptions
    def efd_add_retry(item_id: InternalTestId, start_immediately: bool = False) -> t.Optional[int]:
        log.debug("Adding Early Flake Detection retry for item %s", item_id)
        core.dispatch("test_visibility.efd.add_retry", (item_id, start_immediately))
        retry_number = core.get_item(f"test_visibility.efd.add_retry.{item_id}")
        log.debug("Added Early Flake Detection retry number %s for item %s", retry_number, item_id)
        return retry_number

    @staticmethod
    @_catch_and_log_exceptions
    def efd_start_retry(item_id: InternalTestId, retry_number: int):
        log.debug("Starting Early Flake Detection retry number %s for item %s", retry_number, item_id)
        core.dispatch("test_visibility.efd.start_retry", (item_id, retry_number))

    class EFDRetryFinishArgs(t.NamedTuple):
        test_id: InternalTestId
        retry_number: int
        status: ext_api.TestStatus
        skip_reason: t.Optional[str] = None
        exc_info: t.Optional[ext_api.TestExcInfo] = None

    @staticmethod
    @_catch_and_log_exceptions
    def efd_finish_retry(
        item_id: InternalTestId,
        retry_number: int,
        status: ext_api.TestStatus,
        skip_reason: t.Optional[str] = None,
        exc_info: t.Optional[ext_api.TestExcInfo] = None,
    ):
        log.debug(
            "Finishing EFD test retry %s for item %s, status: %s, skip_reason: %s, exc_info: %s",
            retry_number,
            item_id,
            status,
            skip_reason,
            exc_info,
        )
        core.dispatch(
            "test_visibility.efd.finish_retry",
            (
                EFDTestMixin.EFDRetryFinishArgs(
                    item_id, retry_number, status, skip_reason=skip_reason, exc_info=exc_info
                ),
            ),
        )

    @staticmethod
    @_catch_and_log_exceptions
    def efd_get_final_status(item_id) -> EFDTestStatus:
        log.debug("Getting final status for item %s in Early Flake Detection", item_id)
        core.dispatch("test_visibility.efd.get_final_status", (item_id,))
        final_status = core.get_item(f"test_visibility.efd.get_final_status.{item_id}")
        return final_status
