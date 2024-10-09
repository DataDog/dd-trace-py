import typing as t

from ddtrace.ext.test_visibility._utils import _catch_and_log_exceptions
import ddtrace.ext.test_visibility.api as ext_api
from ddtrace.ext.test_visibility.api import TestStatus
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.test_visibility._internal_item_ids import InternalTestId


log = get_logger(__name__)


class EFDSessionMixin:
    @staticmethod
    @_catch_and_log_exceptions
    def is_faulty_session() -> bool:
        log.debug("Checking if session is faulty for Early Flake Detection")
        is_faulty_session = core.dispatch_with_results("test_visibility.efd.session_is_faulty").is_faulty_session.value
        log.debug("Session faulty: %s", is_faulty_session)
        return is_faulty_session


class EFDTestMixin:
    @staticmethod
    @_catch_and_log_exceptions
    def efd_should_retry(item_id: InternalTestId) -> bool:
        """Checks whether a test should be retried

        This does not differentiate between the feature being disabled, the test retries having completed, or the
        session maximums having been reached.
        """
        log.debug("Checking if item %s should be retried for Early Flake Detection", item_id)
        should_retry_test = core.dispatch_with_results(
            "test_visibility.efd.should_retry_test", (item_id,)
        ).should_retry_test.value
        return should_retry_test

    class EFDRecordInitialArgs(t.NamedTuple):
        """InternalTest allows recording an initial duration (for EFD purposes)"""

        test_id: InternalTestId
        status: ext_api.TestStatus
        skip_reason: t.Optional[str] = None
        exc_info: t.Optional[ext_api.TestExcInfo] = None

    @staticmethod
    @_catch_and_log_exceptions
    def efd_record_initial(
        item_id: InternalTestId,
        status: TestStatus,
        skip_reason: t.Optional[str] = None,
        exc_info: t.Optional[ext_api.TestExcInfo] = None,
    ):
        log.debug(
            "Recording initial Early Flake Detection result for item %s: status: %s, skip_reason: %s, exc_info: %s ",
            item_id,
            status,
            skip_reason,
            exc_info,
        )
        core.dispatch(
            "test_visibility.efd.record_initial",
            (EFDTestMixin.EFDRecordInitialArgs(item_id, status, skip_reason, exc_info),),
        )

    @staticmethod
    @_catch_and_log_exceptions
    def efd_add_retry(item_id: InternalTestId, start_immediately: bool = False) -> t.Optional[int]:
        log.debug("Adding Early Flake Detection retry for item %s", item_id)
        retry_number = core.dispatch_with_results(
            "test_visibility.efd.add_retry", (item_id, start_immediately)
        ).retry_number.value
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
    def efd_get_final_status(item_id):
        log.debug("Getting final status for item %s in Early Flake Detection", item_id)
        final_status = core.dispatch_with_results(
            "test_visibility.efd.get_final_status", (item_id,)
        ).efd_final_status.value
        return final_status

    @staticmethod
    @_catch_and_log_exceptions
    def efd_finish_test(item_id):
        log.debug("Finishing item %s in Early Flake Detection", item_id)
        core.dispatch("test_visibility.efd.finish_test", (item_id,))
