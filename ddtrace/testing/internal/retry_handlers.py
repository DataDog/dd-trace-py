from abc import ABC
from abc import abstractmethod
from collections import defaultdict
import os
import typing as t

from ddtrace.testing.internal.constants import TAG_FALSE
from ddtrace.testing.internal.constants import TAG_TRUE
from ddtrace.testing.internal.test_data import Test
from ddtrace.testing.internal.test_data import TestRun
from ddtrace.testing.internal.test_data import TestStatus
from ddtrace.testing.internal.test_data import TestTag


if t.TYPE_CHECKING:
    from ddtrace.testing.internal.session_manager import SessionManager


class RetryHandler(ABC):
    def __init__(self, session_manager: "SessionManager") -> None:
        self.session_manager = session_manager

    @abstractmethod
    def should_apply(self, test: Test) -> bool:
        """
        Return whether this retry policy should be applied to the given test.

        This is called before any test runs have happened, and should consider test properties (such as whether it's
        new), as well as per-session retry limits (accessible via `self.session_manager`).

        For each test, the test plugin will try each retry handler in the session's retry handlers list, and use the
        first one for which `should_apply()` returns True. The `should_apply()` check can assume that the retry feature
        is enabled for the current session (otherwise the retry handler would not be in the session's retry handlers
        list).
        """

    @abstractmethod
    def should_retry(self, test: Test) -> bool:
        """
        Return whether one more test run should be performed for the given test.

        This should consider the status of previous runs, as well as number of attempts and per-session retry limits.
        """

    @abstractmethod
    def get_final_status(self, test: Test) -> TestStatus:
        """
        Return the final status to assign to the test, and set the final test run tags on the passed test.

        Final status and tags are calculated together because they typically depend on the same data (count of
        passed/failed/skipped test runs).
        """

    @abstractmethod
    def set_tags_for_test_run(self, test_run: TestRun) -> None:
        """
        Set the tags to be added to a given retry test run.
        """

    @abstractmethod
    def get_pretty_name(self) -> str:
        """
        Return a human-readable name of the retry handler.
        """


class AutoTestRetriesHandler(RetryHandler):
    def __init__(self, session_manager: "SessionManager") -> None:
        super().__init__(session_manager=session_manager)
        self.max_tests_to_retry_per_session = int(os.getenv("DD_CIVISIBILITY_TOTAL_FLAKY_RETRY_COUNT", "1000"))
        self.max_retries_per_test = int(os.getenv("DD_CIVISIBILITY_FLAKY_RETRY_COUNT", "5"))

    def get_pretty_name(self) -> str:
        return "Auto Test Retries"

    def should_apply(self, test: Test) -> bool:
        return self.max_tests_to_retry_per_session > 0

    def should_retry(self, test: Test) -> bool:
        retries_so_far = len(test.test_runs) - 1  # Initial attempt does not count.
        return test.last_test_run.get_status() == TestStatus.FAIL and retries_so_far < self.max_retries_per_test

    def get_final_status(self, test: Test) -> TestStatus:
        self.max_tests_to_retry_per_session -= 1
        return test.last_test_run.get_status()

    def set_tags_for_test_run(self, test_run: TestRun) -> None:
        if test_run.attempt_number == 0:
            return

        test_run.set_tags(
            {
                TestTag.IS_RETRY: TAG_TRUE,
                TestTag.RETRY_REASON: "auto_test_retry",
            }
        )


class EarlyFlakeDetectionHandler(RetryHandler):
    EFD_ABORT_TEST_SECONDS = 300

    def get_pretty_name(self) -> str:
        return "Early Flake Detection"

    def should_apply(self, test: Test) -> bool:
        # NOTE: currently we replicate dd-trace-py's behavior and disable EFD for parameterized tests. This is
        # technically NOT correct: we should instead treat all parameterized versions of a test as a single test for
        # EFD. But this would be more complex, and for now replicating dd-trace-py's behavior is Fine™.
        return test.is_new() and not test.has_parameters()

    def _target_number_of_retries(self, test: Test) -> int:
        efd_settings = self.session_manager.settings.early_flake_detection
        initial_attempt_seconds = test.test_runs[0].seconds_so_far()

        if initial_attempt_seconds <= 5:
            return efd_settings.slow_test_retries_5s
        if initial_attempt_seconds <= 10:
            return efd_settings.slow_test_retries_10s
        if initial_attempt_seconds <= 30:
            return efd_settings.slow_test_retries_30s
        if initial_attempt_seconds <= 300:
            return efd_settings.slow_test_retries_5m
        return 0  # No retries if the test ran for more than 5 minutes.

    def should_retry(self, test: Test) -> bool:
        if test.seconds_so_far() > self.EFD_ABORT_TEST_SECONDS:
            test.set_early_flake_detection_abort_reason("slow")
            return False

        target_number_of_retries = self._target_number_of_retries(test)
        retries_so_far = len(test.test_runs) - 1  # Initial attempt does not count.
        return retries_so_far < target_number_of_retries

    def get_final_status(self, test: Test) -> TestStatus:
        status_counts: t.Dict[TestStatus, int] = defaultdict(lambda: 0)
        total_count = 0

        for test_run in test.test_runs:
            status_counts[test_run.get_status()] += 1
            total_count += 1

        if status_counts[TestStatus.PASS] > 0:
            if status_counts[TestStatus.PASS] != total_count:
                test.mark_flaky_run()
            return TestStatus.PASS

        if status_counts[TestStatus.FAIL] > 0:
            return TestStatus.FAIL

        return TestStatus.SKIP

    def set_tags_for_test_run(self, test_run: TestRun) -> None:
        if test_run.attempt_number == 0:
            return

        test_run.set_tags(
            {
                TestTag.IS_RETRY: TAG_TRUE,
                TestTag.RETRY_REASON: "early_flake_detection",
            }
        )


class AttemptToFixHandler(RetryHandler):
    def get_pretty_name(self) -> str:
        return "Attempt to Fix"

    def should_apply(self, test: Test) -> bool:
        return test.is_attempt_to_fix()

    def should_retry(self, test: Test) -> bool:
        retries_so_far = len(test.test_runs) - 1  # Initial attempt does not count.
        return retries_so_far < self.session_manager.settings.test_management.attempt_to_fix_retries

    def get_final_status(self, test: Test) -> TestStatus:
        final_status: TestStatus
        final_tags: t.Dict[str, str] = {
            TestTag.ATTEMPT_TO_FIX_PASSED: TAG_FALSE,
        }

        status_counts: t.Dict[TestStatus, int] = defaultdict(lambda: 0)
        total_count = 0

        for test_run in test.test_runs:
            status_counts[test_run.get_status()] += 1
            total_count += 1

        if status_counts[TestStatus.PASS] > 0:
            final_status = TestStatus.PASS
        elif status_counts[TestStatus.FAIL] > 0:
            final_status = TestStatus.FAIL
        else:
            final_status = TestStatus.SKIP

        if status_counts[TestStatus.PASS] == total_count:
            final_tags[TestTag.ATTEMPT_TO_FIX_PASSED] = TAG_TRUE
        elif status_counts[TestStatus.FAIL] == total_count:
            final_tags[TestTag.HAS_FAILED_ALL_RETRIES] = TAG_TRUE

        test.last_test_run.set_tags(final_tags)

        return final_status

    def set_tags_for_test_run(self, test_run: TestRun) -> None:
        if test_run.attempt_number == 0:
            return

        test_run.set_tags(
            {
                TestTag.IS_RETRY: TAG_TRUE,
                TestTag.RETRY_REASON: "attempt_to_fix",
            }
        )
