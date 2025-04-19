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
from dataclasses import dataclass
from dataclasses import field
from collections import defaultdict

log = get_logger(__name__)

@dataclass
class ATRSessionStatus:
    total_retries: int = 0
    attempts: t.Dict[TestStatus, int] = field(default_factory=lambda: defaultdict(lambda: 0))



class ATRRetryManager:
    @classmethod
    def should_apply(cls, test: TestVisibilityTest) -> bool:
        # ꙮ TODO: check max retries per session too?
        if not test.is_finished():
            log.debug("Auto Test Retries: atr_should_retry called but test is not finished")
            return False

        return test._session_settings.atr_settings.enabled and test._status == TestStatus.FAIL

    @staticmethod
    def _get_session_status(test: TestVisibilityTest) -> ATRSessionStatus:
        session = test.get_session()
        session_status = session.stash_get("atr_session_status")
        if not session_status:
            session_status = ATRSessionStatus()
            session.stash_set("atr_session_status", session_status)
        return session_status

    def __init__(self, test_id: TestId) -> None:
        self.test_id = test_id
        self.test = CIVisibility.get_test_by_id(test_id)
        self.session_status = ATRRetryManager._get_session_status(self.test)
        self.retries = []
        self.session_status.attempts[self.test.get_status()] += 1

    def _max_retries_per_test_reached(self) -> bool:
        return len(self.retries) >= self.test._session_settings.atr_settings.max_retries

    def _max_retries_per_session_reached(self) -> bool:
        return (
            self.session_status.total_retries >= self.test._session_settings.atr_settings.max_session_total_retries
        )

    def should_retry(self) -> bool:
        return (
            not self._max_retries_per_test_reached()
            and not self._max_retries_per_session_reached()
            and self.get_final_status() == TestStatus.FAIL
        )

    def add_and_start_retry(self) -> int:
        retry_test = self.test._make_retry_test(is_atr_retry=True)
        self.retries.append(retry_test)
        self.session_status.total_retries += 1

        retry_test.start()
        return len(self.retries)

    def finish_retry(self, retry_number: int, status: TestStatus, skip_reason, exc_info: t.Optional[TestExcInfo]) -> None:
        retry_test = self.retries[retry_number - 1]
        retry_test.set_status(status)  # needed to get the final status correctly below

        if self._max_retries_per_test_reached() and self.get_final_status() == TestStatus.FAIL:
            retry_test.set_tag(TEST_HAS_FAILED_ALL_RETRIES, True)

        retry_test.finish_test(status, exc_info=exc_info)
        self.session_status.attempts[status] += 1

    def get_final_status(self) -> TestStatus:
        if self.test._status in [TestStatus.PASS, TestStatus.SKIP]:
            return self.test._status

        if any(retry._status == TestStatus.PASS for retry in self.retries):
            return TestStatus.PASS

        return TestStatus.FAIL
