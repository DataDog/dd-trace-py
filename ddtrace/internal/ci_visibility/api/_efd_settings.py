"""Settings for Early Flake Detection.

This module contains settings classes for the Early Flake Detection feature.
"""

import dataclasses
from typing import List
from typing import Tuple

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


@dataclasses.dataclass(frozen=True)
class EarlyFlakeDetectionSettings:
    """Settings for Early Flake Detection.

    This class encapsulates the settings for the Early Flake Detection feature, which
    includes configuration for retry counts based on test duration and thresholds for
    determining faulty sessions.

    Attributes:
        enabled: Whether Early Flake Detection is enabled
        slow_test_retries_5s: Maximum retries for tests that take less than 5 seconds
        slow_test_retries_10s: Maximum retries for tests that take less than 10 seconds
        slow_test_retries_30s: Maximum retries for tests that take less than 30 seconds
        slow_test_retries_5m: Maximum retries for tests that take less than 5 minutes
        faulty_session_threshold: Percentage threshold for determining if a session is faulty
    """

    enabled: bool = False
    slow_test_retries_5s: int = 10
    slow_test_retries_10s: int = 5
    slow_test_retries_30s: int = 3
    slow_test_retries_5m: int = 2
    faulty_session_threshold: int = 30

    def get_threshold_definitions(self) -> List[Tuple[float, int]]:
        """Get the duration thresholds and their associated retry limits.

        Returns:
            A list of tuples (duration_threshold, retry_limit) sorted by duration
        """
        return [
            (5.0, self.slow_test_retries_5s),  # ≤ 5 seconds tests
            (10.0, self.slow_test_retries_10s),  # ≤ 10 seconds tests
            (30.0, self.slow_test_retries_30s),  # ≤ 30 seconds tests
            (300.0, self.slow_test_retries_5m),  # ≤ 5 minutes tests
        ]

    def get_max_retries_for_duration(self, duration_seconds: float) -> int:
        """Get the maximum number of retries allowed for a test with the given duration.

        Args:
            duration_seconds: The duration of the test in seconds

        Returns:
            The maximum number of retries allowed for this test
        """
        for threshold, retries in self.get_threshold_definitions():
            if duration_seconds <= threshold:
                return retries
        return 0  # No retries for tests longer than 5 minutes
