"""Dynamic Auto Test Retries policy using duration-based retry budgets."""

from __future__ import annotations

from functools import lru_cache
import logging
import typing as t

from ddtrace.internal.settings import env
from ddtrace.testing.internal.retry_handlers import AutoTestRetriesHandler
from ddtrace.testing.internal.settings_data import Settings
from ddtrace.testing.internal.test_data import Test
from ddtrace.testing.internal.test_data import TestStatus
from ddtrace.testing.internal.utils import asbool


log = logging.getLogger(__name__)

DYNAMIC_ATR_ENABLED_ENV = "DD_CIVISIBILITY_DYNAMIC_ATR_ENABLED"
DYNAMIC_ATR_BUCKETS_ENV = "DD_CIVISIBILITY_DYNAMIC_ATR_BUCKETS"
_RETRY_BUCKET_COUNT = 5
_MAX_RETRIES_PER_BUCKET = 20


def is_dynamic_retries_enabled() -> bool:
    """Return whether duration-based ATR retry budgets are enabled."""
    return asbool(env.get(DYNAMIC_ATR_ENABLED_ENV))


def get_retries_buckets() -> t.Optional[tuple[int, int, int, int, int]]:
    """Return configured ATR retry buckets, or None to use the EFD retry settings."""
    raw_buckets = env.get(DYNAMIC_ATR_BUCKETS_ENV)
    if raw_buckets is None or raw_buckets == "":
        return None

    try:
        buckets = tuple(int(value.strip()) for value in raw_buckets.split(","))
    except ValueError:
        buckets = ()

    if len(buckets) != _RETRY_BUCKET_COUNT or any(value < 1 or value > _MAX_RETRIES_PER_BUCKET for value in buckets):
        log.warning(
            "Invalid %s value %r; expected five comma-separated integers in [1, %d]",
            DYNAMIC_ATR_BUCKETS_ENV,
            raw_buckets,
            _MAX_RETRIES_PER_BUCKET,
        )
        return None

    return t.cast(tuple[int, int, int, int, int], buckets)


class DynamicATRRetriesHandler(AutoTestRetriesHandler):
    """Apply dynamic, duration-based ATR retry budgets instead of the flat per-test retry limit."""

    def __init__(self, settings: Settings, retries_buckets: t.Optional[tuple[int, int, int, int, int]] = None) -> None:
        super().__init__(settings)
        self._retries_buckets = retries_buckets
        self._max_retries_for = lru_cache(maxsize=1024)(self._get_max_retries_for)

    def _get_max_retries_for(self, test: Test) -> int:
        """Cache the initial-duration classification for every retry of a test."""
        initial_attempt_seconds = test.test_runs[0].seconds_so_far()
        efd_settings = self.settings.early_flake_detection
        if self._retries_buckets is None:
            duration_retries = efd_settings.retries_for_duration(initial_attempt_seconds)
        else:
            duration_retries = self._retries_buckets[
                efd_settings.retry_bucket_index_for_duration(initial_attempt_seconds)
            ]
        return max(1, duration_retries)

    def should_retry(self, test: Test) -> bool:
        if test.has_passed():
            return False

        retries_so_far = len(test.test_runs) - 1  # Initial attempt does not count.
        return test.last_test_run.get_status() == TestStatus.FAIL and retries_so_far < self._max_retries_for(test)
