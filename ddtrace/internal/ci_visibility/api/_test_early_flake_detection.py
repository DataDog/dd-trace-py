from time import time_ns
from typing import TYPE_CHECKING
from typing import Dict
from typing import List
from typing import Optional
from typing import Protocol
from typing import TypeVar
from typing import Union
from typing import cast
from typing import overload
from dataclasses import dataclass

from ddtrace.ext.test_visibility.api import TestExcInfo
from ddtrace.ext.test_visibility.api import TestStatus
from ddtrace.internal.logger import get_logger
from ddtrace.internal.test_visibility._efd_mixins import EFDTestStatus


if TYPE_CHECKING:
    from ddtrace.internal.ci_visibility.api._base import SPECIAL_STATUS
    from ddtrace.internal.ci_visibility.api._base import TestVisibilitySession
    from ddtrace.internal.ci_visibility.api._base import TestVisibilitySessionSettings
    from ddtrace.internal.ci_visibility.api._test import TestVisibilityTest

T = TypeVar("T", bound="TestVisibilityTest")

log = get_logger(__name__)


# Domain Events
@dataclass
class TestRetryRequested:
    """Event representing a request to retry a test."""
    test_id: str
    original_test_name: str
    retry_number: int


@dataclass
class TestRetryStarted:
    """Event representing a retry test being started."""
    test_id: str
    retry_number: int


@dataclass
class TestRetryFinished:
    """Event representing a retry test being finished."""
    test_id: str
    retry_number: int
    status: TestStatus


# Value Objects
class DurationThreshold:
    """Value object representing a test duration threshold with its retry limit."""
    
    def __init__(self, max_duration_seconds: float, retry_limit: int):
        self.max_duration_seconds = max_duration_seconds
        self.retry_limit = retry_limit
        
    def is_applicable(self, duration: float) -> bool:
        """Check if this threshold applies to the given duration."""
        return duration <= self.max_duration_seconds
        
    def allows_retry(self, retry_count: int) -> bool:
        """Check if more retries are allowed for this threshold."""
        return retry_count < self.retry_limit


# Commands
@dataclass
class AddRetryCommand:
    """Command to add a retry for a test."""
    start_immediately: bool = False


@dataclass
class FinishRetryCommand:
    """Command to finish a retry test."""
    retry_number: int
    status: TestStatus
    exc_info: Optional[TestExcInfo] = None


# Service Interface
class EarlyFlakeDetectionService(Protocol):
    """Interface for the Early Flake Detection service."""
    
    @property
    def is_retry(self) -> bool:
        """Whether the current test is a retry."""
        ...
    
    @property
    def abort_reason(self) -> Optional[str]:
        """The reason for aborting retries, if any."""
        ...
    
    def set_abort_reason(self, reason: str) -> None:
        """Set the reason for aborting retries."""
        ...
    
    def should_retry(self) -> bool:
        """Determine if the test should be retried."""
        ...
    
    def has_retries(self) -> bool:
        """Check if the test has any retries."""
        ...
    
    def add_retry(self, command: AddRetryCommand) -> Optional[int]:
        """Add a retry for the test."""
        ...
    
    def start_retry(self, retry_number: int) -> None:
        """Start a specific retry test."""
        ...
    
    def finish_retry(self, command: FinishRetryCommand) -> None:
        """Finish a specific retry test."""
        ...
    
    def get_final_status(self) -> EFDTestStatus:
        """Calculate the final status based on original test and retries."""
        ...
    
    def get_consolidated_status(self) -> Union[TestStatus, "SPECIAL_STATUS"]:
        """Convert the EFD final status to a consolidated test status."""
        ...
    
    def set_tags(self) -> None:
        """Set EFD-related tags on the test."""
        ...
    
    def check_and_handle_abort_if_needed(self) -> bool:
        """Check if the test should abort EFD and handle it."""
        ...


