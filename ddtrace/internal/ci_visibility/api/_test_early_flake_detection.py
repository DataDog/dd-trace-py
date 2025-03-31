from time import time_ns
from typing import Dict, List, Optional, TypeVar, Union, TYPE_CHECKING

from ddtrace.ext.test_visibility.api import TestExcInfo, TestStatus
from ddtrace.internal.test_visibility._efd_mixins import EFDTestStatus
from ddtrace.internal.logger import get_logger

if TYPE_CHECKING:
    from ddtrace.internal.ci_visibility.api._test import TestVisibilityTest
    from ddtrace.internal.ci_visibility.api._base import TestVisibilitySessionSettings

T = TypeVar('T', bound='TestVisibilityTest')

log = get_logger(__name__)

class EarlyFlakeDetectionHandler:
    """Domain service responsible for handling Early Flake Detection (EFD) related operations.
    
    This class encapsulates all the EFD-specific logic previously embedded in TestVisibilityTest.
    It follows DDD principles by focusing specifically on the EFD domain concern.
    """
    
    def __init__(self, test: 'TestVisibilityTest', session_settings: 'TestVisibilitySessionSettings'):
        self._test = test
        self._session_settings = session_settings
        self._is_retry: bool = False
        self._retries: List['TestVisibilityTest'] = []
        self._abort_reason: Optional[str] = None
    
    @property
    def is_retry(self) -> bool:
        return self._is_retry
    
    @is_retry.setter
    def is_retry(self, value: bool) -> None:
        self._is_retry = value
    
    @property
    def abort_reason(self) -> Optional[str]:
        return self._abort_reason

    def set_abort_reason(self, reason: str) -> None:
        self._abort_reason = reason
    
    def make_retry_from_test(self) -> 'TestVisibilityTest':
        """Create a retry test from the original test."""
        if self._test._parameters is not None:
            raise ValueError("Cannot create an early flake retry from a test with parameters")
            
        retry_test = self._test.__class__(
            self._test.name,
            self._session_settings,
            codeowners=self._test._codeowners,
            source_file_info=self._test._source_file_info,
            initial_tags=self._test._tags,
            is_efd_retry=True,
            is_new=self._test._is_new,
        )
        retry_test.parent = self._test.parent

        return retry_test
    
    def _get_retry_test(self, retry_number: int) -> 'TestVisibilityTest':
        return self._retries[retry_number - 1]
    
    def should_abort(self) -> bool:
        """Check if the test duration is too long for EFD retries."""
        # We have to use current time since the span is not yet finished
        if self._test._span is None or self._test._span.start_ns is None:
            raise ValueError("Test span has not started")
        duration_s = (time_ns() - self._test._span.start_ns) / 1e9
        return duration_s > 300
    
    def should_retry(self) -> bool:
        """Determine if the test should be retried as part of EFD."""
        efd_settings = self._session_settings.efd_settings
        if not efd_settings.enabled:
            return False

        if self._test.get_session().efd_is_faulty_session():
            return False

        if self._abort_reason is not None:
            return False

        if not self._test.is_new():
            return False

        if not self._test.is_finished():
            log.debug("Early Flake Detection: should_retry called but test is not finished")
            return False

        duration_s = self._test._span.duration
        num_retries = len(self._retries)

        if duration_s <= 5:
            return num_retries < efd_settings.slow_test_retries_5s
        if duration_s <= 10:
            return num_retries < efd_settings.slow_test_retries_10s
        if duration_s <= 30:
            return num_retries < efd_settings.slow_test_retries_30s
        if duration_s <= 300:
            return num_retries < efd_settings.slow_test_retries_5m

        return False
    
    def has_retries(self) -> bool:
        return len(self._retries) > 0
    
    def add_retry(self, start_immediately=False) -> Optional[int]:
        """Add a retry test and optionally start it immediately."""
        if not self.should_retry():
            log.debug("Early Flake Detection: add_retry called but test should not retry")
            return None

        retry_number = len(self._retries) + 1

        retry_test = self.make_retry_from_test()
        self._retries.append(retry_test)
        if start_immediately:
            retry_test.start()

        return retry_number
    
    def start_retry(self, retry_number: int) -> None:
        """Start a specific retry test."""
        self._get_retry_test(retry_number).start()
    
    def finish_retry(
        self, retry_number: int, status: TestStatus, exc_info: Optional[TestExcInfo] = None
    ) -> None:
        """Finish a specific retry test with the given status."""
        retry_test = self._get_retry_test(retry_number)

        if status is not None:
            retry_test.set_status(status)

        retry_test.finish_test(status, exc_info=exc_info)
    
    def get_final_status(self) -> EFDTestStatus:
        """Calculate the final status based on original test and retries."""
        status_counts: Dict[TestStatus, int] = {
            TestStatus.PASS: 0,
            TestStatus.FAIL: 0,
            TestStatus.SKIP: 0,
        }

        # NOTE: we assume that any unfinished test (eg: defaulting to failed) mean the test failed
        status_counts[self._test._status] += 1
        for retry in self._retries:
            status_counts[retry._status] += 1

        expected_total = len(self._retries) + 1

        if status_counts[TestStatus.PASS] == expected_total:
            return EFDTestStatus.ALL_PASS
        if status_counts[TestStatus.FAIL] == expected_total:
            return EFDTestStatus.ALL_FAIL
        if status_counts[TestStatus.SKIP] == expected_total:
            return EFDTestStatus.ALL_SKIP

        return EFDTestStatus.FLAKY
    
    def set_tags(self) -> None:
        """Set EFD-related tags on the test."""
        if self._is_retry:
            self._test.set_tag("test.is_retry", self._is_retry)
            self._test.set_tag("test.retry_reason", "early_flake_detection")

        if self._abort_reason is not None:
            self._test.set_tag("test.efd.abort_reason", self._abort_reason)

        # NOTE: The is_new tag is currently only being set in the context of EFD (since that is the only context in
        # which unique tests are fetched). Additionally, if a session is considered faulty, we do not want to tag the
        # test as new.
        session = self._test.get_session()
        if self._test.is_new() and session is not None and not session.efd_is_faulty_session():
            self._test.set_tag("test.is_new", self._test._is_new) 