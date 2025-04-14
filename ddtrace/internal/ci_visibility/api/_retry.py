"""superclass of EFD, ATR and Attempt-to-Fix.

It should be able to tell:
- does it apply to the current test?
- should it retry one more time?
- save retry status
- get final status
- handle faulty session, i.e., session-level state
"""
import typing as t

from ddtrace.ext.test_visibility._item_ids import TestId
from ddtrace.ext.test_visibility.api import TestExcInfo
from ddtrace.ext.test_visibility.api import TestStatus
from ddtrace.internal.ci_visibility.api._test import TestVisibilityTest
from ddtrace.internal.ci_visibility.constants import TEST_HAS_FAILED_ALL_RETRIES
from ddtrace.internal.ci_visibility.recorder import CIVisibility
from ddtrace.internal.logger import get_logger
from ddtrace.internal.test_visibility.api import InternalTest


log = get_logger(__name__)


class ATRRetryManager:
    TOTAL_ATR_RETRIES_IN_SESSION = "total_atr_retries_in_session"

    @classmethod
    def should_apply(cls, test: TestVisibilityTest) -> bool:
        if not test.is_finished():
            log.debug("Auto Test Retries: atr_should_retry called but test is not finished")
            return False

        return test._session_settings.atr_settings.enabled and test._status == TestStatus.FAIL

    def __init__(self, test_id: TestId) -> None:
        self.test_id = test_id
        self.test = CIVisibility.get_test_by_id(test_id)
        self.session = self.test.get_session()
        self.retries = []

    def _max_retries_per_test_reached(self):
        return len(self.retries) >= self.test._session_settings.atr_settings.max_retries

    def _max_retries_per_session_reached(self):
        return (
            self._total_atr_retries_in_session() >= self.test._session_settings.atr_settings.max_session_total_retries
        )

    def _total_atr_retries_in_session(self):
        return self.session.stash_get(ATRRetryManager.TOTAL_ATR_RETRIES_IN_SESSION) or 0

    def _count_atr_retry_in_session(self):
        self.session.stash_set(ATRRetryManager.TOTAL_ATR_RETRIES_IN_SESSION, self._total_atr_retries_in_session() + 1)

    def should_retry(self):
        return (
            not self._max_retries_per_test_reached()
            and not self._max_retries_per_session_reached()
            and self.get_final_status() == TestStatus.FAIL
        )

    def add_and_start_retry(self):
        retry_test = self.test._atr_make_retry_test()  # ꙮ TODO: make it not ATR-specific generic
        self.retries.append(retry_test)
        self._count_atr_retry_in_session()

        retry_test.start()
        return len(self.retries)

    def finish_retry(self, retry_number: int, status: TestStatus, skip_reason, exc_info: t.Optional[TestExcInfo]):
        retry_test = self.retries[retry_number - 1]
        retry_test.set_status(status)  # needed to get the final status correctly below

        if self._max_retries_per_test_reached() and self.get_final_status() == TestStatus.FAIL:
            retry_test.set_tag(TEST_HAS_FAILED_ALL_RETRIES, True)

        retry_test.finish_test(status, exc_info=exc_info)

    def get_final_status(self) -> TestStatus:
        if self.test._status in [TestStatus.PASS, TestStatus.SKIP]:
            return self.test._status

        if any(retry._status == TestStatus.PASS for retry in self.retries):
            return TestStatus.PASS

        return TestStatus.FAIL