class EarlyFlakeDetectionHandler:
    """Domain service responsible for handling Early Flake Detection (EFD) related operations.

    This class encapsulates all the EFD-specific logic previously embedded in TestVisibilityTest.
    It follows DDD principles by focusing specifically on the EFD domain concern.
    """

    def __init__(self, test: "TestVisibilityTest", session_settings: "TestVisibilitySessionSettings"):
        self._test = test
        self._session_settings = session_settings
        self._is_retry: bool = False
        self._retries: List["TestVisibilityTest"] = []
        self._abort_reason: Optional[str] = None
    
    @classmethod
    def create(cls, test: "TestVisibilityTest", session_settings: "TestVisibilitySessionSettings") -> "EarlyFlakeDetectionService":
        """Factory method to create an EFD handler.
        
        Args:
            test: The test for which to create the handler
            session_settings: The session settings
            
        Returns:
            An EFD handler that implements the EarlyFlakeDetectionService interface
        """
        handler = cls(test, session_settings)
        
        # If this is a retry test, mark it as such
        if getattr(test, "_is_efd_retry", False):
            handler.is_retry = True
            
        return handler
        
    def _get_duration_thresholds(self) -> List[DurationThreshold]:
        """Get duration thresholds from session settings as domain objects."""
        efd_settings = self._session_settings.efd_settings
        return [
            DurationThreshold(5, efd_settings.slow_test_retries_5s),
            DurationThreshold(10, efd_settings.slow_test_retries_10s),
            DurationThreshold(30, efd_settings.slow_test_retries_30s),
            DurationThreshold(300, efd_settings.slow_test_retries_5m),
        ]

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

    def make_retry_from_test(self) -> "TestVisibilityTest":
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

    def _get_retry_test(self, retry_number: int) -> "TestVisibilityTest":
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

        session = self._test.get_session()
        if session is None or session.efd_is_faulty_session():
            return False

        if self._abort_reason is not None:
            return False

        if not self._test.is_new():
            return False

        if not self._test.is_finished():
            log.debug("Early Flake Detection: should_retry called but test is not finished")
            return False

        if self._test._span is None or self._test._span.duration is None:
            log.debug("Early Flake Detection: test span or duration is None")
            return False

        duration_s = cast(float, self._test._span.duration)
        num_retries = len(self._retries)
        
        # Use domain objects to check thresholds
        for threshold in self._get_duration_thresholds():
            if threshold.is_applicable(duration_s):
                return threshold.allows_retry(num_retries)

        return False

    def has_retries(self) -> bool:
        return len(self._retries) > 0

    # Overloaded method for backward compatibility
    @overload
    def add_retry(self, start_immediately: bool = False) -> Optional[int]:
        ...
    
    @overload
    def add_retry(self, command: AddRetryCommand = ...) -> Optional[int]:
        ...

    def add_retry(self, *args, **kwargs) -> Optional[int]:
        """Add a retry test and optionally start it immediately.
        
        This method supports both the old signature for backward compatibility:
            add_retry(start_immediately=False)
        
        And the new command-based signature:
            add_retry(command)
            
        Args:
            *args: Either start_immediately (old style) or a command (new style)
            **kwargs: Support for keyword arguments like start_immediately=False
            
        Returns:
            The retry number if a retry was added, None otherwise
        """
        # Parse arguments to maintain backward compatibility
        if args and isinstance(args[0], AddRetryCommand):
            command = args[0]
        else:
            # Handle both positional and keyword arguments for backward compatibility
            start_immediately = False
            if args and isinstance(args[0], bool):
                start_immediately = args[0]
            elif 'start_immediately' in kwargs:
                start_immediately = kwargs['start_immediately']
            
            command = AddRetryCommand(start_immediately=start_immediately)

        if not self.should_retry():
            log.debug("Early Flake Detection: add_retry called but test should not retry")
            return None

        retry_number = len(self._retries) + 1

        retry_test = self.make_retry_from_test()
        self._retries.append(retry_test)
        
        # Emit domain event
        log.debug(
            "Early Flake Detection: Retry requested", 
            extra={"event": TestRetryRequested(
                test_id=str(self._test._span.span_id if self._test._span else "unknown"),
                original_test_name=self._test.name,
                retry_number=retry_number
            )}
        )
        
        if command.start_immediately:
            retry_test.start()
            # Emit domain event for retry started
            log.debug(
                "Early Flake Detection: Retry started", 
                extra={"event": TestRetryStarted(
                    test_id=str(retry_test._span.span_id if retry_test._span else "unknown"),
                    retry_number=retry_number
                )}
            )

        return retry_number

    def start_retry(self, retry_number: int) -> None:
        """Start a specific retry test."""
        retry_test = self._get_retry_test(retry_number)
        retry_test.start()
        
        # Emit domain event
        log.debug(
            "Early Flake Detection: Retry started", 
            extra={"event": TestRetryStarted(
                test_id=str(retry_test._span.span_id if retry_test._span else "unknown"),
                retry_number=retry_number
            )}
        )

    # Overloaded method for backward compatibility
    @overload
    def finish_retry(self, retry_number: int, status: TestStatus, exc_info: Optional[TestExcInfo] = None) -> None:
        ...
    
    @overload
    def finish_retry(self, command: FinishRetryCommand) -> None:
        ...

    def finish_retry(self, arg1, status=None, exc_info=None):
        """Finish a specific retry test with the given status.
        
        This method supports both the old signature for backward compatibility:
            finish_retry(retry_number, status, exc_info=None)
        
        And the new command-based signature:
            finish_retry(command)
        """
        # Handle both old and new style invocations
        if isinstance(arg1, FinishRetryCommand):
            command = arg1
        else:
            # Old style invocation with separate parameters
            command = FinishRetryCommand(
                retry_number=arg1,
                status=status,
                exc_info=exc_info
            )
            
        retry_test = self._get_retry_test(command.retry_number)

        if command.status is not None:
            retry_test.set_status(command.status)

        retry_test.finish_test(command.status, exc_info=command.exc_info)
        
        # Emit domain event
        log.debug(
            "Early Flake Detection: Retry finished", 
            extra={"event": TestRetryFinished(
                test_id=str(retry_test._span.span_id if retry_test._span else "unknown"),
                retry_number=command.retry_number,
                status=command.status
            )}
        )

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

    def get_consolidated_status(self) -> Union[TestStatus, "SPECIAL_STATUS"]:
        """Convert the EFD final status to a consolidated test status.

        This converts the more detailed EFDTestStatus enum to the simpler TestStatus
        that's expected by the system.
        """
        efd_status = self.get_final_status()
        if efd_status in (EFDTestStatus.ALL_PASS, EFDTestStatus.FLAKY):
            return TestStatus.PASS
        if efd_status == EFDTestStatus.ALL_SKIP:
            return TestStatus.SKIP
        return TestStatus.FAIL

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

    def check_and_handle_abort_if_needed(self) -> bool:
        """Check if the test should abort EFD (e.g., due to being too slow) and handle it.

        Returns:
            bool: True if the test was aborted, False otherwise.
        """
        if (
            self._session_settings.efd_settings.enabled
            and self._test.is_new()
            and not self.is_retry
            and self.should_abort()
        ):
            self.set_abort_reason("slow")
            return True
        return False
