from typing import Optional

from ddtrace.ext.test_visibility._utils import _catch_and_log_exceptions
import ddtrace.ext.test_visibility.api as ext_api
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.test_visibility._internal_item_ids import InternalTestId


log = get_logger(__name__)


class EFDTestMixin(ext_api.TestBase):
    @staticmethod
    @_catch_and_log_exceptions
    def should_efd_retry_test(self, item_id: InternalTestId) -> bool:
        """Checks whether a test should be retried

        This does not differentiate between the feature being disabled, the test retries having completed, or the
        session maximums having been reached.
        """
        log.debug("Checking if item %s should be retried for Early Flake Detection", item_id)
        should_retry_test = core.dispatch_with_results(
            "test_visibility.efd.should_retry_test", (item_id,)
        ).should_retry_test.value
        return should_retry_test

    @staticmethod
    @_catch_and_log_exceptions
    def add_efd_retry(self, item_id: InternalTestId, retry_number: int):
        log.debug("Adding retry %s for item %s to Early Flake Detection", retry_number, item_id)

    @staticmethod
    @_catch_and_log_exceptions
    def finish_efd_retry(
        self,
        item_id: InternalTestId,
        retry_number: int,
        status: ext_api.TestStatus,
        skip_reason: str,
        exc_info: Optional[ext_api.TestExcInfo],
    ):
        pass
